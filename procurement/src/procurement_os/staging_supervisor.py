"""Fixed private control and child-lifecycle primitives for Railway staging."""
from __future__ import annotations

import asyncio
from collections import deque
import ctypes
from dataclasses import dataclass, field as dataclass_field
import functools
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import struct
import subprocess
import threading
import time
from typing import Callable, Mapping, Protocol

from .staging_internal import (
    AssertionError as InternalAssertionError,
    NonceReplayCache,
)
from .staging_process_contract import (
    StagingProcessContractError,
    child_argv,
    validated_process_environment,
)
from .staging_research_readiness import (
    ReadinessProcessIdentity,
    ResearchReadinessAbsent,
    ResearchReadinessProof,
    ResearchReadinessReader,
)
from .staging_uds import (
    PeerCredentials,
    SocketContract,
    StagingUdsError,
    peer_credentials_from_transport,
    validate_socket_contract,
)


CONTROL_VERSION = "BUFFALO_STAGING_SUPERVISOR_CONTROL_V1"
CONTROL_TTL_SECONDS = 5
MAX_CONTROL_MESSAGE_BYTES = 2_048
CONTROL_OPERATIONS = frozenset(
    {"research-start", "research-stop", "research-status"}
)
CONTROL_STATES = frozenset(
    {"STOPPED", "VALIDATING", "READY", "STOPPING", "FAILED"}
)
_CONTROL_RATE_WINDOW_SECONDS = 1.0
_CONTROL_RATE_MAXIMUM = 8
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37


class StagingSupervisorError(ValueError):
    """A supervisor control or owned-process contract differs."""


class ResearchChildCrashed(StagingSupervisorError):
    """The exact research child exited without an accepted failure proof."""


def _serialized(method: Callable[..., object]) -> Callable[..., object]:
    """Run one state transition under its owner's reentrant lock."""

    @functools.wraps(method)
    def wrapper(self, *args: object, **kwargs: object) -> object:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def enable_child_subreaper() -> None:
    """Make the supervisor adopt and reap descendants after a leader crash."""

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")
        enabled = ctypes.c_int(0)
        if libc.prctl(
            _PR_GET_CHILD_SUBREAPER,
            ctypes.byref(enabled),
            0,
            0,
            0,
        ) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_GET_CHILD_SUBREAPER) failed")
    except (AttributeError, OSError) as exc:
        raise StagingSupervisorError("child subreaper is unavailable") from exc
    if enabled.value != 1:
        raise StagingSupervisorError("child subreaper could not be enabled")


@dataclass(frozen=True)
class ControlStatus:
    state: str
    generation: int
    retry_after_seconds: int | None = None

    def validate(self) -> None:
        if self.state not in CONTROL_STATES:
            raise StagingSupervisorError("research state is invalid")
        if type(self.generation) is not int or self.generation < 0:
            raise StagingSupervisorError("research generation is invalid")
        if self.retry_after_seconds is not None and (
            type(self.retry_after_seconds) is not int
            or self.retry_after_seconds < 1
            or self.retry_after_seconds > 2_700
        ):
            raise StagingSupervisorError("research retry interval is invalid")


class ResearchControlHooks(Protocol):
    def start(self) -> ControlStatus: ...
    def stop(self) -> ControlStatus: ...
    def status(self) -> ControlStatus: ...


class ControlRateLimiter:
    """Bound all authenticated supervisor-control operations per boot."""

    def __init__(
        self,
        *,
        maximum: int = _CONTROL_RATE_MAXIMUM,
        window_seconds: float = _CONTROL_RATE_WINDOW_SECONDS,
    ) -> None:
        if (
            type(maximum) is not int
            or maximum < 1
            or maximum > 64
            or not isinstance(window_seconds, (int, float))
            or window_seconds <= 0
            or window_seconds > 60
        ):
            raise StagingSupervisorError("control rate limit is invalid")
        self.maximum = maximum
        self.window_seconds = float(window_seconds)
        self._accepted: deque[float] = deque()

    def admit(self, *, now: float) -> None:
        if not isinstance(now, (int, float)) or now < 0:
            raise StagingSupervisorError("control rate time is invalid")
        threshold = float(now) - self.window_seconds
        while self._accepted and self._accepted[0] <= threshold:
            self._accepted.popleft()
        if len(self._accepted) >= self.maximum:
            raise StagingSupervisorError("control rate limit reached")
        self._accepted.append(float(now))


class SupervisorControlProtocol:
    """Strict no-parameter, boot-keyed control-message verifier."""

    def __init__(
        self,
        *,
        key: bytes,
        expected_peer: PeerCredentials,
        hooks: ResearchControlHooks,
        wall_clock: Callable[[], float] = time.time,
        rate_clock: Callable[[], float] = time.monotonic,
        replay_cache: NonceReplayCache | None = None,
        rate_limiter: ControlRateLimiter | None = None,
    ) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise StagingSupervisorError("control key is invalid")
        self._key = key
        self.expected_peer = expected_peer
        self.hooks = hooks
        self.wall_clock = wall_clock
        self.rate_clock = rate_clock
        self.replay_cache = replay_cache or NonceReplayCache(maximum_entries=1_024)
        self.rate_limiter = rate_limiter or ControlRateLimiter()

    def dispatch(self, message: bytes, *, peer: PeerCredentials) -> bytes:
        if peer != self.expected_peer:
            raise StagingSupervisorError("control peer credentials differ")
        now = float(self.wall_clock())
        self.rate_limiter.admit(now=float(self.rate_clock()))
        operation, nonce, expires_at = self._verify(message, peer=peer)
        try:
            self.replay_cache.accept(nonce, expires_at=expires_at, now=now)
        except InternalAssertionError as exc:
            raise StagingSupervisorError("control nonce cannot be accepted") from exc
        handler = {
            "research-start": self.hooks.start,
            "research-stop": self.hooks.stop,
            "research-status": self.hooks.status,
        }[operation]
        status = handler()
        status.validate()
        payload = {
            "generation": status.generation,
            "operation": operation,
            "retry_after_seconds": status.retry_after_seconds,
            "state": status.state,
            "version": CONTROL_VERSION,
        }
        return _canonical_json(payload)

    def _verify(
        self, message: bytes, *, peer: PeerCredentials
    ) -> tuple[str, str, float]:
        if peer != self.expected_peer:
            raise StagingSupervisorError("control peer credentials differ")
        if (
            not isinstance(message, bytes)
            or not message
            or len(message) > MAX_CONTROL_MESSAGE_BYTES
        ):
            raise StagingSupervisorError("control message size is invalid")
        try:
            envelope = json.loads(message)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise StagingSupervisorError("control message is invalid") from None
        if not isinstance(envelope, dict) or set(envelope) != {
            "expires_ms",
            "issued_ms",
            "nonce",
            "operation",
            "signature",
            "version",
        }:
            raise StagingSupervisorError("control message contract differs")
        signature = envelope.pop("signature")
        if not isinstance(signature, str) or len(signature) != 64:
            raise StagingSupervisorError("control signature is invalid")
        encoded = _canonical_json(envelope)
        if not hmac.compare_digest(
            signature,
            hmac.digest(self._key, encoded, "sha256").hex(),
        ):
            raise StagingSupervisorError("control signature differs")
        operation = envelope.get("operation")
        nonce = envelope.get("nonce")
        issued_ms = envelope.get("issued_ms")
        expires_ms = envelope.get("expires_ms")
        if (
            envelope.get("version") != CONTROL_VERSION
            or operation not in CONTROL_OPERATIONS
            or not isinstance(nonce, str)
            or len(nonce) != 64
            or any(character not in "0123456789abcdef" for character in nonce)
            or type(issued_ms) is not int
            or type(expires_ms) is not int
            or expires_ms - issued_ms != CONTROL_TTL_SECONDS * 1_000
        ):
            raise StagingSupervisorError("control message values differ")
        now = float(self.wall_clock())
        issued_at = issued_ms / 1_000
        expires_at = expires_ms / 1_000
        if issued_at > now + 1 or now > expires_at:
            raise StagingSupervisorError("control message time is invalid")
        return operation, nonce, expires_at


class SupervisorControlServer:
    """One-message-per-connection length-bounded private UDS handler."""

    def __init__(self, protocol: SupervisorControlProtocol) -> None:
        self.protocol = protocol
        self._active = False

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._active:
            writer.close()
            await writer.wait_closed()
            return
        self._active = True
        try:
            transport = writer.transport
            peer = peer_credentials_from_transport(transport)
            prefix = await asyncio.wait_for(reader.readexactly(4), timeout=2)
            length = struct.unpack("!I", prefix)[0]
            if length < 1 or length > MAX_CONTROL_MESSAGE_BYTES:
                raise StagingSupervisorError("control frame length is invalid")
            message = await asyncio.wait_for(reader.readexactly(length), timeout=2)
            response = self.protocol.dispatch(message, peer=peer)
            writer.write(struct.pack("!I", len(response)) + response)
            await writer.drain()
        except (
            asyncio.IncompleteReadError,
            asyncio.TimeoutError,
            StagingSupervisorError,
            ValueError,
        ):
            pass
        finally:
            self._active = False
            writer.close()
            await writer.wait_closed()


class SupervisorControlClient:
    """Gateway-side client pinned to one private supervisor socket and peer."""

    def __init__(
        self,
        *,
        contract: SocketContract,
        key: bytes,
        expected_supervisor_peer: PeerCredentials,
        wall_clock: Callable[[], float] = time.time,
        timeout_seconds: float = 2.0,
    ) -> None:
        if not isinstance(key, bytes) or len(key) != 32:
            raise StagingSupervisorError("control key is invalid")
        if (
            not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
            or timeout_seconds > 10
        ):
            raise StagingSupervisorError("control timeout is invalid")
        self.contract = contract
        self._key = key
        self.expected_supervisor_peer = expected_supervisor_peer
        self.wall_clock = wall_clock
        self.timeout_seconds = float(timeout_seconds)

    async def request(self, operation: str) -> ControlStatus:
        if operation not in CONTROL_OPERATIONS:
            raise StagingSupervisorError("control operation is invalid")
        try:
            validate_socket_contract(self.contract)
        except StagingUdsError as exc:
            raise StagingSupervisorError("control socket contract differs") from exc
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.contract.path)),
                timeout=self.timeout_seconds,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise StagingSupervisorError("control socket is unavailable") from exc
        try:
            peer = peer_credentials_from_transport(writer.transport)
            if peer != self.expected_supervisor_peer:
                raise StagingSupervisorError("control server peer differs")
            issued_at = float(int(self.wall_clock()))
            message = mint_control_message(
                key=self._key,
                operation=operation,
                issued_at=issued_at,
            )
            writer.write(struct.pack("!I", len(message)) + message)
            await asyncio.wait_for(writer.drain(), timeout=self.timeout_seconds)
            prefix = await asyncio.wait_for(
                reader.readexactly(4), timeout=self.timeout_seconds
            )
            length = struct.unpack("!I", prefix)[0]
            if length < 1 or length > MAX_CONTROL_MESSAGE_BYTES:
                raise StagingSupervisorError("control response length is invalid")
            response = await asyncio.wait_for(
                reader.readexactly(length), timeout=self.timeout_seconds
            )
            trailing = await asyncio.wait_for(
                reader.read(1), timeout=self.timeout_seconds
            )
            if trailing:
                raise StagingSupervisorError("control response has trailing data")
            return _parse_control_response(response, operation=operation)
        except (
            asyncio.IncompleteReadError,
            asyncio.TimeoutError,
            OSError,
            StagingUdsError,
        ) as exc:
            if isinstance(exc, StagingSupervisorError):
                raise
            raise StagingSupervisorError("control exchange failed") from exc
        finally:
            writer.close()
            await writer.wait_closed()


