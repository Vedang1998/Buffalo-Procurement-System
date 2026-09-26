"""Sealed private real-source research workspace composition.

The workspace is a read-only, zero-authority derivative of an immutable intake.
It is physically separate from operational procurement storage and has no database
or commerce integration.
"""
from __future__ import annotations

import gc
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
    private_research_projection_sha256,
    render_private_research_csv,
    render_private_research_html,
)
from .storage import LocalFilesystemStorage


CONTRACT = "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V1"
V2_CONTRACT = "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V2"
V3_CONTRACT = "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3"
V3_CORRECTED_CONTRACT = "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3_CORRECTED_V1"
AUTHORITY = "PRIVATE_REAL_SOURCE_REVIEW_ONLY"
DATA_MODE = "PRIVATE_REAL_SOURCE_REVIEW"
V2_DATA_MODE = "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY"
V2_PROJECTION_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2"
V3_PROJECTION_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3"
V3_CORRECTED_PROJECTION_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3_CORRECTED_V1"
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
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PrivateResearchError(
            "private research canonical bytes differ"
        ) from exc


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
    contract = projection.get("contract")
    expected_modes = {
        PROJECTION_CONTRACT: "PRIVATE_REAL_DATA_RESEARCH_ONLY",
        V2_PROJECTION_CONTRACT: V2_DATA_MODE,
        V3_PROJECTION_CONTRACT: V2_DATA_MODE,
        V3_CORRECTED_PROJECTION_CONTRACT: V2_DATA_MODE,
    }
    expected_mode = expected_modes.get(contract)
    if expected_mode is None or projection.get("data_mode") != expected_mode:
        raise PrivateResearchError("private research coverage projection tuple differs")
    try:
        private_research_projection_sha256(projection)
    except Exception as exc:
        if isinstance(exc, MemoryError):
            raise
        raise PrivateResearchError(
            "private research coverage projection differs"
        ) from exc
    coverage = projection.get("coverage")
    if coverage is None:
        coverage = projection.get("coverage_rows")
    return {
        "contract": (
            "BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V3_CORRECTED_V1"
            if contract == V3_CORRECTED_PROJECTION_CONTRACT
            else (
            "BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V3"
            if contract == V3_PROJECTION_CONTRACT
            else (
                "BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V2"
                if contract == V2_PROJECTION_CONTRACT
                else "BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V1"
            ))
        ),
        "authority": (
            "ZERO_AUTHORITY_RESEARCH_ONLY"
            if contract == V3_CORRECTED_PROJECTION_CONTRACT
            else AUTHORITY
        ),
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


def _v2_workspace_identity(
    research_input: Mapping[str, Any],
    base_intake: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "contract": V2_CONTRACT,
                "input_id": research_input.get("input_id"),
                "input_sha256": _sha256(_canonical_bytes(research_input)),
                "base_intake_id": base_intake.get("intake_id"),
                "base_intake_sha256": _sha256(_canonical_bytes(base_intake)),
                "projection_contract": V2_PROJECTION_CONTRACT,
                "projection_sha256": projection.get("projection_sha256"),
            }
        )
    )


