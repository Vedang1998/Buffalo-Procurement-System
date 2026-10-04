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
from procurement_os.staging_process_contract import (
    SYNTHETIC_PRICE_BACKUP_ENVIRONMENT_NAMES,
    StagingProcessContractError,
    validated_process_environment,
)
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
                "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST": "1",
                "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
                "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
                "BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1",
                "BUFFALO_STAGING_POSTGRES_PRIVATE_HOST": "postgres.railway.internal",
                "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
                "DATABASE_URL": "postgresql://buffalo_synthetic_runtime@postgres.railway.internal/buffalo_synthetic_staging_demo",
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

    def test_synthetic_local_database_authority_is_an_all_or_none_pair(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-env-") as raw:
            key_file = Path(raw) / "worker.key"
            environment = _worker_environment(role="synthetic", key_file=key_file)
            self.assertEqual(
                validated_process_environment(role="synthetic", environ=environment),
                environment,
            )
            local = {
                **environment,
                "BUFFALO_STAGING_LOCAL_ACCEPTANCE": "1",
                "BUFFALO_STAGING_OWNED_LOCAL_PORT": "55432",
            }
            self.assertEqual(
                validated_process_environment(role="synthetic", environ=local),
                local,
            )
            for name in (
                "BUFFALO_STAGING_LOCAL_ACCEPTANCE",
                "BUFFALO_STAGING_OWNED_LOCAL_PORT",
            ):
                with self.subTest(name=name), self.assertRaisesRegex(
                    StagingProcessContractError,
                    "local database authority is incomplete",
                ):
                    validated_process_environment(
                        role="synthetic",
                        environ={**environment, name: "1"},
                    )

    def test_synthetic_price_backup_binding_is_optional_exact_and_role_local(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-env-") as raw:
            key_file = Path(raw) / "worker.key"
            synthetic = _worker_environment(
                role="synthetic", key_file=key_file
            )
            binding = {
                "BUFFALO_STAGING_PRICE_BACKUP_ROOT": "/data/synthetic-recovery",
                "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST": (
                    "/data/synthetic-recovery/backups/"
                    "staging-v2-20261004T120000Z-0123456789ab/manifest.json"
                ),
                "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST_SHA256": "1" * 64,
                "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_COMMIT": "2" * 40,
                "BUFFALO_STAGING_PRICE_BACKUP_SOURCE_TREE": "3" * 40,
            }
            self.assertEqual(
                set(binding), SYNTHETIC_PRICE_BACKUP_ENVIRONMENT_NAMES
            )
            self.assertEqual(
                validated_process_environment(
                    role="synthetic", environ={**synthetic, **binding}
                ),
                {**synthetic, **binding},
            )
            for missing in sorted(binding):
                partial = {**synthetic, **binding}
                partial.pop(missing)
                with self.subTest(missing=missing), self.assertRaisesRegex(
                    StagingProcessContractError,
                    "price backup binding is incomplete",
                ):
                    validated_process_environment(
                        role="synthetic", environ=partial
                    )
            empty = {**synthetic, **binding}
            empty["BUFFALO_STAGING_PRICE_BACKUP_SOURCE_TREE"] = ""
            with self.assertRaisesRegex(
                StagingProcessContractError,
                "price backup binding is incomplete",
            ):
                validated_process_environment(role="synthetic", environ=empty)
            for name, value in (
                ("BUFFALO_STAGING_PRICE_BACKUP_ROOT", "relative"),
                (
                    "BUFFALO_STAGING_PRICE_BACKUP_ROOT",
                    "/data/other-recovery",
                ),
                (
                    "BUFFALO_STAGING_PRICE_BACKUP_MANIFEST",
                    "/data/synthetic-recovery/other/manifest.json",
                ),
                ("BUFFALO_STAGING_PRICE_BACKUP_MANIFEST_SHA256", "A" * 64),
                ("BUFFALO_STAGING_PRICE_BACKUP_SOURCE_COMMIT", "2" * 39),
                ("BUFFALO_STAGING_PRICE_BACKUP_SOURCE_TREE", "3" * 41),
            ):
                with self.subTest(name=name), self.assertRaisesRegex(
                    StagingProcessContractError,
                    "price backup binding differs",
                ):
                    validated_process_environment(
                        role="synthetic",
                        environ={**synthetic, **binding, name: value},
                    )
            research = _worker_environment(role="research", key_file=key_file)
            with self.assertRaisesRegex(
                StagingProcessContractError, "unapproved entry"
            ):
                validated_process_environment(
                    role="research", environ={**research, **binding}
                )

    def test_synthetic_mode_and_every_canonical_capability_are_exact(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-env-") as raw:
            environment = _worker_environment(
                role="synthetic",
                key_file=Path(raw) / "worker.key",
            )
            changes = (
                ("BUFFALO_RUNTIME_MODE", "LOCAL"),
                ("BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST", "0"),
                ("BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO", "true"),
                ("BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT", "0"),
                ("BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS", "yes"),
            )
            for name, value in changes:
                with self.subTest(name=name), self.assertRaisesRegex(
                    StagingProcessContractError,
                    "synthetic capability contract differs",
                ):
                    validated_process_environment(
                        role="synthetic",
                        environ={**environment, name: value},
                    )

    def test_readiness_descriptors_cannot_cross_worker_roles(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-worker-env-") as raw:
            key_file = Path(raw) / "worker.key"
            for role, own, other in (
                (
                    "synthetic",
                    "BUFFALO_STAGING_SYNTHETIC_READINESS_FD",
                    "BUFFALO_STAGING_RESEARCH_READINESS_FD",
                ),
                (
                    "research",
                    "BUFFALO_STAGING_RESEARCH_READINESS_FD",
                    "BUFFALO_STAGING_SYNTHETIC_READINESS_FD",
                ),
            ):
                environment = _worker_environment(role=role, key_file=key_file)
                accepted = {**environment, own: "19"}
                self.assertEqual(
                    validated_process_environment(role=role, environ=accepted),
                    accepted,
                )
                with self.subTest(role=role), self.assertRaisesRegex(
                    StagingProcessContractError, "unapproved entry"
                ):
                    validated_process_environment(
                        role=role,
                        environ={**environment, other: "19"},
                    )


if __name__ == "__main__":
    unittest.main()
