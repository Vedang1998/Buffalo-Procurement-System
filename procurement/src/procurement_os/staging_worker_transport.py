"""Authenticated Unix-socket boundary for Railway staging workers."""
from __future__ import annotations

import asyncio
import hmac
import importlib
import os
from pathlib import Path
import socket
import time
from typing import Any, Callable

import httpx
from starlette.responses import PlainTextResponse
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn import Config, Server

from .local_access import (
    bind_staging_request_identity,
    reset_request_identity,
)
from .staging_internal import (
    AssertionError as InternalAssertionError,
    NonceReplayCache,
    verify_assertion,
)
from .staging_routes import StagingRouteError, match_route, validate_route_query
from .staging_identity import (
    OWNER_PRINCIPAL_REF,
    OWNER_ROLE_REF,
    StagingRequestIdentity,
)
from .staging_worker_types import WorkerResponse
from .staging_uds import (
    PeerCredentials,
    SocketContract,
    StagingUdsError,
    peer_credentials_from_transport as _peer_credentials_from_transport,
    validate_socket_contract as _validate_socket_contract,
)


INTERNAL_ASSERTION_HEADER = b"x-buffalo-internal-assertion"
PEER_CREDENTIALS_EXTENSION = "buffalo.staging.peer_credentials.v1"
_MAX_ASSERTION_HEADER_BYTES = 8_192
_INTERNAL_HOST = b"worker.internal"
_ALLOWED_REQUEST_HEADERS = frozenset(
    {
        b"host",
        b"accept",
        b"content-type",
        b"content-length",
        INTERNAL_ASSERTION_HEADER,
    }
)


class StagingWorkerBoundaryError(ValueError):
    """The private worker transport or request boundary differs."""


def validate_socket_contract(contract: SocketContract) -> None:
    try:
        _validate_socket_contract(contract)
    except StagingUdsError as exc:
        raise StagingWorkerBoundaryError(str(exc)) from exc


