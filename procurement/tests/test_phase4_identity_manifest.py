from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock


PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT.parent))
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os.historical_sales_manifest import (
    APPROVED_MANIFEST_SHA256,
    APPROVED_RUN_ID,
    EXCLUSION_SOURCE_KEYS,
    FIESTA_CANONICAL_VARIANT_ID,
    HIGH_NOON_TEQUILA_SOURCE_KEYS,
    NUTRL_CANONICAL_VARIANT_ID,
    NUTRL_SOURCE_KEYS,
    ManifestValidationError,
    load_authorized_manifest,
    manifest_controls,
    parse_manifest,
    read_manifest_bytes,
    require_review_authorization,
    validate_manifest_rows,
    validate_static_manifest,
)


MANIFEST_PATH = (
    PROCUREMENT_ROOT / "review" / "phase4_identity_manifest_corrected.csv"
)


class Phase4IdentityManifestContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_bytes = read_manifest_bytes(MANIFEST_PATH)
        cls.rows = parse_manifest(cls.manifest_bytes)

    def test_approved_manifest_hash_and_exact_controls(self):
        manifest = load_authorized_manifest(MANIFEST_PATH)
        self.assertEqual(manifest.sha256, APPROVED_MANIFEST_SHA256)
        self.assertEqual(manifest.run_id, APPROVED_RUN_ID)
        self.assertEqual(
            manifest.controls.as_dict(),
            {
                "affected_raw_rows": 3112,
                "absolute_sales_magnitude": "72616.29",
                "absolute_unit_magnitude": "3696.0000",
                "distinct_map_targets": 51,
                "exclude": 8,
                "flag_owner": 0,
                "leave_unresolved": 280,
                "map": 55,
                "map_targets_populated": 55,
                "material": 341,
                "nonmap_targets_populated": 0,
                "nonmaterial": 2,
                "rows": 343,
                "unique_source_identity_keys": 343,
            },
        )

    def test_controls_are_decimal_exact_and_order_independent(self):
        controls = manifest_controls(tuple(reversed(self.rows)))
        self.assertIsInstance(controls.absolute_unit_magnitude, Decimal)
        self.assertIsInstance(controls.absolute_sales_magnitude, Decimal)
        self.assertEqual(controls.absolute_unit_magnitude, Decimal("3696.0000"))
        self.assertEqual(controls.absolute_sales_magnitude, Decimal("72616.29"))
        self.assertEqual(controls, manifest_controls(self.rows))

    def test_altered_bytes_fail_the_sha_check(self):
        altered = self.manifest_bytes.replace(
            b"No qualifying unique canonical identity.",
            b"No qualifying unique canonical identity!",
            1,
        )
        altered_rows = parse_manifest(altered)
        with self.assertRaisesRegex(ManifestValidationError, "SHA-256"):
            validate_static_manifest(altered, altered_rows)

    def test_duplicate_source_key_fails(self):
        mutated = self.rows[:-1] + (replace(self.rows[-1], source_identity_key=self.rows[0].source_identity_key),)
        with self.assertRaisesRegex(ManifestValidationError, "duplicate source_identity_key"):
            validate_manifest_rows(mutated)

    def test_header_drift_and_unknown_disposition_fail(self):
        drifted = self.manifest_bytes.replace(
            b"source_identity_key,historical_product_title",
            b"source_key,historical_product_title",
            1,
        )
        with self.assertRaisesRegex(ManifestValidationError, "header"):
            parse_manifest(drifted)
        mutated = self.rows[:-1] + (replace(self.rows[-1], review_disposition="FLAG_OWNER"),)
        with self.assertRaisesRegex(ManifestValidationError, "unknown review disposition"):
            validate_manifest_rows(mutated)

    def test_map_requires_target(self):
        index = next(i for i, row in enumerate(self.rows) if row.review_disposition == "MAP")
        mutated = list(self.rows)
        mutated[index] = replace(mutated[index], canonical_variant_id=None)
        with self.assertRaisesRegex(ManifestValidationError, "MAP row requires"):
            validate_manifest_rows(tuple(mutated))

    def test_nonmap_forbids_target(self):
        index = next(i for i, row in enumerate(self.rows) if row.review_disposition != "MAP")
        mutated = list(self.rows)
        mutated[index] = replace(mutated[index], canonical_variant_id="41716813627467")
        with self.assertRaisesRegex(ManifestValidationError, "non-MAP row forbids"):
            validate_manifest_rows(tuple(mutated))

    def test_exact_exclusion_set_is_required(self):
        self.assertEqual(
            {row.source_identity_key for row in self.rows if row.review_disposition == "EXCLUDE"},
            EXCLUSION_SOURCE_KEYS,
        )
        index = next(i for i, row in enumerate(self.rows) if row.review_disposition == "EXCLUDE")
        mutated = list(self.rows)
        mutated[index] = replace(mutated[index], review_disposition="LEAVE_UNRESOLVED")
        with self.assertRaisesRegex(ManifestValidationError, "exclusion set"):
            validate_manifest_rows(tuple(mutated))

    def test_fiesta_target_is_forbidden(self):
        index = next(i for i, row in enumerate(self.rows) if row.review_disposition == "MAP")
        mutated = list(self.rows)
        mutated[index] = replace(mutated[index], canonical_variant_id=FIESTA_CANONICAL_VARIANT_ID)
        with self.assertRaisesRegex(ManifestValidationError, "Fiesta"):
            validate_manifest_rows(tuple(mutated))

    def test_nutrl_exception_is_exactly_three_rows(self):
        nutrl = {row.source_identity_key: row for row in self.rows if row.source_identity_key in NUTRL_SOURCE_KEYS}
        self.assertEqual(set(nutrl), NUTRL_SOURCE_KEYS)
        self.assertEqual({row.canonical_variant_id for row in nutrl.values()}, {NUTRL_CANONICAL_VARIANT_ID})
        index = next(i for i, row in enumerate(self.rows) if row.source_identity_key in NUTRL_SOURCE_KEYS)
        mutated = list(self.rows)
        mutated[index] = replace(mutated[index], canonical_variant_id="41224455684171")
        with self.assertRaisesRegex(ManifestValidationError, "NUTRL"):
            validate_manifest_rows(tuple(mutated))

    def test_high_noon_tequila_stays_unresolved(self):
        tequila = {row.source_identity_key: row for row in self.rows if row.source_identity_key in HIGH_NOON_TEQUILA_SOURCE_KEYS}
        self.assertEqual(set(tequila), HIGH_NOON_TEQUILA_SOURCE_KEYS)
        self.assertEqual({row.review_disposition for row in tequila.values()}, {"LEAVE_UNRESOLVED"})
        index = next(i for i, row in enumerate(self.rows) if row.source_identity_key in HIGH_NOON_TEQUILA_SOURCE_KEYS)
        mutated = list(self.rows)
        mutated[index] = replace(
            mutated[index],
            review_disposition="MAP",
            canonical_variant_id=FIESTA_CANONICAL_VARIANT_ID,
        )
        with self.assertRaisesRegex(ManifestValidationError, "Fiesta|High Noon"):
            validate_manifest_rows(tuple(mutated))

    def test_missing_configured_review_token_fails_closed(self):
        with self.assertRaisesRegex(PermissionError, "not configured"):
            require_review_authorization(None, "supplied")

    def test_missing_or_invalid_supplied_token_fails_without_leaking_values(self):
        expected = "synthetic-expected-secret"
        for supplied in (None, "", "synthetic-wrong-secret"):
            with self.subTest(supplied=bool(supplied)):
                with self.assertRaises(PermissionError) as caught:
                    require_review_authorization(expected, supplied)
                rendered = str(caught.exception)
                self.assertNotIn(expected, rendered)
                if supplied:
                    self.assertNotIn(supplied, rendered)


