"""Read-only inventory-derived bytes/counts; never capture, seal, memory or execution proof.

Git blobs bind the requested published/frozen source. Publisher-authenticated
native row sizes/hashes bind required payload identities. Fixed-length hash values
stand in ONLY for unavailable input-bundle hash fields when calculating JSON byte
length; no invented digest is reported as an actual bundle identity.
"""
import ast,hashlib,json,pathlib,re,subprocess,sys,types
ROOT=pathlib.Path('/workspace/Buffalo-Procurement-System')
COMMIT=sys.argv[1] if len(sys.argv)>1 else '5b8dc5425cbb5d087c79beab4d6c2e467b29a92b'
assert len(COMMIT)==40 and all(c in '0123456789abcdef' for c in COMMIT)
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
def blob(path):return git('show',COMMIT+':'+path)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def literal(tree,names,initial=None):
 values={} if initial is None else dict(initial)
 for node in tree.body:
  if not isinstance(node,ast.Assign):continue
  for target in node.targets:
   if isinstance(target,ast.Name) and target.id in names:
    values[target.id]=eval(compile(ast.Expression(node.value),'<fixed-source-constant>','eval'),{'__builtins__':{},'tuple':tuple,**values})
 return values
rt_tree=ast.parse(blob('procurement/tools/hosted_synthetic_runtime.py'))
pg_tree=ast.parse(blob('procurement/tools/hosted_synthetic_postgres_runtime.py'))
src_tree=ast.parse(blob('procurement/tools/hosted_synthetic_source.py'))
rt=literal(rt_tree,{'POLICY','APPLICATION_POLICY','POSTGRES_POLICY','POSTGRES_PHASE_POLICY','POSTGRES_PHASE_CHANGE_CONTROL','PROJECTED_ROOT','PROJECTED_TOOLS','SENTINEL','MAX_BYTES','POSTGRES_PHASE_MAX_BYTES','MAX_ENTRIES','_IDENTITY_PATH'})
pg=literal(pg_tree,{'BIN','LIB','SHARE','ZONE','EXECUTABLES','_LOCALE','_FIXED','_CONTROLS'})
src=literal(src_tree,{'WORKFLOW','SOURCE_PATHS'})
manifest=json.loads(blob('procurement/tools/hosted_native_proposed_inventory.json'))
rows={r['path']:r for r in manifest['rows']}
layout_changes={}
native_tree=ast.parse(blob('procurement/tools/hosted_synthetic_native_inputs.py'))
layout_functions=[n for n in native_tree.body if isinstance(n,ast.FunctionDef) and n.name=='execution_inventory']
if layout_functions:
 def layout_require(value,*args):assert value,args
 layout_globals={'Path':pathlib.Path,'re':re,'require':layout_require,'_proposed_inventory':lambda:manifest}
 exec(compile(ast.Module(body=layout_functions,type_ignores=[]),'<fixed-source-layout-function>','exec'),layout_globals)
 layout=layout_globals['execution_inventory']();rows=layout['rows'];layout_changes=layout['renamed_logical_files']
assert len(rows)==manifest['known_file_count']==831 and sum(r['bytes'] for r in rows.values())==149540145
native_graph=json.loads(blob('docs/review/2026-10-27-runtime-control-lifecycle/oct29-native-capacity-logical-with-explicit-nss.json'))
selected_server=json.loads(blob('docs/review/2026-10-27-runtime-control-lifecycle/native-postgres-server-inventory.json'))
# The source-derived metadata assignment is evaluated with only fixed static
# inventory data. It is the same canonical field shape used by _capture.
capture=next(n for n in pg_tree.body if isinstance(n,ast.FunctionDef) and n.name=='_capture')
metadata_assign=next(n for n in capture.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='metadata' for t in n.targets))
all_source={path:sha(blob(path)) for path in src['SOURCE_PATHS']}
metadata_globals={**pg,'hashlib':hashlib,'list':list,'dict':dict,'sorted':sorted,'binding':types.SimpleNamespace(files=tuple(all_source.items())),
 'modules':set(selected_server['modules']),'stops':tuple(selected_server['stopwords']),'zones':tuple(selected_server['timezones_requiring_external_tzdata'])}
metadata=eval(compile(ast.Expression(metadata_assign.value),'<fixed-pg-metadata>','eval'),{'__builtins__':{},**metadata_globals})
def require(condition):assert condition
identity_globals={**rt,'json':json,'ns':types.SimpleNamespace(_require=require)}
functions=[n for n in rt_tree.body if isinstance(n,ast.FunctionDef) and n.name in ('_identity_bytes','_phase_capacity_identity')]
exec(compile(ast.Module(body=functions,type_ignores=[]),'<fixed-source-identity-functions>','exec'),identity_globals)
identity_bytes=identity_globals['_identity_bytes']
TREE=git('rev-parse',COMMIT+'^{tree}').decode().strip()
GENERATION='0'*64 # length placeholder only, not actual execution generation
PYTHON='/opt/hostedtoolcache/Python/3.13.5/x64/bin/python3.13'
fixed={path:{'bytes':len(raw),'sha256':sha(raw)} for path,raw in pg['_FIXED'].items()}
SENTINEL_BYTES=341 # immutable sentinel_bytes length asserted by source
source_rows={rt['PROJECTED_ROOT']+'/'+path:{'bytes':len(blob(path)),'sha256':digest} for path,digest in all_source.items()
 if path.endswith('.py') or path in ('procurement/tools/hosted_native_inputs.json','procurement/tools/hosted_native_runpath.json','procurement/tools/hosted_native_proposed_inventory.json')}
