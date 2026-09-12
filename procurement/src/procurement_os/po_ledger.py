"""Procurement run/PO ledger and conservative open-incoming reconciliation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any


PO_LEDGER_LOCK = 5_920_230_301
RECONCILIATION_CLOCK_TOLERANCE_SECONDS = 5
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FINALIZATION_APPLICABLE_GATES = frozenset(
    {
        "INVENTORY_HISTORY",
        "MAPPING_INTEGRITY",
        "OPEN_PO_RECONCILIATION",
        "PRICE_COVERAGE",
        "VENDOR_RULES",
    }
)


class ProcurementLedgerError(ValueError):
    pass


def _fingerprint(value: str) -> str:
    cleaned = str(value)
    if cleaned != cleaned.strip() or not SHA256.fullmatch(cleaned):
        raise ProcurementLedgerError("input fingerprint must be a lowercase SHA-256")
    return cleaned


def _nonnegative(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ProcurementLedgerError(f"{field} must be a nonnegative decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProcurementLedgerError(f"{field} must be a nonnegative decimal") from exc
    if not result.is_finite() or result < 0 or max(0, -result.as_tuple().exponent) > 4:
        raise ProcurementLedgerError(
            f"{field} must be nonnegative with at most four decimal places"
        )
    return result


def _transaction_lock(conn: Any) -> None:
    locked = conn.execute(
        "SELECT pg_try_advisory_xact_lock(%s)", (PO_LEDGER_LOCK,)
    ).fetchone()[0]
    if not locked:
        raise RuntimeError("Procurement PO ledger transaction lock is unavailable")


def _validated_as_of(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ProcurementLedgerError("as_of must be timezone-aware")
    return result


def _trusted_incoming_state(
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


def create_procurement_run(
    conn: Any,
    *,
    business_date: date,
    idempotency_key: str,
    input_fingerprint: str,
    source_data_through: datetime | None = None,
) -> dict[str, Any]:
    key = str(idempotency_key).strip()
    if not key:
        raise ProcurementLedgerError("run idempotency_key is required")
    fingerprint = _fingerprint(input_fingerprint)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        existing = conn.execute(
            """SELECT run_id,business_date,input_fingerprint,status,workflow_stage,
                      source_data_through
               FROM runs
               WHERE run_type='MONDAY_PROCUREMENT' AND idempotency_key=%s
               FOR UPDATE""",
            (key,),
        ).fetchone()
        if existing is not None:
            if (
                existing[1] != business_date
                or existing[2] != fingerprint
                or existing[5] != source_data_through
            ):
                raise ProcurementLedgerError(
                    "run idempotency key already exists with different frozen inputs"
                )
            return {
                "run_id": str(existing[0]),
                "business_date": existing[1].isoformat(),
                "status": existing[3],
                "workflow_stage": existing[4],
                "idempotent_replay": True,
            }
        row = conn.execute(
            """INSERT INTO runs(
                   run_type,status,source_data_through,business_date,
                   idempotency_key,input_fingerprint,workflow_stage
               ) VALUES ('MONDAY_PROCUREMENT','RUNNING',%s,%s,%s,%s,'PREPARING')
               RETURNING run_id,status,workflow_stage""",
            (source_data_through, business_date, key, fingerprint),
        ).fetchone()
    return {
        "run_id": str(row[0]),
        "business_date": business_date.isoformat(),
        "status": row[1],
        "workflow_stage": row[2],
        "idempotent_replay": False,
    }


def ensure_draft_po(
    conn: Any,
    *,
    run_id: str,
    vendor_id: str,
    input_fingerprint: str,
    expected_receipt_at: datetime | None = None,
) -> dict[str, Any]:
    fingerprint = _fingerprint(input_fingerprint)
    if expected_receipt_at is not None:
        expected_receipt_at = _validated_as_of(expected_receipt_at)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        run = conn.execute(
            """SELECT run_type,status,workflow_stage,input_fingerprint
               FROM runs WHERE run_id=%s FOR UPDATE""",
            (run_id,),
        ).fetchone()
        if run is None or run[0] != "MONDAY_PROCUREMENT":
            raise ProcurementLedgerError("draft PO requires a Procurement run")
        if run[3] != fingerprint:
            raise ProcurementLedgerError(
                "draft PO fingerprint must match the frozen Procurement run"
            )
        vendor = conn.execute(
            "SELECT active FROM vendors WHERE vendor_id=%s", (vendor_id,)
        ).fetchone()
        if vendor is None or not vendor[0]:
            raise ProcurementLedgerError("draft PO requires an active vendor")
        existing = conn.execute(
            """SELECT po_id,po_status,input_fingerprint,po_revision,
                      expected_receipt_at
               FROM purchase_orders WHERE run_id=%s AND vendor_id=%s FOR UPDATE""",
            (run_id, vendor_id),
        ).fetchone()
        if existing is not None:
            if run[1] != "RUNNING" or run[2] not in {
                "REVIEWED",
                "DRAFTS_BUILT",
                "PACKET_BUILT",
            }:
                raise ProcurementLedgerError(
                    "existing DRAFT is unavailable outside its reviewed run lifecycle"
                )
            if existing[1] != "DRAFT" or existing[2] != fingerprint:
                raise ProcurementLedgerError(
                    "existing vendor PO is not the identical mutable DRAFT"
                )
            if existing[4] != expected_receipt_at:
                raise ProcurementLedgerError(
                    "existing vendor DRAFT has a different expected receipt"
                )
            return {
                "po_id": str(existing[0]),
                "po_status": existing[1],
                "po_revision": int(existing[3]),
                "idempotent_replay": True,
            }
        if run[1] != "RUNNING" or run[2] != "REVIEWED":
            raise ProcurementLedgerError(
                "draft PO requires an active run that completed human review"
            )
        row = conn.execute(
            """INSERT INTO purchase_orders(
                   run_id,vendor_id,po_status,input_fingerprint,
                   expected_receipt_at,receipt_status,shopify_import_status
               ) VALUES (%s,%s,'DRAFT',%s,%s,'UNKNOWN','NOT_IMPORTED')
               RETURNING po_id,po_status,po_revision""",
            (run_id, vendor_id, fingerprint, expected_receipt_at),
        ).fetchone()
    return {
        "po_id": str(row[0]),
        "po_status": row[1],
        "po_revision": int(row[2]),
        "idempotent_replay": False,
    }


def record_po_import_status(
    conn: Any,
    *,
    po_id: str,
    status: str,
    reference: str | None,
    evidence: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Record post-final export/import state without performing a Shopify call."""

    target_status = str(status).strip().upper()
    target_reference = None if reference is None else str(reference).strip() or None
    if not isinstance(actor, str):
        raise ProcurementLedgerError("actor and import-status evidence are required")
    clean_actor = actor.strip()
    if target_status not in {"EXPORTED", "IMPORTED", "FAILED"}:
        raise ProcurementLedgerError("unsupported Procurement PO import status")
    if target_status in {"EXPORTED", "IMPORTED"} and target_reference is None:
        raise ProcurementLedgerError("exported/imported PO requires an external reference")
    if not clean_actor or not isinstance(evidence, dict) or not evidence:
        raise ProcurementLedgerError("actor and import-status evidence are required")
    transitions = {
        "NOT_IMPORTED": {"EXPORTED", "IMPORTED", "FAILED"},
        "EXPORTED": {"IMPORTED", "FAILED"},
        "FAILED": {"EXPORTED", "IMPORTED", "FAILED"},
        "IMPORTED": set(),
    }
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        current = conn.execute(
            """SELECT po_status,shopify_import_status,shopify_po_reference
                 FROM purchase_orders WHERE po_id=%s FOR UPDATE""",
            (po_id,),
        ).fetchone()
        if current is None:
            raise ProcurementLedgerError("unknown Procurement PO")
        if current[0] != "FINAL":
            raise ProcurementLedgerError("import status requires a FINAL Procurement PO")
        if current[1] == target_status and current[2] == target_reference:
            return {
                "po_id": str(po_id),
                "status": target_status,
                "reference": target_reference,
                "idempotent_replay": True,
            }
        if target_status not in transitions.get(current[1], set()):
            raise ProcurementLedgerError("invalid post-final import-status transition")
        conn.execute(
            """INSERT INTO po_operational_events(
                   po_id,event_type,prior_status,new_status,prior_reference,
                   new_reference,evidence_json,recorded_by
               ) VALUES (%s,'SHOPIFY_IMPORT_STATUS',%s,%s,%s,%s,%s::jsonb,%s)""",
            (
                po_id,
                current[1],
                target_status,
                current[2],
                target_reference,
                json.dumps(evidence, sort_keys=True),
                clean_actor,
            ),
        )
        conn.execute(
            """UPDATE purchase_orders
                  SET shopify_import_status=%s,shopify_po_reference=%s
                WHERE po_id=%s""",
            (target_status, target_reference, po_id),
        )
        _persist_open_po_gates(conn, evaluate_open_po_reconciliation(conn))
    return {
        "po_id": str(po_id),
        "status": target_status,
        "reference": target_reference,
        "idempotent_replay": False,
    }


