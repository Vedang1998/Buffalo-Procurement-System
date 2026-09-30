"""Fail-closed deployment inventory for the accepted private research closure.

The accepted dependency inventory remains immutable historical evidence.  This
module derives a deterministic deployment inventory from that evidence, adds
only the two A1 auxiliary files already pinned by the accepted semantic reader,
and provides exact local validation/staging primitives.  It never discovers a
payload by walking a private root.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence


ACCEPTED_COMMIT = "608929ad00adfd2eefaab29449743c5a3f0f035e"
ACCEPTED_TREE = "68a72c84d26dc15e0e0c8075dd2c1c6bc46d00b8"
ACCEPTED_TRANSFER_MANIFEST_SHA256 = (
    "1e2477f9221877c496130ee1458e608e0aaa3d3ffbf41729a46520d289b239c8"
)
ACCEPTED_DEPENDENCY_INVENTORY_SHA256 = (
    "a5d0862c7b37fa729e95dfd3c510c7dd69dda05b96512d3974ba6a567cbc1eb1"
)
ACCEPTED_DEPENDENCY_INVENTORY_BYTES = 21_834
ACCEPTED_TARGET_WORKSPACE_ID = (
    "031dfc5f8e4ed83f14184fc0d324dfc99c6af02bf3896bd493ceeaebed62451a"
)
ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256 = (
    "1387c28db7e458e196fa1961e55db3289bbb2855eb35b39a10deb814fc673353"
)

HISTORICAL_CONTRACT = "BUFFALO_ACCEPTED_SOURCE_DEPENDENCY_INVENTORY_V1"
DEPLOYMENT_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_DEPLOYMENT_INVENTORY_V2"
HISTORICAL_RECORD_COUNT = 75
HISTORICAL_AGGREGATE_BYTES = 1_030_999_197
HISTORICAL_HOST_RECORD_COUNT = 71
HISTORICAL_HOST_AGGREGATE_BYTES = 1_030_613_679
GIT_RECORD_COUNT = 4
GIT_AGGREGATE_BYTES = 385_518
DEPLOYMENT_RECORD_COUNT = 77
DEPLOYMENT_AGGREGATE_BYTES = 1_031_003_702
DEPLOYMENT_HOST_RECORD_COUNT = 73
DEPLOYMENT_HOST_AGGREGATE_BYTES = 1_030_618_184
AUXILIARY_RECORD_COUNT = 2
AUXILIARY_AGGREGATE_BYTES = 4_505
HISTORICAL_MEMBERSHIP_SHA256 = (
    "d8514a0b795bd5e588df4a8995b4a8003f5182f9aba4ab4aff10da11e5a99395"
)
AUXILIARY_MEMBERSHIP_SHA256 = (
    "0e15e027e618591b6e4d23b95bfc4df41b8f925445b16c15455998f6cb384881"
)
DEPLOYMENT_INVENTORY_SHA256 = (
    "285381c6bab3c11427e83c37765b9e728bc987af16e6f6b3b5e914d9fe68f1b1"
)
DEPLOYMENT_INVENTORY_BYTES = 49_648
DEPLOYMENT_INVENTORY_FILE_SHA256 = (
    "97fd10669302a2158882d08102b8d306b51b6b3c61bae6941ac957ef787238b2"
)

SOURCE_CLASS_HOST = "HOST_FILE"
SOURCE_CLASS_GIT = "GIT_TRACKED_BLOB"
OWNER_RESEARCH = "RESEARCH_WORKER"
OWNER_SOURCE = "ACCEPTED_SOURCE_STORE"
OWNER_IMAGE = "APPLICATION_IMAGE_ROOT"

_HEX64 = frozenset("0123456789abcdef")
_RENAME_NOREPLACE = 1
_CHUNK_BYTES = 1024 * 1024
_MAX_CONTROL_BYTES = 64 * 1024 * 1024

_TRACKED_PATH_BY_ROLE = {
    "V3 seed manifest": "procurement/seed/manifest.json",
    "V3 seed variants fixture": "procurement/seed/variants.csv",
    "V2 forecast policy": "procurement/config/development_forecast_policy_v2.json",
    "joint-horizon policy": (
        "procurement/config/development_forecast_joint_horizon_policy_v1.json"
    ),
}
_TRACKED_DEPLOYMENT_PATH_BY_ROLE = {
    "V3 seed manifest": (
        "private-research/v3-sources/sha256/"
        "2231ee97b9f01e98ada7da765718f1b217d4c6003a56e27506443f2848556456.json"
    ),
    "V3 seed variants fixture": (
        "private-research/v3-sources/sha256/"
        "dd31f1852c0ea79f8a66f8a34e7b1dac892501183da7f83d4de025a2e1349509.csv"
    ),
    "V2 forecast policy": "procurement/config/development_forecast_policy_v2.json",
    "joint-horizon policy": (
        "procurement/config/development_forecast_joint_horizon_policy_v1.json"
    ),
}

_CHAIN_PATH_BUILDERS: Mapping[str, Callable[[Mapping[str, Any]], str]] = {
    "Corrected input": lambda row: (
        f"private-research/corrected-v3-inputs/{row['logical_id']}.json"
    ),
    "Creation-delta envelope": lambda row: (
        f"private-research/corrected-v3-deltas/{row['logical_id']}.json"
    ),
    "Creation-delta raw source": lambda row: (
        f"private-research/corrected-v3-sources/sha256/"
        f"{row['physical_sha256']}.csv"
    ),
    "Parent V3 input": lambda row: (
        f"private-research/v3-inputs/{row['logical_id']}.json"
    ),
    "Parent V2 input": lambda row: (
        f"private-research/v2-inputs/{row['logical_id']}.json"
    ),
    "Base intake": lambda row: (
        f"private-research/intakes/{row['logical_id']}.json"
    ),
    "Historical extension source": lambda row: (
        f"private-research/v2-sources/sha256/{row['physical_sha256']}.json"
    ),
    "Historical replacement source": lambda row: (
        f"private-research/v2-sources/sha256/{row['physical_sha256']}.json"
    ),
}


class ResearchTransferError(ValueError):
    """The accepted dependency closure or deployment inventory differs."""


@dataclass(frozen=True, order=True)
class InventoryMember:
    relative_path: str
    deployment_path: str
    source_class: str
    bytes: int
    sha256: str
    mode: str
    ownership_role: str
    deployment_mode: str
    deployment_ownership_role: str
    dependency_role: str
    authority_source: str

    def __post_init__(self) -> None:
        _validate_relative_path(self.relative_path)
        _validate_relative_path(self.deployment_path)
        if self.source_class not in {SOURCE_CLASS_HOST, SOURCE_CLASS_GIT}:
            raise ResearchTransferError("inventory member source class differs")
        if not isinstance(self.bytes, int) or isinstance(self.bytes, bool) or self.bytes < 0:
            raise ResearchTransferError("inventory member byte count differs")
        _require_sha256(self.sha256)
        expected_mode = "0600" if self.source_class == SOURCE_CLASS_HOST else "100644"
        expected_owner = OWNER_RESEARCH if self.source_class == SOURCE_CLASS_HOST else OWNER_SOURCE
        if self.mode != expected_mode or self.ownership_role != expected_owner:
            raise ResearchTransferError("inventory member filesystem contract differs")
        materialized_git = (
            self.source_class == SOURCE_CLASS_GIT
            and self.deployment_path.startswith("private-research/")
        )
        expected_deployment_mode = (
            "0600"
            if self.source_class == SOURCE_CLASS_HOST or materialized_git
            else "0644"
        )
        expected_deployment_owner = (
            OWNER_RESEARCH
            if self.source_class == SOURCE_CLASS_HOST or materialized_git
            else OWNER_IMAGE
        )
        if (
            self.deployment_mode != expected_deployment_mode
            or self.deployment_ownership_role != expected_deployment_owner
        ):
            raise ResearchTransferError("inventory deployment filesystem contract differs")
        if (
            self.source_class == SOURCE_CLASS_HOST
            and self.deployment_path != self.relative_path
        ) or (
            self.source_class == SOURCE_CLASS_GIT
            and not materialized_git
            and self.deployment_path != self.relative_path
        ):
            raise ResearchTransferError("inventory deployment path contract differs")
        if not self.dependency_role or not self.authority_source:
            raise ResearchTransferError("inventory member authority is incomplete")

    def to_dict(self) -> dict[str, object]:
        return {
            "authority_source": self.authority_source,
            "bytes": self.bytes,
            "dependency_role": self.dependency_role,
            "deployment_mode": self.deployment_mode,
            "deployment_ownership_role": self.deployment_ownership_role,
            "deployment_path": self.deployment_path,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "source_mode": self.mode,
            "source_ownership_role": self.ownership_role,
            "source_class": self.source_class,
        }


@dataclass(frozen=True, order=True)
class DirectoryContract:
    relative_path: str
    mode: str
    ownership_role: str = OWNER_RESEARCH

    def __post_init__(self) -> None:
        if self.relative_path != ".":
            _validate_relative_path(self.relative_path)
        if self.mode not in {"0700", "0755"} or self.ownership_role != OWNER_RESEARCH:
            raise ResearchTransferError("inventory directory contract differs")

    def to_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "ownership_role": self.ownership_role,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True, order=True)
class UnavailableDependency:
    identity: str
    reason: str
    expected_bytes: int
    expected_sha256: str

    def __post_init__(self) -> None:
        _validate_leaf_name(self.identity)
        if self.reason not in {
            "UNAVAILABLE_ORIGINAL_BYTES",
            "UNRESOLVED_STANDALONE_READER_FILE",
        }:
            raise ResearchTransferError("unavailable dependency reason differs")
        if (
            not isinstance(self.expected_bytes, int)
            or isinstance(self.expected_bytes, bool)
            or self.expected_bytes <= 0
        ):
            raise ResearchTransferError("unavailable dependency byte count differs")
        _require_sha256(self.expected_sha256)

    def to_dict(self) -> dict[str, object]:
        return {
            "expected_bytes": self.expected_bytes,
            "expected_sha256": self.expected_sha256,
            "identity": self.identity,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DeploymentInventory:
    members: tuple[InventoryMember, ...]
    historical_membership_sha256: str
    auxiliary_membership_sha256: str
    directories: tuple[DirectoryContract, ...]
    unavailable: tuple[UnavailableDependency, ...]

    def __post_init__(self) -> None:
        _validate_inventory_structure(self)

    @property
    def identity_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self._body())).hexdigest()

    def _body(self) -> dict[str, object]:
        host = tuple(item for item in self.members if item.source_class == SOURCE_CLASS_HOST)
        tracked = tuple(item for item in self.members if item.source_class == SOURCE_CLASS_GIT)
        return {
            "accepted_authority": {
                "accepted_commit": ACCEPTED_COMMIT,
                "accepted_dependency_inventory_sha256": (
                    ACCEPTED_DEPENDENCY_INVENTORY_SHA256
                ),
                "accepted_transfer_manifest_sha256": (
                    ACCEPTED_TRANSFER_MANIFEST_SHA256
                ),
                "accepted_tree": ACCEPTED_TREE,
                "target_workspace_id": ACCEPTED_TARGET_WORKSPACE_ID,
                "target_workspace_manifest_sha256": (
                    ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256
                ),
            },
            "aggregate_bytes": sum(item.bytes for item in self.members),
            "auxiliary_delta": {
                "aggregate_bytes": AUXILIARY_AGGREGATE_BYTES,
                "membership_sha256": self.auxiliary_membership_sha256,
                "reason": "UNCHANGED_SEMANTIC_READER_REQUIRED_TOP_LEVEL_FILES",
                "record_count": AUXILIARY_RECORD_COUNT,
            },
            "contract": DEPLOYMENT_CONTRACT,
            "directories": [item.to_dict() for item in self.directories],
            "git_aggregate_bytes": sum(item.bytes for item in tracked),
            "git_record_count": len(tracked),
            "host_aggregate_bytes": sum(item.bytes for item in host),
            "host_record_count": len(host),
            "members": [item.to_dict() for item in self.members],
            "record_count": len(self.members),
            "supersedes": {
                "accepted_inventory_sha256": ACCEPTED_DEPENDENCY_INVENTORY_SHA256,
                "aggregate_bytes": HISTORICAL_AGGREGATE_BYTES,
                "contract": HISTORICAL_CONTRACT,
                "derived_available_membership_sha256": (
                    self.historical_membership_sha256
                ),
                "record_count": HISTORICAL_RECORD_COUNT,
                "status": "SUPERSEDED_FOR_DEPLOYMENT_DEPENDENCY_CLOSURE",
            },
            "unavailable": [item.to_dict() for item in self.unavailable],
        }

    def to_dict(self) -> dict[str, object]:
        result = self._body()
        result["identity_sha256"] = self.identity_sha256
        return result

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True)
class ClosureProof:
    record_count: int
    aggregate_bytes: int
    membership_sha256: str
    source_unchanged: bool


@dataclass(frozen=True, order=True)
class ClosureFinding:
    code: str
    relative_path: str


@dataclass(frozen=True)
class ClosureAudit:
    findings: tuple[ClosureFinding, ...]
    verified_record_count: int
    verified_bytes: int
    membership_sha256: str | None
    source_unchanged: bool


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ResearchTransferError("deployment inventory contains duplicate keys")
        result[key] = value
    return result


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], field: str
) -> None:
    if set(value) != expected:
        raise ResearchTransferError(f"{field} schema differs")


def parse_accepted_deployment_inventory(raw: bytes) -> DeploymentInventory:
    """Parse only the code-owned canonical revised deployment inventory."""

    if (
        not isinstance(raw, bytes)
        or len(raw) != DEPLOYMENT_INVENTORY_BYTES
        or hashlib.sha256(raw).hexdigest() != DEPLOYMENT_INVENTORY_FILE_SHA256
    ):
        raise ResearchTransferError("deployment inventory file identity differs")
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchTransferError("deployment inventory is unreadable") from exc
    body = _require_mapping(document, "deployment inventory")
    members_raw = _require_sequence(body.get("members"), "deployment members")
    members: list[InventoryMember] = []
    member_keys = {
        "authority_source",
        "bytes",
        "dependency_role",
        "deployment_mode",
        "deployment_ownership_role",
        "deployment_path",
        "relative_path",
        "sha256",
        "source_class",
        "source_mode",
        "source_ownership_role",
    }
    for value in members_raw:
        row = _require_mapping(value, "deployment member")
        _require_exact_keys(row, member_keys, "deployment member")
        members.append(
            InventoryMember(
                relative_path=row.get("relative_path"),
                deployment_path=row.get("deployment_path"),
                source_class=row.get("source_class"),
                bytes=row.get("bytes"),
                sha256=row.get("sha256"),
                mode=row.get("source_mode"),
                ownership_role=row.get("source_ownership_role"),
                deployment_mode=row.get("deployment_mode"),
                deployment_ownership_role=row.get("deployment_ownership_role"),
                dependency_role=row.get("dependency_role"),
                authority_source=row.get("authority_source"),
            )
        )
    directories_raw = _require_sequence(body.get("directories"), "deployment directories")
    directories: list[DirectoryContract] = []
    for value in directories_raw:
        row = _require_mapping(value, "deployment directory")
        _require_exact_keys(
            row, {"mode", "ownership_role", "relative_path"}, "deployment directory"
        )
        directories.append(
            DirectoryContract(
                relative_path=row.get("relative_path"),
                mode=row.get("mode"),
                ownership_role=row.get("ownership_role"),
            )
        )
    unavailable_raw = _require_sequence(
        body.get("unavailable"), "unavailable dependencies"
    )
    unavailable: list[UnavailableDependency] = []
    for value in unavailable_raw:
        row = _require_mapping(value, "unavailable dependency")
        _require_exact_keys(
            row,
            {"expected_bytes", "expected_sha256", "identity", "reason"},
            "unavailable dependency",
        )
        unavailable.append(
            UnavailableDependency(
                identity=row.get("identity"),
                reason=row.get("reason"),
                expected_bytes=row.get("expected_bytes"),
                expected_sha256=row.get("expected_sha256"),
            )
        )
    supersedes = _require_mapping(body.get("supersedes"), "superseded inventory")
    auxiliary = _require_mapping(body.get("auxiliary_delta"), "auxiliary delta")
    inventory = DeploymentInventory(
        members=tuple(members),
        historical_membership_sha256=supersedes.get(
            "derived_available_membership_sha256"
        ),
        auxiliary_membership_sha256=auxiliary.get("membership_sha256"),
        directories=tuple(directories),
        unavailable=tuple(unavailable),
    )
    _validate_inventory(inventory)
    if inventory.canonical_bytes() != raw:
        raise ResearchTransferError("deployment inventory canonical bytes differ")
    return inventory


def _canonical_json(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("ascii")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise ResearchTransferError("inventory value is not canonical JSON") from exc


def _require_sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX64 for character in value)
    ):
        raise ResearchTransferError("SHA-256 value differs")
    return value


def _validate_leaf_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ResearchTransferError("dependency identity is not a canonical leaf name")
    return value


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ResearchTransferError("inventory path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            any(ord(character) < 32 or ord(character) == 127 for character in part)
            for part in path.parts
        )
    ):
        raise ResearchTransferError("inventory path is not canonical relative POSIX")
    return value


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ResearchTransferError(f"{field} differs")
    return value


def _require_sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ResearchTransferError(f"{field} differs")
    return value


def _safe_root(path: str | Path, *, expected_uid: int | None = None) -> Path:
    root = Path(path)
    try:
        info = root.stat(follow_symlinks=False)
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ResearchTransferError("dependency root is unavailable") from exc
    if (
        not root.is_absolute()
        or root != resolved
        or root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or (expected_uid is not None and info.st_uid != expected_uid)
    ):
        raise ResearchTransferError("dependency root contract differs")
    return root


def _open_relative_file(root: Path, relative_path: str) -> tuple[int, os.stat_result]:
    _validate_relative_path(relative_path)
    descriptors: list[int] = []
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        descriptors.append(current)
        parts = PurePosixPath(relative_path).parts
        for part in parts[:-1]:
            current = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            descriptors.append(current)
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=current,
        )
        info = os.fstat(file_descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_descriptor)
            raise ResearchTransferError("dependency member is not a regular file")
        return file_descriptor, info
    except OSError as exc:
        raise ResearchTransferError("dependency member is unavailable") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _stream_file(
    root: Path,
    member: InventoryMember,
    *,
    expected_uid: int,
    expected_gid: int,
    collect: bool = False,
) -> tuple[bytes | None, tuple[int, ...]]:
    descriptor, before = _open_relative_file(root, member.relative_path)
    try:
        if (
            before.st_uid != expected_uid
            or before.st_gid != expected_gid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size != member.bytes
        ):
            raise ResearchTransferError("dependency member metadata differs")
        digest = hashlib.sha256()
        observed = 0
        chunks: list[bytes] | None = [] if collect else None
        while True:
            chunk = os.read(descriptor, _CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > member.bytes:
                raise ResearchTransferError("dependency member size differs")
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ResearchTransferError("dependency member read failed") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    identity_before = _file_identity(before)
    identity_after = _file_identity(after)
    if (
        identity_before != identity_after
        or observed != member.bytes
        or digest.hexdigest() != member.sha256
    ):
        raise ResearchTransferError("dependency member bytes changed")
    return (b"".join(chunks) if chunks is not None else None), identity_before


def _read_exact_control(
    root: Path,
    member: InventoryMember,
    *,
    expected_uid: int,
    expected_gid: int,
) -> Mapping[str, Any]:
    if member.bytes > _MAX_CONTROL_BYTES:
        raise ResearchTransferError("control document exceeds the fixed bound")
    raw, _ = _stream_file(
        root,
        member,
        expected_uid=expected_uid,
        expected_gid=expected_gid,
        collect=True,
    )
    try:
        value = json.loads(raw or b"")
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchTransferError("control document is unreadable") from exc
    return _require_mapping(value, "control document")


def _host_member(
    path: str,
    size: object,
    digest: object,
    role: str,
    authority: str,
) -> InventoryMember:
    if not isinstance(size, int) or isinstance(size, bool):
        raise ResearchTransferError("accepted byte count differs")
    return InventoryMember(
        relative_path=path,
        deployment_path=path,
        source_class=SOURCE_CLASS_HOST,
        bytes=size,
        sha256=_require_sha256(digest),
        mode="0600",
        ownership_role=OWNER_RESEARCH,
        deployment_mode="0600",
        deployment_ownership_role=OWNER_RESEARCH,
        dependency_role=role,
        authority_source=authority,
    )


def _git_member(
    path: str,
    deployment_path: str,
    size: object,
    digest: object,
    role: str,
) -> InventoryMember:
    if not isinstance(size, int) or isinstance(size, bool):
        raise ResearchTransferError("accepted Git byte count differs")
    return InventoryMember(
        relative_path=path,
        deployment_path=deployment_path,
        source_class=SOURCE_CLASS_GIT,
        bytes=size,
        sha256=_require_sha256(digest),
        mode="100644",
        ownership_role=OWNER_SOURCE,
        deployment_mode=(
            "0600" if deployment_path.startswith("private-research/") else "0644"
        ),
        deployment_ownership_role=(
            OWNER_RESEARCH
            if deployment_path.startswith("private-research/")
            else OWNER_IMAGE
        ),
        dependency_role=role,
        authority_source=(
            f"accepted-commit:{ACCEPTED_COMMIT}:tracked_commitments_requiring_no_separate_copy"
        ),
    )


def _membership_sha256(members: Iterable[InventoryMember]) -> str:
    records = [item.to_dict() for item in sorted(members)]
    return hashlib.sha256(_canonical_json(records)).hexdigest()


def _extract_blob_members(
    intake: Mapping[str, Any],
    *,
    expected_count: int,
    expected_bytes: int,
) -> tuple[InventoryMember, ...]:
    descriptors: dict[str, InventoryMember] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            key = value.get("key")
            if isinstance(key, str) and key.startswith("private-research/blobs/sha256/"):
                member = _host_member(
                    key,
                    value.get("bytes"),
                    value.get("sha256"),
                    "Base intake immutable sidecar",
                    "accepted-base-intake:blob-descriptor",
                )
                existing = descriptors.get(key)
                if existing is not None and existing != member:
                    raise ResearchTransferError("base sidecar descriptor differs")
                descriptors[key] = member
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(intake)
    result = tuple(sorted(descriptors.values()))
    if len(result) != expected_count or sum(item.bytes for item in result) != expected_bytes:
        raise ResearchTransferError("base sidecar aggregate differs")
    return result


def _workspace_members(
    value: Mapping[str, Any],
    *,
    role: str,
) -> tuple[InventoryMember, ...]:
    workspace_id = value.get("workspace_id")
    if not isinstance(workspace_id, str) or len(workspace_id) != 64:
        raise ResearchTransferError("workspace identity differs")
    artifacts = _require_sequence(value.get("artifacts"), f"{role} artifacts")
    members = tuple(
        _host_member(
            f"private-research/workspaces/{workspace_id}/{_validate_leaf_name(item.get('name'))}",
            item.get("bytes"),
            item.get("sha256"),
            f"{role} artifact",
            "accepted-dependency-inventory:workspace-artifact",
        )
        for item in (_require_mapping(raw, f"{role} artifact") for raw in artifacts)
    )
    if len(members) != 5:
        raise ResearchTransferError("workspace artifact count differs")
    return tuple(sorted(members))


def _inventory_file_member(
    package_root: Path,
    name: str,
    size: int,
    digest: str,
) -> InventoryMember:
    return InventoryMember(
        relative_path=name,
        deployment_path=name,
        source_class=SOURCE_CLASS_HOST,
        bytes=size,
        sha256=digest,
        mode="0600",
        ownership_role=OWNER_RESEARCH,
        deployment_mode="0600",
        deployment_ownership_role=OWNER_RESEARCH,
        dependency_role="Accepted package control",
        authority_source="code-owned accepted package identity",
    )


def build_accepted_deployment_inventory(
    *,
    research_root: str | Path,
    accepted_package_root: str | Path,
    git_blob_reader: Callable[[str], bytes],
    expected_source_uid: int,
    expected_source_gid: int,
) -> DeploymentInventory:
    """Rebuild the exact revised deployment inventory from accepted evidence."""

    source_root = _safe_root(research_root, expected_uid=expected_source_uid)
    package_root = _safe_root(accepted_package_root, expected_uid=expected_source_uid)
    transfer_member = _inventory_file_member(
        package_root,
        "manifest.json",
        6_232,
        ACCEPTED_TRANSFER_MANIFEST_SHA256,
    )
    dependency_member = _inventory_file_member(
        package_root,
        "dependency-inventory.json",
        ACCEPTED_DEPENDENCY_INVENTORY_BYTES,
        ACCEPTED_DEPENDENCY_INVENTORY_SHA256,
    )
    transfer = _read_exact_control(
        package_root,
        transfer_member,
        expected_uid=expected_source_uid,
        expected_gid=expected_source_gid,
    )
    dependency = _read_exact_control(
        package_root,
        dependency_member,
        expected_uid=expected_source_uid,
        expected_gid=expected_source_gid,
    )
    if (
        transfer.get("contract") != "BUFFALO_ACCEPTED_SOURCE_TRANSFER_MANIFEST_V1"
        or transfer.get("status") != "PASS_LOCAL_ONLY"
        or transfer.get("lineage")
        != {
            "accepted_commit": ACCEPTED_COMMIT,
            "accepted_tree": ACCEPTED_TREE,
            "implementation_parent": "223c0ae6ba89248223cc9093579e195295b7024e",
            "implementation_parent_tree": "3daaa79a1d622fa99a8625df9e044303b35f9119",
        }
    ):
        raise ResearchTransferError("accepted transfer manifest identity differs")
    companions = _require_sequence(transfer.get("companion_records"), "companion records")
    expected_companion = {
        "bytes": ACCEPTED_DEPENDENCY_INVENTORY_BYTES,
        "file": "dependency-inventory.json",
        "sha256": ACCEPTED_DEPENDENCY_INVENTORY_SHA256,
    }
    if expected_companion not in companions:
        raise ResearchTransferError("dependency inventory is not transfer-anchored")
    if (
        dependency.get("contract") != "BUFFALO_ACCEPTED_SOURCE_DEPENDENCY_INVENTORY_V1"
        or dependency.get("status") != "PASS_WITH_RECORDED_GAPS"
        or dependency.get("source_checkpoint")
        != {"commit": ACCEPTED_COMMIT, "tree": ACCEPTED_TREE}
    ):
        raise ResearchTransferError("accepted dependency inventory identity differs")

    section = _require_mapping(
        dependency.get("private_research_inputs_sidecars_evidence_and_viewer_artifacts"),
        "private research dependency inventory",
    )
    historical_host: list[InventoryMember] = []
    chain = _require_sequence(section.get("accepted_chain"), "accepted chain")
    chain_by_role: dict[str, Mapping[str, Any]] = {}
    for raw in chain:
        row = _require_mapping(raw, "accepted chain row")
        role = row.get("role")
        if (
            not isinstance(role, str)
            or role not in _CHAIN_PATH_BUILDERS
            or role in chain_by_role
            or row.get("availability") != "HASH_VERIFIED"
            or row.get("classification") != "HOST_LOCAL_PRIVATE_HASH_VERIFIED"
        ):
            raise ResearchTransferError("accepted chain membership differs")
        chain_by_role[role] = row
        historical_host.append(
            _host_member(
                _CHAIN_PATH_BUILDERS[role](row),
                row.get("bytes"),
                row.get("physical_sha256"),
                role,
                "accepted-dependency-inventory:accepted_chain",
            )
        )
    if set(chain_by_role) != set(_CHAIN_PATH_BUILDERS):
        raise ResearchTransferError("accepted chain role set differs")

    corrected_workspace = _require_mapping(
        section.get("accepted_corrected_workspace"), "accepted corrected workspace"
    )
    if corrected_workspace.get("workspace_id") != ACCEPTED_TARGET_WORKSPACE_ID:
        raise ResearchTransferError("accepted target workspace differs")
    historical_host.extend(
        _workspace_members(corrected_workspace, role="Accepted corrected workspace")
    )
    parent_workspace = _require_mapping(
        section.get("parent_v3_workspace"), "parent V3 workspace"
    )
    historical_host.extend(_workspace_members(parent_workspace, role="Parent V3 workspace"))

    base_record = chain_by_role["Base intake"]
    base_member = next(item for item in historical_host if item.dependency_role == "Base intake")
    intake = _read_exact_control(
        source_root,
        base_member,
        expected_uid=expected_source_uid,
        expected_gid=expected_source_gid,
    )
    if intake.get("intake_id") != base_record.get("logical_id"):
        raise ResearchTransferError("base intake identity differs")
    sidecar_summary = _require_mapping(section.get("base_sidecars"), "base sidecars")
    historical_host.extend(
        _extract_blob_members(
            intake,
            expected_count=sidecar_summary.get("record_count"),
            expected_bytes=sidecar_summary.get("aggregate_bytes"),
        )
    )

    source_map = _require_mapping(intake.get("sources"), "base intake sources")
    a1 = _require_mapping(source_map.get("a1_review_package"), "A1 review package")
    package_path = _validate_relative_path(a1.get("private_path"))
    originals_path = _validate_relative_path(a1.get("external_evidence_path"))
    if package_path != "a1-package-v1" or originals_path != "a1-original-sources-v1":
        raise ResearchTransferError("A1 source roots differ")
    a1_summary = _require_mapping(section.get("review_evidence_A1"), "A1 evidence")
    root_summary = _require_mapping(a1_summary.get("root_manifest"), "A1 root summary")
    seal_summary = _require_mapping(a1_summary.get("seal"), "A1 seal summary")
    root_member = _host_member(
        f"{package_path}/BUFFALO_REVIEW_PACKAGE_ROOT.json",
        root_summary.get("bytes"),
        root_summary.get("sha256"),
        "A1 root manifest",
        "accepted-dependency-inventory:A1-root",
    )
    seal_member = _host_member(
        f"{package_path}/BUFFALO_REVIEW_PACKAGE_SEAL.json",
        seal_summary.get("bytes"),
        seal_summary.get("sha256"),
        "A1 integrity seal",
        "accepted-dependency-inventory:A1-seal",
    )
    historical_host.extend((root_member, seal_member))
    root_document = _read_exact_control(
        source_root,
        root_member,
        expected_uid=expected_source_uid,
        expected_gid=expected_source_gid,
    )
    seal_document = _read_exact_control(
        source_root,
        seal_member,
        expected_uid=expected_source_uid,
        expected_gid=expected_source_gid,
    )
    if (
        root_document.get("package_id") != a1_summary.get("package_id")
        or seal_document.get("package_id") != a1_summary.get("package_id")
    ):
        raise ResearchTransferError("A1 package identity differs")
    archives = _require_sequence(seal_document.get("archives"), "A1 archives")
    archive_members: list[InventoryMember] = []
    for raw in archives:
        row = _require_mapping(raw, "A1 archive")
        name = _validate_leaf_name(row.get("file"))
        archive_members.append(
            _host_member(
                f"{package_path}/{name}",
                row.get("bytes"),
                row.get("sha256"),
                f"A1 sealed archive: {row.get('role')}",
                "accepted-A1-seal:archives",
            )
        )
    if (
        len(archive_members) != 5
        or sum(item.bytes for item in archive_members)
        != a1_summary.get("five_sealed_archives_aggregate_bytes")
    ):
        raise ResearchTransferError("A1 archive aggregate differs")
    historical_host.extend(archive_members)

    external = _require_sequence(
        root_document.get("external_original_files"), "A1 external originals"
    )
    pdf_members: list[InventoryMember] = []
    unavailable_pages: list[UnavailableDependency] = []
    declared_page_status = _require_mapping(
        a1.get("page_bundle_status"), "A1 page bundle status"
    )
    for raw in external:
        row = _require_mapping(raw, "A1 external original")
        name = _validate_leaf_name(row.get("file"))
        if "physical_pages" in row:
            pdf_members.append(
                _host_member(
                    f"{originals_path}/{name}",
                    row.get("bytes"),
                    row.get("sha256"),
                    "A1 original supplier PDF",
                    "accepted-A1-root:external_original_files",
                )
            )
        else:
            if declared_page_status.get(name) != "UNAVAILABLE_ORIGINAL_BYTES":
                raise ResearchTransferError("A1 unavailable page bundle status differs")
            unavailable_pages.append(
                UnavailableDependency(
                    identity=name,
                    reason="UNAVAILABLE_ORIGINAL_BYTES",
                    expected_bytes=row.get("bytes"),
                    expected_sha256=row.get("sha256"),
                )
            )
    if (
        len(pdf_members) != a1_summary.get("original_pdf_count")
        or sum(item.bytes for item in pdf_members)
        != a1_summary.get("original_pdf_total_bytes")
        or len(unavailable_pages) != a1_summary.get("unavailable_source_page_bundles")
    ):
        raise ResearchTransferError("A1 external evidence aggregate differs")
    historical_host.extend(pdf_members)

    unavailable_readers = tuple(
        UnavailableDependency(
            identity=_validate_leaf_name(row.get("file")),
            reason="UNRESOLVED_STANDALONE_READER_FILE",
            expected_bytes=row.get("bytes"),
            expected_sha256=row.get("sha256"),
        )
        for row in (
            _require_mapping(raw, "A1 review reader")
            for raw in _require_sequence(
                seal_document.get("review_reader_files"), "A1 review readers"
            )
        )
    )
    if len(unavailable_readers) != a1_summary.get("unresolved_standalone_reader_files"):
        raise ResearchTransferError("A1 unresolved reader aggregate differs")

    tracked_rows = _require_sequence(
        section.get("tracked_commitments_requiring_no_separate_copy"),
        "tracked commitments",
    )
    tracked: list[InventoryMember] = []
    for raw in tracked_rows:
        row = _require_mapping(raw, "tracked commitment")
        role = row.get("role")
        if not isinstance(role, str) or role not in _TRACKED_PATH_BY_ROLE:
            raise ResearchTransferError("tracked commitment role differs")
        path = _TRACKED_PATH_BY_ROLE[role]
        try:
            blob = git_blob_reader(path)
        except Exception as exc:
            raise ResearchTransferError("accepted Git blob is unavailable") from exc
        if not isinstance(blob, bytes):
            raise ResearchTransferError("accepted Git blob reader returned invalid bytes")
        member = _git_member(
            path,
            _TRACKED_DEPLOYMENT_PATH_BY_ROLE[role],
            row.get("bytes"),
            row.get("sha256"),
            role,
        )
        if len(blob) != member.bytes or hashlib.sha256(blob).hexdigest() != member.sha256:
            raise ResearchTransferError("accepted Git blob differs")
        tracked.append(member)
    if len({item.relative_path for item in tracked}) != len(_TRACKED_PATH_BY_ROLE):
        raise ResearchTransferError("tracked commitment membership differs")

    from .supplier_review_real_v5 import _AUXILIARY_FILES

    auxiliary = tuple(
        _host_member(
            f"{package_path}/{name}",
            size,
            digest,
            "A1 semantic-reader auxiliary",
            (
                f"accepted-commit:{ACCEPTED_COMMIT}:"
                "supplier_review_real_v5._AUXILIARY_FILES"
            ),
        )
        for name, (size, digest) in sorted(_AUXILIARY_FILES.items())
    )
    historical = tuple(sorted((*historical_host, *tracked)))
    members = tuple(sorted((*historical, *auxiliary)))
    directories = _directory_contracts(members, source_root)
    inventory = DeploymentInventory(
        members=members,
        historical_membership_sha256=_membership_sha256(historical),
        auxiliary_membership_sha256=_membership_sha256(auxiliary),
        directories=directories,
        unavailable=tuple(sorted((*unavailable_pages, *unavailable_readers))),
    )
    _validate_inventory(inventory)
    return inventory


def _directory_contracts(
    members: Sequence[InventoryMember], source_root: Path
) -> tuple[DirectoryContract, ...]:
    paths = {"."}
    for member in members:
        deployment_path = (
            member.relative_path
            if member.source_class == SOURCE_CLASS_HOST
            else member.deployment_path
        )
        if member.source_class == SOURCE_CLASS_GIT and not deployment_path.startswith(
            "private-research/"
        ):
            continue
        parent = PurePosixPath(deployment_path).parent
        while str(parent) != ".":
            paths.add(str(parent))
            parent = parent.parent
    result: list[DirectoryContract] = []
    for relative in sorted(paths):
        target = source_root if relative == "." else source_root / relative
        try:
            info = target.stat(follow_symlinks=False)
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise ResearchTransferError("dependency directory is unavailable") from exc
        if (
            target.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or resolved != target
            or stat.S_IMODE(info.st_mode) not in {0o700, 0o755}
        ):
            raise ResearchTransferError("dependency directory contract differs")
        result.append(
            DirectoryContract(
                relative_path=relative,
                mode=f"{stat.S_IMODE(info.st_mode):04o}",
            )
        )
    return tuple(result)


def _validate_inventory_structure(inventory: DeploymentInventory) -> None:
    members = tuple(sorted(inventory.members))
    if members != inventory.members:
        raise ResearchTransferError("deployment inventory is not canonically ordered")
    if len({item.relative_path for item in members}) != len(members):
        raise ResearchTransferError("deployment inventory contains duplicate paths")
    if len({item.relative_path.casefold() for item in members}) != len(members):
        raise ResearchTransferError("deployment inventory contains a path collision")
    if len({item.deployment_path for item in members}) != len(members):
        raise ResearchTransferError("deployment inventory contains duplicate destinations")
    if len({item.deployment_path.casefold() for item in members}) != len(members):
        raise ResearchTransferError("deployment inventory contains a destination collision")
    if tuple(sorted(inventory.directories)) != inventory.directories:
        raise ResearchTransferError("directory inventory is not canonically ordered")
    if len({item.relative_path for item in inventory.directories}) != len(
        inventory.directories
    ):
        raise ResearchTransferError("directory inventory contains duplicate paths")
    if len({item.relative_path.casefold() for item in inventory.directories}) != len(
        inventory.directories
    ):
        raise ResearchTransferError("directory inventory contains a path collision")
    directories = {item.relative_path for item in inventory.directories}
    materialized = {
        item.deployment_path
        for item in members
        if item.source_class == SOURCE_CLASS_HOST
        or (
            item.source_class == SOURCE_CLASS_GIT
            and item.deployment_path.startswith("private-research/")
        )
    }
    if directories.intersection(materialized):
        raise ResearchTransferError("deployment file and directory paths collide")
    for path in materialized:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            if str(parent) not in directories:
                raise ResearchTransferError("deployment directory closure is incomplete")
            parent = parent.parent
    if "." not in directories:
        raise ResearchTransferError("deployment root directory contract is missing")


def _validate_inventory(inventory: DeploymentInventory) -> None:
    _validate_inventory_structure(inventory)
    members = inventory.members
    host = tuple(item for item in members if item.source_class == SOURCE_CLASS_HOST)
    tracked = tuple(item for item in members if item.source_class == SOURCE_CLASS_GIT)
    auxiliary = tuple(
        item for item in host if item.dependency_role == "A1 semantic-reader auxiliary"
    )
    historical = tuple(item for item in members if item not in auxiliary)
    if (
        len(members) != DEPLOYMENT_RECORD_COUNT
        or sum(item.bytes for item in members) != DEPLOYMENT_AGGREGATE_BYTES
        or len(host) != DEPLOYMENT_HOST_RECORD_COUNT
        or sum(item.bytes for item in host) != DEPLOYMENT_HOST_AGGREGATE_BYTES
        or len(tracked) != GIT_RECORD_COUNT
        or sum(item.bytes for item in tracked) != GIT_AGGREGATE_BYTES
        or len(historical) != HISTORICAL_RECORD_COUNT
        or sum(item.bytes for item in historical) != HISTORICAL_AGGREGATE_BYTES
        or len(auxiliary) != AUXILIARY_RECORD_COUNT
        or sum(item.bytes for item in auxiliary) != AUXILIARY_AGGREGATE_BYTES
        or inventory.identity_sha256 != DEPLOYMENT_INVENTORY_SHA256
    ):
        raise ResearchTransferError("deployment inventory aggregate differs")
    if (
        _membership_sha256(historical) != inventory.historical_membership_sha256
        or _membership_sha256(auxiliary) != inventory.auxiliary_membership_sha256
        or inventory.historical_membership_sha256 != HISTORICAL_MEMBERSHIP_SHA256
        or inventory.auxiliary_membership_sha256 != AUXILIARY_MEMBERSHIP_SHA256
    ):
        raise ResearchTransferError("deployment inventory membership commitment differs")
    if len(inventory.unavailable) != 6:
        raise ResearchTransferError("unavailable dependency inventory differs")


def validate_host_dependency_closure(
    inventory: DeploymentInventory,
    *,
    research_root: str | Path,
    expected_source_uid: int,
    expected_source_gid: int,
) -> ClosureProof:
    """Hash every authorized host record and prove source metadata is stable."""

    audit = audit_host_dependency_closure(
        inventory,
        research_root=research_root,
        expected_source_uid=expected_source_uid,
        expected_source_gid=expected_source_gid,
    )
    if audit.findings:
        summary = ",".join(
            f"{item.code}:{item.relative_path}" for item in audit.findings
        )
        raise ResearchTransferError(f"dependency closure failed ({summary})")
    return ClosureProof(
        record_count=audit.verified_record_count,
        aggregate_bytes=audit.verified_bytes,
        membership_sha256=audit.membership_sha256 or "",
        source_unchanged=audit.source_unchanged,
    )


def audit_host_dependency_closure(
    inventory: DeploymentInventory,
    *,
    research_root: str | Path,
    expected_source_uid: int,
    expected_source_gid: int,
) -> ClosureAudit:
    """Return every safe-to-report host-closure finding in one bounded pass."""

    _validate_inventory(inventory)
    root = _safe_root(research_root, expected_uid=expected_source_uid)
    host = tuple(item for item in inventory.members if item.source_class == SOURCE_CLASS_HOST)
    before: dict[str, tuple[int, ...]] = {}
    verified: list[InventoryMember] = []
    findings: set[ClosureFinding] = set()
    for member in host:
        try:
            _, identity = _stream_file(
                root,
                member,
                expected_uid=expected_source_uid,
                expected_gid=expected_source_gid,
            )
        except ResearchTransferError:
            findings.add(ClosureFinding("MEMBER_INVALID", member.relative_path))
        else:
            before[member.relative_path] = identity
            verified.append(member)
    for directory in inventory.directories:
        target = root if directory.relative_path == "." else root / directory.relative_path
        try:
            info = target.stat(follow_symlinks=False)
            valid = (
                not target.is_symlink()
                and stat.S_ISDIR(info.st_mode)
                and info.st_uid == expected_source_uid
                and info.st_gid == expected_source_gid
                and f"{stat.S_IMODE(info.st_mode):04o}" == directory.mode
            )
        except OSError:
            valid = False
        if not valid:
            findings.add(ClosureFinding("DIRECTORY_INVALID", directory.relative_path))
    for member in tuple(verified):
        try:
            _, identity = _stream_file(
                root,
                member,
                expected_uid=expected_source_uid,
                expected_gid=expected_source_gid,
            )
        except ResearchTransferError:
            findings.add(ClosureFinding("MEMBER_CHANGED", member.relative_path))
            verified.remove(member)
            continue
        if before[member.relative_path] != identity:
            findings.add(ClosureFinding("MEMBER_CHANGED", member.relative_path))
            verified.remove(member)
    clean = not findings and len(verified) == len(host)
    return ClosureAudit(
        findings=tuple(sorted(findings)),
        verified_record_count=len(verified),
        verified_bytes=sum(item.bytes for item in verified),
        membership_sha256=_membership_sha256(host) if clean else None,
        source_unchanged=clean,
    )


def _rename_noreplace(source: Path, destination: Path) -> None:
    _validate_leaf_name(source.name)
    _validate_leaf_name(destination.name)
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ResearchTransferError("atomic no-replace rename is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    source_parent: int | None = None
    destination_parent: int | None = None
    try:
        source_parent = os.open(
            source.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        destination_parent = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        result = renameat2(
            source_parent,
            os.fsencode(source.name),
            destination_parent,
            os.fsencode(destination.name),
            _RENAME_NOREPLACE,
        )
        if result != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise ResearchTransferError("deployment destination already exists")
            if error == errno.EXDEV:
                raise ResearchTransferError("deployment staging crosses filesystems")
            raise ResearchTransferError("atomic deployment promotion failed")
    except OSError as exc:
        raise ResearchTransferError("atomic deployment promotion failed") from exc
    finally:
        # No fallible cleanup may turn a committed rename into an ambiguous
        # failure.  Linux close errors do not roll back renameat2.
        for descriptor in (destination_parent, source_parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _validate_private_parent(
    path: Path, *, expected_uid: int, expected_gid: int | None = None
) -> os.stat_result:
    try:
        info = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ResearchTransferError("deployment parent is unavailable") from exc
    if (
        not path.is_absolute()
        or path != resolved
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != expected_uid
        or (expected_gid is not None and info.st_gid != expected_gid)
    ):
        raise ResearchTransferError("deployment parent contract differs")
    return info


def _copy_member(
    root: Path,
    member: InventoryMember,
    destination: Path,
    *,
    source_uid: int,
    source_gid: int,
    destination_uid: int,
    destination_gid: int,
) -> None:
    source_descriptor, before = _open_relative_file(root, member.relative_path)
    destination_descriptor: int | None = None
    try:
        if (
            before.st_uid != source_uid
            or before.st_gid != source_gid
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size != member.bytes
        ):
            raise ResearchTransferError("dependency member metadata differs")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(source_descriptor, _CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > member.bytes:
                raise ResearchTransferError("dependency member size differs")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                view = view[written:]
        after = os.fstat(source_descriptor)
        if (
            _file_identity(before) != _file_identity(after)
            or observed != member.bytes
            or digest.hexdigest() != member.sha256
        ):
            raise ResearchTransferError("dependency source changed during copy")
        os.fchmod(destination_descriptor, 0o600)
        os.fchown(destination_descriptor, destination_uid, destination_gid)
        os.fsync(destination_descriptor)
    except OSError as exc:
        raise ResearchTransferError("dependency member copy failed") from exc
    finally:
        os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _stage_and_promote_host_closure(
    inventory: DeploymentInventory,
    *,
    research_root: str | Path,
    staging_parent: str | Path,
    destination_parent: str | Path,
    destination_name: str,
    source_uid: int,
    source_gid: int,
    destination_uid: int,
    destination_gid: int,
) -> Path:
    """Copy the exact host allowlist and atomically publish one local release."""

    _validate_inventory(inventory)
    _validate_leaf_name(destination_name)
    root = _safe_root(research_root, expected_uid=source_uid)
    staging = Path(staging_parent)
    destination_root = Path(destination_parent)
    staging_info = _validate_private_parent(
        staging, expected_uid=os.geteuid(), expected_gid=os.getegid()
    )
    destination_info = _validate_private_parent(
        destination_root,
        expected_uid=destination_uid,
        expected_gid=destination_gid,
    )
    if staging_info.st_dev != destination_info.st_dev:
        raise ResearchTransferError("deployment staging crosses filesystems")
    final = destination_root / destination_name
    if final.exists() or final.is_symlink():
        raise ResearchTransferError("deployment destination already exists")
    temporary = Path(tempfile.mkdtemp(prefix=".research-release.", dir=staging))
    promoted = False
    try:
        os.chmod(temporary, 0o700)
        payload = temporary / "payload"
        payload.mkdir(mode=0o700)
        for directory in inventory.directories:
            if directory.relative_path == ".":
                continue
            target = payload / directory.relative_path
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, int(directory.mode, 8))
        for member in inventory.members:
            if member.source_class != SOURCE_CLASS_HOST:
                continue
            _copy_member(
                root,
                member,
                payload / member.relative_path,
                source_uid=source_uid,
                source_gid=source_gid,
                destination_uid=destination_uid,
                destination_gid=destination_gid,
            )
        manifest_path = temporary / "deployment-inventory.json"
        manifest_descriptor = os.open(
            manifest_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            raw = inventory.canonical_bytes()
            view = memoryview(raw)
            while view:
                written = os.write(manifest_descriptor, view)
                view = view[written:]
            os.fchmod(manifest_descriptor, 0o600)
            os.fchown(manifest_descriptor, destination_uid, destination_gid)
            os.fsync(manifest_descriptor)
        finally:
            os.close(manifest_descriptor)
        _validate_staged_payload(
            inventory,
            payload,
            expected_uid=destination_uid,
            expected_gid=destination_gid,
        )
        for path in sorted(
            (item for item in temporary.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            mode = 0o700
            try:
                relative = path.relative_to(payload).as_posix()
            except ValueError:
                relative = "."
            directory = next(
                (item for item in inventory.directories if item.relative_path == relative),
                None,
            )
            if directory is not None:
                mode = int(directory.mode, 8)
            os.chmod(path, mode)
            os.chown(path, destination_uid, destination_gid)
        os.chmod(temporary, 0o700)
        os.chown(temporary, destination_uid, destination_gid)
        _rename_noreplace(temporary, final)
        promoted = True
        return final
    finally:
        if not promoted and temporary.exists():
            shutil.rmtree(temporary)


def _validate_staged_payload(
    inventory: DeploymentInventory,
    payload: Path,
    *,
    expected_uid: int,
    expected_gid: int,
    include_materialized_git: bool = False,
) -> None:
    expected_directories = {
        item.relative_path: item for item in inventory.directories
    }
    root_contract = expected_directories.get(".")
    root_info = payload.stat(follow_symlinks=False)
    if (
        root_contract is None
        or payload.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != expected_uid
        or root_info.st_gid != expected_gid
        or f"{stat.S_IMODE(root_info.st_mode):04o}" != root_contract.mode
    ):
        raise ResearchTransferError("staged payload root metadata differs")
    expected_files = {
        item.deployment_path: item
        for item in inventory.members
        if item.source_class == SOURCE_CLASS_HOST
        or (
            include_materialized_git
            and item.source_class == SOURCE_CLASS_GIT
            and item.deployment_path.startswith("private-research/")
        )
    }
    observed_files: set[str] = set()
    observed_directories = {"."}
    for path in payload.rglob("*"):
        relative = path.relative_to(payload).as_posix()
        info = path.stat(follow_symlinks=False)
        if path.is_symlink():
            raise ResearchTransferError("staged payload contains a symlink")
        if stat.S_ISDIR(info.st_mode):
            directory = expected_directories.get(relative)
            if (
                directory is None
                or info.st_uid != expected_uid
                or info.st_gid != expected_gid
                or f"{stat.S_IMODE(info.st_mode):04o}" != directory.mode
            ):
                raise ResearchTransferError("staged payload directory metadata differs")
            observed_directories.add(relative)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ResearchTransferError("staged payload contains a non-regular file")
        member = expected_files.get(relative)
        if member is None:
            raise ResearchTransferError("staged payload contains an unrelated file")
        observed_files.add(relative)
        descriptor, before = _open_relative_file(payload, relative)
        try:
            if (
                before.st_uid != expected_uid
                or before.st_gid != expected_gid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
                or before.st_size != member.bytes
            ):
                raise ResearchTransferError("staged payload metadata differs")
            digest = hashlib.sha256()
            observed = 0
            while True:
                chunk = os.read(descriptor, _CHUNK_BYTES)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > member.bytes:
                    raise ResearchTransferError("staged payload size differs")
                digest.update(chunk)
            after = os.fstat(descriptor)
        except OSError as exc:
            raise ResearchTransferError("staged payload read failed") from exc
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if (
            _file_identity(before) != _file_identity(after)
            or observed != member.bytes
            or digest.hexdigest() != member.sha256
        ):
            raise ResearchTransferError("staged payload bytes differ")
    if observed_files != set(expected_files):
        raise ResearchTransferError("staged payload membership differs")
    if observed_directories != set(expected_directories):
        raise ResearchTransferError("staged payload directory membership differs")


__all__ = [
    "AUXILIARY_AGGREGATE_BYTES",
    "AUXILIARY_RECORD_COUNT",
    "ClosureAudit",
    "ClosureFinding",
    "ClosureProof",
    "DEPLOYMENT_AGGREGATE_BYTES",
    "DEPLOYMENT_CONTRACT",
    "DEPLOYMENT_HOST_AGGREGATE_BYTES",
    "DEPLOYMENT_HOST_RECORD_COUNT",
    "DEPLOYMENT_INVENTORY_BYTES",
    "DEPLOYMENT_INVENTORY_FILE_SHA256",
    "DEPLOYMENT_INVENTORY_SHA256",
    "DEPLOYMENT_RECORD_COUNT",
    "DeploymentInventory",
    "DirectoryContract",
    "GIT_AGGREGATE_BYTES",
    "GIT_RECORD_COUNT",
    "HISTORICAL_AGGREGATE_BYTES",
    "HISTORICAL_CONTRACT",
    "HISTORICAL_RECORD_COUNT",
    "InventoryMember",
    "OWNER_IMAGE",
    "ResearchTransferError",
    "SOURCE_CLASS_GIT",
    "SOURCE_CLASS_HOST",
    "UnavailableDependency",
    "audit_host_dependency_closure",
    "build_accepted_deployment_inventory",
    "parse_accepted_deployment_inventory",
    "validate_host_dependency_closure",
]
