from __future__ import annotations

import importlib.util
from pathlib import Path
import signal
import tomllib
import unittest
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "replit_production_start.py"
SPEC = importlib.util.spec_from_file_location("replit_production_start", MODULE_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import contract guard
    raise RuntimeError("unable to load Replit production supervisor")
STARTUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STARTUP)


class FakeProcess:
    def __init__(self, *, returncode: int | None = None, wait_result: int = 0) -> None:
        self.returncode = returncode
        self.wait_result = wait_result
        self.terminated = False
        self.killed = False
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.wait_result

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def send_signal(self, signum: int) -> None:
        self.signals.append(signum)


class ReplitProductionStartupTests(unittest.TestCase):
    def test_wait_for_fastapi_requires_health_before_returning(self) -> None:
        process = FakeProcess()
        health_results = iter((False, False, True))
        clock = [0.0]
        sleeps: list[float] = []

        def healthcheck() -> bool:
            return next(health_results)

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        STARTUP.wait_for_fastapi(
            process,
            timeout_seconds=5.0,
            poll_interval_seconds=0.25,
            healthcheck=healthcheck,
            monotonic=monotonic,
            sleep=sleep,
        )

        self.assertEqual(sleeps, [0.25, 0.25])

    def test_wait_for_fastapi_fails_closed_on_timeout(self) -> None:
        process = FakeProcess()
        clock = [0.0]

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        with self.assertRaisesRegex(RuntimeError, "did not become healthy"):
            STARTUP.wait_for_fastapi(
                process,
                timeout_seconds=0.2,
                poll_interval_seconds=0.1,
                healthcheck=lambda: False,
                monotonic=monotonic,
                sleep=sleep,
            )

    def test_wait_for_fastapi_fails_if_backend_exits(self) -> None:
        process = FakeProcess(returncode=7)
        healthcheck = Mock(return_value=True)

        with self.assertRaisesRegex(RuntimeError, "exited before readiness with code 7"):
            STARTUP.wait_for_fastapi(
                process,
                timeout_seconds=5.0,
                poll_interval_seconds=0.1,
                healthcheck=healthcheck,
            )

        healthcheck.assert_not_called()

    def test_wait_for_services_fails_if_fastapi_dies_after_readiness(self) -> None:
        fastapi = FakeProcess(returncode=9)
        node = FakeProcess()

        with self.assertRaisesRegex(RuntimeError, "exited after readiness with code 9"):
            STARTUP.wait_for_services(
                fastapi,
                node,
                poll_interval_seconds=0.1,
                sleep=lambda _seconds: None,
            )

    def test_wait_for_services_returns_node_exit_code(self) -> None:
        fastapi = FakeProcess()
        node = FakeProcess(returncode=3)

        self.assertEqual(
            STARTUP.wait_for_services(
                fastapi,
                node,
                poll_interval_seconds=0.1,
                sleep=lambda _seconds: None,
            ),
            3,
        )

    def test_main_starts_node_only_after_fastapi_readiness(self) -> None:
        events: list[str] = []
        fastapi = FakeProcess()
        node = FakeProcess()

        def start_fastapi() -> FakeProcess:
            events.append("start_fastapi")
            return fastapi

        def wait_for_fastapi(*_args: object, **_kwargs: object) -> None:
            events.append("wait_fastapi")

        def start_node() -> FakeProcess:
            events.append("start_node")
            return node

        def wait_for_services(*_args: object, **_kwargs: object) -> int:
            events.append("wait_services")
            return 0

        with (
            patch.object(STARTUP, "start_fastapi", side_effect=start_fastapi),
            patch.object(STARTUP, "wait_for_fastapi", side_effect=wait_for_fastapi),
            patch.object(STARTUP, "start_node", side_effect=start_node),
            patch.object(STARTUP, "wait_for_services", side_effect=wait_for_services),
            patch.object(STARTUP.signal, "signal", return_value=signal.SIG_DFL),
        ):
            self.assertEqual(STARTUP.main(), 0)

        self.assertEqual(
            events[:4],
            ["start_fastapi", "wait_fastapi", "start_node", "wait_services"],
        )
        self.assertTrue(node.terminated)
        self.assertTrue(fastapi.terminated)

    def test_main_never_starts_node_when_fastapi_readiness_fails(self) -> None:
        fastapi = FakeProcess()
        start_node = Mock()

        with (
            patch.object(STARTUP, "start_fastapi", return_value=fastapi),
            patch.object(
                STARTUP,
                "wait_for_fastapi",
                side_effect=RuntimeError("backend unavailable"),
            ),
            patch.object(STARTUP, "start_node", start_node),
            patch.object(STARTUP.signal, "signal", return_value=signal.SIG_DFL),
        ):
            self.assertEqual(STARTUP.main(), 1)

        start_node.assert_not_called()
        self.assertTrue(fastapi.terminated)

    def test_artifact_routes_production_through_supervisor(self) -> None:
        with (REPO_ROOT / "artifacts" / "api-server" / ".replit-artifact" / "artifact.toml").open(
            "rb"
        ) as handle:
            artifact = tomllib.load(handle)

        service = artifact["services"][0]
        self.assertEqual(service["localPort"], 8080)
        self.assertEqual(service["paths"], ["/api", "/procurement"])
        self.assertEqual(
            service["production"]["run"]["args"],
            ["python3", "scripts/replit_production_start.py"],
        )
        self.assertEqual(
            service["production"]["health"]["startup"]["path"],
            "/api/healthz",
        )

    def test_replit_build_uses_pinned_uv_without_obsolete_runtime_command(self) -> None:
        with (REPO_ROOT / ".replit").open("rb") as handle:
            config = tomllib.load(handle)

        deployment = config["deployment"]
        self.assertEqual(deployment["build"], "uvx uv@0.12.3 sync --frozen")
        self.assertNotIn("run", deployment)
        self.assertNotIn("packager", config)

    def test_pyproject_does_not_block_replit_platform_uv_hook(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)

        self.assertNotIn("uv", pyproject.get("tool", {}))


if __name__ == "__main__":
    unittest.main()
