"""Disposable held-directory materialization/cleanup, no live restore claims."""
from dataclasses import replace
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest import mock

import hosted_synthetic_recovery_inputs as inputs
import hosted_synthetic_recovery_storage as s
from test_hosted_synthetic_recovery_inputs import descriptors,storage_fixture,archive_bytes


class RecoveryStorageMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.parent=Path(self.tmp.name);self.addCleanup(self.tmp.cleanup)
        records,inventory,bindings=storage_fixture()
        self.members,_=inputs.validate_storage_archive(archive_bytes(records),inventory,bindings)
        self.nonce='a'*32;self.root=self.parent/('buffalo-recovered-storage-'+self.nonce)
        self.before=descriptors()

    def tearDown(self):self.assertEqual(descriptors(),self.before)

    def materialize(self):return s.materialize_storage(str(self.parent),self.nonce,self.members)

    def test_actual_bytes_modes_and_owned_cleanup(self):
        with self.materialize() as value:
            proof=value.verify();self.assertEqual(proof['files'],2);self.assertFalse(proof['execution_authority'])
            self.assertEqual(proof['external_handles'],'NOT_PROVEN')
            for member in self.members:
                path=Path(value.path)/member.name;self.assertEqual(path.read_bytes(),member.data)
                self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(self.root.stat().st_mode&0o777,0o700)
        self.assertEqual(value.state,'REMOVED');self.assertFalse(self.root.exists())

    def test_existing_nonce_directory_is_preserved(self):
        self.root.mkdir();marker=self.root/'foreign';marker.write_text('existing component')
        with self.assertRaises(FileExistsError):
            with self.materialize():self.fail('entered')
        self.assertEqual(marker.read_text(),'existing component')

    def test_nonprivate_parent_and_symlink_ancestor_refuse_before_creation(self):
        self.parent.chmod(0o755)
        with self.assertRaises(inputs.RecoveryInputRefusal):
            with self.materialize():self.fail('entered')
        self.assertFalse(self.root.exists());self.parent.chmod(0o700)
        link=self.parent/'alias';link.symlink_to(self.parent,target_is_directory=True)
        with self.assertRaises(OSError):
            with s.materialize_storage(str(link),self.nonce,self.members):self.fail('entered')
        self.assertFalse(self.root.exists())

    def test_invalid_member_paths_hashes_duplicates_and_nonce_refuse(self):
        for members in ((replace(self.members[0],name='../foreign'),),self.members+self.members,
                        (replace(self.members[0],data=b'changed'),*self.members[1:])):
            with self.assertRaises(inputs.RecoveryInputRefusal):
                with s.materialize_storage(str(self.parent),self.nonce,members):self.fail('entered')
        with self.assertRaises(inputs.RecoveryInputRefusal):
            with s.materialize_storage(str(self.parent),'../bad',self.members):self.fail('entered')
        self.assertFalse(self.root.exists())

    def test_partial_file_write_is_removed_without_losing_original_error(self):
        error=OSError('injected partial write')
        def partial(fd,data,**kwargs):os.write(fd,data[:2]);raise error
        with mock.patch.object(inputs,'_write_all',side_effect=partial),self.assertRaises(OSError) as observed:
            with self.materialize():self.fail('entered')
        self.assertIs(observed.exception,error);self.assertFalse(self.root.exists())

    def test_pending_cancellation_during_write_removes_only_owned_tree(self):
        original=inputs._write_all;sent=False
        def cancel(fd,data,**kwargs):
            nonlocal sent
            original(fd,data,**kwargs)
            if not sent:sent=True;os.kill(os.getpid(),signal.SIGINT)
        with mock.patch.object(inputs,'_write_all',side_effect=cancel),self.assertRaises(KeyboardInterrupt):
            with self.materialize():self.fail('entered')
        self.assertFalse(self.root.exists())

    def test_body_error_preserved_after_full_component_cleanup(self):
        error=ValueError('component body failure')
        with self.assertRaises(ValueError) as observed:
            with self.materialize() as value:raise error
        self.assertIs(observed.exception,error);self.assertEqual(value.state,'REMOVED')
        self.assertFalse(self.root.exists())

    def test_unknown_file_blocks_all_path_cleanup_and_preserves_original_failure(self):
        error=ValueError('original failure')
        with self.assertRaises(inputs.RecoveryInputCleanupError) as observed:
            with self.materialize() as value:
                marker=self.root/'unknown';marker.write_bytes(b'foreign component')
                raise error
        self.assertIs(observed.exception.__cause__,error);self.assertEqual(value.state,'CLEANUP_REFUSED')
        self.assertEqual(marker.read_bytes(),b'foreign component')
        for member in self.members:self.assertTrue((self.root/member.name).exists())

    def test_replaced_leaf_preserves_foreign_and_all_other_files(self):
        with self.assertRaises(inputs.RecoveryInputCleanupError):
            with self.materialize() as value:
                leaf=self.root/self.members[0].name;leaf.unlink();leaf.write_bytes(b'foreign replacement')
        self.assertEqual(value.state,'CLEANUP_REFUSED');self.assertEqual(leaf.read_bytes(),b'foreign replacement')
        self.assertTrue((self.root/self.members[1].name).exists())

    def test_replaced_root_is_not_removed_and_held_original_survives(self):
        moved=self.parent/'moved-component'
        with self.assertRaises(inputs.RecoveryInputCleanupError):
            with self.materialize():
                self.root.rename(moved);self.root.mkdir();(self.root/'foreign').write_bytes(b'foreign')
        self.assertTrue((self.root/'foreign').exists())
        self.assertTrue((moved/self.members[0].name).exists())

    def test_failed_exit_signal_precondition_closes_descriptors_without_path_mutation(self):
        original=inputs._signal_boundary;count=0
        def fail_exit():
            nonlocal count
            count+=1
            if count==2:raise inputs.RecoveryInputRefusal('exit precondition')
            original()
        with mock.patch.object(inputs,'_signal_boundary',side_effect=fail_exit),self.assertRaises(inputs.RecoveryInputCleanupError):
            with self.materialize() as value:pass
        self.assertEqual(value.state,'CLEANUP_REFUSED')
        for member in self.members:self.assertTrue((self.root/member.name).exists())

    def test_ambiguous_directory_open_preserves_created_name(self):
        original=s._open_directory
        def fail(parent,name,owners):
            if name.startswith('buffalo-recovered-storage-'):raise OSError('ambiguous native open')
            return original(parent,name,owners)
        with mock.patch.object(s,'_open_directory',side_effect=fail),self.assertRaises(inputs.RecoveryInputCleanupError) as observed:
            with self.materialize():self.fail('entered')
        self.assertIsInstance(observed.exception.__cause__,OSError);self.assertTrue(self.root.exists())

    def test_changed_parent_permissions_refuse_cleanup(self):
        with self.assertRaises(inputs.RecoveryInputCleanupError):
            with self.materialize() as value:self.parent.chmod(0o755)
        self.assertEqual(value.state,'CLEANUP_REFUSED');self.assertTrue(self.root.exists())
        self.parent.chmod(0o700)

    def test_symlink_or_hardlink_in_owned_tree_is_not_followed_or_removed(self):
        for link in ('symlink','hardlink'):
            self.nonce=('b' if link=='symlink' else 'c')*32;self.root=self.parent/('buffalo-recovered-storage-'+self.nonce)
            with self.subTest(link=link),self.assertRaises(inputs.RecoveryInputCleanupError):
                with self.materialize():
                    leaf=self.root/self.members[0].name
                    if link=='symlink':leaf.unlink();leaf.symlink_to('/dev/null')
                    else:os.link(leaf,self.parent/('extra-'+link))
            self.assertTrue(self.root.exists())

    def test_descriptor_registration_failure_closes_root_handle_without_mutation(self):
        with mock.patch.object(inputs,'_register_descriptor',side_effect=lambda d,o:(d.close(),(_ for _ in ()).throw(MemoryError('registration')))[1]),self.assertRaises(MemoryError):
            with self.materialize():self.fail('entered')
        self.assertFalse(self.root.exists())

    def test_renamed_parent_before_first_mutation_refuses_without_creating_root(self):
        original=s._open_directory;moved=self.parent.with_name(self.parent.name+'-moved')
        def replace_parent(parent,name,owners):
            descriptor=original(parent,name,owners)
            if name==self.parent.name:
                self.parent.rename(moved);self.parent.mkdir(mode=0o700)
            return descriptor
        try:
            with mock.patch.object(s,'_open_directory',side_effect=replace_parent),mock.patch.object(s.os,'mkdir',wraps=os.mkdir) as mkdir:
                # The wrapper itself creates only the replacement parent.
                with self.assertRaises(inputs.RecoveryInputRefusal):
                    with self.materialize():self.fail('entered')
                self.assertFalse(any(call.args and str(call.args[0]).startswith('buffalo-recovered-storage-') for call in mkdir.call_args_list))
            self.assertEqual(list(moved.iterdir()),[]);self.assertEqual(list(self.parent.iterdir()),[])
        finally:
            if moved.exists():moved.rmdir()

    def test_parent_replacement_between_short_writes_refuses_remaining_write(self):
        original=os.write;mutated=False;written=[];moved=self.parent/'moved-component'
        def replace_root(fd,data):
            nonlocal mutated
            if not mutated:
                count=original(fd,data[:2]);written.append(count);mutated=True
                self.root.rename(moved);self.root.mkdir();return count
            written.append(len(data));return original(fd,data)
        with mock.patch.object(inputs.os,'write',side_effect=replace_root),self.assertRaises(inputs.RecoveryInputCleanupError):
            with self.materialize():self.fail('entered')
        self.assertEqual(written,[2]);self.assertEqual(list(self.root.iterdir()),[])
        self.assertEqual((moved/self.members[0].name).read_bytes(),self.members[0].data[:2])
