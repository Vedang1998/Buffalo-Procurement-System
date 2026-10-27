"""Pure PG16 TOC policy tests; fabricated catalog rows are not database proof."""
from dataclasses import replace
import unittest
from unittest.mock import patch
import sys
import types

import hosted_synthetic_recovery_toc as t


def fixture():
    rows = [
        (2615, 100, 'SCHEMA', '-', t.SCHEMA, t.OWNER, True),
        (3079, 101, 'EXTENSION', '-', 'pgcrypto', t.LEGACY_OWNER, True),
        (1259, 200, 'TABLE', t.SCHEMA, 'orders', t.OWNER, True),
        (1259, 201, 'SEQUENCE', t.SCHEMA, 'orders_id_seq', t.OWNER, True),
        (1259, 202, 'VIEW', t.SCHEMA, 'order_view', t.OWNER, True),
        (1255, 203, 'FUNCTION', t.SCHEMA, 'observe(integer, timestamp with time zone, text[])', t.OWNER, True),
        (1259, 204, 'INDEX', t.SCHEMA, 'orders_lookup', t.OWNER, True),
        (2606, 205, 'CONSTRAINT', t.SCHEMA, 'orders orders_pkey', t.OWNER, True),
        (2606, 206, 'FK CONSTRAINT', t.SCHEMA, 'orders orders_fk', t.OWNER, True),
        (2620, 207, 'TRIGGER', t.SCHEMA, 'orders immutable', t.OWNER, True),
        (2604, 208, 'DEFAULT', t.SCHEMA, 'orders order_id', t.OWNER, False),
        (2606, 209, 'CHECK CONSTRAINT', t.SCHEMA, 'orders positive', t.OWNER, False),
        (0, 0, 'SEQUENCE OWNED BY', t.SCHEMA, 'orders_id_seq', t.OWNER, True),
        (0, 200, 'TABLE DATA', t.SCHEMA, 'orders', t.OWNER, True),
        (0, 0, 'SEQUENCE SET', t.SCHEMA, 'orders_id_seq', t.OWNER, True),
    ]
    return t.CatalogCapture(tuple(t.CatalogRecord(*r) for r in rows), '00000003-0000000A-1', 'a'*64)


def toc(rows, *, schema=False, optional=True, offset=0):
    selected = [r for r in rows if (not schema or r.kind not in t._DATA) and (optional or r.required)]
    header = f''';
; Archive created at 2026-10-27 00:00:00 UTC
;     dbname: {t.DATABASE}
;     TOC Entries: {len(selected)+4}
;     Compression: gzip
;     Dump Version: 1.15-0
;     Format: CUSTOM
;     Integer: 4 bytes
;     Offset: 8 bytes
;     Dumped from database version: 16.14 (Ubuntu 16.14-1)
;     Dumped by pg_dump version: 16.14 (Ubuntu 16.14-1)
;
; Selected TOC Entries:
;
'''
    return (header + ''.join(f'{i+1+offset}; {a} {b} {c}\n' for i, r in enumerate(selected) for a,b,c in [r.key()])).encode('ascii')


