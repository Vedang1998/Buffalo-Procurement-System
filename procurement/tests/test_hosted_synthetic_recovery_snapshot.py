"""Locked-application adapter tests with fake connections; no SQL/server proof."""
from contextlib import ExitStack
from dataclasses import replace
import copy
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from psycopg.pq import TransactionStatus
from procurement_os import synthetic_staging_database as db
from procurement_os import synthetic_staging_backup_v2 as backup
from procurement_os import local_backup_v2 as local
from procurement_os import staging_config as config
from hosted_synthetic_recovery_inputs import RecoveryInputRefusal
import hosted_synthetic_recovery_snapshot as s
import hosted_synthetic_recovery_toc as toc
import hosted_synthetic_restart_evidence as parity
from test_hosted_synthetic_recovery_toc import fixture


class FakeConnection:
    def __init__(self):
        self.autocommit=True;self.closed=False;self.broken=False;self.events=[];self.fail=None
        self.info=SimpleNamespace(transaction_status=TransactionStatus.IDLE,host='127.0.0.1',hostaddr='127.0.0.1',port=55432,
            dbname=toc.DATABASE,user=db.RUNTIME_LOGIN,backend_pid=12345)
        self.endpoint=('127.0.0.1',55432,12345);self.rollback_fails=False;self.rollback_nonidle=False

    def execute(self,query):
        text=query if isinstance(query,str) else query.as_string()
        self.events.append(text)
        if text=='ROLLBACK':
            if self.rollback_fails:raise RuntimeError('injected rollback failure')
            if not self.rollback_nonidle:self.info.transaction_status=TransactionStatus.IDLE
        elif text==s._BEGIN:self.info.transaction_status=TransactionStatus.INTRANS
        if self.fail and self.fail in text:raise RuntimeError('injected statement failure')
        return SimpleNamespace(fetchone=lambda:self.endpoint if text==s._ENDPOINT else None)


class PopulatedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.conn=FakeConnection()
        self.target=db.SyntheticStagingTarget(
            database_url=f'postgresql://{db.RUNTIME_LOGIN}@127.0.0.1:55432/{toc.DATABASE}',
            expected_private_host='127.0.0.1',project_id=config.EXPECTED_PROJECT_ID,
            environment_id=config.EXPECTED_ENVIRONMENT_ID,app_service_id=config.EXPECTED_APP_SERVICE_ID,
            postgres_service_id=config.EXPECTED_POSTGRES_SERVICE_ID,transfer_manifest_sha256='a'*64,owned_local_port=55432)
        capture=fixture();rows=list(capture.records)
        for i,name in enumerate(sorted(s._TABLE_COUNTS.values())):
            rows += [toc.CatalogRecord(1259,300+i,'TABLE',toc.SCHEMA,name,toc.OWNER,True),
                     toc.CatalogRecord(0,300+i,'TABLE DATA',toc.SCHEMA,name,toc.OWNER,True)]
        self.capture=replace(capture,records=tuple(rows),release_sha256=db.STAGING_BACKUP_RELEASE_SHA256)
        relations=[{'relation':'orders','row_count':1,'sha256':'a'*64}]
        relations += [{'relation':table,'row_count':parity.COUNTS[key],'sha256':'b'*64} for key,table in s._TABLE_COUNTS.items()]
        self.facts={**parity.COUNTS,**{k:'|'.join(['1'*64]*count) for k,count in parity.HASH_LIST_COUNTS.items()},
            'relation_inventory':sorted(relations,key=lambda x:x['relation']),
            'sequence_inventory':[{'sequence':'orders_id_seq','last_value':91,'is_called':True}]}
        self.bindings=[];u='00000000-0000-4000-8000-000000000001';hashes=[]
        for i in range(3):
            digest=hashlib.sha256(str(i).encode()).hexdigest();hashes.append(digest)
            suffix=f'vendor-00000000-0000-4000-8000-00000000000{i+2}/{digest}.internal.csv' if i<2 else f'packet/{digest}.review.zip'
            self.bindings.append(dict(path=f'monday-runs/{u}/{suffix}',bytes=1,sha256=digest))
        digest=hashlib.sha256(b'raw').hexdigest();self.bindings.append(dict(path=f'price-books/raw/{digest}.csv',bytes=None,sha256=digest))
        self.bindings.sort(key=lambda x:x['path']);self.facts['artifact_hashes']='|'.join(hashes)
        self.runtime_identity=db.EXPECTED_RUNTIME_ATTESTATION_IDENTITY
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.attest=self.stack.enter_context(mock.patch.object(db,'attest_runtime_connection',side_effect=self.attestation))
        self.state=self.stack.enter_context(mock.patch.object(local,'database_state_evidence',side_effect=self.state_evidence))
        self.storage=self.stack.enter_context(mock.patch.object(backup,'_storage_bindings',side_effect=self.storage_bindings))

    def attestation(self,conn,target):
        self.assertIs(conn,self.conn);self.assertIs(target,self.target);conn.events.append('ATTEST_RUNTIME')
        self.assertTrue(any(x.startswith('SET TRANSACTION SNAPSHOT ') for x in conn.events))
        return self.runtime_identity

    def state_evidence(self,conn,**kwargs):
        self.assertIs(conn,self.conn);self.assertEqual(kwargs,{'schema':toc.SCHEMA,'staging_runtime':True})
        self.assertIn('ATTEST_RUNTIME',conn.events);conn.events.append('STATE')
        return local._validated_state_evidence({'facts':copy.deepcopy(self.facts),'sha256':local.canonical_sha256(self.facts)})

    def storage_bindings(self,conn):conn.events.append('STORAGE');return copy.deepcopy(self.bindings)

    def run_case(self):return s.capture_populated_snapshot(self.conn,self.target,self.capture,commit='a'*40,tree='b'*40)

    def refuses(self):
        with self.assertRaises((s.RecoverySnapshotRefusal,toc.RecoveryTocRefusal,RecoveryInputRefusal,ValueError)):self.run_case()

    def test_import_precedes_all_reads_and_distinct_fixed_identities_are_bound(self):
        result=self.run_case();proof=json.loads(result.observation_json)
        self.assertEqual(proof['backup_release_sha256'],db.STAGING_BACKUP_RELEASE_SHA256)
        self.assertEqual(proof['runtime_attestation_identity'],db.EXPECTED_RUNTIME_ATTESTATION_IDENTITY)
        self.assertNotEqual(proof['backup_release_sha256'],proof['runtime_attestation_identity'])
        self.assertFalse(proof['execution_authority']);self.assertEqual(proof['sequence_quiescence'],'CALLER_PROOF_REQUIRED_NOT_MVCC')
        self.assertEqual(self.conn.events[0],s._BEGIN);self.assertEqual(self.conn.events[-1],'ROLLBACK')
        imported=next(i for i,q in enumerate(self.conn.events) if q.startswith('SET TRANSACTION SNAPSHOT '))
        self.assertLess(imported,self.conn.events.index(s._ENDPOINT));self.assertEqual(self.attest.call_count,1)
        self.assertEqual(json.loads(result.state_json)['facts'],self.facts)
        self.assertEqual(self.conn.info.transaction_status,TransactionStatus.IDLE)

    def test_snapshot_import_failure_cannot_reach_attestation_or_state(self):
        self.conn.fail='SET TRANSACTION SNAPSHOT'
        with self.assertRaises(RuntimeError):self.run_case()
        self.attest.assert_not_called();self.state.assert_not_called();self.storage.assert_not_called()
        self.assertEqual(self.conn.events[-1],'ROLLBACK')

    def test_remote_or_unowned_target_refuses_before_sql(self):
        for target in (replace(self.target,owned_local_port=None),replace(self.target,owned_local_port=True),
                       replace(self.target,expected_private_host='production.example')):
            self.target=target;self.refuses();self.assertEqual(self.conn.events,[])

    def test_nonidle_or_implicit_transaction_connection_refuses_without_rollback(self):
        for field,value in [('autocommit',False),('closed',True),('broken',True)]:
            original=getattr(self.conn,field);setattr(self.conn,field,value);self.refuses();setattr(self.conn,field,original)
        self.conn.info.transaction_status=TransactionStatus.INTRANS;self.refuses()
        self.assertEqual(self.conn.events,[])

    def test_provisioner_role_or_other_client_endpoint_refuses_before_sql(self):
        for field,value in [('user',db.PROVISIONER),('dbname','postgres'),('host','localhost'),('port',55433)]:
            original=getattr(self.conn.info,field);setattr(self.conn.info,field,value);self.refuses();setattr(self.conn.info,field,original)
        self.assertEqual(self.conn.events,[])

    def test_observed_server_endpoint_mismatch_refuses_before_attestation(self):
        self.conn.endpoint=('127.0.0.1',55433,12345);self.refuses();self.attest.assert_not_called()
        self.assertEqual(self.conn.events[-1],'ROLLBACK')

    def test_wrong_backup_release_refuses_before_sql_and_wrong_runtime_identity_before_state(self):
        self.capture=replace(self.capture,release_sha256='f'*64);self.refuses();self.assertEqual(self.conn.events,[])
        self.capture=replace(self.capture,release_sha256=db.STAGING_BACKUP_RELEASE_SHA256)
        self.runtime_identity=db.STAGING_BACKUP_RELEASE_SHA256;self.refuses();self.state.assert_not_called()
        self.assertEqual(self.conn.events[-1],'ROLLBACK')

    def test_missing_or_additional_state_relation_cannot_hide_behind_summary_counts(self):
        self.facts['relation_inventory']=[r for r in self.facts['relation_inventory'] if r['relation']!='orders'];self.refuses()
        self.facts['relation_inventory'].append({'relation':'zz_foreign','row_count':0,'sha256':'a'*64});self.refuses()

    def test_sequence_catalog_coverage_is_exact(self):
        self.facts['sequence_inventory'][0]['sequence']='other_seq';self.refuses()

    def test_summary_count_must_match_complete_relation_inventory(self):
        row=next(r for r in self.facts['relation_inventory'] if r['relation']=='runs');row['row_count']=3;self.refuses()

    def test_storage_artifact_hashes_and_counts_bind_state_hash_list(self):
        self.bindings=[r for r in self.bindings if not r['path'].endswith('.zip')];self.refuses()

    def test_changed_content_addressed_artifact_cannot_keep_old_state_hash(self):
        row=self.bindings[0];new='f'*64;row['path']=row['path'].replace(row['sha256'],new);row['sha256']=new
        self.bindings.sort(key=lambda r:r['path']);self.refuses()

    def test_duplicate_storage_keys_or_unbound_credentials_refuse(self):
        self.bindings.append(self.bindings[0]);self.refuses()
        self.bindings[-1]={'path':'credentials.env','bytes':1,'sha256':'a'*64};self.refuses()

    def test_begin_or_state_failure_still_rolls_back_and_preserves_original(self):
        self.conn.fail='BEGIN'
        with self.assertRaises(RuntimeError):self.run_case()
        self.assertEqual(self.conn.events[-1],'ROLLBACK');self.attest.assert_not_called()
        self.conn.fail=None;error=ValueError('component state failure');self.state.side_effect=error
        with self.assertRaises(ValueError) as observed:self.run_case()
        self.assertIs(observed.exception,error);self.assertEqual(self.conn.events[-1],'ROLLBACK')

    def test_ambiguous_rollback_withholds_result_and_preserves_first_failure(self):
        error=ValueError('first failure');self.state.side_effect=error;self.conn.rollback_fails=True
        with self.assertRaises(s.RecoverySnapshotCleanupError) as observed:self.run_case()
        self.assertIs(observed.exception.__cause__,error)

    def test_rollback_success_command_without_idle_readback_is_not_cleanup_pass(self):
        self.conn.rollback_nonidle=True
        with self.assertRaises(s.RecoverySnapshotCleanupError):self.run_case()

    def test_malformed_source_and_snapshot_labels_refuse_without_sql(self):
        self.capture=replace(self.capture,snapshot="x';SELECT 1;--");self.refuses();self.assertEqual(self.conn.events,[])
        self.capture=replace(self.capture,snapshot='00000003-0000000A-1')
        with self.assertRaises(s.RecoverySnapshotRefusal):s.capture_populated_snapshot(self.conn,self.target,self.capture,commit='short',tree='b'*40)
        self.assertEqual(self.conn.events,[])

    def test_keyboard_interrupt_during_capture_rolls_back_without_false_result(self):
        self.state.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):self.run_case()
        self.assertEqual(self.conn.events[-1],'ROLLBACK');self.assertTrue(s._idle(self.conn))

    def test_libpq_hostaddr_must_be_verified_loopback_before_any_sql(self):
        for address in ('198.51.100.1','','::1'):
            self.conn.info.hostaddr=address;self.refuses()
            self.assertEqual(self.conn.events,[])
        self.conn.info.hostaddr='127.0.0.1';self.run_case()

    def test_endpoint_uses_existing_host_projection_not_inet_text_netmask(self):
        queries=[]
        class ProducerConnection:
            def execute(inner,q):
                queries.append(q)
                return SimpleNamespace(fetchone=lambda:(toc.DATABASE,16,'127.0.0.1',db.RUNTIME_LOGIN,db.RUNTIME_LOGIN,toc.OWNER))
        facts=backup._database_facts(ProducerConnection(),self.target)
        self.assertEqual(facts['server_address'],'127.0.0.1')
        self.assertIn('pg_catalog.host(inet_server_addr())',queries[0])
        self.assertIn('pg_catalog.host(pg_catalog.inet_server_addr())',s._ENDPOINT)
        self.assertNotIn('inet_server_addr()::text',s._ENDPOINT)
        self.conn.endpoint=('127.0.0.1/32',55432,12345);self.refuses()
