"""Immutable private-research workspace composition tests."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from procurement_os import private_research
from procurement_os import private_research_v3
from procurement_os import private_research_v3_corrected as corrected


class PrivateResearchWorkspaceTests(unittest.TestCase):
    @staticmethod
    def _corrected_identity_fixture():
        corrected_input = {
            "contract": corrected.INPUT_CONTRACT,
            "input_id": "1" * 64,
            "parent_v3_input": {"input_id": "2" * 64, "sha256": "3" * 64},
            "parent_v2_input": {"input_id": "4" * 64, "sha256": "5" * 64},
            "base_intake": {"intake_id": "6" * 64, "sha256": "7" * 64},
            "parent_projection": {
                "projection_sha256": "8" * 64,
                "artifact_sha256": "9" * 64,
            },
            "creation_evidence_delta": {
                "delta_id": "a" * 64,
                "raw_csv_sha256": "b" * 64,
                "normalized_row_set_sha256": "c" * 64,
            },
            "joint_policy": {"policy_canonical_sha256": "d" * 64},
            "implementation_lineage": {
                "accepted_commit": "1" * 40,
                "accepted_tree": "2" * 40,
                "runtime_commit": "3" * 40,
                "runtime_tree": "4" * 40,
                "test_commit": "5" * 40,
                "test_tree": "6" * 40,
                "implementation_commit": "7" * 40,
                "implementation_tree": "8" * 40,
            },
        }
        projection = {
            "contract": private_research.V3_CORRECTED_PROJECTION_CONTRACT,
            "data_mode": private_research.V2_DATA_MODE,
            "projection_sha256": "e" * 64,
            "limitations": ["ZERO_AUTHORITY_FIXTURE"],
        }
        records = [
            {
                "name": name,
                "path": name,
                "bytes": index,
                "sha256": str(index) * 64,
                "media_type": private_research._ARTIFACT_MEDIA_TYPES[name],
            }
            for index, name in enumerate(
                sorted(private_research._ARTIFACT_MEDIA_TYPES), 1
            )
        ]
        return corrected_input, projection, records

    def _patch_sources(self):
        intake = {"intake_id": "1" * 64}
        projection = {
            "contract": private_research.PROJECTION_CONTRACT,
            "data_mode": "PRIVATE_REAL_DATA_RESEARCH_ONLY",
            "projection_sha256": "a" * 64,
            "limitations": ["Private evidence has no operational authority."],
        }
        return mock.patch.multiple(
            private_research,
            read_private_research_intake=mock.DEFAULT,
            build_private_research_projection=mock.DEFAULT,
            render_private_research_html=mock.DEFAULT,
            render_private_research_csv=mock.DEFAULT,
            private_research_projection_sha256=mock.DEFAULT,
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
                patched["private_research_projection_sha256"].return_value = (
                    "a" * 64
                )
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
                coverage = workspace / "coverage.json"
                coverage.chmod(0o644)
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError,
                    "workspace object differs",
                ):
                    private_research.build_private_research_workspace(
                        root, "1" * 64
                    )
                coverage.chmod(0o600)
                extra = workspace / "unexpected.tmp"
                extra.write_bytes(b"unexpected")
                extra.chmod(0o600)
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError,
                    "workspace inventory differs",
                ):
                    private_research.build_private_research_workspace(
                        root, "1" * 64
                    )
                extra.unlink()
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
                patched["private_research_projection_sha256"].return_value = (
                    "a" * 64
                )
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

    def test_corrected_workspace_identity_binds_every_artifact_hash(self):
        corrected_input, projection, records = self._corrected_identity_fixture()
        identity = private_research._v3_corrected_workspace_identity(
            corrected_input, projection, records
        )
        self.assertEqual(
            identity,
            "810c7862c1f3132454c1cf255b0ff0670f11f807c8f4cefd33f53913c1627eda",
        )
        with mock.patch.object(
            private_research,
            "private_research_projection_sha256",
            return_value="e" * 64,
        ):
            self.assertEqual(
                private_research._coverage_export(projection)["contract"],
                "BUFFALO_PRIVATE_RESEARCH_COVERAGE_EXPORT_V3_CORRECTED_V1",
            )
        for bad_projection in (
            {**projection, "contract": "UNKNOWN"},
            {**projection, "data_mode": "PRIVATE_REAL_DATA_RESEARCH_ONLY"},
            {**projection, "contract": private_research.V3_PROJECTION_CONTRACT},
        ):
            with self.subTest(contract=bad_projection["contract"]), self.assertRaisesRegex(
                private_research.PrivateResearchError,
                "(?:tuple|coverage projection) differs",
            ):
                private_research._coverage_export(bad_projection)
        for index in range(len(records)):
            forged = deepcopy(records)
            forged[index]["sha256"] = "f" * 64
            with self.subTest(artifact=forged[index]["name"]):
                self.assertNotEqual(
                    private_research._v3_corrected_workspace_identity(
                        corrected_input, projection, forged
                    ),
                    identity,
                )
        float_bytes = deepcopy(records)
        float_bytes[0]["bytes"] = float(float_bytes[0]["bytes"])
        with self.assertRaisesRegex(
            private_research.PrivateResearchError, "artifact records differ"
        ):
            private_research._v3_corrected_workspace_identity(
                corrected_input, projection, float_bytes
            )

    def test_corrected_builder_replays_public_inventory_and_modes(self):
        corrected_input, projection, _records = self._corrected_identity_fixture()
        corrected_input.update(
            {
                "parent_v3_input": {
                    **corrected_input["parent_v3_input"],
                    "storage_key": "private-research/v3-inputs/" + "2" * 64 + ".json",
                },
                "parent_v2_input": {
                    **corrected_input["parent_v2_input"],
                    "storage_key": "private-research/v2-inputs/" + "4" * 64 + ".json",
                },
                "base_intake": {
                    **corrected_input["base_intake"],
                    "storage_key": "private-research/intakes/" + "6" * 64 + ".json",
                },
                "parent_projection": {
                    **corrected_input["parent_projection"],
                    "workspace_id": "f" * 64,
                },
                "creation_evidence_delta": {
                    **corrected_input["creation_evidence_delta"],
                },
            }
        )
        projection.update(
            {
                "data_mode": private_research.V2_DATA_MODE,
                "coverage_rows": [],
                "projection_sha256": "e" * 64,
            }
        )
        with TemporaryDirectory(prefix="buffalo-corrected-workspace-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            parent_projection = {"contract": "accepted-parent-fixture"}
            original_reader = private_research.read_private_research_workspace

            def read_with_parent(path):
                if Path(path).name == "f" * 64:
                    return {"projection": parent_projection}
                return original_reader(path)

            with (
                mock.patch.object(
                    corrected,
                    "build_private_v3_corrected_projection",
                    return_value=projection,
                ) as build_projection,
                mock.patch.object(
                    corrected,
                    "read_private_v3_corrected_bundle",
                    return_value=({}, corrected_input),
                ),
                mock.patch.object(
                    corrected,
                    "accepted_parent_snapshot",
                    return_value={"accepted_parent": "unchanged"},
                ),
                mock.patch.object(
                    private_research_v3,
                    "read_private_v3_research_bundle",
                    return_value=({}, {}, {}),
                ),
                mock.patch.object(
                    private_research,
                    "render_private_research_html",
                    return_value="<html>corrected</html>",
                ),
                mock.patch.object(
                    private_research,
                    "render_private_research_csv",
                    return_value="row_type\r\n",
                ),
                mock.patch.object(
                    private_research,
                    "read_private_research_workspace",
                    side_effect=read_with_parent,
                ),
                mock.patch.object(
                    private_research,
                    "private_research_projection_sha256",
                    return_value="e" * 64,
                ),
            ):
                first = private_research.build_private_v3_corrected_research_workspace(
                    root,
                    corrected_input["input_id"],
                    repo_root=Path(__file__).resolve().parents[2],
                )
                second = private_research.build_private_v3_corrected_research_workspace(
                    root,
                    corrected_input["input_id"],
                    repo_root=Path(__file__).resolve().parents[2],
                )
                self.assertEqual(first, second)
                workspace = (
                    root
                    / "private-research"
                    / "workspaces"
                    / first["workspace_id"]
                )
                restarted = original_reader(workspace)
                self.assertEqual(restarted["manifest"], first)
                self.assertEqual(restarted["projection"], projection)
                self.assertGreaterEqual(build_projection.call_count, 5)
                coverage = workspace / "coverage.json"
                coverage.chmod(0o644)
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError,
                    "workspace object differs",
                ):
                    private_research.build_private_v3_corrected_research_workspace(
                        root,
                        corrected_input["input_id"],
                        repo_root=Path(__file__).resolve().parents[2],
                    )
                coverage.chmod(0o600)
                extra = workspace / "unexpected.tmp"
                extra.write_bytes(b"unexpected")
                extra.chmod(0o600)
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError,
                    "workspace inventory differs",
                ):
                    private_research.build_private_v3_corrected_research_workspace(
                        root,
                        corrected_input["input_id"],
                        repo_root=Path(__file__).resolve().parents[2],
                    )

    def test_malformed_corrected_input_id_is_normalized_to_workspace_error(self):
        with self.assertRaisesRegex(
            private_research.PrivateResearchError, "canonical bytes differ"
        ):
            private_research._canonical_bytes({"forbidden": float("nan")})
        with TemporaryDirectory(prefix="buffalo-corrected-manifest-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with self.assertRaisesRegex(
                private_research.PrivateResearchError,
                "manifest contract differs",
            ):
                private_research._read_private_v3_corrected_workspace(
                    root=root,
                    prefix="private-research/workspaces/" + "a" * 64,
                    expected_workspace_id="a" * 64,
                    storage=mock.Mock(),
                    manifest={"corrected_input_id": "../escape"},
                    manifest_bytes=b"{}\n",
                    repo_root=Path(__file__).resolve().parents[2],
                )


if __name__ == "__main__":
    unittest.main()
