import copy,hashlib,json,os,platform,shutil,subprocess,sys,tempfile,time
from pathlib import Path
repo=Path.cwd();out=Path('/tmp/buffalo-status-repair');inputs=Path('/tmp/buffalo-nov3-provenance');sys.path.insert(0,str(repo/'procurement/tools'))
import hosted_synthetic_native_route as route
import hosted_synthetic_native_inputs as native
commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();assert commit=='fb2dc86a43330f13376369c59db815dd03eb4d70'
key=(inputs/'ubuntu-archive-keyring.gpg').read_bytes();anchor=native.installation_manifest()['trust_root'];assert hashlib.sha256(key).hexdigest()==anchor['sha256'] and len(key)==anchor['bytes']
inventory=native.require_complete_inventory();candidate=json.loads((inputs/'replacement-metadata-candidate.json').read_text());cases=[]
for suite in ('noble','noble-updates'):
 selected=[a for a in inventory['artifacts'].values() if a.get('suite')==suite];a=selected[0]
 cases.append(('original-'+suite,route._EVIDENCE/('oct29-native-'+suite+'-InRelease'),a['signed_release']['signed_sha256'],a['signed_release']['verified_release_sha256'],a['packages_index']))
cases.append(('replacement-noble-updates',inputs/'candidate-noble-updates-InRelease',candidate['candidate_release']['signed_sha256'],candidate['candidate_release']['verified_payload_sha256'],candidate['candidate_index']))
record={'commit':commit,'tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],text=True).strip(),'tool':'/usr/bin/gpg','tool_sha256':hashlib.sha256(Path('/usr/bin/gpg').read_bytes()).hexdigest(),'version':subprocess.check_output(['/usr/bin/gpg','--version'],text=True).splitlines()[0],'python':sys.version,'platform':platform.platform(),'gpgv':{'path':shutil.which('gpgv'),'result':'NOT_RUN_ABSENT'},'scope':'REAL_LOCAL_GPG_SIGNATURE_VERIFICATION_AND_REPAIRED_PARSER; NOT_TARGET_GPGV_OR_INSTALLATION','cases':[],'network_calls':0,'installation_effects':0}
for label,path,signed_sha,payload_sha,index in cases:
 signed=path.read_bytes();assert hashlib.sha256(signed).hexdigest()==signed_sha and len(signed)<=2097152
 before=set(os.listdir('/proc/self/fd'))
 with route._sealed_metadata(key) as keyfd,route._sealed_metadata(signed) as releasefd,tempfile.TemporaryDirectory(prefix='buffalo-status-public-gpg-') as home:
  argv=['/usr/bin/gpg','--batch','--no-options','--homedir',home,'--no-auto-key-retrieve','--auto-key-locate','clear','--no-default-keyring','--lock-never','--keyring','/proc/self/fd/'+str(keyfd),'--status-fd','2','--output','-','--decrypt','/proc/self/fd/'+str(releasefd)]
  result=subprocess.run(argv,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},pass_fds=(keyfd,releasefd),stdin=subprocess.DEVNULL,capture_output=True,timeout=15)
  payload=route._verified_release_payload(result,anchor['signer'],payload_sha);route._release_index_binding(payload,index)
  (out/(label+'-actual-gpg-status.txt')).write_bytes(result.stderr)
  negatives=[]
  for status in ('REVKEYSIG','KEYREVOKED','EXPKEYSIG','EXPSIG'):
   altered=subprocess.CompletedProcess([],0,result.stdout,result.stderr+('[GNUPG:] '+status+' '+anchor['signer'][-16:]+' public fixture\n').encode())
   try:route._verified_release_payload(altered,anchor['signer'],payload_sha)
   except native.ns.NamespaceProofError as error:negatives.append({'injected_status':status,'result':'REFUSED','code':str(error)})
   else:raise AssertionError('injected status accepted')
  files=sorted(p.name for p in Path(home).iterdir())
 assert before==set(os.listdir('/proc/self/fd')) and not Path(home).exists()
 record['cases'].append({'label':label,'signed_sha256':signed_sha,'signed_bytes':len(signed),'verified_payload_sha256':payload_sha,'verified_payload_bytes':len(payload),'returncode':result.returncode,'status_sha256':hashlib.sha256(result.stderr).hexdigest(),'keyring_sha256':anchor['sha256'],'signer':anchor['signer'],'signature_and_index_binding':'PASS','descriptor_inventory_equal':True,'owned_home_removed':True,'owned_home_entries_before_cleanup':files,'injected_status_tests':negatives})
artifacts=copy.deepcopy([a for a in inventory['artifacts'].values() if a.get('suite')=='noble-updates'])
for a in artifacts:a['index_sha256']=candidate['candidate_index']['sha256'];a['packages_index']=candidate['candidate_index']
before=set(os.listdir('/proc/self/fd'));chain=route.verify_index_records(inputs,candidate['candidate_index'],artifacts,time.monotonic()+30)
assert len(chain['matched_artifacts'])==47 and before==set(os.listdir('/proc/self/fd'))
record.update(replacement_records=chain,accepted_metadata_unchanged=True,actual_expired_or_revoked_key_verification='NOT_RUN',revocation_freshness='NOT_ESTABLISHED; byte-pinned keyring; gpgv documents no expiry/revocation check',accepted_inputs_sha256=hashlib.sha256((repo/'procurement/tools/hosted_native_proposed_inventory.json').read_bytes()).hexdigest())
(out/'retained-release-verification.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'commit':commit,'real_local_gpg_releases':len(record['cases']),'all_signature_index_bindings':'PASS','replacement_records':len(chain['matched_artifacts']),'parsed_records':chain['parsed_records'],'gpgv':record['gpgv'],'all_cleanup':'PASS'}))
