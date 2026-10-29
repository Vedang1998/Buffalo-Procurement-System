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
import stat
import sysconfig
import time
import tracemalloc
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


def _shared_job_memory():
    """Read only the existing job cgroup; never claim exclusive attribution."""
    from hosted_synthetic_cgroup import CgroupProofError,CgroupSocketPressureError,_require_zero_socket
    result={'scope':'SHARED_JOB_CGROUP_NOT_OWNED_SERVICE','status':'NOT_OBSERVED'}
    try:
        raw=ns._read('/proc/self/cgroup',4096)
        matched=re.fullmatch(rb'0::(/[A-Za-z0-9_.@/\\-]*)\n',raw)
        ns._require(matched is not None and b'..' not in matched[1].split(b'/'))
        path=Path('/sys/fs/cgroup')/matched[1].decode().lstrip('/')
        identity=ns._identity(path.stat());values={}
        for name in ('memory.current','memory.peak','memory.events'):
            data=ns._read(str(path/name),4096)
            if name=='memory.events':
                from hosted_synthetic_cgroup import parse_memory_events
                keys=[line.partition(b' ')[0] for line in data.splitlines()]
                if len(keys)<=32 and all(re.fullmatch(rb'[a-z_]{1,32}',key) for key in keys):
                    result['observed_event_keys']=[key.decode('ascii') for key in keys]
                values[name]=parse_memory_events(data)
                _require_zero_socket(values[name])
            else:
                ns._require(re.fullmatch(rb'[0-9]{1,20}\n',data) is not None)
                values[name]=int(data)
        ns._require(ns._identity(path.stat())==identity)
        result.update(status='OBSERVED',identity=list(identity),values=values)
    except CgroupSocketPressureError:result['status']='REFUSED_SOCKET_PRESSURE'
    except CgroupProofError:result['status']='REFUSED_COUNTER_SCHEMA'
    except (OSError,ns.NamespaceProofError):pass
    return result


def _capture_failure_frames(error):
    """Fixed source locations only; never exception strings or frame locals."""
    allowed={str(Path(__file__).resolve().parent/name) for name in (
        'hosted_synthetic_runtime.py','hosted_synthetic_postgres_runtime.py',
        'hosted_synthetic_runtime_regression.py','hosted_synthetic_source.py',
        'hosted_synthetic_namespace.py','run_local_staging_acceptance.py')}
    frames=[];trace=error.__traceback__
    for _ in range(64):
        if trace is None:break
        filename=trace.tb_frame.f_code.co_filename
        if filename in allowed and 0<trace.tb_lineno<20000:
            frames.append({'file':'procurement/tools/'+Path(filename).name,'line':trace.tb_lineno})
        trace=trace.tb_next
    return frames[-8:]


def _native_input_chain(path):
    """Observe fixed native inputs; this neither establishes trust nor changes it."""
    result={'status':'NOT_OBSERVED','scope':'READ_ONLY_NATIVE_METADATA_NOT_TRUST_APPROVAL'}
    def public(value):
        return (type(value) is str and re.fullmatch(r'/[A-Za-z0-9_./+\-]+',value)
                and '..' not in Path(value).parts
                and value.startswith(('/usr/','/lib/','/lib64/','/opt/hostedtoolcache/')))
    if not public(path):return result
    try:
        requested=Path(path);resolved=requested.resolve(strict=True)
        if not public(str(resolved)):return result
        chains={}
        for name,selected in (('requested',requested),('canonical',resolved)):
            paths=(selected,*selected.parents)
            if len(paths)>16:return result
            rows=[]
            for item in paths:
                info=item.lstat();link=stat.S_ISLNK(info.st_mode)
                rows.append({'path':str(item),'uid':info.st_uid,'gid':info.st_gid,
                    'mode':oct(stat.S_IMODE(info.st_mode)),'symlink':link,
                    'root_owned_no_write':info.st_uid==0 and (link or not info.st_mode&0o022)})
            chains[name]=rows
        result.update(status='OBSERVED',chains=chains)
    except OSError as error:result.update(status='UNREADABLE',errno=error.errno)
    return result


