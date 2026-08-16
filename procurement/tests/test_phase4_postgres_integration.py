"""Transactional PostgreSQL integration coverage for the Phase 4 workflow.

The test creates an isolated schema and rolls the entire schema/data lifecycle
back. It never calls Shopify and never exposes DATABASE_URL.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import sys
import unittest
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from procurement_os.historical_sales import (
    AUTHORITATIVE_START_DATE,
    ControlTotals,
    _chunk_rows,
    _complete_chunk_control,
    _mark_page_running,
    _persist_page,
    _record_page_failure,
    create_sales_backfill_run,
    current_store_date,
    finalize_sales_backfill,
    get_historical_sales_review_items,
    prepare_resume_run,
    query_contract_hash,
    record_historical_sales_review_decision,
    source_identity_key,
)
from procurement_os.sales import (
    SalesSourceRow,
    load_identity_index,
    search_historical_sales_catalog,
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
)


@unittest.skipUnless(os.getenv("DATABASE_URL"), "PostgreSQL integration requires DATABASE_URL")
class Phase4PostgresIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg import sql

        self.conn = psycopg.connect(os.environ["DATABASE_URL"])
        self.schema = f"phase4_test_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for name in MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
        self.conn.execute(
            """UPDATE readiness_gates SET status='PASS',checked_at=now()
               WHERE gate_name='CATALOG_SYNC' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.execute(
            """INSERT INTO catalog_sync_runs(
                 status,completed_at,shopify_api_version,
                 shopify_reported_variant_count,live_rows_received,
                 exact_current_ids,new_live_variants,source_hash,pagination_complete
               ) VALUES ('COMPLETED',now(),'2026-07',3,3,3,0,
                         'phase4-integration-catalog',TRUE)"""
        )
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,sku,active,catalog_state
               ) VALUES
                 ('100','10','Live A','750ML','DUP',TRUE,'LIVE'),
                 ('200','20','Live B','1L','DUP',TRUE,'LIVE'),
                 ('300','30','Retired C','750ML','OLD-C',FALSE,'RETIRED_CONFIRMED')"""
        )
        self.end_date = current_store_date("America/New_York")

    def tearDown(self) -> None:
        self.conn.rollback()
        self.conn.close()

    @staticmethod
    def row(
        variant_id: str | None,
        sku: str | None,
        product: str,
        variant: str,
        units: str,
        sales: str,
    ) -> SalesSourceRow:
        return SalesSourceRow(
            AUTHORITATIVE_START_DATE, variant_id, sku, product, variant,
            Decimal(units), Decimal(sales),
        )

    def persist_complete_run(self, rows: list[SalesSourceRow]) -> tuple[str, dict]:
        totals = ControlTotals(
            sum((row.net_items_sold for row in rows), Decimal("0")),
            sum((row.net_sales or Decimal("0") for row in rows), Decimal("0")),
        )
        run_id = create_sales_backfill_run(
            self.conn, start_date=AUTHORITATIVE_START_DATE, end_date=self.end_date,
            store_timezone="America/New_York", chunk_days=10000, page_size=1000,
        )
        chunk = _chunk_rows(self.conn, run_id)[0]
        page_id, _ = _mark_page_running(
            self.conn, chunk_id=str(chunk[0]), page_index=0, page_size=1000,
            chunk_start=chunk[2], chunk_end=chunk[3], contract_hash=query_contract_hash(),
        )
        _persist_page(
            self.conn, run_id=run_id, chunk_id=str(chunk[0]), page_id=page_id,
            rows=rows, identity=load_identity_index(self.conn), terminal=True,
        )
        _complete_chunk_control(
            self.conn, run_id=run_id, chunk_id=str(chunk[0]), totals=totals,
        )
        return run_id, finalize_sales_backfill(
            self.conn, run_id=run_id, independent_totals=totals,
        )

    def business_state_hash(self) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT md5(COALESCE(string_agg(payload,'|' ORDER BY payload),''))
                   FROM (
                     SELECT 'variants:' || to_jsonb(v)::text AS payload FROM variants v
                     UNION ALL
                     SELECT 'aliases:' || to_jsonb(a)::text FROM variant_aliases a
                     UNION ALL
                     SELECT 'exclusions:' || to_jsonb(e)::text FROM historical_sales_exclusions e
                     UNION ALL
                     SELECT 'decisions:' || to_jsonb(d)::text FROM historical_sales_review_decisions d
                     UNION ALL
                     SELECT 'readiness:' || to_jsonb(g)::text FROM readiness_gates g
                     UNION ALL
                     SELECT 'changes:' || to_jsonb(c)::text FROM change_log c
                     UNION ALL
                     SELECT 'sales:' || to_jsonb(s)::text FROM sales_daily s
                     UNION ALL
                     SELECT 'runs:' || to_jsonb(r)::text FROM sales_backfill_runs r
                   ) snapshot"""
            )
            return str(cur.fetchone()[0])

    def test_local_catalog_search_is_bounded_literal_deterministic_and_read_only(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,handle,
                     sku,barcode,active,catalog_state
                   ) VALUES
                     ('401','41','O''Brien 100% Reserve','Special_750ML','obrien-reserve',
                      'SKU_SPECIAL','BAR%401',TRUE,'LIVE'),
                     ('402','42','O Brien 100X Reserve','Standard 750ML','other-reserve',
                      'SKUXSPECIAL','BARX401',TRUE,'LIVE'),
                     ('500','50','500 Prefix Product','750ML','exact-id',
                      'OTHER-500','BAR-500',TRUE,'LIVE'),
                     ('501','51','Exact SKU Product','750ML','exact-sku',
                      '500','BAR-501',TRUE,'LIVE'),
                     ('502','52','Contains 500 Product','750ML','contains-500',
                      'OTHER-502','BAR-502',TRUE,'LIVE')"""
            )
            cur.executemany(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,handle,
                     sku,barcode,active,catalog_state
                   ) VALUES (%s,%s,'Limit Bottle','750ML',%s,%s,%s,TRUE,'LIVE')""",
                [
                    (str(600 + index), str(60 + index), f"limit-{index}",
                     f"LIMIT-{index}", f"LIMIT-BAR-{index}")
                    for index in range(25)
                ],
            )

        before = self.business_state_hash()
        self.assertEqual(search_historical_sales_catalog(self.conn, "   "), [])
        with self.assertRaisesRegex(ValueError, "128 characters or fewer"):
            search_historical_sales_catalog(self.conn, "x" * 129)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            search_historical_sales_catalog(self.conn, "Bottle", limit=0)

        self.assertEqual(
            [row["variant_id"] for row in search_historical_sales_catalog(self.conn, "500")],
            ["500", "501", "502"],
        )
        self.assertEqual(
            [row["variant_id"] for row in search_historical_sales_catalog(self.conn, "SKU_SPECIAL")],
            ["401"],
        )
        self.assertEqual(
            [row["variant_id"] for row in search_historical_sales_catalog(self.conn, "BAR%401")],
            ["401"],
        )
        self.assertEqual(
            [row["variant_id"] for row in search_historical_sales_catalog(self.conn, "100%")],
            ["401"],
        )
        self.assertEqual(
            [row["variant_id"] for row in search_historical_sales_catalog(self.conn, "Special_750")],
            ["401"],
        )
        self.assertEqual(
            [row["variant_id"] for row in search_historical_sales_catalog(self.conn, "O'Brien")],
            ["401"],
        )
        self.assertEqual(
            [row["variant_id"] for row in search_historical_sales_catalog(self.conn, "obrien-reserve")],
            ["401"],
        )
        self.assertEqual(
            search_historical_sales_catalog(self.conn, "'; DELETE FROM variants; --"),
            [],
        )
        self.assertEqual(search_historical_sales_catalog(self.conn, "no such bottle"), [])

        limited = search_historical_sales_catalog(self.conn, "Limit Bottle", limit=100)
        self.assertEqual(len(limited), 20)
        self.assertEqual(
            [row["variant_id"] for row in limited],
            [str(value) for value in range(600, 620)],
        )

        retired = search_historical_sales_catalog(self.conn, "Retired C")
        self.assertEqual(
            retired,
            [{
                "variant_id": "300",
                "product_title": "Retired C",
                "variant_title": "750ML",
                "sku": "OLD-C",
                "barcode": None,
                "active": False,
                "catalog_state": "RETIRED_CONFIRMED",
            }],
        )
        self.assertEqual(self.business_state_hash(), before)

    def test_unknown_mapping_target_is_rejected_without_partial_persistence(self):
        unresolved = self.row(
            "0", "UNKNOWN-TARGET", "Unknown Target History", "750ML", "2", "20.00"
        )
        run_id, initial = self.persist_complete_run([unresolved])
        self.assertEqual(initial["status"], "FAIL")
        unknown_variant_id = "999999999999999"

        def guarded_state():
            with self.conn.cursor() as cur:
                cur.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM variant_aliases),
                         (SELECT COUNT(*) FROM historical_sales_review_decisions),
                         (SELECT COUNT(*) FROM change_log),
                         (SELECT COUNT(*) FROM historical_sales_exclusions),
                         (SELECT COUNT(*) FROM variants WHERE variant_id=%s)""",
                    (unknown_variant_id,),
                )
                mutation_counts = cur.fetchone()
                cur.execute(
                    """SELECT status,blocks_po,evidence_json::text,message,checked_at
                       FROM readiness_gates
                       WHERE gate_name='SALES_BACKFILL'
                         AND scope_type='GLOBAL' AND scope_id=''"""
                )
                readiness = cur.fetchone()
                cur.execute(
                    """SELECT COUNT(*),COALESCE(SUM(units_sold),0),
                              COALESCE(SUM(net_sales),0)
                       FROM sales_daily"""
                )
                sales_aggregate = cur.fetchone()
                cur.execute(
                    """SELECT status,completed_at,resolved_rows,unresolved_rows,
                              ambiguous_rows,canonical_net_items_sold,
                              canonical_net_sales
                       FROM sales_backfill_runs WHERE sales_backfill_id=%s""",
                    (run_id,),
                )
                run_state = cur.fetchone()
            return mutation_counts, readiness, sales_aggregate, run_state

        state_before = guarded_state()
        hash_before = self.business_state_hash()
        with self.assertRaisesRegex(ValueError, "^unknown canonical Variant ID$"):
            record_historical_sales_review_decision(
                self.conn,
                source_key=source_identity_key(unresolved),
                action="MAP_TO_CANONICAL",
                canonical_variant_id=unknown_variant_id,
                actor="phase4-test",
                reason="synthetic unknown-target rejection",
            )

        self.assertEqual(guarded_state(), state_before)
        self.assertEqual(self.business_state_hash(), hash_before)
        self.assertEqual(state_before[0], (0, 0, 0, 0, 0))

    def test_durable_ingest_review_rebuild_restatement_and_interruption(self):
        active = self.row("100", "DUP", "Live A", "750ML", "5", "50.00")
        ambiguous = self.row(None, "DUP", "Historical Unknown", "750ML", "2", "20.00")
        unresolved = self.row("0", "UNKNOWN", "No Candidate", "500ML", "3", "30.00")
        returned = self.row("300", "OLD-C", "Retired C", "750ML", "-1", "-10.00")

        run_id, initial = self.persist_complete_run([active, ambiguous, unresolved, returned])
        self.assertEqual(initial["status"], "FAIL")
        self.assertEqual((initial["resolved_rows"], initial["ambiguous_rows"], initial["unresolved_rows"]), (2, 1, 1))
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(units_sold),0),COALESCE(SUM(net_sales),0) FROM sales_daily"
            )
            self.assertEqual(cur.fetchone(), (Decimal("4.0000"), Decimal("40.00")))
            # A newer failed/incomplete run must never shadow the trusted review
            # queue or accept permanent owner decisions against unverified facts.
            cur.execute(
                """INSERT INTO sales_backfill_runs(
                     start_date,end_date,status,unique_source_facts,
                     coverage_complete,pages_complete,started_at
                   ) VALUES (%s,%s,'FAILED',1,TRUE,TRUE,now() + interval '1 day')""",
                (AUTHORITATIVE_START_DATE, self.end_date),
            )

        queue = get_historical_sales_review_items(self.conn)
        self.assertEqual(len(queue), 2)
        by_key = {item["source_identity_key"]: item for item in queue}
        self.assertIn(source_identity_key(ambiguous), by_key)
        self.assertIn(source_identity_key(unresolved), by_key)

        mapped = record_historical_sales_review_decision(
            self.conn, source_key=source_identity_key(ambiguous), action="MAP_TO_CANONICAL",
            canonical_variant_id="100", actor="phase4-test", reason="owner-approved exact test map",
        )
        self.assertEqual(mapped["readiness"]["status"], "FAIL")
        excluded = record_historical_sales_review_decision(
            self.conn, source_key=source_identity_key(unresolved), action="EXCLUDE_HISTORICAL_ITEM",
            canonical_variant_id=None, actor="phase4-test", reason="owner-approved test exclusion",
        )
        self.assertEqual(excluded["readiness"]["status"], "PASS")
        self.assertEqual(excluded["readiness"]["excluded_rows"], 1)
        self.assertEqual(excluded["readiness"]["excluded_net_items_sold"], "3.0000")
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM historical_sales_review_decisions")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute("SELECT COUNT(*) FROM change_log WHERE actor='phase4-test'")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute(
                "SELECT COALESCE(SUM(units_sold),0),COALESCE(SUM(net_sales),0) FROM sales_daily"
            )
            self.assertEqual(cur.fetchone(), (Decimal("6.0000"), Decimal("60.00")))

        restated_active = self.row("100", "DUP", "Live A", "750ML", "6", "60.00")
        rerun_id, rerun = self.persist_complete_run(
            [restated_active, ambiguous, unresolved, returned]
        )
        self.assertEqual(rerun["status"], "PASS")
        self.assertEqual(rerun["unique_source_facts"], 4)
        self.assertEqual(rerun["duplicate_observations"], 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM shopify_sales_daily_raw")
            self.assertEqual(cur.fetchone()[0], 4)
            cur.execute(
                """SELECT restatement_detected,observation_count
                   FROM sales_backfill_run_facts rf
                   JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
                   WHERE rf.sales_backfill_id=%s AND r.source_variant_id='100'""",
                (rerun_id,),
            )
            self.assertEqual(cur.fetchone(), (True, 1))

        interrupted_id = create_sales_backfill_run(
            self.conn, start_date=AUTHORITATIVE_START_DATE, end_date=self.end_date,
            store_timezone="America/New_York", chunk_days=10000, page_size=1,
        )
        interrupted_chunk = _chunk_rows(self.conn, interrupted_id)[0]
        first_page, _ = _mark_page_running(
            self.conn, chunk_id=str(interrupted_chunk[0]), page_index=0, page_size=1,
            chunk_start=interrupted_chunk[2], chunk_end=interrupted_chunk[3],
            contract_hash=query_contract_hash(),
        )
        _persist_page(
            self.conn, run_id=interrupted_id, chunk_id=str(interrupted_chunk[0]),
            page_id=first_page, rows=[restated_active], identity=load_identity_index(self.conn),
            terminal=False,
        )
        failed_page, _ = _mark_page_running(
            self.conn, chunk_id=str(interrupted_chunk[0]), page_index=1, page_size=1,
            chunk_start=interrupted_chunk[2], chunk_end=interrupted_chunk[3],
            contract_hash=query_contract_hash(),
        )
        _record_page_failure(
            self.conn, run_id=interrupted_id, chunk_id=str(interrupted_chunk[0]),
            page_id=failed_page, exc=RuntimeError("synthetic parse interruption"),
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT status,completed_pages FROM sales_backfill_runs WHERE sales_backfill_id=%s",
                (interrupted_id,),
            )
            self.assertEqual(cur.fetchone(), ("PARTIAL", 1))
            cur.execute(
                "SELECT COUNT(*) FROM sales_backfill_run_facts WHERE sales_backfill_id=%s",
                (interrupted_id,),
            )
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute(
                "SELECT status FROM readiness_gates WHERE gate_name='SALES_BACKFILL'"
            )
            self.assertEqual(cur.fetchone()[0], "FAIL")

    def test_resume_uses_durable_range_after_store_date_rollover(self):
        run_id = create_sales_backfill_run(
            self.conn,
            start_date=AUTHORITATIVE_START_DATE,
            end_date=AUTHORITATIVE_START_DATE,
            store_timezone="America/New_York",
            chunk_days=31,
            page_size=1000,
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE sales_backfill_runs
                   SET started_at=TIMESTAMPTZ '2024-11-28 12:00:00-05'
                   WHERE sales_backfill_id=%s""",
                (run_id,),
            )
        settings = prepare_resume_run(
            self.conn, run_id, start_date=AUTHORITATIVE_START_DATE, end_date=None,
        )
        self.assertEqual(settings["end_date"], AUTHORITATIVE_START_DATE)
        self.assertEqual(settings["store_timezone"], "America/New_York")

    def test_normalization_equivalent_conflicting_alias_rolls_mapping_back(self):
        source = self.row(None, "HIST", "Alias & Name", "750ML", "2", "20.00")
        run_id, initial = self.persist_complete_run([source])
        self.assertEqual(initial["status"], "FAIL")
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO variant_aliases(
                     variant_id,historical_sku,historical_product_title,
                     historical_variant_title,match_method,source,approved,
                     approved_by,approved_at
                   ) VALUES ('200',' hist ','Alias and Name','750ML',
                             'HUMAN_TEST','PREEXISTING_TEST',TRUE,'phase4-test',now())"""
            )
        with self.assertRaisesRegex(ValueError, "did not resolve uniquely"):
            record_historical_sales_review_decision(
                self.conn,
                source_key=source_identity_key(source),
                action="MAP_TO_CANONICAL",
                canonical_variant_id="100",
                actor="phase4-test",
                reason="synthetic conflicting normalized alias",
            )
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FROM variant_aliases
                   WHERE source='SALES_BACKFILL_REVIEW' AND variant_id='100'"""
            )
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute(
                """SELECT COUNT(*) FROM historical_sales_review_decisions
                   WHERE sales_backfill_id=%s""",
                (run_id,),
            )
            self.assertEqual(cur.fetchone()[0], 0)

    def test_distinct_zero_id_groups_can_map_to_distinct_canonical_variants(self):
        first = self.row("0", "ZERO-A", "Historical Zero A", "750ML", "2", "20.00")
        second = self.row("0", "ZERO-B", "Historical Zero B", "1L", "3", "30.00")
        _, initial = self.persist_complete_run([first, second])
        self.assertEqual(initial["unresolved_rows"], 2)

        first_result = record_historical_sales_review_decision(
            self.conn,
            source_key=source_identity_key(first),
            action="MAP_TO_CANONICAL",
            canonical_variant_id="100",
            actor="phase4-test",
            reason="owner-approved first zero-ID identity",
        )
        self.assertEqual(first_result["readiness"]["status"], "FAIL")
        second_result = record_historical_sales_review_decision(
            self.conn,
            source_key=source_identity_key(second),
            action="MAP_TO_CANONICAL",
            canonical_variant_id="200",
            actor="phase4-test",
            reason="owner-approved second zero-ID identity",
        )
        self.assertEqual(second_result["readiness"]["status"], "PASS")
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT variant_id,old_variant_id,historical_sku
                   FROM variant_aliases WHERE source='SALES_BACKFILL_REVIEW'
                   ORDER BY historical_sku"""
            )
            self.assertEqual(
                cur.fetchall(),
                [("100", None, "ZERO-A"), ("200", None, "ZERO-B")],
            )


if __name__ == "__main__":
    unittest.main()
