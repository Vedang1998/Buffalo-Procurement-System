"""Focused worker-side readiness timing and failure-class tests."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import psycopg
from uvicorn import Config, Server

from procurement_os import private_research_app, staging_process
from procurement_os.synthetic_staging_database import (
    EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
    SyntheticStagingDatabaseError,
)


class _Writer:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.closed = False

    def emit_ready(self, identity: str) -> None:
        self.events.append(("READY", identity))
        self.closed = True

    def emit_failure(self, failure: str) -> None:
        self.events.append(("FAILED", failure))
        self.closed = True


async def _application(scope, receive, send):
    del scope, receive, send


class StagingProcessReadinessTests(unittest.TestCase):
    def _server(self, writer: _Writer) -> staging_process._ResearchReadinessServer:
        return staging_process._ResearchReadinessServer(
            Config(_application, lifespan="off"), readiness=writer
        )

    def _synthetic_server(
        self, writer: _Writer
    ) -> staging_process._SyntheticReadinessServer:
        return staging_process._SyntheticReadinessServer(
            Config(_application, lifespan="off"),
            readiness=writer,
            environment={"DATABASE_URL": "postgresql://runtime@private/staging"},
        )

    def test_ready_is_emitted_only_after_uvicorn_reports_started(self) -> None:
        writer = _Writer()
        server = self._server(writer)

        async def startup(instance, sockets=None):
            del sockets
            self.assertEqual(writer.events, [])
            instance.started = True

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            private_research_app,
            "staging_research_validation_identity",
            return_value="a" * 64,
        ):
            asyncio.run(server.startup())
        self.assertEqual(writer.events, [("READY", "a" * 64)])

    def test_lifespan_failure_uses_the_sanitized_semantic_class(self) -> None:
        writer = _Writer()
        server = self._server(writer)
        server.lifespan = mock.Mock(should_exit=True)

        async def startup(instance, sockets=None):
            del instance, sockets
            raise SystemExit(3)

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            private_research_app,
            "staging_research_startup_failure",
            return_value="INTEGRITY",
        ), self.assertRaises(SystemExit):
            asyncio.run(server.startup())
        self.assertEqual(writer.events, [("FAILED", "INTEGRITY")])

    def test_memory_failure_is_never_misclassified_as_a_crash(self) -> None:
        writer = _Writer()
        server = self._server(writer)

        async def startup(instance, sockets=None):
            del instance, sockets
            raise MemoryError

        with mock.patch.object(Server, "startup", new=startup), self.assertRaises(
            MemoryError
        ):
            asyncio.run(server.startup())
        self.assertEqual(writer.events, [("FAILED", "OOM")])

    def test_post_start_integrity_drift_is_not_semantic_readiness(self) -> None:
        writer = _Writer()
        server = self._server(writer)

        async def startup(instance, sockets=None):
            del sockets
            instance.started = True

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            private_research_app,
            "staging_research_validation_identity",
            side_effect=staging_process.StagingResearchValidationError(
                "injected integrity drift"
            ),
        ):
            asyncio.run(server.startup())
        self.assertTrue(server.should_exit)
        self.assertEqual(writer.events, [("FAILED", "INTEGRITY")])

    def test_synthetic_ready_follows_uvicorn_and_exact_database_attestation(self) -> None:
        writer = _Writer()
        server = self._synthetic_server(writer)
        ordering: list[str] = []

        async def startup(instance, sockets=None):
            del sockets
            self.assertEqual(writer.events, [])
            ordering.append("uvicorn")
            instance.started = True

        def attest() -> str:
            self.assertTrue(server.started)
            self.assertEqual(writer.events, [])
            ordering.append("database")
            return EXPECTED_RUNTIME_ATTESTATION_IDENTITY

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            server, "_attest_database", side_effect=attest
        ) as database:
            asyncio.run(server.startup())
        database.assert_called_once_with()
        self.assertEqual(ordering, ["uvicorn", "database"])
        self.assertEqual(
            writer.events,
            [("READY", EXPECTED_RUNTIME_ATTESTATION_IDENTITY)],
        )

    def test_synthetic_database_attestation_uses_one_bounded_repeatable_snapshot(self) -> None:
        writer = _Writer()
        server = self._synthetic_server(writer)
        connection = mock.MagicMock()
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        target = object()
        with mock.patch("psycopg.connect", return_value=context) as connect, mock.patch(
            "procurement_os.staging_process._validated_synthetic_pgpass",
            return_value="/run/buffalo/synthetic/private/pgpass",
        ) as credential, mock.patch(
            "procurement_os.synthetic_staging_database.target_from_environment",
            return_value=target,
        ), mock.patch(
            "procurement_os.synthetic_staging_database.attest_runtime_connection",
            return_value=EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        ) as attest:
            observed = server._attest_database()
        self.assertEqual(observed, EXPECTED_RUNTIME_ATTESTATION_IDENTITY)
        connect.assert_called_once_with(
            "postgresql://runtime@private/staging",
            connect_timeout=5,
            autocommit=False,
            passfile="/run/buffalo/synthetic/private/pgpass",
        )
        credential.assert_called_once_with(server._environment, target)
        self.assertEqual(
            [call.args[0] for call in connection.execute.call_args_list],
            [
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
                "SET LOCAL statement_timeout = '10000ms'",
                "SET LOCAL lock_timeout = '2000ms'",
                "SET LOCAL idle_in_transaction_session_timeout = '15000ms'",
            ],
        )
        attest.assert_called_once_with(connection, target)
        connection.rollback.assert_called_once_with()

    def test_synthetic_pgpass_is_inode_stable_private_and_role_local(self) -> None:
        with tempfile.TemporaryDirectory(prefix="buffalo-pgpass-") as raw:
            runtime_root = Path(raw) / "synthetic"
            private = runtime_root / "private"
            private.mkdir(parents=True)
            runtime_root.chmod(0o700)
            private.chmod(0o700)
            passfile = private / "pgpass"
            passfile.write_text(
                "host:5432:database:buffalo_synthetic_runtime:fixture-only-secret\n",
                encoding="utf-8",
            )
            passfile.chmod(0o600)
            environment = {
                "BUFFALO_STAGING_RUNTIME_ROOT": str(runtime_root),
                "PGPASSFILE": str(passfile),
            }
            target = mock.Mock(
                database_url=(
                    "postgresql://buffalo_synthetic_runtime@host:5432/database"
                )
            )
            self.assertEqual(
                staging_process._validated_synthetic_pgpass(environment, target),
                str(passfile),
            )

            passfile.chmod(0o640)
            with self.assertRaisesRegex(
                staging_process.SyntheticCredentialError, "file differs"
            ):
                staging_process._validated_synthetic_pgpass(environment, target)
            passfile.chmod(0o600)

            invalid_records = (
                "*:5432:database:buffalo_synthetic_runtime:fixture-only-secret\n",
                "host:5432:database:buffalo_synthetic_owner:fixture-only-secret\n",
                (
                    "host:5432:database:buffalo_synthetic_runtime:fixture-only-secret\n"
                    "host:5432:database:buffalo_synthetic_owner:extra-secret\n"
                ),
                "host:5432:database:buffalo_synthetic_runtime:\n",
            )
            for invalid_record in invalid_records:
                with self.subTest(invalid_record=invalid_record.count("\n")):
                    passfile.write_text(invalid_record, encoding="utf-8")
                    with self.assertRaises(
                        staging_process.SyntheticCredentialError
                    ):
                        staging_process._validated_synthetic_pgpass(
                            environment, target
                        )
            passfile.write_text(
                "host:5432:database:buffalo_synthetic_runtime:fixture-only-secret\n",
                encoding="utf-8",
            )

            outside = Path(raw) / "outside"
            outside.write_text("secret", encoding="utf-8")
            outside.chmod(0o600)
            with self.assertRaisesRegex(
                staging_process.SyntheticCredentialError, "path differs"
            ):
                staging_process._validated_synthetic_pgpass(
                    {**environment, "PGPASSFILE": str(outside)}, target
                )

            link = private / "linked-pgpass"
            os.symlink(passfile, link)
            with self.assertRaises(staging_process.SyntheticCredentialError):
                staging_process._validated_synthetic_pgpass(
                    {**environment, "PGPASSFILE": str(link)}, target
                )

    def test_synthetic_database_failures_are_sanitized_by_class(self) -> None:
        cases = (
            (psycopg.OperationalError("private DSN and password"), "DATABASE"),
            (psycopg.DatabaseError("private SQL assertion detail"), "INTEGRITY"),
            (SyntheticStagingDatabaseError("private catalog detail"), "INTEGRITY"),
            (RuntimeError("private implementation detail"), "ACTIVATION"),
        )

        async def startup(instance, sockets=None):
            del sockets
            instance.started = True

        for exception, expected in cases:
            with self.subTest(expected=expected):
                writer = _Writer()
                server = self._synthetic_server(writer)
                with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
                    server, "_attest_database", side_effect=exception
                ):
                    asyncio.run(server.startup())
                self.assertTrue(server.should_exit)
                self.assertEqual(writer.events, [("FAILED", expected)])
                self.assertNotIn("private", repr(writer.events))

    def test_synthetic_attestation_memory_failure_is_explicit_oom(self) -> None:
        writer = _Writer()
        server = self._synthetic_server(writer)

        async def startup(instance, sockets=None):
            del sockets
            instance.started = True

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            server, "_attest_database", side_effect=MemoryError
        ):
            asyncio.run(server.startup())
        self.assertTrue(server.should_exit)
        self.assertEqual(writer.events, [("FAILED", "OOM")])

    def test_synthetic_shutdown_during_attestation_never_emits_ready(self) -> None:
        writer = _Writer()
        server = self._synthetic_server(writer)

        async def startup(instance, sockets=None):
            del sockets
            instance.started = True

        def attest() -> str:
            server.should_exit = True
            return EXPECTED_RUNTIME_ATTESTATION_IDENTITY

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            server, "_attest_database", side_effect=attest
        ):
            asyncio.run(server.startup())
        self.assertEqual(writer.events, [("FAILED", "ACTIVATION")])

    def test_synthetic_attestation_cancellation_is_not_swallowed(self) -> None:
        writer = _Writer()
        server = self._synthetic_server(writer)

        async def startup(instance, sockets=None):
            del sockets
            instance.started = True

        async def cancelled(_function):
            raise asyncio.CancelledError

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            asyncio, "to_thread", side_effect=cancelled
        ), self.assertRaises(asyncio.CancelledError):
            asyncio.run(server.startup())
        self.assertTrue(server.should_exit)
        self.assertEqual(writer.events, [("FAILED", "ACTIVATION")])

    def test_synthetic_uvicorn_failure_never_attempts_database_attestation(self) -> None:
        writer = _Writer()
        server = self._synthetic_server(writer)

        async def startup(instance, sockets=None):
            del instance, sockets
            raise RuntimeError("private listener detail")

        with mock.patch.object(Server, "startup", new=startup), mock.patch.object(
            server, "_attest_database"
        ) as database, self.assertRaises(RuntimeError):
            asyncio.run(server.startup())
        database.assert_not_called()
        self.assertEqual(writer.events, [("FAILED", "ACTIVATION")])


if __name__ == "__main__":
    unittest.main()
