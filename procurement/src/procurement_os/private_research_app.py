"""Authenticated, read-only UI for a sealed private research workspace.

This composition root deliberately does not import the operational procurement
API.  It exposes only authentication, health, and immutable research artifacts.
"""
from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .local_access import (
    LocalAccessMiddleware,
    SESSION_COOKIE,
    create_local_session,
    destroy_local_session,
    runtime_config,
)
from .private_research import PrivateResearchError, read_private_research_workspace
from .private_research_projection import (
    PrivateResearchProjectionError,
    filter_private_research_rows,
)


app = FastAPI(
    title="Buffalo private research review",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(LocalAccessMiddleware)

_CACHED_WORKSPACE_PATH: str | None = None
_CACHED_WORKSPACE: dict[str, object] | None = None


def _workspace() -> dict[str, object]:
    global _CACHED_WORKSPACE_PATH, _CACHED_WORKSPACE
    raw = os.getenv("BUFFALO_PRIVATE_RESEARCH_WORKSPACE", "").strip()
    if not raw:
        raise HTTPException(status_code=503, detail="Private research workspace is absent")
    if _CACHED_WORKSPACE_PATH == raw and _CACHED_WORKSPACE is not None:
        return _CACHED_WORKSPACE
    try:
        loaded = read_private_research_workspace(Path(raw))
    except PrivateResearchError as exc:
        raise HTTPException(status_code=503, detail="Private research workspace is invalid") from exc
    _CACHED_WORKSPACE_PATH = raw
    _CACHED_WORKSPACE = loaded
    return loaded


@app.on_event("startup")
def validate_workspace_at_startup() -> None:
    """Perform the expensive immutable replay once before readiness is served."""

    _workspace()


@app.get("/health")
def health() -> dict[str, object]:
    configured = runtime_config().mode == "PRIVATE_REAL_SOURCE_REVIEW"
    if not configured:
        raise HTTPException(status_code=503, detail="Private review mode is unavailable")
    workspace = _workspace()
    manifest = workspace.get("manifest")
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=503, detail="Private manifest is absent")
    return {
        "ok": True,
        "service": "buffalo-private-research-review",
        "mode": runtime_config().mode,
        "operational_authority": False,
    }


@app.get("/auth/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    config = runtime_config()
    available = config.mode == "PRIVATE_REAL_SOURCE_REVIEW"
    form = (
        "<form method='post'><label>Private local access secret "
        "<input type='password' name='secret' autocomplete='current-password' required>"
        "</label><button type='submit'>Sign in</button></form>"
        if available
        else "<p>Private review authentication is unavailable.</p>"
    )
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Buffalo private research sign in</title></head><body>"
        "<h1>Buffalo private research — local sign in</h1>"
        f"<p>Mode: {html.escape(config.mode)}</p>{form}</body></html>"
    )


