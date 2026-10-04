"""Tests for the code-owned staging research validation identity."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from procurement_os import staging_research_validation as target
from procurement_os.staging_source_bundle import SourceStoreProof


def _workspace() -> dict[str, object]:
    membership = {
        name: {"count": value}
        for name, value in target.EXPECTED_MEMBERSHIP_COUNTS.items()
    }
    return {
        "manifest": {
            "workspace_id": target.ACCEPTED_TARGET_WORKSPACE_ID,
            "operational_authority": False,
            "artifacts": [
                {"name": name, "path": name, **record}
                for name, record in target.EXPECTED_ARTIFACTS.items()
            ],
        },
        "projection": {
            "projection_sha256": target.EXPECTED_PROJECTION_SHA256,
            "coverage_summary": deepcopy(target.EXPECTED_COVERAGE),
            "joint_forecast_research": {
                "primary_status_counts": deepcopy(
                    target.EXPECTED_PRIMARY_STATUS_COUNTS
                ),
                "violation_counts": deepcopy(target.EXPECTED_VIOLATIONS),
                "membership_controls": membership,
            },
        },
        "artifacts": {},
    }


def _source_proof() -> SourceStoreProof:
    return SourceStoreProof(
        ref=target.ACCEPTED_REF,
        tip=target.ACCEPTED_TIP,
        tree=target.ACCEPTED_TREE,
        parent=target.ACCEPTED_PARENT,
        parent_tree=target.ACCEPTED_PARENT_TREE,
        object_count=target.ACCEPTED_OBJECT_COUNT,
        object_set_sha256=target.ACCEPTED_OBJECT_SET_SHA256,
        object_type_counts=target.ACCEPTED_OBJECT_TYPE_COUNTS,
        commit_count=target.ACCEPTED_COMMIT_COUNT,
        ls_tree_sha256=target.ACCEPTED_LS_TREE_SHA256,
        ls_tree_entries=target.ACCEPTED_LS_TREE_ENTRIES,
        mode_counts=target.ACCEPTED_MODE_COUNTS,
    )


class StagingResearchValidationTests(unittest.TestCase):
    def test_exact_semantic_controls_return_only_the_code_owned_identity(self):
        proof = target.validate_replayed_workspace(
            _workspace(),
            manifest_sha256=target.ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256,
        )
        self.assertEqual(proof.validation_identity, target.RESEARCH_VALIDATION_IDENTITY)
        self.assertEqual(len(proof.validation_identity), 64)

    def test_each_semantic_control_family_fails_closed(self):
        cases = []
        artifact = _workspace()
        artifact["manifest"]["artifacts"][0]["bytes"] += 1
        cases.append(artifact)
        coverage = _workspace()
        coverage["projection"]["coverage_summary"]["variant_count"] -= 1
        cases.append(coverage)
        horizon = _workspace()
        horizon["projection"]["joint_forecast_research"][
            "primary_status_counts"
        ]["H10"]["numerical_zero"] -= 1
        cases.append(horizon)
        violation = _workspace()
        violation["projection"]["joint_forecast_research"][
            "violation_counts"
        ]["corrected_unique_variants"] = 1
        cases.append(violation)
        membership = _workspace()
        membership["projection"]["joint_forecast_research"][
            "membership_controls"
        ]["eligible"]["count"] -= 1
        cases.append(membership)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(target.StagingResearchValidationError):
                    target.validate_replayed_workspace(
                        value,
                        manifest_sha256=target.ACCEPTED_TARGET_WORKSPACE_MANIFEST_SHA256,
                    )

    def test_all_worker_paths_must_share_one_exact_release(self):
        with tempfile.TemporaryDirectory(prefix="research-path-binding-") as raw:
            release = Path(raw).resolve()
            payload = release / "payload"
            workspace = (
                payload
                / "private-research"
                / "workspaces"
                / target.ACCEPTED_TARGET_WORKSPACE_ID
            )
            workspace.mkdir(parents=True)
            (release / "source.git").mkdir()
            manifest = release / "deployment-inventory.json"
            manifest.write_bytes(b"manifest")
            environment = {
                "BUFFALO_RESEARCH_ROOT": str(payload),
                "BUFFALO_RESEARCH_MANIFEST": str(manifest),
                "BUFFALO_RESEARCH_GIT_DIR": str(release / "source.git"),
                "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": str(workspace),
            }
            with mock.patch.object(
                target,
                "_stream_sha256",
                return_value=(
                    target.DEPLOYMENT_INVENTORY_BYTES,
                    target.DEPLOYMENT_INVENTORY_FILE_SHA256,
                ),
            ):
                paths = target.validate_staging_research_paths(environment)
                self.assertEqual(paths.release, release)
                for name in tuple(environment):
                    altered = dict(environment)
                    altered[name] = str(release)
                    with self.subTest(name=name), self.assertRaises(
                        target.StagingResearchValidationError
                    ):
                        target.validate_staging_research_paths(altered)

    def test_worker_source_proof_is_observed_not_echoed(self):
        with tempfile.TemporaryDirectory(prefix="research-worker-binding-") as raw:
            release = Path(raw).resolve()
            payload = release / "payload"
            workspace = (
                payload
                / "private-research"
                / "workspaces"
                / target.ACCEPTED_TARGET_WORKSPACE_ID
            )
            workspace.mkdir(parents=True)
            git_dir = release / "source.git"
            git_dir.mkdir()
            manifest = release / "deployment-inventory.json"
            manifest.write_bytes(b"manifest")
            environment = {
                "BUFFALO_RESEARCH_ROOT": str(payload),
                "BUFFALO_RESEARCH_MANIFEST": str(manifest),
                "BUFFALO_RESEARCH_GIT_DIR": str(git_dir),
                "BUFFALO_PRIVATE_RESEARCH_WORKSPACE": str(workspace),
            }
            worker_uid, worker_gid, supervisor_uid = 2001, 2002, 2000
            real_stat = Path.stat

            def fake_stat(path: Path, *args, **kwargs):
                info = real_stat(path, *args, **kwargs)
                mode = {
                    release: 0o710,
                    payload: 0o700,
                    workspace: 0o700,
                    git_dir: 0o550,
                    manifest: 0o440,
                }.get(path)
                if mode is None:
                    return info
                uid = worker_uid if path in {payload, workspace} else supervisor_uid
                return SimpleNamespace(
                    st_mode=(info.st_mode & ~0o7777) | mode,
                    st_uid=uid,
                    st_gid=worker_gid,
                    st_nlink=1 if path == manifest else info.st_nlink,
                )

            patches = (
                mock.patch.object(target, "_stream_sha256", return_value=(
                    target.DEPLOYMENT_INVENTORY_BYTES,
                    target.DEPLOYMENT_INVENTORY_FILE_SHA256,
                )),
                mock.patch.object(Path, "stat", autospec=True, side_effect=fake_stat),
                mock.patch.object(target.os, "getuid", return_value=worker_uid),
                mock.patch.object(target.os, "getgid", return_value=worker_gid),
                mock.patch.object(
                    target, "validate_accepted_source_store", return_value=_source_proof()
                ),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                paths = target.validate_staging_research_paths(
                    environment, worker_runtime=True
                )
                self.assertEqual(paths.git_dir, git_dir)
                target.validate_accepted_source_store.assert_called_once_with(
                    git_dir, owner_uid=supervisor_uid, owner_gid=worker_gid
                )
                target.validate_accepted_source_store.return_value = SimpleNamespace(
                    **{**_source_proof().__dict__, "object_count": 1}
                )
                with self.assertRaisesRegex(
                    target.StagingResearchValidationError, "source proof"
                ):
                    target.validate_staging_research_paths(
                        environment, worker_runtime=True
                    )


if __name__ == "__main__":
    unittest.main()
