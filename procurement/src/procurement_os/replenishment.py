"""Fail-closed baseline replenishment and pack conversion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING


POLICY_MODES = frozenset({"ROUTINE", "ONE_BOTTLE", "ALLOCATED", "ROUTINE_EXCLUDED"})


@dataclass(frozen=True)
class BaselineNeed:
    status: str
    policy_mode: str | None
    protection_days: int | None
    target_units: Decimal
    effective_inventory_units: Decimal
    raw_need_units: int
    cases: int
    loose_units: int
    ordered_units: int
    pack_rounding_units: int
    loose_fee: Decimal
    reason_codes: tuple[str, ...]


def _decimal(value: object, field: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite nonnegative decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite nonnegative decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field} must be a finite nonnegative decimal")
    return parsed


def calculate_baseline_need(
    *,
    forecast_daily_velocity: object,
    available_units: object | None,
    trusted_incoming_units: object | None,
    order_cycle_days: int,
    lead_time_days: int,
    lead_time_variability_days: object,
    policy_mode: str | None,
    units_per_case: int,
    loose_order_allowed: bool,
    loose_unit_fee: object | None,
    forecast_units_for_protection: object,
    forecast_horizon_days: int,
    open_po_blocked: bool = False,
) -> BaselineNeed:
    """Calculate legitimate baseline need without filler or untrusted incoming."""

    mode = str(policy_mode).strip().upper() if policy_mode is not None else None
    reasons: list[str] = []
    zero = Decimal("0")
    if mode not in POLICY_MODES:
        reasons.append("MISSING_OR_INVALID_REPLENISHMENT_POLICY")
    if available_units is None:
        reasons.append("MISSING_AVAILABLE_INVENTORY")
    if trusted_incoming_units is None:
        reasons.append("MISSING_TRUSTED_INCOMING_POSITION")
    if open_po_blocked:
        reasons.append("OPEN_PO_RECONCILIATION_BLOCKED")
    if reasons:
        return BaselineNeed(
            "BLOCKED", mode, None, zero, zero, 0, 0, 0, 0, 0, zero, tuple(reasons)
        )
    if isinstance(order_cycle_days, bool) or not isinstance(order_cycle_days, int) or order_cycle_days < 1:
        raise ValueError("order_cycle_days must be a positive whole day count")
    if isinstance(lead_time_days, bool) or not isinstance(lead_time_days, int) or lead_time_days < 0:
        raise ValueError("lead_time_days must be a nonnegative whole day count")
    if isinstance(units_per_case, bool) or not isinstance(units_per_case, int) or units_per_case < 1:
        raise ValueError("units_per_case must be a positive whole unit count")
    if not isinstance(loose_order_allowed, bool):
        raise ValueError("loose_order_allowed must be boolean")
    velocity = _decimal(forecast_daily_velocity, "forecast_daily_velocity")
    available = _decimal(available_units, "available_units")
    incoming = _decimal(trusted_incoming_units, "trusted_incoming_units")
    variability = _decimal(lead_time_variability_days, "lead_time_variability_days")
    fee = _decimal(loose_unit_fee, "loose_unit_fee", allow_none=True)
    if loose_order_allowed and fee is None:
        raise ValueError("loose_unit_fee is required when loose ordering is allowed")
    fee = fee or zero
    protection_days = order_cycle_days + lead_time_days + int(
        variability.to_integral_value(rounding=ROUND_CEILING)
    )
    if (
        isinstance(forecast_horizon_days, bool)
        or not isinstance(forecast_horizon_days, int)
        or forecast_horizon_days != protection_days
    ):
        raise ValueError("forecast horizon must exactly match the protection period")
    target = _decimal(forecast_units_for_protection, "forecast_units_for_protection")
    effective = available + incoming

    if mode in {"ALLOCATED", "ROUTINE_EXCLUDED"}:
        return BaselineNeed(
            "ROUTINE_EXCLUDED", mode, protection_days, zero, effective,
            0, 0, 0, 0, 0, zero, (f"{mode}_NO_ROUTINE_REPLENISHMENT",)
        )
    if mode == "ONE_BOTTLE":
        if effective >= 1:
            return BaselineNeed(
                "NO_ORDER_NEEDED", mode, protection_days, zero, effective,
                0, 0, 0, 0, 0, zero, ("ONE_BOTTLE_ALREADY_COVERED",)
            )
        if not loose_order_allowed:
            return BaselineNeed(
                "BLOCKED", mode, protection_days, Decimal("1"), effective,
                1, 0, 0, 0, 0, zero, ("ONE_BOTTLE_REQUIRES_CONFIRMED_LOOSE_ORDER",)
            )
        return BaselineNeed(
            "READY_FOR_REVIEW", mode, protection_days, Decimal("1"), effective,
            1, 0, 1, 1, 0, fee, ("ONE_BOTTLE_POLICY_OVERRIDE",)
        )

    raw_need = max(
        0,
        int((target - effective).to_integral_value(rounding=ROUND_CEILING)),
    )
    if raw_need == 0:
        return BaselineNeed(
            "NO_ORDER_NEEDED", mode, protection_days, target, effective,
            0, 0, 0, 0, 0, zero, ("EFFECTIVE_INVENTORY_COVERS_TARGET",)
        )
    if loose_order_allowed:
        cases, loose = divmod(raw_need, units_per_case)
    else:
        cases = (raw_need + units_per_case - 1) // units_per_case
        loose = 0
    ordered = cases * units_per_case + loose
    return BaselineNeed(
        "READY_FOR_REVIEW", mode, protection_days, target, effective, raw_need,
        cases, loose, ordered, ordered - raw_need, fee if loose else zero,
        (
            "BASELINE_NEED_ONLY",
            "EMPIRICAL_SAFETY_STOCK_NOT_VALIDATED",
            "NO_FILLER_ADDED",
        ),
    )
