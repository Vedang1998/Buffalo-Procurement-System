"""Deterministic tests for the additive coherent H3/H10/H17 planner."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_DOWN, localcontext
import random
import unittest
from unittest.mock import patch

import procurement_os.development_forecast_joint_horizon as joint
from procurement_os.development_forecast import (
    V2_CONTRACT,
    _InapplicableModel,
    load_development_forecast_policy,
    plan_development_forecast,
)
from procurement_os.development_forecast_joint_horizon import (
    JointHorizonForecastError,
    _coherent_prefix_cap,
    _fva_decision,
    _joint_confidence,
    _publish_paths,
    joint_horizon_policy_descriptor,
    plan_joint_horizon_forecast,
    validate_joint_horizon_forecast_evidence,
)
from procurement_os.forecasting import DemandObservation


_HASH = "a" * 64


def observations(values: list[object]) -> list[DemandObservation]:
    start = date(2026, 5, 4)
    return [
        DemandObservation(start + timedelta(days=index), Decimal(str(value)), "UNKNOWN")
        for index, value in enumerate(values)
    ]


def observations_with_stockouts(
    values: list[object], stockout_indices: set[int]
) -> list[DemandObservation]:
    start = date(2026, 5, 4)
    return [
        DemandObservation(
            start + timedelta(days=index),
            Decimal(str(value)),
            "STOCKOUT" if index in stockout_indices else "UNKNOWN",
        )
        for index, value in enumerate(values)
    ]


def descriptor() -> dict[str, object]:
    return {
        "contract": "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3",
        "projection_sha256": "b" * 64,
        "artifact_sha256": "c" * 64,
        "artifact_path": "private-research/workspaces/" + "d" * 64 + "/projection.json",
        "workspace_id": "d" * 64,
        "workspace_manifest_sha256": "e" * 64,
        "sidecars_sha256": "f" * 64,
    }


def delta_descriptor() -> dict[str, str]:
    return {
        "contract": "BUFFALO_PRIVATE_VARIANT_CREATION_EVIDENCE_DELTA_V1",
        "delta_id": "1" * 64,
        "raw_csv_sha256": "2" * 64,
        "normalized_row_set_sha256": "3" * 64,
        "raw_storage_key": "private-research/corrected-v3-sources/sha256/"
        + "2" * 64
        + ".csv",
        "envelope_storage_key": "private-research/corrected-v3-deltas/"
        + "1" * 64
        + ".json",
    }


class DevelopmentForecastJointHorizonTests(unittest.TestCase):
    def _plan(self, values: list[object]) -> dict[str, object]:
        return plan_joint_horizon_forecast(
            observations(values),
            variant_id="100",
            corrected_input_id=_HASH,
            parent_projection=descriptor(),
            creation_evidence_delta=delta_descriptor(),
            observations_sha256="4" * 64,
        )

    def test_prefix_cap_is_not_the_legacy_whole_path_scaler(self) -> None:
        cumulative, daily, hits = _coherent_prefix_cap(
            [Decimal("20")] + [Decimal("0")] * 16,
            Decimal("2"),
        )
        self.assertEqual(
            cumulative,
            [Decimal(index * 2) for index in range(1, 11)]
            + [Decimal("20")] * 7,
        )
        self.assertEqual(daily, [Decimal("2")] * 10 + [Decimal("0")] * 7)
        self.assertEqual(hits, list(range(1, 10)))

    def test_fva_gate_uses_full_precision_and_inclusive_two_percent(self) -> None:
        fail = _fva_decision(
            Decimal("100"),
            [("DAMPED_ETS", Decimal("98.00000001"))],
        )
        at = _fva_decision(
            Decimal("100"), [("DAMPED_ETS", Decimal("98"))]
        )
        above = _fva_decision(
            Decimal("100"),
            [("DAMPED_ETS", Decimal("97.99999999"))],
        )
        self.assertFalse(fail["complex_cleared_gate"])
        self.assertEqual(fail["selected_model"], "NAIVE")
        self.assertTrue(at["complex_cleared_gate"])
        self.assertTrue(above["complex_cleared_gate"])
        self.assertEqual(fail["relative_improvement"], "0.020000")
        self.assertEqual(at["relative_improvement"], "0.020000")

    def test_confidence_thresholds_are_exact_and_unknown_forces_low(self) -> None:
        self.assertEqual(_joint_confidence(Decimal("20"), Decimal("100"), False)[0], "HIGH")
        self.assertEqual(
            _joint_confidence(Decimal("20.00000001"), Decimal("100"), False)[0],
            "MEDIUM",
        )
        self.assertEqual(_joint_confidence(Decimal("50"), Decimal("100"), False)[0], "MEDIUM")
        self.assertEqual(
            _joint_confidence(Decimal("50.00000001"), Decimal("100"), False)[0],
            "LOW",
        )
        confidence, reasons = _joint_confidence(
            Decimal("1"), Decimal("100"), True
        )
        self.assertEqual(confidence, "LOW")
        self.assertIn("UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK", reasons)

    def test_publication_preserves_prefix_and_target_identity(self) -> None:
        point, target, protection, daily = _publish_paths(
            [Decimal("0.00014"), Decimal("0.00016")],
            [Decimal("0.00025"), Decimal("0.00034")],
        )
        self.assertEqual(point, ["0.0001", "0.0002"])
        self.assertEqual(target, ["0.0003", "0.0003"])
        self.assertEqual(protection, ["0.0002", "0.0001"])
        self.assertEqual(daily, ["0.0001", "0.0001"])

    def test_constant_series_builds_one_coherent_replayable_sidecar(self) -> None:
        value = plan_joint_horizon_forecast(
            observations([2] * 138),
            variant_id="100",
            corrected_input_id=_HASH,
            parent_projection=descriptor(),
            creation_evidence_delta=delta_descriptor(),
            observations_sha256="4" * 64,
        )
        validated = validate_joint_horizon_forecast_evidence(
            value,
            observations([2] * 138),
            expected_variant_id="100",
            expected_corrected_input_id=_HASH,
            expected_parent_projection=descriptor(),
            expected_creation_evidence_delta=delta_descriptor(),
            expected_observations_sha256="4" * 64,
        )
        self.assertEqual(validated, value)
        self.assertEqual(value["selected_model"], "NAIVE")
        summaries = value["summaries"]
        self.assertEqual(
            [summaries[key]["point_forecast_units"] for key in ("H3", "H10", "H17")],
            ["6.0000", "20.0000", "34.0000"],
        )
        self.assertEqual(len(value["final_path"]["published_cumulative"]), 17)
        self.assertEqual(len(value["candidate_records"]), 5)

    def test_replay_rejects_semantic_tamper_even_when_outer_hash_is_resealed(self) -> None:
        source = plan_joint_horizon_forecast(
            observations([0, 1] * 69),
            variant_id="100",
            corrected_input_id=_HASH,
            parent_projection=descriptor(),
            creation_evidence_delta=delta_descriptor(),
            observations_sha256="4" * 64,
        )
        forged = deepcopy(source)
        forged["summaries"]["H3"]["point_forecast_units"] = "999.0000"
        forged.pop("sidecar_sha256")
        from procurement_os.development_forecast_joint_horizon import _logical_sha

        forged["sidecar_sha256"] = _logical_sha(forged)
        with self.assertRaisesRegex(
            JointHorizonForecastError, "semantic replay differs"
        ):
            validate_joint_horizon_forecast_evidence(
                forged,
                observations([0, 1] * 69),
                expected_variant_id="100",
                expected_corrected_input_id=_HASH,
                expected_parent_projection=descriptor(),
                expected_creation_evidence_delta=delta_descriptor(),
                expected_observations_sha256="4" * 64,
            )

    def test_hostile_ambient_decimal_context_cannot_change_evidence(self) -> None:
        args = dict(
            variant_id="100",
            corrected_input_id=_HASH,
            parent_projection=descriptor(),
            creation_evidence_delta=delta_descriptor(),
            observations_sha256="4" * 64,
        )
        baseline = plan_joint_horizon_forecast(observations([1, 0, 2] * 46), **args)
        with localcontext(Context(prec=6, rounding=ROUND_DOWN)):
            hostile = plan_joint_horizon_forecast(observations([1, 0, 2] * 46), **args)
        self.assertEqual(hostile, baseline)

    def test_policy_descriptor_is_registered_and_zero_authority(self) -> None:
        value = joint_horizon_policy_descriptor()
        self.assertEqual(
            value["policy_contract"],
            "BUFFALO_DEVELOPMENT_FORECAST_JOINT_HORIZON_POLICY_V1",
        )
        self.assertFalse(value["commercial_authority"])
        self.assertFalse(value["production_activation"])

    def test_common_h17_origin_partitions_are_exact_for_every_candidate(self) -> None:
        value = self._plan([2] * 138)
        partitions = value["origin_partitions"]
        self.assertEqual(
            (len(partitions["selection"]["usable_dates"]), partitions["selection"]["usable_dates"][0], partitions["selection"]["usable_dates"][-1]),
            (22, "2026-06-01", "2026-06-22"),
        )
        self.assertEqual(
            (len(partitions["calibration"]["usable_dates"]), partitions["calibration"]["usable_dates"][0], partitions["calibration"]["usable_dates"][-1]),
            (22, "2026-07-09", "2026-07-30"),
        )
        self.assertEqual(
            (len(partitions["evaluation"]["usable_dates"]), partitions["evaluation"]["usable_dates"][0], partitions["evaluation"]["usable_dates"][-1]),
            (18, "2026-08-16", "2026-09-02"),
        )
        common = partitions["selection"]["usable_dates"]
        for record in value["candidate_records"]:
            if record["status"] == "ELIGIBLE":
                self.assertEqual(record["selection_origin_dates"], common)
            else:
                self.assertIsNone(record["selection_origin_dates"])

    def test_registered_candidate_families_win_and_category_prior_is_not_invented(self) -> None:
        fixtures = {
            "NAIVE": [2] * 138,
            "SEASONAL_NAIVE": [7 if index % 7 in {4, 5} else 0 for index in range(138)],
            "DAMPED_ETS": [
                max(Decimal("0"), Decimal(138 - index) / Decimal("10"))
                for index in range(138)
            ],
            "TSB": [
                (1 if index % 2 == 0 else 0)
                if index < 28
                else (5 if index % 2 == 0 else 0)
                if index < 66
                else 0
                for index in range(138)
            ],
        }
        for expected, values in fixtures.items():
            with self.subTest(model=expected):
                value = self._plan(values)
                self.assertEqual(value["selected_model"], expected)
                category = next(
                    item
                    for item in value["candidate_records"]
                    if item["model"] == "CATEGORY_SHRINKAGE"
                )
                self.assertEqual(category["status"], "INELIGIBLE")
                self.assertEqual(
                    category["reason_codes"],
                    ["JOINT_CANDIDATE_INELIGIBLE_AT_COMMON_SELECTION_ORIGIN"],
                )

    def test_old_independent_horizons_can_contradict_while_joint_path_cannot(self) -> None:
        # Deterministic synthetic sparse series discovered specifically to lock
        # the root cause: independent horizon selection chooses different paths.
        generator = random.Random(125)
        values = [
            generator.randrange(20) if generator.random() < 0.2 else 0
            for _ in range(138)
        ]
        policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        independent = [
            plan_development_forecast(
                observations(values), horizon_days=horizon, policy=policy
            ).point_forecast_units
            for horizon in (3, 10, 17)
        ]
        # The exact generator is stable; its purpose is to prove the legacy
        # calls are not structurally constrained, not to prescribe their winner.
        self.assertTrue(
            independent[1] < independent[0] or independent[2] < independent[1],
            independent,
        )
        summaries = self._plan(values)["summaries"]
        joint_points = [
            Decimal(summaries[key]["point_forecast_units"])
            for key in ("H3", "H10", "H17")
        ]
        joint_targets = [
            Decimal(summaries[key]["target_units"])
            for key in ("H3", "H10", "H17")
        ]
        self.assertEqual(joint_points, sorted(joint_points))
        self.assertEqual(joint_targets, sorted(joint_targets))

    def test_candidate_missing_one_common_origin_is_wholly_ineligible(self) -> None:
        original = joint._predict

        def fail_one(model, history, horizon, policy, category):
            if model == "SEASONAL_NAIVE" and len(history) == 35:
                raise _InapplicableModel("synthetic common-origin refusal")
            return original(model, history, horizon, policy, category)

        with patch.object(joint, "_predict", side_effect=fail_one):
            value = self._plan([2] * 138)
        seasonal = next(
            item for item in value["candidate_records"]
            if item["model"] == "SEASONAL_NAIVE"
        )
        self.assertEqual(seasonal["status"], "INELIGIBLE")
        for field in (
            "selection_origin_dates", "origin_paths", "segments", "joint_score"
        ):
            self.assertIsNone(seasonal[field])

    def test_selected_model_failure_blocks_calibration_evaluation_and_final(self) -> None:
        original = joint._predict
        for failing_history in (66, 104, 138):
            with self.subTest(history_length=failing_history):
                def fail_selected(model, history, horizon, policy, category):
                    if model == "NAIVE" and len(history) == failing_history:
                        raise _InapplicableModel("synthetic selected-path refusal")
                    return original(model, history, horizon, policy, category)

                with patch.object(joint, "_predict", side_effect=fail_selected):
                    with self.assertRaises(JointHorizonForecastError) as raised:
                        self._plan([2] * 138)
                self.assertEqual(
                    raised.exception.reason_code,
                    "JOINT_SELECTED_MODEL_PATH_UNAVAILABLE",
                )

    def test_zero_and_intermittent_demand_remain_nonnegative_and_replayable(self) -> None:
        for values in (
            [0] * 138,
            [5 if index % 19 == 0 else 0 for index in range(138)],
        ):
            with self.subTest(nonzero=sum(bool(item) for item in values)):
                value = self._plan(values)
                self.assertTrue(
                    all(
                        Decimal(item) >= 0
                        for item in value["final_path"]["published_daily"]
                    )
                )
                self.assertEqual(
                    validate_joint_horizon_forecast_evidence(
                        value,
                        observations(values),
                        expected_variant_id="100",
                        expected_corrected_input_id=_HASH,
                        expected_parent_projection=descriptor(),
                        expected_creation_evidence_delta=delta_descriptor(),
                        expected_observations_sha256="4" * 64,
                    ),
                    value,
                )
        zero = self._plan([0] * 138)
        self.assertIn(
            "JOINT_EVALUATION_ACTUAL_SCALE_ZERO",
            zero["evaluation"]["reason_codes"],
        )

    def test_late_history_change_cannot_leak_into_selection_or_calibration(self) -> None:
        baseline_values = [2] * 138
        changed_values = list(baseline_values)
        changed_values[-1] = 200
        baseline = self._plan(baseline_values)
        changed = self._plan(changed_values)
        self.assertEqual(baseline["candidate_records"], changed["candidate_records"])
        self.assertEqual(baseline["origin_partitions"], changed["origin_partitions"])
        for key in (
            "origin_dates",
            "cap_hit_days_by_origin",
            "daily_shortfall_paths",
            "cumulative_daily_shortfall_paths",
        ):
            self.assertEqual(baseline["calibration"][key], changed["calibration"][key])
        self.assertNotEqual(baseline["final_path"], changed["final_path"])

    def test_planner_refuses_partial_descriptors_and_validator_refuses_type_drift(self) -> None:
        with self.assertRaisesRegex(
            JointHorizonForecastError, "parent projection descriptor"
        ):
            plan_joint_horizon_forecast(
                observations([2] * 138),
                variant_id="100",
                corrected_input_id=_HASH,
                parent_projection={},
                creation_evidence_delta=delta_descriptor(),
                observations_sha256="4" * 64,
            )
        value = self._plan([2] * 138)
        forged = deepcopy(value)
        forged["zero_authority"]["database_writes"] = False
        forged["sidecar_sha256"] = joint._logical_sha(forged)
        with self.assertRaises(JointHorizonForecastError):
            validate_joint_horizon_forecast_evidence(
                forged,
                observations([2] * 138),
                expected_variant_id="100",
                expected_corrected_input_id=_HASH,
                expected_parent_projection=descriptor(),
                expected_creation_evidence_delta=delta_descriptor(),
                expected_observations_sha256="4" * 64,
            )

    def test_q90_and_held_out_metrics_have_exact_known_answers(self) -> None:
        value = self._plan([2] * 138)
        calibration = value["calibration"]
        self.assertEqual(calibration["nearest_rank"], 20)
        self.assertTrue(
            all(
                item == "0"
                for path in calibration["daily_shortfall_paths"]
                for item in path
            )
        )
        self.assertEqual(
            calibration["target_sample_paths"][0][:4], ["2", "4", "6", "8"]
        )
        self.assertEqual(
            calibration["internal_target_path"][:4], ["2", "4", "6", "8"]
        )
        self.assertEqual(
            value["evaluation"]["target_metrics"]["H17"],
            {
                "shortfall_sum": "0",
                "mean_shortfall": "0.000000",
                "covered_count": 18,
                "total_count": 18,
                "service_coverage": "1.000000",
            },
        )

        actual_daily = [Decimal("1"), Decimal("0"), Decimal("0")]
        actual_daily += [Decimal("100")] + [Decimal("0")] * 6
        actual_daily += [Decimal("100")] + [Decimal("0")] * 6
        point_daily = [Decimal("2"), Decimal("0"), Decimal("0")]
        point_daily += [Decimal("100")] + [Decimal("0")] * 6
        point_daily += [Decimal("100")] + [Decimal("0")] * 6
        point_cumulative = joint._cumulative(point_daily)
        ordered = observations(actual_daily)
        evidence = joint._evaluation_evidence(
            [0],
            [
                (
                    list(point_daily),
                    point_cumulative,
                    Decimal("1000"),
                    [],
                    point_daily,
                )
            ],
            ordered,
            actual_daily,
            [[Decimal("0")] * 17],
            False,
        )
        self.assertEqual(evidence["point_metrics"]["H3"]["wape"], "1.000000")
        self.assertEqual(
            evidence["point_metrics"]["H3"],
            {
                "signed_error_sum": "1",
                "absolute_error_sum": "1",
                "actual_absolute_sum": "1",
                "bias": "1.000000",
                "mae": "1.000000",
                "wape": "1.000000",
            },
        )
        self.assertEqual(
            evidence["point_metrics"]["H10"],
            {
                "signed_error_sum": "1",
                "absolute_error_sum": "1",
                "actual_absolute_sum": "101",
                "bias": "1.000000",
                "mae": "1.000000",
                "wape": "0.009901",
            },
        )
        self.assertEqual(
            evidence["point_metrics"]["H17"],
            {
                "signed_error_sum": "1",
                "absolute_error_sum": "1",
                "actual_absolute_sum": "201",
                "bias": "1.000000",
                "mae": "1.000000",
                "wape": "0.004975",
            },
        )
        self.assertEqual(evidence["point_metrics"]["daily_mae"], "0.058824")
        self.assertEqual(
            evidence["point_metrics"]["maximum_prefix_absolute_error_mean"],
            "1.000000",
        )
        self.assertEqual(evidence["joint_objective"]["wape"], "0.004975")
        self.assertEqual(evidence["joint_confidence"], "HIGH")

        zero_path = [Decimal("0")] * 17
        zero_evidence = joint._evaluation_evidence(
            [0],
            [(zero_path, zero_path, Decimal("0"), [], zero_path)],
            observations([0] * 17),
            zero_path,
            [zero_path],
            False,
        )
        for label in ("H3", "H10", "H17"):
            self.assertIsNone(zero_evidence["point_metrics"][label]["wape"])
            self.assertEqual(zero_evidence["point_metrics"][label]["bias"], "0.000000")
            self.assertEqual(zero_evidence["point_metrics"][label]["mae"], "0.000000")
        self.assertIsNone(zero_evidence["joint_objective"]["wape"])
        self.assertEqual(zero_evidence["joint_confidence"], "LOW")
        self.assertEqual(
            zero_evidence["reason_codes"],
            ["JOINT_EVALUATION_ACTUAL_SCALE_ZERO"],
        )

        under_point_daily = [Decimal("1")] + [Decimal("0")] * 16
        under_point_cumulative = joint._cumulative(under_point_daily)
        under_actual = [Decimal("3")] + [Decimal("0")] * 16
        under_evidence = joint._evaluation_evidence(
            [0],
            [
                (
                    list(under_point_daily),
                    under_point_cumulative,
                    Decimal("10"),
                    [],
                    under_point_daily,
                )
            ],
            observations(under_actual),
            under_actual,
            [[Decimal("1")] * 17],
            False,
        )
        for label in ("H3", "H10", "H17"):
            self.assertEqual(
                under_evidence["target_metrics"][label],
                {
                    "shortfall_sum": "1",
                    "mean_shortfall": "1.000000",
                    "covered_count": 0,
                    "total_count": 1,
                    "service_coverage": "0.000000",
                },
            )
        self.assertEqual(
            under_evidence["target_metrics"]["maximum_prefix_shortfall_mean"],
            "1.000000",
        )

        block_values: list[Decimal] = []
        origins = []
        for index in range(22):
            origins.append(len(block_values))
            block_values.extend([Decimal(index)] + [Decimal("0")] * 16)
        zero_origin_path = (
            [Decimal("0")] * 17,
            [Decimal("0")] * 17,
            Decimal("100"),
            [],
            [Decimal("0")] * 17,
        )
        calibrated, shortfalls, target = joint._calibration_evidence(
            origins,
            [zero_origin_path for _ in origins],
            observations(block_values),
            block_values,
            [Decimal("0")] * 17,
        )
        self.assertEqual(calibrated["nearest_rank"], 20)
        self.assertEqual(calibrated["daily_shortfall_paths"][0], ["0"] * 17)
        self.assertEqual(
            calibrated["daily_shortfall_paths"][-1],
            ["21"] + ["0"] * 16,
        )
        self.assertEqual(calibrated["cumulative_daily_shortfall_paths"][-1], ["21"] * 17)
        self.assertEqual(calibrated["internal_target_path"], ["19"] * 17)
        self.assertEqual(shortfalls[-1], [Decimal("21")] * 17)
        self.assertEqual(target, [Decimal("19")] * 17)

    def test_fva_ties_zero_baseline_and_no_complex_cases_are_explicit(self) -> None:
        tie = _fva_decision(
            Decimal("100"),
            [("SEASONAL_NAIVE", Decimal("98")), ("DAMPED_ETS", Decimal("98"))],
        )
        self.assertEqual(tie["selected_model"], "SEASONAL_NAIVE")
        self.assertTrue(tie["complex_cleared_gate"])
        zero = _fva_decision(
            Decimal("0"), [("DAMPED_ETS", Decimal("0"))]
        )
        self.assertEqual(zero["selected_model"], "NAIVE")
        self.assertEqual(zero["reason"], "SIMPLE_BASELINE_SELECTED_ZERO_ERROR")
        absent = _fva_decision(Decimal("5"), [])
        self.assertEqual(absent["best_complex_model"], None)
        self.assertEqual(absent["reason"], "NO_ELIGIBLE_COMPLEX_CANDIDATE")

    def test_minimum_common_origin_refusals_are_stage_specific(self) -> None:
        cases = (
            ({44}, "JOINT_SELECTION_ORIGINS_INSUFFICIENT"),
            ({82}, "JOINT_CALIBRATION_ORIGINS_INSUFFICIENT"),
            ({120}, "JOINT_EVALUATION_ORIGINS_INSUFFICIENT"),
        )
        for indices, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(JointHorizonForecastError) as raised:
                    plan_joint_horizon_forecast(
                        observations_with_stockouts([2] * 138, indices),
                        variant_id="100",
                        corrected_input_id=_HASH,
                        parent_projection=descriptor(),
                        creation_evidence_delta=delta_descriptor(),
                        observations_sha256="4" * 64,
                    )
                self.assertEqual(raised.exception.reason_code, reason)

    def test_cap_evidence_is_recorded_at_every_stage_but_summary_is_final_only(self) -> None:
        original = joint._predict

        def capped(model, history, horizon, policy, category):
            if model != "CATEGORY_SHRINKAGE":
                return [Decimal("20")] + [Decimal("0")] * 16
            return original(model, history, horizon, policy, category)

        with patch.object(joint, "_predict", side_effect=capped):
            value = self._plan([1] * 138)
        selected = next(
            record for record in value["candidate_records"] if record["selected"]
        )
        expected_hits = list(range(1, 16))
        expected_daily = ["1.25"] * 16 + ["0"]
        expected_cumulative = [
            "1.25", "2.5", "3.75", "5", "6.25", "7.5", "8.75", "10",
            "11.25", "12.5", "13.75", "15", "16.25", "17.5", "18.75",
            "20", "20",
        ]
        first_selection = selected["origin_paths"][0]
        self.assertEqual(first_selection["calendar_mean_cap"], "1.25")
        self.assertEqual(first_selection["cap_hit_days"], expected_hits)
        self.assertEqual(first_selection["forecast_daily"], expected_daily)
        self.assertEqual(first_selection["forecast_cumulative"], expected_cumulative)
        self.assertTrue(
            all(
                hits == expected_hits
                for hits in value["calibration"]["cap_hit_days_by_origin"].values()
            )
        )
        self.assertTrue(
            all(
                hits == expected_hits
                for hits in value["evaluation"]["cap_hit_days_by_origin"].values()
            )
        )
        self.assertEqual(value["final_path"]["calendar_mean_cap"], "1.25")
        self.assertEqual(value["final_path"]["cap_hit_days"], expected_hits)
        self.assertEqual(value["final_path"]["raw_daily"], ["20"] + ["0"] * 16)
        self.assertEqual(value["final_path"]["internal_capped_daily"], expected_daily)
        self.assertEqual(value["final_path"]["internal_cumulative"], expected_cumulative)
        self.assertIn(
            "DEVELOPMENT_RATE_CAP_APPLIED",
            value["summaries"]["H3"]["reason_codes"],
        )

        def historical_only(model, history, horizon, policy, category):
            if model != "CATEGORY_SHRINKAGE" and len(history) < 138:
                return [Decimal("20")] + [Decimal("0")] * 16
            if model != "CATEGORY_SHRINKAGE":
                return [Decimal("0")] * 17
            return original(model, history, horizon, policy, category)

        with patch.object(joint, "_predict", side_effect=historical_only):
            historical = self._plan([1] * 138)
        self.assertFalse(historical["final_path"]["cap_hit_days"])
        self.assertNotIn(
            "DEVELOPMENT_RATE_CAP_APPLIED",
            historical["summaries"]["H3"]["reason_codes"],
        )

    def test_malformed_candidate_and_identity_types_fail_closed(self) -> None:
        original = joint._predict

        def malformed(model, history, horizon, policy, category):
            if model == "SEASONAL_NAIVE":
                return [Decimal("1")] * 16
            return original(model, history, horizon, policy, category)

        with patch.object(joint, "_predict", side_effect=malformed):
            value = self._plan([2] * 138)
        seasonal = next(
            record
            for record in value["candidate_records"]
            if record["model"] == "SEASONAL_NAIVE"
        )
        self.assertEqual(seasonal["status"], "INELIGIBLE")

        def unexpected(*_args, **_kwargs):
            raise RuntimeError("unexpected synthetic defect")

        with patch.object(joint, "_predict", side_effect=unexpected):
            with self.assertRaisesRegex(RuntimeError, "unexpected synthetic defect"):
                self._plan([2] * 138)
        for field, bad in (
            ("variant_id", 100),
            ("corrected_input_id", 1),
            ("observations_sha256", False),
        ):
            arguments = {
                "variant_id": "100",
                "corrected_input_id": _HASH,
                "parent_projection": descriptor(),
                "creation_evidence_delta": delta_descriptor(),
                "observations_sha256": "4" * 64,
            }
            arguments[field] = bad
            with self.subTest(field=field):
                with self.assertRaises(JointHorizonForecastError):
                    plan_joint_horizon_forecast(observations([2] * 138), **arguments)

    def test_second_legacy_contradiction_has_h17_below_h10(self) -> None:
        generator = random.Random(29)
        values = [
            (10 if index % 7 == generator.randrange(7) else 0)
            + generator.randrange(3)
            for index in range(138)
        ]
        policy = load_development_forecast_policy(evidence_contract=V2_CONTRACT)
        independent = [
            plan_development_forecast(
                observations(values), horizon_days=horizon, policy=policy
            ).point_forecast_units
            for horizon in (3, 10, 17)
        ]
        self.assertLess(independent[2], independent[1])
        summaries = self._plan(values)["summaries"]
        self.assertLessEqual(
            Decimal(summaries["H10"]["point_forecast_units"]),
            Decimal(summaries["H17"]["point_forecast_units"]),
        )


if __name__ == "__main__":
    unittest.main()
