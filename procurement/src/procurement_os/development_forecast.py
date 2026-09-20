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
from .replenishment import BaselineNeed, calculate_development_baseline_need


CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V1"
POLICY_CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_POLICY_V1"
METHOD_VERSION = "DEVELOPMENT_ROLLING_ORIGIN_V1"
V2_CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2"
V2_POLICY_CONTRACT = "BUFFALO_DEVELOPMENT_FORECAST_POLICY_V2"
V2_METHOD_VERSION = "DEVELOPMENT_ROLLING_ORIGIN_V2"
CAPABILITY_ENV = "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST"
FIXTURE_META_KEY = "synthetic_development_forecast_contract"
FIXTURE_CONTRACT = "BUFFALO_SYNTHETIC_DEVELOPMENT_FORECAST_V1"
V2_FIXTURE_CONTRACT = "BUFFALO_SYNTHETIC_DEVELOPMENT_FORECAST_V2"
V2_SCHEDULE_CONTRACT = "BUFFALO_SYNTHETIC_DEVELOPMENT_SCHEDULE_V2"
FIXTURE_PROFILE_META_KEY = "synthetic_owner_demo_profile"
POLICY_SOURCE_SHA_META_KEY = "synthetic_development_forecast_policy_source_sha256"
POLICY_CANONICAL_SHA_META_KEY = (
    "synthetic_development_forecast_policy_canonical_sha256"
)
SCHEDULE_CONTRACT_META_KEY = "synthetic_development_forecast_schedule_contract"
SCHEDULE_SOURCE_SHA_META_KEY = (
    "synthetic_development_forecast_schedule_source_sha256"
)
SCHEDULE_CANONICAL_SHA_META_KEY = (
    "synthetic_development_forecast_schedule_canonical_sha256"
)
REGISTRATION_META_KEY = "synthetic_development_forecast_registration"
V2_REGISTRATION_CONTRACT = "BUFFALO_SYNTHETIC_DEVELOPMENT_FORECAST_REGISTRATION_V2"
POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "development_forecast_policy_v1.json"
)
V2_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "development_forecast_policy_v2.json"
)
V2_SCHEDULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "development_forecast_schedule_v2.json"
)

UNITS = Decimal("0.0001")
VELOCITY = Decimal("0.000001")
METRIC = Decimal("0.000001")
_SHA256_LENGTH = 64
BASELINE_NEED_BINDING_CONTRACT = "BUFFALO_DEVELOPMENT_BASELINE_NEED_BINDING_V2"
_BASELINE_NEED_KEYS = {
    "status",
    "policy_mode",
    "protection_days",
    "target_units",
    "effective_inventory_units",
    "raw_need_units",
    "cases",
    "loose_units",
    "ordered_units",
    "pack_rounding_units",
    "loose_fee",
    "reason_codes",
}


class DevelopmentForecastError(ValueError):
    """The development policy, input series, or evidence is invalid."""


class _InapplicableModel(ValueError):
    pass


@dataclass(frozen=True)
class DevelopmentForecastPolicy:
    values: Mapping[str, Any]
    source_sha256: str
    canonical_sha256: str
    contract: str
    method_version: str
    source_file: str
    evidence_contract: str

    def evidence(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "method_version": self.method_version,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "canonical_sha256": self.canonical_sha256,
            "commercial_authority": False,
            "production_activation": False,
        }


@dataclass(frozen=True)
class DevelopmentForecastDefinition:
    evidence_contract: str
    policy_contract: str
    method_version: str
    fixture_contract: str
    profile: str
    policy_path: Path
    policy_source_sha256: str
    policy_canonical_sha256: str
    schedule_contract: str | None = None
    schedule_path: Path | None = None
    schedule_source_sha256: str | None = None
    schedule_canonical_sha256: str | None = None


@dataclass(frozen=True)
class DevelopmentForecastSchedule:
    values: Mapping[str, Any]
    source_sha256: str
    canonical_sha256: str
    source_file: str

    def evidence(self) -> dict[str, Any]:
        return {
            "contract": str(self.values["contract"]),
            "profile": str(self.values["profile"]),
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "canonical_sha256": self.canonical_sha256,
            "commercial_authority": False,
            "production_activation": False,
        }


DEVELOPMENT_FORECAST_DEFINITIONS = (
    DevelopmentForecastDefinition(
        evidence_contract=CONTRACT,
        policy_contract=POLICY_CONTRACT,
        method_version=METHOD_VERSION,
        fixture_contract=FIXTURE_CONTRACT,
        profile="development-forecast-v1",
        policy_path=POLICY_PATH,
        policy_source_sha256=(
            "a44a9760b9f9a348cf2c99ab769f9f042ee9873c4938f18e54e2ffb41d3cf6fc"
        ),
        policy_canonical_sha256=(
            "75d5e57423c306ebed4b9ba039a784df8e26d55a3fc6ca4716e4b92a5fd64edf"
        ),
    ),
    DevelopmentForecastDefinition(
        evidence_contract=V2_CONTRACT,
        policy_contract=V2_POLICY_CONTRACT,
        method_version=V2_METHOD_VERSION,
        fixture_contract=V2_FIXTURE_CONTRACT,
        profile="development-forecast-v2",
        policy_path=V2_POLICY_PATH,
        policy_source_sha256=(
            "fee2e91e14565a835f1d69c27ff547ccdda5c22c6bc43a2bb003c2b78f190b52"
        ),
        policy_canonical_sha256=(
            "ff62b1d313a18a907e811e8722431efee2349fc95a31e08ea5843ca2664ca858"
        ),
        schedule_contract=V2_SCHEDULE_CONTRACT,
        schedule_path=V2_SCHEDULE_PATH,
        schedule_source_sha256=(
            "1cb3edf5e82f2f01cb0a4c972d6140f97e767df67468373b245d6dd414ad8640"
        ),
        schedule_canonical_sha256=(
            "2c8b09152d3005b227a93ba700a8c08eec68433bf8f39e44c85353af34639c39"
        ),
    ),
)


def development_forecast_definition(
    evidence_contract: str,
) -> DevelopmentForecastDefinition:
    matches = tuple(
        definition
        for definition in DEVELOPMENT_FORECAST_DEFINITIONS
        if definition.evidence_contract == evidence_contract
    )
    if len(matches) != 1:
        raise DevelopmentForecastError("development forecast contract is unregistered")
    return matches[0]


def _definition_for_policy_contract(
    policy_contract: str,
) -> DevelopmentForecastDefinition:
    matches = tuple(
        definition
        for definition in DEVELOPMENT_FORECAST_DEFINITIONS
        if definition.policy_contract == policy_contract
    )
    if len(matches) != 1:
        raise DevelopmentForecastError("development forecast policy is unregistered")
    return matches[0]


def _definition_for_fixture_contract(
    fixture_contract: str,
) -> DevelopmentForecastDefinition:
    matches = tuple(
        definition
        for definition in DEVELOPMENT_FORECAST_DEFINITIONS
        if definition.fixture_contract == fixture_contract
    )
    if len(matches) != 1:
        raise DevelopmentForecastError("development forecast fixture is unregistered")
    return matches[0]


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
    if variability != 0:
        raise DevelopmentForecastError(
            "nonzero lead-time variability requires a validated delivery-delay model"
        )
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
        "variability_treatment": "ZERO_CONFIRMED_EMPIRICAL_DEMAND_PROTECTION_ONLY",
        "horizon_days": horizon_days,
    }


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise DevelopmentForecastError(f"{field} key inventory differs")


def _schedule_date(value: Any, field: str) -> date:
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise DevelopmentForecastError(f"{field} must be an ISO date") from exc
    return parsed


def _validate_opportunity_set(
    raw: Any,
    *,
    field: str,
    valid_from: date,
    valid_through: date,
) -> None:
    if not isinstance(raw, dict):
        raise DevelopmentForecastError(f"{field} must be an object")
    _require_exact_keys(
        raw,
        {"series", "added_dates", "removed_dates"},
        field,
    )
    series = raw["series"]
    if not isinstance(series, list) or not series:
        raise DevelopmentForecastError(f"{field} requires recurrence series")
    series_ids: list[str] = []
    for index, item in enumerate(series):
        if not isinstance(item, dict):
            raise DevelopmentForecastError(f"{field} series must be objects")
        _require_exact_keys(
            item,
            {"series_id", "anchor_date", "cadence_weeks", "weekday"},
            f"{field}.series[{index}]",
        )
        series_id = str(item["series_id"]).strip()
        anchor = _schedule_date(item["anchor_date"], f"{field}.anchor_date")
        weekday = str(item["weekday"]).strip().upper()
        cadence = item["cadence_weeks"]
        if (
            not series_id
            or weekday not in _WEEKDAY_INDEX
            or anchor.weekday() != _WEEKDAY_INDEX[weekday]
            or not isinstance(cadence, int)
            or isinstance(cadence, bool)
            or cadence not in {1, 2}
            or not (valid_from <= anchor <= valid_through)
        ):
            raise DevelopmentForecastError(f"{field} recurrence differs")
        series_ids.append(series_id)
    if series_ids != sorted(set(series_ids)):
        raise DevelopmentForecastError(f"{field} series IDs differ")
    parsed_exceptions: dict[str, list[date]] = {}
    for key in ("added_dates", "removed_dates"):
        values = raw[key]
        if not isinstance(values, list):
            raise DevelopmentForecastError(f"{field}.{key} must be a list")
        parsed = [_schedule_date(value, f"{field}.{key}") for value in values]
        if (
            parsed != sorted(set(parsed))
            or any(not (valid_from <= value <= valid_through) for value in parsed)
        ):
            raise DevelopmentForecastError(f"{field}.{key} differs")
        parsed_exceptions[key] = parsed
    if set(parsed_exceptions["added_dates"]).intersection(
        parsed_exceptions["removed_dates"]
    ):
        raise DevelopmentForecastError(f"{field} exception dates conflict")


def load_development_forecast_schedule(
    path: Path | None = None,
) -> DevelopmentForecastSchedule:
    """Load the exact synthetic V2 recurrence overlay."""

    definition = development_forecast_definition(V2_CONTRACT)
    resolved_path = definition.schedule_path if path is None else Path(path)
    if resolved_path is None:
        raise DevelopmentForecastError("registered forecast schedule is absent")
    try:
        raw = resolved_path.read_bytes()
        values = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DevelopmentForecastError("development schedule is unreadable") from exc
    if not isinstance(values, dict):
        raise DevelopmentForecastError("development schedule must be an object")
    _require_exact_keys(
        values,
        {
            "contract",
            "version",
            "profile",
            "valid_from",
            "valid_through",
            "provenance",
            "vendors",
            "commercial_authority",
            "production_activation",
        },
        "development schedule",
    )
    valid_from = _schedule_date(values["valid_from"], "schedule.valid_from")
    valid_through = _schedule_date(values["valid_through"], "schedule.valid_through")
    provenance = values["provenance"]
    vendors = values["vendors"]
    if not isinstance(provenance, dict):
        raise DevelopmentForecastError("schedule provenance must be an object")
    _require_exact_keys(
        provenance,
        {"kind", "evidence_ref", "real_supplier_confirmation"},
        "schedule provenance",
    )
    if (
        values.get("contract") != definition.schedule_contract
        or values.get("version") != 2
        or values.get("profile") != definition.profile
        or values.get("commercial_authority") is not False
        or values.get("production_activation") is not False
        or valid_from > valid_through
        or provenance.get("kind") != "FABRICATED_SYNTHETIC_TEST_DATA"
        or not str(provenance.get("evidence_ref") or "").strip()
        or provenance.get("real_supplier_confirmation") is not False
        or not isinstance(vendors, list)
        or not vendors
    ):
        raise DevelopmentForecastError("development schedule identity differs")
    vendor_ids: list[str] = []
    schedule_ids: list[str] = []
    for vendor in vendors:
        if not isinstance(vendor, dict):
            raise DevelopmentForecastError("schedule vendors must be objects")
        _require_exact_keys(
            vendor,
            {
                "vendor_id",
                "schedule_id",
                "timezone_name",
                "submission_cutoff_local",
                "receipt_available_local",
                "lead_time_days",
                "lead_time_variability_days",
                "vendor_rule_order_cycle_days",
                "review_opportunities",
                "submission_opportunities",
                "delivery_opportunities",
            },
            "schedule vendor",
        )
        vendor_id = str(vendor["vendor_id"]).strip()
        schedule_id = str(vendor["schedule_id"]).strip()
        try:
            ZoneInfo(str(vendor["timezone_name"]))
            cutoff = time.fromisoformat(str(vendor["submission_cutoff_local"]))
            receipt_time = time.fromisoformat(str(vendor["receipt_available_local"]))
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise DevelopmentForecastError("schedule local time differs") from exc
        lead_time = vendor["lead_time_days"]
        projected_cycle = vendor["vendor_rule_order_cycle_days"]
        variability = _decimal(
            vendor["lead_time_variability_days"],
            "schedule lead_time_variability_days",
        )
        if (
            not vendor_id
            or not schedule_id
            or cutoff.tzinfo is not None
            or receipt_time.tzinfo is not None
            or receipt_time != time(0, 0)
            or not isinstance(lead_time, int)
            or isinstance(lead_time, bool)
            or lead_time < 0
            or not isinstance(projected_cycle, int)
            or isinstance(projected_cycle, bool)
            or not 1 <= projected_cycle <= 31
            or variability != 0
        ):
            raise DevelopmentForecastError("schedule vendor terms differ")
        vendor_ids.append(vendor_id)
        schedule_ids.append(schedule_id)
        for key in (
            "review_opportunities",
            "submission_opportunities",
            "delivery_opportunities",
        ):
            _validate_opportunity_set(
                vendor[key],
                field=f"schedule vendor {vendor_id} {key}",
                valid_from=valid_from,
                valid_through=valid_through,
            )
    if vendor_ids != sorted(set(vendor_ids)) or len(schedule_ids) != len(
        set(schedule_ids)
    ):
        raise DevelopmentForecastError("schedule vendor identities differ")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    canonical_sha256 = canonical_evidence_sha256(values)
    if path is None and (
        source_sha256 != definition.schedule_source_sha256
        or canonical_sha256 != definition.schedule_canonical_sha256
    ):
        raise DevelopmentForecastError("registered forecast schedule bytes differ")
    return DevelopmentForecastSchedule(
        values=values,
        source_sha256=source_sha256,
        canonical_sha256=canonical_sha256,
        source_file=definition.schedule_path.name,
    )


