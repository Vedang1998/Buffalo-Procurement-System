"""Transparent deterministic emergency forecast and demand evidence.

This bounded V2 helper deliberately exposes low-confidence evidence instead of
pretending that sparse or unknown inventory history proves availability.  It is
not the canonical model-selection/FVA program; downstream callers must retain
the returned method and confidence diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
from statistics import median
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from uuid import UUID


VELOCITY = Decimal("0.000001")
UNITS = Decimal("0.0001")
METHOD_VERSION = "EMERGENCY_TRANSPARENT_V2"
DEMAND_EVIDENCE_CONTRACT = "BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2"
INVENTORY_STATES = frozenset({"IN_STOCK", "STOCKOUT", "UNKNOWN"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_SALES_AUTHORITY_CONTRACTS = frozenset(
    {
        "DISPOSABLE_SYNTHETIC_DAILY_VARIANT_COVERAGE_V1",
        "CANONICAL_SHOPIFYQL_RUN_FACT_AGGREGATE_V1",
        "OWNED_SYNTHETIC_DEMO_CANONICAL_BACKFILL_V1",
    }
)
_MAX_INVENTORY_QUANTITY = Decimal("9999999999.9999")
_STATE_BASIS = frozenset(
    {
        "NO_POINT_IN_TIME_SNAPSHOT",
        "POINT_IN_TIME_SNAPSHOT_ONLY",
        "INCOMPATIBLE_POINT_IN_TIME_EVIDENCE",
    }
)


def _json_primitive(value: Any) -> Any:
    """Return the exact primitive representation used for evidence hashing."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical evidence decimals must be finite")
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical evidence datetimes must be timezone-aware")
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("canonical evidence object keys must be strings")
        return {key: _json_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_primitive(item) for item in value]
    raise ValueError(f"unsupported canonical evidence type: {type(value).__name__}")


