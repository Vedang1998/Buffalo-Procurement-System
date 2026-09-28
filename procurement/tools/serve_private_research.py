#!/usr/bin/env python3
"""Initialize and serve the isolated private research review application."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "procurement" / "src"
RUNTIME_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_VIEWER_RUNTIME_V1"
SECRET_NAME = "private-viewer.secret"
PID_NAME = "private-viewer.pid"
# A full private V2 semantic replay recalculates every registered horizon before
# the app can become ready.  The ceiling is bounded but must cover that genuine
# fail-closed replay on the complete private research population.
# Corrected-V3 performs a full accepted-parent replay plus 1,365 joint-horizon
# semantic replays at application startup.  Keep the wait bounded while leaving
# enough headroom for the authenticated cold-start path on the owned 8 GiB host.
READINESS_TIMEOUT_SECONDS = 45 * 60
PROCESS_IDENTITY_TIMEOUT_SECONDS = 5
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PID = (1 << 31) - 1
_MAX_PID_RECORD_BYTES = 4_096

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class PrivateResearchServeError(RuntimeError):
    pass


def read_private_research_workspace(path: Path) -> dict[str, object]:
    from procurement_os.private_research import (
        read_private_research_workspace_structural as reader,
    )

    return reader(path)


def _validate_workspace_tuple(workspace: object) -> None:
    if not isinstance(workspace, dict):
        raise PrivateResearchServeError("private viewer workspace tuple differs")
    manifest = workspace.get("manifest")
    projection = workspace.get("projection")
    tuples = {
        "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V1": (
            "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V1",
            "PRIVATE_REAL_SOURCE_REVIEW",
            "PRIVATE_REAL_DATA_RESEARCH_ONLY",
        ),
        "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V2": (
            "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2",
            "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
            "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
        ),
        "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3": (
            "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3",
            "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
            "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
        ),
        "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3_CORRECTED_V1": (
            "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3_CORRECTED_V1",
            "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
            "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
        ),
    }
    expected = (
        tuples.get(manifest.get("contract"))
        if isinstance(manifest, dict)
        else None
    )
    if (
        not isinstance(projection, dict)
        or expected is None
        or manifest.get("data_mode") != expected[1]
        or manifest.get("projection_contract") != expected[0]
        or projection.get("contract") != expected[0]
        or projection.get("data_mode") != expected[2]
    ):
        raise PrivateResearchServeError("private viewer workspace tuple differs")


def _run_structural_preflight(workspace: Path) -> None:
    try:
        child_pid = os.fork()
    except OSError as exc:
        raise PrivateResearchServeError(
            "private viewer structural preflight could not start"
        ) from exc
    if child_pid == 0:
        try:
            result = read_private_research_workspace(workspace)
            _validate_workspace_tuple(result)
        except BaseException:
            os._exit(1)
        os._exit(0)
    try:
        while True:
            try:
                waited_pid, status_value = os.waitpid(child_pid, 0)
                break
            except InterruptedError:
                continue
    except ChildProcessError as exc:
        raise PrivateResearchServeError(
            "private viewer structural preflight could not be reaped"
        ) from exc
    except BaseException:
        child_is_live = False
        while True:
            try:
                probed_pid, _ = os.waitpid(child_pid, os.WNOHANG)
                child_is_live = probed_pid == 0
                break
            except InterruptedError:
                continue
            except ChildProcessError:
                break
        if child_is_live:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            while True:
                try:
                    os.waitpid(child_pid, 0)
                    break
                except InterruptedError:
                    continue
                except ChildProcessError:
                    break
        raise
    if (
        waited_pid != child_pid
        or not os.WIFEXITED(status_value)
        or os.WEXITSTATUS(status_value)
    ):
        raise PrivateResearchServeError(
            "private viewer structural preflight failed"
        )


def _reject_symlinked_components(path: Path) -> None:
    if not path.is_absolute():
        raise PrivateResearchServeError(f"required path must be absolute: {path}")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PrivateResearchServeError(
                f"required path component is unavailable: {current}"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise PrivateResearchServeError(
                f"required path has a symlinked component: {current}"
            )


def _owned_mode(
    path: Path, expected_mode: int, *, regular: bool = False
) -> os.stat_result:
    _reject_symlinked_components(path)
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrivateResearchServeError(f"required path is unavailable: {path}") from exc
    expected_type = stat.S_ISREG if regular else stat.S_ISDIR
    if path.is_symlink() or not expected_type(info.st_mode):
        raise PrivateResearchServeError(f"required path type differs: {path}")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != expected_mode:
        raise PrivateResearchServeError(f"required path ownership or mode differs: {path}")
    return info


def _inside_workspace_layout(path: Path) -> bool:
    current = path.resolve(strict=False)
    return any(
        candidate.parent.name == "workspaces"
        and candidate.parent.parent.name == "private-research"
        for candidate in (current, *current.parents)
    )


def _require_disjoint_paths(runtime_root: Path, workspace_root: Path) -> None:
    _reject_symlinked_components(runtime_root)
    _reject_symlinked_components(workspace_root)
    runtime = runtime_root.resolve(strict=False)
    workspace = workspace_root.resolve(strict=False)
    if (
        runtime == workspace
        or runtime.is_relative_to(workspace)
        or workspace.is_relative_to(runtime)
    ):
        raise PrivateResearchServeError(
            "runtime and private workspace paths must be disjoint"
        )


def initialize_runtime(root: Path) -> dict[str, object]:
    if not root.is_absolute():
        raise PrivateResearchServeError("runtime root must be absolute")
    _reject_symlinked_components(root)
    if _inside_workspace_layout(root):
        raise PrivateResearchServeError(
            "runtime root must not be inside a private research workspace"
        )
    created_root = False
    if not root.exists():
        root.mkdir(mode=0o700, parents=False)
        created_root = True
    _owned_mode(root, 0o700)
    if any(root.iterdir()):
        raise PrivateResearchServeError("runtime root must be empty")
    secret_path = root / SECRET_NAME
    try:
        descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
    except Exception:
        secret_path.unlink(missing_ok=True)
        if created_root:
            root.rmdir()
        raise
    return {
        "contract": RUNTIME_CONTRACT,
        "runtime_root": str(root),
        "secret_file": str(secret_path),
        "secret_value_disclosed": False,
    }


def _source_identity() -> tuple[str, str]:
    if subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "")},
    ).stdout:
        raise PrivateResearchServeError("source worktree must be clean")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD^{commit}"], cwd=REPO_ROOT, check=True,
        text=True, stdout=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "")}
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=REPO_ROOT, check=True,
        text=True, stdout=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "")}
    ).stdout.strip()
    return commit, tree


def _assert_port_free(port: int) -> None:
    if port < 1024 or port > 65535:
        raise PrivateResearchServeError("private viewer port is outside the permitted range")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise PrivateResearchServeError("private viewer port is unavailable") from exc


def _process_identity(pid: int) -> dict[str, object]:
    try:
        stat_value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        remainder = stat_value[stat_value.rindex(") ") + 2 :].split()
        start_ticks = int(remainder[19])
    except (OSError, ValueError, IndexError) as exc:
        raise PrivateResearchServeError(
            "private viewer process identity is unavailable"
        ) from exc
    if b"procurement_os.private_research_app:app" not in command.split(b"\0"):
        raise PrivateResearchServeError("private viewer process command differs")
    return {
        "process_start_ticks": start_ticks,
        "cmdline_sha256": hashlib.sha256(command).hexdigest(),
    }


def _wait_process_identity(
    process: subprocess.Popen[bytes],
) -> dict[str, object]:
    deadline = time.monotonic() + PROCESS_IDENTITY_TIMEOUT_SECONDS
    last_error: PrivateResearchServeError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PrivateResearchServeError(
                "private viewer exited before process identity stabilized"
            )
        try:
            return _process_identity(process.pid)
        except PrivateResearchServeError as exc:
            last_error = exc
        time.sleep(0.01)
    raise PrivateResearchServeError(
        "private viewer process identity did not stabilize"
    ) from last_error


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object has duplicate fields")
        result[key] = value
    return result


def serve(root: Path, workspace: Path, port: int) -> None:
    if not root.is_absolute() or not workspace.is_absolute():
        raise PrivateResearchServeError("runtime and workspace roots must be absolute")
    _owned_mode(root, 0o700)
    _owned_mode(workspace, 0o700)
    _require_disjoint_paths(root, workspace)
    secret_path = root / SECRET_NAME
    _owned_mode(secret_path, 0o600, regular=True)
    pid_path = root / PID_NAME
    if pid_path.exists() or pid_path.is_symlink():
        raise PrivateResearchServeError("private viewer runtime is already reserved")
    # The application startup performs the full raw-source semantic replay. For
    # legacy contracts this call is structural/artifact-only; corrected-V3 also
    # performs its independently required planner/source replay here.
    _run_structural_preflight(workspace)
    gc.collect()
    commit, tree = _source_identity()
    _assert_port_free(port)
    child_env = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONPATH": str(SRC_ROOT),
        "BUFFALO_RUNTIME_MODE": "PRIVATE_REAL_SOURCE_REVIEW",
        "BUFFALO_LOCAL_PORT": str(port),
        "BUFFALO_LOCAL_AUTH_SECRET_FILE": str(secret_path),
        "BUFFALO_LOCAL_PRINCIPAL_REF": "private:owner-research:01",
        "BUFFALO_LOCAL_ROLE_REF": "PRIVATE_REAL_REVIEWER",
        "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": str(workspace),
        "BUFFALO_SOURCE_COMMIT": commit,
        "BUFFALO_SOURCE_TREE": tree,
    }
    def stop_requested(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, stop_requested)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "procurement_os.private_research_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--no-proxy-headers",
            "--no-access-log",
        ],
        cwd=REPO_ROOT,
        env=child_env,
        start_new_session=True,
    )
    requested_stop = False
    try:
        process_identity = _wait_process_identity(process)
        descriptor = os.open(pid_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "pid": process.pid,
                    "source_commit": commit,
                    "source_tree": tree,
                    **process_identity,
                },
                handle,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
        ready = False
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise PrivateResearchServeError("private viewer exited before readiness")
            try:
                request = Request(
                    f"http://127.0.0.1:{port}/health",
                    headers={"Host": f"127.0.0.1:{port}"},
                )
                with urlopen(request, timeout=1) as response:
                    payload = json.loads(response.read())
                if response.status == 200 and payload.get("ok") is True:
                    ready = True
                    break
            except (URLError, TimeoutError, json.JSONDecodeError):
                pass
            time.sleep(0.1)
        if not ready:
            raise PrivateResearchServeError("private viewer did not become ready")
        print(json.dumps({
            "contract": RUNTIME_CONTRACT,
            "url": f"http://127.0.0.1:{port}/private-research",
            "source_commit": commit,
            "source_tree": tree,
            "operational_authority": False,
        }, sort_keys=True), flush=True)
        process.wait()
    except KeyboardInterrupt:
        requested_stop = True
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        pid_path.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, previous_sigterm)
    if not requested_stop and process.returncode not in (0, None):
        raise PrivateResearchServeError(
            f"private viewer exited unsuccessfully with status {process.returncode}"
        )


def status(root: Path) -> dict[str, object]:
    _owned_mode(root, 0o700)
    pid_path = root / PID_NAME
    if not pid_path.exists() and not pid_path.is_symlink():
        return {"contract": RUNTIME_CONTRACT, "running": False}
    try:
        info = _owned_mode(pid_path, 0o600, regular=True)
        if info.st_size < 2 or info.st_size > _MAX_PID_RECORD_BYTES:
            raise ValueError("PID record size differs")
        record = json.loads(
            pid_path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
        if not isinstance(record, dict) or set(record) != {
            "pid",
            "source_commit",
            "source_tree",
            "process_start_ticks",
            "cmdline_sha256",
        }:
            raise ValueError("PID record shape differs")
        pid = record["pid"]
        if type(pid) is not int or pid <= 0 or pid > _MAX_PID:
            raise ValueError("PID record process identifier differs")
        if (
            type(record["process_start_ticks"]) is not int
            or record["process_start_ticks"] <= 0
            or not isinstance(record["source_commit"], str)
            or _HEX40.fullmatch(record["source_commit"]) is None
            or not isinstance(record["source_tree"], str)
            or _HEX40.fullmatch(record["source_tree"]) is None
            or not isinstance(record["cmdline_sha256"], str)
            or _HEX64.fullmatch(record["cmdline_sha256"]) is None
        ):
            raise ValueError("PID record identity shape differs")
        os.kill(pid, 0)
        actual_identity = _process_identity(pid)
        if (
            actual_identity["process_start_ticks"]
            != record["process_start_ticks"]
            or actual_identity["cmdline_sha256"] != record["cmdline_sha256"]
        ):
            raise ValueError("PID record identity differs")
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        PrivateResearchServeError,
    ):
        return {"contract": RUNTIME_CONTRACT, "running": False, "stale_pid_file": True}
    return {"contract": RUNTIME_CONTRACT, "running": True, "pid": pid}


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("initialize-runtime")
    initialize.add_argument("--runtime-root", type=Path, required=True)
    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--runtime-root", type=Path, required=True)
    serve_parser.add_argument("--workspace-root", type=Path, required=True)
    serve_parser.add_argument("--port", type=int, default=8876)
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "initialize-runtime":
            result = initialize_runtime(args.runtime_root)
        elif args.command == "serve":
            serve(args.runtime_root, args.workspace_root, args.port)
            result = {"contract": RUNTIME_CONTRACT, "stopped": True}
        else:
            result = status(args.runtime_root)
    except (PrivateResearchServeError, PermissionError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
