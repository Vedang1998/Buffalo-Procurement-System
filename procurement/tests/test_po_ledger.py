"""Packet 3 Procurement run, PO ledger, and reconciliation tests."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import inspect
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from postgres_test_support import validated_test_connection
from procurement_os import po_ledger
from procurement_os.po_ledger import (
    ProcurementLedgerError,
    create_procurement_run,
    ensure_draft_po,
    evaluate_open_po_reconciliation,
    finalize_reviewed_po,
    open_po_position,
    recompute_open_po_reconciliation_gate,
    record_po_import_status,
    record_po_reconciliation,
)


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
)


class ProcurementLedgerPureTests(unittest.TestCase):
    def test_invalid_fingerprints_and_quantities_fail_before_sql(self):
        for value in ("", "A" * 64, "a" * 63, "not-a-hash"):
            with self.subTest(value=value), self.assertRaises(ProcurementLedgerError):
                create_procurement_run(
                    object(),
                    business_date=date(2026, 9, 7),
                    idempotency_key="monday",
                    input_fingerprint=value,
                )
        for value in (-1, "NaN", "1.00001", True):
            with self.subTest(value=value), self.assertRaises(ProcurementLedgerError):
                record_po_reconciliation(
                    object(),
                    po_line_id=1,
                    received_units=value,
                    cancelled_units=0,
                    reconciliation_status="OPEN",
                    evidence={"source": "fixture"},
                    actor="operator",
                )
        with self.assertRaisesRegex(ProcurementLedgerError, "actor"):
            record_po_import_status(
                object(),
                po_id="fixture",
                status="FAILED",
                reference=None,
                evidence={"source": "fixture"},
                actor=None,
            )
        with self.assertRaisesRegex(ProcurementLedgerError, "actor"):
            record_po_reconciliation(
                object(),
                po_line_id=1,
                received_units=0,
                cancelled_units=0,
                reconciliation_status="OPEN",
                evidence={"source": "fixture"},
                actor=None,
            )
        with self.assertRaisesRegex(ProcurementLedgerError, "actor"):
            finalize_reviewed_po(object(), po_id="fixture", actor=None)

    def test_service_has_no_shopify_or_release_action(self):
        source = inspect.getsource(po_ledger)
        self.assertNotIn("ShopifyGraphQLClient", source)
        self.assertNotIn("shopify_admin", source)
        self.assertIn("po_readiness(", source)
        self.assertIn('"release_performed": False', source)


class ProcurementLedgerPostgresTests(unittest.TestCase):
    BUSINESS_DATE = date(2026, 9, 7)
    EXPECTED_RECEIPT = datetime(2099, 9, 9, 16, tzinfo=timezone.utc)

    def setUp(self) -> None:
        from psycopg import sql

        self.conn, self.test_target, self.test_database_info = (
            validated_test_connection()
        )
        self.schema = f"po_ledger_mvp_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for migration in MIGRATIONS:
            self.conn.execute((DB_DIR / migration).read_text(encoding="utf-8"))
        self.conn.commit()

    def tearDown(self) -> None:
        from psycopg import sql

        try:
            self.conn.rollback()
            self.conn.execute("SET search_path TO public")
            self.conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )
            self.conn.commit()
        finally:
            self.conn.close()

    def add_vendor(self, name: str) -> str:
        return str(
            self.conn.execute(
                "INSERT INTO vendors(vendor_name) VALUES (%s) RETURNING vendor_id",
                (name,),
            ).fetchone()[0]
        )

    def add_current(self, variant_id: str) -> None:
        self.conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,
                   active,catalog_state,identity_scope
               ) VALUES (%s,%s,%s,'750ML',TRUE,'LIVE','CURRENT')""",
            (variant_id, f"product-{variant_id}", f"Product {variant_id}"),
        )

    def add_historical(self, variant_id: str) -> None:
        self.conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,active,
                   catalog_state,identity_scope,restoration_manifest_sha256,
                   restoration_manifest_row_number,restoration_evidence_version,
                   restoration_owner_authorization,restoration_authority_git_sha,
                   restoration_execution_git_sha
               ) VALUES (%s,NULL,%s,'750ML',FALSE,'RETIRED_CONFIRMED',
                         'HISTORICAL_ONLY',%s,1,'fixture-v1','fixture-owner',%s,%s)""",
            (variant_id, f"Historical {variant_id}", "a" * 64, "b" * 40, "c" * 40),
        )

    def add_offer(self, *, variant_id: str, vendor_id: str, sku: str) -> int:
        return int(
            self.conn.execute(
                """INSERT INTO supplier_offers(
                       variant_id,vendor_id,supplier_sku,shopify_units_per_case,
                       active,confidence
                   ) VALUES (%s,%s,%s,12,TRUE,'VERIFIED') RETURNING offer_id""",
                (variant_id, vendor_id, sku),
            ).fetchone()[0]
        )

    def add_run(
        self,
        *,
        key: str = "monday-2026-09-07",
        fingerprint: str = "a" * 64,
        source_data_through: datetime | None = None,
    ):
        self.conn.commit()
        result = create_procurement_run(
            self.conn,
            business_date=self.BUSINESS_DATE,
            idempotency_key=key,
            input_fingerprint=fingerprint,
            source_data_through=source_data_through,
        )
        self.conn.execute(
            "UPDATE runs SET workflow_stage='REVIEWED' WHERE run_id=%s",
            (result["run_id"],),
        )
        self.conn.commit()
        return result

    def add_draft(self, run_id: str, vendor_id: str, *, fingerprint: str = "a" * 64):
        return ensure_draft_po(
            self.conn,
            run_id=run_id,
            vendor_id=vendor_id,
            input_fingerprint=fingerprint,
            expected_receipt_at=self.EXPECTED_RECEIPT,
        )

    def pass_synthetic_finalization_readiness(self) -> None:
        self.conn.execute(
            """INSERT INTO catalog_sync_runs(
                   completed_at,status,shopify_api_version,
                   shopify_reported_variant_count,live_rows_received,
                   exact_current_ids,new_live_variants,source_hash,
                   pagination_complete
               ) VALUES (now(),'COMPLETED','synthetic-disposable-test',1,1,1,0,%s,TRUE)""",
            ("f" * 64,),
        )
        self.conn.execute(
            """UPDATE readiness_gates
                  SET status='PASS',message='Synthetic disposable fixture pass',
                      evidence_json='{"source":"synthetic-disposable-test-fixture"}'::jsonb,
                      checked_at=now()
                WHERE scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.commit()

    def add_line(
        self,
        *,
        po_id: str,
        variant_id: str,
        offer_id: int,
        ordered: str = "12",
        reconciliation_status: str = "UNKNOWN",
        line_status: str = "DRAFT",
        expected_receipt_at=None,
    ) -> int:
        run_id, vendor_id = self.conn.execute(
            "SELECT run_id,vendor_id FROM purchase_orders WHERE po_id=%s", (po_id,)
        ).fetchone()
        supplier_sku = self.conn.execute(
            "SELECT supplier_sku FROM supplier_offers WHERE offer_id=%s", (offer_id,)
        ).fetchone()[0]
        recommendation_id = self.conn.execute(
            """INSERT INTO procurement_recommendations(
                   run_id,variant_id,vendor_id,offer_id,baseline_units,
                   recommended_cases,recommended_loose_units,review_required
               ) VALUES (%s,%s,%s,%s,%s,1,0,TRUE)
               RETURNING recommendation_id""",
            (run_id, variant_id, vendor_id, offer_id, ordered),
        ).fetchone()[0]
        decision_id = self.conn.execute(
            """INSERT INTO review_decisions(
                   run_id,recommendation_id,decision_type,scope,action,decided_by
               ) VALUES (%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY','ACCEPT','fixture-owner')
               RETURNING decision_id""",
            (run_id, recommendation_id),
        ).fetchone()[0]
        return int(
            self.conn.execute(
                """INSERT INTO purchase_order_lines(
                       po_id,variant_id,offer_id,recommendation_id,review_decision_id,
                       supplier_sku,cases,loose_units,
                       ordered_units,unit_cost,line_total,line_status,
                       reconciliation_status,expected_receipt_at,input_fingerprint
                   ) VALUES (%s,%s,%s,%s,%s,%s,1,0,%s,10,120,%s,%s,%s,%s)
                   RETURNING po_line_id""",
                (
                    po_id,
                    variant_id,
                    offer_id,
                    recommendation_id,
                    decision_id,
                    supplier_sku,
                    ordered,
                    line_status,
                    reconciliation_status,
                    expected_receipt_at,
                    "c" * 64,
                ),
            ).fetchone()[0]
        )

    def make_fixture_line(self, *, second_vendor: bool = False):
        self.add_current("1001")
        vendor_id = self.add_vendor("Primary Distributor")
        offer_id = self.add_offer(variant_id="1001", vendor_id=vendor_id, sku="SKU-1001")
        if second_vendor:
            self.add_vendor("Second Distributor")
        self.conn.commit()
        run = self.add_run()
        draft = self.add_draft(run["run_id"], vendor_id)
        line_id = self.add_line(
            po_id=draft["po_id"], variant_id="1001", offer_id=offer_id
        )
        self.conn.commit()
        return run, draft, vendor_id, offer_id, line_id

    def make_final_open(
        self,
        *,
        trustworthy: bool,
        imported: bool | None = None,
        expected_receipt_at: datetime | None = None,
    ):
        expected_receipt_at = expected_receipt_at or self.EXPECTED_RECEIPT
        should_import = trustworthy if imported is None else imported
        run, draft, vendor_id, offer_id, line_id = self.make_fixture_line()
        self.conn.execute(
            "UPDATE purchase_order_lines SET expected_receipt_at=%s WHERE po_line_id=%s",
            (expected_receipt_at, line_id),
        )
        self.conn.execute(
            """UPDATE purchase_order_lines SET line_status='ORDERED',
                      reconciliation_status='UNKNOWN'
               WHERE po_line_id=%s""",
            (line_id,),
        )
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='REVIEW',
                      reviewed_by='fixture-owner',reviewed_at=%s,
                      merchandise_total=120,delivery_fee=0,po_total=120,
                      expected_receipt_at=%s
               WHERE po_id=%s""",
            (
                datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
                expected_receipt_at,
                draft["po_id"],
            ),
        )
        self.conn.commit()
        self.pass_synthetic_finalization_readiness()
        finalized = finalize_reviewed_po(
            self.conn,
            po_id=draft["po_id"],
            actor="fixture-owner",
            finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
        )
        self.assertFalse(finalized["release_performed"])
        self.conn.commit()
        if should_import:
            record_po_import_status(
                self.conn,
                po_id=draft["po_id"],
                status="IMPORTED",
                reference="synthetic-fixture-import",
                evidence={"source": "synthetic-disposable-test-fixture"},
                actor="fixture-owner",
            )
        if trustworthy:
            record_po_reconciliation(
                self.conn,
                po_line_id=line_id,
                received_units=0,
                cancelled_units=0,
                reconciliation_status="OPEN",
                evidence={"source": "direct-order-confirmation", "reference": "fixture"},
                actor="fixture-owner",
            )
        return run, draft, vendor_id, offer_id, line_id

    def test_run_and_vendor_draft_are_idempotent_but_frozen_inputs_cannot_change(self):
        vendor_id = self.add_vendor("Idempotent Distributor")
        self.conn.commit()
        run = self.add_run()
        replay = self.add_run()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(run["run_id"], replay["run_id"])
        with self.assertRaisesRegex(ProcurementLedgerError, "different frozen inputs"):
            self.add_run(fingerprint="d" * 64)
        with self.assertRaisesRegex(ProcurementLedgerError, "different frozen inputs"):
            self.add_run(
                source_data_through=datetime(2026, 9, 6, 23, tzinfo=timezone.utc)
            )
        draft = self.add_draft(run["run_id"], vendor_id)
        draft_replay = self.add_draft(run["run_id"], vendor_id)
        self.assertTrue(draft_replay["idempotent_replay"])
        self.assertEqual(draft["po_id"], draft_replay["po_id"])
        with self.assertRaisesRegex(ProcurementLedgerError, "match the frozen"):
            self.add_draft(run["run_id"], vendor_id, fingerprint="e" * 64)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_orders").fetchone()[0], 1
        )

    def test_exactly_one_draft_po_per_vendor_per_run(self):
        first = self.add_vendor("First Distributor")
        second = self.add_vendor("Second Distributor")
        self.conn.commit()
        run = self.add_run()
        one = self.add_draft(run["run_id"], first)
        two = self.add_draft(run["run_id"], second)
        self.assertNotEqual(one["po_id"], two["po_id"])
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_orders").fetchone()[0], 2
        )
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO purchase_orders(run_id,vendor_id,po_status)
                   VALUES (%s,%s,'DRAFT')""",
                (run["run_id"], first),
            )
        self.conn.rollback()
        legacy_run = self.conn.execute(
            """INSERT INTO runs(run_type,status) VALUES ('LEGACY','COMPLETED')
               RETURNING run_id"""
        ).fetchone()[0]
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "identity is immutable"):
            self.conn.execute(
                "UPDATE purchase_orders SET run_id=%s WHERE po_id=%s",
                (legacy_run, one["po_id"]),
            )
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                "SELECT run_id FROM purchase_orders WHERE po_id=%s", (one["po_id"],)
            ).fetchone()[0],
            uuid.UUID(run["run_id"]),
        )

    def test_draft_requires_reviewed_active_run_and_matching_fingerprint(self):
        vendor_id = self.add_vendor("Review Boundary Distributor")
        self.conn.commit()
        run = create_procurement_run(
            self.conn,
            business_date=self.BUSINESS_DATE,
            idempotency_key="not-reviewed",
            input_fingerprint="a" * 64,
        )
        with self.assertRaisesRegex(ProcurementLedgerError, "completed human review"):
            ensure_draft_po(
                self.conn,
                run_id=run["run_id"],
                vendor_id=vendor_id,
                input_fingerprint="a" * 64,
            )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_orders").fetchone()[0], 0
        )
        self.conn.commit()
        with self.assertRaises(Exception):
            self.conn.execute(
                "INSERT INTO runs(run_type,status) VALUES ('MONDAY_PROCUREMENT','RUNNING')"
            )
        self.conn.rollback()

    def test_draft_never_counts_as_trusted_incoming(self):
        _run, _draft, vendor_id, _offer, _line = self.make_fixture_line()
        position = open_po_position(self.conn, variant_id="1001", vendor_id=vendor_id)
        self.assertEqual(position["trusted_incoming_units"], Decimal("0"))
        self.assertEqual(position["open_line_count"], 0)
        self.assertFalse(position["blocks_reorder"])
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM v_open_procurement_incoming").fetchone()[0],
            0,
        )

    def test_only_imported_reconciled_open_line_is_trusted_incoming(self):
        _run, _draft, vendor_id, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=2,
            cancelled_units=0,
            reconciliation_status="OPEN",
            evidence={"source": "direct-receipt", "reference": "fixture-2"},
            actor="fixture-owner",
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        position = open_po_position(
            self.conn,
            variant_id="1001",
            vendor_id=vendor_id,
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(position["trusted_incoming_units"], Decimal("10.0000"))
        self.assertFalse(position["blocks_reorder"])
        self.assertEqual(
            self.conn.execute(
                "SELECT trusted_incoming_units FROM v_open_procurement_incoming"
            ).fetchone()[0],
            Decimal("10.0000"),
        )

    def test_incoming_and_ambiguity_follow_variant_across_vendor_boundaries(self):
        _run, _draft, source_vendor, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        candidate_vendor = self.add_vendor("Alternate Distributor")
        self.conn.commit()
        position = open_po_position(
            self.conn,
            variant_id="1001",
            vendor_id=candidate_vendor,
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(position["trusted_incoming_units"], Decimal("12.0000"))
        self.assertEqual(position["trusted_sources"][0]["source_vendor_id"], source_vendor)
        self.conn.commit()
        record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=0,
            cancelled_units=0,
            reconciliation_status="AMBIGUOUS",
            evidence={"source": "direct-review", "note": "vendor response unclear"},
            actor="owner",
        )
        blocked = open_po_position(
            self.conn,
            variant_id="1001",
            vendor_id=candidate_vendor,
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(blocked["trusted_incoming_units"], Decimal("0"))
        self.assertTrue(blocked["blocks_reorder"])
        self.assertEqual(blocked["blockers"][0]["source_vendor_id"], source_vendor)

    def test_overdue_direct_evidence_never_counts_as_incoming(self):
        _run, _draft, vendor_id, _offer, line_id = self.make_final_open(
            trustworthy=True,
            expected_receipt_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        )
        overdue = open_po_position(
            self.conn,
            variant_id="1001",
            vendor_id=vendor_id,
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertTrue(overdue["blocks_reorder"])
        self.assertTrue(overdue["blockers"][0]["expected_receipt_overdue"])
        self.assertTrue(overdue["blockers"][0]["direct_evidence_present"])

    def test_as_of_evaluation_rejects_receipt_evidence_from_the_future(self):
        _run, _draft, vendor_id, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        self.conn.execute(
            """UPDATE purchase_order_lines
                  SET last_reconciled_at=%s,
                      reconciliation_evidence=%s::jsonb,
                      last_reconciled_by='future-fixture'
                WHERE po_line_id=%s""",
            (
                datetime(2099, 1, 1, tzinfo=timezone.utc),
                '{"source":"synthetic-future-evidence"}',
                line_id,
            ),
        )
        self.conn.commit()
        position = open_po_position(
            self.conn,
            variant_id="1001",
            vendor_id=vendor_id,
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(position["trusted_incoming_units"], Decimal("0"))
        self.assertTrue(position["blocks_reorder"])
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM v_open_procurement_incoming").fetchone()[0],
            0,
        )

    def test_ambiguous_or_untrusted_open_line_blocks_reorder_and_gate(self):
        _run, _draft, vendor_id, _offer, _line = self.make_final_open(
            trustworthy=False
        )
        position = open_po_position(self.conn, variant_id="1001", vendor_id=vendor_id)
        self.assertEqual(position["trusted_incoming_units"], Decimal("0"))
        self.assertTrue(position["blocks_reorder"])
        self.conn.commit()
        result = recompute_open_po_reconciliation_gate(
            self.conn, as_of=datetime(2026, 9, 7, tzinfo=timezone.utc)
        )
        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["evidence"]["blocking_lines"], 1)
        self.assertEqual(
            self.conn.execute(
                """SELECT status,blocks_po FROM readiness_gates
                   WHERE gate_name='OPEN_PO_RECONCILIATION'
                     AND scope_type='GLOBAL' AND scope_id=''"""
            ).fetchone(),
            ("WARN", False),
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT status,blocks_po FROM readiness_gates
                   WHERE gate_name='OPEN_PO_RECONCILIATION'
                     AND scope_type='VENDOR' AND scope_id=%s""",
                (vendor_id,),
            ).fetchone(),
            ("FAIL", True),
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT exception_type,severity,status FROM exceptions
                   WHERE po_line_id IS NOT NULL"""
            ).fetchone(),
            ("OPEN_PO_RECONCILIATION_REQUIRED", "HIGH", "OPEN"),
        )

    def test_ambiguous_receipt_creates_review_and_direct_evidence_resolves_it(self):
        _run, _draft, vendor_id, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        first = record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=0,
            cancelled_units=0,
            reconciliation_status="AMBIGUOUS",
            evidence={"source": "operator-review", "note": "backorder unclear"},
            actor="owner",
        )
        self.assertEqual(first["line_status"], "BACKORDER_REVIEW")
        self.assertEqual(first["readiness"]["status"], "WARN")
        self.assertEqual(
            self.conn.execute(
                """SELECT severity,status FROM exceptions
                    WHERE po_line_id=%s
                    ORDER BY exception_id DESC LIMIT 1""",
                (line_id,),
            ).fetchone(),
            ("HIGH", "OPEN"),
        )
        self.conn.commit()
        second = record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=3,
            cancelled_units=0,
            reconciliation_status="OPEN",
            evidence={"source": "direct-vendor-confirmation", "reference": "fixture-1"},
            actor="owner",
        )
        self.assertEqual(second["line_status"], "PARTIALLY_RECEIVED")
        self.assertEqual(second["open_units"], Decimal("9.0000"))
        self.assertEqual(second["readiness"]["status"], "PASS")
        self.assertEqual(
            self.conn.execute(
                """SELECT status FROM exceptions
                    WHERE po_line_id=%s
                    ORDER BY exception_id DESC LIMIT 1""",
                (line_id,),
            ).fetchone()[0],
            "RESOLVED",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM po_reconciliation_events WHERE po_line_id=%s",
                (line_id,),
            ).fetchone()[0],
            3,
        )
        self.assertEqual(
            open_po_position(self.conn, variant_id="1001", vendor_id=vendor_id)[
                "trusted_incoming_units"
            ],
            Decimal("9.0000"),
        )

    def test_scoped_fail_gates_clear_and_header_rolls_up_when_last_line_closes(self):
        _run, draft, vendor_id, _offer, line_id = self.make_final_open(
            trustworthy=False, imported=True
        )
        recompute_open_po_reconciliation_gate(
            self.conn, as_of=datetime(2026, 9, 7, tzinfo=timezone.utc)
        )
        opened = record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=0,
            cancelled_units=0,
            reconciliation_status="OPEN",
            evidence={"source": "direct-order-confirmation"},
            actor="owner",
        )
        self.assertEqual(opened["readiness"]["status"], "PASS")
        self.assertEqual(
            self.conn.execute(
                """SELECT scope_type,status,blocks_po FROM readiness_gates
                   WHERE gate_name='OPEN_PO_RECONCILIATION'
                     AND scope_type IN ('VENDOR','VARIANT')
                   ORDER BY scope_type"""
            ).fetchall(),
            [("VARIANT", "PASS", False), ("VENDOR", "PASS", False)],
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT receipt_status,reconciled_at FROM purchase_orders WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            "OPEN",
        )
        self.conn.commit()
        closed = record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=12,
            cancelled_units=0,
            reconciliation_status="RECONCILED",
            evidence={"source": "direct-receipt"},
            actor="owner",
        )
        self.assertEqual(closed["readiness"]["status"], "PASS")
        header = self.conn.execute(
            """SELECT receipt_status,reconciled_at,reconciliation_evidence
               FROM purchase_orders WHERE po_id=%s""",
            (draft["po_id"],),
        ).fetchone()
        self.assertEqual(header[0], "RECEIVED")
        self.assertIsNotNone(header[1])
        self.assertEqual(header[2]["received_units"], "12.0000")
        self.assertEqual(
            self.conn.execute(
                """SELECT scope_type,status,blocks_po FROM readiness_gates
                   WHERE gate_name='OPEN_PO_RECONCILIATION'
                     AND scope_type IN ('VENDOR','VARIANT')
                   ORDER BY scope_type"""
            ).fetchall(),
            [("VARIANT", "PASS", False), ("VENDOR", "PASS", False)],
        )

    def test_reconciliation_rejects_draft_and_zero_open_ambiguity(self):
        _run, draft, _vendor, _offer, line_id = self.make_fixture_line()
        with self.assertRaisesRegex(ProcurementLedgerError, "FINAL imported"):
            record_po_reconciliation(
                self.conn,
                po_line_id=line_id,
                received_units=0,
                cancelled_units=0,
                reconciliation_status="OPEN",
                evidence={"source": "fixture"},
                actor="owner",
            )
        self.conn.execute(
            "UPDATE purchase_order_lines SET line_status='ORDERED' WHERE po_line_id=%s",
            (line_id,),
        )
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='REVIEW',reviewed_by='owner',
                      reviewed_at=%s,merchandise_total=120,
                      delivery_fee=0,po_total=120 WHERE po_id=%s""",
            (datetime(2026, 9, 7, 12, tzinfo=timezone.utc), draft["po_id"]),
        )
        self.conn.commit()
        self.pass_synthetic_finalization_readiness()
        finalize_reviewed_po(
            self.conn,
            po_id=draft["po_id"],
            actor="owner",
            finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
        )
        self.conn.commit()
        record_po_import_status(
            self.conn,
            po_id=draft["po_id"],
            status="IMPORTED",
            reference="synthetic-fixture-import",
            evidence={"source": "synthetic-disposable-test-fixture"},
            actor="fixture-owner",
        )
        with self.assertRaisesRegex(ProcurementLedgerError, "remaining open"):
            record_po_reconciliation(
                self.conn,
                po_line_id=line_id,
                received_units=12,
                cancelled_units=0,
                reconciliation_status="AMBIGUOUS",
                evidence={"source": "fixture"},
                actor="owner",
            )

    def test_quantity_overstatement_fails_without_event_or_line_change(self):
        _run, _draft, _vendor, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        before = self.conn.execute(
            "SELECT received_units,cancelled_units,line_status FROM purchase_order_lines"
        ).fetchone()
        before_events = self.conn.execute(
            "SELECT count(*) FROM po_reconciliation_events"
        ).fetchone()[0]
        self.conn.commit()
        with self.assertRaisesRegex(ProcurementLedgerError, "cannot exceed"):
            record_po_reconciliation(
                self.conn,
                po_line_id=line_id,
                received_units=10,
                cancelled_units=3,
                reconciliation_status="OPEN",
                evidence={"source": "fixture"},
                actor="owner",
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT received_units,cancelled_units,line_status FROM purchase_order_lines"
            ).fetchone(),
            before,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM po_reconciliation_events").fetchone()[0],
            before_events,
        )

    def test_database_enforces_review_final_and_line_state_lifecycle(self):
        _run, draft, _vendor, _offer, line_id = self.make_fixture_line()
        with self.assertRaises(Exception):
            self.conn.execute(
                """UPDATE purchase_orders SET reviewed_at=%s,reviewed_by=NULL
                   WHERE po_id=%s""",
                (datetime(2026, 9, 7, tzinfo=timezone.utc), draft["po_id"]),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "must enter REVIEW"):
            self.conn.execute(
                """UPDATE purchase_orders SET po_status='FINAL',reviewed_at=%s,
                          reviewed_by='owner',finalized_at=%s WHERE po_id=%s""",
                (
                    datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
                    datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
                    draft["po_id"],
                ),
            )
        self.conn.rollback()
        with self.assertRaises(Exception):
            self.conn.execute(
                """UPDATE purchase_order_lines SET line_status='RECEIVED',
                          reconciliation_status='UNKNOWN' WHERE po_line_id=%s""",
                (line_id,),
            )
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                "SELECT po_status FROM purchase_orders WHERE po_id=%s", (draft["po_id"],)
            ).fetchone()[0],
            "DRAFT",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT line_status FROM purchase_order_lines WHERE po_line_id=%s",
                (line_id,),
            ).fetchone()[0],
            "DRAFT",
        )

    def test_review_and_cancelled_states_preserve_commercial_evidence(self):
        _run, draft, _vendor, offer_id, line_id = self.make_fixture_line()
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='REVIEW',reviewed_by='owner',
                      reviewed_at=%s,merchandise_total=120,
                      delivery_fee=0,po_total=120 WHERE po_id=%s""",
            (datetime(2026, 9, 7, 12, tzinfo=timezone.utc), draft["po_id"]),
        )
        self.conn.commit()
        for statement, arguments, message in (
            (
                "UPDATE purchase_order_lines SET unit_cost=11,line_total=132 WHERE po_line_id=%s",
                (line_id,),
                "commercial line facts are immutable",
            ),
            (
                "UPDATE purchase_orders SET merchandise_total=132,po_total=132 WHERE po_id=%s",
                (draft["po_id"],),
                "require renewed human review",
            ),
            (
                "DELETE FROM purchase_order_lines WHERE po_line_id=%s",
                (line_id,),
                "lines are immutable",
            ),
            (
                """UPDATE purchase_order_lines
                      SET reconciliation_status='OPEN',
                          reconciliation_evidence='{"source":"preseed"}'::jsonb,
                          last_reconciled_at=now(),last_reconciled_by='owner'
                    WHERE po_line_id=%s""",
                (line_id,),
                "receipt facts are immutable",
            ),
        ):
            with self.subTest(statement=statement), self.assertRaisesRegex(Exception, message):
                self.conn.execute(statement, arguments)
            self.conn.rollback()
        with self.assertRaisesRegex(Exception, "cannot accept new lines"):
            self.add_line(po_id=draft["po_id"], variant_id="1001", offer_id=offer_id)
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "new revision"):
            self.conn.execute(
                "UPDATE purchase_orders SET po_status='DRAFT' WHERE po_id=%s",
                (draft["po_id"],),
            )
        self.conn.rollback()
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='DRAFT',reviewed_by=NULL,
                      reviewed_at=NULL,po_revision=po_revision+1 WHERE po_id=%s""",
            (draft["po_id"],),
        )
        self.conn.execute(
            """UPDATE purchase_order_lines SET unit_cost=11,line_total=132
                 WHERE po_line_id=%s""",
            (line_id,),
        )
        self.conn.execute(
            """UPDATE purchase_orders SET merchandise_total=132,po_total=132
                 WHERE po_id=%s""",
            (draft["po_id"],),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                """SELECT po_status,po_revision,reviewed_by,merchandise_total
                     FROM purchase_orders WHERE po_id=%s""",
                (draft["po_id"],),
            ).fetchone(),
            ("DRAFT", 2, None, Decimal("132.00")),
        )
        self.conn.execute(
            "UPDATE purchase_orders SET po_status='CANCELLED' WHERE po_id=%s",
            (draft["po_id"],),
        )
        self.conn.commit()
        for statement, arguments, message in (
            (
                "UPDATE purchase_orders SET merchandise_total=999 WHERE po_id=%s",
                (draft["po_id"],),
                "CANCELLED Procurement PO facts are immutable",
            ),
            (
                "UPDATE purchase_order_lines SET unit_cost=99 WHERE po_line_id=%s",
                (line_id,),
                "commercial line facts are immutable",
            ),
            (
                "DELETE FROM purchase_order_lines WHERE po_line_id=%s",
                (line_id,),
                "lines are immutable",
            ),
        ):
            with self.subTest(cancelled_statement=statement), self.assertRaisesRegex(
                Exception, message
            ):
                self.conn.execute(statement, arguments)
            self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                "SELECT po_status FROM purchase_orders WHERE po_id=%s", (draft["po_id"],)
            ).fetchone()[0],
            "CANCELLED",
        )

    def test_reconciliation_events_are_append_only_with_exact_prior_values(self):
        _run, _draft, _vendor, offer_id, line_id = self.make_final_open(
            trustworthy=True
        )
        self.conn.execute(
            "UPDATE supplier_offers SET active=FALSE WHERE offer_id=%s", (offer_id,)
        )
        self.conn.commit()
        record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=2,
            cancelled_units=0,
            reconciliation_status="OPEN",
            evidence={"source": "direct-receipt"},
            actor="owner",
        )
        event = self.conn.execute(
            """SELECT prior_received_units,new_received_units,
                      prior_cancelled_units,new_cancelled_units,recorded_by
               FROM po_reconciliation_events ORDER BY po_reconciliation_event_id DESC LIMIT 1"""
        ).fetchone()
        self.assertEqual(
            event,
            (Decimal("0.0000"), Decimal("2.0000"), Decimal("0.0000"), Decimal("0.0000"), "owner"),
        )
        event_id = self.conn.execute(
            "SELECT max(po_reconciliation_event_id) FROM po_reconciliation_events"
        ).fetchone()[0]
        with self.assertRaisesRegex(Exception, "append-only"):
            self.conn.execute(
                "UPDATE po_reconciliation_events SET recorded_by='tampered' WHERE po_reconciliation_event_id=%s",
                (event_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "append-only"):
            self.conn.execute(
                "DELETE FROM po_reconciliation_events WHERE po_reconciliation_event_id=%s",
                (event_id,),
            )
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                "SELECT recorded_by FROM po_reconciliation_events WHERE po_reconciliation_event_id=%s",
                (event_id,),
            ).fetchone()[0],
            "owner",
        )

    def test_reconciliation_evidence_requires_attributed_json_objects(self):
        _run, draft, _vendor, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        before_line = self.conn.execute(
            """SELECT reconciliation_evidence,last_reconciled_at,last_reconciled_by
                 FROM purchase_order_lines WHERE po_line_id=%s""",
            (line_id,),
        ).fetchone()
        before_header = self.conn.execute(
            "SELECT reconciliation_evidence FROM purchase_orders WHERE po_id=%s",
            (draft["po_id"],),
        ).fetchone()[0]
        before_reconciliation_events = self.conn.execute(
            "SELECT count(*) FROM po_reconciliation_events"
        ).fetchone()[0]
        before_operational_events = self.conn.execute(
            "SELECT count(*) FROM po_operational_events"
        ).fetchone()[0]
        self.conn.commit()
        for payload in ("null", "[]", '"scalar"'):
            with self.subTest(payload=payload), self.assertRaises(Exception):
                self.conn.execute(
                    """UPDATE purchase_order_lines
                          SET reconciliation_evidence=%s::jsonb,
                              last_reconciled_at=%s,last_reconciled_by='owner'
                        WHERE po_line_id=%s""",
                    (
                        payload,
                        datetime(2026, 9, 7, 15, tzinfo=timezone.utc),
                        line_id,
                    ),
                )
            self.conn.rollback()
            with self.subTest(header_payload=payload), self.assertRaises(Exception):
                self.conn.execute(
                    "UPDATE purchase_orders SET reconciliation_evidence=%s::jsonb WHERE po_id=%s",
                    (payload, draft["po_id"]),
                )
            self.conn.rollback()
            with self.subTest(operational_payload=payload), self.assertRaises(Exception):
                self.conn.execute(
                    """INSERT INTO po_operational_events(
                           po_id,event_type,prior_status,new_status,evidence_json,recorded_by
                       ) VALUES (%s,'SHOPIFY_IMPORT_STATUS','IMPORTED','FAILED',%s::jsonb,'owner')""",
                    (draft["po_id"], payload),
                )
            self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                """SELECT reconciliation_evidence,last_reconciled_at,last_reconciled_by
                     FROM purchase_order_lines WHERE po_line_id=%s""",
                (line_id,),
            ).fetchone(),
            before_line,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT reconciliation_evidence FROM purchase_orders WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            before_header,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM po_reconciliation_events").fetchone()[0],
            before_reconciliation_events,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM po_operational_events").fetchone()[0],
            before_operational_events,
        )

    def test_referenced_offer_identity_and_recommendation_scope_are_immutable(self):
        run, draft, vendor_id, offer_id, line_id = self.make_fixture_line()
        other_vendor = self.add_vendor("Other Distributor")
        with self.assertRaisesRegex(Exception, "offer identity is immutable"):
            self.conn.execute(
                "UPDATE supplier_offers SET vendor_id=%s WHERE offer_id=%s",
                (other_vendor, offer_id),
            )
        self.conn.rollback()
        self.add_current("1002")
        other_offer = self.add_offer(
            variant_id="1002", vendor_id=vendor_id, sku="SKU-1002"
        )
        wrong_recommendation = self.conn.execute(
            """INSERT INTO procurement_recommendations(
                   run_id,variant_id,vendor_id,offer_id,baseline_units,
                   recommended_cases,recommended_loose_units,review_required
               ) VALUES (%s,'1002',%s,%s,1,1,0,TRUE)
               RETURNING recommendation_id""",
            (run["run_id"], vendor_id, other_offer),
        ).fetchone()[0]
        wrong_decision = self.conn.execute(
            """INSERT INTO review_decisions(
                   run_id,recommendation_id,decision_type,scope,action,decided_by
               ) VALUES (%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY','ACCEPT','owner')
               RETURNING decision_id""",
            (run["run_id"], wrong_recommendation),
        ).fetchone()[0]
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "recommendation relationship"):
            self.conn.execute(
                """UPDATE purchase_order_lines SET recommendation_id=%s,
                          review_decision_id=%s WHERE po_line_id=%s""",
                (wrong_recommendation, wrong_decision, line_id),
            )
        self.conn.rollback()
        recommendation_id, decision_id = self.conn.execute(
            """SELECT recommendation_id,review_decision_id
               FROM purchase_order_lines WHERE po_line_id=%s""",
            (line_id,),
        ).fetchone()
        for statement, arguments, message in (
            (
                "UPDATE runs SET input_fingerprint=%s WHERE run_id=%s",
                ("f" * 64, run["run_id"]),
                "run identity is immutable",
            ),
            (
                "UPDATE runs SET source_data_through=%s WHERE run_id=%s",
                (datetime(2026, 9, 6, 23, tzinfo=timezone.utc), run["run_id"]),
                "run identity is immutable",
            ),
            (
                "UPDATE procurement_recommendations SET variant_id='1002' WHERE recommendation_id=%s",
                (recommendation_id,),
                "recommendation identity is immutable",
            ),
            (
                "UPDATE procurement_recommendations SET baseline_units=99 WHERE recommendation_id=%s",
                (recommendation_id,),
                "recommendation identity is immutable",
            ),
            (
                "UPDATE review_decisions SET action='REJECT' WHERE decision_id=%s",
                (decision_id,),
                "review decision is immutable",
            ),
            (
                "UPDATE review_decisions SET comment='tamper' WHERE decision_id=%s",
                (decision_id,),
                "review decision is immutable",
            ),
        ):
            with self.subTest(statement=statement), self.assertRaisesRegex(Exception, message):
                self.conn.execute(statement, arguments)
            self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                "SELECT offer_id FROM purchase_order_lines WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            offer_id,
        )

    def test_final_po_and_commercial_lines_cannot_be_hidden_or_rewritten(self):
        run, draft, vendor_id, offer_id, line_id = self.make_final_open(
            trustworthy=True
        )
        other_vendor = self.add_vendor("Tamper Target Distributor")
        legacy_run = self.conn.execute(
            """INSERT INTO runs(run_type,status) VALUES ('LEGACY','COMPLETED')
               RETURNING run_id"""
        ).fetchone()[0]
        legacy_po = self.conn.execute(
            """INSERT INTO purchase_orders(run_id,vendor_id,po_status)
               VALUES (%s,%s,'DRAFT') RETURNING po_id""",
            (legacy_run, other_vendor),
        ).fetchone()[0]
        self.conn.commit()
        self.conn.execute(
            "SELECT set_config('procurement.po_reconciliation_write','v1',FALSE)"
        )
        attempts = (
            (
                "UPDATE purchase_orders SET po_status='CANCELLED' WHERE po_id=%s",
                (draft["po_id"],),
                "cannot change lifecycle state",
            ),
            (
                "UPDATE purchase_orders SET vendor_id=%s WHERE po_id=%s",
                (other_vendor, draft["po_id"]),
                "identity is immutable",
            ),
            (
                "UPDATE purchase_orders SET run_id=%s WHERE po_id=%s",
                (legacy_run, draft["po_id"]),
                "fingerprint must match|identity is immutable",
            ),
            (
                "UPDATE purchase_order_lines SET po_id=%s WHERE po_line_id=%s",
                (legacy_po, line_id),
                "commercial line facts are immutable",
            ),
            (
                "UPDATE purchase_order_lines SET po_line_id=%s WHERE po_line_id=%s",
                (line_id + 100000, line_id),
                "commercial line facts are immutable",
            ),
            (
                "UPDATE purchase_order_lines SET ordered_units=24 WHERE po_line_id=%s",
                (line_id,),
                "commercial line facts are immutable",
            ),
            (
                "UPDATE purchase_order_lines SET received_units=1,line_status='PARTIALLY_RECEIVED' WHERE po_line_id=%s",
                (line_id,),
                "requires attributed direct evidence",
            ),
            (
                "UPDATE purchase_orders SET receipt_status='RECEIVED' WHERE po_id=%s",
                (draft["po_id"],),
                "exact audited line rollup",
            ),
            (
                "UPDATE purchase_orders SET created_at=created_at + interval '1 second' WHERE po_id=%s",
                (draft["po_id"],),
                "identity and economics are immutable",
            ),
            (
                "DELETE FROM purchase_order_lines WHERE po_line_id=%s",
                (line_id,),
                "lines are immutable",
            ),
            (
                "DELETE FROM purchase_orders WHERE po_id=%s",
                (draft["po_id"],),
                "durable and cannot be deleted",
            ),
        )
        for statement, arguments, message in attempts:
            with self.subTest(statement=statement), self.assertRaisesRegex(Exception, message):
                self.conn.execute(statement, arguments)
            self.conn.rollback()
        self.conn.execute(
            """UPDATE purchase_order_lines
                  SET received_units=1,line_status='PARTIALLY_RECEIVED',
                      reconciliation_status='OPEN',
                      reconciliation_evidence=%s::jsonb,
                      last_reconciled_at=%s,last_reconciled_by='owner'
                WHERE po_line_id=%s""",
            (
                '{"source":"synthetic-direct-receipt"}',
                datetime(2026, 9, 7, 14, tzinfo=timezone.utc),
                line_id,
            ),
        )
        with self.assertRaisesRegex(Exception, "exact audited line rollup"):
            self.conn.execute(
                "UPDATE purchase_orders SET receipt_status='RECEIVED' WHERE po_id=%s",
                (draft["po_id"],),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "cannot accept new lines"):
            self.add_line(po_id=draft["po_id"], variant_id="1001", offer_id=offer_id)
        self.conn.rollback()
        self.assertEqual(
            open_po_position(
                self.conn,
                variant_id="1001",
                vendor_id=vendor_id,
                as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
            )["trusted_incoming_units"],
            Decimal("12.0000"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT run_id,po_status FROM purchase_orders WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone(),
            (uuid.UUID(run["run_id"]), "FINAL"),
        )

    def test_post_final_import_status_is_audited_idempotent_and_transactional(self):
        _run, draft, _vendor, _offer, _line = self.make_final_open(
            trustworthy=False
        )
        self.conn.execute(
            """CREATE OR REPLACE FUNCTION fail_import_fixture() RETURNS trigger
               LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic import failure'; END $$"""
        )
        self.conn.execute(
            """CREATE TRIGGER zz_fail_import_fixture
               BEFORE UPDATE OF shopify_import_status ON purchase_orders
               FOR EACH ROW EXECUTE FUNCTION fail_import_fixture()"""
        )
        before_events = self.conn.execute(
            "SELECT count(*) FROM po_operational_events WHERE po_id=%s",
            (draft["po_id"],),
        ).fetchone()[0]
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "synthetic import failure"):
            record_po_import_status(
                self.conn,
                po_id=draft["po_id"],
                status="EXPORTED",
                reference="synthetic-export.csv",
                evidence={"source": "synthetic-disposable-test-fixture"},
                actor="owner",
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM po_operational_events WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            before_events,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT shopify_import_status FROM purchase_orders WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            "NOT_IMPORTED",
        )
        self.conn.execute("DROP TRIGGER zz_fail_import_fixture ON purchase_orders")
        self.conn.execute("DROP FUNCTION fail_import_fixture()")
        self.conn.commit()

        exported = record_po_import_status(
            self.conn,
            po_id=draft["po_id"],
            status="EXPORTED",
            reference="synthetic-export.csv",
            evidence={"source": "synthetic-disposable-test-fixture"},
            actor="owner",
        )
        self.assertFalse(exported["idempotent_replay"])
        replay = record_po_import_status(
            self.conn,
            po_id=draft["po_id"],
            status="EXPORTED",
            reference="synthetic-export.csv",
            evidence={"source": "synthetic-disposable-test-fixture"},
            actor="owner",
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM po_operational_events WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            before_events + 1,
        )
        self.conn.commit()
        imported = record_po_import_status(
            self.conn,
            po_id=draft["po_id"],
            status="IMPORTED",
            reference="synthetic-shopify-reference",
            evidence={"source": "synthetic-disposable-test-fixture"},
            actor="owner",
        )
        self.assertEqual(imported["status"], "IMPORTED")
        event_id = self.conn.execute(
            "SELECT max(po_operational_event_id) FROM po_operational_events"
        ).fetchone()[0]
        with self.assertRaisesRegex(Exception, "append-only"):
            self.conn.execute(
                "UPDATE po_operational_events SET recorded_by='tamper' WHERE po_operational_event_id=%s",
                (event_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "audited ledger event"):
            self.conn.execute(
                "UPDATE purchase_orders SET shopify_po_reference='tamper' WHERE po_id=%s",
                (draft["po_id"],),
            )
        self.conn.rollback()

    def test_inactive_offer_or_fake_approval_cannot_become_orderable(self):
        run, draft, vendor_id, offer_id, line_id = self.make_fixture_line()
        self.conn.execute("UPDATE supplier_offers SET active=FALSE WHERE offer_id=%s", (offer_id,))
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "relationship is invalid"):
            self.conn.execute(
                "UPDATE purchase_order_lines SET line_status='ORDERED' WHERE po_line_id=%s",
                (line_id,),
            )
        self.conn.rollback()
        self.conn.execute("UPDATE supplier_offers SET active=TRUE WHERE offer_id=%s", (offer_id,))
        fake_recommendation = self.conn.execute(
            """INSERT INTO procurement_recommendations(
                   run_id,variant_id,vendor_id,offer_id,baseline_units,
                   recommended_cases,recommended_loose_units,review_required
               ) VALUES (%s,'1001',%s,%s,1,1,0,TRUE)
               RETURNING recommendation_id""",
            (run["run_id"], vendor_id, offer_id),
        ).fetchone()[0]
        fake_decision = self.conn.execute(
            """INSERT INTO review_decisions(
                   run_id,recommendation_id,decision_type,scope,action,decided_by
               ) VALUES (%s,%s,'UNRELATED','RUN_ONLY','ACCEPT','')
               RETURNING decision_id""",
            (run["run_id"], fake_recommendation),
        ).fetchone()[0]
        with self.assertRaisesRegex(Exception, "human approval"):
            self.conn.execute(
                """UPDATE purchase_order_lines SET recommendation_id=%s,
                          review_decision_id=%s WHERE po_line_id=%s""",
                (fake_recommendation, fake_decision, line_id),
            )
        self.conn.rollback()

    def test_finalization_requires_canonical_affected_scope_readiness(self):
        run, draft, vendor_id, _offer_id, line_id = self.make_fixture_line()
        self.conn.execute(
            "UPDATE purchase_order_lines SET line_status='ORDERED' WHERE po_line_id=%s",
            (line_id,),
        )
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='REVIEW',reviewed_by='owner',
                      reviewed_at=%s,merchandise_total=120,
                      delivery_fee=0,po_total=120 WHERE po_id=%s""",
            (datetime(2026, 9, 7, 12, tzinfo=timezone.utc), draft["po_id"]),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ProcurementLedgerError, "canonical PO readiness"):
            finalize_reviewed_po(
                self.conn,
                po_id=draft["po_id"],
                actor="owner",
                finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT po_status FROM purchase_orders WHERE po_id=%s", (draft["po_id"],)
            ).fetchone()[0],
            "REVIEW",
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM po_operational_events
                    WHERE po_id=%s AND event_type='FINALIZATION_AUTHORITY'""",
                (draft["po_id"],),
            ).fetchone()[0],
            0,
        )
        self.pass_synthetic_finalization_readiness()
        self.conn.execute(
            """INSERT INTO readiness_gates(
                   gate_name,scope_type,scope_id,status,severity,blocks_po,message
               ) VALUES ('VENDOR_RULES','VENDOR',%s,'FAIL','HIGH',TRUE,
                         'Synthetic affected-vendor blocker')
               ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
                   status='FAIL',blocks_po=TRUE,message=EXCLUDED.message""",
            (vendor_id,),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ProcurementLedgerError, "VENDOR_RULES"):
            finalize_reviewed_po(
                self.conn,
                po_id=draft["po_id"],
                actor="owner",
                finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
            )
        self.conn.execute(
            """UPDATE readiness_gates SET status='PASS',blocks_po=FALSE
                 WHERE gate_name='VENDOR_RULES'
                   AND scope_type='VENDOR' AND scope_id=%s""",
            (vendor_id,),
        )
        self.conn.commit()
        result = finalize_reviewed_po(
            self.conn,
            po_id=draft["po_id"],
            actor="owner",
            finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
        )
        self.assertEqual(result["po_status"], "FINAL")
        self.assertFalse(result["release_performed"])
        self.assertEqual(
            self.conn.execute(
                """SELECT event_type,evidence_json->>'source'
                     FROM po_operational_events
                    WHERE po_id=%s AND event_type='FINALIZATION_AUTHORITY'""",
                (draft["po_id"],),
            ).fetchone(),
            ("FINALIZATION_AUTHORITY", "CANONICAL_PO_READINESS"),
        )
        self.assertEqual(
            [item["variant_id"] for item in result["readiness_evaluations"]],
            ["1001"],
        )

    def test_finalization_recomputes_stale_open_po_state_before_authorizing(self):
        _first_run, _first_po, vendor_id, offer_id, _first_line = self.make_final_open(
            trustworthy=False, imported=True
        )
        self.conn.execute(
            """DELETE FROM readiness_gates
                WHERE gate_name='OPEN_PO_RECONCILIATION' AND scope_type <> 'GLOBAL'"""
        )
        self.conn.execute(
            """UPDATE readiness_gates SET status='PASS',blocks_po=FALSE,
                      message='Synthetic stale PASS'
                WHERE gate_name='OPEN_PO_RECONCILIATION'
                  AND scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.execute(
            """DELETE FROM exceptions
                WHERE exception_type='OPEN_PO_RECONCILIATION_REQUIRED'"""
        )
        self.conn.commit()
        second_run = self.add_run(key="monday-2026-09-07-second")
        second_po = self.add_draft(second_run["run_id"], vendor_id)
        second_line = self.add_line(
            po_id=second_po["po_id"], variant_id="1001", offer_id=offer_id
        )
        self.conn.execute(
            "UPDATE purchase_order_lines SET line_status='ORDERED' WHERE po_line_id=%s",
            (second_line,),
        )
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='REVIEW',reviewed_by='owner',
                      reviewed_at=%s,merchandise_total=120,
                      delivery_fee=0,po_total=120 WHERE po_id=%s""",
            (datetime(2026, 9, 7, 12, tzinfo=timezone.utc), second_po["po_id"]),
        )
        self.conn.commit()
        for attempt in range(2):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(
                ProcurementLedgerError, "OPEN_PO_RECONCILIATION|OPEN_EXCEPTION"
            ):
                finalize_reviewed_po(
                    self.conn,
                    po_id=second_po["po_id"],
                    actor="owner",
                    finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
                )
        self.assertEqual(
            self.conn.execute(
                "SELECT po_status FROM purchase_orders WHERE po_id=%s",
                (second_po["po_id"],),
            ).fetchone()[0],
            "REVIEW",
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM po_operational_events
                    WHERE po_id=%s AND event_type='FINALIZATION_AUTHORITY'""",
                (second_po["po_id"],),
            ).fetchone()[0],
            0,
        )

    def test_finalization_rechecks_verified_offer_vendor_variant_and_cost(self):
        _run, draft, vendor_id, offer_id, line_id = self.make_fixture_line()
        self.pass_synthetic_finalization_readiness()
        self.conn.execute(
            "UPDATE purchase_order_lines SET line_status='ORDERED' WHERE po_line_id=%s",
            (line_id,),
        )
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='REVIEW',reviewed_by='owner',
                      reviewed_at=%s,merchandise_total=999,
                      delivery_fee=0,po_total=999 WHERE po_id=%s""",
            (datetime(2026, 9, 7, 12, tzinfo=timezone.utc), draft["po_id"]),
        )
        self.conn.commit()
        with self.assertRaisesRegex(Exception, "totals do not match"):
            finalize_reviewed_po(
                self.conn,
                po_id=draft["po_id"],
                actor="owner",
                finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
            )
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='DRAFT',reviewed_by=NULL,
                      reviewed_at=NULL,po_revision=po_revision+1 WHERE po_id=%s""",
            (draft["po_id"],),
        )
        self.conn.commit()
        self.conn.execute(
            """UPDATE purchase_orders SET po_status='REVIEW',reviewed_by='owner',
                      reviewed_at=%s,merchandise_total=120,
                      delivery_fee=0,po_total=120 WHERE po_id=%s""",
            (datetime(2026, 9, 7, 12, tzinfo=timezone.utc), draft["po_id"]),
        )
        self.conn.commit()

        with self.assertRaisesRegex(Exception, "canonical readiness authorization"):
            self.conn.execute(
                """UPDATE purchase_orders SET po_status='FINAL',finalized_at=%s
                     WHERE po_id=%s""",
                (datetime(2026, 9, 7, 13, tzinfo=timezone.utc), draft["po_id"]),
            )
        self.conn.rollback()

        with self.assertRaisesRegex(Exception, "commercial line facts are immutable"):
            self.conn.execute(
                "UPDATE purchase_order_lines SET supplier_sku='WRONG-SKU' WHERE po_line_id=%s",
                (line_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "commercial line facts are immutable"):
            self.conn.execute(
                "UPDATE purchase_order_lines SET ordered_units=24 WHERE po_line_id=%s",
                (line_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "commercial line facts are immutable"):
            self.conn.execute(
                "UPDATE purchase_order_lines SET unit_cost=NULL WHERE po_line_id=%s",
                (line_id,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(Exception, "offer identity is immutable"):
            self.conn.execute(
                "UPDATE supplier_offers SET confidence='REVIEW' WHERE offer_id=%s",
                (offer_id,),
            )
        self.conn.rollback()

        drift_cases = (
            (
                "UPDATE vendors SET active=FALSE WHERE vendor_id=%s",
                (vendor_id,),
                "UPDATE vendors SET active=TRUE WHERE vendor_id=%s",
                (vendor_id,),
            ),
            (
                "UPDATE variants SET active=FALSE WHERE variant_id='1001'",
                (),
                "UPDATE variants SET active=TRUE WHERE variant_id='1001'",
                (),
            ),
        )
        for drift_sql, drift_args, restore_sql, restore_args in drift_cases:
            with self.subTest(drift_sql=drift_sql):
                self.conn.execute(drift_sql, drift_args)
                self.conn.commit()
                with self.assertRaisesRegex(Exception, "active ORDERED approved lines"):
                    finalize_reviewed_po(
                        self.conn,
                        po_id=draft["po_id"],
                        actor="owner",
                        finalized_at=datetime(2026, 9, 7, 13, tzinfo=timezone.utc),
                    )
                self.conn.rollback()
                self.conn.execute(restore_sql, restore_args)
                self.conn.commit()

        with self.assertRaisesRegex(Exception, "record import status after finalization"):
            self.conn.execute(
                """UPDATE purchase_orders SET po_status='FINAL',finalized_at=%s,
                          shopify_import_status='IMPORTED',
                          shopify_po_reference='synthetic-reference'
                     WHERE po_id=%s""",
                (datetime(2026, 9, 7, 13, tzinfo=timezone.utc), draft["po_id"]),
            )
        self.conn.rollback()

    def test_partial_cancellation_retains_trusted_remaining_incoming(self):
        _run, draft, vendor_id, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        result = record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=0,
            cancelled_units=3,
            reconciliation_status="OPEN",
            evidence={"source": "vendor-partial-cancellation", "reference": "fixture"},
            actor="owner",
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(result["line_status"], "PARTIALLY_CANCELLED")
        self.assertEqual(result["open_units"], Decimal("9.0000"))
        self.assertEqual(
            open_po_position(
                self.conn,
                variant_id="1001",
                vendor_id=vendor_id,
                as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
            )["trusted_incoming_units"],
            Decimal("9.0000"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT receipt_status FROM purchase_orders WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            "PARTIALLY_CANCELLED",
        )

    def test_partial_receipt_with_cancelled_remainder_is_not_labeled_received(self):
        _run, draft, _vendor, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        result = record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=3,
            cancelled_units=9,
            reconciliation_status="RECONCILED",
            evidence={"source": "vendor-cancellation", "reference": "fixture"},
            actor="owner",
            as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(result["line_status"], "PARTIALLY_RECEIVED_CANCELLED")
        self.assertEqual(result["open_units"], Decimal("0.0000"))
        self.assertEqual(
            self.conn.execute(
                "SELECT receipt_status FROM purchase_orders WHERE po_id=%s",
                (draft["po_id"],),
            ).fetchone()[0],
            "PARTIALLY_RECEIVED_CANCELLED",
        )

    def test_migration_reapplication_preserves_data_and_exact_objects(self):
        _run, _draft, _vendor, _offer, line_id = self.make_fixture_line()
        self.conn.execute((DB_DIR / "010_monday_po_ledger.sql").read_text(encoding="utf-8"))
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM purchase_order_lines WHERE po_line_id=%s", (line_id,)
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM pg_trigger
                   WHERE tgrelid='purchase_order_lines'::regclass
                     AND tgname='trg_validate_monday_po_line' AND NOT tgisinternal"""
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT value FROM meta WHERE key='monday_po_ledger_contract'"
            ).fetchone()[0],
            "v1",
        )
        constraint_names = {
            row[0]
            for row in self.conn.execute(
                """SELECT conname FROM pg_constraint
                   WHERE conrelid IN (
                       'runs'::regclass,'purchase_orders'::regclass,
                       'purchase_order_lines'::regclass
                   )"""
            ).fetchall()
        }
        self.assertTrue(
            {
                "ck_runs_input_fingerprint",
                "ck_runs_workflow_stage",
                "ck_runs_monday_procurement_identity",
                "ck_purchase_orders_mvp_fields",
                "ck_purchase_order_line_quantities",
                "ck_purchase_order_line_status",
                "ck_purchase_order_line_reconciliation_evidence",
            }.issubset(constraint_names)
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT is_generated,is_nullable FROM information_schema.columns
                   WHERE table_schema=current_schema()
                     AND table_name='purchase_order_lines' AND column_name='open_units'"""
            ).fetchone(),
            ("ALWAYS", "YES"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT to_regclass(current_schema() || '.v_open_procurement_incoming')::text"
            ).fetchone()[0],
            "v_open_procurement_incoming",
        )
        expected_triggers = {
            "trg_validate_monday_po_line",
            "trg_protect_final_po_line",
            "trg_validate_monday_purchase_order",
            "trg_prevent_monday_purchase_order_delete",
            "trg_prevent_referenced_offer_identity_change",
            "trg_prevent_linked_run_identity_change",
            "trg_prevent_linked_recommendation_identity_change",
            "trg_prevent_linked_review_decision_change",
            "trg_audit_final_po_line_reconciliation",
            "trg_po_reconciliation_events_append_only",
            "trg_po_operational_events_append_only",
        }
        actual_triggers = {
            row[0]
            for row in self.conn.execute(
                """SELECT tgname FROM pg_trigger
                   WHERE tgrelid IN (
                       'purchase_order_lines'::regclass,'purchase_orders'::regclass,
                       'supplier_offers'::regclass,'runs'::regclass,
                       'procurement_recommendations'::regclass,
                       'review_decisions'::regclass,'po_reconciliation_events'::regclass,
                       'po_operational_events'::regclass
                   ) AND NOT tgisinternal"""
            ).fetchall()
        }
        self.assertTrue(expected_triggers.issubset(actual_triggers))

    def test_migration_failure_rolls_back_every_packet3_schema_change(self):
        from psycopg import sql

        failure_schema = f"po_ledger_failure_{uuid.uuid4().hex}"
        self.conn.commit()
        self.conn.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(failure_schema))
        )
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(
                sql.Identifier(failure_schema)
            )
        )
        for migration in MIGRATIONS[:-1]:
            self.conn.execute((DB_DIR / migration).read_text(encoding="utf-8"))
        self.add_current("legacy-line")
        vendor_id = self.add_vendor("Legacy Line Distributor")
        offer_id = self.add_offer(
            variant_id="legacy-line", vendor_id=vendor_id, sku="LEGACY-LINE"
        )
        run_id = self.conn.execute(
            """INSERT INTO runs(run_type,status) VALUES ('LEGACY','COMPLETED')
               RETURNING run_id"""
        ).fetchone()[0]
        po_id = self.conn.execute(
            """INSERT INTO purchase_orders(run_id,vendor_id,po_status)
               VALUES (%s,%s,'DRAFT') RETURNING po_id""",
            (run_id, vendor_id),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO purchase_order_lines(
                   po_id,variant_id,offer_id,cases,loose_units,unit_cost,line_total
               ) VALUES (%s,'legacy-line',%s,1,0,10,120)""",
            (po_id, offer_id),
        )
        self.conn.commit()
        try:
            with self.assertRaises(Exception):
                with self.conn.transaction():
                    self.conn.execute(
                        (DB_DIR / "010_monday_po_ledger.sql").read_text(encoding="utf-8")
                    )
            self.assertEqual(
                self.conn.execute(
                    """SELECT count(*) FROM information_schema.columns
                       WHERE table_schema=%s AND table_name='runs'
                         AND column_name='business_date'""",
                    (failure_schema,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                self.conn.execute(
                    "SELECT count(*) FROM meta WHERE key='monday_po_ledger_contract'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                self.conn.execute(
                    """SELECT to_regclass(current_schema() || '.po_reconciliation_events'),
                              to_regclass(current_schema() || '.po_operational_events')"""
                ).fetchone(),
                (None, None),
            )
            self.assertEqual(
                self.conn.execute("SELECT count(*) FROM purchase_order_lines").fetchone()[0],
                1,
            )
        finally:
            self.conn.rollback()
            self.conn.execute(
                sql.SQL("SET search_path TO {}, public").format(
                    sql.Identifier(self.schema)
                )
            )
            self.conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(
                    sql.Identifier(failure_schema)
                )
            )
            self.conn.commit()

    def test_historical_only_variant_is_blocked_by_database_guard(self):
        self.add_current("current-offer")
        self.add_historical("old-variant")
        vendor_id = self.add_vendor("Historical Distributor")
        offer_id = self.add_offer(
            variant_id="current-offer", vendor_id=vendor_id, sku="CURRENT-SKU"
        )
        self.conn.commit()
        run = self.add_run()
        draft = self.add_draft(run["run_id"], vendor_id)
        with self.assertRaisesRegex(Exception, "active CURRENT variant"):
            self.add_line(
                po_id=draft["po_id"], variant_id="old-variant", offer_id=offer_id
            )
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_order_lines").fetchone()[0], 0
        )

    def test_wrong_vendor_or_variant_offer_relationship_is_blocked(self):
        self.add_current("1001")
        self.add_current("1002")
        first = self.add_vendor("Offer Vendor")
        second = self.add_vendor("PO Vendor")
        offer_id = self.add_offer(variant_id="1001", vendor_id=first, sku="SKU-1001")
        self.conn.commit()
        run = self.add_run()
        wrong_vendor_po = self.add_draft(run["run_id"], second)
        with self.assertRaisesRegex(Exception, "relationship is invalid"):
            self.add_line(
                po_id=wrong_vendor_po["po_id"], variant_id="1001", offer_id=offer_id
            )
        self.conn.rollback()
        right_vendor_po = self.add_draft(run["run_id"], first)
        with self.assertRaisesRegex(Exception, "relationship is invalid"):
            self.add_line(
                po_id=right_vendor_po["po_id"], variant_id="1002", offer_id=offer_id
            )
        self.conn.rollback()

    def test_duplicate_offer_line_and_invalid_database_quantity_are_rejected(self):
        _run, draft, _vendor, offer_id, _line_id = self.make_fixture_line()
        with self.assertRaises(Exception):
            self.add_line(po_id=draft["po_id"], variant_id="1001", offer_id=offer_id)
        self.conn.rollback()
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE purchase_order_lines SET received_units=13 WHERE po_id=%s",
                (draft["po_id"],),
            )
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_order_lines").fetchone()[0], 1
        )

    def test_fully_received_line_leaves_no_open_position(self):
        _run, _draft, vendor_id, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        result = record_po_reconciliation(
            self.conn,
            po_line_id=line_id,
            received_units=12,
            cancelled_units=0,
            reconciliation_status="RECONCILED",
            evidence={"source": "direct-receipt"},
            actor="owner",
        )
        self.assertEqual(result["line_status"], "RECEIVED")
        self.assertEqual(result["open_units"], Decimal("0.0000"))
        self.assertEqual(result["readiness"]["status"], "PASS")
        self.assertEqual(
            open_po_position(
                self.conn,
                variant_id="1001",
                vendor_id=vendor_id,
                as_of=datetime(2026, 9, 7, tzinfo=timezone.utc),
            )["open_line_count"],
            0,
        )

    def test_post_update_failure_rolls_back_line_event_exception_and_gate(self):
        _run, _draft, _vendor, _offer, line_id = self.make_final_open(
            trustworthy=True
        )
        recompute_open_po_reconciliation_gate(self.conn)
        before_line = self.conn.execute(
            """SELECT received_units,cancelled_units,line_status,reconciliation_status,
                      reconciliation_evidence,last_reconciled_at,last_reconciled_by
               FROM purchase_order_lines WHERE po_line_id=%s""",
            (line_id,),
        ).fetchone()
        before_gate = self.conn.execute(
            """SELECT status,blocks_po,message,evidence_json,checked_at
               FROM readiness_gates WHERE gate_name='OPEN_PO_RECONCILIATION'
                 AND scope_type='GLOBAL' AND scope_id=''"""
        ).fetchone()
        before_events = self.conn.execute(
            "SELECT count(*) FROM po_reconciliation_events"
        ).fetchone()[0]
        before_exceptions = self.conn.execute(
            """SELECT exception_id,status,resolution,resolved_at
                 FROM exceptions WHERE po_line_id=%s ORDER BY exception_id""",
            (line_id,),
        ).fetchall()
        self.conn.commit()
        with patch.object(
            po_ledger,
            "_persist_open_po_gates",
            side_effect=RuntimeError("synthetic post-update failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic post-update"):
                record_po_reconciliation(
                    self.conn,
                    po_line_id=line_id,
                    received_units=2,
                    cancelled_units=0,
                    reconciliation_status="AMBIGUOUS",
                    evidence={"source": "fixture"},
                    actor="owner",
                )
        self.assertEqual(
            self.conn.execute(
                """SELECT received_units,cancelled_units,line_status,reconciliation_status,
                          reconciliation_evidence,last_reconciled_at,last_reconciled_by
                   FROM purchase_order_lines WHERE po_line_id=%s""",
                (line_id,),
            ).fetchone(),
            before_line,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT status,blocks_po,message,evidence_json,checked_at
                   FROM readiness_gates WHERE gate_name='OPEN_PO_RECONCILIATION'
                     AND scope_type='GLOBAL' AND scope_id=''"""
            ).fetchone(),
            before_gate,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM po_reconciliation_events").fetchone()[0],
            before_events,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT exception_id,status,resolution,resolved_at
                     FROM exceptions WHERE po_line_id=%s ORDER BY exception_id""",
                (line_id,),
            ).fetchall(),
            before_exceptions,
        )


if __name__ == "__main__":
    unittest.main()
