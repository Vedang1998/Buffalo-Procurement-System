"""Focused synthetic tests for the private-research Chromium audit harness."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import date
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from procurement_os.private_research_projection import (
    build_private_research_projection,
    canonical_private_research_projection_bytes,
    filter_private_research_rows,
)
from test_private_research_projection import (
    hypothesis,
    intake,
    variant,
)
from test_private_research_v2 import (
    ORIGINAL_AUTHORITY,
    SEED_ALIASES,
    TERMINAL_AUTHORITY,
    capture as v2_capture,
    sha as v2_sha,
    source_identity as v2_source_identity,
)
from procurement_os.private_research_v2 import (
    build_private_v2_composite_history,
    build_private_v2_research_input,
    build_private_v2_research_projection,
)
import procurement_os.private_research_v2 as private_research_v2
import procurement_os.private_research_v3_corrected as corrected


TOOL = Path(__file__).resolve().parents[1] / "tools" / "audit_private_research_browser.py"
SCRIPT = TOOL.with_suffix(".mjs")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "audit_private_research_browser_test", TOOL
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("private browser audit module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_workspace() -> dict[str, object]:
    mapping_evidence = {
        "candidate_disposition": "REVIEW_REQUIRED",
        "blockers": {
            "identity": ["SOURCE_IDENTITY_UNRESOLVED"],
            "program_price": ["NO_CURRENT_PRICE_OR_TIER_AUTHORITY"],
        },
        "relationship_record_sha256": "d" * 64,
    }
    projection = build_private_research_projection(
        intake(
            [
                variant(
                    "100",
                    hypotheses=[
                        hypothesis(
                            "100",
                            "Alpha",
                            "ALPHA-100",
                            "8.00",
                            source_occurrence_id="source-occurrence-browser-100",
                            mapping_confidence={
                                "status": "UNAPPROVED",
                                "basis": "SYNTHETIC_BROWSER_REVIEW",
                            },
                            mapping_evidence=mapping_evidence,
                        )
                    ],
                ),
                variant(
                    "200",
                    hypotheses=[hypothesis("200", "Zulu", "ZULU-200", "9.00")],
                    with_forecast=True,
                ),
            ]
        )
    )
    artifacts = {
        "owner-preview.html": b"<!doctype html><p>synthetic review only</p>\n",
        "owner-worksheet.csv": b"variant_id,status\r\n100,REVIEW_ONLY\r\n",
        "coverage.json": b'{"synthetic":true}\n',
        "projection.json": (
            json.dumps(projection, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"),
    }
    records = [
        {
            "name": name,
            "path": name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "media_type": "application/octet-stream",
        }
        for name, data in sorted(artifacts.items())
    ]
    return {
        "manifest": {
            "contract": "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V1",
            "data_mode": "PRIVATE_REAL_SOURCE_REVIEW",
            "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
            "operational_authority": False,
            "workspace_id": "f" * 64,
            "intake_sha256": "e" * 64,
            "projection_contract": "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V1",
            "projection_sha256": projection["projection_sha256"],
            "artifacts": records,
        },
        "projection": projection,
        "artifacts": artifacts,
    }


def _synthetic_v2_workspace() -> dict[str, object]:
    with TemporaryDirectory(prefix="buffalo-private-v2-browser-") as directory:
        root = Path(directory)
        extension = root / "extension.json"
        replacement = root / "replacement.json"
        for path, value in (
            (
                extension,
                v2_capture(
                    date(2026, 5, 4),
                    54,
                    contract="BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_EXTENSION_V2",
                ),
            ),
            (
                replacement,
                v2_capture(
                    date(2026, 6, 27),
                    84,
                    contract="BUFFALO_PRIVATE_SHOPIFY_HISTORICAL_REPLACEMENT_V2",
                ),
            ),
        ):
            path.write_text(
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
        identity = v2_source_identity(
            v2_sha(extension.read_bytes()), v2_sha(replacement.read_bytes())
        )
        base = intake(
            [
                variant(
                    "100",
                    hypotheses=[hypothesis("100", "Alpha", "ALPHA-100", "8.00")],
                ),
                variant("200"),
            ]
        )
        with mock.patch.object(
            private_research_v2,
            "_require_registered_source_identity",
            side_effect=private_research_v2.validate_source_identity_record,
        ):
            composite = build_private_v2_composite_history(
                base,
                extension_capture_path=extension,
                replacement_capture_path=replacement,
                source_identity=identity,
                seed_alias_path=SEED_ALIASES,
                original_authority_path=ORIGINAL_AUTHORITY,
                terminal_authority_path=TERMINAL_AUTHORITY,
            )
            research_input = build_private_v2_research_input(
                base, composite_history=composite, source_identity=identity
            )
            projection = build_private_v2_research_projection(research_input, base)
    artifacts = {
        "owner-preview.html": b"<!doctype html><p>synthetic V2 review only</p>\n",
        "owner-worksheet.csv": b"variant_id,status\r\n100,RESEARCH_ONLY\r\n",
        "coverage.json": b'{"synthetic":true}\n',
        "projection.json": (
            json.dumps(projection, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8"),
    }
    records = [
        {
            "name": name,
            "path": name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "media_type": "application/octet-stream",
        }
        for name, data in sorted(artifacts.items())
    ]
    return {
        "manifest": {
            "contract": "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V2",
            "data_mode": "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
            "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
            "operational_authority": False,
            "workspace_id": "9" * 64,
            "projection_sha256": projection["projection_sha256"],
            "projection_contract": "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2",
            "base_intake_id": research_input["base_intake"]["intake_id"],
            "base_intake_sha256": research_input["base_intake"]["sha256"],
            "artifacts": records,
        },
        "projection": projection,
        "artifacts": artifacts,
    }


def _phase_result(tool, expectations, phase: str, base_url: str) -> dict[str, object]:
    if "tooling" not in expectations:
        expectations["tooling"] = {
            "node_version": process_version(),
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        }
    return {
        "contract": tool.CONTRACT,
        "phase": phase,
        "base_url": base_url,
        "passed": True,
        "assertions": [
            {"id": identifier, "passed": True}
            for identifier in expectations["assertion_ids"]
        ],
        "semantic_hashes": expectations["semantic_hashes"],
        "workspace_hashes": expectations["workspace_hashes"],
        "counts": expectations["counts"],
        "artifact_hashes": {
            item["name"]: item["sha256"] for item in expectations["artifacts"]
        },
        "external_requests": [],
        "blocked_external_requests": [],
        "external_websocket_requests": [],
        "unexpected_scheme_requests": [],
        "tooling": {
            **expectations["tooling"],
            "browser": {
                "product": "Chrome/fixture",
                "protocol_version": "1.3",
                "js_version": "fixture",
            },
        },
        "target_summary": {
            "all_observed_types": ["page"],
            "observed_types": ["page"],
            "by_type": {"page": 1},
            "inert": 0,
            "unsupported": 0,
            "tracked": 1,
            "created": 1,
            "destroyed": 0,
            "detached": 0,
            "active_guarded": 1,
            "live_detached": 0,
            "attached": 1,
            "guarded": 1,
            "resumed": 1,
            "uncreated": 0,
            "unattached": 0,
            "unguarded": 0,
            "unresumed": 0,
        },
        "request_count": 18,
        "response_count": 18,
    }


def process_version() -> str:
    completed = subprocess.run(
        [shutil.which("node") or "node", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _start_synthetic_viewer(
    port,
    workspace,
    secret,
    tool,
    *,
    auth_required=True,
    attack_origin=None,
):
    projection = workspace["projection"]
    manifest = workspace["manifest"]
    artifacts = workspace["artifacts"]
    expected_auth = "Basic " + base64.b64encode(
        b"private:" + secret
    ).decode("ascii")
    attack_pages_remaining = [1]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def _send(self, status, body, content_type="text/plain", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if (
                auth_required
                and parsed.path != "/health"
                and self.headers.get("Authorization") != expected_auth
            ):
                self._send(
                    401,
                    b"Private review authentication failed",
                    extra={
                        "WWW-Authenticate": (
                            'Basic realm="Buffalo private research", charset="UTF-8"'
                        )
                    },
                )
                return
            if parsed.path == "/worker.js" and attack_origin:
                nested_source = (
                    "setTimeout(() => fetch("
                    + json.dumps(f"{attack_origin}/nested-worker-timer")
                    + ").catch(() => {}), 25);"
                    + "setTimeout(() => { try { new WebSocket("
                    + json.dumps(
                        f"{str(attack_origin).replace('http://', 'ws://')}/nested-worker-socket"
                    )
                    + "); } catch (_) {} }, 30);"
                    + "setTimeout(() => close(), 100);"
                )
                script = (
                    "const nestedSource="
                    + json.dumps(nested_source)
                    + ";const nestedUrl=URL.createObjectURL(new Blob([nestedSource],"
                    + "{type:'text/javascript'}));new Worker(nestedUrl);"
                    + "setInterval(() => {}, 1000);"
                ).encode("utf-8")
                self._send(200, script, "text/javascript; charset=utf-8")
                return
            if parsed.path == "/popup" and attack_origin:
                page = (
                    "<!doctype html><script>setTimeout(() => fetch("
                    + json.dumps(f"{attack_origin}/popup-timer")
                    + ").catch(() => {}), 25)</script>"
                ).encode("utf-8")
                self._send(200, page, "text/html; charset=utf-8")
                return
            if parsed.path == "/health":
                self._send(
                    200,
                    json.dumps(
                        {
                            "ok": True,
                            "service": "buffalo-private-research-review",
                            "mode": "PRIVATE_REAL_SOURCE_REVIEW",
                            "operational_authority": False,
                        },
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    "application/json",
                )
                return
            if parsed.path == "/private-research/manifest":
                self._send(
                    200,
                    json.dumps(manifest, separators=(",", ":")).encode("utf-8"),
                    "application/json",
                )
                return
            if parsed.path == "/private-research/projection":
                self._send(
                    200,
                    json.dumps(projection, separators=(",", ":")).encode("utf-8"),
                    "application/json",
                )
                return
            artifact_prefix = "/private-research/artifacts/"
            if parsed.path.startswith(artifact_prefix):
                name = parsed.path.removeprefix(artifact_prefix)
                data = artifacts.get(name)
                if not isinstance(data, bytes):
                    self._send(404, b"absent")
                    return
                self._send(
                    200,
                    data,
                    "application/octet-stream",
                    {"Content-Disposition": f'attachment; filename="{name}"'},
                )
                return
            if parsed.path != "/private-research":
                self._send(404, b'{"detail":"Not Found"}', "application/json")
                return

            parameters = parse_qs(parsed.query, keep_blank_values=True)
            query = parameters.get("q", [""])[0]
            vendor = parameters.get("vendor", [""])[0]
            status = parameters.get("status", [""])[0]
            stockout = parameters.get("stockout", [""])[0]
            rows = filter_private_research_rows(
                projection, query=query, vendor=vendor, status=status
            )
            if stockout:
                rows = [row for row in rows if tool._stockout_status(row) == stockout]
            selected = rows[:50]
            inject_attack = bool(attack_origin and attack_pages_remaining[0])
            if inject_attack:
                attack_pages_remaining[0] -= 1
            cards = "".join(
                "<article class='record' data-variant-id='"
                + html.escape(str(row["shopify_variant_id"]), quote=True)
                + "'><details><summary>Evidence and research diagnostics</summary><pre>"
                + html.escape(
                    json.dumps(
                        row,
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    quote=True,
                )
                + "</pre></details></article>"
                for row in selected
            )
            vendor_options = "".join(
                f"<option value='{html.escape(name, quote=True)}'>{html.escape(name)}</option>"
                for name in projection["vendor_names"]
            )
            artifact_links = "".join(
                f"<a href='/private-research/artifacts/{name}'>{name}</a>"
                for name in tool.ARTIFACT_NAMES
            )
            page = (
                "<!doctype html><html><body>"
                + (
                    "<script>"
                    "setTimeout(() => fetch("
                    + json.dumps(f"{attack_origin}/page-timer")
                    + ").catch(() => {}), 50);"
                    "setTimeout(() => { try { new WebSocket("
                    + json.dumps(
                        f"{str(attack_origin).replace('http://', 'ws://')}/page-socket"
                    )
                    + "); } catch (_) {} }, 30);"
                    "new Worker('/worker.js');"
                    "setTimeout(() => window.open('/popup'), 20);"
                    "</script>"
                    if inject_attack
                    else ""
                )
                + artifact_links
                + "<form method='get' action='/private-research'>"
                + f"<input name='q' value='{html.escape(query, quote=True)}'>"
                + f"<select name='vendor'><option value=''></option>{vendor_options}</select>"
                + f"<input name='status' value='{html.escape(status, quote=True)}'>"
                + "<select name='stockout'><option value=''></option>"
                + "<option value='NOT_CAPTURED'>Not captured</option>"
                + "<option value='CAPTURED_IN_RESEARCH_INPUT'>Captured</option>"
                + "<option value='INCOMPLETE_OR_UNKNOWN'>Unknown</option></select>"
                + "<button type='submit'>Filter</button></form>"
                + f"<p>Showing {len(selected)} of {len(rows)} matched rows</p>"
                + cards
                + "</body></html>"
            ).encode("utf-8")
            self._send(200, page, "text/html; charset=utf-8")

        def _deny_write(self):
            self._send(403, b"Route is not authorized")

        do_POST = _deny_write
        do_PUT = _deny_write
        do_PATCH = _deny_write
        do_DELETE = _deny_write

    class ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True

    server = ReusableServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _start_capture_listener(port, received):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def do_GET(self):
            received.append(
                {
                    "path": urlsplit(self.path).path,
                    "authorization": self.headers.get("Authorization"),
                }
            )
            body = b"unexpected external listener contact"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    class ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True

    server = ReusableServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class PrivateResearchBrowserAuditTests(unittest.TestCase):
    def test_expectations_reject_unknown_mixed_and_partial_contract_tuples(self):
        tool = _load_tool()
        cases = (
            (("manifest", "contract"), "UNKNOWN_WORKSPACE"),
            (
                ("manifest", "projection_contract"),
                "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2",
            ),
            (
                ("projection", "data_mode"),
                "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
            ),
        )
        for path, replacement in cases:
            workspace = _synthetic_workspace()
            workspace[path[0]][path[1]] = replacement
            with self.subTest(path=path), self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError,
                "canonical workspace contract tuple differs",
            ):
                tool._build_expectations(workspace)

    def test_canonical_expectations_cover_counts_filters_details_and_artifacts(self):
        tool = _load_tool()
        workspace = _synthetic_workspace()
        expectations = tool._build_expectations(workspace)
        self.assertEqual(expectations["contract"], tool.CONTRACT)
        self.assertEqual(expectations["counts"]["research_rows"], 2)
        self.assertEqual(
            [item["name"] for item in expectations["filters"]],
            ["search", "vendor", "status", "stockout"],
        )
        self.assertTrue(
            all(item["total"] >= 1 for item in expectations["filters"])
        )
        self.assertEqual(
            set(expectations["initial"]["detail_row"]),
            set(workspace["projection"]["research_rows"][0]),
        )
        self.assertNotIn("projection", expectations)
        self.assertEqual(set(expectations["projection_endpoint"]), {"bytes", "sha256"})
        self.assertEqual(
            expectations["projection_endpoint"]["sha256"],
            tool._sha256(
                json.dumps(
                    workspace["projection"],
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
        )
        self.assertEqual(
            {item["name"] for item in expectations["artifacts"]},
            set(tool.ARTIFACT_NAMES),
        )
        self.assertEqual(
            expectations["route_table"],
            [
                {"path": item["path"], "methods": list(item["methods"])}
                for item in tool.EXPECTED_APP_ROUTE_TABLE
            ],
        )
        self.assertTrue(
            all(route["methods"] == ["GET"] for route in expectations["route_table"])
        )
        malformed_v2 = deepcopy(_synthetic_workspace())
        malformed_v2["manifest"]["contract"] = (
            "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V2"
        )
        with self.assertRaisesRegex(
            tool.PrivateResearchBrowserAuditError,
            "canonical workspace contract tuple differs",
        ):
            tool._build_expectations(malformed_v2)

        v2_expectations = tool._build_expectations(_synthetic_v2_workspace())
        self.assertEqual(v2_expectations["initial"]["total"], 2)
        self.assertEqual(
            set(v2_expectations["initial"]["detail_row"]["scenario_results"]),
            {"H3", "H10", "H17"},
        )
        self.assertIn(
            "recorded_sales_coverage",
            v2_expectations["evidence"]["required_fields"],
        )
        self.assertIn(
            "V2 research source, policy, and 138-day coverage",
            v2_expectations["page_markers"],
        )
        self.assertTrue(v2_expectations["evidence"]["visible_markers"])

    def test_corrected_expectations_bind_joint_counts_markers_and_raw_projection(self):
        from test_private_research_v3_corrected import (
            PrivateResearchV3CorrectedTests,
        )

        tool = _load_tool()
        helper = PrivateResearchV3CorrectedTests(
            methodName="test_primary_counts_and_memberships_are_exact"
        )
        with helper._built() as built:
            projection = built["corrected_projection"]
            projection_bytes = canonical_private_research_projection_bytes(projection)
            artifacts = {
                "owner-preview.html": b"<!doctype html><p>corrected fixture</p>\n",
                "owner-worksheet.csv": b"row_type,shopify_variant_id\n",
                "coverage.json": b'{"corrected":true}\n',
                "projection.json": projection_bytes,
            }
            records = [
                {
                    "name": name,
                    "path": name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "media_type": "application/octet-stream",
                }
                for name, data in sorted(artifacts.items())
            ]
            child_input = built["input"]
            corrected_input_sha = hashlib.sha256(
                corrected.canonical_json_bytes(child_input)
            ).hexdigest()
            workspace = {
                "manifest": {
                    "contract": "BUFFALO_PRIVATE_REAL_RESEARCH_WORKSPACE_V3_CORRECTED_V1",
                    "data_mode": "PRIVATE_REAL_DATA_DEVELOPMENT_RESEARCH_ONLY",
                    "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
                    "operational_authority": False,
                    "workspace_id": "f" * 64,
                    "projection_sha256": projection["projection_sha256"],
                    "projection_contract": projection["contract"],
                    "corrected_input_sha256": corrected_input_sha,
                    "base_intake_id": child_input["base_intake"]["intake_id"],
                    "base_intake_sha256": child_input["base_intake"]["sha256"],
                    "artifacts": records,
                },
                "projection": projection,
                "artifacts": artifacts,
            }
            expectations = tool._build_expectations(workspace)

            wrong_disposition = deepcopy(workspace)
            wrong_h3 = wrong_disposition["projection"]["joint_forecast_research"][
                "primary_status_counts"
            ]["H3"]
            wrong_h3["BLOCKED"] = 1
            wrong_h3["NOT_APPLICABLE"] -= 1
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError,
                "corrected-V3 H3 result controls differ",
            ):
                tool._build_expectations(wrong_disposition)

            wrong_sidecars = deepcopy(workspace)
            wrong_sidecars["projection"]["forecast_sidecars"] = []
            with (
                mock.patch.object(
                    tool,
                    "filter_private_research_rows",
                    return_value=projection["owner_worksheet"],
                ),
                self.assertRaisesRegex(
                    tool.PrivateResearchBrowserAuditError,
                    "projection counts are incomplete",
                ),
            ):
                tool._build_expectations(wrong_sidecars)

            null_wape = deepcopy(workspace)
            null_rows = null_wape["projection"]["owner_worksheet"]
            calculated = next(
                row
                for row in null_rows
                if row["scenario_results"]["H3"].get("primary_status")
                == "CALCULATED"
            )
            calculated["scenario_results"]["H3"]["horizon_evaluation_wape"] = None
            with mock.patch.object(
                tool,
                "filter_private_research_rows",
                return_value=null_rows,
            ):
                null_expectations = tool._build_expectations(null_wape)
            self.assertIn("—", null_expectations["evidence"]["visible_markers"])
            self.assertNotIn(
                "None", null_expectations["evidence"]["visible_markers"]
            )

            missing_input_hash = deepcopy(workspace)
            del missing_input_hash["manifest"]["corrected_input_sha256"]
            with (
                mock.patch.object(
                    tool,
                    "filter_private_research_rows",
                    return_value=projection["owner_worksheet"],
                ),
                self.assertRaisesRegex(
                    tool.PrivateResearchBrowserAuditError,
                    "workspace identity hashes differ",
                ),
            ):
                tool._build_expectations(missing_input_hash)

        self.assertEqual(len(corrected_input_sha), 64)
        self.assertEqual(
            expectations["workspace_hashes"]["corrected_input_sha256"],
            corrected_input_sha,
        )
        self.assertNotIn("intake_sha256", expectations["workspace_hashes"])
        self.assertEqual(
            expectations["counts"],
            {"coverage_rows": 5, "owner_worksheet": 5, "forecast_sidecars": 2},
        )
        self.assertIn("CORRECTED JOINT H3/H10/H17", expectations["page_markers"])
        self.assertIn(
            "Joint H3/H10/H17 confidence",
            expectations["evidence"]["visible_markers"],
        )
        self.assertEqual(
            expectations["projection_endpoint"],
            {
                "bytes": len(projection_bytes),
                "sha256": hashlib.sha256(projection_bytes).hexdigest(),
            },
        )

    def test_phase_validation_rejects_external_requests(self):
        tool = _load_tool()
        expectations = tool._build_expectations(_synthetic_workspace())
        base_url = "http://127.0.0.1:18876"
        result = _phase_result(tool, expectations, "initial", base_url)
        summary = tool._validate_phase_result(
            result,
            phase="initial",
            base_url=base_url,
            expectations=expectations,
        )
        self.assertEqual(summary["assertions"], len(tool.ASSERTION_IDS))
        changed = dict(result)
        changed["external_requests"] = [
            {"method": "GET", "url": "https://external.invalid/"}
        ]
        with self.assertRaisesRegex(
            tool.PrivateResearchBrowserAuditError, "result differs"
        ):
            tool._validate_phase_result(
                changed,
                phase="initial",
                base_url=base_url,
                expectations=expectations,
            )
        duplicate = json.loads(json.dumps(result))
        duplicate["assertions"][-1] = dict(duplicate["assertions"][0])
        with self.assertRaisesRegex(
            tool.PrivateResearchBrowserAuditError, "result differs"
        ):
            tool._validate_phase_result(
                duplicate,
                phase="initial",
                base_url=base_url,
                expectations=expectations,
            )
        failed_assertion = json.loads(json.dumps(result))
        failed_assertion["assertions"][0]["passed"] = False
        with self.assertRaisesRegex(
            tool.PrivateResearchBrowserAuditError, "result differs"
        ):
            tool._validate_phase_result(
                failed_assertion,
                phase="initial",
                base_url=base_url,
                expectations=expectations,
            )
        wrong_script = json.loads(json.dumps(result))
        wrong_script["tooling"]["script_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            tool.PrivateResearchBrowserAuditError, "result differs"
        ):
            tool._validate_phase_result(
                wrong_script,
                phase="initial",
                base_url=base_url,
                expectations=expectations,
            )
        incomplete_target = json.loads(json.dumps(result))
        incomplete_target["target_summary"]["created"] = 0
        incomplete_target["target_summary"]["uncreated"] = 1
        with self.assertRaisesRegex(
            tool.PrivateResearchBrowserAuditError, "result differs"
        ):
            tool._validate_phase_result(
                incomplete_target,
                phase="initial",
                base_url=base_url,
                expectations=expectations,
            )

    def test_source_binding_rejects_missing_mismatched_dirty_and_changed_repo(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-browser-git-") as temporary:
            repo = Path(temporary).resolve()

            def git(*arguments):
                return subprocess.run(
                    ["git", "-C", str(repo), *arguments],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.name", "Browser Audit Test")
            git("config", "user.email", "browser-audit@example.invalid")
            source = repo / "source.txt"
            source.write_text("first\n", encoding="utf-8")
            git("add", "source.txt")
            git("commit", "-q", "-m", "first")
            first = tool._repository_source_identity(repo)
            self.assertEqual(
                tool._validate_source_object_relation(
                    repo, commit=first["commit"], tree=first["tree"]
                ),
                first,
            )
            with TemporaryDirectory(
                prefix="buffalo-private-browser-git-shim-"
            ) as shim_temporary:
                shim_root = Path(shim_temporary)
                shim = shim_root / "git"
                shim.write_text("#!/bin/sh\necho spoofed-git\n", encoding="utf-8")
                shim.chmod(0o700)
                with mock.patch.dict(os.environ, {"PATH": str(shim_root)}):
                    self.assertEqual(tool._repository_source_identity(repo), first)

            git("update-index", "--assume-unchanged", "source.txt")
            source.write_text("evil!\n", encoding="utf-8")
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError, "non-normal tracked flags"
            ):
                tool._repository_source_identity(repo)
            git("update-index", "--no-assume-unchanged", "source.txt")
            source.write_text("first\n", encoding="utf-8")

            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError, "unavailable"
            ):
                tool._validate_source_object_relation(
                    repo, commit="0" * 40, tree=first["tree"]
                )

            source.write_text("dirty\n", encoding="utf-8")
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError, "must be clean"
            ):
                tool._repository_source_identity(repo)

            git("add", "source.txt")
            git("commit", "-q", "-m", "second")
            second = tool._repository_source_identity(repo)
            self.assertNotEqual(first, second)
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError, "relation differs"
            ):
                tool._validate_source_object_relation(
                    repo, commit=first["commit"], tree=second["tree"]
                )
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError, "changed during"
            ):
                tool._assert_repository_source_identity(first, repo)

    def test_route_inventory_rejects_added_write_surface(self):
        tool = _load_tool()
        from procurement_os.private_research_app import app

        added = mock.Mock(path="/private-research/write", methods={"POST"})
        with mock.patch.object(app.router, "routes", [*app.routes, added]):
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError,
                "route table or method inventory differs",
            ):
                tool._private_app_route_table()

    def test_source_binding_rejects_ignored_stale_bytecode(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-browser-pyc-") as temporary:
            repo = Path(temporary).resolve()

            def git(*arguments):
                subprocess.run(
                    ["git", "-C", str(repo), *arguments],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            git("init", "-q")
            git("config", "user.name", "Browser Audit Test")
            git("config", "user.email", "browser-audit@example.invalid")
            source = repo / "procurement" / "src" / "fixture.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            git("add", ".gitignore", "procurement/src/fixture.py")
            git("commit", "-q", "-m", "fixture")
            cache = Path(tool.importlib.util.cache_from_source(str(source)))
            cache.parent.mkdir()
            subprocess.run(
                [sys.executable, "-m", "py_compile", str(source)],
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                tool._repository_source_identity(repo)["commit"],
                subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip(),
            )
            raw = cache.read_bytes()
            cached = tool.marshal.loads(raw[16:])
            malicious = compile(
                "raise RuntimeError('ignored bytecode executed')\n",
                cached.co_filename,
                "exec",
                dont_inherit=True,
                optimize=sys.flags.optimize,
            )
            cache.write_bytes(raw[:16] + tool.marshal.dumps(malicious))
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError, "bytecode cache differs"
            ):
                tool._repository_source_identity(repo)

    def test_two_phase_orchestration_reuses_origin_and_removes_runtime_secret(self):
        tool = _load_tool()
        workspace = _synthetic_workspace()
        expectations = tool._build_expectations(workspace)
        expectations["tooling"] = {
            "node_version": process_version(),
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        }
        with TemporaryDirectory(prefix="buffalo-private-browser-test-") as temporary:
            parent = Path(temporary).resolve()
            workspace_root = (
                parent
                / "private-research"
                / "workspaces"
                / str(workspace["manifest"]["workspace_id"])
            )
            workspace_root.mkdir(parents=True, mode=0o700)
            workspace_root.chmod(0o700)
            runtime_root = parent / "runtime"
            evidence_root = parent / "evidence"
            args = argparse.Namespace(
                workspace_root=workspace_root,
                runtime_root=runtime_root,
                evidence_root=evidence_root,
                port=18876,
            )
            secret_value = b"synthetic-private-browser-secret-value"
            compact = tool._compact_runtime_expectations(expectations)
            self.assertEqual(
                set(compact),
                {
                    "workspace_id",
                    "route_table",
                    "workspace_hashes",
                    "semantic_hashes",
                    "counts",
                    "artifacts",
                    "assertion_ids",
                    "tooling",
                },
            )
            self.assertNotIn("manifest", compact)
            self.assertNotIn("projection", compact)
            self.assertEqual(
                compact["workspace_id"], workspace["manifest"]["workspace_id"]
            )

            def initialize(root):
                secret = root / "private-viewer.secret"
                secret.write_bytes(secret_value + b"\n")
                secret.chmod(0o600)
                return secret

            phase_calls: list[tuple[str, str, Path]] = []

            def run_phase(**kwargs):
                phase_calls.append(
                    (kwargs["phase"], kwargs["base_url"], kwargs["downloads"])
                )
                return _phase_result(
                    tool, expectations, kwargs["phase"], kwargs["base_url"]
                )

            fake_process = mock.Mock()
            fake_handle = mock.Mock()
            source_identity = {"commit": "a" * 40, "tree": "b" * 40}
            previous_umask = os.umask(0o077)
            os.umask(previous_umask)
            try:
                with (
                    mock.patch.object(
                        tool,
                        "read_private_research_workspace",
                        return_value=workspace,
                    ),
                    mock.patch.object(tool, "_initialize_runtime", side_effect=initialize),
                    mock.patch.object(tool, "_enable_child_subreaper"),
                    mock.patch.object(
                        tool,
                        "_repository_source_identity",
                        return_value=source_identity,
                    ),
                    mock.patch.object(
                        tool, "_assert_repository_source_identity"
                    ) as assert_source,
                    mock.patch.object(
                        tool,
                        "_start_browser",
                        return_value=(fake_process, fake_handle),
                    ),
                    mock.patch.object(
                        tool,
                        "_start_server",
                        return_value=(fake_process, fake_handle, source_identity),
                    ) as start_server,
                    mock.patch.object(tool, "_wait_cdp", return_value="http://127.0.0.1:19999"),
                    mock.patch.object(tool, "_run_cdp_phase", side_effect=run_phase),
                    mock.patch.object(tool, "_stop_server") as stop_server,
                    mock.patch.object(tool, "_stop_browser") as stop_browser,
                    mock.patch.object(
                        tool,
                        "_node_runtime_info",
                        return_value={"version": process_version(), "major": "22"},
                    ),
                    mock.patch.object(tool.gc, "collect") as collect,
                    mock.patch.object(tool, "_assert_port_free"),
                    mock.patch.object(
                        tool.shutil,
                        "which",
                        side_effect=lambda name: f"/synthetic/bin/{name}",
                    ),
                ):
                    result = tool.run(args)
            finally:
                os.umask(previous_umask)

            self.assertEqual(
                [(phase, origin) for phase, origin, _ in phase_calls],
                [
                    ("initial", "http://127.0.0.1:18876"),
                    ("restart", "http://127.0.0.1:18876"),
                ],
            )
            self.assertEqual(start_server.call_count, 2)
            self.assertEqual(stop_server.call_count, 2)
            self.assertEqual(stop_browser.call_count, 2)
            self.assertEqual(assert_source.call_count, 5)
            collect.assert_called_once_with()
            self.assertFalse(runtime_root.exists())
            self.assertTrue(result["cleanup"]["runtime_root_removed"])
            self.assertEqual(result["source"], source_identity)
            self.assertEqual(result["route_table"], expectations["route_table"])
            retained = b"".join(
                path.read_bytes()
                for path in evidence_root.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(secret_value, retained)
            self.assertTrue((evidence_root / "acceptance-result.json").is_file())

    def test_roots_reject_symlinked_ancestors_and_cleanup_is_exact(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-browser-path-") as temporary:
            parent = Path(temporary).resolve()
            real = parent / "real"
            real.mkdir(mode=0o700)
            linked = parent / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError, "symlinked"
            ):
                tool._prepare_empty_root(
                    linked / "runtime", label="runtime root"
                )

            runtime = parent / "runtime"
            tool._prepare_empty_root(runtime, label="runtime root")
            for name in ("private-viewer.secret", "private-viewer.pid"):
                item = runtime / name
                item.write_text("synthetic\n", encoding="utf-8")
                item.chmod(0o600)
            tool._cleanup_runtime(runtime)
            self.assertFalse(runtime.exists())

            evidence = parent / "evidence"
            evidence.mkdir(mode=0o700)
            nested = evidence / "nested"
            nested.mkdir(mode=0o700)
            secret = b"synthetic-secret-that-must-not-survive"
            (nested / "contaminated.json").write_bytes(b'{"value":"' + secret + b'"}')
            (evidence / "otherwise-safe.json").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                tool.PrivateResearchBrowserAuditError,
                "fresh evidence root was removed",
            ):
                tool._assert_no_auth_material(evidence, secret)
            self.assertFalse(evidence.exists())

            raced = parent / "raced-evidence"
            raced.mkdir(mode=0o700)
            with mock.patch.object(
                tool,
                "_assert_repository_source_identity",
                side_effect=tool.PrivateResearchBrowserAuditError(
                    "repository source identity changed during browser acceptance"
                ),
            ):
                with self.assertRaisesRegex(
                    tool.PrivateResearchBrowserAuditError, "changed during"
                ):
                    tool._finalize_acceptance_evidence(
                        evidence_root=raced,
                        result={"source": {"commit": "a" * 40, "tree": "b" * 40}},
                        secret=b"",
                        expected_source={"commit": "a" * 40, "tree": "b" * 40},
                    )
            self.assertFalse(raced.exists())

    def test_server_launch_rejects_launcher_source_identity_mismatch(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-browser-launch-") as temporary:
            root = Path(temporary).resolve()
            workspace = root / "workspace"
            runtime = root / "runtime"
            workspace.mkdir()
            runtime.mkdir()
            log_path = root / "viewer.log"
            fake_process = mock.Mock(pid=os.getpid(), returncode=0)
            expected = {"commit": "a" * 40, "tree": "b" * 40}
            observed = {
                "pid": os.getpid(),
                "source_commit": "c" * 40,
                "source_tree": "d" * 40,
            }
            with (
                mock.patch.object(tool.subprocess, "Popen", return_value=fake_process),
                mock.patch.object(tool, "_register_owned_process"),
                mock.patch.object(tool, "_wait_health"),
                mock.patch.object(tool, "_viewer_pid_record", return_value=observed),
                mock.patch.object(
                    tool, "_stop_server", side_effect=lambda _p, handle, _r: handle.close()
                ) as stop_server,
            ):
                with self.assertRaisesRegex(
                    tool.PrivateResearchBrowserAuditError,
                    "launcher source identity differs",
                ):
                    tool._start_server(
                        workspace,
                        runtime,
                        18876,
                        log_path,
                        expected,
                    )
            stop_server.assert_called_once()

            matching = {
                "pid": 424242,
                "source_commit": expected["commit"],
                "source_tree": expected["tree"],
                "process_start_ticks": 12345,
            }
            fake_registry = object()
            with (
                mock.patch.object(tool.subprocess, "Popen", return_value=fake_process),
                mock.patch.object(tool, "_register_owned_process"),
                mock.patch.object(tool, "_wait_health"),
                mock.patch.object(tool, "_viewer_pid_record", return_value=matching),
                mock.patch.object(
                    tool, "_owned_registry", return_value=fake_registry
                ),
                mock.patch.object(tool, "_register_owned_identity") as register_child,
            ):
                returned_process, returned_handle, returned_source = tool._start_server(
                    workspace,
                    runtime,
                    18876,
                    log_path,
                    expected,
                )
            returned_handle.close()
            self.assertIs(returned_process, fake_process)
            self.assertEqual(returned_source, expected)
            register_child.assert_called_once_with(
                fake_registry, pid=424242, start_ticks=12345
            )

    def test_process_cleanup_kills_detached_setsid_listener_after_leader_exits(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-browser-process-") as temporary:
            marker = Path(temporary) / "child.pid"
            port = tool._free_port()
            token = tool._new_process_token("detached-test")
            child_code = """