@app.post("/auth/login")
def login(secret: str = Form(...)) -> RedirectResponse:
    if runtime_config().mode != "PRIVATE_REAL_SOURCE_REVIEW":
        raise HTTPException(status_code=503, detail="Private review mode is unavailable")
    try:
        token, _ = create_local_session(secret)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Local sign-in failed") from exc
    response = RedirectResponse(url="/private-research", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=8 * 60 * 60,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/auth/logout")
def logout(request: Request) -> RedirectResponse:
    destroy_local_session(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse(url="/auth/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/private-research", status_code=303)


def _stockout_evidence_status(row: dict[str, object]) -> str:
    forecast = row.get("forecast")
    reasons = forecast.get("reason_codes", []) if isinstance(forecast, dict) else []
    if any(
        isinstance(reason, str)
        and (
            "FORECAST_EVIDENCE" in reason
            or "INVENTORY_STATE" in reason
            or "AVAILABILITY" in reason
        )
        for reason in reasons
    ):
        return "NOT_CAPTURED"
    if isinstance(forecast, dict) and forecast.get("status") == "CALCULATED_RESEARCH_ONLY":
        return "CAPTURED_IN_RESEARCH_INPUT"
    return "INCOMPLETE_OR_UNKNOWN"


def _json_html(value: object) -> str:
    return html.escape(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
        quote=True,
    )


@app.get("/private-research", response_class=HTMLResponse)
def private_research_index(
    q: str = "",
    vendor: str = "",
    status: str = "",
    stockout: str = "",
    page: int = 1,
) -> HTMLResponse:
    workspace = _workspace()
    projection = workspace.get("projection")
    manifest = workspace.get("manifest")
    if not isinstance(projection, dict) or not isinstance(manifest, dict):
        raise HTTPException(status_code=503, detail="Private projection is absent")
    if any(len(value) > 200 for value in (q, vendor, status, stockout)):
        raise HTTPException(status_code=422, detail="Private research filter is too long")
    if stockout not in {"", "NOT_CAPTURED", "CAPTURED_IN_RESEARCH_INPUT", "INCOMPLETE_OR_UNKNOWN"}:
        raise HTTPException(status_code=422, detail="Stockout evidence filter differs")
    try:
        rows = filter_private_research_rows(
            projection, query=q, vendor=vendor, status=status
        )
    except PrivateResearchProjectionError as exc:
        raise HTTPException(status_code=503, detail="Private projection is invalid") from exc
    if stockout:
        rows = [row for row in rows if _stockout_evidence_status(row) == stockout]
    page_size = 50
    total_pages = max(1, math.ceil(len(rows) / page_size))
    if page < 1 or page > total_pages:
        raise HTTPException(status_code=404, detail="Private research page is absent")
    selected = rows[(page - 1) * page_size : page * page_size]
    vendor_names = sorted(
        {
            str(item)
            for item in projection.get("vendor_names", [])
            if isinstance(item, str) and item
        }
    )
    options = ["<option value=''>All named suppliers</option>"] + [
        f"<option value='{html.escape(name, quote=True)}'"
        f"{' selected' if name == vendor else ''}>{html.escape(name)}</option>"
        for name in vendor_names
    ]
    cards = []
    for row in selected:
        variant_id = html.escape(str(row.get("shopify_variant_id", "")))
        supplier = html.escape(str(row.get("supplier_name") or "No named supplier"))
        title = html.escape(
            " — ".join(
                value
                for value in (
                    str(row.get("product_title") or ""),
                    str(row.get("variant_title") or ""),
                )
                if value
            )
        )
        reasons = row.get("missing_data_reasons", [])
        cards.append(
            "<article class='record' data-variant-id='"
            + variant_id
            + "'><h2>Variant "
            + variant_id
            + " — "
            + title
            + "</h2><p><strong>Named hypothesis:</strong> "
            + supplier
            + " · <strong>Join:</strong> "
            + html.escape(str(row.get("join_status", "")))
            + " · <strong>Stockout evidence:</strong> "
            + _stockout_evidence_status(row)
            + "</p><p><strong>Missing/pending:</strong> "
            + html.escape(", ".join(str(item) for item in reasons) or "none")
            + "</p><details><summary>Evidence and research diagnostics</summary><pre>"
            + _json_html(row)
            + "</pre></details></article>"
        )
    base_params = {"q": q, "vendor": vendor, "status": status, "stockout": stockout}
    navigation = []
    if page > 1:
        navigation.append(
            f"<a href='/private-research?{html.escape(urlencode({**base_params, 'page': page - 1}), quote=True)}'>Previous</a>"
        )
    if page < total_pages:
        navigation.append(
            f"<a href='/private-research?{html.escape(urlencode({**base_params, 'page': page + 1}), quote=True)}'>Next</a>"
        )
    declared = projection.get("declared_coverage", {})
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='robots' content='noindex,nofollow'><title>Private real-source research</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:90rem;margin:2rem auto;padding:0 1rem}"
        ".warning{border:3px solid #8b1e1e;background:#fff4f4;padding:1rem;font-weight:700}"
        ".badges span{display:inline-block;border:1px solid #555;padding:.25rem .5rem;margin:.2rem}"
        "form{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));gap:.6rem;align-items:end}"
        "label{display:grid;gap:.2rem}.record{border-top:1px solid #aaa;padding:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}"
        "nav{display:flex;gap:1rem}</style></head><body>"
        "<h1>Private real-source procurement research</h1>"
        "<p class='warning'>REVIEW ONLY · UNAPPROVED · ZERO OPERATIONAL AUTHORITY. "
        "No mapping, price, selection, forecast policy, DRAFT, PO, Shopify write, supplier contact, or order is authorized.</p>"
        "<p class='badges'><span>PRIVATE_REAL_SOURCE_REVIEW</span><span>immutable workspace</span>"
        f"<span>projection {html.escape(str(manifest.get('projection_sha256', '')))}</span></p>"
        "<details><summary>Source and scope coverage</summary><pre>"
        + _json_html(declared)
        + "</pre></details>"
        "<p><a href='/private-research/artifacts/owner-preview.html'>Offline HTML preview</a> · "
        "<a href='/private-research/artifacts/owner-worksheet.csv'>Owner worksheet CSV</a> · "
        "<a href='/private-research/artifacts/coverage.json'>Coverage JSON</a> · "
        "<a href='/private-research/artifacts/projection.json'>Projection JSON</a></p>"
        "<form method='get' action='/private-research'>"
        f"<label>Search<input name='q' value='{html.escape(q, quote=True)}'></label>"
        f"<label>Named supplier<select name='vendor'>{''.join(options)}</select></label>"
        f"<label>Status or reason<input name='status' value='{html.escape(status, quote=True)}'></label>"
        "<label>Stockout evidence<select name='stockout'>"
        + "".join(
            f"<option value='{item}'{' selected' if item == stockout else ''}>{label}</option>"
            for item, label in (
                ("", "Any"),
                ("NOT_CAPTURED", "Not captured"),
                ("CAPTURED_IN_RESEARCH_INPUT", "Captured in research input"),
                ("INCOMPLETE_OR_UNKNOWN", "Incomplete or unknown"),
            )
        )
        + "</select></label><button type='submit'>Filter</button></form>"
        f"<p>Showing {len(selected)} of {len(rows)} matched rows · page {page} of {total_pages}.</p>"
        + "".join(cards)
        + f"<nav>{' · '.join(navigation)}</nav>"
        "<form method='post' action='/auth/logout'><button type='submit'>Sign out</button></form>"
        "</body></html>"
    )


@app.get("/private-research/manifest")
def private_research_manifest() -> JSONResponse:
    workspace = _workspace()
    manifest = workspace.get("manifest")
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=503, detail="Private manifest is absent")
    return JSONResponse(manifest)


@app.get("/private-research/projection")
def private_research_projection() -> JSONResponse:
    workspace = _workspace()
    projection = workspace.get("projection")
    if not isinstance(projection, dict):
        raise HTTPException(status_code=503, detail="Private projection is absent")
    return JSONResponse(projection)


@app.get("/private-research/artifacts/{artifact_name}")
def private_research_artifact(artifact_name: str) -> Response:
    media_types = {
        "owner-preview.html": "text/html; charset=utf-8",
        "owner-worksheet.csv": "text/csv; charset=utf-8",
        "projection.json": "application/json",
        "coverage.json": "application/json",
    }
    if artifact_name not in media_types:
        raise HTTPException(status_code=404, detail="Private artifact is not published")
    artifacts = _workspace().get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get(artifact_name), bytes):
        raise HTTPException(status_code=404, detail="Private artifact is absent")
    return Response(
        artifacts[artifact_name],
        media_type=media_types[artifact_name],
        headers={"Content-Disposition": f'attachment; filename="{artifact_name}"'},
    )
