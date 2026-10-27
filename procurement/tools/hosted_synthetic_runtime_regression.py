"""Explicit credential-free projection/exec regressions; never launcher proof.

Runs only on request from an exact detached checkout. The kernel component
deliberately does not claim the host identity, groups, cgroup or worker-entry
gates. It creates no service/account/database, and never accepts credentials.
"""
from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosted_synthetic_runtime as runtime
import hosted_synthetic_namespace as ns


_CONSUMER = r'''
import sys,os,json
sys.path.insert(0,sys.argv[1]);import hosted_synthetic_runtime as r
fd=int(sys.argv[2]);packet=json.loads(os.pread(fd,8193,0));os.close(fd)
value=r.receive_runtime(packet['runtime'])
proof=r.verify_consumer(value,packet['root']);value.close()
print(json.dumps(proof,sort_keys=True))
'''
_PROJECTOR = r'''
import sys,os,json,fcntl
sys.path.insert(0,sys.argv[1]);import hosted_synthetic_runtime as r
fd=int(sys.argv[2]);packet=json.loads(os.pread(fd,8193,0));os.close(fd)
value=r.receive_runtime(packet['runtime'])
root=r._enter_projection(value)
fd=os.memfd_create('buffalo-component-exec',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
raw=json.dumps({'runtime':value.packet(),'root':root},sort_keys=True,separators=(',',':')).encode()
assert len(raw)<=8192 and os.write(fd,raw)==len(raw)
fcntl.fcntl(fd,fcntl.F_ADD_SEALS,15)
os.set_inheritable(fd,True);os.set_inheritable(value.bundle.descriptor,True)
os.execve(value.executable,[value.executable,'-I','-S','-B','-c',sys.argv[3],r.PROJECTED_TOOLS,str(fd)],
          {'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'})
'''


def _omit_dependency(value, path):
    """Test-only deliberate fault, with its own different bundle identity."""
    entries=[entry for entry in runtime._entries(value) if entry.path not in ('/proc','/tmp',path)]
    native=dict(value.native_files);del native[path]
    identity=runtime._identity_bytes(value.generation,value.executable,value.source_files,native,value.source_commit,value.source_tree)
    entries=[replace(entry,content=identity,provenance='audit-source:'+hashlib.sha256(identity).hexdigest())
             if entry.path==runtime._IDENTITY_PATH else entry for entry in entries]
    bundle=ns.backend._seal_browser_runtime_bundle(tuple(entries))
    result=runtime.RuntimeBundle(bundle,value.generation,value.executable,value.source_files,native,value.source_commit,value.source_tree)
    result._owner=result;result._token=runtime._TOKEN
    return result


def _execute(value):
    value.verify()
    fd=os.memfd_create('buffalo-component-input',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
    try:
        raw=json.dumps({'runtime':value.packet()},sort_keys=True,separators=(',',':')).encode()
        ns._require(len(raw)<=8192 and os.write(fd,raw)==len(raw))
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,15)
        # No --fork/--pid or subprocess in the stages: the retained direct
        # subprocess owns this mount namespace until exec/exit. run() kills
        # and waits for that exact child on its bounded timeout.
        return subprocess.run(['/usr/bin/unshare','--user','--map-root-user','--mount','--',
            sys.executable,'-I','-S','-B','-c',_PROJECTOR,str(Path(__file__).parent),str(fd),_CONSUMER],
            pass_fds=(fd,value.bundle.descriptor),env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},
            capture_output=True,timeout=30)
    finally:os.close(fd)


def run(binding, root):
    ns._require(os.geteuid()>0 and os.getegid()>0)
    value=runtime.capture_runtime('a'*64,root,binding)
    try:
        positive=_execute(value)
        if positive.returncode==1 and positive.stderr.startswith(b'unshare: unshare failed:'):
            return {'runtime_component':'NOT_RUN','reason':'KERNEL_NAMESPACE_UNAVAILABLE','credential_release':'WITHHELD'}
        ns._require(positive.returncode==0)
        proof=json.loads(positive.stdout);runtime.verify_report_binding(value,proof)
        cases=[{'case':'sealed_projected_exec','result':'PASS','proof':proof}]
        image=runtime.parse_elf(ns._read(value.executable,runtime.MAX_BYTES))
        interpreter=image['interpreter']
        libc=[path for path in value.native_files if Path(path).name=='libc.so.6']
        ns._require(len(libc)==1)
        for name,path,code,diagnostic in (
            ('missing_projected_interpreter',interpreter,1,b'FileNotFoundError'),
            ('missing_projected_libc',libc[0],127,b'error while loading shared libraries: libc.so.6:')):
            ns._require(Path(path).is_file())  # Host copy still exists.
            omitted=_omit_dependency(value,path)
            try:
                result=_execute(omitted)
                ns._require(result.returncode==code and diagnostic in result.stderr and not result.stdout)
                cases.append({'case':name,'result':'PASS','expected_exec_refusal':code,
                              'fault_bundle_sha256':omitted.bundle.sha256,'host_fallback':False})
            finally:omitted.close()
        return {'runtime_component':'PASS','scope':'KERNEL_PROJECTION_AND_EXEC_ONLY','cases':cases,
                'source_commit':binding.commit,'source_tree':binding.tree,
                'host_identity_groups_cgroup_worker_entry':'NOT_TESTED','credential_release':'WITHHELD',
                'execution_authority':False}
    finally:value.close()


def main(arguments):
    ns._require(len(arguments)==2 and arguments[0]=='--run-runtime-component')
    from hosted_synthetic_source import bind_hosted_source,REPOSITORY,REF
    root=Path(__file__).resolve().parents[2]
    binding=bind_hosted_source(root,repository=REPOSITORY,event='push',ref=REF,sha=arguments[1])
    result=run(binding,root)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['runtime_component']=='PASS' else 78


if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
