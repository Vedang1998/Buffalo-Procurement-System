from __future__ import annotations

import unittest

from procurement_os.readiness import REQUIRED_PO_GATES, readiness_gate_blockers


def gate(name: str, status: str = "PASS", *, scope_type: str = "GLOBAL", scope_id: str = "") -> dict:
    return {
        "gate_name": name,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "status": status,
        "severity": "CRITICAL",
        "blocks_po": status == "FAIL",
        "message": "test",
        "evidence": {},
        "checked_at": None,
    }


class ReadinessGateSafetyTests(unittest.TestCase):
    def setUp(self):
        self.passing = [gate(name) for name in REQUIRED_PO_GATES]

    def test_every_required_global_gate_must_pass(self):
        self.assertEqual(readiness_gate_blockers(self.passing), [])
        for name in ("SALES_BACKFILL", "VENDOR_RULES"):
            with self.subTest(name=name):
                gates = [gate(row["gate_name"], "FAIL" if row["gate_name"] == name else "PASS") for row in self.passing]
                blockers = readiness_gate_blockers(gates)
                self.assertTrue(any(b["detail"]["gate_name"] == name for b in blockers))

    def test_warn_is_not_po_ready_even_if_legacy_blocks_flag_is_false(self):
        gates = [gate(row["gate_name"], "WARN" if row["gate_name"] == "PRICE_COVERAGE" else "PASS") for row in self.passing]
        gates[4]["blocks_po"] = False
        blockers = readiness_gate_blockers(gates)
        self.assertEqual(blockers[0]["type"], "REQUIRED_GATE_NOT_PASS")
        self.assertEqual(blockers[0]["detail"]["gate_name"], "PRICE_COVERAGE")

    def test_missing_gate_fails_closed(self):
        blockers = readiness_gate_blockers(
            [row for row in self.passing if row["gate_name"] != "OPEN_PO_RECONCILIATION"]
        )
        self.assertTrue(any(
            blocker["type"] == "MISSING_REQUIRED_GATE"
            and blocker["detail"]["gate_name"] == "OPEN_PO_RECONCILIATION"
            for blocker in blockers
        ))

    def test_scoped_failure_applies_only_to_matching_scope(self):
        gates = self.passing + [gate("MAPPING_INTEGRITY", "FAIL", scope_type="VENDOR", scope_id="vendor-a")]
        self.assertFalse(readiness_gate_blockers(gates, vendor_id="vendor-b"))
        blockers = readiness_gate_blockers(gates, vendor_id="vendor-a")
        self.assertEqual(blockers[0]["detail"]["scope_id"], "vendor-a")


if __name__ == "__main__":
    unittest.main()
