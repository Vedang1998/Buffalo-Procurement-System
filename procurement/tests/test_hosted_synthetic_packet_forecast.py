"""Real pure forecast/need execution over packet components, not workflow output."""
import ast
import copy
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal
from functools import lru_cache
import json
import unittest

from test_hosted_synthetic_packet_evidence import EvidenceFixture,ROOT
from test_hosted_synthetic_packet_controls import COMMIT,TREE,SNAP
from hosted_synthetic_artifacts import HostedArtifactError
import hosted_synthetic_packet_forecast as forecast
from procurement_os.development_forecast import (
    V2_CONTRACT,build_development_baseline_need_binding,calculate_anchored_schedule_horizon,
    load_development_forecast_policy,plan_development_forecast,serialize_baseline_need,
)
from procurement_os.forecasting import DemandObservation
from procurement_os.replenishment import calculate_development_baseline_need


@lru_cache(maxsize=2)
def pure_context_bytes(evaluation_hour=14):
    # Reuse the existing source-shaped pure need-binding test recipe. No DB,
    # completed decisions or purchasing outcome is seeded into an application.
    module=ast.parse((ROOT/'procurement/tests/test_development_forecast.py').read_text())
    method=next(n for n in ast.walk(module) if isinstance(n,ast.FunctionDef) and n.name=='test_v2_confidence_and_need_binding_are_policy_and_context_bound')
    assignment=next(n for n in method.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='context' for t in n.targets))
    recipe=compile(ast.Module(body=[assignment],type_ignores=[]),'source-shaped-pure-need-recipe','exec')
    policy=load_development_forecast_policy(evidence_contract=V2_CONTRACT);contexts=[]
    controls=[('1001',6,6,'ROUTINE','SUP-001','750ML','6x750ML'),('4001',4,12,'ROUTINE','WEST-MULTI-4001','4x187ML','4x6x187ML'),
              ('4002',4,4,'ROUTINE','WEST-BTL-4002','750ML','4x750ML'),('4004',6,6,'ROUTINE','WEST-LOOSE-4004','375ML','6x375ML'),
              ('4005',4,4,'ALLOCATED','WEST-ALLOC-4005','750ML','4x750ML')]
    for index,(variant,pack,qualifiers,mode,sku,size,raw_pack) in enumerate(controls,1):
        west=variant!='1001';vendor_id=forecast.WEST if west else forecast.SOUTH;actor='synthetic:local-owner:01'
        vendor=['Synthetic Western Acceptance' if west else 'Synthetic Southern',True,2 if west else 14,1,Decimal('0'),'DOLLAR',
                Decimal('120.00' if west else '20.00'),Decimal('7.00' if west else '5.00'),west,Decimal('3.00') if west else None,
                'FABRICATED MULTIVENDOR ACCEPTANCE' if west else 'FABRICATED OWNER DEMO',actor,1,
                ['MONDAY','WEDNESDAY'] if west else ['MONDAY'],time(17),'America/New_York',['THURSDAY'],Decimal('1'),None,None,
                datetime(2026,9,1,tzinfo=timezone.utc),datetime(2026,9,1,tzinfo=timezone.utc)]
        calendar=calculate_anchored_schedule_horizon(evaluation_at=datetime(2026,10,5,evaluation_hour,tzinfo=timezone.utc),business_date=date(2026,10,5),vendor_id=vendor_id,
            vendor_rules=dict(timezone_name=vendor[15],order_days=vendor[13],order_cutoff_local=vendor[14],expected_delivery_days=vendor[16],
                              order_cycle_days=vendor[2],lead_time_days=vendor[3],lead_time_variability_days=vendor[4]))
        source=[DemandObservation(date(2026,5,20)+timedelta(days=i),Decimal('0' if variant in ('4001','4002','4004') and i%3==2 else '2'),'UNKNOWN') for i in range(138)]
        plan=plan_development_forecast(source,horizon_days=calendar['horizon_days'],protection_calendar=calendar,policy=policy)
        need=calculate_development_baseline_need(forecast_daily_velocity=plan.forecast_daily_velocity,point_forecast_units=plan.point_forecast_units,
            empirical_protection_units=plan.protection_units,forecast_horizon_days=plan.horizon_days,available_units='0',trusted_incoming_units='0',
            order_cycle_days=vendor[2],lead_time_days=vendor[3],lead_time_variability_days=vendor[4],policy_mode=mode,units_per_case=pack,
            loose_order_allowed=vendor[8],loose_unit_fee=vendor[9],open_po_blocked=False,protection_days_override=plan.horizon_days)
        ns=dict(V2_CONTRACT=V2_CONTRACT,plan=plan,source=source,Decimal=Decimal,date=date,datetime=datetime,timezone=timezone,vendor=vendor,need=need,serialize_baseline_need=serialize_baseline_need)
        exec(recipe,ns);c=ns['context'];offer_id=200+index
        c.update(variant_id=variant,vendor_id=vendor_id,offer_id=offer_id,units_per_case=pack,qualifying_units_per_case=qualifiers,
                 supplier_sku=sku,policy_mode=mode,policies=[[index,{'mode':mode},actor,'']],forecast={'evidence':plan.to_json_dict()})
        c['inventory_capture']=[SNAP,date(2026,10,5),datetime(2026,10,5,12,tzinfo=timezone.utc),'SYNTHETIC_DEMO','a'*64,1,1,0,0]
        c['inventory_rows'][0][0]='gid://shopify/Location/9001';c['inventory_rows'][0][4]=SNAP
        c['open_po_position'].update(variant_id=variant,vendor_id=vendor_id)
        c['offer_evidence'].update(supplier_sku=sku,size_text=size,raw_pack=raw_pack,shopify_units_per_case=Decimal(pack),qualifying_units_per_case=Decimal(qualifiers),assortment_scope='PRODUCT',valid_from=None,valid_to=None)
        c['selected_offer_input_evidence']={'selected_offer':{**copy.deepcopy(c['offer_evidence']),'offer_id':offer_id,'variant_id':variant,'vendor_id':vendor_id},
                                          'applicable_vendor_terms':{'vendor_id':vendor_id,'vendor_rules':copy.deepcopy(vendor)}}
        c['blockers']=['LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED'] if variant=='4004' else []
        c['development_baseline_need_binding']=build_development_baseline_need_binding(c,need)
        contexts.append(c)
    contexts.append({'variant_id':'4003','development_forecast_contract':V2_CONTRACT,'development_forecast_status':'NOT_REACHED','blockers':['ROUTINE_SELECTED_OFFER_HEAD_REQUIRED']})
    return json.dumps(sorted(contexts,key=lambda c:c['variant_id']),default=str).encode()