class Phase4IdentityManifestCliTests(unittest.TestCase):
    def setUp(self) -> None:
        from procurement.tools import persist_phase4_identity_manifest as cli

        self.cli = cli
        self.environment = {
            "RECONCILIATION_REVIEW_TOKEN": "synthetic-expected-secret",
            "PHASE4_REVIEW_TOKEN_INPUT": "synthetic-expected-secret",
            "DATABASE_URL": "postgresql://credential-that-must-not-be-rendered",
        }

    def test_authorization_failure_occurs_before_connection_creation(self):
        environment = dict(self.environment)
        environment.pop("PHASE4_REVIEW_TOKEN_INPUT")
        with mock.patch.object(self.cli, "connect_database") as connect:
            with self.assertRaises(PermissionError):
                self.cli.execute(
                    ["--manifest", str(MANIFEST_PATH), "--dry-run", "--actor", "owner"],
                    environ=environment,
                )
        connect.assert_not_called()

    def test_dry_run_never_calls_persistence_and_output_is_secret_free(self):
        report = {"mode": "DRY_RUN", "manifest_sha256": APPROVED_MANIFEST_SHA256}
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        with (
            mock.patch.object(self.cli, "connect_database", return_value=connection),
            mock.patch.object(self.cli, "dry_run_manifest", return_value=report) as dry_run,
            mock.patch.object(self.cli, "persist_manifest_decisions") as persist,
            mock.patch.object(self.cli, "repository_state", return_value=("a" * 40, True)),
        ):
            result = self.cli.execute(
                ["--manifest", str(MANIFEST_PATH), "--dry-run", "--actor", "owner"],
                environ=self.environment,
            )
        dry_run.assert_called_once()
        persist.assert_not_called()
        rendered = json.dumps(result, sort_keys=True)
        for secret in self.environment.values():
            self.assertNotIn(secret, rendered)

    def test_apply_requires_clean_commit_and_passes_exact_head(self):
        args = ["--manifest", str(MANIFEST_PATH), "--apply", "--actor", "owner"]
        with mock.patch.object(self.cli, "repository_state", return_value=("b" * 40, False)):
            with self.assertRaisesRegex(RuntimeError, "clean committed"):
                self.cli.execute(args, environ=self.environment)

        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        report = {"mode": "APPLY", "manifest_sha256": APPROVED_MANIFEST_SHA256}
        with (
            mock.patch.object(self.cli, "repository_state", return_value=("c" * 40, True)),
            mock.patch.object(self.cli, "connect_database", return_value=connection),
            mock.patch.object(self.cli, "persist_manifest_decisions", return_value=report) as persist,
            mock.patch.object(self.cli, "dry_run_manifest") as dry_run,
        ):
            result = self.cli.execute(args, environ=self.environment)
        dry_run.assert_not_called()
        persist.assert_called_once()
        context = persist.call_args.args[2]
        self.assertEqual(context.implementation_git_sha, "c" * 40)
        self.assertEqual(result, report)


if __name__ == "__main__":
    unittest.main()
