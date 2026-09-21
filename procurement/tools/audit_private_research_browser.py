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
import ctypes
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import re
import secrets
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
CONTRACT = "BUFFALO_PRIVATE_RESEARCH_BROWSER_ACCEPTANCE_V2"
ARTIFACT_NAMES = (
    "owner-preview.html",
    "owner-worksheet.csv",
    "coverage.json",
    "projection.json",
)
# The launcher performs one canonical replay before it starts Uvicorn, then the
# app performs its own startup replay under the launcher's bounded window.  A
# complete V2 population takes roughly fifteen minutes per replay in the owned
# test environment, so the outer ceiling covers both without weakening either.
SERVER_READY_SECONDS = 35 * 60
CDP_READY_SECONDS = 30
_HEX40 = re.compile(r"[0-9a-f]{40}")
_PROCESS_TOKEN_ENV = "BUFFALO_PRIVATE_BROWSER_AUDIT_PROCESS_TOKEN"
_PR_SET_CHILD_SUBREAPER = 36
_SUBREAPER_ENABLED = False
_TRUSTED_GIT = Path("/usr/bin/git")
EXPECTED_APP_ROUTE_TABLE = (
    {"path": "/", "methods": ["GET"]},
    {"path": "/auth/login", "methods": ["GET"]},
    {"path": "/health", "methods": ["GET"]},
    {"path": "/private-research", "methods": ["GET"]},
    {"path": "/private-research/artifacts/{artifact_name}", "methods": ["GET"]},
    {"path": "/private-research/manifest", "methods": ["GET"]},
    {"path": "/private-research/projection", "methods": ["GET"]},
)


def _expected_assertion_ids() -> tuple[str, ...]:
    identifiers = [
        "v2.cdp.node_prerequisites",
        "v2.cdp.browser_version",
        "v2.page.initial.rows",
        "v2.page.initial.details",
        "v2.page.initial.count",
        "v2.page.initial.mode",
        "v2.page.initial.read_only",
        "v2.detail.exact",
        "v2.detail.core_evidence",
        "v2.detail.row_identity",
        "v2.detail.bound_evidence",
        "v2.detail.reason_binding",
        "v2.detail.visible_result",
        "v2.health.contract",
        "v2.health.no_hashes",
        "v2.readback.manifest",
        "v2.readback.projection",
        "v2.readback.counts",
    ]
    for mode in ("missing", "wrong"):
        for surface in ("index", "manifest", "projection", "artifact"):
            identifiers.append(f"v2.auth.{mode}.{surface}")
    identifiers.extend(
        ("v2.routes.operational_absent", "v2.routes.write_methods_denied")
    )
    for name in ("search", "vendor", "status", "stockout"):
        for check in ("submit", "rows", "details", "count", "read_only"):
            identifiers.append(f"v2.filter.{name}.{check}")
    identifiers.append("v2.search.target")
    for name in ARTIFACT_NAMES:
        slug = name.replace(".", "_").replace("-", "_")
        for check in ("filename", "bytes", "response"):
            identifiers.append(f"v2.artifact.{slug}.{check}")
    identifiers.extend(
        (
            "v2.artifacts.complete",
            "v2.auth.no_ambient_cookie",
            "v2.restart.identity",
            "v2.cdp.all_targets_guarded",
            "v2.network.no_external_http",
            "v2.network.no_external_websocket",
            "v2.network.no_unexpected_scheme",
            "v2.browser.no_runtime_exceptions",
            "v2.browser.no_console_errors",
            "v2.cdp.no_event_errors",
            "v2.responses.no_store",
        )
    )
    if len(identifiers) != len(set(identifiers)):
        raise AssertionError("browser assertion identifiers must be unique")
    return tuple(identifiers)


ASSERTION_IDS = _expected_assertion_ids()

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def read_private_research_workspace(path: Path) -> dict[str, object]:
    from procurement_os.private_research import (
        read_private_research_workspace_structural as reader,
    )

    return reader(path)


def filter_private_research_rows(
    projection: Mapping[str, Any], **filters: str
) -> list[dict[str, object]]:
    from procurement_os.private_research_projection import (
        filter_private_research_rows as filter_rows,
    )

    return filter_rows(projection, **filters)


class PrivateResearchBrowserAuditError(RuntimeError):
    pass


class _OwnedProcessRegistry:
    """Identity-checked ownership for one launched process tree."""

    def __init__(
        self,
        *,
        token: str,
        label: str,
        leader_pid: int,
        leader_start_ticks: int,
        session_id: int | None = None,
        identities: dict[int, int] | None = None,
        baseline_owner_children: dict[int, int] | None = None,
    ) -> None:
        self.token = token
        self.label = label
        self.leader_pid = leader_pid
        self.leader_start_ticks = leader_start_ticks
        self.session_id = session_id
        self.identities = dict(identities or {})
        self.owner_pid = os.getpid()
        self.baseline_owner_children = dict(baseline_owner_children or {})


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


