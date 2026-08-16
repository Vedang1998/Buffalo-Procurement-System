"""F1 authoritative catalog-attempt selection and readiness consistency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from urllib.parse import quote
import uuid

from procurement_os import api, health
from procurement_os.catalog import (
    authoritative_catalog_gate,
    catalog_gate_blockers,
    evaluate_authoritative_catalog_run,
    recompute_catalog_gate,
)
from procurement_os.historical_sales import assert_catalog_ready
from procurement_os.readiness import po_readiness


DB_DIR = Path(__file__).resolve().parents[1] / "db"
TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
MIGRATIONS = (
    "schema_postgres.sql",
    "001_v1_3_catalog_sales.sql",
    "002_seed_import_records.sql",
    "003_phase3_reconciliation.sql",
    "004_identity_decision_invariants.sql",
    "005_identity_investigation.sql",
    "006_phase4_sales_backfill.sql",
)


def load_tool_module(filename: str):
    path = TOOLS_DIR / filename
    spec = importlib.util.spec_from_file_location(f"test_tool_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load tool module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


@unittest.skipUnless(os.getenv("DATABASE_URL"), "PostgreSQL integration requires DATABASE_URL")
class CatalogReadinessIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql

        self.conn = psycopg.connect(os.environ["DATABASE_URL"])
        self.schema = f"catalog_readiness_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for name in MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
        self.conn.commit()
        self.base_time = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        from psycopg import sql

        self.conn.rollback()
        self.conn.execute("SET search_path TO public")
        self.conn.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
        )
        self.conn.commit()
        self.conn.close()

    def insert_run(
        self,
        *,
        started_at: datetime,
        catalog_sync_id: str | None = None,
        status: str = "COMPLETED",
        completed_at: datetime | None | object = ...,
        pagination_complete: bool | None = True,
        reported_count: int | None = 2,
        live_rows: int = 2,
        exact_ids: int = 1,
        new_ids: int = 1,
        source_hash: str | None = "fixture-hash",
    ) -> str:
        if completed_at is ...:
            completed_at = started_at if status != "RUNNING" else None
        run_id = catalog_sync_id or str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO catalog_sync_runs(
                     catalog_sync_id,started_at,completed_at,status,
                     shopify_api_version,shopify_reported_variant_count,
                     live_rows_received,exact_current_ids,new_live_variants,
                     source_hash,pagination_complete
                   ) VALUES (%s,%s,%s,%s,'2026-07',%s,%s,%s,%s,%s,%s)""",
                (
                    run_id,
                    started_at,
                    completed_at,
                    status,
                    reported_count,
                    live_rows,
                    exact_ids,
                    new_ids,
                    source_hash,
                    pagination_complete,
                ),
            )
        return run_id

    def test_no_catalog_attempt_fails_closed(self):
        result = evaluate_authoritative_catalog_run(self.conn)
        self.assertEqual(result["status"], "FAIL")
        self.assertIsNone(result["catalog_sync_id"])
        self.assertEqual(result["blockers"], ("NO_CATALOG_SYNC_ATTEMPT",))

    def test_newest_complete_catalog_run_passes_with_all_controls(self):
        run_id = self.insert_run(started_at=self.base_time)
        result = evaluate_authoritative_catalog_run(self.conn)
        self.assertEqual(result["catalog_sync_id"], run_id)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], ())

    def test_newer_failed_attempt_cannot_fall_back_to_older_success(self):
        self.insert_run(started_at=self.base_time)
        failed_id = self.insert_run(
            started_at=self.base_time + timedelta(minutes=1),
            status="FAILED",
            pagination_complete=False,
            live_rows=0,
            exact_ids=0,
            new_ids=0,
            source_hash=None,
            reported_count=None,
        )
        result = evaluate_authoritative_catalog_run(self.conn)
        self.assertEqual(result["catalog_sync_id"], failed_id)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("AUTHORITATIVE_CATALOG_RUN_NOT_COMPLETED", result["blockers"])

    def test_newer_pagination_incomplete_attempt_masks_older_success(self):
        self.insert_run(started_at=self.base_time)
        incomplete_id = self.insert_run(
            started_at=self.base_time + timedelta(minutes=1),
            pagination_complete=False,
        )
        result = evaluate_authoritative_catalog_run(self.conn)
        self.assertEqual(result["catalog_sync_id"], incomplete_id)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("CATALOG_PAGINATION_INCOMPLETE", result["blockers"])

    def test_equal_start_time_uses_uuid_only_as_deterministic_tie_break(self):
        lower_id = "00000000-0000-0000-0000-000000000001"
        higher_id = "00000000-0000-0000-0000-000000000002"
        self.insert_run(started_at=self.base_time, catalog_sync_id=lower_id)
        self.insert_run(
            started_at=self.base_time,
            catalog_sync_id=higher_id,
            status="FAILED",
        )
        first = evaluate_authoritative_catalog_run(self.conn)
        second = evaluate_authoritative_catalog_run(self.conn)
        self.assertEqual(first["catalog_sync_id"], higher_id)
        self.assertEqual(second["catalog_sync_id"], higher_id)
        self.assertEqual(first["status"], "FAIL")

    def test_reported_count_drift_is_diagnostic_and_does_not_block(self):
        run_id = self.insert_run(
            started_at=self.base_time,
            reported_count=2003,
            live_rows=1999,
            exact_ids=1979,
            new_ids=20,
            pagination_complete=True,
            source_hash="known-count-drift-fixture",
        )
        result = evaluate_authoritative_catalog_run(self.conn)
        self.assertEqual(result["catalog_sync_id"], run_id)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], ())
        self.assertEqual(
            result["diagnostics"],
            {
                "shopify_reported_count_mismatch": True,
                "shopify_reported_count_delta": 4,
            },
        )
        gate = authoritative_catalog_gate(self.conn)
        self.assertEqual(gate["status"], "PASS")
        self.assertIs(gate["evidence"]["shopify_reported_count_mismatch"], True)
        self.assertEqual(gate["evidence"]["shopify_reported_count_delta"], 4)
        assert_catalog_ready(self.conn)

        optional_id = self.insert_run(
            started_at=self.base_time + timedelta(minutes=1), reported_count=None
        )
        optional = evaluate_authoritative_catalog_run(self.conn)
        self.assertEqual(optional["catalog_sync_id"], optional_id)
        self.assertEqual(optional["status"], "PASS")
        self.assertEqual(
            optional["diagnostics"],
            {
                "shopify_reported_count_mismatch": False,
                "shopify_reported_count_delta": None,
            },
        )

    def test_every_public_status_consumer_uses_same_authoritative_run(self):
        self.insert_run(started_at=self.base_time)
        authoritative_id = self.insert_run(
            started_at=self.base_time + timedelta(minutes=1),
            status="FAILED",
            pagination_complete=False,
            live_rows=0,
            exact_ids=0,
            new_ids=0,
            source_hash=None,
            reported_count=None,
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE readiness_gates SET status='PASS'
                   WHERE gate_name='CATALOG_SYNC'
                     AND scope_type='GLOBAL' AND scope_id=''"""
            )
        with self.assertRaisesRegex(RuntimeError, "CATALOG_SYNC is not PASS"):
            assert_catalog_ready(self.conn)
        self.conn.commit()
        context = ConnectionContext(self.conn)
        with patch.object(api, "_db_conn", return_value=context):
            reconciliation = api.reconciliation_items()
            investigation = api.investigation_items()
            foundation = api.foundation_status()

        effective_catalog = next(
            gate
            for gate in foundation["gates"]
            if gate["gate_name"] == "CATALOG_SYNC"
            and gate["scope_type"] == "GLOBAL"
        )

        database_url = os.environ["DATABASE_URL"]
        schema_url = (
            f"{database_url}?options="
            f"{quote(f'-csearch_path={self.schema},public', safe='')}"
        )
        with patch.dict(os.environ, {"DATABASE_URL": schema_url}), patch.object(
            health, "check_schema", return_value={"ok": True}
        ), patch.object(
            health,
            "check_seed",
            return_value={"ok": True, "imported": True},
        ):
            full_status = api.health_full()

        ids = {
            reconciliation["run"]["catalog_sync_id"],
            investigation["run"],
            effective_catalog["evidence"]["catalog_sync_id"],
            full_status["gates"]["CATALOG_SYNC"]["evidence"]["catalog_sync_id"],
        }
        self.assertEqual(ids, {authoritative_id})
        self.assertEqual(reconciliation["run"]["readiness_status"], "FAIL")
        self.assertEqual(
            investigation["catalog_readiness"]["status"], "FAIL"
        )

    def test_recompute_does_not_create_identity_decisions_or_clear_blockers(self):
        run_id = self.insert_run(started_at=self.base_time)
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO catalog_reconciliation_items(
                     catalog_sync_id,variant_id,seed_variant_id,classification,
                     blocking,evidence_json
                   ) VALUES (%s,'old-id','old-id','MISSING',TRUE,'{}'::jsonb)""",
                (run_id,),
            )
        result = recompute_catalog_gate(self.conn)
        self.assertEqual(result["status"], "FAIL")
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM variant_aliases")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute(
                """SELECT blocking,resolved_at FROM catalog_reconciliation_items
                   WHERE catalog_sync_id=%s""",
                (run_id,),
            )
            self.assertEqual(cur.fetchone(), (True, None))

    def test_identity_tool_never_falls_back_from_newer_failed_attempt(self):
        self.insert_run(started_at=self.base_time)
        failed_id = self.insert_run(
            started_at=self.base_time + timedelta(minutes=1),
            status="FAILED",
            pagination_complete=False,
            live_rows=0,
            exact_ids=0,
            new_ids=0,
            source_hash=None,
            reported_count=None,
        )
        tool = load_tool_module("run_identity_investigation.py")
        with self.assertRaisesRegex(
            RuntimeError, "AUTHORITATIVE_CATALOG_RUN_NOT_COMPLETED"
        ):
            tool.authoritative_investigation_catalog_sync_id(self.conn)
        self.assertEqual(
            evaluate_authoritative_catalog_run(self.conn)["catalog_sync_id"],
            failed_id,
        )

    def test_identity_tool_accepts_complete_run_with_identity_blockers(self):
        run_id = self.insert_run(started_at=self.base_time)
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO catalog_reconciliation_items(
                     catalog_sync_id,variant_id,seed_variant_id,classification,
                     blocking,evidence_json
                   ) VALUES (%s,'old-id','old-id','MISSING',TRUE,'{}'::jsonb)""",
                (run_id,),
            )
        tool = load_tool_module("run_identity_investigation.py")
        self.assertEqual(
            tool.authoritative_investigation_catalog_sync_id(self.conn), run_id
        )

    def test_count_diagnostic_never_attaches_to_older_completed_run(self):
        old_id = self.insert_run(started_at=self.base_time)
        self.insert_run(
            started_at=self.base_time + timedelta(minutes=1),
            status="FAILED",
            pagination_complete=False,
            live_rows=0,
            exact_ids=0,
            new_ids=0,
            source_hash=None,
            reported_count=None,
        )
        tool = load_tool_module("diagnose_count_discrepancy.py")
        with self.assertRaisesRegex(
            RuntimeError, "AUTHORITATIVE_CATALOG_RUN_NOT_COMPLETED"
        ):
            tool.persist_diagnostic_report(self.conn, {"fixture": True})
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT notes FROM catalog_sync_runs WHERE catalog_sync_id=%s",
                (old_id,),
            )
            self.assertIsNone(cur.fetchone()[0])

    def test_postgres_po_readiness_preserves_scope_and_declared_applicability(self):
        self.insert_run(started_at=self.base_time)
        vendor_a = "10000000-0000-0000-0000-000000000001"
        vendor_b = "10000000-0000-0000-0000-000000000002"
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE readiness_gates SET status='PASS' WHERE gate_name='SALES_BACKFILL'"
            )

        current = po_readiness(self.conn, vendor_id=vendor_b, variant_id="Y")
        self.assertEqual(current["po_generation_enabled"], False)
        self.assertEqual(
            [item["detail"]["gate_name"] for item in current["blockers"]],
            ["VENDOR_RULES"],
        )

        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE readiness_gates SET status='PASS' WHERE gate_name='VENDOR_RULES'"
            )
            cur.execute(
                """INSERT INTO vendors(vendor_id,vendor_name)
                   VALUES (%s,'Vendor A'),(%s,'Vendor B')""",
                (vendor_a, vendor_b),
            )
            cur.execute(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,active
                   ) VALUES
                     ('X','PX','Product X','Variant X',TRUE),
                     ('Y','PY','Product Y','Variant Y',TRUE)"""
            )
            cur.execute(
                """INSERT INTO readiness_gates(
                     gate_name,scope_type,scope_id,status,severity,blocks_po,message
                   ) VALUES
                     ('PRICE_COVERAGE','VENDOR',%s,'FAIL','HIGH',TRUE,'Vendor A fixture'),
                     ('MAPPING_INTEGRITY','VARIANT','X','FAIL','HIGH',TRUE,'Variant X fixture')""",
                (vendor_a,),
            )
            cur.execute(
                """INSERT INTO exceptions(
                     exception_type,severity,variant_id,vendor_id,message,status
                   ) VALUES ('FIXTURE','HIGH','X',%s,'Combined scope fixture','OPEN')""",
                (vendor_a,),
            )

        affected = po_readiness(self.conn, vendor_id=vendor_a, variant_id="X")
        affected_types = [item["type"] for item in affected["blockers"]]
        self.assertEqual(affected_types.count("READINESS_GATE"), 2)
        self.assertEqual(affected_types.count("OPEN_EXCEPTION"), 1)

        unrelated = po_readiness(self.conn, vendor_id=vendor_b, variant_id="Y")
        self.assertEqual(unrelated["po_generation_enabled"], True)
        self.assertEqual(unrelated["blockers"], [])

        vendor_only = po_readiness(self.conn, vendor_id=vendor_a, variant_id="Y")
        self.assertEqual(
            [item["type"] for item in vendor_only["blockers"]],
            ["READINESS_GATE"],
        )

        with self.conn.cursor() as cur:
            cur.execute(
                """DELETE FROM readiness_gates
                   WHERE gate_name='OPEN_PO_RECONCILIATION'
                     AND scope_type='GLOBAL' AND scope_id=''"""
            )
        declared_missing = po_readiness(
            self.conn,
            vendor_id=vendor_b,
            variant_id="Y",
            applicable_gate_names={"OPEN_PO_RECONCILIATION"},
        )
        self.assertEqual(declared_missing["po_generation_enabled"], False)
        self.assertEqual(
            [(item["type"], item["detail"]["gate_name"])
             for item in declared_missing["blockers"]],
            [("MISSING_APPLICABLE_GATE", "OPEN_PO_RECONCILIATION")],
        )


