"""Dependency-light gateway/worker transport value types."""
from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
import secrets
from threading import RLock
from typing import Awaitable, Callable, Protocol


@dataclass(frozen=True)
class WorkerResponse:
    status: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes | AsyncIterable[bytes]
    close: Callable[[], Awaitable[None] | None] | None = None


class WorkerTransport(Protocol):
    async def request(self, **request: object) -> WorkerResponse: ...


class WorkerKeyring:
    """Atomic role-key generations for worker restart rotation."""

    def __init__(self, keys: dict[str, bytes]) -> None:
        if set(keys) != {"synthetic", "research"} or any(
            not isinstance(value, bytes) or len(value) != 32
            for value in keys.values()
        ):
            raise ValueError("worker key inventory is invalid")
        if secrets.compare_digest(keys["synthetic"], keys["research"]):
            raise ValueError("worker keys must be independently generated")
        self._values = {
            role: (bytes(value), 0) for role, value in keys.items()
        }
        self._used_keys = {bytes(value) for value in keys.values()}
        self._lock = RLock()

    def current(self, role: str) -> tuple[bytes, int]:
        with self._lock:
            try:
                key, generation = self._values[role]
            except KeyError:
                raise ValueError("worker role is invalid") from None
            return bytes(key), generation

    def replace(self, *, role: str, key: bytes, generation: int) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("replacement worker key is invalid")
        with self._lock:
            if role not in self._values:
                raise ValueError("worker role is invalid")
            _, current_generation = self._values[role]
            if (
                type(generation) is not int
                or generation <= current_generation
                or generation > (2**63 - 1)
            ):
                raise ValueError("worker key generation is not newer")
            if any(secrets.compare_digest(key, used) for used in self._used_keys):
                raise ValueError("worker keys must never be reused")
            value = bytes(key)
            self._values[role] = (value, generation)
            self._used_keys.add(value)
