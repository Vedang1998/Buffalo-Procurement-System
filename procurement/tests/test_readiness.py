"""Owner-approved F4 scope-aware readiness and exception semantics."""

from __future__ import annotations

import unittest

from procurement_os.readiness import exception_blockers, readiness_gate_blockers


def gate(
    name: str,
    status: str = "PASS",
    *,
    scope_type: str = "GLOBAL",
    scope_id: str = "",
    blocks_po: bool = True,
) -> dict:
    return {
        "gate_name": name,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "status": status,
        "severity": "HIGH",
        "blocks_po": blocks_po,
        "message": "fixture",
        "evidence": {},
        "checked_at": None,
    }


def foundation_passes() -> list[dict]:
    return [gate("CATALOG_SYNC"), gate("SALES_BACKFILL")]


def exception(
    *,
    severity: str = "HIGH",
    vendor_id: str | None = None,
    variant_id: str | None = None,
    run_id: str | None = None,
    status: str = "OPEN",
) -> dict:
    return {
        "exception_id": 1,
        "severity": severity,
        "exception_type": "FIXTURE",
        "message": "fixture",
        "vendor_id": vendor_id,
        "variant_id": variant_id,
        "run_id": run_id,
        "status": status,
    }


class ReadinessScopeTests(unittest.TestCase):
    def test_global_fail_blocks_every_scope(self):
        gates = [gate("CATALOG_SYNC", "FAIL"), gate("SALES_BACKFILL")]
        for scope in ({}, {"vendor_id": "A"}, {"variant_id": "X"}):
            with self.subTest(scope=scope):
                blockers = readiness_gate_blockers(gates, **scope)
                self.assertEqual(blockers[0]["detail"]["gate_name"], "CATALOG_SYNC")

    def test_global_warn_is_non_blocking(self):
        gates = [gate("CATALOG_SYNC", "WARN"), gate("SALES_BACKFILL")]
        self.assertEqual(readiness_gate_blockers(gates), [])

    def test_vendor_fail_isolated_to_matching_vendor(self):
        gates = foundation_passes() + [
            gate("PRICE_COVERAGE", "FAIL", scope_type="VENDOR", scope_id="A")
        ]
        self.assertTrue(readiness_gate_blockers(gates, vendor_id="A"))
        self.assertEqual(readiness_gate_blockers(gates, vendor_id="B"), [])

    def test_variant_fail_isolated_to_matching_variant(self):
        gates = foundation_passes() + [
            gate("MAPPING_INTEGRITY", "FAIL", scope_type="VARIANT", scope_id="X")
        ]
        self.assertTrue(readiness_gate_blockers(gates, variant_id="X"))
        self.assertEqual(readiness_gate_blockers(gates, variant_id="Y"), [])

    def test_scoped_warn_never_blocks_even_when_declared_or_blocks_po(self):
        gates = foundation_passes() + [
            gate("PRICE_COVERAGE", "WARN", scope_type="VARIANT", scope_id="X")
        ]
        blockers = readiness_gate_blockers(
            gates, variant_id="X", applicable_gate_names={"PRICE_COVERAGE"}
        )
        self.assertEqual(blockers, [])

    def test_missing_declared_applicable_gate_fails_closed(self):
        blockers = readiness_gate_blockers(
            foundation_passes(),
            vendor_id="A",
            applicable_gate_names={"OPEN_PO_RECONCILIATION"},
        )
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["type"], "MISSING_APPLICABLE_GATE")
        self.assertEqual(
            blockers[0]["detail"]["gate_name"], "OPEN_PO_RECONCILIATION"
        )

    def test_non_applicable_scoped_gate_does_not_create_global_blocker(self):
        gates = foundation_passes() + [
            gate("VENDOR_RULES", "FAIL", scope_type="VENDOR", scope_id="A")
        ]
        self.assertEqual(readiness_gate_blockers(gates, vendor_id="B"), [])
        self.assertEqual(readiness_gate_blockers(gates), [])

    def test_price_and_mapping_failures_do_not_leak_to_unrelated_scope(self):
        gates = foundation_passes() + [
            gate("PRICE_COVERAGE", "FAIL", scope_type="VENDOR", scope_id="A"),
            gate("MAPPING_INTEGRITY", "FAIL", scope_type="VARIANT", scope_id="X"),
        ]
        self.assertEqual(
            readiness_gate_blockers(gates, vendor_id="B", variant_id="Y"), []
        )
        self.assertEqual(
            len(readiness_gate_blockers(gates, vendor_id="A", variant_id="X")), 2
        )

    def test_advisory_fail_does_not_block_until_declared_applicable(self):
        gates = foundation_passes() + [
            gate("PRICE_COVERAGE", "FAIL", blocks_po=False)
        ]
        self.assertEqual(readiness_gate_blockers(gates), [])
        blockers = readiness_gate_blockers(
            gates, applicable_gate_names={"PRICE_COVERAGE"}
        )
        self.assertEqual(blockers[0]["detail"]["gate_name"], "PRICE_COVERAGE")

    def test_existing_global_vendor_rules_fail_still_blocks(self):
        gates = foundation_passes() + [gate("VENDOR_RULES", "FAIL")]
        blockers = readiness_gate_blockers(gates, vendor_id="A")
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["detail"]["gate_name"], "VENDOR_RULES")

    def test_missing_undeclared_vendor_rules_is_not_universal_blocker(self):
        self.assertEqual(
            readiness_gate_blockers(foundation_passes(), vendor_id="A"), []
        )

    def test_missing_declared_vendor_rules_fails_closed(self):
        blockers = readiness_gate_blockers(
            foundation_passes(),
            vendor_id="A",
            applicable_gate_names={"VENDOR_RULES"},
        )
        self.assertEqual(blockers[0]["type"], "MISSING_APPLICABLE_GATE")
        self.assertEqual(blockers[0]["detail"]["gate_name"], "VENDOR_RULES")


class ExceptionScopeTests(unittest.TestCase):
    def test_applicable_high_and_critical_exceptions_block(self):
        rows = [exception(severity="HIGH"), exception(severity="CRITICAL")]
        self.assertEqual(len(exception_blockers(rows, vendor_id="A")), 2)

    def test_warn_level_and_closed_exceptions_do_not_block(self):
        rows = [exception(severity="MEDIUM"), exception(status="RESOLVED")]
        self.assertEqual(exception_blockers(rows), [])

    def test_vendor_variant_and_combined_scopes_match_conjunctively(self):
        vendor_only = exception(vendor_id="A")
        variant_only = exception(variant_id="X")
        combined = exception(vendor_id="A", variant_id="X")
        rows = [vendor_only, variant_only, combined]

        self.assertEqual(len(exception_blockers(rows, vendor_id="A")), 1)
        self.assertEqual(len(exception_blockers(rows, variant_id="X")), 1)
        self.assertEqual(
            len(exception_blockers(rows, vendor_id="A", variant_id="X")), 3
        )
        self.assertEqual(
            len(exception_blockers(rows, vendor_id="A", variant_id="Y")), 1
        )
        self.assertEqual(
            exception_blockers(rows, vendor_id="B", variant_id="Y"), []
        )


if __name__ == "__main__":
    unittest.main()
