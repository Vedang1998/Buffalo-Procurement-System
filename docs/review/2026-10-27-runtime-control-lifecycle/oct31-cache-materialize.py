import hashlib,json,os,pathlib,tempfile,time,subprocess
import hosted_synthetic_native_inputs as n
import hosted_synthetic_native_route as r
artifact=pathlib.Path('/tmp/buffalo-h12-readonly/libc6_2.39-0ubuntu8.9_amd64.deb')
row=n.execution_inventory()['rows'][n.GCONV_CACHE]
before=set(os.listdir('/proc/self/fd'));started=time.monotonic()
with tempfile.TemporaryDirectory(prefix='buffalo-cache-check-') as parent:
 stage=n.Stage(pathlib.Path(parent)/'stage');fd=os.open(artifact,os.O_RDONLY|os.O_CLOEXEC)
 try:
  result=r.materialize_artifact(stage,fd,row['artifact_sha256'],[row],started+60)
  data=(stage.path/n.GCONV_CACHE.lstrip('/')).read_bytes()
  assert hashlib.sha256(data).hexdigest()==row['sha256'] and len(data)==27028
 finally:
  os.close(fd)
  if not stage.closed:stage.close()
 assert not stage.path.exists()
assert set(os.listdir('/proc/self/fd'))==before
print(json.dumps({'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],text=True).strip(),'scope':'UNPRIVILEGED_EXACT_PACKAGE_MEMBER_MATERIALIZATION_NOT_PRIVATE_VIEW_OR_INSTALLATION','artifact_sha256':row['artifact_sha256'],'member':row['artifact_member'],'bytes':len(data),'sha256':row['sha256'],'elapsed_seconds':time.monotonic()-started,'result':result,'stage_removed':True,'descriptor_inventory_restored':True,'target_interpreter_private_view':'NOT_RUN'},sort_keys=True))
