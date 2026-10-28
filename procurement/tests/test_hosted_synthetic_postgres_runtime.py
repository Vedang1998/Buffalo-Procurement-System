"""Causal PG dependency, policy-separation and real sealed-memfd tests.

Synthetic package bytes are not trusted-host native execution evidence.
"""
from dataclasses import replace
import fcntl
import hashlib
import os
import copy
from types import SimpleNamespace
import stat
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import hosted_synthetic_namespace as ns
import hosted_synthetic_postgres_runtime as pg
import hosted_synthetic_runtime as runtime
import hosted_synthetic_source as source
from test_hosted_synthetic_runtime import elf, fixture_bundle

BINDING=source.HostedSourceBinding('b'*40,'c'*40,(('uv.lock','d'*64),))


def repack(value,entries,**changes):
    """Owned synthetic replacement, never a trusted native input."""
    fields={name:getattr(value,name) for name in ('generation','executable','source_files','native_files',
        'source_commit','source_tree','policy','application','postgres')}
    fields.update(changes)
    raw=runtime._identity_bytes(fields['generation'],fields['executable'],fields['source_files'],fields['native_files'],
        fields['source_commit'],fields['source_tree'],policy=fields['policy'],application=fields['application'],postgres=fields['postgres'])
    regular=[replace(e,content=raw,provenance='audit-source:'+hashlib.sha256(raw).hexdigest())
             if e.path==runtime._IDENTITY_PATH else e for e in entries if e.kind=='F']
    bundle=ns.backend._seal_browser_runtime_bundle(ns.backend._complete_browser_runtime_bundle_parents(tuple(regular)))
    result=runtime.RuntimeBundle(bundle,**fields);result._owner=result;result._token=runtime._TOKEN
    return result


def package():
    values={pg.SHARE+name:(b'-- fixed native data\n',0o444) for name in pg._DATA}
    values[pg.SHARE+'postgres.bki']=(b"# PostgreSQL 16\ninsert ('$libdir/utf8_and_win')\n",0o444)
    values[pg.SHARE+'snowball_create.sql']=(b"AS '$libdir/dict_snowball'; StopWords=english);\n",0o444)
    values[pg.SHARE+'timezonesets/Default']=(b'UTC 0\nEDT -14400 D\nART America/Argentina/Buenos_Aires\n',0o444)
    values[pg.SHARE+'tsearch_data/english.stop']=(b'words\n',0o444)
    for name,fields in pg._CONTROLS.items():
        values[pg.SHARE+'extension/'+name+'.control']=('\n'.join(k+' = '+v for k,v in fields.items()).encode(),0o444)
        version=fields['default_version'][1:-1]
        values[pg.SHARE+'extension/'+name+'--'+version+'.sql']=(b"AS 'MODULE_PATHNAME';",0o444)
    for zone in ('UTC','America/New_York','America/Argentina/Buenos_Aires'):
        values[pg.ZONE+zone]=(b'TZif\x00synthetic',0o444)
    for name in pg._LOCALE:values['/usr/lib/locale/C.utf8/'+name]=(b'synthetic locale',0o444)
    for path in pg.EXECUTABLES:values[path]=(elf(),0o555)
    for name in ('utf8_and_win','dict_snowball','pgcrypto','plpgsql'):
        values[pg.LIB+name+'.so']=(elf(),0o444)
    values['/lib64/ld-linux-x86-64.so.2']=(elf(),0o555)
    values['/lib/x86_64-linux-gnu/libc.so.6']=(elf(),0o555)
    values['/lib/x86_64-linux-gnu/libnss_files.so.2']=(elf(),0o444)
    return values


