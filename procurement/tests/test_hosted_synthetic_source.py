"""Portable adversarial source-binding tests; no Linux capability claim."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import hosted_synthetic_source as source


class HostedSyntheticSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buffalo-source-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Source Test")
        self.git("config", "user.email", "synthetic@example.invalid")
        for relative in source.SOURCE_PATHS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic fixture: " + relative + "\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "synthetic baseline")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "--quiet", "--detach", self.sha)
        baseline = patch.object(source, "BASELINE", self.sha)
        baseline.start()
        self.addCleanup(baseline.stop)

    def git(self, *arguments):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *arguments],
            env={"PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": os.devnull},
            check=True, capture_output=True, text=True,
        ).stdout

    def bind(self, **changes):
        metadata = dict(repository=source.REPOSITORY, event="push", ref=source.REF, sha=self.sha)
        metadata.update(changes)
        return source.bind_hosted_source(self.root, **metadata)

    def test_clean_detached_source_binds_exact_tree_and_bytes_without_authority(self):
        before = self.git("status", "--porcelain=v1", "--untracked-files=all")
        result = self.bind()
        self.assertEqual(result.commit, self.sha)
        self.assertEqual(result.tree, self.git("rev-parse", "HEAD^{tree}").strip())
        self.assertEqual(dict(result.files), {
            path: hashlib.sha256((self.root / path).read_bytes()).hexdigest()
            for path in source.SOURCE_PATHS
        })
        self.assertFalse(result.execution_authority)
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), before)

    def test_wrong_repository_ref_and_events_refuse_before_git(self):
        for changes in (
            {"repository": "foreign/repository"}, {"ref": "refs/heads/main"},
            {"event": "pull_request"}, {"event": "workflow_dispatch"},
            {"event": "pull_request_target"}, {"event": "schedule"},
        ):
            with self.subTest(changes=changes), patch.object(source, "_git") as command:
                with self.assertRaisesRegex(source.HostedSourceError, "source_event_invalid"):
                    self.bind(**changes)
                command.assert_not_called()

    def test_noncanonical_sha_refuses_before_git(self):
        for value in (None, "main", "a" * 39, "A" * 40, self.sha + "\n"):
            with self.subTest(value=value), patch.object(source, "_git") as command:
                with self.assertRaisesRegex(source.HostedSourceError, "source_event_invalid"):
                    self.bind(sha=value)
                command.assert_not_called()

    def test_branch_checkout_is_not_immutable_checkout(self):
        self.git("checkout", "--quiet", "-b", "unfrozen")
        with self.assertRaisesRegex(source.HostedSourceError, "source_checkout_not_detached"):
            self.bind()

    def test_wrong_commit_refuses(self):
        with self.assertRaisesRegex(source.HostedSourceError, "source_commit_mismatch"):
            self.bind(sha="a" * 40)

    def test_nonancestor_baseline_refuses(self):
        self.git("checkout", "--quiet", "--orphan", "unrelated")
        self.git("commit", "--quiet", "-m", "unrelated")
        other = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "--quiet", "--detach", self.sha)
        with patch.object(source, "BASELINE", other):
            with self.assertRaisesRegex(source.HostedSourceError, "source_git_refused"):
                self.bind()

    def test_modified_tracked_source_refuses(self):
        (self.root / "uv.lock").write_text("changed\n")
        with self.assertRaisesRegex(source.HostedSourceError, "source_bytes_mismatch"):
            self.bind()

    def test_staged_source_refuses(self):
        (self.root / "uv.lock").write_text("changed\n")
        self.git("add", "uv.lock")
        with self.assertRaisesRegex(source.HostedSourceError, "source_checkout_dirty"):
            self.bind()

    def test_untracked_source_refuses(self):
        (self.root / "injection.py").write_text("untracked\n")
        with self.assertRaisesRegex(source.HostedSourceError, "source_checkout_dirty"):
            self.bind()

    def test_hidden_worktree_drift_is_detected_by_blob_bytes(self):
        self.git("update-index", "--assume-unchanged", "uv.lock")
        (self.root / "uv.lock").write_text("hidden change\n")
        self.assertEqual(self.git("status", "--porcelain=v1"), "")
        with self.assertRaisesRegex(source.HostedSourceError, "source_index_flags_invalid"):
            self.bind()

    def test_hidden_symlink_replacement_refuses(self):
        path = self.root / "uv.lock"
        path.unlink()
        path.symlink_to(self.root / "pyproject.toml")
        with self.assertRaisesRegex(source.HostedSourceError, "source_path_indirection"):
            self.bind()

    def test_hardlinked_source_refuses(self):
        with tempfile.TemporaryDirectory() as external:
            os.link(self.root / "uv.lock", Path(external) / "shared")
            with self.assertRaisesRegex(source.HostedSourceError, "source_file_invalid"):
                self.bind()

    def test_missing_committed_workflow_refuses(self):
        self.git("rm", "--quiet", source.WORKFLOW)
        self.git("commit", "--quiet", "-m", "missing workflow")
        self.sha = self.git("rev-parse", "HEAD").strip()
        with self.assertRaisesRegex(source.HostedSourceError, "source_blob_missing"):
            self.bind()

    def test_nested_directory_is_not_repository_root(self):
        with self.assertRaisesRegex(source.HostedSourceError, "source_root_mismatch"):
            source.bind_hosted_source(self.root / "procurement", repository=source.REPOSITORY,
                                      event="push", ref=source.REF, sha=self.sha)

    def test_ambient_git_and_credential_variables_are_not_inherited(self):
        with patch.dict(os.environ, {
            "GIT_DIR": "/not/the/repository", "GIT_WORK_TREE": "/elsewhere",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.bare",
            "GIT_CONFIG_VALUE_0": "true", "GH_TOKEN": "fabricated-test-value",
            "DATABASE_URL": "fabricated-test-value",
        }):
            result = self.bind()
        self.assertEqual(result.commit, self.sha)

    def test_checkout_drift_during_binding_refuses(self):
        original = source._file_bytes
        def change_after_read(root, relative):
            raw = original(root, relative)
            if relative == source.SOURCE_PATHS[-1]:
                (root / "injection.py").write_text("arrived during binding\n")
            return raw
        with patch.object(source, "_file_bytes", side_effect=change_after_read):
            with self.assertRaisesRegex(source.HostedSourceError, "source_checkout_dirty"):
                self.bind()

    def test_hidden_drift_outside_report_paths_refuses(self):
        path = self.root / "application.py"
        path.write_text("original\n")
        self.git("add", "application.py")
        self.git("commit", "--quiet", "-m", "application")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("update-index", "--assume-unchanged", "application.py")
        path.write_text("changed\n")
        with self.assertRaisesRegex(source.HostedSourceError, "source_index_flags_invalid"):
            self.bind()

    def test_selected_file_change_after_first_read_refuses(self):
        original = source._file_bytes
        changed = False
        def change_after_read(root, relative):
            nonlocal changed
            raw = original(root, relative)
            if relative == source.WORKFLOW and not changed:
                changed = True
                (root / relative).write_text("changed after read\n")
            return raw
        with patch.object(source, "_file_bytes", side_effect=change_after_read):
            with self.assertRaisesRegex(source.HostedSourceError, "source_bytes_mismatch"):
                self.bind()

    def test_clean_filter_is_never_executed(self):
        (self.root / ".gitattributes").write_text("uv.lock filter=probe\n")
        self.git("add", ".gitattributes")
        self.git("commit", "--quiet", "-m", "attributes")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("config", "filter.probe.clean", "touch .git/filter-marker; cat")
        self.git("config", "filter.probe.required", "true")
        os.utime(self.root / "uv.lock", None)
        self.assertEqual(self.bind().commit, self.sha)
        self.assertFalse((self.root / ".git/filter-marker").exists())

    def test_grafted_false_ancestry_refuses(self):
        baseline = self.sha
        self.git("checkout", "--quiet", "--orphan", "unrelated")
        self.git("commit", "--quiet", "-m", "unrelated")
        self.sha = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "--quiet", "--detach", self.sha)
        (self.root / ".git/info/grafts").write_text(f"{self.sha} {baseline}\n")
        with self.assertRaisesRegex(source.HostedSourceError, "source_git_layout_invalid"):
            self.bind()

    def test_executable_mode_drift_refuses_with_filemode_ignored(self):
        self.git("config", "core.filemode", "false")
        (self.root / "uv.lock").chmod(0o755)
        with self.assertRaisesRegex(source.HostedSourceError, "source_mode_mismatch"):
            self.bind()

    def test_partial_clone_missing_object_never_fetches_or_executes_remote(self):
        with tempfile.TemporaryDirectory(prefix="buffalo-source-origin-") as remote:
            remote_root = Path(remote).resolve()
            bare = remote_root / "origin.git"
            marker = remote_root / "remote-was-executed"
            self.git("clone", "--quiet", "--bare", "--no-hardlinks", str(self.root), str(bare))
            self.git("config", "remote.origin.url", str(bare))
            self.git("config", "remote.origin.promisor", "true")
            self.git("config", "extensions.partialClone", "origin")
            self.git("config", "remote.origin.uploadpack",
                     "touch " + shlex.quote(str(marker)) + "; git-upload-pack")
            tree = self.git("rev-parse", "HEAD^{tree}").strip()
            missing = self.root / ".git/objects" / tree[:2] / tree[2:]
            missing.unlink()
            with self.assertRaisesRegex(source.HostedSourceError, "source_git_refused"):
                self.bind()
            self.assertFalse(marker.exists())
            self.assertFalse(missing.exists())

    def test_git_failure_does_not_expose_output(self):
        with patch.object(source.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 2, b"fabricated-sensitive-output", b"fabricated-sensitive-output",
        )):
            with self.assertRaises(source.HostedSourceError) as caught:
                self.bind()
        self.assertEqual(str(caught.exception), "source_git_refused")


if __name__ == "__main__":
    unittest.main()
