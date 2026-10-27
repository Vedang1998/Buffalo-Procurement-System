"""Fabricated canonical records for pure selected-evidence component tests."""
import copy
import hashlib
from uuid import NAMESPACE_URL,uuid5
from procurement_os.persistent_mapping import _candidate_base
from procurement_os.synthetic_mapping_packet import load_synthetic_multivendor_mapping_packets
from procurement_os.synthetic_selected_offer import _pg_jsonb_sha256 as H,_REVIEWED_FACT_FIELDS,_DECISION_CANDIDATE_FIELDS


def seal(row,hash_key,omit):
    value=copy.deepcopy(row)
    value['canonical_payload']={k:copy.deepcopy(v) for k,v in value.items() if k not in set(omit)|{'canonical_payload',hash_key}}
    value[hash_key]=H(value['canonical_payload']);return value


def selected_records(context,*,at='2026-10-05T14:00:00+00:00',principal='owner:staging:01'):
    variant=context['variant_id'];offer=context['offer_id'];packets=load_synthetic_multivendor_mapping_packets()
    packet=next(p for p in packets if any(c.get('proposed_variant_id')==variant for c in p['candidates']))
    key=packet['intake_idempotency_key'];batch_id=uuid5(NAMESPACE_URL,f'buffalo-review-batch:{key}');candidate_records=[]
    for index,raw in enumerate(packet['candidates'],1):
        cid=uuid5(batch_id,raw['occurrence_key']);c=_candidate_base(review_batch_id=batch_id,candidate_id=cid,occurrence_index=index,value=raw)
        identity_fields=('proposed_vendor_id','source_vendor_identity','distributor_product_id_state','distributor_product_id_value','supplier_code_state','supplier_code_value')
        c['supplier_identity_key_sha256']=H({'contract_version':'SUPPLIER_IDENTITY_V1',**{k:c[k] for k in identity_fields}})
        operational_fields=('proposed_vendor_id','supplier_code_state','supplier_code_value','proposed_variant_id','offer_class','package_type_state','package_type_value','size_state','size_value',
            'raw_pack_state','raw_pack_value','physical_units_state','physical_units_value','retail_pack_units_state','retail_pack_units_value','shopify_units_state','shopify_units_value',
            'qualifying_units_state','qualifying_units_value','assortment_scope_state','assortment_scope_value','assortment_group_state','assortment_group_value','assortable_state','assortable_value',
            'identity_qualifiers','component_relationships')
        c['operational_offer_key_sha256']=H({'contract_version':'OPERATIONAL_OFFER_V1',**{k:c[k] for k in operational_fields}})
        c['decision_scope_sha256']=H({'contract_version':'CANDIDATE_DECISION_SCOPE_V1','review_batch_id':str(batch_id),'candidate_id':str(cid)})
        c.update(created_at=at,created_txid=1);candidate_records.append(seal(c,'candidate_sha256',('candidate_id','created_at','created_txid')))
    candidate=next(c for c in candidate_records if c['proposed_variant_id']==variant)
    batch={**copy.deepcopy(packet['package']),'review_batch_id':str(batch_id),'intake_idempotency_key':str(key),'source_package_kind':'SEALED_V5_REVIEW_PACKAGE','candidate_count':len(candidate_records),
        'candidate_set_sha256':hashlib.sha256(''.join(c['candidate_sha256']+'\n' for c in candidate_records).encode()).hexdigest(),
        'creator_principal_ref':principal,'creator_role_ref':'procurement.review.intake','creator_authn_context_sha256':H({'fixture':'intake-auth'}),'created_at':at,'created_txid':1}
    batch=seal(batch,'payload_sha256',('review_batch_id','intake_idempotency_key','created_at','created_txid'))
    fingerprints={name:H({'fixture_projection':name,'variant_id':variant,'offer_id':offer,'vendor_id':context['vendor_id']}) for name in ('offer_sha256','catalog_sha256','vendor_sha256','rejection_memory_sha256')}
    decision_key=uuid5(NAMESPACE_URL,'fixture-decision:'+variant);did=uuid5(NAMESPACE_URL,f'buffalo-mapping-decision:{decision_key}')
    d={k:copy.deepcopy(candidate[k]) for k in _DECISION_CANDIDATE_FIELDS}
    d.update(mapping_decision_id=str(did),decision_idempotency_key=str(decision_key),request_sha256=H({'fixture':'decision-request','variant':variant}),
        review_batch_id=str(batch_id),candidate_id=candidate['candidate_id'],decision_scope_sha256=candidate['decision_scope_sha256'],action='APPROVE_MAPPING',decision_origin='HUMAN',authority_kind='HUMAN_APPROVED',
        variant_id=variant,vendor_id=context['vendor_id'],result_offer_package_type='STANDARD',reviewed_facts={k:copy.deepcopy(candidate[k]) for k in _REVIEWED_FACT_FIELDS},
        owner_clarifications=copy.deepcopy(candidate['owner_clarifications']),historical_capture_scope=copy.deepcopy(candidate['historical_capture_scope']),
        expected_batch_payload_sha256=batch['payload_sha256'],expected_candidate_sha256=candidate['candidate_sha256'],expected_catalog_sha256=fingerprints['catalog_sha256'],
        expected_vendor_sha256=fingerprints['vendor_sha256'],expected_rejection_memory_sha256=fingerprints['rejection_memory_sha256'],
        evidence_set_sha256=H({'contract_version':'INDEPENDENT_LINKAGE_EVIDENCE_V1','printed_source_sha256':candidate['source_file_sha256'],'evidence':candidate['independent_linkage_evidence']}),
        human_principal_ref=principal,human_role_ref='procurement.mapping.approve',human_authn_context_sha256=H({'fixture':'decision-auth'}),preview_sha256=H({'fixture':'decision-preview','variant':variant}),
        confirmation_sha256=H({'fixture':'decision-confirmation','variant':variant}),service_principal_ref=None,policy_ref=None,policy_version=None,policy_publication_sha256=None,
        policy_predicate_version=None,policy_predicate_result_sha256=None,reason='Fabricated exact acceptance mapping for '+variant,result_offer_id=offer,offer_link_kind='LINKED_EXISTING',
        result_offer_contract_sha256=fingerprints['offer_sha256'],result_rejection_id=None,result_rejection_contract_sha256=None,supersedes_mapping_decision_id=None,decided_at=at,decided_txid=2)
    d=seal(d,'payload_sha256',('mapping_decision_id','decision_idempotency_key','decided_at','decided_txid'));fingerprints['mapping_decision_sha256']=d['payload_sha256']
    selection_key=uuid5(NAMESPACE_URL,'fixture-selection:'+variant);eid=uuid5(NAMESPACE_URL,f'buffalo-offer-selection:{selection_key}')
    event=dict(selection_event_id=str(eid),selection_idempotency_key=str(selection_key),variant_id=variant,selection_scope='ROUTINE_PROCUREMENT_STANDARD',action='SELECT',selected_offer_id=offer,mapping_decision_id=str(did),
        expected_prior_event_id=None,expected_prior_head_version=0,expected_mapping_decision_sha256=d['payload_sha256'],expected_offer_contract_sha256=fingerprints['offer_sha256'],
        expected_catalog_sha256=fingerprints['catalog_sha256'],expected_vendor_sha256=fingerprints['vendor_sha256'],expected_rejection_memory_sha256=fingerprints['rejection_memory_sha256'],
        effective_from='2026-10-05',effective_through=None,human_principal_ref=principal,human_role_ref='procurement.offer.select',human_authn_context_sha256=H({'fixture':'selection-auth'}),
        preview_sha256=H({'fixture':'selection-preview','variant':variant}),confirmation_sha256=H({'fixture':'selection-confirmation','variant':variant}),reason='Fabricated routine selection for '+variant,selected_at=at,selected_txid=3)
    event=seal(event,'payload_sha256',('selection_event_id','selection_idempotency_key','selected_at','selected_txid'))
    return dict(selection_head=dict(variant_id=variant,scope='ROUTINE_PROCUREMENT_STANDARD',head_version=1,selection_event_id=str(eid),updated_at=at,updated_txid=3),
                selection_event=event,mapping_decision=d,mapping_candidate=candidate,review_batch=batch,current_fingerprints=fingerprints)