import os
from pathlib import Path
import socket
import sys
import time

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", int(sys.argv[2])))
listener.listen()
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
time.sleep(60)
"""
            parent_code = """
import os
from pathlib import Path
import subprocess
import sys
import time

child_env = {
    "PATH": os.environ.get("PATH", ""),
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
subprocess.Popen(
    [sys.executable, "-c", sys.argv[3], sys.argv[1], sys.argv[2]],
    env=child_env,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
marker = Path(sys.argv[1])
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not marker.exists():
    time.sleep(0.01)
"""
            tool._enable_child_subreaper()
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    parent_code,
                    str(marker),
                    str(port),
                    child_code,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=tool._minimal_environment(
                    path=os.environ.get("PATH", ""), process_token=token
                ),
                start_new_session=True,
            )
            tool._register_owned_process(
                process, token=token, label="detached fixture"
            )
            try:
                process.wait(timeout=5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not marker.exists():
                    time.sleep(0.01)
                child_pid = int(marker.read_text(encoding="ascii"))
                token_marker = (
                    f"{tool._PROCESS_TOKEN_ENV}={token}".encode("utf-8")
                )
                self.assertNotIn(
                    token_marker,
                    Path(f"/proc/{child_pid}/environ").read_bytes().split(b"\0"),
                )
                owned = tool._discover_owned_processes(
                    tool._owned_registry(process)
                )
                self.assertIn(child_pid, owned)
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
                tool._stop_process_group(process, timeout=1)
                self.assertEqual(
                    tool._discover_owned_processes(tool._owned_registry(process)),
                    {},
                )
                tool._assert_port_free(port, label="detached fixture")
            finally:
                tool._stop_process_group(process, timeout=1)

            graceful_marker = Path(temporary) / "graceful-ready"
            graceful_code = """
import signal
from pathlib import Path
import sys
import time

def stop_requested(_signum, _frame):
    raise KeyboardInterrupt

signal.signal(signal.SIGTERM, stop_requested)
Path(sys.argv[1]).write_text("ready", encoding="ascii")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    time.sleep(0.2)
"""
            graceful_token = tool._new_process_token("graceful-test")
            graceful = subprocess.Popen(
                [sys.executable, "-c", graceful_code, str(graceful_marker)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=tool._minimal_environment(
                    path=os.environ.get("PATH", ""),
                    process_token=graceful_token,
                ),
                start_new_session=True,
            )
            tool._register_owned_process(
                graceful, token=graceful_token, label="graceful fixture"
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not graceful_marker.exists():
                time.sleep(0.01)
            self.assertTrue(graceful_marker.exists())
            tool._stop_process_group(graceful, timeout=2)
            self.assertEqual(graceful.returncode, 0)

    def test_registration_failure_cleanup_kills_real_detached_listener(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-browser-register-") as temporary:
            root = Path(temporary).resolve()
            marker = root / "child.pid"
            log_path = root / "child.log"
            port = tool._free_port()
            token = tool._new_process_token("registration-failure")
            code = """
import os
from pathlib import Path
import socket
import sys
import time

child = os.fork()
if child == 0:
    os.setsid()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", int(sys.argv[2])))
    listener.listen()
    Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
    time.sleep(60)
    raise SystemExit(0)
deadline = time.monotonic() + 5
while time.monotonic() < deadline and not Path(sys.argv[1]).exists():
    time.sleep(0.01)
time.sleep(60)
"""
            tool._enable_child_subreaper()
            handle = log_path.open("wb", buffering=0)
            process = subprocess.Popen(
                [sys.executable, "-c", code, str(marker), str(port)],
                env=tool._minimal_environment(
                    path=os.environ.get("PATH", ""), process_token=token
                ),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                start_new_session=True,
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
            tool._cleanup_failed_owned_launch(
                process,
                handle,
                token=token,
                label="registration failure fixture",
            )
            self.assertTrue(handle.closed)
            tool._assert_port_free(port, label="registration failure fixture")

    def test_node_script_is_dependency_free_and_syntax_valid(self):
        tool = _load_tool()
        self.assertEqual(tool.SERVER_READY_SECONDS, 5400)
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "Target.setAutoAttach",
            "Target.targetCreated",
            "Target.targetDestroyed",
            "const APPLICATION_TARGET_TYPES = new Set(GUARDED_TARGET_TYPES)",
            "waitForDebuggerOnStart: true",
            "Fetch.continueRequest",
            "Fetch.failRequest",
            "Browser.setDownloadBehavior",
            "blocked_external_requests",
            "external_websocket_requests",
            "owner-preview.html",
            "owner-worksheet.csv",
            "coverage.json",
            "projection.json",
            'crypto.subtle.digest("SHA-256", body)',
            "const PAGE_LOAD_TIMEOUT_MS = 120000",
            "void loaded.catch(() => {});",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn("Network.setExtraHTTPHeaders", source)
        self.assertNotIn("node_modules", source)
        completed = subprocess.run(
            [sys.executable, "-c", "import shutil; print(shutil.which('node') or '')"],
            capture_output=True,
            text=True,
            check=True,
        )
        node = completed.stdout.strip()
        if not node:
            self.skipTest("Node.js is unavailable")
        subprocess.run(
            [node, "--check", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=True,
        )
        runtime = _load_tool()._node_runtime_info(node)
        self.assertGreaterEqual(int(runtime["major"]), 22)

    def test_harness_import_excludes_operational_services_and_database_driver(self):
        probe = """
import importlib.util
import json
import pathlib
import sys

tool = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("private_browser_import_probe", tool)
if spec is None or spec.loader is None:
    raise RuntimeError("browser audit import spec is unavailable")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
forbidden = {
    "procurement_os.api",
    "procurement_os.draft_po",
    "procurement_os.emergency_packet",
    "procurement_os.monday_run",
    "procurement_os.persistent_mapping",
    "procurement_os.po_csv",
    "procurement_os.price_book",
    "procurement_os.procurement_review",
    "procurement_os.recommendations",
    "procurement_os.synthetic_price_replacement",
    "psycopg",
}
print(json.dumps(sorted(forbidden & set(sys.modules))))
"""
        completed = subprocess.run(
            [sys.executable, "-c", probe, str(TOOL)],
            cwd=TOOL.parents[2],
            env={
                **os.environ,
                "PYTHONPATH": str(TOOL.parents[1] / "src"),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(completed.stdout), [], completed.stderr)

    def test_real_chromium_cdp_against_synthetic_read_only_fixture(self):
        tool = _load_tool()
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        node = shutil.which("node")
        if chromium is None or node is None:
            self.skipTest("Chromium and Node.js are unavailable")
        workspace = _synthetic_workspace()
        expectations = tool._build_expectations(workspace)
        expectations["tooling"] = {
            "node_version": process_version(),
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        }
        secret = b"synthetic-private-browser-secret-value"
        with TemporaryDirectory(prefix="buffalo-private-browser-cdp-") as temporary:
            root = Path(temporary).resolve()
            evidence = root / "evidence"
            downloads = root / "downloads"
            initial_downloads = downloads / "initial"
            restart_downloads = downloads / "restart"
            for directory in (evidence, downloads, initial_downloads, restart_downloads):
                directory.mkdir(mode=0o700)
            secret_path = root / "private-viewer.secret"
            secret_path.write_bytes(secret + b"\n")
            secret_path.chmod(0o600)
            expectations_path = root / "expectations.json"
            tool._write_private_json(expectations_path, expectations)
            state_path = evidence / "restart-state.json"
            app_port = tool._free_port()
            cdp_port = tool._free_port()
            while cdp_port == app_port:
                cdp_port = tool._free_port()
            server = None
            thread = None
            browser = None
            browser_handle = None
            try:
                for phase, phase_downloads in (
                    ("initial", initial_downloads),
                    ("restart", restart_downloads),
                ):
                    server, thread = _start_synthetic_viewer(
                        app_port, workspace, secret, tool
                    )
                    try:
                        browser, browser_handle = tool._start_browser(
                            chromium,
                            root / f"profile-{phase}",
                            cdp_port,
                            root / f"chromium-{phase}.stderr",
                        )
                        cdp = tool._wait_cdp(browser, cdp_port)
                        raw = tool._run_cdp_phase(
                            node=node,
                            phase=phase,
                            base_url=f"http://127.0.0.1:{app_port}",
                            cdp_endpoint=cdp,
                            evidence_root=evidence,
                            downloads=phase_downloads,
                            secret_path=secret_path,
                            expectations_path=expectations_path,
                            state_path=state_path,
                        )
                        checked = tool._validate_phase_result(
                            raw,
                            phase=phase,
                            base_url=f"http://127.0.0.1:{app_port}",
                            expectations=expectations,
                        )
                        self.assertEqual(
                            checked["assertions"], len(tool.ASSERTION_IDS)
                        )
                    finally:
                        if browser is not None and browser_handle is not None:
                            tool._stop_browser(browser, browser_handle)
                            browser = None
                            browser_handle = None
                            tool._assert_port_free(cdp_port, label="test CDP")
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)
                        server = None
                        thread = None
                        tool._assert_port_free(app_port, label="test viewer")
                tool._assert_no_auth_material(evidence, secret)
            finally:
                if browser is not None and browser_handle is not None:
                    tool._stop_browser(browser, browser_handle)
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)

    def test_real_browser_rejects_auth_disabled_private_fixture(self):
        tool = _load_tool()
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        node = shutil.which("node")
        if chromium is None or node is None:
            self.skipTest("Chromium and Node.js are unavailable")
        workspace = _synthetic_workspace()
        expectations = tool._build_expectations(workspace)
        expectations["tooling"] = {
            "node_version": process_version(),
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        }
        secret = b"synthetic-private-browser-secret-value"
        with TemporaryDirectory(prefix="buffalo-private-browser-noauth-") as temporary:
            root = Path(temporary).resolve()
            evidence = root / "evidence"
            downloads = root / "downloads"
            evidence.mkdir(mode=0o700)
            downloads.mkdir(mode=0o700)
            secret_path = root / "private-viewer.secret"
            secret_path.write_bytes(secret + b"\n")
            secret_path.chmod(0o600)
            expectations_path = root / "expectations.json"
            tool._write_private_json(expectations_path, expectations)
            app_port = tool._free_port()
            cdp_port = tool._free_port()
            while cdp_port == app_port:
                cdp_port = tool._free_port()
            server, thread = _start_synthetic_viewer(
                app_port,
                workspace,
                secret,
                tool,
                auth_required=False,
            )
            browser, browser_handle = tool._start_browser(
                chromium, root / "profile", cdp_port, root / "chromium.stderr"
            )
            try:
                cdp = tool._wait_cdp(browser, cdp_port)
                with self.assertRaisesRegex(
                    tool.PrivateResearchBrowserAuditError,
                    r"v2\.auth\.missing\.index",
                ):
                    tool._run_cdp_phase(
                        node=node,
                        phase="initial",
                        base_url=f"http://127.0.0.1:{app_port}",
                        cdp_endpoint=cdp,
                        evidence_root=evidence,
                        downloads=downloads,
                        secret_path=secret_path,
                        expectations_path=expectations_path,
                        state_path=root / "restart-state.json",
                    )
                failed = json.loads(
                    (evidence / "initial-browser-results.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertFalse(failed["passed"])
                tool._assert_no_auth_material(evidence, secret)
            finally:
                tool._stop_browser(browser, browser_handle)
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_all_target_guard_blocks_nested_worker_second_listener_popup_and_timers(self):
        tool = _load_tool()
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        node = shutil.which("node")
        if chromium is None or node is None:
            self.skipTest("Chromium and Node.js are unavailable")
        workspace = _synthetic_workspace()
        expectations = tool._build_expectations(workspace)
        expectations["tooling"] = {
            "node_version": process_version(),
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        }
        expectations["adversarial"] = {
            "popup_path": "/popup",
            "detach_live_target": True,
        }
        secret = b"synthetic-private-browser-secret-value"
        with TemporaryDirectory(prefix="buffalo-private-browser-attack-") as temporary:
            root = Path(temporary).resolve()
            evidence = root / "evidence"
            downloads = root / "downloads"
            evidence.mkdir(mode=0o700)
            downloads.mkdir(mode=0o700)
            secret_path = root / "private-viewer.secret"
            secret_path.write_bytes(secret + b"\n")
            secret_path.chmod(0o600)
            expectations_path = root / "expectations.json"
            tool._write_private_json(expectations_path, expectations)
            app_port = tool._free_port()
            external_port = tool._free_port()
            cdp_port = tool._free_port()
            while len({app_port, external_port, cdp_port}) != 3:
                external_port = tool._free_port()
                cdp_port = tool._free_port()
            received: list[dict[str, object]] = []
            capture, capture_thread = _start_capture_listener(
                external_port, received
            )
            server, thread = _start_synthetic_viewer(
                app_port,
                workspace,
                secret,
                tool,
                attack_origin=f"http://127.0.0.1:{external_port}",
            )
            browser, browser_handle = tool._start_browser(
                chromium, root / "profile", cdp_port, root / "chromium.stderr"
            )
            try:
                cdp = tool._wait_cdp(browser, cdp_port)
                with self.assertRaisesRegex(
                    tool.PrivateResearchBrowserAuditError,
                    r"v2\.cdp\.all_targets_guarded",
                ):
                    tool._run_cdp_phase(
                        node=node,
                        phase="initial",
                        base_url=f"http://127.0.0.1:{app_port}",
                        cdp_endpoint=cdp,
                        evidence_root=evidence,
                        downloads=downloads,
                        secret_path=secret_path,
                        expectations_path=expectations_path,
                        state_path=root / "restart-state.json",
                    )
                failed = json.loads(
                    (evidence / "initial-browser-results.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertTrue(failed["external_requests"])
                self.assertTrue(failed["blocked_external_requests"])
                self.assertTrue(failed["external_websocket_requests"])
                summary = failed["target_summary"]
                self.assertEqual(summary["attached"], summary["guarded"])
                self.assertEqual(summary["attached"], summary["resumed"])
                self.assertEqual(summary["tracked"], summary["created"])
                self.assertEqual(summary["tracked"], summary["attached"])
                self.assertEqual(summary["uncreated"], 0)
                self.assertEqual(summary["unattached"], 0)
                self.assertGreaterEqual(summary["detached"], 1)
                self.assertGreaterEqual(summary["live_detached"], 1)
                self.assertIn("worker", summary["observed_types"])
                self.assertGreaterEqual(summary["by_type"]["worker"], 2)
                self.assertGreaterEqual(summary["by_type"]["page"], 2)
                self.assertGreaterEqual(summary["destroyed"], 1)
                self.assertTrue(
                    any(
                        item["url"].endswith("/nested-worker-timer")
                        for item in failed["external_requests"]
                    )
                )
                self.assertTrue(
                    any(
                        item["url"].endswith("/nested-worker-socket")
                        for item in failed["external_websocket_requests"]
                    )
                )
                self.assertNotIn(
                    "v2.cdp.all_targets_guarded",
                    {item["id"] for item in failed["assertions"]},
                )
                self.assertEqual(received, [])
                tool._assert_no_auth_material(evidence, secret)
            finally:
                tool._stop_browser(browser, browser_handle)
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                capture.shutdown()
                capture.server_close()
                capture_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
