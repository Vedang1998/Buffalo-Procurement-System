from __future__ import annotations

from datetime import date
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import load_rules
from .economics import qualifying_quantity, target_cost
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
