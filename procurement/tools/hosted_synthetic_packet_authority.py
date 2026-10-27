"""Dormant selected-offer/price packet checks using the locked app predicates.

This application-dependent component makes no SQL or network calls. Its inputs
are retained caller observations, not authority to apply prices or release a PO.
"""
from __future__ import annotations

import hashlib
import json
import re

from hosted_synthetic_artifacts import packet_json
from hosted_synthetic_artifacts import HostedArtifactError,_require,bounded_json,packet_members,_HASH,_UUID
from hosted_synthetic_packet_controls import _by,_equal,_money,_captured_utc
from hosted_synthetic_packet_evidence import _same_json
from hosted_synthetic_packet_forecast import validate_packet_forecast_evidence,VARIANTS,RECOMMENDED,SOUTH,WEST

POLICY_SHA='060aec03cb652734b1374b8375f60d5eb8666bb4613e721a4ca12fcce04193a4'
BOOK_SHA='00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c'
ROW_SHAS={2:'466f4388e8883a05130fc5ca9e702c567af376abb2cb57c0813dde4fb650f8e2',3:'1c77981ff4122df5b4ca43f7b31aeba96a40088dc2653ae6d73433f93574a60a'}
LADDERS={'1001':[('BASE',None,None,'36','6',1),('BREAK','2','CS','30','5',2)],
         '4001':[('BASE',None,None,'48','12',1),('BREAK','18','BT','42','10.5',2)],
         '4002':[('BASE',None,None,'18','4.5',3)],'4004':[('BASE',None,None,'24','4',7)],'4005':[('BASE',None,None,'20','5',9)]}
INTENT_FIELDS=frozenset(('contract','price_book_batch_id','vendor_id','price_scope_key','apply_idempotency_key','prior_event_id','expected_prior_head_version',
 'expected_current_scope_sha256','resulting_current_scope_sha256','scope_membership_sha256','raw_content_sha256','declaration_sha256','policy_ref','policy_sha256',
 'confirmation_payload_sha256','price_book_promotion_event_id','expected_current_row_count','resulting_current_row_count','unaffected_price_state_sha256',
 'backup_manifest_sha256','backup_prechange_scope_sha256','application_at','variant_ids','principal_ref','role_ref','commercial_authority','real_price_approval',
 'staging_backup_release_sha256','staging_runtime_attestation_identity'))


