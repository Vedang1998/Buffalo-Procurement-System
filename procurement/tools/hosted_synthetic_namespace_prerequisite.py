"""One exact credential-free namespace syscall; no fallback or policy mutation.

Dormant callable for the reviewed dedicated supervisor. A successful syscall
is only a prerequisite: no maps, private proc, child PID1 or payload is created.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import selectors
import signal
import time
import sys

import hosted_synthetic_namespace as ns
from hosted_synthetic_source import _resource_operation
from hosted_synthetic_launcher import inspect_host_identity

_POLICY={
 '/proc/sys/kernel/apparmor_restrict_unprivileged_userns':'integer',
 '/proc/sys/kernel/unprivileged_userns_clone':'integer',
 '/proc/sys/user/max_user_namespaces':'integer',
 '/proc/sys/user/max_mnt_namespaces':'integer',
 '/proc/sys/user/max_pid_namespaces':'integer',
 '/proc/sys/user/max_net_namespaces':'integer',
 '/sys/module/apparmor/parameters/enabled':'apparmor',
 '/proc/self/attr/current':'profile',
}
_CHILD='''import ctypes,errno,json,os,sys,signal
sys.path.insert(0,sys.argv[1])
from hosted_synthetic_launcher import inspect_host_identity
from hosted_synthetic_namespace_prerequisite import observe_policy
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(1,int(signal.SIGKILL),0,0,0)!=0 or os.getppid()!=int(sys.argv[2]): raise RuntimeError("parent binding refused")
identity=inspect_host_identity()
policy=observe_policy()
if libc.prctl(4,1,0,0,0)!=0: raise RuntimeError("dumpable prerequisite refused")
flags=os.CLONE_NEWUSER|os.CLONE_NEWNS|os.CLONE_NEWPID|os.CLONE_NEWNET
try:
 os.unshare(flags)
 result={"operation":"unshare_USER_MNT_PID_NET","attempts":1,"errno":0,"errno_name":None,"result":"SYSCALL_SUCCEEDED_ONLY"}
except OSError as error:
 result={"operation":"unshare_USER_MNT_PID_NET","attempts":1,"errno":error.errno,"errno_name":errno.errorcode.get(error.errno),"result":"SYSCALL_REFUSED"}
result["attempt_context_policy"]=policy
print(json.dumps(result,sort_keys=True,separators=(",",":")))
'''


def observe_policy():
    result={}
    for path,kind in _POLICY.items():
        try:raw=ns._read(path,256)
        except OSError as error:
            ns._require(type(error.errno) is int and 0<error.errno<=4095)
            result[path]={'state':'MISSING' if error.errno==errno.ENOENT else 'DENIED' if error.errno in (errno.EPERM,errno.EACCES) else 'READ_ERROR','errno':error.errno}
            continue
        except ns.NamespaceProofError:
            result[path]={'state':'MALFORMED_OR_OVERSIZED'};continue
        if not raw or len(raw)>256 or not raw.endswith(b'\n'):
            result[path]={'state':'MALFORMED_OR_OVERSIZED'};continue
        value=raw[:-1]
        if kind=='integer':valid=value.isdigit() and len(value)<=10
        elif kind=='apparmor':valid=value in (b'Y',b'N')
        else:valid=all(32<=x<=126 for x in value)
        if not valid:result[path]={'state':'MALFORMED_OR_OVERSIZED'};continue
        result[path]={'state':'OBSERVED','sha256':hashlib.sha256(raw).hexdigest()}
        if kind!='profile':result[path]['value']=int(value) if kind=='integer' else value.decode()
        else:result[path]['profile']='unconfined' if value==b'unconfined' else 'REDACTED_PROFILE'
    return result


@_resource_operation
def _run_probe(command):
    process=None;descriptor=None;output=None;error=None;result=None
    try:
        process=subprocess.Popen(command,env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,bufsize=0)
        descriptor=ns._acquire(lambda:os.pidfd_open(process.pid,0))
        output={process.stdout:bytearray(),process.stderr:bytearray()};deadline=time.monotonic()+3
        with selectors.DefaultSelector() as selector:
            for stream in output:selector.register(stream,selectors.EVENT_READ)
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired('fixed namespace prerequisite',3)
                ready=selector.select(remaining)
                if not ready:raise subprocess.TimeoutExpired('fixed namespace prerequisite',3)
                for key,_ in ready:
                    data=os.read(key.fileobj.fileno(),min(4096,8193-len(output[key.fileobj])))
                    if not data:selector.unregister(key.fileobj);continue
                    output[key.fileobj].extend(data)
                    ns._require(len(output[key.fileobj])<=8192)
        code=process.wait(timeout=max(.001,deadline-time.monotonic()))
        result=subprocess.CompletedProcess(command,code,bytes(output[process.stdout]),bytes(output[process.stderr]))
    except BaseException as failure:error=failure
    cleanup=[]
    if process is not None:
        try:
            if process.poll() is None:
                if descriptor is not None:
                    descriptor.verify();signal.pidfd_send_signal(descriptor.number,signal.SIGKILL)
                else:process.kill()
            process.wait(timeout=2)
        except BaseException as failure:cleanup.append(failure)
        for stream in (process.stdout,process.stderr):
            if stream is not None:
                try:stream.close()
                except BaseException as failure:cleanup.append(failure)
    if descriptor is not None:
        try:descriptor.close()
        except BaseException as failure:cleanup.append(failure)
    if cleanup:raise ns.NamespaceCleanupError('namespace_prerequisite_cleanup_failed') from (error or cleanup[0])
    if error is not None:raise error
    return result


def observe_prerequisite(binding):
    identity=inspect_host_identity()
    before=observe_policy()
    command=[sys.executable,'-I','-S','-B','-c',_CHILD,str(Path(__file__).parent),str(os.getpid())]
    try:
        child=_run_probe(command)
    except subprocess.TimeoutExpired:
        operation={'result':'HARNESS_TIMEOUT','errno':None,'attempts':'NOT_CONFIRMED'}
    else:
        if child.returncode!=0 or child.stderr or len(child.stdout)>8192:
            operation={'result':'HARNESS_FAILURE','errno':None,'returncode':child.returncode,'attempts':'NOT_CONFIRMED'}
        else:
            try:operation=json.loads(child.stdout)
            except (ValueError,UnicodeError):
                operation={'result':'HARNESS_MALFORMED_OUTPUT','errno':None,'attempts':'NOT_CONFIRMED'}
                return {'source_commit':binding.commit,'source_tree':binding.tree,'identity':identity,
                    'policy_before':before,'policy_after':observe_policy(),'policy_stable':False,'operation':operation,
                    'policy_cause':'UNCONFIRMED','maps_mounts_payloads':0,'credential_release':'WITHHELD','execution_authority':False}
            ns._require(type(operation) is dict and set(operation)=={'operation','attempts','errno','errno_name','result','attempt_context_policy'}
                and operation['operation']=='unshare_USER_MNT_PID_NET' and type(operation['attempts']) is int and operation['attempts']==1
                and type(operation['attempt_context_policy']) is dict and set(operation['attempt_context_policy'])==set(_POLICY)
                and type(operation['errno']) is int and 0<=operation['errno']<=4095
                and operation['errno_name']==errno.errorcode.get(operation['errno'])
                and operation['result']==('SYSCALL_SUCCEEDED_ONLY' if operation['errno']==0 else 'SYSCALL_REFUSED'))
    after=observe_policy()
    return {'source_commit':binding.commit,'source_tree':binding.tree,'identity':identity,
        'policy_before':before,'policy_after':after,'policy_stable':before==after,'operation':operation,
        'attempt_context_matches_supervisor':operation.get('attempt_context_policy')==before,
        'policy_cause':'UNCONFIRMED; errno and policy observations alone do not attribute an LSM denial',
        'maps_mounts_payloads':0,'credential_release':'WITHHELD','execution_authority':False}
