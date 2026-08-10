from __future__ import annotations

from datetime import date
import os

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .catalog import (
    approve_recreated_variant, recompute_catalog_gate, reject_recreation_candidate,
    retire_missing_variant,
)
from .config import load_rules
from .economics import qualifying_quantity, target_cost
from .health import full_health
from .matching import MatchCandidate, score_candidate
from .pricing import rollover
from .readiness import po_readiness
from . import sales as sales_service

app = FastAPI(title="Buffalo Procurement OS", version="1.3.0")


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


def _require_review_token(supplied: str | None) -> None:
    """Identity decisions are permanent; mutations are disabled unless the reviewer
    presents the shared review token (fail-closed when the token is not configured).
    The token value is never logged or echoed."""
    import hmac
    expected = os.getenv("RECONCILIATION_REVIEW_TOKEN")
    if not expected:
        raise HTTPException(status_code=503,
                            detail="Reconciliation decisions are disabled: RECONCILIATION_REVIEW_TOKEN is not configured")
    if not supplied or not hmac.compare_digest(str(supplied), expected):
        raise HTTPException(status_code=403, detail="Invalid review token")


def _db_conn():
    db = os.getenv("DATABASE_URL")
    if not db:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    import psycopg
    return psycopg.connect(db)


@app.get("/health")
def health():
    return {"ok": True, "service": "buffalo-procurement-os", "version": "1.3.0"}


@app.get("/health/full")
def health_full():
    """Component-level health: app, DB, schema, seed, Shopify creds, foundation gates."""
    return full_health()


STATUS_BADGE = {
    True: '<span style="color:#116329;background:#dafbe1;padding:2px 8px;border-radius:4px">OK</span>',
    False: '<span style="color:#82071e;background:#ffebe9;padding:2px 8px;border-radius:4px">FAIL</span>',
}


@app.get("/", response_class=HTMLResponse)
@app.get("/admin/status", response_class=HTMLResponse)
def admin_status():
    """Phase 0-2 admin status page: seed import state + readiness gates.

    Intentionally simple and read-only. PO generation is disabled and shown as such.
    """
    h = full_health()
    gates = h.get("gates", {})
    shopify = h["shopify_credentials"]

    def gate_row(name):
        g = gates.get(name, {})
        status = g.get("status", "UNKNOWN")
        color = {"PASS": "#116329", "WARN": "#7d4e00"}.get(status, "#82071e")
        return (f"<tr><td>{name}</td><td style='color:{color};font-weight:600'>{status}</td>"
                f"<td>{g.get('message', '')}</td></tr>")

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
         "" if shopify["configured"] else f"not configured (missing: {', '.join(shopify['missing_vars'])})"),
    ]
    comp = "".join(
        f"<tr><td>{name}</td><td>{STATUS_BADGE[ok]}</td><td>{note}</td></tr>"
        for name, ok, note in rows
    )
    return f"""<!doctype html><html><head><title>Buffalo Procurement OS — System Status</title>
<style>body{{font-family:system-ui,sans-serif;max-width:880px;margin:2rem auto;padding:0 1rem;color:#1f2328}}
table{{border-collapse:collapse;width:100%;margin-bottom:2rem}}td,th{{border:1px solid #d1d9e0;padding:8px 12px;text-align:left;font-size:14px}}
th{{background:#f6f8fa}}h1{{font-size:22px}}h2{{font-size:17px}}
.po{{background:#ffebe9;border:1px solid #ff8182;border-radius:6px;padding:12px 16px;font-weight:600;color:#82071e}}</style>
</head><body>
<h1>Buffalo Procurement OS — System Status</h1>
<p class="po">PO generation: DISABLED — CATALOG_SYNC and SALES_BACKFILL must PASS against production data first.</p>
<h2>Components</h2>
<table><tr><th>Component</th><th>Status</th><th>Detail</th></tr>{comp}</table>
<h2>Foundation readiness gates</h2>
<table><tr><th>Gate</th><th>Status</th><th>Message</th></tr>
{gate_row('CATALOG_SYNC')}{gate_row('SALES_BACKFILL')}</table>
<p style="color:#59636e;font-size:13px">Machine-readable: <a href="health/full">/health/full</a></p>
</body></html>"""


@app.get("/rules")
def rules():
    return load_rules()


