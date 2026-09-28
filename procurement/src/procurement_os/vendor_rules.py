"""Confirmed vendor operating profiles and canonical VENDOR_RULES evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VENDOR_RULES_LOCK = 5_920_230_201
WEEKDAYS = (
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)
RULE_FIELDS = (
    "order_days",
    "order_cutoff_local",
    "timezone_name",
    "expected_delivery_days",
    "order_cycle_days",
    "lead_time_days",
    "lead_time_variability_days",
    "reliability_pct",
    "minimum_type",
    "minimum_value",
    "below_minimum_fee",
    "loose_order_allowed",
    "loose_unit_fee",
    "special_rules",
    "holiday_blackout_notes",
    "confirmation_source",
)


class VendorRuleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VendorRulesInput:
    order_days: tuple[str, ...]
    order_cutoff_local: time
    timezone_name: str
    expected_delivery_days: tuple[str, ...]
    order_cycle_days: int
    lead_time_days: int
    lead_time_variability_days: Decimal
    reliability_pct: Decimal
    minimum_type: str
    minimum_value: Decimal | None
    below_minimum_fee: Decimal
    loose_order_allowed: bool
    loose_unit_fee: Decimal | None
    special_rules: str | None
    holiday_blackout_notes: str | None
    confirmation_source: str


def _decimal(
    value: Any,
    *,
    field: str,
    scale: int,
    required: bool = True,
) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise VendorRuleValidationError(f"{field} is required")
        return None
    if isinstance(value, bool):
        raise VendorRuleValidationError(f"{field} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise VendorRuleValidationError(f"{field} must be a finite decimal") from exc
    if not result.is_finite() or max(0, -result.as_tuple().exponent) > scale:
        raise VendorRuleValidationError(
            f"{field} must be finite with no more than {scale} decimal places"
        )
    return result


def _days(value: Iterable[str], *, field: str) -> tuple[str, ...]:
    provided = [str(day).strip().upper() for day in value if str(day).strip()]
    unknown = sorted(set(provided) - set(WEEKDAYS))
    if unknown:
        raise VendorRuleValidationError(f"{field} contains unknown day(s): {', '.join(unknown)}")
    if not provided:
        raise VendorRuleValidationError(f"{field} requires at least one day")
    return tuple(day for day in WEEKDAYS if day in set(provided))


def _whole_days(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise VendorRuleValidationError(f"{field} must be a whole number of days")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise VendorRuleValidationError(
            f"{field} must be a whole number of days"
        ) from exc
    if (
        not parsed.is_finite()
        or parsed != parsed.to_integral_value()
        or parsed > 2_147_483_647
        or parsed < -2_147_483_648
    ):
        raise VendorRuleValidationError(f"{field} must be a whole number of days")
    return int(parsed)


def validate_vendor_rules_input(value: VendorRulesInput | dict[str, Any]) -> VendorRulesInput:
    raw = asdict(value) if isinstance(value, VendorRulesInput) else dict(value)
    cutoff = raw.get("order_cutoff_local")
    if isinstance(cutoff, str):
        try:
            cutoff = time.fromisoformat(cutoff)
        except ValueError as exc:
            raise VendorRuleValidationError("order_cutoff_local must be a valid local time") from exc
    if not isinstance(cutoff, time) or cutoff.tzinfo is not None:
        raise VendorRuleValidationError("order_cutoff_local must be a timezone-free local time")

    timezone_name = str(raw.get("timezone_name") or "").strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise VendorRuleValidationError("timezone_name must be a valid IANA timezone") from exc

    order_cycle_days = _whole_days(
        raw.get("order_cycle_days"), field="order_cycle_days"
    )
    lead_time_days = _whole_days(
        raw.get("lead_time_days"), field="lead_time_days"
    )
    if order_cycle_days < 1:
        raise VendorRuleValidationError("order_cycle_days must be at least one")
    if lead_time_days < 0:
        raise VendorRuleValidationError("lead_time_days cannot be negative")

    variability = _decimal(
        raw.get("lead_time_variability_days"),
        field="lead_time_variability_days",
        scale=2,
    )
    reliability = _decimal(
        raw.get("reliability_pct"), field="reliability_pct", scale=6
    )
    if variability is None or variability < 0:
        raise VendorRuleValidationError("lead_time_variability_days cannot be negative")
    if reliability is None or reliability < 0 or reliability > 1:
        raise VendorRuleValidationError("reliability_pct must be between zero and one")

    minimum_type = str(raw.get("minimum_type") or "").strip().upper()
    if minimum_type not in {"NONE", "CASE", "DOLLAR"}:
        raise VendorRuleValidationError("minimum_type must be NONE, CASE, or DOLLAR")
    minimum_value = _decimal(
        raw.get("minimum_value"), field="minimum_value", scale=2, required=False
    )
    if minimum_type == "NONE":
        if minimum_value is not None:
            raise VendorRuleValidationError("minimum_value must be blank when minimum_type is NONE")
    elif minimum_value is None or minimum_value <= 0:
        raise VendorRuleValidationError(
            "CASE and DOLLAR minimums require a positive minimum_value"
        )
    if minimum_type == "CASE" and minimum_value != minimum_value.to_integral_value():
        raise VendorRuleValidationError("CASE minimum_value must be a whole case count")

    below_fee = _decimal(
        raw.get("below_minimum_fee"), field="below_minimum_fee", scale=2
    )
    if below_fee is None or below_fee < 0:
        raise VendorRuleValidationError("below_minimum_fee cannot be negative")
    loose_allowed = raw.get("loose_order_allowed")
    if not isinstance(loose_allowed, bool):
        raise VendorRuleValidationError("loose_order_allowed must be true or false")
    loose_fee = _decimal(
        raw.get("loose_unit_fee"), field="loose_unit_fee", scale=2, required=False
    )
    if loose_fee is not None and loose_fee < 0:
        raise VendorRuleValidationError("loose_unit_fee cannot be negative")
    if loose_allowed and loose_fee is None:
        raise VendorRuleValidationError(
            "loose_unit_fee is required when loose ordering is allowed"
        )

    confirmation_source = str(raw.get("confirmation_source") or "").strip()
    if not confirmation_source:
        raise VendorRuleValidationError("confirmation_source is required")
    return VendorRulesInput(
        order_days=_days(raw.get("order_days") or (), field="order_days"),
        order_cutoff_local=cutoff,
        timezone_name=timezone_name,
        expected_delivery_days=_days(
            raw.get("expected_delivery_days") or (), field="expected_delivery_days"
        ),
        order_cycle_days=order_cycle_days,
        lead_time_days=lead_time_days,
        lead_time_variability_days=variability,
        reliability_pct=reliability,
        minimum_type=minimum_type,
        minimum_value=minimum_value,
        below_minimum_fee=below_fee,
        loose_order_allowed=loose_allowed,
        loose_unit_fee=loose_fee,
        special_rules=str(raw.get("special_rules") or "").strip() or None,
        holiday_blackout_notes=(
            str(raw.get("holiday_blackout_notes") or "").strip() or None
        ),
        confirmation_source=confirmation_source,
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _business_payload(rules: VendorRulesInput | dict[str, Any]) -> dict[str, Any]:
    source = asdict(rules) if isinstance(rules, VendorRulesInput) else rules
    return {field: _json_value(source.get(field)) for field in RULE_FIELDS}


def _rule_row(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    names = (*RULE_FIELDS, "confirmed_by", "confirmed_at", "rules_version", "updated_at")
    return dict(zip(names, row, strict=True))


def evaluate_vendor_rules(conn: Any) -> dict[str, Any]:
    """Return backend-owned vendor/global completeness without writing gates."""

    columns = ",".join(f"r.{field}" for field in RULE_FIELDS)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""SELECT v.vendor_id,v.vendor_name,v.active,{columns},
                       r.confirmed_by,r.confirmed_at,r.rules_version,r.updated_at
                FROM vendors v
                LEFT JOIN vendor_operating_rules r ON r.vendor_id=v.vendor_id
                ORDER BY lower(v.vendor_name),v.vendor_id"""
        )
        rows = cursor.fetchall()

    vendors: list[dict[str, Any]] = []
    for row in rows:
        vendor_id, vendor_name, active = str(row[0]), row[1], bool(row[2])
        rules = _rule_row(row[3:]) if row[3] is not None else None
        missing: list[str] = []
        if active and rules is None:
            missing.append("CONFIRMED_RULE_PROFILE")
        elif active and rules is not None:
            try:
                validate_vendor_rules_input(rules)
            except VendorRuleValidationError as exc:
                missing.append(str(exc))
            if not str(rules.get("confirmed_by") or "").strip():
                missing.append("CONFIRMED_BY")
            if rules.get("confirmed_at") is None:
                missing.append("CONFIRMED_AT")
        if not active:
            status = "WARN"
            message = "Inactive vendor is excluded from Monday procurement readiness."
        elif missing:
            status = "FAIL"
            message = "Vendor operating rules are incomplete: " + ", ".join(missing)
        else:
            status = "PASS"
            message = "Vendor operating rules are complete and owner-confirmed."
        vendors.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "active": active,
                "status": status,
                "message": message,
                "missing": missing,
                "rules": (
                    {key: _json_value(value) for key, value in rules.items()}
                    if rules is not None
                    else None
                ),
            }
        )

    active_vendors = [vendor for vendor in vendors if vendor["active"]]
    failing = [vendor for vendor in active_vendors if vendor["status"] == "FAIL"]
    if not active_vendors:
        global_status = "FAIL"
        global_message = "No active vendors exist for Monday procurement."
    elif failing:
        global_status = "WARN"
        global_message = (
            f"Vendor operating rules are incomplete for {len(failing)} active vendor(s); "
            "affected vendors remain blocked by their VENDOR-scoped gates."
        )
    else:
        global_status = "PASS"
        global_message = "All active vendor operating rules are complete and owner-confirmed."
    return {
        "status": global_status,
        "message": global_message,
        "blocks_po": not active_vendors,
        "evidence": {
            "active_vendors": len(active_vendors),
            "passing_vendors": len(active_vendors) - len(failing),
            "failing_vendors": len(failing),
            "failing_vendor_ids": [vendor["vendor_id"] for vendor in failing],
        },
        "vendors": vendors,
    }


