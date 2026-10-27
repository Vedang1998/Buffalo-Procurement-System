"""Causal outside-observer failures with synthetic kernel/manager evidence."""
from dataclasses import replace
import copy
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_observer as observer
import hosted_synthetic_bootstrap as boot
import hosted_synthetic_cgroup as cg
import hosted_synthetic_namespace as ns

UNIT='buffalo-hosted-01234567-89ab-cdef-0123-456789abcdef.service'


def fixture():
    value=object.__new__(observer.ExternalObserver);value._owner=value
    value.account=SimpleNamespace(uncertain=False,journal=Mock(),identity=SimpleNamespace(uid=900,gid=900,unit=UNIT))
    value.binding=SimpleNamespace(commit='a'*40,tree='b'*40)
    value.unit=UNIT;value.slice_unit=observer.slice_name(UNIT)
    value.path='/sys/fs/cgroup/system.slice/'+value.slice_unit
    value.root=Mock(number=10,identity=(1,2));value.mount=3
    value.controls={name:Mock() for name in observer._CONTROLS}
    value.service=Mock(number=11);value.generation_dir=Mock(number=12)
    value.actors={role:Mock(number=20+index) for index,role in enumerate(observer._ROLES)}
    value.generation='c'*64;value.invocation='d'*32;value.service_invocation='e'*32
    value.closed=False;value.failed=False;value.descriptors_closed_successfully=False;value.creation_acknowledged=True;value.work_acknowledged=True
    value.aggregate=None;value.proof=None
    return value


def counters():
    return {'memory.current':b'0\n','memory.peak':b'1234\n','memory.max':b'536870912\n','pids.max':b'64\n',
            'memory.events':b'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n',
            'cgroup.procs':b'','cgroup.threads':b'','cgroup.events':b'populated 0\nfrozen 0\n'}


