"""Offline/CSV safety tests for private research projection exports."""

from __future__ import annotations

import ast
import copy
import csv
import hashlib
import io
import json
from pathlib import Path
import unittest

from procurement_os.private_research_projection import (
    AUTHORITY_LABEL,
    PROJECTION_CONTRACT,
    PrivateResearchProjectionError,
    build_private_research_projection,
    canonical_private_research_projection_bytes,
    render_private_research_csv,
    render_private_research_html,
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


def export_intake(*, formula_values: bool = False) -> dict[str, object]:
    supplier_names = (
        [
            "=CMD()",
            "  +SUM(1,1)",
            "-1+1",
            "@IMPORTDATA(x)",
            "\v=VT_FORMULA()",
            "\f@FF_FORMULA()",
        ]
        if formula_values
        else ["Alpha <script>alert(1)</script>"]
    )
    hypotheses = [
        {
            "shopify_variant_id": "100",
            "vendor_name": supplier_name,
            "supplier_sku": f"={index}+1" if formula_values else "SKU<&>",
            "supplier_description": "<img src=x onerror=alert(1)>",
            "source_ref": "private-page-1",
            "source_occurrence_id": (
                f"=OCC-{index}" if formula_values else f"A1-OCC-{index}"
            ),
            "mapping_status": "UNAPPROVED_HYPOTHESIS",
            "mapping_confidence": {"status": "UNAPPROVED", "basis": "A1"},
            "mapping_evidence": {
                "candidate_disposition": "REVIEW_REQUIRED",
                "blockers": {
                    "identity": ["SOURCE_IDENTITY_UNRESOLVED"],
                    "packaging": [],
                },
                "relationship_record_sha256": "f" * 64,
            },
            "package_type": "STANDARD",
            "raw_pack": "12/750ML",
            "units_per_case": 12,
            "qualifying_units_per_case": 12,
            "break_unit": "BT",
            "break_quantity": 24,
            "current_unit_cost": "8.00",
            "allocated_excluded": False,
            "combo_excluded": True,
            "unapproved_price_ladder_evidence": [
                {
                    "level_type": "BREAK",
                    "break_quantity": 24,
                    "break_unit": "BT",
                    "case_price": "96.00",
                    "unit_price": "8.00",
                    "source_tier_id": f"OPER-TIER-{index}",
                }
            ],
            "offer_id": f"OPER-OFFER-{index}",
            "source_price_id": f"OPER-PRICE-{index}",
        }
        for index, supplier_name in enumerate(supplier_names, start=1)
    ]
    result: dict[str, object] = {
        "contract": "BUFFALO_PRIVATE_RESEARCH_INTAKE_V1",
        "data_mode": "PRIVATE_REAL_SOURCE_REVIEW",
        "authority": dict(INTAKE_AUTHORITY),
        "intake_id": None,
        "sources": {
            "private_catalog": {
                "source_name": "<source & evidence>",
                "source_kind": "PRIVATE",
                "sha256": "b" * 64,
                "row_count": 1,
                "coverage_status": "RESEARCH_ONLY",
                "recommendation_id": "OPER-REC-1",
            }
        },
        "coverage": {
            "current_catalog_population": 1,
            "a1_original_review_population": 2000,
            "a1_historical_current_census_population": 2003,
            "abc_cohort": {"coverage_complete": False},
            "purchase_order_id": "OPER-PO-1",
            "vendor_id": "OPER-VENDOR-1",
            "price_book_batch_id": "OPER-BATCH-1",
            "selection_event_id": "OPER-EVENT-1",
        },
        "variants": [
            {
                "shopify_variant_id": "100",
                "product_title": "<script>alert('product')</script>",
                "variant_title": "=2+2" if formula_values else "750ML & gift",
                "shopify_sku": "@SHOP" if formula_values else "SHOP-100",
                "barcode": "BAR-100",
                "current_retail_price": "20.00",
                "current_inventory_item_cost": "9.00",
                "current_inventory_item_cost_currency": "USD",
                "target_margin_pct": "0.25",
                "historical_revenue": "100.00",
                "historical_cogs": "50.00",
                "available": "2",
                "incoming": "1",
                "on_hand": "5",
                "committed": "3",
                "raw_incoming": "7",
                "raw_incoming_trust": "UNTRUSTED_CAPTURE_ONLY",
                "inventory_evidence": {
                    "variant_updated_at": "2026-09-19T12:00:00Z",
                    "locations": [
                        {
                            "location_id": "OPER-LOCATION-1",
                            "inventory_level_id": "OPER-LEVEL-1",
                            "location_name": "Main",
                            "available": "2",
                            "on_hand": "5",
                            "committed": "3",
                            "raw_incoming": "7",
                        }
                    ],
                },
                "sales_history": {
                    "start_date": "2026-09-18",
                    "end_date": "2026-09-19",
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
                "raw_pack": "6x4",
                "units_per_case": 24,
                "qualifying_units_per_case": 6,
                "break_unit": "CS",
                "allocated_excluded": False,
                "combo_excluded": True,
                "supplier_hypotheses": hypotheses,
                "mapping_id": "OPER-MAPPING-1",
                "selected_offer_id": "OPER-SELECTED-1",
                "run_id": "OPER-RUN-1",
            }
        ],
        "vendors": [{"vendor_name": name} for name in supplier_names],
        "limitations": [
            "Text mentioning https://example.invalid is inert and never linked."
        ],
        "zero_authority": dict(ZERO_AUTHORITY),
    }
    return readdress_intake(result)


class PrivateResearchExportTests(unittest.TestCase):
    def test_html_and_csv_bind_the_same_projection_sha_and_authority(self):
        projection = build_private_research_projection(export_intake())
        rendered_html = render_private_research_html(projection)
        rendered_csv = render_private_research_csv(projection)
        digest = projection["projection_sha256"]

        self.assertIn(digest, rendered_html)
        self.assertIn(digest, rendered_csv)
        self.assertIn(PROJECTION_CONTRACT, rendered_html)
        self.assertIn(PROJECTION_CONTRACT, rendered_csv)
        self.assertIn(AUTHORITY_LABEL, rendered_html)
        parsed = list(csv.DictReader(io.StringIO(rendered_csv)))
        self.assertGreaterEqual(len(parsed), 4)
        self.assertTrue(all(row["projection_sha256"] == digest for row in parsed))
        self.assertTrue(all(row["authority"] == AUTHORITY_LABEL for row in parsed))
        self.assertEqual(parsed[0]["row_type"], "PROJECTION")
        self.assertIn("COVERAGE", {row["row_type"] for row in parsed})
        self.assertIn("RESEARCH", {row["row_type"] for row in parsed})
        self.assertIn("OWNER_WORKSHEET", {row["row_type"] for row in parsed})
        research = next(row for row in parsed if row["row_type"] == "RESEARCH")
        self.assertEqual(
            (
                research["hypothesis_raw_pack"],
                research["hypothesis_shopify_sellable_units_per_case"],
                research["hypothesis_qualifying_units_per_case"],
                research["hypothesis_break_unit"],
                research["hypothesis_allocated_excluded"],
                research["hypothesis_combo_excluded"],
            ),
            ("12/750ML", "12", "12", "BT", "FALSE", "TRUE"),
        )
        self.assertEqual(
            (
                research["catalog_raw_pack"],
                research["catalog_shopify_sellable_units_per_case"],
                research["catalog_qualifying_units_per_case"],
                research["catalog_break_unit"],
                research["catalog_allocated_excluded"],
                research["catalog_combo_excluded"],
            ),
            ("6x4", "24", "6", "CS", "FALSE", "TRUE"),
        )
        self.assertEqual(
            (
                research["catalog_available"],
                research["catalog_on_hand"],
                research["catalog_committed"],
                research["catalog_incoming"],
                research["catalog_raw_incoming"],
                research["catalog_raw_incoming_trust"],
                research["catalog_raw_incoming_operational_use"],
                research["current_inventory_item_cost_currency"],
                research["economics_scope"],
            ),
            (
                "2",
                "5",
                "3",
                "1",
                "7",
                "UNTRUSTED_CAPTURE_ONLY",
                "PROHIBITED_UNTRUSTED_CAPTURE_ONLY",
                "USD",
                "UNIT_MARGIN_DIAGNOSTIC_ONLY",
            ),
        )
        self.assertIn(
            '"case_price":"96.00"',
            research["unapproved_price_ladder_evidence"],
        )
        self.assertEqual(research["source_occurrence_ref"], "A1-OCC-1")
        self.assertIn(
            '"candidate_disposition":"REVIEW_REQUIRED"',
            research["unapproved_mapping_evidence"],
        )
        self.assertIn(
            "A1_MAPPING_BLOCKER:identity:SOURCE_IDENTITY_UNRESOLVED",
            research["unapproved_mapping_blocker_reasons"],
        )
        self.assertNotIn("source_tier_id", research["unapproved_price_ladder_evidence"])
        inventory = json.loads(research["catalog_inventory_evidence"])
        self.assertEqual(inventory["locations"][0]["location_name"], "Main")
        self.assertNotIn("location_id", inventory["locations"][0])
        coverage_row = next(row for row in parsed if row["row_type"] == "COVERAGE")
        sales_evidence = json.loads(
            coverage_row["catalog_historical_sales_evidence"]
        )
        self.assertEqual(sales_evidence["net_units_series"], ["1", "0"])
        self.assertEqual(sales_evidence["gross_sales_series"], ["20", "0"])
        self.assertRegex(
            coverage_row["catalog_historical_sales_evidence_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            research["catalog_historical_sales_evidence_sha256"],
            coverage_row["catalog_historical_sales_evidence_sha256"],
        )
        for header in (
            "Hypothesis raw pack",
            "Hypothesis allocated excluded",
            "Catalog raw pack",
            "Catalog allocated excluded",
            "Raw incoming (untrusted capture only)",
            "Raw incoming operational use",
            "Unapproved source ladder evidence",
            "Source occurrence reference",
            "Unapproved mapping evidence",
            "Unapproved mapping blocker reasons",
            "Economics scope",
            "Historical daily sales evidence SHA-256",
            "Historical daily sales evidence",
        ):
            self.assertIn(header, rendered_html)

    def test_html_is_escaped_static_and_offline_by_construction(self):
        projection = build_private_research_projection(export_intake())
        rendered = render_private_research_html(projection)

        self.assertIn("Content-Security-Policy", rendered)
        for directive in (
            "default-src 'none'",
            "script-src 'none'",
            "connect-src 'none'",
            "object-src 'none'",
            "frame-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
        ):
            self.assertIn(directive, rendered)
        self.assertNotIn("<script>", rendered.lower())
        self.assertNotIn("<img ", rendered.lower())
        self.assertNotIn(" href=", rendered.lower())
        self.assertNotIn(" src=", rendered.lower())
        self.assertNotIn("onerror=alert", rendered.lower().replace("&gt;", ""))
        self.assertIn("&lt;script&gt;alert(&#x27;product&#x27;)&lt;/script&gt;", rendered)
        self.assertIn("Alpha &lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn("https://example.invalid", rendered)

    def test_csv_neutralizes_every_formula_leading_display_cell(self):
        projection = build_private_research_projection(
            export_intake(formula_values=True)
        )
        rows = list(csv.DictReader(io.StringIO(render_private_research_csv(projection))))
        research = [row for row in rows if row["row_type"] == "RESEARCH"]
        self.assertEqual(len(research), 6)
        for row in research:
            self.assertTrue(row["supplier_name"].startswith("'"), row)
            self.assertTrue(row["supplier_sku"].startswith("'"), row)
            self.assertTrue(row["variant_title"].startswith("'"), row)
        for row in rows:
            for value in row.values():
                stripped = value.lstrip(" \t\r\n\v\f")
                self.assertFalse(
                    stripped.startswith(("=", "+", "-", "@")),
                    (row["row_type"], value),
                )

    def test_renderers_are_deterministic_and_reject_projection_drift(self):
        projection = build_private_research_projection(export_intake())
        projection_bytes = canonical_private_research_projection_bytes(projection)
        html_bytes = render_private_research_html(projection).encode("utf-8")
        csv_bytes = render_private_research_csv(projection).encode("utf-8")
        self.assertEqual(
            hashlib.sha256(projection_bytes).hexdigest(),
            "8259e89152cbb7ed029a4567be2cdd32418a3961154aea3c60351192124749af",
        )
        self.assertEqual(
            hashlib.sha256(html_bytes).hexdigest(),
            "1e61a83128703c62c915039ebd28a390e507a36db62fda0408660288fd971eb6",
        )
        self.assertEqual(
            hashlib.sha256(csv_bytes).hexdigest(),
            "96bc514ef0b59f0dd448b86eaf250e871d5951fc48d3cf68755688dc08f5cb6f",
        )
        self.assertEqual(
            render_private_research_html(projection),
            render_private_research_html(copy.deepcopy(projection)),
        )
        self.assertEqual(
            render_private_research_csv(projection),
            render_private_research_csv(copy.deepcopy(projection)),
        )
        self.assertEqual(
            canonical_private_research_projection_bytes(projection),
            canonical_private_research_projection_bytes(copy.deepcopy(projection)),
        )
        drifted = copy.deepcopy(projection)
        drifted["owner_worksheet"][0]["owner_response"] = "approve"
        for renderer in (render_private_research_html, render_private_research_csv):
            with self.assertRaisesRegex(PrivateResearchProjectionError, "SHA differs"):
                renderer(drifted)

    def test_operational_identifiers_never_reach_either_export(self):
        projection = build_private_research_projection(export_intake())
        outputs = (
            canonical_private_research_projection_bytes(projection).decode(),
            render_private_research_html(projection),
            render_private_research_csv(projection),
        )
        for output in outputs:
            for marker in (
                "OPER-OFFER",
                "OPER-PRICE",
                "OPER-REC",
                "OPER-PO",
                "OPER-MAPPING",
                "OPER-SELECTED",
                "OPER-RUN",
                "OPER-VENDOR",
                "OPER-BATCH",
                "OPER-EVENT",
                "OPER-TIER",
                "OPER-LOCATION",
                "OPER-LEVEL",
            ):
                self.assertNotIn(marker, output)

    def test_empty_research_projection_still_exports_hash_bound_metadata(self):
        source = export_intake()
        source["variants"] = []
        source["coverage"]["current_catalog_population"] = 0
        readdress_intake(source)
        projection = build_private_research_projection(source)
        csv_rows = list(
            csv.DictReader(io.StringIO(render_private_research_csv(projection)))
        )
        self.assertEqual(len(csv_rows), 1)
        self.assertEqual(csv_rows[0]["row_type"], "PROJECTION")
        self.assertEqual(
            csv_rows[0]["projection_sha256"], projection["projection_sha256"]
        )
        rendered = render_private_research_html(projection)
        self.assertIn(projection["projection_sha256"], rendered)
        self.assertIn(AUTHORITY_LABEL, rendered)

    def test_module_import_surface_excludes_operational_subsystems(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "procurement_os"
            / "private_research_projection.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = (
            "mapping",
            "price_book",
            "pricing",
            "recommendations",
            "monday",
            "draft_po",
            "po_csv",
            "po_ledger",
        )
        self.assertFalse(
            [name for name in imports if any(item in name for item in forbidden)],
            imports,
        )
        source = module_path.read_text(encoding="utf-8")
        self.assertNotIn("os.environ", source)
        self.assertNotIn("datetime.now", source)
        self.assertNotIn("Path(", source)


if __name__ == "__main__":
    unittest.main()
