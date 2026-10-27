"""Disposable kernel-seal/file/tar checks, not PostgreSQL or restore execution."""
import ast
from dataclasses import replace
import errno
import fcntl
import hashlib
import io
import os
from pathlib import Path
import signal
import stat
import tarfile
import tempfile
import unittest
from unittest import mock

import hosted_synthetic_recovery_inputs as r
from test_hosted_synthetic_recovery_toc import fixture,toc

ROOT=Path(__file__).resolve().parents[2]


def archive_bytes(records, *, change=None, format=tarfile.PAX_FORMAT):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w',format=format) as tar:
        for name,data in records:
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o600;info.mtime=info.uid=info.gid=0
            info.uname=info.gname=''
            if change:change(info)
            tar.addfile(info,io.BytesIO(data))
    return out.getvalue()


def storage_fixture():
    data=b'synthetic fixture only\n';digest=hashlib.sha256(data).hexdigest();u='00000000-0000-4000-8000-000000000001'
    records=sorted([(f'price-books/raw/{digest}.csv',data),(f'monday-runs/{u}/vendor-{u}/{digest}.internal.csv',data)])
    inventory=[dict(path=n,bytes=len(d),sha256=hashlib.sha256(d).hexdigest()) for n,d in records]
    bindings=[dict(item,bytes=None if item['path'].startswith('price-books/') else item['bytes']) for item in inventory]
    return records,inventory,bindings


def descriptors():
    out={}
    for name in os.listdir('/proc/self/fd'):
        try:info=os.fstat(int(name))
        except OSError:continue
        out[int(name)]=(info.st_dev,info.st_ino)
    return out


class HeldRecoveryInputsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.records,self.inventory,self.bindings=storage_fixture()
        self.payloads=(b'PGDMP\x01\x0f\x00\x04\x08\x01'+b'fabricated full dump',
                       b'PGDMP\x01\x0f\x00\x04\x08\x01'+b'fabricated schema dump',archive_bytes(self.records))
        self.specs=tuple(r.ArchiveSpec(name,len(data),hashlib.sha256(data).hexdigest()) for name,data in zip(r.LIMITS,self.payloads,strict=True))
        for spec,data in zip(self.specs,self.payloads,strict=True):
            p=self.root/spec.name;p.write_bytes(data);p.chmod(0o600)
        self.before=descriptors()

    def tearDown(self):
        self.assertEqual(descriptors(),self.before)

    def held(self):return r.hold_recovery_inputs(str(self.root),self.specs)

    def test_actual_seals_prevent_write_growth_shrink_and_reseal(self):
        with self.held() as value:
            for fd in value.descriptors():
                self.assertFalse(os.get_inheritable(fd));self.assertEqual(fcntl.fcntl(fd,fcntl.F_GET_SEALS),r._SEALS)
                for action in (lambda:os.pwrite(fd,b'X',0),lambda:os.ftruncate(fd,0),lambda:os.ftruncate(fd,99999),lambda:fcntl.fcntl(fd,fcntl.F_ADD_SEALS,r._SEALS)):
                    with self.assertRaises(OSError) as error:action()
                    self.assertEqual(error.exception.errno,errno.EPERM)
            self.assertEqual(value.read_storage(),self.payloads[2])
        self.assertEqual(value.state,'CLOSED')
        with self.assertRaises(r.RecoveryInputRefusal):value.descriptors()

    def test_toc_use_list_is_sealed_but_no_native_emission_or_restore_claim(self):
        capture=fixture()
        with self.held() as value:
            proof=value.bind_tocs(capture,toc(capture.records),toc(capture.records,schema=True))
            self.assertEqual(fcntl.fcntl(value.use_list.number,fcntl.F_GET_SEALS),r._SEALS)
            self.assertEqual(proof['native_toc_emission'],'CALLER_HELD_DESCRIPTOR_PREREQUISITE')
            self.assertFalse(proof['execution_authority'])
            self.assertEqual(proof['restore_execution'],'NOT_RUN')
            with self.assertRaises(r.RecoveryInputRefusal):value.bind_tocs(capture,toc(capture.records),toc(capture.records,schema=True))

    def test_mutating_original_files_does_not_change_held_bytes(self):
        with self.held() as value:
            (self.root/'storage.tar').write_bytes(b'replaced mutable file')
            self.assertEqual(value.read_storage(),self.payloads[2])

    def test_wrong_hash_size_magic_and_reordered_specs_refuse_without_handles(self):
        cases=[(replace(self.specs[0],sha256='f'*64),*self.specs[1:]),
               (replace(self.specs[0],size=self.specs[0].size+1),*self.specs[1:]),tuple(reversed(self.specs)),
               (replace(self.specs[0],size=True),*self.specs[1:])]
        for specs in cases:
            with self.subTest(specs=specs),self.assertRaises(r.RecoveryInputRefusal):
                with r.hold_recovery_inputs(str(self.root),specs):self.fail('entered')
            self.assertEqual(descriptors(),self.before)
        data=b'not a PostgreSQL custom dump';(self.root/'database.dump').write_bytes(data)
        self.specs=(replace(self.specs[0],size=len(data),sha256=hashlib.sha256(data).hexdigest()),*self.specs[1:])
        with self.assertRaises(r.RecoveryInputRefusal):
            with self.held():self.fail('entered')

    def test_nonprivate_root_and_file_permissions_refuse(self):
        self.root.chmod(0o755)
        with self.assertRaises(r.RecoveryInputRefusal):
            with self.held():self.fail('entered')
        self.root.chmod(0o700);(self.root/'database.dump').chmod(0o644)
        with self.assertRaises(r.RecoveryInputRefusal):
            with self.held():self.fail('entered')

    def test_symlink_hardlink_and_fifo_leaves_refuse(self):
        leaf=self.root/'database.dump';data=leaf.read_bytes()
        leaf.rename(self.root/'original');leaf.symlink_to(self.root/'original')
        with self.assertRaises(r.RecoveryInputRefusal):
            with self.held():self.fail('entered')
        leaf.unlink();os.link(self.root/'original',leaf)
        with self.assertRaises(r.RecoveryInputRefusal):
            with self.held():self.fail('entered')
        leaf.unlink();os.mkfifo(leaf)
        with self.assertRaises(r.RecoveryInputRefusal):
            with self.held():self.fail('entered')

    def test_ancestor_symlink_and_dotdot_roots_refuse(self):
        link=self.root/'alias';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(r.RecoveryInputRefusal):
            with r.hold_recovery_inputs(str(link),self.specs):self.fail('entered')
        with self.assertRaises(r.RecoveryInputRefusal):
            with r.hold_recovery_inputs(str(self.root)+'/../'+self.root.name,self.specs):self.fail('entered')

    def test_partial_write_and_seal_failure_close_all_acquired_handles(self):
        for target in ('_write_all','_new_sealed'):
            original=getattr(r,target);count=0
            def fail(*args,**kwargs):
                nonlocal count
                count+=1
                if count==2:raise OSError('injected component failure')
                return original(*args,**kwargs)
            with self.subTest(target=target),mock.patch.object(r,target,side_effect=fail),self.assertRaises(r.RecoveryInputRefusal):
                with self.held():self.fail('entered')
            self.assertEqual(descriptors(),self.before)

    def test_pending_sigint_during_acquisition_is_delivered_inside_cleanup(self):
        original=r._write_all;sent=False
        def cancel(fd,data):
            nonlocal sent
            original(fd,data)
            if not sent:sent=True;os.kill(os.getpid(),signal.SIGINT)
        with mock.patch.object(r,'_write_all',side_effect=cancel),self.assertRaises(KeyboardInterrupt):
            with self.held():self.fail('entered')

    def test_body_failure_preserved_after_descriptor_cleanup(self):
        error=ValueError('component body failure')
        with self.assertRaises(ValueError) as observed:
            with self.held() as value:raise error
        self.assertIs(observed.exception,error);self.assertEqual(value.state,'CLOSED')

    def test_failed_exit_signal_precondition_still_closes_descriptors(self):
        error=ValueError('original failure')
        original=r._signal_boundary;count=0
        def boundary():
            nonlocal count
            count+=1
            if count==2:raise r.RecoveryInputRefusal('injected exit precondition')
            original()
        with mock.patch.object(r,'_signal_boundary',side_effect=boundary),self.assertRaises(r.RecoveryInputCleanupError) as observed:
            with self.held() as value:raise error
        self.assertIs(observed.exception.__cause__,error);self.assertEqual(value.state,'CLEANUP_REFUSED')

    def test_foreign_replacement_descriptor_is_preserved_while_others_close(self):
        foreign=None
        try:
            with self.assertRaises(r.RecoveryInputCleanupError):
                with self.held() as value:
                    victim=value.archives[0].number
                    foreign=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
                    os.dup2(foreign,victim,inheritable=False)
            self.assertEqual(value.state,'CLEANUP_REFUSED')
            self.assertTrue(stat.S_ISCHR(os.fstat(victim).st_mode))
            os.close(victim)
        finally:
            if foreign is not None:os.close(foreign)

    def test_failed_entry_signal_boundary_allocates_nothing(self):
        with mock.patch.object(r,'_signal_boundary',side_effect=r.RecoveryInputRefusal('entry')),self.assertRaises(r.RecoveryInputRefusal):
            with self.held():self.fail('entered')

    def test_owner_registration_failure_closes_new_memfd(self):
        class RefusingOwners(list):
            def append(self,value):raise MemoryError('injected registration failure')
        with self.assertRaises(MemoryError):r._new_sealed('component',(b'data',),RefusingOwners())

    def test_pending_sigint_during_toc_binding_closes_all_handles(self):
        capture=fixture();original=r._new_sealed
        def cancel(name,chunks,owners):
            result=original(name,chunks,owners)
            if name=='use-list':os.kill(os.getpid(),signal.SIGINT)
            return result
        with self.assertRaises(KeyboardInterrupt):
            with self.held() as value:
                with mock.patch.object(r,'_new_sealed',side_effect=cancel):
                    value.bind_tocs(capture,toc(capture.records),toc(capture.records,schema=True))
        self.assertEqual(value.state,'CLOSED')


