"""Protocol/ownership causal tests. No sealed PostgreSQL or namespace execution."""
import os
from types import SimpleNamespace
import signal
import time
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_postgres_version as version
import hosted_synthetic_postgres_image as image
import hosted_synthetic_namespace as ns
from hosted_synthetic_source import _resource_operation

GEN='a'*64

class PostgresVersionTests(unittest.TestCase):
    def lease(self):
        value=object.__new__(image.VersionImage)
        value.spec=SimpleNamespace(role='postgres',generation=GEN,executable='/usr/lib/postgresql/16/bin/postgres',
            executable_sha256='b'*64,bundle_sha256='c'*64,source_commit='d'*40,source_tree='e'*40)
        value.descriptor=SimpleNamespace(number=80)
        value.verify=Mock(return_value={'image_identity':[1,2]})
        return value

    def execute_worker(self,change=None):
        value=self.lease();request=version._request(value.spec,42,value.verify(),80)
        changed=dict(request)
        if change:changed[change]='wrong'
        with patch.object(ns,'_process',return_value={'start':42}),patch.object(ns,'_send') as sent,patch.object(ns,'_receive',return_value={'request':changed}),patch.object(os,'execve') as execute,patch.object(os,'supports_fd',{os.execve}):
            if change:
                with self.assertRaises(ns.NamespaceProofError):version._exec(value,Mock(),Mock(),time.monotonic()+2)
                execute.assert_not_called()
            else:
                with self.assertRaisesRegex(version.VersionRefusal,'EXEC_RETURNED'):version._exec(value,Mock(),Mock(),time.monotonic()+2)
                execute.assert_called_once_with(80,[value.spec.executable,'--version'],version.ENV)
        self.assertEqual(sent.call_args.args[2],'PG_ARMED')

    def test_only_fixed_fd_argv_and_environment_after_ack(self):self.execute_worker()
    def test_wrong_role_ack_refuses_exec(self):self.execute_worker('role')
    def test_wrong_start_ack_refuses_exec(self):self.execute_worker('start')
    def test_wrong_image_ack_refuses_exec(self):self.execute_worker('image_identity')
    def test_wrong_bundle_ack_refuses_exec(self):self.execute_worker('bundle_sha256')
    def test_wrong_source_ack_refuses_exec(self):self.execute_worker('source_commit')

    def test_image_drift_after_ack_prevents_exec(self):
        value=self.lease();request=version._request(value.spec,42,{'image_identity':[1,2]},80)
        value.verify.side_effect=[{'image_identity':[1,2]},ns.NamespaceProofError('image_drift')]
        with patch.object(ns,'_process',return_value={'start':42}),patch.object(ns,'_send'),patch.object(ns,'_receive',return_value={'request':request}),patch.object(os,'execve') as execute,self.assertRaisesRegex(ns.NamespaceProofError,'image_drift'):version._exec(value,Mock(),Mock(),time.monotonic()+2)
        execute.assert_not_called()

    def test_missing_ack_refuses_exec(self):
        value=self.lease()
        with patch.object(ns,'_process',return_value={'start':42}),patch.object(ns,'_send'),patch.object(ns,'_receive',side_effect=TimeoutError()),patch.object(os,'execve') as execute,self.assertRaises(TimeoutError):version._exec(value,Mock(),Mock(),time.monotonic()+.1)
        execute.assert_not_called()

    def test_real_pipe_wrong_generation_refuses_before_exec(self):self.pipe_protocol('wrong')
    def test_real_pipe_missing_ack_is_bounded(self):self.pipe_protocol('missing')
    def test_real_parent_stop_and_ack_protocol_only(self):self.pipe_protocol('positive')
    def pipe_protocol(self,mode):
        @_resource_operation
        def run():
            gate_r,gate_w=ns._pipe();status_r,status_w=ns._pipe();marker_r,marker_w=ns._pipe()
            child=os.fork();pidfd=None
            try:
                if child==0:
                    try:
                        gate_w.close();status_r.close();marker_r.close()
                        def simulated(fd,argv,env):os.write(marker_w.number,b'EXEC_CALL');os._exit(0)
                        with patch.object(os,'execve',side_effect=simulated),patch.object(os,'supports_fd',{os.execve}):
                            version._exec(self.lease(),gate_r,status_w,time.monotonic()+.25)
                    except BaseException:os._exit(125)
                    finally:os._exit(126)
                gate_r.close();status_w.close();marker_w.close();pidfd=ns._pidfd(child)
                request=version._armed(status_r,GEN,time.monotonic()+2)['request']
                if mode!='missing':
                    signal.pidfd_send_signal(pidfd.number,signal.SIGSTOP);ns._wait(child,time.monotonic()+2,stopped=True)
                    self.assertEqual(ns._process(child)['state'],'T')
                    ns._send(gate_w,'f'*64 if mode=='wrong' else GEN,'PG_EXEC',time.monotonic()+2,request=request)
                    signal.pidfd_send_signal(pidfd.number,signal.SIGCONT)
                terminal=version._read_exit(child,time.monotonic()+2)
                self.assertEqual(terminal.si_status,0 if mode=='positive' else 125)
                self.assertEqual(os.read(marker_r.number,64),b'EXEC_CALL' if mode=='positive' else b'')
                os.waitpid(child,0);child=-1
            finally:
                if child>0:ns._kill_unreaped_child(child,pidfd)
                for descriptor in (pidfd,gate_r,gate_w,status_r,status_w,marker_r,marker_w):
                    if descriptor is not None:descriptor.close()
        run()

    def test_valid_fixed_postgres_version_output(self):
        result=version.validate_output({'stdout':b'postgres (PostgreSQL) 16.14 (Ubuntu 16.14-1.pgdg24.04+1)\n','stderr':b'','protocol':b''},SimpleNamespace(si_code=os.CLD_EXITED,si_status=0),GEN)
        self.assertTrue(result.startswith('postgres (PostgreSQL) 16.14'))

    def test_wrong_major_or_arbitrary_output_refuses(self):
        for raw in (b'postgres (PostgreSQL) 17.1\n',b'pg_dump (PostgreSQL) 16.1\n',b'OK\n',b'postgres (PostgreSQL) 16.1\nextra\n'):
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):version.validate_output({'stdout':raw,'stderr':b'','protocol':b''},SimpleNamespace(si_code=os.CLD_EXITED,si_status=0),GEN)

    def test_signal_or_nonzero_exit_never_passes(self):
        for code,status in ((os.CLD_EXITED,125),(os.CLD_KILLED,9)):
            with self.subTest(code=code),self.assertRaisesRegex(version.VersionRefusal,'NONZERO_EXIT'):version.validate_output({'stdout':b'','stderr':b'','protocol':b''},SimpleNamespace(si_code=code,si_status=status),GEN)

    def refusal(self,generation=GEN):
        return {'protocol':ns.backend._BROWSER_CONTAINMENT_PROTOCOL,'generation':generation,'stage':'PG_REFUSED','code':'EXEC_OR_KERNEL_REFUSED','errno':errno_value()}

    def test_explicit_worker_refusal_distinct_from_harness_exit(self):
        raw=ns.backend.encode_browser_worker_frame(self.refusal(),maximum_bytes=1024)
        with self.assertRaisesRegex(version.VersionRefusal,'EXPLICIT_EXEC_OR_KERNEL_REFUSED:13'):
            version.validate_output({'stdout':b'','stderr':b'','protocol':raw},None,GEN)

    def test_early_refusal_retains_explicit_reason(self):
        with patch.object(ns.backend,'_read_browser_guardian_frame',return_value=self.refusal()),self.assertRaisesRegex(version.VersionRefusal,'EXPLICIT_EXEC_OR_KERNEL_REFUSED'):version._armed(Mock(),GEN,time.monotonic()+2)

    def test_refusal_wrong_generation_is_not_attributed(self):
        raw=ns.backend.encode_browser_worker_frame(self.refusal('f'*64),maximum_bytes=1024)
        with self.assertRaises(ns.NamespaceProofError):version.validate_output({'stdout':b'','stderr':b'','protocol':raw},None,GEN)

    def test_output_bound_real_pipe(self):
        read,write=ns._pipe()
        try:
            os.write(write.number,b'x'*1025);write.close()
            with self.assertRaisesRegex(version.VersionRefusal,'OUTPUT_BOUND'):version._collect({'stdout':read},time.monotonic()+1)
        finally:read.close();write.close()

    def test_output_deadline_real_pipe(self):
        read,write=ns._pipe()
        try:
            with self.assertRaisesRegex(version.VersionRefusal,'OUTPUT_TIMEOUT'):version._collect({'stdout':read},time.monotonic()+.01)
        finally:read.close();write.close()

    def supervisor(self):
        value=object.__new__(version.StoppedVersion);value._owner=value;value.owner_pid=os.getpid()
        value.closed=False;value.cleanup_failed=False;value.executed=False;value.pid=-1;value.pidfd=None;value.descriptors=[]
        value.spec=self.lease().spec;value.deadline=time.monotonic()+1;value.request={}
        return value

    def test_outer_ack_failure_closes_owned_worker(self):
        value=self.supervisor();value.attest=Mock(return_value={});value.close=Mock()
        with patch.object(ns,'_send'),patch.object(ns,'_receive',side_effect=TimeoutError()),self.assertRaises(TimeoutError):value.execute(Mock(),Mock())
        value.close.assert_called_once()

    def test_cancel_preserves_original_failure_and_closes(self):
        value=self.supervisor();value.attest=Mock(side_effect=KeyboardInterrupt());value.close=Mock()
        with self.assertRaises(KeyboardInterrupt):value.execute(Mock(),Mock())
        value.close.assert_called_once()

    def test_cleanup_failure_is_sticky_and_not_pass(self):
        value=self.supervisor();value.attest=Mock(side_effect=ValueError('original'));value.close=Mock(side_effect=RuntimeError('cleanup'))
        with self.assertRaises(ns.NamespaceCleanupError) as caught:value.execute(Mock(),Mock())
        self.assertEqual(str(caught.exception.__cause__),'original')

    def test_failed_signal_boundary_closes_fds_without_signals_or_reap(self):
        value=self.supervisor();value.pid=99;value.pidfd=Mock();value.descriptors=[Mock(),Mock()]
        with patch.object(version,'_signal_boundary',side_effect=RuntimeError('boundary')),patch.object(os,'waitid') as wait,patch.object(signal,'pidfd_send_signal') as kill,self.assertRaises(ns.NamespaceCleanupError):value.close()
        wait.assert_not_called();kill.assert_not_called();value.pidfd.close.assert_called_once()
        for fd in value.descriptors:fd.close.assert_called_once()
        with self.assertRaises(ns.NamespaceProofError):value.close()

    def test_no_child_ownership_refuses_before_numeric_signal(self):
        value=self.supervisor();value.pid=99
        with patch.object(version,'_signal_boundary'),patch.object(os,'waitid',side_effect=ChildProcessError()),patch.object(os,'kill') as kill,self.assertRaises(ns.NamespaceCleanupError):value.close()
        kill.assert_not_called()

    def test_copied_lease_cannot_close(self):
        import copy
        value=copy.copy(self.supervisor())
        with self.assertRaises(ns.NamespaceProofError):value.close()

    def test_init_mount_clone_rebinds_id_and_reverifies_consumer(self):
        descriptor=Mock(number=8,identity=(4,5));old={'root_identity':[4,5],'root_mount_id':21}
        with patch.object(version,'_signal_boundary'),patch.object(os,'getpid',return_value=1),patch.object(os,'getppid',return_value=0),patch.object(ns,'_require_mapped_identity'),patch.object(ns,'_acquire',return_value=descriptor),patch.object(ns,'_identity',return_value=(4,5)),patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0x01021994),patch.object(ns,'_mount_id',return_value=44),patch.object(image.pg,'verify_postgres_phase_consumer') as verify:
            value=Mock();current=version.rebind_init_root(value,old)
        self.assertEqual(current['root_mount_id'],44);self.assertEqual(old['root_mount_id'],21)
        verify.assert_called_once_with(value,current);descriptor.close.assert_called_once()

    def test_missing_private_mount_transition_refuses_before_consumer(self):
        descriptor=Mock(number=8,identity=(4,5));old={'root_identity':[4,5],'root_mount_id':21}
        with patch.object(version,'_signal_boundary'),patch.object(os,'getpid',return_value=1),patch.object(os,'getppid',return_value=0),patch.object(ns,'_require_mapped_identity'),patch.object(ns,'_acquire',return_value=descriptor),patch.object(ns,'_identity',return_value=(4,5)),patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0x01021994),patch.object(ns,'_mount_id',return_value=21),patch.object(image.pg,'verify_postgres_phase_consumer') as verify,self.assertRaises(ns.NamespaceProofError):version.rebind_init_root(Mock(),old)
        verify.assert_not_called();descriptor.close.assert_called_once()

    def test_native_refusal_survives_generic_guardian_frames(self):
        read,write=ns._pipe()
        try:
            ns._child_failure(write,GEN,time.monotonic()+1,version.VersionRefusal('EXPLICIT_EXEC_OR_KERNEL_REFUSED:13'))
            with self.assertRaisesRegex(ns.NamespaceProofError,'_pg_explicit_exec_or_kernel_refused:13'):
                ns._receive(read,GEN,'CLEAN',time.monotonic()+1)
        finally:read.close();write.close()

    def test_fixed_pg_activation_stays_dormant_and_selects_only_pg(self):
        import hosted_synthetic_identity as host
        import hosted_synthetic_bootstrap as boot
        with patch.object(host,'activation_entry',return_value={}) as activate:
            host.postgres_version_activation_entry(['repo','event','ref','commit'])
        activate.assert_called_once_with(['repo','event','ref','commit'],diagnostic='postgres-version')
        with patch.object(host,'postgres_version_activation_entry') as live,patch('builtins.print'):
            self.assertEqual(boot.main(['--native-bootstrap-postgres-version']),78)
        live.assert_not_called()

    def test_unreviewed_mode_cannot_create_account(self):
        import hosted_synthetic_identity as host
        with patch.object(host.AccountLease,'create') as create,self.assertRaises(Exception):
            host.run_provisioned_candidate(None,source='/source',python='/usr/bin/python3',repository='repo',event='push',ref='ref',commit='a'*40,diagnostic='shell')
        create.assert_not_called()

    def test_pg_observer_requires_delegation_and_external_observer_before_fork(self):
        with patch('hosted_synthetic_capability._require_clean_supervisor'),patch('hosted_synthetic_launcher.inspect_host_identity'),patch.object(os,'fork') as fork,self.assertRaises(ns.NamespaceProofError):
            ns.observe_private_proc(GEN,postgres_version=True)
        fork.assert_not_called()

    def test_actual_stopped_state_is_required_before_namespace_or_image_reads(self):
        value=self.supervisor();value.pid=2;value.pidfd=Mock(number=8)
        value.request={'start':42};value.spec.init_start=50
        with patch.object(version,'_signal_boundary'),patch.object(os,'getpid',return_value=1),patch.object(os,'getppid',return_value=0),patch.object(ns,'_require_mapped_identity'),patch.object(ns.backend,'_browser_pidfd_is_terminal',return_value=False),patch.object(ns.backend,'_read_browser_pidfd_metadata',return_value=(None,2,None,None)),patch.object(ns,'_process',return_value={'pid':2,'state':'S','parent':1,'start':42}),patch.object(version.identity,'_namespaces') as read,self.assertRaises(ns.NamespaceProofError):version._attest(value)
        read.assert_not_called()


    def attest_fixture(self,mutation=None):
        import hashlib
        value=self.supervisor();value.pid=2;value.pidfd=Mock(number=8)
        spec=value.spec;spec.init_start=50;spec.root_identity=(4,5);spec.root_mount=44;spec.filter_count=3
        spec.namespaces=tuple((name,(4,n)) for n,name in enumerate(('user','mnt','pid','net','cgroup'),10))
        spec.executable_sha256=hashlib.sha256(b'image').hexdigest()
        value.request={'start':42,'image_fd':9,'image_identity':[4,9]}
        value.child_gate=7;value.child_status=8
        value.gate=Mock(identity=(1,7));value.status=Mock(identity=(1,8))
        value.stdout=Mock(identity=(1,1));value.stderr=Mock(identity=(1,2))
        current={'pid':2,'state':'T','parent':1,'start':42}
        status={'uid':[0]*4,'gid':[0]*4,'groups':[],'no_new_privs':1,'nspid':[2],
            **{key:0 for key in version.worker._CAPS},'Seccomp':2,'Seccomp_filters':3}
        if mutation=='caps':status['CapEff']=1
        if mutation=='uid':status['uid']=[1000]*4
        if mutation=='groups':status['groups']=[1]
        if mutation=='filter':status['Seccomp_filters']=2
        def read(path,limit):
            if path==spec.executable:return b'image'
            if path=='/proc/self/task/1/children':return b'2'
            if path=='/proc/2/task/2/children':return b'3' if mutation=='descendant' else b''
            if path.endswith('_map'):return b'0 0 1\n' if mutation=='mapping' else b'1000 0 1\n'
            if path.endswith('setgroups'):return b'deny\n'
            if path.endswith('cgroup'):return b'0::/owned\n'
            raise AssertionError(path)
        def info(path):
            if path in (spec.executable,'/proc/2/fd/9'):return SimpleNamespace(st_dev=4,st_ino=9,st_mode=0o100555)
            if '/fd/' in path:return SimpleNamespace(st_dev=1,st_ino=int(path.rsplit('/',1)[1]),st_mode=0o10600)
            return SimpleNamespace(st_dev=4,st_ino=5)
        def fdmeta(pid,number):
            flags=(os.O_RDONLY if number in (7,9) else os.O_WRONLY)|(os.O_CLOEXEC if number not in (1,2) else 0)
            return 0,flags,44,number
        names=dict((k,list(v)) for k,v in spec.namespaces);names['user']=[4,999]
        if mutation=='namespace':names['mnt']=[4,999]
        root=Mock(number=6,identity=(4,5))
        with patch.object(version,'_signal_boundary'),patch.object(os,'getpid',return_value=1),patch.object(os,'getppid',return_value=0),patch.object(ns,'_require_mapped_identity'),patch.object(ns.backend,'_browser_pidfd_is_terminal',return_value=False),patch.object(ns.backend,'_read_browser_pidfd_metadata',return_value=(None,2,None,None)),patch.object(ns,'_process',side_effect=lambda pid:current if pid==2 else {'start':50}),patch.object(os,'listdir',side_effect=lambda path:['2'] if path.endswith('/task') else ['0','1','2','7','8','9']),patch.object(ns,'_read',side_effect=read),patch.object(version.worker,'_status',return_value=status),patch.object(version.identity,'_namespaces',return_value=names),patch.object(ns,'_acquire',return_value=root),patch.object(os,'stat',side_effect=info),patch.object(os,'fstatvfs',return_value=SimpleNamespace(f_flag=os.ST_RDONLY|os.ST_NOSUID|os.ST_NODEV)),patch.object(ns,'_mount_id',return_value=44),patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0x01021994),patch.object(ns.backend,'_read_browser_worker_fd_metadata',side_effect=fdmeta),patch.object(os,'readlink',return_value='/dev/null'):
            return version._attest(value)

    def test_parent_attests_zero_uid_in_u1_and_distinct_worker_user_namespace(self):
        result=self.attest_fixture();self.assertEqual(result['status']['uid'],[0]*4)
        self.assertEqual(result['scope'],'ACTUAL_STOPPED_PG_WORKER')

    def test_parent_rejects_credential_namespace_mapping_or_descendant_drift(self):
        for mutation in ('caps','uid','groups','filter','mapping','namespace','descendant'):
            with self.subTest(mutation=mutation),self.assertRaises(ns.NamespaceProofError):self.attest_fixture(mutation)


    def test_existing_lifecycle_injections_forward_fixed_pg_mode(self):
        import hosted_synthetic_lifecycle as lifecycle
        from hosted_synthetic_source import HostedSourceBinding
        binding=HostedSourceBinding('a'*40,'b'*40,())
        for case in lifecycle.CASES:
            with self.subTest(case=case),patch('hosted_synthetic_capability._require_clean_supervisor'),patch.object(lifecycle.source,'bind_hosted_source',return_value=binding),patch.object(lifecycle,'LifecycleCase') as fault,patch.object(lifecycle.identity,'run_provisioned_candidate',return_value={}) as run:
                fault.return_value.finish.return_value={}
                result=lifecycle.run_lifecycle_case(case,object(),root='/fixture',python='/usr/bin/python3.13',repository='repo',event='push',ref='ref',commit='a'*40,diagnostic='postgres-version')
                self.assertEqual(run.call_args.kwargs['diagnostic'],'postgres-version')
                self.assertIs(run.call_args.kwargs['lifecycle'],fault.return_value)
                self.assertEqual(result['diagnostic'],'FIXED_POSTGRES_VERSION')


def errno_value():return 13
if __name__=='__main__':unittest.main()
