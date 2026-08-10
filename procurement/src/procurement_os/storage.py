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
        self._root.mkdir(parents=True, exist_ok=True)

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
        p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> list[str]:
        base = self._root.resolve()
        return sorted(
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file() and str(p.relative_to(base)).startswith(prefix)
        )


def get_storage() -> StorageAdapter:
    """Composition-root factory. Backend selection stays out of domain modules."""
    backend = os.getenv("PROCUREMENT_STORAGE_BACKEND", "local")
    if backend == "local":
        return LocalFilesystemStorage(os.getenv("PROCUREMENT_STORAGE_ROOT", "storage"))
    raise ValueError(f"Unknown storage backend: {backend}")
