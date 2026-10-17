"""Bounded kernel-evidence and ownership regressions; no fabricated host PASS."""
from __future__ import annotations

import copy
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_namespace as namespace


def process_stat(pid=1, parent=0, state=b"R", start=123):
    fields = [state, str(parent).encode(), b"0", b"0"] + [b"0"] * 15 + [str(start).encode(), b"0", b"0"]
    return str(pid).encode() + b" (synthetic ) name) " + b" ".join(fields) + b"\n"


def process_status(pid=1, parent=0, nspid=b"1"):
    return (f"Pid:\t{pid}\nPPid:\t{parent}\nNSpid:\t".encode() + nspid
            + b"\nUid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\nGroups:\t\nNoNewPrivs:\t1\n")


def mountinfo(number=45, options=b"rw,nosuid,nodev,noexec", optional=b"", target=b"/proc", fs=b"proc", root=b"/"):
    return (str(number).encode() + b" 20 0:25 " + root + b" " + target + b" " + options
            + optional + b" - " + fs + b" proc rw\n")


class HostedSyntheticNamespaceTests(unittest.TestCase):
    def test_pid_one_parent_zero_and_command_parentheses(self):
        self.assertEqual(namespace.parse_process_stat(process_stat(), 1),
                         dict(pid=1, state="R", parent=0, group=0, session=0, start=123))

    def test_stopped_pid_two_topology(self):
        parsed = namespace.parse_process_stat(process_stat(2, 1, b"T"), 2)
        self.assertEqual((parsed["pid"], parsed["parent"], parsed["state"]), (2, 1, "T"))

    def test_long_host_uptime_remains_valid(self):
        self.assertEqual(namespace.parse_process_stat(process_stat(start=2**48), 1)["start"], 2**48)

    def test_process_identity_mismatch_and_boolean_refuse(self):
        for selected in (True, False, 0, -1, 2, None):
            with self.subTest(selected=selected), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_process_stat(process_stat(), selected)

    def test_process_shape_truncation_and_bounds_refuse(self):
        for raw in (b"", process_stat()[:-1], b"1 (x) R 0\n", b"x"*8193, process_stat(start=2**64)):
            with self.subTest(raw=raw[:40]), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_process_stat(raw, 1)

    def test_process_noncanonical_numbers_refuse(self):
        for raw in (process_stat().replace(b"1 (", b"01 ("), process_stat(parent=-1), process_stat(start=0)):
            with self.subTest(raw=raw[:40]), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_process_stat(raw, 1)

    def test_private_and_outer_namespace_pid_views_are_distinct(self):
        self.assertEqual(namespace.parse_status(process_status())["nspid"], [1])
        outer = namespace.parse_status(process_status(100, 90, b"100\t1"))
        self.assertEqual(outer["nspid"], [100, 1])

    def test_status_duplicate_keys_refuse(self):
        with self.assertRaises(namespace.NamespaceProofError):
            namespace.parse_status(process_status() + b"Pid:\t1\n")

    def test_status_missing_and_malformed_keys_refuse(self):
        for raw in (process_status().replace(b"NoNewPrivs:\t1\n", b""), b"malformed\n", b"x"*16385):
            with self.subTest(raw=raw[:40]), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_status(raw)

    def test_status_wrong_credential_cardinality_refuses(self):
        for raw in (process_status().replace(b"Uid:\t0\t0\t0\t0", b"Uid:\t0"),
                    process_status().replace(b"NoNewPrivs:\t1", b"NoNewPrivs:\t2")):
            with self.subTest(raw=raw), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_status(raw)

    def test_status_namespace_pid_mismatch_refuses(self):
        for values in (b"2", b"0", b"", b"1 "*33, b"01"):
            with self.subTest(values=values), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_status(process_status(nspid=values))

    def test_mapping_accepts_kernel_padding(self):
        self.assertEqual(namespace.parse_id_map(b"         0       1000          1\n", 1000), [0, 1000, 1])

    def test_mapping_rejects_broad_or_different_authority(self):
        for raw in (b"0 1000 2\n", b"1 1000 1\n", b"0 1001 1\n", b"0 1000 1\n1 1001 1\n"):
            with self.subTest(raw=raw), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_id_map(raw, 1000)

    def test_mapping_requires_bounded_canonical_numbers(self):
        for raw in (b"00 1000 1\n", b"0 +1000 1\n", b"0 1000 1", b" "*129):
            with self.subTest(raw=raw), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_id_map(raw, 1000)

    def test_mapping_rejects_boolean_expected_identity(self):
        with self.assertRaises(namespace.NamespaceProofError):
            namespace.parse_id_map(b"0 1 1\n", True)

    def test_mount_selected_by_held_identity_not_first_path(self):
        result = namespace.parse_private_proc_mount(mountinfo(20) + mountinfo(45), 45)
        self.assertEqual(result["mount_id"], 45)
        self.assertEqual(result["flags"], ["nosuid", "nodev", "noexec"])

    def test_mount_missing_identity_refuses(self):
        with self.assertRaises(namespace.NamespaceProofError):
            namespace.parse_private_proc_mount(mountinfo(), 46)

    def test_mount_duplicate_identity_refuses(self):
        with self.assertRaises(namespace.NamespaceProofError):
            namespace.parse_private_proc_mount(mountinfo() * 2, 45)

    def test_mount_each_missing_security_flag_refuses(self):
        for flag in (b"nosuid", b"nodev", b"noexec"):
            with self.subTest(flag=flag), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_private_proc_mount(mountinfo().replace(b"," + flag, b""), 45)

    def test_mount_shared_master_and_unknown_propagation_refuse(self):
        for optional in (b" shared:1", b" master:2", b" propagate_from:3", b" unexpected:1"):
            with self.subTest(optional=optional), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_private_proc_mount(mountinfo(optional=optional), 45)

    def test_mount_foreign_target_filesystem_and_root_refuse(self):
        for changes in (dict(target=b"/foreign"), dict(fs=b"tmpfs"), dict(root=b"/subdir")):
            with self.subTest(changes=changes), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_private_proc_mount(mountinfo(**changes), 45)

    def test_mount_duplicate_flags_malformed_and_oversize_refuse(self):
        for raw in (mountinfo(options=b"rw,nosuid,nodev,noexec,nodev"), b"malformed\n", b"x"*(namespace._LIMIT+1)):
            with self.subTest(raw=raw[:40]), self.assertRaises(namespace.NamespaceProofError):
                namespace.parse_private_proc_mount(raw, 45)

    def test_other_mount_propagation_also_refuses(self):
        with self.assertRaises(namespace.NamespaceProofError):
            namespace.parse_private_proc_mount(mountinfo(20, target=b"/", fs=b"ext4", optional=b" shared:8") + mountinfo(), 45)

    def test_descriptor_owner_copy_cannot_close_original(self):
        read, write = namespace._pipe()
        self.addCleanup(read.close)
        self.addCleanup(write.close)
        forged = copy.copy(read)
        with self.assertRaises(namespace.NamespaceProofError):
            forged.close()
        read.verify()

    def test_descriptor_foreign_replacement_is_not_closed(self):
        read, write = namespace._pipe()
        self.addCleanup(write.close)
        number = read.number
        os.close(number)
        foreign = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        self.addCleanup(os.close, foreign)
        self.assertEqual(foreign, number)
        with self.assertRaises(namespace.NamespaceProofError):
            read.close()
        os.fstat(foreign)

    def test_descriptor_inheritability_drift_refuses(self):
        read, write = namespace._pipe()
        self.addCleanup(read.close)
        self.addCleanup(write.close)
        os.set_inheritable(read.number, True)
        try:
            with self.assertRaises(namespace.NamespaceProofError):
                read.verify()
        finally:
            os.set_inheritable(read.number, False)

    def test_partial_pipe_capture_closes_both_new_descriptors(self):
        original = namespace._Descriptor.capture
        captured = []
        def capture(number):
            captured.append(number)
            if len(captured) == 2:
                raise RuntimeError("synthetic capture refusal")
            return original(number)
        with patch.object(namespace._Descriptor, "capture", side_effect=capture):
            with self.assertRaises(RuntimeError):
                namespace._pipe()
        for number in captured:
            with self.assertRaises(OSError):
                os.fstat(number)

    def test_partial_pipe_capture_preserves_replaced_endpoint_and_closes_other(self):
        original = namespace._Descriptor.capture
        numbers = []
        def capture(number):
            numbers.append(number)
            if len(numbers) == 2:
                replacement = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
                try:
                    os.dup2(replacement, numbers[0], inheritable=False)
                finally:
                    os.close(replacement)
                raise OSError('synthetic capture failure')
            return original(number)
        try:
            with patch.object(namespace._Descriptor, 'capture', side_effect=capture):
                with self.assertRaises(namespace.NamespaceCleanupError):
                    namespace._pipe()
            self.assertEqual(os.readlink(f'/proc/self/fd/{numbers[0]}'), '/dev/null')
            with self.assertRaises(OSError):
                os.fstat(numbers[1])
        finally:
            os.close(numbers[0])

    def test_pipe_close_failure_still_attempts_other_endpoint(self):
        numbers = os.pipe2(os.O_CLOEXEC)
        close = os.close
        def refused(number):
            if number == numbers[0]:
                raise OSError('synthetic close failure')
            close(number)
        try:
            with patch.object(namespace.os, 'pipe2', return_value=numbers), \
                 patch.object(namespace._Descriptor, 'capture', side_effect=OSError('capture')), \
                 patch.object(namespace.os, 'close', side_effect=refused):
                with self.assertRaises(namespace.NamespaceCleanupError):
                    namespace._pipe()
            with self.assertRaises(OSError):
                os.fstat(numbers[1])
        finally:
            close(numbers[0])

    def test_mount_capture_failure_never_enters_legacy_fallback(self):
        filesystem = Mock(number=11)
        with patch.object(namespace, '_acquire', side_effect=[filesystem, OSError('capture')]), \
             patch.object(namespace.backend, '_browser_syscall', return_value=12), \
             patch.object(namespace.backend, '_browser_mount') as legacy:
            with self.assertRaises(OSError):
                namespace._mount_proc()
        legacy.assert_not_called()
        filesystem.close.assert_called_once()

    def test_mount_cleanup_attempts_both_owners_and_overrides_body(self):
        filesystem, mount = Mock(number=11), Mock(number=12)
        mount.close.side_effect = OSError('close')
        with patch.object(namespace, '_acquire', side_effect=[filesystem, mount]), \
             patch.object(namespace.backend, '_browser_syscall', side_effect=[None, 12, OSError('move')]):
            with self.assertRaises(namespace.NamespaceCleanupError):
                namespace._mount_proc()
        filesystem.close.assert_called_once()
        mount.close.assert_called_once()

    def test_only_fsmount_syscall_refusal_uses_explicit_legacy_fallback(self):
        filesystem = Mock(number=11)
        with patch.object(namespace, '_acquire', return_value=filesystem), \
             patch.object(namespace.backend, '_browser_syscall', side_effect=[None, PermissionError(errno.EPERM, 'fsmount')]), \
             patch.object(namespace.backend, '_browser_mount') as legacy:
            self.assertEqual(namespace._mount_proc(), {'method': 'legacy', 'fsmount_errno': errno.EPERM})
        legacy.assert_called_once_with(b'proc', b'/proc', b'proc', 14, None)
        filesystem.close.assert_called_once()

    def test_stale_pidfd_still_kills_and_reaps_owned_child_preserving_foreign_fd(self):
        child = os.fork()
        if child == 0:
            while True:
                signal.pause()
        descriptor = None
        reaped = False
        try:
            descriptor = namespace._pidfd(child)
            replacement = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.dup2(replacement, descriptor.number, inheritable=False)
            finally:
                os.close(replacement)
            with self.assertRaises(namespace.NamespaceCleanupError):
                namespace._kill_unreaped_child(child, descriptor)
            with self.assertRaises(ChildProcessError):
                os.waitpid(child, os.WNOHANG)
            reaped = True
            self.assertEqual(os.readlink(f'/proc/self/fd/{descriptor.number}'), '/dev/null')
        finally:
            if not reaped:
                try:
                    os.kill(child, signal.SIGKILL)
                    os.waitpid(child, 0)
                except ProcessLookupError:
                    pass
            if descriptor is not None:
                os.close(descriptor.number)

    def test_child_cleanup_failure_survives_protocol_hops(self):
        read, write = namespace._pipe()
        self.addCleanup(read.close)
        self.addCleanup(write.close)
        deadline = time.monotonic() + 1
        error = namespace.NamespaceCleanupError('_private_proc_cleanup:1')
        namespace._child_failure(write, 'a'*64, deadline, error)
        with self.assertRaisesRegex(namespace.NamespaceCleanupError, '^_private_proc_cleanup:1$'):
            namespace._receive(read, 'a'*64, 'CLEAN', deadline)

    def test_guardian_final_cleanup_attempts_every_owner_and_preserves_failure(self):
        owners = [Mock(number=number) for number in (11, 12, 13, 14)]
        owners[2].close.side_effect = OSError('synthetic close refusal')
        with patch.object(namespace, '_send'), patch.object(namespace, '_receive'), \
             patch.object(namespace, '_wait'), \
             patch.object(namespace.backend, '_browser_pidfd_is_terminal', return_value=True):
            with self.assertRaises(namespace.NamespaceCleanupError):
                namespace._finish_private_probe(*owners, 123, 'a'*64, time.monotonic()+1)
        for owner in owners:
            owner.close.assert_called_once()

    def test_child_cleanup_failure_overrides_successful_supervisor_fallback(self):
        root = str(Path(namespace.__file__).resolve().parent)
        program = f'''import sys,os,signal,json
sys.path.insert(0,{root!r})
import hosted_synthetic_namespace as n
def guardian(gate,status,generation,supervisor,deadline,*unused):
 n._hardening(supervisor); os.setsid()
 n._send(status,generation,'STOPPED',deadline)
 os.kill(os.getpid(),signal.SIGSTOP)
 n._receive(gate,generation,'START',deadline)
 n._send(status,generation,'USERNS',deadline)
 n._receive(gate,generation,'MAPPED',deadline)
 raise n.NamespaceCleanupError('_guardian_cleanup:1')
n._guardian=guardian
n.backend._write_browser_user_namespace_maps=lambda *args,**kwargs:None
result=n.observe_private_proc('a'*64)
n.backend._browser_prctl(36,0)
from hosted_synthetic_capability import _require_clean_supervisor
_require_clean_supervisor()
assert result['reason']=='CLEANUP_FAILED' and not result['cleanup_complete']
assert result['provider_calls']==0 and result['credential_release']=='WITHHELD'
print('cleanup failure preserved')
'''
        run = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                             env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'},
                             capture_output=True, text=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), 'cleanup failure preserved')

    def test_child_cleanup_frame_rejects_boolean_substitution(self):
        read, write = namespace._pipe()
        self.addCleanup(read.close)
        self.addCleanup(write.close)
        deadline = time.monotonic() + 1
        namespace._send(write, 'a'*64, 'REFUSED', deadline, location='_unknown:1', cleanup_failed=1)
        with self.assertRaises(namespace.NamespaceProofError) as caught:
            namespace._receive(read, 'a'*64, 'CLEAN', deadline)
        self.assertNotIsInstance(caught.exception, namespace.NamespaceCleanupError)

    def test_readable_diagnostic_runtime_images_are_non_authorizing_identities(self):
        executable, images = namespace._runtime_images()
        try:
            self.assertEqual(images['executable'], list(namespace._identity(os.stat('/proc/self/exe'))))
            self.assertEqual(set(images), {'executable', 'loader'})
            executable.verify()
        finally:
            executable.close()

    def test_replaced_executable_path_refuses_before_reading_or_executing(self):
        with patch.object(namespace.Path, 'resolve', return_value=Path('/dev/null')), \
             patch.object(namespace.os, 'pread') as read, patch.object(namespace.os, 'execve') as execute:
            with self.assertRaises(namespace.NamespaceProofError):
                namespace._runtime_images()
        read.assert_not_called()
        execute.assert_not_called()

    def test_resume_foreign_descriptor_is_not_mutated(self):
        read, write = namespace._pipe()
        self.addCleanup(write.close)
        packet = [read.number, *read.identity, read.flags]
        foreign = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.dup2(foreign, read.number, inheritable=True)
            with self.assertRaises(namespace.NamespaceProofError):
                namespace._resume_descriptor(packet)
            self.assertTrue(os.get_inheritable(read.number))
            self.assertEqual(os.readlink(f'/proc/self/fd/{read.number}'), '/dev/null')
        finally:
            os.close(foreign)
            os.close(read.number)

    def test_resume_owned_descriptor_restores_cloexec_and_rechecks_identity(self):
        read, write = namespace._pipe()
        self.addCleanup(read.close)
        self.addCleanup(write.close)
        packet = [read.number, *read.identity, read.flags]
        os.set_inheritable(read.number, True)
        resumed = namespace._resume_descriptor(packet)
        resumed.verify()
        self.assertFalse(os.get_inheritable(read.number))

    def test_resume_terminal_supervisor_refuses_after_rearming_without_child(self):
        parent = Mock(number=42)
        with patch.object(namespace, '_hardening') as harden, \
             patch.object(namespace.os, 'readlink', return_value='anon_inode:[pidfd]'), \
             patch.object(namespace, '_read', return_value=b'Pid:\t123\n'), \
             patch.object(namespace.backend, '_browser_pidfd_is_terminal', return_value=True), \
             patch.object(namespace.os, 'fork') as fork:
            with self.assertRaises(namespace.NamespaceProofError):
                namespace._resume_supervisor(parent, 123)
        harden.assert_called_once_with(123)
        parent.verify.assert_called_once()
        fork.assert_not_called()

    def test_resume_signal_mask_or_sigchld_drift_refuses(self):
        expected = sorted(int(item) for item in namespace.backend._BROWSER_PROJECTION_MASKED_SIGNALS)
        with patch.object(namespace.signal, 'pthread_sigmask', return_value=set(expected)), \
             patch.object(namespace.signal, 'getsignal', return_value=signal.SIG_DFL):
            namespace._resume_signal_state(expected)
            for changed in ([], expected + expected, [True]):
                with self.assertRaises(namespace.NamespaceProofError):
                    namespace._resume_signal_state(changed)
            with patch.object(namespace.signal, 'getsignal', return_value=signal.SIG_IGN):
                with self.assertRaises(namespace.NamespaceProofError):
                    namespace._resume_signal_state(expected)

    def test_resume_namespace_drift_or_missing_transition_refuses(self):
        original = {name: [4, index + 1] for index, name in enumerate(namespace._NAMESPACES)}
        expected = {name: [4, index + 10] for index, name in enumerate(namespace._NAMESPACES)}
        expected['pid'] = original['pid']
        with patch.object(namespace, '_namespaces', return_value=expected), \
             patch.object(namespace.os, 'stat', side_effect=FileNotFoundError()):
            namespace._resume_namespaces(original, expected)
            for name in namespace._NAMESPACES:
                changed = dict(expected, **{name: [4, 800]})
                with self.assertRaises(namespace.NamespaceProofError):
                    namespace._resume_namespaces(original, changed)
            with self.assertRaises(namespace.NamespaceProofError):
                namespace._resume_namespaces(expected, expected)
            with patch.object(namespace.os, 'stat', return_value=Mock()):
                with self.assertRaisesRegex(namespace.NamespaceProofError, 'pid_namespace_not_empty'):
                    namespace._resume_namespaces(original, expected)

    def test_reexec_carries_only_owned_protocol_bootstrap_and_null_stdio(self):
        root = str(Path(namespace.__file__).resolve().parent)
        program = f'''import sys,os,json,fcntl,time
from unittest.mock import patch
sys.path.insert(0,{root!r})
import hosted_synthetic_namespace as n
gate,unused=n._pipe(); other,status=n._pipe()
unused.close(); other.close()
parent=n._pidfd(os.getppid())
n.backend._normalize_browser_guardian_stdio()
def execute(image,arguments,environment):
 assert isinstance(image,int) and not os.get_inheritable(image)
 bootstrap=int(arguments[-1])
 assert fcntl.fcntl(bootstrap,fcntl.F_GET_SEALS)==15
 packet=json.loads(os.pread(bootstrap,8192,0))
 carried={{0,1,2,bootstrap,gate.number,status.number,parent.number}}
 for name in os.listdir('/proc/self/fd'):
  number=int(name)
  try: os.fstat(number)
  except OSError: continue
  assert os.get_inheritable(number)==(number in carried)
 assert packet['descriptors']==[[item.number,*item.identity,item.flags] for item in (gate,status,parent)]
 assert environment=={{'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'}}
 os._exit(0)
with patch.object(n.os,'execve',side_effect=execute):
 n._reexec_guardian(gate,status,parent,'a'*64,os.getppid(),time.monotonic()+5,os.getuid(),os.getgid(),n._namespaces('self'))
'''
        run = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                             env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'},
                             capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 0)

    def test_diagnostic_resume_requires_owned_sealed_bootstrap(self):
        run = subprocess.run([sys.executable, '-I', '-S', '-B', namespace.__file__, '--resume-diagnostic', '0'],
                             env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'}, capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stdout + run.stderr, b'')

    def test_new_allocation_capture_failure_closes_owned_fd(self):
        raw = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        with patch.object(namespace._Descriptor, "capture", side_effect=OSError("capture")):
            with self.assertRaises(OSError):
                namespace._acquire(lambda: raw)
        with self.assertRaises(OSError):
            os.fstat(raw)

    def test_new_allocation_initial_fstat_failure_closes_raw_owner(self):
        raw = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        with patch.object(namespace.os, "fstat", side_effect=OSError("fstat")):
            with self.assertRaises(OSError):
                namespace._acquire(lambda: raw)
        with self.assertRaises(OSError):
            os.fstat(raw)

    def test_new_allocation_fcntl_failure_closes_bound_owner(self):
        raw = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        with patch.object(namespace.fcntl, "fcntl", side_effect=OSError("fcntl")):
            with self.assertRaises(OSError):
                namespace._acquire(lambda: raw)
        with self.assertRaises(OSError):
            os.fstat(raw)

    def test_new_allocation_failure_preserves_foreign_replacement(self):
        raw = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
        owned_read, owned_write = os.pipe2(os.O_CLOEXEC)
        self.addCleanup(os.close, owned_read)
        self.addCleanup(os.close, owned_write)
        def replace(number):
            os.dup2(owned_read, number, inheritable=False)
            raise RuntimeError("changed")
        try:
            with patch.object(namespace._Descriptor, "capture", side_effect=replace):
                with self.assertRaisesRegex(namespace.NamespaceProofError, "cleanup_refused"):
                    namespace._acquire(lambda: raw)
            self.assertEqual(os.fstat(raw).st_ino, os.fstat(owned_read).st_ino)
        finally:
            os.close(raw)

    def _prearm_failure(self, transition):
        root = str(Path(namespace.__file__).resolve().parent)
        program = f'''import sys,os,time,signal
from unittest.mock import patch
sys.path.insert(0,{root!r})
import hosted_synthetic_namespace as n
own=n._pidfd(os.getpid()); parent=n._pidfd(os.getppid())
ir,iw=n._pipe(); rr,rw=n._pipe()
created=[]
fork=os.fork
def capture_fork():
 child=fork()
 if child>0: created.append(child)
 return child
def idle(*args):
 while True: signal.pause()
def refused(*args,**kwargs): raise OSError("synthetic transition refusal")
transition={transition!r}
target,attribute={{"inner_close":(ir,"close"),"result_close":(rw,"close"),"pidfd":(n,"_pidfd"),"parent_verify":(parent,"verify"),"armed":(n.backend,"_read_browser_guardian_armed_and_rearm")}}[transition]
with patch.object(n,"_init",side_effect=idle), patch.object(n.os,"fork",side_effect=capture_fork), patch.object(target,attribute,side_effect=refused):
 try:
  n._start_init(own,parent,ir,iw,rr,rw,"a"*64,os.getppid(),time.monotonic()+2.0,os.getuid(),os.getgid(),{{}})
 except OSError: pass
 else: raise AssertionError("transition unexpectedly accepted")
assert len(created)==1
try: os.waitpid(created[0],os.WNOHANG)
except ChildProcessError: pass
else: raise AssertionError("owned child not reaped")
for owned in (own,parent,ir,iw,rr,rw): owned.close()
print("owned pre-ARMED child reaped")
'''
        run = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program],
                             env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                             capture_output=True, text=True, timeout=8, check=True)
        self.assertEqual(run.stdout.strip(), "owned pre-ARMED child reaped")

    def test_init_inner_pipe_close_failure_kills_and_reaps_before_rearm(self):
        self._prearm_failure("inner_close")

    def test_init_result_pipe_close_failure_kills_and_reaps_before_rearm(self):
        self._prearm_failure("result_close")

    def test_init_pidfd_acquisition_failure_retains_raw_child_ownership(self):
        self._prearm_failure("pidfd")

    def test_init_parent_verification_failure_kills_and_reaps(self):
        self._prearm_failure("parent_verify")

    def test_init_armed_handshake_failure_kills_and_reaps(self):
        self._prearm_failure("armed")

    def _supervisor_death(self, armed):
        root = str(Path(namespace.__file__).resolve().parent)
        program = r'''import os,sys,signal,time,selectors
from unittest.mock import patch
sys.path.insert(0,ROOT)
import hosted_synthetic_namespace as n
n.backend._browser_prctl(36,1)
r,w=os.pipe2(os.O_CLOEXEC)
outer=os.getpid()
supervisor=os.fork()
if supervisor==0:
 n._hardening(outer)
 guardian=os.fork()
 if guardian==0:
  os.setsid(); n._hardening(os.getppid())
  parent_pid=os.getppid()
  own=n._pidfd(os.getpid()); parent=n._pidfd(parent_pid)
  ir,iw=n._pipe(); rr,rw=n._pipe()
  def init(gate,status,guardian_fd,generation,deadline,*unused):
   if ARMED:
    n._hardening(os.getppid())
    n._send(status,generation,"ARMED",deadline)
   else:
    os.write(w,f"{os.getppid()} {os.getpid()}\n".encode())
   while True: signal.pause()
  try:
   with patch.object(n,"_init",side_effect=init):
    child,fd=n._start_init(own,parent,ir,iw,rr,rw,"a"*64,parent_pid,time.monotonic()+5,os.getuid(),os.getgid(),{})
   os.write(w,f"{os.getpid()} {child}\n".encode())
   while True: signal.pause()
  except BaseException:
   os._exit(1)
 while True: signal.pause()
os.close(w)
pids=[]; descriptors=[]; reaped=set()
try:
 with selectors.DefaultSelector() as selector:
  selector.register(r,selectors.EVENT_READ)
  assert selector.select(3),"missing transition"
 pids=[int(value) for value in os.read(r,128).split()]
 assert len(pids)==2
 descriptors=[os.pidfd_open(pid,0) for pid in pids]
 os.kill(supervisor,signal.SIGKILL)
 deadline=time.monotonic()+4
 while len(reaped)<3 and time.monotonic()<deadline:
  try: pid,status=os.waitpid(-1,os.WNOHANG)
  except ChildProcessError: break
  if pid: reaped.add(pid)
  else: time.sleep(.005)
 expected={supervisor,*pids} if AFTER_ARMED else {supervisor,pids[0]}
 assert reaped==expected,"owned descendants not reaped"
 assert all(n.backend._browser_pidfd_is_terminal(fd) for fd in descriptors)
 print("supervisor death cleaned")
finally:
 for pid in [supervisor,*pids]:
  if pid in reaped: continue
  try: os.kill(pid,signal.SIGKILL)
  except ProcessLookupError: pass
 for fd in descriptors: os.close(fd)
 os.close(r)
'''.replace('ROOT', repr(root)).replace('ARMED', repr(armed), 1).replace('AFTER_ARMED', repr(armed))
        # Replace only the conditional token, preserving the protocol stage.
        run = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                             env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'},
                             capture_output=True, text=True, timeout=9)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), 'supervisor death cleaned')

    def test_supervisor_death_before_init_armed_kills_and_reaps_init(self):
        self._supervisor_death(False)

    def test_supervisor_death_after_init_armed_kills_and_reaps_both(self):
        self._supervisor_death(True)

    def test_stale_generation_frame_refuses(self):
        read, write = namespace._pipe()
        self.addCleanup(read.close)
        self.addCleanup(write.close)
        deadline = time.monotonic() + 1.0
        namespace._send(write, "a"*64, "CLOSE", deadline)
        with self.assertRaises(namespace.NamespaceProofError):
            namespace._receive(read, "b"*64, "CLOSE", deadline)

    def test_extra_frame_authority_field_refuses(self):
        read, write = namespace._pipe()
        self.addCleanup(read.close)
        self.addCleanup(write.close)
        deadline = time.monotonic() + 1.0
        namespace._send(write, "a"*64, "CLOSE", deadline, authority=True)
        with self.assertRaises(namespace.NamespaceProofError):
            namespace._receive(read, "a"*64, "CLOSE", deadline)

    def test_namespace_permission_refusal_is_precise_and_bounded(self):
        with patch.object(namespace.os, "stat", side_effect=PermissionError(errno.EACCES, "synthetic-private-value")):
            with self.assertRaisesRegex(namespace.NamespaceProofError, r"^_namespace_sentinel_user:13$"):
                namespace._namespaces("2")

    def test_failure_diagnostic_never_echoes_arbitrary_exception(self):
        self.assertEqual(namespace._failure_location(RuntimeError("synthetic-private-value")), "_unknown:1")

    def _isolated_refusal(self, mutation):
        root = str(Path(namespace.__file__).resolve().parent)
        program = f'''import sys,os,signal,json,ctypes
sys.path.insert(0,{root!r})
import hosted_synthetic_namespace as n
before=ctypes.c_int()
assert n.backend._browser_libc().prctl(37,ctypes.byref(before),0,0,0)==0
mask=signal.pthread_sigmask(signal.SIG_BLOCK, [])
{mutation}
result=n.observe_private_proc("a"*64)
after=ctypes.c_int()
assert n.backend._browser_libc().prctl(37,ctypes.byref(after),0,0,0)==0
assert before.value==after.value
assert signal.pthread_sigmask(signal.SIG_BLOCK, [])==mask
n.__dict__["_require"](result["cleanup_complete"])
print(json.dumps(result))
'''
        run = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", program],
                             env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                             capture_output=True, text=True, timeout=5, check=True)
        result = json.loads(run.stdout)
        self.assertEqual(result["HOST_CAPABILITY"], "FAIL")
        self.assertFalse(result["execution_authority"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["payload_processes"], 0)
        self.assertEqual(result["credential_release"], "WITHHELD")
        return result

    def test_partial_start_failure_restores_subreaper_signals_and_descriptors(self):
        result = self._isolated_refusal('def refused(): raise OSError("synthetic fork refusal")\nn.os.fork=refused')
        self.assertEqual(result["reason"], "NAMESPACE_PROOF_REFUSED")

    def test_actual_sigint_waits_for_owned_cleanup(self):
        result = self._isolated_refusal('def cancelled():\n os.kill(os.getpid(),signal.SIGINT)\n raise OSError("synthetic fork refusal")\nn.os.fork=cancelled')
        self.assertEqual(result["reason"], "CANCELLED")

    def test_invalid_generation_has_no_process_side_effect(self):
        with patch("hosted_synthetic_capability._require_clean_supervisor"), patch.object(namespace.os, "fork") as fork:
            for value in (None, "", "x"*64, "a"*63, "A"*64):
                with self.subTest(value=value), self.assertRaises(namespace.NamespaceProofError):
                    namespace.observe_private_proc(value)
            fork.assert_not_called()

    def test_environment_refusal_precedes_namespace_or_fork(self):
        with patch("hosted_synthetic_capability._require_clean_supervisor", side_effect=RuntimeError("refused")), patch.object(namespace.os, "fork") as fork:
            with self.assertRaises(RuntimeError):
                namespace.observe_private_proc("a"*64)
            fork.assert_not_called()


if __name__ == "__main__":
    unittest.main()
