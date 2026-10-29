"""Portable adversarial source-binding tests; no Linux capability claim."""

from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
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
            # Git maintenance --auto --detach daemonizes before deciding that
            # tiny fixtures need no work. Non-reaping container PID1 would
            # retain that grandchild; keep fixture commits fully synchronous.
            ["git", "-c", "maintenance.auto=false", "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *arguments],
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

    def test_shared_deadline_refuses_before_any_git_process(self):
        with patch.object(source.time,'monotonic',return_value=10),patch.object(source.subprocess,'Popen') as process:
            with self.assertRaisesRegex(source.HostedSourceError,'source_shared_deadline'):
                self.bind(operation_deadline=9)
            with self.assertRaisesRegex(source.HostedSourceError,'source_shared_deadline'):
                source._git(self.root,'rev-parse','HEAD',operation_deadline=9)
        process.assert_not_called()

    def test_source_drift_pass_does_not_reset_shared_deadline(self):
        clock=[0];original=source._file_bytes
        def read(*args):
            result=original(*args);clock[0]=11;return result
        with patch.object(source.time,'monotonic',side_effect=lambda:clock[0]),patch.object(source,'_file_bytes',side_effect=read):
            with self.assertRaisesRegex(source.HostedSourceError,'source_shared_deadline'):
                self.bind(operation_deadline=10)
        self.assertIsNone(source._SOURCE_DEADLINE.get())

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
        def change_after_read(root, relative, mode):
            raw = original(root, relative, mode)
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
        def change_after_read(root, relative, mode):
            nonlocal changed
            raw = original(root, relative, mode)
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
        with self.synthetic_git("import sys; print('fabricated-sensitive-output'); sys.exit(2)"):
            with self.assertRaises(source.HostedSourceError) as caught:
                self.bind()
        self.assertEqual(str(caught.exception), "source_git_refused")

    def synthetic_git(self, program, after_spawn=None):
        original = subprocess.Popen
        self.children = []
        def spawn(command, **kwargs):
            self.assertNotIn("GH_TOKEN", kwargs["env"])
            self.assertNotIn("DATABASE_URL", kwargs["env"])
            child = original([sys.executable, "-I", "-c", program], **kwargs)
            self.children.append(child)
            if after_spawn is not None:
                after_spawn()
            return child
        return patch.object(source.subprocess, "Popen", side_effect=spawn)

    def assert_children_reaped(self):
        self.assertEqual(len(self.children), 1)
        child = self.children[0]
        self.assertIsNotNone(child.returncode)
        self.assertTrue(child.stdout.closed)
        with self.assertRaises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)

    def test_git_output_overflow_is_bounded_and_child_reaped(self):
        original_read = os.read
        read_sizes = []
        def measured_read(fd, count):
            block = original_read(fd, count)
            read_sizes.append(len(block))
            return block
        program = "import os; block=b'x'*65536\nwhile True: os.write(1, block)"
        with self.synthetic_git(program), patch.object(source, "_MAX_SOURCE_BYTES", 4096):
            # Popen also reads its private exec-error pipe: count only output
            # after successful spawn by wrapping the selector loop's reads.
            with patch.object(source.os, "read", side_effect=measured_read):
                with self.assertRaisesRegex(source.HostedSourceError, "source_git_output_limit"):
                    source._git(self.root, "rev-parse", "HEAD")
        self.assertEqual(sum(read_sizes), 4097)
        self.assert_children_reaped()

    def test_git_output_at_limit_is_accepted(self):
        with self.synthetic_git("import os; os.write(1, b'x'*4096)"), patch.object(
                source, "_MAX_SOURCE_BYTES", 4096):
            self.assertEqual(source._git(self.root, "rev-parse", "HEAD"), b"x" * 4096)
        self.assert_children_reaped()

    def test_git_output_timeout_reaps_child(self):
        with self.synthetic_git("import time; time.sleep(60)"), patch.object(
                source, "_GIT_TIMEOUT_SECONDS", 0.2):
            with self.assertRaisesRegex(source.HostedSourceError, "source_git_unavailable"):
                source._git(self.root, "rev-parse", "HEAD")
        self.assert_children_reaped()

    def test_git_closed_output_cannot_bypass_exit_deadline(self):
        with self.synthetic_git("import os,time; os.close(1); time.sleep(60)"), patch.object(
                source, "_GIT_TIMEOUT_SECONDS", 0.2):
            with self.assertRaisesRegex(source.HostedSourceError, "source_git_unavailable"):
                source._git(self.root, "rev-parse", "HEAD")
        self.assert_children_reaped()

    def test_ancestor_symlink_swap_before_open_refuses_without_descriptor_leak(self):
        original_open = os.open
        moved = False
        def replace_before_open(name, flags, **kwargs):
            nonlocal moved
            if name == "procurement" and kwargs.get("dir_fd") is not None and not moved:
                moved = True
                (self.root / "procurement").rename(self.root / "moved")
                (self.root / "procurement").symlink_to(self.root / "moved", target_is_directory=True)
            return original_open(name, flags, **kwargs)
        before = len(os.listdir("/proc/self/fd"))
        with patch.object(source.os, "open", side_effect=replace_before_open):
            with self.assertRaisesRegex(source.HostedSourceError, "source_file_unavailable"):
                source._file_bytes(self.root, "procurement/pyproject.toml", b"100644")
        self.assertTrue(moved)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_held_ancestor_replacement_after_read_refuses(self):
        original_read = os.read
        moved = False
        def replace_after_read(fd, count):
            nonlocal moved
            raw = original_read(fd, count)
            if raw and not moved:
                moved = True
                (self.root / "procurement").rename(self.root / "moved")
                (self.root / "procurement").mkdir()
            return raw
        before = len(os.listdir("/proc/self/fd"))
        with patch.object(source.os, "read", side_effect=replace_after_read):
            with self.assertRaisesRegex(source.HostedSourceError, "source_path_changed"):
                source._file_bytes(self.root, "procurement/pyproject.toml", b"100644")
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_mode_change_during_read_refuses_on_held_file(self):
        original_read = os.read
        def change_mode(fd, count):
            raw = original_read(fd, count)
            os.fchmod(fd, 0o755)
            return raw
        with patch.object(source.os, "read", side_effect=change_mode):
            with self.assertRaisesRegex(source.HostedSourceError, "source_file_changed"):
                source._file_bytes(self.root, "uv.lock", b"100644")

    def signal_at_line(self, function, text, call):
        lines, start = inspect.getsourcelines(function)
        target = start + next(i for i, line in enumerate(lines) if line.strip() == text)
        delivered = False
        previous_trace = sys.gettrace()
        def trace(frame, event, arg):
            nonlocal delivered
            if (not delivered and event == "line" and frame.f_code == function.__code__
                    and frame.f_lineno == target):
                delivered = True
                os.kill(os.getpid(), signal.SIGINT)
            return trace
        before = len(os.listdir("/proc/self/fd"))
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            sys.settrace(trace)
            with self.assertRaises(KeyboardInterrupt):
                call()
        finally:
            sys.settrace(previous_trace)
        self.assertTrue(delivered)
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), previous_mask)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_sigint_during_initial_descriptor_ownership_is_deferred(self):
        self.signal_at_line(source._held_source_file.__wrapped__, "descriptors.append(parent)",
                            lambda: source._file_bytes(self.root, "uv.lock", b"100644"))

    def test_sigint_during_inner_descriptor_ownership_is_deferred(self):
        self.signal_at_line(source._held_source_file.__wrapped__, "descriptors.append(descriptor)",
                            lambda: source._file_bytes(self.root, "uv.lock", b"100644"))

    def test_sigint_during_descriptor_cleanup_is_deferred(self):
        self.signal_at_line(source._held_source_file.__wrapped__, "os.close(descriptor)",
                            lambda: source._file_bytes(self.root, "uv.lock", b"100644"))

    def test_sigint_during_git_process_ownership_reaps_child(self):
        before = len(os.listdir("/proc/self/fd"))
        with self.synthetic_git("import os; os.write(1, b'x')",
                                after_spawn=lambda: os.kill(os.getpid(), signal.SIGINT)):
            with self.assertRaises(KeyboardInterrupt):
                source._git(self.root, "rev-parse", "HEAD")
        self.assert_children_reaped()
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_cleanup_failure_takes_precedence_over_deferred_sigint(self):
        original_close = os.close
        injected = False
        def failing_close(fd):
            nonlocal injected
            original_close(fd)
            if not injected:
                injected = True
                os.kill(os.getpid(), signal.SIGINT)
                raise OSError("synthetic cleanup failure")
        before = len(os.listdir("/proc/self/fd"))
        with patch.object(source.os, "close", side_effect=failing_close):
            with self.assertRaisesRegex(source.HostedSourceError, "source_file_cleanup_failed"):
                source._file_bytes(self.root, "uv.lock", b"100644")
        self.assertTrue(injected)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_multithreaded_observer_refuses_before_resource_creation(self):
        release = threading.Event()
        other = threading.Thread(target=release.wait)
        other.start()
        try:
            with patch.object(source.subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(source.HostedSourceError, "source_signal_boundary_invalid"):
                    source._git(self.root, "rev-parse", "HEAD")
                spawn.assert_not_called()
        finally:
            release.set()
            other.join(timeout=2)
        self.assertFalse(other.is_alive())


if __name__ == "__main__":
    unittest.main()
