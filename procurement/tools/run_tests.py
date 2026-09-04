#!/usr/bin/env python3
"""Fail-closed deterministic Procurement OS unittest entrypoint."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Iterable
import unittest
from urllib.parse import unquote, urlparse


REQUIRED_PYTHON = (3, 13)
REQUIRED_POSTGRESQL_MAJOR = 16

# These are checkpoint floors set to every currently registered test in each
# module. New tests remain discoverable, while deleting, renaming, or hiding a
# checkpoint test cannot be concealed by growth in a different module.
REQUIRED_MODULE_MINIMUMS = {
    "test_assortment.py": 4,
    "test_catalog.py": 7,
    "test_catalog_readiness.py": 14,
    "test_catalog_reconciliation_phase3.py": 12,
    "test_economics.py": 4,
    "test_historical_sales_review_api.py": 17,
    "test_identity_investigation.py": 32,
    "test_matching.py": 3,
    "test_phase4_historical_sales.py": 33,
    "test_phase4_identity_manifest.py": 16,
    "test_phase4_identity_manifest_postgres.py": 24,
    "test_phase4_postgres_integration.py": 6,
    "test_phase4_query_contracts.py": 5,
    "test_phase4_terminal_disposition.py": 23,
    "test_phase4_terminal_disposition_postgres.py": 35,
    "test_phase5_foundation_ui.py": 14,
    "test_pricing.py": 3,
    "test_readiness.py": 19,
    "test_review.py": 3,
    "test_sales.py": 18,
    "test_shopify_auth.py": 3,
    "test_shopify_queries.py": 1,
    "test_storage.py": 5,
    "test_test_runner.py": 18,
}
GLOBAL_MINIMUM_TESTS = sum(REQUIRED_MODULE_MINIMUMS.values())


@dataclass(frozen=True)
class TestDatabaseTarget:
    url: str
    database: str


@dataclass(frozen=True)
class TestDatabaseInfo:
    database: str
    server_version: str
    server_major: int


@dataclass(frozen=True)
class TestRunSummary:
    discovered: int
    executed: int
    passes: int
    failures: int
    errors: int
    skips: int
    expected_failures: int
    unexpected_successes: int

    @property
    def clean(self) -> bool:
        return (
            self.executed == self.discovered
            and self.passes >= 0
            and self.passes == self.executed
            and self.failures == 0
            and self.errors == 0
            and self.skips == 0
            and self.expected_failures == 0
            and self.unexpected_successes == 0
        )


class CountingTextTestResult(unittest.TextTestResult):
    """Track actual successful TestCase executions without arithmetic guesses."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.successes = 0

    def addSuccess(self, test: unittest.TestCase) -> None:  # noqa: N802
        self.successes += 1
        super().addSuccess(test)


