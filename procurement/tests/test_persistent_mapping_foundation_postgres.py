"""Exact PostgreSQL acceptance matrix for the shadow-only mapping foundation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import copy
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import mock
from urllib.parse import quote, urlparse
from uuid import UUID, uuid4
import unittest

import psycopg
from psycopg import sql

import apply_schema
from postgres_test_support import validated_test_connection
from procurement_os import persistent_mapping as mapping_service
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
TEST_V2_FUNCTION_CATALOG_SHA256 = "e6df71df1878487b548b2a72e987db6f5a055e7bed1e297bb6c347a90d836c4a"
TEST_V2_CONFIG_SHA256 = "28c0a279a3d203b8aad7c2ee159fe7a0eae41f0d7adce1342e8f4bef4a377d7f"

ROTATION_LOGIN = "qa_release_login_next"
ROTATION_STAGE_VERSION = "v2-test-maintenance-pair-stage"
ROTATION_STAGE_NAME = "015_persistent_mapping_test_rotation_stage.sql"
ROTATION_REMOVE_VERSION = "v3-test-maintenance-pair-remove"
ROTATION_REMOVE_NAME = "016_persistent_mapping_test_rotation_remove.sql"
ROTATION_DISJOINT_VERSION = "v2-test-maintenance-pair-disjoint"


def _rotation_config(version: str, pairs: list[dict[str, str]]) -> bytes:
    return apply_schema._canonical_json(
        {
            "allowed_pairs": pairs,
            "contract": "BUFFALO_PERSISTENT_MAPPING_MAINTENANCE_IDENTITY_V1",
            "family": apply_schema.MAPPING_RELEASE.family,
            "release": version,
            "target_schema": SCHEMA,
        }
    ) + b"\n"


def _render_rotation_topology(
    pairs: list[dict[str, str]], *, config_sha256: str
) -> str:
    source = (DB_DIR / apply_schema.MAPPING_MIGRATION_NAME).read_text(encoding="utf-8")
    start_marker = (
        f'CREATE OR REPLACE FUNCTION "{SCHEMA}".'
        "persistent_mapping_assert_safe_role_topology()"
    )
    start = source.index(start_marker)
    terminator = "$role_topology$;"
    end = source.index(terminator, start) + len(terminator)
    rendered = source[start:end]
    base_config = (
        DB_DIR.parent / apply_schema.MAPPING_RELEASE.maintenance_identity_config_ref
    ).read_bytes()
    base_value = json.loads(base_config)
    base_pairs_json = apply_schema._canonical_json(base_value["allowed_pairs"]).decode()
    next_pairs_json = apply_schema._canonical_json(pairs).decode()
    replacements = (
        (hashlib.sha256(base_config).hexdigest(), config_sha256),
        (
            apply_schema.MAPPING_RELEASE.maintenance_identity_pairs_sha256,
            hashlib.sha256(apply_schema._canonical_json(pairs)).hexdigest(),
        ),
        (base_pairs_json, next_pairs_json),
    )
    for old, new in replacements:
        if rendered.count(old) != 1:
            raise AssertionError(f"rotation topology binding count differs for {old}")
        rendered = rendered.replace(old, new)
    return rendered


def _rotation_release_sql(
    *,
    version: str,
    migration_name: str,
    predecessor: str,
    pairs: list[dict[str, str]],
    config_sha256: str,
) -> bytes:
    topology = _render_rotation_topology(pairs, config_sha256=config_sha256)
    return f'''-- buffalo-migration-replay: checksum-skip-v2
-- buffalo-contract-family: persistent-mapping-foundation
-- TEST-ONLY maintenance-pair rotation fixture; never a production migration.
DO $guard$
DECLARE installed_contract text;
BEGIN
  SELECT value INTO installed_contract
    FROM "{SCHEMA}".meta
   WHERE key='persistent_mapping_foundation_contract';
  IF installed_contract IS DISTINCT FROM '{predecessor}' THEN
    RAISE EXCEPTION 'test rotation requires exact predecessor';
  END IF;
  IF EXISTS (
      SELECT 1 FROM "{SCHEMA}".meta
       WHERE key='migration:{migration_name}'
  ) THEN
    RAISE EXCEPTION 'test rotation body must not replay';
  END IF;
END
$guard$;

{topology}

CREATE OR REPLACE FUNCTION "{SCHEMA}".assert_persistent_mapping_foundation_contract()
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, "{SCHEMA}"
AS $function$
DECLARE
  installed_contract text;
  installed_catalog text;
  computed_catalog text;
BEGIN
  PERFORM "{SCHEMA}".persistent_mapping_assert_safe_role_topology();
  SELECT value INTO installed_contract FROM "{SCHEMA}".meta
   WHERE key='persistent_mapping_foundation_contract';
  SELECT value INTO installed_catalog FROM "{SCHEMA}".meta
   WHERE key='persistent_mapping_foundation_catalog_sha256';
  IF installed_contract IS DISTINCT FROM '{version}' THEN
    RAISE EXCEPTION 'test rotation contract differs';
  END IF;
  computed_catalog := "{SCHEMA}".compute_persistent_mapping_catalog_sha256();
  IF installed_catalog IS DISTINCT FROM computed_catalog THEN
    RAISE EXCEPTION 'test rotation catalog differs';
  END IF;
END
$function$;
'''.encode()


ROTATION_OLD_PAIR = {
    "current_user": "qa_mapping_owner",
    "session_user": "qa_release_login",
}
ROTATION_NEXT_PAIR = {
    "current_user": "qa_mapping_owner",
    "session_user": ROTATION_LOGIN,
}
ROTATION_STAGE_PAIRS = [ROTATION_OLD_PAIR, ROTATION_NEXT_PAIR]
ROTATION_REMOVE_PAIRS = [ROTATION_NEXT_PAIR]
ROTATION_STAGE_CONFIG = _rotation_config(ROTATION_STAGE_VERSION, ROTATION_STAGE_PAIRS)
ROTATION_REMOVE_CONFIG = _rotation_config(ROTATION_REMOVE_VERSION, ROTATION_REMOVE_PAIRS)
ROTATION_DISJOINT_CONFIG = _rotation_config(
    ROTATION_DISJOINT_VERSION, ROTATION_REMOVE_PAIRS
)
ROTATION_STAGE_SQL = _rotation_release_sql(
    version=ROTATION_STAGE_VERSION,
    migration_name=ROTATION_STAGE_NAME,
    predecessor=apply_schema.MAPPING_RELEASE.version,
    pairs=ROTATION_STAGE_PAIRS,
    config_sha256=hashlib.sha256(ROTATION_STAGE_CONFIG).hexdigest(),
)
ROTATION_REMOVE_SQL = _rotation_release_sql(
    version=ROTATION_REMOVE_VERSION,
    migration_name=ROTATION_REMOVE_NAME,
    predecessor=ROTATION_STAGE_VERSION,
    pairs=ROTATION_REMOVE_PAIRS,
    config_sha256=hashlib.sha256(ROTATION_REMOVE_CONFIG).hexdigest(),
)
ROTATION_DISJOINT_SQL = _rotation_release_sql(
    version=ROTATION_DISJOINT_VERSION,
    migration_name=ROTATION_STAGE_NAME,
    predecessor=apply_schema.MAPPING_RELEASE.version,
    pairs=ROTATION_REMOVE_PAIRS,
    config_sha256=hashlib.sha256(ROTATION_DISJOINT_CONFIG).hexdigest(),
)
ROTATION_STAGE_SQL_SHA256 = "183d27337730ca5fd99d3e1bc239d93f2c1f1902f98efa31ce543140a85de911"
ROTATION_REMOVE_SQL_SHA256 = "2399f773b960995c08e5b953c2a03c8b27d904ad60b8124ce8f8434e1eec5255"
ROTATION_DISJOINT_SQL_SHA256 = "df47084cbc5554402a05b85127c43462f7c5bb4c74df9d971a76342f3e60c21e"
ROTATION_STAGE_CONFIG_SHA256 = "71c09ffcb4449f5bdf967af198824838009433410acbaa9272afb3c33bd679f5"
ROTATION_REMOVE_CONFIG_SHA256 = "63d0cd4d7113094cde627230fa98adbe681c0eceacc0beae89334869fd31661c"
ROTATION_DISJOINT_CONFIG_SHA256 = "b4bbbbd4cb2c3f007fe14ea90945a8870522e5169adcd65190fd64d46d6da7da"
ROTATION_STAGE_FUNCTION_CATALOG_SHA256 = "5018c85d6cf9fa30e52ff90576f1444fc7c8ea3b63af3a7363b862b678e06551"
ROTATION_REMOVE_FUNCTION_CATALOG_SHA256 = "92fbd2799ce8409ef581b8aa9fbb2fabd03cc1de1ed39eab6460e445c01c8ff5"


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
                self.assertEqual(
                    applied[-2:],
                    [
                        apply_schema.MAPPING_MIGRATION_NAME,
                        apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                    ],
                )
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
            corruptions = (
                f"DELETE FROM {SCHEMA}.meta WHERE key='migration:012_monday_review_draft_packet.sql'",
                f"DELETE FROM {SCHEMA}.meta WHERE key='migration:013_monday_p1_remediation.sql'",
                f"INSERT INTO {SCHEMA}.meta(key,value) VALUES('migration:012a_unknown.sql','applied')",
                f"DROP INDEX {SCHEMA}.uq_active_vendor_supplier_sku",
                f"DROP TRIGGER trg_prevent_referenced_offer_identity_change ON {SCHEMA}.supplier_offers",
                f"DROP TRIGGER trg_prevent_referenced_offer_identity_change ON {SCHEMA}.supplier_offers; "
                f"CREATE TRIGGER trg_prevent_referenced_offer_identity_change "
                f"BEFORE UPDATE OF variant_id ON {SCHEMA}.supplier_offers FOR EACH ROW "
                f"EXECUTE FUNCTION {SCHEMA}.prevent_referenced_offer_identity_change()",
                f"CREATE OR REPLACE FUNCTION {SCHEMA}.prevent_referenced_offer_identity_change() "
                "RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$",
                f"UPDATE {SCHEMA}.meta SET value='altered' "
                "WHERE key='monday_p1_remediation_contract'",
            )
            for statement in corruptions:
                with self.subTest(statement=statement), self.assertRaises(
                    (RuntimeError, psycopg.Error)
                ):
                    with conn.transaction(force_rollback=True):
                        conn.execute(statement)
                        apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            with conn.transaction():
                with self.assertRaisesRegex(RuntimeError, "predecessor marker"):
                    conn.execute(
                        f"DELETE FROM {SCHEMA}.meta "
                        "WHERE key='migration:013_monday_p1_remediation.sql'"
                    )
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
                DB_DIR / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                db_dir / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
            )
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
                with self.subTest(message="historical visit verifies highest release"), conn.transaction(
                    force_rollback=True
                ):
                    conn.execute(
                        f"ALTER FUNCTION {SCHEMA}.assert_persistent_mapping_foundation_contract() IMMUTABLE"
                    )
                    with self.assertRaisesRegex(
                        RuntimeError, "function properties differ"
                    ):
                        apply_schema._verify_or_apply_mapping_release(
                            conn, db_dir, apply_schema.MAPPING_RELEASE
                        )
            self._prepare_roles_and_schema()
            with (
                mock.patch.object(
                    apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", manifest
                ),
                mock.patch.object(
                    apply_schema, "MAPPING_RELEASE_TRUST_MANIFEST", trust
                ),
                mock.patch.object(apply_schema, "MIGRATION_ORDER", order),
                psycopg.connect(self.mapping_url) as fresh_conn,
            ):
                applied = apply_schema.apply_schema_connection(
                    fresh_conn, db_dir, include_persistent_mapping=True
                )
                self.assertEqual(
                    applied[-3:],
                    [
                        apply_schema.MAPPING_MIGRATION_NAME,
                        apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                        TEST_V2_NAME,
                    ],
                )
                self.assertEqual(
                    apply_schema.apply_schema_connection(
                        fresh_conn, db_dir, include_persistent_mapping=True
                    ),
                    [],
                )

    def test_replay_independently_rejects_anchor_and_helper_tampering(self):
        with psycopg.connect(self.mapping_url) as conn:
            corruptions = (
                f"CREATE OR REPLACE FUNCTION {SCHEMA}.assert_persistent_mapping_foundation_contract() "
                "RETURNS void LANGUAGE plpgsql AS $$BEGIN RETURN; END$$",
                f"CREATE OR REPLACE FUNCTION {SCHEMA}.compute_persistent_mapping_catalog_sha256() "
                f"RETURNS text LANGUAGE sql AS $$SELECT value FROM {SCHEMA}.meta "
                "WHERE key='persistent_mapping_foundation_catalog_sha256'$$",
                f"ALTER FUNCTION {SCHEMA}.assert_persistent_mapping_foundation_contract() VOLATILE",
                f"DROP FUNCTION {SCHEMA}.persistent_mapping_json_sha256(jsonb) CASCADE",
                f"UPDATE {SCHEMA}.meta SET value='unknown-release' "
                "WHERE key='persistent_mapping_foundation_contract'",
                "CREATE SCHEMA hostile_digest AUTHORIZATION CURRENT_USER; "
                "CREATE FUNCTION hostile_digest.digest(bytea,text) RETURNS bytea "
                "LANGUAGE sql IMMUTABLE STRICT AS $$SELECT $1$$",
            )
            for statement in corruptions:
                with self.subTest(statement=statement), self.assertRaises(
                    (RuntimeError, psycopg.Error)
                ):
                    with conn.transaction(force_rollback=True):
                        conn.execute(statement)
                        apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            with conn.transaction():
                self.assertFalse(
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
                )

    def test_mapping_family_lock_serializes_marker_without_claiming_whole_run_atomicity(self):
        self._reset(include_mapping=False)
        barrier = Barrier(2)

        def replay() -> bool:
            barrier.wait(timeout=10)
            with psycopg.connect(self.mapping_url) as conn, conn.transaction():
                return apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in (pool.submit(replay), pool.submit(replay))]
        self.assertEqual(sorted(results), [False, True])
        marker_key = f"migration:{apply_schema.MAPPING_MIGRATION_NAME}"
        with psycopg.connect(self.mapping_url) as conn:
            marker_before = conn.execute(
                f"SELECT value FROM {SCHEMA}.meta WHERE key=%s", (marker_key,)
            ).fetchone()[0]
            authority_before = self._counts()

        with TemporaryDirectory(prefix="mapping-different-bytes-") as temporary:
            altered_dir = Path(temporary) / "db"
            shutil.copytree(DB_DIR, altered_dir)
            migration_path = altered_dir / apply_schema.MAPPING_MIGRATION_NAME
            altered_bytes = migration_path.read_bytes() + b"-- different reviewed candidate\n"
            migration_path.write_bytes(altered_bytes)
            altered = replace(
                apply_schema.MAPPING_RELEASE,
                migration_sha256=hashlib.sha256(altered_bytes).hexdigest(),
            )
            trust = apply_schema.MAPPING_RELEASE_TRUST_MANIFEST[
                (apply_schema.MAPPING_RELEASE.family, apply_schema.MAPPING_RELEASE.version)
            ]
            with (
                mock.patch.object(
                    apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", (altered,)
                ),
                mock.patch.object(
                    apply_schema,
                    "MAPPING_RELEASE_TRUST_MANIFEST",
                    {(altered.family, altered.version): trust},
                ),
                psycopg.connect(self.mapping_url) as conn,
                self.assertRaisesRegex(RuntimeError, "checksum differs"),
            ):
                with conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(
                        conn, altered_dir, altered
                    )
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT value FROM {SCHEMA}.meta WHERE key=%s", (marker_key,)
                ).fetchone()[0],
                marker_before,
            )
        self.assertEqual(self._counts(), authority_before)

    def test_explicit_schema_binding_defeats_hostile_search_path_temp_and_helper_decoys(self):
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("CREATE TEMP TABLE meta(key text,value text)")
            conn.execute("SET search_path=pg_temp,public")
            with conn.transaction():
                self.assertFalse(apply_schema._verify_or_apply_mapping_release(conn, DB_DIR))
            self.assertEqual(conn.execute(f"SELECT value FROM {SCHEMA}.meta WHERE key=%s", (f"migration:{apply_schema.MAPPING_MIGRATION_NAME}",)).fetchone()[0], f"sha256:{apply_schema.MAPPING_RELEASE.migration_sha256}")

    def test_exact_013_upgrade_preserves_all_legacy_bytes_and_counts(self):
        self._reset(include_mapping=False)
        self._seed_catalog()
        self._legacy_offer()

        mapping_tables = {
            "supplier_mapping_review_batches",
            "supplier_mapping_review_candidates",
            "supplier_mapping_decisions",
            "supplier_offer_selection_events",
            "supplier_offer_selection_heads",
        }

        def legacy_snapshot(conn: Any) -> dict[str, tuple[int, str]]:
            names = [
                str(row[0])
                for row in conn.execute(
                    "SELECT c.relname FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relkind IN ('r','p') "
                    "ORDER BY c.relname",
                    (SCHEMA,),
                ).fetchall()
                if str(row[0]) not in mapping_tables
            ]
            snapshot: dict[str, tuple[int, str]] = {}
            for name in names:
                predicate = (
                    sql.SQL(
                        " WHERE key<>'migration:014_persistent_mapping_foundation.sql' "
                        "AND key NOT IN ('persistent_mapping_foundation_contract',"
                        "'persistent_mapping_foundation_catalog_sha256')"
                    )
                    if name == "meta"
                    else sql.SQL("")
                )
                row = conn.execute(
                    sql.SQL(
                        "SELECT count(*),pg_catalog.encode({}.digest("
                        "pg_catalog.convert_to(COALESCE(pg_catalog.string_agg("
                        "row_value,E'\\n' ORDER BY row_value),''),'UTF8'),'sha256'),'hex') "
                        "FROM (SELECT pg_catalog.to_jsonb(t)::text AS row_value "
                        "FROM {}.{} AS t{}) AS rows"
                    ).format(
                        sql.Identifier(SCHEMA),
                        sql.Identifier(SCHEMA),
                        sql.Identifier(name),
                        predicate,
                    )
                ).fetchone()
                snapshot[name] = (int(row[0]), str(row[1]))
            return snapshot

        evaluation_at = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        with psycopg.connect(self.mapping_url) as conn:
            before = legacy_snapshot(conn)
            recommendation_before = recommendations._load_context(
                conn,
                business_date=BUSINESS_DATE,
                variant_id="1001",
                evaluation_at=evaluation_at,
            )
            conn.rollback()
            with conn.transaction():
                self.assertTrue(apply_schema._verify_or_apply_mapping_release(conn, DB_DIR))
            self.assertEqual(legacy_snapshot(conn), before)
            self.assertEqual(
                recommendations._canonical_json(
                    recommendations._load_context(
                        conn,
                        business_date=BUSINESS_DATE,
                        variant_id="1001",
                        evaluation_at=evaluation_at,
                    )
                ),
                recommendations._canonical_json(recommendation_before),
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT (SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_batches),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_candidates),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_events),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads)"
                ).fetchone(),
                (0, 0, 0, 0, 0),
            )

    def test_migration_failure_and_late_validation_rolls_back_every_object(self):
        self._reset(include_mapping=False)
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(
                f"INSERT INTO {SCHEMA}.vendors(vendor_id,vendor_name) "
                "VALUES(%s,'late-validation-preserved')",
                (VENDOR_ID,),
            )
            conn.execute(f"CREATE TABLE {SCHEMA}.collision_probe(id integer)")
            conn.execute(f"CREATE INDEX uq_mapping_decision_root_per_scope ON {SCHEMA}.collision_probe(id)")
            conn.commit()
            with self.assertRaises(psycopg.Error):
                with conn.transaction():
                    conn.execute((DB_DIR / apply_schema.MAPPING_MIGRATION_NAME).read_text())
            self.assertIsNone(conn.execute(f"SELECT to_regclass('{SCHEMA}.supplier_mapping_review_batches')").fetchone()[0])
            conn.execute(f"DROP TABLE {SCHEMA}.collision_probe")
            conn.commit()
            before_markers = dict(
                conn.execute(f"SELECT key,value FROM {SCHEMA}.meta ORDER BY key").fetchall()
            )
            before_relations = tuple(
                conn.execute(
                    "SELECT c.relname,c.relkind FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s ORDER BY c.relname,c.relkind",
                    (SCHEMA,),
                ).fetchall()
            )
            with (
                mock.patch.object(
                    apply_schema,
                    "_verify_installed_function_catalog",
                    side_effect=RuntimeError("injected post-DDL validation failure"),
                ),
                self.assertRaisesRegex(RuntimeError, "post-DDL"),
            ):
                with conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            self.assertEqual(
                dict(
                    conn.execute(
                        f"SELECT key,value FROM {SCHEMA}.meta ORDER BY key"
                    ).fetchall()
                ),
                before_markers,
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT c.relname,c.relkind FROM pg_catalog.pg_class c "
                        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=%s ORDER BY c.relname,c.relkind",
                        (SCHEMA,),
                    ).fetchall()
                ),
                before_relations,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT vendor_name FROM {SCHEMA}.vendors WHERE vendor_id=%s",
                    (VENDOR_ID,),
                ).fetchone()[0],
                "late-validation-preserved",
            )

    def test_late_decision_or_head_validation_rolls_back_the_whole_transaction(self):
        create_candidate = self._intake(
            self._packet(
                1,
                occurrence="late-offer",
                package_id="late-offer-package",
                candidate_changes={
                    "supplier_code_value": "LATE-001",
                    "distributor_product_id_value": "LATE-001",
                },
            )
        )
        before = self._counts()
        with psycopg.connect(self.mapping_url) as conn:
            offer_count = conn.execute(
                f"SELECT count(*) FROM {SCHEMA}.supplier_offers"
            ).fetchone()[0]
            conn.execute(
                f"CREATE FUNCTION {SCHEMA}.synthetic_late_decision_refusal() "
                "RETURNS trigger LANGUAGE plpgsql AS "
                "$$BEGIN RAISE EXCEPTION 'synthetic late decision refusal'; END$$"
            )
            conn.execute(
                f"CREATE CONSTRAINT TRIGGER synthetic_late_decision_refusal "
                f"AFTER INSERT ON {SCHEMA}.supplier_mapping_decisions "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
                f"{SCHEMA}.synthetic_late_decision_refusal()"
            )
        try:
            with self.assertRaises(PersistentMappingError):
                self._decide(
                    create_candidate,
                    action="APPROVE_MAPPING",
                    reason="late offer refusal",
                    link_kind="CREATED_INACTIVE",
                )
            self.assertEqual(self._counts(), before)
            with psycopg.connect(self.mapping_url) as conn:
                self.assertEqual(
                    conn.execute(
                        f"SELECT count(*) FROM {SCHEMA}.supplier_offers"
                    ).fetchone()[0],
                    offer_count,
                )
        finally:
            with psycopg.connect(self.mapping_url) as conn:
                conn.execute(
                    f"DROP TRIGGER IF EXISTS synthetic_late_decision_refusal "
                    f"ON {SCHEMA}.supplier_mapping_decisions"
                )
                conn.execute(
                    f"DROP FUNCTION IF EXISTS {SCHEMA}.synthetic_late_decision_refusal()"
                )

        offer = self._legacy_offer(sku="LATE-HEAD")
        head_candidate = self._intake(
            self._packet(
                1,
                occurrence="late-head",
                package_id="late-head-package",
                candidate_changes={
                    "supplier_code_value": "LATE-HEAD",
                    "distributor_product_id_value": "LATE-HEAD",
                },
            )
        )
        decision = self._decide(
            head_candidate,
            action="APPROVE_MAPPING",
            reason="late head setup",
            offer_id=offer,
            link_kind="LINKED_EXISTING",
        )
        before_head = self._counts()
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(
                f"CREATE FUNCTION {SCHEMA}.synthetic_late_head_refusal() "
                "RETURNS trigger LANGUAGE plpgsql AS "
                "$$BEGIN RAISE EXCEPTION 'synthetic late head refusal'; END$$"
            )
            conn.execute(
                f"CREATE CONSTRAINT TRIGGER synthetic_late_head_refusal "
                f"AFTER INSERT ON {SCHEMA}.supplier_offer_selection_heads "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION "
                f"{SCHEMA}.synthetic_late_head_refusal()"
            )
        try:
            with self.assertRaises(PersistentMappingError):
                self._select(decision, reason="late head refusal")
            self.assertEqual(self._counts(), before_head)
        finally:
            with psycopg.connect(self.mapping_url) as conn:
                conn.execute(
                    f"DROP TRIGGER IF EXISTS synthetic_late_head_refusal "
                    f"ON {SCHEMA}.supplier_offer_selection_heads"
                )
                conn.execute(
                    f"DROP FUNCTION IF EXISTS {SCHEMA}.synthetic_late_head_refusal()"
                )

    def test_valid_intake_adds_only_immutable_batch_and_candidates(self):
        self._intake(self._packet(1))
        self.assertEqual(self._counts(), (1, 1, 0, 0, 0, 0, 0))

    def test_intake_exact_replay_returns_existing_and_payload_change_conflicts(self):
        packet = self._packet(1)
        first = execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        self._decide(
            UUID(first["candidate_ids"][0]),
            action="DEFER",
            reason="later review state must not change intake replay",
        )
        second = execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["candidate_ids"], second["candidate_ids"])
        changed = self._packet(
            1,
            occurrence="changed-complete-request",
            package_id="changed-complete-package",
        )
        changed["intake_idempotency_key"] = packet["intake_idempotency_key"]
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
        barrier = Barrier(2)

        def create(index: int) -> tuple[str, dict | PersistentMappingError]:
            barrier.wait(timeout=10)
            try:
                return (
                    "committed",
                    execute_mapping_decision(
                        self.mapping_url,
                        candidate_id=ids[index],
                        action="APPROVE_MAPPING",
                        reason="create shared",
                        principal=self.principal,
                        decision_idempotency_key=create_keys[index],
                        expected_preview_sha256=previews[index]["preview_sha256"],
                        offer_link_kind="CREATED_INACTIVE",
                    ),
                )
            except PersistentMappingError as exc:
                return ("refused", exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [future.result() for future in (pool.submit(create, 0), pool.submit(create, 1))]
        self.assertEqual([item[0] for item in outcomes].count("committed"), 1)
        self.assertEqual([item[0] for item in outcomes].count("refused"), 1)
        winner_index = next(index for index, item in enumerate(outcomes) if item[0] == "committed")
        loser_index = 1 - winner_index
        first_result = outcomes[winner_index][1]
        assert isinstance(first_result, dict)
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_offers "
                    "WHERE supplier_sku='NEW-001'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s)",
                    (ids,),
                ).fetchone()[0],
                1,
            )

        # A fresh linked preview and separate key are required after the winner.
        linked_key = uuid4()
        linked_preview = self._preview_decision(
            ids[loser_index],
            action="APPROVE_MAPPING",
            reason="create shared",
            key=linked_key,
            offer_id=first_result["result_offer_id"],
            link_kind="LINKED_EXISTING",
        )
        linked = execute_mapping_decision(
            self.mapping_url,
            candidate_id=ids[loser_index],
            action="APPROVE_MAPPING",
            reason="create shared",
            principal=self.principal,
            decision_idempotency_key=linked_key,
            expected_preview_sha256=linked_preview["preview_sha256"],
            existing_offer_id=first_result["result_offer_id"],
            offer_link_kind="LINKED_EXISTING",
        )
        self.assertEqual(first_result["result_offer_id"], linked["result_offer_id"])

        # Superseding the first approval never releases its historical
        # operational-key/offer binding for a later occurrence.
        self._decide(
            ids[winner_index],
            action="DEFER",
            reason="supersede without erasing historical offer identity",
        )
        later_id = self._intake(
            self._packet(
                1,
                occurrence="create-c",
                package_id="create-later",
                candidate_changes={
                    "supplier_code_value": "NEW-001",
                    "distributor_product_id_value": "NEW-001",
                },
            )
        )
        with self.assertRaisesRegex(PersistentMappingError, "equivalent offer"):
            self._decide(
                later_id,
                action="APPROVE_MAPPING",
                reason="historical binding refuses duplicate create",
                link_kind="CREATED_INACTIVE",
            )
        historical_link = self._decide(
            later_id,
            action="APPROVE_MAPPING",
            reason="historical binding reuses exact offer",
            offer_id=first_result["result_offer_id"],
            link_kind="LINKED_EXISTING",
        )
        self.assertEqual(
            historical_link["result_offer_id"], first_result["result_offer_id"]
        )

    def test_regular_gift_special_alternate_component_and_combo_do_not_collapse(self):
        keys: set[str] = set()
        offers: set[int] = set()
        decisions: dict[str, dict] = {}
        package_types = {
            "REGULAR": "STANDARD",
            "GIFT": "GIFT",
            "SPECIAL": "SPECIAL",
            "ALTERNATE": "ALTERNATE",
            "COMPONENT": "COMPONENT",
            "COMBO": "COMBO",
        }
        for index, offer_class in enumerate(package_types, 1):
            code = f"CLASS-{index}"
            relationships = (
                [{"kind": "COMPONENT_OF", "group": f"GROUP-{index}"}]
                if offer_class in {"COMPONENT", "COMBO"}
                else []
            )
            packet = self._packet(
                1,
                occurrence=f"class-{index}",
                package_id=f"class-package-{index}",
                candidate_changes={
                    "offer_class": offer_class,
                    "occurrence_role": offer_class if offer_class != "REGULAR" else "PRIMARY",
                    "package_type_value": package_types[offer_class],
                    "supplier_code_value": code,
                    "distributor_product_id_value": code,
                    "component_relationships": relationships,
                },
            )
            candidate_id = self._intake(packet)
            with psycopg.connect(self.mapping_url) as conn:
                keys.add(
                    conn.execute(
                        f"SELECT operational_offer_key_sha256 FROM {SCHEMA}.supplier_mapping_review_candidates WHERE candidate_id=%s",
                        (candidate_id,),
                    ).fetchone()[0]
                )
            decision = self._decide(
                candidate_id,
                action="APPROVE_MAPPING",
                reason=f"distinct {offer_class.lower()} offer",
                link_kind="CREATED_INACTIVE",
            )
            decisions[offer_class] = decision
            offers.add(int(decision["result_offer_id"]))
        self.assertEqual(len(keys), 6)
        self.assertEqual(len(offers), 6)
        with psycopg.connect(self.mapping_url) as conn:
            rows = conn.execute(
                f"SELECT package_type,active FROM {SCHEMA}.supplier_offers "
                "WHERE offer_id=ANY(%s) ORDER BY package_type",
                (list(offers),),
            ).fetchall()
        self.assertEqual({row[0] for row in rows}, set(package_types.values()))
        self.assertTrue(all(row[1] is False for row in rows))
        self._select(decisions["REGULAR"], reason="regular alone may select")
        for offer_class in package_types.keys() - {"REGULAR"}:
            with self.subTest(offer_class=offer_class), self.assertRaises(
                PersistentMappingError
            ):
                self._select(decisions[offer_class], reason="nonregular must refuse")

        shared_offer = self._legacy_offer(sku="SHARED-RACE")
        first = self._packet(
            1,
            occurrence="different-key-a",
            package_id="different-key-race",
            candidate_changes={
                "supplier_code_value": "SHARED-RACE",
                "distributor_product_id_value": "SHARED-RACE",
                "identity_qualifiers": {"material_qualifier": "A"},
            },
        )
        second = copy.deepcopy(first["candidates"][0])
        second["occurrence_key"] = "different-key-b"
        second["source_row_key"] = "different-key-b"
        second["identity_qualifiers"] = {"material_qualifier": "B"}
        race = self._packet(
            1,
            occurrence="different-key-a",
            package_id="different-key-race",
            candidate_changes={
                "supplier_code_value": "SHARED-RACE",
                "distributor_product_id_value": "SHARED-RACE",
                "identity_qualifiers": {"material_qualifier": "A"},
            },
            extra_candidates=[second],
        )
        intake = execute_supplier_mapping_intake(
            self.mapping_url,
            package=race["package"],
            candidates=race["candidates"],
            principal=self.principal,
            intake_idempotency_key=race["intake_idempotency_key"],
        )
        race_ids = [UUID(value) for value in intake["candidate_ids"]]
        race_keys = [uuid4(), uuid4()]
        previews = [
            self._preview_decision(
                candidate_id,
                action="APPROVE_MAPPING",
                reason="different material key race",
                key=key,
                offer_id=shared_offer,
                link_kind="LINKED_EXISTING",
            )
            for candidate_id, key in zip(race_ids, race_keys, strict=True)
        ]
        race_barrier = Barrier(2)

        def approve(index: int):
            race_barrier.wait(timeout=10)
            return execute_mapping_decision(
                self.mapping_url,
                candidate_id=race_ids[index],
                action="APPROVE_MAPPING",
                reason="different material key race",
                principal=self.principal,
                decision_idempotency_key=race_keys[index],
                expected_preview_sha256=previews[index]["preview_sha256"],
                existing_offer_id=shared_offer,
                offer_link_kind="LINKED_EXISTING",
            )

        outcomes: list[object] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve, index) for index in range(2)]
            for future in futures:
                try:
                    outcomes.append(future.result())
                except PersistentMappingError as exc:
                    outcomes.append(exc)
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(sum(isinstance(item, PersistentMappingError) for item in outcomes), 1)
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s)",
                    (race_ids,),
                ).fetchone()[0],
                1,
            )

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
        hostile_urls = (
            self.mapping_url + "&hostaddr=192.0.2.10",
            self.mapping_url + "&dbname=production",
            self.mapping_url.replace("127.0.0.1", "localhost"),
            self.mapping_url.replace("qa_release_login@", "runner@"),
            self.mapping_url.replace(f"/{self.database}?", "/production?"),
        )
        for hostile_url in hostile_urls:
            with (
                self.subTest(hostile_url=hostile_url),
                mock.patch.object(psycopg, "connect") as connect,
                self.assertRaises(PersistentMappingError),
            ):
                execute_mapping_decision(
                    hostile_url,
                    candidate_id=candidate_id,
                    action="DEFER",
                    reason="pre-connect refusal",
                    principal=self.principal,
                    decision_idempotency_key=uuid4(),
                    expected_preview_sha256="0" * 64,
                )
            connect.assert_not_called()

        class IdentityResult:
            def fetchone(self):
                return (
                    "different_test",
                    "127.0.0.1/32",
                    160009,
                    "qa_release_login",
                    "qa_mapping_owner",
                )

        class WrongIdentityConnection:
            closed = False

            def execute(self, _statement, _parameters=()):
                return IdentityResult()

            def close(self):
                self.closed = True

        wrong_identity = WrongIdentityConnection()
        with (
            mock.patch.object(psycopg, "connect", return_value=wrong_identity),
            self.assertRaises(PersistentMappingError),
        ):
            execute_mapping_decision(
                self.mapping_url,
                candidate_id=candidate_id,
                action="DEFER",
                reason="connected identity refusal",
                principal=self.principal,
                decision_idempotency_key=uuid4(),
                expected_preview_sha256="0" * 64,
            )
        self.assertTrue(wrong_identity.closed)

    def test_role_topology_rejects_direct_transitive_inherit_set_and_mixed_owner_paths(self):
        config_path = (
            DB_DIR.parent / apply_schema.MAPPING_RELEASE.maintenance_identity_config_ref
        )
        config_bytes = config_path.read_bytes()
        config = json.loads(config_bytes)
        pair_bytes = apply_schema._canonical_json(config["allowed_pairs"])
        migration_source = (DB_DIR / apply_schema.MAPPING_MIGRATION_NAME).read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            hashlib.sha256(config_bytes).hexdigest(),
            apply_schema.MAPPING_RELEASE.maintenance_identity_config_sha256,
        )
        self.assertEqual(
            hashlib.sha256(pair_bytes).hexdigest(),
            apply_schema.MAPPING_RELEASE.maintenance_identity_pairs_sha256,
        )
        for literal in (
            apply_schema.MAPPING_RELEASE.maintenance_identity_config_sha256,
            apply_schema.MAPPING_RELEASE.maintenance_identity_pairs_sha256,
            pair_bytes.decode(),
        ):
            self.assertIn(literal, migration_source)
        spec_source = (
            DB_DIR.parents[1]
            / "docs/superpowers/specs/2026-09-10-persistent-mapping-foundation-implementation-spec.md"
        ).read_text(encoding="utf-8")
        spec_start = spec_source.index(
            'CREATE OR REPLACE FUNCTION "__BUFFALO_TARGET_SCHEMA__".'
            "persistent_mapping_assert_safe_role_topology()"
        )
        terminator = "$role_topology$;"
        spec_function = spec_source[
            spec_start : spec_source.index(terminator, spec_start) + len(terminator)
        ]
        rendered_spec_function = (
            spec_function.replace('"__BUFFALO_TARGET_SCHEMA__"', f'"{SCHEMA}"')
            .replace("'__BUFFALO_TARGET_SCHEMA_TEXT__'", f"'{SCHEMA}'")
            .replace('"__BUFFALO_PGCRYPTO_SCHEMA__"', f'"{SCHEMA}"')
            .replace("'__BUFFALO_PGCRYPTO_SCHEMA_TEXT__'", f"'{SCHEMA}'")
            .replace(
                "__BUFFALO_MAINTENANCE_IDENTITY_CONFIG_SHA256_LITERAL__",
                "'" + apply_schema.MAPPING_RELEASE.maintenance_identity_config_sha256 + "'",
            )
            .replace(
                "__BUFFALO_MAINTENANCE_PAIRS_SHA256_LITERAL__",
                "'" + apply_schema.MAPPING_RELEASE.maintenance_identity_pairs_sha256 + "'",
            )
            .replace(
                "__BUFFALO_MAINTENANCE_PAIRS_JSON_LITERAL__",
                "'" + pair_bytes.decode() + "'",
            )
        )
        migration_start = migration_source.index(
            f'CREATE OR REPLACE FUNCTION "{SCHEMA}".'
            "persistent_mapping_assert_safe_role_topology()"
        )
        migration_function = migration_source[
            migration_start : migration_source.index(terminator, migration_start)
            + len(terminator)
        ]
        self.assertEqual(rendered_spec_function, migration_function)

        def membership_rows() -> list[tuple]:
            with self._admin_connection() as admin:
                return admin.execute(
                    "SELECT member.rolname,owner.rolname,m.inherit_option,"
                    "m.set_option,m.admin_option FROM pg_catalog.pg_auth_members m "
                    "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
                    "JOIN pg_catalog.pg_roles owner ON owner.oid=m.roleid "
                    "WHERE owner.rolname='qa_mapping_owner' "
                    "AND member.rolname IN ('qa_release_login',%s) "
                    "ORDER BY member.rolname",
                    (ROTATION_LOGIN,),
                ).fetchall()

        def cleanup_rotation_role() -> None:
            with self._admin_connection() as admin:
                if admin.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s)",
                    (ROTATION_LOGIN,),
                ).fetchone()[0]:
                    admin.execute(
                        sql.SQL("REVOKE qa_mapping_owner FROM {}").format(
                            sql.Identifier(ROTATION_LOGIN)
                        )
                    )
                    admin.execute(
                        sql.SQL("DROP ROLE {}").format(sql.Identifier(ROTATION_LOGIN))
                    )

        self.addCleanup(cleanup_rotation_role)

        baseline_membership = membership_rows()
        with self._admin_connection() as admin:
            admin.execute("REVOKE qa_mapping_owner FROM qa_release_login")
            admin.execute("GRANT qa_mapping_owner TO qa_release_login WITH INHERIT TRUE, SET TRUE, ADMIN FALSE")
        unsafe_membership = membership_rows()
        with psycopg.connect(self.mapping_url) as conn:
            with self.assertRaisesRegex(RuntimeError, "role topology"):
                with conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            with self.assertRaisesRegex(psycopg.Error, "approved maintenance role topology"):
                conn.execute(
                    f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                )
        self.assertEqual(membership_rows(), unsafe_membership)
        with self._admin_connection() as admin:
            admin.execute("REVOKE qa_mapping_owner FROM qa_release_login")
            admin.execute(
                "GRANT qa_mapping_owner TO qa_release_login "
                "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
            )
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(ROTATION_LOGIN))
            )
        self.assertEqual(membership_rows()[0], baseline_membership[0])
        release_membership = membership_rows()

        def release(
            *,
            version: str,
            name: str,
            source: bytes,
            predecessor: str,
            config_ref: str,
            config_source: bytes,
            pairs: list[dict[str, str]],
        ) -> apply_schema.MappingRelease:
            return replace(
                apply_schema.MAPPING_RELEASE,
                version=version,
                migration_name=name,
                migration_sha256=hashlib.sha256(source).hexdigest(),
                predecessor_release=predecessor,
                maintenance_identity_config_ref=config_ref,
                maintenance_identity_config_sha256=hashlib.sha256(
                    config_source
                ).hexdigest(),
                maintenance_identity_pairs_sha256=hashlib.sha256(
                    apply_schema._canonical_json(pairs)
                ).hexdigest(),
            )

        def trust(
            candidate: apply_schema.MappingRelease, catalog_sha256: str
        ) -> apply_schema.MappingReleaseTrust:
            return apply_schema.MappingReleaseTrust(
                family=candidate.family,
                version=candidate.version,
                function_identities=apply_schema.TRUSTED_FUNCTION_IDENTITIES,
                function_catalog_sha256=catalog_sha256,
                pgcrypto_digest_rows=apply_schema.TRUSTED_PGCRYPTO_DIGEST_ROWS,
            )

        def authority_snapshot(conn) -> tuple:
            markers = tuple(
                conn.execute(
                    f"SELECT key,value FROM {SCHEMA}.meta "
                    "WHERE key LIKE 'migration:%persistent_mapping%' "
                    "OR key LIKE 'persistent_mapping_foundation_%' ORDER BY key"
                ).fetchall()
            )
            counts = tuple(
                conn.execute(
                    f"SELECT (SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_batches),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_candidates),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_events),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads)"
                ).fetchone()
            )
            return markers, counts

        for value, expected in (
            (ROTATION_STAGE_SQL, ROTATION_STAGE_SQL_SHA256),
            (ROTATION_REMOVE_SQL, ROTATION_REMOVE_SQL_SHA256),
            (ROTATION_DISJOINT_SQL, ROTATION_DISJOINT_SQL_SHA256),
            (ROTATION_STAGE_CONFIG, ROTATION_STAGE_CONFIG_SHA256),
            (ROTATION_REMOVE_CONFIG, ROTATION_REMOVE_CONFIG_SHA256),
            (ROTATION_DISJOINT_CONFIG, ROTATION_DISJOINT_CONFIG_SHA256),
        ):
            self.assertEqual(hashlib.sha256(value).hexdigest(), expected)

        with TemporaryDirectory(prefix="buffalo-mapping-role-rotation-") as temporary:
            root = Path(temporary)
            db_dir = root / "db"
            config_dir = root / "config"
            db_dir.mkdir()
            config_dir.mkdir()
            for name in apply_schema.LEGACY_MIGRATION_SHA256:
                shutil.copyfile(DB_DIR / name, db_dir / name)
            shutil.copyfile(
                DB_DIR / apply_schema.MAPPING_MIGRATION_NAME,
                db_dir / apply_schema.MAPPING_MIGRATION_NAME,
            )
            shutil.copyfile(
                DB_DIR / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                db_dir / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
            )
            shutil.copyfile(config_path, config_dir / "persistent_mapping.synthetic.json")

            disjoint_ref = "config/rotation-disjoint.json"
            (config_dir / "rotation-disjoint.json").write_bytes(
                ROTATION_DISJOINT_CONFIG
            )
            (db_dir / ROTATION_STAGE_NAME).write_bytes(ROTATION_DISJOINT_SQL)
            disjoint = release(
                version=ROTATION_DISJOINT_VERSION,
                name=ROTATION_STAGE_NAME,
                source=ROTATION_DISJOINT_SQL,
                predecessor=apply_schema.MAPPING_RELEASE.version,
                config_ref=disjoint_ref,
                config_source=ROTATION_DISJOINT_CONFIG,
                pairs=ROTATION_REMOVE_PAIRS,
            )
            disjoint_trust = trust(
                disjoint, ROTATION_REMOVE_FUNCTION_CATALOG_SHA256
            )
            base_release = replace(
                apply_schema.MAPPING_RELEASE,
                maintenance_identity_config_ref="config/persistent_mapping.synthetic.json",
            )
            manifest = (base_release, disjoint)
            trust_manifest = {
                (base_release.family, base_release.version):
                    apply_schema.MAPPING_RELEASE_TRUST_MANIFEST[
                        (apply_schema.MAPPING_RELEASE.family, apply_schema.MAPPING_RELEASE.version)
                    ],
                (disjoint.family, disjoint.version): disjoint_trust,
            }
            with psycopg.connect(self.mapping_url) as conn:
                before = authority_snapshot(conn)
                before_membership = membership_rows()
                with (
                    mock.patch.object(
                        apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", manifest
                    ),
                    mock.patch.object(
                        apply_schema, "MAPPING_RELEASE_TRUST_MANIFEST", trust_manifest
                    ),
                    mock.patch.object(
                        apply_schema,
                        "MIGRATION_ORDER",
                        [*apply_schema.LEGACY_MIGRATION_SHA256, base_release.migration_name, disjoint.migration_name],
                    ),
                    self.assertRaisesRegex(RuntimeError, "no reviewed intersection"),
                    conn.transaction(),
                ):
                    apply_schema._verify_or_apply_mapping_release(
                        conn, db_dir, disjoint
                    )
                self.assertEqual(authority_snapshot(conn), before)
                self.assertEqual(membership_rows(), before_membership)

            stage_ref = "config/rotation-stage.json"
            (config_dir / "rotation-stage.json").write_bytes(ROTATION_STAGE_CONFIG)
            (db_dir / ROTATION_STAGE_NAME).write_bytes(ROTATION_STAGE_SQL)
            stage = release(
                version=ROTATION_STAGE_VERSION,
                name=ROTATION_STAGE_NAME,
                source=ROTATION_STAGE_SQL,
                predecessor=base_release.version,
                config_ref=stage_ref,
                config_source=ROTATION_STAGE_CONFIG,
                pairs=ROTATION_STAGE_PAIRS,
            )
            stage_trust = trust(stage, ROTATION_STAGE_FUNCTION_CATALOG_SHA256)
            stage_manifest = (base_release, stage)
            stage_trust_manifest = {
                (base_release.family, base_release.version): trust_manifest[
                    (base_release.family, base_release.version)
                ],
                (stage.family, stage.version): stage_trust,
            }
            stage_order = [
                *apply_schema.LEGACY_MIGRATION_SHA256,
                base_release.migration_name,
                stage.migration_name,
            ]
            with (
                mock.patch.object(
                    apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", stage_manifest
                ),
                mock.patch.object(
                    apply_schema, "MAPPING_RELEASE_TRUST_MANIFEST", stage_trust_manifest
                ),
                mock.patch.object(apply_schema, "MIGRATION_ORDER", stage_order),
                psycopg.connect(self.mapping_url) as conn,
            ):
                with conn.transaction():
                    self.assertTrue(
                        apply_schema._verify_or_apply_mapping_release(conn, db_dir, stage)
                    )
                conn.execute(
                    f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                )
            self.assertEqual(membership_rows(), release_membership)

            with self._admin_connection() as admin:
                admin.execute(
                    sql.SQL(
                        "GRANT qa_mapping_owner TO {} "
                        "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                    ).format(sql.Identifier(ROTATION_LOGIN))
                )

            parsed = urlparse(self.mapping_url)
            next_url = self.mapping_url.replace(
                f"{parsed.username}@", f"{ROTATION_LOGIN}@", 1
            )
            with (
                mock.patch.object(
                    apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", stage_manifest
                ),
                mock.patch.object(
                    apply_schema, "MAPPING_RELEASE_TRUST_MANIFEST", stage_trust_manifest
                ),
                mock.patch.object(apply_schema, "MIGRATION_ORDER", stage_order),
                psycopg.connect(next_url) as conn,
            ):
                conn.execute(
                    f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                )
                with conn.transaction():
                    self.assertFalse(
                        apply_schema._verify_or_apply_mapping_release(conn, db_dir, stage)
                    )

            remove_ref = "config/rotation-remove.json"
            (config_dir / "rotation-remove.json").write_bytes(ROTATION_REMOVE_CONFIG)
            (db_dir / ROTATION_REMOVE_NAME).write_bytes(ROTATION_REMOVE_SQL)
            remove = release(
                version=ROTATION_REMOVE_VERSION,
                name=ROTATION_REMOVE_NAME,
                source=ROTATION_REMOVE_SQL,
                predecessor=stage.version,
                config_ref=remove_ref,
                config_source=ROTATION_REMOVE_CONFIG,
                pairs=ROTATION_REMOVE_PAIRS,
            )
            remove_trust = trust(remove, ROTATION_REMOVE_FUNCTION_CATALOG_SHA256)
            remove_manifest = (base_release, stage, remove)
            remove_trust_manifest = {
                **stage_trust_manifest,
                (remove.family, remove.version): remove_trust,
            }
            remove_order = [*stage_order, remove.migration_name]
            with self._admin_connection() as admin:
                admin.execute("REVOKE qa_mapping_owner FROM qa_release_login")
            post_admin_rotation = membership_rows()
            with (
                mock.patch.object(
                    apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", remove_manifest
                ),
                mock.patch.object(
                    apply_schema, "MAPPING_RELEASE_TRUST_MANIFEST", remove_trust_manifest
                ),
                mock.patch.object(apply_schema, "MIGRATION_ORDER", remove_order),
                psycopg.connect(next_url) as conn,
            ):
                with conn.transaction():
                    self.assertTrue(
                        apply_schema._verify_or_apply_mapping_release(conn, db_dir, remove)
                    )
                conn.execute(
                    f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                )
                with conn.transaction():
                    self.assertFalse(
                        apply_schema._verify_or_apply_mapping_release(conn, db_dir, remove)
                    )
            self.assertEqual(membership_rows(), post_admin_rotation)

            with self._admin_connection() as admin:
                admin.execute(
                    "GRANT qa_mapping_owner TO qa_release_login "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                )
            restored_old_membership = membership_rows()
            with (
                mock.patch.object(
                    apply_schema, "PERSISTENT_MAPPING_RELEASE_MANIFEST", remove_manifest
                ),
                mock.patch.object(
                    apply_schema, "MAPPING_RELEASE_TRUST_MANIFEST", remove_trust_manifest
                ),
                mock.patch.object(apply_schema, "MIGRATION_ORDER", remove_order),
                psycopg.connect(self.mapping_url) as conn,
            ):
                with self.assertRaisesRegex(psycopg.Error, "not approved"):
                    conn.execute(
                        f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                    )
                conn.rollback()
                with self.assertRaisesRegex(RuntimeError, "not approved"), conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(conn, db_dir, remove)
            self.assertEqual(membership_rows(), restored_old_membership)

    def test_policy_mapping_requires_published_policy_and_independent_evidence(self):
        with psycopg.connect(self.mapping_url) as conn:
            arguments = (
                (None, None, None, None),
                ("", "", "bad", "bad"),
                ("policy", "v1", "0" * 64, "1" * 64),
            )
            for values in arguments:
                with self.subTest(values=values):
                    self.assertFalse(
                        conn.execute(
                            f"SELECT {SCHEMA}.supplier_mapping_policy_is_published(%s,%s,%s,%s)",
                            values,
                        ).fetchone()[0]
                    )
        candidate_id = self._intake(self._packet(0))
        offer = self._legacy_offer()
        eligible_candidate = self._intake(
            self._packet(
                1,
                occurrence="policy-eligible",
                package_id="policy-eligible-package",
            )
        )
        before = self._counts()
        for action, target in (
            ("APPROVE_MAPPING", eligible_candidate),
            ("DEFER", candidate_id),
            ("REJECT_MAPPING", eligible_candidate),
        ):
            with self.subTest(action=action), psycopg.connect(self.mapping_url) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                conn.execute(
                    "SELECT pg_catalog.set_config(%s,%s,true)",
                    ("procurement.enabled_capability", "policy_mapping_writes_enabled"),
                )
                key = uuid4()
                record = mapping_service._mapping_decision_record(
                    conn,
                    candidate_id=target,
                    action=action,
                    reason="fabricated policy-origin attempt",
                    principal=self.principal,
                    decision_idempotency_key=key,
                    existing_offer_id=offer if action == "APPROVE_MAPPING" else None,
                    offer_link_kind=(
                        "LINKED_EXISTING" if action == "APPROVE_MAPPING" else None
                    ),
                )
                if action == "REJECT_MAPPING":
                    candidate, _batch = mapping_service._candidate_decision_facts(
                        conn, target
                    )
                    rejection_id = mapping_service._insert_mapping_rejection(
                        conn,
                        candidate=candidate,
                        principal=self.principal,
                        evidence_set_sha256=record["evidence_set_sha256"],
                    )
                    record["result_rejection_id"] = rejection_id
                    record["result_rejection_contract_sha256"] = conn.execute(
                        f"SELECT {SCHEMA}.persistent_mapping_rejection_contract_fingerprint(%s)",
                        (rejection_id,),
                    ).fetchone()[0]
                record.update(
                    {
                        "decision_origin": "POLICY",
                        "authority_kind": (
                            "POLICY_APPROVED"
                            if action == "APPROVE_MAPPING"
                            else None
                        ),
                        "human_principal_ref": None,
                        "human_role_ref": None,
                        "human_authn_context_sha256": None,
                        "preview_sha256": None,
                        "confirmation_sha256": None,
                        "service_principal_ref": "synthetic:policy-evaluator:01",
                        "policy_ref": "unpublished-synthetic-policy",
                        "policy_version": "v1",
                        "policy_publication_sha256": "0" * 64,
                        "policy_predicate_version": "v1",
                        "policy_predicate_result_sha256": "1" * 64,
                    }
                )
                record["request_sha256"] = mapping_service._composite_value(
                    conn,
                    table="supplier_mapping_decisions",
                    function="persistent_mapping_decision_request_sha256",
                    record=record,
                )
                payload, payload_sha = mapping_service._project_record(
                    conn,
                    table="supplier_mapping_decisions",
                    record=record,
                    omit=(
                        "mapping_decision_id",
                        "decision_idempotency_key",
                        "canonical_payload",
                        "payload_sha256",
                        "decided_at",
                        "decided_txid",
                    ),
                )
                record["canonical_payload"] = payload
                record["payload_sha256"] = payload_sha
                with self.assertRaises(psycopg.Error):
                    mapping_service._insert_record(
                        conn,
                        table="supplier_mapping_decisions",
                        record=record,
                    )
                conn.rollback()
        self.assertEqual(self._counts(), before)

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
                return self.value if isinstance(self.value, tuple) else (self.value,)

        class Info:
            transaction_status = type("Status", (), {"name": "IDLE"})()

        expected_database = self.database

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
                if "current_database" in rendered:
                    return Result(
                        (
                            expected_database,
                            "127.0.0.1/32",
                            160009,
                            "qa_release_login",
                            "qa_mapping_owner",
                        )
                    )
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
        operation_calls_after_unknown = []
        recovery_calls = []

        def idempotent_recovery(_conn):
            operation_calls_after_unknown.append(True)
            return {"replayed": False}

        def committed_lookup(_conn):
            recovery_calls.append(True)
            return {"replayed": True}

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
                recovery_lookup=committed_lookup,
            )
        self.assertTrue(uncertain.closed)
        self.assertTrue(recovery.closed)
        self.assertEqual(recovered, {"replayed": True})
        self.assertEqual(len(operation_calls_after_unknown), 1)
        self.assertEqual(len(recovery_calls), 1)

        absent_first = FakeConnection(
            commit_error=psycopg.OperationalError("lost response before commit")
        )
        absent_recovery = FakeConnection()
        absent_operations = []
        absent_lookups = []

        def absent_operation(_conn):
            absent_operations.append(True)
            return {"replayed": False}

        def absent_lookup(_conn):
            absent_lookups.append(True)
            return None

        with mock.patch.object(
            psycopg, "connect", side_effect=[absent_first, absent_recovery]
        ):
            absent_result = __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-unknown-absent",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: ((0, "matrix-domain-lock"),),
                operation=absent_operation,
                recovery_lookup=absent_lookup,
            )
        self.assertEqual(absent_result, {"replayed": False})
        self.assertEqual(len(absent_operations), 2)
        self.assertEqual(len(absent_lookups), 1)

        class NamedIdempotencyViolation(psycopg.errors.UniqueViolation):
            @property
            def diag(self):
                return type(
                    "Diagnostic",
                    (),
                    {
                        "constraint_name":
                            "uq_supplier_mapping_decisions_idempotency"
                    },
                )()

        for recovery_result, recovery_error_code in (
            ({"replayed": True}, None),
            ("CONFLICT", "IDEMPOTENCY_CONFLICT"),
            (None, "IDEMPOTENCY_PROTOCOL_VIOLATION"),
        ):
            first = FakeConnection()
            lookup_connection = FakeConnection()
            unique_operations = []
            unique_lookups = []

            def collide(_conn):
                unique_operations.append(True)
                raise NamedIdempotencyViolation("fabricated named uniqueness race")

            def lookup(_conn, recovery_result=recovery_result):
                unique_lookups.append(True)
                if recovery_result == "CONFLICT":
                    raise PersistentMappingError(
                        "different payload won the idempotency key",
                        code="IDEMPOTENCY_CONFLICT",
                    )
                return recovery_result

            context = (
                self.assertRaises(PersistentMappingError)
                if recovery_error_code is not None
                else nullcontext()
            )
            with (
                self.subTest(recovery_result=recovery_result),
                mock.patch.object(
                    psycopg, "connect", side_effect=[first, lookup_connection]
                ),
                context as unique_error,
            ):
                unique_result = __import__(
                    "procurement_os.persistent_mapping", fromlist=["_execute_write"]
                )._execute_write(
                    self.mapping_url,
                    operation_name="matrix-named-unique",
                    capability="human_mapping_writes_enabled",
                    principal=self.principal,
                    idempotency_key=uuid4(),
                    domain_locks=lambda _conn: ((0, "matrix-domain-lock"),),
                    operation=collide,
                    recovery_lookup=lookup,
                )
            if recovery_error_code is None:
                self.assertEqual(unique_result, recovery_result)
            else:
                self.assertEqual(unique_error.exception.code, recovery_error_code)
            self.assertEqual(len(unique_operations), 1)
            self.assertEqual(len(unique_lookups), 1)
            self.assertTrue(first.closed)
            self.assertTrue(lookup_connection.closed)

        ambiguous_error_first = FakeConnection(
            commit_error=psycopg.OperationalError("lost response")
        )
        ambiguous_error_recovery = FakeConnection()
        with (
            mock.patch.object(
                psycopg,
                "connect",
                side_effect=[ambiguous_error_first, ambiguous_error_recovery],
            ),
            self.assertRaises(PersistentMappingError) as ambiguous_error,
        ):
            __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-unknown-lookup-error",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: (),
                operation=lambda _conn: {"created": True},
                recovery_lookup=lambda _conn: (_ for _ in ()).throw(
                    psycopg.OperationalError("fabricated recovery read failure")
                ),
            )
        self.assertEqual(ambiguous_error.exception.code, "COMMIT_OUTCOME_UNKNOWN")

        for error_type in (
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
        ):
            exhausted = FakeConnection()
            exhausted_calls = []

            def always_retry(_conn, error_type=error_type):
                exhausted_calls.append(True)
                raise error_type("fabricated retry exhaustion")

            with (
                self.subTest(error_type=error_type.__name__),
                mock.patch.object(psycopg, "connect", return_value=exhausted),
                self.assertRaises(PersistentMappingError) as retry_error,
            ):
                __import__(
                    "procurement_os.persistent_mapping", fromlist=["_execute_write"]
                )._execute_write(
                    self.mapping_url,
                    operation_name="matrix-exhaustion",
                    capability="human_mapping_writes_enabled",
                    principal=self.principal,
                    idempotency_key=uuid4(),
                    domain_locks=lambda _conn: (),
                    operation=always_retry,
                )
            self.assertEqual(
                retry_error.exception.code,
                "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED",
            )
            self.assertEqual(len(exhausted_calls), 3)
            self.assertEqual(
                sum("pg_advisory_unlock" in item for item in exhausted.statements),
                1,
            )

        cancelled = FakeConnection()
        cancel_calls = []

        def cancel_operation(_conn):
            cancel_calls.append(True)
            raise KeyboardInterrupt("fabricated cancellation")

        with (
            mock.patch.object(psycopg, "connect", return_value=cancelled),
            self.assertRaises(KeyboardInterrupt),
        ):
            __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-cancel",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: (),
                operation=cancel_operation,
            )
        self.assertEqual(len(cancel_calls), 1)
        self.assertTrue(cancelled.closed)

        definitive = FakeConnection(
            commit_error=psycopg.errors.CheckViolation("definitive refusal")
        )
        definitive_calls = []
        with (
            mock.patch.object(psycopg, "connect", return_value=definitive),
            self.assertRaises(PersistentMappingError) as definitive_error,
        ):
            __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-definitive",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: (),
                operation=lambda _conn: definitive_calls.append(True) or {"created": True},
                recovery_lookup=lambda _conn: self.fail(
                    "a definitive commit refusal must not enter recovery"
                ),
            )
        self.assertEqual(definitive_error.exception.code, "DATABASE_VALIDATION_REFUSED")
        self.assertEqual(len(definitive_calls), 1)

        unresolved_first = FakeConnection(
            commit_error=psycopg.OperationalError("lost response")
        )
        unresolved_recovery = FakeConnection()
        unresolved_calls = []
        with (
            mock.patch.object(
                psycopg, "connect", side_effect=[unresolved_first, unresolved_recovery]
            ),
            self.assertRaises(PersistentMappingError) as unknown_error,
        ):
            __import__(
                "procurement_os.persistent_mapping", fromlist=["_execute_write"]
            )._execute_write(
                self.mapping_url,
                operation_name="matrix-unknown-no-lookup",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: (),
                operation=lambda _conn: unresolved_calls.append(True) or {"created": True},
            )
        self.assertEqual(unknown_error.exception.code, "COMMIT_OUTCOME_UNKNOWN")
        self.assertEqual(len(unresolved_calls), 1)

        import procurement_os.persistent_mapping as mapping_service

        with (
            mock.patch.object(mapping_service, "TOTAL_OPERATION_SECONDS", 0),
            mock.patch.object(psycopg, "connect") as connect,
            self.assertRaises(PersistentMappingError) as timeout_error,
        ):
            mapping_service._execute_write(
                self.mapping_url,
                operation_name="matrix-timeout",
                capability="human_mapping_writes_enabled",
                principal=self.principal,
                idempotency_key=uuid4(),
                domain_locks=lambda _conn: (),
                operation=lambda _conn: self.fail("expired operation must not begin"),
            )
        self.assertEqual(
            timeout_error.exception.code,
            "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED",
        )
        connect.assert_not_called()

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
        offer = self._legacy_offer()
        evaluation_at = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        with psycopg.connect(self.mapping_url) as conn:
            flags = conn.execute(f"SELECT current_setting('procurement.recommendation_cutover_enabled',true),current_setting('procurement.offer_activation_enabled',true)").fetchone()
            before = recommendations._load_context(
                conn,
                business_date=BUSINESS_DATE,
                variant_id="1001",
                evaluation_at=evaluation_at,
            )
        with psycopg.connect(self.mapping_url) as conn:
            run_before = recommendations.prepare_monday_run(
                conn,
                business_date=BUSINESS_DATE,
                idempotency_key="mapping-shadow-does-not-cut-over",
                variant_ids=["1001"],
                actor="synthetic:matrix-owner:01",
            )
        self.assertEqual(flags, (None, None))
        self.assertEqual(run_before["recommendations"], [])
        self.assertEqual(
            [item["message"] for item in run_before["blockers"]],
            ["CONFIRMED_VENDOR_RULES_REQUIRED"],
        )
        before_run_projection = {
            key: value
            for key, value in run_before.items()
            if key != "idempotent_replay"
        }
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="shadow parity",
            offer_id=offer,
            link_kind="LINKED_EXISTING",
        )
        self._select(decision, reason="shadow parity selection")
        with psycopg.connect(self.mapping_url) as conn:
            after = recommendations._load_context(
                conn,
                business_date=BUSINESS_DATE,
                variant_id="1001",
                evaluation_at=evaluation_at,
            )
            self.assertTrue(
                recommendations.monday_run_inputs_match(
                    conn,
                    run_before["run_id"],
                    run_before["input_fingerprint"],
                )
            )
            run_after = recommendations.get_monday_run(
                conn, run_before["run_id"]
            )
        self.assertEqual(
            recommendations._canonical_json(after),
            recommendations._canonical_json(before),
        )
        self.assertEqual(before["blockers"], ["CONFIRMED_VENDOR_RULES_REQUIRED"])
        self.assertEqual(
            {
                key: value
                for key, value in run_after.items()
                if key != "idempotent_replay"
            },
            before_run_projection,
        )
        self.assertEqual(run_after["input_fingerprint"], run_before["input_fingerprint"])


if __name__ == "__main__":
    unittest.main()
