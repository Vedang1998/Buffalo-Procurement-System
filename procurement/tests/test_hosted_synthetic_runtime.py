"""Hosted closure parsing and sealed-byte tests, independent of live mounts."""
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
import copy
import errno
import fcntl
import hashlib
import json
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
    def test_capture_refusal_evidence_has_only_bound_source_locations(self):
        import hosted_synthetic_runtime_regression as driver
        try:runtime._aggregate_limit('private-untrusted-policy')
        except ns.NamespaceProofError as error:frames=driver._capture_failure_frames(error)
        self.assertTrue(frames);self.assertLessEqual(len(frames),8)
        self.assertTrue(all(set(row)=={'file','line'} and row['file'].startswith('procurement/tools/') for row in frames))
        self.assertNotIn('private-untrusted-policy',str(frames))
        self.assertEqual(driver._capture_failure_frames(RuntimeError('private-message')),[])

    def test_native_input_observation_reports_untrusted_ownership_without_mutation(self):
        import hosted_synthetic_runtime_regression as driver
        self.assertEqual(driver._native_input_chain('/private/input')['status'],'NOT_OBSERVED')
        info=SimpleNamespace(st_uid=1001,st_gid=1001,st_mode=0o100775)
        with patch.object(Path,'resolve',return_value=Path('/usr/bin/python3.13')),patch.object(Path,'lstat',return_value=info),patch.object(os,'chmod') as chmod,patch.object(os,'chown') as chown:
            report=driver._native_input_chain('/usr/bin/python3.13')
        self.assertEqual(report['status'],'OBSERVED');chmod.assert_not_called();chown.assert_not_called()
        self.assertFalse(any(row['root_owned_no_write'] for rows in report['chains'].values() for row in rows))

    def test_disproven_native_trust_never_retries_combined_capture(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        inputs={'interpreter':{'status':'OBSERVED','chains':{'requested':[{'root_owned_no_write':False}]}}}
        with patch.object(pg,'capture_phase_fit') as combined,patch.object(driver,'_measure_postgres_input',return_value={'status':'PASS','scope':'PG_ONLY_CAPTURE_NOT_COMPOSED_PHASE_OR_EXECUTION'}) as native:
            report=driver._measure_inputs(SimpleNamespace(commit='a'*40,tree='b'*40),Path('/fixture'),inputs)
        combined.assert_not_called();native.assert_called_once()
        self.assertEqual(report['capacity'],'NOT_MEASURED_INPUT_TRUST_REFUSED')
        inputs['postgres_data']=inputs['interpreter']
        with patch.object(pg,'capture_phase_fit') as combined,patch.object(driver,'_measure_postgres_input') as native:
            report=driver._measure_inputs(SimpleNamespace(commit='a'*40,tree='b'*40),Path('/fixture'),inputs)
        combined.assert_not_called();native.assert_not_called()
        self.assertEqual(report['postgres_input']['status'],'NOT_RUN_NATIVE_INPUT_TRUST_REFUSED')
        del inputs['interpreter']
        with patch.object(pg,'capture_phase_fit') as combined,patch.object(driver,'_measure_postgres_input') as native:
            report=driver._measure_inputs(SimpleNamespace(commit='a'*40,tree='b'*40),Path('/fixture'),inputs)
        combined.assert_not_called();native.assert_not_called()
        self.assertEqual(report['capacity'],'NOT_MEASURED_INPUT_TRUST_REFUSED')

    def test_fixed_pg_data_observation_deduplicates_and_detects_changed_metadata(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        def observe(path):return {'status':'OBSERVED','chains':{'requested':[
            {'path':pg.SHARE,'root_owned_no_write':False},{'path':path,'root_owned_no_write':True}]}}
        with patch.object(driver,'_native_input_chain',side_effect=observe):report=driver._postgres_data_inputs()
        rows=report['chains']['distinct_observed_paths'];self.assertEqual(len(rows),len(pg._DATA)+1)
        self.assertEqual(sum(not row['root_owned_no_write'] for row in rows),1)
        counter=iter(range(100))
        def changed(path):return {'status':'OBSERVED','chains':{'requested':[
            {'path':pg.SHARE,'root_owned_no_write':bool(next(counter)%2)}]}}
        with patch.object(driver,'_native_input_chain',side_effect=changed):
            self.assertEqual(driver._postgres_data_inputs()['status'],'METADATA_CHANGED')

    def test_native_pg_input_measurement_owns_cleanup_and_close_refusal(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        from test_hosted_synthetic_postgres_runtime import PostgresRuntimeTests,BINDING
        for refuse in (False,True):
            value=PostgresRuntimeTests.capture(self);fd=value.bundle.descriptor
            try:
                with patch.object(pg,'capture_postgres',return_value=value):
                    if refuse:
                        with patch.object(value,'close',side_effect=ns.NamespaceCleanupError('injected')),self.assertRaises(ns.NamespaceCleanupError):driver._measure_postgres_input(BINDING,Path('/fixture'))
                    else:
                        report=driver._measure_postgres_input(BINDING,Path('/fixture'))
                        self.assertTrue(report['owned_capture_closed']);self.assertEqual(report['status'],'PASS')
                        self.assertEqual(report['maximum_regular_bytes'],83886080)
            finally:
                if getattr(value,'_token',None) is runtime._TOKEN:value.close()
            with self.assertRaises(OSError):os.fstat(fd)

    def test_codec_copy_reduction_keeps_exact_legacy_bytes_and_digest(self):
        raw=b'fixed synthetic bytes'*1000
        entries=(ns.backend._BrowserRuntimeBundleEntry('/a','F',0o444,0,0,'native-runtime:'+hashlib.sha256(raw).hexdigest(),raw),)
        encoded,manifest_hash,bundle_hash,size=ns.backend._encode_browser_runtime_bundle(entries)
        count=int.from_bytes(encoded[:8],'big');manifest=encoded[8:8+count]
        self.assertEqual(encoded,len(manifest).to_bytes(8,'big')+manifest+raw)
        self.assertEqual(bundle_hash,hashlib.sha256(ns.backend._BROWSER_RUNTIME_BUNDLE_MAGIC+manifest+raw).hexdigest())
        self.assertEqual(manifest_hash,hashlib.sha256(manifest).hexdigest());self.assertEqual(size,len(raw))
        parsed=ns.backend._parse_browser_runtime_bundle(encoded)
        self.assertEqual(parsed,(entries,manifest_hash,bundle_hash,size));self.assertIs(type(parsed[0][0].content),bytes)
        corrupt=bytearray(encoded);corrupt[-1]^=1
        with self.assertRaises(ns.backend.LocalStagingAcceptanceError):ns.backend._parse_browser_runtime_bundle(bytes(corrupt))

    def test_sealer_streams_without_calling_aggregate_encoder_or_reader(self):
        raw=b'synthetic'*10000
        entries=(ns.backend._BrowserRuntimeBundleEntry('/a','F',0o444,0,0,'native-runtime:'+hashlib.sha256(raw).hexdigest(),raw),)
        encoded=ns.backend._encode_browser_runtime_bundle(entries)
        with patch.object(ns.backend,'_encode_browser_runtime_bundle',side_effect=AssertionError('aggregate encode')),patch.object(ns.backend,'_read_browser_runtime_bundle_descriptor',side_effect=AssertionError('aggregate read')):
            bundle=ns.backend._seal_browser_runtime_bundle(entries)
            try:
                self.assertEqual(os.pread(bundle.descriptor,bundle.size,0),encoded[0])
                self.assertEqual(ns.backend._validate_pinned_browser_runtime_bundle(bundle),entries)
            finally:self.assertTrue(ns.backend._close_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.target,(bundle.device,bundle.inode)))

    def test_streamed_short_writes_and_eintr_preserve_canonical_bytes(self):
        raw=b'x'*4096;entries=(ns.backend._BrowserRuntimeBundleEntry('/a','F',0o444,0,0,'native-runtime:'+hashlib.sha256(raw).hexdigest(),raw),)
        original=os.write;calls=[]
        def short(fd,data):
            calls.append(len(data))
            if len(calls)==1:raise InterruptedError()
            return original(fd,data[:127])
        with patch.object(os,'write',side_effect=short):bundle=ns.backend._seal_browser_runtime_bundle(entries)
        try:self.assertEqual(ns.backend._validate_pinned_browser_runtime_bundle(bundle),entries)
        finally:ns.backend._close_browser_runtime_bundle_descriptor(bundle.descriptor,bundle.target,(bundle.device,bundle.inode))
        self.assertGreater(len(calls),10)

    def test_owned_cleanup_does_not_allocate_or_validate_payload_again(self):
        value=fixture_bundle();fd=value.bundle.descriptor
        with patch.object(value,'verify',side_effect=MemoryError('payload')),patch.object(ns.backend,'_parse_browser_runtime_bundle_input',side_effect=MemoryError('payload')):
            value.close()
        with self.assertRaises(OSError):os.fstat(fd)

    def test_real_capture_fit_driver_has_no_namespace_or_native_execution(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        for capacity,rss,expected in (('FIT',1024,'PASS'),('REFUSED',1024,'REFUSED_CAPACITY'),('FIT',536870912,'REFUSED_MEMORY_ENVELOPE')):
            # Process-lifetime peaks and host job counters are separate live
            # inputs, not deterministic facts of this mocked capture fixture.
            with self.subTest(capacity=capacity,rss=rss),patch.object(os,'geteuid',return_value=1001),patch.object(os,'getegid',return_value=1001),patch.object(runtime,'read_native',return_value=(b'ID=ubuntu\nVERSION_ID="24.04"\n',0o444)),patch.object(Path,'is_file',return_value=True),patch.object(driver,'_native_input_chain',return_value={'status':'NOT_OBSERVED'}),patch.object(driver.resource,'getrusage',return_value=SimpleNamespace(ru_maxrss=rss)),patch.object(driver,'_shared_job_memory',return_value={'status':'NOT_OBSERVED'}),patch.object(pg,'capture_phase_fit',return_value={'capacity':capacity,'input_memfd_allocated_bytes':0}) as capture,patch.object(driver,'_measure_composed_phase',return_value={'phase_memfd_allocated_bytes':0,'stages':[]}) as composed,patch.object(driver,'_execute') as execute:
                result=driver.run_postgres_fit(SimpleNamespace(commit='a'*40,tree='b'*40),Path('/fixture'))
            capture.assert_called_once();execute.assert_not_called()
            self.assertEqual(result['capture_fit'],expected);self.assertTrue(result['owned_descriptors_closed'])
            self.assertEqual(result['native_execution'],'NOT_RUN');self.assertEqual(result['whole_service_memory'],'NOT_PROVEN')
            self.assertEqual(composed.call_count,1 if capacity=='FIT' else 0)

    def test_memory_measurement_runs_actual_composition_and_adoption_then_closes(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        from test_hosted_synthetic_postgres_runtime import PostgresPhaseRuntimeTests,BINDING
        import tracemalloc
        fixture=PostgresPhaseRuntimeTests();fixture.setUp()
        try:
            measurement=pg.measure_phase_fit(fixture.diagnostic,fixture.postgres)
            with patch.object(pg,'_source_identity'),patch.object(runtime,'capture_runtime',return_value=fixture.diagnostic),patch.object(pg,'capture_postgres',return_value=fixture.postgres):
                before=driver._descriptor_inventory();tracemalloc.start()
                try:report=driver._measure_composed_phase(BINDING,Path('/fixture'),measurement)
                finally:tracemalloc.stop()
            self.assertEqual(report['composition'],'PASS');self.assertEqual(report['adoption'],'PASS')
            self.assertTrue(report['owned_captures_and_adoption_closed'])
            self.assertEqual(len(report['stages']),2)
            self.assertEqual(len(driver._descriptor_inventory()),len(before)-2)
        finally:fixture.doCleanups()

    def test_measurement_adoption_refusal_closes_transferred_and_original_fd(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        from test_hosted_synthetic_postgres_runtime import PostgresPhaseRuntimeTests,BINDING
        fixture=PostgresPhaseRuntimeTests();fixture.setUp()
        try:
            measurement=pg.measure_phase_fit(fixture.diagnostic,fixture.postgres);before=driver._descriptor_inventory()
            def refused(packet):
                owned=pg.runtime._receive_runtime(packet,(runtime.POSTGRES_PHASE_POLICY,));owned.close()
                raise ns.NamespaceProofError('synthetic adopter refusal')
            with patch.object(pg,'_source_identity'),patch.object(runtime,'capture_runtime',return_value=fixture.diagnostic),patch.object(pg,'capture_postgres',return_value=fixture.postgres),patch.object(pg,'receive_postgres_phase',side_effect=refused),self.assertRaisesRegex(ns.NamespaceProofError,'synthetic adopter refusal'):
                driver._measure_composed_phase(BINDING,Path('/fixture'),measurement)
            self.assertEqual(len(driver._descriptor_inventory()),len(before)-2)
        finally:fixture.doCleanups()

    def test_shared_counter_refusals_distinguish_schema_from_socket_pressure(self):
        import hosted_synthetic_runtime_regression as driver
        values=b'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n'
        for extra,expected in ((b'unknown_safety_event 0\n','REFUSED_COUNTER_SCHEMA'),
                               (b'sock_throttled 1\n','REFUSED_SOCKET_PRESSURE'),
                               (b'sock_throttled 0\n','OBSERVED')):
            def read(path,*args):
                if path=='/proc/self/cgroup':return b'0::/owned-job\n'
                return values+extra if path.endswith('memory.events') else b'0\n'
            with self.subTest(extra=extra),patch.object(driver.ns,'_read',side_effect=read),patch.object(driver.Path,'stat',return_value=SimpleNamespace(st_dev=1,st_ino=2)):
                report=driver._shared_job_memory()
            self.assertEqual(report['status'],expected)
            self.assertIn(extra.partition(b' ')[0].decode(),report['observed_event_keys'])
            if expected!='OBSERVED':self.assertNotIn('values',report)
            else:self.assertEqual(report['values']['memory.events']['sock_throttled'],0)

    def test_capacity_refusal_never_enters_composition_or_changes_trace_state(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        from hosted_synthetic_source import HostedSourceError
        for kind in (MemoryError,HostedSourceError,ns.backend.LocalStagingAcceptanceError):
            with self.subTest(kind=kind.__name__),patch.object(os,'geteuid',return_value=1001),patch.object(os,'getegid',return_value=1001),patch.object(runtime,'read_native',return_value=(b'ID=ubuntu\nVERSION_ID="24.04"\n',0o444)),patch.object(Path,'is_file',return_value=True),patch.object(driver,'_native_input_chain',return_value={'status':'NOT_OBSERVED'}),patch.object(driver,'_shared_job_memory',return_value={'status':'OBSERVED','identity':[1,2],'values':{'memory.events':{}}}),patch.object(pg,'capture_phase_fit',side_effect=kind('excluded failure text')),patch.object(driver,'_measure_composed_phase') as composed:
                report=driver.run_postgres_fit(SimpleNamespace(commit='a'*40,tree='b'*40),Path('/fixture'))
            composed.assert_not_called();self.assertEqual(report['capture_fit'],'REFUSED_COMPONENT')
            self.assertEqual(report['failure_type'],kind.__name__);self.assertFalse(driver.tracemalloc.is_tracing())
            self.assertNotIn('excluded failure text',str(report))
            self.assertEqual(report['shared_job_zero_oom'],'NOT_PROVEN')

    def test_real_capture_fit_unavailable_target_is_not_a_passing_substitute(self):
        import hosted_synthetic_runtime_regression as driver
        import hosted_synthetic_postgres_runtime as pg
        for release,present in ((b'ID=debian\nVERSION_ID="13"\n',True),(b'ID=ubuntu\nVERSION_ID="24.04"\n',False)):
            with self.subTest(release=release,present=present),patch.object(os,'geteuid',return_value=1001),patch.object(os,'getegid',return_value=1001),patch.object(runtime,'read_native',return_value=(release,0o444)),patch.object(Path,'is_file',return_value=present),patch.object(pg,'capture_phase_fit') as capture:
                result=driver.run_postgres_fit(SimpleNamespace(commit='a'*40,tree='b'*40),Path('/fixture'))
            capture.assert_not_called();self.assertEqual(result['capture_fit'],'NOT_RUN')

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

    def test_diagnostic_postseal_constructor_failure_closes_new_descriptor(self):
        import hosted_synthetic_source as source
        allocated=[]
        def constructor(bundle,*args):allocated.append(bundle.descriptor);raise MemoryError('constructor')
        with tempfile.TemporaryDirectory() as root,patch.object(source,'SOURCE_PATHS',()), \
             patch.object(runtime,'read_native',return_value=(elf(),0o555)), \
             patch.object(runtime,'_selected_stdlib',return_value=[]), \
             patch.object(runtime,'_resolve_library',return_value=('/lib/libc.so.6',elf(),0o555)), \
             patch.object(runtime,'RuntimeBundle',side_effect=constructor),self.assertRaises(MemoryError):
            runtime.capture_runtime('a'*64,root,source.HostedSourceBinding('b'*40,'c'*40,()))
        self.assertEqual(len(allocated),1)
        with self.assertRaises(OSError):os.fstat(allocated[0])


if __name__=='__main__':unittest.main()
