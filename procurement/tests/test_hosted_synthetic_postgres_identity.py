"""Simulated irreversible identity transitions; no namespace/live-host PASS."""
import ctypes
import fcntl
import os
import signal
import unittest
from unittest.mock import Mock, patch

import hosted_synthetic_namespace as ns
import hosted_synthetic_postgres_identity as pg
import hosted_synthetic_worker as worker


class PostgresIdentityTests(unittest.TestCase):
    def setUp(self):
        self.state={'unshared':False,'maps':{},'erased':False,'filtered':False}
        self.events=[];self.before={'user':[1,10],'mnt':[1,11],'pid':[1,12],'net':[1,13],'cgroup':[1,15]}
        self.after=dict(self.before,user=[1,14]);self.parent=Mock(number=5)
        self.boundary=self.enterContext(patch.object(pg,'_signal_boundary'))
        self.stdio=self.enterContext(patch.object(pg,'_stdio',return_value=[]))
        self.proc_owner=self.enterContext(patch.object(os,'stat',return_value=Mock(st_mode=0o100644,st_uid=0,st_gid=0)))
        self.mapped=self.enterContext(patch.object(ns,'_require_mapped_identity'))
        self.root=self.enterContext(patch.object(worker,'require_projected_root',side_effect=lambda *a,**k:self.events.append('root') or {'scope':'synthetic'}))
        self.enterContext(patch.object(os,'getpid',return_value=2));self.enterContext(patch.object(os,'getppid',return_value=1))
        self.groups=self.enterContext(patch.object(os,'getgroups',return_value=[]))
        self.enterContext(patch.object(os,'listdir',return_value=['2']))
        self.enterContext(patch.object(os,'getresuid',side_effect=lambda:(1000 if 'uid_map' in self.state['maps'] else 0,)*3))
        self.enterContext(patch.object(os,'getresgid',side_effect=lambda:(1000 if 'gid_map' in self.state['maps'] else 0,)*3))
        self.process=self.enterContext(patch.object(ns,'_process',side_effect=lambda pid: {'parent':0,'start':10} if pid==1 else {'parent':1,'start':20}))
        self.names=self.enterContext(patch.object(pg,'_namespaces',side_effect=lambda pid:dict(self.after if pid=='self' and self.state['unshared'] else self.before)))
        self.read=self.enterContext(patch.object(ns,'_read',side_effect=self.read_value))
        self.pidfd=self.enterContext(patch.object(ns,'_pidfd',return_value=self.parent))
        self.terminal=self.enterContext(patch.object(ns.backend,'_browser_pidfd_is_terminal',return_value=False))
        self.enterContext(patch.object(ns.backend,'_read_browser_pidfd_metadata',return_value=(None,1,0,0)))
        self.harden=self.enterContext(patch.object(ns,'_hardening',side_effect=lambda p:self.events.append('harden')))
        self.death=self.enterContext(patch.object(pg,'_death_signal'))
        self.unshare=self.enterContext(patch.object(os,'unshare',side_effect=self.unshare_value))
        self.maps=self.enterContext(patch.object(pg,'_write_map',side_effect=self.map_value))
        self.status=self.enterContext(patch.object(worker,'_status',side_effect=self.status_value))
        self.erase=self.enterContext(patch.object(worker,'_erase_capabilities',side_effect=self.erase_value))
        self.filter=self.enterContext(patch.object(worker,'install_filter',side_effect=self.filter_value))
        self.core=self.enterContext(patch.object(pg.native_core,'enforce_native_core',return_value={'scope':'simulated'}))
        self.prctl=self.enterContext(patch.object(worker,'_get_prctl',side_effect=lambda n:worker._SECUREBITS if n==27 else 0))

    def read_value(self,path,limit):
        if path.endswith('/children'):return b''
        if path.endswith('/cgroup'):return b'0::/owned/generation\n'
        name=path.rsplit('/',1)[-1]
        if name in ('uid_map','gid_map'):
            return self.state['maps'].get(name,b'' if self.state['unshared'] else b'0 61000 1\n')
        if name=='setgroups':return self.state['maps'].get(name,b'allow\n')
        raise AssertionError(path)

    def status_value(self):
        return {'uid':[os.getresuid()[0]]*4,
            'gid':[os.getresgid()[0]]*4,'groups':os.getgroups(),'no_new_privs':1,
            **{key:0 if self.state['erased'] else 0xffffffffff for key in worker._CAPS}}

    def unshare_value(self,flags):
        self.assertEqual(flags,os.CLONE_NEWUSER);self.events.append('unshare');self.state['unshared']=True

    def map_value(self,name,raw):
        self.events.append(name);self.state['maps'][name]=raw

    def erase_value(self,before):
        self.assertEqual(before['uid'],[1000]*4);self.assertEqual(before['gid'],[1000]*4)
        self.events.append('erase');self.state['erased']=True;return {key:0 for key in worker._CAPS}

    def filter_value(self):
        self.assertTrue(self.state['erased']);self.events.append('filter');self.state['filtered']=True
        return {'sha256':worker.POLICY_SHA256,'installed_in_current_process':True}

    def run_identity(self):return pg.restrict_postgres_worker((2,3),4)

    def test_nonzero_mapping_readback_precedes_erasure_and_fixed_filter(self):
        value=self.run_identity()
        self.assertEqual(self.events,['root','harden','unshare','uid_map','setgroups','gid_map','harden','erase','filter','root'])
        self.assertEqual(value['uid'],1000);self.assertEqual(value['gid'],1000)
        self.assertFalse(value['execution_authority']);self.assertEqual(value['credential_release'],'WITHHELD')
        self.assertEqual(value['external_containment_and_cleanup'],'NOT_PROVEN')
        self.parent.close.assert_called_once();self.mapped.assert_called_once()

    def test_signal_boundary_failure_precedes_all_effects(self):
        self.boundary.side_effect=ns.NamespaceProofError('boundary')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.root.assert_not_called();self.unshare.assert_not_called();self.pidfd.assert_not_called()

    def test_nonempty_inherited_groups_refuse_before_root_or_namespace(self):
        self.groups.return_value=[0]
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.root.assert_not_called();self.unshare.assert_not_called()

    def test_missing_full_u1_capability_gate_refuses_before_namespace(self):
        self.mapped.side_effect=ns.NamespaceProofError('missing_setfcap_or_other_scoped_capability')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.unshare.assert_not_called();self.maps.assert_not_called()

    def test_host_or_multi_id_map_cannot_enter(self):
        original=self.read_value
        for raw in (b'0 0 4294967295\n',b'1000 61000 1\n',b'0 61000 2\n',b'0 0 1\n'):
            self.read.side_effect=lambda path,n:raw if path.endswith('_map') else original(path,n)
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.unshare.assert_not_called()

    def test_foreign_control_handle_or_unsealed_root_refuses_before_pidfd(self):
        self.root.side_effect=ns.NamespaceProofError('root_or_descriptor')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.pidfd.assert_not_called();self.unshare.assert_not_called()

    def test_control_file_disguised_as_stdio_refuses_before_namespace(self):
        self.stdio.side_effect=ns.NamespaceProofError('stdio control')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.pidfd.assert_not_called();self.unshare.assert_not_called()

    def test_nondumpable_u1_reexec_ownership_is_required(self):
        self.proc_owner.return_value=Mock(st_mode=0o100644,st_uid=65534,st_gid=65534)
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.root.assert_not_called();self.unshare.assert_not_called()

    def test_proc_directory_owner_cannot_substitute_for_mm_namespace_proof(self):
        self.proc_owner.side_effect=lambda path:Mock(st_mode=0o040555,st_uid=0,st_gid=0) if path=='/proc/self' else Mock(st_mode=0o100644,st_uid=65534,st_gid=65534)
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.unshare.assert_not_called()
        self.proc_owner.assert_called_once_with('/proc/self/uid_map')

    def test_non_init_parent_or_existing_children_refuse_before_namespace(self):
        with patch.object(os,'getppid',return_value=8),self.assertRaises(ns.NamespaceProofError):self.run_identity()
        original=self.read_value;self.read.side_effect=lambda p,n:b'3 ' if p.endswith('/children') else original(p,n)
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.unshare.assert_not_called()

    def test_parent_namespace_mismatch_refuses_before_namespace(self):
        self.names.side_effect=lambda p:dict(self.before,user=[1,99]) if p=='1' else self.before
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.pidfd.assert_not_called()

    def test_dead_parent_never_starts_transition(self):
        self.terminal.return_value=True
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.unshare.assert_not_called();self.parent.close.assert_called_once()

    def test_unavailable_kernel_namespace_refuses_without_mapping_or_pass(self):
        self.unshare.side_effect=PermissionError('synthetic unavailable kernel capability')
        with self.assertRaises(PermissionError):self.run_identity()
        self.maps.assert_not_called();self.erase.assert_not_called();self.parent.close.assert_called_once()

    def test_only_user_namespace_may_change(self):
        for key in ('mnt','pid','net','cgroup'):
            self.state['unshared']=False;self.after=dict(self.before,user=[1,14],**{key:[1,99]})
            with self.subTest(key=key),self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.maps.assert_not_called()

    def test_unchanged_user_namespace_refuses_before_mapping(self):
        self.after=self.before
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.maps.assert_not_called()

    def test_partial_mapping_failure_does_not_retry_or_drop_capabilities(self):
        def fail(name,raw):
            if name=='gid_map':raise OSError('synthetic final map failure')
            self.map_value(name,raw)
        self.maps.side_effect=fail
        with self.assertRaises(OSError):self.run_identity()
        self.assertEqual([c.args[0] for c in self.maps.call_args_list],['uid_map','setgroups','gid_map'])
        self.erase.assert_not_called();self.filter.assert_not_called();self.parent.close.assert_called_once()

    def test_wrong_completed_map_refuses_before_capability_effects(self):
        def wrong(name,raw):self.map_value(name,b'1000 1 1\n' if name=='uid_map' else raw)
        self.maps.side_effect=wrong
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.erase.assert_not_called()

    def test_capability_erasure_failure_never_installs_filter(self):
        self.erase.side_effect=ns.NamespaceProofError('capability_drop')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.filter.assert_not_called();self.parent.close.assert_called_once()

    def test_any_retained_capability_refuses_final_identity(self):
        original=self.status_value
        for key in worker._CAPS:
            self.state.update(unshared=False,maps={},erased=False)
            self.status.side_effect=lambda:dict(original(),**({key:1} if self.state['erased'] else {}))
            with self.subTest(key=key),self.assertRaises(ns.NamespaceProofError):self.run_identity()

    def test_filter_failure_cannot_return_identity(self):
        self.filter.side_effect=ns.NamespaceProofError('filter')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.parent.close.assert_called_once()

    def test_native_core_gate_refusal_prevents_identity_pass(self):
        self.core.side_effect=ns.NamespaceProofError('piped host core policy')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.parent.close.assert_called_once()

    def test_four_ids_empty_groups_and_nnp_must_survive_mapping(self):
        original=self.status_value
        for key,value in (('uid',[1000,1000,1000,0]),('gid',[1000,1000,1000,0]),('groups',[0]),('no_new_privs',0)):
            self.state.update(unshared=False,maps={},erased=False)
            self.status.side_effect=lambda:dict(original(),**({key:value} if 'gid_map' in self.state['maps'] else {}))
            with self.subTest(key=key),self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.erase.assert_not_called();self.filter.assert_not_called()

    def test_parent_death_after_mapping_prevents_identity_pass(self):
        self.terminal.side_effect=lambda fd:self.state['erased']
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.parent.close.assert_called_once()

    def test_pdeathsig_readback_failure_precedes_capability_drop(self):
        self.death.side_effect=ns.NamespaceProofError('death signal missing')
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.erase.assert_not_called();self.parent.close.assert_called_once()

    def test_cgroup_movement_refuses_even_when_identity_is_correct(self):
        original=self.read_value
        self.read.side_effect=lambda p,n:b'0::/foreign\n' if p.endswith('/cgroup') and self.state['erased'] else original(p,n)
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.parent.close.assert_called_once()

    def test_parent_close_failure_preserves_original_transition_failure(self):
        original=PermissionError('synthetic namespace');self.unshare.side_effect=original
        self.parent.close.side_effect=OSError('synthetic cleanup')
        with self.assertRaises(ns.NamespaceCleanupError) as caught:self.run_identity()
        self.assertIs(caught.exception.__cause__,original)

    def test_final_descriptor_readback_must_pass_after_parent_fd_closes(self):
        self.root.side_effect=[{},ns.NamespaceProofError('extra descriptor')]
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.parent.close.assert_called_once()

    def test_stdio_identity_must_survive_mapping_and_parent_fd_close(self):
        self.stdio.side_effect=[[],[{'foreign':True}]]
        with self.assertRaises(ns.NamespaceProofError):self.run_identity()
        self.parent.close.assert_called_once()