class ForecastFixture(EvidenceFixture):
    def __init__(self,evaluation_hour=14):
        super().__init__();self.contexts=json.loads(pure_context_bytes(evaluation_hour));self.frozen['contexts']=self.contexts
        self.frozen['evaluation_at']=datetime(2026,10,5,evaluation_hour,tzinfo=timezone.utc).isoformat()
        self.vendor_ids=(forecast.SOUTH,forecast.WEST);self.preparation['vendors']['southern']=forecast.SOUTH
        self.drafts[0]['vendor_id']=forecast.SOUTH;self.review['draft_bindings'][0]['vendor_id']=forecast.SOUTH
        self.preparation['forecasts']=[{'variant_id':c['variant_id'],'sha256':c['development_forecast_evidence_sha256']} for c in self.contexts if c['variant_id'] in forecast.RECOMMENDED]
        self.review_velocities={c['variant_id']:c['development_forecast_evidence']['forecast_daily_velocity'] for c in self.contexts if c['variant_id']!='4003'}
        self.change_docs=lambda docs:None;self.edit_evidence=self.complete

    def complete(self,docs):
        contexts={c['variant_id']:c for c in self.contexts};items=[]
        for c in self.contexts:
            item={'variant_id':c['variant_id'],'status':c['development_forecast_status'],'blockers':c['blockers']}
            if item['status']=='READY':item.update(evidence=c['development_forecast_evidence'],evidence_sha256=c['development_forecast_evidence_sha256'],calculated_need=c['need'],calculated_need_binding=c['development_baseline_need_binding'])
            items.append(item)
        docs['forecast-and-protection-evidence.json'].update(contract=V2_CONTRACT,commercial_authority=False,production_activation=False,items=items)
        ledger=[]
        for r in docs['recommendations-and-reasons.json']['items']:
            c=contexts[r['variant_id']];n=c['need'];e=c['development_forecast_evidence']
            r.update(recommended_cases=Decimal(n['cases']).quantize(Decimal('.0000')),recommended_loose_units=Decimal(n['loose_units']).quantize(Decimal('.0000')),
                     recommended_units=Decimal(n['ordered_units']).quantize(Decimal('.0000')),reason_code='BASELINE_REPLENISHMENT')
            r['metrics']={'development_forecast_contract':V2_CONTRACT,'development_forecast_evidence':copy.deepcopy(e),'development_forecast_evidence_sha256':e['sha256'],
                'frozen_open_po_position':c['open_po_position'],'frozen_inventory_capture':c['inventory_capture'],'frozen_inventory_rows':c['inventory_rows'],
                'frozen_offer_evidence':c['offer_evidence'],'forecast_horizon_days':e['horizon_days'],'forecast_method_version':e['method_version'],
                'raw_need_units':n['raw_need_units'],'pack_rounding_units':n['pack_rounding_units'],'need_reason_codes':n['reason_codes'],
                'available_units':c['available_units'],'trusted_incoming_units':c['trusted_incoming_units'],'target_units':n['target_units'],'loose_fee':n['loose_fee'],
                'forecast_reason_codes':e['reason_codes'],'selected_model':e['selected_model'],'demand_regime':e['demand_regime'],
                'xyz_class':e['xyz_class'],'abc_class':e['abc_class'],'in_stock_velocity':e['in_stock_velocity'],
                'forecast_status':'DEVELOPMENT_CALCULATED','model_selection_status':'ROLLING_ORIGIN_FVA_CALCULATED',
                'classification_status':'XYZ_CALCULATED_ABC_NOT_CONFIGURED','stockout_censoring_status':'EXPLICIT_CAUSAL_EVIDENCE_ONLY',
                'safety_stock_status':'EMPIRICAL_FULL_HORIZON_PROTECTION','forecast_units':e['point_forecast_units'],
                'forecast_daily_velocity':e['forecast_daily_velocity'],'forecast_point_units':e['point_forecast_units'],'safety_stock_units':e['protection_units'],'forecast_target_units':e['target_units']}
            ledger.append({'variant_id':c['variant_id'],'position':copy.deepcopy(c['open_po_position'])})
        docs['open-po-ledger-evidence.json']['items']=ledger
        self.change_docs(docs)


