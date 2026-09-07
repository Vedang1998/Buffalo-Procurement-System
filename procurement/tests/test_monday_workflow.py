"""Real PostgreSQL Monday recommendation -> review -> DRAFT -> packet tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from contextlib import nullcontext
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
from threading import Barrier
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urljoin
import uuid
import zipfile

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.errors import UniqueViolation

from postgres_test_support import validated_test_connection
from procurement_os import api
from procurement_os.catalog import recompute_catalog_gate
from procurement_os.draft_po import (
    DRAFT_BUILD_LOCK,
    DraftPoError,
    _vendor_economics,
    build_vendor_drafts,
    get_vendor_drafts,
    preview_vendor_drafts,
)
from procurement_os.emergency_packet import (
    PACKET_BUILD_LOCK,
    EmergencyPacketError,
    build_emergency_review_packet,
    list_monday_artifacts,
    read_monday_artifact,
)
from procurement_os.inventory import capture_daily_inventory, recompute_inventory_history_gate
from procurement_os.monday_controls import (
    MaterialEditPolicy,
    classify_material_edit,
    load_material_edit_policy,
)
from procurement_os.po_csv import FORMAT_WARNING, render_vendor_draft_csv
from procurement_os.po_ledger import open_po_position, recompute_open_po_reconciliation_gate
from procurement_os.readiness import po_readiness
from procurement_os.procurement_review import (
    REVIEW_LOCK,
    ProcurementReviewError,
    acknowledge_and_exclude_blocked_item,
    confirm_material_recommendation_edit,
    preview_recommendation_review,
    record_recommendation_review,
)
from procurement_os.recommendations import (
    MONDAY_ANALYSIS_LOCK,
    MondayRecommendationError,
    _authoritative_sales_rows,
    _price_tiers_from_rows,
    _sales_coverage_digest,
    _whole as recommendation_whole,
    monday_run_inputs_match,
    prepare_monday_run,
)
from procurement_os.storage import LocalFilesystemStorage
from procurement_os.vendor_rules import recompute_vendor_rules_gates


DB_DIR = Path(__file__).resolve().parents[1] / "db"
PRE_PRICE_MIGRATIONS = (
    "schema_postgres.sql","001_v1_3_catalog_sales.sql","002_seed_import_records.sql",
    "003_phase3_reconciliation.sql","004_identity_decision_invariants.sql",
    "005_identity_investigation.sql","006_phase4_sales_backfill.sql",
    "007_phase4_terminal_disposition.sql","008_monday_inventory_foundation.sql",
    "009_monday_vendor_rules.sql","010_monday_po_ledger.sql",
)
POST_PRICE_MIGRATIONS = (
    "011_monday_price_book_staging.sql","012_monday_review_draft_packet.sql",
    "013_monday_p1_remediation.sql",
)
BUSINESS_DATE = date(2026, 9, 7)


class MondayWorkflowPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluation_at = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
        self.clock_patch = patch(
            "procurement_os.recommendations._database_evaluation_at",
            return_value=self.evaluation_at,
        )
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.conn,_,self.database_info = validated_test_connection()
        self.schema = f"monday_workflow_{uuid.uuid4().hex}"
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        for name in PRE_PRICE_MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text())
        self.vendor_a = self._vendor("Alpha Test Vendor",loose=False,pack=6)
        self.vendor_b = self._vendor("Beta Test Vendor",loose=True,pack=12)
        self.variant_a,self.offer_a = self._variant_offer_price(
            "1001",self.vendor_a,"ALPHA-1001",6,
            unit_price=Decimal("1.66"),case_price=Decimal("10.01"),
        )
        self.variant_b,self.offer_b = self._variant_offer_price("2002",self.vendor_b,"BETA-2002",12)
        for name in POST_PRICE_MIGRATIONS:
            self.conn.execute((DB_DIR / name).read_text())
        self._supporting_evidence()
        self.conn.commit()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = LocalFilesystemStorage(self.temp.name)

    def tearDown(self) -> None:
        try:
            self.conn.rollback()
            self.conn.execute("SET search_path TO public")
            self.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
            self.conn.commit()
        finally:
            self.conn.close()

    def _vendor(
        self,name: str,*,loose: bool,pack: int,
        loose_fee: Decimal | None=None,
    ) -> str:
        vendor_id = self.conn.execute(
            "INSERT INTO vendors(vendor_name,active) VALUES (%s,TRUE) RETURNING vendor_id",(name,),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO vendor_operating_rules(
                       vendor_id,order_days,order_cutoff_local,timezone_name,expected_delivery_days,
                       order_cycle_days,lead_time_days,lead_time_variability_days,reliability_pct,
                       minimum_type,minimum_value,below_minimum_fee,loose_order_allowed,loose_unit_fee,
                       confirmation_source,confirmed_by,rules_version)
                VALUES (%s,ARRAY['MONDAY'],'12:00','America/New_York',ARRAY['THURSDAY'],
                        2,1,0,1,'DOLLAR',100,5,%s,%s,'synthetic owner fixture','test-owner',1)""",
            (
                vendor_id,loose,
                (Decimal("0") if loose_fee is None else loose_fee)
                if loose else None,
            ),
        )
        return str(vendor_id)

    def _variant_offer_price(
        self,variant_id: str,vendor_id: str,sku: str,pack: int,
        *,unit_price: Decimal=Decimal("10"),case_price: Decimal | None=None,
    ):
        self.conn.execute(
            """INSERT INTO variants(variant_id,product_id,product_title,variant_title,active,
                       catalog_state,identity_scope,sku)
                VALUES (%s,%s,%s,'750ML',TRUE,'LIVE','CURRENT',%s)""",
            (variant_id,f"product-{variant_id}",f"Synthetic {variant_id}",sku),
        )
        offer_id = self.conn.execute(
            """INSERT INTO supplier_offers(
                       variant_id,vendor_id,supplier_sku,supplier_description,package_type,size_text,
                       raw_pack,shopify_units_per_case,qualifying_units_per_case,assortment_scope,
                       assortable,active,confidence,source_file)
                VALUES (%s,%s,%s,%s,'STANDARD','750ML',%s,%s,%s,'PRODUCT',FALSE,TRUE,
                        'VERIFIED','synthetic-test.csv') RETURNING offer_id""",
            (variant_id,vendor_id,sku,f"Synthetic {variant_id}",f"{pack}x750ML",pack,pack),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO prices(offer_id,price_state,effective_month,level_type,case_price,
                       unit_price,source_file,extraction_confidence,verified,notes)
                VALUES (%s,'current',%s,'BASE',%s,%s,'synthetic-test.csv','VERIFIED',TRUE,
                        'legacy seed import: synthetic pre-011 fixture')""",
            (
                offer_id,BUSINESS_DATE.replace(day=1),
                case_price if case_price is not None else Decimal(pack)*unit_price,
                unit_price,
            ),
        )
        return variant_id,int(offer_id)

    def _supporting_evidence(self) -> None:
        self.conn.execute(
            """INSERT INTO catalog_sync_runs(
                       completed_at,status,shopify_api_version,shopify_reported_variant_count,
                       live_rows_received,exact_current_ids,new_live_variants,source_hash,
                       pagination_complete,notes)
                VALUES (now(),'COMPLETED','fixture',2,2,2,0,%s,TRUE,'synthetic test evidence')""",
            ("c"*64,),
        )
        for variant_id in (self.variant_a,self.variant_b):
            self.conn.execute(
                """INSERT INTO variant_policies(
                           variant_id,policy_type,value_json,active,effective_from,approved_by,note)
                    VALUES (%s,'REPLENISHMENT_MODE','{"mode":"ROUTINE"}',TRUE,%s,
                            'test-owner','synthetic explicit policy')""",
                (variant_id,BUSINESS_DATE),
            )
            for offset in range(84):
                sale_date = BUSINESS_DATE - timedelta(days=84-offset)
                self.conn.execute(
                    """INSERT INTO sales_daily(sale_date,variant_id,units_sold,source)
                        VALUES (%s,%s,1,'SYNTHETIC_TEST')""",
                    (sale_date,variant_id),
                )
        self.conn.commit()
        inventory_result = capture_daily_inventory(
            self.conn,
            business_date=BUSINESS_DATE,
            captured_at=datetime(2026,9,7,12,tzinfo=timezone.utc),
            source="SYNTHETIC_TEST",
            rows=(
                {"variant_id": self.variant_a,"location_gid": "location-test","available_quantity": 0,"incoming_quantity": 0},
                {"variant_id": self.variant_b,"location_gid": "location-test","available_quantity": 0,"incoming_quantity": 0},
            ),
        )
        if inventory_result["readiness"]["status"] != "PASS":
            raise AssertionError("synthetic inventory fixture did not produce PASS readiness")
        catalog = recompute_catalog_gate(self.conn)
        vendor_rules = recompute_vendor_rules_gates(self.conn)
        open_orders = recompute_open_po_reconciliation_gate(
            self.conn, as_of=self.evaluation_at
        )
        if catalog["status"] != "PASS" or vendor_rules["status"] != "PASS":
            raise AssertionError("synthetic catalog/vendor evidence did not produce PASS readiness")
        if open_orders["status"] != "PASS":
            raise AssertionError("synthetic open-PO evidence did not produce PASS readiness")
        self._refresh_synthetic_sales_gate()

    def _refresh_synthetic_sales_gate(self) -> None:
        sales_rows = self.conn.execute(
            "SELECT count(*) FROM sales_daily WHERE source='SYNTHETIC_TEST'"
        ).fetchone()[0]
        if int(sales_rows) != 168:
            raise AssertionError("synthetic sales evidence is incomplete")
        variant_coverage = {}
        for variant_id in (self.variant_a, self.variant_b):
            rows = self.conn.execute(
                """SELECT sale_date,units_sold,net_sales,distinct_orders,source,run_id::text
                     FROM sales_daily
                    WHERE variant_id=%s AND source='SYNTHETIC_TEST'
                      AND sale_date BETWEEN %s AND %s
                    ORDER BY sale_date,source""",
                (
                    variant_id,
                    BUSINESS_DATE - timedelta(days=84),
                    BUSINESS_DATE - timedelta(days=1),
                ),
            ).fetchall()
            variant_coverage[variant_id] = {
                "row_count": len(rows),
                "sha256": _sales_coverage_digest(rows),
            }
        updated = self.conn.execute(
            """UPDATE readiness_gates
                   SET status='PASS',severity='CRITICAL',blocks_po=TRUE,
                       message='Disposable fixture has exact synthetic sales coverage.',
                       evidence_json=%s::jsonb,checked_at=transaction_timestamp()
                 WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''
                 RETURNING status""",
            (
                json.dumps(
                    {
                        "coverage_contract":
                            "DISPOSABLE_SYNTHETIC_DAILY_VARIANT_COVERAGE_V1",
                        "source": "SYNTHETIC_TEST",
                        "sales_rows": int(sales_rows),
                        "variant_count": 2,
                        "history_start": str(BUSINESS_DATE-timedelta(days=84)),
                        "history_end": str(BUSINESS_DATE-timedelta(days=1)),
                        "variant_coverage": variant_coverage,
                    },
                    sort_keys=True,
                ),
            ),
        ).fetchall()
        if updated != [("PASS",)]:
            raise AssertionError("synthetic SALES_BACKFILL fixture gate was not unique")

    def _prepare(self,key="monday-fixture"):
        return prepare_monday_run(
            self.conn,business_date=BUSINESS_DATE,idempotency_key=key,
            variant_ids=(self.variant_a,self.variant_b),actor="test-owner",
        )

    def _review_all(self,run,*,edit_beta=True,reject_beta=False):
        decisions=[]
        for item in run["recommendations"]:
            if item["variant_id"] == self.variant_b and reject_beta:
                action="REJECT"; kwargs={"comment":"not needed in synthetic review"}
            elif item["variant_id"] == self.variant_b and edit_beta:
                action="EDIT_QUANTITY"; kwargs={"approved_cases":1,"approved_loose_units":2,"comment":"=synthetic edit"}
            else:
                action="ACCEPT"; kwargs={}
            if action in {"ACCEPT", "EDIT_QUANTITY"}:
                preview = preview_recommendation_review(
                    self.conn,recommendation_id=item["recommendation_id"],action=action,
                    actor="test-reviewer",expected_input_fingerprint=run["input_fingerprint"],
                    **kwargs,
                )
                kwargs["expected_review_preview_fingerprint"] = preview["preview_fingerprint"]
                if preview["materiality"]["materiality_tier"] == "MATERIAL":
                    confirmation = confirm_material_recommendation_edit(
                        self.conn,recommendation_id=item["recommendation_id"],
                        actor="test-reviewer",
                        expected_input_fingerprint=run["input_fingerprint"],
                        expected_review_preview_fingerprint=preview["preview_fingerprint"],
                        approved_cases=kwargs.get("approved_cases"),
                        approved_loose_units=kwargs.get("approved_loose_units"),
                        comment=kwargs.get("comment", ""),
                        confirmation_reason="synthetic material edit confirmation",
                    )
                    kwargs["material_edit_confirmation_id"] = confirmation[
                        "material_edit_confirmation_id"
                    ]
            decisions.append(record_recommendation_review(
                self.conn,recommendation_id=item["recommendation_id"],action=action,
                actor="test-reviewer",expected_input_fingerprint=run["input_fingerprint"],**kwargs,
            ))
        return decisions

    def _build_drafts(self, run, *, actor="builder", conn=None):
        target = conn or self.conn
        preview = preview_vendor_drafts(
            target, run_id=run["run_id"], actor=actor
        )
        if preview.get("already_built"):
            return build_vendor_drafts(
                target, run_id=run["run_id"], actor=actor
            )
        return build_vendor_drafts(
            target,
            run_id=run["run_id"],
            actor=actor,
            expected_preview_fingerprint=preview["preview_fingerprint"],
            minimum_disposition=preview["minimum_disposition"],
        )

    def test_migration_applies_twice_and_contract_marker_exists(self):
        for name in ("012_monday_review_draft_packet.sql","013_monday_p1_remediation.sql"):
            self.conn.execute((DB_DIR/name).read_text())
            self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT value FROM meta WHERE key='monday_review_draft_packet_contract'").fetchone()[0],"v2",
        )
        self.assertEqual(
            self.conn.execute("SELECT value FROM meta WHERE key='monday_p1_remediation_contract'").fetchone()[0],"v1",
        )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM monday_run_artifacts").fetchone()[0],0)

    def test_prepare_freezes_two_pack_sizes_and_strategic_extra_zero(self):
        result=self._prepare()
        self.assertEqual(result["workflow_stage"],"AWAITING_REVIEW")
        self.assertEqual(len(result["recommendations"]),2)
        self.assertEqual({item["variant_id"] for item in result["recommendations"]},{"1001","2002"})
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM run_price_snapshots WHERE run_id=%s",(result["run_id"],)).fetchone()[0],2,
        )
        self.assertEqual(
            self.conn.execute("SELECT max(strategic_extra_units) FROM procurement_recommendations WHERE run_id=%s",(result["run_id"],)).fetchone()[0],0,
        )

    def test_prepare_replay_is_read_only_and_changed_source_conflicts(self):
        first=self._prepare()
        counts=self.conn.execute(
            "SELECT (SELECT count(*) FROM runs),(SELECT count(*) FROM procurement_recommendations),(SELECT count(*) FROM exceptions)"
        ).fetchone()
        self.conn.commit()
        replay=self._prepare()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(counts,self.conn.execute(
            "SELECT (SELECT count(*) FROM runs),(SELECT count(*) FROM procurement_recommendations),(SELECT count(*) FROM exceptions)"
        ).fetchone())
        self.conn.commit()
        self.conn.execute("UPDATE vendor_operating_rules SET order_cycle_days=3 WHERE vendor_id=%s",(self.vendor_a,)); self.conn.commit()
        with self.assertRaisesRegex(MondayRecommendationError,"different frozen inputs"):
            self._prepare()
        self.assertEqual(first["run_id"],replay["run_id"])

    def test_material_edit_policy_drift_invalidates_frozen_run_before_review(self):
        run=self._prepare("frozen-material-policy")
        recommendation=run["recommendations"][0]
        changed_rules={
            "review":{
                "emergency_material_edit":{
                    "policy_version":"EMERGENCY_MONDAY_MATERIAL_EDIT_V1",
                    "owner_approval_status":"PENDING_OWNER_APPROVAL",
                    "max_normal_baseline_multiplier":Decimal("1.9"),
                    "max_normal_resulting_days_supply":Decimal("30.0"),
                }
            }
        }
        with patch(
            "procurement_os.monday_controls.load_rules",return_value=changed_rules
        ):
            self.assertFalse(
                monday_run_inputs_match(
                    self.conn,run["run_id"],run["input_fingerprint"]
                )
            )
            with self.assertRaisesRegex(ProcurementReviewError,"stale|changed"):
                preview_recommendation_review(
                    self.conn,recommendation_id=recommendation["recommendation_id"],
                    action="ACCEPT",actor="reviewer",
                    expected_input_fingerprint=run["input_fingerprint"],
                )

    def test_missing_inventory_and_stale_sales_are_visible_blockers(self):
        capture_daily_inventory(
            self.conn,
            business_date=BUSINESS_DATE,
            captured_at=datetime(2026,9,7,13,tzinfo=timezone.utc),
            source="SYNTHETIC_INCOMPLETE_COVERAGE",
            rows=(
                {"variant_id": self.variant_b,"location_gid": "location-test","available_quantity": 0,"incoming_quantity": 0},
            ),
        )
        self.conn.execute(
            "DELETE FROM sales_daily WHERE variant_id=%s AND sale_date=%s",
            (self.variant_b, BUSINESS_DATE-timedelta(days=1)),
        )
        self.conn.commit()
        result=self._prepare("blocked-run")
        self.assertTrue(result["blockers"])
        messages=" ".join(item["message"] for item in result["blockers"])
        self.assertIn("COMPLETE_CURRENT_INVENTORY_REQUIRED",messages)
        self.assertIn("CURRENT_SALES_COVERAGE_UNPROVEN",messages)
        self.assertEqual(result["recommendations"],[])

    def test_nominal_canonical_sales_gate_without_exact_run_facts_blocks_first_prepare(self):
        history_start=BUSINESS_DATE-timedelta(days=84)
        history_end=BUSINESS_DATE-timedelta(days=1)
        asserted_evidence={
            "sales_backfill_id":str(uuid.uuid4()),
            "start_date":str(history_start),"end_date":str(history_end),
            "store_timezone":"America/New_York","expected_chunks":1,
            "completed_chunks":1,"expected_pages":1,"completed_pages":1,
            "source_rows":168,"unique_source_facts":168,"resolved_rows":168,
            "unresolved_rows":0,"ambiguous_rows":0,"excluded_rows":0,
            "coverage_complete":True,"pages_complete":True,
            "source_facts_persisted":True,"idempotency_verified":True,
            "control_totals_reconciled":True,"canonical_aggregate_rebuilt":True,
            "blockers":[],
        }
        self.conn.execute(
            """UPDATE readiness_gates
                  SET status='PASS',evidence_json=%s::jsonb
                WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''""",
            (json.dumps(asserted_evidence,sort_keys=True),),
        )
        self.conn.commit()
        result=self._prepare("unproven-canonical-sales")
        self.assertEqual(result["recommendations"],[])
        self.assertEqual(len(result["blockers"]),2)
        self.assertTrue(
            all(
                "CURRENT_SALES_COVERAGE_UNPROVEN" in item["message"]
                for item in result["blockers"]
            )
        )

    def test_canonical_sales_authority_reconciles_exact_run_facts_by_day(self):
        history_start=BUSINESS_DATE-timedelta(days=84)
        history_end=BUSINESS_DATE-timedelta(days=1)
        run_id=uuid.uuid4()
        control_evidence={
            "sales_backfill_id":str(run_id),
            "start_date":str(history_start),"end_date":str(history_end),
            "store_timezone":"America/New_York","expected_chunks":1,
            "completed_chunks":1,"expected_pages":1,"completed_pages":1,
            "source_rows":1,"unique_source_facts":1,"resolved_rows":1,
            "unresolved_rows":0,"ambiguous_rows":0,"excluded_rows":0,
            "coverage_complete":True,"pages_complete":True,
            "source_facts_persisted":True,"idempotency_verified":True,
            "control_totals_reconciled":True,"canonical_aggregate_rebuilt":True,
        }
        self.conn.execute(
            """INSERT INTO sales_backfill_runs(
                       sales_backfill_id,completed_at,status,start_date,end_date,source,
                       query_version,store_timezone,expected_chunks,completed_chunks,
                       expected_pages,completed_pages,source_rows,unique_source_facts,
                       resolved_rows,unresolved_rows,ambiguous_rows,excluded_rows,
                       coverage_complete,pages_complete,source_facts_persisted,
                       idempotency_verified,control_totals_reconciled,
                       canonical_aggregate_rebuilt,control_evidence)
                VALUES (%s,now(),'COMPLETED',%s,%s,'SHOPIFYQL_SALES',
                        'SHOPIFYQL_SALES_V2','America/New_York',1,1,1,1,1,1,1,0,0,0,
                        TRUE,TRUE,TRUE,TRUE,TRUE,TRUE,%s::jsonb)""",
            (run_id,history_start,history_end,json.dumps(control_evidence,sort_keys=True)),
        )
        raw_id=self.conn.execute(
            """INSERT INTO shopify_sales_daily_raw(
                       sales_backfill_id,sale_date,source_variant_id,source_sku,
                       source_product_title,source_variant_title,net_items_sold,net_sales,
                       canonical_variant_id,resolution_status,resolution_method,
                       resolution_evidence,source_row_hash,source_identity_key)
                VALUES (%s,%s,%s,'ALPHA-1001','Synthetic 1001','750ML',2,3,
                        %s,'RESOLVED','EXACT_VARIANT_ID','{"authority":"test"}',%s,%s)
                RETURNING raw_sales_id""",
            (
                run_id,history_start,self.variant_a,self.variant_a,
                "canonical-test-source-hash","1001|ALPHA-1001|Synthetic 1001|750ML",
            ),
        ).fetchone()[0]
        self.conn.execute(
            """INSERT INTO sales_backfill_run_facts(
                       sales_backfill_id,raw_sales_id,source_row_hash,
                       first_observed_net_items_sold,first_observed_net_sales,
                       observed_net_items_sold,observed_net_sales)
                VALUES (%s,%s,%s,2,3,2,3)""",
            (run_id,raw_id,"canonical-test-source-hash"),
        )
        self.conn.execute(
            """INSERT INTO sales_daily(sale_date,variant_id,units_sold,net_sales,source)
                VALUES (%s,%s,2,3,'SHOPIFYQL_SALES')""",
            (history_start,self.variant_a),
        )
        gate_evidence={**control_evidence,"blockers":[]}
        self.conn.execute(
            """UPDATE readiness_gates
                  SET status='PASS',evidence_json=%s::jsonb
                WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''""",
            (json.dumps(gate_evidence,sort_keys=True),),
        )
        self.conn.commit()
        rows,authority=_authoritative_sales_rows(
            self.conn,business_date=BUSINESS_DATE,variant_id=self.variant_a
        )
        self.assertEqual((len(rows),rows[0][1]),(1,Decimal("2")))
        self.assertEqual(authority["sales_backfill_id"],str(run_id))
        self.assertEqual(authority["source_fact_count"],1)
        self.conn.execute(
            """UPDATE sales_daily SET units_sold=3
                WHERE variant_id=%s AND source='SHOPIFYQL_SALES'""",
            (self.variant_a,),
        )
        with self.assertRaisesRegex(
            MondayRecommendationError,"CURRENT_SALES_COVERAGE_UNPROVEN"
        ):
            _authoritative_sales_rows(
                self.conn,business_date=BUSINESS_DATE,variant_id=self.variant_a
            )

    def test_invalid_price_ladder_is_visible_without_fractional_threshold_truncation(self):
        fractional = (
            (1,self.offer_a,BUSINESS_DATE.replace(day=1),"BASE",None,None,
             Decimal("10.0050"),Decimal("1.6675"),"synthetic-test.csv",None),
            (2,self.offer_a,BUSINESS_DATE.replace(day=1),"BREAK",Decimal("2.5"),"CS",
             None,Decimal("1.5000"),"synthetic-test.csv",None),
        )
        with self.assertRaisesRegex(MondayRecommendationError,"whole"):
            _price_tiers_from_rows(fractional)
        with patch(
            "procurement_os.recommendations._price_tiers_from_rows",
            side_effect=MondayRecommendationError("synthetic invalid frozen ladder"),
        ):
            result=self._prepare("invalid-price-ladder")
        self.assertEqual(result["recommendations"],[])
        self.assertIn(
            "VERIFIED_CURRENT_PRICE_LADDER_INVALID",
            " ".join(item["message"] for item in result["blockers"]),
        )

    def test_accept_edit_and_reject_are_append_only_and_stale_safe(self):
        run=self._prepare()
        decisions=self._review_all(run)
        self.assertEqual({item["action"] for item in decisions},{"ACCEPT","EDIT_QUANTITY"})
        self.assertEqual(
            self.conn.execute("SELECT workflow_stage FROM runs WHERE run_id=%s",(run["run_id"],)).fetchone()[0],"REVIEWED",
        )
        self.conn.commit()
        with self.assertRaisesRegex(ProcurementReviewError,"not awaiting|already"):
            record_recommendation_review(
                self.conn,recommendation_id=run["recommendations"][0]["recommendation_id"],action="REJECT",
                actor="test-reviewer",comment="changed mind",expected_input_fingerprint=run["input_fingerprint"],
            )
        with self.assertRaises(Exception):
            self.conn.execute("DELETE FROM review_decisions WHERE decision_id=%s",(decisions[0]["decision_id"],))
        self.conn.rollback()

    def test_edit_preview_is_read_only_and_exact_confirmation_is_required(self):
        run=self._prepare("edit-preview")
        beta=next(item for item in run["recommendations"] if item["variant_id"]==self.variant_b)
        self.conn.commit()
        before=self.conn.execute(
            """SELECT (SELECT count(*) FROM review_decisions),
                      (SELECT workflow_stage FROM runs WHERE run_id=%s),
                      (SELECT count(*) FROM purchase_orders),
                      (SELECT count(*) FROM monday_run_artifacts),
                      txid_current_if_assigned()""",
            (run["run_id"],),
        ).fetchone()
        self.conn.commit()
        preview=preview_recommendation_review(
            self.conn,recommendation_id=beta["recommendation_id"],action="EDIT_QUANTITY",
            actor="test-reviewer",expected_input_fingerprint=run["input_fingerprint"],
            approved_cases=1,approved_loose_units=2,comment="reviewed edit",
        )
        self.assertEqual(preview["approved_unit_cost"],Decimal("10.0000"))
        self.assertEqual(preview["approved_case_price"],Decimal("120.0000"))
        self.assertEqual(preview["approved_merchandise_total"],Decimal("140.00"))
        self.assertEqual(preview["approved_loose_order_fee"],Decimal("0.00"))
        self.assertEqual(preview["approved_line_total"],Decimal("140.00"))
        self.assertEqual(preview["resulting_inventory_units"],Decimal("14"))
        self.assertEqual(preview["resulting_days_supply"],Decimal("14.00"))
        self.assertEqual(
            preview["days_supply_status"],"CALCULATED_FROM_FROZEN_FORECAST"
        )
        self.assertEqual(preview["materiality"]["materiality_tier"],"MATERIAL")
        after=self.conn.execute(
            """SELECT (SELECT count(*) FROM review_decisions),
                      (SELECT workflow_stage FROM runs WHERE run_id=%s),
                      (SELECT count(*) FROM purchase_orders),
                      (SELECT count(*) FROM monday_run_artifacts),
                      txid_current_if_assigned()""",
            (run["run_id"],),
        ).fetchone()
        self.assertEqual(before,after)
        self.assertIsNone(after[-1])
        self.conn.commit()
        with self.assertRaisesRegex(ProcurementReviewError,"previewed and confirmed"):
            record_recommendation_review(
                self.conn,recommendation_id=beta["recommendation_id"],action="EDIT_QUANTITY",
                actor="different-reviewer",expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=1,approved_loose_units=2,comment="reviewed edit",
                expected_review_preview_fingerprint=preview["preview_fingerprint"],
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM review_decisions WHERE recommendation_id=%s",
                (beta["recommendation_id"],),
            ).fetchone()[0],0,
        )
        self.conn.commit()
        with self.assertRaisesRegex(ProcurementReviewError,"distinct confirmation"):
            record_recommendation_review(
                self.conn,recommendation_id=beta["recommendation_id"],action="EDIT_QUANTITY",
                actor="test-reviewer",expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=1,approved_loose_units=2,comment="reviewed edit",
                expected_review_preview_fingerprint=preview["preview_fingerprint"],
            )
        confirmation=confirm_material_recommendation_edit(
            self.conn,recommendation_id=beta["recommendation_id"],
            actor="test-reviewer",expected_input_fingerprint=run["input_fingerprint"],
            expected_review_preview_fingerprint=preview["preview_fingerprint"],
            approved_cases=1,approved_loose_units=2,comment="reviewed edit",
            confirmation_reason="reviewed exceptional cash and days exposure",
        )
        decision=record_recommendation_review(
            self.conn,recommendation_id=beta["recommendation_id"],action="EDIT_QUANTITY",
            actor="test-reviewer",expected_input_fingerprint=run["input_fingerprint"],
            approved_cases=1,approved_loose_units=2,comment="reviewed edit",
            expected_review_preview_fingerprint=preview["preview_fingerprint"],
            material_edit_confirmation_id=confirmation["material_edit_confirmation_id"],
        )
        self.assertEqual(decision["approved_unit_cost"],Decimal("10.0000"))
        self.assertEqual(decision["approved_merchandise_total"],Decimal("140.00"))
        self.assertEqual(decision["approved_loose_order_fee"],Decimal("0.00"))
        self.assertEqual(decision["approved_line_total"],Decimal("140.00"))

    def test_stale_fingerprint_and_unconfirmed_loose_edit_reject(self):
        run=self._prepare()
        alpha=next(item for item in run["recommendations"] if item["variant_id"]==self.variant_a)
        with self.assertRaisesRegex(ProcurementReviewError,"stale"):
            record_recommendation_review(
                self.conn,recommendation_id=alpha["recommendation_id"],action="ACCEPT",actor="owner",
                expected_input_fingerprint="f"*64,
            )
        with self.assertRaisesRegex(ProcurementReviewError,"loose"):
            record_recommendation_review(
                self.conn,recommendation_id=alpha["recommendation_id"],action="EDIT_QUANTITY",actor="owner",
                approved_cases=0,approved_loose_units=1,comment="bad loose",expected_input_fingerprint=run["input_fingerprint"],
            )

    def test_builds_one_draft_per_vendor_with_exact_reviewed_totals(self):
        run=self._prepare(); self._review_all(run)
        self.conn.commit()
        before=self.conn.execute(
            """SELECT (SELECT count(*) FROM purchase_orders WHERE run_id=%s),
                      (SELECT workflow_stage FROM runs WHERE run_id=%s),
                      txid_current_if_assigned()""",
            (run["run_id"],run["run_id"]),
        ).fetchone()
        self.conn.commit()
        preview=preview_vendor_drafts(
            self.conn,run_id=run["run_id"],actor="test-builder"
        )
        self.assertEqual(preview["minimum_disposition"],"PAY_FEE")
        alpha_preview=next(
            item for item in preview["vendors"] if item["vendor_id"]==self.vendor_a
        )
        beta_preview=next(
            item for item in preview["vendors"] if item["vendor_id"]==self.vendor_b
        )
        self.assertEqual(
            (
                alpha_preview["merchandise_total"],
                alpha_preview["minimum_shortfall"],
                alpha_preview["delivery_fee"],
                alpha_preview["po_total"],
            ),
            (Decimal("10.01"),Decimal("89.99"),Decimal("5"),Decimal("15.01")),
        )
        self.assertEqual(
            (
                beta_preview["merchandise_total"],
                beta_preview["loose_order_fee_total"],
                beta_preview["below_minimum_fee"],
                beta_preview["delivery_fee"],
                beta_preview["po_total"],
                beta_preview["minimum_disposition"],
            ),
            (
                Decimal("140.00"),Decimal("0.00"),Decimal("0"),
                Decimal("0.00"),Decimal("140.00"),"NOT_APPLICABLE",
            ),
        )
        with self.assertRaisesRegex(
            DraftPoError,"LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED"
        ):
            _vendor_economics(
                "threshold-vendor",
                [{
                    "minimum_type":"DOLLAR","minimum_value":Decimal("100"),
                    "below_minimum_fee":Decimal("5"),"cases":1,
                    "merchandise_total":Decimal("97"),"loose_order_fee":Decimal("3"),
                    "loose_unit_fee":Decimal("3"),
                    "recommendation_id":1,"decision_id":1,"variant_id":"probe",
                    "offer_id":1,"loose_units":1,"ordered_units":7,
                    "unit_cost":Decimal("13.8571"),"case_price":Decimal("83.14"),
                    "line_total":Decimal("100"),
                }],
            )
        after=self.conn.execute(
            """SELECT (SELECT count(*) FROM purchase_orders WHERE run_id=%s),
                      (SELECT workflow_stage FROM runs WHERE run_id=%s),
                      txid_current_if_assigned()""",
            (run["run_id"],run["run_id"]),
        ).fetchone()
        self.assertEqual(before,after)
        self.assertIsNone(after[-1])
        self.conn.commit()
        with self.assertRaisesRegex(DraftPoError,"previewed and explicitly confirmed"):
            build_vendor_drafts(
                self.conn,run_id=run["run_id"],actor="test-builder",
                expected_preview_fingerprint="f"*64,minimum_disposition="PAY_FEE",
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM purchase_orders WHERE run_id=%s",(run["run_id"],)
            ).fetchone()[0],0,
        )
        self.conn.commit()
        result=build_vendor_drafts(
            self.conn,run_id=run["run_id"],actor="test-builder",
            expected_preview_fingerprint=preview["preview_fingerprint"],
            minimum_disposition=preview["minimum_disposition"],
        )
        self.assertEqual(result["workflow_stage"],"DRAFTS_BUILT")
        self.assertEqual(len(result["drafts"]),2)
        self.assertEqual({draft["po_status"] for draft in result["drafts"]},{"DRAFT"})
        beta=next(draft for draft in result["drafts"] if draft["vendor_id"]==self.vendor_b)
        self.assertEqual((beta["lines"][0]["cases"],beta["lines"][0]["loose_units"]),(1,2))
        self.assertEqual(beta["lines"][0]["line_total"],Decimal("140.00"))
        self.assertEqual(beta["lines"][0]["merchandise_total"],Decimal("140.00"))
        self.assertEqual(beta["lines"][0]["loose_order_fee"],Decimal("0.00"))
        self.assertEqual(beta["minimum_disposition"],"NOT_APPLICABLE")
        alpha=next(draft for draft in result["drafts"] if draft["vendor_id"]==self.vendor_a)
        self.assertEqual(alpha["lines"][0]["line_total"],Decimal("10.01"))
        self.assertEqual(alpha["minimum_disposition"],"PAY_FEE")
        self.assertEqual(alpha["economics_confirmed_by"],"test-builder")
        self.assertFalse(result["release_performed"])

    def test_rejected_item_is_absent_and_draft_replay_adds_nothing(self):
        run=self._prepare(); self._review_all(run,reject_beta=True)
        first=self._build_drafts(run,actor="test-builder")
        self.assertEqual(len(first["drafts"]),1)
        count=self.conn.execute("SELECT count(*) FROM purchase_order_lines").fetchone()[0]
        self.conn.commit()
        replay=self._build_drafts(run,actor="test-builder")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM purchase_order_lines").fetchone()[0],count)

    def test_draft_never_counts_as_incoming_and_built_evidence_is_immutable(self):
        run=self._prepare(); self._review_all(run); self._build_drafts(run)
        position=open_po_position(self.conn,variant_id=self.variant_a,vendor_id=self.vendor_a)
        self.assertEqual(position["trusted_incoming_units"],0)
        po_id=get_vendor_drafts(self.conn,run["run_id"])["drafts"][0]["po_id"]
        with self.assertRaises(Exception):
            self.conn.execute("UPDATE purchase_orders SET po_status='FINAL' WHERE po_id=%s",(po_id,))
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT po_status FROM purchase_orders WHERE po_id=%s",(po_id,)).fetchone()[0],"DRAFT")

    def test_internal_csv_is_deterministic_labeled_and_formula_safe(self):
        draft={
            "po_id":"draft-1","vendor_name":"=MALICIOUS",
            "merchandise_total":Decimal("60"),"delivery_fee":Decimal("5"),
            "loose_order_fee_total":Decimal("0"),"below_minimum_fee":Decimal("5"),
            "po_total":Decimal("65"),"below_vendor_minimum":True,"lines":[{
                "po_line_id":1,"variant_id":"+123","supplier_sku":"@sku","cases":1,
                "loose_units":0,"ordered_units":6,"unit_cost":Decimal("10"),
                "case_price":Decimal("60"),"merchandise_total":Decimal("60"),
                "loose_order_fee":Decimal("0"),"line_total":Decimal("60"),
            }],
        }
        first=render_vendor_draft_csv("run-1",draft)
        self.assertEqual(first,render_vendor_draft_csv("run-1",draft))
        text=first.decode()
        self.assertIn("TEST DATA — NOT FOR ORDERING",text)
        self.assertIn(FORMAT_WARNING,text)
        self.assertIn("'=MALICIOUS",text)
        self.assertIn("'@sku",text)

    def test_packet_is_deterministic_complete_and_idempotent(self):
        run=self._prepare(); self._review_all(run); self._build_drafts(run)
        packet=build_emergency_review_packet(self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner")
        payload=self.storage.get_bytes(packet["storage_key"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(),packet["sha256"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names=archive.namelist()
            self.assertIn("manifest.json",names); self.assertIn("packet-summary.json",names)
            self.assertEqual(sum(name.endswith(".internal.csv") for name in names),2)
            for required in (
                "recommendations-and-reasons.json","frozen-price-economics.json",
                "supplier-mapping-evidence.json","open-po-ledger-evidence.json",
                "draft-readiness-evidence.json","frozen-input-manifest.json",
                "blocked-item-exclusions.json","material-edit-confirmations.json",
            ):
                self.assertIn(required,names)
            self.assertIn("TEST DATA — NOT FOR ORDERING",archive.read("packet-summary.json").decode())
            self.assertIn("'=synthetic edit",archive.read("human-review-decisions.csv").decode())
            summary=json.loads(archive.read("packet-summary.json"))
            self.assertEqual(len(summary["vendor_draft_economics"]),2)
            alpha_economics=next(
                item for item in summary["vendor_draft_economics"]
                if item["vendor_id"]==self.vendor_a
            )
            self.assertEqual(alpha_economics["minimum_disposition"],"PAY_FEE")
            self.assertEqual(alpha_economics["minimum_shortfall"],"89.99")
            self.assertEqual(alpha_economics["economics_confirmed_by"],"builder")
            self.assertRegex(alpha_economics["draft_preview_fingerprint"],r"^[0-9a-f]{64}$")
            beta_economics=next(
                item for item in summary["vendor_draft_economics"]
                if item["vendor_id"]==self.vendor_b
            )
            self.assertEqual(beta_economics["minimum_disposition"],"NOT_APPLICABLE")
            self.assertEqual(beta_economics["loose_order_fee_total"],"0.00")
            self.assertEqual(beta_economics["below_minimum_fee"],"0")
            readiness=json.loads(archive.read("draft-readiness-evidence.json"))
            self.assertEqual(len(readiness["items"]),2)
            self.assertTrue(
                all(
                    not evidence["blockers"]
                    for item in readiness["items"]
                    for evidence in item["readiness_by_variant"]
                )
            )
            confirmations=json.loads(archive.read("material-edit-confirmations.json"))
            self.assertEqual(len(confirmations["items"]),1)
            confirmation=confirmations["items"][0]
            self.assertEqual(confirmation["action"],"CONFIRM_MATERIAL_EDIT")
            self.assertEqual(confirmation["input_fingerprint"],run["input_fingerprint"])
            self.assertRegex(confirmation["review_preview_fingerprint"],r"^[0-9a-f]{64}$")
            self.assertEqual(confirmation["confirmed_by"],"test-reviewer")
            self.assertEqual(confirmation["reason"],"synthetic material edit confirmation")
            self.assertEqual(Decimal(confirmation["approved_cases"]),Decimal("1"))
            self.assertEqual(Decimal(confirmation["approved_loose_units"]),Decimal("2"))
            self.assertEqual(Decimal(confirmation["approved_units"]),Decimal("14"))
            self.assertEqual(confirmation["evidence"]["materiality_tier"],"MATERIAL")
            self.assertEqual(confirmation["evidence"]["baseline_multiplier"],"4.6667")
            self.assertEqual(confirmation["evidence"]["recommended_line_cash"],"30.00")
            self.assertEqual(confirmation["evidence"]["incremental_line_cash"],"110.00")
            self.assertEqual(confirmation["evidence"]["final_line_cash"],"140.00")
            self.assertEqual(
                confirmation["evidence"]["policy"],
                {
                    "max_normal_baseline_multiplier":"2.0",
                    "max_normal_resulting_days_supply":"30.0",
                    "owner_approval_status":"PENDING_OWNER_APPROVAL",
                    "policy_version":"EMERGENCY_MONDAY_MATERIAL_EDIT_V1",
                },
            )
            for name in (entry for entry in names if entry.endswith(".internal.csv")):
                rows=list(csv.DictReader(io.StringIO(archive.read(name).decode())))
                self.assertTrue(rows)
                merchandise=sum(
                    (Decimal(row["line_merchandise_total"]) for row in rows),
                    Decimal("0"),
                )
                self.assertEqual(merchandise,Decimal(rows[0]["vendor_merchandise_total"]))
                self.assertEqual(
                    Decimal(rows[0]["vendor_po_total"]),
                    merchandise+Decimal(rows[0]["vendor_delivery_fee"]),
                )
                self.assertIn(rows[0]["minimum_disposition"],{"PAY_FEE","NOT_APPLICABLE"})
                self.assertRegex(rows[0]["draft_preview_fingerprint"],r"^[0-9a-f]{64}$")
        replay=build_emergency_review_packet(self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner")
        self.assertTrue(replay["idempotent_replay"]); self.assertEqual(replay["sha256"],packet["sha256"])

    def test_packet_tamper_fails_closed_without_rebuild(self):
        run=self._prepare(); self._review_all(run); self._build_drafts(run)
        packet=build_emergency_review_packet(self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner")
        vendor_key=self.conn.execute(
            """SELECT storage_key FROM monday_run_artifacts
                WHERE run_id=%s AND artifact_type='VENDOR_INTERNAL_CSV' ORDER BY vendor_id LIMIT 1""",
            (run["run_id"],),
        ).fetchone()[0]
        self.conn.commit()
        original_vendor=self.storage.get_bytes(vendor_key)
        self.storage.put_bytes(vendor_key,b"tampered vendor CSV")
        with self.assertRaisesRegex(EmergencyPacketError,"hash mismatch"):
            build_emergency_review_packet(self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner")
        self.storage.put_bytes(vendor_key,original_vendor)
        self.storage.put_bytes(packet["storage_key"],b"tampered")
        with self.assertRaisesRegex(EmergencyPacketError,"hash mismatch"):
            build_emergency_review_packet(self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner")

    def test_db_payload_evidence_keeps_missing_storage_copy_readable(self):
        run=self._prepare("db-artifact-evidence"); self._review_all(run)
        self._build_drafts(run)
        packet=build_emergency_review_packet(
            self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner"
        )
        artifact_id=self.conn.execute(
            """SELECT monday_run_artifact_id FROM monday_run_artifacts
                WHERE run_id=%s AND artifact_type='EMERGENCY_REVIEW_PACKET'""",
            (run["run_id"],),
        ).fetchone()[0]
        expected=self.storage.get_bytes(packet["storage_key"])
        (Path(self.temp.name)/packet["storage_key"]).unlink()
        recovered=read_monday_artifact(
            self.conn,storage=self.storage,run_id=run["run_id"],artifact_id=artifact_id
        )
        self.assertEqual(recovered["data"],expected)
        replay=build_emergency_review_packet(
            self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner"
        )
        self.assertTrue(replay["idempotent_replay"])

    def test_stage_reversal_late_evidence_and_run_identity_changes_are_blocked(self):
        run=self._prepare()
        for statement,params in (
            ("UPDATE runs SET workflow_stage='PREPARING' WHERE run_id=%s",(run["run_id"],)),
            ("UPDATE runs SET input_fingerprint=%s WHERE run_id=%s",("f"*64,run["run_id"])),
            ("UPDATE forecast_results SET diagnostics='{}'::jsonb WHERE run_id=%s",(run["run_id"],)),
            ("INSERT INTO exceptions(run_id,exception_type,severity,message) VALUES (%s,'LATE','HIGH','late')",(run["run_id"],)),
        ):
            with self.subTest(statement=statement),self.assertRaises(Exception):
                self.conn.execute(statement,params)
            self.conn.rollback()

    def test_live_material_input_change_invalidates_reviewed_run_before_draft(self):
        run=self._prepare(); self._review_all(run)
        self.conn.execute(
            "UPDATE vendor_operating_rules SET order_days=ARRAY['TUESDAY'] WHERE vendor_id=%s",
            (self.vendor_a,),
        )
        self.conn.commit()
        with self.assertRaisesRegex(DraftPoError,"material recommendation inputs changed"):
            self._build_drafts(run)
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_orders WHERE run_id=%s",(run["run_id"],)).fetchone()[0],0,
        )

    def test_expired_offer_is_a_visible_affected_item_blocker(self):
        variant_id="3003"
        self.conn.execute(
            """INSERT INTO variants(variant_id,product_id,product_title,variant_title,active,
                       catalog_state,identity_scope,sku)
                VALUES (%s,%s,'Expired synthetic offer','750ML',TRUE,'LIVE','CURRENT','EXPIRED-3003')""",
            (variant_id,"product-3003"),
        )
        self.conn.execute(
            """INSERT INTO supplier_offers(
                       variant_id,vendor_id,supplier_sku,supplier_description,package_type,
                       size_text,raw_pack,shopify_units_per_case,qualifying_units_per_case,
                       assortment_scope,assortable,active,confidence,source_file,valid_to)
                VALUES (%s,%s,'EXPIRED-3003','Expired synthetic offer','STANDARD','750ML',
                        '6x750ML',6,6,'PRODUCT',FALSE,TRUE,'VERIFIED','synthetic-test.csv',%s)""",
            (variant_id,self.vendor_a,BUSINESS_DATE-timedelta(days=1)),
        )
        self.conn.commit()
        result=prepare_monday_run(
            self.conn,business_date=BUSINESS_DATE,idempotency_key="expired-offer",
            variant_ids=(variant_id,),actor="test-owner",
        )
        self.assertEqual(result["recommendations"],[])
        self.assertEqual(len(result["blockers"]),1)
        self.assertIn("SUPPLIER_OFFER_NOT_VALID_FOR_BUSINESS_DATE",result["blockers"][0]["message"])

    def test_computed_stale_inventory_gate_blocks_draft_atomically(self):
        run=self._prepare("stale-readiness"); self._review_all(run)
        gate=recompute_inventory_history_gate(
            self.conn,as_of_date=BUSINESS_DATE+timedelta(days=2),stale_after_days=1,
        )
        self.assertEqual((gate["status"],gate["blocks_po"]),("FAIL",True))
        with self.assertRaisesRegex(DraftPoError,"readiness blocks DRAFT"):
            self._build_drafts(run)
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM purchase_orders WHERE run_id=%s),
                          (SELECT count(*) FROM purchase_order_lines l JOIN purchase_orders p ON p.po_id=l.po_id WHERE p.run_id=%s),
                          (SELECT workflow_stage FROM runs WHERE run_id=%s)""",
                (run["run_id"],run["run_id"],run["run_id"]),
            ).fetchone(),(0,0,"REVIEWED"),
        )

    def test_review_queue_surfaces_recalculated_approved_economics(self):
        run=self._prepare("reviewed-economics"); self._review_all(run)
        queue=api.list_review_queue(self.conn,run["run_id"])
        alpha=next(item for item in queue["items"] if item["variant_id"]==self.variant_a)
        beta=next(item for item in queue["items"] if item["variant_id"]==self.variant_b)
        self.assertEqual(
            (
                alpha["approved_unit_cost"],alpha["approved_case_price"],
                alpha["approved_merchandise_total"],alpha["approved_line_total"],
            ),
            (Decimal("1.6600"),Decimal("10.0100"),Decimal("10.01"),Decimal("10.01")),
        )
        self.assertEqual((beta["approved_units"],beta["approved_unit_cost"],beta["approved_line_total"]),(14,Decimal("10"),Decimal("140.00")))
        rendered=api._monday_run_html(run,queue,get_vendor_drafts(self.conn,run["run_id"]),[])
        self.assertIn("reviewed line total $140.00",rendered)

    def test_direct_review_with_unfrozen_economics_is_rejected(self):
        run=self._prepare()
        item=run["recommendations"][0]
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO review_decisions(
                           run_id,recommendation_id,decision_type,scope,action,decided_by,
                           input_fingerprint,decision_fingerprint,approved_cases,
                           approved_loose_units,approved_units,approved_unit_cost,
                           approved_line_total,evidence_json)
                    SELECT run_id,recommendation_id,'PROCUREMENT_RECOMMENDATION','RUN_ONLY',
                           'ACCEPT','attacker',input_fingerprint,%s,recommended_cases,
                           recommended_loose_units,recommended_units,999,999,
                           '{"source":"forged"}'::jsonb
                      FROM procurement_recommendations WHERE recommendation_id=%s""",
                ("e"*64,item["recommendation_id"]),
            )
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM review_decisions WHERE run_id=%s",(run["run_id"],)).fetchone()[0],0,
        )

    def test_draft_build_failure_rolls_back_all_vendors_and_can_retry(self):
        run=self._prepare(); self._review_all(run)
        self.conn.execute(
            f"""CREATE FUNCTION {self.schema}.fail_draft_fixture() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'synthetic draft failure'; END $$"""
        )
        self.conn.execute(
            f"""CREATE TRIGGER fail_draft_fixture BEFORE INSERT ON purchase_order_lines
                FOR EACH ROW WHEN (NEW.variant_id='{self.variant_b}')
                EXECUTE FUNCTION {self.schema}.fail_draft_fixture()"""
        )
        self.conn.commit()
        with self.assertRaisesRegex(Exception,"synthetic draft failure"):
            self._build_drafts(run)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM purchase_orders WHERE run_id=%s",(run["run_id"],)).fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT workflow_stage FROM runs WHERE run_id=%s",(run["run_id"],)).fetchone()[0],"REVIEWED")
        self.conn.execute("DROP TRIGGER fail_draft_fixture ON purchase_order_lines")
        self.conn.execute(f"DROP FUNCTION {self.schema}.fail_draft_fixture()")
        self.conn.commit()
        self.assertEqual(len(self._build_drafts(run)["drafts"]),2)

    def test_packet_terminal_vendor_set_and_append_only_state_are_enforced(self):
        run=self._prepare(); self._review_all(run); self._build_drafts(run)
        build_emergency_review_packet(self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner")
        extra_vendor=self.conn.execute("INSERT INTO vendors(vendor_name,active) VALUES ('Unrelated Artifact Vendor',TRUE) RETURNING vendor_id").fetchone()[0]
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO monday_run_artifacts(
                           run_id,artifact_type,vendor_id,storage_key,sha256,size_bytes,
                           content_type,safety_label,input_fingerprint,created_by)
                    VALUES (%s,'VENDOR_INTERNAL_CSV',%s,'forged.csv',%s,1,'text/csv',%s,%s,'attacker')""",
                (run["run_id"],extra_vendor,"d"*64,"TEST DATA — NOT FOR ORDERING",run["input_fingerprint"]),
            )
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT workflow_stage FROM runs WHERE run_id=%s",(run["run_id"],)).fetchone()[0],"PACKET_BUILT")

    def test_packet_terminal_rejects_artifact_metadata_without_build_event(self):
        run=self._prepare("forged-artifacts"); self._review_all(run)
        drafts=self._build_drafts(run)["drafts"]
        try:
            for index,draft in enumerate(drafts, start=1):
                payload=f"forged CSV {index}".encode()
                digest=hashlib.sha256(payload).hexdigest()
                self.conn.execute(
                    """INSERT INTO monday_run_artifacts(
                               run_id,artifact_type,vendor_id,storage_key,sha256,size_bytes,
                               payload,content_type,safety_label,input_fingerprint,created_by)
                        VALUES (%s,'VENDOR_INTERNAL_CSV',%s,%s,%s,%s,%s,'text/csv',%s,%s,'attacker')""",
                    (
                        run["run_id"],draft["vendor_id"],
                        f"monday-runs/{run['run_id']}/vendor-{draft['vendor_id']}/{digest}.internal.csv",
                        digest,len(payload),payload,"TEST DATA — NOT FOR ORDERING",run["input_fingerprint"],
                    ),
                )
            packet_payload=b"forged packet bytes"
            packet_digest=hashlib.sha256(packet_payload).hexdigest()
            self.conn.execute(
                """INSERT INTO monday_run_artifacts(
                           run_id,artifact_type,vendor_id,storage_key,sha256,size_bytes,
                           payload,content_type,safety_label,input_fingerprint,created_by)
                    VALUES (%s,'EMERGENCY_REVIEW_PACKET',NULL,%s,%s,%s,%s,'application/zip',%s,%s,'attacker')""",
                (
                    run["run_id"],f"monday-runs/{run['run_id']}/packet/{packet_digest}.review.zip",
                    packet_digest,len(packet_payload),packet_payload,
                    "TEST DATA — NOT FOR ORDERING",run["input_fingerprint"],
                ),
            )
            with self.assertRaisesRegex(Exception,"packet|artifact"):
                self.conn.execute(
                    "UPDATE runs SET workflow_stage='PACKET_BUILT' WHERE run_id=%s",
                    (run["run_id"],),
                )
        finally:
            self.conn.rollback()
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s",(run["run_id"],)).fetchone()[0],0,
        )
        self.assertEqual(
            self.conn.execute("SELECT workflow_stage FROM runs WHERE run_id=%s",(run["run_id"],)).fetchone()[0],"DRAFTS_BUILT",
        )

    def test_packet_build_event_atomically_matches_artifacts(self):
        run=self._prepare("packet-event"); self._review_all(run)
        self._build_drafts(run)
        packet=build_emergency_review_packet(
            self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner"
        )
        event=self.conn.execute(
            """SELECT artifact_set_sha256,packet_sha256,csv_count,artifact_count,transaction_id,
                      verification_method
                 FROM monday_packet_build_events WHERE run_id=%s""",
            (run["run_id"],),
        ).fetchone()
        self.assertEqual((event[1],event[2],event[3]),(packet["sha256"],2,3))
        self.assertEqual(event[5],"DB_PAYLOAD_AND_STORAGE_READBACK_SHA256_V1")
        self.assertEqual(
            event[0],self.conn.execute("SELECT monday_artifact_set_sha256(%s)",(run["run_id"],)).fetchone()[0],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s AND packet_build_transaction_id=%s",
                (run["run_id"],event[4]),
            ).fetchone()[0],3,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM monday_run_artifacts
                    WHERE run_id=%s AND octet_length(payload)=size_bytes
                      AND encode(digest(payload,'sha256'),'hex')=sha256""",
                (run["run_id"],),
            ).fetchone()[0],3,
        )

    def test_packet_transaction_failure_rolls_back_authority_then_retry_succeeds(self):
        run=self._prepare("packet-rollback"); self._review_all(run)
        self._build_drafts(run)
        with self.assertRaisesRegex(RuntimeError,"synthetic packet transaction failure"):
            build_emergency_review_packet(
                self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner",
                _inject_failure_before_stage=True,
            )
        self.assertGreater(len(self.storage.list_keys(f"monday-runs/{run['run_id']}/")),0)
        self.assertEqual(
            self.conn.execute(
                """SELECT (SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s),
                          (SELECT count(*) FROM monday_packet_build_events WHERE run_id=%s),
                          (SELECT workflow_stage FROM runs WHERE run_id=%s)""",
                (run["run_id"],run["run_id"],run["run_id"]),
            ).fetchone(),(0,0,"DRAFTS_BUILT"),
        )
        self.conn.commit()
        result=build_emergency_review_packet(
            self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner"
        )
        self.assertFalse(result["idempotent_replay"])

    def test_raw_date_specific_sales_changes_invalidate_same_key_even_when_aggregates_match(self):
        first_day=BUSINESS_DATE-timedelta(days=84)
        second_day=first_day+timedelta(days=1)
        self._prepare("raw-series-fingerprint")
        self.conn.execute(
            "UPDATE sales_daily SET units_sold=2 WHERE variant_id=%s AND sale_date=%s",
            (self.variant_a,second_day),
        )
        self.conn.execute(
            "UPDATE sales_daily SET units_sold=2 WHERE variant_id=%s AND sale_date=%s",
            (self.variant_a,first_day),
        )
        self.conn.execute(
            "UPDATE sales_daily SET units_sold=0 WHERE variant_id=%s AND sale_date=%s",
            (self.variant_a,second_day),
        )
        self.conn.commit()
        with self.assertRaisesRegex(MondayRecommendationError,"different frozen inputs"):
            self._prepare("raw-series-fingerprint")

    def test_ad_hoc_daily_inventory_cannot_override_completed_capture_evidence(self):
        self.conn.execute(
            """UPDATE daily_inventory_snapshots SET available_quantity=999,incoming_quantity=999,
                      inventory_snapshot_run_id=NULL,source='UNOWNED_AD_HOC'
                WHERE snapshot_date=%s AND variant_id=%s""",
            (BUSINESS_DATE,self.variant_a),
        )
        self.conn.commit()
        run=self._prepare("owned-inventory-only")
        alpha=next(item for item in run["recommendations"] if item["variant_id"]==self.variant_a)
        self.assertEqual(Decimal(alpha["metrics"]["available_units"]),Decimal("0"))
        self.assertEqual(Decimal(alpha["metrics"]["trusted_incoming_units"]),Decimal("0"))

    def test_all_reject_run_builds_zero_po_review_packet_and_replays(self):
        run=self._prepare("all-reject")
        for item in run["recommendations"]:
            record_recommendation_review(
                self.conn,recommendation_id=item["recommendation_id"],action="REJECT",
                actor="test-reviewer",comment="synthetic no-order decision",
                expected_input_fingerprint=run["input_fingerprint"],
            )
        drafts=self._build_drafts(run)
        self.assertEqual(drafts["drafts"],[])
        packet=build_emergency_review_packet(
            self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner"
        )
        self.assertFalse(packet["idempotent_replay"])
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_orders WHERE run_id=%s",(run["run_id"],)).fetchone()[0],0,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s",(run["run_id"],)).fetchone()[0],1,
        )
        replay=build_emergency_review_packet(
            self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner"
        )
        self.assertTrue(replay["idempotent_replay"])

    def test_two_connections_fail_closed_on_prepare_and_review_conflicts(self):
        other,_,_=validated_test_connection()
        try:
            other.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
            other.commit()
            with self.conn.transaction():
                self.conn.execute("SELECT pg_advisory_xact_lock(%s)",(MONDAY_ANALYSIS_LOCK,))
                with self.assertRaisesRegex(MondayRecommendationError,"lock is unavailable"):
                    prepare_monday_run(
                        other,business_date=BUSINESS_DATE,idempotency_key="concurrent-run",
                        variant_ids=(self.variant_a,self.variant_b),actor="other",
                    )
            run=prepare_monday_run(
                other,business_date=BUSINESS_DATE,idempotency_key="concurrent-run",
                variant_ids=(self.variant_a,self.variant_b),actor="other",
            )
            recommendation=run["recommendations"][0]
            with self.conn.transaction():
                self.conn.execute("SELECT pg_advisory_xact_lock(%s)",(REVIEW_LOCK,))
                with self.assertRaisesRegex(ProcurementReviewError,"lock is unavailable"):
                    record_recommendation_review(
                        other,recommendation_id=recommendation["recommendation_id"],action="ACCEPT",
                        actor="other",expected_input_fingerprint=run["input_fingerprint"],
                    )
            preview=preview_recommendation_review(
                other,recommendation_id=recommendation["recommendation_id"],action="ACCEPT",
                actor="other",expected_input_fingerprint=run["input_fingerprint"],
            )
            decision=record_recommendation_review(
                other,recommendation_id=recommendation["recommendation_id"],action="ACCEPT",
                actor="other",expected_input_fingerprint=run["input_fingerprint"],
                expected_review_preview_fingerprint=preview["preview_fingerprint"],
            )
            self.assertFalse(decision["idempotent_replay"])
        finally:
            other.close()

    def test_draft_and_packet_lock_conflicts_fail_closed_then_retry(self):
        run=self._prepare("draft-packet-locks"); self._review_all(run)
        other,_,_=validated_test_connection()
        try:
            other.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
            other.commit()
            with self.conn.transaction():
                self.conn.execute("SELECT pg_advisory_xact_lock(%s)",(DRAFT_BUILD_LOCK,))
                with self.assertRaisesRegex(DraftPoError,"lock is unavailable"):
                    build_vendor_drafts(other,run_id=run["run_id"],actor="other")
            self.assertEqual(
                other.execute(
                    "SELECT count(*) FROM purchase_orders WHERE run_id=%s",(run["run_id"],)
                ).fetchone()[0],0,
            )
            other.commit()
            built=self._build_drafts(run,actor="other",conn=other)
            self.assertEqual(len(built["drafts"]),2)
            with self.conn.transaction():
                self.conn.execute("SELECT pg_advisory_xact_lock(%s)",(PACKET_BUILD_LOCK,))
                with self.assertRaisesRegex(EmergencyPacketError,"lock is unavailable"):
                    build_emergency_review_packet(
                        other,storage=self.storage,run_id=run["run_id"],actor="other"
                    )
            self.assertEqual(
                other.execute(
                    """SELECT (SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s),
                              (SELECT count(*) FROM monday_packet_build_events WHERE run_id=%s),
                              (SELECT workflow_stage FROM runs WHERE run_id=%s)""",
                    (run["run_id"],run["run_id"],run["run_id"]),
                ).fetchone(),(0,0,"DRAFTS_BUILT"),
            )
            other.commit()
            packet=build_emergency_review_packet(
                other,storage=self.storage,run_id=run["run_id"],actor="other"
            )
            self.assertFalse(packet["idempotent_replay"])
        finally:
            other.close()

    def test_mixed_blocked_and_eligible_run_excludes_only_blocked_item(self):
        self.conn.execute(
            """UPDATE vendor_operating_rules
                  SET loose_unit_fee=3,rules_version=rules_version+1,updated_at=now()
                WHERE vendor_id=%s""",
            (self.vendor_b,),
        )
        recompute_vendor_rules_gates(self.conn)
        self.conn.commit()
        run=self._prepare("mixed-blocked-eligible")
        self.assertTrue(monday_run_inputs_match(self.conn,run["run_id"],run["input_fingerprint"]))
        self.assertEqual([item["variant_id"] for item in run["recommendations"]],[self.variant_a])
        self.assertEqual(len(run["blockers"]),1)
        blocker=run["blockers"][0]
        self.assertEqual(blocker["variant_id"],self.variant_b)
        self.assertIn("LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED",blocker["message"])
        original=self.conn.execute(
            """SELECT run_id,exception_type,severity,variant_id,vendor_id,offer_id,
                      supplier_sku,message,status,created_at
                 FROM exceptions WHERE exception_id=%s""",
            (blocker["exception_id"],),
        ).fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(ProcurementReviewError,"stale"):
            acknowledge_and_exclude_blocked_item(
                self.conn,run_id=run["run_id"],exception_id=blocker["exception_id"],
                actor="test-owner",reason="exclude unresolved loose-fee item from this run",
                expected_input_fingerprint="f"*64,
            )
        exclusion=acknowledge_and_exclude_blocked_item(
            self.conn,run_id=run["run_id"],exception_id=blocker["exception_id"],
            actor="test-owner",reason="exclude unresolved loose-fee item from this run",
            expected_input_fingerprint=run["input_fingerprint"],
        )
        self.assertFalse(exclusion["idempotent_replay"])
        replayed_exclusion=acknowledge_and_exclude_blocked_item(
            self.conn,run_id=run["run_id"],exception_id=blocker["exception_id"],
            actor="test-owner",reason="exclude unresolved loose-fee item from this run",
            expected_input_fingerprint=run["input_fingerprint"],
        )
        self.assertTrue(replayed_exclusion["idempotent_replay"])
        self.assertEqual(replayed_exclusion["exclusion_id"],exclusion["exclusion_id"])
        self.assertEqual(
            self.conn.execute(
                """SELECT run_id,exception_type,severity,variant_id,vendor_id,offer_id,
                          supplier_sku,message,status,created_at
                     FROM exceptions WHERE exception_id=%s""",
                (blocker["exception_id"],),
            ).fetchone(),
            original,
        )
        refreshed=api.get_monday_run(self.conn,run["run_id"])
        self.assertTrue(refreshed["blockers"][0]["excluded"])
        eligible=run["recommendations"][0]
        self.conn.commit()
        preview=preview_recommendation_review(
            self.conn,recommendation_id=eligible["recommendation_id"],action="ACCEPT",
            actor="reviewer",expected_input_fingerprint=run["input_fingerprint"],
        )
        record_recommendation_review(
            self.conn,recommendation_id=eligible["recommendation_id"],action="ACCEPT",
            actor="reviewer",expected_input_fingerprint=run["input_fingerprint"],
            expected_review_preview_fingerprint=preview["preview_fingerprint"],
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT workflow_stage FROM runs WHERE run_id=%s",(run["run_id"],)
            ).fetchone()[0],"REVIEWED",
        )
        self.conn.commit()
        drafts=self._build_drafts(run,actor="builder")
        self.assertEqual(len(drafts["drafts"]),1)
        self.assertEqual(
            (
                drafts["drafts"][0]["vendor_id"],
                drafts["drafts"][0]["lines"][0]["variant_id"],
                drafts["drafts"][0]["lines"][0]["line_total"],
            ),
            (self.vendor_a,self.variant_a,Decimal("10.01")),
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM procurement_recommendations WHERE run_id=%s AND variant_id=%s""",
                (run["run_id"],self.variant_b),
            ).fetchone()[0],0,
        )
        self.conn.commit()
        packet=build_emergency_review_packet(
            self.conn,storage=self.storage,run_id=run["run_id"],actor="packet-owner"
        )
        with zipfile.ZipFile(io.BytesIO(self.storage.get_bytes(packet["storage_key"]))) as archive:
            evidence=json.loads(archive.read("blocked-item-exclusions.json"))
            self.assertEqual(len(evidence["items"]),1)
            self.assertEqual(
                evidence["items"][0]["run_only_exclusion"]["action"],
                "ACKNOWLEDGE_AND_EXCLUDE",
            )
            self.assertIn(
                "LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED",
                evidence["items"][0]["message"],
            )
        self.conn.commit()
        with self.assertRaises(Exception):
            self.conn.execute(
                "UPDATE monday_run_blocker_exclusions SET reason='changed' WHERE exclusion_id=%s",
                (exclusion["exclusion_id"],),
            )
        self.conn.rollback()
        with self.assertRaises(Exception):
            self.conn.execute(
                "DELETE FROM monday_run_blocker_exclusions WHERE exclusion_id=%s",
                (exclusion["exclusion_id"],),
            )
        self.conn.rollback()

    def test_same_day_second_run_is_blocked_after_draft_and_claim_cannot_release(self):
        first=self._prepare("sole-active-monday-run")
        self._review_all(first)
        built=self._build_drafts(first)
        original_ids={draft["po_id"] for draft in built["drafts"]}
        original_counts=self.conn.execute(
            """SELECT count(*),(SELECT count(*) FROM purchase_order_lines l
                    JOIN purchase_orders p ON p.po_id=l.po_id WHERE p.run_id=%s)
                 FROM purchase_orders WHERE run_id=%s""",
            (first["run_id"],first["run_id"]),
        ).fetchone()
        self.conn.commit()
        with self.assertRaisesRegex(Exception,"cannot release its business-date claim"):
            self.conn.execute(
                "UPDATE runs SET workflow_stage='FAILED',status='FAILED' WHERE run_id=%s",
                (first["run_id"],),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(
            MondayRecommendationError,"active Monday Procurement run already exists"
        ):
            prepare_monday_run(
                self.conn,business_date=BUSINESS_DATE,
                idempotency_key="forbidden-same-day-replacement",
                variant_ids=(self.variant_a,self.variant_b),actor="second-operator",
            )
        self.assertEqual(
            original_counts,
            self.conn.execute(
                """SELECT count(*),(SELECT count(*) FROM purchase_order_lines l
                        JOIN purchase_orders p ON p.po_id=l.po_id WHERE p.run_id=%s)
                     FROM purchase_orders WHERE run_id=%s""",
                (first["run_id"],first["run_id"]),
            ).fetchone(),
        )
        self.assertEqual(
            {
                str(row[0]) for row in self.conn.execute(
                    "SELECT po_id FROM purchase_orders WHERE run_id=%s",(first["run_id"],)
                ).fetchall()
            },original_ids,
        )

    def test_prebuild_failed_run_releases_business_date_for_one_replacement(self):
        first=self._prepare("failed-before-draft")
        self.conn.execute(
            "UPDATE runs SET workflow_stage='FAILED',status='FAILED' WHERE run_id=%s",
            (first["run_id"],),
        )
        self.conn.commit()
        with self.assertRaisesRegex(Exception,"DRAFT requires the run to hold"):
            self.conn.execute(
                """INSERT INTO purchase_orders(run_id,vendor_id,po_status)
                    VALUES (%s,%s,'DRAFT')""",
                (first["run_id"],self.vendor_a),
            )
        self.conn.rollback()
        second=self._prepare("replacement-after-clean-failure")
        self.assertNotEqual(first["run_id"],second["run_id"])
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM runs WHERE run_type='MONDAY_PROCUREMENT'
                      AND business_date=%s AND status='RUNNING'""",
                (BUSINESS_DATE,),
            ).fetchone()[0],1,
        )

    def test_migration_rejects_preexisting_nonrunning_monday_draft(self):
        run=self._prepare("pre-013-nonrunning-draft")
        self._review_all(run)
        self._build_drafts(run)
        self.conn.commit()
        self.conn.execute(
            "DROP TRIGGER trg_guard_monday_p1_failed_run_with_draft ON runs"
        )
        self.conn.execute("DROP INDEX uq_active_monday_run_business_date")
        self.conn.commit()
        self.conn.execute(
            "UPDATE runs SET workflow_stage='FAILED',status='FAILED' WHERE run_id=%s",
            (run["run_id"],),
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                """SELECT r.status,p.po_status FROM runs r
                    JOIN purchase_orders p ON p.run_id=r.run_id
                   WHERE r.run_id=%s LIMIT 1""",
                (run["run_id"],),
            ).fetchone(),
            ("FAILED","DRAFT"),
        )
        self.conn.commit()
        with self.assertRaisesRegex(
            Exception,"non-running Monday run owns a DRAFT"
        ):
            self.conn.execute((DB_DIR/"013_monday_p1_remediation.sql").read_text())
        self.conn.rollback()

    def test_migration_rejects_active_pre_p1_run_without_frozen_policy(self):
        upgrade_schema=f"monday_p1_upgrade_{uuid.uuid4().hex}"
        def cleanup_upgrade_schema():
            cleanup_conn,_,_=validated_test_connection()
            try:
                cleanup_conn.execute("SET search_path TO public")
                cleanup_conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(upgrade_schema)
                    )
                )
                cleanup_conn.commit()
            finally:
                cleanup_conn.close()
        self.addCleanup(cleanup_upgrade_schema)
        self.conn.commit()
        self.conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(upgrade_schema)))
        self.conn.execute(
            sql.SQL("SET search_path TO {}, {}, public").format(
                sql.Identifier(upgrade_schema),sql.Identifier(self.schema)
            )
        )
        for name in PRE_PRICE_MIGRATIONS+POST_PRICE_MIGRATIONS[:-1]:
            self.conn.execute((DB_DIR/name).read_text())
        legacy_manifest=json.dumps({"variant_ids":["legacy-blocked-variant"]})
        legacy_fingerprint=hashlib.sha256(legacy_manifest.encode()).hexdigest()
        self.conn.execute(
            """INSERT INTO runs(
                       run_type,status,business_date,idempotency_key,input_fingerprint,
                       workflow_stage,model_version,notes,procurement_output_mode,
                       procurement_input_manifest)
                VALUES ('MONDAY_PROCUREMENT','RUNNING',%s,'legacy-pre-p1',%s,
                        'PREPARING','legacy-pre-p1','synthetic legacy upgrade probe',
                        'INTERNAL_DRAFT_ONLY',%s)""",
            (BUSINESS_DATE,legacy_fingerprint,legacy_manifest),
        )
        self.conn.commit()
        with self.assertRaisesRegex(
            Exception,"active legacy Monday run lacks the frozen policy"
        ):
            self.conn.execute((DB_DIR/"013_monday_p1_remediation.sql").read_text())
        self.conn.rollback()
        self.assertIsNone(
            self.conn.execute(
                "SELECT to_regclass(%s)",
                (f"{upgrade_schema}.monday_run_blocker_exclusions",),
            ).fetchone()[0]
        )
        self.conn.commit()
        self.conn.execute(
            sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema))
        )
        self.conn.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(upgrade_schema))
        )
        self.conn.commit()

    def test_database_allows_only_one_concurrent_active_same_day_claim(self):
        manifest="{}"
        fingerprint=hashlib.sha256(manifest.encode()).hexdigest()
        barrier=Barrier(2)

        def claim(number):
            connection,_,_=validated_test_connection()
            try:
                connection.execute(
                    sql.SQL("SET search_path TO {}, public").format(
                        sql.Identifier(self.schema)
                    )
                )
                connection.commit()
                try:
                    with connection.transaction():
                        barrier.wait(timeout=5)
                        connection.execute(
                            """INSERT INTO runs(
                                       run_type,status,business_date,idempotency_key,
                                       input_fingerprint,workflow_stage,model_version,notes,
                                       procurement_output_mode,procurement_input_manifest)
                                VALUES ('MONDAY_PROCUREMENT','RUNNING',%s,%s,%s,
                                        'PREPARING','concurrency-test','synthetic',
                                        'INTERNAL_DRAFT_ONLY',%s)""",
                            (BUSINESS_DATE,f"direct-concurrent-{number}",fingerprint,manifest),
                        )
                    return "COMMIT"
                except UniqueViolation:
                    return "UNIQUE_VIOLATION"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results=sorted(pool.map(claim,(1,2)))
        self.assertEqual(results,["COMMIT","UNIQUE_VIOLATION"])
        self.assertEqual(
            self.conn.execute(
                """SELECT count(*) FROM runs WHERE run_type='MONDAY_PROCUREMENT'
                      AND business_date=%s AND status='RUNNING'""",
                (BUSINESS_DATE,),
            ).fetchone()[0],1,
        )

    def test_unrelated_incomplete_vendor_is_scoped_and_valid_vendors_build(self):
        incomplete=str(self.conn.execute(
            "INSERT INTO vendors(vendor_name,active) VALUES ('Incomplete Unrelated',TRUE) RETURNING vendor_id"
        ).fetchone()[0])
        evaluation=recompute_vendor_rules_gates(self.conn)
        self.assertEqual((evaluation["status"],evaluation["blocks_po"]),("WARN",False))
        self.conn.commit()
        # Reproduce the persisted pre-remediation summary: one unrelated
        # incomplete vendor used to poison the GLOBAL row for every vendor.
        self.conn.execute(
            """UPDATE readiness_gates
                  SET status='FAIL',blocks_po=TRUE,
                      message='legacy all-vendor failure',
                      evidence_json='{"legacy_global_fail":true}'::jsonb
                WHERE gate_name='VENDOR_RULES'
                  AND scope_type='GLOBAL' AND scope_id=''"""
        )
        self.conn.commit()
        self.conn.execute((DB_DIR/"013_monday_p1_remediation.sql").read_text())
        self.conn.commit()
        gates=self.conn.execute(
            """SELECT scope_type,scope_id,status,blocks_po FROM readiness_gates
                WHERE gate_name='VENDOR_RULES' ORDER BY scope_type,scope_id"""
        ).fetchall()
        self.assertIn(("GLOBAL","","WARN",False),gates)
        self.assertIn(("VENDOR",self.vendor_a,"PASS",False),gates)
        self.assertIn(("VENDOR",self.vendor_b,"PASS",False),gates)
        self.assertIn(("VENDOR",incomplete,"FAIL",True),gates)
        blocked=po_readiness(
            self.conn,vendor_id=incomplete,applicable_gate_names={"VENDOR_RULES"}
        )
        self.assertFalse(blocked["po_generation_enabled"])
        self.conn.commit()
        run=self._prepare("unrelated-incomplete-vendor")
        self._review_all(run)
        result=self._build_drafts(run)
        self.assertEqual({draft["vendor_id"] for draft in result["drafts"]},{self.vendor_a,self.vendor_b})

    def test_python_and_database_materiality_match_at_days_supply_boundary(self):
        capture_daily_inventory(
            self.conn,
            business_date=BUSINESS_DATE,
            captured_at=datetime(2026,9,7,13,tzinfo=timezone.utc),
            source="SYNTHETIC_DAYS_SUPPLY_BOUNDARY",
            rows=(
                {"variant_id":self.variant_a,"location_gid":"location-test",
                 "available_quantity":0,"incoming_quantity":0},
                {"variant_id":self.variant_b,"location_gid":"location-test",
                 "available_quantity":Decimal("0.0040"),"incoming_quantity":0},
            ),
        )
        self.conn.execute(
            """UPDATE vendor_operating_rules
                  SET order_cycle_days=10,lead_time_days=10,
                      rules_version=rules_version+1,updated_at=now()
                WHERE vendor_id=%s""",
            (self.vendor_b,),
        )
        recompute_vendor_rules_gates(self.conn)
        self.conn.commit()
        run=prepare_monday_run(
            self.conn,business_date=BUSINESS_DATE,
            idempotency_key="days-supply-sql-parity",
            variant_ids=(self.variant_b,),actor="test-owner",
        )
        item=run["recommendations"][0]
        self.assertEqual(item["baseline_units"],Decimal("20.0000"))
        raw_thirty_day_supply=self.conn.execute(
            """SELECT ((metrics->>'available_units')::numeric
                         +(metrics->>'trusted_incoming_units')::numeric+30)
                        /(metrics->>'forecast_daily_velocity')::numeric
                   FROM procurement_recommendations WHERE recommendation_id=%s""",
            (item["recommendation_id"],),
        ).fetchone()[0]
        self.assertEqual(raw_thirty_day_supply,Decimal("30.0040000000000000"))
        for units,tier in ((29,"NORMAL"),(30,"NORMAL"),(31,"MATERIAL")):
            with self.subTest(units=units):
                preview=preview_recommendation_review(
                    self.conn,recommendation_id=item["recommendation_id"],
                    action="EDIT_QUANTITY",actor="boundary-reviewer",
                    expected_input_fingerprint=run["input_fingerprint"],
                    approved_cases=2,approved_loose_units=units-24,
                    comment=f"{units}-day boundary probe",
                )
                database_tier=self.conn.execute(
                    "SELECT monday_edit_materiality(%s,%s)",
                    (item["recommendation_id"],units),
                ).fetchone()[0]
                self.assertEqual(preview["resulting_days_supply"],Decimal(units))
                self.assertEqual(preview["materiality"]["materiality_tier"],tier)
                self.assertEqual(database_tier,tier)

    def test_python_and_database_materiality_match_at_multiplier_boundary(self):
        run=prepare_monday_run(
            self.conn,business_date=BUSINESS_DATE,
            idempotency_key="multiplier-sql-parity",
            variant_ids=(self.variant_b,),actor="test-owner",
        )
        item=run["recommendations"][0]
        self.assertEqual(item["baseline_units"],Decimal("3.0000"))
        expected=(
            (5,"NORMAL",Decimal("1.6667"),Decimal("50.00"),Decimal("20.00")),
            (6,"NORMAL",Decimal("2.0000"),Decimal("60.00"),Decimal("30.00")),
            (7,"MATERIAL",Decimal("2.3333"),Decimal("70.00"),Decimal("40.00")),
        )
        for units,tier,multiplier,final_cash,incremental_cash in expected:
            with self.subTest(units=units):
                preview=preview_recommendation_review(
                    self.conn,recommendation_id=item["recommendation_id"],
                    action="EDIT_QUANTITY",actor="boundary-reviewer",
                    expected_input_fingerprint=run["input_fingerprint"],
                    approved_cases=0,approved_loose_units=units,
                    comment=f"{units}-unit multiplier boundary probe",
                )
                database_tier=self.conn.execute(
                    "SELECT monday_edit_materiality(%s,%s)",
                    (item["recommendation_id"],units),
                ).fetchone()[0]
                self.assertEqual(preview["materiality"]["materiality_tier"],tier)
                self.assertEqual(preview["materiality"]["baseline_multiplier"],multiplier)
                self.assertEqual(preview["materiality"]["final_line_cash"],final_cash)
                self.assertEqual(
                    preview["materiality"]["incremental_line_cash"],incremental_cash
                )
                self.assertEqual(database_tier,tier)

    def test_positive_loose_fee_blocks_loose_but_case_only_order_proceeds(self):
        self.conn.execute(
            """UPDATE vendor_operating_rules
                  SET loose_unit_fee=3,rules_version=rules_version+1,updated_at=now()
                WHERE vendor_id=%s""",
            (self.vendor_b,),
        )
        self.conn.execute(
            "UPDATE sales_daily SET units_sold=4 WHERE variant_id=%s AND source='SYNTHETIC_TEST'",
            (self.variant_b,),
        )
        self._refresh_synthetic_sales_gate()
        recompute_vendor_rules_gates(self.conn)
        self.conn.commit()
        run=prepare_monday_run(
            self.conn,business_date=BUSINESS_DATE,idempotency_key="case-only-positive-fee",
            variant_ids=(self.variant_b,),actor="test-owner",
        )
        self.assertEqual(run["blockers"],[])
        item=run["recommendations"][0]
        self.assertEqual((item["recommended_cases"],item["recommended_loose_units"]),(1,0))
        self.conn.commit()
        with self.assertRaisesRegex(Exception,"LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED"):
            self.conn.execute(
                """UPDATE procurement_recommendations
                      SET recommended_cases=0,recommended_loose_units=1,recommended_units=1
                    WHERE recommendation_id=%s""",
                (item["recommendation_id"],),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(
            ProcurementReviewError,"LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED"
        ):
            preview_recommendation_review(
                self.conn,recommendation_id=item["recommendation_id"],
                action="EDIT_QUANTITY",actor="reviewer",
                expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=0,approved_loose_units=1,comment="unsafe loose edit",
            )
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO review_decisions(
                           run_id,recommendation_id,decision_type,scope,action,comment,
                           decided_by,writeback_type,input_fingerprint,decision_fingerprint,
                           approved_cases,approved_loose_units,approved_units,
                           approved_unit_cost,approved_line_total,evidence_json)
                    VALUES (%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY','EDIT_QUANTITY',
                            'forged loose edit','attacker','EDIT_QUANTITY',%s,%s,
                            0,1,1,10,13,%s::jsonb)""",
                (
                    run["run_id"],item["recommendation_id"],run["input_fingerprint"],
                    "f"*64,json.dumps({"review":{
                        "approved_case_price":"120","approved_merchandise_total":"10",
                        "approved_loose_order_fee":"3",
                    }}),
                ),
            )
        self.conn.rollback()
        preview=preview_recommendation_review(
            self.conn,recommendation_id=item["recommendation_id"],action="ACCEPT",
            actor="reviewer",expected_input_fingerprint=run["input_fingerprint"],
        )
        decision=record_recommendation_review(
            self.conn,recommendation_id=item["recommendation_id"],action="ACCEPT",
            actor="reviewer",expected_input_fingerprint=run["input_fingerprint"],
            expected_review_preview_fingerprint=preview["preview_fingerprint"],
        )
        self.assertEqual(
            (decision["approved_cases"],decision["approved_loose_units"],decision["approved_line_total"]),
            (1,0,Decimal("120.00")),
        )
        drafts=self._build_drafts(run,actor="positive-fee-case-builder")
        self.assertEqual(len(drafts["drafts"]),1)
        draft=drafts["drafts"][0]
        self.assertEqual(
            (
                draft["merchandise_total"],draft["loose_order_fee_total"],
                draft["below_minimum_fee"],draft["delivery_fee"],draft["po_total"],
            ),
            (
                Decimal("120.00"),Decimal("0.00"),Decimal("0"),
                Decimal("0.00"),Decimal("120.00"),
            ),
        )
        self.assertEqual(
            (
                draft["lines"][0]["cases"],draft["lines"][0]["loose_units"],
                draft["lines"][0]["line_total"],
            ),
            (1,0,Decimal("120.00")),
        )

    def test_extreme_material_edit_requires_separate_audited_confirmation(self):
        run=self._prepare("extreme-material-edit")
        beta=next(item for item in run["recommendations"] if item["variant_id"]==self.variant_b)
        preview=preview_recommendation_review(
            self.conn,recommendation_id=beta["recommendation_id"],action="EDIT_QUANTITY",
            actor="risk-reviewer",expected_input_fingerprint=run["input_fingerprint"],
            approved_cases=250,approved_loose_units=0,comment="extreme synthetic probe",
        )
        materiality=preview["materiality"]
        self.assertEqual(materiality["materiality_tier"],"MATERIAL")
        self.assertEqual(materiality["baseline_multiplier"],Decimal("1000.0000"))
        self.assertEqual(materiality["final_line_cash"],Decimal("30000.00"))
        self.assertEqual(materiality["incremental_line_cash"],Decimal("29970.00"))
        with self.assertRaisesRegex(ProcurementReviewError,"distinct confirmation"):
            record_recommendation_review(
                self.conn,recommendation_id=beta["recommendation_id"],action="EDIT_QUANTITY",
                actor="risk-reviewer",expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=250,approved_loose_units=0,comment="extreme synthetic probe",
                expected_review_preview_fingerprint=preview["preview_fingerprint"],
            )
        self.assertEqual(self.conn.execute("SELECT count(*) FROM review_decisions").fetchone()[0],0)
        self.conn.commit()
        with self.assertRaisesRegex(Exception,"MATERIAL EDIT_QUANTITY requires"):
            self.conn.execute(
                """INSERT INTO review_decisions(
                           run_id,recommendation_id,decision_type,scope,action,comment,
                           decided_by,writeback_type,input_fingerprint,decision_fingerprint,
                           approved_cases,approved_loose_units,approved_units,
                           approved_unit_cost,approved_line_total,evidence_json)
                    VALUES (%s,%s,'PROCUREMENT_RECOMMENDATION','RUN_ONLY','EDIT_QUANTITY',
                            'extreme synthetic probe','direct-writer','EDIT_QUANTITY',%s,%s,
                            250,0,3000,10,30000,%s::jsonb)""",
                (
                    run["run_id"],beta["recommendation_id"],run["input_fingerprint"],
                    preview["preview_fingerprint"],json.dumps({"review":{
                        "approved_case_price":"120.0000",
                        "approved_merchandise_total":"30000.00",
                        "approved_loose_order_fee":"0.00",
                        "material_edit_confirmation_id":None,
                    }}),
                ),
            )
        self.conn.rollback()
        confirmation=confirm_material_recommendation_edit(
            self.conn,recommendation_id=beta["recommendation_id"],actor="risk-reviewer",
            expected_input_fingerprint=run["input_fingerprint"],
            expected_review_preview_fingerprint=preview["preview_fingerprint"],
            approved_cases=250,approved_loose_units=0,comment="extreme synthetic probe",
            confirmation_reason="explicitly reviewed 1000x baseline and $30000 cash",
        )
        stored_confirmation=self.conn.execute(
            """SELECT run_id::text,recommendation_id,input_fingerprint,
                      review_preview_fingerprint,approved_cases,approved_loose_units,
                      approved_units,action,confirmed_by,reason,evidence_json,created_at
                 FROM monday_material_edit_confirmations
                WHERE material_edit_confirmation_id=%s""",
            (confirmation["material_edit_confirmation_id"],),
        ).fetchone()
        self.assertEqual(
            stored_confirmation[:10],
            (
                run["run_id"],beta["recommendation_id"],run["input_fingerprint"],
                preview["preview_fingerprint"],Decimal("250"),Decimal("0"),
                Decimal("3000"),"CONFIRM_MATERIAL_EDIT","risk-reviewer",
                "explicitly reviewed 1000x baseline and $30000 cash",
            ),
        )
        self.assertEqual(stored_confirmation[10]["baseline_multiplier"],"1000.0000")
        self.assertEqual(stored_confirmation[10]["incremental_line_cash"],"29970.00")
        self.assertEqual(stored_confirmation[10]["final_line_cash"],"30000.00")
        self.assertEqual(stored_confirmation[10]["resulting_days_supply"],"3000.00")
        self.assertIsNotNone(stored_confirmation[11])
        self.conn.commit()
        with self.assertRaisesRegex(
            ProcurementReviewError,"previewed and confirmed|exact distinct confirmation"
        ):
            record_recommendation_review(
                self.conn,recommendation_id=beta["recommendation_id"],
                action="EDIT_QUANTITY",actor="risk-reviewer",
                expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=249,approved_loose_units=0,
                comment="extreme synthetic probe",
                expected_review_preview_fingerprint=preview["preview_fingerprint"],
                material_edit_confirmation_id=confirmation["material_edit_confirmation_id"],
            )
        decision=record_recommendation_review(
            self.conn,recommendation_id=beta["recommendation_id"],action="EDIT_QUANTITY",
            actor="risk-reviewer",expected_input_fingerprint=run["input_fingerprint"],
            approved_cases=250,approved_loose_units=0,comment="extreme synthetic probe",
            expected_review_preview_fingerprint=preview["preview_fingerprint"],
            material_edit_confirmation_id=confirmation["material_edit_confirmation_id"],
        )
        self.assertEqual(decision["approved_line_total"],Decimal("30000.00"))
        self.conn.commit()
        with self.assertRaises(Exception):
            self.conn.execute(
                """UPDATE monday_material_edit_confirmations SET reason='changed'
                    WHERE material_edit_confirmation_id=%s""",
                (confirmation["material_edit_confirmation_id"],),
            )
        self.conn.rollback()
        with self.assertRaises(Exception):
            self.conn.execute(
                """DELETE FROM monday_material_edit_confirmations
                    WHERE material_edit_confirmation_id=%s""",
                (confirmation["material_edit_confirmation_id"],),
            )
        self.conn.rollback()

    def test_malformed_pack_values_raise_typed_validation_errors(self):
        for value in (None,"",True,"NaN","1.5",0,-1):
            with self.subTest(value=value),self.assertRaises(MondayRecommendationError):
                parsed=recommendation_whole(value,"pack")
                if parsed < 1:
                    raise MondayRecommendationError("pack must be positive")

    def test_monday_http_surface_is_draft_only(self):
        route_methods = {
            route.path: sorted(route.methods)
            for route in api.app.routes
            if route.path.startswith("/monday-runs")
        }
        self.assertEqual(
            route_methods,
            {
                "/monday-runs": ["GET"],
                "/monday-runs/prepare": ["POST"],
                "/monday-runs/{run_id}": ["GET"],
                "/monday-runs/{run_id}/blockers/{exception_id}/exclude": ["POST"],
                "/monday-runs/{run_id}/recommendations/{recommendation_id}/review": ["POST"],
                "/monday-runs/{run_id}/build": ["POST"],
                "/monday-runs/{run_id}/artifacts/{artifact_id}": ["GET"],
            },
        )
        self.assertFalse(any("final" in path or "release" in path for path in route_methods))

    def test_monday_http_mutations_require_authorization_before_database_or_storage(self):
        client = TestClient(api.app)
        with patch.object(api, "_db_conn", side_effect=AssertionError("database reached")), patch.object(
            api, "get_storage", side_effect=AssertionError("storage reached")
        ):
            with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": ""}):
                response = client.post(
                    "/monday-runs/prepare",
                    data={
                        "business_date": str(BUSINESS_DATE), "idempotency_key": "blocked",
                        "variant_ids": self.variant_a, "actor": "test", "review_token": "anything",
                    },
                )
                self.assertEqual(response.status_code, 503)
            with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": "expected"}):
                response = client.post(
                    f"/monday-runs/{uuid.uuid4()}/recommendations/1/review",
                    data={
                        "action": "ACCEPT", "actor": "test",
                        "expected_input_fingerprint": "f" * 64,
                        "approved_cases": "0", "approved_loose_units": "0",
                        "comment": "", "review_token": "wrong",
                    },
                )
                self.assertEqual(response.status_code, 403)
                response = client.post(
                    f"/monday-runs/{uuid.uuid4()}/build",
                    data={"actor": "test", "review_token": "wrong"},
                )
                self.assertEqual(response.status_code, 403)
                response = client.post(
                    f"/monday-runs/{uuid.uuid4()}/blockers/1/exclude",
                    data={
                        "actor":"test","reason":"synthetic",
                        "expected_input_fingerprint":"f"*64,
                        "review_token":"wrong",
                    },
                )
                self.assertEqual(response.status_code,403)

    def test_monday_http_review_rejects_cross_run_recommendation_before_recording(self):
        first=self._prepare("http-run-a")
        self.conn.execute(
            "UPDATE runs SET workflow_stage='FAILED',status='FAILED' WHERE run_id=%s",
            (first["run_id"],),
        )
        self.conn.commit()
        second=self._prepare("http-run-b")
        foreign_recommendation=second["recommendations"][0]["recommendation_id"]
        token="synthetic-cross-run-token"
        client=TestClient(api.app)
        with patch.dict(os.environ,{"RECONCILIATION_REVIEW_TOKEN":token}), patch.object(
            api,"_db_conn",side_effect=lambda:nullcontext(self.conn)
        ), patch.object(
            api,"record_recommendation_review",side_effect=AssertionError("recorder reached")
        ):
            response=client.post(
                f"/monday-runs/{first['run_id']}/recommendations/{foreign_recommendation}/review",
                data={
                    "action":"ACCEPT","actor":"reviewer",
                    "expected_input_fingerprint":first["input_fingerprint"],
                    "approved_cases":"0","approved_loose_units":"0",
                    "comment":"","review_token":token,
                },
            )
        self.assertEqual(response.status_code,409)
        self.assertNotIn(token,response.text)

    def test_monday_http_gets_are_read_only_and_safety_labeled(self):
        run = self._prepare("http-read-only")
        self.conn.commit()
        before = self.conn.execute(
            """SELECT (SELECT count(*) FROM runs),
                      (SELECT count(*) FROM procurement_recommendations),
                      (SELECT count(*) FROM review_decisions),
                      (SELECT count(*) FROM purchase_orders),
                      (SELECT count(*) FROM monday_run_artifacts),
                      txid_current_if_assigned()"""
        ).fetchone()
        self.conn.commit()
        client = TestClient(api.app)
        with patch.object(api, "_db_conn", side_effect=lambda: nullcontext(self.conn)):
            listing = client.get("/monday-runs")
            detail = client.get(f"/monday-runs/{run['run_id']}")
        self.assertEqual((listing.status_code, detail.status_code), (200, 200))
        self.assertIn("TEST DATA — NOT FOR ORDERING", listing.text)
        self.assertIn("SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED", detail.text)
        self.assertEqual(listing.headers["cache-control"], "no-store")
        self.assertEqual(detail.headers["cache-control"], "no-store")
        after = self.conn.execute(
            """SELECT (SELECT count(*) FROM runs),
                      (SELECT count(*) FROM procurement_recommendations),
                      (SELECT count(*) FROM review_decisions),
                      (SELECT count(*) FROM purchase_orders),
                      (SELECT count(*) FROM monday_run_artifacts),
                      txid_current_if_assigned()"""
        ).fetchone()
        self.assertEqual(before, after)
        self.assertIsNone(after[-1])
        self.conn.commit()

    def test_real_http_chain_reviews_builds_downloads_and_replays_without_release(self):
        client = TestClient(api.app)
        token = "synthetic-http-review-token"
        with patch.dict(os.environ, {"RECONCILIATION_REVIEW_TOKEN": token}), patch.object(
            api, "_db_conn", side_effect=lambda: nullcontext(self.conn)
        ), patch.object(api, "get_storage", return_value=self.storage):
            prepared = client.post(
                "/monday-runs/prepare",
                data={
                    "business_date": str(BUSINESS_DATE), "idempotency_key": "http-e2e",
                    "variant_ids": f"{self.variant_a},{self.variant_b}",
                    "actor": "synthetic-http-operator", "review_token": token,
                },
                follow_redirects=False,
            )
            self.assertEqual(prepared.status_code, 303)
            self.assertEqual(
                urljoin("https://example.test/procurement/monday-runs/prepare", prepared.headers["location"]),
                "https://example.test/procurement/" + prepared.headers["location"].split("../", 1)[1],
            )
            run_id = prepared.headers["location"].rsplit("/", 1)[-1]
            run = api.get_monday_run(self.conn, run_id)
            for item in run["recommendations"]:
                is_beta = item["variant_id"] == self.variant_b
                review_data = {
                    "action": "EDIT_QUANTITY" if is_beta else "ACCEPT",
                    "actor": "synthetic-http-reviewer",
                    "expected_input_fingerprint": run["input_fingerprint"],
                    "approved_cases": "1" if is_beta else str(item["recommended_cases"]),
                    "approved_loose_units": "2" if is_beta else str(item["recommended_loose_units"]),
                    "comment": "=synthetic HTTP edit" if is_beta else "",
                    "review_token": token,
                }
                reviewed = client.post(
                    f"/monday-runs/{run_id}/recommendations/{item['recommendation_id']}/review",
                    data=review_data,
                    follow_redirects=False,
                )
                self.assertEqual(reviewed.status_code, 200)
                self.assertEqual(reviewed.headers["cache-control"], "no-store")
                if is_beta:
                    self.assertIn("Recalculated line total</dt><dd>$140.00", reviewed.text)
                    self.assertIn("Resulting inventory units</dt><dd>14", reviewed.text)
                    self.assertIn("Resulting days of supply</dt><dd>14.00", reviewed.text)
                    self.assertIn("Edit materiality</dt><dd>MATERIAL",reviewed.text)
                    self.assertIn("Raw baseline units</dt><dd>3.0000",reviewed.text)
                    self.assertIn("Original recommended units</dt><dd>3.0000",reviewed.text)
                    self.assertIn("Edited / baseline multiplier</dt><dd>4.6667x",reviewed.text)
                    self.assertIn("Original recommended line cash</dt><dd>$30.00",reviewed.text)
                    self.assertIn("Incremental line cash</dt><dd>$110.00",reviewed.text)
                    self.assertIn("Final line cash</dt><dd>$140.00",reviewed.text)
                    self.assertIn("EMERGENCY_MONDAY_MATERIAL_EDIT_V1",reviewed.text)
                self.assertNotIn(token, reviewed.text)
                match = re.search(
                    r"name='review_preview_fingerprint' value='([0-9a-f]{64})'",
                    reviewed.text,
                )
                self.assertIsNotNone(match)
                self.assertEqual(
                    self.conn.execute(
                        "SELECT count(*) FROM review_decisions WHERE recommendation_id=%s",
                        (item["recommendation_id"],),
                    ).fetchone()[0],
                    0,
                )
                self.conn.commit()
                review_data["review_preview_fingerprint"] = match.group(1)
                if is_beta:
                    review_data["material_confirmation_reason"] = (
                        "synthetic review of material quantity exposure"
                    )
                reviewed = client.post(
                    f"/monday-runs/{run_id}/recommendations/{item['recommendation_id']}/review",
                    data=review_data,
                    follow_redirects=False,
                )
                if is_beta:
                    self.assertEqual(reviewed.status_code, 200)
                    self.assertIn("Distinct MATERIAL-risk confirmation recorded",reviewed.text)
                    confirmation_match = re.search(
                        r"name='material_edit_confirmation_id' value='([0-9]+)'",
                        reviewed.text,
                    )
                    self.assertIsNotNone(confirmation_match)
                    review_data["material_edit_confirmation_id"] = confirmation_match.group(1)
                    reviewed = client.post(
                        f"/monday-runs/{run_id}/recommendations/{item['recommendation_id']}/review",
                        data=review_data,
                        follow_redirects=False,
                    )
                self.assertEqual(reviewed.status_code, 303)
            built = client.post(
                f"/monday-runs/{run_id}/build",
                data={"actor": "synthetic-http-builder", "review_token": token},
                follow_redirects=False,
            )
            self.assertEqual(built.status_code, 200)
            self.assertEqual(built.headers["cache-control"], "no-store")
            self.assertIn("Confirm vendor DRAFT economics", built.text)
            self.assertIn("$10.01", built.text)
            self.assertIn("89.99", built.text)
            self.assertIn("$5", built.text)
            self.assertIn("PAY_FEE", built.text)
            self.assertNotIn(token, built.text)
            preview_match = re.search(
                r"name='draft_preview_fingerprint' value='([0-9a-f]{64})'",
                built.text,
            )
            disposition_match = re.search(
                r"name='minimum_disposition' value='([^']+)'", built.text
            )
            self.assertIsNotNone(preview_match)
            self.assertIsNotNone(disposition_match)
            self.assertEqual(
                self.conn.execute(
                    "SELECT count(*) FROM purchase_orders WHERE run_id=%s", (run_id,)
                ).fetchone()[0],
                0,
            )
            self.conn.commit()
            built = client.post(
                f"/monday-runs/{run_id}/build",
                data={
                    "actor": "synthetic-http-builder",
                    "draft_preview_fingerprint": preview_match.group(1),
                    "minimum_disposition": disposition_match.group(1),
                    "review_token": token,
                },
                follow_redirects=False,
            )
            self.assertEqual(built.status_code, 303)
            detail = client.get(f"/monday-runs/{run_id}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("DRAFT", detail.text)
            self.assertIn("disposition PAY_FEE", detail.text)
            self.assertNotIn("Release PO", detail.text)
            artifacts = list_monday_artifacts(self.conn, run_id)
            self.assertEqual(len(artifacts), 3)
            downloads = {}
            for artifact in artifacts:
                response = client.get(
                    f"/monday-runs/{run_id}/artifacts/{artifact['artifact_id']}"
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                downloads[artifact["artifact_type"]] = response.content
            self.assertIn("TEST DATA — NOT FOR ORDERING", downloads["VENDOR_INTERNAL_CSV"].decode())
            with zipfile.ZipFile(io.BytesIO(downloads["EMERGENCY_REVIEW_PACKET"])) as archive:
                self.assertIn("TEST DATA — NOT FOR ORDERING", archive.read("packet-summary.json").decode())
            replay = client.post(
                f"/monday-runs/{run_id}/build",
                data={"actor": "synthetic-http-builder", "review_token": token},
                follow_redirects=False,
            )
            self.assertEqual(replay.status_code, 303)
        self.assertEqual(
            self.conn.execute(
                "SELECT count(*) FROM purchase_orders WHERE run_id=%s AND po_status='DRAFT'",
                (run_id,),
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM purchase_orders WHERE po_status<>'DRAFT'").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s", (run_id,)).fetchone()[0],
            3,
        )


class MondayMaterialEditPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy=MaterialEditPolicy(
            "EMERGENCY_MONDAY_MATERIAL_EDIT_V1","PENDING_OWNER_APPROVAL",
            Decimal("2.0"),Decimal("30.0"),
        )

    def _classify(self,*,baseline,edited,days,original,final):
        return classify_material_edit(
            action="EDIT_QUANTITY",baseline_units=Decimal(str(baseline)),
            recommended_units=Decimal(str(baseline)),edited_units=Decimal(str(edited)),
            resulting_days_supply=Decimal(str(days)) if days is not None else None,
            days_supply_status=(
                "CALCULATED_FROM_FROZEN_FORECAST" if days is not None
                else "UNDEFINED_ZERO_FORECAST"
            ),
            recommended_line_cash=Decimal(str(original)),
            final_line_cash=Decimal(str(final)),policy=self.policy,
        )

    def test_config_declares_exact_owner_reviewable_emergency_thresholds(self):
        loaded=load_material_edit_policy()
        self.assertEqual(loaded,self.policy)
        self.assertEqual(
            loaded.evidence(),
            {
                "policy_version":"EMERGENCY_MONDAY_MATERIAL_EDIT_V1",
                "owner_approval_status":"PENDING_OWNER_APPROVAL",
                "max_normal_baseline_multiplier":"2.0",
                "max_normal_resulting_days_supply":"30.0",
            },
        )

    def test_multiplier_boundary_is_normal_below_and_at_material_above(self):
        expected=(
            (5,"NORMAL",Decimal("1.6667"),Decimal("20"),Decimal("50")),
            (6,"NORMAL",Decimal("2.0000"),Decimal("30"),Decimal("60")),
            (7,"MATERIAL",Decimal("2.3333"),Decimal("40"),Decimal("70")),
        )
        for edited,tier,multiplier,incremental,final in expected:
            with self.subTest(edited=edited):
                result=self._classify(
                    baseline=3,edited=edited,days=edited,original=30,final=edited*10
                )
                self.assertEqual(
                    (
                        result["materiality_tier"],result["baseline_multiplier"],
                        result["incremental_line_cash"],result["final_line_cash"],
                    ),
                    (tier,multiplier,incremental,final),
                )

    def test_days_supply_boundary_is_normal_below_and_at_material_above(self):
        expected=(
            (29,Decimal("29.99"),"NORMAL",Decimal("1.4500"),Decimal("90"),Decimal("290")),
            (30,Decimal("30.00"),"NORMAL",Decimal("1.5000"),Decimal("100"),Decimal("300")),
            (31,Decimal("30.01"),"MATERIAL",Decimal("1.5500"),Decimal("110"),Decimal("310")),
        )
        for edited,days,tier,multiplier,incremental,final in expected:
            with self.subTest(days=days):
                result=self._classify(
                    baseline=20,edited=edited,days=days,original=200,final=final
                )
                self.assertEqual(result["materiality_tier"],tier)
                self.assertEqual(result["baseline_multiplier"],multiplier)
                self.assertEqual(result["incremental_line_cash"],incremental)
                self.assertEqual(result["final_line_cash"],final)
        zero_forecast=self._classify(
            baseline=3,edited=4,days=None,original=30,final=40
        )
        self.assertEqual(zero_forecast["materiality_tier"],"MATERIAL")
        self.assertIn(
            "EDIT_POSITIVE_WITH_ZERO_FORECAST",
            zero_forecast["materiality_reason_codes"],
        )
        downward_but_high_days=self._classify(
            baseline=40,edited=20,days=31,original=400,final=200
        )
        self.assertEqual(downward_but_high_days["materiality_tier"],"MATERIAL")
        self.assertEqual(
            downward_but_high_days["materiality_reason_codes"],
            ["EDIT_ABOVE_RESULTING_DAYS_SUPPLY"],
        )
        zero_order=self._classify(
            baseline=3,edited=0,days=None,original=30,final=0
        )
        self.assertEqual(zero_order["materiality_tier"],"NORMAL")


if __name__ == "__main__":
    unittest.main()
