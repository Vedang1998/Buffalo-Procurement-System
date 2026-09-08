"""Known-answer tests for deterministic, review-only supplier projections."""

from __future__ import annotations

from decimal import Decimal
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from procurement_os.supplier_mapping_review import (
    REPRESENTATION_GAP_CATALOG,
    REVIEW_LABEL,
    SupplierReviewError,
    build_offer_family_report,
    build_review_batches,
    canonical_report_bytes,
    compare_review_packages,
    compare_review_snapshots,
    occurrence_identity,
    protect_spreadsheet_text,
    report_document,
    render_report_html,
)
from procurement_os.supplier_review_package import PackageTable, ReviewPackage
from procurement.tools import review_supplier_mapping_package as report_cli
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
        replayed = json.loads(canonical_report_bytes(report))
        self.assertEqual(
            [item["identity"]["offer_id"] for item in replayed["offer_families"][0]["alternatives"]],
            ["alternate", "gift", "std"],
        )
        self.assertEqual(
            [item["program_type"] for item in replayed["offer_families"][0]["alternatives"]],
            ["ALTERNATE_CASE", "GIFT_WITH_GLASS", "STANDARD"],
        )

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
                "supplier_order_unit": None,
                "alias_conflicts": [],
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
        self.assertEqual(report["duplicate_occurrences"][0]["raw"], exact)
        self.assertEqual(len(report["conflicting_occurrences"]), 1)
        self.assertEqual(report["conflicting_occurrences"][0]["identity"]["offer_id"], "same")

        provenance_conflict = build_offer_family_report(
            [offer("provenance", source_page=1), offer("provenance", source_page=2)]
        )
        self.assertEqual(len(provenance_conflict["conflicting_occurrences"]), 1)

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
        self.assertIn("<table", html)
        self.assertNotIn("<pre>", html)
        self.assertIn("Full canonical machine detail", html)
        self.assertLess(len(html.encode("utf-8")), len(first))
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

    def test_monthly_diff_flags_same_vendor_sku_reuse_without_merging_identity(self):
        previous = [
            offer(
                "old-occurrence",
                variant_id="OLD",
                vendor="Empire",
                supplier_sku="REUSE-01",
            )
        ]
        current = [
            offer(
                "new-occurrence",
                variant_id="NEW",
                vendor="Empire",
                supplier_sku="REUSE-01",
            )
        ]

        comparison = compare_review_snapshots(previous, current)

        self.assertEqual(
            comparison["sku_reuse_or_replacement"],
            [
                {
                    "vendor": "Empire",
                    "supplier_sku": "REUSE-01",
                    "before": [["OLD", "Empire", "old-occurrence"]],
                    "after": [["NEW", "Empire", "new-occurrence"]],
                }
            ],
        )
        self.assertEqual(comparison["summary"]["added"], 1)
        self.assertEqual(comparison["summary"]["missing_not_retired"], 1)
        self.assertFalse(comparison["missing_occurrences"][0]["retirement_inferred"])

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
        before_code = offer("code", supplier_code="OLD")
        after_code = offer("code", supplier_code="NEW")
        before_code.pop("supplier_sku")
        after_code.pop("supplier_sku")
        before = [before_code, offer("reject", variant_id="R", candidate_disposition="REJECTED_ATTRIBUTE_CONFLICT")]
        after = [after_code, offer("reject", variant_id="R", candidate_disposition="REJECTED_ATTRIBUTE_CONFLICT")]
        comparison = compare_review_snapshots(before, after)
        self.assertEqual(comparison["summary"]["changed"], 1)
        self.assertIn("SUPPLIER_SKU_CHANGED", comparison["categories"])
        self.assertIn("REJECTED_MATCH_RECURRED", comparison["categories"])

    def test_source_tier_is_never_an_offer_occurrence_identity(self):
        with self.assertRaisesRegex(SupplierReviewError, "source occurrence ID"):
            occurrence_identity(
                {
                    "variant_id": "1001",
                    "vendor": "Empire",
                    "source_tier_id": "tier-only-is-not-an-offer",
                }
            )

    def test_all_21_representation_gaps_are_literal_review_only_routes(self):
        report = build_offer_family_report([offer("gap-source")])
        gaps = report["representation_gaps"]
        self.assertEqual(
            [item["gap_id"] for item in gaps],
            [f"G{number:02d}" for number in range(1, 22)],
        )
        self.assertEqual(gaps, [dict(item) for item in REPRESENTATION_GAP_CATALOG])
        self.assertEqual(len(gaps), 21)
        self.assertEqual({item["authority"] for item in gaps}, {"REVIEW_ONLY"})
        self.assertEqual(
            {item["later_route_status"] for item in gaps},
            {"SEPARATE_REVIEW_REQUIRED"},
        )
        self.assertEqual(sum(item["application_changes"] for item in gaps), 0)
        self.assertEqual(
            gaps[0],
            {
                "gap_id": "G01",
                "area": "Unapproved mappings",
                "current_handling": "BLOCKED_CANDIDATE_SIDECAR",
                "later_route": "MAPPING_DECISION_AND_CROSSWALK",
                "later_route_status": "SEPARATE_REVIEW_REQUIRED",
                "authority": "REVIEW_ONLY",
                "application_changes": 0,
                "source": "CODE_OWNED_ROUTE_DERIVED_FROM_V4_1_CONTRACT_GAPS",
            },
        )

    def test_vocabulary_and_alias_transitions_are_candidates_not_approvals(self):
        rows = [
            offer("old", supplier_sku="OLD-01", supplier_title="  Brand   Name  "),
            offer(
                "new",
                supplier_sku="NEW-01",
                supplier_title="  Brand   Name  ",
                predecessor_supplier_sku="OLD-01",
            ),
            offer(
                "southern",
                vendor="Southern",
                supplier_sku="S-01",
                supplier_title="  Brand   Name  ",
            ),
            offer(
                "name-only",
                variant_id="2002",
                supplier_sku="UNRELATED",
                supplier_title="Brand Name",
            ),
        ]
        report = build_offer_family_report(rows)
        vocabulary = report["supplier_name_vocabulary"]
        empire = next(
            item
            for item in vocabulary
            if item["vendor"] == "Empire"
            and item["observed_supplier_text"] == "  Brand   Name  "
        )
        self.assertEqual(empire["normalized_candidate"], "brand name")
        self.assertEqual(empire["source_occurrence_ids"], ["new", "old"])
        self.assertFalse(empire["approval_created"])
        self.assertEqual(
            {(item["vendor"], item["observed_supplier_text"]) for item in vocabulary},
            {
                ("Empire", "  Brand   Name  "),
                ("Empire", "Brand Name"),
                ("Southern", "  Brand   Name  "),
            },
        )
        self.assertEqual(
            report["alias_transition_candidates"],
            [
                {
                    "authority": "CANDIDATE_ONLY",
                    "variant_id": "1001",
                    "vendor": "Empire",
                    "before_supplier_code": "OLD-01",
                    "after_supplier_code": "NEW-01",
                    "source_occurrence_id": "new",
                    "basis": "EXPLICIT_REPLACEMENT_EVIDENCE",
                    "retirement_created": False,
                    "approval_created": False,
                }
            ],
        )
        heuristic_only = build_offer_family_report(
            [
                offer("legacy", supplier_sku="LEGACY"),
                offer("proposal", supplier_sku="NEW", offer_type="PROPOSED_REPLACEMENT"),
            ]
        )
        self.assertEqual(heuristic_only["alias_transition_candidates"], [])

    def test_machine_exceptions_keep_distinct_unsafe_evidence_and_block_stale_owner(self):
        rows = [
            offer("stale", owner_decision_status="STALE"),
            offer(
                "missing-book",
                variant_id="2",
                candidate_disposition="SUPPLIER_SOURCE_MISSING",
                mapping_status="SUPPLIER_SOURCE_MISSING",
                source_file=None,
            ),
            offer(
                "prices",
                variant_id="3",
                case_price=Decimal("120.00"),
                unit_price=Decimal("1.00"),
                per_ounce_price=Decimal("1.2345"),
                split_inclusive_price=Decimal("3.2500"),
            ),
            offer(
                "reviewed-null",
                variant_id="4",
                case_price=None,
                unit_price=None,
                supplier_qualifying_units_per_case=None,
                reviewed_null=True,
            ),
        ]
        report = build_offer_family_report(rows)
        codes = set(report["exception_counts"])
        self.assertTrue(
            {
                "STALE_OWNER_DECISION",
                "SUPPLIER_BOOK_MISSING",
                "PUBLISHED_PRICE_ARITHMETIC_CONFLICT",
                "PER_OUNCE_NOT_A_TIER_PRICE",
                "SPLIT_INCLUSION_REQUIRES_SCOPE",
                "PRICE_EVIDENCE_BLANK",
                "REVIEWED_NULL_PRESERVED",
            }.issubset(codes)
        )
        stale = next(
            item for item in report["occurrences"] if item["occurrence_id"] == "stale"
        )
        self.assertTrue(stale["blocked"])
        self.assertIn("STALE_OWNER_DECISION", stale["guard_reasons"])
        arithmetic = next(
            item
            for item in report["exceptions"]
            if item["code"] == "PUBLISHED_PRICE_ARITHMETIC_CONFLICT"
        )
        self.assertEqual(
            arithmetic["raw_values"]["printed_case_price"], Decimal("120.00")
        )
        self.assertEqual(
            arithmetic["raw_values"]["derived_case_equivalent"], Decimal("12.00")
        )
        self.assertEqual(
            arithmetic["raw_values"]["absolute_difference"], Decimal("108.00")
        )

    def test_conflicting_aliases_fail_closed_without_falling_through_reviewed_null(self):
        row = offer("alias", supplier_code="DIFFERENT")
        report = build_offer_family_report([row])
        occurrence = report["occurrences"][0]
        self.assertTrue(occurrence["blocked"])
        self.assertIn("ALIAS_FIELD_CONFLICT", report["exception_counts"])

        before = offer("alias-month", supplier_code="OLD")
        after = offer("alias-month", supplier_code="NEW")
        with self.assertRaisesRegex(SupplierReviewError, "conflicting monthly aliases"):
            compare_review_snapshots([before], [after])

        type_conflict = build_offer_family_report(
            [offer("typed-alias", candidate_disposition=False, mapping_status=0)]
        )
        self.assertTrue(type_conflict["occurrences"][0]["blocked"])
        numeric_code = build_offer_family_report([offer("numeric-code", supplier_sku=12)])
        self.assertTrue(numeric_code["occurrences"][0]["blocked"])
        with self.assertRaisesRegex(SupplierReviewError, "conflicting Variant ID identity aliases"):
            build_offer_family_report([offer("identity", canonical_variant_id="DIFFERENT")])
        with self.assertRaisesRegex(SupplierReviewError, "must be nonblank exact text"):
            build_offer_family_report([offer("identity", variant_id=1001)])
        with self.assertRaisesRegex(SupplierReviewError, "conflicting source Tier ID identity aliases"):
            compare_review_snapshots(
                [offer("tier-alias", source_tier_id="T-1", tier_id="T-2")],
                [offer("tier-alias", source_tier_id="T-1")],
                previous_tiers=[offer("tier-alias", source_tier_id="T-1", tier_id="T-2")],
                current_tiers=[offer("tier-alias", source_tier_id="T-1")],
            )

    def test_complete_comparison_reports_added_missing_tiers_and_exact_precision(self):
        prior = [
            offer("same", supplier_sku="OLD", unit_price=Decimal("10.123400")),
            offer("missing", variant_id="2"),
        ]
        current = [
            offer("same", supplier_sku="NEW", unit_price=Decimal("10.123400")),
            offer("added", variant_id="3", offer_type="GIFT_WITH_GLASS"),
        ]
        prior_tiers = [
            offer(
                "same",
                source_tier_id="T-OLD",
                break_quantity=60,
                unit_price=Decimal("9.2500"),
            )
        ]
        current_tiers = [
            offer(
                "same",
                source_tier_id="T-NEW",
                break_quantity=36,
                unit_price=Decimal("9.2500"),
            )
        ]
        result = compare_review_snapshots(
            prior,
            current,
            previous_tiers=prior_tiers,
            current_tiers=current_tiers,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["summary"],
            {
                "previous_occurrences": 2,
                "current_occurrences": 2,
                "unchanged": 0,
                "changed": 1,
                "added": 1,
                "missing_not_retired": 1,
            },
        )
        self.assertEqual(
            {item["kind"] for item in result["tier_changes"]},
            {"ADDED_TIER", "MISSING_TIER_NOT_RETIREMENT"},
        )
        self.assertEqual(
            set(result["categories"]),
            {"ADDED_TIER", "MISSING_TIER_NOT_RETIREMENT", "SUPPLIER_SKU_CHANGED"},
        )
        self.assertEqual(
            result["changed"][0]["before"]["unit_price"], Decimal("10.123400")
        )
        self.assertEqual(
            result["alias_transition_candidates"][0]["before_supplier_code"], "OLD"
        )
        self.assertEqual(
            result["operational_effects"],
            {"mapping_approvals": 0, "retirements": 0, "price_approvals": 0},
        )

    def test_actual_monthly_aliases_report_pack_program_proof_vintage_and_territory(self):
        prior = [
            offer(
                "same",
                supplier_code="CODE-1",
                supplier_case_pack=8,
                supplier_retail_pack=3,
                proof=Decimal("90.2"),
                supplier_vintage=["2021"],
                effective_from="2026-08-01",
                territory="UPSTATE",
            )
        ]
        prior[0].pop("supplier_sku")
        current_same = offer(
            "same",
            supplier_code="CODE-1",
            supplier_case_pack=6,
            supplier_retail_pack=4,
            proof=Decimal("96.0"),
            supplier_vintage=["2022"],
            effective_from="2026-09-01",
            territory="STATEWIDE",
            offer_type="GIFT_WITH_GLASS",
        )
        current_same.pop("supplier_sku")
        current_alternate = offer(
            "alternate",
            supplier_code="CODE-2",
            supplier_case_pack=12,
            supplier_retail_pack=1,
            offer_type="STANDARD",
        )
        current_alternate.pop("supplier_sku")
        result = compare_review_snapshots(prior, [current_same, current_alternate])
        self.assertEqual(
            set(result["categories"]),
            {
                "DATE_OR_TERRITORY_CHANGED",
                "CHANNEL_OR_TERRITORY_SCOPE_CHANGED",
                "EFFECTIVE_DATE_CHANGED",
                "EXPRESSION_OR_PROOF_CHANGED",
                "PACK_CHANGED",
                "SIMULTANEOUS_GIFT_ALTERNATIVES",
                "SIMULTANEOUS_PACKAGES",
                "VINTAGE_CHANGED",
            },
        )
        self.assertEqual(
            result["simultaneous_packages"],
            [
                {
                    "variant_id": "1001",
                    "source_occurrence_ids": ["alternate", "same"],
                    "program_types": ["GIFT_WITH_GLASS", "STANDARD"],
                    "selection_created": False,
                }
            ],
        )
        self.assertEqual(
            result["new_supplier_codes"],
            [
                {
                    "variant_id": "1001",
                    "vendor": "Empire",
                    "source_occurrence_id": "alternate",
                    "supplier_code": "CODE-2",
                }
            ],
        )
        self.assertEqual(result["missing_supplier_codes_not_retired"], [])

    def test_partial_package_is_not_comparable_but_complete_synthetic_is(self):
        def package(
            status: str,
            rows: list[dict[str, object]],
            unavailable: tuple[str, ...] = (),
        ) -> ReviewPackage:
            table = PackageTable(
                name="offers",
                path="offers.jsonl",
                format="jsonl",
                rows=tuple(rows),
                raw_sha256="0" * 64,
                canonical_jsonl_sha256="1" * 64,
            )
            return ReviewPackage(
                source="synthetic",
                package_kind="SYNTHETIC",
                snapshot_id="fixture",
                status=status,
                label=REVIEW_LABEL,
                file_count=1,
                verified_file_count=1,
                manifest_sha256="2" * 64,
                tables={"offers": table},
                cohorts={},
                issues=(),
                unavailable_evidence=unavailable,
                metadata={
                    "snapshot_scope": {
                        "comparison_contract": "fixture-comparison-v1",
                        "identity_contract": "fixture-identity-v1",
                        "lineage_family_sha256": "3" * 64,
                        "supplier_scope_kind": "COMPLETE",
                        "supplier_scope": ["Empire"],
                        "cohort_scope": "FIXTURE",
                        "period_semantics": "MONTHLY",
                        "period_id": "2026-09",
                        "supplier_period_coverage_complete": status == "PASS",
                        "missing_supplier_periods": [] if status == "PASS" else ["Empire/2026-09"],
                        "channels": ["BOOK"],
                        "territories": ["TEST"],
                        "simulated": True,
                    }
                },
            )

        partial = package(
            "BASELINE_REQUIRED", [offer("one")], ("EXACT_BASELINE",)
        )
        complete = package("PASS", [offer("one")])
        blocked = compare_review_packages(partial, complete)
        self.assertEqual(blocked["status"], "NOT_COMPARABLE")
        self.assertEqual(blocked["missing_prerequisites"], ["EXACT_BASELINE"])
        self.assertEqual(blocked["change_claims"]["changed"], [])
        self.assertEqual(compare_review_packages(complete, complete)["status"], "PASS")

    def test_monthly_identity_code_reuse_gift_and_missing_are_non_authoritative(self):
        same = compare_review_snapshots([offer("same")], [offer("same")])
        self.assertEqual(same["summary"]["unchanged"], 1)
        self.assertEqual(same["categories"], [])

        changed_code = compare_review_snapshots(
            [offer("same", supplier_sku="A01")],
            [offer("same", supplier_sku="A02")],
        )
        self.assertEqual(changed_code["categories"], ["SUPPLIER_SKU_CHANGED"])
        self.assertEqual(changed_code["alias_transition_candidates"][0]["authority"], "CANDIDATE_ONLY")
        self.assertFalse(changed_code["alias_transition_candidates"][0]["approval_created"])

        reuse = compare_review_snapshots(
            [offer("old", supplier_sku="REUSED")],
            [offer("new", variant_id="1002", supplier_sku="REUSED")],
        )
        self.assertEqual((reuse["summary"]["added"], reuse["summary"]["missing_not_retired"]), (1, 1))
        self.assertEqual(len(reuse["sku_reuse_or_replacement"]), 1)
        self.assertFalse(reuse["missing_occurrences"][0]["retirement_inferred"])

        gift_added = compare_review_snapshots(
            [offer("standard")],
            [offer("standard"), offer("gift", offer_type="GIFT_WITH_GLASS")],
        )
        self.assertIn("SIMULTANEOUS_GIFT_ALTERNATIVES", gift_added["categories"])
        self.assertFalse(gift_added["simultaneous_packages"][0]["selection_created"])
        gift_missing = compare_review_snapshots(
            [offer("standard"), offer("gift", offer_type="GIFT_WITH_GLASS")],
            [offer("standard")],
        )
        self.assertIn("GIFT_DISAPPEARED_NOT_RETIREMENT", gift_missing["categories"])
        self.assertFalse(gift_missing["missing_occurrences"][0]["retirement_inferred"])

    def test_monthly_pack_vintage_null_and_scope_changes_preserve_exact_evidence(self):
        vintage = compare_review_snapshots(
            [offer("same", supplier_vintage="2021")],
            [offer("same", supplier_vintage="2022")],
        )
        self.assertEqual(vintage["categories"], ["VINTAGE_CHANGED"])

        pack = compare_review_snapshots(
            [offer("same", physical_units_per_supplier_case=12)],
            [offer("same", physical_units_per_supplier_case=9)],
        )
        self.assertEqual(pack["categories"], ["PACK_CHANGED"])
        self.assertEqual(
            pack["changed"][0]["changes"]["physical_units_per_supplier_case"]["after"],
            9,
        )

        explicit_null = offer("same", proposed_shopify_sellable_units_per_case=24)
        explicit_null["reviewed_shopify_units_per_case"] = None
        null_change = compare_review_snapshots(
            [offer("same", proposed_shopify_sellable_units_per_case=24)],
            [explicit_null],
        )
        state = null_change["changed"][0]["changes"]["shopify_units_per_supplier_case"]
        self.assertEqual(state, {"before_present": True, "before": 24, "after_present": True, "after": None})
        self.assertIn("PACK_CHANGED", null_change["categories"])

        scoped = compare_review_snapshots(
            [
                offer(
                    "same", source_file="fixture.pdf", source_page=10,
                    source_sha256="1" * 64, source_period="2026-09",
                    channel="BOOK", territory="NORTH", effective_from="2026-09-01",
                )
            ],
            [
                offer(
                    "same", source_file="fixture.pdf", source_page=11,
                    source_sha256="1" * 64, source_period="2026-10",
                    channel="DIRECT", territory="SOUTH", effective_from="2026-10-01",
                )
            ],
        )
        self.assertTrue(
            {
                "SOURCE_REPAGINATED", "SOURCE_PERIOD_CHANGED",
                "CHANNEL_OR_TERRITORY_SCOPE_CHANGED", "EFFECTIVE_DATE_CHANGED",
            }.issubset(scoped["categories"])
        )
        self.assertNotIn("SOURCE_DOCUMENT_CHANGED", scoped["categories"])

    def test_monthly_tiers_thresholds_and_rejected_memory_never_select_or_retire(self):
        previous = [offer("same", break_unit="BT", supplier_qualifying_unit="BT", break_quantity=12)]
        current = [offer("same", break_unit="CS", supplier_qualifying_unit="CS", break_quantity=12)]
        old_tier = [offer("same", source_tier_id="tier-bt12", break_unit="BT", break_quantity=12)]
        new_tier = [offer("same", source_tier_id="tier-cs12", break_unit="CS", supplier_qualifying_unit="CS", break_quantity=12)]
        result = compare_review_snapshots(
            previous, current, previous_tiers=old_tier, current_tiers=new_tier
        )
        self.assertIn("BT_CS_THRESHOLD_CHANGED", result["categories"])
        self.assertIn("ADDED_TIER", result["categories"])
        self.assertIn("MISSING_TIER_NOT_RETIREMENT", result["categories"])
        self.assertEqual(
            {item["kind"] for item in result["tier_changes"]},
            {"ADDED_TIER", "MISSING_TIER_NOT_RETIREMENT"},
        )

        moved_tier = compare_review_snapshots(
            [offer("same")],
            [offer("same")],
            previous_tiers=[offer("same", source_tier_id="bt12", break_unit="BT", break_quantity=12)],
            current_tiers=[offer("same", source_tier_id="bt24", break_unit="BT", break_quantity=24)],
        )
        self.assertEqual(
            {item["kind"] for item in moved_tier["tier_changes"]},
            {"ADDED_TIER", "MISSING_TIER_NOT_RETIREMENT"},
        )

        rejected = compare_review_snapshots(
            [offer("same", candidate_disposition="REJECTED_ATTRIBUTE_CONFLICT", mapping_status="REJECTED_ATTRIBUTE_CONFLICT")],
            [offer("same", candidate_disposition="REJECTED_ATTRIBUTE_CONFLICT", mapping_status="REJECTED_ATTRIBUTE_CONFLICT")],
        )
        self.assertIn("REJECTED_MATCH_RECURRED", rejected["categories"])
        self.assertEqual(rejected["operational_effects"]["mapping_approvals"], 0)

    def test_monthly_source_hash_change_is_not_merely_repagination(self):
        result = compare_review_snapshots(
            [offer("same", source_page=10, source_sha256="1" * 64)],
            [offer("same", source_page=11, source_sha256="2" * 64)],
        )
        self.assertIn("SOURCE_DOCUMENT_CHANGED", result["categories"])
        self.assertNotIn("SOURCE_REPAGINATED", result["categories"])

    def test_package_comparison_requires_compatible_complete_supplier_period_scope(self):
        def package(scope_changes: dict[str, object] | None = None) -> ReviewPackage:
            scope: dict[str, object] = {
                "comparison_contract": "comparison-v1",
                "identity_contract": "identity-v1",
                "lineage_family_sha256": "a" * 64,
                "supplier_scope_kind": "COMPLETE",
                "supplier_scope": ["Fixture Supplier"],
                "cohort_scope": "FIXTURE",
                "period_semantics": "MONTHLY",
                "period_id": "2026-09",
                "supplier_period_coverage_complete": True,
                "missing_supplier_periods": [],
                "channels": ["BOOK"],
                "territories": ["TEST"],
                "simulated": True,
            }
            scope.update(scope_changes or {})
            table = PackageTable(
                "offers", "offers", "jsonl", (offer("same"),), "b" * 64, "c" * 64
            )
            return ReviewPackage(
                "portable:fixture", "SYNTHETIC", "fixture", "STRUCTURED_REPLAY",
                REVIEW_LABEL, 1, 1, "d" * 64, {"offers": table}, {}, (), (),
                {"readiness": {"structural_replay": "PASS"}, "snapshot_scope": scope},
            )

        previous = package()
        current = package({"period_id": "2026-10"})
        self.assertEqual(compare_review_packages(previous, current)["status"], "PASS")
        missing = package(
            {
                "period_id": "2026-10",
                "supplier_period_coverage_complete": False,
                "missing_supplier_periods": ["Fixture Supplier/2026-10"],
            }
        )
        blocked = compare_review_packages(previous, missing)
        self.assertEqual(blocked["status"], "NOT_COMPARABLE")
        self.assertIn("CURRENT_SUPPLIER_PERIOD_COVERAGE_INCOMPLETE", blocked["reason_codes"])
        self.assertEqual(blocked["change_claims"]["added"], [])
        incompatible = compare_review_packages(previous, package({"channels": ["DIRECT"]}))
        self.assertIn("CHANNEL_SCOPE_MISMATCH", incompatible["reason_codes"])
        actual = compare_review_packages(previous, package({"simulated": False}))
        self.assertIn("SIMULATION_SCOPE_MISMATCH", actual["reason_codes"])

    def test_v5_review_batches_bind_occurrences_sidecars_and_zero_authority(self):
        relationships = (
            {
                "variant_id": "1001", "supplier_name_raw": "Fixture Supplier",
                "source_offer_id": "book:standard", "supplier_code_exact": "001",
                "source_description_raw": "Fixture Standard", "source_package_type_raw": "STANDARD",
                "candidate_disposition": "PROPOSED_REVIEW_CANDIDATE", "preference": "UNDECIDED",
                "reviewed_shopify_units_per_case": 6, "reviewed_qualifying_units_per_case": None,
                "source_case_pack_raw": 12, "source_physical_count_raw": 12,
                "source_retail_pack_raw": 2, "source_size_raw": "750 ML",
                "source_file": "fixture.pdf", "source_page": 1, "source_sha256": "1" * 64,
                "source_period_raw": "2026-09", "source_territory": "TEST",
                "catalog_source_status": "PROPOSED MATCH", "source_split_fee_basis": None,
                "mapping_approved": False, "price_approved": False, "import_ready": False,
                "new_gift_sidecar_id": None,
            },
            {
                "variant_id": "1001", "supplier_name_raw": "Fixture Supplier",
                "source_offer_id": "book:gift", "supplier_code_exact": "GIFT-001",
                "source_description_raw": "Fixture Gift", "source_package_type_raw": "GIFT_PACK",
                "candidate_disposition": "SEARCH_LEAD_ONLY", "preference": "UNDECIDED",
                "reviewed_shopify_units_per_case": None, "reviewed_qualifying_units_per_case": None,
                "source_case_pack_raw": 6, "source_physical_count_raw": None,
                "source_retail_pack_raw": None, "source_size_raw": "750 ML + GIFT",
                "source_file": "fixture.pdf", "source_page": 2, "source_sha256": "1" * 64,
                "source_period_raw": "2026-09", "source_territory": "TEST",
                "catalog_source_status": "PROPOSED MATCH", "source_split_fee_basis": "UNRESOLVED",
                "mapping_approved": False, "price_approved": False, "import_ready": False,
                "new_gift_sidecar_id": "gift-1",
            },
        )
        sidecar = ({"variant_id": "1001", "source_offer_id": "book:gift", "relationship_id": "gift-1"},)
        tables = {
            "variant_offer_relationships_v5": PackageTable(
                "variant_offer_relationships_v5", "relationships", "jsonl", relationships,
                "2" * 64, "3" * 64,
            ),
            "conditional_gift_relationships_v5": PackageTable(
                "conditional_gift_relationships_v5", "gifts", "jsonl", sidecar,
                "4" * 64, "5" * 64,
            ),
        }
        package = ReviewPackage(
            "fixture", "V5_CHANGED_TABLES_AND_EVIDENCE", "v5-fixture",
            "BASELINE_REQUIRED", REVIEW_LABEL,
            2, 2, "6" * 64, tables, {}, (), ("ORIGINAL_SUPPLIER_PDFS",),
        )
        first = build_review_batches(package)
        second = build_review_batches(package)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(first[0]["offers"]), 2)
        gift = first[0]["offers"][0]
        self.assertEqual(gift["label"], "REVIEW PREVIEW — NOT APPROVED")
        self.assertEqual(gift["authority"], {
            "mapping_approved": False, "price_approved": False,
            "import_ready": False, "selection_created": False,
        })
        self.assertIn("CONDITIONAL_GIFT_REQUIRES_SEPARATE_REVIEW", gift["blockers"]["packaging"])
        self.assertEqual(gift["related_sidecar_records"][0]["table"], "conditional_gift_relationships_v5")
        document = report_document(package)
        self.assertEqual(document["review_batches"], first)
        self.assertEqual(document["package"]["source"], "v5:v5-fixture")
        self.assertEqual(
            document["offer_family"]["occurrence_storage"],
            "REVIEW_BATCHES_ONLY_NO_DUPLICATED_RAW_RECORDS",
        )
        self.assertEqual(document["offer_family"]["summary"]["source_rows"], 2)
        self.assertEqual(document["offer_family"]["summary"]["blocked_alternatives"], 2)
        self.assertNotIn("occurrences", document["offer_family"])
        self.assertNotIn("offer_families", document["offer_family"])
        self.assertEqual(document["operational_effects"]["database_writes"], 0)
        rendered = render_report_html(document)
        self.assertIn("Fixture Standard", rendered)
        self.assertIn("Fixture Gift", rendered)
        self.assertIn(first[0]["offers"][0]["offer_preview_fingerprint"], rendered)
        self.assertIn("<td>2</td>", rendered)
        self.assertNotIn("<script", rendered.lower())

    def test_interrupted_atomic_output_publishes_nothing_and_cleans_staging(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "report"
            original = report_cli._write_file_exclusive
            calls = 0

            def fail_second(path: Path, data: bytes) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic interrupted write")
                original(path, data)

            with patch.object(
                report_cli, "_write_file_exclusive", side_effect=fail_second
            ):
                with self.assertRaisesRegex(OSError, "synthetic interrupted write"):
                    report_cli.write_report_bundle(
                        output, b"{}\n", b"<p>fixture</p>\n"
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_output_mode_stdout_is_bounded_and_does_not_serialize_report_twice(self):
        class Stdout:
            def __init__(self) -> None:
                self.buffer = io.BytesIO()

        stdout = Stdout()
        report = {
            "label": REVIEW_LABEL,
            "status": "BASELINE_REQUIRED",
            "large_private_detail": "must-not-be-repeated-on-stdout",
        }
        with (
            patch.object(report_cli, "execute", return_value=report),
            patch.object(report_cli.sys, "stdout", stdout),
        ):
            self.assertEqual(
                report_cli.main(["fixture.zip", "--output", "fixture-report"]),
                0,
            )
        result = json.loads(stdout.buffer.getvalue())
        self.assertEqual(result["status"], "BASELINE_REQUIRED")
        self.assertTrue(result["report_written"])
        self.assertNotIn("large_private_detail", result)


if __name__ == "__main__":
    unittest.main()