class HostedSyntheticRecoveryTocTests(unittest.TestCase):
    def setUp(self):
        self.capture = fixture()
        self.full = toc(self.capture.records)
        self.schema = toc(self.capture.records, schema=True, offset=100)

    def run_case(self):
        return t.validate_tocs(self.capture, self.full, self.schema)

    def refuses(self):
        with self.assertRaises(t.RecoveryTocRefusal): self.run_case()

    def test_complete_schema_data_and_sequence_inventory_has_no_execution_authority(self):
        use, proof = self.run_case()
        self.assertEqual(proof['full_entries'], 15)
        self.assertEqual(proof['schema_entries'], 13)
        self.assertEqual(proof['source_snapshot'],self.capture.snapshot)
        self.assertEqual(proof['source_release_sha256'],self.capture.release_sha256)
        self.assertRegex(proof['catalog_records_sha256'],r'^[0-9a-f]{64}$')
        self.assertFalse(proof['execution_authority'])
        self.assertEqual(proof['restore_execution'], 'NOT_RUN')
        self.assertNotIn(b'Archive', use)
        self.assertIn(b'0 0 SEQUENCE SET', use)

    def test_inlined_defaults_checks_are_optional_but_serialization_must_match(self):
        self.full = toc(self.capture.records, optional=False)
        self.schema = toc(self.capture.records, schema=True, optional=False)
        self.run_case()
        self.schema = toc(self.capture.records, schema=True)
        self.refuses()

    def test_reordered_schema_dump_ids_do_not_change_semantics(self):
        self.schema = toc(tuple(reversed(self.capture.records)), schema=True, offset=500)
        self.run_case()

    def test_missing_table_data_or_sequence_set_refuses(self):
        for kind in t._DATA:
            with self.subTest(kind=kind):
                self.full = toc(tuple(r for r in self.capture.records if r.kind != kind))
                self.refuses()

    def test_missing_schema_object_in_both_archives_refuses(self):
        for kind in ('FUNCTION','INDEX','CONSTRAINT','FK CONSTRAINT','TRIGGER','SEQUENCE OWNED BY'):
            with self.subTest(kind=kind):
                rows = tuple(r for r in self.capture.records if r.kind != kind)
                self.full = toc(rows); self.schema = toc(rows, schema=True); self.refuses()

    def test_catalog_or_object_oid_mismatch_cannot_use_matching_description(self):
        for old,new in [(b'1259 200 TABLE ',b'1259 999 TABLE '),(b'1259 200 TABLE ',b'1255 200 TABLE '),
                        (b'0 200 TABLE DATA',b'0 201 TABLE DATA')]:
            self.setUp(); self.full = self.full.replace(old,new); self.refuses()

    def test_foreign_schema_owner_and_extension_refuse(self):
        for old,new in [(b'TABLE qa_mapping_test',b'TABLE public'),(t.OWNER.encode(),t.LEGACY_OWNER.encode()),
                        (b'EXTENSION - pgcrypto ',b'EXTENSION - plpython3u ')]:
            self.setUp(); self.full = self.full.replace(old,new); self.refuses()

    def test_acl_comment_global_and_unknown_descriptors_refuse(self):
        for descriptor in ('ACL qa_mapping_test TABLE orders '+t.OWNER,'COMMENT - EXTENSION pgcrypto ',
                           'DATABASE - foreign admin','DEFAULT ACL - DEFAULT PRIVILEGES FOR TABLES '+t.OWNER,
                           'EVENT TRIGGER - evil admin','SUBSCRIPTION - anything admin'):
            self.setUp();self.full += f'900; 0 0 {descriptor}\n'.encode();self.refuses()

    def test_duplicate_dump_id_and_semantic_record_refuse(self):
        self.full += self.full.splitlines(keepends=True)[-1];self.refuses()
        self.setUp();last=self.full.splitlines()[-1].split(b'; ',1)[1]
        self.full += b'999; '+last+b'\n';self.refuses()

    def test_data_in_schema_only_dump_refuses(self):
        self.schema = self.full; self.refuses()

    def test_header_requires_exact_database_pg16_format_and_complete_fields(self):
        for old,new in [(b'dbname: '+t.DATABASE.encode(),b'dbname: postgres'),(b'version: 16.',b'version: 17.'),
                        (b'Format: CUSTOM',b'Format: TAR'),(b'Dump Version: 1.15-0',b'Dump Version: 1.16-0'),
                        (b';     Integer: 4 bytes\n',b''),(b'TOC Entries: 19',b'TOC Entries: 1')]:
            self.setUp();self.full=self.full.replace(old,new);self.refuses()

    def test_header_count_is_not_assumed_selected_count_plus_a_magic_constant(self):
        self.full=self.full.replace(b'TOC Entries: 19',b'TOC Entries: 20');self.run_case()

    def test_controls_unicode_postrecord_comments_and_whitespace_refuse(self):
        for raw in (self.full+b'; hidden\n',self.full.replace(b'1; ',b'01; '),self.full.replace(b'TABLE ',b'TABLE  '),
                    self.full.replace(b'\n',b'\r\n'),self.full+b'\n',self.full+b'\xff\n',self.full[:-1]):
            self.full=raw;self.refuses()

    def test_bounded_input_and_descriptor_lengths(self):
        self.full=b'a'*(t.MAX_TOC_BYTES+1);self.refuses()
        self.setUp();self.full+=b'a'*4097+b'\n';self.refuses()

    def test_capture_records_require_real_types_owners_and_exact_synthetic_oids(self):
        for row in (replace(self.capture.records[0],catalog_oid=True),replace(self.capture.records[0],required=1),
                    replace(self.capture.records[1],owner=t.OWNER),replace(self.capture.records[-1],object_oid=201),
                    replace(self.capture.records[2],required=False)):
            self.setUp();self.capture=replace(self.capture,records=(row,*self.capture.records[1:]));self.refuses()

    def test_duplicate_or_missing_catalog_inventory_refuses(self):
        self.capture=replace(self.capture,records=(*self.capture.records,self.capture.records[0]));self.refuses()
        self.setUp();self.capture=replace(self.capture,records=tuple(r for r in self.capture.records if r.kind!='TABLE DATA'));self.refuses()

    def test_fake_sequence_ownership_and_table_data_identity_refuse(self):
        for kind,change in [('SEQUENCE OWNED BY',{'tag':'other_seq'}),('TABLE DATA',{'object_oid':900})]:
            self.setUp();self.capture=replace(self.capture,records=tuple(replace(r,**change) if r.kind==kind else r for r in self.capture.records));self.refuses()

    def test_identity_sequence_has_no_owned_by_but_keeps_sequence_set(self):
        self.capture=replace(self.capture,records=tuple(r for r in self.capture.records if r.kind!='SEQUENCE OWNED BY'))
        self.full=toc(self.capture.records);self.schema=toc(self.capture.records,schema=True);self.run_case()

    def test_sql_input_binding_has_no_mutation_or_ambient_path_or_fixed_counts(self):
        self.assertEqual(t.CATALOG_SQL.count('%s'),9)
        self.assertIn("k.contype IN ('p','u','x')",t.CATALOG_SQL)
        self.assertIn("d.deptype='a'",t.CATALOG_SQL)
        self.assertIn("a.attidentity=''",t.CATALOG_SQL)
        self.assertIn('p.proargtypes::oid[]',t.CATALOG_SQL)
        self.assertNotIn('842',t.CATALOG_SQL)
        self.assertEqual(t.DUMP_OPTIONS[-5:],('--no-acl','--no-comments','--no-security-labels','--no-publications','--no-subscriptions'))

    def test_capture_exports_only_after_existing_attestation_and_catalog_query(self):
        events=[];rows=[tuple(getattr(r,k) for k in r.__dataclass_fields__) for r in self.capture.records]
        class Result:
            def __init__(self,value):self.value=value
            def fetchone(self):return self.value
            def fetchall(self):return self.value
        class Connection:
            def execute(inner,query,args=None):
                events.append(query)
                if query=='SHOW transaction_isolation':return Result(('repeatable read',))
                if query=='SHOW transaction_read_only':return Result(('on',))
                if query==t.PGCRYPTO_MEMBERS_SQL:return Result([(True,777,0,0)])
                if query==t.CATALOG_SHAPE_SQL:self.assertEqual(args,(t.SCHEMA,));return Result((True,))
                if query==t.CATALOG_SQL:self.assertEqual(args,(t.SCHEMA,)*9);return Result(rows)
                if query=='SELECT pg_catalog.pg_export_snapshot()':return Result((self.capture.snapshot,))
                self.fail('unexpected query')
        module=types.ModuleType('procurement_os.synthetic_staging_database')
        module.attest_provisioner_connection=lambda c,target:(events.append('ATTEST') or 'a'*64)
        module._verified_pgcrypto_rows=lambda c:[(777,*([None]*12),t.OWNER)]
        with patch.dict(sys.modules,{'procurement_os.synthetic_staging_database':module}):
            self.assertEqual(t.capture_catalog(Connection(),object()),self.capture)
        self.assertLess(events.index('ATTEST'),events.index(t.CATALOG_SQL))
        self.assertEqual(events[-1],'SELECT pg_catalog.pg_export_snapshot()')

    def test_readwrite_or_wrong_isolation_cannot_reach_attestation(self):
        for isolation,readonly in [('read committed','on'),('repeatable read','off')]:
            class Result:
                def __init__(self,v):self.v=v
                def fetchone(self):return (self.v,)
            class Connection:
                def execute(self,q):return Result(isolation if 'isolation' in q else readonly)
            module=types.ModuleType('procurement_os.synthetic_staging_database')
            module.attest_provisioner_connection=lambda *a:self.fail('attestation reached')
            with patch.dict(sys.modules,{'procurement_os.synthetic_staging_database':module}),self.assertRaises(t.RecoveryTocRefusal):
                t.capture_catalog(Connection(),object())

    def test_unsupported_catalog_shape_refuses_before_export_or_descriptor_capture(self):
        events=[]
        class Result:
            def __init__(self,v):self.v=v
            def fetchone(self):return (self.v,)
        class Connection:
            def execute(inner,q,args=None):
                events.append(q)
                return Result({'SHOW transaction_isolation':'repeatable read','SHOW transaction_read_only':'on'}.get(q,False))
        module=types.ModuleType('procurement_os.synthetic_staging_database')
        module.attest_provisioner_connection=lambda *a:'a'*64
        with patch.dict(sys.modules,{'procurement_os.synthetic_staging_database':module}),patch.object(t,'_pgcrypto_members'),self.assertRaises(t.RecoveryTocRefusal):
            t.capture_catalog(Connection(),object())
        self.assertNotIn(t.CATALOG_SQL,events)
        self.assertNotIn('SELECT pg_catalog.pg_export_snapshot()',events)
        for fragment in ('relispartition','pg_inherits','extconfig IS NOT NULL','count(*)>1',"a.attidentity NOT IN ('a','d')",'c.oid IS NULL'):
            self.assertIn(fragment,t.CATALOG_SHAPE_SQL)

    def test_pgcrypto_membership_cannot_hide_application_function_or_foreign_object(self):
        module=types.ModuleType('procurement_os.synthetic_staging_database')
        module._verified_pgcrypto_rows=lambda c:[(777,*([None]*12),t.OWNER)]
        class Result:
            def __init__(self,rows):self.rows=rows
            def fetchall(self):return self.rows
        class Connection:
            def __init__(self,rows):self.rows=rows
            def execute(self,q):return Result(self.rows)
        with patch.dict(sys.modules,{'procurement_os.synthetic_staging_database':module}):
            t._pgcrypto_members(Connection([(True,777,0,0)]))
            for rows in ([],[(True,777,0,0),(True,888,0,0)],[(False,777,0,0)],[(True,777,1,0)],[(True,777,0,1)],[(1,777,0,0)],[(True,888,0,0)]):
                with self.subTest(rows=rows),self.assertRaises(t.RecoveryTocRefusal):t._pgcrypto_members(Connection(rows))
            module._verified_pgcrypto_rows=lambda c:[(777,*([None]*12),t.LEGACY_OWNER)]
            with self.assertRaises(t.RecoveryTocRefusal):t._pgcrypto_members(Connection([(True,777,0,0)]))

    def test_catalog_system_columns_are_projected_before_cte_use(self):
        self.assertIn('SELECT c.tableoid,c.*',t.CATALOG_SHAPE_SQL)
        self.assertIn('SELECT c.tableoid,c.*',t.CATALOG_SQL)
