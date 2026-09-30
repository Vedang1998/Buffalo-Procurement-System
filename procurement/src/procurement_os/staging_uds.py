"""Dependency-light Unix-socket ownership and peer-credential contracts."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import socket
import stat
import struct


class StagingUdsError(ValueError):
    """A private staging Unix-socket contract differs."""


@dataclass(frozen=True)
class PeerCredentials:
    pid: int
    uid: int
    gid: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.pid, self.uid, self.gid)
        ):
            raise StagingUdsError("worker peer credentials are invalid")

    def as_scope_value(self) -> dict[str, int]:
        return {"pid": self.pid, "uid": self.uid, "gid": self.gid}

    @classmethod
    def from_scope_value(cls, value: object) -> "PeerCredentials":
        if not isinstance(value, dict) or set(value) != {"pid", "uid", "gid"}:
            raise StagingUdsError("worker peer credential extension differs")
        return cls(pid=value["pid"], uid=value["uid"], gid=value["gid"])


@dataclass(frozen=True)
class SocketContract:
    path: Path
    parent_uid: int
    parent_gid: int
    socket_uid: int
    socket_gid: int

    def __post_init__(self) -> None:
        if (
            not self.path.is_absolute()
            or self.path.name in {"", ".", ".."}
            or any(part in {".", ".."} for part in self.path.parts)
        ):
            raise StagingUdsError("worker socket path is invalid")
        if any(
            type(value) is not int or value < 0
            for value in (
                self.parent_uid,
                self.parent_gid,
                self.socket_uid,
                self.socket_gid,
            )
        ):
            raise StagingUdsError("worker socket ownership is invalid")


def validate_socket_contract(contract: SocketContract) -> None:
    """Require the exact non-symlink 0750-parent/0660-socket contract."""

    _require_no_symlink_ancestors(contract.path.parent)
    try:
        parent = contract.path.parent.stat(follow_symlinks=False)
        item = contract.path.stat(follow_symlinks=False)
    except OSError as exc:
        raise StagingUdsError("worker socket is unavailable") from exc
    if (
        contract.path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o750
        or parent.st_uid != contract.parent_uid
        or parent.st_gid != contract.parent_gid
    ):
        raise StagingUdsError("worker socket parent contract differs")
    if (
        contract.path.is_symlink()
        or not stat.S_ISSOCK(item.st_mode)
        or stat.S_IMODE(item.st_mode) != 0o660
        or item.st_uid != contract.socket_uid
        or item.st_gid != contract.socket_gid
    ):
        raise StagingUdsError("worker socket contract differs")


def peer_credentials_from_transport(
    transport: asyncio.Transport,
) -> PeerCredentials:
    raw_socket = transport.get_extra_info("socket")
    if raw_socket is None or getattr(raw_socket, "family", None) != socket.AF_UNIX:
        raise StagingUdsError("worker connection is not a Unix socket")
    try:
        raw = raw_socket.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        pid, uid, gid = struct.unpack("3i", raw)
    except (AttributeError, OSError, struct.error) as exc:
        raise StagingUdsError("worker peer credentials are unavailable") from exc
    return PeerCredentials(pid=pid, uid=uid, gid=gid)


def _require_no_symlink_ancestors(path: Path) -> None:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise StagingUdsError("worker path is not canonical")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise StagingUdsError("worker path ancestor is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise StagingUdsError("worker path ancestor is a symlink")


__all__ = [
    "PeerCredentials",
    "SocketContract",
    "StagingUdsError",
    "peer_credentials_from_transport",
    "validate_socket_contract",
]
