"""Simulated namespace/root gates with real owned file/descriptor checks.

No native executable, namespace transition, service, database or credential runs.
"""
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import hosted_synthetic_postgres_image as image
import hosted_synthetic_namespace as ns
from test_hosted_synthetic_runtime import elf


class PostgresImageTests(unittest.TestCase):
    def setUp(self):
        self.directory=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(image.pg,'BIN',str(self.directory)+'/'))
        self.enterContext(patch.object(image.identity,'INNER_ID',os.geteuid()))
        self.loader=self.directory/'loader';self.loader.write_bytes(elf());self.loader.chmod(0o555)
        self.program=self.directory/'pg_dump'
        self.program.write_bytes(elf(interpreter=str(self.loader).encode()));self.program.chmod(0o555)
        self.spec=image.VersionSpec('a'*64,'b'*40,'c'*40,'d'*64,'pg_dump',str(self.program),
            hashlib.sha256(self.program.read_bytes()).hexdigest(),str(self.loader),
            hashlib.sha256(self.loader.read_bytes()).hexdigest(),ns._identity(self.directory.stat()),44,99,
            tuple((name,(1,n)) for n,name in enumerate(('user','mnt','pid','net','cgroup'),1)),4)
        object.__setattr__(self.spec,'_owner',self.spec);object.__setattr__(self.spec,'_token',image._TOKEN)
        self.entry=self.enterContext(patch.object(image,'_worker_entry'))
        self.root=self.enterContext(patch.object(image,'_root'))
        self.enterContext(patch.object(image.worker,'require_projected_root'))
        self.enterContext(patch.object(image.identity,'_stdio'))
        self.enterContext(patch.object(image,'_descriptors'))
        self.enterContext(patch.object(ns,'_mount_id',return_value=44))
        self.values=[];self.addCleanup(self.cleanup)

    def cleanup(self):
        for value in self.values:
            if not value.closed:value.close()

    def acquire(self):
        value=image.open_version_image(self.spec);self.values.append(value);return value

    def test_fixed_image_and_loader_are_bound_without_execution_authority(self):
        value=self.acquire();proof=value.verify()
        self.assertEqual(proof['image_sha256'],self.spec.executable_sha256)
        self.assertEqual(proof['loader_sha256'],self.spec.loader_sha256)
        self.assertEqual(proof['source_commit'],self.spec.source_commit)
        self.assertEqual(proof['native_execution'],'NOT_RUN')
        self.assertEqual(proof['stopped_enrollment'],'NOT_PROVEN')
        self.assertEqual(proof['credential_release'],'WITHHELD')
        self.assertFalse(proof['execution_authority'])
        self.assertFalse(os.get_inheritable(value.descriptor.number))
        number=value.descriptor.number;value.close();value.close()
        with self.assertRaises(OSError):os.fstat(number)

    def test_entry_refusal_precedes_file_open(self):
        self.entry.side_effect=ns.NamespaceProofError('entry')
        with patch.object(image,'_open_image') as opened,self.assertRaises(ns.NamespaceProofError):self.acquire()
        opened.assert_not_called()

    def test_extra_control_descriptor_refuses_before_image_acquisition(self):
        with patch.object(image.worker,'require_projected_root',side_effect=ns.NamespaceProofError('fd')), \
             patch.object(image,'_open_image') as opened,self.assertRaises(ns.NamespaceProofError):self.acquire()
        opened.assert_not_called()

    def test_program_bytes_change_refuses_and_closes_descriptor(self):
        self.program.chmod(0o755);self.program.write_bytes(elf());self.program.chmod(0o555)
        self.assert_closed_on_failure()

    def assert_closed_on_failure(self):
        from hosted_synthetic_runtime_regression import _descriptor_inventory
        before=_descriptor_inventory()
        with self.assertRaises((ns.NamespaceProofError,OSError,TypeError)):self.acquire()
        self.assertEqual(_descriptor_inventory(),before)

    def test_writable_or_setid_image_refuses(self):
        for mode in (0o755,0o4555,0o444):
            with self.subTest(mode=mode):self.program.chmod(mode);self.assert_closed_on_failure()

    def test_hardlinked_image_refuses(self):
        os.link(self.program,self.directory/'alias');self.assert_closed_on_failure()

    def test_symlink_image_refuses_without_following_target(self):
        target=self.program.with_name('other');self.program.rename(target);self.program.symlink_to(target)
        self.assert_closed_on_failure()

    def test_missing_or_changed_loader_closes_already_owned_image(self):
        self.loader.unlink();self.assert_closed_on_failure()

    def test_file_on_wrong_mount_refuses(self):
        with patch.object(ns,'_mount_id',return_value=45):self.assert_closed_on_failure()

    def test_inheritable_image_descriptor_refuses(self):
        with patch.object(image.os,'get_inheritable',return_value=True):self.assert_closed_on_failure()

    def test_image_constructor_failure_closes_both_descriptors(self):
        with patch.object(image,'VersionImage',None):self.assert_closed_on_failure()

    def test_final_entry_refusal_closes_acquired_image(self):
        self.entry.side_effect=[None,ns.NamespaceProofError('changed')]
        self.assert_closed_on_failure()

    def test_loader_replacement_is_detected_even_with_identical_bytes(self):
        value=self.acquire();replacement=self.directory/'new-loader'
        replacement.write_bytes(self.loader.read_bytes());replacement.chmod(0o555);replacement.replace(self.loader)
        with self.assertRaises(ns.NamespaceProofError):value.verify()

    def test_copied_image_cannot_close_owned_descriptor(self):
        value=self.acquire();copied=copy.copy(value)
        with self.assertRaises(ns.NamespaceProofError):copied.close()
        os.fstat(value.descriptor.number)

    def test_foreign_fd_replacement_is_preserved_and_cleanup_is_sticky(self):
        value=self.acquire();number=value.descriptor.number
        path=self.directory/'foreign';path.write_bytes(b'foreign')
        foreign=os.open(path,os.O_RDONLY|os.O_CLOEXEC)
        try:
            os.dup2(foreign,number,inheritable=False)
            with self.assertRaises(ns.NamespaceProofError):value.close()
            self.assertTrue(value.closed and value.cleanup_failed)
            with self.assertRaises(ns.NamespaceProofError):value.close()
            self.assertEqual(os.pread(number,7,0),b'foreign')
            os.close(number)
        finally:os.close(foreign)

    def test_failed_identity_precondition_does_not_block_owned_close(self):
        value=self.acquire();number=value.descriptor.number
        self.entry.side_effect=ns.NamespaceProofError('late')
        with self.assertRaises(ns.NamespaceProofError):value.verify()
        value.close()
        with self.assertRaises(OSError):os.fstat(number)

    def test_pending_cancellation_closes_untransferred_image(self):
        from hosted_synthetic_runtime_regression import _descriptor_inventory
        before=_descriptor_inventory()
        with patch.object(image._resource_operation.__globals__['signal'],'pthread_sigmask',side_effect=[set(),KeyboardInterrupt()]), \
             self.assertRaises(KeyboardInterrupt):self.acquire()
        self.assertEqual(_descriptor_inventory(),before)

    def test_original_error_survives_secondary_owned_close_failure(self):
        original=ns.NamespaceProofError('verify');descriptor=Mock();descriptor.close.side_effect=OSError('close')
        with patch.object(image,'_open_image',side_effect=[(descriptor,self.program.read_bytes()),original]), \
             self.assertRaises(ns.NamespaceCleanupError) as caught:self.acquire()
        self.assertIs(caught.exception.__cause__,original)

    def test_loader_close_failure_still_closes_executable_and_keeps_cause(self):
        original=OSError('loader close');program=Mock();loader=Mock()
        loader.close.side_effect=original
        with patch.object(image,'_open_image',side_effect=[(program,self.program.read_bytes()),(loader,b'loader')]), \
             self.assertRaises(ns.NamespaceCleanupError) as caught:self.acquire()
        self.assertIs(caught.exception.__cause__,original);program.close.assert_called_once()


