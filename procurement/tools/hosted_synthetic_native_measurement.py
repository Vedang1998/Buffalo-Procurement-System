"""Dormant fixed native-view measurement transport; no installation authority.

The child report is data. Readiness is minted only after the parent binds the
exact child, executable, mounted objects and service, then observes drain and
closes its handles. The original host remains the ownership/IPC scan context.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parent))
import hosted_synthetic_native_inputs as native
import hosted_synthetic_namespace as ns
import hosted_synthetic_observer as outer
import hosted_synthetic_bootstrap as boot
import hosted_synthetic_cgroup as cg

_TOKEN=object()


class NativeConsumer:
    def __init__(self):raise ns.NamespaceProofError('native_consumer_must_be_reserved')


def reserve_consumer(installation,request,binding,unit, *, role,uid,gid):
    from hosted_synthetic_native_installation import InstallationLease
    native.require(type(installation) is InstallationLease and installation.installed)
    installation.verify();native._verify_request(request,binding)
    native.require(request.root==installation.root and request.root_identity==installation.owned[''][:2]
        and installation.view_requests.get(request.nonce) is request
        and role in ('measurement','service') and boot._UNIT.fullmatch(unit)
        and type(uid) is int and type(gid) is int and uid>0 and gid>0)
    if role=='measurement':native.require((uid,gid)==(os.geteuid(),os.getegid()),'native_measurement_operator_refused')
    native.require(all(c.unit!=unit for c in installation.consumers),'native_consumer_unit_reuse')
    value=object.__new__(NativeConsumer);value._owner=value;value._token=_TOKEN
    value.installation=installation;value.request=request;value.binding=binding;value.unit=unit
    value.role=role;value.uid=uid;value.gid=gid;value.attempted=False;value.observer=None
    value.wire=native.view_wire(request,binding);value.digest=hashlib.sha256(value.wire).hexdigest()
    value.association=native._digest({'wire':value.digest,'unit':unit,'uid':uid,'gid':gid,'role':role,
        'commit':binding.commit,'tree':binding.tree,'files':binding.files})
    installation.consumers.append(value);return value


def _association(value,installation=None):
    native.require(type(value) is NativeConsumer and value._owner is value and value._token is _TOKEN
        and (installation is None or value.installation is installation)
        and value in value.installation.consumers
        and value.installation.view_requests.get(value.request.nonce) is value.request,
        'native_consumer_owner_refused')
    request=value.request;wire=json.loads(value.wire)
    native.require(hashlib.sha256(value.wire).hexdigest()==value.digest and value.association==native._digest({
        'wire':value.digest,'unit':value.unit,'uid':value.uid,'gid':value.gid,'role':value.role,
        'commit':value.binding.commit,'tree':value.binding.tree,'files':value.binding.files})
        and (wire['nonce'],wire['commit'],wire['tree'],wire['root'],wire['root_identity'],wire['deadline'],wire['interpreter_sha256'])==
            (request.nonce,request.commit,request.tree,value.installation.root,list(request.root_identity),request.deadline,request.interpreter_sha256)
        and request.root_identity==value.installation.owned[''][:2], 'native_consumer_binding_refused')


def verify_consumer(value,installation=None):
    _association(value,installation)
    native.require(hashlib.sha256(native.view_wire(value.request,value.binding)).hexdigest()==value.digest,
        'native_consumer_view_changed')


def require_consumer_drain(value,installation):
    # Do not reapply a work deadline to completed cleanup; verify immutable
    # source/request association and exact observer-owned terminal proof.
    _association(value,installation)
    if not value.attempted:
        native.require(value.observer is None,'native_consumer_lifecycle_refused');return
    observer=value.observer
    if value.role=='service':
        native.require(type(observer) is outer.ExternalObserver and getattr(observer,'native_consumer',None) is value)
        outer.require_drain_proof(observer.proof,observer.account)
        native.require((observer.unit,observer.binding)==(value.unit,value.binding)
            and (observer.proof.uid,observer.proof.gid)==(value.uid,value.gid))
    else:
        native.require(type(observer) is MeasurementObserver and observer.consumer is value
            and observer._owner is observer and observer.closed and not observer.failed
            and observer.unit==value.unit and observer.drained and observer.descriptors_closed,'native_measurement_drain_required')


def observe_process_view(consumer,pid,start):
    """Native stat observations of fixed paths; no root repository interpreter."""
    verify_consumer(consumer)
    native.require(ns._process(pid)['start']==start)
    aliases={'/opt':'/opt','/usr/share':'/usr/share',**native.view_aliases()};paths=sorted(aliases)
    observed=consumer.installation.ops.observe_many(['/proc/'+str(pid)+'/root'+p for p in paths],follow=True)
    native.require(len(observed)==len(paths))
    for path,info in zip(paths,observed):
        logical=aliases[path];expected=consumer.installation.owned['view'+logical]
        native.require(info==expected and list(info[:2])==consumer.request.backing[logical],
            'native_child_backing_refused')
    namespace,exe=consumer.installation.ops.observe_many(['/proc/'+str(pid)+'/ns/mnt','/proc/'+str(pid)+'/exe'],follow=True)
    native.require(namespace[:2]!=consumer.request.initial_mount_namespace
        and exe==consumer.installation.owned['view'+native.PYTHON]
        and ns._process(pid)['start']==start,'native_child_interpreter_or_namespace_refused')
    raw=consumer.installation.ops.read_maps(pid)
    native.validate_loaded_mappings(raw,consumer.request)
    native.require(ns._process(pid)['start']==start)
    return namespace[:2]


class MeasurementObserver:
    def __init__(self):raise ns.NamespaceProofError('native_measurement_must_be_owned')

    @classmethod
    def create(cls,consumer):
        verify_consumer(consumer);native.require(consumer.role=='measurement' and not consumer.attempted)
        value=object.__new__(cls);value._owner=value;value.consumer=consumer;value.unit=consumer.unit
        value.slice_unit=outer.slice_name(value.unit);value.path='/sys/fs/cgroup/system.slice/'+value.slice_unit
        value.root=None;value.controls={};value.actor=None;value.service=None;value.aggregate=None
        value.closed=False;value.failed=False;value.drained=False;value.descriptors_closed=False
        value.work_acknowledged=False;value.ready=False;value.service_invocation=None
        value.lifecycle_deadline=consumer.request.deadline
        native.require(outer._manager(value.slice_unit)['LoadState']=='not-found' and not os.path.lexists(value.path)
            and outer._manager(value.unit,service=True)['LoadState']=='not-found'
            and not os.path.lexists('/sys/fs/cgroup'+outer.service_path(value.unit)))
        value.initial_service_absent=True
        consumer.observer=value;consumer.attempted=True
        try:
            code,raw=boot._collect_native_client(outer.plan_slice(value.unit),cwd='/',operation_deadline=value.lifecycle_deadline)
            native.require(code==0 and re.fullmatch(rb'o "/org/freedesktop/systemd1/job/[1-9][0-9]*"\n',raw))
            manager=outer._manager(value.slice_unit,deadline=value.lifecycle_deadline)
            native.require(manager['ActiveState']=='active' and manager['ControlGroup']==value.path[len('/sys/fs/cgroup'):])
            value.invocation=manager['InvocationID'];value.root=outer._directory(value.path,uid=0)
            value.mount=ns._mount_id(value.root)
            for name in outer._CONTROLS:
                value.controls[name]=ns._acquire(lambda:os.open(name,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=value.root.number))
            native.require(not outer._children(value.root) and value.read('cgroup.procs')==value.read('cgroup.threads')==b''
                and outer._events_empty(value.read('cgroup.events')))
            value.sample();return value
        except BaseException:
            value.failed=True;consumer.installation.uncertain=True;value.close_descriptors();raise

    def read(self,name):
        descriptor=self.controls[name];descriptor.verify();info=os.fstat(descriptor.number)
        native.require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
            and ns._identity(os.stat(name,dir_fd=self.root.number,follow_symlinks=False))==descriptor.identity
            and ns._mount_id(descriptor)==self.mount)
        raw=os.pread(descriptor.number,4097,0);native.require(len(raw)<=4096);return raw

    def sample(self):
        native.require(self._owner is self and not self.closed and not self.failed)
        self.root.verify();manager=outer._manager(self.slice_unit,deadline=self.lifecycle_deadline)
        native.require(manager['InvocationID']==self.invocation and manager['ActiveState']=='active'
            and manager['ControlGroup']==self.path[len('/sys/fs/cgroup'):]
            and ns._identity(os.lstat(self.path))==self.root.identity
            and ns._mount_id(self.root)==self.mount)
        self.aggregate=cg._memory_observation(self.read,outer.MEMORY_MAX,self.aggregate)
        native.require(cg.parse_scalar(self.read('pids.max'))==outer.TASKS_MAX)
        return self.aggregate

    def bind_ready(self,frame):
        c=self.consumer;verify_consumer(c)
        native.require(not self.ready and type(frame) is dict and set(frame)=={'nonce','commit','tree','unit','view_sha256','pid','start'}
            and (frame['nonce'],frame['commit'],frame['tree'],frame['unit'],frame['view_sha256'])==
                (c.request.nonce,c.binding.commit,c.binding.tree,c.unit,c.digest),'native_measurement_ready_refused')
        manager=outer._manager(self.unit,service=True,deadline=self.lifecycle_deadline)
        path=outer.service_path(self.unit)
        native.require(manager['ActiveState']=='activating' and manager['ControlGroup']==path
            and manager['MainPID']==str(frame['pid']),'native_measurement_manager_refused')
        self.service_invocation=manager['InvocationID'];self.service=outer._directory('/sys/fs/cgroup'+path,uid=0)
        self.actor,_,_=outer._actor(frame['pid'],frame['start'],c.uid,c.gid,path,'supervisor')
        self.mount_namespace=observe_process_view(c,frame['pid'],frame['start'])
        self.observed_view_binding_sha256=native._digest({'mount_namespace':list(self.mount_namespace),'backing':c.request.backing})
        self.sample();self.ready=True
        return outer._PREFIX+native._canonical({'ack':c.request.nonce})+b'\n'

    def acknowledge_sent(self):
        native.require(self.ready and not self.work_acknowledged);self.work_acknowledged=True

    def finish(self):
        native.require(self.ready and not self.failed)
        self.lifecycle_deadline=min(time.monotonic()+5,self.consumer.request.deadline+5)
        path='/sys/fs/cgroup'+outer.service_path(self.unit)
        outer._await_absence(self.unit,path,self.service_invocation,(self.actor,),deadline=self.lifecycle_deadline)
        outer._removed(self.service,path,self.mount)
        native.require(outer._children(self.root)==[] and self.read('cgroup.procs')==self.read('cgroup.threads')==b''
            and outer._events_empty(self.read('cgroup.events')))
        self.sample()
        code,_=boot._collect_native_client(outer._native(('/usr/bin/systemctl','--system','--no-ask-password','stop',self.slice_unit)),cwd='/',operation_deadline=self.lifecycle_deadline)
        native.require(code==0)
        outer._await_absence(self.slice_unit,self.path,self.invocation,deadline=self.lifecycle_deadline)
        outer._removed(self.root,self.path,self.mount);self.drained=True

    def finish_refusal(self,result,code):
        c=self.consumer
        native.require(code==1 and result=={'native_measurement':'REFUSED','nonce':c.request.nonce,
            'commit':c.binding.commit,'tree':c.binding.tree,'view_sha256':c.digest}
            and self.initial_service_absent and not self.ready and not self.work_acknowledged
            and self.actor is None and self.service is None and not self.failed,
            'native_pre_ack_refusal_unbound')
        self.lifecycle_deadline=min(time.monotonic()+5,c.request.deadline+5)
        invocation=None;absent=False;path='/sys/fs/cgroup'+outer.service_path(self.unit)
        while True:
            native.require(time.monotonic()<self.lifecycle_deadline,'native_pre_ack_cleanup_timeout')
            current=outer._manager(self.unit,service=True,deadline=self.lifecycle_deadline)
            native.require(not os.path.lexists(path) and not outer._children(self.root)
                and self.read('cgroup.procs')==self.read('cgroup.threads')==b''
                and outer._events_empty(self.read('cgroup.events')),'native_pre_ack_subtree_not_empty')
            if current.get('LoadState')=='not-found':
                if absent:break
                absent=True
            else:
                native.require(not absent and current.get('ActiveState') in ('inactive','failed')
                    and current.get('MainPID')=='0' and current.get('ControlGroup') in ('',outer.service_path(self.unit)),
                    'native_pre_ack_terminal_refused')
                if invocation is None:invocation=current['InvocationID']
                native.require(current['InvocationID']==invocation,'native_pre_ack_unit_replaced')
            remaining=self.lifecycle_deadline-time.monotonic();native.require(remaining>0)
            time.sleep(min(.05,remaining))
        self.sample()
        code,_=boot._collect_native_client(outer._native(('/usr/bin/systemctl','--system','--no-ask-password','stop',self.slice_unit)),cwd='/',operation_deadline=self.lifecycle_deadline)
        native.require(code==0)
        outer._await_absence(self.slice_unit,self.path,self.invocation,deadline=self.lifecycle_deadline)
        outer._removed(self.root,self.path,self.mount);self.drained=True

    def close_descriptors(self):
        errors=[]
        for descriptor in [self.actor,self.service,*reversed(tuple(self.controls.values())),self.root]:
            if descriptor is not None:
                try:descriptor.close()
                except BaseException as error:errors.append(error)
        self.closed=True;self.descriptors_closed=not errors
        if errors:
            self.failed=True;self.consumer.installation.uncertain=True
            raise ns.NamespaceCleanupError('native_measurement_handles_unverified') from errors[0]


class NativePhaseReadiness:
    def __init__(self):raise ns.NamespaceProofError('native_readiness_must_be_measured')
    def verify(self,root,binding):
        native.require(self._owner is self and self._token is _TOKEN and self.binding==binding
            and self.root==str(Path(root).resolve(strict=True)),'native_readiness_binding_refused')
        verify_consumer(self.consumer);self.consumer.installation.verify()
        require_consumer_drain(self.consumer,self.consumer.installation)
        from hosted_synthetic_source import bind_hosted_source,REPOSITORY,REF
        native.require(bind_hosted_source(Path(root),repository=REPOSITORY,event='push',ref=REF,sha=binding.commit,
            operation_deadline=self.consumer.request.deadline)==binding)
        native.require(native._digest(self.report)==self.digest,'native_readiness_report_changed')
        validate_result(self.report,self.consumer)
        return self.report['memory_readiness']


def validate_result(result,consumer):
    from hosted_synthetic_postgres_runtime import _validate_memory_result
    native.require(type(result) is dict and set(result)=={'policy','nonce','commit','tree','manifest_sha256',
        'view_identity','interpreter_sha256','memory_readiness','observed_view_binding_sha256','execution_authority','service_memory_proof'}
        and (result['policy'],result['nonce'],result['commit'],result['tree'],result['manifest_sha256'],result['view_identity'],result['interpreter_sha256'])==
            (native.POLICY,consumer.request.nonce,consumer.binding.commit,consumer.binding.tree,native.DIGESTS[0],list(consumer.request.root_identity),consumer.request.interpreter_sha256)
        and result['execution_authority'] is False and result['service_memory_proof']=='NOT_PROVEN'
        and type(result['observed_view_binding_sha256']) is str and re.fullmatch('[0-9a-f]{64}',result['observed_view_binding_sha256']),
        'native_measurement_result_refused')
    _validate_memory_result(result['memory_readiness'],consumer.binding)
    from hosted_synthetic_postgres_runtime import _FIXED
    expected={p:r['sha256'] for p,r in native.execution_inventory()['rows'].items()}
    expected.update({p:hashlib.sha256(data).hexdigest() for p,data in _FIXED.items()})
    native.require(type(consumer.observer) is MeasurementObserver and consumer.observer.consumer is consumer
        and consumer.observer.ready and consumer.observer.work_acknowledged
        and result['observed_view_binding_sha256']==consumer.observer.observed_view_binding_sha256
        and result['memory_readiness']['interpreter']==native.PYTHON
        and result['memory_readiness']['generation']==consumer.request.nonce
        and result['memory_readiness']['native_inventory_sha256']==native._digest(expected),
        'native_measurement_actual_binding_refused')


def measure(consumer,root):
    """No CLI; called only from the dormant native candidate route."""
    verify_consumer(consumer);native.require(consumer.role=='measurement')
    plan=measurement_plan(consumer,root);observer=None;failure=None;result=None;code=None
    try:
        observer=MeasurementObserver.create(consumer)
        code,raw=boot._collect_native_client(plan,cwd=root,observer=observer,operation_deadline=consumer.request.deadline)
        native.require(code in (0,1) and 0<len(raw)<=65536,'native_measurement_child_refused')
        result=json.loads(raw);native.require(native._canonical(result)+b'\n'==raw)
        native.require(code==0,'native_measurement_child_refused')
        validate_result(result,consumer)
    except BaseException as error:failure=error
    if observer is not None and not observer.closed:
        try:
            if observer.ready:observer.finish()
            else:observer.finish_refusal(result,code)
        except BaseException as error:
            observer.failed=True;consumer.installation.uncertain=True
            if failure is None:failure=error
        finally:observer.close_descriptors()
    if failure is not None:raise failure
    require_consumer_drain(consumer,consumer.installation)
    value=object.__new__(NativePhaseReadiness);value._owner=value;value._token=_TOKEN
    value.consumer=consumer;value.binding=consumer.binding;value.root=str(Path(root).resolve(strict=True))
    value.report=result;value.digest=native._digest(result)
    value.verify(root,consumer.binding);return value


def measurement_plan(consumer,root):
    verify_consumer(consumer);native.require(consumer.role=='measurement')
    root=boot._path(str(root))
    from hosted_synthetic_source import bind_hosted_source,REPOSITORY,REF
    native.require(bind_hosted_source(Path(root),repository=REPOSITORY,event='push',ref=REF,sha=consumer.binding.commit,
        operation_deadline=consumer.request.deadline)==consumer.binding,'native_measurement_source_changed')
    boot.inspect_native_prerequisites()
    chain=('/usr/bin/setpriv','--clear-groups','--no-new-privs','--','/usr/bin/setpriv',
        '--reuid='+str(consumer.uid),'--regid='+str(consumer.gid),'--keep-groups','--inh-caps=-all',
        '--ambient-caps=-all','--bounding-set=-all','--no-new-privs','--','/usr/bin/env','-i',
        'PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',native.PYTHON,'-I','-S','-B',
        root+'/procurement/tools/hosted_synthetic_native_measurement.py',consumer.wire.decode(),consumer.unit,
        str(consumer.uid),str(consumer.gid))
    properties=('User='+str(consumer.uid),'Group='+str(consumer.gid),'NoNewPrivileges=yes',
        'CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_SETPCAP','AmbientCapabilities=',
        'MemoryAccounting=yes','TasksAccounting=yes','TimeoutStartSec=60s','TimeoutStopSec=5s','KillMode=control-group',
        *native.view_properties(consumer.request,consumer.binding),'ExecStartEx=:!'+' '.join(boot._quote(t) for t in chain))
    return ('/usr/bin/sudo','-n','--','/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',
        '/usr/bin/timeout','--signal=TERM','--kill-after=5s','60s','/usr/bin/systemd-run','--system','--no-ask-password','--quiet','--wait','--pipe','--collect',
        '--service-type=oneshot','--expand-environment=no','--slice='+outer.slice_name(consumer.unit),
        '--unit='+consumer.unit,'--working-directory='+root,*('--property='+p for p in properties),'--','/usr/bin/true')


def child_main(arguments=None):
    args=list(sys.argv[1:] if arguments is None else arguments);request=None;binding=None
    try:
        native.require(len(args)==4 and boot._UNIT.fullmatch(args[1]) and all(re.fullmatch('[1-9][0-9]{0,9}',s) for s in args[2:]))
        boot.launcher.inspect_host_identity(uid=int(args[2]),gid=int(args[3]))
        from hosted_synthetic_capability import _require_clean_supervisor
        _require_clean_supervisor()
        from hosted_synthetic_source import bind_hosted_source,REPOSITORY,REF
        raw=args[0].encode('ascii');native.require(len(raw)<=65536);identity=json.loads(raw)
        root=Path(__file__).resolve().parents[2]
        binding=bind_hosted_source(root,repository=REPOSITORY,event='push',ref=REF,sha=identity['commit'],operation_deadline=identity['deadline'])
        request=native.view_from_wire(raw,binding)
        with native.protected_view(request,binding):
            process=ns._process(os.getpid())
            frame={'nonce':request.nonce,'commit':binding.commit,'tree':binding.tree,'unit':args[1],
                'view_sha256':hashlib.sha256(raw).hexdigest(),'pid':os.getpid(),'start':process['start']}
            native.require(stat.S_ISFIFO(os.fstat(0).st_mode) and stat.S_ISFIFO(os.fstat(1).st_mode))
            output=outer._PREFIX+native._canonical(frame)+b'\n';native.require(len(output)<=4096)
            native.require(os.write(1,output)==len(output))
            ack=bytearray()
            with selectors.DefaultSelector() as ready:
                ready.register(0,selectors.EVENT_READ)
                while not ack.endswith(b'\n'):
                    remaining=request.deadline-time.monotonic();native.require(remaining>0 and ready.select(remaining))
                    block=os.read(0,257-len(ack));native.require(block and len(ack)+len(block)<=256);ack.extend(block)
            native.require(ack==outer._PREFIX+native._canonical({'ack':request.nonce})+b'\n','native_measurement_ack_refused')
            from hosted_synthetic_postgres_runtime import require_phase_fit
            receipt=require_phase_fit(root,binding,deadline=request.deadline,generation=request.nonce)
            result=native.bind_measurement_result(request,binding,receipt)
        output=native._canonical(result)+b'\n';native.require(len(output)<=65536)
        native.require(os.write(1,output)==len(output));return 0
    except (Exception,KeyboardInterrupt):
        # No raw path/environment/exception disclosure, no false cleanup result.
        result={'native_measurement':'REFUSED'}
        if request is not None and binding is not None:
            result.update(nonce=request.nonce,commit=binding.commit,tree=binding.tree,
                view_sha256=hashlib.sha256(raw).hexdigest())
        os.write(1,native._canonical(result)+b'\n');return 1


if __name__=='__main__':raise SystemExit(child_main())
