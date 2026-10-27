"""Locked-app-dependent pure packet authority tests; no DB or application service."""
import copy
from datetime import date,datetime
from decimal import Decimal
import json
import unittest

from test_hosted_synthetic_packet_forecast import ForecastFixture,ROOT
from test_hosted_synthetic_packet_controls import COMMIT,TREE,uuid,raw,sha
from hosted_selected_fixture import selected_records
from procurement_os import synthetic_selected_offer as selected
from procurement_os.price_book import parse_price_book_csv
from hosted_synthetic_artifacts import HostedArtifactError
import hosted_synthetic_packet_authority as authority


class PriceFixture(ForecastFixture):
    def __init__(self):
        super().__init__();self.frozen['offer_resolution_contract']=selected.CONTRACT
        self.change_price_docs=lambda docs:None;self.change_context=lambda:None
        self.preparation['fixture_proof_sha256']='9'*64
        self.change_docs=self.price_docs
        self.policy=json.loads((ROOT/'procurement/config/synthetic_price_replacement_fixture.json').read_text())['policies'][0]
        self.book=parse_price_book_csv((ROOT/'procurement/config/synthetic_price_replacement_book.csv').read_bytes())
        self.intent={key:'a'*64 for key in authority.INTENT_FIELDS}
        self.intent.update(contract='BUFFALO_SYNTHETIC_PRICE_APPLY_PREVIEW_V1',price_book_batch_id=uuid(81),vendor_id=authority.SOUTH,
            price_scope_key='COMPLETE_VENDOR',apply_idempotency_key='synthetic-apply-v1',prior_event_id=uuid(79),expected_prior_head_version=1,
            resulting_current_scope_sha256='a'*64,scope_membership_sha256='b'*64,raw_content_sha256=self.book['content_sha256'],
            policy_ref=self.policy['policy_ref'],policy_sha256=selected._pg_jsonb_sha256(self.policy),confirmation_payload_sha256='d'*64,price_book_promotion_event_id=1,
            expected_current_row_count=4,resulting_current_row_count=4,application_at='2026-10-01T14:00:00+00:00',variant_ids=['1001'],
            principal_ref=self.preparation['principal_ref'],role_ref='procurement.price.approve',commercial_authority=False,real_price_approval=False)
        self.applied={'contract':'BUFFALO_STAGING_APPLY_STEP_OBSERVATION_V1','commit':COMMIT,'tree':TREE,'batch_id':uuid(81),
            'preview_sha256':sha(authority._compact(self.intent)),'backup_manifest_sha256':self.intent['backup_manifest_sha256'],
            'operator_proof_sha256':'e'*64,'confirmation_proof_sha256':'f'*64,'observed_status':'APPLIED_CURRENT','scope':'APPLY_STEP_ONLY','commercial_authority':False,'execution_authority':False}
        self.mapping={'contract':'BUFFALO_STAGING_MAPPING_STEP_OBSERVATION_V1','commit':COMMIT,'tree':TREE,'apply_proof_sha256':sha(raw(self.applied)),
            'fixture_proof_sha256':self.preparation['fixture_proof_sha256'],'observations':[],'controls':{'review_batch_count':3,'candidate_count':7,'decision_count':7,'selection_event_count':5,'selection_head_count':5},
            'scope':'MAPPING_SELECTION_STEP_ONLY','commercial_authority':False,'execution_authority':False}
        self.preparation['price_batch_id']=uuid(81)
        for c in self.contexts:
            v=c['variant_id'];e=selected.selected_blocker_evidence(blocker=c['blockers'][0] if c['blockers'] else 'unused',variant_id=v,
                business_date=date(2026,10,5),evaluation_at=datetime.fromisoformat(self.frozen['evaluation_at']))
            e['selection_observed_at']=self.frozen['evaluation_at']
            if v=='4003':
                e['resolution_blockers']=c['blockers'];c['selected_offer_input_evidence']=e;c['selected_offer_input_evidence_sha256']=selected._compact_sha256(e);continue
            e.update(copy.deepcopy(c['selected_offer_input_evidence']),recommendation_effect='SELECTED_OFFER_INPUT_AUTHORITY',commercial_source_authority='NOT_APPROVED',source_import_state='NOT_IMPORT_READY')
            e.pop('blocker');e['selected_offer']['active']=True;e['applicable_vendor_terms']['sha256']=selected._compact_sha256(c['vendor_rules'])
            e.update(selected_records(c,at=self.frozen['evaluation_at'],principal=self.preparation['principal_ref']))
            rows=[]
            for i,(level,quantity,unit,case,price,page) in enumerate(authority.LADDERS[v]):
                price_id=501+i if v=='1001' else 600+int(v)*2+i
                rows.append([price_id,c['offer_id'],'2026-10-01',level,None if quantity is None else quantity+'.0000',unit,
                             format(Decimal(case),'.4f'),format(Decimal(price),'.4f'),
                             'synthetic-october-replacement.csv' if v=='1001' else 'synthetic-multivendor-v2.csv',page])
            c['prices']=rows;e['applicable_price_ladder']={'contract':'FROZEN_SELECTED_OFFER_PRICE_LADDER_V1','effective_month':'2026-10-01','rows':copy.deepcopy(rows),'sha256':selected._compact_sha256(rows)}
            first=rows[1] if v=='1001' else rows[0]
            e['initial_applicable_price_tier']=dict(zip(('price_id','offer_id','level_type','break_qty','break_unit','case_price','unit_price'),[first[0],first[1],*first[3:8]],strict=True),price_ladder_sha256=selected._compact_sha256(rows))
            e['legacy_active_standard_offer_comparison']={'authority':'COMPARISON_ONLY_NO_AUTHORITY','active_standard_offer_ids':[c['offer_id']],'active_standard_offer_count':1}
            if v=='1001':
                a={'contract':'BUFFALO_SYNTHETIC_APPLICABLE_PRICE_AUTHORITY_V1','policy_ref':self.intent['policy_ref'],'policy_sha256':self.intent['policy_sha256'],
                   'head':{'event_id':uuid(80),'head_version':2,'active_price_book_batch_id':uuid(81),'current_scope_sha256':'a'*64},
                   'apply_event':{'event_id':uuid(80),'price_book_batch_id':uuid(81),'raw_content_sha256':self.book['content_sha256'],'scope_membership_sha256':'b'*64,
                                  'resulting_current_scope_sha256':'a'*64,'payload_sha256':'c'*64,'recorded_at':'2026-10-01 14:00:00+00:00'},
                   'price_confirmation':{'promotion_event_id':1,'confirmation_payload_sha256':'d'*64},
                   'source_batch':{'price_book_batch_id':uuid(81),'raw_content_sha256':self.book['content_sha256'],'scope_membership_sha256':'b'*64},
                   'prices':[{'price_id':row[0],'source_row_number':parsed['source_row_number'],'source_row_sha256':selected._pg_jsonb_sha256(parsed['raw_payload'])} for row,parsed in zip(rows,self.book['rows'][:2],strict=True)],
                   'commercial_authority':False,'real_price_approval':False}
                e['applicable_price_authority']={**a,'sha256':selected._compact_sha256(a)}
            if v=='4004':
                e.pop('initial_applicable_price_tier');e.update(resolution_blockers=c['blockers'],blocker=c['blockers'][0],recommendation_effect='NO_RECOMMENDATION_BLOCKED')
            # Default Psycopg JSONB decoding of NUMERIC(12,4) metadata yields
            # floats; prices/offer projections remain Decimal strings. SQL row
            # payload hashes are fabricated opaque fixture bindings here.
            for name in ('mapping_candidate','mapping_decision'):
                record=e[name]
                for projection in (record,record['canonical_payload'],record.get('reviewed_facts',{}),record['canonical_payload'].get('reviewed_facts',{})):
                    for field in ('physical_units_value','retail_pack_units_value','shopify_units_value','qualifying_units_value'):
                        if projection.get(field) is not None:projection[field]=float(projection[field])
            c['selected_offer_input_evidence']=e;c['selected_offer_input_evidence_sha256']=selected._compact_sha256(e)
            self.mapping['observations'].append({'candidate_id':e['mapping_candidate']['candidate_id'],'variant_id':v,'offer_id':str(c['offer_id']),'action':'APPROVE_MAPPING',
                'decision':{'mapping_decision_id':e['mapping_decision']['mapping_decision_id'],'preview_sha256':e['mapping_decision']['preview_sha256']},
                'selection':{'mapping_decision_id':e['mapping_decision']['mapping_decision_id'],'preview_sha256':e['selection_event']['preview_sha256']}})
            if v in ('1001','4001','4002'):
                row=rows[1] if v in ('1001','4001') else rows[0]
                tier=dict(zip(('price_id','level_type','break_qty','break_unit','case_price','unit_price'),[row[0],*row[3:8]],strict=True),price_ladder_sha256=selected._compact_sha256(rows),run_price_snapshot_id=row[0]+10000)
                if v=='1001':tier.update(source_price_book_batch_id=uuid(81),source_price_book_row_number=3,supplier_price_authority_event_id=uuid(80),applicable_price_authority_sha256=e['applicable_price_authority']['sha256'])
                next(d for d in self.review['decisions'] if d['variant_id']==v)['calculation']['tier']=tier
        self.mapping['observations'].extend([{'candidate_id':uuid(90),'variant_id':'4003','offer_id':'900','action':'APPROVE_MAPPING','decision':{'mapping_decision_id':uuid(92),'preview_sha256':'8'*64},'selection':None},
                                            {'candidate_id':uuid(91),'variant_id':None,'offer_id':None,'action':'DEFER','decision':{'mapping_decision_id':uuid(93),'preview_sha256':'7'*64},'selection':None}])
        self.preparation['mapping_proof_sha256']=sha(raw(self.mapping))
        self.contexts=json.loads(json.dumps(self.contexts,default=str));self.frozen['contexts']=self.contexts

    def price_docs(self,docs):
        contexts={c['variant_id']:c for c in self.contexts};prices=[]
        for record in docs['recommendations-and-reasons.json']['items']:
            c=contexts[record['variant_id']];e=c['selected_offer_input_evidence']
            record['unit_cost']=e['initial_applicable_price_tier']['unit_price']
            record['metrics'].update(frozen_price_ladder=copy.deepcopy(c['prices']),selected_offer_input_evidence=copy.deepcopy(e),
                selected_offer_input_evidence_sha256=c['selected_offer_input_evidence_sha256'],selected_price_ladder_sha256=e['applicable_price_ladder']['sha256'])
            m=next(x for x in docs['supplier-mapping-evidence.json']['items'] if x['variant_id']==c['variant_id'])
            m.update(selected_offer_input_evidence=copy.deepcopy(e),selected_offer_input_evidence_sha256=c['selected_offer_input_evidence_sha256'],mapping_evidence=c['offer_evidence'])
            for row in c['prices']:prices.append(dict(zip(('offer_id','effective_month','level_type','break_qty','break_unit','case_price','unit_price','source_file','source_page'),row[1:],strict=True),price_state='current'))
        docs['frozen-price-economics.json']['items']=sorted(prices,key=lambda p:(p['offer_id'],p['level_type'],p['break_unit'] or '',p['break_qty'] or ''))
        self.change_price_docs(docs)

    def price_arguments(self):
        self.change_context()
        return raw(self.intent),raw(self.applied),raw(self.mapping),*self.arguments()


