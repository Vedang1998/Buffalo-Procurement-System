"""Deterministic emergency Monday review controls.

The numeric thresholds are temporary, owner-reviewable policy loaded only from
``config/rules.toml``.  They are frozen into every run and are not hidden in
the service or HTTP layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import load_rules


class MondayControlError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialEditPolicy:
    policy_version: str
    owner_approval_status: str
    max_normal_baseline_multiplier: Decimal
    max_normal_resulting_days_supply: Decimal

    def evidence(self) -> dict[str, str]:
        return {
            "policy_version": self.policy_version,
            "owner_approval_status": self.owner_approval_status,
            "max_normal_baseline_multiplier": str(
                self.max_normal_baseline_multiplier
            ),
            "max_normal_resulting_days_supply": str(
                self.max_normal_resulting_days_supply
            ),
        }


def _positive_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise MondayControlError(f"{field} must be a positive finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MondayControlError(
            f"{field} must be a positive finite decimal"
        ) from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MondayControlError(f"{field} must be a positive finite decimal")
    return parsed


def load_material_edit_policy() -> MaterialEditPolicy:
    try:
        values = load_rules()["review"]["emergency_material_edit"]
        version = str(values["policy_version"]).strip()
        approval = str(values["owner_approval_status"]).strip()
        multiplier = _positive_decimal(
            values["max_normal_baseline_multiplier"],
            "max_normal_baseline_multiplier",
        )
        days_supply = _positive_decimal(
            values["max_normal_resulting_days_supply"],
            "max_normal_resulting_days_supply",
        )
    except (KeyError, TypeError) as exc:
        raise MondayControlError(
            "emergency material-edit policy is missing or invalid"
        ) from exc
    if not version or not approval:
        raise MondayControlError(
            "emergency material-edit policy version and approval status are required"
        )
    return MaterialEditPolicy(version, approval, multiplier, days_supply)


def classify_material_edit(
    *,
    action: str,
    baseline_units: Decimal,
    recommended_units: Decimal,
    edited_units: Decimal,
    resulting_days_supply: Decimal | None,
    days_supply_status: str,
    recommended_line_cash: Decimal,
    final_line_cash: Decimal,
    policy: MaterialEditPolicy,
) -> dict[str, Any]:
    """Classify an edit without capping it and expose independent review math."""

    baseline = Decimal(baseline_units)
    recommended = Decimal(recommended_units)
    edited = Decimal(edited_units)
    is_edit = action == "EDIT_QUANTITY"
    multiplier: Decimal | None = None
    multiplier_status = "CALCULATED"
    reasons: list[str] = []
    if baseline > 0:
        multiplier = edited / baseline
        if is_edit and edited > baseline * policy.max_normal_baseline_multiplier:
            reasons.append("EDIT_ABOVE_BASELINE_MULTIPLIER")
    elif edited > 0:
        multiplier_status = "UNBOUNDED_ZERO_BASELINE"
        if is_edit:
            reasons.append("EDIT_POSITIVE_WITH_ZERO_BASELINE")
    else:
        multiplier_status = "ZERO_BASELINE_ZERO_EDIT"

    if is_edit and edited > 0 and resulting_days_supply is not None:
        if resulting_days_supply > policy.max_normal_resulting_days_supply:
            reasons.append("EDIT_ABOVE_RESULTING_DAYS_SUPPLY")
    elif is_edit and edited > 0 and days_supply_status == "UNDEFINED_ZERO_FORECAST":
        reasons.append("EDIT_POSITIVE_WITH_ZERO_FORECAST")

    tier = "MATERIAL" if reasons else "NORMAL"
    return {
        "materiality_tier": tier,
        "materiality_reason_codes": reasons,
        "baseline_units": baseline,
        "recommended_units": recommended,
        "edited_units": edited,
        "baseline_multiplier": (
            multiplier.quantize(Decimal("0.0001")) if multiplier is not None else None
        ),
        "baseline_multiplier_status": multiplier_status,
        "resulting_days_supply": resulting_days_supply,
        "days_supply_status": days_supply_status,
        "recommended_line_cash": recommended_line_cash,
        "incremental_line_cash": final_line_cash - recommended_line_cash,
        "final_line_cash": final_line_cash,
        "policy": policy.evidence(),
    }