def bind_private_listener(
    contract: SocketContract, *, backlog: int = 16
) -> socket.socket:
    """Create exactly one owned/mode-bound AF_UNIX listener without replacement."""

    if type(backlog) is not int or backlog < 1 or backlog > 128:
        raise StagingSupervisorError("private listener backlog is invalid")
    _require_no_symlink_ancestors(contract.path.parent)
    try:
        parent = contract.path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise StagingSupervisorError("private listener parent is unavailable") from exc
    if (
        contract.path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o750
        or parent.st_uid != contract.parent_uid
        or parent.st_gid != contract.parent_gid
        or contract.path.exists()
        or contract.path.is_symlink()
    ):
        raise StagingSupervisorError("private listener path contract differs")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound_identity: tuple[int, int] | None = None
    try:
        listener.bind(str(contract.path))
        bound = contract.path.stat(follow_symlinks=False)
        bound_identity = (bound.st_dev, bound.st_ino)
        os.chown(contract.path, contract.socket_uid, contract.socket_gid)
        os.chmod(contract.path, 0o660)
        listener.listen(backlog)
        try:
            validate_socket_contract(contract)
        except StagingUdsError as exc:
            raise StagingSupervisorError("private listener contract differs") from exc
        return listener
    except BaseException:
        listener.close()
        if bound_identity is not None:
            try:
                _require_no_symlink_ancestors(contract.path.parent)
                current = contract.path.stat(follow_symlinks=False)
                if stat.S_ISSOCK(current.st_mode) and (
                    current.st_dev,
                    current.st_ino,
                ) == bound_identity:
                    contract.path.unlink()
            except (FileNotFoundError, OSError, StagingSupervisorError):
                pass
        raise


def _parse_control_response(message: bytes, *, operation: str) -> ControlStatus:
    try:
        payload = json.loads(message)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise StagingSupervisorError("control response is invalid") from None
    if not isinstance(payload, dict) or set(payload) != {
        "generation",
        "operation",
        "retry_after_seconds",
        "state",
        "version",
    }:
        raise StagingSupervisorError("control response contract differs")
    if (
        payload.get("version") != CONTROL_VERSION
        or payload.get("operation") != operation
    ):
        raise StagingSupervisorError("control response binding differs")
    status = ControlStatus(
        state=payload.get("state"),
        generation=payload.get("generation"),
        retry_after_seconds=payload.get("retry_after_seconds"),
    )
    status.validate()
    return status


def mint_control_message(
    *, key: bytes, operation: str, issued_at: float, nonce: str | None = None
) -> bytes:
    if not isinstance(key, bytes) or len(key) != 32:
        raise StagingSupervisorError("control key is invalid")
    if operation not in CONTROL_OPERATIONS:
        raise StagingSupervisorError("control operation is invalid")
    issued_ms = _milliseconds(issued_at)
    payload: dict[str, object] = {
        "expires_ms": issued_ms + CONTROL_TTL_SECONDS * 1_000,
        "issued_ms": issued_ms,
        "nonce": nonce or secrets.token_hex(32),
        "operation": operation,
        "version": CONTROL_VERSION,
    }
    payload["signature"] = hmac.digest(key, _canonical_json(payload), "sha256").hex()
    encoded = _canonical_json(payload)
    if len(encoded) > MAX_CONTROL_MESSAGE_BYTES:
        raise StagingSupervisorError("control message size is invalid")
    return encoded


@dataclass(frozen=True)
class FixedChildSpec:
    name: str
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = dataclass_field(repr=False)
    uid: int
    gid: int
    extra_groups: tuple[int, ...]
    pass_fds: tuple[int, ...]

    def validate(self) -> None:
        if (
            not self.name
            or not self.argv
            or not Path(self.argv[0]).is_absolute()
            or any(
                not isinstance(value, str) or not value or "\x00" in value
                for value in self.argv
            )
        ):
            raise StagingSupervisorError("child command contract differs")
        if any(type(value) is not int or value < 0 for value in (self.uid, self.gid)):
            raise StagingSupervisorError("child identity contract differs")
        if (
            len(self.extra_groups) != len(set(self.extra_groups))
            or len(self.pass_fds) != len(set(self.pass_fds))
            or tuple(sorted(self.extra_groups)) != self.extra_groups
            or tuple(sorted(self.pass_fds)) != self.pass_fds
            or any(type(value) is not int or value < 1 for value in self.extra_groups)
            or any(type(value) is not int or value < 0 for value in self.pass_fds)
        ):
            raise StagingSupervisorError("child descriptor/group contract differs")
        names = [name for name, _ in self.environment]
        if (
            names != sorted(names)
            or len(names) != len(set(names))
            or any(
                not name
                or not name.replace("_", "A").isalnum()
                or name.upper() != name
                or "\x00" in value
                for name, value in self.environment
            )
        ):
            raise StagingSupervisorError("child environment contract differs")


@dataclass(frozen=True)
class ComponentIsolation:
    """Fixed identity and runtime root for one staging process role."""

    name: str
    uid: int
    gid: int
    extra_groups: tuple[int, ...]
    runtime_root: Path
    child_argv: tuple[str, ...] = ()
    child_environment_names: frozenset[str] = frozenset()
    child_pass_fds: tuple[int, ...] = ()

    def validate(self) -> None:
        if self.name not in {"supervisor", "gateway", "synthetic", "research"}:
            raise StagingSupervisorError("component role is invalid")
        if any(type(value) is not int or value < 0 for value in (self.uid, self.gid)):
            raise StagingSupervisorError("component identity is invalid")
        if self.name == "supervisor":
            if self.uid != 0 or self.gid != 0:
                raise StagingSupervisorError("supervisor must run as root")
            if self.child_argv or self.child_environment_names or self.child_pass_fds:
                raise StagingSupervisorError("supervisor is not a child role")
        elif self.uid == 0 or self.gid == 0:
            raise StagingSupervisorError("staging children must be unprivileged")
        if (
            len(self.extra_groups) != len(set(self.extra_groups))
            or tuple(sorted(self.extra_groups)) != self.extra_groups
            or any(type(value) is not int or value < 0 for value in self.extra_groups)
        ):
            raise StagingSupervisorError("component groups are invalid")
        if (
            not self.runtime_root.is_absolute()
            or self.runtime_root.name in {"", ".", ".."}
            or any(part in {".", ".."} for part in self.runtime_root.parts)
        ):
            raise StagingSupervisorError("component runtime root is invalid")
        if self.name != "supervisor":
            if (
                not self.child_argv
                or not Path(self.child_argv[0]).is_absolute()
                or any(
                    not isinstance(value, str) or not value or "\x00" in value
                    for value in self.child_argv
                )
                or not self.child_environment_names
                or any(
                    not isinstance(value, str) or not value
                    for value in self.child_environment_names
                )
                or tuple(sorted(self.child_pass_fds)) != self.child_pass_fds
                or len(set(self.child_pass_fds)) != len(self.child_pass_fds)
                or any(type(value) is not int or value < 3 for value in self.child_pass_fds)
            ):
                raise StagingSupervisorError("child launch contract is invalid")
            if self.name == "gateway" and len(self.child_pass_fds) != 1:
                raise StagingSupervisorError(
                    "gateway activation descriptor contract differs"
                )
            if self.name in {"synthetic", "research"} and len(self.child_pass_fds) != 1:
                raise StagingSupervisorError("worker listener contract differs")


