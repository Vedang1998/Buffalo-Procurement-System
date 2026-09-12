"""Baseline replenishment and pack-conversion tests."""

from decimal import Decimal
import unittest

from procurement_os.replenishment import calculate_baseline_need
from procurement_os.forecasting import DemandObservation, forecast_demand
from datetime import date, timedelta


def routine(**changes):
    values = {
        "forecast_daily_velocity": "2",
        "available_units": "3",
        "trusted_incoming_units": "1",
        "order_cycle_days": 3,
        "lead_time_days": 2,
        "lead_time_variability_days": "1.2",
        "policy_mode": "ROUTINE",
        "units_per_case": 12,
        "loose_order_allowed": False,
        "loose_unit_fee": None,
        "forecast_units_for_protection": "14",
        "forecast_horizon_days": 7,
    }
    values.update(changes)
    return calculate_baseline_need(**values)


class ReplenishmentTests(unittest.TestCase):
    def test_protection_target_and_case_rounding_are_exact(self):
        result = routine()
        self.assertEqual(result.protection_days, 7)
        self.assertEqual(result.target_units, Decimal("14"))
        self.assertEqual((result.raw_need_units, result.cases, result.loose_units), (10, 1, 0))
        self.assertEqual((result.ordered_units, result.pack_rounding_units), (12, 2))

    def test_confirmed_loose_order_avoids_case_rounding_and_applies_fee(self):
        result = routine(loose_order_allowed=True, loose_unit_fee="3.00")
        self.assertEqual((result.cases, result.loose_units, result.ordered_units), (0, 10, 10))
        self.assertEqual(result.loose_fee, Decimal("3.00"))

    def test_trusted_incoming_reduces_need_and_can_cover_target(self):
        result = routine(available_units="4", trusted_incoming_units="10")
        self.assertEqual(result.status, "NO_ORDER_NEEDED")
        self.assertEqual(result.raw_need_units, 0)

    def test_draft_or_ambiguous_open_po_position_blocks_instead_of_suppressing_need(self):
        result = routine(open_po_blocked=True)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("OPEN_PO_RECONCILIATION_BLOCKED", result.reason_codes)

    def test_missing_inventory_incoming_or_policy_fails_closed(self):
        for changes in (
            {"available_units": None},
            {"trusted_incoming_units": None},
            {"policy_mode": None},
        ):
            with self.subTest(changes=changes):
                self.assertEqual(routine(**changes).status, "BLOCKED")

    def test_one_bottle_is_zero_when_covered_and_one_loose_when_empty(self):
        covered = routine(policy_mode="ONE_BOTTLE", available_units="1", trusted_incoming_units="0")
        empty = routine(
            policy_mode="ONE_BOTTLE", available_units="0", trusted_incoming_units="0",
            loose_order_allowed=True, loose_unit_fee="3",
        )
        self.assertEqual(covered.status, "NO_ORDER_NEEDED")
        self.assertEqual((empty.cases, empty.loose_units, empty.ordered_units), (0, 1, 1))

    def test_one_bottle_without_confirmed_loose_and_routine_exclusions_are_safe(self):
        blocked = routine(policy_mode="ONE_BOTTLE", available_units="0", trusted_incoming_units="0")
        self.assertEqual(blocked.status, "BLOCKED")
        for mode in ("ALLOCATED", "ROUTINE_EXCLUDED"):
            with self.subTest(mode=mode):
                result = routine(policy_mode=mode)
                self.assertEqual((result.status, result.ordered_units), ("ROUTINE_EXCLUDED", 0))

    def test_invalid_pack_calendar_money_and_boolean_types_reject(self):
        for changes in (
            {"units_per_case": 0},
            {"order_cycle_days": True},
            {"lead_time_days": -1},
            {"lead_time_variability_days": "NaN"},
            {"loose_order_allowed": "yes"},
            {"loose_order_allowed": True, "loose_unit_fee": None},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                routine(**changes)

    def test_exact_horizon_forecast_prevents_decimal_rounding_overbuy(self):
        start = date(2026, 9, 1)
        forecast = forecast_demand(
            [
                DemandObservation(start + timedelta(days=index), value, "IN_STOCK")
                for index, value in enumerate((1, 1, 0))
            ],
            horizon_days=3,
        )
        result = routine(
            forecast_daily_velocity=forecast.forecast_daily_velocity,
            forecast_units_for_protection=forecast.forecast_units,
            forecast_horizon_days=3,
            available_units=0,
            trusted_incoming_units=0,
            order_cycle_days=3,
            lead_time_days=0,
            lead_time_variability_days=0,
            units_per_case=2,
        )
        self.assertEqual(forecast.forecast_units, Decimal("2.0000"))
        self.assertEqual((result.raw_need_units, result.cases, result.ordered_units), (2, 1, 2))
        with self.assertRaisesRegex(ValueError, "horizon"):
            routine(forecast_units_for_protection="2", forecast_horizon_days=6)
