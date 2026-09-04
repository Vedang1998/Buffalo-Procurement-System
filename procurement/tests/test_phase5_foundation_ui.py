"""Deterministic Phase 5 Foundation UI acceptance and read-only safety tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin
import uuid

from fastapi.testclient import TestClient

from procurement_os import api, health


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
)

NAV_LABELS = (
    "System Readiness",
    "Catalog Reconciliation",
    "Historical Sales Reconciliation",
    "Data/Sync Runs",
)


def gate(
    name: str,
    status: str,
    message: str,
    *,
    blocks_po: bool,
    evidence: dict | None = None,
) -> dict:
    return {
        "gate_name": name,
        "scope_type": "GLOBAL",
        "scope_id": "",
        "status": status,
        "severity": "CRITICAL" if name in {"CATALOG_SYNC", "SALES_BACKFILL"} else "HIGH",
        "blocks_po": blocks_po,
        "message": message,
        "evidence": evidence or {},
        "checked_at": datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
    }


def current_readiness() -> dict:
    gates = [
        gate("CATALOG_SYNC", "PASS", "Catalog reconciliation passed.", blocks_po=True),
        gate(
            "SALES_BACKFILL",
            "PASS",
            "Historical ShopifyQL sales backfill, identity accounting, and controls passed.",
            blocks_po=True,
            evidence={"sales_backfill_id": "sales-run-current"},
        ),
        gate(
            "VENDOR_RULES",
            "FAIL",
            "Vendor operating rules are not yet confirmed complete.",
            blocks_po=True,
        ),
        gate(
            "INVENTORY_HISTORY",
            "WARN",
            "Own daily inventory snapshots are not yet confirmed running.",
            blocks_po=False,
        ),
        gate(
            "MAPPING_INTEGRITY",
            "WARN",
            "Supplier mapping integrity is not yet fully validated.",
            blocks_po=False,
        ),
        gate(
            "OPEN_PO_RECONCILIATION",
            "WARN",
            "Procurement PO reconciliation is not yet fully operational.",
            blocks_po=False,
        ),
        gate(
            "PRICE_COVERAGE",
            "WARN",
            "Full-catalog verified supplier pricing is not yet complete.",
            blocks_po=False,
        ),
    ]
    vendor_gate = next(item for item in gates if item["gate_name"] == "VENDOR_RULES")
    return {
        "po_generation_enabled": False,
        "scope": {"vendor_id": None, "variant_id": None, "run_id": None},
        "applicable_gate_names": ["CATALOG_SYNC", "SALES_BACKFILL"],
        "blockers": [{"type": "READINESS_GATE", "detail": vendor_gate}],
        "gates": gates,
    }


def health_report(readiness: dict | None = None) -> dict:
    readiness = readiness or current_readiness()
    return {
        "application": {"ok": True, "version": "1.3.0"},
        "database": {"ok": True},
        "database_url_guard": {"ok": True, "configured": True},
        "schema": {"ok": True, "missing_core_tables": []},
        "seed": {
            "ok": True,
            "imported": True,
            "latest_import_at": "2026-08-10T00:00:00Z",
            "latest_validation_result": "PASS",
        },
        "shopify_credentials": {"configured": True, "missing_vars": []},
        "po_generation_enabled": readiness["po_generation_enabled"],
        "po_readiness": readiness,
        "gates": {item["gate_name"]: item for item in readiness["gates"]},
    }


def run_status_report() -> dict:
    return {
        "catalog": {
            "selection": "AUTHORITATIVE_CATALOG_ATTEMPT",
            "run": {
                "catalog_sync_id": "catalog-run-current",
                "started_at": "2026-08-10T14:55:53Z",
                "completed_at": "2026-08-10T14:55:54Z",
                "status": "COMPLETED",
                "shopify_api_version": "2026-07",
                "shopify_reported_variant_count": 2003,
                "live_rows_received": 1999,
                "exact_current_ids": 1999,
                "new_live_variants": 0,
                "pagination_complete": True,
                "unresolved_blockers": 0,
            },
            "readiness": {
                "status": "PASS",
                "message": "Catalog reconciliation passed.",
                "blockers": [],
            },
        },
        "historical_sales": {
            "selection": "CANONICAL_READINESS_GATE",
            "run": {
                "sales_backfill_id": "sales-run-current",
                "start_date": "2024-11-28",
                "end_date": "2026-08-10",
                "started_at": "2026-08-10T16:44:26Z",
                "completed_at": "2026-09-04T11:48:28Z",
                "status": "COMPLETED",
                "completed_chunks": 21,
                "expected_chunks": 21,
                "completed_pages": 70,
                "expected_pages": 70,
                "unique_source_facts": 59083,
                "resolved_rows": 57429,
                "unresolved_rows": 0,
                "ambiguous_rows": 0,
                "excluded_rows": 1654,
                "coverage_complete": True,
                "pages_complete": True,
                "source_facts_persisted": True,
                "idempotency_verified": True,
                "control_totals_reconciled": True,
                "canonical_aggregate_rebuilt": True,
            },
            "readiness": {
                "status": "PASS",
                "message": "Historical sales controls passed.",
                "blockers": [],
            },
        },
    }


class Phase5RenderingTests(unittest.TestCase):
    def test_system_readiness_renders_every_backend_gate_status_and_message(self):
        report = health_report()
        report["po_readiness"]["gates"][3]["message"] = "Snapshot <warning>"
        page = api._admin_status_html(report, nav_root="../")
        for item in report["po_readiness"]["gates"]:
            self.assertIn(item["gate_name"], page)
            self.assertIn(item["status"], page)
        self.assertIn("Snapshot &lt;warning&gt;", page)
        self.assertNotIn("Snapshot <warning>", page)

    def test_current_vendor_rules_failure_is_the_canonical_po_blocker(self):
        page = api._admin_status_html(health_report(), nav_root="../")
        self.assertIn("PO generation: DISABLED", page)
        self.assertIn("VENDOR_RULES", page)
        self.assertIn("Vendor operating rules are not yet confirmed complete.", page)
        self.assertIn("<button type='button' disabled", page)
        self.assertNotIn(
            "CATALOG_SYNC and SALES_BACKFILL must PASS against production data first",
            page,
        )

    def test_backend_enabled_state_adds_no_po_action(self):
        readiness = current_readiness()
        readiness["po_generation_enabled"] = True
        readiness["blockers"] = []
        page = api._admin_status_html(health_report(readiness), nav_root="../")
        self.assertIn("PO generation: ENABLED", page)
        self.assertNotIn("<form", page.lower())
        self.assertNotIn("/po", page.lower())
        self.assertNotIn("onclick", page.lower())

    def test_data_sync_runs_renderer_shows_durable_evidence_without_controls(self):
        page = api._data_sync_runs_html(run_status_report(), nav_root="")
        for value in (
            "catalog-run-current",
            "sales-run-current",
            "1,999",
            "21 / 21",
            "70 / 70",
            "59,083",
            "57,429",
            "1,654",
            "Catalog reconciliation passed.",
            "Historical sales controls passed.",
        ):
            self.assertIn(value, page)
        lowered = page.lower()
        for forbidden in (
            "<form",
            "method='post'",
            'method="post"',
            "onclick=",
            "<script",
            "sync now",
            ">retry<",
            ">rebuild<",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_all_four_surfaces_render_the_same_navigation(self):
        pages = [
            api._admin_status_html(health_report(), nav_root="../"),
            api._data_sync_runs_html(run_status_report(), nav_root=""),
            api._historical_sales_review_html([]),
        ]
        with patch.object(
            api,
            "reconciliation_items",
            return_value={"run": None, "items": []},
        ):
            pages.append(api.reconciliation_page())
        for page in pages:
            with self.subTest(title=page.split("<title>", 1)[-1].split("</title>", 1)[0]):
                for label in NAV_LABELS:
                    self.assertIn(label, page)
                self.assertIn("aria-label='Phase 5 operations'", page)

    def test_shared_navigation_itself_is_get_only_and_non_actionable(self):
        nav = api._operational_nav("../", current="Historical Sales Reconciliation")
        self.assertEqual(nav.count("<a "), 4)
        self.assertIn("../admin/status", nav)
        self.assertIn("../reconciliation", nav)
        self.assertIn("../historical-sales/review", nav)
        self.assertIn("../data-sync-runs", nav)
        self.assertEqual(
            urljoin(
                "https://example.test/procurement/historical-sales/review",
                "../admin/status",
            ),
            "https://example.test/procurement/admin/status",
        )
        for forbidden in ("<form", "<button", "onclick", "method=", "action="):
            self.assertNotIn(forbidden, nav.lower())

    def test_only_the_canonical_data_sync_runs_get_route_is_added(self):
        route_methods = {
            (route.path, method)
            for route in api.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("/data-sync-runs", "GET"), route_methods)
        self.assertNotIn(("/data-sync-runs", "POST"), route_methods)
        self.assertFalse(any(path in {"/runs", "/sync"} for path, _ in route_methods))
        for existing in (
            "/admin/status",
            "/reconciliation",
            "/historical-sales/review",
            "/health/full",
            "/foundation/status",
        ):
            self.assertIn((existing, "GET"), route_methods)

    def test_data_sync_runs_route_returns_200_and_only_reads_backend_status(self):
        client = TestClient(api.app)
        connection = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = connection
        with patch.object(api, "_db_conn", return_value=context), patch.object(
            api, "data_sync_run_status", return_value=run_status_report()
        ) as status:
            response = client.get("/data-sync-runs")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Data/Sync Runs", response.text)
        status.assert_called_once_with(connection)

    def test_existing_reconciliation_mutation_routes_remain_unchanged(self):
        route_methods = {
            (route.path, method)
            for route in api.app.routes
            for method in getattr(route, "methods", set())
        }
        for route in (
            "/reconciliation/approve-recreation",
            "/reconciliation/reject-recreation",
            "/reconciliation/retire",
            "/reconciliation/recompute-gate",
            "/reconciliation/investigation/retire-batch",
            "/reconciliation/decide",
            "/historical-sales/review/decide",
        ):
            self.assertIn((route, "POST"), route_methods)


class Phase5BackendAggregationTests(unittest.TestCase):
    def test_full_health_uses_one_canonical_po_readiness_result(self):
        readiness = current_readiness()
        connection = MagicMock()
        connection_context = MagicMock()
        connection_context.__enter__.return_value = connection
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://fixture"}), patch(
            "psycopg.connect", return_value=connection_context
        ), patch.object(health, "check_database", return_value={"ok": True}), patch.object(
            health, "check_schema", return_value={"ok": True, "missing_core_tables": []}
        ), patch.object(
            health, "check_seed", return_value={"ok": True, "imported": True}
        ), patch.object(health, "po_readiness", return_value=readiness) as canonical:
            report = health.full_health()
        canonical.assert_called_once_with(connection)
        self.assertIs(report["po_readiness"], readiness)
        self.assertFalse(report["po_generation_enabled"])
        self.assertEqual(set(report["gates"]), {item["gate_name"] for item in readiness["gates"]})

    def test_new_backend_status_helpers_are_select_only_and_have_no_job_or_shopify_path(self):
        source = "\n".join(
            (
                inspect.getsource(health.data_sync_run_status),
                inspect.getsource(health._sales_run_row),
            )
        ).upper()
        for forbidden in (
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "UPSERT",
            "CREATE ",
            "ALTER ",
            "DROP ",
            "SHOPIFYGRAPHQLCLIENT",
            "FINALIZE_SALES_BACKFILL",
            "RECOMPUTE_CATALOG_GATE",
            "PERSIST_CATALOG_SYNC",
        ):
            self.assertNotIn(forbidden, source)


@unittest.skipUnless(os.getenv("DATABASE_URL"), "PostgreSQL integration requires DATABASE_URL")
class Phase5RunSelectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql

        self.conn = psycopg.connect(os.environ["DATABASE_URL"])
        self.schema = f"phase5_ui_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for name in MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
        self.conn.commit()
        self.base_time = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        from psycopg import sql

        self.conn.rollback()
        self.conn.execute("SET search_path TO public")
        self.conn.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
        )
        self.conn.commit()
        self.conn.close()

    def insert_catalog_run(self, *, started_at: datetime, status: str) -> str:
        run_id = str(uuid.uuid4())
        completed_at = started_at if status == "COMPLETED" else None
        self.conn.execute(
            """INSERT INTO catalog_sync_runs(
                 catalog_sync_id,started_at,completed_at,status,shopify_api_version,
                 shopify_reported_variant_count,live_rows_received,exact_current_ids,
                 new_live_variants,source_hash,pagination_complete
               ) VALUES (%s,%s,%s,%s,'2026-07',2,%s,%s,0,%s,%s)""",
            (
                run_id,
                started_at,
                completed_at,
                status,
                2 if status == "COMPLETED" else 0,
                2 if status == "COMPLETED" else 0,
                "catalog-hash" if status == "COMPLETED" else None,
                status == "COMPLETED",
            ),
        )
        return run_id

    def insert_sales_run(
        self,
        *,
        started_at: datetime,
        status: str,
        reviewable: bool,
    ) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO sales_backfill_runs(
                 sales_backfill_id,started_at,completed_at,status,start_date,end_date,
                 expected_chunks,completed_chunks,expected_pages,completed_pages,
                 unique_source_facts,resolved_rows,unresolved_rows,ambiguous_rows,
                 excluded_rows,coverage_complete,pages_complete,source_facts_persisted,
                 idempotency_verified,control_totals_reconciled,
                 canonical_aggregate_rebuilt
               ) VALUES (
                 %s,%s,%s,%s,%s,%s,2,%s,3,%s,%s,%s,0,0,0,%s,%s,%s,%s,%s,%s
               )""",
            (
                run_id,
                started_at,
                started_at + timedelta(minutes=1) if status == "COMPLETED" else None,
                status,
                date(2024, 11, 28),
                date(2026, 8, 10),
                2 if reviewable else 0,
                3 if reviewable else 0,
                10 if reviewable else 0,
                10 if reviewable else 0,
                reviewable,
                reviewable,
                reviewable,
                reviewable,
                reviewable,
                reviewable,
            ),
        )
        return run_id

    def persisted_status_snapshot(self) -> tuple[str | None, str | None, str | None]:
        with self.conn.cursor() as cur:
            values = []
            for table, key in (
                ("catalog_sync_runs", "catalog_sync_id"),
                ("sales_backfill_runs", "sales_backfill_id"),
                ("readiness_gates", "gate_id"),
            ):
                cur.execute(
                    f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY {key})::text, '[]') FROM {table} t"
                )
                values.append(cur.fetchone()[0])
        return tuple(values)

    def test_canonical_gate_run_and_authoritative_catalog_attempt_win_without_writes(self):
        self.insert_catalog_run(started_at=self.base_time, status="COMPLETED")
        newest_catalog = self.insert_catalog_run(
            started_at=self.base_time + timedelta(hours=1), status="FAILED"
        )
        gate_sales = self.insert_sales_run(
            started_at=self.base_time, status="COMPLETED", reviewable=True
        )
        self.insert_sales_run(
            started_at=self.base_time + timedelta(hours=2),
            status="FAILED",
            reviewable=False,
        )
        self.conn.execute(
            """UPDATE readiness_gates
               SET status='PASS', evidence_json=%s::jsonb
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''""",
            (json.dumps({"sales_backfill_id": gate_sales}),),
        )
        self.conn.commit()

        before = self.persisted_status_snapshot()
        result = health.data_sync_run_status(self.conn)
        after = self.persisted_status_snapshot()

        self.assertEqual(before, after)
        self.assertEqual(result["catalog"]["run"]["catalog_sync_id"], newest_catalog)
        self.assertEqual(result["catalog"]["readiness"]["status"], "FAIL")
        self.assertEqual(result["historical_sales"]["run"]["sales_backfill_id"], gate_sales)
        self.assertEqual(
            result["historical_sales"]["selection"], "CANONICAL_READINESS_GATE"
        )

    def test_reviewable_domain_run_wins_over_a_newer_failed_attempt(self):
        self.insert_catalog_run(started_at=self.base_time, status="COMPLETED")
        reviewable = self.insert_sales_run(
            started_at=self.base_time, status="COMPLETED", reviewable=True
        )
        self.insert_sales_run(
            started_at=self.base_time + timedelta(hours=1),
            status="FAILED",
            reviewable=False,
        )
        self.conn.execute(
            """UPDATE readiness_gates SET evidence_json='{}'::jsonb
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.commit()
        result = health.data_sync_run_status(self.conn)
        self.assertEqual(result["historical_sales"]["run"]["sales_backfill_id"], reviewable)
        self.assertEqual(result["historical_sales"]["selection"], "LATEST_REVIEWABLE_RUN")

    def test_newest_attempt_is_only_a_labeled_diagnostic_fallback(self):
        self.insert_catalog_run(started_at=self.base_time, status="COMPLETED")
        self.insert_sales_run(
            started_at=self.base_time, status="FAILED", reviewable=False
        )
        newest = self.insert_sales_run(
            started_at=self.base_time + timedelta(hours=1),
            status="RUNNING",
            reviewable=False,
        )
        self.conn.execute(
            """UPDATE readiness_gates SET evidence_json='{}'::jsonb
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.commit()
        result = health.data_sync_run_status(self.conn)
        self.assertEqual(result["historical_sales"]["run"]["sales_backfill_id"], newest)
        self.assertEqual(
            result["historical_sales"]["selection"], "LATEST_ATTEMPT_DIAGNOSTIC"
        )


if __name__ == "__main__":
    unittest.main()
