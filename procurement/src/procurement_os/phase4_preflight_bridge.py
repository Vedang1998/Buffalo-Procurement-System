"""Temporary, read-only transport bridge to the reviewed Phase 4 G9 preflight.

This module deliberately has no database, Shopify, readiness, migration,
finalizer, or purchase-order execution path.  Its only operational action is
launching the frozen G9 bootstrap in a child process after server-side
authorization.  Remove the bridge after published-production Phase 4 closeout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import threading
import time
from typing import Any, Mapping

from .historical_sales_manifest import require_review_authorization


G9_EXECUTION_SHA = "f308ac666a2377f540e528bc873463daecc20cf8"
G9_EXECUTION_TREE = "0a8a2ea80721a97858c2120545d1e6b6f3805247"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
G9_BOOTSTRAP_ARGUMENT = "./scripts/phase4-published-production-bootstrap.sh"
G9_PREFLIGHT_ARGV = (
    "/bin/sh",
    G9_BOOTSTRAP_ARGUMENT,
    G9_EXECUTION_SHA,
    G9_EXECUTION_TREE,
    "--preflight-only",
)

PREFLIGHT_TIMEOUT_SECONDS = 180
TERMINATION_GRACE_SECONDS = 2
KILL_GRACE_SECONDS = 2
MAX_CHILD_STREAM_BYTES = 1_048_576
MAX_PUBLIC_DIAGNOSTIC_CHARACTERS = 1_000
_STREAM_READ_CHUNK_BYTES = 65_536
_SUPERVISOR_POLL_SECONDS = 0.02

_CHILD_ENVIRONMENT_NAMES = (
    "REPLIT_DEPLOYMENT",
    "DATABASE_URL",
    "RECONCILIATION_REVIEW_TOKEN",
    "PHASE4_REVIEW_TOKEN_INPUT",
    # The approved Nix executable relies on Replit's immutable sitecustomize
    # plus the locked deployment environment for installed project packages.
    # These are copied unchanged; PATH and all other Python variables remain
    # unavailable to the child.
    "PYTHONPATH",
    "REPLIT_PYTHONPATH",
)
_EXPECTED_PRODUCTION_ACTIONS = {
    "ddl": 0,
    "dml": 0,
    "rebuild": 0,
    "readiness_writes": 0,
}
_POSTGRESQL_URI = re.compile(r"(?i)postgres(?:ql)?://[^\s\"']+")
_PREFLIGHT_LOCK = threading.Lock()
_PREFLIGHT_CAPACITY_QUARANTINED = threading.Event()


class Phase4PreflightBridgeError(RuntimeError):
    """A fail-closed bridge error containing only API-safe information."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.diagnostic = diagnostic

    def public_detail(self) -> dict[str, str]:
        detail = {"error": self.code, "message": self.message}
        if self.diagnostic:
            detail["diagnostic"] = self.diagnostic
        return detail


class _ProcessGroupCleanupUncertain(RuntimeError):
    """The child/group/resource cleanup contract could not be proven."""


