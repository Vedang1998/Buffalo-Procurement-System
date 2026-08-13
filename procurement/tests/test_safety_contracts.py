from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest

from fastapi import HTTPException

from procurement_os import api
from procurement_os.config import load_rules


ROOT = Path(__file__).resolve().parents[1]


class PermanentSafetyContractTests(unittest.TestCase):
    def test_price_rollover_api_is_fail_closed_before_database_access(self):
        with self.assertRaises(HTTPException) as ctx:
            api.pricing_rollover(api.RolloverRequest(as_of=date(2026, 9, 1)))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_no_po_generation_or_release_route_is_registered(self):
        paths = {route.path.casefold() for route in api.app.routes}
        self.assertFalse(any("purchase-order" in path or path.startswith("/po") for path in paths))

    def test_schema_enforces_one_po_per_vendor_per_run(self):
        schema = (ROOT / "db" / "schema_postgres.sql").read_text(encoding="utf-8")
        normalized = " ".join(schema.split()).casefold()
        self.assertIn("unique(run_id,vendor_id)", normalized)

    def test_locked_rules_keep_consequential_automation_off(self):
        rules = load_rules()
        self.assertTrue(rules["strategy"]["one_vendor_one_po"])
        self.assertFalse(rules["strategy"]["selling_price_auto_update"])
        self.assertFalse(rules["combos"]["auto_add"])
        self.assertTrue(rules["combos"]["human_approval_required"])
        self.assertFalse(rules["vendor_minimums"]["block_po"])
        self.assertFalse(rules["allocated"]["auto_replenish"])
        self.assertTrue(rules["po"]["procurement_po_ledger_required"])


if __name__ == "__main__":
    unittest.main()
