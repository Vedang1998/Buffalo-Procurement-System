import hashlib,json,os,pathlib,subprocess,tempfile,time
root=pathlib.Path.cwd();logs=pathlib.Path('/tmp/buffalo-b2');expected=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],text=True).strip()
py='/tmp/buffalo-review-descendant/venv/bin/python';node='/opt/codex/runtimes/codex-primary-runtime/dependencies/node/bin/node'
env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','BUFFALO_DIAGNOSTIC_NODE':node,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0'}
runner='''import sys,unittest,json;sys.path[:0]=['procurement/tools','procurement/tests','procurement/src'];import run_tests as r
names=['test_run_local_staging_acceptance','test_test_runner','test_persistent_mapping_foundation_contract','test_supplier_format_conformance'];modules=[unittest.defaultTestLoader.loadTestsFromName(n) for n in names];print(json.dumps(dict(zip(names,[m.countTestCases() for m in modules]))));suite=unittest.TestSuite(modules);discovered=suite.countTestCases();result=unittest.TextTestRunner(verbosity=2,resultclass=r.CountingTextTestResult).run(suite)
record=dict(discovered=discovered,executed=result.testsRun,passed=result.successes,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped));print(json.dumps(record,sort_keys=True));assert discovered==result.testsRun==result.successes==88 and result.wasSuccessful() and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
'''
with tempfile.TemporaryDirectory(prefix='buffalo-b2-exact-') as temporary:
 copy=pathlib.Path(temporary)/'repo'
 subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','maintenance.auto=false','clone','--quiet','--no-local','--no-hardlinks','--single-branch','--no-tags','--no-checkout',str(root),str(copy)],env=env,check=True,timeout=60,umask=0o022)
 subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(copy),'checkout','--quiet','--detach',expected],env=env,check=True,timeout=60,umask=0o022)
 assert subprocess.check_output(['git','rev-parse','HEAD','HEAD^{tree}'],cwd=copy,text=True).splitlines()==[expected,tree]
 source=root/'procurement/tools/run_local_staging_browser_worker.py';staged=copy/'procurement/tools/run_local_staging_browser_worker.py'
 assert source.read_bytes()==staged.read_bytes() and source.stat().st_mode&0o777==0o600 and staged.stat().st_mode&0o777==0o644
 path=logs/'frozen-related.txt';start=time.monotonic()
 with path.open('w') as output:r=subprocess.run([py,'-B','-c',runner],cwd=copy,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=180)
 record={'commit':expected,'tree':tree,'returncode':r.returncode,'seconds':round(time.monotonic()-start,3),'log_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'original_runner_mode':'0600','disposable_runner_mode':'0644','runner_sha256':hashlib.sha256(staged.read_bytes()).hexdigest(),'existing_permissions_unchanged':True}
record['owned_copy_removed']=not copy.exists()
(logs/'frozen-related.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));assert r.returncode==0
