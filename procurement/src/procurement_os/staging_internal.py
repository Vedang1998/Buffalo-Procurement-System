"""Canonical short-lived assertions for Railway staging worker requests."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import hmac
import json
import re
from threading import RLock
from typing import Iterable
from urllib.parse import urlsplit


ASSERTION_VERSION = "BUFFALO_STAGING_WORKER_ASSERTION_V1"
ASSERTION_TTL_SECONDS = 5
_CLOCK_SKEW_SECONDS = 1
_MAX_TOKEN_BYTES = 8_192
_MAX_QUERY_BYTES = 8_192
_WORKER_ROLES = frozenset({"synthetic", "research"})
_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"})
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.-]{2,127}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class AssertionError(ValueError):
    """An internal worker assertion is invalid or cannot be accepted."""


@dataclass(frozen=True)
class VerifiedAssertion:
    worker_role: str
    request_id: str
    session_digest: str
    principal_ref: str
    role_ref: str
    capabilities: frozenset[str]
    method: str
    path: str
    origin: str
    nonce: str
    issued_at: float
    expires_at: float


class NonceReplayCache:
    """A bounded nonce set whose entries live no longer than an assertion."""

    def __init__(self, *, maximum_entries: int = 4_096) -> None:
        if maximum_entries < 1:
            raise AssertionError("nonce cache capacity is invalid")
        self._maximum_entries = maximum_entries
        self._entries: dict[str, float] = {}
        self._lock = RLock()

    def accept(self, nonce: str, *, expires_at: float, now: float) -> None:
        with self._lock:
            expired = [
                value
                for value, expiry in self._entries.items()
                if expiry < now
            ]
            for value in expired:
                self._entries.pop(value, None)
            if nonce in self._entries:
                raise AssertionError("internal assertion nonce was replayed")
            if len(self._entries) >= self._maximum_entries:
                raise AssertionError("internal assertion nonce cache capacity reached")
            self._entries[nonce] = expires_at


def mint_assertion(
    *,
    key: bytes,
    worker_role: str,
    request_id: str,
    session_digest: str,
    principal_ref: str,
    role_ref: str,
    capabilities: Iterable[str],
    method: str,
    path: str,
    query: bytes,
    body: bytes,
    origin: str,
    issued_at: float,
    nonce: str,
) -> str:
    _validate_key(key)
    _validate_common(
        worker_role=worker_role,
        request_id=request_id,
        session_digest=session_digest,
        principal_ref=principal_ref,
        role_ref=role_ref,
        capabilities=capabilities,
        method=method,
        path=path,
        query=query,
        origin=origin,
        nonce=nonce,
    )
    issued_ms = _seconds_to_milliseconds(issued_at)
    expires_ms = issued_ms + ASSERTION_TTL_SECONDS * 1_000
    payload = {
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "capabilities": sorted(frozenset(capabilities)),
        "expires_ms": expires_ms,
        "issued_ms": issued_ms,
        "method": method,
        "nonce": nonce,
        "origin": origin,
        "path": path,
        "principal_ref": principal_ref,
        "query_sha256": hashlib.sha256(query).hexdigest(),
        "request_id": request_id,
        "role_ref": role_ref,
        "session_digest": session_digest,
        "version": ASSERTION_VERSION,
        "worker_role": worker_role,
    }
    encoded = _canonical_json(payload)
    signature = hmac.digest(key, encoded, "sha256")
    return f"{_encode(encoded)}.{_encode(signature)}"


def verify_assertion(
    *,
    token: str,
    key: bytes,
    expected_worker_role: str,
    method: str,
    path: str,
    query: bytes,
    body: bytes,
    expected_origin: str,
    now: float,
    nonce_cache: NonceReplayCache,
) -> VerifiedAssertion:
    _validate_key(key)
    if not isinstance(token, str) or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
        raise AssertionError("internal assertion shape is invalid")
    parts = token.split(".")
    if len(parts) != 2:
        raise AssertionError("internal assertion shape is invalid")
    encoded, signature = (_decode(parts[0]), _decode(parts[1]))
    if len(signature) != hashlib.sha256().digest_size or not hmac.compare_digest(
        signature, hmac.digest(key, encoded, "sha256")
    ):
        raise AssertionError("internal assertion signature differs")
    payload = _load_payload(encoded)
    required = {
        "body_sha256",
        "capabilities",
        "expires_ms",
        "issued_ms",
        "method",
        "nonce",
        "origin",
        "path",
        "principal_ref",
        "query_sha256",
        "request_id",
        "role_ref",
        "session_digest",
        "version",
        "worker_role",
    }
    if set(payload) != required or payload.get("version") != ASSERTION_VERSION:
        raise AssertionError("internal assertion contract differs")
    capabilities_value = payload.get("capabilities")
    if (
        not isinstance(capabilities_value, list)
        or capabilities_value != sorted(set(capabilities_value))
        or not all(isinstance(value, str) for value in capabilities_value)
    ):
        raise AssertionError("internal assertion capabilities are invalid")
    capabilities = frozenset(capabilities_value)
    worker_role = _string(payload, "worker_role")
    request_id = _string(payload, "request_id")
    session_digest = _string(payload, "session_digest")
    principal_ref = _string(payload, "principal_ref")
    role_ref = _string(payload, "role_ref")
    asserted_method = _string(payload, "method")
    asserted_path = _string(payload, "path")
    asserted_origin = _string(payload, "origin")
    nonce = _string(payload, "nonce")
    _validate_common(
        worker_role=worker_role,
        request_id=request_id,
        session_digest=session_digest,
        principal_ref=principal_ref,
        role_ref=role_ref,
        capabilities=capabilities,
        method=asserted_method,
        path=asserted_path,
        query=query,
        origin=asserted_origin,
        nonce=nonce,
    )
    if not isinstance(payload.get("issued_ms"), int) or not isinstance(
        payload.get("expires_ms"), int
    ):
        raise AssertionError("internal assertion time is invalid")
    issued_at = payload["issued_ms"] / 1_000
    expires_at = payload["expires_ms"] / 1_000
    if expires_at - issued_at != ASSERTION_TTL_SECONDS:
        raise AssertionError("internal assertion lifetime differs")
    if issued_at > now + _CLOCK_SKEW_SECONDS:
        raise AssertionError("internal assertion was issued in the future")
    if now > expires_at:
        raise AssertionError("internal assertion expired")
    comparisons = (
        (worker_role, expected_worker_role, "worker"),
        (asserted_method, method, "method"),
        (asserted_path, path, "path"),
        (asserted_origin, expected_origin, "origin"),
        (
            _string(payload, "query_sha256"),
            hashlib.sha256(query).hexdigest(),
            "query",
        ),
        (
            _string(payload, "body_sha256"),
            hashlib.sha256(body).hexdigest(),
            "body",
        ),
    )
    for actual, expected, label in comparisons:
        if not hmac.compare_digest(actual, expected):
            raise AssertionError(f"internal assertion {label} binding differs")
    nonce_cache.accept(nonce, expires_at=expires_at, now=now)
    return VerifiedAssertion(
        worker_role=worker_role,
        request_id=request_id,
        session_digest=session_digest,
        principal_ref=principal_ref,
        role_ref=role_ref,
        capabilities=capabilities,
        method=asserted_method,
        path=asserted_path,
        origin=asserted_origin,
        nonce=nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _validate_common(
    *,
    worker_role: str,
    request_id: str,
    session_digest: str,
    principal_ref: str,
    role_ref: str,
    capabilities: Iterable[str],
    method: str,
    path: str,
    query: bytes,
    origin: str,
    nonce: str,
) -> None:
    if worker_role not in _WORKER_ROLES:
        raise AssertionError("internal assertion worker is invalid")
    if not _REQUEST_ID.fullmatch(request_id):
        raise AssertionError("internal assertion request ID is invalid")
    if not _HEX_64.fullmatch(session_digest):
        raise AssertionError("internal assertion session digest is invalid")
    if not _IDENTITY.fullmatch(principal_ref) or not _IDENTITY.fullmatch(role_ref):
        raise AssertionError("internal assertion identity is invalid")
    capability_values = frozenset(capabilities)
    if not capability_values or any(
        not isinstance(value, str) or not _CAPABILITY.fullmatch(value)
        for value in capability_values
    ):
        raise AssertionError("internal assertion capabilities are invalid")
    if method not in _METHODS:
        raise AssertionError("internal assertion method is invalid")
    _validate_path(path)
    if not isinstance(query, bytes) or len(query) > _MAX_QUERY_BYTES:
        raise AssertionError("internal assertion query is invalid")
    _validate_origin(origin)
    if not _HEX_64.fullmatch(nonce):
        raise AssertionError("internal assertion nonce is invalid")


def _validate_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or "%" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise AssertionError("internal assertion path is invalid")


def _validate_origin(origin: str) -> None:
    try:
        parsed = urlsplit(origin)
    except ValueError as exc:
        raise AssertionError("internal assertion origin is invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or origin.endswith("/")
    ):
        raise AssertionError("internal assertion origin is invalid")


def _validate_key(key: bytes) -> None:
    if not isinstance(key, bytes) or len(key) != 32:
        raise AssertionError("internal assertion key is invalid")


def _seconds_to_milliseconds(value: float) -> int:
    if not isinstance(value, (int, float)) or value < 0:
        raise AssertionError("internal assertion time is invalid")
    milliseconds = int(value * 1_000)
    if milliseconds / 1_000 != float(value):
        raise AssertionError("internal assertion time precision is invalid")
    return milliseconds


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _load_payload(value: bytes) -> dict[str, object]:
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise AssertionError("internal assertion contains duplicate fields")
            result[key] = item
        return result

    try:
        loaded = json.loads(value.decode("ascii"), object_pairs_hook=object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, AssertionError) as exc:
        if isinstance(exc, AssertionError):
            raise
        raise AssertionError("internal assertion payload is invalid") from exc
    if not isinstance(loaded, dict) or _canonical_json(loaded) != value:
        raise AssertionError("internal assertion payload is not canonical")
    return loaded


def _string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise AssertionError(f"internal assertion {key} is invalid")
    return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise AssertionError("internal assertion encoding is invalid")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AssertionError("internal assertion encoding is invalid") from exc
    if _encode(decoded) != value:
        raise AssertionError("internal assertion encoding is not canonical")
    return decoded
