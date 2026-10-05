"""Focused contract tests for the staging-specific Chromium phase runner."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import audit_staging_purchasing_browser as browser
from procurement_os.synthetic_price_replacement_contract import (
    REGISTERED_OPERATOR_BOOK_BYTES,
    REGISTERED_OPERATOR_BOOK_REF,
    REGISTERED_OPERATOR_BOOK_SHA256,
    STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
)
from procurement_os.synthetic_staging_database import (
    EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
)


PASSPHRASE = "A" * 43
BATCH_ID = "00000000-0000-4000-8000-000000000123"
SOURCE_COMMIT = "b" * 40
SOURCE_TREE = "c" * 40
TLS_CERTIFICATE_SHA256 = "d" * 64
BROWSER_PID = 12345
BROWSER_START_TIME = "67890"
SCREENSHOT = b"\x89PNG\r\n\x1a\nsynthetic-browser-screenshot"


def _operator_proof() -> dict[str, object]:
    return {
        "ambiguous_commit_recovered": False,
        "batch_id": BATCH_ID,
        "contract": STOPPED_SERVICE_PRICE_STAGE_CONTRACT,
        "declaration_sha256": "1" * 64,
        "idempotent_replay": False,
        "proposed_scope_membership_sha256": "2" * 64,
        "raw_sha256": REGISTERED_OPERATOR_BOOK_SHA256,
        "source_bytes": REGISTERED_OPERATOR_BOOK_BYTES,
        "source_ref": REGISTERED_OPERATOR_BOOK_REF,
        "staging_rows_sha256": "3" * 64,
        "status": "VALIDATED",
        "target_attestation_sha256": EXPECTED_RUNTIME_ATTESTATION_IDENTITY,
        "unchanged_database_sha256": "4" * 64,
        "unchanged_storage_sha256": "5" * 64,
        "validation_fingerprint": "6" * 64,
        "validation_issues_sha256": "7" * 64,
    }


def _operator_proof_sha256(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def _driver_sha256() -> str:
    return hashlib.sha256(Path(browser.__file__).with_suffix(".mjs").read_bytes()).hexdigest()


def _trusted_node(node: Path) -> browser._TrustedNode:
    descriptor = os.open(node, os.O_RDONLY | os.O_CLOEXEC)
    info = os.fstat(descriptor)
    return browser._TrustedNode(
        node,
        browser._EXPECTED_NODE_VERSION,
        browser._EXPECTED_NODE_SHA256,
        descriptor,
        (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
    )


def _browser_proof(
    operator_proof: dict[str, object],
    *,
    driver_sha256: str,
) -> dict[str, object]:
    return {
        "assertion_count": browser._EXPECTED_ASSERTION_COUNT,
        "assertion_manifest_sha256": browser._EXPECTED_ASSERTION_MANIFEST_SHA256,
        "batch_id": BATCH_ID,
        "browser_js_version": "13.2.152.1",
        "browser_pid": BROWSER_PID,
        "browser_product": "Chrome/152.0.0.0",
        "browser_protocol_version": "1.3",
        "browser_start_time": BROWSER_START_TIME,
        "confirmation_preview_sha256": "8" * 64,
        "contract": browser.BROWSER_PHASE_CONTRACT,
        "driver_sha256": driver_sha256,
        "node_sha256": browser._EXPECTED_NODE_SHA256,
        "node_version": browser._EXPECTED_NODE_VERSION,
        "operational_status_after": "VERIFIED_FUTURE",
        "operational_status_before": "VALIDATED",
        "operator_proof_sha256": _operator_proof_sha256(operator_proof),
        "phase": browser.PRICE_CONFIRM_PHASE,
        "raw_bytes": REGISTERED_OPERATOR_BOOK_BYTES,
        "raw_sha256": REGISTERED_OPERATOR_BOOK_SHA256,
        "screenshot_bytes": len(SCREENSHOT),
        "screenshot_sha256": hashlib.sha256(SCREENSHOT).hexdigest(),
        "source_commit": SOURCE_COMMIT,
        "source_tree": SOURCE_TREE,
        "status_after": "VERIFIED_FUTURE",
        "status_before": "VALIDATED",
        "target_summary": {
            "active_guarded": 5,
            "guarded": 5,
            "inert": 0,
            "live_detached": 0,
            "tracked": 5,
            "unattached": 0,
            "unguarded": 0,
            "unresumed": 0,
            "unsupported": 0,
        },
        "temporal_basis": "REGISTERED_OBSERVATION",
        "tls_certificate_sha256": TLS_CERTIFICATE_SHA256,
    }


class AuditStagingPurchasingBrowserTests(unittest.TestCase):
    def test_node_driver_parses_and_does_not_persist_authenticated_html(self):
        self.assertEqual(
            (
                browser.REGISTERED_OPERATOR_BOOK_BYTES,
                browser.REGISTERED_OPERATOR_BOOK_SHA256,
            ),
            (REGISTERED_OPERATOR_BOOK_BYTES, REGISTERED_OPERATOR_BOOK_SHA256),
        )
        node = shutil.which("node")
        self.assertIsNotNone(node)
        driver = Path(browser.__file__).with_suffix(".mjs")
        parsed = subprocess.run(
            (node, "--check", str(driver)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=10,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stdout.decode("utf-8", "replace"))
        source = driver.read_text(encoding="utf-8")
        self.assertNotIn("saveHtml", source)
        self.assertNotIn("cookie.value", source)
        self.assertIn("operator-staged input, browser-approved workflow", source)
        self.assertIn("REGISTERED_OBSERVATION", source)
        self.assertIn("Target.setAutoAttach", source)
        self.assertIn("Network.webTransportCreated", source)
        self.assertIn("Network.directTCPSocketCreated", source)
        self.assertIn("Network.directUDPSocketCreated", source)
        self.assertIn("Network.webSocketCreated", source)
        self.assertIn(browser._HOST_RESOLVER_RULES, source)
        self.assertIn("--proxy-server=direct://", source)
        self.assertIn("--dns-prefetch-disable", source)
        self.assertIn("RTCPeerConnection", source)
        self.assertIn("Emulation.setScriptExecutionDisabled", source)
        self.assertIn(
            "authenticated Chromium page is destroyed before proof without late activity",
            source,
        )
        self.assertIn(
            f"const EXPECTED_ASSERTION_COUNT = {browser._EXPECTED_ASSERTION_COUNT};",
            source,
        )
        self.assertIn(browser._EXPECTED_ASSERTION_MANIFEST_SHA256, source)

        csrf = "C" * 43
        expected_request = {
            "actor": "owner:railway-staging:01",
            "csrfSha256": hashlib.sha256(csrf.encode("ascii")).hexdigest(),
            "idempotencyKey": "staging-browser-price-confirmation-v1",
            "previewSha256": "8" * 64,
            "warningReason": "Reviewed exact operator-staged synthetic price changes.",
        }
        request_fields = {
            "_buffalo_staging_csrf": csrf,
            "actor": expected_request["actor"],
            "confirm": "CONFIRM",
            "confirmation_idempotency_key": expected_request["idempotencyKey"],
            "expected_preview_sha256": expected_request["previewSha256"],
            "warning_review_reason": expected_request["warningReason"],
        }

        def body_probe(fields: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                (
                    node,
                    str(driver),
                    "--self-test-confirmation-request",
                    BATCH_ID,
                    base64.urlsafe_b64encode(urlencode(fields).encode("utf-8"))
                    .rstrip(b"=")
                    .decode("ascii"),
                    base64.urlsafe_b64encode(
                        json.dumps(expected_request, sort_keys=True).encode("utf-8")
                    )
                    .rstrip(b"=")
                    .decode("ascii"),
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=10,
            )

        self.assertEqual(body_probe(request_fields).stdout, b"MATCH\n")
        mutated = {**request_fields, "expected_preview_sha256": "9" * 64}
        self.assertEqual(body_probe(mutated).stdout, b"DIFF\n")

        with TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            repo = parent / "repo"
            repo.mkdir()

            def git(*arguments: str) -> None:
                subprocess.run(
                    (str(browser._TRUSTED_GIT), "-C", str(repo), *arguments),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=10,
                )

            git("init", "-q")
            git("config", "user.name", "Staging Browser Test")
            git("config", "user.email", "browser@example.invalid")
            tracked = repo / "driver.mjs"
            tracked.write_text("export const exact = true;\n", encoding="utf-8")
            (repo / ".gitignore").write_text(
                "__pycache__/\n*.py[co]\n", encoding="utf-8"
            )
            git("add", ".gitignore", "driver.mjs")
            git("commit", "-q", "-m", "bound source")
            monitor_marker = parent / "fsmonitor-ran"
            monitor = parent / "fsmonitor"
            monitor.write_text(
                "#!/bin/sh\nprintf invoked > "
                + str(monitor_marker)
                + "\nexit 1\n",
                encoding="utf-8",
            )
            monitor.chmod(0o700)
            git("config", "core.fsmonitor", str(monitor))
            identity = browser._source_identity(repo)
            self.assertFalse(monitor_marker.exists())
            with patch.dict(os.environ, {"PATH": str(repo)}):
                self.assertEqual(browser._source_identity(repo), identity)
            with (
                patch.object(browser, "REPO_ROOT", repo),
                patch.object(browser, "_DRIVER", tracked),
            ):
                browser._verify_driver_snapshot(
                    commit=identity["commit"],
                    content=tracked.read_bytes(),
                )
                with self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "driver differs"
                ):
                    browser._verify_driver_snapshot(
                        commit=identity["commit"],
                        content=b"export const exact = false;\n",
                    )
            ignored = repo / "procurement" / "tools" / "__pycache__"
            ignored.mkdir(parents=True)
            ignored_file = ignored / "audit_staging_purchasing_browser.pyc"
            ignored_file.write_bytes(b"executable ignored bytes")
            with self.assertRaisesRegex(
                browser.StagingBrowserAuditError, "source identity differs"
            ):
                browser._source_identity(repo)
            ignored_file.unlink()
            ignored.rmdir()
            ignored.parent.rmdir()
            ignored.parent.parent.rmdir()
            git("update-index", "--assume-unchanged", "driver.mjs")
            tracked.write_text("export const exact = false;\n", encoding="utf-8")
            with self.assertRaisesRegex(
                browser.StagingBrowserAuditError, "non-normal flags"
            ):
                browser._source_identity(repo)

    def test_node_secret_reader_accepts_high_fifo_fd_and_rejects_bad_channels(self):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        node_version = subprocess.run(
            (node, "--version"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )
        observed_node_version = node_version.stdout.decode("ascii", "strict").strip()
        self.assertRegex(observed_node_version, r"\Av[0-9]+(?:\.[0-9]+)+\Z")
        observed_node_sha256 = "a" * 64
        driver = Path(browser.__file__).with_suffix(".mjs")

        def invoke(
            payload: bytes,
            *,
            regular: bool = False,
            write_end: bool = False,
        ) -> subprocess.CompletedProcess:
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                (root / "staging-price-confirmation-browser.log").write_bytes(b"")
                (root / "staging-price-confirmation-browser.log").chmod(0o600)
                held: list[int] = []
                descriptor = -1
                try:
                    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
                    selected = write_fd if write_end else read_fd
                    descriptor = fcntl.fcntl(selected, fcntl.F_DUPFD_CLOEXEC, 10)
                    os.close(selected)
                    if write_end:
                        held.append(read_fd)
                    else:
                        os.write(write_fd, payload)
                        os.close(write_fd)
                    if regular:
                        os.close(descriptor)
                        regular_path = root / "regular-secret"
                        regular_path.write_bytes(payload)
                        regular_path.chmod(0o600)
                        regular_fd = os.open(
                            regular_path, os.O_RDONLY | os.O_CLOEXEC
                        )
                        descriptor = fcntl.fcntl(
                            regular_fd, fcntl.F_DUPFD_CLOEXEC, 10
                        )
                        os.close(regular_fd)
                    self.assertGreaterEqual(descriptor, 10)
                    result = subprocess.run(
                        (
                            node,
                            str(driver),
                            browser.PRICE_CONFIRM_PHASE,
                            browser.LOCAL_TLS_ORIGIN,
                            "http://127.0.0.1:9",
                            str(root),
                            str(root / "staging-price-confirmation-proof.json"),
                            BATCH_ID,
                            SOURCE_COMMIT,
                            SOURCE_TREE,
                            _driver_sha256(),
                            observed_node_sha256,
                            observed_node_version,
                            _operator_proof_sha256(_operator_proof()),
                            TLS_CERTIFICATE_SHA256,
                            str(BROWSER_PID),
                            BROWSER_START_TIME,
                            str(descriptor),
                        ),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        pass_fds=(descriptor,),
                        check=False,
                        timeout=10,
                    )
                finally:
                    if descriptor >= 0:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                    for item in held:
                        try:
                            os.close(item)
                        except OSError:
                            pass
                self.assertNotIn(PASSPHRASE.encode("ascii"), result.stdout)
                return result

        valid = invoke(PASSPHRASE.encode("ascii"))
        self.assertNotIn(b"credential", valid.stdout.lower())
        for invalid in (
            b"",
            b"x" * 42,
            b"x" * 44,
            b"x\n",
            b"\xc1" * 43,
            b"\xff" * 43,
        ):
            with self.subTest(invalid=invalid[:4]):
                self.assertIn(b"credential differs", invoke(invalid).stdout)
        self.assertIn(b"credential channel differs", invoke(PASSPHRASE.encode(), regular=True).stdout)
        self.assertIn(b"credential channel differs", invoke(b"", write_end=True).stdout)

    def test_phase_proof_is_exact_and_bound_to_operator_evidence(self):
        operator = _operator_proof()
        driver_sha256 = _driver_sha256()
        proof = _browser_proof(operator, driver_sha256=driver_sha256)
        self.assertEqual(
            browser._validate_phase_proof(
                proof,
                batch_id=BATCH_ID,
                expected_source_commit=SOURCE_COMMIT,
                expected_source_tree=SOURCE_TREE,
                driver_sha256=driver_sha256,
                node_sha256=browser._EXPECTED_NODE_SHA256,
                node_version=browser._EXPECTED_NODE_VERSION,
                operator_proof_sha256=_operator_proof_sha256(operator),
                browser_pid=BROWSER_PID,
                browser_start_time=BROWSER_START_TIME,
                tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                screenshot_bytes=len(SCREENSHOT),
                screenshot_sha256=hashlib.sha256(SCREENSHOT).hexdigest(),
            ),
            proof,
        )
        mutations = (
            {**proof, "extra": True},
            {**proof, "batch_id": "00000000-0000-4000-8000-000000000999"},
            {**proof, "operator_proof_sha256": "9" * 64},
            {**proof, "raw_sha256": "9" * 64},
            {**proof, "status_after": "APPLIED_CURRENT"},
            {**proof, "assertion_count": True},
            {**proof, "assertion_count": browser._EXPECTED_ASSERTION_COUNT - 1},
            {**proof, "assertion_manifest_sha256": "9" * 64},
            {**proof, "node_sha256": "9" * 64},
            {**proof, "node_version": "v0.0.0"},
            {**proof, "screenshot_sha256": "9" * 64},
            {**proof, "source_tree": "9" * 40},
            {**proof, "target_summary": {**proof["target_summary"], "unguarded": 1}},
            {**proof, "target_summary": {**proof["target_summary"], "live_detached": 1}},
            {**proof, "target_summary": {**proof["target_summary"], "active_guarded": 999}},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "proof differs"
                ):
                    browser._validate_phase_proof(
                        mutation,
                        batch_id=BATCH_ID,
                        expected_source_commit=SOURCE_COMMIT,
                        expected_source_tree=SOURCE_TREE,
                        driver_sha256=driver_sha256,
                        node_sha256=browser._EXPECTED_NODE_SHA256,
                        node_version=browser._EXPECTED_NODE_VERSION,
                        operator_proof_sha256=_operator_proof_sha256(operator),
                        browser_pid=BROWSER_PID,
                        browser_start_time=BROWSER_START_TIME,
                        tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                        screenshot_bytes=len(SCREENSHOT),
                        screenshot_sha256=hashlib.sha256(SCREENSHOT).hexdigest(),
                    )

    def test_invalid_operator_or_url_refuses_before_process_start(self):
        with TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            home.chmod(0o700)
            profile = home / "profile"
            profile.mkdir(mode=0o700)
            environment = {
                **browser._EXPECTED_CHROMIUM_STATIC_ENVIRONMENT,
                "HOME": str(home),
                "PWD": str(browser.REPO_ROOT.resolve()),
                "TMPDIR": str(home),
            }

            def serialized(value: dict[str, str]) -> bytes:
                return b"\0".join(
                    f"{name}={item}".encode("utf-8")
                    for name, item in value.items()
                ) + b"\0"

            browser._validate_chromium_environment(
                serialized(environment), profile=profile
            )
            for changed in (
                {**environment, "REPLIT_DB_URL": "postgresql://secret"},
                {**environment, "LD_PRELOAD": "/tmp/untrusted.so"},
                {**environment, "TMPDIR": str(profile)},
            ):
                with self.subTest(environment=changed), self.assertRaisesRegex(
                    browser.StagingBrowserAuditError,
                    "process environment differs",
                ):
                    browser._validate_chromium_environment(
                        serialized(changed), profile=profile
                    )

        invalid_operator = _operator_proof()
        invalid_operator["status"] = "VERIFIED_FUTURE"
        with (
            patch.object(
                browser,
                "_source_identity",
                return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
            ),
            patch.object(browser.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(
                browser.StagingBrowserAuditError, "operator proof differs"
            ):
                browser.run_price_confirmation_phase(
                    node="/not-observed",
                    base_url="https://staging.example.test",
                    cdp_endpoint="http://127.0.0.1:9222",
                    evidence_root="/not-observed",
                    operator_proof=invalid_operator,
                    expected_source_commit=SOURCE_COMMIT,
                    expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                    chromium_pid=BROWSER_PID,
                    passphrase=PASSPHRASE,
                )
            popen.assert_not_called()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            node = root / "node"
            node.write_bytes(b"node")
            node.chmod(0o500)
            for hostile in (
                "https://staging.example.test:not-a-port",
                "https://evil.example",
                "https://.",
            ):
                with self.subTest(hostile=hostile), patch.object(
                    browser,
                    "_source_identity",
                    return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
                ), patch.object(
                    browser,
                    "_verify_driver_snapshot",
                ), self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "input differs"
                ):
                    browser.run_price_confirmation_phase(
                        node=node,
                        base_url=hostile,
                        cdp_endpoint="http://127.0.0.1:9222",
                        evidence_root=root,
                        operator_proof=_operator_proof(),
                        expected_source_commit=SOURCE_COMMIT,
                        expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                        chromium_pid=BROWSER_PID,
                        passphrase=PASSPHRASE,
                    )
            evidence = root / "evidence"
            evidence.mkdir(mode=0o700)
            with (
                patch.object(
                    browser,
                    "_source_identity",
                    return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
                ),
                patch.object(browser, "_verify_driver_snapshot"),
                patch.object(browser.subprocess, "Popen") as popen,
                self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "Node identity differs"
                ),
            ):
                browser.run_price_confirmation_phase(
                    node=node,
                    base_url=browser.LOCAL_TLS_ORIGIN,
                    cdp_endpoint="http://127.0.0.1:9222",
                    evidence_root=evidence,
                    operator_proof=_operator_proof(),
                    expected_source_commit=SOURCE_COMMIT,
                    expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                    chromium_pid=BROWSER_PID,
                    passphrase=PASSPHRASE,
                )
            popen.assert_not_called()

    def test_runner_delivers_secret_only_by_fd_and_validates_child_proof(self):
        operator = _operator_proof()
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "evidence"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            node = workspace / "node"
            node.write_bytes(b"node")
            node.chmod(0o500)
            observed: dict[str, object] = {}

            class Process:
                pid = 12345
                returncode = 0

                def __init__(self, arguments, **kwargs):
                    observed["arguments"] = arguments
                    observed["kwargs"] = kwargs
                    phase_index = arguments.index(browser.PRICE_CONFIRM_PHASE)
                    proof_path = Path(arguments[phase_index + 4])
                    proof_path.write_text(
                        json.dumps(
                            _browser_proof(
                                operator,
                                driver_sha256=arguments[phase_index + 8],
                            ),
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    proof_path.chmod(0o600)
                    screenshot = (
                        Path(arguments[phase_index + 3])
                        / "staging-price-confirmed-test-data.png"
                    )
                    screenshot.write_bytes(SCREENSHOT)
                    screenshot.chmod(0o600)

                def wait(self, timeout=None):
                    return self.returncode

                def poll(self):
                    return self.returncode

            with (
                patch.object(
                    browser,
                    "_source_identity",
                    return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
                ),
                patch.object(
                    browser,
                    "_validate_trusted_node",
                    return_value=_trusted_node(node),
                ),
                patch.object(browser, "_validate_node_process"),
                patch.object(browser, "_verify_driver_snapshot"),
                patch.object(browser, "_validate_local_tls_resolution"),
                patch.object(
                    browser,
                    "_validate_owned_chromium",
                    return_value=BROWSER_START_TIME,
                ),
                patch.object(browser.subprocess, "Popen", Process),
            ):
                proof = browser.run_price_confirmation_phase(
                    node=node,
                    base_url=browser.LOCAL_TLS_ORIGIN,
                    cdp_endpoint="http://127.0.0.1:9222",
                    evidence_root=root,
                    operator_proof=operator,
                    expected_source_commit=SOURCE_COMMIT,
                    expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                    chromium_pid=BROWSER_PID,
                    passphrase=PASSPHRASE,
                )
            self.assertEqual(
                proof,
                _browser_proof(operator, driver_sha256=_driver_sha256()),
            )
            serialized = repr(observed)
            self.assertNotIn(PASSPHRASE, serialized)
            arguments = observed["arguments"]
            kwargs = observed["kwargs"]
            phase_index = arguments.index(browser.PRICE_CONFIRM_PHASE)
            self.assertEqual(arguments[1:3], ("--input-type=module", "--eval"))
            self.assertIn(_driver_sha256(), arguments[3])
            self.assertNotIn(PASSPHRASE, arguments[3])
            self.assertEqual(arguments[4], "buffalo-staging-browser-driver.mjs")
            self.assertEqual(
                arguments[phase_index + 9], browser._EXPECTED_NODE_SHA256
            )
            self.assertEqual(
                arguments[phase_index + 10], browser._EXPECTED_NODE_VERSION
            )
            self.assertEqual(
                arguments[phase_index + 11], _operator_proof_sha256(operator)
            )
            self.assertEqual(arguments[phase_index + 12], TLS_CERTIFICATE_SHA256)
            self.assertEqual(
                arguments[phase_index + 13 : phase_index + 15],
                (str(BROWSER_PID), BROWSER_START_TIME),
            )
            node_descriptor = int(arguments[0].rsplit("/", 1)[1])
            self.assertEqual(arguments[0], f"/proc/self/fd/{node_descriptor}")
            self.assertEqual(
                kwargs["pass_fds"],
                (node_descriptor, int(arguments[-1])),
            )
            self.assertTrue(kwargs["close_fds"])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["umask"], 0o077)
            self.assertEqual(set(kwargs["env"]), {"LANG", "PATH"})
            self.assertEqual(
                stat.S_IMODE((root / "staging-price-confirmation-proof.json").stat().st_mode),
                0o600,
            )

    def test_timeout_kills_and_reaps_the_browser_phase_group(self):
        operator = _operator_proof()
        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "evidence"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            node = workspace / "node"
            node.write_bytes(b"node")
            node.chmod(0o500)

            class Process:
                pid = 23456
                stopped = False

                def __init__(self, *_args, **_kwargs):
                    pass

                def wait(self, timeout=None):
                    if not self.stopped:
                        raise subprocess.TimeoutExpired("node", timeout)
                    return -9

                def poll(self):
                    return None if not self.stopped else -9

            process = Process()

            def killed(pid, signum):
                self.assertEqual((pid, signum), (process.pid, 9))
                process.stopped = True

            with (
                patch.object(
                    browser,
                    "_source_identity",
                    return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
                ),
                patch.object(
                    browser,
                    "_validate_trusted_node",
                    return_value=_trusted_node(node),
                ),
                patch.object(browser, "_validate_node_process"),
                patch.object(browser, "_verify_driver_snapshot"),
                patch.object(browser, "_validate_local_tls_resolution"),
                patch.object(
                    browser,
                    "_validate_owned_chromium",
                    return_value=BROWSER_START_TIME,
                ),
                patch.object(browser.subprocess, "Popen", return_value=process),
                patch.object(browser.os, "killpg", side_effect=killed) as killpg,
            ):
                with self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "timed out"
                ):
                    browser.run_price_confirmation_phase(
                        node=node,
                        base_url=browser.LOCAL_TLS_ORIGIN,
                        cdp_endpoint="http://127.0.0.1:9222",
                        evidence_root=root,
                        operator_proof=operator,
                        expected_source_commit=SOURCE_COMMIT,
                        expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                        chromium_pid=BROWSER_PID,
                        passphrase=PASSPHRASE,
                        timeout_seconds=0.01,
                    )
            killpg.assert_called_once()

        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "evidence"
            root.mkdir(mode=0o700)
            node = workspace / "node"
            node.write_bytes(b"node")
            node.chmod(0o500)

            class UnkillableProcess:
                pid = 24456

                def poll(self):
                    return None

                def wait(self, timeout=None):
                    raise subprocess.TimeoutExpired("node", timeout)

            with (
                patch.object(
                    browser,
                    "_source_identity",
                    return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
                ),
                patch.object(
                    browser,
                    "_validate_trusted_node",
                    return_value=_trusted_node(node),
                ),
                patch.object(browser, "_validate_node_process"),
                patch.object(browser, "_verify_driver_snapshot"),
                patch.object(browser, "_validate_local_tls_resolution"),
                patch.object(
                    browser,
                    "_validate_owned_chromium",
                    return_value=BROWSER_START_TIME,
                ),
                patch.object(
                    browser.subprocess,
                    "Popen",
                    return_value=UnkillableProcess(),
                ),
                patch.object(browser.os, "killpg"),
                patch.object(browser, "_purge_if_credential_present") as purge,
                self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "child cleanup differs"
                ),
            ):
                browser.run_price_confirmation_phase(
                    node=node,
                    base_url=browser.LOCAL_TLS_ORIGIN,
                    cdp_endpoint="http://127.0.0.1:9222",
                    evidence_root=root,
                    operator_proof=operator,
                    expected_source_commit=SOURCE_COMMIT,
                    expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                    chromium_pid=BROWSER_PID,
                    passphrase=PASSPHRASE,
                    timeout_seconds=0.01,
                )
            purge.assert_not_called()

        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "evidence"
            root.mkdir(mode=0o700)
            node = workspace / "node"
            node.write_bytes(b"node")
            node.chmod(0o500)

            class SignalRaceProcess:
                pid = 33445
                stopped = False

                def poll(self):
                    return -9 if self.stopped else None

                def wait(self, timeout=None):
                    return -9 if self.stopped else 0

            signal_process = SignalRaceProcess()

            def spawn_with_pending_signal(*_args, **_kwargs):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                return signal_process

            def stop_signal_process(pid, signum):
                self.assertEqual((pid, signum), (signal_process.pid, signal.SIGKILL))
                signal_process.stopped = True

            old_sigterm = signal.getsignal(signal.SIGTERM)
            real_purge = browser._purge_if_credential_present
            purge_states: list[bool] = []

            def observe_purge(*args, **kwargs):
                purge_states.append(signal_process.stopped)
                return real_purge(*args, **kwargs)

            with (
                patch.object(
                    browser,
                    "_source_identity",
                    return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
                ),
                patch.object(
                    browser,
                    "_validate_trusted_node",
                    return_value=_trusted_node(node),
                ),
                patch.object(browser, "_validate_node_process"),
                patch.object(browser, "_verify_driver_snapshot"),
                patch.object(browser, "_validate_local_tls_resolution"),
                patch.object(
                    browser,
                    "_validate_owned_chromium",
                    return_value=BROWSER_START_TIME,
                ),
                patch.object(
                    browser.subprocess,
                    "Popen",
                    side_effect=spawn_with_pending_signal,
                ),
                patch.object(
                    browser.os,
                    "killpg",
                    side_effect=stop_signal_process,
                ) as signal_killpg,
                patch.object(
                    browser,
                    "_purge_if_credential_present",
                    side_effect=observe_purge,
                ),
                self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "was interrupted"
                ),
            ):
                browser.run_price_confirmation_phase(
                    node=node,
                    base_url=browser.LOCAL_TLS_ORIGIN,
                    cdp_endpoint="http://127.0.0.1:9222",
                    evidence_root=root,
                    operator_proof=operator,
                    expected_source_commit=SOURCE_COMMIT,
                    expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                    chromium_pid=BROWSER_PID,
                    passphrase=PASSPHRASE,
                )
            signal_killpg.assert_called_once()
            self.assertEqual(purge_states, [True])
            self.assertIs(signal.getsignal(signal.SIGTERM), old_sigterm)

        with TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root = workspace / "evidence"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            node = workspace / "node"
            node.write_bytes(b"node")
            node.chmod(0o500)

            class ContaminatingProcess:
                pid = 34567
                returncode = 0

                def __init__(self, arguments, **kwargs):
                    kwargs["stdout"].write(PASSPHRASE.encode("ascii"))
                    kwargs["stdout"].flush()
                    phase_index = arguments.index(browser.PRICE_CONFIRM_PHASE)
                    proof_path = Path(arguments[phase_index + 4])
                    proof_path.write_bytes(b"{malformed:" + PASSPHRASE.encode("ascii"))
                    proof_path.chmod(0o600)
                    screenshot = (
                        Path(arguments[phase_index + 3])
                        / "staging-price-confirmed-test-data.png"
                    )
                    screenshot.write_bytes(SCREENSHOT)
                    screenshot.chmod(0o600)

                def wait(self, timeout=None):
                    return self.returncode

                def poll(self):
                    return self.returncode

            with (
                patch.object(
                    browser,
                    "_source_identity",
                    return_value={"commit": SOURCE_COMMIT, "tree": SOURCE_TREE},
                ),
                patch.object(
                    browser,
                    "_validate_trusted_node",
                    return_value=_trusted_node(node),
                ),
                patch.object(browser, "_validate_node_process"),
                patch.object(browser, "_verify_driver_snapshot"),
                patch.object(browser, "_validate_local_tls_resolution"),
                patch.object(
                    browser,
                    "_validate_owned_chromium",
                    return_value=BROWSER_START_TIME,
                ),
                patch.object(browser.subprocess, "Popen", ContaminatingProcess),
                self.assertRaisesRegex(
                    browser.StagingBrowserAuditError, "contains credential"
                ),
            ):
                browser.run_price_confirmation_phase(
                    node=node,
                    base_url=browser.LOCAL_TLS_ORIGIN,
                    cdp_endpoint="http://127.0.0.1:9222",
                    evidence_root=root,
                    operator_proof=operator,
                    expected_source_commit=SOURCE_COMMIT,
                    expected_tls_certificate_sha256=TLS_CERTIFICATE_SHA256,
                    chromium_pid=BROWSER_PID,
                    passphrase=PASSPHRASE,
                )
            self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
