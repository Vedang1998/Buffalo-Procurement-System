"""Price-tier evidence tests; no strategic auto-buy is permitted."""

from decimal import Decimal
import unittest

from procurement_os.strategic import PriceTier, evaluate_price_tiers


BASE = PriceTier("BASE", Decimal("10"))


class StrategicTierTests(unittest.TestCase):
    def test_base_only_keeps_baseline_cost_and_zero_strategic_extra(self):
        result = evaluate_price_tiers(
            [BASE], baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        self.assertEqual(result.selected_unit_cost, Decimal("10"))
        self.assertEqual(result.strategic_extra_units, 0)
        self.assertEqual(result.next_tiers, ())

    def test_bt_threshold_uses_qualifying_units_and_walks_in_ascending_order(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9"), 12, "BT"),
             PriceTier("BREAK", Decimal("8"), 24, "BT")],
            baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        self.assertEqual(result.selected_unit_cost, Decimal("9"))
        self.assertEqual([tier.break_quantity for tier in result.next_tiers], [24])
        self.assertEqual(result.next_tiers[0].additional_cases, 1)

    def test_cs_threshold_uses_cases_not_bottles(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9"), 2, "CS")],
            baseline_cases=1, baseline_loose_units=6,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        candidate = result.next_tiers[0]
        self.assertEqual((candidate.additional_cases, candidate.incremental_units), (1, 12))

    def test_mixed_typed_ladders_remain_distinct_and_deterministic(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9.50"), 2, "CS"),
             PriceTier("BREAK", Decimal("9"), 24, "BT")],
            baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        self.assertEqual(
            [(item.break_unit, item.break_quantity) for item in result.next_tiers],
            [("BT", 24), ("CS", 2)],
        )

    def test_incremental_cash_and_savings_are_exact_decimal_evidence(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9.25"), 2, "CS")],
            baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=6, qualifying_units_per_case=6,
        )
        candidate = result.next_tiers[0]
        self.assertEqual(candidate.incremental_cash, Decimal("51.00"))
        self.assertEqual(candidate.unit_savings, Decimal("0.75"))

    def test_deeper_cost_increase_and_duplicate_thresholds_reject(self):
        cases = (
            [BASE, PriceTier("BREAK", Decimal("9"), 12, "BT"),
             PriceTier("BREAK", Decimal("9.50"), 24, "BT")],
            [BASE, PriceTier("BREAK", Decimal("9"), 12, "BT"),
             PriceTier("BREAK", Decimal("8"), 12, "BT")],
        )
        for tiers in cases:
            with self.subTest(tiers=tiers), self.assertRaises(ValueError):
                evaluate_price_tiers(
                    tiers, baseline_cases=0, baseline_loose_units=0,
                    sellable_units_per_case=12, qualifying_units_per_case=12,
                )

    def test_invalid_base_break_and_case_inputs_fail_closed(self):
        cases = (
            ([], {}),
            ([BASE, BASE], {}),
            ([PriceTier("BREAK", Decimal("9"), 0, "BT")], {}),
            ([BASE], {"sellable_units_per_case": 0}),
            ([BASE], {"baseline_cases": -1}),
        )
        defaults = dict(
            baseline_cases=0, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        for tiers, changes in cases:
            with self.subTest(tiers=tiers, changes=changes), self.assertRaises(ValueError):
                evaluate_price_tiers(tiers, **{**defaults, **changes})

    def test_every_result_carries_explicit_not_validated_blocker_and_no_filler(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9"), 24, "BT")],
            baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        self.assertEqual(result.status, "EVIDENCE_ONLY")
        self.assertIn("STRATEGIC_OPTIMIZATION_NOT_VALIDATED", result.reason_codes)
        self.assertIn("NO_FILLER_ADDED", result.reason_codes)

    def test_bt_bridge_reuses_baseline_loose_only_with_explicit_evidence(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("8"), 24, "BT")],
            baseline_cases=0, baseline_loose_units=10,
            sellable_units_per_case=12, qualifying_units_per_case=12,
            loose_order_allowed=True, loose_units_qualify=True,
        )
        candidate = result.next_tiers[0]
        self.assertEqual(
            (candidate.additional_cases, candidate.additional_loose_units,
             candidate.incremental_units, candidate.incremental_cash),
            (1, 2, 14, Decimal("92")),
        )

    def test_bt_bridge_never_invents_loose_qualification(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9"), 18, "BT")],
            baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        candidate = result.next_tiers[0]
        self.assertEqual((candidate.additional_cases, candidate.additional_loose_units), (1, 0))
        with self.assertRaises(ValueError):
            evaluate_price_tiers(
                [BASE], baseline_cases=0, baseline_loose_units=0,
                sellable_units_per_case=12, qualifying_units_per_case=12,
                loose_order_allowed=False, loose_units_qualify=True,
            )

    def test_candidates_are_sorted_by_reachable_incremental_units(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("8"), 60, "BT"),
             PriceTier("BREAK", Decimal("9"), 3, "CS")],
            baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        self.assertEqual(
            [(item.break_unit, item.incremental_units) for item in result.next_tiers],
            [("CS", 24), ("BT", 48)],
        )

    def test_bridge_is_marginal_to_already_selected_tier(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9"), 12, "BT"),
             PriceTier("BREAK", Decimal("8"), 2, "CS")],
            baseline_cases=1, baseline_loose_units=0,
            sellable_units_per_case=12, qualifying_units_per_case=12,
        )
        candidate = result.next_tiers[0]
        self.assertEqual(result.selected_unit_cost, Decimal("9"))
        self.assertEqual(candidate.unit_savings, Decimal("1"))
        self.assertEqual(candidate.incremental_cash, Decimal("84"))

    def test_bt_bridge_from_case_plus_ten_loose_adds_only_two_loose(self):
        result = evaluate_price_tiers(
            [BASE, PriceTier("BREAK", Decimal("9"), 24, "BT")],
            baseline_cases=1, baseline_loose_units=10,
            sellable_units_per_case=12, qualifying_units_per_case=12,
            loose_order_allowed=True, loose_units_qualify=True,
        )
        candidate = result.next_tiers[0]
        self.assertEqual(
            (candidate.additional_cases, candidate.additional_loose_units,
             candidate.incremental_units),
            (0, 2, 2),
        )