def _persist_vendor_gates(conn: Any, evaluation: dict[str, Any]) -> None:
    for vendor in evaluation["vendors"]:
        evidence = {
            "vendor_name": vendor["vendor_name"],
            "active": vendor["active"],
            "missing": vendor["missing"],
            "rules_version": (vendor["rules"] or {}).get("rules_version"),
        }
        conn.execute(
            """INSERT INTO readiness_gates(
                   gate_name,scope_type,scope_id,status,severity,blocks_po,
                   message,evidence_json,checked_at
               ) VALUES ('VENDOR_RULES','VENDOR',%s,%s,'HIGH',%s,%s,%s::jsonb,now())
               ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                   status=EXCLUDED.status,severity=EXCLUDED.severity,
                   blocks_po=EXCLUDED.blocks_po,message=EXCLUDED.message,
                   evidence_json=EXCLUDED.evidence_json,checked_at=EXCLUDED.checked_at""",
            (
                vendor["vendor_id"],
                vendor["status"],
                vendor["active"] and vendor["status"] == "FAIL",
                vendor["message"],
                json.dumps(evidence, sort_keys=True),
            ),
        )
    conn.execute(
        """INSERT INTO readiness_gates(
               gate_name,scope_type,scope_id,status,severity,blocks_po,
               message,evidence_json,checked_at
           ) VALUES ('VENDOR_RULES','GLOBAL','',%s,'HIGH',%s,%s,%s::jsonb,now())
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


def recompute_vendor_rules_gates(conn: Any) -> dict[str, Any]:
    with conn.transaction():
        evaluation = evaluate_vendor_rules(conn)
        _persist_vendor_gates(conn, evaluation)
    return evaluation


def update_vendor_rules(
    conn: Any,
    *,
    vendor_id: str,
    rules: VendorRulesInput | dict[str, Any],
    actor: str,
    reason: str,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Create/update one confirmed profile with optimistic concurrency and audit."""

    normalized = validate_vendor_rules_input(rules)
    actor = str(actor).strip()
    reason = str(reason).strip()
    if not actor or not reason:
        raise VendorRuleValidationError("actor and change reason are required")
    desired_payload = _business_payload(normalized)

    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        locked = conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (VENDOR_RULES_LOCK,)
        ).fetchone()[0]
        if not locked:
            raise RuntimeError("vendor rules transaction lock is unavailable")
        vendor = conn.execute(
            "SELECT vendor_name,active FROM vendors WHERE vendor_id=%s FOR UPDATE",
            (vendor_id,),
        ).fetchone()
        if vendor is None:
            raise VendorRuleValidationError("unknown vendor")
        if not vendor[1]:
            raise VendorRuleValidationError("inactive vendor rules cannot be changed")
        columns = ",".join(RULE_FIELDS)
        current_row = conn.execute(
            f"""SELECT {columns},confirmed_by,confirmed_at,rules_version,updated_at
                FROM vendor_operating_rules WHERE vendor_id=%s FOR UPDATE""",
            (vendor_id,),
        ).fetchone()
        current = _rule_row(current_row)
        current_version = int(current["rules_version"]) if current else 0
        if expected_version is not None and expected_version != current_version:
            raise VendorRuleValidationError(
                f"stale vendor rule version: expected {expected_version}, current {current_version}"
            )
        if current is not None and _business_payload(current) == desired_payload:
            evaluation = evaluate_vendor_rules(conn)
            _persist_vendor_gates(conn, evaluation)
            return {
                "vendor_id": str(vendor_id),
                "vendor_name": vendor[0],
                "rules_version": current_version,
                "idempotent_replay": True,
                "readiness": evaluation,
            }

        version = current_version + 1
        values = [
            list(getattr(normalized, field))
            if field in {"order_days", "expected_delivery_days"}
            else getattr(normalized, field)
            for field in RULE_FIELDS
        ]
        conn.execute(
            f"""INSERT INTO vendor_operating_rules(
                   vendor_id,{columns},confirmed_by,confirmed_at,rules_version,updated_at
               ) VALUES (%s,{','.join(['%s'] * len(RULE_FIELDS))},%s,now(),%s,now())
               ON CONFLICT(vendor_id) DO UPDATE SET
                   {','.join(f'{field}=EXCLUDED.{field}' for field in RULE_FIELDS)},
                   confirmed_by=EXCLUDED.confirmed_by,
                   confirmed_at=EXCLUDED.confirmed_at,
                   rules_version=EXCLUDED.rules_version,
                   updated_at=EXCLUDED.updated_at""",
            (vendor_id, *values, actor, version),
        )
        before_json = (
            json.dumps(_business_payload(current), sort_keys=True)
            if current is not None
            else None
        )
        after_json = json.dumps(desired_payload, sort_keys=True)
        conn.execute(
            """INSERT INTO vendor_rule_revisions(
                   vendor_id,rules_version,before_json,after_json,changed_by,change_reason
               ) VALUES (%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
            (vendor_id, version, before_json, after_json, actor, reason),
        )
        conn.execute(
            """INSERT INTO change_log(
                   table_name,row_key,action,before_json,after_json,actor
               ) VALUES ('vendor_operating_rules',%s,%s,%s::jsonb,%s::jsonb,%s)""",
            (
                str(vendor_id),
                "INSERT" if current is None else "UPDATE",
                before_json,
                after_json,
                actor,
            ),
        )
        evaluation = evaluate_vendor_rules(conn)
        _persist_vendor_gates(conn, evaluation)
    return {
        "vendor_id": str(vendor_id),
        "vendor_name": vendor[0],
        "rules_version": version,
        "idempotent_replay": False,
        "readiness": evaluation,
    }
