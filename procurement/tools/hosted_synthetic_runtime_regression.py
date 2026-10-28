"""Explicit credential-free projection/exec regressions; never launcher proof.

Runs only on request from an exact detached checkout. The kernel component
deliberately does not claim the host identity, groups, cgroup or worker-entry
gates. It creates no service/account/database, and never accepts credentials.
"""
from dataclasses import replace
import errno
import fcntl
import hashlib
import json
import os
import resource
import re
import time
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
if packet.get('private_net'):
    assert os.readlink('/proc/self/ns/net')!=packet['parent_net']
    assert {line.split(':',1)[0].strip() for line in open('/proc/net/dev').read().splitlines()[2:]}=={'lo'}
root=r._enter_projection(value)
fd=os.memfd_create('buffalo-component-exec',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
raw=json.dumps({'runtime':value.packet(),'root':root},sort_keys=True,separators=(',',':')).encode()
assert len(raw)<=8192 and os.write(fd,raw)==len(raw)
fcntl.fcntl(fd,fcntl.F_ADD_SEALS,15)
os.set_inheritable(fd,True);os.set_inheritable(value.bundle.descriptor,True)
os.execve(value.executable,[value.executable,'-I','-S','-B','-c',sys.argv[3],r.PROJECTED_TOOLS,str(fd)],
          {'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','PSYCOPG_IMPL':'binary'})
'''


def _omit_dependency(value, path):
    """Test-only deliberate fault, with its own different bundle identity."""
    entries=[entry for entry in runtime._entries(value) if entry.path not in ('/proc','/tmp',path)]
    native=dict(value.native_files);del native[path]
    identity=runtime._identity_bytes(value.generation,value.executable,value.source_files,native,value.source_commit,value.source_tree,policy=value.policy,application=value.application)
    entries=[replace(entry,content=identity,provenance='audit-source:'+hashlib.sha256(identity).hexdigest())
             if entry.path==runtime._IDENTITY_PATH else entry for entry in entries]
    bundle=ns.backend._seal_browser_runtime_bundle(tuple(entries))
    result=runtime.RuntimeBundle(bundle,value.generation,value.executable,value.source_files,native,value.source_commit,value.source_tree,value.policy,value.application)
    result._owner=result;result._token=runtime._TOKEN
    return result


def _descriptor_inventory():
    result={}
    for name in os.listdir('/proc/self/fd'):
        number=int(name)
        try:info=os.fstat(number)
        except OSError as error:
            ns._require(error.errno==errno.EBADF)
            continue
        result[number]=(info.st_dev,info.st_ino,fcntl.fcntl(number,fcntl.F_GETFL),os.get_inheritable(number))
    return result


def _execute(value, *, consumer=_CONSUMER, private_net=False):
    value.verify()
    before=ns._namespaces('self'),[(path,ns._identity(os.stat(path))) for path in ('/','/tmp','/proc')]
    descriptors=_descriptor_inventory()
    fd=os.memfd_create('buffalo-component-input',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
    try:
        raw=json.dumps({'runtime':value.packet(),'private_net':private_net,'parent_net':os.readlink('/proc/self/ns/net')},sort_keys=True,separators=(',',':')).encode()
        ns._require(len(raw)<=8192 and os.write(fd,raw)==len(raw))
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,15)
        # No --fork/--pid or subprocess in the stages: the retained direct
        # subprocess owns this mount namespace until exec/exit. run() kills
        # and waits for that exact child on its bounded timeout.
        result=subprocess.run(['/usr/bin/unshare','--user','--map-root-user','--mount',*(['--net'] if private_net else []),'--',
            sys.executable,'-I','-S','-B','-c',_PROJECTOR,str(Path(__file__).parent),str(fd),consumer],
            pass_fds=(fd,value.bundle.descriptor),env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},
            capture_output=True,timeout=30)
    finally:
        os.close(fd)
        ns._require(before==(ns._namespaces('self'),[(path,ns._identity(os.stat(path))) for path in ('/','/tmp','/proc')]))
        ns._require(_descriptor_inventory()==descriptors)
    return result


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


_APPLICATION_CONSUMER = r'''
import sys,os,json,ssl
sys.path.insert(0,sys.argv[1]);import hosted_synthetic_runtime as r
fd=int(sys.argv[2]);packet=json.loads(os.pread(fd,8193,0));os.close(fd)
value=r.receive_runtime(packet['runtime'])
assert value.policy==r.APPLICATION_POLICY
sys.path[:0]=[value.application['site'],r.PROJECTED_ROOT+'/procurement/src']
# No lifespan, application server, DB connection, HTTP call or credentials.
import procurement_os.api as api
import procurement_os.private_research_app,procurement_os.staging_gateway
import uvicorn,psycopg,httpx,certifi,argon2.low_level
from datetime import datetime
from zoneinfo import ZoneInfo
assert psycopg.pq.__impl__=='binary' and psycopg.pq.version()>0
assert api.health()['ok'] is True and isinstance(api.rules(),dict)
assert api.QualifyingRequest(cases=1,break_unit='CASE').cases==1
hashed=argon2.low_level.hash_secret_raw(b'synthetic-only',b'fixed-synthetic-salt',time_cost=1,
    memory_cost=1024,parallelism=1,hash_len=16,type=argon2.low_level.Type.ID)
assert len(hashed)==16
assert ssl.create_default_context(cafile=certifi.where()).cert_store_stats()['x509_ca']>0
for month,hours in ((1,-5),(7,-4)):
    assert datetime(2026,month,1,tzinfo=ZoneInfo('America/New_York')).utcoffset().total_seconds()==hours*3600
assert datetime(2026,1,1,tzinfo=ZoneInfo('UTC')).utcoffset().total_seconds()==0
proof=r.verify_consumer(value,packet['root']);value.close()
print(json.dumps({'proof':proof,'imports':'PASS','pydantic':'PASS','argon2':'PASS','libpq':'PASS',
    'rules':'PASS','timezones':'PASS','certificates':'PASS','application_startup':'NOT_RUN'},sort_keys=True))
'''


def run_application(binding,root,cache):
    from hosted_synthetic_application_runtime import capture_application
    ns._require(os.geteuid()>0 and os.getegid()>0)
    value=capture_application('a'*64,root,binding,cache)
    try:
        positive=_execute(value,consumer=_APPLICATION_CONSUMER,private_net=True)
        if positive.returncode==1 and positive.stderr.startswith(b'unshare: unshare failed:'):
            return {'application_component':'NOT_RUN','reason':'KERNEL_NAMESPACE_UNAVAILABLE','credential_release':'WITHHELD'}
        if positive.returncode!=0:
            # Credential-free component only. Preserve bounded diagnostics for
            # source-bound diagnosis; never used by credential-bearing launcher.
            sys.stderr.buffer.write(positive.stderr[:16384])
            raise ns.NamespaceProofError('application_component_refused')
        observation=json.loads(positive.stdout);runtime.verify_report_binding(value,observation['proof'])
        cases=[{'case':'sealed_application_imports','result':'PASS','observation':observation}]
        for name,path,diagnostic in (
            ('missing_projected_timezone','/usr/share/zoneinfo/America/New_York',b'ZoneInfoNotFoundError'),
            ('missing_wheel_private_libpq','/buffalo-runtime/site-packages/psycopg_binary.libs/libpq-2be5f14a.so.5.18',
                b'libpq-2be5f14a.so.5.18')):
            omitted=_omit_dependency(value,path)
            try:
                result=_execute(omitted,consumer=_APPLICATION_CONSUMER,private_net=True)
                ns._require(result.returncode!=0 and diagnostic in result.stderr and not result.stdout)
                cases.append({'case':name,'result':'PASS','fault_bundle_sha256':omitted.bundle.sha256,'host_fallback':False})
            finally:omitted.close()
        return {'application_component':'PASS','scope':'SEALED_ROOT_IMPORT_COMPONENT_ONLY','cases':cases,
            'source_commit':binding.commit,'source_tree':binding.tree,'bundle_sha256':value.bundle.sha256,
            'regular_bytes':value.bundle.regular_bytes,'application':value.application,
            'host_identity_groups_cgroup_worker_entry':'NOT_TESTED','credential_release':'WITHHELD',
            'browser_closure':'UNSEALED','execution_authority':False}
    finally:value.close()


def run_postgres_fit(binding,root):
    """Real native capture-fit test; invoke under the fixed60s outer timeout.

    No namespace, native PG execution or account. Process RSS is reported
    separately and excludes unmapped memfd charges; it is not service proof.
    """
    import hosted_synthetic_postgres_runtime as pg
    ns._require(os.geteuid()>0 and os.getegid()>0)
    result={'capture_fit':'NOT_RUN','commit':binding.commit,'tree':binding.tree,
        'native_execution':'NOT_RUN','privileged_processes_started':0,'account_mutations':0,
        'credential_release':'WITHHELD','execution_authority':False,'whole_service_memory':'NOT_MEASURED'}
    release,_=runtime.read_native('/etc/os-release')
    if not (re.search(rb'(?m)^ID=(?:ubuntu|"ubuntu")$',release)
            and re.search(rb'(?m)^VERSION_ID=(?:24\.04|"24\.04")$',release)):
        return dict(result,reason='TARGET_IS_NOT_UBUNTU_24_04')
    if not Path(pg.BIN+'postgres').is_file():return dict(result,reason='FIXED_NATIVE_PG16_PATH_UNAVAILABLE')
    before=_descriptor_inventory();started=time.monotonic()
    try:measurement=pg.capture_phase_fit(hashlib.sha256(os.urandom(32)).hexdigest(),root,binding)
    finally:ns._require(_descriptor_inventory()==before)
    result.update(capture_fit='PASS' if measurement['capacity']=='FIT' else 'REFUSED',measurement=measurement,
        elapsed_seconds=round(time.monotonic()-started,6),process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        rss_scope='PROCESS_PEAK_ONLY_EXCLUDES_UNMAPPED_MEMFD_CHARGES',owned_descriptors_closed=True)
    return result


def main(arguments):
    ns._require((len(arguments)==2 and arguments[0] in ('--run-runtime-component','--measure-postgres-phase'))
                or (len(arguments)==3 and arguments[0]=='--run-application-component'))
    from hosted_synthetic_source import bind_hosted_source,REPOSITORY,REF
    root=Path(__file__).resolve().parents[2]
    binding=bind_hosted_source(root,repository=REPOSITORY,event='push',ref=REF,sha=arguments[1])
    if arguments[0]=='--measure-postgres-phase':
        result=run_postgres_fit(binding,root);key='capture_fit'
    elif arguments[0]=='--run-application-component':
        result=run_application(binding,root,Path(arguments[2]));key='application_component'
    else:result=run(binding,root);key='runtime_component'
    print(json.dumps(result,sort_keys=True))
    return 0 if result[key]=='PASS' else 78


if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
