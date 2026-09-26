"""Synthetic acceptance tests for the additive corrected-V3 contracts."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import csv
from decimal import Context, Decimal, ROUND_DOWN, localcontext
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import test_private_research_v3 as legacy_v3_tests

from procurement_os.private_research_projection import (
    canonical_private_research_projection_bytes,
    filter_private_research_rows,
    render_private_research_csv,
    render_private_research_html,
)
import procurement_os.development_forecast_joint_horizon as joint
import procurement_os.private_research_projection as projection_module
import procurement_os.private_research_v3_corrected as corrected


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class PrivateResearchV3CorrectedTests(unittest.TestCase):
    @staticmethod
    def _raw_for_rows(
        rows: list[dict[str, str]],
        *,
        fieldnames: list[str] | None = None,
    ) -> bytes:
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(
            stream,
            fieldnames=fieldnames or corrected._DELTA_FIELDS,
            lineterminator="\r\n",
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
        return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")

    @staticmethod
    def _write_private(root: Path, key: str, data: bytes) -> Path:
        path = root.joinpath(*key.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        ancestor = path.parent
        while True:
            os.chmod(ancestor, 0o700)
            if ancestor == root:
                break
            ancestor = ancestor.parent
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return path

    @contextmanager
    def _built(self):
        helper = legacy_v3_tests.PrivateResearchV3Tests(
            methodName="test_exact_id_preexistence_expands_only_supported_population"
        )
        helper.setUp()
        try:
            with helper._built() as built:
                value = built["value"]
                parent = built["parent"]
                base = built["base"]
                projection = built["projection"]
                blocked = next(
                    row
                    for row in value["eligibility_ledger"]
                    if row["blocker_reason"]
                    == corrected._PRIOR_ABSENT_REASON
                )
                variant_id = blocked["shopify_variant_id"]
                owner = next(
                    row
                    for row in projection["owner_worksheet"]
                    if row["shopify_variant_id"] == variant_id
                )
                stream = io.StringIO(newline="")
                writer = csv.DictWriter(
                    stream,
                    fieldnames=corrected._DELTA_FIELDS,
                    lineterminator="\r\n",
                    quoting=csv.QUOTE_MINIMAL,
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "shopify_variant_id": variant_id,
                        "product_title": owner["product_title"],
                        "variant_title": owner["variant_title"],
                        "original_primary_status": "BLOCKED",
                        "original_reason": corrected._PRIOR_ABSENT_REASON,
                        "live_exact_id_created_at": "2026-05-06T12:00:00Z",
                        "live_product_status": "ACTIVE",
                        "history_start_date": "2026-05-04",
                        "reviewed_primary_status": "NOT_APPLICABLE",
                        "reviewed_reason": corrected._POST_START_REASON,
                        "forecast_recalculation_required_for_138_day_full_window": "NO",
                        "owner_review_required": "NO",
                    }
                )
                raw = b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")
                normalized = [{
                    "shopify_variant_id": variant_id,
                    "product_title": owner["product_title"],
                    "variant_title": owner["variant_title"],
                    "original_primary_status": "BLOCKED",
                    "original_reason": corrected._PRIOR_ABSENT_REASON,
                    "live_exact_id_created_at": "2026-05-06T12:00:00Z",
                    "live_product_status": "ACTIVE",
                    "history_start_date": "2026-05-04",
                    "reviewed_primary_status": "NOT_APPLICABLE",
                    "reviewed_reason": corrected._POST_START_REASON,
                    "forecast_recalculation_required_for_138_day_full_window": "NO",
                    "owner_review_required": "NO",
                }]
                current = [row["shopify_variant_id"] for row in value["eligibility_ledger"]]
                eligible = [
                    row["shopify_variant_id"]
                    for row in value["eligibility_ledger"]
                    if row["status"] == "ELIGIBLE"
                ]
                parent_na = [
                    row["shopify_variant_id"]
                    for row in value["eligibility_ledger"]
                    if row["blocker_reason"]
                    in {corrected._POST_START_REASON, corrected._FIRST_DAY_REASON}
                ]
                corrected_na = sorted([*parent_na, variant_id], key=int)
                raw_violations = corrected._raw_violations(projection["owner_worksheet"])
                projection_bytes = canonical_private_research_projection_bytes(projection)
                source_identity = projection["forecast_research"]["source_identity"]
                source_identity_bytes = (
                    json.dumps(source_identity, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
                patches = {
                    "DELTA_RAW_BYTES": len(raw),
                    "DELTA_RAW_SHA256": sha(raw),
                    "DELTA_ROW_SET_SHA256": sha(corrected.canonical_json_bytes(normalized)),
                    "DELTA_ROW_COUNT": 1,
                    "CURRENT_VARIANT_COUNT": len(current),
                    "ELIGIBLE_VARIANT_COUNT": len(eligible),
                    "PARENT_NOT_APPLICABLE_COUNT": len(parent_na),
                    "CORRECTED_NOT_APPLICABLE_COUNT": len(corrected_na),
                    "CURRENT_MEMBERSHIP_SHA256": corrected._membership_sha(current),
                    "ELIGIBLE_MEMBERSHIP_SHA256": corrected._membership_sha(eligible),
                    "PARENT_NOT_APPLICABLE_MEMBERSHIP_SHA256": corrected._membership_sha(parent_na),
                    "REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256": corrected._membership_sha([variant_id]),
                    "CORRECTED_NOT_APPLICABLE_MEMBERSHIP_SHA256": corrected._membership_sha(corrected_na),
                    "PARENT_V3_INPUT_ID": value["input_id"],
                    "PARENT_V3_INPUT_SHA256": corrected._sha(value),
                    "PARENT_V2_INPUT_ID": parent["input_id"],
                    "PARENT_V2_INPUT_SHA256": corrected._sha(parent),
                    "BASE_INTAKE_ID": base["intake_id"],
                    "BASE_INTAKE_SHA256": corrected._sha(base),
                    "PARENT_WORKSPACE_ID": "d" * 64,
                    "PARENT_WORKSPACE_MANIFEST_SHA256": "e" * 64,
                    "PARENT_PROJECTION_SHA256": projection["projection_sha256"],
                    "PARENT_PROJECTION_ARTIFACT_SHA256": sha(projection_bytes),
                    "PARENT_SIDECARS_SHA256": projection["forecast_research"]["sidecars_sha256"],
                    "SOURCE_IDENTITY_ID": source_identity["identity_id"],
                    "SOURCE_IDENTITY_FILE_SHA256": sha(source_identity_bytes),
                    "SOURCE_EVIDENCE_SHA256": source_identity["evidence_sha256"],
                    "RAW_POINT_VIOLATIONS": raw_violations[0],
                    "RAW_TARGET_VIOLATIONS": raw_violations[1],
                    "RAW_UNIQUE_VIOLATION_VARIANTS": raw_violations[2],
                }
                lineage = {
                    "accepted_commit": corrected.ACCEPTED_COMMIT,
                    "accepted_tree": corrected.ACCEPTED_TREE,
                    "runtime_commit": corrected.RUNTIME_COMMIT,
                    "runtime_tree": corrected.RUNTIME_TREE,
                    "test_commit": corrected.TEST_COMMIT,
                    "test_tree": corrected.TEST_TREE,
                    "implementation_commit": "a" * 40,
                    "implementation_tree": "b" * 40,
                }
                with patch.multiple(corrected, **patches):
                    delta = corrected.build_creation_evidence_delta(raw, value)
                    child_input = corrected._build_corrected_input(value, delta, lineage)
                    child_projection = corrected.build_private_v3_corrected_projection(
                        child_input, delta, value, parent, projection
                    )
                    yield {
                        **built,
                        "raw": raw,
                        "delta": delta,
                        "input": child_input,
                        "corrected_projection": child_projection,
                        "variant_id": variant_id,
                        "patches": patches,
                    }
        finally:
            helper.doCleanups()

    def test_delta_transport_and_corrected_input_are_content_addressed(self) -> None:
        with self._built() as built:
            delta = built["delta"]
            value = built["input"]
            self.assertEqual(delta["controls"]["row_count"], 1)
            self.assertEqual(delta["raw_transport"]["newline"], "CRLF")
            self.assertEqual(delta["delta_id"], corrected._content_id(delta, "delta_id"))
            self.assertEqual(value["input_id"], corrected._content_id(value, "input_id"))
            self.assertEqual(value["coverage_controls"]["blocked_count"], 0)

    def test_corrected_projection_reclassifies_only_reviewed_blocker(self) -> None:
        with self._built() as built:
            projection = built["corrected_projection"]
            variant_id = built["variant_id"]
            owner = next(
                row for row in projection["owner_worksheet"]
                if row["shopify_variant_id"] == variant_id
            )
            self.assertEqual(owner["existence_basis"], corrected._CORRECTED_EXISTENCE_BASIS)
            self.assertEqual(owner["sidecar_keys"], [])
            self.assertEqual(owner["stage_status"]["CAPTURE"], "SUPPORTED_REVIEWED_EXACT_ID_CREATION_EVIDENCE")
            self.assertEqual(owner["stage_status"]["FORECAST"], "NOT_APPLICABLE:FULL_138_DAY_WINDOW")
            for summary in owner["scenario_results"].values():
                self.assertEqual(summary["primary_status"], "NOT_APPLICABLE")
                self.assertIsNone(summary["point_forecast_units"])

    def test_eligible_rows_share_one_joint_sidecar_and_monotone_prefixes(self) -> None:
        with self._built() as built:
            projection = built["corrected_projection"]
            self.assertEqual(len(projection["forecast_sidecars"]), 2)
            for owner in projection["owner_worksheet"]:
                if owner["scenario_results"]["H3"]["primary_status"] != "CALCULATED":
                    continue
                self.assertEqual(len(owner["sidecar_keys"]), 1)
                values = [
                    Decimal(owner["scenario_results"][key]["point_forecast_units"])
                    for key in ("H3", "H10", "H17")
                ]
                targets = [
                    Decimal(owner["scenario_results"][key]["target_units"])
                    for key in ("H3", "H10", "H17")
                ]
                self.assertEqual(values, sorted(values))
                self.assertEqual(targets, sorted(targets))

    def test_primary_counts_and_memberships_are_exact(self) -> None:
        with self._built() as built:
            projection = built["corrected_projection"]
            for counts in projection["joint_forecast_research"]["primary_status_counts"].values():
                self.assertEqual(counts["CALCULATED"], 2)
                self.assertEqual(counts["NOT_APPLICABLE"], 3)
                self.assertEqual(counts["BLOCKED"], 0)
                self.assertEqual(counts["NOT_PROCESSED"], 0)
            self.assertEqual(projection["coverage_summary"]["variant_count"], 5)

    def test_violation_counts_are_unique_variants_not_pair_events(self) -> None:
        def scenario(point: str, protection: str) -> dict[str, object]:
            return {
                "primary_status": "CALCULATED",
                "point_forecast_units": point,
                "protection_units": protection,
                "target_units": str(Decimal(point) + Decimal(protection)),
            }

        rows = [
            {
                "shopify_variant_id": "100",
                "scenario_results": {
                    "H3": scenario("3", "6"),
                    "H10": scenario("2", "6"),
                    "H17": scenario("1", "6"),
                },
            },
            {
                "shopify_variant_id": "200",
                "scenario_results": {
                    "H3": scenario("1", "4"),
                    "H10": scenario("2", "1"),
                    "H17": scenario("3", "1"),
                },
            },
        ]
        self.assertEqual(corrected._raw_violations(rows), (1, 2, 2))
        with patch.object(corrected, "ELIGIBLE_VARIANT_COUNT", 2):
            self.assertEqual(
                corrected._corrected_coherence_controls(rows),
                (1, 2, 2),
            )

    def test_plain_or_resealed_projection_is_not_source_authenticated(self) -> None:
        with self._built() as built:
            forged = deepcopy(dict(built["corrected_projection"]))
            forged["coverage_summary"]["blocked_variant_count"] = 99
            forged["projection_sha256"] = corrected._logical_sha(
                forged, "projection_sha256"
            )
            with self.assertRaisesRegex(
                corrected.PrivateResearchV3CorrectedError,
                "not source-authenticated",
            ):
                corrected.validate_private_v3_corrected_projection(forged)
            sealed = built["corrected_projection"]
            for invalid in ({1, 2}, float("nan")):
                sealed["forbidden"] = invalid
                with self.subTest(invalid=type(invalid).__name__), self.assertRaises(
                    corrected.PrivateResearchV3CorrectedError
                ):
                    corrected.validate_private_v3_corrected_projection(sealed)
                del sealed["forbidden"]

    def test_two_builds_are_byte_identical(self) -> None:
        with self._built() as built:
            with localcontext(Context(prec=1, rounding=ROUND_DOWN)):
                second = corrected.build_private_v3_corrected_projection(
                    built["input"],
                    built["delta"],
                    built["value"],
                    built["parent"],
                    built["projection"],
                )
                replayed = corrected.replay_private_v3_corrected_projection(
                    built["corrected_projection"],
                    built["input"],
                    built["delta"],
                    built["value"],
                    built["parent"],
                    built["projection"],
                )
            self.assertEqual(second, built["corrected_projection"])
            self.assertEqual(replayed, built["corrected_projection"])

    def test_delta_and_input_validators_reject_json_numeric_type_drift(self) -> None:
        with self._built() as built:
            malformed_delta = dict(built["delta"])
            malformed_delta["authority"] = {1, 2}
            with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
                corrected.validate_creation_evidence_delta(
                    malformed_delta, built["value"]
                )
            malformed_input = dict(built["input"])
            malformed_input["authority"] = {1, 2}
            with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
                corrected.validate_private_v3_corrected_input(malformed_input)

            forged_delta = deepcopy(dict(built["delta"]))
            forged_delta["raw_csv_bytes"] = float(forged_delta["raw_csv_bytes"])
            forged_delta["delta_id"] = corrected._content_id(
                forged_delta, "delta_id"
            )
            with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
                corrected.validate_creation_evidence_delta(
                    forged_delta, built["value"]
                )

            for path, replacement in (
                (("coverage_controls", "blocked_count"), False),
                (("coverage_controls", "current_catalog_count"), 5.0),
                (
                    (
                        "coverage_controls",
                        "noneligible_reason_counts",
                        corrected._FIRST_DAY_REASON,
                    ),
                    1.0,
                ),
            ):
                forged_input = deepcopy(dict(built["input"]))
                target = forged_input
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = replacement
                forged_input["input_id"] = corrected._content_id(
                    forged_input, "input_id"
                )
                with self.subTest(path=path):
                    with self.assertRaises(
                        corrected.PrivateResearchV3CorrectedError
                    ):
                        corrected.validate_private_v3_corrected_input(
                            forged_input
                        )

    def test_delta_transport_schema_membership_and_literal_matrix_fails_closed(self) -> None:
        with self._built() as built:
            base = dict(built["delta"]["normalized_rows"][0])
            eligible_id = next(
                str(item["shopify_variant_id"])
                for item in built["value"]["eligibility_ledger"]
                if item["status"] == "ELIGIBLE"
            )
            row_cases = {
                "leading-zero-id": {**base, "shopify_variant_id": "0" + base["shopify_variant_id"]},
                "noncanonical-utc": {**base, "live_exact_id_created_at": "2026-05-06T08:00:00-04:00"},
                "pre-start-local-date": {**base, "live_exact_id_created_at": "2026-05-04T12:00:00Z"},
                "literal-drift": {**base, "reviewed_primary_status": "BLOCKED"},
                "descriptive-whitespace": {**base, "product_title": " " + base["product_title"]},
                "unknown-extra-id": {**base, "shopify_variant_id": "999999999999999999"},
                "seed-conflict": {**base, "shopify_variant_id": eligible_id},
            }
            raw_cases: dict[str, tuple[bytes, int, list[dict[str, str]]]] = {}
            for label, row in row_cases.items():
                raw_cases[label] = (self._raw_for_rows([row]), 1, [row])
            raw_cases["duplicate-id"] = (
                self._raw_for_rows([base, dict(base)]),
                2,
                [base, dict(base)],
            )
            raw_cases["missing-row"] = (self._raw_for_rows([]), 0, [])
            extra_fields = [*corrected._DELTA_FIELDS, "unexpected"]
            raw_cases["extra-schema-field"] = (
                self._raw_for_rows([{**base, "unexpected": "x"}], fieldnames=extra_fields),
                1,
                [base],
            )
            raw_cases["missing-bom"] = (built["raw"][3:], 1, [base])
            raw_cases["doubled-bom"] = (b"\xef\xbb\xbf" + built["raw"], 1, [base])
            raw_cases["lf-newlines"] = (
                built["raw"].replace(b"\r\n", b"\n"),
                1,
                [base],
            )
            header_only = self._raw_for_rows([])
            raw_cases["malformed-csv"] = (
                header_only + b'"unterminated\r\n',
                1,
                [base],
            )
            raw_cases["invalid-utf8"] = (
                header_only + b"\xff\r\n",
                1,
                [base],
            )
            for label, (raw, row_count, logical_rows) in raw_cases.items():
                ordered = sorted(logical_rows, key=lambda item: int(item["shopify_variant_id"]))
                with self.subTest(case=label), patch.multiple(
                    corrected,
                    DELTA_RAW_BYTES=len(raw),
                    DELTA_RAW_SHA256=sha(raw),
                    DELTA_ROW_COUNT=row_count,
                    DELTA_ROW_SET_SHA256=sha(corrected.canonical_json_bytes(ordered)),
                ):
                    with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
                        corrected.build_creation_evidence_delta(raw, built["value"])

            for field, bad_value in (
                ("DELTA_RAW_SHA256", "0" * 64),
                ("DELTA_ROW_SET_SHA256", "0" * 64),
            ):
                with self.subTest(digest=field), patch.object(
                    corrected, field, bad_value
                ):
                    with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
                        corrected.build_creation_evidence_delta(
                            built["raw"], built["value"]
                        )

            for field, replacement in (
                ("raw_transport", {**built["delta"]["raw_transport"], "terminal_line_terminator": 1}),
                ("controls", {**built["delta"]["controls"], "seed_overlap_count": False}),
                ("parent_v3_input", {**built["delta"]["parent_v3_input"], "input_id": "0" * 64}),
            ):
                forged = deepcopy(dict(built["delta"]))
                forged[field] = replacement
                forged["delta_id"] = corrected._content_id(forged, "delta_id")
                with self.subTest(envelope=field), self.assertRaises(
                    corrected.PrivateResearchV3CorrectedError
                ):
                    corrected.validate_creation_evidence_delta(
                        forged, built["value"]
                    )

            malformed_parent = deepcopy(built["value"])
            malformed_parent["contract"] = "FORGED_PARENT"
            with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
                corrected.validate_creation_evidence_delta(
                    built["delta"], malformed_parent
                )

    def test_delta_normalization_is_row_order_invariant(self) -> None:
        with self._built() as built:
            first = dict(built["delta"]["normalized_rows"][0])
            second = {
                **first,
                "shopify_variant_id": str(int(first["shopify_variant_id"]) + 1000000000),
                "product_title": "Synthetic second product",
                "variant_title": "Synthetic second variant",
                "live_exact_id_created_at": "2026-05-07T12:00:00Z",
            }
            expected = sorted([first, second], key=lambda item: int(item["shopify_variant_id"]))
            fake_parent = {
                "eligibility_ledger": [
                    {
                        "shopify_variant_id": item["shopify_variant_id"],
                        "blocker_reason": corrected._PRIOR_ABSENT_REASON,
                        "seed_row_sha256": None,
                    }
                    for item in expected
                ]
            }
            normalized_results = []
            for rows in ([first, second], [second, first]):
                raw = self._raw_for_rows(list(rows))
                with patch.object(
                    corrected,
                    "_validated_parent_v3_input",
                    return_value=fake_parent,
                ), patch.multiple(
                    corrected,
                    DELTA_RAW_BYTES=len(raw),
                    DELTA_RAW_SHA256=sha(raw),
                    DELTA_ROW_COUNT=2,
                    DELTA_ROW_SET_SHA256=sha(corrected.canonical_json_bytes(expected)),
                    REVIEWED_PRIOR_BLOCKED_MEMBERSHIP_SHA256=corrected._membership_sha(
                        [item["shopify_variant_id"] for item in expected]
                    ),
                ):
                    normalized_results.append(
                        corrected._parse_delta_bytes(raw, fake_parent)
                    )
            self.assertEqual(normalized_results, [expected, expected])

    def test_projection_boundary_replays_planner_and_rejects_coherence_forgery(self) -> None:
        with self._built() as built:
            real_planner = corrected.plan_joint_horizon_forecast

            def forged_planner(*args, **kwargs):
                evidence = deepcopy(real_planner(*args, **kwargs))
                summary = evidence["summaries"]["H3"]
                summary["point_forecast_units"] = "9999.0000"
                summary["target_units"] = str(
                    Decimal(summary["point_forecast_units"])
                    + Decimal(summary["protection_units"])
                )
                evidence["sidecar_sha256"] = joint._logical_sha(evidence)
                return evidence

            with patch.object(
                corrected,
                "plan_joint_horizon_forecast",
                side_effect=forged_planner,
            ):
                with self.assertRaisesRegex(
                    corrected.PrivateResearchV3CorrectedError,
                    "joint forecast refused",
                ):
                    corrected.build_private_v3_corrected_projection(
                        built["input"],
                        built["delta"],
                        built["value"],
                        built["parent"],
                        built["projection"],
                    )

    def test_real_git_object_lineage_uses_commit_and_tree_pairs(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        lineage = {
            "accepted_commit": corrected.ACCEPTED_COMMIT,
            "accepted_tree": corrected.ACCEPTED_TREE,
            "runtime_commit": corrected.RUNTIME_COMMIT,
            "runtime_tree": corrected.RUNTIME_TREE,
            "test_commit": corrected.TEST_COMMIT,
            "test_tree": corrected.TEST_TREE,
            "implementation_commit": corrected.DESIGN_COMMIT,
            "implementation_tree": corrected.DESIGN_TREE,
        }
        corrected._verify_lineage_objects(repo_root, lineage)
        forged = dict(lineage)
        forged["implementation_tree"] = corrected.ACCEPTED_TREE
        with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
            corrected._verify_lineage_objects(repo_root, forged)

    def test_bundle_reader_binds_storage_key_identity_and_private_modes(self) -> None:
        with self._built() as built, tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            delta = dict(built["delta"])
            child_input = dict(built["input"])
            input_path = self._write_private(
                root,
                corrected.corrected_input_manifest_key(child_input["input_id"]),
                corrected.canonical_json_bytes(child_input),
            )
            self._write_private(
                root,
                corrected.delta_manifest_key(delta["delta_id"]),
                corrected.canonical_json_bytes(delta),
            )
            self._write_private(
                root,
                delta["raw_csv_key"],
                built["raw"],
            )
            with patch.object(corrected, "_verify_lineage_objects"):
                loaded_delta, loaded_input = corrected.read_private_v3_corrected_bundle(
                    root,
                    child_input["input_id"],
                    parent_v3_input=built["value"],
                    repo_root=Path(__file__).resolve().parents[2],
                )
                self.assertEqual(dict(loaded_delta), delta)
                self.assertEqual(dict(loaded_input), child_input)

                wrong_id = "9" * 64
                self._write_private(
                    root,
                    corrected.corrected_input_manifest_key(wrong_id),
                    corrected.canonical_json_bytes(child_input),
                )
                with self.assertRaisesRegex(
                    corrected.PrivateResearchV3CorrectedError,
                    "not canonical",
                ):
                    corrected.read_private_v3_corrected_bundle(
                        root,
                        wrong_id,
                        parent_v3_input=built["value"],
                        repo_root=Path(__file__).resolve().parents[2],
                    )

                os.chmod(input_path, 0o644)
                with self.assertRaisesRegex(
                    corrected.PrivateResearchV3CorrectedError,
                    "bytes or mode differ",
                ):
                    corrected.read_private_v3_corrected_bundle(
                        root,
                        child_input["input_id"],
                        parent_v3_input=built["value"],
                        repo_root=Path(__file__).resolve().parents[2],
                    )

        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as outside:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            private = root / "private-research"
            private.mkdir(mode=0o700)
            outside_path = Path(outside).resolve()
            os.chmod(outside_path, 0o755)
            (private / "corrected-v3-inputs").symlink_to(
                outside_path, target_is_directory=True
            )
            with self.assertRaises(corrected.PrivateResearchV3CorrectedError):
                corrected._ensure_corrected_private_parent(
                    root,
                    "private-research/corrected-v3-inputs/fixture.json",
                )
            self.assertEqual(stat.S_IMODE(outside_path.stat().st_mode), 0o755)
            self.assertFalse((outside_path / "fixture.json").exists())

    def test_disposition_hash_and_compact_renderers_cover_parent_na_rows(self) -> None:
        with self._built() as built:
            projection = built["corrected_projection"]
            delta_ids = {
                row["shopify_variant_id"]
                for row in built["delta"]["normalized_rows"]
            }
            ledger = corrected._corrected_disposition_ledger(
                built["value"], delta_ids
            )
            expected = hashlib.sha256(
                corrected._projection_canonical(ledger)
            ).hexdigest()
            self.assertEqual(
                projection["joint_forecast_research"]["history_controls"]
                ["corrected_disposition_ledger_sha256"],
                expected,
            )
            html = render_private_research_html(projection)
            csv_text = render_private_research_csv(projection)
            self.assertIn("Joint H3/H10/H17 confidence", html)
            self.assertIn("Joint H3/H10/H17 confidence", csv_text)
            self.assertNotIn('"H3 confidence"', csv_text)
            self.assertIn("not a purchase quantity", html)
            self.assertEqual(len(list(csv.DictReader(io.StringIO(csv_text)))), 6)
            not_applicable = filter_private_research_rows(
                projection, status="NOT_APPLICABLE"
            )
            self.assertEqual(len(not_applicable), 3)
        with patch.object(
            projection_module,
            "_validated_projection",
            return_value={
                "contract": corrected.PROJECTION_CONTRACT,
                "label": "Café",
            },
        ):
            serialized = canonical_private_research_projection_bytes({})
        self.assertIn("Café".encode("utf-8"), serialized)
        self.assertNotIn(b"\\u00e9", serialized)
        self.assertTrue(serialized.endswith(b"\n"))

    def test_source_capability_registry_does_not_retain_dead_projections(self) -> None:
        proof = object()
        before = len(corrected._VERIFIED)
        value = corrected._seal({"large": ["x" * 1024] * 100}, proof)
        object_id = id(value)
        self.assertIn(object_id, corrected._VERIFIED)
        del value
        gc.collect()
        self.assertNotIn(object_id, corrected._VERIFIED)
        self.assertEqual(len(corrected._VERIFIED), before)

    def test_parent_snapshot_pins_legacy_directory_modes_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            os.chmod(root, 0o700)
            data = b"accepted-parent\n"
            key = "private-research/intakes/accepted.json"
            path = self._write_private(root, key, data)
            os.chmod(path.parent, 0o755)
            modes = {
                ".": 0o700,
                "private-research": 0o700,
                "private-research/intakes": 0o755,
            }
            files = {key: (len(data), sha(data))}
            with patch.object(
                corrected, "_ACCEPTED_PARENT_DIRECTORY_MODES", modes
            ), patch.object(corrected, "_ACCEPTED_PARENT_FILES", files):
                snapshot = corrected.accepted_parent_snapshot(root)
                self.assertEqual(snapshot["directories"][-1]["mode"], "0755")
                os.chmod(path.parent, 0o700)
                with self.assertRaisesRegex(
                    corrected.PrivateResearchV3CorrectedError,
                    "directory mode differs",
                ):
                    corrected.accepted_parent_snapshot(root)


if __name__ == "__main__":
    unittest.main()
