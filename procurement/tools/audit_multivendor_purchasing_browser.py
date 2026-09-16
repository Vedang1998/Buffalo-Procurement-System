#!/usr/bin/env python3
"""Owned Chromium acceptance for the synthetic two-vendor price-to-DRAFT flow."""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
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


def _validate_initial_downloads(downloads: Path, state: dict[str, Any]) -> dict[str, Any]:
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
    expected_lines = {
        "1001": ("2", "0", "30.0000", "60.00"),
        "4001": ("2", "0", "42.0000", "84.00"),
        "4002": ("1", "0", "18.0000", "18.00"),
    }
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
    if any(
        row["vendor_po_total"] != "109.00"
        or row["vendor_merchandise_total"] != "102.00"
        or row["vendor_below_minimum_fee"] != "7.00"
        or row["vendor_delivery_fee"] != "7.00"
        or row["minimum_disposition"] != "PAY_FEE"
        for row in western
    ):
        raise BrowserAcceptanceError("Western vendor-scoped fee economics differ")
    if southern[0]["vendor_po_total"] != "60.00":
        raise BrowserAcceptanceError("Southern uploaded-price total differs")

    with zipfile.ZipFile(zip_files[0]) as archive:
        names = archive.namelist()
        embedded = sorted(name for name in names if name.endswith(".internal.csv"))
        if len(names) != 13 or len(embedded) != 2:
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
    return {
        "files": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in files
        ],
        "packet_members": sorted(names),
        "vendor_rows": by_vendor,
        "totals": {"merchandise": "162.00", "fees": "7.00", "total": "169.00"},
    }


def _database_acceptance(database_url: str, run_id: str) -> dict[str, Any]:
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
            "SELECT workflow_stage FROM runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        if stage is None or stage[0] != "PACKET_BUILT":
            raise BrowserAcceptanceError("workflow stage differs")
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
        if exact_pos != [
            ("Synthetic Southern", "60.00", "0.00", "60.00", 1),
            ("Synthetic Western Acceptance", "102.00", "7.00", "109.00", 2),
        ]:
            raise BrowserAcceptanceError(f"durable vendor economics differ: {exact_pos}")
        variants = conn.execute(
            """SELECT l.variant_id,l.cases,l.loose_units,l.line_total
                 FROM purchase_order_lines l JOIN purchase_orders p USING(po_id)
                WHERE p.run_id=%s ORDER BY l.variant_id""",
            (run_id,),
        ).fetchall()
        if [(str(v), int(c), int(loose), str(total)) for v,c,loose,total in variants] != [
            ("1001", 2, 0, "60.00"),
            ("4001", 2, 0, "84.00"),
            ("4002", 1, 0, "18.00"),
        ]:
            raise BrowserAcceptanceError("durable line economics differ")
        blockers = conn.execute(
            """SELECT e.variant_id,e.exception_type,x.action
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
        if [(str(a), str(b)) for a,b in rejected] != [
            ("1001", "EDIT_QUANTITY"), ("4001", "EDIT_QUANTITY"),
            ("4002", "ACCEPT"), ("4005", "REJECT"),
        ]:
            raise BrowserAcceptanceError("review dispositions differ")
        artifacts = conn.execute(
            """SELECT artifact_type,vendor_id::text,sha256,size_bytes
                 FROM monday_run_artifacts WHERE run_id=%s
                ORDER BY artifact_type,vendor_id NULLS LAST""",
            (run_id,),
        ).fetchall()
        return {
            "counts": [int(value) for value in counts],
            "stage": stage[0],
            "vendor_economics": exact_pos,
            "lines": [[str(value) for value in row] for row in variants],
            "blockers": [[str(value) for value in row] for row in blockers],
            "reviews": [[str(value) for value in row] for row in rejected],
            "artifacts": [[None if value is None else str(value) for value in row] for row in artifacts],
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
        source_runtime, database=DATABASE, port=source_pg_port,
        fixture_profile="multivendor-v2",
    )
    source_url = _local_database_url(DATABASE, source_pg_port)
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
            initial_downloads = _validate_initial_downloads(downloads, browser_state)
            source_acceptance = _database_acceptance(
                source_url, str(browser_state["runId"])
            )
            source_storage = _storage_inventory(source_runtime / "storage")
            v1_manifest = backup(source_url, source_runtime)
            target_init = initialize_local_database(
                target_runtime, database=DATABASE, port=target_pg_port,
                empty_restore_target=True,
            )
            if source_init["system_identifier"] == target_init["system_identifier"]:
                raise BrowserAcceptanceError("source and recovery clusters are not distinct")
            target_url = _local_database_url(DATABASE, target_pg_port)
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
                target_url, str(final_state["runId"])
            )
            if target_acceptance["state"] != source_acceptance["state"]:
                raise BrowserAcceptanceError("recovery replay changed durable state")
            result = {
                "contract": "BUFFALO_MULTIVENDOR_PRICE_TO_DRAFT_ACCEPTANCE_V1",
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
            _write_json(args.evidence_root / "MULTIVENDOR_ACCEPTANCE_SUMMARY.json", result)
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
