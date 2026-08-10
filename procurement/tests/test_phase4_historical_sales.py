"""Adversarial, database-free tests for Phase 4 historical-sales invariants.

These tests deliberately exercise pure contracts and fake local collaborators only.
They never connect to Shopify or PostgreSQL.
"""
from __future__ import annotations

import dataclasses
import inspect
import os
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from procurement_os import historical_sales
from procurement_os.historical_sales import (
    AUTHORITATIVE_START_DATE,
    _coverage_evidence,
    _page_coverage_evidence,
    current_store_date,
    evaluate_sales_readiness,
    parse_control_payload,
    parse_detail_payload,
    sanitize_error,
    source_payload_hash,
    run_end_was_current_store_date,
)
from procurement_os.sales import (
    CurrentIdentity,
    HistoricalAlias,
    HistoricalIdentityIndex,
    SalesSourceRow,
    parse_shopifyql_row,
    rerun_sales_identity_resolution,
    source_row_hash,
)
from procurement_os.shopify.queries import (
    HISTORICAL_SALES_METRICS,
    HISTORICAL_SALES_REQUIRED_COLUMNS,
)


def source_row(
    *,
    sale_date: date = AUTHORITATIVE_START_DATE,
    variant_id: str | None = None,
    sku: str | None = None,
    product: str | None = "Historical Product",
    variant: str | None = "750ML",
    units: str = "1",
    sales: str = "10.00",
) -> SalesSourceRow:
    return SalesSourceRow(
        sale_date=sale_date,
        source_variant_id=variant_id,
        source_sku=sku,
        source_product_title=product,
        source_variant_title=variant,
        net_items_sold=Decimal(units),
        net_sales=Decimal(sales),
    )


def raw_row(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "day": "2024-11-28",
        "product_variant_id": "100",
        "product_title_at_time_of_sale": "Historical Product",
        "product_variant_title_at_time_of_sale": "750ML",
        "product_variant_sku_at_time_of_sale": "HIST-100",
        "net_items_sold": "1",
        "net_sales": "10.00",
    }
    value.update(overrides)
    return value


def payload(rows: list[dict[str, object]], *, columns: tuple[str, ...] = HISTORICAL_SALES_REQUIRED_COLUMNS) -> dict[str, object]:
    return {
        "tableData": {
            "columns": [{"name": name} for name in columns],
            "rows": rows,
        },
        "parseErrors": [],
    }


def passing_evidence(**overrides: object) -> dict[str, object]:
    evidence: dict[str, object] = {
        "start_date": "2024-11-28",
        "end_date": "2026-08-10",
        "end_is_current_store_date": True,
        "expected_chunks": 2,
        "completed_chunks": 2,
        "coverage_complete": True,
        "expected_pages": 3,
        "completed_pages": 3,
        "pages_complete": True,
        "unique_source_facts": 10,
        "source_facts_persisted": True,
        "idempotency_verified": True,
        "control_totals_reconciled": True,
        "canonical_aggregate_rebuilt": True,
        "resolution_accounting_reconciled": True,
        "unresolved_ambiguous_abs_units": "0",
        "unresolved_ambiguous_abs_sales": "0",
    }
    evidence.update(overrides)
    return evidence


