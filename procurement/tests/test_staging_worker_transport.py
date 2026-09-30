"""Unix-socket staging worker identity and transport boundary tests."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import socket
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import FastAPI, HTTPException, Request
from procurement_os import api as operational_api
from procurement_os.local_access import (
    action_principal,
    authenticated_audit_actor,
    has_capability,
    principal_from_request,
    request_runtime_mode,
)
from procurement_os.staging_internal import mint_assertion
from procurement_os.staging_worker_transport import (
    INTERNAL_ASSERTION_HEADER,
    PEER_CREDENTIALS_EXTENSION,
    PeerCredentialH11Protocol,
    PeerCredentials,
    SocketContract,
    StagingInternalAccessMiddleware,
    StagingWorkerBoundaryError,
    UnixWorkerTransport,
    build_worker_server,
    peer_credentials_from_transport,
    validate_socket_contract,
)
from uvicorn import Config, Server


ORIGIN = "https://buffalo-staging.example.test"
KEY = bytes.fromhex("31" * 32)
OTHER_KEY = bytes.fromhex("42" * 32)
SESSION_DIGEST = "51" * 32


class _CaptureApplication:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, scope, receive, send) -> None:
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        identity = scope.get("state", {}).get("staging_identity")
        request = SimpleNamespace(state=SimpleNamespace(staging_identity=identity))
        self.calls.append(
            {
                "scope": scope,
                "body": bytes(body),
                "identity": identity,
                "principal": principal_from_request(request),
                "action_principal": action_principal(
                    request, "procurement.review.read"
                ),
                "has_capability": has_capability(
                    request, "procurement.review.read"
                ),
                "actor": authenticated_audit_actor("browser-controlled"),
                "runtime_mode": request_runtime_mode(),
            }
        )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": ((b"content-type", b"text/plain"),),
            }
        )
        await send({"type": "http.response.body", "body": b"accepted"})


class StagingInternalAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.peer = PeerCredentials(pid=os.getpid(), uid=os.getuid(), gid=os.getgid())
        self.application = _CaptureApplication()
        self.middleware = StagingInternalAccessMiddleware(
            self.application,
            worker_role="synthetic",
            assertion_key=KEY,
            expected_origin=ORIGIN,
            expected_gateway_peer=self.peer,
            wall_clock=lambda: 1_000.0,
        )

    def _assertion(
        self,
        *,
        method: str = "GET",
        path: str = "/",
        query: bytes = b"",
        body: bytes = b"",
        key: bytes = KEY,
        role: str = "synthetic",
        nonce: str = "61" * 32,
        principal_ref: str = "owner:railway-staging:01",
        role_ref: str = "RAILWAY_STAGING_OWNER",
        capabilities: tuple[str, ...] = ("procurement.review.read",),
    ) -> str:
        return mint_assertion(
            key=key,
            worker_role=role,
            request_id="request-0001",
            session_digest=SESSION_DIGEST,
            principal_ref=principal_ref,
            role_ref=role_ref,
            capabilities=capabilities,
            method=method,
            path=path,
            query=query,
            body=body,
            origin=ORIGIN,
            issued_at=1_000.0,
            nonce=nonce,
        )

    async def _invoke(
        self,
        *,
        token: str | None,
        peer: PeerCredentials | None = None,
        method: str = "GET",
        path: str = "/",
        query: bytes = b"",
        body: bytes = b"",
        extra_headers: tuple[tuple[bytes, bytes], ...] = (),
    ) -> tuple[int, bytes]:
        headers = [(b"host", b"worker.internal")]
        if token is not None:
            headers.append((INTERNAL_ASSERTION_HEADER, token.encode("ascii")))
        if method == "POST":
            headers.extend(
                (
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                )
            )
        headers.extend(extra_headers)
        messages = [
            {"type": "http.request", "body": body, "more_body": False}
        ]
        sent: list[dict[str, object]] = []

        async def receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query,
            "headers": headers,
            "state": {},
            "extensions": (
                {PEER_CREDENTIALS_EXTENSION: (peer or self.peer).as_scope_value()}
                if peer is not None or token is not None
                else {}
            ),
        }
        await self.middleware(scope, receive, send)
        status = next(
            int(message["status"])
            for message in sent
            if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            bytes(message.get("body", b""))
            for message in sent
            if message["type"] == "http.response.body"
        )
        return status, response_body

    async def test_valid_assertion_constructs_fixed_server_owned_context(self):
        status, body = await self._invoke(token=self._assertion())
        self.assertEqual((status, body), (200, b"accepted"))
        call = self.application.calls[-1]
        identity = call["identity"]
        self.assertEqual(identity.worker_role, "synthetic")
        self.assertEqual(identity.principal_ref, "owner:railway-staging:01")
        self.assertEqual(identity.role_ref, "RAILWAY_STAGING_OWNER")
        self.assertEqual(identity.capabilities, frozenset({"procurement.review.read"}))
        self.assertEqual(call["principal"].role_ref, "RAILWAY_STAGING_OWNER")
        self.assertEqual(call["action_principal"].role_ref, "procurement.review.read")
        self.assertTrue(call["has_capability"])
        self.assertEqual(call["actor"], "owner:railway-staging:01")
        self.assertEqual(call["runtime_mode"], "SYNTHETIC_DEMO")
        downstream_headers = dict(call["scope"]["headers"])
        self.assertNotIn(INTERNAL_ASSERTION_HEADER, downstream_headers)
        self.assertEqual(
            authenticated_audit_actor("direct-after-request"),
            "direct-after-request",
        )

    async def test_staging_authentication_hash_is_session_stable(self):
        first_token = self._assertion(nonce="66" * 32)
        await self._invoke(token=first_token)
        first = self.application.calls[-1]["action_principal"].authn_context_sha256
        await self._invoke(token=self._assertion(nonce="67" * 32))
        second = self.application.calls[-1]["action_principal"].authn_context_sha256
        changed_session = mint_assertion(
            key=KEY,
            worker_role="synthetic",
            request_id="request-0002",
            session_digest="52" * 32,
            principal_ref="owner:railway-staging:01",
            role_ref="RAILWAY_STAGING_OWNER",
            capabilities=("procurement.review.read",),
            method="GET",
            path="/",
            query=b"",
            body=b"",
            origin=ORIGIN,
            issued_at=1_000.0,
            nonce="68" * 32,
        )
        await self._invoke(token=changed_session)
        third = self.application.calls[-1]["action_principal"].authn_context_sha256
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    async def test_direct_access_wrong_peer_role_or_key_is_refused(self):
        cases = (
            (None, None),
            (
                self._assertion(nonce="62" * 32),
                PeerCredentials(pid=self.peer.pid + 1, uid=self.peer.uid, gid=self.peer.gid),
            ),
            (self._assertion(role="research", nonce="63" * 32), self.peer),
            (self._assertion(key=OTHER_KEY, nonce="64" * 32), self.peer),
        )
        for token, peer in cases:
            with self.subTest(token=token is not None, peer=peer):
                status, _ = await self._invoke(token=token, peer=peer)
                self.assertEqual(status, 403)
        self.assertEqual(self.application.calls, [])

    async def test_wrong_owner_identity_extra_capability_or_query_is_refused(self):
        cases = (
            self._assertion(
                principal_ref="owner:railway-staging:02", nonce="69" * 32
            ),
            self._assertion(role_ref="OTHER_OWNER", nonce="6a" * 32),
            self._assertion(
                capabilities=(
                    "procurement.review.read",
                    "procurement.order.approve",
                ),
                nonce="6b" * 32,
            ),
        )
        for token in cases:
            with self.subTest(token=token[:20]):
                status, _ = await self._invoke(token=token)
                self.assertEqual(status, 403)
        query_token = self._assertion(query=b"unexpected=1", nonce="6c" * 32)
        status, _ = await self._invoke(token=query_token, query=b"unexpected=1")
        self.assertEqual(status, 403)
        self.assertEqual(self.application.calls, [])

    async def test_duplicate_or_browser_security_headers_are_refused(self):
        for headers in (
            ((INTERNAL_ASSERTION_HEADER, self._assertion(nonce="6d" * 32).encode()),),
            ((b"cookie", b"session=browser"),),
            ((b"authorization", b"Bearer browser"),),
            ((b"x-forwarded-for", b"127.0.0.1"),),
        ):
            with self.subTest(headers=headers[0][0]):
                status, _ = await self._invoke(
                    token=self._assertion(nonce=os.urandom(32).hex()),
                    extra_headers=headers,
                )
                self.assertEqual(status, 403)
        self.assertEqual(self.application.calls, [])

    async def test_replay_or_changed_request_binding_is_refused(self):
        token = self._assertion(body=b"alpha", method="POST", path="/economics/target-cost")
        first, _ = await self._invoke(
            token=token,
            method="POST",
            path="/economics/target-cost",
            body=b"alpha",
        )
        replay, _ = await self._invoke(
            token=token,
            method="POST",
            path="/economics/target-cost",
            body=b"alpha",
        )
        changed, _ = await self._invoke(
            token=self._assertion(
                body=b"alpha",
                method="POST",
                path="/economics/target-cost",
                nonce="65" * 32,
            ),
            method="POST",
            path="/economics/target-cost",
            body=b"beta",
        )
        self.assertEqual(first, 200)
        self.assertEqual((replay, changed), (403, 403))

    async def test_sync_fastapi_handler_receives_staging_context_and_legacy_token_bypass(self):
        application = FastAPI()
        should_fail = [False]

        @application.post("/monday-runs/prepare")
        def prepare(request: Request):
            operational_api._require_review_token("")
            actor = authenticated_audit_actor("browser-controlled")
            principal = action_principal(request, "procurement.order.approve")
            if should_fail[0]:
                raise RuntimeError("downstream failure")
            return {"actor": actor, "role": principal.role_ref}

        middleware = StagingInternalAccessMiddleware(
            application,
            worker_role="synthetic",
            assertion_key=KEY,
            expected_origin=ORIGIN,
            expected_gateway_peer=self.peer,
            wall_clock=lambda: 1_000.0,
        )

        async def invoke(nonce: str) -> tuple[int, bytes]:
            body = b"review_token="
            token = mint_assertion(
                key=KEY,
                worker_role="synthetic",
                request_id=f"sync-{nonce[:4]}",
                session_digest=SESSION_DIGEST,
                principal_ref="owner:railway-staging:01",
                role_ref="RAILWAY_STAGING_OWNER",
                capabilities=("procurement.order.approve",),
                method="POST",
                path="/monday-runs/prepare",
                query=b"",
                body=body,
                origin=ORIGIN,
                issued_at=1_000.0,
                nonce=nonce,
            )
            messages = [{"type": "http.request", "body": body, "more_body": False}]
            sent: list[dict[str, object]] = []

            async def receive():
                return messages.pop(0) if messages else {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            await middleware(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "scheme": "http",
                    "method": "POST",
                    "path": "/monday-runs/prepare",
                    "raw_path": b"/monday-runs/prepare",
                    "query_string": b"",
                    "root_path": "",
                    "server": ("worker.internal", 80),
                    "client": None,
                    "headers": [
                        (b"host", b"worker.internal"),
                        (b"content-type", b"application/x-www-form-urlencoded"),
                        (b"content-length", str(len(body)).encode("ascii")),
                        (INTERNAL_ASSERTION_HEADER, token.encode("ascii")),
                    ],
                    "state": {},
                    "extensions": {
                        PEER_CREDENTIALS_EXTENSION: self.peer.as_scope_value()
                    },
                },
                receive,
                send,
            )
            status = next(
                int(message["status"])
                for message in sent
                if message["type"] == "http.response.start"
            )
            response = b"".join(
                bytes(message.get("body", b""))
                for message in sent
                if message["type"] == "http.response.body"
            )
            return status, response

        status, body = await invoke("91" * 32)
        self.assertEqual(status, 200)
        self.assertIn(b'"actor":"owner:railway-staging:01"', body)
        self.assertIn(b'"role":"procurement.order.approve"', body)
        should_fail[0] = True
        with self.assertRaisesRegex(RuntimeError, "downstream failure"):
            await invoke("92" * 32)
        self.assertEqual(authenticated_audit_actor("after-failure"), "after-failure")

    def test_legacy_review_token_remains_required_without_staging_context(self):
        with mock.patch.dict(
            os.environ, {"RECONCILIATION_REVIEW_TOKEN": ""}, clear=False
        ):
            with self.assertRaises(HTTPException) as caught:
                operational_api._require_review_token("")
        self.assertEqual(caught.exception.status_code, 503)


class SocketContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="buffalo-worker-socket-")
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.parent.chmod(0o750)
        self.path = self.parent / "worker.sock"
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.socket.close)
        self.socket.bind(str(self.path))
        self.path.chmod(0o660)
        self.contract = SocketContract(
            path=self.path,
            parent_uid=os.getuid(),
            parent_gid=os.getgid(),
            socket_uid=os.getuid(),
            socket_gid=os.getgid(),
        )

    def test_exact_private_unix_socket_contract_passes(self):
        validate_socket_contract(self.contract)
        info = self.path.stat(follow_symlinks=False)
        self.assertTrue(stat.S_ISSOCK(info.st_mode))

    def test_wrong_mode_owner_type_or_symlink_is_refused(self):
        self.path.chmod(0o600)
        with self.assertRaises(StagingWorkerBoundaryError):
            validate_socket_contract(self.contract)
        self.path.unlink()
        self.path.write_bytes(b"not a socket")
        self.path.chmod(0o660)
        with self.assertRaises(StagingWorkerBoundaryError):
            validate_socket_contract(self.contract)


class _SocketTransport:
    def __init__(self, value: socket.socket) -> None:
        self.value = value

    def get_extra_info(self, name: str):
        return self.value if name == "socket" else None


class UnixWorkerTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="buffalo-worker-client-")
        self.addAsyncCleanup(self._cleanup)
        self.parent = Path(self.temporary.name)
        self.parent.chmod(0o750)
        self.requests: list[bytes] = []
        self.servers: list[asyncio.AbstractServer] = []
        contracts: dict[str, SocketContract] = {}
        for role in ("synthetic", "research"):
            path = self.parent / f"{role}.sock"
            server = await asyncio.start_unix_server(self._handle, path=str(path))
            self.servers.append(server)
            path.chmod(0o660)
            contracts[role] = SocketContract(
                path=path,
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
        self.transport = UnixWorkerTransport(endpoints=contracts)

    async def _cleanup(self) -> None:
        for server in self.servers:
            server.close()
            await server.wait_closed()
        self.temporary.cleanup()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            header_lines = request.split(b"\r\n")
            lengths = [
                int(line.split(b":", 1)[1].strip())
                for line in header_lines
                if line.lower().startswith(b"content-length:")
            ]
            if lengths and lengths[0]:
                request += await reader.readexactly(lengths[0])
            self.requests.append(request)
            response_body = b"worker-ok"
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                + f"Content-Length: {len(response_body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + response_body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_exact_socket_request_forwards_only_bound_assertion(self):
        assertion = mint_assertion(
            key=KEY,
            worker_role="synthetic",
            request_id="request-transport",
            session_digest=SESSION_DIGEST,
            principal_ref="owner:railway-staging:01",
            role_ref="RAILWAY_STAGING_OWNER",
            capabilities=("procurement.review.read",),
            method="GET",
            path="/",
            query=b"",
            body=b"",
            origin=ORIGIN,
            issued_at=float(int(time.time())),
            nonce="71" * 32,
        )
        result = await self.transport.request(
            worker_role="synthetic",
            capability="procurement.review.read",
            method="GET",
            path="/",
            query=b"",
            body=b"",
            headers=((b"accept", b"text/html"),),
            assertion=assertion,
            issued_at=time.time(),
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, b"worker-ok")
        self.assertIsNone(result.close)
        raw = self.requests[-1].lower()
        self.assertIn(INTERNAL_ASSERTION_HEADER + b":", raw)
        self.assertIn(b"accept: text/html", raw)
        self.assertNotIn(b"cookie:", raw)

    def test_peer_credentials_come_from_af_unix_socket(self):
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        value = peer_credentials_from_transport(_SocketTransport(left))
        self.assertEqual((value.pid, value.uid, value.gid), (os.getpid(), os.getuid(), os.getgid()))

    def test_tcp_socket_cannot_supply_worker_peer_credentials(self):
        value = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(value.close)
        with self.assertRaises(StagingWorkerBoundaryError):
            peer_credentials_from_transport(_SocketTransport(value))


class UnixWorkerTransportConstructionTests(unittest.TestCase):
    def test_worker_endpoints_must_be_distinct(self):
        path = Path("/private/runtime/worker.sock")
        contract = SocketContract(
            path=path,
            parent_uid=1,
            parent_gid=2,
            socket_uid=3,
            socket_gid=4,
        )
        with self.assertRaises(StagingWorkerBoundaryError):
            UnixWorkerTransport(endpoints={"synthetic": contract, "research": contract})

    def test_worker_server_requires_exact_inherited_socket_contract(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-fd-") as raw:
            parent = Path(raw)
            parent.chmod(0o750)
            path = parent / "worker.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(listener.close)
            listener.bind(str(path))
            listener.listen(1)
            path.chmod(0o660)
            queued_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(queued_client.close)
            queued_client.connect(str(path))
            contract = SocketContract(
                path=path,
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            with mock.patch(
                "procurement_os.staging_worker_transport.load_worker_application",
                return_value=_CaptureApplication(),
            ):
                server = build_worker_server(
                    worker_role="synthetic",
                    listen_fd=listener.fileno(),
                    socket_contract=contract,
                )
            self.assertEqual(server.config.fd, listener.fileno())
            wrong = SocketContract(
                path=parent / "different.sock",
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            with self.assertRaises(StagingWorkerBoundaryError):
                build_worker_server(
                    worker_role="synthetic",
                    listen_fd=listener.fileno(),
                    socket_contract=wrong,
                )

    def test_worker_server_rejects_bound_socket_that_is_not_listening(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-bound-") as raw:
            parent = Path(raw)
            parent.chmod(0o750)
            path = parent / "worker.sock"
            bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(bound.close)
            bound.bind(str(path))
            path.chmod(0o660)
            contract = SocketContract(
                path=path,
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            with self.assertRaisesRegex(
                StagingWorkerBoundaryError, "listen socket differs"
            ):
                build_worker_server(
                    worker_role="synthetic",
                    listen_fd=bound.fileno(),
                    socket_contract=contract,
                )

    def test_worker_server_binds_descriptor_to_unique_current_endpoint(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-replaced-") as raw:
            parent = Path(raw)
            parent.chmod(0o750)
            path = parent / "worker.sock"
            old_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(old_listener.close)
            self.addCleanup(replacement.close)
            old_listener.bind(str(path))
            old_listener.listen(1)
            path.unlink()
            replacement.bind(str(path))
            replacement.listen(1)
            path.chmod(0o660)
            contract = SocketContract(
                path=path,
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            with self.assertRaisesRegex(
                StagingWorkerBoundaryError, "listen socket endpoint differs"
            ):
                build_worker_server(
                    worker_role="synthetic",
                    listen_fd=old_listener.fileno(),
                    socket_contract=contract,
                )

    def test_worker_server_rejects_bound_only_path_replacement(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-bound-replace-") as raw:
            parent = Path(raw)
            parent.chmod(0o750)
            path = parent / "worker.sock"
            old_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(old_listener.close)
            self.addCleanup(replacement.close)
            old_listener.bind(str(path))
            old_listener.listen(1)
            path.unlink()
            replacement.bind(str(path))
            path.chmod(0o660)
            contract = SocketContract(
                path=path,
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            with self.assertRaisesRegex(
                StagingWorkerBoundaryError, "listen socket endpoint differs"
            ):
                build_worker_server(
                    worker_role="synthetic",
                    listen_fd=old_listener.fileno(),
                    socket_contract=contract,
                )


class RealUdsPeerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_programmatic_uvicorn_injects_kernel_peer_credentials(self):
        temporary = tempfile.TemporaryDirectory(prefix="buffalo-uvicorn-uds-")
        self.addAsyncCleanup(asyncio.to_thread, temporary.cleanup)
        parent = Path(temporary.name)
        parent.chmod(0o750)
        synthetic_path = parent / "synthetic.sock"
        research_path = parent / "research.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dummy = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        self.addCleanup(dummy.close)
        listener.bind(str(synthetic_path))
        dummy.bind(str(research_path))
        synthetic_path.chmod(0o660)
        research_path.chmod(0o660)
        application = _CaptureApplication()
        protected = StagingInternalAccessMiddleware(
            application,
            worker_role="synthetic",
            assertion_key=KEY,
            expected_origin=ORIGIN,
            expected_gateway_peer=PeerCredentials(
                pid=os.getpid(), uid=os.getuid(), gid=os.getgid()
            ),
        )
        config = Config(
            protected,
            http=PeerCredentialH11Protocol,
            proxy_headers=False,
            access_log=False,
            server_header=False,
            lifespan="off",
            ws="none",
            log_config=None,
            log_level="critical",
        )
        server = Server(config)
        server.install_signal_handlers = lambda: None
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        self.addAsyncCleanup(self._stop_server, server, server_task)
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(server.started)
        contracts = {
            role: SocketContract(
                path=path,
                parent_uid=os.getuid(),
                parent_gid=os.getgid(),
                socket_uid=os.getuid(),
                socket_gid=os.getgid(),
            )
            for role, path in (
                ("synthetic", synthetic_path),
                ("research", research_path),
            )
        }
        transport = UnixWorkerTransport(endpoints=contracts)
        issued_at = float(int(time.time()))
        assertion = mint_assertion(
            key=KEY,
            worker_role="synthetic",
            request_id="real-uds-request",
            session_digest=SESSION_DIGEST,
            principal_ref="owner:railway-staging:01",
            role_ref="RAILWAY_STAGING_OWNER",
            capabilities=("procurement.review.read",),
            method="GET",
            path="/",
            query=b"",
            body=b"",
            origin=ORIGIN,
            issued_at=issued_at,
            nonce="81" * 32,
        )
        response = await transport.request(
            worker_role="synthetic",
            capability="procurement.review.read",
            method="GET",
            path="/",
            query=b"",
            body=b"",
            headers=((b"accept", b"text/plain"),),
            assertion=assertion,
            issued_at=issued_at,
        )
        self.assertEqual((response.status, response.body), (200, b"accepted"))
        self.assertEqual(len(application.calls), 1)

    @staticmethod
    async def _stop_server(server: Server, task: asyncio.Task) -> None:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)


if __name__ == "__main__":
    unittest.main()