def finalize_reviewed_po(
    conn: Any,
    *,
    po_id: str,
    actor: str,
    finalized_at: datetime | None = None,
) -> dict[str, Any]:
    """Finalize one reviewed PO only from canonical, affected-scope readiness.

    This is a backend service boundary only. It does not release, export, or
    transmit a PO and is intentionally not exposed by an API/CLI in the MVP.
    """

    from .readiness import po_readiness

    if not isinstance(actor, str) or not actor.strip():
        raise ProcurementLedgerError("finalization actor is required")
    clean_actor = actor.strip()
    completed_at = _validated_as_of(finalized_at)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        po = conn.execute(
            """SELECT p.po_status,p.run_id,p.vendor_id,p.input_fingerprint,
                      p.po_revision,p.reviewed_by
                 FROM purchase_orders p
                WHERE p.po_id=%s FOR UPDATE""",
            (po_id,),
        ).fetchone()
        if po is None:
            raise ProcurementLedgerError("unknown Procurement PO")
        if po[0] != "REVIEW":
            raise ProcurementLedgerError("finalization requires a reviewed Procurement PO")
        variants = [
            str(row[0])
            for row in conn.execute(
                """SELECT variant_id FROM purchase_order_lines
                    WHERE po_id=%s ORDER BY variant_id,po_line_id""",
                (po_id,),
            ).fetchall()
        ]
        if not variants:
            raise ProcurementLedgerError("finalization requires reviewed PO lines")
        _persist_open_po_gates(
            conn,
            evaluate_open_po_reconciliation(conn, as_of=completed_at),
        )
        evaluations = []
        blockers = []
        for variant_id in variants:
            result = po_readiness(
                conn,
                vendor_id=str(po[2]),
                variant_id=variant_id,
                run_id=str(po[1]),
                applicable_gate_names=FINALIZATION_APPLICABLE_GATES,
            )
            evaluations.append(
                {
                    "variant_id": variant_id,
                    "applicable_gate_names": result["applicable_gate_names"],
                    "po_generation_enabled": result["po_generation_enabled"],
                }
            )
            blockers.extend(result["blockers"])
        if blockers:
            reasons = sorted(
                {
                    str(blocker.get("detail", {}).get("gate_name") or blocker["type"])
                    for blocker in blockers
                }
            )
            raise ProcurementLedgerError(
                "canonical PO readiness blocks finalization: " + ", ".join(reasons)
            )
        evidence = {
            "source": "CANONICAL_PO_READINESS",
            "input_fingerprint": po[3],
            "po_revision": str(po[4]),
            "run_id": str(po[1]),
            "vendor_id": str(po[2]),
            "evaluations": evaluations,
        }
        conn.execute(
            """INSERT INTO po_operational_events(
                   po_id,event_type,prior_status,new_status,
                   evidence_json,recorded_by
               ) VALUES (%s,'FINALIZATION_AUTHORITY','REVIEW','FINAL',%s::jsonb,%s)""",
            (po_id, json.dumps(evidence, sort_keys=True), clean_actor),
        )
        row = conn.execute(
            """UPDATE purchase_orders
                  SET po_status='FINAL',finalized_at=%s
                WHERE po_id=%s
                RETURNING po_id,po_status,finalized_at""",
            (completed_at, po_id),
        ).fetchone()
        post_finalization_reconciliation = evaluate_open_po_reconciliation(
            conn, as_of=completed_at
        )
        _persist_open_po_gates(conn, post_finalization_reconciliation)
    return {
        "po_id": str(row[0]),
        "po_status": row[1],
        "finalized_at": row[2],
        "readiness_evaluations": evaluations,
        "open_po_reconciliation": post_finalization_reconciliation,
        "release_performed": False,
    }


