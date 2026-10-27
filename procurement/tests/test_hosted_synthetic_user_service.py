"""Simulated delegated kernel controls; never a live systemd capability claim."""
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_user_service as service
import hosted_synthetic_cgroup as cg


UNIT = 'buffalo-hosted-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.service'
ROOT = '/user.slice/user-1001.slice/user@1001.service/app.slice/' + UNIT


class HostedSyntheticUserServiceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('hosted_synthetic_launcher.inspect_host_identity', return_value={}))
        self.parent = Mock(number=10, identity=(7, 10))
        self.supervisor = Mock(number=11, identity=(7, 11))
        self.control = Mock(number=12, identity=(7, 12))
        self.entries = {'cgroup.type': b'domain\n', 'cgroup.procs': b'', 'cgroup.threads': b'',
                        'memory.max': b'536870912\n', 'pids.max': b'64\n',
                        'cgroup.controllers': b'cpu io memory pids\n', 'cgroup.subtree_control': b''}
        self.members = b'101\n102\n'
        self.current = ROOT + '/supervisor'
        self.enterContext(patch.object(service.os, 'getuid', return_value=1001))
        self.enterContext(patch.object(service.os, 'geteuid', return_value=1001))
        self.enterContext(patch.object(service.os, 'getpid', return_value=102))
        self.enterContext(patch.object(service.os, 'getppid', return_value=101))
        self.enterContext(patch.object(service.ns, '_read', side_effect=lambda *args: b'0::' + self.current.encode() + b'\n'))
        self.enterContext(patch.object(cg, '_open_directory', side_effect=lambda path:
            self.supervisor if path.name == 'supervisor' else self.parent))
        self.enterContext(patch.object(service.ns, '_mount_id', return_value=5))
        self.enterContext(patch.object(cg, '_read_at', side_effect=lambda fd, name:
            self.members if fd is self.supervisor and name in ('cgroup.procs', 'cgroup.threads') else self.entries[name]))
        self.enterContext(patch.object(service.ns, '_acquire', return_value=self.control))
        info = SimpleNamespace(st_uid=1001, st_mode=stat.S_IFREG | 0o644, st_dev=7, st_ino=12)
        self.enterContext(patch.object(service.os, 'fstat', return_value=info))
        self.enterContext(patch.object(service.os, 'stat', return_value=info))
        self.write = self.enterContext(patch.object(service.os, 'write', side_effect=self.write_control))

    def write_control(self, fd, data):
        self.assertEqual((fd, data), (12, b'+memory +pids\n'))
        self.entries['cgroup.subtree_control'] = b'memory pids\n'
        return len(data)

    def prepare(self, unit=UNIT):
        # The real cancellation envelope is separately tested in source tests;
        # simulated PIDs here deliberately cannot represent native /proc.
        return service.prepare_user_service.__wrapped__(unit)

    def test_only_exact_owned_root_is_enabled(self):
        result = self.prepare()
        self.write.assert_called_once_with(12, b'+memory +pids\n')
        self.assertEqual(result['root_path'], ROOT)
        self.assertEqual(result['controller_write_attempts'], 1)
        self.assertEqual(result['enabled_after'], ['memory', 'pids'])
        self.assertFalse(result['execution_authority'])
        self.assertEqual(result['provider_calls'], 0)
        self.control.close.assert_called_once()

    def test_already_enabled_is_idempotent(self):
        self.entries['cgroup.subtree_control'] = b'memory pids\n'
        self.assertEqual(self.prepare()['controller_write_attempts'], 0)
        self.write.assert_not_called()

    def test_system_manager_path_requires_explicit_mode_and_same_owned_controls(self):
        self.current = '/system.slice/system-buffaloaaaaaaaabbbbccccddddeeeeeeeeeeee.slice/' + UNIT + '/supervisor'
        with self.assertRaises(cg.CgroupProofError):
            self.prepare()
        self.write.assert_not_called()
        with self.assertRaises(cg.CgroupProofError):
            service.prepare_user_service.__wrapped__(UNIT, manager='unknown')
        self.write.assert_not_called()
        result = service.prepare_user_service.__wrapped__(UNIT, manager='system')
        self.assertEqual(result['lifecycle_owner'], 'SYSTEMD_SYSTEM_MANAGER')
        self.assertEqual(result['root_path'], '/system.slice/system-buffaloaaaaaaaabbbbccccddddeeeeeeeeeeee.slice/' + UNIT)
        self.assertEqual(result['controller_write_attempts'], 1)
        self.current = ROOT + '/supervisor'
        with self.assertRaises(cg.CgroupProofError):
            service.prepare_user_service.__wrapped__(UNIT, manager='system')

    def test_wrong_unit_or_ancestor_never_gets_write_authority(self):
        for unit in ('main.service', '../' + UNIT, '', None, UNIT.replace('a', 'A')):
            with self.subTest(unit=unit), self.assertRaises(cg.CgroupProofError):
                self.prepare(unit)
        for current in ('/system.slice/system-buffaloaaaaaaaabbbbccccddddeeeeeeeeeeee.slice/' + UNIT + '/supervisor', ROOT,
                        ROOT.replace('user-1001', 'user-1002') + '/supervisor',
                        ROOT.replace('app.slice/', 'app.slice/unrelated.service/') + '/supervisor',
                        ROOT.replace('app.slice', 'nested.slice') + '/supervisor'):
            self.current = current
            with self.subTest(current=current), self.assertRaises(cg.CgroupProofError):
                self.prepare()
        self.write.assert_not_called()

    def test_populated_root_or_wrong_outer_limit_refuses(self):
        for key, value in (('cgroup.procs', b'99\n'), ('cgroup.threads', b'99\n'),
                           ('memory.max', b'max\n'), ('pids.max', b'65\n'), ('cgroup.type', b'threaded\n')):
            previous = self.entries[key]
            self.entries[key] = value
            with self.subTest(key=key), self.assertRaises(cg.CgroupProofError):
                self.prepare()
            self.entries[key] = previous
        self.write.assert_not_called()

    def test_foreign_or_missing_supervisor_member_refuses(self):
        for members in (b'101\n', b'101\n102\n103\n', b'99\n102\n'):
            self.members = members
            with self.subTest(members=members), self.assertRaises(cg.CgroupProofError):
                self.prepare()
        self.write.assert_not_called()

    def test_missing_duplicate_or_unexpected_controls_refuse(self):
        for key, value in (('cgroup.controllers', b'pids\n'), ('cgroup.controllers', b'memory pids pids\n'),
                           ('cgroup.subtree_control', b'cpu\n'), ('cgroup.subtree_control', b'memory pids')):
            previous = self.entries[key]
            self.entries[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(cg.CgroupProofError):
                self.prepare()
            self.entries[key] = previous
        self.write.assert_not_called()

    def test_replaced_name_or_mount_refuses(self):
        replacement = Mock(identity=(7, 99), number=99)
        with patch.object(cg, '_open_directory', side_effect=[self.parent, self.supervisor, replacement]), \
             self.assertRaises(cg.CgroupProofError):
            self.prepare()
        replacement.close.assert_called_once()
        with patch.object(service.ns, '_mount_id', side_effect=lambda fd: 6 if fd is self.supervisor else 5), \
             self.assertRaises(cg.CgroupProofError):
            self.prepare()
        self.write.assert_not_called()

    def test_write_refusal_or_wrong_readback_is_not_a_pass(self):
        for write in (PermissionError('synthetic'), lambda fd, value: len(value), lambda fd, value: 1):
            self.entries['cgroup.subtree_control'] = b''
            with patch.object(service.os, 'write', side_effect=write), \
                 self.assertRaises((cg.CgroupProofError, PermissionError)):
                self.prepare()

    def test_control_identity_or_ownership_refuses_before_write(self):
        for info in (SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644),
                     SimpleNamespace(st_uid=1001, st_mode=stat.S_IFREG | 0o666)):
            with patch.object(service.os, 'fstat', return_value=info), self.assertRaises(cg.CgroupProofError):
                self.prepare()
        with patch.object(service.os, 'stat', return_value=SimpleNamespace(st_dev=7, st_ino=99)), \
             self.assertRaises(cg.CgroupProofError):
            self.prepare()
        self.write.assert_not_called()

    def test_cleanup_failure_overrides_write_failure_and_closes_other_handles(self):
        self.control.close.side_effect = RuntimeError('replaced')
        with patch.object(service.os, 'write', side_effect=PermissionError('test')), \
             self.assertRaises(cg.CgroupCleanupError):
            self.prepare()
        self.parent.close.assert_called()
        self.supervisor.close.assert_called()


if __name__ == '__main__':
    unittest.main()
