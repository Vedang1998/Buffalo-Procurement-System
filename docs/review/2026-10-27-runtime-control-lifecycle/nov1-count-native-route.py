"""Read-only T1 control-flow counts. No native processes or filesystem installs.

Uses actual full selected manifest and InstallationLease methods. Replaces only
privileged transport, source-binding work and stage byte IO with synthetic
metadata. This measures command population, not security or target timing.
"""
import sys,time,stat,json,inspect,collections,contextlib,subprocess
from pathlib import Path
from unittest.mock import patch
root=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path.cwd()
sys.path[:0]=[str(root/'procurement/tools'),str(root/'procurement/tests')]
import hosted_synthetic_native_inputs as native
import hosted_synthetic_native_installation as install
from test_hosted_synthetic_native_installation import SyntheticOps,BINDING
rows=native.execution_inventory()['rows'];artifacts=native.require_complete_inventory()['artifacts']
phase='create';counts=collections.defaultdict(collections.Counter);details=collections.defaultdict(collections.Counter)
class CountingOps(SyntheticOps):
 def hit(self,program,why):counts[phase][program]+=1;details[phase][why]+=1
 def run(self,program,*args):
  self.hit(program,program);self.calls.append((program,args))
  paths=args[args.index('--')+1:] if '--' in args else (args[-1],)
  if program=='sha256sum':
   return b''.join((rows[path.split('/view',1)[1]]['sha256']+'  '+path+'\n').encode() for path in paths)
  if program=='mkdir':
   for path in paths:
    if path in self.nodes:raise native.ns.NamespaceProofError('exists')
    self.serial+=1;self.nodes[path]=[1,self.serial,stat.S_IFDIR|0o700,0,0,2,0]
  elif program=='chmod':
   for path in paths:self.nodes[path][2]=(self.nodes[path][2]&~0o777)|int(args[0],8)
  elif program in ('unlink','rmdir','rm'):
   for path in paths:del self.nodes[path]
  else:raise AssertionError(program)
  return b''
 def stream(self,path,row,chunks):
  self.hit('dd','stream');self.serial+=1;self.nodes[path]=[1,self.serial,stat.S_IFREG|0o600,0,0,1,row['bytes']]
 def observe(self,path):
  self.hit('stat','observe:'+inspect.currentframe().f_back.f_code.co_name)
  return tuple(self.nodes[path])
 def observe_many(self,paths,*,follow=False):
  for offset in range(0,len(paths),64):self.hit('stat','observe_many:'+inspect.currentframe().f_back.f_code.co_name)
  return [tuple(self.nodes[path]) for path in paths]
 def inventory(self,root):self.hit('find','inventory');return super().inventory(root)
 def absent(self,path):details[phase]['unprivileged_lstat_absence']+=1;return super().absent(path)
@contextlib.contextmanager
def stream(stage,row):yield iter(())
def close(stage):stage.closed=True
snapshots={}
with patch.object(install,'_NativeOps',CountingOps),patch.object(install,'_source_binding'),patch.object(native.Stage,'verified_stream',stream),patch.object(native.Stage,'close',close):
 lease=install.InstallationLease._create('f'*32,deadline=time.monotonic()+60,binding=BINDING)
 snapshots['create_owned']=len(lease.owned)
 phase='import'
 for digest in sorted(artifacts):
  selected=[r for r in rows.values() if r['artifact_sha256']==digest]
  s=object.__new__(native.Stage);s.closed=False
  lease.import_stage(s,selected)
 snapshots['import_owned']=len(lease.owned);snapshots['directories']=sum(stat.S_ISDIR(x[2]) for x in lease.owned.values())
 snapshots['inventory_name_bytes']=sum(len(n)+1 for n in lease.owned)
 phase='finish';lease.finish()
 phase='cleanup';lease.close()
 snapshots['closed']=lease.closed;snapshots['remaining_nodes']=len(lease.ops.nodes)
identity=subprocess.run(['git','rev-parse','HEAD','HEAD^{tree}'],cwd=root,capture_output=True,text=True,check=True).stdout.splitlines()
print(json.dumps({'head':identity[0],'tree':identity[1],'scope':'Exact current InstallationLease control flow, synthetic transport and metadata, no file bytes/native privilege/consumer executions','files':len(rows),'artifacts':len(artifacts),'counts':{k:dict(v) for k,v in counts.items()},'totals':{k:sum(v.values()) for k,v in counts.items()},'grand_total':sum(sum(v.values()) for v in counts.values()),'details':{k:dict(v) for k,v in details.items()},'snapshots':snapshots},sort_keys=True,indent=2))