class RecoveryStorageTests(unittest.TestCase):
    def setUp(self):
        self.records,self.inventory,self.bindings=storage_fixture();self.raw=archive_bytes(self.records)

    def check(self):return r.validate_storage_archive(self.raw,self.inventory,self.bindings)

    def refuses(self):
        with self.assertRaises(r.RecoveryInputRefusal):self.check()

    def test_actual_existing_writer_long_pax_path_is_supported_without_extraction(self):
        source=ast.parse((ROOT/'procurement/src/procurement_os/synthetic_staging_backup_v2.py').read_text())
        fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='_write_storage_archive')
        namespace={'Path':Path,'Any':object,'os':os,'stat':stat,'tarfile':tarfile,'SyntheticStagingBackupError':RuntimeError}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual-storage-writer','exec'),namespace)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'storage';root.mkdir()
            for name,data in self.records:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            out=Path(temp)/'storage.tar';namespace['_write_storage_archive'](out,storage_root=root,inventory=self.inventory)
            self.raw=out.read_bytes()
        members,proof=self.check();self.assertEqual(tuple((m.name,m.data) for m in members),tuple(self.records))
        self.assertFalse(proof['execution_authority']);self.assertEqual(proof['restore_execution'],'NOT_RUN')
        self.assertIn(b'path=monday-runs/',self.raw)

    def test_extra_credential_missing_or_duplicate_member_refuses(self):
        for records in (self.records+[('credentials.env',b'synthetic')],self.records[:1],self.records+[self.records[0]]):
            self.raw=archive_bytes(records);self.refuses()

    def test_registry_binding_inventory_and_order_are_exact(self):
        self.bindings=self.bindings[:1];self.refuses()
        self.setUp();self.inventory=list(reversed(self.inventory));self.refuses()
        self.setUp();self.inventory.append(self.inventory[0]);self.refuses()
        self.setUp();self.bindings[0]['bytes']+=1;self.refuses()

    def test_hash_content_address_size_and_bool_counts_refuse(self):
        for key,value in [('sha256','f'*64),('bytes',True),('bytes',2**32),('path','../foreign')]:
            self.setUp();self.inventory[0][key]=value;self.refuses()
        self.setUp();self.raw=archive_bytes([(n,b'x'*len(d)) for n,d in self.records]);self.refuses()

    def test_links_devices_sparse_and_noncanonical_metadata_refuse(self):
        for attr,value in [('type',tarfile.SYMTYPE),('type',tarfile.LNKTYPE),('type',tarfile.CHRTYPE),('type',tarfile.GNUTYPE_SPARSE),
                           ('mode',0o644),('uid',1000),('mtime',1),('uname','operator'),('linkname','foreign')]:
            with self.subTest(attr=attr,value=value):
                self.setUp();self.raw=archive_bytes(self.records,change=lambda i:setattr(i,attr,value));self.refuses()

    def test_unnecessary_pax_gnu_and_global_overrides_refuse(self):
        for headers in ({'path':self.records[0][0]},{'comment':'extra'},{'mtime':'0'},{'GNU.sparse.size':'0'}):
            self.setUp();self.raw=archive_bytes(self.records,change=lambda i:setattr(i,'pax_headers',headers));self.refuses()
        self.setUp();self.raw=archive_bytes(self.records,format=tarfile.GNU_FORMAT);self.refuses()

    def test_truncated_concatenated_and_hidden_trailing_archives_refuse(self):
        raw=self.raw
        for changed in (raw[:600],raw[:-512],raw+b'\0'*512,raw+raw,raw[:-1]+b'x'):
            self.raw=changed;self.refuses()

    def test_member_and_total_bounds_are_not_trusted_from_manifest(self):
        self.raw=b'x'*(r.LIMITS['storage.tar']+1);self.refuses()
        self.setUp();self.bindings=self.bindings*65;self.refuses()
        self.setUp();self.inventory[0]['bytes']=1024**2+1;self.refuses()

    def test_price_binding_is_hash_only_and_artifact_requires_registered_size(self):
        self.bindings[1]['bytes']=self.inventory[1]['bytes'];self.refuses()
        self.setUp();self.bindings[0]['bytes']=None;self.refuses()

    def test_repeated_pax_headers_are_refused_before_recursive_parser(self):
        # First actual writer record is the long vendor path and a local PAX header.
        self.assertEqual(self.raw[156:157],tarfile.XHDTYPE)
        self.raw=self.raw[:1024]*1500+self.raw
        self.refuses()

    def test_invalid_run_or_vendor_uuid_refuses_existing_storage_policy(self):
        for segment in ('run','vendor'):
            self.setUp();old='00000000-0000-4000-8000-000000000001';bad='00000000-0000-0000-0000-000000000000'
            name,data=self.records[0]
            name=name.replace('/'+old+'/' if segment=='run' else 'vendor-'+old,
                              '/'+bad+'/' if segment=='run' else 'vendor-'+bad)
            self.records[0]=(name,data);self.inventory[0]['path']=name;self.bindings[0]['path']=name
            self.raw=archive_bytes(self.records);self.refuses()
