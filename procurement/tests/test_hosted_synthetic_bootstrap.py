"""Native bootstrap plan and fault tests; no sudo/systemd execution occurs."""
import base64
import contextlib
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_bootstrap as boot
import hosted_synthetic_launcher as launcher
import hosted_synthetic_namespace as ns

UNIT = 'buffalo-hosted-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.service'
UID = 1001
GID = 1001


def status(**changes):
    fields = {'Uid':'1001 1001 1001 1001', 'Gid':'1001 1001 1001 1001', 'Groups':'', 'NoNewPrivs':'1',
              **{key:'0000000000000000' for key in boot._CAPABILITIES}}
    fields.update(changes)
    return ''.join(f'{key}:\t{value}\n' for key,value in fields.items()).encode()


def plan(**changes):
    values = dict(root='/work/repo', python='/usr/bin/python3.13', repository='Vedang1998/Buffalo-Procurement-System',
                  event='push', ref='refs/heads/codex/hosted-synthetic-acceptance', commit='a'*40,
                  uid=UID, gid=GID, unit=UNIT, initial_user_namespace=(5,123))
    values.update(changes)
    return boot.plan_native_bootstrap(**values)


class HostedSyntheticBootstrapTests(unittest.TestCase):
    def test_every_supervisor_entry_rejects_retained_authority_before_effects(self):
        import hosted_synthetic_capability as capability
        import hosted_synthetic_host as host
        import hosted_synthetic_user_service as service
        import hosted_synthetic_cgroup as cg
        mutations = [{'Groups':'1001'}, {'NoNewPrivs':'0'}]
        for name in ('Uid','Gid'):
            for index in range(4):
                values=['1001']*4;values[index]='0'
                mutations.append({name:' '.join(values)})
        mutations.extend({key:'0000000000000001'} for key in boot._CAPABILITIES)
        for changed in mutations:
            with self.subTest(changed=changed), \
                 patch.object(launcher,'open',create=True,side_effect=lambda *a,**k: io.BytesIO(status(**changed))), \
                 patch.object(launcher.os,'geteuid',return_value=UID),patch.object(launcher.os,'getegid',return_value=GID), \
                 patch.object(capability,'_require_clean_supervisor'),patch.object(cg,'_signal_boundary'), \
                 patch.object(capability,'bind_hosted_source') as binding,patch.object(ns.os,'fork') as fork, \
                 patch.object(cg,'_open_directory') as opening,patch.object(host,'_program') as program:
                for suffix in ([],['--user-service-unit',UNIT],['--native-bootstrap',UNIT,'1001','1001','5','123']):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(capability.main(['repo','push','ref','a'*40,*suffix]),1)
                    self.assertEqual(json.loads(output.getvalue().splitlines()[0])['reason'],'POSTDROP_PREREQUISITE_REFUSED')
                for invoke in (lambda: ns.observe_private_proc('a'*64),
                               lambda: capability._namespace_attempt(Mock(),'a'*64),
                               lambda: service.prepare_user_service.__wrapped__(UNIT),
                               lambda: cg.CgroupLease.create(Path('/sys/fs/cgroup/unit'),generation='a'*64,memory_max=4096),
                               host.observe_host_prerequisites.__wrapped__):
                    with self.assertRaises(launcher.LauncherRefusal):invoke()
                binding.assert_not_called();fork.assert_not_called();opening.assert_not_called();program.assert_not_called()

    def test_host_readback_success_requires_libc_ids_and_empty_groups_too(self):
        with patch.object(launcher,'open',create=True,side_effect=lambda *a,**k:io.BytesIO(status())), \
             patch.object(launcher.os,'getresuid',return_value=(UID,)*3), \
             patch.object(launcher.os,'getresgid',return_value=(GID,)*3), \
             patch.object(launcher.os,'getgroups',return_value=[]):
            self.assertEqual(launcher.inspect_host_identity(uid=UID,gid=GID)['capabilities'],
                             {key:0 for key in boot._CAPABILITIES})
            with patch.object(launcher.os,'getgroups',return_value=[GID]),self.assertRaises(launcher.LauncherRefusal):
                launcher.inspect_host_identity(uid=UID,gid=GID)

    def test_cli_never_opens_review_gate_from_args_or_environment(self):
        with patch.object(boot, 'run_native_plan') as execute, \
             patch.dict(os.environ, {'BOOTSTRAP_REVIEW':'PASS'}):
            for arguments in ([], ['--execute'], ['--reviewed','PASS']):
                with self.subTest(arguments=arguments), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(boot.main(arguments),78)
                self.assertEqual(json.loads(output.getvalue())['privileged_processes_started'],0)
        execute.assert_not_called()

    def test_actual_isolated_entry_from_other_directory_is_review_required(self):
        result=subprocess.run([sys.executable,'-I','-S','-B',boot.__file__,'--execute','--reviewed=PASS'],
                              cwd='/tmp',env=boot._ENVIRONMENT,capture_output=True,timeout=5)
        self.assertEqual(result.returncode,78,result.stderr.decode())
        self.assertEqual(json.loads(result.stdout)['bootstrap_execution'],'DISABLED_PENDING_INDEPENDENT_REVIEW')
        self.assertEqual(result.stderr,b'')

    def test_launcher_bad_postdrop_status_never_dispatches_repository_child(self):
        args=['repo','push','ref','a'*40,'--native-bootstrap',UNIT,'1001','1001','5','123']
        for changed in ({'Uid':'1001 1001 0 1001'},{'Gid':'1001 1001 1001 0'}, {'Groups':'1001'},
                        {'NoNewPrivs':'0'},*({key:'0000000000000001'} for key in boot._CAPABILITIES)):
            with self.subTest(changed=changed),patch.object(launcher.os,'getuid',return_value=UID), \
                 patch.object(launcher.os,'geteuid',return_value=UID), \
                 patch.object(launcher,'open',create=True,return_value=io.BytesIO(status(**changed))), \
                 patch.object(launcher.subprocess,'run') as run:
                self.assertEqual(launcher.main(args),78)
            run.assert_not_called()

    def test_only_native_commands_run_before_drop_without_root_shell(self):
        selected = plan()
        self.assertEqual(selected.argv[:4],('/usr/bin/sudo','-n','--','/usr/bin/env'))
        command = next(arg for arg in selected.argv if arg.startswith('--property=ExecStartEx='))
        self.assertTrue(command.startswith('--property=ExecStartEx=:!'))
        chain = shlex.split(command.split('=:!',1)[1])
        self.assertEqual(chain[:5],['/usr/bin/setpriv','--clear-groups','--no-new-privs','--','/usr/bin/setpriv'])
        self.assertEqual(chain[5:13],['--reuid=1001','--regid=1001','--keep-groups','--inh-caps=-all',
                                    '--ambient-caps=-all','--bounding-set=-all','--no-new-privs','--'])
        self.assertEqual(chain[13:18],['/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8','/usr/bin/python3.13'])
        self.assertNotIn('/bin/sh',chain)
        self.assertNotIn('/bin/bash',chain)
        self.assertEqual(selected.argv[-2:],('--','/usr/bin/true'))

    def test_oneshot_bounds_and_minimal_preface_capabilities(self):
        argv=plan().argv
        for argument in ('--service-type=oneshot','--property=TimeoutStartSec=120s','--property=TimeoutStopSec=5s',
                         '--property=NoNewPrivileges=yes','--property=MemoryMax=512M','--property=TasksMax=64',
                         '--property=CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP',
                         '--property=AmbientCapabilities=','--property=Delegate=memory pids',
                         '--property=DelegateSubgroup=supervisor','--property=KillMode=control-group',
                         '--property=CPUAccounting=no','--property=IOAccounting=no', '--wait','--pipe','--collect'):
            self.assertIn(argument,argv)
        self.assertFalse(any('RuntimeMax' in arg or 'SYS_ADMIN' in arg or 'SYS_PTRACE' in arg for arg in argv))
        self.assertFalse(any('ExecStartPre' in arg or 'ExecStop' in arg or 'Restart=' in arg for arg in argv))

    def test_source_scope_unit_and_argument_injection_refuse(self):
        cases = ({'root':'/work/%n'},{'root':'/work/../other'},{'python':'/bin/sh -c'},
                 {'root':'/work/line\nnext'},{'uid':0},{'uid':True},{'gid':0},
                 {'unit':'main.service'},{'event':'pull_request'},{'commit':'A'*40},
                 {'repository':'other/repo'},{'initial_user_namespace':(0,123)})
        for values in cases:
            with self.subTest(values=values), self.assertRaises(boot.BootstrapRefusal): plan(**values)
        for value in ('a\nb','a\0b','%n','\x7f','é'):
            with self.subTest(value=value), self.assertRaises(boot.BootstrapRefusal): boot._quote(value)
        for value in ('a"b','a\\b','$VALUE','ordinary token'):
            self.assertEqual(shlex.split(boot._quote(value)),[value])

    def test_exact_zero_capabilities_nnp_and_four_identity_fields(self):
        result=boot.parse_postdrop_status(status(),uid=UID,gid=GID)
        self.assertEqual(result['uids'],[UID]*4)
        self.assertEqual(result['gids'],[GID]*4)
        self.assertEqual(result['groups'],[])
        self.assertEqual(result['capabilities'],{name:0 for name in boot._CAPABILITIES})
        self.assertEqual(result['no_new_privs'],1)

    def test_every_retained_capability_and_nnp_zero_refuses(self):
        for name in boot._CAPABILITIES:
            for value in ('0000000000000001','0000000000280000','ffffffffffffffff'):
                with self.subTest(name=name,value=value), self.assertRaises(boot.BootstrapRefusal):
                    boot.parse_postdrop_status(status(**{name:value}),uid=UID,gid=GID)
        with self.assertRaises(boot.BootstrapRefusal): boot.parse_postdrop_status(status(NoNewPrivs='0'),uid=UID,gid=GID)

    def test_any_wrong_real_effective_saved_or_filesystem_id_refuses(self):
        for field in ('Uid','Gid'):
            for index in range(4):
                values=['1001']*4;values[index]='0'
                with self.subTest(field=field,index=index), self.assertRaises(boot.BootstrapRefusal):
                    boot.parse_postdrop_status(status(**{field:' '.join(values)}),uid=UID,gid=GID)
        for groups in ('1001','0','65534 65534'):
            with self.subTest(groups=groups), self.assertRaises(boot.BootstrapRefusal):
                boot.parse_postdrop_status(status(Groups=groups),uid=UID,gid=GID)

    def test_malformed_or_duplicate_kernel_fields_refuse(self):
        for raw in (b'',status()[:-1],status()+b'CapEff: 0000000000000000\n',status()+b'\xff: bad\n',
                    status(CapEff='0'),status(Uid='1001 1001 1001'),status(NoNewPrivs='true'),
                    status().replace(b'CapBnd:',b'Missing:'),status()+b'X:' + b'a'*16384+b'\n'):
            with self.subTest(size=len(raw)), self.assertRaises(boot.BootstrapRefusal):
                boot.parse_postdrop_status(raw,uid=UID,gid=GID)

    def test_same_process_namespace_and_exact_system_service_scope(self):
        current=f'/system.slice/system-buffaloaaaaaaaabbbbccccddddeeeeeeeeeeee.slice/{UNIT}/supervisor'
        with patch.object(boot.os,'getresuid',return_value=(UID,)*3), \
             patch.object(boot.os,'getresgid',return_value=(GID,)*3), \
             patch.object(boot.os,'getgroups',return_value=[]), \
             patch.object(launcher,'open',create=True,side_effect=lambda path,mode:io.BytesIO(status() if path.endswith('/status') else ('0::'+current+'\n').encode())), \
             patch.object(boot.os,'stat',return_value=Mock(st_dev=5,st_ino=123)), \
             patch.object(ns,'_process',return_value={'pid':42,'start':99}):
            result=boot.verify_postdrop_identity.__wrapped__(uid=UID,gid=GID,unit=UNIT,initial_user_namespace=(5,123))
            self.assertEqual(result['process'],{'pid':42,'start':99})
            self.assertEqual(result['cgroup'],current)
            for changed in ('/system.slice/other.service/supervisor',f'/system.slice/{UNIT}',f'/user.slice/{UNIT}/supervisor'):
                current=changed
                with self.subTest(current=current), self.assertRaises(boot.BootstrapRefusal):
                    boot.verify_postdrop_identity.__wrapped__(uid=UID,gid=GID,unit=UNIT,initial_user_namespace=(5,123))
            current=f'/system.slice/system-buffaloaaaaaaaabbbbccccddddeeeeeeeeeeee.slice/{UNIT}/supervisor'
            with self.assertRaises(boot.BootstrapRefusal):
                boot.verify_postdrop_identity.__wrapped__(uid=UID,gid=GID,unit=UNIT,initial_user_namespace=(5,999))

    def test_postdrop_launcher_never_dispatches_as_root(self):
        with patch.object(launcher.os,'getuid',return_value=0), patch.object(launcher.subprocess,'run') as run:
            self.assertEqual(launcher.main(['ignored']*10),78)
        run.assert_not_called()

    def test_launcher_controls_environment_descriptors_and_timeout(self):
        args=['repo','push','ref','a'*40,'--native-bootstrap',UNIT,'1001','1001','5','123']
        with patch.object(launcher.os,'getuid',return_value=UID),patch.object(launcher.os,'geteuid',return_value=UID), \
             patch.object(launcher,'inspect_postdrop_identity',return_value={}), \
             patch.object(launcher.os,'fstat',return_value=Mock(st_mode=0o010600)), \
             patch.object(launcher.subprocess,'run',return_value=Mock(returncode=1)) as run:
            self.assertEqual(launcher.main(args),1)
            self.assertTrue(run.call_args.kwargs['close_fds'])
            self.assertEqual(run.call_args.kwargs['env'],boot._ENVIRONMENT)
            self.assertEqual(run.call_args.kwargs['timeout'],110)
            self.assertEqual(run.call_args.kwargs['stdin'],0)
            run.side_effect=subprocess.TimeoutExpired('synthetic',110)
            self.assertEqual(launcher.main(args),124)

    def test_client_bounds_real_unprivileged_output_and_reaps(self):
        code,raw=boot._collect_native_client([sys.executable,'-I','-S','-B','-c',"import sys;print('synthetic');sys.exit(1)"],cwd='/tmp')
        self.assertEqual((code,raw),(1,b'synthetic\n'))
        for source in ("import sys;sys.stdout.write('x'*65537)","import sys;sys.stderr.write('x'*4097)"):
            with self.subTest(source=source),self.assertRaises(boot.BootstrapRefusal):
                boot._collect_native_client([sys.executable,'-I','-S','-B','-c',source],cwd='/tmp')

    def test_client_creation_failure_starts_no_cleanup_command(self):
        with patch.object(boot.subprocess,'Popen',side_effect=OSError('synthetic')) as popen, self.assertRaises(OSError):
            boot._collect_native_client(plan().argv,cwd='/tmp')
        popen.assert_called_once()

    def test_readonly_lifecycle_never_stops_unbound_service(self):
        for code in (0,1):
            with patch.object(boot.subprocess,'run',return_value=Mock(returncode=code,stdout=b'not-found\n')) as run:
                self.assertEqual(boot._observe_unit_absence(UNIT),'NOT_FOUND_OBSERVED')
            self.assertIn('show',run.call_args.args[0])
            self.assertNotIn('sudo',' '.join(run.call_args.args[0]))
        with patch.object(boot.subprocess,'run',return_value=Mock(returncode=124,stdout=b'not-found\n')), \
             patch.object(boot.time,'sleep'),self.assertRaises(boot.BootstrapRefusal):
            boot._observe_unit_absence(UNIT)

    def test_report_requires_exact_source_digest_and_no_release_authority(self):
        selected=plan()
        report={'commit':selected.source[3],'repository':selected.source[0],'ref':selected.source[2],
                'execution_authority':False,'provider_calls':0,'payload_processes':0,'credential_release':'WITHHELD',
                'contract':'BUFFALO_HOSTED_NAMESPACE_DIAGNOSTIC_V1','HOST_CAPABILITY':'FAIL','sentinel_exec':'NOT_RUN',
                'runtime':'HOST_RUNTIME_UNSEALED','railway_compatibility':'NOT_PROVEN'}
        def encoded(value):
            first=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
            return first+b'\nevidence_sha256='+hashlib.sha256(first).hexdigest().encode()+b'\n'
        self.assertEqual(boot._decode_report(encoded(report),selected),report)
        for key,value in (('commit','b'*40),('provider_calls',1),('payload_processes',1),('execution_authority',True),('credential_release','RELEASED'),('HOST_CAPABILITY','PASS'),('contract','other'),('provider_calls',False),('payload_processes',False)):
            with self.subTest(key=key),self.assertRaises(boot.BootstrapRefusal):
                boot._decode_report(encoded(dict(report,**{key:value})),selected)
        with self.assertRaises(boot.BootstrapRefusal):boot._decode_report(encoded(report)+b'extra\n',selected)

    def test_modified_plan_cannot_reach_native_client(self):
        with patch('hosted_synthetic_capability._require_clean_supervisor'), \
             patch.object(boot,'_collect_native_client') as client, self.assertRaises(boot.BootstrapRefusal):
            boot.run_native_plan(replace(plan(),argv=('/usr/bin/sudo','/bin/sh')))
        client.assert_not_called()

    def _platform(self, *, controllers=b'cpu memory pids\n', file_caps=False, tool_writable=False):
        from types import SimpleNamespace
        import stat
        import hosted_synthetic_cgroup as cg
        def info(mode, size=4):
            return SimpleNamespace(st_uid=0,st_gid=0,st_mode=mode,st_size=size,st_dev=1,st_ino=100,
                                   st_mtime_ns=1,st_ctime_ns=1)
        file_info=info(stat.S_IFREG | (0o777 if tool_writable else 0o755))
        dir_info=info(stat.S_IFDIR | 0o755)
        owned=Mock(number=10,identity=(1,100))
        self.enterContext(patch.object(boot.os,'getuid',return_value=UID))
        self.enterContext(patch.object(boot.os,'geteuid',return_value=UID))
        self.enterContext(patch.object(Path,'lstat',lambda p:file_info if p.name in boot._NATIVE else dir_info))
        self.enterContext(patch.object(boot.os,'lstat',return_value=dir_info))
        self.enterContext(patch.object(boot.os,'fstat',return_value=file_info))
        self.enterContext(patch.object(ns,'_acquire',side_effect=lambda operation:owned))
        self.enterContext(patch.object(boot.os,'pread',return_value=b'ELF\0'))
        self.enterContext(patch.object(boot.os,'getxattr',side_effect=None if file_caps else OSError(61,'no attribute'),return_value=b'file cap'))
        self.enterContext(patch.object(cg,'_filesystem'))
        self.enterContext(patch.object(cg,'_read_at',side_effect=lambda fd,name:controllers if name=='cgroup.subtree_control' else b'domain\n'))
        self.enterContext(patch.object(ns,'_mount_id',return_value=5))
        return owned

    def test_native_tools_and_existing_ancestor_controllers_are_read_only(self):
        self._platform()
        with patch.object(boot.os,'write') as write,patch.object(boot.os,'chmod') as chmod, \
             patch.object(boot.subprocess,'Popen') as process:
            result=boot.inspect_native_prerequisites.__wrapped__()
        self.assertEqual(set(result['programs']),set(boot._NATIVE))
        self.assertEqual(set(result['ancestors']),{'/sys/fs/cgroup','/sys/fs/cgroup/system.slice'})
        self.assertFalse(result['execution_authority'])
        write.assert_not_called();chmod.assert_not_called();process.assert_not_called()

    def test_missing_ancestor_controller_refuses_without_repair(self):
        self._platform(controllers=b'pids\n')
        with patch.object(boot.os,'write') as write,self.assertRaisesRegex(boot.BootstrapRefusal,'ancestor'):
            boot.inspect_native_prerequisites.__wrapped__()
        write.assert_not_called()

    def test_native_program_file_capability_or_writable_tool_refuses(self):
        self._platform(file_caps=True)
        with self.assertRaisesRegex(boot.BootstrapRefusal,'file_capability'):
            boot.inspect_native_prerequisites.__wrapped__()
        with patch.object(boot.os,'getxattr',side_effect=OSError(61,'none')), \
             patch.object(Path,'lstat',return_value=Mock(st_mode=0o100777,st_uid=0)), \
             self.assertRaises(boot.BootstrapRefusal):
            boot.inspect_native_prerequisites.__wrapped__()

    def test_postdrop_access_closes_handles_and_never_writes(self):
        import hosted_synthetic_cgroup as cg
        workspace=Mock(number=10,identity=(1,10));directory=Mock(number=11,identity=(1,11));control=Mock(number=12)
        with patch.object(ns,'_acquire',side_effect=[workspace,control,control]), \
             patch.object(cg,'_open_directory',return_value=directory), \
             patch.object(boot.os,'fstat',return_value=Mock(st_uid=UID,st_mode=0o40755)), \
             patch.object(boot.os,'geteuid',return_value=UID),patch.object(boot.os,'access',return_value=True), \
             patch.object(ns,'_mount_id',return_value=5), \
             patch.object(cg,'_read_at',side_effect=lambda fd,name:b'536870912\n' if name=='memory.max' else b'64\n'), \
             patch.object(boot.os,'write') as write:
            result=boot.verify_postdrop_access.__wrapped__(Path('/work/repo'),UNIT)
        self.assertEqual(result['controller_writes'],0)
        self.assertTrue(result['delegation_control_open'])
        write.assert_not_called();workspace.close.assert_called_once();directory.close.assert_called_once()
        self.assertEqual(control.close.call_count,2)

    def test_postdrop_access_cleanup_failure_overrides_access_failure(self):
        workspace=Mock(number=10,identity=(1,10));workspace.close.side_effect=OSError('close')
        with patch.object(ns,'_acquire',return_value=workspace), \
             patch.object(boot.os,'fstat',return_value=Mock(st_uid=0,st_mode=0o40755)), \
             patch.object(boot.os,'geteuid',return_value=UID),self.assertRaises(boot.BootstrapCleanupError):
            boot.verify_postdrop_access.__wrapped__(Path('/work/repo'),UNIT)

    def _owned_run(self, failure):
        from types import SimpleNamespace
        import hosted_synthetic_identity as identity
        account=object.__new__(identity.AccountLease);account._owner=account
        account.service_attempted=False;account.service_acknowledged=False;account.journal=Mock();account.uncertain=False
        account.identity=identity.DedicatedIdentity(UNIT.removeprefix('buffalo-hosted-').removesuffix('.service'),950,951,UID,GID)
        selected=plan(uid=950,gid=951,root=account.identity.root+'/repo');events=[]
        binding=Mock(tree='b'*40,files=())
        report={'commit':'a'*40,'tree':'b'*40,'source_files':{},'repository':selected.source[0],'ref':selected.source[2],
                'execution_authority':False,'provider_calls':0,'payload_processes':0,'credential_release':'WITHHELD',
                'contract':'BUFFALO_HOSTED_NAMESPACE_DIAGNOSTIC_V1','HOST_CAPABILITY':'FAIL','sentinel_exec':'NOT_RUN',
                'runtime':'HOST_RUNTIME_UNSEALED','railway_compatibility':'NOT_PROVEN'}
        report.update(reason='INCOMPLETE_PREFLIGHT',namespace_attempt={'cleanup_complete':True,'reason':'INCOMPLETE_PREFLIGHT','resource_cleanup':{'generation_child_removed':True}})
        if failure=='resource_cleanup':report.update(reason='CLEANUP_FAILED',namespace_attempt={'cleanup_complete':False})
        first=json.dumps(report,sort_keys=True,separators=(',',':')).encode()
        raw=first+b'\nevidence_sha256='+hashlib.sha256(first).hexdigest().encode()+b'\n'
        def client(*args,**kwargs):
            events.append('client')
            if failure=='client':raise OSError('partial native activation')
            if failure=='cancel':raise KeyboardInterrupt()
            if failure=='deferred_cancel':os.kill(os.getpid(),signal.SIGINT)
            if failure=='timeout':return 124,raw
            return 1,raw
        observed=Mock(proof=None,work_acknowledged=True,failed=False,closed=False)
        def finish(**kwargs):
            events.append('drain')
            observed.proof=SimpleNamespace(generation='c'*64,source_commit='a'*40,source_tree='b'*40,
                aggregate={},operation_succeeded=kwargs['operation_succeeded'])
        observed.finish_service.side_effect=finish
        def close():
            events.append('external_close');observed.closed=True
            if failure=='cleanup':
                observed.failed=True
                raise boot.BootstrapCleanupError('unit not collected')
        observed.close.side_effect=close
        def inspect():
            events.append('inspect')
            return {'ancestors':failure=='drift' and events.count('inspect')==2}
        with patch('hosted_synthetic_capability._require_clean_supervisor'), \
             patch.object(account,'verify_root'), \
             patch('hosted_synthetic_source.bind_hosted_source',return_value=binding), \
             patch.object(boot.os,'getresuid',return_value=(UID,)*3),patch.object(boot.os,'getresgid',return_value=(GID,)*3), \
             patch.object(boot.os,'stat',return_value=Mock(st_dev=5,st_ino=123)), \
             patch.object(boot,'inspect_native_prerequisites',side_effect=inspect), \
             patch.object(boot,'_collect_native_client',side_effect=client), \
             patch('hosted_synthetic_observer.ExternalObserver.create',return_value=observed):
            try:
                invoke=boot.run_native_plan if failure=='deferred_cancel' else boot.run_native_plan.__wrapped__
                return invoke(selected,source_root='/work/source',account=account)
            finally:
                self.assertEqual(events,['inspect','client','drain','external_close','inspect'])

    def test_partial_client_failure_or_timeout_keeps_cleanup_obligations(self):
        for failure in ('client','timeout'):
            with self.subTest(failure=failure),self.assertRaises((OSError,boot.BootstrapRefusal)):
                self._owned_run(failure)

    def test_cancellation_preserves_cleanup_and_cannot_be_a_pass(self):
        with self.assertRaises(KeyboardInterrupt):self._owned_run('cancel')

    def test_actual_sigint_is_deferred_until_owned_cleanup_observations(self):
        before=signal.pthread_sigmask(signal.SIG_BLOCK,[])
        with self.assertRaises(KeyboardInterrupt):self._owned_run('deferred_cancel')
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK,[]),before)

    def test_drift_or_unknown_service_lifecycle_is_cleanup_failure(self):
        for failure in ('drift','cleanup','resource_cleanup'):
            with self.subTest(failure=failure),self.assertRaises(boot.BootstrapCleanupError):self._owned_run(failure)

    def test_successful_transport_is_only_component_evidence(self):
        result=self._owned_run(None)
        self.assertFalse(result['execution_authority'])
        self.assertEqual(result['runtime_closure'],'UNSEALED')
        self.assertEqual(result['service_absence'],'EXACT_OWNED_SUBTREE_REMOVAL_OBSERVED')
        self.assertEqual(result['client_returncode'],1)



if __name__=='__main__':unittest.main()
