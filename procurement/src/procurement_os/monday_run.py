"""Two explicit Monday workflow boundaries: prepare, then post-review build."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .draft_po import build_vendor_drafts, preview_vendor_drafts
from .emergency_packet import build_emergency_review_packet
from .recommendations import prepare_monday_run
from .storage import StorageAdapter


def prepare(
    conn: Any, *, business_date: date, idempotency_key: str,
    variant_ids: Iterable[str], actor: str,
) -> dict[str, Any]:
    """Prepare recommendations and stop at AWAITING_REVIEW."""
    return prepare_monday_run(
        conn,business_date=business_date,idempotency_key=idempotency_key,
        variant_ids=variant_ids,actor=actor,
    )


def build_after_review(
    conn: Any,
    *,
    storage: StorageAdapter,
    run_id: str,
    actor: str,
    expected_preview_fingerprint: str | None = None,
    minimum_disposition: str | None = None,
) -> dict[str, Any]:
    """Require completed human decisions, then build DRAFTs and internal packet."""
    drafts = build_vendor_drafts(
        conn,
        run_id=run_id,
        actor=actor,
        expected_preview_fingerprint=expected_preview_fingerprint,
        minimum_disposition=minimum_disposition,
    )
    packet = build_emergency_review_packet(conn,storage=storage,run_id=run_id,actor=actor)
    return {"drafts": drafts,"packet": packet,"release_performed": False,"shopify_calls": 0}


def preview_after_review(
    conn: Any, *, run_id: str, actor: str
) -> dict[str, Any]:
    """Preview exact vendor DRAFT economics without building any DRAFT or artifact."""

    return preview_vendor_drafts(conn, run_id=run_id, actor=actor)
