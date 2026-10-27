"""Fixed sealed postgres --version I/W protocol, no CLI or release authority.

Caller must be the retained, enrolled private PID1 in the PostgreSQL phase.
The worker remains stopped until the existing outer observer grants this exact
request. This module cannot grant outer cgroup/service authorization itself.
"""
import errno
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import time

import hosted_synthetic_namespace as ns
import hosted_synthetic_postgres_image as image
import hosted_synthetic_postgres_identity as identity
import hosted_synthetic_worker as worker
from hosted_synthetic_cgroup import _signal_boundary

POLICY='BUFFALO_FIXED_POSTGRES_VERSION_V1'
ENV={'PATH':'/usr/lib/postgresql/16/bin:/usr/bin:/bin','LC_ALL':'C.UTF-8','TZ':'UTC',
     'OPENSSL_CONF':'/etc/ssl/openssl.cnf','PGSSLMODE':'disable','PGGSSENCMODE':'disable'}
MAX_OUTPUT=1024

class VersionRefusal(ns.NamespaceProofError):
    def __init__(self,code):
        ns._require(type(code) is str and re.fullmatch(r'[A-Z_]{1,43}(?::[0-9]{1,4})?',code))
        name,_,number=code.partition(':')
        self.location='_pg_'+name.lower()+':'+str(max(1,int(number or '1')))
        super().__init__(code)


def _request(spec,start,proof,number):
    return {'role':'postgres','start':start,'image_fd':number,'image_identity':proof['image_identity'],
        'image_sha256':spec.executable_sha256,'bundle_sha256':spec.bundle_sha256,
        'source_commit':spec.source_commit,'source_tree':spec.source_tree}


def _exec(image_lease,gate,status,deadline):
    """Worker blocks on exact ACK; only fixed FD exec can follow verification."""
    spec=image_lease.spec
    ns._require(type(image_lease) is image.VersionImage and spec.role=='postgres')
    proof=image_lease.verify();start=ns._process(os.getpid())['start']
    request=_request(spec,start,proof,image_lease.descriptor.number)
    ns._send(status,spec.generation,'PG_ARMED',deadline,request=request)
    ack=ns._receive(gate,spec.generation,'PG_EXEC',deadline,fields=('request',))
    ns._require(ack['request']==request)
    ns._require(ns._process(os.getpid())['start']==start)
    image_lease.verify()
    # No arbitrary argv/env, path fallback, or inheritable protocol handles.
    ns._require(os.execve in os.supports_fd)
    os.execve(image_lease.descriptor.number,[spec.executable,'--version'],dict(ENV))
    raise VersionRefusal('EXEC_RETURNED')


def _worker(spec,gate,status):
    lease=None;code='WORKER_PRECONDITION';number=0
    try:
        ns._hardening(1)
        ns.backend._close_browser_guardian_descriptors(frozenset((0,1,2,gate.number,status.number)))
        identity.restrict_postgres_worker(spec.root_identity,spec.root_mount,channels=(gate,status))
        lease=image.open_version_image(spec,channels=(gate,status))
        code='ACK_OR_IMAGE_REFUSED'
        _exec(lease,gate,status,time.monotonic()+ns._TIMEOUT)
    except BaseException as error:
        if isinstance(error,OSError):code='EXEC_OR_KERNEL_REFUSED';number=error.errno or 0
        if isinstance(error,(KeyboardInterrupt,SystemExit)):code='CANCELLED'
        try:
            if lease is not None:lease.close()
        except BaseException:code='WORKER_CLEANUP_FAILED'
        try:
            ns._send(status,spec.generation,'PG_REFUSED',time.monotonic()+1,code=code,errno=number)
        except BaseException:pass
        os._exit(125)


def _armed(status,generation,deadline):
    status.verify()
    value=ns.backend._read_browser_guardian_frame(status.number,deadline=deadline)
    if type(value) is dict and value.get('stage')=='PG_REFUSED':
        raw=ns.backend.encode_browser_worker_frame(value,maximum_bytes=MAX_OUTPUT)
        validate_output({'stdout':b'','stderr':b'','protocol':raw},None,generation)
    ns._require(type(value) is dict and set(value)=={'protocol','generation','stage','request'}
        and value['protocol']==ns.backend._BROWSER_CONTAINMENT_PROTOCOL and value['generation']==generation
        and value['stage']=='PG_ARMED')
    return value


