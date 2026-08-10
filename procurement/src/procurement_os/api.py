from __future__ import annotations

from datetime import date
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .config import load_rules
from .economics import qualifying_quantity, target_cost
from .health import full_health
from .matching import MatchCandidate, score_candidate
from .pricing import rollover
from .readiness import po_readiness

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
