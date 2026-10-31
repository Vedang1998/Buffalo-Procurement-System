"""Dormant importer causal tests; native transport never activates."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_installation as install
from test_hosted_synthetic_native_inputs import archive,row
from hosted_synthetic_source import HostedSourceBinding
BINDING=HostedSourceBinding("a"*40,"b"*40,())


class SyntheticOps:
    def __init__(self,deadline):self.deadline=deadline;self.nodes={};self.calls=[];self.serial=100;self.uncertain_stream=False
    def run(self,program,*args):
        self.calls.append((program,args));path=args[-1]
        if program=='mkdir':
            if path in self.nodes:raise native.ns.NamespaceProofError('exists')
            self.serial+=1;self.nodes[path]=[1,self.serial,stat.S_IFDIR|0o700,0,0,2,0]
        elif program=='chmod':self.nodes[path][2]=(self.nodes[path][2]&~0o777)|int(args[0],8)
        elif program in ('unlink','rmdir','rm'):
            for path in args[1:]:del self.nodes[path]
        elif program=='sha256sum':return (hashlib.sha256(self.data[path]).hexdigest()+'  '+path+'\n').encode()
        else:raise AssertionError(program)
        return b''
    def begin_cleanup(self,deadline):self.cleanup_deadline=deadline
    def stream(self,path,selected,chunks):
        self.data=getattr(self,'data',{});data=b''.join(chunks);self.data[path]=data
        if self.uncertain_stream:raise native.ns.NamespaceProofError('synthetic_timeout_root_descendants_unknown')
        self.serial+=1;self.nodes[path]=[1,self.serial,stat.S_IFREG|0o600,0,0,1,len(data)]
    def observe(self,path):return tuple(self.nodes[path])
    def observe_many(self,paths, *, follow=False):return [self.observe(p) for p in paths]
    def inventory(self,root):return {p[len(root)+1:] for p in self.nodes if p!=root}
    def absent(self,path):return path not in self.nodes


class NativeInstallationTests(unittest.TestCase):
    def setUp(self):self.enterContext(patch.object(install,"_source_binding"))
    def lease(self,selected):
        selected=[dict(r,artifact_sha256='a'*64) for r in selected]
        with patch.object(install,'_NativeOps',SyntheticOps),patch.object(native,'require_complete_inventory'),patch.object(native,'require_native_capacity'),patch.object(native,'execution_inventory',return_value={'rows':{r['path']:r for r in selected}}),patch.object(os.path,'lexists',return_value=False):
            lease=install.InstallationLease._create('a'*32,deadline=time.monotonic()+60,binding=BINDING)
        return lease,selected
    def stage(self,selected):
        parent=Path(self.enterContext(tempfile.TemporaryDirectory()));stage=native.Stage(parent/'stage')
        self.addCleanup(lambda:None if stage.closed else stage.close())
        stage.materialize_tar(archive(),selected);return stage

    def test_default_real_transport_and_activation_refuse_before_native_calls(self):
        with patch.object(install.subprocess,'Popen') as process:
            with self.assertRaisesRegex(native.ns.NamespaceProofError,'not_authorized'):install._NativeOps(time.monotonic()+60)
            for value in ({},{'approved':True}):
                with self.assertRaisesRegex(native.ns.NamespaceProofError,'not_authorized'):install.activate_installation(**value)
        process.assert_not_called()

    def test_exact_import_retires_stage_keeps_backing_private_and_cleans_owned_files(self):
        lease,rows=self.lease([row()]);stage=self.stage(rows)
        with patch.object(native,'inventory',return_value={'rows':rows}):lease.import_stage(stage,rows)
        self.assertTrue(stage.closed);self.assertIsNone(lease.active_stage)
        lease.finish();self.assertTrue(lease.installed)
        self.assertEqual(stat.S_IMODE(lease.owned[''][2]),0o700)
        self.assertEqual(stat.S_IMODE(lease.owned['view/opt/example'][2]),0o444)
        lease.close();self.assertTrue(lease.closed);self.assertEqual(lease.ops.nodes,{})

    def test_sequential_artifact_stages_are_closed_before_next_import(self):
        lease,rows=self.lease([row(),dict(row(),path='/opt/second')])
        rows[1]['artifact_sha256']='b'*64;lease.rows[rows[1]['path']]=rows[1]
        for selected in rows:
            stage=self.stage([selected])
            with patch.object(native,'inventory',return_value={'rows':rows}):lease.import_stage(stage,[selected])
            self.assertTrue(stage.closed)
        lease.finish();lease.close();self.assertTrue(lease.closed)

    def test_unlisted_row_never_starts_privileged_write(self):
        lease,rows=self.lease([row()]);stage=self.stage(rows);before=list(lease.ops.calls)
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'unlisted'):lease.import_stage(stage,[dict(rows[0],sha256='f'*64)])
        self.assertEqual(lease.ops.calls,before)

    def test_lost_stream_ack_quarantines_and_never_claims_root_cleanup(self):
        lease,rows=self.lease([row()]);stage=self.stage(rows);lease.ops.uncertain_stream=True
        with patch.object(native,'inventory',return_value={'rows':rows}),self.assertRaisesRegex(native.ns.NamespaceProofError,'root_descendants_unknown'):
            lease.import_stage(stage,rows)
        self.assertTrue(stage.closed);self.assertTrue(lease.uncertain)
        before=list(lease.ops.calls)
        with self.assertRaises(native.ns.NamespaceProofError):lease.close()
        self.assertEqual(before,lease.ops.calls);self.assertFalse(lease.closed)

    def test_replaced_owned_root_refuses_before_another_mutation(self):
        lease,rows=self.lease([row()]);stage=self.stage(rows);lease.ops.nodes[lease.root][1]+=1
        before=list(lease.ops.calls)
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'identity_changed'):lease.import_stage(stage,rows)
        self.assertEqual(before,lease.ops.calls)

    def test_unknown_descendant_blocks_all_cleanup_mutations(self):
        lease,rows=self.lease([row()]);lease.ops.nodes[lease.root+'/foreign']=[1,999,stat.S_IFREG|0o444,0,0,1,0]
        before=list(lease.ops.calls)
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'unknown_descendant'):lease.close()
        self.assertEqual(before,lease.ops.calls);self.assertFalse(lease.closed)

    def test_finish_cannot_promote_partial_inventory(self):
        lease,rows=self.lease([row()]);before=list(lease.ops.calls)
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'population'):lease.finish()
        self.assertEqual(before,lease.ops.calls);self.assertFalse(lease.installed)

    def test_commands_never_include_repository_python_shell_or_stage_path(self):
        command=install._command('dd','count=14','of=/run/buffalo-native-'+'a'*32+'/view/x')
        self.assertEqual(command[0:4],('/usr/bin/sudo','-n','--','/usr/bin/env'))
        self.assertNotIn('/bin/sh',command);self.assertNotIn('python',str(command))
        for name in ('python3','bash','apt-get','cp'):
            with self.assertRaises(native.ns.NamespaceProofError):install._command(name)

    def test_native_find_argv_has_textual_escape_and_parses_nul_output(self):
        ops=object.__new__(install._NativeOps)
        with patch.object(ops,'run',return_value=b'\0view\0view/file\0') as command:
            self.assertEqual(ops.inventory('/run/buffalo-native-'+'a'*32),{'view','view/file'})
        self.assertTrue(all('\0' not in arg for arg in command.call_args.args))
        self.assertEqual(command.call_args.args[-1],r'%P\0')
        with patch.object(ops,'run',return_value=b'view\0retained\0') as command:
            self.assertTrue(ops.absent('/run/buffalo-native-'+'a'*32+'/view/gone'))
        self.assertTrue(all('\0' not in arg for arg in command.call_args.args))

    def test_cleanup_refusal_stays_sticky_after_foreign_node_is_removed(self):
        lease,rows=self.lease([row()]);foreign=lease.root+'/foreign'
        lease.ops.nodes[foreign]=[1,999,stat.S_IFREG|0o444,0,0,1,0]
        with self.assertRaises(native.ns.NamespaceProofError):lease.close()
        del lease.ops.nodes[foreign];before=list(lease.ops.calls)
        for operation in (lease.close,lease.finish):
            with self.assertRaises(native.ns.NamespaceProofError):operation()
        self.assertEqual(before,lease.ops.calls);self.assertTrue(lease.uncertain)

    def test_failed_removal_readback_never_resumes_cleanup(self):
        lease,rows=self.lease([row()]);stage=self.stage(rows)
        with patch.object(native,'inventory',return_value={'rows':rows}):lease.import_stage(stage,rows)
        with patch.object(lease.ops,'absent',return_value=False),self.assertRaises(native.ns.NamespaceProofError):lease.close()
        before=list(lease.ops.calls)
        with self.assertRaises(native.ns.NamespaceProofError):lease.close()
        self.assertEqual(before,lease.ops.calls);self.assertFalse(lease.closed)

    def test_unregistered_view_prevents_cleanup_before_first_deletion(self):
        lease,rows=self.lease([row()]);lease.view_requests['unknown']=object();before=list(lease.ops.calls)
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'without_consumer'):lease.close()
        self.assertEqual(before,lease.ops.calls)

    def test_batch_owned_stat_never_follows_symlinks(self):
        ops=object.__new__(install._NativeOps)
        with patch.object(ops,'run',return_value=b'1 2 81a4 0 0 1 3\n') as command:
            self.assertEqual(ops.observe_many(['/owned']),[(1,2,0o100644,0,0,1,3)])
        self.assertNotIn('-L',command.call_args.args)
        with patch.object(ops,'run',return_value=b'1 2 81a4 0 0 1 3\n') as command:
            ops.observe_many(['/proc/42/exe'],follow=True)
        self.assertIn('-L',command.call_args.args)


    def test_expired_work_refuses_without_losing_known_owned_cleanup(self):
        lease,rows=self.lease([row()]);lease.deadline=time.monotonic()-1
        with self.assertRaises(install.WorkExpired):lease.finish()
        self.assertFalse(lease.uncertain);lease.close()
        self.assertTrue(lease.closed);self.assertFalse(lease.ops.nodes)
        deadline=lease.cleanup_deadline;before=list(lease.ops.calls);lease.close()
        self.assertEqual(lease.cleanup_deadline,deadline);self.assertEqual(lease.ops.calls,before)

    def test_cleanup_deadline_is_single_and_timeout_never_renews_it(self):
        lease,rows=self.lease([row()]);lease.cleanup_deadline=time.monotonic()-1
        before=list(lease.ops.calls)
        for _ in range(2):
            with self.assertRaises(native.ns.NamespaceProofError):lease.close()
        self.assertEqual(before,lease.ops.calls);self.assertTrue(lease.uncertain)

    def test_cleanup_mode_forbids_resuming_work(self):
        lease,rows=self.lease([row()]);lease.cleanup_deadline=time.monotonic()+5
        with self.assertRaisesRegex(native.ns.NamespaceProofError,'cleanup_work'):lease.finish()

    def test_expiry_before_import_write_closes_stage_without_quarantine(self):
        lease,rows=self.lease([row()]);stage=self.stage(rows)
        verify=lease.verify;calls=[0]
        def expire_at_second_verify(*a,**k):
            calls[0]+=1
            if calls[0]==2:lease.deadline=time.monotonic()-1
            return verify(*a,**k)
        with patch.object(lease,'verify',side_effect=expire_at_second_verify),self.assertRaises(install.WorkExpired):
            lease.import_stage(stage,rows)
        self.assertTrue(stage.closed);self.assertIsNone(lease.active_stage);self.assertFalse(lease.uncertain)
        lease.close();self.assertTrue(lease.closed)

    def test_partial_setup_expiry_retains_known_lease_for_cleanup(self):
        original=install.InstallationLease._mkdir
        def expire(self,name):
            if name=='view':self.deadline=time.monotonic()-1
            return original(self,name)
        with patch.object(install.InstallationLease,'_mkdir',expire),self.assertRaises(install.WorkExpired) as raised:
            self.lease([row()])
        lease=raised.exception.native_installation_lease
        self.assertFalse(lease.uncertain);lease.close();self.assertTrue(lease.closed)

    def test_cancelled_import_keeps_original_error_when_stage_cleanup_fails(self):
        lease,rows=self.lease([row()]);stage=self.stage(rows)
        error=KeyboardInterrupt('cancelled')
        with patch.object(stage,'verified_stream',side_effect=error),patch.object(stage,'close',side_effect=RuntimeError('cleanup')):
            with self.assertRaises(KeyboardInterrupt) as raised:lease.import_stage(stage,rows)
        self.assertIs(raised.exception,error);self.assertIn('native_stage_cleanup_unverified',error.__notes__)
        self.assertTrue(lease.uncertain);self.assertTrue(lease.pending)

    def test_cleanup_native_timeout_and_kill_grace_fit_remaining_bound(self):
        for remaining in (.1,1,5):
            argv=install._command('stat','--','/owned',remaining=remaining)
            index=argv.index('--signal=TERM')
            kill=float(argv[index+1].split('=')[1][:-1]);work=float(argv[index+2][:-1])
            self.assertLess(kill+work,remaining)
        with self.assertRaises(native.ns.NamespaceProofError):install._command('stat',remaining=0)

    def test_expired_work_does_not_authorize_foreign_replacement_cleanup(self):
        lease,rows=self.lease([row()]);lease.deadline=time.monotonic()-1
        lease.ops.nodes[lease.root][1]+=1;before=list(lease.ops.calls)
        with self.assertRaises(native.ns.NamespaceProofError):lease.close()
        self.assertTrue(lease.uncertain);self.assertEqual(before,lease.ops.calls)


    def test_stage_constructor_open_failure_removes_only_created_empty_directory(self):
        parent=Path(self.enterContext(tempfile.TemporaryDirectory()));target=parent/'stage'
        with patch.object(native.os,'open',side_effect=OSError('open failed')),self.assertRaisesRegex(OSError,'open failed'):
            native.Stage(target)
        self.assertFalse(target.exists())

    def test_stage_constructor_replacement_is_retained_with_original_failure(self):
        parent=Path(self.enterContext(tempfile.TemporaryDirectory()));target=parent/'stage';original=parent/'original'
        error=OSError('open failed')
        def replace(*a,**k):target.rename(original);target.mkdir(mode=0o700);raise error
        with patch.object(native.os,'open',side_effect=replace),self.assertRaises(OSError) as raised:native.Stage(target)
        self.assertIs(raised.exception,error);self.assertTrue(target.exists());self.assertTrue(original.exists())
        self.assertIn('native_stage_initialization_cleanup_unverified',error.__notes__)

    def test_bounded_client_reap_failure_retains_original_and_closes_all_pipes(self):
        from unittest.mock import Mock
        import hosted_synthetic_bootstrap as boot
        error=RuntimeError('original work failure');process=Mock()
        process.poll.return_value=None;process.wait.side_effect=install.subprocess.TimeoutExpired('fixture',0)
        process.stdout.fileno.return_value=100;process.stderr.fileno.return_value=101
        with patch.object(boot.subprocess,'Popen',return_value=process),patch.object(boot.selectors,'DefaultSelector',side_effect=error),self.assertRaises(RuntimeError) as raised:
            boot._collect_native_client(('fixture',),cwd='/',operation_deadline=time.monotonic()+1,cleanup_deadline=time.monotonic()+1)
        self.assertIs(raised.exception,error);self.assertIn('bootstrap_client_cleanup_failed',error.__notes__)
        process.stdin.close.assert_called_once();process.stdout.close.assert_called_once();process.stderr.close.assert_called_once()
