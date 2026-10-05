from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest import mock
from uuid import NAMESPACE_URL, UUID, uuid5
import zipfile

import psycopg
from psycopg import sql

from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID, EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID, EXPECTED_PROJECT_ID,
)
from procurement_os import synthetic_staging_database as staging_database
from procurement_os.database_lifecycle import (
    DatabaseLifecycleError,
    acquire_database_lifecycle_lock,
    assert_database_lifecycle_lock,
    database_lifecycle_lock_name,
    release_database_lifecycle_lock,
)
from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE, EXPECTED_PRIVATE_HOST, IMMUTABLE_FIXTURE_MANIFEST_SHA256,
    PERMISSION_MATRIX_SHA256, PREDECESSOR_CATALOG_SHA256, PROVISIONER,
    RUNTIME_LOGIN, SUCCESSOR_CATALOG_SHA256,
    SyntheticStagingDatabaseError, SyntheticStagingTarget,
    attest_provisioner_connection, attest_runtime_connection, bootstrap_roles,
    compute_catalog_sha256, compute_predecessor_catalog_sha256,
    compute_immutable_fixture_sha256, install_transfer_provenance,
    permission_records, provision_contract,
)


class SyntheticStagingDatabaseContractTests(unittest.TestCase):
    TRANSFER_MANIFEST = "a" * 64

    def target(self):
        return SyntheticStagingTarget(
            database_url=f"postgresql://{RUNTIME_LOGIN}@{EXPECTED_PRIVATE_HOST}/{EXPECTED_DATABASE}",
            expected_private_host=EXPECTED_PRIVATE_HOST,
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
            transfer_manifest_sha256=self.TRANSFER_MANIFEST,
        )

    def test_exact_target_is_accepted_without_connecting(self):
        self.target().validate_static()
        self.assertRegex(PERMISSION_MATRIX_SHA256, r"^[0-9a-f]{64}$")

    def test_runtime_attestation_identity_is_source_pinned(self):
        payload = {
            "catalog": staging_database.SUCCESSOR_CATALOG_SHA256,
            "contract": staging_database.CONTRACT_VERSION,
            "database": staging_database.EXPECTED_DATABASE,
            "fixture": staging_database.DEVELOPMENT_FIXTURE_CONTRACT,
            "permission_matrix": staging_database.PERMISSION_MATRIX_SHA256,
            "postgres_major": staging_database.EXPECTED_POSTGRES_MAJOR,
            "schema": staging_database.SCHEMA,
        }
        observed = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
        ).hexdigest()
        self.assertEqual(
            observed,
            staging_database.EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        )

    def test_wrong_scope_host_database_and_login_are_refused(self):
        changes = (
            {"project_id": "wrong"},
            {"expected_private_host": "evil.example"},
            {"transfer_manifest_sha256": "A" * 64},
            {"transfer_manifest_sha256": "a" * 63},
            {
                "database_url": (
                    f"postgresql://{RUNTIME_LOGIN}@unrelated-clone.railway.internal/"
                    f"{EXPECTED_DATABASE}"
                ),
                "expected_private_host": "unrelated-clone.railway.internal",
            },
            {"database_url": "postgresql://buffalo_synthetic_runtime:x@postgres.railway.internal/production"},
            {"database_url": f"postgresql://admin:x@postgres.railway.internal/{EXPECTED_DATABASE}"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(SyntheticStagingDatabaseError):
                replace(self.target(), **change).validate_static()

    def test_permission_matrix_is_explicit_and_default_denies_admin_operations(self):
        records = permission_records()
        self.assertTrue(records)
        self.assertEqual(len(records), len({(r["object"], r["operation"]) for r in records}))
        self.assertFalse({"CREATE", "TEMP", "TRUNCATE", "TRIGGER", "GRANT"} & {r["operation"] for r in records})

    def test_research_and_gateway_contracts_have_no_database_credential(self):
        from procurement_os.staging_process_contract import GATEWAY_ENVIRONMENT_NAMES, WORKER_ENVIRONMENT_NAMES
        for names in (GATEWAY_ENVIRONMENT_NAMES, WORKER_ENVIRONMENT_NAMES["research"]):
            self.assertNotIn("DATABASE_URL", names)
            self.assertNotIn("PGPASSFILE", names)

    def test_fixture_backfill_identity_is_optional_and_validated(self):
        from procurement_os import historical_sales

        conn = mock.MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        generated = UUID("11111111-1111-4111-8111-111111111111")
        cursor.fetchone.return_value = (generated,)
        with mock.patch.object(historical_sales, "assert_catalog_ready"):
            observed = historical_sales.create_sales_backfill_run(
                conn,
                start_date=historical_sales.AUTHORITATIVE_START_DATE,
                end_date=historical_sales.AUTHORITATIVE_START_DATE,
                store_timezone="UTC",
            )
        self.assertEqual(observed, str(generated))
        insert = cursor.execute.call_args_list[0]
        self.assertIn("COALESCE(%s::uuid,gen_random_uuid())", insert.args[0])
        self.assertIsNone(insert.args[1][0])
        with self.assertRaisesRegex(ValueError, "identity is malformed"):
            historical_sales.create_sales_backfill_run(
                object(),
                start_date=historical_sales.AUTHORITATIVE_START_DATE,
                end_date=historical_sales.AUTHORITATIVE_START_DATE,
                store_timezone="UTC",
                fixture_sales_backfill_id="not-a-uuid",
            )


class SyntheticStagingDatabasePostgresTests(unittest.TestCase):
    """One isolated PG16 proves the exact transition and real runtime path."""

    ADMIN = "staging_contract_admin"
    SENTINEL_ROLE = "staging_unrelated_owner"
    SENTINEL_DATABASE = "staging_unrelated_sentinel"
    BUSINESS_DATE = date(2026, 10, 5)
    TRANSFER_MANIFEST = "a" * 64
    PROVISIONER_SECRET = "P" * 48
    RUNTIME_SECRET = "R" * 48

    @classmethod
    def _command(cls, argv: list[str]) -> None:
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("PG")
        }
        result = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        if result.returncode:
            raise RuntimeError(
                f"PostgreSQL test command failed ({argv[0]}): {result.stderr.strip()}"
            )

    @classmethod
    def _stop_cluster(cls) -> None:
        if getattr(cls, "_started", False):
            cls._command(
                [cls._pg_ctl, "-D", str(cls._data), "-m", "fast", "-w", "stop"]
            )
            cls._started = False

    @classmethod
    def _restart_cluster(cls) -> None:
        root = Path(cls._temporary.name)
        cls._command(
            [
                cls._pg_ctl,
                "-D",
                str(cls._data),
                "-l",
                str(root / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {cls._port} -k {root}",
                "-m",
                "fast",
                "-w",
                "restart",
            ]
        )

    @classmethod
    def _sentinel_snapshot(cls) -> tuple[object, ...]:
        with psycopg.connect(cls._sentinel_admin_url) as conn:
            database = conn.execute(
                "SELECT pg_catalog.pg_get_userbyid(datdba),datacl::text,datallowconn "
                "FROM pg_catalog.pg_database WHERE datname=current_database()"
            ).fetchone()
            schema = conn.execute(
                "SELECT pg_catalog.pg_get_userbyid(nspowner),nspacl::text "
                "FROM pg_catalog.pg_namespace WHERE nspname='sentinel'"
            ).fetchone()
            relation = conn.execute(
                "SELECT pg_catalog.pg_get_userbyid(c.relowner),c.relacl::text "
                "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
                "ON n.oid=c.relnamespace "
                "WHERE n.nspname='sentinel' AND c.relname='preserved'"
            ).fetchone()
            rows = conn.execute(
                "SELECT sentinel_key,sentinel_value FROM sentinel.preserved "
                "ORDER BY sentinel_key"
            ).fetchall()
        with psycopg.connect(cls._postgres_admin_url) as conn:
            role = conn.execute(
                "SELECT rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,"
                "rolreplication,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=%s",
                (cls.SENTINEL_ROLE,),
            ).fetchone()
            memberships = conn.execute(
                "SELECT parent.rolname,member.rolname FROM pg_catalog.pg_auth_members m "
                "JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid "
                "JOIN pg_catalog.pg_roles member ON member.oid=m.member "
                "WHERE parent.rolname=%s OR member.rolname=%s ORDER BY 1,2",
                (cls.SENTINEL_ROLE, cls.SENTINEL_ROLE),
            ).fetchall()
        return database, schema, relation, tuple(rows), role, tuple(memberships)

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        binaries = {
            name: shutil.which(name) for name in ("initdb", "pg_ctl")
        }
        if any(value is None for value in binaries.values()):
            raise RuntimeError("PostgreSQL 16 initdb and pg_ctl are required")
        cls._pg_ctl = str(binaries["pg_ctl"])
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="buffalo-staging-contract-postgres-"
        )
        cls.addClassCleanup(cls._temporary.cleanup)
        root = Path(cls._temporary.name)
        cls._data = root / "data"
        log = root / "postgres.log"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls._port = int(listener.getsockname()[1])
        cls._command(
            [
                str(binaries["initdb"]),
                "-D",
                str(cls._data),
                "-U",
                cls.ADMIN,
                "-A",
                "trust",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        cls._command(
            [
                cls._pg_ctl,
                "-D",
                str(cls._data),
                "-l",
                str(log),
                "-o",
                f"-h 127.0.0.1 -p {cls._port} -k {root}",
                "-w",
                "start",
            ]
        )
        cls._started = True
        cls.addClassCleanup(cls._stop_cluster)
        prefix = f"postgresql://{cls.ADMIN}@127.0.0.1:{cls._port}"
        cls._postgres_admin_url = f"{prefix}/postgres"
        cls._target_admin_url = f"{prefix}/{EXPECTED_DATABASE}"
        cls._sentinel_admin_url = f"{prefix}/{cls.SENTINEL_DATABASE}"
        cls._initializer_url = (
            f"postgresql://qa_release_login@127.0.0.1:{cls._port}/"
            f"{EXPECTED_DATABASE}?options=-c%20role%3Dqa_mapping_owner"
        )
        cls._provisioner_url = (
            f"postgresql://{PROVISIONER}@127.0.0.1:{cls._port}/{EXPECTED_DATABASE}"
        )
        cls._runtime_url = (
            f"postgresql://{RUNTIME_LOGIN}@127.0.0.1:{cls._port}/{EXPECTED_DATABASE}"
        )
        cls._sentinel_runtime_url = (
            f"postgresql://{RUNTIME_LOGIN}@127.0.0.1:{cls._port}/"
            f"{cls.SENTINEL_DATABASE}"
        )

        with psycopg.connect(cls._postgres_admin_url, autocommit=True) as conn:
            conn.execute(
                "CREATE ROLE qa_mapping_owner NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            conn.execute(
                "CREATE ROLE qa_release_login LOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            conn.execute(
                "GRANT qa_mapping_owner TO qa_release_login "
                "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
            )
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(cls.SENTINEL_ROLE))
            )
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER qa_mapping_owner").format(
                    sql.Identifier(EXPECTED_DATABASE)
                )
            )
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(cls.SENTINEL_DATABASE),
                    sql.Identifier(cls.SENTINEL_ROLE),
                )
            )
            for database in ("postgres", "template0", "template1", cls.SENTINEL_DATABASE):
                conn.execute(
                    sql.SQL(
                        "REVOKE CONNECT,TEMPORARY ON DATABASE {} FROM PUBLIC"
                    ).format(sql.Identifier(database))
                )
        with psycopg.connect(cls._sentinel_admin_url, autocommit=True) as conn:
            conn.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(cls.SENTINEL_ROLE))
            )
            conn.execute("CREATE SCHEMA sentinel")
            conn.execute(
                "CREATE TABLE sentinel.preserved("
                "sentinel_key text PRIMARY KEY,sentinel_value text NOT NULL)"
            )
            conn.execute(
                "INSERT INTO sentinel.preserved VALUES "
                "('contract','unrelated-state-must-not-change')"
            )
            conn.execute("RESET ROLE")
        cls._sentinel_before = cls._sentinel_snapshot()

        from initialize_synthetic_demo import (
            DEVELOPMENT_FORECAST_V2_PROFILE,
            initialize,
        )

        initialized = initialize(
            cls._initializer_url,
            cls.BUSINESS_DATE,
            profile=DEVELOPMENT_FORECAST_V2_PROFILE,
        )
        if initialized.get("initialized") is not True:
            raise AssertionError("fresh staging fixture was not initialized")
        with psycopg.connect(cls._target_admin_url) as conn:
            conn.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(EXPECTED_DATABASE)
                )
            )
            conn.commit()
        with psycopg.connect(cls._target_admin_url) as conn:
            cls._transfer_changed = install_transfer_provenance(
                conn, cls.TRANSFER_MANIFEST
            )
            conn.commit()
        with psycopg.connect(cls._target_admin_url) as conn:
            cls._transfer_noop = install_transfer_provenance(
                conn, cls.TRANSFER_MANIFEST
            )
            conn.execute("SAVEPOINT transfer_partial")
            conn.execute(
                "DELETE FROM qa_mapping_test.meta "
                "WHERE key=%s",
                (staging_database.TRANSFER_CONTRACT_META_KEY,),
            )
            try:
                install_transfer_provenance(conn, cls.TRANSFER_MANIFEST)
            except SyntheticStagingDatabaseError as exc:
                cls._transfer_partial_failure = str(exc)
            else:
                raise AssertionError("partial transfer provenance was accepted")
            conn.execute("ROLLBACK TO SAVEPOINT transfer_partial")
            conn.execute("SAVEPOINT transfer_conflict")
            conn.execute(
                "UPDATE qa_mapping_test.meta SET value=%s WHERE key=%s",
                ("b" * 64, staging_database.TRANSFER_MANIFEST_META_KEY),
            )
            try:
                install_transfer_provenance(conn, cls.TRANSFER_MANIFEST)
            except SyntheticStagingDatabaseError as exc:
                cls._transfer_conflict_failure = str(exc)
            else:
                raise AssertionError("conflicting transfer provenance was accepted")
            conn.execute("ROLLBACK TO SAVEPOINT transfer_conflict")
            try:
                install_transfer_provenance(conn, "malformed")
            except SyntheticStagingDatabaseError as exc:
                cls._transfer_malformed_failure = str(exc)
            else:
                raise AssertionError("malformed transfer provenance was accepted")
            conn.rollback()
        with psycopg.connect(cls._target_admin_url) as conn:
            cls._bootstrap_changed = bootstrap_roles(
                conn,
                transfer_manifest_sha256=cls.TRANSFER_MANIFEST,
                provisioner_secret=cls.PROVISIONER_SECRET,
                runtime_secret=cls.RUNTIME_SECRET,
            )
            conn.commit()
        with psycopg.connect(cls._target_admin_url) as conn:
            cls._bootstrap_noop = bootstrap_roles(
                conn,
                transfer_manifest_sha256=cls.TRANSFER_MANIFEST,
                provisioner_secret=cls.PROVISIONER_SECRET,
                runtime_secret=cls.RUNTIME_SECRET,
            )
            conn.commit()
        with psycopg.connect(cls._provisioner_url, autocommit=True) as conn:
            try:
                provision_contract(
                    conn,
                    transfer_manifest_sha256=cls.TRANSFER_MANIFEST,
                )
            except SyntheticStagingDatabaseError:
                cls._autocommit_refused = True
            else:
                cls._autocommit_refused = False
        with psycopg.connect(cls._provisioner_url) as conn:
            cls._predecessor = compute_predecessor_catalog_sha256(
                conn, database_acl="fenced"
            )
            conn.rollback()

        real_apply_permissions = staging_database._apply_permissions

        def fail_after_permissions(conn):
            real_apply_permissions(conn)
            raise SyntheticStagingDatabaseError("injected transition failure")

        with psycopg.connect(cls._provisioner_url) as conn:
            try:
                with mock.patch.object(
                    staging_database,
                    "_apply_permissions",
                    side_effect=fail_after_permissions,
                ):
                    provision_contract(
                        conn,
                        transfer_manifest_sha256=cls.TRANSFER_MANIFEST,
                    )
            except SyntheticStagingDatabaseError as exc:
                cls._rollback_failure = str(exc)
                conn.rollback()
            else:
                raise AssertionError("injected transition failure did not fire")
        with psycopg.connect(cls._provisioner_url) as conn:
            cls._post_rollback_catalog = compute_predecessor_catalog_sha256(
                conn, database_acl="fenced"
            )
            cls._post_rollback_markers = staging_database._observed_staging_markers(
                conn
            )
            conn.rollback()
        with psycopg.connect(cls._provisioner_url) as conn:
            cls._provision_changed = provision_contract(
                conn,
                transfer_manifest_sha256=cls.TRANSFER_MANIFEST,
            )
            conn.commit()
        with psycopg.connect(cls._provisioner_url) as conn:
            cls._successor = compute_catalog_sha256(conn)
            cls._sql_successor = conn.execute(
                "SELECT qa_mapping_test.compute_synthetic_staging_catalog_sha256()"
            ).fetchone()[0]
            conn.rollback()

        cls._target = SyntheticStagingTarget(
            database_url=cls._runtime_url,
            expected_private_host="127.0.0.1",
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
            transfer_manifest_sha256=cls.TRANSFER_MANIFEST,
            owned_local_port=cls._port,
        )
        with psycopg.connect(cls._runtime_url) as conn:
            cls._runtime_generation = attest_runtime_connection(conn, cls._target)
            conn.rollback()

    def test_01_exact_transition_and_transactional_rollback(self):
        self.assertTrue(self._transfer_changed)
        self.assertFalse(self._transfer_noop)
        self.assertEqual(
            self._transfer_partial_failure,
            "synthetic staging transfer provenance is partial or conflicting",
        )
        self.assertEqual(
            self._transfer_conflict_failure,
            "synthetic staging transfer provenance is partial or conflicting",
        )
        self.assertEqual(
            self._transfer_malformed_failure,
            "synthetic staging transfer manifest identity is malformed",
        )
        self.assertTrue(self._bootstrap_changed)
        self.assertFalse(self._bootstrap_noop)
        self.assertTrue(self._autocommit_refused)
        self.assertEqual(self._predecessor, PREDECESSOR_CATALOG_SHA256)
        self.assertEqual(self._rollback_failure, "injected transition failure")
        self.assertEqual(self._post_rollback_catalog, PREDECESSOR_CATALOG_SHA256)
        self.assertEqual(self._post_rollback_markers, {})
        self.assertTrue(self._provision_changed)
        self.assertEqual(self._successor, SUCCESSOR_CATALOG_SHA256)
        self.assertEqual(self._sql_successor, SUCCESSOR_CATALOG_SHA256)
        self.assertRegex(self._runtime_generation, r"^[0-9a-f]{64}$")

    def test_02_successor_reapplication_is_verified_noop(self):
        with psycopg.connect(self._provisioner_url) as conn:
            self.assertFalse(
                provision_contract(
                    conn,
                    transfer_manifest_sha256=self.TRANSFER_MANIFEST,
                )
            )
            self.assertEqual(compute_catalog_sha256(conn), SUCCESSOR_CATALOG_SHA256)
            conn.commit()
        source = (
            Path(staging_database.__file__).resolve().parents[2]
            / "db"
            / staging_database.MIGRATION_NAME
        ).read_bytes()
        with tempfile.TemporaryDirectory(
            prefix="buffalo-staging-contract-source-"
        ) as directory:
            altered = Path(directory) / staging_database.MIGRATION_NAME
            altered.write_bytes(source + b"\n")
            with psycopg.connect(self._provisioner_url) as conn:
                with self.assertRaisesRegex(
                    SyntheticStagingDatabaseError, "source hash differs"
                ):
                    provision_contract(
                        conn,
                        transfer_manifest_sha256=self.TRANSFER_MANIFEST,
                        sql_path=altered,
                    )
                conn.rollback()

    def test_03_sql_assertion_rejects_missing_wrong_and_malformed_markers(self):
        mutations = (
            "DELETE FROM qa_mapping_test.meta "
            "WHERE key='synthetic_staging_permission_matrix_sha256'",
            "UPDATE qa_mapping_test.meta SET value='f' || substring(value from 2) "
            "WHERE key='synthetic_staging_fixture_manifest_sha256'",
            "UPDATE qa_mapping_test.meta SET value='malformed' "
            "WHERE key='synthetic_staging_catalog_sha256'",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), psycopg.connect(
                self._target_admin_url
            ) as conn:
                conn.execute(mutation)
                conn.execute(
                    sql.SQL("SET SESSION AUTHORIZATION {}").format(
                        sql.Identifier(RUNTIME_LOGIN)
                    )
                )
                conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
                with self.assertRaises(psycopg.Error):
                    conn.execute(
                        "SELECT qa_mapping_test.assert_synthetic_staging_contract()"
                    )
                conn.rollback()
        with psycopg.connect(self._runtime_url) as conn:
            self.assertEqual(
                attest_runtime_connection(conn, self._target),
                self._runtime_generation,
            )

    def test_04_transfer_provenance_is_required_by_both_attestations(self):
        mutations = (
            "DELETE FROM qa_mapping_test.meta "
            f"WHERE key='{staging_database.TRANSFER_CONTRACT_META_KEY}'",
            "UPDATE qa_mapping_test.meta SET value='b' || substring(value from 2) "
            f"WHERE key='{staging_database.TRANSFER_MANIFEST_META_KEY}'",
            "UPDATE qa_mapping_test.meta SET value='malformed' "
            f"WHERE key='{staging_database.TRANSFER_MANIFEST_META_KEY}'",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), psycopg.connect(
                self._target_admin_url
            ) as conn:
                conn.execute(mutation)
                conn.execute(
                    sql.SQL("SET SESSION AUTHORIZATION {}").format(
                        sql.Identifier(RUNTIME_LOGIN)
                    )
                )
                conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
                with self.assertRaisesRegex(
                    SyntheticStagingDatabaseError,
                    "transfer provenance differs",
                ):
                    attest_runtime_connection(conn, self._target)
                conn.execute("RESET SESSION AUTHORIZATION")
                conn.rollback()

        wrong_target = replace(
            self._target, transfer_manifest_sha256="b" * 64
        )
        with psycopg.connect(self._runtime_url) as conn:
            with self.assertRaisesRegex(
                SyntheticStagingDatabaseError, "transfer provenance differs"
            ):
                attest_runtime_connection(conn, wrong_target)
            conn.rollback()
        with psycopg.connect(self._provisioner_url) as conn:
            with self.assertRaisesRegex(
                SyntheticStagingDatabaseError, "transfer provenance differs"
            ):
                attest_provisioner_connection(conn, wrong_target)
            conn.rollback()

        with psycopg.connect(self._target_admin_url) as conn:
            conn.execute(
                "CREATE OPERATOR qa_mapping_test.=== ("
                "LEFTARG=text,RIGHTARG=text,PROCEDURE=pg_catalog.texteq)"
            )
            conn.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(RUNTIME_LOGIN)
                )
            )
            conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
            with self.assertRaisesRegex(
                SyntheticStagingDatabaseError,
                "semantic catalog envelope differs",
            ):
                attest_runtime_connection(conn, self._target)
            conn.execute("RESET SESSION AUTHORIZATION")
            conn.rollback()

        with psycopg.connect(self._target_admin_url) as conn:
            conn.execute(
                sql.SQL("ALTER DATABASE {} SET application_name={}").format(
                    sql.Identifier(EXPECTED_DATABASE),
                    sql.Literal("tampered-database-default"),
                )
            )
            conn.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(RUNTIME_LOGIN)
                )
            )
            conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
            with self.assertRaisesRegex(
                SyntheticStagingDatabaseError,
                "database security defaults differ",
            ):
                attest_runtime_connection(conn, self._target)
            conn.execute("RESET SESSION AUTHORIZATION")
            conn.rollback()

        dump_semantic_mutations = (
            "CREATE SCHEMA audit_hidden",
            "CREATE STATISTICS public.audit_stats "
            "ON variant_id,vendor_id FROM qa_mapping_test.supplier_offers",
            "ALTER FUNCTION qa_mapping_test."
            "is_operational_current_variant(text) COST 999",
            "ALTER FUNCTION qa_mapping_test."
            "is_operational_current_variant(text) DEPENDS ON EXTENSION pgcrypto",
            "ALTER TRIGGER trg_assert_monday_stale_forecast_retirement_audit_commit "
            "ON qa_mapping_test.change_log DEPENDS ON EXTENSION pgcrypto",
            "ALTER TABLE qa_mapping_test.catalog_reconciliation_items "
            "REPLICA IDENTITY FULL",
            "ALTER TABLE qa_mapping_test.catalog_reconciliation_items "
            "CLUSTER ON catalog_reconciliation_items_pkey",
            "ALTER TABLE qa_mapping_test.catalog_reconciliation_items "
            "SET (toast.autovacuum_enabled=false)",
            "ALTER SEQUENCE qa_mapping_test."
            "catalog_reconciliation_items_reconciliation_item_id_seq "
            "OWNED BY qa_mapping_test.catalog_reconciliation_items.catalog_sync_id",
            "COMMENT ON EXTENSION pgcrypto IS 'altered transfer comment'",
        )
        for mutation in dump_semantic_mutations:
            with self.subTest(mutation=mutation), psycopg.connect(
                self._target_admin_url
            ) as conn:
                conn.execute(mutation)
                conn.execute(
                    sql.SQL("SET SESSION AUTHORIZATION {}").format(
                        sql.Identifier(RUNTIME_LOGIN)
                    )
                )
                conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
                with self.assertRaisesRegex(
                    SyntheticStagingDatabaseError,
                    "semantic catalog envelope differs",
                ):
                    attest_runtime_connection(conn, self._target)
                conn.execute("RESET SESSION AUTHORIZATION")
                conn.rollback()

        tablespace_path = Path(self._temporary.name) / "audit-tablespace"
        tablespace_path.mkdir(mode=0o700)
        try:
            with psycopg.connect(
                self._target_admin_url, autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL("CREATE TABLESPACE buffalo_audit_space LOCATION {}").format(
                        sql.Literal(str(tablespace_path))
                    )
                )
            with psycopg.connect(self._target_admin_url) as conn:
                conn.execute(
                    "ALTER TABLE qa_mapping_test.catalog_reconciliation_items "
                    "SET TABLESPACE buffalo_audit_space"
                )
                conn.execute(
                    sql.SQL("SET SESSION AUTHORIZATION {}").format(
                        sql.Identifier(RUNTIME_LOGIN)
                    )
                )
                conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
                with self.assertRaisesRegex(
                    SyntheticStagingDatabaseError,
                    "semantic catalog envelope differs",
                ):
                    attest_runtime_connection(conn, self._target)
                conn.execute("RESET SESSION AUTHORIZATION")
                conn.rollback()
        finally:
            with psycopg.connect(
                self._target_admin_url, autocommit=True
            ) as admin:
                admin.execute("DROP TABLESPACE IF EXISTS buffalo_audit_space")

    def test_05_runtime_has_only_the_exact_nonowner_authority(self):
        denied = (
            "CREATE TEMP TABLE staging_denied(value integer)",
            "CREATE SCHEMA staging_denied",
            "TRUNCATE qa_mapping_test.meta",
            "UPDATE qa_mapping_test.variants SET title=title WHERE variant_id='1001'",
            "SELECT nextval('qa_mapping_test.supplier_offers_offer_id_seq')",
            "SELECT * FROM qa_mapping_test.combos LIMIT 1",
        )
        for statement in denied:
            with self.subTest(statement=statement), psycopg.connect(
                self._runtime_url
            ) as conn:
                with self.assertRaises(psycopg.Error):
                    conn.execute(statement)
                conn.rollback()
        with psycopg.connect(self._runtime_url) as conn:
            before = conn.execute(
                "SELECT c.relacl::text FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='qa_mapping_test' AND c.relname='meta'"
            ).fetchone()[0]
            conn.execute("GRANT SELECT ON qa_mapping_test.meta TO PUBLIC")
            after = conn.execute(
                "SELECT c.relacl::text FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='qa_mapping_test' AND c.relname='meta'"
            ).fetchone()[0]
            grant_option = conn.execute(
                "SELECT pg_catalog.has_table_privilege("
                "%s,'qa_mapping_test.meta','SELECT WITH GRANT OPTION')",
                (RUNTIME_LOGIN,),
            ).fetchone()[0]
            self.assertEqual(after, before)
            self.assertFalse(grant_option)
            conn.rollback()
        with self.assertRaises(psycopg.OperationalError):
            psycopg.connect(self._sentinel_runtime_url, connect_timeout=2)

    def test_06_unrelated_database_role_and_objects_are_unchanged(self):
        self.assertEqual(self._sentinel_snapshot(), self._sentinel_before)

    def test_07_runtime_backup_state_facade_matches_owner_projection(self):
        from procurement_os.local_backup_v2 import database_state_evidence

        with psycopg.connect(self._provisioner_url) as conn:
            owner_projection = database_state_evidence(
                conn, schema=staging_database.SCHEMA
            )
            definition = conn.execute(
                "SELECT pg_catalog.pg_get_userbyid(p.proowner),p.provolatile,"
                "p.prosecdef,p.proconfig "
                "FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=%s "
                "AND p.proname='synthetic_staging_backup_v2_state_facts' "
                "AND p.pronargs=0",
                (staging_database.SCHEMA,),
            ).fetchone()
            self.assertEqual(
                definition,
                (staging_database.OBJECT_OWNER, "s", True, ["search_path=pg_catalog"]),
            )
            with self.assertRaises(psycopg.Error):
                conn.execute(
                    "SELECT qa_mapping_test."
                    "synthetic_staging_backup_v2_state_facts()"
                )
            conn.rollback()
        with psycopg.connect(self._runtime_url) as conn:
            conn.execute("SET LOCAL search_path=pg_catalog")
            runtime_projection = database_state_evidence(
                conn,
                schema=staging_database.SCHEMA,
                staging_runtime=True,
            )
            conn.rollback()

        self.assertEqual(runtime_projection, owner_projection)
        relation_counts = {
            item["relation"]: item["row_count"]
            for item in runtime_projection["facts"]["relation_inventory"]
        }
        self.assertEqual(relation_counts["purchase_orders"], 0)
        self.assertGreater(relation_counts["variants"], 0)
        empty_hash = next(
            item["sha256"]
            for item in runtime_projection["facts"]["relation_inventory"]
            if item["relation"] == "purchase_orders"
        )
        self.assertEqual(
            empty_hash,
            hashlib.sha256(b"").hexdigest(),
        )

    def test_08_price_preflight_rechecks_full_target_attestation(self):
        from procurement_os.local_backup_v2 import VerifiedPriceApplyBackup
        from procurement_os.synthetic_price_replacement import (
            SyntheticPriceReplacementError,
            _apply_preflight,
        )
        from procurement_os.synthetic_price_replacement_contract import (
            CATALOG_SHA256,
            MIGRATION_SHA256,
        )

        drift_database = "staging_attestation_drift"
        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
            "BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1",
            "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
            "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST": "1",
        }
        backup = VerifiedPriceApplyBackup(
            manifest_ref="staging-attestation-test",
            manifest_sha256="1" * 64,
            dump_sha256="2" * 64,
            storage_sha256="3" * 64,
            prechange_scope_sha256="4" * 64,
            database=EXPECTED_DATABASE,
            batch_id="11111111-1111-4111-8111-111111111111",
            vendor_id="22222222-2222-4222-8222-222222222222",
            price_scope_key="COMPLETE_VENDOR",
            prior_event_id="33333333-3333-4333-8333-333333333333",
            prior_head_version=1,
            raw_content_sha256="5" * 64,
            raw_storage_key="price-books/raw/" + "5" * 64 + ".csv",
            migration_sha256=MIGRATION_SHA256,
            catalog_sha256=CATALOG_SHA256,
            state={},
            target_kind="staging",
            staging_release_sha256=staging_database.STAGING_BACKUP_RELEASE_SHA256,
            runtime_attestation_identity=self._runtime_generation,
        )
        with psycopg.connect(self._runtime_url) as conn:
            before = conn.execute(
                "SELECT (SELECT count(*) FROM price_book_promotion_events),"
                "(SELECT count(*) FROM supplier_price_authority_heads),"
                "(SELECT count(*) FROM prices)"
            ).fetchone()
        try:
            with psycopg.connect(self._postgres_admin_url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(drift_database), sql.Identifier(self.ADMIN)
                    )
                )
                conn.execute(
                    sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(drift_database)
                    )
                )
                conn.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(drift_database), sql.Identifier(RUNTIME_LOGIN)
                    )
                )
            with mock.patch.dict(os.environ, environment, clear=False), psycopg.connect(
                self._runtime_url
            ) as conn:
                with self.assertRaisesRegex(
                    SyntheticPriceReplacementError,
                    "price APPLY recovery target differs",
                ):
                    _apply_preflight(
                        conn,
                        batch_id=backup.batch_id,
                        backup=backup,
                    )
                conn.rollback()
        finally:
            with psycopg.connect(self._postgres_admin_url, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        sql.Identifier(drift_database)
                    )
                )
        with psycopg.connect(self._runtime_url) as conn:
            after = conn.execute(
                "SELECT (SELECT count(*) FROM price_book_promotion_events),"
                "(SELECT count(*) FROM supplier_price_authority_heads),"
                "(SELECT count(*) FROM prices)"
            ).fetchone()
        self.assertEqual(after, before)

    def test_09_runtime_backup_v2_price_apply_and_retry_attestation(self):
        from procurement_os import synthetic_price_replacement as price_service
        from procurement_os.staging_identity import (
            OWNER_PRINCIPAL_REF,
            OWNER_ROLE_REF,
            StagingRequestIdentity,
        )
        from procurement_os.storage import LocalFilesystemStorage
        from procurement_os.synthetic_price_replacement import (
            SyntheticPriceReplacementError,
            apply_price_replacement,
            confirm_declared_price_book,
            preview_declared_price_confirmation,
            preview_price_replacement,
            registered_target_declaration,
            stage_and_validate_declared_price_book,
        )
        from procurement_os.synthetic_staging_backup_v2 import (
            create_staging_backup_v2,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
            "BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1",
            "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
            "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST": "1",
        }
        identity = StagingRequestIdentity(
            session_digest="02" * 32,
            principal_ref=OWNER_PRINCIPAL_REF,
            role_ref=OWNER_ROLE_REF,
            capabilities=frozenset({"procurement.price.approve"}),
            worker_role="synthetic",
        )
        principal = identity.principal_for("procurement.price.approve")
        book_path = (
            Path(__file__).resolve().parents[1]
            / "config"
            / "synthetic_price_replacement_book.csv"
        )
        book = book_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(book).hexdigest(),
            "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c",
        )
        source_root = Path(__file__).resolve().parents[2]
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
        ).strip()
        source_tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=source_root, text=True
        ).strip()
        warning_reason = "Reviewed four fabricated synthetic price changes."

        with tempfile.TemporaryDirectory(
            prefix="buffalo-staging-price-backup-"
        ) as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            recovery_root = root / "recovery"
            backup_root = recovery_root / "backups"
            storage_root = root / "storage"
            recovery_root.mkdir(mode=0o700)
            backup_root.mkdir(mode=0o700)
            storage_root.mkdir(mode=0o700)
            storage = LocalFilesystemStorage(storage_root)
            with mock.patch.dict(os.environ, environment, clear=False):
                with psycopg.connect(self._runtime_url) as conn:
                    declaration = registered_target_declaration(conn)
                    conn.commit()
                    staged = stage_and_validate_declared_price_book(
                        conn,
                        storage,
                        csv_bytes=book,
                        principal=principal,
                        expected_declaration_sha256=declaration[
                            "declaration_sha256"
                        ],
                    )
                    confirmation_preview = preview_declared_price_confirmation(
                        conn,
                        storage,
                        batch_id=staged["price_book_batch_id"],
                        confirmation_idempotency_key=(
                            "staging-price-confirmation-v1"
                        ),
                        warning_review_reason=warning_reason,
                        principal=principal,
                    )
                    confirmed = confirm_declared_price_book(
                        conn,
                        storage,
                        batch_id=staged["price_book_batch_id"],
                        confirmation_idempotency_key=(
                            "staging-price-confirmation-v1"
                        ),
                        expected_preview_sha256=confirmation_preview[
                            "preview_sha256"
                        ],
                        confirm="CONFIRM",
                        warning_review_reason=warning_reason,
                        principal=principal,
                    )
                self.assertEqual(confirmed["status"], "VERIFIED_FUTURE")
                with mock.patch(
                    "procurement_os.synthetic_staging_backup_v2._source_identity",
                    return_value={"commit": source_commit, "tree": source_tree},
                ):
                    manifest_path = create_staging_backup_v2(
                        runtime_url=self._runtime_url,
                        provisioner_url=self._provisioner_url,
                        target=self._target,
                        recovery_root=recovery_root,
                        storage_root=storage_root,
                        source_root=source_root,
                        batch_id=staged["price_book_batch_id"],
                    )
                manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                binding = {
                    **environment,
                    "BUFFALO_STAGING_PRICE_BACKUP_ROOT": str(recovery_root),
                    "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST": str(manifest_path),
                    "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST_SHA256": manifest_sha,
                    "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_COMMIT": source_commit,
                    "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_TREE": source_tree,
                }

                with psycopg.connect(self._provisioner_url) as conn:
                    conn.execute(
                        "INSERT INTO qa_mapping_test.meta(key,value) VALUES "
                        "('staging_backup_drift_probe','intentional')"
                    )
                    conn.commit()
                with mock.patch.dict(os.environ, binding, clear=False), psycopg.connect(
                    self._runtime_url
                ) as conn:
                    with self.assertRaisesRegex(
                        SyntheticPriceReplacementError,
                        "price APPLY recovery proof differs",
                    ):
                        preview_price_replacement(
                            conn,
                            storage,
                            batch_id=staged["price_book_batch_id"],
                            apply_idempotency_key="staging-price-apply-v1",
                            principal=principal,
                        )
                    conn.rollback()
                with psycopg.connect(self._provisioner_url) as conn:
                    conn.execute(
                        "DELETE FROM qa_mapping_test.meta "
                        "WHERE key='staging_backup_drift_probe'"
                    )
                    conn.commit()

                with mock.patch.dict(os.environ, binding, clear=False), psycopg.connect(
                    self._runtime_url
                ) as conn:
                    apply_preview = preview_price_replacement(
                        conn,
                        storage,
                        batch_id=staged["price_book_batch_id"],
                        apply_idempotency_key="staging-price-apply-v1",
                        principal=principal,
                    )
                with psycopg.connect(self._runtime_url) as conn:
                    before = conn.execute(
                        "SELECT b.status,h.head_version,"
                        "(SELECT count(*) FROM supplier_price_authority_events "
                        " WHERE apply_idempotency_key='staging-price-apply-v1') "
                        "FROM price_book_batches b "
                        "JOIN supplier_price_authority_heads h "
                        "ON h.vendor_id=b.vendor_id "
                        "AND h.price_scope_key=b.price_scope_key "
                        "WHERE b.price_book_batch_id=%s",
                        (staged["price_book_batch_id"],),
                    ).fetchone()
                real_preflight = price_service._apply_preflight
                drift_database = "staging_apply_retry_drift"

                def preflight_then_drift(*args, **kwargs):
                    result = real_preflight(*args, **kwargs)
                    with psycopg.connect(
                        self._postgres_admin_url, autocommit=True
                    ) as admin:
                        admin.execute(
                            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                                sql.Identifier(drift_database),
                                sql.Identifier(self.ADMIN),
                            )
                        )
                        admin.execute(
                            sql.SQL(
                                "REVOKE CONNECT ON DATABASE {} FROM PUBLIC"
                            ).format(sql.Identifier(drift_database))
                        )
                        admin.execute(
                            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                                sql.Identifier(drift_database),
                                sql.Identifier(RUNTIME_LOGIN),
                            )
                        )
                    return result

                try:
                    with mock.patch.dict(
                        os.environ, binding, clear=False
                    ), mock.patch.object(
                        price_service,
                        "_apply_preflight",
                        side_effect=preflight_then_drift,
                    ), psycopg.connect(self._runtime_url) as conn:
                        with self.assertRaisesRegex(
                            SyntheticPriceReplacementError,
                            "price APPLY recovery target differs",
                        ):
                            apply_price_replacement(
                                conn,
                                storage,
                                batch_id=staged["price_book_batch_id"],
                                apply_idempotency_key="staging-price-apply-v1",
                                expected_preview_sha256=apply_preview[
                                    "preview_sha256"
                                ],
                                confirm="CONFIRM",
                                principal=principal,
                            )
                        conn.rollback()
                finally:
                    with psycopg.connect(
                        self._postgres_admin_url, autocommit=True
                    ) as admin:
                        admin.execute(
                            sql.SQL("DROP DATABASE IF EXISTS {}").format(
                                sql.Identifier(drift_database)
                            )
                        )
                with psycopg.connect(self._runtime_url) as conn:
                    after_refusal = conn.execute(
                        "SELECT b.status,h.head_version,"
                        "(SELECT count(*) FROM supplier_price_authority_events "
                        " WHERE apply_idempotency_key='staging-price-apply-v1') "
                        "FROM price_book_batches b "
                        "JOIN supplier_price_authority_heads h "
                        "ON h.vendor_id=b.vendor_id "
                        "AND h.price_scope_key=b.price_scope_key "
                        "WHERE b.price_book_batch_id=%s",
                        (staged["price_book_batch_id"],),
                    ).fetchone()
                self.assertEqual(after_refusal, before)

                with mock.patch.dict(os.environ, binding, clear=False):
                    with psycopg.connect(self._runtime_url) as conn:
                        applied = apply_price_replacement(
                            conn,
                            storage,
                            batch_id=staged["price_book_batch_id"],
                            apply_idempotency_key="staging-price-apply-v1",
                            expected_preview_sha256=apply_preview["preview_sha256"],
                            confirm="CONFIRM",
                            principal=principal,
                        )
                    with psycopg.connect(self._runtime_url) as conn:
                        replay = apply_price_replacement(
                            conn,
                            storage,
                            batch_id=staged["price_book_batch_id"],
                            apply_idempotency_key="staging-price-apply-v1",
                            expected_preview_sha256=apply_preview["preview_sha256"],
                            confirm="CONFIRM",
                            principal=principal,
                        )
                self.assertFalse(applied["idempotent_replay"])
                self.assertTrue(replay["idempotent_replay"])
                self.assertEqual(
                    replay["supplier_price_authority_event_id"],
                    applied["supplier_price_authority_event_id"],
                )
                with psycopg.connect(self._runtime_url) as conn:
                    result = conn.execute(
                        "SELECT b.status,h.head_version,e.evidence_json "
                        "FROM price_book_batches b "
                        "JOIN supplier_price_authority_heads h "
                        "ON h.vendor_id=b.vendor_id "
                        "AND h.price_scope_key=b.price_scope_key "
                        "JOIN supplier_price_authority_events e "
                        "ON e.supplier_price_authority_event_id="
                        "h.supplier_price_authority_event_id "
                        "WHERE b.price_book_batch_id=%s",
                        (staged["price_book_batch_id"],),
                    ).fetchone()
                    prices = conn.execute(
                        "SELECT o.supplier_sku,p.level_type,p.break_qty,"
                        "p.break_unit,p.case_price,p.price_state "
                        "FROM prices p JOIN supplier_offers o USING(offer_id) "
                        "WHERE p.source_price_book_batch_id=%s "
                        "ORDER BY p.source_price_book_row_number",
                        (staged["price_book_batch_id"],),
                    ).fetchall()
                self.assertEqual(result[:2], ("APPLIED_CURRENT", 2))
                self.assertEqual(
                    result[2]["staging_backup_release_sha256"],
                    staging_database.STAGING_BACKUP_RELEASE_SHA256,
                )
                self.assertEqual(
                    result[2]["staging_runtime_attestation_identity"],
                    self._runtime_generation,
                )
                self.assertEqual(len(prices), 4)
                self.assertTrue(all(row[5] == "current" for row in prices))
                southern_break = next(
                    row
                    for row in prices
                    if row[0] == "SUP-001" and row[1] == "BREAK"
                )
                self.assertEqual(
                    southern_break[2:5],
                    (Decimal("2"), "CS", Decimal("30")),
                )
                type(self)._applied_price_batch_id = staged[
                    "price_book_batch_id"
                ]

    def test_10_restricted_runtime_completes_selected_price_draft_packet(self):
        from procurement_os.draft_po import build_vendor_drafts, preview_vendor_drafts
        from procurement_os.emergency_packet import build_emergency_review_packet
        from procurement_os.persistent_mapping import (
            execute_mapping_decision,
            execute_routine_offer_selection,
            execute_supplier_mapping_intake,
            preview_mapping_decision,
            preview_routine_offer_selection,
        )
        from procurement_os.procurement_review import (
            preview_recommendation_review,
            record_recommendation_review,
        )
        from procurement_os.recommendations import (
            confirm_monday_stale_forecast_retirement,
            prepare_monday_run,
            preview_monday_stale_forecast_retirement,
        )
        from procurement_os.staging_identity import (
            OWNER_PRINCIPAL_REF,
            OWNER_ROLE_REF,
            StagingRequestIdentity,
        )
        from procurement_os.storage import LocalFilesystemStorage
        from procurement_os.synthetic_mapping_packet import (
            load_synthetic_mapping_packets,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
            "BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1",
            "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
            "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST": "1",
        }
        actor = OWNER_PRINCIPAL_REF
        stale_run_id = "00000000-0000-4000-8000-000000000901"
        retirement_reason = (
            "Synthetic retired forecast method requires V2 re-preparation"
        )
        with mock.patch.dict(os.environ, environment, clear=False):
            with psycopg.connect(self._runtime_url) as conn:
                preview = preview_monday_stale_forecast_retirement(
                    conn,
                    run_id=stale_run_id,
                    actor=actor,
                    reason=retirement_reason,
                )
                retired = confirm_monday_stale_forecast_retirement(
                    conn,
                    run_id=stale_run_id,
                    actor=actor,
                    reason=retirement_reason,
                    expected_confirmation_sha256=preview["confirmation_sha256"],
                )
                replay = confirm_monday_stale_forecast_retirement(
                    conn,
                    run_id=stale_run_id,
                    actor=actor,
                    reason=retirement_reason,
                    expected_confirmation_sha256=preview["confirmation_sha256"],
                )
            self.assertFalse(retired["idempotent_replay"])
            self.assertTrue(replay["idempotent_replay"])
            self.assertFalse(retired["release_performed"])
            self.assertEqual(retired["shopify_calls"], 0)

            identity = StagingRequestIdentity(
                session_digest="01" * 32,
                principal_ref=OWNER_PRINCIPAL_REF,
                role_ref=OWNER_ROLE_REF,
                capabilities=frozenset(
                    {
                        "procurement.review.intake",
                        "procurement.mapping.approve",
                        "procurement.offer.select",
                        "procurement.order.approve",
                    }
                ),
                worker_role="synthetic",
            )
            packet_source = load_synthetic_mapping_packets()[1]
            intake = execute_supplier_mapping_intake(
                self._runtime_url,
                package=packet_source["package"],
                candidates=packet_source["candidates"],
                principal=identity.principal_for("procurement.review.intake"),
                intake_idempotency_key=packet_source["intake_idempotency_key"],
            )
            self.assertEqual(len(intake["candidate_ids"]), 1)
            candidate_id = UUID(str(intake["candidate_ids"][0]))
            with psycopg.connect(self._runtime_url) as conn:
                offers = conn.execute(
                    "SELECT offer_id FROM supplier_offers "
                    "WHERE variant_id='1001' AND supplier_sku='SUP-001' "
                    "AND active ORDER BY offer_id"
                ).fetchall()
            self.assertEqual(len(offers), 1)
            offer_id = int(offers[0][0])
            decision_key = uuid5(
                NAMESPACE_URL, "buffalo:staging-runtime-proof:decision:v1"
            )
            decision_principal = identity.principal_for(
                "procurement.mapping.approve"
            )
            with psycopg.connect(self._runtime_url) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                decision_preview = preview_mapping_decision(
                    conn,
                    candidate_id=candidate_id,
                    action="APPROVE_MAPPING",
                    reason="restricted-runtime exact existing-offer proof",
                    principal=decision_principal,
                    decision_idempotency_key=decision_key,
                    existing_offer_id=offer_id,
                    offer_link_kind="LINKED_EXISTING",
                )
            decision = execute_mapping_decision(
                self._runtime_url,
                candidate_id=candidate_id,
                action="APPROVE_MAPPING",
                reason="restricted-runtime exact existing-offer proof",
                principal=decision_principal,
                decision_idempotency_key=decision_key,
                expected_preview_sha256=decision_preview["preview_sha256"],
                existing_offer_id=offer_id,
                offer_link_kind="LINKED_EXISTING",
            )
            selection_key = uuid5(
                NAMESPACE_URL, "buffalo:staging-runtime-proof:selection:v1"
            )
            selection_principal = identity.principal_for(
                "procurement.offer.select"
            )
            with psycopg.connect(self._runtime_url) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                selection_preview = preview_routine_offer_selection(
                    conn,
                    mapping_decision_id=UUID(str(decision["mapping_decision_id"])),
                    principal=selection_principal,
                    selection_idempotency_key=selection_key,
                    reason="restricted-runtime selected-offer proof",
                    effective_from=self.BUSINESS_DATE,
                )
            selection = execute_routine_offer_selection(
                self._runtime_url,
                mapping_decision_id=UUID(str(decision["mapping_decision_id"])),
                principal=selection_principal,
                selection_idempotency_key=selection_key,
                reason="restricted-runtime selected-offer proof",
                effective_from=self.BUSINESS_DATE,
                expected_preview_sha256=selection_preview["preview_sha256"],
            )
            self.assertEqual(int(selection["selected_offer_id"]), offer_id)

            with psycopg.connect(self._runtime_url) as conn:
                run = prepare_monday_run(
                    conn,
                    business_date=self.BUSINESS_DATE,
                    idempotency_key="restricted-runtime-v2-one-variant-v1",
                    variant_ids=("1001",),
                    actor=actor,
                )
            self.assertEqual(run["model_version"], "DEVELOPMENT_ROLLING_ORIGIN_V2")
            self.assertEqual(run["blockers"], [])
            self.assertEqual(len(run["recommendations"]), 1)
            recommendation = run["recommendations"][0]
            self.assertEqual(
                (recommendation["recommended_cases"], recommendation["recommended_units"]),
                (6, 36),
            )
            evidence = recommendation["metrics"]["development_forecast_evidence"]
            self.assertEqual(evidence["horizon_days"], 17)
            self.assertEqual(Decimal(str(evidence["point_forecast_units"])), Decimal("34"))

            with psycopg.connect(self._runtime_url) as conn:
                review_preview = preview_recommendation_review(
                    conn,
                    recommendation_id=int(recommendation["recommendation_id"]),
                    action="ACCEPT",
                    actor=actor,
                    expected_input_fingerprint=run["input_fingerprint"],
                    approved_cases=6,
                    approved_loose_units=0,
                    comment="",
                )
                review = record_recommendation_review(
                    conn,
                    recommendation_id=int(recommendation["recommendation_id"]),
                    action="ACCEPT",
                    actor=actor,
                    expected_input_fingerprint=run["input_fingerprint"],
                    approved_cases=6,
                    approved_loose_units=0,
                    comment="",
                    expected_review_preview_fingerprint=review_preview[
                        "preview_fingerprint"
                    ],
                )
            self.assertEqual(review["final_price_tier"]["level_type"], "BREAK")
            self.assertEqual(
                Decimal(str(review["final_price_tier"]["case_price"])),
                Decimal("30"),
            )

            with tempfile.TemporaryDirectory(
                prefix="buffalo-staging-runtime-packet-"
            ) as artifact_root:
                storage = LocalFilesystemStorage(artifact_root)
                with psycopg.connect(self._runtime_url) as conn:
                    draft_preview = preview_vendor_drafts(
                        conn, run_id=str(run["run_id"]), actor=actor
                    )
                    drafts = build_vendor_drafts(
                        conn,
                        run_id=str(run["run_id"]),
                        actor=actor,
                        expected_preview_fingerprint=draft_preview[
                            "preview_fingerprint"
                        ],
                        minimum_disposition=draft_preview["minimum_disposition"],
                    )
                    packet = build_emergency_review_packet(
                        conn,
                        storage=storage,
                        run_id=str(run["run_id"]),
                        actor=actor,
                    )
                with psycopg.connect(self._runtime_url) as conn:
                    packet_replay = build_emergency_review_packet(
                        conn,
                        storage=storage,
                        run_id=str(run["run_id"]),
                        actor=actor,
                    )
                self.assertTrue(packet_replay["idempotent_replay"])
                self.assertEqual(packet_replay["sha256"], packet["sha256"])
                with zipfile.ZipFile(
                    io.BytesIO(storage.get_bytes(packet["storage_key"]))
                ) as archive:
                    self.assertEqual(len(archive.namelist()), 13)
            self.assertEqual(len(drafts["drafts"]), 1)
            self.assertEqual(len(drafts["drafts"][0]["lines"]), 1)
            self.assertEqual(
                Decimal(str(drafts["drafts"][0]["po_total"])), Decimal("180")
            )

        run_id = str(run["run_id"])
        with psycopg.connect(self._runtime_url) as conn:
            controls = tuple(
                int(value)
                for value in conn.execute(
                    """SELECT
                      (SELECT count(*) FROM supplier_mapping_review_batches),
                      (SELECT count(*) FROM supplier_mapping_review_candidates),
                      (SELECT count(*) FROM supplier_mapping_decisions),
                      (SELECT count(*) FROM supplier_offer_selection_events),
                      (SELECT count(*) FROM supplier_offer_selection_heads),
                      (SELECT count(*) FROM monday_stale_forecast_retirements),
                      (SELECT count(*) FROM runs),
                      (SELECT count(*) FROM forecast_results WHERE run_id=%s),
                      (SELECT count(*) FROM procurement_recommendations WHERE run_id=%s),
                      (SELECT count(*) FROM inventory_snapshots WHERE run_id=%s),
                      (SELECT count(*) FROM run_price_snapshots WHERE run_id=%s),
                      (SELECT count(*) FROM review_decisions WHERE run_id=%s),
                      (SELECT count(*) FROM purchase_orders WHERE run_id=%s),
                      (SELECT count(*) FROM purchase_order_lines l
                        JOIN purchase_orders p USING(po_id) WHERE p.run_id=%s),
                      (SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s),
                      (SELECT count(*) FROM monday_packet_build_events WHERE run_id=%s),
                      (SELECT count(*) FROM purchase_orders WHERE po_status<>'DRAFT')""",
                    (run_id,) * 9,
                ).fetchone()
            )
            state = conn.execute(
                "SELECT status,workflow_stage,procurement_output_mode "
                "FROM runs WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            self.assertEqual(
                compute_immutable_fixture_sha256(conn),
                IMMUTABLE_FIXTURE_MANIFEST_SHA256,
            )
            self.assertEqual(
                attest_runtime_connection(conn, self._target),
                self._runtime_generation,
            )
        self.assertEqual(
            controls,
            (1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 2, 1, 1, 1, 2, 1, 0),
        )
        self.assertEqual(state, ("RUNNING", "PACKET_BUILT", "INTERNAL_DRAFT_ONLY"))

    def test_11_fresh_mapping_write_connection_rechecks_full_attestation(self):
        from procurement_os.persistent_mapping import (
            PersistentMappingError,
            Principal,
            _execute_write,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        }
        with psycopg.connect(self._runtime_url) as conn:
            before = conn.execute(
                "SELECT count(*) FROM supplier_mapping_review_batches"
            ).fetchone()[0]
        domain_locks = mock.Mock(
            side_effect=AssertionError("domain locks ran before attestation")
        )
        operation = mock.Mock(
            side_effect=AssertionError("write ran before attestation")
        )
        with psycopg.connect(self._target_admin_url) as conn:
            conn.execute(
                "UPDATE qa_mapping_test.meta SET value=%s WHERE key=%s",
                (
                    "c" * 64,
                    staging_database.TRANSFER_MANIFEST_META_KEY,
                ),
            )
            conn.commit()
        try:
            with mock.patch.dict(os.environ, environment, clear=False):
                from procurement_os import api, health

                report = health.full_health()
                self.assertFalse(report["database"]["ok"])
                self.assertFalse(report["po_generation_enabled"])
                with self.assertRaises(SyntheticStagingDatabaseError):
                    api._db_conn()
                with self.assertRaisesRegex(
                    PersistentMappingError,
                    "connected synthetic mapping database identity differs",
                ):
                    _execute_write(
                        self._runtime_url,
                        operation_name="staging-attestation-negative",
                        capability="review_intake_writes_enabled",
                        principal=Principal(
                            principal_ref="owner:railway-staging:01",
                            role_ref="role:owner",
                            authn_context_sha256="1" * 64,
                        ),
                        idempotency_key=uuid5(
                            NAMESPACE_URL,
                            "buffalo:staging:attestation-negative:v1",
                        ),
                        domain_locks=domain_locks,
                        operation=operation,
                    )
        finally:
            with psycopg.connect(self._target_admin_url) as conn:
                conn.execute(
                    "UPDATE qa_mapping_test.meta SET value=%s WHERE key=%s",
                    (
                        self.TRANSFER_MANIFEST,
                        staging_database.TRANSFER_MANIFEST_META_KEY,
                    ),
                )
                conn.commit()
        domain_locks.assert_not_called()
        operation.assert_not_called()
        with psycopg.connect(self._runtime_url) as conn:
            after = conn.execute(
                "SELECT count(*) FROM supplier_mapping_review_batches"
            ).fetchone()[0]
            self.assertEqual(
                attest_runtime_connection(conn, self._target),
                self._runtime_generation,
            )
        self.assertEqual(after, before)

    def test_12_unknown_commit_recovery_reattests_before_lookup(self):
        from procurement_os import persistent_mapping as mapping_service
        from procurement_os.persistent_mapping import (
            PersistentMappingError,
            Principal,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        }
        real_connect = psycopg.connect
        operation = mock.Mock(return_value={"created": True})
        recovery_lookup = mock.Mock(
            side_effect=AssertionError("recovery lookup ran before attestation")
        )
        outer = self

        class CommitResponseLost:
            def __init__(self, raw):
                self.raw = raw
                self.injected = False

            @property
            def closed(self):
                return self.raw.closed

            @property
            def info(self):
                return self.raw.info

            def execute(self, statement, parameters=None):
                if parameters is None:
                    return self.raw.execute(statement)
                return self.raw.execute(statement, parameters)

            def rollback(self):
                return self.raw.rollback()

            def close(self):
                return self.raw.close()

            def commit(self):
                self.raw.commit()
                if not self.injected:
                    self.injected = True
                    with real_connect(outer._target_admin_url) as admin:
                        admin.execute(
                            "UPDATE qa_mapping_test.meta SET value=%s "
                            "WHERE key=%s",
                            (
                                "d" * 64,
                                staging_database.TRANSFER_MANIFEST_META_KEY,
                            ),
                        )
                        admin.commit()
                    raise psycopg.OperationalError(
                        "lost response after server commit"
                    )

        connections = 0

        def connect(*args, **kwargs):
            nonlocal connections
            connections += 1
            raw = real_connect(*args, **kwargs)
            return CommitResponseLost(raw) if connections == 1 else raw

        try:
            with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
                mapping_service.psycopg, "connect", side_effect=connect
            ):
                with self.assertRaises(PersistentMappingError) as raised:
                    mapping_service._execute_write(
                        self._runtime_url,
                        operation_name="staging-unknown-commit-attestation",
                        capability="review_intake_writes_enabled",
                        principal=Principal(
                            principal_ref="owner:railway-staging:01",
                            role_ref="role:owner",
                            authn_context_sha256="2" * 64,
                        ),
                        idempotency_key=uuid5(
                            NAMESPACE_URL,
                            "buffalo:staging:unknown-commit-attestation:v1",
                        ),
                        domain_locks=lambda _conn: (),
                        operation=operation,
                        recovery_lookup=recovery_lookup,
                    )
            self.assertEqual(
                raised.exception.code,
                "COMMIT_OUTCOME_UNKNOWN",
                repr(raised.exception.__cause__),
            )
            self.assertEqual(connections, 2)
            operation.assert_called_once()
            recovery_lookup.assert_not_called()
        finally:
            with real_connect(self._target_admin_url) as conn:
                conn.execute(
                    "UPDATE qa_mapping_test.meta SET value=%s WHERE key=%s",
                    (
                        self.TRANSFER_MANIFEST,
                        staging_database.TRANSFER_MANIFEST_META_KEY,
                    ),
                )
                conn.commit()
        with real_connect(self._runtime_url) as conn:
            self.assertEqual(
                attest_runtime_connection(conn, self._target),
                self._runtime_generation,
            )

    def test_13_core_global_privilege_drift_fails_every_runtime_attestation(self):
        mutations = (
            (
                "GRANT SELECT ON pg_catalog.pg_authid TO PUBLIC",
                "REVOKE SELECT ON pg_catalog.pg_authid FROM PUBLIC",
            ),
            (
                "GRANT SELECT (rolpassword) ON pg_catalog.pg_authid TO PUBLIC",
                "REVOKE SELECT (rolpassword) ON pg_catalog.pg_authid FROM PUBLIC",
            ),
            (
                "GRANT CREATE ON SCHEMA pg_catalog TO PUBLIC",
                "REVOKE CREATE ON SCHEMA pg_catalog FROM PUBLIC",
            ),
            (
                "GRANT SET ON PARAMETER session_replication_role TO PUBLIC",
                "REVOKE SET ON PARAMETER session_replication_role FROM PUBLIC",
            ),
        )
        for grant, revoke in mutations:
            with self.subTest(grant=grant):
                try:
                    with psycopg.connect(
                        self._target_admin_url, autocommit=True
                    ) as admin:
                        admin.execute(grant)
                    with psycopg.connect(self._runtime_url) as runtime:
                        with self.assertRaisesRegex(
                            SyntheticStagingDatabaseError,
                            "core global privilege envelope differs",
                        ):
                            attest_runtime_connection(runtime, self._target)
                        runtime.rollback()
                finally:
                    with psycopg.connect(
                        self._target_admin_url, autocommit=True
                    ) as admin:
                        admin.execute(revoke)
                with psycopg.connect(self._runtime_url) as runtime:
                    self.assertEqual(
                        attest_runtime_connection(runtime, self._target),
                        self._runtime_generation,
                    )
                    runtime.rollback()

        try:
            with psycopg.connect(
                self._target_admin_url, autocommit=True
            ) as admin:
                admin.execute(
                    "CREATE FUNCTION information_schema.buffalo_forbidden() "
                    "RETURNS text LANGUAGE sql SECURITY DEFINER "
                    "SET search_path=pg_catalog AS 'SELECT current_user::text'"
                )
            with psycopg.connect(self._runtime_url) as runtime:
                self.assertEqual(
                    runtime.execute(
                        "SELECT information_schema.buffalo_forbidden()"
                    ).fetchone(),
                    (self.ADMIN,),
                )
                with self.assertRaisesRegex(
                    SyntheticStagingDatabaseError,
                    "core global privilege envelope differs",
                ):
                    attest_runtime_connection(runtime, self._target)
                runtime.rollback()
        finally:
            with psycopg.connect(
                self._target_admin_url, autocommit=True
            ) as admin:
                admin.execute(
                    "DROP FUNCTION IF EXISTS information_schema.buffalo_forbidden()"
                )
        with psycopg.connect(self._runtime_url) as runtime:
            self.assertEqual(
                attest_runtime_connection(runtime, self._target),
                self._runtime_generation,
            )
            runtime.rollback()

    def test_15_deciding_write_rechecks_effective_postgres_safety(self):
        from procurement_os.persistent_mapping import (
            PersistentMappingError,
            Principal,
            _execute_write,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        }
        observed_roles: list[str] = []

        def drift_after_attestation(conn):
            with psycopg.connect(
                self._postgres_admin_url, autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL("GRANT SET ON PARAMETER session_replication_role TO {}")
                    .format(sql.Identifier(RUNTIME_LOGIN))
                )
            conn.execute("SET session_replication_role=replica")
            observed_roles.append(
                conn.execute(
                    "SELECT pg_catalog.current_setting('session_replication_role')"
                ).fetchone()[0]
            )
            return ()

        operation = mock.Mock(
            side_effect=AssertionError("write ran with unsafe PostgreSQL settings")
        )
        try:
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(
                    PersistentMappingError,
                    "database security state differs",
                ):
                    _execute_write(
                        self._runtime_url,
                        operation_name="staging-effective-safety-recheck",
                        capability="review_intake_writes_enabled",
                        principal=Principal(
                            principal_ref="owner:railway-staging:01",
                            role_ref="role:owner",
                            authn_context_sha256="4" * 64,
                        ),
                        idempotency_key=uuid5(
                            NAMESPACE_URL,
                            "buffalo:staging:effective-safety-recheck:v1",
                        ),
                        domain_locks=drift_after_attestation,
                        operation=operation,
                    )
        finally:
            with psycopg.connect(
                self._postgres_admin_url, autocommit=True
            ) as admin:
                admin.execute(
                    sql.SQL(
                        "REVOKE SET ON PARAMETER session_replication_role FROM {}"
                    ).format(sql.Identifier(RUNTIME_LOGIN))
                )
        self.assertEqual(observed_roles, ["replica"])
        operation.assert_not_called()
        with psycopg.connect(self._runtime_url) as runtime:
            self.assertEqual(
                attest_runtime_connection(runtime, self._target),
                self._runtime_generation,
            )
            runtime.rollback()

    def test_16_durability_settings_refuse_attestation_and_write(self):
        from procurement_os.persistent_mapping import (
            PersistentMappingError,
            Principal,
            _execute_write,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        }
        cases = (
            ("fsync", "off", "on"),
            ("full_page_writes", "off", "on"),
            ("synchronous_commit", "off", "on"),
            ("zero_damaged_pages", "on", "off"),
            ("ignore_checksum_failure", "on", "off"),
        )
        for setting, unsafe, safe in cases:
            with self.subTest(setting=setting, unsafe=unsafe):
                domain_locks = mock.Mock(
                    side_effect=AssertionError(
                        "domain locks ran before durability attestation"
                    )
                )
                operation = mock.Mock(
                    side_effect=AssertionError(
                        "write ran before durability attestation"
                    )
                )
                with psycopg.connect(
                    self._postgres_admin_url, autocommit=True
                ) as admin:
                    try:
                        admin.execute(
                            sql.SQL("ALTER SYSTEM SET {}={}").format(
                                sql.Identifier(setting), sql.Literal(unsafe)
                            )
                        )
                        admin.execute("SELECT pg_catalog.pg_reload_conf()")
                        with psycopg.connect(self._runtime_url) as runtime:
                            self.assertEqual(
                                runtime.execute(
                                    sql.SQL(
                                        "SELECT pg_catalog.current_setting({})"
                                    ).format(sql.Literal(setting))
                                ).fetchone(),
                                (unsafe,),
                            )
                            with self.assertRaisesRegex(
                                SyntheticStagingDatabaseError,
                                "core global privilege envelope differs",
                            ):
                                attest_runtime_connection(runtime, self._target)
                            runtime.rollback()
                        with mock.patch.dict(
                            os.environ, environment, clear=False
                        ):
                            with self.assertRaisesRegex(
                                PersistentMappingError,
                                "connected synthetic mapping database identity differs",
                            ):
                                _execute_write(
                                    self._runtime_url,
                                    operation_name=(
                                        f"staging-durability-negative-{setting}"
                                    ),
                                    capability="review_intake_writes_enabled",
                                    principal=Principal(
                                        principal_ref="owner:railway-staging:01",
                                        role_ref="role:owner",
                                        authn_context_sha256="5" * 64,
                                    ),
                                    idempotency_key=uuid5(
                                        NAMESPACE_URL,
                                        "buffalo:staging:durability-negative:"
                                        f"{setting}:v1",
                                    ),
                                    domain_locks=domain_locks,
                                    operation=operation,
                                )
                    finally:
                        admin.execute(
                            sql.SQL("ALTER SYSTEM RESET {}").format(
                                sql.Identifier(setting)
                            )
                        )
                        admin.execute("SELECT pg_catalog.pg_reload_conf()")
                domain_locks.assert_not_called()
                operation.assert_not_called()
                with psycopg.connect(self._runtime_url) as runtime:
                    self.assertEqual(
                        runtime.execute(
                            sql.SQL("SELECT pg_catalog.current_setting({})").format(
                                sql.Literal(setting)
                            )
                        ).fetchone(),
                        (safe,),
                    )
                    self.assertEqual(
                        attest_runtime_connection(runtime, self._target),
                        self._runtime_generation,
                    )
                    runtime.rollback()

    def test_14_database_default_resists_cluster_replication_reload(self):
        from procurement_os.persistent_mapping import (
            PersistentMappingError,
            Principal,
            _execute_write,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        }
        with psycopg.connect(self._runtime_url) as existing_runtime:
            self.assertEqual(
                existing_runtime.execute(
                    "SELECT pg_catalog.current_setting('session_replication_role'),"
                    "source FROM pg_catalog.pg_settings "
                    "WHERE name='session_replication_role'"
                ).fetchone(),
                ("origin", "database"),
            )
            with psycopg.connect(
                self._postgres_admin_url, autocommit=True
            ) as admin:
                try:
                    admin.execute(
                        "ALTER SYSTEM SET session_replication_role='replica'"
                    )
                    admin.execute("SELECT pg_catalog.pg_reload_conf()")
                    self.assertEqual(
                        existing_runtime.execute(
                            "SELECT pg_catalog.current_setting("
                            "'session_replication_role'),source "
                            "FROM pg_catalog.pg_settings "
                            "WHERE name='session_replication_role'"
                        ).fetchone(),
                        ("origin", "database"),
                    )
                    self.assertEqual(
                        attest_runtime_connection(existing_runtime, self._target),
                        self._runtime_generation,
                    )
                    existing_runtime.rollback()
                    with psycopg.connect(self._runtime_url) as fresh_runtime:
                        self.assertEqual(
                            fresh_runtime.execute(
                                "SELECT pg_catalog.current_setting("
                                "'session_replication_role'),source "
                                "FROM pg_catalog.pg_settings "
                                "WHERE name='session_replication_role'"
                            ).fetchone(),
                            ("origin", "database"),
                        )
                        self.assertEqual(
                            attest_runtime_connection(fresh_runtime, self._target),
                            self._runtime_generation,
                        )
                        fresh_runtime.rollback()
                finally:
                    admin.execute("ALTER SYSTEM RESET session_replication_role")
                    admin.execute("SELECT pg_catalog.pg_reload_conf()")

        domain_locks = mock.Mock(return_value=())
        operation = mock.Mock(return_value="safe")
        with mock.patch.dict(os.environ, environment, clear=False):
            self.assertEqual(
                _execute_write(
                    self._runtime_url,
                    operation_name="staging-replication-default-positive",
                    capability="review_intake_writes_enabled",
                    principal=Principal(
                        principal_ref="owner:railway-staging:01",
                        role_ref="role:owner",
                        authn_context_sha256="3" * 64,
                    ),
                    idempotency_key=uuid5(
                        NAMESPACE_URL,
                        "buffalo:staging:replication-default-positive:v1",
                    ),
                    domain_locks=domain_locks,
                    operation=operation,
                ),
                "safe",
            )
        domain_locks.assert_called_once()
        operation.assert_called_once()

        domain_locks.reset_mock()
        operation.reset_mock()
        with psycopg.connect(self._target_admin_url, autocommit=True) as admin:
            try:
                admin.execute(
                    sql.SQL(
                        "ALTER DATABASE {} SET session_replication_role TO replica"
                    ).format(sql.Identifier(EXPECTED_DATABASE))
                )
                with psycopg.connect(self._runtime_url) as runtime:
                    self.assertEqual(
                        runtime.execute(
                            "SELECT pg_catalog.current_setting("
                            "'session_replication_role'),source "
                            "FROM pg_catalog.pg_settings "
                            "WHERE name='session_replication_role'"
                        ).fetchone(),
                        ("replica", "database"),
                    )
                    with self.assertRaisesRegex(
                        SyntheticStagingDatabaseError,
                        "core global privilege envelope differs",
                    ):
                        attest_runtime_connection(runtime, self._target)
                    runtime.rollback()
                with mock.patch.dict(os.environ, environment, clear=False):
                    with self.assertRaisesRegex(
                        PersistentMappingError,
                        "connected synthetic mapping database identity differs",
                    ):
                        _execute_write(
                            self._runtime_url,
                            operation_name="staging-replication-setting-negative",
                            capability="review_intake_writes_enabled",
                            principal=Principal(
                                principal_ref="owner:railway-staging:01",
                                role_ref="role:owner",
                                authn_context_sha256="3" * 64,
                            ),
                            idempotency_key=uuid5(
                                NAMESPACE_URL,
                                "buffalo:staging:replication-setting-negative:v1",
                            ),
                            domain_locks=domain_locks,
                            operation=operation,
                        )
            finally:
                admin.execute(
                    sql.SQL(
                        "ALTER DATABASE {} SET session_replication_role TO origin"
                    ).format(sql.Identifier(EXPECTED_DATABASE))
                )
        domain_locks.assert_not_called()
        operation.assert_not_called()
        with psycopg.connect(self._runtime_url) as runtime:
            self.assertEqual(
                runtime.execute(
                    "SELECT pg_catalog.current_setting('session_replication_role')"
                ).fetchone(),
                ("origin",),
            )
            self.assertEqual(
                attest_runtime_connection(runtime, self._target),
                self._runtime_generation,
            )
            runtime.rollback()

        with psycopg.connect(self._target_admin_url) as admin:
            definition = admin.execute(
                "SELECT pg_catalog.pg_get_viewdef('pg_catalog.pg_roles'::regclass,false)"
            ).fetchone()[0]
            original = "'********'::text AS rolpassword"
            self.assertIn(original, definition)
            admin.execute(
                "CREATE OR REPLACE VIEW pg_catalog.pg_roles AS "
                + definition.replace(
                    original,
                    "pg_authid.rolpassword COLLATE \"default\" AS rolpassword",
                    1,
                )
            )
            admin.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(RUNTIME_LOGIN)
                )
            )
            admin.execute("SET search_path TO qa_mapping_test,pg_catalog")
            with self.assertRaisesRegex(
                SyntheticStagingDatabaseError,
                "core global privilege envelope differs",
            ):
                attest_runtime_connection(admin, self._target)
            admin.execute("RESET SESSION AUTHORIZATION")
            admin.rollback()

        with psycopg.connect(self._target_admin_url) as admin:
            admin.execute(
                "CREATE OR REPLACE FUNCTION pg_catalog.quote_literal(anyelement) "
                "RETURNS text LANGUAGE sql SECURITY DEFINER "
                "SET search_path=pg_catalog AS "
                "'SELECT current_user::text'"
            )
            admin.execute(
                sql.SQL("SET SESSION AUTHORIZATION {}").format(
                    sql.Identifier(RUNTIME_LOGIN)
                )
            )
            admin.execute("SET search_path TO qa_mapping_test,pg_catalog")
            with self.assertRaisesRegex(
                SyntheticStagingDatabaseError,
                "core global privilege envelope differs",
            ):
                attest_runtime_connection(admin, self._target)
            admin.execute("RESET SESSION AUTHORIZATION")
            admin.rollback()

        with psycopg.connect(self._runtime_url) as runtime:
            self.assertEqual(
                attest_runtime_connection(runtime, self._target),
                self._runtime_generation,
            )
            runtime.rollback()

    def test_17_restart_only_corruption_and_prepared_xact_modes_refuse(self):
        from procurement_os.persistent_mapping import (
            PersistentMappingError,
            Principal,
            _execute_write,
        )

        environment = {
            "DATABASE_URL": self._runtime_url,
            "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "127.0.0.1",
            "BUFFALO_STAGING_TRANSFER_MANIFEST_SHA256": self.TRANSFER_MANIFEST,
            "BUFFALO_STAGING_OWNED_LOCAL_PORT": str(self._port),
            "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
        }
        unsafe = {
            "data_sync_retry": "on",
            "ignore_invalid_pages": "on",
            "max_prepared_transactions": "1",
        }
        gid = "buffalo-staging-detached-transaction-proof"
        prepared = False
        try:
            with psycopg.connect(
                self._postgres_admin_url, autocommit=True
            ) as admin:
                for setting, value in unsafe.items():
                    admin.execute(
                        sql.SQL("ALTER SYSTEM SET {}={}").format(
                            sql.Identifier(setting), sql.Literal(value)
                        )
                    )
            self._restart_cluster()
            with psycopg.connect(self._runtime_url) as runtime:
                observed = runtime.execute(
                    "SELECT pg_catalog.current_setting('data_sync_retry'),"
                    "pg_catalog.current_setting('ignore_invalid_pages'),"
                    "pg_catalog.current_setting('max_prepared_transactions')"
                ).fetchone()
                self.assertEqual(observed, ("on", "on", "1"))
                with self.assertRaisesRegex(
                    SyntheticStagingDatabaseError,
                    "core global privilege envelope differs",
                ):
                    attest_runtime_connection(runtime, self._target)
                runtime.rollback()

            with psycopg.connect(self._runtime_url) as runtime:
                runtime.execute("SELECT 1")
                runtime.execute(
                    sql.SQL("PREPARE TRANSACTION {}").format(sql.Literal(gid))
                )
                prepared = True
            with psycopg.connect(self._target_admin_url, autocommit=True) as admin:
                self.assertEqual(
                    admin.execute(
                        "SELECT owner,database FROM pg_catalog.pg_prepared_xacts "
                        "WHERE gid=%s",
                        (gid,),
                    ).fetchone(),
                    (RUNTIME_LOGIN, EXPECTED_DATABASE),
                )
                admin.execute(
                    sql.SQL("ROLLBACK PREPARED {}").format(sql.Literal(gid))
                )
                prepared = False

            domain_locks = mock.Mock(
                side_effect=AssertionError("locks ran with unsafe PostgreSQL settings")
            )
            operation = mock.Mock(
                side_effect=AssertionError("write ran with unsafe PostgreSQL settings")
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(
                    PersistentMappingError,
                    "connected synthetic mapping database identity differs",
                ):
                    _execute_write(
                        self._runtime_url,
                        operation_name="staging-restart-only-safety-negative",
                        capability="review_intake_writes_enabled",
                        principal=Principal(
                            principal_ref="owner:railway-staging:01",
                            role_ref="role:owner",
                            authn_context_sha256="6" * 64,
                        ),
                        idempotency_key=uuid5(
                            NAMESPACE_URL,
                            "buffalo:staging:restart-only-safety-negative:v1",
                        ),
                        domain_locks=domain_locks,
                        operation=operation,
                    )
            domain_locks.assert_not_called()
            operation.assert_not_called()
        finally:
            if prepared:
                with psycopg.connect(
                    self._target_admin_url, autocommit=True
                ) as admin:
                    admin.execute(
                        sql.SQL("ROLLBACK PREPARED {}").format(sql.Literal(gid))
                    )
            with psycopg.connect(
                self._postgres_admin_url, autocommit=True
            ) as admin:
                for setting in unsafe:
                    admin.execute(
                        sql.SQL("ALTER SYSTEM RESET {}").format(
                            sql.Identifier(setting)
                        )
                    )
            self._restart_cluster()

        with psycopg.connect(self._runtime_url) as runtime:
            self.assertEqual(
                runtime.execute(
                    "SELECT pg_catalog.current_setting('data_sync_retry'),"
                    "pg_catalog.current_setting('ignore_invalid_pages'),"
                    "pg_catalog.current_setting('max_prepared_transactions')"
                ).fetchone(),
                ("off", "off", "0"),
            )
            self.assertEqual(
                attest_runtime_connection(runtime, self._target),
                self._runtime_generation,
            )
            runtime.rollback()

    def test_18_shared_lifecycle_lock_excludes_two_real_sessions(self):
        expected_lock = database_lifecycle_lock_name(EXPECTED_DATABASE)
        first = psycopg.connect(self._runtime_url, autocommit=True)
        second = psycopg.connect(self._runtime_url, autocommit=True)
        self.addCleanup(first.close)
        self.addCleanup(second.close)

        observed = acquire_database_lifecycle_lock(
            first, database=EXPECTED_DATABASE
        )
        self.assertEqual(observed, expected_lock)
        assert_database_lifecycle_lock(first, lock_name=expected_lock)
        with self.assertRaisesRegex(
            DatabaseLifecycleError, "database lifecycle lock is already held"
        ):
            acquire_database_lifecycle_lock(first, database=EXPECTED_DATABASE)
        with self.assertRaisesRegex(
            DatabaseLifecycleError,
            "another local purchasing lifecycle operation is active",
        ):
            acquire_database_lifecycle_lock(second, database=EXPECTED_DATABASE)

        release_database_lifecycle_lock(first, lock_name=expected_lock)
        observed = acquire_database_lifecycle_lock(
            second, database=EXPECTED_DATABASE
        )
        self.assertEqual(observed, expected_lock)
        second.close()

        replacement = psycopg.connect(self._runtime_url, autocommit=True)
        self.addCleanup(replacement.close)
        self.assertEqual(
            acquire_database_lifecycle_lock(
                replacement, database=EXPECTED_DATABASE
            ),
            expected_lock,
        )
        release_database_lifecycle_lock(replacement, lock_name=expected_lock)


if __name__ == "__main__":
    unittest.main()
