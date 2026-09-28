"""Deterministic emergency forecast and demand-evidence tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import UUID

from procurement_os.forecasting import (
    DailyNetSales,
    DemandObservation,
    PointInTimeSnapshotRef,
    ValidatedSalesCoverage,
    build_demand_evidence,
    canonical_evidence_sha256,
    forecast_demand,
    to_forecast_observations,
)


START = date(2026, 8, 1)


def series(units, states):
    return [
        DemandObservation(START + timedelta(days=index), Decimal(str(value)), state)
        for index, (value, state) in enumerate(zip(units, states, strict=True))
    ]


def sales_rows(units, *, source="SYNTHETIC_TEST"):
    return tuple(
        DailyNetSales(START + timedelta(days=index), Decimal(str(value)), source)
        for index, value in enumerate(units)
    )


def coverage(rows, *, start=None, end=None, source="SYNTHETIC_TEST"):
    history_start = start or min(row.business_date for row in rows)
    history_end = end or max(row.business_date for row in rows)
    rows_sha256 = canonical_evidence_sha256(
        [row.to_json_dict() for row in sorted(rows, key=lambda item: item.business_date)]
    )
    authority = {
        "contract": "DISPOSABLE_SYNTHETIC_DAILY_VARIANT_COVERAGE_V1",
        "source": source,
        "history_start": history_start,
        "history_end": history_end,
        "coverage_complete": True,
        "sales_rows_sha256": rows_sha256,
        "gate_evidence": {"coverage_complete": True, "source": source},
    }
    return ValidatedSalesCoverage.from_authority(authority, rows)


def snapshot(
    day,
    *,
    location="gid://shopify/Location/1",
    run_id="00000000-0000-4000-8000-000000000001",
    source="SYNTHETIC_TEST",
    source_hash="a" * 64,
    captured_at=datetime(2026, 8, 1, 21, 0, tzinfo=timezone.utc),
    completed_at=datetime(2026, 8, 1, 21, 5, tzinfo=timezone.utc),
    available="0",
    incoming="0",
    validation_status="VALID",
):
    return PointInTimeSnapshotRef(
        snapshot_date=day,
        location_gid=location,
        inventory_snapshot_run_id=run_id,
        source=source,
        source_hash=source_hash,
        captured_at=captured_at,
        completed_at=completed_at,
        available_quantity=Decimal(available),
        incoming_quantity=Decimal(incoming),
        validation_status=validation_status,
    )


class ForecastingTests(unittest.TestCase):
    def test_stable_in_stock_history_is_high_confidence_and_exact(self):
        result = forecast_demand(series([1] * 28, ["IN_STOCK"] * 28), horizon_days=10)
        self.assertEqual(result.calendar_velocity, Decimal("1.000000"))
        self.assertEqual(result.in_stock_velocity, Decimal("1.000000"))
        self.assertEqual(result.forecast_units, Decimal("10.0000"))
        self.assertEqual(result.confidence, "HIGH")

    def test_proven_stockouts_are_censored_but_unknown_is_not_in_stock(self):
        proven = forecast_demand(
            series([2] * 7 + [0] * 7, ["IN_STOCK"] * 7 + ["STOCKOUT"] * 7),
            horizon_days=7,
        )
        unknown = forecast_demand(
            series([2] * 7 + [0] * 7, ["IN_STOCK"] * 7 + ["UNKNOWN"] * 7),
            horizon_days=7,
        )
        self.assertEqual(proven.in_stock_velocity, Decimal("2.000000"))
        self.assertEqual(proven.censored_stockout_days, 7)
        self.assertEqual(proven.forecast_units, Decimal("14.0000"))
        self.assertGreater(proven.forecast_units, unknown.forecast_units)
        self.assertEqual(unknown.unknown_inventory_days, 7)
        self.assertIn("UNKNOWN_INVENTORY_DAYS_NOT_ASSUMED_IN_STOCK", unknown.reason_codes)

    def test_true_in_stock_zero_days_reduce_velocity(self):
        result = forecast_demand(
            series([2] * 7 + [0] * 7, ["IN_STOCK"] * 14), horizon_days=7
        )
        self.assertEqual(result.in_stock_velocity, Decimal("1.000000"))
        self.assertEqual(result.forecast_daily_velocity, Decimal("1.000000"))

    def test_returns_remain_in_net_demand_and_never_make_negative_forecast(self):
        result = forecast_demand(
            series([5, -10], ["IN_STOCK", "IN_STOCK"]), horizon_days=30
        )
        self.assertEqual(result.calendar_velocity, Decimal("0.000000"))
        self.assertEqual(result.forecast_units, Decimal("0.0000"))

    def test_low_level_forecast_requires_builder_materialized_contiguous_dates(self):
        with self.assertRaisesRegex(ValueError, "contiguous"):
            forecast_demand(
                [
                    DemandObservation(START, Decimal("1"), "IN_STOCK"),
                    DemandObservation(
                        START + timedelta(days=2), Decimal("1"), "IN_STOCK"
                    ),
                ],
                horizon_days=3,
            )

    def test_recent_signal_is_damped_and_bounded(self):
        result = forecast_demand(
            series([1] * 14 + [10] * 14, ["IN_STOCK"] * 28), horizon_days=1
        )
        self.assertEqual(result.calendar_velocity, Decimal("5.500000"))
        self.assertEqual(result.recent_velocity, Decimal("10.000000"))
        self.assertEqual(result.forecast_daily_velocity, Decimal("6.187500"))

    def test_bulk_event_is_robustly_capped_and_forces_review_evidence(self):
        result = forecast_demand(
            series([1000] + [1] * 83, ["IN_STOCK"] * 84), horizon_days=7
        )
        self.assertEqual(result.forecast_units, Decimal("7.0000"))
        self.assertEqual(result.outlier_capped_days, 1)
        self.assertIn("ROBUST_OUTLIER_CAP_APPLIED", result.reason_codes)
        self.assertIn("EVENT_OR_BULK_CONCENTRATION_REVIEW", result.reason_codes)
        sparse = forecast_demand(
            series([1000] + [0] * 83, ["IN_STOCK"] * 84), horizon_days=7
        )
        self.assertGreater(sparse.forecast_units, Decimal("0"))
        self.assertLessEqual(sparse.forecast_units, Decimal("1"))
        self.assertEqual(sparse.confidence, "LOW")
        ordinary = forecast_demand(
            series([1] + [0] * 83, ["IN_STOCK"] * 84), horizon_days=7
        )
        self.assertGreater(ordinary.forecast_units, Decimal("0"))
        self.assertEqual(ordinary.outlier_capped_days, 0)

    def test_sparse_stockout_censoring_is_evidence_shrunk(self):
        result = forecast_demand(
            series([2] + [0] * 27, ["IN_STOCK"] + ["STOCKOUT"] * 27),
            horizon_days=30,
        )
        self.assertLess(result.forecast_units, Decimal("60"))
        self.assertEqual(result.confidence, "LOW")

    def test_thin_or_low_coverage_history_is_explicitly_low_confidence(self):
        result = forecast_demand(
            series([1] * 10, ["UNKNOWN"] * 10), horizon_days=7
        )
        self.assertEqual(result.confidence, "LOW")
        self.assertIn("LOW_CONFIDENCE_REVIEW_REQUIRED", result.reason_codes)

    def test_invalid_dates_states_numbers_and_horizon_fail_closed(self):
        good = DemandObservation(START, Decimal("1"), "IN_STOCK")
        cases = (
            ([good], 0),
            ([good, good], 1),
            ([DemandObservation(datetime(2026, 8, 1), Decimal("1"), "IN_STOCK")], 1),
            ([DemandObservation(START, Decimal("NaN"), "IN_STOCK")], 1),
            ([DemandObservation(START, Decimal("1"), "ASSUMED")], 1),
        )
        for observations, horizon in cases:
            with self.subTest(observations=observations, horizon=horizon):
                with self.assertRaises(ValueError):
                    forecast_demand(observations, horizon_days=horizon)

    def test_point_in_time_inventory_and_positive_sales_never_prove_full_day_state(self):
        rows = sales_rows([0, 3])
        evidence = build_demand_evidence(
            history_start=START,
            history_end=START + timedelta(days=1),
            sales_rows=rows,
            sales_coverage=coverage(rows),
            snapshot_rows=(
                snapshot(START, available="0"),
                snapshot(
                    START + timedelta(days=1),
                    run_id="00000000-0000-4000-8000-000000000002",
                    captured_at=datetime(2026, 8, 2, 21, 0, tzinfo=timezone.utc),
                    completed_at=datetime(2026, 8, 2, 21, 5, tzinfo=timezone.utc),
                    available="5",
                ),
            ),
        )
        self.assertEqual(
            tuple(day.inventory_state for day in evidence.daily),
            ("UNKNOWN", "UNKNOWN"),
        )
        self.assertEqual(
            tuple(day.state_basis for day in evidence.daily),
            ("POINT_IN_TIME_SNAPSHOT_ONLY", "POINT_IN_TIME_SNAPSHOT_ONLY"),
        )
        self.assertEqual(evidence.availability_summary["unknown_days"], 2)
        self.assertEqual(evidence.availability_summary["proven_full_day_in_stock_days"], 0)
        self.assertEqual(evidence.availability_summary["proven_full_day_stockout_days"], 0)
        self.assertEqual(evidence.availability_summary["positive_snapshot_rows"], 1)
        self.assertEqual(evidence.availability_summary["zero_snapshot_rows"], 1)
        self.assertEqual(
            evidence.reason_codes,
            (
                "POINT_IN_TIME_INVENTORY_NOT_FULL_DAY_AVAILABILITY",
                "STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE",
            ),
        )
        forecast = forecast_demand(
            to_forecast_observations(evidence), horizon_days=1
        )
        self.assertEqual(forecast.censored_stockout_days, 0)
        self.assertIsNone(forecast.in_stock_velocity)
        self.assertIn(
            "STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE", forecast.reason_codes
        )

    def test_complete_coverage_materializes_dates_and_mismatches_fail_closed(self):
        sparse = (
            DailyNetSales(START, Decimal("1"), "SYNTHETIC_TEST"),
            DailyNetSales(START + timedelta(days=2), Decimal("2"), "SYNTHETIC_TEST"),
        )
        exact_coverage = coverage(
            sparse, start=START, end=START + timedelta(days=2)
        )
        evidence = build_demand_evidence(
            history_start=START,
            history_end=START + timedelta(days=2),
            sales_rows=sparse,
            sales_coverage=exact_coverage,
            snapshot_rows=(),
        )
        self.assertEqual(
            tuple(day.net_units for day in evidence.daily),
            (Decimal("1"), Decimal("0"), Decimal("2")),
        )
        self.assertEqual(
            tuple(day.state_basis for day in evidence.daily),
            ("NO_POINT_IN_TIME_SNAPSHOT",) * 3,
        )
        mismatches = (
            (replace(exact_coverage, history_end=START + timedelta(days=1)), sparse),
            (replace(exact_coverage, source="OTHER"), sparse),
            (replace(exact_coverage, sales_rows_sha256="f" * 64), sparse),
            (replace(exact_coverage, authority_sha256="b" * 64), sparse),
            (
                exact_coverage,
                (
                    DailyNetSales(START, Decimal("9"), "SYNTHETIC_TEST"),
                    sparse[1],
                ),
            ),
        )
        for mismatched_coverage, mismatched_rows in mismatches:
            with self.subTest(
                coverage=mismatched_coverage, rows=mismatched_rows
            ):
                with self.assertRaises(ValueError):
                    build_demand_evidence(
                        history_start=START,
                        history_end=START + timedelta(days=2),
                        sales_rows=mismatched_rows,
                        sales_coverage=mismatched_coverage,
                        snapshot_rows=(),
                    )

        for complete_value in (None, False):
            with self.subTest(coverage_complete=complete_value):
                authority = {
                    "contract": "DISPOSABLE_SYNTHETIC_DAILY_VARIANT_COVERAGE_V1",
                    "source": "SYNTHETIC_TEST",
                    "history_start": START,
                    "history_end": START + timedelta(days=2),
                    "sales_rows_sha256": canonical_evidence_sha256(
                        [row.to_json_dict() for row in sparse]
                    ),
                    "gate_evidence": {"coverage_complete": complete_value},
                }
                if complete_value is not None:
                    authority["coverage_complete"] = complete_value
                with self.assertRaises(ValueError):
                    ValidatedSalesCoverage.from_authority(authority, sparse)

    def test_raw_windows_use_actual_denominators_and_preserve_signed_returns(self):
        for length in (6, 7, 13, 14, 27, 28):
            with self.subTest(length=length):
                rows = sales_rows([1] * length)
                evidence = build_demand_evidence(
                    history_start=START,
                    history_end=START + timedelta(days=length - 1),
                    sales_rows=rows,
                    sales_coverage=coverage(rows),
                    snapshot_rows=(),
                )
                for requested in (7, 14, 28):
                    window = evidence.raw_windows[str(requested)]
                    expected_days = min(length, requested)
                    self.assertEqual(window.requested_window, requested)
                    self.assertEqual(window.actual_denominator_days, expected_days)
                    self.assertEqual(
                        window.signed_net_units, Decimal(expected_days).quantize(Decimal("0.0001"))
                    )
                    self.assertEqual(
                        window.signed_calendar_velocity, Decimal("1.000000")
                    )

        return_rows = sales_rows([-10, 1, 1, 1, 1, 1, 1])
        return_evidence = build_demand_evidence(
            history_start=START,
            history_end=START + timedelta(days=6),
            sales_rows=return_rows,
            sales_coverage=coverage(return_rows),
            snapshot_rows=(),
        )
        self.assertEqual(
            return_evidence.raw_windows["7"].signed_net_units, Decimal("-4.0000")
        )
        self.assertEqual(
            return_evidence.raw_windows["7"].signed_calendar_velocity,
            Decimal("-0.571429"),
        )
        forecast = forecast_demand(
            to_forecast_observations(return_evidence), horizon_days=7
        )
        self.assertEqual(forecast.forecast_units, Decimal("0.0000"))

    def test_invalid_or_conflicting_evidence_refuses_deterministically(self):
        rows = sales_rows([1])
        exact_coverage = coverage(rows)
        invalid_cases = (
            (
                rows + (DailyNetSales(START, Decimal("1"), "SYNTHETIC_TEST"),),
                exact_coverage,
                (),
            ),
            (
                (DailyNetSales(START, Decimal("NaN"), "SYNTHETIC_TEST"),),
                exact_coverage,
                (),
            ),
            (rows, replace(exact_coverage, authority_sha256="not-a-sha"), ()),
            (
                rows,
                exact_coverage,
                (snapshot(START, captured_at=datetime(2026, 8, 1, 21, 0)),),
            ),
            (rows, exact_coverage, (snapshot(START, source_hash="bad"),)),
            (
                rows,
                exact_coverage,
                (snapshot(START, validation_status="INCOMPLETE"),),
            ),
            (rows, exact_coverage, (snapshot(START, available="-1"),)),
            (rows, exact_coverage, (snapshot(START, incoming="-1"),)),
            (
                rows,
                exact_coverage,
                (replace(snapshot(START), incoming_quantity=None),),
            ),
            (rows, exact_coverage, (snapshot(START, available="0.00001"),)),
            (
                rows,
                exact_coverage,
                (
                    snapshot(START),
                    snapshot(
                        START,
                        location="gid://shopify/Location/2",
                        source_hash="b" * 64,
                    ),
                ),
            ),
        )
        for bad_rows, bad_coverage, bad_snapshots in invalid_cases:
            with self.subTest(
                rows=bad_rows, coverage=bad_coverage, snapshots=bad_snapshots
            ):
                with self.assertRaises(ValueError):
                    build_demand_evidence(
                        history_start=START,
                        history_end=START,
                        sales_rows=bad_rows,
                        sales_coverage=bad_coverage,
                        snapshot_rows=bad_snapshots,
                    )

        two_day_rows = sales_rows([1, 1])
        with self.assertRaisesRegex(ValueError, "conflicting provenance"):
            build_demand_evidence(
                history_start=START,
                history_end=START + timedelta(days=1),
                sales_rows=two_day_rows,
                sales_coverage=coverage(two_day_rows),
                snapshot_rows=(
                    snapshot(START),
                    snapshot(START + timedelta(days=1)),
                ),
            )

    def test_coherent_locations_aggregate_with_capture_and_completion_kept_distinct(self):
        rows = sales_rows([1])
        captured = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 8, 1, 20, 7, tzinfo=timezone.utc)
        evidence = build_demand_evidence(
            history_start=START,
            history_end=START,
            sales_rows=rows,
            sales_coverage=coverage(rows),
            snapshot_rows=(
                snapshot(
                    START,
                    location="gid://shopify/Location/2",
                    captured_at=captured,
                    completed_at=completed,
                    available="3",
                    incoming="2",
                ),
                snapshot(
                    START,
                    location="gid://shopify/Location/1",
                    captured_at=captured,
                    completed_at=completed,
                    available="2",
                    incoming="1",
                ),
            ),
        )
        self.assertEqual(len(evidence.snapshot_groups), 1)
        group = evidence.snapshot_groups[0]
        self.assertEqual(
            group.locations,
            ("gid://shopify/Location/1", "gid://shopify/Location/2"),
        )
        self.assertEqual(group.compatibility_status, "COMPATIBLE_POINT_IN_TIME_EVIDENCE")
        self.assertEqual(group.aggregate_available_quantity, Decimal("5.0000"))
        self.assertEqual(group.aggregate_incoming_quantity, Decimal("3.0000"))
        frozen = evidence.to_json_dict()
        frozen_group = frozen["snapshot_groups"][0]
        self.assertEqual(frozen_group["captured_at"], captured.isoformat())
        self.assertEqual(frozen_group["completed_at"], completed.isoformat())
        self.assertNotEqual(frozen_group["captured_at"], frozen_group["completed_at"])
        self.assertEqual(
            [ref["location_gid"] for ref in frozen["daily"][0]["snapshot_refs"]],
            ["gid://shopify/Location/1", "gid://shopify/Location/2"],
        )

    def test_incompatible_groups_never_aggregate_and_serialization_is_canonical(self):
        rows = sales_rows([2])
        first = snapshot(START, available="1")
        second = snapshot(
            START,
            location="gid://shopify/Location/2",
            run_id="00000000-0000-4000-8000-000000000002",
            source_hash="b" * 64,
            captured_at=datetime(2026, 8, 1, 22, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 1, 22, 5, tzinfo=timezone.utc),
            available="4",
        )
        evidence = build_demand_evidence(
            history_start=START,
            history_end=START,
            sales_rows=rows,
            sales_coverage=coverage(rows),
            snapshot_rows=(second, first),
        )
        reordered = build_demand_evidence(
            history_start=START,
            history_end=START,
            sales_rows=tuple(reversed(rows)),
            sales_coverage=coverage(rows),
            snapshot_rows=(first, second),
        )
        self.assertEqual(
            {group.compatibility_status for group in evidence.snapshot_groups},
            {"INCOMPATIBLE_POINT_IN_TIME_EVIDENCE"},
        )
        self.assertTrue(
            all(group.aggregate_available_quantity is None for group in evidence.snapshot_groups)
        )
        self.assertTrue(
            all(group.aggregate_incoming_quantity is None for group in evidence.snapshot_groups)
        )
        self.assertEqual(
            evidence.daily[0].state_basis,
            "INCOMPATIBLE_POINT_IN_TIME_EVIDENCE",
        )
        self.assertEqual(
            evidence.reason_codes,
            (
                "POINT_IN_TIME_INVENTORY_NOT_FULL_DAY_AVAILABILITY",
                "INCOMPATIBLE_POINT_IN_TIME_EVIDENCE",
                "STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE",
            ),
        )
        frozen = evidence.to_json_dict()
        self.assertEqual(
            list(frozen),
            [
                "contract",
                "method_version",
                "history_start",
                "history_end",
                "calendar_days",
                "sales_coverage",
                "daily",
                "snapshot_groups",
                "raw_windows",
                "availability_summary",
                "statuses",
                "reason_codes",
            ],
        )
        self.assertEqual(frozen["contract"], "BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2")
        self.assertEqual(list(frozen["raw_windows"]), ["7", "14", "28"])
        self.assertEqual(frozen, reordered.to_json_dict())
        self.assertEqual(
            canonical_evidence_sha256(frozen),
            canonical_evidence_sha256(reordered.to_json_dict()),
        )
        self.assertEqual(
            UUID(frozen["daily"][0]["snapshot_refs"][0]["inventory_snapshot_run_id"]),
            UUID("00000000-0000-4000-8000-000000000001"),
        )