def _compact(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _intent(raw):
    value=bounded_json(raw);_require(type(value) is dict and set(value)==INTENT_FIELDS)
    for item in value.values():
        if type(item) is str:_require(re.fullmatch(r'[\x20-\x7e]{1,256}',item))
        elif type(item) is int:_require(0<=item<=2**53-1)
        elif type(item) is bool:pass
        else:_require(type(item) is list and 0<len(item)<=100 and all(type(x) is str and re.fullmatch(r'[1-9][0-9]{0,19}',x) for x in item))
    return value


def validate_packet_price_evidence(intent_json,apply_json,mapping_json,preparation_json,review_json,artifacts,*,commit,tree):
    try:return _validate(intent_json,apply_json,mapping_json,preparation_json,review_json,artifacts,commit=commit,tree=tree)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        if isinstance(exc,HostedArtifactError):raise
        raise HostedArtifactError('hosted_packet_price_evidence_refused') from None


def _validate(intent_json,apply_json,mapping_json,preparation_json,review_json,artifacts,*,commit,tree):
    from procurement_os.recommendations import _price_tiers_from_rows
    from procurement_os.strategic import evaluate_price_tiers
    from procurement_os.synthetic_selected_offer import classify_frozen_manifest,_compact_sha256,_valid_applicable_price_authority,final_price_tier_matches_authority
    prior=validate_packet_forecast_evidence(preparation_json,review_json,artifacts,commit=commit,tree=tree)
    intent=_intent(intent_json);applied,mapping,prep=map(bounded_json,(apply_json,mapping_json,preparation_json))
    _equal(applied,{'contract':'BUFFALO_STAGING_APPLY_STEP_OBSERVATION_V1','commit':commit,'tree':tree,'observed_status':'APPLIED_CURRENT',
                    'scope':'APPLY_STEP_ONLY','commercial_authority':False,'execution_authority':False,'preview_sha256':_sha(_compact(intent))})
    _equal(mapping,{'contract':'BUFFALO_STAGING_MAPPING_STEP_OBSERVATION_V1','commit':commit,'tree':tree,'apply_proof_sha256':_sha(apply_json),
                    'scope':'MAPPING_SELECTION_STEP_ONLY','commercial_authority':False,'execution_authority':False})
    _equal(mapping,{'fixture_proof_sha256':prep['fixture_proof_sha256']})
    _require(type(prep['fixture_proof_sha256']) is str and _HASH.fullmatch(prep['fixture_proof_sha256']))
    _equal(prep,{'mapping_proof_sha256':_sha(mapping_json),'price_batch_id':applied['batch_id']})
    _equal(intent,{'contract':'BUFFALO_SYNTHETIC_PRICE_APPLY_PREVIEW_V1','role_ref':'procurement.price.approve','price_book_batch_id':applied['batch_id'],'vendor_id':SOUTH,'price_scope_key':'COMPLETE_VENDOR','policy_ref':'synthetic-southern-monthly-complete-v1',
                   'policy_sha256':POLICY_SHA,'raw_content_sha256':BOOK_SHA,'commercial_authority':False,'real_price_approval':False,'principal_ref':prep['principal_ref'],
                   'expected_prior_head_version':1,'expected_current_row_count':4,'resulting_current_row_count':4,'variant_ids':['1001'],
                   'backup_manifest_sha256':applied['backup_manifest_sha256']})
    _require(type(applied['batch_id']) is str and _UUID.fullmatch(applied['batch_id']) and _captured_utc(intent['application_at'])=='2026-10-01T14:00:00+00:00')
    for key in ('operator_proof_sha256','confirmation_proof_sha256','backup_manifest_sha256'):_require(type(applied[key]) is str and _HASH.fullmatch(applied[key]))
    for key,value in intent.items():
        if key.endswith('_sha256'):_require(type(value) is str and _HASH.fullmatch(value))
    _require(type(intent['prior_event_id']) is str and _UUID.fullmatch(intent['prior_event_id']) and type(intent['apply_idempotency_key']) is str
             and re.fullmatch(r'[A-Za-z0-9_-]{1,100}',intent['apply_idempotency_key']) and type(intent['staging_runtime_attestation_identity']) is str and _HASH.fullmatch(intent['staging_runtime_attestation_identity']))
    observations=mapping['observations'];_require(type(observations) is list and len(observations)==7)
    _require(all(type(o) is dict and type(o.get('candidate_id')) is str and _UUID.fullmatch(o['candidate_id']) for o in observations))
    _require(len({o['candidate_id'] for o in observations})==7)
    _by([o for o in observations if o.get('variant_id') is not None],'variant_id',VARIANTS)
    unresolved=[o for o in observations if o.get('variant_id') is None];_require(len(unresolved)==1)
    for o in observations:
        _require(set(o)=={'candidate_id','variant_id','offer_id','action','decision','selection'})
        d=o['decision'];_require(type(d) is dict and type(d.get('mapping_decision_id')) is str and _UUID.fullmatch(d['mapping_decision_id'])
                               and type(d.get('preview_sha256')) is str and _HASH.fullmatch(d['preview_sha256']))
        if o['variant_id'] is None:_require(o['offer_id'] is None and o['action']=='DEFER' and o['selection'] is None)
        else:
            _require(type(o['offer_id']) is str and re.fullmatch(r'[1-9][0-9]{0,17}',o['offer_id']) and o['action']=='APPROVE_MAPPING')
            _require(o['selection'] is None if o['variant_id']=='4003' else type(o['selection']) is dict)

    _equal(mapping['controls'],{'review_batch_count':3,'candidate_count':7,'decision_count':7,'selection_event_count':5,'selection_head_count':5})
    packet=next(x for x in artifacts if x.registry.kind=='EMERGENCY_REVIEW_PACKET')
    docs={n:packet_json(n,b) for n,b in packet_members(packet,(SOUTH,WEST)) if n.endswith('.json')}
    manifest=docs['frozen-input-manifest.json']['input_manifest']
    _require(classify_frozen_manifest(json.dumps(manifest))==('SYNTHETIC_CONFIRMED_SELECTION_V1',VARIANTS))
    contexts=_by(manifest['contexts'],'variant_id',VARIANTS);recs=_by(docs['recommendations-and-reasons.json']['items'],'variant_id',RECOMMENDED)
    mappings=_by(docs['supplier-mapping-evidence.json']['items'],'variant_id',RECOMMENDED)
    projected=[];used_price_ids=set();used_observations=set();snapshot_ids=set()
    for variant,c in contexts.items():
        e=c['selected_offer_input_evidence'];digest=c['selected_offer_input_evidence_sha256']
        _require(digest==_compact_sha256(e))
        if variant=='4003':continue
        offer=e['selected_offer'];_equal(offer,{'offer_id':c['offer_id'],'variant_id':variant,'vendor_id':c['vendor_id'],'supplier_sku':c['supplier_sku']})
        for record,key,minimum in [(e['selection_head'],'head_version',1),(e['selection_event'],'expected_prior_head_version',0),
                                   (e['selection_event'],'selected_txid',1),(e['mapping_decision'],'decided_txid',1),(e['mapping_candidate'],'occurrence_index',1)]:
            _require(type(record[key]) is int and record[key]>=minimum)
        _equal(e['mapping_decision'],{'human_principal_ref':prep['principal_ref'],'human_role_ref':'procurement.mapping.approve'})
        _equal(e['selection_event'],{'human_principal_ref':prep['principal_ref'],'human_role_ref':'procurement.offer.select'})
        matches=[o for o in observations if o.get('variant_id')==variant and type(o.get('offer_id')) is str and re.fullmatch(r'[1-9][0-9]{0,17}',o['offer_id']) and int(o['offer_id'])==c['offer_id'] and o.get('selection') is not None]
        _require(len(matches)==1);observed=matches[0];_require(observed['candidate_id'] not in used_observations);used_observations.add(observed['candidate_id'])
        _equal(observed,{'candidate_id':e['mapping_candidate']['candidate_id'],'action':'APPROVE_MAPPING'})
        _equal(observed['decision'],{'mapping_decision_id':e['mapping_decision']['mapping_decision_id'],'preview_sha256':e['mapping_decision']['preview_sha256']})
        _equal(observed['selection'],{'mapping_decision_id':e['mapping_decision']['mapping_decision_id'],'preview_sha256':e['selection_event']['preview_sha256']})
        ladder=e['applicable_price_ladder'];rows=ladder['rows']
        _require(_same_json(rows,c['prices']) and ladder['sha256']==_compact_sha256(rows) and type(rows) is list and len(rows)==len(LADDERS[variant]))
        for row,(level,quantity,unit,case,price,page) in zip(rows,LADDERS[variant],strict=True):
            _require(type(row) is list and len(row)==10 and type(row[0]) is int and row[0]>0 and row[0] not in used_price_ids and type(row[1]) is int and row[1]==c['offer_id'])
            used_price_ids.add(row[0]);_require(row[2:4]==['2026-10-01',level] and row[5]==unit and _money(row[6])==_money(case) and _money(row[7])==_money(price))
            _require(row[4] is None if quantity is None else _money(row[4])==_money(quantity))
            _require(type(row[9]) is int and row[9]==page and row[8]==('synthetic-october-replacement.csv' if variant=='1001' else 'synthetic-multivendor-v2.csv'))
            if variant in RECOMMENDED:projected.append(dict(zip(('offer_id','effective_month','level_type','break_qty','break_unit','case_price','unit_price','source_file','source_page'),row[1:],strict=True),price_state='current'))
        if variant=='1001':
            a=e['applicable_price_authority'];_require(_valid_applicable_price_authority(a,ladder,offer))
            _equal(a,{'policy_ref':intent['policy_ref'],'policy_sha256':intent['policy_sha256']})
            _equal(a['head'],{'head_version':2,'active_price_book_batch_id':intent['price_book_batch_id'],'current_scope_sha256':intent['resulting_current_scope_sha256']})
            _equal(a['apply_event'],{'price_book_batch_id':intent['price_book_batch_id'],'raw_content_sha256':intent['raw_content_sha256'],
                                    'scope_membership_sha256':intent['scope_membership_sha256'],'resulting_current_scope_sha256':intent['resulting_current_scope_sha256']})
            _require(type(a['head']['event_id']) is str and _UUID.fullmatch(a['head']['event_id']) and _captured_utc(a['apply_event']['recorded_at'])==_captured_utc(intent['application_at']))
            _equal(a['price_confirmation'],{'promotion_event_id':intent['price_book_promotion_event_id'],'confirmation_payload_sha256':intent['confirmation_payload_sha256']})
            _require(type(a['price_confirmation']['promotion_event_id']) is int and a['price_confirmation']['promotion_event_id']>0)
            for binding,row,number in zip(a['prices'],rows,(2,3),strict=True):
                _equal(binding,{'price_id':row[0],'source_row_number':number,'source_row_sha256':ROW_SHAS[number]})
        else:_require('applicable_price_authority' not in e)
        if variant not in RECOMMENDED:continue
        baseline=rows[1] if variant=='1001' else rows[0]
        initial=e['initial_applicable_price_tier']
        _equal(initial,{'price_id':baseline[0],'offer_id':baseline[1],'level_type':baseline[3],'break_qty':baseline[4],
                        'break_unit':baseline[5],'case_price':baseline[6],'unit_price':baseline[7],'price_ladder_sha256':ladder['sha256']})
        need=c['need']
        recomputed=evaluate_price_tiers(_price_tiers_from_rows(rows),baseline_cases=need['cases'],baseline_loose_units=need['loose_units'],
            sellable_units_per_case=c['units_per_case'],qualifying_units_per_case=c['qualifying_units_per_case'],loose_order_allowed=c['vendor_rules'][8],loose_units_qualify=False)
        _require(_money(recs[variant]['unit_cost'])==_money(baseline[7])==recomputed.selected_unit_cost)
        m=mappings[variant];metrics=recs[variant]['metrics']
        for source in (m,metrics):
            _require(_same_json(source['selected_offer_input_evidence'],e) and source['selected_offer_input_evidence_sha256']==digest)
        _require(_same_json(m['mapping_evidence'],c['offer_evidence']) and _same_json(metrics['frozen_price_ladder'],rows) and metrics['selected_price_ladder_sha256']==ladder['sha256'])
        if variant=='4005':_require('final_price_tier' not in m);continue
        tier=m['final_price_tier'];_require(type(tier['price_id']) is int and type(tier['run_price_snapshot_id']) is int and tier['run_price_snapshot_id']>0
            and tier['run_price_snapshot_id'] not in snapshot_ids);snapshot_ids.add(tier['run_price_snapshot_id'])
        selected=rows[1] if variant in ('1001','4001') else rows[0]
        _equal(tier,{'price_id':selected[0],'level_type':selected[3],'break_unit':selected[5],'price_ladder_sha256':ladder['sha256']})
        _require(tier['break_qty'] is None if selected[4] is None else _money(tier['break_qty'])==_money(selected[4]))
        _require(_money(tier['case_price'])==_money(selected[6]) and _money(tier['unit_price'])==_money(selected[7]) and final_price_tier_matches_authority(e,tier))
        if variant=='1001':_require(type(tier['source_price_book_row_number']) is int and tier['source_price_book_row_number']==3)
    prices=docs['frozen-price-economics.json']['items']
    _require(type(prices) is list and len(prices)==6 and _same_json(prices,sorted(projected,key=lambda p:(p['offer_id'],p['level_type'],p['break_unit'] or '',p['break_qty'] or ''))))
    _require(len(used_observations)==5)
    return {'contract':'BUFFALO_HOSTED_PACKET_SELECTED_PRICE_V1','commit':commit,'tree':tree,'run_id':prep['run_id'],'packet_sha256':packet.registry.sha256,
            'forecast_need':prior,'classified_contexts':6,'observed_selection_bindings':5,'price_snapshot_rows':6,'reviewed_final_tiers':3,
            'scope':'PACKET_SELECTED_PRICE_COMPONENT_ONLY','whole_vendor_scope_authenticity':'CONNECTED_DATABASE_PREREQUISITE',
            'apply_event_authenticity':'CONNECTED_DATABASE_PREREQUISITE','run_snapshot_authenticity':'CONNECTED_DATABASE_PREREQUISITE',
            'registry_authenticity':'CALLER_SAME_GENERATION_PREREQUISITE','execution_authority':False,'commercial_authority':False}
