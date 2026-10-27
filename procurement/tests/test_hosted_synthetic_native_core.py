"""Native core policy tests; no crashing payload or core handler is executed."""
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_namespace as ns
import hosted_synthetic_native_core as core
import hosted_synthetic_worker as worker
import hosted_synthetic_source as source


class NativeCoreTests(unittest.TestCase):
    def test_nonpiped_pattern_is_bound_without_exposing_host_path(self):
        raw=b'/private-host-path/core.%p.%e\n'
        self.assertEqual(core.parse_pattern(raw),hashlib.sha256(raw).hexdigest())

    def test_pipe_handlers_are_refused_not_treated_as_filenames(self):
        for raw in (b'|/usr/bin/handler %p\n',b' |handler\n',b'core|handler\n'):
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):core.parse_pattern(raw)

    def test_empty_truncated_control_nonascii_and_oversized_patterns_refuse(self):
        for raw in (b'',b'\n',b' \n',b'core',b'core\nmore\n',b'core\0\n',b'\xff\n',b'a'*128+b'\n'):
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):core.parse_pattern(raw)

    def test_kernel_proc_identity_is_required_before_pattern_read(self):
        descriptor=Mock(number=7)
        with patch.object(ns,'_acquire',return_value=descriptor),patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0x01021994),patch.object(os,'pread') as read,self.assertRaises(ns.NamespaceProofError):core.observe_pattern()
        read.assert_not_called();descriptor.close.assert_called_once()

    def test_signal_ownership_failure_precedes_pattern_descriptor_acquisition(self):
        with patch.object(source.signal,'getsignal',return_value=signal.SIG_IGN),patch.object(ns,'_acquire') as acquire,self.assertRaises(source.HostedSourceError):core.observe_pattern()
        acquire.assert_not_called()

    def test_pending_cancellation_arrives_after_pattern_descriptor_closes(self):
        descriptor=Mock(number=7)
        with patch.object(source.signal,'pthread_sigmask',side_effect=[set(),KeyboardInterrupt()]),patch.object(ns,'_acquire',return_value=descriptor),patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0x9fa0),patch.object(os,'pread',return_value=b'core\n'),self.assertRaises(KeyboardInterrupt):core.observe_pattern()
        descriptor.close.assert_called_once()

    def test_pattern_descriptor_closes_on_rejection(self):
        descriptor=Mock(number=7)
        with patch.object(ns,'_acquire',return_value=descriptor),patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0x9fa0),patch.object(os,'pread',return_value=b'|handler\n'),self.assertRaises(ns.NamespaceProofError):core.observe_pattern()
        descriptor.close.assert_called_once()

    def test_pattern_close_failure_preserves_original_read_failure(self):
        original=OSError('synthetic read');descriptor=Mock(number=7);descriptor.close.side_effect=OSError('synthetic close')
        with patch.object(ns,'_acquire',return_value=descriptor),patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0x9fa0),patch.object(os,'pread',side_effect=original),self.assertRaises(ns.NamespaceCleanupError) as caught:core.observe_pattern()
        self.assertIs(caught.exception.__cause__,original)

    def test_entry_failure_precedes_pattern_and_resource_effects(self):
        with patch.object(core,'_entry',side_effect=ns.NamespaceProofError('entry')),patch.object(core,'observe_pattern') as read,patch.object(core,'_zero_limit') as limit,self.assertRaises(ns.NamespaceProofError):core.enforce_native_core()
        read.assert_not_called();limit.assert_not_called()

    def test_pipe_policy_failure_precedes_limit_change(self):
        with patch.object(core,'_entry'),patch.object(core,'observe_pattern',side_effect=ns.NamespaceProofError('piped')),patch.object(core,'_zero_limit') as limit,self.assertRaises(ns.NamespaceProofError):core.enforce_native_core()
        limit.assert_not_called()

    def test_observed_policy_and_hard_limit_are_required_without_exec_grant(self):
        with patch.object(core,'_entry') as entry,patch.object(core,'observe_pattern',return_value='a'*64) as read,patch.object(core,'_zero_limit') as limit:
            result=core.enforce_native_core()
        self.assertEqual(entry.call_count,2);self.assertEqual(read.call_count,2);limit.assert_called_once()
        self.assertFalse(result['execution_authority']);self.assertEqual(result['hard_limit'],0)
        self.assertEqual(result['external_lifetime_observation'],'NOT_PROVEN')

    def test_policy_change_withholds_observation(self):
        with patch.object(core,'_entry'),patch.object(core,'observe_pattern',side_effect=['a'*64,'b'*64]),patch.object(core,'_zero_limit'),self.assertRaises(ns.NamespaceProofError):core.enforce_native_core()

    def test_limit_failure_withholds_observation(self):
        with patch.object(core,'_entry'),patch.object(core,'observe_pattern',return_value='a'*64),patch.object(core,'_zero_limit',side_effect=OSError('synthetic limit')),self.assertRaises(OSError):core.enforce_native_core()

    def test_successful_setrlimit_without_zero_readback_refuses(self):
        with patch.object(resource,'setrlimit') as set_limit,patch.object(resource,'getrlimit',return_value=(0,1)),self.assertRaises(ns.NamespaceProofError):core._zero_limit()
        set_limit.assert_called_once_with(resource.RLIMIT_CORE,(0,0))

    def test_real_disposable_child_cannot_raise_its_zero_hard_limit(self):
        script=r'''
import json,resource,sys
sys.path.insert(0,sys.argv[1])
import hosted_synthetic_native_core as c
c._zero_limit()
try:resource.setrlimit(resource.RLIMIT_CORE,(1,1))
except (ValueError,PermissionError):pass
else:raise AssertionError('zero hard limit raised')
print(json.dumps({'limit':resource.getrlimit(resource.RLIMIT_CORE),'scope':'RLIMIT_ONLY_NOT_CONTAINMENT'}))
'''
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',script,str(Path(core.__file__).parent)],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        self.assertEqual(json.loads(result.stdout)['limit'],[0,0])

    def test_every_capability_and_four_field_identity_remains_required(self):
        base={'uid':[1000]*4,'gid':[1000]*4,'groups':[],'no_new_privs':1,'Seccomp':2,'Seccomp_filters':2,**{k:0 for k in worker._CAPS}}
        variants=[dict(base,**{k:1}) for k in worker._CAPS]+[dict(base,uid=[1000,1000,1000,0]),dict(base,groups=[0]),dict(base,no_new_privs=0),dict(base,Seccomp=0)]
        with patch.object(os,'getresuid',return_value=(1000,)*3),patch.object(os,'getresgid',return_value=(1000,)*3),patch.object(os,'getgroups',return_value=[]),patch.object(worker,'_get_prctl',side_effect=lambda n:worker._SECUREBITS if n==27 else 0):
            for value in variants:
                with self.subTest(value=value),patch.object(worker,'_status',return_value=value),self.assertRaises(ns.NamespaceProofError):core._entry()
