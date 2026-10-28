"""No sudo/account/IPC mutation: real disposable files and local Git only."""
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import hosted_synthetic_account_preflight as pre
import hosted_synthetic_identity as identity
import hosted_synthetic_bootstrap as boot

ID=identity.DedicatedIdentity('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',950,951,1001,1001)
MOUNTS=b'1 0 8:1 / / rw - ext4 /dev/root rw\n2 1 0:2 / /proc rw - proc proc rw\n3 1 0:3 / /sys rw - sysfs sysfs rw\n'
IPC=b'key shmid perms uid gid cuid cgid\n'
INITIAL_NAMESPACES=pre._initial_namespaces

class AccountPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(self.enterContext(tempfile.TemporaryDirectory(dir="/tmp")));self.tmp.chmod(0o755)
        actual_stat=os.stat
        def self_stat(path,*args,**kwargs):
            if str(path) in ('/proc/self/ns/ipc','/proc/self/ns/mnt'):
                return SimpleNamespace(st_dev=1,st_ino=2)
            if str(path) in ('/proc/1/ns/ipc','/proc/1/ns/mnt'):
                raise PermissionError(13,'synthetic root-owned PID1')
            return actual_stat(path,*args,**kwargs)
        self.enterContext(patch.object(pre.os,'stat',side_effect=self_stat))
        self.enterContext(patch.object(pre,'_initial_namespaces',return_value={'ipc':(1,2),'mnt':(1,2)}))

    def read(self,path,maximum):return MOUNTS if path.endswith('mountinfo') else IPC

    def inspect(self):
        with patch.object(pre.ns,'_identity',return_value=(1,2)),patch.object(pre.ns,'_read',side_effect=self.read):
            return pre.inspect_ownership(ID)

    def test_empty_full_scan_records_coverage_without_reservation(self):
        with patch.object(boot,'_collect_native_client',return_value=(0,b'')) as execute:
            result=self.inspect()
        command=execute.call_args.args[0]
        self.assertIn('60s',command);self.assertNotIn('-xdev',command)
        self.assertIn('-P',command);self.assertNotIn('-ignore_readdir_race',command)
        self.assertFalse(result['collision']);self.assertEqual(result['sysv_objects'],{'shm':0,'sem':0,'msg':0})

    def test_real_orphan_file_uid_collision(self):self.real_collision(False)
    def test_real_group_owned_object_collision(self):self.real_collision(True)
    def real_collision(self,group):
        orphan=self.tmp/'orphan';orphan.write_text('synthetic')
        def collect(command,**kwargs):
            argv=list(command[command.index('/usr/bin/find'):]);argv[2]=str(self.tmp)
            argv[argv.index('-uid')+1]=str(2**31-1 if group else os.getuid())
            argv[argv.index('-gid')+1]=str(os.getgid() if group else 2**31-1)
            result=subprocess.run(argv,capture_output=True,timeout=5,check=False)
            return result.returncode,result.stdout
        with patch.object(boot,'_collect_native_client',side_effect=collect),self.assertRaises(boot.BootstrapRefusal):self.inspect()
        self.assertEqual(orphan.read_text(),'synthetic')

    def test_permission_or_timeout_or_unknown_output_is_not_absence(self):
        for code,output in ((1,b''),(124,b''),(0,b'partial'),(0,b'COLLISION\n')):
            with self.subTest(code=code,output=output),patch.object(boot,'_collect_native_client',return_value=(code,output)),self.assertRaises(boot.BootstrapRefusal):self.inspect()

    def test_only_complete_collision_output_can_authorize_candidate_retry(self):
        for code,raw,kind in ((0,b'COLLISION\n',pre.OwnershipCollision),(1,b'COLLISION\n',boot.BootstrapRefusal),(124,b'',boot.BootstrapRefusal)):
            with self.subTest(code=code),patch.object(boot,'_collect_native_client',return_value=(code,raw)),self.assertRaises(boot.BootstrapRefusal) as caught:self.inspect()
            self.assertIs(type(caught.exception),kind)

    def test_shared_selection_budget_bounds_scan_and_refuses_expiry(self):
        with patch.object(pre.ns,'_read',side_effect=self.read),patch.object(pre.time,'monotonic',return_value=12),patch.object(boot,'_collect_native_client',return_value=(0,b'')) as native:
            pre.inspect_ownership(ID,scan_deadline=20)
        self.assertIn('8s',native.call_args.args[0])
        with patch.object(pre.ns,'_read',side_effect=self.read),patch.object(pre.time,'monotonic',return_value=20),patch.object(boot,'_collect_native_client') as native,self.assertRaisesRegex(boot.BootstrapRefusal,'selection_deadline'):
            pre.inspect_ownership(ID,scan_deadline=20)
        native.assert_not_called()

    def test_current_and_creator_ipc_ownership_all_refuse(self):
        for index,value in ((0,950),(1,951),(2,950),(3,951)):
            values=[0]*4;values[index]=value
            with self.subTest(index=index),self.assertRaisesRegex(boot.BootstrapRefusal,'ipc_ownership_collision'):
                pre.parse_ipc(IPC+b'0 1 600 '+b' '.join(str(x).encode() for x in values)+b'\n',950,951)

    def test_malformed_or_missing_ipc_columns_refuse(self):
        for raw in (b'',b'uid gid cuid\n',b'uid gid cuid cgid uid\n',IPC+b'0 1 600 x 0 0 0\n',IPC+b'0 1 600 0\n',IPC[:-1]):
            with self.subTest(raw=raw),self.assertRaises(boot.BootstrapRefusal):pre.parse_ipc(raw,950,951)

    def test_ipc_permission_error_refuses(self):
        with patch.object(pre.ns,'_identity',return_value=(1,2)),patch.object(pre.ns,'_read',side_effect=[MOUNTS,PermissionError()]),patch.object(boot,'_collect_native_client',return_value=(0,b'')),self.assertRaisesRegex(boot.BootstrapRefusal,'ipc_coverage_incomplete'):pre.inspect_ownership(ID)

    def test_mount_change_refuses(self):
        with patch.object(pre.ns,'_identity',return_value=(1,2)),patch.object(pre.ns,'_read',side_effect=[MOUNTS,IPC,IPC,IPC,MOUNTS+b'4 1 8:2 / /mnt rw - ext4 x rw\n']),patch.object(boot,'_collect_native_client',return_value=(0,b'')),self.assertRaisesRegex(boot.BootstrapRefusal,'mount_coverage_changed'):pre.inspect_ownership(ID)

    def test_non_kernel_mount_below_exclusion_refuses_before_scan(self):
        with patch.object(pre.ns,'_identity',return_value=(1,2)),patch.object(pre.ns,'_read',return_value=MOUNTS+b'4 2 8:2 / /proc/hidden rw - ext4 x rw\n'),patch.object(boot,'_collect_native_client') as execute,self.assertRaisesRegex(boot.BootstrapRefusal,'excluded_mount'):pre.inspect_ownership(ID)
        execute.assert_not_called()

    def test_non_host_namespace_refuses_before_scan(self):
        with patch.object(pre,'_initial_namespaces',return_value={'ipc':(1,3),'mnt':(1,2)}),patch.object(boot,'_collect_native_client') as execute,self.assertRaises(boot.BootstrapRefusal):pre.inspect_ownership(ID)
        execute.assert_not_called()

    def test_ptrace_gated_pid1_uses_fixed_native_stat_without_root_python(self):
        commands=[]
        def collect(command,**kwargs):
            commands.append(command)
            return (0,b'1:2\n1:2\n') if '/usr/bin/stat' in command else (0,b'')
        with patch.object(pre,'_initial_namespaces',INITIAL_NAMESPACES),patch.object(pre.ns,'_read',side_effect=self.read),patch.object(boot,'_collect_native_client',side_effect=collect):
            result=pre.inspect_ownership(ID)
        self.assertFalse(result['collision']);self.assertEqual(len(commands),3)
        for command in (commands[0],commands[-1]):
            self.assertEqual(command,identity.native(('/usr/bin/stat','-L','--format=%d:%i','--','/proc/1/ns/ipc','/proc/1/ns/mnt')))
            self.assertNotIn('python',' '.join(command));self.assertNotIn('/bin/sh',command)

    def test_initial_namespace_native_failures_are_fixed_refusals(self):
        for code,raw in ((1,b''),(124,b''),(0,b'1:2\n'),(0,b'1:2\n1:2\nextra'),(0,b'1:0\n1:2\n'),(0,b'1:18446744073709551616\n1:2\n')):
            with self.subTest(code=code,raw=raw),patch.object(boot,'_collect_native_client',return_value=(code,raw)),self.assertRaisesRegex(boot.BootstrapRefusal,'host_initial_namespace_unreadable'):
                INITIAL_NAMESPACES()
        with patch.object(boot,'_collect_native_client',side_effect=PermissionError(13,'private detail')),self.assertRaisesRegex(boot.BootstrapRefusal,'host_initial_namespace_unreadable'):
            INITIAL_NAMESPACES()

    def test_initial_namespace_cleanup_and_cancellation_are_not_hidden(self):
        for error in (KeyboardInterrupt(),boot.BootstrapCleanupError('owned cleanup failed')):
            with self.subTest(error=type(error)),patch.object(boot,'_collect_native_client',side_effect=error),self.assertRaises(type(error)) as caught:
                INITIAL_NAMESPACES()
            self.assertIs(caught.exception,error)

    def test_namespace_drift_after_complete_scan_refuses(self):
        with patch.object(pre,'_initial_namespaces',side_effect=[{'ipc':(1,2),'mnt':(1,2)},{'ipc':(1,3),'mnt':(1,2)}]),patch.object(pre.ns,'_read',side_effect=self.read),patch.object(boot,'_collect_native_client',return_value=(0,b'')),self.assertRaisesRegex(boot.BootstrapRefusal,'host_scan_namespace_changed'):
            pre.inspect_ownership(ID)

    def test_unreadable_self_namespace_refuses_before_native_or_reservation(self):
        with patch.object(pre.os,'stat',side_effect=PermissionError(13,'synthetic')),patch.object(boot,'_collect_native_client') as native,self.assertRaisesRegex(boot.BootstrapRefusal,'host_self_namespace_unreadable'):
            pre.inspect_ownership(ID)
        native.assert_not_called()

    def test_readonly_target_inventory_reports_mount_conflict_without_traversal(self):
        from hosted_synthetic_source import HostedSourceBinding
        raw=MOUNTS+b'4 1 8:2 / /boot/efi rw - vfat /dev/fixture rw\n5 2 0:5 / /proc/private-fixture rw - autofs systemd rw\n6 2 0:6 / /proc/sys/fs/binfmt_misc rw - autofs systemd rw\n7 6 0:7 / /proc/sys/fs/binfmt_misc rw - binfmt_misc binfmt_misc rw\n'
        old_stat=os.stat
        def selected(path,*args,**kwargs):
            if str(path)=='/proc/1':return SimpleNamespace(st_uid=0)
            return old_stat(path,*args,**kwargs)
        with patch.object(pre.ns,'_read',return_value=raw),patch.object(os,'stat',side_effect=selected),patch.object(os,'geteuid',return_value=1001),patch.object(boot,'_collect_native_client') as native:
            result=pre.observe_scan_host(HostedSourceBinding('a'*40,'b'*40,()))
        native.assert_not_called()
        self.assertEqual(result['ownership_scan_mount_policy'],'REFUSED')
        self.assertEqual(result['fixed_mount_points']['/boot/efi'],['vfat'])
        self.assertEqual(result['fixed_mount_points']['/proc/sys/fs/binfmt_misc'],['autofs','binfmt_misc'])
        self.assertEqual(result['root_owned_pid1_condition'],'MET')
        self.assertEqual(result['direct_pid1_namespace_read']['ipc']['errno'],13)
        self.assertEqual(result['repaired_native_stat_regression'],'NOT_RUN_REVIEW_REQUIRED')
        self.assertNotIn('private-fixture',str(result))
        self.assertEqual(result['account_mutations'],0)

    def test_readonly_inventory_does_not_turn_malformed_mounts_into_absence(self):
        from hosted_synthetic_source import HostedSourceBinding
        for raw in (b'incomplete',b'1 0 bad\n'):
            with self.subTest(raw=raw),patch.object(pre.ns,'_read',return_value=raw),patch.object(os,'geteuid',return_value=1001),self.assertRaises(boot.BootstrapRefusal):
                pre.observe_scan_host(HostedSourceBinding('a'*40,'b'*40,()))

    def test_unreadable_regular_source_refuses(self):
        p=self.tmp/'source';p.write_text('synthetic');p.chmod(0o600)
        with self.assertRaisesRegex(boot.BootstrapRefusal,'candidate_access'):pre._access(p,ID)
        self.assertEqual(p.stat().st_mode & 0o777,0o600)

    def test_untraversable_ancestor_refuses(self):
        self.tmp.chmod(0o700)
        with self.assertRaisesRegex(boot.BootstrapRefusal,'candidate_access'):pre._access(self.tmp,ID,directory=True)

    def test_acl_dependent_access_refuses(self):
        with patch.object(os,'getxattr',return_value=b'acl'),self.assertRaisesRegex(boot.BootstrapRefusal,'acl_requires_review'):pre._access(self.tmp,ID,directory=True)

    def test_source_symlink_refuses(self):
        p=self.tmp/'link';p.symlink_to(self.tmp)
        with self.assertRaises(boot.BootstrapRefusal):pre._access(p,ID,directory=True)

    def setup_git(self):
        source=self.tmp/'source';source.mkdir(mode=0o755)
        def git(*args):return subprocess.check_output(['/usr/bin/git','-c','core.hooksPath=/dev/null',*args],cwd=source,env=boot._ENVIRONMENT,stderr=subprocess.DEVNULL).decode().strip()
        git('init');(source/'synthetic').write_text('synthetic\n');git('add','synthetic')
        git('-c','user.name=Synthetic','-c','user.email=278831488+Vedang1998@users.noreply.github.com','-c','commit.gpgsign=false','commit','-m','synthetic')
        commit=git('rev-parse','HEAD')
        for path in (source,*source.rglob('*')):path.chmod(0o755 if path.is_dir() else 0o644)
        return source,commit

    def run_source(self,source,commit):
        with patch.object(os,'geteuid',return_value=1001),patch.object(os,'getegid',return_value=1001):
            return pre.inspect_source(ID,str(source),commit)

    def test_real_local_git_rehearsal_matches_exact_commit_tree(self):
        source,commit=self.setup_git();result=self.run_source(source,commit)
        self.assertEqual(result['commit'],commit);self.assertEqual(result['account_mutations'],0)
        self.assertEqual(result['copy_command_timeout_seconds'],60)

    def test_copy_failure_refuses_without_source_permissions_change(self):
        source,commit=self.setup_git();before=(source/'synthetic').stat().st_mode
        with patch.object(boot,'_collect_native_client',return_value=(124,b'')),self.assertRaisesRegex(boot.BootstrapRefusal,'copy_rehearsal'):self.run_source(source,commit)
        self.assertEqual((source/'synthetic').stat().st_mode,before)

    def test_copy_byte_bound_refuses_before_git_copy(self):
        source,commit=self.setup_git()
        with patch.object(pre,'MAX_BYTES',1),patch.object(boot,'_collect_native_client') as execute,self.assertRaises(boot.BootstrapRefusal):self.run_source(source,commit)
        execute.assert_not_called()

    def test_git_directory_enumeration_refuses_before_unbounded_materialization(self):
        from types import SimpleNamespace
        source,commit=self.setup_git();seen=[]
        class Entries:
            def __enter__(self):return self
            def __exit__(self,*unused):return False
            def __iter__(self):
                for index in range(1000000):
                    seen.append(index)
                    yield SimpleNamespace(path=str(source/'.git'/str(index)),name=str(index),is_dir=lambda **k:False)
        with patch.object(pre,'MAX_FILES',12),patch.object(os,'scandir',return_value=Entries()),patch.object(boot,'_collect_native_client') as execute,self.assertRaisesRegex(boot.BootstrapRefusal,'scan_file_bound'):
            self.run_source(source,commit)
        self.assertLessEqual(len(seen),12);execute.assert_not_called()

    def test_external_git_object_store_refuses_before_copy(self):
        source,commit=self.setup_git()
        (source/'.git/objects/info/alternates').write_text('/private/foreign/objects\n')
        with patch.object(boot,'_collect_native_client') as execute,self.assertRaisesRegex(boot.BootstrapRefusal,'external_git'):
            self.run_source(source,commit)
        execute.assert_not_called()

    def test_full_real_destination_refuses_before_copy(self):
        from types import SimpleNamespace
        source,commit=self.setup_git()
        with patch.object(os,'statvfs',return_value=SimpleNamespace(f_bavail=0,f_frsize=4096,f_flag=0,f_favail=100000)),patch.object(boot,'_collect_native_client') as execute,self.assertRaisesRegex(boot.BootstrapRefusal,'destination_preflight'):
            self.run_source(source,commit)
        execute.assert_not_called()

    def test_readonly_or_inode_exhausted_destination_refuses(self):
        from types import SimpleNamespace
        source,commit=self.setup_git()
        for flags,inodes in ((os.ST_RDONLY,100000),(0,0)):
            with self.subTest(flags=flags,inodes=inodes),patch.object(os,'statvfs',return_value=SimpleNamespace(f_bavail=1000000,f_frsize=4096,f_flag=flags,f_favail=inodes)),patch.object(boot,'_collect_native_client') as execute,self.assertRaisesRegex(boot.BootstrapRefusal,'destination_preflight'):
                self.run_source(source,commit)
            execute.assert_not_called()

    def test_cgroup_tree_is_not_pruned(self):
        with patch.object(boot,'_collect_native_client',return_value=(0,b'')) as execute:self.inspect()
        command=execute.call_args.args[0]
        self.assertEqual(command[command.index('/usr/bin/find')+1:command.index('-prune')],('-P','/','-path','/proc'))
        self.assertNotIn('/sys',command)

    def test_network_or_automount_filesystem_refuses_before_scan(self):
        for kind in (b'nfs',b'cifs',b'fuse.sshfs',b'autofs'):
            with self.subTest(kind=kind),self.assertRaisesRegex(boot.BootstrapRefusal,'filesystem_scope'):
                pre._mount_scope(MOUNTS+b'4 1 0:8 / /mnt rw - '+kind+b' external rw\n')

    def test_copy_rehearsal_refusal_precedes_quarantine_and_account_commands(self):self.before_mutation('inspect_source')
    def test_ownership_refusal_precedes_quarantine_and_account_commands(self):self.before_mutation('inspect_ownership')
    def before_mutation(self,which):
        with patch.object(os,'getresuid',return_value=(1001,)*3),patch.object(os,'getresgid',return_value=(1001,)*3),patch.object(identity,'inspect_account_platform',return_value={}),patch.object(identity,'inspect_account'),patch.object(identity,'identity_processes',return_value=[]),patch.object(os.path,'lexists',return_value=False),patch.object(pre,'inspect_source',return_value={}),patch.object(pre,'inspect_ownership',return_value={}),patch.object(pre,which,side_effect=boot.BootstrapRefusal('causal')),patch('hosted_synthetic_quarantine.Quarantine.create') as reserve,patch.object(boot,'_collect_native_client') as execute,self.assertRaisesRegex(boot.BootstrapRefusal,'causal'):
            identity.AccountLease.create(ID,'/work/source','a'*40)
        reserve.assert_not_called();execute.assert_not_called()

if __name__=='__main__':unittest.main()