def _postgres_data_inputs():
    """Bounded metadata for the existing fixed data list, with shared paths once."""
    import hosted_synthetic_postgres_runtime as pg
    rows={}
    for name in pg._DATA:
        value=_native_input_chain(pg.SHARE+name)
        if value['status']!='OBSERVED':return {'status':'NOT_OBSERVED'}
        for chain in value['chains'].values():
            for row in chain:
                if row['path'] in rows and rows[row['path']]!=row:
                    return {'status':'METADATA_CHANGED'}
                rows[row['path']]=row
                ns._require(len(rows)<=64)
    return {'status':'OBSERVED','scope':'FIXED_PG_DATA_METADATA_NOT_CAPTURE_OR_TRUST_APPROVAL',
            'chains':{'distinct_observed_paths':[rows[key] for key in sorted(rows)]}}


def _memory_stage(name):
    current,peak=tracemalloc.get_traced_memory()
    return {'stage':name,'traced_current_bytes':current,'traced_peak_bytes':peak,
            'process_cumulative_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            'child_process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024,
            'shared_job':_shared_job_memory()}


def _measure_postgres_input(binding,root):
    """Independent fixed native-input measurement, never an alternative root."""
    import hosted_synthetic_postgres_runtime as pg
    value=None;failure=None;report=None
    try:
        value=pg.capture_postgres(hashlib.sha256(os.urandom(32)).hexdigest(),root,binding)
        value.verify()
        report={'status':'PASS','scope':'PG_ONLY_CAPTURE_NOT_COMPOSED_PHASE_OR_EXECUTION',
            'policy':value.policy,'maximum_regular_bytes':runtime.MAX_BYTES,
            'regular_bytes':value.bundle.regular_bytes,'entries':value.bundle.entries,
            'bundle_sha256':value.bundle.sha256,'manifest_sha256':value.bundle.manifest_sha256,
            'memfd_allocated_bytes':os.fstat(value.bundle.descriptor).st_blocks*512,
            'postgres_sha256':value.native_files[pg.BIN+'postgres'],
            'native_inventory_sha256':hashlib.sha256(json.dumps(value.native_files,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    except BaseException as error:failure=error
    if value is not None:
        try:value.close()
        except BaseException as error:raise ns.NamespaceCleanupError('postgres_input_measurement_cleanup_refused') from (failure or error)
    if failure is not None:raise failure
    report['owned_capture_closed']=True
    return report


def _measure_inputs(binding,root,native_inputs):
    """Do not repeat a capture whose fixed native trust precondition is disproven."""
    refused=any(value['status']=='OBSERVED' and any(not row['root_owned_no_write']
        for rows in value['chains'].values() for row in rows)
        for name,value in native_inputs.items() if name in ('interpreter','stdlib','libdir'))
    pg_refused=any(value['status']=='OBSERVED' and any(not row['root_owned_no_write']
        for rows in value['chains'].values() for row in rows)
        for name,value in native_inputs.items() if name in ('postgres','postgres_data'))
    if refused or pg_refused:
        return {'capacity':'NOT_MEASURED_INPUT_TRUST_REFUSED',
            'postgres_input':({'status':'NOT_RUN_NATIVE_INPUT_TRUST_REFUSED'} if pg_refused
                              else _measure_postgres_input(binding,root))}
    import hosted_synthetic_postgres_runtime as pg
    return pg.capture_phase_fit(hashlib.sha256(os.urandom(32)).hexdigest(),root,binding)


def _measure_composed_phase(binding,root,measurement):
    """Execute real composition/codec/adoption, never PostgreSQL or projection."""
    import hosted_synthetic_postgres_runtime as pg
    value=received=None;descriptor=None;failure=None;errors=[];report=None;stages=[]
    try:
        tracemalloc.reset_peak();started=time.monotonic()
        value=pg.capture_postgres_phase(measurement['generation'],root,binding)
        stages.append(_memory_stage('CAPTURE_COMPOSE_SERIALIZE_AND_VERIFY'))
        ns._require(value.bundle.regular_bytes==measurement['composed_regular_bytes']
            and value.bundle.entries==measurement['composed_entries']
            and value.postgres['diagnostic_bundle_sha256']==measurement['diagnostic_bundle_sha256']
            and value.postgres['postgres_bundle_sha256']==measurement['postgres_bundle_sha256'])
        allocated=os.fstat(value.bundle.descriptor).st_blocks*512
        report={'composition':'PASS','regular_bytes':value.bundle.regular_bytes,'entries':value.bundle.entries,
            'bundle_sha256':value.bundle.sha256,'manifest_sha256':value.bundle.manifest_sha256,
            'phase_memfd_allocated_bytes':allocated,'phase_memfd_logical_bytes':value.bundle.size,
            'source_bound_input_hashes_match_fit':True,'fit_and_phase_are_separate_captures':True,
            'capture_compose_seconds':round(time.monotonic()-started,6),
            'interpreter':{'path':value.executable,'version':sys.version.split()[0],'sha256':value.native_files[value.executable]},
            'postgres':{'path':pg.BIN+'postgres','sha256':value.native_files[pg.BIN+'postgres']},
            'native_inventory_sha256':hashlib.sha256(json.dumps(value.native_files,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        tracemalloc.reset_peak();packet=value.packet()
        descriptor=os.dup(value.bundle.descriptor);packet['bundle']['descriptor']=descriptor
        received=pg.receive_postgres_phase(packet);descriptor=None
        ns._require(received.bundle.sha256==value.bundle.sha256)
        stages.append(_memory_stage('SAME_PROCESS_DESCRIPTOR_ADOPTION'))
        report.update(adoption='PASS',adoption_scope='ACTUAL_RECEIVER_SAME_PROCESS_NOT_EXECUTION',stages=stages)
    except BaseException as error:failure=error
    if descriptor is not None:
        # Receiver may have closed a transferred descriptor on refusal. Never
        # close a number without proving it is still the exact owned bundle.
        try:os.fstat(descriptor)
        except OSError as error:
            if error.errno!=errno.EBADF:errors.append(error)
        else:
            try:ns._require(ns.backend._close_browser_runtime_bundle_descriptor(descriptor,value.bundle.target,(value.bundle.device,value.bundle.inode)))
            except BaseException as error:errors.append(error)
    for owned in (received,value):
        if owned is not None:
            try:owned.close()
            except BaseException as error:errors.append(error)
    if errors:raise ns.NamespaceCleanupError('postgres_measurement_cleanup_refused') from (failure or errors[0])
    if failure is not None:raise failure
    ns._require(report is not None)
    report['owned_captures_and_adoption_closed']=True
    return report


def run_postgres_fit(binding,root):
    """Fixed60s outer bound; read-only real capture plus memory components.

    No namespace, PG execution, account or service. RSS/heap and owned memfd
    allocation are separate. Shared-job counters never prove owned-service fit.
    """
    import hosted_synthetic_postgres_runtime as pg
    from hosted_synthetic_source import HostedSourceError
    ns._require(os.geteuid()>0 and os.getegid()>0 and not tracemalloc.is_tracing())
    result={'capture_fit':'NOT_RUN','commit':binding.commit,'tree':binding.tree,
        'native_execution':'NOT_RUN','privileged_processes_started':0,'account_mutations':0,
        'credential_release':'WITHHELD','execution_authority':False,'whole_service_memory':'NOT_PROVEN'}
    release,_=runtime.read_native('/etc/os-release')
    if not (re.search(rb'(?m)^ID=(?:ubuntu|"ubuntu")$',release)
            and re.search(rb'(?m)^VERSION_ID=(?:24\.04|"24\.04")$',release)):
        return dict(result,reason='TARGET_IS_NOT_UBUNTU_24_04')
    if not Path(pg.BIN+'postgres').is_file():return dict(result,reason='FIXED_NATIVE_PG16_PATH_UNAVAILABLE')
    result['native_inputs']={name:_native_input_chain(path) for name,path in (
        ('interpreter',str(Path('/proc/self/exe').resolve(strict=True))),
        ('stdlib',sysconfig.get_path('stdlib')),('libdir',sysconfig.get_config_var('LIBDIR')),
        ('postgres',pg.BIN+'postgres'))}
    result['native_inputs']['postgres_data']=_postgres_data_inputs()
    before=_descriptor_inventory();started=time.monotonic();stages=[]
    tracemalloc.start()
    try:
        stages.append(_memory_stage('BEFORE_CAPTURE'))
        measurement=_measure_inputs(binding,root,result['native_inputs'])
        stages.append(_memory_stage('CAPTURE_FIT_INPUTS_AND_CLEANUP'))
        status={'FIT':'PASS','REFUSED':'REFUSED_CAPACITY',
                'NOT_MEASURED_INPUT_TRUST_REFUSED':'REFUSED_NATIVE_INPUT_TRUST'}[measurement['capacity']]
        result.update(capture_fit=status,measurement=measurement)
        if measurement.get('postgres_input',{}).get('status')=='PASS':
            native=measurement['postgres_input']
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
            children=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*1024
            envelope=rss+children+native['memfd_allocated_bytes']
            result['pg_input_memory']={'process_peak_rss_bytes':rss,'child_peak_rss_bytes':children,
                'owned_memfd_allocation_bytes':native['memfd_allocated_bytes'],'conservative_sum_bytes':envelope,
                'service_limit_bytes':536870912,'within_component_envelope':envelope<=536870912,
                'scope':'PG_ONLY_CAPTURE_COMPONENT_NOT_COMPOSED_PHASE_OR_WHOLE_SERVICE'}
        if measurement['capacity']=='FIT':
            detail=_measure_composed_phase(binding,root,measurement);result['phase_component']=detail
            stages.extend(detail.pop('stages'))
            # Owning capture closes both inputs before composed allocation. FD
            # aliases share an inode, so adoption does not duplicate backing.
            peak_backing=max(measurement['input_memfd_allocated_bytes'],detail['phase_memfd_allocated_bytes'])
            result['memory_envelope']=pg.memory_envelope(peak_backing,0)
            if result['memory_envelope']['result']!='WITHIN_COMPONENT_ENVELOPE':result['capture_fit']='REFUSED_MEMORY_ENVELOPE'
        stages.append(_memory_stage('AFTER_OWNED_CLEANUP'))
    except (ns.NamespaceProofError,HostedSourceError,ns.backend.LocalStagingAcceptanceError,MemoryError,OSError) as error:
        result.update(capture_fit='REFUSED_COMPONENT',failure_type=type(error).__name__,
                      failure_frames=_capture_failure_frames(error))
        # No exception text, caller data or private filesystem paths in evidence.
    finally:
        tracemalloc.stop();ns._require(_descriptor_inventory()==before)
    observed=[stage['shared_job'] for stage in stages]
    result['component_result_before_counter_gate']=result['capture_fit']
    result['shared_job_zero_oom']='NOT_PROVEN'
    if any(item['status']=='REFUSED_COUNTER_SCHEMA' for item in observed):
        result['capture_fit']='REFUSED_COUNTER_SCHEMA'
    elif any(item['status']=='REFUSED_SOCKET_PRESSURE' for item in observed):
        result['capture_fit']='REFUSED_SOCKET_PRESSURE'
    elif (len(observed)>=2 and stages[0]['stage']=='BEFORE_CAPTURE'
          and stages[-1]['stage']=='AFTER_OWNED_CLEANUP'
          and all(item['status']=='OBSERVED' for item in observed)):
        from hosted_synthetic_cgroup import require_zero_oom_delta,CgroupProofError
        try:
            ns._require(all(item['identity']==observed[0]['identity'] for item in observed))
            for earlier,later in zip(observed,observed[1:]):require_zero_oom_delta(earlier['values']['memory.events'],later['values']['memory.events'])
        except (ns.NamespaceProofError,CgroupProofError):result['capture_fit']='REFUSED_SHARED_JOB_COUNTERS'
        else:result['shared_job_zero_oom']='OBSERVED_ZERO_DELTA_SHARED_SCOPE_ONLY'
    result.update(elapsed_seconds=round(time.monotonic()-started,6),memory_stages=stages,
        owned_descriptors_closed=True,namespace_deadline_proof='NOT_RUN',service_limits_applied='NOT_RUN_REVIEW_REQUIRED')
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
