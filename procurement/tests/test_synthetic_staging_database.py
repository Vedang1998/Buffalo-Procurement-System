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
from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE, IMMUTABLE_FIXTURE_MANIFEST_SHA256,
    PERMISSION_MATRIX_SHA256, PREDECESSOR_CATALOG_SHA256, PROVISIONER,
    RUNTIME_LOGIN, SUCCESSOR_CATALOG_SHA256,
    SyntheticStagingDatabaseError, SyntheticStagingTarget,
    attest_runtime_connection, bootstrap_roles, compute_catalog_sha256,
    compute_immutable_fixture_sha256, permission_records, provision_contract,
)


class SyntheticStagingDatabaseContractTests(unittest.TestCase):
    def target(self):
        return SyntheticStagingTarget(
            database_url=f"postgresql://{RUNTIME_LOGIN}@postgres.railway.internal/{EXPECTED_DATABASE}",
            expected_private_host="postgres.railway.internal",
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
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
                f"-h 127.0.0.1 -p {cls._port} -k {root} -F",
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
            cls._bootstrap_changed = bootstrap_roles(conn)
            conn.commit()
        with psycopg.connect(cls._target_admin_url) as conn:
            cls._bootstrap_noop = bootstrap_roles(conn)
            conn.commit()
        with psycopg.connect(cls._provisioner_url, autocommit=True) as conn:
            try:
                provision_contract(conn)
            except SyntheticStagingDatabaseError:
                cls._autocommit_refused = True
            else:
                cls._autocommit_refused = False
        with psycopg.connect(cls._provisioner_url) as conn:
            cls._predecessor = compute_catalog_sha256(conn)
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
                    provision_contract(conn)
            except SyntheticStagingDatabaseError as exc:
                cls._rollback_failure = str(exc)
                conn.rollback()
            else:
                raise AssertionError("injected transition failure did not fire")
        with psycopg.connect(cls._provisioner_url) as conn:
            cls._post_rollback_catalog = compute_catalog_sha256(conn)
            cls._post_rollback_markers = staging_database._observed_staging_markers(
                conn
            )
            conn.rollback()
        with psycopg.connect(cls._provisioner_url) as conn:
            cls._provision_changed = provision_contract(conn)
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
            owned_local_port=cls._port,
        )
        with psycopg.connect(cls._runtime_url) as conn:
            cls._runtime_generation = attest_runtime_connection(conn, cls._target)
            conn.rollback()

    def test_01_exact_transition_and_transactional_rollback(self):
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
            self.assertFalse(provision_contract(conn))
            self.assertEqual(compute_catalog_sha256(conn), SUCCESSOR_CATALOG_SHA256)
            conn.commit()

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

    def test_04_runtime_has_only_the_exact_nonowner_authority(self):
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

    def test_05_unrelated_database_role_and_objects_are_unchanged(self):
        self.assertEqual(self._sentinel_snapshot(), self._sentinel_before)

    def test_06_restricted_runtime_completes_selected_price_draft_packet(self):
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
                Decimal("9.5"),
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
                Decimal(str(drafts["drafts"][0]["po_total"])), Decimal("57")
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


if __name__ == "__main__":
    unittest.main()
