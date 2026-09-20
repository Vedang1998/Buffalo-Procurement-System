"""Authenticated private-research viewer boundary tests."""
from __future__ import annotations

import ast
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from procurement_os import local_access, private_research_app


PORT = 18876
ORIGIN = f"http://127.0.0.1:{PORT}"
SECRET = "private-research-viewer-secret-fixture"


class PrivateResearchAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="buffalo-private-viewer-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        root.chmod(0o700)
        secret = root / "viewer.secret"
        secret.write_text(SECRET + "\n", encoding="utf-8")
        secret.chmod(0o600)
        self.secret_path = secret
        self.environment = mock.patch.dict(
            os.environ,
            {
                "BUFFALO_RUNTIME_MODE": "PRIVATE_REAL_SOURCE_REVIEW",
                "BUFFALO_LOCAL_PORT": str(PORT),
                "BUFFALO_LOCAL_AUTH_SECRET_FILE": str(secret),
                "BUFFALO_LOCAL_PRINCIPAL_REF": "private:test-owner:01",
                "BUFFALO_LOCAL_ROLE_REF": "PRIVATE_REAL_REVIEWER",
                "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": str(root / "not-read"),
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        local_access.clear_local_sessions()
        self.addCleanup(local_access.clear_local_sessions)
        private_research_app._CACHED_WORKSPACE_PATH = None
        private_research_app._CACHED_WORKSPACE = None
        self.addCleanup(
            lambda: setattr(private_research_app, "_CACHED_WORKSPACE_PATH", None)
        )
        self.addCleanup(lambda: setattr(private_research_app, "_CACHED_WORKSPACE", None))
        self.workspace = {
            "manifest": {
                "contract": "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V1",
                "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
                "operational_authority": False,
                "workspace_id": "b" * 64,
                "projection_sha256": "a" * 64,
            },
            "projection": {
                "contract": "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V1",
                "projection_sha256": "a" * 64,
                "vendor_names": ["Fixture Supplier"],
                "declared_coverage": {"current_catalog_population": 1},
            },
            "artifacts": {
                "owner-preview.html": b"<!doctype html><h1>Private owner worksheet</h1>",
                "owner-worksheet.csv": b"contract,authority\r\nfixture,review-only\r\n",
                "projection.json": b"{}\n",
                "coverage.json": b"{}\n",
            },
        }
        self.research_rows = [
            {
                "shopify_variant_id": "1001",
                "product_title": "Fixture Product",
                "variant_title": "750 mL",
                "supplier_name": "Fixture Supplier",
                "join_status": "EXACT_SHOPIFY_VARIANT_ID",
                "missing_data_reasons": ["FORECAST_EVIDENCE_MISSING"],
                "forecast": {
                    "status": "REAL_NUMERICAL_EVALUATION_NOT_RUN",
                    "reason_codes": ["FORECAST_EVIDENCE_MISSING"],
                },
            }
        ]

    def _client(self) -> TestClient:
        patcher = mock.patch.object(
            private_research_app,
            "read_private_research_workspace",
            return_value=self.workspace,
        )
        self.read_workspace_mock = patcher.start()
        self.addCleanup(patcher.stop)
        filter_patcher = mock.patch.object(
            private_research_app,
            "filter_private_research_rows",
            return_value=self.research_rows,
        )
        filter_patcher.start()
        self.addCleanup(filter_patcher.stop)
        client = TestClient(
            private_research_app.app,
            base_url=ORIGIN,
            client=("127.0.0.1", 50001),
        )
        self.addCleanup(client.close)
        return client

    def _login(self, client: TestClient) -> None:
        client.headers["Authorization"] = self._authorization(SECRET)

    def _authorization(self, secret: str) -> str:
        credential = base64.b64encode(
            f"private:{secret}".encode("utf-8")
        ).decode("ascii")
        return f"Basic {credential}"

    def test_basic_protection_space_rejects_ambient_cookie_and_wrong_port(self):
        client = self._client()
        token, _ = local_access.create_local_session(SECRET)
        client.cookies.set(local_access.SESSION_COOKIE, token)
        cookie_only = client.get("/private-research")
        self.assertEqual(cookie_only.status_code, 401)
        self.assertIn("Basic", cookie_only.headers["www-authenticate"])

        authorized = client.get(
            "/private-research",
            headers={"Authorization": self._authorization(SECRET)},
        )
        self.assertEqual(authorized.status_code, 200)
        self.assertNotIn("set-cookie", authorized.headers)

        wrong_port = client.get(
            "/private-research",
            headers={
                "Authorization": self._authorization(SECRET),
                "Host": "127.0.0.1:18877",
            },
        )
        self.assertEqual(wrong_port.status_code, 403)

    def test_authenticated_view_and_exact_artifacts_are_read_only(self):
        client = self._client()
        challenge = client.get("/private-research")
        self.assertEqual(challenge.status_code, 401)
        self.assertIn("Basic", challenge.headers["www-authenticate"])
        self._login(client)
        page = client.get("/private-research")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Private real-source procurement research", page.text)
        self.assertIn("Fixture Product", page.text)
        self.assertIn("NOT_CAPTURED", page.text)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(self.read_workspace_mock.call_count, 1)
        manifest = client.get("/private-research/manifest").json()
        self.assertFalse(manifest["operational_authority"])
        self.assertEqual(
            client.get("/private-research/artifacts/owner-worksheet.csv").content,
            self.workspace["artifacts"]["owner-worksheet.csv"],
        )
        self.assertEqual(
            client.get("/private-research/artifacts/not-published.bin").status_code,
            404,
        )
        self.assertEqual(
            client.post(
                "/private-research",
                headers={"Origin": ORIGIN},
            ).status_code,
            403,
        )

    def test_health_refuses_an_unreadable_workspace(self):
        client = self._client()
        private_research_app._CACHED_WORKSPACE_PATH = None
        private_research_app._CACHED_WORKSPACE = None
        with mock.patch.object(
            private_research_app,
            "read_private_research_workspace",
            side_effect=private_research_app.PrivateResearchError("corrupt"),
        ):
            response = client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertNotEqual(response.json().get("ok"), True)

    def _assert_server_secret_unavailable(self, client: TestClient) -> None:
        responses = (
            client.get("/health"),
            client.get("/auth/login"),
            client.get(
                "/private-research",
                headers={"Authorization": self._authorization(SECRET)},
            ),
        )
        for response in responses:
            with self.subTest(path=response.request.url.path):
                self.assertEqual(response.status_code, 503)
                self.assertIn(
                    "Private review authentication is unavailable", response.text
                )
                self.assertNotIn(SECRET, response.text)
                self.assertNotIn("sha256", response.text.lower())
                self.assertIsNone(re.search(r"\b[0-9a-f]{64}\b", response.text))
        self.assertEqual(self.read_workspace_mock.call_count, 0)

    def test_health_and_auth_fail_closed_when_server_secret_is_missing(self):
        client = self._client()
        self.secret_path.unlink()
        self._assert_server_secret_unavailable(client)

    def test_health_and_auth_fail_closed_when_server_secret_is_invalid(self):
        client = self._client()
        self.secret_path.chmod(0o644)
        self._assert_server_secret_unavailable(client)

    def test_health_and_auth_normalize_server_secret_read_failure(self):
        client = self._client()
        with mock.patch.object(
            private_research_app,
            "_read_secret",
            side_effect=OSError("simulated read race"),
        ):
            self._assert_server_secret_unavailable(client)

    def test_wrong_supplied_secret_remains_forbidden_when_server_secret_is_valid(self):
        client = self._client()
        response = client.get(
            "/private-research",
            headers={
                "Authorization": self._authorization(
                    "wrong-private-research-secret"
                )
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers["www-authenticate"])
        self.assertNotIn(SECRET, response.text)
        self.assertNotEqual(response.status_code, 503)

    def test_viewer_composition_has_no_operational_service_imports(self):
        source = Path(private_research_app.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.rsplit(".", 1)[-1])
        forbidden = {
            "persistent_mapping",
            "price_book",
            "synthetic_price_replacement",
            "recommendations",
            "monday_run",
            "procurement_review",
            "draft_po",
            "po_csv",
            "emergency_packet",
        }
        self.assertTrue(imported.isdisjoint(forbidden), imported & forbidden)

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, sys; import procurement_os.private_research_app; "
                    "forbidden={'procurement_os.api','procurement_os.draft_po',"
                    "'procurement_os.emergency_packet','procurement_os.monday_run',"
                    "'procurement_os.persistent_mapping','procurement_os.po_csv',"
                    "'procurement_os.price_book','procurement_os.procurement_review',"
                    "'procurement_os.recommendations','procurement_os.synthetic_price_replacement',"
                    "'psycopg'}; "
                    "print(json.dumps({'forbidden': sorted(forbidden & set(sys.modules)), "
                    "'contract_loaded': 'procurement_os.price_book_contract' in sys.modules}, "
                    "sort_keys=True))"
                ),
            ],
            cwd=Path(private_research_app.__file__).resolve().parents[3],
            env={**os.environ, "PYTHONPATH": str(Path(private_research_app.__file__).resolve().parents[1])},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {"contract_loaded": True, "forbidden": []},
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
