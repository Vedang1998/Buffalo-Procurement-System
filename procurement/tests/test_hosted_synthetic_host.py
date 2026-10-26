"""Host observation never substitutes for live containment or application proof."""
import os
import stat
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_host as host
import hosted_synthetic_cgroup as cg
import hosted_synthetic_namespace as ns


class HostedSyntheticHostTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('hosted_synthetic_launcher.inspect_host_identity', return_value={}))

    def program_fixture(self, *, after_changes=None, version=b'pg_dump (PostgreSQL) 16.14\n'):
        content = b'root-owned synthetic program bytes'
        before = dict(st_dev=3, st_ino=22, st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o755,
                      st_size=len(content), st_mtime_ns=100, st_ctime_ns=100, st_atime=100)
        after = dict(before, **(after_changes or {}))
        handles = [Mock(number=number, identity=(3, 22)) for number in range(10, 17)]
        leaf_reads = iter((SimpleNamespace(**before), SimpleNamespace(**after)))
        def fstat(fd):
            return next(leaf_reads) if fd == 16 else SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o755)
        with patch.object(ns, '_acquire', side_effect=handles), patch.object(os, 'fstat', side_effect=fstat), \
             patch.object(os, 'pread', return_value=content), \
             patch.object(os, 'stat', return_value=SimpleNamespace(**after)), \
             patch.object(host, '_version', return_value=version) as run:
            result = host._program('pg_dump')
        for handle in handles:
            handle.close.assert_called_once()
        self.assertEqual(run.call_args.args[0], Path('/usr/lib/postgresql/16/bin/pg_dump'))
        return result

    def test_trusted_program_success_and_atime_change(self):
        self.assertEqual(self.program_fixture(after_changes={'st_atime': 101})['status'],
                         'SYSTEM_METADATA_AND_VERSION_MATCH')

    def test_changed_program_identity_or_content_metadata_refuses(self):
        for changes in ({'st_mtime_ns': 101}, {'st_ctime_ns': 101}, {'st_size': 100},
                        {'st_ino': 23}, {'st_mode': stat.S_IFREG | 0o777}):
            with self.subTest(changes=changes):
                self.assertEqual(self.program_fixture(after_changes=changes)['status'], 'UNAVAILABLE_OR_UNTRUSTED')

    def test_wrong_version_or_unbounded_version_output_refuses(self):
        for version in (b'pg_dump (PostgreSQL) 17.0\n', b'pg_dump (PostgreSQL) 16.14\nextra\n', b'x'*500,
                        b'pg_dump (PostgreSQL) 16.14 \xff\n'):
            with self.subTest(version=version[:40]):
                self.assertEqual(self.program_fixture(version=version)['status'], 'UNAVAILABLE_OR_UNTRUSTED')

    def test_version_capture_is_bounded_and_child_reaped(self):
        popen = subprocess.Popen
        children = []
        def spawn(arguments, **kwargs):
            self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
            child = popen([sys.executable, '-I', '-S', '-B', '-c',
                           'import os; os.write(1,b"x"*131072)'], **kwargs)
            children.append(child)
            return child
        with patch.object(host.subprocess, 'Popen', side_effect=spawn), self.assertRaises(cg.CgroupProofError):
            host._version(Path('/fixed-synthetic-program'))
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdout.closed)

    def test_version_capture_small_output(self):
        self.assertRegex(host._version(Path(sys.executable)), rb'^Python 3\.')
    def test_program_name_is_fixed(self):
        for value in ('sh', '../pg_dump', '/usr/bin/pg_dump', '', None):
            with self.subTest(value=value), patch.object(os, 'open') as opening, self.assertRaises(ValueError):
                host._program(value)
            opening.assert_not_called()

    def test_missing_program_never_executes(self):
        with patch.object(os, 'open', side_effect=FileNotFoundError), patch.object(host, '_version') as run:
            result = host._program('pg_dump')
        self.assertEqual(result['status'], 'UNAVAILABLE_OR_UNTRUSTED')
        run.assert_not_called()

    def test_mutable_root_never_executes(self):
        descriptor = Mock(number=44)
        with patch.object(ns, '_acquire', return_value=descriptor), \
             patch.object(os, 'fstat', return_value=Mock(st_uid=1000)), \
             patch.object(host, '_version') as run:
            result = host._program('pg_dump')
        self.assertEqual(result['status'], 'UNAVAILABLE_OR_UNTRUSTED')
        run.assert_not_called()
        descriptor.close.assert_called_once()

    def test_descriptor_cleanup_failure_is_not_swallowed(self):
        descriptor = Mock(number=44)
        descriptor.close.side_effect = ns.NamespaceCleanupError('test')
        with patch.object(ns, '_acquire', return_value=descriptor), \
             patch.object(os, 'fstat', return_value=Mock(st_uid=1000)), \
             self.assertRaises(cg.CgroupCleanupError):
            host._program('pg_dump')

    def test_acquisition_cleanup_failure_is_not_swallowed(self):
        with patch.object(ns, '_acquire', side_effect=ns.NamespaceCleanupError('test')), \
             self.assertRaises(ns.NamespaceCleanupError):
            host._program('pg_dump')

    def test_ordinary_host_observation_remains_nonauthorizing(self):
        with patch.object(host, '_program', return_value={'status': 'SYSTEM_METADATA_AND_VERSION_MATCH'}), \
             patch.object(host, '_delegation', return_value={'candidates': ['/owned']}):
            result = host.observe_host_prerequisites()
        self.assertTrue(result['prerequisites_available'])
        self.assertFalse(result['execution_authority'])
        self.assertEqual(result['application_backup_validator'], 'NOT_RUN')
        self.assertEqual(result['cgroup_write_attempts'], 0)
        self.assertEqual(result['provider_calls'], 0)

    def test_supervisor_identity_requires_empty_groups_without_modification(self):
        for groups, eligible in (([], True), ([1001], False), ([65534, 65534], False)):
            with self.subTest(groups=groups), patch.object(os, 'getresuid', return_value=(1001,)*3), \
                 patch.object(os, 'getresgid', return_value=(1001,)*3), \
                 patch.object(os, 'getgroups', return_value=groups), \
                 patch.object(os, 'setgroups') as mutate:
                result = host.observe_supervisor_identity()
            self.assertEqual(result['eligible_for_user_mapping'], eligible)
            self.assertEqual(result['supplementary_groups_empty'], not groups)
            self.assertFalse(result['execution_authority'])
            self.assertNotIn('groups', result)
            mutate.assert_not_called()

    def test_supervisor_identity_refuses_root_or_mixed_credentials(self):
        for uids, gids in (((0, 0, 0), (1001,)*3), ((1001, 0, 1001), (1001,)*3),
                           ((1001,)*3, (1001, 0, 1001))):
            with self.subTest(uids=uids, gids=gids), patch.object(os, 'getresuid', return_value=uids), \
                 patch.object(os, 'getresgid', return_value=gids), \
                 patch.object(os, 'getgroups', return_value=[]):
                self.assertFalse(host.observe_supervisor_identity()['eligible_for_user_mapping'])

    def test_either_missing_requirement_blocks(self):
        for program, candidates in (('UNAVAILABLE', ['/owned']), ('SYSTEM_METADATA_AND_VERSION_MATCH', [])):
            with self.subTest(program=program), patch.object(host, '_program', return_value={'status': program}), \
                 patch.object(host, '_delegation', return_value={'candidates': candidates}):
                self.assertFalse(host.observe_host_prerequisites()['prerequisites_available'])

    def test_observation_failure_is_explicit(self):
        with patch.object(host, '_program', return_value={'status': 'SYSTEM_METADATA_AND_VERSION_MATCH'}), \
             patch.object(host, '_delegation', side_effect=PermissionError):
            result = host.observe_host_prerequisites()
        self.assertEqual(result['delegation']['status'], 'OBSERVATION_REFUSED')
        self.assertFalse(result['prerequisites_available'])

    def test_delegation_cleanup_failure_propagates(self):
        for error in (cg.CgroupCleanupError, ns.NamespaceCleanupError):
            with self.subTest(error=error), patch.object(host, '_program', return_value={}), \
                 patch.object(host, '_delegation', side_effect=error), self.assertRaises(error):
                host.observe_host_prerequisites()

    def test_delegation_checks_only_current_ancestry_without_mutation(self):
        with patch.object(ns, '_read', return_value=b'0::/scope/launcher\n'), \
             patch.object(os.path, 'lexists', return_value=False), \
             patch.object(cg, '_open_directory', side_effect=PermissionError) as opening, \
             patch.object(os, 'mkdir') as mkdir, patch.object(os, 'pwrite') as write:
            result = host._delegation()
        self.assertEqual([str(call.args[0]) for call in opening.call_args_list],
                         ['/sys/fs/cgroup/scope/launcher', '/sys/fs/cgroup/scope', '/sys/fs/cgroup'])
        self.assertEqual(result['status'], 'NO_USABLE_CURRENT_ANCESTRY_DELEGATION')
        mkdir.assert_not_called()
        write.assert_not_called()

    def test_depth_and_malformed_membership_are_bounded(self):
        for raw in (b'0::/' + b'a/'*65 + b'b\n', b'0::/../other\n', b'0::/scope'):
            with self.subTest(raw=raw), patch.object(ns, '_read', return_value=raw), \
                 patch.object(os.path, 'lexists', return_value=False), \
                 patch.object(cg, '_open_directory') as opening, self.assertRaises(RuntimeError):
                host._delegation()
            opening.assert_not_called()

    def test_populated_or_missing_controls_cannot_be_a_candidate(self):
        for key, replacement in (('cgroup.procs', b'2\n'), ('cgroup.threads', b'2\n'),
                                 ('cgroup.type', b'threaded\n'), ('cgroup.subtree_control', b'pids\n')):
            values = {'cgroup.procs': b'', 'cgroup.threads': b'', 'cgroup.type': b'domain\n',
                      'cgroup.subtree_control': b'memory pids\n'}
            values[key] = replacement
            with self.subTest(key=key), patch.object(ns, '_read', return_value=b'0::/scope\n'), \
                 patch.object(os.path, 'lexists', return_value=False), \
                 patch.object(cg, '_open_directory', return_value=Mock()), \
                 patch.object(cg, '_read_at', side_effect=lambda fd, name: values[name]), \
                 patch.object(os, 'access', return_value=True):
                self.assertEqual(host._delegation()['candidates'], [])

    def test_candidate_requires_writable_directory_and_enrollment_file(self):
        values = {'cgroup.procs': b'', 'cgroup.threads': b'', 'cgroup.type': b'domain\n',
                  'cgroup.subtree_control': b'memory pids\n'}
        for writable_directory, writable_file, expected in ((True, True, ['/scope']),
                                                           (False, True, []), (True, False, [])):
            with self.subTest(directory=writable_directory, file=writable_file), \
                 patch.object(ns, '_read', return_value=b'0::/scope\n'), \
                 patch.object(os.path, 'lexists', return_value=False), \
                 patch.object(cg, '_open_directory', side_effect=lambda path: Mock() if str(path).endswith('/scope') else (_ for _ in ()).throw(PermissionError())), \
                 patch.object(cg, '_read_at', side_effect=lambda fd, name: values[name]), \
                 patch.object(os, 'access', side_effect=lambda path, mode: writable_file if str(path).endswith('/cgroup.procs') else writable_directory):
                self.assertEqual(host._delegation()['candidates'], expected)


if __name__ == '__main__':
    unittest.main()
