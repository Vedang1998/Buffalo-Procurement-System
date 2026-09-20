"""Authenticated private-research viewer boundary tests."""
from __future__ import annotations

import ast
import os
from pathlib import Path
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
        response = client.post(
            "/auth/login",
            data={"secret": SECRET},
            headers={"Origin": ORIGIN},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def test_authenticated_view_and_exact_artifacts_are_read_only(self):
        client = self._client()
        self.assertEqual(client.get("/private-research").status_code, 401)
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
                    "import sys; import procurement_os.private_research_app; "
                    "print(int('procurement_os.persistent_mapping' in sys.modules), "
                    "int('psycopg' in sys.modules))"
                ),
            ],
            cwd=Path(private_research_app.__file__).resolve().parents[3],
            env={**os.environ, "PYTHONPATH": str(Path(private_research_app.__file__).resolve().parents[1])},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "0 0", result.stderr)


if __name__ == "__main__":
    unittest.main()
