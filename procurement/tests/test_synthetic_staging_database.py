from __future__ import annotations

from dataclasses import replace
import unittest

from procurement_os.staging_config import (
    EXPECTED_APP_SERVICE_ID, EXPECTED_ENVIRONMENT_ID,
    EXPECTED_POSTGRES_SERVICE_ID, EXPECTED_PROJECT_ID,
)
from procurement_os.synthetic_staging_database import (
    EXPECTED_DATABASE, PERMISSION_MATRIX_SHA256, RUNTIME_LOGIN,
    SyntheticStagingDatabaseError, SyntheticStagingTarget,
    permission_records,
)


class SyntheticStagingDatabaseContractTests(unittest.TestCase):
    def target(self):
        return SyntheticStagingTarget(
            database_url=f"postgresql://{RUNTIME_LOGIN}@postgres.railway.internal/{EXPECTED_DATABASE}",
            expected_private_host="postgres.railway.internal",
            project_id=EXPECTED_PROJECT_ID,
            environment_id=EXPECTED_ENVIRONMENT_ID,
            app_service_id=EXPECTED_APP_SERVICE_ID,
            postgres_service_id=EXPECTED_POSTGRES_SERVICE_ID,
        )

    def test_exact_target_is_accepted_without_connecting(self):
        self.target().validate_static()
        self.assertRegex(PERMISSION_MATRIX_SHA256, r"^[0-9a-f]{64}$")

    def test_wrong_scope_host_database_and_login_are_refused(self):
        changes = (
            {"project_id": "wrong"},
            {"expected_private_host": "evil.example"},
            {"database_url": "postgresql://buffalo_synthetic_runtime:x@postgres.railway.internal/production"},
            {"database_url": f"postgresql://admin:x@postgres.railway.internal/{EXPECTED_DATABASE}"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(SyntheticStagingDatabaseError):
                replace(self.target(), **change).validate_static()

    def test_permission_matrix_is_explicit_and_default_denies_admin_operations(self):
        records = permission_records()
        self.assertTrue(records)
        self.assertEqual(len(records), len({(r["object"], r["operation"]) for r in records}))
        self.assertFalse({"CREATE", "TEMP", "TRUNCATE", "TRIGGER", "GRANT"} & {r["operation"] for r in records})

    def test_research_and_gateway_contracts_have_no_database_credential(self):
        from procurement_os.staging_process_contract import GATEWAY_ENVIRONMENT_NAMES, WORKER_ENVIRONMENT_NAMES
        for names in (GATEWAY_ENVIRONMENT_NAMES, WORKER_ENVIRONMENT_NAMES["research"]):
            self.assertNotIn("DATABASE_URL", names)
            self.assertNotIn("PGPASSFILE", names)


if __name__ == "__main__":
    unittest.main()
