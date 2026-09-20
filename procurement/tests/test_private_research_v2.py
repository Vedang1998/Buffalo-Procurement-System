"""Pure acceptance tests for the private Development Forecast V2 connection."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_private_research_projection import intake, variant

from procurement_os.development_forecast import (
    CONTRACT as V1_CONTRACT,
    V2_CONTRACT,
    DevelopmentForecastError,
    development_forecast_definition,
    validate_development_forecast_evidence,
)
from procurement_os.private_research_projection import (
    CALCULATED_RESEARCH_ONLY,
    PrivateResearchProjectionError,
    REAL_NUMERICAL_EVALUATION_NOT_RUN,
    build_private_research_projection,
    filter_private_research_rows,
    private_research_projection_sha256,
)
from procurement_os.private_research_v2 import (
    COMPOSITE_HISTORY_CONTRACT,
    INPUT_CONTRACT,
    PROJECTION_CONTRACT,
    SOURCE_IDENTITY_CONTRACT,
    PrivateResearchV2Error,
    build_private_v2_composite_history,
    build_private_v2_research_input,
    build_private_v2_research_projection,
    build_source_identity_record,
    read_private_v2_research_input,
    validate_private_v2_research_input,
    write_private_v2_research_input,
)
from procurement_os.private_research import (
    build_private_v2_research_workspace,
    read_private_research_workspace,
)
import procurement_os.private_research_v2 as private_research_v2
from procurement_os.private_research_app import _stockout_evidence_status


ROOT = Path(__file__).resolve().parents[2]
SEED_ALIASES = ROOT / "procurement" / "seed" / "variant_aliases.csv"
ORIGINAL_AUTHORITY = (
    ROOT / "procurement" / "review" / "phase4_identity_manifest_corrected.csv"
)
TERMINAL_AUTHORITY = (
    ROOT / "procurement" / "review" / "phase4_terminal_disposition_manifest.csv"
)
QUERY = (
    "FROM sales SHOW net_items_sold, gross_sales, returns, net_sales, "
    "cost_of_goods_sold, gross_profit GROUP BY product_id, product_variant_id, "
    "product_title, product_variant_title TIMESERIES day SINCE {date} UNTIL {date}"
)
COLUMNS = [
    {"name": "day", "dataType": "DAY_TIMESTAMP"},
    {"name": "product_id", "dataType": "IDENTITY"},
    {"name": "product_variant_id", "dataType": "IDENTITY"},
    {"name": "product_title", "dataType": "STRING"},
    {"name": "product_variant_title", "dataType": "STRING"},
    {"name": "net_items_sold", "dataType": "INTEGER"},
    {"name": "gross_sales", "dataType": "MONEY"},
    {"name": "returns", "dataType": "MONEY"},
    {"name": "net_sales", "dataType": "MONEY"},
    {"name": "cost_of_goods_sold", "dataType": "MONEY"},
    {"name": "gross_profit", "dataType": "MONEY"},
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture(
    start: date,
    count: int,
    *,
    contract: str,
    unit_overrides: dict[str, str] | None = None,
    include_late_variant: bool = False,
) -> dict[str, object]:
    unit_overrides = unit_overrides or {}
    days: list[dict[str, object]] = []
    for offset in range(count):
        day = (start + timedelta(days=offset)).isoformat()
        rows = [
            [
                day,
                "10",
                "100",
                "Product 100",
                "750ML",
                unit_overrides.get(day, "2"),
                "20.00",
                "0.00",
                "20.00",
                "10.00",
                "10.00",
            ]
        ]
        if include_late_variant and offset > 0:
            rows.append(
                [day, "20", "200", "Late product", "750ML", "1", "10", "0", "10", "5", "5"]
            )
        days.append(
            {
                "business_date": day,
                "requested_at_utc": f"{day}T12:00:00Z",
                "received_at_utc": f"{day}T12:00:01Z",
                "result": {
                    "query": QUERY.format(date=day),
                    "columns": COLUMNS,
                    "rows": rows,
                    "rowCount": len(rows),
                    "chartHint": {},
                    "summaryMetric": {},
                    "shopDomain": "example.myshopify.com",
                },
            }
        )
    source = {
        "connector": "ALREADY_CONNECTED_SHOPIFY_READ_ONLY_ANALYTICS",
        "shop": (
            {
                "domain": "example.com",
                "currency_code": "USD",
                "timezone": "EDT",
                "country": "United States",
            }
            if contract == "BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_EXTENSION_V2"
            else {
                "shop_gid": "gid://shopify/Shop/1",
                "myshopify_domain": "example.myshopify.com",
                "primary_domain": "example.com",
                "currency_code": "USD",
                "timezone": "America/New_York",
            }
        ),
    }
    payload = {
        "contract": contract,
        "authority": (
            "PRIVATE_REAL_SOURCE_REVIEW_ONLY"
            if contract == "BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_EXTENSION_V2"
            else "PRIVATE_REAL_SOURCE_REVIEW_ONLY_NO_OPERATIONAL_AUTHORITY"
        ),
        "approval_state": "PROPOSED_UNAPPROVED_REVIEW_ONLY",
        "source": source,
        "days": days,
    }
    if contract == "BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_REPLACEMENT_V2":
        source.update(
            {
                "query_template": QUERY.format(date="{business_date}"),
                "first_complete_business_date": start.isoformat(),
                "last_complete_business_date": (start + timedelta(days=count - 1)).isoformat(),
                "business_days": count,
                "one_query_per_business_date": True,
                "population": "ALL_VARIANT_GROUPS_RETURNED_BY_UNFILTERED_DAILY_SALES_QUERY",
                "absent_variant_day_semantics": "OBSERVED_ZERO_ONLY_FOR_DAYS_WITH_A_RECORDED_COMPLETE_QUERY_AND_ESTABLISHED_ITEM_SCOPE; NEVER IMPUTE AN UNQUERIED_OR_PRE_EXISTENCE_DAY",
            }
        )
        payload["controls"] = {
            "day_count": 84,
            "row_count": sum(day["result"]["rowCount"] for day in days),
            "every_response_success": True,
            "every_response_same_shop_domain": True,
            "every_declared_row_count_matches": True,
        }
        payload["limitations"] = [
            "READ_ONLY_PRIVATE_DEVELOPMENT_RESEARCH_SOURCE",
            "SEPARATE_REPLACEMENT_CAPTURE_NOT_A_REWRITE_OF_V1",
            "NO_STOCKOUT_OR_INCOMING_EVIDENCE",
            "NO_PRODUCTION_FORECAST_OR_PURCHASING_AUTHORITY",
        ]
    return payload


def source_identity(extension_sha: str, replacement_sha: str) -> dict[str, object]:
    evidence = [
        {
            "kind": "ADMIN_SHOP_IDENTITY",
            "observed_at_utc": "2026-09-20T19:43:37Z",
            "reference": "admin-shop-query",
            "reference_sha256": "a" * 64,
            "observed_domain": "example.myshopify.com",
            "binding_basis": "ACCESS_TOKEN_SHOP_NODE",
        },
        {
            "kind": "CONNECTED_SHOP_INFO",
            "observed_at_utc": "2026-09-20T19:43:38Z",
            "reference": "shop-info-query",
            "reference_sha256": "b" * 64,
            "observed_domain": "example.com",
            "binding_basis": "SAME_CONNECTED_CONTEXT",
        },
        {
            "kind": "CONNECTED_ANALYTICS_PROBE",
            "observed_at_utc": "2026-09-20T19:43:39Z",
            "reference": "analytics-probe",
            "reference_sha256": "c" * 64,
            "observed_domain": "example.myshopify.com",
            "binding_basis": "SAME_CONNECTED_CONTEXT",
        },
        {
            "kind": "HISTORY_EXTENSION_54D",
            "observed_at_utc": "2026-09-20T18:18:02Z",
            "reference": "extension.json",
            "reference_sha256": extension_sha,
            "observed_domain": "example.myshopify.com",
            "binding_basis": "EVERY_RAW_RESPONSE_ENVELOPE",
        },
        {
            "kind": "HISTORY_REPLACEMENT_84D",
            "observed_at_utc": "2026-09-20T19:47:00Z",
            "reference": "replacement.json",
            "reference_sha256": replacement_sha,
            "observed_domain": "example.myshopify.com",
            "binding_basis": "EVERY_RAW_RESPONSE_ENVELOPE",
        },
    ]
    return build_source_identity_record(
        verdict="VERIFIED_SAME_SHOP",
        observed_at_utc="2026-09-20T19:44:00Z",
        shop={
            "shop_gid": "gid://shopify/Shop/1",
            "myshopify_domain": "example.myshopify.com",
            "primary_domain": "example.com",
            "currency_code": "USD",
            "timezone": "America/New_York",
        },
        evidence=evidence,
        limitations=["TEST_ONLY_SYNTHETIC_SOURCE_EVIDENCE"],
    )


class PrivateResearchV2Tests(unittest.TestCase):
    def setUp(self):
        self.registered_source_validator = (
            private_research_v2._require_registered_source_identity
        )
        patcher = patch.object(
            private_research_v2,
            "_require_registered_source_identity",
            side_effect=private_research_v2.validate_source_identity_record,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _source_files(self, root: Path, **changes: object):
        extension = capture(
            date(2026, 5, 4),
            54,
            contract="BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_EXTENSION_V2",
            **changes,
        )
        replacement = capture(
            date(2026, 6, 27),
            84,
            contract="BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_REPLACEMENT_V2",
            **changes,
        )
        extension_path = root / "extension.json"
        replacement_path = root / "replacement.json"
        for path, payload in ((extension_path, extension), (replacement_path, replacement)):
            path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            path.chmod(0o600)
        identity = source_identity(
            sha(extension_path.read_bytes()), sha(replacement_path.read_bytes())
        )
        return extension_path, replacement_path, identity

    def _rewrite_capture(
        self,
        extension_path: Path,
        replacement_path: Path,
        *,
        kind: str,
        mutate,
    ):
        path = extension_path if kind == "HISTORY_EXTENSION_54D" else replacement_path
        payload = json.loads(path.read_bytes())
        mutate(payload)
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return source_identity(
            sha(extension_path.read_bytes()), sha(replacement_path.read_bytes())
        )

    def _build(self, root: Path, **changes: object):
        base = intake([variant("100"), variant("200")])
        extension, replacement, identity = self._source_files(root, **changes)
        composite = build_private_v2_composite_history(
            base,
            extension_capture_path=extension,
            replacement_capture_path=replacement,
            source_identity=identity,
            seed_alias_path=SEED_ALIASES,
            original_authority_path=ORIGINAL_AUTHORITY,
            terminal_authority_path=TERMINAL_AUTHORITY,
        )
        value = build_private_v2_research_input(
            base, composite_history=composite, source_identity=identity
        )
        return base, identity, composite, value, {
            "HISTORY_EXTENSION_54D": extension,
            "HISTORY_REPLACEMENT_84D": replacement,
        }

    def test_explicit_v2_dispatch_runs_three_unknown_availability_scenarios(self):
        with tempfile.TemporaryDirectory() as directory:
            base, _, composite, value, _ = self._build(Path(directory))
        self.assertEqual(composite["contract"], COMPOSITE_HISTORY_CONTRACT)
        self.assertEqual(value["contract"], INPUT_CONTRACT)
        self.assertEqual(value["policy"]["evidence_contract"], V2_CONTRACT)
        self.assertEqual(
            value["policy"]["method_version"],
            development_forecast_definition(V2_CONTRACT).method_version,
        )
        self.assertEqual(len(value["variants"]), 1)
        self.assertTrue(
            all(
                observation["inventory_state"] == "UNKNOWN"
                for observation in value["variants"][0]["observations"]
            )
        )
        self.assertTrue(
            all("incoming" not in item for item in value["variants"])
        )
        legacy_evidence = {
            "coverage_complete": True,
            "horizon_days": 3,
            "observations": [
                {
                    "business_date": item["business_date"],
                    "net_units": item["net_units"],
                    "inventory_state": item["inventory_state"],
                }
                for item in value["variants"][0]["observations"]
            ],
        }
        legacy_red = build_private_research_projection(
            intake([variant("100", forecast_evidence=legacy_evidence)])
        )
        self.assertEqual(
            legacy_red["research_rows"][0]["forecast"]["status"],
            REAL_NUMERICAL_EVALUATION_NOT_RUN,
        )
        self.assertEqual(
            legacy_red["research_rows"][0]["forecast"]["reason_codes"],
            ["FORECAST_HISTORY_NOT_EXACT_84_DAYS"],
        )
        legacy_evidence["observations"] = legacy_evidence["observations"][:84]
        legacy_green = build_private_research_projection(
            intake([variant("100", forecast_evidence=legacy_evidence)])
        )
        self.assertEqual(
            legacy_green["research_rows"][0]["forecast"]["status"],
            CALCULATED_RESEARCH_ONLY,
        )
        self.assertEqual(
            legacy_green["research_rows"][0]["forecast"]["method_version"],
            development_forecast_definition(V1_CONTRACT).method_version,
        )
        projection = build_private_v2_research_projection(value, base)
        self.assertEqual(projection["contract"], PROJECTION_CONTRACT)
        scenarios = projection["owner_worksheet"][0]["scenario_results"]
        self.assertEqual(set(scenarios), {"H3", "H10", "H17"})
        self.assertEqual(
            {key: result["point_forecast_units"] for key, result in scenarios.items()},
            {"H3": "6.0000", "H10": "20.0000", "H17": "34.0000"},
        )
        self.assertTrue(all(result["confidence"] == "LOW" for result in scenarios.values()))
        self.assertIn(
            "UNKNOWN_AVAILABILITY_NOT_ASSUMED_IN_STOCK",
            projection["research_rows"][0]["forecast"]["reason_codes"],
        )
        self.assertTrue(
            all(
                validate_development_forecast_evidence(
                    sidecar["forecast_evidence"]
                )
                for sidecar in projection["forecast_sidecars"].values()
            )
        )
        self.assertEqual(
            projection["owner_worksheet"][0]["stage_status"]["NET_NEED"],
            "BLOCKED:NET_NEED",
        )
        filtered = filter_private_research_rows(
            projection,
            query="Product 100",
            status="LOW",
        )
        self.assertEqual(filtered, [projection["owner_worksheet"][0]])
        self.assertEqual(
            _stockout_evidence_status(filtered[0]), "NOT_CAPTURED"
        )
        with patch(
            "procurement_os.private_research_v2.plan_development_forecast",
            side_effect=DevelopmentForecastError("synthetic typed refusal"),
        ):
            refused = build_private_v2_research_projection(value, base)
        self.assertTrue(
            all(
                bucket == {
                    "calculated": 0,
                    "blocked": 1,
                    "missing": 1,
                    "unprocessed": 0,
                    "numerical_zero": 0,
                }
                for bucket in refused["forecast_research_counts"].values()
            )
        )

    def test_source_binding_gap_overlap_and_forged_reference_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = intake([variant("100")])
            extension, replacement, identity = self._source_files(root)
            with self.assertRaisesRegex(
                PrivateResearchV2Error, "registered binding"
            ):
                self.registered_source_validator(identity)
            forged = deepcopy(identity)
            forged["evidence"][3]["reference_sha256"] = "f" * 64
            forged["evidence_sha256"] = hashlib.sha256(
                (json.dumps(forged["evidence"], sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest()
            forged["identity_id"] = None
            forged["identity_id"] = hashlib.sha256(
                (json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest()
            with self.assertRaises(PrivateResearchV2Error):
                build_private_v2_composite_history(
                    base,
                    extension_capture_path=extension,
                    replacement_capture_path=replacement,
                    source_identity=forged,
                    seed_alias_path=SEED_ALIASES,
                    original_authority_path=ORIGINAL_AUTHORITY,
                    terminal_authority_path=TERMINAL_AUTHORITY,
                )
            unresolved = deepcopy(identity)
            unresolved["verdict"] = "UNRESOLVED"
            unresolved["identity_id"] = None
            unresolved["identity_id"] = hashlib.sha256(
                (json.dumps(unresolved, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest()
            with self.assertRaises(PrivateResearchV2Error):
                build_private_v2_composite_history(
                    base,
                    extension_capture_path=extension,
                    replacement_capture_path=replacement,
                    source_identity=unresolved,
                    seed_alias_path=SEED_ALIASES,
                    original_authority_path=ORIGINAL_AUTHORITY,
                    terminal_authority_path=TERMINAL_AUTHORITY,
                )

            different = deepcopy(identity)
            different["verdict"] = "DIFFERENT_SHOP"
            different["identity_id"] = None
            different["identity_id"] = hashlib.sha256(
                (json.dumps(different, sort_keys=True, separators=(",", ":")) + "\n").encode()
            ).hexdigest()
            with self.assertRaisesRegex(PrivateResearchV2Error, "not verified"):
                build_private_v2_composite_history(
                    base,
                    extension_capture_path=extension,
                    replacement_capture_path=replacement,
                    source_identity=different,
                    seed_alias_path=SEED_ALIASES,
                    original_authority_path=ORIGINAL_AUTHORITY,
                    terminal_authority_path=TERMINAL_AUTHORITY,
                )

        mutations = {
            "gap": (
                "HISTORY_EXTENSION_54D",
                lambda payload: payload["days"][10].update(
                    {"business_date": payload["days"][11]["business_date"]}
                ),
            ),
            "overlap": (
                "HISTORY_REPLACEMENT_84D",
                lambda payload: payload["days"][0].update(
                    {"business_date": "2026-06-26"}
                ),
            ),
            "duplicate": (
                "HISTORY_REPLACEMENT_84D",
                lambda payload: (
                    payload["days"][0]["result"]["rows"].append(
                        deepcopy(payload["days"][0]["result"]["rows"][0])
                    ),
                    payload["days"][0]["result"].update(
                        {
                            "rowCount": len(
                                payload["days"][0]["result"]["rows"]
                            )
                        }
                    ),
                    payload["controls"].update(
                        {
                            "row_count": payload["controls"]["row_count"] + 1
                        }
                    ),
                ),
            ),
            "incompatible-columns": (
                "HISTORY_EXTENSION_54D",
                lambda payload: payload["days"][0]["result"]["columns"].append(
                    None
                ),
            ),
            "request-chronology": (
                "HISTORY_EXTENSION_54D",
                lambda payload: payload["days"][0].update(
                    {
                        "requested_at_utc": "2026-09-20T12:00:01.100000Z",
                        "received_at_utc": "2026-09-20T12:00:01Z",
                    }
                ),
            ),
            "query-scope": (
                "HISTORY_EXTENSION_54D",
                lambda payload: payload["days"][0]["result"].update(
                    {
                        "query": payload["days"][0]["result"]["query"]
                        + " WHERE product_id = 10"
                    }
                ),
            ),
        }
        for label, (kind, mutate) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base = intake([variant("100")])
                extension, replacement, _ = self._source_files(root)
                changed_identity = self._rewrite_capture(
                    extension,
                    replacement,
                    kind=kind,
                    mutate=mutate,
                )
                with self.assertRaises(PrivateResearchV2Error):
                    build_private_v2_composite_history(
                        base,
                        extension_capture_path=extension,
                        replacement_capture_path=replacement,
                        source_identity=changed_identity,
                        seed_alias_path=SEED_ALIASES,
                        original_authority_path=ORIGINAL_AUTHORITY,
                        terminal_authority_path=TERMINAL_AUTHORITY,
                    )

    def test_preexistence_is_not_padded_and_every_raw_row_is_disposed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, composite, value, _ = self._build(
                Path(directory), include_late_variant=True
            )
        self.assertEqual([row["shopify_variant_id"] for row in value["variants"]], ["100"])
        controls = composite["allocation_controls"]
        self.assertEqual(
            controls["raw_row_count"],
            controls["direct_current_row_count"]
            + controls["historically_allocated_row_count"]
            + controls["absent_current_target_row_count"]
            + controls["quarantined_row_count"],
        )
        self.assertEqual(controls["eligible_variant_count"], 1)
        self.assertEqual(controls["preexistence_unproven_variant_count"], 1)

        def add_identity_scope_rows(payload):
            result = payload["days"][0]["result"]
            template = result["rows"][0]
            for source_id in ("900", "901", "902", "903"):
                source = deepcopy(template)
                source[1] = str(1000 + int(source_id))
                source[2] = source_id
                source[3] = "Product 100"
                source[4] = "750ML"
                result["rows"].append(source)
            result["rowCount"] = len(result["rows"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scoped_base = intake([variant("100"), variant("200")])
            extension, replacement, _ = self._source_files(root)
            scoped_identity = self._rewrite_capture(
                extension,
                replacement,
                kind="HISTORY_EXTENSION_54D",
                mutate=add_identity_scope_rows,
            )
            authority = {
                "seed_aliases_sha256": "a" * 64,
                "seed_alias_row_count": 1,
                "original_phase4_sha256": "b" * 64,
                "original_phase4_row_count": 1,
                "terminal_phase4_sha256": "c" * 64,
                "terminal_phase4_row_count": 1,
                "terminal_safe_alias_family_count": 1,
                "combined_conflict_free_alias_count": 2,
                "approved_self_identity_count": 1,
                "source_key_specific_decisions_applied_to_extension": 0,
            }
            with patch.object(
                private_research_v2,
                "_authority_aliases",
                return_value=(
                    {"900": "200", "901": "999"},
                    {"902"},
                    authority,
                ),
            ):
                scoped = build_private_v2_composite_history(
                    scoped_base,
                    extension_capture_path=extension,
                    replacement_capture_path=replacement,
                    source_identity=scoped_identity,
                    seed_alias_path=SEED_ALIASES,
                    original_authority_path=ORIGINAL_AUTHORITY,
                    terminal_authority_path=TERMINAL_AUTHORITY,
                )
        dispositions = {
            row["source_variant_id"]: (
                row["disposition"],
                row["target_shopify_variant_id"],
            )
            for row in scoped["allocation_ledger"]
            if row["source_variant_id"] in {"900", "901", "902", "903"}
        }
        self.assertEqual(
            dispositions,
            {
                "900": ("HISTORICALLY_ALLOCATED", "200"),
                "901": ("ABSENT_CURRENT_TARGET", "999"),
                "902": ("ABSENT_CURRENT_TARGET", "902"),
                "903": ("QUARANTINED", None),
            },
        )
        scoped_controls = scoped["allocation_controls"]
        self.assertEqual(scoped_controls["historically_allocated_row_count"], 1)
        self.assertEqual(scoped_controls["absent_current_target_row_count"], 2)
        self.assertEqual(scoped_controls["quarantined_row_count"], 1)
        self.assertEqual(scoped_controls["eligible_variant_count"], 2)

    def test_history_change_is_causal_and_replay_is_deterministic(self):
        change_day = "2026-09-18"
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            base_a, _, _, value_a, _ = self._build(Path(a))
            base_b, _, _, value_b, _ = self._build(
                Path(b), unit_overrides={change_day: "20"}
            )
        projection_a = build_private_v2_research_projection(value_a, base_a)
        projection_b = build_private_v2_research_projection(value_b, base_b)
        self.assertNotEqual(value_a["input_id"], value_b["input_id"])
        self.assertNotEqual(
            projection_a["owner_worksheet"][0]["scenario_results"],
            projection_b["owner_worksheet"][0]["scenario_results"],
        )
        sidecars_a = {
            value["scenario"]["scenario_id"]: value["forecast_evidence"]
            for value in projection_a["forecast_sidecars"].values()
        }
        sidecars_b = {
            value["scenario"]["scenario_id"]: value["forecast_evidence"]
            for value in projection_b["forecast_sidecars"].values()
        }
        for scenario_id in ("H3", "H10", "H17"):
            self.assertEqual(
                sidecars_a[scenario_id]["selection_metrics"],
                sidecars_b[scenario_id]["selection_metrics"],
            )
            self.assertEqual(
                sidecars_a[scenario_id]["protection"],
                sidecars_b[scenario_id]["protection"],
            )
            self.assertNotEqual(
                sidecars_a[scenario_id]["evaluation_metrics"],
                sidecars_b[scenario_id]["evaluation_metrics"],
            )
        self.assertEqual(
            build_private_v2_research_projection(value_a, base_a), projection_a
        )

    def test_content_address_and_private_readback_reject_tamper(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as private:
            private_root = Path(private).resolve()
            private_root.chmod(0o700)
            base, identity, composite, value, sources = self._build(Path(source_dir))
            forged_composite = deepcopy(dict(composite))
            forged_composite["variants"][0]["observations"][-1][
                "net_units"
            ] = "200"
            forged_composite["variants"][0][
                "observations_sha256"
            ] = private_research_v2._sha(
                forged_composite["variants"][0]["observations"]
            )
            forged_composite["composite_id"] = None
            forged_composite["composite_id"] = private_research_v2._sha(
                forged_composite
            )
            with self.assertRaisesRegex(
                PrivateResearchV2Error, "not source-authenticated"
            ):
                build_private_v2_research_input(
                    base,
                    composite_history=forged_composite,
                    source_identity=identity,
                )
            forged_input = deepcopy(dict(value))
            forged_input["variants"][0]["observations"][-1]["net_units"] = "200"
            forged_input["variants"][0][
                "observations_sha256"
            ] = private_research_v2._sha(
                forged_input["variants"][0]["observations"]
            )
            forged_input["input_id"] = None
            forged_input["input_id"] = private_research_v2._sha(forged_input)
            with self.assertRaisesRegex(
                PrivateResearchV2Error, "not source-authenticated"
            ):
                build_private_v2_research_projection(forged_input, base)
            original_input = deepcopy(dict(value))
            value["variants"][0]["observations"][-1]["net_units"] = "200"
            value["variants"][0]["observations_sha256"] = private_research_v2._sha(
                value["variants"][0]["observations"]
            )
            value["input_id"] = None
            value["input_id"] = private_research_v2._sha(value)
            value._sealed_sha256 = private_research_v2._sha_bytes(
                private_research_v2._canonical(dict(value))
            )
            with self.assertRaisesRegex(
                PrivateResearchV2Error, "not source-authenticated"
            ):
                build_private_v2_research_projection(value, base)
            value.clear()
            value.update(original_input)
            with patch(
                "procurement_os.private_research_v2.read_private_research_intake",
                return_value=base,
            ):
                written = write_private_v2_research_input(
                    private_root, value, source_capture_paths=sources
                )
                self.assertEqual(written, value)
                self.assertEqual(
                    read_private_v2_research_input(private_root, value["input_id"]), value
                )
            with patch(
                "procurement_os.private_research.read_private_research_intake",
                return_value=base,
            ), patch(
                "procurement_os.private_research_v2.read_private_research_intake",
                return_value=base,
            ):
                manifest = build_private_v2_research_workspace(
                    private_root, value["input_id"]
                )
                workspace = (
                    private_root
                    / "private-research"
                    / "workspaces"
                    / manifest["workspace_id"]
                )
                replay = read_private_research_workspace(workspace)
            self.assertEqual(replay["manifest"], manifest)
            self.assertEqual(
                replay["projection"]["contract"], PROJECTION_CONTRACT
            )
            projection = replay["projection"]

            def reseal(changed):
                changed.pop("projection_sha256", None)
                changed["projection_sha256"] = private_research_v2._sha_bytes(
                    private_research_v2._projection_canonical(changed)
                )

            forged_policy = deepcopy(projection)
            forged_policy["forecast_research"]["policy"]["profile"] = (
                "unregistered-profile"
            )
            for sidecar in forged_policy["forecast_sidecars"].values():
                sidecar["policy"]["profile"] = "unregistered-profile"
                sidecar.pop("sidecar_sha256", None)
                sidecar["sidecar_sha256"] = private_research_v2._sha(sidecar)
            forged_policy["forecast_research"]["sidecars_sha256"] = (
                private_research_v2._sha(forged_policy["forecast_sidecars"])
            )
            reseal(forged_policy)
            with self.assertRaisesRegex(
                PrivateResearchProjectionError, "identity tuple"
            ):
                private_research_projection_sha256(forged_policy)

            forged_counts = deepcopy(projection)
            forged_counts["forecast_research_counts"]["H3"]["calculated"] += 99
            reseal(forged_counts)
            with self.assertRaisesRegex(
                PrivateResearchProjectionError, "result controls"
            ):
                private_research_projection_sha256(forged_counts)

            for field, changed_value in (
                (
                    "stage_status",
                    {
                        **projection["owner_worksheet"][0]["stage_status"],
                        "NET_NEED": "SUPPORTED",
                    },
                ),
                ("operational_effect", "ORDER_CREATED"),
                ("product_title", "forged product"),
                ("supplier_names", ["forged supplier"]),
                ("reason_codes", []),
            ):
                forged_owner = deepcopy(projection)
                forged_owner["owner_worksheet"][0][field] = changed_value
                reseal(forged_owner)
                with self.subTest(owner_field=field), self.assertRaisesRegex(
                    PrivateResearchProjectionError,
                    "owner identity or stage evidence",
                ):
                    private_research_projection_sha256(forged_owner)

            shifted = deepcopy(projection)
            for scenario in shifted["forecast_research"]["scenarios"]:
                scenario["contract"] = "UNREGISTERED_SCENARIO"
            for sidecar in shifted["forecast_sidecars"].values():
                sidecar["scenario"]["contract"] = "UNREGISTERED_SCENARIO"
                sidecar.pop("sidecar_sha256", None)
                sidecar["sidecar_sha256"] = private_research_v2._sha(sidecar)
            shifted["forecast_research"]["sidecars_sha256"] = (
                private_research_v2._sha(shifted["forecast_sidecars"])
            )
            reseal(shifted)
            with self.assertRaisesRegex(
                PrivateResearchProjectionError, "scenario differs"
            ):
                private_research_projection_sha256(shifted)
            tampered = deepcopy(value)
            tampered["history"]["complete_day_count"] = 137
            with self.assertRaises(PrivateResearchV2Error):
                validate_private_v2_research_input(tampered)


if __name__ == "__main__":
    unittest.main()
