"""Dedicated-account boundaries, with every privileged operation simulated."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_identity as identity
import hosted_synthetic_bootstrap as boot

ID=identity.DedicatedIdentity('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',950,951,1001,1001)


class HostedDedicatedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.journal=Mock()
        self.enterContext(patch('hosted_synthetic_quarantine.Quarantine.create',return_value=self.journal))

    def test_complete_owned_native_sequence_and_final_identity_removal(self):
        state={'root':None};commands=[]
        def collect(command,**kwargs):
            commands.append(command)
            if '/usr/bin/passwd' in command:return 0,(ID.user+' L 2026-10-26 0 99999 7 -1\n').encode()
            if '-l' in command:return 1,('User '+ID.user+' is not allowed to run sudo on ephemeral-host.\n').encode()
            if '/usr/bin/mkdir' in command:state['root']='root'
            if '/usr/bin/chown' in command:state['root']='account'
            if '/usr/bin/rmdir' in command:state['root']=None
            return 0,b''
        def info(path):
            self.assertEqual(path,ID.root)
            return SimpleNamespace(st_dev=5,st_ino=6,st_mode=0o40700,
                st_uid=0 if state['root']=='root' else ID.uid,st_gid=0 if state['root']=='root' else ID.gid)
        with patch.object(identity,'inspect_account_platform',return_value={'native':'synthetic-bound-hash'}), \
             patch.object(identity,'inspect_account'),patch.object(identity,'identity_processes',return_value=[]), \
             patch.object(os,'getresuid',return_value=(1001,)*3),patch.object(os,'getresgid',return_value=(1001,)*3), \
             patch.object(os,'lstat',side_effect=info), \
             patch.object(os.path,'lexists',side_effect=lambda path:state['root'] is not None if path==ID.root else False), \
             patch.object(boot,'_collect_native_client',side_effect=collect), \
             patch.object(boot,'_observe_unit_absence',return_value='NOT_FOUND_OBSERVED'), \
             patch.object(identity,'_lookup',side_effect=lambda func,value:self.group() if func in (identity.grp.getgrnam,identity.grp.getgrgid) else None):
            lease=identity.AccountLease.create(ID,'/work/source','a'*40)
            self.assertTrue(lease.created and not lease.closed and not lease.uncertain)
            lease.close_after_service()
        self.assertTrue(lease.closed and not lease.uncertain)
        self.assertEqual(len(commands),12)
        self.assertIn('/usr/bin/find',commands[8]);self.assertIn('/usr/bin/rmdir',commands[9])
        self.assertIn('/usr/sbin/userdel',commands[10]);self.assertIn('/usr/sbin/groupdel',commands[11])

    def test_shared_operator_route_cannot_reach_native_execution(self):
        plan=boot.plan_native_bootstrap(root='/work/source',python='/usr/bin/python3.13',
            repository='Vedang1998/Buffalo-Procurement-System',event='push',
            ref='refs/heads/codex/hosted-synthetic-acceptance',commit='a'*40,uid=1001,gid=1001,
            unit=ID.unit,initial_user_namespace=(5,6))
        with patch('hosted_synthetic_capability._require_clean_supervisor'), \
             patch.object(os,'stat',return_value=SimpleNamespace(st_dev=5,st_ino=6)), \
             patch.object(boot,'_collect_native_client') as execute, \
             self.assertRaisesRegex(boot.BootstrapRefusal,'dedicated_account_required'):
            boot.run_native_plan.__wrapped__(plan)
        execute.assert_not_called()

    def test_dormant_orchestration_binds_source_and_always_cleans_account(self):
        for failure in (None,'candidate','cleanup'):
            lease=self.lease();events=[]
            def create(*args):events.append('create');return lease
            def run(plan,**kwargs):
                events.append('candidate')
                self.assertEqual((plan.uid,plan.gid),(950,951))
                self.assertEqual(plan.root,ID.root+'/repo')
                self.assertEqual(kwargs,{'source_root':'/work/source','account':lease})
                if failure=='candidate':raise boot.BootstrapRefusal('candidate refusal')
                return {'report':{'tree':'b'*40},'execution_authority':False}
            def close():
                events.append('cleanup')
                if failure=='cleanup':raise boot.BootstrapCleanupError('cleanup refusal')
                lease.closed=True
            with self.subTest(failure=failure),patch('hosted_synthetic_capability._require_clean_supervisor'), \
                 patch('hosted_synthetic_source.bind_hosted_source',side_effect=lambda *a,**k:
                     (events.append('bind') or SimpleNamespace(tree='b'*40))), \
                 patch.object(identity.AccountLease,'create',side_effect=create), \
                 patch.object(boot,'run_native_plan',side_effect=run), \
                 patch.object(lease,'close_after_service',side_effect=close):
                def invoke():return identity.run_provisioned_candidate(ID,source='/work/source',python='/usr/bin/python3.13',
                    repository='Vedang1998/Buffalo-Procurement-System',event='push',ref='refs/heads/codex/hosted-synthetic-acceptance',commit='a'*40)
                if failure:
                    with self.assertRaises(boot.BootstrapRefusal):invoke()
                else:
                    result=invoke();self.assertTrue(result['dedicated_identity_removed'])
                    self.assertEqual(result['worker_control_exclusion'],'UNPROVEN')
            self.assertEqual(events,['bind','create','candidate','cleanup'])

    def test_stage_exact_detached_source_without_any_local_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();stage=root/'stage'
            env=dict(boot._ENVIRONMENT,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',GIT_TERMINAL_PROMPT='0')
            def git(*args):return subprocess.check_output(['/usr/bin/git','-c','maintenance.auto=false','-c','core.hooksPath=/dev/null',*args],cwd=source,env=env,stderr=subprocess.DEVNULL,text=True).strip()
            git('init','--initial-branch=fixture')
            (source/'synthetic.txt').write_text('synthetic source only\n');git('add','synthetic.txt')
            git('-c','user.name=Synthetic Fixture','-c','user.email=278831488+Vedang1998@users.noreply.github.com',
                '-c','commit.gpgsign=false','commit','-m','synthetic fixture')
            commit=git('rev-parse','HEAD');tree=git('rev-parse','HEAD^{tree}')
            git('checkout','--detach',commit);git('update-ref','-d','refs/heads/fixture')
            self.assertEqual(git('for-each-ref','refs/heads'),'')
            commands=identity.provisioning_commands(ID,str(source),commit)
            clone=list(commands[-2][commands[-2].index('/usr/bin/git'):]);clone[-1]=str(stage)
            subprocess.run(clone,cwd=source,env=env,capture_output=True,check=True,timeout=10)
            checkout=list(commands[-1][commands[-1].index('/usr/bin/git'):]);checkout[checkout.index('-C')+1]=str(stage)
            subprocess.run(checkout,cwd=source,env=env,capture_output=True,check=True,timeout=10)
            actual=subprocess.check_output(['/usr/bin/git','-C',str(stage),'rev-parse','HEAD','HEAD^{tree}'],env=env,text=True).splitlines()
            self.assertEqual(actual,[commit,tree]);self.assertEqual((stage/'synthetic.txt').read_bytes(),(source/'synthetic.txt').read_bytes())

    def account(self):
        return SimpleNamespace(pw_name=ID.user,pw_uid=ID.uid,pw_gid=ID.gid,
                               pw_dir='/nonexistent',pw_shell='/usr/sbin/nologin')

    def group(self):return SimpleNamespace(gr_name=ID.group,gr_gid=ID.gid,gr_mem=[])

    def lease(self):
        lease=object.__new__(identity.AccountLease)
        lease.identity=ID;lease._owner=lease;lease.created=True;lease.closed=False
        lease.uncertain=False;lease.root_identity=(5,6);lease.platform={}
        lease.service_attempted=False;lease.service_acknowledged=False
        lease.service_creation_acknowledged=False;lease.drain_proof=None;lease.source_commit='a'*40;lease.journal=Mock()
        return lease

    def test_plan_uses_only_explicit_native_commands_and_dropped_git(self):
        commands=identity.provisioning_commands(ID,'/work/source','a'*40)
        self.assertEqual(len(commands),8)
        flat=[token for command in commands for token in command]
        self.assertNotIn('--force',flat);self.assertNotIn('-R',flat);self.assertNotIn('/bin/sh',flat)
        self.assertNotIn('/bin/bash',flat);self.assertNotIn('python',flat)
        self.assertNotEqual(ID.user,ID.group)
        for command in commands:
            self.assertEqual(command[:4],('/usr/bin/sudo','-n','--','/usr/bin/env'))
            self.assertIn('15s',command)
        user=commands[1]
        for flag in ('--no-create-home','--no-user-group','--no-log-init','SUB_UID_COUNT=0','SUB_GID_COUNT=0'):
            self.assertIn(flag,user)
        for command in commands[-2:]:
            self.assertLess(command.index('--clear-groups'),command.index('--reuid=950'))
            self.assertLess(command.index('--bounding-set=-all'),command.index('/usr/bin/git'))
            self.assertIn('GIT_CONFIG_NOSYSTEM=1',command)
        self.assertIn('--no-local',commands[-2]);self.assertIn('--single-branch',commands[-2])

    def test_shared_existing_or_injected_identity_plan_never_executes(self):
        with patch.object(boot,'_collect_native_client') as execute:
            for changes in ({'uid':1001},{'gid':1001},{'operator_uid':0},{'uid':True},
                            {'uid':0},{'nonce':'../foreign'},{'gid':65534}):
                with self.subTest(changes=changes),self.assertRaises(boot.BootstrapRefusal):
                    identity.provisioning_commands(replace(ID,**changes),'/work/source','a'*40)
        execute.assert_not_called()

    def test_collision_by_any_name_or_number_refuses_before_commands(self):
        lookups=('getpwnam','getpwuid','getgrnam','getgrgid')
        for selected in lookups:
            with self.subTest(selected=selected),patch.object(identity,'_local_rows',return_value=[]), \
                 patch.object(identity,'_lookup',side_effect=lambda func,value:
                 self.account() if func.__name__==selected else None) as lookup,self.assertRaises(boot.BootstrapRefusal):
                identity.inspect_account(ID,present=False)
            self.assertEqual(lookup.call_count,4)

    def test_dormant_primary_group_or_duplicate_local_account_refuses(self):
        for users,groups in (([['dormant','x','700','951','','/nonexistent','/usr/sbin/nologin']],[]),
                             ([[ID.user,'x','950','951','','/nonexistent','/usr/sbin/nologin']]*2,
                              [[ID.group,'x','951','']])):
            with self.subTest(users=len(users)),patch.object(identity,'_local_rows',side_effect=[users,groups]), \
                 patch.object(boot,'_collect_native_client') as execute,self.assertRaises(boot.BootstrapRefusal):
                identity.inspect_account(ID,present=False)
            execute.assert_not_called()

    def test_created_account_readback_rejects_memberships_aliases_and_login_shell(self):
        for change in (None,'shell','uid','supplementary','group_members','alias'):
            account=self.account();group=self.group()
            if change=='shell':account.pw_shell='/bin/bash'
            if change=='uid':account.pw_uid=1001
            if change=='group_members':group.gr_mem=['runner']
            def lookup(func,value):
                if func is identity.pwd.getpwuid and change=='alias':return None
                return account if func in (identity.pwd.getpwnam,identity.pwd.getpwuid) else group
            rows=([[ID.user,'x','950','951','','/nonexistent','/usr/sbin/nologin']],[[ID.group,'x','951','']])
            with self.subTest(change=change),patch.object(identity,'_local_rows',side_effect=rows), \
                 patch.object(identity,'_lookup',side_effect=lookup) as observed, \
                 patch.object(os,'getgrouplist',return_value=[ID.gid,27] if change=='supplementary' else [ID.gid]) as groups:
                if change:
                    with self.assertRaises(boot.BootstrapRefusal):identity.inspect_account(ID,present=True)
                else:identity.inspect_account(ID,present=True)
            self.assertEqual(observed.call_count,4)
            if change in (None,'supplementary'):groups.assert_called_once_with(ID.user,ID.gid)
            else:groups.assert_not_called()

    def test_partial_account_creation_never_blindly_deletes_or_retries(self):
        with patch.object(identity,'inspect_account_platform',return_value={}), \
             patch.object(identity,'inspect_account'),patch.object(identity,'identity_processes',return_value=[]), \
             patch.object(os.path,'lexists',return_value=False), \
             patch.object(os,'getresuid',return_value=(1001,)*3),patch.object(os,'getresgid',return_value=(1001,)*3), \
             patch.object(boot,'_collect_native_client',side_effect=[(0,b''),(124,b'')]) as execute, \
             self.assertRaisesRegex(boot.BootstrapCleanupError,'partial_setup'):
            identity.AccountLease.create(ID,'/work/source','a'*40)
        self.assertEqual(execute.call_count,2)
        self.assertFalse(any('/usr/sbin/userdel' in call.args[0] for call in execute.call_args_list))

    def test_untrusted_native_prefix_refuses_before_first_account_command(self):
        with patch.object(boot,'inspect_native_prerequisites',side_effect=boot.BootstrapRefusal('wrapper')), \
             patch.object(os,'getresuid',return_value=(1001,)*3),patch.object(os,'getresgid',return_value=(1001,)*3), \
             patch.object(boot,'_collect_native_client') as execute,self.assertRaises(boot.BootstrapRefusal):
            identity.AccountLease.create(ID,'/work/source','a'*40)
        execute.assert_not_called()

    def test_live_resources_or_ambiguous_ownership_prevent_identity_release(self):
        for failure in ('unit','subtree','process','owner'):
            lease=self.lease()
            if failure=='owner':lease._owner=object()
            with self.subTest(failure=failure),patch.object(lease,'verify_root'), \
                 patch.object(identity,'inspect_account_platform',return_value={}) as platform, \
                 patch.object(boot,'_observe_unit_absence',return_value='UNKNOWN' if failure=='unit' else 'NOT_FOUND_OBSERVED') as unit, \
                 patch.object(os.path,'lexists',return_value=failure=='subtree') as subtree, \
                 patch.object(identity,'identity_processes',return_value=[456] if failure=='process' else []) as processes, \
                 patch.object(boot,'_collect_native_client') as execute,self.assertRaises(boot.BootstrapRefusal):
                lease.close_after_service()
            execute.assert_not_called();self.assertFalse(lease.closed)
            self.assertEqual(platform.call_count,int(failure!='owner'))
            self.assertEqual(unit.call_count,int(failure!='owner'))
            self.assertEqual(subtree.call_count,int(failure in ('subtree','process')))
            self.assertEqual(processes.call_count,int(failure=='process'))

    def test_lost_native_ack_cannot_release_identity_on_temporary_absence(self):
        lease=self.lease();lease.service_attempted=True
        with patch.object(lease,'verify_root'),patch.object(boot,'_observe_unit_absence',return_value='NOT_FOUND_OBSERVED'), \
             patch.object(identity,'identity_processes',return_value=[]),patch.object(os.path,'lexists',return_value=False), \
             patch.object(boot,'_collect_native_client') as execute,self.assertRaises(boot.BootstrapCleanupError):
            lease.close_after_service()
        execute.assert_not_called();self.assertTrue(lease.uncertain);self.assertFalse(lease.closed)

    def test_cleanup_failure_retains_account_and_does_not_force_release(self):
        lease=self.lease()
        with patch.object(lease,'verify_root'),patch.object(identity,'inspect_account'), \
             patch.object(identity,'inspect_account_platform',return_value={}), \
             patch.object(boot,'_observe_unit_absence',return_value='NOT_FOUND_OBSERVED'), \
             patch.object(os.path,'lexists',return_value=False), \
             patch.object(identity,'identity_processes',return_value=[]), \
             patch.object(boot,'_collect_native_client',return_value=(1,b'')) as execute, \
             self.assertRaises(boot.BootstrapCleanupError):lease.close_after_service()
        self.assertEqual(execute.call_count,1);self.assertTrue(lease.uncertain)
        self.assertIn('/usr/bin/find',execute.call_args.args[0])
        self.assertLess(execute.call_args.args[0].index('--reuid=950'),execute.call_args.args[0].index('/usr/bin/find'))

    def test_source_refusal_precedes_any_account_provisioning(self):
        from hosted_synthetic_source import HostedSourceError
        with patch('hosted_synthetic_capability._require_clean_supervisor'), \
             patch('hosted_synthetic_source.bind_hosted_source',side_effect=HostedSourceError('source')), \
             patch.object(identity.AccountLease,'create') as create,self.assertRaises(HostedSourceError):
            identity.run_provisioned_candidate(ID,source='/work/source',python='/usr/bin/python3',
                repository='repo',event='push',ref='ref',commit='a'*40)
        create.assert_not_called()


if __name__=='__main__':unittest.main()
