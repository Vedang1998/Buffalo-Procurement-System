from pathlib import Path
import base64,hashlib,json,zlib
v=json.loads(Path('/tmp/buffalo-v3/34e8f3a-ci-later.json').read_text());reports=[x['report'] for j in v['jobs'] for x in j['reports']];summary={'source_commit':v['commit'],'source_tree':'5d300467117ce3e577c72c92068a13970aa1826f','run':v['run'],'url':v['url']}
for r in reports:
 if r.get('encoding')=='GZIP_BASE64_V1':
  decoder=zlib.decompressobj(31);raw=decoder.decompress(base64.b64decode(r['payload'],validate=True),65537)
  assert decoder.eof and not decoder.unconsumed_tail and not decoder.unused_data and len(raw)==r['bytes']<=65536 and hashlib.sha256(raw).hexdigest()==r['sha256']
  value=json.loads(raw);assert (value['commit'],value['tree'])==(v['commit'],summary['source_tree'])
  Path('/tmp/buffalo-v3/34e8f3a-native-target.json').write_text(json.dumps(value,indent=2)+'\n')
  summary['native_observation']={'rows':len(value['rows']),'package_claims':len(value['package_claims']),'seconds':value['elapsed_seconds'],'unresolved':value['unresolved']}
 elif 'passed_methods' in r:summary['focused']=r
 elif 'capture_fit' in r:summary['capture']=r
 else:summary['ownership_preflight']=r
Path('/tmp/buffalo-v3/34e8f3a-target-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({'run':summary['run'],'focused':summary['focused'],'capture':{k:summary['capture'][k] for k in ('capture_fit','account_mutations','privileged_processes_started','owned_descriptors_closed','whole_service_memory','native_execution')},'native_observation':summary['native_observation']},sort_keys=True))
