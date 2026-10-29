import hashlib,json,os,pathlib,subprocess,sys,tarfile,time,urllib.request
sys.path.insert(0,'/workspace/Buffalo-Procurement-System/procurement/tools')
import hosted_synthetic_runtime as runtime
D=pathlib.Path('/tmp/buffalo-native-packages-readonly');records=json.loads((D/'exact-package-records.json').read_text())['packages']
selected={'postgresql-client-16':{'./usr/lib/postgresql/16/bin/pg_dump','./usr/lib/postgresql/16/bin/pg_restore'},'libpq5':{'./usr/lib/x86_64-linux-gnu/libpq.so.5.16','./usr/lib/x86_64-linux-gnu/libpq.so.5'}}
out={'scope':'AUTHENTICATED_SELECTED_CLIENT_LIBPQ_STATIC_ELF_AND_ALIAS_ONLY','version':'16.15-0ubuntu0.24.04.1','files':[],'aliases':[],'artifact_execution':False,'spool_removed':False,'parser_source_sha256':hashlib.sha256(pathlib.Path(runtime.__file__).read_bytes()).hexdigest()}
for package,wanted in selected.items():
 rec=records[package];p=D/'current-client-payload.deb';assert not p.exists();fd=None;proc=None
 try:
  h=hashlib.sha256();size=0
  with urllib.request.urlopen(rec['url'],timeout=20) as response,p.open('xb') as stream:
   while True:
    chunk=response.read(min(65536,rec['artifact_bytes']+1-size))
    if not chunk:break
    size+=len(chunk);assert size<=rec['artifact_bytes'];h.update(chunk);stream.write(chunk)
  assert size==rec['artifact_bytes'] and h.hexdigest()==rec['artifact_sha256']
  fd=os.open(p,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
  proc=subprocess.Popen(['/usr/bin/timeout','--signal=TERM','--kill-after=2s','15s','/usr/bin/dpkg-deb','--fsys-tarfile','/proc/self/fd/'+str(fd)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,pass_fds=(fd,),env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
  seen=set()
  with tarfile.open(fileobj=proc.stdout,mode='r|') as tar:
   for i,member in enumerate(tar):
    assert i<50000
    if member.name not in wanted:continue
    assert member.name not in seen;seen.add(member.name)
    if member.issym():
     assert member.name=='./usr/lib/x86_64-linux-gnu/libpq.so.5' and member.linkname=='libpq.so.5.16'
     out['aliases'].append({'path':member.name[1:],'target':member.linkname,'package':package,'artifact_sha256':rec['artifact_sha256']});continue
    assert member.isfile() and member.size<1024**2
    raw=tar.extractfile(member).read(member.size+1);assert len(raw)==member.size
    image=runtime.parse_elf(raw)
    out['files'].append({'path':member.name[1:],'artifact_member':member.name,'artifact_sha256':rec['artifact_sha256'],'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'mode':format(member.mode,'04o'),'elf':image})
  assert seen==wanted;proc.stdout.close();assert proc.wait(timeout=5)==0
 finally:
  if proc is not None:
   if proc.poll() is None:proc.kill()
   proc.wait(timeout=5)
   if not proc.stdout.closed:proc.stdout.close()
  if fd is not None:os.close(fd)
  if p.exists():p.unlink()
out['spool_removed']=True
(D/'selected-client-libpq-elf.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