@dataclass(frozen=True)
class StagingIsolationContract:
    """Exact OS-identity/group/runtime separation for the four processes."""

    components: tuple[ComponentIsolation, ...]
    synthetic_socket_group: int
    research_socket_group: int
    control_socket_group: int
    shared_socket_root: Path
    shared_socket_root_uid: int
    shared_socket_root_gid: int
    shared_key_root: Path
    shared_key_root_uid: int
    shared_key_root_gid: int
    socket_contracts: tuple[tuple[str, SocketContract], ...]

    def validate(self) -> None:
        if len(self.components) != 4:
            raise StagingSupervisorError("component inventory differs")
        by_name = {component.name: component for component in self.components}
        if set(by_name) != {"supervisor", "gateway", "synthetic", "research"}:
            raise StagingSupervisorError("component inventory differs")
        if len(by_name) != len(self.components):
            raise StagingSupervisorError("component roles are duplicated")
        for component in self.components:
            component.validate()
        if len({component.uid for component in self.components}) != 4:
            raise StagingSupervisorError("component UIDs must be distinct")
        if len({component.gid for component in self.components}) != 4:
            raise StagingSupervisorError("component GIDs must be distinct")
        roots = [component.runtime_root for component in self.components]
        if len(set(roots)) != 4 or any(
            first != second
            and (first.is_relative_to(second) or second.is_relative_to(first))
            for index, first in enumerate(roots)
            for second in roots[index + 1 :]
        ):
            raise StagingSupervisorError("component runtime roots overlap")
        groups = (
            self.synthetic_socket_group,
            self.research_socket_group,
            self.control_socket_group,
        )
        if (
            any(type(value) is not int or value < 1 for value in groups)
            or len(set(groups)) != 3
            or set(groups) & {component.gid for component in self.components}
        ):
            raise StagingSupervisorError("socket groups are not isolated")
        if (
            not self.shared_socket_root.is_absolute()
            or any(part in {".", ".."} for part in self.shared_socket_root.parts)
            or type(self.shared_socket_root_uid) is not int
            or type(self.shared_socket_root_gid) is not int
            or self.shared_socket_root_uid < 0
            or self.shared_socket_root_gid < 0
            or self.shared_socket_root_uid != by_name["supervisor"].uid
            or self.shared_socket_root_gid != by_name["supervisor"].gid
            or any(
                self.shared_socket_root.is_relative_to(root)
                or root.is_relative_to(self.shared_socket_root)
                for root in roots
            )
        ):
            raise StagingSupervisorError("shared socket root differs")
        if (
            not self.shared_key_root.is_absolute()
            or any(part in {".", ".."} for part in self.shared_key_root.parts)
            or self.shared_key_root == self.shared_socket_root
            or self.shared_key_root.is_relative_to(self.shared_socket_root)
            or self.shared_socket_root.is_relative_to(self.shared_key_root)
            or self.shared_key_root_uid != by_name["supervisor"].uid
            or self.shared_key_root_gid != by_name["supervisor"].gid
            or any(
                self.shared_key_root.is_relative_to(root)
                or root.is_relative_to(self.shared_key_root)
                for root in roots
            )
        ):
            raise StagingSupervisorError("shared key root differs")
        expected_groups = {
            "supervisor": (),
            "gateway": tuple(sorted(groups)),
            "synthetic": (self.synthetic_socket_group,),
            "research": (self.research_socket_group,),
        }
        for name, expected in expected_groups.items():
            if by_name[name].extra_groups != tuple(sorted(expected)):
                raise StagingSupervisorError(f"{name} groups differ")
        sockets = dict(self.socket_contracts)
        if (
            len(self.socket_contracts) != 3
            or set(sockets) != {"synthetic", "research", "control"}
            or len({contract.path for contract in sockets.values()}) != 3
        ):
            raise StagingSupervisorError("private socket inventory differs")
        expected_socket_bindings = {
            "synthetic": (
                by_name["synthetic"],
                self.synthetic_socket_group,
            ),
            "research": (
                by_name["research"],
                self.research_socket_group,
            ),
            "control": (
                by_name["supervisor"],
                self.control_socket_group,
            ),
        }
        for role, (owner, group) in expected_socket_bindings.items():
            contract = sockets[role]
            if (
                contract.parent_uid != by_name["supervisor"].uid
                or contract.parent_gid != group
                or contract.socket_uid != owner.uid
                or contract.socket_gid != group
                or any(
                    contract.path.is_relative_to(component.runtime_root)
                    for component in self.components
                )
                or contract.path.parent.parent != self.shared_socket_root
            ):
                raise StagingSupervisorError(f"{role} socket binding differs")
        socket_parents = [contract.path.parent for contract in sockets.values()]
        if len(set(socket_parents)) != 3 or any(
            first != second
            and (first.is_relative_to(second) or second.is_relative_to(first))
            for index, first in enumerate(socket_parents)
            for second in socket_parents[index + 1 :]
        ):
            raise StagingSupervisorError("private socket roots are not isolated")
        descriptor_values = [
            descriptor
            for component in self.components
            for descriptor in component.child_pass_fds
        ]
        if len(descriptor_values) != len(set(descriptor_values)):
            raise StagingSupervisorError("child descriptors cross process roles")

    def validate_children(self, specs: tuple[FixedChildSpec, ...]) -> None:
        """Bind immutable child specs to the validated non-root roles."""

        self.validate()
        by_name = {spec.name: spec for spec in specs}
        if len(specs) != 3 or set(by_name) != {"gateway", "synthetic", "research"}:
            raise StagingSupervisorError("child inventory differs")
        for spec in specs:
            self.validate_child(spec)
        _validate_environment_separation(by_name)

    def validate_child(self, spec: FixedChildSpec) -> None:
        """Bind one late-created child spec to its fixed process role."""

        self.validate()
        identities = {component.name: component for component in self.components}
        if spec.name not in {"gateway", "synthetic", "research"}:
            raise StagingSupervisorError("child role differs")
        name = spec.name
        spec.validate()
        identity = identities[name]
        if (spec.uid, spec.gid, spec.extra_groups) != (
            identity.uid,
            identity.gid,
            identity.extra_groups,
        ):
            raise StagingSupervisorError(f"{name} child identity differs")
        if spec.argv != identity.child_argv:
            raise StagingSupervisorError(f"{name} child command differs")
        try:
            expected_argv = child_argv(
                python_executable=spec.argv[0], role=name
            )
        except ValueError as exc:
            raise StagingSupervisorError(f"{name} child command differs") from exc
        if spec.argv != expected_argv:
            raise StagingSupervisorError(f"{name} child command differs")
        if spec.pass_fds != identity.child_pass_fds:
            raise StagingSupervisorError(f"{name} child descriptors differ")
        environment = dict(spec.environment)
        if set(environment) != identity.child_environment_names:
            raise StagingSupervisorError(f"{name} environment inventory differs")
        if environment.get("BUFFALO_STAGING_RUNTIME_ROOT") != str(
            identity.runtime_root
        ):
            raise StagingSupervisorError(f"{name} runtime root differs")
        _validate_role_environment(name, environment)
        sockets = dict(self.socket_contracts)
        if name == "gateway":
            control = sockets["control"]
            key_path = Path(environment.get("BUFFALO_STAGING_CONTROL_KEY_FILE", ""))
            if (
                environment.get("BUFFALO_STAGING_ACTIVATION_FD")
                != str(spec.pass_fds[0])
                or environment.get("BUFFALO_STAGING_SUPERVISOR_PID")
                != str(os.getpid())
                or environment.get("BUFFALO_STAGING_CONTROL_SOCKET_PATH")
                != str(control.path)
                or environment.get("BUFFALO_STAGING_CONTROL_SOCKET_GID")
                != str(control.socket_gid)
                or environment.get("BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_UID")
                != str(identities["supervisor"].uid)
                or environment.get("BUFFALO_STAGING_CONTROL_KEY_DIRECTORY_GID")
                != str(identity.gid)
                or not key_path.is_absolute()
                or any(part in {".", ".."} for part in key_path.parts)
                or key_path.name != "control.key"
                or key_path.parent.parent != self.shared_key_root
            ):
                raise StagingSupervisorError("gateway management binding differs")
            for role in ("synthetic", "research"):
                contract = sockets[role]
                prefix = f"BUFFALO_STAGING_{role.upper()}_SOCKET"
                if (
                    environment.get(f"{prefix}_PATH") != str(contract.path)
                    or environment.get(f"{prefix}_UID")
                    != str(contract.socket_uid)
                    or environment.get(f"{prefix}_GID")
                    != str(contract.socket_gid)
                ):
                    raise StagingSupervisorError(
                        f"gateway {role} socket binding differs"
                    )
        else:
            contract = sockets[name]
            if (
                environment.get("BUFFALO_STAGING_LISTEN_FD")
                != str(spec.pass_fds[0])
                or environment.get("BUFFALO_STAGING_SOCKET_PATH")
                != str(contract.path)
                or environment.get("BUFFALO_STAGING_SOCKET_GID")
                != str(contract.socket_gid)
            ):
                raise StagingSupervisorError(f"{name} listener binding differs")

    def validate_research_launch(
        self,
        *,
        template: FixedChildSpec,
        spec: FixedChildSpec,
        readiness_fd: int,
    ) -> None:
        """Validate the sole per-attempt delta to the static research spec."""

        self.validate_child(template)
        if (
            template.name != "research"
            or spec.name != "research"
            or type(readiness_fd) is not int
            or readiness_fd < 3
            or readiness_fd in template.pass_fds
        ):
            raise StagingSupervisorError("research launch contract differs")
        expected_environment = dict(template.environment)
        expected_environment["BUFFALO_STAGING_RESEARCH_READINESS_FD"] = str(
            readiness_fd
        )
        expected_fds = tuple(sorted((*template.pass_fds, readiness_fd)))
        if (
            spec.argv != template.argv
            or spec.uid != template.uid
            or spec.gid != template.gid
            or spec.extra_groups != template.extra_groups
            or spec.pass_fds != expected_fds
            or dict(spec.environment) != expected_environment
        ):
            raise StagingSupervisorError("research launch contract differs")
        spec.validate()
        _validate_role_environment("research", expected_environment)

    def validate_runtime_roots(self) -> None:
        """Verify each already-created root without following a symlink."""

        self.validate()
        for component in self.components:
            _require_no_symlink_ancestors(component.runtime_root)
            try:
                info = component.runtime_root.stat(follow_symlinks=False)
            except OSError as exc:
                raise StagingSupervisorError(
                    f"{component.name} runtime root is unavailable"
                ) from exc
            if (
                component.runtime_root.is_symlink()
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != component.uid
                or info.st_gid != component.gid
            ):
                raise StagingSupervisorError(
                    f"{component.name} runtime root contract differs"
                )
        _require_no_symlink_ancestors(self.shared_key_root)
        try:
            key_root = self.shared_key_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise StagingSupervisorError("shared key root is unavailable") from exc
        if (
            self.shared_key_root.is_symlink()
            or not stat.S_ISDIR(key_root.st_mode)
            or stat.S_IMODE(key_root.st_mode) != 0o755
            or key_root.st_uid != self.shared_key_root_uid
            or key_root.st_gid != self.shared_key_root_gid
        ):
            raise StagingSupervisorError("shared key root contract differs")

    def validate_private_sockets(self) -> None:
        self.validate()
        _require_no_symlink_ancestors(self.shared_socket_root)
        try:
            shared = self.shared_socket_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise StagingSupervisorError("shared socket root is unavailable") from exc
        if (
            self.shared_socket_root.is_symlink()
            or not stat.S_ISDIR(shared.st_mode)
            or stat.S_IMODE(shared.st_mode) != 0o755
            or shared.st_uid != self.shared_socket_root_uid
            or shared.st_gid != self.shared_socket_root_gid
        ):
            raise StagingSupervisorError("shared socket root contract differs")
        for _, contract in self.socket_contracts:
            validate_socket_contract(contract)


def _validate_environment_separation(specs: Mapping[str, FixedChildSpec]) -> None:
    values = {role: dict(spec.environment) for role, spec in specs.items()}
    for role, environment in values.items():
        _validate_role_environment(role, environment)
    names = {role: set(environment) for role, environment in values.items()}
    if "BUFFALO_STAGING_WORKER_ROLE" in names["gateway"]:
        raise StagingSupervisorError("gateway cannot select a worker role")


def _validate_role_environment(role: str, environment: Mapping[str, str]) -> None:
    try:
        validated_process_environment(role=role, environ=environment)
    except StagingProcessContractError as exc:
        raise StagingSupervisorError(f"{role} environment contract differs") from exc
    names = set(environment)
    if role == "gateway":
        if "BUFFALO_STAGING_OWNER_VERIFIER" not in names:
            raise StagingSupervisorError("gateway verifier binding is absent")
        return
    required = {
        "BUFFALO_STAGING_ASSERTION_KEY_FILE",
        "BUFFALO_STAGING_EXTERNAL_ORIGIN",
        "BUFFALO_STAGING_GATEWAY_GID",
        "BUFFALO_STAGING_GATEWAY_PID",
        "BUFFALO_STAGING_GATEWAY_UID",
        "BUFFALO_STAGING_LISTEN_FD",
        "BUFFALO_STAGING_WORKER_ROLE",
    }
    if not required.issubset(names):
        raise StagingSupervisorError(f"{role} worker binding is incomplete")
    if environment.get("BUFFALO_STAGING_WORKER_ROLE") != role:
        raise StagingSupervisorError(f"{role} worker role differs")
    if role == "synthetic" and not {"DATABASE_URL", "PGPASSFILE"}.issubset(names):
        raise StagingSupervisorError("synthetic database binding is absent")
    if role == "research" and not {
        "BUFFALO_PRIVATE_RESEARCH_WORKSPACE",
        "BUFFALO_RESEARCH_ROOT",
        "BUFFALO_RESEARCH_MANIFEST",
        "BUFFALO_RESEARCH_GIT_DIR",
    }.issubset(names):
        raise StagingSupervisorError("research dependency binding is absent")


