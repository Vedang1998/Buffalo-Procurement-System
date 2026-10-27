"""Dormant packet owner-decision, RUN_ONLY exclusion and readiness checks.

These are frozen evidence checks, not a fresh database-readiness decision or
authority to generate/release a PO. Forecast/need verification remains separate.
"""
from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
import io
import json
import re

from hosted_synthetic_artifacts import packet_json
from hosted_synthetic_artifacts import HostedArtifactError, SAFETY_LABEL, _require, bounded_json, packet_members
from hosted_synthetic_packet_controls import validate_packet_controls, _by, _equal, _money, _captured_utc

REVIEW_HEADERS = (
    "safety_label","variant_id","vendor_name","action","approved_cases",
    "approved_loose_units","approved_units","approved_unit_cost",
    "approved_case_price","approved_merchandise_total","approved_loose_order_fee",
    "approved_line_total","comment","decided_by","resulting_inventory_units",
    "resulting_days_supply","days_supply_status",
)
GATES = frozenset({"CATALOG_SYNC","SALES_BACKFILL","INVENTORY_HISTORY","MAPPING_INTEGRITY",
                   "OPEN_PO_RECONCILIATION","PRICE_COVERAGE","VENDOR_RULES"})
_DECISIONS = {
    "1001": ("ACCEPT", "6", "36", "5", "30", "180", ""),
    "4001": ("EDIT_QUANTITY", "2", "8", "10.5", "42", "84", "Cross Western 18 BT multipack break"),
    "4002": ("ACCEPT", "1", "4", "4.5", "18", "18", ""),
    "4005": ("REJECT", "0", "0", "0", "", "0", "Allocated item remains excluded from internal DRAFT"),
}
_BLOCKERS = {"4003":"ROUTINE_SELECTED_OFFER_HEAD_REQUIRED","4004":"LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED"}


def _same_json(left,right):
    # Python equality aliases booleans and integers; JSON identity must not.
    return json.dumps(left,sort_keys=True,separators=(",",":"))==json.dumps(right,sort_keys=True,separators=(",",":"))