def _v3_workspace_identity(
    research_input: Mapping[str, Any],
    parent_v2_input: Mapping[str, Any],
    base_intake: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> str:
    return _sha256(
        _canonical_bytes(
            {
                "contract": V3_CONTRACT,
                "input_id": research_input.get("input_id"),
                "input_sha256": _sha256(_canonical_bytes(research_input)),
                "parent_v2_input_id": parent_v2_input.get("input_id"),
                "parent_v2_input_sha256": _sha256(
                    _canonical_bytes(parent_v2_input)
                ),
                "base_intake_id": base_intake.get("intake_id"),
                "base_intake_sha256": _sha256(_canonical_bytes(base_intake)),
                "projection_contract": V3_PROJECTION_CONTRACT,
                "projection_sha256": projection.get("projection_sha256"),
            }
        )
    )


def _v3_corrected_workspace_identity(
    corrected_input: Mapping[str, Any],
    projection: Mapping[str, Any],
    artifact_records: list[dict[str, Any]],
) -> str:
    if (
        not isinstance(artifact_records, list)
        or len(artifact_records) != len(_ARTIFACT_MEDIA_TYPES)
        or any(
            not isinstance(record, dict)
            or set(record) != {"name", "path", "bytes", "sha256", "media_type"}
            or not isinstance(record.get("name"), str)
            or record.get("path") != record.get("name")
            or record.get("name") not in _ARTIFACT_MEDIA_TYPES
            or type(record.get("bytes")) is not int
            or record["bytes"] < 0
            or not isinstance(record.get("sha256"), str)
            or not _HEX64.fullmatch(record["sha256"])
            or record.get("media_type")
            != _ARTIFACT_MEDIA_TYPES[record["name"]]
            for record in artifact_records
        )
        or len({record["name"] for record in artifact_records})
        != len(_ARTIFACT_MEDIA_TYPES)
    ):
        raise PrivateResearchError(
            "private corrected-V3 workspace artifact records differ"
        )
    parent_v3 = corrected_input["parent_v3_input"]
    parent_v2 = corrected_input["parent_v2_input"]
    base = corrected_input["base_intake"]
    parent_projection = corrected_input["parent_projection"]
    delta = corrected_input["creation_evidence_delta"]
    preimage = {
        "contract": V3_CORRECTED_CONTRACT,
        "implementation_lineage": corrected_input["implementation_lineage"],
        "corrected_input": {
            "input_id": corrected_input["input_id"],
            "sha256": _sha256(_canonical_bytes(corrected_input)),
        },
        "parent_v3_input": {
            "input_id": parent_v3["input_id"],
            "sha256": parent_v3["sha256"],
        },
        "parent_v2_input": {
            "input_id": parent_v2["input_id"],
            "sha256": parent_v2["sha256"],
        },
        "base_intake": {
            "intake_id": base["intake_id"],
            "sha256": base["sha256"],
        },
        "parent_projection": {
            "projection_sha256": parent_projection["projection_sha256"],
            "artifact_sha256": parent_projection["artifact_sha256"],
        },
        "creation_evidence_delta": {
            "delta_id": delta["delta_id"],
            "raw_csv_sha256": delta["raw_csv_sha256"],
            "normalized_row_set_sha256": delta["normalized_row_set_sha256"],
        },
        "joint_policy": {
            "policy_canonical_sha256": corrected_input["joint_policy"][
                "policy_canonical_sha256"
            ]
        },
        "corrected_projection": {
            "contract": V3_CORRECTED_PROJECTION_CONTRACT,
            "projection_sha256": projection["projection_sha256"],
        },
        "artifacts": sorted(artifact_records, key=lambda item: item["name"]),
    }
    return _sha256(_canonical_bytes(preimage))


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
    _put_once_or_verify(storage, manifest_key, _canonical_bytes(manifest))
    return read_private_research_workspace_structural(workspace_path)["manifest"]


def build_private_v2_research_workspace(
    private_root: str | Path,
    input_id: str,
) -> dict[str, Any]:
    """Build or exactly replay a sealed workspace from one V2 research input."""

    try:
        from .private_research_v2 import (
            build_private_v2_research_projection,
            input_manifest_key as v2_input_manifest_key,
            read_private_v2_research_bundle,
        )

        root = validate_private_root(Path(private_root))
        research_input, base_intake = read_private_v2_research_bundle(root, input_id)
        base_id = str(research_input["base_intake"]["intake_id"])
        projection = build_private_v2_research_projection(
            research_input, base_intake
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private V2 research inputs are invalid") from exc
    if (
        not isinstance(research_input, dict)
        or not isinstance(base_intake, dict)
        or not isinstance(projection, dict)
        or projection.get("contract") != V2_PROJECTION_CONTRACT
    ):
        raise PrivateResearchError("private V2 research input shape differs")
    projection_sha = projection.get("projection_sha256")
    if not isinstance(projection_sha, str) or not _HEX64.fullmatch(projection_sha):
        raise PrivateResearchError("private V2 research projection identity differs")
    workspace_id = _v2_workspace_identity(research_input, base_intake, projection)
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
    artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    artifact_records: list[dict[str, Any]] = []
    for name in sorted(artifacts):
        data = artifacts[name]
        _put_once_or_verify(storage, f"{prefix}/{name}", data)
        artifact_records.append(_artifact_record(name, data))
    manifest: dict[str, Any] = {
        "contract": V2_CONTRACT,
        "data_mode": V2_DATA_MODE,
        "authority": AUTHORITY,
        "operational_authority": False,
        "workspace_id": workspace_id,
        "intake_id": input_id,
        "intake_manifest_key": v2_input_manifest_key(input_id),
        "intake_sha256": _sha256(_canonical_bytes(research_input)),
        "base_intake_id": base_id,
        "base_intake_manifest_key": intake_manifest_key(base_id),
        "base_intake_sha256": _sha256(_canonical_bytes(base_intake)),
        "projection_contract": V2_PROJECTION_CONTRACT,
        "projection_sha256": projection_sha,
        "artifacts": artifact_records,
        "limitations": _limitations(projection),
        "zero_authority": dict(_ZERO_AUTHORITY),
    }
    _put_once_or_verify(storage, manifest_key, _canonical_bytes(manifest))
    return read_private_research_workspace_structural(workspace_path)["manifest"]


def build_private_v3_research_workspace(
    private_root: str | Path,
    input_id: str,
) -> dict[str, Any]:
    """Build or exactly replay a sealed additive V3 research workspace."""

    try:
        from .private_research_v2 import input_manifest_key as v2_input_manifest_key
        from .private_research_v3 import (
            build_private_v3_research_projection,
            input_manifest_key as v3_input_manifest_key,
            read_private_v3_research_bundle,
        )

        root = validate_private_root(Path(private_root))
        research_input, parent_v2_input, base_intake = (
            read_private_v3_research_bundle(root, input_id)
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private V3 research inputs are invalid") from exc
    return _build_private_v3_research_workspace_from_verified(
        root,
        research_input,
        parent_v2_input,
        base_intake,
    )


def _build_private_v3_research_workspace_from_verified(
    private_root: str | Path,
    research_input: Mapping[str, Any],
    parent_v2_input: Mapping[str, Any],
    base_intake: Mapping[str, Any],
) -> dict[str, Any]:
    """Publish V3 from already source-authenticated in-process inputs.

    This is deliberately private to the local orchestration boundary.  The
    projection builder verifies both source capabilities and their content
    bindings before any output is accepted.  Public ID-based replay continues
    through ``build_private_v3_research_workspace`` above.
    """

    try:
        from .private_research_v2 import input_manifest_key as v2_input_manifest_key
        from .private_research_v3 import (
            build_private_v3_research_projection,
            input_manifest_key as v3_input_manifest_key,
        )

        root = validate_private_root(Path(private_root))
        projection = build_private_v3_research_projection(
            research_input, parent_v2_input, base_intake
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private V3 research inputs are invalid") from exc
    if (
        not isinstance(research_input, dict)
        or not isinstance(parent_v2_input, dict)
        or not isinstance(base_intake, dict)
        or not isinstance(projection, dict)
        or projection.get("contract") != V3_PROJECTION_CONTRACT
    ):
        raise PrivateResearchError("private V3 research input shape differs")
    projection_sha = projection.get("projection_sha256")
    if not isinstance(projection_sha, str) or not _HEX64.fullmatch(projection_sha):
        raise PrivateResearchError("private V3 research projection identity differs")
    input_id = research_input.get("input_id")
    if not isinstance(input_id, str) or not _HEX64.fullmatch(input_id):
        raise PrivateResearchError("private V3 research input identity differs")
    workspace_id = _v3_workspace_identity(
        research_input, parent_v2_input, base_intake, projection
    )
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
    artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    artifact_records: list[dict[str, Any]] = []
    for name in sorted(artifacts):
        data = artifacts[name]
        _put_once_or_verify(storage, f"{prefix}/{name}", data)
        artifact_records.append(_artifact_record(name, data))
    manifest: dict[str, Any] = {
        "contract": V3_CONTRACT,
        "data_mode": V2_DATA_MODE,
        "authority": AUTHORITY,
        "operational_authority": False,
        "workspace_id": workspace_id,
        "intake_id": input_id,
        "intake_manifest_key": v3_input_manifest_key(input_id),
        "intake_sha256": _sha256(_canonical_bytes(research_input)),
        "parent_v2_input_id": parent_v2_input["input_id"],
        "parent_v2_input_manifest_key": v2_input_manifest_key(
            parent_v2_input["input_id"]
        ),
        "parent_v2_input_sha256": _sha256(
            _canonical_bytes(parent_v2_input)
        ),
        "base_intake_id": base_intake["intake_id"],
        "base_intake_manifest_key": intake_manifest_key(base_intake["intake_id"]),
        "base_intake_sha256": _sha256(_canonical_bytes(base_intake)),
        "projection_contract": V3_PROJECTION_CONTRACT,
        "projection_sha256": projection_sha,
        "artifacts": artifact_records,
        "limitations": _limitations(projection),
        "zero_authority": dict(_ZERO_AUTHORITY),
    }
    _put_once_or_verify(
        storage, f"{prefix}/manifest.json", _canonical_bytes(manifest)
    )
    return read_private_research_workspace_structural(workspace_path)["manifest"]


def build_private_v3_corrected_research_workspace(
    private_root: str | Path,
    input_id: str,
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Build the additive corrected-V3 workspace from immutable parents."""

    try:
        from .private_research_v3 import read_private_v3_research_bundle
        from .private_research_v3_corrected import (
            PARENT_V3_INPUT_ID,
            accepted_parent_snapshot,
            read_private_v3_corrected_bundle,
        )

        root = validate_private_root(Path(private_root))
        parent_before = accepted_parent_snapshot(root)
        parent_v3, parent_v2, base = read_private_v3_research_bundle(
            root, PARENT_V3_INPUT_ID
        )
        delta, corrected_input = read_private_v3_corrected_bundle(
            root,
            input_id,
            parent_v3_input=parent_v3,
            repo_root=repo_root,
        )
        parent_workspace = read_private_research_workspace(
            root
            / "private-research"
            / "workspaces"
            / corrected_input["parent_projection"]["workspace_id"]
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 research inputs are invalid"
        ) from exc
    manifest = _build_private_v3_corrected_workspace_from_verified(
        root,
        corrected_input,
        delta,
        parent_v3,
        parent_v2,
        parent_workspace["projection"],
    )
    try:
        parent_after = accepted_parent_snapshot(root)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 accepted parent post-check failed"
        ) from exc
    if _canonical_bytes(parent_before) != _canonical_bytes(parent_after):
        raise PrivateResearchError("private corrected-V3 accepted parent changed")
    return manifest


def _build_private_v3_corrected_workspace_from_verified(
    private_root: str | Path,
    corrected_input: Mapping[str, Any],
    delta: Mapping[str, Any],
    parent_v3: Mapping[str, Any],
    parent_v2: Mapping[str, Any],
    parent_projection: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        from .private_research_v3_corrected import (
            _ensure_corrected_private_parent,
            build_private_v3_corrected_projection,
            corrected_input_manifest_key,
            delta_manifest_key,
        )

        root = validate_private_root(Path(private_root))
        projection = build_private_v3_corrected_projection(
            corrected_input, delta, parent_v3, parent_v2, parent_projection
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 projection inputs are invalid"
        ) from exc
    if (
        projection.get("contract") != V3_CORRECTED_PROJECTION_CONTRACT
        or corrected_input.get("contract")
        != "BUFFALO_PRIVATE_DEVELOPMENT_FORECAST_RESEARCH_INPUT_V3_CORRECTED_V1"
    ):
        raise PrivateResearchError("private corrected-V3 input shape differs")
    artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    artifact_records = [
        _artifact_record(name, artifacts[name]) for name in sorted(artifacts)
    ]
    workspace_id = _v3_corrected_workspace_identity(
        corrected_input, projection, artifact_records
    )
    prefix = f"private-research/workspaces/{workspace_id}"
    workspace_path = root / prefix
    _ensure_corrected_private_parent(root, f"{prefix}/manifest.json")
    storage = LocalFilesystemStorage(root)
    for name in sorted(artifacts):
        _put_once_or_verify(storage, f"{prefix}/{name}", artifacts[name])
    parent_v3_descriptor = corrected_input["parent_v3_input"]
    parent_v2_descriptor = corrected_input["parent_v2_input"]
    base_descriptor = corrected_input["base_intake"]
    parent_projection_descriptor = corrected_input["parent_projection"]
    delta_descriptor = corrected_input["creation_evidence_delta"]
    manifest: dict[str, Any] = {
        "contract": V3_CORRECTED_CONTRACT,
        "data_mode": V2_DATA_MODE,
        "authority": AUTHORITY,
        "operational_authority": False,
        "workspace_id": workspace_id,
        "corrected_input_id": corrected_input["input_id"],
        "corrected_input_key": corrected_input_manifest_key(
            corrected_input["input_id"]
        ),
        "corrected_input_sha256": _sha256(_canonical_bytes(corrected_input)),
        "parent_v3_input_id": parent_v3_descriptor["input_id"],
        "parent_v3_input_key": parent_v3_descriptor["storage_key"],
        "parent_v3_input_sha256": parent_v3_descriptor["sha256"],
        "parent_v2_input_id": parent_v2_descriptor["input_id"],
        "parent_v2_input_key": parent_v2_descriptor["storage_key"],
        "parent_v2_input_sha256": parent_v2_descriptor["sha256"],
        "base_intake_id": base_descriptor["intake_id"],
        "base_intake_key": base_descriptor["storage_key"],
        "base_intake_sha256": base_descriptor["sha256"],
        "parent_projection_sha256": parent_projection_descriptor[
            "projection_sha256"
        ],
        "parent_projection_artifact_sha256": parent_projection_descriptor[
            "artifact_sha256"
        ],
        "creation_delta_id": delta_descriptor["delta_id"],
        "creation_delta_raw_sha256": delta_descriptor["raw_csv_sha256"],
        "creation_delta_normalized_row_set_sha256": delta_descriptor[
            "normalized_row_set_sha256"
        ],
        "implementation_lineage": corrected_input["implementation_lineage"],
        "joint_policy_canonical_sha256": corrected_input["joint_policy"][
            "policy_canonical_sha256"
        ],
        "projection_contract": V3_CORRECTED_PROJECTION_CONTRACT,
        "projection_sha256": projection["projection_sha256"],
        "artifacts": artifact_records,
        "limitations": _limitations(projection),
        "zero_authority": dict(_ZERO_AUTHORITY),
    }
    _put_once_or_verify(
        storage, f"{prefix}/manifest.json", _canonical_bytes(manifest)
    )
    return read_private_research_workspace(workspace_path)["manifest"]


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


def _read_private_v2_workspace(
    *,
    root: Path,
    prefix: str,
    expected_workspace_id: str,
    storage: LocalFilesystemStorage,
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
) -> dict[str, Any]:
    try:
        from .private_research_v2 import (
            build_private_v2_research_projection,
            input_manifest_key as v2_input_manifest_key,
            read_private_v2_research_bundle,
        )

        input_id = str(manifest.get("intake_id", ""))
        base_id = str(manifest.get("base_intake_id", ""))
        expected_input_key = v2_input_manifest_key(input_id)
        expected_base_key = intake_manifest_key(base_id)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private V2 research manifest contract differs") from exc
    expected_keys = {
        "contract",
        "data_mode",
        "authority",
        "operational_authority",
        "workspace_id",
        "intake_id",
        "intake_manifest_key",
        "intake_sha256",
        "base_intake_id",
        "base_intake_manifest_key",
        "base_intake_sha256",
        "projection_contract",
        "projection_sha256",
        "artifacts",
        "limitations",
        "zero_authority",
    }
    if (
        set(manifest) != expected_keys
        or manifest.get("contract") != V2_CONTRACT
        or manifest.get("data_mode") != V2_DATA_MODE
        or manifest.get("authority") != AUTHORITY
        or manifest.get("operational_authority") is not False
        or manifest.get("workspace_id") != expected_workspace_id
        or manifest.get("projection_contract") != V2_PROJECTION_CONTRACT
        or manifest.get("intake_manifest_key") != expected_input_key
        or manifest.get("base_intake_manifest_key") != expected_base_key
        or not _exact_zero_authority(manifest.get("zero_authority"))
        or _canonical_bytes(manifest) != manifest_bytes
    ):
        raise PrivateResearchError("private V2 research manifest contract differs")
    try:
        research_input, base_intake = read_private_v2_research_bundle(
            root, input_id
        )
        projection = build_private_v2_research_projection(
            research_input, base_intake
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private V2 research semantic replay failed") from exc
    if (
        manifest.get("intake_sha256") != _sha256(_canonical_bytes(research_input))
        or manifest.get("base_intake_sha256") != _sha256(_canonical_bytes(base_intake))
        or manifest.get("projection_sha256") != projection.get("projection_sha256")
        or expected_workspace_id
        != _v2_workspace_identity(research_input, base_intake, projection)
        or manifest.get("limitations") != _limitations(projection)
    ):
        raise PrivateResearchError("private V2 research source binding differs")
    expected_artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    records = manifest.get("artifacts")
    if not isinstance(records, list) or len(records) != len(expected_artifacts):
        raise PrivateResearchError("private V2 research artifact inventory differs")
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "name",
            "path",
            "bytes",
            "sha256",
            "media_type",
        }:
            raise PrivateResearchError("private V2 research artifact record differs")
        name = record.get("name")
        if not isinstance(name, str) or name in by_name or record.get("path") != name:
            raise PrivateResearchError("private V2 research artifact name differs")
        by_name[name] = record
    if set(by_name) != set(expected_artifacts):
        raise PrivateResearchError("private V2 research artifact set differs")
    loaded: dict[str, bytes] = {}
    for name, expected in expected_artifacts.items():
        try:
            data = storage.get_bytes(f"{prefix}/{name}")
        except OSError as exc:
            raise PrivateResearchError("private V2 research artifact is unavailable") from exc
        if data != expected or by_name[name] != _artifact_record(name, data):
            raise PrivateResearchError("private V2 research artifact integrity differs")
        loaded[name] = data
    return {"manifest": dict(manifest), "projection": projection, "artifacts": loaded}


def _read_private_v3_workspace(
    *,
    root: Path,
    prefix: str,
    expected_workspace_id: str,
    storage: LocalFilesystemStorage,
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
) -> dict[str, Any]:
    try:
        from .private_research_v2 import input_manifest_key as v2_input_manifest_key
        from .private_research_v3 import (
            build_private_v3_research_projection,
            input_manifest_key as v3_input_manifest_key,
            read_private_v3_research_bundle,
        )

        input_id = str(manifest.get("intake_id", ""))
        parent_id = str(manifest.get("parent_v2_input_id", ""))
        base_id = str(manifest.get("base_intake_id", ""))
        expected_input_key = v3_input_manifest_key(input_id)
        expected_parent_key = v2_input_manifest_key(parent_id)
        expected_base_key = intake_manifest_key(base_id)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private V3 research manifest contract differs") from exc
    expected_keys = {
        "contract",
        "data_mode",
        "authority",
        "operational_authority",
        "workspace_id",
        "intake_id",
        "intake_manifest_key",
        "intake_sha256",
        "parent_v2_input_id",
        "parent_v2_input_manifest_key",
        "parent_v2_input_sha256",
        "base_intake_id",
        "base_intake_manifest_key",
        "base_intake_sha256",
        "projection_contract",
        "projection_sha256",
        "artifacts",
        "limitations",
        "zero_authority",
    }
    if (
        set(manifest) != expected_keys
        or manifest.get("contract") != V3_CONTRACT
        or manifest.get("data_mode") != V2_DATA_MODE
        or manifest.get("authority") != AUTHORITY
        or manifest.get("operational_authority") is not False
        or manifest.get("workspace_id") != expected_workspace_id
        or manifest.get("projection_contract") != V3_PROJECTION_CONTRACT
        or manifest.get("intake_manifest_key") != expected_input_key
        or manifest.get("parent_v2_input_manifest_key") != expected_parent_key
        or manifest.get("base_intake_manifest_key") != expected_base_key
        or not _exact_zero_authority(manifest.get("zero_authority"))
        or _canonical_bytes(manifest) != manifest_bytes
    ):
        raise PrivateResearchError("private V3 research manifest contract differs")
    try:
        research_input, parent_v2_input, base_intake = (
            read_private_v3_research_bundle(root, input_id)
        )
        projection = build_private_v3_research_projection(
            research_input, parent_v2_input, base_intake
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private V3 research semantic replay failed") from exc
    if (
        manifest.get("intake_sha256") != _sha256(_canonical_bytes(research_input))
        or manifest.get("parent_v2_input_sha256")
        != _sha256(_canonical_bytes(parent_v2_input))
        or manifest.get("base_intake_sha256")
        != _sha256(_canonical_bytes(base_intake))
        or manifest.get("projection_sha256") != projection.get("projection_sha256")
        or expected_workspace_id
        != _v3_workspace_identity(
            research_input, parent_v2_input, base_intake, projection
        )
        or manifest.get("limitations") != _limitations(projection)
    ):
        raise PrivateResearchError("private V3 research source binding differs")
    # The source bundles are needed only through the semantic identity checks
    # above.  Releasing them before rendering the large immutable artifacts keeps
    # the private viewer's fail-closed startup replay within a bounded footprint.
    del research_input, parent_v2_input, base_intake
    gc.collect()
    artifact_names = tuple(_ARTIFACT_MEDIA_TYPES)
    records = manifest.get("artifacts")
    if not isinstance(records, list) or len(records) != len(artifact_names):
        raise PrivateResearchError("private V3 research artifact inventory differs")
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "name",
            "path",
            "bytes",
            "sha256",
            "media_type",
        }:
            raise PrivateResearchError("private V3 research artifact record differs")
        name = record.get("name")
        if not isinstance(name, str) or name in by_name or record.get("path") != name:
            raise PrivateResearchError("private V3 research artifact name differs")
        by_name[name] = record
    if set(by_name) != set(artifact_names):
        raise PrivateResearchError("private V3 research artifact set differs")
    loaded: dict[str, bytes] = {}
    for name in artifact_names:
        if name == "coverage.json":
            expected = _canonical_bytes(_coverage_export(projection))
        elif name == "owner-preview.html":
            expected = render_private_research_html(projection).encode("utf-8")
        elif name == "owner-worksheet.csv":
            expected = render_private_research_csv(projection).encode("utf-8")
        else:
            expected = _canonical_bytes(projection)
        try:
            data = storage.get_bytes(f"{prefix}/{name}")
        except OSError as exc:
            raise PrivateResearchError("private V3 research artifact is unavailable") from exc
        if data != expected or by_name[name] != _artifact_record(name, data):
            raise PrivateResearchError("private V3 research artifact integrity differs")
        loaded[name] = data
    return {"manifest": dict(manifest), "projection": projection, "artifacts": loaded}


def _read_private_v3_corrected_workspace(
    *,
    root: Path,
    prefix: str,
    expected_workspace_id: str,
    storage: LocalFilesystemStorage,
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
    repo_root: str | Path,
) -> dict[str, Any]:
    try:
        from .private_research_v3 import read_private_v3_research_bundle
        from .private_research_v3_corrected import (
            PARENT_V3_INPUT_ID,
            accepted_parent_snapshot,
            build_private_v3_corrected_projection,
            corrected_input_manifest_key,
            read_private_v3_corrected_bundle,
        )
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 manifest contract differs"
        ) from exc
    expected_keys = {
        "contract", "data_mode", "authority", "operational_authority",
        "workspace_id", "corrected_input_id", "corrected_input_key",
        "corrected_input_sha256", "parent_v3_input_id", "parent_v3_input_key",
        "parent_v3_input_sha256", "parent_v2_input_id", "parent_v2_input_key",
        "parent_v2_input_sha256", "base_intake_id", "base_intake_key",
        "base_intake_sha256", "parent_projection_sha256",
        "parent_projection_artifact_sha256", "creation_delta_id",
        "creation_delta_raw_sha256", "creation_delta_normalized_row_set_sha256",
        "implementation_lineage", "joint_policy_canonical_sha256",
        "projection_contract", "projection_sha256", "artifacts", "limitations",
        "zero_authority",
    }
    input_id = str(manifest.get("corrected_input_id", ""))
    try:
        expected_input_key = corrected_input_manifest_key(input_id)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 manifest contract differs"
        ) from exc
    if (
        set(manifest) != expected_keys
        or manifest.get("contract") != V3_CORRECTED_CONTRACT
        or manifest.get("data_mode") != V2_DATA_MODE
        or manifest.get("authority") != AUTHORITY
        or manifest.get("operational_authority") is not False
        or manifest.get("workspace_id") != expected_workspace_id
        or manifest.get("corrected_input_key") != expected_input_key
        or manifest.get("projection_contract")
        != V3_CORRECTED_PROJECTION_CONTRACT
        or not _exact_zero_authority(manifest.get("zero_authority"))
        or _canonical_bytes(manifest) != manifest_bytes
    ):
        raise PrivateResearchError("private corrected-V3 manifest contract differs")
    try:
        parent_before = accepted_parent_snapshot(root)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 accepted parent pre-check failed"
        ) from exc
    records = manifest.get("artifacts")
    if not isinstance(records, list) or len(records) != len(_ARTIFACT_MEDIA_TYPES):
        raise PrivateResearchError("private corrected-V3 artifact inventory differs")
    if [record.get("name") if isinstance(record, Mapping) else None for record in records] != sorted(
        _ARTIFACT_MEDIA_TYPES
    ):
        raise PrivateResearchError("private corrected-V3 artifact order differs")
    by_name: dict[str, dict[str, Any]] = {}
    loaded: dict[str, bytes] = {}
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != {"name", "path", "bytes", "sha256", "media_type"}
            or not isinstance(record.get("name"), str)
            or record["name"] in by_name
            or record.get("path") != record["name"]
            or record["name"] not in _ARTIFACT_MEDIA_TYPES
            or type(record.get("bytes")) is not int
            or record["bytes"] < 0
            or not isinstance(record.get("sha256"), str)
            or not _HEX64.fullmatch(record["sha256"])
            or record.get("media_type")
            != _ARTIFACT_MEDIA_TYPES[record["name"]]
        ):
            raise PrivateResearchError("private corrected-V3 artifact record differs")
        by_name[record["name"]] = dict(record)
    if set(by_name) != set(_ARTIFACT_MEDIA_TYPES):
        raise PrivateResearchError("private corrected-V3 artifact set differs")
    for name in _ARTIFACT_MEDIA_TYPES:
        try:
            data = storage.get_bytes(f"{prefix}/{name}")
        except OSError as exc:
            raise PrivateResearchError(
                "private corrected-V3 artifact is unavailable"
            ) from exc
        if by_name[name] != _artifact_record(name, data):
            raise PrivateResearchError(
                "private corrected-V3 artifact integrity differs"
            )
        loaded[name] = data
    try:
        serialized_projection = json.loads(loaded["projection.json"])
        if (
            not isinstance(serialized_projection, dict)
            or _canonical_bytes(serialized_projection) != loaded["projection.json"]
        ):
            raise PrivateResearchError(
                "private corrected-V3 projection serialization differs"
            )
        parent_v3, parent_v2, _base = read_private_v3_research_bundle(
            root, PARENT_V3_INPUT_ID
        )
        delta, corrected_input = read_private_v3_corrected_bundle(
            root,
            input_id,
            parent_v3_input=parent_v3,
            repo_root=repo_root,
        )
        parent_workspace = read_private_research_workspace(
            root
            / "private-research"
            / "workspaces"
            / corrected_input["parent_projection"]["workspace_id"]
        )
        projection = build_private_v3_corrected_projection(
            corrected_input,
            delta,
            parent_v3,
            parent_v2,
            parent_workspace["projection"],
        )
    except PrivateResearchError:
        raise
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 semantic replay failed"
        ) from exc
    if dict(projection) != serialized_projection:
        raise PrivateResearchError("private corrected-V3 projection replay differs")
    expected_artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    if any(loaded[name] != data for name, data in expected_artifacts.items()):
        raise PrivateResearchError("private corrected-V3 artifact content differs")
    input_descriptor = corrected_input
    if (
        manifest.get("corrected_input_sha256")
        != _sha256(_canonical_bytes(corrected_input))
        or manifest.get("parent_v3_input_id")
        != input_descriptor["parent_v3_input"]["input_id"]
        or manifest.get("parent_v3_input_key")
        != input_descriptor["parent_v3_input"]["storage_key"]
        or manifest.get("parent_v3_input_sha256")
        != input_descriptor["parent_v3_input"]["sha256"]
        or manifest.get("parent_v2_input_id")
        != input_descriptor["parent_v2_input"]["input_id"]
        or manifest.get("parent_v2_input_key")
        != input_descriptor["parent_v2_input"]["storage_key"]
        or manifest.get("parent_v2_input_sha256")
        != input_descriptor["parent_v2_input"]["sha256"]
        or manifest.get("base_intake_id")
        != input_descriptor["base_intake"]["intake_id"]
        or manifest.get("base_intake_key")
        != input_descriptor["base_intake"]["storage_key"]
        or manifest.get("base_intake_sha256")
        != input_descriptor["base_intake"]["sha256"]
        or manifest.get("parent_projection_sha256")
        != input_descriptor["parent_projection"]["projection_sha256"]
        or manifest.get("parent_projection_artifact_sha256")
        != input_descriptor["parent_projection"]["artifact_sha256"]
        or manifest.get("creation_delta_id")
        != input_descriptor["creation_evidence_delta"]["delta_id"]
        or manifest.get("creation_delta_raw_sha256")
        != input_descriptor["creation_evidence_delta"]["raw_csv_sha256"]
        or manifest.get("creation_delta_normalized_row_set_sha256")
        != input_descriptor["creation_evidence_delta"]["normalized_row_set_sha256"]
        or manifest.get("implementation_lineage")
        != input_descriptor["implementation_lineage"]
        or manifest.get("joint_policy_canonical_sha256")
        != input_descriptor["joint_policy"]["policy_canonical_sha256"]
        or manifest.get("projection_sha256") != projection["projection_sha256"]
        or manifest.get("limitations") != _limitations(projection)
        or _v3_corrected_workspace_identity(
            corrected_input, projection, records
        )
        != expected_workspace_id
    ):
        raise PrivateResearchError("private corrected-V3 source binding differs")
    try:
        parent_after = accepted_parent_snapshot(root)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError(
            "private corrected-V3 accepted parent post-check failed"
        ) from exc
    if _canonical_bytes(parent_before) != _canonical_bytes(parent_after):
        raise PrivateResearchError("private corrected-V3 accepted parent changed")
    return {
        "manifest": dict(manifest),
        "projection": projection,
        "artifacts": loaded,
    }


def read_private_research_workspace_structural(
    workspace_root: str | Path,
) -> dict[str, Any]:
    """Validate a sealed workspace for read-only delivery preflight.

    Legacy contracts validate paths and modes, canonical manifests,
    content-addressed identity, projection semantics, exact exports, and artifact
    hashes without replaying upstream source packages. Corrected-V3 intentionally
    performs its independently mandated accepted-parent and joint-planner replay
    at this boundary. The application startup also calls
    :func:`read_private_research_workspace`, which replays the complete raw-source
    derivation and referenced-input availability on every launch and restart.
    """

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
    if not isinstance(manifest, dict) or _canonical_bytes(manifest) != manifest_bytes:
        raise PrivateResearchError("private research manifest contract differs")

    if manifest.get("contract") == V3_CORRECTED_CONTRACT:
        return _read_private_v3_corrected_workspace(
            root=root,
            prefix=prefix,
            expected_workspace_id=expected_workspace_id,
            storage=storage,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            repo_root=Path(__file__).resolve().parents[3],
        )

    workspace_contract = manifest.get("contract")
    is_v2 = workspace_contract == V2_CONTRACT
    is_v3 = workspace_contract == V3_CONTRACT
    is_development = is_v2 or is_v3
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
    if is_development:
        expected_keys |= {
            "base_intake_id",
            "base_intake_manifest_key",
            "base_intake_sha256",
        }
    if is_v3:
        expected_keys |= {
            "parent_v2_input_id",
            "parent_v2_input_manifest_key",
            "parent_v2_input_sha256",
        }
    try:
        if is_v3:
            from .private_research_v2 import input_manifest_key as v2_input_manifest_key
            from .private_research_v3 import input_manifest_key as v3_input_manifest_key

            expected_input_key = v3_input_manifest_key(
                str(manifest.get("intake_id", ""))
            )
            expected_parent_key = v2_input_manifest_key(
                str(manifest.get("parent_v2_input_id", ""))
            )
            expected_base_key = intake_manifest_key(
                str(manifest.get("base_intake_id", ""))
            )
        elif is_v2:
            from .private_research_v2 import input_manifest_key as v2_input_manifest_key

            expected_input_key = v2_input_manifest_key(str(manifest.get("intake_id", "")))
            expected_base_key = intake_manifest_key(
                str(manifest.get("base_intake_id", ""))
            )
        else:
            expected_input_key = intake_manifest_key(
                str(manifest.get("intake_id", ""))
            )
            expected_base_key = None
            expected_parent_key = None
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private research manifest contract differs") from exc
    if (
        set(manifest) != expected_keys
        or manifest.get("contract")
        != (V3_CONTRACT if is_v3 else (V2_CONTRACT if is_v2 else CONTRACT))
        or manifest.get("data_mode")
        != (V2_DATA_MODE if is_development else DATA_MODE)
        or manifest.get("authority") != AUTHORITY
        or manifest.get("operational_authority") is not False
        or manifest.get("workspace_id") != expected_workspace_id
        or manifest.get("projection_contract")
        != (
            V3_PROJECTION_CONTRACT
            if is_v3
            else (V2_PROJECTION_CONTRACT if is_v2 else PROJECTION_CONTRACT)
        )
        or manifest.get("intake_manifest_key") != expected_input_key
        or (
            is_development
            and manifest.get("base_intake_manifest_key") != expected_base_key
        )
        or (
            is_v3
            and manifest.get("parent_v2_input_manifest_key")
            != expected_parent_key
        )
        or not _exact_zero_authority(manifest.get("zero_authority"))
    ):
        raise PrivateResearchError("private research manifest contract differs")

    records = manifest.get("artifacts")
    if not isinstance(records, list) or len(records) != len(_ARTIFACT_MEDIA_TYPES):
        raise PrivateResearchError("private research artifact inventory differs")
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "name",
            "path",
            "bytes",
            "sha256",
            "media_type",
        }:
            raise PrivateResearchError("private research artifact record differs")
        name = record.get("name")
        if (
            not isinstance(name, str)
            or name in by_name
            or name not in _ARTIFACT_MEDIA_TYPES
            or record.get("path") != name
            or record.get("media_type") != _ARTIFACT_MEDIA_TYPES[name]
        ):
            raise PrivateResearchError("private research artifact name differs")
        by_name[name] = record
    if set(by_name) != set(_ARTIFACT_MEDIA_TYPES):
        raise PrivateResearchError("private research artifact set differs")
    loaded: dict[str, bytes] = {}
    for name in sorted(_ARTIFACT_MEDIA_TYPES):
        try:
            data = storage.get_bytes(f"{prefix}/{name}")
        except OSError as exc:
            raise PrivateResearchError("private research artifact is unavailable") from exc
        if by_name[name] != _artifact_record(name, data):
            raise PrivateResearchError("private research artifact integrity differs")
        loaded[name] = data
    try:
        projection = json.loads(loaded["projection.json"])
        if (
            not isinstance(projection, dict)
            or _canonical_bytes(projection) != loaded["projection.json"]
            or private_research_projection_sha256(projection)
            != manifest.get("projection_sha256")
        ):
            raise PrivateResearchError("private research projection differs")
    except PrivateResearchError:
        raise
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchError("private research projection is invalid") from exc
    expected_artifacts = {
        "coverage.json": _canonical_bytes(_coverage_export(projection)),
        "owner-preview.html": render_private_research_html(projection).encode("utf-8"),
        "owner-worksheet.csv": render_private_research_csv(projection).encode("utf-8"),
        "projection.json": _canonical_bytes(projection),
    }
    if any(loaded[name] != expected for name, expected in expected_artifacts.items()):
        raise PrivateResearchError("private research artifact content differs")
    if manifest.get("limitations") != _limitations(projection):
        raise PrivateResearchError("private research limitations differ")
    if is_v3:
        expected_identity = _sha256(
            _canonical_bytes(
                {
                    "contract": V3_CONTRACT,
                    "input_id": manifest.get("intake_id"),
                    "input_sha256": manifest.get("intake_sha256"),
                    "parent_v2_input_id": manifest.get("parent_v2_input_id"),
                    "parent_v2_input_sha256": manifest.get(
                        "parent_v2_input_sha256"
                    ),
                    "base_intake_id": manifest.get("base_intake_id"),
                    "base_intake_sha256": manifest.get("base_intake_sha256"),
                    "projection_contract": V3_PROJECTION_CONTRACT,
                    "projection_sha256": manifest.get("projection_sha256"),
                }
            )
        )
    elif is_v2:
        expected_identity = _sha256(
            _canonical_bytes(
                {
                    "contract": V2_CONTRACT,
                    "input_id": manifest.get("intake_id"),
                    "input_sha256": manifest.get("intake_sha256"),
                    "base_intake_id": manifest.get("base_intake_id"),
                    "base_intake_sha256": manifest.get("base_intake_sha256"),
                    "projection_contract": V2_PROJECTION_CONTRACT,
                    "projection_sha256": manifest.get("projection_sha256"),
                }
            )
        )
    else:
        expected_identity = _sha256(
            _canonical_bytes(
                {
                    "contract": CONTRACT,
                    "intake_id": manifest.get("intake_id"),
                    "intake_sha256": manifest.get("intake_sha256"),
                    "projection_contract": PROJECTION_CONTRACT,
                    "projection_sha256": manifest.get("projection_sha256"),
                }
            )
        )
    if expected_identity != expected_workspace_id:
        raise PrivateResearchError("private research workspace identity differs")
    return {"manifest": manifest, "projection": projection, "artifacts": loaded}


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
    if isinstance(manifest, dict) and manifest.get("contract") == V3_CONTRACT:
        return _read_private_v3_workspace(
            root=root,
            prefix=prefix,
            expected_workspace_id=expected_workspace_id,
            storage=storage,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
        )
    if (
        isinstance(manifest, dict)
        and manifest.get("contract") == V3_CORRECTED_CONTRACT
    ):
        return _read_private_v3_corrected_workspace(
            root=root,
            prefix=prefix,
            expected_workspace_id=expected_workspace_id,
            storage=storage,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            repo_root=Path(__file__).resolve().parents[3],
        )
    if isinstance(manifest, dict) and manifest.get("contract") == V2_CONTRACT:
        return _read_private_v2_workspace(
            root=root,
            prefix=prefix,
            expected_workspace_id=expected_workspace_id,
            storage=storage,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
        )
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
