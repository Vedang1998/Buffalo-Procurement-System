"""Transparent deterministic emergency forecast calculations.

This bounded V1 helper deliberately exposes low-confidence evidence instead of
pretending that sparse or unknown inventory history proves availability.  It is
not the canonical model-selection/FVA program; downstream callers must retain
the returned method and confidence diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median
from typing import Iterable


VELOCITY = Decimal("0.000001")
UNITS = Decimal("0.0001")
METHOD_VERSION = "EMERGENCY_TRANSPARENT_V1"
INVENTORY_STATES = frozenset({"IN_STOCK", "STOCKOUT", "UNKNOWN"})


@dataclass(frozen=True)
class DemandObservation:
    business_date: date
    net_units: Decimal
    inventory_state: str = "UNKNOWN"


@dataclass(frozen=True)
class ForecastEvidence:
    method_version: str
    history_start: date
    history_end: date
    horizon_days: int
    calendar_days: int
    proven_in_stock_days: int
    censored_stockout_days: int
    unknown_inventory_days: int
    outlier_capped_days: int
    calendar_velocity: Decimal
    in_stock_velocity: Decimal | None
    recent_velocity: Decimal
    forecast_daily_velocity: Decimal
    forecast_units: Decimal
    confidence: str
    reason_codes: tuple[str, ...]


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return parsed


def forecast_demand(
    observations: Iterable[DemandObservation], *, horizon_days: int
) -> ForecastEvidence:
    """Return a conservative nonnegative forecast with explicit evidence.

    Missing calendar dates count as zero sales with UNKNOWN inventory. Proven
    stockout dates are censored only from the in-stock velocity. Returns remain
    in net sales, while final velocities and forecasts are floored at zero.
    Recent influence is damped to a maximum +/-12.5% around the evidence base.
    """

    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days < 1:
        raise ValueError("horizon_days must be a positive whole day count")
    indexed: dict[date, DemandObservation] = {}
    for raw in observations:
        if not isinstance(raw.business_date, date):
            raise ValueError("business_date must be a date")
        state = str(raw.inventory_state).strip().upper()
        if state not in INVENTORY_STATES:
            raise ValueError("inventory_state must be IN_STOCK, STOCKOUT, or UNKNOWN")
        if raw.business_date in indexed:
            raise ValueError("one demand observation per business date is required")
        indexed[raw.business_date] = DemandObservation(
            business_date=raw.business_date,
            net_units=_decimal(raw.net_units, "net_units"),
            inventory_state=state,
        )
    if not indexed:
        raise ValueError("at least one demand observation is required")

    start = min(indexed)
    end = max(indexed)
    calendar_days = (end - start).days + 1
    dates = (start + timedelta(days=offset) for offset in range(calendar_days))
    full = [indexed.get(day) for day in dates]
    raw_positive = [max(item.net_units, Decimal("0")) for item in full if item is not None]
    positive = [value for value in raw_positive if value > 0]
    raw_positive_total = sum(positive, Decimal("0"))
    concentrated = bool(
        raw_positive_total
        and max(positive, default=Decimal("0")) * 2 >= raw_positive_total
    )
    outlier_cap: Decimal | None = None
    if len(positive) >= 8:
        middle = Decimal(str(median(positive)))
        deviations = [abs(value - middle) for value in positive]
        mad = Decimal(str(median(deviations)))
        outlier_cap = middle + Decimal("3") * mad if mad else middle
    elif concentrated:
        ordered_positive = sorted(positive, reverse=True)
        # With only one positive observation there is no empirical peer from
        # which to infer a recurring event size.  Preserve one unit of demand
        # evidence while preventing an arbitrary bulk sale from becoming the
        # recurring daily rate.
        outlier_cap = (
            ordered_positive[1]
            if len(ordered_positive) > 1
            else min(ordered_positive[0], Decimal("1"))
        )
    modeled_by_date: dict[date, Decimal] = {}
    outlier_capped_days = 0
    for day, item in indexed.items():
        modeled = item.net_units
        if outlier_cap is not None and modeled > outlier_cap:
            modeled = outlier_cap
            outlier_capped_days += 1
        modeled_by_date[day] = modeled

    stockout_days = sum(
        1 for item in full if item is not None and item.inventory_state == "STOCKOUT"
    )
    total_all_days = max(sum(modeled_by_date.values(), Decimal("0")), Decimal("0"))
    uncensored_calendar_velocity = total_all_days / calendar_days
    modeled_calendar_days = calendar_days - stockout_days
    total_modeled = max(sum(
        (
            modeled_by_date[item.business_date]
            for item in full
            if item is not None and item.inventory_state != "STOCKOUT"
        ),
        Decimal("0"),
    ), Decimal("0"))
    censored_velocity = (
        total_modeled / modeled_calendar_days if modeled_calendar_days else Decimal("0")
    )
    in_stock = [item for item in full if item is not None and item.inventory_state == "IN_STOCK"]
    known_inventory_coverage = Decimal(len(in_stock) + stockout_days) / Decimal(calendar_days)
    censor_weight = min(Decimal(len(in_stock)) / Decimal("7"), Decimal("1")) * known_inventory_coverage
    calendar_velocity_raw = uncensored_calendar_velocity + censor_weight * (
        censored_velocity - uncensored_calendar_velocity
    )
    calendar_velocity = calendar_velocity_raw.quantize(
        VELOCITY, rounding=ROUND_HALF_UP
    )

    unknown_days = calendar_days - len(in_stock) - stockout_days
    in_stock_velocity: Decimal | None = None
    in_stock_velocity_raw: Decimal | None = None
    if in_stock:
        in_stock_net = max(
            sum((modeled_by_date[item.business_date] for item in in_stock), Decimal("0")),
            Decimal("0"),
        )
        in_stock_velocity_raw = in_stock_net / len(in_stock)
        in_stock_velocity = in_stock_velocity_raw.quantize(
            VELOCITY, rounding=ROUND_HALF_UP
        )

    base_velocity = calendar_velocity_raw

    recent_days = min(14, calendar_days)
    recent_start = end - timedelta(days=recent_days - 1)
    recent_all = [item for item in indexed.values() if item.business_date >= recent_start]
    recent_items = [item for item in recent_all if item.inventory_state != "STOCKOUT"]
    recent_net = max(
        sum(
            (modeled_by_date[item.business_date] for item in recent_items),
            Decimal("0"),
        ),
        Decimal("0"),
    )
    recent_denominator = recent_days - sum(
        1
        for item in indexed.values()
        if item.business_date >= recent_start and item.inventory_state == "STOCKOUT"
    )
    recent_censored_velocity = (
        recent_net / recent_denominator if recent_denominator else Decimal("0")
    )
    recent_all_net = max(
        sum((modeled_by_date[item.business_date] for item in recent_all), Decimal("0")),
        Decimal("0"),
    )
    recent_uncensored_velocity = recent_all_net / recent_days
    recent_in_stock = sum(1 for item in recent_all if item.inventory_state == "IN_STOCK")
    recent_stockout = sum(1 for item in recent_all if item.inventory_state == "STOCKOUT")
    recent_known_coverage = Decimal(recent_in_stock + recent_stockout) / Decimal(recent_days)
    recent_censor_weight = min(Decimal(recent_in_stock) / Decimal("7"), Decimal("1")) * recent_known_coverage
    recent_velocity_raw = recent_uncensored_velocity + recent_censor_weight * (
        recent_censored_velocity - recent_uncensored_velocity
    )
    recent_velocity = recent_velocity_raw.quantize(
        VELOCITY, rounding=ROUND_HALF_UP
    )
    forecast_velocity = base_velocity
    if calendar_days >= 14 and base_velocity > 0:
        ratio = recent_velocity_raw / base_velocity
        bounded_ratio = min(max(ratio, Decimal("0.75")), Decimal("1.25"))
        damped_factor = Decimal("1") + (bounded_ratio - Decimal("1")) / 2
        forecast_velocity = base_velocity * damped_factor
    forecast_velocity_raw = max(forecast_velocity, Decimal("0"))
    forecast_velocity = forecast_velocity_raw.quantize(
        VELOCITY, rounding=ROUND_HALF_UP
    )

    coverage = Decimal(len(in_stock)) / Decimal(calendar_days)
    if concentrated:
        confidence = "LOW"
    elif calendar_days >= 28 and coverage >= Decimal("0.75"):
        confidence = "HIGH"
    elif calendar_days >= 14 and coverage >= Decimal("0.50"):
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    reasons: list[str] = []
    if stockout_days:
        reasons.append("PROVEN_STOCKOUT_DAYS_CENSORED")
    if unknown_days:
        reasons.append("UNKNOWN_INVENTORY_DAYS_NOT_ASSUMED_IN_STOCK")
    if calendar_days < 28:
        reasons.append("THIN_HISTORY")
    if confidence == "LOW":
        reasons.append("LOW_CONFIDENCE_REVIEW_REQUIRED")
    if outlier_capped_days:
        reasons.append("ROBUST_OUTLIER_CAP_APPLIED")
    if concentrated:
        reasons.append("EVENT_OR_BULK_CONCENTRATION_REVIEW")
    reasons.append("MODEL_SELECTION_FVA_NOT_VALIDATED")
    reasons.append("RECENT_INFLUENCE_BOUNDED")
    return ForecastEvidence(
        method_version=METHOD_VERSION,
        history_start=start,
        history_end=end,
        horizon_days=horizon_days,
        calendar_days=calendar_days,
        proven_in_stock_days=len(in_stock),
        censored_stockout_days=stockout_days,
        unknown_inventory_days=unknown_days,
        outlier_capped_days=outlier_capped_days,
        calendar_velocity=calendar_velocity,
        in_stock_velocity=in_stock_velocity,
        recent_velocity=recent_velocity,
        forecast_daily_velocity=forecast_velocity,
        forecast_units=(forecast_velocity_raw * horizon_days).quantize(
            UNITS, rounding=ROUND_HALF_UP
        ),
        confidence=confidence,
        reason_codes=tuple(reasons),
    )
