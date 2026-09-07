"""Deterministic emergency forecast tests."""

from datetime import date, timedelta
from decimal import Decimal
import unittest

from procurement_os.forecasting import DemandObservation, forecast_demand


START = date(2026, 8, 1)


def series(units, states):
    return [
        DemandObservation(START + timedelta(days=index), Decimal(str(value)), state)
        for index, (value, state) in enumerate(zip(units, states, strict=True))
    ]


class ForecastingTests(unittest.TestCase):
    def test_stable_in_stock_history_is_high_confidence_and_exact(self):
        result = forecast_demand(series([1] * 28, ["IN_STOCK"] * 28), horizon_days=10)
        self.assertEqual(result.calendar_velocity, Decimal("1.000000"))
        self.assertEqual(result.in_stock_velocity, Decimal("1.000000"))
        self.assertEqual(result.forecast_units, Decimal("10.0000"))
        self.assertEqual(result.confidence, "HIGH")

    def test_proven_stockouts_are_censored_but_unknown_is_not_in_stock(self):
        proven = forecast_demand(
            series([2] * 7 + [0] * 7, ["IN_STOCK"] * 7 + ["STOCKOUT"] * 7),
            horizon_days=7,
        )
        unknown = forecast_demand(
            series([2] * 7 + [0] * 7, ["IN_STOCK"] * 7 + ["UNKNOWN"] * 7),
            horizon_days=7,
        )
        self.assertEqual(proven.in_stock_velocity, Decimal("2.000000"))
        self.assertEqual(proven.censored_stockout_days, 7)
        self.assertEqual(proven.forecast_units, Decimal("14.0000"))
        self.assertGreater(proven.forecast_units, unknown.forecast_units)
        self.assertEqual(unknown.unknown_inventory_days, 7)
        self.assertIn("UNKNOWN_INVENTORY_DAYS_NOT_ASSUMED_IN_STOCK", unknown.reason_codes)

    def test_true_in_stock_zero_days_reduce_velocity(self):
        result = forecast_demand(
            series([2] * 7 + [0] * 7, ["IN_STOCK"] * 14), horizon_days=7
        )
        self.assertEqual(result.in_stock_velocity, Decimal("1.000000"))
        self.assertEqual(result.forecast_daily_velocity, Decimal("1.000000"))

    def test_returns_remain_in_net_demand_and_never_make_negative_forecast(self):
        result = forecast_demand(
            series([5, -10], ["IN_STOCK", "IN_STOCK"]), horizon_days=30
        )
        self.assertEqual(result.calendar_velocity, Decimal("0.000000"))
        self.assertEqual(result.forecast_units, Decimal("0.0000"))

    def test_missing_dates_are_unknown_zero_sales_not_proven_availability(self):
        result = forecast_demand(
            [
                DemandObservation(START, Decimal("1"), "IN_STOCK"),
                DemandObservation(START + timedelta(days=2), Decimal("1"), "IN_STOCK"),
            ],
            horizon_days=3,
        )
        self.assertEqual(result.calendar_days, 3)
        self.assertEqual(result.unknown_inventory_days, 1)
        self.assertEqual(result.calendar_velocity, Decimal("0.666667"))

    def test_recent_signal_is_damped_and_bounded(self):
        result = forecast_demand(
            series([1] * 14 + [10] * 14, ["IN_STOCK"] * 28), horizon_days=1
        )
        self.assertEqual(result.calendar_velocity, Decimal("5.500000"))
        self.assertEqual(result.recent_velocity, Decimal("10.000000"))
        self.assertEqual(result.forecast_daily_velocity, Decimal("6.187500"))

    def test_bulk_event_is_robustly_capped_and_forces_review_evidence(self):
        result = forecast_demand(
            series([1000] + [1] * 83, ["IN_STOCK"] * 84), horizon_days=7
        )
        self.assertEqual(result.forecast_units, Decimal("7.0000"))
        self.assertEqual(result.outlier_capped_days, 1)
        self.assertIn("ROBUST_OUTLIER_CAP_APPLIED", result.reason_codes)
        self.assertIn("EVENT_OR_BULK_CONCENTRATION_REVIEW", result.reason_codes)
        sparse = forecast_demand(
            series([1000] + [0] * 83, ["IN_STOCK"] * 84), horizon_days=7
        )
        self.assertGreater(sparse.forecast_units, Decimal("0"))
        self.assertLessEqual(sparse.forecast_units, Decimal("1"))
        self.assertEqual(sparse.confidence, "LOW")
        ordinary = forecast_demand(
            series([1] + [0] * 83, ["IN_STOCK"] * 84), horizon_days=7
        )
        self.assertGreater(ordinary.forecast_units, Decimal("0"))
        self.assertEqual(ordinary.outlier_capped_days, 0)

    def test_sparse_stockout_censoring_is_evidence_shrunk(self):
        result = forecast_demand(
            series([2] + [0] * 27, ["IN_STOCK"] + ["STOCKOUT"] * 27),
            horizon_days=30,
        )
        self.assertLess(result.forecast_units, Decimal("60"))
        self.assertEqual(result.confidence, "LOW")

    def test_thin_or_low_coverage_history_is_explicitly_low_confidence(self):
        result = forecast_demand(
            series([1] * 10, ["UNKNOWN"] * 10), horizon_days=7
        )
        self.assertEqual(result.confidence, "LOW")
        self.assertIn("LOW_CONFIDENCE_REVIEW_REQUIRED", result.reason_codes)

    def test_invalid_dates_states_numbers_and_horizon_fail_closed(self):
        good = DemandObservation(START, Decimal("1"), "IN_STOCK")
        cases = (
            ([good], 0),
            ([good, good], 1),
            ([DemandObservation(START, Decimal("NaN"), "IN_STOCK")], 1),
            ([DemandObservation(START, Decimal("1"), "ASSUMED")], 1),
        )
        for observations, horizon in cases:
            with self.subTest(observations=observations, horizon=horizon):
                with self.assertRaises(ValueError):
                    forecast_demand(observations, horizon_days=horizon)
