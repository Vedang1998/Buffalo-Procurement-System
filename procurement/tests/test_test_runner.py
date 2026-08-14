"""Self-tests for the fail-closed deterministic unittest runner."""

from __future__ import annotations

from collections import Counter
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


RUNNER_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_tests.py"
SPEC = importlib.util.spec_from_file_location("procurement_test_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def run_inner_test(case: type[unittest.TestCase]) -> runner.TestRunSummary:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
    result = unittest.TextTestRunner(
        stream=io.StringIO(), resultclass=runner.CountingTextTestResult
    ).run(suite)
    return runner._summarize_result(suite.countTestCases(), result)


class TestFailClosedResults(unittest.TestCase):
    def test_expected_failure_is_rejected_and_not_counted_as_pass(self):
        class ExpectedFailure(unittest.TestCase):
            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail("synthetic expected failure")

        summary = run_inner_test(ExpectedFailure)
        self.assertEqual(summary.expected_failures, 1)
        self.assertEqual(summary.passes, 0)
        self.assertFalse(summary.clean)

    def test_unexpected_success_is_rejected_and_not_counted_as_pass(self):
        class UnexpectedSuccess(unittest.TestCase):
            @unittest.expectedFailure
            def test_unexpected_success(self):
                pass

        summary = run_inner_test(UnexpectedSuccess)
        self.assertEqual(summary.unexpected_successes, 1)
        self.assertEqual(summary.passes, 0)
        self.assertFalse(summary.clean)

    def test_skipped_test_is_rejected_and_not_counted_as_pass(self):
        class Skipped(unittest.TestCase):
            @unittest.skip("synthetic skip")
            def test_skipped(self):
                pass

        summary = run_inner_test(Skipped)
        self.assertEqual(summary.skips, 1)
        self.assertEqual(summary.passes, 0)
        self.assertFalse(summary.clean)

    def test_pass_count_excludes_every_nonpass_result(self):
        class MixedResults(unittest.TestCase):
            def test_pass(self):
                pass

            def test_failure(self):
                self.fail("synthetic failure")

            def test_error(self):
                raise RuntimeError("synthetic error")

            @unittest.skip("synthetic skip")
            def test_skip(self):
                pass

            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail("synthetic expected failure")

            @unittest.expectedFailure
            def test_unexpected_success(self):
                pass

        summary = run_inner_test(MixedResults)
        self.assertEqual(summary.executed, 6)
        self.assertEqual(summary.passes, 1)
        self.assertEqual(
            (
                summary.failures,
                summary.errors,
                summary.skips,
                summary.expected_failures,
                summary.unexpected_successes,
            ),
            (1, 1, 1, 1, 1),
        )
        self.assertFalse(summary.clean)

    def test_multiple_subtest_failures_do_not_make_pass_count_negative(self):
        class FailedSubtests(unittest.TestCase):
            def test_subtests(self):
                for value in (1, 2):
                    with self.subTest(value=value):
                        self.fail("synthetic subtest failure")

        summary = run_inner_test(FailedSubtests)
        self.assertEqual(summary.executed, 1)
        self.assertEqual(summary.failures, 2)
        self.assertEqual(summary.passes, 0)
        self.assertFalse(summary.clean)

    def test_discovery_execution_mismatch_is_rejected(self):
        class Passing(unittest.TestCase):
            def test_pass(self):
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Passing)
        result = unittest.TextTestRunner(
            stream=io.StringIO(), resultclass=runner.CountingTextTestResult
        ).run(suite)
        summary = runner._summarize_result(2, result)
        self.assertEqual(summary.executed, 1)
        self.assertFalse(summary.clean)


class TestModuleMinimums(unittest.TestCase):
    def test_unregistered_on_disk_module_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            tests_dir = Path(temporary_directory)
            (tests_dir / "test_registered.py").touch()
            (tests_dir / "test_unregistered.py").touch()

            errors = runner._module_registration_errors(
                tests_dir, {"test_registered.py": 1}
            )
            registered_errors = runner._module_registration_errors(
                tests_dir,
                {"test_registered.py": 1, "test_unregistered.py": 1},
            )

        self.assertEqual(errors, ["unregistered test module: test_unregistered.py"])
        self.assertEqual(registered_errors, [])

    def test_missing_required_module_is_rejected(self):
        errors = runner._module_minimum_errors(
            Counter({"test_present.py": 1}),
            {"test_present.py": 1, "test_missing.py": 1},
        )
        self.assertEqual(errors, ["required test module missing or empty: test_missing.py"])

    def test_per_module_count_deficiency_is_rejected(self):
        errors = runner._module_minimum_errors(
            Counter({"test_required.py": 2}), {"test_required.py": 3}
        )
        self.assertEqual(
            errors,
            ["test_required.py discovered 2 tests; required minimum is 3"],
        )

    def test_new_tests_in_registered_module_above_lower_bound_are_allowed(self):
        errors = runner._module_minimum_errors(
            Counter({"test_required.py": 4}),
            {"test_required.py": 3},
        )
        self.assertEqual(errors, [])


class TestDatabaseUrlSafety(unittest.TestCase):
    def test_safe_loopback_test_database_is_accepted(self):
        target = runner._validated_test_database_target(
            "postgresql://test:test@127.0.0.1:5432/procurement_test"
        )
        self.assertEqual(target.database, "procurement_test")

    def test_non_loopback_database_url_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            runner._validated_test_database_target(
                "postgresql://test:test@database.example/procurement_test"
            )

    def test_non_test_database_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "end in '_test'"):
            runner._validated_test_database_target(
                "postgresql://test:test@127.0.0.1:5432/procurement"
            )

    def test_query_and_libpq_path_parameters_are_rejected(self):
        unsafe_urls = (
            "postgresql://test:test@127.0.0.1:5432/procurement_test?host=evil",
            "postgresql://test:test@127.0.0.1:5432/procurement_test;host=evil",
        )
        for url in unsafe_urls:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "parameters"):
                runner._validated_test_database_target(url)

    def test_runtime_database_url_is_never_a_fallback(self):
        with mock.patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://production.example/procurement"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "TEST_DATABASE_URL is required"):
                runner._validated_test_database_target()


class TestConnectedDatabaseSafety(unittest.TestCase):
    def setUp(self):
        self.target = runner.TestDatabaseTarget(
            "postgresql://test:test@127.0.0.1:5432/procurement_test",
            "procurement_test",
        )

    def test_current_database_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "current_database"):
            runner._validate_database_facts(
                self.target,
                database="production",
                server_version="16.14",
                server_version_num=160014,
            )

    def test_libpq_redirect_environment_is_scrubbed(self):
        with mock.patch.dict(
            os.environ,
            {"PGHOST": "production.example", "PGSERVICE": "production", "SAFE": "yes"},
            clear=True,
        ):
            runner._clear_libpq_environment()
            self.assertNotIn("PGHOST", os.environ)
            self.assertNotIn("PGSERVICE", os.environ)
            self.assertEqual(os.environ["SAFE"], "yes")

    def test_postgresql_major_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "PostgreSQL 16 is required"):
            runner._validate_database_facts(
                self.target,
                database="procurement_test",
                server_version="17.5",
                server_version_num=170005,
            )


if __name__ == "__main__":
    unittest.main()
