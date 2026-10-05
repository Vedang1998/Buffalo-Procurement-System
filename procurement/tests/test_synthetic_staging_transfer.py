from __future__ import annotations

from datetime import date
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

import psycopg
from psycopg import sql

from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID,
    EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID,
    EXPECTED_PROJECT_ID,
)
from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE,
    EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
    IMMUTABLE_FIXTURE_MANIFEST_SHA256,
    PROVISIONER,
    RUNTIME_LOGIN,
    SyntheticStagingTarget,
    attest_runtime_connection,
)
from procurement_os import synthetic_staging_transfer as transfer
from procurement_os.synthetic_staging_transfer import (
    DUMP_NAME,
    MANIFEST_NAME,
    SyntheticStagingTransferError,
    create_transfer_artifact,
    prepare_transfer_destination,
    restore_and_transition,
    verify_transfer_artifact,
)
import transfer_synthetic_staging as transfer_cli


class SyntheticStagingTransferUnitTests(unittest.TestCase):
    MANIFEST_SHA256 = "a" * 64
    COMMIT = "1" * 40
    TREE = "2" * 40
    PORT = 55432
    PROVISIONER_SECRET = "P" * 48
    RUNTIME_SECRET = "R" * 48

    def target(self) -> SyntheticStagingTarget:
        return SyntheticStagingTarget(
            database_url=(
                f"postgresql://{RUNTIME_LOGIN}@127.0.0.1:{self.PORT}/"
                f"{EXPECTED_DATABASE}"
            ),
            expected_private_host="127.0.0.1",
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
            transfer_manifest_sha256=self.MANIFEST_SHA256,
            owned_local_port=self.PORT,
        )

    def restore_arguments(self) -> dict[str, object]:
        base = f"127.0.0.1:{self.PORT}/{EXPECTED_DATABASE}"
        return {
            "artifact_root": Path("/private/transfer"),
            "expected_manifest_sha256": self.MANIFEST_SHA256,
            "admin_url": f"postgresql://admin@{base}",
            "provisioner_url": f"postgresql://{PROVISIONER}@{base}",
            "runtime_url": f"postgresql://{RUNTIME_LOGIN}@{base}",
            "provisioner_secret": self.PROVISIONER_SECRET,
            "runtime_secret": self.RUNTIME_SECRET,
            "target": self.target(),
            "source_root": Path("/reviewed/source"),
            "expected_source_commit": self.COMMIT,
            "expected_source_tree": self.TREE,
        }

    @staticmethod
    def toc_output(*, records: list[str] | None = None) -> str:
        selected = records or [
            "1; 0 0 SCHEMA - qa_mapping_test qa_mapping_owner",
            "2; 0 0 EXTENSION - pgcrypto ",
            "3; 0 0 COMMENT - EXTENSION pgcrypto ",
        ]
        return "\n".join(
            [
                ";",
                "; Archive created at 2026-10-04 00:00:00 UTC",
                f";     dbname: {EXPECTED_DATABASE}",
                ";     TOC Entries: 3",
                ";     Compression: gzip",
                ";     Dump Version: 1.15-0",
                ";     Format: CUSTOM",
                ";     Integer: 4 bytes",
                ";     Offset: 8 bytes",
                ";     Dumped from database version: 16.9",
                ";     Dumped by pg_dump version: 16.9",
                ";",
                "; Selected TOC Entries:",
                ";",
                *selected,
                "",
            ]
        )

    @staticmethod
    def toc_semantic_sha256() -> str:
        descriptions = sorted(
            [
                "SCHEMA - qa_mapping_test qa_mapping_owner",
                "EXTENSION - pgcrypto",
                "COMMENT - EXTENSION pgcrypto",
            ]
        )
        return hashlib.sha256(
            ("\n".join(descriptions) + "\n").encode("ascii")
        ).hexdigest()

    def test_mixed_destination_endpoints_refuse_before_connecting(self):
        arguments = self.restore_arguments()
        arguments["admin_url"] = (
            f"postgresql://admin@127.0.0.1:{self.PORT + 1}/{EXPECTED_DATABASE}"
        )
        with mock.patch.object(
            transfer,
            "_source_identity",
            return_value={"commit": self.COMMIT, "tree": self.TREE},
        ), mock.patch.object(
            transfer, "verify_transfer_artifact", return_value=mock.Mock()
        ), mock.patch.object(transfer.psycopg, "connect") as connect:
            with self.assertRaisesRegex(
                SyntheticStagingTransferError, "endpoint differs"
            ):
                restore_and_transition(**arguments)
        connect.assert_not_called()

    def test_post_restore_failure_is_reported_as_partial_without_cleanup(self):
        arguments = self.restore_arguments()
        connection = mock.MagicMock()
        prepared = mock.MagicMock()
        artifact = mock.MagicMock()
        artifact.manifest = {
            "source": {"database": {"system_identifier": "1234567890123456789"}}
        }
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        context.__exit__.return_value = False
        connection.execute.return_value.fetchone.return_value = (
            transfer.LEGACY_LOGIN,
            transfer.LEGACY_OWNER,
        )
        with mock.patch.object(
            transfer,
            "_source_identity",
            return_value={"commit": self.COMMIT, "tree": self.TREE},
        ), mock.patch.object(
            transfer, "verify_transfer_artifact", return_value=artifact
        ), mock.patch.object(
            transfer.psycopg, "connect", return_value=context
        ), mock.patch.object(
            transfer, "_prepare_restore_dump", return_value=prepared
        ), mock.patch.object(
            transfer, "_preflight_empty_destination"
        ), mock.patch.object(
            transfer, "_destination_pid", side_effect=(101, 102)
        ), mock.patch.object(
            transfer, "_require_destination_quiescent"
        ), mock.patch.object(
            transfer, "_fence_destination"
        ), mock.patch.object(
            transfer, "_restore_dump"
        ) as restore_dump, mock.patch.object(
            transfer,
            "_normalize_restored_owner_acls",
            side_effect=SyntheticStagingTransferError("injected"),
        ):
            with self.assertRaisesRegex(
                SyntheticStagingTransferError,
                "partial and must be discarded",
            ):
                restore_and_transition(**arguments)
        restore_dump.assert_called_once()
        self.assertIs(restore_dump.call_args.kwargs["prepared"], prepared)
        prepared.close.assert_called_once_with()

    def test_toc_parser_is_exact_and_returns_the_validated_use_list(self):
        completed = subprocess.CompletedProcess(
            ["pg_restore"], 0, self.toc_output(), ""
        )
        with mock.patch.object(
            transfer, "TRANSFER_ARCHIVE_TOC_ENTRIES", 3
        ), mock.patch.object(
            transfer, "TRANSFER_TOC_ENTRIES", 3
        ), mock.patch.object(
            transfer,
            "TRANSFER_TOC_SEMANTIC_SHA256",
            self.toc_semantic_sha256(),
        ), mock.patch.object(transfer.subprocess, "run", return_value=completed):
            _raw_sha, count, semantic_sha, use_list = transfer._normalized_toc(
                "/trusted/pg_restore", Path("/private/database.dump")
            )
        self.assertEqual(count, 3)
        self.assertEqual(semantic_sha, self.toc_semantic_sha256())
        self.assertEqual(
            use_list,
            b"1; 0 0 SCHEMA - qa_mapping_test qa_mapping_owner\n"
            b"2; 0 0 EXTENSION - pgcrypto \n"
            b"3; 0 0 COMMENT - EXTENSION pgcrypto \n",
        )

    def test_toc_parser_refuses_noncanonical_or_hidden_records(self):
        canonical = self.toc_output().splitlines()
        mutations = {
            "whitespace": [
                line.replace("1; 0 0", "1;  0 0") if line.startswith("1;") else line
                for line in canonical
            ],
            "commented": [
                "; 1; 0 0 SCHEMA - qa_mapping_test qa_mapping_owner"
                if line.startswith("1;")
                else line
                for line in canonical
            ],
            "late-comment": [*canonical, "; unexpected"],
            "security-label": [
                "3; 0 0 SECURITY LABEL qa_mapping_test TABLE prices qa_mapping_owner"
                if line.startswith("3;")
                else line
                for line in canonical
            ],
            "wrong-schema": [
                "3; 0 0 TABLE public qa_mapping_test qa_mapping_owner"
                if line.startswith("3;")
                else line
                for line in canonical
            ],
            "duplicate-id": [
                line.replace("3; 0 0", "2; 0 0") if line.startswith("3;") else line
                for line in canonical
            ],
        }
        for label, lines in mutations.items():
            completed = subprocess.CompletedProcess(
                ["pg_restore"], 0, "\n".join(lines) + "\n", ""
            )
            with self.subTest(label=label), mock.patch.object(
                transfer, "TRANSFER_ARCHIVE_TOC_ENTRIES", 3
            ), mock.patch.object(
                transfer, "TRANSFER_TOC_ENTRIES", 3
            ), mock.patch.object(
                transfer,
                "TRANSFER_TOC_SEMANTIC_SHA256",
                self.toc_semantic_sha256(),
            ), mock.patch.object(
                transfer.subprocess, "run", return_value=completed
            ), self.assertRaisesRegex(
                SyntheticStagingTransferError, "table of contents differs"
            ):
                transfer._normalized_toc(
                    "/trusted/pg_restore", Path("/private/database.dump")
                )

    def test_dump_swap_after_verification_never_reaches_pg_restore(self):
        with tempfile.TemporaryDirectory(
            prefix="buffalo-transfer-swap-"
        ) as directory:
            root = Path(directory)
            root.chmod(0o700)
            original = b"reviewed custom dump bytes"
            dump = root / DUMP_NAME
            dump.write_bytes(original)
            dump.chmod(0o600)
            artifact = transfer.VerifiedTransferArtifact(
                root=root,
                manifest_path=root / MANIFEST_NAME,
                dump_path=dump,
                manifest_sha256=self.MANIFEST_SHA256,
                manifest={
                    "dump": {
                        "bytes": len(original),
                        "sha256": hashlib.sha256(original).hexdigest(),
                    }
                },
            )
            replacement = root / "replacement"
            replacement.write_bytes(b"unreviewed replacement")
            replacement.chmod(0o600)
            replacement.replace(dump)
            with mock.patch.object(
                transfer, "_postgres_program", return_value="/trusted/pg_restore"
            ), mock.patch.object(transfer.subprocess, "run") as run:
                with self.assertRaisesRegex(
                    SyntheticStagingTransferError,
                    "dump changed before restore",
                ):
                    transfer._restore_dump(
                        artifact=artifact,
                        admin_url=(
                            f"postgresql://admin@127.0.0.1:{self.PORT}/"
                            f"{EXPECTED_DATABASE}"
                        ),
                    )
            run.assert_not_called()

    def test_export_cli_requires_a_fresh_initializer_transition(self):
        environment = {
            "BUFFALO_SYNTHETIC_TRANSFER_SOURCE_URL": (
                f"postgresql://qa_release_login@127.0.0.1:{self.PORT}/"
                f"{EXPECTED_DATABASE}?options=-c%20role%3Dqa_mapping_owner"
            ),
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            transfer_cli,
            "initialize",
            return_value={"initialized": False},
        ), mock.patch.object(transfer_cli, "create_transfer_artifact") as create:
            with self.assertRaisesRegex(
                SystemExit, "source was not freshly initialized"
            ):
                transfer_cli.main(
                    [
                        "export",
                        "--confirm-database",
                        EXPECTED_DATABASE,
                        "--confirm-service-state",
                        "STOPPED",
                        "--label",
                        "fresh-only",
                    ]
                )
        create.assert_not_called()

    def test_operator_cli_wires_prepare_and_keeps_restore_secrets_out_of_output(self):
        environment = {
            "BUFFALO_SYNTHETIC_TRANSFER_RUNTIME_URL": (
                f"postgresql://{RUNTIME_LOGIN}@127.0.0.1:{self.PORT}/"
                f"{EXPECTED_DATABASE}"
            ),
            "BUFFALO_SYNTHETIC_TRANSFER_ARTIFACT": "/private/artifact",
            "BUFFALO_SYNTHETIC_TRANSFER_MAINTENANCE_ADMIN_URL": (
                f"postgresql://admin@127.0.0.1:{self.PORT}/postgres"
            ),
            "BUFFALO_SYNTHETIC_TRANSFER_ADMIN_URL": (
                f"postgresql://admin@127.0.0.1:{self.PORT}/{EXPECTED_DATABASE}"
            ),
            "BUFFALO_SYNTHETIC_TRANSFER_PROVISIONER_URL": (
                f"postgresql://{PROVISIONER}@127.0.0.1:{self.PORT}/"
                f"{EXPECTED_DATABASE}"
            ),
        }
        common = [
            "--confirm-database",
            EXPECTED_DATABASE,
            "--confirm-service-state",
            "STOPPED",
            "--manifest-sha256",
            self.MANIFEST_SHA256,
            "--source-commit",
            self.COMMIT,
            "--source-tree",
            self.TREE,
        ]
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            transfer_cli, "target_from_environment", return_value=self.target()
        ), mock.patch.object(
            transfer_cli,
            "prepare_transfer_destination",
            return_value={"prepared": True},
        ) as prepare, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(transfer_cli.main(["prepare-target", *common]), 0)
        self.assertEqual(
            prepare.call_args.kwargs["maintenance_admin_url"],
            environment["BUFFALO_SYNTHETIC_TRANSFER_MAINTENANCE_ADMIN_URL"],
        )

        provisioner_read, provisioner_write = os.pipe()
        runtime_read, runtime_write = os.pipe()
        os.write(provisioner_write, self.PROVISIONER_SECRET.encode("ascii"))
        os.write(runtime_write, self.RUNTIME_SECRET.encode("ascii"))
        os.close(provisioner_write)
        os.close(runtime_write)
        output = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            transfer_cli, "target_from_environment", return_value=self.target()
        ), mock.patch.object(
            transfer_cli,
            "restore_and_transition",
            return_value={"restored": True},
        ) as restore, contextlib.redirect_stdout(output):
            self.assertEqual(
                transfer_cli.main(
                    [
                        "restore",
                        *common,
                        "--provisioner-secret-fd",
                        str(provisioner_read),
                        "--runtime-secret-fd",
                        str(runtime_read),
                    ]
                ),
                0,
            )
        self.assertEqual(
            restore.call_args.kwargs["provisioner_secret"],
            self.PROVISIONER_SECRET,
        )
        self.assertEqual(
            restore.call_args.kwargs["runtime_secret"], self.RUNTIME_SECRET
        )
        self.assertNotIn(self.PROVISIONER_SECRET, output.getvalue())
        self.assertNotIn(self.RUNTIME_SECRET, output.getvalue())