def _opportunity_trace(raw: Mapping[str, Any], candidate: date) -> dict[str, Any]:
    candidate_text = candidate.isoformat()
    removed = candidate_text in raw["removed_dates"]
    added = candidate_text in raw["added_dates"]
    matching_series = [
        str(item["series_id"])
        for item in raw["series"]
        if candidate.weekday() == _WEEKDAY_INDEX[str(item["weekday"])]
        and (
            (candidate - date.fromisoformat(str(item["anchor_date"]))).days
            % (int(item["cadence_weeks"]) * 7)
            == 0
        )
    ]
    occurs = False if removed else added or bool(matching_series)
    return {
        "date": candidate_text,
        "occurs": occurs,
        "source": (
            "REMOVED"
            if removed
            else "ADDED"
            if added
            else "RECURRENCE"
            if matching_series
            else "NONE"
        ),
        "matching_series_ids": matching_series,
    }


def _first_schedule_occurrence(
    raw: Mapping[str, Any],
    *,
    start: date,
    valid_through: date,
    strictly_after: bool,
) -> tuple[date, dict[str, Any]]:
    first = start + timedelta(days=1) if strictly_after else start
    for offset in range((valid_through - first).days + 1):
        candidate = first + timedelta(days=offset)
        trace = _opportunity_trace(raw, candidate)
        if trace["occurs"]:
            return candidate, trace
    raise DevelopmentForecastError("registered schedule opportunity is unavailable")


def calculate_anchored_schedule_horizon(
    *,
    evaluation_at: datetime,
    business_date: date,
    vendor_id: str,
    vendor_rules: Mapping[str, Any],
    schedule: DevelopmentForecastSchedule | None = None,
) -> dict[str, Any]:
    """Resolve one server-owned V2 schedule into a frozen half-open horizon."""

    if evaluation_at.tzinfo is None:
        raise DevelopmentForecastError("evaluation_at must be timezone-aware")
    if not isinstance(business_date, date) or isinstance(business_date, datetime):
        raise DevelopmentForecastError("business_date must be a date")
    active_schedule = schedule or load_development_forecast_schedule()
    values = active_schedule.values
    matching = [
        item for item in values["vendors"] if item["vendor_id"] == str(vendor_id)
    ]
    if len(matching) != 1:
        raise DevelopmentForecastError("vendor schedule is not registered")
    vendor = matching[0]
    valid_from = date.fromisoformat(str(values["valid_from"]))
    valid_through = date.fromisoformat(str(values["valid_through"]))
    zone = ZoneInfo(str(vendor["timezone_name"]))
    local_evaluation = evaluation_at.astimezone(zone)
    cutoff = time.fromisoformat(str(vendor["submission_cutoff_local"]))
    if (
        local_evaluation.date() != business_date
        or not (valid_from <= business_date <= valid_through)
        or local_evaluation.timetz().replace(tzinfo=None) >= cutoff
    ):
        raise DevelopmentForecastError("evaluation is not an open registered schedule")
    current_review_trace = _opportunity_trace(
        vendor["review_opportunities"], business_date
    )
    current_submission_trace = _opportunity_trace(
        vendor["submission_opportunities"], business_date
    )
    if not current_review_trace["occurs"] or not current_submission_trace["occurs"]:
        raise DevelopmentForecastError("evaluation is off the registered schedule")
    lead_time = int(vendor["lead_time_days"])
    current_receipt, current_receipt_trace = _first_schedule_occurrence(
        vendor["delivery_opportunities"],
        start=business_date + timedelta(days=lead_time),
        valid_through=valid_through,
        strictly_after=False,
    )
    next_review, next_review_trace = _first_schedule_occurrence(
        vendor["review_opportunities"],
        start=business_date,
        valid_through=valid_through,
        strictly_after=True,
    )
    next_submission, next_submission_trace = _first_schedule_occurrence(
        vendor["submission_opportunities"],
        start=business_date,
        valid_through=valid_through,
        strictly_after=True,
    )
    next_receipt, next_receipt_trace = _first_schedule_occurrence(
        vendor["delivery_opportunities"],
        start=next_submission + timedelta(days=lead_time),
        valid_through=valid_through,
        strictly_after=False,
    )
    horizon_days = (next_receipt - business_date).days
    submission_weekdays = sorted(
        {str(item["weekday"]) for item in vendor["submission_opportunities"]["series"]},
        key=lambda item: _WEEKDAY_INDEX[item],
    )
    delivery_weekdays = sorted(
        {str(item["weekday"]) for item in vendor["delivery_opportunities"]["series"]},
        key=lambda item: _WEEKDAY_INDEX[item],
    )
    normalized_rules = {
        "timezone_name": str(vendor_rules.get("timezone_name")),
        "order_days": [str(item).strip().upper() for item in vendor_rules.get("order_days", [])],
        "order_cutoff_local": (
            vendor_rules.get("order_cutoff_local").isoformat()
            if isinstance(vendor_rules.get("order_cutoff_local"), time)
            else str(vendor_rules.get("order_cutoff_local"))
        ),
        "expected_delivery_days": [
            str(item).strip().upper()
            for item in vendor_rules.get("expected_delivery_days", [])
        ],
        "order_cycle_days": vendor_rules.get("order_cycle_days"),
        "lead_time_days": vendor_rules.get("lead_time_days"),
        "lead_time_variability_days": (
            "0"
            if _decimal(
                vendor_rules.get("lead_time_variability_days"),
                "vendor lead_time_variability_days",
            )
            == 0
            else str(
                _decimal(
                    vendor_rules.get("lead_time_variability_days"),
                    "vendor lead_time_variability_days",
                ).normalize()
            )
        ),
    }
    expected_rules = {
        "timezone_name": str(vendor["timezone_name"]),
        "order_days": submission_weekdays,
        "order_cutoff_local": cutoff.isoformat(),
        "expected_delivery_days": delivery_weekdays,
        "order_cycle_days": int(vendor["vendor_rule_order_cycle_days"]),
        "lead_time_days": lead_time,
        "lead_time_variability_days": "0",
    }
    if normalized_rules != expected_rules:
        raise DevelopmentForecastError("vendor rules differ from registered schedule")
    if horizon_days < 1 or horizon_days > 31:
        raise DevelopmentForecastError("registered schedule horizon is outside bounds")
    return {
        "basis": "ANCHORED_REVIEW_SUBMISSION_DELIVERY_SCHEDULE_V2",
        "schedule": active_schedule.evidence(),
        "schedule_id": str(vendor["schedule_id"]),
        "vendor_id": str(vendor_id),
        "timezone_name": str(vendor["timezone_name"]),
        "valid_from": valid_from.isoformat(),
        "valid_through": valid_through.isoformat(),
        "evaluation_at": local_evaluation.isoformat(),
        "business_date": business_date.isoformat(),
        "submission_cutoff_local": cutoff.isoformat(),
        "receipt_available_local": str(vendor["receipt_available_local"]),
        "current_review_date": business_date.isoformat(),
        "current_submission_date": business_date.isoformat(),
        "current_order_receipt_date": current_receipt.isoformat(),
        "next_review_date": next_review.isoformat(),
        "next_submission_date": next_submission.isoformat(),
        "next_order_receipt_date": next_receipt.isoformat(),
        "lead_time_days": lead_time,
        "lead_time_variability_days": "0",
        "interval_semantics": (
            "HALF_OPEN_[EVALUATION_DATE,NEXT_RECEIPT)_RECEIPT_START_OF_DAY"
        ),
        "target_start_date": business_date.isoformat(),
        "target_end_exclusive": next_receipt.isoformat(),
        "horizon_days": horizon_days,
        "vendor_rules_projection": expected_rules,
        "opportunity_sets": {
            "review": vendor["review_opportunities"],
            "submission": vendor["submission_opportunities"],
            "delivery": vendor["delivery_opportunities"],
        },
        "resolved_occurrences": {
            "current_review": current_review_trace,
            "current_submission": current_submission_trace,
            "current_order_receipt": current_receipt_trace,
            "next_review": next_review_trace,
            "next_submission": next_submission_trace,
            "next_order_receipt": next_receipt_trace,
        },
    }


