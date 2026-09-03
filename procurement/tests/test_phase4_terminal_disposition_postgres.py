"""Disposable-PostgreSQL controls for Phase 4 terminal disposition."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.historical_sales import (
    _re_resolve_run_facts,
    acquire_backfill_transaction_lock,
    derive_exclusion_integrity,
    derive_phase4_exclusion_integrity,
    record_historical_sales_review_decision,
)
from procurement_os.catalog import (
    HistoricalOnlyVariantReappearanceError,
    LiveVariant,
    persist_catalog_sync,
    reconcile_catalog,
)
from procurement_os.historical_sales_manifest import (
    APPROVED_RUN_ID,
    ManifestExecutionContext,
    persist_manifest_decisions,
)
from procurement_os.historical_sales_terminal import (
    AUTHORITY_GIT_SHA,
    ExecutionGitIdentity,
    TERMINAL_AUTHORITY_VERSION,
    TerminalExecutionContext,
    _terminal_safe_alias_families,
    dry_run_terminal_disposition,
    inspect_terminal_state,
    load_terminal_artifact,
    persist_terminal_disposition,
)
from procurement_os.sales import (
    HistoricalIdentityIndex,
    SalesSourceRow,
    load_identity_index,
)


DB_DIR = PROCUREMENT_ROOT / "db"
ORIGINAL_MANIFEST_PATH = (
    PROCUREMENT_ROOT / "review" / "phase4_identity_manifest_corrected.csv"
)
TERMINAL_MANIFEST_PATH = (
    PROCUREMENT_ROOT / "review" / "phase4_terminal_disposition_manifest.csv"
)
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

TERMINAL_SHA = "fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff"
ORIGINAL_SHA = "95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287"
AUTHORITY_SHA = "701548dfacbc35d505f1d726146c268d6e42260d"
EXECUTION_SHA = "a" * 40
OWNER_AUTHORIZATION = "OWNER_AUTHORIZATION_2026-08-21_PHASE4_TERMINAL_PACKET"
EVIDENCE_VERSION = "phase4-terminal-disposition-evidence-v1"


@unittest.skipUnless(os.getenv("DATABASE_URL"), "PostgreSQL integration requires DATABASE_URL")
class Phase4TerminalDispositionPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg import sql

        self.conn = psycopg.connect(os.environ["DATABASE_URL"])
        self.schema = f"phase4_terminal_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for name in MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
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

    def assert_rejected(self, statement: str, parameters: tuple = ()) -> None:
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(statement, parameters)

    def insert_historical_variant(self, variant_id: str = "900") -> None:
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,sku,active,
                 catalog_state,identity_scope,
                 restoration_manifest_sha256,restoration_manifest_row_number,
                 restoration_evidence_version,restoration_owner_authorization,
                 restoration_authority_git_sha,restoration_execution_git_sha
               ) VALUES (%s,NULL,'Historical Bottle','750ML','HIST-900',FALSE,
                         'RETIRED_CONFIRMED','HISTORICAL_ONLY',%s,1,%s,%s,%s,%s)""",
            (
                variant_id,
                TERMINAL_SHA,
                EVIDENCE_VERSION,
                OWNER_AUTHORIZATION,
                AUTHORITY_SHA,
                EXECUTION_SHA,
            ),
        )

    def insert_current_variant(self, variant_id: str = "100") -> None:
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,sku,active,catalog_state
               ) VALUES (%s,%s,'Current Bottle','750ML','CUR-100',TRUE,'LIVE')""",
            (variant_id, f"product-{variant_id}"),
        )

    def insert_vendor(self) -> str:
        vendor_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO vendors(vendor_id,vendor_name) VALUES (%s,%s)",
            (vendor_id, f"Vendor {vendor_id}"),
        )
        return vendor_id

    def insert_run(self, run_type: str = "TEST") -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO runs(run_id,run_type,status) VALUES (%s,%s,'RUNNING')",
            (run_id, run_type),
        )
        return run_id

    @staticmethod
    def source_parts(source_identity_key: str) -> tuple[str | None, str | None]:
        variant_id, sku, _, _ = source_identity_key.split("|", 3)
        return variant_id or None, sku or None

    @staticmethod
    def signed_parts(net: Decimal, absolute: Decimal) -> tuple[Decimal, Decimal]:
        return (absolute + net) / 2, (absolute - net) / 2

    def seed_preterminal_state(self):
        artifact = load_terminal_artifact(
            TERMINAL_MANIFEST_PATH, ORIGINAL_MANIFEST_PATH
        )
        original = artifact.original_manifest
        terminal_by_key = {row.source_identity_key: row for row in artifact.rows}
        restore_ids = {
            row.source_variant_id for row in artifact.rows if row.action == "RESTORE"
        }
        target_ids = {
            row.canonical_variant_id
            for row in original.rows
            if row.review_disposition == "MAP"
        } | {
            row.canonical_variant_id for row in artifact.rows if row.action == "MAP"
        }
        target_ids.discard(None)
        target_ids -= restore_ids
        pre_aliases = _terminal_safe_alias_families(artifact, terminal=False)
        current_aliases = _terminal_safe_alias_families(artifact, terminal=True)
        terminal_aliases = {
            old_id: target
            for old_id, target in current_aliases.items()
            if old_id not in pre_aliases
        }
        baseline_family_aliases = {
            old_id: target
            for old_id, target in terminal_aliases.items()
            if target not in restore_ids
        }
        baseline_family_count = len(baseline_family_aliases)
        generic_historical_count = 136 - baseline_family_count
        generic_unique_count = 36392 + 19430 + generic_historical_count + 8
        generic_total_count = generic_unique_count + 5
        historical_end = 55822 + generic_historical_count
        preserved_start = historical_end
        self.assertEqual(generic_total_count + baseline_family_count, 55971)
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,sku,active,catalog_state
                   ) VALUES (%s,%s,%s,'750ML',%s,TRUE,'LIVE')""",
                [
                    (
                        target,
                        f"product-{target}",
                        f"Canonical {target}",
                        f"TARGET-{target}",
                    )
                    for target in sorted(target_ids)
                ],
            )
            cur.execute(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,sku,active,catalog_state
                   )
                   SELECT 'BASE-' || lpad(g::text,3,'0'),
                          'product-base-' || lpad(g::text,3,'0'),
                          'Baseline Product ' || lpad(g::text,3,'0'),
                          '750ML','BASE-SKU-' || lpad(g::text,3,'0'),TRUE,'LIVE'
                   FROM generate_series(0,99) AS g"""
            )
            cur.execute(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,sku,active,catalog_state
                   )
                   SELECT 'BASE-RET-' || g::text,'product-base-ret-' || g::text,
                          'Preserved Product ' || g::text,'750ML',
                          'PRESERVED-SKU-' || g::text,FALSE,'RETIRED_CONFIRMED'
                   FROM generate_series(0,7) AS g"""
            )
            cur.execute(
                """INSERT INTO variant_aliases(
                     variant_id,old_variant_id,historical_product_title,
                     historical_variant_title,historical_sku,match_method,
                     confidence,source,approved,approved_by,approved_at,evidence_json
                   )
                   SELECT 'BASE-' || lpad(g::text,3,'0'),
                          (70000000000000 + g)::text,
                          'Alias Product ' || lpad(g::text,3,'0'),'750ML',
                          'ALIAS-SKU-' || lpad(g::text,3,'0'),
                          'BASELINE_APPROVED_OLD_ID',1.0,'PRODUCTION_SHAPED_FIXTURE',
                          TRUE,'fixture',now(),'{}'::jsonb
                   FROM generate_series(0,99) AS g"""
            )
            cur.execute(
                """INSERT INTO variant_aliases(
                     variant_id,old_variant_id,historical_product_title,
                     historical_variant_title,historical_sku,match_method,
                     confidence,source,approved,approved_by,approved_at,evidence_json
                   )
                   SELECT 'BASE-' || lpad(((55822+g) %% 100)::text,3,'0'),NULL,
                          'Historical Product ' || lpad(((55822+g) %% 100)::text,3,'0'),
                          '750ML','HIST-SKU-' || lpad(((55822+g) %% 100)::text,3,'0'),
                          'BASELINE_APPROVED_IDENTITY',1.0,'PRODUCTION_SHAPED_FIXTURE',
                          TRUE,'fixture',now(),'{}'::jsonb
                   FROM generate_series(0,%s) AS g""",
                (generic_historical_count - 1,),
            )
        self.conn.execute(
            """INSERT INTO sales_backfill_runs(
                 sales_backfill_id,started_at,completed_at,status,start_date,end_date,
                 source,query_version,raw_rows,resolved_rows,unresolved_rows,
                 ambiguous_rows,unique_source_facts,expected_chunks,completed_chunks,
                 expected_pages,completed_pages,source_rows,coverage_complete,pages_complete,
                 source_facts_persisted,idempotency_verified,control_totals_reconciled,
                 canonical_aggregate_rebuilt,store_timezone
               ) VALUES (
                 %s,TIMESTAMPTZ '2026-08-10 16:44:26+00',
                 TIMESTAMPTZ '2026-08-10 16:45:59+00','COMPLETED',
                 DATE '2024-11-28',DATE '2026-08-10','SHOPIFYQL_SALES','SHOPIFYQL_SALES_V2',
                 59083,55971,3112,0,59083,21,21,70,70,59083,
                 TRUE,TRUE,TRUE,TRUE,TRUE,TRUE,'America/New_York'
               )""",
            (APPROVED_RUN_ID,),
        )
        self.conn.execute(
            """UPDATE readiness_gates SET status='FAIL',
                 evidence_json='{"stage":"OWNER_REVIEW"}'::jsonb,
                 message='Owner terminal decisions not yet persisted.'
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
        )

        # Production-shaped prior resolved population.  Generic facts contain
        # five intentional aggregation collisions.  Twenty additional facts
        # exercise the proposed broad old-ID alias families whose targets exist
        # before terminal restoration; the remaining nineteen families target
        # identities restored by this package and therefore have no legitimate
        # pre-terminal resolved member.
        self.conn.execute(
            """WITH limits AS (
                 SELECT %s::int AS unique_count,%s::int AS historical_end,
                        %s::int AS preserved_start
               ), generated AS (
                 SELECT g,preserved_start,
                        CASE WHEN g<unique_count THEN g %% 100 ELSE g-unique_count END AS variant_n,
                        CASE WHEN g<unique_count THEN g / 100 ELSE 0 END AS day_n,
                        CASE
                          WHEN g>=unique_count OR g<36392 THEN 'EXACT'
                          WHEN g<55822 THEN 'OLD_ALIAS'
                          WHEN g<historical_end THEN 'HISTORICAL_IDENTITY'
                          ELSE 'PRESERVED'
                        END AS identity_class
                 FROM generate_series(0,%s) AS g CROSS JOIN limits
               )
               INSERT INTO shopify_sales_daily_raw(
                 raw_sales_id,sales_backfill_id,sale_date,source_variant_id,source_sku,
                 source_product_title,source_variant_title,net_items_sold,net_sales,
                 canonical_variant_id,resolution_status,resolution_method,
                 resolution_evidence,source_identity_key,source_row_hash
               )
               SELECT g+1,%s,DATE '2024-11-28' + day_n,
                      CASE identity_class
                        WHEN 'EXACT' THEN 'BASE-' || lpad(variant_n::text,3,'0')
                        WHEN 'OLD_ALIAS' THEN (70000000000000 + variant_n)::text
                        WHEN 'HISTORICAL_IDENTITY' THEN '0'
                        ELSE 'BASE-RET-' || (g-preserved_start)::text
                      END,
                      CASE identity_class
                        WHEN 'EXACT' THEN 'BASE-SKU-' || lpad(variant_n::text,3,'0')
                        WHEN 'OLD_ALIAS' THEN 'ALIAS-SKU-' || lpad(variant_n::text,3,'0')
                        WHEN 'HISTORICAL_IDENTITY' THEN 'HIST-SKU-' || lpad(variant_n::text,3,'0')
                        ELSE 'PRESERVED-SKU-' || (g-preserved_start)::text
                      END,
                      CASE identity_class
                        WHEN 'EXACT' THEN 'Baseline Product ' || lpad(variant_n::text,3,'0')
                        WHEN 'OLD_ALIAS' THEN 'Alias Product ' || lpad(variant_n::text,3,'0')
                        WHEN 'HISTORICAL_IDENTITY' THEN 'Historical Product ' || lpad(variant_n::text,3,'0')
                        ELSE 'Preserved Product ' || (g-preserved_start)::text
                      END,'750ML',
                      CASE g WHEN 0 THEN 78832.0000 WHEN 1 THEN -17.0000 ELSE 0 END,
                      CASE g WHEN 0 THEN 1231838.67 WHEN 1 THEN -465.84 ELSE 0 END,
                      CASE identity_class
                        WHEN 'PRESERVED' THEN 'BASE-RET-' || (g-preserved_start)::text
                        ELSE 'BASE-' || lpad(variant_n::text,3,'0')
                      END,'RESOLVED',
                      CASE identity_class
                        WHEN 'EXACT' THEN 'EXACT_ACTIVE_VARIANT_ID'
                        WHEN 'OLD_ALIAS' THEN 'APPROVED_VARIANT_ID_ALIAS'
                        WHEN 'HISTORICAL_IDENTITY' THEN 'APPROVED_HISTORICAL_IDENTITY'
                        ELSE 'EXACT_PRESERVED_HISTORICAL_VARIANT_ID'
                      END,'{}'::jsonb,
                      CASE identity_class
                        WHEN 'EXACT' THEN
                          'BASE-' || lpad(variant_n::text,3,'0') || '|BASE-SKU-' ||
                          lpad(variant_n::text,3,'0') || '|BASELINE PRODUCT ' ||
                          lpad(variant_n::text,3,'0') || '|750ML'
                        WHEN 'OLD_ALIAS' THEN
                          (70000000000000 + variant_n)::text || '|ALIAS-SKU-' ||
                          lpad(variant_n::text,3,'0') || '|ALIAS PRODUCT ' ||
                          lpad(variant_n::text,3,'0') || '|750ML'
                        WHEN 'HISTORICAL_IDENTITY' THEN
                          '0|HIST-SKU-' || lpad(variant_n::text,3,'0') ||
                          '|HISTORICAL PRODUCT ' || lpad(variant_n::text,3,'0') || '|750ML'
                        ELSE
                          'BASE-RET-' || (g-preserved_start)::text || '|PRESERVED-SKU-' ||
                          (g-preserved_start)::text || '|PRESERVED PRODUCT ' ||
                          (g-preserved_start)::text || '|750ML'
                      END,
                      md5('phase4-baseline:' || g::text)
               FROM generated""",
            (
                generic_unique_count,
                historical_end,
                preserved_start,
                generic_total_count - 1,
                APPROVED_RUN_ID,
            ),
        )

        # Allocate one deterministic target/date pair per fact that will resolve
        # after the terminal package.  Terminal evidence windows keep their exact
        # endpoints; interior dates are selected deterministically.  This leaves
        # the fixture's only sales_daily aggregation collisions in the five
        # deliberately duplicated baseline facts.
        resolved_dates: dict[str, list[date]] = {}
        used_target_dates: dict[str, set[date]] = {}
        terminal_resolved: list[tuple[object, str]] = []
        original_resolved: list[tuple[object, str]] = []
        for original_row in original.rows:
            terminal_row = terminal_by_key.get(original_row.source_identity_key)
            action = terminal_row.action if terminal_row else original_row.review_disposition
            if terminal_row and action == "RESTORE":
                target = terminal_row.source_variant_id
            else:
                target = (
                    terminal_row.canonical_variant_id
                    if terminal_row
                    else original_row.canonical_variant_id
                )
            if action not in {"MAP", "RESTORE"} or not target:
                continue
            if terminal_row:
                terminal_resolved.append((terminal_row, target))
                used_target_dates.setdefault(target, set()).update(
                    {terminal_row.first_sale_date, terminal_row.last_sale_date}
                )
            else:
                original_resolved.append((original_row, target))

        for row, target in sorted(
            terminal_resolved, key=lambda item: item[0].source_identity_key
        ):
            dates = [row.first_sale_date]
            if row.last_sale_date != row.first_sale_date:
                dates.append(row.last_sale_date)
            cursor_date = row.first_sale_date
            while len(dates) < row.raw_row_count:
                if (
                    cursor_date not in used_target_dates[target]
                    and cursor_date not in dates
                ):
                    dates.append(cursor_date)
                    used_target_dates[target].add(cursor_date)
                cursor_date += timedelta(days=1)
                if cursor_date > row.last_sale_date:
                    raise AssertionError(
                        f"insufficient unique evidence dates for {row.source_identity_key}"
                    )
            resolved_dates[row.source_identity_key] = sorted(dates)

        for row, target in sorted(
            original_resolved, key=lambda item: item[0].source_identity_key
        ):
            dates = []
            cursor_date = date(2024, 11, 28)
            while len(dates) < row.affected_raw_rows:
                if cursor_date not in used_target_dates.setdefault(target, set()):
                    dates.append(cursor_date)
                    used_target_dates[target].add(cursor_date)
                cursor_date += timedelta(days=1)
                if cursor_date > date(2026, 8, 10):
                    raise AssertionError(
                        f"insufficient unique run dates for {row.source_identity_key}"
                    )
            resolved_dates[row.source_identity_key] = dates

        family_rows: list[tuple[object, ...]] = []
        family_aliases: list[tuple[object, ...]] = []
        next_id = generic_total_count + 1
        for old_id, target in sorted(baseline_family_aliases.items()):
            occupied = used_target_dates.setdefault(target, set())
            family_date = date(2024, 11, 28)
            while family_date in occupied:
                family_date += timedelta(days=1)
            occupied.add(family_date)
            sku = f"BASE-FAMILY-{old_id}"
            title = f"Baseline Family {old_id}"
            source_key = HistoricalIdentityIndex.source_key(
                SalesSourceRow(
                    family_date, old_id, sku, title, "750ML",
                    Decimal("0"), Decimal("0"),
                )
            )
            source_hash = hashlib.sha256(
                f"phase4-baseline-family:{old_id}".encode("utf-8")
            ).hexdigest()
            family_rows.append(
                (
                    next_id, APPROVED_RUN_ID, family_date, old_id, sku, title,
                    "750ML", target, source_key, source_hash,
                )
            )
            family_aliases.append((target, title, "750ML", sku))
            next_id += 1
        self.assertEqual(len(family_rows), baseline_family_count)
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO shopify_sales_daily_raw(
                     raw_sales_id,sales_backfill_id,sale_date,source_variant_id,source_sku,
                     source_product_title,source_variant_title,net_items_sold,net_sales,
                     canonical_variant_id,resolution_status,resolution_method,
                     resolution_evidence,source_identity_key,source_row_hash
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,%s,'RESOLVED',
                             'APPROVED_HISTORICAL_IDENTITY','{}'::jsonb,%s,%s)""",
                family_rows,
            )
            cur.executemany(
                """INSERT INTO variant_aliases(
                     variant_id,old_variant_id,historical_product_title,
                     historical_variant_title,historical_sku,match_method,
                     confidence,source,approved,approved_by,approved_at,evidence_json
                   ) VALUES (%s,NULL,%s,%s,%s,'FULL_IDENTITY_FIXTURE',1.0,
                             'PRODUCTION_SHAPED_FIXTURE',TRUE,'fixture',now(),'{}'::jsonb)""",
                family_aliases,
            )
        self.conn.execute(
            """INSERT INTO sales_backfill_run_facts(
                 sales_backfill_id,raw_sales_id,source_row_hash,
                 first_observed_net_items_sold,first_observed_net_sales,
                 observed_net_items_sold,observed_net_sales
               )
               SELECT %s,raw_sales_id,source_row_hash,net_items_sold,net_sales,
                      net_items_sold,net_sales
               FROM shopify_sales_daily_raw WHERE raw_sales_id<=55971""",
            (APPROVED_RUN_ID,),
        )
        self.conn.execute(
            """INSERT INTO sales_daily(
                 sale_date,variant_id,units_sold,net_sales,distinct_orders,source
               )
               SELECT r.sale_date,r.canonical_variant_id,
                      SUM(rf.observed_net_items_sold),SUM(rf.observed_net_sales),
                      NULL,'SHOPIFYQL_SALES'
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE rf.sales_backfill_id=%s AND r.resolution_status='RESOLVED'
               GROUP BY r.sale_date,r.canonical_variant_id""",
            (APPROVED_RUN_ID,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM sales_daily WHERE source='SHOPIFYQL_SALES'"
            ).fetchone()[0],
            55966,
        )

        raw_rows: list[tuple[object, ...]] = []
        run_facts: list[tuple[object, ...]] = []
        raw_sales_id = 55972
        fallback_date = date(2024, 11, 28)
        for original_row in original.rows:
            terminal_row = terminal_by_key.get(original_row.source_identity_key)
            source_variant_id, source_sku = self.source_parts(
                original_row.source_identity_key
            )
            if terminal_row is None:
                row_count = original_row.affected_raw_rows
                first_date = fallback_date
                last_date = fallback_date + timedelta(days=max(row_count - 1, 0))
                net_units = absolute_units = original_row.absolute_unit_magnitude
                net_sales = absolute_sales = original_row.absolute_sales_magnitude
                if original_row.source_identity_key == "|||":
                    # Frozen original-exclusion control: $9.15 positive and
                    # $11.99 negative facts produce -$2.84 net / $21.14 abs.
                    net_sales = Decimal("-2.84")
                product_title = original_row.historical_product_title
                variant_title = original_row.historical_variant_title
            else:
                row_count = terminal_row.raw_row_count
                self.assertEqual(row_count, original_row.affected_raw_rows)
                first_date = terminal_row.first_sale_date
                last_date = terminal_row.last_sale_date
                net_units = terminal_row.net_units
                absolute_units = terminal_row.absolute_units
                net_sales = terminal_row.net_sales
                absolute_sales = terminal_row.absolute_sales
                product_title = terminal_row.historical_product_title
                variant_title = terminal_row.historical_variant_title
            positive_units, negative_units = self.signed_parts(
                net_units, absolute_units
            )
            positive_sales, negative_sales = self.signed_parts(
                net_sales, absolute_sales
            )
            if (negative_units or negative_sales) and row_count < 2:
                raise AssertionError("signed source controls require two raw rows")
            for offset in range(row_count):
                assigned_dates = resolved_dates.get(original_row.source_identity_key)
                if offset == 0:
                    units, sales = positive_units, positive_sales
                elif offset == 1:
                    units, sales = -negative_units, -negative_sales
                else:
                    units = sales = Decimal("0")
                if assigned_dates is not None:
                    sale_date = assigned_dates[offset]
                elif offset == 0:
                    sale_date = first_date
                elif offset == row_count - 1:
                    sale_date = last_date
                else:
                    sale_date = first_date
                source_hash = hashlib.sha256(
                    f"{original_row.source_identity_key}:{offset}".encode("utf-8")
                ).hexdigest()
                raw_rows.append(
                    (
                        raw_sales_id,
                        APPROVED_RUN_ID,
                        sale_date,
                        source_variant_id,
                        source_sku,
                        product_title or None,
                        variant_title or None,
                        units,
                        sales,
                        original_row.source_identity_key,
                        source_hash,
                    )
                )
                run_facts.append(
                    (
                        APPROVED_RUN_ID,
                        raw_sales_id,
                        source_hash,
                        units,
                        sales,
                        units,
                        sales,
                    )
                )
                raw_sales_id += 1
        self.assertEqual(len(raw_rows), 3112)
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO shopify_sales_daily_raw(
                     raw_sales_id,sales_backfill_id,sale_date,source_variant_id,source_sku,
                     source_product_title,source_variant_title,net_items_sold,net_sales,
                     source_identity_key,source_row_hash,resolution_status,resolution_evidence
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'UNRESOLVED','{}'::jsonb)""",
                raw_rows,
            )
            cur.executemany(
                """INSERT INTO sales_backfill_run_facts(
                     sales_backfill_id,raw_sales_id,source_row_hash,
                     first_observed_net_items_sold,first_observed_net_sales,
                     observed_net_items_sold,observed_net_sales
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                run_facts,
            )
        self.conn.commit()
        persist_manifest_decisions(
            self.conn,
            original,
            ManifestExecutionContext(
                actor="phase4-original-manifest-fixture",
                implementation_git_sha="b" * 40,
            ),
        )
        self.conn.commit()
        return artifact

    @staticmethod
    def terminal_context() -> TerminalExecutionContext:
        return TerminalExecutionContext(
            actor="phase4-terminal-test",
            expected_execution_git_sha=EXECUTION_SHA,
        )

    @staticmethod
    def execution_patch():
        return patch(
            "procurement_os.historical_sales_terminal.derive_runtime_execution_git_identity",
            return_value=ExecutionGitIdentity(Path("/fixture"), EXECUTION_SHA),
        )

    def apply_and_resolve_terminal_fixture(self):
        artifact = self.seed_preterminal_state()
        with self.execution_patch():
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())
        identity = load_identity_index(self.conn)
        _re_resolve_run_facts(self.conn, APPROVED_RUN_ID, identity)
        self.conn.commit()
        return artifact

    def test_migration_is_idempotent_and_constraints_are_named(self):
        self.conn.execute(
            (DB_DIR / "007_phase4_terminal_disposition.sql").read_text(
                encoding="utf-8"
            )
        )
        rows = self.conn.execute(
            """SELECT conname FROM pg_constraint
               WHERE conrelid='variants'::regclass
                 AND conname IN (
                   'ck_variants_identity_scope',
                   'ck_variants_identity_invariants',
                   'ck_variants_restoration_provenance'
                 ) ORDER BY conname"""
        ).fetchall()
        self.assertEqual(
            [row[0] for row in rows],
            [
                "ck_variants_identity_invariants",
                "ck_variants_identity_scope",
                "ck_variants_restoration_provenance",
            ],
        )
        definitions = self.conn.execute(
            """SELECT viewname,definition FROM pg_views
               WHERE schemaname=current_schema()
                 AND viewname IN ('v_current_prices','v_future_prices')
               ORDER BY viewname"""
        ).fetchall()
        self.assertEqual(len(definitions), 2)
        self.assertTrue(
            all("is_operational_current_variant" in row[1] for row in definitions)
        )
        self.insert_current_variant("postcondition-current")
        self.insert_historical_variant("postcondition-historical")
        self.assertTrue(
            self.conn.execute(
                "SELECT is_operational_current_variant('postcondition-current')"
            ).fetchone()[0]
        )
        self.assertFalse(
            self.conn.execute(
                "SELECT is_operational_current_variant('postcondition-historical')"
            ).fetchone()[0]
        )

    def test_product_id_preflight_fails_before_terminal_migration_mutation(self):
        for constraint in (
            "ck_variants_identity_invariants",
            "ck_variants_restoration_provenance",
            "ck_variants_identity_scope",
        ):
            self.conn.execute(f"ALTER TABLE variants DROP CONSTRAINT {constraint}")
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,active,catalog_state
               ) VALUES ('BAD-NULL',NULL,'Bad Null','750ML',TRUE,'LIVE'),
                        ('BAD-BLANK','   ','Bad Blank','750ML',TRUE,'LIVE')"""
        )
        self.conn.commit()
        with self.assertRaisesRegex(
            Exception,
            "CURRENT variants with missing Product ID count=2, sample=BAD-BLANK,BAD-NULL",
        ):
            with self.conn.transaction():
                self.conn.execute(
                    (DB_DIR / "007_phase4_terminal_disposition.sql").read_text(
                        encoding="utf-8"
                    )
                )
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM pg_constraint
                   WHERE conrelid='variants'::regclass
                     AND conname LIKE 'ck_variants_identity_%'"""
            ).fetchone()[0],
            0,
        )

    def test_schema_reapply_remains_filtered_when_007_fails(self):
        from procurement.tools import apply_schema as schema_tool

        for constraint in (
            "ck_variants_identity_invariants",
            "ck_variants_restoration_provenance",
            "ck_variants_identity_scope",
        ):
            self.conn.execute(f"ALTER TABLE variants DROP CONSTRAINT {constraint}")
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,active,catalog_state
               ) VALUES ('BAD-REAPPLY',NULL,'Bad Reapply','750ML',TRUE,'LIVE')"""
        )
        self.conn.commit()
        with patch.object(
            schema_tool,
            "MIGRATION_ORDER",
            ["schema_postgres.sql", "007_phase4_terminal_disposition.sql"],
        ):
            with self.assertRaisesRegex(Exception, "missing Product ID"):
                schema_tool.apply_schema_connection(self.conn, DB_DIR)
        definitions = self.conn.execute(
            """SELECT definition FROM pg_views
               WHERE schemaname=current_schema()
                 AND viewname IN ('v_current_prices','v_future_prices')"""
        ).fetchall()
        self.assertEqual(len(definitions), 2)
        self.assertTrue(
            all("is_operational_current_variant" in row[0] for row in definitions)
        )

    def test_historical_only_variant_invariants_and_current_product_requirement(self):
        self.insert_historical_variant()
        self.insert_current_variant()
        self.assert_rejected(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,active,catalog_state
               ) VALUES ('101',NULL,'Invalid Current','750ML',TRUE,'LIVE')"""
        )
        self.assert_rejected(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,active,catalog_state,
                 identity_scope,restoration_manifest_sha256,
                 restoration_manifest_row_number,restoration_evidence_version,
                 restoration_owner_authorization,restoration_authority_git_sha,
                 restoration_execution_git_sha
               ) VALUES ('901',NULL,'Invalid Historical','750ML',TRUE,
                         'RETIRED_CONFIRMED','HISTORICAL_ONLY',%s,1,%s,%s,%s,%s)""",
            (TERMINAL_SHA, EVIDENCE_VERSION, OWNER_AUTHORIZATION, AUTHORITY_SHA, EXECUTION_SHA),
        )
        self.assert_rejected(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,active,catalog_state,
                 identity_scope,restoration_manifest_sha256,
                 restoration_manifest_row_number,restoration_evidence_version,
                 restoration_owner_authorization,restoration_authority_git_sha,
                 restoration_execution_git_sha
               ) VALUES ('902',NULL,'Invalid Historical','750ML',FALSE,
                         'MISSING','HISTORICAL_ONLY',%s,1,%s,%s,%s,%s)""",
            (TERMINAL_SHA, EVIDENCE_VERSION, OWNER_AUTHORIZATION, AUTHORITY_SHA, EXECUTION_SHA),
        )
        self.assert_rejected(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,active,catalog_state,
                 identity_scope
               ) VALUES ('903',NULL,'Incomplete Historical','750ML',FALSE,
                         'RETIRED_CONFIRMED','HISTORICAL_ONLY')"""
        )

    def test_historical_only_allows_sales_and_archival_inventory_evidence(self):
        self.insert_historical_variant()
        run_id = self.insert_run()
        self.conn.execute(
            """INSERT INTO sales_daily(
                 sale_date,variant_id,units_sold,net_sales,source,run_id
               ) VALUES ('2025-01-01','900',1,10,'SHOPIFYQL',%s)""",
            (run_id,),
        )
        self.conn.execute(
            """INSERT INTO inventory_snapshots(
                 run_id,variant_id,available_quantity
               ) VALUES (%s,'900',0)""",
            (run_id,),
        )
        self.conn.execute(
            """INSERT INTO daily_inventory_snapshots(
                 snapshot_date,captured_at,variant_id,available_quantity,source
               ) VALUES ('2025-01-01',now(),'900',0,'ARCHIVAL_TEST')"""
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM sales_daily WHERE variant_id='900'"
            ).fetchone()[0],
            1,
        )

    def test_live_shopify_reappearance_is_classified_before_catalog_upsert(self):
        self.insert_historical_variant("900")
        live = LiveVariant(
            "900",
            "gid://shopify/ProductVariant/900",
            "product-900",
            "gid://shopify/Product/product-900",
            "Reappeared Bottle",
            "750ML",
            "reappeared-bottle",
            "ACTIVE",
            "HIST-900",
            None,
            Decimal("10.00"),
            Decimal("7.00"),
            Decimal("1"),
            "gid://shopify/InventoryItem/900",
            True,
            "Vendor",
            "Spirit",
            None,
            None,
            None,
        )
        reconciliation = reconcile_catalog([], [live])
        with self.assertRaises(HistoricalOnlyVariantReappearanceError) as ctx:
            persist_catalog_sync(
                self.conn,
                1,
                [live],
                reconciliation,
                api_version="2026-07",
            )
        self.assertEqual(ctx.exception.variant_ids, ("900",))
        row = self.conn.execute(
            "SELECT identity_scope,active,product_id FROM variants WHERE variant_id='900'"
        ).fetchone()
        self.assertEqual(row, ("HISTORICAL_ONLY", False, None))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM catalog_sync_runs").fetchone()[0],
            0,
        )

    def test_active_offer_price_and_supplier_mapping_are_blocked(self):
        self.insert_historical_variant()
        vendor_id = self.insert_vendor()
        self.assert_rejected(
            """INSERT INTO supplier_offers(
                 variant_id,vendor_id,supplier_sku,active
               ) VALUES ('900',%s,'ACTIVE-HIST',TRUE)""",
            (vendor_id,),
        )
        offer_id = self.conn.execute(
            """INSERT INTO supplier_offers(
                 variant_id,vendor_id,supplier_sku,active
               ) VALUES ('900',%s,'ARCHIVE-HIST',FALSE) RETURNING offer_id""",
            (vendor_id,),
        ).fetchone()[0]
        self.assert_rejected(
            """INSERT INTO prices(
                 offer_id,price_state,effective_month,level_type,unit_price,source_file
               ) VALUES (%s,'current','2026-09-01','BASE',10,'fixture')""",
            (offer_id,),
        )
        self.assert_rejected(
            """INSERT INTO supplier_aliases(
                 vendor_id,variant_id,supplier_text,normalized_supplier_text,
                 approved,match_method
               ) VALUES (%s,'900','Historical','HISTORICAL',TRUE,'OWNER')""",
            (vendor_id,),
        )
        self.conn.execute(
            """INSERT INTO supplier_aliases(
                 vendor_id,variant_id,supplier_text,normalized_supplier_text,
                 approved,match_method
               ) VALUES (%s,'900','Historical Archive','HISTORICAL ARCHIVE',FALSE,'ARCHIVE')""",
            (vendor_id,),
        )

    def test_overrides_policies_forecasts_recommendations_and_po_lines_are_blocked(self):
        self.insert_historical_variant()
        vendor_id = self.insert_vendor()
        run_id = self.insert_run("PROCUREMENT")
        self.assert_rejected(
            """INSERT INTO manual_overrides(
                 active,variant_id,override_type,note
               ) VALUES (TRUE,'900','DEMAND','blocked')"""
        )
        self.conn.execute(
            """INSERT INTO manual_overrides(
                 active,variant_id,override_type,note
               ) VALUES (FALSE,'900','ARCHIVE','allowed audit')"""
        )
        self.assert_rejected(
            """INSERT INTO variant_policies(
                 variant_id,policy_type,active
               ) VALUES ('900','REPLENISHMENT',TRUE)"""
        )
        self.assert_rejected(
            "INSERT INTO forecast_results(run_id,variant_id) VALUES (%s,'900')",
            (run_id,),
        )
        self.assert_rejected(
            """INSERT INTO procurement_recommendations(
                 run_id,variant_id,vendor_id
               ) VALUES (%s,'900',%s)""",
            (run_id, vendor_id),
        )
        po_id = self.conn.execute(
            """INSERT INTO purchase_orders(run_id,vendor_id)
               VALUES (%s,%s) RETURNING po_id""",
            (run_id, vendor_id),
        ).fetchone()[0]
        self.assert_rejected(
            "INSERT INTO purchase_order_lines(po_id,variant_id) VALUES (%s,'900')",
            (po_id,),
        )

    def test_operational_views_exclude_historical_only_and_inactive_offers(self):
        self.insert_historical_variant()
        self.insert_current_variant()
        vendor_id = self.insert_vendor()
        historical_offer = self.conn.execute(
            """INSERT INTO supplier_offers(variant_id,vendor_id,supplier_sku,active)
               VALUES ('900',%s,'HIST-OFFER',FALSE) RETURNING offer_id""",
            (vendor_id,),
        ).fetchone()[0]
        current_offer = self.conn.execute(
            """INSERT INTO supplier_offers(variant_id,vendor_id,supplier_sku,active)
               VALUES ('100',%s,'CURRENT-OFFER',TRUE) RETURNING offer_id""",
            (vendor_id,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO prices(
                 offer_id,price_state,effective_month,level_type,unit_price,source_file
               ) VALUES (%s,'current','2026-09-01','BASE',12,'fixture')""",
            (current_offer,),
        )
        self.assertEqual(
            self.conn.execute("SELECT offer_id FROM v_current_prices").fetchall(),
            [(current_offer,)],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM v_operational_inventory_snapshots WHERE variant_id='900'"
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(historical_offer)

    def test_downgrade_to_historical_only_fails_with_operational_dependents(self):
        self.insert_current_variant()
        vendor_id = self.insert_vendor()
        self.conn.execute(
            """INSERT INTO supplier_offers(variant_id,vendor_id,supplier_sku,active)
               VALUES ('100',%s,'CURRENT-OFFER',TRUE)""",
            (vendor_id,),
        )
        self.assert_rejected(
            """UPDATE variants SET
                 product_id=NULL,active=FALSE,catalog_state='RETIRED_CONFIRMED',
                 identity_scope='HISTORICAL_ONLY',
                 restoration_manifest_sha256=%s,
                 restoration_manifest_row_number=1,
                 restoration_evidence_version=%s,
                 restoration_owner_authorization=%s,
                 restoration_authority_git_sha=%s,
                 restoration_execution_git_sha=%s
               WHERE variant_id='100'""",
            (TERMINAL_SHA, EVIDENCE_VERSION, OWNER_AUTHORIZATION, AUTHORITY_SHA, EXECUTION_SHA),
        )

    def test_terminal_ledger_constraints_and_exclusion_linkage(self):
        self.insert_historical_variant()
        restore_id = self.conn.execute(
            """INSERT INTO historical_sales_review_decisions(
                 source_identity_key,source_variant_id,decision_action,
                 canonical_variant_id,actor,reason,decision_schema_version,
                 primary_manifest_sha256,primary_manifest_row_number,
                 evidence_version,owner_authorization,authority_git_sha,
                 execution_git_sha
               ) VALUES (
                 '900||HISTORICAL BOTTLE|750ML','900','RESTORE','900',
                 'owner','terminal restore','PHASE4_TERMINAL_V1',%s,1,%s,%s,%s,%s
               ) RETURNING historical_sales_review_decision_id""",
            (TERMINAL_SHA, EVIDENCE_VERSION, OWNER_AUTHORIZATION, AUTHORITY_SHA, EXECUTION_SHA),
        ).fetchone()[0]
        self.assertIsNotNone(restore_id)
        self.assert_rejected(
            """UPDATE historical_sales_review_decisions SET reason='rewritten'
               WHERE historical_sales_review_decision_id=%s""",
            (restore_id,),
        )
        self.assert_rejected(
            """DELETE FROM historical_sales_review_decisions
               WHERE historical_sales_review_decision_id=%s""",
            (restore_id,),
        )
        self.assert_rejected(
            """INSERT INTO historical_sales_review_decisions(
                 source_identity_key,decision_action,actor,reason,
                 decision_schema_version
               ) VALUES ('|||','EXCLUDE','owner','incomplete','PHASE4_TERMINAL_V1')"""
        )
        self.assert_rejected(
            """INSERT INTO historical_sales_review_decisions(
                 source_identity_key,decision_action,actor,reason,
                 decision_schema_version,reason_code,primary_manifest_sha256,
                 primary_manifest_row_number,evidence_version,owner_authorization,
                 authority_git_sha,execution_git_sha
               ) VALUES ('|||','EXCLUDE','owner','unknown',
                         'PHASE4_TERMINAL_V1','UNKNOWN_REASON',%s,1,%s,%s,%s,%s)""",
            (ORIGINAL_SHA, EVIDENCE_VERSION, OWNER_AUTHORIZATION, AUTHORITY_SHA, EXECUTION_SHA),
        )
        exclude_id = self.conn.execute(
            """INSERT INTO historical_sales_review_decisions(
                 source_identity_key,decision_action,actor,reason,
                 decision_schema_version,reason_code,primary_manifest_sha256,
                 primary_manifest_row_number,evidence_version,owner_authorization,
                 authority_git_sha,execution_git_sha
               ) VALUES ('|||','EXCLUDE','owner','original exclusion',
                         'PHASE4_TERMINAL_V1',
                         'PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION',%s,343,%s,%s,%s,%s)
               RETURNING historical_sales_review_decision_id""",
            (ORIGINAL_SHA, EVIDENCE_VERSION, OWNER_AUTHORIZATION, AUTHORITY_SHA, EXECUTION_SHA),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO historical_sales_exclusions(
                 source_key,reason,approved_by,reason_code,effective_decision_id
               ) VALUES ('|||','original exclusion','owner',
                         'PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION',%s)""",
            (exclude_id,),
        )
        self.assert_rejected(
            """INSERT INTO historical_sales_exclusions(
                 source_key,reason,approved_by,reason_code
               ) VALUES ('0||TIP|','bad linkage','owner',
                         'PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION')"""
        )

    def test_exact_preterminal_state_and_planned_mutations(self):
        artifact = self.seed_preterminal_state()
        xid_before = self.conn.execute(
            "SELECT txid_current_if_assigned()"
        ).fetchone()[0]
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        xid_after = self.conn.execute(
            "SELECT txid_current_if_assigned()"
        ).fetchone()[0]
        self.assertIsNone(xid_before)
        self.assertIsNone(xid_after)
        self.assertEqual(report["classification"], "PRE_TERMINAL_EXACT")
        self.assertEqual(report["source_lifecycle"], "PRE_REBUILD")
        self.assertEqual(
            report["source_status_counts"],
            {
                "facts": 59083,
                "resolved": 55971,
                "unresolved": 3112,
                "ambiguous": 0,
                "excluded": 0,
            },
        )
        self.assertEqual(
            report["planned_mutations"],
            {
                "restored_variants": 43,
                "terminal_decisions": 280,
                "original_exclusion_normalizations": 8,
                "terminal_aliases": 39,
                "active_exclusions": 198,
                "authority_registrations": 1,
            },
        )

    def test_all_proposed_aliases_are_proven_across_full_fact_population(self):
        artifact = self.seed_preterminal_state()
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        evidence = report["broad_alias_family_evidence"]
        self.assertEqual(len(evidence), 39)
        self.assertEqual(
            sum(item["full_run_raw_facts"] for item in evidence.values()),
            197,
        )
        self.assertEqual(
            sum(item["previously_resolved_raw_facts"] for item in evidence.values()),
            20,
        )
        self.assertEqual(
            sum(
                item["previously_resolved_raw_facts"] > 0
                for item in evidence.values()
            ),
            20,
        )
        self.assertTrue(
            all(item["manifest_raw_facts"] >= 1 for item in evidence.values())
        )
        self.assertEqual(
            sum(item["manifest_raw_facts"] for item in evidence.values()),
            177,
        )
        self.assertTrue(
            all(
                item["proof"] == "FULL_APPROVED_RUN_FAMILY_UNIFORM"
                for item in evidence.values()
            )
        )

    def test_alias_family_conflicting_prior_attribution_is_hard_stop(self):
        artifact = self.seed_preterminal_state()
        current_aliases = _terminal_safe_alias_families(artifact, terminal=True)
        pre_aliases = _terminal_safe_alias_families(artifact, terminal=False)
        restore_ids = {
            row.source_variant_id for row in artifact.rows if row.action == "RESTORE"
        }
        old_id, target = next(
            iter(
                sorted(
                    (old_id, target)
                    for old_id, target in current_aliases.items()
                    if old_id not in pre_aliases and target not in restore_ids
                )
            )
        )
        conflicting_target = "BASE-000"
        self.conn.execute(
            """UPDATE shopify_sales_daily_raw
               SET canonical_variant_id=%s
               WHERE raw_sales_id=(
                 SELECT raw_sales_id FROM shopify_sales_daily_raw
                 WHERE source_variant_id=%s AND resolution_status='RESOLVED'
                 ORDER BY raw_sales_id LIMIT 1
               )""",
            (conflicting_target, old_id),
        )
        self.conn.commit()
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(report["classification"], "CONFLICT")
        self.assertTrue(
            any(
                "reattribute prior resolved fact" in value
                for value in report["diagnostics"]
            )
        )

    def test_alias_family_uses_resolver_variant_id_whitespace_normalization(self):
        artifact = self.seed_preterminal_state()
        current_aliases = _terminal_safe_alias_families(artifact, terminal=True)
        pre_aliases = _terminal_safe_alias_families(artifact, terminal=False)
        restore_ids = {
            row.source_variant_id for row in artifact.rows if row.action == "RESTORE"
        }
        old_id, target = next(
            iter(
                sorted(
                    (old_id, target)
                    for old_id, target in current_aliases.items()
                    if old_id not in pre_aliases and target not in restore_ids
                )
            )
        )
        fact = self.conn.execute(
            """SELECT r.raw_sales_id,r.source_identity_key,r.canonical_variant_id,
                      rf.observed_net_items_sold,rf.observed_net_sales
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE rf.sales_backfill_id=%s AND r.source_variant_id=%s
                 AND r.resolution_status='RESOLVED'
               ORDER BY r.raw_sales_id LIMIT 1""",
            (APPROVED_RUN_ID, old_id),
        ).fetchone()
        self.assertIsNotNone(fact)
        self.assertEqual(str(fact[2]), target)
        self.conn.execute(
            "UPDATE shopify_sales_daily_raw SET source_variant_id=%s WHERE raw_sales_id=%s",
            (f"  {old_id}  ", fact[0]),
        )
        self.conn.commit()

        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        evidence = report["broad_alias_family_evidence"][old_id]
        self.assertEqual(report["classification"], "PRE_TERMINAL_EXACT")
        self.assertIn(str(fact[1]), evidence["full_run_source_keys"])
        self.assertEqual(evidence["canonical_variant_id"], target)
        unchanged = self.conn.execute(
            """SELECT canonical_variant_id,observed_net_items_sold,observed_net_sales
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE r.raw_sales_id=%s""",
            (fact[0],),
        ).fetchone()
        self.assertEqual(unchanged, (fact[2], fact[3], fact[4]))

        self.conn.execute(
            "UPDATE shopify_sales_daily_raw SET canonical_variant_id='BASE-000' WHERE raw_sales_id=%s",
            (fact[0],),
        )
        self.conn.commit()
        conflict = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(conflict["classification"], "CONFLICT")
        self.assertTrue(
            any(
                "reattribute prior resolved fact" in value
                for value in conflict["diagnostics"]
            )
        )

    def test_aliases_preserve_prior_per_variant_flow_and_recompute_methods(self):
        artifact = self.seed_preterminal_state()
        before_flow = self.conn.execute(
            """SELECT canonical_variant_id,COUNT(*)::int,
                      SUM(rf.observed_net_items_sold),SUM(rf.observed_net_sales)
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE rf.sales_backfill_id=%s AND r.raw_sales_id<=55971
               GROUP BY canonical_variant_id ORDER BY canonical_variant_id""",
            (APPROVED_RUN_ID,),
        ).fetchall()
        before_methods = dict(
            self.conn.execute(
                """SELECT resolution_method,COUNT(*)::int
                   FROM shopify_sales_daily_raw WHERE raw_sales_id<=55971
                   GROUP BY resolution_method"""
            ).fetchall()
        )
        self.conn.commit()
        with self.execution_patch():
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())
        _re_resolve_run_facts(self.conn, APPROVED_RUN_ID, load_identity_index(self.conn))
        after_flow = self.conn.execute(
            """SELECT canonical_variant_id,COUNT(*)::int,
                      SUM(rf.observed_net_items_sold),SUM(rf.observed_net_sales)
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE rf.sales_backfill_id=%s AND r.raw_sales_id<=55971
               GROUP BY canonical_variant_id ORDER BY canonical_variant_id""",
            (APPROVED_RUN_ID,),
        ).fetchall()
        after_methods = dict(
            self.conn.execute(
                """SELECT resolution_method,COUNT(*)::int
                   FROM shopify_sales_daily_raw WHERE raw_sales_id<=55971
                   GROUP BY resolution_method"""
            ).fetchall()
        )
        self.assertEqual(after_flow, before_flow)
        self.assertEqual(
            before_methods,
            {
                "EXACT_ACTIVE_VARIANT_ID": 36397,
                "APPROVED_VARIANT_ID_ALIAS": 19430,
                "APPROVED_HISTORICAL_IDENTITY": 136,
                "EXACT_PRESERVED_HISTORICAL_VARIANT_ID": 8,
            },
        )
        self.assertEqual(
            after_methods,
            {
                "EXACT_ACTIVE_VARIANT_ID": 36397,
                "APPROVED_VARIANT_ID_ALIAS": 19450,
                "APPROVED_HISTORICAL_IDENTITY": 116,
                "EXACT_PRESERVED_HISTORICAL_VARIANT_ID": 8,
            },
        )

    def test_terminal_dry_run_is_authorized_read_only_snapshot_with_no_xid(self):
        artifact = self.seed_preterminal_state()
        before = self.conn.execute(
            """SELECT (SELECT COUNT(*) FROM variants),
                      (SELECT COUNT(*) FROM historical_sales_review_decisions),
                      (SELECT COUNT(*) FROM variant_aliases),
                      (SELECT COUNT(*) FROM historical_sales_exclusions),
                      (SELECT COUNT(*) FROM historical_sales_exclusion_authority_runs)"""
        ).fetchone()
        self.conn.commit()
        with self.execution_patch():
            report = dry_run_terminal_disposition(
                self.conn, artifact, self.terminal_context()
            )
        after = self.conn.execute(
            """SELECT (SELECT COUNT(*) FROM variants),
                      (SELECT COUNT(*) FROM historical_sales_review_decisions),
                      (SELECT COUNT(*) FROM variant_aliases),
                      (SELECT COUNT(*) FROM historical_sales_exclusions),
                      (SELECT COUNT(*) FROM historical_sales_exclusion_authority_runs)"""
        ).fetchone()
        self.assertEqual(report["classification"], "PRE_TERMINAL_EXACT")
        self.assertTrue(report["transaction_read_only"])
        self.assertEqual(report["transaction_isolation"], "repeatable read")
        self.assertIsNone(report["xid_before"])
        self.assertIsNone(report["xid_after"])
        self.assertEqual(report["database_dml"], 0)
        self.assertEqual(after, before)

    def test_missing_target_and_active_old_id_fail_before_terminal_dml(self):
        artifact = self.seed_preterminal_state()
        restore_ids = {
            row.source_variant_id for row in artifact.rows if row.action == "RESTORE"
        }
        original_targets = {
            row.canonical_variant_id
            for row in artifact.original_manifest.rows
            if row.review_disposition == "MAP"
        }
        target = next(
            row.canonical_variant_id
            for row in artifact.rows
            if row.action == "MAP"
            and row.canonical_variant_id not in restore_ids
            and row.canonical_variant_id not in original_targets
        )
        self.conn.execute("DELETE FROM variants WHERE variant_id=%s", (target,))
        self.conn.commit()
        before = self.conn.execute(
            "SELECT COUNT(*) FROM historical_sales_review_decisions"
        ).fetchone()[0]
        self.conn.rollback()
        with self.execution_patch(), self.assertRaisesRegex(Exception, "CONFLICT"):
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM historical_sales_review_decisions"
            ).fetchone()[0],
            before,
        )

        self.conn.rollback()
        # Re-seed the missing target, then make one prospective old-ID alias an
        # active current identity. The classifier must reject this identity
        # collision before restoration or ledger DML.
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,sku,active,catalog_state
               ) VALUES (%s,%s,'Restored target fixture','750ML','TARGET',TRUE,'LIVE')""",
            (target, f"product-{target}"),
        )
        terminal_aliases = _terminal_safe_alias_families(artifact, terminal=True)
        pre_aliases = _terminal_safe_alias_families(artifact, terminal=False)
        old_id = next(key for key in terminal_aliases if key not in pre_aliases)
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,sku,active,catalog_state
               ) VALUES (%s,%s,'Conflicting active old ID','750ML','OLD-ID',TRUE,'LIVE')""",
            (old_id, f"product-{old_id}"),
        )
        self.conn.commit()
        with self.execution_patch(), self.assertRaisesRegex(Exception, "CONFLICT"):
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())

    def test_additional_approved_run_decision_key_is_conflict(self):
        artifact = self.seed_preterminal_state()
        self.conn.execute(
            """INSERT INTO historical_sales_review_decisions(
                 sales_backfill_id,source_identity_key,decision_action,actor,reason
               ) VALUES (%s,'999||ADDED IDENTITY|750ML','LEAVE_UNRESOLVED',
                         'adversary','unexpected source-key state')""",
            (APPROVED_RUN_ID,),
        )
        self.conn.commit()
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(report["classification"], "CONFLICT")
        self.assertFalse(report["preterminal_components"]["decisions"])

    def test_unexpected_historical_only_identity_is_conflict(self):
        artifact = self.seed_preterminal_state()
        self.conn.execute(
            """INSERT INTO variants(
                 variant_id,product_id,product_title,variant_title,active,catalog_state,
                 identity_scope,restoration_manifest_sha256,
                 restoration_manifest_row_number,restoration_evidence_version,
                 restoration_owner_authorization,restoration_authority_git_sha,
                 restoration_execution_git_sha
               ) VALUES ('99999999999999',NULL,'Unexpected historical','750ML',FALSE,
                         'RETIRED_CONFIRMED','HISTORICAL_ONLY',%s,1,%s,%s,%s,%s)""",
            (
                TERMINAL_SHA,
                EVIDENCE_VERSION,
                OWNER_AUTHORIZATION,
                AUTHORITY_SHA,
                EXECUTION_SHA,
            ),
        )
        self.conn.commit()
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(report["classification"], "CONFLICT")
        self.assertFalse(report["preterminal_components"]["restores"])

    def test_partial_terminal_state_is_conflict_and_not_repaired(self):
        artifact = self.seed_preterminal_state()
        terminal_row = next(row for row in artifact.rows if row.action == "EXCLUDE")
        prior_id = self.conn.execute(
            """SELECT historical_sales_review_decision_id
               FROM historical_sales_review_decisions
               WHERE source_identity_key=%s ORDER BY decided_at DESC,
                     historical_sales_review_decision_id DESC LIMIT 1""",
            (terminal_row.source_identity_key,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO historical_sales_review_decisions(
                 sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                 source_product_title,source_variant_title,decision_action,actor,reason,
                 supersedes_decision_id,decision_schema_version,reason_code,
                 primary_manifest_sha256,primary_manifest_row_number,evidence_version,
                 owner_authorization,authority_git_sha,execution_git_sha
               ) VALUES (%s,%s,%s,%s,%s,%s,'EXCLUDE','owner','partial',%s,
                         'PHASE4_TERMINAL_V1',%s,%s,%s,%s,%s,%s,%s)""",
            (
                APPROVED_RUN_ID,
                terminal_row.source_identity_key,
                terminal_row.source_variant_id,
                terminal_row.historical_sku,
                terminal_row.historical_product_title,
                terminal_row.historical_variant_title,
                prior_id,
                terminal_row.exclusion_reason_code,
                TERMINAL_SHA,
                terminal_row.row_number,
                EVIDENCE_VERSION,
                OWNER_AUTHORIZATION,
                AUTHORITY_SHA,
                EXECUTION_SHA,
            ),
        )
        self.conn.commit()
        self.assertEqual(
            inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)["classification"],
            "CONFLICT",
        )
        self.conn.rollback()
        before = self.conn.execute(
            "SELECT COUNT(*) FROM historical_sales_review_decisions"
        ).fetchone()[0]
        self.conn.rollback()
        with self.execution_patch(), self.assertRaisesRegex(Exception, "CONFLICT"):
            persist_terminal_disposition(
                self.conn, artifact, self.terminal_context()
            )
        after = self.conn.execute(
            "SELECT COUNT(*) FROM historical_sales_review_decisions"
        ).fetchone()[0]
        self.assertEqual(after, before)

    def test_apply_readback_and_second_execution_are_exact_noop(self):
        artifact = self.seed_preterminal_state()
        with self.execution_patch():
            first = persist_terminal_disposition(
                self.conn, artifact, self.terminal_context()
            )
            second = persist_terminal_disposition(
                self.conn, artifact, self.terminal_context()
            )
        self.assertEqual(first["classification_before"], "PRE_TERMINAL_EXACT")
        self.assertEqual(first["classification_after"], "CURRENT_TERMINAL_EXACT")
        self.assertGreater(first["committed_mutations"], 0)
        self.assertEqual(second["classification_before"], "CURRENT_TERMINAL_EXACT")
        self.assertEqual(second["committed_mutations"], 0)
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(report["classification"], "CURRENT_TERMINAL_EXACT")

    def test_manual_review_service_is_frozen_while_terminal_rebuild_is_pending(self):
        artifact = self.seed_preterminal_state()
        with self.execution_patch():
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())
        counts_before = self.conn.execute(
            """SELECT (SELECT COUNT(*) FROM historical_sales_review_decisions),
                      (SELECT COUNT(*) FROM variant_aliases),
                      (SELECT COUNT(*) FROM historical_sales_exclusions),
                      (SELECT COUNT(*) FROM change_log)"""
        ).fetchone()
        row = artifact.rows[0]
        with self.assertRaisesRegex(ValueError, "decisions are frozen"):
            record_historical_sales_review_decision(
                self.conn,
                source_key=row.source_identity_key,
                action="LEAVE_UNRESOLVED",
                canonical_variant_id=None,
                actor="manual-reviewer",
                reason="attempt during frozen terminal window",
            )
        counts_after = self.conn.execute(
            """SELECT (SELECT COUNT(*) FROM historical_sales_review_decisions),
                      (SELECT COUNT(*) FROM variant_aliases),
                      (SELECT COUNT(*) FROM historical_sales_exclusions),
                      (SELECT COUNT(*) FROM change_log)"""
        ).fetchone()
        self.assertEqual(counts_after, counts_before)
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(
            report["effective_actions"],
            {"RESTORE": 43, "MAP": 102, "EXCLUDE": 198, "LEAVE_UNRESOLVED": 0},
        )
        self.assertEqual(report["historical_only_variants"], 43)
        self.assertEqual(report["active_exclusions"], 198)
        self.assertEqual(report["approved_alias_families"], 56)

    def test_terminal_alias_provenance_drift_is_conflict(self):
        artifact = self.seed_preterminal_state()
        with self.execution_patch():
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())
        current_aliases = _terminal_safe_alias_families(artifact, terminal=True)
        pre_aliases = _terminal_safe_alias_families(artifact, terminal=False)
        old_id = next(key for key in current_aliases if key not in pre_aliases)
        self.conn.execute(
            """UPDATE variant_aliases
               SET evidence_json=evidence_json-'owner_authorization'
               WHERE old_variant_id=%s AND approved=TRUE""",
            (old_id,),
        )
        self.conn.commit()
        report = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(report["classification"], "CONFLICT")
        self.assertFalse(report["current_components"]["aliases"])

    def test_original_eight_keep_original_primary_provenance(self):
        artifact = self.seed_preterminal_state()
        with self.execution_patch():
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())
        rows = self.conn.execute(
            """WITH ranked AS (
                 SELECT source_identity_key,primary_manifest_sha256,
                        primary_manifest_row_number,reason_code,
                        owner_authorization,authority_git_sha,execution_git_sha,
                        ROW_NUMBER() OVER (
                          PARTITION BY source_identity_key
                          ORDER BY decided_at DESC,historical_sales_review_decision_id DESC
                        ) AS rn
                 FROM historical_sales_review_decisions
               )
               SELECT source_identity_key,primary_manifest_sha256,
                      primary_manifest_row_number,reason_code,
                      owner_authorization,authority_git_sha,execution_git_sha
               FROM ranked WHERE rn=1 AND reason_code=
                    'PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION'
               ORDER BY source_identity_key"""
        ).fetchall()
        self.assertEqual(len(rows), 8)
        original_by_key = {
            row.source_identity_key: row for row in artifact.original_manifest.rows
        }
        for row in rows:
            self.assertEqual(row[1], ORIGINAL_SHA)
            self.assertEqual(row[2], original_by_key[row[0]].row_number)
            self.assertEqual(row[4], OWNER_AUTHORIZATION)
            self.assertEqual(row[5], AUTHORITY_GIT_SHA)
            self.assertEqual(row[6], EXECUTION_SHA)

    def test_all_five_failure_stages_roll_back_every_terminal_mutation(self):
        artifact = self.seed_preterminal_state()
        before = self.conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM variants WHERE identity_scope='HISTORICAL_ONLY'),
                 (SELECT COUNT(*) FROM historical_sales_review_decisions),
                 (SELECT COUNT(*) FROM historical_sales_exclusions WHERE active),
                 (SELECT COUNT(*) FROM variant_aliases),
                 (SELECT COUNT(*) FROM historical_sales_exclusion_authority_runs),
                 (SELECT COUNT(*) FROM change_log)"""
        ).fetchone()
        for stage in (
            "after_restorations",
            "after_decisions",
            "after_aliases",
            "after_exclusions",
            "before_readback",
        ):
            self.conn.rollback()
            with self.subTest(stage=stage):
                with self.execution_patch(), self.assertRaisesRegex(
                    RuntimeError, "injected"
                ):
                    persist_terminal_disposition(
                        self.conn,
                        artifact,
                        self.terminal_context(),
                        inject_failure_stage=stage,
                    )
                after = self.conn.execute(
                    """SELECT
                         (SELECT COUNT(*) FROM variants WHERE identity_scope='HISTORICAL_ONLY'),
                         (SELECT COUNT(*) FROM historical_sales_review_decisions),
                         (SELECT COUNT(*) FROM historical_sales_exclusions WHERE active),
                         (SELECT COUNT(*) FROM variant_aliases),
                         (SELECT COUNT(*) FROM historical_sales_exclusion_authority_runs),
                         (SELECT COUNT(*) FROM change_log)"""
                ).fetchone()
                self.assertEqual(after, before)

    def test_restored_identity_resolves_exactly_without_alias(self):
        artifact = self.seed_preterminal_state()
        with self.execution_patch():
            persist_terminal_disposition(self.conn, artifact, self.terminal_context())
        restored = next(row for row in artifact.rows if row.action == "RESTORE")
        identity = load_identity_index(self.conn)
        resolution = identity.resolve(
            SalesSourceRow(
                restored.first_sale_date,
                restored.source_variant_id,
                restored.historical_sku,
                restored.historical_product_title,
                restored.historical_variant_title,
                Decimal("1"),
                Decimal("10"),
            )
        )
        self.assertEqual(resolution.status, "RESOLVED")
        self.assertEqual(resolution.canonical_variant_id, restored.source_variant_id)
        self.assertEqual(resolution.method, "EXACT_PRESERVED_HISTORICAL_VARIANT_ID")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM variant_aliases WHERE old_variant_id=variant_id"
            ).fetchone()[0],
            0,
        )

    def test_exact_exclusion_integrity_and_reason_aware_resolution(self):
        self.apply_and_resolve_terminal_fixture()
        report = derive_phase4_exclusion_integrity(self.conn, APPROVED_RUN_ID)
        self.assertTrue(report.passed, report.diagnostics)
        self.assertEqual(report.active_exclusion_keys, 198)
        self.assertEqual(report.excluded_source_facts, 1654)
        methods = dict(
            self.conn.execute(
                """SELECT resolution_method,COUNT(*) FROM shopify_sales_daily_raw
                   WHERE resolution_status='EXCLUDED'
                   GROUP BY resolution_method ORDER BY resolution_method"""
            ).fetchall()
        )
        self.assertEqual(
            methods,
            {
                "EXPLICIT_EXCLUSION": 189,
                "EXPLICIT_UNATTRIBUTABLE_EXCLUSION": 1465,
            },
        )

    def test_versioned_authority_supports_explicit_future_run_not_uuid_inheritance(self):
        self.apply_and_resolve_terminal_fixture()
        future_run = str(uuid.uuid4())
        unregistered_run = str(uuid.uuid4())
        for run_id in (future_run, unregistered_run):
            self.conn.execute(
                """INSERT INTO sales_backfill_runs(
                     sales_backfill_id,status,start_date,end_date,source,query_version,
                     raw_rows,resolved_rows,unresolved_rows,ambiguous_rows,
                     unique_source_facts,coverage_complete,pages_complete,
                     source_facts_persisted,idempotency_verified,
                     control_totals_reconciled,canonical_aggregate_rebuilt,store_timezone
                   ) VALUES (%s,'COMPLETED','2024-11-28','2026-08-10',
                             'SHOPIFYQL_SALES','SHOPIFYQL_SALES_V2',59083,57429,0,0,
                             59083,TRUE,TRUE,TRUE,TRUE,TRUE,TRUE,'America/New_York')""",
                (run_id,),
            )
            self.conn.execute(
                """INSERT INTO sales_backfill_run_facts(
                     sales_backfill_id,raw_sales_id,source_row_hash,
                     first_observed_net_items_sold,first_observed_net_sales,
                     observed_net_items_sold,observed_net_sales
                   )
                   SELECT %s,raw_sales_id,source_row_hash,
                          first_observed_net_items_sold,first_observed_net_sales,
                          observed_net_items_sold,observed_net_sales
                   FROM sales_backfill_run_facts WHERE sales_backfill_id=%s""",
                (run_id, APPROVED_RUN_ID),
            )
        self.conn.execute(
            """INSERT INTO historical_sales_exclusion_authority_runs(
                 sales_backfill_id,authority_version,decision_authority_run_id,
                 original_manifest_sha256,terminal_manifest_sha256,
                 decision_schema_version,evidence_version,owner_authorization,
                 authority_git_sha,execution_git_sha,registered_by
               ) VALUES (%s,%s,%s,%s,%s,'PHASE4_TERMINAL_V1',%s,%s,%s,%s,'fixture')""",
            (
                future_run,
                TERMINAL_AUTHORITY_VERSION,
                APPROVED_RUN_ID,
                ORIGINAL_SHA,
                TERMINAL_SHA,
                EVIDENCE_VERSION,
                OWNER_AUTHORIZATION,
                AUTHORITY_SHA,
                EXECUTION_SHA,
            ),
        )
        self.conn.commit()
        future = derive_exclusion_integrity(self.conn, future_run)
        self.assertTrue(future.passed, future.diagnostics)
        unregistered = derive_exclusion_integrity(self.conn, unregistered_run)
        self.assertFalse(unregistered.passed)
        self.assertNotEqual(unregistered.reason_buckets, future.reason_buckets)

    def test_missing_structured_reason_does_not_silently_exclude(self):
        artifact = self.apply_and_resolve_terminal_fixture()
        row = next(item for item in artifact.rows if item.action == "EXCLUDE")
        self.conn.execute(
            """UPDATE historical_sales_exclusions
               SET reason_code=NULL,effective_decision_id=NULL
               WHERE source_key=%s""",
            (row.source_identity_key,),
        )
        self.conn.commit()
        with self.assertRaisesRegex(
            ValueError, "no approved effective method"
        ):
            load_identity_index(self.conn)
        report = derive_phase4_exclusion_integrity(self.conn, APPROVED_RUN_ID)
        self.assertFalse(report.passed)
        self.assertIn(
            f"ACTIVE_EXCLUSION_LINK_MISMATCH:{row.source_identity_key}",
            report.diagnostics,
        )

    def test_incompatible_latest_decision_invalidates_older_valid_history(self):
        artifact = self.apply_and_resolve_terminal_fixture()
        row = next(item for item in artifact.rows if item.action == "EXCLUDE")
        prior = self.conn.execute(
            """SELECT historical_sales_review_decision_id
               FROM historical_sales_review_decisions
               WHERE source_identity_key=%s
               ORDER BY decided_at DESC,historical_sales_review_decision_id DESC LIMIT 1""",
            (row.source_identity_key,),
        ).fetchone()[0]
        invalid_id = self.conn.execute(
            """INSERT INTO historical_sales_review_decisions(
                 sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                 source_product_title,source_variant_title,decision_action,actor,reason,
                 supersedes_decision_id
               ) VALUES (%s,%s,%s,%s,%s,%s,'LEAVE_UNRESOLVED','adversary',
                         'synthetic incompatible latest state',%s)
               RETURNING historical_sales_review_decision_id""",
            (
                APPROVED_RUN_ID,
                row.source_identity_key,
                row.source_variant_id,
                row.historical_sku,
                row.historical_product_title,
                row.historical_variant_title,
                prior,
            ),
        ).fetchone()[0]
        self.conn.commit()
        report = derive_phase4_exclusion_integrity(self.conn, APPROVED_RUN_ID)
        self.assertFalse(report.passed)
        self.assertIn(
            f"LATEST_DECISION_MISMATCH:{row.source_identity_key}",
            report.diagnostics,
        )
        valid_id = self.conn.execute(
            """INSERT INTO historical_sales_review_decisions(
                 sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                 source_product_title,source_variant_title,decision_action,actor,reason,
                 supersedes_decision_id,decision_schema_version,reason_code,
                 primary_manifest_sha256,primary_manifest_row_number,evidence_version,
                 owner_authorization,authority_git_sha,execution_git_sha
               ) VALUES (%s,%s,%s,%s,%s,%s,'EXCLUDE','owner','valid latest',%s,
                         'PHASE4_TERMINAL_V1',%s,%s,%s,%s,%s,%s,%s)
               RETURNING historical_sales_review_decision_id""",
            (
                APPROVED_RUN_ID,
                row.source_identity_key,
                row.source_variant_id,
                row.historical_sku,
                row.historical_product_title,
                row.historical_variant_title,
                invalid_id,
                row.exclusion_reason_code,
                TERMINAL_SHA,
                row.row_number,
                EVIDENCE_VERSION,
                OWNER_AUTHORIZATION,
                AUTHORITY_SHA,
                EXECUTION_SHA,
            ),
        ).fetchone()[0]
        self.conn.execute(
            """UPDATE historical_sales_exclusions
               SET effective_decision_id=%s WHERE source_key=%s""",
            (valid_id, row.source_identity_key),
        )
        self.conn.commit()
        recovered = derive_phase4_exclusion_integrity(self.conn, APPROVED_RUN_ID)
        self.assertTrue(recovered.passed, recovered.diagnostics)

    def test_missing_extra_and_primary_provenance_drift_fail_integrity(self):
        artifact = self.apply_and_resolve_terminal_fixture()
        missing = next(item for item in artifact.rows if item.action == "EXCLUDE")
        self.conn.execute(
            "DELETE FROM historical_sales_exclusions WHERE source_key=%s",
            (missing.source_identity_key,),
        )
        self.conn.execute(
            """INSERT INTO historical_sales_exclusions(
                 source_key,reason,approved_by,active
               ) VALUES ('999||UNEXPECTED|750ML','legacy extra','adversary',TRUE)"""
        )
        original_key = "|||"
        original = next(
            row
            for row in artifact.original_manifest.rows
            if row.source_identity_key == original_key
        )
        prior_id = self.conn.execute(
            """SELECT historical_sales_review_decision_id
               FROM historical_sales_review_decisions
               WHERE source_identity_key=%s
               ORDER BY decided_at DESC,historical_sales_review_decision_id DESC LIMIT 1""",
            (original_key,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO historical_sales_review_decisions(
                 sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                 source_product_title,source_variant_title,decision_action,actor,reason,
                 supersedes_decision_id,decision_schema_version,reason_code,
                 primary_manifest_sha256,primary_manifest_row_number,evidence_version,
                 owner_authorization,authority_git_sha,execution_git_sha
               ) VALUES (%s,%s,%s,%s,%s,%s,'EXCLUDE','adversary',
                         'wrong primary row provenance',%s,'PHASE4_TERMINAL_V1',%s,
                         %s,%s,%s,%s,%s,%s)""",
            (
                APPROVED_RUN_ID,
                original_key,
                original.source_variant_id,
                original.source_sku,
                original.historical_product_title or None,
                original.historical_variant_title or None,
                prior_id,
                "PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION",
                ORIGINAL_SHA,
                original.row_number - 1,
                EVIDENCE_VERSION,
                OWNER_AUTHORIZATION,
                AUTHORITY_SHA,
                EXECUTION_SHA,
            ),
        )
        self.conn.commit()
        report = derive_phase4_exclusion_integrity(self.conn, APPROVED_RUN_ID)
        self.assertFalse(report.passed)
        self.assertIn("ACTIVE_EXCLUSION_MEMBERSHIP_MISMATCH", report.diagnostics)
        self.assertIn(
            f"LATEST_DECISION_MISMATCH:{original_key}", report.diagnostics
        )

    def test_partial_fact_exclusion_and_financial_drift_fail_integrity(self):
        artifact = self.apply_and_resolve_terminal_fixture()
        row = next(item for item in artifact.rows if item.action == "EXCLUDE")
        raw_id = self.conn.execute(
            """SELECT raw_sales_id FROM shopify_sales_daily_raw
               WHERE source_identity_key=%s ORDER BY raw_sales_id LIMIT 1""",
            (row.source_identity_key,),
        ).fetchone()[0]
        self.conn.execute(
            """UPDATE shopify_sales_daily_raw
               SET resolution_status='UNRESOLVED',resolution_method=NULL
               WHERE raw_sales_id=%s""",
            (raw_id,),
        )
        self.conn.execute(
            """UPDATE sales_backfill_run_facts
               SET observed_net_sales=observed_net_sales+1
               WHERE sales_backfill_id=%s AND raw_sales_id=%s""",
            (APPROVED_RUN_ID, raw_id),
        )
        self.conn.commit()
        report = derive_phase4_exclusion_integrity(self.conn, APPROVED_RUN_ID)
        self.assertFalse(report.passed)
        self.assertIn(
            f"RAW_EXCLUSION_STATE_MISMATCH:{row.source_identity_key}",
            report.diagnostics,
        )
        self.assertIn(
            "REASON_BUCKET_CONTROL_MISMATCH:"
            "HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW",
            report.diagnostics,
        )

    def test_disposable_terminal_partition_recomputes_projected_full_controls(self):
        artifact = self.apply_and_resolve_terminal_fixture()
        rows = self.conn.execute(
            """SELECT r.resolution_status,COUNT(*)::int,
                      SUM(rf.observed_net_items_sold),
                      SUM(ABS(rf.observed_net_items_sold)),
                      SUM(rf.observed_net_sales),SUM(ABS(rf.observed_net_sales))
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s
               GROUP BY r.resolution_status ORDER BY r.resolution_status""",
            (APPROVED_RUN_ID,),
        ).fetchall()
        terminal = {
            row[0]: (
                row[1],
                Decimal(str(row[2])),
                Decimal(str(row[3])),
                Decimal(str(row[4])),
                Decimal(str(row[5])),
            )
            for row in rows
        }
        self.assertEqual(
            terminal,
            {
                "EXCLUDED": (
                    1654,
                    Decimal("1842.0000"),
                    Decimal("1852.0000"),
                    Decimal("37841.30"),
                    Decimal("40855.28"),
                ),
                "RESOLVED": (
                    57429,
                    Decimal("80659.0000"),
                    Decimal("80693.0000"),
                    Decimal("1263133.84"),
                    Decimal("1264065.52"),
                ),
            },
        )
        methods = dict(
            self.conn.execute(
                """SELECT resolution_method,COUNT(*)::int
                   FROM shopify_sales_daily_raw GROUP BY resolution_method"""
            ).fetchall()
        )
        self.assertEqual(
            methods,
            {
                "EXACT_ACTIVE_VARIANT_ID": 36397,
                "APPROVED_VARIANT_ID_ALIAS": 19450,
                "APPROVED_HISTORICAL_IDENTITY": 116,
                "APPROVED_SOURCE_IDENTITY_DECISION": 1023,
                "EXACT_PRESERVED_HISTORICAL_VARIANT_ID": 443,
                "EXPLICIT_EXCLUSION": 189,
                "EXPLICIT_UNATTRIBUTABLE_EXCLUSION": 1465,
            },
        )
        self.assertEqual(sum(methods.values()), 59083)

        self.conn.execute("DELETE FROM sales_daily WHERE source='SHOPIFYQL_SALES'")
        self.conn.execute(
            """INSERT INTO sales_daily(
                 sale_date,variant_id,units_sold,net_sales,distinct_orders,source
               )
               SELECT r.sale_date,r.canonical_variant_id,
                      SUM(rf.observed_net_items_sold),SUM(rf.observed_net_sales),
                      NULL,'SHOPIFYQL_SALES'
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE rf.sales_backfill_id=%s AND r.resolution_status='RESOLVED'
               GROUP BY r.sale_date,r.canonical_variant_id""",
            (APPROVED_RUN_ID,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM sales_daily WHERE source='SHOPIFYQL_SALES'"
            ).fetchone()[0],
            57424,
        )
        self.conn.execute(
            """UPDATE sales_backfill_runs
               SET resolved_rows=57429,unresolved_rows=0,ambiguous_rows=0
               WHERE sales_backfill_id=%s""",
            (APPROVED_RUN_ID,),
        )
        self.conn.execute(
            """UPDATE readiness_gates SET status='PASS'
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.commit()
        post = inspect_terminal_state(self.conn, artifact, EXECUTION_SHA)
        self.assertEqual(post["classification"], "CURRENT_TERMINAL_EXACT")
        self.assertEqual(post["source_lifecycle"], "POST_REBUILD")

    def test_concurrent_transaction_lock_fails_closed(self):
        import psycopg

        artifact = self.seed_preterminal_state()
        blocker = psycopg.connect(os.environ["DATABASE_URL"])
        try:
            blocker.execute("BEGIN")
            acquire_backfill_transaction_lock(blocker)
            with self.execution_patch(), self.assertRaisesRegex(
                RuntimeError, "execution lock"
            ):
                persist_terminal_disposition(
                    self.conn, artifact, self.terminal_context()
                )
        finally:
            blocker.rollback()
            blocker.close()


if __name__ == "__main__":
    unittest.main()