class PostgresRuntimeTests(unittest.TestCase):
    def capture(self,values=None):
        values=package() if values is None else values
        def resolve(name,roots):
            path='/lib/x86_64-linux-gnu/'+name
            return path,*values[path]
        with patch.object(pg,'_source_identity'),patch.object(runtime,'read_native',side_effect=values.__getitem__), \
                patch.object(runtime,'_library_roots',return_value=()),patch.object(runtime,'_resolve_library',side_effect=resolve):
            return pg.capture_postgres('a'*64,Path('/fixture'),BINDING)

    def test_complete_module_references_are_deduplicated_and_sorted(self):
        self.assertEqual(pg.module_references(b"'$libdir/z' '$libdir/a' '$libdir/z'"),('a','z'))

    def test_partial_paths_expansions_and_unquoted_modules_refuse(self):
        for raw in (b"'$libdir/a.so'",b"'$libdir/../a'",b"'$libdir/a/b'",b'$libdir/a',b"'$libdir/a' '$libdir/b/escape'"):
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):pg.module_references(raw)

    def test_bounded_ascii_module_data_required(self):
        for raw in (b'',b'\xff',b'\0',b'a'*(2*1024**2+1)):
            with self.subTest(size=len(raw)),self.assertRaises(ns.NamespaceProofError):pg.module_references(raw)

    def test_stopword_assignment_requires_full_terminal(self):
        self.assertEqual(pg.stopword_references(b'StopWords=english); StopWords = turkish, x'),('english','turkish'))
        for raw in (b'StopWords=english/escape)',b'StopWords=../x)',b'StopWords=english',b'StopWords=',b'StopWords=\'english\')'):
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):pg.stopword_references(raw)

    def test_default_timezone_includes_fixed_workflow_and_referenced_zones(self):
        self.assertEqual(pg.timezone_references(b'UTC 0\nAKDT -28800 D\nART America/Argentina/Buenos_Aires # comment\n'),
            ('America/Argentina/Buenos_Aires','America/New_York','UTC'))

    def test_timezone_directives_duplicate_or_unknown_fields_refuse(self):
        for raw in (b'@INCLUDE Default',b'@OVERRIDE',b'UTC 0\nUTC 1',b'UTC 0 X',b'A Area/Zone D',b'A 90000',b'A +10',b'A 01'):
            with self.subTest(raw=raw),self.assertRaises(ns.NamespaceProofError):pg.timezone_references(raw)

    def test_timezone_traversal_and_absolute_paths_refuse(self):
        for name in ('../UTC','/etc/passwd','Area//Zone','Area/./Zone','Area/../Zone','Area\\Zone'):
            with self.subTest(name=name),self.assertRaises(ns.NamespaceProofError):pg.timezone_references(('A '+name).encode())

    def test_timezone_size_and_line_bounds_refuse(self):
        for raw in (b'',b'UTC 0 #'+b'x'*513,b'x'*(128*1024+1),b'\n'*4097):
            with self.subTest(size=len(raw)),self.assertRaises(ns.NamespaceProofError):pg.timezone_references(raw)

    def test_exact_extension_controls_and_versions(self):
        for name,expected in (('pgcrypto','1.3'),('plpgsql','1.0')):
            self.assertEqual(pg.extension_control(name,package()[pg.SHARE+'extension/'+name+'.control'][0]),expected)

    def test_extension_directory_requires_duplicates_and_version_drift_refuse(self):
        raw=package()[pg.SHARE+'extension/pgcrypto.control'][0]
        for changed in (raw+b'\ndirectory = \'/host\'',raw+b'\nrequires = \'other\'',raw+b'\ntrusted = true',raw.replace(b"'1.3'",b"'1.4'"),raw.replace(b'$libdir/pgcrypto',b'/host/module')):
            with self.subTest(changed=changed),self.assertRaises(ns.NamespaceProofError):pg.extension_control('pgcrypto',changed)

    def test_capture_seals_all_native_and_generated_data_without_execution_authority(self):
        value=self.capture()
        try:
            entries={e.path:e for e in runtime._entries(value)}
            self.assertEqual(value.policy,runtime.POSTGRES_POLICY)
            self.assertIsNone(value.application)
            self.assertEqual(value.postgres['execution'],'NOT_PROVEN')
            self.assertEqual(value.postgres['credential_release'],'WITHHELD')
            self.assertFalse(value.bundle.execution_authority)
            self.assertEqual(value.executable,pg.BIN+'postgres')
            for p in (*package(),*pg._FIXED):self.assertIn(p,entries)
            self.assertEqual(entries[pg.LIB+'pgcrypto.so'].mode,0o444)
            self.assertEqual(fcntl.fcntl(value.bundle.descriptor,fcntl.F_GET_SEALS),ns.backend._BROWSER_RUNTIME_BUNDLE_SEALS)
            with self.assertRaises(OSError):os.pwrite(value.bundle.descriptor,b'x',0)
            self.assertNotIn('/dev/null',entries)
            for p in pg._FIXED:
                self.assertTrue(entries[p].provenance.startswith('audit-source:'))
                self.assertEqual(value.postgres['generated_configuration'][p],hashlib.sha256(entries[p].content).hexdigest())
        finally:value.close()

    def test_sample_configuration_libdir_search_path_is_not_a_module_reference(self):
        values=package();values[pg.SHARE+'postgresql.conf.sample']=(b"#dynamic_library_path = '$libdir'",0o444)
        value=self.capture(values)
        try:self.assertEqual(value.postgres['modules'],['dict_snowball','pgcrypto','plpgsql','utf8_and_win'])
        finally:value.close()

    def test_native_locale_category_is_required_without_host_archive_fallback(self):
        values=package();del values['/usr/lib/locale/C.utf8/LC_CTYPE']
        with self.assertRaises(KeyError):self.capture(values)

    def test_diagnostic_policy_cannot_carry_postgres_identity(self):
        value=fixture_bundle()
        try:
            value.postgres={}
            with self.assertRaises(ns.NamespaceProofError):value.verify()
        finally:value.postgres=None;value.close()

    def test_no_path_fallback_for_missing_native_executable(self):
        values=package();del values[pg.BIN+'initdb']
        with self.assertRaises(KeyError):self.capture(values)

    def test_initdb_system_collation_locale_program_cannot_be_silently_absent(self):
        values=package();del values['/usr/bin/locale']
        with self.assertRaises(KeyError):self.capture(values)

    def test_wrong_bki_major_refuses(self):
        values=package();values[pg.SHARE+'postgres.bki']=(b"# PostgreSQL 17\n'$libdir/x'",0o444)
        with self.assertRaises(ns.NamespaceProofError):self.capture(values)

    def test_missing_module_stopword_timezone_or_extension_script_refuses(self):
        for path in (pg.LIB+'dict_snowball.so',pg.SHARE+'tsearch_data/english.stop',pg.ZONE+'America/Argentina/Buenos_Aires',pg.SHARE+'extension/pgcrypto--1.3.sql'):
            values=package();del values[path]
            with self.subTest(path=path),self.assertRaises(KeyError):self.capture(values)

    def test_non_elf_or_nonexecutable_native_program_refuses(self):
        for changed in ((b'#!/bin/sh\nexit 0',0o555),(elf(),0o444)):
            values=package();values[pg.BIN+'pg_dump']=changed
            with self.subTest(mode=changed[1]),self.assertRaises(ns.NamespaceProofError):self.capture(values)

    def test_data_file_cannot_smuggle_executable_image(self):
        values=package();values[pg.SHARE+'sql_features.txt']=(elf(),0o444)
        with self.assertRaises(ns.NamespaceProofError):self.capture(values)

    def test_diagnostic_receiver_rejects_postgres_before_touching_descriptor(self):
        value=self.capture()
        try:
            with patch.object(os,'set_inheritable') as change,self.assertRaises(ns.NamespaceProofError):runtime.receive_runtime(value.packet())
            change.assert_not_called();value.verify()
        finally:value.close()

    def test_postgres_receiver_rejects_diagnostic_policy(self):
        value=fixture_bundle()
        try:
            with self.assertRaises(ns.NamespaceProofError):pg.receive_postgres(value.packet())
            value.verify()
        finally:value.close()

    def test_explicit_postgres_resume_reconstructs_sealed_metadata(self):
        value=self.capture();received=None;fd=None
        try:
            packet=value.packet();fd=os.dup(value.bundle.descriptor);packet['bundle']['descriptor']=fd
            received=pg.receive_postgres(packet);fd=None
            self.assertEqual(received.postgres,value.postgres)
            self.assertEqual(received.native_files,value.native_files)
            self.assertEqual(received.bundle.sha256,value.bundle.sha256)
        finally:
            if fd is not None:os.close(fd)
            if received is not None:received.close()
            value.close()

    def test_failed_receive_closes_consumed_matching_descriptor(self):
        value=self.capture();fd=None
        try:
            packet=value.packet();fd=os.dup(value.bundle.descriptor);packet['bundle']['descriptor']=fd
            packet['source_tree']='e'*40
            with self.assertRaises(ns.NamespaceProofError):pg.receive_postgres(packet)
            with self.assertRaises(OSError):os.fstat(fd)
            fd=None;value.verify()
        finally:
            if fd is not None:os.close(fd)
            value.close()

    def test_failed_receive_keeps_foreign_descriptor_unchanged(self):
        value=self.capture();fd=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
        try:
            os.set_inheritable(fd,True);packet=value.packet();packet['bundle']['descriptor']=fd
            with self.assertRaises(ns.NamespaceProofError):pg.receive_postgres(packet)
            self.assertTrue(os.get_inheritable(fd))
            self.assertEqual(os.readlink(f'/proc/self/fd/{fd}'),'/dev/null')
        finally:os.close(fd);value.close()

    def test_native_metadata_mutation_refuses_sealed_identity(self):
        value=self.capture();original=value.postgres
        try:
            value.postgres={**original,'execution':'PASS'}
            with self.assertRaises(ns.NamespaceProofError):runtime._entries(value)
        finally:value.postgres=original;value.close()

    def test_native_policy_cannot_enter_diagnostic_or_claim_python_consumer_proof(self):
        value=self.capture()
        try:
            for operation,args in ((runtime.enter_runtime,(value,)),(runtime.verify_consumer,(value,{})),(runtime.verify_report_binding,(value,{}))):
                with self.subTest(operation=operation.__name__),self.assertRaises(ns.NamespaceProofError):operation(*args)
        finally:value.close()

    def test_complete_source_binding_repeated_before_and_after_native_reads(self):
        observed=[];values=package()
        def read(path):observed.append(('native',path));return values[path]
        def bind(*args):observed.append(('source',))
        def resolve(name,roots):return '/lib/x86_64-linux-gnu/'+name,*values['/lib/x86_64-linux-gnu/'+name]
        with patch.object(pg,'_source_identity',side_effect=bind),patch.object(runtime,'read_native',side_effect=read),patch.object(runtime,'_resolve_library',side_effect=resolve):
            value=pg.capture_postgres('a'*64,Path('/fixture'),BINDING)
        try:self.assertEqual(observed[0],('source',));self.assertEqual(observed[-1],('source',));self.assertEqual(observed.count(('source',)),2)
        finally:value.close()

    def test_changed_source_refuses_before_native_capture(self):
        with patch.object(source,'bind_hosted_source',return_value=replace(BINDING,tree='e'*40)),patch.object(runtime,'read_native') as read,self.assertRaises(ns.NamespaceProofError):
            pg.capture_postgres('a'*64,Path('/fixture'),BINDING)
        read.assert_not_called()

    def test_native_root_trust_refusal_is_not_substituted(self):
        with patch.object(pg,'_source_identity'),patch.object(runtime,'read_native',side_effect=ns.NamespaceProofError('untrusted')) as read,self.assertRaises(ns.NamespaceProofError):
            pg.capture_postgres('a'*64,Path('/fixture'),BINDING)
        self.assertEqual(read.call_count,1)

    def test_capture_keeps_existing_entry_and_byte_limits(self):
        for name in ('MAX_BYTES','MAX_ENTRIES'):
            with self.subTest(bound=name),patch.object(runtime,name,1),self.assertRaises(ns.NamespaceProofError):self.capture()

    def test_failed_final_validation_closes_unreturned_memfd(self):
        before=set(os.listdir('/proc/self/fd'))
        with patch.object(runtime,'_entries',side_effect=ns.NamespaceProofError('synthetic validation')),self.assertRaises(ns.NamespaceProofError):self.capture()
        self.assertEqual(set(os.listdir('/proc/self/fd')),before)

    def test_constructor_failure_closes_already_sealed_bundle(self):
        before=set(os.listdir('/proc/self/fd'))
        with patch.object(runtime,'RuntimeBundle',side_effect=MemoryError('synthetic allocation')),self.assertRaises(MemoryError):self.capture()
        self.assertEqual(set(os.listdir('/proc/self/fd')),before)

    def test_pending_cancellation_with_close_failure_is_cleanup_refusal(self):
        result=Mock();result.close.side_effect=OSError('synthetic close')
        original=KeyboardInterrupt()
        with patch.object(pg,'_capture',return_value=result),patch.object(source.signal,'pthread_sigmask',side_effect=[set(),original]),self.assertRaises(ns.NamespaceCleanupError) as caught:
            pg.capture_postgres('a'*64,Path('/fixture'),BINDING)
        self.assertIs(caught.exception.__cause__,original)

    def test_native_dependencies_are_loaded_lazily_with_budget_checks(self):
        values=package();reads=[]
        def read(path):reads.append(path);return values[path]
        def resolve(name,roots):
            path='/lib/x86_64-linux-gnu/'+name;reads.append(path);return path,*values[path]
        # The first native library exhausts the remaining regular-byte budget;
        # none of the still-queued binaries/modules may be read in advance.
        total=sum(len(v[0]) for p,v in values.items() if not v[0].startswith(b'\x7fELF'))+sum(map(len,pg._FIXED.values()))
        with patch.object(pg,'_source_identity'),patch.object(runtime,'read_native',side_effect=read),patch.object(runtime,'_resolve_library',side_effect=resolve),patch.object(runtime,'MAX_BYTES',total+100),self.assertRaises(ns.NamespaceProofError):
            pg.capture_postgres('a'*64,Path('/fixture'),BINDING)
        self.assertFalse(set(reads).intersection(pg.EXECUTABLES))
        self.assertEqual([p for p in reads if p.endswith('.so.2')],['/lib/x86_64-linux-gnu/libnss_files.so.2'])

    def test_extension_references_cannot_exceed_total_module_bound(self):
        values=package();values[pg.SHARE+'extension/pgcrypto--1.3.sql']=(b"'MODULE_PATHNAME' "+b' '.join(
            ("'$libdir/extra%d'"%n).encode() for n in range(64)),0o444)
        with self.assertRaises(ns.NamespaceProofError):self.capture(values)

    def test_repeated_native_dependency_paths_are_not_read_again(self):
        values=package();reads=[]
        def read(path):reads.append(path);return values[path]
        def resolve(name,roots):
            path='/lib/x86_64-linux-gnu/'+name;reads.append(path);return path,*values[path]
        with patch.object(pg,'_source_identity'),patch.object(runtime,'read_native',side_effect=read),patch.object(runtime,'_resolve_library',side_effect=resolve):
            value=pg.capture_postgres('a'*64,Path('/fixture'),BINDING)
        try:self.assertEqual(len(reads),len(set(reads)))
        finally:value.close()

    def test_pending_cancellation_closes_untransferred_result(self):
        result=Mock()
        with patch.object(pg,'_capture',return_value=result),patch.object(source.signal,'pthread_sigmask',side_effect=[set(),KeyboardInterrupt()]),self.assertRaises(KeyboardInterrupt):
            pg.capture_postgres('a'*64,Path('/fixture'),BINDING)
        result.close.assert_called_once()


