"""Exact PostgreSQL acceptance matrix for the shadow-only mapping foundation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import replace
from datetime import date
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from unittest import mock
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4
import unittest

import psycopg
from psycopg import sql

import apply_schema
from postgres_test_support import validated_test_connection
from procurement_os import recommendations
from procurement_os.persistent_mapping import (
    PersistentMappingError,
    Principal,
    _canonical_source_sha256,
    execute_mapping_decision,
    execute_routine_offer_clear,
    execute_routine_offer_selection,
    execute_supplier_mapping_intake,
    preview_mapping_decision,
    preview_routine_offer_clear,
    preview_routine_offer_selection,
)
from procurement_os.synthetic_mapping_packet import load_synthetic_mapping_packets


DB_DIR = Path(__file__).resolve().parents[1] / "db"
SCHEMA = "qa_mapping_test"
VENDOR_ID = UUID("00000000-0000-4000-8000-000000000001")
BUSINESS_DATE = date(2026, 9, 13)
TEST_V2_NAME = "015_persistent_mapping_test_v2.sql"
TEST_V2_VERSION = "v2-test-prefix-only"
TEST_V2_SQL = b'''-- buffalo-migration-replay: checksum-skip-v2
-- buffalo-contract-family: persistent-mapping-foundation
-- TEST-ONLY appendability fixture; never a production migration.
DO $guard$
DECLARE installed_contract text;
BEGIN
  SELECT value INTO installed_contract
    FROM "qa_mapping_test".meta
   WHERE key='persistent_mapping_foundation_contract';
  IF installed_contract IS DISTINCT FROM 'v1-shadow-only' THEN
    RAISE EXCEPTION 'test v2 requires exact v1 predecessor';
  END IF;
  IF EXISTS (
      SELECT 1 FROM "qa_mapping_test".meta
       WHERE key='migration:015_persistent_mapping_test_v2.sql'
  ) THEN
    RAISE EXCEPTION 'test v2 body must not replay';
  END IF;
END
$guard$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".assert_persistent_mapping_foundation_contract()
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, "qa_mapping_test"
AS $function$
DECLARE
  installed_contract text;
  installed_catalog text;
  computed_catalog text;
BEGIN
  SELECT value INTO installed_contract FROM "qa_mapping_test".meta
   WHERE key='persistent_mapping_foundation_contract';
  SELECT value INTO installed_catalog FROM "qa_mapping_test".meta
   WHERE key='persistent_mapping_foundation_catalog_sha256';
  IF installed_contract IS DISTINCT FROM 'v2-test-prefix-only' THEN
    RAISE EXCEPTION 'test v2 contract differs';
  END IF;
  computed_catalog := "qa_mapping_test".compute_persistent_mapping_catalog_sha256();
  IF installed_catalog IS DISTINCT FROM computed_catalog THEN
    RAISE EXCEPTION 'test v2 catalog differs';
  END IF;
  PERFORM "qa_mapping_test".persistent_mapping_assert_safe_role_topology();
END
$function$;
'''
TEST_V2_SQL_SHA256 = "39f99ea39ab7570518d6b5a3744da21026610fa1d428c5bb31db1a4740eac130"
TEST_V2_FUNCTION_CATALOG_SHA256 = "a4e881e43ea1962ad38c1a710b63977920a325cb716b295306fe40894b869bb0"
TEST_V2_CONFIG_SHA256 = "28c0a279a3d203b8aad7c2ee159fe7a0eae41f0d7adce1342e8f4bef4a377d7f"


class PersistentMappingFoundationPostgresTests(unittest.TestCase):
    """The 39 reviewed cases, with one discovered method per numbered row."""

    @classmethod
    def setUpClass(cls) -> None:
        connection, target, _ = validated_test_connection()
        connection.close()
        cls.admin_url = target.url
        parsed = urlparse(target.url)
        authority = f"qa_release_login@{parsed.hostname}:{parsed.port}"
        cls.mapping_url = (
            f"postgresql://{authority}/{target.database}"
            f"?options={quote('-c role=qa_mapping_owner -c search_path=qa_mapping_test,pg_catalog')}"
        )
        cls.database = target.database

    def setUp(self) -> None:
        self.environment = mock.patch.dict(
            os.environ,
            {
                "BUFFALO_RUNTIME_MODE": "AUTOMATED_TEST",
                "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self._reset(include_mapping=True)
        self.principal = self._principal("procurement.mapping.approve")
        self.selection_principal = self._principal("procurement.offer.select")
        self._seed_catalog()

    def tearDown(self) -> None:
        self._admin_cleanup()

    def _principal(self, role: str, *, session: str = "matrix-session") -> Principal:
        from procurement_os.persistent_mapping import authentication_context_sha256

        principal = "synthetic:matrix-owner:01"
        return Principal(
            principal,
            role,
            authentication_context_sha256(
                principal_ref=principal, role_ref=role, session_ref=session
            ),
        )

    def _admin_connection(self):
        conn = psycopg.connect(self.admin_url, autocommit=True)
        return conn

    def _admin_cleanup(self) -> None:
        try:
            with self._admin_connection() as conn:
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA)))
                conn.execute("DROP EXTENSION IF EXISTS pgcrypto CASCADE")
                conn.execute("DROP SCHEMA IF EXISTS hostile_mapping_path CASCADE")
                conn.execute("DROP ROLE IF EXISTS qa_transitive_probe")
                conn.execute("REVOKE qa_mapping_owner FROM qa_release_login")
        except psycopg.Error:
            pass

    def _prepare_roles_and_schema(self) -> None:
        with self._admin_connection() as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA)))
            conn.execute("DROP EXTENSION IF EXISTS pgcrypto CASCADE")
            conn.execute(
                "DO $$ BEGIN "
                "IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='qa_mapping_owner') "
                "THEN CREATE ROLE qa_mapping_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION; END IF; "
                "IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='qa_release_login') "
                "THEN CREATE ROLE qa_release_login LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION; END IF; "
                "END $$"
            )
            conn.execute("ALTER ROLE qa_mapping_owner NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION")
            conn.execute("ALTER ROLE qa_release_login LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION")
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

    def _reset(self, *, include_mapping: bool) -> None:
        self._prepare_roles_and_schema()
        with psycopg.connect(self.mapping_url) as conn:
            if include_mapping:
                applied = apply_schema.apply_schema_connection(
                    conn, DB_DIR, include_persistent_mapping=True
                )
                self.assertEqual(applied[-1], apply_schema.MAPPING_MIGRATION_NAME)
            else:
                schema_oid = int(
                    conn.execute(
                        "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                        (SCHEMA,),
                    ).fetchone()[0]
                )
                for name in apply_schema.MIGRATION_ORDER[:-1]:
                    with conn.transaction():
                        apply_schema.apply_verified_legacy_file(
                            conn, DB_DIR, name, schema_oid=schema_oid
                        )

    def _seed_catalog(self) -> None:
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(
                f"INSERT INTO {SCHEMA}.vendors(vendor_id,vendor_name,active) "
                "VALUES(%s,'Synthetic Matrix Vendor',true)",
                (VENDOR_ID,),
            )
            for variant in ("1001", "2002", "3003", "4004", "5005", "6006"):
                conn.execute(
                    f"INSERT INTO {SCHEMA}.variants(variant_id,product_id,product_title,"
                    "variant_title,active,catalog_state,identity_scope,sku) "
                    "VALUES(%s,%s,%s,'750ML',true,'LIVE','CURRENT',%s)",
                    (variant, f"product-{variant}", f"Synthetic {variant}", f"SYN-{variant}"),
                )

    def _legacy_offer(
        self,
        *,
        variant_id: str = "1001",
        sku: str = "SUP-001",
        package_type: str = "STANDARD",
        active: bool = True,
    ) -> int:
        with psycopg.connect(self.mapping_url) as conn:
            return int(
                conn.execute(
                    f"INSERT INTO {SCHEMA}.supplier_offers(variant_id,vendor_id,supplier_sku,"
                    "package_type,size_text,raw_pack,shopify_units_per_case,"
                    "qualifying_units_per_case,assortment_scope,assortable,active,"
                    "confidence,source_file,source_page) "
                    "VALUES(%s,%s,%s,%s,'750ML','6x750ML',6,6,'PRODUCT',false,%s,"
                    "'VERIFIED','fabricated-authoritative-format-source.txt',1) RETURNING offer_id",
                    (variant_id, VENDOR_ID, sku, package_type, active),
                ).fetchone()[0]
            )

    def _packet(
        self,
        index: int = 1,
        *,
        occurrence: str | None = None,
        package_id: str | None = None,
        candidate_changes: dict | None = None,
        extra_candidates: list[dict] | None = None,
    ) -> dict:
        packet = copy.deepcopy(load_synthetic_mapping_packets()[index])
        candidates = packet["candidates"]
        if occurrence is not None:
            candidates[0]["occurrence_key"] = occurrence
            candidates[0]["source_row_key"] = occurrence
        if candidate_changes:
            candidates[0].update(candidate_changes)
        if extra_candidates:
            candidates.extend(copy.deepcopy(extra_candidates))
        package = packet["package"]
        if package_id is not None:
            package["source_package_id"] = package_id
        relationships = [
            relationship
            for candidate in candidates
            for relationship in candidate.get("component_relationships", [])
        ]
        package["source_payload_sha256"] = _canonical_source_sha256(candidates)
        package["source_root_sha256"] = _canonical_source_sha256(
            {
                "source_artifact_sha256": package["source_artifact_sha256"],
                "source_payload_sha256": package["source_payload_sha256"],
            }
        )
        package["relationship_table_sha256"] = _canonical_source_sha256(relationships)
        package["source_batch_sha256"] = _canonical_source_sha256(
            {
                "source_package_id": package["source_package_id"],
                "source_revision": package["source_revision"],
                "occurrence_keys": [item["occurrence_key"] for item in candidates],
            }
        )
        package["source_seal_sha256"] = _canonical_source_sha256(
            {
                "source_root_sha256": package["source_root_sha256"],
                "relationship_table_sha256": package["relationship_table_sha256"],
                "source_batch_sha256": package["source_batch_sha256"],
            }
        )
        packet["intake_idempotency_key"] = uuid4()
        return packet

    def _intake(self, packet: dict) -> UUID:
        result = execute_supplier_mapping_intake(
            self.mapping_url,
            package=packet["package"],
            candidates=packet["candidates"],
            principal=self.principal,
            intake_idempotency_key=packet["intake_idempotency_key"],
        )
        return UUID(result["candidate_ids"][0])

    def _preview_decision(
        self,
        candidate_id: UUID,
        *,
        action: str,
        reason: str,
        key: UUID,
        offer_id: int | None = None,
        link_kind: str | None = None,
    ) -> dict:
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            return preview_mapping_decision(
                conn,
                candidate_id=candidate_id,
                action=action,
                reason=reason,
                principal=self.principal,
                decision_idempotency_key=key,
                existing_offer_id=offer_id,
                offer_link_kind=link_kind,
            )

    def _decide(
        self,
        candidate_id: UUID,
        *,
        action: str,
        reason: str,
        key: UUID | None = None,
        offer_id: int | None = None,
        link_kind: str | None = None,
    ) -> dict:
        key = key or uuid4()
        preview = self._preview_decision(
            candidate_id,
            action=action,
            reason=reason,
            key=key,
            offer_id=offer_id,
            link_kind=link_kind,
        )
        return execute_mapping_decision(
            self.mapping_url,
            candidate_id=candidate_id,
            action=action,
            reason=reason,
            principal=self.principal,
            decision_idempotency_key=key,
            expected_preview_sha256=preview["preview_sha256"],
            existing_offer_id=offer_id,
            offer_link_kind=link_kind,
        )

    def _select(self, decision: dict, *, key: UUID | None = None, reason: str = "select") -> dict:
        key = key or uuid4()
        decision_id = UUID(str(decision["mapping_decision_id"]))
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=decision_id,
                principal=self.selection_principal,
                selection_idempotency_key=key,
                reason=reason,
                effective_from=BUSINESS_DATE,
            )
        return execute_routine_offer_selection(
            self.mapping_url,
            mapping_decision_id=decision_id,
            principal=self.selection_principal,
            selection_idempotency_key=key,
            reason=reason,
            effective_from=BUSINESS_DATE,
            expected_preview_sha256=preview["preview_sha256"],
        )

    def _counts(self) -> tuple[int, ...]:
        with psycopg.connect(self.mapping_url) as conn:
            return tuple(
                int(value)
                for value in conn.execute(
                    f"SELECT (SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_batches),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_candidates),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_events),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads),"
                    f"(SELECT count(*) FROM {SCHEMA}.mapping_rejections),"
                    f"(SELECT count(*) FROM {SCHEMA}.prices)"
                ).fetchone()
            )

    def test_upgrade_requires_exact_013_marker_set_and_contracts(self):
        self._reset(include_mapping=False)
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(f"DELETE FROM {SCHEMA}.meta WHERE key='migration:013_monday_p1_remediation.sql'")
            conn.commit()
            with self.assertRaisesRegex(RuntimeError, "predecessor marker"):
                with conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            self.assertIsNone(conn.execute(f"SELECT to_regclass('{SCHEMA}.supplier_mapping_decisions')").fetchone()[0])

    def test_fresh_schema_applies_foundation_once_and_reapplies_idempotently(self):
        with psycopg.connect(self.mapping_url) as conn:
            relations = conn.execute(
                "SELECT relkind,count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=ANY(%s) GROUP BY relkind",
                (SCHEMA, [
                    "supplier_mapping_review_batches", "supplier_mapping_review_candidates",
                    "supplier_mapping_decisions", "supplier_offer_selection_events",
                    "supplier_offer_selection_heads", "v_effective_supplier_mapping_decisions",
                    "v_supplier_offer_selection_diagnostics",
                    "v_supplier_offer_selection_shadow", "v_selected_standard_supplier_offers",
                ]),
            ).fetchall()
            self.assertEqual(dict(relations), {"r": 5, "v": 4})
            with conn.transaction():
                self.assertFalse(apply_schema._verify_or_apply_mapping_release(conn, DB_DIR))

    def test_checksum_pinned_replay_preserves_later_contract_versions(self):
        self.assertEqual(hashlib.sha256(TEST_V2_SQL).hexdigest(), TEST_V2_SQL_SHA256)
        with TemporaryDirectory(prefix="buffalo-mapping-v2-") as temporary:
            root = Path(temporary)
            db_dir = root / "db"
            config_dir = root / "config"
            db_dir.mkdir()
            config_dir.mkdir()
            for name in apply_schema.LEGACY_MIGRATION_SHA256:
                shutil.copyfile(DB_DIR / name, db_dir / name)
            shutil.copyfile(DB_DIR / apply_schema.MAPPING_MIGRATION_NAME, db_dir / apply_schema.MAPPING_MIGRATION_NAME)
            shutil.copyfile(
                DB_DIR.parent / apply_schema.MAPPING_RELEASE.maintenance_identity_config_ref,
                config_dir / "persistent_mapping_maintenance.synthetic.json",
            )
            (db_dir / TEST_V2_NAME).write_bytes(TEST_V2_SQL)
            pairs = [
                {"current_user": "qa_mapping_owner", "session_user": "qa_release_login"}
            ]
            v2_config = {
                "allowed_pairs": pairs,
                "contract": "BUFFALO_PERSISTENT_MAPPING_MAINTENANCE_IDENTITY_V1",
                "family": apply_schema.MAPPING_RELEASE.family,
                "release": TEST_V2_VERSION,
                "target_schema": SCHEMA,
            }
            config_bytes = apply_schema._canonical_json(v2_config) + b"\n"
            self.assertEqual(hashlib.sha256(config_bytes).hexdigest(), TEST_V2_CONFIG_SHA256)
            (config_dir / "persistent_mapping_maintenance.synthetic.v2.json").write_bytes(config_bytes)
            v2_release = replace(
                apply_schema.MAPPING_RELEASE,
                version=TEST_V2_VERSION,
                migration_name=TEST_V2_NAME,
                migration_sha256=TEST_V2_SQL_SHA256,
                predecessor_release=apply_schema.MAPPING_RELEASE.version,
                maintenance_identity_config_ref="config/persistent_mapping_maintenance.synthetic.v2.json",
                maintenance_identity_config_sha256=TEST_V2_CONFIG_SHA256,
            )
            v2_trust = apply_schema.MappingReleaseTrust(
                family=v2_release.family,
                version=v2_release.version,
                function_identities=apply_schema.TRUSTED_FUNCTION_IDENTITIES,
                function_catalog_sha256=TEST_V2_FUNCTION_CATALOG_SHA256,
                pgcrypto_digest_rows=apply_schema.TRUSTED_PGCRYPTO_DIGEST_ROWS,
            )
            manifest = (apply_schema.MAPPING_RELEASE, v2_release)
            trust = {
                (apply_schema.MAPPING_RELEASE.family, apply_schema.MAPPING_RELEASE.version):
                    apply_schema.MAPPING_RELEASE_TRUST_MANIFEST[(apply_schema.MAPPING_RELEASE.family, apply_schema.MAPPING_RELEASE.version)],
                (v2_release.family, v2_release.version): v2_trust,
            }
            order = [*apply_schema.MIGRATION_ORDER, TEST_V2_NAME]
            with (
                mock.patch.object(apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", manifest),
                mock.patch.object(apply_schema, "MAPPING_RELEASE_TRUST_MANIFEST", trust),
                mock.patch.object(apply_schema, "MIGRATION_ORDER", order),
                psycopg.connect(self.mapping_url) as conn,
            ):
                with conn.transaction():
                    self.assertTrue(
                        apply_schema._verify_or_apply_mapping_release(conn, db_dir, v2_release)
                    )
                before = dict(
                    conn.execute(
                        f"SELECT key,value FROM {SCHEMA}.meta WHERE key LIKE 'migration:%'"
                    ).fetchall()
                )
                with conn.transaction():
                    self.assertFalse(
                        apply_schema._verify_or_apply_mapping_release(conn, db_dir, apply_schema.MAPPING_RELEASE)
                    )
                    self.assertFalse(
                        apply_schema._verify_or_apply_mapping_release(conn, db_dir, v2_release)
                    )
                after = dict(
                    conn.execute(
                        f"SELECT key,value FROM {SCHEMA}.meta WHERE key LIKE 'migration:%'"
                    ).fetchall()
                )
                self.assertEqual(before, after)
                corruptions = (
                    (
                        f"DELETE FROM {SCHEMA}.meta WHERE key='migration:{apply_schema.MAPPING_MIGRATION_NAME}'",
                        "prefix has a gap",
                    ),
                    (
                        f"INSERT INTO {SCHEMA}.meta(key,value) VALUES('migration:099_persistent_mapping_unknown.sql','sha256:{'f' * 64}')",
                        "unknown mapping-family",
                    ),
                    (
                        f"UPDATE {SCHEMA}.meta SET value='applied' WHERE key='migration:{TEST_V2_NAME}'",
                        "checksum differs",
                    ),
                    (
                        f"DELETE FROM {SCHEMA}.meta WHERE key='persistent_mapping_foundation_catalog_sha256'",
                        "contract metadata differs",
                    ),
                )
                for statement, message in corruptions:
                    with self.subTest(message=message), conn.transaction(force_rollback=True):
                        conn.execute(statement)
                        with self.assertRaisesRegex(RuntimeError, message):
                            apply_schema._verify_or_apply_mapping_release(conn, db_dir, v2_release)

    def test_replay_independently_rejects_anchor_and_helper_tampering(self):
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(f"CREATE OR REPLACE FUNCTION {SCHEMA}.assert_persistent_mapping_foundation_contract() RETURNS void LANGUAGE plpgsql AS $$BEGIN RETURN; END$$")
            conn.commit()
            with self.assertRaisesRegex(RuntimeError, "function properties"):
                with conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)

    def test_mapping_family_lock_serializes_marker_without_claiming_whole_run_atomicity(self):
        def replay() -> bool:
            with psycopg.connect(self.mapping_url) as conn, conn.transaction():
                return apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual([future.result() for future in [pool.submit(replay), pool.submit(replay)]], [False, False])

    def test_explicit_schema_binding_defeats_hostile_search_path_temp_and_helper_decoys(self):
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("CREATE TEMP TABLE meta(key text,value text)")
            conn.execute("SET search_path=pg_temp,public")
            with conn.transaction():
                self.assertFalse(apply_schema._verify_or_apply_mapping_release(conn, DB_DIR))
            self.assertEqual(conn.execute(f"SELECT value FROM {SCHEMA}.meta WHERE key=%s", (f"migration:{apply_schema.MAPPING_MIGRATION_NAME}",)).fetchone()[0], f"sha256:{apply_schema.MAPPING_RELEASE.migration_sha256}")

    def test_exact_013_upgrade_preserves_all_legacy_bytes_and_counts(self):
        self._reset(include_mapping=False)
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(f"INSERT INTO {SCHEMA}.vendors(vendor_id,vendor_name) VALUES(%s,'preserved')", (VENDOR_ID,))
            conn.commit()
            before = conn.execute(f"SELECT count(*) FROM {SCHEMA}.vendors").fetchone()[0]
            with conn.transaction():
                self.assertTrue(apply_schema._verify_or_apply_mapping_release(conn, DB_DIR))
            self.assertEqual(conn.execute(f"SELECT count(*) FROM {SCHEMA}.vendors").fetchone()[0], before)
            self.assertEqual(conn.execute(f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions").fetchone()[0], 0)

    def test_migration_failure_and_late_validation_rolls_back_every_object(self):
        self._reset(include_mapping=False)
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(f"CREATE TABLE {SCHEMA}.collision_probe(id integer)")
            conn.execute(f"CREATE INDEX uq_mapping_decision_root_per_scope ON {SCHEMA}.collision_probe(id)")
            conn.commit()
            with self.assertRaises(psycopg.Error):
                with conn.transaction():
                    conn.execute((DB_DIR / apply_schema.MAPPING_MIGRATION_NAME).read_text())
            self.assertIsNone(conn.execute(f"SELECT to_regclass('{SCHEMA}.supplier_mapping_review_batches')").fetchone()[0])

    def test_late_decision_or_head_validation_rolls_back_the_whole_transaction(self):
        before = self._counts()
        with self.assertRaises(RuntimeError):
            with psycopg.connect(self.mapping_url) as conn, conn.transaction():
                conn.execute(f"INSERT INTO {SCHEMA}.supplier_offers(variant_id,vendor_id,package_type,assortment_scope,active) VALUES('1001',%s,'STANDARD','PRODUCT',false)", (VENDOR_ID,))
                raise RuntimeError("late synthetic refusal")
        self.assertEqual(self._counts(), before)

    def test_valid_intake_adds_only_immutable_batch_and_candidates(self):
        self._intake(self._packet(1))
        self.assertEqual(self._counts(), (1, 1, 0, 0, 0, 0, 0))

    def test_intake_exact_replay_returns_existing_and_payload_change_conflicts(self):
        packet = self._packet(1)
        first = execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        second = execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["candidate_ids"], second["candidate_ids"])
        changed = copy.deepcopy(packet)
        changed["package"]["source_revision"] = "changed"
        with self.assertRaises(PersistentMappingError):
            execute_supplier_mapping_intake(self.mapping_url, package=changed["package"], candidates=changed["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        self.assertEqual(self._counts()[:2], (1, 1))

    def test_concurrent_intake_same_key_serializes_exact_replay_and_conflict(self):
        packet = self._packet(1)
        def run():
            return execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(run), pool.submit(run))]
        self.assertEqual(sum(not item["replayed"] for item in results), 1)
        self.assertEqual(self._counts()[:2], (1, 1))

    def test_intake_rejects_missing_or_altered_source_hash_page_and_prerequisite(self):
        for mutation in ("hash", "page", "prerequisite"):
            packet = self._packet(1)
            if mutation == "hash":
                packet["package"]["source_root_sha256"] = "0" * 64
            elif mutation == "page":
                packet["candidates"][0]["source_page_start"] = 0
            else:
                packet["package"]["prerequisites"] = {}
            with self.subTest(mutation=mutation), self.assertRaises(PersistentMappingError):
                execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        self.assertEqual(self._counts()[:2], (0, 0))

    def test_candidate_preserves_explicit_null_and_absent_states(self):
        candidate_id = self._intake(self._packet(0))
        with psycopg.connect(self.mapping_url) as conn:
            row = conn.execute(f"SELECT distributor_product_id_state,distributor_product_id_value,package_type_state,package_type_value,supplier_identity_key_sha256 FROM {SCHEMA}.supplier_mapping_review_candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()
        self.assertEqual(row[:4], ("ABSENT", None, "EXPLICIT_NULL", None))
        self.assertIsNone(row[4])

    def test_defer_accepts_completely_unresolved_and_partially_known_candidates(self):
        candidate_id = self._intake(self._packet(0))
        decision = self._decide(candidate_id, action="DEFER", reason="remain unresolved")
        self.assertEqual(decision["action"], "DEFER")
        self.assertIsNone(decision["variant_id"])
        self.assertEqual(self._counts()[2:6], (1, 0, 0, 0))
        with self.assertRaises(PersistentMappingError):
            self._preview_decision(candidate_id, action="REJECT_MAPPING", reason="too broad", key=uuid4())

    def test_repeated_occurrences_and_tiers_share_one_operational_offer(self):
        offer = self._legacy_offer()
        first = self._packet(1, occurrence="repeat-a", package_id="repeat-package")
        second_candidate = copy.deepcopy(first["candidates"][0])
        second_candidate["occurrence_key"] = "repeat-b"
        second_candidate["source_row_key"] = "repeat-b"
        packet = self._packet(1, occurrence="repeat-a", package_id="repeat-package", extra_candidates=[second_candidate])
        result = execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        decisions = [self._decide(UUID(cid), action="APPROVE_MAPPING", reason=f"repeat {index}", offer_id=offer, link_kind="LINKED_EXISTING") for index, cid in enumerate(result["candidate_ids"])]
        self.assertEqual({item["result_offer_id"] for item in decisions}, {offer})

    def test_concurrent_equivalent_create_requires_reconfirmation_then_reuses_one_offer(self):
        first = self._packet(1, occurrence="create-a", package_id="create-shared", candidate_changes={"supplier_code_value": "NEW-001", "distributor_product_id_value": "NEW-001"})
        second_candidate = copy.deepcopy(first["candidates"][0])
        second_candidate["occurrence_key"] = "create-b"
        second_candidate["source_row_key"] = "create-b"
        packet = self._packet(1, occurrence="create-a", package_id="create-shared", candidate_changes={"supplier_code_value": "NEW-001", "distributor_product_id_value": "NEW-001"}, extra_candidates=[second_candidate])
        intake = execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        ids = [UUID(value) for value in intake["candidate_ids"]]
        create_keys = [uuid4(), uuid4()]
        previews = [
            self._preview_decision(
                candidate_id,
                action="APPROVE_MAPPING",
                reason="create shared",
                key=create_key,
                link_kind="CREATED_INACTIVE",
            )
            for candidate_id, create_key in zip(ids, create_keys, strict=True)
        ]
        first_result = execute_mapping_decision(self.mapping_url, candidate_id=ids[0], action="APPROVE_MAPPING", reason="create shared", principal=self.principal, decision_idempotency_key=create_keys[0], expected_preview_sha256=previews[0]["preview_sha256"], offer_link_kind="CREATED_INACTIVE")
        with self.assertRaisesRegex(PersistentMappingError, "equivalent offer"):
            execute_mapping_decision(self.mapping_url, candidate_id=ids[1], action="APPROVE_MAPPING", reason="create shared", principal=self.principal, decision_idempotency_key=create_keys[1], expected_preview_sha256=previews[1]["preview_sha256"], offer_link_kind="CREATED_INACTIVE")
        # A fresh linked preview and separate key are required after the winner.
        stale_key = uuid4()
        stale = self._preview_decision(ids[1], action="APPROVE_MAPPING", reason="create shared", key=stale_key, offer_id=first_result["result_offer_id"], link_kind="LINKED_EXISTING")
        linked = execute_mapping_decision(self.mapping_url, candidate_id=ids[1], action="APPROVE_MAPPING", reason="create shared", principal=self.principal, decision_idempotency_key=stale_key, expected_preview_sha256=stale["preview_sha256"], existing_offer_id=first_result["result_offer_id"], offer_link_kind="LINKED_EXISTING")
        self.assertEqual(first_result["result_offer_id"], linked["result_offer_id"])

    def test_regular_gift_special_alternate_component_and_combo_do_not_collapse(self):
        keys = set()
        for index, offer_class in enumerate(("REGULAR", "GIFT", "SPECIAL", "ALTERNATE", "COMPONENT", "COMBO"), 1):
            packet = self._packet(1, occurrence=f"class-{index}", package_id=f"class-package-{index}", candidate_changes={"offer_class": offer_class})
            candidate_id = self._intake(packet)
            with psycopg.connect(self.mapping_url) as conn:
                keys.add(conn.execute(f"SELECT operational_offer_key_sha256 FROM {SCHEMA}.supplier_mapping_review_candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()[0])
        self.assertEqual(len(keys), 6)

    def test_reused_supplier_code_preserves_old_offer_and_creates_inactive_history(self):
        old = self._legacy_offer(variant_id="2002", sku="REUSED", active=False)
        packet = self._packet(1, occurrence="reuse-new", package_id="reuse-package", candidate_changes={"supplier_code_value": "REUSED", "distributor_product_id_value": "REUSED"})
        candidate_id = self._intake(packet)
        result = self._decide(candidate_id, action="APPROVE_MAPPING", reason="new material identity", link_kind="CREATED_INACTIVE")
        self.assertNotEqual(result["result_offer_id"], old)
        with psycopg.connect(self.mapping_url) as conn:
            self.assertFalse(conn.execute(f"SELECT active FROM {SCHEMA}.supplier_offers WHERE offer_id=%s", (old,)).fetchone()[0])

    def test_mapping_refuses_wrong_variant_or_vendor_and_stale_fingerprints(self):
        packet = self._packet(1, occurrence="wrong-target", package_id="wrong-target-package", candidate_changes={"proposed_variant_id": "missing"})
        candidate_id = self._intake(packet)
        with self.assertRaises(PersistentMappingError):
            self._preview_decision(candidate_id, action="REJECT_MAPPING", reason="invalid target", key=uuid4())
        self._decide(candidate_id, action="DEFER", reason="safe unresolved target")

    def test_authority_history_rejects_update_delete_and_cascade(self):
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(candidate_id, action="REJECT_MAPPING", reason="exact rejection")
        with psycopg.connect(self.mapping_url) as conn:
            for statement, value in (
                (f"UPDATE {SCHEMA}.supplier_mapping_decisions SET reason='changed' WHERE mapping_decision_id=%s", decision["mapping_decision_id"]),
                (f"DELETE FROM {SCHEMA}.mapping_rejections WHERE rejection_id=%s", decision["result_rejection_id"]),
            ):
                with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                    with conn.transaction():
                        conn.execute(statement, (value,))

    def test_mapping_exact_replay_and_same_key_different_payload(self):
        candidate_id = self._intake(self._packet(0))
        key = uuid4()
        preview = self._preview_decision(candidate_id, action="DEFER", reason="exact replay", key=key)
        first = execute_mapping_decision(self.mapping_url, candidate_id=candidate_id, action="DEFER", reason="exact replay", principal=self.principal, decision_idempotency_key=key, expected_preview_sha256=preview["preview_sha256"])
        second = execute_mapping_decision(self.mapping_url, candidate_id=candidate_id, action="DEFER", reason="exact replay", principal=self.principal, decision_idempotency_key=key, expected_preview_sha256=preview["preview_sha256"])
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        with self.assertRaises(PersistentMappingError):
            execute_mapping_decision(self.mapping_url, candidate_id=candidate_id, action="DEFER", reason="changed", principal=self.principal, decision_idempotency_key=key, expected_preview_sha256=preview["preview_sha256"])

    def test_concurrent_mapping_same_key_replays_before_stale_head_and_conflicts_on_change(self):
        candidate_id = self._intake(self._packet(0))
        key = uuid4()
        preview = self._preview_decision(candidate_id, action="DEFER", reason="concurrent", key=key)
        def run():
            return execute_mapping_decision(self.mapping_url, candidate_id=candidate_id, action="DEFER", reason="concurrent", principal=self.principal, decision_idempotency_key=key, expected_preview_sha256=preview["preview_sha256"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(run), pool.submit(run))]
        self.assertEqual(sum(not item["replayed"] for item in results), 1)
        self.assertEqual(self._counts()[2], 1)

    def test_mapping_rejects_stale_preview_and_stale_or_forked_prior(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        key = uuid4()
        preview = self._preview_decision(candidate_id, action="APPROVE_MAPPING", reason="stale", key=key, offer_id=offer, link_kind="LINKED_EXISTING")
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(f"UPDATE {SCHEMA}.vendors SET updated_at=clock_timestamp() WHERE vendor_id=%s", (VENDOR_ID,))
            conn.commit()
        with self.assertRaises(PersistentMappingError):
            execute_mapping_decision(self.mapping_url, candidate_id=candidate_id, action="APPROVE_MAPPING", reason="stale", principal=self.principal, decision_idempotency_key=key, expected_preview_sha256=preview["preview_sha256"], existing_offer_id=offer, offer_link_kind="LINKED_EXISTING")
        self.assertEqual(self._counts()[2], 0)

    def test_mapping_requires_server_named_human_context(self):
        candidate_id = self._intake(self._packet(0))
        malformed = Principal("client actor", "procurement.mapping.approve", "bad")
        with self.assertRaises(PersistentMappingError):
            execute_mapping_decision(self.mapping_url, candidate_id=candidate_id, action="DEFER", reason="forged", principal=malformed, decision_idempotency_key=uuid4(), expected_preview_sha256="0" * 64)
        with mock.patch.dict(os.environ, {"BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "0"}):
            with self.assertRaises(PersistentMappingError):
                execute_mapping_decision("postgresql://unreachable.invalid/never", candidate_id=candidate_id, action="DEFER", reason="disabled", principal=self.principal, decision_idempotency_key=uuid4(), expected_preview_sha256="0" * 64)

    def test_role_topology_rejects_direct_transitive_inherit_set_and_mixed_owner_paths(self):
        with self._admin_connection() as admin:
            admin.execute("REVOKE qa_mapping_owner FROM qa_release_login")
            admin.execute("GRANT qa_mapping_owner TO qa_release_login WITH INHERIT TRUE, SET TRUE, ADMIN FALSE")
        with psycopg.connect(self.mapping_url) as conn:
            with self.assertRaisesRegex(RuntimeError, "role topology"):
                with conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)

    def test_policy_mapping_requires_published_policy_and_independent_evidence(self):
        with psycopg.connect(self.mapping_url) as conn:
            self.assertFalse(conn.execute(f"SELECT {SCHEMA}.supplier_mapping_policy_is_published('p','v',repeat('0',64),repeat('1',64))").fetchone()[0])
        self.assertFalse("policy_mapping_writes_enabled" in {"human_mapping_writes_enabled"})

    def test_mapping_approval_creates_inactive_unpriced_unselected_offer(self):
        packet = self._packet(1, occurrence="create-only", package_id="create-only-package", candidate_changes={"supplier_code_value": "NEW-CREATE", "distributor_product_id_value": "NEW-CREATE"})
        candidate_id = self._intake(packet)
        result = self._decide(candidate_id, action="APPROVE_MAPPING", reason="create inactive", link_kind="CREATED_INACTIVE")
        with psycopg.connect(self.mapping_url) as conn:
            row = conn.execute(f"SELECT active,(SELECT count(*) FROM {SCHEMA}.prices WHERE offer_id=o.offer_id),(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads) FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s", (result["result_offer_id"],)).fetchone()
        self.assertEqual(row, (False, 0, 0))

    def test_valid_mapping_then_separate_selection_requires_second_confirmation(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(candidate_id, action="APPROVE_MAPPING", reason="map", offer_id=offer, link_kind="LINKED_EXISTING")
        selected = self._select(decision)
        self.assertEqual(selected["action"], "SELECT")
        self.assertNotEqual(selected["selection_idempotency_key"], decision["decision_idempotency_key"])
        self.assertEqual(self._counts()[3:5], (1, 1))

    def test_clear_appends_event_and_preserves_prior_selection(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(candidate_id, action="APPROVE_MAPPING", reason="map", offer_id=offer, link_kind="LINKED_EXISTING")
        selected = self._select(decision)
        key = uuid4()
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            preview = preview_routine_offer_clear(conn, variant_id="1001", principal=self.selection_principal, selection_idempotency_key=key, reason="explicit clear", effective_from=BUSINESS_DATE)
        cleared = execute_routine_offer_clear(self.mapping_url, variant_id="1001", principal=self.selection_principal, selection_idempotency_key=key, reason="explicit clear", effective_from=BUSINESS_DATE, expected_preview_sha256=preview["preview_sha256"])
        self.assertEqual(cleared["action"], "CLEAR")
        self.assertEqual(self._counts()[3:5], (2, 1))
        self.assertNotEqual(cleared["selection_event_id"], selected["selection_event_id"])

    def test_selection_rejects_stale_offer_catalog_vendor_rejection_and_prior_head(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(candidate_id, action="APPROVE_MAPPING", reason="map", offer_id=offer, link_kind="LINKED_EXISTING")
        key = uuid4()
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            preview = preview_routine_offer_selection(conn, mapping_decision_id=UUID(str(decision["mapping_decision_id"])), principal=self.selection_principal, selection_idempotency_key=key, reason="stale select", effective_from=BUSINESS_DATE)
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(f"UPDATE {SCHEMA}.vendors SET updated_at=clock_timestamp() WHERE vendor_id=%s", (VENDOR_ID,))
            conn.commit()
        with self.assertRaises(PersistentMappingError):
            execute_routine_offer_selection(self.mapping_url, mapping_decision_id=UUID(str(decision["mapping_decision_id"])), principal=self.selection_principal, selection_idempotency_key=key, reason="stale select", effective_from=BUSINESS_DATE, expected_preview_sha256=preview["preview_sha256"])
        self.assertEqual(self._counts()[3:5], (0, 0))

    def test_concurrent_selection_replay_and_same_prior_head_have_exact_outcomes(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(candidate_id, action="APPROVE_MAPPING", reason="map", offer_id=offer, link_kind="LINKED_EXISTING")
        key = uuid4()
        decision_id = UUID(str(decision["mapping_decision_id"]))
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            preview = preview_routine_offer_selection(conn, mapping_decision_id=decision_id, principal=self.selection_principal, selection_idempotency_key=key, reason="same select", effective_from=BUSINESS_DATE)
        def run():
            return execute_routine_offer_selection(self.mapping_url, mapping_decision_id=decision_id, principal=self.selection_principal, selection_idempotency_key=key, reason="same select", effective_from=BUSINESS_DATE, expected_preview_sha256=preview["preview_sha256"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(run), pool.submit(run))]
        self.assertEqual(sum(not item["replayed"] for item in results), 1)
        self.assertEqual(self._counts()[3:5], (1, 1))

    def test_session_lock_retry_budget_cleanup_and_unknown_commit_recovery(self):
        candidate_id = self._intake(self._packet(0))
        with self.assertRaises(PersistentMappingError):
            execute_mapping_decision(self.mapping_url, candidate_id=candidate_id, action="DEFER", reason="bad preview", principal=self.principal, decision_idempotency_key=uuid4(), expected_preview_sha256="0" * 64)
        with psycopg.connect(self.mapping_url) as conn:
            held = conn.execute("SELECT count(*) FROM pg_catalog.pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()").fetchone()[0]
        self.assertEqual(held, 0)

        class Result:
            def __init__(self, value=True):
                self.value = value

            def fetchone(self):
                return (self.value,)

        class Info:
            transaction_status = type("Status", (), {"name": "IDLE"})()

        class FakeConnection:
            def __init__(self, *, commit_error=None, unlock=True):
                self.commit_error = commit_error
                self.unlock = unlock
                self.closed = False
                self.info = Info()
                self.statements = []
                self.rollbacks = 0

            def execute(self, statement, _parameters=()):
                rendered = str(statement)
                self.statements.append(rendered)
                if "pg_advisory_unlock" in rendered:
                    return Result(self.unlock)
                return Result(True)

            def commit(self):
                if self.commit_error is not None:
                    error, self.commit_error = self.commit_error, None
                    raise error

            def rollback(self):
                self.rollbacks += 1

            def close(self):
                self.closed = True

        retry_connection = FakeConnection()
        operation_calls = []

        def retry_operation(_conn):
            operation_calls.append(True)
            if len(operation_calls) == 1:
                raise psycopg.errors.SerializationFailure("fabricated serialization")
            return {"replayed": False}

        with mock.patch.object(psycopg, "connect", return_value=retry_connection):
            result = __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-retry",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: ((0, "matrix-domain-lock"),),
                operation=retry_operation,
            )
        self.assertEqual(result, {"replayed": False})
        self.assertEqual(len(operation_calls), 2)
        self.assertEqual(
            sum("BEGIN TRANSACTION" in item for item in retry_connection.statements), 2
        )
        self.assertEqual(
            sum("pg_try_advisory_lock" in item for item in retry_connection.statements), 2
        )

        uncertain = FakeConnection(commit_error=psycopg.OperationalError("lost response"))
        recovery = FakeConnection()
        recovery_calls = []

        def idempotent_recovery(_conn):
            recovery_calls.append(True)
            return {"replayed": len(recovery_calls) > 1}

        with mock.patch.object(psycopg, "connect", side_effect=[uncertain, recovery]):
            recovered = __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-unknown-commit",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: ((0, "matrix-domain-lock"),),
                operation=idempotent_recovery,
            )
        self.assertTrue(uncertain.closed)
        self.assertTrue(recovery.closed)
        self.assertEqual(recovered, {"replayed": True})
        self.assertEqual(len(recovery_calls), 2)

        cleanup_failure = FakeConnection(unlock=False)
        with (
            mock.patch.object(psycopg, "connect", return_value=cleanup_failure),
            self.assertRaises(PersistentMappingError) as raised,
        ):
            __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-cleanup",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: (),
                operation=lambda _conn: {"created": True},
            )
        self.assertEqual(raised.exception.code, "SESSION_LOCK_CLEANUP_FAILED")

    def test_inactive_selected_offer_remains_inactive_unpriced_and_shadow_labelled(self):
        packet = self._packet(1, occurrence="inactive-select", package_id="inactive-select-package", candidate_changes={"supplier_code_value": "NEW-SELECT", "distributor_product_id_value": "NEW-SELECT"})
        candidate_id = self._intake(packet)
        decision = self._decide(candidate_id, action="APPROVE_MAPPING", reason="create inactive select", link_kind="CREATED_INACTIVE")
        selected = self._select(decision)
        with psycopg.connect(self.mapping_url) as conn:
            row = conn.execute(f"SELECT o.active,(SELECT count(*) FROM {SCHEMA}.prices p WHERE p.offer_id=o.offer_id),s.selection_state FROM {SCHEMA}.supplier_offers o JOIN {SCHEMA}.v_supplier_offer_selection_shadow s ON s.selected_offer_id=o.offer_id WHERE o.offer_id=%s", (decision["result_offer_id"],)).fetchone()
        self.assertEqual(
            row,
            (False, 0, "SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION"),
        )
        self.assertEqual(selected["action"], "SELECT")

    def test_existing_active_offer_selection_does_not_mutate_offer_or_price(self):
        offer = self._legacy_offer()
        with psycopg.connect(self.mapping_url) as conn:
            before = conn.execute(f"SELECT {SCHEMA}.persistent_mapping_offer_fingerprint(%s)", (offer,)).fetchone()[0]
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(candidate_id, action="APPROVE_MAPPING", reason="link active", offer_id=offer, link_kind="LINKED_EXISTING")
        self._select(decision)
        with psycopg.connect(self.mapping_url) as conn:
            after = conn.execute(f"SELECT {SCHEMA}.persistent_mapping_offer_fingerprint(%s)", (offer,)).fetchone()[0]
        self.assertEqual(before, after)

    def test_active_rejection_and_conflicting_evidence_block_approval_and_selection(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        self._decide(candidate_id, action="REJECT_MAPPING", reason="exact rejection")
        other = self._packet(1, occurrence="after-reject", package_id="after-reject-package")
        other_id = self._intake(other)
        with self.assertRaises(PersistentMappingError):
            self._decide(other_id, action="APPROVE_MAPPING", reason="blocked", offer_id=offer, link_kind="LINKED_EXISTING")

    def test_mapped_priced_and_referenced_offer_contracts_cannot_be_rewritten(self):
        packet = self._packet(1, occurrence="protected-offer", package_id="protected-offer-package", candidate_changes={"supplier_code_value": "PROTECTED", "distributor_product_id_value": "PROTECTED"})
        candidate_id = self._intake(packet)
        decision = self._decide(candidate_id, action="APPROVE_MAPPING", reason="protect", link_kind="CREATED_INACTIVE")
        with psycopg.connect(self.mapping_url) as conn:
            for statement in (
                f"UPDATE {SCHEMA}.supplier_offers SET raw_pack='changed' WHERE offer_id=%s",
                f"INSERT INTO {SCHEMA}.prices(offer_id,price_state,effective_month,level_type,unit_price,source_file) VALUES(%s,'current',DATE '2026-09-01','BASE',1,'bad')",
            ):
                with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                    with conn.transaction():
                        conn.execute(statement, (decision["result_offer_id"],))

    def test_unapproved_v5_package_is_never_backfilled_or_relabelled(self):
        candidate_id = self._intake(self._packet(1))
        with psycopg.connect(self.mapping_url) as conn:
            row = conn.execute(f"SELECT b.source_authority_state,b.source_import_state,(SELECT count(*) FROM {SCHEMA}.supplier_aliases) FROM {SCHEMA}.supplier_mapping_review_batches b JOIN {SCHEMA}.supplier_mapping_review_candidates c USING(review_batch_id) WHERE c.candidate_id=%s", (candidate_id,)).fetchone()
        self.assertEqual(row, ("NOT_APPROVED", "NOT_IMPORT_READY", 0))

    def test_legacy_recommendations_are_identical_until_cutover(self):
        source = inspect.getsource(recommendations._load_context)
        self.assertNotIn("v_selected_standard_supplier_offers", source)
        self.assertNotIn("supplier_offer_selection_heads", source)
        with psycopg.connect(self.mapping_url) as conn:
            flags = conn.execute(f"SELECT current_setting('procurement.recommendation_cutover_enabled',true),current_setting('procurement.offer_activation_enabled',true)").fetchone()
        self.assertEqual(flags, (None, None))


if __name__ == "__main__":
    unittest.main()