class PostgresMapWriteTests(unittest.TestCase):
    def stdio_fixture(self,*,mode=0o010600):
        values={n:Mock(st_dev=1,st_ino=n+10,st_mode=mode,st_uid=0,st_gid=0,st_rdev=os.makedev(1,3)) for n in (0,1,2)}
        self.enterContext(patch.object(os,'fstat',side_effect=lambda n:values[n]))
        self.enterContext(patch.object(fcntl,'fcntl',side_effect=lambda n,op:os.O_RDONLY if n==0 else os.O_WRONLY))
        self.enterContext(patch.object(os,'readlink',return_value='/dev/null'))
        return values

    def test_stdio_accepts_directional_pipes_or_exact_null_device(self):
        values=self.stdio_fixture()
        self.assertEqual([x['number'] for x in pg._stdio(())],[0,1,2])
        for value in values.values():value.st_mode=0o020666
        self.assertEqual(len(pg._stdio(())),3)

    def test_regular_cgroup_control_and_other_devices_are_not_stdio(self):
        values=self.stdio_fixture()
        for mode,rdev in ((0o100600,0),(0o020666,os.makedev(1,5))):
            values[0].st_mode=mode;values[0].st_rdev=rdev
            with self.subTest(mode=mode),self.assertRaises(ns.NamespaceProofError):pg._stdio(())

    def test_control_protocol_channel_cannot_be_aliased_through_stdio(self):
        values=self.stdio_fixture();values[7]=values[1]
        with self.assertRaises(ns.NamespaceProofError):pg._stdio((Mock(number=7),))

    def test_pdeathsig_uses_pointer_readback_not_prctl_return_value(self):
        def result(option,pointer,*unused):
            self.assertEqual(option,2)
            ctypes.cast(pointer,ctypes.POINTER(ctypes.c_int))[0]=signal.SIGKILL
            return 0
        with patch.object(ns.backend,'_browser_libc',return_value=Mock(prctl=Mock(side_effect=result))):pg._death_signal()

    def test_successful_prctl_without_expected_signal_is_refused(self):
        with patch.object(ns.backend,'_browser_libc',return_value=Mock(prctl=Mock(return_value=0))),self.assertRaises(ns.NamespaceProofError):pg._death_signal()

    def test_unrecognized_mapping_is_rejected_before_open(self):
        for pair in (('uid_map',b'0 0 1\n'),('gid_map',b'1000 1 1\n'),('other',b'deny\n')):
            with self.subTest(pair=pair),patch.object(ns,'_acquire') as acquire,self.assertRaises(ns.NamespaceProofError):pg._write_map(*pair)
            acquire.assert_not_called()

    def test_single_short_write_refuses_and_closes_descriptor(self):
        descriptor=Mock(number=7)
        with patch.object(ns,'_acquire',return_value=descriptor),patch.object(os,'fstat',return_value=Mock(st_mode=0o100600)),patch.object(os,'write',return_value=1) as write,self.assertRaises(ns.NamespaceProofError):
            pg._write_map('uid_map',pg._MAPPING)
        write.assert_called_once();descriptor.close.assert_called_once()

    def test_map_close_failure_retains_original_write_failure(self):
        original=OSError('synthetic write');descriptor=Mock(number=7);descriptor.close.side_effect=OSError('synthetic close')
        with patch.object(ns,'_acquire',return_value=descriptor),patch.object(os,'fstat',return_value=Mock(st_mode=0o100600)),patch.object(os,'write',side_effect=original),self.assertRaises(ns.NamespaceCleanupError) as caught:
            pg._write_map('gid_map',pg._MAPPING)
        self.assertIs(caught.exception.__cause__,original)