class StagingInternalAccessMiddleware:
    """Verify the Unix peer and one short-lived gateway assertion per request."""

    def __init__(
        self,
        app: Any,
        *,
        worker_role: str,
        assertion_key: bytes,
        expected_origin: str,
        expected_gateway_peer: PeerCredentials,
        wall_clock: Callable[[], float] = time.time,
        nonce_cache: NonceReplayCache | None = None,
    ) -> None:
        if worker_role not in {"synthetic", "research"}:
            raise StagingWorkerBoundaryError("worker role is invalid")
        if not isinstance(assertion_key, bytes) or len(assertion_key) != 32:
            raise StagingWorkerBoundaryError("worker assertion key is invalid")
        self.app = app
        self.worker_role = worker_role
        self.assertion_key = assertion_key
        self.expected_origin = expected_origin
        self.expected_gateway_peer = expected_gateway_peer
        self.wall_clock = wall_clock
        self.nonce_cache = nonce_cache or NonceReplayCache()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope_type != "http":
            return
        try:
            peer = PeerCredentials.from_scope_value(
                (scope.get("extensions") or {}).get(PEER_CREDENTIALS_EXTENSION)
            )
            if not hmac.compare_digest(
                f"{peer.pid}:{peer.uid}:{peer.gid}",
                (
                    f"{self.expected_gateway_peer.pid}:"
                    f"{self.expected_gateway_peer.uid}:"
                    f"{self.expected_gateway_peer.gid}"
                ),
            ):
                raise StagingWorkerBoundaryError("worker peer credentials differ")
            method = scope.get("method")
            path = scope.get("path")
            raw_path = scope.get("raw_path", b"")
            query = scope.get("query_string", b"")
            if not isinstance(method, str) or method != method.upper():
                raise StagingWorkerBoundaryError("worker request method is invalid")
            if not isinstance(path, str) or not isinstance(raw_path, bytes):
                raise StagingWorkerBoundaryError("worker request path is invalid")
            if not isinstance(query, bytes):
                raise StagingWorkerBoundaryError("worker request query is invalid")
            route = match_route(method=method, path=path, raw_path=raw_path)
            if route is None or route.worker_role != self.worker_role:
                raise StagingWorkerBoundaryError("worker route is not allowed")
            validate_route_query(query, route.allowed_query_fields)
            raw_headers = self._validated_headers(scope.get("headers") or ())
            grouped: dict[bytes, bytes] = dict(raw_headers)
            assertion_values = [value for name, value in raw_headers if name == INTERNAL_ASSERTION_HEADER]
            if len(assertion_values) != 1:
                raise StagingWorkerBoundaryError("worker assertion header differs")
            raw_assertion = assertion_values[0]
            if (
                not isinstance(raw_assertion, bytes)
                or not raw_assertion
                or len(raw_assertion) > _MAX_ASSERTION_HEADER_BYTES
            ):
                raise StagingWorkerBoundaryError("worker assertion header is invalid")
            try:
                assertion = raw_assertion.decode("ascii")
            except UnicodeDecodeError:
                raise StagingWorkerBoundaryError(
                    "worker assertion header is invalid"
                ) from None
            declared_length = self._declared_length(grouped)
            if declared_length > route.body_limit:
                raise StagingWorkerBoundaryError("worker request body exceeds limit")
            content_type = grouped.get(b"content-type", b"")
            try:
                media_type = content_type.decode("ascii").split(";", 1)[0].strip().lower()
            except UnicodeDecodeError:
                raise StagingWorkerBoundaryError(
                    "worker request content type is invalid"
                ) from None
            if method == "POST" and media_type not in route.allowed_content_types:
                raise StagingWorkerBoundaryError(
                    "worker request content type is not allowed"
                )
            if method == "GET" and (declared_length or content_type):
                raise StagingWorkerBoundaryError("worker safe request carries a body")
            body = await self._read_body(receive, limit=route.body_limit)
            if len(body) != declared_length:
                raise StagingWorkerBoundaryError("worker request length differs")
            verified = verify_assertion(
                token=assertion,
                key=self.assertion_key,
                expected_worker_role=self.worker_role,
                method=method,
                path=path,
                query=query,
                body=body,
                expected_origin=self.expected_origin,
                now=float(self.wall_clock()),
                nonce_cache=self.nonce_cache,
            )
            if (
                verified.principal_ref != OWNER_PRINCIPAL_REF
                or verified.role_ref != OWNER_ROLE_REF
                or verified.capabilities != frozenset({route.capability})
            ):
                raise StagingWorkerBoundaryError("worker capability binding differs")
            identity = StagingRequestIdentity(
                session_digest=verified.session_digest,
                principal_ref=verified.principal_ref,
                role_ref=verified.role_ref,
                capabilities=verified.capabilities,
                worker_role=verified.worker_role,
            )
            identity.validate()
        except (
            InternalAssertionError,
            StagingRouteError,
            StagingUdsError,
            StagingWorkerBoundaryError,
            ValueError,
        ):
            await self._refuse(send)
            return

        filtered_scope = dict(scope)
        filtered_scope["headers"] = [
            (name, value)
            for name, value in raw_headers
            if not (isinstance(name, bytes) and name.lower() == INTERNAL_ASSERTION_HEADER)
        ]
        state = dict(scope.get("state") or {})
        state["staging_identity"] = identity
        filtered_scope["state"] = state
        delivered = False

        async def replay_receive() -> dict[str, object]:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        token = bind_staging_request_identity(identity)
        try:
            await self.app(filtered_scope, replay_receive, send)
        finally:
            reset_request_identity(token)

    @staticmethod
    async def _read_body(receive: Any, *, limit: int) -> bytes:
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                raise StagingWorkerBoundaryError("worker request stream is invalid")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise StagingWorkerBoundaryError("worker request body is invalid")
            body.extend(chunk)
            if len(body) > limit:
                raise StagingWorkerBoundaryError("worker request body exceeds limit")
            if not message.get("more_body", False):
                return bytes(body)

    @staticmethod
    def _validated_headers(raw_headers: object) -> tuple[tuple[bytes, bytes], ...]:
        if not isinstance(raw_headers, (tuple, list)) or len(raw_headers) > 8:
            raise StagingWorkerBoundaryError("worker request header inventory differs")
        normalized: list[tuple[bytes, bytes]] = []
        names: list[bytes] = []
        for item in raw_headers:
            if (
                not isinstance(item, (tuple, list))
                or len(item) != 2
                or not isinstance(item[0], bytes)
                or not isinstance(item[1], bytes)
            ):
                raise StagingWorkerBoundaryError("worker request header is invalid")
            name = item[0].lower()
            value = item[1]
            if (
                name not in _ALLOWED_REQUEST_HEADERS
                or not value
                or len(name) > 64
                or len(value) > _MAX_ASSERTION_HEADER_BYTES
                or b"\x00" in value
                or b"\r" in value
                or b"\n" in value
            ):
                raise StagingWorkerBoundaryError("worker request header is not allowed")
            normalized.append((name, value))
            names.append(name)
        if len(names) != len(set(names)) or names.count(b"host") != 1:
            raise StagingWorkerBoundaryError("worker singleton header inventory differs")
        if dict(normalized)[b"host"] != _INTERNAL_HOST:
            raise StagingWorkerBoundaryError("worker internal host differs")
        return tuple(normalized)

    @staticmethod
    def _declared_length(headers: dict[bytes, bytes]) -> int:
        value = headers.get(b"content-length")
        if value is None:
            return 0
        try:
            text = value.decode("ascii")
            if not text or not text.isdecimal() or text.startswith("+"):
                raise ValueError
            result = int(text)
        except (UnicodeDecodeError, ValueError):
            raise StagingWorkerBoundaryError(
                "worker request content length is invalid"
            ) from None
        return result

    @staticmethod
    async def _refuse(send: Any) -> None:
        response = PlainTextResponse(
            "Internal worker request refused",
            status_code=403,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
        await response({"type": "http"}, None, send)


def peer_credentials_from_transport(transport: asyncio.Transport) -> PeerCredentials:
    try:
        return _peer_credentials_from_transport(transport)
    except StagingUdsError as exc:
        raise StagingWorkerBoundaryError(str(exc)) from exc


class PeerCredentialH11Protocol(H11Protocol):
    """Inject Linux SO_PEERCRED into each ASGI request on one UDS connection."""

    def connection_made(self, transport: asyncio.Transport) -> None:
        try:
            peer = peer_credentials_from_transport(transport)
        except StagingWorkerBoundaryError:
            transport.close()
            return
        application = self.app

        async def peer_bound_application(scope, receive, send):
            bound_scope = dict(scope)
            extensions = dict(scope.get("extensions") or {})
            extensions[PEER_CREDENTIALS_EXTENSION] = peer.as_scope_value()
            bound_scope["extensions"] = extensions
            await application(bound_scope, receive, send)

        self.app = peer_bound_application
        super().connection_made(transport)


def load_worker_application(worker_role: str) -> Any:
    modules = {
        "synthetic": "procurement_os.api",
        "research": "procurement_os.private_research_app",
    }
    try:
        module_name = modules[worker_role]
    except KeyError:
        raise StagingWorkerBoundaryError("worker application role is invalid") from None
    module = importlib.import_module(module_name)
    if getattr(module, "_ACCESS_BOUNDARY", None) != "RAILWAY_STAGING_INTERNAL":
        raise StagingWorkerBoundaryError("worker application boundary differs")
    application = getattr(module, "app", None)
    if application is None:
        raise StagingWorkerBoundaryError("worker application is absent")
    return application


def _require_listener_endpoint_binding(listen_fd: int, path: Path) -> None:
    """Bind a Linux socket descriptor to the one current pathname endpoint.

    AF_UNIX uses a sockfs inode for the open descriptor and a different VFS
    inode for the filesystem node, so fstat/lstat inode equality is not a
    valid proof.  Linux exposes the endpoint inode and bound pathname together
    in the calling network namespace through /proc/self/net/unix.
    """

    try:
        descriptor_inode = os.fstat(listen_fd).st_ino
        rows = Path("/proc/self/net/unix").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise StagingWorkerBoundaryError(
            "worker listen socket endpoint is unavailable"
        ) from exc
    matches: list[int] = []
    for row in rows[1:]:
        fields = row.split(maxsplit=7)
        if len(fields) != 8 or fields[7] != str(path):
            continue
        try:
            flags = int(fields[3], 16)
            endpoint_type = int(fields[4], 16)
            state = int(fields[5], 16)
            inode = int(fields[6], 10)
        except ValueError as exc:
            raise StagingWorkerBoundaryError(
                "worker listen socket endpoint differs"
            ) from exc
        # Queued/connected peers can repeat the listener pathname with state
        # 02 and inode zero.  Only the bound listening endpoints participate
        # in the exact descriptor/path proof.
        if state == 0x01:
            if endpoint_type == socket.SOCK_STREAM and (flags & 0x00010000):
                matches.append(inode)
            else:
                # A second merely-bound endpoint at the same pathname steals
                # new connects even though the inherited old listener remains
                # visible.  Refuse that ambiguous/unreachable state.
                raise StagingWorkerBoundaryError(
                    "worker listen socket endpoint differs"
                )
        elif state not in {0x02, 0x03, 0x04}:
            raise StagingWorkerBoundaryError(
                "worker listen socket endpoint differs"
            )
    if matches != [descriptor_inode]:
        raise StagingWorkerBoundaryError("worker listen socket endpoint differs")


def build_worker_server(
    *, worker_role: str, listen_fd: int, socket_contract: SocketContract
) -> Server:
    if type(listen_fd) is not int or listen_fd < 3:
        raise StagingWorkerBoundaryError("worker listen descriptor is invalid")
    validate_socket_contract(socket_contract)
    try:
        duplicate = socket.fromfd(listen_fd, socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as exc:
        raise StagingWorkerBoundaryError("worker listen descriptor is unavailable") from exc
    try:
        if (
            duplicate.family != socket.AF_UNIX
            or duplicate.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
            != socket.SOCK_STREAM
            or duplicate.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1
        ):
            raise StagingWorkerBoundaryError("worker listen socket differs")
        bound_path = duplicate.getsockname()
        if (
            not isinstance(bound_path, str)
            or Path(bound_path) != socket_contract.path
        ):
            raise StagingWorkerBoundaryError("worker listen socket path is invalid")
        _require_listener_endpoint_binding(listen_fd, socket_contract.path)
    except OSError as exc:
        raise StagingWorkerBoundaryError("worker listen socket differs") from exc
    finally:
        duplicate.close()
    application = load_worker_application(worker_role)
    config = Config(
        application,
        fd=listen_fd,
        http=PeerCredentialH11Protocol,
        ws="none",
        lifespan="on",
        proxy_headers=False,
        access_log=False,
        server_header=False,
        date_header=False,
        workers=1,
    )
    return Server(config)


def run_worker(
    *, worker_role: str, listen_fd: int, socket_contract: SocketContract
) -> None:
    build_worker_server(
        worker_role=worker_role,
        listen_fd=listen_fd,
        socket_contract=socket_contract,
    ).run()


class UnixWorkerTransport:
    """One-request UDS client with exact socket and response-size checks."""

    def __init__(
        self,
        *,
        endpoints: dict[str, SocketContract],
        timeout: httpx.Timeout | None = None,
    ) -> None:
        if set(endpoints) != {"synthetic", "research"}:
            raise StagingWorkerBoundaryError("worker endpoint inventory differs")
        if len({contract.path for contract in endpoints.values()}) != 2:
            raise StagingWorkerBoundaryError("worker endpoints are not isolated")
        self.endpoints = dict(endpoints)
        self.timeout = timeout or httpx.Timeout(
            connect=5.0,
            read=60.0,
            write=30.0,
            pool=5.0,
        )

    async def request(self, **request: object) -> WorkerResponse:
        worker_role = request.get("worker_role")
        capability = request.get("capability")
        method = request.get("method")
        path = request.get("path")
        query = request.get("query")
        body = request.get("body")
        headers = request.get("headers")
        assertion = request.get("assertion")
        if (
            worker_role not in self.endpoints
            or not isinstance(capability, str)
            or not isinstance(method, str)
            or method != method.upper()
            or not isinstance(path, str)
            or not isinstance(query, bytes)
            or not isinstance(body, bytes)
            or not isinstance(headers, tuple)
            or not isinstance(assertion, str)
        ):
            raise StagingWorkerBoundaryError("worker transport request is invalid")
        try:
            raw_path = path.encode("ascii")
            assertion_value = assertion.encode("ascii")
        except UnicodeEncodeError:
            raise StagingWorkerBoundaryError(
                "worker transport request is invalid"
            ) from None
        route = match_route(method=method, path=path, raw_path=raw_path)
        if (
            route is None
            or route.worker_role != worker_role
            or route.capability != capability
            or len(body) > route.body_limit
        ):
            raise StagingWorkerBoundaryError("worker transport route differs")
        try:
            validate_route_query(query, route.allowed_query_fields)
        except StagingRouteError as exc:
            raise StagingWorkerBoundaryError(str(exc)) from exc
        forwarded: list[tuple[bytes, bytes]] = []
        for item in headers:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(isinstance(value, bytes) for value in item)
            ):
                raise StagingWorkerBoundaryError("worker transport header is invalid")
            name, value = item
            if name.lower() not in {b"accept", b"content-type"}:
                raise StagingWorkerBoundaryError("worker transport header is not allowed")
            forwarded.append((name.lower(), value))
        if any(name == INTERNAL_ASSERTION_HEADER for name, _ in forwarded):
            raise StagingWorkerBoundaryError("worker assertion header was supplied")
        forwarded.append((INTERNAL_ASSERTION_HEADER, assertion_value))
        contract = self.endpoints[worker_role]
        validate_socket_contract(contract)
        transport = httpx.AsyncHTTPTransport(
            uds=str(contract.path),
            retries=0,
        )
        client = httpx.AsyncClient(
            transport=transport,
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        response: httpx.Response | None = None
        try:
            url = httpx.URL(scheme="http", host="worker.internal", path=path)
            if query:
                url = url.copy_with(query=query)
            expected_raw_path = raw_path + (b"?" + query if query else b"")
            if url.raw_path != expected_raw_path:
                raise StagingWorkerBoundaryError("worker transport URL was normalized")
            prepared = httpx.Request(
                method,
                url,
                headers=forwarded,
                content=body,
            )
            response = await client.send(prepared, stream=True)
            response_headers = tuple(response.headers.raw)
            if route.stream_response:
                closed = False

                async def close() -> None:
                    nonlocal closed
                    if closed:
                        return
                    closed = True
                    await response.aclose()
                    await client.aclose()

                return WorkerResponse(
                    status=response.status_code,
                    headers=response_headers,
                    body=response.aiter_raw(),
                    close=close,
                )
            chunks = bytearray()
            async for chunk in response.aiter_raw():
                chunks.extend(chunk)
                if len(chunks) > route.max_response_bytes:
                    raise StagingWorkerBoundaryError(
                        "worker buffered response exceeds route limit"
                    )
            result = WorkerResponse(
                status=response.status_code,
                headers=response_headers,
                body=bytes(chunks),
            )
            await response.aclose()
            await client.aclose()
            return result
        except BaseException:
            if response is not None:
                await response.aclose()
            await client.aclose()
            raise


__all__ = [
    "INTERNAL_ASSERTION_HEADER",
    "PEER_CREDENTIALS_EXTENSION",
    "PeerCredentialH11Protocol",
    "PeerCredentials",
    "SocketContract",
    "StagingInternalAccessMiddleware",
    "StagingWorkerBoundaryError",
    "UnixWorkerTransport",
    "build_worker_server",
    "load_worker_application",
    "peer_credentials_from_transport",
    "validate_socket_contract",
    "run_worker",
]