class _PostgresCluster:
    def __init__(self, root: Path, *, admin: str) -> None:
        self.root = root
        self.admin = admin
        self.data = root / "data"
        self.log = root / "postgres.log"
        self.started = False
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = int(listener.getsockname()[1])

    @staticmethod
    def _program(name: str) -> str:
        value = shutil.which(name)
        if value is None:
            raise RuntimeError(f"PostgreSQL 16 {name} is required")
        return value

    def _run(self, arguments: list[str]) -> None:
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("PG")
        }
        result = subprocess.run(
            arguments,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip())

    def start(self) -> None:
        self._run(
            [
                self._program("initdb"),
                "-D",
                str(self.data),
                "-U",
                self.admin,
                "-A",
                "trust",
                "--no-locale",
                "--encoding=UTF8",
            ]
        )
        hba = self.data / "pg_hba.conf"
        hba.write_text(
            "host buffalo_synthetic_staging_demo "
            "buffalo_synthetic_provisioner 127.0.0.1/32 scram-sha-256\n"
            "host buffalo_synthetic_staging_demo "
            "buffalo_synthetic_runtime 127.0.0.1/32 scram-sha-256\n"
            + hba.read_text("utf-8"),
            encoding="utf-8",
        )
        self._run(
            [
                self._program("pg_ctl"),
                "-D",
                str(self.data),
                "-l",
                str(self.log),
                "-o",
                f"-h 127.0.0.1 -p {self.port} -k {self.root}",
                "-w",
                "start",
            ]
        )
        self.started = True

    def stop(self) -> None:
        if self.started:
            self._run(
                [
                    self._program("pg_ctl"),
                    "-D",
                    str(self.data),
                    "-m",
                    "fast",
                    "-w",
                    "stop",
                ]
            )
            self.started = False

    def url(self, database: str, *, user: str | None = None, query: str = "") -> str:
        suffix = f"?{query}" if query else ""
        return (
            f"postgresql://{user or self.admin}@127.0.0.1:{self.port}/"
            f"{database}{suffix}"
        )

    def scaffold(self, *, sentinel: bool) -> None:
        with psycopg.connect(self.url("postgres"), autocommit=True) as conn:
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
                sql.SQL("CREATE DATABASE {} OWNER qa_mapping_owner").format(
                    sql.Identifier(EXPECTED_DATABASE)
                )
            )
            if sentinel:
                conn.execute("CREATE DATABASE transfer_sentinel")
            for database in (
                "postgres",
                "template0",
                "template1",
                *(('transfer_sentinel',) if sentinel else ()),
            ):
                conn.execute(
                    sql.SQL(
                        "REVOKE CONNECT,TEMPORARY ON DATABASE {} FROM PUBLIC"
                    ).format(sql.Identifier(database))
                )
        if sentinel:
            with psycopg.connect(
                self.url("transfer_sentinel"), autocommit=True
            ) as conn:
                conn.execute("CREATE TABLE public.preserved(value text PRIMARY KEY)")
                conn.execute("INSERT INTO public.preserved VALUES ('unchanged')")


