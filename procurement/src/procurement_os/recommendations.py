"""Frozen Monday analysis and persisted recommendation evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import os
from enum import StrEnum
import re
import time as monotonic_time
from typing import Any, Iterable
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from .forecasting import (
    DailyNetSales,
    METHOD_VERSION,
    PointInTimeSnapshotRef,
    ValidatedSalesCoverage,
    build_demand_evidence,
    canonical_evidence_sha256,
    forecast_demand,
    to_forecast_observations,
)
from .development_forecast import (
    CONTRACT as DEVELOPMENT_FORECAST_CONTRACT,
    METHOD_VERSION as DEVELOPMENT_FORECAST_METHOD_VERSION,
    V2_CONTRACT as DEVELOPMENT_FORECAST_V2_CONTRACT,
    DevelopmentForecastError,
    build_development_baseline_need_binding,
    calculate_anchored_schedule_horizon,
    calculate_calendar_protection_horizon,
    development_forecast_contract_for_run,
    development_forecast_definition,
    development_forecast_definition_for_run,
    load_development_forecast_policy,
    plan_development_forecast,
)
from .monday_controls import MondayControlError, load_material_edit_policy
from .monday_forecast_retirement import (
    CURRENT_METHOD_VERSION,
    RETIRED_METHOD_VERSION,
    MondayForecastRetirementContractError,
    verify_monday_forecast_v2_retirement_contract,
)
from .po_ledger import open_po_position
from .replenishment import (
    calculate_baseline_need,
    calculate_development_baseline_need,
)
from .strategic import PriceTier, evaluate_price_tiers
from .synthetic_selected_offer import (
    CONTRACT as SYNTHETIC_SELECTED_OFFER_CONTRACT,
    SELECTED_OPERATION_SECONDS,
    SyntheticSelectedOfferError,
    acquire_input_locks,
    classify_frozen_manifest,
    release_input_locks,
    require_attested_selected_mode,
    require_selected_mode_process_policy,
    resolve_selected_offer,
    selected_blocker_evidence,
    selected_mode_requested,
)
from .vendor_rules import VendorRuleValidationError, validate_vendor_rules_input


MONDAY_ANALYSIS_LOCK = 5_920_230_501
SAFETY_LABEL = "TEST DATA — NOT FOR ORDERING"


class MondayRecommendationError(ValueError):
    pass


class _DeadlineCursor:
    """Refresh one absolute operation budget before every cursor statement."""

    def __init__(self, cursor: Any, connection: "_DeadlineConnection") -> None:
        self._cursor = cursor
        self._connection = connection

    def __enter__(self) -> "_DeadlineCursor":
        self._cursor.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        return self._cursor.__exit__(exc_type, exc, traceback)

    def execute(self, *args: Any, **kwargs: Any) -> "_DeadlineCursor":
        self._connection._refresh_timeout()
        self._cursor.execute(*args, **kwargs)
        return self

    def executemany(self, *args: Any, **kwargs: Any) -> "_DeadlineCursor":
        self._connection._refresh_timeout()
        self._cursor.executemany(*args, **kwargs)
        return self

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _DeadlineConnection:
    """Connection view whose SQL budget always derives from one deadline."""

    def __init__(self, connection: Any, deadline: float) -> None:
        self._connection = connection
        self._deadline = deadline

    def _refresh_timeout(self) -> None:
        remaining = self._deadline - monotonic_time.monotonic()
        if remaining <= 0:
            raise MondayRecommendationError("MONDAY_PREPARATION_RETRY_REQUIRED")
        remaining_ms = max(1, int(remaining * 1000))
        self._connection.execute(
            f"SET LOCAL statement_timeout = '{remaining_ms}ms'"
        )
        self._connection.execute(
            f"SET LOCAL lock_timeout = '{min(5000, remaining_ms)}ms'"
        )

    def arm_commit(self) -> None:
        """Bound the transaction COMMIT statement by the same deadline."""
        self._refresh_timeout()

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self._refresh_timeout()
        return self._connection.execute(*args, **kwargs)

    def cursor(self, *args: Any, **kwargs: Any) -> _DeadlineCursor:
        return _DeadlineCursor(
            self._connection.cursor(*args, **kwargs),
            self,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


@dataclass(frozen=True)
class _PreparedRunReference:
    run_id: str
    idempotent_replay: bool


@contextmanager
def _monday_transaction(conn: Any, *, selected: bool):
    """Distinguish selected-mode body failures from ambiguous COMMIT failures."""

    if not selected:
        with conn.transaction():
            yield
        return
    transaction = conn.transaction()
    transaction.__enter__()
    try:
        yield
    except BaseException as exc:
        suppress = transaction.__exit__(type(exc), exc, exc.__traceback__)
        if not suppress:
            raise
    else:
        try:
            transaction.__exit__(None, None, None)
        except BaseException as exc:
            if getattr(exc, "sqlstate", None) in {"40001", "40P01"}:
                raise
            ambiguous = isinstance(
                exc, (psycopg.OperationalError, psycopg.InterfaceError)
            ) or not isinstance(exc, Exception)
            if not ambiguous:
                raise
            try:
                conn.close()
            finally:
                raise MondayRecommendationError(
                    "MONDAY_PREPARATION_COMMIT_OUTCOME_UNKNOWN"
                ) from exc


class MondayRunInputValidationState(StrEnum):
    MATCH = "MATCH"
    MATERIAL_INPUTS_CHANGED = "MATERIAL_INPUTS_CHANGED"
    FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED = (
        "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"
    )


@dataclass(frozen=True)
class MondayRunInputValidation:
    state: MondayRunInputValidationState
    run_id: str
    model_version: str | None
    workflow_stage: str | None
    input_fingerprint: str | None

    @property
    def matches(self) -> bool:
        return self.state is MondayRunInputValidationState.MATCH


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (date, datetime, time, Decimal)):
        return str(value)
    raise TypeError(f"unsupported frozen input type: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_json_default
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _exception_sqlstate(exc: BaseException) -> str | None:
    """Find a driver SQLSTATE without losing a fail-closed wrapper boundary."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        value = getattr(current, "sqlstate", None)
        if isinstance(value, str):
            return value
        next_error = current.__cause__ or current.__context__
        current = next_error if isinstance(next_error, BaseException) else None
    return None


def _arm_selected_commit(conn: Any, deadline: float | None) -> None:
    if monotonic_time.monotonic() >= (deadline or 0):
        raise MondayRecommendationError("MONDAY_PREPARATION_RETRY_REQUIRED")
    if not isinstance(conn, _DeadlineConnection):
        raise MondayRecommendationError(
            "selected preparation lost its absolute deadline boundary"
        )
    conn.arm_commit()


