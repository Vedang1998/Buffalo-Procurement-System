import ast,hashlib,json,os,pathlib,re,shutil,subprocess,sys,time
root=pathlib.Path.cwd();logs=pathlib.Path('/tmp/buffalo-b2')
expected=sys.argv[1]
def git(*args):return subprocess.check_output(['git',*args],text=True).strip()
assert git('rev-parse','HEAD')==expected and not git('status','--porcelain=v1')
tree=git('rev-parse','HEAD^{tree}'); paths=git('diff','--name-only','03035fff',expected).splitlines()
hashes={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
workflow=(root/'.github/workflows/hosted-synthetic-acceptance.yml').read_text()
modules=ast.literal_eval(re.search(r'for name in (\["test_hosted_synthetic_source".+?\])\];',workflow).group(1))
runner='''import sys,unittest,json;sys.path[:0]=['procurement/tools','procurement/tests','procurement/src'];import run_tests as r
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in sys.argv[2:]);discovered=suite.countTestCases()
result=unittest.TextTestRunner(verbosity=2,resultclass=r.CountingTextTestResult).run(suite)
record=dict(discovered=discovered,executed=result.testsRun,passed=result.successes,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),expected_failures=len(result.expectedFailures),unexpected_successes=len(result.unexpectedSuccesses));print(json.dumps(record,sort_keys=True))
assert record['discovered']==record['executed']==record['passed']==int(sys.argv[1]) and result.wasSuccessful() and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
'''
py='/tmp/buffalo-review-descendant/venv/bin/python';node=shutil.which('node')
env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','BUFFALO_DIAGNOSTIC_NODE':node}
commands={
'hosted':[py,'-B','-c',runner,'1050',*modules],
'isolated':['/usr/bin/python3.13','-I','-S','-B','-c',runner,'1050',*modules],
'price-components':[py,'-B','-c',runner,'17','test_hosted_synthetic_packet_authority'],
'snapshot-components':[py,'-B','-c',runner,'20','test_hosted_synthetic_recovery_snapshot'],
'startup':[py,'-B','-m','unittest','discover','-v','-s','scripts/tests','-p','test_replit_production_start.py'],
'inert':['/usr/bin/python3.13','-I','-S','-B','procurement/tools/hosted_synthetic_bootstrap.py'],
'discovery':[py,'-B','-c',"import sys,unittest,json;sys.path[:0]=['procurement/tools','procurement/tests','procurement/src'];import run_tests as r;s=unittest.defaultTestLoader.discover('procurement/tests');counts=r._module_test_counts(s);issues=r._module_minimum_errors(counts)+r._module_registration_errors(__import__('pathlib').Path('procurement/tests'));print(json.dumps(dict(discovered=s.countTestCases(),modules=len(counts),issues=issues,executed=0)));assert s.countTestCases()==2617 and not issues"],
}
record={'commit':expected,'tree':tree,'base':'03035fff71d63adab5b36e6f775db3932edae1e7','files':hashes,'results':{},'diagnostic_node':{'path':node,'sha256':hashlib.sha256(pathlib.Path(node).read_bytes()).hexdigest(),'scope':'DIAGNOSTIC_ONLY_NOT_ACCEPTED_BROWSER_IDENTITY'}}
commands['isolated']=['/usr/bin/python3.13','-I','-S','-B','-c',runner,'198','test_hosted_synthetic_runtime','test_hosted_synthetic_postgres_runtime','test_hosted_synthetic_workflow','test_hosted_synthetic_cgroup','test_hosted_synthetic_observer']
commands={name:cmd for name,cmd in commands.items() if name in ('isolated','startup','inert','discovery')}
for name,cmd in commands.items():
 start=time.monotonic();path=logs/('frozen-'+name+'.txt')
 with path.open('w') as output:r=subprocess.run(cmd,cwd=root,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=180)
 record['results'][name]={'returncode':r.returncode,'seconds':round(time.monotonic()-start,3),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 print(json.dumps({name:record['results'][name]}),flush=True)
 (logs/'frozen-validation.json').write_text(json.dumps(record,indent=2)+'\n')
 assert r.returncode==(78 if name=='inert' else 0),name
assert not git('status','--porcelain=v1') and git('rev-parse','HEAD')==expected
assert hashes=={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
record['source_unchanged']=True;record['full_supported_suite']='NOT_RUN';record['live_privilege']='NOT_RUN'
(logs/'frozen-validation.json').write_text(json.dumps(record,indent=2)+'\n')
