"""Source-rendered review/readiness packet components; no DB or workflow run."""
import ast
import copy
import csv
from decimal import Decimal, ROUND_HALF_UP
import io
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'procurement/src'))
import hosted_synthetic_packet_evidence as evidence
from hosted_synthetic_artifacts import HostedArtifactError, SAFETY_LABEL
from test_hosted_synthetic_packet_controls import Fixture, COMMIT, TREE, RUN, SOUTH, WEST, uuid


def review_renderer():
    module=ast.parse((ROOT/'procurement/src/procurement_os/emergency_packet.py').read_text())
    funcs=[n for n in module.body if isinstance(n,ast.FunctionDef) and n.name in ('_review_csv','_spreadsheet_cell')]
    ns={'Any':object,'io':io,'csv':csv,'SAFETY_LABEL':SAFETY_LABEL}
    exec(compile(ast.Module(body=funcs,type_ignores=[]),'actual-review-csv-renderer','exec'),ns)
    return ns['_review_csv']


class EvidenceFixture(Fixture):
    def __init__(self):
        super().__init__();self.edit_evidence=lambda docs:None;self.edit_review_rows=lambda rows:rows
        self.preparation['recommendations']=[{'variant_id':v,'recommendation_id':str(101+i)} for i,v in enumerate(('1001','4001','4002','4005'))]
        self.preparation['blockers']=[{'variant_id':v,'exception_id':str(501+i)} for i,v in enumerate(('4003','4004'))]
        self.review['decisions']=[]
        for i,context in enumerate(self.contexts):
            v=context['variant_id'];context['offer_id']=201+i;context['blockers']=[]
            context['development_forecast_evidence']={'forecast_daily_velocity':'2.000000' if v in ('1001','4005') else '1.333333'}
            if v=='4005':context.update(supplier_sku='WEST-ALLOC-4005',units_per_case=4,qualifying_units_per_case=4)
            if v in ('4003','4004'):context['blockers']=[evidence._BLOCKERS[v]]
        # Missing selected head has no vendor/offer authority.
        self.contexts[3]['vendor_id']=None
        for variant,(action,_,_,unit,case,amount,_) in evidence._DECISIONS.items():
            d={'variant_id':variant,'action':action}
            if action!='REJECT':d.update(calculation={'amount_cents':int(Decimal(amount)*100),'tier':{'case_price':case,'unit_price':unit}},preview_fingerprint='d'*64)
            self.review['decisions'].append(d)
        self.review_velocities={c["variant_id"]:c["development_forecast_evidence"]["forecast_daily_velocity"] for c in self.contexts}
        self.edit_entries=self.inject

    def gate(self,name):
        return {'gate_name':name,'scope_type':'GLOBAL','scope_id':'','status':'PASS','severity':'HIGH','blocks_po':True,
                'message':'Synthetic component evidence','evidence':{},'checked_at':'2026-10-05 14:00:00+00:00'}

    def inject(self,entries):
        contexts={c['variant_id']:c for c in self.contexts};docs={n:json.loads(v) for n,v in entries.items() if n.endswith('.json')}
        recs=[];mappings=[];decision_rows=[]
        for i,(variant,(action,cases,units,unit,case,amount,comment)) in enumerate(evidence._DECISIONS.items()):
            context=contexts[variant];vendor=context['vendor_id'];name='Synthetic Southern' if variant=='1001' else 'Synthetic Western Acceptance'
            recs.append({'recommendation_id':101+i,'variant_id':variant,'vendor_id':vendor,'offer_id':context['offer_id']})
            mapping={'variant_id':variant,'vendor_id':vendor,'offer_id':context['offer_id'],'supplier_sku':context['supplier_sku'],
                     'units_per_case':context['units_per_case'],'qualifying_units_per_case':context['qualifying_units_per_case']}
            decision=next(d for d in self.review['decisions'] if d['variant_id']==variant)
            if action!='REJECT':mapping['final_price_tier']=copy.deepcopy(decision['calculation']['tier'])
            mappings.append(mapping)
            velocity=Decimal(self.review_velocities[variant])
            days=(Decimal(units)/velocity).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if velocity else None
            reviewed={'approved_case_price':case or None,'approved_merchandise_total':amount,'approved_loose_order_fee':'0',
                      'resulting_inventory_units':units,'resulting_days_supply':None if days is None else str(days),
                      'days_supply_status':'CALCULATED_FROM_FROZEN_FORECAST' if velocity else 'UNDEFINED_ZERO_FORECAST'}
            decision_rows.append([variant,name,action,int(cases),0,int(units),Decimal(unit),Decimal(amount),comment,'owner:staging:01',{'review':reviewed}])
        docs['recommendations-and-reasons.json']['items']=recs;docs['supplier-mapping-evidence.json']['items']=mappings
        exclusions=[]
        for i,(variant,reason) in enumerate(evidence._BLOCKERS.items()):
            exclusions.append({'exception_id':501+i,'type':'MONDAY_INPUT_BLOCKER','severity':'HIGH','variant_id':variant,'message':reason,'status':'OPEN',
                'run_only_exclusion':{'exclusion_id':601+i,'action':'ACKNOWLEDGE_AND_EXCLUDE','scope':'RUN_ONLY','actor':'owner:staging:01',
                    'reason':f'Acknowledged synthetic blocker for Variant {variant}; omit RUN_ONLY','input_fingerprint':self.preparation['input_fingerprint'],
                    'created_at':'2026-10-05 14:00:00+00:00','evidence':{'original_exception_type':'MONDAY_INPUT_BLOCKER','original_severity':'HIGH','original_message':reason,
                        'original_vendor_id':contexts[variant].get('vendor_id'),'original_variant_id':variant,'action_effect':'EXCLUDED_FROM_THIS_RUN_ONLY','global_rules_changed':False}}})
        docs['blocked-item-exclusions.json']['items']=exclusions;docs['packet-summary.json']['exceptions']=copy.deepcopy(exclusions)
        docs['material-edit-confirmations.json']['items']=[]
        readiness=[]
        for i,vendor in enumerate((SOUTH,WEST)):
            readiness.append({'vendor_id':vendor,'draft_po_id':uuid(10+i),'readiness_by_variant':[
                {'variant_id':v,'vendor_id':vendor,'run_id':RUN,'applicable_gate_names':sorted(evidence.GATES),'blockers':[],
                 'gates':[self.gate(n) for n in sorted(evidence.GATES)]} for v in (['1001'] if i==0 else ['4001','4002'])]})
        docs['draft-readiness-evidence.json']['items']=readiness
        rows=self.edit_review_rows(decision_rows)
        class Connection:
            def execute(self,sql,args):
                assert 'FROM review_decisions' in sql and args==(RUN,)
                return self
            def fetchall(self):return rows
        entries['human-review-decisions.csv']=review_renderer()(Connection(),RUN)
        self.edit_evidence(docs)
        for name,value in docs.items():entries[name]=self.builders['_json_entry'](value)


