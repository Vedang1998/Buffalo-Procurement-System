"""Private research viewer launcher boundary tests."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


TOOL = Path(__file__).resolve().parents[1] / "tools" / "serve_private_research.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("serve_private_research_test", TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError("private research launcher could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrivateResearchToolsTests(unittest.TestCase):
    def test_initialize_runtime_creates_only_private_secret_without_disclosure(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-launcher-parent-") as temp:
            root = Path(temp) / "runtime"
            result = tool.initialize_runtime(root)
            self.assertEqual(result["contract"], tool.RUNTIME_CONTRACT)
            self.assertFalse(result["secret_value_disclosed"])
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            secret = root / tool.SECRET_NAME
            self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
            self.assertGreaterEqual(len(secret.read_text(encoding="utf-8").strip()), 24)
            self.assertNotIn(secret.read_text(encoding="utf-8").strip(), str(result))
            with self.assertRaisesRegex(tool.PrivateResearchServeError, "must be empty"):
                tool.initialize_runtime(root)

    def test_runtime_refuses_symlink_and_reports_only_owned_pid_state(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-launcher-parent-") as temp:
            parent = Path(temp)
            real = parent / "real"
            real.mkdir(mode=0o700)
            link = parent / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(tool.PrivateResearchServeError):
                tool.initialize_runtime(link)
            self.assertEqual(
                tool.status(real),
                {"contract": tool.RUNTIME_CONTRACT, "running": False},
            )
            pid = real / tool.PID_NAME
            pid.write_text('{"pid":999999999}\n', encoding="utf-8")
            pid.chmod(0o600)
            state = tool.status(real)
            self.assertFalse(state["running"])
            self.assertTrue(state["stale_pid_file"])
            pid.write_text(
                '{"cmdline_sha256":"'
                + ("a" * 64)
                + '","pid":'
                + str(os.getpid())
                + ',"process_start_ticks":1,"source_commit":"'
                + ("b" * 40)
                + '","source_tree":"'
                + ("c" * 40)
                + '"}\n',
                encoding="utf-8",
            )
            self.assertFalse(tool.status(real)["running"])

    def test_runtime_refuses_workspace_overlap_and_child_failure_is_typed(self):
        tool = _load_tool()
        with TemporaryDirectory(prefix="buffalo-private-launcher-parent-") as temp:
            parent = Path(temp)
            workspace = (
                parent
                / "private-research"
                / "workspaces"
                / ("a" * 64)
            )
            workspace.mkdir(parents=True, mode=0o700)
            with self.assertRaisesRegex(
                tool.PrivateResearchServeError, "must not be inside"
            ):
                tool.initialize_runtime(workspace / "runtime")

            runtime = parent / "runtime"
            runtime.mkdir(mode=0o700)
            secret = runtime / tool.SECRET_NAME
            secret.write_text("fixture-secret\n", encoding="utf-8")
            secret.chmod(0o600)

            class FakeProcess:
                pid = 424242
                returncode = None

                def poll(self):
                    return self.returncode

                def wait(self, timeout=None):
                    self.returncode = 7
                    return self.returncode

            class FakeResponse:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self):
                    return b'{"ok":true}'

            with (
                mock.patch.object(tool, "read_private_research_workspace"),
                mock.patch.object(tool, "_source_identity", return_value=("a" * 40, "b" * 40)),
                mock.patch.object(tool, "_assert_port_free"),
                mock.patch.object(
                    tool,
                    "_process_identity",
                    return_value={
                        "process_start_ticks": 1,
                        "cmdline_sha256": "c" * 64,
                    },
                ),
                mock.patch.object(tool.subprocess, "Popen", return_value=FakeProcess()),
                mock.patch.object(tool, "urlopen", return_value=FakeResponse()),
            ):
                with self.assertRaisesRegex(
                    tool.PrivateResearchServeError, "status 7"
                ):
                    tool.serve(runtime, workspace, 18876)
            self.assertFalse((runtime / tool.PID_NAME).exists())

    def test_launcher_source_has_no_database_or_shopify_composition(self):
        source = TOOL.read_text(encoding="utf-8")
        for forbidden in (
            "DATABASE_URL",
            "TEST_DATABASE_URL",
            "SHOPIFY_ACCESS_TOKEN",
            "procurement_os.api",
            "0.0.0.0",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn('"127.0.0.1"', source)
        self.assertIn('"PRIVATE_REAL_SOURCE_REVIEW"', source)


if __name__ == "__main__":
    unittest.main()
