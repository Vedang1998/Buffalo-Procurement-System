"""G10 safety tests for the temporary Autoscale-to-G9 preflight bridge."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import copy
import inspect
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from procurement_os import api
from procurement_os import phase4_preflight_bridge as bridge


CONFIGURED_TOKEN = "configured-review-token-g10"
DATABASE_URL = "postgresql://production-user:production-password@db.invalid/neondb"
SERVER_ENVIRONMENT = {
    "REPLIT_DEPLOYMENT": "1",
    "DATABASE_URL": DATABASE_URL,
    "RECONCILIATION_REVIEW_TOKEN": CONFIGURED_TOKEN,
    "PHASE4_REVIEW_TOKEN_INPUT": CONFIGURED_TOKEN,
    "PYTHONPATH": "/nix/store/reviewed-sitecustomize/lib/python/site-packages",
    "REPLIT_PYTHONPATH": "/app/.pythonlibs/lib/python3.13/site-packages",
}
_REAL_POPEN = subprocess.Popen


def success_report(**updates):
    report = {
        "result": "PHASE4_PUBLISHED_PRODUCTION_PREFLIGHT",
        "mode": "READ_ONLY",
        "state": "A_FROZEN_PRODUCTION_BASELINE",
        "execution_git_sha": bridge.G9_EXECUTION_SHA,
        "execution_tree_sha": bridge.G9_EXECUTION_TREE,
        "mutation_state_machine_entered": False,
        "production_actions": {
            "ddl": 0,
            "dml": 0,
            "rebuild": 0,
            "readiness_writes": 0,
        },
        "preflight_evidence": {"safe": True},
    }
    report.update(updates)
    return report


def report_bytes(report=None):
    return json.dumps(report or success_report(), sort_keys=True).encode()


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes | None = None,
        stderr: bytes = b"",
        returncode: int | None = 0,
        effects: list[object] | None = None,
    ) -> None:
        self.stdout = report_bytes() if stdout is None else stdout
        self.stderr = stderr
        self.returncode = returncode
        self.effects = list(effects or [])

    def supervise(self):
        if self.effects:
            effect = self.effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            return effect
        if len(self.stdout) > bridge.MAX_CHILD_STREAM_BYTES or len(
            self.stderr
        ) > bridge.MAX_CHILD_STREAM_BYTES:
            raise bridge.Phase4PreflightBridgeError(
                status_code=502,
                code="G9_PREFLIGHT_OUTPUT_LIMIT",
                message="The reviewed Phase 4 preflight exceeded its output limit.",
            )
        return self.stdout, self.stderr


class BlockingProcess(FakeProcess):
    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.entered = entered
        self.release = release

    def supervise(self):
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("test did not release blocked child")
        return self.stdout, self.stderr


def _defensively_stop_real_process(process: subprocess.Popen[bytes]) -> None:
    """Keep every credential-free test subprocess bounded on assertion failure."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    for pipe in (process.stdout, process.stderr):
        if pipe is not None and not pipe.closed:
            pipe.close()


def _wait_for_file(path: Path, *, timeout: float = 2) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return path.read_text(encoding="utf-8")
        time.sleep(0.01)
    raise AssertionError(f"test subprocess did not create {path}")


def _assert_process_group_absent(test: unittest.TestCase, process_group_id: int) -> None:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return
    test.fail(f"test process group {process_group_id} remains alive")


def _assert_pid_absent(test: unittest.TestCase, process_id: int) -> None:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return
    test.fail(f"test process {process_id} remains alive")


class BridgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api.app)
        self.assertFalse(bridge._PREFLIGHT_LOCK.locked())
        self.assertFalse(bridge._PREFLIGHT_CAPACITY_QUARANTINED.is_set())

    def tearDown(self) -> None:
        self.assertFalse(bridge._PREFLIGHT_LOCK.locked())
        self.assertFalse(bridge._PREFLIGHT_CAPACITY_QUARANTINED.is_set())

    def spawn_real_process(
        self,
        source: str,
        *arguments: str,
    ) -> subprocess.Popen[bytes]:
        process = _REAL_POPEN(
            [sys.executable, "-I", "-S", "-c", source, *arguments],
            cwd="/",
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        self.addCleanup(_defensively_stop_real_process, process)
        return process

    def post_with_process(self, process: FakeProcess, **request_kwargs):
        with patch.dict(os.environ, SERVER_ENVIRONMENT, clear=True), patch.object(
            bridge.subprocess, "Popen", return_value=process
        ) as popen, patch.object(
            bridge, "_supervise_g9_process", side_effect=lambda child: child.supervise()
        ):
            response = self.client.post(
                "/internal/phase4-production-preflight", **request_kwargs
            )
        return response, popen


class RouteContractTests(BridgeTestCase):
    def test_route_is_post_only_and_synchronous(self):
        routes = [
            route
            for route in api.app.routes
            if isinstance(route, APIRoute)
            and route.path == "/internal/phase4-production-preflight"
        ]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].methods, {"POST"})
        self.assertFalse(inspect.iscoroutinefunction(routes[0].endpoint))
        response = self.client.get("/internal/phase4-production-preflight")
        self.assertEqual(response.status_code, 405)

    def test_route_is_hidden_from_openapi(self):
        self.assertNotIn(
            "/internal/phase4-production-preflight",
            api.app.openapi()["paths"],
        )


