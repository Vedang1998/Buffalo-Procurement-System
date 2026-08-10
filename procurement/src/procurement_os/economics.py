from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP

MONEY = Decimal("0.01")

def target_cost(retail_price, target_margin_pct) -> Decimal:
    retail = Decimal(str(retail_price)); margin = Decimal(str(target_margin_pct))
    if retail < 0: raise ValueError("retail_price cannot be negative")
    if not Decimal("0") <= margin < Decimal("1"): raise ValueError("target_margin_pct must be between 0 and 1")
    return (retail * (Decimal("1") - margin)).quantize(MONEY, rounding=ROUND_HALF_UP)

def gross_margin_pct(retail_price, unit_cost):
    retail = Decimal(str(retail_price)); cost = Decimal(str(unit_cost))
    if retail <= 0: return None
    return (retail - cost) / retail

def incremental_gp_per_unit(old_cost, new_cost) -> Decimal:
    return Decimal(str(old_cost)) - Decimal(str(new_cost))

def qualifying_quantity(cases, break_unit: str, qualifying_units_per_case=None) -> Decimal:
    cases_d = Decimal(str(cases)); unit = break_unit.upper()
    if unit == "CS": return cases_d
    if unit in {"BT", "EA"}:
        if qualifying_units_per_case is None: raise ValueError(f"{unit} break requires qualifying_units_per_case")
        return cases_d * Decimal(str(qualifying_units_per_case))
    raise ValueError(f"Unsupported break unit: {break_unit}")

def gp_per_100_cash(incremental_gp, incremental_cash):
    gp = Decimal(str(incremental_gp)); cash = Decimal(str(incremental_cash))
    if cash <= 0: return None
    return (gp / cash * Decimal("100")).quantize(MONEY, rounding=ROUND_HALF_UP)