def _validated_test_database_target(value: str | None = None) -> TestDatabaseTarget:
    if value is None:
        value = os.getenv("TEST_DATABASE_URL")
    if not value:
        raise ValueError(
            "TEST_DATABASE_URL is required; use scripts/procurement-tests to create "
            "disposable local PostgreSQL 16 infrastructure"
        )

    parsed = urlparse(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("TEST_DATABASE_URL must be a PostgreSQL URL")
    if (
        parsed.params
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or ";" in value
    ):
        raise ValueError("TEST_DATABASE_URL must not contain parameters, a query, or a fragment")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("TEST_DATABASE_URL must target loopback disposable infrastructure")
    if not parsed.path.startswith("/") or parsed.path.count("/") != 1:
        raise ValueError("TEST_DATABASE_URL must name exactly one database")

    database = unquote(parsed.path[1:])
    if (
        not database
        or not all(character.isalnum() or character in "_-" for character in database)
        or not database.endswith("_test")
    ):
        raise ValueError("TEST_DATABASE_URL database name must end in '_test'")
    return TestDatabaseTarget(url=value, database=database)


def _clear_libpq_environment() -> None:
    """Prevent libpq environment settings from redirecting the explicit test URL."""

    for name in tuple(os.environ):
        if name.startswith("PG"):
            os.environ.pop(name, None)


def _validate_database_facts(
    target: TestDatabaseTarget,
    *,
    database: str,
    server_version: str,
    server_version_num: int,
) -> TestDatabaseInfo:
    if database != target.database or not database.endswith("_test"):
        raise ValueError(
            "connected current_database() does not match the validated TEST_DATABASE_URL"
        )
    server_major = server_version_num // 10000
    if server_major != REQUIRED_POSTGRESQL_MAJOR:
        raise ValueError(
            f"PostgreSQL {REQUIRED_POSTGRESQL_MAJOR} is required; "
            f"connected server is {server_version}"
        )
    return TestDatabaseInfo(
        database=database,
        server_version=server_version,
        server_major=server_major,
    )


def _validate_test_database(target: TestDatabaseTarget) -> TestDatabaseInfo:
    import psycopg

    try:
        with psycopg.connect(target.url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT current_database(), current_setting('server_version'),
                              current_setting('server_version_num')::integer"""
                )
                row = cursor.fetchone()
    except psycopg.Error as exc:
        raise ValueError(
            f"PostgreSQL connection or identity query failed ({type(exc).__name__})"
        ) from exc
    if row is None:
        raise ValueError("PostgreSQL identity query returned no row")
    return _validate_database_facts(
        target,
        database=row[0],
        server_version=row[1],
        server_version_num=row[2],
    )


def _iter_tests(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def _module_test_counts(suite: unittest.TestSuite) -> Counter[str]:
    counts: Counter[str] = Counter()
    for test in _iter_tests(suite):
        module = test.id().split(".", 1)[0]
        counts[f"{module}.py"] += 1
    return counts


def _module_registration_errors(
    tests_dir: Path,
    required: dict[str, int] = REQUIRED_MODULE_MINIMUMS,
) -> list[str]:
    on_disk_modules = {
        path.name for path in tests_dir.glob("test_*.py") if path.is_file()
    }
    return [
        f"unregistered test module: {module}"
        for module in sorted(on_disk_modules - required.keys())
    ]


def _module_minimum_errors(
    actual: Counter[str] | dict[str, int],
    required: dict[str, int] = REQUIRED_MODULE_MINIMUMS,
) -> list[str]:
    errors = []
    for module, minimum in sorted(required.items()):
        count = actual.get(module, 0)
        if count == 0:
            errors.append(f"required test module missing or empty: {module}")
        elif count < minimum:
            errors.append(
                f"{module} discovered {count} tests; required minimum is {minimum}"
            )
    return errors


def _summarize_result(discovered: int, result: unittest.TestResult) -> TestRunSummary:
    failures = len(result.failures)
    errors = len(result.errors)
    skips = len(result.skipped)
    expected_failures = len(result.expectedFailures)
    unexpected_successes = len(result.unexpectedSuccesses)
    passes = getattr(result, "successes", 0)
    return TestRunSummary(
        discovered=discovered,
        executed=result.testsRun,
        passes=passes,
        failures=failures,
        errors=errors,
        skips=skips,
        expected_failures=expected_failures,
        unexpected_successes=unexpected_successes,
    )


def main() -> int:
    if sys.version_info[:2] != REQUIRED_PYTHON:
        print(
            f"ERROR: Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]} is required; "
            f"running {sys.version_info.major}.{sys.version_info.minor}",
            file=sys.stderr,
        )
        return 2

    try:
        database_target = _validated_test_database_target()
        _clear_libpq_environment()
        database_info = _validate_test_database(database_target)
    except (ValueError, OSError) as exc:
        print(f"ERROR: unsafe or unavailable test database: {exc}", file=sys.stderr)
        return 2

    # The suite's integration tests read DATABASE_URL. Only the separately
    # named and independently validated test URL may be passed through.
    os.environ["DATABASE_URL"] = database_target.url
    print(
        "Procurement OS runtime verified: "
        f"python={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} "
        f"postgresql={database_info.server_version} "
        f"database={database_info.database} loopback=verified",
        flush=True,
    )

    procurement_root = Path(__file__).resolve().parents[1]
    tests_dir = procurement_root / "tests"
    src_dir = procurement_root / "src"
    sys.path.insert(0, str(src_dir))

    suite = unittest.defaultTestLoader.discover(
        start_dir=str(tests_dir),
        pattern="test_*.py",
        top_level_dir=str(tests_dir),
    )
    discovered = suite.countTestCases()
    module_counts = _module_test_counts(suite)
    print(f"Procurement OS tests discovered: {discovered}", flush=True)

    module_errors = _module_registration_errors(tests_dir)
    module_errors.extend(_module_minimum_errors(module_counts))
    if discovered < GLOBAL_MINIMUM_TESTS:
        module_errors.append(
            f"global discovered count {discovered} is below floor {GLOBAL_MINIMUM_TESTS}"
        )
    if module_errors:
        for error in module_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2

    result = unittest.TextTestRunner(
        stream=sys.stdout,
        verbosity=2,
        resultclass=CountingTextTestResult,
    ).run(suite)
    summary = _summarize_result(discovered, result)
    print(
        "Procurement OS test summary: "
        f"discovered={summary.discovered} executed={summary.executed} "
        f"passes={summary.passes} failures={summary.failures} "
        f"errors={summary.errors} skips={summary.skips} "
        f"expected_failures={summary.expected_failures} "
        f"unexpected_successes={summary.unexpected_successes}",
        flush=True,
    )
    if summary.executed != summary.discovered:
        print("ERROR: discovered and executed test counts differ", file=sys.stderr)
    if summary.skips:
        print("ERROR: deterministic suite permits no skipped tests", file=sys.stderr)
    if summary.expected_failures:
        print("ERROR: deterministic suite permits no expected failures", file=sys.stderr)
    if summary.unexpected_successes:
        print("ERROR: deterministic suite permits no unexpected successes", file=sys.stderr)
    return 0 if summary.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
