"""One-shot authenticated readiness proof for the staging research worker."""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hmac
import json
import os
import stat
import struct


READINESS_VERSION = "BUFFALO_STAGING_RESEARCH_READINESS_V1"
MAX_READINESS_PAYLOAD_BYTES = 2_048
_READINESS_DOMAIN = b"BUFFALO_STAGING_RESEARCH_READINESS_V1\x00"
_MAX_GENERATION = 2**63 - 1
_FAILURES = frozenset(
    {"TIMEOUT", "INTEGRITY", "SEMANTIC", "RESOURCE", "OOM", "ACTIVATION"}
)


class ResearchReadinessError(ValueError):
    """The fixed readiness channel or its terminal proof differs."""


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _reject_constant(_value: str) -> None:
    raise ResearchReadinessError("readiness JSON value is invalid")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ResearchReadinessError("readiness JSON member is duplicated")
        result[name] = value
    return result


def _parse_canonical(message: bytes) -> dict[str, object]:
    if (
        not isinstance(message, bytes)
        or not message
        or len(message) > MAX_READINESS_PAYLOAD_BYTES
    ):
        raise ResearchReadinessError("readiness message size is invalid")
    try:
        value = json.loads(
            message,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ResearchReadinessError("readiness message is invalid") from None
    try:
        canonical = _canonical_json(value) if isinstance(value, dict) else None
    except (TypeError, ValueError, RecursionError):
        raise ResearchReadinessError("readiness message is invalid") from None
    if canonical is None or message != canonical:
        raise ResearchReadinessError("readiness message is not canonical")
    return value


def _valid_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ResearchReadinessError("readiness key is invalid")


@dataclass(frozen=True)
class ReadinessProcessIdentity:
    pid: int
    start_ticks: int
    process_group: int
    session_id: int

    def validate(self) -> None:
        if (
            any(
                type(value) is not int or value < 1
                for value in (
                    self.pid,
                    self.start_ticks,
                    self.process_group,
                    self.session_id,
                )
            )
            or self.process_group != self.pid
            or self.session_id != self.pid
        ):
            raise ResearchReadinessError("readiness process identity is invalid")

    def to_dict(self) -> dict[str, int]:
        self.validate()
        return {
            "pid": self.pid,
            "process_group": self.process_group,
            "session_id": self.session_id,
            "start_ticks": self.start_ticks,
        }


@dataclass(frozen=True)
class ResearchReadinessProof:
    generation: int
    identity: ReadinessProcessIdentity
    state: str
    failure: str | None
    validation_identity: str | None

    @property
    def ready(self) -> bool:
        return self.state == "READY"


def _validate_values(
    *,
    generation: int,
    identity: ReadinessProcessIdentity,
    state: str,
    failure: str | None,
    validation_identity: str | None,
) -> None:
    if (
        type(generation) is not int
        or not 0 <= generation <= _MAX_GENERATION
        or not isinstance(identity, ReadinessProcessIdentity)
        or type(state) is not str
        or state not in {"READY", "FAILED"}
    ):
        raise ResearchReadinessError("readiness values differ")
    identity.validate()
    if state == "READY":
        if failure is not None or not _valid_hex(validation_identity):
            raise ResearchReadinessError("ready proof values differ")
    elif (
        type(failure) is not str
        or failure not in _FAILURES
        or validation_identity is not None
    ):
        raise ResearchReadinessError("failed proof values differ")


def mint_readiness_payload(
    *,
    key: bytes,
    generation: int,
    identity: ReadinessProcessIdentity,
    state: str,
    failure: str | None = None,
    validation_identity: str | None = None,
) -> bytes:
    """Mint one canonical, generation- and process-bound terminal proof."""

    _validate_key(key)
    _validate_values(
        generation=generation,
        identity=identity,
        state=state,
        failure=failure,
        validation_identity=validation_identity,
    )
    unsigned: dict[str, object] = {
        "failure": failure,
        "generation": generation,
        "identity": identity.to_dict(),
        "role": "research",
        "state": state,
        "validation_identity": validation_identity,
        "version": READINESS_VERSION,
    }
    message = dict(unsigned)
    message["signature"] = hmac.digest(
        key, _READINESS_DOMAIN + _canonical_json(unsigned), "sha256"
    ).hex()
    encoded = _canonical_json(message)
    if len(encoded) > MAX_READINESS_PAYLOAD_BYTES:
        raise ResearchReadinessError("readiness message size is invalid")
    return encoded


def mint_readiness_frame(
    *,
    key: bytes,
    generation: int,
    identity: ReadinessProcessIdentity,
    state: str,
    failure: str | None = None,
    validation_identity: str | None = None,
) -> bytes:
    payload = mint_readiness_payload(
        key=key,
        generation=generation,
        identity=identity,
        state=state,
        failure=failure,
        validation_identity=validation_identity,
    )
    return struct.pack("!I", len(payload)) + payload


def parse_readiness_payload(
    message: bytes,
    *,
    key: bytes,
    expected_generation: int,
    expected_identity: ReadinessProcessIdentity,
    expected_validation_identity: str,
) -> ResearchReadinessProof:
    """Verify one terminal proof against the exact owned launch."""

    _validate_key(key)
    if (
        type(expected_generation) is not int
        or not 0 <= expected_generation <= _MAX_GENERATION
        or not isinstance(expected_identity, ReadinessProcessIdentity)
        or not _valid_hex(expected_validation_identity)
    ):
        raise ResearchReadinessError("readiness expectation differs")
    expected_identity.validate()
    envelope = _parse_canonical(message)
    if set(envelope) != {
        "failure",
        "generation",
        "identity",
        "role",
        "signature",
        "state",
        "validation_identity",
        "version",
    }:
        raise ResearchReadinessError("readiness message contract differs")
    signature = envelope.pop("signature")
    if not _valid_hex(signature) or not hmac.compare_digest(
        signature,
        hmac.digest(
            key,
            _READINESS_DOMAIN + _canonical_json(envelope),
            "sha256",
        ).hex(),
    ):
        raise ResearchReadinessError("readiness signature differs")
    raw_identity = envelope.get("identity")
    if not isinstance(raw_identity, dict) or set(raw_identity) != {
        "pid",
        "process_group",
        "session_id",
        "start_ticks",
    }:
        raise ResearchReadinessError("readiness identity contract differs")
    identity = ReadinessProcessIdentity(
        pid=raw_identity.get("pid"),  # type: ignore[arg-type]
        start_ticks=raw_identity.get("start_ticks"),  # type: ignore[arg-type]
        process_group=raw_identity.get("process_group"),  # type: ignore[arg-type]
        session_id=raw_identity.get("session_id"),  # type: ignore[arg-type]
    )
    generation = envelope.get("generation")
    state = envelope.get("state")
    failure = envelope.get("failure")
    validation_identity = envelope.get("validation_identity")
    _validate_values(
        generation=generation,  # type: ignore[arg-type]
        identity=identity,
        state=state,  # type: ignore[arg-type]
        failure=failure,  # type: ignore[arg-type]
        validation_identity=validation_identity,  # type: ignore[arg-type]
    )
    if (
        envelope.get("version") != READINESS_VERSION
        or envelope.get("role") != "research"
        or generation != expected_generation
        or identity != expected_identity
        or (state == "READY" and validation_identity != expected_validation_identity)
    ):
        raise ResearchReadinessError("readiness binding differs")
    return ResearchReadinessProof(
        generation=generation,  # type: ignore[arg-type]
        identity=identity,
        state=state,  # type: ignore[arg-type]
        failure=failure,  # type: ignore[arg-type]
        validation_identity=validation_identity,  # type: ignore[arg-type]
    )


class ResearchReadinessReader:
    """Own and nonblockingly drain one fresh anonymous-pipe read endpoint."""

    def __init__(
        self,
        descriptor: int,
        *,
        key: bytes,
        expected_generation: int,
        expected_identity: ReadinessProcessIdentity,
        expected_validation_identity: str,
    ) -> None:
        _validate_key(key)
        if type(descriptor) is not int or descriptor < 0:
            raise ResearchReadinessError("readiness descriptor is invalid")
        # Validate all public expectations before touching or assuming ownership
        # of the caller's descriptor.
        if (
            type(expected_generation) is not int
            or not 0 <= expected_generation <= _MAX_GENERATION
            or not isinstance(expected_identity, ReadinessProcessIdentity)
            or not _valid_hex(expected_validation_identity)
        ):
            raise ResearchReadinessError("readiness expectation differs")
        expected_identity.validate()
        try:
            info = os.fstat(descriptor)
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
            endpoint = os.readlink(f"/proc/self/fd/{descriptor}")
            if (
                not stat.S_ISFIFO(info.st_mode)
                or flags & os.O_ACCMODE != os.O_RDONLY
                or endpoint != f"pipe:[{info.st_ino}]"
            ):
                raise ResearchReadinessError("readiness descriptor differs")
            os.set_inheritable(descriptor, False)
            os.set_blocking(descriptor, False)
        except ResearchReadinessError:
            raise
        except OSError:
            raise ResearchReadinessError(
                "readiness descriptor is unavailable"
            ) from None
        self._descriptor: int | None = descriptor
        self._key = bytes(key)
        self._expected_generation = expected_generation
        self._expected_identity = expected_identity
        self._expected_validation_identity = expected_validation_identity
        self._buffer = bytearray()
        self._payload_bytes: int | None = None
        self._proof: ResearchReadinessProof | None = None
        self._poisoned = False

    @property
    def closed(self) -> bool:
        return self._descriptor is None

    def close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                raise ResearchReadinessError("readiness descriptor close failed") from exc

    def _fail(self, message: str) -> None:
        self._poisoned = True
        try:
            self.close()
        except ResearchReadinessError:
            pass
        raise ResearchReadinessError(message)

    def _check_prefix_and_bound(self) -> None:
        if len(self._buffer) < 4:
            return
        if self._payload_bytes is None:
            self._payload_bytes = struct.unpack("!I", self._buffer[:4])[0]
            if not 1 <= self._payload_bytes <= MAX_READINESS_PAYLOAD_BYTES:
                self._fail("readiness frame size differs")
        if len(self._buffer) > 4 + self._payload_bytes:
            self._fail("readiness frame contains trailing bytes")

    def poll(self) -> ResearchReadinessProof | None:
        if self._proof is not None:
            return self._proof
        if self._poisoned:
            raise ResearchReadinessError("readiness reader is poisoned")
        descriptor = self._descriptor
        if descriptor is None:
            raise ResearchReadinessError("readiness descriptor is closed")
        eof = False
        while True:
            try:
                chunk = os.read(descriptor, MAX_READINESS_PAYLOAD_BYTES + 5)
            except BlockingIOError:
                break
            except OSError:
                self._fail("readiness channel read failed")
            if not chunk:
                eof = True
                break
            self._buffer.extend(chunk)
            self._check_prefix_and_bound()
        if not eof:
            return None
        try:
            self.close()
        except ResearchReadinessError:
            self._poisoned = True
            raise
        self._check_prefix_and_bound()
        if (
            self._payload_bytes is None
            or len(self._buffer) != 4 + self._payload_bytes
        ):
            self._fail("readiness frame ended early")
        try:
            proof = parse_readiness_payload(
                bytes(self._buffer[4:]),
                key=self._key,
                expected_generation=self._expected_generation,
                expected_identity=self._expected_identity,
                expected_validation_identity=self._expected_validation_identity,
            )
        except ResearchReadinessError:
            self._poisoned = True
            raise
        self._proof = proof
        return proof


__all__ = [
    "MAX_READINESS_PAYLOAD_BYTES",
    "READINESS_VERSION",
    "ReadinessProcessIdentity",
    "ResearchReadinessError",
    "ResearchReadinessProof",
    "ResearchReadinessReader",
    "mint_readiness_frame",
    "mint_readiness_payload",
    "parse_readiness_payload",
]
