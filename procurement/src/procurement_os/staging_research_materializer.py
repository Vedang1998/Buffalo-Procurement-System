"""One-shot publication of the exact accepted research release.

This module is an explicit LOCAL-acceptance operator.  It is never called by
the ordinary staging bootstrap: bootstrap validates an already published
release and remains incapable of importing private source material.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import sys

from . import staging_source_bundle
from .staging_bootstrap import (
    RESEARCH_ACCOUNT,
    RUNTIME_ROOT,
    SUPERVISOR_ACCOUNT,
    _validate_root_identity_and_accounts,
    _validate_volume_mount,
)
from .staging_research_release import (
    ResearchReleaseError,
    reconcile_unpublished_research_staging,
    stage_accepted_research_release,
    validate_accepted_research_release,
)
from .staging_research_transfer import (
    DEPLOYMENT_INVENTORY_BYTES,
    DEPLOYMENT_INVENTORY_FILE_SHA256,
    DeploymentInventory,
    ResearchTransferError,
    parse_accepted_deployment_inventory,
)


MATERIALIZER_CONTRACT = "BUFFALO_ACCEPTED_RESEARCH_MATERIALIZER_V1"


class ResearchMaterializerError(RuntimeError):
    """The accepted release could not be proven and published."""


@dataclass(frozen=True)
class _MaterializerContract:
    volume_root: Path
    transfer_root: Path
    release_root: Path
    validation_parent: Path
    research_root: Path
    inventory_path: Path
    bundle_path: Path
    source_uid: int
    source_gid: int
    supervisor_uid: int
    supervisor_gid: int
    destination_uid: int
    destination_gid: int
    validate_host_identity: bool = True
    validate_mounts: bool = True


_PRODUCTION_CONTRACT = _MaterializerContract(
    volume_root=Path("/data"),
    transfer_root=Path("/data/transfer"),
    release_root=Path("/data/research-release"),
    validation_parent=Path("/run/buffalo-research-materializer"),
    research_root=Path("/mnt/buffalo-accepted-research"),
    inventory_path=Path("/mnt/deployment-inventory.json"),
    bundle_path=Path(
        "/mnt/buffalo-procurement-os-accepted-source-608929ad.bundle"
    ),
    source_uid=1000,
    source_gid=1000,
    supervisor_uid=SUPERVISOR_ACCOUNT.uid,
    supervisor_gid=SUPERVISOR_ACCOUNT.gid,
    destination_uid=RESEARCH_ACCOUNT.uid,
    destination_gid=RESEARCH_ACCOUNT.gid,
)

_ALLOWED_TOP_LEVEL = frozenset({"research-release", "transfer"})
_CHUNK_BYTES = 1024 * 1024


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
    )


def _require_directory(
    path: Path,
    *,
    uid: int,
    gid: int,
    mode: int,
) -> os.stat_result:
    try:
        info = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ResearchMaterializerError("materializer directory is unavailable") from exc
    if (
        not path.is_absolute()
        or path != resolved
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_uid, info.st_gid) != (uid, gid)
        or stat.S_IMODE(info.st_mode) != mode
    ):
        raise ResearchMaterializerError("materializer directory differs")
    return info


def _read_exact_file(
    path: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
    uid: int,
    gid: int,
    retain: bool,
) -> bytes | None:
    descriptor = -1
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    observed = 0
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        before = os.fstat(descriptor)
        named_before = path.stat(follow_symlinks=False)
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (before.st_uid, before.st_gid) != (uid, gid)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != expected_bytes
            or (before.st_dev, before.st_ino)
            != (named_before.st_dev, named_before.st_ino)
        ):
            raise ResearchMaterializerError("materializer ingress file differs")
        while observed <= expected_bytes:
            block = os.read(
                descriptor,
                min(_CHUNK_BYTES, expected_bytes + 1 - observed),
            )
            if not block:
                break
            observed += len(block)
            digest.update(block)
            if retain:
                chunks.append(block)
        after = os.fstat(descriptor)
        named_after = path.stat(follow_symlinks=False)
    except ResearchMaterializerError:
        raise
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer ingress file is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        observed != expected_bytes
        or digest.hexdigest() != expected_sha256
        or _identity(before) != _identity(after)
        or (after.st_size, after.st_mtime_ns)
        != (before.st_size, before.st_mtime_ns)
        or (after.st_dev, after.st_ino)
        != (named_after.st_dev, named_after.st_ino)
    ):
        raise ResearchMaterializerError("materializer ingress file changed")
    return b"".join(chunks) if retain else None


def _require_read_only_ingress(paths: tuple[Path, ...]) -> None:
    try:
        flags = tuple(os.statvfs(path).f_flag for path in paths)
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer ingress mount is unavailable"
        ) from exc
    if any(not value & os.ST_RDONLY for value in flags):
        raise ResearchMaterializerError("materializer ingress mount differs")


def _load_ingress(contract: _MaterializerContract) -> DeploymentInventory:
    if contract.validate_mounts:
        _require_read_only_ingress(
            (
                contract.research_root,
                contract.inventory_path,
                contract.bundle_path,
            )
        )
    _require_directory(
        contract.research_root,
        uid=contract.source_uid,
        gid=contract.source_gid,
        mode=0o700,
    )
    raw = _read_exact_file(
        contract.inventory_path,
        expected_bytes=DEPLOYMENT_INVENTORY_BYTES,
        expected_sha256=DEPLOYMENT_INVENTORY_FILE_SHA256,
        uid=contract.source_uid,
        gid=contract.source_gid,
        retain=True,
    )
    _read_exact_file(
        contract.bundle_path,
        expected_bytes=staging_source_bundle.ACCEPTED_BUNDLE_BYTES,
        expected_sha256=staging_source_bundle.ACCEPTED_BUNDLE_SHA256,
        uid=contract.source_uid,
        gid=contract.source_gid,
        retain=False,
    )
    try:
        return parse_accepted_deployment_inventory(raw or b"")
    except ResearchTransferError as exc:
        raise ResearchMaterializerError("materializer inventory differs") from exc


def _top_level(volume_root: Path) -> dict[str, Path]:
    try:
        entries = {item.name: item for item in volume_root.iterdir()}
    except OSError as exc:
        raise ResearchMaterializerError("materializer volume is unavailable") from exc
    if not set(entries).issubset(_ALLOWED_TOP_LEVEL):
        raise ResearchMaterializerError("materializer volume membership differs")
    for item in entries.values():
        try:
            info = item.stat(follow_symlinks=False)
        except OSError as exc:
            raise ResearchMaterializerError(
                "materializer volume member is unavailable"
            ) from exc
        if item.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise ResearchMaterializerError("materializer volume member differs")
    return entries


def _require_volume_state(
    contract: _MaterializerContract,
    *,
    volume_info: os.stat_result,
    entries: dict[str, Path],
) -> None:
    state = (
        volume_info.st_uid,
        volume_info.st_gid,
        stat.S_IMODE(volume_info.st_mode),
    )
    allowed = {(contract.supervisor_uid, contract.supervisor_gid, 0o755)}
    if "transfer" in entries:
        allowed.update(
            {
                (contract.supervisor_uid, contract.supervisor_gid, 0o710),
                (contract.supervisor_uid, contract.destination_gid, 0o710),
            }
        )
        _require_directory(
            contract.transfer_root,
            uid=contract.supervisor_uid,
            gid=contract.supervisor_gid,
            mode=0o700,
        )
    if state not in allowed:
        raise ResearchMaterializerError("materializer volume metadata differs")


def _ensure_transfer(
    contract: _MaterializerContract,
    *,
    volume_descriptor: int,
) -> None:
    try:
        os.mkdir(contract.transfer_root, 0o700)
        os.chown(
            contract.transfer_root,
            contract.supervisor_uid,
            contract.supervisor_gid,
            follow_symlinks=False,
        )
        os.chmod(contract.transfer_root, 0o700, follow_symlinks=False)
        os.fsync(volume_descriptor)
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer transfer root could not be created"
        ) from exc
    _require_directory(
        contract.transfer_root,
        uid=contract.supervisor_uid,
        gid=contract.supervisor_gid,
        mode=0o700,
    )


def _set_publish_parent(
    contract: _MaterializerContract,
    *,
    volume_descriptor: int,
) -> None:
    try:
        info = os.fstat(volume_descriptor)
        state = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        if state == (contract.supervisor_uid, contract.supervisor_gid, 0o755):
            os.fchmod(volume_descriptor, 0o710)
            os.fsync(volume_descriptor)
            state = (contract.supervisor_uid, contract.supervisor_gid, 0o710)
        if state == (contract.supervisor_uid, contract.supervisor_gid, 0o710):
            os.fchown(
                volume_descriptor,
                contract.supervisor_uid,
                contract.destination_gid,
            )
            os.fchmod(volume_descriptor, 0o710)
            os.fsync(volume_descriptor)
            state = (contract.supervisor_uid, contract.destination_gid, 0o710)
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer publish parent transition failed"
        ) from exc
    if state != (contract.supervisor_uid, contract.destination_gid, 0o710):
        raise ResearchMaterializerError("materializer publish parent differs")


def _restore_volume_parent(
    contract: _MaterializerContract,
    *,
    volume_descriptor: int,
) -> None:
    try:
        os.fchown(
            volume_descriptor,
            contract.supervisor_uid,
            contract.supervisor_gid,
        )
        os.fchmod(volume_descriptor, 0o755)
        os.fsync(volume_descriptor)
        info = os.fstat(volume_descriptor)
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer volume restoration failed"
        ) from exc
    if (
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
    ) != (contract.supervisor_uid, contract.supervisor_gid, 0o755):
        raise ResearchMaterializerError("materializer volume restoration differs")


def _reconcile(
    inventory: DeploymentInventory,
    contract: _MaterializerContract,
    *,
    parent: Path,
) -> None:
    try:
        reconcile_unpublished_research_staging(
            inventory,
            staging_parent=parent,
            supervisor_uid=contract.supervisor_uid,
            supervisor_gid=contract.supervisor_gid,
            destination_uid=contract.destination_uid,
            destination_gid=contract.destination_gid,
        )
    except ResearchReleaseError as exc:
        raise ResearchMaterializerError(
            "materializer recovery state differs"
        ) from exc
    try:
        if tuple(parent.iterdir()):
            raise ResearchMaterializerError("materializer recovery state differs")
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer recovery state is unavailable"
        ) from exc


def _validate_release(
    contract: _MaterializerContract,
) -> DeploymentInventory:
    try:
        inventory, _proof = validate_accepted_research_release(
            contract.release_root,
            destination_uid=contract.destination_uid,
            destination_gid=contract.destination_gid,
            supervisor_uid=contract.supervisor_uid,
            supervisor_gid=contract.supervisor_gid,
            validation_parent=contract.validation_parent,
        )
    except ResearchReleaseError as exc:
        raise ResearchMaterializerError(
            "materializer published release differs"
        ) from exc
    try:
        if tuple(contract.validation_parent.iterdir()):
            raise ResearchMaterializerError(
                "materializer validation cleanup differs"
            )
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer validation parent is unavailable"
        ) from exc
    return inventory


def _remove_transfer(
    contract: _MaterializerContract,
    *,
    volume_descriptor: int,
) -> None:
    _require_directory(
        contract.transfer_root,
        uid=contract.supervisor_uid,
        gid=contract.supervisor_gid,
        mode=0o700,
    )
    try:
        if tuple(contract.transfer_root.iterdir()):
            raise ResearchMaterializerError("materializer transfer root is not empty")
        contract.transfer_root.rmdir()
        os.fsync(volume_descriptor)
    except ResearchMaterializerError:
        raise
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer transfer cleanup failed"
        ) from exc


def _final_state(
    contract: _MaterializerContract,
    *,
    volume_descriptor: int,
) -> None:
    volume_info = _require_directory(
        contract.volume_root,
        uid=contract.supervisor_uid,
        gid=contract.supervisor_gid,
        mode=0o755,
    )
    entries = _top_level(contract.volume_root)
    if set(entries) != {"research-release"}:
        raise ResearchMaterializerError("materializer final membership differs")
    try:
        descriptor_info = os.fstat(volume_descriptor)
        if (volume_info.st_dev, volume_info.st_ino) != (
            descriptor_info.st_dev,
            descriptor_info.st_ino,
        ):
            raise ResearchMaterializerError("materializer final volume differs")
        os.fsync(volume_descriptor)
    except ResearchMaterializerError:
        raise
    except OSError as exc:
        raise ResearchMaterializerError(
            "materializer final durability proof failed"
        ) from exc


def materialize_accepted_research_release(
    *,
    contract: _MaterializerContract = _PRODUCTION_CONTRACT,
) -> None:
    """Publish once, or fully validate the exact already-published release."""

    if contract.validate_host_identity:
        _validate_root_identity_and_accounts(runtime_root=RUNTIME_ROOT)
    if contract.validate_mounts:
        try:
            _validate_volume_mount(contract.volume_root, local_acceptance=True)
        except Exception as exc:
            raise ResearchMaterializerError(
                "materializer volume mount differs"
            ) from exc
    _require_directory(
        contract.validation_parent,
        uid=contract.supervisor_uid,
        gid=contract.supervisor_gid,
        mode=0o700,
    )

    volume_descriptor = -1
    restored = True
    try:
        volume_descriptor = os.open(
            contract.volume_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            fcntl.flock(volume_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ResearchMaterializerError(
                "another research materializer is active"
            ) from exc
        volume_before = os.fstat(volume_descriptor)
        entries = _top_level(contract.volume_root)
        _require_volume_state(
            contract,
            volume_info=volume_before,
            entries=entries,
        )
        restored = (
            volume_before.st_uid,
            volume_before.st_gid,
            stat.S_IMODE(volume_before.st_mode),
        ) == (contract.supervisor_uid, contract.supervisor_gid, 0o755)

        if "research-release" in entries:
            inventory = _validate_release(contract)
            if "transfer" in entries:
                _reconcile(inventory, contract, parent=contract.transfer_root)
                restored = False
                _restore_volume_parent(
                    contract,
                    volume_descriptor=volume_descriptor,
                )
                restored = True
                _remove_transfer(
                    contract,
                    volume_descriptor=volume_descriptor,
                )
            _validate_release(contract)
            _final_state(contract, volume_descriptor=volume_descriptor)
            return

        inventory = _load_ingress(contract)
        _reconcile(inventory, contract, parent=contract.validation_parent)
        if "transfer" not in entries:
            _ensure_transfer(contract, volume_descriptor=volume_descriptor)
        _reconcile(inventory, contract, parent=contract.transfer_root)
        restored = False
        _set_publish_parent(contract, volume_descriptor=volume_descriptor)
        published = False
        failure: BaseException | None = None
        try:
            try:
                release, _proof = stage_accepted_research_release(
                    inventory,
                    research_root=contract.research_root,
                    bundle_path=contract.bundle_path,
                    staging_parent=contract.transfer_root,
                    destination_parent=contract.volume_root,
                    destination_name=contract.release_root.name,
                    source_uid=contract.source_uid,
                    source_gid=contract.source_gid,
                    destination_uid=contract.destination_uid,
                    destination_gid=contract.destination_gid,
                )
                if release != contract.release_root:
                    raise ResearchMaterializerError(
                        "materializer release destination differs"
                    )
                published = True
            except BaseException as exc:
                failure = exc
                if contract.release_root.exists() and not contract.release_root.is_symlink():
                    observed = _validate_release(contract)
                    if observed.canonical_bytes() != inventory.canonical_bytes():
                        raise ResearchMaterializerError(
                            "materializer observed release differs"
                        ) from exc
                    published = True
                else:
                    raise
            if published:
                observed = _validate_release(contract)
                if observed.canonical_bytes() != inventory.canonical_bytes():
                    raise ResearchMaterializerError(
                        "materializer published inventory differs"
                    )
        finally:
            _restore_volume_parent(
                contract,
                volume_descriptor=volume_descriptor,
            )
            restored = True
        if not published:
            if failure is not None:
                raise ResearchMaterializerError(
                    "materializer publication failed"
                ) from failure
            raise ResearchMaterializerError("materializer publication failed")
        _reconcile(inventory, contract, parent=contract.transfer_root)
        _remove_transfer(contract, volume_descriptor=volume_descriptor)
        _validate_release(contract)
        _final_state(contract, volume_descriptor=volume_descriptor)
    except ResearchMaterializerError:
        raise
    except (OSError, ResearchReleaseError, ResearchTransferError) as exc:
        raise ResearchMaterializerError("research materialization failed") from exc
    finally:
        if volume_descriptor >= 0:
            if not restored:
                try:
                    _restore_volume_parent(
                        contract,
                        volume_descriptor=volume_descriptor,
                    )
                except ResearchMaterializerError:
                    pass
            try:
                fcntl.flock(volume_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(volume_descriptor)


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if values:
        return 2
    try:
        materialize_accepted_research_release()
    except BaseException:
        sys.stderr.write("Buffalo research materialization failed\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MATERIALIZER_CONTRACT",
    "ResearchMaterializerError",
    "main",
    "materialize_accepted_research_release",
]