def _whole(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise MondayRecommendationError(f"{field} must be a nonnegative whole number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MondayRecommendationError(
            f"{field} must be a nonnegative whole number"
        ) from exc
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
        raise MondayRecommendationError(f"{field} must be a nonnegative whole number")
    return int(parsed)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _price_tiers_from_rows(rows: Iterable[tuple[Any, ...]]) -> tuple[PriceTier, ...]:
    """Translate frozen database rows without truncating typed break quantities."""

    tiers: list[PriceTier] = []
    for row in rows:
        level_type = str(row[3]).strip().upper()
        break_quantity: int | None = None
        break_unit = str(row[5]).strip().upper() if row[5] is not None else None
        if level_type == "BASE":
            if row[4] is not None or break_unit is not None:
                raise MondayRecommendationError(
                    "BASE price tier cannot carry a break threshold"
                )
        elif level_type == "BREAK":
            break_quantity = _whole(row[4], "price break quantity")
            if break_quantity < 1 or break_unit not in {"BT", "CS"}:
                raise MondayRecommendationError(
                    "price break requires a positive whole BT or CS threshold"
                )
        else:
            raise MondayRecommendationError("price tier must be BASE or BREAK")
        try:
            unit_price = Decimal(row[7])
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise MondayRecommendationError(
                "price tier unit cost must be a positive finite decimal"
            ) from exc
        if not unit_price.is_finite() or unit_price <= 0:
            raise MondayRecommendationError(
                "price tier unit cost must be a positive finite decimal"
            )
        tiers.append(
            PriceTier(level_type, unit_price, break_quantity, break_unit)
        )
    return tuple(tiers)


def _database_evaluation_at(conn: Any) -> datetime:
    """Use the database transaction clock as the one run-wide evaluation instant."""
    if os.getenv("BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT") == "1":
        from .synthetic_price_replacement import registered_monday_evaluation_at

        return registered_monday_evaluation_at(conn)
    return conn.execute("SELECT transaction_timestamp()").fetchone()[0]


def _sales_coverage_digest(rows: Iterable[tuple[Any, ...]]) -> str:
    """Fingerprint exact source-level daily rows used by the Monday forecast."""

    return _fingerprint([list(row) for row in rows])


def _authoritative_sales_rows(
    conn: Any, *, business_date: date, variant_id: str, history_days: int = 84
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    """Return one proven sales source or fail closed on current coverage.

    The canonical ShopifyQL aggregate is trustworthy only when its durable
    readiness evidence proves the complete requested period.  Disposable test
    databases may instead use an explicitly typed, per-variant synthetic daily
    manifest.  Other ad-hoc ``sales_daily`` sources never become buying input.
    """

    if isinstance(history_days, bool) or not isinstance(history_days, int) or history_days < 1:
        raise MondayRecommendationError("CURRENT_SALES_COVERAGE_UNPROVEN")
    history_start = business_date - timedelta(days=history_days)
    history_end = business_date - timedelta(days=1)
    gate = conn.execute(
        """SELECT status,evidence_json
             FROM readiness_gates
            WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
    ).fetchone()
    evidence = gate[1] if gate is not None and isinstance(gate[1], dict) else {}
    database_name, server_address, server_version_num = conn.execute(
        "SELECT current_database(),host(inet_server_addr()),current_setting('server_version_num')::int"
    ).fetchone()

    automated_test_contract = (
        str(database_name).endswith("_test")
        and server_address in {"127.0.0.1", "::1"}
        and int(server_version_num) // 10000 == 16
        and gate is not None
        and gate[0] == "PASS"
        and evidence.get("coverage_contract")
        == "DISPOSABLE_SYNTHETIC_DAILY_VARIANT_COVERAGE_V1"
        and evidence.get("source") == "SYNTHETIC_TEST"
        and evidence.get("history_start") == history_start.isoformat()
        and evidence.get("history_end") == history_end.isoformat()
    )
    demo_markers = dict(
        conn.execute(
            "SELECT key,value FROM meta WHERE key=ANY(%s)",
            (
                [
                    "synthetic_owner_demo_contract",
                    "synthetic_owner_demo_sales_backfill_id",
                ],
            ),
        ).fetchall()
    )
    owned_demo_database = (
        str(database_name).endswith("_demo")
        and server_address in {"127.0.0.1", "::1"}
        and int(server_version_num) // 10000 == 16
        and os.getenv("BUFFALO_RUNTIME_MODE", "").strip().upper()
        == "SYNTHETIC_DEMO"
        and demo_markers.get("synthetic_owner_demo_contract")
        == "BUFFALO_SYNTHETIC_OWNER_DEMO_V1"
    )
    synthetic_contract = automated_test_contract
    if automated_test_contract:
        source = "SYNTHETIC_TEST"
    else:
        try:
            canonical_run_id = UUID(str(evidence.get("sales_backfill_id")))
            canonical_start = date.fromisoformat(str(evidence.get("start_date")))
        except (TypeError, ValueError):
            raise MondayRecommendationError(
                "CURRENT_SALES_COVERAGE_UNPROVEN"
            ) from None
        canonical_run = conn.execute(
            """SELECT status,completed_at,start_date,end_date,source,query_version,
                      store_timezone,expected_chunks,completed_chunks,expected_pages,
                      completed_pages,source_rows,unique_source_facts,resolved_rows,
                      unresolved_rows,ambiguous_rows,excluded_rows,coverage_complete,
                      pages_complete,source_facts_persisted,idempotency_verified,
                      control_totals_reconciled,canonical_aggregate_rebuilt,control_evidence
                 FROM sales_backfill_runs WHERE sales_backfill_id=%s""",
            (canonical_run_id,),
        ).fetchone()
        gate_control_evidence = dict(evidence)
        blockers = gate_control_evidence.pop("blockers", None)
        run_control_evidence = (
            canonical_run[23] if canonical_run is not None else None
        )
        required_integer_evidence = {
            "expected_chunks": 7,
            "completed_chunks": 8,
            "expected_pages": 9,
            "completed_pages": 10,
            "source_rows": 11,
            "unique_source_facts": 12,
            "resolved_rows": 13,
            "unresolved_rows": 14,
            "ambiguous_rows": 15,
            "excluded_rows": 16,
        }
        required_boolean_evidence = {
            "coverage_complete": 17,
            "pages_complete": 18,
            "source_facts_persisted": 19,
            "idempotency_verified": 20,
            "control_totals_reconciled": 21,
            "canonical_aggregate_rebuilt": 22,
        }
        owned_demo_contract = (
            owned_demo_database
            and demo_markers.get("synthetic_owner_demo_sales_backfill_id")
            == str(canonical_run_id)
        )
        expected_run_end = business_date if owned_demo_contract else history_end
        canonical_contract = (
            gate is not None
            and gate[0] == "PASS"
            and blockers == []
            and canonical_run is not None
            and canonical_run[0] == "COMPLETED"
            and canonical_run[1] is not None
            and canonical_run[2] == canonical_start
            and canonical_start <= history_start
            and canonical_run[3] == expected_run_end
            and evidence.get("end_date") == expected_run_end.isoformat()
            and canonical_run[4] == "SHOPIFYQL_SALES"
            and canonical_run[5] == "SHOPIFYQL_SALES_V2"
            and canonical_run[6] == "America/New_York"
            and evidence.get("store_timezone") == canonical_run[6]
            and canonical_run[7] > 0
            and canonical_run[8] == canonical_run[7]
            and canonical_run[9] > 0
            and canonical_run[10] == canonical_run[9]
            and canonical_run[11] == canonical_run[12]
            and canonical_run[12] > 0
            and all(canonical_run[index] is True for index in required_boolean_evidence.values())
            and all(
                type(evidence.get(key)) is int
                and evidence.get(key) == canonical_run[index]
                for key, index in required_integer_evidence.items()
            )
            and all(
                evidence.get(key) is True and canonical_run[index] is True
                for key, index in required_boolean_evidence.items()
            )
            and isinstance(run_control_evidence, dict)
            and run_control_evidence == gate_control_evidence
        )
        if not canonical_contract:
            raise MondayRecommendationError("CURRENT_SALES_COVERAGE_UNPROVEN")

        source_facts = conn.execute(
            """SELECT r.sale_date,r.raw_sales_id,r.source_identity_key,
                      r.canonical_variant_id,r.resolution_status,r.resolution_method,
                      r.resolution_evidence,rf.source_row_hash,r.source_row_hash,
                      rf.observed_net_items_sold,rf.observed_net_sales,
                      rf.observation_count,rf.restatement_detected
                 FROM sales_backfill_run_facts rf
                 JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
                WHERE rf.sales_backfill_id=%s AND r.resolution_status='RESOLVED'
                  AND r.canonical_variant_id=%s AND r.sale_date BETWEEN %s AND %s
                ORDER BY r.sale_date,r.raw_sales_id""",
            (canonical_run_id, str(variant_id), history_start, history_end),
        ).fetchall()
        if any(
            not str(fact[2] or "").strip()
            or not str(fact[7] or "").strip()
            or fact[7] != fact[8]
            for fact in source_facts
        ):
            raise MondayRecommendationError("CURRENT_SALES_COVERAGE_UNPROVEN")

        aggregate_mismatch = conn.execute(
            """WITH expected AS (
                     SELECT r.sale_date,SUM(rf.observed_net_items_sold) AS units_sold,
                            SUM(rf.observed_net_sales) AS net_sales
                       FROM sales_backfill_run_facts rf
                       JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
                      WHERE rf.sales_backfill_id=%s AND r.resolution_status='RESOLVED'
                        AND r.canonical_variant_id=%s
                        AND r.sale_date BETWEEN %s AND %s
                      GROUP BY r.sale_date
                 ), actual AS (
                     SELECT sale_date,units_sold,net_sales,distinct_orders,run_id,
                            source_product_title,source_variant_title
                       FROM sales_daily
                      WHERE variant_id=%s AND source='SHOPIFYQL_SALES'
                        AND sale_date BETWEEN %s AND %s
                 )
                 SELECT COALESCE(e.sale_date,a.sale_date)
                   FROM expected e FULL JOIN actual a USING(sale_date)
                  WHERE e.sale_date IS NULL OR a.sale_date IS NULL
                     OR e.units_sold IS DISTINCT FROM a.units_sold
                     OR e.net_sales IS DISTINCT FROM a.net_sales
                     OR a.distinct_orders IS NOT NULL OR a.run_id IS NOT NULL
                     OR a.source_product_title IS NOT NULL
                     OR a.source_variant_title IS NOT NULL
                  LIMIT 1""",
            (
                canonical_run_id,
                str(variant_id),
                history_start,
                history_end,
                str(variant_id),
                history_start,
                history_end,
            ),
        ).fetchone()
        if aggregate_mismatch is not None:
            raise MondayRecommendationError("CURRENT_SALES_COVERAGE_UNPROVEN")
        source = "SHOPIFYQL_SALES"

    rows = conn.execute(
        """SELECT sale_date,units_sold,net_sales,distinct_orders,source,run_id::text
             FROM sales_daily
            WHERE variant_id=%s AND source=%s AND sale_date BETWEEN %s AND %s
            ORDER BY sale_date,source""",
        (str(variant_id), source, history_start, history_end),
    ).fetchall()
    if synthetic_contract:
        coverage = (evidence.get("variant_coverage") or {}).get(str(variant_id))
        expected_dates = [
            history_start + timedelta(days=offset) for offset in range(history_days)
        ]
        if (
            not isinstance(coverage, dict)
            or len(rows) != history_days
            or [row[0] for row in rows] != expected_dates
            or coverage.get("row_count") != history_days
            or coverage.get("sha256") != _sales_coverage_digest(rows)
        ):
            raise MondayRecommendationError("CURRENT_SALES_COVERAGE_UNPROVEN")

    normalized_daily_sales = tuple(
        DailyNetSales(
            business_date=row[0], net_units=Decimal(row[1]), source=str(row[4])
        )
        for row in rows
    )
    normalized_sales_sha256 = canonical_evidence_sha256(
        [row.to_json_dict() for row in normalized_daily_sales]
    )
    authority = {
        "contract": (
            evidence["coverage_contract"]
            if synthetic_contract
            else (
                "OWNED_SYNTHETIC_DEMO_CANONICAL_BACKFILL_V1"
                if owned_demo_contract
                else "CANONICAL_SHOPIFYQL_RUN_FACT_AGGREGATE_V1"
            )
        ),
        "source": source,
        "history_start": history_start,
        "history_end": history_end,
        "coverage_complete": True,
        "sales_rows_sha256": normalized_sales_sha256,
        "gate_evidence": evidence,
    }
    if not synthetic_contract:
        authority.update(
            {
                "sales_backfill_id": str(canonical_run_id),
                "source_fact_count": len(source_facts),
                "source_fact_sha256": _sales_coverage_digest(source_facts),
                "source_facts": [list(row) for row in source_facts],
            }
        )
        if owned_demo_contract:
            authority["data_mode"] = "SYNTHETIC_DEMO"
    return list(rows), authority


def _load_context_unfinalized(
    conn: Any,
    *,
    business_date: date,
    variant_id: str,
    evaluation_at: datetime,
    offer_resolution_contract: str | None = None,
    development_forecast_contract: str | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {"variant_id": str(variant_id), "blockers": []}
    if development_forecast_contract is not None:
        if development_forecast_contract not in {
            DEVELOPMENT_FORECAST_CONTRACT,
            DEVELOPMENT_FORECAST_V2_CONTRACT,
        }:
            raise MondayRecommendationError(
                "DEVELOPMENT_FORECAST_CONTRACT_INVALID"
            )
        context["development_forecast_contract"] = development_forecast_contract
        context["development_forecast_status"] = "NOT_REACHED"
    variant = conn.execute(
        """SELECT variant_id,product_title,variant_title,sku,active,identity_scope,catalog_state,
                  retail_price
             FROM variants WHERE variant_id=%s""",
        (str(variant_id),),
    ).fetchone()
    if variant is None or not variant[4] or variant[5] != "CURRENT" or variant[6] != "LIVE":
        context["blockers"].append("VARIANT_NOT_CURRENT_ACTIVE_LIVE")
        return context
    context["variant"] = list(variant)
    context["retail_price"] = variant[7]

    legacy_offers = conn.execute(
        """SELECT offer_id,vendor_id::text,supplier_sku,package_type,size_text,raw_pack,
                  shopify_units_per_case,qualifying_units_per_case,assortment_scope,
                  assortment_group,assortable,confidence,source_file,source_page,
                  notes,valid_from,valid_to
             FROM supplier_offers
            WHERE variant_id=%s AND active AND package_type='STANDARD'
            ORDER BY offer_id""",
        (str(variant_id),),
    ).fetchall()
    if offer_resolution_contract is None:
        context["offers"] = [list(row) for row in legacy_offers]
        if len(legacy_offers) != 1:
            context["blockers"].append("EXACTLY_ONE_ACTIVE_STANDARD_OFFER_REQUIRED")
            return context
        offer = legacy_offers[0]
    elif offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
        context["legacy_offer_comparison"] = {
            "active_standard_offer_ids": [int(row[0]) for row in legacy_offers],
            "active_standard_offer_count": len(legacy_offers),
        }
        offer, selected_evidence = resolve_selected_offer(
            conn,
            variant_id=str(variant_id),
            business_date=business_date,
            evaluation_at=evaluation_at,
        )
        selected_evidence["legacy_active_standard_offer_comparison"] = {
            "authority": "COMPARISON_ONLY_NO_AUTHORITY",
            "active_standard_offer_ids": [int(row[0]) for row in legacy_offers],
            "active_standard_offer_count": len(legacy_offers),
        }
        context["selected_offer_input_evidence"] = selected_evidence
        if offer is None:
            context["blockers"].append(str(selected_evidence["blocker"]))
            return context
        context["offers"] = [list(offer)]
    else:
        raise MondayRecommendationError("UNKNOWN_OFFER_RESOLUTION_CONTRACT")
    if offer[11] != "VERIFIED":
        context["blockers"].append("SUPPLIER_OFFER_MAPPING_NOT_VERIFIED")
    try:
        units_per_case = _whole(offer[6], "shopify_units_per_case")
        qualifying_units = _whole(offer[7], "qualifying_units_per_case")
        if units_per_case < 1 or qualifying_units < 1:
            raise MondayRecommendationError("case unit counts must be positive")
    except (MondayRecommendationError, ValueError):
        context["blockers"].append("PACK_CONVERSION_MISSING_OR_INVALID")
        return context
    context["offer_id"] = int(offer[0])
    context["vendor_id"] = str(offer[1])
    context["supplier_sku"] = str(offer[2] or "")
    if not context["supplier_sku"].strip():
        context["blockers"].append("SUPPLIER_SKU_EVIDENCE_REQUIRED")
        return context
    context["units_per_case"] = units_per_case
    context["qualifying_units_per_case"] = qualifying_units
    context["offer_evidence"] = {
        "supplier_sku": offer[2],
        "package_type": offer[3],
        "size_text": offer[4],
        "raw_pack": offer[5],
        "shopify_units_per_case": offer[6],
        "qualifying_units_per_case": offer[7],
        "assortment_scope": offer[8],
        "assortment_group": offer[9],
        "assortable": offer[10],
        "confidence": offer[11],
        "source_file": offer[12],
        "source_page": offer[13],
        "notes": offer[14],
        "valid_from": offer[15],
        "valid_to": offer[16],
    }
    if (
        (offer[15] is not None and business_date < offer[15])
        or (offer[16] is not None and business_date > offer[16])
    ):
        context["blockers"].append("SUPPLIER_OFFER_NOT_VALID_FOR_BUSINESS_DATE")
        return context

    vendor = conn.execute(
        """SELECT v.vendor_name,v.active,r.order_cycle_days,r.lead_time_days,
                  r.lead_time_variability_days,r.minimum_type,r.minimum_value,
                  r.below_minimum_fee,r.loose_order_allowed,r.loose_unit_fee,
                  r.confirmation_source,r.confirmed_by,r.rules_version,
                  r.order_days,r.order_cutoff_local,r.timezone_name,
                  r.expected_delivery_days,r.reliability_pct,r.special_rules,
                  r.holiday_blackout_notes,r.confirmed_at,r.updated_at
             FROM vendors v LEFT JOIN vendor_operating_rules r ON r.vendor_id=v.vendor_id
            WHERE v.vendor_id=%s""",
        (context["vendor_id"],),
    ).fetchone()
    context["vendor_rules"] = list(vendor) if vendor else None
    if vendor is None or not vendor[1] or vendor[2] is None:
        context["blockers"].append("CONFIRMED_VENDOR_RULES_REQUIRED")
        return context
    vendor_rule_input = {
        "order_days": vendor[13],
        "order_cutoff_local": vendor[14],
        "timezone_name": vendor[15],
        "expected_delivery_days": vendor[16],
        "order_cycle_days": vendor[2],
        "lead_time_days": vendor[3],
        "lead_time_variability_days": vendor[4],
        "reliability_pct": vendor[17],
        "minimum_type": vendor[5],
        "minimum_value": vendor[6],
        "below_minimum_fee": vendor[7],
        "loose_order_allowed": vendor[8],
        "loose_unit_fee": vendor[9],
        "special_rules": vendor[18],
        "holiday_blackout_notes": vendor[19],
        "confirmation_source": vendor[10],
    }
    try:
        confirmed_rules = validate_vendor_rules_input(vendor_rule_input)
        vendor_zone = ZoneInfo(confirmed_rules.timezone_name)
    except (VendorRuleValidationError, ZoneInfoNotFoundError, ValueError):
        context["blockers"].append("CONFIRMED_VENDOR_RULES_REQUIRED")
        return context
    if not str(vendor[11] or "").strip() or vendor[20] is None:
        context["blockers"].append("CONFIRMED_VENDOR_RULES_REQUIRED")
        return context
    if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
        context["selected_offer_input_evidence"]["applicable_vendor_terms"] = {
            "vendor_id": context["vendor_id"],
            "vendor_rules": list(vendor),
            "sha256": _fingerprint(list(vendor)),
        }
    local_evaluation = evaluation_at.astimezone(vendor_zone)
    order_weekday = business_date.strftime("%A").upper()
    if (
        local_evaluation.date() != business_date
        or (
            development_forecast_contract != DEVELOPMENT_FORECAST_V2_CONTRACT
            and order_weekday not in confirmed_rules.order_days
        )
        or local_evaluation.timetz().replace(tzinfo=None) >= confirmed_rules.order_cutoff_local
    ):
        context["blockers"].append("VENDOR_ORDER_CALENDAR_NOT_OPEN_FOR_RUN")
        return context

    inventory_capture = conn.execute(
        """SELECT inventory_snapshot_run_id::text,business_date,completed_at,source,
                  source_hash,rows_received,eligible_rows,invalid_rows,incomplete_rows
             FROM inventory_snapshot_runs
            WHERE business_date=%s AND status='COMPLETED'
            ORDER BY completed_at DESC,inventory_snapshot_run_id DESC LIMIT 1""",
        (business_date,),
    ).fetchone()
    context["inventory_capture"] = list(inventory_capture) if inventory_capture else None
    if inventory_capture is None:
        context["blockers"].append("COMPLETE_CURRENT_INVENTORY_REQUIRED")
        return context
    inventory = conn.execute(
        """SELECT location_gid,available_quantity,incoming_quantity,validation_status,
                  inventory_snapshot_run_id::text
             FROM inventory_snapshot_run_rows
            WHERE inventory_snapshot_run_id=%s AND variant_id=%s
            ORDER BY location_gid""",
        (inventory_capture[0], str(variant_id)),
    ).fetchall()
    context["inventory_rows"] = [list(row) for row in inventory]
    if (
        not inventory
        or any(row[3] != "VALID" or row[1] is None or row[2] is None for row in inventory)
    ):
        context["blockers"].append("COMPLETE_CURRENT_INVENTORY_REQUIRED")
        return context
    available = sum((Decimal(row[1]) for row in inventory), Decimal("0"))
    captured_incoming = sum((Decimal(row[2]) for row in inventory), Decimal("0"))

    position = open_po_position(
        conn,
        variant_id=str(variant_id),
        vendor_id=context["vendor_id"],
        as_of=evaluation_at,
    )
    context["open_po_position"] = position
    if position["blocks_reorder"]:
        context["blockers"].append("OPEN_PO_RECONCILIATION_BLOCKED")
    trusted_incoming = Decimal(position["trusted_incoming_units"])
    if captured_incoming != trusted_incoming:
        context["blockers"].append("INCOMING_EVIDENCE_MISMATCH")
    context["available_units"] = available
    context["trusted_incoming_units"] = trusted_incoming

    policies = conn.execute(
        """SELECT policy_id,value_json,approved_by,note
             FROM variant_policies
            WHERE variant_id=%s AND policy_type='REPLENISHMENT_MODE' AND active
              AND (effective_from IS NULL OR effective_from<=%s)
              AND (effective_through IS NULL OR effective_through>=%s)
            ORDER BY policy_id""",
        (str(variant_id), business_date, business_date),
    ).fetchall()
    context["policies"] = [list(row) for row in policies]
    if len(policies) != 1 or not str(policies[0][2] or "").strip():
        context["blockers"].append("OWNER_APPROVED_REPLENISHMENT_POLICY_REQUIRED")
        return context
    policy_mode = str((policies[0][1] or {}).get("mode") or "").strip().upper()
    context["policy_mode"] = policy_mode

    prices = conn.execute(
        """SELECT price_id,offer_id,effective_month,level_type,break_qty,break_unit,
                  case_price,unit_price,source_file,source_page,
                  source_price_book_batch_id::text,source_price_book_row_number
             FROM v_verified_current_prices
            WHERE offer_id=%s ORDER BY CASE level_type WHEN 'BASE' THEN 0 ELSE 1 END,
                  break_unit,break_qty,price_id""",
        (context["offer_id"],),
    ).fetchall()
    context["prices"] = [list(row[:10]) for row in prices]
    if not prices or any(row[2] != _month_start(business_date) for row in prices):
        context["blockers"].append("VERIFIED_CURRENT_PRICE_FOR_RUN_MONTH_REQUIRED")
        return context
    if sum(1 for row in prices if row[3] == "BASE") != 1:
        context["blockers"].append("EXACTLY_ONE_CURRENT_BASE_PRICE_REQUIRED")
        return context
    if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
        context["selected_offer_input_evidence"]["applicable_price_ladder"] = {
            "contract": "FROZEN_SELECTED_OFFER_PRICE_LADDER_V1",
            "effective_month": _month_start(business_date),
            "rows": [list(row[:10]) for row in prices],
            "sha256": _fingerprint([list(row[:10]) for row in prices]),
        }
        lineage_rows = [(int(row[0]), row[10], row[11]) for row in prices]
        has_lineage = [row[1] is not None or row[2] is not None for row in lineage_rows]
        if any(has_lineage):
            batch_ids = {str(row[1]) for row in lineage_rows if row[1] is not None}
            if not all(has_lineage) or len(batch_ids) != 1:
                context["blockers"].append("APPLICABLE_PRICE_AUTHORITY_INVALID")
                return context
            authority_batch_id = next(iter(batch_ids))
            authority = conn.execute(
                """SELECT b.price_book_batch_id::text,b.vendor_id::text,b.status,
                          b.content_sha256,b.scope_membership_sha256,
                          b.schedule_policy_ref,p.policy_sha256,
                          h.supplier_price_authority_event_id::text,h.head_version,
                          h.active_price_book_batch_id::text,h.current_scope_sha256,
                          e.action,e.price_book_batch_id::text,e.raw_content_sha256,
                          e.scope_membership_sha256,e.resulting_current_scope_sha256,
                          e.payload_sha256,e.recorded_at,
                          pe.price_book_promotion_event_id,
                          pe.confirmation_payload_sha256
                     FROM price_book_batches b
                     JOIN supplier_price_schedule_policies p
                       ON p.policy_ref=b.schedule_policy_ref
                     JOIN supplier_price_authority_heads h
                       ON h.vendor_id=b.vendor_id AND h.price_scope_key=b.price_scope_key
                     JOIN supplier_price_authority_events e
                       ON e.supplier_price_authority_event_id=
                          h.supplier_price_authority_event_id
                     JOIN price_book_promotion_events pe
                       ON pe.price_book_batch_id=b.price_book_batch_id
                      AND pe.replacement_contract=b.replacement_contract
                    WHERE b.price_book_batch_id=%s""",
                (authority_batch_id,),
            ).fetchall()
            bindings = conn.execute(
                """SELECT p.price_id,m.source_row_number,m.source_row_sha256
                     FROM prices p
                     JOIN price_book_scope_memberships m
                       ON m.price_book_batch_id=p.source_price_book_batch_id
                      AND m.source_row_number=p.source_price_book_row_number
                      AND m.offer_id=p.offer_id
                      AND m.level_type=p.level_type
                      AND m.break_qty IS NOT DISTINCT FROM p.break_qty
                      AND m.break_unit IS NOT DISTINCT FROM p.break_unit
                    WHERE p.source_price_book_batch_id=%s
                      AND p.offer_id=%s
                      AND p.price_state='current'
                    ORDER BY p.price_id""",
                (authority_batch_id, context["offer_id"]),
            ).fetchall()
            if (
                len(authority) != 1
                or len(bindings) != len(prices)
                or {int(row[0]) for row in bindings}
                != {int(row[0]) for row in prices}
            ):
                context["blockers"].append("APPLICABLE_PRICE_AUTHORITY_INVALID")
                return context
            record = authority[0]
            if (
                record[1] != context["vendor_id"]
                or record[2] != "APPLIED_CURRENT"
                or record[0] != authority_batch_id
                or record[3] is None
                or record[4] != conn.execute(
                    "SELECT supplier_price_membership_sha256(%s)",
                    (authority_batch_id,),
                ).fetchone()[0]
                or record[9] != authority_batch_id
                or record[10] != conn.execute(
                    "SELECT supplier_price_scope_current_sha256(%s)",
                    (context["vendor_id"],),
                ).fetchone()[0]
                or record[11] != "APPLY_REPLACEMENT"
                or record[12] != authority_batch_id
                or record[13] != record[3]
                or record[14] != record[4]
                or record[15] != record[10]
            ):
                context["blockers"].append("APPLICABLE_PRICE_AUTHORITY_INVALID")
                return context
            authority_payload = {
                "contract": "BUFFALO_SYNTHETIC_APPLICABLE_PRICE_AUTHORITY_V1",
                "policy_ref": record[5],
                "policy_sha256": record[6],
                "head": {
                    "event_id": record[7],
                    "head_version": int(record[8]),
                    "active_price_book_batch_id": record[9],
                    "current_scope_sha256": record[10],
                },
                "apply_event": {
                    "event_id": record[7],
                    "price_book_batch_id": record[12],
                    "raw_content_sha256": record[13],
                    "scope_membership_sha256": record[14],
                    "resulting_current_scope_sha256": record[15],
                    "payload_sha256": record[16],
                    "recorded_at": record[17],
                },
                "price_confirmation": {
                    "promotion_event_id": int(record[18]),
                    "confirmation_payload_sha256": record[19],
                },
                "source_batch": {
                    "price_book_batch_id": record[0],
                    "raw_content_sha256": record[3],
                    "scope_membership_sha256": record[4],
                },
                "prices": [
                    {
                        "price_id": int(row[0]),
                        "source_row_number": int(row[1]),
                        "source_row_sha256": row[2],
                    }
                    for row in bindings
                ],
                "commercial_authority": False,
                "real_price_approval": False,
            }
            context["selected_offer_input_evidence"][
                "applicable_price_authority"
            ] = {
                **authority_payload,
                "sha256": _fingerprint(authority_payload),
            }

    try:
        development_policy = (
            load_development_forecast_policy(
                evidence_contract=development_forecast_contract
            )
            if development_forecast_contract is not None
            else None
        )
    except DevelopmentForecastError as exc:
        raise MondayRecommendationError(
            "DEVELOPMENT_FORECAST_INPUT_INVALID"
        ) from exc
    history_days = (
        int(development_policy.values["history"]["connected_history_days"])
        if development_policy is not None
        else 84
    )
    history_start = business_date - timedelta(days=history_days)
    history_end = business_date - timedelta(days=1)
    try:
        sales_rows, sales_authority = _authoritative_sales_rows(
            conn,
            business_date=business_date,
            variant_id=str(variant_id),
            history_days=history_days,
        )
    except MondayRecommendationError:
        context["blockers"].append("CURRENT_SALES_COVERAGE_UNPROVEN")
        return context
    context["sales_authority"] = sales_authority
    context["sales_rows"] = [list(row) for row in sales_rows]
    if development_forecast_contract == DEVELOPMENT_FORECAST_V2_CONTRACT:
        expected_dates = [
            history_start + timedelta(days=offset) for offset in range(history_days)
        ]
        if (
            len(sales_rows) != history_days
            or [row[0] for row in sales_rows] != expected_dates
        ):
            context["blockers"].append("CURRENT_SALES_COVERAGE_UNPROVEN")
            return context
    inventory_history_rows = conn.execute(
        """SELECT d.snapshot_date,d.location_gid,d.available_quantity,d.incoming_quantity,
                  d.inventory_snapshot_run_id::text,d.source,r.source,r.source_hash,
                  d.captured_at,r.completed_at,d.validation_status,
                  rr.inventory_snapshot_run_id::text,rr.available_quantity,
                  rr.incoming_quantity,rr.on_hand_quantity,rr.committed_quantity,
                  rr.reserved_quantity,rr.damaged_quantity,rr.validation_status,
                  rr.validation_message,d.on_hand_quantity,d.committed_quantity,
                  d.reserved_quantity,d.damaged_quantity,d.validation_message,r.started_at
             FROM daily_inventory_snapshots d
             JOIN inventory_snapshot_runs r
               ON r.inventory_snapshot_run_id=d.inventory_snapshot_run_id
              AND r.status='COMPLETED' AND r.business_date=d.snapshot_date
             LEFT JOIN inventory_snapshot_run_rows rr
               ON rr.inventory_snapshot_run_id=d.inventory_snapshot_run_id
              AND rr.variant_id=d.variant_id AND rr.location_gid=d.location_gid
            WHERE d.variant_id=%s AND d.snapshot_date BETWEEN %s AND %s
            ORDER BY d.snapshot_date,d.location_gid,d.inventory_snapshot_run_id""",
        (str(variant_id), history_start, history_end),
    ).fetchall()
    context["historical_inventory_rows"] = [list(row) for row in inventory_history_rows]
    daily_sales = tuple(
        DailyNetSales(
            business_date=row[0], net_units=Decimal(row[1]), source=str(row[4])
        )
        for row in sales_rows
    )
    if any(
        row[5] != row[6]
        or row[11] != row[4]
        or (row[2],row[3],row[20],row[21],row[22],row[23],row[10],row[24])
        != (row[12],row[13],row[14],row[15],row[16],row[17],row[18],row[19])
        or row[8] != row[25]
        for row in inventory_history_rows
    ):
        raise MondayRecommendationError("CURRENT_INVENTORY_HISTORY_EVIDENCE_INVALID")
    snapshot_rows = tuple(
        PointInTimeSnapshotRef(
            snapshot_date=row[0],
            location_gid=str(row[1]),
            inventory_snapshot_run_id=str(row[4]),
            source=str(row[5]),
            source_hash=str(row[7]),
            captured_at=row[8],
            completed_at=row[9],
            available_quantity=row[2],
            incoming_quantity=row[3],
            validation_status=str(row[10]),
        )
        for row in inventory_history_rows
    )
    try:
        sales_coverage = ValidatedSalesCoverage.from_authority(
            sales_authority, daily_sales
        )
        demand_evidence = build_demand_evidence(
            history_start=history_start,
            history_end=history_end,
            sales_rows=daily_sales,
            sales_coverage=sales_coverage,
            snapshot_rows=snapshot_rows,
        )
    except (TypeError, ValueError) as exc:
        raise MondayRecommendationError(
            "CURRENT_DEMAND_EVIDENCE_INVALID"
        ) from exc
    demand_payload = demand_evidence.to_json_dict()
    context["demand_evidence"] = demand_payload
    observations = list(to_forecast_observations(demand_evidence))
    context["demand_observations"] = observations

    protection_days = int(vendor[2]) + int(vendor[3]) + int(
        Decimal(vendor[4]).to_integral_value(rounding=ROUND_CEILING)
    )
    if development_forecast_contract is None:
        forecast = forecast_demand(observations, horizon_days=protection_days)
        need = calculate_baseline_need(
            forecast_daily_velocity=forecast.forecast_daily_velocity,
            forecast_units_for_protection=forecast.forecast_units,
            forecast_horizon_days=protection_days,
            available_units=available,
            trusted_incoming_units=trusted_incoming,
            order_cycle_days=int(vendor[2]),
            lead_time_days=int(vendor[3]),
            lead_time_variability_days=vendor[4],
            policy_mode=policy_mode,
            units_per_case=units_per_case,
            loose_order_allowed=bool(vendor[8]),
            loose_unit_fee=vendor[9],
            open_po_blocked=bool(position["blocks_reorder"]),
        )
    elif development_forecast_contract in {
        DEVELOPMENT_FORECAST_CONTRACT,
        DEVELOPMENT_FORECAST_V2_CONTRACT,
    }:
        try:
            if development_forecast_contract == DEVELOPMENT_FORECAST_V2_CONTRACT:
                protection_calendar = calculate_anchored_schedule_horizon(
                    evaluation_at=evaluation_at,
                    business_date=business_date,
                    vendor_id=context["vendor_id"],
                    vendor_rules=vendor_rule_input,
                )
            else:
                protection_calendar = calculate_calendar_protection_horizon(
                    evaluation_at=evaluation_at,
                    timezone_name=confirmed_rules.timezone_name,
                    order_days=confirmed_rules.order_days,
                    order_cutoff_local=confirmed_rules.order_cutoff_local,
                    expected_delivery_days=confirmed_rules.expected_delivery_days,
                    order_cycle_days=confirmed_rules.order_cycle_days,
                    lead_time_days=confirmed_rules.lead_time_days,
                    lead_time_variability_days=(
                        confirmed_rules.lead_time_variability_days
                    ),
                )
            protection_days = int(protection_calendar["horizon_days"])
            forecast = plan_development_forecast(
                observations,
                horizon_days=protection_days,
                protection_calendar=protection_calendar,
                policy=development_policy,
            )
        except DevelopmentForecastError as exc:
            raise MondayRecommendationError(
                "DEVELOPMENT_FORECAST_INPUT_INVALID"
            ) from exc
        development_evidence = forecast.to_json_dict()
        context["development_forecast_evidence"] = development_evidence
        context["development_forecast_evidence_sha256"] = forecast.evidence_sha256
        if forecast.status != "READY":
            context["development_forecast_status"] = "BLOCKED"
            context["blockers"].append(
                "DEVELOPMENT_FORECAST_PROTECTION_UNAVAILABLE"
            )
            return context
        context["development_forecast_status"] = "READY"
        need = calculate_development_baseline_need(
            forecast_daily_velocity=forecast.forecast_daily_velocity,
            point_forecast_units=forecast.point_forecast_units,
            empirical_protection_units=forecast.protection_units,
            forecast_horizon_days=protection_days,
            available_units=available,
            trusted_incoming_units=trusted_incoming,
            order_cycle_days=int(vendor[2]),
            lead_time_days=int(vendor[3]),
            lead_time_variability_days=vendor[4],
            policy_mode=policy_mode,
            units_per_case=units_per_case,
            loose_order_allowed=bool(vendor[8]),
            loose_unit_fee=vendor[9],
            open_po_blocked=bool(position["blocks_reorder"]),
            protection_days_override=protection_days,
        )
        if development_forecast_contract == DEVELOPMENT_FORECAST_V2_CONTRACT:
            context["forecast"] = forecast
            context["need"] = need
            context["development_baseline_need_binding"] = (
                build_development_baseline_need_binding(context, need)
            )
    else:
        raise MondayRecommendationError(
            "DEVELOPMENT_FORECAST_CONTRACT_INVALID"
        )
    if need.status == "BLOCKED":
        context["blockers"].extend(need.reason_codes)
        return context
    if need.loose_units > 0 and Decimal(vendor[9] or 0) > 0:
        context["blockers"].append("LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED")
        return context
    try:
        tiers = _price_tiers_from_rows(prices)
        strategic = evaluate_price_tiers(
            tiers,
            baseline_cases=need.cases,
            baseline_loose_units=need.loose_units,
            sellable_units_per_case=units_per_case,
            qualifying_units_per_case=qualifying_units,
            loose_order_allowed=bool(vendor[8]),
            loose_units_qualify=False,
        )
    except (MondayRecommendationError, ValueError, TypeError, InvalidOperation):
        context["blockers"].append("VERIFIED_CURRENT_PRICE_LADDER_INVALID")
        return context
    context["forecast"] = forecast
    context["need"] = need
    context["strategic"] = strategic
    if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
        eligible_rows = [
            row
            for row in prices
            if row[3] == "BASE"
            or (
                row[3] == "BREAK"
                and row[5] == "CS"
                and Decimal(row[4]) <= Decimal(need.cases)
            )
            or (
                row[3] == "BREAK"
                and row[5] == "BT"
                and Decimal(row[4])
                <= Decimal(need.cases * qualifying_units)
            )
        ]
        selected_rows = [
            row
            for row in eligible_rows
            if Decimal(row[7]) == strategic.selected_unit_cost
        ]
        if len(selected_rows) != 1:
            context["blockers"].append(
                "SELECTED_INITIAL_PRICE_TIER_IDENTITY_AMBIGUOUS"
            )
            return context
        initial_price = selected_rows[0]
        context["selected_offer_input_evidence"]["initial_applicable_price_tier"] = {
            "price_id": int(initial_price[0]),
            "offer_id": int(initial_price[1]),
            "level_type": initial_price[3],
            "break_qty": initial_price[4],
            "break_unit": initial_price[5],
            "case_price": initial_price[6],
            "unit_price": initial_price[7],
            "price_ladder_sha256": context["selected_offer_input_evidence"][
                "applicable_price_ladder"
            ]["sha256"],
        }
        context["selected_offer_input_evidence_sha256"] = _fingerprint(
            context["selected_offer_input_evidence"]
        )
    return context


def _load_context(
    conn: Any,
    *,
    business_date: date,
    variant_id: str,
    evaluation_at: datetime,
    offer_resolution_contract: str | None = None,
    development_forecast_contract: str | None = None,
) -> dict[str, Any]:
    """Finalize every selected context as one hash-bound evidence envelope."""

    context = _load_context_unfinalized(
        conn,
        business_date=business_date,
        variant_id=variant_id,
        evaluation_at=evaluation_at,
        offer_resolution_contract=offer_resolution_contract,
        development_forecast_contract=development_forecast_contract,
    )
    if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
        evidence = context.get("selected_offer_input_evidence")
        if not isinstance(evidence, dict):
            blockers = context.get("blockers") or [
                "ROUTINE_SELECTED_OFFER_LINEAGE_INVALID"
            ]
            evidence = selected_blocker_evidence(
                blocker=str(blockers[0]),
                variant_id=str(variant_id),
                business_date=business_date,
                evaluation_at=evaluation_at,
            )
            context["selected_offer_input_evidence"] = evidence
        if context.get("blockers"):
            normalized_blockers = sorted(
                {str(value) for value in context["blockers"]}
            )
            context["blockers"] = normalized_blockers
            evidence["resolution_blockers"] = normalized_blockers
            evidence["blocker"] = normalized_blockers[0]
            evidence["recommendation_effect"] = "NO_RECOMMENDATION_BLOCKED"
        context["selected_offer_input_evidence_sha256"] = _fingerprint(evidence)
    return context


def _prepare_monday_run_impl(
    conn: Any,
    *,
    business_date: date,
    idempotency_key: str,
    variant_ids: Iterable[str],
    actor: str,
    offer_resolution_contract: str | None = None,
    _inject_failure_after_persistence: bool = False,
    _operation_deadline: float | None = None,
) -> dict[str, Any]:
    """Freeze source evidence and persist deterministic recommendations."""

    key = str(idempotency_key).strip()
    reviewer = str(actor).strip()
    normalized_ids = tuple(sorted({str(value).strip() for value in variant_ids if str(value).strip()}))
    if not key or not reviewer or not normalized_ids:
        raise MondayRecommendationError("business run key, actor, and Variant IDs are required")
    with _monday_transaction(
        conn,
        selected=offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT,
    ):
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
            conn = _DeadlineConnection(conn, _operation_deadline or 0)
        development_forecast_contract: str | None = None
        development_forecast_definition_value = None
        try:
            if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
                require_attested_selected_mode(conn)
                development_forecast_definition_value = (
                    development_forecast_definition_for_run(
                        conn,
                        offer_resolution_contract=offer_resolution_contract,
                    )
                )
                development_forecast_contract = (
                    None
                    if development_forecast_definition_value is None
                    else development_forecast_definition_value.evidence_contract
                )
            verify_monday_forecast_v2_retirement_contract(conn)
            material_edit_policy = load_material_edit_policy().evidence()
        except (
            DevelopmentForecastError,
            MondayControlError,
            MondayForecastRetirementContractError,
        ) as exc:
            raise MondayRecommendationError(str(exc)) from exc
        if offer_resolution_contract is None and not conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (MONDAY_ANALYSIS_LOCK,)
        ).fetchone()[0]:
            raise MondayRecommendationError("Monday analysis lock is unavailable")
        existing = conn.execute(
            """SELECT run_id,input_fingerprint,workflow_stage,procurement_output_mode,status,
                      started_at,model_version,procurement_input_manifest
                 FROM runs
                WHERE run_type='MONDAY_PROCUREMENT' AND idempotency_key=%s FOR UPDATE""",
            (key,),
        ).fetchone()
        if existing is not None and (
            existing[3] != "INTERNAL_DRAFT_ONLY" or existing[4] != "RUNNING"
        ):
            raise MondayRecommendationError("run key belongs to an incompatible workflow")
        if existing is not None and (
            existing[6] == RETIRED_METHOD_VERSION
            and existing[4] == "RUNNING"
            and existing[2] in {"PREPARING", "AWAITING_REVIEW", "REVIEWED"}
        ):
            raise MondayRecommendationError(
                MondayRunInputValidationState.FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED.value
            )
        if existing is not None:
            try:
                stored_manifest = json.loads(existing[7])
                stored_contract, classified_variant_ids = classify_frozen_manifest(
                    existing[7]
                )
                stored_business_date = str(stored_manifest["business_date"])
                stored_variant_ids = tuple(
                    str(value) for value in stored_manifest["variant_ids"]
                )
                if stored_variant_ids != classified_variant_ids:
                    raise MondayRecommendationError(
                        "run key has an invalid frozen input manifest"
                    )
                if hashlib.sha256(existing[7].encode("utf-8")).hexdigest() != existing[1]:
                    raise MondayRecommendationError(
                        "run key has an invalid frozen input manifest"
                    )
            except (
                AttributeError,
                json.JSONDecodeError,
                SyntheticSelectedOfferError,
                TypeError,
            ):
                raise MondayRecommendationError("run key has an invalid frozen input manifest")
            except KeyError as exc:
                raise MondayRecommendationError(
                    "run key has an invalid frozen input manifest"
                ) from exc
            if stored_contract not in {None, SYNTHETIC_SELECTED_OFFER_CONTRACT}:
                raise MondayRecommendationError("UNKNOWN_OFFER_RESOLUTION_CONTRACT")
            if stored_contract != offer_resolution_contract:
                if stored_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
                    raise MondayRecommendationError(
                        "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED"
                    )
                raise MondayRecommendationError(
                    "run key already exists with different frozen inputs"
                )
            if stored_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT and (
                stored_business_date != str(business_date)
                or stored_variant_ids != normalized_ids
            ):
                raise MondayRecommendationError(
                    "run key already exists with different frozen inputs"
                )
            if (
                stored_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT
                and existing[2] in {"DRAFTS_BUILT", "PACKET_BUILT"}
            ):
                _arm_selected_commit(conn, _operation_deadline)
                return _PreparedRunReference(str(existing[0]), True)
        active_same_day = conn.execute(
            """SELECT run_id,idempotency_key,model_version,status,workflow_stage FROM runs
                WHERE run_type='MONDAY_PROCUREMENT' AND status='RUNNING'
                  AND business_date=%s
                ORDER BY run_id FOR UPDATE""",
            (business_date,),
        ).fetchone()
        if active_same_day is not None and (
            existing is None or active_same_day[0] != existing[0]
        ):
            if (
                active_same_day[2] == RETIRED_METHOD_VERSION
                and active_same_day[3] == "RUNNING"
                and active_same_day[4] in {"PREPARING", "AWAITING_REVIEW", "REVIEWED"}
            ):
                raise MondayRecommendationError(
                    MondayRunInputValidationState.FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED.value
                )
            raise MondayRecommendationError(
                "an active Monday Procurement run already exists for this business date; "
                "same-day replacement/supersession is not implemented"
            )
        evaluation_at = (
            existing[5]
            if existing is not None
            else _database_evaluation_at(conn)
        )
        contexts = [
            _load_context(
                conn,
                business_date=business_date,
                variant_id=variant_id,
                evaluation_at=evaluation_at,
                offer_resolution_contract=offer_resolution_contract,
                development_forecast_contract=development_forecast_contract,
            )
            for variant_id in normalized_ids
        ]
        run_method_version = (
            development_forecast_definition_value.method_version
            if development_forecast_definition_value is not None
            else METHOD_VERSION
        )
        frozen_manifest = {
            "business_date": business_date,
            "variant_ids": normalized_ids,
            "method_version": run_method_version,
            "evaluation_at": evaluation_at,
            "material_edit_policy": material_edit_policy,
            "contexts": contexts,
        }
        if offer_resolution_contract is not None:
            frozen_manifest["offer_resolution_contract"] = offer_resolution_contract
        if development_forecast_contract is not None:
            frozen_manifest["development_forecast_contract"] = (
                development_forecast_contract
            )
        input_manifest = _canonical_json(frozen_manifest)
        if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
            try:
                classified_contract, classified_ids = classify_frozen_manifest(
                    input_manifest
                )
            except SyntheticSelectedOfferError as exc:
                raise MondayRecommendationError(str(exc)) from exc
            if (
                classified_contract != offer_resolution_contract
                or classified_ids != normalized_ids
            ):
                raise MondayRecommendationError(
                    "run has an invalid frozen input manifest"
                )
        input_fingerprint = hashlib.sha256(input_manifest.encode()).hexdigest()
        if existing is not None:
            if existing[1] != input_fingerprint:
                raise MondayRecommendationError("run key already exists with different frozen inputs")
            if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
                _arm_selected_commit(conn, _operation_deadline)
                return _PreparedRunReference(str(existing[0]), True)
            return get_monday_run(conn, str(existing[0]), idempotent_replay=True)

        run_id = conn.execute(
            """INSERT INTO runs(run_type,status,source_data_through,business_date,started_at,
                       idempotency_key,input_fingerprint,workflow_stage,model_version,notes,
                       procurement_output_mode,procurement_input_manifest)
                VALUES ('MONDAY_PROCUREMENT','RUNNING',%s,%s,%s,%s,%s,'PREPARING',%s,%s,
                        'INTERNAL_DRAFT_ONLY',%s)
                RETURNING run_id""",
            (
                datetime.combine(business_date - timedelta(days=1), time.max, tzinfo=timezone.utc),
                business_date,evaluation_at,key,input_fingerprint,run_method_version,
                f"{SAFETY_LABEL}; prepared by {reviewer}",input_manifest,
            ),
        ).fetchone()[0]
        exception_count = 0
        for context in contexts:
            blockers = sorted(set(context["blockers"]))
            if blockers:
                exception_count += 1
                variant_exists = "VARIANT_NOT_CURRENT_ACTIVE_LIVE" not in blockers or bool(
                    conn.execute(
                        "SELECT 1 FROM variants WHERE variant_id=%s",
                        (context["variant_id"],),
                    ).fetchone()
                )
                exception_variant_id = (
                    context["variant_id"] if variant_exists else None
                )
                message = ", ".join(blockers)
                if exception_variant_id is None:
                    message += f"; requested Variant ID {context['variant_id']} is absent"
                conn.execute(
                    """INSERT INTO exceptions(run_id,exception_type,severity,variant_id,vendor_id,
                               offer_id,supplier_sku,message,status)
                        VALUES (%s,'MONDAY_INPUT_BLOCKER','HIGH',%s,%s,%s,%s,%s,'OPEN')""",
                    (
                        run_id,exception_variant_id,context.get("vendor_id"),context.get("offer_id"),
                        context.get("supplier_sku"),message,
                    ),
                )
                continue
            forecast = context["forecast"]
            need = context["need"]
            strategic = context["strategic"]
            conn.execute(
                """INSERT INTO inventory_snapshots(
                           run_id,variant_id,available_quantity,incoming_quantity,captured_at,
                           source_inventory_snapshot_run_id)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,context["variant_id"],context["available_units"],
                    context["trusted_incoming_units"],context["inventory_capture"][2],
                    context["inventory_capture"][0],
                ),
            )
            development_evidence = context.get("development_forecast_evidence")
            if isinstance(development_evidence, dict):
                evaluation_metrics = development_evidence["evaluation_metrics"]
                forecast_diagnostics = {
                    "demand_evidence": context["demand_evidence"],
                    "development_forecast_evidence": development_evidence,
                    "development_forecast_evidence_sha256": context[
                        "development_forecast_evidence_sha256"
                    ],
                    "forecast_status": "DEVELOPMENT_CALCULATED",
                    "model_selection_status": "ROLLING_ORIGIN_FVA_CALCULATED",
                    "classification_status": "XYZ_CALCULATED_ABC_NOT_CONFIGURED",
                    "stockout_censoring_status": "EXPLICIT_CAUSAL_EVIDENCE_ONLY",
                    "safety_stock_status": "EMPIRICAL_FULL_HORIZON_PROTECTION",
                    "reason_codes": forecast.reason_codes,
                    "outlier_capped_days": 0,
                }
                demand_regime = development_evidence["demand_regime"]
                selected_model = development_evidence["selected_model"]
                xyz_class = development_evidence["xyz_class"]
                protection_units = forecast.protection_units
                wape = evaluation_metrics["wape"]
                mase = evaluation_metrics["mase"]
                bias = evaluation_metrics["bias"]
            else:
                forecast_diagnostics = {
                    "demand_evidence": context["demand_evidence"],
                    "forecast_status": "EMERGENCY_BASELINE_ONLY",
                    "model_selection_status": "NOT_VALIDATED",
                    "classification_status": "NOT_CALCULATED",
                    "stockout_censoring_status": "EVIDENCE_UNAVAILABLE",
                    "safety_stock_status": "NOT_CALCULATED",
                    "reason_codes": forecast.reason_codes,
                    "outlier_capped_days": forecast.outlier_capped_days,
                }
                demand_regime = selected_model = xyz_class = protection_units = None
                wape = mase = bias = None
            conn.execute(
                """INSERT INTO forecast_results(
                           run_id,variant_id,demand_regime,selected_model,xyz_class,forecast_units,
                           safety_stock_units,protection_days,baseline_replenishment_units,
                           calendar_velocity,in_stock_velocity,wape,mase,bias,confidence,diagnostics,
                           input_fingerprint,method_version,history_start,history_end,horizon_days)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,
                            %s,%s,%s,%s,%s)""",
                (
                    run_id,context["variant_id"],demand_regime,selected_model,
                    xyz_class,forecast.forecast_units,protection_units,
                    need.protection_days,need.raw_need_units,
                    forecast.calendar_velocity,forecast.in_stock_velocity,
                    wape,mase,bias,forecast.confidence,
                    json.dumps(forecast_diagnostics, sort_keys=True),
                    input_fingerprint,forecast.method_version,forecast.history_start,forecast.history_end,
                    forecast.horizon_days,
                ),
            )
            price_authority = (
                context.get("selected_offer_input_evidence", {}).get(
                    "applicable_price_authority"
                )
                if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT
                else None
            )
            authority_by_price = (
                {
                    int(item["price_id"]): item
                    for item in price_authority["prices"]
                }
                if isinstance(price_authority, dict)
                else {}
            )
            for price in context["prices"]:
                lineage = authority_by_price.get(int(price[0]))
                if price_authority is None:
                    conn.execute(
                        """INSERT INTO run_price_snapshots(
                                   run_id,offer_id,price_state,effective_month,level_type,break_qty,
                                   break_unit,case_price,unit_price,source_file,source_page)
                            VALUES (%s,%s,'current',%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (run_id,price[1],price[2],price[3],price[4],price[5],price[6],price[7],price[8],price[9]),
                    )
                elif lineage is None:
                    raise MondayRecommendationError(
                        "APPLICABLE_PRICE_AUTHORITY_INVALID"
                    )
                else:
                    conn.execute(
                        """INSERT INTO run_price_snapshots(
                                   run_id,offer_id,price_state,effective_month,level_type,
                                   break_qty,break_unit,case_price,unit_price,source_file,
                                   source_page,source_price_id,source_price_book_batch_id,
                                   source_price_book_row_number,
                                   supplier_price_authority_event_id)
                            VALUES (%s,%s,'current',%s,%s,%s,%s,%s,%s,%s,%s,
                                    %s,%s,%s,%s)""",
                        (
                            run_id,price[1],price[2],price[3],price[4],price[5],
                            price[6],price[7],price[8],price[9],price[0],
                            price_authority["source_batch"]["price_book_batch_id"],
                            lineage["source_row_number"],
                            price_authority["apply_event"]["event_id"],
                        ),
                    )
            retail_price = (
                Decimal(context["retail_price"])
                if context["retail_price"] is not None
                else None
            )
            unit_gross_profit = (
                retail_price - strategic.selected_unit_cost
                if retail_price is not None
                else None
            )
            gross_margin_pct = (
                (unit_gross_profit / retail_price * Decimal("100")).quantize(
                    Decimal("0.01")
                )
                if retail_price is not None and retail_price > 0
                else None
            )
            metrics = {
                "available_units": context["available_units"],
                "trusted_incoming_units": context["trusted_incoming_units"],
                "frozen_inventory_capture": context["inventory_capture"],
                "frozen_inventory_rows": context["inventory_rows"],
                "frozen_sales_authority": context["sales_authority"],
                "frozen_price_ladder": context["prices"],
                "frozen_catalog_retail_price": retail_price,
                "frozen_unit_gross_profit": unit_gross_profit,
                "frozen_gross_margin_pct": gross_margin_pct,
                "frozen_open_po_position": context["open_po_position"],
                "frozen_vendor_name": context["vendor_rules"][0],
                "target_units": need.target_units,
                "raw_need_units": need.raw_need_units,
                "pack_rounding_units": need.pack_rounding_units,
                "loose_fee": need.loose_fee,
                "forecast_daily_velocity": forecast.forecast_daily_velocity,
                "forecast_units": forecast.forecast_units,
                "forecast_horizon_days": forecast.horizon_days,
                "forecast_method_version": forecast.method_version,
                "forecast_status": "EMERGENCY_BASELINE_ONLY",
                "model_selection_status": "NOT_VALIDATED",
                "classification_status": "NOT_CALCULATED",
                "stockout_censoring_status": "EVIDENCE_UNAVAILABLE",
                "safety_stock_status": "NOT_CALCULATED",
                "demand_regime": None,
                "selected_model": None,
                "abc_class": None,
                "xyz_class": None,
                "in_stock_velocity": None,
                "safety_stock_units": None,
                "demand_evidence": context["demand_evidence"],
                "forecast_reason_codes": forecast.reason_codes,
                "need_reason_codes": need.reason_codes,
                "strategic_status": strategic.status,
                "strategic_reason_codes": strategic.reason_codes,
                "strategic_candidates": [candidate.__dict__ for candidate in strategic.next_tiers],
                "frozen_vendor_terms": {
                    "supplier_sku": context["supplier_sku"],
                    "qualifying_units_per_case": context["qualifying_units_per_case"],
                    "order_days": context["vendor_rules"][13],
                    "order_cutoff_local": context["vendor_rules"][14],
                    "timezone_name": context["vendor_rules"][15],
                    "expected_delivery_days": context["vendor_rules"][16],
                    "order_cycle_days": context["vendor_rules"][2],
                    "lead_time_days": context["vendor_rules"][3],
                    "lead_time_variability_days": context["vendor_rules"][4],
                    "reliability_pct": context["vendor_rules"][17],
                    "loose_order_allowed": bool(context["vendor_rules"][8]),
                    "loose_unit_fee": context["vendor_rules"][9] or Decimal("0"),
                    "minimum_type": context["vendor_rules"][5],
                    "minimum_value": context["vendor_rules"][6],
                    "below_minimum_fee": context["vendor_rules"][7],
                    "special_rules": context["vendor_rules"][18],
                    "holiday_blackout_notes": context["vendor_rules"][19],
                    "confirmation_source": context["vendor_rules"][10],
                    "confirmed_by": context["vendor_rules"][11],
                    "rules_version": context["vendor_rules"][12],
                    "confirmed_at": context["vendor_rules"][20],
                    "updated_at": context["vendor_rules"][21],
                },
                "frozen_offer_evidence": context["offer_evidence"],
                "material_edit_policy": material_edit_policy,
                "safety_label": SAFETY_LABEL,
            }
            if isinstance(development_evidence, dict):
                metrics.update(
                    {
                        "development_forecast_contract": development_forecast_contract,
                        "development_forecast_evidence": development_evidence,
                        "development_forecast_evidence_sha256": context[
                            "development_forecast_evidence_sha256"
                        ],
                        "forecast_status": "DEVELOPMENT_CALCULATED",
                        "model_selection_status": "ROLLING_ORIGIN_FVA_CALCULATED",
                        "classification_status": "XYZ_CALCULATED_ABC_NOT_CONFIGURED",
                        "stockout_censoring_status": "EXPLICIT_CAUSAL_EVIDENCE_ONLY",
                        "safety_stock_status": "EMPIRICAL_FULL_HORIZON_PROTECTION",
                        "forecast_point_units": forecast.point_forecast_units,
                        "safety_stock_units": forecast.protection_units,
                        "forecast_target_units": forecast.target_units,
                        "demand_regime": development_evidence["demand_regime"],
                        "selected_model": development_evidence["selected_model"],
                        "abc_class": development_evidence["abc_class"],
                        "xyz_class": development_evidence["xyz_class"],
                        "in_stock_velocity": forecast.in_stock_velocity,
                    }
                )
            if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
                metrics["selected_offer_input_evidence"] = context[
                    "selected_offer_input_evidence"
                ]
                metrics["selected_offer_input_evidence_sha256"] = context[
                    "selected_offer_input_evidence_sha256"
                ]
                metrics["selected_price_ladder_sha256"] = context[
                    "selected_offer_input_evidence"
                ]["applicable_price_ladder"]["sha256"]
            conn.execute(
                """INSERT INTO procurement_recommendations(
                           run_id,variant_id,vendor_id,offer_id,baseline_units,recommended_cases,
                           recommended_loose_units,recommended_unit_cost,reason_code,review_required,
                           explanation,metrics,input_fingerprint,recommendation_status,
                           strategic_extra_units,units_per_case,recommended_units,
                           frozen_supplier_sku,frozen_qualifying_units_per_case,
                           frozen_loose_order_allowed,frozen_loose_unit_fee,
                           frozen_minimum_type,frozen_minimum_value,frozen_below_minimum_fee)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s::jsonb,%s,%s,0,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,context["variant_id"],context["vendor_id"],context["offer_id"],
                    need.raw_need_units,need.cases,need.loose_units,strategic.selected_unit_cost,
                    "BASELINE_REPLENISHMENT",SAFETY_LABEL,json.dumps(metrics,default=_json_default,sort_keys=True),
                    input_fingerprint,need.status,context["units_per_case"],need.ordered_units,
                    context["supplier_sku"],context["qualifying_units_per_case"],
                    bool(context["vendor_rules"][8]),context["vendor_rules"][9] or Decimal("0"),
                    context["vendor_rules"][5],context["vendor_rules"][6],context["vendor_rules"][7],
                ),
            )
        if _inject_failure_after_persistence:
            raise MondayRecommendationError(
                "injected selected-offer preparation failure before commit"
            )
        conn.execute(
            "UPDATE runs SET workflow_stage='AWAITING_REVIEW',exception_count=%s WHERE run_id=%s",
            (exception_count,run_id),
        )
        if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
            _arm_selected_commit(conn, _operation_deadline)
    if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
        return _PreparedRunReference(str(run_id), False)
    return get_monday_run(conn, str(run_id), idempotent_replay=False)