def launch_fixed_child(spec: FixedChildSpec) -> subprocess.Popen[bytes]:
    spec.validate()
    return subprocess.Popen(
        list(spec.argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dict(spec.environment),
        close_fds=True,
        pass_fds=spec.pass_fds,
        user=spec.uid,
        group=spec.gid,
        extra_groups=spec.extra_groups,
        start_new_session=True,
        shell=False,
    )


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    process_group: int
    session_id: int

    @classmethod
    def capture(cls, pid: int) -> "ProcessIdentity":
        start_ticks, process_group, session_id = _proc_identity(pid)
        return cls(
            pid=pid,
            start_ticks=start_ticks,
            process_group=process_group,
            session_id=session_id,
        )

    def is_live(self) -> bool:
        try:
            start_ticks, process_group, session_id = _proc_identity(self.pid)
        except ProcessLookupError:
            return False
        return (start_ticks, process_group, session_id) == (
            self.start_ticks,
            self.process_group,
            self.session_id,
        )

    def validate_owned_session(self) -> None:
        if self.process_group != self.pid or self.session_id != self.pid:
            raise StagingSupervisorError("owned process is not a session leader")

    def group_is_live(self) -> bool:
        self.validate_owned_session()
        return bool(_owned_process_group_members(self))


@dataclass
class ManagedChild:
    spec: FixedChildSpec
    process: subprocess.Popen[bytes]
    identity: ProcessIdentity


@dataclass(frozen=True)
class ResearchChildSnapshot:
    generation: int
    identity: ProcessIdentity


@dataclass
class _ResearchAttempt:
    snapshot: ResearchChildSnapshot
    reader: ResearchReadinessReader
    proof: ResearchReadinessProof | None = None
    committed: bool = False


class StagingChildSupervisor:
    """Start fixed children and own each process/key generation to cleanup."""

    def __init__(
        self,
        *,
        isolation: StagingIsolationContract,
        gateway_spec: FixedChildSpec,
        key_lifecycle: "WorkerKeyLifecycle",
        worker_specs: tuple[FixedChildSpec, FixedChildSpec],
        worker_key_preparer: Callable[["StagedWorkerKey", ManagedChild], None],
        worker_key_committer: Callable[
            ["StagedWorkerKey", ManagedChild, ManagedChild], None
        ],
        worker_key_disabler: Callable[["StagedWorkerKey"], None],
        research_validation_identity: str,
        launcher: Callable[[FixedChildSpec], subprocess.Popen[bytes]] = launch_fixed_child,
        final_cleanup: Callable[[], None] = lambda: None,
    ) -> None:
        enable_child_subreaper()
        supervisor_identity = next(
            (
                component
                for component in isolation.components
                if component.name == "supervisor"
            ),
            None,
        )
        if (
            supervisor_identity is None
            or (os.geteuid(), os.getegid())
            != (supervisor_identity.uid, supervisor_identity.gid)
        ):
            raise StagingSupervisorError("supervisor process identity differs")
        isolation.validate_child(gateway_spec)
        _validate_key_lifecycle_binding(isolation, key_lifecycle)
        worker_templates = {spec.name: spec for spec in worker_specs}
        if len(worker_specs) != 2 or set(worker_templates) != {
            "synthetic",
            "research",
        }:
            raise StagingSupervisorError("worker template inventory differs")
        for template in worker_specs:
            isolation.validate_child(template)
        if gateway_spec.name != "gateway":
            raise StagingSupervisorError("gateway child contract differs")
        if (
            not isinstance(research_validation_identity, str)
            or len(research_validation_identity) != 64
            or any(
                character not in "0123456789abcdef"
                for character in research_validation_identity
            )
        ):
            raise StagingSupervisorError("research validation identity differs")
        self.isolation = isolation
        self.gateway_spec = gateway_spec
        self.key_lifecycle = key_lifecycle
        self.worker_templates = worker_templates
        self.worker_key_preparer = worker_key_preparer
        self.worker_key_committer = worker_key_committer
        self.worker_key_disabler = worker_key_disabler
        self.research_validation_identity = research_validation_identity
        self.launcher = launcher
        self.final_cleanup = final_cleanup
        self._lock = threading.RLock()
        self.children: dict[str, ManagedChild] = {}
        self._uncaptured_children: dict[
            str, tuple[FixedChildSpec, subprocess.Popen[bytes]]
        ] = {}
        self._gateway_started = False
        self._worker_generations = {"synthetic": 0, "research": 0}
        self._used_worker_key_digests: set[bytes] = set()
        self._reserved_worker_keys: dict[str, StagedWorkerKey] = {}
        self._active_worker_keys: dict[str, StagedWorkerKey] = {}
        self._prepared_worker_keys: set[tuple[str, int]] = set()
        self._committed_worker_keys: set[tuple[str, int]] = set()
        self._deactivated_worker_keys: set[tuple[str, int]] = set()
        self._research_attempt: _ResearchAttempt | None = None
        self._shutdown_requested = False
        self._shutdown_complete = False
        self._final_cleanup_done = False
        self._gateway_proven_gone = False
        self._activation_channel_ambiguous = False
        self._signal_requested = False

    @_serialized
    def start(self, name: str) -> ManagedChild:
        if name == "research":
            raise StagingSupervisorError(
                "research requires the generation-bound readiness launch"
            )
        return self._start_child_locked(name)

    def _start_child_locked(
        self,
        name: str,
        *,
        readiness_fd: int | None = None,
    ) -> ManagedChild:
        if (
            self._shutdown_requested
            or self._signal_requested
            or name in self.children
            or name in self._uncaptured_children
            or name in self._active_worker_keys
            or (name == "gateway" and self._gateway_started)
            or name not in {"gateway", "synthetic", "research"}
        ):
            raise StagingSupervisorError("child start transition is invalid")
        if (name == "research") != (readiness_fd is not None):
            raise StagingSupervisorError("research readiness binding differs")
        if name == "gateway":
            if self._has_owned_worker_state():
                raise StagingSupervisorError("gateway start transition is invalid")
            try:
                self._ensure_initial_worker_keys()
                spec = self.gateway_spec
                staged_key = None
                self.isolation.validate_child(spec)
                self.isolation.validate_runtime_roots()
                self.isolation.validate_private_sockets()
            except BaseException as startup_error:
                self._shutdown_requested = True
                try:
                    self._finish_shutdown_if_empty()
                except BaseException as cleanup_error:
                    raise StagingSupervisorError(
                        "gateway preparation and cleanup failed"
                    ) from startup_error
                raise
        else:
            gateway = self.children.get("gateway")
            if (
                gateway is None
                or gateway.process.poll() is not None
                or not gateway.identity.is_live()
            ):
                raise StagingSupervisorError("worker requires the live gateway")
            staged_key = self._reserved_worker_keys.pop(name, None)
            if staged_key is None:
                staged_key = self.key_lifecycle.stage(name)
                try:
                    self._accept_staged_worker_key(name, staged_key)
                except BaseException:
                    self.key_lifecycle.destroy(staged_key)
                    raise
            self._active_worker_keys[name] = staged_key
            activation_attempted = False
            try:
                base_spec = self._build_worker_spec(
                    name=name,
                    gateway=gateway,
                    staged_key=staged_key,
                )
                self._validate_worker_spec(
                    name=name,
                    spec=base_spec,
                    gateway=gateway,
                    staged_key=staged_key,
                )
                if name == "research":
                    if readiness_fd is None:
                        raise StagingSupervisorError(
                            "research readiness binding differs"
                        )
                    environment = dict(base_spec.environment)
                    environment["BUFFALO_STAGING_RESEARCH_READINESS_FD"] = str(
                        readiness_fd
                    )
                    spec = FixedChildSpec(
                        name=base_spec.name,
                        argv=base_spec.argv,
                        environment=tuple(sorted(environment.items())),
                        uid=base_spec.uid,
                        gid=base_spec.gid,
                        extra_groups=base_spec.extra_groups,
                        pass_fds=tuple(
                            sorted((*base_spec.pass_fds, readiness_fd))
                        ),
                    )
                    self.isolation.validate_research_launch(
                        template=base_spec,
                        spec=spec,
                        readiness_fd=readiness_fd,
                    )
                else:
                    spec = base_spec
                self.isolation.validate_runtime_roots()
                self.isolation.validate_private_sockets()
                if gateway.process.poll() is not None or not gateway.identity.is_live():
                    raise StagingSupervisorError("gateway exited before worker launch")
                activation_attempted = True
                self._prepared_worker_keys.add(
                    (staged_key.role, staged_key.generation)
                )
                self.worker_key_preparer(staged_key, gateway)
            except BaseException as preparation_error:
                if activation_attempted:
                    self._activation_channel_ambiguous = True
                try:
                    self._abort_worker_key(
                        name, activation_attempted=activation_attempted
                    )
                except BaseException:
                    raise StagingSupervisorError(
                        "worker preparation and key cleanup failed"
                    ) from preparation_error
                raise
        try:
            process = self.launcher(spec)
        except BaseException as launch_error:
            if staged_key is not None:
                try:
                    self._abort_worker_key(name, activation_attempted=True)
                except BaseException:
                    raise StagingSupervisorError(
                        "worker launch and key cleanup failed"
                    ) from launch_error
            else:
                try:
                    self._fail_gateway_start()
                except BaseException:
                    raise StagingSupervisorError(
                        "gateway launch and cleanup failed"
                    ) from launch_error
            raise
        try:
            identity = ProcessIdentity.capture(process.pid)
            identity.validate_owned_session()
        except BaseException as identity_error:
            try:
                _kill_uncaptured_process_group(process)
            except BaseException as exc:
                self._uncaptured_children[name] = (spec, process)
                if name != "research":
                    self._shutdown_requested = True
                raise StagingSupervisorError(
                    "child identity and cleanup failed"
                ) from identity_error
            cleanup_failures: list[BaseException] = []
            if staged_key is not None:
                try:
                    self._abort_worker_key(name, activation_attempted=True)
                except BaseException as exc:
                    cleanup_failures.append(exc)
            else:
                try:
                    self._fail_gateway_start()
                except BaseException as exc:
                    cleanup_failures.append(exc)
            if cleanup_failures:
                raise StagingSupervisorError(
                    "child identity and cleanup failed"
                ) from identity_error
            raise
        child = ManagedChild(spec=spec, process=process, identity=identity)
        self.children[name] = child
        if name != "gateway":
            gateway = self.children.get("gateway")
            if (
                gateway is None
                or gateway.process.poll() is not None
                or not gateway.identity.is_live()
            ):
                cleanup_failures: list[BaseException] = []
                self._stop_dead_gateway_first(
                    timeout_seconds=5,
                    failures=cleanup_failures,
                )
                try:
                    self._stop_owned(name, timeout_seconds=5)
                except BaseException as exc:
                    cleanup_failures.append(exc)
                if cleanup_failures:
                    raise StagingSupervisorError(
                        "gateway exit and worker cleanup failed"
                    ) from cleanup_failures[0]
                raise StagingSupervisorError("gateway exited during worker launch")
        if name == "gateway":
            self._gateway_started = True
        return child

    @_serialized
    def start_research(self, minimum_generation: int) -> ResearchChildSnapshot:
        """Launch one research attempt with a fresh authenticated readiness pipe."""

        if (
            type(minimum_generation) is not int
            or not 0 <= minimum_generation <= 2**63 - 1
            or self._research_attempt is not None
        ):
            raise StagingSupervisorError("research launch request differs")
        reserved = self._reserved_worker_keys.get("research")
        next_generation = (
            reserved.generation
            if reserved is not None
            else self._worker_generations["research"]
        )
        if not minimum_generation <= next_generation <= 2**63 - 1:
            raise StagingSupervisorError("research key generation is below minimum")
        try:
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        except OSError as exc:
            raise StagingSupervisorError(
                "research readiness pipe creation failed"
            ) from exc
        reader_owned = False
        child: ManagedChild | None = None
        try:
            static_fds = {
                descriptor
                for template in self.worker_templates.values()
                for descriptor in template.pass_fds
            } | set(self.gateway_spec.pass_fds)
            if (
                read_fd < 3
                or write_fd < 3
                or read_fd == write_fd
                or read_fd in static_fds
                or write_fd in static_fds
            ):
                raise StagingSupervisorError("research readiness pipe differs")
            child = self._start_child_locked("research", readiness_fd=write_fd)
            staged_key = self._active_worker_keys["research"]
            if staged_key.generation < minimum_generation:
                raise StagingSupervisorError(
                    "research key generation is below minimum"
                )
            snapshot = ResearchChildSnapshot(
                generation=staged_key.generation,
                identity=child.identity,
            )
            readiness_identity = ReadinessProcessIdentity(
                pid=child.identity.pid,
                start_ticks=child.identity.start_ticks,
                process_group=child.identity.process_group,
                session_id=child.identity.session_id,
            )
            reader = ResearchReadinessReader(
                read_fd,
                key=staged_key.key,
                expected_generation=staged_key.generation,
                expected_identity=readiness_identity,
                expected_validation_identity=self.research_validation_identity,
            )
            reader_owned = True
            self._research_attempt = _ResearchAttempt(
                snapshot=snapshot,
                reader=reader,
            )
            return snapshot
        except BaseException as startup_error:
            cleanup_error: BaseException | None = None
            if child is not None and "research" in self.children:
                try:
                    self._stop_owned("research", timeout_seconds=10.0)
                except BaseException as exc:
                    cleanup_error = exc
            if cleanup_error is not None:
                raise StagingSupervisorError(
                    "research launch and cleanup failed"
                ) from startup_error
            raise
        finally:
            try:
                os.close(write_fd)
            except OSError:
                pass
            if not reader_owned:
                try:
                    os.close(read_fd)
                except OSError:
                    pass

    def _ensure_initial_worker_keys(self) -> None:
        if self._reserved_worker_keys:
            return
        staged: list[StagedWorkerKey] = []
        try:
            for role in ("synthetic", "research"):
                value = self.key_lifecycle.stage(role)
                self._accept_staged_worker_key(role, value)
                staged.append(value)
            environment = dict(self.gateway_spec.environment)
            for value in staged:
                contract = self.key_lifecycle.contracts[value.role]
                prefix = f"BUFFALO_STAGING_{value.role.upper()}_KEY"
                if (
                    environment.get(f"{prefix}_FILE")
                    != str(value.gateway_path)
                    or environment.get(f"{prefix}_DIRECTORY_UID")
                    != str(contract.gateway_parent_uid)
                    or environment.get(f"{prefix}_DIRECTORY_GID")
                    != str(contract.gateway_parent_gid)
                ):
                    raise StagingSupervisorError(
                        f"gateway {value.role} key binding differs"
                    )
            self._reserved_worker_keys = {value.role: value for value in staged}
        except BaseException:
            cleanup_failures: list[BaseException] = []
            for value in staged:
                try:
                    self.key_lifecycle.destroy(value)
                except BaseException as exc:
                    cleanup_failures.append(exc)
            if cleanup_failures:
                raise StagingSupervisorError(
                    "initial worker key cleanup failed"
                ) from cleanup_failures[0]
            raise

    def _accept_staged_worker_key(
        self, role: str, staged_key: "StagedWorkerKey"
    ) -> None:
        if (
            not isinstance(staged_key, StagedWorkerKey)
            or staged_key.role != role
            or staged_key.generation != self._worker_generations[role]
        ):
            raise StagingSupervisorError("worker key generation is not consecutive")
        digest = hmac.digest(
            b"BUFFALO_STAGING_KEY_HISTORY_V1", staged_key.key, "sha256"
        )
        if any(
            hmac.compare_digest(digest, used)
            for used in self._used_worker_key_digests
        ):
            raise StagingSupervisorError("worker key was reused")
        self._used_worker_key_digests.add(digest)
        self._worker_generations[role] = staged_key.generation + 1

    def _validate_worker_spec(
        self,
        *,
        name: str,
        spec: FixedChildSpec,
        gateway: ManagedChild,
        staged_key: "StagedWorkerKey",
    ) -> None:
        self.isolation.validate_child(spec)
        environment = dict(spec.environment)
        contract = self.key_lifecycle.contracts[name]
        if (
            environment.get("BUFFALO_STAGING_GATEWAY_PID")
            != str(gateway.identity.pid)
            or environment.get("BUFFALO_STAGING_GATEWAY_UID")
            != str(gateway.spec.uid)
            or environment.get("BUFFALO_STAGING_GATEWAY_GID")
            != str(gateway.spec.gid)
            or environment.get("BUFFALO_STAGING_ASSERTION_KEY_FILE")
            != str(staged_key.worker_path)
            or environment.get("BUFFALO_STAGING_KEY_GENERATION")
            != str(staged_key.generation)
            or environment.get("BUFFALO_STAGING_KEY_DIRECTORY_UID")
            != str(contract.worker_parent_uid)
            or environment.get("BUFFALO_STAGING_KEY_DIRECTORY_GID")
            != str(contract.worker_parent_gid)
        ):
            raise StagingSupervisorError("worker gateway/key binding differs")

    def _build_worker_spec(
        self,
        *,
        name: str,
        gateway: ManagedChild,
        staged_key: "StagedWorkerKey",
    ) -> FixedChildSpec:
        template = self.worker_templates[name]
        environment = dict(template.environment)
        environment["BUFFALO_STAGING_GATEWAY_PID"] = str(gateway.identity.pid)
        environment["BUFFALO_STAGING_ASSERTION_KEY_FILE"] = str(
            staged_key.worker_path
        )
        environment["BUFFALO_STAGING_KEY_GENERATION"] = str(
            staged_key.generation
        )
        return FixedChildSpec(
            name=template.name,
            argv=template.argv,
            environment=tuple(sorted(environment.items())),
            uid=template.uid,
            gid=template.gid,
            extra_groups=template.extra_groups,
            pass_fds=template.pass_fds,
        )

    def _abort_worker_key(self, name: str, *, activation_attempted: bool) -> None:
        staged_key = self._active_worker_keys[name]
        generation = (staged_key.role, staged_key.generation)
        if (
            (activation_attempted or generation in self._prepared_worker_keys)
            and generation not in self._deactivated_worker_keys
        ):
            if not self._gateway_proven_gone:
                try:
                    self.worker_key_disabler(staged_key)
                except BaseException as exc:
                    self._activation_channel_ambiguous = True
                    # Retain both manager ownership and key copies so DISABLE
                    # can be retried; deleting a possibly prepared key is unsafe.
                    raise StagingSupervisorError(
                        "worker key disable was not acknowledged"
                    ) from exc
            self._deactivated_worker_keys.add(generation)
        try:
            self.key_lifecycle.destroy(staged_key)
        except BaseException as exc:
            raise StagingSupervisorError("worker key destruction failed") from exc
        self._active_worker_keys.pop(name)
        self._prepared_worker_keys.discard(generation)
        self._deactivated_worker_keys.discard(generation)
        self._committed_worker_keys.discard(generation)

    def _fail_gateway_start(self) -> None:
        self._shutdown_requested = True
        self._finish_shutdown_if_empty()

    @_serialized
    def start_initial(self) -> None:
        """Ordinary startup includes gateway+synthetic but never research."""

        started: list[str] = []
        try:
            for name in ("gateway", "synthetic"):
                self.start(name)
                started.append(name)
        except BaseException as startup_error:
            self._shutdown_requested = True
            cleanup_failures: list[BaseException] = []
            self._cleanup_uncaptured_children(failures=cleanup_failures)
            self._stop_dead_gateway_first(
                timeout_seconds=10.0,
                failures=cleanup_failures,
            )
            for role in ("research", "synthetic"):
                if (
                    role in self._active_worker_keys
                    and role not in self.children
                    and role not in self._uncaptured_children
                ):
                    try:
                        self._abort_worker_key(
                            role,
                            activation_attempted=False,
                        )
                    except BaseException as exc:
                        cleanup_failures.append(exc)
            for name in reversed(started):
                if name == "gateway" and self._has_owned_worker_state():
                    cleanup_failures.append(
                        StagingSupervisorError(
                            "gateway retained until worker cleanup completes"
                        )
                    )
                    continue
                try:
                    self._stop_owned(name, timeout_seconds=10.0)
                except BaseException as exc:
                    cleanup_failures.append(exc)
            self._finish_shutdown_if_empty()
            if cleanup_failures:
                raise StagingSupervisorError(
                    "initial child startup and cleanup failed"
                ) from startup_error
            raise

    @_serialized
    def commit_worker(self, name: str) -> None:
        """Make a prepared worker routable only after external readiness proof."""

        if name == "research":
            raise StagingSupervisorError(
                "research requires the generation-bound readiness commit"
            )
        self._commit_worker_locked(name, terminal_on_unknown=True)

    def _commit_worker_locked(
        self,
        name: str,
        *,
        terminal_on_unknown: bool,
    ) -> None:
        if name not in {"synthetic", "research"}:
            raise StagingSupervisorError("worker commit role is invalid")
        if self._shutdown_requested or self._shutdown_complete or self._signal_requested:
            raise StagingSupervisorError("worker cannot be committed")
        worker = self.children.get(name)
        gateway = self.children.get("gateway")
        staged_key = self._active_worker_keys.get(name)
        if (
            worker is None
            or gateway is None
            or staged_key is None
            or worker.process.poll() is not None
            or gateway.process.poll() is not None
            or not worker.identity.is_live()
            or not gateway.identity.is_live()
            or (staged_key.role, staged_key.generation)
            in self._deactivated_worker_keys
        ):
            raise StagingSupervisorError("worker cannot be committed")
        generation = (staged_key.role, staged_key.generation)
        if generation not in self._prepared_worker_keys:
            raise StagingSupervisorError("worker is not prepared")
        if generation in self._committed_worker_keys:
            raise StagingSupervisorError("worker is already committed")
        try:
            self.worker_key_committer(staged_key, gateway, worker)
        except BaseException as exc:
            self._activation_channel_ambiguous = True
            # A lost/failed acknowledgement cannot prove whether the gateway
            # made the generation routable.  Enter terminal cleanup rather
            # than retrying COMMIT or serving with ambiguous state.
            if terminal_on_unknown:
                self._shutdown_requested = True
            raise StagingSupervisorError(
                "worker commit acknowledgement is unknown"
            ) from exc
        self._committed_worker_keys.add(generation)

    def _research_process_is_live(self, attempt: _ResearchAttempt) -> bool:
        child = self.children.get("research")
        staged_key = self._active_worker_keys.get("research")
        state: str | None = None
        identity_values: tuple[int, int, int] | None = None
        if child is not None:
            try:
                state, start_ticks, process_group, session_id = _proc_record(
                    child.identity.pid
                )
                identity_values = (start_ticks, process_group, session_id)
            except (ProcessLookupError, StagingSupervisorError):
                pass
        return bool(
            child is not None
            and staged_key is not None
            and child.identity == attempt.snapshot.identity
            and staged_key.generation == attempt.snapshot.generation
            # Do not call Popen.poll here: it reaps a dead leader before the
            # resource sampler can retain its final VmHWM.  A zombie is not
            # live, but remains owned until generation-bound termination.
            and state not in {None, "Z", "X", "x"}
            and identity_values
            == (
                child.identity.start_ticks,
                child.identity.process_group,
                child.identity.session_id,
            )
        )

    def _probe_research_locked(
        self, generation: int
    ) -> ResearchReadinessProof | None:
        attempt = self._research_attempt
        if (
            type(generation) is not int
            or attempt is None
            or attempt.snapshot.generation != generation
        ):
            raise StagingSupervisorError("research readiness generation differs")
        proof = attempt.proof
        if proof is None:
            try:
                proof = attempt.reader.poll()
            except ResearchReadinessAbsent:
                if not self._research_process_is_live(attempt):
                    raise ResearchChildCrashed(
                        "research child exited without an accepted failure proof"
                    ) from None
                raise
            if proof is not None:
                attempt.proof = proof
        if proof is not None and not proof.ready:
            return proof
        if not self._research_process_is_live(attempt):
            raise ResearchChildCrashed(
                "research child exited without an accepted failure proof"
            )
        gateway = self.children.get("gateway")
        if (
            gateway is None
            or gateway.process.poll() is not None
            or not gateway.identity.is_live()
        ):
            raise StagingSupervisorError("research readiness lost the gateway")
        return proof

    @_serialized
    def probe_research(
        self, generation: int
    ) -> ResearchReadinessProof | None:
        """Poll only the fresh readiness channel bound to this generation."""

        return self._probe_research_locked(generation)

    @_serialized
    def commit_research(self, generation: int) -> None:
        """Commit the exact live generation only after its signed READY proof."""

        proof = self._probe_research_locked(generation)
        if proof is None or not proof.ready:
            raise StagingSupervisorError("research worker is not ready")
        attempt = self._research_attempt
        if attempt is None:
            raise StagingSupervisorError("research readiness generation differs")
        if attempt.committed:
            raise StagingSupervisorError("worker is already committed")
        self._commit_worker_locked("research", terminal_on_unknown=False)
        if not self._research_process_is_live(attempt):
            raise ResearchChildCrashed("research child exited during commit")
        attempt.committed = True

    @_serialized
    def research_snapshot(self) -> ResearchChildSnapshot | None:
        attempt = self._research_attempt
        return None if attempt is None else attempt.snapshot

    @_serialized
    def owned_process_identities(self) -> tuple[ProcessIdentity, ...]:
        """Return immutable identities without polling or reaping any child."""

        if self._uncaptured_children:
            raise StagingSupervisorError("uncaptured child identity is unavailable")
        return tuple(
            self.children[name].identity for name in sorted(self.children)
        )

    @_serialized
    def stop(self, name: str, *, timeout_seconds: float = 10.0) -> int:
        if name == "research":
            raise StagingSupervisorError(
                "research requires the generation-bound termination"
            )
        if name == "gateway" and self._has_owned_worker_state():
            raise StagingSupervisorError("gateway cannot stop before workers")
        return self._stop_owned(name, timeout_seconds=timeout_seconds)

    def _stop_owned(self, name: str, *, timeout_seconds: float) -> int:
        try:
            child = self.children[name]
        except KeyError:
            raise StagingSupervisorError("child stop transition is invalid") from None
        staged_key: StagedWorkerKey | None = None
        generation: tuple[str, int] | None = None
        if name in {"synthetic", "research"}:
            staged_key = self._active_worker_keys[name]
            generation = (staged_key.role, staged_key.generation)
            if generation not in self._deactivated_worker_keys:
                if not self._gateway_proven_gone:
                    try:
                        self.worker_key_disabler(staged_key)
                    except BaseException:
                        self._activation_channel_ambiguous = True
                        raise
                self._deactivated_worker_keys.add(generation)
        result = terminate_owned_process_group(
            child.process,
            child.identity,
            timeout_seconds=timeout_seconds,
        )
        if staged_key is not None and generation is not None:
            self.key_lifecycle.destroy(staged_key)
            self._active_worker_keys.pop(name)
            self._prepared_worker_keys.discard(generation)
            self._deactivated_worker_keys.discard(generation)
            self._committed_worker_keys.discard(generation)
        self.children.pop(name)
        if name == "gateway":
            self._gateway_proven_gone = True
        elif name == "research":
            self._close_research_attempt()
        return result

    def _close_research_attempt(self) -> None:
        attempt = self._research_attempt
        if attempt is None:
            return
        attempt.reader.close()
        self._research_attempt = None

    @_serialized
    def terminate_research(
        self,
        generation: int | None,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Disable and remove every exact-owned state for one research attempt."""

        if generation is not None and (
            type(generation) is not int or not 0 <= generation <= 2**63 - 1
        ):
            raise StagingSupervisorError("research termination generation differs")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise StagingSupervisorError("owned process timeout is invalid")
        attempt = self._research_attempt
        binding_differs = bool(
            generation is not None
            and attempt is not None
            and generation != attempt.snapshot.generation
        )
        if "research" in self.children:
            self._stop_owned("research", timeout_seconds=timeout_seconds)
        else:
            staged_key = self._active_worker_keys.get("research")
            token: tuple[str, int] | None = None
            if staged_key is not None:
                token = (staged_key.role, staged_key.generation)
                if token not in self._deactivated_worker_keys:
                    if not self._gateway_proven_gone:
                        try:
                            self.worker_key_disabler(staged_key)
                        except BaseException as exc:
                            self._activation_channel_ambiguous = True
                            raise StagingSupervisorError(
                                "worker key disable was not acknowledged"
                            ) from exc
                    self._deactivated_worker_keys.add(token)
            uncaptured = self._uncaptured_children.get("research")
            if uncaptured is not None:
                try:
                    _kill_uncaptured_process_group(uncaptured[1])
                except BaseException as exc:
                    raise StagingSupervisorError(
                        "research uncaptured process cleanup failed"
                    ) from exc
                self._uncaptured_children.pop("research")
            if staged_key is not None and token is not None:
                try:
                    self.key_lifecycle.destroy(staged_key)
                except BaseException as exc:
                    raise StagingSupervisorError(
                        "worker key destruction failed"
                    ) from exc
                self._active_worker_keys.pop("research")
                self._prepared_worker_keys.discard(token)
                self._deactivated_worker_keys.discard(token)
                self._committed_worker_keys.discard(token)
            self._close_research_attempt()
        reserved = self._reserved_worker_keys.get("research")
        if reserved is not None:
            try:
                self.key_lifecycle.destroy(reserved)
            except BaseException as exc:
                raise StagingSupervisorError(
                    "reserved research key destruction failed"
                ) from exc
            self._reserved_worker_keys.pop("research")
        try:
            self.key_lifecycle.destroy_role("research")
        except BaseException as exc:
            raise StagingSupervisorError(
                "research role key cleanup failed"
            ) from exc
        if binding_differs:
            raise StagingSupervisorError("research termination generation differs")

    @_serialized
    def reap_crashed(self) -> tuple[str, ...]:
        crashed = tuple(
            name
            for name, child in self.children.items()
            if name != "research"
            if child.process.poll() is not None
        )
        uncaptured_terminal = any(
            name != "research" for name in self._uncaptured_children
        )
        if not crashed and not uncaptured_terminal:
            return crashed
        self._shutdown_requested = True
        failures: list[BaseException] = []
        self._cleanup_uncaptured_children(failures=failures)
        self._stop_dead_gateway_first(
            timeout_seconds=10.0,
            failures=failures,
        )
        for name in ("research", "synthetic", "gateway"):
            if name in self._uncaptured_children:
                continue
            if name not in self.children and name not in self._active_worker_keys:
                continue
            if name == "gateway" and self._has_owned_worker_state():
                failures.append(
                    StagingSupervisorError(
                        "gateway retained until worker cleanup completes"
                    )
                )
                continue
            try:
                if name in self.children:
                    self._stop_owned(name, timeout_seconds=10.0)
                else:
                    self._abort_worker_key(name, activation_attempted=False)
            except BaseException as exc:
                failures.append(exc)
        if (
            self._research_attempt is not None
            and "research" not in self.children
            and "research" not in self._active_worker_keys
            and "research" not in self._uncaptured_children
        ):
            try:
                self._close_research_attempt()
            except BaseException as exc:
                failures.append(exc)
        self._finish_shutdown_if_empty()
        if failures:
            raise StagingSupervisorError("crash cleanup did not stop every child") from failures[0]
        return crashed

    @_serialized
    def shutdown(self, *, timeout_seconds: float = 10.0) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_requested = True
        failures: list[BaseException] = []
        self._cleanup_uncaptured_children(failures=failures)
        self._stop_dead_gateway_first(
            timeout_seconds=timeout_seconds,
            failures=failures,
        )
        for name in ("research", "synthetic", "gateway"):
            if name in self._uncaptured_children:
                continue
            if name in self.children or name in self._active_worker_keys:
                if name == "gateway" and self._has_owned_worker_state():
                    failures.append(
                        StagingSupervisorError(
                            "gateway retained until worker cleanup completes"
                        )
                    )
                    continue
                try:
                    if name in self.children:
                        self._stop_owned(name, timeout_seconds=timeout_seconds)
                    else:
                        self._abort_worker_key(name, activation_attempted=False)
                except BaseException as exc:
                    failures.append(exc)
        if (
            self._research_attempt is not None
            and "research" not in self.children
            and "research" not in self._active_worker_keys
            and "research" not in self._uncaptured_children
        ):
            try:
                self._close_research_attempt()
            except BaseException as exc:
                failures.append(exc)
        self._finish_shutdown_if_empty()
        if (
            failures
            and self._activation_channel_ambiguous
            and not self._gateway_proven_gone
        ):
            fallback_failures: list[BaseException] = []
            self._force_gateway_gone_for_terminal_shutdown(
                timeout_seconds=timeout_seconds,
                failures=fallback_failures,
            )
            self._cleanup_uncaptured_children(failures=fallback_failures)
            for name in ("research", "synthetic"):
                try:
                    if name in self.children:
                        self._stop_owned(name, timeout_seconds=timeout_seconds)
                    elif name in self._active_worker_keys:
                        self._abort_worker_key(name, activation_attempted=False)
                except BaseException as exc:
                    fallback_failures.append(exc)
            if (
                self._research_attempt is not None
                and "research" not in self.children
                and "research" not in self._active_worker_keys
                and "research" not in self._uncaptured_children
            ):
                try:
                    self._close_research_attempt()
                except BaseException as exc:
                    fallback_failures.append(exc)
            self._finish_shutdown_if_empty()
            if not fallback_failures and self._shutdown_complete:
                return
            failures = fallback_failures or failures
        if failures:
            raise StagingSupervisorError("one or more child groups did not stop") from failures[0]

    def _force_gateway_gone_for_terminal_shutdown(
        self,
        *,
        timeout_seconds: float,
        failures: list[BaseException],
    ) -> None:
        """Resolve poisoned activation state only during whole-service shutdown."""

        gateway = self.children.get("gateway")
        if gateway is not None:
            try:
                self._stop_owned("gateway", timeout_seconds=timeout_seconds)
            except BaseException as exc:
                failures.append(exc)
                return
        uncaptured = self._uncaptured_children.get("gateway")
        if uncaptured is not None:
            try:
                _kill_uncaptured_process_group(uncaptured[1])
            except BaseException as exc:
                failures.append(exc)
                return
            self._uncaptured_children.pop("gateway")
            self._gateway_proven_gone = True
        if self._gateway_started and not self._gateway_proven_gone:
            failures.append(
                StagingSupervisorError("gateway absence is not proven")
            )

    def _finish_shutdown_if_empty(self) -> None:
        if (
            self.children
            or self._uncaptured_children
            or self._active_worker_keys
            or self._research_attempt is not None
        ):
            return
        if not self._final_cleanup_done:
            self.key_lifecycle.destroy_all()
            self._reserved_worker_keys.clear()
            self.final_cleanup()
            self._final_cleanup_done = True
        self._shutdown_complete = True

    def _has_owned_worker_state(self) -> bool:
        return any(
            role in self.children
            or role in self._uncaptured_children
            or role in self._active_worker_keys
            for role in ("synthetic", "research")
        )

    def _cleanup_uncaptured_children(
        self, *, failures: list[BaseException]
    ) -> None:
        for name in ("research", "synthetic", "gateway"):
            record = self._uncaptured_children.get(name)
            if record is None:
                continue
            _, process = record
            if name in {"synthetic", "research"}:
                staged_key = self._active_worker_keys.get(name)
                if staged_key is not None:
                    generation = (staged_key.role, staged_key.generation)
                    if generation not in self._deactivated_worker_keys:
                        if not self._gateway_proven_gone:
                            try:
                                self.worker_key_disabler(staged_key)
                            except BaseException as exc:
                                self._activation_channel_ambiguous = True
                                failures.append(exc)
                                continue
                        self._deactivated_worker_keys.add(generation)
            try:
                _kill_uncaptured_process_group(process)
            except BaseException as exc:
                failures.append(exc)
                continue
            self._uncaptured_children.pop(name)
            if name == "gateway":
                self._gateway_proven_gone = True

    def _stop_dead_gateway_first(
        self,
        *,
        timeout_seconds: float,
        failures: list[BaseException],
    ) -> None:
        gateway = self.children.get("gateway")
        if gateway is None:
            return
        if gateway.process.poll() is None and gateway.identity.is_live():
            return
        try:
            self._stop_owned("gateway", timeout_seconds=timeout_seconds)
        except BaseException as exc:
            failures.append(exc)

    def signal_handler(self, signum: int, _frame: object = None) -> None:
        if signum not in {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}:
            raise StagingSupervisorError("supervisor signal is invalid")
        # Python signal handlers may interrupt lifecycle transactions between
        # bytecodes.  Record only; the serialized supervisor loop drains this
        # after the current transaction reaches a stable boundary.
        self._signal_requested = True

    @_serialized
    def process_pending_signal(self) -> bool:
        """Drain one recorded signal from the serialized supervisor loop."""

        if not self._signal_requested:
            return False
        self._signal_requested = False
        try:
            self.shutdown()
        except BaseException:
            self._signal_requested = True
            raise
        return True


def terminate_owned_process_group(
    process: subprocess.Popen[bytes],
    identity: ProcessIdentity,
    *,
    timeout_seconds: float = 10.0,
) -> int:
    if process.pid != identity.pid:
        raise StagingSupervisorError("owned process identity differs")
    if timeout_seconds <= 0 or timeout_seconds > 60:
        raise StagingSupervisorError("owned process timeout is invalid")
    identity.validate_owned_session()
    enable_child_subreaper()
    leader_status = process.poll()
    _reap_owned_descendants(process, identity)
    if leader_status is None and not identity.is_live():
        # The leader may have exited normally between the first poll and the
        # identity read.  Re-poll before treating this as an identity mismatch;
        # a reaped leader still leaves this supervisor responsible for every
        # surviving member of its exact process group/session.
        leader_status = process.poll()
        _reap_owned_descendants(process, identity)
        if leader_status is None:
            raise StagingSupervisorError("owned process identity is no longer live")
    if leader_status is not None and not identity.group_is_live():
        return process.wait()
    try:
        os.killpg(identity.process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        process.poll()
        _reap_owned_descendants(process, identity)
        if not identity.group_is_live():
            break
        time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
    if identity.group_is_live():
        try:
            os.killpg(identity.process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        kill_deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < kill_deadline:
            process.poll()
            _reap_owned_descendants(process, identity)
            if not identity.group_is_live():
                break
            time.sleep(min(0.02, max(0.0, kill_deadline - time.monotonic())))
        if identity.group_is_live():
            raise StagingSupervisorError("owned process group did not stop")
    if process.poll() is None:
        return process.wait(timeout=timeout_seconds)
    return process.wait()


def _kill_uncaptured_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill a just-launched session even when its leader exited before capture."""

    provisional = ProcessIdentity(
        pid=process.pid,
        start_ticks=0,
        process_group=process.pid,
        session_id=process.pid,
    )
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        process.poll()
        _reap_owned_descendants(process, provisional)
        if not provisional.group_is_live():
            break
        time.sleep(0.01)
    try:
        process.wait(timeout=1)
    except BaseException:
        try:
            process.kill()
            process.wait(timeout=1)
        except BaseException:
            pass
    if provisional.group_is_live():
        raise StagingSupervisorError("uncaptured child process group survived cleanup")


def remove_owned_stale_socket(
    contract: SocketContract, *, identity: ProcessIdentity
) -> None:
    if identity.is_live() or identity.group_is_live():
        raise StagingSupervisorError("stale socket ownership is not proven")
    try:
        contract.path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    try:
        validate_socket_contract(contract)
    except StagingUdsError as exc:
        raise StagingSupervisorError("stale socket contract differs") from exc
    contract.path.unlink()


def write_private_key_copy(
    path: Path,
    *,
    key: bytes,
    uid: int,
    gid: int,
    parent_uid: int,
    parent_gid: int,
    on_created: Callable[[tuple[int, int]], None] | None = None,
) -> None:
    if not path.is_absolute() or not isinstance(key, bytes) or len(key) != 32:
        raise StagingSupervisorError("private key-copy request is invalid")
    parent = path.parent.stat(follow_symlinks=False)
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o710
        or parent.st_uid != parent_uid
        or parent.st_gid != parent_gid
        or path.exists()
        or path.is_symlink()
    ):
        raise StagingSupervisorError("private key-copy parent differs")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    created_identity: tuple[int, int] | None = None
    try:
        created = os.fstat(descriptor)
        created_identity = (created.st_dev, created.st_ino)
        if on_created is not None:
            on_created(created_identity)
        remaining = memoryview(key)
        while remaining:
            written = os.write(descriptor, remaining)
            if written < 1:
                raise StagingSupervisorError("private key-copy write failed")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, uid, gid)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != uid
            or info.st_gid != gid
            or info.st_size != 32
        ):
            raise StagingSupervisorError("private key-copy contract differs")
    except BaseException:
        try:
            current = path.stat(follow_symlinks=False)
            if created_identity == (current.st_dev, current.st_ino):
                path.unlink()
        except (FileNotFoundError, OSError):
            pass
        raise
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class WorkerKeyCopyContract:
    role: str
    gateway_directory: Path
    worker_directory: Path
    gateway_uid: int
    gateway_gid: int
    worker_uid: int
    worker_gid: int
    gateway_parent_uid: int
    gateway_parent_gid: int
    worker_parent_uid: int
    worker_parent_gid: int

    def validate(self) -> None:
        if self.role not in {"synthetic", "research"}:
            raise StagingSupervisorError("worker key role is invalid")
        if (
            not self.gateway_directory.is_absolute()
            or not self.worker_directory.is_absolute()
            or self.gateway_directory == self.worker_directory
            or any(
                part in {".", ".."}
                for root in (self.gateway_directory, self.worker_directory)
                for part in root.parts
            )
            or any(
                type(value) is not int or value < 1
                for value in (
                    self.gateway_uid,
                    self.gateway_gid,
                    self.worker_uid,
                    self.worker_gid,
                    self.gateway_parent_gid,
                    self.worker_parent_gid,
                )
            )
            or any(
                type(value) is not int or value < 0
                for value in (
                    self.gateway_parent_uid,
                    self.worker_parent_uid,
                )
            )
        ):
            raise StagingSupervisorError("worker key-copy contract differs")
        _validate_private_directory(
            self.gateway_directory,
            uid=self.gateway_parent_uid,
            gid=self.gateway_parent_gid,
        )
        _validate_private_directory(
            self.worker_directory,
            uid=self.worker_parent_uid,
            gid=self.worker_parent_gid,
        )


