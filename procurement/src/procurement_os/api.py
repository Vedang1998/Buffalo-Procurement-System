from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
import os
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel

from .catalog import (
    approve_recreated_variant, recompute_catalog_gate, reject_recreation_candidate,
    retire_missing_variant,
)
from .config import load_rules
from .economics import qualifying_quantity, target_cost
from .draft_po import DraftPoError, get_vendor_drafts
from .emergency_packet import (
    EmergencyPacketError,
    list_monday_artifacts,
    read_monday_artifact,
)
from .health import data_sync_run_status, full_health
from .inventory import inventory_business_date, latest_inventory_snapshot_status
from .local_access import (
    LocalAccessMiddleware,
    SESSION_COOKIE,
    action_principal,
    authenticated_audit_actor,
    create_local_session,
    destroy_local_session,
    runtime_config,
    staging_request_has_capability,
    staging_request_worker_role,
)
from .matching import MatchCandidate, score_candidate
from .monday_run import (
    build_after_review,
    prepare as prepare_monday_run,
    preview_after_review,
)
from .price_book import (
    MAX_PRICE_BOOK_BYTES,
    PriceBookError,
    get_price_book_batch,
    list_price_book_batches,
    normalized_price_book_template,
    promote_price_book_batch,
    read_raw_price_book,
    reject_price_book_batch,
    stage_and_validate_price_book,
)
from .po_csv import PoCsvError
from .persistent_mapping import (
    PersistentMappingError,
    execute_mapping_decision,
    execute_routine_offer_clear,
    execute_routine_offer_selection,
    execute_supplier_mapping_intake,
    get_mapping_candidate_detail,
    list_mapping_candidates,
    mapping_status,
    preview_mapping_decision,
    preview_routine_offer_clear,
    preview_routine_offer_selection,
    require_synthetic_mapping_capability,
)
from .readiness import po_readiness
from .procurement_review import (
    ProcurementReviewError,
    acknowledge_and_exclude_blocked_item,
    confirm_material_recommendation_edit,
    list_review_queue,
    preview_recommendation_review,
    record_recommendation_review,
)
from .recommendations import (
    CURRENT_METHOD_VERSION,
    RETIRED_METHOD_VERSION,
    MondayRecommendationError,
    confirm_monday_stale_forecast_retirement,
    get_monday_run,
    list_monday_runs,
    preview_monday_stale_forecast_retirement,
)
from .development_forecast import (
    CONTRACT as DEVELOPMENT_FORECAST_CONTRACT,
    METHOD_VERSION as DEVELOPMENT_FORECAST_METHOD_VERSION,
    V2_CONTRACT as DEVELOPMENT_FORECAST_V2_CONTRACT,
    V2_METHOD_VERSION as DEVELOPMENT_FORECAST_V2_METHOD_VERSION,
)
from .storage import get_storage
from .staging_composition import (
    STAGING_WORKER_ROLE_ENV,
    install_access_boundary as _install_access_boundary,
)
from .synthetic_price_replacement import (
    CAPABILITY_ENV as SYNTHETIC_PRICE_REPLACEMENT_CAPABILITY_ENV,
    CONTRACT as SYNTHETIC_PRICE_REPLACEMENT_CONTRACT,
    SyntheticPriceReplacementError,
    apply_price_replacement,
    confirm_declared_price_book,
    get_declared_price_book_batch,
    list_declared_price_book_batches,
    preview_declared_price_confirmation,
    preview_price_replacement,
    registered_target_declaration,
    stage_and_validate_declared_price_book,
)
from .synthetic_mapping_packet import (
    SyntheticPacketError,
    load_synthetic_mapping_packets,
    load_synthetic_multivendor_mapping_packets,
)
from .vendor_rules import evaluate_vendor_rules, update_vendor_rules
from . import catalog as catalog_service
from . import sales as sales_service

MAX_PRICE_BOOK_REQUEST_BYTES = MAX_PRICE_BOOK_BYTES + 65_536


class _BoundedPriceBookUploadMiddleware:
    """Bound the multipart request before Starlette parses or spools it."""

    def __init__(self, app, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/price-books/import"
        ):
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", ())}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except ValueError:
                declared_length = self.max_bytes + 1
            if declared_length < 0 or declared_length > self.max_bytes:
                await Response("Price-book request is too large", status_code=413)(
                    scope, receive, send
                )
                return
        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await Response("Price-book request is too large", status_code=413)(
                    scope, receive, send
                )
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


app = FastAPI(
    title="Buffalo Procurement OS",
    version="1.3.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    _BoundedPriceBookUploadMiddleware, max_bytes=MAX_PRICE_BOOK_REQUEST_BYTES
)


class TargetCostRequest(BaseModel):
    retail_price: float
    target_margin_pct: float


class QualifyingRequest(BaseModel):
    cases: float
    break_unit: str
    qualifying_units_per_case: float | None = None


class MatchRequest(BaseModel):
    supplier_text: str
    shopify_product_title: str
    shopify_variant_title: str
    supplier_size: str | None = None
    shopify_size: str | None = None
    supplier_pack: str | None = None
    shopify_pack: str | None = None


class RolloverRequest(BaseModel):
    as_of: date


class RecreationDecision(BaseModel):
    old_variant_id: str
    new_variant_id: str
    actor: str
    note: str = ""
    review_token: str


class RetireDecision(BaseModel):
    variant_id: str
    actor: str
    note: str
    review_token: str


def _review_token_form():
    """Keep legacy required fields; staging authority comes from the gateway."""

    return (
        Form("")
        if os.getenv(STAGING_WORKER_ROLE_ENV, "") == "synthetic"
        else Form(...)
    )


def _require_review_token(supplied: str | None) -> None:
    """Identity decisions are permanent; mutations are disabled unless the reviewer
    presents the shared review token (fail-closed when the token is not configured).
    The token value is never logged or echoed."""
    if staging_request_has_capability("procurement.order.approve"):
        return
    import hmac
    expected = os.getenv("RECONCILIATION_REVIEW_TOKEN")
    if not expected:
        raise HTTPException(status_code=503,
                            detail="Reconciliation decisions are disabled: RECONCILIATION_REVIEW_TOKEN is not configured")
    if not supplied or not hmac.compare_digest(str(supplied), expected):
        raise HTTPException(status_code=403, detail="Invalid review token")


def _require_price_book_review_token(supplied: str | None) -> None:
    """FUTURE price activation has a distinct fail-closed authority."""
    if staging_request_has_capability("procurement.price.approve"):
        return
    import hmac

    expected = os.getenv("PRICE_BOOK_REVIEW_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Price-book actions are disabled: PRICE_BOOK_REVIEW_TOKEN is not configured",
        )
    if not supplied or not hmac.compare_digest(str(supplied), expected):
        raise HTTPException(status_code=403, detail="Invalid price-book review token")


def _request_runtime_mode() -> str:
    return (
        "SYNTHETIC_DEMO"
        if staging_request_worker_role() == "synthetic"
        else runtime_config().mode
    )


def _db_conn():
    db = _database_url()
    import psycopg
    from urllib.parse import urlparse

    conn = psycopg.connect(db)
    parsed = urlparse(db)
    if (
        parsed.username == "buffalo_synthetic_runtime"
        or parsed.path == "/buffalo_synthetic_staging_demo"
    ):
        try:
            from .synthetic_staging_database import (
                attest_runtime_connection,
                target_from_environment,
            )

            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            conn.execute("SET LOCAL statement_timeout = '10000ms'")
            conn.execute("SET LOCAL lock_timeout = '2000ms'")
            conn.execute(
                "SET LOCAL idle_in_transaction_session_timeout = '15000ms'"
            )
            attest_runtime_connection(conn, target_from_environment(os.environ))
            conn.rollback()
        except Exception:
            conn.close()
            raise
    return conn


def _database_url() -> str:
    db = os.getenv("DATABASE_URL")
    if not db:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    return db


def _server_audit_actor(client_value: str = "") -> str:
    """Use the authenticated named session, never a browser actor field."""

    try:
        return authenticated_audit_actor(client_value)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Authenticated principal is absent") from exc


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "buffalo-procurement-os",
        "version": "1.3.0",
        "process_id": os.getpid(),
    }