class CatalogEvidenceUnitTests(unittest.TestCase):
    def complete_run(self) -> dict:
        return {
            "status": "COMPLETED",
            "completed_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
            "pagination_complete": True,
            "shopify_reported_variant_count": 2,
            "live_rows_received": 2,
            "exact_current_ids": 1,
            "new_live_variants": 1,
            "source_hash": "fixture-hash",
        }

    def test_each_required_catalog_control_fails_closed(self):
        cases = (
            ({"status": "RUNNING"}, "AUTHORITATIVE_CATALOG_RUN_NOT_COMPLETED"),
            ({"completed_at": None}, "CATALOG_COMPLETION_TIMESTAMP_MISSING"),
            ({"pagination_complete": False}, "CATALOG_PAGINATION_INCOMPLETE"),
            (
                {
                    "live_rows_received": 0,
                    "exact_current_ids": 0,
                    "new_live_variants": 0,
                    "shopify_reported_variant_count": None,
                },
                "NO_LIVE_CATALOG_ROWS",
            ),
            ({"new_live_variants": 0}, "CATALOG_IDENTITY_ACCOUNTING_MISMATCH"),
            ({"source_hash": None}, "CATALOG_SNAPSHOT_HASH_MISSING"),
        )
        for override, expected in cases:
            with self.subTest(expected=expected):
                blockers = catalog_gate_blockers(
                    {**self.complete_run(), **override}, unresolved_blockers=0
                )
                self.assertIn(expected, blockers)
        self.assertIn(
            "CATALOG_IDENTITY_BLOCKERS_UNRESOLVED",
            catalog_gate_blockers(self.complete_run(), unresolved_blockers=1),
        )

    def test_catalog_run_selection_sql_has_one_implementation_point(self):
        procurement_root = Path(__file__).resolve().parents[1]
        selector_pattern = re.compile(
            r"\bfrom\s+catalog_sync_runs\b", re.IGNORECASE
        )
        selectors = [
            path.relative_to(procurement_root).as_posix()
            for path in procurement_root.rglob("*.py")
            if "tests" not in path.relative_to(procurement_root).parts
            and selector_pattern.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(selectors, ["src/procurement_os/catalog.py"])

if __name__ == "__main__":
    unittest.main()
