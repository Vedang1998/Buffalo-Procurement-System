"""Prepare bounded public objects only. No network, credentials or bucket writes."""
import hashlib,json,os,stat,subprocess
from pathlib import Path
repo=Path('/workspace/Buffalo-Procurement-System');e=repo/'docs/review/2026-10-27-runtime-control-lifecycle';cache=Path('/tmp/buffalo-nov3-artifacts');p=Path('/tmp/buffalo-nov3-provenance');out=Path('/tmp/buffalo-nov3')
selection=json.loads((e/'nov1-native-installation-decision.json').read_text());objects={};local={}
def add(path,role,expected=None,size=None):
 fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  before=os.fstat(fd);assert stat.S_ISREG(before.st_mode) and before.st_nlink==1
  digest=hashlib.sha256();total=0
  while data:=os.read(fd,65536):total+=len(data);digest.update(data)
  after=os.fstat(fd);assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)
 finally:os.close(fd)
 sha=digest.hexdigest();assert expected is None or sha==expected;assert size is None or total==size
 key='native-runtime-evidence/sha256/'+sha
 if key not in objects:objects[key]={'key':key,'sha256':sha,'bytes':total,'roles':[]}
 assert objects[key]['bytes']==total;objects[key]['roles'].append(role);local[key]=str(path)
for sha,a in sorted(selection['artifacts'].items()):add(cache/sha,'SELECTED_RUNTIME_ARTIFACT',sha,a['artifact_bytes'])
add(cache/'80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31','PINNED_PUBLIC_TRUST_ROOT',selection['trust_root']['sha256'],selection['trust_root']['bytes'])
for name,role in [('noble-2a6a199e-Packages.xz','ACCEPTED_NOBLE_INDEX'),('oct29-native-noble-InRelease','ACCEPTED_NOBLE_SIGNED_RELEASE'),('oct29-native-noble-updates-InRelease','ACCEPTED_UPDATES_SIGNED_RELEASE'),('native-python-publisher-hashes.txt','PYTHON_PUBLISHER_DIGESTS')]:add(e/name,role)
for name,role in [('4292c6f61b84c8685c73f6db585fce9974f293a0e62fb070a4c7a2572e333996.Packages.xz','UNADOPTED_REPLACEMENT_UPDATES_INDEX'),('candidate-noble-updates-InRelease','UNADOPTED_REPLACEMENT_SIGNED_RELEASE'),('candidate-verified-Release','UNADOPTED_REPLACEMENT_VERIFIED_PAYLOAD'),('original-noble-verified-Release','ACCEPTED_NOBLE_VERIFIED_PAYLOAD'),('original-noble-updates-verified-Release','ACCEPTED_UPDATES_VERIFIED_PAYLOAD'),('replacement-metadata-candidate.json','UNADOPTED_REPLACEMENT_MATCH_RECEIPT'),('candidate-signature-verification.json','REPLACEMENT_SIGNATURE_RECEIPT'),('original-release-signature-verification.json','ACCEPTED_RELEASE_SIGNATURE_RECEIPT'),('candidate-route-record-verification.json','REPLACEMENT_RECORD_RECEIPT')]:add(p/name,role)
add(cache/'recovery-receipt.json','ARTIFACT_RETRIEVAL_RECEIPT')
total=sum(x['bytes'] for x in objects.values());assert len([x for x in objects.values() if 'SELECTED_RUNTIME_ARTIFACT' in x['roles']])==66
assert sum(x['bytes'] for x in objects.values() if 'SELECTED_RUNTIME_ARTIFACT' in x['roles'])==175344809
manifest={'schema':'BUFFALO_PUBLIC_RUNTIME_RETENTION_PACKAGE_V1','status':'INCOMPLETE_FOR_INSTALLATION','source_checkpoint':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'accepted_installation_manifest_sha256':selection['implementation_manifest_sha256'],'target':{'project_name':'Buffalo Procurement Migration','project_id':'5bdb474a-4af8-4187-b1be-be45594a22b2','environment_name':'production','environment_id':'5d8f28f5-168c-4eb9-8eef-6c639ae4cf46','bucket_name':'migration-private-artifacts','bucket_id':'9d7e0ef1-e776-401d-8629-c407da96e3f0','prefix':'native-runtime-evidence/','identity_status':'OWNER_SUPPLIED_AND_HISTORICAL_REPO_MATCH_NOT_AUTHENTICATED_REMOTE_VERIFICATION'},'objects':sorted(objects.values(),key=lambda x:x['key']),'unique_objects':len(objects),'payload_bytes':total,'maximum_additional_bytes':209715200,'creation':'CONDITIONAL_CREATE_ONLY_IF_NONE_MATCH_STAR; existing exact bytes may be independently downloaded and reused; conflict refuses','retention':'UNTIL_SEPARATELY_AUTHORIZED_DELETION; no automatic job or bucket lifecycle change','object_lock':'NOT_VERIFIED_NOT_CLAIMED','credentials':'No credentials in package or acceptance workers; trusted transfer only; bucket-wide access must not be assumed prefix-scoped','remote_status':'NOT_RUN_AUTHENTICATED_TRANSFER_UNAVAILABLE','remote_verified_objects':0,'remote_verified_bytes':0,'missing_accepted_index_sha256':'8e3c832aed4bc99e14b0e501f578cd95db6bae0e6a435d46f292048696852c25','replacement_adoption':'NOT_GRANTED','fresh_download':'REQUIRED_SEPARATE_EMPTY_DIRECTORY_AND_CACHE_DISABLED; read all bytes, verify SHA256/size; remote metadata/ETag alone insufficient'}
raw=(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n').encode();sha=hashlib.sha256(raw).hexdigest();assert total+len(raw)<=209715200
(out/'retention-package.json').write_bytes(raw)
receipt={'manifest_sha256':sha,'manifest_bytes':len(raw),'manifest_key':'native-runtime-evidence/sha256/'+sha,'payload_objects':len(objects),'payload_bytes':total,'total_with_manifest':total+len(raw),'additional_storage_ceiling':209715200,'headroom_bytes':209715200-total-len(raw),'verification':'LOCAL_EVERY_OBJECT_COMPLETE_READ_SHA256_SIZE_NO_SYMLINK_SINGLE_LINK_IDENTITY_STABLE','remote_verification':'NOT_RUN','remote_objects_written':0,'remote_bytes_written':0,'cache':'DISPOSABLE_NOT_DURABLE'}
(out/'retention-package-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
(out/'retention-local-paths.json').write_text(json.dumps(local,indent=2)+'\n')
print(json.dumps(receipt,indent=2))
