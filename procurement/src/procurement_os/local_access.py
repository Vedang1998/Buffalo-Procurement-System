"""Fail-closed loopback authentication for the local purchasing candidate.

This is deliberately a local identity fixture, not a production IdP.  It uses
one server-owned named principal, opaque in-memory sessions, an exact loopback
origin, and a complete method/path capability policy.  Browser input cannot
choose the runtime mode, principal, role, or capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar, Token
from http.cookies import SimpleCookie
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import stat
from threading import RLock
import time
from typing import Any

from starlette.responses import Response

from .local_identity import Principal, authentication_context_sha256
from .staging_identity import StagingRequestIdentity


SESSION_COOKIE = "buffalo_local_session"
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 8 * 60 * 60
_MODES = frozenset({"AUTOMATED_TEST", "SYNTHETIC_DEMO", "PRIVATE_REAL_SOURCE_REVIEW"})
_FULL_CAPABILITIES = frozenset(
    {
        "procurement.review.read",
        "procurement.evidence.download",
        "procurement.catalog_identity.decide",
        "procurement.vendor_rules.manage",
        "procurement.price.approve",
        "procurement.order.approve",
        "procurement.review.intake",
        "procurement.mapping.approve",
        "procurement.offer.select",
    }
)
_READ_ONLY_CAPABILITIES = frozenset(
    {
        "procurement.review.read",
        "procurement.evidence.download",
        "procurement.private_research.read",
        "procurement.private_research.download",
    }
)


@dataclass(frozen=True)
class LocalAccessConfig:
    mode: str
    port: int
    origin: str
    secret_file: Path | None
    principal_ref: str
    role_ref: str
    capabilities: frozenset[str]


@dataclass
class LocalSession:
    session_ref: str
    principal: Principal
    capabilities: frozenset[str]
    mode: str
    origin: str
    secret_binding_sha256: str
    created_monotonic: float
    last_seen_monotonic: float


_sessions: dict[str, LocalSession] = {}
_session_lock = RLock()
_NO_REQUEST_CONTEXT = object()
_request_session: ContextVar[
    LocalSession | StagingRequestIdentity | None | object
] = ContextVar(
    "buffalo_local_request_session", default=_NO_REQUEST_CONTEXT
)


def clear_local_sessions() -> None:
    """Invalidate every local session, including across a simulated restart."""

    with _session_lock:
        _sessions.clear()


def bind_staging_request_identity(
    identity: StagingRequestIdentity,
) -> Token[LocalSession | StagingRequestIdentity | None | object]:
    """Bind a verified staging identity without fabricating a local session."""

    identity.validate()
    return _request_session.set(identity)


def reset_request_identity(
    token: Token[LocalSession | StagingRequestIdentity | None | object],
) -> None:
    _request_session.reset(token)


def request_runtime_mode() -> str:
    """Return the server-owned request mode while preserving local defaults."""

    identity = _request_session.get()
    if isinstance(identity, StagingRequestIdentity):
        return (
            "SYNTHETIC_DEMO"
            if identity.worker_role == "synthetic"
            else "PRIVATE_REAL_SOURCE_REVIEW"
        )
    return runtime_config().mode


def staging_request_has_capability(capability: str) -> bool:
    identity = _request_session.get()
    return (
        isinstance(identity, StagingRequestIdentity)
        and capability in identity.capabilities
    )


def staging_request_worker_role() -> str | None:
    identity = _request_session.get()
    return identity.worker_role if isinstance(identity, StagingRequestIdentity) else None


def runtime_config() -> LocalAccessConfig:
    raw_mode = os.getenv("BUFFALO_RUNTIME_MODE", "UNCONFIGURED").strip().upper()
    mode = raw_mode if raw_mode in _MODES else "UNCONFIGURED"
    try:
        port = int(os.getenv("BUFFALO_LOCAL_PORT", "8765"))
    except ValueError:
        port = 0
    if port < 1 or port > 65535:
        mode = "UNCONFIGURED"
        port = 8765
    secret_value = os.getenv("BUFFALO_LOCAL_AUTH_SECRET_FILE", "").strip()
    secret_file = Path(secret_value) if secret_value else None
    principal_ref = os.getenv("BUFFALO_LOCAL_PRINCIPAL_REF", "").strip()
    role_ref = os.getenv("BUFFALO_LOCAL_ROLE_REF", "").strip()
    if not principal_ref or not role_ref or secret_file is None:
        mode = "UNCONFIGURED"
    capabilities = (
        _FULL_CAPABILITIES
        if mode in {"AUTOMATED_TEST", "SYNTHETIC_DEMO"}
        else _READ_ONLY_CAPABILITIES
        if mode == "PRIVATE_REAL_SOURCE_REVIEW"
        else frozenset()
    )
    return LocalAccessConfig(
        mode=mode,
        port=port,
        origin=f"http://127.0.0.1:{port}",
        secret_file=secret_file,
        principal_ref=principal_ref,
        role_ref=role_ref,
        capabilities=capabilities,
    )


def _read_secret(config: LocalAccessConfig) -> bytes:
    path = config.secret_file
    if path is None or not path.is_absolute():
        raise PermissionError("local authentication secret file is not configured")
    try:
        parent = path.parent.stat(follow_symlinks=False)
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise PermissionError("local authentication secret file is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise PermissionError("local authentication secret must be a regular file")
    if stat.S_IMODE(parent.st_mode) != 0o700 or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError("local authentication secret permissions differ")
    if info.st_uid != os.getuid() or parent.st_uid != os.getuid():
        raise PermissionError("local authentication secret ownership differs")
    value = path.read_bytes()
    if value.endswith(b"\n"):
        value = value[:-1]
    if len(value) < 24 or len(value) > 512 or b"\x00" in value:
        raise PermissionError("local authentication secret shape differs")
    return value


def authenticate_secret(supplied: str) -> bool:
    config = runtime_config()
    if config.mode not in _MODES:
        return False
    try:
        expected = _read_secret(config)
    except PermissionError:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected)


def create_local_session(supplied: str) -> tuple[str, LocalSession]:
    """Atomically authenticate the supplied secret and mint one bound session.

    Callers cannot split verification from session creation.  The session also
    retains only a digest binding to the exact secret bytes; a later file or
    configuration replacement invalidates it on the next protected request.
    """

    config = runtime_config()
    if config.mode not in _MODES:
        raise PermissionError("local authentication is unavailable")
    expected = _read_secret(config)
    if not hmac.compare_digest(supplied.encode("utf-8"), expected):
        raise PermissionError("local authentication failed")
    secret_binding_sha256 = hashlib.sha256(expected).hexdigest()
    token = secrets.token_urlsafe(32)
    session_ref = secrets.token_hex(32)
    principal = Principal(
        principal_ref=config.principal_ref,
        role_ref=config.role_ref,
        authn_context_sha256=authentication_context_sha256(
            principal_ref=config.principal_ref,
            role_ref=config.role_ref,
            session_ref=session_ref,
        ),
    )
    now = time.monotonic()
    session = LocalSession(
        session_ref=session_ref,
        principal=principal,
        capabilities=config.capabilities,
        mode=config.mode,
        origin=config.origin,
        secret_binding_sha256=secret_binding_sha256,
        created_monotonic=now,
        last_seen_monotonic=now,
    )
    with _session_lock:
        _sessions[token] = session
    return token, session


def destroy_local_session(token: str | None) -> None:
    if not token:
        return
    with _session_lock:
        _sessions.pop(token, None)


def _cookie_token(headers: dict[bytes, bytes]) -> str | None:
    raw = headers.get(b"cookie")
    if raw is None:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw.decode("latin-1"))
    except Exception:
        return None
    item = cookie.get(SESSION_COOKIE)
    return item.value if item is not None else None


def _session(token: str | None, config: LocalAccessConfig) -> LocalSession | None:
    if not token:
        return None
    now = time.monotonic()
    with _session_lock:
        value = _sessions.get(token)
        if value is None:
            return None
        try:
            current_secret_binding = hashlib.sha256(_read_secret(config)).hexdigest()
        except PermissionError:
            current_secret_binding = ""
        expired = (
            now - value.created_monotonic > SESSION_ABSOLUTE_SECONDS
            or now - value.last_seen_monotonic > SESSION_IDLE_SECONDS
        )
        if (
            expired
            or value.mode != config.mode
            or value.origin != config.origin
            or value.principal.principal_ref != config.principal_ref
            or value.principal.role_ref != config.role_ref
            or value.secret_binding_sha256 != current_secret_binding
        ):
            _sessions.pop(token, None)
            return None
        value.last_seen_monotonic = now
        return value


_DOWNLOAD_PATHS = (
    re.compile(r"^/monday-runs/[^/]+/artifacts/[^/]+$"),
    re.compile(r"^/price-books/template\.csv$"),
    re.compile(r"^/price-books/[^/]+/raw\.csv$"),
    re.compile(r"^/supplier-mapping/[^/]+/source$"),
)
_POST_POLICIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/(economics/(target-cost|qualifying-quantity)|matching/score)$"), "procurement.review.read"),
    (re.compile(r"^/vendor-rules/[^/]+$"), "procurement.vendor_rules.manage"),
    (re.compile(r"^/(reconciliation/.*|historical-sales/review/decide)$"), "procurement.catalog_identity.decide"),
    (re.compile(r"^/monday-runs(/.*)?$"), "procurement.order.approve"),
    (re.compile(r"^/(price-books/.*|pricing/rollover)$"), "procurement.price.approve"),
    (re.compile(r"^/supplier-mapping/intake$"), "procurement.review.intake"),
    (re.compile(r"^/supplier-mapping/[^/]+/decision$"), "procurement.mapping.approve"),
    (re.compile(r"^/supplier-mapping/[^/]+/selection$"), "procurement.offer.select"),
)


def required_capability(method: str, path: str) -> str | None:
    method = method.upper()
    if path == "/health" and method == "GET":
        return "PUBLIC"
    if path == "/auth/login" and method in {"GET", "POST"}:
        return "PUBLIC"
    if path == "/auth/logout" and method == "POST":
        return "procurement.review.read"
    if method in {"GET", "HEAD"}:
        if re.fullmatch(r"/private-research/artifacts/[^/]+", path):
            return "procurement.private_research.download"
        if path == "/private-research" or path.startswith("/private-research/"):
            return "procurement.private_research.read"
        if path in {"/docs", "/redoc", "/openapi.json"}:
            return None
        if any(pattern.fullmatch(path) for pattern in _DOWNLOAD_PATHS):
            return "procurement.evidence.download"
        return "procurement.review.read"
    if method == "POST":
        for pattern, capability in _POST_POLICIES:
            if pattern.fullmatch(path):
                return capability
    return None


def principal_from_request(request: Any) -> Principal:
    session = getattr(request.state, "local_session", None)
    if isinstance(session, LocalSession):
        return session.principal
    identity = getattr(request.state, "staging_identity", None)
    if isinstance(identity, StagingRequestIdentity):
        return identity.principal_for()
    raise PermissionError("authenticated server session is absent")


def action_principal(request: Any, capability: str) -> Principal:
    """Return an action-specific role bound to the same server session."""

    session = getattr(request.state, "local_session", None)
    if isinstance(session, LocalSession):
        if capability not in session.capabilities:
            raise PermissionError("authenticated local capability is absent")
        return Principal(
            principal_ref=session.principal.principal_ref,
            role_ref=capability,
            authn_context_sha256=authentication_context_sha256(
                principal_ref=session.principal.principal_ref,
                role_ref=capability,
                session_ref=session.session_ref,
            ),
        )
    identity = getattr(request.state, "staging_identity", None)
    if isinstance(identity, StagingRequestIdentity):
        if capability not in identity.capabilities:
            raise PermissionError("authenticated staging capability is absent")
        return identity.principal_for(capability)
    raise PermissionError("authenticated server capability is absent")


def has_capability(request: Any, capability: str) -> bool:
    session = getattr(request.state, "local_session", None)
    if isinstance(session, LocalSession):
        return capability in session.capabilities
    identity = getattr(request.state, "staging_identity", None)
    return (
        isinstance(identity, StagingRequestIdentity)
        and capability in identity.capabilities
    )


def authenticated_audit_actor(direct_call_fallback: str = "") -> str:
    """Return the server-owned principal for an HTTP audit record.

    Direct Python calls made by existing service-oriented unit tests have no
    ASGI request context and retain their explicit test actor.  Once a request
    has entered the middleware, however, the browser-supplied actor is never
    authoritative: an absent authenticated session fails closed.
    """

    session = _request_session.get()
    if session is _NO_REQUEST_CONTEXT:
        value = str(direct_call_fallback).strip()
        if not value:
            raise PermissionError("direct-call audit actor is absent")
        return value
    if not isinstance(session, LocalSession):
        if isinstance(session, StagingRequestIdentity):
            return session.principal_ref
        raise PermissionError("authenticated server session is absent")
    return session.principal.principal_ref


class LocalAccessMiddleware:
    """Outer ASGI authorization and exact-origin CSRF boundary."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        config = runtime_config()
        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        raw_headers = [(key.lower(), value) for key, value in scope.get("headers", ())]
        if any(
            sum(1 for key, _ in raw_headers if key == protected) != 1
            for protected in (b"host",)
        ) or any(
            sum(1 for key, _ in raw_headers if key == protected) > 1
            for protected in (b"origin", b"cookie")
        ):
            await self._response(send, 403, b"Duplicate security header refused")
            return
        headers = dict(raw_headers)
        host = headers.get(b"host", b"").decode("latin-1")
        forwarded = any(
            key == b"forwarded" or key.startswith(b"x-forwarded-") for key in headers
        )
        client_host = str((scope.get("client") or ("", 0))[0])
        allowed_client = client_host in {"127.0.0.1", "::1"} or (
            config.mode == "AUTOMATED_TEST" and client_host == "testclient"
        )
        if forwarded or host != f"127.0.0.1:{config.port}" or not allowed_client:
            await self._response(send, 403, b"Exact loopback origin required")
            return
        capability = required_capability(method, path)
        if capability is None:
            await self._response(send, 403, b"Route is not authorized")
            return
        if method not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin != config.origin:
                await self._response(send, 403, b"Exact same-origin request required")
                return
        token = _cookie_token(headers)
        session = _session(token, config)
        if capability != "PUBLIC":
            if config.mode == "UNCONFIGURED":
                await self._response(send, 503, b"Local access mode is not configured")
                return
            if session is None:
                await self._response(send, 401, b"Authentication required")
                return
            if capability not in session.capabilities:
                await self._response(send, 403, b"Capability denied")
                return
            scope.setdefault("state", {})["local_session"] = session

        async def protected_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                protected_names = {
                    b"cache-control",
                    b"pragma",
                    b"x-content-type-options",
                    b"referrer-policy",
                    b"content-security-policy",
                }
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", ())
                    if key.lower() not in protected_names
                ]
                response_headers.extend(
                    (
                        (b"cache-control", b"no-store"),
                        (b"pragma", b"no-cache"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"same-origin"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; "
                            b"style-src 'self' 'unsafe-inline'; object-src 'none'; "
                            b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                        ),
                    )
                )
                message["headers"] = response_headers
            await send(message)

        context_token: Token[LocalSession | None | object] = _request_session.set(
            session
        )
        try:
            await self.app(scope, receive, protected_send)
        finally:
            _request_session.reset(context_token)

    async def _response(self, send: Any, status: int, body: bytes) -> None:
        response = Response(
            body,
            status_code=status,
            media_type="text/plain",
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self' 'unsafe-inline'; object-src 'none'; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
                ),
            },
        )
        await response({"type": "http"}, None, send)
