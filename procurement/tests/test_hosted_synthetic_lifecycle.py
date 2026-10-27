"""Dormant causal fault wiring; no privileged native operation executes."""
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_lifecycle as life
import hosted_synthetic_identity as identity
import hosted_synthetic_observer as observer
import hosted_synthetic_bootstrap as boot
import hosted_synthetic_source as source

ID=identity.DedicatedIdentity('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',950,951,os.geteuid(),os.getegid())
BINDING=source.HostedSourceBinding('a'*40,'b'*40,())


class LifecycleTests(unittest.TestCase):
    def fixture(self,case):
        value=life.LifecycleCase(case,BINDING,ID)
        account=object.__new__(identity.AccountLease)
        account.identity=ID;account._owner=account;account.source_commit=BINDING.commit
        account.uncertain=False;account.closed=False;account.drain_proof=None;account.service_attempted=False;account.platform={}
        value.account_created(account)
        observed=object.__new__(observer.ExternalObserver)
        observed.account=account;observed.binding=BINDING;observed.work_acknowledged=False;observed.closed=False;observed.descriptors_closed_successfully=False
        observed.generation='c'*64;observed.unit=ID.unit;observed.actors={};observed.service_invocation='d'*32
        value.observer_created(observed)
        return value,account,observed

    def test_unknown_case_refuses_before_source_or_native_access(self):
        with patch('hosted_synthetic_capability._require_clean_supervisor'),patch.object(source,'bind_hosted_source') as bind,patch.object(identity,'run_provisioned_candidate') as run,self.assertRaises(boot.BootstrapRefusal):
            life.run_lifecycle_case('arbitrary',ID,root='/fixture',python='/usr/bin/python3.13',repository='x',event='x',ref='x',commit='a'*40)
        bind.assert_not_called();run.assert_not_called()

    def test_copied_case_cannot_operate_owned_resources(self):
        import copy
        value,_,_=self.fixture('normal');copied=copy.copy(value)
        with self.assertRaises(boot.BootstrapRefusal):copied.verify()

    def test_wrong_identity_and_commit_cannot_attach_fault(self):
        value,_,_=self.fixture('normal')
        for selected,commit in ((replace(ID,uid=952),'a'*40),(ID,'b'*40)):
            with self.subTest(),self.assertRaises(boot.BootstrapRefusal):life.require_case(value,selected,commit)

    def test_partial_setup_injection_waits_for_acknowledged_no_sudo_stage(self):
        value=life.LifecycleCase('partial_setup',BINDING,ID)
        account=object.__new__(identity.AccountLease);account._owner=account;account.identity=ID;account.source_commit=BINDING.commit
        value.account_created(account)
        for index in range(3):value.provisioned(index)
        self.assertFalse(value.partial)
        with self.assertRaises(life.PartialSetupFault):value.provisioned(3)
        self.assertEqual(value.events,['provision_ack_0','provision_ack_1','provision_ack_2','provision_ack_3'])

    def test_ack_modes_distinguish_deadline_from_eof(self):
        for case,expected in (('normal','SEND'),('timeout','HOLD'),('lost_ack','DROP')):
            value,_,observed=self.fixture(case)
            with self.subTest(case=case),patch.object(value,'_live_service'):
                self.assertEqual(value.before_ack(observed,{'actors':{'worker':{'pid':2,'start':3}}}),expected)
            self.assertFalse(observed.work_acknowledged)

    def test_repeated_generation_cannot_inject_twice(self):
        value,_,observed=self.fixture('normal')
        with patch.object(value,'_live_service'):
            value.before_ack(observed,{'actors':{'worker':{'pid':2,'start':3}}})
            with self.assertRaises(boot.BootstrapRefusal):value.before_ack(observed,{'actors':{}})

    def test_manager_kill_requires_stop_before_ack_and_kill_after_ack(self):
        value,_,observed=self.fixture('manager_kill');events=[]
        with patch.object(value,'_live_service'),patch.object(value,'_signal_service',side_effect=events.append):
            self.assertEqual(value.before_ack(observed,{'actors':{'worker':{'pid':2,'start':3}}}),'SEND')
            self.assertEqual(events,['SIGSTOP'])
            with self.assertRaises(boot.BootstrapRefusal):value.after_ack(observed)
            self.assertEqual(events,['SIGSTOP'])
            observed.work_acknowledged=True;value.after_ack(observed)
            self.assertEqual(events,['SIGSTOP','SIGKILL'])

    def test_native_signal_refuses_unbound_service_before_command(self):
        value,_,_=self.fixture('manager_kill')
        with patch.object(value,'_live_service',side_effect=boot.BootstrapRefusal('replaced')),patch.object(boot,'_collect_native_client') as run,self.assertRaises(boot.BootstrapRefusal):value._signal_service('SIGKILL')
        run.assert_not_called()

    def test_native_kill_targets_only_exact_owned_unit_and_fixed_signal(self):
        value,_,_=self.fixture('manager_kill')
        with patch.object(value,'_live_service'),patch.object(boot,'_collect_native_client',return_value=(0,b'')) as run:
            value._signal_service('SIGKILL')
        argv=run.call_args.args[0]
        self.assertEqual(argv[-7:],('/usr/bin/systemctl','--system','--no-ask-password','kill','--kill-whom=all','--signal=SIGKILL',ID.unit))
        self.assertTrue(value.kill_sent)
        with patch.object(boot,'_collect_native_client') as run,self.assertRaises(boot.BootstrapRefusal):value._signal_service('SIGCONT')
        run.assert_not_called()

    def test_unacknowledged_native_kill_does_not_claim_fault(self):
        value,_,_=self.fixture('manager_kill')
        with patch.object(value,'_live_service'),patch.object(boot,'_collect_native_client',return_value=(1,b'')),self.assertRaises(boot.BootstrapRefusal):value._signal_service('SIGKILL')
        self.assertFalse(value.kill_sent)

    def test_stopped_actors_require_actual_state_readback(self):
        value,_,_=self.fixture('manager_kill');value.actors={'worker':{'pid':2,'start':3}}
        with patch.object(value,'_live_service'),patch.object(boot,'_collect_native_client',return_value=(0,b'')),patch.object(life.ns,'_process',return_value={'state':'R'}),patch.object(life.time,'monotonic',side_effect=[0,3]),self.assertRaisesRegex(boot.BootstrapRefusal,'lifecycle_stop_unobserved'):
            value._signal_service('SIGSTOP')
        self.assertNotIn('all_bound_actors_stopped',value.events)

    def test_missing_drain_proof_never_passes_normal_or_manager_kill(self):
        for case in ('normal','manager_kill'):
            value,account,_=self.fixture(case);value.kill_sent=True
            report=value.finish({},None if case=='normal' else boot.BootstrapRefusal('killed'))
            self.assertEqual(report['assertion'],'FAIL');self.assertEqual(report['cleanup'],'UNVERIFIED')
            self.assertEqual(report['resource_acceptance'],'UNVERIFIED')

    def test_quarantine_never_becomes_resource_acceptance(self):
        value,account,observed=self.fixture('lost_ack');account.uncertain=True;observed.closed=True;observed.descriptors_closed_successfully=True
        value.child_report={'namespace_attempt':{'external_observer_failure':'external_observer_ack_refused'}}
        with patch.object(value,'_quarantined',return_value=['RESERVED']):report=value.finish(None,boot.BootstrapCleanupError('uncertain'))
        self.assertEqual(report['assertion'],'PASS');self.assertEqual(report['cleanup'],'QUARANTINED')
        self.assertEqual(report['resource_acceptance'],'UNVERIFIED');self.assertEqual(report['privileged_live_acceptance'],'INCOMPLETE')

    def test_timeout_requires_exact_source_bound_refusal_cause(self):
        value,account,observed=self.fixture('timeout');account.uncertain=True;observed.closed=True;observed.descriptors_closed_successfully=True
        value.child_report={'namespace_attempt':{'external_observer_failure':'external_observer_ack_refused'}}
        with patch.object(value,'_quarantined',return_value=['RESERVED']):report=value.finish(None,boot.BootstrapCleanupError('uncertain'))
        self.assertEqual(report['assertion'],'FAIL')

    def test_false_child_authority_and_source_drift_refuse(self):
        value,_,_=self.fixture('normal')
        valid=dict(commit=BINDING.commit,tree=BINDING.tree,provider_calls=0,credential_release='WITHHELD',execution_authority=False)
        for key,changed in (('commit','c'*40),('tree','d'*40),('provider_calls',1),('credential_release','RELEASED'),('execution_authority',True)):
            with self.subTest(key=key),self.assertRaises(boot.BootstrapRefusal):value.report_received(dict(valid,**{key:changed}))

    def test_case_is_single_use_after_evidence_finalization(self):
        value,_,_=self.fixture('normal');value.finish(None,boot.BootstrapRefusal('failure'))
        with self.assertRaises(boot.BootstrapRefusal):value.finish(None,None)

    def test_real_operator_sigint_is_deferred_through_cleanup(self):
        code=r'''
import os,signal,sys
sys.path.insert(0,sys.argv[1])
import hosted_synthetic_lifecycle as l
import hosted_synthetic_source as s
import hosted_synthetic_identity as i
from unittest.mock import patch
from types import SimpleNamespace
identity=i.DedicatedIdentity('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',950,951,os.getuid(),os.getgid())
v=l.LifecycleCase('cancellation',s.HostedSourceBinding('a'*40,'b'*40,()),identity)
v.observer=SimpleNamespace();events=[]
@s._resource_operation
def operation():
    with patch.object(v,'_live_service'):
        assert v.before_ack(v.observer,{'actors':{'worker':{'pid':2,'start':3}}})=='SEND'
    events.append('cleanup-before-signal-delivery')
try:operation()
except KeyboardInterrupt:events.append('cancellation-propagated')
else:raise AssertionError('missing actual SIGINT')
assert events==['cleanup-before-signal-delivery','cancellation-propagated'] and v.cancel_sent
print('PASS: actual deferred SIGINT; no native/service execution')
'''
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(Path(life.__file__).parent)],
            env=boot._ENVIRONMENT,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn(b'actual deferred SIGINT',result.stdout)

    def test_activation_remains_unconditionally_disabled(self):
        with patch.object(identity,'activation_entry') as run,patch('builtins.print'):
            self.assertEqual(boot.main(['--lifecycle','manager_kill']),78)
        run.assert_not_called()

    def test_failed_handle_close_cannot_be_reported_as_descriptor_proof(self):
        from test_hosted_synthetic_observer import fixture
        value,account,_=self.fixture('lost_ack');observed=fixture();observed.account=account;value.observer=observed
        observed.work_acknowledged=False;observed.actors['worker'].close.side_effect=OSError('synthetic close failure')
        with self.assertRaises(boot.BootstrapCleanupError):observed._close_descriptors()
        account.uncertain=True
        value.child_report={'namespace_attempt':{'external_observer_failure':'external_observer_ack_refused'}}
        with patch.object(value,'_quarantined',return_value=['RESERVED']):report=value.finish(None,boot.BootstrapCleanupError('uncertain'))
        self.assertFalse(report['observer_descriptors_closed']);self.assertEqual(report['assertion'],'FAIL')

    def test_actual_collector_pipe_drop_is_eof_and_hold_reaches_deadline(self):
        prefix=observer._PREFIX
        frame={'actors':{'worker':{'pid':2,'start':3}}}
        code="import sys,selectors; print(sys.argv[1],flush=True); s=selectors.DefaultSelector(); s.register(0,selectors.EVENT_READ); ready=s.select(.05); print('EOF' if ready and not sys.stdin.buffer.read(1) else 'TIMEOUT',flush=True)"
        for case,expected in (('lost_ack',b'EOF\n'),('timeout',b'TIMEOUT\n')):
            value,_,observed=self.fixture(case)
            observed.bind_ready=Mock(return_value=b'never sent\n');observed.acknowledge_sent=Mock()
            with self.subTest(case=case),patch.object(value,'_live_service'):
                status,raw=boot._collect_native_client((sys.executable,'-I','-S','-B','-c',code,
                    (prefix+json.dumps(frame,sort_keys=True,separators=(',',':')).encode()).decode()),cwd='/',observer=observed,lifecycle=value)
            self.assertEqual((status,raw),(0,expected));observed.acknowledge_sent.assert_not_called()

    def test_real_provisioning_loop_injects_after_four_acks_and_never_stages(self):
        value=life.LifecycleCase('partial_setup',BINDING,ID);commands=[];journal=Mock()
        def collect(command,**kwargs):
            commands.append(command)
            if '/usr/bin/passwd' in command:return 0,(ID.user+' L 2026-10-27 0 99999 7 -1\n').encode()
            if '-l' in command:return 1,('User '+ID.user+' is not allowed to run sudo on ephemeral-host.\n').encode()
            return 0,b''
        with patch('hosted_synthetic_account_preflight.inspect_source',return_value={}),patch('hosted_synthetic_account_preflight.inspect_ownership',return_value={}),patch.object(identity,'inspect_account_platform',return_value={}),patch.object(identity,'inspect_account'),patch.object(identity,'identity_processes',return_value=[]),patch.object(identity.os.path,'lexists',return_value=False),patch('hosted_synthetic_quarantine.Quarantine.create',return_value=journal),patch.object(boot,'_collect_native_client',side_effect=collect),self.assertRaises(boot.BootstrapCleanupError):
            identity.AccountLease.create(ID,'/fixture',BINDING.commit,lifecycle=value)
        self.assertEqual(len(commands),4);self.assertTrue(value.partial and value.lease.uncertain)
        self.assertFalse(value.lease.service_attempted);journal.close.assert_called_once()
        self.assertNotIn('/usr/bin/mkdir',[token for command in commands for token in command])

    def test_replaced_invocation_process_or_membership_blocks_native_signal(self):
        for fault in ('invocation','start','membership'):
            value,_,observed=self.fixture('manager_kill')
            roles=('launcher','supervisor','guardian','init','worker')
            value.actors={role:{'pid':100+index,'start':200+index} for index,role in enumerate(roles)}
            observed.actors={role:Mock(number=300+index) for index,role in enumerate(roles)}
            observed.verify=Mock();observed.service=Mock();observed.generation_dir=Mock()
            current={'InvocationID':('e'*32 if fault=='invocation' else observed.service_invocation),
                'ControlGroup':observer.service_path(ID.unit),'ActiveState':'activating'}
            def process(pid):
                if pid==os.getpid():return value.operator
                return {'pid':pid,'start':pid+100+(1 if fault=='start' and pid==104 else 0),'state':'T'}
            def read(path,limit):
                pid=int(path.split('/')[2]);suffix='/buffalo-'+observed.generation if pid>=103 else '/supervisor'
                return ('0::'+observer.service_path(ID.unit)+('/foreign' if fault=='membership' and pid==104 else suffix)+'\n').encode()
            with self.subTest(fault=fault),patch.object(observer,'_manager',return_value=current),patch.object(life.ns,'_process',side_effect=process),patch.object(life.ns,'_read',side_effect=read),patch.object(life.ns.backend,'_read_browser_pidfd_metadata',side_effect=lambda fd:(None,fd-200,None,None)),patch.object(life.ns.backend,'_browser_pidfd_is_terminal',return_value=False),patch.object(boot,'_collect_native_client') as execute,self.assertRaises(boot.BootstrapRefusal):
                value._signal_service('SIGKILL')
            execute.assert_not_called();self.assertFalse(value.kill_sent)
