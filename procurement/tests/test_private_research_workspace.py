"""Immutable private-research workspace composition tests."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock
import weakref

from procurement_os import private_research
from procurement_os import private_research_v2
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

            def read_with_parent(path, **kwargs):
                if Path(path).name == "f" * 64:
                    return {"projection": deepcopy(parent_projection)}
                return original_reader(path, **kwargs)

            with (
                mock.patch.object(
                    corrected,
                    "build_private_v3_corrected_projection",
                    side_effect=lambda *_args: corrected._seal_owned(
                        deepcopy(projection), corrected._PROJECTION_PROOF
                    ),
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

    def test_corrected_publication_is_single_projection_and_sequential_bytes(self):
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
            }
        )

        class WeakPayload(bytearray):
            pass

        projection_calls = 0
        projection_refs: list[weakref.ReferenceType] = []
        payload_refs: list[weakref.ReferenceType] = []
        events: list[str] = []

        def build_projection(*_args):
            nonlocal projection_calls
            projection_calls += 1
            value = corrected._seal_owned(
                deepcopy(projection), corrected._PROJECTION_PROOF
            )
            projection_refs.append(weakref.ref(value))
            return value

        def render(name, _projection):
            if payload_refs:
                self.assertIsNone(payload_refs[-1]())
            payload = WeakPayload(("artifact:" + name).encode("utf-8"))
            payload_refs.append(weakref.ref(payload))
            events.append("render:" + name)
            return payload

        def publish(_storage, key, data):
            name = Path(key).name
            events.append("write:" + name)
            if name != "manifest.json":
                self.assertIs(payload_refs[-1](), data)

        with TemporaryDirectory(prefix="buffalo-corrected-publish-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with (
                mock.patch.object(
                    corrected,
                    "build_private_v3_corrected_projection",
                    new=build_projection,
                ),
                mock.patch.object(
                    corrected,
                    "_ensure_corrected_private_parent",
                    new=lambda *_args: None,
                ),
                mock.patch.object(
                    private_research, "_render_artifact_bytes", new=render
                ),
                mock.patch.object(
                    private_research, "_put_once_or_verify", new=publish
                ),
                mock.patch.object(
                    private_research.gc, "collect", new=lambda: 0
                ),
            ):
                manifest = (
                    private_research._build_private_v3_corrected_workspace_from_verified(
                        root,
                        corrected_input,
                        {},
                        {},
                        {},
                        {},
                    )
                )
        names = sorted(private_research._ARTIFACT_MEDIA_TYPES)
        self.assertEqual(projection_calls, 1)
        self.assertEqual(
            events,
            [*("render:" + name for name in names),
             *(item for name in names for item in ("render:" + name, "write:" + name)),
             "write:manifest.json"],
        )
        self.assertEqual([record["name"] for record in manifest["artifacts"]], names)
        self.assertTrue(all(reference() is None for reference in payload_refs))
        self.assertIsNone(projection_refs[0]())

        before = len(corrected._VERIFIED)

        def fail_render(_name, _projection):
            raise private_research.PrivateResearchError("synthetic render failure")

        with TemporaryDirectory(prefix="buffalo-corrected-publish-fail-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with (
                mock.patch.object(
                    corrected,
                    "build_private_v3_corrected_projection",
                    new=build_projection,
                ),
                mock.patch.object(
                    private_research, "_render_artifact_bytes", new=fail_render
                ),
                mock.patch.object(
                    private_research.gc, "collect", new=lambda: 0
                ),
            ):
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError,
                    "synthetic render failure",
                ):
                    private_research._build_private_v3_corrected_workspace_from_verified(
                        root, corrected_input, {}, {}, {}, {}
                    )
        self.assertEqual(len(corrected._VERIFIED), before)
        self.assertIsNone(projection_refs[-1]())

        write_failure_refs: list[weakref.ReferenceType] = []

        def render_before_write_failure(name, _projection):
            if write_failure_refs:
                self.assertIsNone(write_failure_refs[-1]())
            payload = WeakPayload(("write-failure:" + name).encode("utf-8"))
            write_failure_refs.append(weakref.ref(payload))
            return payload

        def fail_publish(_storage, _key, data):
            self.assertIs(write_failure_refs[-1](), data)
            raise private_research.PrivateResearchError(
                "synthetic artifact write failure"
            )

        with TemporaryDirectory(prefix="buffalo-corrected-write-fail-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with (
                mock.patch.object(
                    corrected,
                    "build_private_v3_corrected_projection",
                    new=build_projection,
                ),
                mock.patch.object(
                    private_research,
                    "_render_artifact_bytes",
                    new=render_before_write_failure,
                ),
                mock.patch.object(
                    private_research, "_put_once_or_verify", new=fail_publish
                ),
                mock.patch.object(private_research.gc, "collect", new=lambda: 0),
            ):
                with self.assertRaisesRegex(
                    private_research.PrivateResearchError,
                    "synthetic artifact write failure",
                ):
                    private_research._build_private_v3_corrected_workspace_from_verified(
                        root, corrected_input, {}, {}, {}, {}
                    )
        self.assertEqual(len(corrected._VERIFIED), before)
        self.assertIsNone(projection_refs[-1]())
        self.assertTrue(
            all(reference() is None for reference in write_failure_refs)
        )

    def test_corrected_readback_is_sequential_compact_and_retry_safe(self):
        corrected_input, projection_value, _records = self._corrected_identity_fixture()
        artifact_templates = {
            name: ("verified:" + name).encode("utf-8")
            for name in sorted(private_research._ARTIFACT_MEDIA_TYPES)
        }
        records = [
            private_research._artifact_record(name, artifact_templates[name])
            for name in sorted(artifact_templates)
        ]
        manifest = {
            "contract": private_research.V3_CORRECTED_CONTRACT,
            "data_mode": private_research.V2_DATA_MODE,
            "authority": private_research.AUTHORITY,
            "operational_authority": False,
            "workspace_id": "f" * 64,
            "corrected_input_id": corrected_input["input_id"],
            "corrected_input_key": corrected.corrected_input_manifest_key(
                corrected_input["input_id"]
            ),
            "corrected_input_sha256": "0" * 64,
            "parent_v3_input_id": "1" * 64,
            "parent_v3_input_key": "parent-v3",
            "parent_v3_input_sha256": "2" * 64,
            "parent_v2_input_id": "3" * 64,
            "parent_v2_input_key": "parent-v2",
            "parent_v2_input_sha256": "4" * 64,
            "base_intake_id": "5" * 64,
            "base_intake_key": "base",
            "base_intake_sha256": "6" * 64,
            "parent_projection_sha256": "7" * 64,
            "parent_projection_artifact_sha256": "8" * 64,
            "creation_delta_id": "9" * 64,
            "creation_delta_raw_sha256": "a" * 64,
            "creation_delta_normalized_row_set_sha256": "b" * 64,
            "implementation_lineage": corrected_input["implementation_lineage"],
            "joint_policy_canonical_sha256": "c" * 64,
            "projection_contract": private_research.V3_CORRECTED_PROJECTION_CONTRACT,
            "projection_sha256": projection_value["projection_sha256"],
            "artifacts": records,
            "limitations": projection_value["limitations"],
            "zero_authority": dict(private_research._ZERO_AUTHORITY),
        }
        events: list[str] = []
        payload_refs: list[weakref.ReferenceType] = []
        enforce_sequential_release = False
        snapshot_results: list[dict[str, str]] = []

        class WeakPayload(bytearray):
            pass

        def parent_snapshot(_root):
            if snapshot_results:
                return snapshot_results.pop(0)
            return {"accepted": "unchanged"}

        class Storage:
            def __init__(self, *, tampered=False):
                self.tampered = tampered

            def get_bytes(self, key):
                name = Path(key).name
                events.append("read:" + name)
                if self.tampered and name == "coverage.json":
                    payload = WeakPayload(b"tampered")
                else:
                    payload = WeakPayload(artifact_templates[name])
                payload_refs.append(weakref.ref(payload))
                return payload

        def replay(**_kwargs):
            events.append("replay")
            return corrected._seal_owned(
                deepcopy(projection_value), corrected._PROJECTION_PROOF
            )

        def render(name, _projection):
            if enforce_sequential_release and payload_refs:
                self.assertTrue(
                    all(reference() is None for reference in payload_refs)
                )
                payload_refs.clear()
            events.append("render:" + name)
            payload = WeakPayload(artifact_templates[name])
            payload_refs.append(weakref.ref(payload))
            return payload

        def reject_json_loads(*_args, **_kwargs):
            raise AssertionError("projection JSON must not be parsed into a duplicate graph")

        patches = (
            mock.patch.object(
                private_research,
                "_replay_private_v3_corrected_workspace_projection",
                new=replay,
            ),
            mock.patch.object(
                private_research, "_render_artifact_bytes", new=render
            ),
            mock.patch.object(
                corrected,
                "accepted_parent_snapshot",
                new=parent_snapshot,
            ),
            mock.patch.object(
                private_research.json, "loads", new=reject_json_loads
            ),
            mock.patch.object(private_research.gc, "collect", new=lambda: 0),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            enforce_sequential_release = True
            compact = private_research._read_private_v3_corrected_workspace(
                root=Path("/private"),
                prefix="private-research/workspaces/" + "f" * 64,
                expected_workspace_id="f" * 64,
                storage=Storage(),
                manifest=manifest,
                manifest_bytes=private_research._canonical_bytes(manifest),
                repo_root=Path(__file__).resolve().parents[2],
                retain_artifacts=False,
            )
            self.assertEqual(compact["artifacts"], {})
            self.assertTrue(
                all(reference() is None for reference in payload_refs)
            )
            payload_refs.clear()
            private_research._release_heavy_research_values(
                {"readback_projection": compact["projection"]}
            )
            compact.clear()
            expected_events = ["replay"]
            for name in sorted(artifact_templates):
                expected_events.extend(("render:" + name, "read:" + name))
            self.assertEqual(events, expected_events)

            enforce_sequential_release = False
            events.clear()
            public = private_research._read_private_v3_corrected_workspace(
                root=Path("/private"),
                prefix="private-research/workspaces/" + "f" * 64,
                expected_workspace_id="f" * 64,
                storage=Storage(),
                manifest=manifest,
                manifest_bytes=private_research._canonical_bytes(manifest),
                repo_root=Path(__file__).resolve().parents[2],
            )
            self.assertEqual(public["artifacts"], artifact_templates)
            private_research._release_heavy_research_values(
                {"readback_projection": public["projection"]}
            )
            public.clear()
            self.assertTrue(
                all(reference() is None for reference in payload_refs)
            )
            payload_refs.clear()

            active_before = len(corrected._VERIFIED)
            with self.assertRaisesRegex(
                private_research.PrivateResearchError,
                "artifact content differs",
            ):
                private_research._read_private_v3_corrected_workspace(
                    root=Path("/private"),
                    prefix="private-research/workspaces/" + "f" * 64,
                    expected_workspace_id="f" * 64,
                    storage=Storage(tampered=True),
                    manifest=manifest,
                    manifest_bytes=private_research._canonical_bytes(manifest),
                    repo_root=Path(__file__).resolve().parents[2],
                    retain_artifacts=False,
                )
            self.assertEqual(len(corrected._VERIFIED), active_before)
            self.assertTrue(
                all(reference() is None for reference in payload_refs)
            )
            payload_refs.clear()

            snapshot_results.extend(
                ({"accepted": "before"}, {"accepted": "after"})
            )
            with self.assertRaisesRegex(
                private_research.PrivateResearchError,
                "accepted parent changed",
            ):
                private_research._read_private_v3_corrected_workspace(
                    root=Path("/private"),
                    prefix="private-research/workspaces/" + "f" * 64,
                    expected_workspace_id="f" * 64,
                    storage=Storage(),
                    manifest=manifest,
                    manifest_bytes=private_research._canonical_bytes(manifest),
                    repo_root=Path(__file__).resolve().parents[2],
                    retain_artifacts=False,
                )
            self.assertEqual(len(corrected._VERIFIED), active_before)
            self.assertTrue(
                all(reference() is None for reference in payload_refs)
            )

    def test_corrected_public_build_phase_ends_before_independent_readback(self):
        class WeakMapping(dict):
            pass

        references: dict[str, weakref.ReferenceType] = {}
        phases: list[str] = []
        source_active_before = len(private_research_v2._SOURCE_VERIFIED_REGISTRY)
        source_released_before = len(private_research_v2._SOURCE_RELEASED_REGISTRY)
        corrected_active_before = len(corrected._VERIFIED)
        corrected_released_before = len(corrected._RELEASED)
        parent_workspace_id = "f" * 64
        corrected_workspace_id = "e" * 64
        manifest = {"workspace_id": corrected_workspace_id}

        def read_bundle(_root, _input_id):
            parent_v3 = private_research_v2._seal_source_verified(
                {"parent": "v3"},
                proof=private_research_v3._V3_INPUT_SOURCE_PROOF,
            )
            parent_v2 = private_research_v2._seal_source_verified(
                {"parent": "v2"}, proof=private_research_v2._INPUT_SOURCE_PROOF
            )
            base = WeakMapping({"base": "intake"})
            references.update(
                parent_v3=weakref.ref(parent_v3),
                parent_v2=weakref.ref(parent_v2),
                base_intake=weakref.ref(base),
            )
            phases.append("bundle")
            return parent_v3, parent_v2, base

        def read_corrected(_root, _input_id, **_kwargs):
            delta = corrected._seal_owned(
                {"delta": "fixture"}, corrected._DELTA_PROOF
            )
            corrected_input = corrected._seal_owned(
                {"parent_projection": {"workspace_id": parent_workspace_id}},
                corrected._INPUT_PROOF,
            )
            references.update(
                delta=weakref.ref(delta),
                corrected_input=weakref.ref(corrected_input),
            )
            phases.append("corrected-input")
            return delta, corrected_input

        def read_workspace(path, **kwargs):
            if Path(path).name == parent_workspace_id:
                parent_projection = WeakMapping({"parent": "projection"})
                references["parent_projection"] = weakref.ref(parent_projection)
                phases.append("parent-workspace")
                return {
                    "projection": parent_projection,
                    "artifacts": {"large": b"parent-artifact"},
                }
            self.assertEqual(Path(path).name, corrected_workspace_id)
            self.assertEqual(kwargs, {"_retain_corrected_artifacts": False})
            self.assertTrue(references)
            self.assertTrue(
                all(reference() is None for reference in references.values())
            )
            phases.append("readback")
            return {
                "manifest": dict(manifest),
                "projection": corrected._seal_owned(
                    {"projection": "verified"}, corrected._PROJECTION_PROOF
                ),
                "artifacts": {},
            }

        def publish_verified(
            _root,
            corrected_input,
            delta,
            parent_v3,
            parent_v2,
            parent_projection,
        ):
            self.assertIsNone(references["base_intake"]())
            self.assertIs(references["corrected_input"](), corrected_input)
            self.assertIs(references["delta"](), delta)
            self.assertIs(references["parent_v3"](), parent_v3)
            self.assertIs(references["parent_v2"](), parent_v2)
            self.assertIs(references["parent_projection"](), parent_projection)
            phases.append("publish")
            return dict(manifest)

        with TemporaryDirectory(prefix="buffalo-corrected-boundary-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            with (
                mock.patch.object(
                    private_research_v3,
                    "read_private_v3_research_bundle",
                    new=read_bundle,
                ),
                mock.patch.object(
                    corrected,
                    "read_private_v3_corrected_bundle",
                    new=read_corrected,
                ),
                mock.patch.object(
                    corrected,
                    "accepted_parent_snapshot",
                    new=lambda _root: {"accepted": "unchanged"},
                ),
                mock.patch.object(
                    private_research,
                    "read_private_research_workspace",
                    new=read_workspace,
                ),
                mock.patch.object(
                    private_research,
                    "_build_private_v3_corrected_workspace_from_verified",
                    new=publish_verified,
                ),
                mock.patch.object(private_research.gc, "collect", new=lambda: 0),
            ):
                results = []
                for _index in range(2):
                    results.append(
                        private_research.build_private_v3_corrected_research_workspace(
                            root,
                            "a" * 64,
                            repo_root=Path(__file__).resolve().parents[2],
                        )
                    )
                    self.assertTrue(
                        all(
                            reference() is None
                            for reference in references.values()
                        )
                    )
                    self.assertEqual(
                        len(private_research_v2._SOURCE_VERIFIED_REGISTRY),
                        source_active_before,
                    )
                    self.assertEqual(
                        len(private_research_v2._SOURCE_RELEASED_REGISTRY),
                        source_released_before,
                    )
                    self.assertEqual(len(corrected._VERIFIED), corrected_active_before)
                    self.assertEqual(
                        len(corrected._RELEASED), corrected_released_before
                    )
        self.assertEqual(results, [manifest, manifest])
        self.assertEqual(
            phases,
            2
            * [
                "bundle",
                "corrected-input",
                "parent-workspace",
                "publish",
                "readback",
            ],
        )
        self.assertTrue(all(reference() is None for reference in references.values()))

    def test_corrected_replay_drops_base_before_projection_rebuild(self):
        class WeakMapping(dict):
            pass

        references: dict[str, weakref.ReferenceType] = {}
        parent_workspace_id = "f" * 64

        def read_bundle(_root, _input_id):
            parent_v3 = private_research_v2._seal_source_verified(
                {"parent": "v3"},
                proof=private_research_v3._V3_INPUT_SOURCE_PROOF,
            )
            parent_v2 = private_research_v2._seal_source_verified(
                {"parent": "v2"}, proof=private_research_v2._INPUT_SOURCE_PROOF
            )
            base = WeakMapping({"base": "intake"})
            references.update(
                parent_v3=weakref.ref(parent_v3),
                parent_v2=weakref.ref(parent_v2),
                base_intake=weakref.ref(base),
            )
            return parent_v3, parent_v2, base

        def read_corrected(_root, _input_id, **_kwargs):
            delta = corrected._seal_owned(
                {"delta": "fixture"}, corrected._DELTA_PROOF
            )
            corrected_input = corrected._seal_owned(
                {"parent_projection": {"workspace_id": parent_workspace_id}},
                corrected._INPUT_PROOF,
            )
            references.update(
                delta=weakref.ref(delta),
                corrected_input=weakref.ref(corrected_input),
            )
            return delta, corrected_input

        def read_parent(_path, **_kwargs):
            parent_projection = WeakMapping({"parent": "projection"})
            references["parent_projection"] = weakref.ref(parent_projection)
            return {"projection": parent_projection, "artifacts": {}}

        def stop_at_rebuild(*_args):
            self.assertIsNone(references["base_intake"]())
            self.assertIsNotNone(references["parent_v3"]())
            self.assertIsNotNone(references["parent_v2"]())
            raise corrected.PrivateResearchV3CorrectedError(
                "synthetic replay stop"
            )

        with (
            mock.patch.object(
                private_research_v3,
                "read_private_v3_research_bundle",
                new=read_bundle,
            ),
            mock.patch.object(
                corrected,
                "read_private_v3_corrected_bundle",
                new=read_corrected,
            ),
            mock.patch.object(
                private_research,
                "read_private_research_workspace",
                new=read_parent,
            ),
            mock.patch.object(
                corrected,
                "build_private_v3_corrected_projection",
                new=stop_at_rebuild,
            ),
            mock.patch.object(private_research.gc, "collect", new=lambda: 0),
        ):
            with self.assertRaisesRegex(
                private_research.PrivateResearchError,
                "semantic replay failed",
            ):
                private_research._replay_private_v3_corrected_workspace_projection(
                    root=Path("/private"),
                    input_id="a" * 64,
                    manifest={},
                    records=[],
                    expected_workspace_id="e" * 64,
                    repo_root=Path(__file__).resolve().parents[2],
                )
        self.assertTrue(all(reference() is None for reference in references.values()))

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