def _read_exit(pid,deadline):
    while time.monotonic()<deadline:
        result=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)
        if result is not None:
            ns._require(result.si_pid==pid)
            return result
        time.sleep(.005)
    raise VersionRefusal('NATIVE_EXIT_TIMEOUT')


def _collect(streams,deadline):
    output={name:bytearray() for name in streams}
    with selectors.DefaultSelector() as selector:
        for name,descriptor in streams.items():
            descriptor.verify();os.set_blocking(descriptor.number,False)
            selector.register(descriptor.number,selectors.EVENT_READ,name)
        while selector.get_map():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise VersionRefusal('NATIVE_OUTPUT_TIMEOUT')
            ready=selector.select(remaining)
            if not ready:raise VersionRefusal('NATIVE_OUTPUT_TIMEOUT')
            for key,_ in ready:
                data=os.read(key.fd,min(4096,MAX_OUTPUT+1-len(output[key.data])))
                if not data:selector.unregister(key.fd);continue
                output[key.data].extend(data)
                if len(output[key.data])>MAX_OUTPUT:raise VersionRefusal('NATIVE_OUTPUT_BOUND')
    return {key:bytes(value) for key,value in output.items()}


def validate_output(output,exit_observation,generation):
    ns._require(type(output) is dict and set(output)=={'stdout','stderr','protocol'}
        and all(type(value) is bytes and len(value)<=MAX_OUTPUT for value in output.values()))
    if output['protocol']:
        # The protocol is length-prefixed by the existing frame transport.
        raw=output['protocol'];ns._require(len(raw)>=4)
        length=int.from_bytes(raw[:4],'big');ns._require(length==len(raw)-4)
        value=ns.backend.decode_browser_worker_frame(raw,maximum_bytes=MAX_OUTPUT)
        ns._require(type(value) is dict and set(value)=={'protocol','generation','stage','code','errno'}
            and value['protocol']==ns.backend._BROWSER_CONTAINMENT_PROTOCOL and value['generation']==generation
            and value['stage']=='PG_REFUSED' and value['code'] in ('WORKER_PRECONDITION','ACK_OR_IMAGE_REFUSED','EXEC_OR_KERNEL_REFUSED','CANCELLED','WORKER_CLEANUP_FAILED')
            and type(value['errno']) is int and 0<=value['errno']<=4095)
        raise VersionRefusal('EXPLICIT_'+value['code']+':'+str(value['errno']))
    if exit_observation.si_code!=os.CLD_EXITED or exit_observation.si_status!=0:
        raise VersionRefusal('NATIVE_SIGNAL_OR_NONZERO_EXIT')
    ns._require(output['stderr']==b'' and re.fullmatch(rb'postgres \(PostgreSQL\) 16\.[0-9]{1,3}(?: \([A-Za-z0-9.+~:\-/ ]{1,160}\))?\n',output['stdout']))
    return output['stdout'].decode('ascii').strip()


