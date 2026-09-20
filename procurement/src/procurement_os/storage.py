"""Object-storage adapter boundary.

Procurement-domain code must never call platform-specific object storage
directly (canonical spec section D / execution prompt requirement 9).
Everything goes through StorageAdapter. The concrete backend is selected by
configuration at the composition root, not inside domain modules.

Phase 0-2 only requires the interface plus a local filesystem backend for
development. A Replit App Storage backend can be added later behind the same
interface without touching domain logic.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
import stat
import tempfile


class StorageAdapter(ABC):
    """Interface for supplier books, import artifacts, PO files, packets, backups."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]: ...


class LocalFilesystemStorage(StorageAdapter):
    """Development/fallback backend rooted at a local directory."""

    def __init__(self, root: str | Path):
        self._root = Path(root)
        # Construction and every read path are side-effect free. The write
        # boundary creates only the parent directories needed for that object.

    def _path(self, key: str) -> Path:
        if Path(key).is_absolute():
            raise ValueError("absolute storage keys are not permitted")
        root = self._root.resolve()
        p = (root / key).resolve()
        if not p.is_relative_to(root):
            raise ValueError("storage key escapes storage root")
        return p

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{p.name}.", suffix=".tmp", dir=p.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, p)
            directory_descriptor = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary_path.unlink(missing_ok=True)

    def put_bytes_once(self, key: str, data: bytes) -> None:
        """Atomically create one immutable object without replacing a prior one.

        The temporary file is completely written and fsynced before a hard link
        publishes it at ``key``.  ``os.link`` is the portable local-filesystem
        create-if-absent primitive we need here: if another writer already
        published the key, ``FileExistsError`` is raised and the existing bytes
        are left untouched.  Callers that want idempotent content-addressed
        replay may catch that error and compare the already-published bytes.
        """

        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Stage outside the final object's directory. A hard kill can leave a
        # temporary inode behind, but it must never poison an exact-inventory
        # content-addressed directory that has already been published.
        staging = self._root.resolve() / ".immutable-staging"
        staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging_info = staging.stat(follow_symlinks=False)
        if (
            staging.is_symlink()
            or not stat.S_ISDIR(staging_info.st_mode)
            or staging_info.st_uid != os.getuid()
            or stat.S_IMODE(staging_info.st_mode) != 0o700
        ):
            raise PermissionError("immutable staging directory is unsafe")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{p.name}.", suffix=".tmp", dir=staging
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary_path, p, follow_symlinks=False)
            directory_descriptor = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            temporary_path.unlink(missing_ok=True)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> list[str]:
        base = self._root.resolve()
        return sorted(
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file()
            and p.relative_to(base).parts[0] != ".immutable-staging"
            and str(p.relative_to(base)).startswith(prefix)
        )


def get_storage() -> StorageAdapter:
    """Composition-root factory. Backend selection stays out of domain modules."""
    backend = os.getenv("PROCUREMENT_STORAGE_BACKEND", "local")
    if backend == "local":
        return LocalFilesystemStorage(os.getenv("PROCUREMENT_STORAGE_ROOT", "storage"))
    raise ValueError(f"Unknown storage backend: {backend}")
