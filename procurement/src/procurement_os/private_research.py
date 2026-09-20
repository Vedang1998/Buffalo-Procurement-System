"""Sealed private real-source research workspace composition.

The workspace is a read-only, zero-authority derivative of an immutable intake.
It is physically separate from operational procurement storage and has no database
or commerce integration.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from .private_research_intake import (
    intake_manifest_key,
    read_private_research_intake,
    validate_private_root,
)
from .private_research_projection import (
    PROJECTION_CONTRACT,
    build_private_research_projection,
    render_private_research_csv,
    render_private_research_html,
)
from .storage import LocalFilesystemStorage


CONTRACT = "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V1"
AUTHORITY = "PRIVATE_REAL_SOURCE_REVIEW_ONLY"
DATA_MODE = "PRIVATE_REAL_SOURCE_REVIEW"
OPERATIONAL_AUTHORITY = False
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_MEDIA_TYPES = {
    "coverage.json": "application/json",
    "owner-preview.html": "text/html; charset=utf-8",
    "owner-worksheet.csv": "text/csv; charset=utf-8",
    "projection.json": "application/json",
}
_ZERO_AUTHORITY = {
    "commercial_authority": False,
    "mapping_approval": False,
    "price_approval": False,
    "forecast_policy_approval": False,
    "draft_or_po_authority": False,
    "shopify_write_authority": False,
}


class PrivateResearchError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifact_record(name: str, data: bytes) -> dict[str, Any]:
    return {
        "name": name,
        "path": name,
        "bytes": len(data),
        "sha256": _sha256(data),
        "media_type": _ARTIFACT_MEDIA_TYPES[name],
    }


def _put_once_or_verify(
    storage: LocalFilesystemStorage, key: str, data: bytes
) -> None:
    if storage.exists(key):
        if storage.get_bytes(key) != data:
            raise PrivateResearchError("private research immutable artifact differs")
        return
    try:
        storage.put_bytes_once(key, data)
    except FileExistsError:
        if storage.get_bytes(key) != data:
            raise PrivateResearchError("private research immutable artifact differs")


def _limitations(projection: Mapping[str, Any]) -> list[str]:
    value = projection.get("limitations", [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise PrivateResearchError("private research limitations differ")
    return sorted(set(value))


def _exact_zero_authority(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(_ZERO_AUTHORITY)
        and all(type(value[key]) is bool and value[key] is False for key in value)
    )


def _coverage_export(projection: Mapping[str, Any]) -> dict[str, Any]:
    coverage = projection.get("coverage")
    if coverage is None:
        coverage = projection.get("coverage_rows")
    return {
        "contract": "BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V1",
        "authority": AUTHORITY,
        "operational_authority": False,
        "projection_sha256": projection.get("projection_sha256"),
        "coverage": coverage,
    }


def _workspace_identity(intake: Mapping[str, Any], projection: Mapping[str, Any]) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "contract": CONTRACT,
                "intake_id": intake.get("intake_id"),
                "intake_sha256": _sha256(_canonical_bytes(intake)),
                "projection_contract": PROJECTION_CONTRACT,
                "projection_sha256": projection.get("projection_sha256"),
            }
        )
    )


def build_private_research_workspace(
    private_root: str | Path,
    intake_id: str,
) -> dict[str, Any]:
    """Build or exactly replay a sealed workspace from one validated intake."""

    try:
        root = validate_private_root(Path(private_root))
        intake = read_private_research_intake(root, intake_id)
        projection = build_private_research_projection(intake)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private research inputs are invalid") from exc
    if not isinstance(intake, dict) or not isinstance(projection, dict):
        raise PrivateResearchError("private research input shape differs")
    projection_sha = projection.get("projection_sha256")
    if not isinstance(projection_sha, str) or not _HEX64.fullmatch(projection_sha):
        raise PrivateResearchError("private research projection identity differs")
    workspace_id = _workspace_identity(intake, projection)
    prefix = f"private-research/workspaces/{workspace_id}"
    workspace_path = root / prefix
    workspace_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (
        root / "private-research",
        root / "private-research" / "workspaces",
        workspace_path,
    ):
        directory.chmod(0o700)
    storage = LocalFilesystemStorage(root)
    manifest_key = f"{prefix}/manifest.json"
    if storage.exists(manifest_key):
        return read_private_research_workspace(root / prefix)["manifest"]
    artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    artifact_records: list[dict[str, Any]] = []
    for name in sorted(artifacts):
        data = artifacts[name]
        key = f"{prefix}/{name}"
        _put_once_or_verify(storage, key, data)
        artifact_records.append(_artifact_record(name, data))
    manifest: dict[str, Any] = {
        "contract": CONTRACT,
        "data_mode": DATA_MODE,
        "authority": AUTHORITY,
        "operational_authority": False,
        "workspace_id": workspace_id,
        "intake_id": intake_id,
        "intake_manifest_key": intake_manifest_key(intake_id),
        "intake_sha256": _sha256(_canonical_bytes(intake)),
        "projection_contract": PROJECTION_CONTRACT,
        "projection_sha256": projection_sha,
        "artifacts": artifact_records,
        "limitations": _limitations(projection),
        "zero_authority": dict(_ZERO_AUTHORITY),
    }
    try:
        storage.put_bytes_once(manifest_key, _canonical_bytes(manifest))
    except FileExistsError:
        return read_private_research_workspace(root / prefix)["manifest"]
    return read_private_research_workspace(root / prefix)["manifest"]


def _validate_workspace_root(path: Path) -> tuple[Path, str]:
    if not path.is_absolute() or path.is_symlink():
        raise PrivateResearchError("private research workspace path differs")
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PrivateResearchError("private research workspace is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PrivateResearchError("private research workspace ownership differs")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise PrivateResearchError("private research workspace mode differs")
    if (
        path.parent.name != "workspaces"
        or path.parent.parent.name != "private-research"
        or not _HEX64.fullmatch(path.name)
    ):
        raise PrivateResearchError("private research workspace location differs")
    return path.parent.parent.parent, path.name


def read_private_research_workspace(workspace_root: str | Path) -> dict[str, Any]:
    """Rehash and semantically rebuild one immutable research workspace."""

    path = Path(workspace_root)
    private_root, expected_workspace_id = _validate_workspace_root(path)
    expected_names = set(_ARTIFACT_MEDIA_TYPES) | {"manifest.json"}
    try:
        entries = list(path.iterdir())
    except OSError as exc:
        raise PrivateResearchError("private research workspace is unavailable") from exc
    if {entry.name for entry in entries} != expected_names:
        raise PrivateResearchError("private research workspace inventory differs")
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise PrivateResearchError(
                "private research workspace inventory is unavailable"
            ) from exc
        if (
            entry.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise PrivateResearchError("private research workspace object differs")
    try:
        root = validate_private_root(private_root)
        storage = LocalFilesystemStorage(root)
        prefix = f"private-research/workspaces/{expected_workspace_id}"
        manifest_bytes = storage.get_bytes(f"{prefix}/manifest.json")
        manifest = json.loads(manifest_bytes)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private research manifest is unreadable") from exc
    expected_keys = {
        "contract",
        "data_mode",
        "authority",
        "operational_authority",
        "workspace_id",
        "intake_id",
        "intake_manifest_key",
        "intake_sha256",
        "projection_contract",
        "projection_sha256",
        "artifacts",
        "limitations",
        "zero_authority",
    }
    try:
        expected_intake_key = intake_manifest_key(
            str(manifest.get("intake_id", "")) if isinstance(manifest, dict) else ""
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private research manifest contract differs") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != expected_keys
        or manifest.get("contract") != CONTRACT
        or manifest.get("data_mode") != DATA_MODE
        or manifest.get("authority") != AUTHORITY
        or manifest.get("operational_authority") is not False
        or manifest.get("workspace_id") != expected_workspace_id
        or manifest.get("projection_contract") != PROJECTION_CONTRACT
        or manifest.get("intake_manifest_key") != expected_intake_key
        or not _exact_zero_authority(manifest.get("zero_authority"))
        or _canonical_bytes(manifest) != manifest_bytes
    ):
        raise PrivateResearchError("private research manifest contract differs")
    try:
        intake = read_private_research_intake(root, str(manifest["intake_id"]))
        projection = build_private_research_projection(intake)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private research semantic replay failed") from exc
    if (
        manifest.get("intake_sha256") != _sha256(_canonical_bytes(intake))
        or manifest.get("projection_sha256") != projection.get("projection_sha256")
        or expected_workspace_id != _workspace_identity(intake, projection)
        or manifest.get("limitations") != _limitations(projection)
    ):
        raise PrivateResearchError("private research source binding differs")
    expected_artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    records = manifest.get("artifacts")
    if not isinstance(records, list) or len(records) != len(expected_artifacts):
        raise PrivateResearchError("private research artifact inventory differs")
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "name", "path", "bytes", "sha256", "media_type"
        }:
            raise PrivateResearchError("private research artifact record differs")
        name = record.get("name")
        if not isinstance(name, str) or name in by_name or record.get("path") != name:
            raise PrivateResearchError("private research artifact name differs")
        by_name[name] = record
    if set(by_name) != set(expected_artifacts):
        raise PrivateResearchError("private research artifact set differs")
    loaded: dict[str, bytes] = {}
    for name, expected in expected_artifacts.items():
        record = by_name[name]
        try:
            data = storage.get_bytes(f"{prefix}/{name}")
        except OSError as exc:
            raise PrivateResearchError("private research artifact is unavailable") from exc
        if (
            data != expected
            or record != _artifact_record(name, data)
        ):
            raise PrivateResearchError("private research artifact integrity differs")
        loaded[name] = data
    return {"manifest": manifest, "projection": projection, "artifacts": loaded}