def _attest(lease):
    _signal_boundary();ns._require(os.getpid()==1 and os.getppid()==0)
    ns._require_mapped_identity();lease.pidfd.verify()
    ns._require(not ns.backend._browser_pidfd_is_terminal(lease.pidfd.number))
    ns._require(ns.backend._read_browser_pidfd_metadata(lease.pidfd.number)[1]==lease.pid)
    current=ns._process(lease.pid);spec=lease.spec;request=lease.request
    ns._require(current['pid']==2 and current['state']=='T' and current['parent']==1
        and current['start']==request['start'] and ns._process(1)['start']==spec.init_start
        and sorted(os.listdir('/proc/2/task'))==['2'] and ns._read('/proc/2/task/2/children',4096)==b''
        and ns._read('/proc/self/task/1/children',4096).split()==[b'2'])
    status=worker._status(2)
    ns._require(status['uid']==status['gid']==[0]*4 and status['groups']==[] and status['no_new_privs']==1
        and status['nspid']==[2] and all(status[key]==0 for key in worker._CAPS)
        and status['Seccomp']==2 and status['Seccomp_filters']==spec.filter_count)
    for name in ('uid','gid'):ns._require(ns._read('/proc/2/'+name+'_map',128).split()==identity._MAPPING.split())
    ns._require(ns._read('/proc/2/setgroups',32)==b'deny\n')
    actual=identity._namespaces('2');expected=dict((k,list(v)) for k,v in spec.namespaces)
    ns._require(actual['user']!=expected['user'] and all(actual[k]==expected[k] for k in expected if k!='user'))
    ns._require(ns._read('/proc/2/cgroup',4096)==ns._read('/proc/self/cgroup',4096))
    root=ns._acquire(lambda:os.open('/proc/2/root',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC))
    try:
        flags=os.fstatvfs(root.number).f_flag
        ns._require(root.identity==spec.root_identity==ns._identity(os.stat('/proc/2/cwd'))
            and ns._mount_id(root)==spec.root_mount and ns.backend._browser_fstatfs_magic(root.number)==0x01021994
            and flags & os.ST_RDONLY and flags & os.ST_NOSUID and flags & os.ST_NODEV and not flags & os.ST_NOEXEC)
    finally:root.close()
    expected_fds={0,1,2,lease.child_gate,lease.child_status,request['image_fd']}
    ns._require(len(expected_fds)==6 and set(map(int,os.listdir('/proc/2/fd')))==expected_fds)
    for number,own in ((lease.child_gate,lease.gate),(lease.child_status,lease.status),(1,lease.stdout),(2,lease.stderr)):
        own.verify();info=os.stat('/proc/2/fd/'+str(number));ns._require(stat.S_ISFIFO(info.st_mode) and ns._identity(info)==own.identity)
        _,flags,_,_=ns.backend._read_browser_worker_fd_metadata(2,number)
        ns._require(flags & os.O_ACCMODE==(os.O_RDONLY if number==lease.child_gate else os.O_WRONLY)
            and bool(flags & os.O_CLOEXEC)==(number not in (1,2)))
    ns._require(os.readlink('/proc/2/fd/0')=='/dev/null')
    number=request['image_fd'];info=os.stat('/proc/2/fd/'+str(number))
    _,flags,mount,inode=ns.backend._read_browser_worker_fd_metadata(2,number)
    ns._require(list(ns._identity(info))==request['image_identity']==list(ns._identity(os.stat(spec.executable)))
        and mount==spec.root_mount and inode==info.st_ino and stat.S_IMODE(info.st_mode)==0o555
        and flags & (os.O_ACCMODE|os.O_PATH|os.O_NONBLOCK)==os.O_RDONLY and flags & os.O_CLOEXEC
        and hashlib.sha256(ns._read(spec.executable,image.runtime.MAX_BYTES)).hexdigest()==spec.executable_sha256)
    ns._require(ns._process(2)==current)
    return {'process':current,'status':status,'namespaces':actual,'root_identity':list(root.identity),
        'root_mount_id':spec.root_mount,'descriptors':sorted(expected_fds),'request':request,
        'scope':'ACTUAL_STOPPED_PG_WORKER','control_exclusion':'PATH_FD_POLICY_OBSERVED; ADVERSARIAL_LIFETIME_NOT_PROVEN'}


