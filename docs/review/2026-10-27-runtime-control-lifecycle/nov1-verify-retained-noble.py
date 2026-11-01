"""Exact retained index-to-record verification; no signature tool or installation."""
import hashlib,json,os,shutil,subprocess,sys,tempfile,time
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root/'procurement/tools'))
import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_route as route
assert os.geteuid()>0
ids=subprocess.check_output(['git','rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
selected=[a for a in native.require_complete_inventory()['artifacts'].values() if a.get('suite')=='noble']
index=selected[0]['packages_index'];source=root/'docs/review/2026-10-27-runtime-control-lifecycle/noble-2a6a199e-Packages.xz'
assert source.stat().st_size==index['bytes'] and hashlib.sha256(source.read_bytes()).hexdigest()==index['sha256']
fds=set(os.listdir('/proc/self/fd'));start=time.monotonic()
with tempfile.TemporaryDirectory(prefix='buffalo-noble-records-') as name:
 path=Path(name)/(index['sha256']+'.Packages.xz');shutil.copyfile(source,path)
 result=route.verify_index_records(name,index,selected,time.monotonic()+60)
result.update(commit=ids[0],tree=ids[1],seconds=time.monotonic()-start,descriptor_inventory_equal=set(os.listdir('/proc/self/fd'))==fds,temporary_directory_removed=not Path(name).exists(),scope='EXACT_RETAINED_INDEX_AND_18_RECORDS_NOT_NEW_SIGNATURE_OR_FULL_CHAIN_VERIFICATION')
print(json.dumps(result,indent=2))
