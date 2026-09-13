"""Fail-closed lifecycle and recovery tests for the local owner candidate."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import local_purchasing_candidate as candidate


DATABASE_URL = (
    "postgresql://qa_release_login@127.0.0.1:55433/buffalo_test_demo"
    "?options=-c+role%3Dqa_mapping_owner"
)
RESTORE_URL = (
    "postgresql://qa_release_login@127.0.0.1:55433/buffalo_test_restore_demo"
    "?options=-c+role%3Dqa_mapping_owner"
)
VALID_RESTORE_TOC = (
    "7; 2615 1 SCHEMA - qa_mapping_test qa_mapping_owner\n"
    "2; 3079 2 EXTENSION - pgcrypto\n"
    "3; 1259 3 TABLE qa_mapping_test meta qa_mapping_owner\n"
)


def _state() -> dict:
    facts = {
        "review_batches": 2,
        "review_candidates": 2,
        "mapping_decisions": 2,
        "selection_events": 1,
        "selection_heads": 1,
        "runs": 1,
        "purchase_orders": 1,
        "artifacts": 2,
        "decision_payloads": "a" * 64,
        "selection_payloads": "b" * 64,
        "run_fingerprints": "c" * 64,
        "artifact_hashes": "d" * 64,
        "relation_inventory": [
            {"relation": "meta", "row_count": 3, "sha256": "e" * 64}
        ],
        "sequence_inventory": [
            {"sequence": "offers_id_seq", "last_value": 1, "is_called": True}
        ],
    }
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
    return {"facts": facts, "sha256": hashlib.sha256(encoded).hexdigest()}


class _Result:
    def __init__(self, *, one=None, all_rows=None) -> None:
        self.one = one
        self.all_rows = [] if all_rows is None else all_rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class _FactsConnection:
    def __init__(self, *, initialized: bool) -> None:
        self.initialized = initialized
        self.calls = 0

    def execute(self, _statement, _parameters=()):
        self.calls += 1
        if self.calls == 1:
            return _Result(
                one=(
                    "buffalo_test_demo" if self.initialized else "buffalo_test_restore_demo",
                    160009,
                    "127.0.0.1",
                    "qa_release_login",
                    "qa_mapping_owner",
                    "qa_mapping_owner",
                )
            )
        if self.calls == 2:
            return _Result(one=(1234 if self.initialized else None,))
        if self.calls == 3:
            return _Result(all_rows=[(candidate.SCHEMA,)] if self.initialized else [])
        if self.calls == 4:
            return _Result(
                all_rows=[("pgcrypto",), ("plpgsql",)]
                if self.initialized
                else [("plpgsql",)]
            )
        if self.calls == 5:
            return _Result(one=(0, 0, 0))
        if self.calls == 6:
            return _Result(
                all_rows=[
                    ("synthetic_owner_demo_contract", candidate.DEMO_CONTRACT),
                    ("persistent_mapping_foundation_contract", candidate.MAPPING_CONTRACT),
                    ("persistent_mapping_foundation_catalog_sha256", candidate.MAPPING_CATALOG_SHA256),
                    ("migration:014_persistent_mapping_foundation.sql", candidate.MAPPING_MIGRATION_MARKER),
                ]
            )
        if self.calls == 7:
            return _Result(one=(candidate.MAPPING_CATALOG_SHA256,))
        return _Result(one=(None,))


class _Process:
    def __init__(self, *, pid: int = 424242, return_code: int = 0) -> None:
        self.pid = pid
        self.return_code = return_code
        self.running = False
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.running else self.return_code

    def wait(self, timeout=None):
        del timeout
        self.running = False
        return self.return_code

    def terminate(self):
        self.terminated = True
        self.running = False

    def kill(self):
        self.killed = True
        self.running = False

    def send_signal(self, _signal):
        self.running = False


class _HealthResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return json.dumps(self.payload).encode()


class LocalPurchasingCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory(prefix="buffalo-candidate-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)

    def _runtime(self, name: str = "runtime") -> Path:
        root = self.root / name
        root.mkdir(mode=0o700)
        (root / "storage").mkdir(mode=0o700)
        (root / "backups").mkdir(mode=0o700)
        for index, filename in enumerate(candidate.SECRET_FILES.values(), start=1):
            path = root / filename
            path.write_text(f"fabricated-secret-{index}-0123456789\n", encoding="utf-8")
            path.chmod(0o600)
        return root

    def _backup_fixture(self, name: str = "backup", *, member: str = "nested/artifact.txt") -> Path:
        root = self.root / name
        root.mkdir(mode=0o700)
        dump = root / "database.dump"
        dump.write_bytes(b"synthetic custom dump bytes")
        dump.chmod(0o600)
        archive = root / "storage.tar"
        payload = b"sealed synthetic artifact"
        with tarfile.open(archive, "w") as handle:
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            info.mode = 0o600
            import io

            handle.addfile(info, io.BytesIO(payload))
        archive.chmod(0o600)
        evidence = _state()
        manifest = {
            "contract": "BUFFALO_LOCAL_CANDIDATE_BACKUP_V1",
            "created_utc": "20260913T070000Z",
            "database": {
                "database": "buffalo_test_demo",
                "postgres_major": 16,
                "server_address": "127.0.0.1",
                "session_user": "qa_release_login",
                "current_user": "qa_mapping_owner",
                "database_owner": "qa_mapping_owner",
            },
            "database_dump": {
                "path": "database.dump",
                "sha256": candidate._sha256_file(dump),
                "bytes": dump.stat().st_size,
            },
            "storage_archive": {
                "path": "storage.tar",
                "sha256": candidate._sha256_file(archive),
                "bytes": archive.stat().st_size,
                "files": [
                    {
                        "path": member,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "bytes": len(payload),
                    }
                ],
            },
            "state": evidence,
            "limitations": [
                "same-host local recovery only",
                "sessions and secret files are intentionally excluded",
            ],
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)
        return path

    def test_database_urls_require_exact_loopback_role_suffix_and_query(self):
        self.assertEqual(candidate._database_name(DATABASE_URL), "buffalo_test_demo")
        self.assertEqual(
            candidate._database_name(RESTORE_URL, restore=True),
            "buffalo_test_restore_demo",
        )
        app_url = candidate._application_database_url(DATABASE_URL)
        self.assertIn("search_path%3Dqa_mapping_test%2Cpg_catalog", app_url)
        bad_urls = (
            DATABASE_URL.replace("127.0.0.1", "localhost"),
            DATABASE_URL.replace("127.0.0.1", "192.0.2.1"),
            DATABASE_URL.replace("qa_release_login@", "qa_release_login:secret@"),
            DATABASE_URL.replace("qa_release_login", "postgres", 1),
            DATABASE_URL.replace(":55433", ""),
            DATABASE_URL + "&extra=",
            DATABASE_URL + "&options=-c+role%3Dqa_mapping_owner",
            DATABASE_URL.replace("buffalo_test_demo", "buffalo/test_demo"),
            DATABASE_URL.replace("buffalo_test_demo", "buffalo%2Ftest_demo"),
            DATABASE_URL + "#fragment",
        )
        for value in bad_urls:
            with self.subTest(value=value), self.assertRaises(candidate.CandidateBoundaryError):
                candidate._database_name(value)

    def test_database_lifecycle_lock_is_shared_exact_and_spans_every_operation(self):
        class LifecycleConnection:
            def __init__(self, locked: bool = True) -> None:
                self.locked = locked
                self.exited = False
                self.lock_name = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.exited = True
                return False

            def execute(self, statement, parameters=()):
                rendered = str(statement)
                if "current_database" in rendered:
                    return _Result(
                        one=(
                            "buffalo_test_demo",
                            160009,
                            "127.0.0.1/32",
                            "qa_release_login",
                            "qa_mapping_owner",
                            "qa_mapping_owner",
                        )
                    )
                if "pg_try_advisory_lock" in rendered:
                    self.lock_name = parameters[0]
                    return _Result(one=(self.locked,))
                raise AssertionError(rendered)

        connection = LifecycleConnection()
        with mock.patch.object(candidate.psycopg, "connect", return_value=connection):
            with candidate._database_lifecycle_guard(DATABASE_URL):
                self.assertFalse(connection.exited)
        self.assertTrue(connection.exited)
        self.assertEqual(
            connection.lock_name,
            "buffalo:local-purchasing-candidate:lifecycle:v1:buffalo_test_demo",
        )
        refused = LifecycleConnection(locked=False)
        with (
            mock.patch.object(candidate.psycopg, "connect", return_value=refused),
            self.assertRaises(candidate.CandidateBoundaryError),
        ):
            with candidate._database_lifecycle_guard(DATABASE_URL):
                self.fail("a contended lifecycle lock must not enter the operation")
        self.assertTrue(refused.exited)

        events = []

        @contextmanager
        def guarded(_url, *, restore=False):
            events.append(("enter", restore))
            try:
                yield
            finally:
                events.append(("exit", restore))

        def body(label, value):
            self.assertEqual(events[-1][0], "enter")
            events.append((label, events[-1][1]))
            return value

        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", side_effect=guarded),
            mock.patch.object(candidate, "_serve_locked", side_effect=lambda *_: body("serve", 0)),
            mock.patch.object(candidate, "_backup_locked", side_effect=lambda *_: body("backup", Path("manifest"))),
            mock.patch.object(candidate, "_restore_locked", side_effect=lambda *_: body("restore", {"restored": True})),
        ):
            self.assertEqual(candidate.serve(DATABASE_URL, self.root, 18765), 0)
            self.assertEqual(candidate.backup(DATABASE_URL, self.root), Path("manifest"))
            self.assertTrue(candidate.restore(RESTORE_URL, self.root, self.root)["restored"])
        self.assertEqual(
            events,
            [
                ("enter", False), ("serve", False), ("exit", False),
                ("enter", False), ("backup", False), ("exit", False),
                ("enter", True), ("restore", True), ("exit", True),
            ],
        )
        initializer_source = (
            candidate.REPO_ROOT
            / "procurement"
            / "tools"
            / "initialize_synthetic_demo.py"
        ).read_text(encoding="utf-8")
        self.assertIn("acquire_database_lifecycle_lock(conn, str(row[0]))", initializer_source)
        self.assertNotIn("buffalo:synthetic-demo-initialize", initializer_source)

    def test_database_preflight_requires_disabled_policy_source_hash_identity_and_catalog(self):
        initialized = _FactsConnection(initialized=True)
        with mock.patch.object(candidate.psycopg, "connect", return_value=nullcontext(initialized)):
            facts = candidate._database_facts(DATABASE_URL, require_initialized=True)
        self.assertEqual(facts["database"], "buffalo_test_demo")
        self.assertEqual(facts["postgres_major"], 16)
        self.assertEqual(initialized.calls, 8)
        empty = _FactsConnection(initialized=False)
        with mock.patch.object(candidate.psycopg, "connect", return_value=nullcontext(empty)):
            candidate._database_facts(RESTORE_URL, require_initialized=False, restore=True)
        with (
            mock.patch.object(candidate.tomllib, "loads", return_value={}),
            mock.patch.object(candidate.psycopg, "connect") as connect,
            self.assertRaises(candidate.CandidateBoundaryError),
        ):
            candidate._database_facts(DATABASE_URL, require_initialized=True)
        connect.assert_not_called()
        initializer_source = (
            candidate.REPO_ROOT
            / "procurement"
            / "tools"
            / "initialize_synthetic_demo.py"
        ).read_text(encoding="utf-8")
        self.assertNotRegex(
            initializer_source,
            r"UPDATE\s+readiness_gates\s+SET\s+status\s*=\s*['\"]PASS",
        )
        self.assertNotIn("INSERT INTO sales_daily", initializer_source)
        self.assertIn("finalize_sales_backfill", initializer_source)

    def test_runtime_tree_and_secret_files_require_owned_exact_modes_and_distinct_values(self):
        runtime = self._runtime()
        storage, backups, pid = candidate._runtime_paths(runtime)
        self.assertEqual((storage, backups, pid), (runtime / "storage", runtime / "backups", runtime / candidate.PID_FILE))
        self.assertEqual(set(candidate._secret_values(runtime)), set(candidate.SECRET_FILES))
        weak = runtime / "storage"
        weak.chmod(0o755)
        with self.assertRaises(candidate.CandidateBoundaryError):
            candidate._runtime_paths(runtime)
        weak.chmod(0o700)
        first, second = list(candidate.SECRET_FILES.values())[:2]
        (runtime / second).write_bytes((runtime / first).read_bytes())
        (runtime / second).chmod(0o600)
        with self.assertRaises(candidate.CandidateBoundaryError):
            candidate._secret_values(runtime)

    def test_child_environment_is_allowlisted_and_health_is_bound_to_exact_process(self):
        runtime = self._runtime()
        with mock.patch.dict(
            os.environ,
            {"SHOPIFY_ACCESS_TOKEN": "must-not-propagate", "DATABASE_URL": "ambient"},
            clear=False,
        ):
            environment = candidate._child_environment(
                database_url=DATABASE_URL,
                runtime_root=runtime,
                storage=runtime / "storage",
                port=18765,
            )
        self.assertNotIn("SHOPIFY_ACCESS_TOKEN", environment)
        self.assertEqual(environment["BUFFALO_RUNTIME_MODE"], "SYNTHETIC_DEMO")
        self.assertIn("search_path%3Dqa_mapping_test%2Cpg_catalog", environment["DATABASE_URL"])
        process = _Process(pid=4567)
        process.running = True
        with (
            mock.patch.object(
                candidate,
                "urlopen",
                side_effect=[
                    _HealthResponse({"service": "buffalo-procurement-os", "process_id": 1}),
                    _HealthResponse({"service": "buffalo-procurement-os", "process_id": 4567}),
                ],
            ) as opened,
            mock.patch.object(candidate.time, "sleep"),
        ):
            candidate._wait_for_health(process, 18765)
        self.assertEqual(opened.call_count, 2)

    def test_pid_reservation_rejects_live_malformed_reserved_and_changed_files(self):
        pid_file = self.root / "candidate.pid"
        descriptor = candidate._reserve_pid_file(pid_file)
        identity = candidate._reservation_identity(descriptor)
        candidate._write_reserved_pid(descriptor, 4321)
        self.assertEqual(stat.S_IMODE(pid_file.stat().st_mode), 0o600)
        candidate._release_reserved_pid_path(pid_file, identity)
        for raw in ("", "not-a-pid", "0", "-1"):
            pid_file.write_text(raw, encoding="ascii")
            pid_file.chmod(0o600)
            with self.subTest(raw=raw), self.assertRaises(candidate.CandidateBoundaryError):
                candidate._reserve_pid_file(pid_file)
            pid_file.unlink()
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        with self.assertRaises(candidate.CandidateBoundaryError):
            candidate._reserve_pid_file(pid_file)
        pid_file.unlink()
        pid_file.write_text("99999999", encoding="ascii")
        with mock.patch.object(candidate.os, "kill", side_effect=ProcessLookupError):
            descriptor = candidate._reserve_pid_file(pid_file)
        os.close(descriptor)
        pid_file.unlink()

    def test_serve_uses_loopback_single_worker_and_cleans_up_only_its_pid(self):
        runtime = self._runtime()
        process = _Process()
        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(candidate, "_database_facts"),
            mock.patch.object(candidate, "_child_environment", return_value={"SAFE": "1"}),
            mock.patch.object(candidate, "_wait_for_health"),
            mock.patch.object(candidate.signal, "signal", return_value=object()),
            mock.patch.object(candidate.subprocess, "Popen", return_value=process) as popen,
        ):
            self.assertEqual(candidate.serve(DATABASE_URL, runtime, 18765), 0)
        self.assertFalse((runtime / candidate.PID_FILE).exists())
        command = popen.call_args.args[0]
        self.assertIn("127.0.0.1", command)
        self.assertIn("--workers", command)
        self.assertIn("--no-proxy-headers", command)
        leaking = _Process()
        leaking.running = True
        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(candidate, "_database_facts"),
            mock.patch.object(candidate, "_child_environment", return_value={}),
            mock.patch.object(candidate.subprocess, "Popen", return_value=leaking),
            mock.patch.object(candidate, "_write_reserved_pid", side_effect=OSError("write refused")),
            self.assertRaises(OSError),
        ):
            candidate.serve(DATABASE_URL, runtime, 18765)
        self.assertTrue(leaking.terminated)
        self.assertFalse((runtime / candidate.PID_FILE).exists())
        stubborn = mock.Mock()
        stubborn.pid = 515151
        stubborn.poll.return_value = None
        stubborn.wait.side_effect = [
            subprocess.TimeoutExpired(["uvicorn"], 1),
            -9,
        ]
        with mock.patch.object(
            candidate.os,
            "killpg",
            side_effect=[None, None, ProcessLookupError],
        ) as kill_group:
            candidate._terminate_owned_process_group(stubborn, timeout=1)
        self.assertEqual(
            kill_group.call_args_list,
            [
                mock.call(stubborn.pid, candidate.signal.SIGTERM),
                mock.call(stubborn.pid, candidate.signal.SIGKILL),
                mock.call(stubborn.pid, 0),
            ],
        )

    def test_backup_holds_lifecycle_reservation_and_refuses_any_active_marker(self):
        runtime = self._runtime()
        pid_file = runtime / candidate.PID_FILE
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        pid_file.chmod(0o600)
        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(candidate, "_database_facts", return_value={}),
            self.assertRaises(candidate.CandidateBoundaryError),
        ):
            candidate.backup(DATABASE_URL, runtime)
        pid_file.unlink()
        observed = []

        def state(_url):
            with self.assertRaises(candidate.CandidateBoundaryError):
                candidate._reserve_pid_file(pid_file)
            observed.append(True)
            return _state()

        def dump(command, **_kwargs):
            Path(command[command.index("--file") + 1]).write_bytes(b"dump")
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(candidate, "_database_facts", return_value={"database": "buffalo_test_demo"}),
            mock.patch.object(candidate, "_postgres_tool", return_value="/fake/pg_dump"),
            mock.patch.object(candidate, "_state_evidence", side_effect=state),
            mock.patch.object(candidate.subprocess, "run", side_effect=dump),
        ):
            candidate.backup(DATABASE_URL, runtime)
        self.assertEqual(len(observed), 2)
        self.assertFalse(pid_file.exists())

    def test_backup_writes_secure_canonical_manifest_dump_and_storage_archive(self):
        runtime = self._runtime()
        artifact = runtime / "storage" / "nested" / "artifact.csv"
        artifact.parent.mkdir(mode=0o700)
        artifact.write_bytes(b"synthetic,artifact\n")
        artifact.chmod(0o600)

        def dump(command, **_kwargs):
            Path(command[command.index("--file") + 1]).write_bytes(b"custom dump")
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(
                candidate,
                "_database_facts",
                return_value={
                    "database": "buffalo_test_demo",
                    "postgres_major": 16,
                    "server_address": "127.0.0.1",
                    "session_user": "qa_release_login",
                    "current_user": "qa_mapping_owner",
                    "database_owner": "qa_mapping_owner",
                },
            ),
            mock.patch.object(candidate, "_postgres_tool", return_value="/fake/pg_dump"),
            mock.patch.object(candidate, "_state_evidence", return_value=_state()),
            mock.patch.object(candidate.subprocess, "run", side_effect=dump) as run,
        ):
            manifest_path = candidate.backup(DATABASE_URL, runtime)
        self.assertEqual(stat.S_IMODE(manifest_path.parent.stat().st_mode), 0o700)
        for path in manifest_path.parent.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        manifest, _, _, files = candidate._restore_preflight(manifest_path)
        self.assertEqual(files[0]["path"], "nested/artifact.csv")
        self.assertEqual(manifest["limitations"][0], "same-host local recovery only")
        self.assertIn("--schema", run.call_args.args[0])
        self.assertIn("--extension", run.call_args.args[0])
        serialized = manifest_path.read_text(encoding="utf-8")
        for filename in candidate.SECRET_FILES.values():
            self.assertNotIn(filename, serialized)

    def test_backup_rejects_symlinks_database_drift_and_storage_archive_drift(self):
        def dump(command, **_kwargs):
            Path(command[command.index("--file") + 1]).write_bytes(b"dump")
            return subprocess.CompletedProcess(command, 0)

        for mode in ("symlink", "database", "archive", "storage"):
            with self.subTest(mode=mode):
                runtime = self._runtime(f"runtime-{mode}")
                artifact = runtime / "storage" / "artifact.txt"
                artifact.write_bytes(b"before")
                artifact.chmod(0o600)
                if mode == "symlink":
                    artifact.unlink()
                    artifact.symlink_to(runtime / next(iter(candidate.SECRET_FILES.values())))
                states = [_state(), {**_state(), "sha256": "f" * 64}] if mode == "database" else [_state(), _state()]
                original_hash = candidate._sha256_file

                def hash_file(path):
                    if mode == "archive" and Path(path).name == "artifact.txt":
                        return "0" * 64
                    return original_hash(Path(path))

                original_validate = candidate._validate_storage_archive

                def validate_archive(archive_path, expected):
                    result = original_validate(archive_path, expected)
                    if mode == "storage":
                        late = runtime / "storage" / "late-artifact.txt"
                        late.write_bytes(b"arrived during backup")
                        late.chmod(0o600)
                    return result

                with (
                    mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
                    mock.patch.object(candidate, "_database_facts", return_value={"database": "buffalo_test_demo"}),
                    mock.patch.object(candidate, "_postgres_tool", return_value="/fake/pg_dump"),
                    mock.patch.object(candidate, "_state_evidence", side_effect=states),
                    mock.patch.object(candidate.subprocess, "run", side_effect=dump),
                    mock.patch.object(candidate, "_sha256_file", side_effect=hash_file),
                    mock.patch.object(
                        candidate,
                        "_validate_storage_archive",
                        side_effect=validate_archive,
                    ),
                    self.assertRaises(candidate.CandidateBoundaryError),
                ):
                    candidate.backup(DATABASE_URL, runtime)
                self.assertEqual(list((runtime / "backups").iterdir()), [])
                self.assertFalse((runtime / candidate.PID_FILE).exists())

    def test_restore_preflight_rejects_contract_path_mode_hash_archive_and_state_tampering(self):
        valid = self._backup_fixture()
        candidate._restore_preflight(valid)
        candidate._validate_restore_toc(VALID_RESTORE_TOC)
        for invalid_toc in (
            "2; 3079 2 EXTENSION - pgcrypto\n",
            "7; 2615 1 SCHEMA - qa_mapping_test qa_mapping_owner\n",
            VALID_RESTORE_TOC
            + "4; 1259 4 TABLE unrelated_schema foreign_data qa_mapping_owner\n",
        ):
            with self.subTest(invalid_toc=invalid_toc), self.assertRaises(
                candidate.CandidateBoundaryError
            ):
                candidate._validate_restore_toc(invalid_toc)
        original = json.loads(valid.read_text(encoding="utf-8"))
        mutations = (
            lambda value: value.update(contract="WRONG"),
            lambda value: value["database"].update(extra=True),
            lambda value: value["database_dump"].update(path="../database.dump"),
            lambda value: value["state"].update(sha256="0" * 64),
            lambda value: value["limitations"].append("unreviewed limitation"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                value = json.loads(json.dumps(original))
                mutate(value)
                valid.write_text(json.dumps(value), encoding="utf-8")
                valid.chmod(0o600)
                with self.assertRaises(candidate.CandidateBoundaryError):
                    candidate._restore_preflight(valid)
        valid.write_text(json.dumps(original), encoding="utf-8")
        valid.chmod(0o644)
        with self.assertRaises(candidate.CandidateBoundaryError):
            candidate._restore_preflight(valid)
        traversal = self._backup_fixture("traversal", member="../secret")
        with self.assertRaises(candidate.CandidateBoundaryError):
            candidate._restore_preflight(traversal)

    def test_restore_success_stages_then_restores_single_transaction_verifies_and_publishes(self):
        manifest = self._backup_fixture()
        restore_root = self.root / "restored"
        database_facts = [
            {"database": "buffalo_test_restore_demo"},
            {"database": "buffalo_test_restore_demo"},
        ]
        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(candidate, "_database_facts", side_effect=database_facts),
            mock.patch.object(candidate, "_postgres_tool", return_value="/fake/pg_restore"),
            mock.patch.object(candidate, "_state_evidence", return_value=_state()),
            mock.patch.object(candidate, "_normalize_restored_acl_representation") as normalize,
            mock.patch.object(
                candidate.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=VALID_RESTORE_TOC),
            ) as run,
        ):
            result = candidate.restore(RESTORE_URL, restore_root, manifest)
        self.assertTrue(result["restored"])
        self.assertTrue((restore_root / "nested" / "artifact.txt").is_file())
        self.assertEqual(stat.S_IMODE((restore_root / "nested" / "artifact.txt").stat().st_mode), 0o600)
        restore_command = run.call_args_list[1].args[0]
        self.assertIn("--single-transaction", restore_command)
        self.assertIn("--exit-on-error", restore_command)
        self.assertNotIn("--schema", restore_command)
        self.assertNotIn("--clean", restore_command)
        normalize.assert_called_once_with(RESTORE_URL)

    def test_restore_failure_removes_only_owned_staging_and_cleans_exact_database_scope(self):
        manifest = self._backup_fixture()
        restore_root = self.root / "restored"
        restore_root.mkdir(mode=0o700)
        sentinel = restore_root / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(candidate, "_database_facts", return_value={"database": "buffalo_test_restore_demo"}),
            mock.patch.object(candidate, "_postgres_tool", return_value="/fake/pg_restore"),
            mock.patch.object(
                candidate.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=VALID_RESTORE_TOC),
            ),
            mock.patch.object(candidate, "_clean_failed_restore") as clean,
            self.assertRaises(candidate.CandidateBoundaryError),
        ):
            candidate.restore(RESTORE_URL, restore_root, manifest)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        clean.assert_not_called()
        for path in sorted(restore_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
        restore_root.rmdir()
        failure = subprocess.CalledProcessError(1, ["pg_restore"])
        with (
            mock.patch.object(candidate, "_database_lifecycle_guard", return_value=nullcontext()),
            mock.patch.object(candidate, "_database_facts", return_value={"database": "buffalo_test_restore_demo"}),
            mock.patch.object(candidate, "_postgres_tool", return_value="/fake/pg_restore"),
            mock.patch.object(
                candidate.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=VALID_RESTORE_TOC),
                    failure,
                ],
            ),
            mock.patch.object(candidate, "_clean_failed_restore") as clean,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            candidate.restore(RESTORE_URL, restore_root, manifest)
        clean.assert_called_once_with(RESTORE_URL)
        self.assertFalse(restore_root.exists())


if __name__ == "__main__":
    unittest.main()