class _BoundedChildStreams:
    """Drain both child pipes fairly while retaining at most the fixed caps."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None or process.stderr is None:
            raise ValueError("G9 child stdout and stderr must both be pipes")
        self._selector = selectors.DefaultSelector()
        self._pipes = {
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
        self._buffers = {
            "stdout": bytearray(),
            "stderr": bytearray(),
        }
        try:
            for name, pipe in self._pipes.items():
                os.set_blocking(pipe.fileno(), False)
                self._selector.register(pipe, selectors.EVENT_READ, name)
        except Exception:
            self.close()
            raise

    @property
    def at_eof(self) -> bool:
        return not self._selector.get_map()

    def output(self) -> tuple[bytes, bytes]:
        return bytes(self._buffers["stdout"]), bytes(self._buffers["stderr"])

    def pump(self, timeout: float, *, retain: bool) -> str | None:
        """Read at most one fixed chunk per ready stream.

        Returns the first stream whose next byte crossed its independent cap.
        During cleanup, callers set ``retain=False`` to drain without growing
        either buffer.
        """

        if not self._selector.get_map():
            if timeout > 0:
                time.sleep(timeout)
            return None

        exceeded: str | None = None
        for key, _events in self._selector.select(timeout):
            name = str(key.data)
            if retain:
                room = MAX_CHILD_STREAM_BYTES - len(self._buffers[name])
                read_size = min(_STREAM_READ_CHUNK_BYTES, room + 1)
            else:
                room = 0
                read_size = _STREAM_READ_CHUNK_BYTES
            try:
                chunk = os.read(key.fd, read_size)
            except BlockingIOError:
                continue
            if not chunk:
                self._selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            if retain:
                self._buffers[name].extend(chunk[:room])
                if len(chunk) > room and exceeded is None:
                    exceeded = name
        return exceeded

    def close(self) -> bool:
        """Close the selector and every owned child pipe; report exact success."""

        closed_cleanly = True
        for key in list(self._selector.get_map().values()):
            try:
                self._selector.unregister(key.fileobj)
            except Exception:
                closed_cleanly = False
            try:
                key.fileobj.close()
            except Exception:
                closed_cleanly = False
        for pipe in self._pipes.values():
            if not pipe.closed:
                try:
                    pipe.close()
                except Exception:
                    closed_cleanly = False
        try:
            self._selector.close()
        except Exception:
            closed_cleanly = False
        return closed_cleanly


def _server_child_environment() -> dict[str, str]:
    """Copy only unchanged values that the reviewed bootstrap consumes."""

    return {
        name: os.environ[name]
        for name in _CHILD_ENVIRONMENT_NAMES
        if name in os.environ
    }


def _authorize_server_environment(environment: Mapping[str, str]) -> None:
    configured = environment.get("RECONCILIATION_REVIEW_TOKEN")
    supplied = environment.get("PHASE4_REVIEW_TOKEN_INPUT")
    if not configured:
        raise Phase4PreflightBridgeError(
            status_code=503,
            code="PHASE4_REVIEW_AUTHORIZATION_NOT_CONFIGURED",
            message="Phase 4 production preflight authorization is unavailable.",
        )
    if not supplied:
        raise Phase4PreflightBridgeError(
            status_code=503,
            code="PHASE4_REVIEW_AUTHORIZATION_INPUT_UNAVAILABLE",
            message="Phase 4 production preflight authorization is unavailable.",
        )
    try:
        require_review_authorization(configured, supplied)
    except PermissionError:
        raise Phase4PreflightBridgeError(
            status_code=403,
            code="PHASE4_REVIEW_AUTHORIZATION_FAILED",
            message="Phase 4 production preflight authorization failed.",
        ) from None
    if not environment.get("PYTHONPATH") or not environment.get("REPLIT_PYTHONPATH"):
        raise Phase4PreflightBridgeError(
            status_code=503,
            code="G9_RUNTIME_ENVIRONMENT_UNAVAILABLE",
            message="The reviewed Phase 4 preflight runtime is unavailable.",
        )


def _redact_child_text(raw: bytes, environment: Mapping[str, str]) -> str:
    text = raw.decode("utf-8", errors="replace")
    for name in (
        "DATABASE_URL",
        "RECONCILIATION_REVIEW_TOKEN",
        "PHASE4_REVIEW_TOKEN_INPUT",
    ):
        value = environment.get(name)
        if value:
            encoded_values = {
                value,
                json.dumps(value)[1:-1],
                json.dumps(value, ensure_ascii=False)[1:-1],
                value.encode("unicode_escape").decode("ascii"),
            }
            for encoded in sorted(encoded_values, key=len, reverse=True):
                if encoded:
                    text = text.replace(encoded, "[REDACTED]")
    text = _POSTGRESQL_URI.sub("postgresql://[REDACTED]", text)
    text = " ".join(text.split())
    return text[:MAX_PUBLIC_DIAGNOSTIC_CHARACTERS]


def _safe_failure_diagnostic(
    stdout: bytes, stderr: bytes, environment: Mapping[str, str]
) -> str | None:
    raw = stderr if stderr.strip() else stdout
    if not raw:
        return None
    diagnostic = _redact_child_text(raw, environment)
    return diagnostic or None


def _process_group_exists(process_group_id: int) -> bool:
    """Return False only when the kernel proves that the process group is gone."""

    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    return True


def _signal_process_group(process_group_id: int, sig: signal.Signals) -> bool:
    """Signal the exact launched process group; False means it was already gone."""

    try:
        os.killpg(process_group_id, sig)
    except ProcessLookupError:
        return False
    return True


def _cleanup_is_proven(process: subprocess.Popen[bytes], process_group_id: int) -> bool:
    """Require both direct-child reaping and kernel-proven group absence."""

    direct_child_reaped = process.poll() is not None
    return direct_child_reaped and not _process_group_exists(process_group_id)


def _wait_for_cleanup_proof(
    process: subprocess.Popen[bytes],
    process_group_id: int,
    streams: _BoundedChildStreams | None,
    *,
    deadline: float,
) -> bool:
    """Drain/discard and poll until cleanup is proven or the deadline expires."""

    while True:
        try:
            if _cleanup_is_proven(process, process_group_id):
                return True
        except OSError:
            # A later successful probe may still prove cleanup before deadline.
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        wait_for = min(_SUPERVISOR_POLL_SECONDS, remaining)
        if streams is None:
            time.sleep(wait_for)
        else:
            try:
                streams.pump(wait_for, retain=False)
            except OSError:
                # Closing the owned descriptors is the final resource proof.
                time.sleep(wait_for)


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    process_group_id: int,
    streams: _BoundedChildStreams | None,
) -> None:
    """Boundedly terminate, reap, and prove absence of the launched group."""

    try:
        _signal_process_group(process_group_id, signal.SIGTERM)
    except OSError:
        # Still attempt hard termination and proof before quarantining capacity.
        pass
    if _wait_for_cleanup_proof(
        process,
        process_group_id,
        streams,
        deadline=time.monotonic() + TERMINATION_GRACE_SECONDS,
    ):
        return

    try:
        _signal_process_group(process_group_id, signal.SIGKILL)
    except OSError:
        pass
    if _wait_for_cleanup_proof(
        process,
        process_group_id,
        streams,
        deadline=time.monotonic() + KILL_GRACE_SECONDS,
    ):
        return
    raise _ProcessGroupCleanupUncertain(
        "direct-child reaping or process-group absence could not be proven"
    )


def _supervise_g9_process(
    process: subprocess.Popen[bytes],
) -> tuple[bytes, bytes]:
    """Collect one G9 child with live stream caps and exact group completion."""

    process_group_id = process.pid
    streams: _BoundedChildStreams | None = None
    failure: Phase4PreflightBridgeError | None = None
    result: tuple[bytes, bytes] | None = None
    cleanup_required = False
    try:
        streams = _BoundedChildStreams(process)
        execution_deadline = time.monotonic() + PREFLIGHT_TIMEOUT_SECONDS
        while True:
            remaining = execution_deadline - time.monotonic()
            if remaining <= 0:
                cleanup_required = True
                failure = Phase4PreflightBridgeError(
                    status_code=504,
                    code="G9_PREFLIGHT_TIMEOUT",
                    message="The reviewed Phase 4 preflight exceeded its execution limit.",
                )
                break

            exceeded = streams.pump(
                min(_SUPERVISOR_POLL_SECONDS, remaining), retain=True
            )
            if exceeded is not None:
                cleanup_required = True
                failure = Phase4PreflightBridgeError(
                    status_code=502,
                    code="G9_PREFLIGHT_OUTPUT_LIMIT",
                    message="The reviewed Phase 4 preflight exceeded its output limit.",
                    diagnostic=f"{exceeded} exceeded its fixed byte limit.",
                )
                break

            if process.poll() is None:
                continue
            try:
                group_exists = _process_group_exists(process_group_id)
            except OSError:
                cleanup_required = True
                failure = Phase4PreflightBridgeError(
                    status_code=502,
                    code="G9_PREFLIGHT_SUPERVISION_FAILED",
                    message="The reviewed Phase 4 preflight could not be supervised safely.",
                )
                break
            if group_exists:
                cleanup_required = True
                failure = Phase4PreflightBridgeError(
                    status_code=502,
                    code="G9_PREFLIGHT_PROCESS_GROUP_REMAINED",
                    message="The reviewed Phase 4 preflight left a child process running.",
                )
                break
            if streams.at_eof:
                result = streams.output()
                break
    except Exception:
        cleanup_required = True
        failure = Phase4PreflightBridgeError(
            status_code=502,
            code="G9_PREFLIGHT_SUPERVISION_FAILED",
            message="The reviewed Phase 4 preflight could not be supervised safely.",
        )

    cleanup_uncertain: _ProcessGroupCleanupUncertain | None = None
    try:
        if cleanup_required:
            try:
                _terminate_process_group(process, process_group_id, streams)
            except Exception:
                cleanup_uncertain = _ProcessGroupCleanupUncertain(
                    "G9 process-group cleanup raised before proof completed"
                )
    finally:
        try:
            streams_closed = streams.close() if streams is not None else False
        except Exception:
            streams_closed = False
    if cleanup_uncertain is not None or not streams_closed:
        raise _ProcessGroupCleanupUncertain(
            "G9 process-group or pipe cleanup could not be proven"
        ) from None
    if failure is not None:
        raise failure
    if result is None:
        raise _ProcessGroupCleanupUncertain(
            "G9 completion did not produce a fully supervised result"
        )
    return result


def _launch_g9_preflight(environment: Mapping[str, str]) -> tuple[bytes, bytes]:
    try:
        process = subprocess.Popen(
            list(G9_PREFLIGHT_ARGV),
            cwd=REPOSITORY_ROOT,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except OSError as exc:
        diagnostic = _redact_child_text(str(exc).encode(), environment)
        raise Phase4PreflightBridgeError(
            status_code=502,
            code="G9_PREFLIGHT_START_FAILED",
            message="The reviewed Phase 4 preflight could not be started.",
            diagnostic=diagnostic or None,
        ) from None

    stdout, stderr = _supervise_g9_process(process)
    if process.returncode != 0:
        raise Phase4PreflightBridgeError(
            status_code=502,
            code="G9_PREFLIGHT_FAILED",
            message="The reviewed Phase 4 preflight failed closed.",
            diagnostic=_safe_failure_diagnostic(stdout, stderr, environment),
        )
    return stdout, stderr


def _parse_success_report(stdout: bytes, environment: Mapping[str, str]) -> dict[str, Any]:
    def reject_nonstandard_json_constant(value: str) -> None:
        raise ValueError(f"nonstandard JSON constant: {value}")

    try:
        report = json.loads(
            stdout.decode("utf-8", errors="strict"),
            parse_constant=reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, ValueError):
        raise Phase4PreflightBridgeError(
            status_code=502,
            code="G9_PREFLIGHT_INVALID_OUTPUT",
            message="The reviewed Phase 4 preflight returned invalid output.",
            diagnostic=_safe_failure_diagnostic(stdout, b"", environment),
        ) from None
    if not isinstance(report, dict):
        raise Phase4PreflightBridgeError(
            status_code=502,
            code="G9_PREFLIGHT_INVALID_OUTPUT",
            message="The reviewed Phase 4 preflight returned invalid output.",
        )

    production_actions = report.get("production_actions")
    exact_zero_actions = (
        isinstance(production_actions, dict)
        and set(production_actions) == set(_EXPECTED_PRODUCTION_ACTIONS)
        and all(
            type(production_actions[name]) is int and production_actions[name] == 0
            for name in _EXPECTED_PRODUCTION_ACTIONS
        )
    )
    valid_contract = (
        report.get("result") == "PHASE4_PUBLISHED_PRODUCTION_PREFLIGHT"
        and report.get("mode") == "READ_ONLY"
        and report.get("state") == "A_FROZEN_PRODUCTION_BASELINE"
        and report.get("mutation_state_machine_entered") is False
        and report.get("execution_git_sha") == G9_EXECUTION_SHA
        and report.get("execution_tree_sha") == G9_EXECUTION_TREE
        and exact_zero_actions
    )
    if not valid_contract:
        raise Phase4PreflightBridgeError(
            status_code=502,
            code="G9_PREFLIGHT_CONTRACT_MISMATCH",
            message="The reviewed Phase 4 preflight result failed contract validation.",
        )

    report_strings: list[str] = []
    pending: list[Any] = [report]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            report_strings.extend(str(key) for key in value)
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
        elif isinstance(value, str):
            report_strings.append(value)

    serialized = json.dumps(report, sort_keys=True, separators=(",", ":"))
    for name in (
        "DATABASE_URL",
        "RECONCILIATION_REVIEW_TOKEN",
        "PHASE4_REVIEW_TOKEN_INPUT",
    ):
        value = environment.get(name)
        if value and (
            any(value in item for item in report_strings) or value in serialized
        ):
            raise Phase4PreflightBridgeError(
                status_code=502,
                code="G9_PREFLIGHT_UNSAFE_OUTPUT",
                message="The reviewed Phase 4 preflight returned unsafe output.",
            )
    if any(_POSTGRESQL_URI.search(item) for item in report_strings) or _POSTGRESQL_URI.search(
        serialized
    ):
        raise Phase4PreflightBridgeError(
            status_code=502,
            code="G9_PREFLIGHT_UNSAFE_OUTPUT",
            message="The reviewed Phase 4 preflight returned unsafe output.",
        )
    return report


def run_phase4_production_preflight() -> dict[str, Any]:
    """Authorize and run one exact, frozen G9 read-only preflight subprocess."""

    child_environment = _server_child_environment()
    _authorize_server_environment(child_environment)
    if _PREFLIGHT_CAPACITY_QUARANTINED.is_set():
        raise Phase4PreflightBridgeError(
            status_code=503,
            code="G9_PREFLIGHT_CLEANUP_UNPROVEN",
            message="Phase 4 preflight capacity is blocked pending process restart.",
        )
    if not _PREFLIGHT_LOCK.acquire(blocking=False):
        raise Phase4PreflightBridgeError(
            status_code=409,
            code="G9_PREFLIGHT_BUSY",
            message="A Phase 4 production preflight is already running in this process.",
        )
    try:
        if _PREFLIGHT_CAPACITY_QUARANTINED.is_set():
            raise Phase4PreflightBridgeError(
                status_code=503,
                code="G9_PREFLIGHT_CLEANUP_UNPROVEN",
                message="Phase 4 preflight capacity is blocked pending process restart.",
            )
        try:
            stdout, _stderr = _launch_g9_preflight(child_environment)
            return _parse_success_report(stdout, child_environment)
        except _ProcessGroupCleanupUncertain:
            _PREFLIGHT_CAPACITY_QUARANTINED.set()
            raise Phase4PreflightBridgeError(
                status_code=503,
                code="G9_PREFLIGHT_CLEANUP_UNPROVEN",
                message="Phase 4 preflight cleanup could not be proven; capacity is blocked pending process restart.",
            ) from None
    finally:
        _PREFLIGHT_LOCK.release()
