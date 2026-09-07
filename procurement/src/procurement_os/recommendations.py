"""Frozen Monday analysis and persisted recommendation evidence."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
from typing import Any, Iterable
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .forecasting import DemandObservation, METHOD_VERSION, forecast_demand
from .monday_controls import MondayControlError, load_material_edit_policy
from .po_ledger import open_po_position
from .replenishment import calculate_baseline_need
from .strategic import PriceTier, evaluate_price_tiers
from .vendor_rules import VendorRuleValidationError, validate_vendor_rules_input


MONDAY_ANALYSIS_LOCK = 5_920_230_501
SAFETY_LABEL = "TEST DATA — NOT FOR ORDERING"


class MondayRecommendationError(ValueError):
    pass


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

    return conn.execute("SELECT transaction_timestamp()").fetchone()[0]


def _sales_coverage_digest(rows: Iterable[tuple[Any, ...]]) -> str:
    """Fingerprint exact source-level daily rows used by the Monday forecast."""

    return _fingerprint([list(row) for row in rows])


def _authoritative_sales_rows(
    conn: Any, *, business_date: date, variant_id: str
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    """Return one proven sales source or fail closed on current coverage.

    The canonical ShopifyQL aggregate is trustworthy only when its durable
    readiness evidence proves the complete requested period.  Disposable test
    databases may instead use an explicitly typed, per-variant synthetic daily
    manifest.  Other ad-hoc ``sales_daily`` sources never become buying input.
    """

    history_start = business_date - timedelta(days=84)
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

    synthetic_contract = (
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
    if synthetic_contract:
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
        canonical_contract = (
            gate is not None
            and gate[0] == "PASS"
            and blockers == []
            and canonical_run is not None
            and canonical_run[0] == "COMPLETED"
            and canonical_run[1] is not None
            and canonical_run[2] == canonical_start
            and canonical_start <= history_start
            and canonical_run[3] == history_end
            and evidence.get("end_date") == history_end.isoformat()
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
        expected_dates = [history_start + timedelta(days=offset) for offset in range(84)]
        if (
            not isinstance(coverage, dict)
            or len(rows) != 84
            or [row[0] for row in rows] != expected_dates
            or coverage.get("row_count") != 84
            or coverage.get("sha256") != _sales_coverage_digest(rows)
        ):
            raise MondayRecommendationError("CURRENT_SALES_COVERAGE_UNPROVEN")

    authority = {
        "contract": (
            evidence["coverage_contract"]
            if synthetic_contract
            else "CANONICAL_SHOPIFYQL_RUN_FACT_AGGREGATE_V1"
        ),
        "source": source,
        "history_start": history_start,
        "history_end": history_end,
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
    return list(rows), authority


def _load_context(
    conn: Any, *, business_date: date, variant_id: str, evaluation_at: datetime
) -> dict[str, Any]:
    context: dict[str, Any] = {"variant_id": str(variant_id), "blockers": []}
    variant = conn.execute(
        """SELECT variant_id,product_title,variant_title,sku,active,identity_scope,catalog_state
             FROM variants WHERE variant_id=%s""",
        (str(variant_id),),
    ).fetchone()
    if variant is None or not variant[4] or variant[5] != "CURRENT" or variant[6] != "LIVE":
        context["blockers"].append("VARIANT_NOT_CURRENT_ACTIVE_LIVE")
        return context
    context["variant"] = list(variant)

    offers = conn.execute(
        """SELECT offer_id,vendor_id::text,supplier_sku,package_type,size_text,raw_pack,
                  shopify_units_per_case,qualifying_units_per_case,assortment_scope,
                  assortment_group,assortable,confidence,source_file,source_page,
                  notes,valid_from,valid_to
             FROM supplier_offers
            WHERE variant_id=%s AND active AND package_type='STANDARD'
            ORDER BY offer_id""",
        (str(variant_id),),
    ).fetchall()
    context["offers"] = [list(row) for row in offers]
    if len(offers) != 1:
        context["blockers"].append("EXACTLY_ONE_ACTIVE_STANDARD_OFFER_REQUIRED")
        return context
    offer = offers[0]
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
        "confidence": offer[11], "source_file": offer[12], "source_page": offer[13],
        "notes": offer[14], "valid_from": offer[15], "valid_to": offer[16],
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
    local_evaluation = evaluation_at.astimezone(vendor_zone)
    order_weekday = business_date.strftime("%A").upper()
    if (
        local_evaluation.date() != business_date
        or order_weekday not in confirmed_rules.order_days
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
                  case_price,unit_price,source_file,source_page
             FROM v_verified_current_prices
            WHERE offer_id=%s ORDER BY CASE level_type WHEN 'BASE' THEN 0 ELSE 1 END,
                  break_unit,break_qty,price_id""",
        (context["offer_id"],),
    ).fetchall()
    context["prices"] = [list(row) for row in prices]
    if not prices or any(row[2] != _month_start(business_date) for row in prices):
        context["blockers"].append("VERIFIED_CURRENT_PRICE_FOR_RUN_MONTH_REQUIRED")
        return context
    if sum(1 for row in prices if row[3] == "BASE") != 1:
        context["blockers"].append("EXACTLY_ONE_CURRENT_BASE_PRICE_REQUIRED")
        return context

    history_start = business_date - timedelta(days=84)
    history_end = business_date - timedelta(days=1)
    try:
        sales_rows, sales_authority = _authoritative_sales_rows(
            conn, business_date=business_date, variant_id=str(variant_id)
        )
    except MondayRecommendationError:
        context["blockers"].append("CURRENT_SALES_COVERAGE_UNPROVEN")
        return context
    context["sales_authority"] = sales_authority
    context["sales_rows"] = [list(row) for row in sales_rows]
    inventory_history_rows = conn.execute(
        """SELECT d.snapshot_date,d.location_gid,d.available_quantity,d.incoming_quantity,
                  d.inventory_snapshot_run_id::text,r.source,r.source_hash,r.completed_at
             FROM daily_inventory_snapshots d
             JOIN inventory_snapshot_runs r
               ON r.inventory_snapshot_run_id=d.inventory_snapshot_run_id
              AND r.status='COMPLETED' AND r.business_date=d.snapshot_date
            WHERE d.variant_id=%s AND d.snapshot_date BETWEEN %s AND %s
              AND d.validation_status='VALID' AND d.available_quantity IS NOT NULL
            ORDER BY d.snapshot_date,d.location_gid,d.inventory_snapshot_run_id""",
        (str(variant_id), history_start, history_end),
    ).fetchall()
    context["historical_inventory_rows"] = [list(row) for row in inventory_history_rows]
    inventory_history: dict[date, Decimal] = {}
    for row in inventory_history_rows:
        inventory_history[row[0]] = inventory_history.get(row[0], Decimal("0")) + Decimal(row[2])
    sales_by_date = {row[0]: Decimal(row[1]) for row in sales_rows}
    observations: list[DemandObservation] = []
    for offset in range(84):
        day = history_start + timedelta(days=offset)
        units = sales_by_date.get(day, Decimal("0"))
        available_on_day = inventory_history.get(day)
        state = (
            "IN_STOCK"
            if units > 0 or (available_on_day is not None and available_on_day > 0)
            else "STOCKOUT"
            if available_on_day is not None
            else "UNKNOWN"
        )
        observations.append(DemandObservation(day, units, state))
    context["demand_observations"] = observations

    protection_days = int(vendor[2]) + int(vendor[3]) + int(
        Decimal(vendor[4]).to_integral_value(rounding=ROUND_CEILING)
    )
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
    return context


