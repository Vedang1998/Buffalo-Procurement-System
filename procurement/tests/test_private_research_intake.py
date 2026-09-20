from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable
import unittest
from unittest.mock import patch

from procurement_os.private_research_intake import (
    CATALOG_ROW_FIELDS,
    CATALOG_SNAPSHOT_CONTRACT,
    DAILY_SALES_ROW_FIELDS,
    DAILY_SALES_SNAPSHOT_CONTRACT,
    INTAKE_AUTHORITY,
    JSONL_FORMAT,
    PRIVATE_REAL_SOURCE_REVIEW,
    PRIVATE_RESEARCH_INTAKE_CONTRACT,
    SOURCE_AUTHORITY,
    ZERO_AUTHORITY,
    PrivateResearchIntakeError,
    build_private_research_intake,
    canonical_json_bytes,
    intake_manifest_key,
    read_private_research_intake,
    validate_private_root,
)
from procurement_os.storage import LocalFilesystemStorage
from procurement_os.supplier_review_package import ReviewPackage
from procurement_os.supplier_review_real_v5 import (
    A1_ADAPTER,
    A1_PACKAGE_ID,
    A1_PACKAGE_KIND,
    A1_REVIEW_LABEL,
    A1_ROOT_SHA256,
    A1_SEAL_SHA256,
)


def _jsonl(rows: list[dict[str, object]]) -> tuple[bytes, list[bytes]]:
    lines = [canonical_json_bytes(row) for row in rows]
    return b"".join(lines), lines


