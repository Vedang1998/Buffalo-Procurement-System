"""Packet identity/economics fixtures, not an executed purchasing workflow."""
import ast
import copy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import csv
import unittest

import hosted_synthetic_packet_controls as controls
from hosted_synthetic_artifacts import CapturedArtifact, RegisteredArtifact, HostedArtifactError, SAFETY_LABEL
from test_hosted_synthetic_artifacts import source_builders

ROOT=Path(__file__).resolve().parents[2]
COMMIT='a'*40;TREE='b'*40
def uuid(n):return f'00000000-0000-4000-8000-{n:012d}'
RUN=uuid(1);SOUTH=uuid(2);WEST=uuid(3);SNAP=uuid(4)
def raw(value):return json.dumps(value,separators=(',',':'),ensure_ascii=False).encode()
def sha(value):return hashlib.sha256(value).hexdigest()


def csv_renderer():
    tree=ast.parse((ROOT/'procurement/src/procurement_os/po_csv.py').read_text())
    headers=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CSV_HEADERS' for t in n.targets))
    ns={'Any':object,'csv':csv,'io':io,'json':json,'datetime':datetime,'timezone':timezone,'Decimal':Decimal,'PoCsvError':ValueError,
        'CSV_HEADERS':headers,'SAFETY_LABEL':SAFETY_LABEL,'FORMAT_WARNING':'SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED','INTERNAL_OUTPUT_CONTRACT':'BUFFALO_INTERNAL_DRAFT_LINE_V2'}
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_cell','_captured_at','render_vendor_draft_csv')]
    exec(compile(ast.Module(body=functions,type_ignores=[]),'actual-vendor-csv-renderer','exec'),ns)
    return ns


