import ast,hashlib,json,os,pathlib,re,shutil,subprocess,tempfile,time
root=pathlib.Path.cwd();out=pathlib.Path('/tmp/buffalo-nov3');commit='216169bfe82a4275ac78fcb54a716d400fe564c8'
py='/tmp/buffalo-nov3/venv/bin/python'
def git(*args):return subprocess.check_output(['git',*args],text=True).strip()
assert git('rev-parse','HEAD')==commit and not git('status','--porcelain=v1')
record={'commit':commit,'tree':git('rev-parse','HEAD^{tree}'),'results':{}}
env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','BUFFALO_DIAGNOSTIC_NODE':shutil.which('node'),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0'}
runner="""import sys,unittest,json;sys.path[:0]=['procurement/tools','procurement/tests','procurement/src'];import run_tests as r
suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in sys.argv[2:]);n=suite.countTestCases();result=unittest.TextTestRunner(verbosity=2,resultclass=r.CountingTextTestResult).run(suite)
record=dict(discovered=n,executed=result.testsRun,passed=result.successes,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),expected_failures=len(result.expectedFailures),unexpected_successes=len(result.unexpectedSuccesses));print(json.dumps(record,sort_keys=True));assert n==result.testsRun==result.successes==int(sys.argv[1]) and result.wasSuccessful() and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
"""
def run(name,args,cwd=root,expected=0):
 path=out/(commit[:7]+'-'+name+'.txt');start=time.monotonic()
 with path.open('w') as stream:r=subprocess.run(args,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=180)
 record['results'][name]={'returncode':r.returncode,'seconds':time.monotonic()-start,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 (out/'frozen-validation.json').write_text(json.dumps(record,indent=2)+'\n');print(name,r.returncode,flush=True);assert r.returncode==expected
modules=(out/'focused_modules.txt').read_text().split()
run('focused',[py,'-B','-c',runner,'1235',*modules])
run('isolated',['/usr/bin/python3.13','-I','-S','-B','-c',runner,'298','test_hosted_synthetic_runtime','test_hosted_synthetic_postgres_runtime','test_hosted_synthetic_workflow','test_hosted_synthetic_native_inputs','test_hosted_synthetic_native_installation','test_hosted_synthetic_native_measurement','test_hosted_synthetic_native_route','test_hosted_synthetic_native_batch','test_hosted_synthetic_native_provenance'])
run('inert',['/usr/bin/python3.13','-I','-S','-B','procurement/tools/hosted_synthetic_bootstrap.py'],expected=78)
run('discovery',[py,'-B','-c',"import sys,unittest,json,pathlib;sys.path[:0]=['procurement/tools','procurement/tests','procurement/src'];import run_tests as r;s=unittest.defaultTestLoader.discover('procurement/tests');c=r._module_test_counts(s);issues=r._module_minimum_errors(c)+r._module_registration_errors(pathlib.Path('procurement/tests'));print(json.dumps(dict(discovered=s.countTestCases(),modules=len(c),issues=issues,executed=0)));assert s.countTestCases()==2799 and not issues"])
run('startup',[py,'-B','-m','unittest','discover','-s','scripts/tests','-p','test_replit_production_start.py','-v'])
with tempfile.TemporaryDirectory(prefix='buffalo-h123-related-') as parent:
 clone=pathlib.Path(parent)/'repo'
 subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-c','maintenance.auto=false','clone','--quiet','--no-local','--no-hardlinks','--single-branch','--no-tags','--no-checkout',str(root),str(clone)],env=env,check=True,timeout=60,umask=0o022)
 subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(clone),'checkout','--quiet','--detach',commit],env=env,check=True,timeout=60,umask=0o022)
 run('related',[py,'-B','-c',runner,'88','test_run_local_staging_acceptance','test_test_runner','test_persistent_mapping_foundation_contract','test_supplier_format_conformance'],clone)
record['related_disposable_clone_removed']=not clone.exists();record['clean_after']=not git('status','--porcelain=v1');assert record['clean_after']
(out/'frozen-validation.json').write_text(json.dumps(record,indent=2)+'\n')