def prepare_monday_run(
    conn: Any,
    *,
    business_date: date,
    idempotency_key: str,
    variant_ids: Iterable[str],
    actor: str,
) -> dict[str, Any]:
    """Freeze source evidence and persist deterministic recommendations."""

    key = str(idempotency_key).strip()
    reviewer = str(actor).strip()
    normalized_ids = tuple(sorted({str(value).strip() for value in variant_ids if str(value).strip()}))
    if not key or not reviewer or not normalized_ids:
        raise MondayRecommendationError("business run key, actor, and Variant IDs are required")
    try:
        material_edit_policy = load_material_edit_policy().evidence()
    except MondayControlError as exc:
        raise MondayRecommendationError(str(exc)) from exc
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        if not conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (MONDAY_ANALYSIS_LOCK,)).fetchone()[0]:
            raise MondayRecommendationError("Monday analysis lock is unavailable")
        existing = conn.execute(
            """SELECT run_id,input_fingerprint,workflow_stage,procurement_output_mode,status,
                      started_at
                 FROM runs
                WHERE run_type='MONDAY_PROCUREMENT' AND idempotency_key=%s FOR UPDATE""",
            (key,),
        ).fetchone()
        if existing is not None and (
            existing[3] != "INTERNAL_DRAFT_ONLY" or existing[4] != "RUNNING"
        ):
            raise MondayRecommendationError("run key belongs to an incompatible workflow")
        active_same_day = conn.execute(
            """SELECT run_id,idempotency_key FROM runs
                WHERE run_type='MONDAY_PROCUREMENT' AND status='RUNNING'
                  AND business_date=%s
                ORDER BY run_id FOR UPDATE""",
            (business_date,),
        ).fetchone()
        if active_same_day is not None and (
            existing is None or active_same_day[0] != existing[0]
        ):
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
            )
            for variant_id in normalized_ids
        ]
        frozen_manifest = {
            "business_date": business_date,
            "variant_ids": normalized_ids,
            "method_version": METHOD_VERSION,
            "evaluation_at": evaluation_at,
            "material_edit_policy": material_edit_policy,
            "contexts": contexts,
        }
        input_manifest = _canonical_json(frozen_manifest)
        input_fingerprint = hashlib.sha256(input_manifest.encode()).hexdigest()
        if existing is not None:
            if existing[1] != input_fingerprint:
                raise MondayRecommendationError("run key already exists with different frozen inputs")
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
                business_date,evaluation_at,key,input_fingerprint,METHOD_VERSION,
                f"{SAFETY_LABEL}; prepared by {reviewer}",input_manifest,
            ),
        ).fetchone()[0]
        exception_count = 0
        for context in contexts:
            blockers = sorted(set(context["blockers"]))
            if blockers:
                exception_count += 1
                conn.execute(
                    """INSERT INTO exceptions(run_id,exception_type,severity,variant_id,vendor_id,
                               offer_id,supplier_sku,message,status)
                        VALUES (%s,'MONDAY_INPUT_BLOCKER','HIGH',%s,%s,%s,%s,%s,'OPEN')""",
                    (
                        run_id,context["variant_id"],context.get("vendor_id"),context.get("offer_id"),
                        context.get("supplier_sku"),", ".join(blockers),
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
            conn.execute(
                """INSERT INTO forecast_results(
                           run_id,variant_id,demand_regime,selected_model,forecast_units,
                           safety_stock_units,protection_days,baseline_replenishment_units,
                           calendar_velocity,in_stock_velocity,confidence,diagnostics,
                           input_fingerprint,method_version,history_start,history_end,horizon_days)
                    VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)""",
                (
                    run_id,context["variant_id"],"EMERGENCY_TRANSPARENT",forecast.method_version,
                    forecast.forecast_units,need.protection_days,need.raw_need_units,
                    forecast.calendar_velocity,forecast.in_stock_velocity,forecast.confidence,
                    json.dumps({"reason_codes": forecast.reason_codes, "outlier_capped_days": forecast.outlier_capped_days}),
                    input_fingerprint,forecast.method_version,forecast.history_start,forecast.history_end,
                    forecast.horizon_days,
                ),
            )
            for price in context["prices"]:
                conn.execute(
                    """INSERT INTO run_price_snapshots(
                               run_id,offer_id,price_state,effective_month,level_type,break_qty,
                               break_unit,case_price,unit_price,source_file,source_page)
                        VALUES (%s,%s,'current',%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (run_id,price[1],price[2],price[3],price[4],price[5],price[6],price[7],price[8],price[9]),
                )
            metrics = {
                "available_units": context["available_units"],
                "trusted_incoming_units": context["trusted_incoming_units"],
                "frozen_open_po_position": context["open_po_position"],
                "frozen_vendor_name": context["vendor_rules"][0],
                "target_units": need.target_units,
                "raw_need_units": need.raw_need_units,
                "pack_rounding_units": need.pack_rounding_units,
                "loose_fee": need.loose_fee,
                "forecast_daily_velocity": forecast.forecast_daily_velocity,
                "forecast_units": forecast.forecast_units,
                "forecast_horizon_days": forecast.horizon_days,
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
        conn.execute(
            "UPDATE runs SET workflow_stage='AWAITING_REVIEW',exception_count=%s WHERE run_id=%s",
            (exception_count,run_id),
        )
    return get_monday_run(conn, str(run_id), idempotent_replay=False)


def monday_run_inputs_match(conn: Any, run_id: str, expected_fingerprint: str) -> bool:
    """Recompute material inputs for an unbuilt run without persisting anything."""

    run = conn.execute(
        """SELECT business_date,model_version,procurement_output_mode,input_fingerprint,
                  started_at,procurement_input_manifest
             FROM runs WHERE run_id=%s AND run_type='MONDAY_PROCUREMENT'""",
        (run_id,),
    ).fetchone()
    if (
        run is None
        or run[1] != METHOD_VERSION
        or run[2] != "INTERNAL_DRAFT_ONLY"
        or run[3] != expected_fingerprint
    ):
        return False
    try:
        manifest = json.loads(run[5])
        manifest_variant_ids = manifest["variant_ids"]
        variant_ids = tuple(str(value) for value in manifest_variant_ids)
        material_edit_policy = load_material_edit_policy().evidence()
    except (json.JSONDecodeError, KeyError, MondayControlError, TypeError, ValueError):
        return False
    if (
        not variant_ids
        or variant_ids != tuple(sorted(set(variant_ids)))
        or any(not value.strip() for value in variant_ids)
        or manifest.get("material_edit_policy") != material_edit_policy
    ):
        return False
    contexts = [
        _load_context(
            conn, business_date=run[0], variant_id=variant_id, evaluation_at=run[4]
        )
        for variant_id in variant_ids
    ]
    current = _fingerprint(
        {
            "business_date": run[0],
            "variant_ids": variant_ids,
            "method_version": METHOD_VERSION,
            "evaluation_at": run[4],
            "material_edit_policy": material_edit_policy,
            "contexts": contexts,
        }
    )
    return current == expected_fingerprint


def get_monday_run(conn: Any, run_id: str, *, idempotent_replay: bool = False) -> dict[str, Any]:
    with conn.transaction():
        run = conn.execute(
            """SELECT run_id,business_date,status,workflow_stage,input_fingerprint,exception_count,model_version
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
    }


def list_monday_runs(conn: Any) -> list[dict[str, Any]]:
    """Read-only summary of emergency internal-DRAFT runs."""

    with conn.transaction():
        rows = conn.execute(
            """SELECT run_id,business_date,status,workflow_stage,input_fingerprint,
                      exception_count,started_at
                 FROM runs
                WHERE run_type='MONDAY_PROCUREMENT'
                  AND procurement_output_mode='INTERNAL_DRAFT_ONLY'
                ORDER BY started_at DESC,run_id"""
        ).fetchall()
    return [
        {
            "run_id": str(row[0]), "business_date": row[1], "status": row[2],
            "workflow_stage": row[3], "input_fingerprint": row[4],
            "exception_count": int(row[5]), "started_at": row[6],
            "safety_label": SAFETY_LABEL,
        }
        for row in rows
    ]
