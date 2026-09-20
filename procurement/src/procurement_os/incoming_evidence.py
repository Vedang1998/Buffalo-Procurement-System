"""Dependency-light predicates for frozen incoming inventory evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def trusted_incoming_state(
    *,
    reconciliation: str,
    line_status: str,
    import_status: str,
    expected_receipt_at: datetime | None,
    evaluated_at: datetime,
    reconciled_at: datetime | None,
    reconciled_by: str | None,
    evidence: dict[str, Any] | None,
) -> bool:
    """Return whether one frozen PO line is admissible as trusted incoming."""

    return (
        reconciliation == "OPEN"
        and line_status in {"ORDERED", "PARTIALLY_RECEIVED", "PARTIALLY_CANCELLED"}
        and import_status == "IMPORTED"
        and expected_receipt_at is not None
        and expected_receipt_at >= evaluated_at
        and reconciled_at is not None
        and reconciled_at <= evaluated_at
        and reconciled_by is not None
        and bool(str(reconciled_by).strip())
        and isinstance(evidence, dict)
        and bool(evidence)
        and isinstance(evidence.get("source"), str)
        and bool(evidence["source"].strip())
        and isinstance(evidence.get("reference"), str)
        and bool(evidence["reference"].strip())
    )