def prepare_monday_run(
    conn: Any,
    *,
    business_date: date,
    idempotency_key: str,
    variant_ids: Iterable[str],
    actor: str,
    _inject_failure_after_persistence: bool = False,
) -> dict[str, Any]:
    """Prepare legacy inputs unchanged or the attested synthetic selected path."""

    try:
        selected = selected_mode_requested()
    except SyntheticSelectedOfferError as exc:
        raise MondayRecommendationError(str(exc)) from exc
    if not selected:
        return _prepare_monday_run_impl(
            conn,
            business_date=business_date,
            idempotency_key=idempotency_key,
            variant_ids=variant_ids,
            actor=actor,
            _inject_failure_after_persistence=_inject_failure_after_persistence,
        )
    normalized_ids = tuple(
        sorted(
            {str(value).strip() for value in variant_ids if str(value).strip()},
            key=lambda value: value.encode("utf-8"),
        )
    )
    if not str(idempotency_key).strip() or not str(actor).strip() or not normalized_ids:
        raise MondayRecommendationError(
            "business run key, actor, and Variant IDs are required"
        )
    if conn.info.transaction_status.name != "IDLE":
        raise MondayRecommendationError("SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED")
    try:
        # Process policy is checked before the first database statement.  Full
        # database attestation occurs inside each fresh SERIALIZABLE snapshot.
        require_selected_mode_process_policy()
    except SyntheticSelectedOfferError as exc:
        raise MondayRecommendationError(str(exc)) from exc
    lock_names: tuple[str, ...] = ()
    lock_acquisition_started = False
    locks_acquired = False
    prepared: _PreparedRunReference | None = None
    operation_deadline = monotonic_time.monotonic() + SELECTED_OPERATION_SECONDS
    try:
        lock_acquisition_started = True
        lock_names = acquire_input_locks(
            conn,
            normalized_ids,
            global_lock_id=MONDAY_ANALYSIS_LOCK,
            operation_deadline=operation_deadline,
        )
        locks_acquired = True
        last_error: BaseException | None = None
        for attempt in range(3):
            try:
                attempt_result = _prepare_monday_run_impl(
                    conn,
                    business_date=business_date,
                    idempotency_key=idempotency_key,
                    variant_ids=normalized_ids,
                    actor=actor,
                    offer_resolution_contract=SYNTHETIC_SELECTED_OFFER_CONTRACT,
                    _inject_failure_after_persistence=_inject_failure_after_persistence,
                    _operation_deadline=operation_deadline,
                )
                if not isinstance(attempt_result, _PreparedRunReference):
                    raise MondayRecommendationError(
                        "selected preparation returned an invalid result reference"
                    )
                prepared = attempt_result
                break
            except Exception as exc:
                last_error = exc
                sqlstate = _exception_sqlstate(exc)
                if sqlstate in {"57014", "55P03"}:
                    conn.rollback()
                    raise MondayRecommendationError(
                        "MONDAY_PREPARATION_RETRY_REQUIRED"
                    ) from exc
                if sqlstate not in {"40001", "40P01"} or attempt == 2:
                    if sqlstate in {"40001", "40P01"}:
                        raise MondayRecommendationError(
                            "MONDAY_PREPARATION_RETRY_REQUIRED"
                        ) from exc
                    raise
                conn.rollback()
                if monotonic_time.monotonic() >= operation_deadline:
                    raise MondayRecommendationError(
                        "MONDAY_PREPARATION_RETRY_REQUIRED"
                    ) from exc
                remaining = max(0.0, operation_deadline - monotonic_time.monotonic())
                monotonic_time.sleep(min(0.01 * (attempt + 1), remaining))
        if prepared is None:
            raise MondayRecommendationError(
                "MONDAY_PREPARATION_RETRY_REQUIRED"
            ) from last_error
    except SyntheticSelectedOfferError as exc:
        raise MondayRecommendationError(str(exc)) from exc
    finally:
        if (
            lock_acquisition_started
            and not locks_acquired
            and not getattr(conn, "closed", False)
        ):
            conn.close()
        elif locks_acquired and not getattr(conn, "closed", False):
            try:
                release_input_locks(
                    conn, lock_names, global_lock_id=MONDAY_ANALYSIS_LOCK
                )
            except SyntheticSelectedOfferError as exc:
                raise MondayRecommendationError(str(exc)) from exc
    if prepared is None:
        raise MondayRecommendationError("MONDAY_PREPARATION_RETRY_REQUIRED")
    return get_monday_run(
        conn,
        prepared.run_id,
        idempotent_replay=prepared.idempotent_replay,
    )


