"""Pure, zero-authority projection for private procurement research.

The projection deliberately stops before every operational boundary.  It joins
supplier hypotheses only on an exact Shopify Variant ID, retains all named
hypotheses without selecting one, and runs the existing development calculators
only when their complete evidence contracts are present.  The HTML and CSV
renderers accept the same hash-bound projection and perform no I/O.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import html
import io
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from .development_forecast import assign_gp_dollar_abc, plan_development_forecast
from .economics import gross_margin_pct, incremental_gp_per_unit, target_cost
from .forecasting import DemandObservation
from .private_research_intake import ABC_COHORT_EVIDENCE_CONTRACT


PROJECTION_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V1"
AUTHORITY_LABEL = "ZERO_AUTHORITY_RESEARCH_ONLY"
DATA_MODE = "PRIVATE_REAL_DATA_RESEARCH_ONLY"
INTAKE_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_INTAKE_V1"
INTAKE_DATA_MODE = "PRIVATE_REAL_SOURCE_REVIEW"
REAL_NUMERICAL_EVALUATION_NOT_RUN = "REAL_NUMERICAL_EVALUATION_NOT_RUN"
CALCULATED_RESEARCH_ONLY = "CALCULATED_RESEARCH_ONLY"

INTAKE_AUTHORITY = {
    "status": "REVIEW_ONLY",
    "approval_status": "UNAPPROVED",
    "operational_use": "PROHIBITED",
    "mapping_authority": False,
    "price_authority": False,
    "selection_authority": False,
    "inventory_authority": False,
    "forecast_authority": False,
    "procurement_authority": False,
    "shopify_write_authority": False,
    "po_authority": False,
}

INTAKE_ZERO_AUTHORITY = {
    "database_writes": 0,
    "mapping_approvals": 0,
    "price_approvals": 0,
    "selected_offers": 0,
    "activated_prices": 0,
    "inventory_writes": 0,
    "forecast_authorizations": 0,
    "shopify_writes": 0,
    "supplier_messages": 0,
    "po_actions": 0,
}

ZERO_AUTHORITY_EFFECTS = dict(INTAKE_ZERO_AUTHORITY)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DANGEROUS_FORMULA_PREFIXES = ("=", "+", "-", "@")
_ALLOWED_BREAK_UNITS = frozenset({"BT", "CS"})
_PUBLIC_IDENTIFIER_KEYS = frozenset(
    {
        "eligible_variant_ids",
        "scope_id",
        "shopify_variant_id",
        "shopify_variant_ids",
    }
)
_FORBIDDEN_OPERATIONAL_KEYS = frozenset(
    {
        "draft_id",
        "mapping_decision_id",
        "mapping_id",
        "monday_run_id",
        "offer_id",
        "po_id",
        "price_id",
        "purchase_order_id",
        "recommendation_id",
        "run_id",
        "selected_offer_id",
        "source_offer_id",
        "source_price_id",
    }
)


class PrivateResearchProjectionError(ValueError):
    """The private research intake or projection is structurally unsafe."""


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise PrivateResearchProjectionError(f"{field} must be a list")
    return value


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PrivateResearchProjectionError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise PrivateResearchProjectionError(f"{field} keys must be strings")
    return value


def _exact_identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PrivateResearchProjectionError(
            f"{field} must be a nonblank exact string"
        )
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    text = str(value)
    return text if text.strip() else None


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _decimal_text(value: Any) -> str | None:
    parsed = _decimal(value)
    return None if parsed is None else format(parsed, "f")


def _whole_nonnegative(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    parsed = _decimal(value)
    if parsed is None or parsed < 0 or parsed != parsed.to_integral_value():
        return None
    return int(parsed)


def _whole_positive(value: Any) -> int | None:
    parsed = _whole_nonnegative(value)
    return parsed if parsed is not None and parsed > 0 else None


def _json_value(value: Any) -> Any:
    """Return deterministic JSON values without binary-float or object leakage."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise PrivateResearchProjectionError("nonfinite decimal is not supported")
        return format(value, "f")
    if isinstance(value, float):
        parsed = _decimal(value)
        if parsed is None:
            raise PrivateResearchProjectionError("nonfinite number is not supported")
        return format(parsed, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PrivateResearchProjectionError("projection keys must be strings")
        return {key: _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise PrivateResearchProjectionError(
        f"unsupported projection value: {type(value).__name__}"
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _canonical_intake_json_bytes(value: Any) -> bytes:
    """Match the intake producer's content-addressing representation exactly."""

    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise PrivateResearchProjectionError(
            "private research intake is not canonical JSON data"
        ) from exc


def _is_forbidden_operational_key(key: str) -> bool:
    normalized = re.sub(r"(?<!^)(?=[A-Z])", "_", key.strip()).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    if normalized in _PUBLIC_IDENTIFIER_KEYS:
        return False
    return (
        normalized in _FORBIDDEN_OPERATIONAL_KEYS
        or normalized == "id"
        or normalized.endswith("_id")
        or normalized.endswith("_ids")
    )


def _sanitized_metadata(value: Any) -> Any:
    """Copy descriptive metadata while dropping operational identifier fields."""

    if value is None or isinstance(value, (str, bool, int, Decimal, float, date)):
        return _json_value(value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PrivateResearchProjectionError("metadata keys must be strings")
        return {
            key: _sanitized_metadata(item)
            for key, item in sorted(value.items())
            if not _is_forbidden_operational_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitized_metadata(item) for item in value]
    raise PrivateResearchProjectionError(
        f"unsupported metadata value: {type(value).__name__}"
    )


def _validate_intake_envelope(source: Mapping[str, Any]) -> None:
    if source.get("contract") != INTAKE_CONTRACT:
        raise PrivateResearchProjectionError("private research intake contract differs")
    if source.get("data_mode") != INTAKE_DATA_MODE:
        raise PrivateResearchProjectionError("private research intake data mode differs")
    authority = _mapping(source.get("authority"), "intake.authority")
    if set(authority) != set(INTAKE_AUTHORITY) or any(
        (authority[key] is not expected if isinstance(expected, bool) else authority[key] != expected)
        for key, expected in INTAKE_AUTHORITY.items()
    ):
        raise PrivateResearchProjectionError(
            "private research intake authority is not the exact review-only contract"
        )
    zero_authority = _mapping(source.get("zero_authority"), "zero_authority")
    if set(zero_authority) != set(INTAKE_ZERO_AUTHORITY):
        raise PrivateResearchProjectionError("zero_authority key inventory differs")
    for key in INTAKE_ZERO_AUTHORITY:
        item = zero_authority[key]
        if isinstance(item, bool) or not isinstance(item, int) or item != 0:
            raise PrivateResearchProjectionError(
                f"zero_authority.{key} must be integer zero"
            )


def _evidence_sha256s(value: Any) -> list[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.strip().lower().replace("-", "_")
            if (
                "sha256" in normalized
                and isinstance(item, str)
                and _SHA256.fullmatch(item)
            ):
                found.add(item)
            else:
                found.update(_evidence_sha256s(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_evidence_sha256s(item))
    return sorted(found)


def _source_rows(value: Any) -> list[dict[str, Any]]:
    source_mapping = _mapping(value, "sources")
    rows = [(name, item) for name, item in sorted(source_mapping.items())]
    result: list[dict[str, Any]] = []
    for position, (declared_name, raw) in enumerate(rows, start=1):
        source = _mapping(raw, f"sources[{position}]")
        hashes = _evidence_sha256s(source)
        result.append(
            {
                "source_name": declared_name,
                "source_kind": _optional_text(source.get("source_kind"))
                or declared_name,
                "sha256": (
                    str(source["sha256"])
                    if isinstance(source.get("sha256"), str)
                    and _SHA256.fullmatch(str(source["sha256"]))
                    else None
                ),
                "evidence_sha256s": hashes,
                "row_count": _whole_nonnegative(source.get("row_count")),
                "coverage_status": _optional_text(source.get("coverage_status")),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["source_name"] or "",
            item["source_kind"] or "",
            item["sha256"] or "",
            -1 if item["row_count"] is None else item["row_count"],
            _canonical_json_bytes(item).decode("ascii"),
        ),
    )


def _vendor_names(value: Any) -> list[str]:
    if value is None:
        return []
    rows = _sequence(value, "vendors")
    names: set[str] = set()
    for position, raw in enumerate(rows, start=1):
        row = _mapping(raw, f"vendors[{position}]")
        name = _optional_text(_first(row, "vendor_name", "supplier_name", "name"))
        if name:
            names.add(name)
    return sorted(names)


def _limitations(value: Any) -> list[str]:
    if value is None:
        return []
    rows = _sequence(value, "limitations")
    normalized: set[str] = set()
    for item in rows:
        if not isinstance(item, str) or not item.strip():
            raise PrivateResearchProjectionError(
                "limitations must contain nonblank strings"
            )
        normalized.add(item.strip())
    normalized.update(
        {
            "No supplier hypothesis is a mapping, selection, or price authority.",
            "No numerical diagnostic is a procurement recommendation or order authority.",
            "No cheapest-offer selection is performed.",
        }
    )
    return sorted(normalized)


def _validate_declared_coverage(
    coverage: Mapping[str, Any], *, variant_count: int
) -> None:
    """Reconcile declared A1/current populations without hard-coded counts."""

    count_keys = (
        "current_catalog_population",
        "a1_original_review_population",
        "a1_historical_current_census_population",
        "a1_current_original_returned",
        "a1_current_additions",
        "a1_variants_joined_to_current_catalog",
        "a1_variants_not_in_current_catalog",
        "current_catalog_variants_without_a1_review",
    )
    counts: dict[str, int] = {}
    for key in count_keys:
        if key not in coverage:
            continue
        parsed = _whole_nonnegative(coverage[key])
        if parsed is None:
            raise PrivateResearchProjectionError(
                f"coverage.{key} must be a nonnegative whole count"
            )
        counts[key] = parsed
    current = counts.get("current_catalog_population")
    if current is not None and current != variant_count:
        raise PrivateResearchProjectionError(
            "coverage.current_catalog_population differs from current Variant rows"
        )
    a1 = counts.get("a1_original_review_population")
    joined = counts.get("a1_variants_joined_to_current_catalog")
    not_current = counts.get("a1_variants_not_in_current_catalog")
    current_without_a1 = counts.get("current_catalog_variants_without_a1_review")
    if (
        a1 is not None
        and joined is not None
        and not_current is not None
        and a1 != joined + not_current
    ):
        raise PrivateResearchProjectionError(
            "declared A1 review population does not reconcile"
        )
    if (
        current is not None
        and joined is not None
        and current_without_a1 is not None
        and current != joined + current_without_a1
    ):
        raise PrivateResearchProjectionError(
            "declared current catalog population does not reconcile to A1 coverage"
        )


def _forecast_input(
    variant: Mapping[str, Any],
) -> tuple[tuple[DemandObservation, ...] | None, int | None, list[str]]:
    raw = variant.get("forecast_evidence")
    if not isinstance(raw, Mapping):
        return None, None, ["FORECAST_EVIDENCE_MISSING"]
    reasons: list[str] = []
    if raw.get("coverage_complete") is not True:
        reasons.append("FORECAST_COVERAGE_NOT_CONFIRMED_COMPLETE")
    horizon = raw.get("horizon_days")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 31:
        reasons.append("FORECAST_HORIZON_INVALID")
        horizon_value = None
    else:
        horizon_value = horizon
    observations = raw.get("observations")
    if not isinstance(observations, (list, tuple)):
        reasons.append("FORECAST_OBSERVATIONS_MISSING")
        return None, horizon_value, sorted(set(reasons))
    if len(observations) != 84:
        reasons.append("FORECAST_HISTORY_NOT_EXACT_84_DAYS")
    parsed: list[DemandObservation] = []
    seen: set[date] = set()
    for item in observations:
        if not isinstance(item, Mapping):
            reasons.append("FORECAST_OBSERVATION_INVALID")
            continue
        try:
            business_date = date.fromisoformat(str(item.get("business_date")))
        except (TypeError, ValueError):
            reasons.append("FORECAST_OBSERVATION_DATE_INVALID")
            continue
        units = _decimal(item.get("net_units"))
        state = item.get("inventory_state")
        if units is None:
            reasons.append("FORECAST_OBSERVATION_UNITS_INVALID")
            continue
        if state not in {"IN_STOCK", "STOCKOUT", "UNKNOWN"}:
            reasons.append("FORECAST_OBSERVATION_INVENTORY_STATE_INVALID")
            continue
        if business_date in seen:
            reasons.append("FORECAST_OBSERVATION_DATE_DUPLICATED")
            continue
        seen.add(business_date)
        parsed.append(DemandObservation(business_date, units, state))
    parsed.sort(key=lambda item: item.business_date)
    if parsed and any(
        current.business_date != prior.business_date + timedelta(days=1)
        for prior, current in zip(parsed, parsed[1:])
    ):
        reasons.append("FORECAST_HISTORY_NOT_CONTIGUOUS")
    if reasons:
        return None, horizon_value, sorted(set(reasons))
    return tuple(parsed), horizon_value, []


def _forecast_projection(variant: Mapping[str, Any]) -> dict[str, Any]:
    observations, horizon, reasons = _forecast_input(variant)
    common = {
        "authority": AUTHORITY_LABEL,
        "commercial_authority": False,
        "production_activation": False,
    }
    if observations is None or horizon is None:
        return {
            **common,
            "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
            "reason_codes": reasons or ["FORECAST_EVIDENCE_INSUFFICIENT"],
        }
    try:
        plan = plan_development_forecast(observations, horizon_days=horizon)
    except (TypeError, ValueError):
        return {
            **common,
            "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
            "reason_codes": ["FORECAST_EVIDENCE_REJECTED_BY_DEVELOPMENT_CALCULATOR"],
        }
    evidence = plan.to_json_dict()
    return {
        **common,
        "status": CALCULATED_RESEARCH_ONLY,
        "calculator_status": plan.status,
        "method_version": plan.method_version,
        "selected_model": plan.selected_model,
        "history_start": evidence.get("history_start"),
        "history_end": evidence.get("history_end"),
        "horizon_days": plan.horizon_days,
        "forecast_daily_velocity": format(plan.forecast_daily_velocity, "f"),
        "point_forecast_units": format(plan.point_forecast_units, "f"),
        "protection_units": evidence.get("protection_units"),
        "target_units": evidence.get("target_units"),
        "confidence": plan.confidence,
        "reason_codes": list(plan.reason_codes),
        "evidence_sha256": plan.evidence_sha256,
    }


def _abc_not_run(variant_ids: Iterable[str], reasons: Iterable[str]) -> dict[str, Any]:
    reason_codes = sorted(set(reasons)) or ["ABC_COHORT_EVIDENCE_INSUFFICIENT"]
    return {
        "summary": {
            "authority": AUTHORITY_LABEL,
            "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
            "reason_codes": reason_codes,
        },
        "members": {
            variant_id: {
                "authority": AUTHORITY_LABEL,
                "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
                "abc_class": "NOT_CONFIGURED",
                "gross_profit_dollars": None,
                "reason_codes": reason_codes,
            }
            for variant_id in variant_ids
        },
    }


def _abc_projection(
    coverage: Mapping[str, Any], variants: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    variant_ids = sorted(str(item["shopify_variant_id"]) for item in variants)
    raw_scope = coverage.get("abc_cohort")
    reasons: list[str] = []
    if not isinstance(raw_scope, Mapping):
        return _abc_not_run(variant_ids, ["ABC_COHORT_EVIDENCE_MISSING"])
    if raw_scope.get("contract") != ABC_COHORT_EVIDENCE_CONTRACT:
        reasons.append("ABC_COHORT_EVIDENCE_CONTRACT_INVALID")
    if raw_scope.get("coverage_complete") is not True:
        reasons.append("ABC_COHORT_COVERAGE_NOT_CONFIRMED_COMPLETE")
    scope_id = _optional_text(raw_scope.get("scope_id"))
    try:
        lookback_start = date.fromisoformat(str(raw_scope.get("lookback_start")))
        lookback_end = date.fromisoformat(str(raw_scope.get("lookback_end")))
    except (TypeError, ValueError):
        lookback_start = None
        lookback_end = None
        reasons.append("ABC_LOOKBACK_RANGE_INVALID")
    period = raw_scope.get("classification_period_days")
    if (
        not scope_id
        or isinstance(period, bool)
        or not isinstance(period, int)
        or period != 84
        or lookback_start is None
        or lookback_end is None
        or (lookback_end - lookback_start).days + 1 != 84
    ):
        reasons.append("ABC_SCOPE_NOT_EXACT_84_DAYS")

    raw_eligible = raw_scope.get("eligible_variant_ids")
    if (
        not isinstance(raw_eligible, (list, tuple))
        or any(
            not isinstance(item, str) or not item or item != item.strip()
            for item in raw_eligible
        )
    ):
        eligible: list[str] = []
        reasons.append("ABC_ELIGIBLE_MEMBERSHIP_MISSING_OR_INVALID")
    else:
        eligible = list(raw_eligible)
        if eligible != sorted(set(eligible)) or not eligible:
            reasons.append("ABC_ELIGIBLE_MEMBERSHIP_MISSING_OR_INVALID")

    raw_exclusions = raw_scope.get("excluded_variants")
    exclusions: list[dict[str, str]] = []
    if not isinstance(raw_exclusions, (list, tuple)):
        reasons.append("ABC_EXCLUSION_MEMBERSHIP_MISSING_OR_INVALID")
    else:
        for item in raw_exclusions:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"variant_id", "reason_code"}
                or not isinstance(item.get("variant_id"), str)
                or not item["variant_id"]
                or item["variant_id"] != item["variant_id"].strip()
                or not isinstance(item.get("reason_code"), str)
                or not item["reason_code"].strip()
            ):
                reasons.append("ABC_EXCLUSION_MEMBERSHIP_MISSING_OR_INVALID")
                continue
            exclusions.append(
                {
                    "variant_id": item["variant_id"],
                    "reason_code": item["reason_code"],
                }
            )
    exclusions.sort(key=lambda item: item["variant_id"])
    excluded_ids = [item["variant_id"] for item in exclusions]
    if (
        excluded_ids != sorted(set(excluded_ids))
        or set(eligible).intersection(excluded_ids)
        or sorted([*eligible, *excluded_ids]) != variant_ids
    ):
        reasons.append("ABC_COHORT_PARTITION_DIFFERS_FROM_CURRENT_CATALOG")

    rows: list[dict[str, str]] = []
    variants_by_id = {
        str(item["shopify_variant_id"]): item for item in variants
    }
    for variant_id in eligible:
        variant = variants_by_id.get(variant_id)
        if variant is None:
            reasons.append("ABC_ELIGIBLE_MEMBER_NOT_IN_CURRENT_CATALOG")
            continue
        revenue = _decimal(variant.get("historical_revenue"))
        cogs = _decimal(variant.get("historical_cogs"))
        if revenue is None or revenue < 0:
            reasons.append("ABC_HISTORICAL_REVENUE_INCOMPLETE")
        if cogs is None or cogs < 0:
            reasons.append("ABC_HISTORICAL_COGS_INCOMPLETE")
        if revenue is not None and revenue >= 0 and cogs is not None and cogs >= 0:
            rows.append(
                {
                    "variant_id": variant_id,
                    "historical_revenue": format(revenue, "f"),
                    "historical_cogs": format(cogs, "f"),
                }
            )
    if not variant_ids or not eligible:
        reasons.append("ABC_COHORT_EMPTY")
    if len(rows) != len(eligible):
        reasons.append("ABC_COHORT_MEMBER_EVIDENCE_INCOMPLETE")
    eligible_sha = raw_scope.get("eligible_variant_ids_sha256")
    expected_eligible_sha = hashlib.sha256(
        _canonical_json_bytes(eligible)
    ).hexdigest()
    if (
        not isinstance(eligible_sha, str)
        or not _SHA256.fullmatch(eligible_sha)
        or eligible_sha != expected_eligible_sha
    ):
        reasons.append("ABC_ELIGIBLE_MEMBERSHIP_SHA256_DIFFERS")
    exclusion_sha = raw_scope.get("excluded_variants_sha256")
    expected_exclusion_sha = hashlib.sha256(
        _canonical_json_bytes(exclusions)
    ).hexdigest()
    if (
        not isinstance(exclusion_sha, str)
        or not _SHA256.fullmatch(exclusion_sha)
        or exclusion_sha != expected_exclusion_sha
    ):
        reasons.append("ABC_EXCLUSION_MEMBERSHIP_SHA256_DIFFERS")
    catalog_sha = raw_scope.get("catalog_variant_ids_sha256")
    expected_catalog_sha = hashlib.sha256(
        _canonical_json_bytes(variant_ids)
    ).hexdigest()
    if (
        not isinstance(catalog_sha, str)
        or not _SHA256.fullmatch(catalog_sha)
        or catalog_sha != expected_catalog_sha
    ):
        reasons.append("ABC_CATALOG_MEMBERSHIP_SHA256_DIFFERS")

    rows.sort(key=lambda item: item["variant_id"])
    historical_sha = raw_scope.get("historical_evidence_sha256")
    expected_historical_sha = hashlib.sha256(_canonical_json_bytes(rows)).hexdigest()
    if (
        not isinstance(historical_sha, str)
        or not _SHA256.fullmatch(historical_sha)
        or historical_sha != expected_historical_sha
    ):
        reasons.append("ABC_HISTORICAL_EVIDENCE_SHA256_DIFFERS")
    if reasons:
        return _abc_not_run(variant_ids, reasons)
    cohort = {
        "contract": "BUFFALO_DEVELOPMENT_ABC_COHORT_V2",
        "scope": {
            "scope_id": scope_id,
            "lookback_start": lookback_start.isoformat(),
            "lookback_end": lookback_end.isoformat(),
            "classification_period_days": period,
        },
        "eligible_variant_ids": eligible,
        "exclusions": exclusions,
        "rows": rows,
    }
    try:
        result = assign_gp_dollar_abc(cohort)
    except (TypeError, ValueError):
        return _abc_not_run(
            variant_ids, ["ABC_EVIDENCE_REJECTED_BY_DEVELOPMENT_CALCULATOR"]
        )
    if result.get("classification_status") != "CALCULATED":
        return _abc_not_run(variant_ids, ["ABC_CLASSIFICATION_NOT_CALCULATED"])
    members = {
        str(item["variant_id"]): {
            "authority": AUTHORITY_LABEL,
            "status": CALCULATED_RESEARCH_ONLY,
            "abc_class": item["abc_class"],
            "gross_profit_dollars": item["gross_profit_dollars"],
            "cumulative_share_after": item.get("cumulative_share_after"),
            "reason_codes": [],
        }
        for item in result["members"]
    }
    for exclusion in exclusions:
        members[exclusion["variant_id"]] = {
            "authority": AUTHORITY_LABEL,
            "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
            "abc_class": "NOT_CONFIGURED",
            "gross_profit_dollars": None,
            "reason_codes": [
                f"ABC_COHORT_EXCLUDED:{exclusion['reason_code']}"
            ],
        }
    return {
        "summary": {
            "authority": AUTHORITY_LABEL,
            "status": CALCULATED_RESEARCH_ONLY,
            "basis": result["basis"],
            "scope": result["scope"],
            "coverage": result["coverage"],
            "reason_codes": [],
        },
        "members": members,
    }


def _economics_projection(
    variant: Mapping[str, Any], hypothesis: Mapping[str, Any] | None
) -> dict[str, Any]:
    retail = _decimal(variant.get("current_retail_price"))
    margin = _decimal(variant.get("target_margin_pct"))
    if hypothesis is None:
        cost = _decimal(variant.get("current_inventory_item_cost"))
        cost_basis = "CURRENT_SHOPIFY_INVENTORY_ITEM_COST_REFERENCE"
        cost_reason = "CURRENT_SHOPIFY_UNIT_COST_MISSING_OR_INVALID"
    else:
        cost = _decimal(_first(hypothesis, "current_unit_cost", "unit_cost"))
        cost_basis = "NAMED_SUPPLIER_HYPOTHESIS_CURRENT_UNIT_COST"
        cost_reason = "SUPPLIER_HYPOTHESIS_CURRENT_UNIT_COST_MISSING_OR_INVALID"
    reasons: list[str] = []
    if retail is None or retail <= 0:
        reasons.append("CURRENT_RETAIL_PRICE_MISSING_OR_INVALID")
    if margin is None or not Decimal("0") <= margin < Decimal("1"):
        reasons.append("TARGET_MARGIN_MISSING_OR_INVALID")
    if cost is None or cost <= 0:
        reasons.append(cost_reason)
    reasons.extend(_catalog_structural_reasons(variant))
    if hypothesis is not None:
        reasons.extend(_hypothesis_missing_reasons(_hypothesis_fields(hypothesis)))
    common = {
        "authority": AUTHORITY_LABEL,
        "commercial_authority": False,
        "production_activation": False,
        "historical_cogs_used": False,
        "calculation_scope": "UNIT_MARGIN_DIAGNOSTIC_ONLY",
        "source_ladder_used": False,
        "pack_break_economics_status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
    }
    if reasons:
        return {
            **common,
            "status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
            "reason_codes": sorted(reasons),
            "cost_basis": cost_basis,
        }
    assert retail is not None and margin is not None and cost is not None
    diagnostic_target = target_cost(retail, margin)
    margin_pct = gross_margin_pct(retail, cost)
    current_reference = _decimal(variant.get("current_inventory_item_cost"))
    difference = (
        incremental_gp_per_unit(current_reference, cost)
        if hypothesis is not None
        and current_reference is not None
        and current_reference > 0
        else None
    )
    return {
        **common,
        "status": CALCULATED_RESEARCH_ONLY,
        "reason_codes": [],
        "cost_basis": cost_basis,
        "retail_price": format(retail, "f"),
        "current_unit_cost": format(cost, "f"),
        "target_margin_pct": format(margin, "f"),
        "target_cost_diagnostic": format(diagnostic_target, "f"),
        "gross_margin_pct_diagnostic": (
            None if margin_pct is None else format(margin_pct, "f")
        ),
        "current_reference_cost_difference_per_unit": (
            None if difference is None else format(difference, "f")
        ),
    }


def _catalog_structural_reasons(variant: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not isinstance(variant.get("raw_pack"), str) or not str(
        variant.get("raw_pack")
    ).strip():
        reasons.append("CATALOG_RAW_PACK_MISSING_OR_INVALID")
    if (
        _whole_positive(
            _first(variant, "shopify_sellable_units_per_case", "units_per_case")
        )
        is None
    ):
        reasons.append("CATALOG_SELLABLE_UNITS_PER_CASE_MISSING_OR_INVALID")
    if _whole_positive(variant.get("qualifying_units_per_case")) is None:
        reasons.append("CATALOG_QUALIFYING_UNITS_PER_CASE_MISSING_OR_INVALID")
    if variant.get("break_unit") not in _ALLOWED_BREAK_UNITS:
        reasons.append("CATALOG_BREAK_UNIT_MISSING_OR_INVALID")
    if not isinstance(variant.get("allocated_excluded"), bool):
        reasons.append("ALLOCATED_EXCLUSION_STATUS_MISSING")
    if not isinstance(variant.get("combo_excluded"), bool):
        reasons.append("COMBO_EXCLUSION_STATUS_MISSING")
    if variant.get("raw_incoming") is not None:
        if _decimal(variant.get("raw_incoming")) is None:
            reasons.append("RAW_INCOMING_CAPTURE_MISSING_OR_INVALID")
        if variant.get("raw_incoming_trust") != "UNTRUSTED_CAPTURE_ONLY":
            reasons.append("RAW_INCOMING_TRUST_LABEL_MISSING_OR_INVALID")
    return reasons


def _variant_missing_reasons(
    variant: Mapping[str, Any], exact_hypothesis_count: int
) -> list[str]:
    reasons = _catalog_structural_reasons(variant)
    if not _optional_text(variant.get("product_title")):
        reasons.append("PRODUCT_TITLE_MISSING")
    if not _optional_text(variant.get("variant_title")):
        reasons.append("VARIANT_TITLE_MISSING")
    retail = _decimal(variant.get("current_retail_price"))
    if retail is None or retail <= 0:
        reasons.append("CURRENT_RETAIL_PRICE_MISSING_OR_INVALID")
    current_cost = _decimal(variant.get("current_inventory_item_cost"))
    if current_cost is None or current_cost <= 0:
        reasons.append("CURRENT_UNIT_COST_MISSING_OR_INVALID")
    revenue = _decimal(variant.get("historical_revenue"))
    if revenue is None or revenue < 0:
        reasons.append("HISTORICAL_REVENUE_MISSING_OR_INVALID")
    cogs = _decimal(variant.get("historical_cogs"))
    if cogs is None or cogs < 0:
        reasons.append("HISTORICAL_COGS_MISSING_OR_INVALID")
    margin = _decimal(variant.get("target_margin_pct"))
    if margin is None or not Decimal("0") <= margin < Decimal("1"):
        reasons.append("TARGET_MARGIN_MISSING_OR_INVALID")
    if exact_hypothesis_count == 0:
        reasons.append("NO_EXACT_VARIANT_ID_SUPPLIER_HYPOTHESIS")
    forecast, _, forecast_reasons = _forecast_input(variant)
    if forecast is None:
        reasons.extend(forecast_reasons)
    return sorted(set(reasons))


def _hypothesis_fields(hypothesis: Mapping[str, Any]) -> dict[str, Any]:
    raw_source_ref = _first(hypothesis, "source_ref", "source_reference")
    if isinstance(raw_source_ref, (Mapping, list, tuple)):
        source_ref: Any = _sanitized_metadata(raw_source_ref)
    else:
        source_ref = _optional_text(raw_source_ref)
    return {
        "supplier_name": _optional_text(
            _first(hypothesis, "supplier_name", "vendor_name")
        ),
        "supplier_sku": _optional_text(hypothesis.get("supplier_sku")),
        "supplier_description": _optional_text(
            _first(hypothesis, "supplier_description", "description")
        ),
        "source_ref": source_ref,
        "source_hypothesis_label": _optional_text(
            _first(hypothesis, "hypothesis_label", "mapping_status", "confidence")
        ),
        "package_type": _optional_text(hypothesis.get("package_type")),
        "raw_pack": _json_value(hypothesis.get("raw_pack")),
        "shopify_sellable_units_per_case": _json_value(
            _first(
                hypothesis,
                "shopify_sellable_units_per_case",
                "units_per_case",
            )
        ),
        "qualifying_units_per_case": _json_value(
            hypothesis.get("qualifying_units_per_case")
        ),
        "break_unit": _optional_text(hypothesis.get("break_unit")),
        "break_quantity": _json_value(hypothesis.get("break_quantity")),
        "current_unit_cost": _decimal_text(
            _first(hypothesis, "current_unit_cost", "unit_cost")
        ),
        "assortment_scope": _optional_text(hypothesis.get("assortment_scope")),
        "assortment_group": _optional_text(hypothesis.get("assortment_group")),
        "allocated_excluded": (
            hypothesis.get("allocated_excluded")
            if isinstance(hypothesis.get("allocated_excluded"), bool)
            else None
        ),
        "combo_excluded": (
            hypothesis.get("combo_excluded")
            if isinstance(hypothesis.get("combo_excluded"), bool)
            else None
        ),
        "unapproved_price_ladder_evidence": _sanitized_metadata(
            hypothesis.get("unapproved_price_ladder_evidence", [])
        ),
    }


def _hypothesis_missing_reasons(fields: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not fields["supplier_name"]:
        reasons.append("SUPPLIER_NAME_MISSING")
    if not fields["supplier_sku"]:
        reasons.append("SUPPLIER_SKU_MISSING")
    cost = _decimal(fields["current_unit_cost"])
    if cost is None or cost <= 0:
        reasons.append("SUPPLIER_HYPOTHESIS_CURRENT_UNIT_COST_MISSING_OR_INVALID")
    if not isinstance(fields["raw_pack"], str) or not fields["raw_pack"].strip():
        reasons.append("HYPOTHESIS_RAW_PACK_MISSING_OR_INVALID")
    if _whole_positive(fields["shopify_sellable_units_per_case"]) is None:
        reasons.append("HYPOTHESIS_SELLABLE_UNITS_PER_CASE_MISSING_OR_INVALID")
    if _whole_positive(fields["qualifying_units_per_case"]) is None:
        reasons.append("HYPOTHESIS_QUALIFYING_UNITS_PER_CASE_MISSING_OR_INVALID")
    if fields["break_unit"] not in _ALLOWED_BREAK_UNITS:
        reasons.append("HYPOTHESIS_BREAK_UNIT_MISSING_OR_INVALID")
    if _whole_positive(fields["break_quantity"]) is None:
        reasons.append("HYPOTHESIS_BREAK_QUANTITY_MISSING_OR_INVALID")
    if fields["allocated_excluded"] is None:
        reasons.append("HYPOTHESIS_ALLOCATED_EXCLUSION_STATUS_MISSING")
    if fields["combo_excluded"] is None:
        reasons.append("HYPOTHESIS_COMBO_EXCLUSION_STATUS_MISSING")
    return sorted(reasons)


def _public_hypothesis_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Name supplier-hypothesis evidence so it cannot be mistaken for catalog facts."""

    return {
        "supplier_name": fields["supplier_name"],
        "supplier_sku": fields["supplier_sku"],
        "supplier_description": fields["supplier_description"],
        "source_ref": fields["source_ref"],
        "source_hypothesis_label": fields["source_hypothesis_label"],
        "hypothesis_package_type": fields["package_type"],
        "hypothesis_raw_pack": fields["raw_pack"],
        "hypothesis_shopify_sellable_units_per_case": fields[
            "shopify_sellable_units_per_case"
        ],
        "hypothesis_qualifying_units_per_case": fields[
            "qualifying_units_per_case"
        ],
        "hypothesis_break_unit": fields["break_unit"],
        "hypothesis_break_quantity": fields["break_quantity"],
        "hypothesis_current_unit_cost": fields["current_unit_cost"],
        "hypothesis_assortment_scope": fields["assortment_scope"],
        "hypothesis_assortment_group": fields["assortment_group"],
        "hypothesis_allocated_excluded": fields["allocated_excluded"],
        "hypothesis_combo_excluded": fields["combo_excluded"],
        "unapproved_price_ladder_evidence": fields[
            "unapproved_price_ladder_evidence"
        ],
    }


def _hypothesis_sort_key(hypothesis: Mapping[str, Any]) -> tuple[str, ...]:
    fields = _hypothesis_fields(hypothesis)
    return (
        str(fields["supplier_name"] or ""),
        str(fields["supplier_sku"] or ""),
        _canonical_json_bytes(fields["source_ref"]).decode("ascii"),
        str(fields["package_type"] or ""),
        _canonical_json_bytes(fields).decode("ascii"),
    )


def _normalize_variants(
    raw_variants: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _sequence(raw_variants, "variants")
    variants: list[dict[str, Any]] = []
    unjoined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, raw in enumerate(rows, start=1):
        variant = dict(_mapping(raw, f"variants[{position}]"))
        variant_id = _exact_identity(
            variant.get("shopify_variant_id"),
            f"variants[{position}].shopify_variant_id",
        )
        if variant_id in seen:
            raise PrivateResearchProjectionError(
                "duplicate exact Shopify Variant ID in private intake"
            )
        seen.add(variant_id)
        hypotheses = variant.get("supplier_hypotheses", [])
        hypothesis_rows = _sequence(
            hypotheses, f"variants[{position}].supplier_hypotheses"
        )
        exact: list[dict[str, Any]] = []
        for hypothesis_position, raw_hypothesis in enumerate(hypothesis_rows, start=1):
            hypothesis = dict(
                _mapping(
                    raw_hypothesis,
                    f"variants[{position}].supplier_hypotheses[{hypothesis_position}]",
                )
            )
            supplied_id = hypothesis.get("shopify_variant_id")
            if not isinstance(supplied_id, str) or not supplied_id or supplied_id != supplied_id.strip():
                reason = "HYPOTHESIS_EXACT_VARIANT_ID_MISSING"
            elif supplied_id != variant_id:
                reason = "HYPOTHESIS_EXACT_VARIANT_ID_MISMATCH"
            else:
                exact.append(hypothesis)
                continue
            fields = _hypothesis_fields(hypothesis)
            unjoined.append(
                {
                    "authority": AUTHORITY_LABEL,
                    "research_only": True,
                    "parent_shopify_variant_id": variant_id,
                    "provided_shopify_variant_id": (
                        supplied_id if isinstance(supplied_id, str) else None
                    ),
                    "join_status": "REJECTED_NO_EXACT_VARIANT_ID_JOIN",
                    "reason_code": reason,
                    "selection_status": "NOT_RUN_NO_SELECTION_AUTHORITY",
                    **_public_hypothesis_fields(fields),
                    "numerical_evaluation_status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
                }
            )
        variant["shopify_variant_id"] = variant_id
        variant["supplier_hypotheses"] = sorted(exact, key=_hypothesis_sort_key)
        variants.append(variant)
    variants.sort(key=lambda item: str(item["shopify_variant_id"]))
    unjoined.sort(
        key=lambda item: (
            str(item["parent_shopify_variant_id"]),
            str(item.get("provided_shopify_variant_id") or ""),
            str(item.get("supplier_name") or ""),
            str(item.get("supplier_sku") or ""),
            str(item.get("source_ref") or ""),
            str(item.get("hypothesis_current_unit_cost") or ""),
            _canonical_json_bytes(item).decode("ascii"),
        )
    )
    return variants, unjoined


def _variant_public_fields(variant: Mapping[str, Any]) -> dict[str, Any]:
    raw_inventory_evidence = variant.get("inventory_evidence")
    inventory_evidence = (
        _sanitized_metadata(raw_inventory_evidence)
        if isinstance(raw_inventory_evidence, Mapping)
        else None
    )
    raw_sales_evidence = variant.get("sales_history")
    historical_sales_evidence = (
        _sanitized_metadata(raw_sales_evidence)
        if isinstance(raw_sales_evidence, Mapping)
        else None
    )
    historical_sales_evidence_sha256 = (
        hashlib.sha256(
            _canonical_json_bytes(historical_sales_evidence)
        ).hexdigest()
        if historical_sales_evidence is not None
        else None
    )
    raw_incoming = _decimal_text(variant.get("raw_incoming"))
    return {
        "shopify_variant_id": str(variant["shopify_variant_id"]),
        "product_title": _optional_text(variant.get("product_title")),
        "variant_title": _optional_text(variant.get("variant_title")),
        "shopify_sku": _optional_text(variant.get("shopify_sku")),
        "barcode": _optional_text(variant.get("barcode")),
        "current_retail_price": _decimal_text(variant.get("current_retail_price")),
        "current_inventory_item_cost": _decimal_text(
            variant.get("current_inventory_item_cost")
        ),
        "current_inventory_item_cost_currency": _optional_text(
            variant.get("current_inventory_item_cost_currency")
        ),
        "historical_revenue": _decimal_text(variant.get("historical_revenue")),
        "historical_cogs": _decimal_text(variant.get("historical_cogs")),
        "available": _decimal_text(variant.get("available")),
        "incoming": _decimal_text(variant.get("incoming")),
        "on_hand": _decimal_text(variant.get("on_hand")),
        "committed": _decimal_text(variant.get("committed")),
        "raw_incoming": raw_incoming,
        "raw_incoming_trust": _optional_text(
            variant.get("raw_incoming_trust")
        ),
        "raw_incoming_operational_use": (
            "PROHIBITED_UNTRUSTED_CAPTURE_ONLY"
            if raw_incoming is not None
            else None
        ),
        "inventory_evidence": inventory_evidence,
        "historical_sales_evidence": historical_sales_evidence,
        "historical_sales_evidence_sha256": historical_sales_evidence_sha256,
        "raw_pack": _json_value(variant.get("raw_pack")),
        "shopify_sellable_units_per_case": _json_value(
            _first(
                variant,
                "shopify_sellable_units_per_case",
                "units_per_case",
            )
        ),
        "qualifying_units_per_case": _json_value(
            variant.get("qualifying_units_per_case")
        ),
        "break_unit": _optional_text(variant.get("break_unit")),
        "allocated_excluded": (
            variant.get("allocated_excluded")
            if isinstance(variant.get("allocated_excluded"), bool)
            else None
        ),
        "combo_excluded": (
            variant.get("combo_excluded")
            if isinstance(variant.get("combo_excluded"), bool)
            else None
        ),
        "one_bottle_policy": (
            variant.get("one_bottle_policy")
            if isinstance(variant.get("one_bottle_policy"), bool)
            else None
        ),
    }


def build_private_research_projection(intake: Mapping[str, Any]) -> dict[str, Any]:
    """Build one deterministic, research-only projection from private intake.

    Required top-level identity fields are ``contract``, ``data_mode``,
    ``intake_id``, and ``variants``.  Supplier SKU, title, barcode, vendor, and
    cost never participate in the join; every hypothesis must repeat the exact
    parent ``shopify_variant_id``.
    """

    source = _mapping(intake, "intake")
    _validate_intake_envelope(source)
    intake_contract = INTAKE_CONTRACT
    intake_data_mode = INTAKE_DATA_MODE
    intake_id = _exact_identity(source.get("intake_id"), "intake.intake_id")
    identity_basis = dict(source)
    identity_basis["intake_id"] = None
    expected_intake_id = hashlib.sha256(
        _canonical_intake_json_bytes(identity_basis)
    ).hexdigest()
    if not _SHA256.fullmatch(intake_id) or intake_id != expected_intake_id:
        raise PrivateResearchProjectionError(
            "private research intake identity differs from its canonical content"
        )
    coverage = _mapping(source.get("coverage", {}), "coverage")
    variants, unjoined = _normalize_variants(source.get("variants"))
    _validate_declared_coverage(coverage, variant_count=len(variants))
    abc = _abc_projection(coverage, variants)

    coverage_rows: list[dict[str, Any]] = []
    research_rows: list[dict[str, Any]] = []
    owner_worksheet: list[dict[str, Any]] = []
    forecast_count = 0
    economics_count = 0
    joined_hypothesis_count = 0

    for variant in variants:
        public = _variant_public_fields(variant)
        variant_id = public["shopify_variant_id"]
        hypotheses = list(variant["supplier_hypotheses"])
        forecast = _forecast_projection(variant)
        if forecast["status"] == CALCULATED_RESEARCH_ONLY:
            forecast_count += 1
        abc_member = abc["members"][variant_id]
        missing = sorted(
            set(_variant_missing_reasons(variant, len(hypotheses)))
            .union(str(item) for item in forecast.get("reason_codes", []))
            .union(str(item) for item in abc_member.get("reason_codes", []))
        )
        coverage_rows.append(
            {
                "authority": AUTHORITY_LABEL,
                "research_only": True,
                **public,
                "exact_joined_supplier_hypothesis_count": len(hypotheses),
                "forecast_status": forecast["status"],
                "abc_status": abc_member["status"],
                "missing_data_reasons": missing,
            }
        )
        projected_hypotheses: list[Mapping[str, Any] | None] = hypotheses or [None]
        for hypothesis in projected_hypotheses:
            fields = (
                {
                    "supplier_name": None,
                    "supplier_sku": None,
                    "supplier_description": None,
                    "source_ref": None,
                    "source_hypothesis_label": None,
                    "package_type": None,
                    "raw_pack": None,
                    "shopify_sellable_units_per_case": None,
                    "qualifying_units_per_case": None,
                    "break_unit": None,
                    "break_quantity": None,
                    "current_unit_cost": None,
                    "assortment_scope": None,
                    "assortment_group": None,
                    "allocated_excluded": None,
                    "combo_excluded": None,
                    "unapproved_price_ladder_evidence": [],
                }
                if hypothesis is None
                else _hypothesis_fields(hypothesis)
            )
            economics = _economics_projection(variant, hypothesis)
            if economics["status"] == CALCULATED_RESEARCH_ONLY:
                economics_count += 1
            hypothesis_missing = (
                ["NO_EXACT_VARIANT_ID_SUPPLIER_HYPOTHESIS"]
                if hypothesis is None
                else _hypothesis_missing_reasons(fields)
            )
            public_hypothesis = _public_hypothesis_fields(fields)
            research_rows.append(
                {
                    "authority": AUTHORITY_LABEL,
                    "research_only": True,
                    "shopify_variant_id": variant_id,
                    "product_title": public["product_title"],
                    "variant_title": public["variant_title"],
                    "join_status": (
                        "NO_NAMED_SUPPLIER_HYPOTHESIS"
                        if hypothesis is None
                        else "EXACT_SHOPIFY_VARIANT_ID"
                    ),
                    "hypothesis_status": "NAMED_SUPPLIER_HYPOTHESIS_NOT_AUTHORITY",
                    "selection_status": "NOT_RUN_NO_SELECTION_AUTHORITY",
                    **public_hypothesis,
                    "catalog_raw_pack": public["raw_pack"],
                    "catalog_shopify_sellable_units_per_case": public[
                        "shopify_sellable_units_per_case"
                    ],
                    "catalog_qualifying_units_per_case": public[
                        "qualifying_units_per_case"
                    ],
                    "catalog_break_unit": public["break_unit"],
                    "catalog_allocated_excluded": public["allocated_excluded"],
                    "catalog_combo_excluded": public["combo_excluded"],
                    "shopify_current_unit_cost_reference": public[
                        "current_inventory_item_cost"
                    ],
                    "current_inventory_item_cost_currency": public[
                        "current_inventory_item_cost_currency"
                    ],
                    "catalog_available": public["available"],
                    "catalog_incoming": public["incoming"],
                    "catalog_on_hand": public["on_hand"],
                    "catalog_committed": public["committed"],
                    "catalog_raw_incoming": public["raw_incoming"],
                    "catalog_raw_incoming_trust": public[
                        "raw_incoming_trust"
                    ],
                    "catalog_raw_incoming_operational_use": public[
                        "raw_incoming_operational_use"
                    ],
                    "catalog_inventory_evidence": public[
                        "inventory_evidence"
                    ],
                    "catalog_historical_sales_evidence_sha256": public[
                        "historical_sales_evidence_sha256"
                    ],
                    "historical_cogs": public["historical_cogs"],
                    "forecast": forecast,
                    "abc": abc_member,
                    "economics": economics,
                    "missing_data_reasons": sorted(
                        set(missing)
                        .union(hypothesis_missing)
                        .union(economics.get("reason_codes", []))
                    ),
                }
            )
            if hypothesis is not None:
                joined_hypothesis_count += 1

        supplier_names = sorted(
            {
                name
                for hypothesis in hypotheses
                if (name := _hypothesis_fields(hypothesis)["supplier_name"])
            }
        )
        worksheet_reasons = list(missing)
        if len(hypotheses) > 1:
            worksheet_reasons.append(
                "MULTIPLE_NAMED_SUPPLIER_HYPOTHESES_REQUIRE_OWNER_RESEARCH"
            )
        if public["allocated_excluded"] is True:
            worksheet_reasons.append("ALLOCATED_ROUTINE_EXCLUSION_PRESERVED")
        if public["combo_excluded"] is True:
            worksheet_reasons.append("COMBO_AUTO_ADD_EXCLUSION_PRESERVED")
        if not hypotheses:
            question = (
                "Identify source-backed named supplier hypotheses for this exact "
                "Shopify Variant ID; do not infer identity from SKU or title."
            )
        elif len(hypotheses) > 1:
            question = (
                "Review every named supplier hypothesis and the evidence gaps; "
                "do not choose an offer by lowest unit cost."
            )
        else:
            question = (
                "Validate the named supplier hypothesis and resolve the evidence "
                "gaps; this worksheet performs no mapping or selection."
            )
        owner_worksheet.append(
            {
                "authority": AUTHORITY_LABEL,
                "research_only": True,
                "shopify_variant_id": variant_id,
                "product_title": public["product_title"],
                "variant_title": public["variant_title"],
                "supplier_names": supplier_names,
                "question": question,
                "reason_codes": sorted(set(worksheet_reasons)),
                "owner_response": "",
                "operational_effect": "NONE",
            }
        )

    research_rows.sort(
        key=lambda item: (
            str(item["shopify_variant_id"]),
            str(item.get("supplier_name") or ""),
            str(item.get("supplier_sku") or ""),
            str(item.get("source_ref") or ""),
            str(item.get("hypothesis_package_type") or ""),
            _canonical_json_bytes(item).decode("ascii"),
        )
    )
    declared_coverage = _sanitized_metadata(coverage)
    payload = {
        "contract": PROJECTION_CONTRACT,
        "data_mode": DATA_MODE,
        "status": "RESEARCH_ONLY",
        "authority": AUTHORITY_LABEL,
        "research_only": True,
        "zero_authority": dict(ZERO_AUTHORITY_EFFECTS),
        "intake": {
            "contract": intake_contract,
            "data_mode": intake_data_mode,
            "intake_id": intake_id,
        },
        "sources": _source_rows(source.get("sources")),
        "vendor_names": _vendor_names(source.get("vendors")),
        "declared_coverage": declared_coverage,
        "coverage_summary": {
            "variant_count": len(variants),
            "exact_joined_supplier_hypothesis_count": joined_hypothesis_count,
            "unjoined_supplier_hypothesis_count": len(unjoined),
            "variants_with_missing_data": sum(
                bool(item["missing_data_reasons"]) for item in coverage_rows
            ),
            "forecast_calculated_research_only_count": forecast_count,
            "abc_calculated_research_only_count": sum(
                item["status"] == CALCULATED_RESEARCH_ONLY
                for item in abc["members"].values()
            ),
            "economics_calculated_research_only_count": economics_count,
        },
        "abc_evaluation": abc["summary"],
        "coverage_rows": coverage_rows,
        "research_rows": research_rows,
        "unjoined_supplier_hypotheses": unjoined,
        "owner_worksheet": owner_worksheet,
        "limitations": _limitations(source.get("limitations")),
    }
    normalized = _json_value(payload)
    projection_sha256 = hashlib.sha256(_canonical_json_bytes(normalized)).hexdigest()
    return {**normalized, "projection_sha256": projection_sha256}


def _validated_projection(projection: Mapping[str, Any]) -> dict[str, Any]:
    source = dict(_mapping(projection, "projection"))
    supplied_sha = source.pop("projection_sha256", None)
    if source.get("contract") != PROJECTION_CONTRACT:
        raise PrivateResearchProjectionError("private research projection contract differs")
    zero_authority = source.get("zero_authority")
    if (
        source.get("data_mode") != DATA_MODE
        or source.get("status") != "RESEARCH_ONLY"
        or source.get("authority") != AUTHORITY_LABEL
        or source.get("research_only") is not True
        or not isinstance(zero_authority, Mapping)
        or set(zero_authority) != set(ZERO_AUTHORITY_EFFECTS)
        or any(
            isinstance(zero_authority[key], bool)
            or not isinstance(zero_authority[key], int)
            or zero_authority[key] != 0
            for key in ZERO_AUTHORITY_EFFECTS
        )
    ):
        raise PrivateResearchProjectionError("projection is not zero-authority research")
    normalized = _json_value(source)
    calculated_sha = hashlib.sha256(_canonical_json_bytes(normalized)).hexdigest()
    if not isinstance(supplied_sha, str) or supplied_sha != calculated_sha:
        raise PrivateResearchProjectionError("private research projection SHA differs")
    return {**normalized, "projection_sha256": supplied_sha}


def canonical_private_research_projection_bytes(
    projection: Mapping[str, Any],
) -> bytes:
    """Return canonical full projection bytes after verifying its bound SHA."""

    return _canonical_json_bytes(_validated_projection(projection)) + b"\n"


def private_research_projection_sha256(projection: Mapping[str, Any]) -> str:
    """Verify and return the canonical unsigned projection SHA."""

    return str(_validated_projection(projection)["projection_sha256"])


def filter_private_research_rows(
    projection: Mapping[str, Any],
    *,
    query: str = "",
    vendor: str = "",
    status: str = "",
) -> list[dict[str, Any]]:
    """Return a deterministic server-rendering filter over the same projection.

    ``vendor`` is an exact supplier-name filter. ``status`` may be one of the
    fixed values visible on a research row (join, selection, forecast, ABC,
    economics, or ABC class), ``MISSING_DATA``, or an exact reason code.  This
    helper neither changes nor re-hashes the projection.
    """

    value = _validated_projection(projection)
    if not all(isinstance(item, str) for item in (query, vendor, status)):
        raise PrivateResearchProjectionError("research filters must be strings")
    needle = query.strip().casefold()
    vendor_filter = vendor.strip()
    status_filter = status.strip()
    result: list[dict[str, Any]] = []
    for row in value["research_rows"]:
        reasons = [str(item) for item in row["missing_data_reasons"]]
        searchable = "\n".join(
            str(item or "")
            for item in (
                row["shopify_variant_id"],
                row["product_title"],
                row["variant_title"],
                row["supplier_name"],
                row["supplier_sku"],
                row["supplier_description"],
                row["source_ref"],
                row["hypothesis_package_type"],
                *reasons,
            )
        ).casefold()
        statuses = {
            str(row["join_status"]),
            str(row["selection_status"]),
            str(row["forecast"]["status"]),
            str(row["abc"]["status"]),
            str(row["abc"]["abc_class"]),
            str(row["economics"]["status"]),
            *reasons,
        }
        if reasons:
            statuses.add("MISSING_DATA")
        if needle and needle not in searchable:
            continue
        if vendor_filter and row["supplier_name"] != vendor_filter:
            continue
        if status_filter and status_filter not in statuses:
            continue
        result.append(dict(row))
    return result


def _html_value(value: Any) -> str:
    if value is None:
        text = "—"
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, (Mapping, list, tuple)):
        text = _canonical_json_bytes(value).decode("ascii")
    else:
        text = str(value)
    return html.escape(text, quote=True)


def _html_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    head = "".join(f"<th scope=\"col\">{html.escape(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_html_value(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_private_research_html(projection: Mapping[str, Any]) -> str:
    """Render one static offline HTML worksheet from a verified projection."""

    value = _validated_projection(projection)
    coverage = value["coverage_rows"]
    research = value["research_rows"]
    unjoined = value["unjoined_supplier_hypotheses"]
    worksheet = value["owner_worksheet"]
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        "script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; connect-src "
        "'none'; object-src 'none'; frame-src 'none'; form-action 'none'; "
        "base-uri 'none'\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        "<title>Private procurement research — zero authority</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:120rem;"
        "padding:0 1rem;color:#181818}h1,h2{line-height:1.2}.banner{border:3px solid "
        "#8b1e1e;background:#fff4f4;padding:1rem;font-weight:700}table{border-collapse:"
        "collapse;width:100%;margin:1rem 0 2rem}th,td{border:1px solid #aaa;padding:.45rem;"
        "text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#eee}"
        "code{overflow-wrap:anywhere}</style></head><body>"
        "<h1>Private procurement research</h1>"
        f"<p class=\"banner\">{_html_value(AUTHORITY_LABEL)}. No mapping, selection, "
        "price activation, Shopify write, supplier contact, purchase order, or order "
        "is authorized.</p>"
        f"<p>Contract: <code>{_html_value(value['contract'])}</code><br>"
        f"Projection SHA-256: <code id=\"projection-sha256\">"
        f"{_html_value(value['projection_sha256'])}</code><br>"
        f"Intake: <code>{_html_value(value['intake']['intake_id'])}</code></p>"
        "<h2>Coverage</h2>"
        + _html_table(
            (
                "Shopify Variant ID",
                "Product",
                "Variant",
                "Exact hypotheses",
                "Forecast",
                "ABC",
                "Missing-data reasons",
                "Available",
                "On hand",
                "Committed",
                "Trusted incoming",
                "Raw incoming (untrusted capture only)",
                "Raw incoming trust",
                "Raw incoming operational use",
                "Current cost currency",
                "Inventory evidence (operational IDs removed)",
                "Historical daily sales evidence SHA-256",
                "Historical daily sales evidence",
                "Allocated excluded",
                "Combo excluded",
            ),
            (
                (
                    row["shopify_variant_id"],
                    row["product_title"],
                    row["variant_title"],
                    row["exact_joined_supplier_hypothesis_count"],
                    row["forecast_status"],
                    row["abc_status"],
                    row["missing_data_reasons"],
                    row["available"],
                    row["on_hand"],
                    row["committed"],
                    row["incoming"],
                    row["raw_incoming"],
                    row["raw_incoming_trust"],
                    row["raw_incoming_operational_use"],
                    row["current_inventory_item_cost_currency"],
                    row["inventory_evidence"],
                    row["historical_sales_evidence_sha256"],
                    row["historical_sales_evidence"],
                    row["allocated_excluded"],
                    row["combo_excluded"],
                )
                for row in coverage
            ),
        )
        + "<h2>Named supplier hypotheses — no selection</h2>"
        + _html_table(
            (
                "Shopify Variant ID",
                "Supplier",
                "Supplier SKU",
                "Join",
                "Hypothesis package",
                "Hypothesis raw pack",
                "Hypothesis sellable units/case",
                "Hypothesis qualifying units/case",
                "Hypothesis break",
                "Hypothesis allocated excluded",
                "Hypothesis combo excluded",
                "Unapproved source ladder evidence",
                "Catalog raw pack",
                "Catalog sellable units/case",
                "Catalog qualifying units/case",
                "Catalog break unit",
                "Catalog allocated excluded",
                "Catalog combo excluded",
                "Hypothesis current unit cost",
                "Shopify current unit cost reference",
                "Current cost currency",
                "Historical COGS (separate)",
                "Forecast",
                "ABC",
                "Economics",
                "Economics scope",
                "Selection",
            ),
            (
                (
                    row["shopify_variant_id"],
                    row["supplier_name"],
                    row["supplier_sku"],
                    row["join_status"],
                    row["hypothesis_package_type"],
                    row["hypothesis_raw_pack"],
                    row["hypothesis_shopify_sellable_units_per_case"],
                    row["hypothesis_qualifying_units_per_case"],
                    f"{row['hypothesis_break_quantity'] or '—'} "
                    f"{row['hypothesis_break_unit'] or ''}".strip(),
                    row["hypothesis_allocated_excluded"],
                    row["hypothesis_combo_excluded"],
                    row["unapproved_price_ladder_evidence"],
                    row["catalog_raw_pack"],
                    row["catalog_shopify_sellable_units_per_case"],
                    row["catalog_qualifying_units_per_case"],
                    row["catalog_break_unit"],
                    row["catalog_allocated_excluded"],
                    row["catalog_combo_excluded"],
                    row["hypothesis_current_unit_cost"],
                    row["shopify_current_unit_cost_reference"],
                    row["current_inventory_item_cost_currency"],
                    row["historical_cogs"],
                    row["forecast"]["status"],
                    row["abc"]["abc_class"],
                    row["economics"]["status"],
                    row["economics"]["calculation_scope"],
                    row["selection_status"],
                )
                for row in research
            ),
        )
        + "<h2>Unjoined supplier evidence</h2>"
        + _html_table(
            (
                "Parent Variant ID",
                "Provided Variant ID",
                "Supplier",
                "Supplier SKU",
                "Hypothesis raw pack",
                "Hypothesis sellable units/case",
                "Hypothesis qualifying units/case",
                "Hypothesis break",
                "Hypothesis allocated excluded",
                "Hypothesis combo excluded",
                "Unapproved source ladder evidence",
                "Reason",
            ),
            (
                (
                    row["parent_shopify_variant_id"],
                    row["provided_shopify_variant_id"],
                    row["supplier_name"],
                    row["supplier_sku"],
                    row["hypothesis_raw_pack"],
                    row["hypothesis_shopify_sellable_units_per_case"],
                    row["hypothesis_qualifying_units_per_case"],
                    f"{row['hypothesis_break_quantity'] or '—'} "
                    f"{row['hypothesis_break_unit'] or ''}".strip(),
                    row["hypothesis_allocated_excluded"],
                    row["hypothesis_combo_excluded"],
                    row["unapproved_price_ladder_evidence"],
                    row["reason_code"],
                )
                for row in unjoined
            ),
        )
        + "<h2>Owner worksheet</h2>"
        + _html_table(
            (
                "Shopify Variant ID",
                "Named suppliers",
                "Research question",
                "Reason codes",
                "Owner response",
            ),
            (
                (
                    row["shopify_variant_id"],
                    row["supplier_names"],
                    row["question"],
                    row["reason_codes"],
                    row["owner_response"],
                )
                for row in worksheet
            ),
        )
        + "<h2>Limitations</h2><ul>"
        + "".join(f"<li>{_html_value(item)}</li>" for item in value["limitations"])
        + "</ul></body></html>"
    )


def _spreadsheet_safe(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "TRUE" if value else "FALSE"
    elif isinstance(value, (Mapping, list, tuple)):
        text = _canonical_json_bytes(value).decode("ascii")
    else:
        text = str(value)
    stripped = text.lstrip(" \t\r\n")
    if stripped.startswith(_DANGEROUS_FORMULA_PREFIXES) or text.startswith(("\t", "\r")):
        return "'" + text
    return text


_CSV_FIELDS = (
    "projection_sha256",
    "projection_contract",
    "authority",
    "row_type",
    "shopify_variant_id",
    "product_title",
    "variant_title",
    "supplier_name",
    "supplier_sku",
    "join_status",
    "selection_status",
    "hypothesis_package_type",
    "hypothesis_raw_pack",
    "hypothesis_shopify_sellable_units_per_case",
    "hypothesis_qualifying_units_per_case",
    "hypothesis_break_quantity",
    "hypothesis_break_unit",
    "hypothesis_current_unit_cost",
    "hypothesis_allocated_excluded",
    "hypothesis_combo_excluded",
    "unapproved_price_ladder_evidence",
    "catalog_raw_pack",
    "catalog_shopify_sellable_units_per_case",
    "catalog_qualifying_units_per_case",
    "catalog_break_unit",
    "catalog_allocated_excluded",
    "catalog_combo_excluded",
    "catalog_available",
    "catalog_on_hand",
    "catalog_committed",
    "catalog_incoming",
    "catalog_raw_incoming",
    "catalog_raw_incoming_trust",
    "catalog_raw_incoming_operational_use",
    "catalog_inventory_evidence",
    "catalog_historical_sales_evidence_sha256",
    "catalog_historical_sales_evidence",
    "shopify_current_unit_cost_reference",
    "current_inventory_item_cost_currency",
    "historical_cogs",
    "forecast_status",
    "abc_status",
    "abc_class",
    "economics_status",
    "economics_scope",
    "missing_data_reasons",
    "owner_question",
    "details",
)


def render_private_research_csv(projection: Mapping[str, Any]) -> str:
    """Render one formula-safe UTF-8 CSV from a verified projection."""

    value = _validated_projection(projection)
    common = {
        "projection_sha256": value["projection_sha256"],
        "projection_contract": value["contract"],
        "authority": AUTHORITY_LABEL,
    }
    rows: list[dict[str, Any]] = [
        {
            **common,
            "row_type": "PROJECTION",
            "details": {
                "intake": value["intake"],
                "coverage_summary": value["coverage_summary"],
                "zero_authority": value["zero_authority"],
            },
        }
    ]
    for item in value["coverage_rows"]:
        rows.append(
            {
                **common,
                "row_type": "COVERAGE",
                "shopify_variant_id": item["shopify_variant_id"],
                "product_title": item["product_title"],
                "variant_title": item["variant_title"],
                "catalog_raw_pack": item["raw_pack"],
                "catalog_shopify_sellable_units_per_case": item[
                    "shopify_sellable_units_per_case"
                ],
                "catalog_qualifying_units_per_case": item[
                    "qualifying_units_per_case"
                ],
                "catalog_break_unit": item["break_unit"],
                "catalog_allocated_excluded": item["allocated_excluded"],
                "catalog_combo_excluded": item["combo_excluded"],
                "catalog_available": item["available"],
                "catalog_on_hand": item["on_hand"],
                "catalog_committed": item["committed"],
                "catalog_incoming": item["incoming"],
                "catalog_raw_incoming": item["raw_incoming"],
                "catalog_raw_incoming_trust": item["raw_incoming_trust"],
                "catalog_raw_incoming_operational_use": item[
                    "raw_incoming_operational_use"
                ],
                "catalog_inventory_evidence": item["inventory_evidence"],
                "catalog_historical_sales_evidence_sha256": item[
                    "historical_sales_evidence_sha256"
                ],
                "catalog_historical_sales_evidence": item[
                    "historical_sales_evidence"
                ],
                "shopify_current_unit_cost_reference": item[
                    "current_inventory_item_cost"
                ],
                "current_inventory_item_cost_currency": item[
                    "current_inventory_item_cost_currency"
                ],
                "forecast_status": item["forecast_status"],
                "abc_status": item["abc_status"],
                "missing_data_reasons": item["missing_data_reasons"],
                "details": {
                    "exact_joined_supplier_hypothesis_count": item[
                        "exact_joined_supplier_hypothesis_count"
                    ]
                },
            }
        )
    for item in value["research_rows"]:
        rows.append(
            {
                **common,
                "row_type": "RESEARCH",
                "shopify_variant_id": item["shopify_variant_id"],
                "product_title": item["product_title"],
                "variant_title": item["variant_title"],
                "supplier_name": item["supplier_name"],
                "supplier_sku": item["supplier_sku"],
                "join_status": item["join_status"],
                "selection_status": item["selection_status"],
                "hypothesis_package_type": item["hypothesis_package_type"],
                "hypothesis_raw_pack": item["hypothesis_raw_pack"],
                "hypothesis_shopify_sellable_units_per_case": item[
                    "hypothesis_shopify_sellable_units_per_case"
                ],
                "hypothesis_qualifying_units_per_case": item[
                    "hypothesis_qualifying_units_per_case"
                ],
                "hypothesis_break_quantity": item[
                    "hypothesis_break_quantity"
                ],
                "hypothesis_break_unit": item["hypothesis_break_unit"],
                "hypothesis_current_unit_cost": item[
                    "hypothesis_current_unit_cost"
                ],
                "hypothesis_allocated_excluded": item[
                    "hypothesis_allocated_excluded"
                ],
                "hypothesis_combo_excluded": item[
                    "hypothesis_combo_excluded"
                ],
                "unapproved_price_ladder_evidence": item[
                    "unapproved_price_ladder_evidence"
                ],
                "catalog_raw_pack": item["catalog_raw_pack"],
                "catalog_shopify_sellable_units_per_case": item[
                    "catalog_shopify_sellable_units_per_case"
                ],
                "catalog_qualifying_units_per_case": item[
                    "catalog_qualifying_units_per_case"
                ],
                "catalog_break_unit": item["catalog_break_unit"],
                "catalog_allocated_excluded": item[
                    "catalog_allocated_excluded"
                ],
                "catalog_combo_excluded": item["catalog_combo_excluded"],
                "catalog_available": item["catalog_available"],
                "catalog_on_hand": item["catalog_on_hand"],
                "catalog_committed": item["catalog_committed"],
                "catalog_incoming": item["catalog_incoming"],
                "catalog_raw_incoming": item["catalog_raw_incoming"],
                "catalog_raw_incoming_trust": item[
                    "catalog_raw_incoming_trust"
                ],
                "catalog_raw_incoming_operational_use": item[
                    "catalog_raw_incoming_operational_use"
                ],
                "catalog_inventory_evidence": item[
                    "catalog_inventory_evidence"
                ],
                "catalog_historical_sales_evidence_sha256": item[
                    "catalog_historical_sales_evidence_sha256"
                ],
                "shopify_current_unit_cost_reference": item[
                    "shopify_current_unit_cost_reference"
                ],
                "current_inventory_item_cost_currency": item[
                    "current_inventory_item_cost_currency"
                ],
                "historical_cogs": item["historical_cogs"],
                "forecast_status": item["forecast"]["status"],
                "abc_status": item["abc"]["status"],
                "abc_class": item["abc"]["abc_class"],
                "economics_status": item["economics"]["status"],
                "economics_scope": item["economics"]["calculation_scope"],
                "missing_data_reasons": item["missing_data_reasons"],
                "details": {
                    "source_ref": item["source_ref"],
                    "supplier_description": item["supplier_description"],
                    "hypothesis_assortment_scope": item[
                        "hypothesis_assortment_scope"
                    ],
                    "hypothesis_assortment_group": item[
                        "hypothesis_assortment_group"
                    ],
                    "forecast": item["forecast"],
                    "abc": item["abc"],
                    "economics": item["economics"],
                },
            }
        )
    for item in value["unjoined_supplier_hypotheses"]:
        rows.append(
            {
                **common,
                "row_type": "UNJOINED_HYPOTHESIS",
                "shopify_variant_id": item["parent_shopify_variant_id"],
                "supplier_name": item["supplier_name"],
                "supplier_sku": item["supplier_sku"],
                "join_status": item["join_status"],
                "selection_status": item["selection_status"],
                "hypothesis_package_type": item["hypothesis_package_type"],
                "hypothesis_raw_pack": item["hypothesis_raw_pack"],
                "hypothesis_shopify_sellable_units_per_case": item[
                    "hypothesis_shopify_sellable_units_per_case"
                ],
                "hypothesis_qualifying_units_per_case": item[
                    "hypothesis_qualifying_units_per_case"
                ],
                "hypothesis_break_quantity": item[
                    "hypothesis_break_quantity"
                ],
                "hypothesis_break_unit": item["hypothesis_break_unit"],
                "hypothesis_current_unit_cost": item[
                    "hypothesis_current_unit_cost"
                ],
                "hypothesis_allocated_excluded": item[
                    "hypothesis_allocated_excluded"
                ],
                "hypothesis_combo_excluded": item[
                    "hypothesis_combo_excluded"
                ],
                "unapproved_price_ladder_evidence": item[
                    "unapproved_price_ladder_evidence"
                ],
                "forecast_status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
                "abc_status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
                "economics_status": REAL_NUMERICAL_EVALUATION_NOT_RUN,
                "missing_data_reasons": [item["reason_code"]],
                "details": {
                    "provided_shopify_variant_id": item[
                        "provided_shopify_variant_id"
                    ],
                    "source_ref": item["source_ref"],
                },
            }
        )
    for item in value["owner_worksheet"]:
        rows.append(
            {
                **common,
                "row_type": "OWNER_WORKSHEET",
                "shopify_variant_id": item["shopify_variant_id"],
                "product_title": item["product_title"],
                "variant_title": item["variant_title"],
                "supplier_name": item["supplier_names"],
                "missing_data_reasons": item["reason_codes"],
                "owner_question": item["question"],
                "details": {"owner_response": item["owner_response"]},
            }
        )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {field: _spreadsheet_safe(row.get(field)) for field in _CSV_FIELDS}
        )
    return stream.getvalue()