def canonical_evidence_json(value: Any) -> str:
    """Serialize evidence deterministically without lossy numeric coercion."""

    return json.dumps(
        _json_primitive(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def canonical_evidence_sha256(value: Any) -> str:
    """Hash the canonical UTF-8 evidence serialization."""

    return hashlib.sha256(canonical_evidence_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DailyNetSales:
    business_date: date
    net_units: Decimal
    source: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "business_date": self.business_date.isoformat(),
            "net_units": format(self.net_units, "f"),
            "source": self.source,
        }


def _validated_daily_sales(
    rows: Iterable[DailyNetSales],
) -> tuple[DailyNetSales, ...]:
    validated: list[DailyNetSales] = []
    seen: set[date] = set()
    for raw in rows:
        if not isinstance(raw, DailyNetSales):
            raise ValueError("sales rows must be DailyNetSales values")
        if not isinstance(raw.business_date, date) or isinstance(
            raw.business_date, datetime
        ):
            raise ValueError("sales business_date must be a date")
        if raw.business_date in seen:
            raise ValueError("sales rows must contain at most one row per date")
        seen.add(raw.business_date)
        source = str(raw.source).strip()
        if not source:
            raise ValueError("sales source is required")
        validated.append(
            DailyNetSales(
                business_date=raw.business_date,
                net_units=_decimal(raw.net_units, "net_units"),
                source=source,
            )
        )
    return tuple(sorted(validated, key=lambda row: (row.business_date, row.source)))


def _sales_rows_sha256(rows: Iterable[DailyNetSales]) -> str:
    ordered = _validated_daily_sales(rows)
    return canonical_evidence_sha256([row.to_json_dict() for row in ordered])


@dataclass(frozen=True)
class ValidatedSalesCoverage:
    contract: str
    source: str
    history_start: date
    history_end: date
    authority_sha256: str
    sales_rows_sha256: str
    authority_json: str

    @classmethod
    def from_authority(
        cls,
        authority: Mapping[str, Any],
        sales_rows: Iterable[DailyNetSales],
    ) -> "ValidatedSalesCoverage":
        if not isinstance(authority, Mapping):
            raise ValueError("sales authority must be an object")
        contract = str(authority.get("contract") or "").strip()
        source = str(authority.get("source") or "").strip()
        history_start = authority.get("history_start")
        history_end = authority.get("history_end")
        if contract not in _SUPPORTED_SALES_AUTHORITY_CONTRACTS or not source:
            raise ValueError("sales authority contract and source are not supported")
        if (
            not isinstance(history_start, date)
            or isinstance(history_start, datetime)
            or not isinstance(history_end, date)
            or isinstance(history_end, datetime)
            or history_end < history_start
        ):
            raise ValueError("sales authority requires valid inclusive date bounds")
        ordered = _validated_daily_sales(sales_rows)
        rows_sha256 = canonical_evidence_sha256(
            [row.to_json_dict() for row in ordered]
        )
        if (
            authority.get("coverage_complete") is not True
            or authority.get("sales_rows_sha256") != rows_sha256
        ):
            raise ValueError("sales authority does not prove exact complete coverage")
        authority_json = canonical_evidence_json(authority)
        return cls(
            contract=contract,
            source=source,
            history_start=history_start,
            history_end=history_end,
            authority_sha256=hashlib.sha256(authority_json.encode("utf-8")).hexdigest(),
            sales_rows_sha256=rows_sha256,
            authority_json=authority_json,
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "source": self.source,
            "history_start": self.history_start.isoformat(),
            "history_end": self.history_end.isoformat(),
            "authority_sha256": self.authority_sha256,
            "sales_rows_sha256": self.sales_rows_sha256,
        }


@dataclass(frozen=True)
class PointInTimeSnapshotRef:
    snapshot_date: date
    location_gid: str
    inventory_snapshot_run_id: str
    source: str
    source_hash: str
    captured_at: datetime
    completed_at: datetime
    available_quantity: Decimal
    incoming_quantity: Decimal
    validation_status: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "snapshot_date": self.snapshot_date.isoformat(),
            "location_gid": self.location_gid,
            "inventory_snapshot_run_id": self.inventory_snapshot_run_id,
            "source": self.source,
            "source_hash": self.source_hash,
            "captured_at": self.captured_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "available_quantity": format(self.available_quantity, "f"),
            "incoming_quantity": format(self.incoming_quantity, "f"),
            "validation_status": self.validation_status,
        }


@dataclass(frozen=True)
class RawWindowEvidence:
    requested_window: int
    actual_denominator_days: int
    inclusive_start: date
    inclusive_end: date
    signed_net_units: Decimal
    signed_calendar_velocity: Decimal

    def to_json_dict(self) -> dict[str, object]:
        return {
            "requested_window": self.requested_window,
            "actual_denominator_days": self.actual_denominator_days,
            "inclusive_start": self.inclusive_start.isoformat(),
            "inclusive_end": self.inclusive_end.isoformat(),
            "signed_net_units": format(self.signed_net_units, "f"),
            "signed_calendar_velocity": format(
                self.signed_calendar_velocity, "f"
            ),
        }


@dataclass(frozen=True)
class DemandEvidenceDay:
    business_date: date
    net_units: Decimal
    inventory_state: str
    state_basis: str
    snapshot_refs: tuple[PointInTimeSnapshotRef, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "business_date": self.business_date.isoformat(),
            "net_units": format(self.net_units, "f"),
            "inventory_state": self.inventory_state,
            "state_basis": self.state_basis,
            "snapshot_refs": [row.to_json_dict() for row in self.snapshot_refs],
        }


@dataclass(frozen=True)
class SnapshotGroup:
    snapshot_date: date
    inventory_snapshot_run_id: str
    source: str
    source_hash: str
    captured_at: datetime
    completed_at: datetime
    locations: tuple[str, ...]
    compatibility_status: str
    aggregate_available_quantity: Decimal | None
    aggregate_incoming_quantity: Decimal | None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "snapshot_date": self.snapshot_date.isoformat(),
            "inventory_snapshot_run_id": self.inventory_snapshot_run_id,
            "source": self.source,
            "source_hash": self.source_hash,
            "captured_at": self.captured_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "locations": list(self.locations),
            "compatibility_status": self.compatibility_status,
            "aggregate_available_quantity": (
                format(self.aggregate_available_quantity, "f")
                if self.aggregate_available_quantity is not None
                else None
            ),
            "aggregate_incoming_quantity": (
                format(self.aggregate_incoming_quantity, "f")
                if self.aggregate_incoming_quantity is not None
                else None
            ),
        }


@dataclass(frozen=True)
class DemandEvidence:
    contract: str
    method_version: str
    history_start: date
    history_end: date
    calendar_days: int
    sales_coverage: ValidatedSalesCoverage
    daily: tuple[DemandEvidenceDay, ...]
    snapshot_groups: tuple[SnapshotGroup, ...]
    raw_windows: Mapping[str, RawWindowEvidence]
    availability_summary: Mapping[str, int]
    statuses: Mapping[str, str]
    reason_codes: tuple[str, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract,
            "method_version": self.method_version,
            "history_start": self.history_start.isoformat(),
            "history_end": self.history_end.isoformat(),
            "calendar_days": self.calendar_days,
            "sales_coverage": self.sales_coverage.to_json_dict(),
            "daily": [row.to_json_dict() for row in self.daily],
            "snapshot_groups": [row.to_json_dict() for row in self.snapshot_groups],
            "raw_windows": {
                key: self.raw_windows[key].to_json_dict() for key in ("7", "14", "28")
            },
            "availability_summary": dict(self.availability_summary),
            "statuses": dict(self.statuses),
            "reason_codes": list(self.reason_codes),
        }


