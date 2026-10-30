import hashlib,json,os,pathlib,subprocess,tempfile,time,shutil
root=pathlib.Path.cwd();logs=pathlib.Path('/tmp/buffalo-v3');expected='34e8f3ad16ef31e8ecafc2d915b76f16a9571b98'
py='/tmp/buffalo-review-descendant/venv/bin/python';env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','BUFFALO_DIAGNOSTIC_NODE':shutil.which('node'),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0'}
runner='''import sys,unittest,json;sys.path[:0]=['procurement/tools','procurement/tests','procurement/src'];import run_tests as r
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in sys.argv[2:]);discovered=suite.countTestCases();result=unittest.TextTestRunner(verbosity=2,resultclass=r.CountingTextTestResult).run(suite)
record=dict(discovered=discovered,executed=result.testsRun,passed=result.successes,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),expected_failures=len(result.expectedFailures),unexpected_successes=len(result.unexpectedSuccesses));print(json.dumps(record,sort_keys=True));assert record['discovered']==record['executed']==record['passed']==int(sys.argv[1]) and result.wasSuccessful() and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
'''
record={'commit':expected,'tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],text=True).strip(),'results':{}}
def run(name,command,cwd,rc=0):
 path=logs/('34e8f3a-'+name+'.txt');start=time.monotonic()
 with path.open('w') as out:r=subprocess.run(command,cwd=cwd,env=env,stdout=out,stderr=subprocess.STDOUT,timeout=180)
 record['results'][name]={'returncode':r.returncode,'seconds':time.monotonic()-start,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 (logs/'34e8f3a-other-validation.json').write_text(json.dumps(record,indent=2)+'\n');print(name,r.returncode,flush=True);assert r.returncode==rc
run('isolated',['/usr/bin/python3.13','-I','-S','-B','-c',runner,'222','test_hosted_synthetic_runtime','test_hosted_synthetic_postgres_runtime','test_hosted_synthetic_workflow','test_hosted_synthetic_native_inputs','test_hosted_synthetic_native_installation','test_hosted_synthetic_native_measurement'],root)
run('inert',['/usr/bin/python3.13','-I','-S','-B','procurement/tools/hosted_synthetic_bootstrap.py'],root,78)
run('discovery',[py,'-B','-c',"import sys,unittest,json;sys.path[:0]=['procurement/tools','procurement/tests','procurement/src'];import run_tests as r;s=unittest.defaultTestLoader.discover('procurement/tests');counts=r._module_test_counts(s);issues=r._module_minimum_errors(counts)+r._module_registration_errors(__import__('pathlib').Path('procurement/tests'));print(json.dumps(dict(discovered=s.countTestCases(),modules=len(counts),issues=issues,executed=0)));assert s.countTestCases()==2723 and not issues"],root)
with tempfile.TemporaryDirectory(prefix='buffalo-memory-related-') as temporary:
 copy=pathlib.Path(temporary)/'repo'
 subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','maintenance.auto=false','clone','--quiet','--no-local','--no-hardlinks','--single-branch','--no-tags','--no-checkout',str(root),str(copy)],env=env,check=True,timeout=60,umask=0o022)
 subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(copy),'checkout','--quiet','--detach',expected],env=env,check=True,timeout=60,umask=0o022)
 assert subprocess.check_output(['/usr/bin/git','-C',str(copy),'rev-parse','HEAD','HEAD^{tree}'],text=True,env=env).splitlines()==[expected,record['tree']]
 run('related',[py,'-B','-c',runner,'88','test_run_local_staging_acceptance','test_test_runner','test_persistent_mapping_foundation_contract','test_supplier_format_conformance'],copy)
record['disposable_clone_removed']=not copy.exists()
assert not subprocess.check_output(['git','status','--porcelain=v1'],cwd=root)
(logs/'34e8f3a-other-validation.json').write_text(json.dumps(record,indent=2)+'\n')