class HostedPacketAuthorityTests(unittest.TestCase):
    def setUp(self):self.f=PriceFixture()
    def run_fixture(self):return authority.validate_packet_price_evidence(*self.f.price_arguments(),commit=COMMIT,tree=TREE)
    def refuses(self):
        with self.assertRaises(HostedArtifactError):self.run_fixture()

    def test_actual_classifier_and_tier_predicates_reconcile_selected_packet(self):
        r=self.run_fixture();self.assertEqual((r['classified_contexts'],r['observed_selection_bindings'],r['price_snapshot_rows'],r['reviewed_final_tiers']),(6,5,6,3))
        self.assertFalse(r['execution_authority']);self.assertEqual(r['run_snapshot_authenticity'],'CONNECTED_DATABASE_PREREQUISITE')

    def test_registered_policy_book_and_source_row_pins_come_from_actual_parsers(self):
        self.assertEqual(selected._pg_jsonb_sha256(self.f.policy),authority.POLICY_SHA);self.assertEqual(self.f.book['content_sha256'],authority.BOOK_SHA)
        self.assertEqual({r['source_row_number']:selected._pg_jsonb_sha256(r['raw_payload']) for r in self.f.book['rows'][:2]},authority.ROW_SHAS)

    def test_original_apply_mapping_preparation_bytes_form_an_unbroken_chain(self):
        self.f.mapping['apply_proof_sha256']='f'*64;self.refuses()
        self.f=PriceFixture();self.f.preparation['mapping_proof_sha256']='f'*64;self.refuses()
        self.f=PriceFixture();args=list(self.f.price_arguments());args[1]+=b' '
        with self.assertRaises(HostedArtifactError):authority.validate_packet_price_evidence(*args,commit=COMMIT,tree=TREE)

    def test_intent_digest_source_status_backup_and_batch_are_bound(self):
        for key,value in [('commit','f'*40),('observed_status','VERIFIED_FUTURE'),('preview_sha256','f'*64),('backup_manifest_sha256','f'*64),('batch_id',uuid(99))]:
            self.f=PriceFixture();self.f.applied[key]=value;self.f.mapping['apply_proof_sha256']=sha(raw(self.f.applied));self.f.preparation['mapping_proof_sha256']=sha(raw(self.f.mapping));self.refuses()

    def test_wrong_policy_or_book_refuses_even_with_new_intent_hash(self):
        for key,value in [('policy_sha256','f'*64),('raw_content_sha256','f'*64),('policy_ref','foreign'),('expected_prior_head_version',True)]:
            self.f=PriceFixture();self.f.intent[key]=value;self.f.applied['preview_sha256']=sha(authority._compact(self.f.intent));self.f.mapping['apply_proof_sha256']=sha(raw(self.f.applied));self.f.preparation['mapping_proof_sha256']=sha(raw(self.f.mapping));self.refuses()

    def test_actual_classifier_rejects_rehashed_selection_ancestry_change(self):
        c=self.f.contexts[1];e=c['selected_offer_input_evidence'];e['selection_event']['expected_prior_head_version']=1
        c['selected_offer_input_evidence_sha256']=selected._compact_sha256(e);self.refuses()

    def test_observed_mapping_decision_and_selection_previews_are_exact(self):
        for key in ('mapping_decision_id','preview_sha256'):
            self.f=PriceFixture();self.f.mapping['observations'][0]['decision'][key]=uuid(99) if key.endswith('_id') else 'f'*64
            self.f.preparation['mapping_proof_sha256']=sha(raw(self.f.mapping));self.refuses()
        self.f=PriceFixture();self.f.mapping['observations'][1]['selection']['preview_sha256']='f'*64;self.f.preparation['mapping_proof_sha256']=sha(raw(self.f.mapping));self.refuses()

    def test_packet_selected_evidence_and_metric_hash_copies_cannot_diverge(self):
        for member,key,value in [('supplier-mapping-evidence.json','selected_offer_input_evidence_sha256','f'*64),('supplier-mapping-evidence.json','mapping_evidence',{})]:
            self.f=PriceFixture();self.f.change_price_docs=lambda d,m=member,k=key,v=value:d[m]['items'][0].__setitem__(k,v);self.refuses()
        self.f=PriceFixture();self.f.change_price_docs=lambda d:d['recommendations-and-reasons.json']['items'][0]['metrics'].__setitem__('frozen_price_ladder',[]);self.refuses()

    def test_price_packet_cannot_include_blocked_rows_or_drop_consumed_rows(self):
        self.f.change_price_docs=lambda d:d['frozen-price-economics.json']['items'].pop();self.refuses()
        self.f=PriceFixture();self.f.change_price_docs=lambda d:d['frozen-price-economics.json']['items'].append(copy.deepcopy(d['frozen-price-economics.json']['items'][0]));self.refuses()

    def test_price_packet_state_source_page_and_economics_are_exact(self):
        for key,value in [('price_state','future'),('source_page',99),('case_price','0'),('effective_month','2026-11-01'),('offer_id',999)]:
            self.f=PriceFixture();self.f.change_price_docs=lambda d,k=key,v=value:d['frozen-price-economics.json']['items'][0].__setitem__(k,v);self.refuses()

    def test_southern_final_tier_must_match_uploaded_break_row(self):
        self.f.review['decisions'][0]['calculation']['tier']['source_price_book_row_number']=2;self.refuses()
        self.f=PriceFixture();self.f.review['decisions'][0]['calculation']['tier']['supplier_price_authority_event_id']=uuid(99);self.refuses()

    def test_western_legacy_tier_cannot_gain_authority_or_choose_base_after_edit(self):
        self.f.review['decisions'][1]['calculation']['tier']['source_price_book_batch_id']=uuid(81);self.refuses()
        self.f=PriceFixture();self.f.review['decisions'][1]['calculation']['tier']['price_id']=self.f.contexts[1]['prices'][0][0];self.refuses()

    def test_bool_ids_and_duplicate_snapshots_refuse(self):
        self.f.review['decisions'][1]['calculation']['tier']['run_price_snapshot_id']=True;self.refuses()
        self.f=PriceFixture();self.f.review['decisions'][1]['calculation']['tier']['run_price_snapshot_id']=self.f.review['decisions'][0]['calculation']['tier']['run_price_snapshot_id'];self.refuses()

    def test_mapping_offer_identity_uses_actual_browser_decimal_string(self):
        for value in (201,True,'0201','201.0'):
            self.f=PriceFixture();self.f.mapping['observations'][0]['offer_id']=value
            self.f.preparation['mapping_proof_sha256']=sha(raw(self.f.mapping));self.refuses()

    def test_actual_jsonb_numeric_pack_metadata_is_parsed_without_float_economics(self):
        from hosted_synthetic_artifacts import bounded_json,packet_json
        args=self.f.price_arguments();self.run_fixture()
        e=self.f.contexts[0]['selected_offer_input_evidence'];self.assertIs(type(e['mapping_candidate']['shopify_units_value']),float)
        raw_manifest=json.dumps({'input_manifest':self.f.frozen}).encode()
        with self.assertRaises(HostedArtifactError):bounded_json(raw_manifest)
        self.assertEqual(packet_json('frozen-input-manifest.json',raw_manifest)['input_manifest'],self.f.frozen)
        self.f.change_price_docs=lambda d:d['frozen-price-economics.json']['items'][0].__setitem__('unit_price',6.0);self.refuses()

    def test_initial_baseline_tier_and_recommendation_unit_cost_are_recomputed(self):
        self.f.change_price_docs=lambda d:d['recommendations-and-reasons.json']['items'][0].__setitem__('unit_cost','6.0000')
        args=self.f.price_arguments();authority.validate_packet_forecast_evidence(*args[3:],commit=COMMIT,tree=TREE)
        with self.assertRaises(HostedArtifactError):authority.validate_packet_price_evidence(*args,commit=COMMIT,tree=TREE)
        self.f=PriceFixture();c=self.f.contexts[0];e=c['selected_offer_input_evidence'];row=c['prices'][0]
        e['initial_applicable_price_tier']=dict(zip(('price_id','offer_id','level_type','break_qty','break_unit','case_price','unit_price'),[row[0],row[1],*row[3:8]],strict=True),price_ladder_sha256=e['applicable_price_ladder']['sha256'])
        c['selected_offer_input_evidence_sha256']=selected._compact_sha256(e)
        self.assertEqual(selected.classify_frozen_manifest(json.dumps(self.f.frozen))[0],selected.CONTRACT)
        self.refuses()

    def test_mapping_fixture_proof_and_all_seven_outcomes_are_bound(self):
        self.f.mapping['fixture_proof_sha256']='f'*64;self.f.preparation['mapping_proof_sha256']=sha(raw(self.f.mapping));self.refuses()
        for index,key,value in [(5,'action','DEFER'),(6,'variant_id','4003'),(6,'offer_id','900'),(5,'selection',{})]:
            self.f=PriceFixture();self.f.mapping['observations'][index][key]=value
            self.f.preparation['mapping_proof_sha256']=sha(raw(self.f.mapping));self.refuses()
