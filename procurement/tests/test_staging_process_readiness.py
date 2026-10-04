"""Focused worker-side readiness timing and failure-class tests."""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from uvicorn import Config, Server

from procurement_os import private_research_app, staging_process


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


if __name__ == "__main__":
    unittest.main()
