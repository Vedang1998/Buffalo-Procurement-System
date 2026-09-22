"""Synthetic and adversarial tests for the private research projection."""

from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import unittest
from unittest.mock import patch

from procurement_os.private_research_projection import (
    ABC_COHORT_EVIDENCE_CONTRACT,
    AUTHORITY_LABEL,
    CALCULATED_RESEARCH_ONLY,
    PROJECTION_CONTRACT,
    REAL_NUMERICAL_EVALUATION_NOT_RUN,
    PrivateResearchProjectionError,
    _iter_filtered_private_research_rows,
    build_private_research_projection,
    canonical_private_research_projection_bytes,
    filter_private_research_rows,
)


INTAKE_AUTHORITY = {
    "status": "REVIEW_ONLY",
    "approval_status": "UNAPPROVED",
    "operational_use": "PROHIBITED",
    "mapping_authority": False,
    "price_authority": False,
    "selection_authority": False,
    "inventory_authority": False,
    "forecast_authority": False,
    "procurement_authority": False,
    "shopify_write_authority": False,
    "po_authority": False,
}


ZERO_AUTHORITY = {
    "database_writes": 0,
    "mapping_approvals": 0,
    "price_approvals": 0,
    "selected_offers": 0,
    "activated_prices": 0,
    "inventory_writes": 0,
    "forecast_authorizations": 0,
    "shopify_writes": 0,
    "supplier_messages": 0,
    "po_actions": 0,
}


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def readdress_intake(value: dict[str, object]) -> dict[str, object]:
    value["intake_id"] = None
    value["intake_id"] = hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    ).hexdigest()
    return value