def validate_monday_run_inputs(
    conn: Any, run_id: str, expected_fingerprint: str
) -> MondayRunInputValidation:
    """Return a typed, fail-closed validation result for a write path."""

    try:
        verify_monday_forecast_v2_retirement_contract(conn)
    except MondayForecastRetirementContractError:
        return MondayRunInputValidation(
            MondayRunInputValidationState.MATERIAL_INPUTS_CHANGED,
            str(run_id),
            None,
            None,
            None,
        )
    run = conn.execute(
        """SELECT business_date,model_version,procurement_output_mode,input_fingerprint,
                  started_at,procurement_input_manifest,status,workflow_stage
             FROM runs WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'""",
        (run_id,),
    ).fetchone()
    base = MondayRunInputValidation(
        MondayRunInputValidationState.MATERIAL_INPUTS_CHANGED,
        str(run_id),
        None if run is None else str(run[1]),
        None if run is None else str(run[7]),
        None if run is None else str(run[3]),
    )
    if run is None or run[2] != "INTERNAL_DRAFT_ONLY":
        return base
    if (
        run[1] == RETIRED_METHOD_VERSION
        and run[6] == "RUNNING"
        and run[7] in {"PREPARING", "AWAITING_REVIEW", "REVIEWED"}
    ):
        return MondayRunInputValidation(
            MondayRunInputValidationState.FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED,
            str(run_id),
            str(run[1]),
            str(run[7]),
            str(run[3]),
        )
    if run[3] != expected_fingerprint:
        return base
    try:
        manifest = json.loads(run[5])
        manifest_variant_ids = manifest["variant_ids"]
        variant_ids = tuple(str(value) for value in manifest_variant_ids)
        material_edit_policy = load_material_edit_policy().evidence()
    except (json.JSONDecodeError, KeyError, MondayControlError, TypeError, ValueError):
        return base
    if (
        not variant_ids
        or variant_ids != tuple(sorted(set(variant_ids)))
        or any(not value.strip() for value in variant_ids)
        or manifest.get("material_edit_policy") != material_edit_policy
    ):
        return base
    offer_resolution_contract = manifest.get("offer_resolution_contract")
    if offer_resolution_contract not in {None, SYNTHETIC_SELECTED_OFFER_CONTRACT}:
        return base
    development_forecast_contract = manifest.get("development_forecast_contract")
    if development_forecast_contract is None:
        expected_method_version = CURRENT_METHOD_VERSION
    else:
        try:
            expected_method_version = development_forecast_definition(
                str(development_forecast_contract)
            ).method_version
        except DevelopmentForecastError:
            return base
    if (
        run[1] != expected_method_version
        or manifest.get("method_version") != expected_method_version
        or (
            development_forecast_contract is None
            and METHOD_VERSION != CURRENT_METHOD_VERSION
        )
    ):
        return base
    frozen_contexts = manifest.get("contexts")
    if not isinstance(frozen_contexts, list):
        return base
    has_selected_fields = any(
        isinstance(context, dict) and "selected_offer_input_evidence" in context
        for context in frozen_contexts
    )
    if (offer_resolution_contract is None) == has_selected_fields:
        return base
    has_development_fields = any(
        isinstance(context, dict)
        and bool(
            {
                "development_forecast_contract",
                "development_forecast_status",
                "development_forecast_evidence",
                "development_forecast_evidence_sha256",
            }.intersection(context)
        )
        for context in frozen_contexts
    )
    if (development_forecast_contract is None) != (not has_development_fields):
        return base
    if offer_resolution_contract == SYNTHETIC_SELECTED_OFFER_CONTRACT:
        try:
            require_attested_selected_mode(conn)
            current_development_contract = development_forecast_contract_for_run(
                conn,
                offer_resolution_contract=offer_resolution_contract,
            )
        except (DevelopmentForecastError, SyntheticSelectedOfferError):
            return base
        if current_development_contract != development_forecast_contract:
            return base
    contexts = [
        _load_context(
            conn,
            business_date=run[0],
            variant_id=variant_id,
            evaluation_at=run[4],
            offer_resolution_contract=offer_resolution_contract,
            development_forecast_contract=development_forecast_contract,
        )
        for variant_id in variant_ids
    ]
    current_manifest = {
        "business_date": run[0],
        "variant_ids": variant_ids,
        "method_version": expected_method_version,
        "evaluation_at": run[4],
        "material_edit_policy": material_edit_policy,
        "contexts": contexts,
    }
    if offer_resolution_contract is not None:
        current_manifest["offer_resolution_contract"] = offer_resolution_contract
    if development_forecast_contract is not None:
        current_manifest["development_forecast_contract"] = (
            development_forecast_contract
        )
    current = _fingerprint(current_manifest)
    return MondayRunInputValidation(
        (
            MondayRunInputValidationState.MATCH
            if current == expected_fingerprint
            else MondayRunInputValidationState.MATERIAL_INPUTS_CHANGED
        ),
        str(run_id),
        str(run[1]),
        str(run[7]),
        str(run[3]),
    )


