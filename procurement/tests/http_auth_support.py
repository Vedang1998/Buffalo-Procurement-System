"""Real local-access fixture for HTTP route tests.

The helper signs in through the public login route.  It does not inject a
session, bypass middleware, or copy the ambient environment.
"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from procurement_os.local_access import clear_local_sessions


TEST_PORT = 18765
TEST_ORIGIN = f"http://127.0.0.1:{TEST_PORT}"
TEST_SECRET = "fabricated-local-http-secret-20260913"


def loopback_test_client(app, *, origin: str = "http://127.0.0.1:8765") -> TestClient:
    """Return an unauthenticated real-middleware client for negative tests."""

    return TestClient(
        app,
        base_url=origin,
        headers={"Origin": origin},
        client=("127.0.0.1", 50000),
    )


def authenticated_test_client(test_case, app) -> TestClient:
    """Create, log in, and register cleanup for an AUTOMATED_TEST client."""

    temporary = TemporaryDirectory(prefix="buffalo-http-auth-")
    root = Path(temporary.name)
    root.chmod(0o700)
    secret_file = root / "local-auth.secret"
    secret_file.write_text(TEST_SECRET + "\n", encoding="utf-8")
    secret_file.chmod(0o600)
    environment = {
        "BUFFALO_RUNTIME_MODE": "AUTOMATED_TEST",
        "BUFFALO_LOCAL_PORT": str(TEST_PORT),
        "BUFFALO_LOCAL_AUTH_SECRET_FILE": str(secret_file),
        "BUFFALO_LOCAL_PRINCIPAL_REF": "synthetic:http-test-owner:01",
        "BUFFALO_LOCAL_ROLE_REF": "LOCAL_TEST_OWNER",
        "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
    }
    environment_patch = patch.dict(os.environ, environment, clear=False)
    environment_patch.start()
    clear_local_sessions()
    client = loopback_test_client(app, origin=TEST_ORIGIN)
    response = client.post(
        "/auth/login",
        data={"secret": TEST_SECRET},
        follow_redirects=False,
    )
    if response.status_code != 303:
        client.close()
        clear_local_sessions()
        environment_patch.stop()
        temporary.cleanup()
        raise AssertionError(f"synthetic local login failed: {response.status_code}")

    def cleanup() -> None:
        client.close()
        clear_local_sessions()
        environment_patch.stop()
        temporary.cleanup()

    test_case.addCleanup(cleanup)
    return client