def validate_anchored_schedule_evidence(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        expected = calculate_anchored_schedule_horizon(
            evaluation_at=datetime.fromisoformat(str(value["evaluation_at"])),
            business_date=date.fromisoformat(str(value["business_date"])),
            vendor_id=str(value["vendor_id"]),
            vendor_rules=value["vendor_rules_projection"],
        )
        return canonical_evidence_json(expected) == canonical_evidence_json(value)
    except (DevelopmentForecastError, KeyError, TypeError, ValueError):
        return False


def load_development_forecast_policy(
    path: Path | None = None,
    *,
    evidence_contract: str = CONTRACT,
) -> DevelopmentForecastPolicy:
    """Load and deeply validate the checked-in development policy."""

    definition = development_forecast_definition(evidence_contract)
    resolved_path = definition.policy_path if path is None else Path(path)
    try:
        raw = resolved_path.read_bytes()
        values = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DevelopmentForecastError(
            "development forecast policy is unreadable"
        ) from exc
    if not isinstance(values, dict):
        raise DevelopmentForecastError("development forecast policy must be an object")
    expected_policy_keys = {
            "abc",
            "availability",
            "candidate_order",
            "category_shrinkage",
            "commercial_authority",
            "contract",
            "damped_ets",
            "fva",
            "history",
            "method_version",
            "metrics",
            "naive",
            "production_activation",
            "protection",
            "rate_cap",
            "selection_metric",
            "seasonal_naive",
            "tsb",
            "tie_break_rule",
            "windows",
            "xyz",
    }
    if definition.evidence_contract == V2_CONTRACT:
        expected_policy_keys.add("confidence")
    _require_exact_keys(values, expected_policy_keys, "development forecast policy")
    candidate_order = values.get("candidate_order")
    if (
        values.get("contract") != definition.policy_contract
        or values.get("method_version") != definition.method_version
        or values.get("commercial_authority") is not False
        or values.get("production_activation") is not False
        or values.get("selection_metric") != "WAPE_WHEN_DEFINED_ELSE_MAE"
        or values.get("tie_break_rule") != "CANDIDATE_ORDER"
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
    required_objects = [
        "abc",
        "availability",
        "category_shrinkage",
        "damped_ets",
        "fva",
        "history",
        "metrics",
        "naive",
        "protection",
        "rate_cap",
        "seasonal_naive",
        "tsb",
        "windows",
        "xyz",
    ]
    if definition.evidence_contract == V2_CONTRACT:
        required_objects.append("confidence")
    for field in required_objects:
        if not isinstance(values.get(field), dict):
            raise DevelopmentForecastError(f"policy {field} must be an object")
    nested_key_inventory = {
        "abc": {
            "a_cumulative_share",
            "b_incremental_share",
            "basis",
            "classification_period_days",
            "missing_cost_status",
        },
        "category_shrinkage": {"minimum_history_days", "prior_strength_days"},
        "damped_ets": {"alpha", "beta", "minimum_history_days", "phi"},
        "fva": {"minimum_relative_improvement", "simple_baseline"},
        "naive": {"trailing_window_days"},
        "protection": {
            "minimum_full_horizon_origins",
            "quantile",
            "residual_definition",
        },
        "rate_cap": {"maximum_calendar_mean_multiplier"},
        "seasonal_naive": {"minimum_history_days", "period_days"},
        "tsb": {
            "alpha_probability",
            "alpha_size",
            "intermittent_zero_share",
            "minimum_history_days",
            "minimum_positive_days",
        },
        "xyz": {
            "minimum_week_buckets",
            "x_max_coefficient_of_variation",
            "y_max_coefficient_of_variation",
            "zero_demand_class",
        },
    }
    for field, keys in nested_key_inventory.items():
        _require_exact_keys(values[field], keys, f"policy {field}")
    if definition.evidence_contract == V2_CONTRACT:
        confidence = values["confidence"]
        _require_exact_keys(
            confidence,
            {
                "boundary_rule",
                "high_max_inclusive",
                "medium_max_inclusive",
                "metric",
            },
            "policy confidence",
        )
        high = _decimal(
            confidence.get("high_max_inclusive"),
            "confidence.high_max_inclusive",
        )
        medium = _decimal(
            confidence.get("medium_max_inclusive"),
            "confidence.medium_max_inclusive",
        )
        if not (
            confidence.get("boundary_rule") == "UPPER_BOUNDS_INCLUSIVE"
            and confidence.get("metric") == "EVALUATION_WAPE"
            and high >= 0
            and medium >= 0
            and high < medium
        ):
            raise DevelopmentForecastError("policy confidence thresholds differ")
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
    history = values["history"]
    _require_exact_keys(
        history,
        {"connected_history_days", "maximum_history_days"},
        "policy history",
    )
    connected_history_days = _positive_int(
        history.get("connected_history_days"), "history.connected_history_days"
    )
    maximum_history_days = _positive_int(
        history.get("maximum_history_days"), "history.maximum_history_days"
    )
    if connected_history_days > maximum_history_days:
        raise DevelopmentForecastError("connected forecast history exceeds its cap")
    if definition.evidence_contract == V2_CONTRACT and (
        connected_history_days != 138
        or maximum_history_days != 138
        or windows
        != {
            "calibration_days": 38,
            "evaluation_days": 34,
            "minimum_evaluation_origins": 4,
            "minimum_selection_origins": 8,
            "minimum_training_days": 28,
            "selection_origin_days": 38,
        }
    ):
        raise DevelopmentForecastError("V2 chronological policy differs")
    availability = values["availability"]
    _require_exact_keys(
        availability,
        {"minimum_proven_in_stock_days_for_full_protection"},
        "policy availability",
    )
    _positive_int(
        availability.get("minimum_proven_in_stock_days_for_full_protection"),
        "availability.minimum_proven_in_stock_days_for_full_protection",
    )
    if values["metrics"] != {
        "bias": "MEAN_FORECAST_MINUS_ACTUAL",
        "mae": "MEAN_ABSOLUTE_ERROR",
        "mase": "MAE_DIVIDED_BY_PRESELECTION_NAIVE_SCALE_OR_NULL",
        "wape": "SUM_ABSOLUTE_ERROR_DIVIDED_BY_SUM_ABSOLUTE_ACTUAL_OR_NULL",
    }:
        raise DevelopmentForecastError("policy metric definitions differ")
    if values["abc"].get("basis") != "HISTORICAL_GROSS_PROFIT_DOLLARS":
        raise DevelopmentForecastError("policy ABC basis differs")
    if values["abc"].get("missing_cost_status") != "NOT_CONFIGURED":
        raise DevelopmentForecastError("policy missing-cost status differs")
    if values["fva"].get("simple_baseline") != "NAIVE":
        raise DevelopmentForecastError("policy FVA baseline differs")
    if values["xyz"].get("zero_demand_class") != "Z":
        raise DevelopmentForecastError("policy zero-demand class differs")
    for obj, key in (
        (values["category_shrinkage"], "minimum_history_days"),
        (values["damped_ets"], "minimum_history_days"),
        (values["naive"], "trailing_window_days"),
        (values["protection"], "minimum_full_horizon_origins"),
        (values["seasonal_naive"], "minimum_history_days"),
        (values["seasonal_naive"], "period_days"),
        (values["tsb"], "minimum_history_days"),
        (values["tsb"], "minimum_positive_days"),
        (values["xyz"], "minimum_week_buckets"),
    ):
        _positive_int(obj.get(key), key)
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
    source_sha256 = hashlib.sha256(raw).hexdigest()
    canonical_sha256 = canonical_evidence_sha256(values)
    if path is None and (
        source_sha256 != definition.policy_source_sha256
        or canonical_sha256 != definition.policy_canonical_sha256
    ):
        raise DevelopmentForecastError("registered forecast policy bytes differ")
    return DevelopmentForecastPolicy(
        values=values,
        source_sha256=source_sha256,
        canonical_sha256=canonical_sha256,
        contract=definition.policy_contract,
        method_version=definition.method_version,
        source_file=definition.policy_path.name,
        evidence_contract=definition.evidence_contract,
    )


def development_forecast_v2_registration() -> dict[str, Any]:
    """Return the exact self-hashed registration published by the V2 fixture."""

    definition = development_forecast_definition(V2_CONTRACT)
    policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
    schedule = load_development_forecast_schedule()
    unsigned = {
        "contract": V2_REGISTRATION_CONTRACT,
        "profile": definition.profile,
        "fixture_contract": definition.fixture_contract,
        "evidence_contract": definition.evidence_contract,
        "method_version": definition.method_version,
        "policy_contract": definition.policy_contract,
        "policy_source_sha256": policy.source_sha256,
        "policy_canonical_sha256": policy.canonical_sha256,
        "schedule_contract": definition.schedule_contract,
        "schedule_source_sha256": schedule.source_sha256,
        "schedule_canonical_sha256": schedule.canonical_sha256,
        "commercial_authority": False,
        "production_activation": False,
    }
    return {**unsigned, "sha256": canonical_evidence_sha256(unsigned)}


def development_forecast_definition_for_run(
    conn: Any, *, offer_resolution_contract: str | None
) -> DevelopmentForecastDefinition | None:
    """Resolve the indivisible server-owned synthetic forecast tuple."""

    requested_keys = [
        FIXTURE_META_KEY,
        FIXTURE_PROFILE_META_KEY,
        REGISTRATION_META_KEY,
        "synthetic_multivendor_acceptance_contract",
    ]
    rows = conn.execute(
        "SELECT key,value FROM meta WHERE key=ANY(%s) ORDER BY key",
        (requested_keys,),
    ).fetchall()
    metadata = {str(key): str(value) for key, value in rows}
    marker = metadata.get(FIXTURE_META_KEY)
    related = {
        key for key in (FIXTURE_PROFILE_META_KEY, REGISTRATION_META_KEY) if key in metadata
    }
    if marker is None:
        if related:
            raise DevelopmentForecastError(
                "synthetic development forecast registration is partial"
            )
        return None
    try:
        definition = _definition_for_fixture_contract(marker)
    except DevelopmentForecastError as exc:
        raise DevelopmentForecastError(
            "synthetic development forecast fixture marker differs"
        ) from exc
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
    if definition.evidence_contract == CONTRACT:
        if related:
            raise DevelopmentForecastError(
                "V1 development forecast registration contains V2 identity"
            )
        return definition
    if (
        metadata.get(FIXTURE_PROFILE_META_KEY) != definition.profile
        or REGISTRATION_META_KEY not in metadata
    ):
        raise DevelopmentForecastError(
            "V2 development forecast registration is incomplete"
        )
    try:
        registration = json.loads(metadata[REGISTRATION_META_KEY])
    except (TypeError, json.JSONDecodeError) as exc:
        raise DevelopmentForecastError(
            "V2 development forecast registration is malformed"
        ) from exc
    expected = development_forecast_v2_registration()
    if (
        not isinstance(registration, dict)
        or canonical_evidence_json(registration) != canonical_evidence_json(expected)
        or registration.get("sha256")
        != canonical_evidence_sha256(
            {key: value for key, value in registration.items() if key != "sha256"}
        )
    ):
        raise DevelopmentForecastError(
            "V2 development forecast registration differs"
        )
    return definition


def development_forecast_contract_for_run(
    conn: Any, *, offer_resolution_contract: str | None
) -> str | None:
    """Compatibility wrapper returning the registered evidence contract."""

    definition = development_forecast_definition_for_run(
        conn,
        offer_resolution_contract=offer_resolution_contract,
    )
    return None if definition is None else definition.evidence_contract


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
    value = metrics.wape if metrics.wape is not None else metrics.mae
    return value.quantize(METRIC, rounding=ROUND_HALF_UP)


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
    # The class decision must use the same precision frozen into evidence so
    # a genuine plan cannot cross a boundary when it is later revalidated.
    coefficient = (variance.sqrt() / mean).quantize(
        METRIC, rounding=ROUND_HALF_UP
    )
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


def _confidence_thresholds(
    policy: DevelopmentForecastPolicy,
) -> tuple[Decimal, Decimal]:
    if policy.evidence_contract == V2_CONTRACT:
        confidence = policy.values["confidence"]
        return (
            _decimal(
                confidence["high_max_inclusive"],
                "confidence.high_max_inclusive",
            ).quantize(METRIC, rounding=ROUND_HALF_UP),
            _decimal(
                confidence["medium_max_inclusive"],
                "confidence.medium_max_inclusive",
            ).quantize(METRIC, rounding=ROUND_HALF_UP),
        )
    return Decimal("0.20"), Decimal("0.50")


def _confidence_policy_evidence(
    policy: DevelopmentForecastPolicy,
) -> dict[str, Any]:
    high, medium = _confidence_thresholds(policy)
    return {
        "metric": "EVALUATION_WAPE",
        "boundary_rule": "UPPER_BOUNDS_INCLUSIVE",
        "high_max_inclusive": str(high),
        "medium_max_inclusive": str(medium),
    }


def _classify_confidence(
    *,
    evaluation_status: str,
    availability_limited: bool,
    evaluation_wape: Decimal | None,
    policy: DevelopmentForecastPolicy,
) -> str:
    high, medium = _confidence_thresholds(policy)
    if (
        evaluation_status != "SUFFICIENT"
        or availability_limited
        or evaluation_wape is None
    ):
        return "LOW"
    if evaluation_wape <= high:
        return "HIGH"
    if evaluation_wape <= medium:
        return "MEDIUM"
    return "LOW"


def _origin_window_evidence(
    *,
    ordered: Sequence[DemandObservation],
    origins: Sequence[int],
    horizon: int,
    usable_origin_dates: Sequence[str],
    censored_origin_dates: Sequence[str],
    model_applicability: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    planned_dates = [ordered[index].business_date.isoformat() for index in origins]
    intervals = [
        {
            "origin_date": ordered[index].business_date.isoformat(),
            "target_start_date": ordered[index].business_date.isoformat(),
            "target_end_exclusive": (
                ordered[index].business_date + timedelta(days=horizon)
            ).isoformat(),
        }
        for index in origins
    ]
    return {
        "planned_origin_count": len(planned_dates),
        "planned_origin_dates": planned_dates,
        "usable_origin_count": len(usable_origin_dates),
        "usable_origin_dates": list(usable_origin_dates),
        "censored_origin_count": len(censored_origin_dates),
        "censored_origin_dates": list(censored_origin_dates),
        "model_applicability": {
            str(name): {
                "origin_count": len(dates),
                "origin_dates": list(dates),
            }
            for name, dates in model_applicability.items()
        },
        "target_intervals": intervals,
        "target_interval_semantics": "HALF_OPEN_[ORIGIN,ORIGIN_PLUS_HORIZON)",
        "overlapping_targets": horizon > 1 and len(planned_dates) > 1,
        "independent_samples": False,
    }


def _v2_origin_windows(
    *,
    ordered: Sequence[DemandObservation],
    horizon: int,
    selection_start: int,
    calibration_start: int,
    evaluation_start: int,
    candidate_records: Sequence[Mapping[str, Any]],
    selection_usable_origin_dates: Sequence[str],
    selected_model: str | None,
    calibration_usable_origin_dates: Sequence[str] = (),
    evaluation_usable_origin_dates: Sequence[str] = (),
) -> dict[str, Any]:
    """Freeze all planned V2 origins, including fail-closed blocked paths."""

    selection_indices = list(
        range(
            selection_start,
            max(selection_start, calibration_start - horizon + 1),
        )
    )
    calibration_indices = list(
        range(
            calibration_start,
            max(calibration_start, evaluation_start - horizon + 1),
        )
    )
    evaluation_indices = list(
        range(
            evaluation_start,
            max(evaluation_start, len(ordered) - horizon + 1),
        )
    )

    def censored(indices: Sequence[int]) -> list[str]:
        return [
            ordered[index].business_date.isoformat()
            for index in indices
            if any(
                item.inventory_state == "STOCKOUT"
                for item in ordered[index : index + horizon]
            )
        ]

    model_name = selected_model or "UNSELECTED"
    selection_applicability = {
        str(record["name"]): list(record.get("selection_origin_dates", []))
        for record in candidate_records
        if isinstance(record, Mapping) and isinstance(record.get("name"), str)
    }
    if not selection_applicability:
        selection_applicability = {model_name: []}
    return {
        "selection": _origin_window_evidence(
            ordered=ordered,
            origins=selection_indices,
            horizon=horizon,
            usable_origin_dates=selection_usable_origin_dates,
            censored_origin_dates=censored(selection_indices),
            model_applicability=selection_applicability,
        ),
        "calibration": _origin_window_evidence(
            ordered=ordered,
            origins=calibration_indices,
            horizon=horizon,
            usable_origin_dates=calibration_usable_origin_dates,
            censored_origin_dates=censored(calibration_indices),
            model_applicability={model_name: list(calibration_usable_origin_dates)},
        ),
        "evaluation": _origin_window_evidence(
            ordered=ordered,
            origins=evaluation_indices,
            horizon=horizon,
            usable_origin_dates=evaluation_usable_origin_dates,
            censored_origin_dates=censored(evaluation_indices),
            model_applicability={model_name: list(evaluation_usable_origin_dates)},
        ),
        "partition": {
            "training_start_index": 0,
            "training_end_exclusive": selection_start,
            "selection_start_index": selection_start,
            "selection_end_exclusive": calibration_start,
            "calibration_start_index": calibration_start,
            "calibration_end_exclusive": evaluation_start,
            "evaluation_start_index": evaluation_start,
            "evaluation_end_exclusive": len(ordered),
        },
    }


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
    definition = development_forecast_definition(active_policy.evidence_contract)
    if (
        active_policy.contract != definition.policy_contract
        or active_policy.method_version != definition.method_version
    ):
        raise DevelopmentForecastError("development forecast policy tuple differs")
    ordered, normalized = _validated_observations(observations)
    if len(ordered) > int(active_policy.values["history"]["maximum_history_days"]):
        raise DevelopmentForecastError("forecast history exceeds the policy cap")
    if definition.evidence_contract == V2_CONTRACT and len(ordered) != int(
        active_policy.values["history"]["connected_history_days"]
    ):
        raise DevelopmentForecastError("V2 forecast history must contain exactly 138 days")
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
    if (
        definition.evidence_contract == V2_CONTRACT
        and calendar_evidence.get("basis")
        == "ANCHORED_REVIEW_SUBMISSION_DELIVERY_SCHEDULE_V2"
    ):
        try:
            target_start = date.fromisoformat(
                str(calendar_evidence["target_start_date"])
            )
            target_end = date.fromisoformat(
                str(calendar_evidence["target_end_exclusive"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DevelopmentForecastError(
                "anchored protection calendar target dates differ"
            ) from exc
        if (
            target_start != ordered[-1].business_date + timedelta(days=1)
            or target_end != target_start + timedelta(days=horizon)
            or str(calendar_evidence.get("business_date"))
            != target_start.isoformat()
        ):
            raise DevelopmentForecastError(
                "anchored protection calendar is not adjacent to forecast history"
            )
    base_payload: dict[str, Any] = {
        "contract": definition.evidence_contract,
        "method_version": definition.method_version,
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
    if definition.evidence_contract == V2_CONTRACT:
        base_payload["confidence_policy"] = _confidence_policy_evidence(
            active_policy
        )
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
        blocked_payload = {
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
        if definition.evidence_contract == V2_CONTRACT:
            baseline_record = next(
                (
                    record
                    for record in candidate_records
                    if record.get("name") == baseline_name
                ),
                None,
            )
            blocked_payload["origin_windows"] = _v2_origin_windows(
                ordered=ordered,
                horizon=horizon,
                selection_start=selection_start,
                calibration_start=calibration_start,
                evaluation_start=evaluation_start,
                candidate_records=candidate_records,
                selection_usable_origin_dates=(
                    []
                    if baseline_record is None
                    else baseline_record.get("selection_origin_dates", [])
                ),
                selected_model=None,
            )
        return _finalize_evidence(blocked_payload)
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
    evaluation_origin_dates: list[str] = []
    censored_evaluation_origin_dates: list[str] = []
    evaluation_cap_hits = 0
    last_evaluation_origin = len(ordered) - horizon
    for origin in range(evaluation_start, last_evaluation_origin + 1):
        target_window = ordered[origin : origin + horizon]
        if any(item.inventory_state == "STOCKOUT" for item in target_window):
            censored_evaluation_origin_dates.append(
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
            evaluation_cap_hits += cap_hit
        except _InapplicableModel as exc:
            raise DevelopmentForecastError(
                "selected model became inapplicable during evaluation"
            ) from exc
        evaluation_actuals.append(
            sum(normalized[origin : origin + horizon], Decimal("0"))
        )
        evaluation_forecasts.append(predicted)
        evaluation_origin_dates.append(ordered[origin].business_date.isoformat())
    if not evaluation_actuals:
        if definition.evidence_contract == V2_CONTRACT:
            for record in candidate_records:
                record["selected"] = False
            selected_record = next(
                record
                for record in candidate_records
                if record.get("name") == selected_name
            )
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
                    "reason_codes": ["EVALUATION_ORIGINS_UNAVAILABLE"],
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
                    "origin_windows": _v2_origin_windows(
                        ordered=ordered,
                        horizon=horizon,
                        selection_start=selection_start,
                        calibration_start=calibration_start,
                        evaluation_start=evaluation_start,
                        candidate_records=candidate_records,
                        selection_usable_origin_dates=selected_record.get(
                            "selection_origin_dates", []
                        ),
                        selected_model=None,
                    ),
                }
            )
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
        else (
            "INSUFFICIENT_FOR_READINESS"
            if definition.evidence_contract == V2_CONTRACT
            else "INSUFFICIENT_FOR_CONFIDENCE"
        )
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
    proven_in_stock_days = sum(
        1 for item in ordered if item.inventory_state == "IN_STOCK"
    )
    unknown_days = sum(1 for item in ordered if item.inventory_state == "UNKNOWN")
    minimum_proven_in_stock_days = int(
        active_policy.values["availability"][
            "minimum_proven_in_stock_days_for_full_protection"
        ]
    )
    availability_limited = (
        unknown_days > 0
        or proven_in_stock_days < minimum_proven_in_stock_days
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
            "proven_in_stock_days": proven_in_stock_days,
            "unknown_days": unknown_days,
            "minimum_proven_in_stock_days_for_full_protection": (
                minimum_proven_in_stock_days
            ),
            "protection_qualification": (
                "LIMITED" if availability_limited else "FULL"
            ),
            "negative_net_days": sum(1 for item in ordered if item.net_units < 0),
        },
    }
    if definition.evidence_contract == V2_CONTRACT:
        common_payload["origin_windows"] = _v2_origin_windows(
            ordered=ordered,
            horizon=horizon,
            selection_start=selection_start,
            calibration_start=calibration_start,
            evaluation_start=evaluation_start,
            candidate_records=candidate_records,
            selection_usable_origin_dates=next(
                record["selection_origin_dates"]
                for record in candidate_records
                if record["name"] == selected_name
            ),
            selected_model=selected_name,
            calibration_usable_origin_dates=calibration_origin_dates,
            evaluation_usable_origin_dates=evaluation_origin_dates,
        )
    if (
        definition.evidence_contract == V2_CONTRACT
        and len(evaluation_actuals) < minimum_evaluation
    ):
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
                    "EVALUATION_ORIGINS_INSUFFICIENT_FOR_READINESS",
                ],
                "protection": {
                    "status": "WITHHELD_EVALUATION_ORIGINS_INSUFFICIENT",
                    "method": "FULL_HORIZON_SHORTFALL_EMPIRICAL_QUANTILE",
                    "quantile": active_policy.values["protection"]["quantile"],
                    "minimum_origin_count": minimum_calibration,
                    "full_horizon_origin_count": len(shortfalls),
                    "censored_stockout_origin_count": len(
                        censored_calibration_origin_dates
                    ),
                    "censored_stockout_origin_dates": (
                        censored_calibration_origin_dates
                    ),
                    "origin_dates": calibration_origin_dates,
                    "shortfalls": [str(item.quantize(UNITS)) for item in shortfalls],
                    "availability_qualification": (
                        "LIMITED" if availability_limited else "FULL"
                    ),
                },
            }
        )
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
                    "availability_qualification": (
                        "LIMITED" if availability_limited else "FULL"
                    ),
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
    evaluation_wape = (
        None
        if evaluation.wape is None
        else evaluation.wape.quantize(METRIC, rounding=ROUND_HALF_UP)
    )
    confidence = _classify_confidence(
        evaluation_status=evaluation_status,
        availability_limited=availability_limited,
        evaluation_wape=evaluation_wape,
        policy=active_policy,
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
    if availability_limited:
        reasons.append("AVAILABILITY_COVERAGE_LIMITS_PROTECTION")
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
                "status": (
                    "CALCULATED_LIMITED_AVAILABILITY"
                    if availability_limited
                    else "CALCULATED"
                ),
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
                "availability_qualification": (
                    "LIMITED" if availability_limited else "FULL"
                ),
            },
        }
    )


def _validate_v2_origin_window(
    raw: Any,
    *,
    history_start: date,
    start_index: int,
    end_exclusive: int,
    horizon: int,
) -> bool:
    if not isinstance(raw, dict):
        return False
    expected_keys = {
        "planned_origin_count",
        "planned_origin_dates",
        "usable_origin_count",
        "usable_origin_dates",
        "censored_origin_count",
        "censored_origin_dates",
        "model_applicability",
        "target_intervals",
        "target_interval_semantics",
        "overlapping_targets",
        "independent_samples",
    }
    last_origin = end_exclusive - horizon
    indices = list(range(start_index, max(start_index, last_origin + 1)))
    planned = [
        (history_start + timedelta(days=index)).isoformat() for index in indices
    ]
    intervals = [
        {
            "origin_date": origin,
            "target_start_date": origin,
            "target_end_exclusive": (
                date.fromisoformat(origin) + timedelta(days=horizon)
            ).isoformat(),
        }
        for origin in planned
    ]
    usable = raw.get("usable_origin_dates")
    censored = raw.get("censored_origin_dates")
    applicability = raw.get("model_applicability")
    if not (
        set(raw) == expected_keys
        and raw.get("planned_origin_count") == len(planned)
        and raw.get("planned_origin_dates") == planned
        and isinstance(usable, list)
        and usable == sorted(set(usable))
        and isinstance(censored, list)
        and censored == sorted(set(censored))
        and raw.get("usable_origin_count") == len(usable)
        and raw.get("censored_origin_count") == len(censored)
        and set(usable).issubset(planned)
        and set(censored).issubset(planned)
        and not set(usable).intersection(censored)
        and isinstance(applicability, dict)
        and applicability
        and raw.get("target_intervals") == intervals
        and raw.get("target_interval_semantics")
        == "HALF_OPEN_[ORIGIN,ORIGIN_PLUS_HORIZON)"
        and raw.get("overlapping_targets") == (horizon > 1 and len(planned) > 1)
        and raw.get("independent_samples") is False
    ):
        return False
    for model, evidence in applicability.items():
        if not isinstance(model, str) or not model or not isinstance(evidence, dict):
            return False
        dates = evidence.get("origin_dates")
        if not (
            set(evidence) == {"origin_count", "origin_dates"}
            and isinstance(dates, list)
            and dates == sorted(set(dates))
            and set(dates).issubset(planned)
            and evidence.get("origin_count") == len(dates)
        ):
            return False
    return True


def _validate_v2_development_forecast_evidence(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        unsigned = {key: item for key, item in value.items() if key != "sha256"}
        history_start = date.fromisoformat(str(value["history_start"]))
        history_end = date.fromisoformat(str(value["history_end"]))
        horizon = int(value["horizon_days"])
        base_keys = {
            "contract",
            "method_version",
            "policy",
            "history_start",
            "history_end",
            "cutoff_date",
            "history_days",
            "horizon_days",
            "protection_calendar",
            "calendar_velocity",
            "in_stock_velocity",
            "input_sha256",
            "category_prior",
            "commercial_authority",
            "production_activation",
            "status",
            "selected_model",
            "demand_regime",
            "xyz_class",
            "xyz_coefficient_of_variation",
            "abc_class",
            "forecast_daily_velocity",
            "point_forecast_units",
            "protection_units",
            "target_units",
            "confidence",
            "confidence_policy",
            "reason_codes",
            "split",
            "candidates",
            "protection",
            "caps",
            "origin_windows",
            "sha256",
        }
        selected_keys = {
            "abc_status",
            "selection_metrics",
            "evaluation_metrics",
            "evaluation_origin_count",
            "minimum_evaluation_origin_count",
            "evaluation_status",
            "availability",
        }
        expected_keys = base_keys | (
            selected_keys if value.get("selected_model") is not None else set()
        )
        if not (
            set(value) == expected_keys
            and value.get("contract") == V2_CONTRACT
            and value.get("method_version") == V2_METHOD_VERSION
            and value.get("policy") == policy.evidence()
            and value.get("history_days") == 138
            and history_end == history_start + timedelta(days=137)
            and str(value.get("cutoff_date")) == history_end.isoformat()
            and 1 <= horizon <= 31
            and value.get("commercial_authority") is False
            and value.get("production_activation") is False
            and value.get("status") in {"READY", "BLOCKED"}
            and value.get("confidence") in {"HIGH", "MEDIUM", "LOW"}
            and value.get("confidence_policy")
            == _confidence_policy_evidence(policy)
            and isinstance(value.get("sha256"), str)
            and value["sha256"] == canonical_evidence_sha256(unsigned)
            and isinstance(value.get("input_sha256"), str)
            and len(value["input_sha256"]) == _SHA256_LENGTH
            and bytes.fromhex(value["input_sha256"])
            and isinstance(value.get("reason_codes"), list)
            and len(value["reason_codes"]) == len(set(value["reason_codes"]))
            and all(isinstance(item, str) and item for item in value["reason_codes"])
        ):
            return False
        calendar = value.get("protection_calendar")
        if not isinstance(calendar, dict) or calendar.get("horizon_days") != horizon:
            return False
        if calendar.get("basis") == "CALLER_VALIDATED_HORIZON":
            if set(calendar) != {"basis", "horizon_days"}:
                return False
        elif not validate_anchored_schedule_evidence(calendar):
            return False
        elif (
            date.fromisoformat(str(calendar.get("target_start_date")))
            != history_end + timedelta(days=1)
            or date.fromisoformat(str(calendar.get("target_end_exclusive")))
            != history_end + timedelta(days=1 + horizon)
        ):
            return False
        partition = value["origin_windows"].get("partition")
        if partition != {
            "training_start_index": 0,
            "training_end_exclusive": 28,
            "selection_start_index": 28,
            "selection_end_exclusive": 66,
            "calibration_start_index": 66,
            "calibration_end_exclusive": 104,
            "evaluation_start_index": 104,
            "evaluation_end_exclusive": 138,
        }:
            return False
        if set(value["origin_windows"]) != {
            "selection",
            "calibration",
            "evaluation",
            "partition",
        }:
            return False
        for name, start, end in (
            ("selection", 28, 66),
            ("calibration", 66, 104),
            ("evaluation", 104, 138),
        ):
            if not _validate_v2_origin_window(
                value["origin_windows"][name],
                history_start=history_start,
                start_index=start,
                end_exclusive=end,
                horizon=horizon,
            ):
                return False
        point = _decimal(value.get("point_forecast_units"), "point forecast")
        velocity = _decimal(
            value.get("forecast_daily_velocity"), "forecast velocity"
        )
        if point < 0 or velocity != (point / Decimal(horizon)).quantize(
            VELOCITY, rounding=ROUND_HALF_UP
        ):
            return False
        if value["status"] == "READY":
            protection = _decimal(value.get("protection_units"), "protection")
            target = _decimal(value.get("target_units"), "target")
            availability = value.get("availability")
            metrics = value.get("evaluation_metrics")
            if not isinstance(availability, dict) or not isinstance(metrics, dict):
                return False
            availability_limited = (
                int(availability.get("unknown_days", -1)) > 0
                or int(availability.get("proven_in_stock_days", -1))
                < int(
                    policy.values["availability"][
                        "minimum_proven_in_stock_days_for_full_protection"
                    ]
                )
            )
            wape_raw = metrics.get("wape")
            wape = None if wape_raw is None else _decimal(wape_raw, "evaluation wape")
            expected_confidence = _classify_confidence(
                evaluation_status=str(value.get("evaluation_status")),
                availability_limited=availability_limited,
                evaluation_wape=wape,
                policy=policy,
            )
            if not (
                protection >= 0
                and target == point + protection
                and value.get("confidence") == expected_confidence
            ):
                return False
        elif not (
            value.get("protection_units") is None
            and value.get("target_units") is None
            and value.get("confidence") == "LOW"
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


def validate_development_forecast_evidence(value: Any) -> bool:
    """Deeply validate the immutable shape and self-hash of V1 evidence."""

    if isinstance(value, dict) and value.get("contract") == V2_CONTRACT:
        return _validate_v2_development_forecast_evidence(value)
    if not isinstance(value, dict):
        return False
    unsigned = {key: item for key, item in value.items() if key != "sha256"}
    policy = value.get("policy")
    protection = value.get("protection")
    candidates = value.get("candidates")
    protection_calendar = value.get("protection_calendar")
    try:
        active_policy = load_development_forecast_policy()
        sha256 = value.get("sha256")
        history_start = date.fromisoformat(str(value.get("history_start")))
        history_end = date.fromisoformat(str(value.get("history_end")))
        history_days = value.get("history_days")
        base_keys = {
            "contract",
            "method_version",
            "policy",
            "history_start",
            "history_end",
            "cutoff_date",
            "history_days",
            "horizon_days",
            "protection_calendar",
            "calendar_velocity",
            "in_stock_velocity",
            "input_sha256",
            "category_prior",
            "commercial_authority",
            "production_activation",
            "status",
            "selected_model",
            "demand_regime",
            "xyz_class",
            "xyz_coefficient_of_variation",
            "abc_class",
            "forecast_daily_velocity",
            "point_forecast_units",
            "protection_units",
            "target_units",
            "confidence",
            "reason_codes",
            "split",
            "candidates",
            "protection",
            "caps",
            "sha256",
        }
        selected_keys = {
            "abc_status",
            "selection_metrics",
            "evaluation_metrics",
            "evaluation_origin_count",
            "minimum_evaluation_origin_count",
            "evaluation_status",
            "availability",
        }
        expected_top_keys = base_keys | (
            selected_keys if value.get("selected_model") is not None else set()
        )
        if not (
            set(value) == expected_top_keys
            and value.get("contract") == CONTRACT
            and value.get("method_version") == METHOD_VERSION
            and value.get("commercial_authority") is False
            and value.get("production_activation") is False
            and isinstance(sha256, str)
            and len(sha256) == _SHA256_LENGTH
            and bytes.fromhex(sha256)
            and sha256 == canonical_evidence_sha256(unsigned)
            and policy == active_policy.evidence()
            and value.get("status") in {"READY", "BLOCKED"}
            and isinstance(history_days, int)
            and 0 < history_days
            <= int(active_policy.values["history"]["maximum_history_days"])
            and isinstance(value.get("horizon_days"), int)
            and 0 < value["horizon_days"] <= 31
            and history_start <= history_end
            and (history_end - history_start).days + 1 == history_days
            and history_end == date.fromisoformat(str(value.get("cutoff_date")))
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
        ):
            return False

        calendar_velocity = _decimal(
            value.get("calendar_velocity"), "calendar_velocity"
        )
        in_stock_velocity_raw = value.get("in_stock_velocity")
        if not (
            calendar_velocity >= 0
            and (
                in_stock_velocity_raw is None
                or _decimal(in_stock_velocity_raw, "in_stock_velocity") >= 0
            )
            and _decimal(
                value.get("forecast_daily_velocity"),
                "forecast_daily_velocity",
            )
            >= 0
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
                and variability == 0
                and int(
                    variability.to_integral_value(rounding=ROUND_CEILING)
                )
                == protection_calendar.get("variability_days_ceiling")
                and protection_calendar.get("variability_treatment")
                == "ZERO_CONFIRMED_EMPIRICAL_DEMAND_PROTECTION_ONLY"
            ):
                return False
        else:
            return False

        windows = active_policy.values["windows"]
        selection_evaluation_start = history_days - int(
            windows["evaluation_days"]
        )
        selection_calibration_start = selection_evaluation_start - int(
            windows["calibration_days"]
        )
        selection_start = max(
            int(windows["minimum_training_days"]),
            selection_calibration_start - int(windows["selection_origin_days"]),
        )
        last_selection_origin = (
            selection_calibration_start - int(value["horizon_days"])
        )
        selection_date_min = history_start + timedelta(days=selection_start)
        selection_date_max = history_start + timedelta(
            days=last_selection_origin
        )
        expected_candidates = list(active_policy.values["candidate_order"])
        if candidates and [item.get("name") for item in candidates] != expected_candidates:
            return False
        candidate_metrics: dict[str, dict[str, Any]] = {}
        for item in candidates:
            if not isinstance(item, dict):
                return False
            expected_candidate_keys = {
                "name",
                "status",
                "reason_codes",
                "selection_metrics",
                "selection_origin_dates",
                "postprocessing_cap_hits",
                "forecast_horizon_days",
            }
            if value.get("selected_model") is not None:
                expected_candidate_keys.add("selected")
            status = item.get("status")
            reasons = item.get("reason_codes")
            origins = item.get("selection_origin_dates")
            metrics = item.get("selection_metrics")
            parsed_origins = (
                [date.fromisoformat(str(origin)) for origin in origins]
                if isinstance(origins, list)
                else []
            )
            if not (
                set(item) == expected_candidate_keys
                and item.get("name") in expected_candidates
                and status in {"ELIGIBLE", "INAPPLICABLE", "FAILED"}
                and isinstance(reasons, list)
                and all(isinstance(reason, str) and reason for reason in reasons)
                and len(reasons) == len(set(reasons))
                and isinstance(origins, list)
                and len(origins) == len(set(origins))
                and parsed_origins == sorted(parsed_origins)
                and all(
                    selection_date_min <= origin <= selection_date_max
                    for origin in parsed_origins
                )
                and len(parsed_origins)
                <= max(0, last_selection_origin - selection_start + 1)
                and item.get("forecast_horizon_days") == value["horizon_days"]
                and isinstance(item.get("postprocessing_cap_hits"), int)
                and item["postprocessing_cap_hits"] >= 0
                and (
                    "selected" not in item or isinstance(item.get("selected"), bool)
                )
            ):
                return False
            if status == "ELIGIBLE":
                if not (
                    reasons == []
                    and isinstance(metrics, dict)
                    and set(metrics)
                    == {"observation_count", "bias", "mae", "wape", "mase"}
                    and metrics.get("observation_count") == len(origins)
                    and len(origins)
                    >= int(active_policy.values["windows"]["minimum_selection_origins"])
                    and _decimal(metrics.get("bias"), "selection bias").is_finite()
                    and _decimal(metrics.get("mae"), "selection mae") >= 0
                    and (
                        metrics.get("wape") is None
                        or _decimal(metrics.get("wape"), "selection wape") >= 0
                    )
                    and (
                        metrics.get("mase") is None
                        or _decimal(metrics.get("mase"), "selection mase") >= 0
                    )
                ):
                    return False
                candidate_metrics[str(item["name"])] = metrics
            elif metrics is not None or not reasons or item.get("selected") is True:
                return False

        eligible_origin_sets = {
            tuple(item["selection_origin_dates"])
            for item in candidates
            if item.get("status") == "ELIGIBLE"
        }
        if len(eligible_origin_sets) > 1:
            return False

        def metric_objective(metrics: Mapping[str, Any]) -> Decimal:
            raw = metrics.get("wape")
            return _decimal(
                metrics.get("mae") if raw is None else raw,
                "selection objective",
            )

        expected_selected_model: str | None = None
        expected_fva_reason: str | None = None
        baseline_name = str(active_policy.values["fva"]["simple_baseline"])
        baseline_metrics = candidate_metrics.get(baseline_name)
        if baseline_metrics is not None:
            best_name = min(
                candidate_metrics,
                key=lambda name: (
                    metric_objective(candidate_metrics[name]),
                    expected_candidates.index(name),
                ),
            )
            expected_selected_model = baseline_name
            expected_fva_reason = "SIMPLE_BASELINE_SELECTED"
            if best_name != baseline_name:
                baseline_score = metric_objective(baseline_metrics)
                best_score = metric_objective(candidate_metrics[best_name])
                improvement = (
                    Decimal("0")
                    if baseline_score == 0
                    else max(
                        (baseline_score - best_score) / baseline_score,
                        Decimal("0"),
                    )
                )
                if improvement >= _decimal(
                    active_policy.values["fva"]["minimum_relative_improvement"],
                    "fva.minimum_relative_improvement",
                ):
                    expected_selected_model = best_name
                    expected_fva_reason = "COMPLEX_MODEL_CLEARED_FVA_GATE"
                else:
                    expected_fva_reason = (
                        "COMPLEX_MODEL_DID_NOT_CLEAR_FVA_GATE"
                    )

        selected_model = value.get("selected_model")
        selected_rows = [
            item for item in candidates if item.get("selected") is True
        ]
        if selected_model is None:
            if selected_rows or value.get("selection_metrics") is not None:
                return False
        elif not (
            selected_model == expected_selected_model
            and len(selected_rows) == 1
            and selected_rows[0].get("name") == selected_model
            and selected_rows[0].get("status") == "ELIGIBLE"
            and value.get("selection_metrics")
            == selected_rows[0].get("selection_metrics")
            and all("selected" in item for item in candidates)
        ):
            return False

        if value.get("status") == "BLOCKED" and selected_model is None:
            windows = active_policy.values["windows"]
            expected_evaluation_start = history_days - int(
                windows["evaluation_days"]
            )
            expected_calibration_start = expected_evaluation_start - int(
                windows["calibration_days"]
            )
            expected_selection_start = max(
                int(windows["minimum_training_days"]),
                expected_calibration_start
                - int(windows["selection_origin_days"]),
            )
            expected_split = {
                "selection_start_index": expected_selection_start,
                "calibration_start_index": expected_calibration_start,
                "evaluation_start_index": expected_evaluation_start,
            }
            chronology_blocked = candidates == []
            baseline_blocked = (
                [item.get("name") for item in candidates]
                == expected_candidates
                and baseline_metrics is None
            )
            if not (
                _decimal(value.get("forecast_daily_velocity"), "forecast velocity") == 0
                and _decimal(value.get("point_forecast_units"), "point forecast") == 0
                and value.get("protection_units") is None
                and value.get("target_units") is None
                and value.get("confidence") == "LOW"
                and value.get("split") == expected_split
                and value.get("xyz_coefficient_of_variation") is None
                and value.get("abc_class") == "NOT_CONFIGURED"
                and value.get("caps") == {"daily_rate_cap_hits": 0}
                and set(protection)
                == {"status", "full_horizon_origin_count", "shortfalls"}
                and protection.get("status") == "UNAVAILABLE"
                and protection.get("full_horizon_origin_count") == 0
                and protection.get("shortfalls") == []
                and (
                    (
                        chronology_blocked
                        and value.get("demand_regime") == "THIN"
                        and value.get("xyz_class") == "NOT_CONFIGURED"
                        and value["reason_codes"]
                        == ["INSUFFICIENT_CHRONOLOGICAL_WINDOWS"]
                    )
                    or (
                        baseline_blocked
                        and value.get("demand_regime") == "UNCLASSIFIED"
                        and value.get("xyz_class") == "NOT_CONFIGURED"
                        and value["reason_codes"] == ["SIMPLE_BASELINE_UNAVAILABLE"]
                    )
                )
            ):
                return False
            return True

        if selected_model is None:
            return False

        evaluation_count = value.get("evaluation_origin_count")
        minimum_evaluation = value.get("minimum_evaluation_origin_count")
        evaluation_status = value.get("evaluation_status")
        point = _decimal(value.get("point_forecast_units"), "point forecast")
        availability = value.get("availability")
        if not isinstance(availability, dict):
            return False
        windows = active_policy.values["windows"]
        expected_evaluation_start = history_days - int(windows["evaluation_days"])
        expected_calibration_start = expected_evaluation_start - int(
            windows["calibration_days"]
        )
        expected_selection_start = max(
            int(windows["minimum_training_days"]),
            expected_calibration_start - int(windows["selection_origin_days"]),
        )

        def history_date(index: int) -> str:
            return (history_start + timedelta(days=index)).isoformat()

        expected_split = {
            "selection_start_date": history_date(expected_selection_start),
            "selection_end_date": history_date(expected_calibration_start - 1),
            "calibration_start_date": history_date(expected_calibration_start),
            "calibration_end_date": history_date(expected_evaluation_start - 1),
            "evaluation_start_date": history_date(expected_evaluation_start),
            "evaluation_end_date": history_end.isoformat(),
        }
        caps = value.get("caps")
        evaluation_metrics = value.get("evaluation_metrics")
        if not (
            value.get("demand_regime") in {"REGULAR", "INTERMITTENT"}
            and value.get("xyz_class") in {"X", "Y", "Z"}
            and (
                value.get("xyz_coefficient_of_variation") is None
                or _decimal(
                    value.get("xyz_coefficient_of_variation"),
                    "xyz_coefficient_of_variation",
                )
                >= 0
            )
            and value.get("abc_class") == "NOT_CONFIGURED"
            and value.get("abc_status") == "MISSING_HISTORICAL_COGS"
            and value.get("split") == expected_split
            and isinstance(caps, dict)
            and set(caps)
            == {
                "daily_rate_cap",
                "daily_rate_cap_hits",
                "selection_cap_hits",
                "evaluation_cap_hits",
                "calibration_cap_hits",
                "final_cap_hits",
            }
            and _decimal(caps.get("daily_rate_cap"), "daily_rate_cap") >= 0
            and all(
                isinstance(caps.get(key), int) and caps[key] >= 0
                for key in (
                    "daily_rate_cap_hits",
                    "selection_cap_hits",
                    "evaluation_cap_hits",
                    "calibration_cap_hits",
                    "final_cap_hits",
                )
            )
            and caps["daily_rate_cap_hits"]
            == caps["selection_cap_hits"]
            + caps["evaluation_cap_hits"]
            + caps["calibration_cap_hits"]
            + caps["final_cap_hits"]
            and isinstance(evaluation_metrics, dict)
            and set(evaluation_metrics)
            == {"observation_count", "bias", "mae", "wape", "mase"}
            and _decimal(evaluation_metrics.get("bias"), "evaluation bias").is_finite()
            and _decimal(evaluation_metrics.get("mae"), "evaluation mae") >= 0
            and (
                evaluation_metrics.get("wape") is None
                or _decimal(evaluation_metrics.get("wape"), "evaluation wape") >= 0
            )
            and (
                evaluation_metrics.get("mase") is None
                or _decimal(evaluation_metrics.get("mase"), "evaluation mase") >= 0
            )
            and set(availability)
            == {
                "proven_stockout_days",
                "proven_in_stock_days",
                "unknown_days",
                "minimum_proven_in_stock_days_for_full_protection",
                "protection_qualification",
                "negative_net_days",
            }
        ):
            return False
        proven_in_stock_days = availability.get("proven_in_stock_days")
        proven_stockout_days = availability.get("proven_stockout_days")
        unknown_days = availability.get("unknown_days")
        minimum_in_stock_days = int(
            active_policy.values["availability"][
                "minimum_proven_in_stock_days_for_full_protection"
            ]
        )
        if not (
            all(
                isinstance(item, int) and item >= 0
                for item in (proven_in_stock_days, proven_stockout_days, unknown_days)
            )
            and proven_in_stock_days + proven_stockout_days + unknown_days
            == history_days
            and isinstance(availability.get("negative_net_days"), int)
            and 0 <= availability["negative_net_days"] <= history_days
            and availability.get(
                "minimum_proven_in_stock_days_for_full_protection"
            )
            == minimum_in_stock_days
        ):
            return False
        availability_limited = (
            unknown_days > 0 or proven_in_stock_days < minimum_in_stock_days
        )
        if availability.get("protection_qualification") != (
            "LIMITED" if availability_limited else "FULL"
        ):
            return False
        shortfalls = [
            _decimal(item, "protection shortfall")
            for item in protection.get("shortfalls", [])
        ]
        quantile = _decimal(protection.get("quantile"), "protection.quantile")
        protection_keys = {
            "status",
            "method",
            "quantile",
            "minimum_origin_count",
            "full_horizon_origin_count",
            "censored_stockout_origin_count",
            "censored_stockout_origin_dates",
            "origin_dates",
            "shortfalls",
            "availability_qualification",
        }
        if value.get("status") == "READY":
            protection_keys.add("units")
        origin_dates = protection.get("origin_dates")
        censored_dates = protection.get("censored_stockout_origin_dates")
        last_calibration_origin = (
            expected_evaluation_start - int(value["horizon_days"])
        )
        expected_calibration_dates = [
            history_date(index)
            for index in range(
                expected_calibration_start,
                last_calibration_origin + 1,
            )
        ]
        maximum_evaluation_origins = max(
            0,
            history_days
            - int(value["horizon_days"])
            - expected_evaluation_start
            + 1,
        )
        if not (
            set(protection) == protection_keys
            and quantile
            == _decimal(
                active_policy.values["protection"]["quantile"],
                "policy protection.quantile",
            )
            and protection.get("minimum_origin_count")
            == int(
                active_policy.values["protection"][
                    "minimum_full_horizon_origins"
                ]
            )
            and isinstance(origin_dates, list)
            and len(origin_dates) == len(set(origin_dates)) == len(shortfalls)
            and origin_dates == sorted(origin_dates)
            and all(
                history_start <= date.fromisoformat(str(item)) <= history_end
                for item in origin_dates
            )
            and isinstance(censored_dates, list)
            and len(censored_dates) == len(set(censored_dates))
            and censored_dates == sorted(censored_dates)
            and all(
                history_start <= date.fromisoformat(str(item)) <= history_end
                for item in censored_dates
            )
            and not set(origin_dates).intersection(censored_dates)
            and sorted(origin_dates + censored_dates)
            == expected_calibration_dates
            and protection.get("censored_stockout_origin_count")
            == len(censored_dates)
        ):
            return False
        if not (
            isinstance(evaluation_count, int)
            and evaluation_count > 0
            and evaluation_count <= maximum_evaluation_origins
            and isinstance(minimum_evaluation, int)
            and minimum_evaluation
            == int(active_policy.values["windows"]["minimum_evaluation_origins"])
            and evaluation_status
            == (
                "SUFFICIENT"
                if evaluation_count >= minimum_evaluation
                else "INSUFFICIENT_FOR_CONFIDENCE"
            )
            and evaluation_metrics.get("observation_count")
            == evaluation_count
            and point >= 0
            and protection.get("method")
            == "FULL_HORIZON_SHORTFALL_EMPIRICAL_QUANTILE"
            and isinstance(protection.get("minimum_origin_count"), int)
            and isinstance(protection.get("full_horizon_origin_count"), int)
            and protection["full_horizon_origin_count"] == len(shortfalls)
            and all(item >= 0 for item in shortfalls)
            and protection.get("availability_qualification")
            == ("LIMITED" if availability_limited else "FULL")
        ):
            return False

        xyz_reason_codes: list[str] = []
        xyz_coefficient_raw = value.get("xyz_coefficient_of_variation")
        if xyz_coefficient_raw is None:
            if not (
                value.get("xyz_class")
                == active_policy.values["xyz"]["zero_demand_class"]
                and calendar_velocity == 0
            ):
                return False
            xyz_reason_codes.append("ZERO_DEMAND_XYZ_POLICY")
        else:
            xyz_coefficient = _decimal(
                xyz_coefficient_raw, "xyz_coefficient_of_variation"
            )
            x_max = _decimal(
                active_policy.values["xyz"]["x_max_coefficient_of_variation"],
                "xyz.x_max",
            )
            y_max = _decimal(
                active_policy.values["xyz"]["y_max_coefficient_of_variation"],
                "xyz.y_max",
            )
            expected_xyz = "X" if xyz_coefficient <= x_max else "Y" if xyz_coefficient <= y_max else "Z"
            if value.get("xyz_class") != expected_xyz:
                return False

        expected_velocity = (point / Decimal(value["horizon_days"])).quantize(
            VELOCITY, rounding=ROUND_HALF_UP
        )
        if _decimal(
            value.get("forecast_daily_velocity"), "forecast_daily_velocity"
        ) != expected_velocity:
            return False

        if value.get("status") == "BLOCKED":
            expected_blocked_reasons = [
                expected_fva_reason,
                *xyz_reason_codes,
                "EMPIRICAL_PROTECTION_ORIGINS_INSUFFICIENT",
            ]
            if not (
                value.get("protection_units") is None
                and value.get("target_units") is None
                and value.get("confidence") == "LOW"
                and protection.get("status") == "UNAVAILABLE"
                and value["reason_codes"] == expected_blocked_reasons
                and len(shortfalls)
                == protection["full_horizon_origin_count"]
                < protection["minimum_origin_count"]
            ):
                return False
            return True

        protection_units = _decimal(
            value.get("protection_units"), "protection units"
        )
        target = _decimal(value.get("target_units"), "target units")
        evaluation_wape_raw = value["evaluation_metrics"].get("wape")
        evaluation_wape = (
            None
            if evaluation_wape_raw is None
            else _decimal(evaluation_wape_raw, "evaluation wape")
        )
        expected_confidence = (
            "HIGH"
            if evaluation_status == "SUFFICIENT"
            and not availability_limited
            and evaluation_wape is not None
            and evaluation_wape <= Decimal("0.20")
            else "MEDIUM"
            if evaluation_status == "SUFFICIENT"
            and not availability_limited
            and evaluation_wape is not None
            and evaluation_wape <= Decimal("0.50")
            else "LOW"
        )
        expected_protection_status = (
            "CALCULATED_LIMITED_AVAILABILITY"
            if availability_limited
            else "CALCULATED"
        )
        expected_reason_codes = [expected_fva_reason, *xyz_reason_codes]
        if proven_stockout_days:
            expected_reason_codes.append("PROVEN_STOCKOUT_DAYS_CAUSALLY_IMPUTED")
        if unknown_days:
            expected_reason_codes.append("UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK")
        if availability["negative_net_days"]:
            expected_reason_codes.append(
                "NEGATIVE_NET_DAYS_FLOORED_FOR_DEMAND_ONLY"
            )
        if caps["daily_rate_cap_hits"]:
            expected_reason_codes.append("DEVELOPMENT_RATE_CAP_APPLIED")
        if evaluation_status != "SUFFICIENT":
            expected_reason_codes.append(
                "EVALUATION_ORIGINS_INSUFFICIENT_FOR_CONFIDENCE"
            )
        if availability_limited:
            expected_reason_codes.append(
                "AVAILABILITY_COVERAGE_LIMITS_PROTECTION"
            )
        expected_reason_codes.append("EMPIRICAL_FULL_HORIZON_PROTECTION_BOUND")
        if not (
            value.get("confidence") == expected_confidence
            and protection.get("status") == expected_protection_status
            and protection_units >= 0
            and target == point + protection_units
            and protection.get("units") == value.get("protection_units")
            and len(shortfalls)
            == protection["full_horizon_origin_count"]
            >= protection["minimum_origin_count"]
            > 0
            and protection_units
            == _quantile(shortfalls, quantile).quantize(
                UNITS, rounding=ROUND_HALF_UP
            )
            and (
                "AVAILABILITY_COVERAGE_LIMITS_PROTECTION" in value["reason_codes"]
            )
            == availability_limited
            and value["reason_codes"] == expected_reason_codes
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


def validate_development_forecast_context_evidence(
    value: Any,
    frozen_observations: Any,
) -> bool:
    """Replan one frozen context and require byte-equivalent forecast evidence.

    The detached evidence object contains only an input digest, so structural
    validation alone cannot prove model-derived values.  Monday manifests and
    packets already freeze the complete observation projection; this boundary
    reconstructs it, verifies the digest, and deterministically reruns the
    exact recorded registry entry.
    """

    if not validate_development_forecast_evidence(value):
        return False
    if not isinstance(value, dict) or not isinstance(frozen_observations, list):
        return False
    try:
        parsed: list[DemandObservation] = []
        input_rows: list[dict[str, Any]] = []
        for raw in frozen_observations:
            if not isinstance(raw, dict) or set(raw) != {
                "business_date",
                "net_units",
                "inventory_state",
            }:
                return False
            business_date = date.fromisoformat(str(raw["business_date"]))
            net_units = _decimal(raw["net_units"], "net_units")
            inventory_state = str(raw["inventory_state"])
            parsed.append(
                DemandObservation(business_date, net_units, inventory_state)
            )
            input_rows.append(
                {
                    "business_date": business_date,
                    "net_units": net_units,
                    "inventory_state": inventory_state,
                }
            )
        if canonical_evidence_sha256(input_rows) != value.get("input_sha256"):
            return False
        evidence_contract = str(value["contract"])
        active_policy = load_development_forecast_policy(
            evidence_contract=evidence_contract
        )
        replanned = plan_development_forecast(
            parsed,
            horizon_days=int(value["horizon_days"]),
            protection_calendar=value["protection_calendar"],
            policy=active_policy,
        ).to_json_dict()
        return canonical_evidence_json(replanned) == canonical_evidence_json(value)
    except (DevelopmentForecastError, KeyError, TypeError, ValueError):
        return False


def validate_connected_development_forecast_context_evidence(
    value: Any,
    frozen_observations: Any,
    *,
    expected_contract: str,
) -> bool:
    """Require the exact registered connected calendar for a stored context."""

    calendar = value.get("protection_calendar") if isinstance(value, dict) else None
    if (
        expected_contract not in {CONTRACT, V2_CONTRACT}
        or not isinstance(value, dict)
        or value.get("contract") != expected_contract
        or (
            expected_contract == V2_CONTRACT
            and (
                not isinstance(calendar, dict)
                or calendar.get("basis")
                != "ANCHORED_REVIEW_SUBMISSION_DELIVERY_SCHEDULE_V2"
            )
        )
    ):
        return False
    return validate_development_forecast_context_evidence(
        value,
        frozen_observations,
    )


def serialize_baseline_need(value: BaselineNeed | Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact immutable JSON contract for one calculated need."""

    if isinstance(value, BaselineNeed):
        payload: dict[str, Any] = {
            "status": value.status,
            "policy_mode": value.policy_mode,
            "protection_days": value.protection_days,
            "target_units": str(value.target_units),
            "effective_inventory_units": str(value.effective_inventory_units),
            "raw_need_units": value.raw_need_units,
            "cases": value.cases,
            "loose_units": value.loose_units,
            "ordered_units": value.ordered_units,
            "pack_rounding_units": value.pack_rounding_units,
            "loose_fee": str(value.loose_fee),
            "reason_codes": list(value.reason_codes),
        }
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise DevelopmentForecastError("baseline need has the wrong type")
    _require_exact_keys(payload, _BASELINE_NEED_KEYS, "baseline need")
    if (
        not isinstance(payload["status"], str)
        or not payload["status"]
        or (
            payload["policy_mode"] is not None
            and not isinstance(payload["policy_mode"], str)
        )
        or (
            payload["protection_days"] is not None
            and (
                not isinstance(payload["protection_days"], int)
                or isinstance(payload["protection_days"], bool)
                or payload["protection_days"] < 1
            )
        )
        or any(
            not isinstance(payload[key], int)
            or isinstance(payload[key], bool)
            or payload[key] < 0
            for key in (
                "raw_need_units",
                "cases",
                "loose_units",
                "ordered_units",
                "pack_rounding_units",
            )
        )
        or not isinstance(payload["reason_codes"], (list, tuple))
        or not all(
            isinstance(item, str) and item for item in payload["reason_codes"]
        )
        or len(payload["reason_codes"]) != len(set(payload["reason_codes"]))
    ):
        raise DevelopmentForecastError("baseline need shape differs")
    for key in ("target_units", "effective_inventory_units", "loose_fee"):
        if _decimal(payload[key], f"baseline need {key}") < 0:
            raise DevelopmentForecastError("baseline need decimal differs")
        if not isinstance(payload[key], str):
            raise DevelopmentForecastError("baseline need decimals must be strings")
    return {
        **payload,
        "reason_codes": list(payload["reason_codes"]),
    }


def _development_need_calculation_inputs(context: Mapping[str, Any]) -> dict[str, Any]:
    evidence = context.get("development_forecast_evidence")
    if not isinstance(evidence, dict):
        raise DevelopmentForecastError("development evidence is absent")
    projection = {
        "variant_id": context.get("variant_id"),
        "development_forecast_contract": context.get(
            "development_forecast_contract"
        ),
        "development_forecast_evidence_sha256": context.get(
            "development_forecast_evidence_sha256"
        ),
        "policy_identity": evidence.get("policy"),
        "schedule_identity": evidence.get("protection_calendar"),
        "available_units": context.get("available_units"),
        "trusted_incoming_units": context.get("trusted_incoming_units"),
        "inventory_capture": context.get("inventory_capture"),
        "inventory_rows": context.get("inventory_rows"),
        "open_po_position": context.get("open_po_position"),
        "policy_mode": context.get("policy_mode"),
        "policies": context.get("policies"),
        "units_per_case": context.get("units_per_case"),
        "qualifying_units_per_case": context.get("qualifying_units_per_case"),
        "offer_id": context.get("offer_id"),
        "offer_evidence": context.get("offer_evidence"),
        "vendor_id": context.get("vendor_id"),
        "vendor_rules": context.get("vendor_rules"),
    }
    if (
        not isinstance(projection["variant_id"], str)
        or not projection["variant_id"]
        or projection["available_units"] is None
        or projection["trusted_incoming_units"] is None
        or not isinstance(projection["vendor_rules"], (list, tuple))
        or len(projection["vendor_rules"]) < 22
    ):
        raise DevelopmentForecastError("development need inputs are incomplete")
    # Bind the exact primitive projection that the historical Monday manifest
    # serializer persists.  That serializer intentionally uses ``str`` for
    # datetime/time/Decimal values; normalizing here avoids a false mismatch
    # between an in-memory context and its byte-identical parsed manifest while
    # leaving the long-standing V1 serializer untouched.
    return json.loads(
        json.dumps(
            projection,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def build_development_baseline_need_binding(
    context: Mapping[str, Any],
    need: BaselineNeed | Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a V2 need to its exact enclosing frozen calculation context."""

    if context.get("development_forecast_contract") != V2_CONTRACT:
        raise DevelopmentForecastError("baseline need binding requires V2")
    need_payload = serialize_baseline_need(need)
    inputs = _development_need_calculation_inputs(context)
    unsigned = {
        "contract": BASELINE_NEED_BINDING_CONTRACT,
        "variant_id": str(context["variant_id"]),
        "forecast_evidence_sha256": str(
            context["development_forecast_evidence_sha256"]
        ),
        "need_sha256": canonical_evidence_sha256(need_payload),
        "calculation_inputs_sha256": canonical_evidence_sha256(inputs),
    }
    return {**unsigned, "sha256": canonical_evidence_sha256(unsigned)}


def _validate_v2_development_need_sources(
    context: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    vendor: Sequence[Any],
    position: Mapping[str, Any],
) -> bool:
    """Bind duplicated V2 calculator scalars to their frozen source evidence."""

    def strict_decimal(value: Any, field: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
            raise DevelopmentForecastError(f"{field} has the wrong type")
        return _decimal(value, field)

    def strict_positive_int(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise DevelopmentForecastError(f"{field} has the wrong type")
        return value

    units_per_case = strict_positive_int(
        context.get("units_per_case"), "units_per_case"
    )
    qualifying_units_per_case = strict_positive_int(
        context.get("qualifying_units_per_case"),
        "qualifying_units_per_case",
    )
    offer = context.get("offer_evidence")
    if not isinstance(offer, dict):
        return False
    selected_input = context.get("selected_offer_input_evidence")
    selected_offer = (
        selected_input.get("selected_offer")
        if isinstance(selected_input, dict)
        else None
    )
    if (
        not isinstance(selected_offer, dict)
        or selected_offer.get("offer_id") != context.get("offer_id")
        or selected_offer.get("variant_id") != context.get("variant_id")
        or str(selected_offer.get("vendor_id")) != str(context.get("vendor_id"))
        or any(
            selected_offer.get(field) != offer.get(field)
            for field in (
                "supplier_sku",
                "package_type",
                "size_text",
                "raw_pack",
                "shopify_units_per_case",
                "qualifying_units_per_case",
                "assortment_scope",
                "assortment_group",
                "assortable",
                "confidence",
                "valid_from",
                "valid_to",
            )
        )
    ):
        return False
    for field, expected in (
        ("shopify_units_per_case", units_per_case),
        ("qualifying_units_per_case", qualifying_units_per_case),
    ):
        raw = offer.get(field)
        if isinstance(raw, bool) or not isinstance(raw, (int, str, Decimal)):
            return False
        numeric = _decimal(raw, f"offer {field}")
        if numeric != numeric.to_integral_value() or int(numeric) != expected:
            return False

    available = strict_decimal(context.get("available_units"), "available_units")
    trusted_incoming = strict_decimal(
        context.get("trusted_incoming_units"), "trusted_incoming_units"
    )
    inventory_rows = context.get("inventory_rows")
    if not isinstance(inventory_rows, (list, tuple)) or not inventory_rows:
        return False
    inventory_available = Decimal("0")
    inventory_incoming = Decimal("0")
    for row in inventory_rows:
        if (
            not isinstance(row, (list, tuple))
            or len(row) < 5
            or row[3] != "VALID"
            or not isinstance(row[0], str)
            or not row[0]
        ):
            return False
        inventory_available += strict_decimal(row[1], "inventory available")
        inventory_incoming += strict_decimal(row[2], "inventory incoming")
    if inventory_available != available:
        return False

    if (
        position.get("variant_id") != context.get("variant_id")
        or position.get("vendor_id") != context.get("vendor_id")
        or not isinstance(position.get("blocks_reorder"), bool)
        or isinstance(position.get("open_line_count"), bool)
        or not isinstance(position.get("open_line_count"), int)
        or position.get("open_line_count") < 0
        or strict_decimal(
            position.get("trusted_incoming_units"),
            "open position trusted incoming",
        )
        != trusted_incoming
        or not isinstance(position.get("blockers"), list)
        or bool(position["blockers"]) != position["blocks_reorder"]
        or not isinstance(position.get("trusted_sources"), list)
        or position["open_line_count"]
        != len(position["trusted_sources"]) + len(position["blockers"])
    ):
        return False
    trusted_source_units = Decimal("0")
    line_ids: set[int] = set()
    for source in position["trusted_sources"]:
        if (
            not isinstance(source, dict)
            or isinstance(source.get("po_line_id"), bool)
            or not isinstance(source.get("po_line_id"), int)
            or source["po_line_id"] < 1
            or not isinstance(source.get("source_vendor_id"), str)
            or not source["source_vendor_id"]
        ):
            return False
        open_units = strict_decimal(
            source.get("open_units"), "trusted source open units"
        )
        if open_units <= 0 or source["po_line_id"] in line_ids:
            return False
        line_ids.add(source["po_line_id"])
        trusted_source_units += open_units
    for item in position["blockers"]:
        if (
            not isinstance(item, dict)
            or isinstance(item.get("po_line_id"), bool)
            or not isinstance(item.get("po_line_id"), int)
            or item["po_line_id"] <= 0
            or item["po_line_id"] in line_ids
            or not isinstance(item.get("reason"), str)
            or not item["reason"]
        ):
            return False
        line_ids.add(item["po_line_id"])
    if trusted_source_units != trusted_incoming:
        return False
    blockers = context.get("blockers")
    if not isinstance(blockers, list) or not all(
        isinstance(item, str) and item for item in blockers
    ):
        return False
    if (
        ("OPEN_PO_RECONCILIATION_BLOCKED" in blockers)
        != position["blocks_reorder"]
        or ("INCOMING_EVIDENCE_MISMATCH" in blockers)
        != (inventory_incoming != trusted_incoming)
    ):
        return False

    policies = context.get("policies")
    policy_mode = context.get("policy_mode")
    if (
        not isinstance(policy_mode, str)
        or not policy_mode
        or not isinstance(policies, (list, tuple))
        or len(policies) != 1
        or not isinstance(policies[0], (list, tuple))
        or len(policies[0]) < 3
        or isinstance(policies[0][0], bool)
        or not isinstance(policies[0][0], int)
        or not isinstance(policies[0][1], dict)
        or str(policies[0][1].get("mode") or "").strip().upper()
        != policy_mode
        or not isinstance(policies[0][2], str)
        or not policies[0][2].strip()
    ):
        return False

    if (
        len(vendor) < 22
        or isinstance(vendor[2], bool)
        or not isinstance(vendor[2], int)
        or vendor[2] < 1
        or isinstance(vendor[3], bool)
        or not isinstance(vendor[3], int)
        or vendor[3] < 0
        or not isinstance(vendor[8], bool)
        or (
            vendor[9] is not None
            and (
                isinstance(vendor[9], bool)
                or not isinstance(vendor[9], (str, Decimal))
            )
        )
    ):
        return False
    calendar = evidence.get("protection_calendar")
    if not isinstance(calendar, dict):
        return False
    selected_terms = selected_input.get("applicable_vendor_terms")
    if (
        not isinstance(selected_terms, dict)
        or str(selected_terms.get("vendor_id")) != str(context.get("vendor_id"))
        or canonical_evidence_json(selected_terms.get("vendor_rules"))
        != canonical_evidence_json(list(vendor))
    ):
        return False
    expected_vendor_projection = {
        "timezone_name": vendor[15],
        "order_days": vendor[13],
        "order_cutoff_local": (
            vendor[14].isoformat() if isinstance(vendor[14], time) else vendor[14]
        ),
        "expected_delivery_days": vendor[16],
        "order_cycle_days": vendor[2],
        "lead_time_days": vendor[3],
        "lead_time_variability_days": (
            "0"
            if strict_decimal(
                vendor[4], "vendor lead_time_variability_days"
            )
            == 0
            else str(
                strict_decimal(
                    vendor[4], "vendor lead_time_variability_days"
                ).normalize()
            )
        ),
    }
    return canonical_evidence_json(expected_vendor_projection) == (
        canonical_evidence_json(calendar.get("vendor_rules_projection"))
    )


def validate_development_baseline_need_context(
    context: Mapping[str, Any],
    *,
    manifest_contract: str,
) -> bool:
    """Recalculate and bind a stored baseline need under its recorded version."""

    try:
        if manifest_contract not in {CONTRACT, V2_CONTRACT}:
            return False
        evidence = context.get("development_forecast_evidence")
        if (
            not isinstance(evidence, dict)
            or context.get("development_forecast_contract") != manifest_contract
            or evidence.get("contract") != manifest_contract
            or evidence.get("method_version")
            != development_forecast_definition(manifest_contract).method_version
            or evidence.get("sha256")
            != context.get("development_forecast_evidence_sha256")
        ):
            return False
        if not validate_connected_development_forecast_context_evidence(
            evidence,
            context.get("demand_observations"),
            expected_contract=manifest_contract,
        ):
            return False
        if evidence.get("status") != "READY":
            return context.get("need") is None
        raw_need = context.get("need")
        if raw_need is None:
            allowed_v1_missing = {
                "OPEN_PO_RECONCILIATION_BLOCKED",
                "INCOMING_EVIDENCE_MISMATCH",
                "LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED",
                "VERIFIED_CURRENT_PRICE_LADDER_INVALID",
                "ONE_BOTTLE_REQUIRES_CONFIRMED_LOOSE_ORDER",
                "MISSING_OR_INVALID_REPLENISHMENT_POLICY",
            }
            return (
                manifest_contract == CONTRACT
                and isinstance(context.get("blockers"), list)
                and bool(set(context["blockers"]).intersection(allowed_v1_missing))
            )
        stored_need = serialize_baseline_need(raw_need)
        vendor = context.get("vendor_rules")
        position = context.get("open_po_position")
        if not isinstance(vendor, (list, tuple)) or len(vendor) < 22:
            return False
        if not isinstance(position, dict):
            return False
        if manifest_contract == V2_CONTRACT and not _validate_v2_development_need_sources(
            context,
            evidence=evidence,
            vendor=vendor,
            position=position,
        ):
            return False
        plan = DevelopmentForecastPlan(evidence)
        expected = calculate_development_baseline_need(
            forecast_daily_velocity=plan.forecast_daily_velocity,
            point_forecast_units=plan.point_forecast_units,
            empirical_protection_units=plan.protection_units,
            forecast_horizon_days=plan.horizon_days,
            available_units=context.get("available_units"),
            trusted_incoming_units=context.get("trusted_incoming_units"),
            order_cycle_days=int(vendor[2]),
            lead_time_days=int(vendor[3]),
            lead_time_variability_days=vendor[4],
            policy_mode=context.get("policy_mode"),
            units_per_case=int(context.get("units_per_case")),
            loose_order_allowed=bool(vendor[8]),
            loose_unit_fee=vendor[9],
            open_po_blocked=bool(position.get("blocks_reorder")),
            protection_days_override=plan.horizon_days,
        )
        expected_need = serialize_baseline_need(expected)
        if canonical_evidence_json(stored_need) != canonical_evidence_json(
            expected_need
        ):
            return False
        binding_present = "development_baseline_need_binding" in context
        if manifest_contract == CONTRACT:
            return not binding_present
        binding = context.get("development_baseline_need_binding")
        if not isinstance(binding, dict):
            return False
        expected_binding = build_development_baseline_need_binding(
            context,
            stored_need,
        )
        return canonical_evidence_json(binding) == canonical_evidence_json(
            expected_binding
        )
    except (
        DevelopmentForecastError,
        InvalidOperation,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False


def assign_gp_dollar_abc(
    cohort: Mapping[str, Any],
    *,
    policy: DevelopmentForecastPolicy | None = None,
) -> dict[str, Any]:
    """Classify an explicit complete cohort from historical revenue and COGS."""

    active_policy = policy or load_development_forecast_policy()
    if not isinstance(cohort, dict):
        raise DevelopmentForecastError("ABC requires an explicit cohort envelope")
    _require_exact_keys(
        cohort,
        {"contract", "scope", "eligible_variant_ids", "exclusions", "rows"},
        "ABC cohort",
    )
    scope = cohort["scope"]
    eligible = cohort["eligible_variant_ids"]
    exclusions = cohort["exclusions"]
    rows = cohort["rows"]
    if not isinstance(scope, dict):
        raise DevelopmentForecastError("ABC cohort scope must be an object")
    _require_exact_keys(
        scope,
        {"scope_id", "lookback_start", "lookback_end", "classification_period_days"},
        "ABC cohort scope",
    )
    period_days = int(active_policy.values["abc"]["classification_period_days"])
    lookback_start = _schedule_date(scope["lookback_start"], "ABC lookback_start")
    lookback_end = _schedule_date(scope["lookback_end"], "ABC lookback_end")
    if (
        cohort.get("contract") != "BUFFALO_DEVELOPMENT_ABC_COHORT_V2"
        or not str(scope.get("scope_id") or "").strip()
        or scope.get("classification_period_days") != period_days
        or (lookback_end - lookback_start).days + 1 != period_days
        or not isinstance(eligible, list)
        or not eligible
        or any(not isinstance(item, str) or not item.strip() for item in eligible)
        or eligible != sorted(set(eligible))
        or not isinstance(exclusions, list)
        or not isinstance(rows, list)
    ):
        raise DevelopmentForecastError("ABC cohort identity differs")
    exclusion_ids: list[str] = []
    normalized_exclusions: list[dict[str, str]] = []
    for exclusion in exclusions:
        if not isinstance(exclusion, dict):
            raise DevelopmentForecastError("ABC exclusions must be objects")
        _require_exact_keys(
            exclusion,
            {"variant_id", "reason_code"},
            "ABC exclusion",
        )
        variant_id = str(exclusion["variant_id"]).strip()
        reason = str(exclusion["reason_code"]).strip()
        if not variant_id or not reason:
            raise DevelopmentForecastError("ABC exclusion identity differs")
        exclusion_ids.append(variant_id)
        normalized_exclusions.append(
            {"variant_id": variant_id, "reason_code": reason}
        )
    if (
        exclusion_ids != sorted(set(exclusion_ids))
        or set(exclusion_ids).intersection(eligible)
    ):
        raise DevelopmentForecastError("ABC cohort membership conflicts")
    observed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise DevelopmentForecastError("ABC rows must be objects")
        _require_exact_keys(
            row,
            {"variant_id", "historical_revenue", "historical_cogs"},
            "ABC row",
        )
        variant_id = str(row["variant_id"]).strip()
        if not variant_id or variant_id not in eligible or variant_id in observed:
            raise DevelopmentForecastError("ABC rows differ from cohort membership")
        observed[variant_id] = row

    missing_ids = sorted(set(eligible) - set(observed))
    invalid_ids: list[str] = []
    parsed: dict[str, Decimal] = {}
    for variant_id in eligible:
        row = observed.get(variant_id)
        if row is None:
            continue
        try:
            revenue = _decimal(row.get("historical_revenue"), "historical_revenue")
            cogs = _decimal(row.get("historical_cogs"), "historical_cogs")
            if revenue < 0 or cogs < 0:
                raise DevelopmentForecastError("historical cost evidence is negative")
        except DevelopmentForecastError:
            invalid_ids.append(variant_id)
            continue
        parsed[variant_id] = revenue - cogs

    coverage = {
        "expected_count": len(eligible),
        "observed_count": len(observed),
        "calculable_count": len(parsed),
        "missing_variant_ids": missing_ids,
        "invalid_variant_ids": sorted(invalid_ids),
        "excluded_count": len(normalized_exclusions),
    }
    common = {
        "contract": "BUFFALO_DEVELOPMENT_ABC_COHORT_RESULT_V2",
        "scope": json.loads(canonical_evidence_json(scope)),
        "basis": "HISTORICAL_GROSS_PROFIT_DOLLARS",
        "coverage": coverage,
        "exclusions": normalized_exclusions,
        "commercial_authority": False,
        "production_activation": False,
    }
    if missing_ids or invalid_ids:
        members = []
        for variant_id in eligible:
            gp = parsed.get(variant_id)
            members.append(
                {
                    "variant_id": variant_id,
                    "abc_class": "NOT_CONFIGURED",
                    "status": (
                        "MISSING_COHORT_MEMBER"
                        if variant_id in missing_ids
                        else "INVALID_HISTORICAL_COST_EVIDENCE"
                        if variant_id in invalid_ids
                        else "COHORT_INCOMPLETE_KNOWN_GP_DIAGNOSTIC"
                    ),
                    "gross_profit_dollars": (
                        None
                        if gp is None
                        else str(gp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                    ),
                }
            )
        return {
            **common,
            "cohort_status": "INCOMPLETE",
            "classification_status": "NOT_CONFIGURED",
            "members": members,
        }

    positive_total = sum(
        (max(gp, Decimal("0")) for gp in parsed.values()), Decimal("0")
    )
    if positive_total == 0:
        return {
            **common,
            "cohort_status": "COMPLETE",
            "classification_status": "NOT_CONFIGURED",
            "members": [
                {
                    "variant_id": variant_id,
                    "abc_class": "NOT_CONFIGURED",
                    "status": "NONPOSITIVE_HISTORICAL_GROSS_PROFIT",
                    "gross_profit_dollars": str(
                        parsed[variant_id].quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                    ),
                }
                for variant_id in eligible
            ],
        }
    a_share = _decimal(
        active_policy.values["abc"]["a_cumulative_share"], "abc.a_share"
    )
    b_share = _decimal(
        active_policy.values["abc"]["b_incremental_share"], "abc.b_share"
    )
    cumulative = Decimal("0")
    classified: dict[str, dict[str, Any]] = {}
    for variant_id, gp in sorted(parsed.items(), key=lambda item: (-item[1], item[0])):
        share_before = cumulative / positive_total
        abc_class = (
            "A"
            if share_before < a_share
            else "B"
            if share_before < a_share + b_share
            else "C"
        )
        cumulative += max(gp, Decimal("0"))
        classified[variant_id] = {
            "variant_id": variant_id,
            "abc_class": abc_class,
            "status": "CALCULATED",
            "gross_profit_dollars": str(
                gp.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            ),
            "cumulative_share_after": str(
                (cumulative / positive_total).quantize(METRIC, rounding=ROUND_HALF_UP)
            ),
        }
    return {
        **common,
        "cohort_status": "COMPLETE",
        "classification_status": "CALCULATED",
        "members": [classified[variant_id] for variant_id in eligible],
    }