class Fixture:
    def __init__(self):
        self.builders=source_builders();self.renderer=csv_renderer();self.vendor_ids=(SOUTH,WEST)
        self.contexts=[{'variant_id':v,'vendor_id':SOUTH if v=='1001' else WEST,'available_units':'0','trusted_incoming_units':'0','inventory_capture':[SNAP,'2026-10-05','2026-10-05 12:00:00+00:00'],
                       'inventory_rows':[['gid://shopify/Location/9001','0','0','VALID',SNAP]]} for v in ('1001','4001','4002','4003','4004','4005')]
        for context,(sku,physical,qualifying) in zip(self.contexts[:3],[('SUP-001',6,6),('WEST-MULTI-4001',4,12),('WEST-BTL-4002',4,4)],strict=True):
            context.update(supplier_sku=sku,units_per_case=physical,qualifying_units_per_case=qualifying)
        self.frozen={'business_date':'2026-10-05','method_version':'DEVELOPMENT_ROLLING_ORIGIN_V2','development_forecast_contract':'BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2',
                     'variant_ids':['1001','4001','4002','4003','4004','4005'],'contexts':self.contexts}
        self.preparation={'contract':'BUFFALO_STAGING_PREPARATION_STEP_OBSERVATION_V1','commit':COMMIT,'tree':TREE,'commercial_authority':False,'execution_authority':False,
                          'scope':'PREPARATION_DOM_COMPONENT_ONLY','run_id':RUN,'principal_ref':'owner:staging:01','vendors':{'southern':SOUTH,'western':WEST}}
        self.review={'contract':'BUFFALO_STAGING_REVIEW_DRAFT_STEP_OBSERVATION_V1','commit':COMMIT,'tree':TREE,'commercial_authority':False,'execution_authority':False,
                     'scope':'REVIEW_DRAFT_DOM_COMPONENT_ONLY','run_id':RUN,'draft_preview_fingerprint':'c'*64,
                     'draft_bindings':[{'vendor_id':SOUTH,'draft_po_id':uuid(10)},{'vendor_id':WEST,'draft_po_id':uuid(11)}],'draft_ids':[uuid(10),uuid(11)]}
        line_values=[('1001','SUP-001',6,36,'5','30','180'),('4001','WEST-MULTI-4001',2,8,'10.5','42','84'),('4002','WEST-BTL-4002',1,4,'4.5','18','18')]
        lines=[{'po_line_id':i+1,'variant_id':v,'supplier_sku':sku,'cases':cases,'loose_units':0,'ordered_units':units,'unit_cost':unit,'case_price':case,'merchandise_total':total,
                'loose_order_fee':'0','line_total':total,'captured_available_quantity':'0','inventory_captured_at':'2026-10-05T12:00:00+00:00','source_inventory_snapshot_run_id':SNAP,
                'inventory_location_scope':[{'location_gid':'gid://shopify/Location/9001','available_quantity':'0','captured_incoming_quantity':'0','validation_status':'VALID'}]}
               for i,(v,sku,cases,units,unit,case,total) in enumerate(line_values)]
        self.drafts=[]
        for i,vendor in enumerate(self.vendor_ids):
            southern=i==0;self.drafts.append({'vendor_id':vendor,'vendor_name':'Synthetic Southern' if southern else 'Synthetic Western Acceptance','po_id':uuid(10+i),
                'merchandise_total':'180' if southern else '102','delivery_fee':'0' if southern else '7','po_total':'180' if southern else '109',
                'below_vendor_minimum':not southern,'minimum_shortfall':'0' if southern else '18','minimum_disposition':'NOT_APPLICABLE' if southern else 'PAY_FEE',
                'loose_order_fee_total':'0','below_minimum_fee':'0' if southern else '7','economics_confirmed_by':'owner:staging:01','draft_preview_fingerprint':'c'*64,
                'lines':lines[:1] if southern else lines[1:]})
        self.summary={'safety_label':SAFETY_LABEL,'format_status':'SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED','run_id':RUN,'vendor_draft_count':2,
                      'draft_po_ids':[uuid(10),uuid(11)],'merchandise_total':'282','po_total':'289','release_performed':False,'shopify_calls':0,
                      'exceptions':[]}
        self.edit_entries=lambda entries:None;self.edit_artifacts=lambda artifacts:artifacts;self.edit_review=lambda review:None

    def arguments(self):
        fingerprint=sha(json.dumps(self.frozen,sort_keys=True,separators=(',',':')).encode())
        self.preparation['input_fingerprint']=fingerprint;self.summary['input_fingerprint']=fingerprint
        self.summary['vendor_draft_economics']=[{**{k:v for k,v in d.items() if k not in ('lines','po_id')},'draft_po_id':d['po_id']} for d in self.drafts]
        entries={name:self.builders['_json_entry']({'safety_label':SAFETY_LABEL,'items':[]}) for name in (
            'recommendations-and-reasons.json','frozen-price-economics.json','supplier-mapping-evidence.json','open-po-ledger-evidence.json',
            'blocked-item-exclusions.json','material-edit-confirmations.json','draft-readiness-evidence.json','forecast-and-protection-evidence.json')}
        entries['human-review-decisions.csv']=b'component_fixture_only\n'
        entries['frozen-input-manifest.json']=self.builders['_json_entry']({'safety_label':SAFETY_LABEL,'input_manifest':self.frozen})
        entries['packet-summary.json']=self.builders['_json_entry'](self.summary)
        csvs=[self.renderer['render_vendor_draft_csv'](RUN,d) for d in self.drafts]
        for vendor,data in zip(self.vendor_ids,csvs,strict=True):entries[f'vendor-{vendor}.internal.csv']=data
        self.edit_entries(entries)
        entries['manifest.json']=self.builders['_json_entry']({'safety_label':SAFETY_LABEL,'entries':{n:sha(v) for n,v in sorted(entries.items())}})
        packet=self.builders['_zip_bytes'](entries)
        artifacts=[];observations=[]
        for i,(data,vendor) in enumerate(zip([*csvs,packet],[*self.vendor_ids,None],strict=True)):
            kind='VENDOR_INTERNAL_CSV' if vendor else 'EMERGENCY_REVIEW_PACKET';identifier=str(42+i)
            reg=RegisteredArtifact(RUN,identifier,kind,vendor,len(data),sha(data));artifacts.append(CapturedArtifact(reg,uuid(20+i),data))
            observations.append({'url':controls.ORIGIN+f'/monday-runs/{RUN}/artifacts/{identifier}','cells':[kind,vendor or '—',str(len(data)),'Download']})
        prep=raw(self.preparation);self.review['preparation_proof_sha256']=sha(prep);self.review['artifacts']=observations;self.edit_review(self.review)
        return prep,raw(self.review),tuple(self.edit_artifacts(artifacts))


