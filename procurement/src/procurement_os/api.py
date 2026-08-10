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


class RetireDecision(BaseModel):
    variant_id: str
    actor: str
    note: str


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
    with _db_conn() as conn:
        try:
            approve_recreated_variant(conn, req.old_variant_id, req.new_variant_id, actor=req.actor, note=req.note)
            gate = recompute_catalog_gate(conn)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"decision": "APPROVED_RECREATION", "gate": gate}


@app.post("/reconciliation/reject-recreation")
def reject_recreation_endpoint(req: RecreationDecision):
    with _db_conn() as conn:
        try:
            reject_recreation_candidate(conn, req.old_variant_id, req.new_variant_id, actor=req.actor, note=req.note)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return {"decision": "REJECTED_KEEP_SEPARATE"}


@app.post("/reconciliation/retire")
def retire_endpoint(req: RetireDecision):
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
<button class='ok'>APPROVE RECREATION</button></form>
<form method='post' action='decide'><input type=hidden name=action value=reject>
<input type=hidden name=old value='{esc(old.get("variant_id"))}'><input type=hidden name=new value='{esc(c.get("new_variant_id"))}'>
<input name=actor placeholder='your name' required><input name=note placeholder='why separate' required>
<button class='bad'>REJECT / KEEP SEPARATE</button></form>
</div>"""
        rows += f"""<div class='actions'>
<form method='post' action='decide'><input type=hidden name=action value=retire>
<input type=hidden name=old value='{esc(old.get("variant_id"))}'>
<input name=actor placeholder='your name' required><input name=note placeholder='retirement note' required>
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


@app.post("/reconciliation/decide", response_class=HTMLResponse)
def reconciliation_decide(action: str = Form(...), old: str = Form(...), new: str = Form(None),
                          actor: str = Form(...), note: str = Form("")):
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
