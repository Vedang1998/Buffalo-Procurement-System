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
    SHOPIFY_CATALOG_CAPTURE_CONTRACT,
    SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT,
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

    def _write_native_captures(self) -> None:
        catalog_dir = self.root / "native-catalog"
        sales_dir = self.root / "native-sales"
        (catalog_dir / "parts").mkdir(parents=True, exist_ok=True)
        (sales_dir / "parts").mkdir(parents=True, exist_ok=True)

        catalog_query = b"query NativeCatalog { products(first: 20) { nodes { id } } }\n"
        (catalog_dir / "query.graphql").write_bytes(catalog_query)

        def variant(
            variant_id: str,
            item_id: str,
            level_id: str,
            *,
            sku: str,
            price: str,
            cost: str | None,
            available: int,
            on_hand: int,
            committed: int,
            incoming: int,
        ) -> dict[str, object]:
            unit_cost = None if cost is None else {"amount": cost, "currencyCode": "USD"}
            return {
                "id": f"gid://shopify/ProductVariant/{variant_id}",
                "title": "750 mL",
                "sku": sku,
                "barcode": None,
                "price": price,
                "compareAtPrice": None,
                "inventoryQuantity": available,
                "updatedAt": "2026-08-25T12:00:00Z",
                "selectedOptions": [{"name": "Size", "value": "750 mL"}],
                "inventoryItem": {
                    "id": f"gid://shopify/InventoryItem/{item_id}",
                    "tracked": True,
                    "updatedAt": "2026-08-25T12:00:00Z",
                    "unitCost": unit_cost,
                    "inventoryLevels": {
                        "pageInfo": {"hasNextPage": False, "endCursor": "level-end"},
                        "nodes": [
                            {
                                "id": f"gid://shopify/InventoryLevel/{level_id}",
                                "updatedAt": "2026-08-25T12:00:00Z",
                                "location": {
                                    "id": "gid://shopify/Location/501",
                                    "name": "Fixture Location",
                                    "isActive": True,
                                },
                                "quantities": [
                                    {
                                        "name": "available",
                                        "quantity": available,
                                        "updatedAt": "2026-08-25T12:00:00Z",
                                    },
                                    {
                                        "name": "on_hand",
                                        "quantity": on_hand,
                                        "updatedAt": "2026-08-25T12:00:00Z",
                                    },
                                    {
                                        "name": "committed",
                                        "quantity": committed,
                                        "updatedAt": None,
                                    },
                                    {
                                        "name": "incoming",
                                        "quantity": incoming,
                                        "updatedAt": None,
                                    },
                                ],
                            }
                        ],
                    },
                },
            }

        catalog_products = [
            {
                "id": "gid://shopify/Product/71",
                "title": " Fixture Native Product ",
                "handle": "fixture-native-product",
                "vendor": "Fixture Brand",
                "productType": "Wine",
                "status": "ACTIVE",
                "tags": ["fixture"],
                "updatedAt": "2026-08-25T12:00:00Z",
                "variants": {
                    "pageInfo": {"hasNextPage": False, "endCursor": "variant-end"},
                    "nodes": [
                        variant(
                            "1001",
                            "91",
                            "191",
                            sku=" SKU-1 ",
                            price="15.00",
                            cost="5.00",
                            available=2,
                            on_hand=3,
                            committed=1,
                            incoming=4,
                        ),
                        variant(
                            "1002",
                            "92",
                            "192",
                            sku="SKU-2",
                            price="20.00",
                            cost="8.00",
                            available=0,
                            on_hand=0,
                            committed=0,
                            incoming=0,
                        ),
                    ],
                },
            }
        ]
        catalog_part = b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
            for row in catalog_products
        )
        (catalog_dir / "parts" / "catalog.jsonl").write_bytes(catalog_part)
        catalog_manifest = {
            "contract": SHOPIFY_CATALOG_CAPTURE_CONTRACT,
            "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
            "approval_state": "PROPOSED_UNAPPROVED_REVIEW_ONLY",
            "captured_at_utc": "2026-08-25T12:00:00Z",
            "capture_timing_note": "Synthetic complete capture",
            "source": {
                "connector": "ALREADY_CONNECTED_SHOPIFY_READ_ONLY",
                "shop": {
                    "name": "Fixture Shop",
                    "domain": "fixture.example",
                    "currency_code": "USD",
                    "timezone": "EDT",
                    "country": "US",
                },
                "query_path": "query.graphql",
                "query_bytes": len(catalog_query),
                "query_sha256": hashlib.sha256(catalog_query).hexdigest(),
                "page_size": 20,
                "page_count": 1,
                "pagination_complete": True,
                "nested_variant_pagination_complete": True,
                "nested_inventory_location_pagination_complete": True,
                "omitted_current_day_demand": True,
            },
            "population": {"products": 1, "variants": 2},
            "parts": [
                {
                    "path": "parts/catalog.jsonl",
                    "bytes": len(catalog_part),
                    "sha256": hashlib.sha256(catalog_part).hexdigest(),
                    "rows": 1,
                }
            ],
            "limitations": ["SYNTHETIC_FIXTURE_ONLY"],
        }
        (catalog_dir / "manifest.json").write_bytes(
            (json.dumps(catalog_manifest, indent=2) + "\n").encode("utf-8")
        )

        sales_query = b"FROM sales SHOW net_items_sold GROUP BY day, product_variant_id\n"
        (sales_dir / "query-template.shopifyql").write_bytes(sales_query)
        start = date(2026, 6, 1)
        end = start + timedelta(days=83)
        sales_rows = [
            {
                "day": start.isoformat(),
                "product_id": "71",
                "product_variant_id": "1001",
                "product_title": "Fixture Native Product",
                "product_variant_title": "750 mL",
                "net_items_sold": "2",
                "gross_sales": "30.00",
                "returns": "0.00",
                "net_sales": "30.00",
                "cost_of_goods_sold": "5.00",
                "gross_profit": "25.00",
            },
            {
                "day": start.isoformat(),
                "product_id": "79",
                "product_variant_id": "9999",
                "product_title": "Historical Fixture",
                "product_variant_title": "Old",
                "net_items_sold": "1",
                "gross_sales": "10.00",
                "returns": "0.00",
                "net_sales": "10.00",
                "cost_of_goods_sold": "3.00",
                "gross_profit": "7.00",
            },
            {
                "day": start.isoformat(),
                "product_id": "",
                "product_variant_id": "",
                "product_title": "",
                "product_variant_title": "",
                "net_items_sold": "-1",
                "gross_sales": "0.00",
                "returns": "-4.00",
                "net_sales": "-4.00",
                "cost_of_goods_sold": "0.00",
                "gross_profit": "-4.00",
            },
        ]
        sales_part = b"".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
            for row in sales_rows
        )
        (sales_dir / "parts" / "sales.jsonl").write_bytes(sales_part)
        columns = [
            ("day", "DAY_TIMESTAMP"),
            ("product_id", "IDENTITY"),
            ("product_variant_id", "IDENTITY"),
            ("product_title", "STRING"),
            ("product_variant_title", "STRING"),
            ("net_items_sold", "INTEGER"),
            ("gross_sales", "MONEY"),
            ("returns", "MONEY"),
            ("net_sales", "MONEY"),
            ("cost_of_goods_sold", "MONEY"),
            ("gross_profit", "MONEY"),
        ]
        sales_manifest = {
            "contract": SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT,
            "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
            "approval_state": "PROPOSED_UNAPPROVED_REVIEW_ONLY",
            "captured_at_utc": "2026-08-24T12:00:00Z",
            "source": {
                "connector": "ALREADY_CONNECTED_SHOPIFY_READ_ONLY_ANALYTICS",
                "shop": {
                    "domain": "fixture.example",
                    "currency_code": "USD",
                    "timezone": "EDT",
                },
                "query_template_path": "query-template.shopifyql",
                "query_template_bytes": len(sales_query),
                "query_template_sha256": hashlib.sha256(sales_query).hexdigest(),
                "first_complete_business_date": start.isoformat(),
                "last_complete_business_date": end.isoformat(),
                "business_days": 84,
                "local_capture_day_omitted_as_partial": (end + timedelta(days=1)).isoformat(),
                "one_query_per_business_date": True,
                "connector_row_ceiling": 1000,
                "every_query_below_row_ceiling": True,
                "population": "ALL_VARIANT_GROUPS_RETURNED_BY_UNFILTERED_DAILY_SALES_QUERY",
                "absent_variant_day_semantics": (
                    "OBSERVED_ZERO_ONLY_FOR_DAYS_WITH_A_RECORDED_COMPLETE_QUERY; "
                    "NEVER IMPUTE AN UNQUERIED DAY"
                ),
            },
            "columns": [
                {"name": name, "dataType": data_type} for name, data_type in columns
            ],
            "day_queries": [
                {
                    "business_date": (start + timedelta(days=index)).isoformat(),
                    "row_count": 3 if index == 0 else 0,
                }
                for index in range(84)
            ],
            "totals": {"rows": 3},
            "parts": [
                {
                    "path": "parts/sales.jsonl",
                    "bytes": len(sales_part),
                    "sha256": hashlib.sha256(sales_part).hexdigest(),
                    "rows": 3,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                }
            ],
            "limitations": ["SYNTHETIC_FIXTURE_ONLY"],
        }
        (sales_dir / "manifest.json").write_bytes(
            (json.dumps(sales_manifest, indent=2) + "\n").encode("utf-8")
        )

    def _build_native(self) -> dict[str, object]:
        return build_private_research_intake(
            self.root,
            catalog_manifest_path="native-catalog/manifest.json",
            daily_sales_manifest_path="native-sales/manifest.json",
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

    def test_native_shopify_captures_preserve_unjoined_history_and_inventory_evidence(self):
        self._write_native_captures()
        intake = self._build_native()
        coverage = intake["coverage"]
        self.assertEqual(coverage["current_catalog_population"], 2)
        self.assertEqual(coverage["sales_source_row_count"], 3)
        self.assertEqual(coverage["sales_normalized_current_row_count"], 1)
        self.assertEqual(coverage["sales_distinct_variant_count"], 3)
        self.assertEqual(coverage["sales_variants_joined_to_current_catalog"], 1)
        self.assertEqual(coverage["sales_variants_not_in_current_catalog"], 2)
        self.assertEqual(coverage["sales_historical_unjoined_source_row_count"], 2)
        self.assertEqual(
            coverage["sales_current_catalog_variants_observed_zero_all_days"], 1
        )
        self.assertRegex(
            coverage["sales_historical_unjoined_variant_identities_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertFalse(coverage["abc_cohort"]["coverage_complete"])
        self.assertEqual(coverage["abc_cohort"]["scope_id"], None)
        self.assertIn("NOT_CONFIGURED", coverage["abc_cohort"]["basis"])

        variants = {row["shopify_variant_id"]: row for row in intake["variants"]}
        self.assertEqual(set(variants), {"1001", "1002"})
        first = variants["1001"]
        self.assertEqual(first["product_title"], "Fixture Native Product")
        self.assertEqual(first["shopify_sku"], "SKU-1")
        self.assertEqual(first["available"], "2")
        self.assertEqual(first["on_hand"], "3")
        self.assertEqual(first["committed"], "1")
        self.assertEqual(first["raw_incoming"], "4")
        self.assertIsNone(first["incoming"])
        self.assertEqual(first["raw_incoming_trust"], "UNTRUSTED_CAPTURE_ONLY")
        self.assertEqual(first["current_inventory_item_cost"], "5")
        self.assertEqual(first["current_inventory_item_cost_currency"], "USD")
        self.assertEqual(first["historical_cogs"], "5")
        self.assertEqual(first["historical_revenue"], "30")
        self.assertEqual(first["sales_history"]["gross_sales_series"][0], "30")
        self.assertEqual(first["sales_history"]["returns_series"][0], "0")
        self.assertEqual(
            first["sales_history"]["source_gross_profit_series"][0], "25"
        )
        self.assertEqual(
            first["inventory_evidence"]["locations"][0]["location_id"],
            "gid://shopify/Location/501",
        )

        catalog_capture = intake["sources"]["catalog"]["native_capture"]
        sales_capture = intake["sources"]["daily_sales"]["native_capture"]
        self.assertEqual(catalog_capture["contract"], SHOPIFY_CATALOG_CAPTURE_CONTRACT)
        self.assertEqual(
            sales_capture["contract"], SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT
        )
        self.assertIsNone(catalog_capture["unjoined_sales_blob"])
        self.assertIsNotNone(sales_capture["unjoined_sales_blob"])
        self.assertEqual(
            read_private_research_intake(self.root, intake["intake_id"]), intake
        )
        manifest_path = self.root / intake_manifest_key(intake["intake_id"])
        prior_mtime = manifest_path.stat().st_mtime_ns
        self.assertEqual(self._build_native(), intake)
        self.assertEqual(manifest_path.stat().st_mtime_ns, prior_mtime)

    def test_native_capture_rejects_manifest_part_and_daily_completeness_drift(self):
        self._write_native_captures()
        catalog_manifest_path = self.root / "native-catalog" / "manifest.json"
        manifest = json.loads(catalog_manifest_path.read_text())
        manifest["unexpected"] = True
        catalog_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "SCHEMA_MISMATCH"):
            self._build_native()

        self._write_native_captures()
        (self.root / "native-catalog" / "parts" / "catalog.jsonl").write_bytes(
            b"tampered\n"
        )
        with self.assertRaisesRegex(PrivateResearchIntakeError, "CONTENT_HASH_MISMATCH"):
            self._build_native()

        self._write_native_captures()
        sales_manifest_path = self.root / "native-sales" / "manifest.json"
        manifest = json.loads(sales_manifest_path.read_text())
        manifest["day_queries"].pop()
        sales_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        with self.assertRaisesRegex(
            PrivateResearchIntakeError, "DATE_COVERAGE_INCOMPLETE"
        ):
            self._build_native()

        self._write_native_captures()
        manifest = json.loads(catalog_manifest_path.read_text())
        manifest["source"]["nested_inventory_location_pagination_complete"] = False
        catalog_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "PAGINATION_INCOMPLETE"):
            self._build_native()

    def test_native_capture_rejects_unsafe_paths_duplicate_facts_and_mixed_contracts(self):
        self._write_native_captures()
        catalog_manifest_path = self.root / "native-catalog" / "manifest.json"
        manifest = json.loads(catalog_manifest_path.read_text())
        manifest["parts"][0]["path"] = "../input/catalog.jsonl"
        catalog_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "UNSAFE_PATH"):
            self._build_native()

        self._write_native_captures()
        sales_part_path = self.root / "native-sales" / "parts" / "sales.jsonl"
        lines = sales_part_path.read_bytes().splitlines(keepends=True)
        changed = b"".join([*lines, lines[0]])
        sales_part_path.write_bytes(changed)
        sales_manifest_path = self.root / "native-sales" / "manifest.json"
        manifest = json.loads(sales_manifest_path.read_text())
        manifest["parts"][0].update(
            {
                "bytes": len(changed),
                "sha256": hashlib.sha256(changed).hexdigest(),
                "rows": 4,
            }
        )
        manifest["totals"]["rows"] = 4
        manifest["day_queries"][0]["row_count"] = 4
        sales_manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "DUPLICATE_SALES_FACT"):
            self._build_native()

        self._write_native_captures()
        with self.assertRaisesRegex(PrivateResearchIntakeError, "MIXED_SOURCE_CONTRACTS"):
            build_private_research_intake(
                self.root,
                catalog_manifest_path="native-catalog/manifest.json",
                daily_sales_manifest_path="input/sales-manifest.json",
                a1_package_path="a1",
            )

    def test_native_readback_rehashes_raw_part_and_unjoined_evidence_blobs(self):
        self._write_native_captures()
        intake = self._build_native()
        sales_capture = intake["sources"]["daily_sales"]["native_capture"]
        raw_part = next(
            item["blob"]["key"]
            for item in sales_capture["files"]
            if item["path"].endswith("sales.jsonl")
        )
        (self.root / raw_part).write_bytes(b"tampered raw capture\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "CONTENT_HASH_MISMATCH"):
            read_private_research_intake(self.root, intake["intake_id"])

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        os.chmod(self.root, 0o700)
        (self.root / "input").mkdir()
        (self.root / "a1").mkdir()
        self._write_catalog(self.catalog_rows)
        self._write_sales(self.sales_rows)
        self._write_native_captures()
        intake = self._build_native()
        unjoined = intake["sources"]["daily_sales"]["native_capture"][
            "unjoined_sales_blob"
        ]["key"]
        (self.root / unjoined).write_bytes(b"tampered unjoined evidence\n")
        with self.assertRaisesRegex(PrivateResearchIntakeError, "CONTENT_HASH_MISMATCH"):
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
