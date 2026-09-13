"""Exact-role PostgreSQL contract tests for migration 015 and V1 retirement."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, time, timezone
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4

import psycopg
from psycopg import sql

import apply_schema
from postgres_test_support import validated_test_connection
from procurement_os.emergency_packet import list_monday_artifacts, read_monday_artifact
from procurement_os.monday_controls import load_material_edit_policy
from procurement_os.monday_forecast_retirement import (
    CATALOG_SHA256,
    MIGRATION_NAME,
    MIGRATION_SHA256,
    MondayForecastRetirementContractError,
    compute_retirement_catalog_sha256,
    verify_monday_forecast_v2_retirement_contract,
)
from procurement_os.monday_run import build_after_review
from procurement_os.recommendations import (
    MondayRecommendationError,
    confirm_monday_stale_forecast_retirement,
    preview_monday_stale_forecast_retirement,
)
from procurement_os.storage import LocalFilesystemStorage


DB_DIR = Path(__file__).resolve().parents[1] / "db"
SCHEMA = "qa_mapping_test"
BUSINESS_DATE = date(2026, 9, 13)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class MondayForecastRetirementPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        connection, target, _ = validated_test_connection()
        connection.close()
        cls.admin_url = target.url
        parsed = urlparse(target.url)
        cls.database = target.database
        cls.mapping_url = (
            f"postgresql://qa_release_login@{parsed.hostname}:{parsed.port}/{target.database}"
            f"?options={quote('-c role=qa_mapping_owner -c search_path=qa_mapping_test,pg_catalog')}"
        )

    def setUp(self) -> None:
        self.conn: psycopg.Connection | None = None
        self._reset_before_015()
        with self.conn.transaction():
            self.assertTrue(
                apply_schema._verify_or_apply_post_mapping_release(self.conn, DB_DIR)
            )

    def tearDown(self) -> None:
        if self.conn is not None:
            self.conn.close()
        self._admin_cleanup()

    def _admin_connection(self):
        return psycopg.connect(self.admin_url, autocommit=True)

    def _admin_cleanup(self) -> None:
        try:
            with self._admin_connection() as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(SCHEMA)
                    )
                )
                conn.execute("DROP EXTENSION IF EXISTS pgcrypto CASCADE")
                conn.execute("REVOKE qa_mapping_owner FROM qa_release_login")
        except psycopg.Error:
            pass

    def _prepare_roles_and_schema(self) -> None:
        with self._admin_connection() as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(SCHEMA)
                )
            )
            conn.execute("DROP EXTENSION IF EXISTS pgcrypto CASCADE")
            conn.execute(
                "DO $$ BEGIN "
                "IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles "
                "WHERE rolname='qa_mapping_owner') THEN CREATE ROLE qa_mapping_owner "
                "NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION; "
                "END IF; IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles "
                "WHERE rolname='qa_release_login') THEN CREATE ROLE qa_release_login "
                "LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION; "
                "END IF; END $$"
            )
            conn.execute(
                "ALTER ROLE qa_mapping_owner NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION"
            )
            conn.execute(
                "ALTER ROLE qa_release_login LOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION"
            )
            conn.execute("REVOKE qa_mapping_owner FROM qa_release_login")
            conn.execute(
                "GRANT qa_mapping_owner TO qa_release_login "
                "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
            )
            conn.execute(
                sql.SQL("GRANT CREATE ON DATABASE {} TO qa_mapping_owner").format(
                    sql.Identifier(self.database)
                )
            )
            conn.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION qa_mapping_owner").format(
                    sql.Identifier(SCHEMA)
                )
            )
            conn.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(SCHEMA)
                )
            )

    def _reset_before_015(self) -> None:
        if self.conn is not None:
            self.conn.close()
        self._prepare_roles_and_schema()
        self.conn = psycopg.connect(self.mapping_url)
        self._install_legacy_mapping_and_v1(self.conn)

    def _insert_run(
        self,
        conn: psycopg.Connection,
        *,
        model_version: str,
        business_date: date = BUSINESS_DATE,
        workflow_stage: str = "PREPARING",
        output_mode: str | None = "INTERNAL_DRAFT_ONLY",
    ) -> tuple[UUID, str]:
        run_id = uuid4()
        manifest = _canonical_json(
            {
                "business_date": business_date,
                "contexts": [],
                "evaluation_at": datetime.combine(
                    business_date, time(12), tzinfo=timezone.utc
                ),
                "material_edit_policy": load_material_edit_policy().evidence(),
                "method_version": model_version,
                "variant_ids": ["1001"],
            }
        )
        fingerprint = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO runs(
                       run_id,run_type,status,source_data_through,business_date,
                       started_at,idempotency_key,input_fingerprint,workflow_stage,
                       model_version,notes,procurement_output_mode,
                       procurement_input_manifest)
                VALUES (%s,'MONDAY_PROCUREMENT','RUNNING',%s,%s,%s,%s,%s,%s,
                        %s,'TEST DATA — NOT FOR ORDERING',%s,%s)""",
            (
                run_id,
                datetime.combine(
                    business_date, time.max, tzinfo=timezone.utc
                ),
                business_date,
                datetime.combine(
                    business_date, time(12), tzinfo=timezone.utc
                ),
                f"retirement-test:{run_id}",
                fingerprint,
                workflow_stage,
                model_version,
                output_mode,
                manifest,
            ),
        )
        return run_id, fingerprint

    def _install_legacy_mapping_and_v1(self, conn: psycopg.Connection) -> None:
        schema_oid = int(
            conn.execute(
                "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s", (SCHEMA,)
            ).fetchone()[0]
        )
        conn.commit()
        for name in apply_schema.MIGRATION_ORDER[:-1]:
            with conn.transaction():
                apply_schema.apply_verified_legacy_file(
                    conn, DB_DIR, name, schema_oid=schema_oid
                )
        with conn.transaction():
            self.assertTrue(apply_schema._verify_or_apply_mapping_release(conn, DB_DIR))
        with conn.transaction():
            self.v1_run_id, self.v1_fingerprint = self._insert_run(
                conn, model_version="EMERGENCY_TRANSPARENT_V1"
            )

    def _seed_v1_guard_parents_before_015(self) -> dict[str, object]:
        assert self.conn is not None
        vendor_id = UUID("00000000-0000-4000-8000-000000000077")
        variant_id = "retirement-guard-variant"
        self.conn.execute(
            "INSERT INTO vendors(vendor_id,vendor_name,active) VALUES (%s,%s,TRUE)",
            (vendor_id, "Synthetic retirement guard vendor"),
        )
        self.conn.execute(
            """INSERT INTO variants(
                       variant_id,product_id,product_title,variant_title,active,
                       catalog_state,identity_scope)
                VALUES (%s,%s,%s,%s,TRUE,'LIVE','CURRENT')""",
            (
                variant_id,
                "retirement-guard-product",
                "Synthetic retirement guard product",
                "Synthetic",
            ),
        )
        self.conn.execute(
            "ALTER TABLE procurement_recommendations DISABLE TRIGGER USER"
        )
        recommendation_id = self.conn.execute(
            """INSERT INTO procurement_recommendations(
                       run_id,variant_id,vendor_id,metrics)
                VALUES (%s,%s,%s,'{}') RETURNING recommendation_id""",
            (self.v1_run_id, variant_id, vendor_id),
        ).fetchone()[0]
        self.conn.execute(
            "ALTER TABLE procurement_recommendations ENABLE TRIGGER USER"
        )
        self.conn.execute("ALTER TABLE review_decisions DISABLE TRIGGER USER")
        review_decision_id = self.conn.execute(
            """INSERT INTO review_decisions(
                       run_id,recommendation_id,decision_type,scope,action,decided_by)
                VALUES (%s,%s,'SYNTHETIC','RUN_ONLY','DEFER','synthetic:test')
                RETURNING decision_id""",
            (self.v1_run_id, recommendation_id),
        ).fetchone()[0]
        self.conn.execute("ALTER TABLE review_decisions ENABLE TRIGGER USER")
        exception_id = self.conn.execute(
            """INSERT INTO exceptions(
                       run_id,exception_type,severity,variant_id,message)
                VALUES (%s,'SYNTHETIC','HIGH',%s,'frozen V1 blocker')
                RETURNING exception_id""",
            (self.v1_run_id, variant_id),
        ).fetchone()[0]
        self.conn.execute("ALTER TABLE purchase_orders DISABLE TRIGGER USER")
        po_id = self.conn.execute(
            """INSERT INTO purchase_orders(run_id,vendor_id,po_status,notes)
                VALUES (%s,%s,'DRAFT','frozen V1 test PO') RETURNING po_id""",
            (self.v1_run_id, vendor_id),
        ).fetchone()[0]
        self.conn.execute("ALTER TABLE purchase_orders ENABLE TRIGGER USER")
        self.conn.execute("ALTER TABLE purchase_order_lines DISABLE TRIGGER USER")
        po_line_id = self.conn.execute(
            """INSERT INTO purchase_order_lines(
                       po_id,variant_id,recommendation_id,review_decision_id,
                       cases,ordered_units,line_status,reconciliation_status)
                VALUES (%s,%s,%s,%s,1,1,'DRAFT','UNKNOWN')
                RETURNING po_line_id""",
            (po_id, variant_id, recommendation_id, review_decision_id),
        ).fetchone()[0]
        self.conn.execute("ALTER TABLE purchase_order_lines ENABLE TRIGGER USER")
        line_exception_id = self.conn.execute(
            """INSERT INTO exceptions(
                       run_id,exception_type,severity,variant_id,po_line_id,message)
                VALUES (NULL,'OPEN_PO_RECONCILIATION_REQUIRED','HIGH',%s,%s,
                        'frozen V1 line-linked blocker')
                RETURNING exception_id""",
            (variant_id, po_line_id),
        ).fetchone()[0]
        return {
            "vendor_id": vendor_id,
            "variant_id": variant_id,
            "recommendation_id": int(recommendation_id),
            "review_decision_id": int(review_decision_id),
            "exception_id": int(exception_id),
            "po_id": po_id,
            "po_line_id": int(po_line_id),
            "line_exception_id": int(line_exception_id),
        }

    def _preview(self, *, actor: str = "synthetic:retirement-owner:01", reason: str = "Retire exact fabricated V1 run"):
        assert self.conn is not None
        return preview_monday_stale_forecast_retirement(
            self.conn,
            run_id=str(self.v1_run_id),
            actor=actor,
            reason=reason,
        )

    def test_apply_replay_marker_and_literal_manifest_are_exact(self):
        assert self.conn is not None
        self.assertEqual(
            hashlib.sha256((DB_DIR / MIGRATION_NAME).read_bytes()).hexdigest(),
            MIGRATION_SHA256,
        )
        self.assertEqual(
            compute_retirement_catalog_sha256(self.conn, SCHEMA), CATALOG_SHA256
        )
        self.assertEqual(
            verify_monday_forecast_v2_retirement_contract(self.conn), CATALOG_SHA256
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT value FROM meta WHERE key=%s", (f"migration:{MIGRATION_NAME}",)
            ).fetchone()[0],
            f"sha256:{MIGRATION_SHA256}",
        )
        self.conn.commit()
        self.assertEqual(
            apply_schema.apply_schema_connection(
                self.conn, DB_DIR, include_persistent_mapping=True
            ),
            [],
        )
        unmanifested = replace(
            apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE,
            version="unmanifested-test-release",
        )
        with self.conn.transaction(), self.assertRaisesRegex(
            RuntimeError, "not in the literal manifest"
        ):
            apply_schema._verify_or_apply_post_mapping_release(
                self.conn, DB_DIR, unmanifested
            )

    def test_changed_015_source_refuses_before_any_schema_effect(self):
        assert self.conn is not None
        self.conn.close()
        self.conn = None
        self._prepare_roles_and_schema()
        with TemporaryDirectory(prefix="buffalo-retirement-source-") as temporary:
            altered = Path(temporary) / "db"
            shutil.copytree(DB_DIR, altered)
            target = altered / MIGRATION_NAME
            target.write_bytes(target.read_bytes() + b"\n-- altered test byte\n")
            with psycopg.connect(self.mapping_url) as conn:
                with self.assertRaisesRegex(
                    RuntimeError,
                    "post-mapping application migration checksum differs",
                ):
                    apply_schema.apply_schema_connection(
                        conn, altered, include_persistent_mapping=True
                    )
                self.assertIsNone(
                    conn.execute(
                        "SELECT pg_catalog.to_regclass(%s)",
                        (f'"{SCHEMA}".meta',),
                    ).fetchone()[0]
                )

    def test_marker_and_catalog_tamper_fail_with_typed_refusal(self):
        assert self.conn is not None
        self.conn.execute(
            "ALTER TABLE monday_stale_forecast_retirements "
            "DISABLE TRIGGER trg_validate_monday_stale_forecast_retirement"
        )
        with self.assertRaises(MondayForecastRetirementContractError):
            verify_monday_forecast_v2_retirement_contract(self.conn)
        self.conn.rollback()
        self.assertEqual(
            verify_monday_forecast_v2_retirement_contract(self.conn), CATALOG_SHA256
        )
        self.conn.execute(
            "SET search_path TO pg_catalog"
        )
        self.assertEqual(
            verify_monday_forecast_v2_retirement_contract(
                self.conn, schema=SCHEMA
            ),
            CATALOG_SHA256,
        )
        self.conn.rollback()
        self.conn.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION {}."
                "assert_monday_forecast_v2_retirement_contract() RETURNS void "
                "LANGUAGE plpgsql SET search_path TO {},pg_catalog "
                "AS 'BEGIN RETURN; END'"
            ).format(sql.Identifier(SCHEMA), sql.Identifier(SCHEMA))
        )
        with self.assertRaisesRegex(
            MondayForecastRetirementContractError, "installed catalog differs"
        ):
            verify_monday_forecast_v2_retirement_contract(self.conn)
        self.conn.rollback()
        self.conn.execute(
            "UPDATE meta SET value='forged' WHERE key=%s",
            (f"migration:{MIGRATION_NAME}",),
        )
        with self.assertRaisesRegex(
            MondayForecastRetirementContractError, "marker differs"
        ):
            verify_monday_forecast_v2_retirement_contract(self.conn)
        self.conn.rollback()
        self._reset_before_015()
        assert self.conn is not None
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO meta(key,value) VALUES (%s,%s)",
                (f"migration:{MIGRATION_NAME}", f"sha256:{MIGRATION_SHA256}"),
            )
        with self.assertRaisesRegex(
            MondayForecastRetirementContractError,
            "retirement contract metadata differs",
        ):
            with self.conn.transaction():
                apply_schema._verify_or_apply_post_mapping_release(
                    self.conn, DB_DIR
                )
        self.assertIsNone(
            self.conn.execute(
                "SELECT pg_catalog.to_regclass(%s)",
                (f'"{SCHEMA}".monday_stale_forecast_retirements',),
            ).fetchone()[0]
        )
        self.conn.rollback()
        self._reset_before_015()
        assert self.conn is not None
        with self.conn.transaction():
            self.conn.execute(
                "ALTER TABLE change_log ADD COLUMN evidence_json JSONB"
            )
        with self.assertRaisesRegex(
            RuntimeError, "partial post-mapping retirement contract exists"
        ):
            with self.conn.transaction():
                apply_schema._verify_or_apply_post_mapping_release(
                    self.conn, DB_DIR
                )
        self.assertIsNone(
            self.conn.execute(
                "SELECT value FROM meta WHERE key=%s",
                (f"migration:{MIGRATION_NAME}",),
            ).fetchone()
        )
        self.conn.rollback()
        self._reset_before_015()
        assert self.conn is not None
        with self.conn.transaction():
            legacy_nullable_mode_run, _ = self._insert_run(
                self.conn,
                model_version="EMERGENCY_TRANSPARENT_V1",
                business_date=date(2026, 9, 17),
                output_mode=None,
            )
        with self.assertRaisesRegex(
            psycopg.Error,
            "unsupported historical V1 Monday run blocks retirement migration",
        ):
            with self.conn.transaction():
                apply_schema._verify_or_apply_post_mapping_release(
                    self.conn, DB_DIR
                )
        self.assertIsNotNone(legacy_nullable_mode_run)
        self.assertIsNone(
            self.conn.execute(
                "SELECT value FROM meta WHERE key=%s",
                (f"migration:{MIGRATION_NAME}",),
            ).fetchone()
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT pg_catalog.to_regclass(%s)",
                (f'"{SCHEMA}".monday_stale_forecast_retirements',),
            ).fetchone()[0]
        )

    def test_preview_confirm_replay_and_business_date_release_are_exact(self):
        assert self.conn is not None
        preview = self._preview()
        self.assertEqual(
            (preview["prior_status"], preview["prior_workflow_stage"]),
            ("RUNNING", "PREPARING"),
        )
        self.assertEqual(
            (preview["purchase_order_count"], preview["artifact_count"]), (0, 0)
        )
        self.assertEqual(
            self.conn.execute("SELECT pg_catalog.txid_current_if_assigned()").fetchone()[0],
            None,
        )
        self.conn.commit()
        with self.assertRaisesRegex(
            MondayRecommendationError,
            "retirement preview changed; review and confirm again",
        ):
            confirm_monday_stale_forecast_retirement(
                self.conn,
                run_id=str(self.v1_run_id),
                actor=preview["actor"],
                reason=preview["reason"],
                expected_confirmation_sha256="b" * 64,
            )
        self.assertEqual(
            self.conn.execute(
                """SELECT status,workflow_stage,
                          (SELECT count(*) FROM monday_stale_forecast_retirements),
                          (SELECT count(*) FROM change_log WHERE evidence_json->>'contract'=
                              'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1')
                     FROM runs WHERE run_id=%s""",
                (self.v1_run_id,),
            ).fetchone(),
            ("RUNNING", "PREPARING", 0, 0),
        )
        self.conn.commit()
        result = confirm_monday_stale_forecast_retirement(
            self.conn,
            run_id=str(self.v1_run_id),
            actor=preview["actor"],
            reason=preview["reason"],
            expected_confirmation_sha256=preview["confirmation_sha256"],
        )
        self.assertFalse(result["idempotent_replay"])
        stored = self.conn.execute(
            """SELECT status,workflow_stage,pg_catalog.to_jsonb(r),
                      (SELECT count(*) FROM monday_stale_forecast_retirements e
                        WHERE e.run_id=r.run_id),
                      (SELECT count(*) FROM change_log c WHERE c.run_id=r.run_id
                        AND c.evidence_json->>'contract'=
                            'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1')
                 FROM runs r WHERE run_id=%s""",
            (self.v1_run_id,),
        ).fetchone()
        self.assertEqual((stored[0], stored[1], stored[3], stored[4]), ("FAILED", "FAILED", 1, 1))
        changed_keys = {
            key
            for key in preview["before_run_json"]
            if preview["before_run_json"][key] != stored[2][key]
        }
        self.assertEqual(changed_keys, {"status", "workflow_stage"})
        self.conn.commit()
        replay = confirm_monday_stale_forecast_retirement(
            self.conn,
            run_id=str(self.v1_run_id),
            actor=preview["actor"],
            reason=preview["reason"],
            expected_confirmation_sha256=preview["confirmation_sha256"],
        )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            (replay["transaction_id"], replay["created_at"]),
            (result["transaction_id"], result["created_at"]),
        )
        for label, actor, reason, confirmation in (
            (
                "changed actor",
                "synthetic:different-owner:01",
                preview["reason"],
                preview["confirmation_sha256"],
            ),
            (
                "changed reason",
                preview["actor"],
                "A different retirement reason",
                preview["confirmation_sha256"],
            ),
            (
                "changed confirmation",
                preview["actor"],
                preview["reason"],
                "c" * 64,
            ),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                MondayRecommendationError,
                "retirement request conflicts with the persisted event",
            ):
                confirm_monday_stale_forecast_retirement(
                    self.conn,
                    run_id=str(self.v1_run_id),
                    actor=actor,
                    reason=reason,
                    expected_confirmation_sha256=confirmation,
                )
        with self.assertRaisesRegex(
            MondayRecommendationError, "retirement confirmation hash is malformed"
        ):
            confirm_monday_stale_forecast_retirement(
                self.conn,
                run_id=str(self.v1_run_id),
                actor=preview["actor"],
                reason=preview["reason"],
                expected_confirmation_sha256="not-a-hash",
            )
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM monday_stale_forecast_retirements),
                          (SELECT count(*) FROM change_log WHERE evidence_json->>'contract'=
                              'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1')"""
            ).fetchone(),
            (1, 1),
        )
        self.conn.commit()
        with self.conn.transaction():
            replacement, _ = self._insert_run(
                self.conn,
                model_version="EMERGENCY_TRANSPARENT_V2",
                business_date=BUSINESS_DATE,
            )
        self.assertIsNotNone(replacement)

    def test_event_audit_and_run_transition_are_atomic_and_append_only(self):
        assert self.conn is not None
        preview = self._preview()
        self.conn.commit()
        with self.assertRaises(psycopg.Error):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO monday_stale_forecast_retirements(
                               run_id,input_fingerprint,retired_model_version,prior_status,
                               prior_workflow_stage,target_status,target_workflow_stage,
                               purchase_order_count,artifact_count,actor,reason,
                               confirmation_sha256,before_run_json,after_run_json)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s::jsonb,%s::jsonb)""",
                    (
                        self.v1_run_id,
                        preview["input_fingerprint"],
                        preview["retired_model_version"],
                        preview["prior_status"],
                        preview["prior_workflow_stage"],
                        preview["target_status"],
                        preview["target_workflow_stage"],
                        preview["actor"],
                        preview["reason"],
                        preview["confirmation_sha256"],
                        json.dumps(preview["before_run_json"], sort_keys=True),
                        json.dumps(preview["after_run_json"], sort_keys=True),
                    ),
                )
        self.assertEqual(
            self.conn.execute(
                """SELECT status,workflow_stage,
                          (SELECT count(*) FROM monday_stale_forecast_retirements)
                     FROM runs WHERE run_id=%s""",
                (self.v1_run_id,),
            ).fetchone(),
            ("RUNNING", "PREPARING", 0),
        )
        self.conn.rollback()
        with self.assertRaises(psycopg.Error):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE runs SET status='FAILED',workflow_stage='FAILED' WHERE run_id=%s",
                    (self.v1_run_id,),
                )
        with self.assertRaises(psycopg.Error):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO change_log(
                               table_name,row_key,action,before_json,after_json,actor,
                               run_id,evidence_json)
                        VALUES ('runs',%s,'UPDATE',%s::jsonb,%s::jsonb,%s,%s,%s::jsonb)""",
                    (
                        str(self.v1_run_id),
                        json.dumps(preview["before_run_json"], sort_keys=True),
                        json.dumps(preview["after_run_json"], sort_keys=True),
                        preview["actor"],
                        self.v1_run_id,
                        json.dumps(
                            {
                                "contract": "BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1",
                                "retirement_run_id": str(self.v1_run_id),
                            },
                            sort_keys=True,
                        ),
                    ),
                )
        result = confirm_monday_stale_forecast_retirement(
            self.conn,
            run_id=str(self.v1_run_id),
            actor=preview["actor"],
            reason=preview["reason"],
            expected_confirmation_sha256=preview["confirmation_sha256"],
        )
        self.assertFalse(result["idempotent_replay"])
        for statement in (
            "UPDATE monday_stale_forecast_retirements SET reason='changed' WHERE run_id=%s",
            "DELETE FROM monday_stale_forecast_retirements WHERE run_id=%s",
            "UPDATE change_log SET actor='changed' WHERE run_id=%s AND evidence_json->>'contract'='BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1'",
            "DELETE FROM change_log WHERE run_id=%s AND evidence_json->>'contract'='BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1'",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                with self.conn.transaction():
                    self.conn.execute(statement, (self.v1_run_id,))
        with self.conn.transaction():
            change_id = self.conn.execute(
                """INSERT INTO change_log(
                           table_name,row_key,action,before_json,after_json,actor,evidence_json)
                    VALUES ('synthetic','ordinary','UPDATE','{}','{}','synthetic:test','{}')
                    RETURNING change_id"""
            ).fetchone()[0]
            self.assertEqual(
                self.conn.execute(
                    "DELETE FROM change_log WHERE change_id=%s RETURNING change_id",
                    (change_id,),
                ).fetchone()[0],
                change_id,
            )

    def test_built_v1_packet_and_artifacts_remain_exactly_replayable(self):
        assert self.conn is not None
        self._reset_before_015()
        vendor_id = UUID("00000000-0000-4000-8000-000000000088")
        variant_id = "retirement-built-v1-variant"
        actor = "synthetic:built-v1-reviewer:01"
        review_evidence = {
            "review": {
                "approved_case_price": "1.0000",
                "approved_merchandise_total": "1.00",
                "approved_loose_order_fee": "0.00",
                "resulting_inventory_units": "1.0000",
                "resulting_days_supply": "1.00",
                "days_supply_status": "SYNTHETIC",
                "actor": actor,
            }
        }
        recommendation_metrics = {
            "frozen_vendor_name": "Synthetic built V1 vendor",
            "frozen_offer_evidence": {"source": "fabricated-pre-015"},
            "frozen_open_po_position": {"open_units": "0.0000"},
        }
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO vendors(vendor_id,vendor_name,active) VALUES (%s,%s,TRUE)",
                (vendor_id, "Synthetic built V1 vendor"),
            )
            self.conn.execute(
                """INSERT INTO vendor_operating_rules(
                           vendor_id,order_days,order_cutoff_local,timezone_name,
                           expected_delivery_days,order_cycle_days,lead_time_days,
                           lead_time_variability_days,reliability_pct,minimum_type,
                           minimum_value,below_minimum_fee,loose_order_allowed,
                           loose_unit_fee,confirmation_source,confirmed_by,rules_version)
                    VALUES (%s,ARRAY['MONDAY'],'12:00','America/New_York',
                            ARRAY['THURSDAY'],2,1,0,1,'NONE',NULL,0,FALSE,NULL,
                            'fabricated-pre-015','synthetic:test-owner',1)""",
                (vendor_id,),
            )
            self.conn.execute(
                """INSERT INTO variants(
                           variant_id,product_id,product_title,variant_title,active,
                           catalog_state,identity_scope,sku)
                    VALUES (%s,%s,%s,'750ML',TRUE,'LIVE','CURRENT',%s)""",
                (
                    variant_id,
                    "retirement-built-v1-product",
                    "Synthetic built V1 product",
                    "SYN-BUILT-V1",
                ),
            )
            offer_id = self.conn.execute(
                """INSERT INTO supplier_offers(
                           variant_id,vendor_id,supplier_sku,supplier_description,
                           package_type,size_text,raw_pack,shopify_units_per_case,
                           qualifying_units_per_case,assortment_scope,assortable,
                           active,confidence,source_file,source_page)
                    VALUES (%s,%s,'SYN-BUILT-V1','Synthetic built V1 offer',
                            'STANDARD','750ML','1x750ML',1,1,'PRODUCT',FALSE,
                            TRUE,'VERIFIED','fabricated-pre-015.csv',1)
                    RETURNING offer_id""",
                (variant_id, vendor_id),
            ).fetchone()[0]
            recommendation_id = self.conn.execute(
                """INSERT INTO procurement_recommendations(
                           run_id,variant_id,vendor_id,offer_id,baseline_units,
                           recommended_cases,recommended_loose_units,
                           recommended_unit_cost,reason_code,review_required,metrics,
                           input_fingerprint,recommendation_status,strategic_extra_units,
                           units_per_case,recommended_units,frozen_supplier_sku,
                           frozen_qualifying_units_per_case,frozen_loose_order_allowed,
                           frozen_loose_unit_fee,frozen_minimum_type,
                           frozen_minimum_value,frozen_below_minimum_fee)
                    VALUES (%s,%s,%s,%s,1,1,0,1,'SYNTHETIC_BUILT_V1',TRUE,%s,
                            %s,'READY_FOR_REVIEW',0,1,1,'SYN-BUILT-V1',1,FALSE,
                            0,'NONE',NULL,0)
                    RETURNING recommendation_id""",
                (
                    self.v1_run_id,
                    variant_id,
                    vendor_id,
                    offer_id,
                    json.dumps(recommendation_metrics, sort_keys=True),
                    self.v1_fingerprint,
                ),
            ).fetchone()[0]
            self.conn.execute(
                """INSERT INTO run_price_snapshots(
                           run_id,offer_id,price_state,effective_month,level_type,
                           case_price,unit_price,source_file,source_page)
                    VALUES (%s,%s,'current',%s,'BASE',1,1,
                            'fabricated-pre-015.csv',1)""",
                (self.v1_run_id, offer_id, BUSINESS_DATE.replace(day=1)),
            )
            self.conn.execute(
                "UPDATE runs SET workflow_stage='AWAITING_REVIEW' WHERE run_id=%s",
                (self.v1_run_id,),
            )
            decision_id = self.conn.execute(
                """INSERT INTO review_decisions(
                           run_id,recommendation_id,decision_type,scope,action,
                           comment,decided_by,input_fingerprint,decision_fingerprint,
                           approved_cases,approved_loose_units,approved_units,
                           approved_unit_cost,approved_line_total,evidence_json)
                    VALUES (%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY','ACCEPT',
                            'Synthetic built V1 approval',%s,%s,%s,1,0,1,1,1,%s)
                    RETURNING decision_id""",
                (
                    self.v1_run_id,
                    recommendation_id,
                    actor,
                    self.v1_fingerprint,
                    "a" * 64,
                    json.dumps(review_evidence, sort_keys=True),
                ),
            ).fetchone()[0]
            self.conn.execute(
                "UPDATE runs SET workflow_stage='REVIEWED' WHERE run_id=%s",
                (self.v1_run_id,),
            )
            build_evidence = {
                "safety_label": "TEST DATA — NOT FOR ORDERING",
                "line_count": 1,
                "has_loose": False,
                "minimum_disposition": "NOT_APPLICABLE",
                "minimum_shortfall": "0",
                "loose_order_fee_total": "0",
                "below_minimum_fee": "0",
                "draft_preview_fingerprint": "b" * 64,
                "economics_confirmed_by": actor,
                "readiness_by_variant": [],
            }
            po_id = self.conn.execute(
                """INSERT INTO purchase_orders(
                           run_id,vendor_id,po_status,merchandise_total,delivery_fee,
                           po_total,below_vendor_minimum,notes,input_fingerprint,
                           receipt_status,shopify_import_status,reconciliation_evidence)
                    VALUES (%s,%s,'DRAFT',1,0,1,FALSE,
                            'TEST DATA — NOT FOR ORDERING',%s,'UNKNOWN',
                            'NOT_IMPORTED',%s)
                    RETURNING po_id""",
                (
                    self.v1_run_id,
                    vendor_id,
                    self.v1_fingerprint,
                    json.dumps(build_evidence, sort_keys=True),
                ),
            ).fetchone()[0]
            self.conn.execute(
                """INSERT INTO purchase_order_lines(
                           po_id,variant_id,offer_id,recommendation_id,
                           review_decision_id,supplier_sku,cases,loose_units,
                           ordered_units,unit_cost,line_total,reason_code,comment,
                           input_fingerprint,line_status,reconciliation_status)
                    VALUES (%s,%s,%s,%s,%s,'SYN-BUILT-V1',1,0,1,1,1,
                            'HUMAN_REVIEWED_BASELINE','TEST DATA — NOT FOR ORDERING',
                            %s,'DRAFT','UNKNOWN')""",
                (
                    po_id,
                    variant_id,
                    offer_id,
                    recommendation_id,
                    decision_id,
                    self.v1_fingerprint,
                ),
            )
            self.conn.execute(
                "UPDATE runs SET workflow_stage='DRAFTS_BUILT' WHERE run_id=%s",
                (self.v1_run_id,),
            )
            before_run = self.conn.execute(
                "SELECT to_jsonb(r) FROM runs r WHERE run_id=%s",
                (self.v1_run_id,),
            ).fetchone()[0]
        with self.conn.transaction():
            self.assertTrue(
                apply_schema._verify_or_apply_post_mapping_release(
                    self.conn, DB_DIR
                )
            )
        with TemporaryDirectory() as storage_root:
            storage = LocalFilesystemStorage(storage_root)
            first = build_after_review(
                self.conn,
                storage=storage,
                run_id=str(self.v1_run_id),
                actor=actor,
            )
            first_rows = self.conn.execute(
                """SELECT monday_run_artifact_id,artifact_type,sha256,payload
                     FROM monday_run_artifacts WHERE run_id=%s
                     ORDER BY monday_run_artifact_id""",
                (self.v1_run_id,),
            ).fetchall()
            self.conn.commit()
            second = build_after_review(
                self.conn,
                storage=storage,
                run_id=str(self.v1_run_id),
                actor=actor,
            )
            second_rows = self.conn.execute(
                """SELECT monday_run_artifact_id,artifact_type,sha256,payload
                     FROM monday_run_artifacts WHERE run_id=%s
                     ORDER BY monday_run_artifact_id""",
                (self.v1_run_id,),
            ).fetchall()
            listed = list_monday_artifacts(self.conn, str(self.v1_run_id))
            self.assertEqual(first_rows, second_rows)
            self.assertEqual(len(first_rows), 2)
            self.assertEqual(len(listed), 2)
            rows_by_id = {int(row[0]): row for row in first_rows}
            for listed_item in listed:
                row = rows_by_id[listed_item["artifact_id"]]
                recovered = read_monday_artifact(
                    self.conn,
                    storage=storage,
                    run_id=str(self.v1_run_id),
                    artifact_id=listed_item["artifact_id"],
                )
                self.assertEqual(recovered["data"], bytes(row[3]))
                self.assertEqual(recovered["sha256"], row[2])
                self.assertEqual(hashlib.sha256(recovered["data"]).hexdigest(), row[2])
        after_run = self.conn.execute(
            "SELECT to_jsonb(r) FROM runs r WHERE run_id=%s",
            (self.v1_run_id,),
        ).fetchone()[0]
        self.assertEqual(
            {key for key in before_run if before_run[key] != after_run[key]},
            {"workflow_stage"},
        )
        self.assertEqual(after_run["workflow_stage"], "PACKET_BUILT")
        self.assertTrue(first["drafts"]["idempotent_replay"])
        self.assertFalse(first["packet"]["idempotent_replay"])
        self.assertTrue(second["drafts"]["idempotent_replay"])
        self.assertTrue(second["packet"]["idempotent_replay"])
        self.assertEqual((first["release_performed"], first["shopify_calls"]), (False, 0))
        self.assertEqual((second["release_performed"], second["shopify_calls"]), (False, 0))
        self.assertEqual(
            self.conn.execute(
                """SELECT
                         (SELECT count(*) FROM monday_packet_build_events WHERE run_id=%s),
                         (SELECT count(*) FROM monday_stale_forecast_retirements WHERE run_id=%s)""",
                (self.v1_run_id, self.v1_run_id),
            ).fetchone(),
            (1, 0),
        )

    def test_direct_v1_writes_refuse_while_v2_remains_available(self):
        assert self.conn is not None
        for label, statement, params in (
            (
                "run update",
                "UPDATE runs SET notes='changed' WHERE run_id=%s",
                (self.v1_run_id,),
            ),
            (
                "recommendation insert",
                "INSERT INTO procurement_recommendations(run_id) VALUES (%s)",
                (self.v1_run_id,),
            ),
            (
                "forecast insert",
                "INSERT INTO forecast_results(run_id) VALUES (%s)",
                (self.v1_run_id,),
            ),
            (
                "inventory insert",
                "INSERT INTO inventory_snapshots(run_id) VALUES (%s)",
                (self.v1_run_id,),
            ),
            (
                "price snapshot insert",
                "INSERT INTO run_price_snapshots(run_id) VALUES (%s)",
                (self.v1_run_id,),
            ),
            (
                "exception insert",
                "INSERT INTO exceptions(run_id,exception_type,severity,message) "
                "VALUES (%s,'SYNTHETIC','HIGH','must refuse')",
                (self.v1_run_id,),
            ),
            (
                "null-parent review insert",
                "INSERT INTO review_decisions("
                "run_id,recommendation_id,decision_type,scope,action,decided_by) "
                "VALUES (%s,NULL,'SYNTHETIC','RUN_ONLY','DEFER','synthetic:test')",
                (self.v1_run_id,),
            ),
            (
                "null-parent exclusion insert",
                "INSERT INTO monday_run_blocker_exclusions("
                "run_id,exception_id,variant_id,input_fingerprint,action,scope,actor,reason,evidence_json) "
                "VALUES (%s,NULL,'1001',%s,'ACKNOWLEDGE_AND_EXCLUDE','RUN_ONLY',"
                "'synthetic:test','must refuse','{\"test\":true}')",
                (self.v1_run_id, self.v1_fingerprint),
            ),
            (
                "null-parent material confirmation insert",
                "INSERT INTO monday_material_edit_confirmations("
                "run_id,recommendation_id,input_fingerprint,review_preview_fingerprint,"
                "approved_cases,approved_loose_units,approved_units,action,confirmed_by,reason,evidence_json) "
                "VALUES (%s,NULL,%s,%s,0,0,0,'CONFIRM_MATERIAL_EDIT',"
                "'synthetic:test','must refuse','{\"test\":true}')",
                (self.v1_run_id, self.v1_fingerprint, "a" * 64),
            ),
            (
                "purchase order insert",
                "INSERT INTO purchase_orders(run_id) VALUES (%s)",
                (self.v1_run_id,),
            ),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                psycopg.Error, "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"
            ):
                with self.conn.transaction():
                    self.conn.execute(statement, params)
        with self.assertRaisesRegex(
            psycopg.Error, "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"
        ):
            with self.conn.transaction():
                self._insert_run(
                    self.conn,
                    model_version="EMERGENCY_TRANSPARENT_V1",
                    business_date=date(2026, 9, 14),
                )
        with self.assertRaisesRegex(
            psycopg.Error, "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"
        ):
            with self.conn.transaction():
                self._insert_run(
                    self.conn,
                    model_version="EMERGENCY_TRANSPARENT_V1",
                    business_date=date(2026, 9, 15),
                    output_mode=None,
                )
        with self.conn.transaction():
            v2_run_id, _ = self._insert_run(
                self.conn,
                model_version="EMERGENCY_TRANSPARENT_V2",
                business_date=date(2026, 9, 14),
            )
            self.conn.execute(
                "UPDATE runs SET notes='permitted V2 update' WHERE run_id=%s",
                (v2_run_id,),
            )
            self.conn.execute(
                "INSERT INTO exceptions(run_id,exception_type,severity,message) "
                "VALUES (%s,'SYNTHETIC','HIGH','permitted V2 evidence')",
                (v2_run_id,),
            )
            nullable_mode_v2_run_id, _ = self._insert_run(
                self.conn,
                model_version="EMERGENCY_TRANSPARENT_V2",
                business_date=date(2026, 9, 16),
                output_mode=None,
            )
        with self.assertRaisesRegex(
            psycopg.Error, "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"
        ):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE runs SET model_version='EMERGENCY_TRANSPARENT_V1' "
                    "WHERE run_id=%s",
                    (nullable_mode_v2_run_id,),
                )
        self.assertEqual(
            self.conn.execute(
                "SELECT model_version,procurement_output_mode FROM runs WHERE run_id=%s",
                (nullable_mode_v2_run_id,),
            ).fetchone(),
            ("EMERGENCY_TRANSPARENT_V2", None),
        )
        self._reset_before_015()
        assert self.conn is not None
        with self.conn.transaction():
            parents = self._seed_v1_guard_parents_before_015()
        with self.conn.transaction():
            self.assertTrue(
                apply_schema._verify_or_apply_post_mapping_release(
                    self.conn, DB_DIR
                )
            )
        with self.conn.transaction():
            v2_run_id, v2_fingerprint = self._insert_run(
                self.conn,
                model_version="EMERGENCY_TRANSPARENT_V2",
                business_date=date(2026, 9, 14),
            )
        with self.assertRaisesRegex(
            MondayRecommendationError,
            "only an active unbuilt EMERGENCY_TRANSPARENT_V1 run can be retired",
        ):
            self._preview()
        for label, statement, params in (
            (
                "frozen recommendation update",
                "UPDATE procurement_recommendations SET metrics='{\"changed\":true}' "
                "WHERE recommendation_id=%s",
                (parents["recommendation_id"],),
            ),
            (
                "frozen recommendation delete",
                "DELETE FROM procurement_recommendations WHERE recommendation_id=%s",
                (parents["recommendation_id"],),
            ),
            (
                "frozen exception update",
                "UPDATE exceptions SET message='changed' WHERE exception_id=%s",
                (parents["exception_id"],),
            ),
            (
                "frozen exception delete",
                "DELETE FROM exceptions WHERE exception_id=%s",
                (parents["exception_id"],),
            ),
            (
                "line-linked exception insert",
                "INSERT INTO exceptions("
                "run_id,exception_type,severity,variant_id,po_line_id,message) "
                "VALUES (NULL,'SYNTHETIC_V1_LINE_BYPASS','HIGH',%s,%s,'must refuse')",
                (parents["variant_id"], parents["po_line_id"]),
            ),
            (
                "frozen line-linked exception update",
                "UPDATE exceptions SET message='changed' WHERE exception_id=%s",
                (parents["line_exception_id"],),
            ),
            (
                "frozen line-linked exception delete",
                "DELETE FROM exceptions WHERE exception_id=%s",
                (parents["line_exception_id"],),
            ),
            (
                "frozen review delete",
                "DELETE FROM review_decisions WHERE decision_id=%s",
                (parents["review_decision_id"],),
            ),
            (
                "frozen purchase-order update",
                "UPDATE purchase_orders SET notes='changed' WHERE po_id=%s",
                (parents["po_id"],),
            ),
            (
                "frozen purchase-order delete",
                "DELETE FROM purchase_orders WHERE po_id=%s",
                (parents["po_id"],),
            ),
            (
                "authoritative recommendation parent beats declared V2 review run",
                "INSERT INTO review_decisions("
                "run_id,recommendation_id,decision_type,scope,action,decided_by) "
                "VALUES (%s,%s,'SYNTHETIC','RUN_ONLY','DEFER','synthetic:test')",
                (v2_run_id, parents["recommendation_id"]),
            ),
            (
                "authoritative blocker parent beats declared V2 exclusion run",
                "INSERT INTO monday_run_blocker_exclusions("
                "run_id,exception_id,variant_id,input_fingerprint,action,scope,actor,reason,evidence_json) "
                "VALUES (%s,%s,%s,%s,'ACKNOWLEDGE_AND_EXCLUDE','RUN_ONLY',"
                "'synthetic:test','must refuse','{\"test\":true}')",
                (
                    v2_run_id,
                    parents["exception_id"],
                    parents["variant_id"],
                    v2_fingerprint,
                ),
            ),
            (
                "authoritative recommendation parent beats declared V2 material run",
                "INSERT INTO monday_material_edit_confirmations("
                "run_id,recommendation_id,input_fingerprint,review_preview_fingerprint,"
                "approved_cases,approved_loose_units,approved_units,action,confirmed_by,reason,evidence_json) "
                "VALUES (%s,%s,%s,%s,0,0,0,'CONFIRM_MATERIAL_EDIT',"
                "'synthetic:test','must refuse','{\"test\":true}')",
                (
                    v2_run_id,
                    parents["recommendation_id"],
                    v2_fingerprint,
                    "a" * 64,
                ),
            ),
            (
                "purchase-order parent protects V1 line",
                "INSERT INTO purchase_order_lines(po_id,variant_id) VALUES (%s,%s)",
                (parents["po_id"], parents["variant_id"]),
            ),
            (
                "frozen purchase-order line update",
                "UPDATE purchase_order_lines SET comment='changed' WHERE po_line_id=%s",
                (parents["po_line_id"],),
            ),
            (
                "frozen purchase-order line delete",
                "DELETE FROM purchase_order_lines WHERE po_line_id=%s",
                (parents["po_line_id"],),
            ),
            (
                "purchase-order parent protects operational event",
                "INSERT INTO po_operational_events("
                "po_id,event_type,prior_status,new_status,evidence_json,recorded_by) "
                "VALUES (%s,'FINALIZATION_AUTHORITY','DRAFT','DRAFT',"
                "'{\"test\":true}','synthetic:test')",
                (parents["po_id"],),
            ),
            (
                "purchase-order line parent protects reconciliation event",
                "INSERT INTO po_reconciliation_events("
                "po_line_id,prior_received_units,new_received_units,"
                "prior_cancelled_units,new_cancelled_units,reconciliation_status,"
                "evidence_json,recorded_by) "
                "VALUES (%s,0,0,0,0,'OPEN','{\"test\":true}','synthetic:test')",
                (parents["po_line_id"],),
            ),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                psycopg.Error, "FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED"
            ):
                with self.conn.transaction():
                    self.conn.execute(statement, params)

    def test_concurrent_confirmation_has_one_effect_and_stable_replay(self):
        assert self.conn is not None
        preview = self._preview()
        self.conn.commit()
        barrier = Barrier(2)

        def worker() -> tuple[str, bool | str]:
            with psycopg.connect(self.mapping_url) as conn:
                barrier.wait()
                try:
                    result = confirm_monday_stale_forecast_retirement(
                        conn,
                        run_id=str(self.v1_run_id),
                        actor=preview["actor"],
                        reason=preview["reason"],
                        expected_confirmation_sha256=preview["confirmation_sha256"],
                    )
                    return "ok", bool(result["idempotent_replay"])
                except (MondayRecommendationError, psycopg.Error) as exc:
                    return "error", str(exc)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda _: worker(), range(2)))
        successes = [value for status, value in outcomes if status == "ok"]
        self.assertEqual(successes.count(False), 1)
        self.assertLessEqual(len(successes), 2)
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM monday_stale_forecast_retirements),
                          (SELECT count(*) FROM change_log WHERE evidence_json->>'contract'=
                              'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1')"""
            ).fetchone(),
            (1, 1),
        )
        self.conn.commit()
        replay = confirm_monday_stale_forecast_retirement(
            self.conn,
            run_id=str(self.v1_run_id),
            actor=preview["actor"],
            reason=preview["reason"],
            expected_confirmation_sha256=preview["confirmation_sha256"],
        )
        self.assertTrue(replay["idempotent_replay"])


if __name__ == "__main__":
    unittest.main()
