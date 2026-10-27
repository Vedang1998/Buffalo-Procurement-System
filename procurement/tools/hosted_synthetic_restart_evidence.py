"""Dormant state and fresh-download parity checks across an owned lifecycle.

Consumes the existing Backup V2 state-facts schema, with bounded strict parsing.
It performs no lifecycle action or database authentication and grants no cleanup,
restore or execution authority. The caller must supply independent observations.
"""
from __future__ import annotations

import hashlib
import json
import re

from hosted_synthetic_artifacts import CapturedArtifact, HostedArtifactError, _require, bounded_json, _HASH, _UUID

COUNTS={'review_batches':3,'review_candidates':7,'mapping_decisions':7,'selection_events':5,'selection_heads':5,'runs':2,'purchase_orders':2,'artifacts':3}
HASH_LIST_COUNTS={'decision_payloads':7,'selection_payloads':5,'run_fingerprints':2,'artifact_hashes':3}
_NAME=re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
_OID=re.compile(r'[0-9a-f]{40}\Z')


def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def _state(raw):
    value=bounded_json(raw)
    _require(type(value) is dict and set(value)=={'facts','sha256'} and type(value.get('sha256')) is str and _HASH.fullmatch(value['sha256']))
    facts=value['facts'];_require(type(facts) is dict and set(facts)==set(COUNTS)|set(HASH_LIST_COUNTS)|{'relation_inventory','sequence_inventory'})
    for key,expected in COUNTS.items():_require(type(facts[key]) is int and facts[key]==expected)
    for key,count in HASH_LIST_COUNTS.items():
        _require(type(facts[key]) is str);parts=facts[key].split('|')
        _require(len(parts)==count and all(_HASH.fullmatch(p) for p in parts))
        if key in ('decision_payloads','selection_payloads'):_require(parts==sorted(parts))
    for key,kind,fields in [('relation_inventory','relation',{'relation','row_count','sha256'}),('sequence_inventory','sequence',{'sequence','last_value','is_called'})]:
        rows=facts[key];_require(type(rows) is list and 0<len(rows)<=4096);names=[]
        for row in rows:
            _require(type(row) is dict and set(row)==fields and type(row[kind]) is str and _NAME.fullmatch(row[kind]));names.append(row[kind])
            if kind=='relation':
                _require(type(row['row_count']) is int and 0<=row['row_count']<=2**63-1 and type(row['sha256']) is str and _HASH.fullmatch(row['sha256']))
            else:_require(type(row['last_value']) is int and -(2**63)<=row['last_value']<=2**63-1 and type(row['is_called']) is bool)
        _require(names==sorted(names) and len(names)==len(set(names)))
    _require(hashlib.sha256(_canonical(facts)).hexdigest()==value['sha256'])
    return value


def _artifacts(items,run_id):
    _require(type(items) is tuple and len(items)==3);result={};guids=set();vendors=set();packets=0
    for item in items:
        _require(type(item) is CapturedArtifact);item.validate();r=item.registry
        _require(r.run_id==run_id and r.artifact_id not in result and item.guid not in guids)
        guids.add(item.guid);result[r.artifact_id]=item
        if r.kind=='VENDOR_INTERNAL_CSV':_require(r.vendor_id not in vendors);vendors.add(r.vendor_id)
        else:packets+=1
    _require(len(vendors)==2 and packets==1)
    return result,guids


def compare_restart_evidence(before_state_json,after_state_json,before_artifacts,after_artifacts,*,commit,tree,run_id):
    """Compare immutable observed bytes; does not prove a restart occurred.

    Three fresh transfer GUIDs prevent accidental reuse of the same download
    record. They are not proof of target identity, browser activity or freshness.
    Complete catalog coverage and DB provenance remain outer attestation duties.
    """
    try:
        _require(type(commit) is str and _OID.fullmatch(commit) and type(tree) is str and _OID.fullmatch(tree)
                 and type(run_id) is str and _UUID.fullmatch(run_id))
        before,after=_state(before_state_json),_state(after_state_json)
        _require(_canonical(before)==_canonical(after))
        old,old_guids=_artifacts(before_artifacts,run_id);new,new_guids=_artifacts(after_artifacts,run_id)
        _require(not old_guids.intersection(new_guids) and set(old)==set(new))
        for identifier,item in old.items():
            _require(item.registry==new[identifier].registry and item.data==new[identifier].data)
        ordered=[old[k] for k in sorted(old,key=int)]
        _require(before['facts']['artifact_hashes']=='|'.join(x.registry.sha256 for x in ordered))
        return {'contract':'BUFFALO_HOSTED_RESTART_PARITY_V1','commit':commit,'tree':tree,'run_id':run_id,
                'state_sha256':before['sha256'],'relations_compared':len(before['facts']['relation_inventory']),
                'sequences_compared':len(before['facts']['sequence_inventory']),'artifacts_compared':3,
                'artifact_sha256':[x.registry.sha256 for x in ordered],'scope':'STATE_AND_ARTIFACT_PARITY_COMPONENT_ONLY',
                'observation_authenticity':'CALLER_SAME_GENERATION_PREREQUISITE','catalog_coverage':'CALLER_ATTESTATION_REQUIRED',
                'packet_semantics':'REQUIRED_SEPARATE_GATE','restart_execution':'NOT_PROVEN','restore_execution':'NOT_PROVEN',
                'resource_cleanup':'NOT_PROVEN','execution_authority':False}
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        if isinstance(exc,HostedArtifactError):raise
        raise HostedArtifactError('hosted_restart_parity_refused') from None
