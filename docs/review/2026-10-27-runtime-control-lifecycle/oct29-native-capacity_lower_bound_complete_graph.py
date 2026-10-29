import json,pathlib,hashlib
D=pathlib.Path('/tmp/buffalo-native-packages-readonly');R=pathlib.Path('/workspace/Buffalo-Procurement-System')
T=pathlib.Path('/tmp/buffalo-memory-repair/6528427-native-target.json');P=R/'procurement/tools/hosted_native_inputs.json';A=D/'authenticated-target-files.json'
t=json.loads(T.read_text());p=json.loads(P.read_text());a=json.loads(A.read_text())
assert len(a['unresolved'])==0 and all(row['target_equal'] for row in a['files'])
known={row['path']:row for row in p['rows']};auth={row['path']:row for row in a['files']}
C=D/'selected-client-libpq-elf.json'; clients=json.loads(C.read_text())
for row in clients['files']:
 original=known[row['path']]
 assert original['sha256']==row['sha256'] and original['bytes']==row['bytes']
 original['elf']=row['elf']
by_soname={r['elf']['soname']:r['path'] for r in known.values() if r.get('elf',{}).get('soname')}
resolved=t['resolved_sonames'];observed_graph=t['elf_graph'];unresolved=[]

def closure(roots,extra=()):
 result={};stack=list(roots)+list(extra)
 while stack:
  name=stack.pop()
  if name in result:continue
  row=known.get(name) or auth.get(name)
  if row is None:
   unresolved.append(name);continue
  result[name]={'bytes':row['bytes'],'sha256':row['sha256'],'artifact_sha256':row['artifact_sha256']}
  elf=row.get('elf') or observed_graph.get(name)
  if elf:
   if elf.get('interpreter'):stack.append(elf['interpreter'])
   for soname in elf['needed']:
    target=by_soname.get(soname) or resolved.get(soname)
    if target is None:unresolved.append(soname)
    else:stack.append(target)
 return result
py=[r['path'] for r in p['rows'] if r['path'].startswith('/opt/')]
pg=[r['path'] for r in p['rows'] if not r['path'].startswith('/opt/')]
pgextras=p['required_unresolved_executables']+p['required_unresolved_locale_files']+p['required_unresolved_timezone_files']
diagnostic=closure(py);postgres=closure(pg,pgextras)
# read_native preserves each requested path. PT_INTERP aliases and SONAME paths
# may share physical inode and still consume separately named regular entries.
# The libpq logical SONAME is resolved to its publisher-authenticated physical
# object. Naming the same bytes differently does not imply an extra copy.
combined=dict(diagnostic)
for name,row in postgres.items():
 assert name not in combined or combined[name]==row
 combined[name]=row
out={'classification':'STATIC_AUTHENTICATED_FILE_UNION_LOWER_BOUND_NOT_RUNTIME_CAPTURE_MEMORY_OR_EXECUTION',
 'target_commit':t['commit'],'target_tree':t['tree'],'target_image':t['image_version'],
 'inputs':{'target_report_sha256':hashlib.sha256(T.read_bytes()).hexdigest(),'partial_manifest_sha256':hashlib.sha256(P.read_bytes()).hexdigest(),'authenticated_target_files_sha256':hashlib.sha256(A.read_bytes()).hexdigest(),'selected_client_libpq_elf_sha256':hashlib.sha256(C.read_bytes()).hexdigest()},
 'diagnostic':{'files':len(diagnostic),'regular_bytes_lower_bound':sum(x['bytes'] for x in diagnostic.values()),'limit':83886080,'files_detail':diagnostic},
 'postgres':{'files':len(postgres),'regular_bytes_lower_bound':sum(x['bytes'] for x in postgres.values()),'limit':83886080,'files_detail':postgres},
 'combined':{'files':len(combined),'regular_bytes_lower_bound':sum(x['bytes'] for x in combined.values()),'limit':134217728},
 'unresolved_graph_inputs':sorted(set(unresolved)),
 'omitted_positive_costs':['source and generated configuration/identity/sentinel entries','additional runtime alias/entry identities if current capture chooses them'],
 'assumptions':['All pinned Python selected files and approved Ubuntu PG selected files are retained without omission','External native file bytes are exact publisher-authenticated same-version target matches','PT_INTERP and resolved SONAME requested paths retain distinct names as current read_native/capture does','Static graph comes from selected publisher Python/PG ELF metadata plus source-bound target ELF metadata whose bytes match authenticated packages'],
 'authority':'No budget, dependency, manifest, accepted input, installation or runtime change authorized by this calculation'}
for name in ('diagnostic','postgres','combined'):
 out[name]['fits_lower_bound']=out[name]['regular_bytes_lower_bound']<=out[name]['limit']
(D/'capacity-lower-bound-complete-graph.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k in ('diagnostic','postgres','combined','unresolved_graph_inputs')},default=str)[:200])
for name in ('diagnostic','postgres','combined'):print(name,{k:v for k,v in out[name].items() if k!='files_detail'})
print('unresolved',out['unresolved_graph_inputs'])
