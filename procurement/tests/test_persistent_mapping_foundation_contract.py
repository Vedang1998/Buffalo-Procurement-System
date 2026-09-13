"""Pure/static acceptance checks for the persistent-mapping authority boundary."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROCUREMENT = ROOT / "procurement"
SERVICE = PROCUREMENT / "src" / "procurement_os" / "persistent_mapping.py"
MIGRATION = PROCUREMENT / "db" / "014_persistent_mapping_foundation.sql"
RULES = PROCUREMENT / "config" / "rules.toml"
RUNNER_PATH = PROCUREMENT / "tools" / "run_tests.py"
EXPECTED_GLOBAL_TEST_POPULATION = 787


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
