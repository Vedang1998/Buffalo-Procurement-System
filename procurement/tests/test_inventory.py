"""Packet 1 owned inventory snapshot and readiness tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect
from pathlib import Path
import unittest
import uuid

from fastapi.routing import APIRoute

from postgres_test_support import validated_test_connection
from procurement_os import api
from procurement_os.inventory import (
    InventoryValidationError,
    capture_daily_inventory,
    evaluate_inventory_history,
    inventory_source_hash,
    latest_inventory_snapshot_status,
    normalize_inventory_levels,
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
)


class InventoryPureTests(unittest.TestCase):
    def test_normalization_is_order_independent_and_rejects_duplicate_keys(self):
        rows = normalize_inventory_levels(
            [
                {"variant_id": "2", "available_quantity": "1.0000"},
                {"variant_id": "1", "available_quantity": 0},
            ]
        )
        self.assertEqual([row.variant_id for row in rows], ["1", "2"])
        self.assertEqual(
            inventory_source_hash(rows, business_date=date(2026, 9, 7)),
            inventory_source_hash(reversed(rows), business_date=date(2026, 9, 7)),
        )
        with self.assertRaisesRegex(InventoryValidationError, "duplicate"):
            normalize_inventory_levels(
                [
                    {"variant_id": "1", "location_gid": "L"},
                    {"variant_id": "1", "location_gid": "L"},
                ]
            )

    def test_nonfinite_or_overprecision_quantity_is_rejected(self):
        for value in ("NaN", "Infinity", "1.00001", True):
            with self.subTest(value=value), self.assertRaises(InventoryValidationError):
                normalize_inventory_levels(
                    [{"variant_id": "1", "available_quantity": value}]
                )

    def test_cli_and_status_route_have_no_shopify_or_mutating_get_path(self):
        service_source = inspect.getsource(
            __import__("procurement_os.inventory", fromlist=["inventory"])
        ).upper()
        tool_source = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "capture_inventory_snapshot.py"
        ).read_text(encoding="utf-8").upper()
        self.assertNotIn("SHOPIFYGRAPHQLCLIENT", service_source + tool_source)
        self.assertNotIn("SHOPIFY_ADMIN", service_source + tool_source)
        route = next(
            route
            for route in api.app.routes
            if isinstance(route, APIRoute)
            and route.path == "/inventory-snapshots/status"
        )
        self.assertEqual(route.methods, {"GET"})


class InventoryPostgresTests(unittest.TestCase):
    BUSINESS_DATE = date(2026, 9, 7)

    def setUp(self) -> None:
        from psycopg import sql

        self.conn, self.test_target, self.test_database_info = (
            validated_test_connection()
        )
        self.schema = f"inventory_mvp_{uuid.uuid4().hex}"
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

    def insert_current(
        self,
        variant_id: str,
        *,
        active: bool = True,
        catalog_state: str = "LIVE",
    ) -> None:
        self.conn.execute(
            """INSERT INTO variants(
                   variant_id,product_id,product_title,variant_title,
                   active,catalog_state,identity_scope
               ) VALUES (%s,%s,%s,'750ML',%s,%s,'CURRENT')""",
            (variant_id, f"product-{variant_id}", f"Product {variant_id}", active, catalog_state),
        )

    def insert_historical(self, variant_id: str) -> None:
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

    @staticmethod
    def row(variant_id: str, available, incoming=0, *, location: str = "L1") -> dict:
        return {
            "variant_id": variant_id,
            "location_gid": location,
            "available_quantity": available,
            "incoming_quantity": incoming,
            "on_hand_quantity": available,
            "committed_quantity": 0,
            "reserved_quantity": 0,
            "damaged_quantity": 0,
        }

    def capture(self, rows, *, day=None, at_hour=2, source="OFFLINE_TEST", **kwargs):
        return capture_daily_inventory(
            self.conn,
            business_date=day or self.BUSINESS_DATE,
            rows=rows,
            source=source,
            captured_at=datetime(2026, 9, 7, at_hour, tzinfo=timezone.utc),
            **kwargs,
        )

    def test_exact_replay_is_zero_dml(self):
        self.insert_current("1")
        self.insert_current("2")
        self.conn.commit()
        first = self.capture([self.row("2", 4), self.row("1", 2)])
        replay = self.capture([self.row("1", 2), self.row("2", 4)])
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["rows_written"], 0)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM inventory_snapshot_runs").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM daily_inventory_snapshots").fetchone()[0],
            2,
        )

    def test_changed_same_day_capture_updates_daily_and_retains_attempt_evidence(self):
        self.insert_current("1")
        self.conn.commit()
        self.capture([self.row("1", 1)], at_hour=2)
        second = self.capture([self.row("1", 3)], at_hour=3)
        self.assertFalse(second["idempotent_replay"])
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM inventory_snapshot_runs").fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM inventory_snapshot_run_rows").fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*),max(available_quantity) FROM daily_inventory_snapshots"
            ).fetchone(),
            (1, Decimal("3.0000")),
        )

    def test_procurement_views_exclude_noncurrent_inactive_and_nonlive_rows(self):
        self.insert_current("current")
        self.insert_current("inactive", active=False)
        self.insert_current("seeded", catalog_state="SEEDED")
        self.insert_historical("historical")
        self.conn.commit()
        result = self.capture(
            [
                self.row("current", 2),
                self.row("inactive", 2),
                self.row("seeded", 2),
                self.row("historical", 2),
            ]
        )
        self.assertEqual(result["validation_counts"]["ARCHIVAL_ONLY"], 3)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM daily_inventory_snapshots").fetchone()[0],
            4,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT variant_id FROM v_latest_procurement_inventory"
            ).fetchall(),
            [("current",)],
        )

    def test_zero_is_valid_and_negative_or_null_is_not_coerced(self):
        for variant in ("zero", "negative", "unknown"):
            self.insert_current(variant)
        self.conn.commit()
        result = self.capture(
            [
                self.row("zero", 0, 0),
                self.row("negative", -1, 0),
                self.row("unknown", None, 0),
            ]
        )
        self.assertEqual(
            result["validation_counts"],
            {"VALID": 1, "INCOMPLETE": 0, "INVALID": 2, "ARCHIVAL_ONLY": 0},
        )
        self.assertEqual(result["readiness"]["status"], "FAIL")
        values = self.conn.execute(
            """SELECT variant_id,available_quantity,validation_status
               FROM daily_inventory_snapshots ORDER BY variant_id"""
        ).fetchall()
        self.assertEqual(values[0], ("negative", Decimal("-1.0000"), "INVALID"))
        self.assertEqual(values[1], ("unknown", None, "INVALID"))
        self.assertEqual(values[2], ("zero", Decimal("0.0000"), "VALID"))

    def test_unknown_variant_fails_before_any_capture_write(self):
        with self.assertRaisesRegex(InventoryValidationError, "unknown canonical"):
            self.capture([self.row("missing", 1)])
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM inventory_snapshot_runs").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM daily_inventory_snapshots").fetchone()[0],
            0,
        )

    def test_mid_batch_failure_rolls_back_run_rows_daily_rows_and_gate(self):
        self.insert_current("1")
        self.insert_current("2")
        before_gate = self.conn.execute(
            """SELECT status,message,checked_at FROM readiness_gates
               WHERE gate_name='INVENTORY_HISTORY'"""
        ).fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(RuntimeError, "synthetic inventory"):
            self.capture(
                [self.row("1", 1), self.row("2", 2)],
                _inject_failure_after_row=1,
            )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM inventory_snapshot_runs").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM daily_inventory_snapshots").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT status,message,checked_at FROM readiness_gates
                   WHERE gate_name='INVENTORY_HISTORY'"""
            ).fetchone(),
            before_gate,
        )

    def test_readiness_pass_warn_and_fail_are_deterministic(self):
        self.insert_current("1")
        self.insert_current("2")
        self.conn.commit()
        passed = self.capture([self.row("1", 1), self.row("2", 2)])
        self.assertEqual(passed["readiness"]["status"], "PASS")

        warning_day = self.BUSINESS_DATE + timedelta(days=1)
        warned = self.capture(
            [self.row("1", 1, None), self.row("2", 2, 0)],
            day=warning_day,
            at_hour=3,
        )
        self.assertEqual(warned["readiness"]["status"], "WARN")
        self.assertIn("unknown incoming", warned["readiness"]["message"])

        failure_day = warning_day + timedelta(days=1)
        failed = self.capture([self.row("1", 1)], day=failure_day, at_hour=4)
        self.assertEqual(failed["readiness"]["status"], "FAIL")
        self.assertEqual(failed["readiness"]["evidence"]["missing_current_variants"], 1)
        gate = self.conn.execute(
            """SELECT status,blocks_po FROM readiness_gates
               WHERE gate_name='INVENTORY_HISTORY'"""
        ).fetchone()
        self.assertEqual(gate, ("FAIL", True))

    def test_stale_snapshot_warns_and_status_readback_is_select_only(self):
        self.insert_current("1")
        self.conn.commit()
        self.capture([self.row("1", 1)])
        self.conn.commit()
        before_xid = self.conn.execute("SELECT txid_current_if_assigned()").fetchone()[0]
        status = latest_inventory_snapshot_status(
            self.conn, as_of_date=self.BUSINESS_DATE + timedelta(days=3)
        )
        after_xid = self.conn.execute("SELECT txid_current_if_assigned()").fetchone()[0]
        self.assertEqual(status["status"], "WARN")
        self.assertIn("3 day", status["message"])
        self.assertIsNone(before_xid)
        self.assertIsNone(after_xid)

    def test_historical_only_rows_do_not_create_current_coverage_deficit(self):
        self.insert_current("current")
        self.insert_historical("historical")
        self.conn.commit()
        result = self.capture([self.row("current", 1)])
        self.assertEqual(result["readiness"]["status"], "PASS")
        evaluation = evaluate_inventory_history(
            self.conn, as_of_date=self.BUSINESS_DATE
        )
        self.assertEqual(evaluation["evidence"]["current_live_variants"], 1)
        self.assertEqual(evaluation["evidence"]["missing_current_variants"], 0)

    def test_inventory_migration_reapplication_is_idempotent(self):
        migration = (DB_DIR / "008_monday_inventory_foundation.sql").read_text(
            encoding="utf-8"
        )
        self.conn.execute(migration)
        self.conn.execute(migration)
        self.conn.commit()
        relations = {
            row[0]
            for row in self.conn.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema=current_schema()
                     AND table_name IN (
                         'inventory_snapshot_runs','inventory_snapshot_run_rows'
                     )"""
            ).fetchall()
        }
        self.assertEqual(
            relations, {"inventory_snapshot_runs", "inventory_snapshot_run_rows"}
        )
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO inventory_snapshot_runs(
                           business_date,status,source,source_hash
                       ) VALUES (%s,'COMPLETED','fixture',%s)""",
                    (self.BUSINESS_DATE, "not-a-sha"),
                )


if __name__ == "__main__":
    unittest.main()
