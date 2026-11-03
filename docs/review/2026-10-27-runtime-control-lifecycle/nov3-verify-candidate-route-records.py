from pathlib import Path
import copy,hashlib,json,os,sys,time
root=Path('/workspace/Buffalo-Procurement-System');out=Path('/tmp/buffalo-nov3-provenance');sys.path.insert(0,str(root/'procurement/tools'))
import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_route as route
candidate=json.loads((out/'replacement-metadata-candidate.json').read_text());index=candidate['candidate_index']
artifacts=copy.deepcopy([a for a in native.require_complete_inventory()['artifacts'].values() if a.get('suite')=='noble-updates'])
original=hashlib.sha256((root/'procurement/tools/hosted_native_proposed_inventory.json').read_bytes()).hexdigest()
by_key={(a['package'],a['version'],a['architecture']):a for a in candidate['tuples']}
for a in artifacts:
 row=by_key[a['package'],a['version'],a['architecture']]
 assert a['package_record_sha256']==row['candidate_package_record_sha256']
 # Only in-memory prospective metadata identity, not accepted source mutation.
 a['index_sha256']=index['sha256'];a['packages_index']=index
fds=set(os.listdir('/proc/self/fd'));start=time.monotonic();result=route.verify_index_records(out,index,artifacts,start+30)
assert result['matched_artifacts']==sorted(a['artifact_sha256'] for a in artifacts)
assert set(os.listdir('/proc/self/fd'))==fds
assert original==hashlib.sha256((root/'procurement/tools/hosted_native_proposed_inventory.json').read_bytes()).hexdigest()
result.update(scope='REAL_COMPLETE_INDEX_PARSER_WITH_PROSPECTIVE_IN_MEMORY_INDEX_PIN_ONLY; ACCEPTED_SOURCE_UNCHANGED',all47accepted_stanza_hashes_equal=True,descriptor_inventory_equal=True,seconds=time.monotonic()-start,accepted_inventory_sha256_unchanged=original,installation_authority=False)
(out/'candidate-route-record-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='matched_artifacts'},indent=2))
