"""Deterministic development forecast, classification, and protection tests."""

from datetime import date, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch

from procurement_os.development_forecast import (
    _predict,
    assign_gp_dollar_abc,
    plan_development_forecast,
    validate_development_forecast_evidence,
)
from procurement_os.forecasting import DemandObservation
from procurement_os.replenishment import calculate_development_baseline_need


START = date(2026, 6, 1)


def observations(values: list[object]) -> list[DemandObservation]:
    return [
        DemandObservation(START + timedelta(days=index), Decimal(str(value)), "UNKNOWN")
        for index, value in enumerate(values)
    ]


class DevelopmentForecastTests(unittest.TestCase):
    def test_constant_oracle_controls_need_and_case_count(self):
        plan = plan_development_forecast(
            observations([2] * 84),
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
        values = [5 if index % 7 in {4, 5} else 0 for index in range(84)]
        plan = plan_development_forecast(observations(values), horizon_days=7)
        self.assertEqual(plan.selected_model, "SEASONAL_NAIVE")
        self.assertEqual(plan.point_forecast_units, Decimal("10.0000"))
        self.assertEqual(plan.evidence["selection_metrics"]["wape"], "0.000000")
        self.assertEqual(plan.evidence["evaluation_metrics"]["wape"], "0.000000")

        perturbed = values[:70] + [20] * 14
        later = plan_development_forecast(observations(perturbed), horizon_days=7)
        self.assertEqual(plan.evidence["candidates"], later.evidence["candidates"])
        self.assertEqual(plan.evidence["protection"], later.evidence["protection"])
        self.assertNotEqual(plan.point_forecast_units, later.point_forecast_units)

        trend = plan_development_forecast(
            observations([Decimal(index) / Decimal("10") for index in range(84)]),
            horizon_days=3,
            category_daily_velocity="2",
        )
        self.assertEqual(trend.selected_model, "DAMPED_ETS")
        category = next(
            item
            for item in trend.evidence["candidates"]
            if item["name"] == "CATEGORY_SHRINKAGE"
        )
        self.assertEqual(category["status"], "ELIGIBLE")
        self.assertFalse(category["selected"])

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
            observations([1] * 40), horizon_days=3, category_daily_velocity="2"
        )
        self.assertEqual(thin.status, "BLOCKED")
        self.assertIsNone(thin.evidence["protection_units"])
        self.assertIn("INSUFFICIENT_CHRONOLOGICAL_WINDOWS", thin.evidence["reason_codes"])

    def test_returns_stockouts_and_unknown_availability_never_invent_state(self):
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

    def test_gp_dollar_abc_never_substitutes_revenue_or_current_price(self):
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

    def test_evidence_hash_and_new_contract_fail_closed_on_tamper(self):
        plan = plan_development_forecast(observations([2] * 84), horizon_days=14)
        frozen = plan.to_json_dict()
        self.assertTrue(validate_development_forecast_evidence(frozen))
        for mutation in (
            lambda value: value.pop("policy"),
            lambda value: value.update({"target_units": "999.0000"}),
            lambda value: value.update({"sha256": "0" * 64}),
            lambda value: value.update({"method_version": "EMERGENCY_TRANSPARENT_V2"}),
        ):
            changed = plan.to_json_dict()
            mutation(changed)
            self.assertFalse(validate_development_forecast_evidence(changed))


if __name__ == "__main__":
    unittest.main()
