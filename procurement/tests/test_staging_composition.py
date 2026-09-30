"""Import-time local/staging worker composition and isolation tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from procurement_os import api, private_research_app
from procurement_os.staging_routes import (
    StagingRouteError,
    validate_worker_application_routes,
)
from procurement_os.staging_composition import load_staging_worker_boundary_config
from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID,
    EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID,
    EXPECTED_PROJECT_ID,
)
from procurement_os.staging_worker_transport import StagingWorkerBoundaryError


SRC = Path(__file__).resolve().parents[1] / "src"
WORKER_ENV = (
    "BUFFALO_STAGING_WORKER_ROLE",
    "BUFFALO_STAGING_ASSERTION_KEY_FILE",
    "BUFFALO_STAGING_EXTERNAL_ORIGIN",
    "BUFFALO_STAGING_GATEWAY_PID",
    "BUFFALO_STAGING_GATEWAY_UID",
    "BUFFALO_STAGING_GATEWAY_GID",
    "BUFFALO_STAGING_KEY_DIRECTORY_UID",
    "BUFFALO_STAGING_KEY_DIRECTORY_GID",
)


def _worker_environment(*, role: str, key_file: Path) -> dict[str, str]:
    environment = {
        "BUFFALO_STAGING_WORKER_ROLE": role,
        "BUFFALO_STAGING_ASSERTION_KEY_FILE": str(key_file),
        "BUFFALO_STAGING_EXTERNAL_ORIGIN": "https://staging.example.test",
        "BUFFALO_STAGING_GATEWAY_PID": str(os.getpid()),
        "BUFFALO_STAGING_GATEWAY_UID": str(os.getuid()),
        "BUFFALO_STAGING_GATEWAY_GID": str(os.getgid()),
        "BUFFALO_STAGING_KEY_GENERATION": "0",
        "BUFFALO_STAGING_KEY_DIRECTORY_UID": str(os.getuid()),
        "BUFFALO_STAGING_KEY_DIRECTORY_GID": str(os.getgid()),
        "BUFFALO_STAGING_LISTEN_FD": "11",
        "BUFFALO_STAGING_RUNTIME_ROOT": f"/run/buffalo/{role}",
        "BUFFALO_STAGING_SOCKET_GID": str(os.getgid()),
        "BUFFALO_STAGING_SOCKET_PATH": f"/run/buffalo/sockets/{role}/worker.sock",
        "HOME": f"/run/buffalo/{role}",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin",
        "PYTHONUNBUFFERED": "1",
        "TMPDIR": f"/run/buffalo/{role}/tmp",
        "TZ": "UTC",
    }
    if role == "synthetic":
        environment.update(
            {
                "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
                "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
                "DATABASE_URL": "postgresql://qa_release_login@postgres.railway.internal/staging_demo",
                "PGPASSFILE": "/run/buffalo/synthetic/private/pgpass",
                "PROCUREMENT_STORAGE_ROOT": "/data/synthetic",
                "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
                "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
                "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            }
        )
    else:
        environment.update(
            {
                "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": "/data/research/workspace",
                "BUFFALO_RESEARCH_GIT_DIR": "/data/research/source.git",
                "BUFFALO_RESEARCH_MANIFEST": "/data/research/manifest.json",
                "BUFFALO_RESEARCH_ROOT": "/data/research",
            }
        )
    return environment


class StagingCompositionTests(unittest.TestCase):
    def _import_application(self, module: str, *, role: str | None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy() if role is None else {}
        for name in WORKER_ENV:
            environment.pop(name, None)
        temporary: tempfile.TemporaryDirectory[str] | None = None
        if role is not None:
            temporary = tempfile.TemporaryDirectory(prefix="buffalo-worker-key-")
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
            root.chmod(0o710)
            key_file = root / "assertion.key"
            key_file.write_bytes(bytes.fromhex("87" * 32))
            key_file.chmod(0o600)
            environment.update(_worker_environment(role=role, key_file=key_file))
        script = (
            "import json,os,sys;os.environ.pop('LD_LIBRARY_PATH',None);"
            f"sys.path.insert(0,{str(SRC)!r});"
            f"from procurement_os import {module} as target;"
            "print(json.dumps({"
            "'boundary':target._ACCESS_BOUNDARY,"
            "'middleware':[item.cls.__name__ for item in target.app.user_middleware]"
            "},sort_keys=True))"
        )
        return subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_default_apps_install_only_their_legacy_access_boundary(self):
        expected = {
            "api": ("LOCAL", "LocalAccessMiddleware"),
            "private_research_app": ("LOCAL", "PrivateResearchBasicAccessMiddleware"),
        }
        for module, (boundary, middleware) in expected.items():
            with self.subTest(module=module):
                result = self._import_application(module, role=None)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["boundary"], boundary)
                self.assertIn(middleware, payload["middleware"])
                self.assertNotIn("StagingInternalAccessMiddleware", payload["middleware"])

    def test_staging_apps_install_only_their_internal_boundary(self):
        for module, role, legacy in (
            ("api", "synthetic", "LocalAccessMiddleware"),
            (
                "private_research_app",
                "research",
                "PrivateResearchBasicAccessMiddleware",
            ),
        ):
            with self.subTest(module=module):
                result = self._import_application(module, role=role)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["boundary"], "RAILWAY_STAGING_INTERNAL")
                self.assertIn("StagingInternalAccessMiddleware", payload["middleware"])
                self.assertNotIn(legacy, payload["middleware"])

    def test_mismatched_worker_profile_fails_import(self):
        result = self._import_application("api", role="research")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staging worker composition role differs", result.stderr)

    def test_each_worker_live_route_inventory_validates_independently(self):
        validate_worker_application_routes(
            worker_role="synthetic", routes=api.app.routes
        )
        validate_worker_application_routes(
            worker_role="research", routes=private_research_app.app.routes
        )
        with self.assertRaises(StagingRouteError):
            validate_worker_application_routes(worker_role="synthetic", routes=())

    def test_gateway_import_does_not_load_worker_applications(self):
        script = (
            "import json,sys;"
            f"sys.path.insert(0,{str(SRC)!r});"
            "import procurement_os.staging_gateway;"
            "print(json.dumps({name:(name in sys.modules) for name in ("
            "'procurement_os.api','procurement_os.private_research_app','psycopg')}))"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "procurement_os.api": False,
            "procurement_os.private_research_app": False,
            "psycopg": False,
        })

    def test_worker_key_file_contract_is_exact(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-config-") as raw:
            root = Path(raw)
            root.chmod(0o710)
            key_file = root / "worker.key"
            key_file.write_bytes(b"k" * 32)
            key_file.chmod(0o600)
            environment = _worker_environment(role="synthetic", key_file=key_file)
            with mock.patch.dict(os.environ, environment, clear=True):
                config = load_staging_worker_boundary_config("synthetic")
                self.assertEqual(config.assertion_key, b"k" * 32)
                key_file.chmod(0o640)
                with self.assertRaises(StagingWorkerBoundaryError):
                    load_staging_worker_boundary_config("synthetic")

    def test_worker_origin_is_exact_canonical_https_without_port(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-origin-") as raw:
            root = Path(raw)
            root.chmod(0o710)
            key_file = root / "worker.key"
            key_file.write_bytes(b"k" * 32)
            key_file.chmod(0o600)
            base = _worker_environment(role="research", key_file=key_file)
            base.pop("BUFFALO_STAGING_EXTERNAL_ORIGIN")
            for origin in (
                "https://Staging.example.test",
                "https://staging.example.test:443",
            ):
                with self.subTest(origin=origin), mock.patch.dict(
                    os.environ,
                    {**base, "BUFFALO_STAGING_EXTERNAL_ORIGIN": origin},
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        StagingWorkerBoundaryError, "origin is invalid"
                    ):
                        load_staging_worker_boundary_config("research")

    def test_worker_environment_rejects_cross_role_or_unapproved_secrets(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-env-") as raw:
            root = Path(raw)
            root.chmod(0o710)
            key_file = root / "worker.key"
            key_file.write_bytes(b"k" * 32)
            key_file.chmod(0o600)
            environment = _worker_environment(role="research", key_file=key_file)
            environment["PGPASSWORD"] = "must-not-cross"
            with mock.patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(
                    StagingWorkerBoundaryError, "unapproved entry"
                ):
                    load_staging_worker_boundary_config("research")


if __name__ == "__main__":
    unittest.main()
