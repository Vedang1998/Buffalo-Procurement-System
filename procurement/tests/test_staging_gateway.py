"""Public HTTPS gateway authentication, routing, and request-boundary tests."""
from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from procurement_os.staging_access import (
    AuthenticationThrottled,
    CSRF_COOKIE,
    LOGIN_CHALLENGE_COOKIE,
    SESSION_COOKIE,
    generate_owner_credential,
)
from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID,
    EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID,
    EXPECTED_PROJECT_ID,
    StagingConfig,
)
from procurement_os.staging_gateway import (
    RegisteredRoutePolicy,
    StagingGateway,
    StagingGatewayError,
    WorkerResponse,
)
from procurement_os.staging_internal import NonceReplayCache, verify_assertion
from procurement_os.staging_routes import response_metadata_is_allowed


HOST = "buffalo-staging.example.test"
ORIGIN = f"https://{HOST}"
MAPPING_CANDIDATE = "00000000-0000-4000-8000-000000000001"


class _RecordingTransport:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.response = WorkerResponse(
            status=200,
            headers=((b"content-type", b"text/plain; charset=utf-8"),),
            body=b"worker response",
        )
        self.error: Exception | None = None
        self.started = 0
        self.stopped = 0

    async def startup(self):
        self.started += 1

    async def shutdown(self):
        self.stopped += 1

    async def request(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


class StagingGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credential = generate_owner_credential()
        self.config = StagingConfig(
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
            replica_id="replica-01",
            external_host=HOST,
            external_origin=ORIGIN,
            port=8080,
            volume_root=Path("/data"),
            expected_source_commit="a" * 40,
            owner_verifier=self.credential.verifier,
        )
        self.synthetic_key = bytes.fromhex("11" * 32)
        self.research_key = bytes.fromhex("22" * 32)
        self.transport = _RecordingTransport()
        self.policy = RegisteredRoutePolicy()
        self.gateway = StagingGateway(
            config=self.config,
            route_policy=self.policy,
            transport=self.transport,
            worker_keys={
                "synthetic": self.synthetic_key,
                "research": self.research_key,
            },
        )
        self.client = TestClient(
            self.gateway,
            base_url=ORIGIN,
            client=("198.51.100.20", 50123),
        )
        self.addCleanup(self.client.close)

    def _login(self, *, passphrase: str | None = None):
        page = self.client.get("/auth/login")
        self.assertEqual(page.status_code, 200)
        match = re.search(
            r'name="csrf_token" value="([A-Za-z0-9_-]+)"', page.text
        )
        self.assertIsNotNone(match)
        return self.client.post(
            "/auth/login",
            data={
                "passphrase": passphrase or self.credential.passphrase,
                "csrf_token": match.group(1),
            },
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )

    def _direct_session(self):
        issued = self.gateway.sessions.create(
            principal_ref="owner:railway-staging:01",
            role_ref="RAILWAY_STAGING_OWNER",
            capabilities=self.policy.capabilities,
            credential_fingerprint=self.gateway.authenticator.fingerprint,
        )
        session = self.gateway.sessions.authenticate(
            issued.token,
            credential_fingerprint=self.gateway.authenticator.fingerprint,
        )
        self.assertIsNotNone(session)
        return issued, session

    def test_synthetic_html_removes_legacy_review_token_prompt(self):
        self.assertEqual(self._login().status_code, 303)
        self.transport.response = WorkerResponse(
            status=200,
            headers=((b"content-type", b"text/html"),),
            body=(
                b"<form method='post'><label>Review token "
                b"<input name='review_token' type='password' required></label>"
                b"<button>Continue</button></form>"
            ),
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("review_token", response.text)
        self.assertNotIn("type='password'", response.text)
        self.assertIn("_buffalo_staging_csrf", response.text)

    def test_health_is_minimal_and_security_headers_are_unconditional(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": True, "service": "buffalo-procurement-staging-gateway"},
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["strict-transport-security"], "max-age=31536000")
        self.assertNotIn("process", response.text)
        self.assertNotIn("commit", response.text)

    def test_login_uses_one_time_csrf_and_secure_host_only_cookies(self):
        response = self._login()
        self.assertEqual(response.status_code, 303)
        cookies = response.headers.get_list("set-cookie")
        session = next(value for value in cookies if value.startswith(SESSION_COOKIE))
        csrf = next(value for value in cookies if value.startswith(CSRF_COOKIE))
        self.assertIn("Secure", session)
        self.assertIn("HttpOnly", session)
        self.assertIn("SameSite=strict", session)
        self.assertIn("Path=/", session)
        self.assertNotIn("Domain=", session)
        self.assertIn("Secure", csrf)
        self.assertNotIn("HttpOnly", csrf)
        self.assertNotIn(self.credential.passphrase, " ".join(cookies))

    def test_wrong_passphrase_and_missing_login_origin_fail_generically(self):
        wrong = self._login(passphrase="wrong-owner-passphrase")
        self.assertEqual(wrong.status_code, 403)
        self.assertNotIn("password", wrong.text.lower())
        page = self.client.get("/auth/login")
        token = re.search(r'value="([A-Za-z0-9_-]+)"', page.text).group(1)
        missing_origin = self.client.post(
            "/auth/login",
            data={"passphrase": self.credential.passphrase, "csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(missing_origin.status_code, 403)

    def test_registered_routes_require_session_and_unknown_routes_deny(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self._login().status_code, 303)
        self.assertEqual(self.client.get("/definitely-not-a-route").status_code, 403)
        self.assertEqual(
            self.client.get("/private-research/artifacts/not-published.txt").status_code,
            403,
        )
        self.assertEqual(self.transport.requests, [])

    def test_explicit_allowlist_rejects_dangerous_and_ambiguous_routes(self):
        self.assertEqual(self._login().status_code, 303)
        refused = (
            ("POST", "/vendor-rules/vendor-01"),
            ("POST", "/pricing/rollover"),
            ("POST", "/price-books/batch-01/reject"),
            ("GET", "/private-research/manifest"),
            ("HEAD", "/private-research"),
        )
        for method, path in refused:
            with self.subTest(method=method, path=path):
                response = self.client.request(
                    method,
                    path,
                    headers={
                        "Origin": ORIGIN,
                        "X-Buffalo-CSRF-Token": self.client.cookies[CSRF_COOKIE],
                    },
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.transport.requests, [])

    def test_route_path_query_cookie_and_framing_are_canonical(self):
        self.assertEqual(self._login().status_code, 303)
        self.assertEqual(self.client.get("/?unexpected=1").status_code, 403)
        self.assertIsNone(
            self.policy.match(
                method="GET",
                path="/private-research/../private-research",
                raw_path=b"/private-research/%2e%2e/private-research",
            )
        )
        self.assertIsNone(
            self.policy.match(
                method="GET",
                path="/supplier-mapping/value\x00",
                raw_path=b"/supplier-mapping/value%00",
            )
        )
        self.assertIsNone(
            self.policy.match(
                method="GET",
                path="/supplier-mapping/value%",
                raw_path=b"/supplier-mapping/value%25",
            )
        )
        session_cookie = self.client.cookies[SESSION_COOKIE]
        duplicate_cookie_scope = {
            "headers": (
                (b"host", HOST.encode("ascii")),
                (
                    b"cookie",
                    f"{SESSION_COOKIE}={session_cookie}; {SESSION_COOKIE}={session_cookie}".encode(
                        "ascii"
                    ),
                ),
            )
        }
        with self.assertRaisesRegex(StagingGatewayError, "duplicate request cookie"):
            self.gateway._validated_headers(duplicate_cookie_scope)
        with self.assertRaisesRegex(StagingGatewayError, "content length differs"):
            self.gateway._validate_body_framing(
                {b"content-length": b"2"}, b"one"
            )

    def test_synthetic_and_research_routes_receive_distinct_bound_assertions(self):
        self.assertEqual(self._login().status_code, 303)
        synthetic = self.client.get("/")
        research = self.client.get("/private-research")
        self.assertEqual(synthetic.status_code, 200)
        self.assertEqual(research.status_code, 200)
        self.assertEqual(
            [value["worker_role"] for value in self.transport.requests],
            ["synthetic", "research"],
        )
        for request, key in zip(
            self.transport.requests, (self.synthetic_key, self.research_key)
        ):
            verified = verify_assertion(
                token=request["assertion"],
                key=key,
                expected_worker_role=request["worker_role"],
                method=request["method"],
                path=request["path"],
                query=request["query"],
                body=request["body"],
                expected_origin=ORIGIN,
                now=request["issued_at"] + 1,
                nonce_cache=NonceReplayCache(),
            )
            self.assertEqual(verified.principal_ref, "owner:railway-staging:01")
            self.assertIn(request["capability"], verified.capabilities)

    def test_unsafe_routes_require_exact_origin_and_session_csrf(self):
        self.assertEqual(self._login().status_code, 303)
        path = "/economics/target-cost"
        payload = {"retail_price": 10, "target_margin_pct": 30}
        self.assertEqual(self.client.post(path, json=payload).status_code, 403)
        wrong_origin = self.client.post(
            path,
            json=payload,
            headers={
                "Origin": "https://other.example.test",
                "X-Buffalo-CSRF-Token": self.client.cookies[CSRF_COOKIE],
            },
        )
        self.assertEqual(wrong_origin.status_code, 403)
        accepted = self.client.post(
            path,
            json=payload,
            headers={
                "Origin": ORIGIN,
                "X-Buffalo-CSRF-Token": self.client.cookies[CSRF_COOKIE],
            },
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.transport.requests[-1]["worker_role"], "synthetic")

    def test_logout_requires_csrf_and_invalidates_session(self):
        self.assertEqual(self._login().status_code, 303)
        self.assertEqual(
            self.client.post("/auth/logout", headers={"Origin": ORIGIN}).status_code,
            403,
        )
        response = self.client.post(
            "/auth/logout",
            data={
                "_buffalo_staging_csrf": self.client.cookies[CSRF_COOKIE],
            },
            headers={
                "Origin": ORIGIN,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.client.get("/").status_code, 401)

    def test_invalid_proxy_and_client_internal_headers_are_refused(self):
        cases = (
            {"Forwarded": "for=198.51.100.20"},
            {"X-Forwarded-For": "198.51.100.20"},
            {"X-Forwarded-Proto": "http"},
            {"X-Forwarded-Host": "other.example.test"},
            {"X-Buffalo-Staging-Assertion": "client-selected"},
        )
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.client.get("/health", headers=headers).status_code, 403
                )
        accepted = self.client.get(
            "/health",
            headers={
                "X-Real-IP": "198.51.100.20",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": HOST,
                "X-Railway-Edge": "iad1",
                "X-Request-Start": "1790610000000",
                "X-Railway-Request-Id": "request-01",
            },
        )
        self.assertEqual(accepted.status_code, 200)

    def test_malformed_railway_and_singleton_metadata_fail_closed(self):
        cases = (
            (b"x-real-ip", b"not-an-ip"),
            (b"x-railway-edge", b"railway/us-west2"),
            (b"x-railway-edge", b"iad1\t"),
            (b"x-request-start", b"t=1790610000000"),
            (b"x-railway-request-id", b"request id with spaces"),
            (b"x-buffalo-csrf-token", b"not_base64url"),
            (b"cookie", b"not-a-cookie"),
            (b"x-real-ip", b""),
            (b"x-railway-edge", b""),
            (b"x-request-start", b""),
            (b"x-railway-request-id", b""),
            (b"origin", b""),
            (b"x-buffalo-csrf-token", b""),
        )
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(
                StagingGatewayError
            ):
                self.gateway._validated_headers(
                    {
                        "headers": (
                            (b"host", HOST.encode("ascii")),
                            (key, value),
                        )
                    }
                )

    def test_gateway_routes_reject_queries_and_oversized_body_before_receive(self):
        self.assertEqual(self.client.get("/health?secret=no").status_code, 403)
        self.assertEqual(self.client.get("/auth/login?next=/").status_code, 403)
        calls = {"receive": 0}
        sent: list[dict[str, object]] = []

        async def receive():
            calls["receive"] += 1
            raise AssertionError("oversized body must be rejected before receive")

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "raw_path": b"/auth/login",
            "query_string": b"",
            "headers": (
                (b"host", HOST.encode("ascii")),
                (b"origin", ORIGIN.encode("ascii")),
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", b"999999999"),
            ),
        }
        asyncio.run(self.gateway(scope, receive, send))
        self.assertEqual(calls["receive"], 0)
        self.assertEqual(sent[0]["status"], 403)

        calls["receive"] = 0
        sent.clear()
        scope["headers"] = tuple(
            pair for pair in scope["headers"] if pair[0] != b"content-length"
        )
        asyncio.run(self.gateway(scope, receive, send))
        self.assertEqual(calls["receive"], 0)
        self.assertEqual(sent[0]["status"], 403)

    def test_lifespan_and_websocket_scopes_use_protocol_correct_messages(self):
        lifespan_sent: list[dict[str, object]] = []
        messages = iter(
            ({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"})
        )

        async def lifespan_receive():
            return next(messages)

        async def lifespan_send(message):
            lifespan_sent.append(message)

        asyncio.run(
            self.gateway(
                {"type": "lifespan"}, lifespan_receive, lifespan_send
            )
        )
        self.assertEqual(
            [message["type"] for message in lifespan_sent],
            ["lifespan.startup.complete", "lifespan.shutdown.complete"],
        )
        self.assertEqual((self.transport.started, self.transport.stopped), (1, 1))

        websocket_sent: list[dict[str, object]] = []

        async def websocket_send(message):
            websocket_sent.append(message)

        asyncio.run(
            self.gateway(
                {"type": "websocket"},
                self.gateway._empty_receive,
                websocket_send,
            )
        )
        self.assertEqual(
            websocket_sent, [{"type": "websocket.close", "code": 1008}]
        )

    def test_worker_keys_must_be_distinct(self):
        with self.assertRaisesRegex(StagingGatewayError, "independently"):
            StagingGateway(
                config=self.config,
                route_policy=self.policy,
                transport=self.transport,
                worker_keys={
                    "synthetic": self.synthetic_key,
                    "research": self.synthetic_key,
                },
            )

    def test_worker_key_rotation_is_monotonic_and_cross_role_safe(self):
        replacement = bytes.fromhex("33" * 32)
        self.gateway.replace_worker_key(
            worker_role="research", key=replacement, generation=1
        )
        self.assertEqual(
            self.gateway.worker_keyring.current("research"),
            (replacement, 1),
        )
        with self.assertRaisesRegex(StagingGatewayError, "not newer"):
            self.gateway.replace_worker_key(
                worker_role="research",
                key=bytes.fromhex("44" * 32),
                generation=1,
            )
        skipped = bytes.fromhex("44" * 32)
        self.gateway.replace_worker_key(
            worker_role="research", key=skipped, generation=3
        )
        self.assertEqual(
            self.gateway.worker_keyring.current("research"), (skipped, 3)
        )
        with self.assertRaisesRegex(StagingGatewayError, "never be reused"):
            self.gateway.replace_worker_key(
                worker_role="research",
                key=self.synthetic_key,
                generation=4,
            )
        with self.assertRaisesRegex(StagingGatewayError, "never be reused"):
            self.gateway.replace_worker_key(
                worker_role="research",
                key=self.research_key,
                generation=4,
            )

    def test_bulk_price_upload_is_not_a_staging_route(self):
        self.assertIsNone(
            self.policy.match(
                method="POST",
                path="/price-books/import",
                raw_path=b"/price-books/import",
            )
        )

    def test_gateway_owned_encoded_aliases_and_noncanonical_parameters_deny(self):
        for path in ("/%68ealth", "/auth/%6cogin", "/%61uth/login"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertIsNone(
            self.policy.match(
                method="GET",
                path="/supplier-mapping/00000000-0000-4000-8000-00000000000A",
                raw_path=(
                    b"/supplier-mapping/00000000-0000-4000-8000-00000000000A"
                ),
            )
        )

        sent: list[dict[str, object]] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(
            self.gateway(
                {
                    "type": "http",
                    "method": "get",
                    "path": "/health",
                    "raw_path": b"/health",
                    "query_string": b"",
                    "headers": ((b"host", HOST.encode("ascii")),),
                },
                receive,
                send,
            )
        )
        self.assertEqual(sent[0]["status"], 403)
        self.assertIsNone(
            self.policy.match(
                method="GET",
                path="/monday-runs/00000000-0000-4000-8000-000000000001/artifacts/01",
                raw_path=(
                    b"/monday-runs/00000000-0000-4000-8000-000000000001/artifacts/01"
                ),
            )
        )
        self.assertIsNone(
            self.policy.match(
                method="GET",
                path="/" + "a" * 256,
                raw_path=b"/" + b"a" * 256,
            )
        )

    def test_gateway_owned_routes_reject_transfer_encoding(self):
        for path in ("/health", "/auth/login"):
            with self.subTest(path=path):
                response = self.client.get(
                    path, headers={"Transfer-Encoding": "chunked"}
                )
                self.assertEqual(response.status_code, 403)

    def test_busy_password_slot_is_refused_before_executor_submission(self):
        reservation = self.gateway.authenticator.reserve()
        try:
            with patch(
                "procurement_os.staging_gateway.asyncio.to_thread",
                side_effect=AssertionError("executor must not be reached"),
            ) as submit:
                response = self._login()
            self.assertEqual(response.status_code, 403)
            submit.assert_not_called()
        finally:
            reservation.verify("definitely-wrong")

    def test_throttle_race_inside_reserved_verification_is_generic_auth_failure(self):
        class _ThrottledReservation:
            def verify(self, supplied):
                raise AuthenticationThrottled("simulated admission race")

        with patch.object(
            self.gateway.authenticator,
            "reserve",
            return_value=_ThrottledReservation(),
        ):
            response = self._login()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.text, "Authentication failed")

    def test_cancelled_login_holds_the_only_verification_slot_until_thread_finishes(self):
        started = threading.Event()
        release = threading.Event()
        calls = {"count": 0}

        def blocking_verification(supplied):
            calls["count"] += 1
            started.set()
            release.wait(timeout=5)
            return True

        def request_parts():
            challenge = self.gateway.login_challenges.create()
            body = urlencode(
                {
                    "passphrase": self.credential.passphrase,
                    "csrf_token": challenge.form_token,
                }
            ).encode("ascii")
            headers = {
                b"origin": ORIGIN.encode("ascii"),
                b"content-type": b"application/x-www-form-urlencoded",
                b"cookie": (
                    f"{LOGIN_CHALLENGE_COOKIE}={challenge.cookie_token}"
                ).encode("ascii"),
            }
            return headers, body

        async def scenario():
            first_sent: list[dict[str, object]] = []
            second_sent: list[dict[str, object]] = []

            async def first_send(message):
                first_sent.append(message)

            async def second_send(message):
                second_sent.append(message)

            first_headers, first_body = request_parts()
            first = asyncio.create_task(
                self.gateway._login(
                    headers=first_headers,
                    body=first_body,
                    send=first_send,
                )
            )
            self.assertTrue(
                await asyncio.wait_for(
                    asyncio.to_thread(started.wait, 2), timeout=3
                )
            )
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first

            second_headers, second_body = request_parts()
            await self.gateway._login(
                headers=second_headers,
                body=second_body,
                send=second_send,
            )
            self.assertEqual(second_sent[0]["status"], 403)
            self.assertEqual(calls["count"], 1)

            release.set()
            for _ in range(1_000):
                if not self.gateway._verification_tasks:
                    break
                await asyncio.sleep(0.001)
            self.assertFalse(self.gateway._verification_tasks)
            self.assertTrue(self.gateway.authenticator._slot.acquire(blocking=False))
            self.gateway.authenticator._slot.release()
            self.assertEqual(first_sent, [])
            second_sent.clear()
            await self.gateway._login(
                headers=second_headers,
                body=second_body,
                send=second_send,
            )
            self.assertEqual(second_sent[0]["status"], 303)
            self.assertEqual(calls["count"], 2)

        with patch.object(
            self.gateway.authenticator,
            "_verify_admitted",
            side_effect=blocking_verification,
        ):
            asyncio.run(scenario())

    def test_html_post_forms_receive_session_csrf_and_submit_without_script(self):
        self.assertEqual(self._login().status_code, 303)
        self.transport.response = WorkerResponse(
            status=200,
            headers=((b"content-type", b"text/html; charset=utf-8"),),
            body=(
                b"<html><body><form method='post' "
                b"action='/supplier-mapping/intake'><button>Run</button>"
                b"</form></body></html>"
            ),
        )
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        token = re.search(
            r'name="_buffalo_staging_csrf" value="([A-Za-z0-9_-]+)"',
            page.text,
        )
        self.assertIsNotNone(token)
        self.transport.response = WorkerResponse(
            status=303,
            headers=((b"location", b"/supplier-mapping"),),
            body=b"",
        )
        submitted = self.client.post(
            "/supplier-mapping/intake",
            data={"_buffalo_staging_csrf": token.group(1)},
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )
        self.assertEqual(submitted.status_code, 303)
        self.assertEqual(submitted.headers["location"], "/supplier-mapping")

    def test_worker_artifact_body_is_streamed_and_closed(self):
        self.assertEqual(self._login().status_code, 303)
        state = {"closed": False, "chunks": 0}

        async def chunks():
            for value in (b"first", b"second"):
                state["chunks"] += 1
                yield value

        async def close():
            state["closed"] = True

        self.transport.response = WorkerResponse(
            status=200,
            headers=(
                (b"content-type", b"application/json"),
                (b"content-length", b"11"),
                (b"access-control-allow-origin", b"*"),
            ),
            body=chunks(),
            close=close,
        )
        response = self.client.get(
            f"/supplier-mapping/{MAPPING_CANDIDATE}/source"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"firstsecond")
        self.assertEqual(state, {"closed": True, "chunks": 2})
        self.assertEqual(response.headers["content-length"], "11")
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_buffered_artifact_response_is_rejected_and_closed(self):
        self.assertEqual(self._login().status_code, 303)
        state = {"closed": 0}

        async def close():
            state["closed"] += 1

        self.transport.response = WorkerResponse(
            status=200,
            headers=(
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ),
            body=b"{}",
            close=close,
        )
        response = self.client.get("/private-research/artifacts/coverage.json")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(state["closed"], 1)

    def test_malformed_stream_close_and_header_types_fail_without_leaking(self):
        self.assertEqual(self._login().status_code, 303)
        state = {"closed": 0}

        class _Stream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self):
                state["closed"] += 1

        self.transport.response = WorkerResponse(
            status=200,
            headers=(
                (b"content-type", b"application/json"),
                (b"content-length", b"1"),
            ),
            body=_Stream(),
            close=42,
        )
        response = self.client.get(
            f"/supplier-mapping/{MAPPING_CANDIDATE}/source"
        )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(state["closed"], 1)

        self.transport.response = WorkerResponse(
            status=200,
            headers=((10**9, b"unsafe"),),
            body=b"safe",
        )
        self.assertEqual(self.client.get("/").status_code, 502)

    def test_stream_stops_and_closes_once_when_browser_disconnects(self):
        issued, session = self._direct_session()
        selection = self.policy.match(
            method="GET",
            path="/private-research/artifacts/projection.json",
            raw_path=b"/private-research/artifacts/projection.json",
        )
        self.assertIsNotNone(selection)
        state = {"chunks": 0, "closed": 0}
        sent: list[dict[str, object]] = []

        async def chunks():
            for _ in range(1_000):
                state["chunks"] += 1
                await asyncio.sleep(0.01)
                yield b"x"

        async def close():
            state["closed"] += 1

        async def receive():
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        async def send(message):
            # Uvicorn may silently ignore response messages after disconnect.
            sent.append(message)

        async def scenario():
            await self.gateway._send_worker_response(
                receive=receive,
                send=send,
                value=WorkerResponse(
                    status=200,
                    headers=(
                        (b"content-type", b"application/json"),
                        (b"content-length", b"191788544"),
                    ),
                    body=chunks(),
                    close=close,
                ),
                request_path="/private-research/artifacts/projection.json",
                session=session,
                request_headers={
                    b"cookie": (
                        f"{CSRF_COOKIE}={issued.csrf_token}"
                    ).encode("ascii")
                },
                selection=selection,
            )

        asyncio.run(scenario())
        self.assertLess(state["chunks"], 1_000)
        self.assertEqual(state["closed"], 1)
        self.assertEqual([message["type"] for message in sent], ["http.response.start"])

    def test_stream_cancellation_cleans_pending_read_and_transport_once(self):
        _, session = self._direct_session()
        selection = self.policy.match(
            method="GET",
            path=f"/supplier-mapping/{MAPPING_CANDIDATE}/source",
            raw_path=(f"/supplier-mapping/{MAPPING_CANDIDATE}/source").encode(),
        )
        entered = asyncio.Event()
        finalized = asyncio.Event()
        release = asyncio.Event()
        closed = {"count": 0}

        async def chunks():
            try:
                entered.set()
                await release.wait()
                yield b"x"
            finally:
                finalized.set()

        async def close():
            closed["count"] += 1

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            return None

        async def scenario():
            task = asyncio.create_task(
                self.gateway._send_worker_response(
                    receive=receive,
                    send=send,
                    value=WorkerResponse(
                        status=200,
                        headers=(
                            (b"content-type", b"application/json"),
                            (b"content-length", b"1"),
                        ),
                        body=chunks(),
                        close=close,
                    ),
                    request_path=f"/supplier-mapping/{MAPPING_CANDIDATE}/source",
                    session=session,
                    request_headers={},
                    selection=selection,
                )
            )
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(finalized.wait(), timeout=1)
            self.assertEqual(
                [
                    pending
                    for pending in asyncio.all_tasks()
                    if pending is not asyncio.current_task() and not pending.done()
                ],
                [],
            )

        asyncio.run(scenario())
        self.assertEqual(closed["count"], 1)

    def test_empty_stream_chunk_is_rejected_and_transport_closed_once(self):
        _, session = self._direct_session()
        selection = self.policy.match(
            method="GET",
            path=f"/supplier-mapping/{MAPPING_CANDIDATE}/source",
            raw_path=(f"/supplier-mapping/{MAPPING_CANDIDATE}/source").encode(),
        )
        closed = {"count": 0}
        sent: list[dict[str, object]] = []

        async def chunks():
            yield b""

        async def close():
            closed["count"] += 1

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        asyncio.run(
            self.gateway._send_worker_response(
                receive=receive,
                send=send,
                value=WorkerResponse(
                    status=200,
                    headers=(
                        (b"content-type", b"application/json"),
                        (b"content-length", b"1"),
                    ),
                    body=chunks(),
                    close=close,
                ),
                request_path=f"/supplier-mapping/{MAPPING_CANDIDATE}/source",
                session=session,
                request_headers={},
                selection=selection,
            )
        )
        self.assertEqual(closed["count"], 1)
        self.assertEqual([message["type"] for message in sent], ["http.response.start"])

    def test_route_specific_stream_sizes_and_media_types_are_exact_or_bounded(self):
        cases = (
            (
                "/private-research/artifacts/coverage.json",
                10_684_242,
                "application/json",
            ),
            (
                "/private-research/artifacts/owner-preview.html",
                21_144_726,
                "text/html",
            ),
            (
                "/private-research/artifacts/owner-worksheet.csv",
                17_347_994,
                "text/csv",
            ),
            (
                "/private-research/artifacts/projection.json",
                191_788_544,
                "application/json",
            ),
        )
        for path, size, media_type in cases:
            route = self.policy.match(
                method="GET", path=path, raw_path=path.encode("ascii")
            )
            with self.subTest(path=path):
                self.assertTrue(
                    response_metadata_is_allowed(
                        route=route,
                        path=path,
                        content_length=size,
                        content_type=media_type,
                    )
                )
                for wrong_size in (size - 1, size + 1):
                    self.assertFalse(
                        response_metadata_is_allowed(
                            route=route,
                            path=path,
                            content_length=wrong_size,
                            content_type=media_type,
                        )
                    )
                self.assertFalse(
                    response_metadata_is_allowed(
                        route=route,
                        path=path,
                        content_length=size,
                        content_type="application/octet-stream",
                    )
                )

        other_cases = (
            ("/price-books/template.csv", 408, "text/csv", 409, False),
            (
                f"/supplier-mapping/{MAPPING_CANDIDATE}/source",
                1_048_576,
                "application/json",
                1_048_577,
                False,
            ),
            (
                f"/price-books/{MAPPING_CANDIDATE}/raw.csv",
                5_000_000,
                "text/csv",
                5_000_001,
                False,
            ),
            (
                f"/monday-runs/{MAPPING_CANDIDATE}/artifacts/1",
                8_388_608,
                "application/zip",
                8_388_609,
                False,
            ),
        )
        for path, accepted, media_type, refused, refused_expected in other_cases:
            route = self.policy.match(
                method="GET", path=path, raw_path=path.encode("ascii")
            )
            with self.subTest(path=path):
                self.assertTrue(
                    response_metadata_is_allowed(
                        route=route,
                        path=path,
                        content_length=accepted,
                        content_type=media_type,
                    )
                )
                self.assertEqual(
                    response_metadata_is_allowed(
                        route=route,
                        path=path,
                        content_length=refused,
                        content_type=media_type,
                    ),
                    refused_expected,
                )

    def test_buffered_worker_response_and_html_form_count_are_bounded(self):
        self.assertEqual(self._login().status_code, 303)
        self.transport.response = WorkerResponse(
            status=200,
            headers=((b"content-type", b"text/plain"),),
            body=b"x" * (8 * 1024 * 1024 + 1),
        )
        self.assertEqual(self.client.get("/").status_code, 502)
        self.transport.response = WorkerResponse(
            status=200,
            headers=((b"content-type", b"text/html"),),
            body=b"<form method='post'></form>" * 513,
        )
        self.assertEqual(self.client.get("/").status_code, 502)

    def test_transport_exception_is_contained_with_security_headers(self):
        self.assertEqual(self._login().status_code, 303)
        self.transport.error = RuntimeError("private transport detail")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("private transport detail", response.text)

    def test_wrong_worker_response_type_is_one_protected_502(self):
        self.assertEqual(self._login().status_code, 303)
        self.transport.response = None
        response = self.client.get("/")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_only_allowlisted_request_headers_reach_worker(self):
        self.assertEqual(self._login().status_code, 303)
        response = self.client.get(
            "/",
            headers={
                "Accept": "text/html",
                "Authorization": "Bearer client-selected",
                "X-Principal": "client-selected",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.transport.requests[-1]["headers"],
            ((b"accept", b"text/html"),),
        )

    def test_worker_cannot_set_cookie_or_replace_gateway_security_headers(self):
        self.assertEqual(self._login().status_code, 303)
        self.transport.response = WorkerResponse(
            status=200,
            headers=(
                (b"content-type", b"text/plain"),
                (b"set-cookie", b"attacker=value"),
                (b"cache-control", b"public"),
                (b"location", b"https://evil.example/"),
            ),
            body=b"safe",
        )
        response = self.client.get("/")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("attacker", response.headers.get("set-cookie", ""))
        self.assertEqual(response.headers["cache-control"], "no-store")

        closed = {"count": 0}

        async def close_invalid():
            closed["count"] += 1

        self.transport.response = WorkerResponse(
            status=303,
            headers=((b"location", b"/\\evil.example"),),
            body=b"",
            close=close_invalid,
        )
        backslash = self.client.get("/")
        self.assertEqual(backslash.status_code, 502)
        self.assertEqual(closed["count"], 1)


if __name__ == "__main__":
    unittest.main()
