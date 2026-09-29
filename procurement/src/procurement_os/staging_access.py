"""Fail-closed owner authentication primitives for Railway staging.

This module has no HTTP, database, research-data, or Railway API dependency.
The public gateway composes these bounded primitives into its request policy.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import base64
import hashlib
import hmac
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

SESSION_COOKIE = "__Host-buffalo_staging_session"
LOGIN_CHALLENGE_COOKIE = "__Host-buffalo_staging_login"
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60


class StagingAccessError(ValueError):
    """The staging authentication contract is invalid."""


class AuthenticationBusy(RuntimeError):
    """The sole password-verification slot is already occupied."""


class AuthenticationThrottled(RuntimeError):
    """The global bounded login cooldown is active."""


@dataclass(frozen=True)
class OwnerCredential:
    passphrase: str
    verifier: str


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str


@dataclass
class StagingSession:
    session_ref: str
    principal_ref: str
    role_ref: str
    capabilities: frozenset[str]
    credential_fingerprint: str
    csrf_digest: bytes
    created_monotonic: float
    last_seen_monotonic: float


@dataclass(frozen=True)
class LoginChallenge:
    cookie_token: str
    form_token: str


@dataclass(frozen=True)
class _StoredLoginChallenge:
    form_digest: bytes
    expires_monotonic: float


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


def cookie_settings(name: str, *, max_age: int) -> dict[str, object]:
    if not name.startswith("__Host-"):
        raise StagingAccessError("staging cookie must use the __Host- prefix")
    if max_age < 1:
        raise StagingAccessError("staging cookie lifetime is invalid")
    return {
        "key": name,
        "max_age": int(max_age),
        "secure": True,
        "httponly": True,
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
        try:
            parameters = extract_parameters(verifier)
        except (InvalidHashError, ValueError, TypeError) as exc:
            raise StagingAccessError("owner verifier is malformed") from exc
        if parameters.type is not Type.ID:
            raise StagingAccessError("owner verifier must use Argon2id")
        if (
            parameters.memory_cost < ARGON2_MEMORY_COST_KIB
            or parameters.time_cost < ARGON2_TIME_COST
            or parameters.parallelism < ARGON2_PARALLELISM
            or parameters.salt_len < ARGON2_SALT_LEN
            or parameters.hash_len < ARGON2_HASH_LEN
        ):
            raise StagingAccessError("owner verifier parameters are weaker than policy")

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
        if not isinstance(supplied, str):
            supplied = ""
        now = self._clock()
        with self._lock:
            self._prune_failures(now)
            if now < self._cooldown_until:
                raise AuthenticationThrottled("owner authentication is temporarily unavailable")
        if not self._slot.acquire(blocking=False):
            raise AuthenticationBusy("owner authentication verifier is busy")
        try:
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
        finally:
            self._slot.release()

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
        now = self._clock()
        session = StagingSession(
            session_ref=secrets.token_hex(32),
            principal_ref=principal_ref,
            role_ref=role_ref,
            capabilities=frozenset(capabilities),
            credential_fingerprint=credential_fingerprint,
            csrf_digest=self._digest(csrf_token),
            created_monotonic=now,
            last_seen_monotonic=now,
        )
        with self._lock:
            self._sessions[self._digest(token)] = session
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
    """Single-use, bounded pre-authentication CSRF challenges."""

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
        self._challenges: dict[bytes, _StoredLoginChallenge] = {}
        self._lock = RLock()

    def create(self) -> LoginChallenge:
        cookie_token = secrets.token_urlsafe(32)
        form_token = secrets.token_urlsafe(32)
        now = self._clock()
        stored = _StoredLoginChallenge(
            form_digest=self._digest(form_token),
            expires_monotonic=now + self._ttl_seconds,
        )
        with self._lock:
            self._prune(now)
            if len(self._challenges) >= self._maximum_challenges:
                oldest = next(iter(self._challenges))
                self._challenges.pop(oldest, None)
            self._challenges[self._digest(cookie_token)] = stored
        return LoginChallenge(cookie_token=cookie_token, form_token=form_token)

    def consume(self, cookie_token: str | None, form_token: str | None) -> bool:
        if not cookie_token or not form_token:
            return False
        now = self._clock()
        cookie_digest = self._digest(cookie_token)
        with self._lock:
            self._prune(now)
            stored = self._challenges.get(cookie_digest)
            if stored is None or now > stored.expires_monotonic:
                return False
            if not hmac.compare_digest(stored.form_digest, self._digest(form_token)):
                return False
            self._challenges.pop(cookie_digest, None)
            return True

    def clear(self) -> None:
        with self._lock:
            self._challenges.clear()

    def _prune(self, now: float) -> None:
        expired = [
            digest
            for digest, value in self._challenges.items()
            if now > value.expires_monotonic
        ]
        for digest in expired:
            self._challenges.pop(digest, None)

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()