@app.get("/auth/login", response_class=HTMLResponse)
def local_login_page():
    config = runtime_config()
    available = config.mode != "UNCONFIGURED"
    form = (
        "<form method='post'><label>Local access secret "
        "<input type='password' name='secret' autocomplete='current-password' required>"
        "</label><button type='submit'>Sign in</button></form>"
        if available
        else "<p>Local authentication is unavailable until an explicit nonproduction mode is configured.</p>"
    )
    return HTMLResponse(
        "<!doctype html><html><head><title>Buffalo local sign in</title></head>"
        "<body><h1>Buffalo Procurement OS — local sign in</h1>"
        f"<p>Mode: {_html_escape(config.mode)}</p>{form}</body></html>",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/auth/login")
def local_login(secret: str = Form(...)):
    try:
        token, _ = create_local_session(secret)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Local sign-in failed") from exc
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=8 * 60 * 60,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/auth/logout")
def local_logout(request: Request):
    destroy_local_session(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health/full")
def health_full():
    """Component-level health: app, DB, schema, seed, Shopify creds, foundation gates."""
    return _runtime_labeled_status(full_health())


def _runtime_labeled_status(value: dict) -> dict:
    """Separate canonical facts from operational authority in the local demo."""

    if _request_runtime_mode() != "SYNTHETIC_DEMO":
        return value
    result = dict(value)
    readiness = result.get("po_readiness")
    if isinstance(readiness, dict):
        canonical = dict(readiness)
        operational = dict(canonical)
        operational["canonical_po_generation_enabled"] = bool(
            canonical.get("po_generation_enabled", False)
        )
        operational["po_generation_enabled"] = False
        result["canonical_po_readiness"] = canonical
        result["po_readiness"] = operational
    if "po_generation_enabled" in result:
        result["canonical_po_generation_enabled"] = bool(
            result.get("po_generation_enabled", False)
        )
        result["po_generation_enabled"] = False
    result.update(
        {
            "runtime_mode": "SYNTHETIC_DEMO",
            "safety_label": "TEST DATA — NOT FOR ORDERING",
            "operational_authority": {
                "production_release_authorized": False,
                "shopify_actions_authorized": False,
                "order_actions_authorized": False,
            },
        }
    )
    return result


STATUS_BADGE = {
    True: '<span style="color:#116329;background:#dafbe1;padding:2px 8px;border-radius:4px">OK</span>',
    False: '<span style="color:#82071e;background:#ffebe9;padding:2px 8px;border-radius:4px">FAIL</span>',
}


_OPERATIONAL_NAV_ITEMS = (
    ("System Readiness", "admin/status"),
    ("Supplier Mapping", "supplier-mapping"),
    ("Vendor Rules", "vendor-rules"),
    ("Price Books", "price-books"),
    ("Catalog Reconciliation", "reconciliation"),
    ("Historical Sales Reconciliation", "historical-sales/review"),
    ("Data/Sync Runs", "data-sync-runs"),
    ("Monday Procurement", "monday-runs"),
)


def _html_escape(value) -> str:
    import html

    return html.escape(str(value), quote=True) if value not in (None, "") else "—"


def _form_value(value) -> str:
    import html

    return html.escape(str(value), quote=True) if value not in (None, "") else ""


def _operational_nav(nav_root: str, *, current: str) -> str:
    links = []
    for label, path in _OPERATIONAL_NAV_ITEMS:
        current_attribute = " aria-current='page'" if label == current else ""
        links.append(
            f"<a href='{nav_root}{path}'{current_attribute}>{label}</a>"
        )
    navigation = (
        "<nav aria-label='Phase 5 operations' "
        "style='display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px'>"
        + "".join(
            "<span style='display:inline-block;border:1px solid #d1d9e0;"
            "border-radius:6px;padding:6px 9px'>" + link + "</span>"
            for link in links
        )
        + "</nav>"
    )
    return navigation + (
        _mapping_banner() if _request_runtime_mode() == "SYNTHETIC_DEMO" else ""
    )


def _po_blocker_html(blocker: dict) -> str:
    detail = blocker.get("detail") or {}
    identity = (
        detail.get("gate_name")
        or detail.get("exception_type")
        or blocker.get("type")
        or "READINESS_BLOCKER"
    )
    message = detail.get("message") or "Required readiness evidence is unavailable."
    return f"<li><b>{_html_escape(identity)}</b> — {_html_escape(message)}</li>"


def _admin_status_html(h: dict, *, nav_root: str) -> str:
    """Render backend-owned health/readiness results without evaluating gates."""
    readiness = h.get("po_readiness") or {}
    gates = readiness.get("gates") or []
    shopify = h["shopify_credentials"]

    gate_rows = []
    for gate in gates:
        status = str(gate.get("status") or "UNKNOWN")
        css_class = {
            "PASS": "gate-pass",
            "WARN": "gate-warn",
            "FAIL": "gate-fail",
        }.get(status, "gate-fail")
        scope_type = gate.get("scope_type") or "GLOBAL"
        scope_id = gate.get("scope_id") or ""
        scope = scope_type if not scope_id else f"{scope_type}: {scope_id}"
        gate_rows.append(
            f"<tr><td>{_html_escape(gate.get('gate_name'))}</td>"
            f"<td>{_html_escape(scope)}</td>"
            f"<td class='{css_class}'>{_html_escape(status)}</td>"
            f"<td>{_html_escape(gate.get('message'))}</td></tr>"
        )

    seed = h.get("seed", {})
    seed_detail = (
        f"latest import: {seed.get('latest_import_at', '—')} · "
        f"validation: {seed.get('latest_validation_result', '—')}"
        if seed.get("imported") else seed.get("reason", "not imported")
    )
    rows = [
        ("Application", h["application"]["ok"], f"v{h['application']['version']}"),
        ("Database connectivity", h.get("database", {}).get("ok", False),
         h.get("database", {}).get("reason", "")),
        ("Database URL guard (PostgreSQL required)", h["database_url_guard"]["ok"],
         h["database_url_guard"].get("reason", "")),
        ("Schema / migrations", h.get("schema", {}).get("ok", False),
         f"missing: {h.get('schema', {}).get('missing_core_tables', [])}"
         if not h.get("schema", {}).get("ok") else ""),
        ("Seed import", seed.get("ok", False), seed_detail),
        ("Shopify credentials", shopify["configured"],
         "" if shopify["configured"] else
         f"not configured (missing: {', '.join(shopify['missing_vars'])})"),
    ]
    components = "".join(
        f"<tr><td>{_html_escape(name)}</td><td>{STATUS_BADGE[bool(ok)]}</td>"
        f"<td>{_html_escape(note)}</td></tr>"
        for name, ok, note in rows
    )

    canonical_enabled = bool(readiness.get("po_generation_enabled", False))
    synthetic_demo = _request_runtime_mode() == "SYNTHETIC_DEMO"
    enabled = canonical_enabled and not synthetic_demo
    po_state = (
        "BLOCKED — SYNTHETIC DEMO / INTERNAL DRAFT ONLY"
        if synthetic_demo
        else ("ENABLED" if enabled else "DISABLED")
    )
    po_class = "po-enabled" if enabled else "po-disabled"
    blockers = readiness.get("blockers") or []
    blocker_detail = (
        "<ul>" + "".join(_po_blocker_html(item) for item in blockers) + "</ul>"
        if blockers else "<p>No canonical PO readiness blockers.</p>"
    )
    if synthetic_demo:
        blocker_detail = (
            "<p><b>TEST DATA — NOT FOR ORDERING.</b> Canonical gate facts below "
            "do not authorize activation, release, Shopify import, or an order.</p>"
            + blocker_detail
        )
    disabled_control = (
        "<button type='button' disabled aria-disabled='true'>"
        "PO generation disabled</button>"
        if not enabled else ""
    )
    rendered_gates = "".join(gate_rows) or (
        "<tr><td colspan='4'>Canonical readiness gates are unavailable.</td></tr>"
    )
    navigation = _operational_nav(nav_root, current="System Readiness")

    return f"""<!doctype html><html><head><title>Buffalo Procurement OS — System Readiness</title>
<style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
table{{border-collapse:collapse;width:100%;margin-bottom:2rem}}td,th{{border:1px solid #d1d9e0;padding:8px 12px;text-align:left;font-size:14px}}
th{{background:#f6f8fa}}h1{{font-size:22px}}h2{{font-size:17px}}.gate-pass{{color:#116329;font-weight:700}}
.gate-warn{{color:#7d4e00;font-weight:700}}.gate-fail{{color:#82071e;font-weight:700}}
.po-enabled,.po-disabled{{border:1px solid;border-radius:6px;padding:12px 16px;margin-bottom:20px}}
.po-enabled{{background:#dafbe1;border-color:#4ac26b;color:#116329}}.po-disabled{{background:#ffebe9;border-color:#ff8182;color:#82071e}}
.po-enabled h2,.po-disabled h2{{margin:0}}button[disabled]{{padding:7px 11px;border:1px solid #afb8c1;border-radius:5px;color:#59636e;cursor:not-allowed}}</style>
</head><body>{navigation}
<h1>Buffalo Procurement OS — System Readiness</h1>
<section class='{po_class}'><h2>PO generation: {po_state}</h2>{blocker_detail}{disabled_control}</section>
<h2>Components</h2>
<table><tr><th>Component</th><th>Status</th><th>Detail</th></tr>{components}</table>
<h2>Readiness gates</h2>
<table><tr><th>Gate</th><th>Scope</th><th>Status</th><th>Canonical message / blocker</th></tr>
{rendered_gates}</table>
<p style="color:#59636e;font-size:13px">Machine-readable: <a href="{nav_root}health/full">/health/full</a></p>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/admin/status", response_class=HTMLResponse)
def admin_status(request: Request):
    """Operational system-readiness page; presentation is read-only."""
    nav_root = "" if request.url.path == "/" else "../"
    return _admin_status_html(full_health(), nav_root=nav_root)


def _display_count(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return _html_escape(value)


def _display_boolean(value) -> str:
    if value is None:
        return "—"
    return "yes" if value is True else "no"


def _run_readiness_html(readiness: dict) -> str:
    status = readiness.get("status") or "MISSING"
    css_class = {
        "PASS": "gate-pass",
        "WARN": "gate-warn",
        "FAIL": "gate-fail",
    }.get(str(status), "gate-fail")
    blockers = readiness.get("blockers") or []
    blocker_text = ", ".join(str(item) for item in blockers) or "none"
    return (
        f"<p>Readiness: <b class='{css_class}'>{_html_escape(status)}</b> — "
        f"{_html_escape(readiness.get('message'))}</p>"
        f"<p>Readiness blockers: {_html_escape(blocker_text)}</p>"
    )


def _data_sync_runs_html(data: dict, *, nav_root: str) -> str:
    """Render existing durable run evidence without exposing an action surface."""
    catalog = data["catalog"]
    catalog_run = catalog.get("run")
    if catalog_run:
        catalog_body = f"""<table><tr><th>Run ID</th><td>{_html_escape(catalog_run.get('catalog_sync_id'))}</td></tr>
<tr><th>Started</th><td>{_html_escape(catalog_run.get('started_at'))}</td></tr>
<tr><th>Completed</th><td>{_html_escape(catalog_run.get('completed_at'))}</td></tr>
<tr><th>Run status</th><td>{_html_escape(catalog_run.get('status'))}</td></tr>
<tr><th>Shopify API</th><td>{_html_escape(catalog_run.get('shopify_api_version'))}</td></tr>
<tr><th>Pagination complete</th><td>{_display_boolean(catalog_run.get('pagination_complete'))}</td></tr>
<tr><th>Live rows</th><td>{_display_count(catalog_run.get('live_rows_received'))}</td></tr>
<tr><th>Shopify-reported variants</th><td>{_display_count(catalog_run.get('shopify_reported_variant_count'))}</td></tr>
<tr><th>Exact current IDs</th><td>{_display_count(catalog_run.get('exact_current_ids'))}</td></tr>
<tr><th>New live variants</th><td>{_display_count(catalog_run.get('new_live_variants'))}</td></tr>
<tr><th>Unresolved blockers</th><td>{_display_count(catalog_run.get('unresolved_blockers'))}</td></tr></table>"""
    else:
        catalog_body = "<p>No authoritative catalog attempt is available.</p>"

    sales = data["historical_sales"]
    sales_run = sales.get("run")
    if sales_run:
        sales_body = f"""<table><tr><th>Run ID</th><td>{_html_escape(sales_run.get('sales_backfill_id'))}</td></tr>
<tr><th>Requested range</th><td>{_html_escape(sales_run.get('start_date'))} through {_html_escape(sales_run.get('end_date'))}</td></tr>
<tr><th>Started</th><td>{_html_escape(sales_run.get('started_at'))}</td></tr>
<tr><th>Completed</th><td>{_html_escape(sales_run.get('completed_at'))}</td></tr>
<tr><th>Run status</th><td>{_html_escape(sales_run.get('status'))}</td></tr>
<tr><th>Chunk completion</th><td>{_display_count(sales_run.get('completed_chunks'))} / {_display_count(sales_run.get('expected_chunks'))}</td></tr>
<tr><th>Page completion</th><td>{_display_count(sales_run.get('completed_pages'))} / {_display_count(sales_run.get('expected_pages'))}</td></tr>
<tr><th>Source facts</th><td>{_display_count(sales_run.get('unique_source_facts'))}</td></tr>
<tr><th>Resolution state</th><td>{_display_count(sales_run.get('resolved_rows'))} resolved · {_display_count(sales_run.get('excluded_rows'))} excluded · {_display_count(sales_run.get('unresolved_rows'))} unresolved · {_display_count(sales_run.get('ambiguous_rows'))} ambiguous</td></tr>
<tr><th>Coverage complete</th><td>{_display_boolean(sales_run.get('coverage_complete'))}</td></tr>
<tr><th>Pages complete</th><td>{_display_boolean(sales_run.get('pages_complete'))}</td></tr>
<tr><th>Source facts persisted</th><td>{_display_boolean(sales_run.get('source_facts_persisted'))}</td></tr>
<tr><th>Idempotency verified</th><td>{_display_boolean(sales_run.get('idempotency_verified'))}</td></tr>
<tr><th>Control totals reconciled</th><td>{_display_boolean(sales_run.get('control_totals_reconciled'))}</td></tr>
<tr><th>Canonical aggregate current</th><td>{_display_boolean(sales_run.get('canonical_aggregate_rebuilt'))}</td></tr></table>"""
    else:
        sales_body = "<p>No historical-sales run evidence is available.</p>"

    navigation = _operational_nav(nav_root, current="Data/Sync Runs")
    return f"""<!doctype html><html><head><title>Data/Sync Runs</title>
<style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
h1{{font-size:24px}}h2{{font-size:18px;margin-top:26px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #d1d9e0;padding:7px 10px;text-align:left;font-size:13px}}th{{background:#f6f8fa;width:230px}}
.gate-pass{{color:#116329}}.gate-warn{{color:#7d4e00}}.gate-fail{{color:#82071e}}.muted{{color:#59636e;font-size:13px}}</style>
</head><body>{navigation}<h1>Data/Sync Runs</h1>
<p class='muted'>Read-only evidence from durable catalog and historical-sales checkpoints.</p>
<section><h2>Catalog</h2><p>Selection: {_html_escape(catalog.get('selection'))}</p>
{_run_readiness_html(catalog.get('readiness') or {})}{catalog_body}</section>
<section><h2>Historical sales</h2><p>Selection: {_html_escape(sales.get('selection'))}</p>
{_run_readiness_html(sales.get('readiness') or {})}{sales_body}</section>
</body></html>"""


@app.get("/data-sync-runs", response_class=HTMLResponse)
def data_sync_runs_page():
    """Read-only durable run/checkpoint evidence; never starts or retries a job."""
    with _db_conn() as conn:
        data = data_sync_run_status(conn)
    return _data_sync_runs_html(data, nav_root="")


@app.get("/inventory-snapshots/status")
def inventory_snapshots_status(as_of: date | None = None):
    """Read-only owned inventory snapshot evidence; never starts a capture."""
    with _db_conn() as conn:
        return latest_inventory_snapshot_status(
            conn, as_of_date=as_of or inventory_business_date()
        )


def _vendor_rules_html(result: dict) -> str:
    cards = []
    for vendor in result["vendors"]:
        rules = vendor.get("rules") or {}
        version = int(rules.get("rules_version") or 0)
        order_days = ", ".join(rules.get("order_days") or [])
        delivery_days = ", ".join(rules.get("expected_delivery_days") or [])
        minimum_type = rules.get("minimum_type") or ""
        options = "".join(
            f"<option value='{kind}'{' selected' if minimum_type == kind else ''}>{kind}</option>"
            for kind in ("NONE", "CASE", "DOLLAR")
        )
        checked = " checked" if rules.get("loose_order_allowed") else ""
        disabled = " disabled" if not vendor["active"] else ""
        cards.append(
            f"""<section class='vendor-card'><h2>{_html_escape(vendor['vendor_name'])}</h2>
<p><b>{_html_escape(vendor['status'])}</b> — {_html_escape(vendor['message'])}</p>
<form method='post' action='/vendor-rules/{_html_escape(vendor['vendor_id'])}'>
<input type='hidden' name='expected_version' value='{version}'>
<label>Order days (comma-separated)<input name='order_days' value='{_form_value(order_days)}' required{disabled}></label>
<label>Order cutoff (local)<input name='order_cutoff_local' type='time' value='{_form_value(rules.get('order_cutoff_local'))}' required{disabled}></label>
<label>IANA timezone<input name='timezone_name' value='{_form_value(rules.get('timezone_name'))}' required{disabled}></label>
<label>Expected delivery days<input name='expected_delivery_days' value='{_form_value(delivery_days)}' required{disabled}></label>
<label>Order cycle days<input name='order_cycle_days' type='number' min='1' value='{_form_value(rules.get('order_cycle_days'))}' required{disabled}></label>
<label>Lead time days<input name='lead_time_days' type='number' min='0' value='{_form_value(rules.get('lead_time_days'))}' required{disabled}></label>
<label>Lead-time variability days<input name='lead_time_variability_days' type='number' min='0' step='0.01' value='{_form_value(rules.get('lead_time_variability_days'))}' required{disabled}></label>
<label>Reliability (0–1)<input name='reliability_pct' type='number' min='0' max='1' step='0.000001' value='{_form_value(rules.get('reliability_pct'))}' required{disabled}></label>
<label>Minimum type<select name='minimum_type' required{disabled}><option value=''>Choose…</option>{options}</select></label>
<label>Minimum value<input name='minimum_value' type='number' min='0' step='0.01' value='{_form_value(rules.get('minimum_value'))}'{disabled}></label>
<label>Below-minimum fee<input name='below_minimum_fee' type='number' min='0' step='0.01' value='{_form_value(rules.get('below_minimum_fee'))}' required{disabled}></label>
<label class='check'><input name='loose_order_allowed' type='checkbox'{checked}{disabled}> Loose/broken-case ordering allowed</label>
<label>Loose-unit fee<input name='loose_unit_fee' type='number' min='0' step='0.01' value='{_form_value(rules.get('loose_unit_fee'))}'{disabled}></label>
<label>Special rules<textarea name='special_rules'{disabled}>{_form_value(rules.get('special_rules'))}</textarea></label>
<label>Holiday / blackout notes<textarea name='holiday_blackout_notes'{disabled}>{_form_value(rules.get('holiday_blackout_notes'))}</textarea></label>
<label>Confirmation source<input name='confirmation_source' value='{_form_value(rules.get('confirmation_source'))}' required{disabled}></label>
<label>Owner/operator<input name='actor' required{disabled}></label>
<label>Change reason<input name='reason' required{disabled}></label>
<label>Review token<input name='review_token' type='password' required{disabled}></label>
<button type='submit'{disabled}>Save confirmed vendor rules</button></form></section>"""
        )
    return f"""<!doctype html><html><head><title>Vendor Rules</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
.summary,.vendor-card{{border:1px solid #d1d9e0;border-radius:8px;padding:14px;margin:14px 0}}
form{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}label{{display:flex;flex-direction:column;font-size:12px;gap:3px}}
input,select,textarea,button{{font:inherit;padding:7px}}.check{{display:block}}button{{align-self:end}}</style></head><body>
{_operational_nav('/', current='Vendor Rules')}
<h1>Vendor Operating Rules</h1><section class='summary'><b>{_html_escape(result['status'])}</b> — {_html_escape(result['message'])}</section>
{''.join(cards) or '<p>No vendors are configured.</p>'}
<p>Blank or unconfirmed material fields keep VENDOR_RULES failed. Vendor minimums never authorize filler.</p>
</body></html>"""


@app.get("/vendor-rules", response_class=HTMLResponse)
def vendor_rules_page():
    with _db_conn() as conn:
        result = evaluate_vendor_rules(conn)
    return _vendor_rules_html(result)


@app.post("/vendor-rules/{vendor_id}", response_class=HTMLResponse)
def vendor_rules_update_page(
    vendor_id: str,
    order_days: str = Form(...),
    order_cutoff_local: str = Form(...),
    timezone_name: str = Form(...),
    expected_delivery_days: str = Form(...),
    order_cycle_days: int = Form(...),
    lead_time_days: int = Form(...),
    lead_time_variability_days: str = Form(...),
    reliability_pct: str = Form(...),
    minimum_type: str = Form(...),
    minimum_value: str = Form(""),
    below_minimum_fee: str = Form(...),
    loose_order_allowed: bool = Form(False),
    loose_unit_fee: str = Form(""),
    special_rules: str = Form(""),
    holiday_blackout_notes: str = Form(""),
    confirmation_source: str = Form(...),
    actor: str = Form(...),
    reason: str = Form(...),
    expected_version: int = Form(...),
    review_token: str = _review_token_form(),
):
    _require_review_token(review_token)
    actor = _server_audit_actor(actor)
    rules = {
        "order_days": order_days.split(","),
        "order_cutoff_local": order_cutoff_local,
        "timezone_name": timezone_name,
        "expected_delivery_days": expected_delivery_days.split(","),
        "order_cycle_days": order_cycle_days,
        "lead_time_days": lead_time_days,
        "lead_time_variability_days": lead_time_variability_days,
        "reliability_pct": reliability_pct,
        "minimum_type": minimum_type,
        "minimum_value": minimum_value,
        "below_minimum_fee": below_minimum_fee,
        "loose_order_allowed": loose_order_allowed,
        "loose_unit_fee": loose_unit_fee,
        "special_rules": special_rules,
        "holiday_blackout_notes": holiday_blackout_notes,
        "confirmation_source": confirmation_source,
    }
    try:
        with _db_conn() as conn:
            update_vendor_rules(
                conn,
                vendor_id=vendor_id,
                rules=rules,
                actor=actor,
                reason=reason,
                expected_version=expected_version,
            )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return "<meta http-equiv='refresh' content='0;url=/vendor-rules'><p>Vendor rules saved.</p>"


@app.get("/rules")
def rules():
    return load_rules()


@app.get("/foundation/status")
def foundation_status():
    if not os.getenv("DATABASE_URL"):
        return _runtime_labeled_status(
            {"database_configured": False, "po_generation_enabled": False, "reason": "DATABASE_URL is not configured"}
        )
    with _db_conn() as conn:
        result = po_readiness(conn)
    return _runtime_labeled_status({"database_configured": True, **result})


_MAPPING_STATE_FIELDS = (
    "distributor_product_id",
    "supplier_code",
    "package_type",
    "size",
    "raw_pack",
    "physical_units",
    "retail_pack_units",
    "shopify_units",
    "qualifying_units",
    "assortment_scope",
    "assortment_group",
    "assortable",
)


def _mapping_state(candidate: dict, field: str) -> str:
    state = candidate.get(f"{field}_state") or "ABSENT"
    if state == "VALUE":
        return f"VALUE: {_html_escape(candidate.get(f'{field}_value'))}"
    return _html_escape(state)


def _mapping_banner() -> str:
    mode = _request_runtime_mode()
    return (
        "<div style='border:2px solid #b42318;background:#ffebe9;padding:12px;"
        "font-weight:700;margin:12px 0'>TEST DATA — NOT FOR ORDERING · "
        f"{_html_escape(mode)} · SHADOW ONLY · no offer activation, price "
        "authority, recommendation cutover, Shopify write, PO release, or order.</div>"
    )


_TRANSIENT_MAPPING_ERROR_CODES = frozenset(
    {
        "SESSION_LOCK_TIMEOUT",
        "SESSION_LOCK_CLEANUP_FAILED",
        "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED",
        "COMMIT_OUTCOME_UNKNOWN",
    }
)


def _mapping_error_status(error: PersistentMappingError) -> int:
    return 503 if error.code in _TRANSIENT_MAPPING_ERROR_CODES else 409


def _supplier_mapping_list_html(
    result: dict,
    status_result: dict,
    *,
    query: str,
    supplier: str,
    status: str,
    limit: int,
    offset: int,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td><a href='/supplier-mapping/{_html_escape(item['candidate_id'])}'>"
        f"{_html_escape(item['occurrence_key'])}</a></td>"
        f"<td>{_html_escape(item['source_vendor_identity'])}</td>"
        f"<td>{_html_escape(item['proposed_variant_id'])}</td>"
        f"<td>{_mapping_state(item, 'supplier_code')}</td>"
        f"<td>{_html_escape(item['offer_class'])}</td>"
        f"<td>{_html_escape(item.get('decision_action') or 'PENDING')}</td>"
        "</tr>"
        for item in result["items"]
    ) or "<tr><td colspan='6'>No candidates match this page.</td></tr>"
    def page_url(page_offset: int) -> str:
        return "/supplier-mapping?" + urlencode(
            {
                "query": query,
                "supplier": supplier,
                "status": status,
                "limit": limit,
                "offset": page_offset,
            }
        )

    previous = (
        f"<a rel='prev' href='{_form_value(page_url(max(0, offset - limit)))}'>← Previous</a>"
        if offset > 0
        else ""
    )
    following = (
        f"<a rel='next' href='{_form_value(page_url(offset + limit))}'>Next →</a>"
        if offset + len(result["items"]) < result["total"]
        else ""
    )
    pagination = " · ".join(item for item in (previous, following) if item) or "End of results"
    return f"""<!doctype html><html><head><title>Supplier Mapping</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1180px;margin:2rem auto;padding:0 1rem;color:#1f2328}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d1d9e0;padding:7px;text-align:left}}input,select,button{{padding:7px}}</style></head><body>
{_operational_nav('/', current='Supplier Mapping')}
<h1>Persistent supplier mapping review</h1>
<p>Batches: {_html_escape(status_result['review_batch_count'])} · candidates: {_html_escape(status_result['candidate_count'])} · decisions: {_html_escape(status_result['decision_count'])} · selection heads: {_html_escape(status_result['selection_head_count'])} · exact legacy shadow matches: {_html_escape(status_result['shadow_match_count'])}</p>
<form method='post' action='/supplier-mapping/intake'><button>Verify and intake the fixed synthetic review suite</button></form>
<form method='get'><label>Search <input name='query' value='{_form_value(query)}'></label>
<label>Supplier <input name='supplier' value='{_form_value(supplier)}'></label>
<label>Status <select name='status'><option value=''>All</option><option value='PENDING'{' selected' if status == 'PENDING' else ''}>Pending</option><option value='DECIDED'{' selected' if status == 'DECIDED' else ''}>Decided</option><option value='DEFER'{' selected' if status == 'DEFER' else ''}>Deferred</option><option value='APPROVE_MAPPING'{' selected' if status == 'APPROVE_MAPPING' else ''}>Approved mapping</option><option value='REJECT_MAPPING'{' selected' if status == 'REJECT_MAPPING' else ''}>Rejected mapping</option></select></label>
<label>Page size <select name='limit'>{''.join(f"<option value='{size}'{' selected' if limit == size else ''}>{size}</option>" for size in (10, 25, 50, 100))}</select></label><input type='hidden' name='offset' value='0'><button>Filter</button></form>
<p>Showing {_html_escape(len(result['items']))} of {_html_escape(result['total'])}; results are server-paged. {pagination}</p>
<table><thead><tr><th>Occurrence</th><th>Supplier</th><th>Variant ID</th><th>Supplier code state</th><th>Class</th><th>Disposition</th></tr></thead><tbody>{rows}</tbody></table>
<form method='post' action='/auth/logout' style='margin-top:24px'><button>Sign out</button></form>
</body></html>"""


@app.get("/supplier-mapping/status")
def supplier_mapping_status():
    try:
        require_synthetic_mapping_capability("selected_offer_shadow_reads_enabled")
        with _db_conn() as conn:
            return mapping_status(conn)
    except PersistentMappingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/supplier-mapping", response_class=HTMLResponse)
def supplier_mapping_page(
    query: str = "", supplier: str = "", status: str = "", limit: int = 50, offset: int = 0
):
    try:
        require_synthetic_mapping_capability("selected_offer_shadow_reads_enabled")
        with _db_conn() as conn:
            result = list_mapping_candidates(
                conn, query=query, supplier=supplier, status=status, limit=limit, offset=offset
            )
            status_result = mapping_status(conn)
    except PersistentMappingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HTMLResponse(
        _supplier_mapping_list_html(
            result,
            status_result,
            query=query,
            supplier=supplier,
            status=status,
            limit=limit,
            offset=offset,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/supplier-mapping/intake")
def supplier_mapping_intake(request: Request):
    principal = action_principal(request, "procurement.review.intake")
    try:
        require_synthetic_mapping_capability("review_intake_writes_enabled")
        with _db_conn() as conn:
            marker = conn.execute(
                "SELECT value FROM meta WHERE key=%s",
                ("synthetic_multivendor_acceptance_contract",),
            ).fetchone()
        if marker is None:
            packets = load_synthetic_mapping_packets()
        elif marker == ("BUFFALO_SYNTHETIC_MULTIVENDOR_ACCEPTANCE_V2",):
            packets = load_synthetic_multivendor_mapping_packets()
        else:
            raise PersistentMappingError(
                "synthetic multivendor fixture registration differs"
            )
        database_url = _database_url()
        results = [
            execute_supplier_mapping_intake(
                database_url,
                package=packet["package"],
                candidates=packet["candidates"],
                principal=principal,
                intake_idempotency_key=packet["intake_idempotency_key"],
            )
            for packet in packets
        ]
        if len(results) != len(packets):
            raise PersistentMappingError("synthetic intake scenario count differs")
    except PersistentMappingError as exc:
        raise HTTPException(status_code=_mapping_error_status(exc), detail=str(exc)) from exc
    except SyntheticPacketError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url="/supplier-mapping", status_code=303)


def _supplier_mapping_detail_html(detail: dict) -> str:
    candidate = detail["candidate"]
    batch = detail["batch"]
    states = "".join(
        f"<tr><th>{_html_escape(field)}</th><td>{_mapping_state(candidate, field)}</td></tr>"
        for field in _MAPPING_STATE_FIELDS
    )
    comparisons = "".join(
        "<tr>"
        f"<td>{_html_escape(item['offer']['offer_id'])}</td>"
        f"<td>{_html_escape(item['offer']['supplier_sku'])}</td>"
        f"<td>{_html_escape(item['offer']['package_type'])}</td>"
        f"<td>{_html_escape(item['offer']['active'])}</td>"
        f"<td>{_html_escape(item['offer']['confidence'])}</td>"
        f"<td>{'EXACT' if item['exact_contract_match'] else 'DIFFERS'}</td></tr>"
        for item in detail["offer_comparisons"]
    ) or "<tr><td colspan='6'>No candidate Variant/vendor offers.</td></tr>"
    approval_eligible = bool(
        candidate.get("proposed_variant_id")
        and candidate.get("proposed_vendor_id")
        and candidate.get("supplier_identity_key_sha256")
        and candidate.get("operational_offer_key_sha256")
        and candidate.get("offer_class") in {
            "REGULAR", "GIFT", "SPECIAL", "ALTERNATE", "COMPONENT", "COMBO"
        }
        and candidate.get("assortment_scope_state") == "VALUE"
        and not candidate.get("blockers")
        and batch.get("structural_state") == "READY"
        and batch.get("source_evidence_state") == "READY"
        and batch.get("semantic_state") == "READY"
        and batch.get("source_is_simulation") is False
    )
    exact_options = "".join(
        f"<option value='{_form_value(item['offer']['offer_id'])}'>Offer {_html_escape(item['offer']['offer_id'])} — {_html_escape(item['offer']['supplier_sku'])}</option>"
        for item in detail["offer_comparisons"]
        if approval_eligible and item["exact_contract_match"]
    )
    can_create_inactive = approval_eligible
    can_reject = bool(
        candidate.get("proposed_variant_id")
        and candidate.get("proposed_vendor_id")
        and candidate.get("supplier_identity_key_sha256")
        and candidate.get("distributor_product_id_state") == "VALUE"
        and str(candidate.get("distributor_product_id_value") or "").strip()
    )
    disposition_options = "".join(
        (
            "<option value='DEFER'>DEFER — no operational effect</option>",
            "<option value='REJECT_MAPPING'>REJECT_MAPPING — append exact rejection memory</option>"
            if can_reject
            else "",
            "<option value='APPROVE_MAPPING'>APPROVE_MAPPING — exact offer result</option>"
            if exact_options or can_create_inactive
            else "",
        )
    )
    offer_result_options = "<option value=''>Not applicable</option>"
    if exact_options:
        offer_result_options += "<option value='LINKED_EXISTING'>LINKED_EXISTING</option>"
    if can_create_inactive:
        offer_result_options += (
            "<option value='CREATED_INACTIVE'>CREATED_INACTIVE — unpriced, unselected, inactive</option>"
        )
    decisions = "".join(
        f"<li>{_html_escape(item['decided_at'])}: <b>{_html_escape(item['action'])}</b> — {_html_escape(item['reason'])} (principal {_html_escape(item['human_principal_ref'])})</li>"
        for item in detail["decisions"]
    ) or "<li>No decision yet.</li>"
    rejections = "".join(
        "<li>"
        f"{_html_escape(item.get('rejected_at'))}: Variant {_html_escape(item.get('rejected_variant_id'))} / "
        f"vendor {_html_escape(item.get('vendor_id'))} / source {_html_escape(item.get('source_text'))} "
        f"(principal {_html_escape(item.get('rejected_by'))})"
        "</li>"
        for item in detail.get("rejections", [])
    ) or "<li>No active exact rejection memory.</li>"
    shadow = "".join(
        f"<li>Variant {_html_escape(item['variant_id'])}: {_html_escape(item['selection_state'])}; legacy comparison <b>{_html_escape(item['shadow_comparison'])}</b></li>"
        for item in detail["shadow"]
    ) or "<li>No routine selection head.</li>"
    decision_key = uuid4()
    selection_form = ""
    effective = detail["effective_decision"]
    if effective and effective.get("action") == "APPROVE_MAPPING":
        selection_key = uuid4()
        selection_form = f"""<h2>Separate routine selection</h2><form method='post' action='/supplier-mapping/{_html_escape(candidate['candidate_id'])}/selection'>
<input type='hidden' name='idempotency_key' value='{selection_key}'>
<input type='hidden' name='selection_action' value='SELECT'>
<label>Effective from <input type='date' name='effective_from' value='{date.today().isoformat()}' required></label>
<label>Reason <input name='reason' required></label><button>Preview separate selection</button></form>"""
    if detail["shadow"] and detail["shadow"][0].get("selection_state") != "EXPLICITLY_CLEARED":
        clear_key = uuid4()
        selection_form += f"""<form method='post' action='/supplier-mapping/{_html_escape(candidate['candidate_id'])}/selection'>
<input type='hidden' name='idempotency_key' value='{clear_key}'>
<input type='hidden' name='selection_action' value='CLEAR'>
<label>Clear effective from <input type='date' name='effective_from' value='{date.today().isoformat()}' required></label>
<label>Reason <input name='reason' required></label><button>Preview explicit routine-selection CLEAR</button></form>"""
    return f"""<!doctype html><html><head><title>Mapping candidate</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1180px;margin:2rem auto;padding:0 1rem;color:#1f2328}}table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #d1d9e0;padding:7px;text-align:left;vertical-align:top}}form{{display:grid;gap:8px;max-width:760px;margin:12px 0}}input,select,button{{padding:7px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:10px}}</style></head><body>
{_operational_nav('/', current='Supplier Mapping')}<p><a href='/supplier-mapping'>← Candidate list</a> · <a href='/supplier-mapping/{_html_escape(candidate['candidate_id'])}/source'>Download bounded evidence metadata</a></p>
<h1>{_html_escape(candidate['occurrence_key'])}</h1>
<p>Supplier {_html_escape(candidate['source_vendor_identity'])} · proposed Variant {_html_escape(candidate['proposed_variant_id'])} · class {_html_escape(candidate['offer_class'])}/{_html_escape(candidate['occurrence_role'])}</p>
<h2>Sealed source status</h2><table><tr><th>Package</th><td>{_html_escape(batch['source_package_id'])} rev {_html_escape(batch['source_revision'])}</td></tr><tr><th>Readiness</th><td>{_html_escape(batch['structural_state'])} / {_html_escape(batch['source_evidence_state'])} / {_html_escape(batch['semantic_state'])}</td></tr><tr><th>Authority/import</th><td>{_html_escape(batch['source_authority_state'])} / {_html_escape(batch['source_import_state'])}</td></tr><tr><th>Fixture flag</th><td>source_is_simulation={_html_escape(batch['source_is_simulation'])}; a false value in this owned demo only exercises the authoritative-format contract branch and is not real approval.</td></tr><tr><th>File</th><td>{_html_escape(candidate['source_file_name'])} · SHA-256 {_html_escape(candidate['source_file_sha256'])} · pages {_html_escape(candidate['source_page_start'])}–{_html_escape(candidate['source_page_end'])}</td></tr></table>
<h2>Typed candidate evidence</h2><table>{states}</table><p>Blockers:</p><pre>{_html_escape(json.dumps(candidate['blockers'], sort_keys=True, default=str))}</pre><p>Independent linkage evidence:</p><pre>{_html_escape(json.dumps(candidate['independent_linkage_evidence'], sort_keys=True, default=str))}</pre>
<h2>Existing offer comparison</h2><table><tr><th>ID</th><th>SKU</th><th>Package</th><th>Active</th><th>Confidence</th><th>Exact candidate contract</th></tr>{comparisons}</table>
<h2>Append-only decision history</h2><ul>{decisions}</ul><h2>Active exact rejection memory</h2><ul>{rejections}</ul>
<form method='post' action='/supplier-mapping/{_html_escape(candidate['candidate_id'])}/decision'><input type='hidden' name='idempotency_key' value='{decision_key}'><label>Disposition <select name='action'>{disposition_options}</select></label><label>Approval result <select name='offer_link_kind'>{offer_result_options}</select></label><label>Exact existing offer (LINKED_EXISTING only)<select name='existing_offer_id'><option value=''>None</option>{exact_options}</select></label><label>Reason <input name='reason' required></label><button>Preview disposition</button></form>
{selection_form}<h2>Routine-selection shadow — SHADOW ONLY</h2><ul>{shadow}</ul>
</body></html>"""


@app.get("/supplier-mapping/{candidate_id}", response_class=HTMLResponse)
def supplier_mapping_detail(candidate_id: UUID):
    try:
        require_synthetic_mapping_capability("selected_offer_shadow_reads_enabled")
        with _db_conn() as conn:
            detail = get_mapping_candidate_detail(conn, candidate_id)
    except PersistentMappingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return HTMLResponse(_supplier_mapping_detail_html(detail), headers={"Cache-Control": "no-store"})


@app.get("/supplier-mapping/{candidate_id}/source")
def supplier_mapping_source(candidate_id: UUID):
    try:
        require_synthetic_mapping_capability("selected_offer_shadow_reads_enabled")
        with _db_conn() as conn:
            detail = get_mapping_candidate_detail(conn, candidate_id)
    except PersistentMappingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    evidence = {
        "safety_label": "TEST DATA — NOT FOR ORDERING",
        "data_mode": "SYNTHETIC_DEMO",
        "source_disclosure": (
            "A false source_is_simulation value in this owned demo exercises "
            "only the fabricated authoritative-format contract branch; it is "
            "not real supplier evidence or approval."
        ),
        "contract": "BUFFALO_MAPPING_EVIDENCE_METADATA_ONLY_V1",
        "authority": "REVIEW_ONLY_NOT_SOURCE_BLOB",
        "candidate": detail["candidate"],
        "batch": detail["batch"],
    }
    data = json.dumps(evidence, sort_keys=True, indent=2, default=str).encode("utf-8") + b"\n"
    return Response(
        data,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename=mapping-evidence-{candidate_id}.json",
        },
    )


def _mapping_confirmation_html(
    *, candidate_id: UUID, kind: str, values: dict[str, str], preview_sha256: str
) -> str:
    hidden = "".join(
        f"<input type='hidden' name='{_form_value(key)}' value='{_form_value(value)}'>"
        for key, value in values.items()
    )
    visible = "".join(
        f"<dt><b>{_html_escape(key.replace('_', ' ').title())}</b></dt>"
        f"<dd>{_html_escape(value) if value else 'not provided'}</dd>"
        for key, value in values.items()
    )
    return f"""<!doctype html><html><head><title>Confirm {kind}</title></head><body>
{_mapping_banner()}<h1>Confirm {kind}</h1><p>This is a separate exact confirmation. No mapping or selection has been written by the preview.</p>
<p>Candidate: <code>{_html_escape(candidate_id)}</code></p><dl>{visible}</dl>
<p>Preview SHA-256: <code>{_html_escape(preview_sha256)}</code></p>
<form method='post'>{hidden}<input type='hidden' name='expected_preview_sha256' value='{_form_value(preview_sha256)}'><input type='hidden' name='confirm' value='CONFIRM'><button>CONFIRM exact {kind}</button></form>
<p><a href='/supplier-mapping/{candidate_id}'>Cancel without writing</a></p></body></html>"""


@app.post("/supplier-mapping/{candidate_id}/decision", response_class=HTMLResponse)
def supplier_mapping_decision(
    request: Request,
    candidate_id: UUID,
    action: str = Form(...),
    reason: str = Form(...),
    idempotency_key: UUID = Form(...),
    existing_offer_id: int | None = Form(None),
    offer_link_kind: str | None = Form(None),
    expected_preview_sha256: str | None = Form(None),
    confirm: str | None = Form(None),
):
    principal = action_principal(request, "procurement.mapping.approve")
    try:
        require_synthetic_mapping_capability("human_mapping_writes_enabled")
        with _db_conn() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            detail = get_mapping_candidate_detail(conn, candidate_id)
            normalized_action = action.strip().upper()
            normalized_link_kind = (offer_link_kind or "").strip().upper() or None
            if normalized_action == "APPROVE_MAPPING" and normalized_link_kind == "LINKED_EXISTING":
                exact_ids = {
                    int(item["offer"]["offer_id"])
                    for item in detail["offer_comparisons"]
                    if item["exact_contract_match"]
                }
                if existing_offer_id not in exact_ids:
                    raise PersistentMappingError("approval requires an exact compared existing offer")
            elif normalized_action == "APPROVE_MAPPING" and normalized_link_kind == "CREATED_INACTIVE":
                if existing_offer_id is not None:
                    raise PersistentMappingError("created-inactive approval cannot name an offer")
            elif normalized_action == "APPROVE_MAPPING":
                raise PersistentMappingError("approval result disposition is required")
            preview = preview_mapping_decision(
                conn,
                candidate_id=candidate_id,
                action=action,
                reason=reason,
                principal=principal,
                decision_idempotency_key=idempotency_key,
                existing_offer_id=existing_offer_id,
                offer_link_kind=normalized_link_kind,
            )
            if not expected_preview_sha256:
                conn.rollback()
                return HTMLResponse(
                    _mapping_confirmation_html(
                        candidate_id=candidate_id,
                        kind="mapping disposition",
                        values={
                            "action": action,
                            "reason": reason,
                            "idempotency_key": str(idempotency_key),
                            "existing_offer_id": "" if existing_offer_id is None else str(existing_offer_id),
                            "offer_link_kind": normalized_link_kind or "",
                        },
                        preview_sha256=preview["preview_sha256"],
                    ),
                    headers={"Cache-Control": "no-store"},
                )
            if confirm != "CONFIRM":
                raise PersistentMappingError("exact mapping confirmation is required")
        execute_mapping_decision(
            _database_url(),
            candidate_id=candidate_id,
            action=action,
            reason=reason,
            principal=principal,
            decision_idempotency_key=idempotency_key,
            expected_preview_sha256=expected_preview_sha256,
            existing_offer_id=existing_offer_id,
            offer_link_kind=(offer_link_kind or "").strip().upper() or None,
        )
    except PersistentMappingError as exc:
        raise HTTPException(status_code=_mapping_error_status(exc), detail=str(exc)) from exc
    return RedirectResponse(url=f"/supplier-mapping/{candidate_id}", status_code=303)


@app.post("/supplier-mapping/{candidate_id}/selection", response_class=HTMLResponse)
def supplier_mapping_selection(
    request: Request,
    candidate_id: UUID,
    reason: str = Form(...),
    effective_from: date = Form(...),
    idempotency_key: UUID = Form(...),
    selection_action: str = Form("SELECT"),
    expected_preview_sha256: str | None = Form(None),
    confirm: str | None = Form(None),
):
    principal = action_principal(request, "procurement.offer.select")
    try:
        require_synthetic_mapping_capability("routine_selection_writes_enabled")
        with _db_conn() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            detail = get_mapping_candidate_detail(conn, candidate_id)
            normalized_action = selection_action.strip().upper()
            effective = detail["effective_decision"]
            mapping_decision_id: UUID | None = None
            if normalized_action == "SELECT":
                if not effective or effective["action"] != "APPROVE_MAPPING":
                    raise PersistentMappingError("candidate has no effective approved mapping")
                mapping_decision_id = UUID(str(effective["mapping_decision_id"]))
                preview = preview_routine_offer_selection(
                    conn,
                    mapping_decision_id=mapping_decision_id,
                    principal=principal,
                    selection_idempotency_key=idempotency_key,
                    reason=reason,
                    effective_from=effective_from,
                )
            elif normalized_action == "CLEAR":
                variant_id = str(detail["candidate"].get("proposed_variant_id") or "")
                preview = preview_routine_offer_clear(
                    conn,
                    variant_id=variant_id,
                    principal=principal,
                    selection_idempotency_key=idempotency_key,
                    reason=reason,
                    effective_from=effective_from,
                )
            else:
                raise PersistentMappingError("routine selection action is unsupported")
            if not expected_preview_sha256:
                conn.rollback()
                return HTMLResponse(
                    _mapping_confirmation_html(
                        candidate_id=candidate_id,
                        kind="routine offer selection",
                        values={
                            "reason": reason,
                            "selection_action": normalized_action,
                            "effective_from": effective_from.isoformat(),
                            "idempotency_key": str(idempotency_key),
                            "mapping_decision_id": "" if mapping_decision_id is None else str(mapping_decision_id),
                        },
                        preview_sha256=preview["preview_sha256"],
                    ),
                    headers={"Cache-Control": "no-store"},
                )
            if confirm != "CONFIRM":
                raise PersistentMappingError("exact selection confirmation is required")
        if normalized_action == "SELECT":
            assert mapping_decision_id is not None
            execute_routine_offer_selection(
                _database_url(),
                mapping_decision_id=mapping_decision_id,
                principal=principal,
                selection_idempotency_key=idempotency_key,
                reason=reason,
                effective_from=effective_from,
                expected_preview_sha256=expected_preview_sha256,
            )
        else:
            execute_routine_offer_clear(
                _database_url(),
                variant_id=variant_id,
                principal=principal,
                selection_idempotency_key=idempotency_key,
                reason=reason,
                effective_from=effective_from,
                expected_preview_sha256=expected_preview_sha256,
            )
    except PersistentMappingError as exc:
        raise HTTPException(status_code=_mapping_error_status(exc), detail=str(exc)) from exc
    return RedirectResponse(url=f"/supplier-mapping/{candidate_id}", status_code=303)


@app.post("/economics/target-cost")
def target_cost_endpoint(req: TargetCostRequest):
    return {"target_cost": str(target_cost(req.retail_price, req.target_margin_pct))}


@app.post("/economics/qualifying-quantity")
def qualifying_endpoint(req: QualifyingRequest):
    try:
        q = qualifying_quantity(req.cases, req.break_unit, req.qualifying_units_per_case)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"qualifying_quantity": str(q)}


@app.post("/matching/score")
def matching_endpoint(req: MatchRequest):
    r = load_rules()
    result = score_candidate(
        MatchCandidate(**req.model_dump()),
        float(r["matching"]["auto_match_min_score"]),
        float(r["matching"]["review_min_score"]),
    )
    return {"score": result.score, "auto_match": result.auto_match, "review": result.review, "blocked": result.blocked, "reasons": result.reasons}


@app.get("/reconciliation/items")
def reconciliation_items(unresolved_only: bool = True):
    """Reconciliation queue for the one authoritative catalog attempt."""
    with _db_conn() as conn, conn.cursor() as cur:
        evaluation = catalog_service.evaluate_authoritative_catalog_run(conn)
        run = evaluation["run"]
        if not run:
            return {"run": None, "items": []}
        q = """SELECT reconciliation_item_id,classification,seed_variant_id,live_variant_id,
                      blocking,evidence_json,resolution,resolved_by,resolved_at
               FROM catalog_reconciliation_items WHERE catalog_sync_id=%s"""
        if unresolved_only:
            q += " AND blocking=TRUE AND resolved_at IS NULL"
        q += " ORDER BY classification,reconciliation_item_id"
        cur.execute(q, (run["catalog_sync_id"],))
        items = [
            {"item_id": r[0], "classification": r[1], "seed_variant_id": r[2],
             "live_variant_id": r[3], "blocking": r[4], "evidence": r[5],
             "resolution": r[6], "resolved_by": r[7],
             "resolved_at": str(r[8]) if r[8] else None}
            for r in cur.fetchall()
        ]
        # Old-side (historical) evidence for side-by-side display.
        seed_ids = [i["seed_variant_id"] for i in items if i["seed_variant_id"]]
        old_rows = {}
        if seed_ids:
            cur.execute("""SELECT variant_id,product_title,variant_title,sku,barcode,retail_price,
                                  handle,shopify_vendor,variant_created_at,catalog_state
                           FROM variants WHERE variant_id = ANY(%s)""", (seed_ids,))
            for r in cur.fetchall():
                old_rows[str(r[0])] = {"variant_id": str(r[0]), "product_title": r[1], "variant_title": r[2],
                                       "sku": r[3], "barcode": r[4],
                                       "price": str(r[5]) if r[5] is not None else None,
                                       "handle": r[6], "vendor": r[7],
                                       "created_at": str(r[8]) if r[8] else None, "catalog_state": r[9]}
        for i in items:
            i["historical_record"] = old_rows.get(i["seed_variant_id"])
    return {
        "run": {
            "catalog_sync_id": run["catalog_sync_id"],
            "started_at": str(run["started_at"]),
            "completed_at": str(run["completed_at"]) if run["completed_at"] else None,
            "status": run["status"],
            "shopify_api_version": run["shopify_api_version"],
            "shopify_reported_variant_count": run["shopify_reported_variant_count"],
            "live_rows_received": run["live_rows_received"],
            "exact_current_ids": run["exact_current_ids"],
            "new_live_variants": run["new_live_variants"],
            "missing_seed_variants": run["missing_seed_variants"],
            "potential_recreations": run["potential_recreations"],
            "unresolved_count": evaluation["unresolved_blockers"],
            "recorded_unresolved_count": run["recorded_unresolved_count"],
            "source_hash": run["source_hash"],
            "pagination_complete": run["pagination_complete"],
            "readiness_status": evaluation["status"],
            "readiness_blockers": list(evaluation["blockers"]),
        },
        "items": items,
    }


@app.post("/reconciliation/approve-recreation")
def approve_recreation_endpoint(req: RecreationDecision):
    _require_review_token(req.review_token)
    actor = _server_audit_actor(req.actor)
    with _db_conn() as conn:
        try:
            approve_recreated_variant(conn, req.old_variant_id, req.new_variant_id, actor=actor, note=req.note)
            gate = recompute_catalog_gate(conn)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"decision": "APPROVED_RECREATION", "gate": gate}


@app.post("/reconciliation/reject-recreation")
def reject_recreation_endpoint(req: RecreationDecision):
    _require_review_token(req.review_token)
    actor = _server_audit_actor(req.actor)
    with _db_conn() as conn:
        try:
            reject_recreation_candidate(conn, req.old_variant_id, req.new_variant_id, actor=actor, note=req.note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"decision": "REJECTED_KEEP_SEPARATE"}


@app.post("/reconciliation/retire")
def retire_endpoint(req: RetireDecision):
    _require_review_token(req.review_token)
    actor = _server_audit_actor(req.actor)
    with _db_conn() as conn:
        try:
            retire_missing_variant(conn, req.variant_id, actor=actor, note=req.note)
            gate = recompute_catalog_gate(conn)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"decision": "CONFIRMED_RETIRED", "gate": gate}


@app.post("/reconciliation/recompute-gate")
def recompute_gate_endpoint():
    with _db_conn() as conn:
        return recompute_catalog_gate(conn)


@app.get("/reconciliation", response_class=HTMLResponse)
def reconciliation_page():
    """Catalog Reconciliation review UI (read + explicit human decisions only)."""
    data = reconciliation_items(unresolved_only=True)
    run = data["run"]
    if not run:
        navigation = _operational_nav("", current="Catalog Reconciliation")
        return (
            "<!doctype html><html><head><title>Catalog Reconciliation</title>"
            "</head><body style='font-family:system-ui,sans-serif;max-width:980px;"
            f"margin:2rem auto;padding:0 1rem;color:#1f2328'>{navigation}"
            "<h1>Catalog Reconciliation</h1>"
            "<p>No catalog sync attempt exists yet.</p></body></html>"
        )
    items = data["items"]

    def esc(v):
        import html
        return html.escape(str(v)) if v is not None else "—"

    def side_by_side(i):
        old = i.get("historical_record") or {}
        ev = i.get("evidence") or {}
        cands = ev.get("candidates") or []
        rows = ""
        for c in cands:
            rows += f"""<table class='cand'><tr><th></th><th>Historical (old)</th><th>Live Shopify (new)</th></tr>
<tr><td>Variant ID</td><td>{esc(old.get('variant_id'))}</td><td>{esc(c.get('new_variant_id'))}</td></tr>
<tr><td>SKU</td><td>{esc(old.get('sku'))}</td><td>{esc(c.get('new_sku'))}</td></tr>
<tr><td>Barcode</td><td>{esc(old.get('barcode'))}</td><td>{esc(c.get('new_barcode'))}</td></tr>
<tr><td>Product</td><td>{esc(old.get('product_title'))}</td><td>{esc(c.get('new_product_title'))}</td></tr>
<tr><td>Variant/size</td><td>{esc(old.get('variant_title'))}</td><td>{esc(c.get('new_variant_title'))}</td></tr>
<tr><td>Handle</td><td>{esc(old.get('handle'))}</td><td>{esc(c.get('new_handle'))}</td></tr>
<tr><td>Vendor</td><td>{esc(old.get('vendor'))}</td><td>{esc(c.get('new_vendor'))}</td></tr>
<tr><td>Price</td><td>{esc(old.get('price'))}</td><td>{esc(c.get('new_price'))}</td></tr>
<tr><td>Inventory item</td><td>—</td><td>{esc(c.get('new_inventory_item_gid'))}</td></tr>
<tr><td>Created</td><td>{esc(old.get('created_at'))}</td><td>{esc(c.get('new_created_at'))}</td></tr>
<tr><td>Matching evidence</td><td colspan=2>{esc(', '.join(c.get('matching_evidence') or []) or 'none')}</td></tr>
<tr><td>Conflicting evidence</td><td colspan=2 class='warn'>{esc(', '.join(c.get('conflicting_evidence') or []) or 'none')}</td></tr>
<tr><td>Evidence class</td><td colspan=2><b>{esc(c.get('confidence'))}</b></td></tr></table>
<div class='actions'>
<form method='post' action='decide'><input type=hidden name=action value=approve>
<input type=hidden name=old value='{esc(old.get("variant_id"))}'><input type=hidden name=new value='{esc(c.get("new_variant_id"))}'>
<input name=actor placeholder='your name' required><input name=note placeholder='note'>
<input name=review_token type=password placeholder='review token' required>
<button class='ok'>APPROVE RECREATION</button></form>
<form method='post' action='decide'><input type=hidden name=action value=reject>
<input type=hidden name=old value='{esc(old.get("variant_id"))}'><input type=hidden name=new value='{esc(c.get("new_variant_id"))}'>
<input name=actor placeholder='your name' required><input name=note placeholder='why separate' required>
<input name=review_token type=password placeholder='review token' required>
<button class='bad'>REJECT / KEEP SEPARATE</button></form>
</div>"""
        rows += f"""<div class='actions'>
<form method='post' action='decide'><input type=hidden name=action value=retire>
<input type=hidden name=old value='{esc(old.get("variant_id"))}'>
<input name=actor placeholder='your name' required><input name=note placeholder='retirement note' required>
<input name=review_token type=password placeholder='review token' required>
<button class='mid'>MARK HISTORICAL IDENTITY RETIRED</button></form>
<span class='muted'>…or leave unresolved (remains a blocker).</span></div>"""
        return rows

    sections = ""
    by_class: dict[str, list] = {}
    for i in items:
        by_class.setdefault(i["classification"], []).append(i)
    for cls in ("AMBIGUOUS_IDENTITY", "POTENTIAL_RECREATION", "MISSING"):
        group = by_class.get(cls, [])
        if not group:
            continue
        sections += f"<h2>{cls} ({len(group)})</h2>"
        for i in group:
            old = i.get("historical_record") or {}
            sections += f"<div class='item'><h3>{esc(old.get('product_title'))} — {esc(old.get('variant_title'))} <small>(old ID {esc(i['seed_variant_id'])})</small></h3>{side_by_side(i)}</div>"

    return f"""<!doctype html><html><head><title>Catalog Reconciliation</title>
<style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
table.cand{{border-collapse:collapse;width:100%;margin:8px 0}}td,th{{border:1px solid #d1d9e0;padding:5px 10px;font-size:13px;text-align:left}}
th{{background:#f6f8fa}}.warn{{color:#82071e}}.item{{border:1px solid #d1d9e0;border-radius:8px;padding:12px 16px;margin:16px 0}}
.actions{{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}}form{{display:flex;gap:6px}}input{{padding:4px 6px;font-size:13px}}
button{{padding:5px 10px;border-radius:5px;border:1px solid;cursor:pointer;font-size:12px;font-weight:600}}
.ok{{background:#dafbe1;color:#116329}}.bad{{background:#ffebe9;color:#82071e}}.mid{{background:#fff8c5;color:#7d4e00}}.muted{{color:#59636e;font-size:12px;align-self:center}}
</style></head><body>
{_operational_nav('', current='Catalog Reconciliation')}
<h1>Catalog Reconciliation — human review queue</h1>
<p>Authoritative run {esc(run['catalog_sync_id'])} · run status {esc(run['status'])}
· readiness {esc(run['readiness_status'])} · API {esc(run['shopify_api_version'])}
· live variants {esc(run['live_rows_received'])}
(reported {esc(run['shopify_reported_variant_count'])}) · pagination complete: {esc(run['pagination_complete'])}
· snapshot {esc((run['source_hash'] or '')[:16])}…</p>
<p>Readiness blockers: {esc(', '.join(run['readiness_blockers']) or 'none')}</p>
<p><b>{len(items)}</b> unresolved blocker(s). Identity decisions are permanent and audited. Nothing here writes to Shopify.</p>
<p class='muted'><a href='reconciliation/investigation'>Catalog Identity Investigation</a> provides subordinate diagnostic evidence.</p>
{sections or '<p>No unresolved blockers.</p>'}
</body></html>"""


@app.get("/reconciliation/investigation/items")
def investigation_items():
    """Persisted evidence for the authoritative catalog attempt (diagnostic only)."""
    with _db_conn() as conn:
        evaluation = catalog_service.evaluate_authoritative_catalog_run(conn)
        run = evaluation["run"]
        if not run:
            return {"run": None, "catalog_readiness": evaluation, "missing": [], "new": []}
        sync_id = run["catalog_sync_id"]
        with conn.cursor() as cur:
            cur.execute(
                """SELECT subject,variant_id,shopify_status,classification,evidence_json,heightened_review,looked_up_at
                   FROM identity_investigations WHERE catalog_sync_id=%s ORDER BY subject,variant_id""", (sync_id,))
            missing, new = [], []
            for subj, vid, status, cls, ev, hr, at in cur.fetchall():
                rec = {"variant_id": vid, "shopify_status": status, "classification": cls,
                       "evidence": ev, "heightened_review": hr, "looked_up_at": str(at)}
                (missing if subj == "MISSING_SEED" else new).append(rec)
    return {
        "run": sync_id,
        "catalog_readiness": {
            "status": evaluation["status"],
            "catalog_sync_id": evaluation["catalog_sync_id"],
            "blockers": list(evaluation["blockers"]),
        },
        "missing": missing,
        "new": new,
    }


@app.get("/reconciliation/investigation", response_class=HTMLResponse)
def investigation_page():
    """Identity Investigation report — groups the blockers with direct Shopify evidence.
    Diagnostic only: no buttons here imply approval by confidence; all decisions
    happen on /reconciliation and individually require the review token."""
    data = investigation_items()
    if not data["run"]:
        return (
            "<!doctype html><html><head><title>Identity Investigation</title></head><body>"
            + _operational_nav("/", current="Catalog Reconciliation")
            + "<h1>Identity Investigation</h1><p>No completed catalog sync run yet.</p>"
            "</body></html>"
        )

    def esc(v):
        import html
        return html.escape(str(v)) if v is not None else "—"

    def row_html(rec, seed_key="seed", counterparts=None):
        ev = rec.get("evidence") or {}
        base = ev.get(seed_key) or ev.get("new") or {}
        analysis = ev.get("recreation_analysis") or ev.get("analysis") or {}
        cands = analysis.get("candidates") or analysis.get("predecessors") or []
        cand_html = ""
        for c in cands:
            cid = c.get("new_variant_id") if seed_key == "seed" else c.get("seed_variant_id")
            other = (counterparts or {}).get(str(cid)) or {}
            cand_html += (f"<div class='cand'><b>Proposed counterpart {esc(cid)}</b>: "
                          f"{esc(other.get('product_title'))} — {esc(other.get('variant_title'))} · "
                          f"SKU {esc(other.get('sku'))} · barcode {esc(other.get('barcode'))}"
                          f"<br>matching: {esc('; '.join(c.get('matched') or c.get('supporting') or []) or 'none')}"
                          f"<br><span class='warn'>conflicting: {esc('; '.join(c.get('conflicting') or []) or 'none')}</span>"
                          f"<br>cautions: {esc('; '.join(c.get('cautions') or []) or 'none')}</div>")
        flag = " <span class='flag'>HEIGHTENED REVIEW</span>" if rec.get("heightened_review") else ""
        action = analysis.get("recommended_action") or ("—" if not rec["classification"].startswith("DELETED/") else "")
        return (f"<tr><td>{esc(rec['variant_id'])}{flag}</td><td>{esc(base.get('product_title'))} — {esc(base.get('variant_title'))}</td>"
                f"<td>{esc(base.get('sku'))}</td><td>{esc(base.get('barcode'))}</td><td>{esc(rec['shopify_status'])}</td>"
                f"<td>{esc(rec['classification'])}<br><small>{esc(analysis.get('reason') or ev.get('existence') or '')}</small>"
                f"{cand_html}<br><small><b>Recommended:</b> {esc(action)}</small></td></tr>")

    # counterpart detail lookup for side-by-side display
    counterparts = {}
    for r in data["new"]:
        base = (r.get("evidence") or {}).get("new") or {}
        counterparts[str(r["variant_id"])] = base
    for r in data["missing"]:
        base = (r.get("evidence") or {}).get("seed") or {}
        counterparts[str(r["variant_id"])] = base

    m = data["missing"]
    groups = {
        "GROUP A — Still exist in Shopify but non-active (not recreation candidates)":
            [r for r in m if r["classification"].startswith("STILL_EXISTS") and r["classification"] != "STILL_EXISTS_ACTIVE"],
        "DEFECT — Marked missing but Shopify says ACTIVE (enumeration bug, investigate first)":
            [r for r in m if r["classification"] == "STILL_EXISTS_ACTIVE"],
        "1 — HIGH-EVIDENCE RECREATION REVIEW (deterministic identity evidence; human approval required)":
            [r for r in m if r["classification"] == "DELETED/HIGH_EVIDENCE_RECREATION_REVIEW"],
        "2 — POSSIBLE RECREATION REVIEW (meaningful evidence, insufficient certainty)":
            [r for r in m if r["classification"] == "DELETED/POSSIBLE_RECREATION_REVIEW"],
        "3 — CONFLICT / AMBIGUOUS (must stay unresolved)":
            [r for r in m if r["classification"] in ("DELETED/CONFLICT_AMBIGUOUS", "DELETED/AMBIGUOUS",
                                                     "DELETED/POSSIBLE_RECREATION_CANDIDATE")],
        "4 — NO CREDIBLE CURRENT COUNTERPART (potential retirement; explicit human approval required)":
            [r for r in m if r["classification"] in ("DELETED/NO_CREDIBLE_CURRENT_COUNTERPART",
                                                     "DELETED/NO_RECREATION_CANDIDATE")],
    }
    sections = ""
    for title, rows in groups.items():
        if not rows:
            continue
        sections += (f"<h2>{esc(title)} ({len(rows)})</h2><table><tr><th>Old Variant ID</th><th>Product / size</th>"
                     f"<th>SKU</th><th>Barcode</th><th>Shopify status</th><th>Classification & evidence</th></tr>"
                     + "".join(row_html(r, counterparts=counterparts) for r in rows) + "</table>")
    no_counterpart = [r for r in m if r["classification"] in ("DELETED/NO_CREDIBLE_CURRENT_COUNTERPART",
                                                              "DELETED/NO_RECREATION_CANDIDATE")]
    if no_counterpart:
        checkboxes = ""
        for r in no_counterpart:
            base = (r.get("evidence") or {}).get("seed") or {}
            checkboxes += (f"<label class='pick'><input type=checkbox name=variant_ids value='{esc(r['variant_id'])}'> "
                           f"{esc(r['variant_id'])} — {esc(base.get('product_title'))} ({esc(base.get('variant_title'))})</label>")
        sections += f"""<h2>Batch retirement authorization</h2>
<p>Select the historical identities you have reviewed and wish to mark RETIRED. Nothing is pre-selected;
each selected identity receives its own permanent audit record. This never runs automatically.</p>
<form method='post' action='investigation/retire-batch'>{checkboxes}
<div class='actions'><input name=actor placeholder='your name' required>
<input name=note placeholder='retirement note (required)' required>
<input name=review_token type=password placeholder='review token' required>
<button class='mid'>RETIRE SELECTED HISTORICAL IDENTITIES</button></div></form>"""
    sections += (f"<h2>Reverse view — {len(data['new'])} NEW live variants</h2><table><tr><th>New Variant ID</th>"
                 f"<th>Product / size</th><th>SKU</th><th>Barcode</th><th>Status</th><th>Classification & evidence</th></tr>"
                 + "".join(row_html(r, seed_key="new", counterparts=counterparts) for r in data["new"]) + "</table>")
    return f"""<!doctype html><html><head><title>Identity Investigation</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
table{{border-collapse:collapse;width:100%;margin:8px 0}}td,th{{border:1px solid #d1d9e0;padding:5px 8px;font-size:13px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa}}.warn{{color:#82071e}}.flag{{background:#fff8c5;color:#7d4e00;font-size:11px;padding:1px 5px;border-radius:4px;font-weight:700}}
.cand{{border-left:3px solid #d1d9e0;margin:4px 0;padding:3px 8px;font-size:12px;background:#f6f8fa}}</style></head><body>
{_operational_nav('/', current='Catalog Reconciliation')}
<h1>Phase 3 Identity Investigation — run {esc(data['run'])}</h1>
<p>Diagnostic evidence only. Nothing on this page makes decisions or writes to Shopify.
Decisions are made individually on <a href='../reconciliation'>the review queue</a> and require the review token.</p>
{sections}
</body></html>"""


@app.post("/reconciliation/investigation/retire-batch", response_class=HTMLResponse)
def retire_batch(variant_ids: list[str] = Form([]), actor: str = Form(...),
                 note: str = Form(...), review_token: str = Form("")):
    """Batch authorization of individually-audited retirements. One token-authorized
    session may cover several explicitly selected identities; each ID still gets its
    own permanent audit record via retire_missing_variant. Never retires unselected IDs."""
    _require_review_token(review_token)
    actor = _server_audit_actor(actor)
    if not variant_ids:
        raise HTTPException(status_code=400, detail="No historical Variant IDs selected")
    results = []
    with _db_conn() as conn:
        for vid in variant_ids:
            try:
                retire_missing_variant(conn, vid, actor=actor, note=note)
                results.append((vid, "RETIRED"))
            except ValueError as exc:
                results.append((vid, f"SKIPPED: {exc}"))
        recompute_catalog_gate(conn)
    import html as _html
    rows = "".join(f"<li>{_html.escape(v)} — {_html.escape(r)}</li>" for v, r in results)
    return (_operational_nav("/", current="Catalog Reconciliation")
            + f"<h1>Batch retirement result</h1><ul>{rows}</ul>"
            "<p><a href='../../reconciliation/investigation'>Back to investigation</a> · "
            "<a href='../../reconciliation'>Review queue</a></p>")


@app.post("/reconciliation/decide", response_class=HTMLResponse)
def reconciliation_decide(action: str = Form(...), old: str = Form(...), new: str = Form(None),
                          actor: str = Form(...), note: str = Form(""), review_token: str = Form("")):
    _require_review_token(review_token)
    actor = _server_audit_actor(actor)
    with _db_conn() as conn:
        try:
            if action == "approve":
                approve_recreated_variant(conn, old, new, actor=actor, note=note)
                recompute_catalog_gate(conn)
            elif action == "reject":
                reject_recreation_candidate(conn, old, new, actor=actor, note=note)
            elif action == "retire":
                retire_missing_variant(conn, old, actor=actor, note=note)
                recompute_catalog_gate(conn)
            else:
                raise HTTPException(status_code=400, detail="Unknown action")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return '<meta http-equiv="refresh" content="0;url=../reconciliation"><p>Recorded. Returning to queue…</p>'


@app.get("/historical-sales/review/items")
def historical_sales_review_items():
    """Aggregated historical source identities requiring an owner decision.

    This endpoint is read-only. It deliberately returns grouped source identities,
    not individual daily facts, so a reviewer can understand the full material
    effect before recording a decision.
    """
    with _db_conn() as conn:
        items = sales_service.get_historical_sales_review_items(conn)
    return {"count": len(items), "items": items}


@app.get("/historical-sales/review/catalog-search")
def historical_sales_catalog_search(q: str = ""):
    """Return bounded local catalog evidence; never records an identity decision."""
    normalized_query = str(q).strip()
    if not normalized_query:
        return {"query": "", "count": 0, "items": []}
    if len(normalized_query) > sales_service.HISTORICAL_SALES_CATALOG_SEARCH_MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="Local catalog search query must be 128 characters or fewer",
        )
    try:
        with _db_conn() as conn:
            items = sales_service.search_historical_sales_catalog(
                conn, normalized_query
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Local catalog search is temporarily unavailable",
        ) from None
    return {"query": normalized_query, "count": len(items), "items": items}


_HISTORICAL_SALES_CATALOG_PICKER_SCRIPT = """
document.addEventListener("click", async function (event) {
  if (!(event.target instanceof Element)) return;

  const selectButton = event.target.closest(".catalog-select-button");
  if (selectButton) {
    const card = selectButton.closest(".historical-sales-review-card");
    const target = card && card.querySelector(
      "form[data-historical-sales-map] input[name='canonical_variant_id']"
    );
    if (target) {
      target.value = selectButton.dataset.variantId;
      target.focus();
    }
    return;
  }

  const searchButton = event.target.closest(".catalog-search-button");
  if (!searchButton) return;
  const picker = searchButton.closest(".catalog-picker");
  const input = picker.querySelector(".catalog-search-input");
  const status = picker.querySelector(".catalog-search-status");
  const results = picker.querySelector(".catalog-search-results");
  const query = input.value.trim();
  results.replaceChildren();
  if (!query) {
    status.textContent = "Enter a local catalog search term.";
    return;
  }

  status.textContent = "Searching local catalog…";
  searchButton.disabled = true;
  try {
    const response = await fetch(
      "review/catalog-search?q=" + encodeURIComponent(query),
      {headers: {"Accept": "application/json"}, credentials: "same-origin"}
    );
    if (!response.ok) throw new Error("catalog search failed");
    const payload = await response.json();
    if (!Array.isArray(payload.items) || payload.items.length === 0) {
      status.textContent = "No local catalog results found.";
      return;
    }

    status.textContent = payload.items.length + " local catalog result(s).";
    for (const item of payload.items) {
      const result = document.createElement("div");
      result.className = "catalog-result";

      const heading = document.createElement("b");
      heading.textContent = "Local catalog result — Variant ID " + item.variant_id;
      result.appendChild(heading);

      const identity = document.createElement("div");
      identity.textContent = (item.product_title || "—") + " — " +
        (item.variant_title || "—");
      result.appendChild(identity);

      const evidence = document.createElement("div");
      evidence.className = "catalog-result-evidence";
      evidence.textContent = "SKU " + (item.sku || "—") + " · Barcode " +
        (item.barcode || "—") + " · Active " + (item.active ? "yes" : "no") +
        " · Catalog state " + (item.catalog_state || "—");
      result.appendChild(evidence);

      const button = document.createElement("button");
      button.type = "button";
      button.className = "catalog-select-button";
      button.dataset.variantId = String(item.variant_id);
      button.textContent = "Select Variant ID";
      result.appendChild(button);
      results.appendChild(result);
    }
  } catch (error) {
    status.textContent = "Local catalog search is temporarily unavailable.";
  } finally {
    searchButton.disabled = false;
  }
});

document.addEventListener("keydown", function (event) {
  if (!(event.target instanceof Element)) return;
  if (event.key !== "Enter" || !event.target.matches(".catalog-search-input")) return;
  event.preventDefault();
  const picker = event.target.closest(".catalog-picker");
  picker.querySelector(".catalog-search-button").click();
});
"""


@app.get("/assets/historical-sales-catalog-picker.js")
def historical_sales_catalog_picker_script():
    return Response(
        _HISTORICAL_SALES_CATALOG_PICKER_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


def _historical_sales_review_html(items: list[dict]) -> str:
    """Render the deliberately small, server-side historical-sales review UI."""
    import html
    import json

    def esc(value) -> str:
        return html.escape(str(value), quote=True) if value is not None and value != "" else "—"

    def field(item: dict, *names: str, default=None):
        for name in names:
            if name in item and item[name] is not None:
                return item[name]
        return default

    def evidence(value) -> str:
        if value in (None, "", [], {}):
            return "none"
        if isinstance(value, str):
            rendered = value
        else:
            rendered = json.dumps(value, sort_keys=True, default=str, indent=2)
        return html.escape(rendered, quote=True)

    def candidate_id(candidate) -> str | None:
        if not isinstance(candidate, dict):
            return str(candidate) if candidate not in (None, "") else None
        value = field(candidate, "canonical_variant_id", "variant_id", "candidate_variant_id")
        return str(value) if value not in (None, "") else None

    def candidate_html(candidate) -> str:
        if not isinstance(candidate, dict):
            return f"<li><b>Canonical Variant ID {esc(candidate)}</b></li>"
        cid = candidate_id(candidate)
        product = field(candidate, "product_title", "candidate_product_title")
        variant = field(candidate, "variant_title", "candidate_variant_title")
        sku = field(candidate, "sku", "candidate_sku")
        support = field(candidate, "evidence", "supporting_evidence", "matching_evidence")
        conflicts = field(candidate, "conflicts", "conflicting_evidence")
        return f"""<li class='candidate'>
<b>Canonical Variant ID {esc(cid)}</b> · {esc(product)} — {esc(variant)} · SKU {esc(sku)}
<div><span class='label'>Evidence:</span> <pre>{evidence(support)}</pre></div>
<div class='conflict'><span class='label'>Conflicts:</span> <pre>{evidence(conflicts)}</pre></div>
</li>"""

    cards = ""
    for index, item in enumerate(items):
        source_key = field(item, "source_key", "source_identity_key")
        source_variant_id = field(item, "source_variant_id", "historical_variant_id")
        sku = field(item, "historical_sku", "source_sku")
        product_title = field(item, "historical_product_title", "source_product_title", "product_title")
        variant_title = field(item, "historical_variant_title", "source_variant_title", "variant_title")
        first_sale = field(item, "first_sale_date", "first_day")
        last_sale = field(item, "last_sale_date", "last_day")
        raw_rows = field(item, "affected_raw_rows", "raw_row_count", "row_count", default=0)
        net_units = field(item, "net_units", "net_items_sold", default=0)
        absolute_units = field(item, "absolute_unit_magnitude", "absolute_units", "abs_net_units", default=0)
        net_sales = field(item, "net_sales", default=0)
        status = field(item, "resolution_status", "status", default="UNRESOLVED")
        materiality = field(item, "materiality", "materiality_label", "material", default="REVIEW")
        candidates = field(item, "candidate_canonical_variants", "candidates", default=[]) or []
        supporting = field(item, "evidence", "supporting_evidence")
        conflicts = field(item, "conflicts", "conflicting_evidence")

        options = []
        for candidate in candidates:
            cid = candidate_id(candidate)
            if cid and cid not in options:
                options.append(cid)
        datalist_id = f"historical-sales-candidates-{index}"
        datalist = "".join(f"<option value='{esc(cid)}'></option>" for cid in options)
        candidates_view = (
            "<ul class='candidates'>" + "".join(candidate_html(c) for c in candidates) + "</ul>"
            if candidates else "<p class='muted'>No deterministic canonical candidate is available.</p>"
        )

        cards += f"""<section class='item historical-sales-review-card'>
<header><div><h2>{esc(product_title)} — {esc(variant_title)}</h2>
<p class='identity'>Source Variant ID {esc(source_variant_id)} · historical SKU {esc(sku)}</p></div>
<div><span class='status'>{esc(status)}</span><span class='materiality'>{esc(materiality)}</span></div></header>
<table><tr><th>First sale</th><th>Last sale</th><th>Raw rows</th><th>Net units</th><th>Absolute units</th><th>Net sales</th></tr>
<tr><td>{esc(first_sale)}</td><td>{esc(last_sale)}</td><td>{esc(raw_rows)}</td><td>{esc(net_units)}</td><td>{esc(absolute_units)}</td><td>{esc(net_sales)}</td></tr></table>
<div class='evidence-grid'><div><h3>Source evidence</h3><pre>{evidence(supporting)}</pre></div>
<div class='conflict'><h3>Conflicts</h3><pre>{evidence(conflicts)}</pre></div></div>
<h3>Candidate canonical variants</h3>{candidates_view}
<p class='muted'>Candidates are evidence only. No mapping is pre-approved or pre-selected.</p>
<div class='catalog-picker'>
<h3>Search local canonical catalog</h3>
<p class='muted'>Search results are evidence only. Selecting a result only fills the existing Canonical Variant ID field.</p>
<div class='catalog-search-controls'>
<label>Product, title, SKU, barcode, or Variant ID
<input class='catalog-search-input' type='search' maxlength='128' autocomplete='off'></label>
<button type='button' class='catalog-search-button'>SEARCH LOCAL CATALOG</button>
</div>
<p class='catalog-search-status muted' role='status' aria-live='polite'></p>
<div class='catalog-search-results'></div>
</div>
<div class='actions'>
<form method='post' action='review/decide' data-historical-sales-map>
<input type='hidden' name='source_key' value='{esc(source_key)}'><input type='hidden' name='action' value='MAP_TO_CANONICAL'>
<label>Canonical Variant ID <input class='canonical-variant-id' name='canonical_variant_id' list='{datalist_id}' placeholder='enter exact Variant ID' required></label>
<datalist id='{datalist_id}'>{datalist}</datalist>
<label>Reviewer <input name='actor' autocomplete='name' required></label>
<label>Reason <input name='reason' required></label>
<label>Review token <input name='review_token' type='password' autocomplete='current-password' required></label>
<button class='map'>MAP TO CANONICAL</button></form>
<form method='post' action='review/decide'>
<input type='hidden' name='source_key' value='{esc(source_key)}'><input type='hidden' name='action' value='EXCLUDE_HISTORICAL_ITEM'>
<label>Reviewer <input name='actor' autocomplete='name' required></label>
<label>Exclusion reason <input name='reason' required></label>
<label>Review token <input name='review_token' type='password' autocomplete='current-password' required></label>
<button class='exclude'>EXCLUDE HISTORICAL ITEM</button></form>
<form method='post' action='review/decide'>
<input type='hidden' name='source_key' value='{esc(source_key)}'><input type='hidden' name='action' value='LEAVE_UNRESOLVED'>
<label>Reviewer <input name='actor' autocomplete='name' required></label>
<label>Reason <input name='reason' required></label>
<label>Review token <input name='review_token' type='password' autocomplete='current-password' required></label>
<button class='leave'>LEAVE UNRESOLVED</button></form>
</div></section>"""

    return f"""<!doctype html><html><head><title>Historical Sales Reconciliation</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1180px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
h1{{font-size:24px}}h2{{font-size:18px;margin:0}}h3{{font-size:14px;margin:10px 0 5px}}.item{{border:1px solid #d1d9e0;border-radius:8px;padding:16px;margin:18px 0}}
header{{display:flex;justify-content:space-between;gap:16px;align-items:start}}.identity,.muted{{color:#59636e;font-size:13px}}.status,.materiality{{display:inline-block;padding:3px 7px;border-radius:4px;font-size:11px;font-weight:700;margin-left:5px}}
.status{{color:#82071e;background:#ffebe9}}.materiality{{color:#7d4e00;background:#fff8c5}}table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{border:1px solid #d1d9e0;padding:6px 9px;text-align:left;font-size:13px}}th{{background:#f6f8fa}}
.evidence-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}pre{{white-space:pre-wrap;word-break:break-word;margin:3px 0;font:12px ui-monospace,monospace}}.conflict{{color:#82071e}}.candidates{{padding-left:22px}}.candidate{{margin:10px 0}}.label{{font-weight:600}}
.catalog-picker{{border:1px solid #afb8c1;border-radius:6px;background:#f6f8fa;padding:10px;margin-top:12px}}.catalog-search-controls{{display:flex;align-items:end;gap:8px;flex-wrap:wrap}}.catalog-search-results{{display:grid;gap:7px}}.catalog-result{{border-left:3px solid #afb8c1;background:#fff;padding:8px;font-size:12px}}.catalog-result-evidence{{color:#59636e;margin:4px 0}}
.actions{{display:grid;gap:10px;margin-top:14px}}form{{border-top:1px solid #d1d9e0;padding-top:10px;display:flex;gap:8px;align-items:end;flex-wrap:wrap}}label{{display:flex;flex-direction:column;gap:3px;font-size:12px}}input{{padding:6px;font-size:13px;min-width:150px}}button{{padding:7px 11px;border:1px solid;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer}}
.map{{background:#dafbe1;color:#116329}}.exclude{{background:#ffebe9;color:#82071e}}.leave{{background:#f6f8fa;color:#1f2328}}@media(max-width:760px){{.evidence-grid{{grid-template-columns:1fr}}header{{display:block}}}}</style>
</head><body>{_operational_nav('../', current='Historical Sales Reconciliation')}<h1>Historical ShopifyQL Sales — identity review</h1>
<p><b>{len(items)}</b> unresolved or ambiguous historical source identity group(s), ranked by materiality. Daily facts are grouped so each decision covers the complete historical source identity. Nothing on this page writes to Shopify.</p>
<p class='muted'>Mapping and exclusion decisions are permanent, audited, and require a reviewer, reason, and review token. Leaving an item unresolved keeps SALES_BACKFILL failed.</p>
{cards or '<p>No unresolved or ambiguous historical source identities require review.</p>'}
<script src='../assets/historical-sales-catalog-picker.js'></script>
</body></html>"""


@app.get("/historical-sales/review", response_class=HTMLResponse)
def historical_sales_review_page():
    data = historical_sales_review_items()
    return _historical_sales_review_html(data["items"])


@app.post("/historical-sales/review/decide", response_class=HTMLResponse)
def historical_sales_review_decide(
    source_key: str = Form(...),
    action: str = Form(...),
    actor: str = Form(...),
    reason: str = Form(...),
    canonical_variant_id: str | None = Form(None),
    review_token: str = Form(""),
):
    """Record one explicit, audited local resolution decision; never writes Shopify."""
    _require_review_token(review_token)
    source_key = str(source_key).strip()
    action = str(action).strip().upper()
    actor = _server_audit_actor(actor)
    reason = str(reason).strip()
    canonical_variant_id = (
        str(canonical_variant_id).strip() if canonical_variant_id not in (None, "") else None
    )
    if not source_key or not actor or not reason:
        raise HTTPException(status_code=400, detail="Source identity, reviewer, and reason are required")
    allowed = {"MAP_TO_CANONICAL", "EXCLUDE_HISTORICAL_ITEM", "LEAVE_UNRESOLVED"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail="Unknown historical-sales review action")
    if action == "MAP_TO_CANONICAL" and not canonical_variant_id:
        raise HTTPException(status_code=400, detail="Canonical Variant ID is required for mapping")
    if action != "MAP_TO_CANONICAL":
        canonical_variant_id = None

    with _db_conn() as conn:
        try:
            result = sales_service.record_historical_sales_review_decision(
                conn,
                source_key=source_key,
                action=action,
                canonical_variant_id=canonical_variant_id,
                actor=actor,
                reason=reason,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    # Never echo the review token or source payload. The refreshed queue is the
    # authoritative display after the local re-resolution/rebuild completes.
    decision = result.get("action", action) if isinstance(result, dict) else action
    import html
    return (f'<meta http-equiv="refresh" content="0;url=../review">'
            f'<p>{html.escape(str(decision), quote=True)} recorded. Returning to the review queue…</p>')


def _monday_runs_html(runs: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f"<td><a href='monday-runs/{_html_escape(run['run_id'])}'>{_html_escape(run['run_id'])}</a></td>"
        f"<td>{_html_escape(run['business_date'])}</td>"
        f"<td>{_html_escape(run['status'])}</td>"
        f"<td>{_html_escape(run['model_version'])}"
        + (
            "<br><strong>SYNTHETIC SELECTED OFFER — TEST DATA / NO REAL AUTHORITY</strong>"
            if run.get("synthetic_selected_offer_inputs")
            else ""
        )
        + "</td>"
        f"<td>{_html_escape(run['workflow_stage'])}</td>"
        f"<td>{_html_escape(run['exception_count'])}</td>"
        "</tr>"
        for run in runs
    ) or "<tr><td colspan='6'>No emergency Monday runs exist.</td></tr>"
    return f"""<!doctype html><html><head><title>Monday Procurement Runs</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1120px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
.warning{{border:2px solid #b42318;background:#ffebe9;padding:12px;font-weight:700}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #d1d9e0;padding:7px;text-align:left}}form{{display:grid;gap:9px;max-width:700px}}input,button{{padding:7px}}</style></head><body>
{_operational_nav('', current='Monday Procurement')}
<p class='warning'>TEST DATA — NOT FOR ORDERING. Internal DRAFT review only. No release or Shopify action is available.</p>
<h1>Monday Procurement Runs</h1>
<form method='post' action='monday-runs/prepare'>
<label>Business date <input type='date' name='business_date' value='{_form_value(inventory_business_date())}' required></label>
<label>Idempotency key <input name='idempotency_key' required></label>
<label>Canonical Shopify Variant IDs (comma separated) <input name='variant_ids' required></label>
<input type='hidden' name='actor' value='browser-value-ignored'>
<p>The immutable audit actor comes from the authenticated local session.</p>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
<button type='submit'>Prepare recommendations and stop for review</button></form>
<h2>Frozen runs</h2><table><thead><tr><th>Run</th><th>Business date</th><th>Status</th><th>Method</th><th>Stage</th><th>Open blockers</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""


def _monday_run_html(
    run: dict, review: dict, drafts: dict, artifacts: list[dict]
) -> str:
    run_id = _html_escape(run["run_id"])
    current_v2 = (
        run.get("model_version")
        in {
            CURRENT_METHOD_VERSION,
            DEVELOPMENT_FORECAST_METHOD_VERSION,
            DEVELOPMENT_FORECAST_V2_METHOD_VERSION,
        }
        and run.get("status") == "RUNNING"
    )
    retired_v1 = run.get("model_version") == RETIRED_METHOD_VERSION
    can_review = current_v2 and run.get("workflow_stage") == "AWAITING_REVIEW"
    can_build = (
        current_v2
        and run.get("workflow_stage") in {"REVIEWED", "DRAFTS_BUILT", "PACKET_BUILT"}
    ) or (
        retired_v1
        and run.get("status") == "RUNNING"
        and run.get("workflow_stage") in {"DRAFTS_BUILT", "PACKET_BUILT"}
    )
    can_retire = (
        retired_v1
        and run.get("status") == "RUNNING"
        and run.get("workflow_stage") in {"PREPARING", "AWAITING_REVIEW", "REVIEWED"}
        and not drafts["drafts"]
        and not artifacts
    )
    selected_run = bool(run.get("synthetic_selected_offer_inputs"))
    selected_banner = (
        "<p class='warning selected-offer-authority'>SYNTHETIC SELECTED OFFER — "
        "TEST DATA / NO REAL AUTHORITY. Selection is input authority only for this "
        "attested local synthetic run; real/default cutover remains disabled.</p>"
        if selected_run
        else ""
    )
    blocker_rows = []
    for item in run["blockers"]:
        if item.get("excluded"):
            exclusion = item["exclusion"]
            control = (
                "<b>ACKNOWLEDGE_AND_EXCLUDE — RUN_ONLY</b><br>"
                f"Actor {_html_escape(exclusion['actor'])}; reason "
                f"{_html_escape(exclusion['reason'])}; at "
                f"{_html_escape(exclusion['created_at'])}. Original blocker retained."
            )
        elif can_review:
            control = f"""<form method='post' action='{run_id}/blockers/{item['exception_id']}/exclude'>
	<input type='hidden' name='expected_input_fingerprint' value='{_form_value(run['input_fingerprint'])}'>
	<label>Exclusion reason <input name='reason' required></label>
	<input type='hidden' name='actor' value='browser-value-ignored'>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
<button type='submit'>Acknowledge and exclude from this run only</button></form>"""
        elif can_retire:
            control = (
                "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED — retire this "
                "unbuilt V1 run before preparing with V2."
            )
        elif retired_v1:
            control = "The frozen V1 run is immutable and read-only at this stage."
        else:
            control = "Not excluded before review completion."
        blocker_rows.append(
            "<tr>"
            f"<td>{_html_escape(item['variant_id'])}</td>"
            f"<td>{_html_escape(item['vendor_id'])}</td>"
            f"<td>{_html_escape(item['message'])}</td>"
            f"<td>{control}</td></tr>"
        )
    blockers = "".join(blocker_rows) or (
        "<tr><td colspan='4'>No original run blockers.</td></tr>"
    )
    review_rows = []
    for item in review["items"]:
        metrics = item.get("metrics") or {}
        frozen_terms = metrics.get("frozen_vendor_terms") or {}
        offer = metrics.get("frozen_offer_evidence") or {}
        selected_evidence = metrics.get("selected_offer_input_evidence")
        selected_authority_row = ""
        if selected_evidence is not None:
            selected_authority_row = (
                "<tr><th>Selected input authority</th><td><pre "
                "class='selected-offer-input-json'>"
                + _html_escape(
                    json.dumps(selected_evidence, sort_keys=True, indent=2, default=str)
                )
                + "</pre></td></tr>"
            )
        demand_evidence = metrics.get("demand_evidence") or {}
        demand_evidence_json = json.dumps(
            demand_evidence, sort_keys=True, indent=2, default=str
        )
        if demand_evidence.get("contract") == "BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2":
            demand_evidence_row = f"""
<tr><th>Evidence-correct demand foundation</th><td><section class='demand-evidence' data-contract='BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2'>
<p>Contract {_html_escape(demand_evidence.get('contract'))}; method {_html_escape(demand_evidence.get('method_version'))}; bounds {_html_escape(demand_evidence.get('history_start'))}–{_html_escape(demand_evidence.get('history_end'))}; calendar days {_html_escape(demand_evidence.get('calendar_days'))}.</p>
<p>Model/FVA {_html_escape((demand_evidence.get('statuses') or {}).get('model_selection_status'))}; classification {_html_escape((demand_evidence.get('statuses') or {}).get('classification_status'))}; stockout censoring {_html_escape((demand_evidence.get('statuses') or {}).get('stockout_censoring_status'))}; safety stock {_html_escape((demand_evidence.get('statuses') or {}).get('safety_stock_status'))}.</p>
<p>Raw 7/14/28-day windows {_html_escape(demand_evidence.get('raw_windows'))}; availability counts {_html_escape(demand_evidence.get('availability_summary'))}; snapshot groups {_html_escape(demand_evidence.get('snapshot_groups'))}; reasons {_html_escape(demand_evidence.get('reason_codes'))}. Point-in-time inventory is evidence only and every daily availability state remains UNKNOWN.</p>
<details><summary>Exact frozen demand evidence</summary><pre class='demand-evidence-json'>{_html_escape(demand_evidence_json)}</pre></details>
</section></td></tr>"""
        else:
            demand_evidence_row = (
                "<tr><th>Evidence-correct demand foundation</th><td>"
                "V2 demand evidence is absent on this frozen legacy record; no "
                "new demand, stockout, model-selection, or safety-stock claim is made."
                "</td></tr>"
            )
        development_forecast_evidence = metrics.get(
            "development_forecast_evidence"
        )
        if (
            isinstance(development_forecast_evidence, dict)
            and development_forecast_evidence.get("contract")
            in {
                DEVELOPMENT_FORECAST_CONTRACT,
                DEVELOPMENT_FORECAST_V2_CONTRACT,
            }
        ):
            development_json = json.dumps(
                development_forecast_evidence,
                sort_keys=True,
                indent=2,
                default=str,
            )
            development_forecast_row = f"""
<tr><th>Development forecast / protection</th><td><section class='development-forecast-evidence' data-contract='{_html_escape(development_forecast_evidence.get('contract'))}'>
<p><b>LOCAL SYNTHETIC DEVELOPMENT ONLY — NO COMMERCIAL AUTHORITY.</b> Method {_html_escape(development_forecast_evidence.get('method_version'))}; selected model {_html_escape(development_forecast_evidence.get('selected_model'))}; regime {_html_escape(development_forecast_evidence.get('demand_regime'))}; XYZ {_html_escape(development_forecast_evidence.get('xyz_class'))}; ABC {_html_escape(development_forecast_evidence.get('abc_class'))} ({_html_escape(development_forecast_evidence.get('abc_status'))}).</p>
<p>Point forecast {_html_escape(development_forecast_evidence.get('point_forecast_units'))}; empirical full-horizon protection {_html_escape(development_forecast_evidence.get('protection_units'))}; target {_html_escape(development_forecast_evidence.get('target_units'))}; horizon {_html_escape(development_forecast_evidence.get('horizon_days'))} day(s).</p>
<p>Protection status {_html_escape((development_forecast_evidence.get('protection') or {}).get('status'))}; availability qualification {_html_escape((development_forecast_evidence.get('availability') or {}).get('protection_qualification'))}; confidence {_html_escape(development_forecast_evidence.get('confidence'))}; evaluation {_html_escape(development_forecast_evidence.get('evaluation_status'))}.</p>
<p>Selection metrics {_html_escape(development_forecast_evidence.get('selection_metrics'))}; final evaluation {_html_escape(development_forecast_evidence.get('evaluation_metrics'))}; reasons {_html_escape(development_forecast_evidence.get('reason_codes'))}; cap evidence {_html_escape(development_forecast_evidence.get('caps'))}.</p>
<details><summary>Exact frozen development forecast evidence</summary><pre class='development-forecast-evidence-json'>{_html_escape(development_json)}</pre></details>
</section></td></tr>"""
        else:
            development_forecast_row = ""
        capture = metrics.get("frozen_inventory_capture") or []
        inventory_rows = "".join(
            "<tr>"
            f"<td>{_html_escape(row[0])}</td><td>{_html_escape(row[1])}</td>"
            f"<td>{_html_escape(row[2])}</td><td>{_html_escape(row[3])}</td>"
            "</tr>"
            for row in (metrics.get("frozen_inventory_rows") or [])
        ) or "<tr><td colspan='4'>No frozen inventory row.</td></tr>"
        price_rows = "".join(
            "<tr>"
            f"<td>{_html_escape(row[3])}</td>"
            f"<td>{_html_escape(row[4])} {_html_escape(row[5])}</td>"
            f"<td>${_html_escape(row[6])}</td><td>${_html_escape(row[7])}</td>"
            f"<td>{_html_escape(row[8])} p.{_html_escape(row[9])}</td>"
            "</tr>"
            for row in (metrics.get("frozen_price_ladder") or [])
        ) or "<tr><td colspan='5'>No frozen price row.</td></tr>"
        capture_text = (
            f"run {_html_escape(capture[0])}; business date {_html_escape(capture[1])}; "
            f"captured {_html_escape(capture[2])}; source {_html_escape(capture[3])}; "
            f"hash {_html_escape(capture[4])}; rows/eligible/invalid/incomplete "
            f"{_html_escape(capture[5])}/{_html_escape(capture[6])}/"
            f"{_html_escape(capture[7])}/{_html_escape(capture[8])}"
            if len(capture) >= 9
            else "Frozen current inventory capture is absent."
        )
        evidence_table = f"""
<table class='facts'>
{selected_authority_row}
<tr><th>Supplier / pack</th><td>{_html_escape(offer.get('supplier_sku'))}; {_html_escape(offer.get('size_text'))}; {_html_escape(offer.get('raw_pack'))}; Shopify units/case {_html_escape(offer.get('shopify_units_per_case'))}; qualifying units/case {_html_escape(offer.get('qualifying_units_per_case'))}; assortment {_html_escape(offer.get('assortment_scope'))}/{_html_escape(offer.get('assortment_group'))}; assortable {_html_escape(offer.get('assortable'))}</td></tr>
<tr><th>Offer evidence</th><td>{_html_escape(offer.get('confidence'))}; {_html_escape(offer.get('source_file'))} p.{_html_escape(offer.get('source_page'))}; validity {_html_escape(offer.get('valid_from'))}–{_html_escape(offer.get('valid_to'))}</td></tr>
<tr><th>Inventory capture</th><td>{capture_text}</td></tr>
<tr><th>Captured Available</th><td>{_html_escape(metrics.get('available_units'))} unit(s), captured at {_html_escape(capture[2] if len(capture) >= 3 else None)} from snapshot run {_html_escape(capture[0] if capture else None)}. This is frozen run evidence, not a live inventory read.</td></tr>
<tr><th>Inventory locations</th><td><table><tr><th>Location</th><th>Available</th><th>Captured incoming</th><th>Status</th></tr>{inventory_rows}</table></td></tr>
<tr><th>Trusted incoming</th><td>{_html_escape(metrics.get('trusted_incoming_units'))}; reconciliation {_html_escape(metrics.get('frozen_open_po_position'))}</td></tr>
<tr><th>Demand / coverage</th><td>Authority {_html_escape(metrics.get('frozen_sales_authority'))}; velocity {_html_escape(metrics.get('forecast_daily_velocity'))}/day; horizon {_html_escape(metrics.get('forecast_horizon_days'))} days; forecast {_html_escape(metrics.get('forecast_units'))}; baseline need {_html_escape(metrics.get('raw_need_units'))}; target {_html_escape(metrics.get('target_units'))}; reasons {_html_escape(metrics.get('need_reason_codes'))}</td></tr>
{demand_evidence_row}
{development_forecast_row}
<tr><th>Retail / margin diagnostic</th><td>Frozen retail ${_html_escape(metrics.get('frozen_catalog_retail_price'))}; unit GP ${_html_escape(metrics.get('frozen_unit_gross_profit'))}; gross margin {_html_escape(metrics.get('frozen_gross_margin_pct'))}% (diagnostic only)</td></tr>
<tr><th>Frozen price ladder</th><td><table><tr><th>Level</th><th>Break</th><th>Case</th><th>Unit</th><th>Source</th></tr>{price_rows}</table></td></tr>
</table>"""
        minimum_evidence = (
            f"Vendor minimum: {_html_escape(frozen_terms.get('minimum_type'))} "
            f"{_html_escape(frozen_terms.get('minimum_value'))}; below-minimum fee "
            f"${_html_escape(frozen_terms.get('below_minimum_fee'))}."
        )
        if item["decision_id"] is not None:
            decision = (
                f"<b>{_html_escape(item['action'])}</b>: "
                f"{_html_escape(item['approved_cases'])} case(s) + "
                f"{_html_escape(item['approved_loose_units'])} loose; "
                f"{_html_escape(item['approved_units'])} unit(s) "
                f"@ ${_html_escape(item['approved_unit_cost'])}; "
                f"merchandise ${_html_escape(item['approved_merchandise_total'])}; "
                f"loose-order fee ${_html_escape(item['approved_loose_order_fee'])}; "
                f"reviewed line total ${_html_escape(item['approved_line_total'])}; "
                f"resulting inventory {_html_escape(item['resulting_inventory_units'])} unit(s), "
                f"{_html_escape(item['resulting_days_supply'])} days supply "
                f"({_html_escape(item['days_supply_status'])})"
                + (
                    "; final frozen tier <pre class='final-price-tier-json'>"
                    + _html_escape(json.dumps(item["final_price_tier"], sort_keys=True, default=str))
                    + "</pre>"
                    if item.get("final_price_tier") is not None
                    else ""
                )
            )
        elif can_review:
            decision = f"""<form method='post' action='{run_id}/recommendations/{item['recommendation_id']}/review'>
<input type='hidden' name='expected_input_fingerprint' value='{_form_value(run['input_fingerprint'])}'>
<label>Decision <select name='action'><option>ACCEPT</option><option>EDIT_QUANTITY</option><option>REJECT</option></select></label>
<label>Cases <input type='number' min='0' step='1' name='approved_cases' value='{_form_value(item['recommended_cases'])}'></label>
<label>Loose units <input type='number' min='0' step='1' name='approved_loose_units' value='{_form_value(item['recommended_loose_units'])}'></label>
	<label>Comment <input name='comment'></label><input type='hidden' name='actor' value='browser-value-ignored'>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
<button type='submit'>Record immutable decision</button></form>"""
        elif retired_v1:
            decision = (
                "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED — no new V1 review "
                "or edit is permitted."
            )
        else:
            decision = "No review action is available at this workflow stage."
        review_rows.append(
            "<tr>"
            f"<td>{_html_escape(item['variant_id'])}<br><small>{_html_escape(item['product_title'])} / {_html_escape(item['variant_title'])}</small></td>"
            f"<td>{_html_escape(item['vendor_name'])}</td>"
            f"<td>{_html_escape(item['recommended_cases'])} case(s) + {_html_escape(item['recommended_loose_units'])} loose<br>"
            f"{_html_escape(item['recommended_units'])} unit(s) @ ${_html_escape(item['unit_cost'])}"
            f"<br><small>{minimum_evidence}</small>"
            f"<details><summary>Structured frozen calculation evidence</summary>{evidence_table}<details><summary>Canonical evidence object</summary><pre>{_html_escape(item['metrics'])}</pre></details></details></td>"
            f"<td>{decision}</td></tr>"
        )
    draft_sections = []
    for draft in drafts["drafts"]:
        line_rows = "".join(
            "<tr>"
            f"<td>{_html_escape(line['variant_id'])}</td><td>{_html_escape(line['supplier_sku'])}</td>"
            f"<td>{_html_escape(line['cases'])}</td><td>{_html_escape(line['loose_units'])}</td>"
            f"<td>{_html_escape(line['ordered_units'])}</td><td>${_html_escape(line['unit_cost'])}</td>"
            f"<td>{_html_escape(line.get('captured_available_quantity'))}</td>"
            f"<td>{_html_escape(line.get('inventory_captured_at'))}</td>"
            f"<td>${_html_escape(line['merchandise_total'])}</td>"
            f"<td>${_html_escape(line['loose_order_fee'])}</td>"
            f"<td>${_html_escape(line['line_total'])}"
            + (
                "<br><pre class='draft-final-price-tier-json'>"
                + _html_escape(json.dumps(line["final_price_tier"], sort_keys=True, default=str))
                + "</pre>"
                if line.get("final_price_tier") is not None
                else ""
            )
            + "</td></tr>"
            for line in draft["lines"]
        )
        draft_sections.append(
            f"<h3>{_html_escape(draft['vendor_name'])} — DRAFT</h3>"
            f"<p>PO {_html_escape(draft['po_id'])}; merchandise ${_html_escape(draft['merchandise_total'])}; "
            f"loose-order fees ${_html_escape(draft['loose_order_fee_total'])}; "
            f"below-minimum fee ${_html_escape(draft['below_minimum_fee'])}; "
            f"total fees ${_html_escape(draft['delivery_fee'])}; total ${_html_escape(draft['po_total'])}; "
            f"minimum shortfall {_html_escape(draft.get('minimum_shortfall'))}; "
            f"disposition {_html_escape(draft.get('minimum_disposition'))}; confirmed by "
            f"{_html_escape(draft.get('economics_confirmed_by'))}</p>"
            "<table><thead><tr><th>Variant</th><th>Supplier SKU</th><th>Cases</th><th>Loose</th>"
            "<th>Units</th><th>Unit cost</th><th>Captured Available</th><th>Captured at</th><th>Merchandise</th><th>Loose fee</th>"
            f"<th>Line total</th></tr></thead><tbody>{line_rows}</tbody></table>"
        )
    artifact_rows = "".join(
        "<tr>"
        f"<td>{_html_escape(item['artifact_type'])}</td><td>{_html_escape(item['vendor_id'])}</td>"
        f"<td>{_html_escape(item['size_bytes'])}</td>"
        f"<td><a href='{run_id}/artifacts/{item['artifact_id']}'>Download hash-checked artifact</a></td></tr>"
        for item in artifacts
    ) or "<tr><td colspan='4'>No artifacts built.</td></tr>"
    merchandise_grand = sum(
        (Decimal(str(draft["merchandise_total"])) for draft in drafts["drafts"]),
        Decimal("0"),
    )
    fee_grand = sum(
        (Decimal(str(draft["delivery_fee"])) for draft in drafts["drafts"]),
        Decimal("0"),
    )
    total_grand = sum(
        (Decimal(str(draft["po_total"])) for draft in drafts["drafts"]),
        Decimal("0"),
    )
    grand_summary = (
        f"<p><b>Grand totals:</b> merchandise ${_html_escape(merchandise_grand)}; "
        f"fees ${_html_escape(fee_grand)}; internal DRAFT total ${_html_escape(total_grand)}.</p>"
        if drafts["drafts"]
        else "<p>No vendor or grand total exists until reviewed quantities are built.</p>"
    )
    build_form = ""
    if can_build:
        build_form = f"""<form method='post' action='{run_id}/build'>
	<input type='hidden' name='actor' value='browser-value-ignored'>
	<p>Builder identity comes from the authenticated local session.</p>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
<button type='submit'>Build/replay internal DRAFTs and review packet</button></form>"""
    retirement_form = ""
    if can_retire:
        retirement_form = f"""<section class='warning'>
<h2>Retired forecast method requires re-preparation</h2>
<p><code>FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED</code>. This action preserves
the complete V1 run and marks only its status/stage FAILED, releasing the business
date for a separately keyed V2 preparation. It cannot supersede an immutable DRAFT
and performs no PO release or Shopify action.</p>
<form method='post' action='{run_id}/retire-stale-forecast'>
<label>Required retirement reason <input name='reason' required></label>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
<button type='submit'>Preview exact V1 retirement</button></form></section>"""
    return f"""<!doctype html><html><head><title>Monday Run {run_id}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1240px;margin:2rem auto;padding:0 1rem;color:#1f2328}}.warning{{border:2px solid #b42318;background:#ffebe9;padding:12px;font-weight:700}}
	table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #d1d9e0;padding:7px;vertical-align:top;text-align:left}}table.facts th:first-child{{width:180px}}form{{display:grid;gap:6px}}input,select,button{{padding:6px}}</style></head><body>
{_operational_nav('../', current='Monday Procurement')}<p><a href='../monday-runs'>Back to Monday runs</a></p>
<p class='warning'>TEST DATA — NOT FOR ORDERING. SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED. DRAFT output only.</p>
{selected_banner}
<h1>Monday run {run_id}</h1><p>Status: <b>{_html_escape(run['status'])}</b>; stage: <b>{_html_escape(run['workflow_stage'])}</b>; method: <b>{_html_escape(run['model_version'])}</b>; business date: {_html_escape(run['business_date'])}; fingerprint: <code>{_html_escape(run['input_fingerprint'])}</code></p>
{retirement_form}
<h2>Blockers</h2><table><thead><tr><th>Variant</th><th>Vendor</th><th>Original reason</th><th>RUN_ONLY disposition</th></tr></thead><tbody>{blockers}</tbody></table>
<h2>Human review</h2><table><thead><tr><th>Item</th><th>Vendor</th><th>Recommendation</th><th>Decision</th></tr></thead><tbody>{''.join(review_rows) or '<tr><td colspan="4">No eligible recommendations.</td></tr>'}</tbody></table>
	{build_form}<h2>Vendor DRAFT POs</h2>{''.join(draft_sections) or '<p>No DRAFTs built.</p>'}{grand_summary}
<h2>Internal artifacts</h2><table><thead><tr><th>Type</th><th>Vendor</th><th>Bytes</th><th>Download</th></tr></thead><tbody>{artifact_rows}</tbody></table>
    </body></html>"""


def _monday_review_preview_html(
    *,
    run_id: UUID,
    recommendation_id: int,
    preview: dict,
    actor: str,
    comment: str,
    material_edit_confirmation_id: int | None = None,
) -> str:
    metrics = preview.get("metrics") or {}
    terms = metrics.get("frozen_vendor_terms") or {}
    case_price = preview.get("approved_case_price")
    case_price_text = (
        f"${_html_escape(case_price)}"
        if case_price is not None
        else "not separately quoted"
    )
    materiality = preview.get("materiality") or {}
    multiplier = materiality.get("baseline_multiplier")
    multiplier_text = (
        f"{_html_escape(multiplier)}x"
        if multiplier is not None
        else _html_escape(materiality.get("baseline_multiplier_status"))
    )
    material_confirmation = ""
    if materiality.get("materiality_tier") == "MATERIAL":
        if material_edit_confirmation_id is None:
            material_confirmation = """<label>Distinct MATERIAL edit confirmation reason
<input name='material_confirmation_reason' required></label>
<button type='submit'>Record distinct MATERIAL-risk confirmation</button>"""
        else:
            material_confirmation = (
                "<input type='hidden' name='material_edit_confirmation_id' "
                f"value='{_form_value(material_edit_confirmation_id)}'>"
                "<p><b>Distinct MATERIAL-risk confirmation recorded.</b> "
                "The next action creates the immutable RUN_ONLY decision.</p>"
                "<button type='submit'>Confirm immutable MATERIAL EDIT_QUANTITY decision</button>"
            )
    else:
        material_confirmation = (
            f"<button type='submit'>Confirm immutable {_html_escape(preview['action'])} decision</button>"
        )
    return f"""<!doctype html><html><head><title>Confirm edited recommendation</title>
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#1f2328}}.warning{{border:2px solid #b42318;background:#ffebe9;padding:12px;font-weight:700}}dl{{display:grid;grid-template-columns:max-content 1fr;gap:8px 16px}}dt{{font-weight:700}}form{{display:grid;gap:9px}}input,button{{padding:7px}}</style></head><body>
<p class='warning'>TEST DATA — NOT FOR ORDERING. Review these exact calculations before creating an immutable RUN_ONLY decision.</p>
<h1>Confirm recommendation economics</h1>
<dl><dt>Cases</dt><dd>{_html_escape(preview['approved_cases'])}</dd>
<dt>Loose units</dt><dd>{_html_escape(preview['approved_loose_units'])}</dd>
<dt>Ordered units</dt><dd>{_html_escape(preview['approved_units'])}</dd>
<dt>Frozen applicable unit cost</dt><dd>${_html_escape(preview['approved_unit_cost'])}</dd>
<dt>Frozen applicable case price</dt><dd>{case_price_text}</dd>
{("<dt>Final frozen price tier</dt><dd><pre class='final-price-tier-json'>" + _html_escape(json.dumps(preview['final_price_tier'], sort_keys=True, default=str)) + "</pre></dd>" if preview.get('final_price_tier') is not None else "")}
<dt>Merchandise total</dt><dd>${_html_escape(preview['approved_merchandise_total'])}</dd>
<dt>Loose-order fee</dt><dd>${_html_escape(preview['approved_loose_order_fee'])}</dd>
<dt>Recalculated line total</dt><dd>${_html_escape(preview['approved_line_total'])}</dd>
<dt>Resulting inventory units</dt><dd>{_html_escape(preview['resulting_inventory_units'])}</dd>
<dt>Resulting days of supply</dt><dd>{_html_escape(preview['resulting_days_supply'])} ({_html_escape(preview['days_supply_status'])})</dd>
<dt>Edit materiality</dt><dd>{_html_escape(materiality.get('materiality_tier'))}: {_html_escape(materiality.get('materiality_reason_codes'))}</dd>
<dt>Raw baseline units</dt><dd>{_html_escape(materiality.get('baseline_units'))}</dd>
<dt>Original recommended units</dt><dd>{_html_escape(materiality.get('recommended_units'))}</dd>
<dt>Edited / baseline multiplier</dt><dd>{multiplier_text}</dd>
<dt>Original recommended line cash</dt><dd>${_html_escape(materiality.get('recommended_line_cash'))}</dd>
<dt>Incremental line cash</dt><dd>${_html_escape(materiality.get('incremental_line_cash'))}</dd>
<dt>Final line cash</dt><dd>${_html_escape(materiality.get('final_line_cash'))}</dd>
<dt>Emergency thresholds</dt><dd>{_html_escape(materiality.get('policy'))}</dd>
<dt>Vendor minimum</dt><dd>{_html_escape(terms.get('minimum_type'))} {_html_escape(terms.get('minimum_value'))}</dd>
<dt>Below-minimum fee</dt><dd>${_html_escape(terms.get('below_minimum_fee'))}</dd></dl>
<p>Vendor-level minimum and fee are rechecked across all accepted lines when DRAFTs are built.</p>
<form method='post'>
<input type='hidden' name='action' value='{_form_value(preview['action'])}'>
<input type='hidden' name='actor' value='{_form_value(actor)}'>
<input type='hidden' name='expected_input_fingerprint' value='{_form_value(preview['input_fingerprint'])}'>
<input type='hidden' name='approved_cases' value='{_form_value(preview['approved_cases'])}'>
<input type='hidden' name='approved_loose_units' value='{_form_value(preview['approved_loose_units'])}'>
<input type='hidden' name='comment' value='{_form_value(comment)}'>
<input type='hidden' name='review_preview_fingerprint' value='{_form_value(preview['preview_fingerprint'])}'>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
{material_confirmation}</form>
<p><a href='../../../{_html_escape(run_id)}'>Cancel and return without recording a decision</a></p>
</body></html>"""


def _monday_draft_preview_html(*, run_id: UUID, preview: dict) -> str:
    vendor_row_items = []
    for item in preview["vendors"]:
        selected_lines = [
            line for line in item.get("lines", []) if line.get("final_price_tier")
        ]
        tier_details = ""
        if selected_lines:
            tier_details = (
                "<details><summary>Selected offer and final tier evidence</summary><pre "
                "class='draft-preview-final-price-tiers-json'>"
                + _html_escape(
                    json.dumps(selected_lines, sort_keys=True, indent=2, default=str)
                )
                + "</pre></details>"
            )
        vendor_row_items.append(
            "<tr>"
            f"<td>{_html_escape(item['vendor_id'])}{tier_details}</td>"
            f"<td>${_html_escape(item['merchandise_total'])}</td>"
            f"<td>{_html_escape(item['case_count'])}</td>"
            f"<td>{_html_escape(item['minimum_type'])} {_html_escape(item['minimum_value'])}</td>"
            f"<td>{_html_escape(item['minimum_shortfall'])}</td>"
            f"<td>${_html_escape(item['loose_order_fee_total'])}</td>"
            f"<td>${_html_escape(item['below_minimum_fee'])}</td>"
            f"<td>${_html_escape(item['delivery_fee'])}</td>"
            f"<td>${_html_escape(item['po_total'])}</td>"
            "</tr>"
        )
    vendor_rows = "".join(vendor_row_items) or (
        "<tr><td colspan='9'>No positive reviewed quantities; the packet will "
        "record a no-order run.</td></tr>"
    )
    disposition_text = (
        "PAY_FEE for every below-minimum vendor"
        if preview["minimum_disposition"] == "PAY_FEE"
        else "NOT_APPLICABLE — no vendor is below minimum"
    )
    merchandise_grand = sum(
        (Decimal(str(item["merchandise_total"])) for item in preview["vendors"]),
        Decimal("0"),
    )
    fee_grand = sum(
        (Decimal(str(item["delivery_fee"])) for item in preview["vendors"]),
        Decimal("0"),
    )
    total_grand = sum(
        (Decimal(str(item["po_total"])) for item in preview["vendors"]),
        Decimal("0"),
    )
    return f"""<!doctype html><html><head><title>Confirm vendor DRAFT economics</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1040px;margin:2rem auto;padding:0 1rem;color:#1f2328}}.warning{{border:2px solid #b42318;background:#ffebe9;padding:12px;font-weight:700}}table{{border-collapse:collapse;width:100%;margin:16px 0}}th,td{{border:1px solid #d1d9e0;padding:7px;text-align:left}}form{{display:grid;gap:9px;max-width:700px}}input,button{{padding:7px}}</style></head><body>
<p class='warning'>TEST DATA — NOT FOR ORDERING. Confirm exact vendor totals and fee disposition before DRAFT persistence.</p>
<h1>Confirm vendor DRAFT economics</h1>
<table><thead><tr><th>Vendor ID</th><th>Merchandise</th><th>Cases</th><th>Minimum</th><th>Shortfall</th><th>Loose-order fees</th><th>Below-minimum fee</th><th>Total fees</th><th>DRAFT total</th></tr></thead><tbody>{vendor_rows}</tbody></table>
<p><b>Preview grand totals:</b> merchandise ${_html_escape(merchandise_grand)}; fees ${_html_escape(fee_grand)}; internal DRAFT total ${_html_escape(total_grand)}.</p>
<p><b>Minimum disposition:</b> {_html_escape(disposition_text)}. DELAY or ADD_LEGITIMATE_NEED requires cancelling and changing the reviewed run; the system never adds filler.</p>
<form method='post'>
<input type='hidden' name='actor' value='{_form_value(preview['actor'])}'>
<input type='hidden' name='draft_preview_fingerprint' value='{_form_value(preview['preview_fingerprint'])}'>
<input type='hidden' name='minimum_disposition' value='{_form_value(preview['minimum_disposition'])}'>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
<button type='submit'>Confirm reviewed economics and build internal DRAFTs</button></form>
<p><a href='../{_html_escape(run_id)}'>Cancel without building DRAFTs</a></p>
</body></html>"""


def _monday_stale_forecast_retirement_preview_html(
    *, run_id: UUID, preview: dict
) -> str:
    return f"""<!doctype html><html><head><title>Confirm retired forecast run</title>
<style>body{{font-family:system-ui,sans-serif;max-width:780px;margin:2rem auto;padding:0 1rem;color:#1f2328}}.warning{{border:2px solid #b42318;background:#ffebe9;padding:12px;font-weight:700}}dl{{display:grid;grid-template-columns:max-content 1fr;gap:8px 16px}}dt{{font-weight:700}}form{{display:grid;gap:9px}}input,button{{padding:7px}}</style></head><body>
<p class='warning'>TEST DATA — NOT FOR ORDERING. This action does not create, release, or transmit a PO.</p>
<h1>Confirm exact V1 retirement</h1>
<p>The frozen run and all evidence remain immutable. Confirmation changes only
<code>status</code> and <code>workflow_stage</code> to <code>FAILED</code>, which
releases the business date for a separately keyed V2 preparation. It cannot
supersede an existing DRAFT.</p>
<dl>
<dt>Run</dt><dd><code>{_html_escape(preview['run_id'])}</code></dd>
<dt>Business date</dt><dd>{_html_escape(preview['business_date'])}</dd>
<dt>Frozen fingerprint</dt><dd><code>{_html_escape(preview['input_fingerprint'])}</code></dd>
<dt>Retired method</dt><dd>{_html_escape(preview['retired_model_version'])}</dd>
<dt>Transition</dt><dd>{_html_escape(preview['prior_status'])}/{_html_escape(preview['prior_workflow_stage'])} → {_html_escape(preview['target_status'])}/{_html_escape(preview['target_workflow_stage'])}</dd>
<dt>Purchase orders / artifacts</dt><dd>{_html_escape(preview['purchase_order_count'])} / {_html_escape(preview['artifact_count'])}</dd>
<dt>Authenticated actor</dt><dd>{_html_escape(preview['actor'])}</dd>
<dt>Reason</dt><dd>{_html_escape(preview['reason'])}</dd>
<dt>Confirmation SHA-256</dt><dd><code>{_html_escape(preview['confirmation_sha256'])}</code></dd>
</dl>
<form method='post'>
<input type='hidden' name='reason' value='{_form_value(preview['reason'])}'>
<input type='hidden' name='expected_confirmation_sha256' value='{_form_value(preview['confirmation_sha256'])}'>
<label>Review token <input type='password' name='review_token' autocomplete='current-password' required></label>
<button type='submit'>Confirm immutable V1 retirement</button></form>
<p><a href='/monday-runs/{_html_escape(run_id)}'>Cancel without changing the run</a></p>
</body></html>"""


@app.get("/monday-runs")
def monday_runs_page():
    with _db_conn() as conn:
        runs = list_monday_runs(conn)
    return HTMLResponse(_monday_runs_html(runs), headers={"Cache-Control": "no-store"})


@app.post("/monday-runs/prepare")
def monday_runs_prepare(
    business_date: date = Form(...),
    idempotency_key: str = Form(...),
    variant_ids: str = Form(...),
    actor: str = Form(...),
    review_token: str = _review_token_form(),
):
    _require_review_token(review_token)
    actor = _server_audit_actor(actor)
    canonical_ids = tuple(value.strip() for value in variant_ids.split(",") if value.strip())
    if not canonical_ids:
        raise HTTPException(status_code=400, detail="At least one canonical Variant ID is required")
    try:
        with _db_conn() as conn:
            result = prepare_monday_run(
                conn,
                business_date=business_date,
                idempotency_key=idempotency_key,
                variant_ids=canonical_ids,
                actor=actor,
            )
    except MondayRecommendationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"../monday-runs/{result['run_id']}",
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/monday-runs/{run_id}")
def monday_run_detail(run_id: UUID):
    try:
        with _db_conn() as conn:
            run = get_monday_run(conn, str(run_id))
            review = list_review_queue(conn, str(run_id))
            drafts = get_vendor_drafts(conn, str(run_id))
            artifacts = list_monday_artifacts(conn, str(run_id))
    except (MondayRecommendationError, ProcurementReviewError, DraftPoError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return HTMLResponse(
        _monday_run_html(run, review, drafts, artifacts),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/monday-runs/{run_id}/retire-stale-forecast")
def monday_stale_forecast_retire(
    request: Request,
    run_id: UUID,
    reason: str = Form(...),
    expected_confirmation_sha256: str | None = Form(None),
    review_token: str = _review_token_form(),
):
    _require_review_token(review_token)
    principal = action_principal(request, "procurement.order.approve")
    try:
        with _db_conn() as conn:
            if expected_confirmation_sha256 is None:
                preview = preview_monday_stale_forecast_retirement(
                    conn,
                    run_id=str(run_id),
                    actor=principal.principal_ref,
                    reason=reason,
                )
                return HTMLResponse(
                    _monday_stale_forecast_retirement_preview_html(
                        run_id=run_id, preview=preview
                    ),
                    headers={"Cache-Control": "no-store"},
                )
            confirm_monday_stale_forecast_retirement(
                conn,
                run_id=str(run_id),
                actor=principal.principal_ref,
                reason=reason,
                expected_confirmation_sha256=expected_confirmation_sha256,
            )
    except MondayRecommendationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"/monday-runs/{run_id}",
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/monday-runs/{run_id}/blockers/{exception_id}/exclude")
def monday_blocked_item_exclude(
    run_id: UUID,
    exception_id: int,
    actor: str = Form(...),
    reason: str = Form(...),
    expected_input_fingerprint: str = Form(...),
    review_token: str = _review_token_form(),
):
    _require_review_token(review_token)
    actor = _server_audit_actor(actor)
    try:
        with _db_conn() as conn:
            acknowledge_and_exclude_blocked_item(
                conn,run_id=str(run_id),exception_id=exception_id,actor=actor,
                reason=reason,expected_input_fingerprint=expected_input_fingerprint,
            )
    except ProcurementReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"../../../{run_id}", status_code=303,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/monday-runs/{run_id}/recommendations/{recommendation_id}/review")
def monday_recommendation_review(
    run_id: UUID,
    recommendation_id: int,
    action: str = Form(...),
    actor: str = Form(...),
    expected_input_fingerprint: str = Form(...),
    approved_cases: str | None = Form(None),
    approved_loose_units: str | None = Form(None),
    comment: str = Form(""),
    review_preview_fingerprint: str | None = Form(None),
    material_confirmation_reason: str = Form(""),
    material_edit_confirmation_id: int | None = Form(None),
    review_token: str = _review_token_form(),
):
    _require_review_token(review_token)
    actor = _server_audit_actor(actor)
    try:
        with _db_conn() as conn:
            queue = list_review_queue(conn, str(run_id))
            if recommendation_id not in {item["recommendation_id"] for item in queue["items"]}:
                raise ProcurementReviewError("recommendation does not belong to this Monday run")
            if (
                action.strip().upper() in {"ACCEPT", "EDIT_QUANTITY"}
                and not review_preview_fingerprint
            ):
                preview = preview_recommendation_review(
                    conn,
                    recommendation_id=recommendation_id,
                    action=action,
                    actor=actor,
                    expected_input_fingerprint=expected_input_fingerprint,
                    approved_cases=approved_cases,
                    approved_loose_units=approved_loose_units,
                    comment=comment,
                )
                return HTMLResponse(
                    _monday_review_preview_html(
                        run_id=run_id,
                        recommendation_id=recommendation_id,
                        preview=preview,
                        actor=actor,
                        comment=comment,
                    ),
                    headers={"Cache-Control": "no-store"},
                )
            if action.strip().upper() == "EDIT_QUANTITY" and review_preview_fingerprint:
                preview = preview_recommendation_review(
                    conn,recommendation_id=recommendation_id,action=action,actor=actor,
                    expected_input_fingerprint=expected_input_fingerprint,
                    approved_cases=approved_cases,
                    approved_loose_units=approved_loose_units,comment=comment,
                )
                if preview["preview_fingerprint"] != review_preview_fingerprint:
                    raise ProcurementReviewError(
                        "edited economics changed after the displayed preview"
                    )
                if (
                    preview["materiality"]["materiality_tier"] == "MATERIAL"
                    and material_edit_confirmation_id is None
                ):
                    confirmation = confirm_material_recommendation_edit(
                        conn,recommendation_id=recommendation_id,actor=actor,
                        expected_input_fingerprint=expected_input_fingerprint,
                        expected_review_preview_fingerprint=review_preview_fingerprint,
                        approved_cases=approved_cases,
                        approved_loose_units=approved_loose_units,comment=comment,
                        confirmation_reason=material_confirmation_reason,
                    )
                    return HTMLResponse(
                        _monday_review_preview_html(
                            run_id=run_id,recommendation_id=recommendation_id,
                            preview=preview,actor=actor,comment=comment,
                            material_edit_confirmation_id=confirmation[
                                "material_edit_confirmation_id"
                            ],
                        ),
                        headers={"Cache-Control": "no-store"},
                    )
            record_recommendation_review(
                conn,
                recommendation_id=recommendation_id,
                action=action,
                actor=actor,
                expected_input_fingerprint=expected_input_fingerprint,
                approved_cases=approved_cases,
                approved_loose_units=approved_loose_units,
                comment=comment,
                expected_review_preview_fingerprint=review_preview_fingerprint,
                material_edit_confirmation_id=material_edit_confirmation_id,
            )
    except ProcurementReviewError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"../../../{run_id}", status_code=303, headers={"Cache-Control": "no-store"}
    )


@app.post("/monday-runs/{run_id}/build")
def monday_run_build(
    run_id: UUID,
    actor: str = Form(...),
    draft_preview_fingerprint: str | None = Form(None),
    minimum_disposition: str | None = Form(None),
    review_token: str = _review_token_form(),
):
    _require_review_token(review_token)
    actor = _server_audit_actor(actor)
    try:
        with _db_conn() as conn:
            if not draft_preview_fingerprint:
                preview = preview_after_review(conn, run_id=str(run_id), actor=actor)
                if not preview.get("already_built"):
                    return HTMLResponse(
                        _monday_draft_preview_html(run_id=run_id, preview=preview),
                        headers={"Cache-Control": "no-store"},
                    )
            build_after_review(
                conn,
                storage=get_storage(),
                run_id=str(run_id),
                actor=actor,
                expected_preview_fingerprint=draft_preview_fingerprint,
                minimum_disposition=minimum_disposition,
            )
    except (DraftPoError, EmergencyPacketError, PoCsvError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"../{run_id}", status_code=303, headers={"Cache-Control": "no-store"}
    )


@app.get("/monday-runs/{run_id}/artifacts/{artifact_id}")
def monday_run_artifact(run_id: UUID, artifact_id: int):
    try:
        with _db_conn() as conn:
            artifact = read_monday_artifact(
                conn, storage=get_storage(), run_id=str(run_id), artifact_id=artifact_id
            )
    except EmergencyPacketError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        artifact["data"],
        media_type=artifact["content_type"],
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename={artifact['filename']}",
        },
    )


def _price_book_list_html(
    batches: list[dict],
    declared_target: dict | None = None,
    *,
    allow_upload: bool = True,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td><a href='price-books/{_html_escape(batch['price_book_batch_id'])}'>"
        f"{_html_escape(batch['batch_ref'])}</a></td>"
        f"<td>{_html_escape(batch['vendor_name'])}</td>"
        f"<td>{_html_escape(batch['target_price_state'])}</td>"
        f"<td>{_html_escape(batch['effective_from'])}</td>"
        f"<td class='price-book-durable-status'>{_html_escape(batch['status'])}</td>"
        f"<td class='price-book-operational-status' data-temporal-basis='"
        f"{_html_escape(batch.get('temporal_basis', 'HOST_CLOCK'))}'>"
        f"{_html_escape(batch['operational_status'])}</td>"
        f"<td>{_html_escape(batch['valid_row_count'])}/{_html_escape(batch['row_count'])}</td>"
        f"<td>{_html_escape(batch['error_count'])}</td>"
        "</tr>"
        for batch in batches
    ) or "<tr><td colspan='8'>No price-book batches have been staged.</td></tr>"
    declared_form = ""
    if declared_target is not None:
        if allow_upload:
            declared_action = f"""<form method='post' action='price-books/import' enctype='multipart/form-data'>
<input type='hidden' name='replacement_contract' value='{SYNTHETIC_PRICE_REPLACEMENT_CONTRACT}'>
<input type='hidden' name='expected_declaration_sha256' value='{_form_value(declared_target['declaration_sha256'])}'>
<label>Fabricated replacement CSV <input type='file' name='price_book_file' accept='.csv,text/csv' required></label><br>
<label>Operator <input name='actor' required></label><br>
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'>Stage and validate declared synthetic replacement</button>
</form>"""
        else:
            declared_action = (
                "<p id='synthetic-price-input-authority'><b>operator-staged input, "
                "browser-approved workflow</b></p>"
                "<p>Input staging is available only through the stopped-service, "
                "exact-fixture operator. Browser confirmation and APPLY remain "
                "separate authenticated actions.</p>"
            )
        declared_form = f"""<section id='synthetic-price-replacement'>
<h2>SYNTHETIC REPLACEMENT PRICE BOOK — TEST DATA / NO REAL AUTHORITY</h2>
<p>Registered target: {_html_escape(declared_target['vendor_name'])}; source validity
{_html_escape(declared_target['source_valid_from'])} through
{_html_escape(declared_target['source_valid_through'])}.</p>
{declared_action}</section>"""
    if allow_upload:
        generic_action = """<p>Uploads may prepare FUTURE pricing only. CURRENT remains untouched until a separately guarded rollover.</p>
<p><a href='price-books/template.csv'>Download strict normalized CSV template</a></p>
<form method='post' action='price-books/import' enctype='multipart/form-data'>
<label>Normalized CSV <input type='file' name='price_book_file' accept='.csv,text/csv' required></label><br>
<label>Operator <input name='actor' required></label><br>
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'>Stage and validate FUTURE</button>
</form>"""
    else:
        generic_action = (
            "<p>Bulk upload is not part of this staging surface. CURRENT remains "
            "unchanged until the separately guarded workflow completes.</p>"
        )
    return f"""<!doctype html><html><head><title>Price Books</title></head><body>
{_operational_nav('/', current='Price Books')}
<h1>Price Book Import / Validation</h1>
{generic_action}
{declared_form}
<h2>Durable batches</h2>
<table border='1' cellpadding='5'><thead><tr><th>Batch</th><th>Vendor</th><th>State</th>
<th>Effective</th><th>Durable status</th><th>Evaluated temporal status</th><th>Valid rows</th><th>Errors</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


def _price_book_detail_html(batch: dict) -> str:
    issues = "".join(
        "<tr>"
        f"<td>{_html_escape(issue['source_row_number'])}</td>"
        f"<td>{_html_escape(issue['severity'])}</td>"
        f"<td>{_html_escape(issue['issue_code'])}</td>"
        f"<td>{_html_escape(issue['message'])}</td>"
        "</tr>"
        for issue in batch["issues"]
    ) or "<tr><td colspan='4'>No validation issues.</td></tr>"
    batch_id = _html_escape(batch["price_book_batch_id"])
    declared_batch = (
        batch.get("replacement_contract")
        == SYNTHETIC_PRICE_REPLACEMENT_CONTRACT
    )
    promotable = (
        batch["status"] == "VALIDATED"
        and batch["operational_status"] == "VALIDATED"
    )
    disabled = "" if promotable else " disabled aria-disabled='true'"
    if declared_batch:
        declared_actionable = (
            batch["status"], batch["operational_status"]
        ) in {
            ("VALIDATED", "VALIDATED"),
            ("VERIFIED_FUTURE", "VERIFIED_FUTURE"),
        }
        blocker = "" if declared_actionable else (
            f"<p><b>Declared action unavailable:</b> durable status is "
            f"{_html_escape(batch['status'])}; evaluated temporal status is "
            f"{_html_escape(batch['operational_status'])}.</p>"
        )
    else:
        blocker = "" if promotable else (
            f"<p><b>Promotion unavailable:</b> operational status is "
            f"{_html_escape(batch['operational_status'])}.</p>"
        )
    warning_reason = (
        "<label>Warning review reason <input name='warning_review_reason' required></label><br>"
        if batch["warning_count"] else ""
    )
    declared_controls = ""
    if declared_batch:
        tier_rows = "".join(
            "<tr>"
            f"<td>{_html_escape(tier['source_row_number'])}</td>"
            f"<td>{_html_escape(tier['supplier_sku'])}</td>"
            f"<td>{_html_escape(tier['variant_id'])}</td>"
            f"<td>{_html_escape(tier['level_type'])}</td>"
            f"<td>{_html_escape(tier['break_quantity'])}</td>"
            f"<td>{_html_escape(tier['break_unit'])}</td>"
            f"<td>{_html_escape(tier['case_price'])}</td>"
            f"<td>{_html_escape(tier['unit_price'])}</td>"
            "</tr>"
            for tier in batch.get("tiers", [])
        ) or "<tr><td colspan='8'>No reusable candidate tiers remain.</td></tr>"
        action_form = ""
        if (
            batch["status"] == "VALIDATED"
            and batch["operational_status"] == "VALIDATED"
        ):
            action_form = f"""<h2>Separate synthetic price confirmation</h2>
<form method='post' action='../price-books/{batch_id}/confirmation-preview'>
<label>Confirmation idempotency key <input name='confirmation_idempotency_key' required></label><br>
<label>Operator <input name='actor' required></label><br>
{warning_reason}
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'>Preview exact confirmation</button></form>"""
        elif (
            batch["status"] == "VERIFIED_FUTURE"
            and batch["operational_status"] == "VERIFIED_FUTURE"
        ):
            action_form = f"""<h2>Guarded effective-boundary APPLY</h2>
<p>A launcher-bound BACKUP V2 is required; no browser path or label grants recovery authority.</p>
<form method='post' action='../price-books/{batch_id}/apply-preview'>
<label>APPLY idempotency key <input name='apply_idempotency_key' required></label><br>
<label>Operator <input name='actor' required></label><br>
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'>Preview guarded CURRENT replacement</button></form>"""
        declared_controls = f"""<section id='declared-price-evidence'>
<h2>SYNTHETIC DECLARED PRICE EVIDENCE — NO REAL AUTHORITY</h2>
<p>Policy: {_html_escape(batch.get('schedule_policy_ref'))}; declaration:
<code>{_html_escape(batch.get('declaration_sha256'))}</code>; membership:
<code>{_html_escape(batch.get('scope_membership_sha256'))}</code>.</p>
<table border='1' cellpadding='5'><thead><tr><th>Source row</th><th>SKU</th><th>Variant</th>
<th>Tier</th><th>Break qty</th><th>Break unit</th><th>Case</th><th>Unit</th></tr></thead>
<tbody>{tier_rows}</tbody></table>{action_form}</section>"""
    reject_form = ""
    if batch["status"] in {"INVALID", "VALIDATED"}:
        reject_form = f"""<h2>Reject / discard typed staging</h2>
<form method='post' action='../price-books/{batch_id}/reject'>
<input type='hidden' name='expected_validation_fingerprint' value='{_form_value(batch['validation_fingerprint'])}'>
<label>Operator <input name='actor' required></label><br>
<label>Reason <input name='reason' required></label><br>
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'>Reject and purge typed staging</button></form>"""
    legacy_promotion_form = "" if declared_controls else f"""<form method='post' action='../price-books/{batch_id}/promote'>
<input type='hidden' name='expected_validation_fingerprint' value='{_form_value(batch['validation_fingerprint'])}'>
<label>Operator <input name='actor' required></label><br>
{warning_reason}
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'{disabled}>Promote VERIFIED FUTURE pricing</button>
</form>"""
    return f"""<!doctype html><html><head><title>Price Book {_html_escape(batch['batch_ref'])}</title></head><body>
{_operational_nav('/', current='Price Books')}
<p><a href='../price-books'>Back to Price Books</a></p>
<h1>{_html_escape(batch['batch_ref'])}</h1>
<dl><dt>Vendor</dt><dd>{_html_escape(batch['vendor_name'])}</dd>
<dt>Target</dt><dd>{_html_escape(batch['target_price_state'])}</dd>
<dt>Durable status</dt><dd id='price-book-durable-status'>{_html_escape(batch['status'])}</dd>
<dt>Evaluated temporal status</dt><dd id='price-book-operational-status'
 data-temporal-basis='{_html_escape(batch.get('temporal_basis', 'HOST_CLOCK'))}'>{_html_escape(batch['operational_status'])}</dd>
<dt>Rows</dt><dd>{_html_escape(batch['valid_row_count'])}/{_html_escape(batch['row_count'])}</dd>
<dt>Coverage</dt><dd>{_html_escape(batch['covered_offer_count'])}/{_html_escape(batch['expected_offer_count'])}</dd>
<dt>Validation fingerprint</dt><dd><code>{_html_escape(batch['validation_fingerprint'])}</code></dd></dl>
<p><a href='../price-books/{batch_id}/raw.csv'>Download immutable raw evidence</a></p>
<h2>Exceptions / diagnostics</h2>
<table border='1' cellpadding='5'><thead><tr><th>Row</th><th>Severity</th><th>Code</th><th>Message</th></tr></thead>
<tbody>{issues}</tbody></table>
{blocker}
{legacy_promotion_form}{declared_controls}{reject_form}
</body></html>"""


def _declared_price_confirmation_html(
    *, batch_id: UUID, preview: dict, actor: str, warning_review_reason: str | None
) -> str:
    return f"""<!doctype html><html><head><title>Confirm Synthetic Price Book</title></head><body>
{_operational_nav('/', current='Price Books')}
<h1>SEPARATE SYNTHETIC PRICE CONFIRMATION — NO REAL AUTHORITY</h1>
<p>This preview has not written promotion, FUTURE, CURRENT, or authority state.</p>
<pre id='declared-price-confirmation-json'>{_html_escape(json.dumps(preview, sort_keys=True, indent=2, default=str))}</pre>
<form method='post' action='/price-books/{_html_escape(batch_id)}/confirm'>
<input type='hidden' name='confirmation_idempotency_key' value='{_form_value(preview['confirmation_idempotency_key'])}'>
<input type='hidden' name='expected_preview_sha256' value='{_form_value(preview['preview_sha256'])}'>
<input type='hidden' name='actor' value='{_form_value(actor)}'>
<input type='hidden' name='warning_review_reason' value='{_form_value(warning_review_reason or '')}'>
<input type='hidden' name='confirm' value='CONFIRM'>
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'>CONFIRM exact VERIFIED FUTURE price book</button>
</form></body></html>"""


def _price_apply_preview_html(
    *, batch_id: UUID, preview: dict, actor: str
) -> str:
    return f"""<!doctype html><html><head><title>Apply Synthetic Price Replacement</title></head><body>
{_operational_nav('/', current='Price Books')}
<h1>GUARDED SYNTHETIC CURRENT REPLACEMENT — NO REAL AUTHORITY</h1>
<p>The server has verified its launcher-bound BACKUP V2. This preview has not changed CURRENT.</p>
<pre id='synthetic-price-apply-json'>{_html_escape(json.dumps(preview, sort_keys=True, indent=2, default=str))}</pre>
<form method='post' action='/price-books/{_html_escape(batch_id)}/apply'>
<input type='hidden' name='apply_idempotency_key' value='{_form_value(preview['apply_idempotency_key'])}'>
<input type='hidden' name='expected_preview_sha256' value='{_form_value(preview['preview_sha256'])}'>
<input type='hidden' name='actor' value='{_form_value(actor)}'>
<input type='hidden' name='confirm' value='CONFIRM'>
<label>Price-book review token <input type='password' name='review_token' required></label><br>
<button type='submit'>CONFIRM guarded effective-boundary APPLY</button>
</form></body></html>"""


@app.get("/price-books/template.csv")
def price_book_template():
    return Response(
        normalized_price_book_template(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=normalized-price-book-template.csv"},
    )


@app.get("/price-books", response_class=HTMLResponse)
def price_books_page():
    try:
        with _db_conn() as conn:
            declared_enabled = (
                os.getenv(SYNTHETIC_PRICE_REPLACEMENT_CAPABILITY_ENV) == "1"
            )
            if declared_enabled:
                batches = list_declared_price_book_batches(conn)
                declared_target = registered_target_declaration(conn)
            else:
                batches = list_price_book_batches(conn)
                declared_target = None
    except SyntheticPriceReplacementError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _price_book_list_html(
        batches,
        declared_target,
        allow_upload=staging_request_worker_role() != "synthetic",
    )


@app.get("/price-books/{batch_id}", response_class=HTMLResponse)
def price_book_detail(batch_id: UUID):
    try:
        with _db_conn() as conn:
            has_contract_column = bool(
                conn.execute(
                    """SELECT EXISTS (
                         SELECT 1 FROM pg_catalog.pg_attribute
                          WHERE attrelid='price_book_batches'::regclass
                            AND attname='replacement_contract'
                            AND NOT attisdropped)"""
                ).fetchone()[0]
            )
            contract = None
            if has_contract_column:
                contract = conn.execute(
                    "SELECT replacement_contract FROM price_book_batches "
                    "WHERE price_book_batch_id=%s",
                    (str(batch_id),),
                ).fetchone()
            if contract is not None and contract[0] is not None:
                batch = get_declared_price_book_batch(conn, str(batch_id))
            else:
                batch = get_price_book_batch(conn, str(batch_id))
    except SyntheticPriceReplacementError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PriceBookError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _price_book_detail_html(batch)


@app.get("/price-books/{batch_id}/raw.csv")
def price_book_raw(batch_id: UUID):
    try:
        with _db_conn() as conn:
            data = read_raw_price_book(conn, get_storage(), batch_id=str(batch_id))
    except PriceBookError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=price-book-{batch_id}.csv"},
    )


@app.post("/price-books/import")
async def price_book_import(
    request: Request,
    price_book_file: UploadFile = File(...),
    actor: str = Form(...),
    review_token: str = _review_token_form(),
    replacement_contract: str | None = Form(None),
    expected_declaration_sha256: str | None = Form(None),
):
    _require_price_book_review_token(review_token)
    actor = _server_audit_actor(actor)
    data = await price_book_file.read(MAX_PRICE_BOOK_BYTES + 1)
    if len(data) > MAX_PRICE_BOOK_BYTES:
        raise HTTPException(status_code=413, detail="Price-book CSV is too large")
    try:
        with _db_conn() as conn:
            if replacement_contract is None:
                if expected_declaration_sha256 is not None:
                    raise SyntheticPriceReplacementError(
                        "declared price contract is incomplete"
                    )
                result = stage_and_validate_price_book(
                    conn, get_storage(), csv_bytes=data, actor=actor
                )
            elif replacement_contract == SYNTHETIC_PRICE_REPLACEMENT_CONTRACT:
                principal = action_principal(
                    request, "procurement.price.approve"
                )
                result = stage_and_validate_declared_price_book(
                    conn,
                    get_storage(),
                    csv_bytes=data,
                    principal=principal,
                    expected_declaration_sha256=(
                        expected_declaration_sha256 or ""
                    ),
                )
            else:
                raise SyntheticPriceReplacementError(
                    "declared price contract is unknown"
                )
    except (PriceBookError, SyntheticPriceReplacementError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(
        url=f"../price-books/{result['price_book_batch_id']}", status_code=303
    )


@app.post("/price-books/{batch_id}/promote")
def price_book_promote(
    batch_id: UUID,
    expected_validation_fingerprint: str = Form(...),
    actor: str = Form(...),
    warning_review_reason: str | None = Form(None),
    review_token: str = _review_token_form(),
):
    _require_price_book_review_token(review_token)
    actor = _server_audit_actor(actor)
    try:
        with _db_conn() as conn:
            promote_price_book_batch(
                conn,
                get_storage(),
                batch_id=str(batch_id),
                expected_validation_fingerprint=expected_validation_fingerprint,
                actor=actor,
                warning_review_reason=warning_review_reason,
            )
    except PriceBookError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"../../price-books/{batch_id}", status_code=303)


@app.post("/price-books/{batch_id}/confirmation-preview")
def declared_price_confirmation_preview(
    request: Request,
    batch_id: UUID,
    confirmation_idempotency_key: str = Form(...),
    actor: str = Form(...),
    warning_review_reason: str | None = Form(None),
    review_token: str = _review_token_form(),
):
    _require_price_book_review_token(review_token)
    actor = _server_audit_actor(actor)
    principal = action_principal(request, "procurement.price.approve")
    try:
        with _db_conn() as conn:
            preview = preview_declared_price_confirmation(
                conn,
                get_storage(),
                batch_id=str(batch_id),
                confirmation_idempotency_key=confirmation_idempotency_key,
                warning_review_reason=warning_review_reason,
                principal=principal,
            )
    except SyntheticPriceReplacementError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return HTMLResponse(
        _declared_price_confirmation_html(
            batch_id=batch_id,
            preview=preview,
            actor=actor,
            warning_review_reason=warning_review_reason,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/price-books/{batch_id}/confirm")
def declared_price_confirm(
    request: Request,
    batch_id: UUID,
    confirmation_idempotency_key: str = Form(...),
    expected_preview_sha256: str = Form(...),
    confirm: str = Form(...),
    actor: str = Form(...),
    warning_review_reason: str | None = Form(None),
    review_token: str = _review_token_form(),
):
    _require_price_book_review_token(review_token)
    _server_audit_actor(actor)
    principal = action_principal(request, "procurement.price.approve")
    try:
        with _db_conn() as conn:
            confirm_declared_price_book(
                conn,
                get_storage(),
                batch_id=str(batch_id),
                confirmation_idempotency_key=confirmation_idempotency_key,
                expected_preview_sha256=expected_preview_sha256,
                confirm=confirm,
                warning_review_reason=warning_review_reason,
                principal=principal,
            )
    except SyntheticPriceReplacementError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"../../price-books/{batch_id}", status_code=303)


@app.post("/price-books/{batch_id}/apply-preview")
def synthetic_price_apply_preview(
    request: Request,
    batch_id: UUID,
    apply_idempotency_key: str = Form(...),
    actor: str = Form(...),
    review_token: str = _review_token_form(),
):
    _require_price_book_review_token(review_token)
    actor = _server_audit_actor(actor)
    principal = action_principal(request, "procurement.price.approve")
    try:
        with _db_conn() as conn:
            preview = preview_price_replacement(
                conn,
                get_storage(),
                batch_id=str(batch_id),
                apply_idempotency_key=apply_idempotency_key,
                principal=principal,
            )
    except SyntheticPriceReplacementError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return HTMLResponse(
        _price_apply_preview_html(
            batch_id=batch_id, preview=preview, actor=actor
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/price-books/{batch_id}/apply")
def synthetic_price_apply(
    request: Request,
    batch_id: UUID,
    apply_idempotency_key: str = Form(...),
    expected_preview_sha256: str = Form(...),
    confirm: str = Form(...),
    actor: str = Form(...),
    review_token: str = _review_token_form(),
):
    _require_price_book_review_token(review_token)
    _server_audit_actor(actor)
    principal = action_principal(request, "procurement.price.approve")
    try:
        with _db_conn() as conn:
            apply_price_replacement(
                conn,
                get_storage(),
                batch_id=str(batch_id),
                apply_idempotency_key=apply_idempotency_key,
                expected_preview_sha256=expected_preview_sha256,
                confirm=confirm,
                principal=principal,
            )
    except SyntheticPriceReplacementError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"../../price-books/{batch_id}", status_code=303)


@app.post("/price-books/{batch_id}/reject")
def price_book_reject(
    batch_id: UUID,
    expected_validation_fingerprint: str = Form(...),
    actor: str = Form(...),
    reason: str = Form(...),
    review_token: str = _review_token_form(),
):
    _require_price_book_review_token(review_token)
    actor = _server_audit_actor(actor)
    try:
        with _db_conn() as conn:
            reject_price_book_batch(
                conn,
                get_storage(),
                batch_id=str(batch_id),
                expected_validation_fingerprint=expected_validation_fingerprint,
                actor=actor,
                reason=reason,
            )
    except PriceBookError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(url=f"../../price-books/{batch_id}", status_code=303)


@app.post("/pricing/rollover")
def pricing_rollover(req: RolloverRequest):
    del req
    raise HTTPException(
        status_code=503,
        detail=(
            "Price rollover is disabled until an authenticated, audited "
            "backup/completeness/rollover transaction is separately reviewed."
        ),
    )


_ACCESS_BOUNDARY = _install_access_boundary(
    app,
    local_middleware=LocalAccessMiddleware,
    expected_worker_role="synthetic",
)