class AuthorizationTests(BridgeTestCase):
    def test_missing_configured_token_stops_before_child(self):
        environment = dict(SERVER_ENVIRONMENT)
        environment.pop("RECONCILIATION_REVIEW_TOKEN")
        with patch.dict(os.environ, environment, clear=True), patch.object(
            bridge.subprocess, "Popen"
        ) as popen:
            response = self.client.post("/internal/phase4-production-preflight")
        self.assertEqual(response.status_code, 503)
        popen.assert_not_called()

    def test_missing_input_token_stops_before_child(self):
        environment = dict(SERVER_ENVIRONMENT)
        environment.pop("PHASE4_REVIEW_TOKEN_INPUT")
        with patch.dict(os.environ, environment, clear=True), patch.object(
            bridge.subprocess, "Popen"
        ) as popen:
            response = self.client.post("/internal/phase4-production-preflight")
        self.assertEqual(response.status_code, 503)
        popen.assert_not_called()

    def test_mismatched_authorization_stops_before_child(self):
        environment = dict(SERVER_ENVIRONMENT)
        environment["PHASE4_REVIEW_TOKEN_INPUT"] = "different-server-token"
        with patch.dict(os.environ, environment, clear=True), patch.object(
            bridge.subprocess, "Popen"
        ) as popen:
            response = self.client.post("/internal/phase4-production-preflight")
        self.assertEqual(response.status_code, 403)
        popen.assert_not_called()