class StoppedVersion:
    def __init__(self):raise VersionRefusal('USE_PRIVATE_INIT_START')

    def attest(self):
        ns._require(self._owner is self and not self.closed and not self.executed and self.owner_pid==os.getpid())
        return _attest(self)

    def execute(self,gate,status):
        """Only after G/S forward the independently gated exact request."""
        try:
            self.attest()
            ns._send(status,self.spec.generation,'PG_STOPPED',self.deadline,evidence=self.attest())
            ack=ns._receive(gate,self.spec.generation,'PG_EXEC',self.deadline,fields=('request',))
            ns._require(ack['request']==self.request)
            self.attest()
            ns._send(self.gate,self.spec.generation,'PG_EXEC',self.deadline,request=self.request)
            self.pidfd.verify();signal.pidfd_send_signal(self.pidfd.number,signal.SIGCONT)
            self.executed=True
            output=_collect({'stdout':self.stdout,'stderr':self.stderr,'protocol':self.status},self.deadline)
            terminal=_read_exit(self.pid,self.deadline)
            version=validate_output(output,terminal,self.spec.generation)
            ns._require(ns._process(self.pid)['start']==self.request['start']
                and ns._read('/proc/self/task/1/children',4096).split()==[b'2'])
            self.close()
            return {'policy':POLICY,'generation':self.spec.generation,'request':self.request,'version':version,
                'native_execution':'FD_BOUND_VERSION_COMPLETED','native_diagnostic_processes':1,'worker_cleanup':'OWNED_CHILD_REAPED_AND_DESCRIPTORS_CLOSED',
                'root_enforcement':'READ_ONLY_PROJECTED_TMPFS','library_maps_lifetime':'NOT_PROVEN',
                'whole_service_resources':'REQUIRES_SAME_GENERATION_OUTER_OBSERVER','credential_release':'WITHHELD',
                'integrated_acceptance':False}

        except BaseException as error:
            try:self.close()
            except BaseException:raise ns.NamespaceCleanupError('pg_version_execute_cleanup_refused') from error
            raise

    def close(self):
        ns._require(self._owner is self and self.owner_pid==os.getpid())
        if self.closed:
            ns._require(not self.cleanup_failed);return
        errors=[]
        try:
            _signal_boundary()
            if self.pid>1:
                # waitid(WNOWAIT) establishes current unreaped direct ownership
                # even if pidfd acquisition failed; never signal a reused PID.
                os.waitid(os.P_PID,self.pid,os.WEXITED|os.WNOWAIT|os.WNOHANG)
                if self.pidfd is not None:
                    self.pidfd.verify()
                    if not ns.backend._browser_pidfd_is_terminal(self.pidfd.number):signal.pidfd_send_signal(self.pidfd.number,signal.SIGKILL)
                else:os.kill(self.pid,signal.SIGKILL) # retained unreaped direct fork child
                _read_exit(self.pid,time.monotonic()+5)
                ns._require(ns._read('/proc/self/task/1/children',4096).split()==[str(self.pid).encode()])
                waited,_=os.waitpid(self.pid,0);ns._require(waited==self.pid)
                ns._require(ns._read('/proc/self/task/1/children',4096)==b''
                    and sorted(int(n) for n in os.listdir('/proc') if n.isdecimal())==[1])
        except BaseException as error:errors.append(error)
        for descriptor in [self.pidfd,*reversed(self.descriptors)]:
            if descriptor is not None:
                try:descriptor.close()
                except BaseException as error:errors.append(error)
        self.closed=True;self.cleanup_failed=bool(errors)
        if errors:raise ns.NamespaceCleanupError('pg_version_cleanup_not_proven') from errors[0]


def start_version(value,root_observation):
    """Private I only. Returns a retained stopped worker, never executes it."""
    _signal_boundary()
    spec=image.prepare_version_spec(value,root_observation,'postgres')
    ns._require({line.split(':',1)[0].strip() for line in ns._read('/proc/net/dev',4096).decode().splitlines()[2:]}=={'lo'})
    lease=object.__new__(StoppedVersion);lease._owner=lease;lease.owner_pid=os.getpid();lease.spec=spec
    lease.closed=False;lease.cleanup_failed=False;lease.executed=False;lease.pid=-1;lease.pidfd=None;lease.descriptors=[None]*8
    lease.deadline=time.monotonic()+ns._TIMEOUT
    try:
        for index in range(0,8,2):
            pair=ns._pipe()
            lease.descriptors[index]=pair[0];lease.descriptors[index+1]=pair[1]
        gate_read,lease.gate,lease.status,status_write,lease.stdout,out_write,lease.stderr,err_write=lease.descriptors
        lease.child_gate=gate_read.number;lease.child_status=status_write.number
        lease.pid=os.fork()
        if lease.pid==0:
            try:
                os.dup2(out_write.number,1,inheritable=True);os.dup2(err_write.number,2,inheritable=True)
                _worker(spec,gate_read,status_write)
            finally:os._exit(126)
        ns._require(lease.pid==2)
        for descriptor in (gate_read,status_write,out_write,err_write):descriptor.close()
        lease.pidfd=ns._pidfd(lease.pid)
        armed=_armed(lease.status,spec.generation,lease.deadline)
        request=armed['request'];ns._require(type(request) is dict and set(request)=={'role','start','image_fd','image_identity','image_sha256','bundle_sha256','source_commit','source_tree'}
            and request['role']=='postgres' and type(request['start']) is int and request['start']>0
            and type(request['image_fd']) is int and request['image_fd']>2 and type(request['image_identity']) is list
            and len(request['image_identity'])==2 and all(type(n) is int and n>0 for n in request['image_identity'])
            and request['image_sha256']==spec.executable_sha256 and request['bundle_sha256']==spec.bundle_sha256
            and request['source_commit']==spec.source_commit and request['source_tree']==spec.source_tree)
        lease.request=request
        signal.pidfd_send_signal(lease.pidfd.number,signal.SIGSTOP)
        ns._wait(lease.pid,lease.deadline,stopped=True)
        lease.attest()
        return lease
    except BaseException as error:
        try:lease.close()
        except BaseException:raise ns.NamespaceCleanupError('pg_version_start_cleanup_refused') from error
        raise


