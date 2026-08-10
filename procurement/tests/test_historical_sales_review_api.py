"""Focused safety and rendering tests for the Phase 4 historical-sales review UI."""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from procurement_os import api


class FakeConnectionContext:
    def __init__(self):
        self.connection = object()

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


def review_item():
    return {
        "source_key": "sku:<script>alert('source')</script>",
        "source_variant_id": "0",
        "historical_sku": "OLD<&SKU",
        "historical_product_title": "Historical <Bottle>",
        "historical_variant_title": "750ML 'Special'",
        "first_sale_date": "2024-11-28",
        "last_sale_date": "2026-08-10",
        "affected_raw_rows": 12,
        "net_units": "7.0000",
        "absolute_unit_magnitude": "9.0000",
        "net_sales": "101.25",
        "resolution_status": "AMBIGUOUS",
        "materiality": "MATERIAL",
        "evidence": {"exact historical SKU": True},
        "conflicts": ["duplicate current SKU"],
        "candidate_canonical_variants": [
            {
                "canonical_variant_id": "222",
                "product_title": "Candidate One",
                "variant_title": "750ML",
                "sku": "CURRENT-1",
                "evidence": ["exact normalized product and size"],
                "conflicts": ["SKU is duplicated"],
            },
            {
                "canonical_variant_id": "333",
                "product_title": "Candidate Two",
                "variant_title": "1L",
                "sku": "CURRENT-1",
                "evidence": ["same historical SKU"],
                "conflicts": ["size differs"],
            },
        ],
    }


class HistoricalSalesReviewRouteTests(unittest.TestCase):
    def test_expected_routes_are_registered(self):
        route_methods = {
            (route.path, method)
            for route in api.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("/historical-sales/review", "GET"), route_methods)
        self.assertIn(("/historical-sales/review/items", "GET"), route_methods)
        self.assertIn(("/historical-sales/review/decide", "POST"), route_methods)

    def test_items_endpoint_returns_aggregated_service_result(self):
        db = FakeConnectionContext()
        items = [review_item()]
        with patch.object(api, "_db_conn", return_value=db), patch.object(
            api.sales_service,
            "get_historical_sales_review_items",
            return_value=items,
            create=True,
        ) as get_items:
            result = api.historical_sales_review_items()
        self.assertEqual(result, {"count": 1, "items": items})
        get_items.assert_called_once_with(db.connection)

    def test_page_groups_evidence_and_escapes_all_source_text(self):
        page = api._historical_sales_review_html([review_item()])
        for label in (
            "Source Variant ID",
            "historical SKU",
            "First sale",
            "Last sale",
            "Raw rows",
            "Net units",
            "Absolute units",
            "Net sales",
            "Candidate canonical variants",
            "Evidence:",
            "Conflicts:",
            "AMBIGUOUS",
            "MATERIAL",
        ):
            self.assertIn(label, page)
        self.assertIn("MAP TO CANONICAL", page)
        self.assertIn("EXCLUDE HISTORICAL ITEM", page)
        self.assertIn("LEAVE UNRESOLVED", page)
        self.assertIn("name='actor'", page)
        self.assertIn("name='reason'", page)
        self.assertIn("name='review_token' type='password'", page)
        self.assertEqual(page.count("action='review/decide'"), 3)
        self.assertNotIn("action='/historical-sales/", page)
        self.assertIn("No mapping is pre-approved or pre-selected", page)
        self.assertNotIn("<option selected", page.lower())
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;alert", page)
        self.assertIn("Historical &lt;Bottle&gt;", page)

    def test_empty_page_is_operational_and_read_only(self):
        page = api._historical_sales_review_html([])
        self.assertIn("No unresolved or ambiguous historical source identities", page)
        self.assertIn("Nothing on this page writes to Shopify", page)