def monday_run_inputs_match(conn: Any, run_id: str, expected_fingerprint: str) -> bool:
    """Compatibility predicate for read-only/static consumers."""

    return validate_monday_run_inputs(conn, run_id, expected_fingerprint).matches


def _normalized_retirement_request(
    *, run_id: str, actor: str, reason: str
) -> tuple[UUID, str, str]:
    try:
        parsed_run_id = UUID(str(run_id))
    except (TypeError, ValueError) as exc:
        raise MondayRecommendationError("retirement run ID is malformed") from exc
    operator = str(actor).strip()
    note = str(reason).strip()
    if not operator or not note:
        raise MondayRecommendationError("retirement actor and reason are required")
    return parsed_run_id, operator, note


def _retirement_facts(conn: Any, run_id: UUID, *, for_update: bool) -> Any:
    suffix = " FOR UPDATE" if for_update else ""
    return conn.execute(
        """SELECT r.run_id,r.business_date,r.status,r.workflow_stage,
                  r.input_fingerprint,r.model_version,r.procurement_output_mode,
                  pg_catalog.to_jsonb(r),
                  (SELECT count(*)::integer FROM purchase_orders p
                    WHERE p.run_id=r.run_id),
                  (SELECT count(*)::integer FROM monday_run_artifacts a
                    WHERE a.run_id=r.run_id)
             FROM runs r
            WHERE r.run_id=%s AND r.run_type='MONDAY_PROCUREMENT'""" + suffix,
        (run_id,),
    ).fetchone()