@dataclass(frozen=True)
class StagedWorkerKey:
    role: str
    generation: int
    gateway_path: Path
    worker_path: Path
    key: bytes = dataclass_field(repr=False)


class WorkerKeyLifecycle:
    """Generate two private copies per role and prevent boot-time key reuse."""

    def __init__(
        self,
        contracts: tuple[WorkerKeyCopyContract, ...],
        *,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        by_role = {contract.role: contract for contract in contracts}
        if len(contracts) != 2 or set(by_role) != {"synthetic", "research"}:
            raise StagingSupervisorError("worker key contract inventory differs")
        for contract in contracts:
            contract.validate()
        all_directories = {
            directory
            for contract in contracts
            for directory in (
                contract.gateway_directory,
                contract.worker_directory,
            )
        }
        directory_list = tuple(all_directories)
        if len(all_directories) != 4 or any(
            first != second
            and (first.is_relative_to(second) or second.is_relative_to(first))
            for index, first in enumerate(directory_list)
            for second in directory_list[index + 1 :]
        ):
            raise StagingSupervisorError("worker key directories are not isolated")
        self.contracts = by_role
        self.random_bytes = random_bytes
        self._lock = threading.RLock()
        self._generations = {"synthetic": -1, "research": -1}
        self._used_digests: set[bytes] = set()
        self._staged: dict[tuple[str, int], StagedWorkerKey] = {}
        self._incomplete: set[tuple[str, int]] = set()
        self._incomplete_identities: dict[
            tuple[str, int], dict[Path, tuple[int, int]]
        ] = {}

    @_serialized
    def stage(self, role: str) -> StagedWorkerKey:
        try:
            contract = self.contracts[role]
        except KeyError:
            raise StagingSupervisorError("worker key role is invalid") from None
        generation = self._generations[role] + 1
        key = self.random_bytes(32)
        if not isinstance(key, bytes) or len(key) != 32:
            raise StagingSupervisorError("worker key generator differed")
        digest = hmac.digest(b"BUFFALO_STAGING_KEY_HISTORY_V1", key, "sha256")
        if digest in self._used_digests:
            raise StagingSupervisorError("worker key was reused")
        # Burn a generated value before either filesystem write.  A partial
        # write may expose the key even when staging ultimately fails.
        self._used_digests.add(digest)
        gateway_path = contract.gateway_directory / f"{role}-{generation}.key"
        worker_path = contract.worker_directory / f"{role}-{generation}.key"
        if (
            gateway_path.exists()
            or gateway_path.is_symlink()
            or worker_path.exists()
            or worker_path.is_symlink()
        ):
            raise StagingSupervisorError("worker key path is already occupied")
        token = (role, generation)
        if token in self._staged:
            raise StagingSupervisorError("worker key cleanup remains pending")
        staged = StagedWorkerKey(
            role=role,
            generation=generation,
            gateway_path=gateway_path,
            worker_path=worker_path,
            key=key,
        )
        # Reserve transaction state before the first write, but record path
        # ownership only from each successful O_EXCL open callback.  This lets
        # destroy_all() retry our exact inode without deleting a racing file.
        self._staged[token] = staged
        self._incomplete.add(token)
        self._incomplete_identities[token] = {}
        try:
            write_private_key_copy(
                gateway_path,
                key=key,
                uid=contract.gateway_uid,
                gid=contract.gateway_gid,
                parent_uid=contract.gateway_parent_uid,
                parent_gid=contract.gateway_parent_gid,
                on_created=lambda identity: self._incomplete_identities[token].__setitem__(
                    gateway_path, identity
                ),
            )
            write_private_key_copy(
                worker_path,
                key=key,
                uid=contract.worker_uid,
                gid=contract.worker_gid,
                parent_uid=contract.worker_parent_uid,
                parent_gid=contract.worker_parent_gid,
                on_created=lambda identity: self._incomplete_identities[token].__setitem__(
                    worker_path, identity
                ),
            )
        except BaseException as staging_error:
            try:
                self._destroy_incomplete(staged)
            except BaseException:
                raise StagingSupervisorError(
                    "worker key staging and cleanup failed"
                ) from staging_error
            raise
        self._incomplete.remove(token)
        self._incomplete_identities.pop(token)
        self._generations[role] = generation
        return staged

    @_serialized
    def destroy(self, staged: StagedWorkerKey) -> None:
        current = self._staged.get((staged.role, staged.generation))
        if current != staged:
            raise StagingSupervisorError("worker key generation is not owned")
        token = (staged.role, staged.generation)
        if token in self._incomplete:
            self._destroy_incomplete(staged)
            return
        contract = self.contracts[staged.role]
        _unlink_private_key(
            staged.gateway_path,
            uid=contract.gateway_uid,
            gid=contract.gateway_gid,
            parent_uid=contract.gateway_parent_uid,
            parent_gid=contract.gateway_parent_gid,
        )
        _unlink_private_key(
            staged.worker_path,
            uid=contract.worker_uid,
            gid=contract.worker_gid,
            parent_uid=contract.worker_parent_uid,
            parent_gid=contract.worker_parent_gid,
        )
        self._staged.pop(token)

    def _destroy_incomplete(self, staged: StagedWorkerKey) -> None:
        token = (staged.role, staged.generation)
        if self._staged.get(token) != staged or token not in self._incomplete:
            raise StagingSupervisorError("incomplete worker key is not owned")
        contract = self.contracts[staged.role]
        identities = self._incomplete_identities[token]
        failures: list[BaseException] = []
        for path, uid, gid, parent_uid, parent_gid in (
            (
                staged.gateway_path,
                contract.gateway_uid,
                contract.gateway_gid,
                contract.gateway_parent_uid,
                contract.gateway_parent_gid,
            ),
            (
                staged.worker_path,
                contract.worker_uid,
                contract.worker_gid,
                contract.worker_parent_uid,
                contract.worker_parent_gid,
            ),
        ):
            identity = identities.get(path)
            if identity is None:
                continue
            try:
                _unlink_incomplete_private_key(
                    path,
                    uid=uid,
                    gid=gid,
                    parent_uid=parent_uid,
                    parent_gid=parent_gid,
                    identity=identity,
                )
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise StagingSupervisorError(
                "incomplete worker key could not be destroyed"
            ) from failures[0]
        self._incomplete.remove(token)
        self._incomplete_identities.pop(token)
        self._staged.pop(token)

    @_serialized
    def destroy_all(self) -> None:
        failures: list[BaseException] = []
        for staged in tuple(self._staged.values()):
            try:
                self.destroy(staged)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise StagingSupervisorError(
                "one or more worker key generations could not be destroyed"
            ) from failures[0]

    @_serialized
    def destroy_role(self, role: str) -> None:
        """Destroy every exact-owned key state for one worker role only."""

        if role not in self.contracts:
            raise StagingSupervisorError("worker key role is invalid")
        failures: list[BaseException] = []
        for staged in tuple(self._staged.values()):
            if staged.role != role:
                continue
            try:
                self.destroy(staged)
            except BaseException as exc:
                failures.append(exc)
        if failures:
            raise StagingSupervisorError(
                "worker role key generations could not be destroyed"
            ) from failures[0]


def _validate_key_lifecycle_binding(
    isolation: StagingIsolationContract,
    lifecycle: WorkerKeyLifecycle,
) -> None:
    """Bind all key-copy directories and owners to their process roles."""

    components = {component.name: component for component in isolation.components}
    contracts = getattr(lifecycle, "contracts", None)
    if not isinstance(contracts, dict) or set(contracts) != {
        "synthetic",
        "research",
    }:
        raise StagingSupervisorError("worker key lifecycle binding differs")
    gateway = components["gateway"]
    for role in ("synthetic", "research"):
        contract = contracts[role]
        worker = components[role]
        if (
            not isinstance(contract, WorkerKeyCopyContract)
            or contract.role != role
            or (contract.gateway_uid, contract.gateway_gid)
            != (gateway.uid, gateway.gid)
            or (contract.worker_uid, contract.worker_gid)
            != (worker.uid, worker.gid)
            or (contract.gateway_parent_uid, contract.gateway_parent_gid)
            != (components["supervisor"].uid, gateway.gid)
            or (contract.worker_parent_uid, contract.worker_parent_gid)
            != (components["supervisor"].uid, worker.gid)
            or contract.gateway_directory.parent != isolation.shared_key_root
            or contract.worker_directory.parent != isolation.shared_key_root
        ):
            raise StagingSupervisorError("worker key lifecycle binding differs")


def _validate_private_directory(path: Path, *, uid: int, gid: int) -> None:
    _require_no_symlink_ancestors(path)
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise StagingSupervisorError("private key directory is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o710
        or info.st_uid != uid
        or info.st_gid != gid
    ):
        raise StagingSupervisorError("private key directory contract differs")


def _require_no_symlink_ancestors(path: Path) -> None:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise StagingSupervisorError("private path is not canonical")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise StagingSupervisorError("private path ancestor is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise StagingSupervisorError("private path ancestor is a symlink")


def _unlink_private_key(
    path: Path,
    *,
    uid: int,
    gid: int,
    parent_uid: int,
    parent_gid: int,
) -> None:
    _validate_private_directory(path.parent, uid=parent_uid, gid=parent_gid)
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != uid
        or info.st_gid != gid
        or info.st_size != 32
    ):
        raise StagingSupervisorError("private key cleanup contract differs")
    path.unlink()


def _unlink_incomplete_private_key(
    path: Path,
    *,
    uid: int,
    gid: int,
    parent_uid: int,
    parent_gid: int,
    identity: tuple[int, int],
) -> None:
    """Remove only a lifecycle-owned key copy that failed before completion."""

    _validate_private_directory(path.parent, uid=parent_uid, gid=parent_gid)
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if (info.st_dev, info.st_ino) != identity:
        # A pathname race or replacement is not lifecycle-owned.  Preserve it.
        return
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & ~0o600
        or info.st_uid not in {parent_uid, uid}
        or info.st_gid not in {parent_gid, gid, os.getegid()}
        or info.st_size > 32
    ):
        raise StagingSupervisorError("incomplete private key cleanup contract differs")
    path.unlink()