@app.get("/foundation/status")
def foundation_status():
    db = os.getenv("DATABASE_URL")
    if not db:
        return {"database_configured": False, "po_generation_enabled": False, "reason": "DATABASE_URL is not configured"}
    import psycopg
    with psycopg.connect(db) as conn:
        result = po_readiness(conn)
    return {"database_configured": True, **result}


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
    """Reconciliation queue for the latest completed catalog sync run."""
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT catalog_sync_id,started_at,completed_at,shopify_api_version,
                              shopify_reported_variant_count,live_rows_received,exact_current_ids,
                              new_live_variants,missing_seed_variants,potential_recreations,
                              unresolved_count,source_hash,pagination_complete
                       FROM catalog_sync_runs WHERE status='COMPLETED'
                       ORDER BY completed_at DESC NULLS LAST,started_at DESC LIMIT 1""")
        run = cur.fetchone()
        if not run:
            return {"run": None, "items": []}
        q = """SELECT reconciliation_item_id,classification,seed_variant_id,live_variant_id,
                      blocking,evidence_json,resolution,resolved_by,resolved_at
               FROM catalog_reconciliation_items WHERE catalog_sync_id=%s"""
        if unresolved_only:
            q += " AND blocking=TRUE AND resolved_at IS NULL"
        q += " ORDER BY classification,reconciliation_item_id"
        cur.execute(q, (run[0],))
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
        "run": {"catalog_sync_id": str(run[0]), "started_at": str(run[1]), "completed_at": str(run[2]),
                "shopify_api_version": run[3], "shopify_reported_variant_count": run[4],
                "live_rows_received": run[5], "exact_current_ids": run[6], "new_live_variants": run[7],
                "missing_seed_variants": run[8], "potential_recreations": run[9],
                "unresolved_count": run[10], "source_hash": run[11], "pagination_complete": run[12]},
        "items": items,
    }


@app.post("/reconciliation/approve-recreation")
def approve_recreation_endpoint(req: RecreationDecision):
    _require_review_token(req.review_token)
    with _db_conn() as conn:
        try:
            approve_recreated_variant(conn, req.old_variant_id, req.new_variant_id, actor=req.actor, note=req.note)
            gate = recompute_catalog_gate(conn)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"decision": "APPROVED_RECREATION", "gate": gate}


@app.post("/reconciliation/reject-recreation")
def reject_recreation_endpoint(req: RecreationDecision):
    _require_review_token(req.review_token)
    with _db_conn() as conn:
        try:
            reject_recreation_candidate(conn, req.old_variant_id, req.new_variant_id, actor=req.actor, note=req.note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"decision": "REJECTED_KEEP_SEPARATE"}


@app.post("/reconciliation/retire")
def retire_endpoint(req: RetireDecision):
    _require_review_token(req.review_token)
    with _db_conn() as conn:
        try:
            retire_missing_variant(conn, req.variant_id, actor=req.actor, note=req.note)
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
        return "<h1>Catalog Reconciliation</h1><p>No completed catalog sync run yet.</p>"
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
<h1>Catalog Reconciliation — human review queue</h1>
<p>Run {esc(run['catalog_sync_id'])} · API {esc(run['shopify_api_version'])} · live variants {esc(run['live_rows_received'])}
(reported {esc(run['shopify_reported_variant_count'])}) · pagination complete: {esc(run['pagination_complete'])}
· snapshot {esc((run['source_hash'] or '')[:16])}…</p>
<p><b>{len(items)}</b> unresolved blocker(s). Identity decisions are permanent and audited. Nothing here writes to Shopify.</p>
{sections or '<p>No unresolved blockers.</p>'}
</body></html>"""


@app.get("/reconciliation/investigation/items")
def investigation_items():
    """Persisted identity-investigation evidence for the latest completed sync (diagnostic only)."""
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT catalog_sync_id FROM catalog_sync_runs WHERE status='COMPLETED' ORDER BY started_at DESC LIMIT 1")
            row = cur.fetchone()
            if not row:
                return {"run": None, "missing": [], "new": []}
            sync_id = str(row[0])
            cur.execute(
                """SELECT subject,variant_id,shopify_status,classification,evidence_json,heightened_review,looked_up_at
                   FROM identity_investigations WHERE catalog_sync_id=%s ORDER BY subject,variant_id""", (sync_id,))
            missing, new = [], []
            for subj, vid, status, cls, ev, hr, at in cur.fetchall():
                rec = {"variant_id": vid, "shopify_status": status, "classification": cls,
                       "evidence": ev, "heightened_review": hr, "looked_up_at": str(at)}
                (missing if subj == "MISSING_SEED" else new).append(rec)
    return {"run": sync_id, "missing": missing, "new": new}


