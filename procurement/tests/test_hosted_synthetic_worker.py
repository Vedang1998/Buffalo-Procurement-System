"""Fixed worker policy tests; ordinary tests never create namespaces/services."""
from pathlib import Path
import errno
import hashlib
import json
import os
import struct
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_worker as worker
import hosted_synthetic_namespace as ns


def evaluate(number, *, arch=0xC000003E, flags=0):
    data=struct.pack('<iIQQQQQQQ',number if number<2**31 else number-2**32,arch,0,flags,0,0,0,0,0)
    code=list(struct.iter_unpack('<HBBI',worker.policy_bytes()));pc=0;value=0
    while pc<len(code):
        op,jt,jf,k=code[pc];pc+=1
        if op==0x20:value=struct.unpack_from('<I',data,k)[0]
        elif op==0x15:pc+=jt if value==k else jf
        elif op==0x35:pc+=jt if value>=k else jf
        elif op==0x45:pc+=jt if value&k else jf
        elif op==0x06:return k
        else:raise AssertionError('unknown BPF instruction')
    raise AssertionError('unterminated policy')


class HostedWorkerPolicyTests(unittest.TestCase):
    def test_policy_is_fixed_and_hash_bound(self):
        self.assertEqual(len(worker.policy_bytes())%8,0)
        self.assertEqual(worker.POLICY_SHA256,hashlib.sha256(b'BUFFALO_HOSTED_WORKER_SECCOMP_V1\0'+worker.policy_bytes()).hexdigest())
        self.assertLess(len(worker.policy_bytes())//8,256)

    def test_other_architectures_and_x32_are_killed(self):
        for arch in (0x40000003,0xC00000B7,0,0xffffffff):
            self.assertEqual(evaluate(39,arch=arch),worker._KILL)
        for number in (0x40000000,0x4000003b,0xffffffff):
            self.assertEqual(evaluate(number),worker._KILL)

    def test_namespace_mount_handle_and_process_authority_are_denied(self):
        for number in worker._DENIED:
            with self.subTest(number=number):self.assertEqual(evaluate(number),worker._ERRNO|errno.EPERM)

    def test_clone3_is_enosys_and_clone_rejects_every_namespace_flag(self):
        self.assertEqual(evaluate(435),worker._ERRNO|errno.ENOSYS)
        for bit in range(64):
            flags=1<<bit
            expected=worker._ERRNO|errno.EPERM if flags&worker._NAMESPACE_FLAGS or flags>>32 else worker._ALLOW
            with self.subTest(bit=bit):self.assertEqual(evaluate(56,flags=flags),expected)

    def test_ordinary_process_thread_and_io_calls_remain_allowed(self):
        for number in (0,1,2,3,9,10,11,39,57,58,59,60,61,202,231,257,322):
            self.assertEqual(evaluate(number),worker._ALLOW)
        self.assertEqual(evaluate(56,flags=0x3d0f00),worker._ALLOW)

    def test_real_filter_blocks_syscalls_and_preserves_ordinary_fork(self):
        script=r'''
import ctypes,errno,json,os,sys
sys.path.insert(0,sys.argv[1])
import hosted_synthetic_worker as w
w.ns.backend._browser_prctl(38,1)
proof=w.install_filter()
libc=ctypes.CDLL(None,use_errno=True)
for number,args in ((272,(0,)),(308,(-1,0)),(165,(0,0,0,0,0)),(430,(0,0)),(442,(-1,0,0,0,0)),(438,(-1,-1,0)),(101,(0,0,0,0)),(56,(0x10000000,0,0,0,0))):
 ctypes.set_errno(0)
 assert libc.syscall(number,*args)==-1 and ctypes.get_errno()==errno.EPERM,(number,ctypes.get_errno())
ctypes.set_errno(0)
assert libc.syscall(435,0,0)==-1 and ctypes.get_errno()==errno.ENOSYS
pid=os.fork()
if pid==0:os._exit(0)
assert os.waitpid(pid,0)==(pid,0)
print(json.dumps(proof,sort_keys=True))
'''
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',script,str(Path(worker.__file__).parent)],
                              capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        proof=json.loads(result.stdout)
        self.assertEqual(proof['sha256'],worker.POLICY_SHA256)
        self.assertEqual(proof['filter_count_after'],proof['filter_count_before']+1)

    def test_filter_requires_nnp_before_installation(self):
        with patch.object(worker,'_status',return_value={'no_new_privs':0,'Seccomp':0,'Seccomp_filters':0}), \
             patch.object(ns.backend,'_browser_libc') as native,self.assertRaises(ns.NamespaceProofError):
            worker.install_filter()
        native.assert_not_called()

    def test_capability_drop_cannot_target_ordinary_host_identity(self):
        with patch.object(os,'getresuid',return_value=(1001,)*3), \
             patch.object(ns.backend,'_browser_prctl') as native,self.assertRaises(ns.NamespaceProofError):
            worker.drop_capabilities()
        native.assert_not_called()

    def test_projected_root_check_precedes_irreversible_changes(self):
        with patch.object(worker,'require_projected_root',side_effect=ns.NamespaceProofError('root')), \
             patch.object(worker,'drop_capabilities') as caps,patch.object(worker,'install_filter') as policy, \
             self.assertRaises(ns.NamespaceProofError):worker.restrict_worker((1,2),3)
        caps.assert_not_called();policy.assert_not_called()

    def test_capability_failure_prevents_policy_or_success(self):
        with patch.object(worker,'require_projected_root',return_value={}), \
             patch.object(worker,'drop_capabilities',side_effect=ns.NamespaceProofError('caps')), \
             patch.object(worker,'install_filter') as policy,self.assertRaises(ns.NamespaceProofError):
            worker.restrict_worker((1,2),3)
        policy.assert_not_called()

    def test_nonempty_groups_refuse_before_capability_or_kernel_effects(self):
        with patch.object(os,'getresuid',return_value=(0,)*3),patch.object(os,'getresgid',return_value=(0,)*3), \
             patch.object(os,'getgroups',return_value=[0]),patch.object(ns.backend,'_browser_prctl') as native, \
             self.assertRaises(ns.NamespaceProofError):worker.drop_capabilities()
        native.assert_not_called()

    def test_external_worker_attestation_rejects_every_retained_capability(self):
        base=dict(uid=[0]*4,gid=[0]*4,groups=[],no_new_privs=1,Seccomp=2,Seccomp_filters=3,
                  **{key:0 for key in worker._CAPS})
        for key in worker._CAPS:
            with self.subTest(key=key),patch.object(worker,'_status',return_value=dict(base,**{key:1})), \
                 patch.object(ns,'_acquire') as acquire,self.assertRaises(ns.NamespaceProofError):
                worker.attest_worker(2,[1,2],3)
            acquire.assert_not_called()

    def test_external_worker_attestation_requires_stopped_filtered_exact_identity(self):
        base=dict(uid=[0]*4,gid=[0]*4,groups=[],no_new_privs=1,Seccomp=2,Seccomp_filters=3,
                  **{key:0 for key in worker._CAPS})
        for key,value in (('uid',[0,0,0,1]),('gid',[0,0,0,1]),('groups',[0]),
                          ('no_new_privs',0),('Seccomp',0),('Seccomp_filters',2)):
            with self.subTest(key=key),patch.object(worker,'_status',return_value=dict(base,**{key:value})), \
                 patch.object(ns,'_acquire') as acquire,self.assertRaises(ns.NamespaceProofError):
                worker.attest_worker(2,[1,2],3)
            acquire.assert_not_called()
        with patch.object(worker,'_status',return_value=base),patch.object(ns,'_process',return_value={'state':'R'}), \
             patch.object(ns,'_acquire') as acquire,self.assertRaises(ns.NamespaceProofError):worker.attest_worker(2,[1,2],3)
        acquire.assert_not_called()

    def test_wrong_worker_root_closes_owned_descriptor_and_never_reports_success(self):
        current=dict(uid=[0]*4,gid=[0]*4,groups=[],no_new_privs=1,Seccomp=2,Seccomp_filters=3,
                     **{key:0 for key in worker._CAPS})
        root=Mock(identity=(3,4))
        with patch.object(worker,'_status',return_value=current),patch.object(ns,'_process',return_value={'state':'T'}), \
             patch.object(ns,'_acquire',return_value=root),self.assertRaises(ns.NamespaceProofError):
            worker.attest_worker(2,[1,2],3)
        root.close.assert_called_once()


if __name__=='__main__':unittest.main()