def _proc_identity(pid: int) -> tuple[int, int, int]:
    _, start_ticks, process_group, session_id = _proc_record(pid)
    return start_ticks, process_group, session_id


def _proc_record(pid: int) -> tuple[str, int, int, int]:
    if type(pid) is not int or pid < 1:
        raise StagingSupervisorError("process ID is invalid")
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        raise ProcessLookupError(pid) from None
    except (OSError, UnicodeError) as exc:
        raise StagingSupervisorError("process identity is unavailable") from exc
    close = raw.rfind(")")
    if close < 1:
        raise StagingSupervisorError("process identity record is malformed")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        raise StagingSupervisorError("process identity record is malformed")
    try:
        state = fields[0]
        if len(state) != 1 or state not in "RSDZTWtXxKPI":
            raise ValueError
        return state, int(fields[19]), int(fields[2]), int(fields[3])
    except ValueError:
        raise StagingSupervisorError("process identity record is malformed") from None


def _owned_process_group_members(identity: ProcessIdentity) -> tuple[int, ...]:
    """List only members of the exact supervisor-created session/process group."""

    identity.validate_owned_session()
    members: list[int] = []
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise StagingSupervisorError("process inventory is unavailable") from exc
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        try:
            state, _, process_group, session_id = _proc_record(pid)
        except ProcessLookupError:
            continue
        except StagingSupervisorError:
            raise
        if process_group == identity.process_group and session_id == identity.session_id:
            members.append(pid)
    return tuple(sorted(members))


def _reap_owned_descendants(
    process: subprocess.Popen[bytes], identity: ProcessIdentity
) -> None:
    """Reap exact adopted group descendants without stealing Popen's leader."""

    process.poll()
    for pid in _owned_process_group_members(identity):
        if pid == process.pid:
            continue
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            # A live leader may still parent the process.  It will be adopted
            # after the leader is reaped and revisited in the next drain.
            continue
        except ProcessLookupError:
            continue


def _milliseconds(value: float) -> int:
    if not isinstance(value, (int, float)) or value < 0:
        raise StagingSupervisorError("control time is invalid")
    result = int(value * 1_000)
    if result / 1_000 != float(value):
        raise StagingSupervisorError("control time precision is invalid")
    return result


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