@app.get("/reconciliation/investigation", response_class=HTMLResponse)
def investigation_page():
    """Identity Investigation report — groups the blockers with direct Shopify evidence.
    Diagnostic only: no buttons here imply approval by confidence; all decisions
    happen on /reconciliation and individually require the review token."""
    data = investigation_items()
    if not data["run"]:
        return "<h1>Identity Investigation</h1><p>No completed catalog sync run yet.</p>"

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
    return (f"<h1>Batch retirement result</h1><ul>{rows}</ul>"
            "<p><a href='../../reconciliation/investigation'>Back to investigation</a> · "
            "<a href='../../reconciliation'>Review queue</a></p>")


@app.post("/reconciliation/decide", response_class=HTMLResponse)
def reconciliation_decide(action: str = Form(...), old: str = Form(...), new: str = Form(None),
                          actor: str = Form(...), note: str = Form(""), review_token: str = Form("")):
    _require_review_token(review_token)
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

        cards += f"""<section class='item'>
<header><div><h2>{esc(product_title)} — {esc(variant_title)}</h2>
<p class='identity'>Source Variant ID {esc(source_variant_id)} · historical SKU {esc(sku)}</p></div>
<div><span class='status'>{esc(status)}</span><span class='materiality'>{esc(materiality)}</span></div></header>
<table><tr><th>First sale</th><th>Last sale</th><th>Raw rows</th><th>Net units</th><th>Absolute units</th><th>Net sales</th></tr>
<tr><td>{esc(first_sale)}</td><td>{esc(last_sale)}</td><td>{esc(raw_rows)}</td><td>{esc(net_units)}</td><td>{esc(absolute_units)}</td><td>{esc(net_sales)}</td></tr></table>
<div class='evidence-grid'><div><h3>Source evidence</h3><pre>{evidence(supporting)}</pre></div>
<div class='conflict'><h3>Conflicts</h3><pre>{evidence(conflicts)}</pre></div></div>
<h3>Candidate canonical variants</h3>{candidates_view}
<p class='muted'>Candidates are evidence only. No mapping is pre-approved or pre-selected.</p>
<div class='actions'>
<form method='post' action='review/decide'>
<input type='hidden' name='source_key' value='{esc(source_key)}'><input type='hidden' name='action' value='MAP_TO_CANONICAL'>
<label>Canonical Variant ID <input name='canonical_variant_id' list='{datalist_id}' placeholder='enter exact Variant ID' required></label>
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
.actions{{display:grid;gap:10px;margin-top:14px}}form{{border-top:1px solid #d1d9e0;padding-top:10px;display:flex;gap:8px;align-items:end;flex-wrap:wrap}}label{{display:flex;flex-direction:column;gap:3px;font-size:12px}}input{{padding:6px;font-size:13px;min-width:150px}}button{{padding:7px 11px;border:1px solid;border-radius:5px;font-size:12px;font-weight:700;cursor:pointer}}
.map{{background:#dafbe1;color:#116329}}.exclude{{background:#ffebe9;color:#82071e}}.leave{{background:#f6f8fa;color:#1f2328}}@media(max-width:760px){{.evidence-grid{{grid-template-columns:1fr}}header{{display:block}}}}</style>
</head><body><h1>Historical ShopifyQL Sales — identity review</h1>
<p><b>{len(items)}</b> unresolved or ambiguous historical source identity group(s), ranked by materiality. Daily facts are grouped so each decision covers the complete historical source identity. Nothing on this page writes to Shopify.</p>
<p class='muted'>Mapping and exclusion decisions are permanent, audited, and require a reviewer, reason, and review token. Leaving an item unresolved keeps SALES_BACKFILL failed.</p>
{cards or '<p>No unresolved or ambiguous historical source identities require review.</p>'}
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
    actor = str(actor).strip()
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


@app.post("/pricing/rollover")
def pricing_rollover(req: RolloverRequest):
    db = os.getenv("DATABASE_URL")
    if not db:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    import psycopg
    try:
        with psycopg.connect(db) as conn:
            return rollover(conn, req.as_of)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
