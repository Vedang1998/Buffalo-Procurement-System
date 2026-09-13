"""Pure/static acceptance checks for the persistent-mapping authority boundary."""
from __future__ import annotations

import ast
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import sys
import tomllib
import unittest
from unittest import mock
from uuid import uuid4

import psycopg


ROOT = Path(__file__).resolve().parents[2]
PROCUREMENT = ROOT / "procurement"
SERVICE = PROCUREMENT / "src" / "procurement_os" / "persistent_mapping.py"
MIGRATION = PROCUREMENT / "db" / "014_persistent_mapping_foundation.sql"
RULES = PROCUREMENT / "config" / "rules.toml"
RUNNER_PATH = PROCUREMENT / "tools" / "run_tests.py"
EXPECTED_GLOBAL_TEST_POPULATION = 788


class PersistentMappingFoundationContractTests(unittest.TestCase):
    def test_mapping_foundation_has_no_shopify_price_order_supplier_or_artifact_mutator(self):
        tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
        imported_roots = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertTrue(
            imported_roots.isdisjoint(
                {"shopify", "draft_po", "recommendations", "emergency_packet"}
            )
        )
        source = SERVICE.read_text(encoding="utf-8")
        for forbidden in (
            "shopify_client.",
            "build_vendor_drafts(",
            "prepare_monday_run(",
            "send_supplier",
            "release_purchase_order",
        ):
            self.assertNotIn(forbidden, source)
        sql_source = MIGRATION.read_text(encoding="utf-8").lower()
        for forbidden in (
            "insert into \"qa_mapping_test\".prices",
            "insert into \"qa_mapping_test\".purchase_orders",
            "insert into \"qa_mapping_test\".procurement_recommendations",
            "update \"qa_mapping_test\".prices set",
            "update \"qa_mapping_test\".supplier_offers set active",
        ):
            self.assertNotIn(forbidden, sql_source)

        import procurement_os.persistent_mapping as mapping_service

        external_calls: list[str] = []

        def forbidden_call(name):
            def invoke(*_args, **_kwargs):
                external_calls.append(name)
                raise AssertionError(f"external mutator was called: {name}")

            return invoke

        class Result:
            def __init__(self, value=True):
                self.value = value

            def fetchone(self):
                return self.value if isinstance(self.value, tuple) else (self.value,)

        class Info:
            transaction_status = type("Status", (), {"name": "IDLE"})()

        class Connection:
            def __init__(self, *, commit_error=None):
                self.commit_error = commit_error
                self.closed = False
                self.info = Info()
                self.statements: list[str] = []

            def execute(self, statement, _parameters=()):
                rendered = str(statement)
                self.statements.append(rendered)
                if "current_database" in rendered:
                    return Result(
                        (
                            "procurement_test",
                            "127.0.0.1/32",
                            160009,
                            "qa_release_login",
                            "qa_mapping_owner",
                        )
                    )
                return Result(True)

            def commit(self):
                if self.commit_error is not None:
                    error, self.commit_error = self.commit_error, None
                    raise error

            def rollback(self):
                return None

            def close(self):
                self.closed = True

        principal = mapping_service.Principal(
            "synthetic:pure-boundary:01",
            "procurement.mapping.approve",
            "a" * 64,
        )
        connections: list[Connection] = []

        def run(*, operation, recovery_lookup=None, connect_side_effect=None):
            side_effect = connect_side_effect or [Connection()]
            connections.extend(side_effect)
            with mock.patch.object(psycopg, "connect", side_effect=side_effect):
                return mapping_service._execute_write(
                    "postgresql://qa_release_login@127.0.0.1:5432/"
                    "procurement_test?options=-c%20role%3Dqa_mapping_owner%20-c%20"
                    "search_path%3Dqa_mapping_test,pg_catalog",
                    operation_name="pure-zero-external-effects",
                    capability="human_mapping_writes_enabled",
                    principal=principal,
                    idempotency_key=uuid4(),
                    domain_locks=lambda _conn: ((0, "pure-domain"),),
                    operation=operation,
                    recovery_lookup=recovery_lookup,
                )

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    mapping_service,
                    "require_synthetic_mapping_capability",
                    return_value=None,
                )
            )
            for seam in (
                "shopify_query",
                "promote_price_book",
                "prepare_monday_run",
                "build_vendor_drafts",
                "release_purchase_order",
                "send_supplier",
                "build_emergency_packet",
                "put_artifact_bytes",
                "run_subprocess",
                "write_file",
            ):
                stack.enter_context(
                    mock.patch.object(
                        mapping_service, seam, forbidden_call(seam), create=True
                    )
                )

            self.assertEqual(run(operation=lambda _conn: {"ok": True}), {"ok": True})
            self.assertEqual(
                run(operation=lambda _conn: {"replayed": True}),
                {"replayed": True},
            )
            with self.assertRaises(mapping_service.PersistentMappingError):
                run(
                    operation=lambda _conn: (_ for _ in ()).throw(
                        mapping_service.PersistentMappingError("validation refused")
                    )
                )
            retry_calls: list[bool] = []

            def retry_once(_conn):
                retry_calls.append(True)
                if len(retry_calls) == 1:
                    raise psycopg.errors.SerializationFailure("pure retry")
                return {"retried": True}

            self.assertEqual(run(operation=retry_once), {"retried": True})
            first = Connection(commit_error=psycopg.OperationalError("lost commit"))
            recovered_connection = Connection()
            self.assertEqual(
                run(
                    operation=lambda _conn: {"created": True},
                    recovery_lookup=lambda _conn: {"replayed": True},
                    connect_side_effect=[first, recovered_connection],
                ),
                {"replayed": True},
            )
            unknown_first = Connection(
                commit_error=psycopg.OperationalError("lost commit")
            )
            unknown_recovery = Connection()
            with self.assertRaises(mapping_service.PersistentMappingError) as unknown:
                run(
                    operation=lambda _conn: {"created": True},
                    connect_side_effect=[unknown_first, unknown_recovery],
                )
            self.assertEqual(unknown.exception.code, "COMMIT_OUTCOME_UNKNOWN")

        self.assertEqual(external_calls, [])
        recorded_sql = "\n".join(
            statement.lower()
            for connection in connections
            for statement in connection.statements
        )
        for forbidden in (
            "insert into prices",
            "update prices",
            "purchase_orders",
            "procurement_recommendations",
            "monday_run_artifacts",
        ):
            self.assertNotIn(forbidden, recorded_sql)

    def test_mapping_authority_and_disabled_flags_match_approved_contract(self):
        rules = tomllib.loads(RULES.read_text(encoding="utf-8"))
        self.assertEqual(
            rules["persistent_mapping"],
            {
                "contract_version": "v1-shadow-only",
                "routine_selection_scope": "ROUTINE_PROCUREMENT_STANDARD",
                "review_intake_writes_enabled": False,
                "human_mapping_writes_enabled": False,
                "policy_mapping_writes_enabled": False,
                "routine_selection_writes_enabled": False,
                "selected_offer_shadow_reads_enabled": False,
                "recommendation_cutover_enabled": False,
                "offer_activation_enabled": False,
            },
        )
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertEqual(migration.count("CREATE TABLE IF NOT EXISTS \"qa_mapping_test\".supplier_mapping_"), 3)
        self.assertIn(
            'CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_offer_selection_events',
            migration,
        )
        self.assertIn(
            'CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_offer_selection_heads',
            migration,
        )
        self.assertEqual(migration.count('CREATE OR REPLACE VIEW "qa_mapping_test".'), 4)
        self.assertIn("recommendation_cutover_enabled", migration)

    def test_persistent_mapping_modules_are_registered_at_exact_discovery_floors(self):
        spec = importlib.util.spec_from_file_location("mapping_matrix_runner", RUNNER_PATH)
        assert spec is not None and spec.loader is not None
        runner = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runner
        spec.loader.exec_module(runner)
        self.assertEqual(
            runner.REQUIRED_MODULE_MINIMUMS["test_persistent_mapping_foundation_postgres.py"],
            39,
        )
        self.assertEqual(
            runner.REQUIRED_MODULE_MINIMUMS["test_persistent_mapping_foundation_contract.py"],
            3,
        )
        self.assertEqual(runner.GLOBAL_MINIMUM_TESTS, EXPECTED_GLOBAL_TEST_POPULATION)
        self.assertEqual(
            sum(runner.REQUIRED_MODULE_MINIMUMS.values()),
            EXPECTED_GLOBAL_TEST_POPULATION,
        )
        altered = dict(runner.REQUIRED_MODULE_MINIMUMS)
        altered["test_sales.py"] -= 1
        self.assertNotEqual(sum(altered.values()), EXPECTED_GLOBAL_TEST_POPULATION)


if __name__ == "__main__":
    unittest.main()