class HostedPacketForecastTests(unittest.TestCase):
    def setUp(self):self.f=ForecastFixture()
    def run_fixture(self):return forecast.validate_packet_forecast_evidence(*self.f.arguments(),commit=COMMIT,tree=TREE)
    def refuses(self):
        with self.assertRaises(HostedArtifactError):self.run_fixture()

    def test_actual_models_recompute_five_contexts_and_no_missing_head_forecast(self):
        r=self.run_fixture();self.assertEqual((r['recomputed_contexts'],r['not_reached_contexts']),(5,1));self.assertFalse(r['execution_authority'])
        self.assertEqual(r['calculated']['1001']['baseline_units'],36);self.assertEqual(r['calculated']['4001']['baseline_units'],4)
        self.assertEqual(r['calculated']['4004']['baseline_loose_units'],4);self.assertEqual(r['calculated']['4005']['baseline_units'],0)

    def test_replacement_forecast_member_passes_prior_gate_but_new_gate_refuses(self):
        self.f.change_docs=lambda d:d['forecast-and-protection-evidence.json'].__setitem__('items',[])
        args=self.f.arguments();forecast.validate_packet_decision_evidence(*args,commit=COMMIT,tree=TREE)
        with self.assertRaises(HostedArtifactError):forecast.validate_packet_forecast_evidence(*args,commit=COMMIT,tree=TREE)

    def test_preparation_hashes_cannot_be_substituted_or_duplicated(self):
        self.f.preparation['forecasts'][0]['sha256']='f'*64;self.refuses()
        self.f=ForecastFixture();self.f.preparation['forecasts'][1]=copy.deepcopy(self.f.preparation['forecasts'][0]);self.refuses()

    def test_packet_wrapper_cannot_claim_commercial_or_other_contract(self):
        for key,value in [('contract','V1'),('commercial_authority',True),('production_activation',True)]:
            self.f=ForecastFixture();self.f.change_docs=lambda d,k=key,v=value:d['forecast-and-protection-evidence.json'].__setitem__(k,v);self.refuses()

    def test_packet_evidence_need_binding_and_status_mirror_exact_frozen_context(self):
        for key,value in [('status','BLOCKED'),('evidence_sha256','f'*64),('calculated_need',{}),('calculated_need_binding',{}),('evidence',{})]:
            self.f=ForecastFixture();self.f.change_docs=lambda d,k=key,v=value:d['forecast-and-protection-evidence.json']['items'][0].__setitem__(k,v);self.refuses()

    def test_frozen_forecast_must_recompute_not_only_match_packet_copy(self):
        self.f.contexts[0]['development_forecast_evidence']['selected_model']='MADE_UP';self.refuses()

    def test_changed_observation_demand_date_or_stock_state_refuses(self):
        for key,value in [('net_units','3'),('business_date','2026-05-19'),('inventory_state','IN_STOCK')]:
            self.f=ForecastFixture();self.f.contexts[1]['demand_observations'][0][key]=value;self.refuses()

    def test_observation_count_and_order_are_exact(self):
        self.f.contexts[1]['demand_observations'].pop();self.refuses()
        self.f=ForecastFixture();self.f.contexts[1]['demand_observations'].reverse();self.refuses()

    def test_vendor_minima_fees_timezone_and_pack_sources_are_bound(self):
        for index,value in [(6,'0'),(7,'0'),(9,'0'),(15,'UTC'),(13,['MONDAY'])]:
            self.f=ForecastFixture();self.f.contexts[1]['vendor_rules'][index]=value;self.refuses()
        self.f=ForecastFixture();self.f.contexts[1]['qualifying_units_per_case']=4;self.refuses()

    def test_allocated_forecast_is_retained_but_need_stays_excluded(self):
        c=self.f.contexts[-1];self.assertEqual(c['development_forecast_evidence']['point_forecast_units'],'6.0000')
        c['need']['ordered_units']=4;self.refuses()
        self.f=ForecastFixture();self.f.contexts[-1]['policy_mode']='ROUTINE';self.refuses()

    def test_loose_fee_blocker_keeps_its_computed_need_and_fee(self):
        self.f.contexts[-2]['need']['loose_fee']='0';self.refuses()
        self.f=ForecastFixture();self.f.contexts[-2]['blockers']=[];self.refuses()

    def test_not_reached_context_cannot_gain_null_or_fabricated_calculation(self):
        for key in ('need','development_forecast_evidence','development_baseline_need_binding','demand_observations'):
            self.f=ForecastFixture();self.f.contexts[3][key]=None;self.refuses()

    def test_recommendation_baseline_is_not_owner_edited_quantity(self):
        self.f.change_docs=lambda d:d['recommendations-and-reasons.json']['items'][1].__setitem__('recommended_cases','2.0000');self.refuses()

    def test_recommendation_frozen_metrics_match_recomputed_sources(self):
        for key,value in [('forecast_horizon_days',17),('forecast_daily_velocity','9'),('raw_need_units',8),('frozen_inventory_rows',[]),('development_forecast_evidence_sha256','f'*64)]:
            self.f=ForecastFixture()
            if key=='xyz_class':value='Y' if self.f.contexts[1]['development_forecast_evidence']['xyz_class']=='X' else 'X'
            with self.subTest(key=key):
                self.f.change_docs=lambda d,k=key,v=value:d['recommendations-and-reasons.json']['items'][1]['metrics'].__setitem__(k,v);self.refuses()

    def test_open_po_packet_position_cannot_lose_or_gain_trusted_incoming(self):
        self.f.change_docs=lambda d:d['open-po-ledger-evidence.json']['items'][1]['position'].__setitem__('trusted_incoming_units','4');self.refuses()
        self.f=ForecastFixture();self.f.change_docs=lambda d:d['open-po-ledger-evidence.json'].__setitem__('items',[]);self.refuses()

    def test_forecast_duplicate_view_is_bound_to_full_model_evidence(self):
        self.f.contexts[0]['forecast']['evidence']['selected_model']='foreign';self.refuses()

    def test_packet_forecast_boolean_identity_cannot_alias_integer(self):
        self.f.change_docs=lambda d:d['forecast-and-protection-evidence.json']['items'][1]['calculated_need'].__setitem__('cases',True)
        self.refuses()

    def test_recommendation_postgres_numeric_columns_use_decimal_strings(self):
        args=self.f.arguments();forecast.validate_packet_forecast_evidence(*args,commit=COMMIT,tree=TREE)
        for value in (6,True,'6.5'):
            self.f=ForecastFixture();self.f.change_docs=lambda d,v=value:d['recommendations-and-reasons.json']['items'][0].__setitem__('recommended_cases',v);self.refuses()

    def test_manifest_evaluation_is_required_aware_and_matches_canonical_instant(self):
        for value in (None,'2026-10-05','2026-10-05T15:00:00+00:00'):
            self.f=ForecastFixture();self.f.frozen['evaluation_at']=value;self.refuses()
        self.f=ForecastFixture();self.f.frozen.pop('evaluation_at');self.refuses()
        self.f=ForecastFixture();self.f.frozen['evaluation_at']='2026-10-05T10:00:00-04:00';self.run_fixture()

    def test_self_consistent_replan_at_another_time_does_not_change_fixture_authority(self):
        from procurement_os.development_forecast import validate_development_baseline_need_context
        self.f=ForecastFixture(evaluation_hour=15)
        for c in self.f.contexts:
            if c['variant_id']!='4003':self.assertTrue(validate_development_baseline_need_context(c,manifest_contract=V2_CONTRACT))
        args=self.f.arguments();forecast.validate_packet_decision_evidence(*args,commit=COMMIT,tree=TREE)
        with self.assertRaises(HostedArtifactError):forecast.validate_packet_forecast_evidence(*args,commit=COMMIT,tree=TREE)

    def test_all_forecast_derived_metric_projections_retain_actual_model_results(self):
        for key,value in [('forecast_units','9'),('selected_model','MADE_UP'),('demand_regime','OTHER'),('xyz_class','OTHER_VALID_CLASS'),('abc_class','A'),
                          ('in_stock_velocity','2'),('forecast_reason_codes',[]),('forecast_status','PRODUCTION'),('safety_stock_status','NOT_CALCULATED')]:
            self.f=ForecastFixture()
            if key=='xyz_class':value='Y' if self.f.contexts[1]['development_forecast_evidence']['xyz_class']=='X' else 'X'
            with self.subTest(key=key):
                self.f.change_docs=lambda d,k=key,v=value:d['recommendations-and-reasons.json']['items'][1]['metrics'].__setitem__(k,v);self.refuses()

    def test_structurally_valid_rehashed_forecast_still_must_replan_from_observations(self):
        from procurement_os.development_forecast import validate_development_forecast_evidence,validate_connected_development_forecast_context_evidence
        from procurement_os.forecasting import canonical_evidence_sha256
        c=self.f.contexts[0];e=c['development_forecast_evidence']
        e.update(point_forecast_units='51.0000',forecast_daily_velocity='3.000000',target_units='51.0000')
        e['sha256']=canonical_evidence_sha256({k:v for k,v in e.items() if k!='sha256'})
        self.assertTrue(validate_development_forecast_evidence(e))
        self.assertFalse(validate_connected_development_forecast_context_evidence(e,c['demand_observations'],expected_contract=V2_CONTRACT))
        c['development_forecast_evidence_sha256']=e['sha256'];c['forecast']={'evidence':copy.deepcopy(e)}
        self.f.preparation['forecasts'][0]['sha256']=e['sha256'];self.f.review_velocities['1001']='3.000000'
        # The old packet gate accepts all refreshed copies and artifact hashes.
        # This gate must reject the model divergence independently of framing.
        args=self.f.arguments();forecast.validate_packet_decision_evidence(*args,commit=COMMIT,tree=TREE)
        with self.assertRaises(HostedArtifactError):forecast.validate_packet_forecast_evidence(*args,commit=COMMIT,tree=TREE)
