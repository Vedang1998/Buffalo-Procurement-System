"""Packet 4 strict normalized price-book staging and promotion tests."""

from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import inspect
import io
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urljoin
import uuid
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from postgres_test_support import validated_test_connection
from procurement_os import api, price_book
from procurement_os.price_book import (
    PRICE_BOOK_HEADERS,
    PriceBookError,
    get_price_book_batch,
    list_price_book_batches,
    normalized_price_book_template,
    parse_price_book_csv,
    price_book_policy,
    promote_price_book_batch,
    read_raw_price_book,
    reject_price_book_batch,
    stage_and_validate_price_book,
)
from procurement_os.storage import LocalFilesystemStorage, StorageAdapter
from procurement.tools.import_seed_csv import import_seed


DB_DIR = Path(__file__).resolve().parents[1] / "db"
MIGRATIONS = (
    "schema_postgres.sql",
    "001_v1_3_catalog_sales.sql",
    "002_seed_import_records.sql",
    "003_phase3_reconciliation.sql",
    "004_identity_decision_invariants.sql",
    "005_identity_investigation.sql",
    "006_phase4_sales_backfill.sql",
    "007_phase4_terminal_disposition.sql",
    "008_monday_inventory_foundation.sql",
    "009_monday_vendor_rules.sql",
    "010_monday_po_ledger.sql",
    "011_monday_price_book_staging.sql",
)


BUFFALO_BUSINESS_DATE = datetime.now(ZoneInfo("America/New_York")).date()
CURRENT_MONTH_START = BUFFALO_BUSINESS_DATE.replace(day=1)
NEXT_MONTH_START = (CURRENT_MONTH_START + timedelta(days=32)).replace(day=1)
FOLLOWING_MONTH_START = (NEXT_MONTH_START + timedelta(days=32)).replace(day=1)
NEXT_MONTH_END = FOLLOWING_MONTH_START - timedelta(days=1)


def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=PRICE_BOOK_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def base_row(**changes: str) -> dict[str, str]:
    row = {
        "batch_ref": f"empire-{NEXT_MONTH_START.isoformat()}-future",
        "target_price_state": "future",
        "effective_from": NEXT_MONTH_START.isoformat(),
        "effective_through": NEXT_MONTH_END.isoformat(),
        "vendor_name": "Empire Fixture",
        "supplier_sku": "EMP-1001",
        "supplier_description": "Fixture Bourbon 750ML",
        "canonical_variant_id": "1001",
        "package_type": "STANDARD",
        "size_text": "750ML",
        "raw_pack": "12x750ML",
        "shopify_units_per_case": "12",
        "qualifying_units_per_case": "12",
        "assortment_scope": "PRODUCT",
        "assortment_group": "",
        "assortable": "true",
        "assortment_evidence": "book product program",
        "level_type": "BASE",
        "break_quantity": "",
        "break_unit": "",
        "case_price": "120.00",
        "unit_price": "10.00",
        "source_file": "synthetic-empire-fixture.csv",
        "source_page": "1",
        "source_evidence": "synthetic normalized row for software testing",
        "extraction_confidence": "VERIFIED",
        "review_note": "synthetic only",
    }
    row.update(changes)
    return row