def rebind_init_root(value,guardian_observation):
    """Copy-mount IDs change in I; retain inode/source proof and verify anew."""
    _signal_boundary();ns._require(os.getpid()==1 and os.getppid()==0)
    ns._require_mapped_identity()
    ns._require(type(guardian_observation) is dict)
    root=ns._acquire(lambda:os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW))
    try:
        ns._require(list(root.identity)==guardian_observation.get('root_identity')
            and root.identity==ns._identity(os.stat('.'))
            and ns.backend._browser_fstatfs_magic(root.number)==0x01021994)
        mount=ns._mount_id(root)
        ns._require(mount!=guardian_observation.get('root_mount_id'))
        current=dict(guardian_observation,root_mount_id=mount)
        image.pg.verify_postgres_phase_consumer(value,current)
        return current
    finally:root.close()


def run_init(value,root_observation,gate,status,generation,deadline,proc_root,before):
    """I is already stopped-enrolled and has received PROBE before this call."""
    lease=None;error=None;result=None
    try:
        ns._require(value.generation==generation)
        local_root=rebind_init_root(value,root_observation)
        lease=start_version(value,local_root)
        lease.deadline=min(lease.deadline,deadline)
        result=lease.execute(gate,status)
    except BaseException as failure:error=failure
    if lease is not None:
        try:lease.close()
        except BaseException as cleanup:raise ns.NamespaceCleanupError('pg_init_worker_cleanup_refused') from (error or cleanup)
    if error is not None:raise error
    proc_root.verify();ns._require(proc_root.identity==ns._identity(os.stat('/proc')))
    ns.parse_private_proc_mount(ns._read('/proc/self/mountinfo'),ns._mount_id(proc_root))
    ns.backend._browser_unmount(b'/proc',2);proc_root.close()
    ns._require(ns._identity(os.stat('/proc'))==before)
    ns._send(status,generation,'PG_RESULT',deadline,result=result)
    ns._send(status,generation,'CLEAN',deadline)