def open_po_position(
    conn: Any,
    *,
    variant_id: str,
    vendor_id: str,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Return trusted incoming plus blockers; ambiguity never becomes zero incoming."""

    evaluated_at = _validated_as_of(as_of)
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT l.po_line_id,l.open_units,l.reconciliation_status,
                      l.line_status,p.shopify_import_status,
                      COALESCE(l.expected_receipt_at,p.expected_receipt_at),
                      p.vendor_id::text,l.last_reconciled_at,
                      l.last_reconciled_by,l.reconciliation_evidence
               FROM purchase_order_lines l
               JOIN purchase_orders p ON p.po_id=l.po_id
               WHERE l.variant_id=%s
                 AND p.po_status='FINAL'
                 AND (l.open_units > 0 OR l.reconciliation_status='AMBIGUOUS')
                 AND l.line_status NOT IN ('RECEIVED','CANCELLED')
               ORDER BY l.po_line_id""",
            (variant_id,),
        )
        rows = cursor.fetchall()
    trusted = Decimal("0")
    blockers: list[dict[str, Any]] = []
    trusted_sources: list[dict[str, Any]] = []
    for (
        line_id,
        open_units,
        reconciliation,
        line_status,
        import_status,
        expected,
        source_vendor_id,
        reconciled_at,
        reconciled_by,
        evidence,
    ) in rows:
        trustworthy = _trusted_incoming_state(
            reconciliation=reconciliation,
            line_status=line_status,
            import_status=import_status,
            expected_receipt_at=expected,
            evaluated_at=evaluated_at,
            reconciled_at=reconciled_at,
            reconciled_by=reconciled_by,
            evidence=evidence,
        )
        if trustworthy:
            quantity = Decimal(open_units)
            trusted += quantity
            trusted_sources.append(
                {
                    "po_line_id": int(line_id),
                    "source_vendor_id": source_vendor_id,
                    "open_units": quantity,
                    "expected_receipt_at": expected,
                    "reconciliation_status": reconciliation,
                    "line_status": line_status,
                    "shopify_import_status": import_status,
                    "last_reconciled_at": reconciled_at,
                    "last_reconciled_by": reconciled_by,
                    "reconciliation_evidence": evidence,
                }
            )
        else:
            blockers.append(
                {
                    "po_line_id": int(line_id),
                    "source_vendor_id": source_vendor_id,
                    "reason": "OPEN_PO_RECEIPT_OR_BACKORDER_STATE_UNRESOLVED",
                    "reconciliation_status": reconciliation,
                    "line_status": line_status,
                    "shopify_import_status": import_status,
                    "expected_receipt_present": expected is not None,
                    "expected_receipt_overdue": (
                        expected is not None and expected < evaluated_at
                    ),
                        "direct_evidence_present": (
                        bool(evidence)
                        and reconciled_at is not None
                        and reconciled_by is not None
                            and bool(str(reconciled_by).strip())
                        ),
                        "last_reconciled_at": reconciled_at,
                        "last_reconciled_by": reconciled_by,
                        "reconciliation_evidence": evidence,
                }
            )
    return {
        "variant_id": str(variant_id),
        "vendor_id": str(vendor_id),
        "trusted_incoming_units": trusted,
        "open_line_count": len(rows),
        "blocks_reorder": bool(blockers),
        "trusted_sources": trusted_sources,
        "blockers": blockers,
    }