def _validated_retirement_preview(
    conn: Any, *, run_id: UUID, actor: str, reason: str, for_update: bool
) -> dict[str, Any]:
    row = _retirement_facts(conn, run_id, for_update=for_update)
    if (
        row is None
        or row[2] != "RUNNING"
        or row[3] not in {"PREPARING", "AWAITING_REVIEW", "REVIEWED"}
        or row[5] != RETIRED_METHOD_VERSION
        or row[6] != "INTERNAL_DRAFT_ONLY"
        or int(row[8]) != 0
        or int(row[9]) != 0
    ):
        raise MondayRecommendationError(
            "only an active unbuilt EMERGENCY_TRANSPARENT_V1 run can be retired"
        )
    confirmation = conn.execute(
        """SELECT qa_mapping_test.
                  monday_stale_forecast_retirement_confirmation_sha256(
                      %s,%s,%s,%s,%s
                  )""",
        (run_id, row[4], row[3], actor, reason),
    ).fetchone()[0]
    after_run = dict(row[7])
    after_run["status"] = "FAILED"
    after_run["workflow_stage"] = "FAILED"
    return {
        "run_id": str(row[0]),
        "business_date": row[1],
        "input_fingerprint": row[4],
        "retired_model_version": row[5],
        "prior_status": row[2],
        "prior_workflow_stage": row[3],
        "target_status": "FAILED",
        "target_workflow_stage": "FAILED",
        "purchase_order_count": int(row[8]),
        "artifact_count": int(row[9]),
        "actor": actor,
        "reason": reason,
        "confirmation_sha256": str(confirmation),
        "before_run_json": row[7],
        "after_run_json": after_run,
        "release_performed": False,
        "shopify_calls": 0,
    }


