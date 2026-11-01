"""One 60s component attempt: authenticated public artifacts to disposable stages.
No installation/importer/account/systemd/sudo or downloaded executable execution.
"""
import contextlib,hashlib,json,os,platform,resource,stat,subprocess,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path('/workspace/Buffalo-Procurement-System')
ARTIFACTS=Path('/tmp/buffalo-artifact-retrieval-20261101-hbvel7k2')
OUTPUT=Path('/tmp/buffalo-t1-readonly/public-materialization-7a106c1-optimized-streams.json')
sys.path[:0]=[str(ROOT/'procurement/tools')]
def code_hashes():
 return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for pattern in ('*.py','*.json') for p in sorted((ROOT/'procurement/tools').glob(pattern))}
before_code=code_hashes()
import hosted_synthetic_native_route as route
import hosted_synthetic_native_inputs as native

def descriptors():
 result={}
 for name in os.listdir('/proc/self/fd'):
  try:
   fd=int(name);s=os.fstat(fd);result[name]=[s.st_dev,s.st_ino,s.st_mode,os.readlink('/proc/self/fd/'+name)]
  except FileNotFoundError:pass
  except OSError:pass
 return result

def usage():
 s=resource.getrusage(resource.RUSAGE_SELF);c=resource.getrusage(resource.RUSAGE_CHILDREN)
 fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if ':' in line}
 return {'self_rss_current_kib':int(fields['VmRSS'].split()[0]),'self_ru_maxrss_kib':s.ru_maxrss,
  'waited_children_ru_maxrss_kib':c.ru_maxrss,'self_user_seconds':s.ru_utime,'self_system_seconds':s.ru_stime,
  'waited_children_user_seconds':c.ru_utime,'waited_children_system_seconds':c.ru_stime}

head=subprocess.run(['git','rev-parse','HEAD','HEAD^{tree}'],cwd=ROOT,check=True,text=True,capture_output=True).stdout.splitlines()
assert head==['7a106c16fb2fc44683d994d28e57198bc60dce37','edeba601d4df5c471744cefb56a6a03e43e23de4'], 'frozen_source_mismatch'
manifest=native.require_complete_inventory();rows=native.execution_inventory()['rows'];artifacts=manifest['artifacts']
records=[];active=None;timings=[];failure=None;parent=None;parent_identity=None;fd_before=descriptors();usage_before=usage()
original_verify=route._verify_fd;original_tar=native.Stage.materialize_tar;original_close=native.Stage.close

def wrap(label,fn):
 def invoke(*a,**k):
  start=time.monotonic();error=None
  try:return fn(*a,**k)
  except BaseException as e:error=type(e).__name__;raise
  finally:timings.append({'artifact':active,'stage':label,'seconds':time.monotonic()-start,'error':error})
 return invoke

started=time.monotonic();deadline=started+60
try:
 parent=Path(tempfile.mkdtemp(prefix='buffalo-t1-public-stage-'));s=parent.lstat();parent_identity=(s.st_dev,s.st_ino,s.st_uid,s.st_mode)
 with patch.object(route,'_verify_fd',wrap('complete_artifact_hash',original_verify)),patch.object(native.Stage,'materialize_tar',wrap('stream_extract_and_stage',original_tar)),patch.object(native.Stage,'close',wrap('owned_stage_cleanup',original_close)):
  for digest in sorted(artifacts):
   route._check(deadline);active=digest;artifact=artifacts[digest];selected=[r for r in rows.values() if r['artifact_sha256']==digest]
   record={'digest':digest,'package':artifact['package'],'version':artifact['version'],'artifact_bytes':artifact['artifact_bytes'],
      'selected_regular_files':len(selected),'selected_logical_bytes':sum(r['bytes'] for r in selected),'started_at_seconds':time.monotonic()-started}
   records.append(record);fd=None;stage=None;error=None;record['usage_before']=usage()
   try:
    t=time.monotonic();fd=route._artifact_fd(ARTIFACTS/digest,digest,artifact['artifact_bytes'],deadline);record['open_and_first_hash_seconds']=time.monotonic()-t
    t=time.monotonic();stage=native.Stage(parent/digest);record['stage_create_seconds']=time.monotonic()-t
    t=time.monotonic();result=route.materialize_artifact(stage,fd,digest,selected,deadline);record['materialize_seconds']=time.monotonic()-t
    assert result['lifetime_regular_files']==len(selected)
    assert result['lifetime_regular_bytes']==sum(r['bytes'] for r in selected)
    record['result']=result
    stream_start=time.monotonic();consumed=0;stream_count=0
    for selected_row in selected:
     route._check(deadline);file_bytes=0
     with stage.verified_stream(selected_row) as chunks:
      for chunk in chunks:
       route._check(deadline);file_bytes+=len(chunk)
     route._check(deadline);assert file_bytes==selected_row['bytes'];consumed+=file_bytes;stream_count+=1
    record['verified_stream_seconds']=time.monotonic()-stream_start;record['verified_stream_bytes']=consumed;record['verified_streams']=stream_count
    timings.append({'artifact':active,'stage':'verified_stream_consume','seconds':record['verified_stream_seconds'],'error':None})
    record['status']='MATERIALIZED_AND_STREAM_VERIFIED_COMPONENT'
   except BaseException as e:error=e;record['status']='REFUSED';record['error']={'type':type(e).__name__,'message':str(e),'notes':getattr(e,'__notes__',[])}
   finally:
    if fd is not None:
     try:os.close(fd)
     except BaseException as e:
      if error is None:error=e
      else:error.add_note('measurement_artifact_fd_cleanup_failed')
    if stage is not None and not stage.closed:
     try:stage.close()
     except BaseException as e:
      if error is None:error=e
      else:error.add_note('measurement_stage_cleanup_failed')
    record['stage_removed']=not (parent/digest).exists();record['usage_after']=usage();record['finished_at_seconds']=time.monotonic()-started
   if error is not None:raise error
