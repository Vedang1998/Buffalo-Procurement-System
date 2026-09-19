"""Deterministic development-only demand planning with frozen evidence.

This module is a bounded synthetic acceptance implementation.  It does not
activate production forecasting, define commercial policy, or read mutable
supplier prices.  Callers must separately attest the synthetic fixture and
freeze the returned evidence into the Monday run input manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .forecasting import (
    DemandObservation,
    INVENTORY_STATES,
    canonical_evidence_json,
    canonical_evidence_sha256,
)


CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V1"
POLICY_CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_POLICY_V1"
METHOD_VERSION = "DEVELOPMENT_ROLLING_ORIGIN_V1"
CAPABILITY_ENV = "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST"
FIXTURE_META_KEY = "synthetic_development_forecast_contract"
FIXTURE_CONTRACT = "BUFFALO_SYNTHETIC_DEVELOPMENT_FORECAST_V1"
POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "development_forecast_policy_v1.json"
)

UNITS = Decimal("0.0001")
VELOCITY = Decimal("0.000001")
METRIC = Decimal("0.000001")
_SHA256_LENGTH = 64


class DevelopmentForecastError(ValueError):
    """The development policy, input series, or evidence is invalid."""


class _InapplicableModel(ValueError):
    pass


@dataclass(frozen=True)
class DevelopmentForecastPolicy:
    values: Mapping[str, Any]
    source_sha256: str
    canonical_sha256: str

    def evidence(self) -> dict[str, Any]:
        return {
            "contract": POLICY_CONTRACT,
            "method_version": METHOD_VERSION,
            "source_file": POLICY_PATH.name,
            "source_sha256": self.source_sha256,
            "canonical_sha256": self.canonical_sha256,
            "commercial_authority": False,
            "production_activation": False,
        }


@dataclass(frozen=True)
class _Metrics:
    observation_count: int
    bias: Decimal
    mae: Decimal
    wape: Decimal | None
    mase: Decimal | None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "observation_count": self.observation_count,
            "bias": str(self.bias.quantize(METRIC, rounding=ROUND_HALF_UP)),
            "mae": str(self.mae.quantize(METRIC, rounding=ROUND_HALF_UP)),
            "wape": (
                None
                if self.wape is None
                else str(self.wape.quantize(METRIC, rounding=ROUND_HALF_UP))
            ),
            "mase": (
                None
                if self.mase is None
                else str(self.mase.quantize(METRIC, rounding=ROUND_HALF_UP))
            ),
        }


@dataclass(frozen=True)
class DevelopmentForecastPlan:
    """Typed view of one canonical evidence object."""

    evidence: Mapping[str, Any]

    @property
    def status(self) -> str:
        return str(self.evidence["status"])

    @property
    def selected_model(self) -> str | None:
        value = self.evidence.get("selected_model")
        return None if value is None else str(value)

    @property
    def method_version(self) -> str:
        return str(self.evidence["method_version"])

    @property
    def history_start(self) -> date:
        return date.fromisoformat(str(self.evidence["history_start"]))

    @property
    def history_end(self) -> date:
        return date.fromisoformat(str(self.evidence["history_end"]))

    @property
    def forecast_daily_velocity(self) -> Decimal:
        return Decimal(str(self.evidence["forecast_daily_velocity"]))

    @property
    def point_forecast_units(self) -> Decimal:
        return Decimal(str(self.evidence["point_forecast_units"]))

    @property
    def forecast_units(self) -> Decimal:
        return self.point_forecast_units

    @property
    def protection_units(self) -> Decimal:
        value = self.evidence.get("protection_units")
        if value is None:
            raise DevelopmentForecastError("empirical protection is unavailable")
        return Decimal(str(value))

    @property
    def target_units(self) -> Decimal:
        value = self.evidence.get("target_units")
        if value is None:
            raise DevelopmentForecastError("development target is unavailable")
        return Decimal(str(value))

    @property
    def horizon_days(self) -> int:
        return int(self.evidence["horizon_days"])

    @property
    def confidence(self) -> str:
        return str(self.evidence["confidence"])

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(str(item) for item in self.evidence.get("reason_codes", []))

    @property
    def calendar_velocity(self) -> Decimal:
        return Decimal(str(self.evidence["calendar_velocity"]))

    @property
    def in_stock_velocity(self) -> Decimal | None:
        value = self.evidence.get("in_stock_velocity")
        return None if value is None else Decimal(str(value))

    @property
    def outlier_capped_days(self) -> int:
        return 0

    @property
    def evidence_sha256(self) -> str:
        return str(self.evidence["sha256"])

    def to_json_dict(self) -> dict[str, Any]:
        return json.loads(canonical_evidence_json(self.evidence))


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise DevelopmentForecastError(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DevelopmentForecastError(
            f"{field} must be a finite decimal"
        ) from exc
    if not parsed.is_finite():
        raise DevelopmentForecastError(f"{field} must be a finite decimal")
    return parsed


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DevelopmentForecastError(f"{field} must be a positive whole number")
    return value


_WEEKDAY_INDEX = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


def calculate_calendar_protection_horizon(
    *,
    evaluation_at: datetime,
    timezone_name: str,
    order_days: Sequence[str],
    order_cutoff_local: time,
    expected_delivery_days: Sequence[str],
    order_cycle_days: int,
    lead_time_days: int,
    lead_time_variability_days: object,
) -> dict[str, Any]:
    """Freeze the next review-to-receipt interval from confirmed calendars.

    The current order opportunity is already being evaluated.  Protection
    therefore runs until the first permitted receipt following the *next*
    confirmed order opportunity.  Lead-time variability remains explicit
    evidence but is not converted into a second demand allowance; empirical
    full-horizon shortfalls provide the only protection addition.  No request
    date or caller-selected horizon participates.
    """

    if evaluation_at.tzinfo is None:
        raise DevelopmentForecastError("evaluation_at must be timezone-aware")
    try:
        zone = ZoneInfo(str(timezone_name))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DevelopmentForecastError("vendor timezone is invalid") from exc
    if not isinstance(order_cutoff_local, time) or order_cutoff_local.tzinfo is not None:
        raise DevelopmentForecastError("order cutoff must be timezone-free")
    order_cycle = _positive_int(order_cycle_days, "order_cycle_days")
    if isinstance(lead_time_days, bool) or not isinstance(lead_time_days, int) or lead_time_days < 0:
        raise DevelopmentForecastError("lead_time_days must be nonnegative whole days")
    variability = _decimal(
        lead_time_variability_days, "lead_time_variability_days"
    )
    if variability < 0:
        raise DevelopmentForecastError("lead_time_variability_days cannot be negative")
    order_indexes = {
        _WEEKDAY_INDEX[str(day).strip().upper()]
        for day in order_days
        if str(day).strip().upper() in _WEEKDAY_INDEX
    }
    delivery_indexes = {
        _WEEKDAY_INDEX[str(day).strip().upper()]
        for day in expected_delivery_days
        if str(day).strip().upper() in _WEEKDAY_INDEX
    }
    if len(order_indexes) != len(tuple(order_days)) or not order_indexes:
        raise DevelopmentForecastError("order day calendar is invalid")
    if len(delivery_indexes) != len(tuple(expected_delivery_days)) or not delivery_indexes:
        raise DevelopmentForecastError("delivery day calendar is invalid")
    local_evaluation = evaluation_at.astimezone(zone)
    if (
        local_evaluation.weekday() not in order_indexes
        or local_evaluation.timetz().replace(tzinfo=None) >= order_cutoff_local
    ):
        raise DevelopmentForecastError("evaluation is not an open order opportunity")

    next_order = None
    for offset in range(1, 15):
        candidate = local_evaluation.date() + timedelta(days=offset)
        if candidate.weekday() in order_indexes:
            next_order = candidate
            break
    if next_order is None:
        raise DevelopmentForecastError("next order opportunity is unavailable")
    next_order_gap = (next_order - local_evaluation.date()).days
    if next_order_gap != order_cycle:
        raise DevelopmentForecastError(
            "confirmed order cycle differs from the order-day calendar"
        )

    earliest_receipt = next_order + timedelta(days=lead_time_days)
    next_receipt = None
    for offset in range(0, 15):
        candidate = earliest_receipt + timedelta(days=offset)
        if candidate.weekday() in delivery_indexes:
            next_receipt = candidate
            break
    if next_receipt is None:
        raise DevelopmentForecastError("next delivery opportunity is unavailable")
    calendar_days = (next_receipt - local_evaluation.date()).days
    variability_days = int(variability.to_integral_value(rounding=ROUND_CEILING))
    horizon_days = calendar_days
    if horizon_days < 1 or horizon_days > 31:
        raise DevelopmentForecastError("calendar protection horizon is outside bounds")
    return {
        "basis": "NEXT_CONFIRMED_ORDER_TO_FIRST_PERMITTED_RECEIPT_V1",
        "timezone_name": str(timezone_name),
        "evaluation_at": local_evaluation.isoformat(),
        "order_days": [
            name for name, index in _WEEKDAY_INDEX.items() if index in order_indexes
        ],
        "order_cutoff_local": order_cutoff_local.isoformat(),
        "expected_delivery_days": [
            name for name, index in _WEEKDAY_INDEX.items() if index in delivery_indexes
        ],
        "order_cycle_days": order_cycle,
        "next_order_date": next_order.isoformat(),
        "lead_time_days": lead_time_days,
        "next_receipt_date": next_receipt.isoformat(),
        "calendar_days_until_receipt": calendar_days,
        "lead_time_variability_days": str(variability),
        "variability_days_ceiling": variability_days,
        "variability_treatment": "EVIDENCE_ONLY_NOT_DEMAND_HORIZON",
        "horizon_days": horizon_days,
    }


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise DevelopmentForecastError(f"{field} key inventory differs")


def load_development_forecast_policy(
    path: Path = POLICY_PATH,
) -> DevelopmentForecastPolicy:
    """Load and deeply validate the checked-in development policy."""

    try:
        raw = path.read_bytes()
        values = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DevelopmentForecastError(
            "development forecast policy is unreadable"
        ) from exc
    if not isinstance(values, dict):
        raise DevelopmentForecastError("development forecast policy must be an object")
    _require_exact_keys(
        values,
        {
            "abc",
            "candidate_order",
            "category_shrinkage",
            "commercial_authority",
            "contract",
            "damped_ets",
            "fva",
            "method_version",
            "naive",
            "production_activation",
            "protection",
            "rate_cap",
            "selection_metric",
            "seasonal_naive",
            "tsb",
            "windows",
            "xyz",
        },
        "development forecast policy",
    )
    candidate_order = values.get("candidate_order")
    if (
        values.get("contract") != POLICY_CONTRACT
        or values.get("method_version") != METHOD_VERSION
        or values.get("commercial_authority") is not False
        or values.get("production_activation") is not False
        or values.get("selection_metric") != "WAPE_THEN_MAE"
        or candidate_order
        != [
            "NAIVE",
            "SEASONAL_NAIVE",
            "DAMPED_ETS",
            "TSB",
            "CATEGORY_SHRINKAGE",
        ]
    ):
        raise DevelopmentForecastError("development forecast policy identity differs")
    for field in (
        "abc",
        "category_shrinkage",
        "damped_ets",
        "fva",
        "naive",
        "protection",
        "rate_cap",
        "seasonal_naive",
        "tsb",
        "windows",
        "xyz",
    ):
        if not isinstance(values.get(field), dict):
            raise DevelopmentForecastError(f"policy {field} must be an object")
    windows = values["windows"]
    _require_exact_keys(
        windows,
        {
            "calibration_days",
            "evaluation_days",
            "minimum_evaluation_origins",
            "minimum_selection_origins",
            "minimum_training_days",
            "selection_origin_days",
        },
        "policy windows",
    )
    for key in windows:
        _positive_int(windows[key], f"windows.{key}")
    if windows["calibration_days"] <= windows["minimum_selection_origins"]:
        raise DevelopmentForecastError("policy calibration window is too short")
    if values["abc"].get("basis") != "HISTORICAL_GROSS_PROFIT_DOLLARS":
        raise DevelopmentForecastError("policy ABC basis differs")
    _positive_int(
        values["abc"].get("classification_period_days"),
        "abc.classification_period_days",
    )
    if values["protection"].get("residual_definition") != (
        "MAX_ACTUAL_HORIZON_MINUS_POINT_FORECAST_ZERO"
    ):
        raise DevelopmentForecastError("policy protection residual differs")
    decimal_bounds = (
        (values["abc"], "a_cumulative_share", Decimal("0"), Decimal("1")),
        (values["abc"], "b_incremental_share", Decimal("0"), Decimal("1")),
        (values["damped_ets"], "alpha", Decimal("0"), Decimal("1")),
        (values["damped_ets"], "beta", Decimal("0"), Decimal("1")),
        (values["damped_ets"], "phi", Decimal("0"), Decimal("1")),
        (values["fva"], "minimum_relative_improvement", Decimal("0"), Decimal("1")),
        (values["protection"], "quantile", Decimal("0"), Decimal("1")),
        (values["rate_cap"], "maximum_calendar_mean_multiplier", Decimal("1"), Decimal("100")),
        (values["tsb"], "alpha_probability", Decimal("0"), Decimal("1")),
        (values["tsb"], "alpha_size", Decimal("0"), Decimal("1")),
        (values["tsb"], "intermittent_zero_share", Decimal("0"), Decimal("1")),
        (values["xyz"], "x_max_coefficient_of_variation", Decimal("0"), Decimal("100")),
        (values["xyz"], "y_max_coefficient_of_variation", Decimal("0"), Decimal("100")),
    )
    for obj, key, lower, upper in decimal_bounds:
        parsed = _decimal(obj.get(key), key)
        if parsed <= lower or parsed > upper:
            raise DevelopmentForecastError(f"policy {key} is outside its bound")
    return DevelopmentForecastPolicy(
        values=values,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_sha256=canonical_evidence_sha256(values),
    )


def development_forecast_contract_for_run(
    conn: Any, *, offer_resolution_contract: str | None
) -> str | None:
    """Resolve the server-owned synthetic development capability.

    The exact fixture marker is authoritative.  A registered fixture cannot
    silently fall back when its process capability is missing or malformed.
    """

    rows = conn.execute(
        "SELECT key,value FROM meta WHERE key=ANY(%s) ORDER BY key",
        (
            [
                FIXTURE_META_KEY,
                "synthetic_multivendor_acceptance_contract",
            ],
        ),
    ).fetchall()
    metadata = {str(key): str(value) for key, value in rows}
    marker = metadata.get(FIXTURE_META_KEY)
    if marker is None:
        return None
    if marker != FIXTURE_CONTRACT:
        raise DevelopmentForecastError(
            "synthetic development forecast fixture marker differs"
        )
    if (
        metadata.get("synthetic_multivendor_acceptance_contract")
        != "BUFFALO_SYNTHETIC_MULTIVENDOR_ACCEPTANCE_V2"
        or os.getenv(CAPABILITY_ENV) != "1"
        or os.getenv("BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO") != "1"
        or os.getenv("BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS") != "1"
        or os.getenv("BUFFALO_RUNTIME_MODE", "").strip().upper()
        not in {"SYNTHETIC_DEMO", "AUTOMATED_TEST"}
        or offer_resolution_contract != "SYNTHETIC_CONFIRMED_SELECTION_V1"
    ):
        raise DevelopmentForecastError(
            "synthetic development forecast is not authorized"
        )
    return CONTRACT


def _validated_observations(
    observations: Iterable[DemandObservation],
) -> tuple[tuple[DemandObservation, ...], tuple[Decimal, ...]]:
    indexed: dict[date, DemandObservation] = {}
    for raw in observations:
        if not isinstance(raw, DemandObservation):
            raise DevelopmentForecastError("demand observations have the wrong type")
        if not isinstance(raw.business_date, date) or isinstance(
            raw.business_date, datetime
        ):
            raise DevelopmentForecastError("business_date must be a date")
        if raw.business_date in indexed:
            raise DevelopmentForecastError("one observation per date is required")
        state = str(raw.inventory_state).strip().upper()
        if state not in INVENTORY_STATES:
            raise DevelopmentForecastError("inventory state differs")
        indexed[raw.business_date] = DemandObservation(
            raw.business_date,
            _decimal(raw.net_units, "net_units"),
            state,
        )
    if not indexed:
        raise DevelopmentForecastError("at least one observation is required")
    ordered = tuple(indexed[key] for key in sorted(indexed))
    if (ordered[-1].business_date - ordered[0].business_date).days + 1 != len(ordered):
        raise DevelopmentForecastError("demand observations must be calendar complete")
    normalized: list[Decimal] = []
    prior_uncensored: list[Decimal] = []
    for item in ordered:
        if item.inventory_state == "STOCKOUT":
            replacement = (
                sum(prior_uncensored[-7:], Decimal("0"))
                / Decimal(len(prior_uncensored[-7:]))
                if prior_uncensored
                else Decimal("0")
            )
            normalized.append(max(replacement, Decimal("0")))
            continue
        value = max(item.net_units, Decimal("0"))
        normalized.append(value)
        prior_uncensored.append(value)
    return ordered, tuple(normalized)


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise _InapplicableModel("EMPTY_TRAINING_WINDOW")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _predict(
    name: str,
    history: Sequence[Decimal],
    horizon: int,
    policy: DevelopmentForecastPolicy,
    category_daily_velocity: Decimal | None,
) -> list[Decimal]:
    values = policy.values
    if name == "NAIVE":
        window = _positive_int(
            values["naive"].get("trailing_window_days"),
            "naive.trailing_window_days",
        )
        level = _mean(history[-window:])
        return [max(level, Decimal("0"))] * horizon
    if name == "SEASONAL_NAIVE":
        minimum = _positive_int(
            values["seasonal_naive"].get("minimum_history_days"),
            "seasonal_naive.minimum_history_days",
        )
        period = _positive_int(
            values["seasonal_naive"].get("period_days"),
            "seasonal_naive.period_days",
        )
        if len(history) < max(minimum, period * 2):
            raise _InapplicableModel("INSUFFICIENT_SEASONAL_HISTORY")
        tail = history[-period:]
        return [max(tail[index % period], Decimal("0")) for index in range(horizon)]
    if name == "DAMPED_ETS":
        minimum = _positive_int(
            values["damped_ets"].get("minimum_history_days"),
            "damped_ets.minimum_history_days",
        )
        if len(history) < minimum:
            raise _InapplicableModel("INSUFFICIENT_ETS_HISTORY")
        alpha = _decimal(values["damped_ets"].get("alpha"), "damped_ets.alpha")
        beta = _decimal(values["damped_ets"].get("beta"), "damped_ets.beta")
        phi = _decimal(values["damped_ets"].get("phi"), "damped_ets.phi")
        level = history[0]
        trend = history[1] - history[0] if len(history) > 1 else Decimal("0")
        for observed in history[1:]:
            previous = level
            level = alpha * observed + (Decimal("1") - alpha) * (
                level + phi * trend
            )
            trend = beta * (level - previous) + (
                Decimal("1") - beta
            ) * phi * trend
        predictions: list[Decimal] = []
        damped_sum = Decimal("0")
        for step in range(1, horizon + 1):
            damped_sum += phi**step
            predictions.append(max(level + damped_sum * trend, Decimal("0")))
        return predictions
    if name == "TSB":
        minimum = _positive_int(
            values["tsb"].get("minimum_history_days"),
            "tsb.minimum_history_days",
        )
        positives = [item for item in history if item > 0]
        minimum_positive = _positive_int(
            values["tsb"].get("minimum_positive_days"),
            "tsb.minimum_positive_days",
        )
        zero_share = Decimal(sum(1 for item in history if item == 0)) / Decimal(
            len(history)
        )
        threshold = _decimal(
            values["tsb"].get("intermittent_zero_share"),
            "tsb.intermittent_zero_share",
        )
        if len(history) < minimum or len(positives) < minimum_positive:
            raise _InapplicableModel("INSUFFICIENT_INTERMITTENT_HISTORY")
        if zero_share < threshold:
            raise _InapplicableModel("DEMAND_NOT_INTERMITTENT")
        alpha_p = _decimal(
            values["tsb"].get("alpha_probability"), "tsb.alpha_probability"
        )
        alpha_z = _decimal(values["tsb"].get("alpha_size"), "tsb.alpha_size")
        probability = Decimal("1") if history[0] > 0 else Decimal("0")
        size = positives[0]
        for observed in history[1:]:
            occurrence = Decimal("1") if observed > 0 else Decimal("0")
            probability = alpha_p * occurrence + (
                Decimal("1") - alpha_p
            ) * probability
            if observed > 0:
                size = alpha_z * observed + (Decimal("1") - alpha_z) * size
        return [max(probability * size, Decimal("0"))] * horizon
    if name == "CATEGORY_SHRINKAGE":
        minimum = _positive_int(
            values["category_shrinkage"].get("minimum_history_days"),
            "category_shrinkage.minimum_history_days",
        )
        if category_daily_velocity is None:
            raise _InapplicableModel("CATEGORY_PRIOR_NOT_FROZEN")
        if len(history) < minimum:
            raise _InapplicableModel("INSUFFICIENT_CATEGORY_SHRINKAGE_HISTORY")
        strength = _decimal(
            values["category_shrinkage"].get("prior_strength_days"),
            "category_shrinkage.prior_strength_days",
        )
        own = _mean(history)
        weight = Decimal(len(history)) / (Decimal(len(history)) + strength)
        level = weight * own + (Decimal("1") - weight) * category_daily_velocity
        return [max(level, Decimal("0"))] * horizon
    raise DevelopmentForecastError(f"unknown forecast candidate {name}")


def _postprocess_predictions(
    predictions: Sequence[Decimal],
    *,
    origin_history: Sequence[Decimal],
    policy: DevelopmentForecastPolicy,
) -> tuple[list[Decimal], int, Decimal]:
    """Apply the identical nonnegative/rate-cap rule at every forecast origin."""

    if not predictions:
        raise DevelopmentForecastError("forecast postprocessing requires predictions")
    nonnegative = [max(item, Decimal("0")) for item in predictions]
    rate_cap = _mean(origin_history) * _decimal(
        policy.values["rate_cap"]["maximum_calendar_mean_multiplier"],
        "rate_cap.maximum_calendar_mean_multiplier",
    )
    raw_daily_rate = sum(nonnegative, Decimal("0")) / Decimal(len(nonnegative))
    cap_hit = int(raw_daily_rate > rate_cap)
    scale = (
        rate_cap / raw_daily_rate
        if cap_hit and raw_daily_rate > 0
        else Decimal("1")
    )
    return [item * scale for item in nonnegative], cap_hit, rate_cap


def _metrics(
    actuals: Sequence[Decimal],
    forecasts: Sequence[Decimal],
    scale_history: Sequence[Decimal],
) -> _Metrics:
    if len(actuals) != len(forecasts) or not actuals:
        raise DevelopmentForecastError("forecast metrics require paired observations")
    errors = [forecast - actual for actual, forecast in zip(actuals, forecasts, strict=True)]
    absolute = [abs(item) for item in errors]
    count = Decimal(len(errors))
    bias = sum(errors, Decimal("0")) / count
    mae = sum(absolute, Decimal("0")) / count
    actual_total = sum((abs(item) for item in actuals), Decimal("0"))
    wape = None if actual_total == 0 else sum(absolute, Decimal("0")) / actual_total
    scale_terms = [
        abs(scale_history[index] - scale_history[index - 1])
        for index in range(1, len(scale_history))
    ]
    scale = _mean(scale_terms) if scale_terms else Decimal("0")
    mase = None if scale == 0 else mae / scale
    return _Metrics(len(errors), bias, mae, wape, mase)


def _objective(metrics: _Metrics) -> Decimal:
    return metrics.wape if metrics.wape is not None else metrics.mae


def _rolling_candidate(
    *,
    name: str,
    origins: range,
    normalized: Sequence[Decimal],
    ordered: Sequence[DemandObservation],
    policy: DevelopmentForecastPolicy,
    category_daily_velocity: Decimal | None,
    scale_history: Sequence[Decimal],
    horizon: int,
) -> tuple[dict[str, Any], _Metrics | None]:
    actuals: list[Decimal] = []
    forecasts: list[Decimal] = []
    origin_dates: list[str] = []
    cap_hits = 0
    try:
        for origin in origins:
            target_window = ordered[origin : origin + horizon]
            if any(item.inventory_state == "STOCKOUT" for item in target_window):
                continue
            try:
                predictions = _predict(
                    name,
                    normalized[:origin],
                    horizon,
                    policy,
                    category_daily_velocity,
                )
            except _InapplicableModel as exc:
                return {
                    "name": name,
                    "status": "INAPPLICABLE",
                    "reason_codes": [str(exc)],
                    "selection_metrics": None,
                    "selection_origin_dates": origin_dates,
                    "postprocessing_cap_hits": cap_hits,
                    "forecast_horizon_days": horizon,
                }, None
            bounded, cap_hit, _rate_cap = _postprocess_predictions(
                predictions,
                origin_history=normalized[:origin],
                policy=policy,
            )
            prediction = sum(bounded, Decimal("0"))
            cap_hits += cap_hit
            actuals.append(
                sum(normalized[origin : origin + horizon], Decimal("0"))
            )
            forecasts.append(prediction)
            origin_dates.append(ordered[origin].business_date.isoformat())
    except Exception as exc:
        return {
            "name": name,
            "status": "FAILED",
            "reason_codes": [f"MODEL_EXECUTION_FAILED:{type(exc).__name__}"],
            "selection_metrics": None,
            "selection_origin_dates": origin_dates,
            "postprocessing_cap_hits": cap_hits,
            "forecast_horizon_days": horizon,
        }, None
    minimum = int(policy.values["windows"]["minimum_selection_origins"])
    if len(actuals) < minimum:
        return {
            "name": name,
            "status": "INAPPLICABLE",
            "reason_codes": ["INSUFFICIENT_SELECTION_ORIGINS"],
            "selection_metrics": None,
            "selection_origin_dates": origin_dates,
            "postprocessing_cap_hits": cap_hits,
            "forecast_horizon_days": horizon,
        }, None
    result = _metrics(actuals, forecasts, scale_history)
    return {
        "name": name,
        "status": "ELIGIBLE",
        "reason_codes": [],
        "selection_metrics": result.to_json_dict(),
        "selection_origin_dates": origin_dates,
        "postprocessing_cap_hits": cap_hits,
        "forecast_horizon_days": horizon,
    }, result


def _weekly_xyz(
    normalized: Sequence[Decimal], policy: DevelopmentForecastPolicy
) -> tuple[str, Decimal | None, tuple[str, ...]]:
    weekly = [
        sum(normalized[index : index + 7], Decimal("0"))
        for index in range(0, len(normalized), 7)
        if len(normalized[index : index + 7]) == 7
    ]
    minimum = int(policy.values["xyz"]["minimum_week_buckets"])
    if len(weekly) < minimum:
        return "NOT_CONFIGURED", None, ("INSUFFICIENT_XYZ_WEEK_BUCKETS",)
    mean = _mean(weekly)
    if mean == 0:
        return str(policy.values["xyz"]["zero_demand_class"]), None, (
            "ZERO_DEMAND_XYZ_POLICY",
        )
    variance = sum(((item - mean) ** 2 for item in weekly), Decimal("0")) / Decimal(
        len(weekly)
    )
    coefficient = variance.sqrt() / mean
    x_max = _decimal(
        policy.values["xyz"]["x_max_coefficient_of_variation"], "xyz.x_max"
    )
    y_max = _decimal(
        policy.values["xyz"]["y_max_coefficient_of_variation"], "xyz.y_max"
    )
    if coefficient <= x_max:
        return "X", coefficient, ()
    if coefficient <= y_max:
        return "Y", coefficient, ()
    return "Z", coefficient, ()


def _quantile(values: Sequence[Decimal], quantile: Decimal) -> Decimal:
    if not values:
        raise DevelopmentForecastError("quantile requires observations")
    ordered = sorted(values)
    rank = int(
        (quantile * Decimal(len(ordered))).to_integral_value(rounding=ROUND_CEILING)
    )
    return ordered[max(1, rank) - 1]


def _finalize_evidence(payload: dict[str, Any]) -> DevelopmentForecastPlan:
    unsigned = json.loads(canonical_evidence_json(payload))
    unsigned["sha256"] = canonical_evidence_sha256(unsigned)
    return DevelopmentForecastPlan(unsigned)


def plan_development_forecast(
    observations: Iterable[DemandObservation],
    *,
    horizon_days: int,
    protection_calendar: Mapping[str, Any] | None = None,
    policy: DevelopmentForecastPolicy | None = None,
) -> DevelopmentForecastPlan:
    """Build a leak-free development forecast and empirical protection plan."""

    horizon = _positive_int(horizon_days, "horizon_days")
    if horizon > 31:
        raise DevelopmentForecastError("horizon_days exceeds the development bound")
    active_policy = policy or load_development_forecast_policy()
    ordered, normalized = _validated_observations(observations)
    # A final-cutoff category scalar cannot be used at earlier rolling origins
    # without leaking future information.  No independently frozen causal
    # category history exists in this bounded slice, so the category model is
    # deliberately and explicitly inapplicable.
    category_prior: Decimal | None = None
    windows = active_policy.values["windows"]
    evaluation_days = int(windows["evaluation_days"])
    calibration_days = int(windows["calibration_days"])
    minimum_training = int(windows["minimum_training_days"])
    selection_days = int(windows["selection_origin_days"])
    evaluation_start = len(ordered) - evaluation_days
    calibration_start = evaluation_start - calibration_days
    selection_start = max(minimum_training, calibration_start - selection_days)
    input_rows = [
        {
            "business_date": item.business_date,
            "net_units": item.net_units,
            "inventory_state": item.inventory_state,
        }
        for item in ordered
    ]
    calendar_evidence = (
        {
            "basis": "CALLER_VALIDATED_HORIZON",
            "horizon_days": horizon,
        }
        if protection_calendar is None
        else json.loads(canonical_evidence_json(protection_calendar))
    )
    if calendar_evidence.get("horizon_days") != horizon:
        raise DevelopmentForecastError(
            "protection calendar horizon differs from the forecast horizon"
        )
    base_payload: dict[str, Any] = {
        "contract": CONTRACT,
        "method_version": METHOD_VERSION,
        "policy": active_policy.evidence(),
        "history_start": ordered[0].business_date,
        "history_end": ordered[-1].business_date,
        "cutoff_date": ordered[-1].business_date,
        "history_days": len(ordered),
        "horizon_days": horizon,
        "protection_calendar": calendar_evidence,
        "calendar_velocity": str(
            _mean(normalized).quantize(VELOCITY, rounding=ROUND_HALF_UP)
        ),
        "in_stock_velocity": (
            str(
                _mean(
                    [
                        normalized[index]
                        for index, item in enumerate(ordered)
                        if item.inventory_state == "IN_STOCK"
                    ]
                ).quantize(VELOCITY, rounding=ROUND_HALF_UP)
            )
            if any(item.inventory_state == "IN_STOCK" for item in ordered)
            else None
        ),
        "input_sha256": canonical_evidence_sha256(input_rows),
        "category_prior": {
            "status": "NOT_CONFIGURED",
            "reason_code": "CATEGORY_PRIOR_NOT_FROZEN",
        },
        "commercial_authority": False,
        "production_activation": False,
    }
    if calibration_start <= minimum_training or selection_start >= calibration_start:
        return _finalize_evidence(
            {
                **base_payload,
                "status": "BLOCKED",
                "selected_model": None,
                "demand_regime": "THIN",
                "xyz_class": "NOT_CONFIGURED",
                "xyz_coefficient_of_variation": None,
                "abc_class": "NOT_CONFIGURED",
                "forecast_daily_velocity": "0.000000",
                "point_forecast_units": "0.0000",
                "protection_units": None,
                "target_units": None,
                "confidence": "LOW",
                "reason_codes": ["INSUFFICIENT_CHRONOLOGICAL_WINDOWS"],
                "split": {
                    "selection_start_index": selection_start,
                    "calibration_start_index": calibration_start,
                    "evaluation_start_index": evaluation_start,
                },
                "candidates": [],
                "protection": {
                    "status": "UNAVAILABLE",
                    "full_horizon_origin_count": 0,
                    "shortfalls": [],
                },
                "caps": {"daily_rate_cap_hits": 0},
            }
        )
    last_selection_origin = calibration_start - horizon
    selection_origins = range(
        selection_start,
        max(selection_start, last_selection_origin + 1),
    )
    scale_history = tuple(
        sum(normalized[index : index + horizon], Decimal("0"))
        for index in range(0, max(0, selection_start - horizon + 1))
    )
    candidate_records: list[dict[str, Any]] = []
    candidate_metrics: dict[str, _Metrics] = {}
    for name in active_policy.values["candidate_order"]:
        record, result = _rolling_candidate(
            name=name,
            origins=selection_origins,
            normalized=normalized,
            ordered=ordered,
            policy=active_policy,
            category_daily_velocity=category_prior,
            scale_history=scale_history,
            horizon=horizon,
        )
        candidate_records.append(record)
        if result is not None:
            candidate_metrics[name] = result
    baseline_name = str(active_policy.values["fva"]["simple_baseline"])
    baseline = candidate_metrics.get(baseline_name)
    if baseline is None:
        return _finalize_evidence(
            {
                **base_payload,
                "status": "BLOCKED",
                "selected_model": None,
                "demand_regime": "UNCLASSIFIED",
                "xyz_class": "NOT_CONFIGURED",
                "xyz_coefficient_of_variation": None,
                "abc_class": "NOT_CONFIGURED",
                "forecast_daily_velocity": "0.000000",
                "point_forecast_units": "0.0000",
                "protection_units": None,
                "target_units": None,
                "confidence": "LOW",
                "reason_codes": ["SIMPLE_BASELINE_UNAVAILABLE"],
                "split": {
                    "selection_start_index": selection_start,
                    "calibration_start_index": calibration_start,
                    "evaluation_start_index": evaluation_start,
                },
                "candidates": candidate_records,
                "protection": {
                    "status": "UNAVAILABLE",
                    "full_horizon_origin_count": 0,
                    "shortfalls": [],
                },
                "caps": {"daily_rate_cap_hits": 0},
            }
        )
    comparable_origin_sets = {
        tuple(record["selection_origin_dates"])
        for record in candidate_records
        if record.get("status") == "ELIGIBLE"
    }
    if len(comparable_origin_sets) != 1:
        raise DevelopmentForecastError(
            "eligible forecast candidates used different rolling origins"
        )
    ordered_names = list(active_policy.values["candidate_order"])
    best_name = min(
        candidate_metrics,
        key=lambda name: (_objective(candidate_metrics[name]), ordered_names.index(name)),
    )
    selected_name = baseline_name
    fva_reason = "SIMPLE_BASELINE_SELECTED"
    if best_name != baseline_name:
        base_score = _objective(baseline)
        best_score = _objective(candidate_metrics[best_name])
        improvement = (
            Decimal("0")
            if base_score == 0
            else max((base_score - best_score) / base_score, Decimal("0"))
        )
        minimum_improvement = _decimal(
            active_policy.values["fva"]["minimum_relative_improvement"],
            "fva.minimum_relative_improvement",
        )
        if improvement >= minimum_improvement:
            selected_name = best_name
            fva_reason = "COMPLEX_MODEL_CLEARED_FVA_GATE"
        else:
            fva_reason = "COMPLEX_MODEL_DID_NOT_CLEAR_FVA_GATE"
    selected_index = ordered_names.index(selected_name)
    candidate_records[selected_index]["selected"] = True
    for index, record in enumerate(candidate_records):
        if index != selected_index:
            record["selected"] = False
    evaluation_actuals: list[Decimal] = []
    evaluation_forecasts: list[Decimal] = []
    evaluation_cap_hits = 0
    last_evaluation_origin = len(ordered) - horizon
    for origin in range(evaluation_start, last_evaluation_origin + 1):
        target_window = ordered[origin : origin + horizon]
        if any(item.inventory_state == "STOCKOUT" for item in target_window):
            continue
        try:
            bounded, cap_hit, _rate_cap = _postprocess_predictions(
                _predict(
                    selected_name,
                    normalized[:origin],
                    horizon,
                    active_policy,
                    category_prior,
                ),
                origin_history=normalized[:origin],
                policy=active_policy,
            )
            predicted = sum(bounded, Decimal("0"))
            evaluation_cap_hits += cap_hit
        except _InapplicableModel as exc:
            raise DevelopmentForecastError(
                "selected model became inapplicable during evaluation"
            ) from exc
        evaluation_actuals.append(
            sum(normalized[origin : origin + horizon], Decimal("0"))
        )
        evaluation_forecasts.append(predicted)
    if not evaluation_actuals:
        raise DevelopmentForecastError(
            "no uncensored purchase-horizon evaluation origins remain"
        )
    evaluation_scale_history = tuple(
        sum(normalized[index : index + horizon], Decimal("0"))
        for index in range(0, max(0, evaluation_start - horizon + 1))
    )
    evaluation = _metrics(
        evaluation_actuals,
        evaluation_forecasts,
        evaluation_scale_history,
    )
    minimum_evaluation = int(
        active_policy.values["windows"]["minimum_evaluation_origins"]
    )
    evaluation_status = (
        "SUFFICIENT"
        if len(evaluation_actuals) >= minimum_evaluation
        else "INSUFFICIENT_FOR_CONFIDENCE"
    )
    shortfalls: list[Decimal] = []
    calibration_origin_dates: list[str] = []
    censored_calibration_origin_dates: list[str] = []
    calibration_cap_hits = 0
    last_calibration_origin = evaluation_start - horizon
    if last_calibration_origin >= calibration_start:
        for origin in range(calibration_start, last_calibration_origin + 1):
            target_window = ordered[origin : origin + horizon]
            if any(item.inventory_state == "STOCKOUT" for item in target_window):
                censored_calibration_origin_dates.append(
                    ordered[origin].business_date.isoformat()
                )
                continue
            try:
                bounded, cap_hit, _rate_cap = _postprocess_predictions(
                    _predict(
                        selected_name,
                        normalized[:origin],
                        horizon,
                        active_policy,
                        category_prior,
                    ),
                    origin_history=normalized[:origin],
                    policy=active_policy,
                )
                predicted = sum(bounded, Decimal("0"))
                calibration_cap_hits += cap_hit
            except _InapplicableModel as exc:
                raise DevelopmentForecastError(
                    "selected model became inapplicable during calibration"
                ) from exc
            actual = sum(normalized[origin : origin + horizon], Decimal("0"))
            shortfalls.append(max(actual - predicted, Decimal("0")))
            calibration_origin_dates.append(ordered[origin].business_date.isoformat())
    minimum_calibration = int(
        active_policy.values["protection"]["minimum_full_horizon_origins"]
    )
    bounded_predictions, final_cap_hits, rate_cap = _postprocess_predictions(
        _predict(
            selected_name,
            normalized,
            horizon,
            active_policy,
            category_prior,
        ),
        origin_history=normalized,
        policy=active_policy,
    )
    point_forecast = sum(bounded_predictions, Decimal("0")).quantize(
        UNITS, rounding=ROUND_HALF_UP
    )
    velocity = (point_forecast / Decimal(horizon)).quantize(
        VELOCITY, rounding=ROUND_HALF_UP
    )
    zero_share = Decimal(sum(1 for item in normalized if item == 0)) / Decimal(
        len(normalized)
    )
    intermittent_threshold = _decimal(
        active_policy.values["tsb"]["intermittent_zero_share"],
        "tsb.intermittent_zero_share",
    )
    demand_regime = "INTERMITTENT" if zero_share >= intermittent_threshold else "REGULAR"
    xyz_class, xyz_coefficient, xyz_reasons = _weekly_xyz(normalized, active_policy)
    common_payload = {
        **base_payload,
        "selected_model": selected_name,
        "demand_regime": demand_regime,
        "xyz_class": xyz_class,
        "xyz_coefficient_of_variation": (
            None
            if xyz_coefficient is None
            else str(xyz_coefficient.quantize(METRIC, rounding=ROUND_HALF_UP))
        ),
        "abc_class": "NOT_CONFIGURED",
        "abc_status": "MISSING_HISTORICAL_COGS",
        "forecast_daily_velocity": str(velocity),
        "point_forecast_units": str(point_forecast),
        "selection_metrics": candidate_metrics[selected_name].to_json_dict(),
        "evaluation_metrics": evaluation.to_json_dict(),
        "evaluation_origin_count": len(evaluation_actuals),
        "minimum_evaluation_origin_count": minimum_evaluation,
        "evaluation_status": evaluation_status,
        "split": {
            "selection_start_date": ordered[selection_start].business_date,
            "selection_end_date": ordered[calibration_start - 1].business_date,
            "calibration_start_date": ordered[calibration_start].business_date,
            "calibration_end_date": ordered[evaluation_start - 1].business_date,
            "evaluation_start_date": ordered[evaluation_start].business_date,
            "evaluation_end_date": ordered[-1].business_date,
        },
        "candidates": candidate_records,
        "caps": {
            "daily_rate_cap": str(rate_cap.quantize(VELOCITY, rounding=ROUND_HALF_UP)),
            "daily_rate_cap_hits": (
                sum(
                    int(record.get("postprocessing_cap_hits", 0))
                    for record in candidate_records
                )
                + evaluation_cap_hits
                + calibration_cap_hits
                + final_cap_hits
            ),
            "selection_cap_hits": sum(
                int(record.get("postprocessing_cap_hits", 0))
                for record in candidate_records
            ),
            "evaluation_cap_hits": evaluation_cap_hits,
            "calibration_cap_hits": calibration_cap_hits,
            "final_cap_hits": final_cap_hits,
        },
        "availability": {
            "proven_stockout_days": sum(
                1 for item in ordered if item.inventory_state == "STOCKOUT"
            ),
            "proven_in_stock_days": sum(
                1 for item in ordered if item.inventory_state == "IN_STOCK"
            ),
            "unknown_days": sum(
                1 for item in ordered if item.inventory_state == "UNKNOWN"
            ),
            "negative_net_days": sum(1 for item in ordered if item.net_units < 0),
        },
    }
    if len(shortfalls) < minimum_calibration:
        return _finalize_evidence(
            {
                **common_payload,
                "status": "BLOCKED",
                "protection_units": None,
                "target_units": None,
                "confidence": "LOW",
                "reason_codes": [
                    fva_reason,
                    *xyz_reasons,
                    "EMPIRICAL_PROTECTION_ORIGINS_INSUFFICIENT",
                ],
                "protection": {
                    "status": "UNAVAILABLE",
                    "method": "FULL_HORIZON_SHORTFALL_EMPIRICAL_QUANTILE",
                    "quantile": active_policy.values["protection"]["quantile"],
                    "minimum_origin_count": minimum_calibration,
                    "full_horizon_origin_count": len(shortfalls),
                    "censored_stockout_origin_count": len(
                        censored_calibration_origin_dates
                    ),
                    "censored_stockout_origin_dates": censored_calibration_origin_dates,
                    "origin_dates": calibration_origin_dates,
                    "shortfalls": [str(item.quantize(UNITS)) for item in shortfalls],
                },
            }
        )
    quantile = _decimal(
        active_policy.values["protection"]["quantile"], "protection.quantile"
    )
    protection = _quantile(shortfalls, quantile).quantize(
        UNITS, rounding=ROUND_HALF_UP
    )
    target = (point_forecast + protection).quantize(UNITS, rounding=ROUND_HALF_UP)
    evaluation_wape = evaluation.wape
    confidence = (
        "HIGH"
        if evaluation_status == "SUFFICIENT"
        and evaluation_wape is not None
        and evaluation_wape <= Decimal("0.20")
        else "MEDIUM"
        if evaluation_status == "SUFFICIENT"
        and evaluation_wape is not None
        and evaluation_wape <= Decimal("0.50")
        else "LOW"
    )
    reasons = [fva_reason, *xyz_reasons]
    if any(item.inventory_state == "STOCKOUT" for item in ordered):
        reasons.append("PROVEN_STOCKOUT_DAYS_CAUSALLY_IMPUTED")
    if any(item.inventory_state == "UNKNOWN" for item in ordered):
        reasons.append("UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK")
    if any(item.net_units < 0 for item in ordered):
        reasons.append("NEGATIVE_NET_DAYS_FLOORED_FOR_DEMAND_ONLY")
    if common_payload["caps"]["daily_rate_cap_hits"]:
        reasons.append("DEVELOPMENT_RATE_CAP_APPLIED")
    if evaluation_status != "SUFFICIENT":
        reasons.append("EVALUATION_ORIGINS_INSUFFICIENT_FOR_CONFIDENCE")
    reasons.append("EMPIRICAL_FULL_HORIZON_PROTECTION_BOUND")
    return _finalize_evidence(
        {
            **common_payload,
            "status": "READY",
            "protection_units": str(protection),
            "target_units": str(target),
            "confidence": confidence,
            "reason_codes": reasons,
            "protection": {
                "status": "CALCULATED",
                "method": "FULL_HORIZON_SHORTFALL_EMPIRICAL_QUANTILE",
                "quantile": str(quantile),
                "minimum_origin_count": minimum_calibration,
                "full_horizon_origin_count": len(shortfalls),
                "censored_stockout_origin_count": len(
                    censored_calibration_origin_dates
                ),
                "censored_stockout_origin_dates": censored_calibration_origin_dates,
                "origin_dates": calibration_origin_dates,
                "shortfalls": [
                    str(item.quantize(UNITS, rounding=ROUND_HALF_UP))
                    for item in shortfalls
                ],
                "units": str(protection),
            },
        }
    )


def validate_development_forecast_evidence(value: Any) -> bool:
    """Deeply validate the immutable shape and self-hash of V1 evidence."""

    if not isinstance(value, dict):
        return False
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    policy = value.get("policy")
    protection = value.get("protection")
    candidates = value.get("candidates")
    protection_calendar = value.get("protection_calendar")
    try:
        sha256 = value.get("sha256")
        if not (
            value.get("contract") == CONTRACT
            and value.get("method_version") == METHOD_VERSION
            and value.get("commercial_authority") is False
            and value.get("production_activation") is False
            and isinstance(sha256, str)
            and len(sha256) == _SHA256_LENGTH
            and bytes.fromhex(sha256)
            and sha256 == canonical_evidence_sha256(unsigned)
            and policy == load_development_forecast_policy().evidence()
            and value.get("status") in {"READY", "BLOCKED"}
            and isinstance(value.get("history_days"), int)
            and value["history_days"] > 0
            and isinstance(value.get("horizon_days"), int)
            and 0 < value["horizon_days"] <= 31
            and date.fromisoformat(str(value.get("history_start")))
            <= date.fromisoformat(str(value.get("history_end")))
            == date.fromisoformat(str(value.get("cutoff_date")))
            and isinstance(value.get("input_sha256"), str)
            and len(value["input_sha256"]) == _SHA256_LENGTH
            and bytes.fromhex(value["input_sha256"])
            and value.get("category_prior")
            == {
                "status": "NOT_CONFIGURED",
                "reason_code": "CATEGORY_PRIOR_NOT_FROZEN",
            }
            and isinstance(value.get("reason_codes"), list)
            and all(
                isinstance(item, str) and bool(item)
                for item in value["reason_codes"]
            )
            and len(value["reason_codes"]) == len(set(value["reason_codes"]))
            and isinstance(protection_calendar, dict)
            and protection_calendar.get("horizon_days") == value["horizon_days"]
            and isinstance(candidates, list)
            and isinstance(protection, dict)
            and protection.get("status")
            == ("CALCULATED" if value.get("status") == "READY" else "UNAVAILABLE")
        ):
            return False

        basis = protection_calendar.get("basis")
        if basis == "CALLER_VALIDATED_HORIZON":
            if set(protection_calendar) != {"basis", "horizon_days"}:
                return False
        elif basis == "NEXT_CONFIRMED_ORDER_TO_FIRST_PERMITTED_RECEIPT_V1":
            required_calendar_keys = {
                "basis",
                "timezone_name",
                "evaluation_at",
                "order_days",
                "order_cutoff_local",
                "expected_delivery_days",
                "order_cycle_days",
                "next_order_date",
                "lead_time_days",
                "next_receipt_date",
                "calendar_days_until_receipt",
                "lead_time_variability_days",
                "variability_days_ceiling",
                "variability_treatment",
                "horizon_days",
            }
            evaluation = datetime.fromisoformat(
                str(protection_calendar.get("evaluation_at"))
            )
            if evaluation.tzinfo is None:
                return False
            evaluation_date = evaluation.date()
            next_order = date.fromisoformat(
                str(protection_calendar.get("next_order_date"))
            )
            next_receipt = date.fromisoformat(
                str(protection_calendar.get("next_receipt_date"))
            )
            order_cycle = int(protection_calendar.get("order_cycle_days"))
            lead_time = int(protection_calendar.get("lead_time_days"))
            variability = _decimal(
                protection_calendar.get("lead_time_variability_days"),
                "lead_time_variability_days",
            )
            order_days = protection_calendar.get("order_days")
            delivery_days = protection_calendar.get("expected_delivery_days")
            if not (
                set(protection_calendar) == required_calendar_keys
                and isinstance(order_days, list)
                and order_days
                and len(order_days) == len(set(order_days))
                and all(day in _WEEKDAY_INDEX for day in order_days)
                and isinstance(delivery_days, list)
                and delivery_days
                and len(delivery_days) == len(set(delivery_days))
                and all(day in _WEEKDAY_INDEX for day in delivery_days)
                and time.fromisoformat(
                    str(protection_calendar.get("order_cutoff_local"))
                ).tzinfo
                is None
                and order_cycle > 0
                and lead_time >= 0
                and next_order > evaluation_date
                and (next_order - evaluation_date).days == order_cycle
                and next_order.strftime("%A").upper() in order_days
                and next_receipt >= next_order + timedelta(days=lead_time)
                and next_receipt.strftime("%A").upper() in delivery_days
                and (next_receipt - evaluation_date).days
                == protection_calendar.get("calendar_days_until_receipt")
                == value["horizon_days"]
                and variability >= 0
                and int(
                    variability.to_integral_value(rounding=ROUND_CEILING)
                )
                == protection_calendar.get("variability_days_ceiling")
                and protection_calendar.get("variability_treatment")
                == "EVIDENCE_ONLY_NOT_DEMAND_HORIZON"
            ):
                return False
        else:
            return False

        if value.get("status") != "READY":
            return True

        expected_candidates = list(
            load_development_forecast_policy().values["candidate_order"]
        )
        if [item.get("name") for item in candidates] != expected_candidates:
            return False
        selected_model = value.get("selected_model")
        selected_rows = [
            item for item in candidates if item.get("selected") is True
        ]
        eligible_counts = {
            item["selection_metrics"]["observation_count"]
            for item in candidates
            if item.get("status") == "ELIGIBLE"
            and isinstance(item.get("selection_metrics"), dict)
        }
        eligible_origin_sets = {
            tuple(item.get("selection_origin_dates", []))
            for item in candidates
            if item.get("status") == "ELIGIBLE"
        }
        evaluation_count = value.get("evaluation_origin_count")
        minimum_evaluation = value.get("minimum_evaluation_origin_count")
        evaluation_status = value.get("evaluation_status")
        point = Decimal(str(value.get("point_forecast_units")))
        protection_units = Decimal(str(value.get("protection_units")))
        target = Decimal(str(value.get("target_units")))
        shortfalls = [
            Decimal(str(item)) for item in protection.get("shortfalls", [])
        ]
        quantile = _decimal(protection.get("quantile"), "protection.quantile")
        if not (
            selected_model in expected_candidates
            and len(selected_rows) == 1
            and selected_rows[0].get("name") == selected_model
            and len(eligible_counts) == 1
            and len(eligible_origin_sets) == 1
            and next(iter(eligible_counts)) > 0
            and len(next(iter(eligible_origin_sets)))
            == next(iter(eligible_counts))
            and isinstance(evaluation_count, int)
            and evaluation_count > 0
            and isinstance(minimum_evaluation, int)
            and minimum_evaluation > 0
            and evaluation_status
            == (
                "SUFFICIENT"
                if evaluation_count >= minimum_evaluation
                else "INSUFFICIENT_FOR_CONFIDENCE"
            )
            and (
                evaluation_status == "SUFFICIENT" or value.get("confidence") == "LOW"
            )
            and point >= 0
            and protection_units >= 0
            and target == point + protection_units
            and protection.get("method")
            == "FULL_HORIZON_SHORTFALL_EMPIRICAL_QUANTILE"
            and isinstance(protection.get("minimum_origin_count"), int)
            and isinstance(protection.get("full_horizon_origin_count"), int)
            and protection["full_horizon_origin_count"] == len(shortfalls)
            and len(shortfalls) >= protection["minimum_origin_count"] > 0
            and all(item >= 0 for item in shortfalls)
            and protection_units
            == _quantile(shortfalls, quantile).quantize(
                UNITS, rounding=ROUND_HALF_UP
            )
        ):
            return False
        return True
    except (
        DevelopmentForecastError,
        InvalidOperation,
        KeyError,
        OSError,
        TypeError,
        ValueError,
    ):
        return False


def assign_gp_dollar_abc(
    rows: Iterable[Mapping[str, Any]],
    *,
    policy: DevelopmentForecastPolicy | None = None,
) -> dict[str, dict[str, Any]]:
    """Assign ABC only from independent historical revenue and COGS."""

    active_policy = policy or load_development_forecast_policy()
    parsed: list[tuple[str, Decimal]] = []
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for row in rows:
        variant_id = str(row.get("variant_id") or "").strip()
        if not variant_id or variant_id in seen:
            raise DevelopmentForecastError("ABC rows require unique Variant IDs")
        seen.add(variant_id)
        revenue_raw = row.get("historical_revenue")
        cogs_raw = row.get("historical_cogs")
        if revenue_raw is None or cogs_raw is None:
            result[variant_id] = {
                "abc_class": "NOT_CONFIGURED",
                "status": "MISSING_HISTORICAL_COGS",
                "gross_profit_dollars": None,
                "classification_period_days": int(
                    active_policy.values["abc"]["classification_period_days"]
                ),
            }
            continue
        revenue = _decimal(revenue_raw, "historical_revenue")
        cogs = _decimal(cogs_raw, "historical_cogs")
        if revenue < 0 or cogs < 0:
            raise DevelopmentForecastError("historical revenue and COGS cannot be negative")
        gp = revenue - cogs
        parsed.append((variant_id, gp))
    positive_total = sum((max(gp, Decimal("0")) for _, gp in parsed), Decimal("0"))
    if parsed and positive_total == 0:
        for variant_id, gp in parsed:
            result[variant_id] = {
                "abc_class": "NOT_CONFIGURED",
                "status": "NONPOSITIVE_HISTORICAL_GROSS_PROFIT",
                "gross_profit_dollars": str(
                    gp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                ),
                "classification_period_days": int(
                    active_policy.values["abc"]["classification_period_days"]
                ),
                "basis": "HISTORICAL_GROSS_PROFIT_DOLLARS",
            }
        return result
    a_share = _decimal(active_policy.values["abc"]["a_cumulative_share"], "abc.a_share")
    b_share = _decimal(active_policy.values["abc"]["b_incremental_share"], "abc.b_share")
    cumulative = Decimal("0")
    for variant_id, gp in sorted(parsed, key=lambda item: (-item[1], item[0])):
        share_before = Decimal("0") if positive_total == 0 else cumulative / positive_total
        abc_class = "A" if share_before < a_share else "B" if share_before < a_share + b_share else "C"
        contribution = max(gp, Decimal("0"))
        cumulative += contribution
        result[variant_id] = {
            "abc_class": abc_class,
            "status": "CALCULATED",
            "gross_profit_dollars": str(gp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "cumulative_share_after": (
                "0.000000"
                if positive_total == 0
                else str((cumulative / positive_total).quantize(METRIC, rounding=ROUND_HALF_UP))
            ),
            "basis": "HISTORICAL_GROSS_PROFIT_DOLLARS",
            "classification_period_days": int(
                active_policy.values["abc"]["classification_period_days"]
            ),
        }
    return result
