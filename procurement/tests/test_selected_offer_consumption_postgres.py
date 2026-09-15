"""Causal PostgreSQL acceptance for synthetic selected-offer consumption."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
from uuid import uuid4

import psycopg
import zipfile

import initialize_synthetic_demo
import test_persistent_mapping_foundation_postgres as mapping_matrix
from procurement_os import recommendations
from procurement_os.catalog import recompute_catalog_gate
from procurement_os.draft_po import build_vendor_drafts, preview_vendor_drafts
from procurement_os.emergency_packet import EmergencyPacketError, build_emergency_review_packet
from procurement_os.inventory import capture_daily_inventory
from procurement_os.po_ledger import recompute_open_po_reconciliation_gate
from procurement_os.procurement_review import (
    ProcurementReviewError,
    confirm_material_recommendation_edit,
    preview_recommendation_review,
    record_recommendation_review,
)
from procurement_os.persistent_mapping import (
    execute_routine_offer_clear,
    preview_routine_offer_clear,
)
from procurement_os.storage import LocalFilesystemStorage
from procurement_os.synthetic_selected_offer import (
    CONTRACT as SELECTED_CONTRACT,
    SELECTION_LOCK_PREFIX,
    SyntheticSelectedOfferError,
    _pg_jsonb_sha256,
    classify_frozen_manifest,
    selected_blocker_evidence,
)
from procurement_os.vendor_rules import recompute_vendor_rules_gates


class SelectedOfferConsumptionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        mapping_matrix.PersistentMappingFoundationPostgresTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = mapping_matrix.PersistentMappingFoundationPostgresTests(
            "test_legacy_recommendations_are_identical_until_cutover"
        )
        self.fixture.setUp()

    def tearDown(self) -> None:
        try:
            self.fixture.tearDown()
        finally:
            self.fixture.doCleanups()

    def _reset_with_two_priced_active_offers(self) -> tuple[int, int]:
        fixture = self.fixture
        fixture._prepare_roles_and_schema()
        cutoff = mapping_matrix.apply_schema.MIGRATION_ORDER.index(
            "010_monday_po_ledger.sql"
        ) + 1
        with psycopg.connect(fixture.mapping_url) as conn:
            schema_oid = int(
                conn.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (mapping_matrix.SCHEMA,),
                ).fetchone()[0]
            )
            for name in mapping_matrix.apply_schema.MIGRATION_ORDER[:cutoff]:
                with conn.transaction():
                    mapping_matrix.apply_schema.apply_verified_legacy_file(
                        conn,
                        mapping_matrix.DB_DIR,
                        name,
                        schema_oid=schema_oid,
                    )
        fixture._seed_catalog()
        selected_offer_id = fixture._legacy_offer(sku="SUP-001")
        alternative_offer_id = fixture._legacy_offer(sku="SUP-ALT")
        with psycopg.connect(fixture.mapping_url) as conn:
            conn.execute(
                f"""INSERT INTO {mapping_matrix.SCHEMA}.prices(
                           offer_id,price_state,effective_month,level_type,
                           break_qty,break_unit,case_price,unit_price,source_file,
                           source_page,extraction_confidence,verified,notes)
                    VALUES
                       (%s,'current',DATE '2026-09-01','BASE',NULL,NULL,
                        31.5000,5.2500,'selected-offer-causal.csv',1,
                        'VERIFIED',true,'authentic pre-011 selected BASE'),
                       (%s,'current',DATE '2026-09-01','BREAK',2,'CS',
                        27.0000,4.5000,'selected-offer-causal.csv',2,
                        'VERIFIED',true,'authentic pre-011 selected BREAK'),
                       (%s,'current',DATE '2026-09-01','BASE',NULL,NULL,
                        60.0000,10.0000,'selected-offer-causal.csv',3,
                        'VERIFIED',true,'authentic pre-011 alternative BASE')""",
                (selected_offer_id, selected_offer_id, alternative_offer_id),
            )
        with psycopg.connect(fixture.mapping_url) as conn:
            schema_oid = int(
                conn.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (mapping_matrix.SCHEMA,),
                ).fetchone()[0]
            )
            for name in mapping_matrix.apply_schema.MIGRATION_ORDER[cutoff:-1]:
                with conn.transaction():
                    mapping_matrix.apply_schema.apply_verified_legacy_file(
                        conn,
                        mapping_matrix.DB_DIR,
                        name,
                        schema_oid=schema_oid,
                    )
            with conn.transaction():
                self.assertTrue(
                    mapping_matrix.apply_schema._verify_or_apply_mapping_release(
                        conn, mapping_matrix.DB_DIR
                    )
                )
            with conn.transaction():
                self.assertTrue(
                    mapping_matrix.apply_schema._verify_or_apply_post_mapping_release(
                        conn, mapping_matrix.DB_DIR
                    )
                )
        return selected_offer_id, alternative_offer_id

    def _seed_monday_evidence(self) -> datetime:
        evaluation_at = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
        with psycopg.connect(self.fixture.mapping_url, autocommit=True) as conn:
            with conn.transaction():
                conn.execute(
                    f"UPDATE {mapping_matrix.SCHEMA}.variants SET active=false "
                    "WHERE variant_id<>'1001'"
                )
                conn.execute(
                    f"""INSERT INTO {mapping_matrix.SCHEMA}.vendor_operating_rules(
                               vendor_id,order_days,order_cutoff_local,timezone_name,
                               expected_delivery_days,order_cycle_days,lead_time_days,
                               lead_time_variability_days,reliability_pct,minimum_type,
                               minimum_value,below_minimum_fee,loose_order_allowed,
                               confirmation_source,confirmed_by,rules_version)
                        VALUES (%s,ARRAY['SUNDAY'],'23:59','UTC',ARRAY['WEDNESDAY'],
                                2,1,0,1,'DOLLAR',60,5,false,
                                'selected-offer causal fixture',
                                'synthetic:matrix-owner:01',1)""",
                    (mapping_matrix.VENDOR_ID,),
                )
            # The fixed fixture date is intentional; only the backfill service's
            # wall-clock predicate is frozen. All pages, facts, controls and the
            # readiness result still flow through the production finalizer.
            with mock.patch(
                "procurement_os.historical_sales.run_end_was_current_store_date",
                return_value=True,
            ):
                sales_backfill_id = initialize_synthetic_demo._seed_evidence(
                    conn,
                    mapping_matrix.BUSINESS_DATE,
                    canonical_sales_end_date=mapping_matrix.BUSINESS_DATE
                    - timedelta(days=1),
                )
        self.assertRegex(sales_backfill_id, r"^[0-9a-f-]{36}$")
        return evaluation_at

    def test_selected_offer_drives_monday_when_legacy_is_ambiguous(self):
        selected_offer_id, alternative_offer_id = (
            self._reset_with_two_priced_active_offers()
        )
        evaluation_at = self._seed_monday_evidence()
        candidate_id = self.fixture._intake(self.fixture._packet(1))
        decision = self.fixture._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="causal selected-offer fixture",
            offer_id=selected_offer_id,
            link_kind="LINKED_EXISTING",
        )
        self.fixture._select(decision, reason="causal selected-offer selection")
        with psycopg.connect(self.fixture.mapping_url) as conn:
            shadow = conn.execute(
                f"""SELECT selected_offer_id,legacy_active_standard_count,
                           shadow_comparison
                      FROM {mapping_matrix.SCHEMA}.v_supplier_offer_selection_shadow
                     WHERE variant_id='1001'"""
            ).fetchone()
        self.assertEqual(
            shadow,
            (
                selected_offer_id,
                2,
                "LEGACY_HAS_MULTIPLE_ACTIVE_STANDARD",
            ),
        )
        self.assertNotEqual(selected_offer_id, alternative_offer_id)
        with mock.patch.dict(
            os.environ,
            {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
            clear=False,
        ):
            with mock.patch.object(
                recommendations, "_database_evaluation_at", return_value=evaluation_at
            ):
                with psycopg.connect(self.fixture.mapping_url) as conn:
                    run = recommendations.prepare_monday_run(
                        conn,
                        business_date=mapping_matrix.BUSINESS_DATE,
                        idempotency_key="selected-offer-causal-baseline",
                        variant_ids=("1001",),
                        actor="synthetic:matrix-owner:01",
                    )
                    replayed_run = recommendations.prepare_monday_run(
                        conn,
                        business_date=mapping_matrix.BUSINESS_DATE,
                        idempotency_key="selected-offer-causal-baseline",
                        variant_ids=("1001",),
                        actor="synthetic:matrix-owner:01",
                    )
        self.assertTrue(replayed_run["idempotent_replay"])
        self.assertEqual(replayed_run["run_id"], run["run_id"])
        self.assertEqual(
            replayed_run["input_fingerprint"], run["input_fingerprint"]
        )
        blocker_messages = [item["message"] for item in run["blockers"]]
        self.assertNotIn(
            "EXACTLY_ONE_ACTIVE_STANDARD_OFFER_REQUIRED", blocker_messages
        )
        self.assertEqual(blocker_messages, [])
        with psycopg.connect(self.fixture.mapping_url) as conn:
            frozen_manifest = json.loads(
                conn.execute(
                    f"SELECT procurement_input_manifest FROM {mapping_matrix.SCHEMA}.runs "
                    "WHERE run_id=%s",
                    (run["run_id"],),
                ).fetchone()[0]
            )

        def assert_resealed_manifest_refused(mutator):
            altered = json.loads(json.dumps(frozen_manifest))
            altered_evidence = altered["contexts"][0][
                "selected_offer_input_evidence"
            ]
            mutator(altered_evidence)
            altered["contexts"][0][
                "selected_offer_input_evidence_sha256"
            ] = hashlib.sha256(
                json.dumps(
                    altered_evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            with self.assertRaisesRegex(
                SyntheticSelectedOfferError,
                "^SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID$",
            ):
                classify_frozen_manifest(json.dumps(altered, sort_keys=True))

        def change_prior_head(evidence):
            event = evidence["selection_event"]
            event["expected_prior_head_version"] = 99
            event["canonical_payload"]["expected_prior_head_version"] = 99
            event["payload_sha256"] = _pg_jsonb_sha256(event["canonical_payload"])

        def change_reviewed_facts(evidence):
            decision = evidence["mapping_decision"]
            decision["reviewed_facts"] = {}
            decision["canonical_payload"]["reviewed_facts"] = {}

        def change_supplier_identity(evidence):
            for key in ("mapping_decision", "mapping_candidate"):
                record = evidence[key]
                record["supplier_code_value"] = "WRONG-SUPPLIER-SKU"
                record["canonical_payload"]["supplier_code_value"] = (
                    "WRONG-SUPPLIER-SKU"
                )

        def disable_frozen_vendor(evidence):
            terms = evidence["applicable_vendor_terms"]
            terms["vendor_rules"][1] = False
            terms["sha256"] = hashlib.sha256(
                json.dumps(
                    terms["vendor_rules"],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()

        for mutation in (
            change_prior_head,
            change_reviewed_facts,
            change_supplier_identity,
            disable_frozen_vendor,
        ):
            assert_resealed_manifest_refused(mutation)
        self.assertEqual(
            [item["offer_id"] for item in run["recommendations"]],
            [selected_offer_id],
        )
        recommendation = run["recommendations"][0]
        selected_evidence = recommendation["metrics"][
            "selected_offer_input_evidence"
        ]
        self.assertEqual(selected_evidence["contract"], SELECTED_CONTRACT)
        self.assertEqual(selected_evidence["authority"], "SYNTHETIC_TEST_ONLY")
        self.assertEqual(
            selected_evidence["selected_offer"]["offer_id"], selected_offer_id
        )
        self.assertEqual(
            selected_evidence["selected_offer"]["supplier_sku"], "SUP-001"
        )
        ladder = selected_evidence["applicable_price_ladder"]
        self.assertEqual(len(ladder["rows"]), 2)
        self.assertEqual(
            ladder["sha256"], recommendation["metrics"]["selected_price_ladder_sha256"]
        )
        source_break = next(row for row in ladder["rows"] if row[3] == "BREAK")
        self.assertEqual(
            source_break[1:],
            [
                selected_offer_id,
                "2026-09-01",
                "BREAK",
                "2.0000",
                "CS",
                "27.0000",
                "4.5000",
                "selected-offer-causal.csv",
                2,
            ],
        )
        self.assertEqual(
            recommendation["metrics"]["frozen_vendor_terms"]["supplier_sku"],
            "SUP-001",
        )
        self.assertEqual(str(recommendation["unit_cost"]), "5.2500")
        self.assertEqual(recommendation["recommended_cases"], 1)
        with mock.patch.dict(
            os.environ,
            {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
            clear=False,
        ):
            with psycopg.connect(self.fixture.mapping_url) as conn:
                preview = preview_recommendation_review(
                    conn,
                    recommendation_id=recommendation["recommendation_id"],
                    action="EDIT_QUANTITY",
                    actor="synthetic:matrix-owner:01",
                    expected_input_fingerprint=run["input_fingerprint"],
                    approved_cases=2,
                    approved_loose_units=0,
                    comment="exercise selected frozen break",
                )
                self.assertEqual(str(preview["approved_unit_cost"]), "4.5000")
                self.assertEqual(str(preview["approved_case_price"]), "27.0000")
                self.assertEqual(str(preview["approved_merchandise_total"]), "54.00")
                self.assertEqual(preview["final_price_tier"]["level_type"], "BREAK")
                self.assertEqual(
                    Decimal(preview["final_price_tier"]["break_qty"]), Decimal("2")
                )
                self.assertEqual(
                    preview["final_price_tier"],
                    {
                        "price_id": source_break[0],
                        "run_price_snapshot_id": preview["final_price_tier"][
                            "run_price_snapshot_id"
                        ],
                        "level_type": "BREAK",
                        "break_qty": "2.0000",
                        "break_unit": "CS",
                        "unit_price": "4.5000",
                        "case_price": "27.0000",
                        "price_ladder_sha256": ladder["sha256"],
                    },
                )
                snapshot = conn.execute(
                    f"""SELECT offer_id,effective_month,level_type,break_qty,
                               break_unit,case_price,unit_price,source_file,source_page
                          FROM {mapping_matrix.SCHEMA}.run_price_snapshots
                         WHERE run_price_snapshot_id=%s""",
                    (preview["final_price_tier"]["run_price_snapshot_id"],),
                ).fetchone()
                self.assertEqual(
                    snapshot,
                    (
                        selected_offer_id,
                        mapping_matrix.BUSINESS_DATE.replace(day=1),
                        "BREAK",
                        Decimal("2"),
                        "CS",
                        Decimal("27.0000"),
                        Decimal("4.5000"),
                        "selected-offer-causal.csv",
                        2,
                    ),
                )
                conn.rollback()
                confirmation_id = None
                if preview["materiality"]["materiality_tier"] == "MATERIAL":
                    confirmation = confirm_material_recommendation_edit(
                        conn,
                        recommendation_id=recommendation["recommendation_id"],
                        actor="synthetic:matrix-owner:01",
                        expected_input_fingerprint=run["input_fingerprint"],
                        expected_review_preview_fingerprint=preview["preview_fingerprint"],
                        approved_cases=2,
                        approved_loose_units=0,
                        comment="exercise selected frozen break",
                        confirmation_reason="confirm causal synthetic break",
                    )
                    confirmation_id = confirmation["material_edit_confirmation_id"]
                review = record_recommendation_review(
                    conn,
                    recommendation_id=recommendation["recommendation_id"],
                    action="EDIT_QUANTITY",
                    actor="synthetic:matrix-owner:01",
                    expected_input_fingerprint=run["input_fingerprint"],
                    approved_cases=2,
                    approved_loose_units=0,
                    comment="exercise selected frozen break",
                    expected_review_preview_fingerprint=preview["preview_fingerprint"],
                    material_edit_confirmation_id=confirmation_id,
                )
                self.assertEqual(review["final_price_tier"], preview["final_price_tier"])
                draft_preview = preview_vendor_drafts(
                    conn, run_id=run["run_id"], actor="synthetic:matrix-owner:01"
                )
                drafts = build_vendor_drafts(
                    conn,
                    run_id=run["run_id"],
                    actor="synthetic:matrix-owner:01",
                    expected_preview_fingerprint=draft_preview["preview_fingerprint"],
                    minimum_disposition=draft_preview["minimum_disposition"],
                )
                replayed_drafts = build_vendor_drafts(
                    conn,
                    run_id=run["run_id"],
                    actor="synthetic:matrix-owner:01",
                )
                self.assertTrue(replayed_drafts["idempotent_replay"])
        self.assertEqual(len(drafts["drafts"]), 1)
        draft = drafts["drafts"][0]
        self.assertEqual(str(draft["merchandise_total"]), "54.00")
        self.assertEqual(str(draft["delivery_fee"]), "5.00")
        self.assertEqual(str(draft["po_total"]), "59.00")
        self.assertEqual(draft["minimum_disposition"], "PAY_FEE")
        self.assertEqual(draft["lines"][0]["supplier_sku"], "SUP-001")
        self.assertEqual(
            draft["lines"][0]["final_price_tier"], preview["final_price_tier"]
        )
        with TemporaryDirectory() as temporary:
            storage = LocalFilesystemStorage(temporary)
            with mock.patch.dict(
                os.environ,
                {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
                clear=False,
            ):
                with psycopg.connect(self.fixture.mapping_url) as conn:
                    packet = build_emergency_review_packet(
                        conn,
                        storage=storage,
                        run_id=run["run_id"],
                        actor="synthetic:matrix-owner:01",
                    )
                    replay = build_emergency_review_packet(
                        conn,
                        storage=storage,
                        run_id=run["run_id"],
                        actor="synthetic:matrix-owner:01",
                    )
                self.assertTrue(replay["idempotent_replay"])
                self.assertEqual(replay["sha256"], packet["sha256"])
            with psycopg.connect(self.fixture.mapping_url) as conn:
                with self.assertRaisesRegex(
                    EmergencyPacketError,
                    "SYNTHETIC_SELECTED_OFFER_INPUTS_NOT_AUTHORIZED",
                ):
                    build_emergency_review_packet(
                        conn,
                        storage=storage,
                        run_id=run["run_id"],
                        actor="synthetic:matrix-owner:01",
                    )
            with zipfile.ZipFile(io.BytesIO(storage.get_bytes(packet["storage_key"]))) as archive:
                mapping_evidence = json.loads(
                    archive.read("supplier-mapping-evidence.json")
                )["items"][0]
        self.assertEqual(mapping_evidence["offer_id"], selected_offer_id)
        self.assertEqual(mapping_evidence["supplier_sku"], "SUP-001")
        self.assertEqual(mapping_evidence["final_price_tier"], preview["final_price_tier"])
        self.assertEqual(
            mapping_evidence["selected_offer_input_evidence_sha256"],
            recommendation["metrics"]["selected_offer_input_evidence_sha256"],
        )

    def test_selected_mode_missing_head_never_uses_legacy_offer(self):
        selected_offer_id, _price_id = self.fixture._reset_with_grandfathered_current_price(
            sku="USABLE-LEGACY"
        )
        evaluation_at = self._seed_monday_evidence()
        with psycopg.connect(self.fixture.mapping_url) as conn:
            self.assertEqual(
                conn.execute(
                    f"SELECT array_agg(offer_id ORDER BY offer_id) "
                    f"FROM {mapping_matrix.SCHEMA}.supplier_offers "
                    "WHERE variant_id='1001' AND active AND package_type='STANDARD'"
                ).fetchone()[0],
                [selected_offer_id],
            )
        with mock.patch.dict(
            os.environ,
            {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
            clear=False,
        ), mock.patch.object(
            recommendations, "_database_evaluation_at", return_value=evaluation_at
        ):
            with psycopg.connect(self.fixture.mapping_url) as conn:
                run = recommendations.prepare_monday_run(
                    conn,
                    business_date=mapping_matrix.BUSINESS_DATE,
                    idempotency_key="selected-offer-missing-head",
                    variant_ids=("1001",),
                    actor="synthetic:matrix-owner:01",
                )
                counts = conn.execute(
                    f"""SELECT
                         (SELECT count(*) FROM {mapping_matrix.SCHEMA}.inventory_snapshots
                           WHERE run_id=%s),
                         (SELECT count(*) FROM {mapping_matrix.SCHEMA}.forecast_results
                           WHERE run_id=%s),
                         (SELECT count(*) FROM {mapping_matrix.SCHEMA}.run_price_snapshots
                           WHERE run_id=%s),
                         (SELECT count(*) FROM {mapping_matrix.SCHEMA}.review_decisions
                           WHERE run_id=%s),
                         (SELECT count(*) FROM {mapping_matrix.SCHEMA}.purchase_orders
                           WHERE run_id=%s),
                         (SELECT count(*) FROM {mapping_matrix.SCHEMA}.monday_run_artifacts
                           WHERE run_id=%s)""",
                    (run["run_id"],) * 6,
                ).fetchone()
        self.assertEqual(run["recommendations"], [])
        self.assertEqual(
            [item["message"] for item in run["blockers"]],
            ["ROUTINE_SELECTED_OFFER_HEAD_REQUIRED"],
        )
        self.assertEqual(counts, (0, 0, 0, 0, 0, 0))

    def test_selected_prepare_late_failure_rolls_back_every_effect(self):
        selected_offer_id, _alternative_offer_id = (
            self._reset_with_two_priced_active_offers()
        )
        evaluation_at = self._seed_monday_evidence()
        candidate_id = self.fixture._intake(self.fixture._packet(1))
        decision = self.fixture._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="late rollback selected-offer fixture",
            offer_id=selected_offer_id,
            link_kind="LINKED_EXISTING",
        )
        self.fixture._select(decision, reason="late rollback selected-offer selection")
        with psycopg.connect(self.fixture.mapping_url) as observer:
            before_head = observer.execute(
                f'''SELECT "{mapping_matrix.SCHEMA}".persistent_mapping_json_sha256(
                               to_jsonb(h))
                      FROM {mapping_matrix.SCHEMA}.supplier_offer_selection_heads h
                     WHERE variant_id='1001'
                       AND selection_scope='ROUTINE_PROCUREMENT_STANDARD' '''
            ).fetchone()[0]
        with mock.patch.dict(
            os.environ,
            {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
            clear=False,
        ), mock.patch.object(
            recommendations, "_database_evaluation_at", return_value=evaluation_at
        ):
            with psycopg.connect(self.fixture.mapping_url) as conn:
                with self.assertRaisesRegex(
                    recommendations.MondayRecommendationError,
                    "injected selected-offer preparation failure before commit",
                ):
                    recommendations.prepare_monday_run(
                        conn,
                        business_date=mapping_matrix.BUSINESS_DATE,
                        idempotency_key="selected-offer-late-rollback",
                        variant_ids=("1001",),
                        actor="synthetic:matrix-owner:01",
                        _inject_failure_after_persistence=True,
                    )
        with psycopg.connect(self.fixture.mapping_url) as observer:
            after = observer.execute(
                f'''SELECT
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.runs
                         WHERE idempotency_key='selected-offer-late-rollback'),
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.inventory_snapshots i
                         JOIN {mapping_matrix.SCHEMA}.runs r USING(run_id)
                        WHERE r.idempotency_key='selected-offer-late-rollback'),
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.forecast_results f
                         JOIN {mapping_matrix.SCHEMA}.runs r USING(run_id)
                        WHERE r.idempotency_key='selected-offer-late-rollback'),
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.run_price_snapshots p
                         JOIN {mapping_matrix.SCHEMA}.runs r USING(run_id)
                        WHERE r.idempotency_key='selected-offer-late-rollback'),
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.procurement_recommendations p
                         JOIN {mapping_matrix.SCHEMA}.runs r USING(run_id)
                        WHERE r.idempotency_key='selected-offer-late-rollback'),
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.review_decisions d
                         JOIN {mapping_matrix.SCHEMA}.runs r USING(run_id)
                        WHERE r.idempotency_key='selected-offer-late-rollback'),
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.purchase_orders p
                         JOIN {mapping_matrix.SCHEMA}.runs r USING(run_id)
                        WHERE r.idempotency_key='selected-offer-late-rollback'),
                       (SELECT count(*) FROM {mapping_matrix.SCHEMA}.monday_run_artifacts a
                         JOIN {mapping_matrix.SCHEMA}.runs r USING(run_id)
                        WHERE r.idempotency_key='selected-offer-late-rollback'),
                       (SELECT "{mapping_matrix.SCHEMA}".persistent_mapping_json_sha256(
                                   to_jsonb(h))
                          FROM {mapping_matrix.SCHEMA}.supplier_offer_selection_heads h
                         WHERE variant_id='1001'
                           AND selection_scope='ROUTINE_PROCUREMENT_STANDARD')'''
            ).fetchone()
        self.assertEqual(after[:8], (0,) * 8)
        self.assertEqual(after[8], before_head)

    def test_late_selection_head_change_blocks_review_without_side_effects(self):
        selected_offer_id, _alternative_offer_id = (
            self._reset_with_two_priced_active_offers()
        )
        evaluation_at = self._seed_monday_evidence()
        candidate_id = self.fixture._intake(self.fixture._packet(1))
        decision = self.fixture._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="late-head-change fixture",
            offer_id=selected_offer_id,
            link_kind="LINKED_EXISTING",
        )
        self.fixture._select(decision, reason="late-head-change selection")
        with mock.patch.dict(
            os.environ,
            {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
            clear=False,
        ), mock.patch.object(
            recommendations, "_database_evaluation_at", return_value=evaluation_at
        ):
            with psycopg.connect(self.fixture.mapping_url) as conn:
                run = recommendations.prepare_monday_run(
                    conn,
                    business_date=mapping_matrix.BUSINESS_DATE,
                    idempotency_key="selected-offer-late-head-change",
                    variant_ids=("1001",),
                    actor="synthetic:matrix-owner:01",
                )
        clear_key = uuid4()
        with psycopg.connect(self.fixture.mapping_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            clear_preview = preview_routine_offer_clear(
                conn,
                variant_id="1001",
                principal=self.fixture.selection_principal,
                selection_idempotency_key=clear_key,
                reason="prove late selected-head revalidation",
                effective_from=mapping_matrix.BUSINESS_DATE,
            )
        execute_routine_offer_clear(
            self.fixture.mapping_url,
            variant_id="1001",
            principal=self.fixture.selection_principal,
            selection_idempotency_key=clear_key,
            reason="prove late selected-head revalidation",
            effective_from=mapping_matrix.BUSINESS_DATE,
            expected_preview_sha256=clear_preview["preview_sha256"],
        )
        recommendation_id = run["recommendations"][0]["recommendation_id"]
        with mock.patch.dict(
            os.environ,
            {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
            clear=False,
        ):
            with psycopg.connect(self.fixture.mapping_url) as conn:
                with self.assertRaisesRegex(
                    ProcurementReviewError,
                    "material recommendation inputs changed",
                ):
                    preview_recommendation_review(
                        conn,
                        recommendation_id=recommendation_id,
                        action="ACCEPT",
                        actor="synthetic:matrix-owner:01",
                        expected_input_fingerprint=run["input_fingerprint"],
                        comment="must refuse changed selection",
                    )
                state = conn.execute(
                    f"""SELECT
                           (SELECT count(*) FROM {mapping_matrix.SCHEMA}.review_decisions
                             WHERE run_id=%s),
                           (SELECT count(*) FROM {mapping_matrix.SCHEMA}.purchase_orders
                             WHERE run_id=%s),
                           (SELECT count(*) FROM {mapping_matrix.SCHEMA}.monday_run_artifacts
                             WHERE run_id=%s),
                           (SELECT workflow_stage FROM {mapping_matrix.SCHEMA}.runs
                             WHERE run_id=%s)""",
                    (run["run_id"],) * 4,
                ).fetchone()
        self.assertEqual(state, (0, 0, 0, "AWAITING_REVIEW"))

    def test_held_selection_lock_returns_typed_refusal_and_no_run(self):
        selected_offer_id, _alternative_offer_id = (
            self._reset_with_two_priced_active_offers()
        )
        evaluation_at = self._seed_monday_evidence()
        candidate_id = self.fixture._intake(self.fixture._packet(1))
        decision = self.fixture._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="held-lock fixture",
            offer_id=selected_offer_id,
            link_kind="LINKED_EXISTING",
        )
        self.fixture._select(decision, reason="held-lock selection")
        holder = psycopg.connect(self.fixture.mapping_url)
        try:
            holder.execute(
                "SELECT pg_catalog.pg_advisory_lock("
                "pg_catalog.hashtextextended(%s,0))",
                (SELECTION_LOCK_PREFIX + "1001",),
            )
            holder.rollback()
            with mock.patch.dict(
                os.environ,
                {"BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1"},
                clear=False,
            ), mock.patch.object(
                recommendations,
                "_database_evaluation_at",
                return_value=evaluation_at,
            ):
                with psycopg.connect(self.fixture.mapping_url) as conn:
                    with self.assertRaisesRegex(
                        recommendations.MondayRecommendationError,
                        "^MONDAY_PREPARATION_RETRY_REQUIRED$",
                    ):
                        recommendations.prepare_monday_run(
                            conn,
                            business_date=mapping_matrix.BUSINESS_DATE,
                            idempotency_key="selected-offer-held-lock",
                            variant_ids=("1001",),
                            actor="synthetic:matrix-owner:01",
                        )
            with psycopg.connect(self.fixture.mapping_url) as observer:
                self.assertEqual(
                    observer.execute(
                        f"SELECT count(*) FROM {mapping_matrix.SCHEMA}.runs "
                        "WHERE idempotency_key='selected-offer-held-lock'"
                    ).fetchone()[0],
                    0,
                )
        finally:
            holder.close()


class SelectedOfferPureContractTests(unittest.TestCase):
    def test_manifest_classifier_and_absolute_deadline_fail_closed(self):
        evaluation_at = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
        blocker = "ROUTINE_SELECTED_OFFER_HEAD_REQUIRED"
        evidence = selected_blocker_evidence(
            blocker=blocker,
            variant_id="1001",
            business_date=mapping_matrix.BUSINESS_DATE,
            evaluation_at=evaluation_at,
        )
        evidence["resolution_blockers"] = [blocker]
        context = {
            "variant_id": "1001",
            "blockers": [blocker],
            "selected_offer_input_evidence": evidence,
            "selected_offer_input_evidence_sha256": hashlib.sha256(
                json.dumps(
                    evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
        }
        manifest = {
            "business_date": str(mapping_matrix.BUSINESS_DATE),
            "evaluation_at": str(evaluation_at),
            "variant_ids": ["1001"],
            "contexts": [context],
            "offer_resolution_contract": SELECTED_CONTRACT,
        }
        raw = json.dumps(manifest, sort_keys=True, default=str)
        self.assertEqual(
            classify_frozen_manifest(raw),
            (SELECTED_CONTRACT, ("1001",)),
        )
        malformed = json.loads(raw)
        malformed["evaluation_at"] = "not-a-timestamp"
        with self.assertRaisesRegex(
            SyntheticSelectedOfferError,
            "^SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID$",
        ):
            classify_frozen_manifest(json.dumps(malformed, sort_keys=True))
        partial = json.loads(raw)
        del partial["contexts"][0]["selected_offer_input_evidence"]
        with self.assertRaisesRegex(
            SyntheticSelectedOfferError,
            "^SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID$",
        ):
            classify_frozen_manifest(json.dumps(partial, sort_keys=True))
        placeholder = json.loads(raw)
        placeholder["contexts"][0]["blockers"] = []
        placeholder_evidence = placeholder["contexts"][0][
            "selected_offer_input_evidence"
        ]
        placeholder_evidence.pop("resolution_blockers")
        placeholder_evidence.pop("blocker")
        placeholder_evidence["recommendation_effect"] = (
            "SELECTED_OFFER_INPUT_AUTHORITY"
        )
        placeholder_evidence.update(
            {
                "selection_head": {},
                "selection_event": {},
                "mapping_decision": {},
                "mapping_candidate": {},
                "review_batch": {},
                "selected_offer": {},
                "current_fingerprints": {},
                "applicable_vendor_terms": {},
                "applicable_price_ladder": {},
                "initial_applicable_price_tier": {},
                "legacy_active_standard_offer_comparison": {},
                "commercial_source_authority": "NOT_APPROVED",
                "source_import_state": "NOT_IMPORT_READY",
            }
        )
        placeholder["contexts"][0][
            "selected_offer_input_evidence_sha256"
        ] = hashlib.sha256(
            json.dumps(
                placeholder_evidence,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(
            SyntheticSelectedOfferError,
            "^SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID$",
        ):
            classify_frozen_manifest(json.dumps(placeholder, sort_keys=True))

        raw_connection = mock.Mock()
        deadline_connection = recommendations._DeadlineConnection(
            raw_connection, 10.0
        )
        with mock.patch.object(
            recommendations.monotonic_time,
            "monotonic",
            side_effect=(1.0, 4.0, 10.1),
        ):
            deadline_connection.execute("SELECT first")
            deadline_connection.execute("SELECT second")
            with self.assertRaisesRegex(
                recommendations.MondayRecommendationError,
                "^MONDAY_PREPARATION_RETRY_REQUIRED$",
            ):
                deadline_connection.execute("SELECT expired")
        statements = [call.args[0] for call in raw_connection.execute.call_args_list]
        self.assertEqual(
            statements,
            [
                "SET LOCAL statement_timeout = '9000ms'",
                "SET LOCAL lock_timeout = '5000ms'",
                "SELECT first",
                "SET LOCAL statement_timeout = '6000ms'",
                "SET LOCAL lock_timeout = '5000ms'",
                "SELECT second",
            ],
        )


if __name__ == "__main__":
    unittest.main()