class HostedPacketEvidenceTests(unittest.TestCase):
    def setUp(self):self.f=EvidenceFixture()
    def run_fixture(self):return evidence.validate_packet_decision_evidence(*self.f.arguments(),commit=COMMIT,tree=TREE)
    def refuses(self):
        with self.assertRaises(HostedArtifactError):self.run_fixture()
    def mutate_gate(self,callback):
        def edit(docs):callback(docs['draft-readiness-evidence.json']['items'][0]['readiness_by_variant'][0])
        self.f.edit_evidence=edit

    def test_actual_review_csv_and_scoped_predicate_reconcile_four_decisions(self):
        result=self.run_fixture();self.assertEqual((result['owner_decisions'],result['run_only_exclusions'],result['readiness_lines']),(4,2,3))
        self.assertFalse(result['execution_authority']);self.assertEqual(result['forecast_need_validation'],'REQUIRED_SEPARATE_GATE')

    def test_decision_action_quantities_actor_comment_and_cash_are_frozen(self):
        for column,value in [(2,'REJECT'),(3,9),(4,1),(5,24),(6,Decimal('99')),(7,Decimal('1')),(8,'foreign'),(9,'owner:foreign')]:
            self.f=EvidenceFixture()
            def edit(rows,c=column,v=value):rows[1][c]=v;return rows
            self.f.edit_review_rows=edit;self.refuses()

    def test_rejected_row_retains_zero_economics_and_no_case_price(self):
        def edit(rows):rows[-1][10]['review']['approved_case_price']='20';return rows
        self.f.edit_review_rows=edit;self.refuses()

    def test_resulting_inventory_and_days_supply_use_frozen_velocity(self):
        for key,value in [('resulting_inventory_units','9'),('resulting_days_supply','7'),('days_supply_status','UNDEFINED_ZERO_FORECAST')]:
            self.f=EvidenceFixture()
            def edit(rows,k=key,v=value):rows[1][10]['review'][k]=v;return rows
            self.f.edit_review_rows=edit;self.refuses()
        self.f=EvidenceFixture();self.f.contexts[-1]['development_forecast_evidence']['forecast_daily_velocity']='0';self.f.review_velocities['4005']='0';self.run_fixture()

    def test_duplicate_or_missing_decision_rows_refuse(self):
        self.f.edit_review_rows=lambda rows:[rows[0],rows[0],*rows[2:]];self.refuses()
        self.f=EvidenceFixture();self.f.edit_review_rows=lambda rows:rows[:-1];self.refuses()

    def test_recommendation_ids_bind_preparation_and_frozen_offer_identity(self):
        for key,value in [('recommendation_id',999),('vendor_id',WEST),('offer_id',999)]:
            self.f=EvidenceFixture();self.f.edit_evidence=lambda d,k=key,v=value:d['recommendations-and-reasons.json']['items'][0].__setitem__(k,v);self.refuses()

    def test_mapping_variant_pack_and_final_price_tier_match_review(self):
        for key,value in [('offer_id',999),('supplier_sku','foreign'),('units_per_case',12),('qualifying_units_per_case',4),('final_price_tier',None)]:
            self.f=EvidenceFixture();self.f.edit_evidence=lambda d,k=key,v=value:d['supplier-mapping-evidence.json']['items'][1].__setitem__(k,v);self.refuses()

    def test_review_amount_and_tier_cannot_both_become_null(self):
        self.f.review['decisions'][0]['calculation']['tier']=None;self.refuses()

    def test_blocker_mirror_original_status_and_run_only_scope_are_retained(self):
        self.f.edit_evidence=lambda d:d['packet-summary.json'].__setitem__('exceptions',[]);self.refuses()
        for field,value in [('status','RESOLVED'),('type','OTHER'),('exception_id',999)]:
            self.f=EvidenceFixture()
            def edit(d,k=field,v=value):
                for rows in [d['blocked-item-exclusions.json']['items'],d['packet-summary.json']['exceptions']]:rows[0][k]=v
            self.f.edit_evidence=edit;self.refuses()

    def test_exclusion_action_scope_actor_fingerprint_and_reason_bind_owner(self):
        for field,value in [('action','RESOLVE'),('scope','GLOBAL'),('actor','owner:foreign'),('input_fingerprint','f'*64),('reason','other')]:
            self.f=EvidenceFixture()
            def edit(d,k=field,v=value):
                for rows in [d['blocked-item-exclusions.json']['items'],d['packet-summary.json']['exceptions']]:rows[0]['run_only_exclusion'][k]=v
            self.f.edit_evidence=edit;self.refuses()

    def test_exclusion_cannot_change_global_rules_or_original_vendor(self):
        for field,value in [('global_rules_changed',True),('original_vendor_id',SOUTH),('original_variant_id','1001'),('action_effect','RESOLVED')]:
            self.f=EvidenceFixture()
            def edit(d,k=field,v=value):
                for rows in [d['blocked-item-exclusions.json']['items'],d['packet-summary.json']['exceptions']]:rows[0]['run_only_exclusion']['evidence'][k]=v
            self.f.edit_evidence=edit;self.refuses()

    def test_unexpected_material_confirmation_is_not_ignored(self):
        self.f.edit_evidence=lambda d:d['material-edit-confirmations.json'].__setitem__('items',[{'unapproved':True}]);self.refuses()

    def test_required_readiness_missing_fail_and_claimed_blocker_refuse(self):
        for edit in [lambda x:x['gates'].pop(),lambda x:x['gates'][0].__setitem__('status','FAIL'),lambda x:x['blockers'].append({'type':'READINESS_GATE'})]:
            self.f=EvidenceFixture();self.mutate_gate(edit);self.refuses()

    def test_warn_and_unrelated_scoped_failure_keep_existing_scope_semantics(self):
        def edit(line):
            line['gates'][0]['status']='WARN';other=self.f.gate('PRICE_COVERAGE');other.update(scope_type='VARIANT',scope_id='9999',status='FAIL');line['gates'].append(other)
        self.mutate_gate(edit);self.run_fixture()

    def test_applied_scope_failure_is_not_hidden_by_global_pass(self):
        def edit(line):
            other=self.f.gate('PRICE_COVERAGE');other.update(scope_type='VARIANT',scope_id='1001',status='FAIL');line['gates'].append(other)
        self.mutate_gate(edit);self.refuses()

    def test_unknown_status_duplicate_gate_and_untyped_flag_refuse(self):
        for edit in [lambda x:x['gates'][0].__setitem__('status','UNKNOWN'),lambda x:x['gates'].append(copy.deepcopy(x['gates'][0])),lambda x:x['gates'][0].__setitem__('blocks_po','false')]:
            self.f=EvidenceFixture();self.mutate_gate(edit);self.refuses()

    def test_readiness_cannot_claim_other_run_vendor_variant_or_gate_set(self):
        for key,value in [('run_id',uuid(99)),('vendor_id',WEST),('variant_id','4001'),('applicable_gate_names',[])]:
            self.f=EvidenceFixture();self.mutate_gate(lambda x,k=key,v=value:x.__setitem__(k,v));self.refuses()

    def test_readiness_timestamp_is_aware_and_exclusion_identity_unique(self):
        self.mutate_gate(lambda x:x['gates'][0].__setitem__('checked_at','2026-10-05'));self.refuses()
        self.f=EvidenceFixture()
        def edit(d):
            for rows in [d['blocked-item-exclusions.json']['items'],d['packet-summary.json']['exceptions']]:rows[1]['run_only_exclusion']['exclusion_id']=rows[0]['run_only_exclusion']['exclusion_id']
        self.f.edit_evidence=edit;self.refuses()

    def test_actual_nested_forecast_field_is_required_no_flat_fallback(self):
        self.f.contexts[0]['forecast_daily_velocity']='2.000000'
        self.f.contexts[0]['development_forecast_evidence'].pop('forecast_daily_velocity')
        # The source-shaped CSV keeps the prior number; whole manifest and all
        # artifact hashes are regenerated, so refusal is caused by missing input.
        args=self.f.arguments()
        evidence.validate_packet_controls(*args,commit=COMMIT,tree=TREE)
        with self.assertRaises(HostedArtifactError):
            evidence.validate_packet_decision_evidence(*args,commit=COMMIT,tree=TREE)

    def test_rejected_variant_stock_and_incoming_are_not_unchecked(self):
        for key in ('available_units','trusted_incoming_units'):
            self.f=EvidenceFixture();self.f.contexts[-1][key]='5';self.refuses()

    def test_nested_json_equality_does_not_alias_booleans_and_integers(self):
        self.f.review['decisions'][0]['calculation']['tier']['price_id']=1
        self.run_fixture()
        self.f.edit_evidence=lambda d:d['supplier-mapping-evidence.json']['items'][0]['final_price_tier'].__setitem__('price_id',True)
        self.refuses()
        self.f=EvidenceFixture()
        self.f.edit_evidence=lambda d:d['packet-summary.json']['exceptions'][0]['run_only_exclusion']['evidence'].__setitem__('global_rules_changed',0)
        self.refuses()
