#!/usr/bin/env python3
"""Supervise the Replit production Node/FastAPI pair with fail-closed startup."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
FASTAPI_HEALTH_URL = "http://127.0.0.1:8000/health"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 180.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5

FASTAPI_COMMAND = (
    sys.executable,
    "-m",
    "uvicorn",
    "procurement_os.api:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
)
NODE_COMMAND = (
    "node",
    "--enable-source-maps",
    "artifacts/api-server/dist/index.mjs",
)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _fastapi_environment() -> dict[str, str]:
    env = os.environ.copy()
    src = str(REPO_ROOT / "procurement" / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing else os.pathsep.join((src, existing))
    return env


def start_fastapi() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        FASTAPI_COMMAND,
        cwd=REPO_ROOT,
        env=_fastapi_environment(),
    )


def start_node() -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.setdefault("PORT", "8080")
    env.setdefault("NODE_ENV", "production")
    return subprocess.Popen(NODE_COMMAND, cwd=REPO_ROOT, env=env)


def fastapi_healthcheck() -> bool:
    try:
        with urlopen(FASTAPI_HEALTH_URL, timeout=2.0) as response:
            return 200 <= response.status < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def wait_for_fastapi(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    healthcheck: Callable[[], bool] = fastapi_healthcheck,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout_seconds
    while True:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                f"FastAPI exited before readiness with code {returncode}"
            )
        if healthcheck():
            return
        if monotonic() >= deadline:
            raise RuntimeError(
                f"FastAPI did not become healthy within {timeout_seconds:g} seconds"
            )
        sleep(poll_interval_seconds)


def wait_for_services(
    fastapi: subprocess.Popen[bytes],
    node: subprocess.Popen[bytes],
    *,
    poll_interval_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    while True:
        fastapi_returncode = fastapi.poll()
        if fastapi_returncode is not None:
            raise RuntimeError(
                f"FastAPI exited after readiness with code {fastapi_returncode}"
            )
        node_returncode = node.poll()
        if node_returncode is not None:
            return node_returncode
        sleep(poll_interval_seconds)


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    fastapi: subprocess.Popen[bytes] | None = None
    node: subprocess.Popen[bytes] | None = None

    def relay_signal(signum: int, _frame: object) -> None:
        for child in (node, fastapi):
            if child is not None and child.poll() is None:
                child.send_signal(signum)

    old_handlers = {
        signum: signal.signal(signum, relay_signal)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }

    try:
        timeout_seconds = _float_env(
            "PROCUREMENT_STARTUP_TIMEOUT_SECONDS",
            DEFAULT_STARTUP_TIMEOUT_SECONDS,
        )
        poll_interval_seconds = _float_env(
            "PROCUREMENT_STARTUP_POLL_SECONDS",
            DEFAULT_POLL_INTERVAL_SECONDS,
        )

        print("Starting FastAPI procurement backend", flush=True)
        fastapi = start_fastapi()
        wait_for_fastapi(
            fastapi,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        print("FastAPI procurement backend is healthy; starting Node API service", flush=True)
        node = start_node()
        return wait_for_services(
            fastapi,
            node,
            poll_interval_seconds=poll_interval_seconds,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: Replit production startup failed closed: {exc}", file=sys.stderr)
        return 1
    finally:
        stop_process(node)
        stop_process(fastapi)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