def abc_evidence(rows: list[dict[str, object]]) -> dict[str, object]:
    variant_ids = sorted(str(row["shopify_variant_id"]) for row in rows)
    historical_rows: list[dict[str, str]] = []
    for row in rows:
        try:
            revenue = Decimal(str(row.get("historical_revenue")))
            cogs = Decimal(str(row.get("historical_cogs")))
        except (InvalidOperation, ValueError):
            continue
        if revenue.is_finite() and cogs.is_finite() and revenue >= 0 and cogs >= 0:
            historical_rows.append(
                {
                    "variant_id": str(row["shopify_variant_id"]),
                    "historical_revenue": format(revenue, "f"),
                    "historical_cogs": format(cogs, "f"),
                }
            )
    historical_rows.sort(key=lambda item: item["variant_id"])
    exclusions: list[dict[str, str]] = []
    lookback_start = "2026-07-13"
    lookback_end = "2026-10-04"
    scope_id = hashlib.sha256(
        (
            json.dumps(
                {
                    "eligible_shopify_variant_ids": variant_ids,
                    "lookback_start": lookback_start,
                    "lookback_end": lookback_end,
                    "classification_period_days": 84,
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    return {
        "contract": ABC_COHORT_EVIDENCE_CONTRACT,
        "coverage_complete": True,
        "scope_id": scope_id,
        "lookback_start": lookback_start,
        "lookback_end": lookback_end,
        "classification_period_days": 84,
        "eligible_variant_count": len(variant_ids),
        "excluded_variant_count": 0,
        "eligible_variant_ids": variant_ids,
        "eligible_variant_ids_sha256": canonical_sha256(variant_ids),
        "excluded_variants": exclusions,
        "excluded_variants_sha256": canonical_sha256(exclusions),
        "catalog_variant_ids_sha256": canonical_sha256(variant_ids),
        "historical_evidence_sha256": canonical_sha256(historical_rows),
    }


def forecast_evidence(value: object = 2, *, reverse: bool = False) -> dict[str, object]:
    start = date(2026, 7, 13)
    observations = [
        {
            "business_date": (start + timedelta(days=offset)).isoformat(),
            "net_units": str(value),
            "inventory_state": "IN_STOCK",
        }
        for offset in range(84)
    ]
    if reverse:
        observations.reverse()
    return {
        "coverage_complete": True,
        "horizon_days": 7,
        "observations": observations,
    }


def hypothesis(
    variant_id: str,
    supplier_name: str,
    supplier_sku: str,
    unit_cost: str,
    **changes: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "shopify_variant_id": variant_id,
        "vendor_name": supplier_name,
        "supplier_sku": supplier_sku,
        "supplier_description": "Research bottle 750ML",
        "source_ref": "private-source-page-1",
        "mapping_status": "UNAPPROVED_HYPOTHESIS",
        "package_type": "STANDARD",
        "raw_pack": "6/750ML",
        "units_per_case": 6,
        "qualifying_units_per_case": 12,
        "break_unit": "BT",
        "break_quantity": 24,
        "current_unit_cost": unit_cost,
        "assortment_scope": "SAME_PRODUCT",
        "assortment_group": None,
        "allocated_excluded": False,
        "combo_excluded": True,
    }
    row.update(changes)
    return row


def variant(
    variant_id: str,
    *,
    hypotheses: list[dict[str, object]] | None = None,
    revenue: object = "100.00",
    cogs: object = "10.00",
    with_forecast: bool = False,
    **changes: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "shopify_variant_id": variant_id,
        "product_title": f"Product {variant_id}",
        "variant_title": "750ML",
        "shopify_sku": f"SHOP-{variant_id}",
        "barcode": f"BAR-{variant_id}",
        "current_retail_price": "20.00",
        "current_inventory_item_cost": "9.00",
        "target_margin_pct": "0.25",
        "historical_revenue": revenue,
        "historical_cogs": cogs,
        "available": "2",
        "incoming": "1",
        "raw_pack": "6/750ML",
        "units_per_case": 6,
        "qualifying_units_per_case": 12,
        "break_unit": "BT",
        "allocated_excluded": False,
        "combo_excluded": True,
        "one_bottle_policy": False,
        "supplier_hypotheses": hypotheses or [],
    }
    if with_forecast:
        row["forecast_evidence"] = forecast_evidence()
    row.update(changes)
    return row


def intake(rows: list[dict[str, object]], **changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "contract": "BUFFALO_PRIVATE_RESEARCH_INTAKE_V1",
        "data_mode": "PRIVATE_REAL_SOURCE_REVIEW",
        "authority": dict(INTAKE_AUTHORITY),
        "intake_id": None,
        "sources": {
            "private_catalog": {
                "source_name": "private catalog",
                "source_kind": "CATALOG",
                "sha256": "a" * 64,
                "row_count": len(rows),
                "coverage_status": "PRIVATE_RESEARCH",
                "run_id": "must-not-leak",
            }
        },
        "coverage": {
            "current_catalog_population": len(rows),
            "a1_original_review_population": 2000,
            "a1_historical_current_census_population": 2003,
            "abc_cohort": abc_evidence(rows),
        },
        "variants": rows,
        "vendors": [{"vendor_name": "Alpha"}, {"vendor_name": "Zulu"}],
        "limitations": ["Private evidence has no operational authority."],
        "zero_authority": dict(ZERO_AUTHORITY),
    }
    result.update(changes)
    return readdress_intake(result)


def nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value), set())
    return set()


class PrivateResearchProjectionTests(unittest.TestCase):
    def test_exact_variant_join_retains_every_hypothesis_without_cheapest_selection(self):
        expensive = hypothesis(
            "100", "Alpha", "ALPHA-1", "11.00", offer_id="SECRET-OFFER-ID"
        )
        cheapest = hypothesis(
            "100", "Zulu", "ZULU-1", "1.00", price_id="SECRET-PRICE-ID"
        )
        wrong = hypothesis(
            "999", "Wrong", "WRONG-1", "0.01", run_id="SECRET-RUN-ID"
        )
        projection = build_private_research_projection(
            intake([variant("100", hypotheses=[cheapest, wrong, expensive])])
        )

        self.assertEqual(projection["contract"], PROJECTION_CONTRACT)
        self.assertEqual(projection["authority"], AUTHORITY_LABEL)
        self.assertTrue(projection["research_only"])
        self.assertEqual(
            [row["supplier_name"] for row in projection["research_rows"]],
            ["Alpha", "Zulu"],
        )
        self.assertEqual(
            [
                row["hypothesis_current_unit_cost"]
                for row in projection["research_rows"]
            ],
            ["11.00", "1.00"],
        )
        self.assertTrue(
            all(
                row["selection_status"] == "NOT_RUN_NO_SELECTION_AUTHORITY"
                for row in projection["research_rows"]
            )
        )
        self.assertEqual(
            projection["unjoined_supplier_hypotheses"][0]["reason_code"],
            "HYPOTHESIS_EXACT_VARIANT_ID_MISMATCH",
        )
        self.assertEqual(
            projection["coverage_summary"],
            {
                "variant_count": 1,
                "exact_joined_supplier_hypothesis_count": 2,
                "unjoined_supplier_hypothesis_count": 1,
                "variants_with_missing_data": 1,
                "forecast_calculated_research_only_count": 0,
                "abc_calculated_research_only_count": 1,
                "economics_calculated_research_only_count": 2,
            },
        )
        serialized = canonical_private_research_projection_bytes(projection).decode()
        for secret in (
            "SECRET-OFFER-ID",
            "SECRET-PRICE-ID",
            "SECRET-RUN-ID",
            "must-not-leak",
        ):
            self.assertNotIn(secret, serialized)
        self.assertTrue(
            {
                "offer_id",
                "price_id",
                "run_id",
                "selected_offer_id",
                "recommendation_id",
                "purchase_order_id",
            }.isdisjoint(nested_keys(projection))
        )

    def test_a1_occurrences_and_mapping_blockers_remain_distinct_and_searchable(self):
        mapping_evidence = {
            "candidate_disposition": "REVIEW_REQUIRED",
            "blockers": {
                "identity": ["SOURCE_IDENTITY_UNRESOLVED"],
                "packaging": [],
                "program_price": ["NO_CURRENT_PRICE_OR_TIER_AUTHORITY"],
                "source_availability": ["SOURCE_BYTES_UNAVAILABLE"],
            },
            "relationship_record_sha256": "f" * 64,
            "offer_preview_fingerprint": "e" * 64,
        }
        first = hypothesis(
            "100",
            "Alpha",
            "A-1",
            "7.00",
            source_occurrence_id="source-occurrence-1",
            mapping_confidence={"status": "UNAPPROVED", "basis": "A1_REVIEW"},
            mapping_evidence=mapping_evidence,
        )
        second = copy.deepcopy(first)
        second["source_occurrence_id"] = "source-occurrence-2"
        projection = build_private_research_projection(
            intake([variant("100", hypotheses=[second, first])])
        )

        rows = projection["research_rows"]
        self.assertEqual(
            [row["source_occurrence_ref"] for row in rows],
            ["source-occurrence-1", "source-occurrence-2"],
        )
        self.assertNotEqual(rows[0], rows[1])
        self.assertEqual(
            rows[0]["unapproved_mapping_confidence"],
            {"basis": "A1_REVIEW", "status": "UNAPPROVED"},
        )
        self.assertEqual(
            rows[0]["unapproved_mapping_evidence"][
                "relationship_record_sha256"
            ],
            "f" * 64,
        )
        reason = (
            "A1_MAPPING_BLOCKER:program_price:"
            "NO_CURRENT_PRICE_OR_TIER_AUTHORITY"
        )
        self.assertIn(reason, rows[0]["unapproved_mapping_blocker_reasons"])
        self.assertIn(reason, rows[0]["missing_data_reasons"])
        self.assertIn(
            reason, projection["coverage_rows"][0]["missing_data_reasons"]
        )
        self.assertIn(reason, projection["owner_worksheet"][0]["reason_codes"])
        self.assertEqual(
            [
                row["source_occurrence_ref"]
                for row in filter_private_research_rows(
                    projection, query="source-occurrence-2"
                )
            ],
            ["source-occurrence-2"],
        )
        self.assertEqual(
            len(filter_private_research_rows(projection, status=reason)), 2
        )

    def test_missing_evidence_never_calls_numerical_calculators(self):
        sparse = {
            "shopify_variant_id": "100",
            "product_title": "Sparse",
            "variant_title": "750ML",
            "supplier_hypotheses": [],
        }
        source = intake([sparse])
        source["coverage"]["abc_cohort"]["coverage_complete"] = False
        readdress_intake(source)
        with (
            patch(
                "procurement_os.private_research_projection.plan_development_forecast"
            ) as forecast,
            patch(
                "procurement_os.private_research_projection.assign_gp_dollar_abc"
            ) as abc,
            patch("procurement_os.private_research_projection.target_cost") as target,
            patch(
                "procurement_os.private_research_projection.gross_margin_pct"
            ) as margin,
        ):
            projection = build_private_research_projection(source)

        forecast.assert_not_called()
        abc.assert_not_called()
        target.assert_not_called()
        margin.assert_not_called()
        row = projection["research_rows"][0]
        self.assertEqual(
            row["forecast"]["status"], REAL_NUMERICAL_EVALUATION_NOT_RUN
        )
        self.assertEqual(row["abc"]["status"], REAL_NUMERICAL_EVALUATION_NOT_RUN)
        self.assertEqual(
            row["economics"]["status"], REAL_NUMERICAL_EVALUATION_NOT_RUN
        )
        self.assertIn("HISTORICAL_COGS_MISSING_OR_INVALID", row["missing_data_reasons"])

    def test_complete_evidence_uses_existing_forecast_abc_and_economics(self):
        rows = [
            variant(
                "100",
                hypotheses=[hypothesis("100", "Alpha", "A-1", "7.00")],
                revenue="100.00",
                cogs="10.00",
                with_forecast=True,
            ),
            variant(
                "200",
                hypotheses=[hypothesis("200", "Zulu", "Z-1", "8.00")],
                revenue="100.00",
                cogs="90.00",
                with_forecast=True,
            ),
        ]
        with (
            patch(
                "procurement_os.private_research_projection.plan_development_forecast",
                wraps=__import__(
                    "procurement_os.development_forecast", fromlist=["plan_development_forecast"]
                ).plan_development_forecast,
            ) as forecast,
            patch(
                "procurement_os.private_research_projection.assign_gp_dollar_abc",
                wraps=__import__(
                    "procurement_os.development_forecast", fromlist=["assign_gp_dollar_abc"]
                ).assign_gp_dollar_abc,
            ) as abc,
            patch(
                "procurement_os.private_research_projection.target_cost",
                wraps=__import__("procurement_os.economics", fromlist=["target_cost"]).target_cost,
            ) as target,
        ):
            projection = build_private_research_projection(intake(rows))

        self.assertEqual(forecast.call_count, 2)
        abc.assert_called_once()
        self.assertEqual(target.call_count, 2)
        by_id = {row["shopify_variant_id"]: row for row in projection["research_rows"]}
        self.assertEqual(by_id["100"]["forecast"]["status"], CALCULATED_RESEARCH_ONLY)
        self.assertEqual(by_id["100"]["abc"]["abc_class"], "A")
        self.assertEqual(by_id["100"]["abc"]["gross_profit_dollars"], "90.00")
        self.assertEqual(by_id["200"]["abc"]["abc_class"], "B")
        self.assertEqual(by_id["200"]["abc"]["gross_profit_dollars"], "10.00")
        self.assertEqual(by_id["100"]["hypothesis_current_unit_cost"], "7.00")
        self.assertEqual(by_id["100"]["shopify_current_unit_cost_reference"], "9.00")
        self.assertEqual(by_id["100"]["historical_cogs"], "10.00")
        self.assertFalse(by_id["100"]["economics"]["historical_cogs_used"])
        cohort = abc.call_args.args[0]
        self.assertEqual(
            cohort["rows"],
            [
                {
                    "variant_id": "100",
                    "historical_revenue": "100.00",
                    "historical_cogs": "10.00",
                },
                {
                    "variant_id": "200",
                    "historical_revenue": "100.00",
                    "historical_cogs": "90.00",
                },
            ],
        )

    def test_current_unit_cost_never_substitutes_for_missing_historical_cogs(self):
        row = variant(
            "100",
            hypotheses=[hypothesis("100", "Alpha", "A-1", "1.00")],
            cogs=None,
            current_inventory_item_cost="0.50",
        )
        with patch(
            "procurement_os.private_research_projection.assign_gp_dollar_abc"
        ) as calculator:
            projection = build_private_research_projection(intake([row]))
        calculator.assert_not_called()
        research = projection["research_rows"][0]
        self.assertEqual(research["abc"]["status"], REAL_NUMERICAL_EVALUATION_NOT_RUN)
        self.assertIn(
            "ABC_HISTORICAL_COGS_INCOMPLETE",
            projection["abc_evaluation"]["reason_codes"],
        )
        self.assertEqual(research["shopify_current_unit_cost_reference"], "0.50")
        self.assertIsNone(research["historical_cogs"])

    def test_pack_qualifying_break_and_exclusion_values_are_not_collapsed(self):
        row = variant(
            "100",
            hypotheses=[
                hypothesis(
                    "100",
                    "Alpha",
                    "A-1",
                    "7.00",
                    raw_pack="12x50ML",
                    units_per_case=120,
                    qualifying_units_per_case=12,
                    break_unit="CS",
                    break_quantity=5,
                    allocated_excluded=True,
                    combo_excluded=False,
                )
            ],
            raw_pack="6x4",
            units_per_case=24,
            qualifying_units_per_case=6,
            break_unit="BT",
            allocated_excluded=False,
            combo_excluded=True,
        )
        research = build_private_research_projection(intake([row]))["research_rows"][0]
        self.assertEqual(
            (
                research["hypothesis_raw_pack"],
                research["hypothesis_shopify_sellable_units_per_case"],
                research["hypothesis_qualifying_units_per_case"],
                research["hypothesis_break_unit"],
                research["hypothesis_break_quantity"],
            ),
            ("12x50ML", 120, 12, "CS", 5),
        )
        self.assertEqual(
            (
                research["catalog_raw_pack"],
                research["catalog_shopify_sellable_units_per_case"],
                research["catalog_qualifying_units_per_case"],
                research["catalog_break_unit"],
            ),
            ("6x4", 24, 6, "BT"),
        )
        self.assertEqual(
            (
                research["hypothesis_allocated_excluded"],
                research["hypothesis_combo_excluded"],
                research["catalog_allocated_excluded"],
                research["catalog_combo_excluded"],
            ),
            (True, False, False, True),
        )

    def test_projection_and_owner_worksheet_are_order_invariant(self):
        first = variant(
            "100",
            hypotheses=[
                hypothesis("100", "Zulu", "Z-1", "4.00"),
                hypothesis("100", "Alpha", "A-1", "8.00"),
            ],
            with_forecast=True,
        )
        second = variant(
            "200",
            hypotheses=[hypothesis("200", "Beta", "B-1", "5.00")],
            revenue="50",
            cogs="25",
            with_forecast=True,
        )
        a = intake([first, second])
        b = copy.deepcopy(a)
        b["variants"].reverse()
        b["variants"][1]["supplier_hypotheses"].reverse()
        b["variants"][0]["forecast_evidence"]["observations"].reverse()
        readdress_intake(b)
        projection_a = build_private_research_projection(a)
        projection_b = build_private_research_projection(b)
        for field in (
            "coverage_rows",
            "research_rows",
            "owner_worksheet",
            "abc_evaluation",
            "coverage_summary",
        ):
            self.assertEqual(projection_a[field], projection_b[field])
        self.assertNotEqual(
            projection_a["intake"]["intake_id"],
            projection_b["intake"]["intake_id"],
        )
        self.assertEqual(
            [row["shopify_variant_id"] for row in projection_a["owner_worksheet"]],
            ["100", "200"],
        )
        self.assertIn(
            "do not choose an offer by lowest unit cost",
            projection_a["owner_worksheet"][0]["question"],
        )

    def test_identity_and_authority_inputs_fail_closed(self):
        duplicate = intake([variant("100"), variant("100")])
        with self.assertRaisesRegex(
            PrivateResearchProjectionError, "duplicate exact Shopify Variant ID"
        ):
            build_private_research_projection(duplicate)

        numeric = intake([variant("100")])
        numeric["variants"][0]["shopify_variant_id"] = 100
        readdress_intake(numeric)
        with self.assertRaisesRegex(PrivateResearchProjectionError, "exact string"):
            build_private_research_projection(numeric)

        authority = intake([variant("100")])
        authority["zero_authority"]["po_actions"] = 1
        readdress_intake(authority)
        with self.assertRaisesRegex(PrivateResearchProjectionError, "integer zero"):
            build_private_research_projection(authority)

        envelope_mutations = (
            ("contract", "SOMETHING_ELSE", "contract differs"),
            ("data_mode", "PUBLIC_OR_OPERATIONAL", "data mode differs"),
            ("authority", {**INTAKE_AUTHORITY, "mapping_authority": True}, "authority"),
            ("zero_authority", {"database_writes": 0}, "key inventory"),
            ("zero_authority", {**ZERO_AUTHORITY, "database_writes": False}, "integer zero"),
        )
        for field, value, message in envelope_mutations:
            with self.subTest(field=field, value=value):
                changed = intake([variant("100")])
                changed[field] = value
                with self.assertRaisesRegex(PrivateResearchProjectionError, message):
                    build_private_research_projection(changed)

    def test_content_address_rejects_tampering_that_retains_the_prior_id(self):
        source = intake([variant("100")])
        retained_id = source["intake_id"]
        source["variants"][0]["product_title"] = "Tampered after addressing"
        self.assertEqual(source["intake_id"], retained_id)
        with self.assertRaisesRegex(
            PrivateResearchProjectionError, "identity differs from its canonical content"
        ):
            build_private_research_projection(source)

    def test_nested_forecast_and_abc_reasons_reach_rows_filters_and_counts(self):
        source = intake(
            [
                variant(
                    "100",
                    hypotheses=[hypothesis("100", "Alpha", "A-1", "7")],
                    with_forecast=True,
                )
            ]
        )
        with (
            patch(
                "procurement_os.private_research_projection.plan_development_forecast",
                side_effect=ValueError("synthetic rejection"),
            ),
            patch(
                "procurement_os.private_research_projection.assign_gp_dollar_abc",
                return_value={"classification_status": "NOT_CONFIGURED"},
            ),
        ):
            projection = build_private_research_projection(source)

        expected = {
            "FORECAST_EVIDENCE_REJECTED_BY_DEVELOPMENT_CALCULATOR",
            "ABC_CLASSIFICATION_NOT_CALCULATED",
        }
        self.assertTrue(
            expected.issubset(projection["coverage_rows"][0]["missing_data_reasons"])
        )
        self.assertTrue(
            expected.issubset(projection["research_rows"][0]["missing_data_reasons"])
        )
        self.assertTrue(
            expected.issubset(projection["owner_worksheet"][0]["reason_codes"])
        )
        self.assertEqual(
            projection["coverage_summary"]["variants_with_missing_data"], 1
        )
        for reason in expected:
            self.assertEqual(
                [
                    row["shopify_variant_id"]
                    for row in filter_private_research_rows(
                        projection, status=reason
                    )
                ],
                ["100"],
            )

    def test_unapproved_price_ladder_is_preserved_but_never_used_as_economics(self):
        ladder = [
            {
                "level_type": "BREAK",
                "break_quantity": 24,
                "break_unit": "BT",
                "case_price": "168.00",
                "unit_price": "7.00",
                "source_tier_id": "OPER-TIER-1",
            }
        ]
        projection = build_private_research_projection(
            intake(
                [
                    variant(
                        "100",
                        hypotheses=[
                            hypothesis(
                                "100",
                                "Alpha",
                                "A-1",
                                "7.00",
                                unapproved_price_ladder_evidence=ladder,
                            )
                        ],
                    )
                ]
            )
        )
        row = projection["research_rows"][0]
        self.assertEqual(
            row["unapproved_price_ladder_evidence"],
            [
                {
                    "break_quantity": 24,
                    "break_unit": "BT",
                    "case_price": "168.00",
                    "level_type": "BREAK",
                    "unit_price": "7.00",
                }
            ],
        )
        self.assertEqual(
            row["economics"]["calculation_scope"],
            "UNIT_MARGIN_DIAGNOSTIC_ONLY",
        )
        self.assertFalse(row["economics"]["source_ladder_used"])
        self.assertEqual(
            row["economics"]["pack_break_economics_status"],
            REAL_NUMERICAL_EVALUATION_NOT_RUN,
        )
        self.assertNotIn(
            "OPER-TIER-1",
            canonical_private_research_projection_bytes(projection).decode(),
        )

    def test_native_raw_inventory_is_preserved_and_explicitly_untrusted(self):
        native = variant(
            "100",
            available="9",
            incoming=None,
            on_hand="13",
            committed="4",
            raw_incoming="6",
            raw_incoming_trust="UNTRUSTED_CAPTURE_ONLY",
            current_inventory_item_cost_currency="USD",
            inventory_evidence={
                "product_updated_at": "2026-09-19T12:00:00Z",
                "locations": [
                    {
                        "location_id": "gid://shopify/Location/501",
                        "location_name": "Main",
                        "available": "9",
                        "on_hand": "13",
                        "committed": "4",
                        "raw_incoming": "6",
                    }
                ],
            },
            sales_history={
                "start_date": "2026-07-12",
                "end_date": "2026-07-13",
                "day_count": 2,
                "coverage_complete": True,
                "observation_basis": "ATTESTED_COMPLETE_DAYS",
                "net_units_series": ["1", "0"],
                "net_revenue_series": ["20", "0"],
                "historical_cogs_series": ["9", "0"],
                "gross_sales_series": ["20", "0"],
                "returns_series": ["0", "0"],
                "source_gross_profit_series": ["11", "0"],
            },
        )
        projection = build_private_research_projection(intake([native]))
        coverage = projection["coverage_rows"][0]
        self.assertEqual(
            (
                coverage["available"],
                coverage["on_hand"],
                coverage["committed"],
                coverage["incoming"],
                coverage["raw_incoming"],
                coverage["raw_incoming_trust"],
                coverage["raw_incoming_operational_use"],
                coverage["current_inventory_item_cost_currency"],
            ),
            (
                "9",
                "13",
                "4",
                None,
                "6",
                "UNTRUSTED_CAPTURE_ONLY",
                "PROHIBITED_UNTRUSTED_CAPTURE_ONLY",
                "USD",
            ),
        )
        self.assertEqual(
            coverage["inventory_evidence"]["locations"][0]["location_name"],
            "Main",
        )
        self.assertNotIn(
            "location_id", coverage["inventory_evidence"]["locations"][0]
        )
        self.assertEqual(
            coverage["historical_sales_evidence"]["net_units_series"],
            ["1", "0"],
        )
        self.assertEqual(
            coverage["historical_sales_evidence"]["gross_sales_series"],
            ["20", "0"],
        )
        self.assertRegex(
            coverage["historical_sales_evidence_sha256"], r"^[0-9a-f]{64}$"
        )
        research = projection["research_rows"][0]
        self.assertEqual(research["catalog_raw_incoming"], "6")
        self.assertEqual(
            research["catalog_raw_incoming_operational_use"],
            "PROHIBITED_UNTRUSTED_CAPTURE_ONLY",
        )
        self.assertEqual(
            research["catalog_historical_sales_evidence_sha256"],
            coverage["historical_sales_evidence_sha256"],
        )

    def test_abc_requires_exact_membership_and_hash_proof(self):
        rows = [variant("100"), variant("200")]
        source = intake(rows)
        scope = source["coverage"]["abc_cohort"]
        scope["eligible_variant_ids"] = ["100"]
        scope["eligible_variant_ids_sha256"] = canonical_sha256(["100"])
        readdress_intake(source)
        with patch(
            "procurement_os.private_research_projection.assign_gp_dollar_abc"
        ) as calculator:
            projection = build_private_research_projection(source)
        calculator.assert_not_called()
        self.assertEqual(
            projection["abc_evaluation"]["status"],
            REAL_NUMERICAL_EVALUATION_NOT_RUN,
        )
        self.assertIn(
            "ABC_COHORT_PARTITION_DIFFERS_FROM_CURRENT_CATALOG",
            projection["abc_evaluation"]["reason_codes"],
        )

        source = intake(rows)
        source["coverage"]["abc_cohort"]["historical_evidence_sha256"] = "0" * 64
        readdress_intake(source)
        with patch(
            "procurement_os.private_research_projection.assign_gp_dollar_abc"
        ) as calculator:
            projection = build_private_research_projection(source)
        calculator.assert_not_called()
        self.assertIn(
            "ABC_HISTORICAL_EVIDENCE_SHA256_DIFFERS",
            projection["abc_evaluation"]["reason_codes"],
        )

        source = intake(rows)
        source["coverage"]["abc_cohort"].update(
            {
                "scope_id": "forged-scope",
                "eligible_variant_count": 999,
                "excluded_variant_count": 999,
            }
        )
        readdress_intake(source)
        projection = build_private_research_projection(source)
        self.assertEqual(
            projection["abc_evaluation"]["status"],
            REAL_NUMERICAL_EVALUATION_NOT_RUN,
        )
        self.assertTrue(
            {
                "ABC_SCOPE_ID_DIFFERS",
                "ABC_ELIGIBLE_COUNT_DIFFERS",
                "ABC_EXCLUDED_COUNT_DIFFERS",
            }.issubset(projection["abc_evaluation"]["reason_codes"])
        )

    def test_abc_exact_eligible_and_excluded_partition_matches_producer(self):
        rows = [variant("100"), variant("200")]
        source = intake(rows)
        eligible = ["100"]
        exclusions = [
            {"variant_id": "200", "reason_code": "PRODUCT_STATUS_ARCHIVED"}
        ]
        historical = [
            {
                "variant_id": "100",
                "historical_revenue": "100.00",
                "historical_cogs": "10.00",
            }
        ]
        source["coverage"]["abc_cohort"].update(
            {
                "scope_id": hashlib.sha256(
                    (
                        json.dumps(
                            {
                                "eligible_shopify_variant_ids": eligible,
                                "lookback_start": "2026-07-13",
                                "lookback_end": "2026-10-04",
                                "classification_period_days": 84,
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                ).hexdigest(),
                "eligible_variant_count": 1,
                "excluded_variant_count": 1,
                "eligible_variant_ids": eligible,
                "eligible_variant_ids_sha256": canonical_sha256(eligible),
                "excluded_variants": exclusions,
                "excluded_variants_sha256": canonical_sha256(exclusions),
                "catalog_variant_ids_sha256": canonical_sha256(["100", "200"]),
                "historical_evidence_sha256": canonical_sha256(historical),
            }
        )
        readdress_intake(source)
        projection = build_private_research_projection(source)
        by_id = {row["shopify_variant_id"]: row for row in projection["coverage_rows"]}
        self.assertEqual(by_id["100"]["abc_status"], CALCULATED_RESEARCH_ONLY)
        excluded = next(
            row for row in projection["research_rows"]
            if row["shopify_variant_id"] == "200"
        )
        self.assertEqual(
            excluded["abc"]["status"], REAL_NUMERICAL_EVALUATION_NOT_RUN
        )
        self.assertEqual(
            excluded["abc"]["reason_codes"],
            ["ABC_COHORT_EXCLUDED:PRODUCT_STATUS_ARCHIVED"],
        )
        self.assertIn(
            "ABC_COHORT_EXCLUDED:PRODUCT_STATUS_ARCHIVED",
            excluded["missing_data_reasons"],
        )

    def test_invalid_structural_and_zero_cost_evidence_never_runs_economics(self):
        invalid = hypothesis(
            "100",
            "Alpha",
            "A-1",
            "0",
            raw_pack=" ",
            units_per_case=0,
            qualifying_units_per_case="1.5",
            break_unit="ZZ",
            break_quantity=-5,
        )
        with patch(
            "procurement_os.private_research_projection.target_cost"
        ) as calculator:
            projection = build_private_research_projection(
                intake([variant("100", hypotheses=[invalid])])
            )
        calculator.assert_not_called()
        row = projection["research_rows"][0]
        self.assertEqual(
            row["economics"]["status"], REAL_NUMERICAL_EVALUATION_NOT_RUN
        )
        self.assertTrue(
            {
                "HYPOTHESIS_RAW_PACK_MISSING_OR_INVALID",
                "HYPOTHESIS_SELLABLE_UNITS_PER_CASE_MISSING_OR_INVALID",
                "HYPOTHESIS_QUALIFYING_UNITS_PER_CASE_MISSING_OR_INVALID",
                "HYPOTHESIS_BREAK_UNIT_MISSING_OR_INVALID",
                "HYPOTHESIS_BREAK_QUANTITY_MISSING_OR_INVALID",
                "SUPPLIER_HYPOTHESIS_CURRENT_UNIT_COST_MISSING_OR_INVALID",
            }.issubset(row["missing_data_reasons"])
        )

    def test_rejected_hypothesis_order_is_fully_deterministic(self):
        first = hypothesis(
            "wrong",
            "Alpha",
            "SAME",
            "8.00",
            source_ref="page-b",
        )
        second = hypothesis(
            "wrong",
            "Alpha",
            "SAME",
            "7.00",
            source_ref="page-a",
        )
        a = build_private_research_projection(
            intake([variant("100", hypotheses=[first, second])])
        )
        b = build_private_research_projection(
            intake([variant("100", hypotheses=[second, first])])
        )
        self.assertEqual(
            a["unjoined_supplier_hypotheses"],
            b["unjoined_supplier_hypotheses"],
        )
        self.assertEqual(a["owner_worksheet"], b["owner_worksheet"])
        self.assertEqual(
            [item["source_ref"] for item in a["unjoined_supplier_hypotheses"]],
            ["page-a", "page-b"],
        )

    def test_declared_current_and_a1_coverage_reconcile_without_fixed_counts(self):
        source = intake([variant("100"), variant("200")])
        source["coverage"].update(
            {
                "a1_original_review_population": 5,
                "a1_variants_joined_to_current_catalog": 2,
                "a1_variants_not_in_current_catalog": 3,
                "current_catalog_variants_without_a1_review": 0,
            }
        )
        readdress_intake(source)
        projection = build_private_research_projection(source)
        self.assertEqual(
            projection["declared_coverage"]["a1_variants_not_in_current_catalog"],
            3,
        )
        drifted = copy.deepcopy(source)
        drifted["coverage"]["current_catalog_variants_without_a1_review"] = 1
        readdress_intake(drifted)
        with self.assertRaisesRegex(
            PrivateResearchProjectionError,
            "current catalog population does not reconcile",
        ):
            build_private_research_projection(drifted)
        drifted = copy.deepcopy(source)
        drifted["coverage"]["a1_variants_not_in_current_catalog"] = 4
        readdress_intake(drifted)
        with self.assertRaisesRegex(
            PrivateResearchProjectionError, "A1 review population does not reconcile"
        ):
            build_private_research_projection(drifted)

    def test_pure_query_vendor_and_status_filter_keeps_projection_hash(self):
        projection = build_private_research_projection(
            intake(
                [
                    variant(
                        "100",
                        hypotheses=[hypothesis("100", "Alpha", "A-1", "7")],
                    ),
                    variant(
                        "200",
                        hypotheses=[hypothesis("200", "Zulu", "Z-1", "8")],
                    ),
                ]
            )
        )
        digest = projection["projection_sha256"]
        self.assertEqual(
            [row["shopify_variant_id"] for row in filter_private_research_rows(
                projection, query="product 200"
            )],
            ["200"],
        )
        self.assertEqual(
            [row["supplier_name"] for row in filter_private_research_rows(
                projection, vendor="Alpha"
            )],
            ["Alpha"],
        )
        self.assertEqual(
            len(filter_private_research_rows(projection, status="MISSING_DATA")),
            2,
        )
        self.assertEqual(projection["projection_sha256"], digest)
        with self.assertRaisesRegex(PrivateResearchProjectionError, "must be strings"):
            filter_private_research_rows(projection, query=1)  # type: ignore[arg-type]

    def test_validated_filter_core_matches_public_semantics_and_public_refuses_tamper(self):
        projection = build_private_research_projection(
            intake(
                [
                    variant(
                        "100",
                        hypotheses=[hypothesis("100", "Alpha", "A-1", "7")],
                    ),
                    variant(
                        "200",
                        hypotheses=[hypothesis("200", "Zulu", "Z-1", "8")],
                    ),
                ]
            )
        )
        filters = (
            {},
            {"query": "product 200"},
            {"vendor": "Alpha"},
            {"status": "MISSING_DATA"},
            {"query": "no match"},
        )
        for selected_filters in filters:
            with self.subTest(filters=selected_filters):
                public = filter_private_research_rows(
                    projection,
                    **selected_filters,
                )
                private = [
                    dict(row)
                    for row in _iter_filtered_private_research_rows(
                        projection,
                        **selected_filters,
                    )
                ]
                self.assertEqual(private, public)
                self.assertEqual(
                    [row["shopify_variant_id"] for row in private],
                    [row["shopify_variant_id"] for row in public],
                )

        forged = copy.deepcopy(projection)
        forged["research_rows"][0]["supplier_name"] = "Forged Supplier"
        with self.assertRaisesRegex(PrivateResearchProjectionError, "SHA differs"):
            filter_private_research_rows(forged)

    def test_projection_sha_binds_every_research_field(self):
        projection = build_private_research_projection(
            intake([variant("100", hypotheses=[hypothesis("100", "Alpha", "A", "7")])])
        )
        self.assertRegex(projection["projection_sha256"], r"^[0-9a-f]{64}$")
        changed = copy.deepcopy(projection)
        changed["research_rows"][0]["supplier_name"] = "Changed"
        with self.assertRaisesRegex(PrivateResearchProjectionError, "SHA differs"):
            canonical_private_research_projection_bytes(changed)
        self.assertEqual(
            json.loads(canonical_private_research_projection_bytes(projection)),
            projection,
        )


if __name__ == "__main__":
    unittest.main()
