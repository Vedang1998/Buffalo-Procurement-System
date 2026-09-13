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

    if read("status", "--porcelain=v1"):
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
    process = subprocess.Popen(
        (
            sys.executable,
            str(LAUNCHER),
            "serve",
            "--database-url",
            database_url,
            "--runtime-root",
            str(runtime_root),
            "--port",
            str(port),
        ),
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
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            _signal_process_group(child_pid, signal.SIGKILL)
            raise BrowserAcceptanceError("local Uvicorn child survived launcher stop")


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
        "case_price": "10.0100",
        "line_merchandise_total": "20.02",
        "line_loose_order_fee": "0.00",
        "line_total": "20.02",
        "vendor_merchandise_total": "20.02",
        "vendor_below_minimum_fee": "0.00",
        "vendor_po_total": "20.02",
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
            or Decimal(str(summary["merchandise_total"])) != Decimal("20.02")
            or Decimal(str(summary["po_total"])) != Decimal("20.02")
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
            or Decimal(str(vendor.get("merchandise_total"))) != Decimal("20.02")
            or Decimal(str(vendor.get("po_total"))) != Decimal("20.02")
            or Decimal(str(vendor.get("delivery_fee"))) != Decimal("0")
            or vendor.get("below_vendor_minimum") is not False
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
            or Decimal(str(evidence.get("recommended_line_cash"))) != Decimal("10.01")
            or Decimal(str(evidence.get("incremental_line_cash"))) != Decimal("10.01")
            or Decimal(str(evidence.get("final_line_cash"))) != Decimal("20.02")
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
            "approved_unit_cost": "1.6683",
            "approved_case_price": "10.0100",
            "approved_merchandise_total": "20.02",
            "approved_loose_order_fee": "0.00",
            "approved_line_total": "20.02",
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
        if set(metadata) != {"contract", "authority", "candidate", "batch"} or (
            metadata.get("contract")
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
        != "synthetic-unresolved-review-v1"
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
        != "fabricated-authoritative-format-review-v1"
        or valid["batch"].get("source_is_simulation") is not False
        or not str(valid["batch"].get("source_artifact_ref", "")).startswith(
            "synthetic-packet:"
        )
        or valid["batch"].get("supplier_period_scope")
        != {"kind": "FABRICATED_TEST_PERIOD"}
        or (valid["batch"].get("prerequisites") or {}).get("packet_contract")
        != "BUFFALO_SYNTHETIC_MAPPING_REVIEW_PACKET_V1"
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
        "packet_summary": summary,
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
            " WHERE o.offer_id=1 AND o.supplier_sku='SUP-001' AND o.active "
            " AND p.case_price=10.01 AND p.unit_price=1.6683),"
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
            "input_fingerprint FROM runs"
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
            "evidence_json #>> '{review,actor}' FROM review_decisions"
        ).fetchall()
        po_rows = conn.execute(
            "SELECT po_id::text,po_status,merchandise_total,po_total,notes,"
            "reconciliation_evidence->>'economics_confirmed_by' FROM purchase_orders"
        ).fetchall()
        artifact_rows = conn.execute(
            "SELECT artifact_type,vendor_id::text,sha256,size_bytes,octet_length(payload),"
            "content_type,safety_label,created_by FROM monday_run_artifacts "
            "ORDER BY artifact_type"
        ).fetchall()
        event_rows = conn.execute(
            "SELECT artifact_set_sha256,packet_sha256,csv_count,artifact_count,"
            "verification_method,actor,monday_artifact_set_sha256(run_id) "
            "FROM monday_packet_build_events"
        ).fetchall()
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
        ):
            spoof_counts[table] = int(
                conn.execute(
                    f"SELECT count(*) FROM {table} AS t WHERE to_jsonb(t)::text LIKE %s",
                    (f"%{spoof_actor}%",),
                ).fetchone()[0]
            )
    assert row is not None
    expected = (1, 1, 1, 1, 1, 1, 0)
    if tuple(int(value) for value in row) != expected:
        raise BrowserAcceptanceError("database nonauthority/control totals differ")
    facts = state["facts"]
    exact_counts = {
        "review_batches": 2,
        "review_candidates": 2,
        "mapping_decisions": 2,
        "selection_events": 1,
        "selection_heads": 1,
        "runs": 1,
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
            "synthetic-unresolved-review-v1",
            "fabricated-authoritative-format-review-v1",
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
        or selections[0][0:3] != ("SELECT", "1001", 1)
        or selections[0][3] != server_actor
        or selections[0][4] != "procurement.offer.select"
        or not _is_sha256(selections[0][5])
    ):
        raise BrowserAcceptanceError("routine selection principal evidence differs")
    summary = downloads_result["packet_summary"]
    if (
        len(run_rows) != 1
        or run_rows[0][0] != browser_state["runId"]
        or run_rows[0][1:4]
        != ("RUNNING", "PACKET_BUILT", "INTERNAL_DRAFT_ONLY")
        or run_rows[0][4]
        != f"TEST DATA — NOT FOR ORDERING; prepared by {server_actor}"
        or run_rows[0][5] != summary["input_fingerprint"]
    ):
        raise BrowserAcceptanceError("Monday run lifecycle or actor evidence differs")
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
    ):
        raise BrowserAcceptanceError("human review database evidence differs")
    if (
        len(po_rows) != 1
        or po_rows[0][0] != summary["draft_po_ids"][0]
        or po_rows[0][1] != "DRAFT"
        or Decimal(po_rows[0][2]) != Decimal("20.02")
        or Decimal(po_rows[0][3]) != Decimal("20.02")
        or po_rows[0][4]
        != f"TEST DATA — NOT FOR ORDERING; built by {server_actor}"
        or po_rows[0][5] != server_actor
    ):
        raise BrowserAcceptanceError("DRAFT purchase-order database evidence differs")
    expected_artifact_hashes = {
        item["sha256"] for item in downloads_result["artifact_files"]
    }
    if set(str(facts["artifact_hashes"]).split("|")) != expected_artifact_hashes:
        raise BrowserAcceptanceError("downloaded and database artifact hashes differ")
    if (
        len(artifact_rows) != 2
        or {item[0] for item in artifact_rows}
        != {"VENDOR_INTERNAL_CSV", "EMERGENCY_REVIEW_PACKET"}
        or any(
            item[3] != item[4]
            or item[6] != "TEST DATA — NOT FOR ORDERING"
            or item[7] != server_actor
            or item[2] not in expected_artifact_hashes
            for item in artifact_rows
        )
    ):
        raise BrowserAcceptanceError("database artifact evidence differs")
    csv_artifact = next(item for item in artifact_rows if item[0] == "VENDOR_INTERNAL_CSV")
    zip_artifact = next(item for item in artifact_rows if item[0] == "EMERGENCY_REVIEW_PACKET")
    if (
        csv_artifact[1] != "00000000-0000-4000-8000-000000000001"
        or csv_artifact[5] != "text/csv"
        or zip_artifact[1] is not None
        or zip_artifact[5] != "application/zip"
        or len(event_rows) != 1
        or event_rows[0][0] != event_rows[0][6]
        or event_rows[0][1] != zip_artifact[2]
        or event_rows[0][2:4] != (1, 2)
        or event_rows[0][4] != "DB_PAYLOAD_AND_STORAGE_READBACK_SHA256_V1"
        or event_rows[0][5] != server_actor
    ):
        raise BrowserAcceptanceError("packet build transaction evidence differs")
    if any(spoof_counts.values()):
        raise BrowserAcceptanceError("client-supplied actor was persisted")
    return {
        "state": state,
        "legacy_offer_price_control": {
            "offers": int(row[0]),
            "active_offers": int(row[1]),
            "prices": int(row[2]),
            "current_prices": int(row[3]),
            "exact_original_offer_price_rows": int(row[4]),
        },
        "draft_only": {"drafts": int(row[5]), "non_drafts": int(row[6])},
        "server_actor": server_actor,
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
        "source_identity": source_identity,
        "database": _database_facts(args.database_url, require_initialized=True),
        "phase1": phase1,
        "phase2": phase2,
        "downloads": downloads_result,
        "database_acceptance": database_result,
        "limitations": [
            "loopback owner-demo evidence only",
            "mapping selection is shadow-only",
            "legacy active offer and CURRENT synthetic price remain the Monday consumer",
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
                "evidence_root": str(args.evidence_root),
                "state_sha256": result["database_acceptance"]["state"]["sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