except BaseException as e:failure={'type':type(e).__name__,'message':str(e),'notes':getattr(e,'__notes__',[])}
finally:
 if parent is not None:
  try:
   s=parent.lstat();assert (s.st_dev,s.st_ino,s.st_uid,s.st_mode)==parent_identity
   parent.rmdir()
  except BaseException as e:
   cleanup={'type':type(e).__name__,'message':str(e)}
   if failure is None:failure={'cleanup':cleanup}
   else:failure['cleanup']=cleanup

elapsed=time.monotonic()-started;usage_after=usage();fd_after=descriptors();after_code=code_hashes()
# Persist measured work and cleanup before optional host observations.
Path('/tmp/buffalo-t1-readonly/public-materialization-7a106c1-optimized-streams-stage-receipt.json').write_text(json.dumps({'elapsed_including_cleanup_seconds':elapsed,'failure':failure,'records':records,'timing_records':timings,'usage_before':usage_before,'usage_after':usage_after,'fd_before':fd_before,'fd_after':fd_after,'parent_removed':parent is None or not parent.exists(),'source_hashes_before':before_code,'source_hashes_after':after_code},sort_keys=True,indent=2)+'\n')
try:
 children=Path('/proc/self/task/'+str(os.getpid())+'/children').read_text().strip()
 children_observation='OBSERVED_PROC_CHILDREN'
except FileNotFoundError:
 children=None;children_observation='NOT_SUPPORTED_PROC_CHILDREN'
head_after=subprocess.run(['git','rev-parse','HEAD','HEAD^{tree}'],cwd=ROOT,check=True,text=True,capture_output=True).stdout.splitlines()
report={'status':'PASS_COMPONENT_ONLY' if failure is None and len(records)==len(artifacts) else 'REFUSED',
 'scope':'Exact public-artifact hashing/extraction/unprivileged staging/all selected verified-stream consumption/cleanup component only; NOT publisher-metadata revalidation, native installation, memory readiness, cgroup/target, capture/adoption or execution proof',
 'head':head[0],'tree':head[1],'head_after':head_after[0],'tree_after':head_after[1],'head_tree_stable':head_after==head,'concurrency':'Root reported concurrent frozen unit tests; CPU contention possible; no implementation edits during attempt','allocation_measurement':'NOT_MEASURED: no tracemalloc/allocation census, cgroup/memfd/page-cache accounting or supported-target service proof','operator_uid':os.geteuid(),'host':{'system':platform.system(),'kernel':platform.release(),'machine':platform.machine(),'python':platform.python_version(),'os_release':Path('/etc/os-release').read_text()},
 'source_hashes_before':before_code,'source_hashes_after':after_code,'source_changed':{p:{'before':before_code.get(p),'after':after_code.get(p)} for p in before_code.keys()|after_code.keys() if before_code.get(p)!=after_code.get(p)},
 'shared_work_budget_seconds':60,'elapsed_including_owned_cleanup_seconds':elapsed,'deadline_extended':False,'failure':failure,
 'artifacts_expected':len(artifacts),'artifacts_attempted':len(records),'artifact_bytes':sum(r['artifact_bytes'] for r in records),'selected_files':sum(r['selected_regular_files'] for r in records),
 'selected_logical_bytes':sum(r['selected_logical_bytes'] for r in records),'archive_selected_members':sum(r.get('result',{}).get('members',0) for r in records),'verified_streams':sum(r.get('verified_streams',0) for r in records),'verified_stream_bytes':sum(r.get('verified_stream_bytes',0) for r in records),
 'usage_before':usage_before,'usage_after':usage_after,'rss_interpretation':'Linux KiB high-water reports: self max includes imports and this component; children max is largest waited-for child RSS, not sum or simultaneous process-group peak. Neither includes a measured cgroup/page-cache charge; do not add these into whole-service usage.',
 'fd_before':fd_before,'fd_after':fd_after,'descriptor_identity_equal':fd_before==fd_after,'owned_direct_children_remaining':children,'direct_child_observation':children_observation,
 'owned_stage_parent_removed':parent is None or not parent.exists(),'timing_totals_seconds':{label:sum(t['seconds'] for t in timings if t['stage']==label) for label in {t['stage'] for t in timings}},
 'hash_calls':sum(t['stage']=='complete_artifact_hash' for t in timings),'timing_records':timings,'artifacts':records}
OUTPUT.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('status','head','tree','head_after','tree_after','head_tree_stable','source_changed','elapsed_including_owned_cleanup_seconds','failure','artifacts_expected','artifacts_attempted','artifact_bytes','selected_files','selected_logical_bytes','archive_selected_members','verified_streams','verified_stream_bytes','usage_before','usage_after','descriptor_identity_equal','owned_direct_children_remaining','direct_child_observation','owned_stage_parent_removed','timing_totals_seconds','hash_calls')},sort_keys=True,indent=2))
if not report['head_tree_stable'] or report['status']!='PASS_COMPONENT_ONLY' or report['source_changed'] or not report['descriptor_identity_equal'] or children:sys.exit(1)