class HostedObserverTests(unittest.TestCase):
    def test_owned_slice_plan_keeps_aggregate_limits_and_uses_native_tools_only(self):
        command=observer.plan_slice(UNIT)
        self.assertEqual(command[:4],('/usr/bin/sudo','-n','--','/usr/bin/env'))
        self.assertIn('/usr/bin/busctl',command);self.assertIn('StartTransientUnit',command)
        self.assertIn('536870912',command);self.assertIn('64',command)
        self.assertNotIn('python',' '.join(command));self.assertNotIn('/bin/sh',command)
        self.assertEqual(observer.service_path(UNIT),'/system.slice/system-buffalo0123456789abcdef0123456789abcdef.slice/'+UNIT)

    def test_foreign_or_noncanonical_unit_cannot_reach_native_plan(self):
        for unit in ('main.service',UNIT+';true',UNIT.upper(),'../'+UNIT,True):
            with self.subTest(unit=unit),self.assertRaises(boot.BootstrapRefusal):observer.plan_slice(unit)

    def test_failed_boundary_closes_every_descriptor_without_native_mutation(self):
        value=fixture();descriptors=[value.root,value.service,value.generation_dir,*value.controls.values(),*value.actors.values()]
        with patch.object(boot,'_collect_native_client') as command,self.assertRaises(boot.BootstrapCleanupError):value.close()
        command.assert_not_called()
        self.assertTrue(value.closed and value.failed and value.account.uncertain)
        for item in descriptors:item.close.assert_called_once()

    def test_one_descriptor_failure_still_closes_other_handles_and_stays_failed(self):
        value=fixture();value.actors['worker'].close.side_effect=OSError('synthetic')
        with self.assertRaises(boot.BootstrapCleanupError):value._close_descriptors()
        self.assertTrue(value.closed and value.failed)
        self.assertFalse(value.descriptors_closed_successfully)
        value.root.close.assert_called_once();value.service.close.assert_called_once()
        for item in value.controls.values():item.close.assert_called_once()

    def test_copied_observer_cannot_close_or_change_original_handles(self):
        value=fixture();copied=copy.copy(value)
        for operation in (copied.close,copied._close_descriptors):
            with self.assertRaises(boot.BootstrapRefusal):operation()
        self.assertFalse(value.closed or value.failed or value.account.uncertain)
        value.root.close.assert_not_called()
        for item in value.actors.values():item.close.assert_not_called()

    def test_post_removal_enodev_cannot_reuse_cached_memory_sample(self):
        value=fixture();value.aggregate={'current':0,'peak':1234,'events':{key:0 for key in cg._MEMORY_KEYS}}
        with patch.object(value,'verify'),patch.object(value,'read',side_effect=OSError(errno.ENODEV,'gone')),self.assertRaises(OSError):value.sample()
        self.assertTrue(value.failed and value.account.uncertain)
        self.assertIsNone(value.proof)

    def test_peak_oom_counter_or_limit_drift_is_sticky(self):
        for key,changed in (('memory.peak',b'536870913\n'),('memory.max',b'1073741824\n'),('pids.max',b'65\n'),
                            ('memory.events',b'low 0\nhigh 0\nmax 0\noom 1\noom_kill 0\noom_group_kill 0\n')):
            value=fixture();data=counters();data[key]=changed
            with self.subTest(key=key),patch.object(value,'verify'),patch.object(value,'read',side_effect=data.__getitem__), \
                 self.assertRaises((boot.BootstrapRefusal,cg.CgroupProofError)):value.sample()
            self.assertTrue(value.failed and value.account.uncertain)

    def test_duplicate_or_lost_ack_cannot_establish_drain_proof(self):
        value=fixture()
        with self.assertRaises(boot.BootstrapRefusal):value.acknowledge_sent()
        self.assertTrue(value.failed)
        value=fixture();value.work_acknowledged=False
        with patch.object(value,'verify') as verify,self.assertRaises(boot.BootstrapRefusal):value.finish_service(operation_succeeded=False)
        verify.assert_not_called();self.assertIsNone(value.proof)

    def test_ready_wrong_source_or_generation_refuses_before_manager_observation(self):
        for key,changed in (('commit','0'*40),('tree','0'*40),('unit','foreign.service'),('generation','bad')):
            value=fixture();value.generation=None;value.work_acknowledged=False
            frame={'commit':'a'*40,'tree':'b'*40,'unit':UNIT,'generation':'c'*64,'actors':dict.fromkeys(observer._ROLES),
                   'generation_identity':[1,2]};frame[key]=changed
            with self.subTest(key=key),patch.object(observer,'_manager') as manager,self.assertRaises(boot.BootstrapRefusal):value.bind_ready(frame)
            manager.assert_not_called();self.assertTrue(value.failed and value.account.uncertain)

    def test_live_actor_prevents_any_cleanup_or_resource_acceptance(self):
        value=fixture()
        with patch.object(value,'verify'),patch.object(ns.backend,'_browser_pidfd_is_terminal',return_value=False), \
             patch.object(observer,'_manager',return_value={'LoadState':'not-found'}),patch.object(os.path,'lexists',return_value=False),self.assertRaises(boot.BootstrapRefusal):value.finish_service(operation_succeeded=True)
        self.assertIsNone(value.proof)

    def test_unit_absence_without_exact_subtree_removal_is_not_cleanup(self):
        value=fixture()
        with patch.object(value,'verify'),patch.object(ns.backend,'_browser_pidfd_is_terminal',return_value=True), \
             patch.object(observer,'_manager',return_value={'LoadState':'not-found'}),patch.object(os.path,'lexists',return_value=True), \
             patch.object(observer.time,'monotonic',side_effect=[0,6]), \
             self.assertRaises(boot.BootstrapRefusal):value.finish_service(operation_succeeded=True)
        self.assertIsNone(value.proof)

    def test_unknown_descendant_is_preserved_and_prevents_cleanup(self):
        value=fixture()
        with patch.object(value,'verify'),patch.object(ns.backend,'_browser_pidfd_is_terminal',return_value=True), \
             patch.object(observer,'_manager',return_value={'LoadState':'not-found'}),patch.object(os.path,'lexists',return_value=False), \
             patch.object(observer,'_removed'),patch.object(observer,'_children',return_value=['foreign']), \
             patch.object(boot,'_collect_native_client') as command,self.assertRaises(boot.BootstrapRefusal):value.finish_service(operation_succeeded=True)
        command.assert_not_called();self.assertIsNone(value.proof)

    def test_failed_operation_can_have_independent_cleanup_without_acceptance(self):
        value=fixture();data=counters()
        with patch.object(value,'verify'),patch.object(ns.backend,'_browser_pidfd_is_terminal',return_value=True), \
             patch.object(observer,'_manager',return_value={'LoadState':'not-found'}),patch.object(os.path,'lexists',return_value=False), \
             patch.object(observer,'_removed'),patch.object(observer,'_children',return_value=[]), \
             patch.object(value,'read',side_effect=data.__getitem__),patch('hosted_synthetic_identity.identity_processes',return_value=[]):
            proof=value.finish_service(operation_succeeded=False)
        self.assertFalse(proof.operation_succeeded)
        self.assertEqual(proof.aggregate['peak'],1234)
        with self.assertRaises(boot.BootstrapRefusal):observer.require_drain_proof(proof,value.account)  # envelope still exists

    def test_copied_or_unowned_proof_cannot_release_identity(self):
        value=fixture();proof=observer.DrainProof('c'*64,'a'*40,'b'*40,UNIT,900,900,{},False,value,observer._TOKEN)
        value.proof=proof;value.aggregate=proof.aggregate;value.closed=True;value.descriptors_closed_successfully=True
        observer.require_drain_proof(proof,value.account)
        for selected in (replace(proof),replace(proof,uid=901),replace(proof,_token=object()),{}):
            with self.subTest(selected=type(selected)),self.assertRaises(boot.BootstrapRefusal):observer.require_drain_proof(selected,value.account)

    def test_last_pre_stop_sample_replaces_provisional_drain_evidence(self):
        value=fixture();data=counters()
        with patch.object(value,'verify'),patch.object(observer,'_await_absence'),patch.object(observer,'_removed'), \
             patch.object(observer,'_children',return_value=[]),patch.object(value,'read',side_effect=data.__getitem__), \
             patch('hosted_synthetic_identity.identity_processes',return_value=[]), \
             patch.object(boot,'_collect_native_client',return_value=(0,b'')) as native:
            provisional=value.finish_service(operation_succeeded=False)
            self.assertEqual(provisional.aggregate['peak'],1234)
            data['memory.peak']=b'2048\n';data['memory.current']=b'128\n'
            value.close()
        native.assert_called_once()
        self.assertEqual(value.proof.aggregate['peak'],2048)
        self.assertEqual(value.proof.aggregate['current'],128)
        observer.require_drain_proof(value.proof,value.account)
        with self.assertRaises(boot.BootstrapRefusal):observer.require_drain_proof(provisional,value.account)

    def test_failed_final_sample_cannot_stop_slice_or_accept_earlier_proof(self):
        value=fixture();data=counters()
        with patch.object(value,'verify'),patch.object(observer,'_await_absence'),patch.object(observer,'_removed'), \
             patch.object(observer,'_children',return_value=[]),patch.object(value,'read',side_effect=data.__getitem__), \
             patch('hosted_synthetic_identity.identity_processes',return_value=[]), \
             patch.object(boot,'_collect_native_client') as native:
            provisional=value.finish_service(operation_succeeded=False)
            data['memory.events']=b'low 0\nhigh 0\nmax 0\noom 1\noom_kill 0\noom_group_kill 0\n'
            with self.assertRaises(boot.BootstrapCleanupError):value.close()
        native.assert_not_called()
        self.assertTrue(value.closed and value.failed and value.account.uncertain)
        with self.assertRaises(boot.BootstrapRefusal):observer.require_drain_proof(provisional,value.account)

    def test_drain_proof_cannot_export_a_different_aggregate(self):
        value=fixture();aggregate={'peak':1};value.aggregate={'peak':2}
        value.proof=observer.DrainProof('c'*64,'a'*40,'b'*40,UNIT,900,900,aggregate,False,value,observer._TOKEN)
        value.closed=True;value.descriptors_closed_successfully=True
        with self.assertRaises(boot.BootstrapRefusal):observer.require_drain_proof(value.proof,value.account)

    def test_manager_schema_rejects_unknown_properties_and_changed_limits(self):
        base={'Id':observer.slice_name(UNIT),'LoadState':'loaded','ActiveState':'active',
              'ControlGroup':'/system.slice/'+observer.slice_name(UNIT),'InvocationID':'a'*32,
              'MemoryMax':'536870912','TasksMax':'64'}
        for key,changed in (('MemoryMax','max'),('TasksMax','65'),('InvocationID','0'*32),('Unexpected','x')):
            values=dict(base,**{key:changed});raw=''.join(f'{k}={v}\n' for k,v in values.items()).encode()
            with self.subTest(key=key),patch.object(observer.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=raw)), \
                 self.assertRaises(boot.BootstrapRefusal):observer._manager(observer.slice_name(UNIT))

    def test_collector_uses_real_pipe_ack_and_removes_only_one_control_frame(self):
        frame={'generation':'a'*64}
        acknowledgment=observer._PREFIX+b'{"ack":"'+b'a'*64+b'"}\n'
        control=observer._PREFIX+json.dumps(frame,sort_keys=True,separators=(',',':')).encode()+b'\n'
        script='import os;os.write(1,'+repr(control)+');assert os.read(0,257)=='+repr(acknowledgment)+';print("synthetic final report")'
        selected=Mock();selected.bind_ready.return_value=acknowledgment
        code,raw=boot._collect_native_client([sys.executable,'-I','-S','-B','-c',script],cwd='/tmp',observer=selected)
        self.assertEqual((code,raw),(0,b'synthetic final report\n'))
        selected.bind_ready.assert_called_once_with(frame);selected.acknowledge_sent.assert_called_once()

    def test_collector_bind_refusal_closes_client_without_ack(self):
        script='import os;os.write(1,'+repr(observer._PREFIX+b'{}\n')+');os.read(0,257)'
        selected=Mock();selected.bind_ready.side_effect=boot.BootstrapRefusal('synthetic bind refusal')
        with self.assertRaisesRegex(boot.BootstrapRefusal,'bind refusal'):
            boot._collect_native_client([sys.executable,'-I','-S','-B','-c',script],cwd='/tmp',observer=selected)
        selected.acknowledge_sent.assert_not_called()

    def test_real_supervisor_channel_rejects_missing_wrong_and_extra_ack(self):
        # Transport component only: no service/cgroup/resource proof is mocked
        # into a PASS. The outside observer never accepts this synthetic frame.
        script=r'''
import sys,time
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import hosted_synthetic_observer as o
unit=sys.argv[2]
try:
 o.await_external_observer(unit=unit,generation='a'*64,binding=SimpleNamespace(commit='b'*40,tree='c'*40),
  lease=SimpleNamespace(delegation=Path('/sys/fs/cgroup'+o.service_path(unit)),child=SimpleNamespace(identity=(1,2))),
  guardian=101,guardian_start=1,outer={'init_pid':102,'init_start':2,'worker_pid':103,'worker_start':3},deadline=time.monotonic()+.2)
except o.boot.BootstrapRefusal:raise SystemExit(3)
raise SystemExit(0)
'''
        correct=observer._PREFIX+json.dumps({'ack':'a'*64},sort_keys=True,separators=(',',':')).encode()+b'\n'
        for supplied,code in ((correct,0),(b'',3),(correct.replace(b'a'*64,b'b'*64),3),(correct+b'extra\n',3)):
            with self.subTest(code=code,bytes=len(supplied)):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',script,str(Path(observer.__file__).parent),UNIT],
                    input=supplied,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=3,
                    env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'})
                self.assertEqual(result.returncode,code,result.stderr.decode())
                self.assertTrue(result.stdout.startswith(observer._PREFIX))

    def test_deleted_dentry_proof_does_not_assume_kernfs_nlink_zero(self):
        directory=Mock(number=10);path='/sys/fs/cgroup/system.slice/owned'
        with patch.object(os.path,'lexists',return_value=False),patch.object(ns,'_mount_id',return_value=3), \
             patch.object(cg,'_filesystem'),patch.object(os,'readlink',return_value=path+' (deleted)'):
            observer._removed(directory,path,3)
            with patch.object(os,'readlink',return_value=path),self.assertRaises(boot.BootstrapRefusal):observer._removed(directory,path,3)

    def test_changed_invocation_is_not_stopped_or_retried(self):
        with patch.object(observer,'_manager',return_value={'LoadState':'loaded','InvocationID':'b'*32}), \
             patch.object(boot,'_collect_native_client') as native,patch.object(observer.time,'sleep') as sleep, \
             self.assertRaisesRegex(boot.BootstrapRefusal,'unit_replaced'):
            observer._await_absence(UNIT,'/sys/fs/cgroup/unused','a'*32)
        native.assert_not_called();sleep.assert_not_called()


if __name__=='__main__':unittest.main()