def _git_stdout(repo_root: Path, *arguments: str) -> bytes:
    try:
        git_info = _TRUSTED_GIT.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrivateResearchBrowserAuditError(
            "trusted Git executable is unavailable"
        ) from exc
    if (
        _TRUSTED_GIT.is_symlink()
        or not stat.S_ISREG(git_info.st_mode)
        or git_info.st_uid != 0
        or stat.S_IMODE(git_info.st_mode) & 0o022
        or not os.access(_TRUSTED_GIT, os.X_OK)
    ):
        raise PrivateResearchBrowserAuditError(
            "trusted Git executable identity differs"
        )
    git_environment = _minimal_environment(path=str(_TRUSTED_GIT.parent))
    git_environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    try:
        completed = subprocess.run(
            (str(_TRUSTED_GIT), "-C", str(repo_root), *arguments),
            env=git_environment,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrivateResearchBrowserAuditError(
            "repository source identity is unavailable"
        ) from exc
    if completed.returncode != 0:
        raise PrivateResearchBrowserAuditError(
            "repository source identity is unavailable"
        )
    return completed.stdout


def _validate_source_object_relation(
    repo_root: Path, *, commit: str, tree: str
) -> dict[str, str]:
    if _HEX40.fullmatch(commit) is None or _HEX40.fullmatch(tree) is None:
        raise PrivateResearchBrowserAuditError("repository source object shape differs")
    try:
        commit_type = _git_stdout(repo_root, "cat-file", "-t", commit).decode(
            "ascii"
        ).strip()
        resolved_tree = _git_stdout(
            repo_root, "rev-parse", "--verify", f"{commit}^{{tree}}"
        ).decode("ascii").strip()
        tree_type = _git_stdout(repo_root, "cat-file", "-t", tree).decode(
            "ascii"
        ).strip()
        commit_body = _git_stdout(repo_root, "cat-file", "-p", commit).decode(
            "utf-8", "strict"
        )
    except UnicodeError as exc:
        raise PrivateResearchBrowserAuditError(
            "repository source object is malformed"
        ) from exc
    first_line = commit_body.splitlines()[0] if commit_body else ""
    if (
        commit_type != "commit"
        or tree_type != "tree"
        or resolved_tree != tree
        or first_line != f"tree {tree}"
    ):
        raise PrivateResearchBrowserAuditError(
            "repository commit and tree relation differs"
        )
    return {"commit": commit, "tree": tree}


def _git_blob_oid(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _attest_tracked_worktree(repo_root: Path, *, commit: str) -> set[Path]:
    try:
        raw_tree = _git_stdout(
            repo_root, "ls-tree", "-r", "-z", "--full-tree", commit
        )
        raw_index = _git_stdout(repo_root, "ls-files", "--stage", "-v", "-z")
    except PrivateResearchBrowserAuditError:
        raise

    tree: dict[str, tuple[str, str]] = {}
    for raw in raw_tree.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise PrivateResearchBrowserAuditError(
                "repository tree inventory is malformed"
            ) from exc
        if object_type != "blob" or mode not in {"100644", "100755", "120000"}:
            raise PrivateResearchBrowserAuditError(
                "repository tree contains an unsupported tracked object"
            )
        tree[relative] = (mode, object_id)

    index: dict[str, tuple[str, str]] = {}
    for raw in raw_index.split(b"\0"):
        if not raw:
            continue
        try:
            tagged_metadata, raw_path = raw.split(b"\t", 1)
            tag, metadata = tagged_metadata[:1], tagged_metadata[2:]
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise PrivateResearchBrowserAuditError(
                "repository index inventory is malformed"
            ) from exc
        if tag != b"H" or stage != "0":
            raise PrivateResearchBrowserAuditError(
                "repository index contains non-normal tracked flags"
            )
        index[relative] = (mode, object_id)
    if index != tree:
        raise PrivateResearchBrowserAuditError(
            "repository index differs from the bound commit tree"
        )

    tracked_paths: set[Path] = set()
    for relative, (mode, expected_oid) in tree.items():
        path = repo_root / relative
        if not path.is_relative_to(repo_root) or any(
            parent.is_symlink()
            for parent in path.parents
            if parent != repo_root and parent.is_relative_to(repo_root)
        ):
            raise PrivateResearchBrowserAuditError(
                "repository tracked path leaves the repository root"
            )
        try:
            info = path.stat(follow_symlinks=False)
            if mode == "120000":
                if not stat.S_ISLNK(info.st_mode):
                    raise PrivateResearchBrowserAuditError(
                        "repository tracked symlink type differs"
                    )
                data = os.fsencode(os.readlink(path))
            else:
                if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                    raise PrivateResearchBrowserAuditError(
                        "repository tracked file type differs"
                    )
                executable = bool(stat.S_IMODE(info.st_mode) & 0o111)
                if executable != (mode == "100755"):
                    raise PrivateResearchBrowserAuditError(
                        "repository tracked executable mode differs"
                    )
                data = path.read_bytes()
        except OSError as exc:
            raise PrivateResearchBrowserAuditError(
                "repository tracked file is unavailable"
            ) from exc
        if _git_blob_oid(data) != expected_oid:
            raise PrivateResearchBrowserAuditError(
                "repository tracked worktree bytes differ from HEAD"
            )
        tracked_paths.add(path)
    return tracked_paths


def _attest_python_execution_surface(
    repo_root: Path, *, tracked_paths: set[Path]
) -> None:
    roots = (
        repo_root / "procurement" / "src",
        repo_root / "procurement" / "tools",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                raise PrivateResearchBrowserAuditError(
                    "Python execution surface contains an unbound symlink"
                )
            if (
                candidate.is_file()
                and candidate.suffix.lower() in {".py", ".pyi", ".so", ".pyd"}
                and candidate not in tracked_paths
            ):
                raise PrivateResearchBrowserAuditError(
                    "Python execution surface contains untracked executable bytes"
                )

        for source in root.rglob("*.py"):
            cache = Path(importlib.util.cache_from_source(str(source)))
            if not cache.exists():
                continue
            try:
                cache_info = cache.stat(follow_symlinks=False)
                if (
                    cache.is_symlink()
                    or not stat.S_ISREG(cache_info.st_mode)
                    or cache_info.st_size < 16
                    or cache_info.st_size > max(1_048_576, source.stat().st_size * 20)
                ):
                    raise PrivateResearchBrowserAuditError(
                        "Python bytecode cache identity differs"
                    )
                cached_code = marshal.loads(cache.read_bytes()[16:])
                compiled_code = compile(
                    source.read_bytes(),
                    cached_code.co_filename,
                    "exec",
                    dont_inherit=True,
                    optimize=sys.flags.optimize,
                )
            except (OSError, ValueError, EOFError, TypeError, AttributeError) as exc:
                raise PrivateResearchBrowserAuditError(
                    "Python bytecode cache is malformed"
                ) from exc
            if cached_code != compiled_code:
                raise PrivateResearchBrowserAuditError(
                    "Python bytecode cache differs from tracked source"
                )

        expected_caches = {
            Path(importlib.util.cache_from_source(str(source)))
            for source in root.rglob("*.py")
        }
        for cache in root.rglob("*.pyc"):
            if cache in expected_caches:
                continue
            if (
                cache.parent.name != "__pycache__"
                or f".{sys.implementation.cache_tag}." in cache.name
            ):
                raise PrivateResearchBrowserAuditError(
                    "Python execution surface contains unexpected bytecode"
                )


def _repository_source_identity(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    try:
        resolved_repo = repo_root.resolve(strict=True)
        git_root = Path(
            _git_stdout(repo_root, "rev-parse", "--show-toplevel")
            .decode("utf-8", "strict")
            .strip()
        ).resolve(strict=True)
        status = _git_stdout(
            repo_root, "status", "--porcelain=v1", "--untracked-files=all", "-z"
        )
        commit = _git_stdout(
            repo_root, "rev-parse", "--verify", "HEAD^{commit}"
        ).decode("ascii").strip()
        tree = _git_stdout(
            repo_root, "rev-parse", "--verify", "HEAD^{tree}"
        ).decode("ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise PrivateResearchBrowserAuditError(
            "repository source identity is unavailable"
        ) from exc
    if git_root != resolved_repo:
        raise PrivateResearchBrowserAuditError("repository root identity differs")
    if status:
        raise PrivateResearchBrowserAuditError(
            "repository must be clean for browser acceptance"
        )
    identity = _validate_source_object_relation(
        repo_root, commit=commit, tree=tree
    )
    tracked_paths = _attest_tracked_worktree(repo_root, commit=commit)
    _attest_python_execution_surface(repo_root, tracked_paths=tracked_paths)
    final_status = _git_stdout(
        repo_root, "status", "--porcelain=v1", "--untracked-files=all", "-z"
    )
    final_commit = _git_stdout(
        repo_root, "rev-parse", "--verify", "HEAD^{commit}"
    ).decode("ascii").strip()
    final_tree = _git_stdout(
        repo_root, "rev-parse", "--verify", "HEAD^{tree}"
    ).decode("ascii").strip()
    if final_status or final_commit != commit or final_tree != tree:
        raise PrivateResearchBrowserAuditError(
            "repository changed during source attestation"
        )
    return identity


def _assert_repository_source_identity(
    expected: Mapping[str, Any], repo_root: Path = REPO_ROOT
) -> None:
    if _repository_source_identity(repo_root) != expected:
        raise PrivateResearchBrowserAuditError(
            "repository source identity changed during browser acceptance"
        )


def _private_app_route_table() -> list[dict[str, object]]:
    # Lazy import keeps the audit module's base import graph operationally inert.
    from procurement_os.private_research_app import app

    actual = sorted(
        (
            {"path": str(route.path), "methods": sorted(route.methods or ())}
            for route in app.routes
        ),
        key=lambda item: item["path"],
    )
    expected = [
        {"path": item["path"], "methods": list(item["methods"])}
        for item in EXPECTED_APP_ROUTE_TABLE
    ]
    if actual != expected:
        raise PrivateResearchBrowserAuditError(
            "private viewer route table or method inventory differs"
        )
    return expected


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
    if isinstance(forecast, Mapping):
        reasons = forecast.get("reason_codes", [])
        calculated = forecast.get("status") == "CALCULATED_RESEARCH_ONLY"
    else:
        scenarios = row.get("scenario_results")
        values = scenarios.values() if isinstance(scenarios, Mapping) else ()
        reasons = [
            reason
            for scenario in values
            if isinstance(scenario, Mapping)
            for reason in scenario.get("reason_codes", [])
        ]
        calculated = any(
            isinstance(scenario, Mapping)
            and scenario.get("status") == "CALCULATED_RESEARCH_ONLY"
            for scenario in (
                scenarios.values() if isinstance(scenarios, Mapping) else ()
            )
        )
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
    if calculated:
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
    is_v2 = manifest.get("contract") == "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V2"
    is_v3 = manifest.get("contract") == "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3"
    is_development = is_v2 or is_v3
    rows = projection.get("owner_worksheet" if is_development else "research_rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise PrivateResearchBrowserAuditError(
            "browser filtering requires at least one research row"
        )
    if is_development:
        research = projection.get("forecast_research")
        counts_value = projection.get("forecast_research_counts")
        owner_rows = projection.get("owner_worksheet")
        scenarios = research.get("scenarios") if isinstance(research, Mapping) else None
        allocation = (
            research.get("history", {}).get("allocation_controls")
            if isinstance(research, Mapping)
            and isinstance(research.get("history"), Mapping)
            else None
        )
        if (
            projection.get("contract")
            != (
                "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3"
                if is_v3
                else "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2"
            )
            or projection.get("data_mode")
            != "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY"
            or manifest.get("data_mode")
            != "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY"
            or manifest.get("projection_contract")
            != (
                "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3"
                if is_v3
                else "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2"
            )
            or not isinstance(manifest.get("base_intake_id"), str)
            or len(manifest["base_intake_id"]) != 64
            or not isinstance(manifest.get("base_intake_sha256"), str)
            or len(manifest["base_intake_sha256"]) != 64
            or not isinstance(scenarios, list)
            or [item.get("scenario_id") for item in scenarios if isinstance(item, Mapping)]
            != ["H3", "H10", "H17"]
            or not isinstance(counts_value, Mapping)
            or set(counts_value) != {"H3", "H10", "H17"}
            or not isinstance(allocation, Mapping)
            or not isinstance(owner_rows, list)
            or len(owner_rows) != len(projection.get("coverage_rows", []))
        ):
            raise PrivateResearchBrowserAuditError(
                (
                    "private V3 forecast workspace contract differs"
                    if is_v3
                    else "private V2 forecast workspace contract differs"
                )
            )
        eligible = allocation.get("eligible_variant_count")
        coverage_count = len(projection.get("coverage_rows", []))
        if isinstance(eligible, bool) or not isinstance(eligible, int):
            raise PrivateResearchBrowserAuditError(
                "private development eligible population differs"
            )
        for horizon in ("H3", "H10", "H17"):
            bucket = counts_value[horizon]
            if (
                not isinstance(bucket, Mapping)
                or set(bucket)
                != {"calculated", "blocked", "missing", "unprocessed", "numerical_zero"}
                or any(
                    isinstance(bucket.get(key), bool)
                    or not isinstance(bucket.get(key), int)
                    or bucket[key] < 0
                    for key in bucket
                )
                or bucket["calculated"] + bucket["blocked"] + bucket["unprocessed"]
                != eligible
                or bucket["missing"] + eligible != coverage_count
                or bucket["numerical_zero"] > bucket["calculated"]
            ):
                raise PrivateResearchBrowserAuditError(
                    f"private development {horizon} result controls differ"
                )
        if is_v3:
            primary_counts = projection.get("forecast_primary_status_counts")
            queue = projection.get("grouped_decision_queue")
            if (
                not isinstance(primary_counts, Mapping)
                or set(primary_counts) != {"H3", "H10", "H17"}
                or not isinstance(queue, Mapping)
                or queue.get("contract")
                != "BUFFALO_PRIVATE_RESEARCH_DECISION_QUEUE_V1"
            ):
                raise PrivateResearchBrowserAuditError(
                    "private V3 primary coverage controls differ"
                )
            for horizon in ("H3", "H10", "H17"):
                primary = primary_counts[horizon]
                if (
                    not isinstance(primary, Mapping)
                    or set(primary)
                    != {
                        "CALCULATED",
                        "BLOCKED",
                        "NOT_APPLICABLE",
                        "NOT_PROCESSED",
                        "numerical_zero",
                    }
                    or any(
                        isinstance(primary.get(key), bool)
                        or not isinstance(primary.get(key), int)
                        or primary[key] < 0
                        for key in primary
                    )
                    or sum(
                        primary[key]
                        for key in (
                            "CALCULATED",
                            "BLOCKED",
                            "NOT_APPLICABLE",
                            "NOT_PROCESSED",
                        )
                    )
                    != coverage_count
                    or primary["numerical_zero"] > primary["CALCULATED"]
                ):
                    raise PrivateResearchBrowserAuditError(
                        f"private V3 {horizon} primary controls differ"
                    )
        expected_stage_refs = [
            "ABC",
            "NET_NEED",
            "CASE_QUANTITY",
            "ECONOMICS",
            "ORDER",
        ]
        if any(
            not isinstance(item, Mapping)
            or set(item.get("scenario_results", {})) != {"H3", "H10", "H17"}
            or item.get("stage_blocker_refs") != expected_stage_refs
            or item.get("question") != ""
            for item in owner_rows
        ):
            raise PrivateResearchBrowserAuditError(
                "private development owner-result inventory differs"
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
            if (
                any(
                    isinstance(name, str) and name in vendor_values
                    for name in row.get("supplier_names", [])
                )
                if is_development
                else isinstance(row.get("supplier_name"), str)
                and row["supplier_name"] in vendor_values
            )
        ),
        None,
    )
    if named_row is None:
        raise PrivateResearchBrowserAuditError(
            "browser vendor filtering requires one named supplier"
        )
    evidence_row = (
        next(
            (
                row
                for row in rows
                if isinstance(row.get("scenario_results"), dict)
                and set(row["scenario_results"]) == {"H3", "H10", "H17"}
                and all(
                    isinstance(result, Mapping)
                    and result.get("status") == "CALCULATED_RESEARCH_ONLY"
                    and isinstance(result.get("selected_model"), str)
                    and isinstance(result.get("confidence"), str)
                    for result in row["scenario_results"].values()
                )
                and isinstance(row.get("stage_status"), dict)
                and isinstance(row.get("sidecar_keys"), list)
            ),
            None,
        )
        if is_development
        else next(
            (
                row
                for row in rows
                if isinstance(row.get("source_occurrence_ref"), str)
                and row["source_occurrence_ref"]
                and isinstance(row.get("unapproved_mapping_evidence"), dict)
                and row["unapproved_mapping_evidence"]
                and isinstance(row.get("unapproved_mapping_blocker_reasons"), list)
                and row["unapproved_mapping_blocker_reasons"]
            ),
            None,
        )
    )
    if evidence_row is None:
        raise PrivateResearchBrowserAuditError(
            "browser evidence requires one exact review-result detail"
        )
    detail_row = dict(evidence_row)
    query = str(
        detail_row["shopify_variant_id"]
        if is_development
        else detail_row["source_occurrence_ref"]
    )
    query_rows = filter_private_research_rows(projection, query=query)
    if detail_row not in query_rows:
        raise PrivateResearchBrowserAuditError(
            "browser evidence target is not live-searchable"
        )
    vendor = str(
        named_row["supplier_names"][0]
        if is_development
        else named_row["supplier_name"]
    )
    vendor_rows = filter_private_research_rows(projection, vendor=vendor)
    raw_reasons = detail_row.get(
            "reason_codes" if is_development else "missing_data_reasons"
    )
    status = (
        str(raw_reasons[0])
        if isinstance(raw_reasons, list) and raw_reasons
        else str(
            detail_row.get("stage_status", {}).get("FORECAST", "")
            if is_development and isinstance(detail_row.get("stage_status"), Mapping)
            else detail_row.get("join_status") or ""
        )
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
                "browser_mime_type": str(record.get("media_type") or "")
                .split(";", 1)[0]
                .strip()
                .lower(),
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
    workspace_id = manifest.get("workspace_id")
    projection_sha256 = manifest.get("projection_sha256")
    intake_sha256 = manifest.get("intake_sha256")
    if (
        not isinstance(workspace_id, str)
        or len(workspace_id) != 64
        or not isinstance(projection_sha256, str)
        or len(projection_sha256) != 64
    ):
        raise PrivateResearchBrowserAuditError("workspace identity hashes differ")
    workspace_hashes = {
        "workspace_id": workspace_id,
        "projection_sha256": projection_sha256,
        "manifest_semantic_sha256": _json_sha256(manifest),
        "projection_semantic_sha256": _json_sha256(projection),
    }
    if isinstance(intake_sha256, str) and len(intake_sha256) == 64:
        workspace_hashes["intake_sha256"] = intake_sha256
    return {
        "contract": CONTRACT,
        "manifest": manifest,
        "projection": projection,
        "semantic_hashes": {
            "manifest_sha256": _json_sha256(manifest),
            "projection_sha256": _json_sha256(projection),
        },
        "workspace_hashes": workspace_hashes,
        "route_table": _private_app_route_table(),
        "assertion_ids": list(ASSERTION_IDS),
        "counts": counts,
        "page_markers": (
            [
                "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
                "H3/H10/H17 ASSUMPTIONS",
                (
                    "V3 additive research source, exact-ID existence evidence, policy, and 138-day coverage"
                    if is_v3
                    else "V2 research source, policy, and 138-day coverage"
                ),
                str(research.get("history", {}).get("start_date", "")),
                str(research.get("history", {}).get("end_date", "")),
                *(
                    [
                        "Primary per-horizon coverage outcomes",
                        "Grouped decision queue",
                        "MACHINE_FIXABLE_DEFECTS",
                        "RETRIEVABLE_MISSING_SOURCES",
                        "GENUINELY_OWNER_SPECIFIC_UNANSWERED_FACTS",
                        "FUTURE_RELEASE_AUTHORITY",
                        "Unapproved supplier/offer hypotheses",
                    ]
                    if is_v3
                    else []
                ),
            ]
            if is_development
            else []
        ),
        "initial": {
            "total": len(rows),
            "first_page_rows": [dict(row) for row in rows[:50]],
            "detail_row": detail_row,
        },
        "evidence": (
            {
                "marker": detail_row["shopify_variant_id"],
                "identity_field": "shopify_variant_id",
                "identity_value": detail_row["shopify_variant_id"],
                "required_fields": [
                    "scenario_results",
                    "recorded_sales_coverage",
                    "captured_stock_provenance",
                    "stage_status",
                    "stage_blocker_refs",
                    "sidecar_keys",
                    "reason_codes",
                    *(
                        [
                            "existence_basis",
                            "recent_observed_sales",
                            "next_missing_stage",
                        ]
                        if is_v3
                        else []
                    ),
                ],
                "bound_field": "scenario_results",
                "bound_evidence": detail_row["scenario_results"],
                "reason_field": "reason_codes",
                "reason_container_field": "reason_codes",
                "reason_codes": detail_row["reason_codes"],
                "visible_markers": [
                    "Recorded sales coverage:",
                    "Captured stock provenance:",
                    "Stage status:",
                    *(
                        [
                            "Existence basis:",
                            "Recent recorded sales:",
                            "Next missing stage:",
                        ]
                        if is_v3
                        else []
                    ),
                    *[
                        marker
                        for scenario_id in ("H3", "H10", "H17")
                        for marker in (
                            f"{scenario_id}:",
                            *(
                                [
                                    str(
                                        detail_row["scenario_results"][
                                            scenario_id
                                        ]["primary_status"]
                                    )
                                ]
                                if is_v3
                                else []
                            ),
                            str(
                                detail_row["scenario_results"][scenario_id][
                                    "selected_model"
                                ]
                            ),
                            str(
                                detail_row["scenario_results"][scenario_id][
                                    "point_forecast_units"
                                ]
                            ),
                            str(
                                detail_row["scenario_results"][scenario_id][
                                    "confidence"
                                ]
                            ),
                        )
                    ],
                    str(detail_row["recorded_sales_coverage"]["status"]),
                    str(detail_row["captured_stock_provenance"]["status"]),
                ],
            }
            if is_development
            else {
                "marker": detail_row["source_occurrence_ref"],
                "identity_field": "source_occurrence_ref",
                "identity_value": detail_row["source_occurrence_ref"],
                "required_fields": [
                    "source_ref",
                    "forecast",
                    "abc",
                    "economics",
                    "missing_data_reasons",
                ],
                "bound_field": "unapproved_mapping_evidence",
                "bound_evidence": detail_row["unapproved_mapping_evidence"],
                "reason_field": "unapproved_mapping_blocker_reasons",
                "reason_container_field": "missing_data_reasons",
                "reason_codes": detail_row[
                    "unapproved_mapping_blocker_reasons"
                ],
                "visible_markers": [],
            }
        ),
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


def _minimal_environment(
    *,
    path: str,
    home: Path | None = None,
    process_token: str | None = None,
) -> dict[str, str]:
    result = {
        "PATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(SRC_ROOT),
    }
    if home is not None:
        result["HOME"] = str(home)
    if process_token is not None:
        if not process_token or "\x00" in process_token or "=" in process_token:
            raise PrivateResearchBrowserAuditError(
                "owned process token shape differs"
            )
        result[_PROCESS_TOKEN_ENV] = process_token
    return result


def _node_runtime_info(node: str) -> dict[str, str]:
    probe = (
        "const out={version:process.version,fetch:typeof fetch,"
        "websocket:typeof WebSocket,structuredClone:typeof structuredClone};"
        "process.stdout.write(JSON.stringify(out));"
    )
    try:
        completed = subprocess.run(
            (node, "-e", probe),
            cwd=REPO_ROOT,
            env=_minimal_environment(path=str(Path(node).parent)),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = json.loads(completed.stdout)
        major = int(str(result.get("version", "")).removeprefix("v").split(".", 1)[0])
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise PrivateResearchBrowserAuditError(
            "Node.js browser prerequisites are unavailable"
        ) from exc
    if (
        completed.returncode != 0
        or major < 22
        or result.get("fetch") != "function"
        or result.get("websocket") != "function"
        or result.get("structuredClone") != "function"
    ):
        raise PrivateResearchBrowserAuditError(
            "Node.js 22+ with fetch and WebSocket is required"
        )
    return {"version": str(result["version"]), "major": str(major)}


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
    expected_source: Mapping[str, str],
) -> tuple[subprocess.Popen[bytes], Any, dict[str, str]]:
    log_handle = log_path.open("ab", buffering=0)
    process_token = _new_process_token("viewer")
    server_environment = _minimal_environment(
        path=str(_TRUSTED_GIT.parent), process_token=process_token
    )
    server_environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    process: subprocess.Popen[bytes] | None = None
    baseline_owner_children = _direct_child_identities(os.getpid())
    try:
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
            env=server_environment,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )
        _register_owned_process(
            process,
            token=process_token,
            label="viewer",
            baseline_owner_children=baseline_owner_children,
        )
    except BaseException:
        if process is None:
            log_handle.close()
        else:
            _cleanup_failed_owned_launch(
                process,
                log_handle,
                token=process_token,
                label="viewer",
            )
        raise
    try:
        _wait_health(process, port)
        record = _viewer_pid_record(runtime_root)
        if record is None:
            raise PrivateResearchBrowserAuditError(
                "private viewer process identity is unavailable"
            )
        source_identity = {
            "commit": str(record["source_commit"]),
            "tree": str(record["source_tree"]),
        }
        if source_identity != expected_source:
            raise PrivateResearchBrowserAuditError(
                "private viewer launcher source identity differs"
            )
        _register_owned_identity(
            _owned_registry(process),
            pid=int(record["pid"]),
            start_ticks=int(record["process_start_ticks"]),
        )
    except BaseException:
        _stop_server(process, log_handle, runtime_root)
        raise
    return (
        process,
        log_handle,
        source_identity,
    )


def _new_process_token(label: str) -> str:
    return f"buffalo-private-browser:{label}:{os.getpid()}:{secrets.token_hex(16)}"


def _enable_child_subreaper() -> None:
    global _SUBREAPER_ENABLED
    if _SUBREAPER_ENABLED:
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
    except (AttributeError, OSError) as exc:
        raise PrivateResearchBrowserAuditError(
            "owned process subreaper is unavailable"
        ) from exc
    if result != 0:
        errno_value = ctypes.get_errno()
        raise PrivateResearchBrowserAuditError(
            f"owned process subreaper failed with errno {errno_value}"
        )
    _SUBREAPER_ENABLED = True


def _process_identity(pid: int) -> tuple[int, str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        remainder = raw[raw.rindex(") ") + 2 :].split()
        return int(remainder[19]), remainder[0]
    except (OSError, ValueError, IndexError):
        return None


def _process_session_id(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        remainder = raw[raw.rindex(") ") + 2 :].split()
        return int(remainder[3])
    except (OSError, ValueError, IndexError):
        return None


def _process_parent_id(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        remainder = raw[raw.rindex(") ") + 2 :].split()
        return int(remainder[1])
    except (OSError, ValueError, IndexError):
        return None


def _direct_child_identities(parent_pid: int) -> dict[int, int]:
    children: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat(follow_symlinks=False).st_uid != os.getuid():
                continue
            pid = int(entry.name)
            if _process_parent_id(pid) != parent_pid:
                continue
            identity = _process_identity(pid)
        except (OSError, ValueError):
            continue
        if identity is not None:
            children[pid] = identity[0]
    return children


def _register_owned_identity(
    registry: _OwnedProcessRegistry, *, pid: int, start_ticks: int
) -> None:
    identity = _process_identity(pid)
    if identity is None or identity[0] != start_ticks:
        raise PrivateResearchBrowserAuditError(
            f"{registry.label} child process identity differs"
        )
    registry.identities[pid] = start_ticks


def _register_owned_process(
    process: subprocess.Popen[bytes],
    *,
    token: str,
    label: str,
    baseline_owner_children: dict[int, int] | None = None,
) -> _OwnedProcessRegistry:
    identity = _process_identity(process.pid)
    if identity is None:
        raise PrivateResearchBrowserAuditError(
            f"{label} process identity is unavailable"
        )
    registry = _OwnedProcessRegistry(
        token=token,
        label=label,
        leader_pid=process.pid,
        leader_start_ticks=identity[0],
        session_id=process.pid,
        identities={process.pid: identity[0]},
        baseline_owner_children=baseline_owner_children,
    )
    setattr(process, "_buffalo_private_browser_registry", registry)
    _discover_owned_processes(registry)
    return registry


def _fallback_owned_registry(
    process: subprocess.Popen[bytes], *, token: str, label: str
) -> _OwnedProcessRegistry:
    identity = _process_identity(process.pid)
    registry = _OwnedProcessRegistry(
        token=token,
        label=label,
        leader_pid=process.pid,
        leader_start_ticks=identity[0] if identity is not None else -1,
        session_id=process.pid,
        identities={process.pid: identity[0]} if identity is not None else {},
        baseline_owner_children={},
    )
    setattr(process, "_buffalo_private_browser_registry", registry)
    _discover_owned_processes(registry)
    return registry


def _cleanup_failed_owned_launch(
    process: subprocess.Popen[bytes],
    handle: Any,
    *,
    token: str,
    label: str,
) -> None:
    try:
        registry = getattr(process, "_buffalo_private_browser_registry", None)
        if not isinstance(registry, _OwnedProcessRegistry):
            _fallback_owned_registry(process, token=token, label=label)
        _stop_process_group(process, timeout=5)
    finally:
        handle.close()


def _owned_registry(process: subprocess.Popen[bytes]) -> _OwnedProcessRegistry:
    registry = getattr(process, "_buffalo_private_browser_registry", None)
    if not isinstance(registry, _OwnedProcessRegistry):
        raise PrivateResearchBrowserAuditError(
            "owned process registry is unavailable"
        )
    return registry


def _discover_owned_processes(
    registry: _OwnedProcessRegistry,
) -> dict[int, tuple[int, str]]:
    marker = f"{_PROCESS_TOKEN_ENV}={registry.token}".encode("utf-8")
    snapshots: dict[int, tuple[int, str, int | None, int | None, bool]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            info = entry.stat(follow_symlinks=False)
            if info.st_uid != os.getuid():
                continue
            pid = int(entry.name)
            before = _process_identity(pid)
            if before is None:
                continue
            session_id = _process_session_id(pid)
            parent_id = _process_parent_id(pid)
            has_marker = False
            if session_id != registry.session_id:
                environment = (entry / "environ").read_bytes().split(b"\0")
                has_marker = marker in environment
            identity = _process_identity(pid)
        except (OSError, ValueError):
            continue
        if identity is not None and identity[0] == before[0]:
            snapshots[pid] = (
                identity[0],
                identity[1],
                parent_id,
                session_id,
                has_marker,
            )

    owned = {
        pid
        for pid, snapshot in snapshots.items()
        if (
            pid in registry.identities
            or snapshot[3] == registry.session_id
            or snapshot[4]
        )
    }
    # Tokenless descendants are still owned through process ancestry. If an
    # owned parent exits, PR_SET_CHILD_SUBREAPER reparents its descendants to
    # this harness; new direct children created after the leader are included,
    # while the pre-launch baseline excludes unrelated pre-existing children.
    changed = True
    while changed:
        changed = False
        for pid, snapshot in snapshots.items():
            if pid not in owned and snapshot[2] in owned:
                owned.add(pid)
                changed = True
    for pid, snapshot in snapshots.items():
        if (
            pid not in owned
            and snapshot[2] == registry.owner_pid
            and pid not in registry.baseline_owner_children
            and snapshot[0] >= registry.leader_start_ticks
        ):
            owned.add(pid)
    for pid in owned:
        registry.identities[pid] = snapshots[pid][0]

    live: dict[int, tuple[int, str]] = {}
    for pid, expected_start in tuple(registry.identities.items()):
        identity = _process_identity(pid)
        if identity is None or identity[0] != expected_start:
            registry.identities.pop(pid, None)
            continue
        live[pid] = identity
    return live


def _reap_owned_processes(
    process: subprocess.Popen[bytes], registry: _OwnedProcessRegistry
) -> None:
    process.poll()
    for pid in tuple(registry.identities):
        if pid == process.pid:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, ProcessLookupError):
            pass


def _signal_owned_processes(
    registry: _OwnedProcessRegistry,
    signum: int,
    *,
    signalled: set[tuple[int, int]],
) -> None:
    for pid, (start_ticks, state) in _discover_owned_processes(registry).items():
        process_identity = (pid, start_ticks)
        if process_identity in signalled:
            continue
        current = _process_identity(pid)
        if state == "Z" or current is None or current[0] != start_ticks:
            continue
        try:
            os.kill(pid, signum)
            signalled.add(process_identity)
        except ProcessLookupError:
            pass


def _viewer_pid_record(runtime_root: Path) -> dict[str, Any] | None:
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
        if (
            not isinstance(record["source_commit"], str)
            or _HEX40.fullmatch(record["source_commit"]) is None
            or not isinstance(record["source_tree"], str)
            or _HEX40.fullmatch(record["source_tree"]) is None
        ):
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
        return record
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, IndexError):
        return None


def _stop_process_group(process: subprocess.Popen[bytes], *, timeout: float) -> None:
    # The environment token survives setsid()/double-fork.  Repeated discovery
    # closes the fork-vs-scan race, while start ticks prevent signalling PID reuse.
    registry = _owned_registry(process)
    terminated: set[tuple[int, int]] = set()
    _signal_owned_processes(registry, signal.SIGTERM, signalled=terminated)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _reap_owned_processes(process, registry)
        live = _discover_owned_processes(registry)
        if not live:
            break
        _signal_owned_processes(registry, signal.SIGTERM, signalled=terminated)
        time.sleep(0.05)
    if _discover_owned_processes(registry):
        killed: set[tuple[int, int]] = set()
        _signal_owned_processes(registry, signal.SIGKILL, signalled=killed)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _reap_owned_processes(process, registry)
            live = _discover_owned_processes(registry)
            if not live:
                break
            _signal_owned_processes(registry, signal.SIGKILL, signalled=killed)
            time.sleep(0.05)
    _reap_owned_processes(process, registry)
    survivors = _discover_owned_processes(registry)
    if survivors:
        raise PrivateResearchBrowserAuditError(
            f"{registry.label} owned process descendants survived cleanup"
        )
    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired as exc:
            raise PrivateResearchBrowserAuditError(
                f"{registry.label} process leader survived cleanup"
            ) from exc


def _stop_server(
    process: subprocess.Popen[bytes], handle: Any, runtime_root: Path
) -> None:
    try:
        _stop_process_group(process, timeout=30)
    finally:
        handle.close()
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
    process_token = _new_process_token("chromium")
    process: subprocess.Popen[bytes] | None = None
    baseline_owner_children = _direct_child_identities(os.getpid())
    try:
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
            env=_minimal_environment(
                path=str(Path(chromium).parent),
                home=profile.parent,
                process_token=process_token,
            ),
            stdout=subprocess.DEVNULL,
            stderr=handle,
            start_new_session=True,
        )
        _register_owned_process(
            process,
            token=process_token,
            label="Chromium",
            baseline_owner_children=baseline_owner_children,
        )
    except BaseException:
        if process is None:
            handle.close()
        else:
            _cleanup_failed_owned_launch(
                process,
                handle,
                token=process_token,
                label="Chromium",
            )
        raise
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
    assertions = result.get("assertions")
    assertion_shape = isinstance(assertions, list) and all(
        isinstance(item, dict)
        and set(item) == {"id", "passed"}
        and isinstance(item.get("id"), str)
        and item.get("passed") is True
        for item in assertions
    )
    assertion_ids = [item["id"] for item in assertions] if assertion_shape else []
    tooling = result.get("tooling")
    browser = tooling.get("browser") if isinstance(tooling, dict) else None
    expected_tooling = expectations.get("tooling")
    target_summary = result.get("target_summary")
    target_shape = (
        isinstance(target_summary, dict)
        and set(target_summary)
        == {
            "all_observed_types",
            "observed_types",
            "by_type",
            "inert",
            "unsupported",
            "tracked",
            "created",
            "destroyed",
            "detached",
            "active_guarded",
            "live_detached",
            "attached",
            "guarded",
            "resumed",
            "uncreated",
            "unattached",
            "unguarded",
            "unresumed",
        }
        and isinstance(target_summary.get("all_observed_types"), list)
        and isinstance(target_summary.get("observed_types"), list)
        and isinstance(target_summary.get("by_type"), dict)
        and all(
            isinstance(name, str) and name
            for name in (
                target_summary["observed_types"]
                + target_summary["all_observed_types"]
            )
        )
        and target_summary["observed_types"]
        == sorted(set(target_summary["observed_types"]))
        and target_summary["all_observed_types"]
        == sorted(set(target_summary["all_observed_types"]))
        and set(target_summary["observed_types"]).issubset(
            target_summary["all_observed_types"]
        )
        and set(target_summary["observed_types"]) == set(target_summary["by_type"])
        and all(
            isinstance(name, str)
            and name
            and type(count) is int
            and count >= 1
            for name, count in target_summary["by_type"].items()
        )
        and all(
            type(target_summary.get(field)) is int
            and target_summary[field] >= 0
            for field in (
                "tracked",
                "created",
                "destroyed",
                "detached",
                "active_guarded",
                "live_detached",
                "inert",
                "unsupported",
                "attached",
                "guarded",
                "resumed",
                "uncreated",
                "unattached",
                "unguarded",
                "unresumed",
            )
        )
        and target_summary["tracked"] >= 1
        and sum(target_summary["by_type"].values()) == target_summary["tracked"]
        and target_summary["created"] == target_summary["tracked"]
        and target_summary["attached"] == target_summary["tracked"]
        and target_summary["guarded"] == target_summary["tracked"]
        and target_summary["resumed"] == target_summary["tracked"]
        and target_summary["destroyed"] <= target_summary["tracked"]
        and target_summary["detached"] <= target_summary["tracked"]
        and target_summary["active_guarded"] + target_summary["destroyed"]
        == target_summary["tracked"]
        and target_summary["live_detached"] == 0
        and target_summary["unsupported"] == 0
        and target_summary["uncreated"] == 0
        and target_summary["unattached"] == 0
        and target_summary["unguarded"] == 0
        and target_summary["unresumed"] == 0
    )
    expected_result_keys = {
        "contract",
        "phase",
        "base_url",
        "passed",
        "assertions",
        "semantic_hashes",
        "workspace_hashes",
        "counts",
        "artifact_hashes",
        "tooling",
        "target_summary",
        "external_requests",
        "blocked_external_requests",
        "external_websocket_requests",
        "unexpected_scheme_requests",
        "request_count",
        "response_count",
    }
    if (
        set(result) != expected_result_keys
        or result.get("contract") != CONTRACT
        or result.get("phase") != phase
        or result.get("base_url") != base_url
        or result.get("passed") is not True
        or not assertion_shape
        or sorted(assertion_ids) != sorted(expectations["assertion_ids"])
        or len(assertion_ids) != len(set(assertion_ids))
        or result.get("external_requests") != []
        or result.get("blocked_external_requests") != []
        or result.get("external_websocket_requests") != []
        or result.get("unexpected_scheme_requests") != []
        or result.get("artifact_hashes") != expected_hashes
        or result.get("semantic_hashes") != expectations["semantic_hashes"]
        or result.get("workspace_hashes") != expectations["workspace_hashes"]
        or result.get("counts") != expectations["counts"]
        or not isinstance(expected_tooling, dict)
        or not isinstance(tooling, dict)
        or set(tooling) != {"node_version", "script_sha256", "browser"}
        or tooling.get("script_sha256") != expected_tooling.get("script_sha256")
        or tooling.get("node_version") != expected_tooling.get("node_version")
        or not isinstance(browser, dict)
        or set(browser) != {"product", "protocol_version", "js_version"}
        or not all(isinstance(value, str) and value for value in browser.values())
        or not target_shape
        or type(result.get("request_count")) is not int
        or result["request_count"] < 1
        or type(result.get("response_count")) is not int
        or result["response_count"] < 1
    ):
        raise PrivateResearchBrowserAuditError(
            f"{phase} browser acceptance result differs"
        )
    return {
        "assertions": len(assertions),
        "request_count": result.get("request_count"),
        "response_count": result.get("response_count"),
        "artifact_hashes": expected_hashes,
        "browser": browser,
        "target_summary": target_summary,
    }


def _purge_evidence_root(evidence_root: Path) -> None:
    if not evidence_root.exists() and not evidence_root.is_symlink():
        return
    try:
        info = evidence_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrivateResearchBrowserAuditError(
            "contaminated evidence root is unavailable"
        ) from exc
    if (
        evidence_root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PrivateResearchBrowserAuditError(
            "contaminated evidence root identity differs"
        )
    try:
        shutil.rmtree(evidence_root)
    except OSError as exc:
        raise PrivateResearchBrowserAuditError(
            "contaminated evidence could not be removed"
        ) from exc
    if evidence_root.exists() or evidence_root.is_symlink():
        raise PrivateResearchBrowserAuditError(
            "contaminated evidence survived removal"
        )


def _assert_no_auth_material(evidence_root: Path, secret: bytes) -> None:
    credential = base64.b64encode(b"private:" + secret)
    forbidden = (
        secret,
        credential,
        b"Basic " + credential,
        _sha256(secret).encode("ascii"),
        _sha256(b"private:" + secret).encode("ascii"),
    )
    contaminated = False
    try:
        for path in evidence_root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            data = path.read_bytes()
            if any(value in data for value in forbidden):
                contaminated = True
                break
    except OSError:
        contaminated = True
    if contaminated:
        _purge_evidence_root(evidence_root)
        raise PrivateResearchBrowserAuditError(
            "authentication material reached retained browser evidence; "
            "the fresh evidence root was removed"
        )


def _finalize_acceptance_evidence(
    *,
    evidence_root: Path,
    result: Mapping[str, Any],
    secret: bytes,
    expected_source: Mapping[str, Any],
) -> None:
    try:
        _write_private_json(evidence_root / "acceptance-result.json", result)
        if secret:
            _assert_no_auth_material(evidence_root, secret)
        _assert_repository_source_identity(expected_source)
    except BaseException:
        if evidence_root.exists() or evidence_root.is_symlink():
            _purge_evidence_root(evidence_root)
        raise


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
    _enable_child_subreaper()
    expected_source = _repository_source_identity()
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
    node_runtime = _node_runtime_info(node)
    expectations["tooling"] = {
        "node_version": node_runtime["version"],
        "script_sha256": _sha256(SCRIPT.read_bytes()),
    }
    _assert_repository_source_identity(expected_source)

    _prepare_empty_root(evidence_root, label="evidence root")
    _prepare_empty_root(runtime_root, label="runtime root")
    downloads_root = evidence_root / "downloads"
    downloads_root.mkdir(mode=0o700)
    initial_downloads = downloads_root / "initial"
    restart_downloads = downloads_root / "restart"
    initial_downloads.mkdir(mode=0o700)
    restart_downloads.mkdir(mode=0o700)
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
    source_identities: list[dict[str, str]] = []
    stopped_servers = 0
    stopped_browsers = 0
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
        state_path = temporary_root / "restart-state.json"
        server_log = temporary_root / "viewer.log"
        server_log.touch(mode=0o600)
        for phase, downloads in (
            ("initial", initial_downloads),
            ("restart", restart_downloads),
        ):
            _assert_repository_source_identity(expected_source)
            server, server_handle, source_identity = _start_server(
                workspace_root,
                runtime_root,
                app_port,
                server_log,
                expected_source,
            )
            source_identities.append(source_identity)
            try:
                profile = temporary_root / f"chromium-profile-{phase}"
                chromium_log = temporary_root / f"chromium-{phase}.stderr"
                browser, browser_handle = _start_browser(
                    chromium, profile, cdp_port, chromium_log
                )
                try:
                    cdp_endpoint = _wait_cdp(browser, cdp_port)
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
                finally:
                    if browser is not None and browser_handle is not None:
                        try:
                            _stop_browser(browser, browser_handle)
                            stopped_browsers += 1
                        finally:
                            browser = None
                            browser_handle = None
                    _assert_port_free(cdp_port, label="Chromium CDP")
            finally:
                if server is not None and server_handle is not None:
                    try:
                        _stop_server(server, server_handle, runtime_root)
                        stopped_servers += 1
                    finally:
                        server = None
                        server_handle = None
                _assert_port_free(app_port, label="private viewer")
    finally:
        if browser is not None and browser_handle is not None:
            try:
                _stop_browser(browser, browser_handle)
            except BaseException as exc:
                cleanup_error = exc
        if server is not None and server_handle is not None:
            try:
                _stop_server(server, server_handle, runtime_root)
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

    _assert_repository_source_identity(expected_source)

    if set(phase_results) != {"initial", "restart"}:
        raise PrivateResearchBrowserAuditError("browser acceptance phases are incomplete")
    if (
        phase_results["initial"]["artifact_hashes"]
        != phase_results["restart"]["artifact_hashes"]
        or phase_results["initial"]["browser"]
        != phase_results["restart"]["browser"]
    ):
        raise PrivateResearchBrowserAuditError("artifact hashes changed across restart")
    if (
        len(source_identities) != 2
        or source_identities[0] != source_identities[1]
    ):
        raise PrivateResearchBrowserAuditError("viewer source identity changed across restart")
    if stopped_servers != 2 or stopped_browsers != 2:
        raise PrivateResearchBrowserAuditError("process cleanup proof is incomplete")
    result = {
        "contract": CONTRACT,
        "workspace_root": str(workspace_root),
        "workspace_id": expectations["manifest"]["workspace_id"],
        "origin": base_url,
        "evidence_root": str(evidence_root),
        "source": expected_source,
        "route_table": expectations["route_table"],
        "workspace_hashes": expectations["workspace_hashes"],
        "semantic_hashes": expectations["semantic_hashes"],
        "counts": expectations["counts"],
        "artifact_hashes": phase_results["initial"]["artifact_hashes"],
        "tooling": {
            **expectations["tooling"],
            "browser": phase_results["initial"]["browser"],
        },
        "phases": phase_results,
        "cleanup": {
            "server_process_sessions_stopped": stopped_servers,
            "browser_process_sessions_stopped": stopped_browsers,
            "server_owned_process_trees_verified_empty": stopped_servers,
            "browser_owned_process_trees_verified_empty": stopped_browsers,
            "private_listener_stopped": True,
            "cdp_listener_stopped": True,
            "runtime_root_removed": not runtime_root.exists(),
            "authentication_material_absent": True,
            "source_revalidated_after_cleanup": True,
        },
        "operational_authority": False,
    }
    _finalize_acceptance_evidence(
        evidence_root=evidence_root,
        result=result,
        secret=secret,
        expected_source=expected_source,
    )
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