class HistoricalIdentityResolutionTests(unittest.TestCase):
    def test_active_exact_variant_id_is_canonical(self):
        index = HistoricalIdentityIndex(
            [CurrentIdentity("100", "NOW", "Product", "750ML", True, "LIVE")], []
        )
        resolution = index.resolve(source_row(variant_id="100", sku="old-value"))
        self.assertEqual(
            (resolution.status, resolution.canonical_variant_id, resolution.method),
            ("RESOLVED", "100", "EXACT_ACTIVE_VARIANT_ID"),
        )

    def test_retired_exact_variant_id_keeps_legitimate_history(self):
        index = HistoricalIdentityIndex(
            [
                CurrentIdentity(
                    "46", "RETIRED", "Retired Product", "750ML", False,
                    "RETIRED_CONFIRMED",
                )
            ],
            [],
        )
        resolution = index.resolve(
            source_row(variant_id="46", sku="RETIRED", product="Old Name")
        )
        self.assertEqual(
            (resolution.status, resolution.canonical_variant_id, resolution.method),
            ("RESOLVED", "46", "EXACT_PRESERVED_HISTORICAL_VARIANT_ID"),
        )
        self.assertEqual(resolution.evidence["catalog_state"], "RETIRED_CONFIRMED")

    def test_recreated_old_variant_id_uses_only_approved_id_alias(self):
        index = HistoricalIdentityIndex(
            [
                CurrentIdentity("50", "OLD", "Old Product", "750ML", False, "RESOLVED_RECREATED"),
                CurrentIdentity("100", "NOW", "Current Product", "750ML", True, "LIVE"),
            ],
            [HistoricalAlias("100", "50", "OLD", "Old Product", "750ML", True)],
        )
        resolution = index.resolve(
            source_row(variant_id="50", sku="changed", product="Renamed Since Sale")
        )
        self.assertEqual(
            (resolution.status, resolution.canonical_variant_id, resolution.method),
            ("RESOLVED", "100", "APPROVED_VARIANT_ID_ALIAS"),
        )

    def test_conflicting_approved_old_id_aliases_are_ambiguous(self):
        index = HistoricalIdentityIndex(
            [
                CurrentIdentity("100", "A", "A", "750ML"),
                CurrentIdentity("200", "B", "B", "750ML"),
            ],
            [
                HistoricalAlias("100", "50", None, None, None),
                HistoricalAlias("200", "50", None, None, None),
            ],
        )
        resolution = index.resolve(source_row(variant_id="50"))
        self.assertEqual(resolution.status, "AMBIGUOUS")
        self.assertIsNone(resolution.canonical_variant_id)
        self.assertEqual(resolution.candidates, ("100", "200"))

    def test_null_and_zero_ids_require_exact_approved_historical_identity(self):
        index = HistoricalIdentityIndex(
            [CurrentIdentity("100", "NOW", "Current", "750ML")],
            [HistoricalAlias("100", None, "OLD", "Old Product", "750ML")],
        )
        for variant_id in (None, "0", "gid://shopify/ProductVariant/0"):
            with self.subTest(variant_id=variant_id):
                resolution = index.resolve(
                    source_row(
                        variant_id=variant_id,
                        sku=" old ",
                        product="OLD PRODUCT",
                        variant="750ml",
                    )
                )
                self.assertEqual(
                    (resolution.status, resolution.canonical_variant_id, resolution.method),
                    ("RESOLVED", "100", "APPROVED_HISTORICAL_IDENTITY"),
                )

    def test_duplicate_sku_never_becomes_unique_proof(self):
        index = HistoricalIdentityIndex(
            [
                CurrentIdentity("100", "DUP", "One", "750ML"),
                CurrentIdentity("200", "DUP", "Two", "750ML"),
            ],
            [],
        )
        resolution = index.resolve(source_row(sku="DUP", product="Neither"))
        self.assertEqual(resolution.status, "AMBIGUOUS")
        self.assertIsNone(resolution.canonical_variant_id)
        self.assertEqual(set(resolution.candidates), {"100", "200"})

    def test_exact_title_does_not_override_duplicate_live_sku(self):
        index = HistoricalIdentityIndex(
            [
                CurrentIdentity("100", "DUP", "One", "750ML"),
                CurrentIdentity("200", "DUP", "Two", "1L"),
            ],
            [],
        )
        resolution = index.resolve(source_row(sku="DUP", product="One", variant="750ML"))
        self.assertEqual(resolution.status, "AMBIGUOUS")
        self.assertIsNone(resolution.canonical_variant_id)
        self.assertEqual(set(resolution.candidates), {"100", "200"})

    def test_same_sku_with_different_size_is_an_explicit_conflict(self):
        index = HistoricalIdentityIndex(
            [CurrentIdentity("100", "SHARED", "Product", "750ML")], []
        )
        resolution = index.resolve(
            source_row(sku="SHARED", product="Renamed Product", variant="1L")
        )
        self.assertEqual(resolution.status, "AMBIGUOUS")
        self.assertIsNone(resolution.canonical_variant_id)
        self.assertEqual(resolution.method, "SKU_EVIDENCE_ONLY")
        self.assertTrue(any("SIZE_CONFLICT" in item for item in resolution.evidence["conflicts"]))

    def test_title_change_does_not_defeat_an_approved_variant_id_alias(self):
        index = HistoricalIdentityIndex(
            [CurrentIdentity("100", "NOW", "Current Product", "750ML")],
            [HistoricalAlias("100", "50", "OLD", "Original Product", "750ML")],
        )
        resolution = index.resolve(
            source_row(
                variant_id="50", sku="completely-changed", product="Renamed", variant="1L"
            )
        )
        self.assertEqual(
            (resolution.status, resolution.canonical_variant_id, resolution.method),
            ("RESOLVED", "100", "APPROVED_VARIANT_ID_ALIAS"),
        )


