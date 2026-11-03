import os,fcntl,hashlib,json,subprocess,tempfile,sys
from pathlib import Path
root=Path('/tmp/buffalo-nov3-provenance');repo=Path('/workspace/Buffalo-Procurement-System');sys.path.insert(0,str(repo/'procurement/tools'))
import hosted_synthetic_native_inputs as native
key=(root/'ubuntu-archive-keyring.gpg').read_bytes();key_sha=hashlib.sha256(key).hexdigest();assert len(key)==3607 and key_sha=='80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'
receipts=[]
for suite in ('noble','noble-updates'):
 artifacts=[a for a in native.require_complete_inventory()['artifacts'].values() if a.get('suite')==suite]
 expected_signed={a['signed_release']['signed_sha256'] for a in artifacts};expected_payload={a['signed_release']['verified_release_sha256'] for a in artifacts}
 assert len(expected_signed)==len(expected_payload)==1
 path=repo/'docs/review/2026-10-27-runtime-control-lifecycle'/('oct29-native-'+suite+'-InRelease');signed=path.read_bytes();signed_sha=hashlib.sha256(signed).hexdigest();assert signed_sha in expected_signed
 fds=[]
 def seal(raw):
  fd=os.memfd_create('public-publisher-metadata',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.write(fd,raw);os.fchmod(fd,0o444)
  fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
  rd=os.open('/proc/self/fd/'+str(fd),os.O_RDONLY|os.O_CLOEXEC);os.close(fd);fds.append(rd);return rd
 before_fds=set(os.listdir('/proc/self/fd'));keyfd=seal(key);releasefd=seal(signed)
 try:
  with tempfile.TemporaryDirectory(prefix='buffalo-original-gpg-') as home:
   output=Path(home)/'verified-release.txt';argv=['/usr/bin/gpg','--batch','--no-options','--homedir',home,'--no-auto-key-retrieve','--auto-key-locate','clear','--no-default-keyring','--lock-never','--keyring','/proc/self/fd/'+str(keyfd),'--status-fd','1','--output',str(output),'--decrypt','/proc/self/fd/'+str(releasefd)]
   result=subprocess.run(argv,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},pass_fds=tuple(fds),stdin=subprocess.DEVNULL,capture_output=True,timeout=15)
   assert result.returncode==0 and len(result.stdout)<=65536 and len(result.stderr)<=65536
   valid=[l.split() for l in result.stdout.decode().splitlines() if l.startswith('[GNUPG:] VALIDSIG ')]
   assert len(valid)==1 and valid[0][2]=='F6ECB3762474EDA9D21B7022871920D1991BC93C'
   payload=output.read_bytes();digest=hashlib.sha256(payload).hexdigest();assert digest in expected_payload
   (root/('original-'+suite+'-verified-Release')).write_bytes(payload)
   (root/('original-'+suite+'-gpg-status.txt')).write_bytes(result.stdout);(root/('original-'+suite+'-gpg-stderr.txt')).write_bytes(result.stderr)
 finally:
  for fd in fds:os.close(fd)
 assert before_fds==set(os.listdir('/proc/self/fd'))
 receipts.append({'suite':suite,'selected_artifacts':len(artifacts),'signed_sha256':signed_sha,'signed_bytes':len(signed),'verified_payload_sha256':digest,'verified_payload_bytes':len(payload),'signature_math':'VERIFIED','signer':valid[0][2],'all_record_pins_match':True,'keyring_sha256':key_sha,'keyring_bytes':len(key),'protected_inputs':'held readonly fully sealed memfds','gpg_returncode':result.returncode,'descriptor_inventory_equal':True,'temporary_gpg_home_removed':not Path(home).exists(),'scope':'Independent gpg2.4.7 extraction, not native-route gpgv target proof'})
(root/'original-release-signature-verification.json').write_text(json.dumps(receipts,indent=2)+'\n');print(json.dumps(receipts,indent=2))
