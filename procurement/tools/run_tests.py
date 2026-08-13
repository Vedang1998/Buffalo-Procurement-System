#!/usr/bin/env python3
"""Fail-closed deterministic Procurement OS unittest entrypoint."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from urllib.parse import urlparse


MINIMUM_EXPECTED_TESTS = 159
EXPECTED_TEST_MODULES = {
    "test_assortment.py",
    "test_catalog.py",
    "test_catalog_reconciliation_phase3.py",
    "test_economics.py",
    "test_historical_sales_review_api.py",
    "test_identity_investigation.py",
    "test_matching.py",
    "test_phase4_historical_sales.py",
    "test_phase4_postgres_integration.py",
    "test_phase4_query_contracts.py",
    "test_pricing.py",
    "test_readiness.py",
    "test_review.py",
    "test_safety_contracts.py",
    "test_sales.py",
    "test_schema_migrations.py",
    "test_shopify_auth.py",
    "test_shopify_queries.py",
    "test_storage.py",
}


def _validated_test_database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        raise SystemExit(
            "TEST_DATABASE_URL is required; use scripts/procurement-tests to create "
            "a disposable local PostgreSQL instance"
        )
    parsed = urlparse(value)
    database = parsed.path.removeprefix("/")
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise SystemExit("TEST_DATABASE_URL must be a PostgreSQL URL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("TEST_DATABASE_URL must target loopback disposable infrastructure")
    if not database.endswith("_test"):
        raise SystemExit("TEST_DATABASE_URL database name must end in '_test'")
    return value


def main() -> int:
    procurement_root = Path(__file__).resolve().parents[1]
    tests_dir = procurement_root / "tests"
    src_dir = procurement_root / "src"
    sys.path.insert(0, str(src_dir))

    # The suite's integration tests read DATABASE_URL. Only the separately named,
    # locally validated test URL may be passed through to them.
    os.environ["DATABASE_URL"] = _validated_test_database_url()

    modules = {path.name for path in tests_dir.glob("test_*.py")}
    missing_modules = sorted(EXPECTED_TEST_MODULES - modules)
    if missing_modules:
        print(f"ERROR: expected test modules missing: {', '.join(missing_modules)}", file=sys.stderr)
        return 2

    suite = unittest.defaultTestLoader.discover(
        start_dir=str(tests_dir),
        pattern="test_*.py",
        top_level_dir=str(tests_dir),
    )
    discovered = suite.countTestCases()
    print(f"Procurement OS tests discovered: {discovered}", flush=True)
    if discovered < MINIMUM_EXPECTED_TESTS:
        print(
            f"ERROR: discovered {discovered} tests; baseline floor is {MINIMUM_EXPECTED_TESTS}",
            file=sys.stderr,
        )
        return 2

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failures = len(result.failures)
    errors = len(result.errors)
    skips = len(result.skipped)
    passes = result.testsRun - failures - errors - skips
    print(
        "Procurement OS test summary: "
        f"discovered={discovered} executed={result.testsRun} passes={passes} "
        f"failures={failures} errors={errors} skips={skips}",
        flush=True,
    )
    if result.testsRun != discovered:
        print("ERROR: discovered and executed test counts differ", file=sys.stderr)
        return 1
    if skips:
        print("ERROR: deterministic suite permits no skipped tests", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
