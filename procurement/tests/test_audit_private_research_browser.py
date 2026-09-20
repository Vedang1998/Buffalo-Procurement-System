"""Focused synthetic tests for the private-research Chromium audit harness."""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from procurement_os.private_research_projection import (
    build_private_research_projection,
    filter_private_research_rows,
)
from procurement.tests.test_private_research_projection import (
    hypothesis,
    intake,
    variant,
)


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
    projection = build_private_research_projection(
        intake(
            [
                variant(
                    "100",
                    hypotheses=[
                        hypothesis("100", "Alpha", "ALPHA-100", "8.00")
                    ],
                ),
                variant(
                    "200",
                    hypotheses=[
                        hypothesis("200", "Zulu", "ZULU-200", "9.00")
                    ],
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
            "authority": "PRIVATE_REAL_SOURCE_REVIEW_ONLY",
            "operational_authority": False,
            "workspace_id": "f" * 64,
            "projection_sha256": projection["projection_sha256"],
            "artifacts": records,
        },
        "projection": projection,
        "artifacts": artifacts,
    }


def _phase_result(tool, expectations, phase: str, base_url: str) -> dict[str, object]:
    return {
        "contract": tool.CONTRACT,
        "phase": phase,
        "base_url": base_url,
        "passed": True,
        "assertions": [{"passed": True}] * 24,
        "semantic_hashes": expectations["semantic_hashes"],
        "counts": expectations["counts"],
        "artifact_hashes": {
            item["name"]: item["sha256"] for item in expectations["artifacts"]
        },
        "external_requests": [],
        "blocked_external_requests": [],
        "request_count": 18,
        "response_count": 18,
    }


def _start_synthetic_viewer(port, workspace, secret, tool):
    projection = workspace["projection"]
    manifest = workspace["manifest"]
    artifacts = workspace["artifacts"]
    expected_auth = "Basic " + base64.b64encode(
        b"private:" + secret
    ).decode("ascii")

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
            if parsed.path != "/health" and self.headers.get("Authorization") != expected_auth:
                self._send(
                    401,
                    b"Private review authentication failed",
                    extra={"WWW-Authenticate": 'Basic realm="synthetic private review"'},
                )
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

    class ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True

    server = ReusableServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class PrivateResearchBrowserAuditTests(unittest.TestCase):
    def test_canonical_expectations_cover_counts_filters_details_and_artifacts(self):
        tool = _load_tool()
        expectations = tool._build_expectations(_synthetic_workspace())
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
            set(expectations["projection"]["research_rows"][0]),
        )
        self.assertEqual(
            {item["name"] for item in expectations["artifacts"]},
            set(tool.ARTIFACT_NAMES),
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
        self.assertEqual(summary["assertions"], 24)
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

    def test_two_phase_orchestration_reuses_origin_and_removes_runtime_secret(self):
        tool = _load_tool()
        workspace = _synthetic_workspace()
        expectations = tool._build_expectations(workspace)
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
                    mock.patch.object(
                        tool,
                        "_start_browser",
                        return_value=(fake_process, fake_handle),
                    ),
                    mock.patch.object(
                        tool,
                        "_start_server",
                        return_value=(fake_process, fake_handle),
                    ) as start_server,
                    mock.patch.object(tool, "_wait_cdp", return_value="http://127.0.0.1:19999"),
                    mock.patch.object(tool, "_run_cdp_phase", side_effect=run_phase),
                    mock.patch.object(tool, "_stop_server") as stop_server,
                    mock.patch.object(tool, "_stop_browser") as stop_browser,
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
            stop_browser.assert_called_once()
            self.assertFalse(runtime_root.exists())
            self.assertFalse(result["runtime_secret_retained"])
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

    def test_node_script_is_dependency_free_and_syntax_valid(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "Network.setExtraHTTPHeaders",
            "Fetch.failRequest",
            "Browser.setDownloadBehavior",
            "blocked_external_requests",
            "owner-preview.html",
            "owner-worksheet.csv",
            "coverage.json",
            "projection.json",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
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
            profile = root / "profile"
            browser, browser_handle = tool._start_browser(
                chromium, profile, cdp_port, root / "chromium.stderr"
            )
            server = None
            thread = None
            try:
                cdp = tool._wait_cdp(browser, cdp_port)
                for phase, phase_downloads in (
                    ("initial", initial_downloads),
                    ("restart", restart_downloads),
                ):
                    server, thread = _start_synthetic_viewer(
                        app_port, workspace, secret, tool
                    )
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
                    self.assertGreaterEqual(checked["assertions"], 20)
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
                    server = None
                    thread = None
                tool._assert_no_auth_material(evidence, secret)
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                if thread is not None:
                    thread.join(timeout=5)
                tool._stop_browser(browser, browser_handle)


if __name__ == "__main__":
    unittest.main()
