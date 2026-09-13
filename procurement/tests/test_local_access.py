"""Local-only session, origin, and route-authorization acceptance tests."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from procurement_os import api
from procurement_os import local_access


PORT = 18865
ORIGIN = f"http://127.0.0.1:{PORT}"
SECRET = "fabricated-local-access-secret-20260913"


class LocalAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="buffalo-local-access-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        root.chmod(0o700)
        self.secret_file = root / "access.secret"
        self.secret_file.write_text(SECRET + "\n", encoding="utf-8")
        self.secret_file.chmod(0o600)
        self.environment = mock.patch.dict(
            os.environ,
            {
                "BUFFALO_RUNTIME_MODE": "AUTOMATED_TEST",
                "BUFFALO_LOCAL_PORT": str(PORT),
                "BUFFALO_LOCAL_AUTH_SECRET_FILE": str(self.secret_file),
                "BUFFALO_LOCAL_PRINCIPAL_REF": "synthetic:local-access-owner:01",
                "BUFFALO_LOCAL_ROLE_REF": "LOCAL_TEST_OWNER",
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        local_access.clear_local_sessions()
        self.addCleanup(local_access.clear_local_sessions)

    def _client(self, *, origin: str = ORIGIN) -> TestClient:
        client = TestClient(
            api.app,
            base_url=origin,
            client=("127.0.0.1", 50001),
        )
        self.addCleanup(client.close)
        return client

    def _login(self, client: TestClient):
        return client.post(
            "/auth/login",
            data={"secret": SECRET},
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )

    def test_unconfigured_mode_refuses_protected_routes(self):
        with mock.patch.dict(os.environ, {"BUFFALO_LOCAL_PRINCIPAL_REF": ""}):
            response = self._client().get("/")
        self.assertEqual(response.status_code, 503)

    def test_atomic_login_mints_opaque_strict_cookie(self):
        response = self._login(self._client())
        self.assertEqual(response.status_code, 303)
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn(SECRET, cookie)

    def test_wrong_secret_never_creates_a_session(self):
        client = self._client()
        response = client.post(
            "/auth/login",
            data={"secret": "definitely-wrong-secret-value"},
            headers={"Origin": ORIGIN},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(local_access._sessions), 0)

    def test_secret_replacement_invalidates_existing_session(self):
        client = self._client()
        self.assertEqual(self._login(client).status_code, 303)
        self.secret_file.write_text("replacement-local-secret-value-20260913\n", encoding="utf-8")
        self.secret_file.chmod(0o600)
        self.assertEqual(client.get("/").status_code, 401)

    def test_exact_loopback_host_rejects_localhost_alias(self):
        client = self._client(origin=f"http://localhost:{PORT}")
        response = client.get("/health")
        self.assertEqual(response.status_code, 403)

    def test_forwarded_headers_are_refused(self):
        response = self._client().get(
            "/health", headers={"X-Forwarded-For": "127.0.0.1"}
        )
        self.assertEqual(response.status_code, 403)

    def test_unsafe_request_requires_exact_origin(self):
        client = self._client()
        self.assertEqual(self._login(client).status_code, 303)
        response = client.post(
            "/auth/logout",
            headers={"Origin": "http://127.0.0.1:1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)

    def test_list_detail_and_download_require_a_session(self):
        client = self._client()
        for path in ("/supplier-mapping", "/monday-runs", "/price-books/template.csv"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)

    def test_docs_and_unmapped_routes_default_deny(self):
        client = self._client()
        self.assertEqual(self._login(client).status_code, 303)
        self.assertEqual(client.get("/docs").status_code, 403)
        self.assertEqual(client.delete("/supplier-mapping").status_code, 403)

    def test_private_real_review_mode_has_no_write_capability(self):
        with mock.patch.dict(os.environ, {"BUFFALO_RUNTIME_MODE": "PRIVATE_REAL_SOURCE_REVIEW"}):
            client = self._client()
            self.assertEqual(self._login(client).status_code, 303)
            response = client.post(
                "/supplier-mapping/intake",
                headers={"Origin": ORIGIN},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 403)

    def test_logout_deletes_and_invalidates_cookie(self):
        client = self._client()
        self.assertEqual(self._login(client).status_code, 303)
        response = client.post(
            "/auth/logout", headers={"Origin": ORIGIN}, follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual(client.get("/").status_code, 401)

    def test_idle_expiry_and_security_headers_fail_closed(self):
        client = self._client()
        self.assertEqual(self._login(client).status_code, 303)
        for session in local_access._sessions.values():
            session.last_seen_monotonic = time.monotonic() - local_access.SESSION_IDLE_SECONDS - 1
        response = client.get("/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        policy = response.headers["content-security-policy"]
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn("style-src 'self' 'unsafe-inline'", policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", policy)
        synthetic = mock.Mock(mode="SYNTHETIC_DEMO")
        price_detail = {
            "price_book_batch_id": "00000000-0000-4000-8000-000000000001",
            "batch_ref": "synthetic-batch",
            "vendor_name": "Synthetic Vendor",
            "target_price_state": "FUTURE",
            "operational_status": "INVALID",
            "status": "INVALID",
            "valid_row_count": 0,
            "row_count": 1,
            "covered_offer_count": 0,
            "expected_offer_count": 1,
            "validation_fingerprint": "a" * 64,
            "warning_count": 0,
            "issues": [],
        }
        with (
            mock.patch.object(api, "runtime_config", return_value=synthetic),
            mock.patch.object(
                api,
                "investigation_items",
                return_value={"run": None, "missing": [], "new": []},
            ),
        ):
            operational_pages = (
                api._vendor_rules_html(
                    {"status": "WARN", "message": "synthetic", "vendors": []}
                ),
                api._price_book_list_html([]),
                api._price_book_detail_html(price_detail),
                api.investigation_page(),
            )
        for page in operational_pages:
            with self.subTest(page=page[:80]):
                self.assertIn("TEST DATA — NOT FOR ORDERING", page)


if __name__ == "__main__":
    unittest.main()
