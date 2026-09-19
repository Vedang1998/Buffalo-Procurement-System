#!/usr/bin/env python3
"""Owned Chromium acceptance for the synthetic two-vendor price-to-DRAFT flow."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal, ROUND_CEILING
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import zipfile

import psycopg

from procurement_os.development_forecast import (
    CONTRACT as DEVELOPMENT_FORECAST_CONTRACT,
    V2_CONTRACT as DEVELOPMENT_FORECAST_V2_CONTRACT,
    V2_METHOD_VERSION as DEVELOPMENT_FORECAST_V2_METHOD_VERSION,
    canonical_evidence_sha256,
    validate_development_baseline_need_context,
    validate_connected_development_forecast_context_evidence,
    validate_development_forecast_evidence,
)
from audit_local_purchasing_browser import (
    BrowserAcceptanceError,
    _free_port,
    _run_phase,
    _source_identity,
    _start_server,
    _stop_server,
    _wait_cdp,
)
from local_purchasing_candidate import (
    _local_database_url,
    _state_evidence,
    backup,
    backup_v2,
    initialize_local_database,
    initialize_runtime,
    local_database_status,
    restore_same_database,
    stop_local_database,
)


SAFETY_LABEL = "TEST DATA — NOT FOR ORDERING"
DATABASE = "buffalo_multivendor_acceptance_demo"
DEVELOPMENT_DATABASE = "buffalo_development_forecast_acceptance_demo"
DEVELOPMENT_PROFILE = "development-forecast-v1"
DEVELOPMENT_V2_DATABASE = "buffalo_development_forecast_v2_acceptance_demo"
DEVELOPMENT_V2_PROFILE = "development-forecast-v2"


def _forecast_expectations(profile: str) -> dict[str, Any] | None:
    if profile == DEVELOPMENT_PROFILE:
        return {
            "contract": DEVELOPMENT_FORECAST_CONTRACT,
            "method_version": "DEVELOPMENT_ROLLING_ORIGIN_V1",
            "history_days": 84,
            "southern_horizon": 10,
            "southern_cases": 3,
            "southern_total": "90.00",
            "merchandise_total": "192.00",
            "draft_total": "199.00",
        }
    if profile == DEVELOPMENT_V2_PROFILE:
        return {
            "contract": DEVELOPMENT_FORECAST_V2_CONTRACT,
            "method_version": DEVELOPMENT_FORECAST_V2_METHOD_VERSION,
            "history_days": 138,
            "southern_horizon": 17,
            "southern_cases": 6,
            "southern_total": "180.00",
            "merchandise_total": "282.00",
            "draft_total": "289.00",
        }
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _storage_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _artifact_group(state: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = state.get(key)
    if not isinstance(value, list) or len(value) != 3:
        raise BrowserAcceptanceError(f"{key} must contain exactly three downloads")
    hashes = [item.get("sha256") for item in value]
    if len(set(hashes)) != 3 or not all(
        isinstance(item, str) and len(item) == 64 for item in hashes
    ):
        raise BrowserAcceptanceError(f"{key} download identities differ")
    return value


def _validate_initial_downloads(
    downloads: Path,
    state: dict[str, Any],
    *,
    fixture_profile: str,
) -> dict[str, Any]:
    forecast = _forecast_expectations(fixture_profile)
    initial = _artifact_group(state, "artifactDownloads")
    files = [downloads / str(item["name"]) for item in initial]
    if not all(path.is_file() and _sha256(path) == item["sha256"] for path, item in zip(files, initial, strict=True)):
        raise BrowserAcceptanceError("initial browser download bytes differ")
    csv_files = [path for path in files if path.suffix == ".csv"]
    zip_files = [path for path in files if path.suffix == ".zip"]
    if (len(csv_files), len(zip_files)) != (2, 1):
        raise BrowserAcceptanceError("expected two vendor CSVs and one packet")
    by_vendor: dict[str, list[dict[str, str]]] = {}
    for path in csv_files:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise BrowserAcceptanceError("vendor CSV is empty")
        by_vendor[rows[0]["vendor_name"]] = rows
        for row in rows:
            if (
                row.get("safety_label") != SAFETY_LABEL
                or row.get("internal_output_contract")
                != "BUFFALO_INTERNAL_DRAFT_LINE_V2"
                or row.get("captured_available_quantity") != "0.0000"
                or row.get("inventory_captured_at")
                != "2026-10-05T12:00:00+00:00"
                or not row.get("source_inventory_snapshot_run_id")
            ):
                raise BrowserAcceptanceError("vendor CSV frozen-stock evidence differs")
            scope = json.loads(row["inventory_location_scope_json"])
            if not isinstance(scope, list) or len(scope) != 1:
                raise BrowserAcceptanceError("vendor CSV location evidence differs")

    southern = by_vendor.get("Synthetic Southern")
    western = by_vendor.get("Synthetic Western Acceptance")
    if southern is None or western is None or len(southern) != 1 or len(western) != 2:
        raise BrowserAcceptanceError("vendor CSV partition differs")
    expected_lines = (
        {
            "1001": (
                str(forecast["southern_cases"]),
                "0",
                "30.0000",
                forecast["southern_total"],
            ),
            "4001": ("2", "0", "42.0000", "84.00"),
            "4002": ("1", "0", "18.0000", "18.00"),
        }
        if forecast is not None
        else {
            "1001": ("2", "0", "30.0000", "60.00"),
            "4001": ("2", "0", "42.0000", "84.00"),
            "4002": ("1", "0", "18.0000", "18.00"),
        }
    )
    for row in southern + western:
        expected = expected_lines.get(row["variant_id"])
        actual = (
            row["cases"],
            row["loose_units"],
            row["case_price"],
            row["line_merchandise_total"],
        )
        if expected != actual:
            raise BrowserAcceptanceError(
                f"line economics differ for Variant {row['variant_id']}: {actual}"
            )
    western_merchandise = "102.00"
    western_total = "109.00"
    if any(
        row["vendor_po_total"] != western_total
        or row["vendor_merchandise_total"] != western_merchandise
        or row["vendor_below_minimum_fee"] != "7.00"
        or row["vendor_delivery_fee"] != "7.00"
        or row["minimum_disposition"] != "PAY_FEE"
        for row in western
    ):
        raise BrowserAcceptanceError("Western vendor-scoped fee economics differ")
    if southern[0]["vendor_po_total"] != (
        forecast["southern_total"] if forecast is not None else "60.00"
    ):
        raise BrowserAcceptanceError("Southern uploaded-price total differs")

    with zipfile.ZipFile(zip_files[0]) as archive:
        names = archive.namelist()
        embedded = sorted(name for name in names if name.endswith(".internal.csv"))
        expected_member_count = 14 if forecast is not None else 13
        if len(names) != expected_member_count or len(embedded) != 2:
            raise BrowserAcceptanceError("two-vendor packet member inventory differs")
        manifest = json.loads(archive.read("manifest.json"))
        if sorted(manifest.get("entries", {})) != sorted(
            name for name in names if name != "manifest.json"
        ):
            raise BrowserAcceptanceError("packet manifest inventory differs")
        for name, expected in manifest["entries"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise BrowserAcceptanceError("packet member hash differs")
        embedded_bytes = sorted(archive.read(name) for name in embedded)
        if embedded_bytes != sorted(path.read_bytes() for path in csv_files):
            raise BrowserAcceptanceError("embedded and downloaded CSV bytes differ")
        summary = json.loads(archive.read("packet-summary.json"))
        economics = summary.get("vendor_draft_economics")
        if not isinstance(economics, list) or len(economics) != 2:
            raise BrowserAcceptanceError("packet vendor economics differ")
        expected_merchandise = (
            forecast["merchandise_total"] if forecast is not None else "162.00"
        )
        expected_total = forecast["draft_total"] if forecast is not None else "169.00"
        economics_by_name = {str(item.get("vendor_name")): item for item in economics}
        southern_economics = economics_by_name.get("Synthetic Southern", {})
        western_economics = economics_by_name.get(
            "Synthetic Western Acceptance", {}
        )
        expected_southern = (
            forecast["southern_total"] if forecast is not None else "60.00"
        )
        if (
            str(summary.get("merchandise_total")) != expected_merchandise
            or str(summary.get("po_total")) != expected_total
            or str(southern_economics.get("merchandise_total"))
            != expected_southern
            or str(southern_economics.get("po_total")) != expected_southern
            or str(western_economics.get("merchandise_total")) != "102.00"
            or str(western_economics.get("delivery_fee")) != "7.00"
            or str(western_economics.get("po_total")) != "109.00"
        ):
            raise BrowserAcceptanceError("packet summary economics differ")
        forecast_member = None
        if forecast is not None:
            if "forecast-and-protection-evidence.json" not in names:
                raise BrowserAcceptanceError(
                    "development packet member is missing"
                )
            forecast_member = json.loads(
                archive.read("forecast-and-protection-evidence.json")
            )
            items = forecast_member.get("items")
            frozen_member = json.loads(archive.read("frozen-input-manifest.json"))
            frozen_manifest = frozen_member.get("input_manifest")
            if isinstance(frozen_manifest, str):
                frozen_manifest = json.loads(frozen_manifest)
            if (
                forecast_member.get("contract") != forecast["contract"]
                or forecast_member.get("commercial_authority") is not False
                or forecast_member.get("production_activation") is not False
                or not isinstance(items, list)
                or len(items) != 6
                or not isinstance(frozen_manifest, dict)
                or frozen_manifest.get("development_forecast_contract")
                != forecast["contract"]
                or frozen_manifest.get("method_version")
                != forecast["method_version"]
            ):
                raise BrowserAcceptanceError(
                    "development packet evidence shape differs"
                )
            contexts = frozen_manifest.get("contexts")
            if not isinstance(contexts, list) or len(contexts) != 6:
                raise BrowserAcceptanceError(
                    "development packet frozen context inventory differs"
                )
            context_by_variant = {
                str(context.get("variant_id")): context
                for context in contexts
                if isinstance(context, dict)
            }
            item_by_variant = {
                str(item.get("variant_id")): item
                for item in items
                if isinstance(item, dict)
            }
            expected_variants = {"1001", "4001", "4002", "4003", "4004", "4005"}
            if (
                set(context_by_variant) != expected_variants
                or set(item_by_variant) != expected_variants
            ):
                raise BrowserAcceptanceError(
                    "development packet Variant inventory differs"
                )
            for variant_id in sorted(expected_variants):
                context = context_by_variant[variant_id]
                item = item_by_variant[variant_id]
                status = context.get("development_forecast_status")
                if (
                    item.get("status") != status
                    or item.get("blockers") != context.get("blockers")
                ):
                    raise BrowserAcceptanceError(
                        f"development packet status differs for Variant {variant_id}"
                    )
                if status in {"READY", "BLOCKED"}:
                    evidence = context.get("development_forecast_evidence")
                    if (
                        not isinstance(evidence, dict)
                        or evidence.get("contract") != forecast["contract"]
                        or evidence.get("method_version")
                        != forecast["method_version"]
                        or item.get("evidence") != evidence
                        or item.get("evidence_sha256")
                        != context.get("development_forecast_evidence_sha256")
                        or not validate_connected_development_forecast_context_evidence(
                            evidence,
                            context.get("demand_observations"),
                            expected_contract=forecast["contract"],
                        )
                    ):
                        raise BrowserAcceptanceError(
                            f"development packet evidence differs for Variant {variant_id}"
                        )
                if status == "READY":
                    if (
                        item.get("calculated_need") != context.get("need")
                        or not validate_development_baseline_need_context(
                            context,
                            manifest_contract=forecast["contract"],
                        )
                    ):
                        raise BrowserAcceptanceError(
                            f"development packet need differs for Variant {variant_id}"
                        )
                    if fixture_profile == DEVELOPMENT_V2_PROFILE and (
                        item.get("calculated_need_binding")
                        != context.get("development_baseline_need_binding")
                    ):
                        raise BrowserAcceptanceError(
                            f"V2 packet need binding differs for Variant {variant_id}"
                        )
                elif status == "BLOCKED":
                    if (
                        item.get("calculated_need") is not None
                        or (
                            fixture_profile == DEVELOPMENT_V2_PROFILE
                            and item.get("calculated_need_binding") is not None
                        )
                    ):
                        raise BrowserAcceptanceError(
                            f"blocked packet need differs for Variant {variant_id}"
                        )
                elif status != "NOT_REACHED":
                    raise BrowserAcceptanceError(
                        f"development packet status is unknown for Variant {variant_id}"
                    )
            if fixture_profile == DEVELOPMENT_V2_PROFILE:
                southern_item = next(
                    (item for item in items if str(item.get("variant_id")) == "1001"),
                    None,
                )
                loose_item = next(
                    (item for item in items if str(item.get("variant_id")) == "4004"),
                    None,
                )
                if not isinstance(southern_item, dict):
                    raise BrowserAcceptanceError(
                        "V2 Southern packet evidence is missing"
                    )
                if not isinstance(loose_item, dict):
                    raise BrowserAcceptanceError(
                        "V2 loose-unit packet evidence is missing"
                    )
                evidence = southern_item.get("evidence")
                need = southern_item.get("calculated_need")
                binding = southern_item.get("calculated_need_binding")
                if (
                    not isinstance(evidence, dict)
                    or not isinstance(need, dict)
                    or not isinstance(binding, dict)
                    or evidence.get("point_forecast_units") != "34.0000"
                    or evidence.get("protection_units") != "0.0000"
                    or need.get("raw_need_units") != 34
                    or need.get("cases") != 6
                    or need.get("ordered_units") != 36
                    or need.get("pack_rounding_units") != 2
                    or binding.get("variant_id") != "1001"
                    or binding.get("forecast_evidence_sha256")
                    != evidence.get("sha256")
                    or binding.get("need_sha256")
                    != canonical_evidence_sha256(need)
                ):
                    raise BrowserAcceptanceError(
                        "V2 Southern forecast-to-need packet evidence differs"
                    )
                loose_evidence = loose_item.get("evidence")
                loose_need = loose_item.get("calculated_need")
                loose_blockers = loose_item.get("blockers")
                if (
                    not isinstance(loose_evidence, dict)
                    or not isinstance(loose_need, dict)
                    or loose_evidence.get("point_forecast_units") != "4.0000"
                    or loose_evidence.get("protection_units") != "0.0000"
                    or loose_evidence.get("target_units") != "4.0000"
                    or loose_need.get("raw_need_units") != 4
                    or loose_need.get("cases") != 0
                    or loose_need.get("loose_units") != 4
                    or loose_need.get("ordered_units") != 4
                    or loose_need.get("loose_fee") != "3"
                    or loose_blockers != [
                        "LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED"
                    ]
                ):
                    raise BrowserAcceptanceError(
                        "V2 loose-unit blocker evidence differs"
                    )
    return {
        "files": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in files
        ],
        "packet_members": sorted(names),
        "input_fingerprint": (
            hashlib.sha256(
                json.dumps(
                    frozen_manifest,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if forecast is not None
            else None
        ),
        "forecast_and_protection": forecast_member,
        "vendor_rows": by_vendor,
        "totals": (
            {
                "merchandise": forecast["merchandise_total"],
                "fees": "7.00",
                "total": forecast["draft_total"],
            }
            if forecast is not None
            else {"merchandise": "162.00", "fees": "7.00", "total": "169.00"}
        ),
    }


def _database_acceptance(
    database_url: str,
    run_id: str,
    *,
    fixture_profile: str,
) -> dict[str, Any]:
    forecast = _forecast_expectations(fixture_profile)
    with psycopg.connect(database_url) as conn:
        conn.execute("SET search_path TO qa_mapping_test,pg_catalog")
        counts = conn.execute(
            """SELECT
                 (SELECT count(*) FROM supplier_mapping_review_batches),
                 (SELECT count(*) FROM supplier_mapping_review_candidates),
                 (SELECT count(*) FROM supplier_mapping_decisions),
                 (SELECT count(*) FROM supplier_offer_selection_events),
                 (SELECT count(*) FROM supplier_offer_selection_heads),
                 (SELECT count(*) FROM procurement_recommendations WHERE run_id=%s),
                 (SELECT count(*) FROM purchase_orders WHERE run_id=%s),
                 (SELECT count(*) FROM purchase_order_lines l JOIN purchase_orders p USING(po_id)
                    WHERE p.run_id=%s),
                 (SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s)""",
            (run_id, run_id, run_id, run_id),
        ).fetchone()
        if tuple(int(value) for value in counts) != (3, 7, 7, 5, 5, 4, 2, 3, 3):
            raise BrowserAcceptanceError(f"durable workflow counts differ: {counts}")
        stage = conn.execute(
            """SELECT workflow_stage,model_version,procurement_input_manifest,
                      input_fingerprint
                 FROM runs WHERE run_id=%s""",
            (run_id,),
        ).fetchone()
        if stage is None or stage[0] != "PACKET_BUILT":
            raise BrowserAcceptanceError("workflow stage differs")
        if stage[1] != (
            forecast["method_version"]
            if forecast is not None
            else "EMERGENCY_TRANSPARENT_V2"
        ):
            raise BrowserAcceptanceError("run method version differs")
        raw_manifest = stage[2]
        if (
            not isinstance(raw_manifest, str)
            or hashlib.sha256(raw_manifest.encode("utf-8")).hexdigest() != stage[3]
        ):
            raise BrowserAcceptanceError("run input manifest differs")
        try:
            manifest = json.loads(raw_manifest)
        except (json.JSONDecodeError, TypeError) as exc:
            raise BrowserAcceptanceError("run input manifest differs") from exc
        if not isinstance(manifest, dict):
            raise BrowserAcceptanceError("run input manifest differs")
        manifest_contract = manifest.get("development_forecast_contract")
        manifest_contexts = {
            str(context.get("variant_id")): context
            for context in manifest.get("contexts", [])
            if isinstance(context, dict)
        }
        pos = conn.execute(
            """SELECT p.vendor_id::text,v.vendor_name,p.merchandise_total,
                      p.delivery_fee,p.po_total,count(l.po_line_id)
                 FROM purchase_orders p JOIN vendors v USING(vendor_id)
                 JOIN purchase_order_lines l USING(po_id)
                WHERE p.run_id=%s GROUP BY p.vendor_id,v.vendor_name,p.merchandise_total,
                     p.delivery_fee,p.po_total ORDER BY v.vendor_name""",
            (run_id,),
        ).fetchall()
        exact_pos = [
            (str(row[1]), str(row[2]), str(row[3]), str(row[4]), int(row[5]))
            for row in pos
        ]
        expected_pos = (
            [
                (
                    "Synthetic Southern",
                    forecast["southern_total"],
                    "0.00",
                    forecast["southern_total"],
                    1,
                ),
                ("Synthetic Western Acceptance", "102.00", "7.00", "109.00", 2),
            ]
            if forecast is not None
            else [
                ("Synthetic Southern", "60.00", "0.00", "60.00", 1),
                ("Synthetic Western Acceptance", "102.00", "7.00", "109.00", 2),
            ]
        )
        if exact_pos != expected_pos:
            raise BrowserAcceptanceError(f"durable vendor economics differ: {exact_pos}")
        variants = conn.execute(
            """SELECT l.variant_id,l.cases,l.loose_units,l.line_total
                 FROM purchase_order_lines l JOIN purchase_orders p USING(po_id)
                WHERE p.run_id=%s ORDER BY l.variant_id""",
            (run_id,),
        ).fetchall()
        expected_variants = (
            [
                (
                    "1001",
                    int(forecast["southern_cases"]),
                    0,
                    forecast["southern_total"],
                ),
                ("4001", 2, 0, "84.00"),
                ("4002", 1, 0, "18.00"),
            ]
            if forecast is not None
            else [("1001", 2, 0, "60.00"), ("4001", 2, 0, "84.00"), ("4002", 1, 0, "18.00")]
        )
        if [(str(v), int(c), int(loose), str(total)) for v,c,loose,total in variants] != expected_variants:
            raise BrowserAcceptanceError("durable line economics differ")
        blockers = conn.execute(
            """SELECT e.variant_id,e.message,x.action
                 FROM exceptions e JOIN monday_run_blocker_exclusions x USING(exception_id)
                WHERE e.run_id=%s ORDER BY e.variant_id""",
            (run_id,),
        ).fetchall()
        if [(str(a), str(b), str(c)) for a,b,c in blockers] != [
            ("4003", "ROUTINE_SELECTED_OFFER_HEAD_REQUIRED", "ACKNOWLEDGE_AND_EXCLUDE"),
            ("4004", "LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED", "ACKNOWLEDGE_AND_EXCLUDE"),
        ]:
            raise BrowserAcceptanceError(f"frozen blocker set differs: {blockers}")
        rejected = conn.execute(
            """SELECT r.variant_id,d.action FROM procurement_recommendations r
                 JOIN review_decisions d USING(recommendation_id)
                WHERE r.run_id=%s ORDER BY r.variant_id""",
            (run_id,),
        ).fetchall()
        expected_reviews = [
            ("1001", "ACCEPT" if forecast is not None else "EDIT_QUANTITY"),
            ("4001", "EDIT_QUANTITY"), ("4002", "ACCEPT"), ("4005", "REJECT"),
        ]
        if [(str(a), str(b)) for a,b in rejected] != expected_reviews:
            raise BrowserAcceptanceError("review dispositions differ")
        artifacts = conn.execute(
            """SELECT artifact_type,vendor_id::text,sha256,size_bytes
                 FROM monday_run_artifacts WHERE run_id=%s
                ORDER BY artifact_type,vendor_id NULLS LAST""",
            (run_id,),
        ).fetchall()
        forecast_rows = conn.execute(
            """SELECT f.variant_id,f.selected_model,f.demand_regime,f.xyz_class,
                      f.forecast_units,f.safety_stock_units,
                      f.baseline_replenishment_units,f.method_version,f.diagnostics,
                      r.metrics,r.recommended_cases,r.recommended_loose_units,
                      r.recommended_units
                 FROM forecast_results f
                 JOIN procurement_recommendations r
                   ON r.run_id=f.run_id AND r.variant_id=f.variant_id
                WHERE f.run_id=%s ORDER BY f.variant_id""",
            (run_id,),
        ).fetchall()
        if forecast is not None:
            if (
                manifest_contract != forecast["contract"]
                or manifest.get("method_version") != forecast["method_version"]
            ):
                raise BrowserAcceptanceError("run forecast contract differs")
            if len(forecast_rows) != 4:
                raise BrowserAcceptanceError(
                    "development forecast result population differs"
                )
            for row in forecast_rows:
                evidence = row[9].get("development_forecast_evidence")
                frozen_context = manifest_contexts.get(str(row[0]))
                forecast_target = Decimal(evidence.get("point_forecast_units")) + Decimal(
                    evidence.get("protection_units")
                ) if isinstance(evidence, dict) else None
                need_target_matches = (
                    Decimal(row[9].get("target_units")) == Decimal("0")
                    and Decimal(row[6]) == Decimal("0")
                    and "ALLOCATED_NO_ROUTINE_REPLENISHMENT"
                    in row[9].get("need_reason_codes", [])
                    if str(row[0]) == "4005"
                    else Decimal(row[9].get("target_units")) == forecast_target
                )
                effective_inventory = Decimal(row[9].get("available_units")) + Decimal(
                    row[9].get("trusted_incoming_units")
                )
                operational_target = Decimal(row[9].get("target_units"))
                expected_raw_need = max(
                    0,
                    int((operational_target - effective_inventory).to_integral_value(
                        rounding=ROUND_CEILING
                    )),
                )
                units_per_case = int(
                    Decimal(
                        row[9]["frozen_offer_evidence"]["shopify_units_per_case"]
                    )
                )
                loose_allowed = bool(row[9]["frozen_vendor_terms"]["loose_order_allowed"])
                expected_cases, expected_loose = (
                    divmod(expected_raw_need, units_per_case)
                    if loose_allowed
                    else ((expected_raw_need + units_per_case - 1) // units_per_case, 0)
                )
                if str(row[0]) == "4005":
                    expected_cases, expected_loose = 0, 0
                expected_horizon = (
                    int(forecast["southern_horizon"])
                    if str(row[0]) == "1001"
                    else 3
                )
                if (
                    row[7] != forecast["method_version"]
                    or not validate_development_forecast_evidence(evidence)
                    or not isinstance(frozen_context, dict)
                    or not validate_development_baseline_need_context(
                        frozen_context,
                        manifest_contract=str(manifest_contract),
                    )
                    or row[1] != evidence.get("selected_model")
                    or row[2] != evidence.get("demand_regime")
                    or row[3] != evidence.get("xyz_class")
                    or Decimal(row[4])
                    != Decimal(evidence.get("point_forecast_units"))
                    or Decimal(row[5])
                    != Decimal(evidence.get("protection_units"))
                    or evidence.get("confidence") != "LOW"
                    or evidence.get("availability", {}).get(
                        "proven_in_stock_days"
                    )
                    != 0
                    or evidence.get("availability", {}).get("unknown_days")
                    != forecast["history_days"]
                    or evidence.get("availability", {}).get(
                        "protection_qualification"
                    )
                    != "LIMITED"
                    or evidence.get("protection", {}).get("status")
                    != "CALCULATED_LIMITED_AVAILABILITY"
                    or "AVAILABILITY_COVERAGE_LIMITS_PROTECTION"
                    not in evidence.get("reason_codes", [])
                    or not need_target_matches
                    or int(evidence.get("horizon_days")) != expected_horizon
                    or int(row[9].get("raw_need_units")) != expected_raw_need
                    or int(row[6]) != expected_raw_need
                    or int(row[10]) != expected_cases
                    or int(row[11]) != expected_loose
                    or int(row[12]) != expected_cases * units_per_case + expected_loose
                ):
                    raise BrowserAcceptanceError(
                        f"development forecast row differs for Variant {row[0]}"
                    )
        elif any(
            row[9].get("development_forecast_contract") is not None
            for row in forecast_rows
        ):
            raise BrowserAcceptanceError(
                "legacy multivendor run unexpectedly activated development forecasting"
            )
        return {
            "counts": [int(value) for value in counts],
            "stage": stage[0],
            "input_fingerprint": stage[3],
            "vendor_economics": exact_pos,
            "lines": [[str(value) for value in row] for row in variants],
            "blockers": [[str(value) for value in row] for row in blockers],
            "reviews": [[str(value) for value in row] for row in rejected],
            "artifacts": [[None if value is None else str(value) for value in row] for row in artifacts],
            "forecast_results": [
                {
                    "variant_id": str(row[0]),
                    "selected_model": row[1],
                    "demand_regime": row[2],
                    "xyz_class": row[3],
                    "point_forecast_units": str(row[4]),
                    "protection_units": None if row[5] is None else str(row[5]),
                    "baseline_replenishment_units": str(row[6]),
                    "method_version": row[7],
                    "evidence_sha256": row[9].get(
                        "development_forecast_evidence_sha256"
                    ),
                    "recommended_cases": int(row[10]),
                    "recommended_loose_units": int(row[11]),
                    "recommended_units": int(row[12]),
                    "horizon_days": (
                        row[9].get("development_forecast_evidence") or {}
                    ).get("horizon_days"),
                }
                for row in forecast_rows
            ],
            "state": _state_evidence(database_url),
        }


def _start_chromium(chromium: str, profile: Path, port: int, log: Path) -> tuple[subprocess.Popen[bytes], Any]:
    handle = log.open("wb")
    process = subprocess.Popen(
        (
            chromium, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-background-networking", "--disable-component-update",
            "--disable-default-apps", "--disable-sync", "--metrics-recording-only",
            "--no-first-run", "--no-default-browser-check",
            "--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE 127.0.0.1",
            "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "about:blank",
        ),
        env={"PATH": str(Path(chromium).parent), "LANG": "C.UTF-8"},
        stdout=subprocess.DEVNULL,
        stderr=handle,
    )
    return process, handle


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_identity = _source_identity()
    forecast = _forecast_expectations(args.fixture_profile)
    database = (
        DEVELOPMENT_V2_DATABASE
        if args.fixture_profile == DEVELOPMENT_V2_PROFILE
        else DEVELOPMENT_DATABASE
        if args.fixture_profile == DEVELOPMENT_PROFILE
        else DATABASE
    )
    os.umask(0o077)
    if args.work_root.exists() or args.evidence_root.exists():
        raise BrowserAcceptanceError("work and evidence roots must both be new")
    args.work_root.mkdir(mode=0o700, parents=True)
    args.evidence_root.mkdir(mode=0o700, parents=True)
    downloads = args.evidence_root / "downloads"
    downloads.mkdir(mode=0o700)
    state_path = args.evidence_root / "browser-state-test-data.json"
    source_runtime = args.work_root / "source-runtime"
    target_runtime = args.work_root / "target-runtime"
    source_runtime.mkdir(mode=0o700)
    target_runtime.mkdir(mode=0o700)
    initialize_runtime(source_runtime)
    initialize_runtime(target_runtime)
    source_pg_port = _free_port()
    target_pg_port = _free_port()
    while target_pg_port == source_pg_port:
        target_pg_port = _free_port()
    source_init = initialize_local_database(
        source_runtime, database=database, port=source_pg_port,
        fixture_profile=args.fixture_profile,
    )
    source_url = _local_database_url(database, source_pg_port)
    app_port = _free_port()
    chromium_port = _free_port()
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    node = shutil.which("node")
    if chromium is None or node is None:
        raise BrowserAcceptanceError("Chromium and Node.js are required")
    server: subprocess.Popen[bytes] | None = None
    server_log: Any = None
    browser: subprocess.Popen[bytes] | None = None
    browser_log: Any = None
    phases: dict[str, Any] = {}
    source_stopped = False
    target_stopped = False
    try:
        with tempfile.TemporaryDirectory(prefix="buffalo-multivendor-chromium-") as temp:
            browser, browser_log = _start_chromium(
                chromium, Path(temp) / "profile", chromium_port,
                args.evidence_root / "chromium.stderr",
            )
            cdp = _wait_cdp(browser, chromium_port)
            base = f"http://127.0.0.1:{app_port}"
            source_server_log = args.evidence_root / "source-server.log"
            source_server_log.touch(mode=0o600)
            server, server_log = _start_server(
                database_url=source_url, runtime_root=source_runtime,
                port=app_port, log_path=source_server_log,
            )
            phases["price"] = _run_phase(
                node=node, phase="price", base_url=base, cdp_endpoint=cdp,
                evidence=args.evidence_root, downloads=downloads,
                runtime_root=source_runtime, state_path=state_path,
            )
            price_state = json.loads(state_path.read_text(encoding="utf-8"))
            _stop_server(server, server_log, source_runtime)
            server = None
            server_log = None
            v2_manifest = backup_v2(
                source_url, source_runtime, str(price_state["price"]["batchId"])
            )
            server, server_log = _start_server(
                database_url=source_url, runtime_root=source_runtime,
                port=app_port, log_path=source_server_log,
                price_apply_backup_label=v2_manifest.parent.name,
            )
            phases["multivendor"] = _run_phase(
                node=node, phase="multivendor", base_url=base, cdp_endpoint=cdp,
                evidence=args.evidence_root, downloads=downloads,
                runtime_root=source_runtime, state_path=state_path,
            )
            _stop_server(server, server_log, source_runtime)
            server = None
            server_log = None
            server, server_log = _start_server(
                database_url=source_url, runtime_root=source_runtime,
                port=app_port, log_path=source_server_log,
            )
            phases["multivendor_restart"] = _run_phase(
                node=node, phase="multivendor_restart", base_url=base,
                cdp_endpoint=cdp, evidence=args.evidence_root, downloads=downloads,
                runtime_root=source_runtime, state_path=state_path,
            )
            _stop_server(server, server_log, source_runtime)
            server = None
            server_log = None

            browser_state = json.loads(state_path.read_text(encoding="utf-8"))
            initial_downloads = _validate_initial_downloads(
                downloads,
                browser_state,
                fixture_profile=args.fixture_profile,
            )
            source_acceptance = _database_acceptance(
                source_url,
                str(browser_state["runId"]),
                fixture_profile=args.fixture_profile,
            )
            if (
                forecast is not None
                and initial_downloads["input_fingerprint"]
                != source_acceptance["input_fingerprint"]
            ):
                raise BrowserAcceptanceError(
                    "packet and database frozen manifests differ"
                )
            source_storage = _storage_inventory(source_runtime / "storage")
            v1_manifest = backup(source_url, source_runtime)
            target_init = initialize_local_database(
                target_runtime, database=database, port=target_pg_port,
                empty_restore_target=True,
            )
            if source_init["system_identifier"] == target_init["system_identifier"]:
                raise BrowserAcceptanceError("source and recovery clusters are not distinct")
            target_url = _local_database_url(database, target_pg_port)
            restore_result = restore_same_database(
                target_url, target_runtime, target_runtime / "storage", v1_manifest
            )
            target_before_browser = _state_evidence(target_url)
            target_storage = _storage_inventory(target_runtime / "storage")
            if target_before_browser != source_acceptance["state"]:
                raise BrowserAcceptanceError("restored durable state differs from source")
            if target_storage != source_storage:
                raise BrowserAcceptanceError("restored artifact storage differs from source")
            target_server_log = args.evidence_root / "target-server.log"
            target_server_log.touch(mode=0o600)
            server, server_log = _start_server(
                database_url=target_url, runtime_root=target_runtime,
                port=app_port, log_path=target_server_log,
            )
            phases["multivendor_recovery"] = _run_phase(
                node=node, phase="multivendor_recovery", base_url=base,
                cdp_endpoint=cdp, evidence=args.evidence_root, downloads=downloads,
                runtime_root=target_runtime, state_path=state_path,
            )
            _stop_server(server, server_log, target_runtime)
            server = None
            server_log = None
            server, server_log = _start_server(
                database_url=target_url, runtime_root=target_runtime,
                port=app_port, log_path=target_server_log,
            )
            phases["multivendor_recovery_restart"] = _run_phase(
                node=node, phase="multivendor_recovery_restart", base_url=base,
                cdp_endpoint=cdp, evidence=args.evidence_root, downloads=downloads,
                runtime_root=target_runtime, state_path=state_path,
            )
            _stop_server(server, server_log, target_runtime)
            server = None
            server_log = None
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            initial_hashes = sorted(item["sha256"] for item in _artifact_group(final_state, "artifactDownloads"))
            for key in (
                "multivendor_restartDownloads", "multivendor_recoveryDownloads",
                "multivendor_recovery_restartDownloads",
            ):
                if sorted(item["sha256"] for item in _artifact_group(final_state, key)) != initial_hashes:
                    raise BrowserAcceptanceError(f"{key} artifact bytes differ")
            target_acceptance = _database_acceptance(
                target_url,
                str(final_state["runId"]),
                fixture_profile=args.fixture_profile,
            )
            if target_acceptance["state"] != source_acceptance["state"]:
                raise BrowserAcceptanceError("recovery replay changed durable state")
            final_target_storage = _storage_inventory(target_runtime / "storage")
            if final_target_storage != source_storage:
                raise BrowserAcceptanceError(
                    "post-replay recovery artifact storage differs from source"
                )
            result = {
                "contract": (
                    "BUFFALO_DEVELOPMENT_FORECAST_V2_TO_DRAFT_ACCEPTANCE_V1"
                    if args.fixture_profile == DEVELOPMENT_V2_PROFILE
                    else "BUFFALO_DEVELOPMENT_FORECAST_TO_DRAFT_ACCEPTANCE_V1"
                    if args.fixture_profile == DEVELOPMENT_PROFILE
                    else "BUFFALO_MULTIVENDOR_PRICE_TO_DRAFT_ACCEPTANCE_V1"
                ),
                "fixture_profile": args.fixture_profile,
                "safety_label": SAFETY_LABEL,
                "source_identity": source_identity,
                "source_cluster": source_init,
                "target_cluster": target_init,
                "different_physical_cluster": True,
                "price_apply_backup_v2": {
                    "path": str(v2_manifest), "sha256": _sha256(v2_manifest),
                },
                "populated_recovery_backup_v1": {
                    "path": str(v1_manifest), "sha256": _sha256(v1_manifest),
                },
                "restore_result": restore_result,
                "phases": phases,
                "downloads": initial_downloads,
                "source_acceptance": source_acceptance,
                "target_acceptance": target_acceptance,
                "storage_inventory": source_storage,
                "limitations": [
                    "fabricated synthetic data only; no real price or mapping authority",
                    "loopback Linux owner fixture only; no deployment, Shopify, supplier, or order action",
                    "Southern alone uses the uploaded replacement book; Western uses disclosed pre-011 synthetic CURRENT prices",
                    "CSV remains internal DRAFT format and is not a validated Shopify import format",
                ],
            }
            summary_name = (
                "DEVELOPMENT_FORECAST_V2_ACCEPTANCE_SUMMARY.json"
                if args.fixture_profile == DEVELOPMENT_V2_PROFILE
                else "DEVELOPMENT_FORECAST_ACCEPTANCE_SUMMARY.json"
                if args.fixture_profile == DEVELOPMENT_PROFILE
                else "MULTIVENDOR_ACCEPTANCE_SUMMARY.json"
            )
            _write_json(args.evidence_root / summary_name, result)
    finally:
        if server is not None and server_log is not None:
            try:
                runtime = target_runtime if (target_runtime / "candidate.pid").exists() else source_runtime
                _stop_server(server, server_log, runtime)
            except BaseException:
                pass
        if browser is not None:
            if browser.poll() is None:
                browser.terminate()
                try:
                    browser.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait(timeout=10)
            if browser_log is not None:
                browser_log.close()
        for runtime, stopped_name in ((target_runtime, "target"), (source_runtime, "source")):
            try:
                if local_database_status(runtime)["running"]:
                    stop_local_database(runtime)
            except BaseException as exc:
                _write_json(
                    args.evidence_root / f"{stopped_name}-database-cleanup-error.json",
                    {"error": str(exc)},
                )
    if _source_identity() != source_identity:
        raise BrowserAcceptanceError("source identity changed during acceptance")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument(
        "--fixture-profile",
        choices=("multivendor-v2", DEVELOPMENT_PROFILE, DEVELOPMENT_V2_PROFILE),
        default="multivendor-v2",
    )
    args = parser.parse_args()
    result = run(args)
    print(json.dumps({
        "passed": True,
        "source_commit": result["source_identity"]["commit"],
        "source_system_identifier": result["source_cluster"]["system_identifier"],
        "target_system_identifier": result["target_cluster"]["system_identifier"],
        "evidence_root": str(args.evidence_root),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
