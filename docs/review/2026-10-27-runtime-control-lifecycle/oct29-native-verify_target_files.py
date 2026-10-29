import hashlib,json,os,pathlib,re,subprocess,tarfile,time,urllib.request
D=pathlib.Path('/tmp/buffalo-native-packages-readonly');T=pathlib.Path('/tmp/buffalo-memory-repair/6528427-native-target.json')
assert os.geteuid()>0
report=json.loads(T.read_text());records=json.loads((D/'exact-package-records.json').read_text())
rows={}
for row in report['rows']:
 if row['path'].startswith('/opt/') or row.get('package','').split(':')[0]=='postgresql-16':continue
 package=row.get('package','').split(':')[0]
 if package not in records['packages']:continue
 rows.setdefault(package,[]).append(row)
order=sorted(rows,key=lambda p:(0 if p in ('libicu74','libc6','libssl3t64','libtcl8.6','libtk8.6','libsqlite3-0') else 1,p))
result={'classification':'AUTHENTICATED_PUBLISHER_PAYLOAD_AND_TARGET_FILE_COMPARISON_NOT_INSTALLATION_OR_RUNTIME_TRUST','target_commit':report['commit'],'target_tree':report['tree'],'target_report_file_sha256':hashlib.sha256(T.read_bytes()).hexdigest(),'packages':{},'files':[],'unresolved':[],'not_performed':['extraction','installation','artifact execution','sudo','account/service creation','repository edit'],'spool_maximum_bytes':256*1024**2}

def normalize(name):
 assert name.startswith('./') or not name.startswith('/')
 value='/'+name.removeprefix('./').rstrip('/')
 assert '..' not in pathlib.PurePosixPath(value).parts
 return value

def aliases(path):
 result={path}
 for prefix in ('/bin/','/sbin/','/lib/','/lib64/'):
  if path.startswith('/usr'+prefix):result.add(path[4:])
 return result
if (D/'authenticated-target-files.json').exists():
 result=json.loads((D/'authenticated-target-files.json').read_text())
 result.setdefault('harness_repairs',[]).append({'failure':'full stat tuple compared access time after reading libssl payload','correction':'retain dev/inode/mode/uid/gid/nlink/size/mtime_ns/ctime_ns plus full before/after artifact digest; access time is not content identity','scope':'read-only research harness, not runtime policy'})

def identity(info):return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

for package in order:
 if package in result['packages']:continue
 record=records['packages'][package];wanted=rows[package];p=D/'current-payload.deb'
 assert not p.exists()
 started=time.monotonic();h=hashlib.sha256();count=0
 try:
  with urllib.request.urlopen(record['url'],timeout=20) as response,p.open('xb') as output:
   assert response.status==200
   while True:
    block=response.read(min(65536,record['artifact_bytes']+1-count))
    if not block:break
    count+=len(block);assert count<=record['artifact_bytes'] and time.monotonic()-started<60;h.update(block);output.write(block)
  assert count==record['artifact_bytes'] and h.hexdigest()==record['artifact_sha256']
  fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);before=os.fstat(fd)
  owned=None
  try:
   command=['/usr/bin/timeout','--signal=TERM','--kill-after=2s','20s','/usr/bin/dpkg-deb','--fsys-tarfile','/proc/self/fd/'+str(fd)]
   owned=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},pass_fds=(fd,))
   members={};entry_count=0;logical_total=0
   with tarfile.open(fileobj=owned.stdout,mode='r|') as archive:
    for member in archive:
     entry_count+=1;assert entry_count<=50000 and time.monotonic()-started<90
     name=normalize(member.name)
     if member.isfile():logical_total+=member.size
     assert logical_total<=256*1024**2
     matched=[row for row in wanted if name in aliases(row['physical_path'])]
     if not matched:continue
     assert member.isfile() and not member.mode&0o6000 and 0<=member.size<=83886080 and name not in members
     stream=archive.extractfile(member);digest=hashlib.sha256();size=0
     while True:
      block=stream.read(65536)
      if not block:break
      size+=len(block);assert size<=member.size;digest.update(block)
     assert size==member.size
     members[name]={'artifact_member':member.name,'bytes':size,'sha256':digest.hexdigest(),'archive_mode':format(member.mode,'04o')}
   owned.stdout.close();returncode=owned.wait(timeout=5);assert returncode==0
   assert identity(before)==identity(os.fstat(fd))
   again=hashlib.sha256();offset=0
   while offset<before.st_size:
    block=os.pread(fd,min(65536,before.st_size-offset),offset);assert block;again.update(block);offset+=len(block)
   assert again.hexdigest()==record['artifact_sha256']
  finally:
   if owned is not None:
    if owned.poll() is None:owned.kill()
    owned.wait(timeout=5)
    if not owned.stdout.closed:owned.stdout.close()
   os.close(fd)
  result['packages'][package]={**record,'payload_verification':'FULL_BYTES_MATCH_SIGNED_INDEX_BEFORE_AND_AFTER_NATIVE_TAR_READ','archive_entries':entry_count,'archive_regular_bytes':logical_total,'seconds':round(time.monotonic()-started,6),'owned_decoder_reaped':True}
  for row in wanted:
   matches=[members[a] for a in aliases(row['physical_path']) if a in members]
   if len(matches)!=1:
    result['unresolved'].append({'path':row['path'],'physical_path':row['physical_path'],'package':package,'reason':'MISSING_OR_AMBIGUOUS_AUTHENTICATED_MEMBER','matches':matches});continue
   match=matches[0];equal=match['sha256']==row['sha256'] and match['bytes']==row['bytes']
   result['files'].append({'path':row['path'],'physical_path':row['physical_path'],'target_sha256':row['sha256'],'target_bytes':row['bytes'],'package':package,'version':record['version'],'artifact_sha256':record['artifact_sha256'],**match,'target_equal':equal,'alias_rule':'exact physical path or documented merged-/usr bin/sbin/lib/lib64 archive alias','publisher_assurance':'SIGNED_UBUNTU_RELEASE_AND_PACKAGE_DIGEST_NOT_SIGNED_BUILD_ATTESTATION'})
   if not equal:result['unresolved'].append({'path':row['path'],'package':package,'reason':'TARGET_BYTES_DIFFER_FROM_AUTHENTICATED_PACKAGE'})
  print(package,'files',len(wanted),'bytes',count,'unresolved',len(result['unresolved']),flush=True)
 finally:
  if p.exists():p.unlink()
 (D/'authenticated-target-files.json').write_text(json.dumps(result,indent=2)+'\n')
result['payload_spool_removed']=not (D/'current-payload.deb').exists();result['matched_file_rows']=sum(row['target_equal'] for row in result['files'])
result['observed_pgdg_server']='NOT_SELECTED; approved Ubuntu16.15 package remains exact selected input'
(D/'authenticated-target-files.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'packages':len(result['packages']),'matched_files':result['matched_file_rows'],'unresolved':result['unresolved'],'payload_spool_removed':result['payload_spool_removed']}),flush=True)
