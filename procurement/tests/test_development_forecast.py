"""Deterministic development forecast, classification, and protection tests."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from procurement_os.development_forecast import (
    _predict,
    assign_gp_dollar_abc,
    calculate_calendar_protection_horizon,
    load_development_forecast_policy,
    plan_development_forecast,
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
        classes = assign_gp_dollar_abc(
            [
                {"variant_id": "a", "historical_revenue": "100", "historical_cogs": "10"},
                {"variant_id": "b", "historical_revenue": "500", "historical_cogs": "490"},
                {"variant_id": "c", "historical_revenue": "50", "historical_cogs": None},
            ]
        )
        self.assertEqual(classes["a"]["abc_class"], "A")
        self.assertEqual(classes["a"]["gross_profit_dollars"], "90.00")
        self.assertNotEqual(classes["b"]["abc_class"], "A")
        self.assertEqual(classes["c"]["abc_class"], "NOT_CONFIGURED")
        self.assertEqual(classes["c"]["status"], "MISSING_HISTORICAL_COGS")
        self.assertEqual(classes["a"]["classification_period_days"], 84)
        nonpositive = assign_gp_dollar_abc(
            [
                {"variant_id": "x", "historical_revenue": "10", "historical_cogs": "10"},
                {"variant_id": "y", "historical_revenue": "5", "historical_cogs": "7"},
            ]
        )
        self.assertEqual(
            {row["abc_class"] for row in nonpositive.values()},
            {"NOT_CONFIGURED"},
        )
        self.assertEqual(
            {row["status"] for row in nonpositive.values()},
            {"NONPOSITIVE_HISTORICAL_GROSS_PROFIT"},
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
