"""Deterministic internal DRAFT CSVs; not a validated Shopify import format."""

from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import io
from typing import Any

from .draft_po import SAFETY_LABEL, get_vendor_drafts
from .storage import StorageAdapter


FORMAT_WARNING = "SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED"
CSV_HEADERS = (
    "safety_label","format_status","run_id","draft_po_id","vendor_name","variant_id",
    "supplier_sku","cases","loose_units","ordered_units","unit_cost","case_price",
    "line_merchandise_total","line_loose_order_fee","line_total",
    "vendor_merchandise_total","vendor_loose_order_fee_total",
    "vendor_below_minimum_fee","vendor_delivery_fee","vendor_po_total",
    "below_vendor_minimum","minimum_shortfall","minimum_disposition",
    "economics_confirmed_by","draft_preview_fingerprint",
)


class PoCsvError(ValueError):
    pass


def _cell(value: Any) -> str:
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def render_vendor_draft_csv(run_id: str, draft: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS, lineterminator="\n")
    writer.writeheader()
    for line in sorted(draft["lines"], key=lambda item: (item["variant_id"], item["po_line_id"])):
        writer.writerow(
            {
                "safety_label": SAFETY_LABEL,
                "format_status": FORMAT_WARNING,
                "run_id": _cell(run_id),
                "draft_po_id": _cell(draft["po_id"]),
                "vendor_name": _cell(draft["vendor_name"]),
                "variant_id": _cell(line["variant_id"]),
                "supplier_sku": _cell(line["supplier_sku"]),
                "cases": line["cases"],
                "loose_units": line["loose_units"],
                "ordered_units": line["ordered_units"],
                "unit_cost": f"{Decimal(line['unit_cost']):.4f}",
                "case_price": (
                    f"{Decimal(line['case_price']):.4f}"
                    if line.get("case_price") is not None
                    else ""
                ),
                "line_merchandise_total": f"{Decimal(line['merchandise_total']):.2f}",
                "line_loose_order_fee": f"{Decimal(line['loose_order_fee']):.2f}",
                "line_total": f"{Decimal(line['line_total']):.2f}",
                "vendor_merchandise_total": f"{Decimal(draft['merchandise_total']):.2f}",
                "vendor_loose_order_fee_total": (
                    f"{Decimal(draft.get('loose_order_fee_total') or 0):.2f}"
                ),
                "vendor_below_minimum_fee": (
                    f"{Decimal(draft.get('below_minimum_fee') or 0):.2f}"
                ),
                "vendor_delivery_fee": f"{Decimal(draft['delivery_fee']):.2f}",
                "vendor_po_total": f"{Decimal(draft['po_total']):.2f}",
                "below_vendor_minimum": str(bool(draft["below_vendor_minimum"])).upper(),
                "minimum_shortfall": f"{Decimal(draft.get('minimum_shortfall') or 0):.2f}",
                "minimum_disposition": _cell(draft.get("minimum_disposition") or ""),
                "economics_confirmed_by": _cell(draft.get("economics_confirmed_by") or ""),
                "draft_preview_fingerprint": _cell(
                    draft.get("draft_preview_fingerprint") or ""
                ),
            }
        )
    return output.getvalue().encode("utf-8")


def write_vendor_draft_csvs(
    conn: Any, *, storage: StorageAdapter, run_id: str, actor: str
) -> list[dict[str, Any]]:
    operator = str(actor).strip()
    if not operator:
        raise PoCsvError("CSV actor is required")
    snapshot = get_vendor_drafts(conn, run_id)
    if snapshot["workflow_stage"] not in {"DRAFTS_BUILT", "PACKET_BUILT"}:
        raise PoCsvError("vendor CSVs require built DRAFTs")
    written: list[dict[str, Any]] = []
    for draft in snapshot["drafts"]:
        payload = render_vendor_draft_csv(run_id, draft)
        digest = hashlib.sha256(payload).hexdigest()
        key = f"monday-runs/{run_id}/vendor-{draft['vendor_id']}/{digest}.internal.csv"
        if storage.exists(key):
            if storage.get_bytes(key) != payload:
                raise PoCsvError("stored vendor CSV hash mismatch")
        else:
            storage.put_bytes(key, payload)
            if storage.get_bytes(key) != payload:
                raise PoCsvError("vendor CSV read-back hash mismatch")
        written.append(
            {
                "artifact_type": "VENDOR_INTERNAL_CSV", "vendor_id": draft["vendor_id"],
                "storage_key": key,"sha256": digest,"size_bytes": len(payload),
                "content_type": "text/csv", "data": payload,
            }
        )
    return written
