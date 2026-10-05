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
        self.assertEqual(
            local_access.required_capability(
                "POST", "/monday-runs/00000000-0000-4000-8000-000000000001/retire-stale-forecast"
            ),
            "procurement.order.approve",
        )
        client = self._client()
        self.assertEqual(self._login(client).status_code, 303)
        response = client.post(
            "/auth/logout",
            headers={"Origin": "http://127.0.0.1:1"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        with mock.patch.object(
            api, "_db_conn", side_effect=AssertionError("database reached")
        ):
            response = client.post(
                "/monday-runs/00000000-0000-4000-8000-000000000001/retire-stale-forecast",
                data={"reason": "synthetic", "review_token": "synthetic"},
                headers={"Origin": "http://127.0.0.1:1"},
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
            config = local_access.runtime_config()
            self.assertIn("procurement.private_research.read", config.capabilities)
            self.assertIn("procurement.private_research.download", config.capabilities)
            self.assertEqual(
                local_access.required_capability("GET", "/private-research"),
                "procurement.private_research.read",
            )
            self.assertEqual(
                local_access.required_capability(
                    "GET", "/private-research/artifacts/owner-worksheet.csv"
                ),
                "procurement.private_research.download",
            )
            response = client.post(
                "/supplier-mapping/intake",
                headers={"Origin": ORIGIN},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 403)

    def test_synthetic_mode_cannot_read_private_research(self):
        client = self._client()
        self.assertEqual(self._login(client).status_code, 303)
        self.assertEqual(client.get("/private-research").status_code, 403)

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
            price_detail_page = api._price_book_detail_html(price_detail)
            operational_pages = (
                api._vendor_rules_html(
                    {"status": "WARN", "message": "synthetic", "vendors": []}
                ),
                api._price_book_list_html([]),
                price_detail_page,
                api.investigation_page(),
            )
        for page in operational_pages:
            with self.subTest(page=page[:80]):
                self.assertIn("TEST DATA — NOT FOR ORDERING", page)
        self.assertIn("id='price-book-durable-status'", price_detail_page)
        self.assertIn("id='price-book-operational-status'", price_detail_page)

        declared_target = {
            "vendor_name": "Synthetic Southern",
            "source_valid_from": "2026-10-01",
            "source_valid_through": "2026-10-31",
            "declaration_sha256": "b" * 64,
        }
        staging_prices = api._price_book_list_html(
            [], declared_target, allow_upload=False
        )
        self.assertIn(
            "operator-staged input, browser-approved workflow", staging_prices
        )
        self.assertNotIn("price-books/import", staging_prices)
        self.assertNotIn("type='file'", staging_prices)

        declared_detail = {
            **price_detail,
            "status": "VALIDATED",
            "operational_status": "TEMPORAL_BLOCKED",
            "replacement_contract": api.SYNTHETIC_PRICE_REPLACEMENT_CONTRACT,
            "schedule_policy_ref": "synthetic-southern-monthly-complete-v1",
            "declaration_sha256": "c" * 64,
            "scope_membership_sha256": None,
            "temporal_basis": "REGISTERED_OBSERVATION",
            "tiers": [],
        }
        blocked_declared_page = api._price_book_detail_html(declared_detail)
        self.assertNotIn("confirmation-preview", blocked_declared_page)
        declared_detail["operational_status"] = "VALIDATED"
        authorized_declared_page = api._price_book_detail_html(declared_detail)
        self.assertIn("confirmation-preview", authorized_declared_page)
        self.assertIn(
            "data-temporal-basis='REGISTERED_OBSERVATION'",
            authorized_declared_page,
        )

        canonical_readiness = {
            "po_generation_enabled": True,
            "gates": [],
            "blockers": [],
        }
        connection = mock.MagicMock()
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        with (
            mock.patch.dict(
                os.environ,
                {
                    "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
                    "DATABASE_URL": "postgresql://synthetic.invalid/not-opened",
                },
            ),
            mock.patch.object(
                api,
                "full_health",
                return_value={
                    "po_generation_enabled": True,
                    "po_readiness": canonical_readiness,
                },
            ),
            mock.patch.object(api, "_db_conn", return_value=context),
            mock.patch.object(
                api, "po_readiness", return_value=canonical_readiness
            ),
        ):
            synthetic_client = self._client()
            self.assertEqual(self._login(synthetic_client).status_code, 303)
            health_payload = synthetic_client.get("/health/full").json()
            foundation_payload = synthetic_client.get("/foundation/status").json()
        for payload in (health_payload, foundation_payload):
            with self.subTest(payload=payload):
                self.assertEqual(payload["runtime_mode"], "SYNTHETIC_DEMO")
                self.assertEqual(
                    payload["safety_label"], "TEST DATA — NOT FOR ORDERING"
                )
                self.assertFalse(payload["operational_authority"]["order_actions_authorized"])
                self.assertFalse(payload["operational_authority"]["shopify_actions_authorized"])
        self.assertTrue(
            health_payload["canonical_po_readiness"]["po_generation_enabled"]
        )
        self.assertTrue(health_payload["canonical_po_generation_enabled"])
        self.assertFalse(health_payload["po_generation_enabled"])
        self.assertFalse(health_payload["po_readiness"]["po_generation_enabled"])
        self.assertTrue(foundation_payload["canonical_po_generation_enabled"])
        self.assertFalse(foundation_payload["po_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