def mapped_guardian(gate,status,parent,generation,supervisor,deadline,outside_uid,outside_gid,
                    original_namespaces,runtime_proof,phase):
    """Retained G forwards EXEC only after S's existing cgroup/observer gate."""
    ns._require(phase[0].generation==generation)
    ns.backend._browser_mount(None,b'/',None,(1<<18)|16384,None)
    descriptors=[None]*7;child=-1;reaped=False;body=None
    try:
        descriptors[0]=ns._pidfd(os.getpid())
        pair=ns._pipe();descriptors[1]=pair[0];descriptors[2]=pair[1]
        pair=ns._pipe();descriptors[3]=pair[0];descriptors[4]=pair[1]
        own,inner_read,inner_write,result_read,result_write=descriptors[:5]
        child,descriptors[5]=ns._start_init(own,parent,inner_read,inner_write,result_read,result_write,
            generation,supervisor,deadline,outside_uid,outside_gid,original_namespaces,True,
            runtime_proof=runtime_proof,postgres_phase=phase)
        init_fd=descriptors[5]
        ns._guardian_enrollment(gate,status,child,init_fd,generation,deadline)
        parent.close();own.close()
        ns._send(inner_write,generation,'PROBE',deadline)
        evidence=ns._receive(result_read,generation,'PG_STOPPED',deadline,fields=('evidence',))['evidence']
        ns._require(type(evidence) is dict and evidence.get('scope')=='ACTUAL_STOPPED_PG_WORKER')
        init=ns._process(child);init_ns=identity._namespaces(str(child));g_ns=identity._namespaces('self')
        ns._require(init['parent']==os.getpid() and init['group']==os.getpid()
            and ns.parse_status(ns._read(f'/proc/{child}/status',16384))['nspid']==[child,1]
            and init_ns['user']==g_ns['user'] and init_ns['net']==g_ns['net']
            and init_ns['mnt'] not in (original_namespaces['mnt'],g_ns['mnt'])
            and ns._identity(os.stat('/proc/self/ns/pid_for_children'))==tuple(init_ns['pid']))
        children=[];candidates=os.listdir('/proc');ns._require(len(candidates)<=65536)
        for name in candidates:
            ns._require(time.monotonic()<deadline)
            if not name.isdecimal():continue
            try:record=ns._process(int(name))
            except FileNotFoundError:continue
            if record['parent']==child:children.append(record)
        ns._require(len(children)==1)
        record=children[0];request=evidence['request']
        ns._require(record['state']=='T' and record['start']==request['start'] and record['group']==os.getpid()
            and ns.parse_status(ns._read(f'/proc/{record["pid"]}/status',16384))['nspid']==[record['pid'],2]
            and identity._namespaces(str(record['pid']))==evidence['namespaces']
            and ns._identity(os.stat(f'/proc/{record["pid"]}/root'))==tuple(runtime_proof['root_identity']))
        for selected in (child,record['pid']):
            held_root=ns._acquire(lambda:os.open(f'/proc/{selected}/root',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC))
            try:
                ns._require(list(held_root.identity)==runtime_proof['root_identity']
                    and ns._mount_id(held_root)==evidence['root_mount_id']
                    and evidence['root_mount_id']!=runtime_proof['root_mount_id'])
            finally:held_root.close()
        descriptors[6]=ns._pidfd(record['pid']);worker_fd=descriptors[6]
        outer={'init_pid':child,'init_start':init['start'],'worker_pid':record['pid'],'worker_start':record['start']}
        ns._send(status,generation,'RETAINED',deadline,evidence={'runtime':runtime_proof,'worker':evidence},outer=outer)
        ack=ns._receive(gate,generation,'PG_EXEC',deadline,fields=('request',))
        ns._require(ack['request']==request and ns._process(record['pid'])==record
            and ns._process(child)['start']==init['start'])
        worker_fd.verify();init_fd.verify()
        ns._require(not ns.backend._browser_pidfd_is_terminal(worker_fd.number)
            and not ns.backend._browser_pidfd_is_terminal(init_fd.number))
        ns._send(inner_write,generation,'PG_EXEC',deadline,request=request)
        result=ns._receive(result_read,generation,'PG_RESULT',deadline,fields=('result',))['result']
        ns._require(type(result) is dict and result.get('request')==request and result.get('generation')==generation
            and result.get('native_execution')=='FD_BOUND_VERSION_COMPLETED' and result.get('credential_release')=='WITHHELD')
        ns._receive(result_read,generation,'CLEAN',deadline)
        ns._wait(child,deadline);reaped=True
        ns._require(ns.backend._browser_pidfd_is_terminal(worker_fd.number))
        ns._send(status,generation,'PG_RESULT',deadline,result=result)
    except BaseException as error:body=error
    cleanup=[]
    if child>1 and not reaped:
        try:ns._kill_unreaped_child(child,descriptors[5])
        except BaseException as error:cleanup.append(error)
    for descriptor in reversed(descriptors):
        if descriptor is not None:
            try:descriptor.close()
            except BaseException as error:cleanup.append(error)
    if cleanup:raise ns.NamespaceCleanupError('pg_guardian_cleanup_refused') from (body or cleanup[0])
    if body is not None:raise body
    ns._send(status,generation,'CLEAN',deadline)
