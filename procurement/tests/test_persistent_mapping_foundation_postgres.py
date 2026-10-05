"""Exact PostgreSQL acceptance matrix for the shadow-only mapping foundation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
import copy
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event
import time
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
                conn.execute(
                    "DROP ROLE IF EXISTS qa_transitive_probe,"
                    "qa_transitive_mid,qa_transitive_leaf"
                )
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

    def _reset_with_grandfathered_current_price(
        self,
        *,
        variant_id: str = "1001",
        sku: str = "LEGACY-PRICED",
    ) -> tuple[int, int]:
        """Install 011+ after an authentic pre-011 CURRENT price exists."""

        self._prepare_roles_and_schema()
        cutoff = apply_schema.MIGRATION_ORDER.index(
            "010_monday_po_ledger.sql"
        ) + 1
        with psycopg.connect(self.mapping_url) as conn:
            schema_oid = int(
                conn.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (SCHEMA,),
                ).fetchone()[0]
            )
            for name in apply_schema.MIGRATION_ORDER[:cutoff]:
                with conn.transaction():
                    apply_schema.apply_verified_legacy_file(
                        conn, DB_DIR, name, schema_oid=schema_oid
                    )
        self._seed_catalog()
        offer_id = self._legacy_offer(variant_id=variant_id, sku=sku)
        with psycopg.connect(self.mapping_url) as conn:
            price_id = int(
                conn.execute(
                    f"INSERT INTO {SCHEMA}.prices("
                    "offer_id,price_state,effective_month,level_type,unit_price,"
                    "source_file,source_page,extraction_confidence,verified,notes"
                    ") VALUES(%s,'current',DATE '2026-09-01','BASE',5.2500,"
                    "'matrix-pre-011-current.csv',1,'VERIFIED',true,"
                    "'authentic pre-011 CURRENT evidence') RETURNING price_id",
                    (offer_id,),
                ).fetchone()[0]
            )
        with psycopg.connect(self.mapping_url) as conn:
            schema_oid = int(
                conn.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (SCHEMA,),
                ).fetchone()[0]
            )
            for name in apply_schema.MIGRATION_ORDER[cutoff:-1]:
                with conn.transaction():
                    apply_schema.apply_verified_legacy_file(
                        conn, DB_DIR, name, schema_oid=schema_oid
                    )
            with conn.transaction():
                self.assertTrue(
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
                )
            with conn.transaction():
                self.assertTrue(
                    apply_schema._verify_or_apply_post_mapping_release(conn, DB_DIR)
                )
        return offer_id, price_id

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
                "source_authority_state": package["source_authority_state"],
                "source_import_state": package["source_import_state"],
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

    def _schema_snapshot(self, conn: Any) -> dict[str, object]:
        """Return exact row and catalog evidence for the isolated target schema."""

        table_names = [
            str(row[0])
            for row in conn.execute(
                "SELECT c.relname FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relkind IN ('r','p') "
                "ORDER BY c.relname",
                (SCHEMA,),
            ).fetchall()
        ]
        table_rows: list[tuple[str, tuple[str, ...]]] = []
        for table_name in table_names:
            rows = conn.execute(
                sql.SQL(
                    "SELECT pg_catalog.to_jsonb(t)::text AS row_value "
                    "FROM {}.{} AS t ORDER BY row_value"
                ).format(
                    sql.Identifier(SCHEMA),
                    sql.Identifier(table_name),
                )
            ).fetchall()
            table_rows.append(
                (table_name, tuple(str(row[0]) for row in rows))
            )
        return {
            "table_rows": tuple(table_rows),
            "schema": tuple(
                conn.execute(
                    "SELECT n.oid,n.nspname,"
                    "n.nspowner::pg_catalog.regrole::text,n.nspacl::text "
                    "FROM pg_catalog.pg_namespace n WHERE n.nspname=%s",
                    (SCHEMA,),
                ).fetchall()
            ),
            "extensions": tuple(
                conn.execute(
                    "SELECT e.oid,e.extname,e.extversion,e.extnamespace,"
                    "e.extowner::pg_catalog.regrole::text,e.extrelocatable,"
                    "d.classid::pg_catalog.regclass::text,d.objid,d.objsubid,"
                    "d.refclassid::pg_catalog.regclass::text,d.refobjid,"
                    "d.refobjsubid,d.deptype "
                    "FROM pg_catalog.pg_extension e "
                    "LEFT JOIN pg_catalog.pg_depend d "
                    "ON d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass "
                    "AND d.refobjid=e.oid WHERE e.extname='pgcrypto' "
                    "ORDER BY d.classid,d.objid,d.objsubid,d.deptype"
                ).fetchall()
            ),
            "relations": tuple(
                conn.execute(
                    "SELECT c.oid,c.relname,c.relkind,c.relpersistence,"
                    "c.relowner::pg_catalog.regrole::text,c.relrowsecurity,"
                    "c.relforcerowsecurity,c.relreplident,c.relacl::text "
                    "FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s ORDER BY c.relname,c.relkind",
                    (SCHEMA,),
                ).fetchall()
            ),
            "columns": tuple(
                conn.execute(
                    "SELECT c.relname,a.attnum,a.attname,"
                    "pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,"
                    "a.attidentity,a.attgenerated,"
                    "a.attacl::text,coll.collname,"
                    "pg_catalog.pg_get_expr(d.adbin,d.adrelid,true) "
                    "FROM pg_catalog.pg_attribute a "
                    "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "LEFT JOIN pg_catalog.pg_attrdef d "
                    "ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
                    "LEFT JOIN pg_catalog.pg_collation coll "
                    "ON coll.oid=a.attcollation "
                    "WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped "
                    "ORDER BY c.relname,a.attnum",
                    (SCHEMA,),
                ).fetchall()
            ),
            "constraints": tuple(
                conn.execute(
                    "SELECT c.relname,k.conname,k.contype,k.condeferrable,"
                    "k.condeferred,k.convalidated,"
                    "pg_catalog.pg_get_constraintdef(k.oid,true) "
                    "FROM pg_catalog.pg_constraint k "
                    "JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s ORDER BY c.relname,k.conname",
                    (SCHEMA,),
                ).fetchall()
            ),
            "indexes": tuple(
                conn.execute(
                    "SELECT t.relname,i.oid,i.relname,"
                    "i.relowner::pg_catalog.regrole::text,i.relacl::text,"
                    "x.indisunique,x.indisprimary,x.indisexclusion,"
                    "x.indimmediate,x.indisvalid,x.indisready,x.indislive,"
                    "x.indisreplident,x.indnullsnotdistinct,x.indnkeyatts,"
                    "x.indnatts,pg_catalog.pg_get_indexdef(i.oid),"
                    "pg_catalog.pg_get_expr(x.indpred,x.indrelid,true),"
                    "pg_catalog.pg_get_expr(x.indexprs,x.indrelid,true) "
                    "FROM pg_catalog.pg_index x "
                    "JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid "
                    "JOIN pg_catalog.pg_class t ON t.oid=x.indrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace "
                    "WHERE n.nspname=%s ORDER BY t.relname,i.relname",
                    (SCHEMA,),
                ).fetchall()
            ),
            "functions": tuple(
                conn.execute(
                    "SELECT p.oid,p.proname,"
                    "pg_catalog.pg_get_function_identity_arguments(p.oid),"
                    "pg_catalog.pg_get_function_result(p.oid),p.prokind,"
                    "l.lanname,p.provolatile,p.prosecdef,p.proisstrict,"
                    "p.proleakproof,p.proparallel,p.proretset,p.pronargs,"
                    "p.pronargdefaults,"
                    "pg_catalog.pg_get_expr(p.proargdefaults,0),p.proconfig,"
                    "p.probin,p.proacl::text,"
                    "p.proowner::pg_catalog.regrole::text,p.prosrc "
                    "FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
                    "WHERE n.nspname=%s ORDER BY p.proname,p.oid",
                    (SCHEMA,),
                ).fetchall()
            ),
            "triggers": tuple(
                conn.execute(
                    "SELECT c.relname,t.oid,t.tgname,t.tgenabled,t.tgisinternal,"
                    "t.tgtype,t.tgdeferrable,t.tginitdeferred,t.tgattr,"
                    "p.oid,p.proname,"
                    "pg_catalog.pg_get_function_identity_arguments(p.oid),"
                    "pg_catalog.pg_get_triggerdef(t.oid,true),t.tgqual::text,"
                    "pg_catalog.encode(t.tgargs,'escape') "
                    "FROM pg_catalog.pg_trigger t "
                    "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
                    "WHERE n.nspname=%s AND NOT t.tgisinternal "
                    "ORDER BY c.relname,t.tgname",
                    (SCHEMA,),
                ).fetchall()
            ),
            "views": tuple(
                conn.execute(
                    "SELECT c.relname,pg_catalog.pg_get_viewdef(c.oid,true) "
                    "FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relkind IN ('v','m') "
                    "ORDER BY c.relname",
                    (SCHEMA,),
                ).fetchall()
            ),
            "types": tuple(
                conn.execute(
                    "SELECT t.oid,t.typname,t.typtype,t.typcategory,t.typispreferred,"
                    "t.typnotnull,t.typbasetype,t.typtypmod,t.typcollation,"
                    "t.typowner::pg_catalog.regrole::text,t.typacl::text "
                    "FROM pg_catalog.pg_type t "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace "
                    "WHERE n.nspname=%s ORDER BY t.typname,t.oid",
                    (SCHEMA,),
                ).fetchall()
            ),
            "default_acls": tuple(
                conn.execute(
                    "SELECT d.oid,d.defaclrole::pg_catalog.regrole::text,"
                    "COALESCE(n.nspname,'<GLOBAL>'),d.defaclobjtype,"
                    "d.defaclacl::text FROM pg_catalog.pg_default_acl d "
                    "LEFT JOIN pg_catalog.pg_namespace n "
                    "ON n.oid=d.defaclnamespace "
                    "WHERE d.defaclrole=(SELECT oid FROM pg_catalog.pg_roles "
                    "WHERE rolname='qa_mapping_owner') "
                    "AND (d.defaclnamespace=0 OR n.nspname=%s) "
                    "ORDER BY d.oid",
                    (SCHEMA,),
                ).fetchall()
            ),
            "role_memberships": tuple(
                conn.execute(
                    "SELECT member.rolname,granted.rolname,grantor.rolname,"
                    "m.inherit_option,m.set_option,m.admin_option "
                    "FROM pg_catalog.pg_auth_members m "
                    "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
                    "JOIN pg_catalog.pg_roles granted ON granted.oid=m.roleid "
                    "JOIN pg_catalog.pg_roles grantor ON grantor.oid=m.grantor "
                    "ORDER BY member.rolname,granted.rolname,grantor.rolname"
                ).fetchall()
            ),
            "sequences": tuple(
                conn.execute(
                    "SELECT sequenceowner,sequencename,last_value,start_value,increment_by,"
                    "max_value,min_value,cycle,cache_size "
                    "FROM pg_catalog.pg_sequences WHERE schemaname=%s "
                    "ORDER BY sequencename",
                    (SCHEMA,),
                ).fetchall()
            ),
        }

    def test_upgrade_requires_exact_013_marker_set_and_contracts(self):
        corruptions = (
            (
                "012-only predecessor marker set",
                f"DELETE FROM {SCHEMA}.meta "
                "WHERE key='migration:013_monday_p1_remediation.sql'",
            ),
            (
                "missing 012 predecessor marker",
                f"DELETE FROM {SCHEMA}.meta "
                "WHERE key='migration:012_monday_review_draft_packet.sql'",
            ),
            (
                "unknown intervening marker",
                f"INSERT INTO {SCHEMA}.meta(key,value) "
                "VALUES('migration:012a_unknown.sql','applied')",
            ),
            (
                "missing required index",
                f"DROP INDEX {SCHEMA}.uq_active_vendor_supplier_sku",
            ),
            (
                "missing required trigger",
                f"DROP TRIGGER trg_prevent_referenced_offer_identity_change "
                f"ON {SCHEMA}.supplier_offers",
            ),
            (
                "wrong referenced-offer update column list",
                f"DROP TRIGGER trg_prevent_referenced_offer_identity_change "
                f"ON {SCHEMA}.supplier_offers; "
                f"CREATE TRIGGER trg_prevent_referenced_offer_identity_change "
                f"BEFORE UPDATE OF variant_id ON {SCHEMA}.supplier_offers "
                f"FOR EACH ROW EXECUTE FUNCTION "
                f"{SCHEMA}.prevent_referenced_offer_identity_change()",
            ),
            (
                "altered predecessor trigger function",
                f"CREATE OR REPLACE FUNCTION "
                f"{SCHEMA}.prevent_referenced_offer_identity_change() "
                "RETURNS trigger LANGUAGE plpgsql "
                "AS $$BEGIN RETURN NEW; END$$",
            ),
            (
                "altered predecessor contract",
                f"UPDATE {SCHEMA}.meta SET value='altered' "
                "WHERE key='monday_p1_remediation_contract'",
            ),
        )
        for label, statement in corruptions:
            with self.subTest(case=label):
                self._reset(include_mapping=False)
                with psycopg.connect(self.mapping_url) as conn:
                    conn.execute(statement)
                    conn.commit()
                    before = self._schema_snapshot(conn)
                    with self.assertRaises((RuntimeError, psycopg.Error)):
                        with conn.transaction():
                            apply_schema._verify_or_apply_mapping_release(
                                conn, DB_DIR
                            )
                    self.assertEqual(self._schema_snapshot(conn), before)
                    self.assertEqual(
                        conn.execute(
                            "SELECT count(*) FROM pg_catalog.pg_class c "
                            "JOIN pg_catalog.pg_namespace n "
                            "ON n.oid=c.relnamespace "
                            "WHERE n.nspname=%s AND c.relname=ANY(%s)",
                            (
                                SCHEMA,
                                [
                                    "supplier_mapping_review_batches",
                                    "supplier_mapping_review_candidates",
                                    "supplier_mapping_decisions",
                                    "supplier_offer_selection_events",
                                    "supplier_offer_selection_heads",
                                ],
                            ),
                        ).fetchone()[0],
                        0,
                    )

    def test_fresh_schema_applies_foundation_once_and_reapplies_idempotently(self):
        self._prepare_roles_and_schema()
        raw_sources = {
            (DB_DIR / name).read_text(encoding="utf-8"): name
            for name in (
                *apply_schema.MIGRATION_ORDER,
                apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
            )
        }
        extension_transitions: list[tuple[str, bool, bool]] = []
        pgcrypto_verifications: list[tuple[bool, bool]] = []
        resolution_verifications: list[bool] = []
        original_pgcrypto_verifier = apply_schema._verify_pgcrypto
        original_resolution_verifier = (
            apply_schema._verify_controlled_digest_resolution
        )

        class ObservedConnection:
            def __init__(self, connection):
                self._connection = connection

            def execute(self, statement, parameters=None):
                migration_name = (
                    raw_sources.get(statement)
                    if isinstance(statement, str)
                    else None
                )
                before = (
                    apply_schema._pgcrypto_installed(self._connection)
                    if migration_name is not None
                    else None
                )
                result = (
                    self._connection.execute(statement)
                    if parameters is None
                    else self._connection.execute(statement, parameters)
                )
                if migration_name is not None:
                    extension_transitions.append(
                        (
                            migration_name,
                            bool(before),
                            apply_schema._pgcrypto_installed(self._connection),
                        )
                    )
                return result

            def __getattr__(self, name):
                return getattr(self._connection, name)

        def observe_pgcrypto(conn, *, schema_oid: int, release=apply_schema.MAPPING_RELEASE):
            result = original_pgcrypto_verifier(
                conn, schema_oid=schema_oid, release=release
            )
            marker_exists = conn.execute(
                sql.SQL(
                    "SELECT EXISTS(SELECT 1 FROM {}.meta WHERE key=%s)"
                ).format(sql.Identifier(SCHEMA)),
                ("migration:schema_postgres.sql",),
            ).fetchone()[0]
            pgcrypto_verifications.append(
                (apply_schema._pgcrypto_installed(conn), bool(marker_exists))
            )
            return result

        def observe_resolution(conn):
            result = original_resolution_verifier(conn)
            marker_exists = conn.execute(
                sql.SQL(
                    "SELECT EXISTS(SELECT 1 FROM {}.meta WHERE key=%s)"
                ).format(sql.Identifier(SCHEMA)),
                ("migration:schema_postgres.sql",),
            ).fetchone()[0]
            resolution_verifications.append(bool(marker_exists))
            return result

        with psycopg.connect(self.mapping_url) as raw_conn:
            self.assertFalse(apply_schema._pgcrypto_installed(raw_conn))
            schema_oid = int(
                raw_conn.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (SCHEMA,),
                ).fetchone()[0]
            )
            self.assertEqual(
                raw_conn.execute(
                    "SELECT count(*) FROM pg_catalog.pg_class "
                    "WHERE relnamespace=%s",
                    (schema_oid,),
                ).fetchone()[0],
                0,
            )
            raw_conn.rollback()
            observed_conn = ObservedConnection(raw_conn)
            with (
                mock.patch.object(
                    apply_schema,
                    "_verify_pgcrypto",
                    side_effect=observe_pgcrypto,
                ),
                mock.patch.object(
                    apply_schema,
                    "_verify_controlled_digest_resolution",
                    side_effect=observe_resolution,
                ),
            ):
                applied = apply_schema.apply_schema_connection(
                    observed_conn, DB_DIR, include_persistent_mapping=True
                )
            self.assertEqual(
                applied,
                [
                    *apply_schema.MIGRATION_ORDER,
                    apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                ],
            )
            self.assertEqual(
                extension_transitions[0],
                ("schema_postgres.sql", False, True),
            )
            self.assertTrue(
                all(
                    before and after
                    for _name, before, after in extension_transitions[1:]
                )
            )
            self.assertTrue(pgcrypto_verifications)
            self.assertEqual(pgcrypto_verifications[0], (True, False))
            self.assertTrue(all(item[0] for item in pgcrypto_verifications))
            self.assertTrue(resolution_verifications)
            self.assertFalse(resolution_verifications[0])

            extension = raw_conn.execute(
                "SELECT e.extversion,n.nspname,"
                "pg_catalog.pg_get_userbyid(e.extowner) "
                "FROM pg_catalog.pg_extension e "
                "JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace "
                "WHERE e.extname='pgcrypto'"
            ).fetchone()
            self.assertEqual(extension, ("1.3", SCHEMA, "qa_mapping_owner"))
            member_rows = raw_conn.execute(
                "SELECT p.oid,pg_catalog.pg_get_function_identity_arguments(p.oid),"
                "n.nspname,d.deptype "
                "FROM pg_catalog.pg_extension e "
                "JOIN pg_catalog.pg_depend d "
                "ON d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass "
                "AND d.refobjid=e.oid "
                "AND d.classid='pg_catalog.pg_proc'::pg_catalog.regclass "
                "JOIN pg_catalog.pg_proc p ON p.oid=d.objid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "WHERE e.extname='pgcrypto' AND p.proname='digest' "
                "AND pg_catalog.pg_get_function_identity_arguments(p.oid) "
                "IN ('bytea, text','text, text') ORDER BY 2"
            ).fetchall()
            self.assertEqual(
                [(row[1], row[2], row[3]) for row in member_rows],
                [
                    ("bytea, text", SCHEMA, "e"),
                    ("text, text", SCHEMA, "e"),
                ],
            )
            self.assertEqual(
                raw_conn.execute(
                    "SELECT pg_catalog.to_regprocedure('digest(bytea,text)')::oid"
                ).fetchone()[0],
                member_rows[0][0],
            )

            relation_names = [
                "supplier_mapping_review_batches",
                "supplier_mapping_review_candidates",
                "supplier_mapping_decisions",
                "supplier_offer_selection_events",
                "supplier_offer_selection_heads",
                "v_effective_supplier_mapping_decisions",
                "v_supplier_offer_selection_diagnostics",
                "v_supplier_offer_selection_shadow",
                "v_selected_standard_supplier_offers",
            ]
            relations = raw_conn.execute(
                "SELECT relkind,count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s AND c.relname=ANY(%s) GROUP BY relkind",
                (SCHEMA, relation_names),
            ).fetchall()
            self.assertEqual(dict(relations), {"r": 5, "v": 4})
            self.assertEqual(
                raw_conn.execute(
                    "SELECT count(*) FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "CROSS JOIN LATERAL pg_catalog.aclexplode(c.relacl) acl "
                    "WHERE n.nspname=%s AND c.relname=ANY(%s) "
                    "AND acl.grantee<>c.relowner",
                    (SCHEMA, relation_names),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                raw_conn.execute(
                    "SELECT count(*) FROM pg_catalog.pg_attribute a "
                    "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) acl "
                    "WHERE n.nspname=%s AND c.relname=ANY(%s) "
                    "AND a.attnum>0 AND NOT a.attisdropped "
                    "AND acl.grantee<>c.relowner",
                    (SCHEMA, relation_names),
                ).fetchone()[0],
                0,
            )
            trusted_function_names = sorted(
                {item[0] for item in apply_schema.TRUSTED_FUNCTION_IDENTITIES}
            )
            self.assertEqual(
                raw_conn.execute(
                    "SELECT count(*) FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "CROSS JOIN LATERAL pg_catalog.aclexplode(p.proacl) acl "
                    "WHERE n.nspname=%s AND p.proname=ANY(%s) "
                    "AND acl.grantee<>p.proowner",
                    (SCHEMA, trusted_function_names),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                raw_conn.execute(
                    "SELECT count(*) FROM pg_catalog.pg_default_acl d "
                    "LEFT JOIN pg_catalog.pg_namespace n "
                    "ON n.oid=d.defaclnamespace "
                    "CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) acl "
                    "WHERE d.defaclrole='qa_mapping_owner'::pg_catalog.regrole "
                    "AND (n.nspname=%s OR d.defaclnamespace=0) "
                    "AND d.defaclobjtype IN ('r','f') "
                    "AND acl.grantee<>d.defaclrole",
                    (SCHEMA,),
                ).fetchone()[0],
                0,
            )
            schema_oid = int(
                raw_conn.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (SCHEMA,),
                ).fetchone()[0]
            )
            apply_schema._verify_installed_release(
                raw_conn,
                release=apply_schema.MAPPING_RELEASE,
                schema_oid=schema_oid,
                allowed_pairs=apply_schema._maintenance_binding(
                    DB_DIR, apply_schema.MAPPING_RELEASE
                ),
            )
            catalog_sha = raw_conn.execute(
                f"SELECT {SCHEMA}.compute_persistent_mapping_catalog_sha256()"
            ).fetchone()[0]
            self.assertEqual(
                raw_conn.execute(
                    f"SELECT value FROM {SCHEMA}.meta "
                    "WHERE key='persistent_mapping_foundation_catalog_sha256'"
                ).fetchone()[0],
                catalog_sha,
            )
            raw_conn.execute(
                f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
            )
            raw_conn.rollback()

            self._seed_catalog()
            self._legacy_offer(sku="FRESH-REPLAY")
            self._intake(
                self._packet(
                    1,
                    occurrence="fresh-replay-candidate",
                    package_id="fresh-replay-package",
                    candidate_changes={
                        "supplier_code_value": "FRESH-REPLAY",
                        "distributor_product_id_value": "FRESH-REPLAY",
                    },
                )
            )
            before_replay = self._schema_snapshot(raw_conn)
            self.assertEqual(
                apply_schema.apply_schema_connection(
                    raw_conn, DB_DIR, include_persistent_mapping=True
                ),
                [],
            )
            self.assertEqual(self._schema_snapshot(raw_conn), before_replay)

            raw_conn.execute(
                f"GRANT SELECT ON {SCHEMA}.supplier_mapping_review_batches "
                "TO qa_release_login"
            )
            raw_conn.commit()
            tampered = self._schema_snapshot(raw_conn)
            with self.assertRaises((RuntimeError, psycopg.Error)):
                with raw_conn.transaction():
                    apply_schema.apply_schema_connection(
                        raw_conn, DB_DIR, include_persistent_mapping=True
                    )
            self.assertEqual(self._schema_snapshot(raw_conn), tampered)

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
                def release_state():
                    metadata = tuple(
                        conn.execute(
                            f"SELECT key,value FROM {SCHEMA}.meta "
                            "WHERE key=ANY(%s) ORDER BY key",
                            (
                                [
                                    f"migration:{apply_schema.MAPPING_MIGRATION_NAME}",
                                    f"migration:{TEST_V2_NAME}",
                                    "persistent_mapping_foundation_contract",
                                    "persistent_mapping_foundation_catalog_sha256",
                                ],
                            ),
                        ).fetchall()
                    )
                    anchors = tuple(
                        conn.execute(
                            "SELECT p.proname,p.provolatile,p.prosecdef,p.proparallel,"
                            "p.proconfig,p.prosrc FROM pg_catalog.pg_proc p "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                            "WHERE n.nspname=%s AND p.proname=ANY(%s) "
                            "ORDER BY p.proname,p.oid",
                            (
                                SCHEMA,
                                [
                                    "assert_persistent_mapping_foundation_contract",
                                    "compute_persistent_mapping_catalog_sha256",
                                ],
                            ),
                        ).fetchall()
                    )
                    return metadata, anchors

                before = release_state()
                metadata = dict(before[0])
                self.assertEqual(
                    metadata[f"migration:{apply_schema.MAPPING_MIGRATION_NAME}"],
                    f"sha256:{apply_schema.MAPPING_RELEASE.migration_sha256}",
                )
                self.assertEqual(
                    metadata[f"migration:{TEST_V2_NAME}"],
                    f"sha256:{v2_release.migration_sha256}",
                )
                self.assertEqual(
                    metadata["persistent_mapping_foundation_contract"],
                    TEST_V2_VERSION,
                )
                self.assertEqual(
                    metadata["persistent_mapping_foundation_catalog_sha256"],
                    conn.execute(
                        f"SELECT {SCHEMA}.compute_persistent_mapping_catalog_sha256()"
                    ).fetchone()[0],
                )

                body_submissions: list[str] = []
                release_text = {
                    (db_dir / apply_schema.MAPPING_MIGRATION_NAME).read_text(
                        encoding="utf-8"
                    ): apply_schema.MAPPING_MIGRATION_NAME,
                    TEST_V2_SQL.decode("utf-8"): TEST_V2_NAME,
                }

                class ReplayRecordingConnection:
                    def execute(self, statement, parameters=None):
                        if isinstance(statement, str) and statement in release_text:
                            body_submissions.append(release_text[statement])
                        if parameters is None:
                            return conn.execute(statement)
                        return conn.execute(statement, parameters)

                    def __getattr__(self, name):
                        return getattr(conn, name)

                replay_connection = ReplayRecordingConnection()
                with conn.transaction():
                    self.assertFalse(
                        apply_schema._verify_or_apply_mapping_release(
                            replay_connection, db_dir, apply_schema.MAPPING_RELEASE
                        )
                    )
                    self.assertFalse(
                        apply_schema._verify_or_apply_mapping_release(
                            replay_connection, db_dir, v2_release
                        )
                    )
                after = release_state()
                self.assertEqual(before, after)
                self.assertEqual(body_submissions, [])
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
                    (
                        f"DELETE FROM {SCHEMA}.meta WHERE key='migration:"
                        f"{apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name}'",
                        "required post-mapping application release is absent",
                    ),
                )
                for statement, message in corruptions:
                    with self.subTest(message=message), conn.transaction(force_rollback=True):
                        conn.execute(statement)
                        with self.assertRaisesRegex(RuntimeError, message):
                            apply_schema._verify_or_apply_mapping_release(conn, db_dir, v2_release)
                with self.subTest(
                    message="historical replay requires installed application dependency"
                ), conn.transaction(force_rollback=True):
                    conn.execute(
                        f"DELETE FROM {SCHEMA}.meta WHERE key='migration:"
                        f"{apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name}'"
                    )
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "required post-mapping application release is absent",
                    ):
                        apply_schema._verify_or_apply_mapping_release(
                            conn, db_dir, apply_schema.MAPPING_RELEASE
                        )
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
        exact_path = f'pg_catalog, "{SCHEMA}", "{SCHEMA}", pg_temp'
        corruptions = (
            (
                "no-op assertion",
                f"CREATE OR REPLACE FUNCTION "
                f"{SCHEMA}.assert_persistent_mapping_foundation_contract() "
                "RETURNS void LANGUAGE plpgsql STABLE "
                f"SET search_path={exact_path} AS $$BEGIN RETURN; END$$",
            ),
            (
                "compute returns stored hash",
                f"CREATE OR REPLACE FUNCTION "
                f"{SCHEMA}.compute_persistent_mapping_catalog_sha256() "
                "RETURNS text LANGUAGE sql STABLE "
                f"SET search_path={exact_path} AS $$SELECT value FROM "
                f"{SCHEMA}.meta WHERE key="
                "'persistent_mapping_foundation_catalog_sha256'$$",
            ),
            (
                "coordinated assertion and compute replacement",
                f"CREATE OR REPLACE FUNCTION "
                f"{SCHEMA}.compute_persistent_mapping_catalog_sha256() "
                "RETURNS text LANGUAGE sql STABLE "
                f"SET search_path={exact_path} AS $$SELECT value FROM "
                f"{SCHEMA}.meta WHERE key="
                "'persistent_mapping_foundation_catalog_sha256'$$; "
                f"CREATE OR REPLACE FUNCTION "
                f"{SCHEMA}.assert_persistent_mapping_foundation_contract() "
                "RETURNS void LANGUAGE plpgsql STABLE "
                f"SET search_path={exact_path} AS $$BEGIN "
                "RAISE EXCEPTION 'UNTRUSTED_ASSERTION_WAS_CALLED'; END$$",
            ),
            (
                "altered assertion volatility",
                f"ALTER FUNCTION "
                f"{SCHEMA}.assert_persistent_mapping_foundation_contract() VOLATILE",
            ),
            (
                "altered compute search path",
                f"ALTER FUNCTION "
                f"{SCHEMA}.compute_persistent_mapping_catalog_sha256() "
                "SET search_path=pg_catalog",
            ),
            (
                "missing assertion anchor",
                f"DROP FUNCTION "
                f"{SCHEMA}.assert_persistent_mapping_foundation_contract()",
            ),
            (
                "missing compute anchor",
                f"DROP FUNCTION "
                f"{SCHEMA}.compute_persistent_mapping_catalog_sha256()",
            ),
            (
                "missing signed helper",
                f"DROP FUNCTION {SCHEMA}.persistent_mapping_json_sha256(jsonb) "
                "CASCADE",
            ),
            (
                "unknown contract version",
                f"UPDATE {SCHEMA}.meta SET value='unknown-release' "
                "WHERE key='persistent_mapping_foundation_contract'",
            ),
            (
                "same-named digest without extension membership",
                "DROP EXTENSION pgcrypto CASCADE; "
                f"CREATE FUNCTION {SCHEMA}.digest(bytea,text) RETURNS bytea "
                "LANGUAGE sql IMMUTABLE STRICT AS $$SELECT $1$$",
            ),
        )
        for label, statement in corruptions:
            with self.subTest(case=label):
                self._reset(include_mapping=True)
                with psycopg.connect(self.mapping_url) as conn:
                    conn.execute(statement)
                    conn.commit()
                    before = self._schema_snapshot(conn)
                    authority_before = self._counts()
                    submitted_anchor_calls: list[str] = []
                    submitted_mutations: list[str] = []
                    mapping_source = (
                        DB_DIR / apply_schema.MAPPING_MIGRATION_NAME
                    ).read_text(encoding="utf-8")

                    class ReplayProbeConnection:
                        def execute(self, command, parameters=None):
                            rendered = (
                                command.as_string(conn)
                                if hasattr(command, "as_string")
                                else str(command)
                            )
                            compact = " ".join(rendered.split())
                            if (
                                f'{SCHEMA}.compute_persistent_mapping_catalog_sha256()'
                                in compact
                                or f'{SCHEMA}.assert_persistent_mapping_foundation_contract()'
                                in compact
                                or f'"{SCHEMA}".compute_persistent_mapping_catalog_sha256()'
                                in compact
                                or f'"{SCHEMA}".assert_persistent_mapping_foundation_contract()'
                                in compact
                            ):
                                submitted_anchor_calls.append(compact)
                            if (
                                rendered == mapping_source
                                or compact.upper().startswith(
                                    ("INSERT ", "UPDATE ", "DELETE ")
                                )
                            ):
                                submitted_mutations.append(compact)
                            if parameters is None:
                                return conn.execute(command)
                            return conn.execute(command, parameters)

                        def __getattr__(self, name):
                            return getattr(conn, name)

                    probed_conn = ReplayProbeConnection()
                    with self.assertRaises(
                        (RuntimeError, psycopg.Error)
                    ) as refused:
                        with conn.transaction():
                            apply_schema._verify_or_apply_mapping_release(
                                probed_conn, DB_DIR
                            )
                    self.assertNotIn(
                        "UNTRUSTED_ASSERTION_WAS_CALLED", str(refused.exception)
                    )
                    self.assertEqual(submitted_anchor_calls, [])
                    self.assertEqual(submitted_mutations, [])
                    self.assertEqual(self._schema_snapshot(conn), before)
                    self.assertEqual(self._counts(), authority_before)

        self._reset(include_mapping=True)
        with psycopg.connect(self.mapping_url) as conn, conn.transaction():
            self.assertFalse(
                apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            )

    def test_mapping_family_lock_serializes_marker_without_claiming_whole_run_atomicity(self):
        self._reset(include_mapping=False)
        barrier = Barrier(2)
        lock_requests = {label: Event() for label in ("canonical", "changed")}
        real_contention_observed = Event()
        poll_interval = Event()
        backend_pids: dict[str, int] = {}
        body_submissions: list[str] = []
        admin_connection = self._admin_connection

        def load_runner(label: str):
            module_name = f"mapping_apply_{label}_{uuid4().hex}"
            spec = importlib.util.spec_from_file_location(
                module_name, Path(apply_schema.__file__).resolve()
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            self.addCleanup(sys.modules.pop, module_name, None)
            spec.loader.exec_module(module)
            return module

        class RecordingConnection:
            def __init__(self, connection, *, label: str, migration_bytes: bytes):
                self._connection = connection
                self._label = label
                self._migration_text = migration_bytes.decode("utf-8")
                self._mapping_lock_seen = False

            def execute(self, statement, parameters=None):
                rendered = str(statement)
                is_mapping_lock = (
                    not self._mapping_lock_seen
                    and "pg_advisory_xact_lock" in rendered
                    and parameters
                    and "persistent-mapping-foundation" in str(parameters[0])
                )
                if is_mapping_lock:
                    self._mapping_lock_seen = True
                    lock_requests[self._label].set()
                if statement == self._migration_text:
                    other_label = (
                        "changed" if self._label == "canonical" else "canonical"
                    )
                    if not lock_requests[other_label].wait(timeout=10):
                        raise AssertionError(
                            "competing candidate never requested the mapping lock"
                        )
                    waiting_pid = backend_pids[other_label]
                    with admin_connection() as observer:
                        for _attempt in range(400):
                            blocked = observer.execute(
                                "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_locks "
                                "WHERE pid=%s AND locktype='advisory' AND NOT granted)",
                                (waiting_pid,),
                            ).fetchone()[0]
                            if blocked:
                                real_contention_observed.set()
                                break
                            poll_interval.wait(0.025)
                    if not real_contention_observed.is_set():
                        raise AssertionError(
                            "competing backend was never blocked on the advisory lock"
                        )
                    body_submissions.append(self._label)
                if parameters is None:
                    result = self._connection.execute(statement)
                else:
                    result = self._connection.execute(statement, parameters)
                return result

            def __getattr__(self, name):
                return getattr(self._connection, name)

        with TemporaryDirectory(prefix="buffalo-mapping-first-apply-race-") as temporary:
            roots: dict[str, Path] = {}
            migration_bytes: dict[str, bytes] = {}
            modules = {label: load_runner(label) for label in ("canonical", "changed")}
            for label in modules:
                root = Path(temporary) / label
                db_dir = root / "db"
                config_dir = root / "config"
                shutil.copytree(DB_DIR, db_dir)
                config_dir.mkdir()
                shutil.copyfile(
                    DB_DIR.parent
                    / apply_schema.MAPPING_RELEASE.maintenance_identity_config_ref,
                    config_dir / "persistent_mapping_maintenance.synthetic.json",
                )
                roots[label] = db_dir
            changed_path = roots["changed"] / apply_schema.MAPPING_MIGRATION_NAME
            changed_bytes = changed_path.read_bytes() + (
                b"-- TEST-ONLY different reviewed first-apply candidate bytes.\n"
            )
            changed_path.write_bytes(changed_bytes)
            migration_bytes["canonical"] = (
                roots["canonical"] / apply_schema.MAPPING_MIGRATION_NAME
            ).read_bytes()
            migration_bytes["changed"] = changed_bytes

            changed_module = modules["changed"]
            changed_release = replace(
                changed_module.MAPPING_RELEASE,
                migration_sha256=hashlib.sha256(changed_bytes).hexdigest(),
            )
            changed_trust = changed_module.MAPPING_RELEASE_TRUST_MANIFEST[
                (changed_release.family, changed_release.version)
            ]
            changed_module.MAPPING_RELEASE = changed_release
            changed_module.PERSISTENT_MAPPING_RELEASE_MANIFEST = (changed_release,)
            changed_module.MAPPING_RELEASE_TRUST_MANIFEST = {
                (changed_release.family, changed_release.version): changed_trust
            }
            changed_retirement_path = (
                roots["changed"]
                / changed_module.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name
            )
            changed_retirement_bytes = changed_retirement_path.read_bytes()
            predecessor_literal = apply_schema.MAPPING_RELEASE.migration_sha256.encode()
            self.assertEqual(changed_retirement_bytes.count(predecessor_literal), 1)
            changed_retirement_bytes = changed_retirement_bytes.replace(
                predecessor_literal,
                changed_release.migration_sha256.encode(),
            )
            changed_retirement_path.write_bytes(changed_retirement_bytes)
            changed_retirement_release = replace(
                changed_module.MONDAY_FORECAST_V2_RETIREMENT_RELEASE,
                migration_sha256=hashlib.sha256(changed_retirement_bytes).hexdigest(),
            )
            changed_module.MONDAY_FORECAST_V2_RETIREMENT_RELEASE = (
                changed_retirement_release
            )
            changed_module.POST_MAPPING_APPLICATION_RELEASE_MANIFEST = (
                changed_retirement_release,
            )
            retirement_module_name = (
                f"procurement_os.mapping_retirement_changed_{uuid4().hex}"
            )
            retirement_spec = importlib.util.spec_from_file_location(
                retirement_module_name,
                Path(changed_module.__file__).resolve().parents[1]
                / "src"
                / "procurement_os"
                / "monday_forecast_retirement.py",
            )
            assert retirement_spec is not None and retirement_spec.loader is not None
            changed_retirement_verifier = importlib.util.module_from_spec(
                retirement_spec
            )
            sys.modules[retirement_module_name] = changed_retirement_verifier
            self.addCleanup(sys.modules.pop, retirement_module_name, None)
            retirement_spec.loader.exec_module(changed_retirement_verifier)
            changed_retirement_verifier.MIGRATION_SHA256 = (
                changed_retirement_release.migration_sha256
            )
            changed_module.verify_monday_forecast_v2_retirement_contract = (
                changed_retirement_verifier.verify_monday_forecast_v2_retirement_contract
            )

            originals = {}
            for label, module in modules.items():
                original = module._verify_or_apply_mapping_release
                originals[label] = original

                def synchronized(
                    conn,
                    db_dir,
                    release=None,
                    *,
                    _module=module,
                    _original=original,
                ):
                    selected_release = release or _module.MAPPING_RELEASE
                    if selected_release.version == _module.MAPPING_RELEASE.version:
                        barrier.wait(timeout=10)
                    return _original(conn, db_dir, selected_release)

                module._verify_or_apply_mapping_release = synchronized

            def invoke(label: str):
                module = modules[label]
                try:
                    with psycopg.connect(self.mapping_url) as raw_connection:
                        backend_pids[label] = raw_connection.info.backend_pid
                        connection = RecordingConnection(
                            raw_connection,
                            label=label,
                            migration_bytes=migration_bytes[label],
                        )
                        return (
                            label,
                            module.apply_schema_connection(
                                connection,
                                roots[label],
                                include_persistent_mapping=True,
                            ),
                            None,
                        )
                except Exception as exc:  # exact loser is asserted below
                    return label, None, exc

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = [
                    future.result()
                    for future in (
                        pool.submit(invoke, "canonical"),
                        pool.submit(invoke, "changed"),
                    )
                ]
            for label, module in modules.items():
                module._verify_or_apply_mapping_release = originals[label]

            winners = [item for item in outcomes if item[2] is None]
            losers = [item for item in outcomes if item[2] is not None]
            self.assertEqual(len(winners), 1, outcomes)
            self.assertEqual(len(losers), 1, outcomes)
            winner_label, applied, _ = winners[0]
            self.assertEqual(
                applied,
                [
                    apply_schema.MAPPING_MIGRATION_NAME,
                    apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                ],
            )
            self.assertIsInstance(losers[0][2], RuntimeError)
            self.assertRegex(str(losers[0][2]), "checksum differs")
            self.assertEqual(body_submissions, [winner_label])
            self.assertTrue(real_contention_observed.is_set())

            winner_release = modules[winner_label].MAPPING_RELEASE
            marker_key = f"migration:{apply_schema.MAPPING_MIGRATION_NAME}"
            with psycopg.connect(self.mapping_url) as conn:
                marker_before = conn.execute(
                    f"SELECT value FROM {SCHEMA}.meta WHERE key=%s", (marker_key,)
                ).fetchone()[0]
                self.assertEqual(
                    marker_before, f"sha256:{winner_release.migration_sha256}"
                )
                authority_before = self._counts()
            self.assertEqual(authority_before, (0,) * len(authority_before))

            with psycopg.connect(self.mapping_url) as raw_connection:
                replay_connection = RecordingConnection(
                    raw_connection,
                    label=winner_label,
                    migration_bytes=migration_bytes[winner_label],
                )
                self.assertEqual(
                    modules[winner_label].apply_schema_connection(
                        replay_connection,
                        roots[winner_label],
                        include_persistent_mapping=True,
                    ),
                    [],
                )
            self.assertEqual(body_submissions, [winner_label])
            with psycopg.connect(self.mapping_url) as conn:
                self.assertEqual(
                    conn.execute(
                        f"SELECT value FROM {SCHEMA}.meta WHERE key=%s", (marker_key,)
                    ).fetchone()[0],
                    marker_before,
                )
            self.assertEqual(self._counts(), authority_before)

    def test_explicit_schema_binding_defeats_hostile_search_path_temp_and_helper_decoys(self):
        def prepare_empty_target() -> None:
            self._admin_cleanup()
            self._prepare_roles_and_schema()

        def assert_pre_effect_refusal(
            label: str,
            setup,
            *,
            manifest: tuple[apply_schema.MappingRelease, ...] | None = None,
        ) -> None:
            with self.subTest(case=label):
                prepare_empty_target()
                setup()
                patcher = (
                    mock.patch.object(
                        apply_schema,
                        "PERSISTENT_MAPPING_RELEASE_MANIFEST",
                        manifest,
                    )
                    if manifest is not None
                    else nullcontext()
                )
                with patcher, psycopg.connect(self.mapping_url) as conn:
                    conn.execute(
                        "SET search_path=hostile_mapping_path,pg_temp,public"
                    )
                    conn.commit()
                    before = self._schema_snapshot(conn)
                    conn.rollback()
                    with self.assertRaises((RuntimeError, psycopg.Error)):
                        apply_schema.apply_schema_connection(
                            conn, DB_DIR, include_persistent_mapping=True
                        )
                    self.assertEqual(self._schema_snapshot(conn), before)
                with self._admin_connection() as admin:
                    self.assertIsNone(apply_schema._migration_markers(admin))

        non_16_release = replace(
            apply_schema.MAPPING_RELEASE,
            postgres_major=99,
        )
        assert_pre_effect_refusal(
            "non-16 server fixture",
            lambda: None,
            manifest=(non_16_release,),
        )

        def make_target_unusable() -> None:
            with self._admin_connection() as conn:
                conn.execute(
                    f"ALTER SCHEMA {SCHEMA} OWNER TO qa_release_login"
                )

        assert_pre_effect_refusal(
            "target owned by the wrong effective role",
            make_target_unusable,
        )

        def install_target_decoy() -> None:
            with psycopg.connect(self.mapping_url) as conn:
                conn.execute(
                    f"CREATE FUNCTION {SCHEMA}."
                    "assert_persistent_mapping_foundation_contract() "
                    "RETURNS void LANGUAGE sql AS $$SELECT$$"
                )

        assert_pre_effect_refusal(
            "unmarked target contains a protected-signature decoy",
            install_target_decoy,
        )

        def grant_public_create() -> None:
            with self._admin_connection() as conn:
                conn.execute(f"GRANT CREATE ON SCHEMA {SCHEMA} TO PUBLIC")

        assert_pre_effect_refusal(
            "PUBLIC can create in target",
            grant_public_create,
        )

        def grant_untrusted_login_create() -> None:
            with self._admin_connection() as conn:
                conn.execute(
                    "CREATE ROLE qa_transitive_probe LOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    f"GRANT USAGE,CREATE ON SCHEMA {SCHEMA} "
                    "TO qa_transitive_probe"
                )

        assert_pre_effect_refusal(
            "unapproved login can create in target",
            grant_untrusted_login_create,
        )

        def grant_untrusted_login_set_owner() -> None:
            with self._admin_connection() as conn:
                conn.execute(
                    "CREATE ROLE qa_transitive_probe LOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    "GRANT qa_mapping_owner TO qa_transitive_probe "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                )

        assert_pre_effect_refusal(
            "unapproved login can SET directly to target owner",
            grant_untrusted_login_set_owner,
        )

        def grant_untrusted_login_transitive_set_owner() -> None:
            with self._admin_connection() as conn:
                conn.execute(
                    "CREATE ROLE qa_transitive_probe LOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    "CREATE ROLE qa_transitive_mid NOLOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    "GRANT qa_mapping_owner TO qa_transitive_mid "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                )
                conn.execute(
                    "GRANT qa_transitive_mid TO qa_transitive_probe "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                )

        assert_pre_effect_refusal(
            "unapproved login has a transitive SET path to target owner",
            grant_untrusted_login_transitive_set_owner,
        )

        def grant_untrusted_login_transitive_inherit_create() -> None:
            with self._admin_connection() as conn:
                conn.execute(
                    "CREATE ROLE qa_transitive_probe LOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    "CREATE ROLE qa_transitive_mid NOLOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    "CREATE ROLE qa_transitive_leaf NOLOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    f"GRANT CREATE ON SCHEMA {SCHEMA} TO qa_transitive_leaf"
                )
                conn.execute(
                    "GRANT qa_transitive_leaf TO qa_transitive_mid "
                    "WITH INHERIT TRUE, SET FALSE, ADMIN FALSE"
                )
                conn.execute(
                    "GRANT qa_transitive_mid TO qa_transitive_probe "
                    "WITH INHERIT TRUE, SET FALSE, ADMIN FALSE"
                )

        assert_pre_effect_refusal(
            "unapproved login has a transitive INHERIT path to CREATE",
            grant_untrusted_login_transitive_inherit_create,
        )

        def grant_untrusted_login_set_then_inherit_create() -> None:
            with self._admin_connection() as conn:
                conn.execute(
                    "CREATE ROLE qa_transitive_probe LOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    "CREATE ROLE qa_transitive_mid NOLOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    "CREATE ROLE qa_transitive_leaf NOLOGIN NOINHERIT "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                conn.execute(
                    f"GRANT CREATE ON SCHEMA {SCHEMA} TO qa_transitive_leaf"
                )
                conn.execute(
                    "GRANT qa_transitive_leaf TO qa_transitive_mid "
                    "WITH INHERIT TRUE, SET FALSE, ADMIN FALSE"
                )
                conn.execute(
                    "GRANT qa_transitive_mid TO qa_transitive_probe "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                )

        assert_pre_effect_refusal(
            "unapproved login has a SET-then-INHERIT path to CREATE",
            grant_untrusted_login_set_then_inherit_create,
        )

        def install_digest_substitute() -> None:
            with self._admin_connection() as conn:
                conn.execute(
                    "CREATE SCHEMA hostile_mapping_path AUTHORIZATION "
                    "qa_mapping_owner"
                )
                conn.execute(
                    "CREATE FUNCTION hostile_mapping_path.digest(bytea,text) "
                    "RETURNS bytea LANGUAGE sql IMMUTABLE STRICT "
                    "AS $$SELECT $1$$"
                )

        assert_pre_effect_refusal(
            "same-signature digest substitute in an earlier schema",
            install_digest_substitute,
        )

        prepare_empty_target()
        with self._admin_connection() as admin:
            admin.execute(
                "CREATE SCHEMA hostile_mapping_path AUTHORIZATION "
                "qa_mapping_owner"
            )
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(
                "CREATE TABLE hostile_mapping_path.meta(key text,value text)"
            )
            conn.execute(
                "INSERT INTO hostile_mapping_path.meta VALUES "
                "('migration:014_persistent_mapping_foundation.sql',"
                "'sha256:decoy')"
            )
            conn.execute(
                "CREATE FUNCTION hostile_mapping_path."
                "assert_persistent_mapping_foundation_contract() "
                "RETURNS void LANGUAGE sql AS $$SELECT$$"
            )
            conn.execute(
                "CREATE FUNCTION hostile_mapping_path."
                "compute_persistent_mapping_catalog_sha256() "
                "RETURNS text LANGUAGE sql IMMUTABLE "
                "AS $$SELECT repeat('0',64)$$"
            )
            temporary_names = (
                "supplier_mapping_review_batches",
                "supplier_mapping_review_candidates",
                "supplier_mapping_decisions",
                "supplier_offer_selection_events",
                "supplier_offer_selection_heads",
            )
            for relation_name in temporary_names:
                conn.execute(
                    sql.SQL("CREATE TEMP TABLE {}(decoy text)").format(
                        sql.Identifier(relation_name)
                    )
                )
                conn.execute(
                    sql.SQL("INSERT INTO {} VALUES ('unchanged')").format(
                        sql.Identifier(relation_name)
                    )
                )
            conn.execute(
                "SET search_path=hostile_mapping_path,pg_temp,public"
            )
            conn.commit()
            self.assertNotIn(
                SCHEMA,
                tuple(
                    row[0]
                    for row in conn.execute(
                        "SELECT * FROM pg_catalog.unnest("
                        "pg_catalog.current_schemas(true))"
                    ).fetchall()
                ),
            )
            conn.commit()

            resolution_evidence: list[
                tuple[str, tuple[tuple[str, int], ...], tuple[int, ...], bool]
            ] = []
            catalog_path_evidence: list[tuple[str, str]] = []
            original_resolution = apply_schema._verify_controlled_digest_resolution
            original_catalog = apply_schema._verify_installed_function_catalog

            def observe_controlled_resolution(observed_conn):
                current_schema = str(
                    observed_conn.execute(
                        "SELECT pg_catalog.current_schema()"
                    ).fetchone()[0]
                )
                members = tuple(
                    (str(row[0]), int(row[1]))
                    for row in observed_conn.execute(
                        "SELECT pg_catalog.pg_get_function_identity_arguments(p.oid),"
                        "p.oid FROM pg_catalog.pg_extension e "
                        "JOIN pg_catalog.pg_depend d ON "
                        "d.refclassid='pg_catalog.pg_extension'::pg_catalog.regclass "
                        "AND d.refobjid=e.oid "
                        "AND d.classid='pg_catalog.pg_proc'::pg_catalog.regclass "
                        "AND d.deptype='e' "
                        "JOIN pg_catalog.pg_proc p ON p.oid=d.objid "
                        "WHERE e.extname='pgcrypto' AND p.proname='digest' "
                        "AND pg_catalog.pg_get_function_identity_arguments(p.oid) "
                        "IN ('bytea, text','text, text') ORDER BY 1"
                    ).fetchall()
                )
                resolved = tuple(
                    int(row[0])
                    for row in observed_conn.execute(
                        "SELECT pg_catalog.to_regprocedure(name)::oid "
                        "FROM pg_catalog.unnest(ARRAY["
                        "'digest(bytea,text)','digest(text,text)']) AS name "
                        "ORDER BY name"
                    ).fetchall()
                )
                marker_exists = bool(
                    observed_conn.execute(
                        sql.SQL(
                            "SELECT EXISTS(SELECT 1 FROM {}.meta WHERE key=%s)"
                        ).format(sql.Identifier(SCHEMA)),
                        ("migration:schema_postgres.sql",),
                    ).fetchone()[0]
                )
                original_resolution(observed_conn)
                resolution_evidence.append(
                    (current_schema, members, resolved, marker_exists)
                )

            def observe_catalog_path(
                observed_conn,
                *,
                schema_oid,
                release=apply_schema.MAPPING_RELEASE,
            ):
                before_path = str(
                    observed_conn.execute(
                        "SELECT pg_catalog.current_setting('search_path')"
                    ).fetchone()[0]
                )
                original_catalog(
                    observed_conn,
                    schema_oid=schema_oid,
                    release=release,
                )
                after_path = str(
                    observed_conn.execute(
                        "SELECT pg_catalog.current_setting('search_path')"
                    ).fetchone()[0]
                )
                catalog_path_evidence.append((before_path, after_path))

            with (
                mock.patch.object(
                    apply_schema,
                    "_verify_controlled_digest_resolution",
                    side_effect=observe_controlled_resolution,
                ),
                mock.patch.object(
                    apply_schema,
                    "_verify_installed_function_catalog",
                    side_effect=observe_catalog_path,
                ),
            ):
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
            self.assertTrue(resolution_evidence)
            self.assertTrue(catalog_path_evidence)
            self.assertTrue(
                all(before == after for before, after in catalog_path_evidence),
                catalog_path_evidence,
            )
            self.assertFalse(resolution_evidence[0][3])
            for current_schema, members, resolved, _marker in resolution_evidence:
                self.assertEqual(current_schema, SCHEMA)
                self.assertEqual(
                    tuple(item[0] for item in members),
                    ("bytea, text", "text, text"),
                )
                self.assertEqual(
                    tuple(item[1] for item in members),
                    resolved,
                )
            self.assertEqual(
                conn.execute(
                    "SELECT key,value FROM hostile_mapping_path.meta"
                ).fetchall(),
                [
                    (
                        "migration:014_persistent_mapping_foundation.sql",
                        "sha256:decoy",
                    )
                ],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM pg_catalog.pg_class "
                    "WHERE relnamespace=pg_catalog.pg_my_temp_schema() "
                    "AND relname=ANY(%s)",
                    (list(temporary_names),),
                ).fetchone()[0],
                len(temporary_names),
            )
            for relation_name in temporary_names:
                self.assertEqual(
                    conn.execute(
                        sql.SQL("SELECT decoy FROM pg_temp.{}").format(
                            sql.Identifier(relation_name)
                        )
                    ).fetchall(),
                    [("unchanged",)],
                )
            self.assertNotIn(
                SCHEMA,
                tuple(
                    row[0]
                    for row in conn.execute(
                        "SELECT * FROM pg_catalog.unnest("
                        "pg_catalog.current_schemas(true))"
                    ).fetchall()
                ),
            )
            with conn.transaction():
                self.assertFalse(
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
                )

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

    def test_migration_failure_and_late_validation_roll_back_every_object(self):
        self._reset(include_mapping=False)
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(
                f"INSERT INTO {SCHEMA}.vendors(vendor_id,vendor_name) "
                "VALUES(%s,'late-validation-preserved')",
                (VENDOR_ID,),
            )
            conn.execute(
                f"UPDATE {SCHEMA}.meta SET value='precondition-refusal' "
                "WHERE key='monday_p1_remediation_contract'"
            )
            conn.commit()
            precondition_before = self._schema_snapshot(conn)
            with self.assertRaisesRegex(
                psycopg.Error,
                "required Monday predecessor contracts differ",
            ):
                with conn.transaction():
                    conn.execute(
                        (
                            DB_DIR / apply_schema.MAPPING_MIGRATION_NAME
                        ).read_text()
                    )
            self.assertEqual(
                self._schema_snapshot(conn), precondition_before
            )
            self.assertIsNone(
                conn.execute(
                    f"SELECT pg_catalog.to_regclass("
                    f"'{SCHEMA}.supplier_mapping_review_batches')"
                ).fetchone()[0]
            )
            conn.execute(
                f"UPDATE {SCHEMA}.meta SET value='v1' "
                "WHERE key='monday_p1_remediation_contract'"
            )
            conn.commit()
            before = self._schema_snapshot(conn)

            def refuse_after_ddl(*_args, **_kwargs):
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT pg_catalog.to_regclass(%s)",
                        (f"{SCHEMA}.supplier_mapping_review_batches",),
                    ).fetchone()[0]
                )
                self.assertIsNone(
                    conn.execute(
                        f"SELECT value FROM {SCHEMA}.meta WHERE key=%s",
                        (f"migration:{apply_schema.MAPPING_MIGRATION_NAME}",),
                    ).fetchone()
                )
                raise RuntimeError("injected post-DDL validation failure")

            with (
                mock.patch.object(
                    apply_schema,
                    "_verify_installed_function_catalog",
                    side_effect=refuse_after_ddl,
                ),
                self.assertRaisesRegex(RuntimeError, "post-DDL"),
            ):
                with conn.transaction():
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            self.assertEqual(self._schema_snapshot(conn), before)
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
            with self.assertRaises(PersistentMappingError) as refused_offer:
                self._decide(
                    create_candidate,
                    action="APPROVE_MAPPING",
                    reason="late offer refusal",
                    link_kind="CREATED_INACTIVE",
                )
            self.assertEqual(
                refused_offer.exception.code, "DATABASE_VALIDATION_REFUSED"
            )
            self.assertEqual(self._counts(), before)
            with psycopg.connect(self.mapping_url) as conn:
                self.assertEqual(
                    conn.execute(
                        f"SELECT count(*) FROM {SCHEMA}.supplier_offers"
                    ).fetchone()[0],
                    offer_count,
                )

            reject_candidate = self._intake(
                self._packet(
                    1,
                    occurrence="late-rejection",
                    package_id="late-rejection-package",
                    candidate_changes={
                        "supplier_code_value": "LATE-REJECT",
                        "distributor_product_id_value": "LATE-REJECT",
                    },
                )
            )
            before_rejection = self._counts()
            with psycopg.connect(self.mapping_url) as conn:
                rejection_rows_before = tuple(
                    conn.execute(
                        f"SELECT * FROM {SCHEMA}.mapping_rejections ORDER BY rejection_id"
                    ).fetchall()
                )
            with self.assertRaises(PersistentMappingError) as refused_rejection:
                self._decide(
                    reject_candidate,
                    action="REJECT_MAPPING",
                    reason="late rejection refusal",
                )
            self.assertEqual(
                refused_rejection.exception.code, "DATABASE_VALIDATION_REFUSED"
            )
            self.assertEqual(self._counts(), before_rejection)
            with psycopg.connect(self.mapping_url) as conn:
                self.assertEqual(
                    tuple(
                        conn.execute(
                            f"SELECT * FROM {SCHEMA}.mapping_rejections "
                            "ORDER BY rejection_id"
                        ).fetchall()
                    ),
                    rejection_rows_before,
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
            head_state_before = tuple(
                conn.execute(
                    f"SELECT * FROM {SCHEMA}.supplier_offer_selection_heads "
                    "ORDER BY variant_id"
                ).fetchall()
            )
            event_state_before = tuple(
                conn.execute(
                    f"SELECT * FROM {SCHEMA}.supplier_offer_selection_events "
                    "ORDER BY selection_event_id"
                ).fetchall()
            )
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
            with self.assertRaises(PersistentMappingError) as refused_head:
                self._select(decision, reason="late head refusal")
            self.assertEqual(
                refused_head.exception.code, "DATABASE_VALIDATION_REFUSED"
            )
            self.assertEqual(self._counts(), before_head)
            with psycopg.connect(self.mapping_url) as conn:
                self.assertEqual(
                    tuple(
                        conn.execute(
                            f"SELECT * FROM {SCHEMA}.supplier_offer_selection_heads "
                            "ORDER BY variant_id"
                        ).fetchall()
                    ),
                    head_state_before,
                )
                self.assertEqual(
                    tuple(
                        conn.execute(
                            f"SELECT * FROM {SCHEMA}.supplier_offer_selection_events "
                            "ORDER BY selection_event_id"
                        ).fetchall()
                    ),
                    event_state_before,
                )
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
        from procurement_os import draft_po, emergency_packet, price_book
        from procurement_os.shopify.graphql import ShopifyGraphQLClient
        from procurement_os.storage import LocalFilesystemStorage

        def non_intake_snapshot(conn: Any) -> dict[str, object]:
            snapshot = self._schema_snapshot(conn)
            snapshot["table_rows"] = tuple(
                row
                for row in snapshot["table_rows"]
                if row[0]
                not in {
                    "supplier_mapping_review_batches",
                    "supplier_mapping_review_candidates",
                }
            )
            return snapshot

        packet = self._packet(1)
        with psycopg.connect(self.mapping_url) as conn:
            before = non_intake_snapshot(conn)
            conn.rollback()
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self.assertEqual(
                conn.execute("SHOW transaction_isolation").fetchone()[0],
                "read committed",
            )
            with self.assertRaisesRegex(
                psycopg.Error, "mapping review intake requires SERIALIZABLE isolation"
            ):
                mapping_service.intake_supplier_mapping_review(
                    conn,
                    package=packet["package"],
                    candidates=packet["candidates"],
                    principal=self.principal,
                    intake_idempotency_key=packet["intake_idempotency_key"],
                )
            conn.rollback()
            self.assertEqual(non_intake_snapshot(conn), before)
        self.assertEqual(self._counts(), (0, 0, 0, 0, 0, 0, 0))

        observed_isolation: list[str] = []
        original_intake = mapping_service.intake_supplier_mapping_review

        def observe_serializable(conn, **kwargs):
            observed_isolation.append(
                conn.execute("SHOW transaction_isolation").fetchone()[0]
            )
            return original_intake(conn, **kwargs)

        with ExitStack() as stack:
            external_mutators = (
                stack.enter_context(
                    mock.patch.object(ShopifyGraphQLClient, "query")
                ),
                stack.enter_context(
                    mock.patch.object(LocalFilesystemStorage, "put_bytes")
                ),
                stack.enter_context(
                    mock.patch.object(draft_po, "build_vendor_drafts")
                ),
                stack.enter_context(
                    mock.patch.object(
                        emergency_packet, "build_emergency_review_packet"
                    )
                ),
                stack.enter_context(
                    mock.patch.object(price_book, "promote_price_book_batch")
                ),
                stack.enter_context(
                    mock.patch.object(recommendations, "prepare_monday_run")
                ),
            )
            stack.enter_context(
                mock.patch.object(
                    mapping_service,
                    "intake_supplier_mapping_review",
                    side_effect=observe_serializable,
                )
            )
            result = execute_supplier_mapping_intake(
                self.mapping_url,
                package=packet["package"],
                candidates=packet["candidates"],
                principal=self.principal,
                intake_idempotency_key=packet["intake_idempotency_key"],
            )
            for mutator in external_mutators:
                mutator.assert_not_called()
        self.assertEqual(observed_isolation, ["serializable"])
        self.assertFalse(result["replayed"])
        self.assertEqual(len(result["candidate_ids"]), 1)
        self.assertEqual(self._counts(), (1, 1, 0, 0, 0, 0, 0))
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(non_intake_snapshot(conn), before)
            self.assertEqual(
                conn.execute(
                    f"SELECT (SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_events),"
                    f"(SELECT count(*) FROM {SCHEMA}.prices)"
                ).fetchone(),
                (0, 0, 0, 0),
            )

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
        def two_candidate_packet(*, occurrence: str, package_id: str) -> dict:
            first = self._packet(
                1, occurrence=occurrence, package_id=package_id
            )
            second = copy.deepcopy(first["candidates"][0])
            second["occurrence_key"] = f"{occurrence}-second"
            second["source_row_key"] = f"{occurrence}-second"
            return self._packet(
                1,
                occurrence=occurrence,
                package_id=package_id,
                extra_candidates=[second],
            )

        def race(requests: tuple[dict, dict]) -> tuple[list[object], set[int]]:
            barrier = Barrier(2)
            contention_observed = Event()
            failed_lock_pids: set[int] = set()
            original_try_lock = mapping_service._try_session_lock
            original_intake = mapping_service.intake_supplier_mapping_review
            expected_key = requests[0]["intake_idempotency_key"]
            expected_lock = (
                f"persistent-mapping:review-intake:idempotency:{expected_key}"
            )

            def observed_try_lock(conn, lock_name: str, deadline: float):
                self.assertEqual(lock_name, expected_lock)
                locked = conn.execute(
                    "SELECT pg_catalog.pg_try_advisory_lock("
                    "pg_catalog.hashtextextended(%s,0))",
                    (lock_name,),
                ).fetchone()[0]
                if locked:
                    return
                failed_lock_pids.add(conn.info.backend_pid)
                contention_observed.set()
                return original_try_lock(conn, lock_name, deadline)

            def hold_winner_until_real_contention(conn, **kwargs):
                if not contention_observed.wait(timeout=10):
                    raise AssertionError(
                        "the competing intake never failed a real PostgreSQL lock attempt"
                    )
                return original_intake(conn, **kwargs)

            def invoke(packet: dict) -> object:
                barrier.wait(timeout=10)
                try:
                    return execute_supplier_mapping_intake(
                        self.mapping_url,
                        package=packet["package"],
                        candidates=packet["candidates"],
                        principal=self.principal,
                        intake_idempotency_key=packet["intake_idempotency_key"],
                    )
                except PersistentMappingError as exc:
                    return exc

            with (
                mock.patch.object(
                    mapping_service,
                    "_try_session_lock",
                    side_effect=observed_try_lock,
                ),
                mock.patch.object(
                    mapping_service,
                    "intake_supplier_mapping_review",
                    side_effect=hold_winner_until_real_contention,
                ),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                futures = [pool.submit(invoke, packet) for packet in requests]
                outcomes = [future.result() for future in futures]
            self.assertTrue(contention_observed.is_set())
            self.assertEqual(len(failed_lock_pids), 1)
            return outcomes, failed_lock_pids

        packet = two_candidate_packet(
            occurrence="same-payload", package_id="same-payload-package"
        )
        results, _failed_pids = race((packet, copy.deepcopy(packet)))
        self.assertTrue(all(isinstance(item, dict) for item in results))
        self.assertEqual(sum(not item["replayed"] for item in results), 1)
        self.assertEqual(
            {item["review_batch_id"] for item in results},
            {results[0]["review_batch_id"]},
        )
        self.assertEqual(results[0]["candidate_ids"], results[1]["candidate_ids"])
        self.assertEqual(len(results[0]["candidate_ids"]), 2)
        self.assertEqual(self._counts()[:2], (1, 2))
        with psycopg.connect(self.mapping_url) as conn:
            batch = conn.execute(
                f"SELECT review_batch_id,candidate_count,candidate_set_sha256,"
                "source_package_id,source_revision,source_artifact_sha256,"
                "source_root_sha256,source_seal_sha256,relationship_table_sha256,"
                "source_batch_sha256,source_payload_sha256 "
                f"FROM {SCHEMA}.supplier_mapping_review_batches "
                "WHERE intake_idempotency_key=%s",
                (packet["intake_idempotency_key"],),
            ).fetchone()
            for result in results:
                self.assertEqual(str(batch[0]), result["review_batch_id"])
            self.assertEqual(batch[1], 2)
            self.assertRegex(batch[2], r"^[0-9a-f]{64}$")
            self.assertEqual(
                batch[3:],
                (
                    packet["package"]["source_package_id"],
                    packet["package"]["source_revision"],
                    packet["package"]["source_artifact_sha256"],
                    packet["package"]["source_root_sha256"],
                    packet["package"]["source_seal_sha256"],
                    packet["package"]["relationship_table_sha256"],
                    packet["package"]["source_batch_sha256"],
                    packet["package"]["source_payload_sha256"],
                ),
            )
            candidate_rows = conn.execute(
                f"SELECT candidate_id,occurrence_index,occurrence_key,"
                "source_row_key,source_file_name,source_file_sha256,"
                "candidate_sha256 "
                f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                "WHERE review_batch_id=%s ORDER BY occurrence_index",
                (batch[0],),
            ).fetchall()
            self.assertEqual(
                [str(row[0]) for row in candidate_rows],
                results[0]["candidate_ids"],
            )
            self.assertEqual(
                [
                    (
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                    )
                    for row in candidate_rows
                ],
                [
                    (
                        index,
                        candidate["occurrence_key"],
                        candidate["source_row_key"],
                        candidate["source_file_name"],
                        candidate["source_file_sha256"],
                    )
                    for index, candidate in enumerate(packet["candidates"], 1)
                ],
            )
            self.assertEqual(
                hashlib.sha256(
                    "".join(f"{row[6]}\n" for row in candidate_rows).encode()
                ).hexdigest(),
                batch[2],
            )
            committed = next(item for item in results if not item["replayed"])
            self.assertEqual(committed["candidate_set_sha256"], batch[2])

        canonical = two_candidate_packet(
            occurrence="conflict-canonical",
            package_id="conflict-canonical-package",
        )
        changed = two_candidate_packet(
            occurrence="conflict-changed",
            package_id="conflict-changed-package",
        )
        changed["intake_idempotency_key"] = canonical["intake_idempotency_key"]
        counts_before = self._counts()
        conflicting, _failed_pids = race((canonical, changed))
        winners = [item for item in conflicting if isinstance(item, dict)]
        losers = [item for item in conflicting if isinstance(item, PersistentMappingError)]
        self.assertEqual(len(winners), 1, conflicting)
        self.assertEqual(len(losers), 1, conflicting)
        self.assertFalse(winners[0]["replayed"])
        self.assertEqual(losers[0].code, "IDEMPOTENCY_CONFLICT")
        self.assertIn("different payload", str(losers[0]))
        expected_counts = list(counts_before)
        expected_counts[0] += 1
        expected_counts[1] += 2
        self.assertEqual(self._counts(), tuple(expected_counts))
        with psycopg.connect(self.mapping_url) as conn:
            winner_batch = conn.execute(
                f"SELECT review_batch_id,candidate_count,candidate_set_sha256,"
                "source_package_id,source_revision,source_artifact_sha256,"
                "source_root_sha256,source_seal_sha256,relationship_table_sha256,"
                "source_batch_sha256,source_payload_sha256 "
                f"FROM {SCHEMA}.supplier_mapping_review_batches "
                "WHERE intake_idempotency_key=%s",
                (canonical["intake_idempotency_key"],),
            ).fetchone()
            self.assertEqual(winner_batch[1], 2)
            winner_packet = next(
                packet
                for packet in (canonical, changed)
                if packet["package"]["source_package_id"] == winner_batch[3]
            )
            self.assertEqual(str(winner_batch[0]), winners[0]["review_batch_id"])
            self.assertEqual(
                winner_batch[3:],
                (
                    winner_packet["package"]["source_package_id"],
                    winner_packet["package"]["source_revision"],
                    winner_packet["package"]["source_artifact_sha256"],
                    winner_packet["package"]["source_root_sha256"],
                    winner_packet["package"]["source_seal_sha256"],
                    winner_packet["package"]["relationship_table_sha256"],
                    winner_packet["package"]["source_batch_sha256"],
                    winner_packet["package"]["source_payload_sha256"],
                ),
            )
            winner_candidates = conn.execute(
                f"SELECT candidate_id,occurrence_index,occurrence_key,"
                "source_row_key,source_file_name,source_file_sha256,"
                "candidate_sha256 "
                f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                "WHERE review_batch_id=%s ORDER BY occurrence_index",
                (winner_batch[0],),
            ).fetchall()
            self.assertEqual(
                [str(row[0]) for row in winner_candidates],
                winners[0]["candidate_ids"],
            )
            self.assertEqual(
                [
                    (row[1], row[2], row[3], row[4], row[5])
                    for row in winner_candidates
                ],
                [
                    (
                        index,
                        candidate["occurrence_key"],
                        candidate["source_row_key"],
                        candidate["source_file_name"],
                        candidate["source_file_sha256"],
                    )
                    for index, candidate in enumerate(
                        winner_packet["candidates"], 1
                    )
                ],
            )
            self.assertEqual(
                hashlib.sha256(
                    "".join(
                        f"{row[6]}\n" for row in winner_candidates
                    ).encode()
                ).hexdigest(),
                winner_batch[2],
            )
            self.assertEqual(
                winners[0]["candidate_set_sha256"], winner_batch[2]
            )

    def test_intake_rejects_missing_or_altered_source_hash_page_and_prerequisite(self):
        def mutate_package_hash(name: str):
            return lambda packet: packet["package"].__setitem__(name, "0" * 64)

        def reseal(packet: dict) -> None:
            candidates = packet["candidates"]
            package = packet["package"]
            relationships = [
                relationship
                for candidate in candidates
                for relationship in candidate.get("component_relationships", [])
            ]
            package["source_payload_sha256"] = _canonical_source_sha256(
                candidates
            )
            package["source_root_sha256"] = _canonical_source_sha256(
                {
                    "source_artifact_sha256": package[
                        "source_artifact_sha256"
                    ],
                    "source_payload_sha256": package["source_payload_sha256"],
                }
            )
            package["relationship_table_sha256"] = _canonical_source_sha256(
                relationships
            )
            package["source_batch_sha256"] = _canonical_source_sha256(
                {
                    "source_package_id": package["source_package_id"],
                    "source_revision": package["source_revision"],
                    "occurrence_keys": [
                        item["occurrence_key"] for item in candidates
                    ],
                    "source_authority_state": package.get(
                        "source_authority_state"
                    ),
                    "source_import_state": package.get("source_import_state"),
                }
            )
            package["source_seal_sha256"] = _canonical_source_sha256(
                {
                    "source_root_sha256": package["source_root_sha256"],
                    "relationship_table_sha256": package[
                        "relationship_table_sha256"
                    ],
                    "source_batch_sha256": package["source_batch_sha256"],
                }
            )

        def remove_and_reseal(field: str):
            def mutate(packet: dict) -> None:
                packet["candidates"][0].pop(field, None)
                reseal(packet)

            return mutate

        cases = (
            (
                "missing sealed source table",
                remove_and_reseal("source_table_name"),
                "review candidate source binding differs",
            ),
            (
                "missing sealed source row",
                remove_and_reseal("source_row_key"),
                "review candidate source binding differs",
            ),
            (
                "missing sealed source file",
                remove_and_reseal("source_file_name"),
                "review candidate source binding differs",
            ),
            (
                "missing sealed source locator",
                remove_and_reseal("source_locator"),
                "review candidate source binding differs",
            ),
            (
                "changed valid sealed source table",
                lambda packet: packet["candidates"][0].__setitem__(
                    "source_table_name", "supplier_offers_v5_changed"
                ),
                "review package seal or member fingerprint differs",
            ),
            (
                "changed valid sealed source row",
                lambda packet: packet["candidates"][0].__setitem__(
                    "source_row_key", "changed-valid-source-row"
                ),
                "review package seal or member fingerprint differs",
            ),
            (
                "changed valid sealed source file",
                lambda packet: packet["candidates"][0].__setitem__(
                    "source_file_name", "changed-valid-source.jsonl"
                ),
                "review package seal or member fingerprint differs",
            ),
            (
                "changed valid sealed source locator",
                lambda packet: packet["candidates"][0].__setitem__(
                    "source_locator", {"row": 999, "section": "changed"}
                ),
                "review package seal or member fingerprint differs",
            ),
            (
                "changed valid sealed page",
                lambda packet: packet["candidates"][0].__setitem__(
                    "source_page_end",
                    packet["candidates"][0]["source_page_end"] + 1,
                ),
                "review package seal or member fingerprint differs",
            ),
            (
                "artifact hash",
                mutate_package_hash("source_artifact_sha256"),
                "review candidate source binding differs",
            ),
            (
                "candidate file hash",
                lambda packet: packet["candidates"][0].__setitem__(
                    "source_file_sha256", "0" * 64
                ),
                "review candidate source binding differs",
            ),
            (
                "payload hash",
                mutate_package_hash("source_payload_sha256"),
                "review package seal or member fingerprint differs",
            ),
            (
                "root hash",
                mutate_package_hash("source_root_sha256"),
                "review package seal or member fingerprint differs",
            ),
            (
                "seal hash",
                mutate_package_hash("source_seal_sha256"),
                "review package seal or member fingerprint differs",
            ),
            (
                "relationship table hash",
                mutate_package_hash("relationship_table_sha256"),
                "review package seal or member fingerprint differs",
            ),
            (
                "batch hash",
                mutate_package_hash("source_batch_sha256"),
                "review package seal or member fingerprint differs",
            ),
            (
                "page lower bound",
                lambda packet: packet["candidates"][0].__setitem__(
                    "source_page_start", 0
                ),
                "review candidate source binding differs",
            ),
            (
                "page reversed bounds",
                lambda packet: packet["candidates"][0].update(
                    source_page_start=2, source_page_end=1
                ),
                "review candidate source binding differs",
            ),
            (
                "prerequisite",
                lambda packet: packet["package"].__setitem__("prerequisites", {}),
                "review package prerequisite proof differs",
            ),
        )
        for name, mutate, message in cases:
            packet = self._packet(1)
            mutate(packet)
            before = self._counts()
            with self.subTest(mutation=name), self.assertRaisesRegex(
                PersistentMappingError, message
            ) as raised:
                execute_supplier_mapping_intake(
                    self.mapping_url,
                    package=packet["package"],
                    candidates=packet["candidates"],
                    principal=self.principal,
                    intake_idempotency_key=packet["intake_idempotency_key"],
                )
            self.assertEqual(raised.exception.code, "MAPPING_REFUSED")
            self.assertEqual(self._counts(), before)

    def test_candidate_preserves_explicit_null_and_absent_states(self):
        absent_packet = self._packet(
            0,
            occurrence="state-absent",
            package_id="state-nullability",
        )
        explicit_null = copy.deepcopy(absent_packet["candidates"][0])
        explicit_null["occurrence_key"] = "state-explicit-null"
        explicit_null["source_row_key"] = "state-explicit-null"
        explicit_null["distributor_product_id_state"] = "EXPLICIT_NULL"
        nullability_packet = self._packet(
            0,
            occurrence="state-absent",
            package_id="state-nullability",
            extra_candidates=[explicit_null],
        )
        first = execute_supplier_mapping_intake(
            self.mapping_url,
            package=nullability_packet["package"],
            candidates=nullability_packet["candidates"],
            principal=self.principal,
            intake_idempotency_key=nullability_packet["intake_idempotency_key"],
        )
        candidate_ids = [UUID(value) for value in first["candidate_ids"]]
        with psycopg.connect(self.mapping_url) as conn:
            state_rows = tuple(
                conn.execute(
                    f"SELECT occurrence_key,distributor_product_id_state,"
                    "distributor_product_id_value,package_type_state,package_type_value,"
                    "supplier_identity_key_sha256,operational_offer_key_sha256,"
                    "decision_scope_sha256,canonical_payload,candidate_sha256 "
                    f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                    "WHERE candidate_id=ANY(%s) ORDER BY occurrence_key",
                    (candidate_ids,),
                ).fetchall()
            )
        self.assertEqual(
            [(row[0], row[1], row[2]) for row in state_rows],
            [
                ("state-absent", "ABSENT", None),
                ("state-explicit-null", "EXPLICIT_NULL", None),
            ],
        )
        self.assertTrue(
            all((row[3], row[4], row[5], row[6]) == ("EXPLICIT_NULL", None, None, None)
                for row in state_rows)
        )
        self.assertNotEqual(state_rows[0][8], state_rows[1][8])
        self.assertNotEqual(state_rows[0][9], state_rows[1][9])
        normalized_payloads = []
        for row in state_rows:
            payload = copy.deepcopy(row[8])
            for field in (
                "occurrence_index",
                "occurrence_key",
                "source_row_key",
                "decision_scope_sha256",
            ):
                payload.pop(field)
            normalized_payloads.append(payload)
        differing_fields = {
            key
            for key in normalized_payloads[0]
            if normalized_payloads[0][key] != normalized_payloads[1][key]
        }
        self.assertEqual(differing_fields, {"distributor_product_id_state"})
        self.assertNotEqual(
            _canonical_source_sha256(normalized_payloads[0]),
            _canonical_source_sha256(normalized_payloads[1]),
        )

        replay = execute_supplier_mapping_intake(
            self.mapping_url,
            package=nullability_packet["package"],
            candidates=nullability_packet["candidates"],
            principal=self.principal,
            intake_idempotency_key=nullability_packet["intake_idempotency_key"],
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["candidate_ids"], first["candidate_ids"])
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                tuple(
                    conn.execute(
                        f"SELECT occurrence_key,distributor_product_id_state,"
                        "distributor_product_id_value,package_type_state,package_type_value,"
                        "supplier_identity_key_sha256,operational_offer_key_sha256,"
                        "decision_scope_sha256,canonical_payload,candidate_sha256 "
                        f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                        "WHERE candidate_id=ANY(%s) ORDER BY occurrence_key",
                        (candidate_ids,),
                    ).fetchall()
                ),
                state_rows,
            )

        full = copy.deepcopy(load_synthetic_mapping_packets()[1]["candidates"][0])
        no_vendor = copy.deepcopy(full)
        no_vendor.update(
            occurrence_key="identity-no-vendor",
            source_row_key="identity-no-vendor",
            proposed_vendor_id=None,
        )
        supplier_only = copy.deepcopy(full)
        supplier_only.update(
            occurrence_key="identity-no-variant",
            source_row_key="identity-no-variant",
            proposed_variant_id=None,
        )
        no_distributor = copy.deepcopy(full)
        no_distributor.update(
            occurrence_key="identity-no-distributor",
            source_row_key="identity-no-distributor",
            distributor_product_id_state="ABSENT",
        )
        no_distributor.pop("distributor_product_id_value")
        unknown_class = copy.deepcopy(full)
        unknown_class.update(
            occurrence_key="identity-unknown-class",
            source_row_key="identity-unknown-class",
            offer_class="UNKNOWN",
        )
        package_absent = copy.deepcopy(full)
        package_absent.update(
            occurrence_key="identity-package-absent",
            source_row_key="identity-package-absent",
            package_type_state="ABSENT",
        )
        package_absent.pop("package_type_value")
        package_null = copy.deepcopy(full)
        package_null.update(
            occurrence_key="identity-package-null",
            source_row_key="identity-package-null",
            package_type_state="EXPLICIT_NULL",
        )
        package_null.pop("package_type_value")
        package_blank = copy.deepcopy(full)
        package_blank.update(
            occurrence_key="identity-package-blank",
            source_row_key="identity-package-blank",
            package_type_value="",
        )
        assortment_absent = copy.deepcopy(full)
        assortment_absent.update(
            occurrence_key="identity-assortment-absent",
            source_row_key="identity-assortment-absent",
            assortment_scope_state="ABSENT",
        )
        assortment_absent.pop("assortment_scope_value")
        distributor_blank = copy.deepcopy(full)
        distributor_blank.update(
            occurrence_key="identity-distributor-blank",
            source_row_key="identity-distributor-blank",
            distributor_product_id_value="",
        )
        supplier_code_blank = copy.deepcopy(full)
        supplier_code_blank.update(
            occurrence_key="identity-supplier-code-blank",
            source_row_key="identity-supplier-code-blank",
            supplier_code_value="",
        )
        supplier_code_padded = copy.deepcopy(full)
        supplier_code_padded.update(
            occurrence_key="identity-supplier-code-padded",
            source_row_key="identity-supplier-code-padded",
            supplier_code_value=" PADDED ",
        )
        minimum_packet = self._packet(
            1,
            occurrence="identity-complete",
            package_id="identity-minimums",
            extra_candidates=[
                no_vendor,
                supplier_only,
                no_distributor,
                unknown_class,
                package_absent,
                package_null,
                package_blank,
                assortment_absent,
                distributor_blank,
                supplier_code_blank,
                supplier_code_padded,
            ],
        )
        minimum_result = execute_supplier_mapping_intake(
            self.mapping_url,
            package=minimum_packet["package"],
            candidates=minimum_packet["candidates"],
            principal=self.principal,
            intake_idempotency_key=minimum_packet["intake_idempotency_key"],
        )
        with psycopg.connect(self.mapping_url) as conn:
            minimum_rows = dict(
                conn.execute(
                    f"SELECT occurrence_key,ARRAY["
                    "supplier_identity_key_sha256,operational_offer_key_sha256] "
                    f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                    "WHERE candidate_id=ANY(%s)",
                    ([UUID(value) for value in minimum_result["candidate_ids"]],),
                ).fetchall()
            )
        self.assertTrue(all(minimum_rows["identity-complete"]))
        self.assertEqual(minimum_rows["identity-no-distributor"], [None, None])
        self.assertEqual(minimum_rows["identity-no-vendor"], [None, None])
        self.assertIsNotNone(minimum_rows["identity-no-variant"][0])
        self.assertIsNone(minimum_rows["identity-no-variant"][1])
        for occurrence in (
            "identity-unknown-class",
            "identity-package-absent",
            "identity-package-null",
            "identity-package-blank",
            "identity-assortment-absent",
        ):
            self.assertIsNotNone(minimum_rows[occurrence][0])
            self.assertIsNone(minimum_rows[occurrence][1])
        self.assertEqual(minimum_rows["identity-distributor-blank"], [None, None])
        for occurrence in (
            "identity-supplier-code-blank",
            "identity-supplier-code-padded",
        ):
            self.assertIsNotNone(minimum_rows[occurrence][0])
            self.assertIsNone(minimum_rows[occurrence][1])

    def test_defer_accepts_completely_unresolved_and_partially_known_candidates(self):
        packets = {
            "ABSENT": self._packet(
                0,
                occurrence="defer-absent",
                package_id="defer-absent-package",
            ),
            "EXPLICIT_NULL": self._packet(
                0,
                occurrence="defer-explicit-null",
                package_id="defer-explicit-null-package",
                candidate_changes={
                    "distributor_product_id_state": "EXPLICIT_NULL",
                    "supplier_code_state": "EXPLICIT_NULL",
                },
            ),
            "VALUE": self._packet(
                1,
                occurrence="defer-partial-value",
                package_id="defer-partial-value-package",
                candidate_changes={"proposed_variant_id": None},
            ),
        }
        deferred_ids: list[UUID] = []
        for source_state, packet in packets.items():
            candidate_id = self._intake(packet)
            with psycopg.connect(self.mapping_url) as conn:
                candidate_before = conn.execute(
                    f"SELECT to_jsonb(c) FROM {SCHEMA}.supplier_mapping_review_candidates c "
                    "WHERE candidate_id=%s",
                    (candidate_id,),
                ).fetchone()[0]
            for action, link_kind in (
                ("APPROVE_MAPPING", "CREATED_INACTIVE"),
                ("REJECT_MAPPING", None),
            ):
                with self.subTest(
                    source_state=source_state, refused_action=action
                ), self.assertRaises(PersistentMappingError) as refused:
                    self._preview_decision(
                        candidate_id,
                        action=action,
                        reason="unresolved target cannot create authority",
                        key=uuid4(),
                        link_kind=link_kind,
                    )
                self.assertEqual(refused.exception.code, "MAPPING_REFUSED")
            decision = self._decide(
                candidate_id,
                action="DEFER",
                reason=f"retain {source_state.lower()} evidence",
            )
            deferred_ids.append(UUID(str(decision["mapping_decision_id"])))
            with psycopg.connect(self.mapping_url) as conn:
                candidate_after = conn.execute(
                    f"SELECT to_jsonb(c) FROM {SCHEMA}.supplier_mapping_review_candidates c "
                    "WHERE candidate_id=%s",
                    (candidate_id,),
                ).fetchone()[0]
                stored = conn.execute(
                    f"SELECT action,decision_origin,authority_kind,variant_id,vendor_id,"
                    "supplier_identity_key_sha256,operational_offer_key_sha256,"
                    "result_offer_package_type,expected_catalog_sha256,expected_vendor_sha256,"
                    "expected_rejection_memory_sha256,result_offer_id,offer_link_kind,"
                    "result_offer_contract_sha256,result_rejection_id,"
                    "result_rejection_contract_sha256,expected_batch_payload_sha256,"
                    "expected_candidate_sha256,reviewed_facts,component_relationships,"
                    "owner_clarifications,historical_capture_scope,human_principal_ref,"
                    "human_role_ref,human_authn_context_sha256,preview_sha256,"
                    "confirmation_sha256 "
                    f"FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE mapping_decision_id=%s",
                    (decision["mapping_decision_id"],),
                ).fetchone()
            self.assertEqual(candidate_after, candidate_before)
            self.assertEqual(stored[:2], ("DEFER", "HUMAN"))
            self.assertEqual(stored[2:16], (None,) * 14)
            self.assertEqual(
                stored[16:22],
                (
                    decision["expected_batch_payload_sha256"],
                    candidate_before["candidate_sha256"],
                    decision["reviewed_facts"],
                    decision["component_relationships"],
                    decision["owner_clarifications"],
                    decision["historical_capture_scope"],
                ),
            )
            self.assertEqual(
                stored[22:25],
                (
                    self.principal.principal_ref,
                    self.principal.role_ref,
                    self.principal.authn_context_sha256,
                ),
            )
            self.assertEqual(stored[25], decision["preview_sha256"])
            self.assertEqual(stored[26], decision["confirmation_sha256"])
            self.assertRegex(stored[25], r"^[0-9a-f]{64}$")
            self.assertRegex(stored[26], r"^[0-9a-f]{64}$")

        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE mapping_decision_id=ANY(%s) AND action='DEFER' "
                    "AND authority_kind IS NULL",
                    (deferred_ids,),
                ).fetchone()[0],
                3,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT (SELECT count(*) FROM {SCHEMA}.supplier_offers),"
                    f"(SELECT count(*) FROM {SCHEMA}.mapping_rejections),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_events),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads),"
                    f"(SELECT count(*) FROM {SCHEMA}.prices)"
                ).fetchone(),
                (0, 0, 0, 0, 0),
            )

        approved_candidate = self._intake(
            self._packet(
                1,
                occurrence="approval-then-defer",
                package_id="approval-then-defer-package",
                candidate_changes={
                    "supplier_code_value": "APPROVE-DEFER-001",
                    "distributor_product_id_value": "APPROVE-DEFER-001",
                },
            )
        )
        approval = self._decide(
            approved_candidate,
            action="APPROVE_MAPPING",
            reason="temporary effective approval",
            link_kind="CREATED_INACTIVE",
        )
        with psycopg.connect(self.mapping_url) as conn:
            approval_before = conn.execute(
                f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                "WHERE mapping_decision_id=%s",
                (approval["mapping_decision_id"],),
            ).fetchone()[0]
            offer_before = conn.execute(
                f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                (approval["result_offer_id"],),
            ).fetchone()[0]
        superseding_defer = self._decide(
            approved_candidate,
            action="DEFER",
            reason="supersede approval without erasing history",
        )
        self.assertEqual(
            superseding_defer["supersedes_mapping_decision_id"],
            approval["mapping_decision_id"],
        )
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT action FROM {SCHEMA}.v_effective_supplier_mapping_decisions "
                    "WHERE candidate_id=%s",
                    (approved_candidate,),
                ).fetchone()[0],
                "DEFER",
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.v_effective_supplier_mapping_decisions "
                    "WHERE candidate_id=%s AND action='APPROVE_MAPPING'",
                    (approved_candidate,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                    "WHERE mapping_decision_id=%s",
                    (approval["mapping_decision_id"],),
                ).fetchone()[0],
                approval_before,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                    (approval["result_offer_id"],),
                ).fetchone()[0],
                offer_before,
            )
            self.assertEqual(
                (
                    approval_before["operational_offer_key_sha256"],
                    approval_before["result_offer_id"],
                ),
                (
                    approval["operational_offer_key_sha256"],
                    approval["result_offer_id"],
                ),
            )

    def test_repeated_occurrences_and_tiers_share_one_operational_offer(self):
        offer = self._legacy_offer()
        first = self._packet(1, occurrence="repeat-a", package_id="repeat-package")
        second_candidate = copy.deepcopy(first["candidates"][0])
        second_candidate["occurrence_key"] = "repeat-b"
        second_candidate["source_row_key"] = "repeat-b"
        second_candidate["occurrence_role"] = "TIER"
        packet = self._packet(
            1,
            occurrence="repeat-a",
            package_id="repeat-package",
            candidate_changes={"occurrence_role": "REPEAT"},
            extra_candidates=[second_candidate],
        )
        result = execute_supplier_mapping_intake(self.mapping_url, package=packet["package"], candidates=packet["candidates"], principal=self.principal, intake_idempotency_key=packet["intake_idempotency_key"])
        candidate_ids = [UUID(value) for value in result["candidate_ids"]]
        with psycopg.connect(self.mapping_url) as conn:
            candidates = tuple(
                conn.execute(
                    f"SELECT candidate_id,occurrence_index,occurrence_key,occurrence_role,"
                    "source_row_key,printed_occurrence_sha256,operational_offer_key_sha256,"
                    "decision_scope_sha256,canonical_payload,candidate_sha256 "
                    f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                    "WHERE candidate_id=ANY(%s) ORDER BY occurrence_index",
                    (candidate_ids,),
                ).fetchall()
            )
        self.assertEqual([row[0] for row in candidates], candidate_ids)
        self.assertEqual(
            [(row[1], row[2], row[3], row[4]) for row in candidates],
            [
                (1, "repeat-a", "REPEAT", "repeat-a"),
                (2, "repeat-b", "TIER", "repeat-b"),
            ],
        )
        self.assertTrue(all(row[5] == candidates[0][5] for row in candidates))
        self.assertTrue(all(row[6] == candidates[0][6] for row in candidates))
        self.assertNotEqual(candidates[0][7], candidates[1][7])
        self.assertNotEqual(candidates[0][8], candidates[1][8])
        self.assertNotEqual(candidates[0][9], candidates[1][9])
        decisions = [
            self._decide(
                candidate_id,
                action="APPROVE_MAPPING",
                reason=f"repeat {index}",
                offer_id=offer,
                link_kind="LINKED_EXISTING",
            )
            for index, candidate_id in enumerate(candidate_ids)
        ]
        self.assertEqual(
            len({UUID(str(item["mapping_decision_id"])) for item in decisions}),
            2,
        )
        self.assertEqual(
            {UUID(str(item["candidate_id"])) for item in decisions},
            set(candidate_ids),
        )
        self.assertEqual(
            {item["decision_scope_sha256"] for item in decisions},
            {row[7] for row in candidates},
        )
        self.assertEqual(
            {item["operational_offer_key_sha256"] for item in decisions},
            {candidates[0][6]},
        )
        self.assertEqual({item["result_offer_id"] for item in decisions}, {offer})
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s)"
                    ,
                    (candidate_ids,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(DISTINCT result_offer_id) "
                    f"FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s)",
                    (candidate_ids,),
                ).fetchone()[0],
                1,
            )

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
        self.assertEqual(
            len({preview["operational_offer_key_sha256"] for preview in previews}),
            1,
        )
        barrier = Barrier(2)
        contention_observed = Event()
        failed_lock_pids: set[int] = set()
        original_try_lock = mapping_service._try_session_lock
        original_record = mapping_service.record_mapping_decision
        expected_offer_lock = (
            "persistent-mapping:operational-offer-key:"
            f"{previews[0]['operational_offer_key_sha256']}"
        )

        def observed_try_lock(conn, lock_name: str, deadline: float):
            if lock_name != expected_offer_lock:
                return original_try_lock(conn, lock_name, deadline)
            locked = conn.execute(
                "SELECT pg_catalog.pg_try_advisory_lock("
                "pg_catalog.hashtextextended(%s,0))",
                (lock_name,),
            ).fetchone()[0]
            if locked:
                return
            failed_lock_pids.add(conn.info.backend_pid)
            contention_observed.set()
            return original_try_lock(conn, lock_name, deadline)

        def hold_winner_until_real_contention(conn, **kwargs):
            if not contention_observed.wait(timeout=10):
                raise AssertionError(
                    "the competing create never failed the shared offer-key lock"
                )
            return original_record(conn, **kwargs)

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

        with (
            mock.patch.object(
                mapping_service, "_try_session_lock", side_effect=observed_try_lock
            ),
            mock.patch.object(
                mapping_service,
                "record_mapping_decision",
                side_effect=hold_winner_until_real_contention,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            outcomes = [
                future.result()
                for future in (pool.submit(create, 0), pool.submit(create, 1))
            ]
        self.assertTrue(contention_observed.is_set())
        self.assertEqual(len(failed_lock_pids), 1)
        self.assertEqual([item[0] for item in outcomes].count("committed"), 1)
        self.assertEqual([item[0] for item in outcomes].count("refused"), 1)
        winner_index = next(index for index, item in enumerate(outcomes) if item[0] == "committed")
        loser_index = 1 - winner_index
        first_result = outcomes[winner_index][1]
        assert isinstance(first_result, dict)
        loser = outcomes[loser_index][1]
        self.assertIsInstance(loser, PersistentMappingError)
        assert isinstance(loser, PersistentMappingError)
        self.assertEqual(loser.code, "RECONFIRMATION_REQUIRED")
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
            self.assertEqual(
                conn.execute(
                    f"SELECT active,(SELECT count(*) FROM {SCHEMA}.prices p "
                    "WHERE p.offer_id=o.offer_id) "
                    f"FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                    (first_result["result_offer_id"],),
                ).fetchone(),
                (False, 0),
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=%s OR decision_idempotency_key=%s",
                    (ids[loser_index], create_keys[loser_index]),
                ).fetchone()[0],
                0,
            )

        # A fresh linked preview and separate key are required after the winner.
        linked_key = uuid4()
        self.assertNotIn(linked_key, create_keys)
        linked_preview = self._preview_decision(
            ids[loser_index],
            action="APPROVE_MAPPING",
            reason="create shared",
            key=linked_key,
            offer_id=first_result["result_offer_id"],
            link_kind="LINKED_EXISTING",
        )
        self.assertNotIn(
            linked_preview["preview_sha256"],
            {preview["preview_sha256"] for preview in previews},
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
        self.assertNotEqual(
            first_result["confirmation_sha256"], linked["confirmation_sha256"]
        )
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*),count(DISTINCT result_offer_id) "
                    f"FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s) "
                    "AND action='APPROVE_MAPPING'",
                    (ids,),
                ).fetchone(),
                (2, 1),
            )

        # Superseding the first approval never releases its historical
        # operational-key/offer binding for a later occurrence.
        self._decide(
            ids[winner_index],
            action="DEFER",
            reason="supersede without erasing historical offer identity",
        )
        self._decide(
            ids[loser_index],
            action="DEFER",
            reason="supersede linked approval without erasing historical identity",
        )
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM "
                    f"{SCHEMA}.v_effective_supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s) AND action='APPROVE_MAPPING'",
                    (ids,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*),count(DISTINCT result_offer_id) FROM "
                    f"{SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s) AND action='APPROVE_MAPPING'",
                    (ids,),
                ).fetchone(),
                (2, 1),
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
        with self.assertRaisesRegex(
            PersistentMappingError, "equivalent offer"
        ) as duplicate_create:
            self._decide(
                later_id,
                action="APPROVE_MAPPING",
                reason="historical binding refuses duplicate create",
                link_kind="CREATED_INACTIVE",
            )
        self.assertEqual(
            duplicate_create.exception.code, "RECONFIRMATION_REQUIRED"
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
        self.assertEqual(
            historical_link["operational_offer_key_sha256"],
            first_result["operational_offer_key_sha256"],
        )
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(DISTINCT operational_offer_key_sha256) "
                    f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                    "WHERE candidate_id=ANY(%s)",
                    ([*ids, later_id],),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(DISTINCT result_offer_id) "
                    f"FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s) AND action='APPROVE_MAPPING'",
                    ([*ids, later_id],),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_offers "
                    "WHERE supplier_sku='NEW-001'"
                ).fetchone()[0],
                1,
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
            code = "CLASS-SHARED"
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
            with psycopg.connect(self.mapping_url) as conn:
                self.assertEqual(
                    conn.execute(
                        f"SELECT component_relationships FROM {SCHEMA}.supplier_mapping_review_candidates "
                        "WHERE candidate_id=%s",
                        (candidate_id,),
                    ).fetchone()[0],
                    relationships,
                )
                self.assertEqual(
                    conn.execute(
                        f"SELECT component_relationships FROM {SCHEMA}.supplier_mapping_decisions "
                        "WHERE mapping_decision_id=%s",
                        (decision["mapping_decision_id"],),
                    ).fetchone()[0],
                    relationships,
                )
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
        selected = self._select(decisions["REGULAR"], reason="regular alone may select")
        with psycopg.connect(self.mapping_url) as conn:
            selection_state = (
                tuple(
                    conn.execute(
                        f"SELECT * FROM {SCHEMA}.supplier_offer_selection_heads "
                        "ORDER BY variant_id"
                    ).fetchall()
                ),
                tuple(
                    conn.execute(
                        f"SELECT * FROM {SCHEMA}.supplier_offer_selection_events "
                        "ORDER BY selection_event_id"
                    ).fetchall()
                ),
            )
        self.assertEqual(
            selection_state[0][0][2], UUID(str(selected["selection_event_id"]))
        )
        for offer_class in package_types.keys() - {"REGULAR"}:
            with self.subTest(offer_class=offer_class), self.assertRaises(
                PersistentMappingError
            ):
                self._select(decisions[offer_class], reason="nonregular must refuse")
            with psycopg.connect(self.mapping_url) as conn:
                self.assertEqual(
                    (
                        tuple(
                            conn.execute(
                                f"SELECT * FROM {SCHEMA}.supplier_offer_selection_heads "
                                "ORDER BY variant_id"
                            ).fetchall()
                        ),
                        tuple(
                            conn.execute(
                                f"SELECT * FROM {SCHEMA}.supplier_offer_selection_events "
                                "ORDER BY selection_event_id"
                            ).fetchall()
                        ),
                    ),
                    selection_state,
                )

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
        with psycopg.connect(self.mapping_url) as conn:
            race_candidates = tuple(
                conn.execute(
                    f"SELECT operational_offer_key_sha256,identity_qualifiers,canonical_payload "
                    f"FROM {SCHEMA}.supplier_mapping_review_candidates "
                    "WHERE candidate_id=ANY(%s) ORDER BY occurrence_index",
                    (race_ids,),
                ).fetchall()
            )
        self.assertNotEqual(race_candidates[0][0], race_candidates[1][0])
        self.assertEqual(
            [row[1] for row in race_candidates],
            [
                {"material_qualifier": "A"},
                {"material_qualifier": "B"},
            ],
        )
        normalized_race_payloads = []
        for row in race_candidates:
            payload = copy.deepcopy(row[2])
            for field in (
                "occurrence_index",
                "occurrence_key",
                "source_row_key",
                "decision_scope_sha256",
                "operational_offer_key_sha256",
                "identity_qualifiers",
            ):
                payload.pop(field)
            normalized_race_payloads.append(payload)
        self.assertEqual(normalized_race_payloads[0], normalized_race_payloads[1])
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
        with psycopg.connect(self.mapping_url) as conn:
            race_state_before = (
                conn.execute(
                    f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                    (shared_offer,),
                ).fetchone()[0],
                tuple(
                    conn.execute(
                        f"SELECT * FROM {SCHEMA}.supplier_offer_selection_heads "
                        "ORDER BY variant_id"
                    ).fetchall()
                ),
                tuple(
                    conn.execute(
                        f"SELECT * FROM {SCHEMA}.supplier_offer_selection_events "
                        "ORDER BY selection_event_id"
                    ).fetchall()
                ),
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.mapping_rejections"
                ).fetchone()[0],
            )
        race_counts_before = self._counts()
        race_barrier = Barrier(2)
        race_contention_observed = Event()
        race_failed_lock_pids: set[int] = set()
        original_try_lock = mapping_service._try_session_lock
        original_record = mapping_service.record_mapping_decision
        expected_offer_id_lock = (
            f"persistent-mapping:existing-offer-id:{shared_offer}"
        )

        def observed_offer_id_lock(conn, lock_name: str, deadline: float):
            if lock_name != expected_offer_id_lock:
                return original_try_lock(conn, lock_name, deadline)
            locked = conn.execute(
                "SELECT pg_catalog.pg_try_advisory_lock("
                "pg_catalog.hashtextextended(%s,0))",
                (lock_name,),
            ).fetchone()[0]
            if locked:
                return
            race_failed_lock_pids.add(conn.info.backend_pid)
            race_contention_observed.set()
            return original_try_lock(conn, lock_name, deadline)

        def hold_link_winner_until_real_contention(conn, **kwargs):
            if not race_contention_observed.wait(timeout=10):
                raise AssertionError(
                    "the competing link never failed the shared offer-ID lock"
                )
            return original_record(conn, **kwargs)

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
        with (
            mock.patch.object(
                mapping_service,
                "_try_session_lock",
                side_effect=observed_offer_id_lock,
            ),
            mock.patch.object(
                mapping_service,
                "record_mapping_decision",
                side_effect=hold_link_winner_until_real_contention,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [pool.submit(approve, index) for index in range(2)]
            for future in futures:
                try:
                    outcomes.append(future.result())
                except PersistentMappingError as exc:
                    outcomes.append(exc)
        self.assertTrue(race_contention_observed.is_set())
        self.assertEqual(len(race_failed_lock_pids), 1)
        self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
        self.assertEqual(sum(isinstance(item, PersistentMappingError) for item in outcomes), 1)
        loser = next(item for item in outcomes if isinstance(item, PersistentMappingError))
        self.assertEqual(loser.code, "DATABASE_VALIDATION_REFUSED")
        self.assertIn(
            "one operational offer cannot represent different material identities",
            str(loser.__cause__),
        )
        winner_index = next(
            index for index, item in enumerate(outcomes) if isinstance(item, dict)
        )
        loser_index = 1 - winner_index
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=ANY(%s)",
                    (race_ids,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=%s OR decision_idempotency_key=%s",
                    (race_ids[loser_index], race_keys[loser_index]),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                (
                    conn.execute(
                        f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                        (shared_offer,),
                    ).fetchone()[0],
                    tuple(
                        conn.execute(
                            f"SELECT * FROM {SCHEMA}.supplier_offer_selection_heads "
                            "ORDER BY variant_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT * FROM {SCHEMA}.supplier_offer_selection_events "
                            "ORDER BY selection_event_id"
                        ).fetchall()
                    ),
                    conn.execute(
                        f"SELECT count(*) FROM {SCHEMA}.mapping_rejections"
                    ).fetchone()[0],
                ),
                race_state_before,
            )
        expected_counts = list(race_counts_before)
        expected_counts[2] += 1
        self.assertEqual(self._counts(), tuple(expected_counts))

    def test_reused_supplier_code_preserves_old_offer_and_creates_inactive_history(self):
        old = self._legacy_offer(variant_id="2002", sku="REUSED", active=True)
        with psycopg.connect(self.mapping_url) as conn:
            old_before = conn.execute(
                f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                (old,),
            ).fetchone()[0]
        packet = self._packet(
            1,
            occurrence="reuse-new",
            package_id="reuse-package",
            candidate_changes={
                "supplier_code_value": "REUSED",
                "distributor_product_id_value": "REUSED",
            },
        )
        candidate_id = self._intake(packet)

        before_refusals = self._counts()
        with self.assertRaises(PersistentMappingError) as human_refusal:
            self._decide(
                candidate_id,
                action="APPROVE_MAPPING",
                reason="materially different identity cannot reuse old offer",
                offer_id=old,
                link_kind="LINKED_EXISTING",
            )
        self.assertEqual(human_refusal.exception.code, "DATABASE_VALIDATION_REFUSED")
        self.assertIn(
            "resulting supplier offer contract differs",
            str(human_refusal.exception.__cause__),
        )
        self.assertEqual(self._counts(), before_refusals)

        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            conn.execute(
                "SELECT pg_catalog.set_config(%s,%s,true)",
                ("procurement.enabled_capability", "policy_mapping_writes_enabled"),
            )
            policy_record = mapping_service._mapping_decision_record(
                conn,
                candidate_id=candidate_id,
                action="APPROVE_MAPPING",
                reason="unpublished policy cannot reuse old offer",
                principal=self.principal,
                decision_idempotency_key=uuid4(),
                existing_offer_id=old,
                offer_link_kind="LINKED_EXISTING",
            )
            policy_record.update(
                {
                    "decision_origin": "POLICY",
                    "authority_kind": "POLICY_APPROVED",
                    "human_principal_ref": None,
                    "human_role_ref": None,
                    "human_authn_context_sha256": None,
                    "preview_sha256": None,
                    "confirmation_sha256": None,
                    "service_principal_ref": "synthetic:policy-evaluator:01",
                    "policy_ref": "unpublished-reuse-policy",
                    "policy_version": "v1",
                    "policy_publication_sha256": "0" * 64,
                    "policy_predicate_version": "v1",
                    "policy_predicate_result_sha256": "1" * 64,
                }
            )
            policy_record["request_sha256"] = mapping_service._composite_value(
                conn,
                table="supplier_mapping_decisions",
                function="persistent_mapping_decision_request_sha256",
                record=policy_record,
            )
            payload, payload_sha = mapping_service._project_record(
                conn,
                table="supplier_mapping_decisions",
                record=policy_record,
                omit=(
                    "mapping_decision_id",
                    "decision_idempotency_key",
                    "canonical_payload",
                    "payload_sha256",
                    "decided_at",
                    "decided_txid",
                ),
            )
            policy_record["canonical_payload"] = payload
            policy_record["payload_sha256"] = payload_sha
            with self.assertRaisesRegex(psycopg.Error, "no published mapping policy"):
                mapping_service._insert_record(
                    conn,
                    table="supplier_mapping_decisions",
                    record=policy_record,
                )
            conn.rollback()
        self.assertEqual(self._counts(), before_refusals)

        result = self._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="new material identity",
            link_kind="CREATED_INACTIVE",
        )
        self.assertNotEqual(result["result_offer_id"], old)
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                    (old,),
                ).fetchone()[0],
                old_before,
            )
            new_offer = conn.execute(
                f"SELECT variant_id,vendor_id,supplier_sku,active,"
                "(SELECT count(*) FROM "
                f"{SCHEMA}.prices p WHERE p.offer_id=o.offer_id),"
                f"{SCHEMA}.persistent_mapping_offer_fingerprint(o.offer_id) "
                f"FROM {SCHEMA}.supplier_offers o WHERE offer_id=%s",
                (result["result_offer_id"],),
            ).fetchone()
            self.assertEqual(
                new_offer,
                (
                    "1001",
                    VENDOR_ID,
                    "REUSED",
                    False,
                    0,
                    result["result_offer_contract_sha256"],
                ),
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*),count(*) FILTER (WHERE active) "
                    f"FROM {SCHEMA}.supplier_offers "
                    "WHERE vendor_id=%s AND supplier_sku='REUSED'",
                    (VENDOR_ID,),
                ).fetchone(),
                (2, 1),
            )
            index = conn.execute(
                "SELECT i.indisunique,i.indisvalid,i.indisready,i.indislive,"
                "i.indisprimary,i.indisexclusion,i.indimmediate,"
                "i.indnullsnotdistinct,i.indnkeyatts,i.indnatts,"
                "pg_catalog.pg_get_indexdef(i.indexrelid,1,true),"
                "pg_catalog.pg_get_indexdef(i.indexrelid,2,true),"
                "pg_catalog.pg_get_expr(i.indpred,i.indrelid,false) "
                "FROM pg_catalog.pg_index i "
                "JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=%s "
                "AND c.relname='uq_active_vendor_supplier_sku' "
                "AND i.indrelid=pg_catalog.to_regclass(%s)",
                (SCHEMA, f'"{SCHEMA}".supplier_offers'),
            ).fetchone()
            self.assertEqual(
                index[:12],
                (
                    True,
                    True,
                    True,
                    True,
                    False,
                    False,
                    True,
                    False,
                    2,
                    2,
                    "vendor_id",
                    "supplier_sku",
                ),
            )
            self.assertIn(
                index[12],
                {
                    "((active = true) AND (supplier_sku IS NOT NULL) AND "
                    "(supplier_sku <> ''::text))",
                    "(active AND (supplier_sku IS NOT NULL) AND "
                    "(supplier_sku <> ''::text))",
                },
            )
            before_duplicate = self._counts()
            conn.rollback()
            with self.assertRaises(psycopg.errors.UniqueViolation) as duplicate:
                with conn.transaction():
                    conn.execute(
                        f"INSERT INTO {SCHEMA}.supplier_offers("
                        "variant_id,vendor_id,supplier_sku,package_type,active) "
                        "VALUES('3003',%s,'REUSED','STANDARD',true)",
                        (VENDOR_ID,),
                    )
            self.assertEqual(
                duplicate.exception.diag.constraint_name,
                "uq_active_vendor_supplier_sku",
            )
            self.assertEqual(self._counts(), before_duplicate)
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*),count(*) FILTER (WHERE active) "
                    f"FROM {SCHEMA}.supplier_offers "
                    "WHERE vendor_id=%s AND supplier_sku='REUSED'",
                    (VENDOR_ID,),
                ).fetchone(),
                (2, 1),
            )

    def test_mapping_refuses_wrong_variant_or_vendor_and_stale_fingerprints(self):
        def authority_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    self._counts(),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                            "ORDER BY offer_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.mapping_rejections r "
                            "ORDER BY rejection_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                            "ORDER BY mapping_decision_id"
                        ).fetchall()
                    ),
                )

        nonexistent_targets = (
            (
                "variant",
                {"proposed_variant_id": "missing-variant"},
            ),
            (
                "vendor",
                {"proposed_vendor_id": str(uuid4())},
            ),
        )
        for target_kind, changes in nonexistent_targets:
            packet = self._packet(
                1,
                occurrence=f"missing-{target_kind}",
                package_id=f"missing-{target_kind}-package",
                candidate_changes=changes,
            )
            candidate_id = self._intake(packet)
            for action, link_kind in (
                ("APPROVE_MAPPING", "CREATED_INACTIVE"),
                ("REJECT_MAPPING", None),
            ):
                before = authority_state()
                with self.subTest(
                    target_kind=target_kind, action=action
                ), self.assertRaisesRegex(
                    PersistentMappingError,
                    "mapping target fingerprints are incomplete",
                ) as refused:
                    self._preview_decision(
                        candidate_id,
                        action=action,
                        reason="nonexistent mapping target",
                        key=uuid4(),
                        link_kind=link_kind,
                    )
                self.assertEqual(refused.exception.code, "MAPPING_REFUSED")
                self.assertEqual(authority_state(), before)
                before_execute = authority_state()
                with self.subTest(
                    target_kind=target_kind,
                    action=action,
                    boundary="execute",
                ), self.assertRaisesRegex(
                    PersistentMappingError,
                    "mapping target fingerprints are incomplete",
                ) as execute_refused:
                    execute_mapping_decision(
                        self.mapping_url,
                        candidate_id=candidate_id,
                        action=action,
                        reason="nonexistent mapping target",
                        principal=self.principal,
                        decision_idempotency_key=uuid4(),
                        expected_preview_sha256="0" * 64,
                        offer_link_kind=link_kind,
                    )
                self.assertEqual(execute_refused.exception.code, "MAPPING_REFUSED")
                self.assertEqual(authority_state(), before_execute)
            deferred = self._decide(
                candidate_id,
                action="DEFER",
                reason="nonexistent target remains review evidence only",
            )
            self.assertEqual(deferred["action"], "DEFER")
            self.assertTrue(
                all(
                    deferred[field] is None
                    for field in (
                        "variant_id",
                        "vendor_id",
                        "result_offer_id",
                        "result_rejection_id",
                    )
                )
            )

        for stale_kind in ("catalog", "vendor", "rejection"):
            for action in ("APPROVE_MAPPING", "REJECT_MAPPING"):
                action_label = action.removesuffix("_MAPPING").lower()
                supplier_code = f"STALE-{stale_kind.upper()}-{action_label.upper()}"
                offer = (
                    self._legacy_offer(sku=supplier_code)
                    if action == "APPROVE_MAPPING"
                    else None
                )
                candidate_id = self._intake(
                    self._packet(
                        1,
                        occurrence=f"stale-{stale_kind}-{action_label}",
                        package_id=f"stale-{stale_kind}-{action_label}-package",
                        candidate_changes={
                            "supplier_code_value": supplier_code,
                            "distributor_product_id_value": supplier_code,
                        },
                    )
                )
                key = uuid4()
                preview = self._preview_decision(
                    candidate_id,
                    action=action,
                    reason=f"stale {stale_kind} {action_label} preview",
                    key=key,
                    offer_id=offer,
                    link_kind=(
                        "LINKED_EXISTING"
                        if action == "APPROVE_MAPPING"
                        else None
                    ),
                )
                if stale_kind == "rejection":
                    blocker = self._intake(
                        self._packet(
                            1,
                            occurrence=(
                                f"stale-rejection-{action_label}-blocker"
                            ),
                            package_id=(
                                f"stale-rejection-{action_label}-blocker-package"
                            ),
                            candidate_changes={
                                "supplier_code_value": supplier_code,
                                "distributor_product_id_value": supplier_code,
                            },
                        )
                    )
                    self._decide(
                        blocker,
                        action="REJECT_MAPPING",
                        reason="change exact rejection-memory fingerprint",
                    )
                else:
                    with psycopg.connect(self.mapping_url) as conn:
                        if stale_kind == "catalog":
                            conn.execute(
                                f"UPDATE {SCHEMA}.variants SET source_snapshot=%s "
                                "WHERE variant_id='1001'",
                                (
                                    f"changed after {action_label} mapping preview",
                                ),
                            )
                        else:
                            conn.execute(
                                f"UPDATE {SCHEMA}.vendors "
                                "SET updated_at=updated_at + interval '1 second' "
                                "WHERE vendor_id=%s",
                                (VENDOR_ID,),
                            )
                before = authority_state()
                with self.subTest(
                    stale=stale_kind, action=action
                ), self.assertRaisesRegex(
                    PersistentMappingError, "mapping preview is stale"
                ) as refused:
                    execute_mapping_decision(
                        self.mapping_url,
                        candidate_id=candidate_id,
                        action=action,
                        reason=f"stale {stale_kind} {action_label} preview",
                        principal=self.principal,
                        decision_idempotency_key=key,
                        expected_preview_sha256=preview["preview_sha256"],
                        existing_offer_id=offer,
                        offer_link_kind=(
                            "LINKED_EXISTING"
                            if action == "APPROVE_MAPPING"
                            else None
                        ),
                    )
                self.assertEqual(refused.exception.code, "MAPPING_REFUSED")
                self.assertEqual(authority_state(), before)
                deferred = self._decide(
                    candidate_id,
                    action="DEFER",
                    reason=(
                        f"stale {stale_kind} {action_label} evidence safely deferred"
                    ),
                )
                self.assertEqual(deferred["action"], "DEFER")

        second_vendor = uuid4()
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute(
                f"INSERT INTO {SCHEMA}.vendors(vendor_id,vendor_name,active) "
                "VALUES(%s,'Synthetic Wrong Target Vendor',true)",
                (second_vendor,),
            )
        correct_offer = self._legacy_offer(sku="FORGED-TARGET")
        forged_candidate = self._intake(
            self._packet(
                1,
                occurrence="forged-valid-target",
                package_id="forged-valid-target-package",
                candidate_changes={
                    "supplier_code_value": "FORGED-TARGET",
                    "distributor_product_id_value": "FORGED-TARGET",
                },
            )
        )
        for action in ("APPROVE_MAPPING", "REJECT_MAPPING"):
            for field, changed_value in (
                ("variant_id", "2002"),
                ("vendor_id", str(second_vendor)),
            ):
                before = authority_state()
                with self.subTest(
                    action=action, forged_field=field
                ), self.assertRaisesRegex(
                    psycopg.Error, "mapping action target differs from candidate"
                ):
                    with psycopg.connect(self.mapping_url) as conn, conn.transaction():
                        conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        mapping_service._set_human_context(
                            conn,
                            self.principal,
                            action="SUPPLIER_MAPPING_DECIDE",
                            capability="human_mapping_writes_enabled",
                        )
                        record = mapping_service._mapping_decision_record(
                            conn,
                            candidate_id=forged_candidate,
                            action=action,
                            reason="forged FK-valid mapping target",
                            principal=self.principal,
                            decision_idempotency_key=uuid4(),
                            existing_offer_id=(
                                correct_offer if action == "APPROVE_MAPPING" else None
                            ),
                            offer_link_kind=(
                                "LINKED_EXISTING"
                                if action == "APPROVE_MAPPING"
                                else None
                            ),
                        )
                        if action == "REJECT_MAPPING":
                            candidate, _batch = (
                                mapping_service._candidate_decision_facts(
                                    conn, forged_candidate
                                )
                            )
                            rejection_id = mapping_service._insert_mapping_rejection(
                                conn,
                                candidate=candidate,
                                principal=self.principal,
                                evidence_set_sha256=record["evidence_set_sha256"],
                            )
                            record["result_rejection_id"] = rejection_id
                            record["result_rejection_contract_sha256"] = conn.execute(
                                f"SELECT {SCHEMA}."
                                "persistent_mapping_rejection_contract_fingerprint(%s)",
                                (rejection_id,),
                            ).fetchone()[0]
                        record[field] = changed_value
                        record = mapping_service._seal_mapping_decision_record(
                            conn, record
                        )
                        mapping_service._insert_record(
                            conn,
                            table="supplier_mapping_decisions",
                            record=record,
                        )
                self.assertEqual(authority_state(), before)

        safe_defer = self._decide(
            forged_candidate,
            action="DEFER",
            reason="FK-valid mismatch attempts leave DEFER available",
        )
        self.assertEqual(safe_defer["action"], "DEFER")
        self.assertIsNone(safe_defer["result_offer_id"])
        self.assertIsNone(safe_defer["result_rejection_id"])

    def test_authority_history_rejects_update_delete_and_cascade(self):
        rejected_candidate = self._intake(
            self._packet(
                1,
                occurrence="immutable-rejection",
                package_id="immutable-rejection-package",
                candidate_changes={
                    "proposed_variant_id": "2002",
                    "supplier_code_value": "IMMUTABLE-REJECTION",
                    "distributor_product_id_value": "IMMUTABLE-REJECTION",
                },
            )
        )
        rejection = self._decide(
            rejected_candidate,
            action="REJECT_MAPPING",
            reason="exact immutable rejection",
        )
        offer = self._legacy_offer(sku="IMMUTABLE-SELECTION")
        selected_candidate = self._intake(
            self._packet(
                1,
                occurrence="immutable-selection",
                package_id="immutable-selection-package",
                candidate_changes={
                    "supplier_code_value": "IMMUTABLE-SELECTION",
                    "distributor_product_id_value": "IMMUTABLE-SELECTION",
                },
            )
        )
        approval = self._decide(
            selected_candidate,
            action="APPROVE_MAPPING",
            reason="exact immutable approval",
            offer_id=offer,
            link_kind="LINKED_EXISTING",
        )
        selection = self._select(approval)

        protected_tables = (
            "supplier_mapping_review_batches",
            "supplier_mapping_review_candidates",
            "supplier_mapping_decisions",
            "mapping_rejections",
            "supplier_offer_selection_events",
            "supplier_offer_selection_heads",
        )

        def authority_rows(conn) -> tuple[tuple[str, tuple[str, ...]], ...]:
            return tuple(
                (
                    table_name,
                    tuple(
                        str(row[0])
                        for row in conn.execute(
                            sql.SQL(
                                "SELECT pg_catalog.to_jsonb(t)::text "
                                "FROM {}.{} t ORDER BY 1"
                            ).format(
                                sql.Identifier(SCHEMA),
                                sql.Identifier(table_name),
                            )
                        ).fetchall()
                    ),
                )
                for table_name in protected_tables
            )

        with psycopg.connect(self.mapping_url) as conn:
            candidate_row = conn.execute(
                f"SELECT review_batch_id FROM "
                f"{SCHEMA}.supplier_mapping_review_candidates "
                "WHERE candidate_id=%s",
                (rejected_candidate,),
            ).fetchone()
            assert candidate_row is not None
            batch_id = candidate_row[0]
            before = authority_rows(conn)
            conn.rollback()
            mutations = (
                (
                    "batch update",
                    f"UPDATE {SCHEMA}.supplier_mapping_review_batches "
                    "SET source_revision='changed' WHERE review_batch_id=%s",
                    batch_id,
                    "supplier_mapping_review_batches is append-only; UPDATE is forbidden",
                ),
                (
                    "batch delete cascade attempt",
                    f"DELETE FROM {SCHEMA}.supplier_mapping_review_batches "
                    "WHERE review_batch_id=%s",
                    batch_id,
                    "supplier_mapping_review_batches is append-only; DELETE is forbidden",
                ),
                (
                    "candidate update",
                    f"UPDATE {SCHEMA}.supplier_mapping_review_candidates "
                    "SET blockers='[\"changed\"]'::jsonb WHERE candidate_id=%s",
                    rejected_candidate,
                    "supplier_mapping_review_candidates is append-only; UPDATE is forbidden",
                ),
                (
                    "candidate delete cascade attempt",
                    f"DELETE FROM {SCHEMA}.supplier_mapping_review_candidates "
                    "WHERE candidate_id=%s",
                    rejected_candidate,
                    "supplier_mapping_review_candidates is append-only; DELETE is forbidden",
                ),
                (
                    "decision update",
                    f"UPDATE {SCHEMA}.supplier_mapping_decisions "
                    "SET reason='changed' WHERE mapping_decision_id=%s",
                    rejection["mapping_decision_id"],
                    "supplier_mapping_decisions is append-only; UPDATE is forbidden",
                ),
                (
                    "decision delete cascade attempt",
                    f"DELETE FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE mapping_decision_id=%s",
                    rejection["mapping_decision_id"],
                    "supplier_mapping_decisions is append-only; DELETE is forbidden",
                ),
                (
                    "selection event update",
                    f"UPDATE {SCHEMA}.supplier_offer_selection_events "
                    "SET reason='changed' WHERE selection_event_id=%s",
                    selection["selection_event_id"],
                    "supplier_offer_selection_events is append-only; UPDATE is forbidden",
                ),
                (
                    "selection event delete cascade attempt",
                    f"DELETE FROM {SCHEMA}.supplier_offer_selection_events "
                    "WHERE selection_event_id=%s",
                    selection["selection_event_id"],
                    "supplier_offer_selection_events is append-only; DELETE is forbidden",
                ),
                (
                    "selection head update without event",
                    f"UPDATE {SCHEMA}.supplier_offer_selection_heads "
                    "SET head_version=head_version+1 WHERE variant_id='1001'",
                    None,
                    "selection head transaction identity differs",
                ),
                (
                    "selection head delete",
                    f"DELETE FROM {SCHEMA}.supplier_offer_selection_heads "
                    "WHERE variant_id='1001'",
                    None,
                    "routine selection head cannot be deleted",
                ),
                (
                    "linked rejection evidence update",
                    f"UPDATE {SCHEMA}.mapping_rejections "
                    "SET evidence_json='{\"changed\":true}'::jsonb "
                    "WHERE rejection_id=%s",
                    rejection["result_rejection_id"],
                    "rejection linked by persistent mapping authority is immutable",
                ),
                (
                    "linked rejection actor update",
                    f"UPDATE {SCHEMA}.mapping_rejections "
                    "SET rejected_by='changed' WHERE rejection_id=%s",
                    rejection["result_rejection_id"],
                    "rejection linked by persistent mapping authority is immutable",
                ),
                (
                    "linked rejection active-state update",
                    f"UPDATE {SCHEMA}.mapping_rejections "
                    "SET active=false WHERE rejection_id=%s",
                    rejection["result_rejection_id"],
                    "rejection linked by persistent mapping authority is immutable",
                ),
                (
                    "linked rejection delete",
                    f"DELETE FROM {SCHEMA}.mapping_rejections "
                    "WHERE rejection_id=%s",
                    rejection["result_rejection_id"],
                    "rejection linked by persistent mapping authority is immutable",
                ),
            )
            for label, statement, value, expected_error in mutations:
                parameters = () if value is None else (value,)
                with self.subTest(mutation=label), self.assertRaisesRegex(
                    psycopg.errors.RaiseException, expected_error
                ):
                    with conn.transaction():
                        conn.execute(statement, parameters)
                self.assertEqual(authority_rows(conn), before)
                conn.rollback()

    def test_mapping_exact_replay_and_same_key_different_payload(self):
        def authority_state() -> tuple:
            tables = (
                "supplier_mapping_review_batches",
                "supplier_mapping_review_candidates",
                "supplier_mapping_decisions",
                "supplier_offers",
                "mapping_rejections",
                "supplier_offer_selection_events",
                "supplier_offer_selection_heads",
                "prices",
                "procurement_recommendations",
            )
            with psycopg.connect(self.mapping_url) as conn:
                return tuple(
                    (
                        table,
                        tuple(
                            str(row[0])
                            for row in conn.execute(
                                sql.SQL(
                                    "SELECT pg_catalog.to_jsonb(t)::text FROM {}.{} t "
                                    "ORDER BY pg_catalog.to_jsonb(t)::text"
                                ).format(
                                    sql.Identifier(SCHEMA), sql.Identifier(table)
                                )
                            ).fetchall()
                        ),
                    )
                    for table in tables
                )

        created_candidate = self._intake(
            self._packet(
                1,
                occurrence="request-hash-created",
                package_id="request-hash-created-package",
                candidate_changes={
                    "supplier_code_value": "REQUEST-HASH-CREATED",
                    "distributor_product_id_value": "REQUEST-HASH-CREATED",
                },
            )
        )
        rejected_candidate = self._intake(
            self._packet(
                1,
                occurrence="request-hash-rejected",
                package_id="request-hash-rejected-package",
                candidate_changes={
                    "proposed_variant_id": "2002",
                    "supplier_code_value": "REQUEST-HASH-REJECTED",
                    "distributor_product_id_value": "REQUEST-HASH-REJECTED",
                },
            )
        )
        deferred_candidate = self._intake(
            self._packet(
                0,
                occurrence="request-hash-deferred",
                package_id="request-hash-deferred-package",
            )
        )
        linked_offer = self._legacy_offer(sku="REQUEST-HASH-LINKED")
        linked_candidate = self._intake(
            self._packet(
                1,
                occurrence="request-hash-linked",
                package_id="request-hash-linked-package",
                candidate_changes={
                    "supplier_code_value": "REQUEST-HASH-LINKED",
                    "distributor_product_id_value": "REQUEST-HASH-LINKED",
                },
            )
        )

        outcomes: list[tuple[str, UUID, dict, dict, dict]] = []
        for label, candidate_id, action, link_kind, existing_offer_id in (
            (
                "created",
                created_candidate,
                "APPROVE_MAPPING",
                "CREATED_INACTIVE",
                None,
            ),
            ("rejected", rejected_candidate, "REJECT_MAPPING", None, None),
            ("deferred", deferred_candidate, "DEFER", None, None),
            (
                "linked",
                linked_candidate,
                "APPROVE_MAPPING",
                "LINKED_EXISTING",
                linked_offer,
            ),
        ):
            key = uuid4()
            reason = f"exact {label} replay"
            preview = self._preview_decision(
                candidate_id,
                action=action,
                reason=reason,
                key=key,
                offer_id=existing_offer_id,
                link_kind=link_kind,
            )
            kwargs = {
                "database_url": self.mapping_url,
                "candidate_id": candidate_id,
                "action": action,
                "reason": reason,
                "principal": self.principal,
                "decision_idempotency_key": key,
                "expected_preview_sha256": preview["preview_sha256"],
                "existing_offer_id": existing_offer_id,
                "offer_link_kind": link_kind,
            }
            first = execute_mapping_decision(**kwargs)
            state_after_first = authority_state()
            second = execute_mapping_decision(**kwargs)
            self.assertFalse(first["replayed"])
            self.assertTrue(second["replayed"])
            for field, value in first.items():
                if field != "replayed":
                    self.assertEqual(
                        mapping_service._jsonable(second[field]),
                        mapping_service._jsonable(value),
                    )
            self.assertEqual(authority_state(), state_after_first)
            outcomes.append((label, key, preview, first, kwargs))

        with psycopg.connect(self.mapping_url) as conn:
            column_types = dict(
                conn.execute(
                    "SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) "
                    "FROM pg_catalog.pg_attribute a "
                    "WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped",
                    (f"{SCHEMA}.supplier_mapping_decisions",),
                ).fetchall()
            )

            def changed_value(field: str, value, data_type: str):
                if data_type == "uuid":
                    return str(uuid4())
                if data_type == "jsonb":
                    return {"changed_confirmed_field": field}
                if data_type == "boolean":
                    return not bool(value)
                if data_type == "date":
                    return "2026-09-14"
                if data_type.startswith("timestamp"):
                    return "2026-09-14T00:00:00+00:00"
                if data_type in {"bigint", "integer", "smallint"}:
                    return int(value or 0) + 1
                if data_type.startswith("numeric"):
                    return str(float(value or 0) + 1)
                if data_type == "text":
                    return f"changed-{field}"
                self.fail(f"unhandled decision column type {field}={data_type}")

            ignored_request_fields = {
                "mapping_decision_id",
                "request_sha256",
                "canonical_payload",
                "payload_sha256",
                "decided_at",
                "decided_txid",
                "result_offer_id",
                "result_rejection_id",
            }
            for label, _key, _preview, result, _kwargs in outcomes:
                stored = conn.execute(
                    f"SELECT to_jsonb(d) FROM "
                    f"{SCHEMA}.supplier_mapping_decisions d "
                    "WHERE mapping_decision_id=%s",
                    (result["mapping_decision_id"],),
                ).fetchone()[0]
                original_request = mapping_service._composite_value(
                    conn,
                    table="supplier_mapping_decisions",
                    function="persistent_mapping_decision_request_sha256",
                    record=stored,
                )
                self.assertEqual(original_request, stored["request_sha256"])
                for field in sorted(stored):
                    if field in ignored_request_fields:
                        continue
                    changed = copy.deepcopy(stored)
                    changed[field] = changed_value(
                        field, stored[field], column_types[field]
                    )
                    with self.subTest(
                        disposition=label, bound_intent=field
                    ):
                        self.assertNotEqual(
                            mapping_service._composite_value(
                                conn,
                                table="supplier_mapping_decisions",
                                function=(
                                    "persistent_mapping_decision_request_sha256"
                                ),
                                record=changed,
                            ),
                            original_request,
                        )

                for generated_field in (
                    "mapping_decision_id",
                    "result_rejection_id",
                ):
                    changed = copy.deepcopy(stored)
                    changed[generated_field] = changed_value(
                        generated_field,
                        stored[generated_field],
                        column_types[generated_field],
                    )
                    self.assertEqual(
                        mapping_service._composite_value(
                            conn,
                            table="supplier_mapping_decisions",
                            function="persistent_mapping_decision_request_sha256",
                            record=changed,
                        ),
                        original_request,
                    )
                changed_offer_result = copy.deepcopy(stored)
                changed_offer_result["result_offer_id"] = int(
                    stored["result_offer_id"] or 0
                ) + 1000000
                changed_offer_request = mapping_service._composite_value(
                    conn,
                    table="supplier_mapping_decisions",
                    function="persistent_mapping_decision_request_sha256",
                    record=changed_offer_result,
                )
                if stored["offer_link_kind"] == "LINKED_EXISTING":
                    self.assertNotEqual(changed_offer_request, original_request)
                else:
                    self.assertEqual(changed_offer_request, original_request)

        first_label, first_key, first_preview, first_result, _first_kwargs = outcomes[0]
        self.assertEqual(first_label, "created")
        state_before_conflicts = authority_state()
        different_principal_ref = "synthetic:different-owner:01"
        different_role_ref = self.principal.role_ref
        different_principal = Principal(
            different_principal_ref,
            different_role_ref,
            mapping_service.authentication_context_sha256(
                principal_ref=different_principal_ref,
                role_ref=different_role_ref,
                session_ref="different-session",
            ),
        )
        conflicts = (
            {
                "candidate_id": rejected_candidate,
                "action": "APPROVE_MAPPING",
                "reason": "exact approve_mapping replay",
                "principal": self.principal,
                "expected_preview_sha256": first_preview["preview_sha256"],
                "offer_link_kind": "CREATED_INACTIVE",
            },
            {
                "candidate_id": created_candidate,
                "action": "REJECT_MAPPING",
                "reason": "exact approve_mapping replay",
                "principal": self.principal,
                "expected_preview_sha256": first_preview["preview_sha256"],
                "offer_link_kind": None,
            },
            {
                "candidate_id": created_candidate,
                "action": "APPROVE_MAPPING",
                "reason": "changed reason",
                "principal": self.principal,
                "expected_preview_sha256": first_preview["preview_sha256"],
                "offer_link_kind": "CREATED_INACTIVE",
            },
            {
                "candidate_id": created_candidate,
                "action": "APPROVE_MAPPING",
                "reason": "exact approve_mapping replay",
                "principal": different_principal,
                "expected_preview_sha256": first_preview["preview_sha256"],
                "offer_link_kind": "CREATED_INACTIVE",
            },
            {
                "candidate_id": created_candidate,
                "action": "APPROVE_MAPPING",
                "reason": "exact approve_mapping replay",
                "principal": self.principal,
                "expected_preview_sha256": "0" * 64,
                "offer_link_kind": "CREATED_INACTIVE",
            },
        )
        for changed in conflicts:
            with self.subTest(changed=changed), self.assertRaises(
                PersistentMappingError
            ) as conflict:
                execute_mapping_decision(
                    self.mapping_url,
                    decision_idempotency_key=first_key,
                    **changed,
                )
            self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")
            self.assertEqual(authority_state(), state_before_conflicts)

        extraneous_offer = self._legacy_offer(
            variant_id="3003", sku="EXTRANEOUS-REPLAY-OFFER"
        )
        state_with_extraneous_offer = authority_state()
        for label, key, _preview, _result, kwargs in outcomes:
            changed = dict(kwargs)
            if label == "linked":
                changed["existing_offer_id"] = extraneous_offer
            else:
                changed["existing_offer_id"] = linked_offer
            with self.subTest(disposition=label, changed="existing_offer_id"), (
                self.assertRaises(PersistentMappingError)
            ) as conflict:
                execute_mapping_decision(**changed)
            self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")
            self.assertEqual(authority_state(), state_with_extraneous_offer)
        self.assertEqual(first_result["action"], "APPROVE_MAPPING")

    def test_concurrent_mapping_same_key_replays_before_stale_head_and_conflicts_on_change(self):
        def authority_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        row[0]
                        for row in conn.execute(
                            f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                            "ORDER BY mapping_decision_id"
                        ).fetchall()
                    ),
                    tuple(
                        row[0]
                        for row in conn.execute(
                            f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                            "ORDER BY offer_id"
                        ).fetchall()
                    ),
                    tuple(
                        row[0]
                        for row in conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.mapping_rejections r "
                            "ORDER BY rejection_id"
                        ).fetchall()
                    ),
                )

        def race(
            *,
            key: UUID,
            requests: tuple[dict, dict],
        ) -> tuple[list[object], set[int], list[int]]:
            barrier = Barrier(2)
            contention_observed = Event()
            failed_lock_pids: set[int] = set()
            recorder_pids: list[int] = []
            expected_lock = (
                f"persistent-mapping:mapping-decision:idempotency:{key}"
            )
            original_try_lock = mapping_service._try_session_lock
            original_record = mapping_service.record_mapping_decision

            def observed_try_lock(conn, lock_name: str, deadline: float):
                if lock_name != expected_lock:
                    return original_try_lock(conn, lock_name, deadline)
                locked = conn.execute(
                    "SELECT pg_catalog.pg_try_advisory_lock("
                    "pg_catalog.hashtextextended(%s,0))",
                    (lock_name,),
                ).fetchone()[0]
                if locked:
                    return
                failed_lock_pids.add(conn.info.backend_pid)
                contention_observed.set()
                return original_try_lock(conn, lock_name, deadline)

            def hold_winner_until_real_contention(conn, **kwargs):
                recorder_pids.append(conn.info.backend_pid)
                if not contention_observed.wait(timeout=10):
                    raise AssertionError(
                        "the competing mapping request never failed the real "
                        "idempotency session lock"
                    )
                return original_record(conn, **kwargs)

            def invoke(request: dict) -> object:
                barrier.wait(timeout=10)
                try:
                    return execute_mapping_decision(
                        self.mapping_url,
                        decision_idempotency_key=key,
                        **request,
                    )
                except PersistentMappingError as exc:
                    return exc

            with (
                mock.patch.object(
                    mapping_service,
                    "_try_session_lock",
                    side_effect=observed_try_lock,
                ),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    side_effect=hold_winner_until_real_contention,
                ),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                futures = [pool.submit(invoke, request) for request in requests]
                results = [future.result() for future in futures]
            self.assertTrue(contention_observed.is_set())
            self.assertEqual(len(failed_lock_pids), 1)
            self.assertEqual(len(recorder_pids), 2)
            self.assertEqual(len(set(recorder_pids)), 2)
            return results, failed_lock_pids, recorder_pids

        candidate_id = self._intake(
            self._packet(
                1,
                occurrence="concurrent-mapping-identical",
                package_id="concurrent-mapping-identical-package",
                candidate_changes={
                    "supplier_code_value": "CONCURRENT-CREATED",
                    "distributor_product_id_value": "CONCURRENT-CREATED",
                },
            )
        )
        prior = self._decide(
            candidate_id,
            action="DEFER",
            reason="established tip before identical approval race",
        )
        key = uuid4()
        preview = self._preview_decision(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="concurrent identical created approval",
            key=key,
            link_kind="CREATED_INACTIVE",
        )
        self.assertEqual(
            str(preview["supersedes_mapping_decision_id"]),
            str(prior["mapping_decision_id"]),
        )
        request = {
            "candidate_id": candidate_id,
            "action": "APPROVE_MAPPING",
            "reason": "concurrent identical created approval",
            "principal": self.principal,
            "expected_preview_sha256": preview["preview_sha256"],
            "offer_link_kind": "CREATED_INACTIVE",
        }
        before_identical = authority_state()
        results, _failed_pids, _recorder_pids = race(
            key=key,
            requests=(request, copy.deepcopy(request)),
        )
        self.assertTrue(all(isinstance(item, dict) for item in results))
        self.assertEqual(sum(not item["replayed"] for item in results), 1)
        self.assertEqual(
            {str(item["mapping_decision_id"]) for item in results},
            {str(results[0]["mapping_decision_id"])},
        )
        self.assertEqual(
            {item["request_sha256"] for item in results},
            {results[0]["request_sha256"]},
        )
        after_identical = authority_state()
        self.assertEqual(len(after_identical[0]), len(before_identical[0]) + 1)
        self.assertEqual(len(after_identical[1]), len(before_identical[1]) + 1)
        self.assertEqual(after_identical[2], before_identical[2])
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=%s",
                    (candidate_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM "
                    f"{SCHEMA}.v_effective_supplier_mapping_decisions "
                    "WHERE candidate_id=%s AND action='APPROVE_MAPPING'",
                    (candidate_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=%s AND action='APPROVE_MAPPING'",
                    (candidate_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_offers o "
                    f"JOIN {SCHEMA}.supplier_mapping_decisions d "
                    "ON d.result_offer_id=o.offer_id "
                    "WHERE d.candidate_id=%s AND d.offer_link_kind='CREATED_INACTIVE'",
                    (candidate_id,),
                ).fetchone()[0],
                1,
            )

        changed_candidate = self._intake(
            self._packet(
                1,
                occurrence="concurrent-mapping-conflict",
                package_id="concurrent-mapping-conflict-package",
                candidate_changes={
                    "proposed_variant_id": "2002",
                    "supplier_code_value": "CONCURRENT-REJECTION",
                    "distributor_product_id_value": "CONCURRENT-REJECTION",
                },
            )
        )
        changed_prior = self._decide(
            changed_candidate,
            action="DEFER",
            reason="established tip before changed rejection race",
        )
        changed_key = uuid4()
        canonical_preview = self._preview_decision(
            changed_candidate,
            action="REJECT_MAPPING",
            reason="concurrent canonical rejection",
            key=changed_key,
        )
        changed_preview = self._preview_decision(
            changed_candidate,
            action="REJECT_MAPPING",
            reason="concurrent changed rejection",
            key=changed_key,
        )
        self.assertEqual(
            str(canonical_preview["supersedes_mapping_decision_id"]),
            str(changed_prior["mapping_decision_id"]),
        )
        before_changed = authority_state()
        conflicting, _failed_pids, _recorder_pids = race(
            key=changed_key,
            requests=(
                {
                    "candidate_id": changed_candidate,
                    "action": "REJECT_MAPPING",
                    "reason": "concurrent canonical rejection",
                    "principal": self.principal,
                    "expected_preview_sha256": canonical_preview[
                        "preview_sha256"
                    ],
                },
                {
                    "candidate_id": changed_candidate,
                    "action": "REJECT_MAPPING",
                    "reason": "concurrent changed rejection",
                    "principal": self.principal,
                    "expected_preview_sha256": changed_preview[
                        "preview_sha256"
                    ],
                },
            ),
        )
        winners = [item for item in conflicting if isinstance(item, dict)]
        losers = [
            item for item in conflicting if isinstance(item, PersistentMappingError)
        ]
        self.assertEqual(len(winners), 1, conflicting)
        self.assertEqual(len(losers), 1, conflicting)
        self.assertEqual(losers[0].code, "IDEMPOTENCY_CONFLICT")
        after_changed = authority_state()
        self.assertEqual(len(after_changed[0]), len(before_changed[0]) + 1)
        self.assertEqual(after_changed[1], before_changed[1])
        self.assertEqual(len(after_changed[2]), len(before_changed[2]) + 1)
        with psycopg.connect(self.mapping_url) as conn:
            stored = conn.execute(
                f"SELECT mapping_decision_id,reason,request_sha256,result_rejection_id "
                f"FROM {SCHEMA}.supplier_mapping_decisions "
                "WHERE decision_idempotency_key=%s",
                (changed_key,),
            ).fetchone()
        self.assertEqual(str(stored[0]), str(winners[0]["mapping_decision_id"]))
        self.assertEqual(stored[1], winners[0]["reason"])
        self.assertEqual(stored[2], winners[0]["request_sha256"])
        self.assertEqual(stored[3], winners[0]["result_rejection_id"])
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE candidate_id=%s",
                    (changed_candidate,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.mapping_rejections "
                    "WHERE rejection_id=%s",
                    (winners[0]["result_rejection_id"],),
                ).fetchone()[0],
                1,
            )

    def test_mapping_rejects_stale_preview_and_stale_or_forked_prior(self):
        def mapping_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    self._counts(),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(c) FROM "
                            f"{SCHEMA}.supplier_mapping_review_candidates c "
                            "ORDER BY candidate_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(d) FROM "
                            f"{SCHEMA}.supplier_mapping_decisions d "
                            "ORDER BY mapping_decision_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.mapping_rejections r "
                            "ORDER BY rejection_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(v) FROM {SCHEMA}.variants v "
                            "ORDER BY variant_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(v) FROM {SCHEMA}.vendors v "
                            "ORDER BY vendor_id"
                        ).fetchall()
                    ),
                )

        original_record = mapping_service.record_mapping_decision
        for stale_kind in ("catalog", "vendor", "rejection", "candidate"):
            supplier_code = f"STALE-{stale_kind.upper()}"
            offer = self._legacy_offer(sku=supplier_code)
            candidate_id = self._intake(
                self._packet(
                    1,
                    occurrence=f"stale-preview-{stale_kind}",
                    package_id=f"stale-preview-{stale_kind}-package",
                    candidate_changes={
                        "supplier_code_value": supplier_code,
                        "distributor_product_id_value": supplier_code,
                    },
                )
            )
            key = uuid4()
            reason = f"stale {stale_kind} preview"
            preview = self._preview_decision(
                candidate_id,
                action="APPROVE_MAPPING",
                reason=reason,
                key=key,
                offer_id=offer,
                link_kind="LINKED_EXISTING",
            )
            candidate_sha_before: str | None = None
            if stale_kind == "rejection":
                blocker = self._intake(
                    self._packet(
                        1,
                        occurrence="stale-preview-rejection-blocker",
                        package_id="stale-preview-rejection-blocker-package",
                        candidate_changes={
                            "supplier_code_value": supplier_code,
                            "distributor_product_id_value": supplier_code,
                        },
                    )
                )
                self._decide(
                    blocker,
                    action="REJECT_MAPPING",
                    reason="change rejection memory after preview",
                )
            else:
                with psycopg.connect(self.mapping_url) as conn:
                    if stale_kind == "catalog":
                        conn.execute(
                            f"UPDATE {SCHEMA}.variants "
                            "SET source_snapshot='changed after mapping preview' "
                            "WHERE variant_id='1001'"
                        )
                    elif stale_kind == "vendor":
                        conn.execute(
                            f"UPDATE {SCHEMA}.vendors "
                            "SET updated_at=updated_at + interval '1 second' "
                            "WHERE vendor_id=%s",
                            (VENDOR_ID,),
                        )
                    else:
                        candidate_sha_before = str(
                            conn.execute(
                                f"SELECT candidate_sha256 FROM "
                                f"{SCHEMA}.supplier_mapping_review_candidates "
                                "WHERE candidate_id=%s",
                                (candidate_id,),
                            ).fetchone()[0]
                        )
                        conn.execute(
                            f"ALTER TABLE {SCHEMA}."
                            "supplier_mapping_review_candidates DISABLE TRIGGER "
                            "trg_immutable_mapping_review_candidates"
                        )
                        conn.execute(
                            f"UPDATE {SCHEMA}.supplier_mapping_review_candidates "
                            "SET candidate_sha256=%s WHERE candidate_id=%s",
                            ("f" * 64, candidate_id),
                        )
                        conn.execute(
                            f"ALTER TABLE {SCHEMA}."
                            "supplier_mapping_review_candidates ENABLE TRIGGER "
                            "trg_immutable_mapping_review_candidates"
                        )
            before = mapping_state()
            with (
                self.subTest(stale=stale_kind),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    wraps=original_record,
                ) as record_call,
                self.assertRaisesRegex(
                    PersistentMappingError, "mapping preview is stale"
                ) as refused,
            ):
                execute_mapping_decision(
                    self.mapping_url,
                    candidate_id=candidate_id,
                    action="APPROVE_MAPPING",
                    reason=reason,
                    principal=self.principal,
                    decision_idempotency_key=key,
                    expected_preview_sha256=preview["preview_sha256"],
                    existing_offer_id=offer,
                    offer_link_kind="LINKED_EXISTING",
                )
            self.assertEqual(refused.exception.code, "MAPPING_REFUSED")
            self.assertEqual(record_call.call_count, 1)
            self.assertEqual(mapping_state(), before)
            if candidate_sha_before is not None:
                with psycopg.connect(self.mapping_url) as conn:
                    conn.execute(
                        f"ALTER TABLE {SCHEMA}."
                        "supplier_mapping_review_candidates DISABLE TRIGGER "
                        "trg_immutable_mapping_review_candidates"
                    )
                    conn.execute(
                        f"UPDATE {SCHEMA}.supplier_mapping_review_candidates "
                        "SET candidate_sha256=%s WHERE candidate_id=%s",
                        (candidate_sha_before, candidate_id),
                    )
                    conn.execute(
                        f"ALTER TABLE {SCHEMA}."
                        "supplier_mapping_review_candidates ENABLE TRIGGER "
                        "trg_immutable_mapping_review_candidates"
                    )

        fork_candidate = self._intake(
            self._packet(
                0,
                occurrence="forked-prior",
                package_id="forked-prior-package",
            )
        )
        root = self._decide(
            fork_candidate,
            action="DEFER",
            reason="root mapping decision",
        )
        winning_key = uuid4()
        losing_key = uuid4()
        winning_preview = self._preview_decision(
            fork_candidate,
            action="DEFER",
            reason="winning successor",
            key=winning_key,
        )
        losing_preview = self._preview_decision(
            fork_candidate,
            action="DEFER",
            reason="forked successor",
            key=losing_key,
        )
        self.assertEqual(
            str(winning_preview["supersedes_mapping_decision_id"]),
            str(root["mapping_decision_id"]),
        )
        self.assertEqual(
            str(losing_preview["supersedes_mapping_decision_id"]),
            str(root["mapping_decision_id"]),
        )
        winner = execute_mapping_decision(
            self.mapping_url,
            candidate_id=fork_candidate,
            action="DEFER",
            reason="winning successor",
            principal=self.principal,
            decision_idempotency_key=winning_key,
            expected_preview_sha256=winning_preview["preview_sha256"],
        )
        forked_before = mapping_state()
        with mock.patch.object(
            mapping_service,
            "record_mapping_decision",
            wraps=original_record,
        ) as record_call, self.assertRaisesRegex(
            PersistentMappingError, "mapping preview is stale"
        ) as stale_prior:
            execute_mapping_decision(
                self.mapping_url,
                candidate_id=fork_candidate,
                action="DEFER",
                reason="forked successor",
                principal=self.principal,
                decision_idempotency_key=losing_key,
                expected_preview_sha256=losing_preview["preview_sha256"],
            )
        self.assertEqual(stale_prior.exception.code, "MAPPING_REFUSED")
        self.assertEqual(record_call.call_count, 1)
        self.assertEqual(mapping_state(), forked_before)
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            mapping_service._set_human_context(
                conn,
                self.principal,
                action="SUPPLIER_MAPPING_DECIDE",
                capability="human_mapping_writes_enabled",
            )
            with self.assertRaisesRegex(
                psycopg.Error, "stale or wrong-scope prior mapping decision"
            ):
                mapping_service._insert_record(
                    conn,
                    table="supplier_mapping_decisions",
                    record=losing_preview,
                )
            conn.rollback()
        self.assertEqual(mapping_state(), forked_before)
        self.assertNotEqual(
            str(winner["mapping_decision_id"]),
            str(root["mapping_decision_id"]),
        )

    def test_mapping_requires_server_named_human_context(self):
        candidate_id = self._intake(self._packet(0))
        baseline = self._counts()

        from procurement_os import config as mapping_config

        disabled_contexts = (
            ("absent synthetic write overlay", None, "AUTOMATED_TEST"),
            ("false synthetic write overlay", "0", "AUTOMATED_TEST"),
            ("absent runtime mode", "1", None),
        )
        for label, enabled, mode in disabled_contexts:
            with (
                self.subTest(case=label),
                mock.patch.dict(os.environ, {}, clear=False),
                mock.patch.object(
                    mapping_config,
                    "load_rules",
                    side_effect=AssertionError("disabled gate reached configuration storage"),
                ) as load_rules,
                mock.patch.object(mapping_service.psycopg, "connect") as connect,
            ):
                if enabled is None:
                    os.environ.pop("BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO", None)
                else:
                    os.environ["BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO"] = enabled
                if mode is None:
                    os.environ.pop("BUFFALO_RUNTIME_MODE", None)
                else:
                    os.environ["BUFFALO_RUNTIME_MODE"] = mode
                with self.assertRaisesRegex(
                    PersistentMappingError,
                    "synthetic persistent mapping capability is disabled",
                ):
                    execute_mapping_decision(
                        self.mapping_url,
                        candidate_id=candidate_id,
                        action="DEFER",
                        reason="disabled before storage",
                        principal=self.principal,
                        decision_idempotency_key=uuid4(),
                        expected_preview_sha256="0" * 64,
                    )
                load_rules.assert_not_called()
                connect.assert_not_called()
            self.assertEqual(self._counts(), baseline)

        unsafe_policies = (
            {},
            {
                "persistent_mapping": {
                    "human_mapping_writes_enabled": True,
                    "recommendation_cutover_enabled": False,
                    "offer_activation_enabled": False,
                }
            },
        )
        for policy in unsafe_policies:
            with (
                self.subTest(case="missing or enabled repository policy", policy=policy),
                mock.patch.object(mapping_config, "load_rules", return_value=policy),
                mock.patch.object(mapping_service.psycopg, "connect") as connect,
                self.assertRaisesRegex(
                    PersistentMappingError,
                    "repository persistent mapping policy differs",
                ),
            ):
                execute_mapping_decision(
                    self.mapping_url,
                    candidate_id=candidate_id,
                    action="DEFER",
                    reason="unsafe repository policy",
                    principal=self.principal,
                    decision_idempotency_key=uuid4(),
                    expected_preview_sha256="0" * 64,
                )
            connect.assert_not_called()
            self.assertEqual(self._counts(), baseline)

        malformed = Principal("client actor", "procurement.mapping.approve", "bad")
        with (
            mock.patch.object(mapping_service.psycopg, "connect") as connect,
            self.assertRaisesRegex(
                PersistentMappingError, "authentication context hash is malformed"
            ),
        ):
            execute_mapping_decision(
                self.mapping_url,
                candidate_id=candidate_id,
                action="DEFER",
                reason="forged",
                principal=malformed,
                decision_idempotency_key=uuid4(),
                expected_preview_sha256="0" * 64,
            )
        connect.assert_not_called()
        self.assertEqual(self._counts(), baseline)

        decision_key = uuid4()
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            valid_record = mapping_service._mapping_decision_record(
                conn,
                candidate_id=candidate_id,
                action="DEFER",
                reason="server-named exact confirmation",
                principal=self.principal,
                decision_idempotency_key=decision_key,
                existing_offer_id=None,
                offer_link_kind=None,
            )
            conn.rollback()

        decision_omit = (
            "mapping_decision_id",
            "decision_idempotency_key",
            "canonical_payload",
            "payload_sha256",
            "decided_at",
            "decided_txid",
        )

        def bind_outer_integrity(
            conn,
            source: dict,
            *,
            refresh_confirmation: bool = False,
        ) -> dict:
            record = copy.deepcopy(source)
            if refresh_confirmation:
                record["confirmation_sha256"] = mapping_service._composite_value(
                    conn,
                    table="supplier_mapping_decisions",
                    function="persistent_mapping_decision_confirmation_sha256",
                    record=record,
                )
            record["request_sha256"] = mapping_service._composite_value(
                conn,
                table="supplier_mapping_decisions",
                function="persistent_mapping_decision_request_sha256",
                record=record,
            )
            payload, payload_sha256 = mapping_service._project_record(
                conn,
                table="supplier_mapping_decisions",
                record=record,
                omit=decision_omit,
            )
            record["canonical_payload"] = payload
            record["payload_sha256"] = payload_sha256
            return record

        def assert_direct_insert_refused(
            label: str,
            source: dict,
            expected_error: str,
            *,
            configure_context=None,
        ) -> None:
            with self.subTest(case=label), psycopg.connect(self.mapping_url) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                if configure_context is None:
                    mapping_service._set_human_context(
                        conn,
                        self.principal,
                        action="SUPPLIER_MAPPING_DECIDE",
                        capability="human_mapping_writes_enabled",
                    )
                else:
                    configure_context(conn)
                with self.assertRaisesRegex(psycopg.Error, expected_error):
                    mapping_service._insert_record(
                        conn,
                        table="supplier_mapping_decisions",
                        record=source,
                    )
                conn.rollback()
            self.assertEqual(self._counts(), baseline)

        for field in (
            "human_authn_context_sha256",
            "preview_sha256",
            "confirmation_sha256",
        ):
            for value in (None, "not-a-hash"):
                with psycopg.connect(self.mapping_url) as conn:
                    bad = copy.deepcopy(valid_record)
                    bad[field] = value
                    bad = bind_outer_integrity(
                        conn,
                        bad,
                        refresh_confirmation=field == "preview_sha256",
                    )
                    conn.rollback()
                assert_direct_insert_refused(
                    f"{field}={value!r}",
                    bad,
                    (
                        "verified named-human authorization context is absent or differs"
                        if field == "human_authn_context_sha256"
                        else "mapping preview or separate confirmation is not bound"
                    ),
                )

        client_values = (
            ("human_principal_ref", "client actor"),
            ("human_role_ref", "shared-review-token"),
        )
        for field, value in client_values:
            with psycopg.connect(self.mapping_url) as conn:
                bad = copy.deepcopy(valid_record)
                bad[field] = value
                bad = bind_outer_integrity(
                    conn, bad, refresh_confirmation=True
                )
                conn.rollback()
            assert_direct_insert_refused(
                f"client-supplied {field}",
                bad,
                "verified named-human authorization context is absent or differs",
            )

        def no_context(_conn) -> None:
            return None

        def capability_only(conn) -> None:
            conn.execute(
                "SELECT pg_catalog.set_config(%s,%s,true)",
                ("procurement.enabled_capability", "human_mapping_writes_enabled"),
            )

        def exact_then_override(key: str, value: str):
            def configure(conn) -> None:
                mapping_service._set_human_context(
                    conn,
                    self.principal,
                    action="SUPPLIER_MAPPING_DECIDE",
                    capability="human_mapping_writes_enabled",
                )
                conn.execute(
                    "SELECT pg_catalog.set_config(%s,%s,true)", (key, value)
                )

            return configure

        guc_cases = (
            (
                "all authorization GUCs absent",
                no_context,
                "required server-enabled persistent-mapping capability is absent",
            ),
            (
                "human tuple absent with capability present",
                capability_only,
                "verified named-human authorization context is absent or differs",
            ),
            (
                "wrong capability",
                exact_then_override(
                    "procurement.enabled_capability", "review_intake_writes_enabled"
                ),
                "required server-enabled persistent-mapping capability is absent",
            ),
            (
                "forged principal kind",
                exact_then_override("procurement.principal_kind", "SERVICE"),
                "verified named-human authorization context is absent or differs",
            ),
            (
                "client actor GUC",
                exact_then_override("procurement.principal_ref", "client actor"),
                "verified named-human authorization context is absent or differs",
            ),
            (
                "wrong authorized role",
                exact_then_override(
                    "procurement.authorized_role_ref", "procurement.offer.select"
                ),
                "verified named-human authorization context is absent or differs",
            ),
            (
                "forged authentication context",
                exact_then_override(
                    "procurement.authn_context_sha256", "f" * 64
                ),
                "verified named-human authorization context is absent or differs",
            ),
            (
                "wrong authorized action",
                exact_then_override(
                    "procurement.authorized_action", "ROUTINE_OFFER_SELECT"
                ),
                "verified named-human authorization context is absent or differs",
            ),
        )
        for label, configure_context, expected_error in guc_cases:
            assert_direct_insert_refused(
                label,
                valid_record,
                expected_error,
                configure_context=configure_context,
            )

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
        self.assertEqual(self._counts(), baseline)

        from fastapi.testclient import TestClient
        from procurement_os import api, local_access

        with TemporaryDirectory(prefix="buffalo-mapping-named-session-") as temporary:
            auth_root = Path(temporary)
            auth_root.chmod(0o700)
            secret = "fabricated-mapping-local-secret-01"
            secret_file = auth_root / "local-auth.secret"
            secret_file.write_text(secret + "\n", encoding="utf-8")
            secret_file.chmod(0o600)
            port = 18766
            origin = f"http://127.0.0.1:{port}"
            shared_token = "fabricated-shared-review-token"
            environment = {
                "BUFFALO_RUNTIME_MODE": "AUTOMATED_TEST",
                "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
                "BUFFALO_LOCAL_PORT": str(port),
                "BUFFALO_LOCAL_AUTH_SECRET_FILE": str(secret_file),
                "BUFFALO_LOCAL_PRINCIPAL_REF": "synthetic:matrix-server-owner:01",
                "BUFFALO_LOCAL_ROLE_REF": "LOCAL_TEST_OWNER",
                "DATABASE_URL": self.mapping_url,
                "RECONCILIATION_REVIEW_TOKEN": shared_token,
            }
            local_access.clear_local_sessions()
            self.addCleanup(local_access.clear_local_sessions)
            with mock.patch.dict(os.environ, environment, clear=False):
                client = TestClient(
                    api.app,
                    base_url=origin,
                    headers={"Origin": origin},
                    client=("127.0.0.1", 50025),
                )
                self.addCleanup(client.close)
                client.cookies.set(local_access.SESSION_COOKIE, shared_token)
                hostile_form = {
                    "action": "DEFER",
                    "reason": "server ignores hostile identity fields",
                    "idempotency_key": str(uuid4()),
                    "actor": "client actor",
                    "review_token": shared_token,
                    "procurement.principal_kind": "SERVICE",
                    "procurement.principal_ref": "client actor",
                    "procurement.enabled_capability": "offer_activation_enabled",
                }
                with (
                    mock.patch.object(api, "_db_conn") as db_connection,
                    mock.patch.object(api, "preview_mapping_decision") as preview,
                    mock.patch.object(api, "execute_mapping_decision") as execute,
                ):
                    denied = client.post(
                        f"/supplier-mapping/{candidate_id}/decision",
                        data=hostile_form,
                        follow_redirects=False,
                    )
                self.assertEqual(denied.status_code, 401)
                db_connection.assert_not_called()
                preview.assert_not_called()
                execute.assert_not_called()
                self.assertEqual(self._counts(), baseline)

                client.cookies.clear()
                login = client.post(
                    "/auth/login",
                    data={"secret": secret},
                    follow_redirects=False,
                )
                self.assertEqual(login.status_code, 303)
                token = client.cookies.get(local_access.SESSION_COOKIE)
                session = local_access._session(
                    token, local_access.runtime_config()
                )
                self.assertIsNotNone(session)
                expected_authn_context_sha256 = (
                    mapping_service.authentication_context_sha256(
                        principal_ref="synthetic:matrix-server-owner:01",
                        role_ref="procurement.mapping.approve",
                        session_ref=session.session_ref,
                    )
                )
                route_key = uuid4()
                hostile_form["idempotency_key"] = str(route_key)
                preview_response = client.post(
                    f"/supplier-mapping/{candidate_id}/decision",
                    data=hostile_form,
                    follow_redirects=False,
                )
                self.assertEqual(preview_response.status_code, 200)
                match = re.search(
                    r"name='expected_preview_sha256' value='([0-9a-f]{64})'",
                    preview_response.text,
                )
                self.assertIsNotNone(match)
                preview_sha256 = match.group(1)
                confirmation_form = {
                    **hostile_form,
                    "expected_preview_sha256": preview_sha256,
                    "confirm": "CONFIRM",
                }
                confirmed = client.post(
                    f"/supplier-mapping/{candidate_id}/decision",
                    data=confirmation_form,
                    follow_redirects=False,
                )
                self.assertEqual(confirmed.status_code, 303, confirmed.text)

        with psycopg.connect(self.mapping_url) as conn:
            stored = conn.execute(
                f"SELECT human_principal_ref,human_role_ref,"
                f"human_authn_context_sha256,preview_sha256,confirmation_sha256,"
                f"action,reason FROM {SCHEMA}.supplier_mapping_decisions "
                "WHERE decision_idempotency_key=%s",
                (route_key,),
            ).fetchone()
        self.assertIsNotNone(stored)
        self.assertEqual(
            stored[:4],
            (
                "synthetic:matrix-server-owner:01",
                "procurement.mapping.approve",
                expected_authn_context_sha256,
                preview_sha256,
            ),
        )
        self.assertRegex(stored[4], r"^[0-9a-f]{64}$")
        self.assertEqual(
            stored[5:],
            ("DEFER", "server ignores hostile identity fields"),
        )
        self.assertNotIn("client actor", stored[:3])
        self.assertNotIn("fabricated-shared-review-token", stored[:3])
        after = self._counts()
        self.assertEqual(after[2], baseline[2] + 1)
        self.assertEqual(after[:2], baseline[:2])
        self.assertEqual(after[3:], baseline[3:])

    def test_role_topology_rejects_direct_transitive_inherit_set_and_mixed_owner_paths(self):
        expected_pair_bytes = (
            b'[{"current_user":"qa_mapping_owner",'
            b'"session_user":"qa_release_login"}]'
        )
        expected_config_bytes = (
            b'{"allowed_pairs":[{"current_user":"qa_mapping_owner",'
            b'"session_user":"qa_release_login"}],'
            b'"contract":"BUFFALO_PERSISTENT_MAPPING_MAINTENANCE_IDENTITY_V1",'
            b'"family":"persistent-mapping-foundation",'
            b'"release":"v1-shadow-only","target_schema":"qa_mapping_test"}\n'
        )
        self.assertEqual(
            hashlib.sha256(expected_pair_bytes).hexdigest(),
            "2d08568ea5df7ef3ee383381ae2d952c197ac87f8d8fffd4c1af877ca05ef69c",
        )
        self.assertEqual(
            hashlib.sha256(expected_config_bytes).hexdigest(),
            "ab773e859feee527bba01c4ae3193fece3258bbe471d92595302531cbc270450",
        )
        config_path = (
            DB_DIR.parent / apply_schema.MAPPING_RELEASE.maintenance_identity_config_ref
        )
        config_bytes = config_path.read_bytes()
        config = json.loads(config_bytes)
        pair_bytes = apply_schema._canonical_json(config["allowed_pairs"])
        self.assertEqual(config_bytes, expected_config_bytes)
        self.assertEqual(pair_bytes, expected_pair_bytes)
        self.assertEqual(
            config,
            {
                "allowed_pairs": [
                    {
                        "current_user": "qa_mapping_owner",
                        "session_user": "qa_release_login",
                    }
                ],
                "contract": "BUFFALO_PERSISTENT_MAPPING_MAINTENANCE_IDENTITY_V1",
                "family": "persistent-mapping-foundation",
                "release": "v1-shadow-only",
                "target_schema": "qa_mapping_test",
            },
        )
        self.assertEqual(
            apply_schema._maintenance_binding(DB_DIR),
            (("qa_release_login", "qa_mapping_owner"),),
        )
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

        with TemporaryDirectory(
            prefix="buffalo-mapping-role-config-refusal-"
        ) as temporary:
            refusal_root = Path(temporary)
            refusal_db = refusal_root / "db"
            refusal_config = refusal_root / "config"
            refusal_db.mkdir()
            refusal_config.mkdir()
            for name in apply_schema.MIGRATION_ORDER:
                shutil.copyfile(DB_DIR / name, refusal_db / name)
            shutil.copyfile(
                DB_DIR
                / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                refusal_db
                / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
            )

            base_config = json.loads(expected_config_bytes)

            def encoded(value: object) -> bytes:
                return apply_schema._canonical_json(value) + b"\n"

            wrong_key = copy.deepcopy(base_config)
            wrong_key["families"] = wrong_key.pop("family")
            missing_key = copy.deepcopy(base_config)
            missing_key.pop("target_schema")
            extra_key = {**base_config, "caller_override": True}
            wrong_contract = {
                **base_config,
                "contract": "BUFFALO_FORGED_MAINTENANCE_IDENTITY",
            }
            wrong_release = {**base_config, "release": "forged-release"}
            wrong_schema = {**base_config, "target_schema": "public"}
            tuple_shaped = {
                **base_config,
                "allowed_pairs": [["qa_release_login", "qa_mapping_owner"]],
            }
            empty_pairs = {**base_config, "allowed_pairs": []}
            duplicate_pairs = {
                **base_config,
                "allowed_pairs": [
                    dict(base_config["allowed_pairs"][0]),
                    dict(base_config["allowed_pairs"][0]),
                ],
            }
            reversed_pair = {
                **base_config,
                "allowed_pairs": [
                    {
                        "current_user": "qa_release_login",
                        "session_user": "qa_mapping_owner",
                    }
                ],
            }
            altered_session = {
                **base_config,
                "allowed_pairs": [
                    {
                        "current_user": "qa_mapping_owner",
                        "session_user": "qa_application_login",
                    }
                ],
            }
            altered_current = {
                **base_config,
                "allowed_pairs": [
                    {
                        "current_user": "qa_application_owner",
                        "session_user": "qa_release_login",
                    }
                ],
            }
            noncanonical = json.dumps(base_config, indent=2).encode("utf-8") + b"\n"
            refusal_cases = (
                ("missing reference", None, None, None),
                ("tuple-shaped pair", encoded(tuple_shaped), None, None),
                ("missing key", encoded(missing_key), None, None),
                ("extra key", encoded(extra_key), None, None),
                ("wrong key", encoded(wrong_key), None, None),
                ("wrong contract", encoded(wrong_contract), None, None),
                ("wrong release", encoded(wrong_release), None, None),
                ("wrong schema", encoded(wrong_schema), None, None),
                ("noncanonical bytes", noncanonical, None, None),
                (
                    "full config digest mismatch",
                    expected_config_bytes,
                    "0" * 64,
                    None,
                ),
                (
                    "pair digest mismatch",
                    expected_config_bytes,
                    None,
                    "0" * 64,
                ),
                ("empty pair set", encoded(empty_pairs), None, None),
                ("duplicate pair set", encoded(duplicate_pairs), None, None),
                ("reversed pair", encoded(reversed_pair), None, None),
                ("altered session role", encoded(altered_session), None, None),
                ("altered current role", encoded(altered_current), None, None),
            )
            for index, (label, raw, full_override, pair_override) in enumerate(
                refusal_cases, 1
            ):
                config_ref = f"config/row26-refusal-{index}.json"
                if raw is not None:
                    (refusal_root / config_ref).write_bytes(raw)
                    try:
                        parsed_value = json.loads(raw)
                        parsed_pairs = parsed_value.get("allowed_pairs", [])
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        parsed_pairs = []
                else:
                    parsed_pairs = []
                candidate_release = replace(
                    apply_schema.MAPPING_RELEASE,
                    maintenance_identity_config_ref=config_ref,
                    maintenance_identity_config_sha256=(
                        full_override
                        or hashlib.sha256(raw or b"").hexdigest()
                    ),
                    maintenance_identity_pairs_sha256=(
                        pair_override
                        or hashlib.sha256(
                            apply_schema._canonical_json(parsed_pairs)
                        ).hexdigest()
                    ),
                )
                with (
                    self.subTest(config_refusal=label),
                    mock.patch.object(
                        apply_schema,
                        "PERSISTENT_MAPPING_RELEASE_MANIFEST",
                        (candidate_release,),
                    ),
                    psycopg.connect(self.mapping_url, autocommit=True) as conn,
                ):
                    before_refusal = self._schema_snapshot(conn)
                    with (
                        self.assertRaises((RuntimeError, OSError)),
                        conn.transaction(),
                    ):
                        apply_schema._verify_or_apply_mapping_release(
                            conn, refusal_db, candidate_release
                        )
                    self.assertEqual(
                        self._schema_snapshot(conn), before_refusal
                    )

        application_login = "qa_mapping_application_login"
        application_owner = "qa_mapping_application_owner"

        def cleanup_application_roles() -> None:
            with self._admin_connection() as admin:
                roles = {
                    str(row[0])
                    for row in admin.execute(
                        "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)",
                        ([application_login, application_owner],),
                    ).fetchall()
                }
                if application_login in roles:
                    admin.execute(
                        sql.SQL("REVOKE qa_mapping_owner FROM {}").format(
                            sql.Identifier(application_login)
                        )
                    )
                if application_owner in roles:
                    for member in (application_login, "qa_release_login"):
                        if member == application_login and member not in roles:
                            continue
                        admin.execute(
                            sql.SQL("REVOKE {} FROM {}").format(
                                sql.Identifier(application_owner),
                                sql.Identifier(member),
                            )
                        )
                for role in (application_login, application_owner):
                    if role in roles:
                        admin.execute(
                            sql.SQL("DROP OWNED BY {}").format(
                                sql.Identifier(role)
                            )
                        )
                if application_login in roles:
                    admin.execute(
                        sql.SQL("DROP ROLE {}").format(
                            sql.Identifier(application_login)
                        )
                    )
                if application_owner in roles:
                    admin.execute(
                        sql.SQL("DROP ROLE {}").format(
                            sql.Identifier(application_owner)
                        )
                    )

        self.addCleanup(cleanup_application_roles)
        with self._admin_connection() as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(application_login))
            )
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(application_owner))
            )
            admin.execute(
                sql.SQL(
                    "GRANT qa_mapping_owner TO {} "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                ).format(sql.Identifier(application_login))
            )

        parsed_url = urlparse(self.mapping_url)
        application_url = self.mapping_url.replace(
            f"{parsed_url.username}@", f"{application_login}@", 1
        )
        forged_meta = {
            "persistent_mapping_maintenance_session_user": "qa_release_login",
            "persistent_mapping_maintenance_current_user": "qa_mapping_owner",
            "persistent_mapping_maintenance_config_sha256": (
                apply_schema.MAPPING_RELEASE.maintenance_identity_config_sha256
            ),
            "persistent_mapping_maintenance_pairs_sha256": (
                apply_schema.MAPPING_RELEASE.maintenance_identity_pairs_sha256
            ),
        }
        with psycopg.connect(self.mapping_url) as owner_connection:
            for item in forged_meta.items():
                owner_connection.execute(
                    f"INSERT INTO {SCHEMA}.meta(key,value) VALUES(%s,%s)",
                    item,
                )
        with psycopg.connect(application_url, autocommit=True) as application:
            for key, value in (
                ("procurement.maintenance_session_user", "qa_release_login"),
                ("procurement.maintenance_current_user", "qa_mapping_owner"),
                (
                    "procurement.maintenance_identity_config_sha256",
                    apply_schema.MAPPING_RELEASE.maintenance_identity_config_sha256,
                ),
                (
                    "procurement.maintenance_identity_pairs_sha256",
                    apply_schema.MAPPING_RELEASE.maintenance_identity_pairs_sha256,
                ),
            ):
                application.execute(
                    "SELECT pg_catalog.set_config(%s,%s,false)", (key, value)
                )
            application_state = self._schema_snapshot(application)
            with self.assertRaisesRegex(RuntimeError, "pair is not approved"):
                apply_schema.apply_schema_connection(
                    application, DB_DIR, include_persistent_mapping=True
                )
            self.assertEqual(
                self._schema_snapshot(application), application_state
            )
            with (
                self.assertRaisesRegex(psycopg.Error, "pair is not approved"),
                application.transaction(),
            ):
                application.execute(
                    f"SELECT {SCHEMA}."
                    "assert_persistent_mapping_foundation_contract()"
                )
            self.assertEqual(
                self._schema_snapshot(application), application_state
            )
        with psycopg.connect(self.mapping_url) as owner_connection:
            owner_connection.execute(
                f"DELETE FROM {SCHEMA}.meta WHERE key=ANY(%s)",
                (list(forged_meta),),
            )

        with psycopg.connect(self.mapping_url, autocommit=True) as conn:
            before_arguments = self._schema_snapshot(conn)
            for function in (
                "persistent_mapping_assert_safe_role_topology",
                "assert_persistent_mapping_foundation_contract",
            ):
                with (
                    self.subTest(forged_function_argument=function),
                    self.assertRaises(psycopg.errors.UndefinedFunction),
                    conn.transaction(),
                ):
                    conn.execute(
                        sql.SQL("SELECT {}.{}(%s)").format(
                            sql.Identifier(SCHEMA), sql.Identifier(function)
                        ),
                        ("forged-maintenance-identity",),
                    )
                self.assertEqual(self._schema_snapshot(conn), before_arguments)

        with self._admin_connection() as admin:
            admin.execute(
                sql.SQL(
                    "GRANT {} TO {} WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                ).format(
                    sql.Identifier(application_owner),
                    sql.Identifier(application_login),
                )
            )
            admin.execute(
                sql.SQL(
                    "GRANT {} TO qa_release_login "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
                ).format(sql.Identifier(application_owner))
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(SCHEMA), sql.Identifier(application_owner)
                )
            )
            admin.execute(
                sql.SQL("GRANT SELECT ON {}.meta TO {}").format(
                    sql.Identifier(SCHEMA), sql.Identifier(application_owner)
                )
            )

        cross_pairs = [
            {
                "current_user": application_owner,
                "session_user": application_login,
            },
            {
                "current_user": "qa_mapping_owner",
                "session_user": "qa_release_login",
            },
        ]
        cross_config = encoded(
            {
                **base_config,
                "allowed_pairs": cross_pairs,
            }
        )
        with TemporaryDirectory(
            prefix="buffalo-mapping-role-recombination-"
        ) as temporary:
            cross_root = Path(temporary)
            cross_db = cross_root / "db"
            (cross_root / "config").mkdir()
            cross_db.mkdir()
            for name in apply_schema.MIGRATION_ORDER:
                shutil.copyfile(DB_DIR / name, cross_db / name)
            shutil.copyfile(
                DB_DIR
                / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
                cross_db
                / apply_schema.MONDAY_FORECAST_V2_RETIREMENT_RELEASE.migration_name,
            )
            cross_ref = "config/row26-cross-pair.json"
            (cross_root / cross_ref).write_bytes(cross_config)
            cross_release = replace(
                apply_schema.MAPPING_RELEASE,
                maintenance_identity_config_ref=cross_ref,
                maintenance_identity_config_sha256=hashlib.sha256(
                    cross_config
                ).hexdigest(),
                maintenance_identity_pairs_sha256=hashlib.sha256(
                    apply_schema._canonical_json(cross_pairs)
                ).hexdigest(),
            )
            cross_url = self.mapping_url.replace(
                "role%3Dqa_mapping_owner",
                f"role%3D{application_owner}",
            )
            with (
                mock.patch.object(
                    apply_schema,
                    "PERSISTENT_MAPPING_RELEASE_MANIFEST",
                    (cross_release,),
                ),
                psycopg.connect(
                    self.mapping_url, autocommit=True
                ) as cross_observer,
                psycopg.connect(cross_url, autocommit=True) as cross_connection,
            ):
                cross_state = self._schema_snapshot(cross_observer)
                with (
                    self.assertRaisesRegex(RuntimeError, "pair is not approved"),
                    cross_connection.transaction(),
                ):
                    apply_schema._verify_or_apply_mapping_release(
                        cross_connection, cross_db, cross_release
                    )
                self.assertEqual(
                    self._schema_snapshot(cross_observer), cross_state
                )
        cleanup_application_roles()

        topology_roles = (
            "qa_row26_topology_probe",
            "qa_row26_topology_mid",
        )

        def cleanup_topology_roles() -> None:
            with self._admin_connection() as admin:
                existing = {
                    str(row[0])
                    for row in admin.execute(
                        "SELECT rolname FROM pg_catalog.pg_roles "
                        "WHERE rolname=ANY(%s)",
                        (list(topology_roles),),
                    ).fetchall()
                }
                for role in topology_roles:
                    if role in existing:
                        admin.execute(
                            sql.SQL("DROP OWNED BY {}").format(
                                sql.Identifier(role)
                            )
                        )
                for role in topology_roles:
                    if role in existing:
                        admin.execute(
                            sql.SQL("DROP ROLE {}").format(
                                sql.Identifier(role)
                            )
                        )

        self.addCleanup(cleanup_topology_roles)
        create_probe = (
            "CREATE ROLE qa_row26_topology_probe LOGIN NOINHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
        )
        create_mid = (
            "CREATE ROLE qa_row26_topology_mid NOLOGIN NOINHERIT "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
        )
        topology_cases = (
            (
                "direct schema CREATE",
                (
                    create_probe,
                    f"GRANT USAGE,CREATE ON SCHEMA {SCHEMA} "
                    "TO qa_row26_topology_probe",
                ),
            ),
            (
                "direct SET to owner",
                (
                    create_probe,
                    "GRANT qa_mapping_owner TO qa_row26_topology_probe "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE",
                ),
            ),
            (
                "transitive SET to owner",
                (
                    create_probe,
                    create_mid,
                    "GRANT qa_mapping_owner TO qa_row26_topology_mid "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE",
                    "GRANT qa_row26_topology_mid TO qa_row26_topology_probe "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE",
                ),
            ),
            (
                "direct INHERIT from owner",
                (
                    create_probe,
                    "GRANT qa_mapping_owner TO qa_row26_topology_probe "
                    "WITH INHERIT TRUE, SET FALSE, ADMIN FALSE",
                ),
            ),
            (
                "transitive INHERIT from owner",
                (
                    create_probe,
                    create_mid,
                    "GRANT qa_mapping_owner TO qa_row26_topology_mid "
                    "WITH INHERIT TRUE, SET FALSE, ADMIN FALSE",
                    "GRANT qa_row26_topology_mid TO qa_row26_topology_probe "
                    "WITH INHERIT TRUE, SET FALSE, ADMIN FALSE",
                ),
            ),
            (
                "SET then INHERIT mixed owner path",
                (
                    create_probe,
                    create_mid,
                    "GRANT qa_mapping_owner TO qa_row26_topology_mid "
                    "WITH INHERIT TRUE, SET FALSE, ADMIN FALSE",
                    "GRANT qa_row26_topology_mid TO qa_row26_topology_probe "
                    "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE",
                ),
            ),
        )
        for label, statements in topology_cases:
            with self.subTest(post_install_topology=label):
                cleanup_topology_roles()
                self._reset(include_mapping=True)
                try:
                    with self._admin_connection() as admin:
                        for statement in statements:
                            admin.execute(statement)
                    with psycopg.connect(
                        self.mapping_url, autocommit=True
                    ) as observer:
                        unsafe_state = self._schema_snapshot(observer)
                        with (
                            self.assertRaisesRegex(
                                RuntimeError,
                                "effective persistent mapping privilege",
                            ),
                            observer.transaction(),
                        ):
                            apply_schema._verify_or_apply_mapping_release(
                                observer, DB_DIR
                            )
                        self.assertEqual(
                            self._schema_snapshot(observer), unsafe_state
                        )
                        with (
                            self.assertRaisesRegex(
                                psycopg.Error,
                                "effective mapping privilege|non-owner principal|"
                                "effective owner-role path",
                            ),
                            observer.transaction(),
                        ):
                            observer.execute(
                                f"SELECT {SCHEMA}."
                                "assert_persistent_mapping_foundation_contract()"
                            )
                        self.assertEqual(
                            self._schema_snapshot(observer), unsafe_state
                        )
                finally:
                    cleanup_topology_roles()

        privilege_probe = "qa_mapping_privilege_probe"

        def cleanup_privilege_probe() -> None:
            with self._admin_connection() as admin:
                if admin.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s)",
                    (privilege_probe,),
                ).fetchone()[0]:
                    admin.execute(
                        sql.SQL("DROP OWNED BY {}")
                        .format(sql.Identifier(privilege_probe))
                    )
                    admin.execute(
                        sql.SQL("DROP ROLE {}")
                        .format(sql.Identifier(privilege_probe))
                    )

        self.addCleanup(cleanup_privilege_probe)
        with self._admin_connection() as admin:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(privilege_probe))
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(SCHEMA), sql.Identifier(privilege_probe)
                )
            )

        for label, grant, revoke in (
            (
                "relation SELECT",
                f"GRANT SELECT ON {SCHEMA}.supplier_mapping_review_batches "
                f"TO {privilege_probe}",
                f"REVOKE SELECT ON {SCHEMA}.supplier_mapping_review_batches "
                f"FROM {privilege_probe}",
            ),
            (
                "column UPDATE",
                f"GRANT UPDATE(source_revision) ON "
                f"{SCHEMA}.supplier_mapping_review_batches TO {privilege_probe}",
                f"REVOKE UPDATE(source_revision) ON "
                f"{SCHEMA}.supplier_mapping_review_batches FROM {privilege_probe}",
            ),
            (
                "function EXECUTE",
                f"GRANT EXECUTE ON FUNCTION "
                f"{SCHEMA}.persistent_mapping_text_sha256(text) TO {privilege_probe}",
                f"REVOKE EXECUTE ON FUNCTION "
                f"{SCHEMA}.persistent_mapping_text_sha256(text) FROM {privilege_probe}",
            ),
        ):
            with self.subTest(effective_privilege=label):
                with self._admin_connection() as admin:
                    admin.execute(grant)
                with psycopg.connect(self.mapping_url, autocommit=True) as conn:
                    unsafe_state = self._schema_snapshot(conn)
                    with (
                        self.assertRaisesRegex(
                            RuntimeError,
                            "effective persistent mapping privilege",
                        ),
                        conn.transaction(),
                    ):
                        apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
                    self.assertEqual(self._schema_snapshot(conn), unsafe_state)
                    with (
                        self.assertRaisesRegex(
                            psycopg.Error,
                            "effective mapping privilege|non-owner principal",
                        ),
                        conn.transaction(),
                    ):
                        conn.execute(
                            f"SELECT {SCHEMA}."
                            "assert_persistent_mapping_foundation_contract()"
                        )
                    self.assertEqual(self._schema_snapshot(conn), unsafe_state)
                with self._admin_connection() as admin:
                    admin.execute(revoke)

        # GRANT/REVOKE can leave a semantically empty explicit ACL rather than
        # the original NULL catalog value. Rebuild the isolated schema so the
        # subsequent release-rotation proof begins from the exact signed bytes.
        self._reset(include_mapping=True)

        baseline_membership = membership_rows()
        with self._admin_connection() as admin:
            admin.execute("REVOKE qa_mapping_owner FROM qa_release_login")
            admin.execute("GRANT qa_mapping_owner TO qa_release_login WITH INHERIT TRUE, SET TRUE, ADMIN FALSE")
        unsafe_membership = membership_rows()
        with psycopg.connect(self.mapping_url, autocommit=True) as conn:
            unsafe_state = self._schema_snapshot(conn)
            with (
                self.assertRaisesRegex(RuntimeError, "role topology"),
                conn.transaction(),
            ):
                apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
            self.assertEqual(self._schema_snapshot(conn), unsafe_state)
            with (
                self.assertRaisesRegex(
                    psycopg.Error, "approved maintenance role topology"
                ),
                conn.transaction(),
            ):
                conn.execute(
                    f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                )
            self.assertEqual(self._schema_snapshot(conn), unsafe_state)
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
                self.assertEqual(
                    apply_schema.apply_schema_connection(
                        conn, db_dir, include_persistent_mapping=True
                    ),
                    [remove.migration_name],
                )
                conn.execute(
                    f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                )
                self.assertEqual(
                    apply_schema.apply_schema_connection(
                        conn, db_dir, include_persistent_mapping=True
                    ),
                    [],
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
                psycopg.connect(self.mapping_url, autocommit=True) as conn,
            ):
                restored_state = self._schema_snapshot(conn)
                with (
                    self.assertRaisesRegex(psycopg.Error, "not approved"),
                    conn.transaction(),
                ):
                    conn.execute(
                        f"SELECT {SCHEMA}.assert_persistent_mapping_foundation_contract()"
                    )
                self.assertEqual(self._schema_snapshot(conn), restored_state)
                with (
                    self.assertRaisesRegex(RuntimeError, "not approved"),
                    conn.transaction(),
                ):
                    apply_schema._verify_or_apply_mapping_release(conn, db_dir, remove)
                self.assertEqual(self._schema_snapshot(conn), restored_state)
            self.assertEqual(membership_rows(), restored_old_membership)

        # The public runner is allowed to have committed the reviewed legacy
        # prefix before it reaches this separately locked authority boundary.
        # An unapproved invocation must still leave that exact predecessor and
        # every role membership byte untouched, without publishing any mapping
        # object or marker.
        self._reset(include_mapping=False)
        with psycopg.connect(self.admin_url, autocommit=True) as unapproved:
            predecessor_state = self._schema_snapshot(unapproved)
            predecessor_membership = membership_rows()
            predecessor_markers = tuple(
                unapproved.execute(
                    f"SELECT key,value FROM {SCHEMA}.meta "
                    "WHERE key LIKE 'migration:%' ORDER BY key"
                ).fetchall()
            )
            self.assertEqual(
                predecessor_markers,
                tuple(
                    sorted(
                        (f"migration:{name}", "applied")
                        for name in apply_schema.MIGRATION_ORDER[:-1]
                    )
                ),
            )
            with (
                self.assertRaisesRegex(RuntimeError, "pair is not approved"),
                unapproved.transaction(),
            ):
                apply_schema._verify_or_apply_mapping_release(
                    unapproved, DB_DIR, apply_schema.MAPPING_RELEASE
                )
            self.assertEqual(
                self._schema_snapshot(unapproved), predecessor_state
            )
            self.assertEqual(membership_rows(), predecessor_membership)
            self.assertEqual(
                tuple(
                    unapproved.execute(
                        f"SELECT key,value FROM {SCHEMA}.meta "
                        "WHERE key LIKE 'migration:%' ORDER BY key"
                    ).fetchall()
                ),
                predecessor_markers,
            )
            self.assertIsNone(
                unapproved.execute(
                    "SELECT pg_catalog.to_regclass(%s)",
                    (f'"{SCHEMA}".supplier_mapping_review_batches',),
                ).fetchone()[0]
            )

    def test_policy_mapping_requires_published_policy_and_independent_evidence(self):
        def authority_state() -> tuple:
            tables = (
                "supplier_mapping_decisions",
                "supplier_offers",
                "mapping_rejections",
                "supplier_offer_selection_events",
                "supplier_offer_selection_heads",
                "prices",
                "procurement_recommendations",
            )
            with psycopg.connect(self.mapping_url) as conn:
                return tuple(
                    (
                        table,
                        tuple(
                            str(row[0])
                            for row in conn.execute(
                                sql.SQL(
                                    "SELECT pg_catalog.to_jsonb(t)::text FROM {}.{} t "
                                    "ORDER BY pg_catalog.to_jsonb(t)::text"
                                ).format(
                                    sql.Identifier(SCHEMA), sql.Identifier(table)
                                )
                            ).fetchall()
                        ),
                    )
                    for table in tables
                )

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
        unresolved_candidate = self._intake(self._packet(0))
        offer = self._legacy_offer(sku="POLICY-ELIGIBLE")
        eligible_candidate = self._intake(
            self._packet(
                1,
                occurrence="policy-eligible",
                package_id="policy-eligible-package",
                candidate_changes={
                    "supplier_code_value": "POLICY-ELIGIBLE",
                    "distributor_product_id_value": "POLICY-ELIGIBLE",
                },
            )
        )

        def attempt_policy_record(
            *,
            label: str,
            action: str,
            target: UUID,
            linked_offer: int | None = None,
            overrides: dict | None = None,
        ) -> None:
            before = authority_state()
            with self.subTest(case=label), psycopg.connect(self.mapping_url) as conn:
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
                    reason=f"fabricated policy-origin attempt: {label}",
                    principal=self.principal,
                    decision_idempotency_key=key,
                    existing_offer_id=linked_offer,
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
                if overrides:
                    record.update(copy.deepcopy(overrides))
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
            self.assertEqual(authority_state(), before)

        for action, target, linked_offer in (
            ("APPROVE_MAPPING", eligible_candidate, offer),
            ("DEFER", unresolved_candidate, None),
            ("REJECT_MAPPING", eligible_candidate, None),
        ):
            attempt_policy_record(
                label=f"false publication stub {action}",
                action=action,
                target=target,
                linked_offer=linked_offer,
            )

        malformed_policy_fields = {
            "publication null": {"policy_publication_sha256": None},
            "publication malformed": {"policy_publication_sha256": "bad"},
            "predicate result null": {"policy_predicate_result_sha256": None},
            "predicate result malformed": {"policy_predicate_result_sha256": "bad"},
            "service principal blank": {"service_principal_ref": ""},
            "policy ref blank": {"policy_ref": ""},
            "policy version blank": {"policy_version": ""},
            "predicate version blank": {"policy_predicate_version": ""},
        }
        for label, overrides in malformed_policy_fields.items():
            attempt_policy_record(
                label=label,
                action="APPROVE_MAPPING",
                target=eligible_candidate,
                linked_offer=offer,
                overrides=overrides,
            )

        evidence_cases: list[tuple[str, dict]] = []
        no_independent = self._packet(
            1,
            occurrence="policy-no-independent",
            package_id="policy-no-independent-package",
            candidate_changes={
                "supplier_code_value": "POLICY-NO-INDEPENDENT",
                "distributor_product_id_value": "POLICY-NO-INDEPENDENT",
                "independent_linkage_evidence": [],
            },
        )
        evidence_cases.append(("no deterministic independent evidence", no_independent))
        fuzzy_authority = self._packet(
            1,
            occurrence="policy-fuzzy-authority",
            package_id="policy-fuzzy-authority-package",
            candidate_changes={
                "supplier_code_value": "POLICY-FUZZY-AUTHORITY",
                "distributor_product_id_value": "POLICY-FUZZY-AUTHORITY",
                "independent_linkage_evidence": [
                    {
                        "evidence_mode": "FUZZY_SCORE",
                        "authoritative": True,
                        "origin_sha256": "2" * 64,
                        "observation_sha256": "3" * 64,
                    }
                ],
            },
        )
        evidence_cases.append(("fuzzy evidence marked authoritative", fuzzy_authority))
        blocked = self._packet(
            1,
            occurrence="policy-blocked",
            package_id="policy-blocked-package",
            candidate_changes={
                "supplier_code_value": "POLICY-BLOCKED",
                "distributor_product_id_value": "POLICY-BLOCKED",
                "blockers": ["FABRICATED_POLICY_BLOCKER"],
            },
        )
        evidence_cases.append(("blocked evidence", blocked))
        simulated = self._packet(
            1,
            occurrence="policy-simulated",
            package_id="policy-simulated-package",
            candidate_changes={
                "supplier_code_value": "POLICY-SIMULATED",
                "distributor_product_id_value": "POLICY-SIMULATED",
            },
        )
        simulated["package"]["source_is_simulation"] = True
        evidence_cases.append(("simulated evidence", simulated))

        for label, packet in evidence_cases:
            supplier_code = packet["candidates"][0]["supplier_code_value"]
            evidence_offer = self._legacy_offer(sku=supplier_code)
            target = self._intake(packet)
            attempt_policy_record(
                label=label,
                action="APPROVE_MAPPING",
                target=target,
                linked_offer=evidence_offer,
            )

        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                    "WHERE decision_origin='POLICY'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    f"SELECT count(*) FROM "
                    f"{SCHEMA}.v_effective_supplier_mapping_decisions "
                    "WHERE authority_kind='POLICY_APPROVED'"
                ).fetchone()[0],
                0,
            )

    def test_mapping_approval_creates_inactive_unpriced_unselected_offer(self):
        def authority_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                            "ORDER BY mapping_decision_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                            "ORDER BY offer_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(p) FROM {SCHEMA}.prices p ORDER BY price_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(e) FROM {SCHEMA}.supplier_offer_selection_events e "
                            "ORDER BY selection_event_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(h) FROM {SCHEMA}.supplier_offer_selection_heads h "
                            "ORDER BY variant_id,selection_scope"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.procurement_recommendations r "
                            "ORDER BY recommendation_id"
                        ).fetchall()
                    ),
                )

        before = authority_state()
        packet = self._packet(
            1,
            occurrence="create-only",
            package_id="create-only-package",
            candidate_changes={
                "supplier_code_value": "NEW-CREATE",
                "distributor_product_id_value": "NEW-CREATE",
            },
        )
        candidate_id = self._intake(packet)
        result = self._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="create inactive",
            link_kind="CREATED_INACTIVE",
        )
        with psycopg.connect(self.mapping_url) as conn:
            candidate = conn.execute(
                f"SELECT proposed_variant_id,proposed_vendor_id,supplier_code_value,"
                "size_value,raw_pack_value,shopify_units_value,"
                "qualifying_units_value,assortment_scope_value,"
                "assortment_group_value,assortable_value,source_file_name,"
                f"source_page_start FROM {SCHEMA}.supplier_mapping_review_candidates "
                "WHERE candidate_id=%s",
                (candidate_id,),
            ).fetchone()
            decision_and_offer = conn.execute(
                f"SELECT to_jsonb(d),to_jsonb(o),d.decided_txid,"
                "d.xmin::text::bigint,o.xmin::text::bigint,"
                f"{SCHEMA}.persistent_mapping_offer_fingerprint(o.offer_id) "
                f"FROM {SCHEMA}.supplier_mapping_decisions d "
                f"JOIN {SCHEMA}.supplier_offers o ON o.offer_id=d.result_offer_id "
                "WHERE d.mapping_decision_id=%s",
                (result["mapping_decision_id"],),
            ).fetchone()
        self.assertIsNotNone(decision_and_offer)
        decision, offer = decision_and_offer[:2]
        self.assertEqual(decision["action"], "APPROVE_MAPPING")
        self.assertEqual(decision["offer_link_kind"], "CREATED_INACTIVE")
        self.assertEqual(decision["candidate_id"], str(candidate_id))
        self.assertEqual(decision["result_offer_id"], result["result_offer_id"])
        self.assertEqual(
            decision_and_offer[2:5],
            (decision_and_offer[2], decision_and_offer[2], decision_and_offer[2]),
        )
        self.assertEqual(
            decision["result_offer_contract_sha256"], decision_and_offer[5]
        )
        self.assertEqual(
            (
                offer["variant_id"],
                offer["vendor_id"],
                offer["supplier_sku"],
                offer["size_text"],
                offer["raw_pack"],
                offer["shopify_units_per_case"],
                offer["qualifying_units_per_case"],
                offer["assortment_scope"],
                offer["assortment_group"],
                offer["assortable"],
                offer["source_file"],
                offer["source_page"],
            ),
            (
                candidate[0],
                str(candidate[1]),
                candidate[2],
                candidate[3],
                candidate[4],
                float(candidate[5]),
                float(candidate[6]),
                candidate[7],
                candidate[8],
                candidate[9],
                candidate[10],
                candidate[11],
            ),
        )
        self.assertEqual(offer["package_type"], "STANDARD")
        self.assertEqual(offer["confidence"], "VERIFIED")
        self.assertFalse(offer["active"])
        after = authority_state()
        self.assertEqual(len(after[0]), len(before[0]) + 1)
        self.assertEqual(len(after[1]), len(before[1]) + 1)
        self.assertEqual(after[2:], before[2:])

        rollback_candidate = self._intake(
            self._packet(
                1,
                occurrence="create-only-rollback",
                package_id="create-only-rollback-package",
                candidate_changes={
                    "supplier_code_value": "NEW-CREATE-ROLLBACK",
                    "distributor_product_id_value": "NEW-CREATE-ROLLBACK",
                },
            )
        )
        before_late_failure = authority_state()
        original_insert_offer = mapping_service._insert_created_inactive_offer

        def insert_then_refuse(conn, **kwargs):
            original_insert_offer(conn, **kwargs)
            raise psycopg.errors.CheckViolation(
                "synthetic refusal after provisional offer insert"
            )

        with (
            mock.patch.object(
                mapping_service,
                "_insert_created_inactive_offer",
                side_effect=insert_then_refuse,
            ),
            self.assertRaises(PersistentMappingError) as refused,
        ):
            self._decide(
                rollback_candidate,
                action="APPROVE_MAPPING",
                reason="prove decision and offer atomic rollback",
                link_kind="CREATED_INACTIVE",
            )
        self.assertEqual(refused.exception.code, "DATABASE_VALIDATION_REFUSED")
        self.assertEqual(authority_state(), before_late_failure)

    def test_valid_mapping_then_separate_selection_requires_second_confirmation(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="map",
            offer_id=offer,
            link_kind="LINKED_EXISTING",
        )
        decision_id = UUID(str(decision["mapping_decision_id"]))
        other_offer = self._legacy_offer(variant_id="2002", sku="SUP-002")
        other_candidate_id = self._intake(
            self._packet(
                1,
                occurrence="selection-idempotency-other-target",
                package_id="selection-idempotency-other-target-package",
                candidate_changes={
                    "proposed_variant_id": "2002",
                    "supplier_code_value": "SUP-002",
                    "distributor_product_id_value": "SUP-002",
                },
            )
        )
        other_decision = self._decide(
            other_candidate_id,
            action="APPROVE_MAPPING",
            reason="independent mapped target",
            offer_id=other_offer,
            link_kind="LINKED_EXISTING",
        )
        other_decision_id = UUID(str(other_decision["mapping_decision_id"]))

        def nonselection_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        conn.execute(
                        f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                        "ORDER BY mapping_decision_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                        f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                        "ORDER BY offer_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.mapping_rejections r "
                            "ORDER BY rejection_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(p) FROM {SCHEMA}.prices p ORDER BY price_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.procurement_recommendations r "
                            "ORDER BY recommendation_id"
                        ).fetchall()
                    ),
                )

        def selection_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(e) FROM {SCHEMA}.supplier_offer_selection_events e "
                            "ORDER BY selection_event_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(h) FROM {SCHEMA}.supplier_offer_selection_heads h "
                            "ORDER BY variant_id,selection_scope"
                        ).fetchall()
                    ),
                )

        before_nonselection = nonselection_state()
        empty_selection = selection_state()
        mapping_key = UUID(str(decision["decision_idempotency_key"]))
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            reused_key_preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=decision_id,
                principal=self.selection_principal,
                selection_idempotency_key=mapping_key,
                reason="mapping key is not a selection confirmation",
                effective_from=BUSINESS_DATE,
            )
        with self.assertRaises(PersistentMappingError) as reused_key:
            execute_routine_offer_selection(
                self.mapping_url,
                mapping_decision_id=decision_id,
                principal=self.selection_principal,
                selection_idempotency_key=mapping_key,
                reason="mapping key is not a selection confirmation",
                effective_from=BUSINESS_DATE,
                expected_preview_sha256=reused_key_preview["preview_sha256"],
            )
        self.assertEqual(reused_key.exception.code, "DATABASE_VALIDATION_REFUSED")
        self.assertEqual(selection_state(), empty_selection)

        for label, mapping_confirmation in (
            ("mapping preview", decision["preview_sha256"]),
            ("mapping confirmation", decision["confirmation_sha256"]),
        ):
            key = uuid4()
            with self.subTest(reused_material=label), self.assertRaises(
                PersistentMappingError
            ) as refused:
                execute_routine_offer_selection(
                    self.mapping_url,
                    mapping_decision_id=decision_id,
                    principal=self.selection_principal,
                    selection_idempotency_key=key,
                    reason=f"{label} is not selection confirmation",
                    effective_from=BUSINESS_DATE,
                    expected_preview_sha256=mapping_confirmation,
                )
            self.assertEqual(refused.exception.code, "MAPPING_REFUSED")
            self.assertEqual(selection_state(), empty_selection)

        selection_key = uuid4()
        selection_reason = "separate exact selection confirmation"
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            selection_preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=decision_id,
                principal=self.selection_principal,
                selection_idempotency_key=selection_key,
                reason=selection_reason,
                effective_from=BUSINESS_DATE,
            )
            other_selection_preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=other_decision_id,
                principal=self.selection_principal,
                selection_idempotency_key=selection_key,
                reason="same key for a different mapped target",
                effective_from=BUSINESS_DATE,
            )
        selected = execute_routine_offer_selection(
            self.mapping_url,
            mapping_decision_id=decision_id,
            principal=self.selection_principal,
            selection_idempotency_key=selection_key,
            reason=selection_reason,
            effective_from=BUSINESS_DATE,
            expected_preview_sha256=selection_preview["preview_sha256"],
        )
        self.assertEqual(selected["action"], "SELECT")
        self.assertNotEqual(
            selected["selection_idempotency_key"], decision["decision_idempotency_key"]
        )
        self.assertNotIn(
            selected["preview_sha256"],
            (decision["preview_sha256"], decision["confirmation_sha256"]),
        )
        self.assertNotIn(
            selected["confirmation_sha256"],
            (decision["preview_sha256"], decision["confirmation_sha256"]),
        )
        after_selection = selection_state()
        self.assertEqual(len(after_selection[0]), 1)
        self.assertEqual(len(after_selection[1]), 1)
        event = after_selection[0][0][0]
        head = after_selection[1][0][0]
        self.assertEqual(event["selection_event_id"], selected["selection_event_id"])
        self.assertEqual(event["mapping_decision_id"], str(decision_id))
        self.assertEqual(event["selected_offer_id"], offer)
        self.assertEqual(event["expected_prior_event_id"], None)
        self.assertEqual(event["expected_prior_head_version"], 0)
        self.assertEqual(head["selection_event_id"], selected["selection_event_id"])
        self.assertEqual(head["head_version"], 1)
        self.assertEqual(nonselection_state(), before_nonselection)

        replay = execute_routine_offer_selection(
            self.mapping_url,
            mapping_decision_id=decision_id,
            principal=self.selection_principal,
            selection_idempotency_key=selection_key,
            reason=selection_reason,
            effective_from=BUSINESS_DATE,
            expected_preview_sha256=selection_preview["preview_sha256"],
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            str(replay["selection_event_id"]), str(selected["selection_event_id"])
        )
        self.assertEqual(replay["payload_sha256"], selected["payload_sha256"])
        self.assertEqual(selection_state(), after_selection)

        for changed in (
            {"reason": "different selection reason"},
            {"effective_from": BUSINESS_DATE.replace(day=14)},
            {
                "mapping_decision_id": other_decision_id,
                "reason": "same key for a different mapped target",
                "expected_preview_sha256": other_selection_preview[
                    "preview_sha256"
                ],
            },
        ):
            arguments = {
                "mapping_decision_id": decision_id,
                "principal": self.selection_principal,
                "selection_idempotency_key": selection_key,
                "reason": selection_reason,
                "effective_from": BUSINESS_DATE,
                "expected_preview_sha256": selection_preview["preview_sha256"],
                **changed,
            }
            with self.subTest(changed=changed), self.assertRaises(
                PersistentMappingError
            ) as conflict:
                execute_routine_offer_selection(self.mapping_url, **arguments)
            self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")
            self.assertEqual(selection_state(), after_selection)
            self.assertEqual(nonselection_state(), before_nonselection)

    def test_clear_appends_event_and_preserves_prior_selection(self):
        offer = self._legacy_offer()
        candidate_id = self._intake(self._packet(1))
        decision = self._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="map",
            offer_id=offer,
            link_kind="LINKED_EXISTING",
        )
        selected = self._select(decision)
        with psycopg.connect(self.mapping_url) as conn:
            immutable_before = (
                conn.execute(
                    f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                    "WHERE offer_id=%s",
                    (offer,),
                ).fetchone()[0],
                conn.execute(
                    f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                    "WHERE mapping_decision_id=%s",
                    (decision["mapping_decision_id"],),
                ).fetchone()[0],
                tuple(
                    conn.execute(
                        f"SELECT to_jsonb(p) FROM {SCHEMA}.prices p ORDER BY price_id"
                    ).fetchall()
                ),
                tuple(
                    conn.execute(
                        f"SELECT to_jsonb(r) FROM {SCHEMA}.procurement_recommendations r "
                        "ORDER BY recommendation_id"
                    ).fetchall()
                ),
            )
            selected_before = conn.execute(
                f"SELECT to_jsonb(e) FROM {SCHEMA}.supplier_offer_selection_events e "
                "WHERE selection_event_id=%s",
                (selected["selection_event_id"],),
            ).fetchone()[0]
        key = uuid4()
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            preview = preview_routine_offer_clear(
                conn,
                variant_id="1001",
                principal=self.selection_principal,
                selection_idempotency_key=key,
                reason="explicit clear",
                effective_from=BUSINESS_DATE,
            )
        cleared = execute_routine_offer_clear(
            self.mapping_url,
            variant_id="1001",
            principal=self.selection_principal,
            selection_idempotency_key=key,
            reason="explicit clear",
            effective_from=BUSINESS_DATE,
            expected_preview_sha256=preview["preview_sha256"],
        )
        self.assertEqual(cleared["action"], "CLEAR")
        self.assertNotEqual(cleared["selection_event_id"], selected["selection_event_id"])
        with psycopg.connect(self.mapping_url) as conn:
            events = tuple(
                row[0]
                for row in conn.execute(
                    f"SELECT to_jsonb(e) FROM {SCHEMA}.supplier_offer_selection_events e "
                    "ORDER BY selected_at,selection_event_id"
                ).fetchall()
            )
            head = conn.execute(
                f"SELECT to_jsonb(h) FROM {SCHEMA}.supplier_offer_selection_heads h "
                "WHERE variant_id='1001' AND selection_scope=%s",
                (mapping_service.SELECTION_SCOPE,),
            ).fetchone()[0]
            immutable_after = (
                conn.execute(
                    f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                    "WHERE offer_id=%s",
                    (offer,),
                ).fetchone()[0],
                conn.execute(
                    f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                    "WHERE mapping_decision_id=%s",
                    (decision["mapping_decision_id"],),
                ).fetchone()[0],
                tuple(
                    conn.execute(
                        f"SELECT to_jsonb(p) FROM {SCHEMA}.prices p ORDER BY price_id"
                    ).fetchall()
                ),
                tuple(
                    conn.execute(
                        f"SELECT to_jsonb(r) FROM {SCHEMA}.procurement_recommendations r "
                        "ORDER BY recommendation_id"
                    ).fetchall()
                ),
            )
        self.assertEqual(len(events), 2)
        select_event = next(row for row in events if row["action"] == "SELECT")
        clear_event = next(row for row in events if row["action"] == "CLEAR")
        self.assertEqual(select_event, selected_before)
        self.assertEqual(
            (
                clear_event["selected_offer_id"],
                clear_event["mapping_decision_id"],
                clear_event["expected_mapping_decision_sha256"],
                clear_event["expected_offer_contract_sha256"],
                clear_event["expected_vendor_sha256"],
                clear_event["expected_rejection_memory_sha256"],
            ),
            (None, None, None, None, None, None),
        )
        self.assertEqual(
            (
                clear_event["expected_prior_event_id"],
                clear_event["expected_prior_head_version"],
            ),
            (selected["selection_event_id"], 1),
        )
        self.assertEqual(head["selection_event_id"], cleared["selection_event_id"])
        self.assertEqual(head["head_version"], 2)
        self.assertEqual(immutable_after, immutable_before)
        self.assertTrue(immutable_after[0]["active"])

        replay = execute_routine_offer_clear(
            self.mapping_url,
            variant_id="1001",
            principal=self.selection_principal,
            selection_idempotency_key=key,
            reason="explicit clear",
            effective_from=BUSINESS_DATE,
            expected_preview_sha256=preview["preview_sha256"],
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            str(replay["selection_event_id"]), str(cleared["selection_event_id"])
        )
        with self.assertRaises(PersistentMappingError) as conflict:
            execute_routine_offer_clear(
                self.mapping_url,
                variant_id="1001",
                principal=self.selection_principal,
                selection_idempotency_key=key,
                reason="changed clear reason",
                effective_from=BUSINESS_DATE,
                expected_preview_sha256=preview["preview_sha256"],
            )
        self.assertEqual(conflict.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(self._counts()[3:5], (2, 1))

    def test_selection_rejects_stale_offer_catalog_vendor_rejection_and_prior_head(self):
        def selection_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(e) FROM {SCHEMA}.supplier_offer_selection_events e "
                            "ORDER BY selection_event_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(h) FROM {SCHEMA}.supplier_offer_selection_heads h "
                            "ORDER BY variant_id,selection_scope"
                        ).fetchall()
                    ),
                )

        original_record = mapping_service.record_routine_offer_selection
        for stale_kind in ("offer", "catalog", "vendor", "rejection", "prior head"):
            self._reset(include_mapping=True)
            self._seed_catalog()
            supplier_code = f"STALE-SELECTION-{stale_kind.upper().replace(' ', '-')}"
            offer = self._legacy_offer(sku=supplier_code)
            candidate_id = self._intake(
                self._packet(
                    1,
                    occurrence=f"stale-selection-{stale_kind.replace(' ', '-')}",
                    package_id=(
                        f"stale-selection-{stale_kind.replace(' ', '-')}-package"
                    ),
                    candidate_changes={
                        "supplier_code_value": supplier_code,
                        "distributor_product_id_value": supplier_code,
                    },
                )
            )
            decision = self._decide(
                candidate_id,
                action="APPROVE_MAPPING",
                reason=f"map before stale {stale_kind}",
                offer_id=offer,
                link_kind="LINKED_EXISTING",
            )
            decision_id = UUID(str(decision["mapping_decision_id"]))
            key = uuid4()
            reason = f"stale {stale_kind} selection"
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

            forged_record = None
            if stale_kind == "offer":
                with psycopg.connect(self.mapping_url) as conn:
                    forged_record = copy.deepcopy(preview)
                    forged_record["expected_offer_contract_sha256"] = "0" * 64
                    forged_record["preview_sha256"] = mapping_service._composite_value(
                        conn,
                        table="supplier_offer_selection_events",
                        function="persistent_mapping_selection_preview_sha256",
                        record=forged_record,
                    )
                    forged_record["confirmation_sha256"] = (
                        mapping_service._composite_value(
                            conn,
                            table="supplier_offer_selection_events",
                            function=(
                                "persistent_mapping_selection_confirmation_sha256"
                            ),
                            record=forged_record,
                        )
                    )
                    payload, payload_sha256 = mapping_service._project_record(
                        conn,
                        table="supplier_offer_selection_events",
                        record=forged_record,
                        omit=(
                            "selection_event_id",
                            "selection_idempotency_key",
                            "canonical_payload",
                            "payload_sha256",
                            "selected_at",
                            "selected_txid",
                        ),
                    )
                    forged_record["canonical_payload"] = payload
                    forged_record["payload_sha256"] = payload_sha256
            elif stale_kind == "catalog":
                with psycopg.connect(self.mapping_url) as conn:
                    conn.execute(
                        f"UPDATE {SCHEMA}.variants SET source_snapshot=%s "
                        "WHERE variant_id='1001'",
                        ("changed after selection preview",),
                    )
            elif stale_kind == "vendor":
                with psycopg.connect(self.mapping_url) as conn:
                    conn.execute(
                        f"UPDATE {SCHEMA}.vendors "
                        "SET updated_at=updated_at + interval '1 second' "
                        "WHERE vendor_id=%s",
                        (VENDOR_ID,),
                    )
            elif stale_kind == "rejection":
                blocker = self._intake(
                    self._packet(
                        1,
                        occurrence="stale-selection-rejection-blocker",
                        package_id="stale-selection-rejection-blocker-package",
                        candidate_changes={
                            "supplier_code_value": supplier_code,
                            "distributor_product_id_value": supplier_code,
                        },
                    )
                )
                self._decide(
                    blocker,
                    action="REJECT_MAPPING",
                    reason="append exact rejection after selection preview",
                )
            else:
                self._select(
                    decision,
                    key=uuid4(),
                    reason="winner advances the shared prior head",
                )

            before = selection_state()
            with ExitStack() as contexts:
                record_call = contexts.enter_context(
                    mock.patch.object(
                        mapping_service,
                        "record_routine_offer_selection",
                        wraps=original_record,
                    )
                )
                if forged_record is not None:
                    contexts.enter_context(
                        mock.patch.object(
                            mapping_service,
                            "_selection_record",
                            return_value=forged_record,
                        )
                    )
                    expected_preview = forged_record["preview_sha256"]
                else:
                    expected_preview = preview["preview_sha256"]
                raised = contexts.enter_context(
                    self.assertRaises(PersistentMappingError)
                )
                execute_routine_offer_selection(
                    self.mapping_url,
                    mapping_decision_id=decision_id,
                    principal=self.selection_principal,
                    selection_idempotency_key=key,
                    reason=reason,
                    effective_from=BUSINESS_DATE,
                    expected_preview_sha256=expected_preview,
                )
            self.assertEqual(record_call.call_count, 1)
            self.assertIn(
                raised.exception.code,
                {"MAPPING_REFUSED", "DATABASE_VALIDATION_REFUSED"},
            )
            if stale_kind == "offer":
                self.assertEqual(
                    raised.exception.code, "DATABASE_VALIDATION_REFUSED"
                )
                self.assertIn(
                    "selected offer is stale",
                    str(raised.exception.__cause__),
                )
            else:
                self.assertEqual(raised.exception.code, "MAPPING_REFUSED")
            self.assertEqual(selection_state(), before)
            with psycopg.connect(self.mapping_url) as conn:
                self.assertIsNone(
                    conn.execute(
                        f"SELECT selection_event_id FROM "
                        f"{SCHEMA}.supplier_offer_selection_events "
                        "WHERE selection_idempotency_key=%s",
                        (key,),
                    ).fetchone()
                )

    def test_concurrent_selection_replay_and_same_prior_head_have_exact_outcomes(self):
        def prepare(label: str) -> UUID:
            self._reset(include_mapping=True)
            self._seed_catalog()
            supplier_code = f"SELECTION-RACE-{label.upper()}"
            offer = self._legacy_offer(sku=supplier_code)
            candidate_id = self._intake(
                self._packet(
                    1,
                    occurrence=f"selection-race-{label}",
                    package_id=f"selection-race-{label}-package",
                    candidate_changes={
                        "supplier_code_value": supplier_code,
                        "distributor_product_id_value": supplier_code,
                    },
                )
            )
            decision = self._decide(
                candidate_id,
                action="APPROVE_MAPPING",
                reason=f"map for selection race {label}",
                offer_id=offer,
                link_kind="LINKED_EXISTING",
            )
            return UUID(str(decision["mapping_decision_id"]))

        def preview(decision_id: UUID, key: UUID, reason: str) -> dict:
            with psycopg.connect(self.mapping_url) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                return preview_routine_offer_selection(
                    conn,
                    mapping_decision_id=decision_id,
                    principal=self.selection_principal,
                    selection_idempotency_key=key,
                    reason=reason,
                    effective_from=BUSINESS_DATE,
                )

        def selection_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        row[0]
                        for row in conn.execute(
                            f"SELECT to_jsonb(e) FROM {SCHEMA}.supplier_offer_selection_events e "
                            "ORDER BY selection_event_id"
                        ).fetchall()
                    ),
                    tuple(
                        row[0]
                        for row in conn.execute(
                            f"SELECT to_jsonb(h) FROM {SCHEMA}.supplier_offer_selection_heads h "
                            "ORDER BY variant_id,selection_scope"
                        ).fetchall()
                    ),
                )

        def race(
            requests: tuple[dict, dict], *, expected_contended_lock: str
        ) -> tuple[list[dict | PersistentMappingError], list[int], set[int]]:
            barrier = Barrier(2)
            contention_observed = Event()
            failed_lock_pids: set[int] = set()
            recorder_pids: list[int] = []
            original_try_lock = mapping_service._try_session_lock
            original_record = mapping_service.record_routine_offer_selection

            def observed_try_lock(conn, lock_name: str, deadline: float):
                if lock_name != expected_contended_lock:
                    return original_try_lock(conn, lock_name, deadline)
                locked = conn.execute(
                    "SELECT pg_catalog.pg_try_advisory_lock("
                    "pg_catalog.hashtextextended(%s,0))",
                    (lock_name,),
                ).fetchone()[0]
                if locked:
                    return
                failed_lock_pids.add(conn.info.backend_pid)
                contention_observed.set()
                return original_try_lock(conn, lock_name, deadline)

            def hold_first_recorder_until_contention(conn, **kwargs):
                recorder_pids.append(conn.info.backend_pid)
                if not contention_observed.wait(timeout=10):
                    raise AssertionError(
                        "the competing selection never failed the real shared session lock"
                    )
                return original_record(conn, **kwargs)

            def invoke(request: dict) -> dict | PersistentMappingError:
                barrier.wait(timeout=10)
                try:
                    return execute_routine_offer_selection(
                        self.mapping_url,
                        principal=self.selection_principal,
                        effective_from=BUSINESS_DATE,
                        **request,
                    )
                except PersistentMappingError as exc:
                    return exc

            with (
                mock.patch.object(
                    mapping_service,
                    "_try_session_lock",
                    side_effect=observed_try_lock,
                ),
                mock.patch.object(
                    mapping_service,
                    "record_routine_offer_selection",
                    side_effect=hold_first_recorder_until_contention,
                ),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                futures = [pool.submit(invoke, request) for request in requests]
                results = [future.result() for future in futures]
            self.assertTrue(contention_observed.is_set())
            self.assertEqual(len(failed_lock_pids), 1)
            self.assertEqual(len(recorder_pids), 2)
            self.assertEqual(len(set(recorder_pids)), 2)
            return results, recorder_pids, failed_lock_pids

        decision_id = prepare("identical")
        key = uuid4()
        identical_preview = preview(decision_id, key, "concurrent identical selection")
        identical_request = {
            "mapping_decision_id": decision_id,
            "selection_idempotency_key": key,
            "reason": "concurrent identical selection",
            "expected_preview_sha256": identical_preview["preview_sha256"],
        }
        identical, _recorder_pids, _failed_pids = race(
            (identical_request, copy.deepcopy(identical_request)),
            expected_contended_lock=(
                f"persistent-mapping:routine-selection:idempotency:{key}"
            ),
        )
        self.assertTrue(all(isinstance(item, dict) for item in identical))
        self.assertEqual(sum(not item["replayed"] for item in identical), 1)
        self.assertEqual(
            {str(item["selection_event_id"]) for item in identical},
            {str(identical[0]["selection_event_id"])},
        )
        self.assertEqual(
            {item["payload_sha256"] for item in identical},
            {identical[0]["payload_sha256"]},
        )
        identical_state = selection_state()
        self.assertEqual(len(identical_state[0]), 1)
        self.assertEqual(len(identical_state[1]), 1)
        self.assertEqual(identical_state[1][0]["head_version"], 1)
        self.assertEqual(
            identical_state[1][0]["selection_event_id"],
            identical_state[0][0]["selection_event_id"],
        )

        decision_id = prepare("changed-intent")
        changed_key = uuid4()
        first_preview = preview(
            decision_id, changed_key, "concurrent first confirmed selection"
        )
        second_preview = preview(
            decision_id, changed_key, "concurrent changed confirmed selection"
        )
        conflicting, _recorder_pids, _failed_pids = race(
            (
                {
                    "mapping_decision_id": decision_id,
                    "selection_idempotency_key": changed_key,
                    "reason": "concurrent first confirmed selection",
                    "expected_preview_sha256": first_preview["preview_sha256"],
                },
                {
                    "mapping_decision_id": decision_id,
                    "selection_idempotency_key": changed_key,
                    "reason": "concurrent changed confirmed selection",
                    "expected_preview_sha256": second_preview["preview_sha256"],
                },
            ),
            expected_contended_lock=(
                f"persistent-mapping:routine-selection:idempotency:{changed_key}"
            ),
        )
        changed_winners = [item for item in conflicting if isinstance(item, dict)]
        changed_losers = [
            item for item in conflicting if isinstance(item, PersistentMappingError)
        ]
        self.assertEqual(len(changed_winners), 1, conflicting)
        self.assertEqual(len(changed_losers), 1, conflicting)
        self.assertEqual(changed_losers[0].code, "IDEMPOTENCY_CONFLICT")
        changed_state = selection_state()
        self.assertEqual(len(changed_state[0]), 1)
        self.assertEqual(len(changed_state[1]), 1)
        self.assertEqual(changed_state[1][0]["head_version"], 1)
        self.assertEqual(
            changed_state[0][0]["reason"], changed_winners[0]["reason"]
        )

        decision_id = prepare("same-prior-head")
        keys = (uuid4(), uuid4())
        reasons = (
            "first contender from empty head",
            "second contender from empty head",
        )
        previews = tuple(
            preview(decision_id, selection_key, reason)
            for selection_key, reason in zip(keys, reasons, strict=True)
        )
        competing, _recorder_pids, _failed_pids = race(
            tuple(
                {
                    "mapping_decision_id": decision_id,
                    "selection_idempotency_key": selection_key,
                    "reason": reason,
                    "expected_preview_sha256": confirmed["preview_sha256"],
                }
                for selection_key, reason, confirmed in zip(
                    keys, reasons, previews, strict=True
                )
            ),
            expected_contended_lock="persistent-mapping:selection-variant:1001",
        )
        prior_winners = [item for item in competing if isinstance(item, dict)]
        prior_losers = [
            item for item in competing if isinstance(item, PersistentMappingError)
        ]
        self.assertEqual(len(prior_winners), 1, competing)
        self.assertEqual(len(prior_losers), 1, competing)
        self.assertEqual(prior_losers[0].code, "MAPPING_REFUSED")
        self.assertIn("selection preview is stale", str(prior_losers[0]))
        prior_state = selection_state()
        self.assertEqual(len(prior_state[0]), 1)
        self.assertEqual(len(prior_state[1]), 1)
        self.assertEqual(prior_state[1][0]["head_version"], 1)
        self.assertEqual(
            prior_state[1][0]["selection_event_id"],
            prior_state[0][0]["selection_event_id"],
        )
        winner_key = str(prior_winners[0]["selection_idempotency_key"])
        losing_key = next(key for key in keys if str(key) != winner_key)
        with psycopg.connect(self.mapping_url) as conn:
            self.assertIsNone(
                conn.execute(
                    f"SELECT selection_event_id FROM "
                    f"{SCHEMA}.supplier_offer_selection_events "
                    "WHERE selection_idempotency_key=%s",
                    (losing_key,),
                ).fetchone()
            )

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
        self.assertFalse(
            any(
                "session_replication_role" in item
                or "pg_prepared_xacts" in item
                for item in retry_connection.statements
            ),
            retry_connection.statements,
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

        # The remaining cases deliberately use real PostgreSQL transactions,
        # errors, backend PIDs, and session locks.  The small connection wrapper
        # only controls what the client observes at COMMIT; it delegates every
        # SQL statement and state transition to Psycopg/PostgreSQL.
        real_connect = psycopg.connect
        original_record = mapping_service.record_mapping_decision
        original_try_lock = mapping_service._try_session_lock

        class CommitInterrupted(BaseException):
            pass

        class RealConnectionWrapper:
            def __init__(
                self,
                raw,
                *,
                label: str,
                commit_mode: str = "normal",
                events: list[str] | None = None,
                rollback_hook=None,
            ):
                self.raw = raw
                self.label = label
                self.backend_pid = raw.info.backend_pid
                self.commit_mode = commit_mode
                self.events = events if events is not None else []
                self.rollback_hook = rollback_hook
                self.statements: list[str] = []
                self.executions: list[tuple[str, object]] = []

            @property
            def closed(self):
                return self.raw.closed

            @property
            def info(self):
                return self.raw.info

            def execute(self, statement, parameters=()):
                rendered = str(statement)
                self.statements.append(rendered)
                self.executions.append((rendered, parameters))
                result = self.raw.execute(statement, parameters)
                if "current_database" in rendered:
                    self.events.append(f"verify:{self.label}")
                if "pg_advisory_unlock" in rendered:
                    self.events.append(
                        f"unlock:{self.label}:{parameters[0]}"
                    )
                return result

            def cursor(self, *args, **kwargs):
                return self.raw.cursor(*args, **kwargs)

            def commit(self):
                self.events.append(f"commit:{self.label}")
                mode, self.commit_mode = self.commit_mode, "normal"
                if mode == "commit_then_operational":
                    self.raw.commit()
                    raise psycopg.OperationalError(
                        "row33 lost response after real commit"
                    )
                if mode == "rollback_then_operational":
                    self.raw.rollback()
                    raise psycopg.OperationalError(
                        "row33 lost response before real commit"
                    )
                if mode == "commit_then_interrupt":
                    self.raw.commit()
                    raise CommitInterrupted(
                        "row33 interrupt after real commit"
                    )
                self.raw.commit()

            def rollback(self):
                before = self.raw.info.transaction_status.name
                self.raw.rollback()
                after = self.raw.info.transaction_status.name
                if self.rollback_hook is not None:
                    self.rollback_hook(self, before, after)

            def close(self):
                self.events.append(f"close:{self.label}")
                self.raw.close()

            def __getattr__(self, name):
                return getattr(self.raw, name)

        def decision_count(key: UUID) -> int:
            with real_connect(self.mapping_url) as observer:
                return int(
                    observer.execute(
                        f"SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions "
                        "WHERE decision_idempotency_key=%s",
                        (key,),
                    ).fetchone()[0]
                )

        def service_lock_count(pid: int) -> int:
            with real_connect(self.admin_url) as observer:
                return int(
                    observer.execute(
                        "SELECT count(*) FROM pg_catalog.pg_locks "
                        "WHERE locktype='advisory' AND pid=%s AND granted",
                        (pid,),
                    ).fetchone()[0]
                )

        def prepare_defer(label: str) -> dict[str, object]:
            candidate = self._intake(
                self._packet(
                    0,
                    occurrence=f"row33-{label}",
                    package_id=f"row33-{label}-package",
                )
            )
            key = uuid4()
            reason = f"row33 real PostgreSQL {label}"
            preview = self._preview_decision(
                candidate,
                action="DEFER",
                reason=reason,
                key=key,
            )
            return {
                "candidate_id": candidate,
                "action": "DEFER",
                "reason": reason,
                "principal": self.principal,
                "decision_idempotency_key": key,
                "expected_preview_sha256": preview["preview_sha256"],
            }

        def raise_server_error(conn, sqlstate: str, label: str) -> None:
            conn.execute(
                sql.SQL(
                    "DO $row33$ BEGIN RAISE EXCEPTION {} USING ERRCODE={}; "
                    "END $row33$;"
                ).format(sql.Literal(label), sql.Literal(sqlstate))
            )

        # Two genuine PostgreSQL aborts are followed by one successful fresh
        # SERIALIZABLE snapshot on the same backend.  Independent commits made
        # between rollbacks become visible as 0 -> 1 -> 2, while every aborted
        # decision insert remains absent and both outer session locks stay held.
        retry_arguments = prepare_defer("retry-fresh-snapshots")
        retry_key = retry_arguments["decision_idempotency_key"]
        retry_marker = f"row33-retry-marker:{retry_key}"
        with real_connect(self.mapping_url) as observer:
            observer.execute(
                f"INSERT INTO {SCHEMA}.meta(key,value) VALUES(%s,'0')",
                (retry_marker,),
            )
        retry_attempts: list[tuple[str, int, str, str]] = []
        retry_rollbacks: list[tuple[str, str, int, int]] = []

        def retry_rollback_hook(wrapper, before: str, after: str) -> None:
            retry_rollbacks.append(
                (
                    before,
                    after,
                    decision_count(retry_key),
                    service_lock_count(wrapper.backend_pid),
                )
            )
            with real_connect(self.mapping_url) as observer:
                observer.execute(
                    f"UPDATE {SCHEMA}.meta SET value=%s WHERE key=%s",
                    (str(len(retry_rollbacks)), retry_marker),
                )

        retry_raw = real_connect(self.mapping_url, autocommit=True)
        retry_wrapper = RealConnectionWrapper(
            retry_raw,
            label="retry",
            rollback_hook=retry_rollback_hook,
        )

        def retry_record(conn, **kwargs):
            result = original_record(conn, **kwargs)
            xid, isolation, marker = conn.execute(
                f"SELECT pg_catalog.pg_current_xact_id()::text,"
                "current_setting('transaction_isolation'),"
                f"(SELECT value FROM {SCHEMA}.meta WHERE key=%s)",
                (retry_marker,),
            ).fetchone()
            retry_attempts.append(
                (str(xid), conn.info.backend_pid, str(marker), str(isolation))
            )
            if len(retry_attempts) == 1:
                raise_server_error(conn, "40001", "row33 serialization retry")
            if len(retry_attempts) == 2:
                raise_server_error(conn, "40P01", "row33 deadlock retry")
            self.assertEqual(isolation, "serializable")
            return result

        with (
            mock.patch.object(
                mapping_service.psycopg, "connect", return_value=retry_wrapper
            ),
            mock.patch.object(
                mapping_service,
                "record_mapping_decision",
                side_effect=retry_record,
            ),
        ):
            retried = execute_mapping_decision(
                self.mapping_url, **retry_arguments
            )
        self.assertFalse(retried["replayed"])
        self.assertEqual([item[2] for item in retry_attempts], ["0", "1", "2"])
        self.assertEqual({item[3] for item in retry_attempts}, {"serializable"})
        self.assertEqual(len({item[0] for item in retry_attempts}), 3)
        self.assertEqual(
            {item[1] for item in retry_attempts}, {retry_wrapper.backend_pid}
        )
        self.assertEqual(
            retry_rollbacks,
            [("INERROR", "IDLE", 0, 2), ("INERROR", "IDLE", 0, 2)],
        )
        self.assertEqual(decision_count(retry_key), 1)
        self.assertEqual(service_lock_count(retry_wrapper.backend_pid), 0)

        # Three real aborted transactions exhaust the exact retry budget.  No
        # decision becomes visible and the session locks survive each rollback
        # only until final reverse-order cleanup closes the backend.
        exhaustion_arguments = prepare_defer("retry-exhaustion")
        exhaustion_key = exhaustion_arguments["decision_idempotency_key"]
        exhaustion_attempts: list[tuple[str, int]] = []
        exhaustion_rollbacks: list[tuple[str, str, int, int]] = []

        def exhaustion_rollback_hook(wrapper, before: str, after: str) -> None:
            exhaustion_rollbacks.append(
                (
                    before,
                    after,
                    decision_count(exhaustion_key),
                    service_lock_count(wrapper.backend_pid),
                )
            )

        exhaustion_raw = real_connect(self.mapping_url, autocommit=True)
        exhaustion_wrapper = RealConnectionWrapper(
            exhaustion_raw,
            label="exhaustion",
            rollback_hook=exhaustion_rollback_hook,
        )
        exhaustion_sqlstates = ("40001", "40P01", "40001")

        def exhaustion_record(conn, **kwargs):
            result = original_record(conn, **kwargs)
            xid = str(
                conn.execute(
                    "SELECT pg_catalog.pg_current_xact_id()::text"
                ).fetchone()[0]
            )
            exhaustion_attempts.append((xid, conn.info.backend_pid))
            raise_server_error(
                conn,
                exhaustion_sqlstates[len(exhaustion_attempts) - 1],
                "row33 retry exhaustion",
            )
            return result

        before_exhaustion = self._counts()
        with (
            mock.patch.object(
                mapping_service.psycopg,
                "connect",
                return_value=exhaustion_wrapper,
            ),
            mock.patch.object(
                mapping_service,
                "record_mapping_decision",
                side_effect=exhaustion_record,
            ),
            self.assertRaises(PersistentMappingError) as exhausted_error,
        ):
            execute_mapping_decision(self.mapping_url, **exhaustion_arguments)
        self.assertEqual(
            exhausted_error.exception.code,
            "CONCURRENT_TRANSACTION_RETRY_EXHAUSTED",
        )
        self.assertEqual(len(exhaustion_attempts), 3)
        self.assertEqual(len({item[0] for item in exhaustion_attempts}), 3)
        self.assertEqual(
            {item[1] for item in exhaustion_attempts},
            {exhaustion_wrapper.backend_pid},
        )
        self.assertEqual(
            exhaustion_rollbacks,
            [("INERROR", "IDLE", 0, 2)] * 3,
        )
        self.assertEqual(decision_count(exhaustion_key), 0)
        self.assertEqual(self._counts(), before_exhaustion)
        self.assertEqual(service_lock_count(exhaustion_wrapper.backend_pid), 0)

        # Cancellation is delivered by PostgreSQL to a backend that has already
        # inserted the decision in its still-uncommitted transaction.
        cancellation_arguments = prepare_defer("backend-cancellation")
        cancellation_key = cancellation_arguments["decision_idempotency_key"]
        cancellation_ready = Event()
        cancellation_pid: list[int] = []

        def sleeping_record(conn, **kwargs):
            result = original_record(conn, **kwargs)
            cancellation_pid.append(conn.info.backend_pid)
            cancellation_ready.set()
            conn.execute("SELECT pg_catalog.pg_sleep(30)")
            return result

        def invoke_cancellation():
            try:
                return execute_mapping_decision(
                    self.mapping_url, **cancellation_arguments
                )
            except BaseException as exc:
                return exc

        before_cancellation = self._counts()
        with (
            mock.patch.object(
                mapping_service,
                "record_mapping_decision",
                side_effect=sleeping_record,
            ),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            cancelled_future = pool.submit(invoke_cancellation)
            self.assertTrue(cancellation_ready.wait(timeout=10))
            pid = cancellation_pid[0]
            activity_deadline = time.monotonic() + 5
            activity = None
            while time.monotonic() < activity_deadline:
                with real_connect(self.admin_url) as observer:
                    activity = observer.execute(
                        "SELECT state,query FROM pg_catalog.pg_stat_activity "
                        "WHERE pid=%s",
                        (pid,),
                    ).fetchone()
                if activity is not None and activity[0] == "active" and "pg_sleep" in activity[1]:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(activity)
            self.assertEqual(activity[0], "active")
            self.assertIn("pg_sleep", activity[1])
            self.assertGreaterEqual(service_lock_count(pid), 2)
            with real_connect(self.admin_url) as observer:
                self.assertTrue(
                    observer.execute(
                        "SELECT pg_catalog.pg_cancel_backend(%s)", (pid,)
                    ).fetchone()[0]
                )
            cancellation_result = cancelled_future.result(timeout=10)
        self.assertIsInstance(cancellation_result, PersistentMappingError)
        self.assertEqual(cancellation_result.code, "DATABASE_VALIDATION_REFUSED")
        self.assertEqual(cancellation_result.__cause__.sqlstate, "57014")
        self.assertEqual(decision_count(cancellation_key), 0)
        self.assertEqual(self._counts(), before_cancellation)
        self.assertEqual(service_lock_count(pid), 0)

        # A holder owns the exact decision-scope lock.  The service backend
        # demonstrably acquires its idempotency lock, waits the reviewed five
        # seconds for the domain lock, never calls the operation, then closes.
        timeout_arguments = prepare_defer("real-session-timeout")
        timeout_key = timeout_arguments["decision_idempotency_key"]
        with real_connect(self.mapping_url) as observer:
            decision_scope = observer.execute(
                f"SELECT decision_scope_sha256 FROM "
                f"{SCHEMA}.supplier_mapping_review_candidates WHERE candidate_id=%s",
                (timeout_arguments["candidate_id"],),
            ).fetchone()[0]
        domain_lock = f"persistent-mapping:decision-scope:{decision_scope}"
        timeout_pid: list[int] = []
        timeout_connected = Event()
        timeout_operation_calls: list[bool] = []

        def timeout_connect(*args, **kwargs):
            raw = real_connect(*args, **kwargs)
            timeout_pid.append(raw.info.backend_pid)
            timeout_connected.set()
            return raw

        def timeout_operation(*args, **kwargs):
            timeout_operation_calls.append(True)
            return original_record(*args, **kwargs)

        def invoke_timeout():
            started_at = time.monotonic()
            try:
                result = execute_mapping_decision(
                    self.mapping_url, **timeout_arguments
                )
            except BaseException as exc:
                return exc, time.monotonic() - started_at
            return result, time.monotonic() - started_at

        holder = real_connect(self.mapping_url, autocommit=True)
        holder.execute(
            "SELECT pg_catalog.pg_advisory_lock("
            "pg_catalog.hashtextextended(%s,0))",
            (domain_lock,),
        )
        try:
            with (
                mock.patch.object(
                    mapping_service.psycopg,
                    "connect",
                    side_effect=timeout_connect,
                ),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    side_effect=timeout_operation,
                ),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                timeout_future = pool.submit(invoke_timeout)
                self.assertTrue(timeout_connected.wait(timeout=10))
                wait_deadline = time.monotonic() + 5
                observed_idempotency_lock = False
                while time.monotonic() < wait_deadline:
                    if service_lock_count(timeout_pid[0]) == 1:
                        observed_idempotency_lock = True
                        break
                    time.sleep(0.01)
                timeout_result, timeout_elapsed = timeout_future.result(timeout=10)
        finally:
            holder.execute(
                "SELECT pg_catalog.pg_advisory_unlock("
                "pg_catalog.hashtextextended(%s,0))",
                (domain_lock,),
            )
            holder.close()
        self.assertTrue(observed_idempotency_lock)
        self.assertIsInstance(timeout_result, PersistentMappingError)
        self.assertEqual(timeout_result.code, "SESSION_LOCK_TIMEOUT")
        self.assertEqual(mapping_service.SESSION_LOCK_WAIT_SECONDS, 5.0)
        self.assertGreaterEqual(timeout_elapsed, 4.5)
        self.assertLess(timeout_elapsed, 10.0)
        self.assertEqual(timeout_operation_calls, [])
        self.assertEqual(decision_count(timeout_key), 0)
        self.assertEqual(service_lock_count(timeout_pid[0]), 0)

        def run_commit_recovery(
            label: str, commit_mode: str
        ) -> tuple[dict, list[RealConnectionWrapper], list[str], list[str]]:
            arguments = prepare_defer(label)
            key = arguments["decision_idempotency_key"]
            with real_connect(self.mapping_url) as observer:
                decision_scope = observer.execute(
                    f"SELECT decision_scope_sha256 FROM "
                    f"{SCHEMA}.supplier_mapping_review_candidates "
                    "WHERE candidate_id=%s",
                    (arguments["candidate_id"],),
                ).fetchone()[0]
            expected_locks = (
                f"persistent-mapping:mapping-decision:idempotency:{key}",
                f"persistent-mapping:decision-scope:{decision_scope}",
            )
            wrappers: list[RealConnectionWrapper] = []
            events: list[str] = []
            xids: list[str] = []

            def connect_wrapper(*args, **kwargs):
                wrapper_label = "first" if not wrappers else "recovery"
                raw = real_connect(*args, **kwargs)
                wrapper = RealConnectionWrapper(
                    raw,
                    label=wrapper_label,
                    commit_mode=(commit_mode if not wrappers else "normal"),
                    events=events,
                )
                wrappers.append(wrapper)
                events.append(f"connect:{wrapper_label}")
                return wrapper

            def observed_try_lock(conn, lock_name: str, deadline: float):
                events.append(f"lock:{conn.label}:{lock_name}")
                return original_try_lock(conn, lock_name, deadline)

            def observed_exists(conn, **kwargs):
                events.append(f"lookup:{conn.label}")
                self.assertEqual(service_lock_count(conn.backend_pid), 2)
                return original_exists(conn, **kwargs)

            def observed_record(conn, **kwargs):
                events.append(f"operation:{conn.label}")
                xids.append(
                    str(
                        conn.execute(
                            "SELECT pg_catalog.pg_current_xact_id()::text"
                        ).fetchone()[0]
                    )
                )
                return original_record(conn, **kwargs)

            original_exists = mapping_service._idempotency_row_exists
            with (
                mock.patch.object(
                    mapping_service.psycopg,
                    "connect",
                    side_effect=connect_wrapper,
                ),
                    mock.patch.object(
                        mapping_service,
                        "_try_session_lock",
                        side_effect=observed_try_lock,
                    ),
                    mock.patch.object(
                    mapping_service,
                    "_idempotency_row_exists",
                    side_effect=observed_exists,
                ),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    side_effect=observed_record,
                ),
            ):
                result = execute_mapping_decision(
                    self.mapping_url, **arguments
                )
            self.assertEqual(decision_count(key), 1)
            self.assertEqual(len(wrappers), 2)
            self.assertEqual(len(xids), 2)
            self.assertEqual(
                [
                    event
                    for event in events
                    if event.startswith("lock:first:")
                ],
                [f"lock:first:{lock_name}" for lock_name in expected_locks],
            )
            self.assertEqual(
                [
                    event
                    for event in events
                    if event.startswith("lock:recovery:")
                ],
                [
                    f"lock:recovery:{lock_name}"
                    for lock_name in expected_locks
                ],
            )
            self.assertEqual(
                [
                    event
                    for event in events
                    if event.startswith("unlock:first:")
                ],
                [],
            )
            self.assertEqual(
                [
                    event
                    for event in events
                    if event.startswith("unlock:recovery:")
                ],
                [
                    f"unlock:recovery:{lock_name}"
                    for lock_name in reversed(expected_locks)
                ],
            )
            ordered_events = (
                "connect:first",
                "verify:first",
                f"lock:first:{expected_locks[0]}",
                f"lock:first:{expected_locks[1]}",
                "operation:first",
                "commit:first",
                "close:first",
                "connect:recovery",
                "verify:recovery",
                f"lock:recovery:{expected_locks[0]}",
                f"lock:recovery:{expected_locks[1]}",
                "lookup:recovery",
                "operation:recovery",
                f"unlock:recovery:{expected_locks[1]}",
                f"unlock:recovery:{expected_locks[0]}",
                "close:recovery",
            )
            self.assertEqual(
                [events.index(event) for event in ordered_events],
                sorted(events.index(event) for event in ordered_events),
            )
            self.assertEqual(events.count("operation:first"), 1)
            self.assertEqual(events.count("operation:recovery"), 1)
            self.assertEqual(events.count("lookup:recovery"), 1)
            for index, wrapper in enumerate(wrappers):
                actual_unlocks = [
                    (statement, parameters)
                    for statement, parameters in wrapper.executions
                    if "pg_advisory_unlock" in statement
                ]
                if index == 0:
                    self.assertEqual(actual_unlocks, [])
                else:
                    self.assertEqual(
                        [parameters for _statement, parameters in actual_unlocks],
                        [(expected_locks[1],), (expected_locks[0],)],
                    )
                    self.assertTrue(
                        all(
                            statement
                            == "SELECT pg_catalog.pg_advisory_unlock("
                            "pg_catalog.hashtextextended(%s,0))"
                            for statement, _parameters in actual_unlocks
                        )
                    )
                self.assertTrue(wrapper.closed)
                self.assertEqual(service_lock_count(wrapper.backend_pid), 0)
            return result, wrappers, events, xids

        landed, landed_wrappers, landed_events, landed_xids = run_commit_recovery(
            "commit-response-lost-after-landed",
            "commit_then_operational",
        )
        self.assertTrue(landed["replayed"])
        self.assertEqual(len(landed_wrappers), 2)
        self.assertEqual(len(landed_xids), 2)
        self.assertEqual(len(set(landed_xids)), 2)
        self.assertLess(
            landed_events.index("close:first"),
            landed_events.index("connect:recovery"),
        )
        self.assertLess(
            next(
                index
                for index, value in enumerate(landed_events)
                if value.startswith("lock:recovery:")
            ),
            landed_events.index("lookup:recovery"),
        )
        self.assertNotIn(
            "pg_advisory_unlock", " ".join(landed_wrappers[0].statements)
        )
        self.assertEqual(
            service_lock_count(landed_wrappers[0].backend_pid), 0
        )

        absent, absent_wrappers, absent_events, absent_xids = run_commit_recovery(
            "commit-response-lost-before-landed",
            "rollback_then_operational",
        )
        self.assertFalse(absent["replayed"])
        self.assertEqual(len(absent_wrappers), 2)
        self.assertEqual(len(absent_xids), 2)
        self.assertEqual(len(set(absent_xids)), 2)
        self.assertLess(
            absent_events.index("lookup:recovery"),
            absent_events.index("operation:recovery"),
        )
        self.assertNotIn(
            "pg_advisory_unlock", " ".join(absent_wrappers[0].statements)
        )

        # If the authenticated, re-locked recovery lookup itself is unavailable,
        # the service reports UNKNOWN and performs no second operation.  The
        # durable database distinguishes the genuinely landed and rolled-back
        # first outcomes without the service guessing either one.
        for label, commit_mode, expected_rows in (
            ("unknown-lookup-landed", "commit_then_operational", 1),
            ("unknown-lookup-rolled-back", "rollback_then_operational", 0),
        ):
            arguments = prepare_defer(label)
            key = arguments["decision_idempotency_key"]
            wrappers: list[RealConnectionWrapper] = []
            record_calls: list[int] = []

            def connect_wrapper(*args, **kwargs):
                raw = real_connect(*args, **kwargs)
                wrapper = RealConnectionWrapper(
                    raw,
                    label="first" if not wrappers else "recovery",
                    commit_mode=(commit_mode if not wrappers else "normal"),
                )
                wrappers.append(wrapper)
                return wrapper

            def observed_record(conn, **kwargs):
                record_calls.append(conn.info.backend_pid)
                return original_record(conn, **kwargs)

            def unavailable_lookup(conn, **_kwargs):
                raise_server_error(
                    conn, "08006", "row33 authenticated lookup unavailable"
                )
                return False

            with (
                self.subTest(commit_recovery=label),
                mock.patch.object(
                    mapping_service.psycopg,
                    "connect",
                    side_effect=connect_wrapper,
                ),
                mock.patch.object(
                    mapping_service,
                    "_idempotency_row_exists",
                    side_effect=unavailable_lookup,
                ),
                mock.patch.object(
                    mapping_service,
                    "record_mapping_decision",
                    side_effect=observed_record,
                ),
                self.assertRaises(PersistentMappingError) as lookup_error,
            ):
                execute_mapping_decision(self.mapping_url, **arguments)
            self.assertEqual(
                lookup_error.exception.code, "COMMIT_OUTCOME_UNKNOWN"
            )
            self.assertEqual(len(wrappers), 2)
            self.assertEqual(len(record_calls), 1)
            self.assertEqual(decision_count(key), expected_rows)
            for wrapper in wrappers:
                self.assertEqual(
                    service_lock_count(wrapper.backend_pid), 0
                )

        # A process-level BaseException raised after a real COMMIT is also
        # ambiguous.  It is converted to the typed unknown result, never leaks
        # raw, never replays, and the uncertain backend is discarded without an
        # explicit unlock (close releases the two session locks).
        interrupt_arguments = prepare_defer("commit-interrupt-after-landed")
        interrupt_key = interrupt_arguments["decision_idempotency_key"]
        interrupt_wrappers: list[RealConnectionWrapper] = []
        interrupt_record_calls: list[int] = []

        def interrupt_connect(*args, **kwargs):
            wrapper = RealConnectionWrapper(
                real_connect(*args, **kwargs),
                label="interrupt",
                commit_mode="commit_then_interrupt",
            )
            interrupt_wrappers.append(wrapper)
            return wrapper

        def interrupt_record(conn, **kwargs):
            interrupt_record_calls.append(conn.info.backend_pid)
            return original_record(conn, **kwargs)

        with (
            mock.patch.object(
                mapping_service.psycopg,
                "connect",
                side_effect=interrupt_connect,
            ),
            mock.patch.object(
                mapping_service,
                "record_mapping_decision",
                side_effect=interrupt_record,
            ),
            self.assertRaises(PersistentMappingError) as interrupt_error,
        ):
            execute_mapping_decision(self.mapping_url, **interrupt_arguments)
        self.assertEqual(
            interrupt_error.exception.code, "COMMIT_OUTCOME_UNKNOWN"
        )
        self.assertIsInstance(interrupt_error.exception.__cause__, CommitInterrupted)
        self.assertEqual(len(interrupt_wrappers), 1)
        self.assertEqual(len(interrupt_record_calls), 1)
        self.assertEqual(decision_count(interrupt_key), 1)
        self.assertNotIn(
            "pg_advisory_unlock",
            " ".join(interrupt_wrappers[0].statements),
        )
        self.assertEqual(
            service_lock_count(interrupt_wrappers[0].backend_pid), 0
        )

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
        offer, price_id = self._reset_with_grandfathered_current_price(
            sku="LEGACY-ACTIVE-SELECT"
        )
        candidate_id = self._intake(
            self._packet(
                1,
                occurrence="legacy-active-select",
                package_id="legacy-active-select-package",
                candidate_changes={
                    "supplier_code_value": "LEGACY-ACTIVE-SELECT",
                    "distributor_product_id_value": "LEGACY-ACTIVE-SELECT",
                },
            )
        )
        decision = self._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="link exact active legacy offer",
            offer_id=offer,
            link_kind="LINKED_EXISTING",
        )
        decision_id = UUID(str(decision["mapping_decision_id"]))

        def nonselection_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(b) FROM {SCHEMA}.supplier_mapping_review_batches b "
                            "ORDER BY review_batch_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(c) FROM {SCHEMA}.supplier_mapping_review_candidates c "
                            "ORDER BY candidate_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                            "ORDER BY mapping_decision_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                            "ORDER BY offer_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(p) FROM {SCHEMA}.prices p ORDER BY price_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.mapping_rejections r "
                            "ORDER BY rejection_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.procurement_recommendations r "
                            "ORDER BY recommendation_id"
                        ).fetchall()
                    ),
                )

        before = nonselection_state()
        selection_key = uuid4()
        reason = "separate active legacy selection"
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=decision_id,
                principal=self.selection_principal,
                selection_idempotency_key=selection_key,
                reason=reason,
                effective_from=BUSINESS_DATE,
            )
            fixed_hashes = conn.execute(
                f"SELECT {SCHEMA}.persistent_mapping_json_sha256(d.canonical_payload),"
                f"{SCHEMA}.persistent_mapping_offer_fingerprint(%s),"
                f"{SCHEMA}.persistent_mapping_catalog_fingerprint('1001'),"
                f"{SCHEMA}.persistent_mapping_vendor_fingerprint(%s),"
                f"{SCHEMA}.persistent_mapping_rejection_fingerprint("
                f"%s,'persistent-mapping:'||d.supplier_identity_key_sha256,NULL) "
                f"FROM {SCHEMA}.supplier_mapping_decisions d "
                "WHERE d.mapping_decision_id=%s",
                (offer, VENDOR_ID, VENDOR_ID, decision_id),
            ).fetchone()
        self.assertEqual(
            tuple(
                preview[key]
                for key in (
                    "expected_mapping_decision_sha256",
                    "expected_offer_contract_sha256",
                    "expected_catalog_sha256",
                    "expected_vendor_sha256",
                    "expected_rejection_memory_sha256",
                )
            ),
            fixed_hashes,
        )
        selected = execute_routine_offer_selection(
            self.mapping_url,
            mapping_decision_id=decision_id,
            principal=self.selection_principal,
            selection_idempotency_key=selection_key,
            reason=reason,
            effective_from=BUSINESS_DATE,
            expected_preview_sha256=preview["preview_sha256"],
        )
        self.assertEqual(nonselection_state(), before)
        with psycopg.connect(self.mapping_url) as conn:
            row = conn.execute(
                f"SELECT to_jsonb(e),to_jsonb(h),"
                "s.selection_state,s.shadow_comparison,"
                "s.recommendation_cutover_enabled,s.legacy_active_standard_count,"
                f"(SELECT to_jsonb(p) FROM {SCHEMA}.prices p WHERE p.price_id=%s) "
                f"FROM {SCHEMA}.supplier_offer_selection_events e "
                f"JOIN {SCHEMA}.supplier_offer_selection_heads h "
                "ON h.selection_event_id=e.selection_event_id "
                f"JOIN {SCHEMA}.v_supplier_offer_selection_shadow s "
                "ON s.selection_event_id=e.selection_event_id "
                "WHERE e.selection_event_id=%s",
                (price_id, selected["selection_event_id"]),
            ).fetchone()
        event, head = row[:2]
        self.assertEqual(event["selected_offer_id"], offer)
        self.assertEqual(event["mapping_decision_id"], str(decision_id))
        self.assertEqual(
            tuple(
                event[key]
                for key in (
                    "expected_mapping_decision_sha256",
                    "expected_offer_contract_sha256",
                    "expected_catalog_sha256",
                    "expected_vendor_sha256",
                    "expected_rejection_memory_sha256",
                )
            ),
            fixed_hashes,
        )
        self.assertEqual(head["selection_event_id"], selected["selection_event_id"])
        self.assertEqual(head["head_version"], 1)
        self.assertEqual(row[2:6], ("SELECTED_ACTIVE", "MATCH", False, 1))
        self.assertEqual(row[6], before[4][0][0])

    def test_active_rejection_and_conflicting_evidence_block_approval_and_selection(self):
        shared_code = "SHARED-REJECTION-IDENTITY"
        variant_a_mapping = self._intake(
            self._packet(
                1,
                occurrence="shared-rejection-variant-a-mapping",
                package_id="shared-rejection-variant-a-mapping-package",
                candidate_changes={
                    "supplier_code_value": shared_code,
                    "distributor_product_id_value": shared_code,
                },
            )
        )
        variant_a_rejection = self._intake(
            self._packet(
                1,
                occurrence="shared-rejection-variant-a-rejection",
                package_id="shared-rejection-variant-a-rejection-package",
                candidate_changes={
                    "supplier_code_value": shared_code,
                    "distributor_product_id_value": shared_code,
                },
            )
        )
        variant_b = self._intake(
            self._packet(
                1,
                occurrence="shared-rejection-variant-b",
                package_id="shared-rejection-variant-b-package",
                candidate_changes={
                    "proposed_variant_id": "2002",
                    "supplier_code_value": shared_code,
                    "distributor_product_id_value": shared_code,
                },
            )
        )
        mapped_a = self._decide(
            variant_a_mapping,
            action="APPROVE_MAPPING",
            reason="map exact Variant A before its rejection evidence",
            link_kind="CREATED_INACTIVE",
        )
        mapped_b = self._decide(
            variant_b,
            action="APPROVE_MAPPING",
            reason="map distinct Variant B before rejection evidence",
            link_kind="CREATED_INACTIVE",
        )
        mapped_a_id = UUID(str(mapped_a["mapping_decision_id"]))
        mapped_b_id = UUID(str(mapped_b["mapping_decision_id"]))
        selected_offer_id = int(mapped_b["result_offer_id"])
        stale_a_key = uuid4()
        stale_a_reason = "preview exact Variant A before rejection"
        stale_b_key = uuid4()
        stale_b_reason = "preview Variant B before shared-identity rejection"
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            stale_a_preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=mapped_a_id,
                principal=self.selection_principal,
                selection_idempotency_key=stale_a_key,
                reason=stale_a_reason,
                effective_from=BUSINESS_DATE,
            )
            stale_b_preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=mapped_b_id,
                principal=self.selection_principal,
                selection_idempotency_key=stale_b_key,
                reason=stale_b_reason,
                effective_from=BUSINESS_DATE,
            )

        rejected_a = self._decide(
            variant_a_rejection,
            action="REJECT_MAPPING",
            reason="exact Variant A rejection",
        )

        def authority_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return (
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(d) FROM {SCHEMA}.supplier_mapping_decisions d "
                            "ORDER BY mapping_decision_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(o) FROM {SCHEMA}.supplier_offers o "
                            "ORDER BY offer_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(r) FROM {SCHEMA}.mapping_rejections r "
                            "ORDER BY rejection_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(e) FROM {SCHEMA}.supplier_offer_selection_events e "
                            "ORDER BY selection_event_id"
                        ).fetchall()
                    ),
                    tuple(
                        conn.execute(
                            f"SELECT to_jsonb(h) FROM {SCHEMA}.supplier_offer_selection_heads h "
                            "ORDER BY variant_id,selection_scope"
                        ).fetchall()
                    ),
                )

        after_rejection = authority_state()
        self.assertEqual(len(after_rejection[0]), 3)
        self.assertEqual(len(after_rejection[1]), 2)
        self.assertEqual(len(after_rejection[2]), 1)
        self.assertEqual(after_rejection[3:], ((), ()))
        rejection_row = after_rejection[2][0][0]
        self.assertEqual(
            rejection_row["rejection_id"], rejected_a["result_rejection_id"]
        )
        self.assertEqual(rejection_row["rejected_variant_id"], "1001")
        self.assertEqual(
            rejection_row["source_key"],
            f"persistent-mapping:{mapped_b['supplier_identity_key_sha256']}",
        )
        self.assertTrue(rejection_row["active"])

        for label, decision_id, key, reason, preview in (
            (
                "exact Variant A",
                mapped_a_id,
                stale_a_key,
                stale_a_reason,
                stale_a_preview,
            ),
            (
                "shared-identity Variant B",
                mapped_b_id,
                stale_b_key,
                stale_b_reason,
                stale_b_preview,
            ),
        ):
            with self.subTest(stale_preview=label):
                with self.assertRaises(PersistentMappingError) as stale:
                    execute_routine_offer_selection(
                        self.mapping_url,
                        mapping_decision_id=decision_id,
                        principal=self.selection_principal,
                        selection_idempotency_key=key,
                        reason=reason,
                        effective_from=BUSINESS_DATE,
                        expected_preview_sha256=preview["preview_sha256"],
                    )
                self.assertEqual(stale.exception.code, "MAPPING_REFUSED")
                self.assertEqual(authority_state(), after_rejection)

        with self.assertRaises(PersistentMappingError) as blocked:
            self._decide(
                variant_a_rejection,
                action="APPROVE_MAPPING",
                reason="exact rejection must block Variant A",
                offer_id=int(mapped_a["result_offer_id"]),
                link_kind="LINKED_EXISTING",
            )
        self.assertEqual(blocked.exception.code, "DATABASE_VALIDATION_REFUSED")
        self.assertEqual(authority_state(), after_rejection)

        fresh_a_key = uuid4()
        fresh_a_reason = "freshly confirm exact rejected Variant A"
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            fresh_a_preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=mapped_a_id,
                principal=self.selection_principal,
                selection_idempotency_key=fresh_a_key,
                reason=fresh_a_reason,
                effective_from=BUSINESS_DATE,
            )
        self.assertNotEqual(
            stale_a_preview["expected_rejection_memory_sha256"],
            fresh_a_preview["expected_rejection_memory_sha256"],
        )
        with self.assertRaises(PersistentMappingError) as rejected_selection:
            execute_routine_offer_selection(
                self.mapping_url,
                mapping_decision_id=mapped_a_id,
                principal=self.selection_principal,
                selection_idempotency_key=fresh_a_key,
                reason=fresh_a_reason,
                effective_from=BUSINESS_DATE,
                expected_preview_sha256=fresh_a_preview["preview_sha256"],
            )
        self.assertEqual(
            rejected_selection.exception.code, "DATABASE_VALIDATION_REFUSED"
        )
        self.assertIn(
            "selected offer is stale, ineligible, rejected, or not regular",
            str(rejected_selection.exception.__cause__),
        )
        self.assertEqual(authority_state(), after_rejection)

        fresh_key = uuid4()
        fresh_reason = "freshly confirm distinct Variant B"
        with psycopg.connect(self.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            fresh_preview = preview_routine_offer_selection(
                conn,
                mapping_decision_id=mapped_b_id,
                principal=self.selection_principal,
                selection_idempotency_key=fresh_key,
                reason=fresh_reason,
                effective_from=BUSINESS_DATE,
            )
        self.assertNotEqual(
            stale_b_preview["expected_rejection_memory_sha256"],
            fresh_preview["expected_rejection_memory_sha256"],
        )
        selected = execute_routine_offer_selection(
            self.mapping_url,
            mapping_decision_id=mapped_b_id,
            principal=self.selection_principal,
            selection_idempotency_key=fresh_key,
            reason=fresh_reason,
            effective_from=BUSINESS_DATE,
            expected_preview_sha256=fresh_preview["preview_sha256"],
        )
        final_state = authority_state()
        self.assertEqual(final_state[:3], after_rejection[:3])
        self.assertEqual(len(final_state[3]), 1)
        self.assertEqual(len(final_state[4]), 1)
        with psycopg.connect(self.mapping_url) as conn:
            final = conn.execute(
                f"SELECT o.active,s.selection_state,s.shadow_comparison,"
                "s.recommendation_cutover_enabled,"
                f"(SELECT to_jsonb(r) FROM {SCHEMA}.mapping_rejections r "
                "WHERE r.rejection_id=%s) "
                f"FROM {SCHEMA}.supplier_offers o "
                f"JOIN {SCHEMA}.v_supplier_offer_selection_shadow s "
                "ON s.selected_offer_id=o.offer_id "
                "WHERE o.offer_id=%s AND s.selection_event_id=%s",
                (
                    rejected_a["result_rejection_id"],
                    selected_offer_id,
                    selected["selection_event_id"],
                ),
            ).fetchone()
        self.assertEqual(
            final[:4],
            (
                False,
                "SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION",
                "SELECTED_INACTIVE_NO_LEGACY_CHANGE",
                False,
            ),
        )
        self.assertEqual(final[4], rejection_row)

    def test_mapped_priced_and_referenced_offer_contracts_cannot_be_rewritten(self):
        priced_offer, priced_price = self._reset_with_grandfathered_current_price(
            variant_id="3003", sku="PRICE-PROTECTED"
        )
        packet = self._packet(
            1,
            occurrence="protected-offer",
            package_id="protected-offer-package",
            candidate_changes={
                "supplier_code_value": "MAPPING-PROTECTED",
                "distributor_product_id_value": "MAPPING-PROTECTED",
            },
        )
        candidate_id = self._intake(packet)
        decision = self._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="protect exact mapping result",
            link_kind="CREATED_INACTIVE",
        )
        mapped_offer = int(decision["result_offer_id"])
        referenced_offer = self._legacy_offer(
            variant_id="2002", sku="REFERENCE-PROTECTED"
        )
        reference_run = uuid4()
        reference_po = uuid4()
        with psycopg.connect(self.mapping_url) as conn:
            with conn.transaction():
                conn.execute(
                    f"INSERT INTO {SCHEMA}.runs(run_id,run_type,status,model_version) "
                    "VALUES(%s,'REFERENCE_FIXTURE','COMPLETED','LEGACY_REFERENCE')",
                    (reference_run,),
                )
                recommendation_id = int(
                    conn.execute(
                        f"INSERT INTO {SCHEMA}.procurement_recommendations("
                        "run_id,variant_id,vendor_id,offer_id,recommended_cases,"
                        "recommended_loose_units,reason_code,explanation) "
                        "VALUES(%s,'2002',%s,%s,1,0,'REFERENCE_FIXTURE',"
                        "'authentic referenced-offer fixture') "
                        "RETURNING recommendation_id",
                        (reference_run, VENDOR_ID, referenced_offer),
                    ).fetchone()[0]
                )
                review_id = int(
                    conn.execute(
                        f"INSERT INTO {SCHEMA}.review_decisions("
                        "run_id,recommendation_id,decision_type,scope,action,decided_by) "
                        "VALUES(%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY',"
                        "'ACCEPT','synthetic:matrix-reference-fixture:01') "
                        "RETURNING decision_id",
                        (reference_run, recommendation_id),
                    ).fetchone()[0]
                )
                conn.execute(
                    f"INSERT INTO {SCHEMA}.purchase_orders(po_id,run_id,vendor_id,po_status) "
                    "VALUES(%s,%s,%s,'DRAFT')",
                    (reference_po, reference_run, VENDOR_ID),
                )
                reference_line = int(
                    conn.execute(
                        f"INSERT INTO {SCHEMA}.purchase_order_lines("
                        "po_id,variant_id,offer_id,recommendation_id,review_decision_id,"
                        "supplier_sku,cases,loose_units,ordered_units,line_status,"
                        "reconciliation_status) "
                        "VALUES(%s,'2002',%s,%s,%s,'REFERENCE-PROTECTED',1,0,6,"
                        "'DRAFT','UNKNOWN') RETURNING po_line_id",
                        (
                            reference_po,
                            referenced_offer,
                            recommendation_id,
                            review_id,
                        ),
                    ).fetchone()[0]
                )

        def protected_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return tuple(
                    tuple(
                        row[0]
                        for row in conn.execute(
                            sql.SQL(
                                "SELECT to_jsonb(t) FROM {}.{} t ORDER BY to_jsonb(t)::text"
                            ).format(sql.Identifier(SCHEMA), sql.Identifier(table))
                        ).fetchall()
                    )
                    for table in (
                        "supplier_offers",
                        "prices",
                        "supplier_mapping_decisions",
                        "runs",
                        "procurement_recommendations",
                        "review_decisions",
                        "purchase_orders",
                        "purchase_order_lines",
                        "vendors",
                    )
                )

        before = protected_state()
        cases = (
            (
                "mapped offer update",
                f"UPDATE {SCHEMA}.supplier_offers SET raw_pack='changed' WHERE offer_id=%s",
                (mapped_offer,),
                psycopg.errors.RaiseException,
                "mapped offer identity/activation requires a separately approved transition",
            ),
            (
                "mapped offer delete",
                f"DELETE FROM {SCHEMA}.supplier_offers WHERE offer_id=%s",
                (mapped_offer,),
                psycopg.errors.RaiseException,
                "mapped offer identity/activation requires a separately approved transition",
            ),
            (
                "mapped offer price",
                f"INSERT INTO {SCHEMA}.prices(offer_id,price_state,effective_month,"
                "level_type,unit_price,source_file) "
                "VALUES(%s,'current',DATE '2026-09-01','BASE',1,'forbidden')",
                (mapped_offer,),
                psycopg.errors.RaiseException,
                "inactive mapping result cannot receive operational pricing",
            ),
            (
                "priced vendor update",
                f"UPDATE {SCHEMA}.vendors SET active=false WHERE vendor_id=%s",
                (VENDOR_ID,),
                psycopg.errors.RaiseException,
                "operational pricing freezes its vendor identity and active state",
            ),
            (
                "priced offer update",
                f"UPDATE {SCHEMA}.supplier_offers SET raw_pack='changed' WHERE offer_id=%s",
                (priced_offer,),
                psycopg.errors.RaiseException,
                "active promoted pricing freezes its supplier offer contract",
            ),
            (
                "priced offer delete",
                f"DELETE FROM {SCHEMA}.supplier_offers WHERE offer_id=%s",
                (priced_offer,),
                psycopg.errors.RaiseException,
                "active promoted pricing freezes its supplier offer contract",
            ),
            (
                "promoted price update",
                f"UPDATE {SCHEMA}.prices SET unit_price=6.2500 WHERE price_id=%s",
                (priced_price,),
                psycopg.errors.RaiseException,
                "grandfathered unprovenanced price evidence is immutable",
            ),
            (
                "promoted price delete",
                f"DELETE FROM {SCHEMA}.prices WHERE price_id=%s",
                (priced_price,),
                psycopg.errors.RaiseException,
                "grandfathered unprovenanced price evidence is immutable",
            ),
            (
                "referenced offer update",
                f"UPDATE {SCHEMA}.supplier_offers SET raw_pack='changed' WHERE offer_id=%s",
                (referenced_offer,),
                psycopg.errors.RaiseException,
                "referenced supplier offer identity is immutable",
            ),
            (
                "referenced offer delete",
                f"DELETE FROM {SCHEMA}.supplier_offers WHERE offer_id=%s",
                (referenced_offer,),
                psycopg.errors.ForeignKeyViolation,
                "procurement_recommendations_offer_id_fkey",
            ),
        )
        with psycopg.connect(self.mapping_url) as conn:
            for label, statement, parameters, error_type, message in cases:
                with (
                    self.subTest(case=label),
                    self.assertRaisesRegex(error_type, message),
                    conn.transaction(),
                ):
                    conn.execute(statement, parameters)
                self.assertEqual(protected_state(), before)
        self.assertEqual(before[7][0]["po_line_id"], reference_line)

    def test_unapproved_v5_package_is_never_backfilled_or_relabelled(self):
        self._reset(include_mapping=False)
        self._seed_catalog()
        legacy_offer = self._legacy_offer(sku="LEGACY-ALIAS-EVIDENCE")
        with psycopg.connect(self.mapping_url) as conn:
            legacy_alias = int(
                conn.execute(
                    f"INSERT INTO {SCHEMA}.supplier_aliases("
                    "vendor_id,variant_id,supplier_text,normalized_supplier_text,"
                    "supplier_sku,size_text,pack_text,approved,match_method,notes) "
                    "VALUES(%s,'1001','Legacy Alias Evidence','legacy alias evidence',"
                    "'LEGACY-ALIAS-EVIDENCE','750ML','6x750ML',false,'LEGACY_REVIEW',"
                    "'must not be adopted by V5 migration') RETURNING alias_id",
                    (VENDOR_ID,),
                ).fetchone()[0]
            )
            absent = tuple(
                conn.execute(
                    "SELECT pg_catalog.to_regclass(%s)",
                    (f'{SCHEMA}."{name}"',),
                ).fetchone()[0]
                for name in (
                    "supplier_mapping_review_batches",
                    "supplier_mapping_review_candidates",
                    "supplier_mapping_decisions",
                    "supplier_offer_selection_events",
                    "supplier_offer_selection_heads",
                )
            )
        self.assertEqual(absent, (None,) * 5)

        def legacy_state() -> tuple:
            with psycopg.connect(self.mapping_url) as conn:
                return tuple(
                    tuple(
                        row[0]
                        for row in conn.execute(
                            sql.SQL(
                                "SELECT to_jsonb(t) FROM {}.{} t ORDER BY to_jsonb(t)::text"
                            ).format(sql.Identifier(SCHEMA), sql.Identifier(table))
                        ).fetchall()
                    )
                    for table in (
                        "vendors",
                        "variants",
                        "supplier_offers",
                        "supplier_aliases",
                        "prices",
                        "mapping_rejections",
                        "runs",
                        "procurement_recommendations",
                    )
                )

        before_migration = legacy_state()
        with psycopg.connect(self.mapping_url) as conn:
            with conn.transaction():
                self.assertTrue(
                    apply_schema._verify_or_apply_mapping_release(conn, DB_DIR)
                )
            with conn.transaction():
                self.assertTrue(
                    apply_schema._verify_or_apply_post_mapping_release(conn, DB_DIR)
                )
        self.assertEqual(legacy_state(), before_migration)
        with psycopg.connect(self.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT "
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_batches),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_review_candidates),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_events),"
                    f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads)"
                ).fetchone(),
                (0, 0, 0, 0, 0),
            )

        def reseal_zero_authority(packet: dict) -> None:
            package = packet["package"]
            package["source_batch_sha256"] = _canonical_source_sha256(
                {
                    "source_package_id": package["source_package_id"],
                    "source_revision": package["source_revision"],
                    "occurrence_keys": [
                        item["occurrence_key"] for item in packet["candidates"]
                    ],
                    "source_authority_state": package.get(
                        "source_authority_state"
                    ),
                    "source_import_state": package.get("source_import_state"),
                }
            )
            package["source_seal_sha256"] = _canonical_source_sha256(
                {
                    "source_root_sha256": package["source_root_sha256"],
                    "relationship_table_sha256": package[
                        "relationship_table_sha256"
                    ],
                    "source_batch_sha256": package["source_batch_sha256"],
                }
            )

        zero_authority_before = self._counts()
        for field, unsafe_value, remove in (
            ("source_authority_state", "APPROVED", False),
            ("source_import_state", "IMPORT_READY", False),
            ("source_authority_state", None, False),
            ("source_import_state", None, False),
            ("source_authority_state", None, True),
            ("source_import_state", None, True),
        ):
            unsafe = self._packet(
                1,
                occurrence=f"unsafe-zero-authority-{field}",
                package_id=f"unsafe-zero-authority-{field}-package",
            )
            if remove:
                unsafe["package"].pop(field)
            else:
                unsafe["package"][field] = unsafe_value
            reseal_zero_authority(unsafe)
            with self.subTest(
                unsafe_zero_authority_field=field,
                unsafe_value=unsafe_value,
                removed=remove,
            ):
                with self.assertRaises(PersistentMappingError) as refused:
                    execute_supplier_mapping_intake(
                        self.mapping_url,
                        package=unsafe["package"],
                        candidates=unsafe["candidates"],
                        principal=self.principal,
                        intake_idempotency_key=unsafe["intake_idempotency_key"],
                    )
                self.assertEqual(
                    str(refused.exception),
                    "review package zero-authority contract differs",
                )
                self.assertEqual(self._counts(), zero_authority_before)
                self.assertEqual(legacy_state(), before_migration)

        packet = self._packet(
            1,
            occurrence="v5-remains-unapproved",
            package_id="v5-remains-unapproved-package",
            candidate_changes={
                "supplier_code_value": "V5-NOT-ADOPTED",
                "distributor_product_id_value": "V5-NOT-ADOPTED",
            },
        )
        candidate_id = self._intake(packet)
        self.assertEqual(legacy_state(), before_migration)
        with psycopg.connect(self.mapping_url) as conn:
            row = conn.execute(
                f"SELECT b.source_package_kind,b.source_package_id,"
                "b.source_authority_state,b.source_import_state,"
                "b.canonical_payload->>'source_authority_state',"
                "b.canonical_payload->>'source_import_state',"
                "b.structural_state,b.source_evidence_state,b.semantic_state,"
                "b.source_is_simulation,b.candidate_count,"
                "c.occurrence_key,c.source_file_name,c.supplier_code_value,"
                f"(SELECT count(*) FROM {SCHEMA}.supplier_aliases),"
                f"(SELECT count(*) FROM {SCHEMA}.supplier_offers),"
                f"(SELECT count(*) FROM {SCHEMA}.supplier_mapping_decisions),"
                f"(SELECT count(*) FROM {SCHEMA}.mapping_rejections),"
                f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_events),"
                f"(SELECT count(*) FROM {SCHEMA}.supplier_offer_selection_heads),"
                f"(SELECT count(*) FROM {SCHEMA}.prices),"
                f"(SELECT count(*) FROM {SCHEMA}.procurement_recommendations) "
                f"FROM {SCHEMA}.supplier_mapping_review_batches b "
                f"JOIN {SCHEMA}.supplier_mapping_review_candidates c "
                "USING(review_batch_id) WHERE c.candidate_id=%s",
                (candidate_id,),
            ).fetchone()
        self.assertEqual(
            row,
            (
                "SEALED_V5_REVIEW_PACKAGE",
                "v5-remains-unapproved-package",
                "NOT_APPROVED",
                "NOT_IMPORT_READY",
                "NOT_APPROVED",
                "NOT_IMPORT_READY",
                "READY",
                "READY",
                "READY",
                False,
                1,
                "v5-remains-unapproved",
                packet["candidates"][0]["source_file_name"],
                "V5-NOT-ADOPTED",
                1,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
            ),
        )
        self.assertEqual(before_migration[2][0]["offer_id"], legacy_offer)
        self.assertEqual(before_migration[3][0]["alias_id"], legacy_alias)

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
