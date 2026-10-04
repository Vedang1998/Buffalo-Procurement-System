"""Code-owned semantic acceptance contract for the staging research worker."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping

from .staging_research_transfer import (
    ACCEPTED_COMMIT,
    ACCEPTED_TARGET_WORKSPACE_ID,
    ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256,
    DEPLOYMENT_AGGREGATE_BYTES,
    DEPLOYMENT_HOST_AGGREGATE_BYTES,
    DEPLOYMENT_HOST_RECORD_COUNT,
    DEPLOYMENT_INVENTORY_BYTES,
    DEPLOYMENT_INVENTORY_FILE_SHA256,
    DEPLOYMENT_INVENTORY_SHA256,
    DEPLOYMENT_RECORD_COUNT,
    GIT_AGGREGATE_BYTES,
    GIT_RECORD_COUNT,
)
from .staging_source_bundle import (
    ACCEPTED_BUNDLE_SHA256,
    ACCEPTED_COMMIT_COUNT,
    ACCEPTED_LS_TREE_SHA256,
    ACCEPTED_LS_TREE_ENTRIES,
    ACCEPTED_MODE_COUNTS,
    ACCEPTED_OBJECT_COUNT,
    ACCEPTED_OBJECT_SET_SHA256,
    ACCEPTED_OBJECT_TYPE_COUNTS,
    ACCEPTED_PARENT,
    ACCEPTED_PARENT_TREE,
    ACCEPTED_REF,
    ACCEPTED_TIP,
    ACCEPTED_TREE,
    SourceBundleError,
    validate_accepted_source_store,
)


EXPECTED_PROJECTION_SHA256 = (
    "650236984b3a95c7ade245b210000153bd93353ddea3cbcf9473e18851efeb3a"
)
EXPECTED_PARENT_SNAPSHOT_SHA256 = (
    "61cc5ce121a244ed7d61b4a4a8a5b964cbb8581a2dc1c0431f8a4519ebe411c9"
)
EXPECTED_ARTIFACTS = {
    "coverage.json": {
        "bytes": 10_684_242,
        "media_type": "application/json",
        "sha256": "0207ca46da3a8a86bc66708250f8365c169abba6c0af90fe7cbb5cb8bb71db32",
    },
    "owner-preview.html": {
        "bytes": 21_144_726,
        "media_type": "text/html; charset=utf-8",
        "sha256": "d306c10d1e1e7298edbfb55c03a516520be3e6f1c6e914fa67b9cb88939ec95a",
    },
    "owner-worksheet.csv": {
        "bytes": 17_347_994,
        "media_type": "text/csv; charset=utf-8",
        "sha256": "ab0e427a8252008ac55e086aa2ff8eba00bf8d06a500773ec642d09bdbdf6f30",
    },
    "projection.json": {
        "bytes": 191_788_544,
        "media_type": "application/json",
        "sha256": "eb54bc5f68094b1b87c78e6fbe76500d3f317616f7695c4b2d70a7d0318da18d",
    },
}
EXPECTED_COVERAGE = {
    "variant_count": 2_009,
    "sidecar_count": 1_365,
    "calculated_variant_count": 1_365,
    "not_applicable_variant_count": 644,
    "blocked_variant_count": 0,
    "not_processed_variant_count": 0,
    "raw_point_violation_count": 38,
    "raw_target_violation_count": 136,
    "corrected_point_violation_count": 0,
    "corrected_target_violation_count": 0,
}
EXPECTED_PRIMARY_STATUS_COUNTS = {
    "H3": {
        "CALCULATED": 1_365,
        "NOT_APPLICABLE": 644,
        "BLOCKED": 0,
        "NOT_PROCESSED": 0,
        "numerical_zero": 871,
    },
    "H10": {
        "CALCULATED": 1_365,
        "NOT_APPLICABLE": 644,
        "BLOCKED": 0,
        "NOT_PROCESSED": 0,
        "numerical_zero": 824,
    },
    "H17": {
        "CALCULATED": 1_365,
        "NOT_APPLICABLE": 644,
        "BLOCKED": 0,
        "NOT_PROCESSED": 0,
        "numerical_zero": 821,
    },
}
EXPECTED_VIOLATIONS = {
    "raw_point": 38,
    "raw_target": 136,
    "raw_unique_variants": 151,
    "corrected_point": 0,
    "corrected_target": 0,
    "corrected_unique_variants": 0,
}
EXPECTED_MEMBERSHIP_COUNTS = {
    "current_catalog": 2_009,
    "eligible": 1_365,
    "parent_not_applicable": 601,
    "reviewed_prior_blocked": 43,
    "corrected_not_applicable": 644,
    "corrected_blocked": 0,
}


class StagingResearchValidationError(ValueError):
    """The accepted release binding or semantic replay result differs."""


@dataclass(frozen=True)
class StagingResearchPaths:
    release: Path
    payload: Path
    manifest: Path
    git_dir: Path
    workspace: Path


@dataclass(frozen=True)
class ResearchSemanticProof:
    validation_identity: str
    workspace_id: str
    manifest_sha256: str
    projection_sha256: str


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


_VALIDATION_FACTS = {
    "contract": "BUFFALO_STAGING_RESEARCH_VALIDATION_V1",
    "deployment": {
        "aggregate_bytes": DEPLOYMENT_AGGREGATE_BYTES,
        "git_aggregate_bytes": GIT_AGGREGATE_BYTES,
        "git_records": GIT_RECORD_COUNT,
        "host_aggregate_bytes": DEPLOYMENT_HOST_AGGREGATE_BYTES,
        "host_records": DEPLOYMENT_HOST_RECORD_COUNT,
        "inventory_bytes": DEPLOYMENT_INVENTORY_BYTES,
        "inventory_file_sha256": DEPLOYMENT_INVENTORY_FILE_SHA256,
        "inventory_identity": DEPLOYMENT_INVENTORY_SHA256,
        "records": DEPLOYMENT_RECORD_COUNT,
    },
    "source": {
        "bundle_sha256": ACCEPTED_BUNDLE_SHA256,
        "commit_count": ACCEPTED_COMMIT_COUNT,
        "commit": ACCEPTED_COMMIT,
        "ls_tree_sha256": ACCEPTED_LS_TREE_SHA256,
        "ls_tree_entries": ACCEPTED_LS_TREE_ENTRIES,
        "mode_counts": ACCEPTED_MODE_COUNTS,
        "object_count": ACCEPTED_OBJECT_COUNT,
        "object_set_sha256": ACCEPTED_OBJECT_SET_SHA256,
        "object_type_counts": ACCEPTED_OBJECT_TYPE_COUNTS,
        "parent": ACCEPTED_PARENT,
        "parent_tree": ACCEPTED_PARENT_TREE,
        "ref": ACCEPTED_REF,
        "tip": ACCEPTED_TIP,
        "tree": ACCEPTED_TREE,
    },
    "semantic": {
        "artifacts": EXPECTED_ARTIFACTS,
        "coverage": EXPECTED_COVERAGE,
        "manifest_sha256": ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256,
        "membership_counts": EXPECTED_MEMBERSHIP_COUNTS,
        "parent_snapshot_sha256": EXPECTED_PARENT_SNAPSHOT_SHA256,
        "primary_status_counts": EXPECTED_PRIMARY_STATUS_COUNTS,
        "projection_sha256": EXPECTED_PROJECTION_SHA256,
        "violations": EXPECTED_VIOLATIONS,
        "workspace_id": ACCEPTED_TARGET_WORKSPACE_ID,
    },
}
RESEARCH_VALIDATION_IDENTITY = hashlib.sha256(
    b"BUFFALO_STAGING_RESEARCH_VALIDATION_V1\x00" + _canonical(_VALIDATION_FACTS)
).hexdigest()


def _absolute_canonical_path(raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise StagingResearchValidationError(f"{field} path differs")
    path = Path(raw)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise StagingResearchValidationError(f"{field} path differs")
    try:
        if path.resolve(strict=True) != path:
            raise StagingResearchValidationError(f"{field} path differs")
    except OSError:
        raise StagingResearchValidationError(f"{field} path is unavailable") from None
    return path


def _stream_sha256(path: Path, *, maximum_bytes: int) -> tuple[int, str]:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise StagingResearchValidationError("research control file is unavailable") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StagingResearchValidationError("research control file differs")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise StagingResearchValidationError("research control file is too large")
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError:
        raise StagingResearchValidationError("research control file is unavailable") from None
    finally:
        os.close(descriptor)
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise StagingResearchValidationError("research control file changed")
    return total, digest.hexdigest()


def validate_staging_research_paths(
    environment: Mapping[str, str],
    *,
    worker_runtime: bool = False,
) -> StagingResearchPaths:
    """Bind all four worker paths to one exact atomically published release."""

    payload = _absolute_canonical_path(
        environment.get("BUFFALO_RESEARCH_ROOT"), "research payload"
    )
    manifest = _absolute_canonical_path(
        environment.get("BUFFALO_RESEARCH_MANIFEST"), "research manifest"
    )
    git_dir = _absolute_canonical_path(
        environment.get("BUFFALO_RESEARCH_GIT_DIR"), "research Git store"
    )
    workspace = _absolute_canonical_path(
        environment.get("BUFFALO_PRIVATE_RESEARCH_WORKSPACE"),
        "research workspace",
    )
    release = payload.parent
    expected_workspace = (
        payload
        / "private-research"
        / "workspaces"
        / ACCEPTED_TARGET_WORKSPACE_ID
    )
    try:
        release_members = {item.name for item in release.iterdir()}
    except OSError:
        raise StagingResearchValidationError(
            "research release membership is unavailable"
        ) from None
    if (
        payload.name != "payload"
        or manifest != release / "deployment-inventory.json"
        or git_dir != release / "source.git"
        or workspace != expected_workspace
        or len({release, payload, manifest.parent, git_dir.parent}) != 2
    ):
        raise StagingResearchValidationError("research release binding differs")
    size, digest = _stream_sha256(
        manifest, maximum_bytes=DEPLOYMENT_INVENTORY_BYTES
    )
    if (
        size != DEPLOYMENT_INVENTORY_BYTES
        or digest != DEPLOYMENT_INVENTORY_FILE_SHA256
    ):
        raise StagingResearchValidationError("research deployment inventory differs")
    metadata: dict[Path, os.stat_result] = {}
    for path, field in (
        (release, "research release"),
        (payload, "research payload"),
        (git_dir, "research Git store"),
        (workspace, "research workspace"),
        (manifest, "research manifest"),
    ):
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            raise StagingResearchValidationError(f"{field} is unavailable") from None
        expected_directory = path != manifest
        if path.is_symlink() or (
            expected_directory != stat.S_ISDIR(info.st_mode)
        ) or (not expected_directory and not stat.S_ISREG(info.st_mode)):
            raise StagingResearchValidationError(f"{field} differs")
        metadata[path] = info
    if not worker_runtime:
        return StagingResearchPaths(
            release=release,
            payload=payload,
            manifest=manifest,
            git_dir=git_dir,
            workspace=workspace,
        )
    worker = (os.getuid(), os.getgid())
    supervisor = (metadata[release].st_uid, metadata[release].st_gid)
    if (
        stat.S_IMODE(metadata[release].st_mode) != 0o710
        or supervisor[0] == worker[0]
        or supervisor[1] != worker[1]
        or (
            metadata[payload].st_uid,
            metadata[payload].st_gid,
            stat.S_IMODE(metadata[payload].st_mode),
        )
        != (*worker, 0o700)
        or (
            metadata[workspace].st_uid,
            metadata[workspace].st_gid,
            stat.S_IMODE(metadata[workspace].st_mode),
        )
        != (*worker, 0o700)
        or (
            metadata[git_dir].st_uid,
            metadata[git_dir].st_gid,
            stat.S_IMODE(metadata[git_dir].st_mode),
        )
        != (supervisor[0], worker[1], 0o550)
        or (
            metadata[manifest].st_uid,
            metadata[manifest].st_gid,
            stat.S_IMODE(metadata[manifest].st_mode),
            metadata[manifest].st_nlink,
        )
        != (supervisor[0], worker[1], 0o440, 1)
        or release_members
        != {"deployment-inventory.json", "payload", "source.git"}
    ):
        raise StagingResearchValidationError("research release metadata differs")
    try:
        proof = validate_accepted_source_store(
            git_dir,
            owner_uid=supervisor[0],
            owner_gid=worker[1],
        )
    except SourceBundleError:
        raise StagingResearchValidationError(
            "research source store differs"
        ) from None
    if {
        "ref": proof.ref,
        "tip": proof.tip,
        "tree": proof.tree,
        "parent": proof.parent,
        "parent_tree": proof.parent_tree,
        "object_count": proof.object_count,
        "object_set_sha256": proof.object_set_sha256,
        "object_type_counts": proof.object_type_counts,
        "commit_count": proof.commit_count,
        "ls_tree_sha256": proof.ls_tree_sha256,
        "ls_tree_entries": proof.ls_tree_entries,
        "mode_counts": proof.mode_counts,
    } != {
        "ref": ACCEPTED_REF,
        "tip": ACCEPTED_TIP,
        "tree": ACCEPTED_TREE,
        "parent": ACCEPTED_PARENT,
        "parent_tree": ACCEPTED_PARENT_TREE,
        "object_count": ACCEPTED_OBJECT_COUNT,
        "object_set_sha256": ACCEPTED_OBJECT_SET_SHA256,
        "object_type_counts": ACCEPTED_OBJECT_TYPE_COUNTS,
        "commit_count": ACCEPTED_COMMIT_COUNT,
        "ls_tree_sha256": ACCEPTED_LS_TREE_SHA256,
        "ls_tree_entries": ACCEPTED_LS_TREE_ENTRIES,
        "mode_counts": ACCEPTED_MODE_COUNTS,
    }:
        raise StagingResearchValidationError("research source proof differs")
    return StagingResearchPaths(
        release=release,
        payload=payload,
        manifest=manifest,
        git_dir=git_dir,
        workspace=workspace,
    )


def validate_replayed_workspace(
    workspace: Mapping[str, object], *, manifest_sha256: str
) -> ResearchSemanticProof:
    """Accept only the exact aggregate result of the full corrected replay."""

    manifest = workspace.get("manifest")
    projection = workspace.get("projection")
    artifacts = workspace.get("artifacts")
    if (
        not isinstance(manifest, Mapping)
        or not isinstance(projection, Mapping)
        or artifacts != {}
        or manifest_sha256 != ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256
        or manifest.get("workspace_id") != ACCEPTED_TARGET_WORKSPACE_ID
        or manifest.get("operational_authority") is not False
        or projection.get("projection_sha256") != EXPECTED_PROJECTION_SHA256
    ):
        raise StagingResearchValidationError("research semantic identity differs")
    records = manifest.get("artifacts")
    if not isinstance(records, list):
        raise StagingResearchValidationError("research artifact inventory differs")
    observed_artifacts: dict[str, dict[str, object]] = {}
    for value in records:
        if not isinstance(value, Mapping) or not isinstance(value.get("name"), str):
            raise StagingResearchValidationError("research artifact inventory differs")
        name = value["name"]
        if name in observed_artifacts:
            raise StagingResearchValidationError("research artifact inventory differs")
        observed_artifacts[name] = {
            "bytes": value.get("bytes"),
            "media_type": value.get("media_type"),
            "sha256": value.get("sha256"),
        }
    joint = projection.get("joint_forecast_research")
    if not isinstance(joint, Mapping):
        raise StagingResearchValidationError("research semantic controls differ")
    raw_membership = joint.get("membership_controls")
    if not isinstance(raw_membership, Mapping):
        raise StagingResearchValidationError("research semantic controls differ")
    membership: dict[str, object] = {}
    for name, value in raw_membership.items():
        if not isinstance(name, str) or not isinstance(value, Mapping):
            raise StagingResearchValidationError("research semantic controls differ")
        membership[name] = value.get("count")
    if (
        observed_artifacts != EXPECTED_ARTIFACTS
        or projection.get("coverage_summary") != EXPECTED_COVERAGE
        or joint.get("primary_status_counts") != EXPECTED_PRIMARY_STATUS_COUNTS
        or joint.get("violation_counts") != EXPECTED_VIOLATIONS
        or membership != EXPECTED_MEMBERSHIP_COUNTS
    ):
        raise StagingResearchValidationError("research semantic controls differ")
    return ResearchSemanticProof(
        validation_identity=RESEARCH_VALIDATION_IDENTITY,
        workspace_id=ACCEPTED_TARGET_WORKSPACE_ID,
        manifest_sha256=ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256,
        projection_sha256=EXPECTED_PROJECTION_SHA256,
    )


__all__ = [
    "EXPECTED_ARTIFACTS",
    "EXPECTED_COVERAGE",
    "EXPECTED_MEMBERSHIP_COUNTS",
    "EXPECTED_PARENT_SNAPSHOT_SHA256",
    "EXPECTED_PRIMARY_STATUS_COUNTS",
    "EXPECTED_PROJECTION_SHA256",
    "EXPECTED_VIOLATIONS",
    "RESEARCH_VALIDATION_IDENTITY",
    "ResearchSemanticProof",
    "StagingResearchPaths",
    "StagingResearchValidationError",
    "validate_replayed_workspace",
    "validate_staging_research_paths",
]
