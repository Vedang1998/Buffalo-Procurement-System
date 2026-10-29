"""Deterministic kernel-boundary simulations, never live delegation evidence."""
from pathlib import Path
import copy
import json
from types import SimpleNamespace
import os
import signal
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_cgroup as cg
import hosted_synthetic_namespace as ns


EVENTS = b'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n'
GENERATION = 'a' * 64
MEMORY = 64 * 1024**2


class HostedCgroupParsingTests(unittest.TestCase):
    def test_real_subreaper_and_kernel_sigchld_flags_guard_numeric_migration(self):
        program=r'''
import ctypes,os,signal,sys
sys.path.insert(0,sys.argv[1])
import hosted_synthetic_cgroup as c
c.backend._block_browser_projection_signals()
c.backend._browser_prctl(36,1)
c._require_unreaped_lifecycle()
c.backend._browser_prctl(36,0)
try:c._require_unreaped_lifecycle();raise AssertionError('subreaper0 accepted')
except c.CgroupProofError:pass
c.backend._browser_prctl(36,1)
class Action(ctypes.Structure):
 _fields_=[('handler',ctypes.c_void_p),('mask',ctypes.c_ulong*16),('flags',ctypes.c_int),('restorer',ctypes.c_void_p)]
libc=ctypes.CDLL(None,use_errno=True)
for handler,flags in ((None,2),(1,0)):
 action=Action();action.handler=handler;action.flags=flags
 assert libc.sigaction(signal.SIGCHLD,ctypes.byref(action),None)==0
 try:c._require_unreaped_lifecycle();raise AssertionError('auto-reaping accepted')
 except c.CgroupProofError:pass
print('subreaper and kernel autoreap guards verified; no migration')
'''
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',program,str(Path(cg.__file__).parent)],
                              capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertIn(b'guards verified',result.stdout)

    def test_finite_canonical_values(self):
        self.assertEqual(cg.parse_scalar(b'0\n'), 0)
        self.assertEqual(cg.parse_scalar(b'18446744073709551615\n'), 2**64 - 1)

    def test_max_negative_overflow_whitespace_and_truncation_refuse(self):
        for raw in (b'max\n', b'-1\n', b'01\n', b'1', b'1 \n', b'+1\n', b'1\n\n',
                    b'18446744073709551616\n', True, b''):
            with self.subTest(raw=raw), self.assertRaises(cg.CgroupProofError):
                cg.parse_scalar(raw)

    def test_complete_memory_counters(self):
        expected = {key: 0 for key in cg._MEMORY_KEYS}
        self.assertEqual(cg.parse_memory_events(EVENTS), expected)
        cg.require_zero_oom_delta(expected, dict(expected, high=2, max=3))

    def test_socket_pressure_counter_is_optional_retained_and_zero(self):
        old=cg.parse_memory_events(EVENTS)
        value=cg.parse_memory_events(EVENTS+b'sock_throttled 0\n')
        self.assertEqual(value,dict(old,sock_throttled=0))
        self.assertEqual(cg.MEMORY_EVENTS_SCHEMA,'BUFFALO_MEMORY_EVENTS_SIX_PLUS_ZERO_SOCKET_V2')
        cg.require_zero_oom_delta(value,dict(value,high=1))
        pressure=cg.parse_memory_events(EVENTS+b'sock_throttled 1\n')
        self.assertEqual(pressure['sock_throttled'],1)
        with self.assertRaises(cg.CgroupSocketPressureError):cg._require_zero_socket(pressure)
        with self.assertRaises(cg.CgroupSocketPressureError):
            cg._memory_observation(lambda name:{'memory.current':b'0\n','memory.peak':b'0\n',
                'memory.events':EVENTS+b'sock_throttled 1\n'}[name],1024)
        for raw in (EVENTS+b'sock_throttled 00\n',
                    EVENTS+b'sock_throttled 18446744073709551616\n',
                    EVENTS+b'sock_throttled 0\nsock_throttled 0\n',
                    EVENTS.replace(b'oom 0\n',b'')+b'sock_throttled 0\n',
                    EVENTS+b'sock_throttled 0\nunknown_safety_event 0\n'):
            with self.subTest(raw=raw),self.assertRaises(cg.CgroupProofError):cg.parse_memory_events(raw)
        for before,after in ((old,value),(value,old),(value,dict(value,sock_throttled=1)),
                             (dict(value,sock_throttled=True),value)):
            with self.subTest(before=before,after=after),self.assertRaises(cg.CgroupProofError):cg.require_zero_oom_delta(before,after)

    def test_malformed_counters_refuse(self):
        for raw in (EVENTS[:-1], EVENTS + b'oom 0\n', EVENTS.replace(b'oom 0\n', b''),
                    EVENTS + b'new 0\n', EVENTS.replace(b'high 0', b'high  0'),
                    EVENTS.replace(b'oom 0', b'oom -1'), b'x'*4097):
            with self.subTest(raw=raw), self.assertRaises(cg.CgroupProofError):
                cg.parse_memory_events(raw)

    def test_oom_increment_or_any_decrease_refuses(self):
        before = {key: 1 for key in cg._MEMORY_KEYS}
        for key in cg._MEMORY_KEYS:
            with self.subTest(key=key), self.assertRaises(cg.CgroupProofError):
                cg.require_zero_oom_delta(before, dict(before, **{key: 0}))
        for key in ('oom', 'oom_kill', 'oom_group_kill'):
            with self.subTest(key=key), self.assertRaises(cg.CgroupProofError):
                cg.require_zero_oom_delta(before, dict(before, **{key: 2}))

    def test_boolean_or_missing_baseline_refuses(self):
        before = {key: 0 for key in cg._MEMORY_KEYS}
        for value in (None, {}, dict(before, oom=True), dict(before, oom=-1)):
            with self.subTest(value=value), self.assertRaises(cg.CgroupProofError):
                cg.require_zero_oom_delta(value, before)

    def test_real_ordinary_filesystem_cannot_masquerade_as_cgroup(self):
        with tempfile.TemporaryDirectory() as root:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(cg.CgroupProofError):
                    cg._filesystem(fd)
            finally:
                os.close(fd)

    def test_ordinary_constructor_is_inert(self):
        with self.assertRaises(cg.CgroupProofError):
            cg.CgroupLease()


class HostedCgroupLeaseTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('hosted_synthetic_launcher.inspect_host_identity', return_value={}))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory = self.root / 'delegated'
        self.directory.mkdir(mode=0o700)
        self.files(self.directory, {'cgroup.type': b'domain\n', 'cgroup.procs': b'',
                                   'cgroup.threads': b'', 'cgroup.subtree_control': b'memory pids\n',
                                   'memory.max': b'536870912\n', 'memory.current': b'4096\n',
                                   'memory.peak': b'8192\n', 'memory.events': EVENTS, 'pids.max': b'64\n'})
        self.real_mkdir = os.mkdir
        self.real_rmdir = os.rmdir
        real_pwrite = os.pwrite
        def kernel_write(fd, value, offset):
            count = real_pwrite(fd, value, offset)
            os.ftruncate(fd, len(value))
            return count
        self.enterContext(patch.object(cg.os, 'pwrite', side_effect=kernel_write))
        self.enterContext(patch.object(cg, '_open_directory', side_effect=lambda path:
            ns._acquire(lambda: os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))))
        self.enterContext(patch.object(cg, '_filesystem'))
        self.enterContext(patch.object(cg, '_signal_boundary'))
        self.enterContext(patch.object(cg.os, 'mkdir', side_effect=self.mkdir))
        self.enterContext(patch.object(cg.os, 'rmdir', side_effect=self.rmdir))
        self.lease = None
        self.addCleanup(self.cleanup)

    @staticmethod
    def files(directory, values):
        for name, value in values.items():
            (directory / name).write_bytes(value)

    def mkdir(self, name, mode=0o777, *, dir_fd=None):
        self.real_mkdir(name, mode, dir_fd=dir_fd)
        path = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name
        self.files(path, {'cgroup.type': b'domain\n', 'cgroup.procs': b'', 'cgroup.threads': b'',
            'cgroup.events': b'populated 0\nfrozen 0\n', 'cgroup.subtree_control': b'',
            'memory.max': b'max\n', 'memory.oom.group': b'0\n', 'memory.events': EVENTS,
            'memory.current': b'0\n', 'memory.peak': b'0\n',
            'pids.max': b'max\n'})

    def rmdir(self, name, *, dir_fd=None):
        path = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name
        for entry in path.iterdir():
            entry.unlink()
        self.real_rmdir(name, dir_fd=dir_fd)

    def create(self):
        self.lease = cg.CgroupLease.create(self.directory, generation=GENERATION, memory_max=MEMORY)
        self.lease.path = "/delegated/" + self.lease.name
        return self.lease

    def cleanup(self):
        if self.lease is not None and not self.lease.closed:
            try:
                self.lease.close()
            except cg.CgroupCleanupError:
                pass  # a test asserting refusal owns its fabricated temp tree

    def change(self, **values):
        self.files(self.directory / ('buffalo-' + GENERATION), values)

    def test_creation_configures_exact_limits_and_cleanup(self):
        lease = self.create()
        evidence = lease.attest(())
        self.assertEqual((evidence['memory_max'], evidence['pids_max'], evidence['memory_oom_group']), (MEMORY, 2, 1))
        self.assertFalse(evidence['execution_authority'])
        lease.close()
        lease.close()
        self.assertFalse((self.directory / lease.name).exists())

    def test_outer_and_child_charges_are_separate_and_bounded(self):
        lease=self.create()
        self.files(self.directory,{'memory.current':b'16384\n','memory.peak':b'32768\n'})
        self.change(**{'memory.current':b'4096\n','memory.peak':b'8192\n'})
        result=lease.attest(())
        self.assertEqual(result['enclosing_service_memory']['peak'],32768)
        self.assertEqual(result['child_memory']['peak'],8192)
        self.assertEqual(result['charge_scope'],'CHILD_POST_MIGRATION_AND_ENCLOSING_SERVICE')
        self.assertEqual(result['child_memory']['events_schema'],cg.MEMORY_EVENTS_SCHEMA)
        lease.close()
        self.assertTrue(lease.final_resources['generation_child_removed'])
        self.assertFalse(lease.final_resources['full_service_lifetime_observed'])
        self.assertEqual(lease.final_resources['enclosing_service_memory']['peak'],32768)

    def test_unrelated_write_permission_on_any_control_refuses(self):
        for name in ('cgroup.procs','cgroup.subtree_control','memory.max','memory.events'):
            lease=self.create();path=self.directory/lease.name/name
            with self.subTest(name=name):
                path.chmod(0o666)
                with self.assertRaises(cg.CgroupProofError):lease.attest(())
                path.chmod(0o644);lease.close()

    def test_outer_drift_during_child_removal_cannot_pass_cleanup(self):
        for name,value in (('memory.events',EVENTS.replace(b'oom 0',b'oom 1')),
                           ('memory.peak',b'536870913\n'),('pids.max',b'65\n')):
            with self.subTest(name=name):
                lease=self.create();old=(self.directory/name).read_bytes()
                def remove_then_drift(child, *, dir_fd=None):
                    self.rmdir(child,dir_fd=dir_fd)
                    (self.directory/name).write_bytes(value)
                with patch.object(cg.os,'rmdir',side_effect=remove_then_drift), \
                     self.assertRaises(cg.CgroupCleanupError):
                    lease.close()
                self.assertFalse((self.directory/lease.name).exists())
                self.assertTrue(lease.closed and lease.cleanup_failed)
                self.assertTrue(all(fd.number==-1 for fd in (*lease.controls.values(),lease.child,lease.parent)))
                with self.assertRaises(cg.CgroupCleanupError):lease.close()
                (self.directory/name).write_bytes(old)

    def test_outer_oom_limit_and_peak_drift_refuse_and_cleanup_stays_failed(self):
        for name,value in (('memory.events',EVENTS.replace(b'oom 0',b'oom 1')),
                           ('memory.max',b'1073741824\n'),('memory.peak',b'536870913\n'),
                           ('memory.peak',b'4096\n'),('memory.current',b'16384\n')):
            with self.subTest(name=name,value=value):
                lease=self.create();old=(self.directory/name).read_bytes()
                (self.directory/name).write_bytes(value)
                with self.assertRaises(cg.CgroupProofError):lease.attest(())
                with self.assertRaises(cg.CgroupCleanupError):lease.close()
                self.assertTrue(lease.closed and lease.cleanup_failed)
                self.assertFalse((self.directory/lease.name).exists())
                (self.directory/name).write_bytes(old)

    def test_failed_boundary_and_close_preserve_first_error_and_close_other_fds(self):
        lease=self.create();original=cg.CgroupProofError('boundary')
        selected=lease.controls['cgroup.type'];close=selected.close
        def failing_close():
            close();raise OSError('secondary close failure')
        with patch.object(cg,'_signal_boundary',side_effect=original),patch.object(selected,'close',side_effect=failing_close), \
             patch.object(cg.os,'rmdir') as remove,self.assertRaises(cg.CgroupCleanupError) as caught:
            lease.close()
        self.assertIs(caught.exception.__cause__,original)
        self.assertTrue(lease.closed and lease.cleanup_failed)
        self.assertTrue(all(fd.number==-1 for fd in (*lease.controls.values(),lease.child,lease.parent)))
        remove.assert_not_called()

    def test_wrong_generation_or_unbounded_memory_refuses_before_open(self):
        for generation, limit in (('z'*64, MEMORY), ('a'*63, MEMORY), (GENERATION, True),
                                  (GENERATION, 0), (GENERATION, 5*1024**3), (GENERATION, 4097)):
            with self.subTest(generation=generation, limit=limit), self.assertRaises(cg.CgroupProofError):
                cg.CgroupLease.create(self.directory, generation=generation, memory_max=limit)
        self.assertEqual(len(list(self.directory.iterdir())), 9)

    def test_parent_member_or_missing_controller_refuses_without_child(self):
        for key, value in (('cgroup.procs', b'44\n'), ('cgroup.threads', b'44\n'),
                           ('cgroup.type', b'threaded\n'), ('cgroup.subtree_control', b'pids\n')):
            original = (self.directory / key).read_bytes()
            with self.subTest(key=key):
                (self.directory / key).write_bytes(value)
                with self.assertRaises(cg.CgroupProofError):
                    self.create()
                (self.directory / key).write_bytes(original)
        self.assertEqual(len(list(self.directory.iterdir())), 9)

    def test_preexisting_child_is_never_removed(self):
        foreign = self.directory / ('buffalo-' + GENERATION)
        self.real_mkdir(foreign)
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertTrue(foreign.is_dir())

    def test_control_write_failure_cleans_original(self):
        with patch.object(cg.os, 'pwrite', side_effect=OSError('refused')):
            with self.assertRaises(OSError):
                self.create()
        self.assertFalse((self.directory / ('buffalo-' + GENERATION)).exists())

    def test_partial_control_open_failure_cleans_original(self):
        original = os.open
        def open_file(path, *args, **kwargs):
            if path == 'memory.max':
                raise PermissionError('test')
            return original(path, *args, **kwargs)
        with patch.object(cg.os, 'open', side_effect=open_file), self.assertRaises(PermissionError):
            self.create()
        self.assertFalse((self.directory / ('buffalo-' + GENERATION)).exists())

    def test_unknown_child_identity_preserves_entry_and_refuses_cleanup(self):
        original = os.open
        def open_file(path, *args, **kwargs):
            if path == 'buffalo-' + GENERATION:
                raise PermissionError('test')
            return original(path, *args, **kwargs)
        with patch.object(cg.os, 'open', side_effect=open_file), self.assertRaises(cg.CgroupCleanupError):
            self.create()
        self.assertTrue((self.directory / ('buffalo-' + GENERATION)).is_dir())

    def test_limit_readback_mismatch_refuses(self):
        lease = self.create()
        for key, value in (('memory.max', b'max\n'), ('pids.max', b'3\n'),
                           ('memory.oom.group', b'0\n'), ('cgroup.type', b'threaded\n'),
                           ('cgroup.subtree_control', b'pids\n')):
            path = self.directory / lease.name / key
            old = path.read_bytes()
            with self.subTest(key=key), self.assertRaises(cg.CgroupProofError):
                path.write_bytes(value)
                lease.attest(())
            path.write_bytes(old)

    def test_exact_process_and_thread_membership(self):
        lease = self.create()
        self.change(**{'cgroup.procs': b'12\n11\n', 'cgroup.threads': b'11\n12\n',
                       'cgroup.events': b'populated 1\nfrozen 0\n'})
        self.assertEqual(lease.attest((11, 12))['members'], [11, 12])
        for key in ('cgroup.procs', 'cgroup.threads'):
            path = self.directory / lease.name / key
            old = path.read_bytes()
            path.write_bytes(b'11\n12\n13\n')
            with self.assertRaises(cg.CgroupProofError):
                lease.attest((11, 12))
            path.write_bytes(old)

    def test_noncanonical_member_arguments_refuse(self):
        lease = self.create()
        for members in ([11], (True,), (1,), (11, 11), (11, 12, 13)):
            with self.subTest(members=members), self.assertRaises(cg.CgroupProofError):
                lease.attest(members)

    def test_oom_change_refuses_attestation_and_cleanup(self):
        lease = self.create()
        self.change(**{'memory.events': EVENTS.replace(b'oom_kill 0', b'oom_kill 1')})
        with self.assertRaises(cg.CgroupProofError):
            lease.attest(())
        with self.assertRaises(cg.CgroupCleanupError):
            lease.close()
        self.assertFalse((self.directory / lease.name).exists())

    def test_copied_lease_cannot_close_original(self):
        lease = self.create()
        copied = copy.copy(lease)
        with self.assertRaises(cg.CgroupProofError):
            copied.close()
        with self.assertRaises(cg.CgroupProofError):
            copied.attest(())
        lease.attest(())
        lease.close()

    def test_unmasked_boundary_refuses_before_owned_mutation(self):
        lease = self.create()
        original = cg.CgroupProofError('unmasked')
        descriptors = [*lease.controls.values(), lease.child, lease.parent]
        with patch.object(cg, '_signal_boundary', side_effect=original), \
             patch.object(cg.os, 'pwrite') as write, patch.object(cg.os, 'rmdir') as remove:
            with self.assertRaises(cg.CgroupProofError):
                lease.attest(())
            with self.assertRaises(cg.CgroupCleanupError) as caught:
                lease.close()
            self.assertIs(caught.exception.__cause__, original)
            with self.assertRaises(cg.CgroupCleanupError):
                lease.close()
        write.assert_not_called()
        remove.assert_not_called()
        self.assertTrue(lease.closed and lease.cleanup_failed)
        self.assertTrue(all(item.number == -1 for item in descriptors))
        self.assertTrue((self.directory / lease.name).is_dir())

    def test_renamed_original_removed_but_foreign_replacement_preserved(self):
        lease = self.create()
        old = self.directory / lease.name
        renamed = self.directory / 'renamed-owned'
        old.rename(renamed)
        self.real_mkdir(old)
        with self.assertRaises(cg.CgroupProofError):
            lease.attest(())
        with self.assertRaises(cg.CgroupCleanupError):
            lease.close()
        self.assertFalse(renamed.exists())
        self.assertTrue(old.is_dir())

    def test_foreign_member_blocks_removal_and_failure_remains_sticky(self):
        lease = self.create()
        self.change(**{'cgroup.procs': b'99\n'})
        with self.assertRaises(cg.CgroupCleanupError):
            lease.close()
        with self.assertRaises(cg.CgroupCleanupError):
            lease.close()
        self.assertTrue((self.directory / lease.name).exists())

    def test_replaced_control_descriptor_is_preserved(self):
        lease = self.create()
        descriptor = lease.controls['pids.max']
        replacement = os.open(self.directory / 'cgroup.procs', os.O_RDONLY)
        try:
            os.dup2(replacement, descriptor.number, inheritable=False)
            with self.assertRaises(ns.NamespaceProofError):
                lease.attest(())
            with self.assertRaises(cg.CgroupCleanupError):
                lease.close()
            os.fstat(descriptor.number)
            os.close(descriptor.number)  # test owns this replacement
        finally:
            os.close(replacement)

    def test_stale_mount_identity_refuses_before_write(self):
        lease = self.create()
        lease.child_mount += 1
        with patch.object(cg.os, 'pwrite') as write, self.assertRaises(cg.CgroupProofError):
            lease._write('pids.max', b'2\n')
        write.assert_not_called()

    def test_verification_closes_parent_even_if_named_close_refuses(self):
        lease = self.create()
        parent = Mock(identity=lease.parent.identity)
        named = Mock(identity=lease.child.identity)
        named.close.side_effect = ns.NamespaceCleanupError('replaced')
        with patch.object(cg, '_open_directory', return_value=parent), \
             patch.object(ns, '_acquire', return_value=named), \
             patch.object(ns, '_mount_id', side_effect=lambda fd: lease.parent_mount if fd is parent else lease.child_mount), \
             self.assertRaises(cg.CgroupCleanupError):
            lease.verify()
        parent.close.assert_called_once()

    def enrollment(self, *, stopped=True, children=b'', start=88, parent=123, terminal=False, foreign_guardian=False, guardian_state='T'):
        lease = self.create()
        descriptor = Mock(number=77)
        guardian_descriptor = Mock(number=78)
        pid = 124
        supervisor = os.getpid()
        process = dict(pid=pid, state='T' if stopped else 'S', start=start, parent=parent, group=123, session=123)
        real_listdir = os.listdir
        real_read = ns._read
        real_readlink = os.readlink
        real_stat = os.stat
        def read(path, maximum):
            if path == '/proc/self/fdinfo/77':
                return b'Pid:\t124\n'
            if path == '/proc/self/fdinfo/78':
                return b'Pid:\t123\n'
            if path.endswith('/status'):
                return json.dumps({'nspid': [pid,1] if path==f'/proc/{pid}/status' else [123]}).encode()
            if path.endswith('/children'):
                return children
            if path == f'/proc/{pid}/cgroup':
                return b'0::' + lease.path.encode() + b'\n'
            if path.endswith('/cgroup'):
                return b'0::/outside\n'
            return real_read(path, maximum)
        original_write = lease._write
        def write(name, value):
            if name == 'cgroup.procs':
                self.change(**{'cgroup.procs': b'124\n', 'cgroup.threads': b'124\n',
                               'cgroup.events': b'populated 1\nfrozen 0\n'})
            else:
                original_write(name, value)
        namespaces={'self':dict(user=[1,7],net=[1,8],mnt=[1,9],pid=[1,4]), '123':dict(user=[1,1],net=[1,2],mnt=[1,3],pid=[1,4]), '124':dict(user=[1,1],net=[1,2],mnt=[1,5],pid=[1,6])}
        contexts = [patch.object(cg, '_require_unreaped_lifecycle'),
                    patch.object(ns, '_namespaces', side_effect=lambda pid: namespaces[pid]),
                    patch.object(ns, 'parse_status', side_effect=json.loads),
                    patch.object(os, 'stat', side_effect=lambda path,*a,**k: SimpleNamespace(st_dev=1,st_ino=6) if path=='/proc/123/ns/pid_for_children' else real_stat(path,*a,**k)),
                    patch.object(ns, '_read', side_effect=read), patch.object(ns, '_process', side_effect=lambda number:
                        process if number == pid else dict(parent=supervisor + int(foreign_guardian),
                                                           group=123, session=123, start=87, state=guardian_state)),
                    patch.object(os, 'readlink', side_effect=lambda path: 'anon_inode:[pidfd]' if path in ('/proc/self/fd/77', '/proc/self/fd/78') else real_readlink(path)),
                    patch.object(os, 'listdir', side_effect=lambda path: [str(pid)] if path == f'/proc/{pid}/task' else (['123'] if path=='/proc/123/task' else real_listdir(path))),
                    patch.object(cg.backend, '_browser_pidfd_is_terminal', return_value=terminal),
                    patch.object(lease, '_write', side_effect=write)]
        for context in contexts:
            self.enterContext(context)
        return lease, descriptor, dict(pid=pid, pidfd=descriptor, start=88, guardian=123,
                                       guardian_pidfd=guardian_descriptor, guardian_start=87, supervisor=supervisor)

    def test_stopped_childless_init_enrolled_without_resuming(self):
        lease, _, args = self.enrollment()
        with patch.object(signal, 'pidfd_send_signal') as send:
            self.assertEqual(lease.enroll_stopped_init(**args)['members'], [124])
        send.assert_not_called()

    def test_shared_readback_observes_kernel_identity_without_migration(self):
        lease,_,args=self.enrollment()
        with patch.object(lease,'_write') as write,patch.object(signal,'pidfd_send_signal') as send:
            evidence=cg.observe_stopped_init_identity(**args)
        self.assertEqual(evidence['scope'],'STOPPED_NAMESPACE_READBACK_ONLY')
        self.assertFalse(evidence['execution_authority'])
        self.assertEqual(evidence['init']['state'],'T')
        self.assertNotEqual(evidence['init_namespaces']['pid'],evidence['supervisor_namespaces']['pid'])
        write.assert_not_called();send.assert_not_called()

    def test_fresh_supervisor_identity_failure_precedes_readback_and_migration(self):
        from hosted_synthetic_launcher import LauncherRefusal
        lease,_,args=self.enrollment()
        with patch('hosted_synthetic_launcher.inspect_host_identity',side_effect=LauncherRefusal('identity')), \
             patch.object(ns,'_namespaces') as namespaces,patch.object(lease,'_write') as write, \
             self.assertRaises(LauncherRefusal):
            lease.enroll_stopped_init(**args)
        namespaces.assert_not_called();write.assert_not_called()

    def test_namespace_nspid_and_reaping_refusals_precede_migration(self):
        for failure in ('subreaper','namespace','namespace_denied','nspid','guardian_threads'):
            lease,_,args=self.enrollment()
            changes={
                'subreaper':patch.object(cg,'_require_unreaped_lifecycle',side_effect=cg.CgroupProofError('subreaper')),
                'namespace':patch.object(ns,'_namespaces',return_value=dict(user=[1,1],net=[1,2],mnt=[1,3],pid=[1,4])),
                'namespace_denied':patch.object(ns,'_namespaces',side_effect=ns.NamespaceProofError('denied')),
                'nspid':patch.object(ns,'parse_status',return_value={'nspid':[124,2]}),
                'guardian_threads':patch.object(cg.os,'listdir',return_value=['123','999'])}
            with self.subTest(failure=failure),changes[failure],patch.object(lease,'attest',return_value={}), \
                 patch.object(lease,'_write') as write,self.assertRaises((cg.CgroupProofError,ns.NamespaceProofError)):
                lease.enroll_stopped_init(**args)
            write.assert_not_called();lease.close()

    def test_running_stale_parented_or_childful_init_is_not_enrolled(self):
        # One fresh lease per subcase, so refusal cannot inherit prior state.
        for options in ({'stopped': False}, {'children': b'125 '}, {'start': 89},
                        {'parent': 122}, {'terminal': True}, {'foreign_guardian': True}, {'guardian_state': 'S'}):
            with self.subTest(options=options):
                lease, _, args = self.enrollment(**options)
                with self.assertRaises(cg.CgroupProofError):
                    lease.enroll_stopped_init(**args)
                self.assertEqual((self.directory / lease.name / 'cgroup.procs').read_bytes(), b'')
                lease.close()


if __name__ == '__main__':
    unittest.main()
