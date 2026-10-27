"""Dormant canonical packet forecast/need recomputation, never PO authority."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from hosted_synthetic_artifacts import HostedArtifactError, _require, bounded_json, packet_members
from hosted_synthetic_packet_controls import _by, _equal, _money, _captured_utc
from hosted_synthetic_packet_evidence import validate_packet_decision_evidence, _same_json

VARIANTS=('1001','4001','4002','4003','4004','4005')
RECOMMENDED=('1001','4001','4002','4005')
V2='BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2'
SOUTH='00000000-0000-4000-8000-000000000001'
WEST='00000000-0000-4000-8000-000000000003'
# Cases/loose/physical units before owner edits; 4001's reviewed two cases are separate.
NEEDS={'1001':(6,0,36,34,2),'4001':(1,0,4,4,0),'4002':(1,0,4,4,0),
       '4004':(0,4,4,4,0),'4005':(0,0,0,0,0)}
PACKS={'1001':(6,6),'4001':(4,12),'4002':(4,4),'4004':(6,6),'4005':(4,4)}


def validate_packet_forecast_evidence(preparation_json,review_json,artifacts,*,commit,tree):
    try:return _validate(preparation_json,review_json,artifacts,commit=commit,tree=tree)
    except (ValueError,KeyError,TypeError,AttributeError,InvalidOperation) as exc:
        if isinstance(exc,HostedArtifactError):raise
        raise HostedArtifactError('hosted_packet_forecast_evidence_refused') from None


def _validate(preparation_json,review_json,artifacts,*,commit,tree):
    from procurement_os.development_forecast import validate_connected_development_forecast_context_evidence, validate_development_baseline_need_context
    prior=validate_packet_decision_evidence(preparation_json,review_json,artifacts,commit=commit,tree=tree)
    prep=bounded_json(preparation_json)
    _require(_same_json(prep['vendors'],{'southern':SOUTH,'western':WEST}))
    packet=next(x for x in artifacts if x.registry.kind=='EMERGENCY_REVIEW_PACKET')
    docs={n:bounded_json(b) for n,b in packet_members(packet,(SOUTH,WEST)) if n.endswith('.json')}
    frozen=docs['frozen-input-manifest.json']['input_manifest']
    evaluation=_captured_utc(frozen['evaluation_at']);_require(evaluation=='2026-10-05T14:00:00+00:00')
    contexts=_by(frozen['contexts'],'variant_id',VARIANTS)
    observed=_by(prep['forecasts'],'variant_id',RECOMMENDED)
    forecasts=docs['forecast-and-protection-evidence.json']
    _equal(forecasts,{'contract':V2,'commercial_authority':False,'production_activation':False})
    items=_by(forecasts['items'],'variant_id',VARIANTS)
    recommendations=_by(docs['recommendations-and-reasons.json']['items'],'variant_id',RECOMMENDED)
    ledgers=_by(docs['open-po-ledger-evidence.json']['items'],'variant_id',RECOMMENDED)
    calculated={}
    for variant,context in contexts.items():
        _equal(context,{'development_forecast_contract':V2})
        expected={'variant_id':variant,'status':context['development_forecast_status'],'blockers':context['blockers']}
        if variant=='4003':
            _equal(context,{'development_forecast_status':'NOT_REACHED','blockers':['ROUTINE_SELECTED_OFFER_HEAD_REQUIRED']})
            _require(not any(k in context for k in ('development_forecast_evidence','development_forecast_evidence_sha256','need','development_baseline_need_binding','forecast','demand_observations')))
            _require(_same_json(items[variant],expected));continue
        _equal(context,{'development_forecast_status':'READY','vendor_id':SOUTH if variant=='1001' else WEST,
                         'policy_mode':'ALLOCATED' if variant=='4005' else 'ROUTINE',
                         'units_per_case':PACKS[variant][0],'qualifying_units_per_case':PACKS[variant][1],
                         'blockers':['LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED'] if variant=='4004' else []})
        _require(_money(context['available_units'])==_money(context['trusted_incoming_units'])==0)
        rows=context['demand_observations'];_require(type(rows) is list and len(rows)==138)
        for i,row in enumerate(rows):
            _require(type(row) is dict and set(row)=={'business_date','net_units','inventory_state'})
            _equal(row,{'business_date':(date(2026,5,20)+timedelta(days=i)).isoformat(),'inventory_state':'UNKNOWN'})
            _require(_money(row['net_units'])==(0 if variant in ('4001','4002','4004') and i%3==2 else 2))
        west=variant!='1001';vendor=context['vendor_rules'];_require(type(vendor) is list and len(vendor)==22)
        for index,value in {0:'Synthetic Western Acceptance' if west else 'Synthetic Southern',1:True,2:2 if west else 14,3:1,5:'DOLLAR',8:west,13:['MONDAY','WEDNESDAY'] if west else ['MONDAY'],14:'17:00:00',15:'America/New_York',16:['THURSDAY']}.items():
            _require(_same_json(vendor[index],value))
        for index,value in {4:0,6:120 if west else 20,7:7 if west else 5}.items():_require(_money(vendor[index])==value)
        _require(_money(vendor[9])==3 if west else vendor[9] is None)
        position=context['open_po_position']
        _equal(position,{'variant_id':variant,'vendor_id':context['vendor_id'],'open_line_count':0,'blocks_reorder':False,'trusted_sources':[],'blockers':[]})
        _require(_money(position['trusted_incoming_units'])==0)
        e=context['development_forecast_evidence'];horizon=17 if not west else 3
        _equal(e,{'contract':V2,'method_version':'DEVELOPMENT_ROLLING_ORIGIN_V2','status':'READY','commercial_authority':False,'production_activation':False,
                  'history_start':'2026-05-20','history_end':'2026-10-04','horizon_days':horizon,'confidence':'LOW'})
        _require(validate_connected_development_forecast_context_evidence(e,rows,expected_contract=V2)
                 and validate_development_baseline_need_context(context,manifest_contract=V2))
        _require(_same_json(context['forecast'],{'evidence':e}))
        _require(_captured_utc(e['protection_calendar']['evaluation_at'])==evaluation)
        _equal(e['protection_calendar'],{'business_date':'2026-10-05','vendor_id':context['vendor_id'],'timezone_name':'America/New_York',
            'current_order_receipt_date':'2026-10-08','next_submission_date':'2026-10-07' if west else '2026-10-19',
            'next_order_receipt_date':'2026-10-08' if west else '2026-10-22'})
        point=34 if variant=='1001' else 6 if variant=='4005' else 4
        _require(_money(e['point_forecast_units'])==_money(e['target_units'])==point and _money(e['protection_units'])==0)
        need=context['need'];cases,loose,units,raw,rounding=NEEDS[variant]
        _equal(need,{'status':'ROUTINE_EXCLUDED' if variant=='4005' else 'READY_FOR_REVIEW','policy_mode':context['policy_mode'],
                     'protection_days':horizon,'cases':cases,'loose_units':loose,'ordered_units':units,'raw_need_units':raw,'pack_rounding_units':rounding})
        _require(_money(need['target_units'])==(0 if variant=='4005' else point) and _money(need['effective_inventory_units'])==0
                 and _money(need['loose_fee'])==(3 if variant=='4004' else 0))
        expected.update(evidence=e,evidence_sha256=context['development_forecast_evidence_sha256'],calculated_need=need,calculated_need_binding=context['development_baseline_need_binding'])
        _require(_same_json(items[variant],expected))
        if variant in RECOMMENDED:
            _equal(observed[variant],{'sha256':e['sha256']})
            rec=recommendations[variant];metrics=rec['metrics']
            _equal(rec,{'reason_code':'BASELINE_REPLENISHMENT'})
            # PostgreSQL NUMERIC columns are exported as Decimal strings.
            for key,value in {'recommended_cases':cases,'recommended_loose_units':loose,'recommended_units':units}.items():
                _require(_money(rec[key])==value)
            for key,value in {'development_forecast_contract':V2,'development_forecast_evidence':e,'development_forecast_evidence_sha256':e['sha256'],
                              'frozen_open_po_position':position,'frozen_inventory_capture':context['inventory_capture'],'frozen_inventory_rows':context['inventory_rows'],
                              'frozen_offer_evidence':context['offer_evidence'],'forecast_horizon_days':horizon,'forecast_method_version':e['method_version'],
                              'raw_need_units':raw,'pack_rounding_units':rounding,'need_reason_codes':need['reason_codes'],
                              'forecast_reason_codes':e['reason_codes'],'selected_model':e['selected_model'],'demand_regime':e['demand_regime'],
                              'xyz_class':e['xyz_class'],'abc_class':e['abc_class'],'in_stock_velocity':e['in_stock_velocity'],
                              'forecast_status':'DEVELOPMENT_CALCULATED','model_selection_status':'ROLLING_ORIGIN_FVA_CALCULATED',
                              'classification_status':'XYZ_CALCULATED_ABC_NOT_CONFIGURED','stockout_censoring_status':'EXPLICIT_CAUSAL_EVIDENCE_ONLY',
                              'safety_stock_status':'EMPIRICAL_FULL_HORIZON_PROTECTION'}.items():
                _require(_same_json(metrics[key],value))
            for key,value in {'available_units':context['available_units'],'trusted_incoming_units':context['trusted_incoming_units'],
                              'target_units':need['target_units'],'loose_fee':need['loose_fee'],'forecast_point_units':e['point_forecast_units'],
                              'safety_stock_units':e['protection_units'],'forecast_target_units':e['target_units'],'forecast_units':e['point_forecast_units']}.items():
                _require(_money(metrics[key])==_money(value))
            _require(type(metrics['forecast_daily_velocity']) is str and Decimal(metrics['forecast_daily_velocity'])==Decimal(e['forecast_daily_velocity']))
            _require(_same_json(ledgers[variant]['position'],position))
        calculated[variant]={'forecast_horizon_days':horizon,'baseline_cases':cases,'baseline_loose_units':loose,'baseline_units':units}
    return {'contract':'BUFFALO_HOSTED_PACKET_FORECAST_NEED_V1','commit':commit,'tree':tree,'run_id':prep['run_id'],
            'packet_sha256':packet.registry.sha256,'review_readiness':prior,'recomputed_contexts':5,'not_reached_contexts':1,'calculated':calculated,
            'scope':'PACKET_FORECAST_NEED_COMPONENT_ONLY','selected_price_authority':'REQUIRED_SEPARATE_GATE',
            'registry_authenticity':'CALLER_SAME_GENERATION_PREREQUISITE','execution_authority':False,'commercial_authority':False}
