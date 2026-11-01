"""At most 50 read-only native stat calls and a 2s outer sample budget; no sudo."""
import hashlib,json,os,platform,statistics,subprocess,time
from pathlib import Path
limit=time.monotonic()+2.0;samples={'single':[],'batch64':[]};runs=0
prefix=('/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8','/usr/bin/timeout','--signal=TERM','--kill-after=0.1s','0.25s','/usr/bin/stat','--printf=%d %i %f %u %g %h %s\\n','--')
started=time.monotonic()
for i in range(50):
 remaining=limit-time.monotonic()
 if remaining<=0:break
 label='single' if i<25 else 'batch64';paths=('/usr/bin/stat',)*(1 if label=='single' else 64)
 before=time.perf_counter_ns()
 p=subprocess.run((*prefix,*paths),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},timeout=min(.5,remaining),check=True)
 elapsed=(time.perf_counter_ns()-before)/1e6
 assert not p.stderr and len(p.stdout.splitlines())==len(paths)
 samples[label].append(elapsed);runs+=1
print(json.dumps({'scope':'Current Codex Cloud unprivileged env/timeout/stat transport only, not sudo/account/systemd/artifact/target fit','operator_uid':os.geteuid(),'host':{'system':platform.system(),'kernel':platform.release(),'machine':platform.machine(),'os_release':Path('/etc/os-release').read_text(),'python':platform.python_version()},'tools':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in ('/usr/bin/env','/usr/bin/timeout','/usr/bin/stat')},'argv_prefix':prefix,'runs':runs,'elapsed_seconds':time.monotonic()-started,'sample_budget_seconds':2,'iteration_limit':50,'measurements_ms':{k:{'n':len(v),'min':min(v),'median':statistics.median(v),'max':max(v),'samples':v} for k,v in samples.items() if v}},sort_keys=True,indent=2))
