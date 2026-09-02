"""Pure controls for the frozen Phase 4 terminal-disposition artifact."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.historical_sales_manifest import (  # noqa: E402
    APPROVED_MANIFEST_SHA256,
    EXCLUSION_SOURCE_KEYS,
)
from procurement_os.historical_sales_terminal import (  # noqa: E402
    MAP_TARGET_FLOW_SHA256,
    ORIGINAL_EXCLUSION_REASON,
    TERMINAL_EXCLUSION_REASON,
    TERMINAL_MANIFEST_SHA256,
    ExecutionGitIdentity,
    TerminalExecutionContext,
    TerminalValidationError,
    canonical_map_target_flow_bytes,
    derive_execution_git_identity,
    load_terminal_artifact,
)


ORIGINAL_PATH = PROCUREMENT_ROOT / "review" / "phase4_identity_manifest_corrected.csv"
TERMINAL_PATH = PROCUREMENT_ROOT / "review" / "phase4_terminal_disposition_manifest.csv"
GOLDEN_PATH = PROCUREMENT_ROOT / "tests" / "fixtures" / "phase4_map_target_flow_v1.json"
FIESTA_TARGET = "41193000796235"


class Phase4TerminalDispositionPureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = load_terminal_artifact(TERMINAL_PATH, ORIGINAL_PATH)

    def test_exact_terminal_artifact_hash_and_membership(self):
        self.assertEqual(self.artifact.sha256, TERMINAL_MANIFEST_SHA256)
        self.assertEqual(hashlib.sha256(TERMINAL_PATH.read_bytes()).hexdigest(), TERMINAL_MANIFEST_SHA256)
        self.assertEqual(len(self.artifact.rows), 280)
        self.assertEqual(len({row.source_identity_key for row in self.artifact.rows}), 280)
        self.assertEqual(
            {row.source_identity_key for row in self.artifact.rows},
            {
                row.source_identity_key
                for row in self.artifact.original_manifest.rows
                if row.review_disposition == "LEAVE_UNRESOLVED"
            },
        )

    def test_exact_supplement_and_combined_controls(self):
        controls = self.artifact.controls.as_dict()
        self.assertEqual(
            controls["supplement_actions"],
            {"RESTORE": 43, "MAP": 47, "EXCLUDE": 190},
        )
        self.assertEqual(
            controls["combined_actions"],
            {"RESTORE": 43, "MAP": 102, "EXCLUDE": 198, "LEAVE_UNRESOLVED": 0},
        )
        self.assertEqual(controls["supplement_raw_rows"], 2100)
        self.assertEqual(controls["supplement_net_units"], "2541.0000")
        self.assertEqual(controls["supplement_absolute_units"], "2551.0000")
        self.assertEqual(controls["supplement_net_sales"], "50297.92")
        self.assertEqual(controls["supplement_absolute_sales"], "53287.92")
        self.assertEqual(controls["combined_map_raw_rows"], 1023)
        self.assertEqual(controls["distinct_map_targets"], 96)

    def test_disposition_totals_are_exact(self):
        by_action = self.artifact.controls.by_action
        self.assertEqual(
            by_action["RESTORE"].as_tuple(),
            (43, 435, Decimal("511.0000"), Decimal("511.0000"), Decimal("8506.52"), Decimal("8506.52")),
        )
        self.assertEqual(
            by_action["MAP"].as_tuple(),
            (47, 200, Decimal("232.0000"), Decimal("232.0000"), Decimal("4096.03"), Decimal("4096.03")),
        )
        self.assertEqual(
            by_action["EXCLUDE"].as_tuple(),
            (190, 1465, Decimal("1798.0000"), Decimal("1808.0000"), Decimal("37695.37"), Decimal("40685.37")),
        )

    def test_restore_targets_are_exact_original_variant_ids(self):
        restores = [row for row in self.artifact.rows if row.action == "RESTORE"]
        self.assertEqual(len(restores), 43)
        self.assertEqual(len({row.source_variant_id for row in restores}), 43)
        for row in restores:
            self.assertNotIn(row.source_variant_id, {None, "0"})
            self.assertEqual(row.canonical_variant_id, row.source_variant_id)
            self.assertIsNone(row.exclusion_reason_code)

    def test_continuity_pairs_are_exact(self):
        pairs: dict[str, list[object]] = {}
        for row in self.artifact.rows:
            if row.continuity_pair_id:
                pairs.setdefault(row.continuity_pair_id, []).append(row)
        self.assertEqual(len(pairs), 19)
        self.assertEqual(sum(map(len, pairs.values())), 38)
        for rows in pairs.values():
            self.assertEqual({row.continuity_role for row in rows}, {"PREDECESSOR", "SUCCESSOR"})
            predecessor = next(row for row in rows if row.continuity_role == "PREDECESSOR")
            successor = next(row for row in rows if row.continuity_role == "SUCCESSOR")
            self.assertEqual(predecessor.action, "MAP")
            self.assertEqual(successor.action, "RESTORE")
            self.assertEqual(predecessor.canonical_variant_id, successor.source_variant_id)
            self.assertEqual(successor.canonical_variant_id, successor.source_variant_id)
            self.assertFalse(predecessor.continuity_sale_periods_overlap)
            self.assertFalse(successor.continuity_sale_periods_overlap)
            self.assertLess(predecessor.last_sale_date, successor.first_sale_date)

    def test_fiesta_high_noon_and_popov_controls(self):
        self.assertFalse(any(row.canonical_variant_id == FIESTA_TARGET for row in self.artifact.rows))
        high_noon = [row for row in self.artifact.rows if "HIGH NOON TEQUILA" in row.source_identity_key]
        popov = [row for row in self.artifact.rows if "POPOV" in row.source_identity_key]
        self.assertEqual(len(high_noon), 3)
        self.assertTrue(all(row.action == "EXCLUDE" for row in high_noon))
        self.assertEqual(len(popov), 2)
        self.assertTrue(all(row.action == "EXCLUDE" for row in popov))

    def test_terminal_exclusions_have_exact_reason_and_no_target(self):
        exclusions = [row for row in self.artifact.rows if row.action == "EXCLUDE"]
        self.assertEqual(len(exclusions), 190)
        self.assertTrue(all(row.canonical_variant_id is None for row in exclusions))
        self.assertEqual({row.exclusion_reason_code for row in exclusions}, {TERMINAL_EXCLUSION_REASON})

    def test_original_eight_keep_original_primary_provenance(self):
        originals = {
            row.source_identity_key: row
            for row in self.artifact.original_manifest.rows
            if row.review_disposition == "EXCLUDE"
        }
        self.assertEqual(set(originals), EXCLUSION_SOURCE_KEYS)
        self.assertEqual(len(originals), 8)
        for key, row in originals.items():
            self.assertEqual(row.row_number, next(r.row_number for r in self.artifact.original_manifest.rows if r.source_identity_key == key))
            self.assertEqual(self.artifact.original_manifest.sha256, APPROVED_MANIFEST_SHA256)
        self.assertEqual(ORIGINAL_EXCLUSION_REASON, "PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION")

    def test_exact_canonical_map_bytes_and_hash(self):
        canonical = canonical_map_target_flow_bytes(self.artifact)
        golden = GOLDEN_PATH.read_bytes()
        self.assertEqual(canonical, golden)
        self.assertEqual(len(canonical), 14522)
        self.assertTrue(canonical.endswith(b"\n"))
        self.assertFalse(canonical.endswith(b"\n\n"))
        self.assertNotIn(b"\r", canonical)
        self.assertFalse(canonical.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), MAP_TARGET_FLOW_SHA256)
        self.assertEqual(MAP_TARGET_FLOW_SHA256, "5d6832cea3df7a4f45d31d7e7a8100409ddc078936095fe3c775ce862a1f64a6")

    def test_map_bytes_are_input_order_independent(self):
        canonical = canonical_map_target_flow_bytes(self.artifact)
        reversed_artifact = replace(self.artifact, rows=tuple(reversed(self.artifact.rows)))
        reversed_original = replace(
            self.artifact.original_manifest,
            rows=tuple(reversed(self.artifact.original_manifest.rows)),
        )
        reversed_artifact = replace(reversed_artifact, original_manifest=reversed_original)
        self.assertEqual(canonical_map_target_flow_bytes(reversed_artifact), canonical)

    def test_map_flow_mutations_change_bytes_and_hash(self):
        map_index = next(i for i, row in enumerate(self.artifact.rows) if row.action == "MAP")
        row = self.artifact.rows[map_index]
        mutations = (
            replace(row, canonical_variant_id="99999999999999"),
            replace(row, raw_row_count=row.raw_row_count + 1),
            replace(row, net_units=row.net_units + Decimal("1.0000")),
            replace(row, net_sales=row.net_sales + Decimal("0.01")),
        )
        expected = canonical_map_target_flow_bytes(self.artifact)
        for mutation in mutations:
            rows = list(self.artifact.rows)
            rows[map_index] = mutation
            changed = canonical_map_target_flow_bytes(replace(self.artifact, rows=tuple(rows)))
            self.assertNotEqual(changed, expected)
            self.assertNotEqual(hashlib.sha256(changed).hexdigest(), MAP_TARGET_FLOW_SHA256)

    def test_map_flow_rejects_extra_decimal_precision(self):
        map_index = next(i for i, row in enumerate(self.artifact.rows) if row.action == "MAP")
        rows = list(self.artifact.rows)
        rows[map_index] = replace(rows[map_index], net_units=Decimal("1.00001"))
        with self.assertRaisesRegex(TerminalValidationError, "decimal precision"):
            canonical_map_target_flow_bytes(replace(self.artifact, rows=tuple(rows)))


class ExecutionGitIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        (self.root / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", "fixture"], check=True)
        self.sha = subprocess.check_output(["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True).strip()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_clean_committed_identity_is_derived(self):
        identity = derive_execution_git_identity(
            self.root,
            expected_sha=self.sha,
            required_paths=("tracked.py",),
        )
        self.assertEqual(identity.git_sha, self.sha)
        self.assertEqual(identity.repository_root, self.root.resolve())

    def test_expected_sha_is_only_an_assertion(self):
        with self.assertRaisesRegex(TerminalValidationError, "authorized execution Git SHA"):
            derive_execution_git_identity(self.root, expected_sha="f" * 40, required_paths=("tracked.py",))

    def test_dirty_staged_and_untracked_state_fail_closed(self):
        cases = ("dirty", "staged", "untracked")
        for case in cases:
            with self.subTest(case=case):
                subprocess.run(["git", "-C", str(self.root), "reset", "--hard", "-q", "HEAD"], check=True)
                untracked = self.root / "extra.py"
                if untracked.exists():
                    untracked.unlink()
                if case == "dirty":
                    (self.root / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
                elif case == "staged":
                    (self.root / "tracked.py").write_text("VALUE = 3\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(self.root), "add", "tracked.py"], check=True)
                else:
                    untracked.write_text("VALUE = 4\n", encoding="utf-8")
                with self.assertRaisesRegex(TerminalValidationError, "clean"):
                    derive_execution_git_identity(self.root, required_paths=("tracked.py",))

    def test_missing_or_untracked_implementation_path_fails(self):
        with self.assertRaisesRegex(TerminalValidationError, "tracked"):
            derive_execution_git_identity(self.root, required_paths=("missing.py",))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TerminalValidationError, "Git repository"):
                derive_execution_git_identity(Path(directory))


class Phase4TerminalDispositionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        from procurement.tools import persist_phase4_terminal_disposition as cli

        self.cli = cli
        self.environment = {"DATABASE_URL": "postgresql://secret-value"}

    def test_context_has_no_caller_supplied_observed_execution_sha(self):
        fields = set(TerminalExecutionContext.__dataclass_fields__)
        self.assertEqual(fields, {"actor", "expected_execution_git_sha"})

    def test_apply_requires_explicit_acknowledgement_before_database_access(self):
        with mock.patch.object(self.cli, "connect_database") as connect:
            with self.assertRaisesRegex(ValueError, "acknowledgement"):
                self.cli.execute(
                    ["--apply", "--actor", "owner"], environ=self.environment
                )
        connect.assert_not_called()

    def test_only_frozen_repository_manifest_paths_are_accepted(self):
        with tempfile.NamedTemporaryFile(suffix=".csv") as alternate:
            with mock.patch.object(self.cli, "connect_database") as connect:
                with self.assertRaisesRegex(ValueError, "frozen repository artifact"):
                    self.cli.execute(
                        [
                            "--dry-run",
                            "--actor",
                            "owner",
                            "--terminal-manifest",
                            alternate.name,
                        ],
                        environ=self.environment,
                    )
        connect.assert_not_called()

    def test_dry_run_derives_identity_and_never_calls_persistence(self):
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        report = {"classification": "CURRENT_TERMINAL_EXACT"}
        execution = ExecutionGitIdentity(Path("/fixture"), "c" * 40)
        with (
            mock.patch.object(
                self.cli,
                "derive_runtime_execution_git_identity",
                return_value=execution,
            ) as derive,
            mock.patch.object(
                self.cli, "connect_database", return_value=connection
            ),
            mock.patch.object(
                self.cli, "inspect_terminal_state", return_value=report
            ) as inspect_state,
            mock.patch.object(self.cli, "persist_terminal_disposition") as persist,
        ):
            result = self.cli.execute(
                [
                    "--dry-run",
                    "--actor",
                    "owner",
                    "--expected-execution-git-sha",
                    "c" * 40,
                ],
                environ=self.environment,
            )
        derive.assert_called_once_with("c" * 40)
        inspect_state.assert_called_once()
        persist.assert_not_called()
        self.assertEqual(result["execution_git_sha"], "c" * 40)
        self.assertNotIn(self.environment["DATABASE_URL"], json.dumps(result))

    def test_cli_and_service_have_no_forbidden_downstream_call_path(self):
        terminal_module = __import__(
            "procurement_os.historical_sales_terminal",
            fromlist=["historical_sales_terminal"],
        )
        source = inspect.getsource(self.cli) + inspect.getsource(terminal_module)
        for forbidden in (
            "finalize_sales_backfill(",
            "run_historical_sales_backfill(",
            "_set_sales_gate(",
            "shopify_client",
            "procurement_recommendations(",
            "purchase_order_lines(",
        ):
            self.assertNotIn(forbidden, source)
        catalog_sync_source = (
            PROCUREMENT_ROOT / "src" / "procurement_os" / "jobs" / "catalog_sync.py"
        ).read_text(encoding="utf-8")
        self.assertIn("identity_scope='CURRENT'", catalog_sync_source)


if __name__ == "__main__":
    unittest.main()
