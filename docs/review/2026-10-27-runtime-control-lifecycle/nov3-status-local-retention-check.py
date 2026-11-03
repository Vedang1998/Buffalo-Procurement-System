"""Local complete-byte verification only. No network, credential or upload code."""
import datetime,hashlib,json,os,re,stat,subprocess,time
from pathlib import Path
REPO=Path('/workspace/Buffalo-Procurement-System')
EVIDENCE=REPO/'docs/review/2026-10-27-runtime-control-lifecycle'
OUT=Path('/tmp/buffalo-status-retention')
EXPECTED='d8ce40daf1d41cc472aa78fb5dfaa6428887a1bd9d9249658badcebce57dfbe1'
PREFIX='native-runtime-evidence/sha256/'
ALLOWED={Path('/tmp/buffalo-nov3-artifacts'),Path('/tmp/buffalo-nov3-provenance'),EVIDENCE}
def identity(info):
 return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
def descriptors():
 result={}
 for name in os.listdir('/proc/self/fd'):
  try:
   s=os.fstat(int(name));result[name]=(s.st_dev,s.st_ino,s.st_mode)
  except OSError:pass
 return result
def verify(path,sha,size):
 assert path.parent in ALLOWED or path in (Path('/tmp/buffalo-nov3/retention-package.json'),)
 assert path.resolve(strict=True)==path
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  before=os.fstat(fd)
  assert stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size==size
  h=hashlib.sha256();total=0
  while raw:=os.read(fd,65536):
   total+=len(raw);assert total<=size;h.update(raw)
  assert total==size and h.hexdigest()==sha
  assert identity(before)==identity(os.fstat(fd))==identity(path.lstat())
  return {'sha256':sha,'bytes':total,'source':str(path),'identity_stable':True,'device':before.st_dev,'inode':before.st_ino,'nlink':before.st_nlink,'regular':True}
 finally:os.close(fd)
start=time.monotonic();fd_before=descriptors()
manifest_path=EVIDENCE/'nov3-retention-package.json'
raw=manifest_path.read_bytes();assert len(raw)==20682 and hashlib.sha256(raw).hexdigest()==EXPECTED
manifest=json.loads(raw);paths=json.loads(Path('/tmp/buffalo-nov3/retention-local-paths.json').read_text())
assert manifest['unique_objects']==len(manifest['objects'])==len(paths)==81
assert manifest['payload_bytes']==179257086 and manifest['maximum_additional_bytes']==209715200
assert manifest['target']['prefix']=='native-runtime-evidence/'
assert manifest['target']['project_id']=='5bdb474a-4af8-4187-b1be-be45594a22b2'
assert manifest['target']['environment_id']=='5d8f28f5-168c-4eb9-8eef-6c639ae4cf46'
assert manifest['target']['bucket_id']=='9d7e0ef1-e776-401d-8629-c407da96e3f0'
rows=[];failures=[]
keys={o['key'] for o in manifest['objects']};assert len(keys)==81 and keys==set(paths)
for item in manifest['objects']:
 assert re.fullmatch('[0-9a-f]{64}',item['sha256']) and item['key']==PREFIX+item['sha256']
 try:row=verify(Path(paths[item['key']]),item['sha256'],item['bytes']);row.update(key=item['key'],roles=item['roles']);rows.append(row)
 except (OSError,AssertionError) as error:failures.append({'key':item['key'],'error_class':type(error).__name__})
manifest_observations=[]
for path in (manifest_path,Path('/tmp/buffalo-nov3/retention-package.json')):
 try:manifest_observations.append(verify(path,EXPECTED,20682))
 except (OSError,AssertionError) as error:failures.append({'key':PREFIX+EXPECTED,'source':str(path),'error_class':type(error).__name__})
assert sum(o['bytes'] for o in manifest['objects'])==179257086
assert len([o for o in manifest['objects'] if 'SELECTED_RUNTIME_ARTIFACT' in o['roles']])==66
assert sum(o['bytes'] for o in manifest['objects'] if 'SELECTED_RUNTIME_ARTIFACT' in o['roles'])==175344809
fd_after=descriptors();ids=subprocess.check_output(['git','rev-parse','HEAD','HEAD^{tree}'],cwd=REPO,text=True).splitlines()
record={'classification':'LOCAL_COMPLETE_BYTE_REVALIDATION_ONLY_NOT_REMOTE_RETENTION_OR_FRESH_DOWNLOAD','created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':ids[0],'tree':ids[1],'status':'PASS_LOCAL' if not failures and fd_before==fd_after else 'REFUSED','expected_manifest_sha256':EXPECTED,'manifest_key':PREFIX+EXPECTED,'manifest_bytes':20682,'payload_objects':81,'payload_bytes':179257086,'unique_total_objects':82,'total_with_manifest':179277768,'ceiling_bytes':209715200,'headroom_bytes':30437432,'complete_payloads_verified':len(rows),'selected_artifacts':66,'selected_artifact_bytes':175344809,'manifest_copies_verified':len(manifest_observations),'manifest_observations':manifest_observations,'rows':rows,'failures':failures,'descriptor_inventory_equal':fd_before==fd_after,'seconds':time.monotonic()-start,'inputs_modified':False,'network_requests':0,'login_attempts':0,'bucket_writes':0,'remote_identity_verification':'NOT_RUN','remote_fresh_download':'NOT_RUN','auth_state':'Parent reports unavailable; no duplicate login or credential inspection performed by this check','cache_retained_for_authorized_transfer':True}
path=OUT/'existing-package-verification.json';path.write_text(json.dumps(record,indent=2)+'\n')
summary={k:v for k,v in record.items() if k not in ('rows','manifest_observations')}
summary['receipt']=str(path);summary['receipt_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();print(json.dumps(summary,indent=2));assert record['status']=='PASS_LOCAL'
