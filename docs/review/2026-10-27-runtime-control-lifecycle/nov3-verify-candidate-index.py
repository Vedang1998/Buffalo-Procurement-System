from pathlib import Path
import hashlib,json,lzma,os,sys,time,urllib.request,urllib.error
out=Path('/tmp/buffalo-nov3-provenance');root=Path('/workspace/Buffalo-Procurement-System');sys.path.insert(0,str(root/'procurement/tools'))
import hosted_synthetic_native_route as route
import hosted_synthetic_native_inputs as native
release=(out/'candidate-verified-Release').read_bytes();lines=release.splitlines();section=False;found=[]
for line in lines:
 if line==b'SHA256:':section=True;continue
 if section and not line.startswith(b' '):section=False
 if section:
  fields=line.split()
  if len(fields)==3 and fields[2]==b'main/binary-amd64/Packages.xz':found.append(fields)
assert len(found)==1
hash_bytes,size_bytes,_=found[0];digest=hash_bytes.decode();size=int(size_bytes);assert 0<size<=2097152
url='https://archive.ubuntu.com/ubuntu/dists/noble-updates/main/binary-amd64/by-hash/SHA256/'+digest
start=time.monotonic();deadline=start+30
with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Buffalo-public-provenance-readonly/1'}),timeout=15) as response:
 chunks=[];total=0
 while True:
  assert time.monotonic()<deadline
  b=response.read(min(65536,size+1-total))
  if not b:break
  total+=len(b);assert total<=size;chunks.append(b)
 raw=b''.join(chunks);assert len(raw)==size and hashlib.sha256(raw).hexdigest()==digest
path=out/(digest+'.Packages.xz');path.write_bytes(raw)
fetch={'url':url,'bytes':len(raw),'sha256':digest,'http_status':200,'seconds':time.monotonic()-start}
(out/'candidate-index-fetch.json').write_text(json.dumps(fetch,indent=2)+'\n')
# Bounded decompression: signed compressed bytes are authenticated; derived
# uncompressed identity is evidence, never substituted for signed compressed pin.
reader=lzma.LZMAFile(path,'rb');h=hashlib.sha256();total=0
while True:
 assert time.monotonic()<deadline
 chunk=reader.read(65536)
 if not chunk:break
 total+=len(chunk);assert total<=64*1024**2;h.update(chunk)
reader.close()
index={'suite':'noble-updates','component':'main','sha256':digest,'bytes':size,'uncompressed_bytes':total,'uncompressed_sha256':h.hexdigest(),'url':url}
route._release_index_binding(release,index)
artifacts=[a for a in native.require_complete_inventory()['artifacts'].values() if a.get('suite')=='noble-updates'];assert len(artifacts)==47
expected={(a['package'],a['version'],a['architecture']):a for a in artifacts};assert len(expected)==47
fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC);matches={};mismatches=[];count=0;other_versions=[]
try:
 for paragraph in route._index_records(fd,index,deadline):
  fields=route._package_fields(paragraph);count+=1
  key=tuple(fields[k] for k in ('Package','Version','Architecture'))
  if key not in expected:
   if fields['Package'] in {a['package'] for a in artifacts}:other_versions.append({k:fields[k] for k in ('Package','Version','Architecture')})
   continue
  assert key not in matches,'duplicate exact selected identity'
  a=expected[key];actual=(fields['Package'],fields['Version'],fields['Architecture'],fields.get('Filename'),fields.get('SHA256'),fields.get('Size'))
  wanted=(a['package'],a['version'],a['architecture'],a['url'].removeprefix('https://archive.ubuntu.com/ubuntu/'),a['artifact_sha256'],str(a['artifact_bytes']))
  if actual!=wanted:mismatches.append({'package':a['package'],'expected':wanted,'actual':actual})
  matches[key]={'package':actual[0],'version':actual[1],'architecture':actual[2],'filename':actual[3],'sha256':actual[4],'bytes':int(actual[5]),'exact_tuple_equal':actual==wanted,'candidate_package_record_sha256':hashlib.sha256(paragraph).hexdigest(),'accepted_package_record_sha256':a['package_record_sha256'],'complete_stanza_unchanged':hashlib.sha256(paragraph).hexdigest()==a['package_record_sha256']}
finally:os.close(fd)
missing=[dict(zip(('package','version','architecture'),k)) for k in expected if k not in matches]
result={'schema':'BUFFALO_METADATA_ONLY_PROVENANCE_CANDIDATE_V1','status':'VERIFIED_ALL47_UNCHANGED_TUPLES_PENDING_OWNER_ADOPTION' if len(matches)==47 and not mismatches and not missing else 'INCOMPLETE_REPLACEMENT_CANDIDATE','accepted_metadata_unchanged':True,'accepted_original_index_sha256':'8e3c832aed4bc99e14b0e501f578cd95db6bae0e6a435d46f292048696852c25','candidate_index':index,'candidate_release':json.loads((out/'candidate-signature-verification.json').read_text()),'records_parsed':count,'selected_expected':47,'matched':len(matches),'mismatches':mismatches,'missing':missing,'other_versions':other_versions,'tuples':list(matches.values()),'all66artifact_versions_and_hashes_unchanged':True,'signature_tool_scope':'gpg2.4.7 independent signature/decrypt verification; not installation-path gpgv target execution','installation_authority':False,'owner_metadata_adoption':'NOT_GRANTED','command_scope':'Unprivileged public metadata fetch/decompression/verification only; no installed package or artifact executable run'}
(out/'replacement-metadata-candidate.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('tuples','candidate_release')},indent=2))