# Explicitly include the required prospective inventory even in the old source
# whose filter incorrectly omitted it. Track that difference rather than calling
# this prediction an executed capture of the old candidate.
capture_source=next(n for n in rt_tree.body if isinstance(n,ast.FunctionDef) and n.name=='capture_runtime')
source_filter_contains_prospective='hosted_native_proposed_inventory.json' in ast.unparse(capture_source)
source_map={path:row['sha256'] for path,row in source_rows.items()}
def remap(path):
 if path=='/usr/lib/x86_64-linux-gnu/libpq.so.5.16':path='/usr/lib/x86_64-linux-gnu/libpq.so.5'
 return layout_changes.get(path,path)
native_parts={k:{remap(path):rows[remap(path)] for path in native_graph[k]['files_detail']} for k in ('diagnostic','postgres')}
assert set(native_parts['diagnostic'])|set(native_parts['postgres'])==set(rows)
def stats(items,identity):
 full={p:dict(r) for p,r in items.items()};full[rt['SENTINEL']]={'bytes':SENTINEL_BYTES,'mode':'0555','sha256':'0'*64};full[rt['_IDENTITY_PATH']]={'bytes':len(identity),'mode':'0444','sha256':sha(identity)}
 paths=set(full)
 for path in tuple(paths):paths.update(str(p) for p in pathlib.PurePosixPath(path).parents if str(p)!='/')
 offset=0;manifest_rows=[]
 for path in sorted(paths):
  is_file=path in full;row=full.get(path,{});size=row.get('bytes',0);digest=row.get('sha256',sha(b''))
  provenance=('native-runtime:'+digest if path in rows else 'audit-source:'+digest) if is_file else 'bundle-parent:'+'0'*64
  manifest_rows.append({'group_id':0,'kind':'F' if is_file else 'D','mode':int(row.get('mode','0444'),8) if is_file else 0o555,'offset':offset,'path':path,'provenance':provenance,'sha256':digest,'size':size,'user_id':0})
  offset+=size
 manifest_bytes=len(json.dumps({'entries':manifest_rows,'protocol':'BUFFALO_LOCAL_BROWSER_RUNTIME_BUNDLE_V1'},sort_keys=True,separators=(',',':'),allow_nan=False,ensure_ascii=True).encode('ascii'))
 return {'regular_files':len(full),'entries_with_parents':len(paths),'regular_bytes':offset,'identity_bytes':len(identity),'sentinel_bytes':SENTINEL_BYTES,
  'canonical_codec_manifest_bytes':manifest_bytes,'codec_header_bytes':8,'sealed_wire_bytes':8+manifest_bytes+offset,'generic_codec_payload_limit_unchanged':536870912,'generic_codec_manifest_limit_unchanged':4194304}

diag_native={p:r['sha256'] for p,r in native_parts['diagnostic'].items()}
pg_native={p:r['sha256'] for p,r in {**native_parts['postgres'],**fixed}.items()}
diag_identity=identity_bytes(GENERATION,PYTHON,source_map,diag_native,COMMIT,TREE)
pg_identity=identity_bytes(GENERATION,pg['BIN']+'postgres',{},pg_native,COMMIT,TREE,policy=rt['POSTGRES_POLICY'],postgres=metadata)
combined_native=dict(diag_native)
for path,digest in pg_native.items():assert path not in combined_native or combined_native[path]==digest;combined_native[path]=digest
combined_metadata={'scope':'POSTGRES_SUPERVISOR_CAPTURE_ONLY','execution':'NOT_PROVEN','credential_release':'WITHHELD','supervisor_executable':PYTHON,'postgres_executables':list(pg['EXECUTABLES']),
 'diagnostic_bundle_sha256':'0'*64,'postgres_bundle_sha256':'0'*64,'native_capture':metadata}
phase_identity=identity_bytes(GENERATION,PYTHON,source_map,combined_native,COMMIT,TREE,policy=rt['POSTGRES_PHASE_POLICY'],postgres=combined_metadata)
results={'diagnostic':stats({**native_parts['diagnostic'],**source_rows},diag_identity),'postgres':stats({**native_parts['postgres'],**fixed},pg_identity),'phase':stats({**rows,**fixed,**source_rows},phase_identity)}
for name,result in results.items():
 cap=rt['POSTGRES_PHASE_MAX_BYTES'] if name=='phase' else rt['MAX_BYTES'];result.update(cap_bytes=cap,headroom_bytes=cap-result['regular_bytes'],within_bytes_and_entries=result['regular_bytes']<=cap and result['entries_with_parents']<=rt['MAX_ENTRIES'])
result={'source_commit':COMMIT,'source_tree':TREE,'classification':'EXACT_INVENTORY_DERIVED_FILE_BYTES_AND_CANONICAL_IDENTITY_LENGTH_NOT_CAPTURE_OR_MEMORY_PROOF',
 'source_projection_contains_required_prospective_inventory':source_filter_contains_prospective,'native_regular_bytes':149540145,'layout_renames':len(layout_changes),'source_projection_files':len(source_rows),'source_projection_bytes':sum(v['bytes'] for v in source_rows.values()),'fixed_generated_bytes':sum(v['bytes'] for v in fixed.values()),'generated_configuration':fixed,'source_rows':source_rows,'results':results,
 'identity_placeholder_scope':'Only fixed-length generation/input-bundle digest slots. No bundle digest/generation/execution is claimed. Canonical JSON length is invariant over valid hexadecimal values.',
 'source_filter_correction':'Required proposed inventory included explicitly; if false above this is corrected-design accounting, not actual old capture output.',
 'limits_changed':False,'actual_capture':'NOT_RUN','memory_readiness':'NOT_PROVEN','execution_authority':False}
out=pathlib.Path('/tmp/buffalo-v3-capacity-readonly')/(COMMIT[:7]+'-full-static-accounting.json');out.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('source_rows','generated_configuration')},indent=2))
