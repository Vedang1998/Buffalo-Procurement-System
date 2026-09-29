"""Fail-closed owner authentication primitives for Railway staging.

This module has no HTTP, database, research-data, or Railway API dependency.
The public gateway composes these bounded primitives into its request policy.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import base64
import binascii
import hashlib
import hmac
import re
import secrets
from threading import BoundedSemaphore, RLock
import time
from typing import Callable

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type


ARGON2_MEMORY_COST_KIB = 19_456
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1
ARGON2_SALT_LEN = 16
ARGON2_HASH_LEN = 32

_ARGON2ID_VERIFIER_RE = re.compile(
    rf"\A\$argon2id\$v=19\$"
    rf"m={ARGON2_MEMORY_COST_KIB},t={ARGON2_TIME_COST},p={ARGON2_PARALLELISM}\$"
    rf"(?P<salt>[A-Za-z0-9+/]{{22}})\$"
    rf"(?P<hash>[A-Za-z0-9+/]{{43}})\Z"
)

SESSION_COOKIE = "__Host-buffalo_staging_session"
LOGIN_CHALLENGE_COOKIE = "__Host-buffalo_staging_login"
CSRF_COOKIE = "__Host-buffalo_staging_csrf"
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60


class StagingAccessError(ValueError):
    """The staging authentication contract is invalid."""


class AuthenticationBusy(RuntimeError):
    """The sole password-verification slot is already occupied."""


class AuthenticationThrottled(RuntimeError):
    """The global bounded login cooldown is active."""


class OwnerPasswordReservation:
    """One already-admitted verification that cannot be queued again."""

    def __init__(self, authenticator: "OwnerPasswordAuthenticator") -> None:
        self._authenticator = authenticator
        self._used = False

    def verify(self, supplied: str) -> bool:
        if self._used:
            raise AuthenticationBusy("owner authentication reservation was consumed")
        self._used = True
        try:
            return self._authenticator._verify_admitted(supplied)
        finally:
            self._authenticator._slot.release()

    def discard(self) -> None:
        """Release an admission that failed pre-Argon request validation."""

        if self._used:
            raise AuthenticationBusy("owner authentication reservation was consumed")
        self._used = True
        self._authenticator._slot.release()


@dataclass(frozen=True)
class OwnerCredential:
    passphrase: str = field(repr=False)
    verifier: str = field(repr=False)


@dataclass(frozen=True)
class IssuedSession:
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)


@dataclass
class StagingSession:
    session_ref: str
    token_digest_hex: str
    principal_ref: str
    role_ref: str
    capabilities: frozenset[str]
    credential_fingerprint: str
    csrf_digest: bytes
    created_monotonic: float
    last_seen_monotonic: float


@dataclass(frozen=True)
class LoginChallenge:
    cookie_token: str = field(repr=False)
    form_token: str = field(repr=False)


def _password_hasher() -> PasswordHasher:
    return PasswordHasher(
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST_KIB,
        parallelism=ARGON2_PARALLELISM,
        hash_len=ARGON2_HASH_LEN,
        salt_len=ARGON2_SALT_LEN,
        type=Type.ID,
    )


def generate_owner_credential() -> OwnerCredential:
    """Create one high-entropy passphrase and its Argon2id verifier."""

    passphrase = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode(
        "ascii"
    )
    return OwnerCredential(
        passphrase=passphrase,
        verifier=_password_hasher().hash(passphrase),
    )


def verifier_fingerprint(verifier: str) -> str:
    if not isinstance(verifier, str) or not verifier:
        raise StagingAccessError("owner verifier is absent")
    return hashlib.sha256(verifier.encode("utf-8")).hexdigest()


def _decode_canonical_phc_base64(value: str, *, expected_length: int) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StagingAccessError(
            "owner verifier does not match exact Argon2id parameters"
        ) from exc
    if len(decoded) != expected_length:
        raise StagingAccessError(
            "owner verifier does not match exact Argon2id parameters"
        )
    canonical = base64.b64encode(decoded).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(canonical, value):
        raise StagingAccessError(
            "owner verifier does not match exact Argon2id parameters"
        )
    return decoded


def _validate_owner_verifier(verifier: str):
    """Reject malformed or resource-amplifying PHC strings before Argon2."""

    if not isinstance(verifier, str):
        raise StagingAccessError(
            "owner verifier does not match exact Argon2id parameters"
        )
    match = _ARGON2ID_VERIFIER_RE.fullmatch(verifier)
    if match is None:
        raise StagingAccessError(
            "owner verifier does not match exact Argon2id parameters"
        )
    _decode_canonical_phc_base64(
        match.group("salt"), expected_length=ARGON2_SALT_LEN
    )
    _decode_canonical_phc_base64(
        match.group("hash"), expected_length=ARGON2_HASH_LEN
    )
    try:
        parameters = extract_parameters(verifier)
    except (InvalidHashError, ValueError, TypeError) as exc:
        raise StagingAccessError(
            "owner verifier does not match exact Argon2id parameters"
        ) from exc
    expected = (
        Type.ID,
        19,
        ARGON2_SALT_LEN,
        ARGON2_HASH_LEN,
        ARGON2_TIME_COST,
        ARGON2_MEMORY_COST_KIB,
        ARGON2_PARALLELISM,
    )
    actual = (
        parameters.type,
        parameters.version,
        parameters.salt_len,
        parameters.hash_len,
        parameters.time_cost,
        parameters.memory_cost,
        parameters.parallelism,
    )
    if actual != expected:
        raise StagingAccessError(
            "owner verifier does not match exact Argon2id parameters"
        )
    return parameters


def cookie_settings(
    name: str, *, max_age: int, httponly: bool = True
) -> dict[str, object]:
    if not name.startswith("__Host-"):
        raise StagingAccessError("staging cookie must use the __Host- prefix")
    if max_age < 1:
        raise StagingAccessError("staging cookie lifetime is invalid")
    return {
        "key": name,
        "max_age": int(max_age),
        "secure": True,
        "httponly": httponly,
        "samesite": "strict",
        "path": "/",
    }


class OwnerPasswordAuthenticator:
    """One-slot Argon2id verification with process-global-style cooldown."""

    def __init__(
        self,
        verifier: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        failure_limit: int = 5,
        failure_window_seconds: float = 5 * 60,
        base_cooldown_seconds: float = 2,
        maximum_cooldown_seconds: float = 5 * 60,
    ) -> None:
        if failure_limit < 1:
            raise StagingAccessError("authentication failure limit is invalid")
        if (
            failure_window_seconds <= 0
            or base_cooldown_seconds <= 0
            or maximum_cooldown_seconds < base_cooldown_seconds
        ):
            raise StagingAccessError("authentication cooldown parameters are invalid")
        parameters = _validate_owner_verifier(verifier)

        self.verifier = verifier
        self.parameters = parameters
        self._clock = clock
        self._failure_limit = failure_limit
        self._failure_window_seconds = float(failure_window_seconds)
        self._base_cooldown_seconds = float(base_cooldown_seconds)
        self._maximum_cooldown_seconds = float(maximum_cooldown_seconds)
        self._failures: deque[float] = deque()
        self._cooldown_until = 0.0
        self._lock = RLock()
        self._slot = BoundedSemaphore(1)
        self._hasher = _password_hasher()

    @property
    def fingerprint(self) -> str:
        return verifier_fingerprint(self.verifier)

    def verify(self, supplied: str) -> bool:
        return self.reserve().verify(supplied)

    def reserve(self) -> OwnerPasswordReservation:
        """Admit one verification without submitting or queueing any work."""

        now = self._clock()
        with self._lock:
            self._prune_failures(now)
            if now < self._cooldown_until:
                raise AuthenticationThrottled("owner authentication is temporarily unavailable")
        if not self._slot.acquire(blocking=False):
            raise AuthenticationBusy("owner authentication verifier is busy")
        return OwnerPasswordReservation(self)

    def _verify_admitted(self, supplied: str) -> bool:
        if not isinstance(supplied, str):
            supplied = ""
        now = self._clock()
        with self._lock:
            self._prune_failures(now)
            if now < self._cooldown_until:
                raise AuthenticationThrottled("owner authentication is temporarily unavailable")
        matched = False
        try:
            matched = bool(self._hasher.verify(self.verifier, supplied))
        except VerifyMismatchError:
            matched = False
        except VerificationError:
            matched = False
        now = self._clock()
        with self._lock:
            self._prune_failures(now)
            if matched:
                self._failures.clear()
                self._cooldown_until = 0.0
                return True
            self._failures.append(now)
            if len(self._failures) >= self._failure_limit:
                excess = len(self._failures) - self._failure_limit
                cooldown = min(
                    self._maximum_cooldown_seconds,
                    self._base_cooldown_seconds * (2**excess),
                )
                self._cooldown_until = max(self._cooldown_until, now + cooldown)
            return False

    def _prune_failures(self, now: float) -> None:
        cutoff = now - self._failure_window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()


class StagingSessionStore:
    """In-memory server sessions indexed only by token digest."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = SESSION_IDLE_SECONDS,
        absolute_seconds: float = SESSION_ABSOLUTE_SECONDS,
    ) -> None:
        if idle_seconds <= 0 or absolute_seconds < idle_seconds:
            raise StagingAccessError("session lifetimes are invalid")
        self._clock = clock
        self._idle_seconds = float(idle_seconds)
        self._absolute_seconds = float(absolute_seconds)
        self._sessions: dict[bytes, StagingSession] = {}
        self._lock = RLock()

    def create(
        self,
        *,
        principal_ref: str,
        role_ref: str,
        capabilities: frozenset[str],
        credential_fingerprint: str,
    ) -> IssuedSession:
        if not principal_ref or not role_ref or not credential_fingerprint:
            raise StagingAccessError("session authority is incomplete")
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        token_digest = self._digest(token)
        now = self._clock()
        session = StagingSession(
            session_ref=secrets.token_hex(32),
            token_digest_hex=token_digest.hex(),
            principal_ref=principal_ref,
            role_ref=role_ref,
            capabilities=frozenset(capabilities),
            credential_fingerprint=credential_fingerprint,
            csrf_digest=self._digest(csrf_token),
            created_monotonic=now,
            last_seen_monotonic=now,
        )
        with self._lock:
            self._sessions[token_digest] = session
        return IssuedSession(token=token, csrf_token=csrf_token)

    def authenticate(
        self, token: str | None, *, credential_fingerprint: str
    ) -> StagingSession | None:
        if not token or not credential_fingerprint:
            return None
        digest = self._digest(token)
        now = self._clock()
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return None
            expired = (
                now - session.last_seen_monotonic > self._idle_seconds
                or now - session.created_monotonic > self._absolute_seconds
            )
            if expired or not hmac.compare_digest(
                session.credential_fingerprint, credential_fingerprint
            ):
                self._sessions.pop(digest, None)
                return None
            session.last_seen_monotonic = now
            return session

    def validate_csrf(
        self, session: StagingSession | None, supplied: str | None
    ) -> bool:
        if session is None or not supplied:
            return False
        return hmac.compare_digest(session.csrf_digest, self._digest(supplied))

    def destroy(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(self._digest(token), None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()


class LoginChallengeStore:
    """Signed single-use login challenges with bounded consumed-token state."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 5 * 60,
        maximum_challenges: int = 64,
    ) -> None:
        if ttl_seconds <= 0 or maximum_challenges < 1:
            raise StagingAccessError("login challenge bounds are invalid")
        self._clock = clock
        self._ttl_seconds = float(ttl_seconds)
        self._maximum_challenges = maximum_challenges
        self._signing_key = secrets.token_bytes(32)
        self._consumed: dict[str, float] = {}
        self._lock = RLock()

    def create(self) -> LoginChallenge:
        form_token = secrets.token_urlsafe(32)
        now = self._clock()
        issued_ms = int(now * 1_000)
        expires_ms = issued_ms + int(self._ttl_seconds * 1_000)
        nonce = secrets.token_urlsafe(32)
        form_digest = hashlib.sha256(form_token.encode("utf-8")).hexdigest()
        payload = (
            f"v1.{issued_ms}.{expires_ms}.{nonce}.{form_digest}"
        ).encode("ascii")
        signature = base64.urlsafe_b64encode(
            hmac.digest(self._signing_key, payload, "sha256")
        ).rstrip(b"=")
        cookie_token = payload.decode("ascii") + "." + signature.decode("ascii")
        return LoginChallenge(
            cookie_token=cookie_token,
            form_token=form_token,
        )

    def consume_after_admission(
        self, cookie_token: str | None, form_token: str | None
    ) -> bool:
        """Consume a valid pair only after the caller owns the Argon slot."""

        if not cookie_token or not form_token:
            return False
        now = self._clock()
        parsed = self._parse(cookie_token)
        if parsed is None:
            return False
        payload, issued_at, expires_at, nonce, expected_form_digest = parsed
        if issued_at > now or expires_at - issued_at != self._ttl_seconds or now > expires_at:
            return False
        supplied_form_digest = hashlib.sha256(form_token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected_form_digest, supplied_form_digest):
            return False
        with self._lock:
            self._prune(now)
            if nonce in self._consumed or len(self._consumed) >= self._maximum_challenges:
                return False
            self._consumed[nonce] = expires_at
        return True

    def clear(self) -> None:
        with self._lock:
            self._consumed.clear()
            self._signing_key = secrets.token_bytes(32)

    def _prune(self, now: float) -> None:
        expired = [
            nonce
            for nonce, expires_at in self._consumed.items()
            if now > expires_at
        ]
        for nonce in expired:
            self._consumed.pop(nonce, None)

    def _parse(
        self, cookie_token: str
    ) -> tuple[bytes, float, float, str, str] | None:
        if not isinstance(cookie_token, str) or len(cookie_token) > 256:
            return None
        parts = cookie_token.split(".")
        if len(parts) != 6 or parts[0] != "v1":
            return None
        _, issued_text, expires_text, nonce, form_digest, encoded_signature = parts
        if (
            not issued_text.isdecimal()
            or not expires_text.isdecimal()
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce) is None
            or re.fullmatch(r"[0-9a-f]{64}", form_digest) is None
            or re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded_signature) is None
        ):
            return None
        payload_text = ".".join(parts[:-1])
        payload = payload_text.encode("ascii")
        try:
            supplied_signature = base64.urlsafe_b64decode(encoded_signature + "=")
        except (ValueError, binascii.Error):
            return None
        expected_signature = hmac.digest(self._signing_key, payload, "sha256")
        if len(supplied_signature) != 32 or not hmac.compare_digest(
            supplied_signature, expected_signature
        ):
            return None
        issued_at = int(issued_text) / 1_000
        expires_at = int(expires_text) / 1_000
        return payload, issued_at, expires_at, nonce, form_digest
