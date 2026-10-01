"""Dependency-light gateway/worker transport value types."""
from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from enum import Enum
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


class WorkerKeyState(str, Enum):
    DISABLED = "DISABLED"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class WorkerKeyStatus:
    role: str
    generation: int
    state: WorkerKeyState


class WorkerKeyring:
    """Atomic role-key generations for worker restart rotation."""

    def __init__(
        self,
        keys: dict[str, bytes],
        *,
        active_roles: frozenset[str],
    ) -> None:
        if set(keys) != {"synthetic", "research"} or any(
            not isinstance(value, bytes) or len(value) != 32
            for value in keys.values()
        ):
            raise ValueError("worker key inventory is invalid")
        if secrets.compare_digest(keys["synthetic"], keys["research"]):
            raise ValueError("worker keys must be independently generated")
        if not isinstance(active_roles, frozenset) or not active_roles.issubset(keys):
            raise ValueError("active worker role inventory is invalid")
        self._values = {
            role: (
                bytes(value),
                0,
                WorkerKeyState.ACTIVE
                if role in active_roles
                else WorkerKeyState.DISABLED,
            )
            for role, value in keys.items()
        }
        self._used_keys = {bytes(value) for value in keys.values()}
        self._initial_unprepared = set(keys) - set(active_roles)
        self._lock = RLock()

    def current(self, role: str) -> tuple[bytes, int]:
        with self._lock:
            try:
                key, generation, state = self._values[role]
            except KeyError:
                raise ValueError("worker role is invalid") from None
            if state is not WorkerKeyState.ACTIVE:
                raise ValueError("worker key is not active")
            return bytes(key), generation

    def status(self, role: str) -> WorkerKeyStatus:
        with self._lock:
            try:
                _, generation, state = self._values[role]
            except KeyError:
                raise ValueError("worker role is invalid") from None
            return WorkerKeyStatus(role=role, generation=generation, state=state)

    def prepare(self, *, role: str, key: bytes, generation: int) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("prepared worker key is invalid")
        with self._lock:
            if role not in self._values:
                raise ValueError("worker role is invalid")
            current_key, current_generation, state = self._values[role]
            if state is not WorkerKeyState.DISABLED:
                raise ValueError("worker key must be disabled before prepare")
            if type(generation) is not int or not 0 <= generation <= (2**63 - 1):
                raise ValueError("worker key generation is not newer")
            if generation == current_generation:
                if role not in self._initial_unprepared:
                    raise ValueError("worker key generation is not newer")
                if not secrets.compare_digest(key, current_key):
                    raise ValueError("worker key generation was rebound")
                self._initial_unprepared.remove(role)
                self._values[role] = (
                    bytes(current_key),
                    current_generation,
                    WorkerKeyState.PENDING,
                )
                return
            if generation < current_generation:
                raise ValueError("worker key generation is not newer")
            if any(secrets.compare_digest(key, used) for used in self._used_keys):
                raise ValueError("worker keys must never be reused")
            value = bytes(key)
            self._initial_unprepared.discard(role)
            self._values[role] = (value, generation, WorkerKeyState.PENDING)
            self._used_keys.add(value)

    def commit(self, *, role: str, generation: int) -> None:
        with self._lock:
            try:
                key, current_generation, state = self._values[role]
            except KeyError:
                raise ValueError("worker role is invalid") from None
            if type(generation) is not int or generation != current_generation:
                raise ValueError("worker key generation differs")
            if state is WorkerKeyState.ACTIVE:
                return
            if state is not WorkerKeyState.PENDING:
                raise ValueError("worker key is not pending")
            self._values[role] = (key, current_generation, WorkerKeyState.ACTIVE)

    def disable(self, *, role: str, generation: int) -> None:
        with self._lock:
            try:
                key, current_generation, state = self._values[role]
            except KeyError:
                raise ValueError("worker role is invalid") from None
            if type(generation) is not int or generation != current_generation:
                raise ValueError("worker key generation differs")
            if state is WorkerKeyState.DISABLED:
                return
            self._values[role] = (key, current_generation, WorkerKeyState.DISABLED)
