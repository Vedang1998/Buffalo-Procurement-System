"""Causal PG dependency, policy-separation and real sealed-memfd tests.

Synthetic package bytes are not trusted-host native execution evidence.
"""
from dataclasses import replace
import fcntl
import hashlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

import hosted_synthetic_namespace as ns
import hosted_synthetic_postgres_runtime as pg
import hosted_synthetic_runtime as runtime
import hosted_synthetic_source as source
from test_hosted_synthetic_runtime import elf, fixture_bundle

BINDING=source.HostedSourceBinding('b'*40,'c'*40,(('uv.lock','d'*64),))


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
