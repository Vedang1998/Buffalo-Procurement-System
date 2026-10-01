"""Boot-scoped authenticated activation channel for staging worker keys."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import hmac
import json
import math
import secrets
import socket
import struct
import threading
import time
from typing import Callable

from .staging_worker_types import WorkerKeyState, WorkerKeyring


ACTIVATION_VERSION = "BUFFALO_STAGING_WORKER_ACTIVATION_V1"
ACTIVATION_OPERATIONS = frozenset({"PREPARE", "COMMIT", "DISABLE"})
ACTIVATION_TTL_MILLISECONDS = 5_000
MAX_ACTIVATION_FRAME_BYTES = 2_048
_REQUEST_DOMAIN = b"BUFFALO_STAGING_ACTIVATION_REQUEST_V1\x00"
_RESPONSE_DOMAIN = b"BUFFALO_STAGING_ACTIVATION_RESPONSE_V1\x00"
_KEY_PROOF_DOMAIN = b"BUFFALO_STAGING_ACTIVATION_KEY_PROOF_V1\x00"
_ROLES = frozenset({"synthetic", "research"})
_MAX_GENERATION = 2**63 - 1


class WorkerActivationError(ValueError):
    """The fixed activation channel or one of its messages differs."""


class _ActivationChannelClosed(Exception):
    pass


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _reject_constant(_value: str) -> None:
    raise WorkerActivationError("activation JSON value is invalid")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise WorkerActivationError("activation JSON member is duplicated")
        result[name] = value
    return result


def _parse_canonical(message: bytes) -> dict[str, object]:
    if (
        not isinstance(message, bytes)
        or not message
        or len(message) > MAX_ACTIVATION_FRAME_BYTES
    ):
        raise WorkerActivationError("activation message size is invalid")
    try:
        value = json.loads(
            message,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WorkerActivationError("activation message is invalid") from None
    if not isinstance(value, dict) or message != _canonical_json(value):
        raise WorkerActivationError("activation message is not canonical")
    return value


def _valid_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _clock_milliseconds(clock: Callable[[], float]) -> int:
    try:
        value = float(clock())
    except (TypeError, ValueError, OverflowError) as exc:
        raise WorkerActivationError("activation clock is invalid") from exc
    if not math.isfinite(value) or value < 0:
        raise WorkerActivationError("activation clock is invalid")
    return int(value * 1_000)


def _signed_payload(
    *, key: bytes, domain: bytes, unsigned: dict[str, object]
) -> bytes:
    payload = dict(unsigned)
    payload["signature"] = hmac.digest(
        key,
        domain + _canonical_json(unsigned),
        "sha256",
    ).hex()
    return _canonical_json(payload)


def mint_activation_request(
    *,
    key: bytes,
    operation: str,
    role: str,
    generation: int,
    issued_ms: int,
    nonce: str | None = None,
) -> bytes:
    """Create one canonical, boot-keyed supervisor request."""

    if not isinstance(key, bytes) or len(key) != 32:
        raise WorkerActivationError("activation key is invalid")
    selected_nonce = secrets.token_hex(32) if nonce is None else nonce
    if (
        operation not in ACTIVATION_OPERATIONS
        or role not in _ROLES
        or type(generation) is not int
        or not 0 <= generation <= _MAX_GENERATION
        or type(issued_ms) is not int
        or issued_ms < 0
        or not _valid_hex(selected_nonce)
    ):
        raise WorkerActivationError("activation request values differ")
    unsigned: dict[str, object] = {
        "expires_ms": issued_ms + ACTIVATION_TTL_MILLISECONDS,
        "generation": generation,
        "issued_ms": issued_ms,
        "nonce": selected_nonce,
        "operation": operation,
        "role": role,
        "version": ACTIVATION_VERSION,
    }
    return _signed_payload(key=key, domain=_REQUEST_DOMAIN, unsigned=unsigned)


def _key_proof(
    *, key: bytes, nonce: str, role: str, generation: int
) -> str:
    binding = _canonical_json(
        {
            "generation": generation,
            "nonce": nonce,
            "operation": "PREPARE",
            "role": role,
            "version": ACTIVATION_VERSION,
        }
    )
    return hmac.digest(key, _KEY_PROOF_DOMAIN + binding, "sha256").hex()


class GatewayActivationProtocol:
    """Verify requests and mutate one gateway keyring on its event loop."""

    def __init__(
        self,
        *,
        key: bytes,
        keyring: WorkerKeyring,
        load_key: Callable[[str, int], bytes],
        wall_clock: Callable[[], float] = time.time,
        replay_entries: int = 1_024,
    ) -> None:
        if (
            not isinstance(key, bytes)
            or len(key) != 32
            or not isinstance(keyring, WorkerKeyring)
            or not callable(load_key)
            or not callable(wall_clock)
            or type(replay_entries) is not int
            or not 1 <= replay_entries <= 4_096
        ):
            raise WorkerActivationError("activation protocol configuration is invalid")
        self._key = bytes(key)
        self.keyring = keyring
        self.load_key = load_key
        self.wall_clock = wall_clock
        self.replay_entries = replay_entries
        self._responses: OrderedDict[str, tuple[int, bytes, bytes]] = OrderedDict()

    def dispatch(self, message: bytes) -> bytes:
        envelope = _parse_canonical(message)
        if set(envelope) != {
            "expires_ms",
            "generation",
            "issued_ms",
            "nonce",
            "operation",
            "role",
            "signature",
            "version",
        }:
            raise WorkerActivationError("activation request contract differs")
        signature = envelope.pop("signature")
        if not _valid_hex(signature):
            raise WorkerActivationError("activation request signature is invalid")
        if not hmac.compare_digest(
            signature,
            hmac.digest(
                self._key,
                _REQUEST_DOMAIN + _canonical_json(envelope),
                "sha256",
            ).hex(),
        ):
            raise WorkerActivationError("activation request signature differs")

        version = envelope.get("version")
        operation = envelope.get("operation")
        role = envelope.get("role")
        generation = envelope.get("generation")
        nonce = envelope.get("nonce")
        issued_ms = envelope.get("issued_ms")
        expires_ms = envelope.get("expires_ms")
        if (
            version != ACTIVATION_VERSION
            or operation not in ACTIVATION_OPERATIONS
            or role not in _ROLES
            or type(generation) is not int
            or not 0 <= generation <= _MAX_GENERATION
            or not _valid_hex(nonce)
            or type(issued_ms) is not int
            or type(expires_ms) is not int
            or expires_ms - issued_ms != ACTIVATION_TTL_MILLISECONDS
        ):
            raise WorkerActivationError("activation request values differ")
        now_ms = _clock_milliseconds(self.wall_clock)
        if issued_ms > now_ms + 1_000 or now_ms > expires_ms:
            raise WorkerActivationError("activation request time is invalid")

        digest = hashlib.sha256(message).digest()
        self._discard_expired(now_ms)
        cached = self._responses.get(nonce)
        if cached is not None:
            _, accepted_digest, response = cached
            if not hmac.compare_digest(accepted_digest, digest):
                raise WorkerActivationError("activation nonce was rebound")
            self._responses.move_to_end(nonce)
            return response
        if len(self._responses) >= self.replay_entries:
            raise WorkerActivationError("activation replay cache is full")

        proof: str | None = None
        try:
            if operation == "PREPARE":
                worker_key = self.load_key(role, generation)
                if not isinstance(worker_key, bytes) or len(worker_key) != 32:
                    raise WorkerActivationError("prepared worker key is invalid")
                self.keyring.prepare(
                    role=role,
                    key=bytes(worker_key),
                    generation=generation,
                )
                proof = _key_proof(
                    key=worker_key,
                    nonce=nonce,
                    role=role,
                    generation=generation,
                )
                expected_state = WorkerKeyState.PENDING
            elif operation == "COMMIT":
                self.keyring.commit(role=role, generation=generation)
                expected_state = WorkerKeyState.ACTIVE
            else:
                self.keyring.disable(role=role, generation=generation)
                expected_state = WorkerKeyState.DISABLED
        except (KeyError, TypeError, ValueError, OSError):
            raise WorkerActivationError("activation key transition refused") from None
        status = self.keyring.status(role)
        if status.generation != generation or status.state is not expected_state:
            raise WorkerActivationError("activation key state differs")
        unsigned_response: dict[str, object] = {
            "generation": generation,
            "key_proof": proof,
            "nonce": nonce,
            "operation": operation,
            "role": role,
            "state": expected_state.value,
            "version": ACTIVATION_VERSION,
        }
        response = _signed_payload(
            key=self._key,
            domain=_RESPONSE_DOMAIN,
            unsigned=unsigned_response,
        )
        self._responses[nonce] = (expires_ms, digest, response)
        return response

    def _discard_expired(self, now_ms: int) -> None:
        expired = [
            nonce
            for nonce, (expires_ms, _, _) in self._responses.items()
            if expires_ms < now_ms
        ]
        for nonce in expired:
            self._responses.pop(nonce, None)


def parse_activation_response(
    *,
    message: bytes,
    key: bytes,
    operation: str,
    role: str,
    generation: int,
    nonce: str,
    worker_key: bytes | None = None,
) -> WorkerKeyState:
    """Verify one gateway acknowledgement and its optional key proof."""

    if not isinstance(key, bytes) or len(key) != 32:
        raise WorkerActivationError("activation key is invalid")
    if (
        operation not in ACTIVATION_OPERATIONS
        or role not in _ROLES
        or type(generation) is not int
        or not 0 <= generation <= _MAX_GENERATION
        or not _valid_hex(nonce)
    ):
        raise WorkerActivationError("activation response expectation is invalid")
    envelope = _parse_canonical(message)
    if set(envelope) != {
        "generation",
        "key_proof",
        "nonce",
        "operation",
        "role",
        "signature",
        "state",
        "version",
    }:
        raise WorkerActivationError("activation response contract differs")
    signature = envelope.pop("signature")
    if not _valid_hex(signature) or not hmac.compare_digest(
        signature,
        hmac.digest(
            key,
            _RESPONSE_DOMAIN + _canonical_json(envelope),
            "sha256",
        ).hex(),
    ):
        raise WorkerActivationError("activation response signature differs")
    expected_state = {
        "PREPARE": WorkerKeyState.PENDING,
        "COMMIT": WorkerKeyState.ACTIVE,
        "DISABLE": WorkerKeyState.DISABLED,
    }.get(operation)
    proof = envelope.get("key_proof")
    if (
        expected_state is None
        or envelope.get("version") != ACTIVATION_VERSION
        or envelope.get("operation") != operation
        or envelope.get("role") != role
        or envelope.get("generation") != generation
        or envelope.get("nonce") != nonce
        or envelope.get("state") != expected_state.value
    ):
        raise WorkerActivationError("activation response values differ")
    if operation == "PREPARE":
        if not isinstance(worker_key, bytes) or len(worker_key) != 32:
            raise WorkerActivationError("activation worker key is invalid")
        expected_proof = _key_proof(
            key=worker_key,
            nonce=nonce,
            role=role,
            generation=generation,
        )
        if not _valid_hex(proof) or not hmac.compare_digest(proof, expected_proof):
            raise WorkerActivationError("activation worker key proof differs")
    elif proof is not None or worker_key is not None:
        raise WorkerActivationError("activation worker key proof is unexpected")
    return expected_state


def _validate_connected_socket(value: socket.socket) -> None:
    try:
        listening = value.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        value.getpeername()
    except (AttributeError, OSError) as exc:
        raise WorkerActivationError("activation socket is invalid") from exc
    if (
        value.family != socket.AF_UNIX
        or (value.type & 0xF) != socket.SOCK_STREAM
        or listening != 0
        or value.fileno() < 0
    ):
        raise WorkerActivationError("activation socket contract differs")


class SupervisorActivationClient:
    """Synchronous supervisor endpoint for one inherited activation channel."""

    def __init__(
        self,
        *,
        channel: socket.socket,
        key: bytes,
        wall_clock: Callable[[], float] = time.time,
        timeout_seconds: float = 2.0,
    ) -> None:
        _validate_connected_socket(channel)
        if (
            not isinstance(key, bytes)
            or len(key) != 32
            or not callable(wall_clock)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < float(timeout_seconds) <= 5
        ):
            raise WorkerActivationError("activation client configuration is invalid")
        self._channel = channel
        self._channel.settimeout(float(timeout_seconds))
        self._key = bytes(key)
        self.wall_clock = wall_clock
        self._lock = threading.Lock()
        self._closed = False

    def prepare(self, *, role: str, generation: int, worker_key: bytes) -> None:
        self._exchange(
            operation="PREPARE",
            role=role,
            generation=generation,
            worker_key=worker_key,
        )

    def commit(self, *, role: str, generation: int) -> None:
        self._exchange(operation="COMMIT", role=role, generation=generation)

    def disable(self, *, role: str, generation: int) -> None:
        self._exchange(operation="DISABLE", role=role, generation=generation)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._poison_locked()

    def _exchange(
        self,
        *,
        operation: str,
        role: str,
        generation: int,
        worker_key: bytes | None = None,
    ) -> None:
        nonce = secrets.token_hex(32)
        request = mint_activation_request(
            key=self._key,
            operation=operation,
            role=role,
            generation=generation,
            issued_ms=_clock_milliseconds(self.wall_clock),
            nonce=nonce,
        )
        with self._lock:
            if self._closed:
                raise WorkerActivationError("activation channel is closed")
            try:
                self._channel.sendall(struct.pack("!I", len(request)) + request)
                length = struct.unpack("!I", self._receive_exact(4))[0]
                if length < 1 or length > MAX_ACTIVATION_FRAME_BYTES:
                    raise WorkerActivationError("activation response length is invalid")
                response = self._receive_exact(length)
                parse_activation_response(
                    message=response,
                    key=self._key,
                    operation=operation,
                    role=role,
                    generation=generation,
                    nonce=nonce,
                    worker_key=worker_key,
                )
            except (OSError, socket.timeout, struct.error, WorkerActivationError):
                self._poison_locked()
                raise WorkerActivationError("activation exchange failed") from None

    def _receive_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self._channel.recv(length - len(chunks))
            if not chunk:
                raise WorkerActivationError("activation channel closed early")
            chunks.extend(chunk)
        return bytes(chunks)

    def _poison_locked(self) -> None:
        self._closed = True
        try:
            self._channel.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._channel.close()


class GatewayActivationServer:
    """Gateway event-loop endpoint for one inherited connected socket."""

    def __init__(
        self,
        *,
        channel: socket.socket,
        protocol: GatewayActivationProtocol,
        fatal_handler: Callable[[], None],
        timeout_seconds: float = 2.0,
    ) -> None:
        _validate_connected_socket(channel)
        if (
            not isinstance(protocol, GatewayActivationProtocol)
            or not callable(fatal_handler)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < float(timeout_seconds) <= 5
        ):
            raise WorkerActivationError("activation server configuration is invalid")
        self._channel = channel
        self._channel.setblocking(False)
        self.protocol = protocol
        self.fatal_handler = fatal_handler
        self.timeout_seconds = float(timeout_seconds)
        self._closed = False
        self._serve_task: asyncio.Task[None] | None = None

    async def serve(self) -> None:
        current = asyncio.current_task()
        if current is None or self._serve_task is not None or self._closed:
            raise WorkerActivationError("activation server transition is invalid")
        self._serve_task = current
        loop = asyncio.get_running_loop()
        try:
            while not self._closed:
                first = await loop.sock_recv(self._channel, 1)
                if not first:
                    raise WorkerActivationError("activation channel closed early")
                prefix = first + await self._receive_exact(loop, 3)
                length = struct.unpack("!I", prefix)[0]
                if length < 1 or length > MAX_ACTIVATION_FRAME_BYTES:
                    raise WorkerActivationError("activation request length is invalid")
                request = await self._receive_exact(loop, length)
                response = self.protocol.dispatch(request)
                await asyncio.wait_for(
                    loop.sock_sendall(
                        self._channel,
                        struct.pack("!I", len(response)) + response,
                    ),
                    timeout=self.timeout_seconds,
                )
        except asyncio.CancelledError:
            if not self._closed:
                self._poison_and_fail()
                raise
        except _ActivationChannelClosed:
            if not self._closed:
                raise WorkerActivationError("activation server failed") from None
        except (asyncio.TimeoutError, OSError, struct.error, WorkerActivationError) as exc:
            if not self._closed:
                self._poison_and_fail()
                raise WorkerActivationError("activation server failed") from None
        finally:
            self._serve_task = None

    async def close(self) -> None:
        if not self._closed:
            task = self._serve_task
            self._poison()
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            else:
                await asyncio.sleep(0)

    def _poison(self) -> None:
        self._closed = True
        try:
            self._channel.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._channel.close()

    def _poison_and_fail(self) -> None:
        self._poison()
        try:
            self.fatal_handler()
        except BaseException:
            pass

    async def _receive_exact(
        self, loop: asyncio.AbstractEventLoop, length: int
    ) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = await asyncio.wait_for(
                loop.sock_recv(self._channel, length - len(chunks)),
                timeout=self.timeout_seconds,
            )
            if not chunk:
                if self._closed:
                    raise _ActivationChannelClosed
                raise WorkerActivationError("activation channel closed early")
            chunks.extend(chunk)
        return bytes(chunks)
