"""Known-answer tests for deterministic, review-only supplier projections."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from procurement_os.supplier_mapping_review import (
    REVIEW_LABEL,
    build_offer_family_report,
    canonical_report_bytes,
    compare_review_snapshots,
    protect_spreadsheet_text,
    render_report_html,
)
from procurement.tools.review_supplier_mapping_package import (
    ReportOutputError,
    _publish_directory,
    write_report_bundle,
)


def offer(offer_id: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "variant_id": "1001",
        "vendor": "Empire",
        "offer_id": offer_id,
        "supplier_sku": offer_id.upper(),
        "supplier_title": "Fixture 750ML",
        "offer_type": "STANDARD",
        "candidate_disposition": "PROPOSED_REVIEW_CANDIDATE",
        "mapping_status": "PROPOSED_REVIEW_CANDIDATE",
        "physical_units_per_supplier_case": 12,
        "supplier_retail_pack": 1,
        "proposed_shopify_sellable_units_per_case": 12,
        "supplier_case_pack": 12,
        "supplier_order_increment": 12,
        "supplier_qualifying_unit": "BT",
        "supplier_qualifying_units_per_case": 12,
        "case_price": Decimal("120.00"),
        "unit_price": Decimal("10.00"),
        "source_file": "synthetic.pdf",
        "source_page": 7,
        "approval_status": "UNAPPROVED_CANDIDATE",
    }
    row.update(changes)
    return row


class SupplierMappingReviewTests(unittest.TestCase):
    def test_one_variant_retains_standard_gift_and_alternate_case_occurrences(self):
        rows = [
            offer("std"),
            offer("gift", offer_type="GIFT_WITH_GLASS", supplier_sku="GIFT-01"),
            offer(
                "alternate",
                offer_type="ALTERNATE_CASE",
                supplier_sku="ALT-06",
                physical_units_per_supplier_case=6,
                proposed_shopify_sellable_units_per_case=6,
            ),
        ]
        report = build_offer_family_report(rows, cohorts={"original": 2000, "current": 2003})

        self.assertEqual(report["label"], REVIEW_LABEL)
        self.assertEqual(report["cohorts"], {"original": 2000, "current": 2003})
        self.assertEqual(len(report["offer_families"]), 1)
        alternatives = report["offer_families"][0]["alternatives"]
        self.assertEqual(
            [item["identity"]["offer_id"] for item in alternatives],
            ["alternate", "gift", "std"],
        )
        self.assertEqual(
            [item["program_type"] for item in alternatives],
            ["ALTERNATE_CASE", "GIFT_WITH_GLASS", "STANDARD"],
        )
        self.assertEqual(len(report["simultaneous_alternatives"]), 1)

    def test_unit_conversions_are_separate_and_catalog_guards_win(self):
        rows = [
            offer(
                "mini",
                physical_units_per_supplier_case=120,
                supplier_case_pack=12,
                supplier_retail_pack=10,
                proposed_shopify_sellable_units_per_case=120,
                supplier_qualifying_units_per_case=None,
            ),
            offer(
                "minus196",
                variant_id="196",
                physical_units_per_supplier_case=24,
                supplier_case_pack=6,
                supplier_retail_pack=4,
                proposed_shopify_sellable_units_per_case=6,
                catalog_status="CONFLICT",
            ),
        ]
        report = build_offer_family_report(rows)
        alternatives = {
            item["identity"]["offer_id"]: item
            for family in report["offer_families"]
            for item in family["alternatives"]
        }
        self.assertEqual(
            alternatives["mini"]["conversions"],
            {
                "physical_units_per_supplier_case": 120,
                "retail_units_per_supplier_case": 12,
                "shopify_units_per_supplier_case": 120,
                "inner_pack_units": 10,
                "supplier_order_increment": 12,
                "supplier_qualifying_unit": "BT",
                "supplier_qualifying_units_per_case": None,
            },
        )
        self.assertEqual(alternatives["minus196"]["conversions"]["physical_units_per_supplier_case"], 24)
        self.assertEqual(alternatives["minus196"]["conversions"]["shopify_units_per_supplier_case"], 6)
        self.assertNotEqual(alternatives["minus196"]["effective_disposition"], "SUPPORTED")
        self.assertIn("CATALOG_CONFLICT", alternatives["minus196"]["guard_reasons"])

    def test_duplicate_occurrence_differs_from_commercial_conflict(self):
        exact = offer("same")
        changed = offer("same", unit_price=Decimal("10.01"))
        report = build_offer_family_report([exact, dict(exact), changed])
        self.assertEqual(len(report["duplicate_occurrences"]), 1)
        self.assertEqual(len(report["conflicting_occurrences"]), 1)
        self.assertEqual(report["conflicting_occurrences"][0]["identity"]["offer_id"], "same")

    def test_monthly_diff_keeps_omission_nonretiring_and_finds_threshold_only_change(self):
        previous = [
            offer("tier", source_tier_id="tier-1", break_quantity=60, break_unit="BT"),
            offer("missing"),
        ]
        current = [
            offer("tier", source_tier_id="tier-1", break_quantity=36, break_unit="BT"),
            offer("new", supplier_sku="REUSED", vendor="Southern"),
        ]
        comparison = compare_review_snapshots(previous, current)
        self.assertEqual(len(comparison["missing_occurrences"]), 1)
        self.assertEqual(comparison["missing_occurrences"][0]["retirement_inferred"], False)
        self.assertEqual(len(comparison["added_occurrences"]), 1)
        self.assertIn("BT_CS_THRESHOLD_CHANGED", comparison["categories"])
        self.assertNotIn("PRICE_CHANGED", comparison["categories"])
        self.assertEqual(comparison["changed_occurrences"][0]["before"]["break_quantity"], 60)
        self.assertEqual(comparison["changed_occurrences"][0]["after"]["break_quantity"], 36)

    def test_rendering_is_deterministic_escaped_and_formula_safe_at_boundary_only(self):
        report = build_offer_family_report(
            [offer("html", supplier_title='<script>alert("x")</script>', review_note="=2+2")]
        )
        first = canonical_report_bytes(report)
        self.assertEqual(first, canonical_report_bytes(report))
        self.assertEqual(json.loads(first)["label"], REVIEW_LABEL)
        html = render_report_html(report, title="<Night & Review>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;Night &amp; Review&gt;", html)
        self.assertEqual(protect_spreadsheet_text("=2+2"), "'=2+2")
        self.assertEqual(protect_spreadsheet_text("ordinary"), "ordinary")
        self.assertEqual(report["offer_families"][0]["alternatives"][0]["raw"]["review_note"], "=2+2")

    def test_atomic_report_bundle_accepts_exact_replay_and_rejects_drift(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "evidence"
            first = write_report_bundle(output, b'{"label":"review"}\n', b"<p>review</p>\n")
            self.assertFalse(first["idempotent_replay"])
            self.assertEqual(sorted(path.name for path in output.iterdir()), [
                "SHA256SUMS.json", "report.html", "report.json"
            ])
            second = write_report_bundle(output, b'{"label":"review"}\n', b"<p>review</p>\n")
            self.assertTrue(second["idempotent_replay"])
            (output / "report.json").write_bytes(b"tampered\n")
            with self.assertRaisesRegex(ReportOutputError, "OUTPUT_DRIFT"):
                write_report_bundle(output, b'{"label":"review"}\n', b"<p>review</p>\n")

    def test_atomic_publish_never_replaces_racing_empty_directory(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            staged = root / "staged"
            destination = root / "destination"
            staged.mkdir()
            destination.mkdir()
            with self.assertRaises(OSError):
                _publish_directory(staged, destination)
            self.assertTrue(staged.is_dir())
            self.assertTrue(destination.is_dir())

    def test_known_answer_pack_examples_remain_distinct(self):
        rows = [
            offer("mini-50", physical_units_per_supplier_case=120, supplier_case_pack=12, supplier_retail_pack=10, proposed_shopify_sellable_units_per_case=120),
            offer("patron", variant_id="72", physical_units_per_supplier_case=72, supplier_case_pack=72, supplier_retail_pack=1, proposed_shopify_sellable_units_per_case=72),
            offer("beatbox", variant_id="12", physical_units_per_supplier_case=12, supplier_case_pack=12, supplier_retail_pack=1, proposed_shopify_sellable_units_per_case=12),
            offer("la-marca", variant_id="24", physical_units_per_supplier_case=24, supplier_case_pack=8, supplier_retail_pack=3, proposed_shopify_sellable_units_per_case=8),
            offer("minus-196", variant_id="196", physical_units_per_supplier_case=24, supplier_case_pack=6, supplier_retail_pack=4, proposed_shopify_sellable_units_per_case=6),
        ]
        report = build_offer_family_report(rows)
        conversions = {
            item["identity"]["offer_id"]: item["conversions"]
            for family in report["offer_families"]
            for item in family["alternatives"]
        }
        self.assertEqual((conversions["mini-50"]["physical_units_per_supplier_case"], conversions["mini-50"]["retail_units_per_supplier_case"], conversions["mini-50"]["inner_pack_units"]), (120, 12, 10))
        self.assertEqual(conversions["patron"]["shopify_units_per_supplier_case"], 72)
        self.assertEqual(conversions["beatbox"]["shopify_units_per_supplier_case"], 12)
        self.assertEqual((conversions["la-marca"]["physical_units_per_supplier_case"], conversions["la-marca"]["shopify_units_per_supplier_case"], conversions["la-marca"]["inner_pack_units"]), (24, 8, 3))
        self.assertEqual((conversions["minus-196"]["physical_units_per_supplier_case"], conversions["minus-196"]["shopify_units_per_supplier_case"], conversions["minus-196"]["inner_pack_units"]), (24, 6, 4))

    def test_identity_expression_combo_and_review_nulls_never_gain_authority(self):
        rows = [
            offer("bloodlines", variant_id="B", supplier_title="Bloodlines", historical_title="Palermo", historical_sales_attribution_resolved=False),
            offer("ocean-bourbon", variant_id="O", expression="Bourbon", supplier_sku="403801"),
            offer("ocean-rye", variant_id="O", expression="Rye", supplier_sku="539040"),
            offer("abv", variant_id="A", abv_percent=Decimal("45.1"), proof=None),
            offer("agave", variant_id="G", supplier_title="100% agave", proof=None),
            offer("glass", variant_id="X", offer_type="GIFT_WITH_GLASS", components=[{"kind":"glassware"}]),
            offer("combo", variant_id="X", offer_type="FIXED_COMBO", components=[{"kind":"alcohol","sku":"ONE"},{"kind":"alcohol","sku":"TWO"}]),
            offer("null", variant_id="N", supplier_qualifying_units_per_case=None, reviewed_null=True),
            offer("excluded", variant_id="E", candidate_disposition="POLICY_EXCLUDED"),
            offer("rejected", variant_id="R", candidate_disposition="REJECTED_ATTRIBUTE_CONFLICT"),
            offer("search", variant_id="S", candidate_disposition="SEARCH_LEAD_ONLY"),
            offer("stale", variant_id="T", owner_decision_status="STALE"),
        ]
        report = build_offer_family_report(rows)
        alternatives = {item["identity"]["offer_id"]: item for family in report["offer_families"] for item in family["alternatives"]}
        self.assertEqual(alternatives["bloodlines"]["raw"]["historical_title"], "Palermo")
        self.assertFalse(alternatives["bloodlines"]["raw"]["historical_sales_attribution_resolved"])
        self.assertNotEqual(alternatives["ocean-bourbon"]["identity"], alternatives["ocean-rye"]["identity"])
        self.assertEqual((alternatives["abv"]["expression"]["abv_percent"], alternatives["abv"]["expression"]["proof"]), (Decimal("45.1"), None))
        self.assertIsNone(alternatives["agave"]["expression"]["proof"])
        self.assertNotEqual(alternatives["glass"]["program_type"], alternatives["combo"]["program_type"])
        self.assertIsNone(alternatives["null"]["conversions"]["supplier_qualifying_units_per_case"])
        self.assertEqual([alternatives[key]["effective_disposition"] for key in ("excluded", "rejected", "search")], ["BLOCKED", "BLOCKED", "BLOCKED"])
        self.assertEqual(report["invariants"]["import_ready_rows"], 0)

    def test_cross_vendor_sku_and_raw_commercial_evidence_are_preserved(self):
        rows = [
            offer("emp-old", vendor="Empire", supplier_sku="0012-A", case_price=None, unit_price=None, per_ounce_price=Decimal("1.2345"), split_inclusive_price=Decimal("10.2500")),
            offer("southern", vendor="Southern", supplier_sku="0012-A"),
            offer("emp-new", vendor="Empire", supplier_sku="0012-A", offer_type="PROPOSED_REPLACEMENT"),
        ]
        report = build_offer_family_report(rows)
        self.assertEqual(report["sku_reuse"][0]["supplier_sku"], "0012-A")
        self.assertEqual(report["sku_reuse"][0]["vendors"], ["Empire", "Southern"])
        alternatives = [item for family in report["offer_families"] for item in family["alternatives"]]
        old = next(item for item in alternatives if item["identity"]["offer_id"] == "emp-old")
        self.assertIsNone(old["raw"]["case_price"])
        self.assertEqual(old["raw"]["per_ounce_price"], Decimal("1.2345"))
        self.assertEqual(old["raw"]["split_inclusive_price"], Decimal("10.2500"))
        self.assertEqual(report["invariants"]["names_or_skus_are_identity"], False)

    def test_real_field_aliases_remain_fail_closed_and_diffable(self):
        guarded = build_offer_family_report([
            offer("conflict", authoritative_catalog_status="CONFLICT"),
            offer("excluded", variant_id="2", policy_excluded=True),
        ])
        alternatives = {item["identity"]["offer_id"]: item for family in guarded["offer_families"] for item in family["alternatives"]}
        self.assertTrue(alternatives["conflict"]["blocked"])
        self.assertIn("CATALOG_CONFLICT", alternatives["conflict"]["guard_reasons"])
        self.assertTrue(alternatives["excluded"]["blocked"])
        self.assertIn("POLICY_EXCLUDED", alternatives["excluded"]["guard_reasons"])
        before = [offer("code", supplier_sku=None, supplier_code="OLD"), offer("reject", variant_id="R", candidate_disposition="REJECTED_ATTRIBUTE_CONFLICT")]
        after = [offer("code", supplier_sku=None, supplier_code="NEW"), offer("reject", variant_id="R", candidate_disposition="REJECTED_ATTRIBUTE_CONFLICT")]
        comparison = compare_review_snapshots(before, after)
        self.assertEqual(comparison["summary"]["changed"], 1)
        self.assertIn("SUPPLIER_SKU_CHANGED", comparison["categories"])
        self.assertIn("REJECTED_MATCH_RECURRED", comparison["categories"])


if __name__ == "__main__":
    unittest.main()