def preview_monday_stale_forecast_retirement(
    conn: Any, *, run_id: str, actor: str, reason: str
) -> dict[str, Any]:
    """Preview an exact V1 retirement without assigning an effect."""

    parsed_run_id, operator, note = _normalized_retirement_request(
        run_id=run_id, actor=actor, reason=reason
    )
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        try:
            verify_monday_forecast_v2_retirement_contract(conn)
        except MondayForecastRetirementContractError as exc:
            raise MondayRecommendationError(str(exc)) from exc
        return _validated_retirement_preview(
            conn,
            run_id=parsed_run_id,
            actor=operator,
            reason=note,
            for_update=False,
        )


def _retirement_result(row: Any, *, replayed: bool) -> dict[str, Any]:
    return {
        "run_id": str(row[0]),
        "input_fingerprint": row[1],
        "retired_model_version": row[2],
        "prior_status": row[3],
        "prior_workflow_stage": row[4],
        "target_status": row[5],
        "target_workflow_stage": row[6],
        "actor": row[7],
        "reason": row[8],
        "confirmation_sha256": row[9],
        "transaction_id": int(row[10]),
        "created_at": row[11],
        "idempotent_replay": replayed,
        "business_date_released": True,
        "release_performed": False,
        "shopify_calls": 0,
    }


