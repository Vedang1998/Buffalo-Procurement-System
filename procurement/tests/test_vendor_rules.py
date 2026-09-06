"""Packet 2 confirmed vendor operating rules and readiness tests."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from fastapi.routing import APIRoute

from postgres_test_support import validated_test_connection
from procurement_os import api
from procurement_os.vendor_rules import (
    VendorRuleValidationError,
    evaluate_vendor_rules,
    update_vendor_rules,
    validate_vendor_rules_input,
)


DB_DIR = Path(__file__).resolve().parents[1] / "db"
MIGRATIONS = (
    "schema_postgres.sql",
    "001_v1_3_catalog_sales.sql",
    "002_seed_import_records.sql",
    "003_phase3_reconciliation.sql",
    "004_identity_decision_invariants.sql",
    "005_identity_investigation.sql",
    "006_phase4_sales_backfill.sql",
    "007_phase4_terminal_disposition.sql",
    "008_monday_inventory_foundation.sql",
    "009_monday_vendor_rules.sql",
)


def complete_rules(**overrides):
    rules = {
        "order_days": ("MONDAY",),
        "order_cutoff_local": time(17, 0),
        "timezone_name": "America/New_York",
        "expected_delivery_days": ("WEDNESDAY",),
        "order_cycle_days": 7,
        "lead_time_days": 2,
        "lead_time_variability_days": "0.50",
        "reliability_pct": "0.95",
        "minimum_type": "CASE",
        "minimum_value": "10.00",
        "below_minimum_fee": "8.00",
        "loose_order_allowed": True,
        "loose_unit_fee": "3.00",
        "special_rules": None,
        "holiday_blackout_notes": None,
        "confirmation_source": "owner-confirmed fixture",
    }
    rules.update(overrides)
    return rules


class VendorRulesPureTests(unittest.TestCase):
    def test_complete_profile_normalizes_and_optional_notes_are_irrelevant(self):
        rules = validate_vendor_rules_input(complete_rules())
        self.assertEqual(rules.order_days, ("MONDAY",))
        self.assertEqual(rules.minimum_type, "CASE")
        self.assertEqual(rules.minimum_value, Decimal("10.00"))
        self.assertIsNone(rules.special_rules)
        self.assertIsNone(rules.holiday_blackout_notes)

    def test_material_fields_and_typed_minimum_or_loose_fee_fail_closed(self):
        invalid = (
            complete_rules(order_days=()),
            complete_rules(timezone_name="Not/A_Zone"),
            complete_rules(minimum_type="CASE", minimum_value=None),
            complete_rules(minimum_type="NONE", minimum_value="1"),
            complete_rules(loose_order_allowed=True, loose_unit_fee=None),
            complete_rules(reliability_pct="1.1"),
        )
        for rules in invalid:
            with self.subTest(rules=rules), self.assertRaises(VendorRuleValidationError):
                validate_vendor_rules_input(rules)


class ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


class VendorRulesPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        from psycopg import sql

        self.conn, self.test_target, self.test_database_info = (
            validated_test_connection()
        )
        self.schema = f"vendor_rules_mvp_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        for migration in MIGRATIONS:
            self.conn.execute((DB_DIR / migration).read_text(encoding="utf-8"))
        self.conn.commit()

    def tearDown(self) -> None:
        from psycopg import sql

        try:
            self.conn.rollback()
            self.conn.execute("SET search_path TO public")
            self.conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema))
            )
            self.conn.commit()
        finally:
            self.conn.close()

    def add_vendor(self, name: str, *, active: bool = True) -> str:
        vendor_id = str(
            self.conn.execute(
                """INSERT INTO vendors(
                       vendor_name,active,order_day,lead_time_days,loose_unit_fee
                   ) VALUES (%s,%s,'Monday',1,3.00) RETURNING vendor_id""",
                (name, active),
            ).fetchone()[0]
        )
        self.conn.commit()
        return vendor_id

    def save(self, vendor_id: str, rules=None, **kwargs):
        return update_vendor_rules(
            self.conn,
            vendor_id=vendor_id,
            rules=rules or complete_rules(),
            actor=kwargs.pop("actor", "owner"),
            reason=kwargs.pop("reason", "confirm operating profile"),
            **kwargs,
        )

    def test_seed_placeholders_do_not_pass_without_confirmed_profile(self):
        vendor_id = self.add_vendor("Unconfirmed Distributor")
        result = evaluate_vendor_rules(self.conn)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["evidence"]["failing_vendor_ids"], [vendor_id])
        self.assertEqual(result["vendors"][0]["missing"], ["CONFIRMED_RULE_PROFILE"])

    def test_complete_confirmed_vendor_passes_global_and_scoped_gates(self):
        vendor_id = self.add_vendor("Complete Distributor")
        result = self.save(vendor_id)
        self.assertEqual(result["readiness"]["status"], "PASS")
        gates = self.conn.execute(
            """SELECT scope_type,scope_id,status,blocks_po
               FROM readiness_gates WHERE gate_name='VENDOR_RULES'
               ORDER BY scope_type,scope_id"""
        ).fetchall()
        self.assertIn(("GLOBAL", "", "PASS", False), gates)
        self.assertIn(("VENDOR", vendor_id, "PASS", False), gates)

    def test_case_and_dollar_minimum_are_typed_and_none_is_explicit(self):
        vendor_id = self.add_vendor("Typed Minimum Distributor")
        first = self.save(vendor_id)
        self.save(
            vendor_id,
            complete_rules(minimum_type="DOLLAR", minimum_value="500"),
            expected_version=first["rules_version"],
        )
        stored = self.conn.execute(
            "SELECT minimum_type,minimum_value FROM vendor_operating_rules"
        ).fetchone()
        self.assertEqual(stored, ("DOLLAR", Decimal("500.00")))
        with self.assertRaises(VendorRuleValidationError):
            self.save(
                vendor_id,
                complete_rules(minimum_type="NONE", minimum_value="1"),
                expected_version=2,
            )

    def test_loose_fee_zero_is_valid_and_missing_allowed_fee_is_not(self):
        vendor_id = self.add_vendor("Loose Distributor")
        result = self.save(vendor_id, complete_rules(loose_unit_fee="0"))
        self.assertEqual(result["readiness"]["status"], "PASS")
        self.assertEqual(
            self.conn.execute(
                "SELECT loose_order_allowed,loose_unit_fee FROM vendor_operating_rules"
            ).fetchone(),
            (True, Decimal("0.00")),
        )

    def test_updates_are_vendor_scoped_versioned_and_append_only_audited(self):
        first_vendor = self.add_vendor("First Distributor")
        second_vendor = self.add_vendor("Second Distributor")
        first = self.save(first_vendor)
        self.save(second_vendor, complete_rules(minimum_type="NONE", minimum_value=None))
        updated = self.save(
            first_vendor,
            complete_rules(lead_time_days=3),
            expected_version=first["rules_version"],
            reason="confirmed revised lead time",
        )
        self.assertEqual(updated["rules_version"], 2)
        versions = self.conn.execute(
            """SELECT vendor_id::text,array_agg(rules_version ORDER BY rules_version)
               FROM vendor_rule_revisions GROUP BY vendor_id ORDER BY vendor_id"""
        ).fetchall()
        self.assertEqual(
            {vendor: list(found) for vendor, found in versions},
            {first_vendor: [1, 2], second_vendor: [1]},
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM change_log WHERE table_name='vendor_operating_rules'"
            ).fetchone()[0],
            3,
        )

    def test_identical_replay_writes_no_revision_or_change_log(self):
        vendor_id = self.add_vendor("Idempotent Distributor")
        first = self.save(vendor_id)
        before = self.conn.execute(
            """SELECT
                   (SELECT count(*) FROM vendor_rule_revisions),
                   (SELECT count(*) FROM change_log
                    WHERE table_name='vendor_operating_rules')"""
        ).fetchone()
        self.conn.commit()
        replay = self.save(
            vendor_id,
            complete_rules(minimum_value="10"),
            expected_version=first["rules_version"],
        )
        after = self.conn.execute(
            """SELECT
                   (SELECT count(*) FROM vendor_rule_revisions),
                   (SELECT count(*) FROM change_log
                    WHERE table_name='vendor_operating_rules')"""
        ).fetchone()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(before, after)

    def test_stale_update_fails_before_rule_or_audit_mutation(self):
        vendor_id = self.add_vendor("Concurrent Distributor")
        self.save(vendor_id)
        before = self.conn.execute(
            """SELECT rules_version,lead_time_days,
                      (SELECT count(*) FROM vendor_rule_revisions)
               FROM vendor_operating_rules"""
        ).fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(VendorRuleValidationError, "stale"):
            self.save(
                vendor_id,
                complete_rules(lead_time_days=9),
                expected_version=0,
            )
        after = self.conn.execute(
            """SELECT rules_version,lead_time_days,
                      (SELECT count(*) FROM vendor_rule_revisions)
               FROM vendor_operating_rules"""
        ).fetchone()
        self.assertEqual(before, after)

    def test_inactive_vendor_is_warn_and_does_not_fail_active_aggregate(self):
        active_id = self.add_vendor("Active Distributor")
        self.add_vendor("Inactive Distributor", active=False)
        self.save(active_id)
        result = evaluate_vendor_rules(self.conn)
        self.assertEqual(result["status"], "PASS")
        inactive = next(vendor for vendor in result["vendors"] if not vendor["active"])
        self.assertEqual(inactive["status"], "WARN")

    def test_get_page_is_read_only_escaped_and_routes_are_explicit(self):
        vendor_id = self.add_vendor("<Unsafe & Distributor>")
        self.save(vendor_id)
        self.conn.commit()
        before_xid = self.conn.execute("SELECT txid_current_if_assigned()").fetchone()[0]
        with patch.object(api, "_db_conn", return_value=ConnectionContext(self.conn)):
            page = api.vendor_rules_page()
        after_xid = self.conn.execute("SELECT txid_current_if_assigned()").fetchone()[0]
        self.assertIn("&lt;Unsafe &amp; Distributor&gt;", page)
        self.assertNotIn("<Unsafe & Distributor>", page)
        self.assertIsNone(before_xid)
        self.assertIsNone(after_xid)
        methods = {
            route.path: route.methods
            for route in api.app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/vendor-rules")
        }
        self.assertEqual(methods["/vendor-rules"], {"GET"})
        self.assertEqual(methods["/vendor-rules/{vendor_id}"], {"POST"})

    def test_invalid_review_token_stops_before_database_access(self):
        with patch.dict(
            os.environ, {"RECONCILIATION_REVIEW_TOKEN": "expected"}, clear=True
        ), patch.object(api, "_db_conn") as database:
            with self.assertRaises(HTTPException) as raised:
                api.vendor_rules_update_page(
                    "vendor",
                    "MONDAY",
                    "17:00",
                    "America/New_York",
                    "WEDNESDAY",
                    7,
                    2,
                    "0.5",
                    "0.95",
                    "NONE",
                    "",
                    "0",
                    False,
                    "",
                    "",
                    "",
                    "owner fixture",
                    "owner",
                    "confirm",
                    0,
                    "wrong",
                )
        self.assertEqual(raised.exception.status_code, 403)
        database.assert_not_called()

    def test_vendor_rule_migration_reapplication_preserves_constraints(self):
        migration = (DB_DIR / "009_monday_vendor_rules.sql").read_text(encoding="utf-8")
        self.conn.execute(migration)
        self.conn.execute(migration)
        self.conn.commit()
        vendor_id = self.add_vendor("Constraint Distributor")
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    """INSERT INTO vendor_operating_rules(
                           vendor_id,order_days,order_cutoff_local,timezone_name,
                           expected_delivery_days,order_cycle_days,lead_time_days,
                           lead_time_variability_days,reliability_pct,minimum_type,
                           minimum_value,below_minimum_fee,loose_order_allowed,
                           confirmation_source,confirmed_by,rules_version
                       ) VALUES (%s,ARRAY['MONDAY'],'17:00','America/New_York',
                                 ARRAY['WEDNESDAY'],7,2,0,1.5,'NONE',NULL,0,FALSE,
                                 'fixture','owner',1)""",
                    (vendor_id,),
                )


if __name__ == "__main__":
    unittest.main()