class PostgresPhaseRuntimeTests(unittest.TestCase):
    def setUp(self):
        value=fixture_bundle()
        try:
            entries=[replace(e,provenance='audit-source:'+hashlib.sha256(e.content).hexdigest())
                     if e.path==runtime.SENTINEL else e for e in runtime._entries(value)]
            self.diagnostic=repack(value,entries)
        finally:value.close()
        self.addCleanup(self.close_owned,self.diagnostic)
        self.postgres=PostgresRuntimeTests.capture(self)
        self.addCleanup(self.close_owned,self.postgres)

    @staticmethod
    def close_owned(value):
        if getattr(value,'_token',None) is runtime._TOKEN:value.close()

    def compose(self):
        value=pg._compose_phase(self.diagnostic,self.postgres)
        self.addCleanup(self.close_owned,value)
        return value

    def test_sealed_union_keeps_both_exact_inputs_and_one_identity(self):
        value=self.compose();entries=pg._phase_inventory(value)
        self.assertEqual(value.policy,runtime.POSTGRES_PHASE_POLICY)
        self.assertEqual(value.executable,self.diagnostic.executable)
        self.assertEqual(value.postgres['diagnostic_bundle_sha256'],self.diagnostic.bundle.sha256)
        self.assertEqual(value.postgres['postgres_bundle_sha256'],self.postgres.bundle.sha256)
        self.assertEqual(value.postgres['execution'],'NOT_PROVEN')
        self.assertEqual(value.postgres['credential_release'],'WITHHELD')
        self.assertEqual({p:e.content for p,e in entries.items() if p in self.postgres.native_files},
            {e.path:e.content for e in runtime._entries(self.postgres) if e.path in self.postgres.native_files})
        self.assertEqual(sum(e.path==runtime._IDENTITY_PATH for e in runtime._entries(value)),1)
        self.assertEqual(sum(e.path=='/proc' for e in runtime._entries(value)),1)
        self.assertFalse(value.bundle.execution_authority)
        with self.assertRaises(OSError):os.pwrite(value.bundle.descriptor,b'x',0)

    def test_shared_native_library_requires_complete_entry_equality(self):
        entry=next(e for e in runtime._entries(self.postgres) if e.path=='/lib64/ld-linux-x86-64.so.2')
        native=dict(self.diagnostic.native_files,**{entry.path:hashlib.sha256(entry.content).hexdigest()})
        combined=repack(self.diagnostic,[*runtime._entries(self.diagnostic),entry],native_files=native)
        try:
            result=pg._compose_phase(combined,self.postgres)
            result.close()
        finally:combined.close()

    def test_conflicting_bytes_mode_or_provenance_refuse_without_seal(self):
        for changes in ({'content':b'foreign'},{'mode':0o444},{'provenance':'native-runtime:'+'f'*64}):
            entries=[replace(e,**changes) if e.path==runtime.SENTINEL else e for e in runtime._entries(self.diagnostic)]
            with self.subTest(changes=changes),patch.object(runtime,'_entries',side_effect=[entries,runtime._entries(self.postgres)]), \
                 patch.object(ns.backend,'_seal_browser_runtime_bundle') as seal,self.assertRaises(ns.NamespaceProofError):
                pg._compose_phase(self.diagnostic,self.postgres)
            seal.assert_not_called()

    def test_source_native_category_collision_refuses(self):
        entry=next(e for e in runtime._entries(self.postgres) if e.path=='/lib64/ld-linux-x86-64.so.2')
        changed=repack(self.diagnostic,[*runtime._entries(self.diagnostic),entry],
            source_files={entry.path:hashlib.sha256(entry.content).hexdigest()})
        try:
            with self.assertRaises(ns.NamespaceProofError):pg._compose_phase(changed,self.postgres)
        finally:changed.close()

    def test_generation_commit_and_tree_mismatch_refuse_before_merge(self):
        for name,changed in (('generation','d'*64),('source_commit','d'*40),('source_tree','d'*40)):
            original=getattr(self.postgres,name);setattr(self.postgres,name,changed)
            try:
                with patch.object(runtime,'_entries') as entries,self.assertRaises(ns.NamespaceProofError):
                    pg._compose_phase(self.diagnostic,self.postgres)
                entries.assert_not_called()
            finally:setattr(self.postgres,name,original)

    def test_wrong_capture_policy_refuses(self):
        with self.assertRaises(ns.NamespaceProofError):pg._compose_phase(self.postgres,self.postgres)
        with self.assertRaises(ns.NamespaceProofError):pg._compose_phase(self.diagnostic,self.diagnostic)

    def test_combined_regular_byte_limit_is_not_sum_of_independent_limits(self):
        limit=max(self.diagnostic.bundle.regular_bytes,self.postgres.bundle.regular_bytes)
        with patch.object(runtime,'MAX_BYTES',limit),self.assertRaises(ns.NamespaceProofError):self.compose()

    def test_regenerated_parent_count_is_bounded(self):
        limit=max(self.diagnostic.bundle.entries,self.postgres.bundle.entries)
        with patch.object(runtime,'MAX_ENTRIES',limit),self.assertRaises(ns.NamespaceProofError):self.compose()

    def test_phase_resume_preserves_identity_and_closes_owned_fd(self):
        value=self.compose();packet=value.packet();fd=os.dup(value.bundle.descriptor);packet['bundle']['descriptor']=fd
        received=pg.receive_postgres_phase(packet)
        self.assertEqual(received.postgres,value.postgres)
        self.assertEqual(received.native_files,value.native_files)
        received.close()
        with self.assertRaises(OSError):os.fstat(fd)

    def test_phase_policy_is_rejected_by_diagnostic_and_pg_only_entrypoints(self):
        value=self.compose()
        for operation in (lambda:runtime.receive_runtime(value.packet()),lambda:pg.receive_postgres(value.packet()),
                          lambda:runtime.enter_runtime(value),lambda:runtime.verify_consumer(value,{}),
                          lambda:runtime.verify_report_binding(value,{})):
            with self.assertRaises(ns.NamespaceProofError):operation()
        value.verify()

    def test_pg_only_bundle_cannot_claim_phase_runtime(self):
        with self.assertRaises(ns.NamespaceProofError):pg.receive_postgres_phase(self.postgres.packet())
        with patch.object(runtime,'_enter_projection') as enter,self.assertRaises(ns.NamespaceProofError):
            pg.enter_postgres_phase(self.postgres)
        enter.assert_not_called()

    def test_phase_source_inventory_must_match_pg_capture_source_binding(self):
        changed=copy.deepcopy(self.postgres.postgres)
        changed['source_files']['procurement/tools/new.py']='a'*64
        value=repack(self.postgres,runtime._entries(self.postgres),postgres=changed)
        try:
            with self.assertRaises(ns.NamespaceProofError):pg._compose_phase(self.diagnostic,value)
        finally:value.close()

    def test_phase_projection_requires_actual_mapped_identity_before_effects(self):
        value=self.compose()
        with patch.object(ns,'_require_mapped_identity',side_effect=ns.NamespaceProofError('identity')), \
             patch.object(runtime,'_enter_projection') as enter,self.assertRaises(ns.NamespaceProofError):
            pg.enter_postgres_phase(value)
        enter.assert_not_called()

    def test_phase_projection_refuses_existing_children_before_shared_mount_change(self):
        value=self.compose()
        with patch.object(ns,'_require_mapped_identity'),patch('hosted_synthetic_cgroup._signal_boundary'), \
             patch.object(ns,'_read',return_value=b'123 '),patch.object(runtime,'_enter_projection') as enter, \
             self.assertRaises(ns.NamespaceProofError):pg.enter_postgres_phase(value)
        enter.assert_not_called()

    def test_phase_report_cannot_be_promoted_to_native_execution_or_diagnostic_scope(self):
        value=self.compose()
        proof=dict(policy=value.policy,generation=value.generation,bundle_sha256=value.bundle.sha256,
            manifest_sha256=value.bundle.manifest_sha256,source_commit=value.source_commit,source_tree=value.source_tree,
            root_identity=[1,2],root_mount_id=3,regular_bytes=value.bundle.regular_bytes,old_root_detached=True,
            consumer_exec_verified=True,mapped_elf_files=2,execution_authority=False,
            scope='HOSTED_POSTGRES_SUPERVISOR_COMPONENT',application_browser_closure='NOT_PROVEN',
            native_postgres_execution='NOT_PROVEN',mapped_postgres_locale_files=1)
        pg.verify_postgres_phase_report(value,proof)
        for changes in ({'native_postgres_execution':'PASS'},{'scope':'HOSTED_DIAGNOSTIC_RUNTIME'},
                        {'consumer_exec_verified':False},{'source_tree':'e'*40},{'root_mount_id':True},
                        {'mapped_postgres_locale_files':True},{'mapped_postgres_locale_files':13}):
            with self.subTest(changes=changes),self.assertRaises(ns.NamespaceProofError):
                pg.verify_postgres_phase_report(value,dict(proof,**changes))

    def test_captured_libc_locale_maps_are_data_not_elf(self):
        raw=b'synthetic captured locale';digest=hashlib.sha256(raw).hexdigest()
        for name in pg._LOCALE:
            self.assertEqual(runtime._mapped_file_kind(runtime.POSTGRES_PHASE_POLICY,
                '/usr/lib/locale/C.utf8/'+name,raw,digest,b'r--p',0o444),'LOCALE')

    def test_locale_exception_never_applies_to_other_policies_paths_or_permissions(self):
        raw=b'locale';digest=hashlib.sha256(raw).hexdigest();path='/usr/lib/locale/C.utf8/LC_CTYPE'
        for policy,p,perms,mode,d in ((runtime.POLICY,path,b'r--p',0o444,digest),
                (runtime.APPLICATION_POLICY,path,b'r--p',0o444,digest),
                (runtime.POSTGRES_PHASE_POLICY,'/etc/passwd',b'r--p',0o444,digest),
                (runtime.POSTGRES_PHASE_POLICY,path,b'r-xp',0o444,digest),
                (runtime.POSTGRES_PHASE_POLICY,path,b'rw-p',0o444,digest),
                (runtime.POSTGRES_PHASE_POLICY,path,b'r--s',0o444,digest),
                (runtime.POSTGRES_PHASE_POLICY,path,b'r--p',0o644,digest),
                (runtime.POSTGRES_PHASE_POLICY,path,b'r--p',0o444,'0'*64)):
            with self.subTest(policy=policy,path=p,perms=perms),self.assertRaises(ns.NamespaceProofError):
                runtime._mapped_file_kind(policy,p,raw,d,perms,mode)

    def test_phase_locale_metadata_cannot_expand_the_fixed_data_allowlist(self):
        value=self.compose();metadata=copy.deepcopy(value.postgres)
        metadata['native_capture']['locale_files'].append('../../etc/passwd')
        changed=repack(value,runtime._entries(value),postgres=metadata)
        try:
            with self.assertRaises(ns.NamespaceProofError):pg._phase_inventory(changed)
        finally:changed.close()

    def mapped_consumer(self,*,second_locale_permission=b'r--p'):
        value=self.compose();entries=pg._phase_inventory(value)
        path='/usr/lib/locale/C.utf8/LC_CTYPE'
        maps=(b'1000-2000 r-xp 0000 00:01 3 /fixture/python\n'
              b'2000-3000 r--p 0000 00:01 4 '+path.encode()+b'\n'
              b'3000-4000 '+second_locale_permission+b' 0000 00:01 4 '+path.encode()+b'\n')
        observation=dict(policy=value.policy,generation=value.generation,bundle_sha256=value.bundle.sha256,
            manifest_sha256=value.bundle.manifest_sha256,root_identity=[1,2],root_mount_id=88,
            regular_bytes=value.bundle.regular_bytes,old_root_detached=True,consumer_exec_verified=False,execution_authority=False)
        def info(name,**kwargs):
            return SimpleNamespace(st_dev=1,st_ino=2 if name in ('/','.') else 4 if name==path else 3,
                st_mode=stat.S_IFREG|(0o444 if name==path else 0o555))
        with patch.object(runtime,'_entries'),patch.object(runtime.Path,'resolve',return_value=Path(value.executable)), \
             patch.object(runtime.os,'stat',side_effect=info),patch.object(ns,'_acquire',return_value=Mock(number=777)), \
             patch.object(ns,'_mount_id',return_value=88), \
             patch.object(runtime.os,'fstatvfs',return_value=SimpleNamespace(f_flag=os.ST_RDONLY|os.ST_NOSUID|os.ST_NODEV)), \
             patch.object(ns,'_read',side_effect=lambda p,*_:maps if p=='/proc/self/maps' else entries[p].content):
            return runtime._verify_consumer(value,observation,(runtime.POSTGRES_PHASE_POLICY,))

    def test_consumer_separates_duplicate_locale_data_from_elf_count(self):
        proof=self.mapped_consumer()
        self.assertEqual(proof['mapped_elf_files'],1)
        self.assertEqual(proof['mapped_postgres_locale_files'],1)
        self.assertEqual(proof['native_postgres_execution'],'NOT_PROVEN')

    def test_later_mapping_of_same_locale_cannot_add_execute_permission(self):
        with self.assertRaises(ns.NamespaceProofError):self.mapped_consumer(second_locale_permission=b'r-xp')

    def test_phase_consumer_uses_same_actual_map_checks_with_separate_scope(self):
        value=self.compose()
        with patch.object(runtime,'_verify_consumer',return_value={'proof':'component'}) as verify:
            self.assertEqual(pg.verify_postgres_phase_consumer(value,{'root':'input'}),{'proof':'component'})
        verify.assert_called_once_with(value,{'root':'input'},(runtime.POSTGRES_PHASE_POLICY,))
        self.assertEqual(runtime._consumer_scope(value.policy),'HOSTED_POSTGRES_SUPERVISOR_COMPONENT')

    def test_capture_rebinds_source_and_closes_inputs_before_return(self):
        with patch.object(pg,'_source_identity') as source_read, \
             patch.object(runtime,'capture_runtime',return_value=self.diagnostic), \
             patch.object(pg,'capture_postgres',return_value=self.postgres):
            value=pg.capture_postgres_phase('a'*64,Path('/fixture'),BINDING)
        self.addCleanup(self.close_owned,value)
        self.assertEqual(source_read.call_count,2)
        self.assertIsNone(self.diagnostic._token);self.assertIsNone(self.postgres._token)
        value.verify()

    def test_pre_account_measurement_matches_actual_composed_bytes_and_parents(self):
        report=pg.measure_phase_fit(self.diagnostic,self.postgres);combined=self.compose()
        self.assertEqual(report['composed_regular_bytes'],combined.bundle.regular_bytes)
        self.assertEqual(report['composed_entries'],combined.bundle.entries)
        self.assertEqual(report['capacity'],'FIT')
        self.assertEqual(report['service_memory_fit'],'NOT_PROVEN')
        self.assertFalse(report['execution_authority'])

    def test_pre_account_measurement_refuses_capacity_without_sealing_or_mutating_inputs(self):
        normal=pg.measure_phase_fit(self.diagnostic,self.postgres)
        # Inputs remain individually valid, while their complete union exceeds
        # this synthetic limit. No accepted production budget is changed.
        limit=max(self.diagnostic.bundle.regular_bytes,self.postgres.bundle.regular_bytes)
        self.assertLess(limit,normal['composed_regular_bytes'])
        with patch.object(runtime,'MAX_BYTES',limit),patch.object(ns.backend,'_seal_browser_runtime_bundle') as seal:
            report=pg.measure_phase_fit(self.diagnostic,self.postgres)
        seal.assert_not_called();self.assertEqual(report['capacity'],'REFUSED')
        self.diagnostic.verify();self.postgres.verify()

    def test_capture_fit_closes_both_inputs_and_rebinds_source(self):
        with patch.object(pg,'_source_identity') as source_read,patch.object(runtime,'capture_runtime',return_value=self.diagnostic),patch.object(pg,'capture_postgres',return_value=self.postgres):
            report=pg.capture_phase_fit('a'*64,Path('/fixture'),BINDING)
        self.assertEqual(source_read.call_count,2);self.assertEqual(report['capacity'],'FIT')
        self.assertIsNone(self.diagnostic._token);self.assertIsNone(self.postgres._token)

    def test_fit_capture_failure_or_source_drift_still_closes_owned_inputs(self):
        with patch.object(pg,'_source_identity',side_effect=[None,source.HostedSourceError('changed')]),patch.object(runtime,'capture_runtime',return_value=self.diagnostic),patch.object(pg,'capture_postgres',return_value=self.postgres),self.assertRaises(source.HostedSourceError):
            pg.capture_phase_fit('a'*64,Path('/fixture'),BINDING)
        self.assertIsNone(self.diagnostic._token);self.assertIsNone(self.postgres._token)

    def test_fit_cleanup_failure_prevents_capacity_report(self):
        close=self.postgres.close
        def failure():close();raise OSError('synthetic close failure')
        with patch.object(pg,'_source_identity'),patch.object(runtime,'capture_runtime',return_value=self.diagnostic),patch.object(pg,'capture_postgres',return_value=self.postgres),patch.object(self.postgres,'close',side_effect=failure),self.assertRaises(ns.NamespaceCleanupError):
            pg.capture_phase_fit('a'*64,Path('/fixture'),BINDING)
        self.assertIsNone(self.diagnostic._token);self.assertIsNone(self.postgres._token)

    def test_capacity_refusal_preserves_measurement_without_execution_authority(self):
        report={'capacity':'REFUSED','composed_regular_bytes':105340350,'maximum_regular_bytes':83886080,'execution_authority':False}
        with patch.object(pg,'capture_phase_fit',return_value=report),self.assertRaises(pg.PhaseCapacityRefusal) as caught:
            pg.require_phase_fit('/fixture',BINDING)
        self.assertIs(caught.exception.evidence,report)

    def test_native_capture_failure_closes_diagnostic_input(self):
        failure=OSError('native')
        with patch.object(pg,'_source_identity'),patch.object(runtime,'capture_runtime',return_value=self.diagnostic), \
             patch.object(pg,'capture_postgres',side_effect=failure),self.assertRaises(OSError) as caught:
            pg.capture_postgres_phase('a'*64,Path('/fixture'),BINDING)
        self.assertIs(caught.exception,failure);self.assertIsNone(self.diagnostic._token)

    def test_final_source_drift_closes_both_inputs_and_unreturned_output(self):
        result=Mock();failure=source.HostedSourceError('changed')
        with patch.object(pg,'_source_identity',side_effect=[None,failure]), \
             patch.object(runtime,'capture_runtime',return_value=self.diagnostic), \
             patch.object(pg,'capture_postgres',return_value=self.postgres), \
             patch.object(pg,'_compose_phase',return_value=result),self.assertRaises(source.HostedSourceError) as caught:
            pg.capture_postgres_phase('a'*64,Path('/fixture'),BINDING)
        self.assertIs(caught.exception,failure);result.close.assert_called_once()
        self.assertIsNone(self.diagnostic._token);self.assertIsNone(self.postgres._token)

    def test_input_close_failure_still_closes_other_input_and_result(self):
        result=Mock();close=self.postgres.close;failure=OSError('close')
        def closed_then_failed():close();raise failure
        with patch.object(pg,'_source_identity'),patch.object(runtime,'capture_runtime',return_value=self.diagnostic), \
             patch.object(pg,'capture_postgres',return_value=self.postgres),patch.object(pg,'_compose_phase',return_value=result), \
             patch.object(self.postgres,'close',side_effect=closed_then_failed),self.assertRaises(ns.NamespaceCleanupError) as caught:
            pg.capture_postgres_phase('a'*64,Path('/fixture'),BINDING)
        self.assertIs(caught.exception.__cause__,failure);result.close.assert_called_once()
        self.assertIsNone(self.diagnostic._token);self.assertIsNone(self.postgres._token)

    def test_postseal_constructor_failure_closes_only_new_descriptor(self):
        allocated=[]
        def constructor(bundle,*args):allocated.append(bundle.descriptor);raise MemoryError('constructor')
        with patch.object(runtime,'RuntimeBundle',side_effect=constructor),self.assertRaises(MemoryError):
            pg._compose_phase(self.diagnostic,self.postgres)
        self.assertEqual(len(allocated),1)
        with self.assertRaises(OSError):os.fstat(allocated[0])
        self.diagnostic.verify();self.postgres.verify()

    def test_pending_cancellation_closes_complete_untransferred_phase(self):
        result=Mock()
        with patch.object(pg,'_source_identity'),patch.object(runtime,'capture_runtime',return_value=self.diagnostic), \
             patch.object(pg,'capture_postgres',return_value=self.postgres),patch.object(pg,'_compose_phase',return_value=result), \
             patch.object(source.signal,'pthread_sigmask',side_effect=[set(),KeyboardInterrupt()]),self.assertRaises(KeyboardInterrupt):
            pg.capture_postgres_phase('a'*64,Path('/fixture'),BINDING)
        result.close.assert_called_once()

    def test_invalid_phase_resume_closes_adopted_descriptor(self):
        value=self.compose();packet=value.packet();fd=os.dup(value.bundle.descriptor);packet['bundle']['descriptor']=fd
        with patch.object(pg,'_phase_inventory',side_effect=ns.NamespaceProofError('phase')),self.assertRaises(ns.NamespaceProofError):
            pg.receive_postgres_phase(packet)
        with self.assertRaises(OSError):os.fstat(fd)
        value.verify()
