#!/usr/bin/env python3
"""Run the full owned synthetic purchasing journey in real Chromium.

The audit starts only the reviewed direct-loopback launcher, retains one
private Chromium profile across a graceful process restart, and writes a
mode-restricted evidence directory.  It never opens a public listener or
uses ambient Shopify/database credentials.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile

import psycopg

from local_purchasing_candidate import (
    REPO_ROOT,
    SCHEMA,
    _database_facts,
    _runtime_paths,
    _state_evidence,
    backup_v2,
)


SCRIPT = Path(__file__).with_suffix(".mjs")
LAUNCHER = Path(__file__).with_name("local_purchasing_candidate.py")


class BrowserAcceptanceError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _source_identity() -> dict[str, str]:
    git = shutil.which("git")
    if git is None:
        raise BrowserAcceptanceError("git is unavailable for source identity binding")
    environment = {"PATH": str(Path(git).parent), "LANG": "C.UTF-8"}

    def read(*arguments: str) -> str:
        return subprocess.run(
            (git, *arguments),
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    if read("status", "--porcelain=v1", "--untracked-files=all"):
        raise BrowserAcceptanceError("browser acceptance requires a clean source tree")
    return {
        "commit": read("rev-parse", "HEAD"),
        "tree": read("rev-parse", "HEAD^{tree}"),
    }


def _wait_health(process: subprocess.Popen[bytes], port: int) -> None:
    request = Request(
        f"http://127.0.0.1:{port}/health",
        headers={"Host": f"127.0.0.1:{port}"},
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BrowserAcceptanceError(
                f"local launcher exited before health ({process.returncode})"
            )
        try:
            with urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.1)
    raise BrowserAcceptanceError("local launcher did not become healthy")


def _start_server(
    *,
    database_url: str,
    runtime_root: Path,
    port: int,
    log_path: Path,
    price_apply_backup_label: str | None = None,
) -> tuple[subprocess.Popen[bytes], Any]:
    log_handle = log_path.open("ab", buffering=0)
    environment = {
        "PATH": os.pathsep.join(
            path
            for path in (
                str(Path(sys.executable).parent),
                str(Path(shutil.which("pg_dump") or "/usr/bin/pg_dump").parent),
                "/usr/local/bin",
                "/usr/bin",
                "/bin",
            )
        ),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(REPO_ROOT / "procurement" / "src"),
    }
    command = [
            sys.executable,
            str(LAUNCHER),
            "serve",
            "--database-url",
            database_url,
            "--runtime-root",
            str(runtime_root),
            "--port",
            str(port),
    ]
    if price_apply_backup_label is not None:
        command.extend(("--price-apply-backup-label", price_apply_backup_label))
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )
    try:
        _wait_health(process, port)
    except BaseException:
        try:
            _stop_server_tree(process, runtime_root, timeout=10)
        finally:
            log_handle.close()
        raise
    return process, log_handle


def _candidate_child_pid(runtime_root: Path) -> int | None:
    pid_path = runtime_root / "candidate.pid"
    try:
        raw = pid_path.read_text(encoding="ascii").strip()
        pid = int(raw)
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def _signal_process_group(pid: int | None, signum: int) -> None:
    if pid is None:
        return
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def _process_group_exists(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _stop_server_tree(
    process: subprocess.Popen[bytes], runtime_root: Path, *, timeout: float
) -> None:
    child_pid = _candidate_child_pid(runtime_root)
    _signal_process_group(child_pid, signal.SIGTERM)
    if process.poll() is None:
        _signal_process_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_process_group(child_pid, signal.SIGKILL)
        _signal_process_group(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
    if child_pid is not None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not _process_group_exists(child_pid):
                break
            time.sleep(0.05)
        else:
            _signal_process_group(child_pid, signal.SIGKILL)
            second_deadline = time.monotonic() + 10
            while time.monotonic() < second_deadline:
                if not _process_group_exists(child_pid):
                    break
                time.sleep(0.05)
            else:
                raise BrowserAcceptanceError(
                    "local Uvicorn process group survived launcher stop"
                )


def _stop_server(
    process: subprocess.Popen[bytes], log_handle: Any, runtime_root: Path
) -> None:
    try:
        _stop_server_tree(process, runtime_root, timeout=20)
    finally:
        log_handle.close()
    if process.returncode not in {0, -15}:
        raise BrowserAcceptanceError(
            f"local launcher exited unexpectedly ({process.returncode})"
        )


def _wait_cdp(process: subprocess.Popen[bytes], port: int) -> str:
    endpoint = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BrowserAcceptanceError(
                f"Chromium exited before CDP readiness ({process.returncode})"
            )
        try:
            with urlopen(f"{endpoint}/json/version", timeout=0.5) as response:
                if response.status == 200:
                    return endpoint
        except (URLError, TimeoutError, OSError):
            pass
        time.sleep(0.1)
    raise BrowserAcceptanceError("Chromium CDP endpoint did not become ready")


def _run_phase(
    *,
    node: str,
    phase: str,
    base_url: str,
    cdp_endpoint: str,
    evidence: Path,
    downloads: Path,
    runtime_root: Path,
    state_path: Path,
) -> dict[str, Any]:
    completed = subprocess.run(
        (
            node,
            str(SCRIPT),
            phase,
            base_url,
            cdp_endpoint,
            str(evidence),
            str(downloads),
            str(runtime_root / "local-auth.secret"),
            str(runtime_root / "review-token.secret"),
            str(runtime_root / "price-token.secret"),
            str(REPO_ROOT / "procurement" / "config" / "synthetic_price_replacement_book.csv"),
            str(state_path),
        ),
        cwd=REPO_ROOT,
        env={
            "PATH": str(Path(node).parent),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": str(evidence),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode:
        result_path = evidence / f"{phase}-browser-results-test-data.json"
        detail = "browser phase failed without a structured result"
        if result_path.is_file():
            try:
                detail = str(json.loads(result_path.read_text(encoding="utf-8"))["error"])
            except (KeyError, json.JSONDecodeError, OSError):
                pass
        raise BrowserAcceptanceError(detail)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BrowserAcceptanceError("browser phase result is malformed") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _canonical_payload_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_demand_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
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
    }:
        raise BrowserAcceptanceError("frozen demand-evidence contract differs")
    history_start = date(2026, 7, 13)
    history_end = date(2026, 10, 4)
    if (
        value.get("contract") != "BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2"
        or value.get("method_version") != "EMERGENCY_TRANSPARENT_V2"
        or value.get("history_start") != history_start.isoformat()
        or value.get("history_end") != history_end.isoformat()
        or value.get("calendar_days") != 84
    ):
        raise BrowserAcceptanceError("frozen demand-evidence identity differs")

    coverage = value.get("sales_coverage")
    if not isinstance(coverage, dict) or set(coverage) != {
        "contract",
        "source",
        "history_start",
        "history_end",
        "authority_sha256",
        "sales_rows_sha256",
    } or (
        coverage.get("contract") != "OWNED_SYNTHETIC_DEMO_CANONICAL_BACKFILL_V1"
        or coverage.get("source") != "SHOPIFYQL_SALES"
        or coverage.get("history_start") != history_start.isoformat()
        or coverage.get("history_end") != history_end.isoformat()
        or not _is_sha256(coverage.get("authority_sha256"))
        or not _is_sha256(coverage.get("sales_rows_sha256"))
    ):
        raise BrowserAcceptanceError("frozen sales-coverage authority differs")

    statuses = {
        "forecast_status": "EMERGENCY_BASELINE_ONLY",
        "model_selection_status": "NOT_VALIDATED",
        "classification_status": "NOT_CALCULATED",
        "stockout_censoring_status": "EVIDENCE_UNAVAILABLE",
        "safety_stock_status": "NOT_CALCULATED",
    }
    availability = {
        "calendar_days": 84,
        "unknown_days": 84,
        "proven_full_day_in_stock_days": 0,
        "proven_full_day_stockout_days": 0,
        "dates_with_no_snapshot": 83,
        "compatible_point_in_time_dates": 1,
        "incompatible_point_in_time_dates": 0,
        "snapshot_rows": 2,
        "positive_snapshot_rows": 2,
        "zero_snapshot_rows": 0,
    }
    reasons = [
        "POINT_IN_TIME_INVENTORY_NOT_FULL_DAY_AVAILABILITY",
        "STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE",
    ]
    if (
        value.get("statuses") != statuses
        or value.get("availability_summary") != availability
        or value.get("reason_codes") != reasons
    ):
        raise BrowserAcceptanceError("demand availability/status evidence differs")

    daily = value.get("daily")
    if not isinstance(daily, list) or len(daily) != 84:
        raise BrowserAcceptanceError("demand daily evidence count differs")
    for offset, item in enumerate(daily):
        expected_date = history_start + timedelta(days=offset)
        expected_units = "1.0000" if offset < 70 else "2.0000"
        if (
            not isinstance(item, dict)
            or set(item) != {
                "business_date",
                "net_units",
                "inventory_state",
                "state_basis",
                "snapshot_refs",
            }
            or item.get("business_date") != expected_date.isoformat()
            or item.get("net_units") != expected_units
            or item.get("inventory_state") != "UNKNOWN"
            or item.get("state_basis")
            != (
                "POINT_IN_TIME_SNAPSHOT_ONLY"
                if expected_date == history_end
                else "NO_POINT_IN_TIME_SNAPSHOT"
            )
            or not isinstance(item.get("snapshot_refs"), list)
            or len(item["snapshot_refs"])
            != (2 if expected_date == history_end else 0)
        ):
            raise BrowserAcceptanceError("demand daily evidence differs")

    final_refs = daily[-1]["snapshot_refs"]
    if any(
        not isinstance(item, dict)
        or set(item)
        != {
            "snapshot_date",
            "location_gid",
            "inventory_snapshot_run_id",
            "source",
            "source_hash",
            "captured_at",
            "completed_at",
            "available_quantity",
            "incoming_quantity",
            "validation_status",
        }
        for item in final_refs
    ):
        raise BrowserAcceptanceError("point-in-time snapshot shape differs")
    if [item.get("location_gid") for item in final_refs] != [
        "synthetic-location-001",
        "synthetic-location-002",
    ]:
        raise BrowserAcceptanceError("historical inventory locations differ")
    run_ids = {item.get("inventory_snapshot_run_id") for item in final_refs}
    source_hashes = {item.get("source_hash") for item in final_refs}
    captured = {item.get("captured_at") for item in final_refs}
    completed = {item.get("completed_at") for item in final_refs}
    if (
        len(run_ids) != 1
        or any(not isinstance(item, str) or not item for item in run_ids)
        or len(source_hashes) != 1
        or any(not _is_sha256(item) for item in source_hashes)
        or captured != {"2026-10-04T12:00:00+00:00"}
        or completed != captured
        or [item.get("available_quantity") for item in final_refs]
        != ["1.0000", "2.0000"]
        or any(
            item.get("snapshot_date") != history_end.isoformat()
            or item.get("source") != "SYNTHETIC_DEMO"
            or item.get("incoming_quantity") != "0.0000"
            or item.get("validation_status") != "VALID"
            for item in final_refs
        )
    ):
        raise BrowserAcceptanceError("point-in-time inventory evidence differs")

    groups = value.get("snapshot_groups")
    if not isinstance(groups, list) or len(groups) != 1:
        raise BrowserAcceptanceError("snapshot-group evidence count differs")
    group = groups[0]
    if (
        not isinstance(group, dict)
        or set(group)
        != {
            "snapshot_date",
            "inventory_snapshot_run_id",
            "source",
            "source_hash",
            "captured_at",
            "completed_at",
            "locations",
            "compatibility_status",
            "aggregate_available_quantity",
            "aggregate_incoming_quantity",
        }
        or group.get("snapshot_date") != history_end.isoformat()
        or group.get("inventory_snapshot_run_id") not in run_ids
        or group.get("source") != "SYNTHETIC_DEMO"
        or group.get("source_hash") not in source_hashes
        or group.get("captured_at") not in captured
        or group.get("completed_at") not in completed
        or group.get("locations")
        != ["synthetic-location-001", "synthetic-location-002"]
        or group.get("compatibility_status")
        != "COMPATIBLE_POINT_IN_TIME_EVIDENCE"
        or group.get("aggregate_available_quantity") != "3.0000"
        or group.get("aggregate_incoming_quantity") != "0.0000"
    ):
        raise BrowserAcceptanceError("compatible snapshot-group evidence differs")

    windows = value.get("raw_windows")
    expected_windows = {
        "7": (7, history_end - timedelta(days=6), Decimal("14"), Decimal("2")),
        "14": (14, history_end - timedelta(days=13), Decimal("28"), Decimal("2")),
        "28": (28, history_end - timedelta(days=27), Decimal("42"), Decimal("1.5")),
    }
    if not isinstance(windows, dict) or set(windows) != set(expected_windows):
        raise BrowserAcceptanceError("raw demand-window inventory differs")
    for key, (denominator, start, units, velocity) in expected_windows.items():
        item = windows[key]
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "requested_window",
                "actual_denominator_days",
                "inclusive_start",
                "inclusive_end",
                "signed_net_units",
                "signed_calendar_velocity",
            }
            or item.get("requested_window") != denominator
            or item.get("actual_denominator_days") != denominator
            or item.get("inclusive_start") != start.isoformat()
            or item.get("inclusive_end") != history_end.isoformat()
            or item.get("signed_net_units") != format(units, ".4f")
            or item.get("signed_calendar_velocity") != format(velocity, ".6f")
        ):
            raise BrowserAcceptanceError("raw demand-window values differ")
    expected_sales_rows = [
        {
            "business_date": item["business_date"],
            "net_units": item["net_units"],
            "source": "SHOPIFYQL_SALES",
        }
        for item in daily
    ]
    if coverage.get("sales_rows_sha256") != _canonical_payload_sha256(
        expected_sales_rows
    ):
        raise BrowserAcceptanceError("sales coverage row digest differs")
    return {
        "contract": value["contract"],
        "sha256": _canonical_payload_sha256(value),
        "calendar_days": 84,
        "snapshot_rows": 2,
        "statuses": statuses,
    }


def _validate_downloads(
    downloads: Path, runtime_root: Path, browser_state: dict[str, Any]
) -> dict[str, Any]:
    files = sorted(path for path in downloads.iterdir() if path.is_file())
    csv_files = [path for path in files if path.suffix == ".csv"]
    zip_files = [path for path in files if path.suffix == ".zip"]
    json_files = [path for path in files if path.suffix == ".json"]
    if len(files) != 4 or (len(csv_files), len(zip_files), len(json_files)) != (
        1,
        1,
        2,
    ):
        raise BrowserAcceptanceError("browser download type/count differs")

    with csv_files[0].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise BrowserAcceptanceError("vendor CSV row count differs")
    row = rows[0]
    expected = {
        "safety_label": "TEST DATA — NOT FOR ORDERING",
        "format_status": "SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED",
        "variant_id": "1001",
        "supplier_sku": "SUP-001",
        "cases": "2",
        "loose_units": "0",
        "ordered_units": "12",
        "unit_cost": "5.0000",
        "case_price": "30.0000",
        "line_merchandise_total": "60.00",
        "line_loose_order_fee": "0.00",
        "line_total": "60.00",
        "vendor_merchandise_total": "60.00",
        "vendor_loose_order_fee_total": "0.00",
        "vendor_below_minimum_fee": "0.00",
        "vendor_delivery_fee": "0.00",
        "vendor_po_total": "60.00",
        "below_vendor_minimum": "FALSE",
        "minimum_shortfall": "0.00",
        "minimum_disposition": "NOT_APPLICABLE",
        "economics_confirmed_by": "synthetic:owner-browser:01",
    }
    differences = {
        key: {"expected": value, "actual": row.get(key)}
        for key, value in expected.items()
        if row.get(key) != value
    }
    if differences:
        raise BrowserAcceptanceError(
            f"vendor CSV values differ: {json.dumps(differences, sort_keys=True)}"
        )

    with zipfile.ZipFile(zip_files[0]) as archive:
        names = sorted(archive.namelist())
        expected_names = sorted(
            {
                "manifest.json",
                "packet-summary.json",
                "human-review-decisions.csv",
                "frozen-input-manifest.json",
                "recommendations-and-reasons.json",
                "frozen-price-economics.json",
                "supplier-mapping-evidence.json",
                "open-po-ledger-evidence.json",
                "blocked-item-exclusions.json",
                "material-edit-confirmations.json",
                "draft-readiness-evidence.json",
                "vendor-00000000-0000-4000-8000-000000000001.internal.csv",
            }
        )
        if names != expected_names:
            raise BrowserAcceptanceError("review ZIP exact member inventory differs")
        manifest = json.loads(archive.read("manifest.json"))
        if set(manifest) != {"safety_label", "entries"} or manifest.get(
            "safety_label"
        ) != "TEST DATA — NOT FOR ORDERING":
            raise BrowserAcceptanceError("review ZIP manifest contract differs")
        entries = manifest.get("entries")
        if not isinstance(entries, dict) or not entries:
            raise BrowserAcceptanceError("ZIP manifest entries are absent")
        if sorted(entries) != sorted(name for name in names if name != "manifest.json"):
            raise BrowserAcceptanceError("ZIP manifest inventory differs")
        for name, expected_hash in entries.items():
            actual_hash = hashlib.sha256(archive.read(name)).hexdigest()
            if actual_hash != expected_hash:
                raise BrowserAcceptanceError("ZIP member checksum differs")
        embedded_csv = [name for name in names if name.endswith(".internal.csv")]
        if len(embedded_csv) != 1 or archive.read(embedded_csv[0]) != csv_files[0].read_bytes():
            raise BrowserAcceptanceError("embedded and downloaded vendor CSV differ")
        for name in names:
            data = archive.read(name)
            if b"spoofed-browser-actor-must-be-ignored" in data:
                raise BrowserAcceptanceError("client-supplied actor appeared in the review ZIP")
            for secret_name in ("local-auth.secret", "review-token.secret", "price-token.secret"):
                secret = (runtime_root / secret_name).read_bytes().rstrip(b"\n")
                if secret and secret in data:
                    raise BrowserAcceptanceError("secret bytes appeared in the review ZIP")

        frozen_input = json.loads(archive.read("frozen-input-manifest.json"))
        recommendation_evidence = json.loads(
            archive.read("recommendations-and-reasons.json")
        )
        if (
            set(frozen_input) != {"safety_label", "input_manifest"}
            or frozen_input.get("safety_label") != "TEST DATA — NOT FOR ORDERING"
            or set(recommendation_evidence) != {"safety_label", "items"}
            or recommendation_evidence.get("safety_label")
            != "TEST DATA — NOT FOR ORDERING"
        ):
            raise BrowserAcceptanceError("frozen recommendation evidence contract differs")
        input_manifest = frozen_input.get("input_manifest")
        contexts = (
            input_manifest.get("contexts") if isinstance(input_manifest, dict) else None
        )
        eligible_contexts = [
            item
            for item in (contexts or [])
            if isinstance(item, dict) and str(item.get("variant_id")) == "1001"
        ]
        recommendation_items = recommendation_evidence.get("items")
        if (
            not isinstance(input_manifest, dict)
            or input_manifest.get("method_version") != "EMERGENCY_TRANSPARENT_V2"
            or input_manifest.get("business_date") != "2026-10-05"
            or len(eligible_contexts) != 1
            or not isinstance(recommendation_items, list)
            or len(recommendation_items) != 1
            or recommendation_items[0].get("variant_id") != "1001"
        ):
            raise BrowserAcceptanceError("frozen V2 recommendation inventory differs")
        manifest_demand = eligible_contexts[0].get("demand_evidence")
        recommendation_metrics = recommendation_items[0].get("metrics") or {}
        recommendation_demand = recommendation_metrics.get("demand_evidence")
        if manifest_demand != recommendation_demand:
            raise BrowserAcceptanceError(
                "manifest and recommendation demand evidence diverge"
            )
        demand_result = _validate_demand_evidence(manifest_demand)
        if browser_state.get("demandEvidence") != manifest_demand:
            raise BrowserAcceptanceError(
                "browser-rendered and packet demand evidence diverge"
            )
        selected_evidence = eligible_contexts[0].get(
            "selected_offer_input_evidence"
        )
        selected_sha = eligible_contexts[0].get(
            "selected_offer_input_evidence_sha256"
        )
        mapping_packet = json.loads(
            archive.read("supplier-mapping-evidence.json")
        )
        mapping_items = (
            mapping_packet.get("items") if isinstance(mapping_packet, dict) else None
        )
        if (
            input_manifest.get("offer_resolution_contract")
            != "SYNTHETIC_CONFIRMED_SELECTION_V1"
            or not isinstance(selected_evidence, dict)
            or selected_evidence.get("authority") != "SYNTHETIC_TEST_ONLY"
            or selected_evidence.get("historical_reconstruction") is not False
            or selected_evidence.get("selected_offer", {}).get("offer_id")
            != browser_state.get("selectedOfferId")
            or selected_evidence
            != recommendation_metrics.get("selected_offer_input_evidence")
            or selected_sha
            != recommendation_metrics.get("selected_offer_input_evidence_sha256")
            or selected_sha != _canonical_payload_sha256(selected_evidence)
            or selected_evidence != browser_state.get("selectedOfferInputEvidence")
            or not isinstance(mapping_items, list)
            or len(mapping_items) != 1
            or mapping_items[0].get("offer_id")
            != browser_state.get("selectedOfferId")
            or mapping_items[0].get("selected_offer_input_evidence")
            != selected_evidence
            or mapping_items[0].get("selected_offer_input_evidence_sha256")
            != selected_sha
            or mapping_items[0].get("final_price_tier")
            != browser_state.get("finalPriceTier")
        ):
            raise BrowserAcceptanceError(
                "selected offer lineage, packet, or browser evidence diverges"
            )
        ladder_rows = selected_evidence.get("applicable_price_ladder", {}).get(
            "rows"
        )
        final_tier = browser_state.get("finalPriceTier") or {}
        source_matches = [
            source
            for source in (ladder_rows or [])
            if isinstance(source, list)
            and len(source) == 10
            and source[0] == final_tier.get("price_id")
            and source[1] == browser_state.get("selectedOfferId")
            and source[3] == final_tier.get("level_type")
            and Decimal(str(source[4])) == Decimal(str(final_tier.get("break_qty")))
            and source[5] == final_tier.get("break_unit")
            and Decimal(str(source[6])) == Decimal(str(final_tier.get("case_price")))
            and Decimal(str(source[7])) == Decimal(str(final_tier.get("unit_price")))
        ]
        if len(source_matches) != 1:
            raise BrowserAcceptanceError(
                "final tier has no unique source row in the selected frozen ladder"
            )
        uncalculated = (
            "demand_regime",
            "selected_model",
            "abc_class",
            "xyz_class",
            "in_stock_velocity",
            "safety_stock_units",
        )
        if (
            recommendation_metrics.get("forecast_method_version")
            != "EMERGENCY_TRANSPARENT_V2"
            or any(recommendation_metrics.get(key) is not None for key in uncalculated)
            or any(
                recommendation_metrics.get(key) != expected
                for key, expected in {
                    "forecast_status": "EMERGENCY_BASELINE_ONLY",
                    "model_selection_status": "NOT_VALIDATED",
                    "classification_status": "NOT_CALCULATED",
                    "stockout_censoring_status": "EVIDENCE_UNAVAILABLE",
                    "safety_stock_status": "NOT_CALCULATED",
                }.items()
            )
        ):
            raise BrowserAcceptanceError(
                "recommendation demand authority fields differ"
            )

        summary = json.loads(archive.read("packet-summary.json"))
        if set(summary) != {
            "safety_label",
            "format_status",
            "run_id",
            "input_fingerprint",
            "vendor_draft_count",
            "draft_po_ids",
            "merchandise_total",
            "po_total",
            "vendor_draft_economics",
            "exceptions",
            "release_performed",
            "shopify_calls",
        }:
            raise BrowserAcceptanceError("packet summary exact contract differs")
        if (
            summary["safety_label"] != "TEST DATA — NOT FOR ORDERING"
            or summary["format_status"]
            != "SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED"
            or summary["run_id"] != browser_state["runId"]
            or not _is_sha256(summary["input_fingerprint"])
            or summary["vendor_draft_count"] != 1
            or len(summary["draft_po_ids"]) != 1
            or summary["draft_po_ids"][0] != row.get("draft_po_id")
            or Decimal(str(summary["merchandise_total"])) != Decimal("60.00")
            or Decimal(str(summary["po_total"])) != Decimal("60.00")
            or summary["release_performed"] is not False
            or summary["shopify_calls"] != 0
        ):
            raise BrowserAcceptanceError("packet summary authority or totals differ")
        economics = summary["vendor_draft_economics"]
        if len(economics) != 1:
            raise BrowserAcceptanceError("packet vendor economics count differs")
        vendor = economics[0]
        if (
            vendor.get("vendor_id") != "00000000-0000-4000-8000-000000000001"
            or vendor.get("vendor_name") != "Synthetic Southern"
            or vendor.get("draft_po_id") != row.get("draft_po_id")
            or Decimal(str(vendor.get("merchandise_total"))) != Decimal("60.00")
            or Decimal(str(vendor.get("po_total"))) != Decimal("60.00")
            or Decimal(str(vendor.get("delivery_fee"))) != Decimal("0.00")
            or vendor.get("below_vendor_minimum") is not False
            or Decimal(str(vendor.get("minimum_shortfall"))) != Decimal("0.00")
            or vendor.get("minimum_disposition") != "NOT_APPLICABLE"
            or vendor.get("economics_confirmed_by")
            != "synthetic:owner-browser:01"
            or not _is_sha256(vendor.get("draft_preview_fingerprint"))
        ):
            raise BrowserAcceptanceError("packet vendor economics differ")

        blockers = json.loads(archive.read("blocked-item-exclusions.json"))
        if set(blockers) != {"safety_label", "items"} or len(blockers["items"]) != 1:
            raise BrowserAcceptanceError("blocked-item evidence contract differs")
        blocker = blockers["items"][0]
        exclusion = blocker.get("run_only_exclusion") or {}
        if (
            blocker.get("type") != "MONDAY_INPUT_BLOCKER"
            or blocker.get("severity") != "HIGH"
            or blocker.get("variant_id") != "2002"
            or blocker.get("status") != "OPEN"
            or "VARIANT_NOT_CURRENT_ACTIVE_LIVE" not in str(blocker.get("message"))
            or exclusion.get("action") != "ACKNOWLEDGE_AND_EXCLUDE"
            or exclusion.get("scope") != "RUN_ONLY"
            or exclusion.get("actor") != "synthetic:owner-browser:01"
            or exclusion.get("reason")
            != "Synthetic missing variant excluded from this run only"
            or exclusion.get("input_fingerprint") != summary["input_fingerprint"]
        ):
            raise BrowserAcceptanceError("blocked-item evidence differs")
        if summary["exceptions"] != blockers["items"]:
            raise BrowserAcceptanceError("packet summary and blocker evidence diverge")

        confirmations = json.loads(archive.read("material-edit-confirmations.json"))
        if set(confirmations) != {"safety_label", "items"} or len(
            confirmations["items"]
        ) != 1:
            raise BrowserAcceptanceError("material confirmation evidence contract differs")
        confirmation = confirmations["items"][0]
        evidence = confirmation.get("evidence") or {}
        if (
            confirmation.get("action") != "CONFIRM_MATERIAL_EDIT"
            or confirmation.get("confirmed_by") != "synthetic:owner-browser:01"
            or confirmation.get("reason")
            != "Synthetic confirmation of material quantity and cash exposure"
            or Decimal(str(confirmation.get("approved_cases"))) != Decimal("2")
            or Decimal(str(confirmation.get("approved_loose_units"))) != Decimal("0")
            or Decimal(str(confirmation.get("approved_units"))) != Decimal("12")
            or confirmation.get("input_fingerprint") != summary["input_fingerprint"]
            or not _is_sha256(confirmation.get("review_preview_fingerprint"))
            or evidence.get("materiality_tier") != "MATERIAL"
            or Decimal(str(evidence.get("baseline_multiplier"))) != Decimal("4")
            or Decimal(str(evidence.get("recommended_line_cash"))) != Decimal("36.00")
            or Decimal(str(evidence.get("incremental_line_cash"))) != Decimal("24.00")
            or Decimal(str(evidence.get("final_line_cash"))) != Decimal("60.00")
            or evidence.get("review_comment") != "Synthetic material quantity acceptance"
        ):
            raise BrowserAcceptanceError("material confirmation evidence differs")

        review_rows = list(
            csv.DictReader(io.StringIO(archive.read("human-review-decisions.csv").decode()))
        )
        if len(review_rows) != 1:
            raise BrowserAcceptanceError("human review evidence row count differs")
        review_row = review_rows[0]
        expected_review = {
            "safety_label": "TEST DATA — NOT FOR ORDERING",
            "variant_id": "1001",
            "vendor_name": "Synthetic Southern",
            "action": "EDIT_QUANTITY",
            "approved_unit_cost": "5.0000",
            "approved_case_price": "30.0000",
            "approved_merchandise_total": "60.00",
            "approved_loose_order_fee": "0.00",
            "approved_line_total": "60.00",
            "comment": "Synthetic material quantity acceptance",
            "decided_by": "synthetic:owner-browser:01",
        }
        if any(
            review_row.get(key) != value for key, value in expected_review.items()
        ) or tuple(
            Decimal(review_row[key])
            for key in ("approved_cases", "approved_loose_units", "approved_units")
        ) != (
            Decimal("2"),
            Decimal("0"),
            Decimal("12"),
        ):
            raise BrowserAcceptanceError("human review decision evidence differs")

    metadata_by_occurrence: dict[str, dict[str, Any]] = {}
    for json_file in json_files:
        metadata = json.loads(json_file.read_text(encoding="utf-8"))
        if set(metadata) != {
            "safety_label",
            "data_mode",
            "source_disclosure",
            "contract",
            "authority",
            "candidate",
            "batch",
        } or (
            metadata.get("safety_label") != "TEST DATA — NOT FOR ORDERING"
            or metadata.get("data_mode") != "SYNTHETIC_DEMO"
            or "fabricated authoritative-format"
            not in str(metadata.get("source_disclosure", ""))
            or "not real supplier evidence or approval"
            not in str(metadata.get("source_disclosure", ""))
            or metadata.get("contract")
            != "BUFFALO_MAPPING_EVIDENCE_METADATA_ONLY_V1"
            or metadata.get("authority") != "REVIEW_ONLY_NOT_SOURCE_BLOB"
        ):
            raise BrowserAcceptanceError("mapping evidence download contract differs")
        candidate = metadata["candidate"]
        batch = metadata["batch"]
        if (
            batch.get("structural_state") != "READY"
            or batch.get("source_evidence_state") != "READY"
            or batch.get("semantic_state") != "READY"
            or batch.get("source_authority_state") != "NOT_APPROVED"
            or batch.get("source_import_state") != "NOT_IMPORT_READY"
            or batch.get("candidate_count") != 1
            or candidate.get("review_batch_id") != batch.get("review_batch_id")
            or candidate.get("source_file_sha256")
            != batch.get("source_artifact_sha256")
        ):
            raise BrowserAcceptanceError("mapping evidence readiness or source binding differs")
        metadata_by_occurrence[candidate["occurrence_key"]] = metadata
    if set(metadata_by_occurrence) != {
        "synthetic-unresolved-occurrence-001",
        "fabricated-authoritative-format-occurrence-001",
    }:
        raise BrowserAcceptanceError("mapping evidence scenarios differ")
    unresolved = metadata_by_occurrence["synthetic-unresolved-occurrence-001"]
    if (
        unresolved["batch"].get("source_package_id")
        != "synthetic-unresolved-review-v2"
        or unresolved["batch"].get("source_is_simulation") is not True
        or unresolved["candidate"].get("source_file_name")
        != "synthetic-unresolved-source.txt"
        or unresolved["candidate"].get("blockers") != ["IDENTITY_UNRESOLVED"]
        or unresolved["candidate"].get("distributor_product_id_state") != "ABSENT"
        or unresolved["candidate"].get("supplier_code_state") != "ABSENT"
        or unresolved["candidate"].get("package_type_state") != "EXPLICIT_NULL"
        or any(
            unresolved["candidate"].get(key) is not None
            for key in (
                "distributor_product_id_value",
                "supplier_code_value",
                "package_type_value",
            )
        )
    ):
        raise BrowserAcceptanceError("unresolved mapping evidence differs")
    valid = metadata_by_occurrence["fabricated-authoritative-format-occurrence-001"]
    linkage = valid["candidate"].get("independent_linkage_evidence") or []
    if (
        valid["batch"].get("source_package_id")
        != "fabricated-authoritative-format-review-v2"
        or valid["batch"].get("source_is_simulation") is not False
        or not str(valid["batch"].get("source_artifact_ref", "")).startswith(
            "synthetic-packet:"
        )
        or valid["batch"].get("supplier_period_scope")
        != {"kind": "FABRICATED_TEST_PERIOD"}
        or (valid["batch"].get("prerequisites") or {}).get("packet_contract")
        != "BUFFALO_SYNTHETIC_MAPPING_REVIEW_PACKET_V2"
        or valid["candidate"].get("proposed_variant_id") != "1001"
        or valid["candidate"].get("proposed_vendor_id")
        != "00000000-0000-4000-8000-000000000001"
        or valid["candidate"].get("supplier_code_value") != "SUP-001"
        or valid["candidate"].get("distributor_product_id_value") != "SUP-001"
        or valid["candidate"].get("offer_class") != "REGULAR"
        or len(linkage) != 1
        or linkage[0].get("evidence_mode") != "DETERMINISTIC_INDEPENDENT"
        or linkage[0].get("fixture_disclosure") != "FABRICATED_TEST_EVIDENCE"
    ):
        raise BrowserAcceptanceError("fabricated exact mapping evidence differs")

    return {
        "files": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in files
        ],
        "csv": {key: row[key] for key in expected},
        "zip_members": names,
        "mapping_evidence_contract": "BUFFALO_MAPPING_EVIDENCE_METADATA_ONLY_V1",
        "mapping_evidence_safety_label": "TEST DATA — NOT FOR ORDERING",
        "packet_summary": summary,
        "demand_evidence": demand_result,
        "demand_evidence_payload": manifest_demand,
        "input_manifest_payload": input_manifest,
        "recommendation_metrics_payload": recommendation_metrics,
        "selected_offer_input_evidence": selected_evidence,
        "selected_offer_input_evidence_sha256": selected_sha,
        "final_price_tier": mapping_items[0]["final_price_tier"],
        "artifact_files": [
            {"name": path.name, "sha256": _sha256(path)}
            for path in (csv_files[0], zip_files[0])
        ],
    }


def _database_acceptance(
    database_url: str,
    downloads_result: dict[str, Any],
    browser_state: dict[str, Any],
) -> dict[str, Any]:
    state = _state_evidence(database_url)
    server_actor = "synthetic:owner-browser:01"
    spoof_actor = "spoofed-browser-actor-must-be-ignored"
    with psycopg.connect(database_url) as conn:
        conn.execute(f"SET search_path TO {SCHEMA},pg_catalog")
        row = conn.execute(
            "SELECT "
            "(SELECT count(*) FROM supplier_offers),"
            "(SELECT count(*) FROM supplier_offers WHERE active),"
            "(SELECT count(*) FROM prices),"
            "(SELECT count(*) FROM prices WHERE price_state='current'),"
            "(SELECT count(*) FROM supplier_offers o JOIN prices p USING(offer_id) "
            " WHERE o.supplier_sku='SUP-001' AND o.active AND p.price_state='current' "
            " AND ((p.level_type='BASE' AND p.case_price=36.00 AND p.unit_price=6.0000) "
            "   OR (p.level_type='BREAK' AND p.break_qty=2 AND p.break_unit='CS' "
            "       AND p.case_price=30.00 AND p.unit_price=5.0000))),"
            "(SELECT count(*) FROM purchase_orders WHERE po_status='DRAFT'),"
            "(SELECT count(*) FROM purchase_orders WHERE po_status<>'DRAFT')"
        ).fetchone()
        batch_actors = conn.execute(
            "SELECT source_package_id,creator_principal_ref,creator_role_ref,"
            "creator_authn_context_sha256 FROM supplier_mapping_review_batches "
            "ORDER BY source_package_id"
        ).fetchall()
        decision_actors = conn.execute(
            "SELECT action,human_principal_ref,human_role_ref,"
            "human_authn_context_sha256 FROM supplier_mapping_decisions ORDER BY action"
        ).fetchall()
        selections = conn.execute(
            "SELECT action,variant_id,selected_offer_id,human_principal_ref,"
            "human_role_ref,human_authn_context_sha256 "
            "FROM supplier_offer_selection_events"
        ).fetchall()
        run_rows = conn.execute(
            "SELECT run_id::text,status,workflow_stage,procurement_output_mode,notes,"
            "input_fingerprint,model_version,business_date,idempotency_key "
            ",pg_catalog.to_jsonb(r) "
            "FROM runs r ORDER BY run_id"
        ).fetchall()
        retirement_rows = conn.execute(
            """SELECT run_id::text,input_fingerprint,retired_model_version,
                      prior_status,prior_workflow_stage,target_status,
                      target_workflow_stage,purchase_order_count,artifact_count,
                      actor,reason,confirmation_sha256,before_run_json,
                      after_run_json,transaction_id,created_at
                 FROM monday_stale_forecast_retirements ORDER BY run_id"""
        ).fetchall()
        retirement_audits = conn.execute(
            """SELECT table_name,row_key,action,before_json,after_json,actor,
                      run_id::text,occurred_at,evidence_json
                 FROM change_log
                WHERE evidence_json->>'contract'=
                      'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1'
                ORDER BY change_id"""
        ).fetchall()
        forecast_rows = conn.execute(
            """SELECT f.run_id::text,f.variant_id,f.demand_regime,
                      f.selected_model,f.abc_class,f.xyz_class,
                      f.safety_stock_units,f.in_stock_velocity,f.method_version,
                      f.history_start,f.history_end,f.horizon_days,
                      f.diagnostics,r.procurement_input_manifest::jsonb
                 FROM forecast_results f JOIN runs r USING(run_id)
                ORDER BY f.run_id,f.variant_id"""
        ).fetchall()
        recommendation_metrics_rows = conn.execute(
            """SELECT run_id::text,variant_id,metrics
                 FROM procurement_recommendations
                ORDER BY run_id,variant_id,recommendation_id"""
        ).fetchall()
        exclusion_rows = conn.execute(
            "SELECT x.actor,x.reason,e.status,e.variant_id FROM monday_run_blocker_exclusions x "
            "JOIN exceptions e USING(exception_id)"
        ).fetchall()
        confirmation_rows = conn.execute(
            "SELECT confirmed_by,action,approved_cases,approved_loose_units,approved_units "
            "FROM monday_material_edit_confirmations"
        ).fetchall()
        review_rows = conn.execute(
            "SELECT decided_by,action,approved_cases,approved_loose_units,approved_units,"
            "evidence_json #>> '{review,actor}',"
            "evidence_json #> '{review,final_price_tier}' FROM review_decisions"
        ).fetchall()
        po_rows = conn.execute(
            "SELECT po_id::text,run_id::text,po_status,merchandise_total,po_total,notes,"
            "reconciliation_evidence->>'economics_confirmed_by',"
            "reconciliation_evidence->'final_price_tiers' FROM purchase_orders"
        ).fetchall()
        selected_price_snapshots = conn.execute(
            """SELECT run_price_snapshot_id,offer_id,effective_month,level_type,
                      break_qty,break_unit,case_price,unit_price,source_file,source_page,
                      source_price_id,source_price_book_batch_id::text,
                      source_price_book_row_number,supplier_price_authority_event_id::text
                 FROM run_price_snapshots
                WHERE run_id=%s ORDER BY run_price_snapshot_id""",
            (browser_state.get("runId"),),
        ).fetchall()
        artifact_rows = conn.execute(
            "SELECT run_id::text,artifact_type,vendor_id::text,sha256,size_bytes,octet_length(payload),"
            "content_type,safety_label,created_by FROM monday_run_artifacts "
            "ORDER BY artifact_type"
        ).fetchall()
        event_rows = conn.execute(
            "SELECT run_id::text,artifact_set_sha256,packet_sha256,csv_count,artifact_count,"
            "verification_method,actor,monday_artifact_set_sha256(run_id) "
            "FROM monday_packet_build_events"
        ).fetchall()
        stale_child_counts = conn.execute(
            """SELECT
                  (SELECT count(*) FROM forecast_results WHERE run_id=%s),
                  (SELECT count(*) FROM procurement_recommendations WHERE run_id=%s),
                  (SELECT count(*) FROM inventory_snapshots WHERE run_id=%s),
                  (SELECT count(*) FROM run_price_snapshots WHERE run_id=%s),
                  (SELECT count(*) FROM exceptions WHERE run_id=%s),
                  (SELECT count(*) FROM review_decisions WHERE run_id=%s),
                  (SELECT count(*) FROM monday_run_blocker_exclusions WHERE run_id=%s),
                  (SELECT count(*) FROM monday_material_edit_confirmations WHERE run_id=%s),
                  (SELECT count(*) FROM purchase_orders WHERE run_id=%s),
                  (SELECT count(*) FROM purchase_order_lines l JOIN purchase_orders p
                    USING(po_id) WHERE p.run_id=%s),
                  (SELECT count(*) FROM monday_run_artifacts WHERE run_id=%s),
                  (SELECT count(*) FROM monday_packet_build_events WHERE run_id=%s),
                  (SELECT count(*) FROM po_operational_events e
                    JOIN purchase_orders p USING(po_id) WHERE p.run_id=%s),
                  (SELECT count(*) FROM po_reconciliation_events e
                    JOIN purchase_order_lines l USING(po_line_id)
                    JOIN purchase_orders p USING(po_id) WHERE p.run_id=%s),
                  (SELECT count(*) FROM change_log WHERE run_id=%s)""",
            tuple(browser_state.get("retiredRunId") for _ in range(15)),
        ).fetchone()
        spoof_counts = {}
        for table in (
            "supplier_mapping_review_batches",
            "supplier_mapping_review_candidates",
            "supplier_mapping_decisions",
            "supplier_offer_selection_events",
            "supplier_offer_selection_heads",
            "mapping_rejections",
            "runs",
            "exceptions",
            "monday_run_blocker_exclusions",
            "monday_material_edit_confirmations",
            "review_decisions",
            "purchase_orders",
            "purchase_order_lines",
            "monday_run_artifacts",
            "monday_packet_build_events",
            "monday_stale_forecast_retirements",
            "change_log",
            "forecast_results",
            "procurement_recommendations",
        ):
            spoof_counts[table] = int(
                conn.execute(
                    f"SELECT count(*) FROM {table} AS t WHERE to_jsonb(t)::text LIKE %s",
                    (f"%{spoof_actor}%",),
                ).fetchone()[0]
            )
    assert row is not None
    expected = (3, 3, 6, 6, 2, 1, 0)
    if tuple(int(value) for value in row) != expected:
        raise BrowserAcceptanceError("database nonauthority/control totals differ")
    facts = state["facts"]
    exact_counts = {
        "review_batches": 2,
        "review_candidates": 2,
        "mapping_decisions": 2,
        "selection_events": 1,
        "selection_heads": 1,
        "runs": 2,
        "purchase_orders": 1,
        "artifacts": 2,
    }
    for key, value in exact_counts.items():
        if int(facts[key]) != value:
            raise BrowserAcceptanceError(f"database count differs for {key}")
    if (
        len(batch_actors) != 2
        or {item[0] for item in batch_actors}
        != {
            "synthetic-unresolved-review-v2",
            "fabricated-authoritative-format-review-v2",
        }
        or any(
            item[1] != server_actor
            or item[2] != "procurement.review.intake"
            or not _is_sha256(item[3])
            for item in batch_actors
        )
    ):
        raise BrowserAcceptanceError("mapping intake principal evidence differs")
    if (
        len(decision_actors) != 2
        or {item[0] for item in decision_actors} != {"DEFER", "APPROVE_MAPPING"}
        or any(
            item[1] != server_actor
            or item[2] != "procurement.mapping.approve"
            or not _is_sha256(item[3])
            for item in decision_actors
        )
    ):
        raise BrowserAcceptanceError("mapping decision principal evidence differs")
    if (
        len(selections) != 1
        or selections[0][0:3]
        != ("SELECT", "1001", browser_state.get("selectedOfferId"))
        or selections[0][3] != server_actor
        or selections[0][4] != "procurement.offer.select"
        or not _is_sha256(selections[0][5])
    ):
        raise BrowserAcceptanceError("routine selection principal evidence differs")
    summary = downloads_result["packet_summary"]
    runs_by_id = {item[0]: item for item in run_rows}
    stale_run_id = browser_state.get("retiredRunId")
    active_run_id = browser_state.get("runId")
    if set(runs_by_id) != {stale_run_id, active_run_id}:
        raise BrowserAcceptanceError("Monday run inventory differs")
    stale_run = runs_by_id[stale_run_id]
    active_run = runs_by_id[active_run_id]
    if (
        stale_run[1:4] != ("FAILED", "FAILED", "INTERNAL_DRAFT_ONLY")
        or stale_run[4]
        != "TEST DATA — NOT FOR ORDERING; fabricated stale V1 lifecycle fixture"
        or not _is_sha256(stale_run[5])
        or stale_run[6] != "EMERGENCY_TRANSPARENT_V1"
        or stale_run[7].isoformat() != "2026-10-05"
        or stale_run[8] != "synthetic-stale-v1:2026-10-05"
        or active_run[1:4]
        != ("RUNNING", "PACKET_BUILT", "INTERNAL_DRAFT_ONLY")
        or active_run[4]
        != f"TEST DATA — NOT FOR ORDERING; prepared by {server_actor}"
        or active_run[5] != summary["input_fingerprint"]
        or active_run[6] != "EMERGENCY_TRANSPARENT_V2"
        or active_run[7].isoformat() != "2026-10-05"
        or active_run[8] != "browser-synthetic-20261005-v2"
    ):
        raise BrowserAcceptanceError("Monday run lifecycle or actor evidence differs")

    if len(retirement_rows) != 1 or len(retirement_audits) != 1:
        raise BrowserAcceptanceError("stale forecast retirement audit count differs")
    retirement = retirement_rows[0]
    audit = retirement_audits[0]
    before_run = retirement[12]
    after_run = retirement[13]
    changed_run_fields = {
        key for key in before_run if before_run.get(key) != after_run.get(key)
    }
    expected_audit_evidence = {
        "confirmation_sha256": retirement[11],
        "contract": "BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1",
        "reason": retirement[10],
        "retirement_run_id": retirement[0],
        "transaction_id": str(retirement[14]),
    }
    if (
        retirement[0] != stale_run_id
        or retirement[1] != stale_run[5]
        or retirement[1] != browser_state.get("retirementInputFingerprint")
        or retirement[2:7]
        != (
            "EMERGENCY_TRANSPARENT_V1",
            "RUNNING",
            "PREPARING",
            "FAILED",
            "FAILED",
        )
        or retirement[7:9] != (0, 0)
        or retirement[9] != server_actor
        or retirement[10]
        != "Synthetic retired forecast method requires V2 re-preparation"
        or retirement[10] != browser_state.get("retirementReason")
        or retirement[11] != browser_state.get("retirementConfirmationSha")
        or not _is_sha256(retirement[11])
        or set(before_run) != set(after_run)
        or changed_run_fields != {"status", "workflow_stage"}
        or before_run.get("status") != "RUNNING"
        or before_run.get("workflow_stage") != "PREPARING"
        or after_run.get("status") != "FAILED"
        or after_run.get("workflow_stage") != "FAILED"
        or stale_run[9] != after_run
        or audit[0:3] != ("runs", stale_run_id, "UPDATE")
        or audit[3] != before_run
        or audit[4] != after_run
        or audit[5] != server_actor
        or audit[6] != stale_run_id
        or audit[7] != retirement[15]
        or audit[8] != expected_audit_evidence
    ):
        raise BrowserAcceptanceError("stale forecast retirement evidence differs")
    if (
        stale_child_counts is None
        or any(int(value) != 0 for value in stale_child_counts[:-1])
        or int(stale_child_counts[-1]) != 1
    ):
        raise BrowserAcceptanceError("retired V1 acquired child evidence")

    if len(forecast_rows) != 1 or len(recommendation_metrics_rows) != 1:
        raise BrowserAcceptanceError("V2 forecast/recommendation row count differs")
    forecast_row = forecast_rows[0]
    recommendation_row = recommendation_metrics_rows[0]
    if (
        forecast_row[0:2] != (active_run_id, "1001")
        or any(value is not None for value in forecast_row[2:8])
        or forecast_row[8] != "EMERGENCY_TRANSPARENT_V2"
        or forecast_row[9].isoformat() != "2026-07-13"
        or forecast_row[10].isoformat() != "2026-10-04"
        or int(forecast_row[11]) != 3
        or recommendation_row[0:2] != (active_run_id, "1001")
    ):
        raise BrowserAcceptanceError("uncalculated forecast authority fields differ")
    diagnostics = forecast_row[12]
    input_manifest = forecast_row[13]
    metrics = recommendation_row[2]
    manifest_contexts = input_manifest.get("contexts") if isinstance(input_manifest, dict) else None
    eligible_contexts = [
        item
        for item in (manifest_contexts or [])
        if isinstance(item, dict) and str(item.get("variant_id")) == "1001"
    ]
    demand_payload = downloads_result["demand_evidence_payload"]
    sales_authority = (
        eligible_contexts[0].get("sales_authority")
        if len(eligible_contexts) == 1
        else None
    )
    sales_coverage = demand_payload.get("sales_coverage")
    if (
        len(eligible_contexts) != 1
        or input_manifest != downloads_result["input_manifest_payload"]
        or metrics != downloads_result["recommendation_metrics_payload"]
        or not isinstance(sales_authority, dict)
        or metrics.get("frozen_sales_authority") != sales_authority
        or not isinstance(sales_coverage, dict)
        or sales_coverage.get("authority_sha256")
        != _canonical_payload_sha256(sales_authority)
        or eligible_contexts[0].get("demand_evidence") != demand_payload
        or diagnostics.get("demand_evidence") != demand_payload
        or metrics.get("demand_evidence") != demand_payload
        or any(
            diagnostics.get(key) != expected
            for key, expected in {
                "forecast_status": "EMERGENCY_BASELINE_ONLY",
                "model_selection_status": "NOT_VALIDATED",
                "classification_status": "NOT_CALCULATED",
                "stockout_censoring_status": "EVIDENCE_UNAVAILABLE",
                "safety_stock_status": "NOT_CALCULATED",
            }.items()
        )
        or _validate_demand_evidence(demand_payload)["sha256"]
        != downloads_result["demand_evidence"]["sha256"]
        or any(
            metrics.get(key) is not None
            for key in (
                "demand_regime",
                "selected_model",
                "abc_class",
                "xyz_class",
                "in_stock_velocity",
                "safety_stock_units",
            )
        )
    ):
        raise BrowserAcceptanceError("frozen demand evidence diverges across surfaces")
    if exclusion_rows != [
        (
            server_actor,
            "Synthetic missing variant excluded from this run only",
            "OPEN",
            "2002",
        )
    ]:
        raise BrowserAcceptanceError("blocker exclusion database evidence differs")
    if (
        len(confirmation_rows) != 1
        or confirmation_rows[0][0:2] != (server_actor, "CONFIRM_MATERIAL_EDIT")
        or tuple(Decimal(value) for value in confirmation_rows[0][2:])
        != (Decimal("2"), Decimal("0"), Decimal("12"))
    ):
        raise BrowserAcceptanceError("material confirmation database evidence differs")
    if (
        len(review_rows) != 1
        or review_rows[0][0:2] != (server_actor, "EDIT_QUANTITY")
        or tuple(Decimal(value) for value in review_rows[0][2:5])
        != (Decimal("2"), Decimal("0"), Decimal("12"))
        or review_rows[0][5] != server_actor
        or review_rows[0][6] != browser_state.get("finalPriceTier")
    ):
        raise BrowserAcceptanceError("human review database evidence differs")
    if (
        len(po_rows) != 1
        or po_rows[0][0] != summary["draft_po_ids"][0]
        or po_rows[0][1] != active_run_id
        or po_rows[0][2] != "DRAFT"
        or Decimal(po_rows[0][3]) != Decimal("60.00")
        or Decimal(po_rows[0][4]) != Decimal("60.00")
        or po_rows[0][5]
        != f"TEST DATA — NOT FOR ORDERING; built by {server_actor}"
        or po_rows[0][6] != server_actor
        or not isinstance(po_rows[0][7], list)
        or len(po_rows[0][7]) != 1
        or po_rows[0][7][0].get("offer_id")
        != browser_state.get("selectedOfferId")
        or po_rows[0][7][0].get("final_price_tier")
        != browser_state.get("finalPriceTier")
    ):
        raise BrowserAcceptanceError("DRAFT purchase-order database evidence differs")
    final_tier = browser_state.get("finalPriceTier") or {}
    source_ladder_rows = downloads_result["selected_offer_input_evidence"].get(
        "applicable_price_ladder", {}
    ).get("rows", [])
    source_tier_rows = [
        item
        for item in source_ladder_rows
        if isinstance(item, list) and item[0] == final_tier.get("price_id")
    ]
    snapshot_tier_rows = [
        item
        for item in selected_price_snapshots
        if int(item[0]) == final_tier.get("run_price_snapshot_id")
    ]
    source_snapshot_match = (
        len(source_tier_rows) == 1
        and len(snapshot_tier_rows) == 1
        and tuple(
            None if value is None else str(value)
            for value in snapshot_tier_rows[0][1:10]
        )
        == tuple(
            None if value is None else str(value)
            for value in source_tier_rows[0][1:]
        )
    )
    if (
        len(selected_price_snapshots) != 2
        or any(
            int(item[1]) != browser_state.get("selectedOfferId")
            for item in selected_price_snapshots
        )
        or final_tier.get("run_price_snapshot_id")
        not in {int(item[0]) for item in selected_price_snapshots}
        or final_tier.get("level_type") != "BREAK"
        or Decimal(str(final_tier.get("break_qty"))) != Decimal("2")
        or final_tier.get("break_unit") != "CS"
        or Decimal(str(final_tier.get("case_price"))) != Decimal("30.0000")
        or Decimal(str(final_tier.get("unit_price"))) != Decimal("5.0000")
        or int(snapshot_tier_rows[0][10]) != final_tier.get("price_id")
        or snapshot_tier_rows[0][11]
        != final_tier.get("source_price_book_batch_id")
        or int(snapshot_tier_rows[0][12])
        != final_tier.get("source_price_book_row_number")
        or snapshot_tier_rows[0][13]
        != final_tier.get("supplier_price_authority_event_id")
        or not source_snapshot_match
    ):
        raise BrowserAcceptanceError("selected run price-tier identity differs")
    expected_artifact_hashes = {
        item["sha256"] for item in downloads_result["artifact_files"]
    }
    if set(str(facts["artifact_hashes"]).split("|")) != expected_artifact_hashes:
        raise BrowserAcceptanceError("downloaded and database artifact hashes differ")
    if (
        len(artifact_rows) != 2
        or any(item[0] != active_run_id for item in artifact_rows)
        or {item[1] for item in artifact_rows}
        != {"VENDOR_INTERNAL_CSV", "EMERGENCY_REVIEW_PACKET"}
        or any(
            item[4] != item[5]
            or item[7] != "TEST DATA — NOT FOR ORDERING"
            or item[8] != server_actor
            or item[3] not in expected_artifact_hashes
            for item in artifact_rows
        )
    ):
        raise BrowserAcceptanceError("database artifact evidence differs")
    csv_artifact = next(item for item in artifact_rows if item[1] == "VENDOR_INTERNAL_CSV")
    zip_artifact = next(item for item in artifact_rows if item[1] == "EMERGENCY_REVIEW_PACKET")
    if (
        csv_artifact[2] != "00000000-0000-4000-8000-000000000001"
        or csv_artifact[6] != "text/csv"
        or zip_artifact[2] is not None
        or zip_artifact[6] != "application/zip"
        or len(event_rows) != 1
        or event_rows[0][0] != active_run_id
        or event_rows[0][1] != event_rows[0][7]
        or event_rows[0][2] != zip_artifact[3]
        or event_rows[0][3:5] != (1, 2)
        or event_rows[0][5] != "DB_PAYLOAD_AND_STORAGE_READBACK_SHA256_V1"
        or event_rows[0][6] != server_actor
    ):
        raise BrowserAcceptanceError("packet build transaction evidence differs")
    if any(spoof_counts.values()):
        raise BrowserAcceptanceError("client-supplied actor was persisted")
    return {
        "state": state,
        "selected_offer_price_control": {
            "offers": int(row[0]),
            "active_offers": int(row[1]),
            "prices": int(row[2]),
            "current_prices": int(row[3]),
            "exact_selected_offer_price_rows": int(row[4]),
        },
        "draft_only": {"drafts": int(row[5]), "non_drafts": int(row[6])},
        "server_actor": server_actor,
        "retired_v1": {
            "run_id": stale_run_id,
            "confirmation_sha256": retirement[11],
            "transaction_id": int(retirement[14]),
            "child_counts": [int(value) for value in stale_child_counts],
        },
        "demand_evidence": downloads_result["demand_evidence"],
        "spoof_actor_persisted_rows": spoof_counts,
        "artifact_hashes": sorted(expected_artifact_hashes),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    source_identity = _source_identity()
    os.umask(0o077)
    if args.evidence_root.exists():
        raise BrowserAcceptanceError("evidence root must be new")
    args.evidence_root.mkdir(mode=0o700, parents=True)
    downloads = args.evidence_root / "downloads"
    downloads.mkdir(mode=0o700)
    state_path = args.evidence_root / "browser-state-test-data.json"
    server_log = args.evidence_root / "server.log"
    server_log.touch(mode=0o600)

    _database_facts(args.database_url, require_initialized=True)
    _runtime_paths(args.runtime_root)
    chromium = shutil.which("chromium") or shutil.which("chromium-browser")
    node = shutil.which("node")
    if chromium is None or node is None:
        raise BrowserAcceptanceError("Chromium and Node.js are required")
    if not SCRIPT.is_file():
        raise BrowserAcceptanceError("browser CDP audit script is absent")

    base_url = f"http://127.0.0.1:{args.port}"
    with tempfile.TemporaryDirectory(prefix="buffalo-purchasing-browser-") as temp:
        temp_root = Path(temp)
        profile = temp_root / "chromium-profile"
        cdp_port = _free_port()
        chromium_log = args.evidence_root / "chromium.stderr"
        with chromium_log.open("wb") as chromium_stderr:
            browser = subprocess.Popen(
                (
                    chromium,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--metrics-recording-only",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--host-resolver-rules=MAP * 0.0.0.0, EXCLUDE 127.0.0.1",
                    "--remote-debugging-address=127.0.0.1",
                    f"--remote-debugging-port={cdp_port}",
                    f"--user-data-dir={profile}",
                    "about:blank",
                ),
                env={"PATH": str(Path(chromium).parent), "LANG": "C.UTF-8"},
                stdout=subprocess.DEVNULL,
                stderr=chromium_stderr,
            )
            server: subprocess.Popen[bytes] | None = None
            log_handle: Any = None
            try:
                cdp_endpoint = _wait_cdp(browser, cdp_port)
                server, log_handle = _start_server(
                    database_url=args.database_url,
                    runtime_root=args.runtime_root,
                    port=args.port,
                    log_path=server_log,
                )
                price_phase = _run_phase(
                    node=node,
                    phase="price",
                    base_url=base_url,
                    cdp_endpoint=cdp_endpoint,
                    evidence=args.evidence_root,
                    downloads=downloads,
                    runtime_root=args.runtime_root,
                    state_path=state_path,
                )
                price_state = json.loads(state_path.read_text(encoding="utf-8"))
                price_batch_id = str(price_state["price"]["batchId"])
                _stop_server(server, log_handle, args.runtime_root)
                server = None
                log_handle = None
                apply_backup_manifest = Path(
                    backup_v2(args.database_url, args.runtime_root, price_batch_id)
                )
                apply_backup_label = apply_backup_manifest.parent.name
                server, log_handle = _start_server(
                    database_url=args.database_url,
                    runtime_root=args.runtime_root,
                    port=args.port,
                    log_path=server_log,
                    price_apply_backup_label=apply_backup_label,
                )
                phase1 = _run_phase(
                    node=node,
                    phase="phase1",
                    base_url=base_url,
                    cdp_endpoint=cdp_endpoint,
                    evidence=args.evidence_root,
                    downloads=downloads,
                    runtime_root=args.runtime_root,
                    state_path=state_path,
                )
                _stop_server(server, log_handle, args.runtime_root)
                server = None
                log_handle = None
                server, log_handle = _start_server(
                    database_url=args.database_url,
                    runtime_root=args.runtime_root,
                    port=args.port,
                    log_path=server_log,
                    price_apply_backup_label=apply_backup_label,
                )
                phase2 = _run_phase(
                    node=node,
                    phase="phase2",
                    base_url=base_url,
                    cdp_endpoint=cdp_endpoint,
                    evidence=args.evidence_root,
                    downloads=downloads,
                    runtime_root=args.runtime_root,
                    state_path=state_path,
                )
            finally:
                try:
                    if server is not None and log_handle is not None:
                        _stop_server(server, log_handle, args.runtime_root)
                finally:
                    if browser.poll() is None:
                        browser.terminate()
                        try:
                            browser.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            browser.kill()
                            browser.wait(timeout=10)

    final_source_identity = _source_identity()
    if final_source_identity != source_identity:
        raise BrowserAcceptanceError(
            "source identity changed during browser acceptance"
        )
    browser_state = json.loads(state_path.read_text(encoding="utf-8"))
    downloads_result = _validate_downloads(
        downloads, args.runtime_root, browser_state
    )
    database_result = _database_acceptance(
        args.database_url, downloads_result, browser_state
    )
    result = {
        "format": "BUFFALO_LOCAL_PURCHASING_BROWSER_ACCEPTANCE_SUMMARY_V1",
        "safety_label": "TEST DATA — NOT FOR ORDERING",
        "source_identity": final_source_identity,
        "source_identity_reverified_at_completion": True,
        "database": _database_facts(args.database_url, require_initialized=True),
        "phase1": phase1,
        "phase2": phase2,
        "price_phase": price_phase,
        "price_apply_backup": {
            "label": apply_backup_label,
            "manifest_sha256": _sha256(apply_backup_manifest),
        },
        "downloads": downloads_result,
        "database_acceptance": database_result,
        "limitations": [
            "loopback owner-demo evidence only",
            "mapping authority remains SHADOW_ONLY; a distinct attested synthetic Monday consumer uses the confirmed selection only in this local demo",
            "demand evidence is an emergency baseline: point-in-time inventory remains UNKNOWN, model selection/FVA is NOT_VALIDATED, and safety stock is NOT_CALCULATED",
            "real/default selected-offer consumption remains disabled and lacks independent database-trigger enforcement",
            "no Shopify, supplier, deployment, publication, release, or order action occurred",
        ],
    }
    summary = args.evidence_root / "ACCEPTANCE_SUMMARY.json"
    summary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary.chmod(0o600)
    secret_values = [
        (args.runtime_root / name).read_bytes().rstrip(b"\n")
        for name in ("local-auth.secret", "review-token.secret", "price-token.secret")
    ]
    for evidence_file in sorted(args.evidence_root.rglob("*")):
        if not evidence_file.is_file() or evidence_file.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
        }:
            continue
        evidence_bytes = evidence_file.read_bytes()
        if any(secret and secret in evidence_bytes for secret in secret_values):
            raise BrowserAcceptanceError(
                f"secret bytes appeared in evidence file {evidence_file.name}"
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    result = run(args)
    print(
        json.dumps(
            {
                "passed": True,
                "phase1_assertions": result["phase1"]["assertionCount"],
                "phase2_assertions": result["phase2"]["assertionCount"],
                "price_assertions": result["price_phase"]["assertionCount"],
                "evidence_root": str(args.evidence_root),
                "state_sha256": result["database_acceptance"]["state"]["sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