class InvocationBoundaryTests(BridgeTestCase):
    def test_valid_authorization_launches_exactly_one_child(self):
        process = FakeProcess()
        canonical_authorize = bridge.require_review_authorization

        def authorize_and_mutate_ambient_environment(configured, supplied):
            canonical_authorize(configured, supplied)
            os.environ["DATABASE_URL"] = "postgresql://changed.invalid/changed"
            os.environ["RECONCILIATION_REVIEW_TOKEN"] = "changed-after-authorization"
            os.environ["PHASE4_REVIEW_TOKEN_INPUT"] = "changed-after-authorization"

        with patch.dict(os.environ, SERVER_ENVIRONMENT, clear=True), patch.object(
            bridge,
            "require_review_authorization",
            side_effect=authorize_and_mutate_ambient_environment,
        ) as authorize, patch.object(
            bridge.subprocess, "Popen", return_value=process
        ) as popen, patch.object(
            bridge, "_supervise_g9_process", side_effect=lambda child: child.supervise()
        ):
            response = self.client.post("/internal/phase4-production-preflight")
        self.assertEqual(response.status_code, 200)
        authorize.assert_called_once_with(CONFIGURED_TOKEN, CONFIGURED_TOKEN)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.kwargs["env"], SERVER_ENVIRONMENT)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_child_argv_cwd_and_environment_are_exact(self):
        response, popen = self.post_with_process(FakeProcess())
        self.assertEqual(response.status_code, 200)
        args, kwargs = popen.call_args
        self.assertEqual(args, (list(bridge.G9_PREFLIGHT_ARGV),))
        self.assertEqual(
            args[0],
            [
                "/bin/sh",
                "./scripts/phase4-published-production-bootstrap.sh",
                "f308ac666a2377f540e528bc873463daecc20cf8",
                "0a8a2ea80721a97858c2120545d1e6b6f3805247",
                "--preflight-only",
            ],
        )
        self.assertEqual(kwargs["cwd"], bridge.REPOSITORY_ROOT)
        self.assertEqual(kwargs["env"], SERVER_ENVIRONMENT)
        self.assertEqual(
            bridge.REPOSITORY_ROOT,
            Path(bridge.__file__).resolve().parents[3],
        )

    def test_request_data_cannot_influence_argv(self):
        hostile = {
            "command": "rm -rf /",
            "sha": "f" * 40,
            "tree": "e" * 40,
            "mode": "execute",
            "DATABASE_URL": "postgresql://attacker.invalid/prod",
        }
        process = FakeProcess()
        response, popen = self.post_with_process(
            process,
            params={"sha": "0" * 40, "mode": "normal"},
            headers={"X-Command": "execute", "X-Database-URL": hostile["DATABASE_URL"]},
            json=hostile,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(popen.call_args.args[0], list(bridge.G9_PREFLIGHT_ARGV))
        command_text = " ".join(popen.call_args.args[0])
        for value in hostile.values():
            self.assertNotIn(value, command_text)

    def test_request_and_ambient_extras_cannot_influence_child_environment(self):
        environment = {
            **SERVER_ENVIRONMENT,
            "PATH": "/hostile/bin",
            "PGHOST": "production-redirect.invalid",
            "GIT_CONFIG_GLOBAL": "/hostile/gitconfig",
            "PYTHONHOME": "/hostile/python",
            "LD_PRELOAD": "/hostile/library.so",
            "SHOPIFY_CLIENT_SECRET": "must-not-reach-child",
            "CALLER_ENV": "must-not-reach-child",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            bridge.subprocess, "Popen", return_value=FakeProcess()
        ) as popen, patch.object(
            bridge, "_supervise_g9_process", side_effect=lambda child: child.supervise()
        ):
            response = self.client.post(
                "/internal/phase4-production-preflight",
                json={"REPLIT_DEPLOYMENT": "0", "CALLER_ENV": "injected"},
                headers={"X-Environment": "injected"},
            )
        self.assertEqual(response.status_code, 200)
        child_environment = popen.call_args.kwargs["env"]
        self.assertEqual(child_environment, SERVER_ENVIRONMENT)
        self.assertIsNot(child_environment, os.environ)
        self.assertEqual(child_environment["DATABASE_URL"], DATABASE_URL)
        self.assertEqual(child_environment["PYTHONPATH"], SERVER_ENVIRONMENT["PYTHONPATH"])
        self.assertEqual(
            child_environment["REPLIT_PYTHONPATH"],
            SERVER_ENVIRONMENT["REPLIT_PYTHONPATH"],
        )

        for required_name in ("PYTHONPATH", "REPLIT_PYTHONPATH"):
            with self.subTest(missing_runtime_name=required_name):
                incomplete = dict(SERVER_ENVIRONMENT)
                incomplete.pop(required_name)
                with patch.dict(os.environ, incomplete, clear=True), patch.object(
                    bridge.subprocess, "Popen"
                ) as blocked_popen:
                    blocked = self.client.post(
                        "/internal/phase4-production-preflight"
                    )
                self.assertEqual(blocked.status_code, 503)
                self.assertEqual(
                    blocked.json()["detail"]["error"],
                    "G9_RUNTIME_ENVIRONMENT_UNAVAILABLE",
                )
                blocked_popen.assert_not_called()

    def test_child_is_never_shell_interpreted(self):
        response, popen = self.post_with_process(FakeProcess())
        self.assertEqual(response.status_code, 200)
        args, kwargs = popen.call_args
        self.assertIsInstance(args[0], list)
        self.assertFalse(kwargs["shell"])
        self.assertTrue(kwargs["start_new_session"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.PIPE)


class ResultContractTests(BridgeTestCase):
    def test_success_requires_exact_g9_contract(self):
        mutations = (
            ("result", "PHASE4_PUBLISHED_PRODUCTION_RECONCILED"),
            ("mode", "NORMAL"),
            ("state", "B_ORIGINAL_MANIFEST_PERSISTED_PRE_007"),
            ("mutation_state_machine_entered", True),
            ("mutation_state_machine_entered", 0),
            ("execution_git_sha", "f" * 40),
            ("execution_tree_sha", "e" * 40),
            ("production_actions", {"ddl": 0, "dml": 1, "rebuild": 0, "readiness_writes": 0}),
            ("production_actions", {**bridge._EXPECTED_PRODUCTION_ACTIONS, "extra": 0}),
            ("production_actions", {"ddl": False, "dml": 0, "rebuild": 0, "readiness_writes": 0}),
            ("production_actions", {"ddl": 0.0, "dml": 0, "rebuild": 0, "readiness_writes": 0}),
            ("production_actions", {"ddl": "0", "dml": 0, "rebuild": 0, "readiness_writes": 0}),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                report = success_report()
                report[field] = value
                with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                    bridge._parse_success_report(report_bytes(report), SERVER_ENVIRONMENT)
                self.assertEqual(caught.exception.status_code, 502)
                self.assertEqual(caught.exception.code, "G9_PREFLIGHT_CONTRACT_MISMATCH")

    def test_malformed_nonobject_and_oversized_output_fail_closed(self):
        for raw in (b"", b"not-json", b"[]", b'"scalar"', b"{bad", b'{"value": NaN}'):
            with self.subTest(raw=raw):
                with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                    bridge._parse_success_report(raw, SERVER_ENVIRONMENT)
                self.assertEqual(caught.exception.status_code, 502)
        oversized = b"x" * (bridge.MAX_CHILD_STREAM_BYTES + 1)
        response, _ = self.post_with_process(FakeProcess(stdout=oversized))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["error"], "G9_PREFLIGHT_OUTPUT_LIMIT")

    def test_nonzero_g9_exit_fails_closed_without_retry(self):
        process = FakeProcess(stdout=b"unsafe output", stderr=b"controlled failure", returncode=2)
        response, popen = self.post_with_process(process)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["error"], "G9_PREFLIGHT_FAILED")
        popen.assert_called_once()
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_stderr_is_redacted_flattened_and_bounded(self):
        secret_text = (
            f"line one\n{DATABASE_URL}\r\n{CONFIGURED_TOKEN} "
            f"postgres://other:secret@elsewhere.invalid/prod "
            + "z" * 4_000
        ).encode()
        process = FakeProcess(stderr=secret_text, returncode=2)
        with redirect_stdout(io.StringIO()) as captured_out, redirect_stderr(
            io.StringIO()
        ) as captured_err:
            response, _ = self.post_with_process(process)
        response_text = response.text
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(DATABASE_URL, response_text)
        self.assertNotIn(CONFIGURED_TOKEN, response_text)
        self.assertNotIn("other:secret", response_text)
        diagnostic = response.json()["detail"]["diagnostic"]
        self.assertLessEqual(len(diagnostic), bridge.MAX_PUBLIC_DIAGNOSTIC_CHARACTERS)
        self.assertNotIn("\n", diagnostic)
        self.assertNotIn("\r", diagnostic)
        self.assertNotIn(DATABASE_URL, captured_out.getvalue() + captured_err.getvalue())
        self.assertNotIn(CONFIGURED_TOKEN, captured_out.getvalue() + captured_err.getvalue())

    def test_database_url_never_appears_in_success_or_failure_response(self):
        leaking = success_report(preflight_evidence={"database": DATABASE_URL})
        response, _ = self.post_with_process(FakeProcess(stdout=report_bytes(leaking)))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(DATABASE_URL, response.text)

        escaped_database_url = 'postgresql://user:p"ass\\word@db.invalid/neondb'
        escaped_environment = {
            **SERVER_ENVIRONMENT,
            "DATABASE_URL": escaped_database_url,
        }
        with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
            bridge._parse_success_report(
                report_bytes(
                    success_report(preflight_evidence={"database": escaped_database_url})
                ),
                escaped_environment,
            )
        self.assertEqual(caught.exception.code, "G9_PREFLIGHT_UNSAFE_OUTPUT")

        response, _ = self.post_with_process(
            FakeProcess(stderr=DATABASE_URL.encode(), returncode=2)
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(DATABASE_URL, response.text)

    def test_review_tokens_never_appear_in_success_or_failure_response(self):
        for token_name in (
            "RECONCILIATION_REVIEW_TOKEN",
            "PHASE4_REVIEW_TOKEN_INPUT",
        ):
            with self.subTest(token_name=token_name):
                token = SERVER_ENVIRONMENT[token_name]
                leaking = success_report(preflight_evidence={"leak": token})
                response, _ = self.post_with_process(
                    FakeProcess(stdout=report_bytes(leaking))
                )
                self.assertEqual(response.status_code, 502)
                self.assertNotIn(token, response.text)
                response, _ = self.post_with_process(
                    FakeProcess(stderr=f"failure {token}".encode(), returncode=2)
                )
                self.assertNotIn(token, response.text)

        for token, leaked_value in (
            ('quote"token', 'quote"token'),
            ("slash\\token", "slash\\token"),
            ("unicode-秘密", "unicode-秘密"),
            ("line\ntoken", "line\ntoken"),
            ("424242", 424242),
            ("true", True),
            ("null", None),
        ):
            with self.subTest(escaped_token=token):
                escaped_environment = {
                    **SERVER_ENVIRONMENT,
                    "RECONCILIATION_REVIEW_TOKEN": token,
                    "PHASE4_REVIEW_TOKEN_INPUT": token,
                }
                report = success_report(preflight_evidence={"leak": leaked_value})
                with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                    bridge._parse_success_report(
                        report_bytes(report), escaped_environment
                    )
                self.assertEqual(caught.exception.code, "G9_PREFLIGHT_UNSAFE_OUTPUT")


class RealSubprocessSupervisionTests(BridgeTestCase):
    _FAST_SUPERVISOR_LIMITS = {
        "PREFLIGHT_TIMEOUT_SECONDS": 2,
        "TERMINATION_GRACE_SECONDS": 0.05,
        "KILL_GRACE_SECONDS": 1,
    }

    @staticmethod
    def _assert_closed_pipes(
        test: unittest.TestCase, process: subprocess.Popen[bytes]
    ) -> None:
        test.assertIsNotNone(process.stdout)
        test.assertIsNotNone(process.stderr)
        test.assertTrue(process.stdout.closed)
        test.assertTrue(process.stderr.closed)

    def test_real_orphan_ignoring_term_with_closed_pipes_is_killed_and_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "descendant.pid"
            descendant = """
import os
from pathlib import Path
import signal
import sys
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')
while True:
    time.sleep(1)
"""
            leader = """
import os
from pathlib import Path
import subprocess
import sys
import time
subprocess.Popen(
    [sys.executable, '-I', '-S', '-c', sys.argv[1], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
deadline = time.monotonic() + 2
while not Path(sys.argv[2]).exists():
    if time.monotonic() >= deadline:
        raise RuntimeError('descendant did not become ready')
    time.sleep(0.01)
os.write(1, sys.argv[3].encode('utf-8'))
os._exit(0)
"""
            process = self.spawn_real_process(
                leader,
                descendant,
                str(pid_path),
                report_bytes().decode("utf-8"),
            )
            started = time.monotonic()
            with patch.multiple(bridge, **self._FAST_SUPERVISOR_LIMITS):
                with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                    bridge._supervise_g9_process(process)
            elapsed = time.monotonic() - started
            descendant_pid = int(_wait_for_file(pid_path))
            self.assertEqual(
                caught.exception.code, "G9_PREFLIGHT_PROCESS_GROUP_REMAINED"
            )
            self.assertLess(elapsed, 2)
            self.assertIsNotNone(process.returncode)
            _assert_process_group_absent(self, process.pid)
            _assert_pid_absent(self, descendant_pid)
            self._assert_closed_pipes(self, process)

    def test_real_stdout_limit_is_enforced_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            completed_path = Path(directory) / "completed"
            source = """
import os
from pathlib import Path
import sys
for _ in range(10000):
    os.write(1, b'x' * 1024)
Path(sys.argv[1]).write_text('unbounded write completed', encoding='utf-8')
"""
            process = self.spawn_real_process(source, str(completed_path))
            real_stream_type = bridge._BoundedChildStreams
            observed_streams = []

            class ObservedStreams(real_stream_type):
                def __init__(self, child):
                    super().__init__(child)
                    observed_streams.append(self)

            started = time.monotonic()
            with patch.multiple(
                bridge,
                **self._FAST_SUPERVISOR_LIMITS,
                MAX_CHILD_STREAM_BYTES=4_096,
                _BoundedChildStreams=ObservedStreams,
            ):
                with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                    bridge._supervise_g9_process(process)
            elapsed = time.monotonic() - started
            self.assertEqual(caught.exception.code, "G9_PREFLIGHT_OUTPUT_LIMIT")
            self.assertIn("stdout", caught.exception.diagnostic)
            self.assertLess(elapsed, 2)
            self.assertFalse(completed_path.exists())
            self.assertEqual(len(observed_streams), 1)
            stdout, stderr = observed_streams[0].output()
            self.assertLessEqual(len(stdout), 4_096)
            self.assertLessEqual(len(stderr), 4_096)
            _assert_process_group_absent(self, process.pid)
            self._assert_closed_pipes(self, process)

    def test_real_stderr_limit_is_enforced_during_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            completed_path = Path(directory) / "completed"
            source = """
import os
from pathlib import Path
import sys
for _ in range(10000):
    os.write(2, b'e' * 1024)
Path(sys.argv[1]).write_text('unbounded write completed', encoding='utf-8')
"""
            process = self.spawn_real_process(source, str(completed_path))
            real_stream_type = bridge._BoundedChildStreams
            observed_streams = []

            class ObservedStreams(real_stream_type):
                def __init__(self, child):
                    super().__init__(child)
                    observed_streams.append(self)

            started = time.monotonic()
            with patch.multiple(
                bridge,
                **self._FAST_SUPERVISOR_LIMITS,
                MAX_CHILD_STREAM_BYTES=4_096,
                _BoundedChildStreams=ObservedStreams,
            ):
                with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                    bridge._supervise_g9_process(process)
            elapsed = time.monotonic() - started
            self.assertEqual(caught.exception.code, "G9_PREFLIGHT_OUTPUT_LIMIT")
            self.assertIn("stderr", caught.exception.diagnostic)
            self.assertLess(elapsed, 2)
            self.assertFalse(completed_path.exists())
            self.assertEqual(len(observed_streams), 1)
            stdout, stderr = observed_streams[0].output()
            self.assertLessEqual(len(stdout), 4_096)
            self.assertLessEqual(len(stderr), 4_096)
            _assert_process_group_absent(self, process.pid)
            self._assert_closed_pipes(self, process)

    def test_real_simultaneous_stdout_and_stderr_are_drained_without_deadlock(self):
        source = """
import os
import threading
def emit(fd, value):
    for _ in range(128):
        os.write(fd, value * 128)
threads = [
    threading.Thread(target=emit, args=(1, b'o')),
    threading.Thread(target=emit, args=(2, b'e')),
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
"""
        process = self.spawn_real_process(source)
        with patch.multiple(
            bridge,
            **self._FAST_SUPERVISOR_LIMITS,
            MAX_CHILD_STREAM_BYTES=32_768,
        ):
            stdout, stderr = bridge._supervise_g9_process(process)
        self.assertEqual(stdout, b"o" * 16_384)
        self.assertEqual(stderr, b"e" * 16_384)
        _assert_process_group_absent(self, process.pid)
        self._assert_closed_pipes(self, process)

    def test_real_continued_emission_after_crossing_limit_is_stopped(self):
        source = """
import os
import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    os.write(1, b'x' * 1024)
"""
        process = self.spawn_real_process(source)
        started = time.monotonic()
        with patch.multiple(
            bridge,
            **self._FAST_SUPERVISOR_LIMITS,
            MAX_CHILD_STREAM_BYTES=1_024,
        ):
            with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                bridge._supervise_g9_process(process)
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.code, "G9_PREFLIGHT_OUTPUT_LIMIT")
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertGreaterEqual(elapsed, 0.04)
        self.assertLess(elapsed, 2)
        _assert_process_group_absent(self, process.pid)
        self._assert_closed_pipes(self, process)

    def test_real_timeout_with_surviving_descendant_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "descendant.pid"
            descendant = """
import os
from pathlib import Path
import signal
import sys
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')
while True:
    time.sleep(1)
"""
            leader = """
import subprocess
import sys
import time
subprocess.Popen(
    [sys.executable, '-I', '-S', '-c', sys.argv[1], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
while True:
    time.sleep(1)
"""
            process = self.spawn_real_process(leader, descendant, str(pid_path))
            descendant_pid = int(_wait_for_file(pid_path))
            started = time.monotonic()
            with patch.multiple(
                bridge,
                PREFLIGHT_TIMEOUT_SECONDS=0.1,
                TERMINATION_GRACE_SECONDS=0.05,
                KILL_GRACE_SECONDS=1,
            ):
                with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
                    bridge._supervise_g9_process(process)
            elapsed = time.monotonic() - started
            self.assertEqual(caught.exception.status_code, 504)
            self.assertEqual(caught.exception.code, "G9_PREFLIGHT_TIMEOUT")
            self.assertEqual(process.returncode, -signal.SIGTERM)
            self.assertGreaterEqual(elapsed, 0.14)
            self.assertLess(elapsed, 2)
            _assert_process_group_absent(self, process.pid)
            _assert_pid_absent(self, descendant_pid)
            self._assert_closed_pipes(self, process)
            self.assertFalse(bridge._PREFLIGHT_LOCK.locked())

    def test_real_normal_valid_completion_returns_exact_streams(self):
        expected_stdout = report_bytes()
        expected_stderr = b"bounded safe diagnostic"
        source = """
import os
import sys
os.write(1, bytes.fromhex(sys.argv[1]))
os.write(2, bytes.fromhex(sys.argv[2]))
"""
        process = self.spawn_real_process(
            source, expected_stdout.hex(), expected_stderr.hex()
        )
        with patch.multiple(bridge, **self._FAST_SUPERVISOR_LIMITS):
            stdout, stderr = bridge._supervise_g9_process(process)
        self.assertEqual(stdout, expected_stdout)
        self.assertEqual(stderr, expected_stderr)
        self.assertEqual(
            bridge._parse_success_report(stdout, SERVER_ENVIRONMENT),
            success_report(),
        )
        _assert_process_group_absent(self, process.pid)
        self._assert_closed_pipes(self, process)

    def test_verified_cleanup_allows_subsequent_request(self):
        emitting_source = """
import os
import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    os.write(1, b'x' * 1024)
"""
        valid_source = """
import os
import sys
os.write(1, bytes.fromhex(sys.argv[1]))
"""
        processes = []
        sources = [
            (emitting_source, ()),
            (valid_source, (report_bytes().hex(),)),
        ]

        def start_safe_test_process(command, **kwargs):
            self.assertEqual(command, list(bridge.G9_PREFLIGHT_ARGV))
            self.assertEqual(kwargs["cwd"], bridge.REPOSITORY_ROOT)
            self.assertEqual(kwargs["env"], SERVER_ENVIRONMENT)
            source, arguments = sources.pop(0)
            process = self.spawn_real_process(source, *arguments)
            processes.append(process)
            return process

        with patch.dict(os.environ, SERVER_ENVIRONMENT, clear=True), patch.object(
            bridge.subprocess, "Popen", side_effect=start_safe_test_process
        ) as popen, patch.multiple(
            bridge,
            **self._FAST_SUPERVISOR_LIMITS,
            MAX_CHILD_STREAM_BYTES=1_024,
        ):
            first = self.client.post("/internal/phase4-production-preflight")
            second = self.client.post("/internal/phase4-production-preflight")
        self.assertEqual(first.status_code, 502)
        self.assertEqual(first.json()["detail"]["error"], "G9_PREFLIGHT_OUTPUT_LIMIT")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(len(processes), 2)
        for process in processes:
            _assert_process_group_absent(self, process.pid)
            self._assert_closed_pipes(self, process)
        self.assertFalse(bridge._PREFLIGHT_CAPACITY_QUARANTINED.is_set())

    def test_timeout_cleanup_allows_subsequent_request_without_retry(self):
        timeout_source = """
import signal
import time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
"""
        valid_source = """
import os
import sys
os.write(1, bytes.fromhex(sys.argv[1]))
"""
        processes = []
        sources = [
            (timeout_source, ()),
            (valid_source, (report_bytes().hex(),)),
        ]

        def start_safe_test_process(command, **kwargs):
            self.assertEqual(command, list(bridge.G9_PREFLIGHT_ARGV))
            self.assertEqual(kwargs["env"], SERVER_ENVIRONMENT)
            source, arguments = sources.pop(0)
            process = self.spawn_real_process(source, *arguments)
            processes.append(process)
            return process

        with patch.dict(os.environ, SERVER_ENVIRONMENT, clear=True), patch.object(
            bridge.subprocess, "Popen", side_effect=start_safe_test_process
        ) as popen, patch.multiple(
            bridge,
            PREFLIGHT_TIMEOUT_SECONDS=0.1,
            TERMINATION_GRACE_SECONDS=0.05,
            KILL_GRACE_SECONDS=1,
        ):
            first = self.client.post("/internal/phase4-production-preflight")
            second = self.client.post("/internal/phase4-production-preflight")
        self.assertEqual(first.status_code, 504)
        self.assertEqual(first.json()["detail"]["error"], "G9_PREFLIGHT_TIMEOUT")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(len(processes), 2)
        self.assertEqual(processes[0].returncode, -signal.SIGKILL)
        for process in processes:
            _assert_process_group_absent(self, process.pid)
            self._assert_closed_pipes(self, process)
        self.assertFalse(bridge._PREFLIGHT_CAPACITY_QUARANTINED.is_set())

    def test_uncertain_cleanup_quarantines_and_blocks_subsequent_launch(self):
        source = """
import os
import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    os.write(1, b'x' * 1024)
"""
        processes = []
        real_terminate = bridge._terminate_process_group

        def start_safe_test_process(_command, **_kwargs):
            process = self.spawn_real_process(source)
            processes.append(process)
            return process

        def terminate_then_report_uncertainty(*args, **kwargs):
            real_terminate(*args, **kwargs)
            raise RuntimeError("test unexpected cleanup exception")

        try:
            with patch.dict(os.environ, SERVER_ENVIRONMENT, clear=True), patch.object(
                bridge.subprocess, "Popen", side_effect=start_safe_test_process
            ) as popen, patch.object(
                bridge,
                "_terminate_process_group",
                side_effect=terminate_then_report_uncertainty,
            ), patch.multiple(
                bridge,
                **self._FAST_SUPERVISOR_LIMITS,
                MAX_CHILD_STREAM_BYTES=1_024,
            ):
                first = self.client.post("/internal/phase4-production-preflight")
                second = self.client.post("/internal/phase4-production-preflight")
            self.assertEqual(first.status_code, 503)
            self.assertEqual(
                first.json()["detail"]["error"],
                "G9_PREFLIGHT_CLEANUP_UNPROVEN",
            )
            self.assertEqual(second.status_code, 503)
            self.assertEqual(
                second.json()["detail"]["error"],
                "G9_PREFLIGHT_CLEANUP_UNPROVEN",
            )
            popen.assert_called_once()
            self.assertTrue(bridge._PREFLIGHT_CAPACITY_QUARANTINED.is_set())
            self.assertEqual(len(processes), 1)
            _assert_process_group_absent(self, processes[0].pid)
            self._assert_closed_pipes(self, processes[0])
        finally:
            bridge._PREFLIGHT_CAPACITY_QUARANTINED.clear()

    def test_stream_close_exception_quarantines_and_blocks_subsequent_launch(self):
        valid_source = """
import os
import sys
os.write(1, bytes.fromhex(sys.argv[1]))
"""
        processes = []
        real_stream_type = bridge._BoundedChildStreams

        class CloseErrorStreams(real_stream_type):
            def close(self):
                super().close()
                raise RuntimeError("test unexpected close exception")

        def start_safe_test_process(_command, **_kwargs):
            process = self.spawn_real_process(valid_source, report_bytes().hex())
            processes.append(process)
            return process

        try:
            with patch.dict(os.environ, SERVER_ENVIRONMENT, clear=True), patch.object(
                bridge.subprocess, "Popen", side_effect=start_safe_test_process
            ) as popen, patch.object(
                bridge, "_BoundedChildStreams", CloseErrorStreams
            ), patch.multiple(
                bridge,
                **self._FAST_SUPERVISOR_LIMITS,
            ):
                first = self.client.post("/internal/phase4-production-preflight")
                second = self.client.post("/internal/phase4-production-preflight")
            self.assertEqual(first.status_code, 503)
            self.assertEqual(
                first.json()["detail"]["error"],
                "G9_PREFLIGHT_CLEANUP_UNPROVEN",
            )
            self.assertEqual(second.status_code, 503)
            popen.assert_called_once()
            self.assertTrue(bridge._PREFLIGHT_CAPACITY_QUARANTINED.is_set())
            self.assertEqual(len(processes), 1)
            _assert_process_group_absent(self, processes[0].pid)
            self._assert_closed_pipes(self, processes[0])
        finally:
            bridge._PREFLIGHT_CAPACITY_QUARANTINED.clear()


class ConcurrencyTests(BridgeTestCase):
    def test_overlapping_request_is_rejected_without_second_child(self):
        entered = threading.Event()
        release = threading.Event()
        process = BlockingProcess(entered, release)
        with patch.dict(os.environ, SERVER_ENVIRONMENT, clear=True), patch.object(
            bridge.subprocess, "Popen", return_value=process
        ) as popen, patch.object(
            bridge, "_supervise_g9_process", side_effect=lambda child: child.supervise()
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    TestClient(api.app).post,
                    "/internal/phase4-production-preflight",
                )
                self.assertTrue(entered.wait(2))
                second = TestClient(api.app).post(
                    "/internal/phase4-production-preflight"
                )
                self.assertEqual(second.status_code, 409)
                self.assertEqual(second.headers["cache-control"], "no-store")
                release.set()
                first = first_future.result(timeout=5)
        self.assertEqual(first.status_code, 200)
        popen.assert_called_once()


class IsolationTests(BridgeTestCase):
    @staticmethod
    def bridge_syntax_names() -> tuple[set[str], set[str]]:
        tree = ast.parse(Path(bridge.__file__).read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names.update(
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        )
        return imports, names

    def test_endpoint_itself_never_opens_a_database_connection(self):
        import psycopg

        with patch.object(api, "_db_conn", side_effect=AssertionError("DB opened")), patch.object(
            psycopg, "connect", side_effect=AssertionError("DB opened")
        ):
            response, _ = self.post_with_process(FakeProcess())
        self.assertEqual(response.status_code, 200)

        imports, names = self.bridge_syntax_names()
        self.assertNotIn("psycopg", imports)
        for forbidden in ("_db_conn", "connect_database", "connect", "execute", "cursor"):
            self.assertNotIn(forbidden, names)
        source = Path(bridge.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".communicate(", source)
        self.assertNotIn(".wait(", source)

    def test_endpoint_has_no_shopify_client_or_call_path(self):
        from procurement_os.shopify import graphql

        with patch.object(
            graphql.ShopifyGraphQLClient,
            "query",
            side_effect=AssertionError("Shopify called"),
        ):
            response, _ = self.post_with_process(FakeProcess())
        self.assertEqual(response.status_code, 200)
        imports, names = self.bridge_syntax_names()
        self.assertFalse(any("shopify" in name.lower() for name in imports))
        self.assertFalse(any("shopify" in name.lower() for name in names))

    def test_endpoint_has_no_migration_finalizer_or_po_mutation_path(self):
        from procurement_os import historical_sales_manifest, historical_sales_terminal, sales

        with patch.object(
            historical_sales_manifest,
            "persist_manifest_decisions",
            side_effect=AssertionError("manifest mutation called"),
        ), patch.object(
            historical_sales_terminal,
            "persist_terminal_disposition",
            side_effect=AssertionError("terminal mutation called"),
        ), patch.object(
            sales,
            "rerun_sales_identity_resolution",
            side_effect=AssertionError("finalizer called"),
        ), patch.object(api, "rollover", side_effect=AssertionError("pricing mutation called")):
            response, _ = self.post_with_process(FakeProcess())
        self.assertEqual(response.status_code, 200)
        _imports, names = self.bridge_syntax_names()
        for forbidden in (
            "apply_migration_007_stage",
            "persist_manifest_decisions",
            "persist_terminal_disposition",
            "rerun_sales_identity_resolution",
            "po_readiness",
            "rollover",
        ):
            self.assertNotIn(forbidden, names)


class ExistingApplicationRegressionTests(BridgeTestCase):
    def test_existing_routes_health_and_navigation_are_unchanged(self):
        expected_existing_routes = {
            ("GET", "/health"),
            ("GET", "/health/full"),
            ("GET", "/admin/status"),
            ("GET", "/"),
            ("GET", "/data-sync-runs"),
            ("GET", "/rules"),
            ("GET", "/foundation/status"),
            ("POST", "/economics/target-cost"),
            ("POST", "/economics/qualifying-quantity"),
            ("POST", "/matching/score"),
            ("GET", "/reconciliation/items"),
            ("POST", "/reconciliation/approve-recreation"),
            ("POST", "/reconciliation/reject-recreation"),
            ("POST", "/reconciliation/retire"),
            ("POST", "/reconciliation/recompute-gate"),
            ("GET", "/reconciliation"),
            ("GET", "/reconciliation/investigation/items"),
            ("GET", "/reconciliation/investigation"),
            ("POST", "/reconciliation/investigation/retire-batch"),
            ("POST", "/reconciliation/decide"),
            ("GET", "/historical-sales/review/items"),
            ("GET", "/historical-sales/review/catalog-search"),
            ("GET", "/historical-sales/review"),
            ("POST", "/historical-sales/review/decide"),
            ("POST", "/pricing/rollover"),
        }
        observed = {
            (method, route.path)
            for route in api.app.routes
            if isinstance(route, APIRoute)
            for method in (route.methods or set())
            if route.path != "/internal/phase4-production-preflight"
        }
        self.assertEqual(observed, expected_existing_routes)
        self.assertEqual(
            self.client.get("/health").json(),
            {"ok": True, "service": "buffalo-procurement-os", "version": "1.3.0"},
        )
        with patch.object(api, "full_health", return_value={"existing": "unchanged"}):
            self.assertEqual(
                self.client.get("/health/full").json(), {"existing": "unchanged"}
            )
        self.assertEqual(
            api._OPERATIONAL_NAV_ITEMS,
            (
                ("System Readiness", "admin/status"),
                ("Catalog Reconciliation", "reconciliation"),
                ("Historical Sales Reconciliation", "historical-sales/review"),
                ("Data/Sync Runs", "data-sync-runs"),
            ),
        )


class RealBootstrapHandoffTests(BridgeTestCase):
    def test_real_g9_bootstrap_handoff_fails_before_clone_without_deployment(self):
        environment = {
            "RECONCILIATION_REVIEW_TOKEN": CONFIGURED_TOKEN,
            "PHASE4_REVIEW_TOKEN_INPUT": CONFIGURED_TOKEN,
        }
        bootstrap = bridge.REPOSITORY_ROOT / bridge.G9_BOOTSTRAP_ARGUMENT.removeprefix("./")
        self.assertTrue(bootstrap.is_file())
        with self.assertRaises(bridge.Phase4PreflightBridgeError) as caught:
            bridge._launch_g9_preflight(environment)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(caught.exception.code, "G9_PREFLIGHT_FAILED")
        self.assertNotIn(CONFIGURED_TOKEN, json.dumps(caught.exception.public_detail()))
        self.assertNotIn("clone", (caught.exception.diagnostic or "").lower())


if __name__ == "__main__":
    unittest.main()