class PostgresSpecEntryTests(unittest.TestCase):
    def spec(self):
        value=image.VersionSpec('a'*64,'b'*40,'c'*40,'d'*64,'pg_dump',image.pg.BIN+'pg_dump','e'*64,
            '/lib/loader','f'*64,(1,2),3,99,
            tuple((name,(1,n)) for n,name in enumerate(('user','mnt','pid','net','cgroup'),1)),4)
        object.__setattr__(value,'_owner',value);object.__setattr__(value,'_token',image._TOKEN)
        return value

    def test_unowned_or_copied_spec_refuses(self):
        value=self.spec()
        with self.assertRaises(ns.NamespaceProofError):copy.copy(value).verify()
        object.__setattr__(value,'_token',None)
        with self.assertRaises(ns.NamespaceProofError):value.verify()

    def test_duck_typed_spec_refuses_before_calling_supplied_verifier(self):
        value=Mock()
        with self.assertRaises(ns.NamespaceProofError):image._worker_entry(value)
        value.verify.assert_not_called()

    def prepare_context(self):
        value=Mock(generation='a'*64,source_commit='b'*40,source_tree='c'*40,
            bundle=Mock(sha256='d'*64),native_files={image.pg.BIN+'pg_dump':'e'*64,'/lib64/ld-linux-x86-64.so.2':'f'*64})
        entries={image.pg.BIN+'pg_dump':Mock(content=elf()),'/lib64/ld-linux-x86-64.so.2':Mock(mode=0o555)}
        self.enterContext(patch.object(image,'_signal_boundary'))
        self.enterContext(patch.object(image.os,'getpid',return_value=1))
        self.enterContext(patch.object(image.os,'getppid',return_value=0))
        self.enterContext(patch.object(ns,'_require_mapped_identity'))
        self.enterContext(patch.object(ns,'_read',return_value=b''))
        self.enterContext(patch.object(ns,'parse_status',return_value={'nspid':[1]}))
        self.enterContext(patch.object(image.pg,'verify_postgres_phase_consumer',return_value={'root_identity':[1,2],'root_mount_id':3}))
        self.enterContext(patch.object(image.pg,'_phase_inventory',return_value=entries))
        self.enterContext(patch.object(image.worker,'_status',return_value={'Seccomp_filters':3}))
        self.enterContext(patch.object(ns,'_process',return_value={'parent':0,'start':99}))
        self.enterContext(patch.object(image.identity,'_namespaces',return_value={k:list(v) for k,v in self.spec().namespaces}))
        return value

    def test_private_init_mints_source_bound_fixed_role_and_expected_filter_count(self):
        value=self.prepare_context();spec=image.prepare_version_spec(value,{'root':'observation'},'pg_dump')
        spec.verify()
        self.assertEqual(spec.executable,image.pg.BIN+'pg_dump')
        self.assertEqual(spec.loader,'/lib64/ld-linux-x86-64.so.2')
        self.assertEqual(spec.filter_count,4);self.assertEqual(spec.source_tree,'c'*40)
        self.assertEqual(spec.init_start,99)
        image.pg.verify_postgres_phase_consumer.assert_called_once_with(value,{'root':'observation'})

    def test_unverified_phase_never_mints_spec(self):
        value=self.prepare_context()
        image.pg.verify_postgres_phase_consumer.side_effect=ns.NamespaceProofError('phase')
        with self.assertRaises(ns.NamespaceProofError):image.prepare_version_spec(value,{},'pg_dump')
        image.pg._phase_inventory.assert_not_called()

    def test_host_proc_or_existing_child_refuses_before_phase_read(self):
        value=self.prepare_context()
        with patch.object(ns,'parse_status',return_value={'nspid':[500,1]}),self.assertRaises(ns.NamespaceProofError):
            image.prepare_version_spec(value,{},'pg_dump')
        with patch.object(ns,'_read',return_value=b'2 '),self.assertRaises(ns.NamespaceProofError):
            image.prepare_version_spec(value,{},'pg_dump')
        image.pg.verify_postgres_phase_consumer.assert_not_called()

    def test_noncanonical_role_refuses_before_phase_or_kernel_work(self):
        with patch.object(image,'_signal_boundary'),patch.object(image.pg,'verify_postgres_phase_consumer') as verify, \
             self.assertRaises(ns.NamespaceProofError):image.prepare_version_spec(None,None,'/bin/sh')
        verify.assert_not_called()

    def test_only_private_init_can_prepare_spec(self):
        with patch.object(image,'_signal_boundary'),patch.object(image.os,'getpid',return_value=2), \
             patch.object(image.pg,'verify_postgres_phase_consumer') as verify,self.assertRaises(ns.NamespaceProofError):
            image.prepare_version_spec(None,None,'pg_dump')
        verify.assert_not_called()

    def contexts(self,value,*,drift=None):
        current={k:list(v) for k,v in value.namespaces};current['user']=[9,9]
        if drift:current[drift]=[8,8]
        contexts=[patch.object(image,'_signal_boundary'),patch.object(image.core,'_entry'),
            patch.object(image.os,'getpid',return_value=2),patch.object(image.os,'getppid',return_value=1),
            patch.object(ns,'_process',return_value={'start':99,'parent':0}),
            patch.object(image.identity,'_inner_identity'),patch.object(image.identity,'_death_signal'),
            patch.object(image.identity,'_namespaces',return_value=current),
            patch.object(image.worker,'_status',return_value={'Seccomp_filters':4}),
            patch.object(ns,'_read',return_value=b'synthetic'),patch.object(ns,'parse_status',return_value={'nspid':[2]})]
        for context in contexts:self.enterContext(context)

    def test_reduced_worker_uses_captured_parent_tuple_without_upward_ptrace(self):
        value=self.spec();self.contexts(value)
        image._worker_entry(value)
        image.identity._namespaces.assert_called_once_with('self')

    def test_worker_mount_pid_net_or_cgroup_drift_refuses(self):
        for name in ('mnt','pid','net','cgroup'):
            with self.subTest(name=name),self.sub_context():
                value=self.spec();self.contexts(value,drift=name)
                with self.assertRaises(ns.NamespaceProofError):image._worker_entry(value)

    def sub_context(self):
        # Restore each nested patch before testing the next independent drift.
        import contextlib
        @contextlib.contextmanager
        def context():
            old=len(self._cleanups)
            try:yield
            finally:
                while len(self._cleanups)>old:
                    function,args,kwargs=self._cleanups.pop();function(*args,**kwargs)
        return context()

    def test_wrong_filter_count_refuses(self):
        value=self.spec();self.contexts(value)
        with patch.object(image.worker,'_status',return_value={'Seccomp_filters':3}),self.assertRaises(ns.NamespaceProofError):
            image._worker_entry(value)

    def test_dead_replaced_or_reparented_init_refuses(self):
        value=self.spec();self.contexts(value)
        with patch.object(ns,'_process',return_value={'start':100,'parent':0}),self.assertRaises(ns.NamespaceProofError):
            image._worker_entry(value)

    def test_unreduced_namespace_refuses(self):
        value=self.spec();self.contexts(value)
        with patch.object(image.identity,'_namespaces',return_value={k:list(v) for k,v in value.namespaces}), \
             self.assertRaises(ns.NamespaceProofError):image._worker_entry(value)

    def test_zero_capability_precondition_refuses_before_namespace_reads(self):
        value=self.spec()
        with patch.object(image,'_signal_boundary'),patch.object(image.core,'_entry',side_effect=ns.NamespaceProofError('cap')), \
             patch.object(image.identity,'_namespaces') as namespaces,self.assertRaises(ns.NamespaceProofError):
            image._worker_entry(value)
        namespaces.assert_not_called()

    def test_exact_fifo_and_image_fd_inventory_rejects_extra_foreign_handle(self):
        read,write=os.pipe2(os.O_CLOEXEC)
        with tempfile.TemporaryFile() as file:
            channel=ns._Descriptor.capture(read)
            try:
                fixed=['0','1','2',str(file.fileno()),str(read)]
                with patch.object(image.os,'listdir',return_value=fixed):
                    image._descriptors(Mock(number=file.fileno()),(channel,))
                with patch.object(image.os,'listdir',return_value=[*fixed,str(write)]),self.assertRaises(ns.NamespaceProofError):
                    image._descriptors(Mock(number=file.fileno()),(channel,))
                os.fstat(write)
            finally:channel.close();os.close(write)

    def test_regular_control_file_cannot_masquerade_as_protocol_channel(self):
        with tempfile.TemporaryFile() as file:
            channel=ns._Descriptor.capture(os.dup(file.fileno()))
            try:
                with self.assertRaises(ns.NamespaceProofError):image._descriptors(Mock(number=999),(channel,))
                os.fstat(channel.number)
            finally:channel.close()

    def test_root_readback_failure_survives_descriptor_close_failure(self):
        value=self.spec();root=Mock(number=999,identity=value.root_identity)
        root.close.side_effect=OSError('close')
        info=Mock(st_dev=1,st_ino=2)
        with patch.object(image.os,'stat',return_value=info),patch.object(ns,'_acquire',return_value=root), \
             patch.object(image.os,'fstatvfs',return_value=Mock(f_flag=0)),patch.object(ns,'_mount_id',return_value=3), \
             patch.object(ns.backend,'_browser_fstatfs_magic',return_value=0),self.assertRaises(ns.NamespaceCleanupError) as caught:
            image._root(value)
        self.assertIsInstance(caught.exception.__cause__,ns.NamespaceProofError)
        root.close.assert_called_once()
