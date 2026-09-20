"""Adversarial path and PID-record tests for the private viewer launcher."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


TOOL = Path(__file__).resolve().parents[1] / "tools" / "serve_private_research.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("serve_private_research_hardening", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError("private research launcher could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServePrivateResearchHardeningTests(unittest.TestCase):
    def _runtime(self, parent: Path, tool) -> Path:
        root = parent / "runtime"
        root.mkdir(mode=0o700)
        secret = root / tool.SECRET_NAME
        secret.write_text("fixture-private-viewer-secret-value\n", encoding="utf-8")
        secret.chmod(0o600)
        return root

    def _pid_record(self, *, pid: object = 43210) -> dict[str, object]:
        return {
            "pid": pid,
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "process_start_ticks": 123,
            "cmdline_sha256": "c" * 64,
        }

    def _write_pid(self, root: Path, tool, record: dict[str, object]) -> None:
        path = root / tool.PID_NAME
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def test_runtime_and_workspace_paths_reject_symlinked_ancestors(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-symlink-parent-") as temp:
            parent = Path(temp)
            real_parent = parent / "real-parent"
            real_parent.mkdir(mode=0o700)
            linked_parent = parent / "linked-parent"
            linked_parent.symlink_to(real_parent, target_is_directory=True)

            linked_runtime = linked_parent / "new-runtime"
            with self.assertRaisesRegex(
                tool.PrivateResearchServeError, "symlink"
            ):
                tool.initialize_runtime(linked_runtime)
            self.assertFalse((real_parent / "new-runtime").exists())

            runtime = self._runtime(parent, tool)
            real_workspace_parent = parent / "real-workspaces"
            real_workspace_parent.mkdir(mode=0o700)
            workspace = real_workspace_parent / "workspace"
            workspace.mkdir(mode=0o700)
            linked_workspaces = parent / "linked-workspaces"
            linked_workspaces.symlink_to(real_workspace_parent, target_is_directory=True)
            read_workspace = mock.Mock()
            with (
                mock.patch.object(tool, "read_private_research_workspace", read_workspace),
                self.assertRaisesRegex(tool.PrivateResearchServeError, "symlink"),
            ):
                tool.serve(runtime, linked_workspaces / "workspace", 18876)
            read_workspace.assert_not_called()

            broken_pid = runtime / tool.PID_NAME
            broken_pid.symlink_to(parent / "absent-pid-target")
            with (
                mock.patch.object(tool, "read_private_research_workspace", read_workspace),
                self.assertRaisesRegex(
                    tool.PrivateResearchServeError, "already reserved"
                ),
            ):
                tool.serve(runtime, workspace, 18876)
            read_workspace.assert_not_called()

            runtime_parent = parent / "runtime-parent"
            runtime_parent.mkdir(mode=0o700)
            nested_runtime = self._runtime(runtime_parent, tool)
            linked_runtime_parent = parent / "linked-runtime-parent"
            linked_runtime_parent.symlink_to(runtime_parent, target_is_directory=True)
            with self.assertRaisesRegex(tool.PrivateResearchServeError, "symlink"):
                tool.status(linked_runtime_parent / nested_runtime.name)

    def test_status_rejects_malformed_pid_identity_before_process_lookup(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-pid-parent-") as temp:
            root = self._runtime(Path(temp), tool)
            invalid_records = (
                {**self._pid_record(), "pid": True},
                {**self._pid_record(), "pid": "43210"},
                {**self._pid_record(), "pid": 0},
                {**self._pid_record(), "pid": 1 << 63},
                {**self._pid_record(), "process_start_ticks": True},
                {**self._pid_record(), "process_start_ticks": 0},
                {**self._pid_record(), "source_commit": "A" * 40},
                {**self._pid_record(), "source_tree": "g" * 40},
                {**self._pid_record(), "cmdline_sha256": "C" * 64},
            )
            for record in invalid_records:
                with self.subTest(record=record):
                    self._write_pid(root, tool, record)
                    with (
                        mock.patch.object(tool.os, "kill") as kill,
                        mock.patch.object(tool, "_process_identity") as identity,
                    ):
                        state = tool.status(root)
                    self.assertEqual(
                        state,
                        {
                            "contract": tool.RUNTIME_CONTRACT,
                            "running": False,
                            "stale_pid_file": True,
                        },
                    )
                    kill.assert_not_called()
                    identity.assert_not_called()

    def test_status_rejects_duplicate_pid_fields_before_process_lookup(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-pid-parent-") as temp:
            root = self._runtime(Path(temp), tool)
            record = self._pid_record()
            pid_path = root / tool.PID_NAME
            pid_path.write_text(
                '{"pid":43210,"pid":43210,"source_commit":"'
                + str(record["source_commit"])
                + '","source_tree":"'
                + str(record["source_tree"])
                + '","process_start_ticks":123,"cmdline_sha256":"'
                + str(record["cmdline_sha256"])
                + '"}\n',
                encoding="utf-8",
            )
            pid_path.chmod(0o600)
            with (
                mock.patch.object(tool.os, "kill") as kill,
                mock.patch.object(tool, "_process_identity") as identity,
            ):
                state = tool.status(root)
            self.assertFalse(state["running"])
            self.assertTrue(state["stale_pid_file"])
            kill.assert_not_called()
            identity.assert_not_called()

    def test_status_accepts_only_a_complete_matching_live_process_identity(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-pid-parent-") as temp:
            root = self._runtime(Path(temp), tool)
            record = self._pid_record()
            self._write_pid(root, tool, record)
            with (
                mock.patch.object(tool.os, "kill") as kill,
                mock.patch.object(
                    tool,
                    "_process_identity",
                    return_value={
                        "process_start_ticks": record["process_start_ticks"],
                        "cmdline_sha256": record["cmdline_sha256"],
                    },
                ) as identity,
            ):
                state = tool.status(root)
            self.assertEqual(
                state,
                {
                    "contract": tool.RUNTIME_CONTRACT,
                    "running": True,
                    "pid": record["pid"],
                },
            )
            kill.assert_called_once_with(record["pid"], 0)
            identity.assert_called_once_with(record["pid"])

    def test_readiness_allows_validation_beyond_legacy_thirty_seconds(self):
        tool = _load_tool()
        self.assertEqual(tool.READINESS_TIMEOUT_SECONDS, 600)

        class FakeProcess:
            pid = 424242
            returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0
                return self.returncode

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true}'

        with TemporaryDirectory(prefix="buffalo-private-readiness-parent-") as temp:
            parent = Path(temp)
            runtime = self._runtime(parent, tool)
            workspace = parent / "workspace"
            workspace.mkdir(mode=0o700)
            ready = FakeResponse()
            with (
                mock.patch.object(tool, "read_private_research_workspace"),
                mock.patch.object(
                    tool, "_source_identity", return_value=("a" * 40, "b" * 40)
                ),
                mock.patch.object(tool, "_assert_port_free"),
                mock.patch.object(
                    tool,
                    "_process_identity",
                    return_value={
                        "process_start_ticks": 1,
                        "cmdline_sha256": "c" * 64,
                    },
                ),
                mock.patch.object(
                    tool.subprocess, "Popen", return_value=FakeProcess()
                ),
                mock.patch.object(
                    tool,
                    "urlopen",
                    side_effect=(tool.URLError("not ready"), ready),
                ) as open_url,
                mock.patch.object(tool.time, "monotonic", side_effect=(0.0, 1.0, 31.0)),
                mock.patch.object(tool.time, "sleep") as sleep,
                mock.patch("builtins.print"),
            ):
                tool.serve(runtime, workspace, 18876)
            self.assertEqual(open_url.call_count, 2)
            self.assertTrue(all(call.kwargs["timeout"] == 1 for call in open_url.call_args_list))
            sleep.assert_called_once_with(0.1)
            self.assertFalse((runtime / tool.PID_NAME).exists())

    def test_launcher_subprocess_import_graph_excludes_operational_services(self):
        script = """
import importlib.util
import json
import pathlib
import sys

tool = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("private_research_launcher_probe", tool)
if spec is None or spec.loader is None:
    raise RuntimeError("launcher import spec is unavailable")
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
print(json.dumps({
    "contract_loaded": "procurement_os.price_book_contract" in sys.modules,
    "forbidden": sorted(forbidden & set(sys.modules)),
}, sort_keys=True))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(TOOL)],
            cwd=TOOL.parents[2],
            env={
                **os.environ,
                "PYTHONPATH": str(TOOL.parents[1] / "src"),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {"contract_loaded": True, "forbidden": []},
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
