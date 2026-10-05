"""Fail-closed public request boundary for Railway staging.

The gateway owns browser authentication and is the only process exposed on
Railway's public port.  It selects a worker from a fixed route table and binds
each forwarded request to a short-lived, worker-specific assertion.  This
module intentionally imports neither operational nor research application
state; callers supply the already-inspected route metadata and a transport.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from contextlib import suppress
from http.cookies import CookieError, SimpleCookie
import html
import ipaddress
import inspect
import json
import os
import re
import secrets
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qs, urljoin, urlsplit

from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from .staging_access import (
    CSRF_COOKIE,
    LOGIN_CHALLENGE_COOKIE,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_COOKIE,
    AuthenticationBusy,
    AuthenticationThrottled,
    LoginChallengeStore,
    OwnerPasswordAuthenticator,
    StagingSession,
    StagingSessionStore,
    cookie_settings,
)
from .staging_config import StagingConfig
from .staging_internal import mint_assertion
from .staging_identity import OWNER_PRINCIPAL_REF, OWNER_ROLE_REF
from .staging_routes import (
    ROUTES,
    RouteSpec,
    StagingRouteError,
    canonical_path_is_valid,
    match_route,
    response_metadata_is_allowed,
    validate_route_query,
)
from .staging_research_gateway import (
    ResearchControlClient,
    ResearchDecision,
    ResearchGatewayCoordinator,
)
from .staging_worker_types import (
    WorkerKeyState,
    WorkerKeyring,
    WorkerResponse,
    WorkerTransport,
)


_MAX_LOGIN_BODY = 16 * 1024
_SECURITY_HEADERS = (
    ("cache-control", "no-store"),
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "no-referrer"),
    ("x-frame-options", "DENY"),
    (
        "content-security-policy",
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
    ),
    ("strict-transport-security", "max-age=31536000"),
)
_SINGLETON_HEADERS = frozenset(
    {
        b"host",
        b"origin",
        b"cookie",
        b"content-length",
        b"content-type",
        b"transfer-encoding",
        b"x-buffalo-csrf-token",
        b"x-real-ip",
        b"x-forwarded-proto",
        b"x-forwarded-host",
        b"x-railway-edge",
        b"x-request-start",
        b"x-railway-request-id",
    }
)
_ALLOWED_PROXY_HEADERS = frozenset(
    {
        b"x-real-ip",
        b"x-forwarded-proto",
        b"x-forwarded-host",
        b"x-railway-edge",
        b"x-request-start",
        b"x-railway-request-id",
    }
)
_HOP_BY_HOP = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
    }
)
_WORKER_REQUEST_HEADERS = frozenset({b"accept", b"content-type"})
_WORKER_RESPONSE_HEADERS = frozenset(
    {b"content-type", b"content-disposition", b"location", b"retry-after"}
)
_CSRF_FORM_FIELD = "_buffalo_staging_csrf"
_RESEARCH_RETRY_PATH = "/private-research/retry"
_RAILWAY_HEALTHCHECK_HOST = "healthcheck.railway.app"
_MAX_HTML_FORMS = 512
_RAILWAY_EDGE_RE = re.compile(r"\A[a-z]{3}[1-9][0-9]{0,2}\Z")
_RAILWAY_REQUEST_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RAILWAY_REQUEST_START_RE = re.compile(r"\A[0-9]{13}\Z")
_REQUEST_LOG_KEYS = frozenset(
    {
        "authenticated",
        "duration_ms",
        "event",
        "method",
        "request_id",
        "route",
        "status",
        "version",
    }
)
_REQUEST_LOG_ID_RE = re.compile(r"\A[a-f0-9]{32}\Z")
_REQUEST_LOG_METHODS = frozenset({"GET", "POST", "HEAD", "OPTIONS", "OTHER", "INVALID"})
_REQUEST_LOG_ROUTES = frozenset(
    {
        "invalid",
        "unmatched",
        "gateway.health",
        "gateway.login",
        "gateway.logout",
        "gateway.readiness",
        "gateway.research_retry",
        *(route.route_id for route in ROUTES),
    }
)
_POST_FORM = re.compile(
    rb"(<form\b[^>]*\bmethod\s*=\s*(['\"])post\2[^>]*>)",
    re.IGNORECASE,
)
_REVIEW_TOKEN_LABEL = re.compile(
    rb"<label\b[^>]*>[^<]*<input\b(?=[^>]*\bname\s*=\s*(['\"]?)review_token\1)[^>]*>\s*</label>(?:<br>)?",
    re.IGNORECASE,
)
_REVIEW_TOKEN_INPUT = re.compile(
    rb"<input\b(?=[^>]*\bname\s*=\s*(['\"]?)review_token\1)[^>]*>",
    re.IGNORECASE,
)
class StagingGatewayError(ValueError):
    """The public staging request cannot be accepted."""


class ResearchWorkerUnavailable(RuntimeError):
    """A previously admitted research generation became unroutable."""


class GatewayActivationService(Protocol):
    """One boot-scoped supervisor-to-gateway activation endpoint."""

    async def serve(self) -> None: ...

    async def close(self) -> None: ...


def write_sanitized_request_log(record: Mapping[str, object]) -> None:
    """Write one bounded record containing no request-derived payload values."""

    if (
        set(record) != _REQUEST_LOG_KEYS
        or record.get("event") != "staging_request"
        or record.get("version") != 1
        or record.get("method") not in _REQUEST_LOG_METHODS
        or record.get("route") not in _REQUEST_LOG_ROUTES
        or type(record.get("authenticated")) is not bool
        or type(record.get("duration_ms")) is not int
        or not 0 <= record["duration_ms"] <= 86_400_000
        or not isinstance(record.get("request_id"), str)
        or _REQUEST_LOG_ID_RE.fullmatch(record["request_id"]) is None
        or type(record.get("status")) is not int
        or not 100 <= record["status"] <= 599
    ):
        raise StagingGatewayError("request log contract differs")
    encoded = json.dumps(
        dict(record),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii") + b"\n"
    if len(encoded) > 512:
        raise StagingGatewayError("request log record is too large")
    os.write(1, encoded)


class RegisteredRoutePolicy:
    """The reviewed static registry; each worker validates its own live routes."""

    def __init__(self) -> None:
        self.capabilities = frozenset(route.capability for route in ROUTES)

    def match(self, *, method: str, path: str, raw_path: bytes) -> RouteSpec | None:
        return match_route(method=method, path=path, raw_path=raw_path)


class StagingGateway:
    """ASGI gateway with server-owned identity and exact worker selection."""

    def __init__(
        self,
        *,
        config: StagingConfig,
        route_policy: RegisteredRoutePolicy,
        transport: WorkerTransport,
        worker_keys: Mapping[str, bytes],
        research_control: ResearchControlClient | None = None,
        activation_service_factory: (
            Callable[[WorkerKeyring], GatewayActivationService] | None
        ) = None,
        request_logger: Callable[[Mapping[str, object]], None] | None = None,
        wall_clock: Any = time.time,
    ) -> None:
        try:
            keyring = WorkerKeyring(dict(worker_keys), active_roles=frozenset())
        except ValueError as exc:
            raise StagingGatewayError(str(exc)) from exc
        self.config = config
        self.route_policy = route_policy
        self.transport = transport
        self.worker_keyring = keyring
        self.research_coordinator = (
            ResearchGatewayCoordinator(
                control=research_control,
                keyring=keyring,
            )
            if research_control is not None
            else None
        )
        activation_service = None
        if activation_service_factory is not None:
            activation_service = activation_service_factory(keyring)
            if activation_service is None or (
                not callable(getattr(activation_service, "serve", None))
                or not callable(getattr(activation_service, "close", None))
            ):
                raise StagingGatewayError("activation service contract differs")
        self._activation_service = activation_service
        self._activation_task: asyncio.Task[None] | None = None
        if request_logger is not None and not callable(request_logger):
            raise StagingGatewayError("request logger contract differs")
        self._request_logger = request_logger
        self.authenticator = OwnerPasswordAuthenticator(config.owner_verifier)
        self.sessions = StagingSessionStore()
        self.login_challenges = LoginChallengeStore()
        self._wall_clock = wall_clock
        self._verification_tasks: set[asyncio.Task[bool]] = set()

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope_type != "http":
            return
        started_at = time.monotonic()
        log_method = "INVALID"
        log_route = "invalid"
        log_status = 500
        log_authenticated = False
        log_request_id = secrets.token_hex(16)
        downstream_send = send

        async def logged_send(message):
            nonlocal log_status
            if message.get("type") == "http.response.start":
                status = message.get("status")
                if type(status) is int and 100 <= status <= 599:
                    log_status = status
            await downstream_send(message)

        send = logged_send
        try:
            raw_method = scope.get("method")
            if (
                not isinstance(raw_method, str)
                or re.fullmatch(r"[A-Z]+", raw_method) is None
            ):
                raise StagingGatewayError("public request method is not canonical")
            method = raw_method
            log_method = method if method in {"GET", "POST", "HEAD", "OPTIONS"} else "OTHER"
            path = str(scope.get("path", ""))
            raw_path = bytes(scope.get("raw_path") or path.encode("utf-8"))
            query = bytes(scope.get("query_string") or b"")
            if not canonical_path_is_valid(path=path, raw_path=raw_path):
                raise StagingGatewayError("public request path is not canonical")
            headers = self._validated_headers(
                scope,
                allow_healthcheck_host=(method == "GET" and path == "/health"),
            )
            if method == "GET" and path == "/health":
                log_route = "gateway.health"
                self._validate_query(query, frozenset())
                self._require_zero_body_headers(headers)
                await self._respond(
                    send,
                    JSONResponse(
                        {
                            "ok": True,
                            "service": "buffalo-procurement-staging-gateway",
                        }
                    ),
                )
                return
            if method == "GET" and path == "/auth/login":
                log_route = "gateway.login"
                self._validate_query(query, frozenset())
                self._require_zero_body_headers(headers)
                await self._login_page(send)
                return
            if method == "POST" and path == "/auth/login":
                log_route = "gateway.login"
                self._validate_query(query, frozenset())
                self._require_body_within_limit(headers, limit=_MAX_LOGIN_BODY)
                body = await self._read_body(receive, limit=_MAX_LOGIN_BODY)
                self._validate_body_framing(headers, body)
                await self._login(headers=headers, body=body, send=send)
                return
            session_token, session = self._session(headers)
            log_authenticated = session is not None
            if method == "POST" and path == "/auth/logout":
                log_route = "gateway.logout"
                self._validate_query(query, frozenset())
                self._require_body_within_limit(headers, limit=1_024)
                body = await self._read_body(receive, limit=1_024)
                self._validate_body_framing(headers, body)
                if body and self._content_type(headers) != "application/x-www-form-urlencoded":
                    raise StagingGatewayError("logout content type is not allowed")
                await self._logout(
                    headers=headers,
                    session_token=session_token,
                    session=session,
                    body=body,
                    send=send,
                )
                return
            if session is None:
                await self._respond(send, PlainTextResponse("Unauthorized", status_code=401))
                return
            if method == "GET" and path == "/ready":
                log_route = "gateway.readiness"
                self._validate_query(query, frozenset())
                self._require_zero_body_headers(headers)
                await self._readiness(send)
                return
            if method == "POST" and path == _RESEARCH_RETRY_PATH:
                log_route = "gateway.research_retry"
                await self._retry_research(
                    headers=headers,
                    query=query,
                    receive=receive,
                    send=send,
                    session=session,
                )
                return
            selection = self.route_policy.match(
                method=method, path=path, raw_path=raw_path
            )
            if selection is not None:
                log_route = selection.route_id
            if selection is None or selection.capability not in session.capabilities:
                await self._respond(send, PlainTextResponse("Forbidden", status_code=403))
                return
            self._validate_query(query, selection.allowed_query_fields)
            if method not in {"GET", "HEAD", "OPTIONS"}:
                self._require_body_within_limit(headers, limit=selection.body_limit)
                body = await self._read_body(receive, limit=selection.body_limit)
                self._validate_body_framing(headers, body)
                self._validate_content_type(headers, selection)
                self._require_origin_and_csrf(
                    headers, session, body=body
                )
            else:
                self._require_zero_body_headers(headers)
                body = b""
            if selection.worker_role == "research":
                coordinator = self.research_coordinator
                lease = (
                    await coordinator.authorized_request()
                    if coordinator is not None
                    else None
                )
                if lease is None:
                    await self._research_unavailable(
                        send=send,
                        headers=headers,
                        session=session,
                        decision=ResearchDecision(
                            state="STOPPING",
                            generation=0,
                            retry_after_seconds=1,
                            routable=False,
                        ),
                    )
                    return
                async with lease as decision:
                    if not decision.routable:
                        await self._research_unavailable(
                            send=send,
                            headers=headers,
                            session=session,
                            decision=decision,
                        )
                        return
                    await self._proxy(
                        receive=receive,
                        send=send,
                        headers=headers,
                        method=method,
                        path=path,
                        query=query,
                        body=body,
                        request_id=log_request_id,
                        session=session,
                        selection=selection,
                        expected_generation=decision.generation,
                    )
                return
            await self._proxy(
                receive=receive,
                send=send,
                headers=headers,
                method=method,
                path=path,
                query=query,
                body=body,
                request_id=log_request_id,
                session=session,
                selection=selection,
                expected_generation=None,
            )
        except asyncio.CancelledError:
            raise
        except ResearchWorkerUnavailable:
            await self._respond(
                send,
                PlainTextResponse(
                    "Private research unavailable",
                    status_code=503,
                    headers={"retry-after": "1"},
                ),
            )
        except StagingGatewayError:
            await self._respond(send, PlainTextResponse("Forbidden", status_code=403))
        except Exception:
            # The public boundary never lets a transport/parser defect escape
            # as an unprotected framework error page.
            await self._respond(
                send, PlainTextResponse("Staging worker unavailable", status_code=502)
            )
        finally:
            self._emit_request_log(
                {
                    "authenticated": log_authenticated,
                    "duration_ms": max(
                        0, min(86_400_000, int((time.monotonic() - started_at) * 1_000))
                    ),
                    "event": "staging_request",
                    "method": log_method,
                    "request_id": log_request_id,
                    "route": log_route,
                    "status": log_status,
                    "version": 1,
                }
            )

    def _emit_request_log(self, record: Mapping[str, object]) -> None:
        logger = self._request_logger
        if logger is None:
            return
        try:
            logger(record)
        except Exception:
            # A telemetry sink cannot replace the already-decided response.
            pass

    async def _readiness(self, send) -> None:
        synthetic = self.worker_keyring.status("synthetic")
        synthetic_state = {
            WorkerKeyState.ACTIVE: "ready",
            WorkerKeyState.PENDING: "validating",
            WorkerKeyState.DISABLED: "unavailable",
        }[synthetic.state]
        coordinator = self.research_coordinator
        research_state = (
            await coordinator.readiness_state()
            if coordinator is not None
            else "unavailable"
        )
        ready = synthetic_state == "ready" and research_state in {"idle", "ready"}
        headers = {} if ready else {"retry-after": "1"}
        await self._respond(
            send,
            JSONResponse(
                {
                    "components": {
                        "gateway": "ready",
                        "research": research_state,
                        "synthetic": synthetic_state,
                    },
                    "ok": ready,
                    "source_commit": self.config.expected_source_commit,
                },
                status_code=200 if ready else 503,
                headers=headers,
            ),
        )

    def _validated_headers(
        self,
        scope: Mapping[str, object],
        *,
        allow_healthcheck_host: bool = False,
    ) -> dict[bytes, bytes]:
        pairs = [(bytes(key).lower(), bytes(value)) for key, value in scope.get("headers", ())]
        grouped: dict[bytes, list[bytes]] = {}
        for key, value in pairs:
            if len(key) > 128 or len(value) > 8_192 or b"\x00" in key + value:
                raise StagingGatewayError("request header is invalid")
            grouped.setdefault(key, []).append(value)
            if key == b"forwarded":
                raise StagingGatewayError("forwarded header is not accepted")
            if key.startswith(b"x-forwarded-") and key not in _ALLOWED_PROXY_HEADERS:
                raise StagingGatewayError("forwarded header is not accepted")
            if key.startswith(b"x-buffalo-") and key != b"x-buffalo-csrf-token":
                raise StagingGatewayError("client internal header is not accepted")
        if any(len(grouped.get(key, ())) > 1 for key in _SINGLETON_HEADERS):
            raise StagingGatewayError("duplicate security header is not accepted")
        decoded_singletons = {
            key: self._decode_header(grouped[key][0])
            for key in _SINGLETON_HEADERS
            if key in grouped
        }
        if b"transfer-encoding" in grouped:
            raise StagingGatewayError("transfer-encoded request is not accepted")
        raw_length = grouped.get(b"content-length", [None])[0]
        if raw_length is not None:
            try:
                text_length = raw_length.decode("ascii")
            except UnicodeDecodeError:
                raise StagingGatewayError("request content length is invalid") from None
            if not text_length or not text_length.isdecimal():
                raise StagingGatewayError("request content length is invalid")
        host = decoded_singletons.get(b"host", "")
        if host != self.config.external_host and not (
            allow_healthcheck_host and host == _RAILWAY_HEALTHCHECK_HOST
        ):
            raise StagingGatewayError("external host differs")
        if decoded_singletons.get(b"x-forwarded-proto", "https") != "https":
            raise StagingGatewayError("external protocol differs")
        if decoded_singletons.get(
            b"x-forwarded-host", self.config.external_host
        ) != self.config.external_host:
            raise StagingGatewayError("forwarded host differs")
        forwarded_pair = {
            b"x-forwarded-proto",
            b"x-forwarded-host",
        }
        if len(forwarded_pair.intersection(grouped)) == 1:
            raise StagingGatewayError("forwarded header set is incomplete")
        if b"x-real-ip" in grouped:
            real_ip = decoded_singletons[b"x-real-ip"]
            try:
                ipaddress.ip_address(real_ip)
            except ValueError:
                raise StagingGatewayError("Railway client IP is invalid") from None
        if b"x-railway-edge" in grouped and _RAILWAY_EDGE_RE.fullmatch(
            decoded_singletons[b"x-railway-edge"]
        ) is None:
            raise StagingGatewayError("Railway edge identity is invalid")
        if b"x-request-start" in grouped and _RAILWAY_REQUEST_START_RE.fullmatch(
            decoded_singletons[b"x-request-start"]
        ) is None:
            raise StagingGatewayError("Railway request timestamp is invalid")
        if b"x-railway-request-id" in grouped and _RAILWAY_REQUEST_ID_RE.fullmatch(
            decoded_singletons[b"x-railway-request-id"]
        ) is None:
            raise StagingGatewayError("Railway request identity is invalid")
        if b"x-buffalo-csrf-token" in grouped and re.fullmatch(
            r"[A-Za-z0-9_-]{43}",
            decoded_singletons[b"x-buffalo-csrf-token"],
        ) is None:
            raise StagingGatewayError("request CSRF header is invalid")
        if b"origin" in grouped and decoded_singletons[b"origin"] != self.config.external_origin:
            raise StagingGatewayError("request origin differs")
        validated = {key: values[0] for key, values in grouped.items()}
        if b"cookie" in validated:
            self._cookies(validated)
        return validated

    async def _lifespan(self, receive, send) -> None:
        started = False
        components_active = False

        async def cleanup() -> None:
            nonlocal components_active
            if components_active:
                try:
                    await self._shutdown_lifespan_components()
                finally:
                    components_active = False

        try:
            while True:
                message = await receive()
                message_type = message.get("type")
                if message_type == "lifespan.startup" and not started:
                    components_active = True
                    try:
                        startup = getattr(self.transport, "startup", None)
                        if callable(startup):
                            result = startup()
                            if inspect.isawaitable(result):
                                await result
                        if self.research_coordinator is not None:
                            await self.research_coordinator.startup()
                        if self._activation_service is not None:
                            task = asyncio.create_task(
                                self._activation_service.serve(),
                                name="buffalo-gateway-worker-activation",
                            )
                            self._activation_task = task
                            task.add_done_callback(self._activation_done)
                            await asyncio.sleep(0)
                            if task.done():
                                if task.cancelled():
                                    raise StagingGatewayError(
                                        "activation service stopped"
                                    )
                                failure = task.exception()
                                if failure is not None:
                                    raise failure
                                raise StagingGatewayError(
                                    "activation service stopped"
                                )
                    except Exception:
                        await cleanup()
                        await send(
                            {
                                "type": "lifespan.startup.failed",
                                "message": "gateway startup refused",
                            }
                        )
                        return
                    await send({"type": "lifespan.startup.complete"})
                    started = True
                    continue
                if message_type == "lifespan.shutdown" and started:
                    await cleanup()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
                failure_type = (
                    "lifespan.shutdown.failed"
                    if started
                    else "lifespan.startup.failed"
                )
                await cleanup()
                await send(
                    {"type": failure_type, "message": "invalid lifespan sequence"}
                )
                return
        except BaseException:
            await cleanup()
            raise

    def _activation_done(self, task: asyncio.Task[None]) -> None:
        # Retrieve every detached-task result.  The activation server's own
        # fatal callback is responsible for making the process exit if this
        # was not an orderly close.
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass

    async def _shutdown_lifespan_components(self) -> None:
        activation = self._activation_service
        task = self._activation_task
        failures: list[BaseException] = []
        caller_cancelled = False
        if activation is not None:
            try:
                await activation.close()
            except asyncio.CancelledError:
                caller_cancelled = True
            except Exception as exc:
                failures.append(exc)
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    caller_cancelled = True
            except Exception:
                pass
        if self.research_coordinator is not None:
            try:
                await self.research_coordinator.shutdown()
            except asyncio.CancelledError:
                caller_cancelled = True
            except Exception as exc:
                failures.append(exc)
        shutdown = getattr(self.transport, "shutdown", None)
        if callable(shutdown):
            try:
                result = shutdown()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                caller_cancelled = True
            except Exception as exc:
                failures.append(exc)
        if task is None or task.done():
            self._activation_task = None
        else:
            failures.append(StagingGatewayError("activation task did not stop"))
        if caller_cancelled:
            raise asyncio.CancelledError
        if failures:
            raise StagingGatewayError("gateway component shutdown failed")

    async def _login_page(self, send) -> None:
        challenge = self.login_challenges.create()
        response = HTMLResponse(
            "<!doctype html><html><body><main><h1>Buffalo staging sign in</h1>"
            '<form method="post" action="/auth/login">'
            f'<input type="hidden" name="csrf_token" value="{html.escape(challenge.form_token)}">'
            '<label>Owner passphrase <input type="password" name="passphrase" '
            'autocomplete="current-password" required></label>'
            '<button type="submit">Sign in</button></form></main></body></html>'
        )
        response.set_cookie(
            value=challenge.cookie_token,
            **cookie_settings(LOGIN_CHALLENGE_COOKIE, max_age=5 * 60),
        )
        await self._respond(send, response)

    async def _login(self, *, headers: Mapping[bytes, bytes], body: bytes, send) -> None:
        if self._header(headers, b"origin") != self.config.external_origin:
            await self._login_failed(send)
            return
        if self._content_type(headers) != "application/x-www-form-urlencoded":
            await self._login_failed(send)
            return
        try:
            fields = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=2,
            )
        except (UnicodeDecodeError, ValueError):
            await self._login_failed(send)
            return
        if set(fields) != {"passphrase", "csrf_token"} or any(
            len(values) != 1 for values in fields.values()
        ):
            await self._login_failed(send)
            return
        cookies = self._cookies(headers)
        try:
            reservation = self.authenticator.reserve()
        except (AuthenticationBusy, AuthenticationThrottled):
            matched = False
        else:
            if not self.login_challenges.consume_after_admission(
                cookies.get(LOGIN_CHALLENGE_COOKIE), fields["csrf_token"][0]
            ):
                reservation.discard()
                matched = False
            else:
                task = asyncio.create_task(
                    asyncio.to_thread(reservation.verify, fields["passphrase"][0])
                )
                self._verification_tasks.add(task)
                task.add_done_callback(self._verification_tasks.discard)
                try:
                    matched = await asyncio.shield(task)
                except (AuthenticationBusy, AuthenticationThrottled):
                    matched = False
        if not matched:
            await self._login_failed(send)
            return
        issued = self.sessions.create(
            principal_ref=OWNER_PRINCIPAL_REF,
            role_ref=OWNER_ROLE_REF,
            capabilities=self.route_policy.capabilities,
            credential_fingerprint=self.authenticator.fingerprint,
        )
        response = Response(status_code=303, headers={"location": "/"})
        response.set_cookie(
            value=issued.token,
            **cookie_settings(SESSION_COOKIE, max_age=SESSION_ABSOLUTE_SECONDS),
        )
        response.set_cookie(
            value=issued.csrf_token,
            **cookie_settings(
                CSRF_COOKIE,
                max_age=SESSION_ABSOLUTE_SECONDS,
                httponly=False,
            ),
        )
        response.delete_cookie(
            LOGIN_CHALLENGE_COOKIE,
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
        await self._respond(send, response)

    async def _login_failed(self, send) -> None:
        await self._respond(
            send,
            PlainTextResponse("Authentication failed", status_code=403),
        )

    async def _logout(
        self,
        *,
        headers: Mapping[bytes, bytes],
        session_token: str | None,
        session: StagingSession | None,
        body: bytes,
        send,
    ) -> None:
        if session is None:
            await self._respond(send, PlainTextResponse("Unauthorized", status_code=401))
            return
        self._require_origin_and_csrf(headers, session, body=body)
        self.sessions.destroy(session_token)
        response = Response(status_code=303, headers={"location": "/auth/login"})
        for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
            response.delete_cookie(
                name,
                path="/",
                secure=True,
                httponly=httponly,
                samesite="strict",
            )
        await self._respond(send, response)

    def _session(
        self, headers: Mapping[bytes, bytes]
    ) -> tuple[str | None, StagingSession | None]:
        token = self._cookies(headers).get(SESSION_COOKIE)
        return token, self.sessions.authenticate(
            token, credential_fingerprint=self.authenticator.fingerprint
        )

    def _require_origin_and_csrf(
        self,
        headers: Mapping[bytes, bytes],
        session: StagingSession,
        *,
        body: bytes = b"",
    ) -> None:
        if self._header(headers, b"origin") != self.config.external_origin:
            raise StagingGatewayError("request origin differs")
        supplied_header = self._header(headers, b"x-buffalo-csrf-token")
        supplied_form = self._csrf_form_value(headers, body)
        if supplied_header and supplied_form and not secrets.compare_digest(
            supplied_header, supplied_form
        ):
            raise StagingGatewayError("request CSRF proofs differ")
        supplied = supplied_header or supplied_form
        cookies = self._cookies(headers)
        cookie_value = cookies.get(CSRF_COOKIE)
        if (
            not supplied
            or not cookie_value
            or not secrets.compare_digest(supplied, cookie_value)
            or not self.sessions.validate_csrf(session, supplied)
        ):
            raise StagingGatewayError("request CSRF proof differs")

    async def _proxy(
        self,
        *,
        receive,
        send,
        headers: Mapping[bytes, bytes],
        method: str,
        path: str,
        query: bytes,
        body: bytes,
        request_id: str,
        session: StagingSession,
        selection: RouteSpec,
        expected_generation: int | None,
    ) -> None:
        # Assertion timestamps deliberately use whole seconds.  This is well
        # inside the five-second lifetime and avoids accepting sub-millisecond
        # precision that the canonical assertion format cannot represent.
        issued_at = float(int(self._wall_clock()))
        try:
            worker_key, actual_generation = self.worker_keyring.current(
                selection.worker_role
            )
        except ValueError as exc:
            if selection.worker_role == "research":
                raise ResearchWorkerUnavailable from None
            raise StagingGatewayError("synthetic worker key is unavailable") from exc
        if selection.worker_role == "research":
            if (
                type(expected_generation) is not int
                or actual_generation != expected_generation
            ):
                raise ResearchWorkerUnavailable
        elif expected_generation is not None:
            raise StagingGatewayError("synthetic worker generation is unexpected")
        assertion = mint_assertion(
            key=worker_key,
            worker_role=selection.worker_role,
            request_id=request_id,
            session_digest=session.token_digest_hex,
            principal_ref=session.principal_ref,
            role_ref=session.role_ref,
            capabilities=(selection.capability,),
            method=method,
            path=path,
            query=query,
            body=body,
            origin=self.config.external_origin,
            issued_at=issued_at,
            nonce=secrets.token_hex(32),
        )
        forwarded_headers = tuple(
            (key, value)
            for key, value in headers.items()
            if key in _WORKER_REQUEST_HEADERS
        )
        worker_response = await self.transport.request(
            worker_role=selection.worker_role,
            capability=selection.capability,
            method=method,
            path=path,
            query=query,
            body=body,
            headers=forwarded_headers,
            assertion=assertion,
            issued_at=issued_at,
        )
        await self._send_worker_response(
            receive=receive,
            send=send,
            value=worker_response,
            request_path=path,
            session=session,
            request_headers=headers,
            selection=selection,
        )

    async def _retry_research(
        self,
        *,
        headers: Mapping[bytes, bytes],
        query: bytes,
        receive,
        send,
        session: StagingSession,
    ) -> None:
        self._validate_query(query, frozenset())
        self._require_body_within_limit(headers, limit=1_024)
        body = await self._read_body(receive, limit=1_024)
        self._validate_body_framing(headers, body)
        if (
            "procurement.private_research.read" not in session.capabilities
            or self._content_type(headers) != "application/x-www-form-urlencoded"
        ):
            raise StagingGatewayError("research retry authority differs")
        try:
            fields = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=1,
            )
        except (UnicodeDecodeError, ValueError):
            raise StagingGatewayError("research retry form is invalid") from None
        if set(fields) != {_CSRF_FORM_FIELD} or len(fields[_CSRF_FORM_FIELD]) != 1:
            raise StagingGatewayError("research retry form differs")
        self._require_origin_and_csrf(headers, session, body=body)
        coordinator = self.research_coordinator
        if coordinator is None:
            decision = ResearchDecision(
                state="STOPPING",
                generation=0,
                retry_after_seconds=1,
                routable=False,
            )
        else:
            decision = await coordinator.retry_failed()
        if decision.state == "VALIDATING":
            await self._respond(
                send,
                Response(
                    status_code=303,
                    headers={"location": "/private-research"},
                ),
            )
            return
        if decision.state in {"STOPPED", "STOPPING"}:
            decision = ResearchDecision(
                state="STOPPING",
                generation=decision.generation,
                retry_after_seconds=1,
                routable=False,
            )
        await self._research_unavailable(
            send=send,
            headers=headers,
            session=session,
            decision=decision,
        )

    async def _research_unavailable(
        self,
        *,
        send,
        headers: Mapping[bytes, bytes],
        session: StagingSession,
        decision: ResearchDecision,
    ) -> None:
        response_headers: dict[str, str] = {}
        retry_after = decision.retry_after_seconds
        if decision.state != "FAILED":
            response_headers["retry-after"] = str(retry_after or 1)
        if decision.state == "FAILED":
            csrf_value = self._cookies(headers).get(CSRF_COOKIE, "")
            if csrf_value and self.sessions.validate_csrf(session, csrf_value):
                response = HTMLResponse(
                    "<!doctype html><html><body><main>"
                    "<h1>Private research unavailable</h1>"
                    f'<form method="post" action="{_RESEARCH_RETRY_PATH}">'
                    f'<input type="hidden" name="{_CSRF_FORM_FIELD}" '
                    f'value="{html.escape(csrf_value, quote=True)}">'
                    '<button type="submit">Retry private research</button>'
                    "</form></main></body></html>",
                    status_code=503,
                    headers=response_headers,
                )
            else:
                response = PlainTextResponse(
                    "Private research unavailable",
                    status_code=503,
                    headers=response_headers,
                )
        else:
            response = PlainTextResponse(
                "Private research unavailable",
                status_code=503,
                headers=response_headers,
            )
        await self._respond(send, response)

    def prepare_worker_key(
        self, *, worker_role: str, key: bytes, generation: int
    ) -> None:
        try:
            self.worker_keyring.prepare(
                role=worker_role,
                key=key,
                generation=generation,
            )
        except ValueError as exc:
            raise StagingGatewayError(str(exc)) from exc

    def commit_worker_key(self, *, worker_role: str, generation: int) -> None:
        try:
            self.worker_keyring.commit(role=worker_role, generation=generation)
        except ValueError as exc:
            raise StagingGatewayError(str(exc)) from exc

    def disable_worker_key(self, *, worker_role: str, generation: int) -> None:
        try:
            self.worker_keyring.disable(role=worker_role, generation=generation)
        except ValueError as exc:
            raise StagingGatewayError(str(exc)) from exc

    async def _send_worker_response(
        self,
        *,
        receive,
        send,
        value: WorkerResponse,
        request_path: str,
        session: StagingSession,
        request_headers: Mapping[bytes, bytes],
        selection: RouteSpec,
    ) -> None:
        started = False
        is_stream_shape = bool(
            isinstance(value, WorkerResponse)
            and type(value.body) is not bytes
            and hasattr(value.body, "__aiter__")
        )
        provided_close = (
            value.close
            if isinstance(value, WorkerResponse) and callable(value.close)
            else None
        )
        fallback_close = (
            getattr(value.body, "aclose", None) if is_stream_shape else None
        )
        close = provided_close or (
            fallback_close if callable(fallback_close) else None
        )
        chunk_task: asyncio.Task[bytes] | None = None
        disconnect_task: asyncio.Task[None] | None = None
        try:
            if (
                not isinstance(value, WorkerResponse)
                or type(value.status) is not int
                or value.status < 200
                or value.status > 599
                or not isinstance(value.headers, tuple)
                or len(value.headers) > 16
                or any(
                    not isinstance(item, tuple)
                    or len(item) != 2
                    or not isinstance(item[0], bytes)
                    or not isinstance(item[1], bytes)
                    for item in value.headers
                )
                or not (
                    type(value.body) is bytes
                    or hasattr(value.body, "__aiter__")
                )
                or (
                    type(value.body) is not bytes
                    and not callable(value.close)
                )
                or (value.close is not None and not callable(value.close))
            ):
                raise StagingGatewayError("worker response shape is invalid")

            output: list[tuple[bytes, bytes]] = []
            seen: set[bytes] = set()
            content_type = ""
            declared_length: int | None = None
            for raw_name, raw_value in value.headers:
                name = raw_name.lower()
                raw_header_value = raw_value
                if len(name) > 128 or len(raw_header_value) > 8_192:
                    raise StagingGatewayError("worker response header is invalid")
                if name not in _WORKER_RESPONSE_HEADERS | {b"content-length"}:
                    continue
                if name in seen:
                    raise StagingGatewayError("worker response header is duplicated")
                seen.add(name)
                decoded_value = self._decode_header(raw_header_value)
                name.decode("ascii")
                if name == b"content-type":
                    content_type = decoded_value.split(";", 1)[0].strip().lower()
                elif name == b"content-length":
                    if not decoded_value.isdecimal():
                        raise StagingGatewayError("worker content length is invalid")
                    declared_length = int(decoded_value)
                    continue
                elif name == b"location":
                    normalized = self._normalize_worker_location(
                        request_path=request_path, location=decoded_value
                    )
                    if normalized is None:
                        raise StagingGatewayError("worker redirect is invalid")
                    decoded_value = normalized
                output.append((name, decoded_value.encode("ascii")))

            csrf_value = self._cookies(request_headers).get(CSRF_COOKIE)
            body: bytes | AsyncIterable[bytes] = value.body
            if selection.stream_response != (not isinstance(body, bytes)):
                raise StagingGatewayError("worker response mode differs")
            iterator = None
            if isinstance(body, bytes):
                if not response_metadata_is_allowed(
                    route=selection,
                    path=request_path,
                    content_length=len(body),
                    content_type=content_type,
                ):
                    raise StagingGatewayError("worker response exceeds route limit")
            else:
                iterator = body.__aiter__()
            if (
                isinstance(body, bytes)
                and content_type == "text/html"
                and selection.capability not in {
                    "procurement.evidence.download",
                    "procurement.private_research.download",
                }
            ):
                if not csrf_value or not self.sessions.validate_csrf(
                    session, csrf_value
                ):
                    raise StagingGatewayError("worker HTML session binding differs")
                if selection.worker_role == "synthetic":
                    body = self._strip_legacy_review_token_fields(body)
                body = self._inject_csrf_forms(
                    body,
                    csrf_value,
                    max_bytes=selection.max_response_bytes,
                )

            if isinstance(body, bytes):
                if declared_length is not None and declared_length != len(value.body):
                    raise StagingGatewayError("worker content length differs")
                expected_length = len(body)
            else:
                if declared_length is None:
                    raise StagingGatewayError("worker stream length is absent")
                if not response_metadata_is_allowed(
                    route=selection,
                    path=request_path,
                    content_length=declared_length,
                    content_type=content_type,
                ):
                    raise StagingGatewayError("worker stream length is not allowed")
                expected_length = declared_length
            output.append((b"content-length", str(expected_length).encode("ascii")))
            output.extend(
                (name.encode("ascii"), header_value.encode("ascii"))
                for name, header_value in _SECURITY_HEADERS
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": value.status,
                    "headers": output,
                }
            )
            started = True
            if isinstance(body, bytes):
                await send({"type": "http.response.body", "body": body})
            else:
                delivered = 0
                disconnect_task = asyncio.create_task(
                    self._wait_for_disconnect(receive)
                )
                while True:
                    chunk_task = asyncio.create_task(anext(iterator))
                    done, _ = await asyncio.wait(
                        (chunk_task, disconnect_task),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if disconnect_task in done:
                        disconnect_task.result()
                        chunk_task.cancel()
                        with suppress(asyncio.CancelledError, StopAsyncIteration):
                            await chunk_task
                        chunk_task = None
                        raise StagingGatewayError(
                            "client disconnected during worker stream"
                        )
                    completed_chunk_task = chunk_task
                    chunk_task = None
                    try:
                        raw_chunk = completed_chunk_task.result()
                    except StopAsyncIteration:
                        break
                    if (
                        not isinstance(raw_chunk, bytes)
                        or not raw_chunk
                        or len(raw_chunk) > 1024 * 1024
                    ):
                        raise StagingGatewayError(
                            "worker stream chunk is invalid"
                        )
                    if delivered + len(raw_chunk) > expected_length:
                        raise StagingGatewayError(
                            "worker stream exceeds declared length"
                        )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": raw_chunk,
                            "more_body": True,
                        }
                    )
                    delivered += len(raw_chunk)
                if delivered != expected_length:
                    raise StagingGatewayError("worker stream is truncated")
                await send({"type": "http.response.body", "body": b""})
        except asyncio.CancelledError:
            raise
        except Exception:
            if not started:
                await self._respond(
                    send,
                    PlainTextResponse("Worker response invalid", status_code=502),
                )
            # Once response.start has been emitted, deliberately omit the
            # terminal body frame.  The ASGI server closes the incomplete
            # response instead of manufacturing a second status or a
            # successful truncated artifact.
        finally:
            for task in (chunk_task, disconnect_task):
                if task is None:
                    continue
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            if close is not None:
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    # Cleanup was attempted; never expose a private transport
                    # exception or try to start a second public response.
                    pass

    @staticmethod
    async def _wait_for_disconnect(receive) -> None:
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                return
            if message_type != "http.request":
                raise StagingGatewayError("response-side request stream is invalid")

    @staticmethod
    async def _read_body(receive, *, limit: int) -> bytes:
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                raise StagingGatewayError("request disconnected")
            if message.get("type") != "http.request":
                raise StagingGatewayError("request stream is invalid")
            body.extend(bytes(message.get("body", b"")))
            if len(body) > limit:
                raise StagingGatewayError("request body exceeds route limit")
            if not message.get("more_body", False):
                return bytes(body)

    @staticmethod
    def _validate_query(query: bytes, allowed_fields: frozenset[str]) -> None:
        try:
            validate_route_query(query, allowed_fields)
        except StagingRouteError as exc:
            raise StagingGatewayError(str(exc)) from exc

    def _validate_content_type(
        self, headers: Mapping[bytes, bytes], route: RouteSpec
    ) -> None:
        value = self._content_type(headers)
        if value not in route.allowed_content_types:
            raise StagingGatewayError("request content type is not allowed")

    def _csrf_form_value(
        self,
        headers: Mapping[bytes, bytes],
        body: bytes,
    ) -> str:
        if not body:
            return ""
        content_type = self._content_type(headers)
        if content_type == "application/x-www-form-urlencoded":
            try:
                fields = parse_qs(
                    body.decode("utf-8"),
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=128,
                )
            except (UnicodeDecodeError, ValueError):
                raise StagingGatewayError("request form is invalid") from None
            values = fields.get(_CSRF_FORM_FIELD, [])
            if len(values) > 1:
                raise StagingGatewayError("request CSRF form field is duplicated")
            return values[0] if values else ""
        return ""

    @staticmethod
    def _content_type(headers: Mapping[bytes, bytes]) -> str:
        value = headers.get(b"content-type", b"")
        try:
            return value.decode("ascii").split(";", 1)[0].strip().lower()
        except UnicodeDecodeError:
            return ""

    @staticmethod
    def _cookies(headers: Mapping[bytes, bytes]) -> dict[str, str]:
        raw = headers.get(b"cookie")
        if raw is None:
            return {}
        try:
            value = raw.decode("ascii")
            cookie_names = []
            for item in value.split(";"):
                name, separator, _ = item.strip().partition("=")
                if not separator or not name:
                    raise StagingGatewayError("request cookie is invalid")
                cookie_names.append(name)
            if len(cookie_names) != len(set(cookie_names)):
                raise StagingGatewayError("duplicate request cookie is invalid")
            parsed = SimpleCookie()
            parsed.load(value)
        except (UnicodeDecodeError, AttributeError, CookieError):
            raise StagingGatewayError("request cookie is invalid") from None
        return {name: morsel.value for name, morsel in parsed.items()}

    @staticmethod
    def _validate_body_framing(
        headers: Mapping[bytes, bytes], body: bytes
    ) -> None:
        if b"transfer-encoding" in headers:
            raise StagingGatewayError("transfer-encoded request is not accepted")
        raw_length = headers.get(b"content-length")
        if raw_length is None:
            if body:
                raise StagingGatewayError("request content length is absent")
            return
        try:
            text = raw_length.decode("ascii")
            if not text or not text.isdecimal() or text.startswith("+"):
                raise ValueError
            declared = int(text)
        except (UnicodeDecodeError, ValueError):
            raise StagingGatewayError("request content length is invalid") from None
        if declared != len(body):
            raise StagingGatewayError("request content length differs")

    @staticmethod
    def _require_body_within_limit(
        headers: Mapping[bytes, bytes], *, limit: int
    ) -> None:
        raw_length = headers.get(b"content-length")
        if raw_length is None:
            raise StagingGatewayError("request content length is absent")
        try:
            text = raw_length.decode("ascii")
            if not text or not text.isdecimal() or text.startswith("+"):
                raise ValueError
            declared = int(text)
        except (UnicodeDecodeError, ValueError):
            raise StagingGatewayError("request content length is invalid") from None
        if declared > limit:
            raise StagingGatewayError("request body exceeds route limit")

    @staticmethod
    def _require_zero_body_headers(headers: Mapping[bytes, bytes]) -> None:
        if b"transfer-encoding" in headers:
            raise StagingGatewayError("transfer-encoded request is not accepted")
        raw_length = headers.get(b"content-length")
        if raw_length not in {None, b"0"}:
            raise StagingGatewayError("gateway route request body is not accepted")

    def _normalize_worker_location(
        self, *, request_path: str, location: str
    ) -> str | None:
        if (
            not location
            or "\\" in location
            or "%" in location
            or location.startswith("//")
            or any(ord(character) < 32 or ord(character) == 127 for character in location)
        ):
            return None
        parsed_input = urlsplit(location)
        if parsed_input.scheme or parsed_input.netloc or parsed_input.fragment:
            return None
        normalized = urljoin(request_path, location)
        parsed = urlsplit(normalized)
        if parsed.scheme or parsed.netloc or parsed.fragment or not parsed.path.startswith("/"):
            return None
        try:
            raw_path = parsed.path.encode("ascii", "strict")
            raw_query = parsed.query.encode("ascii", "strict")
        except UnicodeEncodeError:
            return None
        route = self.route_policy.match(
            method="GET", path=parsed.path, raw_path=raw_path
        )
        if route is None:
            return None
        try:
            self._validate_query(raw_query, route.allowed_query_fields)
        except StagingGatewayError:
            return None
        return parsed.path + (f"?{parsed.query}" if parsed.query else "")

    @staticmethod
    def _inject_csrf_forms(
        body: bytes, csrf_value: str, *, max_bytes: int
    ) -> bytes:
        encoded = html.escape(csrf_value, quote=True).encode("ascii")
        hidden = (
            b'<input type="hidden" name="'
            + _CSRF_FORM_FIELD.encode("ascii")
            + b'" value="'
            + encoded
            + b'">'
        )
        injected, count = _POST_FORM.subn(
            lambda match: match.group(1) + hidden, body
        )
        if count > _MAX_HTML_FORMS or len(injected) > max_bytes:
            raise StagingGatewayError("worker HTML form inventory exceeds limit")
        return injected

    @staticmethod
    def _strip_legacy_review_token_fields(body: bytes) -> bytes:
        without_labels = _REVIEW_TOKEN_LABEL.sub(b"", body)
        return _REVIEW_TOKEN_INPUT.sub(b"", without_labels)

    @staticmethod
    def _header(headers: Mapping[bytes, bytes], name: bytes) -> str:
        value = headers.get(name)
        if value is None:
            return ""
        return StagingGateway._decode_header(value)

    @staticmethod
    def _decode_header(value: bytes) -> str:
        try:
            decoded = value.decode("ascii")
        except UnicodeDecodeError:
            raise StagingGatewayError("request header is not ASCII") from None
        if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
            raise StagingGatewayError("request header contains a control character")
        return decoded

    async def _respond(self, send, response: Response) -> None:
        for name, value in _SECURITY_HEADERS:
            response.headers[name] = value
        await response({"type": "http"}, self._empty_receive, send)

    @staticmethod
    async def _empty_receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}
