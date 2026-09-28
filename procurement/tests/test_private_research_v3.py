"""Pure acceptance tests for additive exact-ID private research V3."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_private_research_projection import hypothesis, intake, variant
from test_private_research_v2 import (
    ORIGINAL_AUTHORITY,
    SEED_ALIASES,
    TERMINAL_AUTHORITY,
    capture,
    source_identity,
)

from procurement_os.development_forecast import DevelopmentForecastError
from procurement_os.private_research import (
    _build_private_v3_research_workspace_from_verified,
    read_private_research_workspace,
    read_private_research_workspace_structural,
)
from procurement_os.private_research_projection import (
    PrivateResearchProjectionError,
    canonical_private_research_projection_bytes,
    filter_private_research_rows,
    private_research_projection_sha256,
    render_private_research_csv,
    render_private_research_html,
)
from procurement_os.private_research_v2 import (
    build_private_v2_composite_history,
    build_private_v2_research_input,
    write_private_v2_research_input,
)
from procurement_os.private_research_v3 import (
    INPUT_CONTRACT,
    PROJECTION_CONTRACT,
    PrivateResearchV3Error,
    build_private_v3_research_input,
    build_private_v3_research_projection,
    read_private_v3_research_bundle,
    write_private_v3_research_input,
)
import procurement_os.private_research as private_research
import procurement_os.private_research_v2 as private_research_v2
import procurement_os.private_research_v3 as private_research_v3
from test_audit_private_research_browser import _load_tool as _load_browser_tool


SEED_FIELDS = [
    "variant_id",
    "shopify_gid",
    "product_id",
    "product_gid",
    "product_title",
    "variant_title",
    "handle",
    "status",
    "sku",
    "barcode",
    "variant_created_at",
    "retail_price",
    "current_cost",
    "inventory_quantity",
    "inventory_tracked",
    "shopify_vendor",
    "product_type",
    "active",
    "source_snapshot",
    "last_synced_at",
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PrivateResearchV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        source_patcher = patch.object(
            private_research_v2,
            "_require_registered_source_identity",
            side_effect=private_research_v2.validate_source_identity_record,
        )
        source_patcher.start()
        self.addCleanup(source_patcher.stop)

    def _seed_files(self, root: Path) -> tuple[Path, Path, dict[str, object]]:
        rows = []
        for variant_id, created_at in (
            ("100", "2020-01-01 00:00:00"),
            ("200", "2020-01-01 00:00:00"),
            ("300", "2026-05-04 01:00:00"),
            ("400", "2026-05-05 00:00:00"),
        ):
            row = {field: "" for field in SEED_FIELDS}
            row.update(
                {
                    "variant_id": variant_id,
                    "shopify_gid": f"gid://shopify/ProductVariant/{variant_id}",
                    "product_id": f"p-{variant_id}",
                    "product_gid": f"gid://shopify/Product/{variant_id}",
                    "product_title": f"Product {variant_id}",
                    "variant_title": "750ML",
                    "status": "ACTIVE",
                    "variant_created_at": created_at,
                    "active": "1",
                    "source_snapshot": "synthetic-reviewed-seed",
                }
            )
            rows.append(row)
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=SEED_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        variants_raw = stream.getvalue().encode("utf-8")
        variants_path = root / "variants.csv"
        variants_path.write_bytes(variants_raw)
        variants_path.chmod(0o600)
        source_sha = "d" * 64
        manifest = {
            "source_sha256": source_sha,
            "files": {
                "variants.csv": {
                    "rows": len(rows),
                    "sha256": _sha(variants_raw),
                }
            },
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_bytes(
            (
                json.dumps(manifest, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
        )
        manifest_path.chmod(0o600)
        return manifest_path, variants_path, {
            "manifest_sha": _sha(manifest_path.read_bytes()),
            "variants_sha": _sha(variants_raw),
            "source_sha": source_sha,
            "row_count": len(rows),
        }

    @contextmanager
    def _built(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base_rows = [
                variant(
                    "100",
                    hypotheses=[
                        hypothesis(
                            "100",
                            "Alpha",
                            "=DANGEROUS-SKU",
                            "9.25",
                            supplier_description="<script>not executable</script>",
                        )
                    ],
                    product_title="=Product <100>",
                ),
                variant("200"),
                variant("300"),
                variant("400"),
                variant("500", product_title="Product 200"),
            ]
            base = intake(base_rows)
            extension = capture(
                date(2026, 5, 4),
                54,
                contract="BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_EXTENSION_V2",
                include_late_variant=True,
            )
            replacement = capture(
                date(2026, 6, 27),
                84,
                contract="BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_REPLACEMENT_V2",
                include_late_variant=True,
            )
            extension_path = root / "extension.json"
            replacement_path = root / "replacement.json"
            for path, payload in (
                (extension_path, extension),
                (replacement_path, replacement),
            ):
                path.write_bytes(
                    (
                        json.dumps(payload, sort_keys=True, separators=(",", ":"))
                        + "\n"
                    ).encode("utf-8")
                )
                path.chmod(0o600)
            identity = source_identity(
                _sha(extension_path.read_bytes()),
                _sha(replacement_path.read_bytes()),
            )
            composite = build_private_v2_composite_history(
                base,
                extension_capture_path=extension_path,
                replacement_capture_path=replacement_path,
                source_identity=identity,
                seed_alias_path=SEED_ALIASES,
                original_authority_path=ORIGINAL_AUTHORITY,
                terminal_authority_path=TERMINAL_AUTHORITY,
            )
            parent = build_private_v2_research_input(
                base,
                composite_history=composite,
                source_identity=identity,
            )
            manifest_path, variants_path, controls = self._seed_files(root)
            with patch.multiple(
                private_research_v3,
                SEED_MANIFEST_SHA256=controls["manifest_sha"],
                SEED_VARIANTS_SHA256=controls["variants_sha"],
                SEED_SOURCE_SHA256=controls["source_sha"],
                SEED_VARIANT_ROW_COUNT=controls["row_count"],
            ):
                value = build_private_v3_research_input(
                    base,
                    parent_v2_input=parent,
                    seed_manifest_path=manifest_path,
                    seed_variants_path=variants_path,
                )
                projection = build_private_v3_research_projection(
                    value, parent, base
                )
                yield {
                    "root": root,
                    "base": base,
                    "identity": identity,
                    "parent": parent,
                    "value": value,
                    "projection": projection,
                    "manifest_path": manifest_path,
                    "variants_path": variants_path,
                    "sources": {
                        "HISTORY_EXTENSION_54D": extension_path,
                        "HISTORY_REPLACEMENT_84D": replacement_path,
                    },
                }

    @staticmethod
    def _reseal_projection(value: dict[str, object]) -> None:
        value.pop("projection_sha256", None)
        value["projection_sha256"] = private_research_v2._sha_bytes(
            private_research_v2._projection_canonical(value)
        )

    def test_exact_id_preexistence_expands_only_supported_population(self):
        with self._built() as built:
            value = built["value"]
            projection = built["projection"]
            self.assertEqual(value["contract"], INPUT_CONTRACT)
            self.assertEqual(projection["contract"], PROJECTION_CONTRACT)
            self.assertEqual(
                value["eligibility_controls"],
                {
                    **value["eligibility_controls"],
                    "catalog_variant_count": 5,
                    "parent_first_day_supported_count": 1,
                    "added_prewindow_exact_id_count": 1,
                    "eligible_variant_count": 2,
                    "missing_evidence_variant_count": 3,
                },
            )
            decisions = {
                item["shopify_variant_id"]: item
                for item in value["eligibility_ledger"]
            }
            self.assertEqual(decisions["100"]["status"], "ELIGIBLE")
            self.assertEqual(decisions["200"]["status"], "ELIGIBLE")
            self.assertEqual(
                decisions["300"]["blocker_reason"],
                "VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED",
            )
            self.assertEqual(
                decisions["400"]["blocker_reason"],
                "VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED",
            )
            self.assertEqual(
                decisions["500"]["blocker_reason"],
                "EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE",
            )
            self.assertEqual(
                [item["shopify_variant_id"] for item in value["added_variants"]],
                ["200"],
            )
            self.assertTrue(
                all(
                    observation["inventory_state"] == "UNKNOWN"
                    for observation in value["added_variants"][0]["observations"]
                )
            )
            expected_primary = {
                "CALCULATED": 2,
                "BLOCKED": 1,
                "NOT_APPLICABLE": 2,
                "NOT_PROCESSED": 0,
            }
            for counts in projection["forecast_primary_status_counts"].values():
                self.assertEqual(
                    {key: counts[key] for key in expected_primary},
                    expected_primary,
                )
                self.assertLessEqual(counts["numerical_zero"], 2)
            owners = {
                item["shopify_variant_id"]: item
                for item in projection["owner_worksheet"]
            }
            self.assertEqual(len(owners), 5)
            self.assertEqual(owners["100"]["next_missing_stage"]["stage"], "ABC")
            self.assertEqual(owners["200"]["next_missing_stage"]["stage"], "ABC")
            self.assertEqual(owners["300"]["next_missing_stage"]["stage"], "CAPTURE")
            self.assertEqual(owners["500"]["next_missing_stage"]["stage"], "CAPTURE")
            self.assertEqual(
                owners["300"]["scenario_results"]["H3"]["primary_status"],
                "NOT_APPLICABLE",
            )
            self.assertEqual(
                owners["500"]["scenario_results"]["H3"]["primary_status"],
                "BLOCKED",
            )
            self.assertEqual(
                owners["500"]["recent_observed_sales"]["windows"]["D7"][
                    "identity_scope"
                ],
                "NO_SUPPORTED_WINDOW_EXISTENCE_OR_RECORDED_ROWS",
            )
            self.assertEqual(
                owners["100"]["unapproved_supplier_offer_summary"]["preview"][0][
                    "supplier_sku"
                ],
                "=DANGEROUS-SKU",
            )
            self.assertEqual(
                owners["100"]["unapproved_supplier_offer_summary"]["preview"][0][
                    "selection_status"
                ],
                "NOT_RUN_NO_SELECTION_AUTHORITY",
            )
            categories = [
                item["category"]
                for item in projection["grouped_decision_queue"]["categories"]
            ]
            self.assertEqual(
                categories,
                [
                    "MACHINE_FIXABLE_DEFECTS",
                    "RETRIEVABLE_MISSING_SOURCES",
                    "GENUINELY_OWNER_SPECIFIC_UNANSWERED_FACTS",
                    "FUTURE_RELEASE_AUTHORITY",
                ],
            )
            queue_json = json.dumps(projection["grouped_decision_queue"])
            self.assertNotIn("VARIANT_CREATED_AFTER_HISTORY_START", queue_json)

    def test_input_and_projection_tampering_fail_closed(self):
        with self._built() as built:
            value = built["value"]
            projection = built["projection"]
            forged_input = deepcopy(dict(value))
            forged_input["added_variants"][0]["observations"][-1]["net_units"] = "99"
            forged_input["added_variants"][0]["observations_sha256"] = (
                private_research_v3._sha(
                    forged_input["added_variants"][0]["observations"]
                )
            )
            forged_input["eligibility_controls"]["added_variants_sha256"] = (
                private_research_v3._sha(forged_input["added_variants"])
            )
            forged_input["input_id"] = None
            forged_input["input_id"] = private_research_v3._sha(forged_input)
            with self.assertRaisesRegex(
                PrivateResearchV3Error, "not source-authenticated"
            ):
                build_private_v3_research_projection(
                    forged_input, built["parent"], built["base"]
                )

            captured_combined = []

            def fail_combined(combined, *_args, **_kwargs):
                captured_combined.append(combined)
                raise RuntimeError("synthetic combined projection failure")

            with patch.object(
                private_research_v3,
                "_build_private_research_projection_from_validated_input",
                new=fail_combined,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "synthetic combined projection failure"
                ):
                    build_private_v3_research_projection(
                        value, built["parent"], built["base"]
                    )
            self.assertEqual(captured_combined, [{}])

            forged_projection = deepcopy(projection)
            ledger = forged_projection["forecast_research"]["history"][
                "eligibility_ledger"
            ]
            ledger.append(deepcopy(ledger[0]))
            controls = forged_projection["forecast_research"]["history"][
                "eligibility_controls"
            ]
            controls["eligibility_ledger_sha256"] = private_research_v3._sha(
                ledger
            )
            self._reseal_projection(forged_projection)
            with self.assertRaises(PrivateResearchProjectionError):
                private_research_projection_sha256(forged_projection)

            forged_contract = deepcopy(projection)
            forged_contract["forecast_research"]["history"][
                "eligibility_controls"
            ]["contract"] = "FORGED_ELIGIBILITY_CONTRACT"
            self._reseal_projection(forged_contract)
            with self.assertRaises(PrivateResearchProjectionError):
                private_research_projection_sha256(forged_contract)

            forged_owner = deepcopy(projection)
            forged_owner["owner_worksheet"][0][
                "unapproved_supplier_offer_summary"
            ]["all_hypotheses_sha256"] = "f" * 64
            self._reseal_projection(forged_owner)
            with self.assertRaises(PrivateResearchProjectionError):
                private_research_projection_sha256(forged_owner)

            forged_recent = deepcopy(projection)
            history = forged_recent["forecast_research"]["history"]
            fake_window = history["recent_observed_sales_by_variant"]["500"][
                "windows"
            ]["D7"]
            fake_window.update(
                {
                    "coverage_status": "COMPLETE_ATTESTED_DAILY_QUERY_AND_EXISTENCE_SCOPE",
                    "complete_day_count": 7,
                    "existence_basis": "FABRICATED",
                    "net_units": "0",
                    "net_revenue": "0",
                }
            )
            history["eligibility_controls"][
                "recent_observed_sales_sha256"
            ] = private_research_v3._sha(
                history["recent_observed_sales_by_variant"]
            )
            next(
                item
                for item in forged_recent["owner_worksheet"]
                if item["shopify_variant_id"] == "500"
            )["recent_observed_sales"] = deepcopy(
                history["recent_observed_sales_by_variant"]["500"]
            )
            self._reseal_projection(forged_recent)
            with self.assertRaises(PrivateResearchProjectionError):
                private_research_projection_sha256(forged_recent)

            forged_units = deepcopy(projection)
            history = forged_units["forecast_research"]["history"]
            changed_recent = history["recent_observed_sales_by_variant"]["100"]
            changed_recent["windows"]["D7"]["net_units"] = "999999"
            history["eligibility_controls"][
                "recent_observed_sales_sha256"
            ] = private_research_v3._sha(
                history["recent_observed_sales_by_variant"]
            )
            next(
                item
                for item in forged_units["owner_worksheet"]
                if item["shopify_variant_id"] == "100"
            )["recent_observed_sales"] = deepcopy(changed_recent)
            self._reseal_projection(forged_units)
            with self.assertRaises(PrivateResearchProjectionError):
                private_research_projection_sha256(forged_units)

            forged_coverage = deepcopy(projection)
            forged_coverage["coverage_rows"][0][
                "primary_forecast_status"
            ] = "NOT_APPLICABLE"
            self._reseal_projection(forged_coverage)
            with self.assertRaises(PrivateResearchProjectionError):
                private_research_projection_sha256(forged_coverage)

    def test_blocked_evaluation_is_forecast_stage_and_not_numerical_zero(self):
        with self._built() as built, patch.object(
            private_research_v2,
            "plan_development_forecast",
            side_effect=DevelopmentForecastError("synthetic refusal"),
        ):
            projection = build_private_v3_research_projection(
                built["value"], built["parent"], built["base"]
            )
            owners = {
                item["shopify_variant_id"]: item
                for item in projection["owner_worksheet"]
            }
            for variant_id in ("100", "200"):
                self.assertEqual(
                    owners[variant_id]["next_missing_stage"]["stage"],
                    "FORECAST",
                )
            for counts in projection["forecast_primary_status_counts"].values():
                self.assertEqual(counts["CALCULATED"], 0)
                self.assertEqual(counts["BLOCKED"], 3)
                self.assertEqual(counts["NOT_APPLICABLE"], 2)
                self.assertEqual(counts["numerical_zero"], 0)

    def test_compact_exports_are_one_row_per_variant_and_safe(self):
        with self._built() as built:
            projection = built["projection"]
            html = render_private_research_html(projection)
            csv_text = render_private_research_csv(projection)
            self.assertIn("Unapproved supplier/offer summary", html)
            self.assertIn("=Product &lt;100&gt;", html)
            self.assertNotIn("<script>not executable</script>", html)
            rows = list(csv.DictReader(io.StringIO(csv_text)))
            owner_rows = [row for row in rows if row["row_type"] == "OWNER_WORKSHEET"]
            self.assertEqual(len(owner_rows), 5)
            self.assertEqual(
                {row["shopify_variant_id"] for row in owner_rows},
                {"100", "200", "300", "400", "500"},
            )
            self.assertTrue(owner_rows[0]["product_title"].startswith("'="))
            self.assertIn("=DANGEROUS-SKU", csv_text)
            self.assertEqual(
                filter_private_research_rows(projection, query="Product 200"),
                [
                    row
                    for row in projection["owner_worksheet"]
                    if "Product 200" in row["product_title"]
                ],
            )
            owner_100 = next(
                row
                for row in projection["owner_worksheet"]
                if row["shopify_variant_id"] == "100"
            )
            self.assertEqual(
                filter_private_research_rows(projection, query="=DANGEROUS-SKU"),
                [owner_100],
            )

            projection_bytes = canonical_private_research_projection_bytes(
                projection
            )
            artifacts = {
                "coverage.json": b"{\"synthetic_v3\":true}\n",
                "owner-preview.html": html.encode("utf-8"),
                "owner-worksheet.csv": csv_text.encode("utf-8"),
                "projection.json": projection_bytes,
            }
            media_types = {
                "coverage.json": "application/json",
                "owner-preview.html": "text/html; charset=utf-8",
                "owner-worksheet.csv": "text/csv; charset=utf-8",
                "projection.json": "application/json",
            }
            manifest = {
                "contract": "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3",
                "data_mode": "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
                "projection_contract": PROJECTION_CONTRACT,
                "base_intake_id": "a" * 64,
                "base_intake_sha256": "b" * 64,
                "workspace_id": "c" * 64,
                "projection_sha256": projection["projection_sha256"],
                "artifacts": [
                    {
                        "name": name,
                        "path": name,
                        "bytes": len(data),
                        "sha256": _sha(data),
                        "media_type": media_types[name],
                    }
                    for name, data in sorted(artifacts.items())
                ],
            }
            expectations = _load_browser_tool()._build_expectations(
                {
                    "manifest": manifest,
                    "projection": projection,
                    "artifacts": artifacts,
                }
            )
            self.assertEqual(
                expectations["projection_endpoint"],
                {
                    "bytes": len(projection_bytes),
                    "sha256": _sha(projection_bytes),
                },
            )
            reordered_projection = dict(reversed(tuple(projection.items())))
            reordered_expectations = _load_browser_tool()._build_expectations(
                {
                    "manifest": manifest,
                    "projection": reordered_projection,
                    "artifacts": artifacts,
                }
            )
            self.assertEqual(
                reordered_expectations["projection_endpoint"],
                expectations["projection_endpoint"],
            )
            self.assertIn(
                "unapproved_supplier_offer_summary",
                expectations["evidence"]["required_fields"],
            )
            self.assertIn(
                "Unapproved supplier/offer summary:",
                expectations["evidence"]["visible_markers"],
            )

    def test_private_write_workspace_replay_and_source_path_guards(self):
        with self._built() as built, tempfile.TemporaryDirectory() as directory:
            private_root = Path(directory).resolve()
            private_root.chmod(0o700)
            with patch(
                "procurement_os.private_research_v2.read_private_research_intake",
                return_value=built["base"],
            ), patch(
                "procurement_os.private_research.read_private_research_intake",
                return_value=built["base"],
            ):
                write_private_v2_research_input(
                    private_root,
                    built["parent"],
                    source_capture_paths=built["sources"],
                )
                with patch.object(
                    private_research_v3,
                    "read_private_v3_research_bundle",
                    side_effect=AssertionError("duplicate semantic replay"),
                ):
                    written = write_private_v3_research_input(
                        private_root,
                        built["value"],
                        seed_manifest_path=built["manifest_path"],
                        seed_variants_path=built["variants_path"],
                    )
                manifest = _build_private_v3_research_workspace_from_verified(
                    private_root,
                    written,
                    built["parent"],
                    built["base"],
                )
                workspace = (
                    private_root
                    / "private-research"
                    / "workspaces"
                    / manifest["workspace_id"]
                )
                self.assertEqual(
                    read_private_research_workspace_structural(workspace)["manifest"],
                    manifest,
                )
                validator = private_research_v3.validate_private_v3_projection
                with patch.object(
                    private_research_v3,
                    "validate_private_v3_projection",
                    wraps=validator,
                ) as validate_projection:
                    self.assertEqual(
                        read_private_research_workspace(workspace)["manifest"],
                        manifest,
                    )
                    cold_validation_count = validate_projection.call_count
                    self.assertGreater(cold_validation_count, 0)
                    self.assertEqual(
                        read_private_research_workspace(workspace)["manifest"],
                        manifest,
                    )
                    self.assertGreater(
                        validate_projection.call_count,
                        cold_validation_count,
                    )
                replayed, parent, base = read_private_v3_research_bundle(
                    private_root, written["input_id"]
                )
                self.assertEqual(replayed, written)
                self.assertEqual(parent, built["parent"])
                self.assertEqual(base, built["base"])

                projection_path = workspace / "projection.json"
                projection_path.chmod(0o644)
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError, "object differs"
                ):
                    read_private_research_workspace_structural(workspace)

            link = built["root"] / "seed-link.csv"
            link.symlink_to(built["variants_path"])
            with self.assertRaisesRegex(PrivateResearchV3Error, "path differs"):
                build_private_v3_research_input(
                    built["base"],
                    parent_v2_input=built["parent"],
                    seed_manifest_path=built["manifest_path"],
                    seed_variants_path=link,
                )
            ancestor_link = built["root"] / "seed-directory-link"
            ancestor_link.symlink_to(built["root"], target_is_directory=True)
            with self.assertRaisesRegex(PrivateResearchV3Error, "path differs"):
                build_private_v3_research_input(
                    built["base"],
                    parent_v2_input=built["parent"],
                    seed_manifest_path=built["manifest_path"],
                    seed_variants_path=ancestor_link / "variants.csv",
                )


if __name__ == "__main__":
    unittest.main()