class PriceBookPureTests(unittest.TestCase):
    def test_template_has_exact_order_and_no_caller_authority_columns(self):
        self.assertEqual(
            normalized_price_book_template().decode(),
            ",".join(PRICE_BOOK_HEADERS) + "\n",
        )
        for forbidden in ("offer_id", "verified", "status", "price_id"):
            self.assertNotIn(forbidden, PRICE_BOOK_HEADERS)

    def test_header_encoding_and_envelope_are_strict(self):
        good = base_row()
        for payload in (
            b"\xff\xfeinvalid",
            b"vendor_name,batch_ref\nFixture,batch\n",
            csv_bytes([good, {**good, "vendor_name": "Other"}]),
            csv_bytes([good, {**good, "effective_from": "bad-date"}]),
        ):
            with self.subTest(payload=payload[:30]), self.assertRaises(PriceBookError):
                parse_price_book_csv(payload)

        for target in ("current", "CURRENT", "FUTURE", " future "):
            with self.subTest(target=target), self.assertRaisesRegex(
                PriceBookError, "FUTURE only"
            ):
                parse_price_book_csv(csv_bytes([base_row(target_price_state=target)]))

    def test_numeric_level_and_assortment_contracts_fail_closed(self):
        cases = (
            {"unit_price": "NaN"},
            {"unit_price": "0"},
            {"unit_price": "1.00001"},
            {"level_type": "BASE", "break_quantity": "10", "break_unit": "BT"},
            {"level_type": "BREAK", "break_quantity": "1.5", "break_unit": "BT"},
            {"level_type": "BREAK", "break_quantity": "12", "break_unit": "EA"},
            {"assortment_scope": "EXPLICIT_CROSS_PRODUCT", "assortment_group": ""},
            {"assortable": "maybe"},
            {"extraction_confidence": "REVIEW"},
            {"size_text": ""},
            {"raw_pack": ""},
            {"shopify_units_per_case": "12.5"},
            {"qualifying_units_per_case": "6.5"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                row = parse_price_book_csv(csv_bytes([base_row(**changes)]))["rows"][0]
                self.assertTrue(row["errors"])

    def test_case_unit_penny_delta_warns_but_material_delta_fails(self):
        warning = parse_price_book_csv(
            csv_bytes([base_row(case_price="120.06", unit_price="10.00")])
        )["rows"][0]
        self.assertFalse(warning["errors"])
        self.assertEqual(warning["warnings"][0]["code"], "CASE_UNIT_ROUNDING_DELTA")
        error = parse_price_book_csv(
            csv_bytes([base_row(case_price="121.20", unit_price="10.00")])
        )["rows"][0]
        self.assertIn("CASE_UNIT_PRICE_MISMATCH", {x["code"] for x in error["errors"]})

    def test_policy_comes_from_canonical_rules(self):
        self.assertEqual(
            price_book_policy(),
            {
                "strict_normalized_import_contract": True,
                "staging_required": True,
                "transactional_promotion_required": True,
                "archive_enabled": False,
            },
        )

    def test_service_has_no_shopify_po_or_legacy_rollover_path(self):
        source = inspect.getsource(price_book)
        self.assertNotIn("ShopifyGraphQLClient", source)
        self.assertNotIn("shopify_admin", source)
        self.assertNotIn("purchase_orders", source)
        self.assertNotIn("rollover(", source)
        self.assertIn("v_verified_current_prices", source)


class FailingStorage(StorageAdapter):
    def put_bytes(self, key: str, data: bytes) -> None:
        raise OSError("synthetic storage failure")

    def get_bytes(self, key: str) -> bytes:
        raise FileNotFoundError(key)

    def exists(self, key: str) -> bool:
        return False

    def list_keys(self, prefix: str = "") -> list[str]:
        return []


class PriceBookPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from psycopg import sql

        self.conn, self.test_target, self.test_database_info = validated_test_connection()
        self.schema = f"price_book_mvp_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for migration in MIGRATIONS:
            self.conn.execute((DB_DIR / migration).read_text(encoding="utf-8"))
        self.conn.commit()
        self.temp = TemporaryDirectory()
        self.storage = LocalFilesystemStorage(self.temp.name)

    def tearDown(self) -> None:
        from psycopg import sql

        try:
            self.conn.rollback()
            self.conn.execute("SET search_path TO public")
            self.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
            self.conn.commit()
        finally:
            self.conn.close()
            self.temp.cleanup()

    def add_vendor(self, *, name: str = "Empire Fixture", active: bool = True) -> str:
        return str(
            self.conn.execute(
                "INSERT INTO vendors(vendor_name,active) VALUES (%s,%s) RETURNING vendor_id",
                (name, active),
            ).fetchone()[0]
        )

    def add_variant(
        self,
        variant_id: str = "1001",
        *,
        active: bool = True,
        catalog_state: str = "LIVE",
        identity_scope: str = "CURRENT",
    ) -> None:
        product_id = f"product-{variant_id}" if identity_scope == "CURRENT" else None
        extra = (
            (None, None, None, None, None, None)
            if identity_scope == "CURRENT"
            else ("a" * 64, 1, "fixture-v1", "fixture-owner", "b" * 40, "c" * 40)
        )
        self.conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,active,
                   catalog_state,identity_scope,restoration_manifest_sha256,
                   restoration_manifest_row_number,restoration_evidence_version,
                   restoration_owner_authorization,restoration_authority_git_sha,
                   restoration_execution_git_sha
               ) VALUES (%s,%s,%s,'750ML',%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                variant_id, product_id, f"Fixture {variant_id}", active,
                catalog_state, identity_scope, *extra,
            ),
        )

    def add_offer(
        self,
        *,
        variant_id: str = "1001",
        vendor_id: str,
        sku: str = "EMP-1001",
        confidence: str = "VERIFIED",
        active: bool = True,
        raw_pack: str = "12x750ML",
    ) -> int:
        return int(
            self.conn.execute(
                """INSERT INTO supplier_offers(
                       variant_id,vendor_id,supplier_sku,supplier_description,
                       package_type,size_text,raw_pack,shopify_units_per_case,
                       qualifying_units_per_case,assortment_scope,assortable,
                       active,confidence
                   ) VALUES (%s,%s,%s,'Fixture Bourbon 750ML','STANDARD','750ML',
                             %s,12,12,'PRODUCT',TRUE,%s,%s)
                   RETURNING offer_id""",
                (variant_id, vendor_id, sku, raw_pack, active, confidence),
            ).fetchone()[0]
        )

    def prepare_offer(self, **offer_changes) -> tuple[str, int]:
        vendor_id = self.add_vendor()
        self.add_variant()
        offer_id = self.add_offer(vendor_id=vendor_id, **offer_changes)
        self.conn.commit()
        return vendor_id, offer_id

    def insert_grandfathered_price(
        self,
        offer_id: int,
        *,
        unit_price: str = "11",
        source_file: str = "pre-011-legacy-current",
    ) -> int:
        """Model a row that existed before migration 011 installed its guards."""
        self.conn.execute("ALTER TABLE prices DISABLE TRIGGER USER")
        try:
            price_id = self.conn.execute(
                """INSERT INTO prices(
                       offer_id,price_state,effective_month,level_type,break_qty,
                       unit_price,source_file,verified
                   ) VALUES (%s,'current','2026-08-01','BASE',1,%s,%s,TRUE)
                   RETURNING price_id""",
                (offer_id, unit_price, source_file),
            ).fetchone()[0]
        finally:
            self.conn.execute("ALTER TABLE prices ENABLE TRIGGER USER")
        return int(price_id)

    def stage(self, rows: list[dict[str, str]], *, actor: str = "fixture-owner"):
        self.conn.commit()
        result = stage_and_validate_price_book(
            self.conn, self.storage, csv_bytes=csv_bytes(rows), actor=actor
        )
        self.conn.commit()
        return result

    @contextmanager
    def api_connection(self):
        yield self.conn

    def test_valid_staging_is_nonoperational_and_raw_evidence_is_verified(self):
        self.prepare_offer()
        before = self.conn.execute("SELECT count(*) FROM prices").fetchone()[0]
        result = self.stage([base_row()])
        self.assertEqual(result["status"], "VALIDATED")
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["expected_offer_count"], 1)
        self.assertEqual(result["covered_offer_count"], 1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM prices").fetchone()[0], before)
        raw = read_raw_price_book(
            self.conn, self.storage, batch_id=str(result["price_book_batch_id"])
        )
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["content_sha256"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM purchase_orders").fetchone()[0], 0)

    def test_exact_replay_is_read_only_and_changed_bytes_same_ref_reject(self):
        self.prepare_offer()
        first = self.stage([base_row()])
        before = self.conn.execute(
            """SELECT (SELECT count(*) FROM price_book_batches),
                      (SELECT count(*) FROM price_book_staging_rows),
                      (SELECT count(*) FROM change_log)"""
        ).fetchone()
        self.conn.commit()
        replay = self.stage([base_row()])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(first["price_book_batch_id"], replay["price_book_batch_id"])
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM price_book_batches),
                          (SELECT count(*) FROM price_book_staging_rows),
                          (SELECT count(*) FROM change_log)"""
            ).fetchone(),
            before,
        )
        self.conn.commit()
        with self.assertRaisesRegex(PriceBookError, "different immutable input"):
            self.stage([base_row(review_note="different bytes")])

    def test_storage_failure_precedes_every_database_row(self):
        self.prepare_offer()
        with self.assertRaisesRegex(OSError, "synthetic storage"):
            stage_and_validate_price_book(
                self.conn,
                FailingStorage(),
                csv_bytes=csv_bytes([base_row()]),
                actor="fixture-owner",
            )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM price_book_batches").fetchone()[0], 0)

    def test_unknown_or_inactive_vendor_is_visible_invalid_batch(self):
        unknown = self.stage([base_row(vendor_name="Unknown Distributor", batch_ref="unknown")])
        self.assertEqual(unknown["status"], "INVALID")
        self.assertIn("UNKNOWN_VENDOR", {issue["issue_code"] for issue in unknown["issues"]})
        self.add_vendor(active=False)
        self.conn.commit()
        inactive = self.stage([base_row(batch_ref="inactive")])
        self.assertEqual(inactive["status"], "INVALID")
        self.assertIn("INACTIVE_VENDOR", {issue["issue_code"] for issue in inactive["issues"]})

    def test_mapping_must_be_exact_verified_and_procurement_eligible(self):
        vendor_id = self.add_vendor()
        self.add_variant()
        self.add_offer(vendor_id=vendor_id, confidence="REVIEW")
        self.conn.commit()
        unverified = self.stage([base_row()])
        self.assertIn("MAPPING_NOT_VERIFIED", {issue["issue_code"] for issue in unverified["issues"]})
        self.assertEqual(self.conn.execute("SELECT count(*) FROM supplier_offers").fetchone()[0], 1)

        # A separate inactive/non-LIVE mapping cannot be promoted through a guessed identity.
        vendor2 = self.add_vendor(name="Other Fixture")
        self.add_variant("2002", catalog_state="REVIEW")
        self.add_offer(variant_id="2002", vendor_id=vendor2, sku="OTH-2002")
        self.conn.commit()
        row = base_row(
            batch_ref="other", vendor_name="Other Fixture", supplier_sku="OTH-2002",
            canonical_variant_id="2002",
        )
        result = self.stage([row])
        self.assertIn("VARIANT_NOT_ELIGIBLE", {issue["issue_code"] for issue in result["issues"]})

    def test_pack_size_and_assortment_drift_never_rewrites_offer(self):
        _vendor, offer_id = self.prepare_offer()
        before = self.conn.execute(
            """SELECT package_type,size_text,raw_pack,shopify_units_per_case,
                      qualifying_units_per_case,assortment_scope,assortable
                 FROM supplier_offers WHERE offer_id=%s""",
            (offer_id,),
        ).fetchone()
        result = self.stage([
            base_row(raw_pack="6x750ML", shopify_units_per_case="6", assortable="false")
        ])
        codes = {issue["issue_code"] for issue in result["issues"]}
        self.assertTrue({"OFFER_RAW_PACK_MISMATCH", "OFFER_ASSORTABLE_MISMATCH"} <= codes)
        self.assertEqual(
            self.conn.execute(
                """SELECT package_type,size_text,raw_pack,shopify_units_per_case,
                          qualifying_units_per_case,assortment_scope,assortable
                     FROM supplier_offers WHERE offer_id=%s""",
                (offer_id,),
            ).fetchone(),
            before,
        )

    def test_duplicate_or_increasing_ladder_is_invalid_but_typed_units_may_mix(self):
        self.prepare_offer()
        rows = [
            base_row(),
            base_row(level_type="BREAK", break_quantity="12", break_unit="CS",
                     case_price="126", unit_price="10.50"),
            base_row(level_type="BREAK", break_quantity="12", break_unit="CS",
                     case_price="114", unit_price="9.50"),
            base_row(level_type="BREAK", break_quantity="24", break_unit="BT",
                     case_price="", unit_price="9.00"),
        ]
        result = self.stage(rows)
        codes = {issue["issue_code"] for issue in result["issues"]}
        self.assertTrue(
            {"DUPLICATE_LADDER_LEVEL", "LADDER_PRICE_INCREASE"} <= codes
        )
        self.assertNotIn("MIXED_BREAK_UNITS", codes)
        self.assertEqual(result["status"], "INVALID")

    def test_valid_mixed_bt_and_cs_programs_remain_distinct(self):
        self.prepare_offer()
        result = self.stage([
            base_row(),
            base_row(level_type="BREAK", break_quantity="12", break_unit="BT",
                     case_price="", unit_price="9.50"),
            base_row(level_type="BREAK", break_quantity="3", break_unit="CS",
                     case_price="108", unit_price="9.00"),
        ])
        self.assertEqual(result["status"], "VALIDATED")
        self.assertNotIn("MIXED_BREAK_UNITS", {i["issue_code"] for i in result["issues"]})

    def test_missing_expected_offer_blocks_complete_batch(self):
        vendor_id, _offer = self.prepare_offer()
        self.add_variant("1002")
        self.add_offer(variant_id="1002", vendor_id=vendor_id, sku="EMP-1002")
        self.conn.commit()
        result = self.stage([base_row()])
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["expected_offer_count"], 2)
        self.assertEqual(result["missing_offer_count"], 1)
        self.assertIn("MISSING_REQUIRED_OFFER", {i["issue_code"] for i in result["issues"]})

    def test_invalid_batch_cannot_promote_or_change_active_prices(self):
        self.prepare_offer()
        result = self.stage([base_row(unit_price="0")])
        before = self.conn.execute("SELECT count(*),coalesce(sum(unit_price),0) FROM prices").fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(PriceBookError, "complete FUTURE VALIDATED"):
            promote_price_book_batch(
                self.conn,
                self.storage,
                batch_id=str(result["price_book_batch_id"]),
                expected_validation_fingerprint=result["validation_fingerprint"],
                actor="fixture-owner",
            )
        self.assertEqual(
            self.conn.execute("SELECT count(*),coalesce(sum(unit_price),0) FROM prices").fetchone(),
            before,
        )

    def test_verified_legacy_current_remains_trusted_and_upload_cannot_target_current(self):
        vendor_id, offer_id = self.prepare_offer()
        price_id = self.insert_grandfathered_price(
            offer_id, unit_price="99", source_file="pre-011-legacy-seed"
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT offer_id,unit_price FROM v_verified_current_prices WHERE vendor_id=%s",
                (vendor_id,),
            ).fetchall(),
            [(offer_id, Decimal("99.0000"))],
        )
        for statement in (
            "UPDATE prices SET unit_price=98 WHERE price_id=%s",
            "DELETE FROM prices WHERE price_id=%s",
        ):
            with self.subTest(statement=statement), self.assertRaisesRegex(
                Exception, "grandfathered.*immutable"
            ):
                self.conn.execute(statement, (price_id,))
            self.conn.rollback()
        with self.assertRaisesRegex(Exception, "unprovenanced"):
            self.conn.execute(
                """INSERT INTO prices(offer_id,price_state,effective_month,level_type,
                                      break_qty,unit_price,source_file,verified)
                   VALUES (%s,'current','2026-08-01','BASE',2,98,'bypass',TRUE)""",
                (offer_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "supplier offer contract"):
            self.conn.execute(
                "UPDATE supplier_offers SET supplier_sku='REBOUND' WHERE offer_id=%s",
                (offer_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "vendor identity"):
            self.conn.execute("UPDATE vendors SET active=FALSE WHERE vendor_id=%s", (vendor_id,))
        self.conn.rollback()
        before = self.conn.execute(
            "SELECT count(*) FROM price_book_batches"
        ).fetchone()[0]
        with self.assertRaisesRegex(PriceBookError, "FUTURE only"):
            stage_and_validate_price_book(
                self.conn,
                FailingStorage(),
                csv_bytes=csv_bytes([base_row(target_price_state="current")]),
                actor="fixture-owner",
            )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM price_book_batches").fetchone()[0], before
        )

    def test_future_promotion_never_touches_current(self):
        _vendor, offer_id = self.prepare_offer()
        self.insert_grandfathered_price(offer_id)
        before = self.conn.execute(
            "SELECT price_id,unit_price,source_file FROM prices WHERE price_state='current'"
        ).fetchall()
        self.conn.commit()
        row = base_row()
        staged = self.stage([row])
        promoted = promote_price_book_batch(
            self.conn, self.storage, batch_id=str(staged["price_book_batch_id"]),
            expected_validation_fingerprint=staged["validation_fingerprint"], actor="owner",
            warning_review_reason="Reviewed exact CURRENT-to-FUTURE unit price change",
        )
        self.assertEqual(promoted["status"], "VERIFIED_FUTURE")
        self.assertEqual(
            self.conn.execute(
                "SELECT price_id,unit_price,source_file FROM prices WHERE price_state='current'"
            ).fetchall(),
            before,
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM v_verified_future_prices").fetchone()[0], 1)

    def test_stale_fingerprint_and_locked_mapping_drift_fail_before_price_dml(self):
        _vendor, offer_id = self.prepare_offer()
        staged = self.stage([base_row()])
        with self.assertRaisesRegex(PriceBookError, "fingerprint"):
            promote_price_book_batch(
                self.conn, self.storage, batch_id=str(staged["price_book_batch_id"]),
                expected_validation_fingerprint="f" * 64, actor="owner",
            )
        self.conn.execute("UPDATE supplier_offers SET confidence='REVIEW' WHERE offer_id=%s", (offer_id,))
        self.conn.commit()
        with self.assertRaisesRegex(PriceBookError, "revalidation"):
            promote_price_book_batch(
                self.conn, self.storage, batch_id=str(staged["price_book_batch_id"]),
                expected_validation_fingerprint=staged["validation_fingerprint"], actor="owner",
            )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM prices").fetchone()[0], 0)

    def test_promoted_replay_is_zero_dml(self):
        self.prepare_offer()
        staged = self.stage([base_row()])
        kwargs = {
            "batch_id": str(staged["price_book_batch_id"]),
            "expected_validation_fingerprint": staged["validation_fingerprint"],
            "actor": "owner",
        }
        promote_price_book_batch(self.conn, self.storage, **kwargs)
        self.conn.commit()
        before = self.conn.execute(
            """SELECT (SELECT count(*) FROM prices),
                      (SELECT count(*) FROM change_log),
                      (SELECT count(*) FROM price_book_staging_rows),
                      (SELECT promoted_at FROM price_book_batches LIMIT 1)"""
        ).fetchone()
        self.conn.commit()
        replay = promote_price_book_batch(self.conn, self.storage, **kwargs)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM prices),
                          (SELECT count(*) FROM change_log),
                          (SELECT count(*) FROM price_book_staging_rows),
                          (SELECT promoted_at FROM price_book_batches LIMIT 1)"""
            ).fetchone(),
            before,
        )

    def test_database_requires_same_transaction_promotion_authority_and_freezes_economics(self):
        _vendor, offer_id = self.prepare_offer()
        staged = self.stage([base_row()])
        batch_id = str(staged["price_book_batch_id"])
        with self.assertRaisesRegex(Exception, "promotion attribution|promotion does not own"):
            self.conn.execute(
                """UPDATE price_book_batches
                      SET status='VERIFIED_FUTURE',promoted_by='bypass',promoted_at=now()
                    WHERE price_book_batch_id=%s""",
                (batch_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "lacks promotion authority"):
            self.conn.execute(
                """INSERT INTO prices(
                       offer_id,price_state,effective_month,level_type,unit_price,
                       source_file,verified,source_price_book_batch_id,
                       source_price_book_row_number,effective_from,effective_through
                   ) VALUES (%s,'future',%s,'BASE',10,'bypass',TRUE,%s,2,%s,%s)""",
                (offer_id, NEXT_MONTH_START, batch_id, NEXT_MONTH_START, NEXT_MONTH_END),
            )
        self.conn.rollback()
        promote_price_book_batch(
            self.conn,
            self.storage,
            batch_id=batch_id,
            expected_validation_fingerprint=staged["validation_fingerprint"],
            actor="owner",
        )
        self.conn.commit()
        price_id = self.conn.execute(
            "SELECT price_id FROM prices WHERE source_price_book_batch_id=%s", (batch_id,)
        ).fetchone()[0]
        self.conn.commit()
        for statement in (
            "UPDATE prices SET unit_price=1 WHERE price_id=%s",
            "DELETE FROM prices WHERE price_id=%s",
        ):
            with self.subTest(statement=statement), self.assertRaisesRegex(Exception, "immutable"):
                self.conn.execute(statement, (price_id,))
            self.conn.rollback()
        with self.assertRaisesRegex(Exception, "not implemented|immutable"):
            self.conn.execute(
                "UPDATE prices SET price_state='current' WHERE price_id=%s", (price_id,)
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "supplier offer contract"):
            self.conn.execute(
                "UPDATE supplier_offers SET confidence='REVIEW' WHERE offer_id=%s",
                (offer_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "vendor identity"):
            self.conn.execute(
                """UPDATE vendors SET active=FALSE
                    WHERE vendor_id=(SELECT vendor_id FROM supplier_offers WHERE offer_id=%s)""",
                (offer_id,),
            )
        self.conn.rollback()
        event_id = self.conn.execute(
            "SELECT price_book_promotion_event_id FROM price_book_promotion_events WHERE price_book_batch_id=%s",
            (batch_id,),
        ).fetchone()[0]
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "append-only"):
            self.conn.execute(
                "DELETE FROM price_book_promotion_events WHERE price_book_promotion_event_id=%s",
                (event_id,),
            )
        self.conn.rollback()

    def test_direct_promotion_event_revalidates_live_complete_offer_scope(self):
        vendor_id, _offer_id = self.prepare_offer()
        staged = self.stage([base_row()])
        self.add_variant("1002")
        self.add_offer(variant_id="1002", vendor_id=vendor_id, sku="EMP-1002")
        self.conn.commit()
        semantic_md5 = self.conn.execute(
            "SELECT price_book_staging_semantic_md5(%s)",
            (staged["price_book_batch_id"],),
        ).fetchone()[0]
        promoted_sha = "0" * 64
        evidence = {
            "content_sha256": staged["content_sha256"],
            "validation_fingerprint": staged["validation_fingerprint"],
            "promoted_future_sha256": promoted_sha,
            "promoted_semantic_md5": semantic_md5,
        }
        with self.assertRaisesRegex(Exception, "locked validation authority"):
            self.conn.execute(
                """INSERT INTO price_book_promotion_events(
                       price_book_batch_id,prior_status,new_status,
                       validation_fingerprint,predecessor_future_sha256,
                       predecessor_batch_id,promoted_future_sha256,promoted_semantic_md5,
                       promoted_row_count,
                       acknowledged_warning_codes,review_reason,evidence_json,recorded_by
                   ) VALUES (%s,'VALIDATED','VERIFIED_FUTURE',%s,%s,NULL,%s,%s,1,
                             '[]'::jsonb,NULL,%s::jsonb,'bypass')""",
                (
                    staged["price_book_batch_id"],
                    staged["validation_fingerprint"],
                    staged["future_predecessor_sha256"],
                    promoted_sha,
                    semantic_md5,
                    json.dumps(evidence, sort_keys=True),
                ),
            )
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM price_book_promotion_events").fetchone()[0],
            0,
        )

    def test_packet4_creates_no_rollover_authority_or_current_transition(self):
        self.assertIsNone(
            self.conn.execute("SELECT to_regclass('price_book_rollover_events')").fetchone()[0]
        )
        self.assertNotIn(
            "ROLLED_TO_CURRENT",
            {
                row[0]
                for row in self.conn.execute(
                    "SELECT status FROM price_book_batches"
                ).fetchall()
            },
        )

    def test_exact_reviewed_seed_import_is_audited_and_idempotent_after_migration(self):
        seed_dir = DB_DIR.parent / "seed"
        first = import_seed(seed_dir, conn=self.conn)
        self.conn.commit()
        self.assertEqual(first["current_prices"], 271)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM prices").fetchone()[0], 271)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM legacy_price_seed_events").fetchone()[0],
            1,
        )
        second = import_seed(seed_dir, conn=self.conn)
        self.conn.commit()
        self.assertEqual(second["current_prices"], 271)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM prices").fetchone()[0], 271)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM legacy_price_seed_events").fetchone()[0],
            2,
        )
        tamper_cases = (
            ("current_prices.csv", b"177.55", b"177.56"),
            ("supplier_offers.csv", b",6.0,", b",7.0,"),
        )
        for filename, original, changed in tamper_cases:
            with self.subTest(filename=filename), TemporaryDirectory() as tampered_root:
                tampered = Path(tampered_root) / "seed"
                shutil.copytree(seed_dir, tampered)
                target = tampered / filename
                payload = target.read_bytes()
                self.assertIn(original, payload)
                target.write_bytes(payload.replace(original, changed, 1))
                with self.assertRaisesRegex(ValueError, "reviewed August authority"):
                    import_seed(tampered, conn=self.conn)
            self.conn.rollback()
            self.assertEqual(self.conn.execute("SELECT count(*) FROM prices").fetchone()[0], 271)
            self.assertEqual(
                self.conn.execute(
                    "SELECT count(*) FROM legacy_price_seed_events"
                ).fetchone()[0],
                2,
            )
        with self.assertRaisesRegex(Exception, "append-only"):
            self.conn.execute("DELETE FROM legacy_price_seed_events")
        self.conn.rollback()

    def test_mid_promotion_database_failure_rolls_back_every_effect(self):
        self.prepare_offer()
        staged = self.stage([
            base_row(),
            base_row(level_type="BREAK", break_quantity="12", break_unit="CS",
                     case_price="108", unit_price="9"),
        ])
        self.conn.execute(
            """CREATE FUNCTION fail_second_price() RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN
                 IF (SELECT count(*) FROM prices WHERE source_price_book_batch_id=NEW.source_price_book_batch_id) >= 1
                 THEN RAISE EXCEPTION 'synthetic second price failure'; END IF;
                 RETURN NEW;
               END $$"""
        )
        self.conn.execute(
            """CREATE TRIGGER trg_fail_second_price BEFORE INSERT ON prices
               FOR EACH ROW EXECUTE FUNCTION fail_second_price()"""
        )
        self.conn.commit()
        before = self.conn.execute(
            """SELECT status,promoted_at FROM price_book_batches
                WHERE price_book_batch_id=%s""",
            (staged["price_book_batch_id"],),
        ).fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "synthetic second price"):
            promote_price_book_batch(
                self.conn, self.storage, batch_id=str(staged["price_book_batch_id"]),
                expected_validation_fingerprint=staged["validation_fingerprint"], actor="owner",
            )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM prices").fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute(
                "SELECT status,promoted_at FROM price_book_batches WHERE price_book_batch_id=%s",
                (staged["price_book_batch_id"],),
            ).fetchone(),
            before,
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM price_book_staging_rows").fetchone()[0], 2)

    def test_replacement_failure_restores_verified_predecessor_and_candidate(self):
        _vendor_id, _offer_id = self.prepare_offer()
        first = self.stage([base_row(batch_ref="rollback-first")])
        promote_price_book_batch(
            self.conn,
            self.storage,
            batch_id=str(first["price_book_batch_id"]),
            expected_validation_fingerprint=first["validation_fingerprint"],
            actor="owner",
        )
        self.conn.commit()
        second = self.stage(
            [base_row(batch_ref="rollback-second", case_price="108", unit_price="9")]
        )
        self.conn.execute(
            """CREATE FUNCTION fail_replacement_price() RETURNS trigger LANGUAGE plpgsql AS $$
               BEGIN RAISE EXCEPTION 'synthetic replacement price failure'; END $$"""
        )
        self.conn.execute(
            """CREATE TRIGGER trg_fail_replacement_price BEFORE INSERT ON prices
               FOR EACH ROW EXECUTE FUNCTION fail_replacement_price()"""
        )
        self.conn.commit()

        def durable_state():
            return (
                self.conn.execute(
                    """SELECT price_book_batch_id,status,promoted_by,promoted_at,
                              disposition_by,disposition_reason,disposition_at
                         FROM price_book_batches ORDER BY batch_generation"""
                ).fetchall(),
                self.conn.execute(
                    """SELECT source_price_book_batch_id,offer_id,price_state,unit_price
                         FROM prices ORDER BY price_id"""
                ).fetchall(),
                self.conn.execute(
                    """SELECT price_book_batch_id,validation_fingerprint,promoted_future_sha256,
                              promoted_semantic_md5,recorded_by,recorded_at
                         FROM price_book_promotion_events ORDER BY price_book_promotion_event_id"""
                ).fetchall(),
                self.conn.execute(
                    """SELECT price_book_batch_id,prior_status,new_status,reason,recorded_at
                         FROM price_book_disposition_events ORDER BY price_book_disposition_event_id"""
                ).fetchall(),
                self.conn.execute(
                    """SELECT price_book_batch_id,source_row_number,validation_status
                         FROM price_book_staging_rows ORDER BY price_book_batch_id,source_row_number"""
                ).fetchall(),
            )

        before = durable_state()
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "synthetic replacement price failure"):
            promote_price_book_batch(
                self.conn,
                self.storage,
                batch_id=str(second["price_book_batch_id"]),
                expected_validation_fingerprint=second["validation_fingerprint"],
                actor="owner",
            )
        self.assertEqual(durable_state(), before)

    def test_raw_evidence_corruption_fails_closed_without_repair(self):
        self.prepare_offer()
        staged = self.stage([base_row()])
        path = Path(self.temp.name) / staged["raw_storage_key"]
        path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(PriceBookError, "hash does not match"):
            read_raw_price_book(
                self.conn, self.storage, batch_id=str(staged["price_book_batch_id"])
            )
        self.assertEqual(path.read_bytes(), b"corrupt")

    def test_raw_corruption_blocks_promotion_before_any_database_change(self):
        self.prepare_offer()
        staged = self.stage([base_row()])
        batch_id = str(staged["price_book_batch_id"])
        path = Path(self.temp.name) / staged["raw_storage_key"]
        path.write_bytes(b"corrupt")
        before = self.conn.execute(
            """SELECT (SELECT status FROM price_book_batches WHERE price_book_batch_id=%s),
                      (SELECT count(*) FROM price_book_staging_rows WHERE price_book_batch_id=%s),
                      (SELECT count(*) FROM price_book_promotion_events),
                      (SELECT count(*) FROM prices)""",
            (batch_id, batch_id),
        ).fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(PriceBookError, "hash does not match"):
            promote_price_book_batch(
                self.conn, self.storage, batch_id=batch_id,
                expected_validation_fingerprint=staged["validation_fingerprint"],
                actor="owner",
            )
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT status FROM price_book_batches WHERE price_book_batch_id=%s),
                          (SELECT count(*) FROM price_book_staging_rows WHERE price_book_batch_id=%s),
                          (SELECT count(*) FROM price_book_promotion_events),
                          (SELECT count(*) FROM prices)""",
                (batch_id, batch_id),
            ).fetchone(),
            before,
        )

    def test_candidate_batches_never_change_operational_readiness_or_generic_exceptions(self):
        vendor_id, _offer = self.prepare_offer()
        for gate, status in (("MAPPING_INTEGRITY", "WARN"), ("PRICE_COVERAGE", "FAIL")):
            self.conn.execute(
                """INSERT INTO readiness_gates(
                       gate_name,scope_type,scope_id,status,severity,blocks_po,message,evidence_json
                   ) VALUES (%s,'VENDOR',%s,%s,'HIGH',TRUE,'preexisting','{"source":"fixture"}')
                   ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                     status=EXCLUDED.status,severity=EXCLUDED.severity,
                     blocks_po=EXCLUDED.blocks_po,message=EXCLUDED.message,
                     evidence_json=EXCLUDED.evidence_json""",
                (gate, vendor_id, status),
            )
        self.conn.commit()
        before = self.conn.execute(
            """SELECT gate_name,status,severity,blocks_po,message,evidence_json,checked_at
                 FROM readiness_gates WHERE scope_type='VENDOR' AND scope_id=%s
                   AND gate_name IN ('MAPPING_INTEGRITY','PRICE_COVERAGE')
                ORDER BY gate_name""",
            (vendor_id,),
        ).fetchall()
        valid = self.stage([base_row()])
        self.stage([base_row(batch_ref="invalid-candidate", unit_price="0")])
        promote_price_book_batch(
            self.conn, self.storage, batch_id=str(valid["price_book_batch_id"]),
            expected_validation_fingerprint=valid["validation_fingerprint"], actor="owner",
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT gate_name,status,severity,blocks_po,message,evidence_json,checked_at
                     FROM readiness_gates WHERE scope_type='VENDOR' AND scope_id=%s
                       AND gate_name IN ('MAPPING_INTEGRITY','PRICE_COVERAGE')
                    ORDER BY gate_name""",
                (vendor_id,),
            ).fetchall(),
            before,
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM exceptions").fetchone()[0], 0)

    def test_invalid_batch_can_be_audited_rejected_and_typed_rows_are_purged(self):
        self.prepare_offer()
        staged = self.stage([base_row(unit_price="0")])
        batch_id = str(staged["price_book_batch_id"])
        result = reject_price_book_batch(
            self.conn, self.storage, batch_id=batch_id,
            expected_validation_fingerprint=staged["validation_fingerprint"],
            actor="owner", reason="Invalid supplier row rejected in fixture",
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM price_book_staging_rows WHERE price_book_batch_id=%s",
                (batch_id,),
            ).fetchone()[0],
            0,
        )
        event_id = self.conn.execute(
            """SELECT price_book_disposition_event_id FROM price_book_disposition_events
                WHERE price_book_batch_id=%s""",
            (batch_id,),
        ).fetchone()[0]
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "append-only"):
            self.conn.execute(
                "DELETE FROM price_book_disposition_events WHERE price_book_disposition_event_id=%s",
                (event_id,),
            )
        self.conn.rollback()

    def test_warning_requires_explicit_reason_and_records_exact_acknowledgement(self):
        _vendor, offer_id = self.prepare_offer()
        self.insert_grandfathered_price(offer_id)
        self.conn.commit()
        staged = self.stage([base_row()])
        self.assertGreater(staged["warning_count"], 0)
        kwargs = {
            "batch_id": str(staged["price_book_batch_id"]),
            "expected_validation_fingerprint": staged["validation_fingerprint"],
            "actor": "owner",
        }
        with self.assertRaisesRegex(PriceBookError, "explicit review reason"):
            promote_price_book_batch(self.conn, self.storage, **kwargs)
        promote_price_book_batch(
            self.conn, self.storage, warning_review_reason="Reviewed structural and cost changes",
            **kwargs,
        )
        codes, reason = self.conn.execute(
            """SELECT acknowledged_warning_codes,review_reason
                 FROM price_book_promotion_events WHERE price_book_batch_id=%s""",
            (staged["price_book_batch_id"],),
        ).fetchone()
        self.assertEqual(codes, staged["validation_evidence"]["warning_codes"])
        self.assertEqual(reason, "Reviewed structural and cost changes")

    def test_newest_batch_wins_and_replaces_complete_vendor_future_set(self):
        vendor_id, _offer = self.prepare_offer()
        older = self.stage([base_row(batch_ref="candidate-a")])
        newer = self.stage([
            base_row(batch_ref="candidate-b", case_price="108", unit_price="9")
        ])
        with self.assertRaisesRegex(PriceBookError, "newer validated FUTURE"):
            promote_price_book_batch(
                self.conn, self.storage, batch_id=str(older["price_book_batch_id"]),
                expected_validation_fingerprint=older["validation_fingerprint"], actor="owner",
            )
        promote_price_book_batch(
            self.conn, self.storage, batch_id=str(newer["price_book_batch_id"]),
            expected_validation_fingerprint=newer["validation_fingerprint"], actor="owner",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM price_book_batches WHERE price_book_batch_id=%s",
                (older["price_book_batch_id"],),
            ).fetchone()[0],
            "SUPERSEDED",
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT p.offer_id,p.unit_price FROM prices p
                    JOIN supplier_offers o USING(offer_id)
                   WHERE o.vendor_id=%s AND p.price_state='future'""",
                (vendor_id,),
            ).fetchall(),
            [(_offer, Decimal("9.0000"))],
        )

    def test_verified_future_replacement_is_exact_and_supersedes_predecessor(self):
        vendor_id, offer_id = self.prepare_offer()
        first = self.stage([base_row(batch_ref="first-future")])
        promote_price_book_batch(
            self.conn, self.storage, batch_id=str(first["price_book_batch_id"]),
            expected_validation_fingerprint=first["validation_fingerprint"], actor="owner",
        )
        self.conn.commit()
        second = self.stage([
            base_row(batch_ref="corrected-future", case_price="108", unit_price="9")
        ])
        self.assertEqual(
            str(second["future_predecessor_batch_id"]),
            str(first["price_book_batch_id"]),
        )
        promote_price_book_batch(
            self.conn, self.storage, batch_id=str(second["price_book_batch_id"]),
            expected_validation_fingerprint=second["validation_fingerprint"], actor="owner",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM price_book_batches WHERE price_book_batch_id=%s",
                (first["price_book_batch_id"],),
            ).fetchone()[0],
            "SUPERSEDED",
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT source_price_book_batch_id,offer_id,unit_price
                     FROM prices p JOIN supplier_offers o USING(offer_id)
                    WHERE o.vendor_id=%s AND p.price_state='future'""",
                (vendor_id,),
            ).fetchall(),
            [(second["price_book_batch_id"], offer_id, Decimal("9.0000"))],
        )

    def test_later_uploaded_wrong_effective_month_is_visible_and_cannot_replace_future(self):
        self.prepare_offer()
        next_month = self.stage([base_row(batch_ref="next-month-future")])
        promote_price_book_batch(
            self.conn, self.storage, batch_id=str(next_month["price_book_batch_id"]),
            expected_validation_fingerprint=next_month["validation_fingerprint"], actor="owner",
        )
        self.conn.commit()
        older = self.stage([
            base_row(
                batch_ref="late-uploaded-wrong-month",
                effective_from=CURRENT_MONTH_START.isoformat(),
                effective_through=(NEXT_MONTH_START - timedelta(days=1)).isoformat(),
            )
        ])
        self.assertEqual(
            get_price_book_batch(self.conn, str(older["price_book_batch_id"]))[
                "operational_status"
            ],
            "TEMPORAL_BLOCKED",
        )
        before = self.conn.execute(
            """SELECT (SELECT count(*) FROM prices),
                      (SELECT count(*) FROM price_book_promotion_events),
                      (SELECT status FROM price_book_batches WHERE price_book_batch_id=%s),
                      (SELECT status FROM price_book_batches WHERE price_book_batch_id=%s)""",
            (next_month["price_book_batch_id"], older["price_book_batch_id"]),
        ).fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(PriceBookError, "next Buffalo business month"):
            promote_price_book_batch(
                self.conn, self.storage, batch_id=str(older["price_book_batch_id"]),
                expected_validation_fingerprint=older["validation_fingerprint"], actor="owner",
            )
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM prices),
                          (SELECT count(*) FROM price_book_promotion_events),
                          (SELECT status FROM price_book_batches WHERE price_book_batch_id=%s),
                          (SELECT status FROM price_book_batches WHERE price_book_batch_id=%s)""",
                (next_month["price_book_batch_id"], older["price_book_batch_id"]),
            ).fetchone(),
            before,
        )

    def test_list_and_detail_are_select_only(self):
        self.prepare_offer()
        staged = self.stage([base_row()])
        self.conn.commit()
        with self.conn.transaction():
            self.conn.execute("SET TRANSACTION READ ONLY")
            self.assertEqual(len(list_price_book_batches(self.conn)), 1)
            detail = get_price_book_batch(self.conn, str(staged["price_book_batch_id"]))
            self.assertEqual(detail["status"], "VALIDATED")
            self.assertIsNone(self.conn.execute("SELECT txid_current_if_assigned()").fetchone()[0])

    def test_migration_reapplication_preserves_rows_and_exact_objects(self):
        self.prepare_offer()
        staged = self.stage([base_row()])
        before = self.conn.execute(
            "SELECT count(*),min(status),max(validation_fingerprint) FROM price_book_batches"
        ).fetchone()
        self.conn.execute((DB_DIR / "011_monday_price_book_staging.sql").read_text())
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*),min(status),max(validation_fingerprint) FROM price_book_batches"
            ).fetchone(),
            before,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM pg_trigger
                    WHERE tgname='trg_guard_price_book_batch_update' AND NOT tgisinternal"""
            ).fetchone()[0],
            1,
        )
        self.assertEqual(staged["status"], "VALIDATED")

    def test_migration_duplicate_ladder_failure_rolls_back_packet4_objects(self):
        # Isolated schema built only through Packet 3, then seeded with a natural duplicate.
        from psycopg import sql

        other = f"price_book_migration_failure_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(other)))
        self.conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(other)))
        for migration in MIGRATIONS[:-1]:
            self.conn.execute((DB_DIR / migration).read_text())
        self.conn.execute(
            """INSERT INTO variants(variant_id,product_id,product_title,variant_title,active,catalog_state,identity_scope)
               VALUES ('failure-v','failure-p','Failure','750ML',TRUE,'LIVE','CURRENT')"""
        )
        vendor = self.conn.execute(
            "INSERT INTO vendors(vendor_name) VALUES ('Failure Vendor') RETURNING vendor_id"
        ).fetchone()[0]
        offer = self.conn.execute(
            """INSERT INTO supplier_offers(variant_id,vendor_id,supplier_sku,active)
               VALUES ('failure-v',%s,'FAIL-SKU',TRUE) RETURNING offer_id""",
            (vendor,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO prices(offer_id,price_state,effective_month,level_type,break_qty,unit_price,source_file)
               VALUES (%s,'current','2026-09-01','BASE',1,10,'a'),
                      (%s,'current','2026-09-01','BASE',1,11,'b')""",
            (offer, offer),
        )
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "duplicate operational structural"):
            with self.conn.transaction():
                self.conn.execute((DB_DIR / "011_monday_price_book_staging.sql").read_text())
        self.assertIsNone(
            self.conn.execute("SELECT to_regclass('price_book_batches')").fetchone()[0]
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name='prices'
                      AND column_name='source_price_book_batch_id'"""
            ).fetchone()[0],
            0,
        )
        self.conn.rollback()
        self.conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(other)))
        self.conn.commit()

    def test_migration_rejects_unprovenanced_pre011_future_atomically(self):
        from psycopg import sql

        other = f"price_book_legacy_future_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(other)))
        self.conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(other)))
        for migration in MIGRATIONS[:-1]:
            self.conn.execute((DB_DIR / migration).read_text())
        self.conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,
                   active,catalog_state,identity_scope
               ) VALUES ('legacy-future-v','legacy-future-p','Legacy Future','750ML',
                         TRUE,'LIVE','CURRENT')"""
        )
        vendor = self.conn.execute(
            "INSERT INTO vendors(vendor_name) VALUES ('Legacy Future Vendor') RETURNING vendor_id"
        ).fetchone()[0]
        offer = self.conn.execute(
            """INSERT INTO supplier_offers(variant_id,vendor_id,supplier_sku,active)
               VALUES ('legacy-future-v',%s,'LEGACY-FUTURE-SKU',TRUE) RETURNING offer_id""",
            (vendor,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO prices(
                   offer_id,price_state,effective_month,level_type,break_qty,
                   unit_price,source_file,verified
               ) VALUES (%s,'future',%s,'BASE',1,10,'legacy-future.csv',TRUE)""",
            (offer, NEXT_MONTH_START),
        )
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "unprovenanced pre-011 FUTURE"):
            with self.conn.transaction():
                self.conn.execute((DB_DIR / "011_monday_price_book_staging.sql").read_text())
        self.assertIsNone(
            self.conn.execute("SELECT to_regclass('price_book_batches')").fetchone()[0]
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name='prices'
                      AND column_name='source_price_book_batch_id'"""
            ).fetchone()[0],
            0,
        )
        self.conn.rollback()
        self.conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(other)))
        self.conn.commit()

    def test_http_routes_are_bounded_and_auth_fails_before_io(self):
        route_methods = {
            route.path: sorted(route.methods)
            for route in api.app.routes
            if route.path.startswith("/price-books")
        }
        self.assertEqual(
            route_methods,
            {
                "/price-books": ["GET"],
                "/price-books/template.csv": ["GET"],
                "/price-books/{batch_id}": ["GET"],
                "/price-books/{batch_id}/raw.csv": ["GET"],
                "/price-books/import": ["POST"],
                "/price-books/{batch_id}/promote": ["POST"],
                "/price-books/{batch_id}/reject": ["POST"],
            },
        )
        client = TestClient(api.app)
        with (
            patch.object(api, "_db_conn", side_effect=AssertionError("database reached")),
            patch.object(api, "get_storage", side_effect=AssertionError("storage reached")),
            patch.dict("os.environ", {}, clear=True),
        ):
            response = client.post(
                "/price-books/import",
                files={"price_book_file": ("fixture.csv", csv_bytes([base_row()]), "text/csv")},
                data={"actor": "owner", "review_token": "missing"},
            )
        self.assertEqual(response.status_code, 503)

    def test_http_gets_are_read_only_escaped_and_proxy_safe(self):
        vendor_id = self.add_vendor(name="Empire <Fixture>")
        self.add_variant()
        self.add_offer(vendor_id=vendor_id)
        self.conn.commit()
        staged = self.stage([
            base_row(vendor_name="Empire <Fixture>", batch_ref="batch <one>")
        ])
        before = self.conn.execute(
            """SELECT (SELECT count(*) FROM price_book_batches),
                      (SELECT count(*) FROM price_book_staging_rows),
                      (SELECT count(*) FROM price_book_validation_issues),
                      (SELECT count(*) FROM prices),
                      (SELECT count(*) FROM change_log)"""
        ).fetchone()
        self.conn.commit()
        client = TestClient(api.app)
        with (
            patch.object(api, "_db_conn", self.api_connection),
            patch.object(api, "get_storage", return_value=self.storage),
        ):
            listing = client.get("/price-books")
            detail = client.get(f"/price-books/{staged['price_book_batch_id']}")
            raw = client.get(f"/price-books/{staged['price_book_batch_id']}/raw.csv")
            template = client.get("/price-books/template.csv")
        self.assertEqual((listing.status_code, detail.status_code, raw.status_code), (200, 200, 200))
        self.assertNotIn("batch <one>", listing.text)
        self.assertIn("batch &lt;one&gt;", listing.text)
        self.assertIn("action='price-books/import'", listing.text)
        self.assertIn("action='../price-books/", detail.text)
        self.assertEqual(
            urljoin("https://fixture/procurement/price-books", "price-books/import"),
            "https://fixture/procurement/price-books/import",
        )
        self.assertEqual(template.content, normalized_price_book_template())
        self.assertEqual(raw.content, csv_bytes([base_row(vendor_name="Empire <Fixture>", batch_ref="batch <one>")]))
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM price_book_batches),
                          (SELECT count(*) FROM price_book_staging_rows),
                          (SELECT count(*) FROM price_book_validation_issues),
                          (SELECT count(*) FROM prices),
                          (SELECT count(*) FROM change_log)"""
            ).fetchone(),
            before,
        )

    def test_http_import_and_promote_are_explicit_authenticated_prg(self):
        self.prepare_offer()
        client = TestClient(api.app)
        payload = csv_bytes([base_row()])
        with (
            patch.object(api, "_db_conn", self.api_connection),
            patch.object(api, "get_storage", return_value=self.storage),
            patch.dict("os.environ", {"PRICE_BOOK_REVIEW_TOKEN": "fixture-token"}, clear=False),
        ):
            imported = client.post(
                "/price-books/import",
                files={"price_book_file": ("ignored-name.csv", payload, "text/csv")},
                data={"actor": "fixture-owner", "review_token": "fixture-token"},
                follow_redirects=False,
            )
        self.assertEqual(imported.status_code, 303)
        batch = list_price_book_batches(self.conn)[0]
        self.conn.commit()
        batch_id = str(batch["price_book_batch_id"])
        self.assertEqual(
            urljoin(
                "https://fixture/procurement/price-books/import",
                imported.headers["location"],
            ),
            f"https://fixture/procurement/price-books/{batch_id}",
        )
        with (
            patch.object(api, "_db_conn", self.api_connection),
            patch.object(api, "get_storage", return_value=self.storage),
            patch.dict("os.environ", {"PRICE_BOOK_REVIEW_TOKEN": "fixture-token"}, clear=False),
        ):
            promoted = client.post(
                f"/price-books/{batch_id}/promote",
                data={
                    "actor": "fixture-owner",
                    "review_token": "fixture-token",
                    "expected_validation_fingerprint": batch["validation_fingerprint"],
                },
                follow_redirects=False,
            )
        self.assertEqual(promoted.status_code, 303)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM v_verified_future_prices").fetchone()[0], 1)

    def test_http_oversized_upload_stops_before_storage_or_database(self):
        client = TestClient(api.app)
        with (
            patch.object(api, "_db_conn", side_effect=AssertionError("database reached")),
            patch.object(api, "get_storage", side_effect=AssertionError("storage reached")),
            patch.dict("os.environ", {"PRICE_BOOK_REVIEW_TOKEN": "fixture-token"}, clear=False),
        ):
            response = client.post(
                "/price-books/import",
                files={"price_book_file": ("too-large.csv", b"x" * (price_book.MAX_PRICE_BOOK_BYTES + 1), "text/csv")},
                data={"actor": "owner", "review_token": "fixture-token"},
            )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