def confirm_monday_stale_forecast_retirement(
    conn: Any,
    *,
    run_id: str,
    actor: str,
    reason: str,
    expected_confirmation_sha256: str,
) -> dict[str, Any]:
    """Atomically retire one exact unbuilt V1 run and append its audit mirror."""

    parsed_run_id, operator, note = _normalized_retirement_request(
        run_id=run_id, actor=actor, reason=reason
    )
    if not isinstance(expected_confirmation_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_confirmation_sha256
    ):
        raise MondayRecommendationError("retirement confirmation hash is malformed")
    result: dict[str, Any]
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        try:
            verify_monday_forecast_v2_retirement_contract(conn)
        except MondayForecastRetirementContractError as exc:
            raise MondayRecommendationError(str(exc)) from exc
        if not conn.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (MONDAY_ANALYSIS_LOCK,)
        ).fetchone()[0]:
            raise MondayRecommendationError("Monday analysis lock is unavailable")
        locked = _retirement_facts(conn, parsed_run_id, for_update=True)
        if locked is None:
            raise MondayRecommendationError("retirement run is absent")
        existing = conn.execute(
            """SELECT run_id,input_fingerprint,retired_model_version,prior_status,
                      prior_workflow_stage,target_status,target_workflow_stage,
                      actor,reason,confirmation_sha256,transaction_id,created_at
                 FROM monday_stale_forecast_retirements WHERE run_id=%s""",
            (parsed_run_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing[7] != operator
                or existing[8] != note
                or existing[9] != expected_confirmation_sha256
            ):
                raise MondayRecommendationError(
                    "retirement request conflicts with the persisted event"
                )
            result = _retirement_result(existing, replayed=True)
        else:
            preview = _validated_retirement_preview(
                conn,
                run_id=parsed_run_id,
                actor=operator,
                reason=note,
                for_update=False,
            )
            if preview["confirmation_sha256"] != expected_confirmation_sha256:
                raise MondayRecommendationError(
                    "retirement preview changed; review and confirm again"
                )
            event = conn.execute(
                """INSERT INTO monday_stale_forecast_retirements(
                           run_id,input_fingerprint,retired_model_version,prior_status,
                           prior_workflow_stage,target_status,target_workflow_stage,
                           purchase_order_count,artifact_count,actor,reason,
                           confirmation_sha256,before_run_json,after_run_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s::jsonb,%s::jsonb)
                    RETURNING run_id,input_fingerprint,retired_model_version,prior_status,
                              prior_workflow_stage,target_status,target_workflow_stage,
                              actor,reason,confirmation_sha256,transaction_id,created_at""",
                (
                    parsed_run_id,
                    preview["input_fingerprint"],
                    preview["retired_model_version"],
                    preview["prior_status"],
                    preview["prior_workflow_stage"],
                    preview["target_status"],
                    preview["target_workflow_stage"],
                    operator,
                    note,
                    preview["confirmation_sha256"],
                    json.dumps(preview["before_run_json"], sort_keys=True),
                    json.dumps(preview["after_run_json"], sort_keys=True),
                ),
            ).fetchone()
            updated = conn.execute(
                """UPDATE runs SET status='FAILED',workflow_stage='FAILED'
                    WHERE run_id=%s AND status='RUNNING'
                      AND workflow_stage=%s AND model_version=%s
                    RETURNING run_id""",
                (
                    parsed_run_id,
                    preview["prior_workflow_stage"],
                    RETIRED_METHOD_VERSION,
                ),
            ).fetchall()
            if len(updated) != 1:
                raise MondayRecommendationError("retirement run changed before confirmation")
            envelope = {
                "confirmation_sha256": event[9],
                "contract": "BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1",
                "reason": event[8],
                "retirement_run_id": str(event[0]),
                "transaction_id": str(event[10]),
            }
            conn.execute(
                """INSERT INTO change_log(
                           table_name,row_key,action,before_json,after_json,actor,
                           run_id,occurred_at,evidence_json)
                    VALUES ('runs',%s,'UPDATE',%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb)""",
                (
                    str(event[0]),
                    json.dumps(preview["before_run_json"], sort_keys=True),
                    json.dumps(preview["after_run_json"], sort_keys=True),
                    operator,
                    parsed_run_id,
                    event[11],
                    json.dumps(envelope, sort_keys=True),
                ),
            )
            result = _retirement_result(event, replayed=False)
    return result


def get_monday_run(conn: Any, run_id: str, *, idempotent_replay: bool = False) -> dict[str, Any]:
    with conn.transaction():
        run = conn.execute(
            """SELECT run_id,business_date,status,workflow_stage,input_fingerprint,
                      exception_count,model_version,procurement_input_manifest
                 FROM runs WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'
                   AND procurement_output_mode='INTERNAL_DRAFT_ONLY'""",
            (run_id,),
        ).fetchone()
        if run is None:
            raise MondayRecommendationError("unknown Monday run")
        recommendations = [
            {
                "recommendation_id": int(row[0]),"variant_id": row[1],"vendor_id": str(row[2]),
                "offer_id": int(row[3]),"baseline_units": Decimal(row[4]),"recommended_cases": int(row[5]),
                "recommended_loose_units": int(row[6]),"recommended_units": int(row[7]),
                "unit_cost": Decimal(row[8]),"status": row[9],"metrics": row[10],
            }
            for row in conn.execute(
                """SELECT recommendation_id,variant_id,vendor_id,offer_id,baseline_units,
                          recommended_cases,recommended_loose_units,recommended_units,
                          recommended_unit_cost,recommendation_status,metrics
                     FROM procurement_recommendations WHERE run_id=%s ORDER BY vendor_id,variant_id""",
                (run_id,),
            ).fetchall()
        ]
        blockers = [
            {
                "exception_id": int(row[0]),"variant_id": row[1],
                "vendor_id": str(row[2]) if row[2] else None,"message": row[3],
                "excluded": row[4] is not None,
                "exclusion": (
                    {
                        "exclusion_id": int(row[4]),"action": row[5],
                        "actor": row[6],"reason": row[7],"created_at": row[8],
                        "input_fingerprint": row[9],
                    }
                    if row[4] is not None else None
                ),
            }
            for row in conn.execute(
                """SELECT e.exception_id,e.variant_id,e.vendor_id,e.message,
                          x.exclusion_id,x.action,x.actor,x.reason,x.created_at,
                          x.input_fingerprint
                     FROM exceptions e
                     LEFT JOIN monday_run_blocker_exclusions x
                       ON x.exception_id=e.exception_id AND x.run_id=e.run_id
                    WHERE e.run_id=%s AND e.status='OPEN'
                    ORDER BY e.exception_id""",
                (run_id,),
            ).fetchall()
        ]
    return {
        "run_id": str(run[0]),"business_date": run[1],"status": run[2],"workflow_stage": run[3],
        "input_fingerprint": run[4],"exception_count": int(run[5]),"model_version": run[6],
        "recommendations": recommendations,"blockers": blockers,"idempotent_replay": idempotent_replay,
        "safety_label": SAFETY_LABEL,
    } | (
        {"synthetic_selected_offer_inputs": True}
        if json.loads(run[7]).get("offer_resolution_contract")
        == SYNTHETIC_SELECTED_OFFER_CONTRACT
        else {}
    )


def list_monday_runs(conn: Any) -> list[dict[str, Any]]:
    """Read-only summary of emergency internal-DRAFT runs."""

    with conn.transaction():
        rows = conn.execute(
            """SELECT run_id,business_date,status,workflow_stage,input_fingerprint,
                      exception_count,started_at,model_version,
                      (procurement_input_manifest::jsonb
                         ->> 'offer_resolution_contract') = %s
                 FROM runs
                WHERE run_type='MONDAY_PROCUREMENT'
                  AND procurement_output_mode='INTERNAL_DRAFT_ONLY'
                ORDER BY started_at DESC,run_id"""
            , (SYNTHETIC_SELECTED_OFFER_CONTRACT,)
        ).fetchall()
    return [
        {
            "run_id": str(row[0]), "business_date": row[1], "status": row[2],
            "workflow_stage": row[3], "input_fingerprint": row[4],
            "exception_count": int(row[5]), "started_at": row[6],
            "model_version": row[7],
            "safety_label": SAFETY_LABEL,
        } | ({"synthetic_selected_offer_inputs": True} if row[8] else {})
        for row in rows
    ]