class RawFactContractTests(unittest.TestCase):
    def test_returns_and_negative_revenue_are_preserved_exactly(self):
        parsed = parse_shopifyql_row(
            raw_row(net_items_sold="-2", net_sales="-19.98")
        )
        self.assertEqual(parsed.net_items_sold, Decimal("-2"))
        self.assertEqual(parsed.net_sales, Decimal("-19.98"))

    def test_zero_net_units_with_revenue_are_not_discarded(self):
        parsed = parse_shopifyql_row(raw_row(net_items_sold="0", net_sales="4.25"))
        self.assertEqual(parsed.net_items_sold, Decimal("0"))
        self.assertEqual(parsed.net_sales, Decimal("4.25"))

    def test_metric_restatement_keeps_natural_identity_but_changes_payload(self):
        before = source_row(variant_id="100", sku="HIST", units="1", sales="10.00")
        restated = source_row(variant_id="100", sku="HIST", units="2", sales="18.00")
        self.assertEqual(source_row_hash(before), source_row_hash(restated))
        self.assertNotEqual(source_payload_hash(before), source_payload_hash(restated))

    def test_null_and_zero_variant_dimensions_remain_distinct_source_facts(self):
        null_id = source_row(variant_id=None, sku="HIST")
        zero_id = source_row(variant_id="0", sku="HIST")
        self.assertNotEqual(source_row_hash(null_id), source_row_hash(zero_id))

    def test_detail_page_rejects_duplicate_natural_facts(self):
        duplicate = raw_row()
        with self.assertRaisesRegex(ValueError, "duplicate natural source fact"):
            parse_detail_payload(
                payload([duplicate, dict(duplicate)]),
                chunk_start=AUTHORITATIVE_START_DATE,
                chunk_end=AUTHORITATIVE_START_DATE,
            )

    def test_detail_page_rejects_parse_errors_before_rows(self):
        invalid = payload([raw_row()])
        invalid["parseErrors"] = [{"message": "invalid query"}]
        with self.assertRaisesRegex(RuntimeError, "parse error"):
            parse_detail_payload(
                invalid,
                chunk_start=AUTHORITATIVE_START_DATE,
                chunk_end=AUTHORITATIVE_START_DATE,
            )

    def test_detail_page_rejects_missing_or_extra_columns(self):
        missing = tuple(name for name in HISTORICAL_SALES_REQUIRED_COLUMNS if name != "net_sales")
        with self.assertRaisesRegex(ValueError, "authorized contract"):
            parse_detail_payload(
                payload([raw_row()], columns=missing),
                chunk_start=AUTHORITATIVE_START_DATE,
                chunk_end=AUTHORITATIVE_START_DATE,
            )
        with self.assertRaisesRegex(ValueError, "authorized contract"):
            parse_detail_payload(
                payload([raw_row()], columns=HISTORICAL_SALES_REQUIRED_COLUMNS + ("customer_email",)),
                chunk_start=AUTHORITATIVE_START_DATE,
                chunk_end=AUTHORITATIVE_START_DATE,
            )

    def test_detail_page_rejects_rows_outside_requested_chunk(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            parse_detail_payload(
                payload([raw_row(day="2024-11-29")]),
                chunk_start=AUTHORITATIVE_START_DATE,
                chunk_end=AUTHORITATIVE_START_DATE,
            )

    def test_required_metrics_may_be_zero_but_not_null_or_missing(self):
        parsed = parse_shopifyql_row(raw_row(net_items_sold="0", net_sales="0"))
        self.assertEqual((parsed.net_items_sold, parsed.net_sales), (Decimal("0"), Decimal("0")))
        for invalid in (
            raw_row(net_items_sold=None),
            raw_row(net_sales=""),
            {key: value for key, value in raw_row().items() if key != "net_sales"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_shopifyql_row(invalid)

    def test_control_payload_is_exact_and_single_row(self):
        valid = payload(
            [{"net_items_sold": "-1.5000", "net_sales": "12.34"}],
            columns=HISTORICAL_SALES_METRICS,
        )
        totals = parse_control_payload(valid)
        self.assertEqual(totals.net_items_sold, Decimal("-1.5000"))
        self.assertEqual(totals.net_sales, Decimal("12.34"))
        with self.assertRaisesRegex(ValueError, "exactly one row"):
            parse_control_payload(payload([], columns=HISTORICAL_SALES_METRICS))

    def test_raw_source_shape_has_no_customer_pii_fields(self):
        fields = {field.name.casefold() for field in dataclasses.fields(SalesSourceRow)}
        for prohibited in ("customer", "email", "phone", "address"):
            self.assertFalse(any(prohibited in field for field in fields))


class ReadinessInvariantTests(unittest.TestCase):
    def test_complete_reconciled_durable_run_can_pass(self):
        result = evaluate_sales_readiness(passing_evidence())
        self.assertTrue(result.passed)
        self.assertEqual(result.blockers, ())

    def test_zero_unresolved_rows_alone_cannot_override_incomplete_controls(self):
        result = evaluate_sales_readiness(
            passing_evidence(
                unresolved_rows=0,
                ambiguous_rows=0,
                control_totals_reconciled=False,
                coverage_complete=False,
            )
        )
        self.assertFalse(result.passed)
        self.assertIn("CONTROL_TOTALS_FAILED", result.blockers)
        self.assertIn("DATE_COVERAGE_INCOMPLETE", result.blockers)

    def test_every_required_completion_signal_fails_closed(self):
        cases = {
            "authoritative_start": ({"start_date": "2024-11-29"}, "AUTHORITATIVE_START_DATE_NOT_COVERED"),
            "current_end": ({"end_is_current_store_date": False}, "STORE_LOCAL_END_DATE_NOT_CURRENT"),
            "missing_chunk": ({"completed_chunks": 1}, "DATE_COVERAGE_INCOMPLETE"),
            "partial_coverage": ({"coverage_complete": False}, "DATE_COVERAGE_INCOMPLETE"),
            "missing_page": ({"completed_pages": 2}, "PAGE_COVERAGE_INCOMPLETE"),
            "partial_pages": ({"pages_complete": False}, "PAGE_COVERAGE_INCOMPLETE"),
            "no_facts": ({"unique_source_facts": 0}, "SOURCE_FACTS_NOT_DURABLE"),
            "not_persisted": ({"source_facts_persisted": False}, "SOURCE_FACTS_NOT_DURABLE"),
            "not_idempotent": ({"idempotency_verified": False}, "SOURCE_FACT_IDEMPOTENCY_FAILED"),
            "control_mismatch": ({"control_totals_reconciled": False}, "CONTROL_TOTALS_FAILED"),
            "not_rebuilt": ({"canonical_aggregate_rebuilt": False}, "CANONICAL_AGGREGATE_NOT_REBUILT"),
            "accounting_mismatch": ({"resolution_accounting_reconciled": False}, "RESOLUTION_ACCOUNTING_FAILED"),
        }
        for label, (override, blocker) in cases.items():
            with self.subTest(label=label):
                result = evaluate_sales_readiness(passing_evidence(**override))
                self.assertFalse(result.passed)
                self.assertIn(blocker, result.blockers)

    def test_material_unresolved_units_or_revenue_block_independently(self):
        for override in (
            {"unresolved_ambiguous_abs_units": "0.0002"},
            {"unresolved_ambiguous_abs_sales": "0.02"},
            {"unresolved_ambiguous_abs_units": "7", "unresolved_ambiguous_abs_sales": "0"},
        ):
            with self.subTest(override=override):
                result = evaluate_sales_readiness(passing_evidence(**override))
                self.assertFalse(result.passed)
                self.assertIn("MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED", result.blockers)

    def test_interrupted_or_missing_chunk_cannot_claim_coverage(self):
        complete = [
            ("chunk-0", 0, date(2024, 11, 28), date(2024, 12, 28), "COMPLETED", 1000, 2, 2, True),
            ("chunk-1", 1, date(2024, 12, 29), date(2025, 1, 1), "COMPLETED", 1000, 1, 1, True),
        ]
        self.assertTrue(
            _coverage_evidence(complete, date(2024, 11, 28), date(2025, 1, 1))["coverage_complete"]
        )

        interrupted = [complete[0], (*complete[1][:4], "PARTIAL", *complete[1][5:8], False)]
        result = _coverage_evidence(interrupted, date(2024, 11, 28), date(2025, 1, 1))
        self.assertFalse(result["coverage_complete"])
        self.assertFalse(result["pages_complete"])

        missing = [complete[1]]
        self.assertFalse(
            _coverage_evidence(missing, date(2024, 11, 28), date(2025, 1, 1))["coverage_complete"]
        )

    def test_missing_page_index_cannot_hide_behind_matching_page_counts(self):
        start = date(2024, 11, 28)
        end = date(2024, 12, 28)
        complete = [
            (0, 0, 1000, start, end, "COMPLETED", False, 1000, "PASS", "hash-0"),
            (1, 1000, 1000, start, end, "COMPLETED", False, 1000, "PASS", "hash-1"),
            (2, 2000, 1000, start, end, "COMPLETED", True, 12, "PASS", "hash-2"),
        ]
        valid = _page_coverage_evidence(complete, chunk_start=start, chunk_end=end)
        self.assertTrue(valid["pages_complete"])
        self.assertEqual((valid["expected_pages"], valid["completed_pages"]), (3, 3))

        gap = _page_coverage_evidence(
            [complete[0], complete[2]], chunk_start=start, chunk_end=end,
        )
        self.assertFalse(gap["pages_complete"])
        self.assertEqual((gap["expected_pages"], gap["completed_pages"]), (3, 2))
        self.assertFalse(gap["page_indexes_contiguous"])


class LocalSafetyContractTests(unittest.TestCase):
    def test_store_local_end_date_honors_timezone_boundary(self):
        instant = datetime(2026, 8, 10, 2, 30, tzinfo=timezone.utc)
        self.assertEqual(current_store_date("America/New_York", now=instant), date(2026, 8, 9))
        self.assertEqual(current_store_date("Asia/Tokyo", now=instant), date(2026, 8, 10))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            current_store_date("America/New_York", now=datetime(2026, 8, 10, 2, 30))

    def test_run_end_evidence_is_anchored_to_creation_not_review_time(self):
        created = datetime(2026, 8, 11, 3, 30, tzinfo=timezone.utc)
        self.assertTrue(
            run_end_was_current_store_date(
                date(2026, 8, 10), "America/New_York", created,
            )
        )
        self.assertFalse(
            run_end_was_current_store_date(
                date(2026, 8, 11), "America/New_York", created,
            )
        )

    def test_local_mutations_hold_transaction_advisory_lock_through_commit(self):
        helper = inspect.getsource(historical_sales.acquire_backfill_transaction_lock)
        finalize = inspect.getsource(historical_sales.finalize_sales_backfill)
        decision = inspect.getsource(historical_sales.record_historical_sales_review_decision)
        self.assertIn("pg_try_advisory_xact_lock", helper)
        self.assertIn("with conn.transaction()", finalize)
        self.assertIn("acquire_backfill_transaction_lock", finalize)
        self.assertIn("with conn.transaction()", decision)
        self.assertIn("acquire_backfill_transaction_lock", decision)
        self.assertNotIn("release_backfill_lock", finalize)
        self.assertNotIn("release_backfill_lock", decision)

    def test_local_reresolution_calls_no_shopify_client(self):
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchone(self):
                return ("durable-run-id",)

        class Connection:
            def cursor(self):
                return Cursor()

        connection = Connection()
        with patch.object(
            historical_sales,
            "finalize_sales_backfill",
            return_value={"status": "FAIL"},
        ) as finalize:
            result = rerun_sales_identity_resolution(
                connection, start_date=AUTHORITATIVE_START_DATE, end_date=date(2026, 8, 10)
            )
        self.assertEqual(result, {"status": "FAIL"})
        finalize.assert_called_once_with(connection, run_id="durable-run-id")
        self.assertNotIn("client", inspect.signature(rerun_sales_identity_resolution).parameters)

    def test_error_sanitization_redacts_all_configured_secret_classes(self):
        secrets = {
            "SHOPIFY_CLIENT_SECRET": "shopify-client-secret-value",
            "SHOPIFY_ACCESS_TOKEN": "shpat_live_access_value",
            "RECONCILIATION_REVIEW_TOKEN": "review-token-value",
            "DATABASE_URL": "postgresql://owner:password@example.invalid/database",
            "OPENAI_API_KEY": "sk-openai-value",
            "ANTHROPIC_API_KEY": "sk-ant-anthropic-value",
        }
        message = " | ".join(secrets.values()) + " | token=raw-token-value"
        with patch.dict(os.environ, secrets, clear=False):
            error_class, sanitized = sanitize_error(RuntimeError(message))
        self.assertEqual(error_class, "RuntimeError")
        for secret in secrets.values():
            self.assertNotIn(secret, sanitized)
        self.assertNotIn("raw-token-value", sanitized)
        self.assertIn("[REDACTED]", sanitized)

    def test_phase4_module_has_no_shopify_mutation_or_orders_fallback(self):
        source = inspect.getsource(historical_sales).casefold()
        self.assertNotIn("client.mutate", source)
        self.assertNotIn("read_all_orders", source)


if __name__ == "__main__":
    unittest.main()