def _pages(lines: list[bytes], page_size: int = 1) -> dict[str, object]:
    count = max(1, (len(lines) + page_size - 1) // page_size)
    pages: list[dict[str, object]] = []
    for index in range(count):
        first = index * page_size
        selected = lines[first : first + page_size]
        pages.append(
            {
                "page_index": index,
                "first_row_index": first,
                "row_count": len(selected),
                "sha256": hashlib.sha256(b"".join(selected)).hexdigest(),
                "terminal": index == count - 1,
            }
        )
    return {
        "page_size": page_size,
        "expected_pages": count,
        "completed_pages": count,
        "pages": pages,
        "terminal_page_seen": True,
        "truncated": False,
    }


def _fake_a1_package() -> ReviewPackage:
    return ReviewPackage(
        source="private-fixture",
        package_kind=A1_PACKAGE_KIND,
        snapshot_id=A1_PACKAGE_ID,
        status="REVIEW_ONLY_VALIDATED",
        label=A1_REVIEW_LABEL,
        file_count=2_853,
        verified_file_count=2_853,
        manifest_sha256=A1_ROOT_SHA256,
        tables={},
        cohorts={
            "original_cohort_count": 2_000,
            "current_census_count": 2_003,
            "current_original_returned": 1_999,
            "current_additions": 4,
        },
        issues=(),
        unavailable_evidence=("ORIGINAL_SUPPLIER_PDFS",),
        metadata={
            "adapter": A1_ADAPTER,
            "readiness": {
                "raw_file_integrity": "PASS",
                "effective_snapshot_restoration": "PASS",
                "structural_replay": "PASS",
                "relationship_validation": "PASS",
                "mapping_approval": "NOT_APPROVED",
                "price_approval": "NOT_APPROVED",
                "import_readiness": "NOT_IMPORT_READY",
            },
            "integrity_checks": {
                "root_raw_sha256": A1_ROOT_SHA256,
                "seal_raw_sha256": A1_SEAL_SHA256,
                "logical_tables": 120,
            },
            "source_evidence": {
                "embedded_source_evidence": 680,
                "external_page_evidence": 174,
                "source_rows_with_pdf_page": 191,
            },
            "external_pdf_status": {
                "fixture.pdf": {
                    "availability": "UNAVAILABLE_NOT_SUPPLIED",
                    "bytes": 100,
                    "sha256": "c" * 64,
                    "physical_pages": 2,
                    "page_range_basis": (
                        "RANGE_CHECKED_AGAINST_PINNED_DECLARATION_NOT_INDEPENDENTLY_PARSED"
                    ),
                }
            },
            "page_bundle_status": {
                "fixture-pages.zip": "UNAVAILABLE_ORIGINAL_BYTES"
            },
            "authority": {
                "label": A1_REVIEW_LABEL,
                "mapping_approvals": 0,
                "price_approvals": 0,
                "import_ready_rows": 0,
                "operational_effects": 0,
            },
        },
    )


def _fake_batches() -> list[dict[str, object]]:
    values = ["1001"] + [str(8_000_000 + index) for index in range(1_999)]
    batches: list[dict[str, object]] = []
    for variant in values:
        offers: list[dict[str, object]] = []
        if variant == "1001":
            offers.append(
                {
                    "variant_id": variant,
                    "vendor": "Fixture Supplier",
                    "source_occurrence_id": "offer-1",
                    "supplier_code": "0012-A",
                    "supplier_description": "Fixture bottle",
                    "program_type": "STANDARD",
                    "candidate_disposition": "PROPOSED_REVIEW_CANDIDATE",
                    "reviewed_shopify_units_per_case": 6,
                    "reviewed_qualifying_units_per_case": 6,
                    "source": {
                        "file": "fixture.pdf",
                        "page": 1,
                        "sha256": "c" * 64,
                        "period": "FIXTURE",
                        "territory": "FIXTURE",
                        "availability": "UNAVAILABLE_NOT_SUPPLIED",
                    },
                    "blockers": {
                        "identity": [],
                        "packaging": [],
                        "program_price": ["NO_CURRENT_PRICE_OR_TIER_AUTHORITY"],
                        "source_availability": ["SOURCE_BYTES_UNAVAILABLE"],
                    },
                    "relationship_record_sha256": "d" * 64,
                    "offer_preview_fingerprint": "e" * 64,
                    "price_ladder": [],
                    "authority": {
                        "mapping_approved": False,
                        "price_approved": False,
                        "import_ready": False,
                        "selection_created": False,
                    },
                }
            )
        batches.append(
            {
                "variant_id": variant,
                "offers": offers,
                "authority": {
                    "mapping_approvals": 0,
                    "price_approvals": 0,
                    "import_ready_rows": 0,
                },
            }
        )
    return batches


class PrivateResearchIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        os.chmod(self.root, 0o700)
        (self.root / "input").mkdir()
        (self.root / "a1").mkdir()
        self.catalog_rows = [
            {
                "shopify_variant_id": "1001",
                "product_title": "Fixture Product",
                "variant_title": "750 mL",
                "shopify_sku": "SAME-SKU",
                "barcode": "000001",
                "current_retail_price": "15.00",
                "current_inventory_item_cost": "5.00",
                "product_status": "ACTIVE",
                "shopify_vendor": "Brand",
                "product_type": "Wine",
                "inventory_item_id": "gid://shopify/InventoryItem/91",
                "inventory_tracked": True,
            },
            {
                "shopify_variant_id": "1002",
                "product_title": "Other Product",
                "variant_title": "750 mL",
                "shopify_sku": "SAME-SKU",
                "barcode": None,
                "current_retail_price": "20.00",
                "current_inventory_item_cost": "8.00",
                "product_status": "ACTIVE",
                "shopify_vendor": "Brand",
                "product_type": "Spirits",
                "inventory_item_id": "gid://shopify/InventoryItem/92",
                "inventory_tracked": True,
            },
        ]
        self.sales_rows = [
            {
                "business_date": "2026-01-01",
                "shopify_variant_id": "1001",
                "net_units": "1",
                "net_revenue": "15.00",
                "historical_cogs": None,
            },
            {
                "business_date": "2026-01-02",
                "shopify_variant_id": "1002",
                "net_units": "0",
                "net_revenue": "0",
                "historical_cogs": "0",
            },
        ]
        self._write_catalog(self.catalog_rows)
        self._write_sales(self.sales_rows)
        self.package = _fake_a1_package()
        self.batches = _fake_batches()
        self.reader = patch(
            "procurement_os.private_research_intake.read_review_package",
            return_value=self.package,
        )
        self.batch_builder = patch(
            "procurement_os.private_research_intake.build_review_batches",
            return_value=self.batches,
        )
        self.reader.start()
        self.batch_builder.start()

    def tearDown(self):
        self.batch_builder.stop()
        self.reader.stop()
        self.temporary.cleanup()

    def _write_catalog(
        self,
        rows: list[dict[str, object]],
        *,
        mutate: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        payload, lines = _jsonl(rows)
        (self.root / "input" / "catalog.jsonl").write_bytes(payload)
        manifest: dict[str, object] = {
            "contract": CATALOG_SNAPSHOT_CONTRACT,
            "data_mode": PRIVATE_REAL_SOURCE_REVIEW,
            "authority": dict(SOURCE_AUTHORITY),
            "snapshot_id": "catalog-fixture-v1",
            "source": {
                "system": "SHOPIFY_ADMIN_GRAPHQL",
                "store_identity": "fixture-store",
                "store_timezone": "America/New_York",
            },
            "extraction": {
                "started_at_utc": "2026-01-03T12:00:00Z",
                "completed_at_utc": "2026-01-03T12:01:00Z",
                "snapshot_at_utc": "2026-01-03T12:00:30Z",
            },
            "population": {
                "row_count": len(rows),
                "reported_row_count": len(rows),
            },
            "query": {"identity": "CATALOG_QUERY_V1", "sha256": "a" * 64},
            "pagination": _pages(lines),
            "blob": {
                "path": "input/catalog.jsonl",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "format": JSONL_FORMAT,
                "schema": list(CATALOG_ROW_FIELDS),
            },
        }
        if mutate is not None:
            mutate(manifest)
        (self.root / "input" / "catalog-manifest.json").write_bytes(
            canonical_json_bytes(manifest)
        )

    def _write_sales(
        self,
        rows: list[dict[str, object]],
        *,
        mutate: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        payload, lines = _jsonl(rows)
        (self.root / "input" / "sales.jsonl").write_bytes(payload)
        variants = {str(row["shopify_variant_id"]) for row in rows}
        manifest: dict[str, object] = {
            "contract": DAILY_SALES_SNAPSHOT_CONTRACT,
            "data_mode": PRIVATE_REAL_SOURCE_REVIEW,
            "authority": dict(SOURCE_AUTHORITY),
            "snapshot_id": "sales-fixture-v1",
            "source": {
                "system": "SHOPIFYQL_SALES",
                "store_identity": "fixture-store",
                "store_timezone": "America/New_York",
            },
            "extraction": {
                "started_at_utc": "2026-01-03T12:02:00Z",
                "completed_at_utc": "2026-01-03T12:03:00Z",
                "snapshot_at_utc": "2026-01-03T12:02:30Z",
            },
            "date_range": {
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
                "complete_through_date": "2026-01-02",
                "store_timezone": "America/New_York",
                "complete_day_count": 2,
                "missing_dates": [],
            },
            "population": {
                "row_count": len(rows),
                "distinct_variant_count": len(variants),
            },
            "query": {"identity": "DAILY_SALES_QUERY_V1", "sha256": "b" * 64},
            "pagination": _pages(lines),
            "blob": {
                "path": "input/sales.jsonl",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "format": JSONL_FORMAT,
                "schema": list(DAILY_SALES_ROW_FIELDS),
            },
        }
        if mutate is not None:
            mutate(manifest)
        (self.root / "input" / "sales-manifest.json").write_bytes(
            canonical_json_bytes(manifest)
        )

    def _build(self) -> dict[str, object]:
        return build_private_research_intake(
            self.root,
            catalog_manifest_path="input/catalog-manifest.json",
            daily_sales_manifest_path="input/sales-manifest.json",
            a1_package_path="a1",
        )

    def test_builds_current_catalog_only_intake_and_exact_readback(self):
        intake = self._build()
        self.assertEqual(
            set(intake),
            {
                "contract",
                "data_mode",
                "authority",
                "intake_id",
                "sources",
                "coverage",
                "variants",
                "vendors",
                "limitations",
                "zero_authority",
            },
        )
        self.assertEqual(intake["contract"], PRIVATE_RESEARCH_INTAKE_CONTRACT)
        self.assertEqual(intake["authority"], INTAKE_AUTHORITY)
        self.assertEqual(intake["zero_authority"], ZERO_AUTHORITY)
        coverage = intake["coverage"]
        self.assertEqual(coverage["current_catalog_population"], 2)
        self.assertEqual(coverage["a1_original_review_population"], 2_000)
        self.assertEqual(coverage["a1_variants_joined_to_current_catalog"], 1)
        self.assertEqual(coverage["a1_variants_not_in_current_catalog"], 1_999)
        self.assertEqual(coverage["current_catalog_variants_without_a1_review"], 1)
        self.assertFalse(coverage["abc_cohort"]["coverage_complete"])

        variants = {row["shopify_variant_id"]: row for row in intake["variants"]}
        self.assertEqual(set(variants), {"1001", "1002"})
        self.assertEqual(len(variants["1001"]["supplier_hypotheses"]), 1)
        self.assertEqual(variants["1002"]["supplier_hypotheses"], [])
        hypothesis = variants["1001"]["supplier_hypotheses"][0]
        self.assertEqual(hypothesis["shopify_variant_id"], "1001")
        self.assertEqual(hypothesis["supplier_sku"], "0012-A")
        self.assertIsNone(hypothesis["current_unit_cost"])
        self.assertIsNone(variants["1001"]["historical_cogs"])
        self.assertEqual(variants["1001"]["current_inventory_item_cost"], "5.00")
        self.assertEqual(
            variants["1001"]["sales_history"]["net_units_series"], ["1", "0"]
        )
        self.assertEqual(
            variants["1002"]["sales_history"]["net_units_series"], ["0", "0"]
        )
        self.assertEqual(intake["vendors"][0]["vendor_name"], "Fixture Supplier")

        readback = read_private_research_intake(self.root, intake["intake_id"])
        self.assertEqual(readback, intake)
        stored = self.root / intake_manifest_key(intake["intake_id"])
        self.assertEqual(stored.read_bytes(), canonical_json_bytes(intake))

    def test_exact_replay_is_idempotent_and_manifest_is_published_last(self):
        calls: list[str] = []
        original = LocalFilesystemStorage.put_bytes_once

        def observed(store, key, data):
            calls.append(key)
            return original(store, key, data)

        with patch.object(LocalFilesystemStorage, "put_bytes_once", new=observed):
            first = self._build()
        manifest = self.root / intake_manifest_key(first["intake_id"])
        prior_mtime = manifest.stat().st_mtime_ns
        self.assertTrue(calls[-1].startswith("private-research/intakes/"))
        self.assertTrue(all("/blobs/" in key for key in calls[:-1]))

        second = self._build()
        self.assertEqual(second, first)
        self.assertEqual(manifest.stat().st_mtime_ns, prior_mtime)

    def test_rejects_nonabsolute_wrong_mode_and_symlink_private_roots(self):
        with self.assertRaisesRegex(PrivateResearchIntakeError, "PRIVATE_ROOT_NOT_ABSOLUTE"):
            validate_private_root("relative-private-root")
        os.chmod(self.root, 0o750)
        with self.assertRaisesRegex(PrivateResearchIntakeError, "PRIVATE_ROOT_MODE"):
            validate_private_root(self.root)
        os.chmod(self.root, 0o700)
        link = self.root.parent / f"{self.root.name}-link"
        link.symlink_to(self.root, target_is_directory=True)
        try:
            with self.assertRaisesRegex(PrivateResearchIntakeError, "PRIVATE_ROOT_SYMLINK"):
                validate_private_root(link)
        finally:
            link.unlink()

    def test_rejects_unknown_manifest_fields_hash_drift_and_truncation(self):
        self._write_catalog(
            self.catalog_rows, mutate=lambda value: value.update({"unknown": True})
        )
        with self.assertRaisesRegex(PrivateResearchIntakeError, "SCHEMA_MISMATCH"):
            self._build()

        self._write_catalog(self.catalog_rows)
        (self.root / "input" / "catalog.jsonl").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "CONTENT_HASH_MISMATCH"):
            self._build()

        def truncated(value):
            value["pagination"]["truncated"] = True

        self._write_catalog(self.catalog_rows, mutate=truncated)
        with self.assertRaisesRegex(PrivateResearchIntakeError, "PAGINATION_INCOMPLETE"):
            self._build()

    def test_rejects_typed_authority_count_drift_and_unsafe_or_symlink_blob_paths(self):
        def false_as_integer(value):
            value["authority"]["operational_authority"] = 0

        self._write_catalog(self.catalog_rows, mutate=false_as_integer)
        with self.assertRaisesRegex(PrivateResearchIntakeError, "AUTHORITY_MISMATCH"):
            self._build()

        def wrong_population(value):
            value["population"]["row_count"] = 3
            value["population"]["reported_row_count"] = 3

        self._write_catalog(self.catalog_rows, mutate=wrong_population)
        with self.assertRaisesRegex(PrivateResearchIntakeError, "PAGINATION_INCOMPLETE"):
            self._build()

        self._write_catalog(
            self.catalog_rows,
            mutate=lambda value: value["blob"].update(
                {"path": "../outside/catalog.jsonl"}
            ),
        )
        with self.assertRaisesRegex(PrivateResearchIntakeError, "UNSAFE_PATH"):
            self._build()

        self._write_catalog(self.catalog_rows)
        link = self.root / "input" / "catalog-link.jsonl"
        link.symlink_to(self.root / "input" / "catalog.jsonl")
        try:
            self._write_catalog(
                self.catalog_rows,
                mutate=lambda value: value["blob"].update(
                    {"path": "input/catalog-link.jsonl"}
                ),
            )
            with self.assertRaisesRegex(PrivateResearchIntakeError, "UNSAFE_PATH"):
                self._build()
        finally:
            link.unlink()

    def test_rejects_duplicate_or_noncanonical_catalog_variant_identity(self):
        duplicate = [deepcopy(self.catalog_rows[0]), deepcopy(self.catalog_rows[0])]
        self._write_catalog(duplicate)
        with self.assertRaisesRegex(PrivateResearchIntakeError, "DUPLICATE_VARIANT_ID"):
            self._build()

        changed = deepcopy(self.catalog_rows)
        changed[0]["shopify_variant_id"] = "gid://shopify/ProductVariant/1001"
        self._write_catalog(changed)
        with self.assertRaisesRegex(PrivateResearchIntakeError, "INVALID_VARIANT_ID"):
            self._build()

    def test_rejects_sales_unknown_variant_duplicate_fact_or_incomplete_day(self):
        unknown = deepcopy(self.sales_rows)
        unknown[0]["shopify_variant_id"] = "9999"
        self._write_sales(unknown)
        with self.assertRaisesRegex(
            PrivateResearchIntakeError, "SALES_VARIANT_NOT_IN_CATALOG"
        ):
            self._build()

        duplicate = [deepcopy(self.sales_rows[0]), deepcopy(self.sales_rows[0])]
        self._write_sales(duplicate)
        with self.assertRaisesRegex(PrivateResearchIntakeError, "DUPLICATE_SALES_FACT"):
            self._build()

        def incomplete(value):
            value["date_range"]["missing_dates"] = ["2026-01-02"]

        self._write_sales(self.sales_rows, mutate=incomplete)
        with self.assertRaisesRegex(
            PrivateResearchIntakeError, "DATE_COVERAGE_INCOMPLETE"
        ):
            self._build()

    def test_rejects_wrong_a1_package_or_a1_cross_variant_offer(self):
        self.reader.stop()
        wrong = replace(self.package, manifest_sha256="0" * 64)
        with patch(
            "procurement_os.private_research_intake.read_review_package",
            return_value=wrong,
        ):
            with self.assertRaisesRegex(PrivateResearchIntakeError, "A1_CONTRACT_MISMATCH"):
                self._build()
        self.reader.start()

        self.reader.stop()
        wrong_counts = replace(
            self.package,
            cohorts={**self.package.cohorts, "original_cohort_count": 1_999},
        )
        with patch(
            "procurement_os.private_research_intake.read_review_package",
            return_value=wrong_counts,
        ):
            with self.assertRaisesRegex(PrivateResearchIntakeError, "A1_CONTRACT_MISMATCH"):
                self._build()
        self.reader.start()

        changed_batches = deepcopy(self.batches)
        changed_batches[0]["offers"][0]["variant_id"] = "1002"
        self.batch_builder.stop()
        with patch(
            "procurement_os.private_research_intake.build_review_batches",
            return_value=changed_batches,
        ):
            with self.assertRaisesRegex(
                PrivateResearchIntakeError, "A1_IDENTITY_JOIN_MISMATCH"
            ):
                self._build()
        self.batch_builder.start()

    def test_readback_rehashes_immutable_blobs_and_refuses_tampering(self):
        intake = self._build()
        catalog_blob = intake["sources"]["catalog"]["data_blob"]["key"]
        (self.root / catalog_blob).write_bytes(b"tampered private bytes\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "CONTENT_HASH_MISMATCH"):
            read_private_research_intake(self.root, intake["intake_id"])
        with self.assertRaisesRegex(
            PrivateResearchIntakeError, "IMMUTABLE_OBJECT_CONFLICT"
        ):
            self._build()

    def test_readback_rejects_boolean_disguised_as_zero_authority_count(self):
        intake = self._build()
        path = self.root / intake_manifest_key(intake["intake_id"])
        changed = deepcopy(intake)
        changed["zero_authority"]["database_writes"] = False
        path.write_bytes(canonical_json_bytes(changed))
        with self.assertRaisesRegex(PrivateResearchIntakeError, "AUTHORITY_MISMATCH"):
            read_private_research_intake(self.root, intake["intake_id"])

    def test_complete_84_day_historical_cogs_can_prove_abc_without_current_cost(self):
        start = date(2025, 10, 11)
        end = start + timedelta(days=83)
        rows: list[dict[str, object]] = []
        for index in range(84):
            current = start + timedelta(days=index)
            rows.append(
                {
                    "business_date": current.isoformat(),
                    "shopify_variant_id": "1001",
                    "net_units": "1",
                    "net_revenue": "15.00",
                    "historical_cogs": "7.00",
                }
            )

        payload, lines = _jsonl(rows)
        (self.root / "input" / "sales.jsonl").write_bytes(payload)
        manifest = {
            "contract": DAILY_SALES_SNAPSHOT_CONTRACT,
            "data_mode": PRIVATE_REAL_SOURCE_REVIEW,
            "authority": dict(SOURCE_AUTHORITY),
            "snapshot_id": "sales-84-day-fixture",
            "source": {
                "system": "SHOPIFYQL_SALES",
                "store_identity": "fixture-store",
                "store_timezone": "America/New_York",
            },
            "extraction": {
                "started_at_utc": "2026-01-03T12:02:00Z",
                "completed_at_utc": "2026-01-03T12:03:00Z",
                "snapshot_at_utc": "2026-01-03T12:02:30Z",
            },
            "date_range": {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "complete_through_date": end.isoformat(),
                "store_timezone": "America/New_York",
                "complete_day_count": 84,
                "missing_dates": [],
            },
            "population": {"row_count": 84, "distinct_variant_count": 1},
            "query": {"identity": "DAILY_SALES_QUERY_V1", "sha256": "b" * 64},
            "pagination": _pages(lines, page_size=20),
            "blob": {
                "path": "input/sales.jsonl",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "format": JSONL_FORMAT,
                "schema": list(DAILY_SALES_ROW_FIELDS),
            },
        }
        (self.root / "input" / "sales-manifest.json").write_bytes(
            canonical_json_bytes(manifest)
        )
        intake = self._build()
        self.assertTrue(intake["coverage"]["abc_cohort"]["coverage_complete"])
        variants = {row["shopify_variant_id"]: row for row in intake["variants"]}
        self.assertEqual(variants["1001"]["historical_revenue"], "1260")
        self.assertEqual(variants["1001"]["historical_cogs"], "588")
        self.assertEqual(variants["1001"]["gross_profit_dollars"], "672")
        # Variant 1002 has a complete, attested zero series; its current cost is
        # never substituted for historical COGS.
        self.assertEqual(variants["1002"]["historical_cogs"], "0")
        self.assertEqual(variants["1002"]["current_inventory_item_cost"], "8.00")


if __name__ == "__main__":
    unittest.main()