def _validated_snapshot_rows(
    rows: Iterable[PointInTimeSnapshotRef],
    *,
    history_start: date,
    history_end: date,
) -> tuple[PointInTimeSnapshotRef, ...]:
    validated: list[PointInTimeSnapshotRef] = []
    seen: set[tuple[date, str]] = set()
    run_provenance: dict[str, tuple[date, str, str, datetime, datetime]] = {}
    for raw in rows:
        if not isinstance(raw, PointInTimeSnapshotRef):
            raise ValueError("snapshot rows must be PointInTimeSnapshotRef values")
        if (
            not isinstance(raw.snapshot_date, date)
            or isinstance(raw.snapshot_date, datetime)
            or not history_start <= raw.snapshot_date <= history_end
        ):
            raise ValueError("snapshot date must fall within the history bounds")
        location = str(raw.location_gid).strip()
        source = str(raw.source).strip()
        run_id = str(raw.inventory_snapshot_run_id).strip()
        try:
            parsed_run_id = UUID(run_id)
        except ValueError as exc:
            raise ValueError("inventory snapshot run ID must be a UUID") from exc
        if str(parsed_run_id) != run_id:
            raise ValueError("inventory snapshot run ID must use canonical UUID form")
        if not location or not source:
            raise ValueError("snapshot location and source are required")
        if not _SHA256.fullmatch(str(raw.source_hash)):
            raise ValueError("snapshot source hash must be lowercase SHA-256")
        if raw.validation_status != "VALID":
            raise ValueError("only VALID snapshot evidence is accepted")
        if (
            not isinstance(raw.captured_at, datetime)
            or raw.captured_at.tzinfo is None
            or raw.captured_at.utcoffset() is None
            or not isinstance(raw.completed_at, datetime)
            or raw.completed_at.tzinfo is None
            or raw.completed_at.utcoffset() is None
            or raw.completed_at < raw.captured_at
        ):
            raise ValueError("snapshot capture/completion timestamps are invalid")
        key = (raw.snapshot_date, location)
        if key in seen:
            raise ValueError("one snapshot row per date and location is required")
        seen.add(key)
        provenance = (
            raw.snapshot_date,
            source,
            str(raw.source_hash),
            raw.captured_at,
            raw.completed_at,
        )
        if run_id in run_provenance and run_provenance[run_id] != provenance:
            raise ValueError("one snapshot run cannot carry conflicting provenance")
        run_provenance[run_id] = provenance
        available = _inventory_quantity(raw.available_quantity, "available_quantity")
        incoming = _inventory_quantity(raw.incoming_quantity, "incoming_quantity")
        validated.append(
            PointInTimeSnapshotRef(
                snapshot_date=raw.snapshot_date,
                location_gid=location,
                inventory_snapshot_run_id=run_id,
                source=source,
                source_hash=str(raw.source_hash),
                captured_at=raw.captured_at,
                completed_at=raw.completed_at,
                available_quantity=available,
                incoming_quantity=incoming,
                validation_status="VALID",
            )
        )
    return tuple(
        sorted(
            validated,
            key=lambda row: (
                row.snapshot_date,
                row.inventory_snapshot_run_id,
                row.source,
                row.source_hash,
                row.captured_at,
                row.location_gid,
            ),
        )
    )