def _velocity(value):
    _require(type(value) is str and re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?",value))
    return Decimal(value)


def validate_packet_decision_evidence(preparation_json, review_json, artifacts, *, commit, tree):
    try:
        return _validate(preparation_json,review_json,artifacts,commit=commit,tree=tree)
    except (ValueError,KeyError,TypeError,UnicodeError,csv.Error,AttributeError) as exc:
        if isinstance(exc,HostedArtifactError):raise
        raise HostedArtifactError("hosted_packet_decision_evidence_refused") from None


def _validate(preparation_json,review_json,artifacts,*,commit,tree):
    # The actual application predicate is dependency-light and does no I/O.
    from procurement_os.readiness import readiness_gate_blockers
    identity=validate_packet_controls(preparation_json,review_json,artifacts,commit=commit,tree=tree)
    prep,review=bounded_json(preparation_json),bounded_json(review_json)
    packet=next(x for x in artifacts if x.registry.kind=='EMERGENCY_REVIEW_PACKET')
    entries=dict(packet_members(packet,(prep['vendors']['southern'],prep['vendors']['western'])))
    docs={n:packet_json(n,b) for n,b in entries.items() if n.endswith('.json')}
    contexts=_by(docs['frozen-input-manifest.json']['input_manifest']['contexts'],'variant_id',('1001','4001','4002','4003','4004','4005'))
    expected_recommendations=_by(prep['recommendations'],'variant_id',_DECISIONS)
    _require(len({v['recommendation_id'] for v in expected_recommendations.values()})==4)
    for item in expected_recommendations.values():_require(type(item['recommendation_id']) is str and re.fullmatch(r'[1-9][0-9]{0,17}',item['recommendation_id']))
    recommendations=_by(docs['recommendations-and-reasons.json']['items'],'variant_id',_DECISIONS)
    decisions=_by(review['decisions'],'variant_id',_DECISIONS)
    mappings=_by(docs['supplier-mapping-evidence.json']['items'],'variant_id',_DECISIONS)
    rows=list(csv.reader(io.StringIO(entries['human-review-decisions.csv'].decode('utf-8'),newline=''),strict=True))
    _require(len(rows)==5 and tuple(rows[0])==REVIEW_HEADERS and all(len(r)==len(REVIEW_HEADERS) for r in rows[1:]))
    reviewed_rows=_by([dict(zip(REVIEW_HEADERS,row,strict=True)) for row in rows[1:]],'variant_id',_DECISIONS)
    for variant,(action,cases,units,unit,case,merchandise,comment) in _DECISIONS.items():
        row=reviewed_rows[variant];context=contexts[variant]
        vendor=prep['vendors']['southern' if variant=='1001' else 'western']
        name='Synthetic Southern' if variant=='1001' else 'Synthetic Western Acceptance'
        _equal(row,{'safety_label':SAFETY_LABEL,'vendor_name':name,'action':action,'approved_cases':cases,
                    'approved_loose_units':'0','approved_units':units,'comment':comment,'decided_by':prep['principal_ref']})
        for key,value in [('approved_unit_cost',unit),('approved_merchandise_total',merchandise),('approved_loose_order_fee','0'),('approved_line_total',merchandise)]:
            _require(_money(row[key])==_money(value))
        if case:_require(_money(row['approved_case_price'])==_money(case))
        else:_require(row['approved_case_price']=='')
        available,incoming=_money(context['available_units']),_money(context['trusted_incoming_units'])
        _require(available==incoming==0)
        inventory=available+incoming+_money(units)
        _require(_money(row['resulting_inventory_units'])==inventory)
        velocity=_velocity(context['development_forecast_evidence']['forecast_daily_velocity'])
        if velocity:
            _require(_money(row['resulting_days_supply'])==(inventory/velocity).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                     and row['days_supply_status']=='CALCULATED_FROM_FROZEN_FORECAST')
        else:_require(row['resulting_days_supply']=='' and row['days_supply_status']=='UNDEFINED_ZERO_FORECAST')
        _equal(decisions[variant],{'action':action})
        recommendation=recommendations[variant]
        _require(type(context['offer_id']) is int and context['offer_id']>0 and type(recommendation['recommendation_id']) is int
                 and recommendation['recommendation_id']==int(expected_recommendations[variant]['recommendation_id']))
        _equal(recommendation,{'vendor_id':vendor,'offer_id':context['offer_id']})
        _equal(mappings[variant],{'vendor_id':vendor,'offer_id':context['offer_id'],'supplier_sku':context['supplier_sku'],
                                  'units_per_case':context['units_per_case'],'qualifying_units_per_case':context['qualifying_units_per_case']})
        if action!='REJECT':
            _require(type(decisions[variant]['calculation']['amount_cents']) is int and decisions[variant]['calculation']['amount_cents']==int(_money(merchandise)*100)
                     and type(decisions[variant]['calculation']['tier']) is dict and type(mappings[variant]['final_price_tier']) is dict
                     and _same_json(mappings[variant]['final_price_tier'],decisions[variant]['calculation']['tier']))
            tier=mappings[variant]['final_price_tier']
            _require(_money(tier['case_price'])==_money(case) and _money(tier['unit_price'])==_money(unit))
        else:_require('final_price_tier' not in mappings[variant])
    blockers=_by(prep['blockers'],'variant_id',_BLOCKERS)
    exclusions=_by(docs['blocked-item-exclusions.json']['items'],'variant_id',_BLOCKERS)
    summary_exclusions=_by(docs['packet-summary.json']['exceptions'],'variant_id',_BLOCKERS)
    _require(_same_json(exclusions,summary_exclusions) and len({x['exception_id'] for x in blockers.values()})==2)
    exclusion_ids=set()
    for variant,reason in _BLOCKERS.items():
        expected=blockers[variant];record=exclusions[variant];exclusion=record['run_only_exclusion']
        _require(type(expected['exception_id']) is str and re.fullmatch(r'[1-9][0-9]{0,17}',expected['exception_id'])
                 and type(record['exception_id']) is int and record['exception_id']==int(expected['exception_id']))
        _equal(record,{'type':'MONDAY_INPUT_BLOCKER','severity':'HIGH','message':reason,'status':'OPEN'})
        _equal(exclusion,{'action':'ACKNOWLEDGE_AND_EXCLUDE','scope':'RUN_ONLY','actor':prep['principal_ref'],
                          'reason':f'Acknowledged synthetic blocker for Variant {variant}; omit RUN_ONLY','input_fingerprint':prep['input_fingerprint']})
        _require(type(exclusion['exclusion_id']) is int and exclusion['exclusion_id']>0 and exclusion['exclusion_id'] not in exclusion_ids)
        exclusion_ids.add(exclusion['exclusion_id']);_captured_utc(exclusion['created_at'])
        _equal(exclusion['evidence'],{'original_exception_type':'MONDAY_INPUT_BLOCKER','original_severity':'HIGH','original_message':reason,
                 'original_vendor_id':contexts[variant].get('vendor_id'),'original_variant_id':variant,'action_effect':'EXCLUDED_FROM_THIS_RUN_ONLY','global_rules_changed':False})
        _require(contexts[variant]['blockers']==[reason])
    _require(docs['material-edit-confirmations.json']['items']==[])
    draft_bindings=_by(review['draft_bindings'],'vendor_id',prep['vendors'].values())
    readiness=_by(docs['draft-readiness-evidence.json']['items'],'vendor_id',prep['vendors'].values())
    readiness_count=0
    for vendor,item in readiness.items():
        _equal(item,{'draft_po_id':draft_bindings[vendor]['draft_po_id']})
        expected_variants=['1001'] if vendor==prep['vendors']['southern'] else ['4001','4002']
        lines=_by(item['readiness_by_variant'],'variant_id',expected_variants)
        for variant,line in lines.items():
            _equal(line,{'vendor_id':vendor,'run_id':prep['run_id'],'applicable_gate_names':sorted(GATES),'blockers':[]})
            gates=line['gates'];_require(type(gates) is list and 0<len(gates)<=256);keys=set()
            for gate in gates:
                _require(type(gate) is dict and set(gate)=={'gate_name','scope_type','scope_id','status','severity','blocks_po','evidence','message','checked_at'}
                         and type(gate.get('gate_name')) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',gate['gate_name'])
                         and gate.get('scope_type') in ('GLOBAL','VENDOR','VARIANT','RUN') and type(gate.get('scope_id')) is str and len(gate['scope_id'])<=128
                         and gate.get('status') in ('PASS','WARN','FAIL') and gate.get('severity') in ('INFO','LOW','MEDIUM','HIGH','CRITICAL')
                         and type(gate.get('blocks_po')) is bool and type(gate.get('evidence')) is dict
                         and (gate.get('message') is None or type(gate.get('message')) is str))
                key=(gate['gate_name'],gate['scope_type'],gate['scope_id']);_require(key not in keys);keys.add(key)
                _captured_utc(gate['checked_at'])
            _require(readiness_gate_blockers(gates,vendor_id=vendor,variant_id=variant,run_id=prep['run_id'],applicable_gate_names=GATES)==[])
            readiness_count+=1
    return {'contract':'BUFFALO_HOSTED_PACKET_REVIEW_READINESS_V1','commit':commit,'tree':tree,'run_id':prep['run_id'],
            'packet_sha256':packet.registry.sha256,'identity_economics':identity,'owner_decisions':len(reviewed_rows),
            'run_only_exclusions':len(exclusions),'readiness_lines':readiness_count,'forecast_need_validation':'REQUIRED_SEPARATE_GATE',
            'registry_authenticity':'CALLER_SAME_GENERATION_PREREQUISITE','scope':'PACKET_REVIEW_READINESS_COMPONENT_ONLY','execution_authority':False}
