"""Deterministic development forecast, classification, and protection tests."""

import copy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from procurement_os.development_forecast import (
    BASELINE_NEED_BINDING_CONTRACT,
    CONTRACT,
    DevelopmentForecastSchedule,
    V2_CONTRACT,
    _classify_confidence,
    _predict,
    _validate_v2_development_need_sources,
    assign_gp_dollar_abc,
    build_development_baseline_need_binding,
    calculate_anchored_schedule_horizon,
    calculate_calendar_protection_horizon,
    development_forecast_v2_registration,
    load_development_forecast_policy,
    load_development_forecast_schedule,
    plan_development_forecast,
    serialize_baseline_need,
    validate_anchored_schedule_evidence,
    validate_connected_development_forecast_context_evidence,
    validate_development_baseline_need_context,
    validate_development_forecast_context_evidence,
    validate_development_forecast_evidence,
)
from procurement_os.forecasting import DemandObservation
from procurement_os.forecasting import canonical_evidence_sha256
from procurement_os.replenishment import calculate_development_baseline_need


START = date(2026, 6, 1)


def observations(
    values: list[object], *, inventory_state: str = "UNKNOWN"
) -> list[DemandObservation]:
    return [
        DemandObservation(
            START + timedelta(days=index),
            Decimal(str(value)),
            inventory_state,
        )
        for index, value in enumerate(values)
    ]


def v2_observations(
    values: list[object], *, inventory_state: str = "IN_STOCK"
) -> list[DemandObservation]:
    start = date(2026, 5, 20)
    return [
        DemandObservation(
            start + timedelta(days=index),
            Decimal(str(value)),
            inventory_state,
        )
        for index, value in enumerate(values)
    ]


def abc_cohort(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "contract": "BUFFALO_DEVELOPMENT_ABC_COHORT_V2",
        "scope": {
            "scope_id": "synthetic-abc-cohort",
            "lookback_start": "2026-07-13",
            "lookback_end": "2026-10-04",
            "classification_period_days": 84,
        },
        "eligible_variant_ids": sorted(str(row["variant_id"]) for row in rows),
        "exclusions": [],
        "rows": rows,
    }


