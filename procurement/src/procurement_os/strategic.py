"""Offer-local price-tier evidence with strategic purchasing disabled."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class PriceTier:
    level_type: str
    unit_price: Decimal
    break_quantity: int | None = None
    break_unit: str | None = None


@dataclass(frozen=True)
class TierCandidate:
    break_quantity: int
    break_unit: str
    unit_price: Decimal
    additional_cases: int
    additional_loose_units: int
    incremental_units: int
    incremental_cash: Decimal
    unit_savings: Decimal


@dataclass(frozen=True)
class StrategicEvidence:
    baseline_unit_cost: Decimal
    selected_unit_cost: Decimal
    selected_tier: PriceTier
    next_tiers: tuple[TierCandidate, ...]
    strategic_extra_units: int
    status: str
    reason_codes: tuple[str, ...]


def _money(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a positive finite decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be a positive finite decimal")
    return result


def evaluate_price_tiers(
    tiers: Iterable[PriceTier], *, baseline_cases: int, baseline_loose_units: int,
    sellable_units_per_case: int, qualifying_units_per_case: int,
    loose_order_allowed: bool = False, loose_units_qualify: bool = False,
) -> StrategicEvidence:
    """Walk an offer's typed ladder; never add strategic inventory automatically."""

    for field, value in (
        ("baseline_cases", baseline_cases),
        ("baseline_loose_units", baseline_loose_units),
        ("sellable_units_per_case", sellable_units_per_case),
        ("qualifying_units_per_case", qualifying_units_per_case),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a nonnegative whole number")
    if sellable_units_per_case < 1 or qualifying_units_per_case < 1:
        raise ValueError("case unit counts must be positive")
    if not isinstance(loose_order_allowed, bool) or not isinstance(loose_units_qualify, bool):
        raise ValueError("loose-order qualification flags must be boolean")
    if loose_units_qualify and not loose_order_allowed:
        raise ValueError("loose units cannot qualify without confirmed loose ordering")
    normalized: list[PriceTier] = []
    for tier in tiers:
        level = str(tier.level_type).strip().upper()
        price = _money(tier.unit_price, "unit_price")
        unit = str(tier.break_unit).strip().upper() if tier.break_unit is not None else None
        quantity = tier.break_quantity
        if level == "BASE":
            if quantity is not None or unit is not None:
                raise ValueError("BASE tier cannot carry a break threshold")
        elif level == "BREAK":
            if unit not in {"BT", "CS"} or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                raise ValueError("BREAK tier requires positive whole BT or CS threshold")
        else:
            raise ValueError("level_type must be BASE or BREAK")
        normalized.append(PriceTier(level, price, quantity, unit))
    bases = [tier for tier in normalized if tier.level_type == "BASE"]
    if len(bases) != 1:
        raise ValueError("exactly one BASE tier is required")
    base = bases[0]
    breaks = sorted(
        (tier for tier in normalized if tier.level_type == "BREAK"),
        key=lambda tier: (tier.break_unit or "", tier.break_quantity or 0),
    )
    for unit in ("BT", "CS"):
        ladder = [tier for tier in breaks if tier.break_unit == unit]
        if any(
            deeper.unit_price > shallower.unit_price
            for shallower, deeper in zip([base, *ladder], ladder)
        ):
            raise ValueError("deeper price tiers cannot increase unit cost")
        thresholds = [tier.break_quantity for tier in ladder]
        if len(thresholds) != len(set(thresholds)):
            raise ValueError("price-tier thresholds must be unique")

    bt_quantity = baseline_cases * qualifying_units_per_case + (
        baseline_loose_units if loose_units_qualify else 0
    )
    cs_quantity = baseline_cases
    selected = base
    qualifying = {"BT": bt_quantity, "CS": cs_quantity}
    for tier in breaks:
        if qualifying[tier.break_unit or ""] >= (tier.break_quantity or 0):
            if tier.unit_price <= selected.unit_price:
                selected = tier

    ordered_units = baseline_cases * sellable_units_per_case + baseline_loose_units
    candidates: list[TierCandidate] = []
    dominated_tiers = 0
    for tier in breaks:
        threshold = tier.break_quantity or 0
        current_qualifying = qualifying[tier.break_unit or ""]
        if threshold <= current_qualifying:
            continue
        if tier.unit_price >= selected.unit_price:
            dominated_tiers += 1
            continue
        gap = threshold - current_qualifying
        if tier.break_unit == "CS":
            add_cases, add_loose = gap, 0
            incremental_units = add_cases * sellable_units_per_case
        else:
            if loose_units_qualify:
                add_cases, add_loose = divmod(gap, qualifying_units_per_case)
                incremental_units = add_cases * sellable_units_per_case + add_loose
            else:
                add_cases = (gap + qualifying_units_per_case - 1) // qualifying_units_per_case
                add_loose = 0
                incremental_units = add_cases * sellable_units_per_case
        candidate_total_units = ordered_units + incremental_units
        bridge_cash = max(
            candidate_total_units * tier.unit_price
            - ordered_units * selected.unit_price,
            Decimal("0"),
        )
        candidates.append(
            TierCandidate(
                threshold,
                tier.break_unit or "",
                tier.unit_price,
                add_cases,
                add_loose,
                incremental_units,
                bridge_cash,
                max(selected.unit_price - tier.unit_price, Decimal("0")),
            )
        )
    candidates.sort(
        key=lambda item: (
            item.incremental_units,
            item.additional_cases,
            item.additional_loose_units,
            item.break_unit,
            item.break_quantity,
        )
    )
    return StrategicEvidence(
        baseline_unit_cost=base.unit_price,
        selected_unit_cost=selected.unit_price,
        selected_tier=selected,
        next_tiers=tuple(candidates),
        strategic_extra_units=0,
        status="EVIDENCE_ONLY",
        reason_codes=(
            "STRATEGIC_OPTIMIZATION_NOT_VALIDATED",
            "NO_AUTOMATIC_FORWARD_BUY",
            "NO_FILLER_ADDED",
            *( ("DOMINATED_TIERS_OMITTED",) if dominated_tiers else () ),
            f"BASELINE_ORDERED_UNITS_{ordered_units}",
        ),
    )