class HostedPacketControlTests(unittest.TestCase):
    def setUp(self):self.f=Fixture()
    def run_fixture(self):return controls.validate_packet_controls(*self.f.arguments(),commit=COMMIT,tree=TREE)
    def refuses(self):
        with self.assertRaises(HostedArtifactError):self.run_fixture()

    def test_actual_source_csv_zip_builders_reconcile_observed_identities_and_cash(self):
        result=self.run_fixture()
        self.assertEqual(result['observed_controls'],{'drafts':2,'lines':3,'packet_members':14,'merchandise_cents':28200,'fee_cents':700,'total_cents':28900})
        self.assertEqual(result['forecast_need_readiness_validation'],'REQUIRED_SEPARATE_GATE');self.assertFalse(result['execution_authority'])
        self.assertEqual(self.f.renderer['CSV_HEADERS'],controls.CSV_HEADERS)

    def test_source_identity_and_observation_scope_are_exact(self):
        for key,value in [('commit','f'*40),('tree','f'*40),('commercial_authority',True),('execution_authority',True),('scope','OTHER')]:
            with self.subTest(key=key):self.f=Fixture();self.f.preparation[key]=value;self.refuses()

    def test_original_preparation_bytes_bind_review_not_reconstructed_hash(self):
        prep,review,artifacts=self.f.arguments()
        with self.assertRaises(HostedArtifactError):controls.validate_packet_controls(prep+b' ',review,artifacts,commit=COMMIT,tree=TREE)

    def test_observed_vendor_to_draft_pairing_cannot_be_swapped_in_packet(self):
        self.f.drafts[0]['po_id'],self.f.drafts[1]['po_id']=self.f.drafts[1]['po_id'],self.f.drafts[0]['po_id'];self.refuses()

    def test_missing_duplicate_or_foreign_observed_draft_binding_refuses(self):
        for value in [[],[{'vendor_id':SOUTH,'draft_po_id':uuid(10)}]*2,[{'vendor_id':SOUTH,'draft_po_id':uuid(10)},{'vendor_id':WEST,'draft_po_id':uuid(10)}]]:
            with self.subTest(value=value):self.f=Fixture();self.f.review['draft_bindings']=value;self.refuses()

    def test_duplicate_western_variant_cannot_replace_other_line(self):
        self.f.drafts[1]['lines'][1]=copy.deepcopy(self.f.drafts[1]['lines'][0]);self.refuses()

    def test_exact_variant_sku_pack_physical_units_and_frozen_prices(self):
        for key,value in [('variant_id','40010'),('supplier_sku','foreign'),('cases',3),('ordered_units',24),('unit_cost','3.5'),('case_price','28'),('loose_units',1)]:
            with self.subTest(key=key):self.f=Fixture();self.f.drafts[1]['lines'][0][key]=value;self.refuses()

    def test_line_arithmetic_and_vendor_fee_are_independently_checked(self):
        for key,value in [('merchandise_total','83'),('loose_order_fee','1'),('line_total','85')]:
            with self.subTest(key=key):self.f=Fixture();self.f.drafts[1]['lines'][0][key]=value;self.refuses()
        for key in ['delivery_fee','below_minimum_fee','po_total','minimum_shortfall']:
            with self.subTest(key=key):self.f=Fixture();self.f.drafts[1][key]='0';self.refuses()

    def test_vendor_minimum_disposition_actor_and_preview_remain_observed(self):
        for key,value in [('below_vendor_minimum',False),('minimum_disposition','WAIVE'),('economics_confirmed_by','owner:other'),('draft_preview_fingerprint','d'*64)]:
            with self.subTest(key=key):self.f=Fixture();self.f.drafts[1][key]=value;self.refuses()

    def test_embedded_csv_must_match_its_own_vendor_not_sorted_bytes(self):
        def swap(entries):
            s,w=f'vendor-{SOUTH}.internal.csv',f'vendor-{WEST}.internal.csv';entries[s],entries[w]=entries[w],entries[s]
        self.f.edit_entries=swap;self.refuses()

    def test_summary_source_run_counts_and_authority_are_exact(self):
        for key,value in [('run_id',uuid(99)),('vendor_draft_count',True),('release_performed',True),('shopify_calls',1),('merchandise_total','281'),('po_total','288')]:
            with self.subTest(key=key):self.f=Fixture();self.f.summary[key]=value;self.refuses()

    def test_current_v2_contract_date_and_variant_inventory_are_required(self):
        for key,value in [('method_version','EMERGENCY_TRANSPARENT_V1'),('development_forecast_contract','OTHER'),('business_date','2026-11-01'),('variant_ids',['1001'])]:
            with self.subTest(key=key):self.f=Fixture();self.f.frozen[key]=value;self.refuses()

    def test_duplicate_frozen_context_and_missing_location_evidence_refuse(self):
        self.f.contexts[1]=copy.deepcopy(self.f.contexts[0]);self.refuses()
        self.f=Fixture();self.f.contexts[0]['inventory_rows']=[];self.refuses()

    def test_captured_stock_source_time_location_and_quantities_bind_frozen_context(self):
        for key,value in [('source_inventory_snapshot_run_id',uuid(99)),('inventory_captured_at','2026-10-06T12:00:00+00:00'),('captured_available_quantity','1')]:
            with self.subTest(key=key):self.f=Fixture();self.f.drafts[0]['lines'][0][key]=value;self.refuses()
        self.f=Fixture();self.f.drafts[0]['lines'][0]['inventory_location_scope'][0]['location_gid']='foreign';self.refuses()

    def test_authoritative_context_vendor_cannot_differ_from_observed_vendor(self):
        self.f.contexts[0]['vendor_id']=WEST;self.refuses()

    def test_capture_business_date_cannot_conflict_with_exact_timestamp(self):
        self.f.contexts[0]['inventory_capture'][1]='2026-10-06';self.refuses()

    def test_aware_offset_capture_normalizes_to_same_instant_but_naive_refuses(self):
        self.f.contexts[0]['inventory_capture'][2]='2026-10-05T08:00:00-04:00';self.run_fixture()
        for timestamp in ['2026-10-05T12:00:00','2026-10-05T11:00:00+00:00']:
            self.f=Fixture();self.f.contexts[0]['inventory_capture'][2]=timestamp;self.refuses()

    def test_frozen_sku_physical_and_qualifying_pack_preserve_multipack_identity(self):
        for key,value in [('supplier_sku','foreign'),('units_per_case',12),('qualifying_units_per_case',4),('units_per_case',True)]:
            self.f=Fixture();self.f.contexts[1][key]=value;self.refuses()

    def test_observed_artifact_ids_types_vendors_and_sizes_bind_registry(self):
        for index,value in [(0,'OTHER'),(1,uuid(99)),(2,'0')]:
            self.f=Fixture();self.f.edit_review=lambda r,i=index,v=value:r['artifacts'][0]['cells'].__setitem__(i,v);self.refuses()

    def test_duplicate_artifact_guid_and_noncanonical_inventory_refuse(self):
        from dataclasses import replace
        self.f.edit_artifacts=lambda items:[items[0],replace(items[1],guid=items[0].guid),items[2]];self.refuses()
        self.f=Fixture();self.f.edit_review=lambda r:r['artifacts'].pop();self.refuses()

    def test_duplicate_csv_header_or_extra_column_refuses(self):
        original=self.f.renderer['render_vendor_draft_csv']
        for suffix in (',extra',',variant_id'):
            self.f=Fixture()
            def altered(run,draft):
                lines=original(run,draft).decode().splitlines();lines[0]+=suffix;return ('\n'.join(lines)+'\n').encode()
            self.f.renderer['render_vendor_draft_csv']=altered;self.refuses()

    def test_document_safety_label_and_duplicate_json_key_refuse(self):
        self.f.edit_entries=lambda e:e.__setitem__('packet-summary.json',b'{"safety_label":"wrong"}');self.refuses()
        self.f=Fixture()
        def duplicate(entries):
            name='frozen-price-economics.json'
            # Last-value-wins parsing would otherwise retain a valid document.
            entries[name]=entries[name].rstrip()[:-1]+b',"safety_label":'+raw(SAFETY_LABEL)+b'}'
        self.f.edit_entries=duplicate;self.refuses()

    def test_nonfinite_exponent_and_overlong_money_refuse(self):
        for value in ['NaN','Infinity','2.82e2','9'*100]:
            self.f=Fixture();self.f.summary['merchandise_total']=value;self.refuses()
