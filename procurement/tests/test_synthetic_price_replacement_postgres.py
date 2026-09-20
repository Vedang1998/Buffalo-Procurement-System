"""Focused PostgreSQL acceptance for the synthetic price-to-DRAFT connection."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import io
import json
import os
from pathlib import Path
import time
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import uuid
import zipfile

import psycopg

import initialize_synthetic_demo as initializer
import test_persistent_mapping_foundation_postgres as mapping_matrix
from procurement_os import recommendations
from procurement_os.draft_po import build_vendor_drafts, preview_vendor_drafts
from procurement_os.development_forecast import (
    CONTRACT as DEVELOPMENT_FORECAST_CONTRACT,
    V2_CONTRACT as DEVELOPMENT_FORECAST_V2_CONTRACT,
    V2_METHOD_VERSION as DEVELOPMENT_FORECAST_V2_METHOD_VERSION,
    development_forecast_v2_registration,
    validate_development_baseline_need_context,
    validate_development_forecast_evidence,
)
from procurement_os.emergency_packet import build_emergency_review_packet
from procurement_os.local_backup_v2 import (
    VerifiedPriceApplyBackup,
    database_state_evidence,
)
from procurement_os.procurement_review import (
    _REVIEW_CONTEXT_SQL,
    ProcurementReviewError,
    confirm_material_recommendation_edit,
    preview_recommendation_review,
    record_recommendation_review,
)
from procurement_os.storage import LocalFilesystemStorage
from procurement_os.synthetic_price_replacement import (
    SyntheticPriceReplacementError,
    _run_price_apply_with_retry,
    apply_price_replacement,
    confirm_declared_price_book,
    preview_declared_price_confirmation,
    preview_price_replacement,
    registered_target_declaration,
    stage_and_validate_declared_price_book,
)
from procurement_os.synthetic_price_replacement_contract import (
    CATALOG_SHA256,
    MIGRATION_SHA256,
)
from procurement_os.synthetic_selected_offer import (
    SyntheticSelectedOfferError,
    classify_frozen_manifest,
    final_price_tier_matches_snapshot,
)


BUSINESS_DATE = date(2026, 10, 5)
BOOK_PATH = Path(__file__).resolve().parents[1] / "config" / "synthetic_price_replacement_book.csv"
BOOK_SHA256 = "00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c"
WARNING_REASON = "Reviewed four fabricated synthetic price changes."


class _ProjectedSingleRowCursor:
    """Return one deliberately altered projection while delegating cursor facts."""

    def __init__(self, cursor, row) -> None:
        self._cursor = cursor
        self._row = row
        self._returned = False

    def fetchone(self):
        if self._returned:
            return None
        self._returned = True
        return self._row

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _ReviewLineageProjectionConnection:
    """Inject one read-boundary lineage fault without changing stored evidence."""

    _PRICE_QUERY_PREFIX = (
        "SELECT run_price_snapshot_id,offer_id,effective_month,level_type,"
    )

    def __init__(
        self,
        connection,
        *,
        omit_snapshot_lineage: bool = False,
        omit_authority_envelope: bool = False,
    ) -> None:
        self._connection = connection
        self.omit_snapshot_lineage = omit_snapshot_lineage
        self.omit_authority_envelope = omit_authority_envelope
        self.snapshot_projection_hits = 0
        self.context_projection_hits = 0

    def execute(self, statement, parameters=None, **kwargs):
        cursor = self._connection.execute(statement, parameters, **kwargs)
        query = str(statement).lstrip()
        if self.omit_snapshot_lineage and query.startswith(self._PRICE_QUERY_PREFIX):
            row = cursor.fetchone()
            self.snapshot_projection_hits += 1
            projected = tuple(row[:10]) + (None, None, None, None)
            return _ProjectedSingleRowCursor(cursor, projected)
        if self.omit_authority_envelope and query.startswith(
            _REVIEW_CONTEXT_SQL.lstrip()
        ):
            row = cursor.fetchone()
            self.context_projection_hits += 1
            projected = list(row)
            metrics = json.loads(json.dumps(projected[13]))
            del metrics["selected_offer_input_evidence"][
                "applicable_price_authority"
            ]
            projected[13] = metrics
            return _ProjectedSingleRowCursor(cursor, tuple(projected))
        return cursor

    def __getattr__(self, name):
        return getattr(self._connection, name)


class SyntheticPriceReplacementPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        mapping_matrix.PersistentMappingFoundationPostgresTests.setUpClass()
        cls.admin_url = mapping_matrix.PersistentMappingFoundationPostgresTests.admin_url
        cls.mapping_url = mapping_matrix.PersistentMappingFoundationPostgresTests.mapping_url
        cls.database = mapping_matrix.PersistentMappingFoundationPostgresTests.database
        cls.test_url = cls.mapping_url

    def setUp(self) -> None:
        self.environment = mock.patch.dict(
            os.environ,
            {
                "BUFFALO_RUNTIME_MODE": "AUTOMATED_TEST",
                "BUFFALO_ENABLE_SYNTHETIC_MAPPING_DEMO": "1",
                "BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "1",
                "BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS": "1",
                "BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST": "1",
                "TEST_DATABASE_URL": self.test_url,
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.fixture = mapping_matrix.PersistentMappingFoundationPostgresTests(
            "test_legacy_recommendations_are_identical_until_cutover"
        )
        self.fixture.admin_url = self.admin_url
        self.fixture.mapping_url = self.mapping_url
        self.fixture.database = self.database
        self.fixture._prepare_roles_and_schema()
        self.fixture.principal = self.fixture._principal("procurement.mapping.approve")
        self.fixture.selection_principal = self.fixture._principal(
            "procurement.offer.select", session="price-selection"
        )
        self.price_principal = self.fixture._principal(
            "procurement.price.approve", session="price-confirmation"
        )
        self._install_fixture()
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.storage = LocalFilesystemStorage(self.temporary.name)
        self.book = BOOK_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(self.book).hexdigest(), BOOK_SHA256)

    def tearDown(self) -> None:
        self.fixture._admin_cleanup()

    def _install_fixture(self) -> None:
        cutoff = tuple(mapping_matrix.apply_schema.LEGACY_MIGRATION_SHA256).index(
            "011_monday_price_book_staging.sql"
        )
        with psycopg.connect(self.mapping_url, autocommit=True) as conn:
            schema_oid = int(
                conn.execute(
                    "SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (mapping_matrix.SCHEMA,),
                ).fetchone()[0]
            )
            for name in tuple(mapping_matrix.apply_schema.LEGACY_MIGRATION_SHA256)[:cutoff]:
                initializer._apply_legacy(conn, name, schema_oid=schema_oid)
            initializer._seed_pre_price(conn, BUSINESS_DATE)
            initializer._seed_multivendor_pre_price(conn, BUSINESS_DATE)
            for name in tuple(mapping_matrix.apply_schema.LEGACY_MIGRATION_SHA256)[cutoff:]:
                initializer._apply_legacy(conn, name, schema_oid=schema_oid)
            with conn.transaction():
                self.assertTrue(
                    mapping_matrix.apply_schema._verify_or_apply_mapping_release(
                        conn, mapping_matrix.DB_DIR
                    )
                )
            sales_id = initializer._seed_evidence(
                conn,
                BUSINESS_DATE,
                include_multivendor=True,
            )
            self._install_synthetic_test_sales_contract(conn)
            with conn.transaction():
                self.assertTrue(
                    mapping_matrix.apply_schema._verify_or_apply_post_mapping_release(
                        conn, mapping_matrix.DB_DIR
                    )
                )
            with conn.transaction():
                self.assertTrue(
                    mapping_matrix.apply_schema.verify_or_apply_synthetic_price_replacement(
                        conn,
                        mapping_matrix.DB_DIR,
                        enable_fixture_registration=True,
                    )
                )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES "
                "('synthetic_owner_demo_contract','BUFFALO_SYNTHETIC_OWNER_DEMO_V1'),"
                "('synthetic_owner_demo_business_date',%s),"
                "('synthetic_owner_demo_sales_backfill_id',%s),"
                "('synthetic_multivendor_acceptance_contract',"
                " 'BUFFALO_SYNTHETIC_MULTIVENDOR_ACCEPTANCE_V2')",
                (BUSINESS_DATE.isoformat(), sales_id),
            )

    def _install_synthetic_test_sales_contract(self, conn) -> None:
        conn.execute(
            "UPDATE sales_daily SET source='SYNTHETIC_TEST' "
            "WHERE source='SHOPIFYQL_SALES'"
        )
        history_start = BUSINESS_DATE - timedelta(days=84)
        history_end = BUSINESS_DATE - timedelta(days=1)
        coverage = {}
        covered_variant_ids = (
            initializer.VARIANT_ID,
            initializer.CONTROL_VARIANT_ID,
            *initializer.WESTERN_VARIANT_IDS,
        )
        for variant_id in covered_variant_ids:
            rows = conn.execute(
                """SELECT sale_date,units_sold,net_sales,distinct_orders,source,
                          run_id::text
                     FROM sales_daily
                    WHERE variant_id=%s AND source='SYNTHETIC_TEST'
                      AND sale_date BETWEEN %s AND %s
                    ORDER BY sale_date,source""",
                (variant_id, history_start, history_end),
            ).fetchall()
            self.assertEqual(len(rows), 84)
            coverage[str(variant_id)] = {
                "row_count": len(rows),
                "sha256": recommendations._sales_coverage_digest(rows),
            }
        evidence = {
            "coverage_contract":
                "DISPOSABLE_SYNTHETIC_DAILY_VARIANT_COVERAGE_V1",
            "source": "SYNTHETIC_TEST",
            "sales_rows": 84 * len(covered_variant_ids),
            "variant_count": len(covered_variant_ids),
            "history_start": history_start.isoformat(),
            "history_end": history_end.isoformat(),
            "variant_coverage": coverage,
        }
        updated = conn.execute(
            """UPDATE readiness_gates
                  SET status='PASS',severity='CRITICAL',blocks_po=TRUE,
                      message='Disposable fixture has exact synthetic sales coverage.',
                      evidence_json=%s::jsonb,checked_at=transaction_timestamp()
                WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL'
                  AND scope_id=''
                RETURNING status""",
            (json.dumps(evidence, sort_keys=True),),
        ).fetchall()
        self.assertEqual(updated, [("PASS",)])

    def _activate_v2_forecast_fixture(self, conn) -> None:
        initializer._configure_development_forecast_vendor_calendars(
            conn,
            BUSINESS_DATE,
            profile=initializer.DEVELOPMENT_FORECAST_V2_PROFILE,
        )
        variant_ids = (
            initializer.VARIANT_ID,
            initializer.CONTROL_VARIANT_ID,
            *initializer.WESTERN_VARIANT_IDS,
        )
        history_start = BUSINESS_DATE - timedelta(days=138)
        history_end = BUSINESS_DATE - timedelta(days=1)
        with conn.transaction():
            conn.execute("DELETE FROM sales_daily WHERE source='SYNTHETIC_TEST'")
            for offset in range(138):
                sale_date = history_start + timedelta(days=offset)
                for variant_id in variant_ids:
                    if variant_id in {"4001", "4002", "4004"}:
                        units = Decimal("2") if offset % 3 in {0, 1} else Decimal("0")
                    else:
                        units = Decimal("2")
                    conn.execute(
                        """INSERT INTO sales_daily(
                                   sale_date,variant_id,units_sold,net_sales,source)
                            VALUES (%s,%s,%s,%s,'SYNTHETIC_TEST')""",
                        (sale_date, variant_id, units, units * Decimal("4.99")),
                    )
            coverage = {}
            for variant_id in variant_ids:
                rows = conn.execute(
                    """SELECT sale_date,units_sold,net_sales,distinct_orders,source,
                              run_id::text
                         FROM sales_daily
                        WHERE variant_id=%s AND source='SYNTHETIC_TEST'
                          AND sale_date BETWEEN %s AND %s
                        ORDER BY sale_date,source""",
                    (variant_id, history_start, history_end),
                ).fetchall()
                self.assertEqual(len(rows), 138)
                coverage[str(variant_id)] = {
                    "row_count": 138,
                    "sha256": recommendations._sales_coverage_digest(rows),
                }
            evidence = {
                "coverage_contract": "DISPOSABLE_SYNTHETIC_DAILY_VARIANT_COVERAGE_V1",
                "source": "SYNTHETIC_TEST",
                "sales_rows": 138 * len(variant_ids),
                "variant_count": len(variant_ids),
                "history_start": history_start.isoformat(),
                "history_end": history_end.isoformat(),
                "variant_coverage": coverage,
            }
            conn.execute(
                """UPDATE readiness_gates
                      SET evidence_json=%s::jsonb,checked_at=transaction_timestamp()
                    WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL'
                      AND scope_id=''""",
                (json.dumps(evidence, sort_keys=True),),
            )
            registration = development_forecast_v2_registration()
            conn.execute(
                """INSERT INTO meta(key,value) VALUES (%s,%s),(%s,%s),(%s,%s)""",
                (
                    initializer.DEVELOPMENT_FORECAST_META_KEY,
                    initializer.DEVELOPMENT_FORECAST_V2_DEMO_CONTRACT,
                    initializer.DEVELOPMENT_FORECAST_PROFILE_META_KEY,
                    initializer.DEVELOPMENT_FORECAST_V2_PROFILE,
                    initializer.DEVELOPMENT_FORECAST_REGISTRATION_META_KEY,
                    json.dumps(registration, sort_keys=True, separators=(",", ":")),
                ),
            )

    def _connection(self):
        return psycopg.connect(self.mapping_url)

    def _select_fixture_offer(self) -> int:
        with self._connection() as conn:
            offer_id = int(
                conn.execute(
                    "SELECT offer_id FROM supplier_offers WHERE supplier_sku='SUP-001'"
                ).fetchone()[0]
            )
        candidate_id = self.fixture._intake(self.fixture._packet(1))
        decision = self.fixture._decide(
            candidate_id,
            action="APPROVE_MAPPING",
            reason="focused uploaded-price connection",
            offer_id=offer_id,
            link_kind="LINKED_EXISTING",
        )
        self.fixture._select(decision, reason="focused uploaded-price selection")
        return offer_id

    def _stage_and_confirm(self) -> tuple[str, dict, dict]:
        with self._connection() as conn:
            declaration = registered_target_declaration(conn)
            conn.commit()
            staged = stage_and_validate_declared_price_book(
                conn,
                self.storage,
                csv_bytes=self.book,
                principal=self.price_principal,
                expected_declaration_sha256=declaration["declaration_sha256"],
            )
            preview = preview_declared_price_confirmation(
                conn,
                self.storage,
                batch_id=staged["price_book_batch_id"],
                confirmation_idempotency_key="focused-price-confirm-v1",
                warning_review_reason=WARNING_REASON,
                principal=self.price_principal,
            )
            confirmed = confirm_declared_price_book(
                conn,
                self.storage,
                batch_id=staged["price_book_batch_id"],
                confirmation_idempotency_key="focused-price-confirm-v1",
                expected_preview_sha256=preview["preview_sha256"],
                confirm="CONFIRM",
                warning_review_reason=WARNING_REASON,
                principal=self.price_principal,
            )
        return staged["price_book_batch_id"], preview, confirmed

    def _backup(self, batch_id: str) -> VerifiedPriceApplyBackup:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT b.vendor_id::text,b.price_scope_key,b.content_sha256,
                          b.raw_storage_key,h.supplier_price_authority_event_id::text,
                          h.head_version,h.current_scope_sha256
                     FROM price_book_batches b
                     JOIN supplier_price_authority_heads h
                       ON h.vendor_id=b.vendor_id AND h.price_scope_key=b.price_scope_key
                    WHERE b.price_book_batch_id=%s""",
                (batch_id,),
            ).fetchone()
            state = database_state_evidence(conn, schema=mapping_matrix.SCHEMA)
        return VerifiedPriceApplyBackup(
            manifest_ref="/owned/test/manifest.json",
            manifest_sha256="1" * 64,
            dump_sha256="2" * 64,
            storage_sha256="3" * 64,
            prechange_scope_sha256=str(row[6]),
            database=self.database,
            batch_id=str(batch_id),
            vendor_id=str(row[0]),
            price_scope_key=str(row[1]),
            prior_event_id=str(row[4]),
            prior_head_version=int(row[5]),
            raw_content_sha256=str(row[2]),
            raw_storage_key=str(row[3]),
            migration_sha256=MIGRATION_SHA256,
            catalog_sha256=CATALOG_SHA256,
            state=state,
        )

    def _apply(self, batch_id: str, *, key: str = "focused-price-apply-v1") -> tuple[dict, dict]:
        backup = self._backup(batch_id)
        with mock.patch(
            "procurement_os.synthetic_price_replacement.verify_bound_price_apply_backup",
            return_value=backup,
        ):
            with self._connection() as conn:
                preview = preview_price_replacement(
                    conn,
                    self.storage,
                    batch_id=batch_id,
                    apply_idempotency_key=key,
                    principal=self.price_principal,
                )
            with self._connection() as conn:
                result = apply_price_replacement(
                    conn,
                    self.storage,
                    batch_id=batch_id,
                    apply_idempotency_key=key,
                    expected_preview_sha256=preview["preview_sha256"],
                    confirm="CONFIRM",
                    principal=self.price_principal,
                )
        return preview, result

    def _prepare_applied_selected_run(self, *, key: str) -> tuple[dict, dict]:
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        self._apply(batch_id, key=f"{key}-apply")
        self._select_fixture_offer()
        with self._connection() as conn:
            run = recommendations.prepare_monday_run(
                conn,
                business_date=BUSINESS_DATE,
                idempotency_key=key,
                variant_ids=("1001",),
                actor="synthetic:matrix-owner:01",
            )
        self.assertEqual(run["blockers"], [])
        self.assertEqual(len(run["recommendations"]), 1)
        return run, run["recommendations"][0]

    def test_declared_stage_confirmation_preserves_current_and_uses_observation_clock(self):
        lines = self.book.decode("utf-8").splitlines()
        incomplete = (
            "\n".join(
                [lines[0]]
                + [line.replace("synthetic-october-replacement-v1", "incomplete-v1") for line in lines[1:4]]
            )
            + "\n"
        ).encode("utf-8")
        with self._connection() as conn:
            declaration = registered_target_declaration(conn)
            conn.commit()
            bad = stage_and_validate_declared_price_book(
                conn,
                self.storage,
                csv_bytes=incomplete,
                principal=self.price_principal,
                expected_declaration_sha256=declaration["declaration_sha256"],
            )
            self.assertEqual((bad["status"], bad["error_count"]), ("INVALID", 1))
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError,
                "only a complete declared VALIDATED batch can be confirmed",
            ):
                preview_declared_price_confirmation(
                    conn,
                    self.storage,
                    batch_id=bad["price_book_batch_id"],
                    confirmation_idempotency_key="bad-confirm",
                    warning_review_reason=None,
                    principal=self.price_principal,
                )
        batch_id, _preview, confirmed = self._stage_and_confirm()
        self.assertEqual(confirmed["status"], "VERIFIED_FUTURE")
        with self._connection() as conn:
            row = conn.execute(
                """SELECT e.recorded_at,
                          count(*) FILTER (WHERE p.price_state='current'),
                          count(*) FILTER (WHERE p.price_state='future')
                     FROM price_book_promotion_events e
                     JOIN price_book_batches b USING(price_book_batch_id)
                     JOIN supplier_offers o ON o.vendor_id=b.vendor_id
                     JOIN prices p USING(offer_id)
                    WHERE b.price_book_batch_id=%s GROUP BY e.recorded_at""",
                (batch_id,),
            ).fetchone()
        self.assertEqual(row[0].isoformat(), "2026-09-16T14:00:00+00:00")
        self.assertEqual(row[1:], (4, 4))

    def test_apply_late_failure_rolls_back_then_replays_without_control_vendor_change(self):
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        backup = self._backup(batch_id)
        key = "focused-rollback-apply-v1"
        with mock.patch(
            "procurement_os.synthetic_price_replacement.verify_bound_price_apply_backup",
            return_value=backup,
        ):
            with self._connection() as conn:
                preview = preview_price_replacement(
                    conn,
                    self.storage,
                    batch_id=batch_id,
                    apply_idempotency_key=key,
                    principal=self.price_principal,
                )
            with self.assertRaisesRegex(
                SyntheticPriceReplacementError, "INJECTED_PRICE_APPLY_FAILURE"
            ):
                with self._connection() as conn:
                    apply_price_replacement(
                        conn,
                        self.storage,
                        batch_id=batch_id,
                        apply_idempotency_key=key,
                        expected_preview_sha256=preview["preview_sha256"],
                        confirm="CONFIRM",
                        principal=self.price_principal,
                        _inject_failure_after_mutation=True,
                    )
            with self._connection() as observer:
                rolled_back = observer.execute(
                    """SELECT b.status,h.head_version,h.active_price_book_batch_id,
                              (SELECT count(*) FROM supplier_price_authority_events
                                WHERE apply_idempotency_key=%s),
                              supplier_price_unaffected_state_sha256(b.vendor_id)
                         FROM price_book_batches b
                         JOIN supplier_price_authority_heads h
                           ON h.vendor_id=b.vendor_id AND h.price_scope_key=b.price_scope_key
                        WHERE b.price_book_batch_id=%s""",
                    (key, batch_id),
                ).fetchone()
            self.assertEqual(rolled_back[:4], ("VERIFIED_FUTURE", 1, None, 0))
            self.assertEqual(rolled_back[4], preview["unaffected_price_state_sha256"])
            with self._connection() as conn:
                applied = apply_price_replacement(
                    conn,
                    self.storage,
                    batch_id=batch_id,
                    apply_idempotency_key=key,
                    expected_preview_sha256=preview["preview_sha256"],
                    confirm="CONFIRM",
                    principal=self.price_principal,
                )
            with self._connection() as conn:
                replay = apply_price_replacement(
                    conn,
                    self.storage,
                    batch_id=batch_id,
                    apply_idempotency_key=key,
                    expected_preview_sha256=preview["preview_sha256"],
                    confirm="CONFIRM",
                    principal=self.price_principal,
                )
        self.assertFalse(applied["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            replay["supplier_price_authority_event_id"],
            applied["supplier_price_authority_event_id"],
        )

    def test_apply_retry_restarts_a_serialization_failure(self):
        attempts = 0

        def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise psycopg.errors.SerializationFailure("synthetic retry")
            return {"result": "ok"}

        with self._connection() as conn:
            result = _run_price_apply_with_retry(
                conn, operation, deadline=time.monotonic() + 5
            )
        self.assertEqual((attempts, result), (2, {"result": "ok"}))

    def test_uploaded_price_drives_selected_offer_review_draft_and_packet(self):
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        _apply_preview, applied = self._apply(batch_id)
        selected_offer_id = self._select_fixture_offer()
        with self._connection() as conn:
            run = recommendations.prepare_monday_run(
                conn,
                business_date=BUSINESS_DATE,
                idempotency_key="focused-uploaded-price-monday-v1",
                variant_ids=("1001",),
                actor="synthetic:matrix-owner:01",
            )
        self.assertEqual(run["blockers"], [])
        recommendation = run["recommendations"][0]
        self.assertEqual(
            (
                recommendation["offer_id"],
                recommendation["recommended_cases"],
                str(recommendation["unit_cost"]),
            ),
            (selected_offer_id, 1, "6.0000"),
        )
        authority = recommendation["metrics"]["selected_offer_input_evidence"][
            "applicable_price_authority"
        ]
        self.assertEqual(
            authority["apply_event"]["event_id"],
            applied["supplier_price_authority_event_id"],
        )
        with self._connection() as conn:
            review_preview = preview_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="EDIT_QUANTITY",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=2,
                approved_loose_units=0,
                comment="cross the uploaded synthetic price break",
            )
            conn.rollback()
            confirmation_id = None
            if review_preview["materiality"]["materiality_tier"] == "MATERIAL":
                confirmation = confirm_material_recommendation_edit(
                    conn,
                    recommendation_id=recommendation["recommendation_id"],
                    actor="synthetic:matrix-owner:01",
                    expected_input_fingerprint=run["input_fingerprint"],
                    expected_review_preview_fingerprint=review_preview[
                        "preview_fingerprint"
                    ],
                    approved_cases=2,
                    approved_loose_units=0,
                    comment="cross the uploaded synthetic price break",
                    confirmation_reason="confirm fabricated uploaded break",
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
                comment="cross the uploaded synthetic price break",
                expected_review_preview_fingerprint=review_preview["preview_fingerprint"],
                material_edit_confirmation_id=confirmation_id,
            )
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
            packet = build_emergency_review_packet(
                conn,
                storage=self.storage,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
            )
        tier = review["final_price_tier"]
        self.assertEqual(
            (
                tier["level_type"],
                Decimal(tier["break_qty"]),
                tier["break_unit"],
                Decimal(tier["case_price"]),
                Decimal(tier["unit_price"]),
                tier["source_price_book_batch_id"],
                tier["source_price_book_row_number"],
                tier["supplier_price_authority_event_id"],
            ),
            (
                "BREAK",
                Decimal("2"),
                "CS",
                Decimal("30"),
                Decimal("5"),
                str(batch_id),
                3,
                applied["supplier_price_authority_event_id"],
            ),
        )
        draft = drafts["drafts"][0]
        self.assertEqual(
            (
                Decimal(draft["merchandise_total"]),
                Decimal(draft["delivery_fee"]),
                Decimal(draft["po_total"]),
            ),
            (Decimal("60"), Decimal("0"), Decimal("60")),
        )
        with self._connection() as conn:
            self.assertTrue(
                final_price_tier_matches_snapshot(
                    conn,
                    run_id=str(run["run_id"]),
                    offer_id=selected_offer_id,
                    final_price_tier=tier,
                )
            )
            forged = {
                **tier,
                "run_price_snapshot_id": tier["run_price_snapshot_id"] + 999,
            }
            self.assertFalse(
                final_price_tier_matches_snapshot(
                    conn,
                    run_id=str(run["run_id"]),
                    offer_id=selected_offer_id,
                    final_price_tier=forged,
                )
            )
        with zipfile.ZipFile(
            io.BytesIO(self.storage.get_bytes(packet["storage_key"]))
        ) as archive:
            self.assertEqual(len(archive.namelist()), 12)
            mapping_evidence = json.loads(
                archive.read("supplier-mapping-evidence.json")
            )["items"][0]
        self.assertEqual(mapping_evidence["final_price_tier"], tier)

    def test_development_forecast_calculation_drives_recommendation_and_packet(self):
        with self._connection() as conn:
            initializer._configure_development_forecast_vendor_calendars(
                conn,
                BUSINESS_DATE,
                profile=initializer.DEVELOPMENT_FORECAST_PROFILE,
            )
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        self._apply(batch_id, key="focused-development-price-apply-v1")
        self._select_fixture_offer()
        with self._connection() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES (%s,%s)",
                    (
                        initializer.DEVELOPMENT_FORECAST_META_KEY,
                        initializer.DEVELOPMENT_FORECAST_DEMO_CONTRACT,
                    ),
                )
            with mock.patch.dict(
                os.environ,
                {"BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST": "0"},
            ):
                with self.assertRaisesRegex(
                    recommendations.MondayRecommendationError,
                    "synthetic development forecast is not authorized",
                ):
                    recommendations.prepare_monday_run(
                        conn,
                        business_date=BUSINESS_DATE,
                        idempotency_key="forecast-marker-alone-must-refuse",
                        variant_ids=("1001",),
                        actor="synthetic:matrix-owner:01",
                    )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM runs WHERE idempotency_key=%s",
                    ("forecast-marker-alone-must-refuse",),
                ).fetchone()[0],
                0,
            )
            conn.rollback()
            run = recommendations.prepare_monday_run(
                conn,
                business_date=BUSINESS_DATE,
                idempotency_key="focused-development-forecast-monday-v1",
                variant_ids=("1001",),
                actor="synthetic:matrix-owner:01",
            )
        self.assertEqual(run["blockers"], [])
        self.assertEqual(run["model_version"], "DEVELOPMENT_ROLLING_ORIGIN_V1")
        self.assertEqual(len(run["recommendations"]), 1)
        recommendation = run["recommendations"][0]
        metrics = recommendation["metrics"]
        evidence = metrics["development_forecast_evidence"]
        self.assertEqual(
            (
                evidence["protection_calendar"]["next_order_date"],
                evidence["protection_calendar"]["next_receipt_date"],
                evidence["horizon_days"],
            ),
            ("2026-10-12", "2026-10-15", 10),
        )
        self.assertEqual(
            metrics["development_forecast_contract"],
            DEVELOPMENT_FORECAST_CONTRACT,
        )
        self.assertTrue(validate_development_forecast_evidence(evidence))
        point = Decimal(evidence["point_forecast_units"])
        protection = Decimal(evidence["protection_units"])
        target = point + protection
        effective = Decimal(metrics["available_units"]) + Decimal(
            metrics["trusted_incoming_units"]
        )
        independent_raw_need = max(
            0,
            int((target - effective).to_integral_value(rounding=ROUND_CEILING)),
        )
        units_per_case = int(
            Decimal(metrics["frozen_offer_evidence"]["shopify_units_per_case"])
        )
        independent_cases = (
            independent_raw_need + units_per_case - 1
        ) // units_per_case
        self.assertEqual(Decimal(metrics["target_units"]), target)
        self.assertEqual(int(metrics["raw_need_units"]), independent_raw_need)
        self.assertEqual(recommendation["recommended_cases"], independent_cases)
        self.assertEqual(
            recommendation["recommended_units"],
            independent_cases * units_per_case,
        )
        with self._connection() as conn:
            frozen = conn.execute(
                """SELECT selected_model,demand_regime,xyz_class,forecast_units,
                          safety_stock_units,baseline_replenishment_units,
                          method_version,diagnostics
                     FROM forecast_results
                    WHERE run_id=%s AND variant_id='1001'""",
                (run["run_id"],),
            ).fetchone()
            self.assertEqual(
                (
                    frozen[0],
                    frozen[1],
                    frozen[2],
                    Decimal(frozen[3]),
                    Decimal(frozen[4]),
                    int(frozen[5]),
                    frozen[6],
                ),
                (
                    evidence["selected_model"],
                    evidence["demand_regime"],
                    evidence["xyz_class"],
                    point,
                    protection,
                    independent_raw_need,
                    evidence["method_version"],
                ),
            )
            self.assertEqual(
                frozen[7]["development_forecast_evidence_sha256"],
                evidence["sha256"],
            )
            manifest = conn.execute(
                "SELECT procurement_input_manifest FROM runs WHERE run_id=%s",
                (run["run_id"],),
            ).fetchone()[0]
            malformed = json.loads(manifest)
            del malformed["contexts"][0][
                "development_forecast_evidence_sha256"
            ]
            with self.assertRaisesRegex(
                SyntheticSelectedOfferError,
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID",
            ):
                classify_frozen_manifest(json.dumps(malformed, sort_keys=True))
            mixed_method = json.loads(manifest)
            mixed_method["method_version"] = DEVELOPMENT_FORECAST_V2_METHOD_VERSION
            with self.assertRaisesRegex(
                SyntheticSelectedOfferError,
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID",
            ):
                classify_frozen_manifest(json.dumps(mixed_method, sort_keys=True))
            conn.rollback()
            review_preview = preview_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=recommendation["recommended_cases"],
                approved_loose_units=recommendation["recommended_loose_units"],
                comment="",
            )
            review = record_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=recommendation["recommended_cases"],
                approved_loose_units=recommendation["recommended_loose_units"],
                comment="",
                expected_review_preview_fingerprint=review_preview[
                    "preview_fingerprint"
                ],
            )
            draft_preview = preview_vendor_drafts(
                conn,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
            )
            drafts = build_vendor_drafts(
                conn,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
                expected_preview_fingerprint=draft_preview["preview_fingerprint"],
                minimum_disposition=draft_preview["minimum_disposition"],
            )
            packet = build_emergency_review_packet(
                conn,
                storage=self.storage,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
            )
        self.assertEqual(review["approved_cases"], independent_cases)
        self.assertEqual(drafts["drafts"][0]["lines"][0]["cases"], independent_cases)
        with zipfile.ZipFile(io.BytesIO(self.storage.get_bytes(packet["storage_key"]))) as archive:
            self.assertIn("forecast-and-protection-evidence.json", archive.namelist())
            forecast_member = json.loads(
                archive.read("forecast-and-protection-evidence.json")
            )
        self.assertEqual(forecast_member["contract"], DEVELOPMENT_FORECAST_CONTRACT)
        self.assertFalse(forecast_member["commercial_authority"])
        self.assertEqual(len(forecast_member["items"]), 1)
        self.assertEqual(forecast_member["items"][0]["variant_id"], "1001")
        self.assertEqual(
            forecast_member["items"][0]["evidence_sha256"],
            evidence["sha256"],
        )

    def test_v2_h17_forecast_drives_selected_price_draft_and_bound_packet_need(self):
        with self._connection() as conn:
            self._activate_v2_forecast_fixture(conn)
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        self._apply(batch_id, key="focused-development-v2-price-apply")
        self._select_fixture_offer()
        with self._connection() as conn:
            run = recommendations.prepare_monday_run(
                conn,
                business_date=BUSINESS_DATE,
                idempotency_key="focused-development-forecast-monday-v2",
                variant_ids=("1001",),
                actor="synthetic:matrix-owner:01",
            )
        self.assertEqual(run["blockers"], [])
        self.assertEqual(run["model_version"], DEVELOPMENT_FORECAST_V2_METHOD_VERSION)
        recommendation = run["recommendations"][0]
        metrics = recommendation["metrics"]
        evidence = metrics["development_forecast_evidence"]
        self.assertEqual(metrics["development_forecast_contract"], DEVELOPMENT_FORECAST_V2_CONTRACT)
        self.assertEqual(
            (
                evidence["protection_calendar"]["current_order_receipt_date"],
                evidence["protection_calendar"]["next_submission_date"],
                evidence["protection_calendar"]["next_order_receipt_date"],
                evidence["horizon_days"],
                Decimal(evidence["point_forecast_units"]),
                Decimal(evidence["protection_units"]),
            ),
            (
                "2026-10-08",
                "2026-10-19",
                "2026-10-22",
                17,
                Decimal("34.0000"),
                Decimal("0.0000"),
            ),
        )
        self.assertEqual(
            (
                int(metrics["raw_need_units"]),
                recommendation["recommended_cases"],
                recommendation["recommended_units"],
                Decimal(recommendation["unit_cost"]),
            ),
            (34, 6, 36, Decimal("5.0000000000")),
        )
        with self._connection() as conn:
            manifest_text = conn.execute(
                "SELECT procurement_input_manifest FROM runs WHERE run_id=%s",
                (run["run_id"],),
            ).fetchone()[0]
            manifest = json.loads(manifest_text)
            context = manifest["contexts"][0]
            self.assertTrue(
                recommendations.validate_frozen_run_inventory_evidence(
                    conn,
                    run_id=str(run["run_id"]),
                    manifest=manifest,
                )
            )

            class InventoryCopyMutationConnection:
                def __init__(self, connection, *, target, mutation):
                    self.connection = connection
                    self.target = target
                    self.mutation = mutation

                def execute(self, statement, parameters=()):
                    cursor = self.connection.execute(statement, parameters)
                    normalized = " ".join(str(statement).split()).lower()
                    if self.target not in normalized:
                        return cursor
                    rows = list(cursor.fetchall())
                    if self.mutation == "missing":
                        rows = rows[1:]
                    elif self.mutation == "extra":
                        rows = rows + rows[:1]

                    class FrozenRows:
                        def fetchall(self):
                            return rows

                    return FrozenRows()

            for target in (
                "from procurement_recommendations",
                "from inventory_snapshots",
            ):
                for mutation in ("missing", "extra"):
                    with self.subTest(target=target, mutation=mutation):
                        self.assertFalse(
                            recommendations.validate_frozen_run_inventory_evidence(
                                InventoryCopyMutationConnection(
                                    conn, target=target, mutation=mutation
                                ),
                                run_id=str(run["run_id"]),
                                manifest=manifest,
                            )
                        )
            self.assertTrue(
                validate_development_baseline_need_context(
                    context,
                    manifest_contract=DEVELOPMENT_FORECAST_V2_CONTRACT,
                )
            )
            forged = json.loads(manifest_text)
            forged["contexts"][0]["need"]["cases"] = 99
            with self.assertRaisesRegex(
                SyntheticSelectedOfferError,
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID",
            ):
                classify_frozen_manifest(json.dumps(forged, sort_keys=True))
            copied = json.loads(manifest_text)
            copied["contexts"][0]["variant_id"] = "4001"
            with self.assertRaisesRegex(
                SyntheticSelectedOfferError,
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID",
            ):
                classify_frozen_manifest(json.dumps(copied, sort_keys=True))
            mixed_method = json.loads(manifest_text)
            mixed_method["method_version"] = "DEVELOPMENT_ROLLING_ORIGIN_V1"
            with self.assertRaisesRegex(
                SyntheticSelectedOfferError,
                "SYNTHETIC_SELECTED_OFFER_FROZEN_MANIFEST_INVALID",
            ):
                classify_frozen_manifest(json.dumps(mixed_method, sort_keys=True))
            conn.rollback()
            preview = preview_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=6,
                approved_loose_units=0,
                comment="",
            )
            review = record_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
                approved_cases=6,
                approved_loose_units=0,
                comment="",
                expected_review_preview_fingerprint=preview["preview_fingerprint"],
            )
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
            with mock.patch(
                "procurement_os.emergency_packet.validate_frozen_run_inventory_evidence",
                wraps=recommendations.validate_frozen_run_inventory_evidence,
            ) as inventory_verifier:
                packet = build_emergency_review_packet(
                    conn,
                    storage=self.storage,
                    run_id=run["run_id"],
                    actor="synthetic:matrix-owner:01",
                )
            inventory_verifier.assert_called_once()
        self.assertEqual(review["approved_cases"], 6)
        self.assertEqual(
            (
                drafts["drafts"][0]["lines"][0]["cases"],
                Decimal(drafts["drafts"][0]["merchandise_total"]),
                Decimal(drafts["drafts"][0]["po_total"]),
            ),
            (6, Decimal("180"), Decimal("180")),
        )
        with zipfile.ZipFile(
            io.BytesIO(self.storage.get_bytes(packet["storage_key"]))
        ) as archive:
            self.assertEqual(len(archive.namelist()), 13)
            forecast_member = json.loads(
                archive.read("forecast-and-protection-evidence.json")
            )
        self.assertEqual(forecast_member["contract"], DEVELOPMENT_FORECAST_V2_CONTRACT)
        self.assertEqual(
            forecast_member["items"][0]["calculated_need_binding"],
            context["development_baseline_need_binding"],
        )
        with self._connection() as conn, mock.patch(
            "procurement_os.emergency_packet.validate_frozen_run_inventory_evidence",
            side_effect=AssertionError("terminal replay revalidated inventory"),
        ), mock.patch(
            "procurement_os.emergency_packet.get_vendor_drafts",
            side_effect=AssertionError("terminal replay rebuilt DRAFT evidence"),
        ):
            replay = build_emergency_review_packet(
                conn,
                storage=self.storage,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
            )
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["sha256"], packet["sha256"])

    def test_v2_prepare_rejects_stale_inventory_capture_header_without_partial_run(self):
        with self._connection() as conn:
            self._activate_v2_forecast_fixture(conn)
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        self._apply(batch_id, key="focused-development-v2-stale-inventory-price-apply")
        self._select_fixture_offer()
        idempotency_key = "focused-development-v2-stale-inventory"
        stale_capture_id = str(uuid.uuid4())
        with self._connection() as conn:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO inventory_snapshot_runs(
                               inventory_snapshot_run_id,business_date,status,source,
                               source_hash,rows_received)
                           VALUES (%s,%s,'RUNNING','STALE_CAPTURE_HEADER_TEST',%s,1)""",
                    (stale_capture_id, BUSINESS_DATE, "0" * 64),
                )
                conn.execute(
                    """INSERT INTO inventory_snapshot_run_rows(
                               inventory_snapshot_run_id,variant_id,location_gid,
                               available_quantity,incoming_quantity,on_hand_quantity,
                               committed_quantity,reserved_quantity,damaged_quantity,
                               validation_status)
                           VALUES (%s,'1001','synthetic-location-001',0,0,0,0,0,0,
                                   'VALID')""",
                    (stale_capture_id,),
                )
                conn.execute(
                    """UPDATE inventory_snapshot_runs
                          SET status='COMPLETED',completed_at=%s,eligible_rows=1
                        WHERE inventory_snapshot_run_id=%s""",
                    (
                        datetime(2026, 10, 5, 13, tzinfo=timezone.utc),
                        stale_capture_id,
                    ),
                )
            with self.assertRaisesRegex(
                recommendations.MondayRecommendationError,
                "frozen inventory evidence differs from its immutable capture",
            ):
                recommendations.prepare_monday_run(
                    conn,
                    business_date=BUSINESS_DATE,
                    idempotency_key=idempotency_key,
                    variant_ids=("1001",),
                    actor="synthetic:matrix-owner:01",
                )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM runs WHERE idempotency_key=%s",
                    (idempotency_key,),
                ).fetchone()[0],
                0,
            )
            conn.rollback()

    def test_v2_registration_history_and_schedule_drift_refuse_without_partial_run(self):
        with self._connection() as conn:
            self._activate_v2_forecast_fixture(conn)
            registration = json.loads(
                conn.execute(
                    "SELECT value FROM meta WHERE key=%s",
                    (initializer.DEVELOPMENT_FORECAST_REGISTRATION_META_KEY,),
                ).fetchone()[0]
            )
            registration["policy_canonical_sha256"] = "0" * 64
            conn.execute(
                "UPDATE meta SET value=%s WHERE key=%s",
                (
                    json.dumps(registration, sort_keys=True, separators=(",", ":")),
                    initializer.DEVELOPMENT_FORECAST_REGISTRATION_META_KEY,
                ),
            )
            conn.commit()
            with self.assertRaisesRegex(
                recommendations.MondayRecommendationError,
                "registration differs",
            ):
                recommendations.prepare_monday_run(
                    conn,
                    business_date=BUSINESS_DATE,
                    idempotency_key="v2-registration-drift-refuses",
                    variant_ids=("1001",),
                    actor="synthetic:matrix-owner:01",
                )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM runs WHERE idempotency_key=%s",
                    ("v2-registration-drift-refuses",),
                ).fetchone()[0],
                0,
            )
            conn.rollback()

    def test_v2_actual_service_context_changes_need_when_history_changes(self):
        with self._connection() as conn:
            self._activate_v2_forecast_fixture(conn)
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        self._apply(batch_id, key="focused-development-v2-history-price-apply")
        self._select_fixture_offer()
        with self._connection() as conn:
            evaluation_at = recommendations._database_evaluation_at(conn)
            conn.rollback()
            first = recommendations._load_context(
                conn,
                business_date=BUSINESS_DATE,
                variant_id="1001",
                evaluation_at=evaluation_at,
                offer_resolution_contract="SYNTHETIC_CONFIRMED_SELECTION_V1",
                development_forecast_contract=DEVELOPMENT_FORECAST_V2_CONTRACT,
            )
            conn.rollback()
            self.assertEqual((first["need"].raw_need_units, first["need"].cases), (34, 6))
            evidence = conn.execute(
                """SELECT evidence_json FROM readiness_gates
                    WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL'
                      AND scope_id=''"""
            ).fetchone()[0]
            conn.execute(
                """UPDATE sales_daily SET units_sold=1,net_sales=4.99
                    WHERE variant_id='1001' AND source='SYNTHETIC_TEST'
                      AND sale_date BETWEEN %s AND %s""",
                (BUSINESS_DATE - timedelta(days=138), BUSINESS_DATE - timedelta(days=1)),
            )
            rows = conn.execute(
                """SELECT sale_date,units_sold,net_sales,distinct_orders,source,
                          run_id::text
                     FROM sales_daily
                    WHERE variant_id='1001' AND source='SYNTHETIC_TEST'
                      AND sale_date BETWEEN %s AND %s
                    ORDER BY sale_date,source""",
                (BUSINESS_DATE - timedelta(days=138), BUSINESS_DATE - timedelta(days=1)),
            ).fetchall()
            evidence["variant_coverage"]["1001"] = {
                "row_count": 138,
                "sha256": recommendations._sales_coverage_digest(rows),
            }
            conn.execute(
                """UPDATE readiness_gates SET evidence_json=%s::jsonb
                    WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL'
                      AND scope_id=''""",
                (json.dumps(evidence, sort_keys=True),),
            )
            conn.commit()
            second = recommendations._load_context(
                conn,
                business_date=BUSINESS_DATE,
                variant_id="1001",
                evaluation_at=evaluation_at,
                offer_resolution_contract="SYNTHETIC_CONFIRMED_SELECTION_V1",
                development_forecast_contract=DEVELOPMENT_FORECAST_V2_CONTRACT,
            )
        self.assertEqual((second["need"].raw_need_units, second["need"].cases), (17, 3))
        self.assertNotEqual(
            first["development_forecast_evidence_sha256"],
            second["development_forecast_evidence_sha256"],
        )
        self.assertNotEqual(
            first["development_baseline_need_binding"]["sha256"],
            second["development_baseline_need_binding"]["sha256"],
        )

    def test_review_rejects_authority_envelope_when_snapshot_lineage_is_missing(self):
        run, recommendation = self._prepare_applied_selected_run(
            key="focused-review-missing-snapshot-lineage"
        )
        with self._connection() as conn:
            control = preview_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
            )
            self.assertTrue(
                all(
                    control["final_price_tier"].get(field) is not None
                    for field in (
                        "source_price_book_batch_id",
                        "source_price_book_row_number",
                        "supplier_price_authority_event_id",
                        "applicable_price_authority_sha256",
                    )
                )
            )
            projected = _ReviewLineageProjectionConnection(
                conn, omit_snapshot_lineage=True
            )
            with self.assertRaisesRegex(
                ProcurementReviewError,
                "^selected final price tier authority is not bound$",
            ):
                record_recommendation_review(
                    projected,
                    recommendation_id=recommendation["recommendation_id"],
                    action="ACCEPT",
                    actor="synthetic:matrix-owner:01",
                    expected_input_fingerprint=run["input_fingerprint"],
                    expected_review_preview_fingerprint=control[
                        "preview_fingerprint"
                    ],
                )
            self.assertEqual(projected.snapshot_projection_hits, 1)
            self.assertEqual(projected.context_projection_hits, 0)
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM review_decisions WHERE recommendation_id=%s",
                    (recommendation["recommendation_id"],),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT workflow_stage FROM runs WHERE run_id=%s",
                    (run["run_id"],),
                ).fetchone()[0],
                "AWAITING_REVIEW",
            )

    def test_review_rejects_snapshot_lineage_without_binding_authority_envelope(self):
        run, recommendation = self._prepare_applied_selected_run(
            key="focused-review-missing-authority-envelope"
        )
        with self._connection() as conn:
            control = preview_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
            )
            self.assertIn(
                "applicable_price_authority",
                control["metrics"]["selected_offer_input_evidence"],
            )
            projected = _ReviewLineageProjectionConnection(
                conn, omit_authority_envelope=True
            )
            with self.assertRaisesRegex(
                ProcurementReviewError,
                "^selected final price tier has unbound authority lineage$",
            ):
                record_recommendation_review(
                    projected,
                    recommendation_id=recommendation["recommendation_id"],
                    action="ACCEPT",
                    actor="synthetic:matrix-owner:01",
                    expected_input_fingerprint=run["input_fingerprint"],
                    expected_review_preview_fingerprint=control[
                        "preview_fingerprint"
                    ],
                )
            self.assertEqual(projected.context_projection_hits, 1)
            snapshot_lineage = conn.execute(
                """SELECT source_price_id,source_price_book_batch_id,
                          source_price_book_row_number,
                          supplier_price_authority_event_id
                     FROM run_price_snapshots
                    WHERE run_id=%s ORDER BY run_price_snapshot_id""",
                (run["run_id"],),
            ).fetchall()
            self.assertTrue(snapshot_lineage)
            self.assertTrue(
                all(all(value is not None for value in row) for row in snapshot_lineage)
            )
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM review_decisions WHERE recommendation_id=%s",
                    (recommendation["recommendation_id"],),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT workflow_stage FROM runs WHERE run_id=%s",
                    (run["run_id"],),
                ).fetchone()[0],
                "AWAITING_REVIEW",
            )

    def test_default_or_wrong_runtime_refuses_before_batch_write(self):
        with self._connection() as conn:
            before = int(conn.execute("SELECT count(*) FROM price_book_batches").fetchone()[0])
            with mock.patch.dict(
                os.environ,
                {"BUFFALO_ENABLE_SYNTHETIC_PRICE_REPLACEMENT": "0"},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    SyntheticPriceReplacementError,
                    "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED",
                ):
                    stage_and_validate_declared_price_book(
                        conn,
                        self.storage,
                        csv_bytes=self.book,
                        principal=self.price_principal,
                        expected_declaration_sha256="0" * 64,
                    )
            self.assertEqual(
                int(conn.execute("SELECT count(*) FROM price_book_batches").fetchone()[0]),
                before,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "BUFFALO_RUNTIME_MODE": "SYNTHETIC_DEMO",
                    "DATABASE_URL": self.test_url,
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    SyntheticPriceReplacementError,
                    "SYNTHETIC_PRICE_REPLACEMENT_NOT_AUTHORIZED",
                ):
                    registered_target_declaration(conn)

    def test_recovery_state_drift_refuses_before_apply(self):
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        backup = self._backup(batch_id)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES "
                "('synthetic_focused_recovery_drift','intentional test-only drift')"
            )
        with mock.patch(
            "procurement_os.synthetic_price_replacement.verify_bound_price_apply_backup",
            return_value=backup,
        ):
            with self._connection() as conn:
                with self.assertRaisesRegex(
                    SyntheticPriceReplacementError,
                    "price APPLY recovery proof differs",
                ):
                    preview_price_replacement(
                        conn,
                        self.storage,
                        batch_id=batch_id,
                        apply_idempotency_key="focused-drift-apply-v1",
                        principal=self.price_principal,
                    )
        with self._connection() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM price_book_batches WHERE price_book_batch_id=%s",
                    (batch_id,),
                ).fetchone(),
                ("VERIFIED_FUTURE",),
            )

    def test_completed_old_price_packet_stays_byte_immutable(self):
        selected_offer_id = self._select_fixture_offer()
        with self._connection() as conn:
            run = recommendations.prepare_monday_run(
                conn,
                business_date=BUSINESS_DATE,
                idempotency_key="focused-old-price-packet-v1",
                variant_ids=("1001",),
                actor="synthetic:matrix-owner:01",
            )
            recommendation = run["recommendations"][0]
            preview = preview_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
            )
            record_recommendation_review(
                conn,
                recommendation_id=recommendation["recommendation_id"],
                action="ACCEPT",
                actor="synthetic:matrix-owner:01",
                expected_input_fingerprint=run["input_fingerprint"],
                expected_review_preview_fingerprint=preview["preview_fingerprint"],
            )
            draft_preview = preview_vendor_drafts(
                conn, run_id=run["run_id"], actor="synthetic:matrix-owner:01"
            )
            build_vendor_drafts(
                conn,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
                expected_preview_fingerprint=draft_preview["preview_fingerprint"],
                minimum_disposition=draft_preview["minimum_disposition"],
            )
            packet_before = build_emergency_review_packet(
                conn,
                storage=self.storage,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
            )
            manifest = json.loads(
                conn.execute(
                    "SELECT procurement_input_manifest FROM runs WHERE run_id=%s",
                    (run["run_id"],),
                ).fetchone()[0]
            )
        before_bytes = self.storage.get_bytes(packet_before["storage_key"])
        malformed = dict(manifest)
        malformed.pop("offer_resolution_contract")
        with self.assertRaises(SyntheticSelectedOfferError):
            classify_frozen_manifest(
                json.dumps(malformed, sort_keys=True, separators=(",", ":"))
            )
        batch_id, _confirmation_preview, _confirmed = self._stage_and_confirm()
        self._apply(batch_id, key="focused-after-old-packet-apply-v1")
        with self._connection() as conn:
            packet_after = build_emergency_review_packet(
                conn,
                storage=self.storage,
                run_id=run["run_id"],
                actor="synthetic:matrix-owner:01",
            )
        self.assertEqual(packet_after["sha256"], packet_before["sha256"])
        self.assertEqual(self.storage.get_bytes(packet_after["storage_key"]), before_bytes)
        self.assertEqual(selected_offer_id, recommendation["offer_id"])


if __name__ == "__main__":
    unittest.main()
