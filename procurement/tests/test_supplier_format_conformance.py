"""Executable contracts for fabricated future supplier-format examples.

These tests validate retained inputs and expected sidecars only.  They do not
exercise a PDF extractor and confer no mapping, selection, pricing, Shopify,
supplier-contact, or purchasing authority.
"""

from __future__ import annotations

from collections import Counter
import csv
import importlib.util
import io
from pathlib import Path
import sys
import unittest

from procurement_os.price_book import PRICE_BOOK_HEADERS, parse_price_book_csv
from supplier_format_test_support import (
    EXPECTED_CASE_IDS,
    REQUIRED_COVERAGE_LABELS,
    ZERO_AUTHORITY_EFFECTS,
    cases_by_id,
    load_synthetic_corpus,
    presence_aware_differences,
    price_projection_differences,
)


TESTS_DIR = Path(__file__).resolve().parent
CORPUS_PATH = TESTS_DIR / "fixtures" / "supplier_format_conformance_v1.jsonl"
RUNNER_PATH = TESTS_DIR.parent / "tools" / "run_tests.py"
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "supplier_format_conformance_runner", RUNNER_PATH
)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)


class SupplierFormatConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_synthetic_corpus(CORPUS_PATH)
        cls.by_id = cases_by_id(cls.cases)

    def test_corpus_is_strict_fabricated_eight_case_review_only_evidence(self):
        self.assertEqual(tuple(self.by_id), EXPECTED_CASE_IDS)
        self.assertEqual(len(self.cases), 8)
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                self.assertIs(case["synthetic"], True)
                self.assertEqual(case["source_kind"], "FABRICATED_TEST_SCENARIO")
                self.assertEqual(tuple(case["coverage_labels"]), REQUIRED_COVERAGE_LABELS)
                self.assertEqual(case["authority_effects"], ZERO_AUTHORITY_EFFECTS)

    def test_identical_bytes_under_another_filename_are_alias_only(self):
        case = self.by_id["SF-01"]
        documents = case["scenario"]["documents"]
        self.assertNotEqual(documents[0]["filename"], documents[1]["filename"])
        self.assertEqual(documents[0]["content_sha256"], documents[1]["content_sha256"])
        observed = {
            "document_relation": "BYTE_IDENTICAL_FILENAME_ALIAS",
            "new_revision_count": 0,
            "new_occurrence_count": 0,
            "approval_state": "UNAPPROVED_EVIDENCE_ONLY",
        }
        self.assertEqual(presence_aware_differences(case["expected"], observed), ())

    def test_codes_suffixes_reuse_and_distributor_scope_preserve_identity_evidence(self):
        case = self.by_id["SF-02"]
        scopes = case["scenario"]["distributor_scopes"]
        self.assertEqual([row["code"] for row in scopes], ["0012-A"] * 3)
        self.assertNotEqual(scopes[0]["expression"], scopes[1]["expression"])
        self.assertNotEqual(scopes[1]["distributor"], scopes[2]["distributor"])
        self.assertEqual(
            case["expected"]["same_distributor_changed_expression"],
            "DISTINCT_OPERATIONAL_OFFER_EVIDENCE",
        )
        self.assertEqual(
            case["expected"]["other_distributor_same_code"],
            "DISTINCT_DISTRIBUTOR_SCOPE",
        )
        self.assertIs(case["expected"]["supplier_code_is_canonical_identity"], False)

    def test_occurrences_tiers_units_and_continuation_boundaries_remain_distinct(self):
        tier_case = self.by_id["SF-03"]
        occurrences = tier_case["scenario"]["occurrences"]
        self.assertEqual(len({row["occurrence_id"] for row in occurrences}), 2)
        self.assertEqual(len({row["printed_tier"] for row in occurrences}), 2)
        self.assertEqual({row["printed_code"] for row in occurrences}, {"0042"})
        self.assertEqual(tier_case["expected"]["source_occurrence_count"], 2)
        self.assertEqual(tier_case["expected"]["operational_offer_identity_count"], 1)
        self.assertIs(tier_case["expected"]["tier_is_offer_identity"], False)
        self.assertEqual(
            set(tier_case["expected"]["unit_dimensions"]),
            {
                "physical_bottles",
                "inner_pack",
                "retail_pack",
                "shopify_sellable_units",
                "qualifier",
                "qualifying_units",
            },
        )

        continuation_case = self.by_id["SF-04"]
        self.assertEqual(
            continuation_case["expected"]["attachments"], {"C1": "R1", "C2": "R1"}
        )
        self.assertEqual(
            continuation_case["expected"]["refused_attachments"], {"C3": "R1"}
        )
        self.assertEqual(
            continuation_case["expected"]["hard_boundaries"],
            ["NEW_HEADING", "COLUMN_SCOPE_CHANGE"],
        )

    def test_price_threshold_and_labeled_periods_use_existing_27_field_contract(self):
        case = self.by_id["SF-05"]
        columns = case["scenario"]["edition_columns"]
        self.assertEqual([row["case_price"] for row in columns], ["120.00", "120.00"])
        self.assertEqual([row["break_quantity"] for row in columns], [36, 24])
        self.assertEqual(case["expected"]["change_categories"], ["THRESHOLD_CHANGED"])
        self.assertIs(case["expected"]["price_changed"], False)
        self.assertEqual(
            case["expected"]["column_binding"],
            {"older": "PERIOD_ALPHA", "newer": "PERIOD_BETA"},
        )
        self.assertIs(case["expected"]["calendar_inference_permitted"], False)
        self.assertEqual(len(PRICE_BOOK_HEADERS), 27)
        self.assertEqual(price_projection_differences(case, PRICE_BOOK_HEADERS), ())
        self.assertEqual(tuple(case["price_projection"]), PRICE_BOOK_HEADERS)
        self.assertEqual(case["price_projection"]["review_note"], "UNAPPROVED_TEST_ONLY")
        output = io.StringIO(newline="")
        writer = csv.DictWriter(
            output, fieldnames=PRICE_BOOK_HEADERS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerow(case["price_projection"])
        parsed = parse_price_book_csv(output.getvalue().encode("utf-8"))
        self.assertEqual(parsed["target_price_state"], "future")
        self.assertEqual(parsed["rows"][0]["errors"], [])
        self.assertEqual(parsed["rows"][0]["warnings"], [])

    def test_unknown_null_absent_zero_arithmetic_and_split_fee_are_preserved(self):
        case = self.by_id["SF-06"]
        facts = case["scenario"]["price_facts"]
        self.assertNotIn("value", facts["unknown"])
        self.assertIn("value", facts["explicit_null"])
        self.assertIsNone(facts["explicit_null"]["value"])
        self.assertNotIn("value", facts["absent"])
        self.assertIs(type(facts["zero"]["value"]), int)
        self.assertEqual(facts["zero"]["value"], 0)
        self.assertEqual(
            case["expected"]["arithmetic_status"],
            "CONTRADICTION_RETAINED_NOT_CORRECTED",
        )
        self.assertEqual(case["expected"]["printed_unit_price"], "8.00")
        self.assertEqual(case["expected"]["computed_unit_price"], "8.4166666667")
        self.assertEqual(
            case["expected"]["split_accounting"],
            {
                "inclusive": {
                    "printed_total": "101.00",
                    "fee_already_included": True,
                    "calculated_total": "101.00",
                },
                "separate": {
                    "merchandise": "101.00",
                    "fee": "2.00",
                    "calculated_total": "103.00",
                },
                "double_count_permitted": False,
            },
        )

    def test_regular_gift_special_combo_components_and_scopes_remain_separate(self):
        case = self.by_id["SF-07"]
        occurrences = case["scenario"]["occurrences"]
        self.assertEqual(
            [row["offer_class"] for row in occurrences],
            ["REGULAR", "GIFT", "SPECIAL", "COMBO"],
        )
        self.assertEqual(case["expected"]["source_occurrence_count"], 4)
        self.assertEqual(case["expected"]["operational_offer_evidence_count"], 4)
        self.assertIs(case["expected"]["combo_collapses_to_single_variant"], False)
        self.assertEqual(
            case["expected"]["combo_components"],
            [{"component": "SYN-A", "quantity": 2}, {"component": "SYN-B", "quantity": 1}],
        )
        self.assertEqual(
            case["expected"]["scope_dimensions"],
            ["TERRITORY", "CHANNEL", "ASSORTMENT"],
        )

    def test_seasonal_deal_rejection_and_clarification_memory_have_no_effects(self):
        case = self.by_id["SF-08"]
        editions = case["scenario"]["editions"]
        self.assertIn("deal", editions[0])
        self.assertNotIn("deal", editions[1])
        self.assertIs(case["expected"]["base_and_deal_are_separate"], True)
        self.assertIs(case["expected"]["missing_deal_is_not_carried_forward"], True)
        self.assertEqual(
            case["expected"]["rejection_memory"], "RETAIN_AND_SURFACE_CONFLICT"
        )
        self.assertEqual(
            case["expected"]["clarification_memory"], "RETAIN_CASE_SPECIFIC_SCOPE"
        )
        self.assertIs(case["expected"]["historical_sales_reassignment"], False)
        self.assertEqual(case["expected"]["next_edition_status"], "REVIEW_ONLY")
        self.assertEqual(case["authority_effects"], ZERO_AUTHORITY_EFFECTS)

    def test_presence_comparison_and_new_module_floor_fail_closed(self):
        expected = {
            "absent_must_stay_absent": {},
            "explicit_null": None,
            "zero": 0,
            "ordered": ["A", "B"],
        }
        self.assertEqual(presence_aware_differences(expected, expected), ())
        differences = presence_aware_differences(
            expected,
            {
                "absent_must_stay_absent": {"value": None},
                "zero": False,
                "ordered": ["B", "A"],
            },
        )
        self.assertIn("$.explicit_null: missing", differences)
        self.assertIn("$.absent_must_stay_absent.value: unexpected", differences)
        self.assertTrue(any(item.startswith("$.zero: expected int") for item in differences))
        self.assertTrue(any(item.startswith("$.ordered[0]:") for item in differences))

        module = "test_supplier_format_conformance.py"
        self.assertEqual(runner.REQUIRED_MODULE_MINIMUMS[module], 9)
        self.assertEqual(
            runner.GLOBAL_MINIMUM_TESTS,
            sum(runner.REQUIRED_MODULE_MINIMUMS.values()),
        )
        self.assertEqual(
            runner._module_minimum_errors(Counter({module: 8}), {module: 9}),
            [f"{module} discovered 8 tests; required minimum is 9"],
        )


if __name__ == "__main__":
    unittest.main()