def build_demand_evidence(
    *,
    history_start: date,
    history_end: date,
    sales_rows: Iterable[DailyNetSales],
    sales_coverage: ValidatedSalesCoverage,
    snapshot_rows: Iterable[PointInTimeSnapshotRef],
) -> DemandEvidence:
    """Build provenance-bound daily demand evidence without availability guesses."""

    if (
        not isinstance(history_start, date)
        or isinstance(history_start, datetime)
        or not isinstance(history_end, date)
        or isinstance(history_end, datetime)
        or history_end < history_start
    ):
        raise ValueError("history bounds must be valid inclusive dates")
    if not isinstance(sales_coverage, ValidatedSalesCoverage):
        raise ValueError("validated sales coverage is required")
    if (
        not sales_coverage.contract.strip()
        or not sales_coverage.source.strip()
        or sales_coverage.history_start != history_start
        or sales_coverage.history_end != history_end
        or not _SHA256.fullmatch(sales_coverage.authority_sha256)
        or not _SHA256.fullmatch(sales_coverage.sales_rows_sha256)
    ):
        raise ValueError("sales coverage does not match the requested evidence bounds")

    ordered_sales = _validated_daily_sales(sales_rows)
    try:
        authority_payload = json.loads(sales_coverage.authority_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("sales coverage authority payload is invalid") from exc
    if (
        not isinstance(authority_payload, dict)
        or canonical_evidence_json(authority_payload) != sales_coverage.authority_json
        or hashlib.sha256(sales_coverage.authority_json.encode("utf-8")).hexdigest()
        != sales_coverage.authority_sha256
        or authority_payload.get("contract") != sales_coverage.contract
        or authority_payload.get("source") != sales_coverage.source
        or authority_payload.get("history_start") != history_start.isoformat()
        or authority_payload.get("history_end") != history_end.isoformat()
        or authority_payload.get("coverage_complete") is not True
        or authority_payload.get("sales_rows_sha256")
        != sales_coverage.sales_rows_sha256
    ):
        raise ValueError("sales coverage authority proof does not match")
    if (
        any(row.source != sales_coverage.source for row in ordered_sales)
        or any(not history_start <= row.business_date <= history_end for row in ordered_sales)
        or _sales_rows_sha256(ordered_sales) != sales_coverage.sales_rows_sha256
    ):
        raise ValueError("sales rows do not match the validated coverage")
    snapshots = _validated_snapshot_rows(
        snapshot_rows, history_start=history_start, history_end=history_end
    )

    sales_by_date = {row.business_date: row.net_units for row in ordered_sales}
    snapshots_by_date: dict[date, list[PointInTimeSnapshotRef]] = {}
    for row in snapshots:
        snapshots_by_date.setdefault(row.snapshot_date, []).append(row)

    grouped_rows: list[SnapshotGroup] = []
    incompatible_dates: set[date] = set()
    for snapshot_date, date_rows in sorted(snapshots_by_date.items()):
        provenance_groups: dict[
            tuple[str, str, str, datetime, datetime],
            list[PointInTimeSnapshotRef],
        ] = {}
        compatibility_keys: set[tuple[str, str, str, datetime]] = set()
        for row in date_rows:
            provenance_key = (
                row.inventory_snapshot_run_id,
                row.source,
                row.source_hash,
                row.captured_at,
                row.completed_at,
            )
            provenance_groups.setdefault(provenance_key, []).append(row)
            compatibility_keys.add(provenance_key[:4])
        compatible = len(compatibility_keys) == 1
        if not compatible:
            incompatible_dates.add(snapshot_date)
        for key, members in sorted(provenance_groups.items()):
            available_total = (
                sum((row.available_quantity for row in members), Decimal("0"))
                .quantize(UNITS, rounding=ROUND_HALF_UP)
                if compatible
                else None
            )
            incoming_total = (
                sum(
                    (row.incoming_quantity for row in members),
                    Decimal("0"),
                ).quantize(UNITS, rounding=ROUND_HALF_UP)
                if compatible
                else None
            )
            grouped_rows.append(
                SnapshotGroup(
                    snapshot_date=snapshot_date,
                    inventory_snapshot_run_id=key[0],
                    source=key[1],
                    source_hash=key[2],
                    captured_at=key[3],
                    completed_at=key[4],
                    locations=tuple(sorted(row.location_gid for row in members)),
                    compatibility_status=(
                        "COMPATIBLE_POINT_IN_TIME_EVIDENCE"
                        if compatible
                        else "INCOMPATIBLE_POINT_IN_TIME_EVIDENCE"
                    ),
                    aggregate_available_quantity=available_total,
                    aggregate_incoming_quantity=incoming_total,
                )
            )

    calendar_days = (history_end - history_start).days + 1
    daily: list[DemandEvidenceDay] = []
    for offset in range(calendar_days):
        day = history_start + timedelta(days=offset)
        refs = tuple(snapshots_by_date.get(day, ()))
        if not refs:
            state_basis = "NO_POINT_IN_TIME_SNAPSHOT"
        elif day in incompatible_dates:
            state_basis = "INCOMPATIBLE_POINT_IN_TIME_EVIDENCE"
        else:
            state_basis = "POINT_IN_TIME_SNAPSHOT_ONLY"
        if state_basis not in _STATE_BASIS:
            raise AssertionError("internal demand-evidence state basis is invalid")
        daily.append(
            DemandEvidenceDay(
                business_date=day,
                net_units=sales_by_date.get(day, Decimal("0")),
                inventory_state="UNKNOWN",
                state_basis=state_basis,
                snapshot_refs=refs,
            )
        )

    raw_windows: dict[str, RawWindowEvidence] = {}
    for requested in (7, 14, 28):
        denominator = min(calendar_days, requested)
        inclusive_start = history_end - timedelta(days=denominator - 1)
        signed_total = sum(
            (
                row.net_units
                for row in daily
                if row.business_date >= inclusive_start
            ),
            Decimal("0"),
        ).quantize(UNITS, rounding=ROUND_HALF_UP)
        raw_windows[str(requested)] = RawWindowEvidence(
            requested_window=requested,
            actual_denominator_days=denominator,
            inclusive_start=inclusive_start,
            inclusive_end=history_end,
            signed_net_units=signed_total,
            signed_calendar_velocity=(signed_total / denominator).quantize(
                VELOCITY, rounding=ROUND_HALF_UP
            ),
        )

    snapshot_dates = set(snapshots_by_date)
    compatible_dates = snapshot_dates - incompatible_dates
    reasons: list[str] = []
    if snapshots:
        reasons.append("POINT_IN_TIME_INVENTORY_NOT_FULL_DAY_AVAILABILITY")
    if incompatible_dates:
        reasons.append("INCOMPATIBLE_POINT_IN_TIME_EVIDENCE")
    reasons.append("STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE")
    return DemandEvidence(
        contract=DEMAND_EVIDENCE_CONTRACT,
        method_version=METHOD_VERSION,
        history_start=history_start,
        history_end=history_end,
        calendar_days=calendar_days,
        sales_coverage=sales_coverage,
        daily=tuple(daily),
        snapshot_groups=tuple(grouped_rows),
        raw_windows=MappingProxyType(raw_windows),
        availability_summary=MappingProxyType(
            {
                "calendar_days": calendar_days,
                "unknown_days": calendar_days,
                "proven_full_day_in_stock_days": 0,
                "proven_full_day_stockout_days": 0,
                "dates_with_no_snapshot": calendar_days - len(snapshot_dates),
                "compatible_point_in_time_dates": len(compatible_dates),
                "incompatible_point_in_time_dates": len(incompatible_dates),
                "snapshot_rows": len(snapshots),
                "positive_snapshot_rows": sum(
                    1 for row in snapshots if row.available_quantity > 0
                ),
                "zero_snapshot_rows": sum(
                    1 for row in snapshots if row.available_quantity == 0
                ),
            }
        ),
        statuses=MappingProxyType(
            {
                "forecast_status": "EMERGENCY_BASELINE_ONLY",
                "model_selection_status": "NOT_VALIDATED",
                "classification_status": "NOT_CALCULATED",
                "stockout_censoring_status": "EVIDENCE_UNAVAILABLE",
                "safety_stock_status": "NOT_CALCULATED",
            }
        ),
        reason_codes=tuple(reasons),
    )


def to_forecast_observations(
    evidence: DemandEvidence,
) -> tuple["DemandObservation", ...]:
    """Project frozen daily evidence into the existing emergency calculator."""

    if not isinstance(evidence, DemandEvidence):
        raise ValueError("demand evidence is required")
    return tuple(
        DemandObservation(row.business_date, row.net_units, row.inventory_state)
        for row in evidence.daily
    )


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


def _inventory_quantity(value: object, field: str) -> Decimal:
    if value is None:
        raise ValueError(f"{field} is required for VALID snapshot evidence")
    parsed = _decimal(value, field)
    if (
        parsed < 0
        or parsed > _MAX_INVENTORY_QUANTITY
        or parsed.as_tuple().exponent < -4
    ):
        raise ValueError(
            f"{field} must be a nonnegative NUMERIC(14,4) quantity"
        )
    return parsed


def forecast_demand(
    observations: Iterable[DemandObservation], *, horizon_days: int
) -> ForecastEvidence:
    """Return a conservative nonnegative forecast with explicit evidence.

    The input must already contain one observation for every calendar date;
    only ``build_demand_evidence`` may materialize a missing date after proving
    source coverage. Proven stockout dates are censored only from the in-stock
    velocity. Returns remain in net sales, while final velocities and forecasts
    are floored at zero. Recent influence is damped to a maximum +/-12.5%
    around the evidence base.
    """

    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or horizon_days < 1:
        raise ValueError("horizon_days must be a positive whole day count")
    indexed: dict[date, DemandObservation] = {}
    for raw in observations:
        if not isinstance(raw.business_date, date) or isinstance(
            raw.business_date, datetime
        ):
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
    if len(indexed) != calendar_days:
        raise ValueError(
            "demand observations must be a contiguous materialized calendar series"
        )
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
        reasons.append("STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE")
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