class HistoricalSalesReviewDecisionTests(unittest.TestCase):
    def call_decision(self, **overrides):
        values = {
            "source_key": "source-key-1",
            "action": "MAP_TO_CANONICAL",
            "actor": "Owner",
            "reason": "Reviewed exact historical evidence",
            "canonical_variant_id": "222",
            "review_token": "correct-token",
        }
        values.update(overrides)
        return api.historical_sales_review_decide(**values)

    def test_invalid_token_blocks_before_database_or_decision_service(self):
        db = Mock()
        decision = Mock()
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}), patch.object(
            api, "_db_conn", db
        ), patch.object(
            api.sales_service,
            "record_historical_sales_review_decision",
            decision,
            create=True,
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.call_decision(review_token="wrong-token")
        self.assertEqual(ctx.exception.status_code, 403)
        db.assert_not_called()
        decision.assert_not_called()

    def test_mapping_records_exact_explicit_fields_without_echoing_token(self):
        db = FakeConnectionContext()
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}), patch.object(
            api, "_db_conn", return_value=db
        ), patch.object(
            api.sales_service,
            "record_historical_sales_review_decision",
            return_value={"action": "MAP_TO_CANONICAL"},
            create=True,
        ) as decide:
            page = self.call_decision()
        decide.assert_called_once_with(
            db.connection,
            source_key="source-key-1",
            action="MAP_TO_CANONICAL",
            canonical_variant_id="222",
            actor="Owner",
            reason="Reviewed exact historical evidence",
        )
        self.assertIn("MAP_TO_CANONICAL recorded", page)
        self.assertIn("url=../review", page)
        self.assertNotIn("url=/historical-sales/", page)
        self.assertNotIn("correct-token", page)

    def test_leave_unresolved_is_an_audited_action_without_mapping_target(self):
        db = FakeConnectionContext()
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}), patch.object(
            api, "_db_conn", return_value=db
        ), patch.object(
            api.sales_service,
            "record_historical_sales_review_decision",
            return_value={"action": "LEAVE_UNRESOLVED"},
            create=True,
        ) as decide:
            self.call_decision(
                action="LEAVE_UNRESOLVED",
                reason="Evidence remains ambiguous",
                canonical_variant_id="should-be-ignored",
            )
        self.assertIsNone(decide.call_args.kwargs["canonical_variant_id"])
        self.assertEqual(decide.call_args.kwargs["reason"], "Evidence remains ambiguous")

    def test_exclusion_is_explicit_and_never_carries_a_mapping_target(self):
        db = FakeConnectionContext()
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}), patch.object(
            api, "_db_conn", return_value=db
        ), patch.object(
            api.sales_service,
            "record_historical_sales_review_decision",
            return_value={"action": "EXCLUDE_HISTORICAL_ITEM"},
            create=True,
        ) as decide:
            self.call_decision(
                action="EXCLUDE_HISTORICAL_ITEM",
                reason="Owner confirmed non-merchandise historical line",
                canonical_variant_id="should-be-ignored",
            )
        self.assertEqual(decide.call_args.kwargs["action"], "EXCLUDE_HISTORICAL_ITEM")
        self.assertIsNone(decide.call_args.kwargs["canonical_variant_id"])
        self.assertEqual(
            decide.call_args.kwargs["reason"],
            "Owner confirmed non-merchandise historical line",
        )

    def test_blank_human_evidence_is_rejected_before_database_access(self):
        db = Mock()
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}), patch.object(
            api, "_db_conn", db
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.call_decision(actor=" ")
        self.assertEqual(ctx.exception.status_code, 400)
        db.assert_not_called()

    def test_mapping_requires_explicit_canonical_variant_id(self):
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}):
            with self.assertRaises(HTTPException) as ctx:
                self.call_decision(canonical_variant_id=None)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_action_is_rejected(self):
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}):
            with self.assertRaises(HTTPException) as ctx:
                self.call_decision(action="AUTO_APPROVE")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_service_rejection_is_a_conflict(self):
        db = FakeConnectionContext()
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "correct-token"}), patch.object(
            api, "_db_conn", return_value=db
        ), patch.object(
            api.sales_service,
            "record_historical_sales_review_decision",
            side_effect=ValueError("canonical target is not uniquely valid"),
            create=True,
        ):
            with self.assertRaises(HTTPException) as ctx:
                self.call_decision()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertNotIn("correct-token", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
