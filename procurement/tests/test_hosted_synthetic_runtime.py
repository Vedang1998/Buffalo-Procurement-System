"""Hosted closure parsing and sealed-byte tests, independent of live mounts."""
from pathlib import Path
from dataclasses import replace
import copy
import errno
import fcntl
import hashlib
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

import hosted_synthetic_runtime as runtime
import hosted_synthetic_namespace as ns


def elf(*, needed=b'libc.so.6', tag=1, interpreter=b'/lib64/ld-linux-x86-64.so.2'):
    data=bytearray(768)
    struct.pack_into('<16sHHIQQQIHHHHHH',data,0,b'\x7fELF\x02\x01\x01'+b'\0'*9,
                     3,62,1,0,64,0,0,64,56,3,0,0,0)
    struct.pack_into('<IIQQQQQQ',data,64,1,5,0,0x400000,0,len(data),len(data),4096)
    struct.pack_into('<IIQQQQQQ',data,120,2,4,256,0x400100,0,64,64,8)
    struct.pack_into('<IIQQQQQQ',data,176,3,4,512,0x400200,0,len(interpreter)+1,len(interpreter)+1,1)
    for index,row in enumerate(((5,0x400180),(10,len(needed)+1),(tag,0),(0,0))):struct.pack_into('<qQ',data,256+16*index,*row)
    data[384:384+len(needed)+1]=needed+b'\0';data[512:513+len(interpreter)]=interpreter+b'\0'
    return bytes(data)


def fixture_bundle():
    data=elf();native={'/fixture/python':hashlib.sha256(data).hexdigest()};source={}
    identity=runtime._identity_bytes('a'*64,'/fixture/python',source,native,'b'*40,'c'*40)
    entries=tuple(ns.backend._BrowserRuntimeBundleEntry(path=path,kind='F',mode=mode,user_id=0,group_id=0,
        provenance='native-runtime:'+hashlib.sha256(content).hexdigest(),content=content)
        for path,content,mode in (('/fixture/python',data,0o555),(runtime._IDENTITY_PATH,identity,0o444),
            (runtime.SENTINEL,__import__('hosted_synthetic_projection').sentinel_bytes(),0o555)))
    bundle=ns.backend._seal_browser_runtime_bundle(ns.backend._complete_browser_runtime_bundle_parents(entries))
    value=runtime.RuntimeBundle(bundle,'a'*64,'/fixture/python',source,native,'b'*40,'c'*40)
    value._owner=value;value._token=runtime._TOKEN
    return value