class DevelopmentForecastTests(unittest.TestCase):
    def test_constant_oracle_controls_need_and_case_count(self):
        evaluation_at = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
        southern_calendar = calculate_calendar_protection_horizon(
            evaluation_at=evaluation_at,
            timezone_name="America/New_York",
            order_days=("MONDAY",),
            order_cutoff_local=time(23, 59, 59),
            expected_delivery_days=("THURSDAY",),
            order_cycle_days=7,
            lead_time_days=1,
            lead_time_variability_days="0",
        )
        western_calendar = calculate_calendar_protection_horizon(
            evaluation_at=evaluation_at,
            timezone_name="America/New_York",
            order_days=("MONDAY", "WEDNESDAY"),
            order_cutoff_local=time(23, 59, 59),
            expected_delivery_days=("THURSDAY",),
            order_cycle_days=2,
            lead_time_days=1,
            lead_time_variability_days="0",
        )
        self.assertEqual(
            (
                southern_calendar["next_order_date"],
                southern_calendar["next_receipt_date"],
                southern_calendar["horizon_days"],
                western_calendar["next_order_date"],
                western_calendar["next_receipt_date"],
                western_calendar["horizon_days"],
            ),
            ("2026-10-12", "2026-10-15", 10, "2026-10-07", "2026-10-08", 3),
        )
        with self.assertRaisesRegex(
            ValueError, "nonzero lead-time variability requires"
        ):
            calculate_calendar_protection_horizon(
                evaluation_at=evaluation_at,
                timezone_name="America/New_York",
                order_days=("MONDAY",),
                order_cutoff_local=time(23, 59, 59),
                expected_delivery_days=("THURSDAY",),
                order_cycle_days=7,
                lead_time_days=1,
                lead_time_variability_days="1.2",
            )
        plan = plan_development_forecast(
            observations([2] * 84, inventory_state="IN_STOCK"),
            horizon_days=14,
        )
        self.assertEqual(plan.point_forecast_units, Decimal("28.0000"))
        self.assertEqual(plan.protection_units, Decimal("0.0000"))
        need = calculate_development_baseline_need(
            forecast_daily_velocity=plan.forecast_daily_velocity,
            point_forecast_units=plan.point_forecast_units,
            empirical_protection_units=plan.protection_units,
            available_units="8",
            trusted_incoming_units="2",
            order_cycle_days=7,
            lead_time_days=7,
            lead_time_variability_days="0",
            policy_mode="ROUTINE",
            units_per_case=6,
            loose_order_allowed=False,
            loose_unit_fee=None,
            forecast_horizon_days=14,
        )
        self.assertEqual(need.target_units, Decimal("28.0000"))
        self.assertEqual(need.raw_need_units, 18)
        self.assertEqual((need.cases, need.loose_units, need.ordered_units), (3, 0, 18))
        with self.assertRaisesRegex(
            ValueError, "nonzero lead-time variability requires"
        ):
            calculate_development_baseline_need(
                forecast_daily_velocity=plan.forecast_daily_velocity,
                point_forecast_units=plan.point_forecast_units,
                empirical_protection_units=plan.protection_units,
                available_units="8",
                trusted_incoming_units="2",
                order_cycle_days=7,
                lead_time_days=7,
                lead_time_variability_days="2.5",
                policy_mode="ROUTINE",
                units_per_case=6,
                loose_order_allowed=False,
                loose_unit_fee=None,
                forecast_horizon_days=14,
                protection_days_override=14,
            )
        with self.assertRaisesRegex(
            ValueError, "nonzero lead-time variability requires"
        ):
            calculate_development_baseline_need(
                forecast_daily_velocity=plan.forecast_daily_velocity,
                point_forecast_units=plan.point_forecast_units,
                empirical_protection_units=plan.protection_units,
                available_units="8",
                trusted_incoming_units="2",
                order_cycle_days=7,
                lead_time_days=7,
                lead_time_variability_days="2.5",
                policy_mode="ROUTINE",
                units_per_case=6,
                loose_order_allowed=False,
                loose_unit_fee=None,
                forecast_horizon_days=17,
            )
        covered_one_bottle = calculate_development_baseline_need(
            forecast_daily_velocity=plan.forecast_daily_velocity,
            point_forecast_units=plan.point_forecast_units,
            empirical_protection_units=plan.protection_units,
            available_units="1",
            trusted_incoming_units="0",
            order_cycle_days=7,
            lead_time_days=7,
            lead_time_variability_days="0",
            policy_mode="ONE_BOTTLE",
            units_per_case=6,
            loose_order_allowed=True,
            loose_unit_fee="3",
            forecast_horizon_days=14,
        )
        self.assertEqual(
            (covered_one_bottle.status, covered_one_bottle.target_units, covered_one_bottle.ordered_units),
            ("NO_ORDER_NEEDED", Decimal("0"), 0),
        )
        empty_one_bottle = calculate_development_baseline_need(
            forecast_daily_velocity=plan.forecast_daily_velocity,
            point_forecast_units=plan.point_forecast_units,
            empirical_protection_units=plan.protection_units,
            available_units="0",
            trusted_incoming_units="0",
            order_cycle_days=7,
            lead_time_days=7,
            lead_time_variability_days="0",
            policy_mode="ONE_BOTTLE",
            units_per_case=6,
            loose_order_allowed=True,
            loose_unit_fee="3",
            forecast_horizon_days=14,
        )
        self.assertEqual(
            (
                empty_one_bottle.status,
                empty_one_bottle.target_units,
                empty_one_bottle.ordered_units,
                empty_one_bottle.loose_fee,
            ),
            ("READY_FOR_REVIEW", Decimal("1"), 1, Decimal("3")),
        )

    def test_changed_history_changes_the_calculated_purchase_quantity(self):
        needs = []
        for daily_units in (1, 2):
            plan = plan_development_forecast(
                observations([daily_units] * 84),
                horizon_days=14,
            )
            needs.append(
                calculate_development_baseline_need(
                    forecast_daily_velocity=plan.forecast_daily_velocity,
                    point_forecast_units=plan.point_forecast_units,
                    empirical_protection_units=plan.protection_units,
                    available_units="8",
                    trusted_incoming_units="2",
                    order_cycle_days=7,
                    lead_time_days=7,
                    lead_time_variability_days="0",
                    policy_mode="ROUTINE",
                    units_per_case=6,
                    loose_order_allowed=False,
                    loose_unit_fee=None,
                    forecast_horizon_days=14,
                )
            )
        self.assertEqual(
            [(need.target_units, need.raw_need_units, need.cases) for need in needs],
            [
                (Decimal("14.0000"), 4, 1),
                (Decimal("28.0000"), 18, 3),
            ],
        )

    def test_weekly_seasonality_beats_naive_without_timing_leakage(self):
        values = [7 if index % 7 in {4, 5} else 0 for index in range(84)]
        plan = plan_development_forecast(observations(values), horizon_days=6)
        self.assertEqual(plan.selected_model, "SEASONAL_NAIVE")
        self.assertEqual(plan.point_forecast_units, Decimal("14.0000"))
        self.assertLess(
            Decimal(plan.evidence["selection_metrics"]["wape"]),
            Decimal(
                next(
                    item["selection_metrics"]["wape"]
                    for item in plan.evidence["candidates"]
                    if item["name"] == "NAIVE"
                )
            ),
        )
        self.assertEqual(plan.evidence["evaluation_metrics"]["wape"], "0.000000")
        self.assertEqual(
            {item["forecast_horizon_days"] for item in plan.evidence["candidates"]},
            {6},
        )
        self.assertEqual(
            {
                item["selection_metrics"]["observation_count"]
                for item in plan.evidence["candidates"]
                if item["status"] == "ELIGIBLE"
            },
            {16},
        )
        eligible_origin_sets = {
            tuple(item["selection_origin_dates"])
            for item in plan.evidence["candidates"]
            if item["status"] == "ELIGIBLE"
        }
        self.assertEqual(len(eligible_origin_sets), 1)
        self.assertEqual(len(next(iter(eligible_origin_sets))), 16)

        same_mean_flat = [Decimal("2")] * 84
        self.assertEqual(sum(values), sum(same_mean_flat))
        flat = plan_development_forecast(
            observations(same_mean_flat), horizon_days=6
        )
        self.assertEqual(flat.selected_model, "NAIVE")
        self.assertNotEqual(flat.selected_model, plan.selected_model)

        perturbed = values[:70] + [20] * 14
        later = plan_development_forecast(observations(perturbed), horizon_days=6)
        self.assertEqual(plan.evidence["candidates"], later.evidence["candidates"])
        self.assertEqual(plan.evidence["protection"], later.evidence["protection"])
        self.assertNotEqual(plan.point_forecast_units, later.point_forecast_units)

        trend = plan_development_forecast(
            observations(
                [Decimal(84 - index) / Decimal("10") for index in range(84)]
            ),
            horizon_days=3,
        )
        self.assertEqual(trend.selected_model, "DAMPED_ETS")
        category = next(
            item
            for item in trend.evidence["candidates"]
            if item["name"] == "CATEGORY_SHRINKAGE"
        )
        self.assertEqual(category["status"], "INAPPLICABLE")
        self.assertEqual(category["reason_codes"], ["CATEGORY_PRIOR_NOT_FROZEN"])
        self.assertFalse(category["selected"])
        self.assertEqual(
            trend.evidence["category_prior"],
            {
                "status": "NOT_CONFIGURED",
                "reason_code": "CATEGORY_PRIOR_NOT_FROZEN",
            },
        )

        boundary_values: list[Decimal] = []
        for week in range(12):
            weekly_total = (
                Decimal("0.4999996")
                if week % 2 == 0
                else Decimal("1.5000004")
            )
            boundary_values.extend([weekly_total, *([Decimal("0")] * 6)])
        boundary_observations = observations(boundary_values)
        boundary = plan_development_forecast(
            boundary_observations, horizon_days=3
        )
        self.assertEqual(
            (
                boundary.evidence["xyz_coefficient_of_variation"],
                boundary.evidence["xyz_class"],
            ),
            ("0.500000", "X"),
        )
        self.assertTrue(
            validate_development_forecast_evidence(boundary.to_json_dict())
        )
        self.assertTrue(
            validate_development_forecast_context_evidence(
                boundary.to_json_dict(),
                [
                    {
                        "business_date": item.business_date.isoformat(),
                        "net_units": str(item.net_units),
                        "inventory_state": item.inventory_state,
                    }
                    for item in boundary_observations
                ],
            )
        )
        tiny_observations = observations([Decimal("0.0000001")] * 84)
        tiny = plan_development_forecast(tiny_observations, horizon_days=3)
        self.assertEqual(tiny.evidence["calendar_velocity"], "0.000000")
        self.assertEqual(
            (
                tiny.evidence["xyz_coefficient_of_variation"],
                tiny.evidence["xyz_class"],
            ),
            ("0.000000", "X"),
        )
        self.assertTrue(validate_development_forecast_evidence(tiny.to_json_dict()))

    def test_intermittent_zero_and_thin_cases_are_explicit(self):
        intermittent = plan_development_forecast(
            observations([5 if index % 10 == 0 else 0 for index in range(84)]),
            horizon_days=3,
        )
        self.assertEqual(intermittent.evidence["demand_regime"], "INTERMITTENT")
        tsb = next(
            item for item in intermittent.evidence["candidates"] if item["name"] == "TSB"
        )
        self.assertEqual(tsb["status"], "ELIGIBLE")

        zero = plan_development_forecast(observations([0] * 84), horizon_days=3)
        self.assertEqual((zero.point_forecast_units, zero.protection_units), (Decimal("0"), Decimal("0")))
        self.assertEqual(zero.evidence["xyz_class"], "Z")
        self.assertIsNone(zero.evidence["selection_metrics"]["wape"])
        self.assertIsNone(zero.evidence["selection_metrics"]["mase"])

        thin = plan_development_forecast(
            observations([1] * 40), horizon_days=3
        )
        self.assertEqual(thin.status, "BLOCKED")
        self.assertIsNone(thin.evidence["protection_units"])
        self.assertIn("INSUFFICIENT_CHRONOLOGICAL_WINDOWS", thin.evidence["reason_codes"])

    def test_returns_stockouts_and_unknown_availability_never_invent_state(self):
        partitioned = plan_development_forecast(
            observations([2] * 28, inventory_state="IN_STOCK")
            + [
                DemandObservation(
                    START + timedelta(days=index), Decimal("2"), "UNKNOWN"
                )
                for index in range(28, 84)
            ],
            horizon_days=3,
        )
        self.assertEqual(partitioned.evidence["availability"]["proven_in_stock_days"], 28)
        self.assertEqual(partitioned.evidence["availability"]["unknown_days"], 56)
        self.assertEqual(
            partitioned.evidence["protection"]["status"],
            "CALCULATED_LIMITED_AVAILABILITY",
        )
        self.assertEqual(partitioned.confidence, "LOW")

        series = observations([2] * 84)
        series[10] = DemandObservation(series[10].business_date, Decimal("-8"), "UNKNOWN")
        series[20] = DemandObservation(series[20].business_date, Decimal("0"), "STOCKOUT")
        plan = plan_development_forecast(series, horizon_days=14)
        self.assertEqual(plan.status, "READY")
        self.assertEqual(plan.evidence["availability"]["negative_net_days"], 1)
        self.assertEqual(plan.evidence["availability"]["proven_stockout_days"], 1)
        self.assertEqual(plan.evidence["availability"]["unknown_days"], 83)
        self.assertIn("NEGATIVE_NET_DAYS_FLOORED_FOR_DEMAND_ONLY", plan.evidence["reason_codes"])
        self.assertIn("PROVEN_STOCKOUT_DAYS_CAUSALLY_IMPUTED", plan.evidence["reason_codes"])
        self.assertIn("UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK", plan.evidence["reason_codes"])
        self.assertEqual(plan.evidence["protection"]["censored_stockout_origin_count"], 0)
        self.assertEqual(
            plan.evidence["protection"]["status"],
            "CALCULATED_LIMITED_AVAILABILITY",
        )
        self.assertEqual(
            plan.evidence["availability"]["protection_qualification"],
            "LIMITED",
        )
        self.assertEqual(plan.evidence["evaluation_status"], "INSUFFICIENT_FOR_CONFIDENCE")
        self.assertEqual(plan.confidence, "LOW")
        self.assertIn(
            "EVALUATION_ORIGINS_INSUFFICIENT_FOR_CONFIDENCE",
            plan.evidence["reason_codes"],
        )

        unavailable = observations([2] * 84)
        unavailable[49] = DemandObservation(
            unavailable[49].business_date, Decimal("0"), "STOCKOUT"
        )
        blocked = plan_development_forecast(unavailable, horizon_days=14)
        self.assertEqual(blocked.status, "BLOCKED")
        self.assertEqual(
            blocked.evidence["protection"]["censored_stockout_origin_count"], 1
        )
        self.assertIn(
            "EMPIRICAL_PROTECTION_ORIGINS_INSUFFICIENT",
            blocked.evidence["reason_codes"],
        )

    def test_model_failure_and_metric_ties_fall_back_deterministically(self):
        original = _predict

        def fail_ets(name, *args, **kwargs):
            if name == "DAMPED_ETS":
                raise ArithmeticError("injected candidate failure")
            return original(name, *args, **kwargs)

        with patch("procurement_os.development_forecast._predict", side_effect=fail_ets):
            plan = plan_development_forecast(observations([2] * 84), horizon_days=14)
        self.assertEqual(plan.selected_model, "NAIVE")
        failed = next(
            item for item in plan.evidence["candidates"] if item["name"] == "DAMPED_ETS"
        )
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["reason_codes"], ["MODEL_EXECUTION_FAILED:ArithmeticError"])

    def test_protection_uses_full_horizon_shortfalls_including_zero(self):
        values = [2 if index % 3 in {0, 1} else 0 for index in range(84)]
        plan = plan_development_forecast(observations(values), horizon_days=3)
        protection = plan.evidence["protection"]
        self.assertEqual(protection["method"], "FULL_HORIZON_SHORTFALL_EMPIRICAL_QUANTILE")
        self.assertGreaterEqual(protection["full_horizon_origin_count"], 8)
        self.assertIn("0.0000", protection["shortfalls"])
        self.assertEqual(plan.target_units, Decimal("4.0000"))
        self.assertEqual(
            plan.target_units,
            plan.point_forecast_units + plan.protection_units,
        )

        cap_series = observations([1] * 42 + [2] * 42)
        with patch(
            "procurement_os.development_forecast._predict",
            side_effect=lambda _name, _history, horizon, _policy, _prior: [
                Decimal("100")
            ]
            * horizon,
        ):
            capped = plan_development_forecast(cap_series, horizon_days=10)
        self.assertEqual(capped.status, "READY")
        self.assertGreater(capped.evidence["caps"]["calibration_cap_hits"], 0)
        self.assertGreater(capped.protection_units, 0)

    def test_gp_dollar_abc_never_substitutes_revenue_or_current_price(self):
        policy = load_development_forecast_policy()
        self.assertEqual(policy.values["abc"]["classification_period_days"], 84)
        self.assertEqual(policy.values["history"]["connected_history_days"], 84)
        self.assertEqual(policy.values["history"]["maximum_history_days"], 84)
        self.assertEqual(policy.values["selection_metric"], "WAPE_WHEN_DEFINED_ELSE_MAE")
        self.assertEqual(policy.values["tie_break_rule"], "CANDIDATE_ORDER")
        incomplete = assign_gp_dollar_abc(
            abc_cohort(
                [
                {"variant_id": "a", "historical_revenue": "100", "historical_cogs": "10"},
                {"variant_id": "b", "historical_revenue": "500", "historical_cogs": "490"},
                {"variant_id": "c", "historical_revenue": "50", "historical_cogs": None},
                ]
            )
        )
        self.assertEqual(incomplete["cohort_status"], "INCOMPLETE")
        self.assertEqual(incomplete["classification_status"], "NOT_CONFIGURED")
        self.assertEqual(
            {row["abc_class"] for row in incomplete["members"]},
            {"NOT_CONFIGURED"},
        )
        by_id = {row["variant_id"]: row for row in incomplete["members"]}
        self.assertEqual(by_id["a"]["gross_profit_dollars"], "90.00")
        self.assertEqual(by_id["a"]["status"], "COHORT_INCOMPLETE_KNOWN_GP_DIAGNOSTIC")
        self.assertEqual(by_id["c"]["status"], "INVALID_HISTORICAL_COST_EVIDENCE")

        complete = assign_gp_dollar_abc(
            abc_cohort(
                [
                    {"variant_id": "a", "historical_revenue": "100", "historical_cogs": "10"},
                    {"variant_id": "b", "historical_revenue": "500", "historical_cogs": "490"},
                    {"variant_id": "c", "historical_revenue": "50", "historical_cogs": "45"},
                ]
            )
        )
        complete_by_id = {row["variant_id"]: row for row in complete["members"]}
        self.assertEqual(complete["classification_status"], "CALCULATED")
        self.assertEqual(complete_by_id["a"]["abc_class"], "A")
        self.assertNotEqual(complete_by_id["b"]["abc_class"], "A")
        nonpositive = assign_gp_dollar_abc(
            abc_cohort([
                {"variant_id": "x", "historical_revenue": "10", "historical_cogs": "10"},
                {"variant_id": "y", "historical_revenue": "5", "historical_cogs": "7"},
            ])
        )
        self.assertEqual(
            {row["abc_class"] for row in nonpositive["members"]},
            {"NOT_CONFIGURED"},
        )
        self.assertEqual(
            {row["status"] for row in nonpositive["members"]},
            {"NONPOSITIVE_HISTORICAL_GROSS_PROFIT"},
        )

    def test_v2_registry_schedule_and_policy_are_exact_and_anchored(self):
        policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        schedule = load_development_forecast_schedule()
        registration = development_forecast_v2_registration()
        self.assertEqual(policy.values["history"], {
            "connected_history_days": 138,
            "maximum_history_days": 138,
        })
        self.assertEqual(
            policy.values["windows"],
            {
                "minimum_training_days": 28,
                "selection_origin_days": 38,
                "calibration_days": 38,
                "evaluation_days": 34,
                "minimum_selection_origins": 8,
                "minimum_evaluation_origins": 4,
            },
        )
        self.assertEqual(registration["profile"], "development-forecast-v2")
        self.assertEqual(registration["policy_source_sha256"], policy.source_sha256)
        self.assertEqual(registration["schedule_source_sha256"], schedule.source_sha256)
        evaluation = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
        common = {
            "timezone_name": "America/New_York",
            "order_cutoff_local": time(17, 0),
            "expected_delivery_days": ["THURSDAY"],
            "lead_time_days": 1,
            "lead_time_variability_days": "0",
        }
        southern = calculate_anchored_schedule_horizon(
            evaluation_at=evaluation,
            business_date=date(2026, 10, 5),
            vendor_id="00000000-0000-4000-8000-000000000001",
            vendor_rules={**common, "order_days": ["MONDAY"], "order_cycle_days": 14},
            schedule=schedule,
        )
        western = calculate_anchored_schedule_horizon(
            evaluation_at=evaluation,
            business_date=date(2026, 10, 5),
            vendor_id="00000000-0000-4000-8000-000000000003",
            vendor_rules={
                **common,
                "order_days": ["MONDAY", "WEDNESDAY"],
                "order_cycle_days": 2,
            },
            schedule=schedule,
        )
        self.assertEqual(
            (
                southern["current_order_receipt_date"],
                southern["next_submission_date"],
                southern["next_order_receipt_date"],
                southern["horizon_days"],
                western["current_order_receipt_date"],
                western["next_submission_date"],
                western["next_order_receipt_date"],
                western["horizon_days"],
            ),
            (
                "2026-10-08", "2026-10-19", "2026-10-22", 17,
                "2026-10-08", "2026-10-07", "2026-10-08", 3,
            ),
        )
        self.assertTrue(validate_anchored_schedule_evidence(southern))
        self.assertTrue(validate_anchored_schedule_evidence(western))
        with self.assertRaisesRegex(ValueError, "off the registered schedule"):
            calculate_anchored_schedule_horizon(
                evaluation_at=evaluation + timedelta(days=7),
                business_date=date(2026, 10, 12),
                vendor_id="00000000-0000-4000-8000-000000000001",
                vendor_rules={**common, "order_days": ["MONDAY"], "order_cycle_days": 14},
                schedule=schedule,
            )
        with self.assertRaisesRegex(ValueError, "open registered schedule"):
            calculate_anchored_schedule_horizon(
                evaluation_at=datetime(2026, 10, 5, 21, tzinfo=timezone.utc),
                business_date=date(2026, 10, 5),
                vendor_id="00000000-0000-4000-8000-000000000001",
                vendor_rules={**common, "order_days": ["MONDAY"], "order_cycle_days": 14},
                schedule=schedule,
            )

        exception_values = copy.deepcopy(schedule.values)
        southern_schedule = exception_values["vendors"][0]
        southern_schedule["submission_opportunities"]["removed_dates"] = [
            "2026-10-19"
        ]
        southern_schedule["submission_opportunities"]["added_dates"] = [
            "2026-10-20"
        ]
        exception_schedule = DevelopmentForecastSchedule(
            values=exception_values,
            source_sha256="f" * 64,
            canonical_sha256="e" * 64,
            source_file="fabricated-exception-test.json",
        )
        shifted_submission = calculate_anchored_schedule_horizon(
            evaluation_at=evaluation,
            business_date=date(2026, 10, 5),
            vendor_id="00000000-0000-4000-8000-000000000001",
            vendor_rules={**common, "order_days": ["MONDAY"], "order_cycle_days": 14},
            schedule=exception_schedule,
        )
        self.assertEqual(shifted_submission["next_submission_date"], "2026-10-20")
        self.assertEqual(
            shifted_submission["vendor_rules_projection"]["order_cycle_days"],
            14,
        )

        added_values = copy.deepcopy(schedule.values)
        added_southern = added_values["vendors"][0]
        for opportunity in ("review_opportunities", "submission_opportunities"):
            added_southern[opportunity]["added_dates"] = ["2026-10-06"]
        added_schedule = DevelopmentForecastSchedule(
            values=added_values,
            source_sha256="d" * 64,
            canonical_sha256="c" * 64,
            source_file="fabricated-added-current-test.json",
        )
        added_current = calculate_anchored_schedule_horizon(
            evaluation_at=evaluation + timedelta(days=1),
            business_date=date(2026, 10, 6),
            vendor_id="00000000-0000-4000-8000-000000000001",
            vendor_rules={**common, "order_days": ["MONDAY"], "order_cycle_days": 14},
            schedule=added_schedule,
        )
        self.assertEqual(added_current["current_submission_date"], "2026-10-06")
        self.assertEqual(added_current["horizon_days"], 16)

    def test_v2_h1_through_h31_have_policy_adequate_origin_evidence(self):
        policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        patterns = {
            "constant": [2] * 138,
            "variable": [1 + index % 5 for index in range(138)],
            "intermittent": [5 if index % 10 == 0 else 0 for index in range(138)],
        }
        for pattern, values in patterns.items():
            for horizon in range(1, 32):
                with self.subTest(pattern=pattern, horizon=horizon):
                    plan = plan_development_forecast(
                        v2_observations(values),
                        horizon_days=horizon,
                        policy=policy,
                    )
                    self.assertEqual(plan.status, "READY")
                    self.assertTrue(validate_development_forecast_evidence(plan.to_json_dict()))
                    windows = plan.evidence["origin_windows"]
                    self.assertEqual(
                        windows["selection"]["planned_origin_count"], 38 - horizon + 1
                    )
                    self.assertEqual(
                        windows["calibration"]["planned_origin_count"], 38 - horizon + 1
                    )
                    self.assertEqual(
                        windows["evaluation"]["planned_origin_count"], 34 - horizon + 1
                    )
                    self.assertGreaterEqual(
                        windows["selection"]["usable_origin_count"], 8
                    )
                    self.assertGreaterEqual(
                        windows["calibration"]["usable_origin_count"], 8
                    )
                    self.assertGreaterEqual(
                        windows["evaluation"]["usable_origin_count"], 4
                    )

    def test_v2_history_and_stockout_evidence_fail_closed_without_lowering_minima(self):
        policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        for count in (137, 139):
            with self.subTest(count=count):
                with self.assertRaisesRegex(
                    ValueError, "exactly 138 days|policy cap"
                ):
                    plan_development_forecast(
                        v2_observations([2] * count), horizon_days=17, policy=policy
                    )
        missing = v2_observations([2] * 138)
        del missing[80]
        with self.assertRaisesRegex(ValueError, "calendar complete"):
            plan_development_forecast(missing, horizon_days=17, policy=policy)
        censored = v2_observations([2] * 138)
        for index in range(66, 90):
            censored[index] = DemandObservation(
                censored[index].business_date, Decimal("0"), "STOCKOUT"
            )
        blocked = plan_development_forecast(censored, horizon_days=17, policy=policy)
        self.assertEqual(blocked.status, "BLOCKED")
        self.assertIn(
            "EMPIRICAL_PROTECTION_ORIGINS_INSUFFICIENT",
            blocked.reason_codes,
        )
        self.assertTrue(validate_development_forecast_evidence(blocked.to_json_dict()))
        self.assertEqual(
            blocked.evidence["origin_windows"]["calibration"]["usable_origin_count"],
            0,
        )

        selection_censored = v2_observations([2] * 138)
        for index in range(28, 66):
            selection_censored[index] = DemandObservation(
                selection_censored[index].business_date, Decimal("0"), "STOCKOUT"
            )
        no_selection = plan_development_forecast(
            selection_censored, horizon_days=17, policy=policy
        )
        self.assertEqual(no_selection.status, "BLOCKED")
        self.assertIn("SIMPLE_BASELINE_UNAVAILABLE", no_selection.reason_codes)
        self.assertEqual(
            no_selection.evidence["origin_windows"]["selection"][
                "usable_origin_count"
            ],
            0,
        )
        self.assertTrue(
            validate_development_forecast_evidence(no_selection.to_json_dict())
        )
        self.assertFalse(
            validate_connected_development_forecast_context_evidence(
                no_selection.to_json_dict(),
                [
                    {
                        "business_date": item.business_date.isoformat(),
                        "net_units": str(item.net_units),
                        "inventory_state": item.inventory_state,
                    }
                    for item in selection_censored
                ],
                expected_contract=V2_CONTRACT,
            )
        )

        evaluation_censored = v2_observations([2] * 138)
        for index in range(104, 138):
            evaluation_censored[index] = DemandObservation(
                evaluation_censored[index].business_date, Decimal("0"), "STOCKOUT"
            )
        no_evaluation = plan_development_forecast(
            evaluation_censored, horizon_days=17, policy=policy
        )
        self.assertEqual(no_evaluation.status, "BLOCKED")
        self.assertIn("EVALUATION_ORIGINS_UNAVAILABLE", no_evaluation.reason_codes)
        self.assertTrue(
            validate_development_forecast_evidence(no_evaluation.to_json_dict())
        )

        thin_evaluation = v2_observations([2] * 138)
        thin_evaluation[120] = DemandObservation(
            thin_evaluation[120].business_date, Decimal("0"), "STOCKOUT"
        )
        insufficient_evaluation = plan_development_forecast(
            thin_evaluation, horizon_days=17, policy=policy
        )
        self.assertEqual(insufficient_evaluation.status, "BLOCKED")
        self.assertEqual(insufficient_evaluation.evidence["evaluation_origin_count"], 1)
        self.assertEqual(
            insufficient_evaluation.evidence["evaluation_status"],
            "INSUFFICIENT_FOR_READINESS",
        )
        self.assertIn(
            "EVALUATION_ORIGINS_INSUFFICIENT_FOR_READINESS",
            insufficient_evaluation.reason_codes,
        )
        self.assertTrue(
            validate_development_forecast_evidence(
                insufficient_evaluation.to_json_dict()
            )
        )
        with self.assertRaisesRegex(ValueError, "exceeds the development bound"):
            plan_development_forecast(
                v2_observations([2] * 138), horizon_days=32, policy=policy
            )

    def test_v2_confidence_and_need_binding_are_policy_and_context_bound(self):
        policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        self.assertEqual(
            _classify_confidence(
                evaluation_status="SUFFICIENT",
                availability_limited=False,
                evaluation_wape=Decimal("0.20"),
                policy=policy,
            ),
            "HIGH",
        )
        self.assertEqual(
            _classify_confidence(
                evaluation_status="SUFFICIENT",
                availability_limited=False,
                evaluation_wape=Decimal("0.50"),
                policy=policy,
            ),
            "MEDIUM",
        )
        calendar = calculate_anchored_schedule_horizon(
            evaluation_at=datetime(2026, 10, 5, 14, tzinfo=timezone.utc),
            business_date=date(2026, 10, 5),
            vendor_id="00000000-0000-4000-8000-000000000001",
            vendor_rules={
                "timezone_name": "America/New_York",
                "order_days": ["MONDAY"],
                "order_cutoff_local": time(17, 0),
                "expected_delivery_days": ["THURSDAY"],
                "order_cycle_days": 14,
                "lead_time_days": 1,
                "lead_time_variability_days": "0",
            },
        )
        source = v2_observations([2] * 138)
        plan = plan_development_forecast(
            source, horizon_days=17, protection_calendar=calendar, policy=policy
        )
        shifted = [
            DemandObservation(
                item.business_date - timedelta(days=31),
                item.net_units,
                item.inventory_state,
            )
            for item in source
        ]
        with self.assertRaisesRegex(ValueError, "not adjacent"):
            plan_development_forecast(
                shifted,
                horizon_days=17,
                protection_calendar=calendar,
                policy=policy,
            )
        need = calculate_development_baseline_need(
            forecast_daily_velocity=plan.forecast_daily_velocity,
            point_forecast_units=plan.point_forecast_units,
            empirical_protection_units=plan.protection_units,
            forecast_horizon_days=17,
            available_units="0",
            trusted_incoming_units="0",
            order_cycle_days=14,
            lead_time_days=1,
            lead_time_variability_days="0",
            policy_mode="ROUTINE",
            units_per_case=6,
            loose_order_allowed=False,
            loose_unit_fee=None,
            protection_days_override=17,
        )
        vendor = [None] * 22
        vendor[0:13] = [
            "Synthetic Southern", True, 14, 1, Decimal("0"), "DOLLAR",
            Decimal("0"), Decimal("0"), False, None,
            "FABRICATED", "synthetic:owner", 1,
        ]
        vendor[13:22] = [
            ["MONDAY"], time(17, 0), "America/New_York", ["THURSDAY"],
            Decimal("1"), None, None, datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 1, tzinfo=timezone.utc),
        ]
        context = {
            "variant_id": "1001",
            "development_forecast_contract": V2_CONTRACT,
            "development_forecast_status": "READY",
            "development_forecast_evidence": plan.to_json_dict(),
            "development_forecast_evidence_sha256": plan.evidence_sha256,
            "demand_observations": [
                {
                    "business_date": item.business_date.isoformat(),
                    "net_units": str(item.net_units),
                    "inventory_state": item.inventory_state,
                }
                for item in source
            ],
            "available_units": Decimal("0"),
            "trusted_incoming_units": Decimal("0"),
            "inventory_capture": [
                "00000000-0000-4000-8000-000000000099",
                date(2026, 10, 5),
                datetime(2026, 10, 5, 12, tzinfo=timezone.utc),
                "SYNTHETIC_DEMO",
                "a" * 64,
                1,
                1,
                0,
                0,
            ],
            "inventory_rows": [
                [
                    "location",
                    Decimal("0"),
                    Decimal("0"),
                    "VALID",
                    "00000000-0000-4000-8000-000000000099",
                ]
            ],
            "open_po_position": {
                "variant_id": "1001",
                "vendor_id": "00000000-0000-4000-8000-000000000001",
                "trusted_incoming_units": Decimal("0"),
                "open_line_count": 0,
                "blocks_reorder": False,
                "trusted_sources": [],
                "blockers": [],
            },
            "policy_mode": "ROUTINE",
            "policies": [[1, {"mode": "ROUTINE"}, "synthetic:owner", ""]],
            "units_per_case": 6,
            "qualifying_units_per_case": 6,
            "offer_id": 1,
            "offer_evidence": {
                "supplier_sku": "SUP-001",
                "package_type": "STANDARD",
                "size_text": "750ML",
                "raw_pack": "6x750ML",
                "shopify_units_per_case": Decimal("6"),
                "qualifying_units_per_case": Decimal("6"),
                "assortment_scope": "SINGLE",
                "assortment_group": None,
                "assortable": False,
                "confidence": "VERIFIED",
                "valid_from": date(2026, 10, 1),
                "valid_to": date(2026, 10, 31),
            },
            "vendor_id": "00000000-0000-4000-8000-000000000001",
            "vendor_rules": vendor,
            "selected_offer_input_evidence": {
                "selected_offer": {
                    "offer_id": 1,
                    "variant_id": "1001",
                    "vendor_id": "00000000-0000-4000-8000-000000000001",
                    "supplier_sku": "SUP-001",
                    "package_type": "STANDARD",
                    "size_text": "750ML",
                    "raw_pack": "6x750ML",
                    "shopify_units_per_case": Decimal("6"),
                    "qualifying_units_per_case": Decimal("6"),
                    "assortment_scope": "SINGLE",
                    "assortment_group": None,
                    "assortable": False,
                    "confidence": "VERIFIED",
                    "valid_from": date(2026, 10, 1),
                    "valid_to": date(2026, 10, 31),
                },
                "applicable_vendor_terms": {
                    "vendor_id": "00000000-0000-4000-8000-000000000001",
                    "vendor_rules": vendor,
                },
            },
            "need": serialize_baseline_need(need),
            "blockers": [],
        }
        binding = build_development_baseline_need_binding(context, need)
        self.assertEqual(binding["contract"], BASELINE_NEED_BINDING_CONTRACT)
        context["development_baseline_need_binding"] = binding
        self.assertTrue(
            validate_development_baseline_need_context(
                context, manifest_contract=V2_CONTRACT
            )
        )
        self.assertFalse(
            validate_development_baseline_need_context(
                context, manifest_contract=CONTRACT
            )
        )
        mixed_context = dict(context)
        mixed_context["development_forecast_contract"] = CONTRACT
        self.assertFalse(
            validate_development_baseline_need_context(
                mixed_context, manifest_contract=V2_CONTRACT
            )
        )
        copied = dict(context)
        copied["variant_id"] = "4001"
        self.assertFalse(
            validate_development_baseline_need_context(
                copied, manifest_contract=V2_CONTRACT
            )
        )
        mismatched_sha = copy.deepcopy(context)
        mismatched_sha["development_forecast_evidence_sha256"] = "0" * 64
        mismatched_sha["development_baseline_need_binding"] = (
            build_development_baseline_need_binding(mismatched_sha, need)
        )
        self.assertFalse(
            validate_development_baseline_need_context(
                mismatched_sha, manifest_contract=V2_CONTRACT
            )
        )
        caller_plan = plan_development_forecast(
            source,
            horizon_days=17,
            policy=policy,
        )
        self.assertTrue(
            validate_development_forecast_evidence(caller_plan.to_json_dict())
        )
        caller_context = copy.deepcopy(context)
        caller_context["development_forecast_evidence"] = (
            caller_plan.to_json_dict()
        )
        caller_context["development_forecast_evidence_sha256"] = (
            caller_plan.evidence_sha256
        )
        caller_context["development_baseline_need_binding"] = (
            build_development_baseline_need_binding(caller_context, need)
        )
        self.assertFalse(
            validate_development_baseline_need_context(
                caller_context, manifest_contract=V2_CONTRACT
            )
        )

        def recalculate_and_rebind(changed):
            changed_vendor = changed["vendor_rules"]
            changed_position = changed["open_po_position"]
            changed_need = calculate_development_baseline_need(
                forecast_daily_velocity=plan.forecast_daily_velocity,
                point_forecast_units=plan.point_forecast_units,
                empirical_protection_units=plan.protection_units,
                forecast_horizon_days=plan.horizon_days,
                available_units=changed["available_units"],
                trusted_incoming_units=changed["trusted_incoming_units"],
                order_cycle_days=int(changed_vendor[2]),
                lead_time_days=int(changed_vendor[3]),
                lead_time_variability_days=changed_vendor[4],
                policy_mode=changed["policy_mode"],
                units_per_case=int(changed["units_per_case"]),
                loose_order_allowed=bool(changed_vendor[8]),
                loose_unit_fee=changed_vendor[9],
                open_po_blocked=bool(changed_position["blocks_reorder"]),
                protection_days_override=plan.horizon_days,
            )
            changed["need"] = serialize_baseline_need(changed_need)
            changed["development_baseline_need_binding"] = (
                build_development_baseline_need_binding(changed, changed_need)
            )

        source_bound = copy.deepcopy(context)
        source_bound["trusted_incoming_units"] = Decimal("6")
        source_bound["inventory_rows"][0][2] = Decimal("6")
        source_bound["open_po_position"] = {
            "variant_id": "1001",
            "vendor_id": "00000000-0000-4000-8000-000000000001",
            "trusted_incoming_units": Decimal("6"),
            "open_line_count": 2,
            "blocks_reorder": True,
            "trusted_sources": [
                {
                    "po_line_id": 10,
                    "source_vendor_id": "00000000-0000-4000-8000-000000000001",
                    "open_units": Decimal("6"),
                    "expected_receipt_at": datetime(
                        2026, 10, 6, 14, tzinfo=timezone.utc
                    ),
                    "reconciliation_status": "OPEN",
                    "line_status": "ORDERED",
                    "shopify_import_status": "IMPORTED",
                    "last_reconciled_at": datetime(
                        2026, 10, 5, 13, tzinfo=timezone.utc
                    ),
                    "last_reconciled_by": "synthetic:owner",
                    "reconciliation_evidence": {
                        "source": "FABRICATED_ACCEPTANCE",
                        "reference": "PO-LINE-10",
                    },
                }
            ],
            "blockers": [
                {
                    "po_line_id": 11,
                    "source_vendor_id": "00000000-0000-4000-8000-000000000001",
                    "reason": "OPEN_PO_RECEIPT_OR_BACKORDER_STATE_UNRESOLVED",
                    "reconciliation_status": "AMBIGUOUS",
                    "line_status": "ORDERED",
                    "shopify_import_status": "IMPORTED",
                    "expected_receipt_present": False,
                    "expected_receipt_overdue": False,
                    "direct_evidence_present": False,
                    "last_reconciled_at": None,
                    "last_reconciled_by": None,
                    "reconciliation_evidence": None,
                }
            ],
        }
        source_bound["blockers"] = ["OPEN_PO_RECONCILIATION_BLOCKED"]
        recalculate_and_rebind(source_bound)
        self.assertTrue(
            validate_development_baseline_need_context(
                source_bound, manifest_contract=V2_CONTRACT
            )
        )
        persisted_source_bound = json.loads(
            json.dumps(source_bound, default=str, allow_nan=False)
        )
        self.assertTrue(
            validate_development_baseline_need_context(
                persisted_source_bound, manifest_contract=V2_CONTRACT
            )
        )
        aggregate_location = copy.deepcopy(context)
        aggregate_location["inventory_rows"][0][0] = ""
        recalculate_and_rebind(aggregate_location)
        self.assertTrue(
            validate_development_baseline_need_context(
                aggregate_location, manifest_contract=V2_CONTRACT
            )
        )
        self.assertTrue(
            validate_development_baseline_need_context(
                json.loads(json.dumps(aggregate_location, default=str)),
                manifest_contract=V2_CONTRACT,
            )
        )

        capture_substitution = copy.deepcopy(context)
        capture_substitution["inventory_rows"][0][4] = (
            "00000000-0000-4000-8000-000000000098"
        )
        recalculate_and_rebind(capture_substitution)
        self.assertFalse(
            validate_development_baseline_need_context(
                capture_substitution, manifest_contract=V2_CONTRACT
            )
        )
        duplicate_location = copy.deepcopy(context)
        duplicate_location["inventory_rows"].append(
            [
                "location",
                Decimal("0"),
                Decimal("0"),
                "VALID",
                "00000000-0000-4000-8000-000000000099",
            ]
        )
        duplicate_location["inventory_capture"][5] = 2
        duplicate_location["inventory_capture"][6] = 2
        recalculate_and_rebind(duplicate_location)
        self.assertFalse(
            validate_development_baseline_need_context(
                duplicate_location, manifest_contract=V2_CONTRACT
            )
        )

        fabricated_source = copy.deepcopy(context)
        fabricated_source["trusted_incoming_units"] = Decimal("6")
        fabricated_source["inventory_rows"][0][2] = Decimal("6")
        fabricated_source["open_po_position"].update(
            {
                "trusted_incoming_units": Decimal("6"),
                "open_line_count": 1,
                "trusted_sources": [
                    {
                        "po_line_id": 999,
                        "source_vendor_id": "fabricated",
                        "open_units": Decimal("6"),
                    }
                ],
            }
        )
        recalculate_and_rebind(fabricated_source)
        self.assertFalse(
            validate_development_baseline_need_context(
                fabricated_source, manifest_contract=V2_CONTRACT
            )
        )

        demoted_blocker = copy.deepcopy(source_bound)
        blocker = demoted_blocker["open_po_position"]["blockers"].pop()
        demoted_blocker["open_po_position"]["trusted_sources"].append(
            {
                "po_line_id": blocker["po_line_id"],
                "source_vendor_id": blocker["source_vendor_id"],
                "open_units": Decimal("1"),
                "expected_receipt_at": datetime(
                    2026, 10, 6, 14, tzinfo=timezone.utc
                ),
                "reconciliation_status": "OPEN",
                "line_status": "ORDERED",
                "shopify_import_status": "IMPORTED",
                "last_reconciled_at": datetime(
                    2026, 10, 5, 13, tzinfo=timezone.utc
                ),
                "last_reconciled_by": "synthetic:owner",
                "reconciliation_evidence": None,
            }
        )
        demoted_blocker["open_po_position"]["trusted_incoming_units"] = Decimal(
            "7"
        )
        demoted_blocker["trusted_incoming_units"] = Decimal("7")
        demoted_blocker["inventory_rows"][0][2] = Decimal("7")
        demoted_blocker["open_po_position"]["blocks_reorder"] = False
        demoted_blocker["blockers"] = []
        recalculate_and_rebind(demoted_blocker)
        self.assertFalse(
            validate_development_baseline_need_context(
                demoted_blocker, manifest_contract=V2_CONTRACT
            )
        )

        scalar_substitutions = []

        copied_offer_pack = copy.deepcopy(context)
        copied_offer_pack["units_per_case"] = 1
        copied_offer_pack["qualifying_units_per_case"] = 1
        copied_offer_pack["offer_evidence"]["shopify_units_per_case"] = Decimal(
            "1"
        )
        copied_offer_pack["offer_evidence"]["qualifying_units_per_case"] = (
            Decimal("1")
        )
        recalculate_and_rebind(copied_offer_pack)
        self.assertFalse(
            validate_development_baseline_need_context(
                copied_offer_pack, manifest_contract=V2_CONTRACT
            )
        )

        copied_vendor_terms = copy.deepcopy(context)
        copied_vendor_terms["vendor_rules"] = copy.deepcopy(context["vendor_rules"])
        copied_vendor_terms["vendor_rules"][2] = 7
        copied_vendor_terms["development_forecast_evidence"][
            "protection_calendar"
        ]["vendor_rules_projection"]["order_cycle_days"] = 7
        self.assertFalse(
            _validate_v2_development_need_sources(
                copied_vendor_terms,
                evidence=copied_vendor_terms["development_forecast_evidence"],
                vendor=copied_vendor_terms["vendor_rules"],
                position=copied_vendor_terms["open_po_position"],
            )
        )

        changed = copy.deepcopy(context)
        changed["units_per_case"] = True
        recalculate_and_rebind(changed)
        scalar_substitutions.append(changed)

        changed = copy.deepcopy(context)
        changed["units_per_case"] = 1
        recalculate_and_rebind(changed)
        scalar_substitutions.append(changed)

        changed = copy.deepcopy(context)
        changed["available_units"] = Decimal("30")
        recalculate_and_rebind(changed)
        scalar_substitutions.append(changed)

        changed = copy.deepcopy(context)
        changed["policy_mode"] = "ALLOCATED"
        recalculate_and_rebind(changed)
        scalar_substitutions.append(changed)

        changed = copy.deepcopy(context)
        changed["open_po_position"]["blocks_reorder"] = True
        changed["open_po_position"]["open_line_count"] = 1
        changed["open_po_position"]["blockers"] = [
            {"po_line_id": 1, "reason": "unresolved"}
        ]
        recalculate_and_rebind(changed)
        scalar_substitutions.append(changed)

        changed = copy.deepcopy(context)
        changed["vendor_rules"][2] = "14"
        recalculate_and_rebind(changed)
        scalar_substitutions.append(changed)

        for changed in scalar_substitutions:
            self.assertFalse(
                validate_development_baseline_need_context(
                    changed, manifest_contract=V2_CONTRACT
                )
            )

        legitimate_incoming_mismatch = copy.deepcopy(context)
        legitimate_incoming_mismatch["inventory_rows"][0][2] = Decimal("6")
        legitimate_incoming_mismatch["blockers"].append(
            "INCOMING_EVIDENCE_MISMATCH"
        )
        recalculate_and_rebind(legitimate_incoming_mismatch)
        self.assertTrue(
            validate_development_baseline_need_context(
                legitimate_incoming_mismatch, manifest_contract=V2_CONTRACT
            )
        )

        unproven_incoming = copy.deepcopy(context)
        unproven_incoming["trusted_incoming_units"] = Decimal("6")
        unproven_incoming["inventory_rows"][0][2] = Decimal("6")
        unproven_incoming["open_po_position"]["trusted_incoming_units"] = Decimal(
            "6"
        )
        recalculate_and_rebind(unproven_incoming)
        self.assertFalse(
            validate_development_baseline_need_context(
                unproven_incoming, manifest_contract=V2_CONTRACT
            )
        )

        malformed_calendar = caller_plan.to_json_dict()
        malformed_calendar["protection_calendar"] = []
        self.assertFalse(
            validate_connected_development_forecast_context_evidence(
                malformed_calendar,
                context["demand_observations"],
                expected_contract=V2_CONTRACT,
            )
        )

    def test_evidence_hash_and_new_contract_fail_closed_on_tamper(self):
        source_observations = observations([2] * 84)
        plan = plan_development_forecast(source_observations, horizon_days=14)
        frozen = plan.to_json_dict()
        self.assertTrue(validate_development_forecast_evidence(frozen))
        frozen_observations = [
            {
                "business_date": item.business_date.isoformat(),
                "net_units": str(item.net_units),
                "inventory_state": item.inventory_state,
            }
            for item in source_observations
        ]
        self.assertTrue(
            validate_development_forecast_context_evidence(
                frozen, frozen_observations
            )
        )
        self.assertTrue(
            validate_development_baseline_need_context(
                {
                    "development_forecast_contract": CONTRACT,
                    "development_forecast_evidence": frozen,
                    "development_forecast_evidence_sha256": frozen["sha256"],
                    "demand_observations": frozen_observations,
                    "need": None,
                    "blockers": ["MISSING_OR_INVALID_REPLENISHMENT_POLICY"],
                },
                manifest_contract=CONTRACT,
            )
        )
        for mutation in (
            lambda value: value.pop("policy"),
            lambda value: value.pop("protection_calendar"),
            lambda value: value.update({"target_units": "999.0000"}),
            lambda value: value.update({"sha256": "0" * 64}),
            lambda value: value.update({"method_version": "EMERGENCY_TRANSPARENT_V2"}),
        ):
            changed = plan.to_json_dict()
            mutation(changed)
            self.assertFalse(validate_development_forecast_evidence(changed))

        def rehash(value):
            value["sha256"] = canonical_evidence_sha256(
                {key: item for key, item in value.items() if key != "sha256"}
            )

        changed = plan.to_json_dict()
        changed["category_prior"]["status"] = "CALCULATED"
        rehash(changed)
        self.assertFalse(validate_development_forecast_evidence(changed))

        changed = plan.to_json_dict()
        eligible = [
            item for item in changed["candidates"] if item["status"] == "ELIGIBLE"
        ]
        eligible[-1]["selection_origin_dates"] = eligible[-1][
            "selection_origin_dates"
        ][1:]
        rehash(changed)
        self.assertFalse(validate_development_forecast_evidence(changed))

        actual_calendar = calculate_calendar_protection_horizon(
            evaluation_at=datetime(2026, 10, 5, 14, tzinfo=timezone.utc),
            timezone_name="America/New_York",
            order_days=("MONDAY",),
            order_cutoff_local=time(23, 59, 59),
            expected_delivery_days=("THURSDAY",),
            order_cycle_days=7,
            lead_time_days=1,
            lead_time_variability_days="0",
        )
        actual = plan_development_forecast(
            observations([2] * 84),
            horizon_days=10,
            protection_calendar=actual_calendar,
        ).to_json_dict()
        actual["protection_calendar"]["variability_treatment"] = (
            "ADDED_TO_DEMAND_HORIZON"
        )
        rehash(actual)
        self.assertFalse(validate_development_forecast_evidence(actual))

        selected = plan_development_forecast(
            observations(
                [7 if index % 7 in {4, 5} else 0 for index in range(84)]
            ),
            horizon_days=6,
        ).to_json_dict()
        selected["selected_model"] = "NAIVE"
        selected["selection_metrics"] = next(
            item["selection_metrics"]
            for item in selected["candidates"]
            if item["name"] == "NAIVE"
        )
        for item in selected["candidates"]:
            item["selected"] = item["name"] == "NAIVE"
        rehash(selected)
        self.assertFalse(validate_development_forecast_evidence(selected))

        blocked = plan_development_forecast(
            observations([1] * 40), horizon_days=3
        ).to_json_dict()
        blocked["point_forecast_units"] = "999999.0000"
        blocked["candidates"] = [{"name": "not-a-candidate"}]
        rehash(blocked)
        self.assertFalse(validate_development_forecast_evidence(blocked))

        derived_tamper = plan.to_json_dict()
        derived_tamper["point_forecast_units"] = "42.0000"
        derived_tamper["forecast_daily_velocity"] = "3.000000"
        derived_tamper["target_units"] = str(
            Decimal("42.0000")
            + Decimal(derived_tamper["protection_units"])
        )
        rehash(derived_tamper)
        self.assertTrue(validate_development_forecast_evidence(derived_tamper))
        self.assertFalse(
            validate_development_forecast_context_evidence(
                derived_tamper, frozen_observations
            )
        )


if __name__ == "__main__":
    unittest.main()
