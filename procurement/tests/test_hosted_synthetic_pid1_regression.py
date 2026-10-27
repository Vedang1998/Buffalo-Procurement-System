"""Driver refusal/accounting tests; real PID1 execution is a separate run."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

import hosted_synthetic_pid1_regression as live


class HostedPid1DriverTests(unittest.TestCase):
    def test_ordinary_or_host_root_invocation_is_inert(self):
        with patch.object(live.subprocess,'run') as run:
            for args in ([],['--execute'],['--run-pid1-regressions','extra']):
                self.assertEqual(live.main(args),78)
            with patch.object(live.os,'geteuid',return_value=0):
                self.assertEqual(live.main(['--run-pid1-regressions']),78)
        run.assert_not_called()

    def test_unavailable_namespace_is_not_run_never_a_passing_test(self):
        with patch.object(live.os,'geteuid',return_value=1001), \
             patch.object(live.subprocess,'run',return_value=Mock(returncode=1)) as run, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(live.main(['--run-pid1-regressions']),78)
        self.assertEqual(run.call_count,1)
        self.assertEqual(json.loads(out.getvalue())['real_pid1_regression'],'NOT_RUN')

    def test_child_assertion_failure_is_fail_not_expected_refusal(self):
        with patch.object(live.os,'geteuid',return_value=1001), \
             patch.object(live.subprocess,'run',side_effect=[Mock(returncode=0),Mock(returncode=1)]) as run, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(live.main(['--run-pid1-regressions']),1)
        result=json.loads(out.getvalue())
        self.assertEqual(result['real_pid1_regression'],'FAIL')
        self.assertEqual(result['completed_cases'],[])
        self.assertEqual(run.call_count,2)

    def test_unknown_case_refuses_before_namespace_or_fork(self):
        with patch.object(live.subprocess,'Popen') as fork,self.assertRaises(AssertionError):
            live._case('not-a-reviewed-case')
        fork.assert_not_called()


class NondumpableReadbackDriverTests(unittest.TestCase):
    """Driver simulations only; these do not execute the real namespace cases."""
    def setUp(self):
        self.enterContext(patch.object(live.os,'geteuid',return_value=1001))
        self.identity=self.enterContext(patch('hosted_synthetic_launcher.inspect_host_identity',return_value={}))
        self.binding=SimpleNamespace(commit='a'*40,tree='b'*40)
        self.source=self.enterContext(patch.object(live,'_readback_source',return_value=self.binding))
        self.out=self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    @staticmethod
    def result(case):
        proof=dict(execution_authority=False,scope='STOPPED_NAMESPACE_READBACK_ONLY',
            init=dict(pid=102,parent=101,group=101,session=101,start=10,state='T'),
            guardian=dict(pid=101,parent=100,group=101,session=101,start=9,state='T'),
            init_namespaces=dict(user=[1,2],net=[1,3],mnt=[1,4],pid=[1,5]),
            guardian_namespaces=dict(user=[1,2],net=[1,3],mnt=[1,6],pid=[1,7]),
            supervisor_namespaces=dict(user=[1,8],net=[1,9],mnt=[1,10],pid=[1,7]),
            supervisor_identity=dict(uids=[1001]*4,gids=[1001]*4,groups=[],no_new_privs=1,
                capabilities={key:0 for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')}))
        return dict(case=case,result='PASS',real_clone_newpid=True,cgroup_required=True,
            init_pid1_observed=True,cleanup_complete=True,private_proc_executed=False,
            provider_calls=0,execution_authority=False,guardian_and_init_dumpable_zero=True,
            simulated_cgroup_writes=int(case=='readback_reexec'),fork_only_refusal=case=='readback_fork_only',
            namespace_readback=(proof if case=='readback_reexec' else None))

    def invoke(self,results=None):
        results=results if results is not None else [self.result(case) for case in live.READBACK_CASES]
        with patch.object(live.subprocess,'run',side_effect=[Mock(returncode=0),
                *[Mock(returncode=0,stdout=json.dumps(result)) for result in results]]) as run:
            status=live.main(['--run-nondumpable-readback'])
        return status,json.loads(self.out.getvalue()),run

    def test_zero_capability_entry_failure_is_not_run_before_probe(self):
        from hosted_synthetic_launcher import LauncherRefusal
        self.identity.side_effect=LauncherRefusal('groups')
        with patch.object(live.subprocess,'run') as run:
            self.assertEqual(live.main(['--run-nondumpable-readback']),78)
        run.assert_not_called();self.source.assert_not_called()
        self.assertEqual(json.loads(self.out.getvalue())['real_pid1_regression'],'NOT_RUN')

    def test_dirty_or_unbound_source_refuses_before_probe(self):
        from hosted_synthetic_source import HostedSourceError
        self.source.side_effect=HostedSourceError('dirty')
        with patch.object(live.subprocess,'run') as run:
            self.assertEqual(live.main(['--run-nondumpable-readback']),78)
        run.assert_not_called()

    def test_unsupported_net_namespace_is_not_a_simulated_pass(self):
        with patch.object(live.subprocess,'run',return_value=Mock(returncode=1)) as run:
            self.assertEqual(live.main(['--run-nondumpable-readback']),78)
        self.assertIn('--net',run.call_args.args[0]);self.assertEqual(run.call_count,1)
        self.assertEqual(json.loads(self.out.getvalue())['real_pid1_regression'],'NOT_RUN')

    def test_success_binds_source_and_keeps_migration_and_runtime_unproven(self):
        status,result,run=self.invoke()
        self.assertEqual(status,0);self.assertEqual(run.call_count,3)
        self.assertEqual(self.source.call_count,2)
        self.assertEqual(result['source_commit'],self.binding.commit)
        self.assertEqual(result['source_tree'],self.binding.tree)
        self.assertEqual(result['cgroup_migration'],'NOT_RUN')
        self.assertEqual(result['runtime_closure'],'NOT_PROVEN')
        self.assertFalse(result['execution_authority'])
        self.assertEqual([r['case'] for r in result['cases']],list(live.READBACK_CASES))

    def test_source_drift_after_cases_is_failure(self):
        self.source.side_effect=[self.binding,SimpleNamespace(commit='c'*40,tree='d'*40)]
        status,result,_=self.invoke()
        self.assertEqual(status,1);self.assertEqual(result['reason'],'SOURCE_CHANGED')

    def test_source_read_failure_after_cases_is_failure(self):
        self.source.side_effect=[self.binding,OSError('unreadable')]
        status,result,_=self.invoke()
        self.assertEqual(status,1);self.assertEqual(result['reason'],'SOURCE_CHANGED')

    def test_positive_needs_actual_readback_report(self):
        first=self.result('readback_reexec');first['namespace_readback']=None
        status,result,_=self.invoke([first])
        self.assertEqual(status,1);self.assertEqual(result['reason'],'INVALID_CASE_RESULT')

    def test_positive_needs_retained_process_namespace_and_identity_records(self):
        for key in ('init','guardian','init_namespaces','guardian_namespaces','supervisor_namespaces','supervisor_identity'):
            result=self.result('readback_reexec');del result['namespace_readback'][key]
            with self.subTest(key=key),self.assertRaises(AssertionError):live._case_result('readback_reexec',result)
        result=self.result('readback_reexec');result['namespace_readback']['supervisor_identity']['capabilities']['CapBnd']=1
        with self.assertRaises(AssertionError):live._case_result('readback_reexec',result)

    def test_probe_timeout_reports_failure_with_unproven_cleanup(self):
        with patch.object(live.subprocess,'run',side_effect=live.subprocess.TimeoutExpired('probe',5)):
            self.assertEqual(live.main(['--run-nondumpable-readback']),1)
        result=json.loads(self.out.getvalue())
        self.assertEqual(result['reason'],'CAPABILITY_PROBE_INCOMPLETE')
        self.assertEqual(result['cleanup'],'NOT_PROVEN')

    def test_case_start_failure_retains_source_and_withholds_cleanup(self):
        with patch.object(live.subprocess,'run',side_effect=[Mock(returncode=0),OSError('start')]):
            self.assertEqual(live.main(['--run-nondumpable-readback']),1)
        result=json.loads(self.out.getvalue())
        self.assertEqual(result['source_commit'],self.binding.commit)
        self.assertEqual(result['reason'],'CASE_INCOMPLETE');self.assertEqual(result['cleanup'],'NOT_PROVEN')

    def test_negative_cannot_accept_an_unrelated_refusal(self):
        second=self.result('readback_fork_only');second['fork_only_refusal']=False
        status,result,_=self.invoke([self.result('readback_reexec'),second])
        self.assertEqual(status,1);self.assertEqual(result['case'],'readback_fork_only')

    def test_negative_never_reports_migration(self):
        second=self.result('readback_fork_only');second['simulated_cgroup_writes']=1
        status,result,_=self.invoke([self.result('readback_reexec'),second])
        self.assertEqual(status,1);self.assertEqual(result['reason'],'INVALID_CASE_RESULT')

    def test_child_assertion_failure_stops_before_second_case(self):
        with patch.object(live.subprocess,'run',side_effect=[Mock(returncode=0),Mock(returncode=1)]) as run:
            self.assertEqual(live.main(['--run-nondumpable-readback']),1)
        self.assertEqual(run.call_count,2)
        self.assertEqual(json.loads(self.out.getvalue())['completed_cases'],[])

    def test_cleanup_or_privilege_claim_cannot_be_accepted_as_pass(self):
        for changes in ({'cleanup_complete':False},{'execution_authority':True},
                        {'guardian_and_init_dumpable_zero':False},{'provider_calls':True},
                        {'private_proc_executed':True},{'case':'success'}):
            with self.subTest(changes=changes),self.assertRaises(AssertionError):
                live._case_result('readback_reexec',dict(self.result('readback_reexec'),**changes))

    def test_malformed_child_output_is_failure(self):
        with patch.object(live.subprocess,'run',side_effect=[Mock(returncode=0),Mock(returncode=0,stdout='{')]):
            self.assertEqual(live.main(['--run-nondumpable-readback']),1)
        self.assertEqual(json.loads(self.out.getvalue())['reason'],'INVALID_CASE_RESULT')

    def test_positive_executes_in_user_namespace_negative_preserves_old_mm(self):
        positive=live._guardian_command('readback_reexec',10,11,12,99,123)
        negative=live._guardian_command('readback_fork_only',10,11,12,99,123)
        self.assertEqual(positive[:7],['/usr/bin/unshare','--user','--map-root-user','--mount','--pid','--net','--'])
        self.assertEqual(positive[7:12],negative[:5])
        self.assertEqual(negative[0],live.sys.executable)
        self.assertNotIn('/usr/bin/unshare',negative)

    def test_fork_only_maps_before_dumpability_hardening_but_after_death_binding(self):
        events=[]
        def opened(path,flags):
            events.append(path);return 99
        def hardened(parent):
            events.append('hardened');raise RuntimeError('end before child creation')
        with patch.object(live.os,'set_inheritable'),patch.object(live.ns._Descriptor,'capture'), \
             patch.object(live.os,'dup2'),patch.object(live.os,'close'), \
             patch.object(live.os,'getegid',return_value=1001),patch.object(live.os,'getgroups',return_value=[]), \
             patch.object(live.os,'getppid',return_value=123), \
             patch.object(live.ns.backend,'_browser_prctl',side_effect=lambda *a:events.append(a)), \
             patch.object(live.os,'unshare',side_effect=lambda _:events.append('unshare')), \
             patch.object(live.os,'open',side_effect=opened), \
             patch.object(live.os,'write',side_effect=lambda fd,raw:len(raw)), \
             patch.object(live.ns.backend,'_block_browser_projection_signals'), \
             patch.object(live.ns,'_hardening',side_effect=hardened),self.assertRaisesRegex(RuntimeError,'end before'):
            live._guardian(['readback_fork_only','10','11','12','99','123'])
        self.assertEqual(events,[(1,int(live.signal.SIGKILL)),'unshare',
            '/proc/self/uid_map','/proc/self/setgroups','/proc/self/gid_map','hardened'])

    def test_real_case_checks_supervisor_identity_before_descriptor_allocation(self):
        from hosted_synthetic_launcher import LauncherRefusal
        self.identity.side_effect=LauncherRefusal('capability')
        with patch.object(live.ns,'_pipe') as pipe,self.assertRaises(LauncherRefusal):
            live._case('readback_reexec')
        pipe.assert_not_called()

    def test_host_root_readback_invocation_is_inert(self):
        with patch.object(live.os,'geteuid',return_value=0),patch.object(live.subprocess,'run') as run:
            self.assertEqual(live.main(['--run-nondumpable-readback']),78)
        run.assert_not_called();self.identity.assert_not_called()


if __name__=='__main__':unittest.main()
