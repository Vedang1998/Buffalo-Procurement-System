import os,fcntl,hashlib,json,subprocess,tempfile
from pathlib import Path
root=Path('/tmp/buffalo-nov3-provenance');key=(root/'ubuntu-archive-keyring.gpg').read_bytes();signed=(root/'candidate-noble-updates-InRelease').read_bytes()
assert len(key)==3607 and hashlib.sha256(key).hexdigest()=='80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31'
assert len(signed)<=2097152
fds=[]
def seal(raw):
 fd=os.memfd_create('public-publisher-metadata',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING);os.write(fd,raw);os.fchmod(fd,0o444)
 fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
 rd=os.open('/proc/self/fd/'+str(fd),os.O_RDONLY|os.O_CLOEXEC);os.close(fd);fds.append(rd);return rd
keyfd=seal(key);releasefd=seal(signed)
try:
 with tempfile.TemporaryDirectory(prefix='buffalo-public-gpg-') as home:
  output=Path(home)/'verified-release.txt'
  command=['/usr/bin/gpg','--batch','--no-options','--homedir',home,'--no-auto-key-retrieve','--auto-key-locate','clear','--no-default-keyring','--lock-never','--keyring','/proc/self/fd/'+str(keyfd),'--status-fd','1','--output',str(output),'--decrypt','/proc/self/fd/'+str(releasefd)]
  result=subprocess.run(command,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},pass_fds=tuple(fds),stdin=subprocess.DEVNULL,capture_output=True,timeout=15)
  assert result.returncode==0 and len(result.stdout)<=65536 and len(result.stderr)<=65536
  statuses=result.stdout.decode().splitlines();valid=[line.split() for line in statuses if line.startswith('[GNUPG:] VALIDSIG ')]
  assert len(valid)==1 and valid[0][2]=='F6ECB3762474EDA9D21B7022871920D1991BC93C'
  payload=output.read_bytes();assert len(payload)<=2097152
  (root/'candidate-verified-Release').write_bytes(payload)
  (root/'candidate-gpg-status.txt').write_bytes(result.stdout);(root/'candidate-gpg-stderr.txt').write_bytes(result.stderr)
  record={'signed_sha256':hashlib.sha256(signed).hexdigest(),'signed_bytes':len(signed),'verified_payload_sha256':hashlib.sha256(payload).hexdigest(),'verified_payload_bytes':len(payload),'signer':valid[0][2],'gpg_returncode':result.returncode,'keyring_sha256':hashlib.sha256(key).hexdigest(),'keyring_bytes':len(key),'keyring_source':'Exact owner-proposed publisher HTTPS pinned keyring; target trust-root comparison not newly performed','protected_inputs':'held readonly fully sealed memfds','tool':'/usr/bin/gpg','tool_version':subprocess.check_output(['/usr/bin/gpg','--version'],env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},text=True).splitlines()[0],'tool_sha256':hashlib.sha256(Path('/usr/bin/gpg').read_bytes()).hexdigest(),'signature_math':'VERIFIED','installation_authority':False}
 record['temporary_gpg_home_removed']=not Path(home).exists()
finally:
 for fd in fds:os.close(fd)
record['input_descriptors_closed']=True
(root/'candidate-signature-verification.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
