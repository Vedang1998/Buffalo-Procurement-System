"""Additive coherent H3/H10/H17 development-research forecast evidence.

This module deliberately does not alter the registered V1/V2 planners.  It
reuses only their frozen candidate recurrences and applies the corrected joint
selection, prefix-cap, calibration, and held-out evaluation contract.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    localcontext,
)
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .development_forecast import (
    DevelopmentForecastError,
    V2_CONTRACT,
    _InapplicableModel,
    _predict,
    _validated_observations,
    load_development_forecast_policy,
)
from .forecasting import DemandObservation


POLICY_CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_JOINT_HORIZON_POLICY_V1"
EVIDENCE_CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_JOINT_HORIZON_EVIDENCE_V1"
METHOD_VERSION = "DEVELOPMENT_ROLLING_ORIGIN_JOINT_HORIZON_V1"
POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "development_forecast_joint_horizon_policy_v1.json"
)
POLICY_SOURCE_SHA256 = (
    "3367e75b3048c4b116f36108c2dafa2d40734920f24b3731c800f098281bb4b4"
)
POLICY_CANONICAL_SHA256 = (
    "993ff322f98bb39fb9a50b4a985e48ed95e1f1742c40e4073011d791eb57f63f"
)
PARENT_POLICY_SOURCE_SHA256 = (
    "fee2e91e14565a835f1d69c27ff547ccdda5c22c6bc43a2bb003c2b78f190b52"
)
PARENT_POLICY_CANONICAL_SHA256 = (
    "ff62b1d313a18a907e811e8722431efee2349fc95a31e08ea5843ca2664ca858"
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VARIANT_ID = re.compile(r"^[1-9][0-9]*$")
_UNITS = Decimal("0.0001")
_METRIC = Decimal("0.000001")
_ZERO_AUTHORITY = {
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
_TOP_LEVEL_KEYS = {
    "contract",
    "authority",
    "research_only",
    "commercial_authority",
    "production_activation",
    "variant_id",
    "corrected_input_id",
    "parent_projection",
    "creation_evidence_delta",
    "joint_policy",
    "history",
    "origin_partitions",
    "candidate_records",
    "selected_model",
    "fva",
    "final_path",
    "calibration",
    "evaluation",
    "summaries",
    "limitations",
    "zero_authority",
    "sidecar_sha256",
}


class JointHorizonForecastError(ValueError):
    """The registered joint planner or its evidence differs."""

    def __init__(self, message: str, *, reason_code: str | None = None):
        super().__init__(message)
        self.reason_code = reason_code


def _context() -> Context:
    value = Context(
        prec=50,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
    )
    value.traps[InvalidOperation] = True
    value.traps[DivisionByZero] = True
    value.traps[Overflow] = True
    return value


def _canonical_utf8(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _logical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _strict_equal(left: Any, right: Any) -> bool:
    try:
        return _logical_bytes(left) == _logical_bytes(right)
    except (TypeError, ValueError):
        return False


def _logical_sha(value: Mapping[str, Any]) -> str:
    unsigned = deepcopy(dict(value))
    unsigned.pop("sidecar_sha256", None)
    return hashlib.sha256(_logical_bytes(unsigned)).hexdigest()


def _nested_hash(value: Mapping[str, Any], field: str) -> str:
    unsigned = deepcopy(dict(value))
    unsigned.pop(field, None)
    return hashlib.sha256(_logical_bytes(unsigned)).hexdigest()


def _internal(value: Decimal) -> str:
    if not value.is_finite():
        raise JointHorizonForecastError("joint forecast decimal is not finite")
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text.startswith("-") and Decimal(text) == 0:
        return "0"
    return text


def _fixed(value: Decimal, quantum: Decimal) -> str:
    result = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)
    digits = abs(quantum.as_tuple().exponent)
    return f"{result:.{digits}f}"


def _units(value: Decimal) -> str:
    return _fixed(value, _UNITS)


def _metric(value: Decimal) -> str:
    return _fixed(value, _METRIC)


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise JointHorizonForecastError("joint forecast mean is empty")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _require_mapping_keys(value: Any, keys: set[str], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise JointHorizonForecastError(f"{field} differs")
    return value


def _load_policy() -> tuple[dict[str, Any], str, str]:
    try:
        raw = POLICY_PATH.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JointHorizonForecastError("joint forecast policy is unreadable") from exc
    source_sha = hashlib.sha256(raw).hexdigest()
    canonical_sha = hashlib.sha256(_canonical_utf8(value)).hexdigest()
    if source_sha != POLICY_SOURCE_SHA256 or canonical_sha != POLICY_CANONICAL_SHA256:
        raise JointHorizonForecastError("registered joint forecast policy bytes differ")
    _require_mapping_keys(
        value,
        {
            "contract",
            "evidence_contract",
            "method_version",
            "parent_policy",
            "history",
            "horizons",
            "candidates",
            "windows",
            "scoring",
            "fva",
            "rate_cap",
            "protection",
            "decimal",
            "output",
            "authority",
        },
        "joint forecast policy",
    )
    if (
        value["contract"] != POLICY_CONTRACT
        or value["evidence_contract"] != EVIDENCE_CONTRACT
        or value["method_version"] != METHOD_VERSION
        or value["authority"]
        != {"commercial_authority": False, "production_activation": False}
        or value["history"]
        != {"days": 138, "start_date": "2026-05-04", "end_date": "2026-09-18"}
        or value["candidates"]
        != {
            "order": [
                "NAIVE",
                "SEASONAL_NAIVE",
                "DAMPED_ETS",
                "TSB",
                "CATEGORY_SHRINKAGE",
            ],
            "simple_baseline": "NAIVE",
        }
        or value["fva"]
        != {
            "gate": "100_TIMES_COMPLEX_ERROR_LE_98_TIMES_NAIVE_ERROR",
            "minimum_relative_improvement_denominator": 100,
            "minimum_relative_improvement_numerator": 2,
        }
        or value["protection"]["quantile_numerator"] != 9
        or value["protection"]["quantile_denominator"] != 10
        or value["decimal"]
        != {
            "emax": 999999,
            "emin": -999999,
            "internal_encoding": "FINITE_PLAIN_DECIMAL_STRIP_TRAILING_FRACTIONAL_ZEROS_NORMALIZE_NEGATIVE_ZERO",
            "precision": 50,
            "rounding": "ROUND_HALF_EVEN",
            "traps": ["InvalidOperation", "DivisionByZero", "Overflow"],
        }
        or value["output"]
        != {
            "metric_quantum": "0.000001",
            "published_daily_basis": "DIFFERENCE_PUBLISHED_CUMULATIVE",
            "rounding": "ROUND_HALF_UP",
            "units_quantum": "0.0001",
        }
    ):
        raise JointHorizonForecastError("joint forecast policy values differ")
    parent = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
    if value["parent_policy"] != {
        "method_version": parent.method_version,
        "policy_canonical_sha256": parent.canonical_sha256,
        "policy_contract": parent.contract,
        "policy_source_sha256": parent.source_sha256,
    }:
        raise JointHorizonForecastError("joint parent forecast policy differs")
    return value, source_sha, canonical_sha


def joint_horizon_policy_descriptor() -> dict[str, Any]:
    value, source_sha, canonical_sha = _load_policy()
    return {
        "evidence_contract": EVIDENCE_CONTRACT,
        "policy_contract": POLICY_CONTRACT,
        "method_version": METHOD_VERSION,
        "policy_source_sha256": source_sha,
        "policy_canonical_sha256": canonical_sha,
        "commercial_authority": value["authority"]["commercial_authority"],
        "production_activation": value["authority"]["production_activation"],
    }


def _sidecar_policy_descriptor() -> dict[str, str]:
    value = joint_horizon_policy_descriptor()
    return {
        key: str(value[key])
        for key in (
            "evidence_contract",
            "policy_contract",
            "method_version",
            "policy_source_sha256",
            "policy_canonical_sha256",
        )
    }


def _coherent_prefix_cap(
    raw_daily: Sequence[Decimal], calendar_mean_cap: Decimal
) -> tuple[list[Decimal], list[Decimal], list[int]]:
    if not raw_daily or calendar_mean_cap < 0:
        raise JointHorizonForecastError("joint forecast cap input differs")
    running = Decimal("0")
    prior = Decimal("0")
    cumulative: list[Decimal] = []
    daily: list[Decimal] = []
    hits: list[int] = []
    for day, raw in enumerate(raw_daily, 1):
        if not raw.is_finite():
            raise JointHorizonForecastError("joint forecast path is not finite")
        running += max(raw, Decimal("0"))
        ceiling = Decimal(day) * calendar_mean_cap
        if running > ceiling:
            hits.append(day)
        value = min(running, ceiling)
        if value < prior:
            raise JointHorizonForecastError("joint forecast cumulative path declined")
        cumulative.append(value)
        daily.append(value - prior)
        prior = value
    return cumulative, daily, hits


def _publish_paths(
    internal_point: Sequence[Decimal], internal_target: Sequence[Decimal]
) -> tuple[list[str], list[str], list[str], list[str]]:
    if len(internal_point) != len(internal_target) or not internal_point:
        raise JointHorizonForecastError("joint published path lengths differ")
    point_dec = [item.quantize(_UNITS, rounding=ROUND_HALF_UP) for item in internal_point]
    target_dec = [item.quantize(_UNITS, rounding=ROUND_HALF_UP) for item in internal_target]
    if any(point < 0 or target < point for point, target in zip(point_dec, target_dec, strict=True)):
        raise JointHorizonForecastError("joint published target path differs")
    if any(left > right for left, right in zip(point_dec, point_dec[1:])):
        raise JointHorizonForecastError("joint published point path declined")
    if any(left > right for left, right in zip(target_dec, target_dec[1:])):
        raise JointHorizonForecastError("joint published target path declined")
    protection_dec = [
        target - point for point, target in zip(point_dec, target_dec, strict=True)
    ]
    daily_dec: list[Decimal] = []
    prior = Decimal("0")
    for point in point_dec:
        daily_dec.append(point - prior)
        prior = point
    return (
        [_units(item) for item in point_dec],
        [_units(item) for item in target_dec],
        [_units(item) for item in protection_dec],
        [_units(item) for item in daily_dec],
    )


def _fva_decision(
    baseline_error: Decimal,
    complex_errors: Sequence[tuple[str, Decimal]],
) -> dict[str, Any]:
    best_name: str | None = None
    best_error: Decimal | None = None
    for name, error in complex_errors:
        if best_error is None or error < best_error:
            best_name, best_error = name, error
    if best_error is None:
        return {
            "baseline_model": "NAIVE",
            "best_complex_model": None,
            "selected_model": "NAIVE",
            "baseline_error_total": _internal(baseline_error),
            "best_complex_error_total": None,
            "relative_improvement": "0.000000",
            "threshold_numerator": 2,
            "threshold_denominator": 100,
            "complex_cleared_gate": False,
            "reason": "NO_ELIGIBLE_COMPLEX_CANDIDATE",
        }
    if baseline_error == 0:
        relative = Decimal("0")
        passed = False
        reason = "SIMPLE_BASELINE_SELECTED_ZERO_ERROR"
    else:
        relative = max((baseline_error - best_error) / baseline_error, Decimal("0"))
        passed = Decimal(100) * best_error <= Decimal(98) * baseline_error
        reason = (
            "JOINT_COMPLEX_MODEL_CLEARED_FVA_GATE"
            if passed
            else "SIMPLE_BASELINE_SELECTED_COMPLEX_DID_NOT_CLEAR_FVA_GATE"
        )
    return {
        "baseline_model": "NAIVE",
        "best_complex_model": best_name,
        "selected_model": best_name if passed else "NAIVE",
        "baseline_error_total": _internal(baseline_error),
        "best_complex_error_total": _internal(best_error),
        "relative_improvement": _metric(relative),
        "threshold_numerator": 2,
        "threshold_denominator": 100,
        "complex_cleared_gate": passed,
        "reason": reason,
    }


def _joint_confidence(
    absolute_error_total: Decimal,
    actual_total: Decimal,
    unknown_availability: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if actual_total == 0:
        confidence = "LOW"
        reasons.append("JOINT_EVALUATION_ACTUAL_SCALE_ZERO")
    elif Decimal(5) * absolute_error_total <= actual_total:
        confidence = "HIGH"
    elif Decimal(2) * absolute_error_total <= actual_total:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    if unknown_availability:
        confidence = "LOW"
        reasons.extend(
            (
                "UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK",
                "AVAILABILITY_COVERAGE_LIMITS_PROTECTION",
            )
        )
    return confidence, sorted(set(reasons))


def _partition(
    ordered: Sequence[DemandObservation],
    *,
    start: int,
    end: int,
    minimum: int,
) -> tuple[dict[str, Any], list[int]]:
    indices = list(range(start, end))
    planned = [ordered[index].business_date.isoformat() for index in indices]
    censored = [
        ordered[index].business_date.isoformat()
        for index in indices
        if any(item.inventory_state == "STOCKOUT" for item in ordered[index : index + 17])
    ]
    usable = [index for index in indices if ordered[index].business_date.isoformat() not in censored]
    return {
        "planned_dates": planned,
        "usable_dates": [ordered[index].business_date.isoformat() for index in usable],
        "censored_dates": censored,
        "minimum_count": minimum,
    }, usable


def _origin_path(
    model: str,
    origin: int,
    normalized: Sequence[Decimal],
    parent_policy: Any,
) -> tuple[list[Decimal], list[Decimal], Decimal, list[int], list[Decimal]]:
    raw = _predict(model, normalized[:origin], 17, parent_policy, None)
    if len(raw) != 17 or any(not item.is_finite() for item in raw):
        raise DevelopmentForecastError("joint model path differs")
    cap = _mean(normalized[:origin]) * Decimal(
        str(parent_policy.values["rate_cap"]["maximum_calendar_mean_multiplier"])
    )
    cumulative, daily, hits = _coherent_prefix_cap(raw, cap)
    return list(raw), cumulative, cap, hits, daily


def _cumulative(values: Sequence[Decimal]) -> list[Decimal]:
    total = Decimal("0")
    result: list[Decimal] = []
    for value in values:
        total += value
        result.append(total)
    return result


def _selection_record(
    model: str,
    origins: Sequence[int],
    ordered: Sequence[DemandObservation],
    normalized: Sequence[Decimal],
    parent_policy: Any,
) -> tuple[dict[str, Any], Decimal | None]:
    paths: list[dict[str, Any]] = []
    forecast_by_origin: list[list[Decimal]] = []
    actual_by_origin: list[list[Decimal]] = []
    try:
        for origin in origins:
            raw, cumulative, cap, hits, daily = _origin_path(
                model, origin, normalized, parent_policy
            )
            actual_daily = list(normalized[origin : origin + 17])
            actual_cumulative = _cumulative(actual_daily)
            forecast_by_origin.append(daily)
            actual_by_origin.append(actual_daily)
            paths.append(
                {
                    "origin_date": ordered[origin].business_date.isoformat(),
                    "calendar_mean_cap": _internal(cap),
                    "cap_hit_days": hits,
                    "forecast_daily": [_internal(item) for item in daily],
                    "forecast_cumulative": [_internal(item) for item in cumulative],
                    "actual_daily": [_internal(item) for item in actual_daily],
                    "actual_cumulative": [_internal(item) for item in actual_cumulative],
                }
            )
    except (_InapplicableModel, DevelopmentForecastError):
        return {
            "model": model,
            "status": "INELIGIBLE",
            "reason_codes": ["JOINT_CANDIDATE_INELIGIBLE_AT_COMMON_SELECTION_ORIGIN"],
            "selection_origin_dates": None,
            "origin_paths": None,
            "segments": None,
            "daily_mae": None,
            "maximum_prefix_absolute_error_mean": None,
            "joint_absolute_error_total": None,
            "joint_actual_total": None,
            "joint_score": None,
            "selected": False,
        }, None

    segments: dict[str, Any] = {}
    total_error = Decimal("0")
    total_actual = Decimal("0")
    for segment_id, start, end in (("A", 0, 3), ("B", 3, 10), ("C", 10, 17)):
        forecasts = [sum(path[start:end], Decimal("0")) for path in forecast_by_origin]
        actuals = [sum(path[start:end], Decimal("0")) for path in actual_by_origin]
        errors = [abs(forecast - actual) for forecast, actual in zip(forecasts, actuals, strict=True)]
        length = end - start
        mae_daily = sum(errors, Decimal("0")) / (Decimal(len(origins)) * Decimal(length))
        actual_daily = sum((abs(item) for item in actuals), Decimal("0")) / (
            Decimal(len(origins)) * Decimal(length)
        )
        contribution = Decimal(length) / Decimal(17) * mae_daily
        total_error += sum(errors, Decimal("0"))
        total_actual += sum((abs(item) for item in actuals), Decimal("0"))
        segments[segment_id] = {
            "start_day": start + 1,
            "end_day": end,
            "length": length,
            "forecast_totals": [_internal(item) for item in forecasts],
            "actual_totals": [_internal(item) for item in actuals],
            "absolute_errors": [_internal(item) for item in errors],
            "mae_daily": _metric(mae_daily),
            "actual_daily": _metric(actual_daily),
            "contribution": _metric(contribution),
        }
    daily_abs = sum(
        (
            abs(forecast - actual)
            for forecasts, actuals in zip(forecast_by_origin, actual_by_origin, strict=True)
            for forecast, actual in zip(forecasts, actuals, strict=True)
        ),
        Decimal("0"),
    )
    maximum_prefix = []
    for forecasts, actuals in zip(forecast_by_origin, actual_by_origin, strict=True):
        maximum_prefix.append(
            max(
                abs(forecast - actual)
                for forecast, actual in zip(
                    _cumulative(forecasts), _cumulative(actuals), strict=True
                )
            )
        )
    denominator = total_actual if total_actual > 0 else Decimal(17 * len(origins))
    score = total_error / denominator
    return {
        "model": model,
        "status": "ELIGIBLE",
        "reason_codes": [],
        "selection_origin_dates": [
            ordered[index].business_date.isoformat() for index in origins
        ],
        "origin_paths": paths,
        "segments": segments,
        "daily_mae": _metric(daily_abs / Decimal(17 * len(origins))),
        "maximum_prefix_absolute_error_mean": _metric(_mean(maximum_prefix)),
        "joint_absolute_error_total": _internal(total_error),
        "joint_actual_total": _internal(total_actual),
        "joint_score": _metric(score),
        "selected": False,
    }, total_error


def _paths_for_selected(
    model: str,
    origins: Sequence[int],
    normalized: Sequence[Decimal],
    parent_policy: Any,
) -> list[tuple[list[Decimal], list[Decimal], Decimal, list[int], list[Decimal]]]:
    result = []
    try:
        for origin in origins:
            result.append(_origin_path(model, origin, normalized, parent_policy))
    except (_InapplicableModel, DevelopmentForecastError) as exc:
        raise JointHorizonForecastError(
            "selected joint model path is unavailable",
            reason_code="JOINT_SELECTED_MODEL_PATH_UNAVAILABLE",
        ) from exc
    return result


def _nearest_rank(paths: Sequence[Sequence[Decimal]], day: int) -> Decimal:
    values = sorted(path[day] for path in paths)
    if not values:
        raise JointHorizonForecastError("joint quantile sample is empty")
    rank = (9 * len(values) + 9) // 10
    return values[rank - 1]


def _calibration_evidence(
    origins: Sequence[int],
    origin_paths: Sequence[tuple[list[Decimal], list[Decimal], Decimal, list[int], list[Decimal]]],
    ordered: Sequence[DemandObservation],
    normalized: Sequence[Decimal],
    final_point: Sequence[Decimal],
) -> tuple[dict[str, Any], list[list[Decimal]], list[Decimal]]:
    daily_shortfalls: list[list[Decimal]] = []
    cumulative_shortfalls: list[list[Decimal]] = []
    samples: list[list[Decimal]] = []
    hits: dict[str, list[int]] = {}
    for origin, path in zip(origins, origin_paths, strict=True):
        _raw, _cumulative_point, _cap, cap_hits, daily_point = path
        actual_daily = normalized[origin : origin + 17]
        shortfall = [
            max(actual - point, Decimal("0"))
            for actual, point in zip(actual_daily, daily_point, strict=True)
        ]
        cumulative = _cumulative(shortfall)
        sample = [
            point + residual
            for point, residual in zip(final_point, cumulative, strict=True)
        ]
        daily_shortfalls.append(shortfall)
        cumulative_shortfalls.append(cumulative)
        samples.append(sample)
        hits[ordered[origin].business_date.isoformat()] = cap_hits
    target = [_nearest_rank(samples, day) for day in range(17)]
    if any(left > right for left, right in zip(target, target[1:])) or any(
        target_value < point
        for target_value, point in zip(target, final_point, strict=True)
    ):
        raise JointHorizonForecastError("joint target path is incoherent")
    point_pub, target_pub, protection_pub, _daily_pub = _publish_paths(
        final_point, target
    )
    del point_pub
    value: dict[str, Any] = {
        "origin_dates": [ordered[index].business_date.isoformat() for index in origins],
        "minimum_count": 8,
        "quantile_numerator": 9,
        "quantile_denominator": 10,
        "nearest_rank": (9 * len(origins) + 9) // 10,
        "cap_hit_days_by_origin": hits,
        "daily_shortfall_paths": [[_internal(item) for item in path] for path in daily_shortfalls],
        "cumulative_daily_shortfall_paths": [[_internal(item) for item in path] for path in cumulative_shortfalls],
        "target_sample_paths": [[_internal(item) for item in path] for path in samples],
        "internal_target_path": [_internal(item) for item in target],
        "published_target_path": target_pub,
        "published_protection_path": protection_pub,
    }
    value["calibration_sha256"] = _nested_hash(value, "calibration_sha256")
    return value, cumulative_shortfalls, target


def _evaluation_evidence(
    origins: Sequence[int],
    paths: Sequence[tuple[list[Decimal], list[Decimal], Decimal, list[int], list[Decimal]]],
    ordered: Sequence[DemandObservation],
    normalized: Sequence[Decimal],
    calibration_shortfalls: Sequence[Sequence[Decimal]],
    unknown_availability: bool,
) -> dict[str, Any]:
    point_paths = [item[1] for item in paths]
    point_daily_paths = [item[4] for item in paths]
    actual_daily_paths = [list(normalized[origin : origin + 17]) for origin in origins]
    actual_paths = [_cumulative(item) for item in actual_daily_paths]
    target_paths: list[list[Decimal]] = []
    for point in point_paths:
        samples = [
            [p + r for p, r in zip(point, residual, strict=True)]
            for residual in calibration_shortfalls
        ]
        target_paths.append([_nearest_rank(samples, day) for day in range(17)])

    point_metrics: dict[str, Any] = {}
    for label, index in (("H3", 2), ("H10", 9), ("H17", 16)):
        signed = [
            point[index] - actual[index]
            for point, actual in zip(point_paths, actual_paths, strict=True)
        ]
        absolute = [abs(item) for item in signed]
        actual_sum = sum((abs(path[index]) for path in actual_paths), Decimal("0"))
        point_metrics[label] = {
            "signed_error_sum": _internal(sum(signed, Decimal("0"))),
            "absolute_error_sum": _internal(sum(absolute, Decimal("0"))),
            "actual_absolute_sum": _internal(actual_sum),
            "bias": _metric(_mean(signed)),
            "mae": _metric(_mean(absolute)),
            "wape": (
                None
                if actual_sum == 0
                else _metric(sum(absolute, Decimal("0")) / actual_sum)
            ),
        }
    all_daily_abs = [
        abs(point - actual)
        for point_path, actual_path in zip(point_daily_paths, actual_daily_paths, strict=True)
        for point, actual in zip(point_path, actual_path, strict=True)
    ]
    max_prefix = [
        max(abs(point - actual) for point, actual in zip(point_path, actual_path, strict=True))
        for point_path, actual_path in zip(point_paths, actual_paths, strict=True)
    ]
    point_metrics["daily_mae"] = _metric(_mean(all_daily_abs))
    point_metrics["maximum_prefix_absolute_error_mean"] = _metric(_mean(max_prefix))

    target_metrics: dict[str, Any] = {}
    for label, index in (("H3", 2), ("H10", 9), ("H17", 16)):
        shortfalls = [
            max(actual[index] - target[index], Decimal("0"))
            for target, actual in zip(target_paths, actual_paths, strict=True)
        ]
        covered = sum(
            actual[index] <= target[index]
            for target, actual in zip(target_paths, actual_paths, strict=True)
        )
        target_metrics[label] = {
            "shortfall_sum": _internal(sum(shortfalls, Decimal("0"))),
            "mean_shortfall": _metric(_mean(shortfalls)),
            "covered_count": covered,
            "total_count": len(origins),
            "service_coverage": _metric(Decimal(covered) / Decimal(len(origins))),
        }
    maximum_shortfall = [
        max(
            max(actual - target, Decimal("0"))
            for target, actual in zip(target_path, actual_path, strict=True)
        )
        for target_path, actual_path in zip(target_paths, actual_paths, strict=True)
    ]
    target_metrics["maximum_prefix_shortfall_mean"] = _metric(_mean(maximum_shortfall))

    joint_error = Decimal("0")
    joint_actual = Decimal("0")
    for point_daily, actual_daily in zip(point_daily_paths, actual_daily_paths, strict=True):
        for start, end in ((0, 3), (3, 10), (10, 17)):
            forecast_total = sum(point_daily[start:end], Decimal("0"))
            actual_total = sum(actual_daily[start:end], Decimal("0"))
            joint_error += abs(forecast_total - actual_total)
            joint_actual += abs(actual_total)
    confidence, reasons = _joint_confidence(
        joint_error, joint_actual, unknown_availability
    )
    joint_objective = {
        "absolute_error_total": _internal(joint_error),
        "actual_total": _internal(joint_actual),
        "wape": None if joint_actual == 0 else _metric(joint_error / joint_actual),
        "daily_mae": _metric(_mean(all_daily_abs)),
    }
    value: dict[str, Any] = {
        "origin_dates": [ordered[index].business_date.isoformat() for index in origins],
        "minimum_count": 4,
        "cap_hit_days_by_origin": {
            ordered[origin].business_date.isoformat(): path[3]
            for origin, path in zip(origins, paths, strict=True)
        },
        "point_metrics": point_metrics,
        "target_metrics": target_metrics,
        "joint_objective": joint_objective,
        "joint_confidence": confidence,
        "reason_codes": reasons,
    }
    value["evaluation_sha256"] = _nested_hash(value, "evaluation_sha256")
    return value


def _validate_descriptor(value: Any, expected: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not _strict_equal(value, expected):
        raise JointHorizonForecastError(f"{field} differs")
    return deepcopy(dict(value))


def _validated_parent_projection_descriptor(value: Any) -> dict[str, Any]:
    keys = {
        "contract", "projection_sha256", "artifact_sha256", "artifact_path",
        "workspace_id", "workspace_manifest_sha256", "sidecars_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise JointHorizonForecastError("parent projection descriptor differs")
    result = deepcopy(dict(value))
    if (
        result["contract"] != "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3"
        or any(
            not isinstance(result[key], str) or not _HEX64.fullmatch(result[key])
            for key in (
                "projection_sha256", "artifact_sha256", "workspace_id",
                "workspace_manifest_sha256", "sidecars_sha256",
            )
        )
        or result["artifact_path"]
        != f"private-research/workspaces/{result['workspace_id']}/projection.json"
    ):
        raise JointHorizonForecastError("parent projection descriptor differs")
    return result


def _validated_delta_descriptor(value: Any) -> dict[str, Any]:
    keys = {
        "contract", "delta_id", "raw_csv_sha256", "normalized_row_set_sha256",
        "raw_storage_key", "envelope_storage_key",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        raise JointHorizonForecastError("creation evidence delta descriptor differs")
    result = deepcopy(dict(value))
    if (
        result["contract"]
        != "BUFFALO_PRIVATE_VARIANT_CREATION_EVIDENCE_DELTA_V1"
        or any(
            not isinstance(result[key], str) or not _HEX64.fullmatch(result[key])
            for key in ("delta_id", "raw_csv_sha256", "normalized_row_set_sha256")
        )
        or result["raw_storage_key"]
        != "private-research/corrected-v3-sources/sha256/"
        + result["raw_csv_sha256"]
        + ".csv"
        or result["envelope_storage_key"]
        != "private-research/corrected-v3-deltas/" + result["delta_id"] + ".json"
    ):
        raise JointHorizonForecastError("creation evidence delta descriptor differs")
    return result


def plan_joint_horizon_forecast(
    observations: Iterable[DemandObservation],
    *,
    variant_id: str,
    corrected_input_id: str,
    parent_projection: Mapping[str, Any],
    creation_evidence_delta: Mapping[str, Any],
    observations_sha256: str,
) -> dict[str, Any]:
    """Build one deterministic coherent 17-day evidence sidecar."""

    with localcontext(_context()):
        policy_value, _source_sha, _canonical_sha = _load_policy()
        if not isinstance(variant_id, str) or not _VARIANT_ID.fullmatch(variant_id):
            raise JointHorizonForecastError("joint forecast Variant ID differs")
        if (
            not isinstance(corrected_input_id, str)
            or not _HEX64.fullmatch(corrected_input_id)
            or not isinstance(observations_sha256, str)
            or not _HEX64.fullmatch(observations_sha256)
        ):
            raise JointHorizonForecastError("joint forecast input identity differs")
        parent_descriptor = _validated_parent_projection_descriptor(parent_projection)
        delta_descriptor = _validated_delta_descriptor(creation_evidence_delta)
        ordered, normalized = _validated_observations(tuple(observations))
        if (
            len(ordered) != 138
            or ordered[0].business_date != date(2026, 5, 4)
            or ordered[-1].business_date != date(2026, 9, 18)
        ):
            raise JointHorizonForecastError("joint forecast history interval differs")
        windows = policy_value["windows"]
        partitions: dict[str, Any] = {}
        partition_indices: dict[str, list[int]] = {}
        for name in ("selection", "calibration", "evaluation"):
            evidence, usable = _partition(
                ordered,
                start=int(windows[f"{name}_start_index"]),
                end=int(windows[f"{name}_end_exclusive"]),
                minimum=int(windows[f"{name}_minimum_common_origins"]),
            )
            partitions[name] = evidence
            partition_indices[name] = usable
            if len(usable) < evidence["minimum_count"]:
                raise JointHorizonForecastError(
                    f"joint {name} origin inventory is insufficient",
                    reason_code=f"JOINT_{name.upper()}_ORIGINS_INSUFFICIENT",
                )

        parent_policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        records: list[dict[str, Any]] = []
        errors: dict[str, Decimal] = {}
        for model in policy_value["candidates"]["order"]:
            record, error = _selection_record(
                model,
                partition_indices["selection"],
                ordered,
                normalized,
                parent_policy,
            )
            records.append(record)
            if error is not None:
                errors[model] = error
        if "NAIVE" not in errors:
            raise JointHorizonForecastError(
                "joint baseline is ineligible", reason_code="JOINT_BASELINE_INELIGIBLE"
            )
        decision = _fva_decision(
            errors["NAIVE"],
            [
                (model, errors[model])
                for model in policy_value["candidates"]["order"]
                if model != "NAIVE" and model in errors
            ],
        )
        selected = str(decision["selected_model"])
        for record in records:
            record["selected"] = record["model"] == selected and record["status"] == "ELIGIBLE"

        calibration_paths = _paths_for_selected(
            selected, partition_indices["calibration"], normalized, parent_policy
        )
        evaluation_paths = _paths_for_selected(
            selected, partition_indices["evaluation"], normalized, parent_policy
        )
        try:
            raw_final, final_point, final_cap, final_hits, final_daily = _origin_path(
                selected, len(normalized), normalized, parent_policy
            )
        except (_InapplicableModel, DevelopmentForecastError) as exc:
            raise JointHorizonForecastError(
                "selected joint final path is unavailable",
                reason_code="JOINT_SELECTED_MODEL_PATH_UNAVAILABLE",
            ) from exc
        calibration, shortfalls, final_target = _calibration_evidence(
            partition_indices["calibration"],
            calibration_paths,
            ordered,
            normalized,
            final_point,
        )
        unknown = any(item.inventory_state == "UNKNOWN" for item in ordered)
        evaluation = _evaluation_evidence(
            partition_indices["evaluation"],
            evaluation_paths,
            ordered,
            normalized,
            shortfalls,
            unknown,
        )
        published_point, published_target, published_protection, published_daily = _publish_paths(
            final_point, final_target
        )
        final_path: dict[str, Any] = {
            "forecast_origin": "2026-09-19",
            "target_start": "2026-09-19",
            "target_end": "2026-10-05",
            "calendar_mean_cap": _internal(final_cap),
            "cap_hit_days": final_hits,
            "raw_daily": [_internal(max(item, Decimal("0"))) for item in raw_final],
            "internal_capped_daily": [_internal(item) for item in final_daily],
            "internal_cumulative": [_internal(item) for item in final_point],
            "published_cumulative": published_point,
            "published_daily": published_daily,
        }
        final_path["path_sha256"] = _nested_hash(final_path, "path_sha256")
        sidecar_key = ":".join(
            (
                "joint17",
                variant_id,
                corrected_input_id,
                POLICY_CANONICAL_SHA256,
            )
        )
        reasons = [decision["reason"], *evaluation["reason_codes"]]
        if any(item.net_units < 0 for item in ordered):
            reasons.append("NEGATIVE_NET_DAYS_FLOORED_FOR_DEMAND_ONLY")
        if final_hits:
            reasons.append("DEVELOPMENT_RATE_CAP_APPLIED")
        reasons = sorted(set(str(item) for item in reasons))
        summaries: dict[str, Any] = {}
        for label, horizon, target_end in (
            ("H3", 3, "2026-09-21"),
            ("H10", 10, "2026-09-28"),
            ("H17", 17, "2026-10-05"),
        ):
            horizon_wape = evaluation["point_metrics"][label]["wape"]
            summaries[label] = {
                "horizon_days": horizon,
                "target_start": "2026-09-19",
                "target_end": target_end,
                "status": "CALCULATED_RESEARCH_ONLY",
                "primary_status": "CALCULATED",
                "selected_model": selected,
                "point_forecast_units": published_point[horizon - 1],
                "protection_units": published_protection[horizon - 1],
                "target_units": published_target[horizon - 1],
                "joint_confidence": evaluation["joint_confidence"],
                "horizon_evaluation_wape": horizon_wape,
                "reason_codes": reasons,
                "joint_sidecar_key": sidecar_key,
            }
        result: dict[str, Any] = {
            "contract": EVIDENCE_CONTRACT,
            "authority": "ZERO_AUTHORITY_RESEARCH_ONLY",
            "research_only": True,
            "commercial_authority": False,
            "production_activation": False,
            "variant_id": variant_id,
            "corrected_input_id": corrected_input_id,
            "parent_projection": parent_descriptor,
            "creation_evidence_delta": delta_descriptor,
            "joint_policy": _sidecar_policy_descriptor(),
            "history": {
                "start_date": "2026-05-04",
                "end_date": "2026-09-18",
                "day_count": 138,
                "observations_sha256": observations_sha256,
                "availability_basis": (
                    "UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK"
                    if unknown
                    else "DAILY_INVENTORY_STATE_CAPTURED"
                ),
            },
            "origin_partitions": partitions,
            "candidate_records": records,
            "selected_model": selected,
            "fva": decision,
            "final_path": final_path,
            "calibration": calibration,
            "evaluation": evaluation,
            "summaries": summaries,
            "limitations": sorted(
                {
                    "DEVELOPMENT_RESEARCH_ONLY",
                    "NO_OPERATIONAL_OR_PURCHASING_AUTHORITY",
                    *(
                        ["UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK"]
                        if unknown
                        else []
                    ),
                }
            ),
            "zero_authority": dict(_ZERO_AUTHORITY),
        }
        result["sidecar_sha256"] = _logical_sha(result)
        return result


def validate_joint_horizon_forecast_evidence(
    value: Mapping[str, Any],
    observations: Iterable[DemandObservation],
    *,
    expected_variant_id: str,
    expected_corrected_input_id: str,
    expected_parent_projection: Mapping[str, Any],
    expected_creation_evidence_delta: Mapping[str, Any],
    expected_observations_sha256: str,
) -> dict[str, Any]:
    """Fully regenerate joint evidence; an outer self-hash is never enough."""

    if not isinstance(value, Mapping) or set(value) != _TOP_LEVEL_KEYS:
        raise JointHorizonForecastError("joint forecast evidence shape differs")
    if (
        value.get("contract") != EVIDENCE_CONTRACT
        or value.get("authority") != "ZERO_AUTHORITY_RESEARCH_ONLY"
        or value.get("research_only") is not True
        or value.get("commercial_authority") is not False
        or value.get("production_activation") is not False
        or not _strict_equal(value.get("zero_authority"), _ZERO_AUTHORITY)
        or value.get("sidecar_sha256") != _logical_sha(value)
    ):
        raise JointHorizonForecastError("joint forecast evidence identity differs")
    expected = plan_joint_horizon_forecast(
        observations,
        variant_id=expected_variant_id,
        corrected_input_id=expected_corrected_input_id,
        parent_projection=_validate_descriptor(
            value.get("parent_projection"), expected_parent_projection, "parent projection"
        ),
        creation_evidence_delta=_validate_descriptor(
            value.get("creation_evidence_delta"),
            expected_creation_evidence_delta,
            "creation evidence delta",
        ),
        observations_sha256=expected_observations_sha256,
    )
    if not _strict_equal(value, expected):
        raise JointHorizonForecastError("joint forecast semantic replay differs")
    return expected


__all__ = [
    "EVIDENCE_CONTRACT",
    "METHOD_VERSION",
    "POLICY_CANONICAL_SHA256",
    "POLICY_CONTRACT",
    "POLICY_SOURCE_SHA256",
    "JointHorizonForecastError",
    "joint_horizon_policy_descriptor",
    "plan_joint_horizon_forecast",
    "validate_joint_horizon_forecast_evidence",
]
