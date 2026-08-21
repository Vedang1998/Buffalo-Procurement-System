"""Disposable-PostgreSQL proof for controlled Phase 4 decision persistence."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import inspect
import os
from pathlib import Path
import sys
import unittest
import uuid


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT.parent))
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.historical_sales_manifest import (
    APPROVED_MANIFEST_SHA256,
    APPROVED_RUN_ID,
    EXCLUSION_SOURCE_KEYS,
    ManifestExecutionContext,
    _alias_insert_families,
    dry_run_manifest,
    load_authorized_manifest,
    persist_manifest_decisions,
    protected_state_fingerprints,
    readback_manifest_decisions,
    load_review_source_snapshot,
    validate_database_preflight,
)
from procurement_os.sales import SalesSourceRow, load_identity_index


DB_DIR = PROCUREMENT_ROOT / "db"
MANIFEST_PATH = PROCUREMENT_ROOT / "review" / "phase4_identity_manifest_corrected.csv"
MIGRATIONS = (
    "schema_postgres.sql",
    "001_v1_3_catalog_sales.sql",
    "002_seed_import_records.sql",
    "003_phase3_reconciliation.sql",
    "004_identity_decision_invariants.sql",
    "005_identity_investigation.sql",
    "006_phase4_sales_backfill.sql",
)

BUSHMILLS_SOURCE_KEY = "0|3010636|BUSHMILLS PROHIBITION|750ML"


@unittest.skipUnless(os.getenv("DATABASE_URL"), "PostgreSQL integration requires DATABASE_URL")
class Phase4IdentityManifestPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        import psycopg
        from psycopg import sql

        self.manifest = load_authorized_manifest(MANIFEST_PATH)
        self.context = ManifestExecutionContext(
            actor="phase4-manifest-test",
            implementation_git_sha="a" * 40,
        )
        self.conn = psycopg.connect(os.environ["DATABASE_URL"])
        self.schema = f"phase4_manifest_test_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for name in MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text(encoding="utf-8"))
        self._seed_manifest_review_snapshot()
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

    @staticmethod
    def _source_parts(source_identity_key: str) -> tuple[str | None, str | None]:
        source_variant_id, source_sku, _, _ = source_identity_key.split("|", 3)
        return source_variant_id or None, source_sku or None

    def _seed_manifest_review_snapshot(self) -> None:
        target_ids = sorted(
            {row.canonical_variant_id for row in self.manifest.rows if row.canonical_variant_id}
        )
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,sku,active,catalog_state
                   ) VALUES (%s,%s,%s,%s,%s,TRUE,'LIVE')""",
                [
                    (
                        target_id,
                        f"product-{target_id}",
                        next(
                            row.canonical_product_title
                            for row in self.manifest.rows
                            if row.canonical_variant_id == target_id
                        )
                        or f"Canonical {target_id}",
                        next(
                            row.canonical_variant_title
                            for row in self.manifest.rows
                            if row.canonical_variant_id == target_id
                        )
                        or "Default",
                        f"TARGET-{target_id}",
                    )
                    for target_id in target_ids
                ],
            )
            # One compatible old-ID alias is deliberately pre-existing. The batch
            # must reuse it and create only the other 16 safe uniform families.
            cur.execute(
                """INSERT INTO variant_aliases(
                     variant_id,old_variant_id,match_method,source,approved,
                     approved_by,approved_at,evidence_json
                   ) VALUES (
                     '42913592377419','42701584531531','SYNTHETIC_COMPATIBLE',
                     'PREEXISTING_TEST',TRUE,'phase4-manifest-test',now(),'{}'::jsonb
                   )"""
            )
            cur.execute(
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
            cur.execute(
                """UPDATE readiness_gates
                   SET status='FAIL',evidence_json='{"stage":"OWNER_REVIEW"}'::jsonb,
                       message='Owner decisions pending.',checked_at=TIMESTAMPTZ '2026-08-10 16:45:59+00'
                   WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
            )

            raw_rows: list[tuple[object, ...]] = []
            run_facts: list[tuple[object, ...]] = []
            raw_sales_id = 1
            start_date = date(2024, 11, 28)
            for manifest_row in self.manifest.rows:
                source_variant_id, source_sku = self._source_parts(
                    manifest_row.source_identity_key
                )
                for offset in range(manifest_row.affected_raw_rows):
                    units = (
                        manifest_row.absolute_unit_magnitude if offset == 0 else Decimal("0")
                    )
                    sales = (
                        manifest_row.absolute_sales_magnitude if offset == 0 else Decimal("0")
                    )
                    source_hash = hashlib.sha256(
                        f"{manifest_row.source_identity_key}:{offset}".encode("utf-8")
                    ).hexdigest()
                    raw_rows.append(
                        (
                            raw_sales_id,
                            APPROVED_RUN_ID,
                            start_date + timedelta(days=offset),
                            source_variant_id,
                            source_sku,
                            manifest_row.historical_product_title or None,
                            manifest_row.historical_variant_title or None,
                            units,
                            sales,
                            manifest_row.source_identity_key,
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

    def _decision_side_counts(self) -> tuple[int, int, int, int]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT
                     (SELECT COUNT(*) FROM historical_sales_review_decisions),
                     (SELECT COUNT(*) FROM historical_sales_exclusions WHERE active),
                     (SELECT COUNT(*) FROM variant_aliases WHERE source='SALES_BACKFILL_REVIEW'),
                     (SELECT COUNT(*) FROM change_log
                       WHERE table_name='historical_sales_review_decisions')"""
            )
            return tuple(int(value) for value in cur.fetchone())

    def _update_one_bushmills_evidence(self, column: str, value: str) -> None:
        allowed_columns = {
            "source_variant_id",
            "source_sku",
            "source_product_title",
            "source_variant_title",
        }
        if column not in allowed_columns:
            raise ValueError(f"unsupported source evidence column {column}")
        with self.conn.cursor() as cur:
            cur.execute(
                f"""UPDATE shopify_sales_daily_raw
                    SET {column}=%s
                    WHERE raw_sales_id=(
                      SELECT MIN(raw_sales_id)
                      FROM shopify_sales_daily_raw
                      WHERE sales_backfill_id=%s AND source_identity_key=%s
                    )
                    RETURNING raw_sales_id""",
                (value, APPROVED_RUN_ID, BUSHMILLS_SOURCE_KEY),
            )
            self.assertIsNotNone(cur.fetchone())
        self.conn.commit()

    def test_canonical_group_allows_bushmills_case_and_whitespace_variants(self):
        self._update_one_bushmills_evidence(
            "source_product_title", "  Bushmills   Prohibition  "
        )

        preflight = validate_database_preflight(self.conn, self.manifest)

        self.assertEqual(preflight.decision_state_counts["MISSING"], 343)

    def test_canonical_group_rejects_genuine_title_identity_change(self):
        self._update_one_bushmills_evidence(
            "source_product_title", "Bushmills Original"
        )

        with self.assertRaisesRegex(ValueError, "canonical review group drift"):
            validate_database_preflight(self.conn, self.manifest)

    def test_canonical_group_rejects_sku_change(self):
        self._update_one_bushmills_evidence("source_sku", "DIFFERENT-SKU")

        with self.assertRaisesRegex(ValueError, "canonical review group drift"):
            validate_database_preflight(self.conn, self.manifest)

    def test_canonical_group_rejects_size_or_variant_change(self):
        self._update_one_bushmills_evidence("source_variant_title", "1L")

        with self.assertRaisesRegex(ValueError, "canonical review group drift"):
            validate_database_preflight(self.conn, self.manifest)

    def test_canonical_group_rejects_old_variant_id_change(self):
        self._update_one_bushmills_evidence(
            "source_variant_id", "41111111111111"
        )

        with self.assertRaisesRegex(ValueError, "canonical review group drift"):
            validate_database_preflight(self.conn, self.manifest)

    def test_exact_preflight_and_missing_unknown_key_fail_closed(self):
        preflight = validate_database_preflight(self.conn, self.manifest)
        self.assertEqual(preflight.decision_state_counts["MISSING"], 343)
        self.assertEqual(sum(preflight.decision_state_counts.values()), 343)

        missing_key = self.manifest.rows[0].source_identity_key
        with self.conn.cursor() as cur:
            cur.execute(
                """DELETE FROM sales_backfill_run_facts rf USING shopify_sales_daily_raw r
                   WHERE rf.raw_sales_id=r.raw_sales_id AND rf.sales_backfill_id=%s
                     AND r.source_identity_key=%s""",
                (APPROVED_RUN_ID, missing_key),
            )
            cur.execute(
                "DELETE FROM shopify_sales_daily_raw WHERE source_identity_key=%s",
                (missing_key,),
            )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "missing manifest source key"):
            validate_database_preflight(self.conn, self.manifest)

    def test_source_magnitude_drift_fails_closed(self):
        key = self.manifest.rows[0].source_identity_key
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE sales_backfill_run_facts rf
                   SET observed_net_items_sold=observed_net_items_sold + 1
                   FROM shopify_sales_daily_raw r
                   WHERE rf.raw_sales_id=r.raw_sales_id AND rf.sales_backfill_id=%s
                     AND r.source_identity_key=%s
                     AND rf.raw_sales_id=(
                       SELECT MIN(rf2.raw_sales_id)
                       FROM sales_backfill_run_facts rf2
                       JOIN shopify_sales_daily_raw r2 ON r2.raw_sales_id=rf2.raw_sales_id
                       WHERE rf2.sales_backfill_id=%s AND r2.source_identity_key=%s
                     )""",
                (APPROVED_RUN_ID, key, APPROVED_RUN_ID, key),
            )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "source controls drift"):
            validate_database_preflight(self.conn, self.manifest)

    def test_sales_backfill_gate_must_stay_fail(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE readiness_gates SET status='PASS'
                   WHERE gate_name='SALES_BACKFILL'
                     AND scope_type='GLOBAL' AND scope_id=''"""
            )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "must remain FAIL"):
            validate_database_preflight(self.conn, self.manifest)

    def test_unknown_database_source_key_fails_closed(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(raw_sales_id),0) + 1 FROM shopify_sales_daily_raw")
            raw_sales_id = int(cur.fetchone()[0])
            source_hash = hashlib.sha256(b"unknown-extra-source-key").hexdigest()
            cur.execute(
                """INSERT INTO shopify_sales_daily_raw(
                     raw_sales_id,sales_backfill_id,sale_date,source_variant_id,
                     source_product_title,source_variant_title,net_items_sold,net_sales,
                     source_identity_key,source_row_hash,resolution_status
                   ) VALUES (
                     %s,%s,DATE '2024-11-28','0','Unexpected Historical Item','750ML',
                     1,10,'0||UNEXPECTED HISTORICAL ITEM|750ML',%s,'UNRESOLVED'
                   )""",
                (raw_sales_id, APPROVED_RUN_ID, source_hash),
            )
            cur.execute(
                """INSERT INTO sales_backfill_run_facts(
                     sales_backfill_id,raw_sales_id,source_row_hash,
                     first_observed_net_items_sold,first_observed_net_sales,
                     observed_net_items_sold,observed_net_sales
                   ) VALUES (%s,%s,%s,1,10,1,10)""",
                (APPROVED_RUN_ID, raw_sales_id, source_hash),
            )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "unknown database source key"):
            validate_database_preflight(self.conn, self.manifest)

    def test_nonuniform_old_id_family_fails_closed(self):
        snapshot = load_review_source_snapshot(self.conn, APPROVED_RUN_ID)
        map_row = next(
            row
            for row in self.manifest.rows
            if row.review_disposition == "MAP"
            and row.source_variant_id not in (None, "0")
        )
        unrelated_key = next(
            row.source_identity_key
            for row in self.manifest.rows
            if row.review_disposition == "LEAVE_UNRESOLVED"
        )
        mutated = dict(snapshot)
        mutated[unrelated_key] = replace(
            mutated[unrelated_key], source_variant_id=map_row.source_variant_id
        )
        with self.assertRaisesRegex(ValueError, "nonuniform old-ID family"):
            _alias_insert_families(self.conn, self.manifest, mutated)

    def test_unknown_map_target_fails_closed(self):
        map_row = next(row for row in self.manifest.rows if row.review_disposition == "MAP")
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM variants WHERE variant_id=%s", (map_row.canonical_variant_id,))
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "canonical MAP target"):
            validate_database_preflight(self.conn, self.manifest)

    def test_active_mapping_rejection_fails_closed(self):
        map_row = next(
            row
            for row in self.manifest.rows
            if row.review_disposition == "MAP"
            and row.source_variant_id not in (None, "0")
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mapping_rejections(
                     mapping_type,source_key,rejected_variant_id,source_text,
                     evidence_json,rejected_by,active
                   ) VALUES ('HISTORICAL_VARIANT',%s,%s,'synthetic rejection',
                             '{}'::jsonb,'phase4-manifest-test',TRUE)""",
                (map_row.source_variant_id, map_row.canonical_variant_id),
            )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "active mapping rejection"):
            validate_database_preflight(self.conn, self.manifest)

    def test_conflicting_alias_fails_closed(self):
        map_row = next(
            row
            for row in self.manifest.rows
            if row.review_disposition == "MAP"
            and row.source_variant_id not in (None, "0")
        )
        conflicting_target = next(
            row.canonical_variant_id
            for row in self.manifest.rows
            if row.review_disposition == "MAP"
            and row.canonical_variant_id != map_row.canonical_variant_id
        )
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO variant_aliases(
                     variant_id,old_variant_id,match_method,source,approved
                   ) VALUES (%s,%s,'SYNTHETIC_CONFLICT','TEST',TRUE)""",
                (conflicting_target, map_row.source_variant_id),
            )
        self.conn.commit()
        with self.assertRaisesRegex(
            ValueError, "alias conflict|existing identity evidence conflicts"
        ):
            validate_database_preflight(self.conn, self.manifest)

    def test_dry_run_is_database_read_only_and_assigns_no_transaction_id(self):
        before = protected_state_fingerprints(self.conn)
        self.conn.commit()
        report = dry_run_manifest(self.conn, self.manifest, self.context)
        after = protected_state_fingerprints(self.conn)
        self.assertEqual(report["transaction_read_only"], "on")
        self.assertIsNone(report["txid_before"])
        self.assertIsNone(report["txid_after"])
        self.assertEqual(report["decision_state_counts"], {
            "CONFLICT": 0,
            "CURRENT_PROVENANCE": 0,
            "LEGACY_COMPATIBLE": 0,
            "MISSING": 343,
        })
        self.assertEqual(report["planned_mutations"]["decision_rows"], 343)
        self.assertEqual(
            report["decision_artifact_counts"]["effective_manifest_decisions"], 0
        )
        self.assertEqual(
            report["decision_artifact_counts"]["compatible_existing_alias_families"],
            1,
        )
        self.assertEqual(before, after)
        self.assertEqual(self._decision_side_counts(), (0, 0, 0, 0))

    def test_apply_persists_exact_controls_without_protected_state_change(self):
        before = protected_state_fingerprints(self.conn)
        self.conn.commit()
        report = persist_manifest_decisions(self.conn, self.manifest, self.context)
        after = protected_state_fingerprints(self.conn)
        self.assertEqual(before, after)
        self.assertEqual(report["manifest_sha256"], APPROVED_MANIFEST_SHA256)
        self.assertEqual(report["readback"]["effective_source_keys"], 343)
        self.assertEqual(report["readback"]["map_to_canonical"], 55)
        self.assertEqual(report["readback"]["exclude_historical_item"], 8)
        self.assertEqual(report["readback"]["leave_unresolved"], 280)
        self.assertEqual(report["readback"]["distinct_map_targets"], 51)
        self.assertEqual(report["readback"]["manifest_provenance_complete"], 343)
        self.assertEqual(report["readback"]["fiesta_mappings"], 0)
        self.assertEqual(report["readback"]["nutrl_mappings"], 3)
        self.assertEqual(report["readback"]["high_noon_tequila_unresolved"], 3)
        self.assertEqual(report["readback"]["active_exclusions"], 8)
        self.assertEqual(report["readback"]["sales_backfill_status"], "FAIL")
        self.assertEqual(
            report["decision_artifact_counts_after"]["effective_manifest_decisions"],
            343,
        )
        self.assertEqual(
            report["decision_artifact_counts_after"][
                "compatible_existing_alias_families"
            ],
            17,
        )
        decisions, exclusions, aliases, changes = self._decision_side_counts()
        self.assertEqual((decisions, exclusions, changes), (343, 8, 343))
        self.assertEqual(aliases, 16)

    def test_title_only_mapping_is_exact_key_resolvable(self):
        map_row = next(
            row
            for row in self.manifest.rows
            if row.review_disposition == "MAP" and row.source_variant_id in (None, "0")
        )
        persist_manifest_decisions(self.conn, self.manifest, self.context)
        source = SalesSourceRow(
            date(2024, 11, 28),
            map_row.source_variant_id,
            map_row.source_sku,
            map_row.historical_product_title,
            map_row.historical_variant_title,
            Decimal("1"),
            Decimal("1"),
        )
        resolution = load_identity_index(self.conn).resolve(source)
        self.assertEqual(
            (resolution.status, resolution.canonical_variant_id, resolution.method),
            ("RESOLVED", map_row.canonical_variant_id, "APPROVED_SOURCE_IDENTITY_DECISION"),
        )
        similar = SalesSourceRow(
            source.sale_date,
            source.source_variant_id,
            source.source_sku,
            f"{source.source_product_title} DIFFERENT",
            source.source_variant_title,
            source.net_items_sold,
            source.net_sales,
        )
        self.assertNotEqual(
            load_identity_index(self.conn).resolve(similar).method,
            "APPROVED_SOURCE_IDENTITY_DECISION",
        )

    def test_identical_rerun_is_a_true_noop(self):
        persist_manifest_decisions(self.conn, self.manifest, self.context)
        before_counts = self._decision_side_counts()
        before_fingerprints = protected_state_fingerprints(self.conn)
        self.conn.commit()
        second = persist_manifest_decisions(self.conn, self.manifest, self.context)
        self.assertEqual(second["decision_state_counts"]["CURRENT_PROVENANCE"], 343)
        self.assertEqual(second["committed_mutations"], 0)
        self.assertEqual(self._decision_side_counts(), before_counts)
        self.assertEqual(protected_state_fingerprints(self.conn), before_fingerprints)

    def test_legacy_identical_decision_is_normalized_once_then_noop(self):
        row = self.manifest.rows[0]
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO historical_sales_review_decisions(
                     sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                     source_product_title,source_variant_title,decision_action,
                     canonical_variant_id,actor,reason,evidence_json
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'legacy-owner','legacy reason','{}'::jsonb)
                   RETURNING historical_sales_review_decision_id""",
                (
                    APPROVED_RUN_ID,
                    row.source_identity_key,
                    row.source_variant_id,
                    row.source_sku,
                    row.historical_product_title or None,
                    row.historical_variant_title or None,
                    row.stored_action,
                    row.canonical_variant_id,
                ),
            )
            legacy_id = str(cur.fetchone()[0])
        self.conn.commit()

        first = persist_manifest_decisions(self.conn, self.manifest, self.context)
        self.assertEqual(first["decision_state_counts"]["LEGACY_COMPATIBLE"], 1)
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT supersedes_decision_id,evidence_json->>'manifest_sha256',
                          evidence_json->>'normalized_legacy_decision_id'
                   FROM historical_sales_review_decisions
                   WHERE source_identity_key=%s
                   ORDER BY decided_at DESC,historical_sales_review_decision_id DESC LIMIT 1""",
                (row.source_identity_key,),
            )
            latest = cur.fetchone()
        self.assertEqual(str(latest[0]), legacy_id)
        self.assertEqual(latest[1], APPROVED_MANIFEST_SHA256)
        self.assertEqual(latest[2], legacy_id)
        counts = self._decision_side_counts()
        self.conn.commit()
        second = persist_manifest_decisions(self.conn, self.manifest, self.context)
        self.assertEqual(second["decision_state_counts"]["CURRENT_PROVENANCE"], 343)
        self.assertEqual(second["committed_mutations"], 0)
        self.assertEqual(self._decision_side_counts(), counts)

    def test_conflicting_prior_decision_is_hard_stop_without_partial_writes(self):
        row = next(row for row in self.manifest.rows if row.review_disposition == "MAP")
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO historical_sales_review_decisions(
                     sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                     source_product_title,source_variant_title,decision_action,
                     canonical_variant_id,actor,reason
                   ) VALUES (%s,%s,%s,%s,%s,%s,'LEAVE_UNRESOLVED',NULL,'legacy-owner','conflict')""",
                (
                    APPROVED_RUN_ID,
                    row.source_identity_key,
                    row.source_variant_id,
                    row.source_sku,
                    row.historical_product_title or None,
                    row.historical_variant_title or None,
                ),
            )
        self.conn.commit()
        before = self._decision_side_counts()
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "conflicting prior decision"):
            persist_manifest_decisions(self.conn, self.manifest, self.context)
        self.assertEqual(self._decision_side_counts(), before)

    def test_injected_mid_transaction_failure_rolls_everything_back(self):
        before = self._decision_side_counts()
        self.conn.commit()
        with self.assertRaisesRegex(RuntimeError, "injected manifest persistence failure"):
            persist_manifest_decisions(
                self.conn,
                self.manifest,
                self.context,
                inject_failure_after_row=100,
            )
        self.assertEqual(self._decision_side_counts(), before)

    def test_fresh_readback_and_second_dry_run_prove_complete_noop_state(self):
        persist_manifest_decisions(self.conn, self.manifest, self.context)
        readback = readback_manifest_decisions(self.conn, self.manifest)
        self.assertEqual(readback["effective_source_keys"], 343)
        self.assertEqual(readback["conflicting_effective_decisions"], 0)
        self.assertEqual(readback["active_exclusion_source_keys"], sorted(EXCLUSION_SOURCE_KEYS))
        report = dry_run_manifest(self.conn, self.manifest, self.context)
        self.assertEqual(report["decision_state_counts"]["CURRENT_PROVENANCE"], 343)
        self.assertEqual(report["planned_mutations"]["total"], 0)
        self.assertEqual(report["readback"]["manifest_provenance_complete"], 343)

    def test_readback_detects_incompatible_latest_effective_target(self):
        persist_manifest_decisions(self.conn, self.manifest, self.context)
        cranberry_rows = [
            row
            for row in self.manifest.rows
            if row.review_disposition == "MAP"
            and row.source_variant_id == "41157780013131"
        ]
        lemonade_target = next(
            row.canonical_variant_id
            for row in self.manifest.rows
            if row.source_variant_id == "41157780045899"
        )
        self.assertEqual(len(cranberry_rows), 2)
        source_key = cranberry_rows[0].source_identity_key
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO historical_sales_review_decisions(
                     sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                     source_product_title,source_variant_title,decision_action,
                     canonical_variant_id,actor,reason,evidence_json,
                     supersedes_decision_id,decided_at
                   )
                   SELECT sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                          source_product_title,source_variant_title,decision_action,
                          %s,actor,reason,evidence_json,
                          historical_sales_review_decision_id,decided_at + INTERVAL '1 second'
                   FROM historical_sales_review_decisions
                   WHERE source_identity_key=%s
                   ORDER BY decided_at DESC,historical_sales_review_decision_id DESC
                   LIMIT 1""",
                (lemonade_target, source_key),
            )

        report = readback_manifest_decisions(
            self.conn, self.manifest, require_complete=False
        )
        self.assertEqual(report["map_to_canonical"], 55)
        self.assertEqual(report["distinct_map_targets"], 51)
        self.assertEqual(report["manifest_provenance_complete"], 343)
        self.assertEqual(report["conflicting_effective_decisions"], 1)
        with self.assertRaisesRegex(
            ValueError,
            "post-write readback conflicting_effective_decisions=1 expected 0",
        ):
            readback_manifest_decisions(self.conn, self.manifest)

    def test_readback_ignores_superseded_historical_conflict(self):
        persist_manifest_decisions(self.conn, self.manifest, self.context)
        cranberry_rows = [
            row
            for row in self.manifest.rows
            if row.review_disposition == "MAP"
            and row.source_variant_id == "41157780013131"
        ]
        lemonade_target = next(
            row.canonical_variant_id
            for row in self.manifest.rows
            if row.source_variant_id == "41157780045899"
        )
        source_key = cranberry_rows[0].source_identity_key
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO historical_sales_review_decisions(
                     sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                     source_product_title,source_variant_title,decision_action,
                     canonical_variant_id,actor,reason,evidence_json,
                     supersedes_decision_id,decided_at
                   )
                   SELECT sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                          source_product_title,source_variant_title,decision_action,
                          %s,actor,reason,evidence_json,
                          historical_sales_review_decision_id,decided_at + INTERVAL '1 second'
                   FROM historical_sales_review_decisions
                   WHERE source_identity_key=%s
                   ORDER BY decided_at DESC,historical_sales_review_decision_id DESC
                   LIMIT 1
                   RETURNING historical_sales_review_decision_id,decided_at""",
                (lemonade_target, source_key),
            )
            conflicting_id, conflicting_at = cur.fetchone()
            cur.execute(
                """INSERT INTO historical_sales_review_decisions(
                     sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                     source_product_title,source_variant_title,decision_action,
                     canonical_variant_id,actor,reason,evidence_json,
                     supersedes_decision_id,decided_at
                   )
                   SELECT sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                          source_product_title,source_variant_title,decision_action,
                          %s,actor,reason,evidence_json,%s,%s + INTERVAL '1 second'
                   FROM historical_sales_review_decisions
                   WHERE source_identity_key=%s
                     AND canonical_variant_id=%s
                   ORDER BY decided_at ASC,historical_sales_review_decision_id ASC
                   LIMIT 1""",
                (
                    cranberry_rows[0].canonical_variant_id,
                    conflicting_id,
                    conflicting_at,
                    source_key,
                    cranberry_rows[0].canonical_variant_id,
                ),
            )

        report = readback_manifest_decisions(self.conn, self.manifest)
        self.assertEqual(report["conflicting_effective_decisions"], 0)
        self.assertEqual(report["manifest_provenance_complete"], 343)

    def test_persistence_source_has_no_rebuild_gate_shopify_or_po_call_path(self):
        import procurement_os.historical_sales_manifest as service
        from procurement.tools import persist_phase4_identity_manifest as cli

        source = inspect.getsource(service) + inspect.getsource(cli)
        for forbidden in (
            "_re_resolve_run_facts",
            "_finalize_sales_backfill_unlocked",
            "finalize_sales_backfill",
            "rerun_sales_identity_resolution",
            "evaluate_sales_readiness",
            "_set_sales_gate",
            "ShopifyGraphQLClient",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
