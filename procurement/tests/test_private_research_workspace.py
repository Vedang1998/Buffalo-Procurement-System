"""Immutable private-research workspace composition tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from procurement_os import private_research


class PrivateResearchWorkspaceTests(unittest.TestCase):
    def _patch_sources(self):
        intake = {"intake_id": "1" * 64}
        projection = {
            "projection_sha256": "a" * 64,
            "limitations": ["Private evidence has no operational authority."],
        }
        return mock.patch.multiple(
            private_research,
            read_private_research_intake=mock.DEFAULT,
            build_private_research_projection=mock.DEFAULT,
            render_private_research_html=mock.DEFAULT,
            render_private_research_csv=mock.DEFAULT,
        ), intake, projection

    def test_exact_replay_and_partial_resume_verify_immutable_artifacts(self):
        patcher, intake, projection = self._patch_sources()
        with TemporaryDirectory(prefix="buffalo-private-workspace-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with patcher as patched:
                patched["read_private_research_intake"].return_value = intake
                patched["build_private_research_projection"].return_value = projection
                patched["render_private_research_html"].return_value = "<html>safe</html>"
                patched["render_private_research_csv"].return_value = "safe\n"
                first = private_research.build_private_research_workspace(
                    root, "1" * 64
                )
                second = private_research.build_private_research_workspace(
                    root, "1" * 64
                )
                self.assertEqual(first, second)
                workspace = (
                    root / "private-research" / "workspaces" / first["workspace_id"]
                )
                (workspace / "manifest.json").unlink()
                resumed = private_research.build_private_research_workspace(
                    root, "1" * 64
                )
                self.assertEqual(resumed["workspace_id"], first["workspace_id"])

                staging = root / ".immutable-staging"
                staging.mkdir(mode=0o700, exist_ok=True)
                orphan = staging / ".coverage.json.crash.tmp"
                orphan.write_bytes(b"orphaned staging bytes")
                orphan.chmod(0o600)
                self.assertEqual(
                    private_research.read_private_research_workspace(workspace)[
                        "manifest"
                    ]["workspace_id"],
                    first["workspace_id"],
                )

                (workspace / "manifest.json").unlink()
                (workspace / "coverage.json").write_bytes(b"tampered\n")
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError,
                    "immutable artifact differs",
                ):
                    private_research.build_private_research_workspace(
                        root, "1" * 64
                    )

    def test_manifest_safety_fields_and_exact_file_inventory_are_bound(self):
        patcher, intake, projection = self._patch_sources()
        with TemporaryDirectory(prefix="buffalo-private-workspace-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with patcher as patched:
                patched["read_private_research_intake"].return_value = intake
                patched["build_private_research_projection"].return_value = projection
                patched["render_private_research_html"].return_value = "<html>safe</html>"
                patched["render_private_research_csv"].return_value = "safe\n"
                manifest = private_research.build_private_research_workspace(
                    root, "1" * 64
                )
                workspace = (
                    root
                    / "private-research"
                    / "workspaces"
                    / manifest["workspace_id"]
                )
                manifest_path = workspace / "manifest.json"
                value = json.loads(manifest_path.read_bytes())
                value["zero_authority"]["price_approval"] = True
                manifest_path.write_bytes(private_research._canonical_bytes(value))
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError, "manifest contract differs"
                ):
                    private_research.read_private_research_workspace(workspace)

                value["zero_authority"]["price_approval"] = False
                manifest_path.write_bytes(private_research._canonical_bytes(value))
                extra = workspace / "private-viewer.secret"
                extra.write_text("must-not-be-here\n", encoding="utf-8")
                os.chmod(extra, 0o600)
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError, "inventory differs"
                ):
                    private_research.read_private_research_workspace(workspace)
                extra.unlink()
                value["intake_id"] = "malformed"
                manifest_path.write_bytes(private_research._canonical_bytes(value))
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError, "manifest contract differs"
                ):
                    private_research.read_private_research_workspace(workspace)


if __name__ == "__main__":
    unittest.main()