class SyntheticStagingTransferTests(unittest.TestCase):
    BUSINESS_DATE = date(2026, 10, 5)
    PROVISIONER_SECRET = "P" * 48
    RUNTIME_SECRET = "R" * 48

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if any(shutil.which(name) is None for name in ("initdb", "pg_ctl", "pg_dump", "pg_restore")):
            raise RuntimeError("PostgreSQL 16 transfer tools are required")
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="buffalo-staging-transfer-postgres-"
        )
        cls.addClassCleanup(cls._temporary.cleanup)
        root = Path(cls._temporary.name)
        cls._source = _PostgresCluster(root / "source", admin="transfer_source_admin")
        cls._destination = _PostgresCluster(
            root / "destination", admin="transfer_destination_admin"
        )
        cls._source.root.mkdir(mode=0o700)
        cls._destination.root.mkdir(mode=0o700)
        cls._source.start()
        cls.addClassCleanup(cls._source.stop)
        cls._destination.start()
        cls.addClassCleanup(cls._destination.stop)
        cls._source.scaffold(sentinel=False)

        from initialize_synthetic_demo import (
            DEVELOPMENT_FORECAST_V2_PROFILE,
            initialize,
        )

        cls._source_initializer = cls._source.url(
            EXPECTED_DATABASE,
            user="qa_release_login",
            query="options=-c%20role%3Dqa_mapping_owner",
        )
        result = initialize(
            cls._source_initializer,
            cls.BUSINESS_DATE,
            profile=DEVELOPMENT_FORECAST_V2_PROFILE,
        )
        if result.get("initialized") is not True:
            raise AssertionError("transfer source was not freshly initialized")

        cls._transfer_root = root / "transfer"
        cls._transfer_root.mkdir(mode=0o700)
        source_root = Path(__file__).resolve().parents[2]
        cls._source_identity = {"commit": "1" * 40, "tree": "2" * 40}
        with mock.patch.object(
            transfer, "_source_identity", return_value=cls._source_identity
        ):
            cls._manifest_path = create_transfer_artifact(
                source_url=cls._source_initializer,
                source_admin_url=cls._source.url(EXPECTED_DATABASE),
                transfer_root=cls._transfer_root,
                source_root=source_root,
                label="canonical-v2",
            )
        cls._manifest_sha = hashlib.sha256(
            cls._manifest_path.read_bytes()
        ).hexdigest()
        cls._artifact_root = cls._manifest_path.parent
        cls._manifest = json.loads(cls._manifest_path.read_text("ascii"))

        destination_target_url = cls._destination.url(
            EXPECTED_DATABASE, user=RUNTIME_LOGIN
        )
        cls._target = SyntheticStagingTarget(
            database_url=destination_target_url,
            expected_private_host="127.0.0.1",
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
            transfer_manifest_sha256=cls._manifest_sha,
            owned_local_port=cls._destination.port,
        )
        cls._destination_admin = cls._destination.url(EXPECTED_DATABASE)
        cls._destination_provisioner = cls._destination.url(
            EXPECTED_DATABASE, user=PROVISIONER
        )
        cls._destination_runtime = destination_target_url

        with mock.patch.object(
            transfer, "_source_identity", return_value=cls._source_identity
        ):
            cls._prepare_proof = prepare_transfer_destination(
                artifact_root=cls._artifact_root,
                expected_manifest_sha256=cls._manifest_sha,
                maintenance_admin_url=cls._destination.url("postgres"),
                target=cls._target,
                source_root=source_root,
                expected_source_commit=cls._source_identity["commit"],
                expected_source_tree=cls._source_identity["tree"],
            )
        with psycopg.connect(
            cls._destination.url("postgres"), autocommit=True
        ) as conn:
            conn.execute("CREATE DATABASE transfer_sentinel")
            conn.execute(
                "REVOKE CONNECT,TEMPORARY ON DATABASE transfer_sentinel FROM PUBLIC"
            )
        with psycopg.connect(
            cls._destination.url("transfer_sentinel"), autocommit=True
        ) as conn:
            conn.execute("CREATE TABLE public.preserved(value text PRIMARY KEY)")
            conn.execute("INSERT INTO public.preserved VALUES ('unchanged')")

        with psycopg.connect(cls._destination_admin, autocommit=True) as conn:
            conn.execute("CREATE TABLE public.unexpected(value integer)")
            try:
                transfer._preflight_empty_destination(
                    conn,
                    source_system_identifier=cls._manifest["source"]["database"][
                        "system_identifier"
                    ],
                )
            except SyntheticStagingTransferError as exc:
                cls._unexpected_preflight = str(exc)
            else:
                raise AssertionError("unexpected destination object was accepted")
            conn.execute("DROP TABLE public.unexpected")
            conn.execute(
                sql.SQL("ALTER DATABASE {} SET application_name='unexpected'").format(
                    sql.Identifier(EXPECTED_DATABASE)
                )
            )
            try:
                transfer._preflight_empty_destination(
                    conn,
                    source_system_identifier=cls._manifest["source"]["database"][
                        "system_identifier"
                    ],
                )
            except SyntheticStagingTransferError as exc:
                cls._setting_preflight = str(exc)
            else:
                raise AssertionError("database-wide setting was accepted")
            conn.execute(
                sql.SQL("ALTER DATABASE {} RESET application_name").format(
                    sql.Identifier(EXPECTED_DATABASE)
                )
            )
            cls._core_privilege_preflight_failures = []
            for grant, revoke in (
                (
                    "GRANT SELECT ON pg_catalog.pg_authid TO PUBLIC",
                    "REVOKE SELECT ON pg_catalog.pg_authid FROM PUBLIC",
                ),
                (
                    "GRANT CREATE ON SCHEMA pg_catalog TO PUBLIC",
                    "REVOKE CREATE ON SCHEMA pg_catalog FROM PUBLIC",
                ),
                (
                    "GRANT SET ON PARAMETER session_replication_role TO PUBLIC",
                    "REVOKE SET ON PARAMETER session_replication_role FROM PUBLIC",
                ),
            ):
                conn.execute(grant)
                try:
                    transfer._preflight_empty_destination(
                        conn,
                        source_system_identifier=cls._manifest["source"]["database"][
                            "system_identifier"
                        ],
                    )
                except SyntheticStagingTransferError as exc:
                    cls._core_privilege_preflight_failures.append(str(exc))
                else:
                    raise AssertionError(
                        "dangerous core privilege was accepted before restore"
                    )
                finally:
                    conn.execute(revoke)
            conn.execute(
                "CREATE FUNCTION information_schema.transfer_forbidden() "
                "RETURNS text LANGUAGE sql SECURITY DEFINER "
                "SET search_path=pg_catalog AS 'SELECT current_user::text'"
            )
            try:
                transfer._preflight_empty_destination(
                    conn,
                    source_system_identifier=cls._manifest["source"]["database"][
                        "system_identifier"
                    ],
                )
            except SyntheticStagingTransferError as exc:
                cls._information_schema_preflight_failure = str(exc)
            else:
                raise AssertionError(
                    "information_schema executable was accepted before restore"
                )
            finally:
                conn.execute(
                    "DROP FUNCTION information_schema.transfer_forbidden()"
                )

        with mock.patch.object(
            transfer, "_source_identity", return_value=cls._source_identity
        ):
            cls._proof = restore_and_transition(
                artifact_root=cls._artifact_root,
                expected_manifest_sha256=cls._manifest_sha,
                admin_url=cls._destination_admin,
                provisioner_url=cls._destination_provisioner,
                runtime_url=cls._destination_runtime,
                provisioner_secret=cls.PROVISIONER_SECRET,
                runtime_secret=cls.RUNTIME_SECRET,
                target=cls._target,
                source_root=source_root,
                expected_source_commit=cls._source_identity["commit"],
                expected_source_tree=cls._source_identity["tree"],
            )

    def test_01_artifact_is_canonical_private_and_source_bound(self):
        verified = verify_transfer_artifact(
            artifact_root=self._artifact_root,
            expected_manifest_sha256=self._manifest_sha,
            expected_source_commit=self._source_identity["commit"],
            expected_source_tree=self._source_identity["tree"],
        )
        self.assertEqual(verified.manifest, self._manifest)
        self.assertEqual(
            sorted(path.name for path in self._artifact_root.iterdir()),
            [DUMP_NAME, MANIFEST_NAME],
        )
        self.assertEqual(stat.S_IMODE(self._artifact_root.stat().st_mode), 0o700)
        for path in (self._manifest_path, self._artifact_root / DUMP_NAME):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self._manifest["source"]["git"], self._source_identity)
        toc = subprocess.run(
            [shutil.which("pg_restore") or "pg_restore", "--list", str(self._artifact_root / DUMP_NAME)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for kind in transfer._FORBIDDEN_TOC_KINDS:
            self.assertNotIn(kind, f" {toc} ")

    def test_02_dump_restore_crosses_distinct_cluster_system_identities(self):
        with psycopg.connect(self._source.url("postgres")) as source_conn:
            source_identity = source_conn.execute(
                "SELECT system_identifier FROM pg_catalog.pg_control_system()"
            ).fetchone()[0]
        with psycopg.connect(self._destination.url("postgres")) as destination_conn:
            destination_identity = destination_conn.execute(
                "SELECT system_identifier FROM pg_catalog.pg_control_system()"
            ).fetchone()[0]
        self.assertNotEqual(source_identity, destination_identity)
        self.assertEqual(
            self._manifest["source"]["database"]["system_identifier"],
            str(source_identity),
        )
        self.assertEqual(
            self._proof["runtime_attestation_identity"],
            EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        )
        self.assertEqual(
            self._proof["fixture_manifest_sha256"],
            IMMUTABLE_FIXTURE_MANIFEST_SHA256,
        )
        self.assertEqual(
            self._proof["administrative_credential_removal"],
            "EXTERNAL_REQUIRED",
        )
        self.assertEqual(self._prepare_proof["database"], EXPECTED_DATABASE)
        self.assertEqual(
            self._prepare_proof["manifest_sha256"], self._manifest_sha
        )

    def test_03_runtime_attestation_repeats_after_transfer(self):
        with psycopg.connect(
            self._destination_runtime, password=self.RUNTIME_SECRET
        ) as conn:
            observed = attest_runtime_connection(conn, self._target)
            conn.rollback()
        self.assertEqual(observed, EXPECTED_RUNTIME_ATTESTATION_IDENTITY)

    def test_04_role_credentials_are_distinct_and_scram_authenticated(self):
        for url, password in (
            (self._destination_runtime, None),
            (self._destination_runtime, "wrong-runtime-secret"),
            (self._destination_runtime, self.PROVISIONER_SECRET),
            (self._destination_provisioner, None),
            (self._destination_provisioner, self.RUNTIME_SECRET),
        ):
            with self.subTest(url=url, supplied=password is not None):
                with self.assertRaises(psycopg.OperationalError):
                    psycopg.connect(url, password=password, connect_timeout=2)
        with psycopg.connect(
            self._destination_runtime, password=self.RUNTIME_SECRET
        ) as runtime:
            self.assertEqual(
                runtime.execute("SELECT session_user::text").fetchone(),
                (RUNTIME_LOGIN,),
            )
        with psycopg.connect(
            self._destination_provisioner, password=self.PROVISIONER_SECRET
        ) as provisioner:
            self.assertEqual(
                provisioner.execute("SELECT session_user::text").fetchone(),
                (PROVISIONER,),
            )

    def test_05_unexpected_destination_object_was_refused_before_restore(self):
        self.assertEqual(
            self._unexpected_preflight,
            "synthetic transfer destination core privilege envelope differs",
        )
        self.assertEqual(
            self._setting_preflight,
            "synthetic transfer destination is not empty",
        )
        self.assertEqual(
            self._core_privilege_preflight_failures,
            [
                "synthetic transfer destination core privilege envelope differs"
            ]
            * 3,
        )
        self.assertEqual(
            self._information_schema_preflight_failure,
            "synthetic transfer destination core privilege envelope differs",
        )

    def test_06_second_restore_is_refused_without_destructive_fallback(self):
        with mock.patch.object(
            transfer, "_source_identity", return_value=self._source_identity
        ):
            with self.assertRaisesRegex(
                SyntheticStagingTransferError,
                "destination identity differs|destination role state differs|destination is not empty",
            ):
                restore_and_transition(
                    artifact_root=self._artifact_root,
                    expected_manifest_sha256=self._manifest_sha,
                    admin_url=self._destination_admin,
                    provisioner_url=self._destination_provisioner,
                    runtime_url=self._destination_runtime,
                    provisioner_secret=self.PROVISIONER_SECRET,
                    runtime_secret=self.RUNTIME_SECRET,
                    target=self._target,
                    source_root=Path(__file__).resolve().parents[2],
                    expected_source_commit=self._source_identity["commit"],
                    expected_source_tree=self._source_identity["tree"],
                )
        with psycopg.connect(
            self._destination_runtime, password=self.RUNTIME_SECRET
        ) as conn:
            self.assertEqual(
                attest_runtime_connection(conn, self._target),
                EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
            )

    def test_07_second_target_preparation_is_refused_without_reset(self):
        with mock.patch.object(
            transfer, "_source_identity", return_value=self._source_identity
        ):
            with self.assertRaisesRegex(
                SyntheticStagingTransferError,
                "maintenance database inventory differs",
            ):
                prepare_transfer_destination(
                    artifact_root=self._artifact_root,
                    expected_manifest_sha256=self._manifest_sha,
                    maintenance_admin_url=self._destination.url("postgres"),
                    target=self._target,
                    source_root=Path(__file__).resolve().parents[2],
                    expected_source_commit=self._source_identity["commit"],
                    expected_source_tree=self._source_identity["tree"],
                )
        with psycopg.connect(
            self._destination_runtime, password=self.RUNTIME_SECRET
        ) as conn:
            self.assertEqual(
                attest_runtime_connection(conn, self._target),
                EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
            )

    def test_08_unrelated_destination_database_is_unchanged(self):
        with psycopg.connect(
            self._destination.url("transfer_sentinel")
        ) as conn:
            self.assertEqual(
                conn.execute("SELECT value FROM public.preserved").fetchall(),
                [("unchanged",)],
            )

    def test_09_wrong_or_noncanonical_manifest_digest_is_refused(self):
        for digest in ("f" * 64, "F" * 64, "not-a-digest"):
            with self.subTest(digest=digest), self.assertRaises(
                SyntheticStagingTransferError
            ):
                verify_transfer_artifact(
                    artifact_root=self._artifact_root,
                    expected_manifest_sha256=digest,
                    expected_source_commit=self._source_identity["commit"],
                    expected_source_tree=self._source_identity["tree"],
                )
        with self.assertRaisesRegex(
            SyntheticStagingTransferError, "source identity differs"
        ):
            verify_transfer_artifact(
                artifact_root=self._artifact_root,
                expected_manifest_sha256=self._manifest_sha,
                expected_source_commit="3" * 40,
                expected_source_tree=self._source_identity["tree"],
            )

    def test_10_manifest_and_dump_mutation_are_refused(self):
        with tempfile.TemporaryDirectory(
            prefix="buffalo-staging-transfer-mutation-"
        ) as directory:
            root = Path(directory) / "artifact"
            shutil.copytree(self._artifact_root, root)
            os.chmod(root, 0o700)
            for path in root.iterdir():
                os.chmod(path, 0o600)
            manifest = root / MANIFEST_NAME
            manifest.write_bytes(manifest.read_bytes() + b"\n")
            os.chmod(manifest, 0o600)
            with self.assertRaisesRegex(
                SyntheticStagingTransferError, "manifest digest differs"
            ):
                verify_transfer_artifact(
                    artifact_root=root,
                    expected_manifest_sha256=self._manifest_sha,
                    expected_source_commit=self._source_identity["commit"],
                    expected_source_tree=self._source_identity["tree"],
                )

    def test_11_custom_rule_and_final_hidden_schema_are_refused(self):
        with psycopg.connect(self._source_initializer) as conn:
            conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
            conn.execute(
                "CREATE RULE transfer_forbidden_rule AS ON UPDATE "
                "TO qa_mapping_test.prices DO INSTEAD NOTHING"
            )
            with self.assertRaisesRegex(
                SyntheticStagingTransferError, "source contract differs"
            ):
                transfer._legacy_source_snapshot(conn)
            conn.rollback()

        with psycopg.connect(self._destination_admin) as conn:
            conn.execute("CREATE SCHEMA transfer_hidden")
            with self.assertRaisesRegex(
                SyntheticStagingTransferError, "global envelope differs"
            ):
                    transfer._audit_global_envelope(conn, stage="final")
            conn.rollback()

        with tempfile.TemporaryDirectory(
            prefix="buffalo-staging-transfer-mutation-"
        ) as directory:
            root = Path(directory) / "artifact"
            shutil.copytree(self._artifact_root, root)
            os.chmod(root, 0o700)
            for path in root.iterdir():
                os.chmod(path, 0o600)
            dump = root / DUMP_NAME
            with dump.open("r+b") as handle:
                handle.seek(-1, os.SEEK_END)
                original = handle.read(1)
                handle.seek(-1, os.SEEK_END)
                handle.write(bytes([original[0] ^ 0x01]))
            with self.assertRaisesRegex(
                SyntheticStagingTransferError, "dump digest differs"
            ):
                verify_transfer_artifact(
                    artifact_root=root,
                    expected_manifest_sha256=self._manifest_sha,
                    expected_source_commit=self._source_identity["commit"],
                    expected_source_tree=self._source_identity["tree"],
                )


if __name__ == "__main__":
    unittest.main()