class HostedRuntimeTests(unittest.TestCase):
    def test_elf_interpreter_and_needed_are_extracted_without_execution(self):
        self.assertEqual(runtime.parse_elf(elf()),{'interpreter':'/lib64/ld-linux-x86-64.so.2','needed':('libc.so.6',),'soname':None})

    def test_wrong_architecture_class_and_truncated_tables_refuse(self):
        variants=[b'',elf()[:63],b'notelf'+elf()[6:]]
        for index,value in ((4,1),(5,2),(18,183),(54,55)):
            data=bytearray(elf());data[index]=value;variants.append(bytes(data))
        data=bytearray(elf());struct.pack_into('<Q',data,32,len(data));variants.append(bytes(data))
        for data in variants:
            with self.subTest(size=len(data)),self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(data)

    def test_dynamic_string_table_must_map_to_one_file_backed_segment(self):
        for address in (0,0x400fff,2**64-1):
            data=bytearray(elf());struct.pack_into('<Q',data,264,address)
            with self.subTest(address=address),self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(bytes(data))

    def test_dynamic_termination_and_bounds_are_required(self):
        data=bytearray(elf());struct.pack_into('<qQ',data,304,1,0)
        with self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(bytes(data))
        data=bytearray(elf());struct.pack_into('<Q',data,152,65)
        with self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(bytes(data))

    def test_needed_names_cannot_select_paths_or_loader_tokens(self):
        for name in (b'../lib.so',b'/host/lib.so',b'$ORIGIN/lib.so',b'',b'bad name',b'\xff'):
            with self.subTest(name=name),self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(elf(needed=name))

    def test_search_override_and_injected_loader_tags_refuse(self):
        for tag in (15,29,*runtime._ELF_FORBIDDEN_TAGS):
            with self.subTest(tag=tag),self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(elf(tag=tag))

    def test_interpreter_must_be_an_absolute_canonical_path(self):
        for value in (b'relative',b'/lib/../host',b'/lib//ld.so',b'/bad name',b'/lib/ld\0x'):
            with self.subTest(value=value),self.assertRaises(ns.NamespaceProofError):runtime.parse_elf(elf(interpreter=value))

    def test_distinct_library_candidates_refuse_instead_of_guessing(self):
        with patch.object(os.path,'lexists',return_value=True), \
             patch.object(runtime,'read_native',side_effect=[(b'first',0o555),(b'second',0o555)]), \
             self.assertRaises(ns.NamespaceProofError):runtime._resolve_library('lib.so',(Path('/one'),Path('/two')))

    def test_equal_library_aliases_are_accepted_and_missing_libraries_refuse(self):
        with patch.object(os.path,'lexists',return_value=True),patch.object(runtime,'read_native',return_value=(b'same',0o555)):
            self.assertEqual(runtime._resolve_library('lib.so',(Path('/one'),Path('/two'))),('/one/lib.so',b'same',0o555))
        with patch.object(os.path,'lexists',return_value=False),self.assertRaises(ns.NamespaceProofError):runtime._resolve_library('missing',(Path('/one'),))

    def test_user_owned_or_writable_native_source_is_not_promoted_to_trust(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'runtime';path.write_bytes(elf())
            with self.assertRaises(ns.NamespaceProofError):runtime.read_native(path)

    def test_sealed_bundle_refuses_write_and_has_no_writer_descriptor(self):
        value=fixture_bundle()
        try:
            self.assertEqual(fcntl.fcntl(value.bundle.descriptor,fcntl.F_GETFL)&os.O_ACCMODE,os.O_RDONLY)
            self.assertEqual(fcntl.fcntl(value.bundle.descriptor,fcntl.F_GET_SEALS),ns.backend._BROWSER_RUNTIME_BUNDLE_SEALS)
            with self.assertRaises(OSError):os.pwrite(value.bundle.descriptor,b'x',0)
            self.assertTrue(runtime._entries(value))
        finally:value.close()

    def test_generation_commit_and_dependency_maps_are_bound_inside_sealed_bytes(self):
        for field,new in (('generation','d'*64),('source_commit','e'*40),('source_tree','f'*40),('native_files',{'/fixture/python':'0'*64})):
            value=fixture_bundle();old=getattr(value,field)
            try:
                setattr(value,field,new)
                with self.subTest(field=field),self.assertRaises(ns.NamespaceProofError):runtime._entries(value)
            finally:setattr(value,field,old);value.close()

    def test_copied_handle_cannot_close_original(self):
        value=fixture_bundle()
        try:
            with self.assertRaises(ns.NamespaceProofError):copy.copy(value).close()
            value.verify()
        finally:value.close()

    def test_compact_resume_reconstructs_exact_inventory_from_sealed_identity(self):
        import json
        value=fixture_bundle();received=None
        fd=os.dup(value.bundle.descriptor)
        try:
            packet=value.packet();packet['bundle']['descriptor']=fd
            self.assertLess(len(json.dumps(packet)),2048)
            self.assertNotIn('native_files',packet)
            os.set_inheritable(fd,True)
            received=runtime.receive_runtime(packet)
            self.assertEqual(received.native_files,value.native_files)
            self.assertEqual(received.source_commit,value.source_commit)
            self.assertFalse(os.get_inheritable(fd))
        finally:
            if received is not None:received.close()
            else:os.close(fd)
            value.close()

    def test_replaced_resume_descriptor_stays_open_and_inheritable(self):
        value=fixture_bundle();foreign=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
        try:
            packet=value.packet();packet['bundle']['descriptor']=foreign
            os.set_inheritable(foreign,True)
            with self.assertRaises(ns.NamespaceProofError):runtime.receive_runtime(packet)
            self.assertTrue(os.get_inheritable(foreign))
            self.assertEqual(os.readlink(f'/proc/self/fd/{foreign}'),'/dev/null')
        finally:os.close(foreign);value.close()

    def test_consumer_refuses_forged_authority_and_unbound_root_fields(self):
        value=fixture_bundle()
        base={'policy':runtime.POLICY,'generation':value.generation,'bundle_sha256':value.bundle.sha256,
              'manifest_sha256':value.bundle.manifest_sha256,'root_identity':[1,2],'root_mount_id':3,
              'regular_bytes':value.bundle.regular_bytes,'old_root_detached':True,
              'consumer_exec_verified':False,'execution_authority':False}
        try:
            for field,changed in (('execution_authority',True),('credential_release','RELEASED'),
                    ('old_root_detached',False),('consumer_exec_verified',True),('regular_bytes',True),
                    ('generation','b'*64),('policy','other'),('root_identity',[True,2])):
                with self.subTest(field=field),patch.object(runtime.os,'stat') as observe,self.assertRaises(ns.NamespaceProofError):
                    runtime.verify_consumer(value,dict(base,**{field:changed}))
                observe.assert_not_called()
        finally:value.close()

    def test_delegated_entry_requires_runtime_before_exec_or_projection(self):
        with patch.object(ns,'_runtime_images') as images,patch.object(runtime,'enter_runtime') as project, \
             self.assertRaises(ns.NamespaceProofError):
            ns._reexec_guardian(None,None,None,'a'*64,123,0.0,900,900,{},True)
        images.assert_not_called();project.assert_not_called()

    def test_supervisor_binds_report_to_its_own_generation_source_and_bundle(self):
        value=fixture_bundle()
        proof={'policy':runtime.POLICY,'generation':value.generation,'bundle_sha256':value.bundle.sha256,
               'manifest_sha256':value.bundle.manifest_sha256,'source_commit':value.source_commit,'source_tree':value.source_tree,
               'root_identity':[1,2],'root_mount_id':3,'regular_bytes':value.bundle.regular_bytes,
               'old_root_detached':True,'consumer_exec_verified':True,'mapped_elf_files':1,'execution_authority':False,
               'scope':'HOSTED_DIAGNOSTIC_RUNTIME','application_browser_closure':'NOT_PROVEN'}
        try:
            runtime.verify_report_binding(value,proof)
            for key,changed in (('generation','b'*64),('source_commit','e'*40),('source_tree','f'*40),
                    ('bundle_sha256','0'*64),('manifest_sha256','0'*64),('execution_authority',True),
                    ('mapped_elf_files',True),('unknown',False),('application_browser_closure','PASS')):
                with self.subTest(key=key),self.assertRaises(ns.NamespaceProofError):
                    runtime.verify_report_binding(value,dict(proof,**{key:changed}))
        finally:value.close()

    def test_identity_gate_precedes_every_projection_effect(self):
        value=fixture_bundle()
        try:
            with patch.object(ns,'_require_mapped_identity',side_effect=ns.NamespaceProofError('identity')), \
                 patch.object(runtime,'_enter_projection') as project,self.assertRaises(ns.NamespaceProofError):runtime.enter_runtime(value)
            project.assert_not_called()
        finally:value.close()

    def test_consumer_cannot_relabel_current_host_root_as_projected_exec(self):
        value=fixture_bundle()
        try:
            proof={'generation':value.generation,'bundle_sha256':value.bundle.sha256,'manifest_sha256':value.bundle.manifest_sha256,'root_identity':[1,2]}
            with self.assertRaises(ns.NamespaceProofError):runtime.verify_consumer(value,proof)
        finally:value.close()


if __name__=='__main__':unittest.main()
