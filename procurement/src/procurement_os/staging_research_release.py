"""Atomic local assembly of one complete private-research staging release."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Callable

from . import staging_source_bundle as source_bundle
from .staging_research_transfer import (
    DEPLOYMENT_INVENTORY_BYTES,
    DEPLOYMENT_INVENTORY_FILE_SHA256,
    DEPLOYMENT_AGGREGATE_BYTES,
    DeploymentInventory,
    OWNER_IMAGE,
    ResearchTransferError,
    SOURCE_CLASS_GIT,
    SOURCE_CLASS_HOST,
    _copy_member,
    _file_identity,
    _rename_noreplace,
    _safe_root,
    _validate_leaf_name,
    _validate_inventory,
    _validate_private_parent,
    _validate_staged_payload,
    parse_accepted_deployment_inventory,
    validate_host_dependency_closure,
)
from .staging_source_bundle import SourceStoreProof


class ResearchReleaseError(ValueError):
    """The complete research release could not be proven and promoted."""


_APPLICATION_IMAGE_UID = 0
_APPLICATION_IMAGE_GID = 0
_RECOVERY_NAME = re.compile(
    r"\A\.(research-release|research-control|research-release-validation)\."
    r"[a-z0-9_]{8}\Z"
)
_RECOVERY_MAX_ENTRIES = 100_000
_RECOVERY_MAX_BYTES = DEPLOYMENT_AGGREGATE_BYTES + 256 * 1024**2
_RECOVERY_GIT_PACK = re.compile(
    r"objects/pack/(pack-[0-9a-f]{40})\.(idx|pack|rev)\Z"
)
_RECOVERY_GIT_TEMP_PACK = re.compile(
    r"objects/pack/tmp_(pack|idx|rev)_[A-Za-z0-9]{6}\Z"
)
_RECOVERY_GIT_FILEMODE_PROBE = re.compile(r"t[A-Za-z0-9]{6}\Z")


def _accepted_application_root() -> Path:
    """Return the repo-shaped application root used by the semantic readers."""

    return Path(__file__).resolve().parents[3]


def _open_application_member(
    root: Path,
    relative_path: str,
    *,
    expected_uid: int,
    expected_gid: int,
) -> tuple[int, os.stat_result]:
    descriptors: list[int] = []
    try:
        current = os.open(
            root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        descriptors.append(current)
        parts = PurePosixPath(relative_path).parts
        for part in parts[:-1]:
            current = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current,
            )
            descriptors.append(current)
            info = os.fstat(current)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != expected_uid
                or info.st_gid != expected_gid
                or stat.S_IMODE(info.st_mode) != 0o755
            ):
                raise ResearchReleaseError(
                    "application image directory contract differs"
                )
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=current,
        )
        info = os.fstat(file_descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_descriptor)
            raise ResearchReleaseError(
                "application image member is not a regular file"
            )
        return file_descriptor, info
    except OSError as exc:
        raise ResearchReleaseError("application image member is unavailable") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_exact_file(
    path: Path,
    raw: bytes,
    *,
    expected_bytes: int,
    expected_sha256: str,
    owner_uid: int,
    owner_gid: int,
    mode: int = 0o600,
) -> None:
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ResearchReleaseError("Git-backed deployment member differs")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            mode,
        )
        try:
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fchmod(descriptor, mode)
            os.fchown(descriptor, owner_uid, owner_gid)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ResearchReleaseError("release member write failed") from exc


def _validate_and_materialize_git_members(
    inventory: DeploymentInventory,
    *,
    repository: Path,
    payload: Path,
    runtime_root: Path,
    owner_uid: int,
    owner_gid: int,
    contract: source_bundle._BundleContract,
    materialize: bool = True,
) -> None:
    executable = source_bundle._trusted_git()
    environment = source_bundle._git_environment(runtime_root)
    for member in inventory.members:
        if member.source_class != SOURCE_CLASS_GIT:
            continue
        tree = source_bundle._run(
            [
                *source_bundle._git_base(executable, repository),
                "ls-tree",
                "-z",
                "--full-tree",
                contract.tip,
                "--",
                member.relative_path,
            ],
            environment=environment,
            cwd=runtime_root,
        ).stdout
        entries = [item for item in tree.split(b"\0") if item]
        expected_suffix = f"\t{member.relative_path}".encode("utf-8")
        if (
            len(entries) != 1
            or not entries[0].startswith(b"100644 blob ")
            or not entries[0].endswith(expected_suffix)
        ):
            raise ResearchReleaseError("Git-backed deployment member mode differs")
        result = source_bundle._run(
            [
                *source_bundle._git_base(executable, repository),
                "show",
                f"{contract.tip}:{member.relative_path}",
            ],
            environment=environment,
            cwd=runtime_root,
        )
        if (
            len(result.stdout) != member.bytes
            or hashlib.sha256(result.stdout).hexdigest() != member.sha256
        ):
            raise ResearchReleaseError("Git-backed deployment member differs")
        if (
            not materialize
            or not member.deployment_path.startswith("private-research/")
        ):
            continue
        _write_exact_file(
            payload / member.deployment_path,
            result.stdout,
            expected_bytes=member.bytes,
            expected_sha256=member.sha256,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )


def _validate_publish_parent(
    path: Path, *, expected_uid: int, expected_gid: int
) -> os.stat_result:
    try:
        info = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ResearchReleaseError("release publish parent is unavailable") from exc
    if (
        not path.is_absolute()
        or path != resolved
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o710
        or info.st_uid != expected_uid
        or info.st_gid != expected_gid
    ):
        raise ResearchReleaseError("release publish parent contract differs")
    return info


def _validate_application_members(
    inventory: DeploymentInventory,
    *,
    application_root: str | Path,
    expected_uid: int,
    expected_gid: int,
) -> None:
    """Prove the exact image-backed bytes consumed by semantic replay."""

    try:
        _validate_inventory(inventory)
    except ResearchTransferError as exc:
        raise ResearchReleaseError(str(exc)) from exc
    root = Path(application_root)
    try:
        root_info = root.stat(follow_symlinks=False)
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ResearchReleaseError("application image root is unavailable") from exc
    if (
        not root.is_absolute()
        or root != resolved
        or root.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != expected_uid
        or root_info.st_gid != expected_gid
        or stat.S_IMODE(root_info.st_mode) != 0o755
    ):
        raise ResearchReleaseError("application image root contract differs")

    members = tuple(
        member
        for member in inventory.members
        if member.deployment_ownership_role == OWNER_IMAGE
    )
    if len(members) != 2:
        raise ResearchReleaseError("application image member inventory differs")
    for member in members:
        descriptor, before = _open_application_member(
            root,
            member.deployment_path,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
        )
        try:
            if (
                before.st_uid != expected_uid
                or before.st_gid != expected_gid
                or f"{stat.S_IMODE(before.st_mode):04o}" != member.deployment_mode
                or before.st_nlink != 1
                or before.st_size != member.bytes
            ):
                raise ResearchReleaseError("application image member metadata differs")
            digest = hashlib.sha256()
            observed = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                observed += len(chunk)
                if observed > member.bytes:
                    raise ResearchReleaseError("application image member size differs")
                digest.update(chunk)
            after = os.fstat(descriptor)
        except OSError as exc:
            raise ResearchReleaseError("application image member read failed") from exc
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
            raise ResearchReleaseError("application image member bytes differ")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_release(root: Path) -> None:
    for parent, directories, files in os.walk(root, topdown=False, followlinks=False):
        parent_path = Path(parent)
        for name in files:
            descriptor = os.open(
                parent_path / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        for name in directories:
            path = parent_path / name
            if path.is_symlink():
                raise ResearchReleaseError("release contains a symlink")
        descriptor = os.open(
            parent_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _collect_recovery_tree(
    root: Path, *, aggregate: list[int]
) -> dict[str, os.stat_result]:
    observed: dict[str, os.stat_result] = {}
    stack = [root]
    while stack:
        path = stack.pop()
        try:
            info = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ResearchReleaseError(
                "unpublished recovery entry is unavailable"
            ) from exc
        relative = "." if path == root else path.relative_to(root).as_posix()
        observed[relative] = info
        aggregate[0] += 1
        if aggregate[0] > _RECOVERY_MAX_ENTRIES:
            raise ResearchReleaseError("unpublished recovery entry limit differs")
        if stat.S_ISDIR(info.st_mode):
            try:
                stack.extend(path.iterdir())
            except OSError as exc:
                raise ResearchReleaseError(
                    "unpublished recovery directory is unavailable"
                ) from exc
        elif stat.S_ISREG(info.st_mode):
            aggregate[1] += info.st_size
            if aggregate[1] > _RECOVERY_MAX_BYTES:
                raise ResearchReleaseError("unpublished recovery byte limit differs")
        else:
            raise ResearchReleaseError("unpublished recovery entry type differs")
    linked: dict[tuple[int, int], list[str]] = {}
    for relative, info in observed.items():
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            linked.setdefault((info.st_dev, info.st_ino), []).append(relative)
    for paths in linked.values():
        infos = [observed[path] for path in paths]
        if not _is_internal_git_install_link(paths, infos):
            raise ResearchReleaseError("unpublished recovery file differs")
    return observed


def _is_internal_git_install_link(
    paths: list[str], infos: list[os.stat_result]
) -> bool:
    if len(paths) != 2 or any(info.st_nlink != 2 for info in infos):
        return False
    temp_kind: str | None = None
    final_kind: str | None = None
    for relative in paths:
        without_root = relative.removeprefix("source.git/")
        if without_root == relative:
            return False
        temporary = _RECOVERY_GIT_TEMP_PACK.fullmatch(without_root)
        final = _RECOVERY_GIT_PACK.fullmatch(without_root)
        if temporary is not None:
            if temp_kind is not None:
                return False
            temp_kind = temporary.group(1)
        elif final is not None and final.group(2) in {"pack", "idx", "rev"}:
            if final_kind is not None:
                return False
            final_kind = final.group(2)
        else:
            return False
    return temp_kind is not None and temp_kind == final_kind


def _require_recovery_entry(
    info: os.stat_result,
    *,
    directory: bool,
    states: set[tuple[int, int, int]],
    maximum_bytes: int | None = None,
) -> None:
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected_type(info.st_mode)
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) not in states
        or (
            maximum_bytes is not None
            and not directory
            and info.st_size > maximum_bytes
        )
    ):
        raise ResearchReleaseError("unpublished recovery metadata differs")


def _validate_payload_recovery(
    records: dict[str, os.stat_result],
    *,
    inventory: DeploymentInventory,
    supervisor_owner: tuple[int, int],
    destination_owner: tuple[int, int],
) -> None:
    _require_recovery_entry(
        records["payload"],
        directory=True,
        states={(*supervisor_owner, 0o700), (*destination_owner, 0o700)},
    )
    directories = {
        f"payload/{item.relative_path}": int(item.mode, 8)
        for item in inventory.directories
        if item.relative_path != "."
    }
    members = {
        f"payload/{item.deployment_path}": item
        for item in inventory.members
        if item.deployment_ownership_role != OWNER_IMAGE
    }
    for relative, info in records.items():
        if not relative.startswith("payload/"):
            continue
        if relative in directories:
            _require_recovery_entry(
                info,
                directory=True,
                # mkdir(parents=True) can be interrupted before the exact
                # inventory mode is applied; cleanup itself normalizes every
                # directory to 0700 before removal.
                states={
                    (*supervisor_owner, directories[relative]),
                    (*supervisor_owner, 0o700),
                    (*supervisor_owner, 0o755),
                    (*destination_owner, directories[relative]),
                    (*destination_owner, 0o700),
                },
            )
        elif relative in members:
            _require_recovery_entry(
                info,
                directory=False,
                states={(*supervisor_owner, 0o600), (*destination_owner, 0o600)},
                maximum_bytes=members[relative].bytes,
            )
        else:
            raise ResearchReleaseError("unpublished payload membership differs")


def _git_recovery_directory_allowed(relative: str) -> bool:
    fixed = {
        "source.git",
        "source.git/objects",
        "source.git/objects/info",
        "source.git/objects/pack",
        "source.git/refs",
        "source.git/refs/heads",
        "source.git/refs/tags",
    }
    accepted_ref = PurePosixPath(source_bundle.ACCEPTED_REF)
    parent = accepted_ref.parent
    while str(parent) not in {".", "refs/heads"}:
        fixed.add(f"source.git/{parent}")
        parent = parent.parent
    return relative in fixed


def _git_recovery_file_limit(relative: str) -> int | None:
    without_root = relative.removeprefix("source.git/")
    if without_root in {"HEAD", "HEAD.lock", "config", "config.lock"}:
        return 64 * 1024
    if (
        _RECOVERY_GIT_PACK.fullmatch(without_root)
        or _RECOVERY_GIT_TEMP_PACK.fullmatch(without_root)
    ):
        return source_bundle.ACCEPTED_BUNDLE_BYTES
    if without_root in {
        source_bundle.ACCEPTED_REF,
        f"{source_bundle.ACCEPTED_REF}.lock",
    }:
        return 64 * 1024
    if _RECOVERY_GIT_FILEMODE_PROBE.fullmatch(without_root):
        return 0
    return None


def _validate_source_store_recovery(
    records: dict[str, os.stat_result],
    *,
    supervisor_uid: int,
    supervisor_gid: int,
    destination_gid: int,
) -> None:
    supervisor_owner = (supervisor_uid, supervisor_gid)
    sealed_owner = (supervisor_uid, destination_gid)
    final_stems: set[str] = set()
    final_kinds: set[str] = set()
    temporary_kinds: set[str] = set()
    final_paths: dict[str, tuple[str, os.stat_result]] = {}
    temporary_paths: dict[str, tuple[str, os.stat_result]] = {}
    filemode_probes = 0
    for relative, info in records.items():
        if relative == "source.git" or relative.startswith("source.git/"):
            if stat.S_ISDIR(info.st_mode):
                if not _git_recovery_directory_allowed(relative):
                    raise ResearchReleaseError(
                        "unpublished source store membership differs"
                    )
                _require_recovery_entry(
                    info,
                    directory=True,
                    states={
                        (*supervisor_owner, 0o550),
                        (*supervisor_owner, 0o700),
                        (*supervisor_owner, 0o755),
                        (*sealed_owner, 0o550),
                        (*sealed_owner, 0o700),
                    },
                )
            else:
                maximum = _git_recovery_file_limit(relative)
                if maximum is None:
                    raise ResearchReleaseError(
                        "unpublished source store membership differs"
                    )
                without_root = relative.removeprefix("source.git/")
                final = _RECOVERY_GIT_PACK.fullmatch(without_root)
                temporary = _RECOVERY_GIT_TEMP_PACK.fullmatch(without_root)
                if final is not None:
                    if final.group(2) in final_kinds:
                        raise ResearchReleaseError(
                            "unpublished source store pack state differs"
                        )
                    final_stems.add(final.group(1))
                    final_kinds.add(final.group(2))
                    final_paths[final.group(2)] = (relative, info)
                elif temporary is not None:
                    if temporary.group(1) in temporary_kinds:
                        raise ResearchReleaseError(
                            "unpublished source store pack state differs"
                        )
                    temporary_kinds.add(temporary.group(1))
                    temporary_paths[temporary.group(1)] = (relative, info)
                if temporary is not None or (
                    final is not None and final.group(2) == "rev"
                ):
                    states = {
                        (*supervisor_owner, 0o400),
                        (*supervisor_owner, 0o444),
                    }
                elif final is not None:
                    states = {
                        (*supervisor_owner, 0o400),
                        (*supervisor_owner, 0o440),
                        (*supervisor_owner, 0o444),
                        (*sealed_owner, 0o440),
                    }
                elif without_root.endswith(".lock"):
                    states = {
                        (*supervisor_owner, 0o600),
                        (*supervisor_owner, 0o644),
                    }
                elif _RECOVERY_GIT_FILEMODE_PROBE.fullmatch(without_root):
                    filemode_probes += 1
                    if filemode_probes > 1:
                        raise ResearchReleaseError(
                            "unpublished source store probe state differs"
                        )
                    states = {(*supervisor_owner, 0o600)}
                else:
                    states = {
                        (*supervisor_owner, 0o440),
                        (*supervisor_owner, 0o600),
                        (*supervisor_owner, 0o644),
                        (*sealed_owner, 0o440),
                    }
                    if without_root == "config":
                        states.add((*supervisor_owner, 0o700))
                        states.add((*supervisor_owner, 0o744))
                _require_recovery_entry(
                    info,
                    directory=False,
                    states=states,
                    maximum_bytes=maximum,
                )
    if len(final_stems) > 1:
        raise ResearchReleaseError("unpublished source store pack state differs")
    installing = final_kinds & temporary_kinds
    if len(installing) > 1:
        raise ResearchReleaseError("unpublished source store install state differs")
    for kind in installing:
        _final_path, final_info = final_paths[kind]
        _temporary_path, temporary_info = temporary_paths[kind]
        if (
            (final_info.st_dev, final_info.st_ino)
            != (temporary_info.st_dev, temporary_info.st_ino)
            or final_info.st_nlink != 2
            or temporary_info.st_nlink != 2
        ):
            raise ResearchReleaseError("unpublished source store install state differs")


def _validate_release_recovery(
    records: dict[str, os.stat_result],
    *,
    inventory: DeploymentInventory,
    supervisor_uid: int,
    supervisor_gid: int,
    destination_uid: int,
    destination_gid: int,
) -> None:
    supervisor_owner = (supervisor_uid, supervisor_gid)
    _require_recovery_entry(
        records["."],
        directory=True,
        states={
            (*supervisor_owner, 0o700),
            (*supervisor_owner, 0o710),
            (supervisor_uid, destination_gid, 0o700),
            (supervisor_uid, destination_gid, 0o710),
        },
    )
    top_level = {relative.split("/", 1)[0] for relative in records if relative != "."}
    if not top_level.issubset(
        {"deployment-inventory.json", "payload", "source.git"}
    ):
        raise ResearchReleaseError("unpublished release membership differs")
    manifest = records.get("deployment-inventory.json")
    if manifest is not None:
        _require_recovery_entry(
            manifest,
            directory=False,
            states={
                (*supervisor_owner, 0o400),
                (*supervisor_owner, 0o440),
                (supervisor_uid, destination_gid, 0o440),
            },
            maximum_bytes=len(inventory.canonical_bytes()),
        )
    if "payload" in records:
        _validate_payload_recovery(
            records,
            inventory=inventory,
            supervisor_owner=supervisor_owner,
            destination_owner=(destination_uid, destination_gid),
        )
    if "source.git" in records:
        _validate_source_store_recovery(
            records,
            supervisor_uid=supervisor_uid,
            supervisor_gid=supervisor_gid,
            destination_gid=destination_gid,
        )


def _validate_control_recovery(
    records: dict[str, os.stat_result],
    *,
    supervisor_uid: int,
    supervisor_gid: int,
) -> None:
    owner = (supervisor_uid, supervisor_gid)
    _require_recovery_entry(
        records["."], directory=True, states={(*owner, 0o700)}
    )
    top_level = {
        relative.split("/", 1)[0] for relative in records if relative != "."
    }
    if len(top_level) > 1:
        raise ResearchReleaseError("unpublished control phase differs")
    if not top_level:
        return
    name = next(iter(top_level))
    if name == "accepted-source-recheck.bundle":
        if set(records) != {".", name}:
            raise ResearchReleaseError("unpublished control membership differs")
        _require_recovery_entry(
            records[name],
            directory=False,
            states={(*owner, 0o600)},
            maximum_bytes=source_bundle.ACCEPTED_BUNDLE_BYTES,
        )
        return
    if name == "extract":
        allowed = {".", "extract", "extract/home", "extract/home/.config"}
    elif re.fullmatch(r"\.git-runtime\.[a-z0-9_]{8}", name):
        allowed = {
            ".",
            name,
            f"{name}/accepted.bundle",
            f"{name}/empty-template",
            f"{name}/home",
            f"{name}/home/.config",
        }
    else:
        raise ResearchReleaseError("unpublished control membership differs")
    if not set(records).issubset(allowed):
        raise ResearchReleaseError("unpublished control membership differs")
    for relative, info in records.items():
        if relative == ".":
            continue
        if relative.endswith("accepted.bundle"):
            _require_recovery_entry(
                info,
                directory=False,
                states={(*owner, 0o600)},
                maximum_bytes=source_bundle.ACCEPTED_BUNDLE_BYTES,
            )
        else:
            _require_recovery_entry(
                info, directory=True, states={(*owner, 0o700)}
            )


def _validate_validation_recovery(
    records: dict[str, os.stat_result],
    *,
    supervisor_uid: int,
    supervisor_gid: int,
) -> None:
    owner = (supervisor_uid, supervisor_gid)
    allowed = {
        ".",
        "members",
        "members/home",
        "members/home/.config",
        "store",
        "store/home",
        "store/home/.config",
    }
    if not set(records).issubset(allowed):
        raise ResearchReleaseError("release validation membership differs")
    for info in records.values():
        _require_recovery_entry(
            info, directory=True, states={(*owner, 0o700)}
        )


def _validate_recovery_tree(
    root: Path,
    *,
    kind: str,
    inventory: DeploymentInventory,
    supervisor_uid: int,
    supervisor_gid: int,
    destination_uid: int,
    destination_gid: int,
    aggregate: list[int],
) -> None:
    records = _collect_recovery_tree(root, aggregate=aggregate)
    if kind == "research-release":
        _validate_release_recovery(
            records,
            inventory=inventory,
            supervisor_uid=supervisor_uid,
            supervisor_gid=supervisor_gid,
            destination_uid=destination_uid,
            destination_gid=destination_gid,
        )
    elif kind == "research-control":
        _validate_control_recovery(
            records,
            supervisor_uid=supervisor_uid,
            supervisor_gid=supervisor_gid,
        )
    elif kind == "research-release-validation":
        _validate_validation_recovery(
            records,
            supervisor_uid=supervisor_uid,
            supervisor_gid=supervisor_gid,
        )
    else:
        raise ResearchReleaseError("unpublished recovery kind differs")


def reconcile_unpublished_research_staging(
    inventory: DeploymentInventory,
    *,
    staging_parent: str | Path,
    supervisor_uid: int,
    supervisor_gid: int,
    destination_uid: int,
    destination_gid: int,
) -> int:
    """Remove only fully proven crash-left unpublished staging trees.

    Validation covers the complete dedicated parent before the first removal,
    so an unknown name, owner, mode, link, or object type leaves every entry in
    place for diagnosis.  Published releases live under a different parent and
    are never candidates for this operation.
    """

    try:
        _validate_inventory(inventory)
        parent = Path(staging_parent)
        _validate_private_parent(
            parent,
            expected_uid=supervisor_uid,
            expected_gid=supervisor_gid,
        )
    except ResearchTransferError as exc:
        raise ResearchReleaseError(str(exc)) from exc
    try:
        paths = sorted(parent.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ResearchReleaseError("unpublished staging is unavailable") from exc
    candidates: list[Path] = []
    kinds: set[str] = set()
    aggregate = [0, 0]
    for path in paths:
        match = _RECOVERY_NAME.fullmatch(path.name)
        if match is None:
            raise ResearchReleaseError("unpublished staging membership differs")
        if path.is_symlink() or not path.is_dir():
            raise ResearchReleaseError("unpublished staging entry differs")
        kind = match.group(1)
        if kind in kinds:
            raise ResearchReleaseError("unpublished staging phase differs")
        kinds.add(kind)
        _validate_recovery_tree(
            path,
            kind=kind,
            inventory=inventory,
            supervisor_uid=supervisor_uid,
            supervisor_gid=supervisor_gid,
            destination_uid=destination_uid,
            destination_gid=destination_gid,
            aggregate=aggregate,
        )
        candidates.append(path)
    if kinds not in (
        set(),
        {"research-release"},
        {"research-control"},
        {"research-release", "research-control"},
        {"research-release-validation"},
    ):
        raise ResearchReleaseError("unpublished staging phase differs")
    failures: list[Exception] = []
    for path in candidates:
        try:
            source_bundle._remove_private_tree(path)
        except Exception as exc:
            failures.append(exc)
    try:
        _fsync_directory(parent)
    except Exception as exc:
        failures.append(exc)
    if failures:
        raise ResearchReleaseError("unpublished staging cleanup failed") from failures[0]
    return len(candidates)


def _stage_release_with_contract(
    inventory: DeploymentInventory,
    *,
    research_root: str | Path,
    bundle_path: str | Path,
    staging_parent: str | Path,
    destination_parent: str | Path,
    destination_name: str,
    source_uid: int,
    source_gid: int,
    destination_uid: int,
    destination_gid: int,
    bundle_contract: source_bundle._BundleContract,
    manifest_bytes: int,
    manifest_sha256: str,
    pre_publish_validator: Callable[[], None] | None = None,
) -> tuple[Path, SourceStoreProof]:
    _validate_inventory(inventory)
    validate_host_dependency_closure(
        inventory,
        research_root=research_root,
        expected_source_uid=source_uid,
        expected_source_gid=source_gid,
    )
    try:
        _validate_leaf_name(destination_name)
    except ResearchTransferError as exc:
        raise ResearchReleaseError("release destination name differs") from exc
    source_root = _safe_root(research_root, expected_uid=source_uid)
    staging = Path(staging_parent)
    destination = Path(destination_parent)
    staging_info = _validate_private_parent(
        staging, expected_uid=os.geteuid(), expected_gid=os.getegid()
    )
    destination_info = _validate_publish_parent(
        destination, expected_uid=os.geteuid(), expected_gid=destination_gid
    )
    if staging_info.st_dev != destination_info.st_dev:
        raise ResearchReleaseError("release staging crosses filesystems")
    final = destination / destination_name
    if final.exists() or final.is_symlink():
        raise ResearchReleaseError("release destination already exists")
    temporary = Path(tempfile.mkdtemp(prefix=".research-release.", dir=staging))
    control = Path(tempfile.mkdtemp(prefix=".research-control.", dir=staging))
    promoted = False
    control_removed = False
    try:
        os.chmod(temporary, 0o700)
        os.chmod(control, 0o700)
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
                source_root,
                member,
                payload / member.deployment_path,
                source_uid=source_uid,
                source_gid=source_gid,
                destination_uid=destination_uid,
                destination_gid=destination_gid,
            )
        repository = temporary / "source.git"
        proof = source_bundle._build_unpublished_with_contract(
            bundle_path,
            repository_path=repository,
            scratch_root=control,
            owner_uid=os.geteuid(),
            owner_gid=destination_gid,
            source_uid=source_uid,
            source_gid=source_gid,
            contract=bundle_contract,
            sealed_directory_mode=0o550,
            sealed_file_mode=0o440,
        )
        extraction_runtime = control / "extract"
        extraction_runtime.mkdir(mode=0o700)
        _validate_and_materialize_git_members(
            inventory,
            repository=repository,
            payload=payload,
            runtime_root=extraction_runtime,
            owner_uid=destination_uid,
            owner_gid=destination_gid,
            contract=bundle_contract,
        )
        source_bundle._remove_private_tree(extraction_runtime)
        manifest = temporary / "deployment-inventory.json"
        manifest_raw = inventory.canonical_bytes()
        _write_exact_file(
            manifest,
            manifest_raw,
            expected_bytes=manifest_bytes,
            expected_sha256=manifest_sha256,
            owner_uid=os.geteuid(),
            owner_gid=destination_gid,
            mode=0o440,
        )
        for path in sorted(
            (item for item in payload.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            relative = path.relative_to(payload).as_posix()
            directory = next(
                item for item in inventory.directories if item.relative_path == relative
            )
            os.chmod(path, int(directory.mode, 8))
            os.chown(path, destination_uid, destination_gid)
        os.chmod(payload, 0o700)
        os.chown(payload, destination_uid, destination_gid)
        _validate_staged_payload(
            inventory,
            payload,
            expected_uid=destination_uid,
            expected_gid=destination_gid,
            include_materialized_git=True,
        )
        if {item.name for item in temporary.iterdir()} != {
            "deployment-inventory.json",
            "payload",
            "source.git",
        }:
            raise ResearchReleaseError("release top-level membership differs")
        os.chmod(temporary, 0o710)
        os.chown(temporary, os.geteuid(), destination_gid)
        _fsync_release(temporary)
        # The build can take long enough for an otherwise valid source to drift
        # after its first descriptor-stable read.  Recheck every external input
        # at the last reversible boundary; a mismatch leaves no final release.
        validate_host_dependency_closure(
            inventory,
            research_root=source_root,
            expected_source_uid=source_uid,
            expected_source_gid=source_gid,
        )
        bundle_recheck = control / "accepted-source-recheck.bundle"
        source_bundle._copy_bundle_from_descriptor(
            Path(bundle_path),
            bundle_recheck,
            contract=bundle_contract,
            source_uid=source_uid,
            source_gid=source_gid,
        )
        bundle_recheck.unlink()
        if pre_publish_validator is not None:
            pre_publish_validator()
        source_bundle._remove_private_tree(control)
        control_removed = True
        _fsync_directory(staging)
        _rename_noreplace(temporary, final)
        promoted = True
        try:
            _fsync_directory(staging)
            _fsync_directory(destination)
        except OSError as exc:
            raise ResearchReleaseError(
                "research release was published but parent durability proof failed"
            ) from exc
        return final, proof
    except (ResearchTransferError, source_bundle.SourceBundleError) as exc:
        raise ResearchReleaseError(str(exc)) from exc
    finally:
        if not control_removed and control.exists():
            source_bundle._remove_private_tree(control)
        if not promoted and temporary.exists():
            source_bundle._remove_private_tree(temporary)


def _read_release_manifest(
    path: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
    expected_uid: int,
    expected_gid: int,
) -> bytes:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
    except OSError as exc:
        raise ResearchReleaseError("release inventory is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o440
            or before.st_uid != expected_uid
            or before.st_gid != expected_gid
            or before.st_nlink != 1
            or before.st_size != expected_bytes
        ):
            raise ResearchReleaseError("release inventory metadata differs")
        chunks: list[bytes] = []
        observed = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > expected_bytes:
                raise ResearchReleaseError("release inventory size differs")
            chunks.append(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        _file_identity(before) != _file_identity(after)
        or observed != expected_bytes
        or digest.hexdigest() != expected_sha256
    ):
        raise ResearchReleaseError("release inventory identity differs")
    return b"".join(chunks)


def _require_release_root(
    root: Path, *, supervisor_uid: int, destination_gid: int
) -> None:
    try:
        root_info = root.stat(follow_symlinks=False)
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ResearchReleaseError("research release is unavailable") from exc
    if (
        not root.is_absolute()
        or root != resolved
        or root.is_symlink()
        or not stat.S_ISDIR(root_info.st_mode)
        or stat.S_IMODE(root_info.st_mode) != 0o710
        or root_info.st_uid != supervisor_uid
        or root_info.st_gid != destination_gid
    ):
        raise ResearchReleaseError("research release root contract differs")


def _validate_release_with_contract(
    release_path: str | Path,
    *,
    inventory: DeploymentInventory,
    manifest_bytes: int,
    manifest_sha256: str,
    destination_uid: int,
    destination_gid: int,
    supervisor_uid: int,
    supervisor_gid: int,
    bundle_contract: source_bundle._BundleContract,
    validation_parent: str | Path,
) -> SourceStoreProof:
    try:
        _validate_inventory(inventory)
    except ResearchTransferError as exc:
        raise ResearchReleaseError(str(exc)) from exc
    root = Path(release_path)
    _require_release_root(
        root, supervisor_uid=supervisor_uid, destination_gid=destination_gid
    )
    if {item.name for item in root.iterdir()} != {
        "deployment-inventory.json",
        "payload",
        "source.git",
    }:
        raise ResearchReleaseError("release top-level membership differs")
    raw = _read_release_manifest(
        root / "deployment-inventory.json",
        expected_bytes=manifest_bytes,
        expected_sha256=manifest_sha256,
        expected_uid=supervisor_uid,
        expected_gid=destination_gid,
    )
    if raw != inventory.canonical_bytes():
        raise ResearchReleaseError("release inventory canonical bytes differ")
    try:
        _validate_staged_payload(
            inventory,
            root / "payload",
            expected_uid=destination_uid,
            expected_gid=destination_gid,
            include_materialized_git=True,
        )
    except ResearchTransferError as exc:
        raise ResearchReleaseError(str(exc)) from exc
    try:
        validation_root = Path(validation_parent)
        _validate_private_parent(
            validation_root,
            expected_uid=supervisor_uid,
            expected_gid=supervisor_gid,
        )
    except ResearchTransferError as exc:
        raise ResearchReleaseError(str(exc)) from exc
    runtime = Path(
        tempfile.mkdtemp(prefix=".research-release-validation.", dir=validation_root)
    )
    try:
        os.chmod(runtime, 0o700)
        store_runtime = runtime / "store"
        store_runtime.mkdir(mode=0o700)
        proof = source_bundle._validate_store_with_contract(
            root / "source.git",
            contract=bundle_contract,
            executable=source_bundle._trusted_git(),
            environment=source_bundle._git_environment(store_runtime),
            cwd=store_runtime,
            sealed=True,
            owner_uid=supervisor_uid,
            owner_gid=destination_gid,
            sealed_directory_mode=0o550,
            sealed_file_mode=0o440,
        )
        member_runtime = runtime / "members"
        member_runtime.mkdir(mode=0o700)
        _validate_and_materialize_git_members(
            inventory,
            repository=root / "source.git",
            payload=root / "payload",
            runtime_root=member_runtime,
            owner_uid=destination_uid,
            owner_gid=destination_gid,
            contract=bundle_contract,
            materialize=False,
        )
        return proof
    except (ResearchTransferError, source_bundle.SourceBundleError) as exc:
        raise ResearchReleaseError(str(exc)) from exc
    finally:
        source_bundle._remove_private_tree(runtime)


def validate_accepted_research_release(
    release_path: str | Path,
    *,
    destination_uid: int,
    destination_gid: int,
    supervisor_uid: int,
    supervisor_gid: int,
    validation_parent: str | Path,
) -> tuple[DeploymentInventory, SourceStoreProof]:
    """Revalidate the exact accepted release without trusting caller expectations."""

    root = Path(release_path)
    _require_release_root(
        root, supervisor_uid=supervisor_uid, destination_gid=destination_gid
    )
    raw = _read_release_manifest(
        root / "deployment-inventory.json",
        expected_bytes=DEPLOYMENT_INVENTORY_BYTES,
        expected_sha256=DEPLOYMENT_INVENTORY_FILE_SHA256,
        expected_uid=supervisor_uid,
        expected_gid=destination_gid,
    )
    try:
        inventory = parse_accepted_deployment_inventory(raw)
    except ResearchTransferError as exc:
        raise ResearchReleaseError(str(exc)) from exc
    reconcile_unpublished_research_staging(
        inventory,
        staging_parent=validation_parent,
        supervisor_uid=supervisor_uid,
        supervisor_gid=supervisor_gid,
        destination_uid=destination_uid,
        destination_gid=destination_gid,
    )
    proof = _validate_release_with_contract(
        root,
        inventory=inventory,
        manifest_bytes=DEPLOYMENT_INVENTORY_BYTES,
        manifest_sha256=DEPLOYMENT_INVENTORY_FILE_SHA256,
        destination_uid=destination_uid,
        destination_gid=destination_gid,
        supervisor_uid=supervisor_uid,
        supervisor_gid=supervisor_gid,
        bundle_contract=source_bundle._ACCEPTED_CONTRACT,
        validation_parent=validation_parent,
    )
    _validate_application_members(
        inventory,
        application_root=_accepted_application_root(),
        expected_uid=_APPLICATION_IMAGE_UID,
        expected_gid=_APPLICATION_IMAGE_GID,
    )
    return inventory, proof


def stage_accepted_research_release(
    inventory: DeploymentInventory,
    *,
    research_root: str | Path,
    bundle_path: str | Path,
    staging_parent: str | Path,
    destination_parent: str | Path,
    destination_name: str,
    source_uid: int,
    source_gid: int,
    destination_uid: int,
    destination_gid: int,
) -> tuple[Path, SourceStoreProof]:
    """Build and atomically promote payload, inventory, and accepted Git store."""

    reconcile_unpublished_research_staging(
        inventory,
        staging_parent=staging_parent,
        supervisor_uid=os.geteuid(),
        supervisor_gid=os.getegid(),
        destination_uid=destination_uid,
        destination_gid=destination_gid,
    )
    _validate_application_members(
        inventory,
        application_root=_accepted_application_root(),
        expected_uid=_APPLICATION_IMAGE_UID,
        expected_gid=_APPLICATION_IMAGE_GID,
    )
    return _stage_release_with_contract(
        inventory,
        research_root=research_root,
        bundle_path=bundle_path,
        staging_parent=staging_parent,
        destination_parent=destination_parent,
        destination_name=destination_name,
        source_uid=source_uid,
        source_gid=source_gid,
        destination_uid=destination_uid,
        destination_gid=destination_gid,
        bundle_contract=source_bundle._ACCEPTED_CONTRACT,
        manifest_bytes=DEPLOYMENT_INVENTORY_BYTES,
        manifest_sha256=DEPLOYMENT_INVENTORY_FILE_SHA256,
        pre_publish_validator=lambda: _validate_application_members(
            inventory,
            application_root=_accepted_application_root(),
            expected_uid=_APPLICATION_IMAGE_UID,
            expected_gid=_APPLICATION_IMAGE_GID,
        ),
    )


__all__ = [
    "reconcile_unpublished_research_staging",
    "ResearchReleaseError",
    "stage_accepted_research_release",
    "validate_accepted_research_release",
]
