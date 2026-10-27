"""State/sequence/download parity components; no database or restart execution."""
import ast
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import unittest

from hosted_synthetic_artifacts import CapturedArtifact, RegisteredArtifact, HostedArtifactError
import hosted_synthetic_restart_evidence as parity
from test_hosted_synthetic_packet_controls import COMMIT,TREE,RUN,SOUTH,WEST,uuid


def encode(facts):return parity._canonical({'facts':facts,'sha256':hashlib.sha256(parity._canonical(facts)).hexdigest()})

def source_state_validator():
    # Execute only the actual dependency-free validation functions. This is a
    # schema-compatibility check, not import/DB/attestation coverage of that module.
    path=Path(__file__).resolve().parents[2]/'procurement/src/procurement_os/local_backup_v2.py'
    nodes=ast.parse(path.read_text()).body
    selected=[n for n in nodes if isinstance(n,ast.FunctionDef) and n.name in ('canonical_sha256','_validated_state_evidence')]
    ns={'Any':object,'json':json,'hashlib':hashlib,'re':re,'_SHA256':re.compile(r'^[0-9a-f]{64}$'),'LocalBackupV2Error':ValueError}
    exec(compile(ast.Module(body=selected,type_ignores=[]),'actual-backup-v2-state-schema','exec'),ns)
    return ns['_validated_state_evidence']


class HostedRestartEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.old=[]
        for i,vendor in enumerate((SOUTH,WEST,None)):
            data=f'component bytes {i}\n'.encode();registry=RegisteredArtifact(RUN,str(42+i),'VENDOR_INTERNAL_CSV' if vendor else 'EMERGENCY_REVIEW_PACKET',vendor,len(data),hashlib.sha256(data).hexdigest())
            self.old.append(CapturedArtifact(registry,uuid(20+i),data))
        self.old=tuple(self.old);self.new=tuple(replace(x,guid=uuid(30+i)) for i,x in enumerate(self.old))
        self.before={**parity.COUNTS,**{key:'|'.join([str(i+1)*64]*count) for i,(key,count) in enumerate(parity.HASH_LIST_COUNTS.items())},
                     'relation_inventory':[{'relation':'meta','row_count':1,'sha256':'a'*64},{'relation':'runs','row_count':2,'sha256':'b'*64}],
                     'sequence_inventory':[{'sequence':'prices_price_id_seq','last_value':91,'is_called':True}]}
        self.before['artifact_hashes']='|'.join(x.registry.sha256 for x in self.old);self.after=copy.deepcopy(self.before)

    def run_fixture(self):return parity.compare_restart_evidence(encode(self.before),encode(self.after),self.old,self.new,commit=COMMIT,tree=TREE,run_id=RUN)
    def refuses(self):
        with self.assertRaises(HostedArtifactError):self.run_fixture()

    def test_exact_state_and_fresh_downloads_compare_without_claiming_lifecycle(self):
        source_state_validator()(json.loads(encode(self.before)))
        r=self.run_fixture();self.assertEqual((r['relations_compared'],r['sequences_compared'],r['artifacts_compared']),(2,1,3))
        self.assertFalse(r['execution_authority']);self.assertEqual(r['restart_execution'],'NOT_PROVEN');self.assertEqual(r['restore_execution'],'NOT_PROVEN')

    def test_relation_hash_or_count_change_is_not_hidden_by_same_summary_counts(self):
        for key,value in [('sha256','c'*64),('row_count',2)]:
            self.setUp();self.after['relation_inventory'][0][key]=value;self.refuses()

    def test_sequence_value_and_called_bit_are_independent_integrity_controls(self):
        self.after['sequence_inventory'][0]['last_value']=92;self.refuses()
        self.setUp();self.after['sequence_inventory'][0]['is_called']=False;self.refuses()

    def test_missing_and_duplicate_relations_are_not_a_complete_parity_claim(self):
        self.after['relation_inventory'].pop();self.refuses()
        self.setUp()
        for facts in (self.before,self.after):facts['relation_inventory'].insert(0,copy.deepcopy(facts['relation_inventory'][0]))
        self.refuses()

    def test_empty_duplicate_or_reordered_sequences_refuse(self):
        for replacement in [[],[{'sequence':'prices_price_id_seq','last_value':91,'is_called':True}]*2,
                            [{'sequence':'z_seq','last_value':1,'is_called':False},{'sequence':'a_seq','last_value':1,'is_called':False}]]:
            self.setUp();self.before['sequence_inventory']=copy.deepcopy(replacement);self.after['sequence_inventory']=replacement;self.refuses()

    def test_untyped_counts_booleans_and_sequence_state_refuse(self):
        for field,value in [('runs',True),('runs','2'),('runs',3)]:
            self.setUp();self.before[field]=value;self.after[field]=value;self.refuses()
        self.setUp();self.before['sequence_inventory'][0]['is_called']=1;self.after=copy.deepcopy(self.before);self.refuses()

    def test_schema_hash_and_duplicate_json_keys_are_validated(self):
        raw=json.loads(encode(self.before));raw['sha256']='f'*64
        with self.assertRaises(HostedArtifactError):parity.compare_restart_evidence(parity._canonical(raw),encode(self.after),self.old,self.new,commit=COMMIT,tree=TREE,run_id=RUN)
        raw=encode(self.before).replace(b'"runs":2',b'"runs":2,"runs":2')
        with self.assertRaises(HostedArtifactError):parity.compare_restart_evidence(raw,encode(self.after),self.old,self.new,commit=COMMIT,tree=TREE,run_id=RUN)

    def test_payload_hash_counts_shapes_and_sorted_order_are_not_opaque_strings(self):
        for value in ['', 'x'*64, 'a'*64, '|'.join(['b'*64,'a'*64]+['c'*64]*5)]:
            self.setUp();self.before['decision_payloads']=value;self.after['decision_payloads']=value;self.refuses()

    def test_artifact_registry_identity_cannot_change_after_restart(self):
        for field,value in [('artifact_id','99'),('vendor_id',uuid(99)),('run_id',uuid(99))]:
            self.setUp();self.new=(replace(self.new[0],registry=replace(self.new[0].registry,**{field:value})),*self.new[1:]);self.refuses()

    def test_artifact_kind_and_vendor_inventory_are_exact(self):
        self.new=(replace(self.new[0],registry=replace(self.new[0].registry,kind='EMERGENCY_REVIEW_PACKET',vendor_id=None)),*self.new[1:]);self.refuses()
        self.setUp();self.new=(self.new[0],replace(self.new[1],registry=replace(self.new[1].registry,vendor_id=SOUTH)),self.new[2]);self.refuses()

    def test_changed_bytes_rehashed_registry_still_refuses_parity(self):
        item=self.new[0];data=b'changed component\n';r=replace(item.registry,size_bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        self.new=(replace(item,registry=r,data=data),*self.new[1:]);self.refuses()

    def test_captured_bytes_must_match_independent_registry(self):
        self.new=(replace(self.new[0],data=b'bad'),*self.new[1:]);self.refuses()

    def test_missing_duplicate_artifact_or_reused_transfer_guid_refuses(self):
        self.new=self.new[:2];self.refuses()
        self.setUp();self.new=(self.new[0],self.new[0],self.new[2]);self.refuses()
        self.setUp();self.new=(replace(self.new[0],guid=self.old[2].guid),*self.new[1:]);self.refuses()

    def test_artifact_hash_aggregation_binds_numeric_registry_order(self):
        self.before['artifact_hashes']='|'.join(x.registry.sha256 for x in reversed(self.old));self.after=copy.deepcopy(self.before);self.refuses()
        self.setUp();self.old=tuple(reversed(self.old));self.new=tuple(reversed(self.new));self.run_fixture()

    def test_relation_name_fields_and_size_limits_are_bounded(self):
        for change in [lambda f:f['relation_inventory'][0].__setitem__('relation','../foreign'),lambda f:f['relation_inventory'][0].__setitem__('row_count',2**64),
                       lambda f:f['relation_inventory'][0].__setitem__('extra',True),lambda f:f['sequence_inventory'][0].__setitem__('last_value',-(2**64))]:
            self.setUp();change(self.before);self.after=copy.deepcopy(self.before);self.refuses()

    def test_source_and_run_identity_arguments_are_typed_and_exact(self):
        for key,value in [('commit','short'),('tree','F'*40),('run_id',uuid(99))]:
            args=dict(commit=COMMIT,tree=TREE,run_id=RUN);args[key]=value
            with self.assertRaises(HostedArtifactError):parity.compare_restart_evidence(encode(self.before),encode(self.after),self.old,self.new,**args)