def evaluate_open_po_reconciliation(
    conn: Any, *, as_of: datetime | None = None
) -> dict[str, Any]:
    evaluated_at = _validated_as_of(as_of)
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT p.vendor_id::text,l.po_line_id,l.variant_id,l.offer_id,l.open_units,
                      l.reconciliation_status,l.line_status,p.shopify_import_status,
                      COALESCE(l.expected_receipt_at,p.expected_receipt_at),
                      l.last_reconciled_at,l.last_reconciled_by,
                      l.reconciliation_evidence
               FROM purchase_order_lines l
               JOIN purchase_orders p ON p.po_id=l.po_id
               WHERE p.po_status='FINAL'
                 AND (l.open_units > 0 OR l.reconciliation_status='AMBIGUOUS')
                 AND l.line_status NOT IN ('RECEIVED','CANCELLED')
               ORDER BY p.vendor_id,l.po_line_id"""
        )
        rows = cursor.fetchall()
    blockers: list[dict[str, Any]] = []
    trusted_units = Decimal("0")
    vendor_totals: dict[str, dict[str, Any]] = {}
    variant_totals: dict[str, dict[str, Any]] = {}
    for (
        vendor_id,
        line_id,
        variant_id,
        offer_id,
        open_units,
        reconciliation,
        line_status,
        import_status,
        expected,
        reconciled_at,
        reconciled_by,
        evidence,
    ) in rows:
        variant_id = str(variant_id)
        vendor = vendor_totals.setdefault(
            vendor_id, {"open_lines": 0, "trusted_units": Decimal("0"), "blockers": []}
        )
        variant = variant_totals.setdefault(
            variant_id,
            {"open_lines": 0, "trusted_units": Decimal("0"), "blockers": []},
        )
        vendor["open_lines"] += 1
        variant["open_lines"] += 1
        trustworthy = _trusted_incoming_state(
            reconciliation=reconciliation,
            line_status=line_status,
            import_status=import_status,
            expected_receipt_at=expected,
            evaluated_at=evaluated_at,
            reconciled_at=reconciled_at,
            reconciled_by=reconciled_by,
            evidence=evidence,
        )
        if trustworthy:
            quantity = Decimal(open_units)
            trusted_units += quantity
            vendor["trusted_units"] += quantity
            variant["trusted_units"] += quantity
        else:
            blocker = {
                "vendor_id": vendor_id,
                "po_line_id": int(line_id),
                "variant_id": variant_id,
                "offer_id": int(offer_id),
                "reason": "OPEN_PO_RECEIPT_OR_BACKORDER_STATE_UNRESOLVED",
                "expected_receipt_overdue": expected is not None and expected < evaluated_at,
                "direct_evidence_present": (
                    bool(evidence)
                    and reconciled_at is not None
                    and reconciled_by is not None
                    and bool(str(reconciled_by).strip())
                ),
            }
            blockers.append(blocker)
            vendor["blockers"].append(blocker)
            variant["blockers"].append(blocker)
    status = "PASS" if not blockers else "WARN"
    message = (
        "Every open Procurement PO line has trusted incoming evidence."
        if status == "PASS"
        else f"{len(blockers)} open Procurement PO line(s) require reconciliation review."
    )
    return {
        "status": status,
        "message": message,
        "blocks_po": False,
        "evidence": {
            "open_lines": len(rows),
            "trusted_incoming_units": format(trusted_units, "f"),
            "blocking_lines": len(blockers),
            "blockers": blockers,
            "evaluated_at": evaluated_at.isoformat(),
        },
        "vendors": vendor_totals,
        "variants": variant_totals,
    }


def _persist_open_po_gates(conn: Any, evaluation: dict[str, Any]) -> None:
    blocker_line_ids = sorted(
        {int(blocker["po_line_id"]) for blocker in evaluation["evidence"]["blockers"]}
    )
    for blocker in evaluation["evidence"]["blockers"]:
        conn.execute(
            """INSERT INTO exceptions(
                   run_id,exception_type,severity,variant_id,offer_id,vendor_id,
                   po_line_id,message,status
               ) SELECT NULL,'OPEN_PO_RECONCILIATION_REQUIRED','HIGH',%s,%s,NULL,%s,%s,'OPEN'
               WHERE NOT EXISTS (
                   SELECT 1 FROM exceptions
                   WHERE po_line_id=%s
                     AND exception_type='OPEN_PO_RECONCILIATION_REQUIRED'
                     AND status='OPEN'
               )""",
            (
                blocker["variant_id"],
                blocker["offer_id"],
                blocker["po_line_id"],
                f"Procurement PO line {blocker['po_line_id']} requires direct receipt/backorder evidence.",
                blocker["po_line_id"],
            ),
        )
    conn.execute(
        """UPDATE exceptions SET status='RESOLVED',
                  resolution='Open-PO reconciliation evidence is now trusted or closed',
                  resolved_at=now()
           WHERE exception_type='OPEN_PO_RECONCILIATION_REQUIRED'
             AND status='OPEN'
             AND NOT (po_line_id=ANY(%s::bigint[]))""",
        (blocker_line_ids,),
    )
    for scope_type, collection_name, noun in (
        ("VENDOR", "vendors", "vendor"),
        ("VARIANT", "variants", "variant"),
    ):
        existing_scope_ids = {
            str(row[0])
            for row in conn.execute(
                """SELECT scope_id FROM readiness_gates
                   WHERE gate_name='OPEN_PO_RECONCILIATION' AND scope_type=%s""",
                (scope_type,),
            ).fetchall()
        }
        values_by_scope = evaluation[collection_name]
        for scope_id in sorted(existing_scope_ids | set(values_by_scope)):
            values = values_by_scope.get(
                scope_id,
                {"open_lines": 0, "trusted_units": Decimal("0"), "blockers": []},
            )
            status = "FAIL" if values["blockers"] else "PASS"
            message = (
                f"{len(values['blockers'])} open line(s) require reconciliation for this {noun}."
                if values["blockers"]
                else f"Open Procurement PO evidence is reconciled for this {noun}."
            )
            evidence = {
                "open_lines": values["open_lines"],
                "trusted_incoming_units": format(values["trusted_units"], "f"),
                "blockers": values["blockers"],
            }
            conn.execute(
                """INSERT INTO readiness_gates(
                       gate_name,scope_type,scope_id,status,severity,blocks_po,
                       message,evidence_json,checked_at
                   ) VALUES ('OPEN_PO_RECONCILIATION',%s,%s,%s,'HIGH',%s,%s,%s::jsonb,now())
                   ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                       status=EXCLUDED.status,severity=EXCLUDED.severity,
                       blocks_po=EXCLUDED.blocks_po,message=EXCLUDED.message,
                       evidence_json=EXCLUDED.evidence_json,checked_at=EXCLUDED.checked_at""",
                (
                    scope_type,
                    scope_id,
                    status,
                    status == "FAIL",
                    message,
                    json.dumps(evidence, sort_keys=True),
                ),
            )
    conn.execute(
        """INSERT INTO readiness_gates(
               gate_name,scope_type,scope_id,status,severity,blocks_po,
               message,evidence_json,checked_at
           ) VALUES ('OPEN_PO_RECONCILIATION','GLOBAL','',%s,'HIGH',%s,%s,%s::jsonb,now())
           ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
               status=EXCLUDED.status,severity=EXCLUDED.severity,
               blocks_po=EXCLUDED.blocks_po,message=EXCLUDED.message,
               evidence_json=EXCLUDED.evidence_json,checked_at=EXCLUDED.checked_at""",
        (
            evaluation["status"],
            evaluation["blocks_po"],
            evaluation["message"],
            json.dumps(evaluation["evidence"], sort_keys=True),
        ),
    )


def recompute_open_po_reconciliation_gate(
    conn: Any, *, as_of: datetime | None = None
) -> dict[str, Any]:
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        evaluation = evaluate_open_po_reconciliation(conn, as_of=as_of)
        _persist_open_po_gates(conn, evaluation)
    return evaluation


def record_po_reconciliation(
    conn: Any,
    *,
    po_line_id: int,
    received_units: Any,
    cancelled_units: Any,
    reconciliation_status: str,
    evidence: dict[str, Any],
    actor: str,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    received = _nonnegative(received_units, field="received_units")
    cancelled = _nonnegative(cancelled_units, field="cancelled_units")
    requested_at = _validated_as_of(as_of) if as_of is not None else None
    status = str(reconciliation_status).strip().upper()
    if not isinstance(actor, str):
        raise ProcurementLedgerError("actor and direct reconciliation evidence are required")
    actor = actor.strip()
    if status not in {"OPEN", "RECONCILED", "AMBIGUOUS"}:
        raise ProcurementLedgerError("unsupported reconciliation status")
    if not actor or not isinstance(evidence, dict) or not evidence:
        raise ProcurementLedgerError("actor and direct reconciliation evidence are required")
    source = evidence.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ProcurementLedgerError(
            "direct reconciliation evidence requires a nonblank source"
        )
    if status == "OPEN":
        reference = evidence.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            raise ProcurementLedgerError(
                "open incoming evidence requires a nonblank reference"
            )
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        _transaction_lock(conn)
        recorded_at = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        if (
            requested_at is not None
            and abs((requested_at - recorded_at).total_seconds())
            > RECONCILIATION_CLOCK_TOLERANCE_SECONDS
        ):
            raise ProcurementLedgerError(
                "reconciliation as_of must match the database recording clock"
            )
        row = conn.execute(
            """SELECT l.ordered_units,l.received_units,l.cancelled_units,
                      l.variant_id,l.offer_id,p.vendor_id,p.run_id,p.po_id,
                      p.po_status,p.shopify_import_status,l.line_status
               FROM purchase_order_lines l
               JOIN purchase_orders p ON p.po_id=l.po_id
               WHERE l.po_line_id=%s FOR UPDATE OF l,p""",
            (po_line_id,),
        ).fetchone()
        if row is None:
            raise ProcurementLedgerError("unknown Procurement PO line")
        if row[8] != "FINAL" or row[9] != "IMPORTED" or row[10] == "DRAFT":
            raise ProcurementLedgerError(
                "reconciliation requires a FINAL imported Procurement PO line"
            )
        ordered = Decimal(row[0])
        if received + cancelled > ordered:
            raise ProcurementLedgerError(
                "received plus cancelled units cannot exceed ordered units"
            )
        open_units = ordered - received - cancelled
        if status == "AMBIGUOUS":
            if open_units <= 0:
                raise ProcurementLedgerError(
                    "ambiguous reconciliation requires remaining open units"
                )
            line_status = "BACKORDER_REVIEW"
        elif open_units == 0:
            if received > 0 and cancelled > 0:
                line_status = "PARTIALLY_RECEIVED_CANCELLED"
            elif received > 0:
                line_status = "RECEIVED"
            else:
                line_status = "CANCELLED"
            status = "RECONCILED"
        elif received > 0:
            line_status = "PARTIALLY_RECEIVED"
            status = "OPEN"
        elif cancelled > 0:
            line_status = "PARTIALLY_CANCELLED"
            status = "OPEN"
        else:
            line_status = "ORDERED"
            status = "OPEN"
        conn.execute(
            """UPDATE purchase_order_lines SET
                   received_units=%s,cancelled_units=%s,line_status=%s,
                   reconciliation_status=%s,reconciliation_evidence=%s::jsonb,
                   last_reconciled_at=%s,last_reconciled_by=%s
               WHERE po_line_id=%s""",
            (
                received,
                cancelled,
                line_status,
                status,
                json.dumps(evidence, sort_keys=True),
                recorded_at,
                actor,
                po_line_id,
            ),
        )
        evaluation = evaluate_open_po_reconciliation(conn, as_of=recorded_at)
        _persist_open_po_gates(conn, evaluation)
    return {
        "po_line_id": int(po_line_id),
        "line_status": line_status,
        "reconciliation_status": status,
        "open_units": open_units,
        "recorded_at": recorded_at,
        "readiness": evaluation,
    }
