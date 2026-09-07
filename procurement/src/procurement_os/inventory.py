"""Owned inventory snapshots and deterministic inventory-history readiness."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Iterable
from zoneinfo import ZoneInfo


INVENTORY_CAPTURE_LOCK = 5_920_230_101
BUFFALO_BUSINESS_TIMEZONE = ZoneInfo("America/New_York")
QUANTITY_FIELDS = (
    "available_quantity",
    "incoming_quantity",
    "on_hand_quantity",
    "committed_quantity",
    "reserved_quantity",
    "damaged_quantity",
)


class InventoryValidationError(ValueError):
    """Raised before a partial or unsafe inventory capture can persist."""


@dataclass(frozen=True)
class InventoryLevel:
    variant_id: str
    location_gid: str = ""
    available_quantity: Decimal | None = None
    incoming_quantity: Decimal | None = None
    on_hand_quantity: Decimal | None = None
    committed_quantity: Decimal | None = None
    reserved_quantity: Decimal | None = None
    damaged_quantity: Decimal | None = None


def inventory_business_date(at: datetime | None = None) -> date:
    instant = at or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InventoryValidationError("inventory business-date instant must be timezone-aware")
    return instant.astimezone(BUFFALO_BUSINESS_TIMEZONE).date()


def _quantity(value: Any, *, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InventoryValidationError(f"{field} must be a finite decimal or null")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InventoryValidationError(
            f"{field} must be a finite decimal or null"
        ) from exc
    if not result.is_finite() or max(0, -result.as_tuple().exponent) > 4:
        raise InventoryValidationError(
            f"{field} must be finite with no more than four decimal places"
        )
    return result


def normalize_inventory_levels(rows: Iterable[InventoryLevel | dict[str, Any]]) -> tuple[InventoryLevel, ...]:
    normalized: list[InventoryLevel] = []
    keys: set[tuple[str, str]] = set()
    for row_number, raw in enumerate(rows, start=1):
        values = asdict(raw) if isinstance(raw, InventoryLevel) else dict(raw)
        variant_id = str(values.get("variant_id") or "").strip()
        location_gid = str(values.get("location_gid") or "").strip()
        if not variant_id:
            raise InventoryValidationError(f"row {row_number}: variant_id is required")
        key = (variant_id, location_gid)
        if key in keys:
            raise InventoryValidationError(
                f"row {row_number}: duplicate variant/location inventory key"
            )
        keys.add(key)
        normalized.append(
            InventoryLevel(
                variant_id=variant_id,
                location_gid=location_gid,
                **{
                    field: _quantity(values.get(field), field=f"row {row_number} {field}")
                    for field in QUANTITY_FIELDS
                },
            )
        )
    if not normalized:
        raise InventoryValidationError("inventory capture requires at least one row")
    return tuple(sorted(normalized, key=lambda row: (row.variant_id, row.location_gid)))


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def inventory_source_hash(rows: Iterable[InventoryLevel], *, business_date: date) -> str:
    ordered_rows = sorted(rows, key=lambda row: (row.variant_id, row.location_gid))
    payload = {
        "business_date": business_date.isoformat(),
        "rows": [
            {
                "variant_id": row.variant_id,
                "location_gid": row.location_gid,
                **{
                    field: _decimal_text(getattr(row, field))
                    for field in QUANTITY_FIELDS
                },
            }
            for row in ordered_rows
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_classification(row: InventoryLevel, identity: tuple[str, bool, str]) -> tuple[str, str | None]:
    identity_scope, active, catalog_state = identity
    if identity_scope != "CURRENT" or not active or catalog_state != "LIVE":
        return "ARCHIVAL_ONLY", "Variant is retained as evidence but is not procurement-eligible."
    if row.available_quantity is None:
        return "INVALID", "Available quantity is required for procurement eligibility."
    negative = [
        field for field in QUANTITY_FIELDS
        if getattr(row, field) is not None and getattr(row, field) < 0
    ]
    if negative:
        return "INVALID", "Negative inventory state requires review: " + ", ".join(negative)
    if row.incoming_quantity is None:
        return "INCOMPLETE", "Incoming quantity is not supplied; it is not assumed to be zero."
    return "VALID", None


def evaluate_inventory_history(
    conn: Any,
    *,
    as_of_date: date,
    stale_after_days: int = 1,
) -> dict[str, Any]:
    """Select-only deterministic evaluation of the latest completed capture."""

    if stale_after_days < 0:
        raise ValueError("stale_after_days cannot be negative")
    with conn.cursor() as cursor:
        cursor.execute(
            """SELECT count(*)
               FROM variants
               WHERE identity_scope='CURRENT' AND active=TRUE AND catalog_state='LIVE'"""
        )
        current_variants = int(cursor.fetchone()[0])
        cursor.execute(
            """SELECT inventory_snapshot_run_id,business_date,started_at,completed_at,
                      source,source_hash,rows_received,eligible_rows,archival_rows,
                      invalid_rows,incomplete_rows,evidence_json
               FROM inventory_snapshot_runs
               WHERE status='COMPLETED' AND business_date <= %s
               ORDER BY business_date DESC,completed_at DESC,
                        inventory_snapshot_run_id DESC
               LIMIT 1""",
            (as_of_date,),
        )
        run = cursor.fetchone()
        if run is None:
            return {
                "status": "FAIL",
                "message": "No completed owned inventory snapshot is available.",
                "blocks_po": True,
                "evidence": {
                    "as_of_date": as_of_date.isoformat(),
                    "current_live_variants": current_variants,
                    "latest_run": None,
                },
            }
        run_id = str(run[0])
        cursor.execute(
            """SELECT count(DISTINCT r.variant_id),
                      count(*) FILTER (WHERE r.validation_status='INVALID'),
                      count(*) FILTER (WHERE r.validation_status='INCOMPLETE')
               FROM inventory_snapshot_run_rows r
               JOIN variants v ON v.variant_id=r.variant_id
               WHERE r.inventory_snapshot_run_id=%s
                 AND v.identity_scope='CURRENT' AND v.active=TRUE
                 AND v.catalog_state='LIVE'""",
            (run_id,),
        )
        covered, invalid_rows, incomplete_rows = map(int, cursor.fetchone())

    missing = max(0, current_variants - covered)
    age_days = (as_of_date - run[1]).days
    evidence = {
        "as_of_date": as_of_date.isoformat(),
        "latest_run": run_id,
        "business_date": run[1].isoformat(),
        "completed_at": run[3].isoformat() if run[3] else None,
        "source": run[4],
        "source_hash": run[5],
        "rows_received": int(run[6]),
        "eligible_rows": int(run[7]),
        "archival_rows": int(run[8]),
        "invalid_rows": invalid_rows,
        "incomplete_rows": incomplete_rows,
        "current_live_variants": current_variants,
        "covered_current_variants": covered,
        "missing_current_variants": missing,
        "age_days": age_days,
    }
    if current_variants == 0:
        status = "FAIL"
        message = "No CURRENT, active, LIVE variants exist for inventory coverage."
    elif missing:
        status = "FAIL"
        message = f"Inventory snapshot is missing {missing} CURRENT/LIVE variant(s)."
    elif invalid_rows:
        status = "FAIL"
        message = f"Inventory snapshot has {invalid_rows} invalid eligible row(s)."
    elif age_days > stale_after_days:
        status = "FAIL"
        message = f"Latest owned inventory snapshot is {age_days} day(s) old."
    elif incomplete_rows:
        status = "FAIL"
        message = (
            f"Inventory is current, but {incomplete_rows} row(s) have unknown incoming; "
            "unknown is not treated as zero."
        )
    else:
        status = "PASS"
        message = "Owned daily inventory snapshot is current, complete, and procurement-eligible."
    return {
        "status": status,
        "message": message,
        "blocks_po": status == "FAIL",
        "evidence": evidence,
    }


def _persist_inventory_gate(conn: Any, evaluation: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO readiness_gates(
               gate_name,scope_type,scope_id,status,severity,blocks_po,
               message,evidence_json,checked_at
           ) VALUES ('INVENTORY_HISTORY','GLOBAL','',%s,'HIGH',%s,%s,%s::jsonb,now())
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


def recompute_inventory_history_gate(
    conn: Any, *, as_of_date: date, stale_after_days: int = 1
) -> dict[str, Any]:
    with conn.transaction():
        evaluation = evaluate_inventory_history(
            conn, as_of_date=as_of_date, stale_after_days=stale_after_days
        )
        _persist_inventory_gate(conn, evaluation)
    return evaluation


def capture_daily_inventory(
    conn: Any,
    *,
    business_date: date,
    rows: Iterable[InventoryLevel | dict[str, Any]],
    source: str,
    captured_at: datetime | None = None,
    _inject_failure_after_row: int | None = None,
) -> dict[str, Any]:
    """Persist one atomic capture, retaining archival rows but gating bad evidence."""

    normalized = normalize_inventory_levels(rows)
    clean_source = str(source).strip()
    if not clean_source:
        raise InventoryValidationError("inventory source is required")
    captured_at = captured_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise InventoryValidationError("captured_at must be timezone-aware")
    if inventory_business_date(captured_at) != business_date:
        raise InventoryValidationError(
            "captured_at must correspond to the inventory business date"
        )
    source_hash = inventory_source_hash(normalized, business_date=business_date)

    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        locked = conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (INVENTORY_CAPTURE_LOCK,)
        ).fetchone()[0]
        if not locked:
            raise RuntimeError("inventory capture transaction lock is unavailable")
        existing = conn.execute(
            """SELECT inventory_snapshot_run_id
               FROM inventory_snapshot_runs
               WHERE business_date=%s AND source=%s AND source_hash=%s
                 AND status='COMPLETED'""",
            (business_date, clean_source, source_hash),
        ).fetchone()
        if existing is not None:
            evaluation = evaluate_inventory_history(conn, as_of_date=business_date)
            return {
                "inventory_snapshot_run_id": str(existing[0]),
                "source_hash": source_hash,
                "idempotent_replay": True,
                "rows_written": 0,
                "readiness": evaluation,
            }

        variant_ids = [row.variant_id for row in normalized]
        identity_rows = conn.execute(
            """SELECT variant_id,identity_scope,active,catalog_state
               FROM variants WHERE variant_id = ANY(%s)""",
            (variant_ids,),
        ).fetchall()
        identities = {
            str(row[0]): (str(row[1]), bool(row[2]), str(row[3]))
            for row in identity_rows
        }
        unknown = sorted(set(variant_ids) - identities.keys())
        if unknown:
            raise InventoryValidationError(
                "inventory capture contains unknown canonical Variant ID(s): "
                + ", ".join(unknown)
            )

        run_id = str(
            conn.execute(
                """INSERT INTO inventory_snapshot_runs(
                       business_date,started_at,status,source,source_hash,rows_received
                   ) VALUES (%s,%s,'RUNNING',%s,%s,%s)
                   RETURNING inventory_snapshot_run_id""",
                (business_date, captured_at, clean_source, source_hash, len(normalized)),
            ).fetchone()[0]
        )
        classifications: list[tuple[InventoryLevel, str, str | None]] = []
        for index, row in enumerate(normalized, start=1):
            status, message = _row_classification(row, identities[row.variant_id])
            classifications.append((row, status, message))
            values = tuple(getattr(row, field) for field in QUANTITY_FIELDS)
            conn.execute(
                """INSERT INTO inventory_snapshot_run_rows(
                       inventory_snapshot_run_id,variant_id,location_gid,
                       available_quantity,incoming_quantity,on_hand_quantity,
                       committed_quantity,reserved_quantity,damaged_quantity,
                       validation_status,validation_message
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (run_id, row.variant_id, row.location_gid, *values, status, message),
            )
            conn.execute(
                """INSERT INTO daily_inventory_snapshots(
                       snapshot_date,captured_at,variant_id,location_gid,
                       available_quantity,incoming_quantity,on_hand_quantity,
                       committed_quantity,reserved_quantity,damaged_quantity,
                       source,inventory_snapshot_run_id,validation_status,
                       validation_message
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(snapshot_date,variant_id,location_gid) DO UPDATE SET
                       captured_at=EXCLUDED.captured_at,
                       available_quantity=EXCLUDED.available_quantity,
                       incoming_quantity=EXCLUDED.incoming_quantity,
                       on_hand_quantity=EXCLUDED.on_hand_quantity,
                       committed_quantity=EXCLUDED.committed_quantity,
                       reserved_quantity=EXCLUDED.reserved_quantity,
                       damaged_quantity=EXCLUDED.damaged_quantity,
                       source=EXCLUDED.source,
                       inventory_snapshot_run_id=EXCLUDED.inventory_snapshot_run_id,
                       validation_status=EXCLUDED.validation_status,
                       validation_message=EXCLUDED.validation_message""",
                (
                    business_date,
                    captured_at,
                    row.variant_id,
                    row.location_gid,
                    *values,
                    clean_source,
                    run_id,
                    status,
                    message,
                ),
            )
            if _inject_failure_after_row == index:
                raise RuntimeError("synthetic inventory capture failure")

        counts = {
            state: sum(status == state for _, status, _ in classifications)
            for state in ("VALID", "INCOMPLETE", "INVALID", "ARCHIVAL_ONLY")
        }
        conn.execute(
            """UPDATE inventory_snapshot_runs SET
                   completed_at=%s,status='COMPLETED',eligible_rows=%s,
                   archival_rows=%s,invalid_rows=%s,incomplete_rows=%s,
                   evidence_json=%s::jsonb
               WHERE inventory_snapshot_run_id=%s""",
            (
                captured_at,
                counts["VALID"] + counts["INCOMPLETE"] + counts["INVALID"],
                counts["ARCHIVAL_ONLY"],
                counts["INVALID"],
                counts["INCOMPLETE"],
                json.dumps({"validation_counts": counts}, sort_keys=True),
                run_id,
            ),
        )
        evaluation = evaluate_inventory_history(conn, as_of_date=business_date)
        _persist_inventory_gate(conn, evaluation)
    return {
        "inventory_snapshot_run_id": run_id,
        "source_hash": source_hash,
        "idempotent_replay": False,
        "rows_written": len(normalized),
        "validation_counts": counts,
        "readiness": evaluation,
    }


def latest_inventory_snapshot_status(conn: Any, *, as_of_date: date) -> dict[str, Any]:
    evaluation = evaluate_inventory_history(conn, as_of_date=as_of_date)
    return {
        "latest_snapshot": evaluation["evidence"].get("latest_run"),
        "status": evaluation["status"],
        "message": evaluation["message"],
        "evidence": evaluation["evidence"],
    }
