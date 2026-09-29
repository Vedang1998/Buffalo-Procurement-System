"""Fail-closed Railway staging configuration tests."""
from __future__ import annotations

from pathlib import Path
import unittest

from procurement_os.staging_access import generate_owner_credential
from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID,
    EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID,
    EXPECTED_PROJECT_ID,
    StagingConfigError,
    load_staging_config,
)


class StagingConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {
            "BUFFALO_STAGING_ENABLED": "1",
            "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
            "BUFFALO_STAGING_EXTERNAL_HOST": "buffalo-staging.example.test",
            "BUFFALO_STAGING_OWNER_VERIFIER": generate_owner_credential().verifier,
            "BUFFALO_STAGING_VOLUME_ROOT": "/data",
            "BUFFALO_STAGING_EXPECTED_COMMIT": "a" * 40,
            "RAILWAY_GIT_COMMIT_SHA": "a" * 40,
            "RAILWAY_PROJECT_ID": EXPECTED_PROJECT_ID,
            "RAILWAY_ENVIRONMENT_ID": EXPECTED_ENVIRONMENT_ID,
            "RAILWAY_SERVICE_ID": EXPECTED_APP_SERVICE_ID,
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID": EXPECTED_POSTGRES_SERVICE_ID,
            "RAILWAY_REPLICA_ID": "replica-01",
            "PORT": "8080",
        }

    def test_exact_scope_and_external_origin_are_loaded(self):
        config = load_staging_config(self.environment)
        self.assertEqual(config.external_host, "buffalo-staging.example.test")
        self.assertEqual(
            config.external_origin, "https://buffalo-staging.example.test"
        )
        self.assertEqual(config.port, 8080)
        self.assertEqual(config.volume_root, Path("/data"))
        self.assertEqual(config.expected_source_commit, "a" * 40)
        self.assertNotIn(config.owner_verifier, repr(config))

    def test_missing_partial_or_mixed_railway_scope_is_refused(self):
        for key in (
            "RAILWAY_PROJECT_ID",
            "RAILWAY_ENVIRONMENT_ID",
            "RAILWAY_SERVICE_ID",
            "BUFFALO_STAGING_POSTGRES_SERVICE_ID",
            "RAILWAY_REPLICA_ID",
        ):
            with self.subTest(key=key):
                values = dict(self.environment)
                values.pop(key)
                with self.assertRaises(StagingConfigError):
                    load_staging_config(values)
        values = dict(self.environment, RAILWAY_SERVICE_ID="wrong-service")
        with self.assertRaisesRegex(StagingConfigError, "scope"):
            load_staging_config(values)

    def test_runtime_mode_and_explicit_enablement_are_required(self):
        for changes in (
            {"BUFFALO_STAGING_ENABLED": "0"},
            {"BUFFALO_RUNTIME_MODE": "AUTOMATED_TEST"},
            {"BUFFALO_RUNTIME_MODE": "PRIVATE_REAL_SOURCE_REVIEW"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(StagingConfigError):
                    load_staging_config(dict(self.environment, **changes))

    def test_host_port_volume_and_source_identity_are_strict(self):
        cases = (
            {"BUFFALO_STAGING_EXTERNAL_HOST": "https://example.test"},
            {"BUFFALO_STAGING_EXTERNAL_HOST": "example.test/path"},
            {"BUFFALO_STAGING_EXTERNAL_HOST": "localhost"},
            {"PORT": "0"},
            {"PORT": "not-a-port"},
            {"BUFFALO_STAGING_VOLUME_ROOT": "relative/data"},
            {"BUFFALO_STAGING_VOLUME_ROOT": "/"},
            {"RAILWAY_GIT_COMMIT_SHA": "b" * 40},
            {"BUFFALO_STAGING_EXPECTED_COMMIT": "not-a-commit"},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(
                StagingConfigError
            ):
                load_staging_config(dict(self.environment, **changes))

    def test_production_or_shopify_credentials_are_refused_from_gateway_scope(self):
        for key in (
            "DATABASE_URL",
            "DATABASE_PRIVATE_URL",
            "PGPASSWORD",
            "SHOPIFY_ACCESS_TOKEN",
            "SHOPIFY_ADMIN_ACCESS_TOKEN",
            "SHOPIFY_CLIENT_SECRET",
            "SHOPIFY_API_PASSWORD",
            "BUFFALO_RESEARCH_ROOT",
            "GH_TOKEN",
            "SSH_AUTH_SOCK",
            "UNEXPECTED_UNSCOPED_VALUE",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                StagingConfigError, "gateway process environment"
            ):
                load_staging_config(dict(self.environment, **{key: "secret"}))


if __name__ == "__main__":
    unittest.main()
