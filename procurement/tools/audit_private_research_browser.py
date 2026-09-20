#!/usr/bin/env python3
"""Audit one sealed private-research workspace in real loopback Chromium.

The production workspace is built by a separate explicit gate.  This harness
rehashes and semantically replays that workspace through the canonical reader,
starts only the isolated private viewer, drives Chromium through dependency-free
CDP, restarts the viewer on the same origin, and retains no authentication
material.  It does not use Shopify, a database, or any operational service.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "procurement" / "src"
SCRIPT = Path(__file__).with_suffix(".mjs")
LAUNCHER = Path(__file__).with_name("serve_private_research.py")
CONTRACT = "BUFFALO_PRIVATE_RESEARCH_BROWSER_ACCEPTANCE_V1"
ARTIFACT_NAMES = (
    "owner-preview.html",
    "owner-worksheet.csv",
    "coverage.json",
    "projection.json",
)
# The launcher performs one canonical replay before it starts Uvicorn, then the
# app performs its own startup replay under the launcher's 600-second window.
SERVER_READY_SECONDS = 21 * 60
CDP_READY_SECONDS = 30

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from procurement_os.private_research import read_private_research_workspace
from procurement_os.private_research_projection import filter_private_research_rows


class PrivateResearchBrowserAuditError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _json_sha256(value: object) -> str:
    return _sha256(_canonical_json_bytes(value))


def _reject_symlinked_components(path: Path, *, label: str) -> None:
    if not path.is_absolute():
        raise PrivateResearchBrowserAuditError(f"{label} must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PrivateResearchBrowserAuditError(
                f"{label} component is unavailable"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise PrivateResearchBrowserAuditError(
                f"{label} must not have symlinked components"
            )


def _prepare_empty_root(path: Path, *, label: str) -> None:
    _reject_symlinked_components(path, label=label)
    if path.exists():
        try:
            info = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise PrivateResearchBrowserAuditError(f"{label} is unavailable") from exc
        if (
            path.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise PrivateResearchBrowserAuditError(
                f"{label} ownership, mode, or type differs"
            )
        if any(path.iterdir()):
            raise PrivateResearchBrowserAuditError(f"{label} must be empty")
        return
    try:
        path.mkdir(mode=0o700, parents=False)
    except OSError as exc:
        raise PrivateResearchBrowserAuditError(f"{label} could not be created") from exc
    os.chmod(path, 0o700)


def _require_disjoint(*paths: Path) -> None:
    resolved = [path.resolve(strict=False) for path in paths]
    for index, left in enumerate(resolved):
        for right in resolved[index + 1 :]:
            if (
                left == right
                or left.is_relative_to(right)
                or right.is_relative_to(left)
            ):
                raise PrivateResearchBrowserAuditError(
                    "workspace, runtime, and evidence roots must be disjoint"
                )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _assert_port_free(port: int, *, label: str) -> None:
    if type(port) is not int or port < 1024 or port > 65535:
        raise PrivateResearchBrowserAuditError(f"{label} port is outside the allowed range")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise PrivateResearchBrowserAuditError(f"{label} port is not free") from exc


def _stockout_status(row: Mapping[str, Any]) -> str:
    forecast = row.get("forecast")
    reasons = forecast.get("reason_codes", []) if isinstance(forecast, Mapping) else []
    if any(
        isinstance(reason, str)
        and (
            "FORECAST_EVIDENCE" in reason
            or "INVENTORY_STATE" in reason
            or "AVAILABILITY" in reason
        )
        for reason in reasons
    ):
        return "NOT_CAPTURED"
    if isinstance(forecast, Mapping) and forecast.get("status") == "CALCULATED_RESEARCH_ONLY":
        return "CAPTURED_IN_RESEARCH_INPUT"
    return "INCOMPLETE_OR_UNKNOWN"


def _filter_expectation(
    name: str,
    parameters: Mapping[str, str],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    selected = [dict(row) for row in rows]
    return {
        "name": name,
        "parameters": dict(parameters),
        "total": len(selected),
        "first_page_rows": selected[:50],
    }


def _build_expectations(workspace: Mapping[str, Any]) -> dict[str, Any]:
    manifest = workspace.get("manifest")
    projection = workspace.get("projection")
    artifacts = workspace.get("artifacts")
    if (
        not isinstance(manifest, dict)
        or not isinstance(projection, dict)
        or not isinstance(artifacts, dict)
    ):
        raise PrivateResearchBrowserAuditError("canonical workspace result is incomplete")
    rows = projection.get("research_rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise PrivateResearchBrowserAuditError(
            "browser filtering requires at least one research row"
        )
    vendor_values = {
        item
        for item in projection.get("vendor_names", [])
        if isinstance(item, str) and item
    }
    named_row = next(
        (
            row
            for row in rows
            if isinstance(row.get("supplier_name"), str)
            and row["supplier_name"] in vendor_values
        ),
        None,
    )
    if named_row is None:
        raise PrivateResearchBrowserAuditError(
            "browser vendor filtering requires one named supplier"
        )
    detail_row = dict(rows[0])
    query = str(detail_row.get("shopify_variant_id") or "")
    if not query:
        raise PrivateResearchBrowserAuditError("browser search fixture is absent")
    query_rows = filter_private_research_rows(projection, query=query)
    vendor = str(named_row["supplier_name"])
    vendor_rows = filter_private_research_rows(projection, vendor=vendor)
    raw_reasons = detail_row.get("missing_data_reasons")
    status = (
        str(raw_reasons[0])
        if isinstance(raw_reasons, list) and raw_reasons
        else str(detail_row.get("join_status") or "")
    )
    if not status:
        raise PrivateResearchBrowserAuditError("browser status fixture is absent")
    status_rows = filter_private_research_rows(projection, status=status)
    stockout_groups = {
        value: [dict(row) for row in rows if _stockout_status(row) == value]
        for value in (
            "NOT_CAPTURED",
            "CAPTURED_IN_RESEARCH_INPUT",
            "INCOMPLETE_OR_UNKNOWN",
        )
    }
    stockout = next((value for value, items in stockout_groups.items() if items), None)
    if stockout is None:
        raise PrivateResearchBrowserAuditError("browser stockout fixture is absent")

    records = manifest.get("artifacts")
    if not isinstance(records, list):
        raise PrivateResearchBrowserAuditError("workspace artifact manifest is absent")
    records_by_name = {
        record.get("name"): record for record in records if isinstance(record, dict)
    }
    artifact_expectations: list[dict[str, Any]] = []
    if set(artifacts) != set(ARTIFACT_NAMES) or set(records_by_name) != set(ARTIFACT_NAMES):
        raise PrivateResearchBrowserAuditError("workspace artifact set differs")
    for name in ARTIFACT_NAMES:
        data = artifacts.get(name)
        record = records_by_name[name]
        if not isinstance(data, bytes):
            raise PrivateResearchBrowserAuditError("workspace artifact bytes are absent")
        digest = _sha256(data)
        if record.get("sha256") != digest or record.get("bytes") != len(data):
            raise PrivateResearchBrowserAuditError("workspace artifact record differs")
        artifact_expectations.append(
            {
                "name": name,
                "bytes": len(data),
                "sha256": digest,
                "media_type": record.get("media_type"),
            }
        )

    count_fields = (
        "coverage_rows",
        "research_rows",
        "owner_worksheet",
        "unjoined_supplier_hypotheses",
        "vendor_names",
    )
    counts = {
        field: len(projection.get(field, []))
        for field in count_fields
        if isinstance(projection.get(field), list)
    }
    if set(counts) != set(count_fields):
        raise PrivateResearchBrowserAuditError("workspace projection counts are incomplete")
    return {
        "contract": CONTRACT,
        "manifest": manifest,
        "projection": projection,
        "semantic_hashes": {
            "manifest_sha256": _json_sha256(manifest),
            "projection_sha256": _json_sha256(projection),
        },
        "counts": counts,
        "initial": {
            "total": len(rows),
            "first_page_rows": [dict(row) for row in rows[:50]],
            "detail_row": detail_row,
        },
        "filters": [
            _filter_expectation("search", {"q": query}, query_rows),
            _filter_expectation("vendor", {"vendor": vendor}, vendor_rows),
            _filter_expectation("status", {"status": status}, status_rows),
            _filter_expectation(
                "stockout", {"stockout": stockout}, stockout_groups[stockout]
            ),
        ],
        "artifacts": artifact_expectations,
    }


def _write_private_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8"))
        handle.write(b"\n")


def _minimal_environment(*, path: str, home: Path | None = None) -> dict[str, str]:
    result = {
        "PATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(SRC_ROOT),
    }
    if home is not None:
        result["HOME"] = str(home)
    return result


def _initialize_runtime(runtime_root: Path) -> Path:
    completed = subprocess.run(
        (
            sys.executable,
            str(LAUNCHER),
            "initialize-runtime",
            "--runtime-root",
            str(runtime_root),
        ),
        cwd=REPO_ROOT,
        env=_minimal_environment(path=os.environ.get("PATH", "")),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        raise PrivateResearchBrowserAuditError("private runtime initialization failed")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PrivateResearchBrowserAuditError(
            "private runtime initialization result is malformed"
        ) from exc
    secret_path = runtime_root / "private-viewer.secret"
    if (
        result.get("secret_file") != str(secret_path)
        or result.get("secret_value_disclosed") is not False
    ):
        raise PrivateResearchBrowserAuditError(
            "private runtime initialization contract differs"
        )
    return secret_path


def _wait_health(process: subprocess.Popen[bytes], port: int) -> None:
    request = Request(
        f"http://127.0.0.1:{port}/health",
        headers={"Host": f"127.0.0.1:{port}"},
    )
    deadline = time.monotonic() + SERVER_READY_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PrivateResearchBrowserAuditError(
                f"private viewer exited before readiness ({process.returncode})"
            )
        try:
            with urlopen(request, timeout=1) as response:
                payload = json.loads(response.read())
            if response.status == 200 and payload.get("ok") is True:
                return
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise PrivateResearchBrowserAuditError("private viewer did not become ready")


def _start_server(
    workspace_root: Path,
    runtime_root: Path,
    port: int,
    log_path: Path,
) -> tuple[subprocess.Popen[bytes], Any]:
    log_handle = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        (
            sys.executable,
            str(LAUNCHER),
            "serve",
            "--runtime-root",
            str(runtime_root),
            "--workspace-root",
            str(workspace_root),
            "--port",
            str(port),
        ),
        cwd=REPO_ROOT,
        env=_minimal_environment(path=os.environ.get("PATH", "")),
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )
    try:
        _wait_health(process, port)
    except BaseException:
        _stop_server(process, log_handle, runtime_root)
        raise
    return process, log_handle


def _signal_process_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def _process_group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _viewer_child_pid(runtime_root: Path) -> int | None:
    pid_path = runtime_root / "private-viewer.pid"
    try:
        info = pid_path.stat(follow_symlinks=False)
        if (
            pid_path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size < 2
            or info.st_size > 4_096
        ):
            return None
        record = json.loads(pid_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or set(record) != {
            "pid",
            "source_commit",
            "source_tree",
            "process_start_ticks",
            "cmdline_sha256",
        }:
            return None
        pid = record["pid"]
        if type(pid) is not int or pid <= 1 or pid > (1 << 31) - 1:
            return None
        stat_value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        remainder = stat_value[stat_value.rindex(") ") + 2 :].split()
        start_ticks = int(remainder[19])
        if (
            record["process_start_ticks"] != start_ticks
            or record["cmdline_sha256"] != _sha256(command)
            or b"procurement_os.private_research_app:app" not in command.split(b"\0")
        ):
            return None
        return pid
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, IndexError):
        return None


def _stop_process_group(process: subprocess.Popen[bytes], *, timeout: float) -> None:
    if process.poll() is None:
        _signal_process_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_process_group(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def _stop_server(
    process: subprocess.Popen[bytes], handle: Any, runtime_root: Path
) -> None:
    child_pid = _viewer_child_pid(runtime_root)
    try:
        _stop_process_group(process, timeout=30)
    finally:
        handle.close()
    if child_pid is not None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _process_group_exists(child_pid):
            time.sleep(0.05)
        if _process_group_exists(child_pid):
            _signal_process_group(child_pid, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _process_group_exists(child_pid):
                time.sleep(0.05)
        if _process_group_exists(child_pid):
            _signal_process_group(child_pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and _process_group_exists(child_pid):
                time.sleep(0.05)
        if _process_group_exists(child_pid):
            raise PrivateResearchBrowserAuditError(
                "private viewer child process survived launcher stop"
            )
    if process.returncode not in {0, -signal.SIGTERM}:
        raise PrivateResearchBrowserAuditError(
            f"private viewer launcher exited unexpectedly ({process.returncode})"
        )


def _start_browser(
    chromium: str,
    profile: Path,
    cdp_port: int,
    log_path: Path,
) -> tuple[subprocess.Popen[bytes], Any]:
    handle = log_path.open("wb", buffering=0)
    process = subprocess.Popen(
        (
            chromium,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-first-run",
            "--no-default-browser-check",
            "--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE 127.0.0.1",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ),
        env=_minimal_environment(path=str(Path(chromium).parent), home=profile.parent),
        stdout=subprocess.DEVNULL,
        stderr=handle,
        start_new_session=True,
    )
    return process, handle


def _wait_cdp(process: subprocess.Popen[bytes], port: int) -> str:
    endpoint = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + CDP_READY_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PrivateResearchBrowserAuditError(
                f"Chromium exited before CDP readiness ({process.returncode})"
            )
        try:
            with urlopen(f"{endpoint}/json/version", timeout=0.5) as response:
                if response.status == 200:
                    return endpoint
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.1)
    raise PrivateResearchBrowserAuditError("Chromium CDP endpoint did not become ready")


def _stop_browser(process: subprocess.Popen[bytes], handle: Any) -> None:
    try:
        _stop_process_group(process, timeout=15)
    finally:
        handle.close()


def _run_cdp_phase(
    *,
    node: str,
    phase: str,
    base_url: str,
    cdp_endpoint: str,
    evidence_root: Path,
    downloads: Path,
    secret_path: Path,
    expectations_path: Path,
    state_path: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        (
            node,
            str(SCRIPT),
            phase,
            base_url,
            cdp_endpoint,
            str(evidence_root),
            str(downloads),
            str(secret_path),
            str(expectations_path),
            str(state_path),
        ),
        cwd=REPO_ROOT,
        env=_minimal_environment(path=str(Path(node).parent), home=expectations_path.parent),
        check=False,
        capture_output=True,
        text=True,
        timeout=5 * 60,
    )
    result_path = evidence_root / f"{phase}-browser-results.json"
    if completed.returncode:
        detail = "browser CDP phase failed"
        try:
            failed = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(failed.get("error"), str):
                detail = failed["error"].splitlines()[0][:500]
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        raise PrivateResearchBrowserAuditError(detail)
    try:
        summary = json.loads(completed.stdout)
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrivateResearchBrowserAuditError("browser CDP result is malformed") from exc
    if summary.get("passed") is not True or summary.get("phase") != phase:
        raise PrivateResearchBrowserAuditError("browser CDP summary differs")
    return result


def _validate_phase_result(
    result: Mapping[str, Any],
    *,
    phase: str,
    base_url: str,
    expectations: Mapping[str, Any],
) -> dict[str, Any]:
    expected_hashes = {
        item["name"]: item["sha256"] for item in expectations["artifacts"]
    }
    if (
        result.get("contract") != CONTRACT
        or result.get("phase") != phase
        or result.get("base_url") != base_url
        or result.get("passed") is not True
        or not isinstance(result.get("assertions"), list)
        or len(result["assertions"]) < 20
        or result.get("external_requests") != []
        or result.get("blocked_external_requests") != []
        or result.get("artifact_hashes") != expected_hashes
        or result.get("semantic_hashes") != expectations["semantic_hashes"]
        or result.get("counts") != expectations["counts"]
    ):
        raise PrivateResearchBrowserAuditError(
            f"{phase} browser acceptance result differs"
        )
    return {
        "assertions": len(result["assertions"]),
        "request_count": result.get("request_count"),
        "response_count": result.get("response_count"),
        "artifact_hashes": expected_hashes,
    }


def _assert_no_auth_material(evidence_root: Path, secret: bytes) -> None:
    credential = base64.b64encode(b"private:" + secret)
    forbidden = (
        secret,
        credential,
        b"Basic " + credential,
        _sha256(secret).encode("ascii"),
        _sha256(b"private:" + secret).encode("ascii"),
    )
    for path in evidence_root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if any(value in data for value in forbidden):
            raise PrivateResearchBrowserAuditError(
                "authentication material reached retained browser evidence"
            )


def _cleanup_runtime(runtime_root: Path) -> None:
    for name in ("private-viewer.pid", "private-viewer.secret"):
        path = runtime_root / name
        if not path.exists() and not path.is_symlink():
            continue
        try:
            info = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise PrivateResearchBrowserAuditError(
                "private runtime cleanup target is unavailable"
            ) from exc
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PrivateResearchBrowserAuditError(
                "private runtime cleanup target differs"
            )
        path.unlink()
    if runtime_root.exists():
        if any(runtime_root.iterdir()):
            raise PrivateResearchBrowserAuditError(
                "private runtime retained unexpected objects"
            )
        runtime_root.rmdir()


def run(args: argparse.Namespace) -> dict[str, Any]:
    os.umask(0o077)
    workspace_root = args.workspace_root
    runtime_root = args.runtime_root
    evidence_root = args.evidence_root
    for path, label in (
        (workspace_root, "workspace root"),
        (runtime_root, "runtime root"),
        (evidence_root, "evidence root"),
    ):
        _reject_symlinked_components(path, label=label)
    _require_disjoint(workspace_root, runtime_root, evidence_root)
    try:
        workspace = read_private_research_workspace(workspace_root)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchBrowserAuditError(
            "sealed private research workspace is invalid"
        ) from exc
    expectations = _build_expectations(workspace)

    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    node = shutil.which("node")
    if chromium is None or node is None:
        raise PrivateResearchBrowserAuditError("Chromium and Node.js are required")
    if not SCRIPT.is_file() or not LAUNCHER.is_file():
        raise PrivateResearchBrowserAuditError("browser audit composition is incomplete")

    _prepare_empty_root(evidence_root, label="evidence root")
    _prepare_empty_root(runtime_root, label="runtime root")
    downloads_root = evidence_root / "downloads"
    downloads_root.mkdir(mode=0o700)
    initial_downloads = downloads_root / "initial"
    restart_downloads = downloads_root / "restart"
    initial_downloads.mkdir(mode=0o700)
    restart_downloads.mkdir(mode=0o700)
    server_log = evidence_root / "viewer.log"
    server_log.touch(mode=0o600)
    chromium_log = evidence_root / "chromium.stderr"
    app_port = args.port if args.port is not None else _free_port()
    _assert_port_free(app_port, label="private viewer")
    cdp_port = _free_port()
    while cdp_port == app_port:
        cdp_port = _free_port()
    _assert_port_free(cdp_port, label="Chromium CDP")
    base_url = f"http://127.0.0.1:{app_port}"

    secret_path: Path | None = None
    secret = b""
    server: subprocess.Popen[bytes] | None = None
    server_handle: Any = None
    browser: subprocess.Popen[bytes] | None = None
    browser_handle: Any = None
    temporary_context: tempfile.TemporaryDirectory[str] | None = None
    phase_results: dict[str, dict[str, Any]] = {}
    cleanup_error: BaseException | None = None
    try:
        secret_path = _initialize_runtime(runtime_root)
        secret = secret_path.read_bytes().removesuffix(b"\n")
        if len(secret) < 24 or len(secret) > 512 or b"\x00" in secret:
            raise PrivateResearchBrowserAuditError("private runtime secret shape differs")
        temporary_context = tempfile.TemporaryDirectory(
            prefix="buffalo-private-browser-"
        )
        temporary_root = Path(temporary_context.name)
        expectations_path = temporary_root / "expectations.json"
        _write_private_json(expectations_path, expectations)
        state_path = evidence_root / "restart-state.json"
        profile = temporary_root / "chromium-profile"
        browser, browser_handle = _start_browser(
            chromium, profile, cdp_port, chromium_log
        )
        cdp_endpoint = _wait_cdp(browser, cdp_port)
        for phase, downloads in (
            ("initial", initial_downloads),
            ("restart", restart_downloads),
        ):
            server, server_handle = _start_server(
                workspace_root, runtime_root, app_port, server_log
            )
            raw_result = _run_cdp_phase(
                node=node,
                phase=phase,
                base_url=base_url,
                cdp_endpoint=cdp_endpoint,
                evidence_root=evidence_root,
                downloads=downloads,
                secret_path=secret_path,
                expectations_path=expectations_path,
                state_path=state_path,
            )
            phase_results[phase] = _validate_phase_result(
                raw_result,
                phase=phase,
                base_url=base_url,
                expectations=expectations,
            )
            _stop_server(server, server_handle, runtime_root)
            server = None
            server_handle = None
            _assert_port_free(app_port, label="private viewer")
    finally:
        if server is not None and server_handle is not None:
            try:
                _stop_server(server, server_handle, runtime_root)
            except BaseException as exc:
                cleanup_error = exc
        if browser is not None and browser_handle is not None:
            try:
                _stop_browser(browser, browser_handle)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if temporary_context is not None:
            try:
                temporary_context.cleanup()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if secret:
            try:
                _assert_no_auth_material(evidence_root, secret)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        try:
            _cleanup_runtime(runtime_root)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        try:
            _assert_port_free(app_port, label="private viewer")
            _assert_port_free(cdp_port, label="Chromium CDP")
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise PrivateResearchBrowserAuditError(
                "browser acceptance cleanup failed"
            ) from cleanup_error

    if set(phase_results) != {"initial", "restart"}:
        raise PrivateResearchBrowserAuditError("browser acceptance phases are incomplete")
    if phase_results["initial"]["artifact_hashes"] != phase_results["restart"]["artifact_hashes"]:
        raise PrivateResearchBrowserAuditError("artifact hashes changed across restart")
    result = {
        "contract": CONTRACT,
        "workspace_root": str(workspace_root),
        "workspace_id": expectations["manifest"]["workspace_id"],
        "origin": base_url,
        "evidence_root": str(evidence_root),
        "semantic_hashes": expectations["semantic_hashes"],
        "counts": expectations["counts"],
        "artifact_hashes": phase_results["initial"]["artifact_hashes"],
        "phases": phase_results,
        "runtime_secret_retained": False,
        "listeners_stopped": True,
        "operational_authority": False,
    }
    _write_private_json(evidence_root / "acceptance-result.json", result)
    if secret:
        _assert_no_auth_material(evidence_root, secret)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit a sealed private-research workspace in real Chromium"
    )
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        result = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
