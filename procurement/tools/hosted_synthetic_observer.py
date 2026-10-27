"""Outside observer for this one ephemeral hosted service, never a worker.

The accounting envelope is a fresh nonce-owned slice with the SAME aggregate
512MiB/64-task limits as the service. Existing ancestors are never modified.
All native activation remains dormant behind independent review. An absent
unit, cached sample, child report or caller-supplied PASS is not cleanup proof.
"""
from dataclasses import dataclass, replace
from functools import wraps
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time

import hosted_synthetic_bootstrap as boot
import hosted_synthetic_namespace as ns
import hosted_synthetic_cgroup as cg


MEMORY_MAX = 512 * 1024**2
TASKS_MAX = 64
_CONTROLS = ('cgroup.procs','cgroup.threads','cgroup.events','memory.current','memory.peak',
             'memory.max','memory.events','pids.max')
_ROLES = ('launcher','supervisor','guardian','init','worker')
_TOKEN = object()
_PREFIX = b'BUFFALO_OBSERVER_V1 '


def _sticky_observation(method):
    @wraps(method)
    def observe(self,*args,**kwargs):
        try:return method(self,*args,**kwargs)
        except BaseException:
            if getattr(self,'_owner',None) is self:
                self.failed=True;self.account.uncertain=True
            raise
    return observe


def slice_name(unit):
    return boot.launcher.accounting_slice(unit)


def service_path(unit):
    return boot.launcher.service_cgroup_path(unit)


def _native(command):
    return ('/usr/bin/sudo','-n','--','/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',
            '/usr/bin/timeout','--signal=TERM','--kill-after=2s','15s',*command)


def plan_slice(unit):
    """Reviewable fixed native command; calling this performs no mutation."""
    properties=(('MemoryAccounting','b','true'),('TasksAccounting','b','true'),
        ('MemoryMax','t',str(MEMORY_MAX)),('TasksMax','t',str(TASKS_MAX)),
        ('CPUAccounting','b','false'),('IOAccounting','b','false'),('IPAccounting','b','false'),
        ('StopWhenUnneeded','b','false'))
    return _native(('/usr/bin/busctl','--system','--no-pager','call','org.freedesktop.systemd1',
        '/org/freedesktop/systemd1','org.freedesktop.systemd1.Manager','StartTransientUnit',
        'ssa(sv)a(sa(sv))',slice_name(unit),'fail',str(len(properties)),
        *(token for row in properties for token in row),'0'))


def _manager(unit, *, service=False):
    boot._require(boot._UNIT.fullmatch(unit) or re.fullmatch(r'system-buffalo[0-9a-f]{32}\.slice',unit))
    keys=('Id','LoadState','ActiveState','ControlGroup','InvocationID','MemoryMax','TasksMax')
    if service:keys+=('MainPID',)
    result=subprocess.run(['/usr/bin/timeout','--signal=TERM','--kill-after=1s','3s',
        '/usr/bin/systemctl','--system','--no-ask-password','show',unit,
        *('--property='+key for key in keys)],env=boot._ENVIRONMENT,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=5)
    boot._require(result.returncode in (0,1) and len(result.stdout)<=4096,'observer_manager_refused')
    values={}
    for line in result.stdout.splitlines():
        key,separator,value=line.partition(b'=')
        boot._require(separator and key.decode('ascii') in keys and key.decode('ascii') not in values)
        values[key.decode('ascii')]=value.decode('ascii')
    boot._require(values.get('Id')==unit)
    if values.get('LoadState')=='not-found':
        boot._require(values.get('ActiveState')=='inactive' and values.get('ControlGroup','')=='')
        return values
    boot._require(set(values)==set(keys) and values['LoadState']=='loaded'
                  and re.fullmatch('[0-9a-f]{32}',values['InvocationID'])
                  and values['InvocationID']!='0'*32
                  and values['MemoryMax']==str(MEMORY_MAX) and values['TasksMax']==str(TASKS_MAX))
    return values


def _directory(path, *, uid, opaque=False):
    """Bind without widening permissions; O_PATH can retain a private final dir."""
    boot._require(type(path) is str and path.startswith('/sys/fs/cgroup/system.slice/')
                  and '..' not in Path(path).parts)
    parent=ns._acquire(lambda:os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW))
    try:
        parts=Path(path).parts[1:]
        for index,name in enumerate(parts):
            flags=os.O_PATH if opaque and index==len(parts)-1 else os.O_RDONLY
            child=ns._acquire(lambda:os.open(name,flags|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=parent.number))
            try:parent.close()
            except BaseException:child.close();raise
            parent=child
        cg._filesystem(parent.number)
        info=os.fstat(parent.number)
        boot._require(stat.S_ISDIR(info.st_mode) and info.st_uid==uid and not info.st_mode&0o022)
        return parent
    except BaseException:parent.close();raise


def _children(directory):
    directory.verify();result=[]
    names=os.listdir(directory.number)
    boot._require(len(names)<=128)
    for name in names:
        info=os.stat(name,dir_fd=directory.number,follow_symlinks=False)
        boot._require(not stat.S_ISLNK(info.st_mode))
        if stat.S_ISDIR(info.st_mode):result.append(name)
        else:boot._require(stat.S_ISREG(info.st_mode))
    return sorted(result)


def _events_empty(raw):
    # Version-bound cgroup.events schema, distinct from memory.events.
    return raw in (b'populated 0\nfrozen 0\n',b'frozen 0\npopulated 0\n')


def _removed(directory,path,mount):
    # kernfs getattr derives directory nlink from subdirectories even after
    # removal. Ordinary-filesystem nlink==0 is not a cgroup deletion test.
    directory.verify()
    boot._require(not os.path.lexists(path) and ns._mount_id(directory)==mount
                  and os.readlink(f'/proc/self/fd/{directory.number}')==path+' (deleted)')
    cg._filesystem(directory.number)


def _await_absence(unit,path,invocation,actors=()):
    deadline=time.monotonic()+5
    while True:
        current=_manager(unit)
        if current['LoadState']=='not-found' and not os.path.lexists(path):
            for descriptor in actors:
                descriptor.verify()
                boot._require(ns.backend._browser_pidfd_is_terminal(descriptor.number))
            return
        if current['LoadState']!='not-found':
            boot._require(current['InvocationID']==invocation,'observer_unit_replaced')
        boot._require(time.monotonic()<deadline,'observer_removal_not_observed')
        time.sleep(.05)


def _actor(pid, start, uid, gid, path, role):
    boot._require(type(pid) is int and pid>1 and type(start) is int and start>0)
    descriptor=ns._pidfd(pid)
    try:
        observed=ns._process(pid)
        raw=ns._read(f'/proc/{pid}/status',16384)
        status=ns.parse_status(raw)
        boot._require(observed['start']==start and status['uid']==[uid]*4 and status['gid']==[gid]*4
                      and status['groups']==[] and status['no_new_privs']==1
                      and ns.backend.parse_browser_cgroup_path(ns._read(f'/proc/{pid}/cgroup',4096))==path
                      and ns._process(pid)['start']==start
                      and not ns.backend._browser_pidfd_is_terminal(descriptor.number))
        if role in ('launcher','supervisor','worker'):
            boot.launcher.parse_postdrop_status(raw,uid=uid,gid=gid)
        else:
            boot._require(role in ('guardian','init'))
            last=ns._integer(ns._read('/proc/sys/kernel/cap_last_cap',32).strip())
            boot._require(last<64)
            for name in boot._CAPABILITIES:
                values=re.findall(rb'(?m)^'+name.encode()+rb':\s*([0-9a-f]{16})$',raw)
                boot._require(len(values)==1 and int(values[0],16)==(0 if name in ('CapInh','CapAmb') else (1<<(last+1))-1))
        return descriptor,observed,status
    except BaseException:descriptor.close();raise


@dataclass(frozen=True)
class DrainProof:
    generation: str
    source_commit: str
    source_tree: str
    unit: str
    uid: int
    gid: int
    aggregate: dict
    operation_succeeded: bool
    _owner: object
    _token: object


class ExternalObserver:
    def __init__(self):raise boot.BootstrapRefusal('use_owned_observer_creation')

    @classmethod
    def create(cls, account, binding):
        """Dormant native creation; cannot be invoked from worker/CLI switches."""
        from hosted_synthetic_identity import AccountLease
        from hosted_synthetic_source import HostedSourceBinding
        boot._require(type(account) is AccountLease and account._owner is account
                      and type(binding) is HostedSourceBinding and binding.commit==account.source_commit)
        account.verify_root()
        unit=account.identity.unit;slice_unit=slice_name(unit)
        path='/sys/fs/cgroup/system.slice/'+slice_unit
        boot._require(_manager(slice_unit)['LoadState']=='not-found' and not os.path.lexists(path),
                      'accounting_name_collision')
        value=object.__new__(cls);value._owner=value;value.account=account;value.binding=binding
        value.unit=unit;value.slice_unit=slice_unit;value.path=path;value.root=None;value.controls={}
        value.service=None;value.generation_dir=None;value.actors={};value.generation=None
        value.closed=False;value.failed=False;value.descriptors_closed_successfully=False;value.creation_acknowledged=False;value.work_acknowledged=False
        value.service_invocation=None;value.aggregate=None;value.proof=None
        # Attribution must already be durable before the first native mutation.
        account.journal.record('accounting_create_attempt')
        try:
            code,raw=boot._collect_native_client(plan_slice(unit),cwd='/')
            boot._require(code==0 and re.fullmatch(rb'o "/org/freedesktop/systemd1/job/[1-9][0-9]*"\n',raw),
                          'accounting_creation_acknowledgment_missing')
            value.job=raw.decode('ascii').strip()
            observed=None
            for _ in range(20):
                observed=_manager(slice_unit)
                if observed.get('ActiveState')=='active':break
                time.sleep(.05)
            boot._require(observed['ActiveState']=='active' and observed['ControlGroup']==path[len('/sys/fs/cgroup'):])
            value.invocation=observed['InvocationID']
            value.root=_directory(path,uid=0)
            value.mount=ns._mount_id(value.root)
            for name in _CONTROLS:
                value.controls[name]=ns._acquire(lambda:os.open(name,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=value.root.number))
            boot._require(not _children(value.root) and value.read('cgroup.procs')==value.read('cgroup.threads')==b''
                          and _events_empty(value.read('cgroup.events')))
            value.sample();value.creation_acknowledged=True
            account.journal.record('accounting_created',{'accounting_identity':list(value.root.identity),
                'mount_id':value.mount,'invocation_id':value.invocation})
            return value
        except BaseException as error:
            value.failed=True;account.uncertain=True
            try:value._close_descriptors()
            except BaseException:error.add_note('external_observer_descriptor_cleanup_failed')
            raise boot.BootstrapCleanupError('accounting_creation_uncertain_identity_retained') from error

    @_sticky_observation
    def verify(self):
        boot._require(self._owner is self and not self.closed and not self.failed and self.root is not None)
        self.root.verify()
        info=os.stat(self.path,follow_symlinks=False)
        boot._require(ns._identity(info)==self.root.identity and info.st_uid==0
                      and not info.st_mode&0o022 and ns._mount_id(self.root)==self.mount)
        manager=_manager(self.slice_unit)
        boot._require(manager['ActiveState']=='active' and manager['InvocationID']==self.invocation
                      and manager['ControlGroup']==self.path[len('/sys/fs/cgroup'):])

    @_sticky_observation
    def read(self,name):
        boot._require(name in self.controls)
        descriptor=self.controls[name];descriptor.verify()
        info=os.fstat(descriptor.number)
        boot._require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
                      and ns._identity(os.stat(name,dir_fd=self.root.number,follow_symlinks=False))==descriptor.identity
                      and ns._mount_id(descriptor)==self.mount
                      and fcntl.fcntl(descriptor.number,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY)
        raw=os.pread(descriptor.number,4097,0)
        boot._require(len(raw)<=4096)
        return raw

    @_sticky_observation
    def sample(self):
        self.verify()
        selected=cg._memory_observation(self.read,MEMORY_MAX,self.aggregate)
        boot._require(cg.parse_scalar(self.read('pids.max'))==TASKS_MAX)
        self.aggregate=selected
        return selected

    @_sticky_observation
    def bind_ready(self,frame):
        """Same live generation, before sending work ACK; no child PASS trusted."""
        boot._require(self.creation_acknowledged and not self.work_acknowledged and self.generation is None
                      and type(frame) is dict and set(frame)=={'generation','commit','tree','unit','actors','generation_identity'}
                      and frame['unit']==self.unit and frame['commit']==self.binding.commit and frame['tree']==self.binding.tree
                      and re.fullmatch('[0-9a-f]{64}',frame['generation'])
                      and type(frame['actors']) is dict and set(frame['actors'])==set(_ROLES))
        self.verify();identity=self.account.identity
        manager=_manager(self.unit,service=True)
        path=service_path(self.unit)
        boot._require(manager['ActiveState'] in ('activating','active') and manager['ControlGroup']==path)
        self.service_invocation=manager['InvocationID']
        self.service=_directory('/sys/fs/cgroup'+path,uid=identity.uid)
        self.generation=frame['generation']
        name='buffalo-'+self.generation
        boot._require(_children(self.root)==[self.unit] and _children(self.service)==sorted(['supervisor',name]))
        self.generation_dir=_directory('/sys/fs/cgroup'+path+'/'+name,uid=identity.uid,opaque=True)
        boot._require(list(self.generation_dir.identity)==frame['generation_identity']
                      and stat.S_IMODE(os.fstat(self.generation_dir.number).st_mode)==0o700)
        records={};statuses={}
        for role in _ROLES:
            item=frame['actors'][role]
            boot._require(type(item) is dict and set(item)=={'pid','start'})
            selected_path=path+('/'+name if role in ('init','worker') else '/supervisor')
            descriptor,records[role],statuses[role]=_actor(item['pid'],item['start'],identity.uid,identity.gid,selected_path,role)
            self.actors[role]=descriptor
        boot._require(len({item['pid'] for item in records.values()})==len(_ROLES)
                      and manager['MainPID']==str(records['launcher']['pid'])
                      and all(records[child]['parent']==records[parent]['pid'] for parent,child in zip(_ROLES,_ROLES[1:]))
                      and records['worker']['state']=='T'
                      and statuses['init']['nspid']==[records['init']['pid'],1]
                      and statuses['worker']['nspid']==[records['worker']['pid'],2])
        self.sample()
        self.account.service_creation_acknowledged=True
        self.account.journal.record('external_generation_bound',{'generation':self.generation,
            'invocation_id':self.service_invocation,'service_identity':list(self.service.identity),
            'generation_identity':list(self.generation_dir.identity),'actors':frame['actors']})
        return _PREFIX+json.dumps({'ack':self.generation},sort_keys=True,separators=(',',':')).encode()+b'\n'

    @_sticky_observation
    def acknowledge_sent(self):
        boot._require(self._owner is self and not self.closed and not self.failed
                      and self.generation is not None and not self.work_acknowledged and len(self.actors)==5)
        self.work_acknowledged=True

    @_sticky_observation
    def finish_service(self, *, operation_succeeded):
        """Whole-service lifetime ends while the accounting parent is still live."""
        boot._require(type(operation_succeeded) is bool and self.creation_acknowledged and self.work_acknowledged
                      and self.generation is not None and len(self.actors)==5 and not self.failed)
        self.verify()
        service='/sys/fs/cgroup'+service_path(self.unit)
        _await_absence(self.unit,service,self.service_invocation,self.actors.values())
        _removed(self.service,service,self.mount)
        _removed(self.generation_dir,service+'/buffalo-'+self.generation,self.mount)
        boot._require(_children(self.root)==[] and self.read('cgroup.procs')==self.read('cgroup.threads')==b''
                      and _events_empty(self.read('cgroup.events')))
        from hosted_synthetic_identity import identity_processes
        boot._require(not identity_processes(self.account.identity))
        final=self.sample()  # ENODEV is failure; never reuse a prior sample.
        identity=self.account.identity
        self.proof=DrainProof(self.generation,self.binding.commit,self.binding.tree,self.unit,identity.uid,identity.gid,
                             final,operation_succeeded,self,_TOKEN)
        self.account.journal.record('external_service_drained')
        return self.proof

    def _close_descriptors(self):
        boot._require(self._owner is self and not self.closed,'external_observer_owner_refused')
        errors=[]
        for descriptor in [*reversed(tuple(self.actors.values())),self.generation_dir,self.service,
                           *reversed(tuple(self.controls.values())),self.root]:
            if descriptor is not None:
                try:descriptor.close()
                except BaseException as error:errors.append(error)
        self.closed=True
        self.descriptors_closed_successfully=not errors
        if errors:
            self.failed=True
            raise boot.BootstrapCleanupError('external_observer_descriptor_cleanup_failed') from errors[0]

    def close(self):
        """Only stop a bound empty owned slice. Always close retained handles."""
        boot._require(self._owner is self and not self.closed,'external_observer_owner_refused')
        error=None
        try:
            boot._require(self.proof is not None and self.proof._owner is self and self.proof._token is _TOKEN
                          and not self.failed and not self.closed)
            self.verify()
            boot._require(not _children(self.root) and self.read('cgroup.procs')==self.read('cgroup.threads')==b''
                          and _events_empty(self.read('cgroup.events')))
            final=self.sample()
            # finish_service's snapshot is provisional: residual charges can
            # still change before stopping the now-empty accounting envelope.
            # Publish the last validated kernel sample, never the earlier copy.
            self.proof=replace(self.proof,aggregate=final)
            code,_=boot._collect_native_client(_native(('/usr/bin/systemctl','--system','--no-ask-password','stop',self.slice_unit)),cwd='/')
            boot._require(code==0)
            _await_absence(self.slice_unit,self.path,self.invocation)
            _removed(self.root,self.path,self.mount)
            self.account.journal.record('external_accounting_removed')
        except BaseException as selected:
            error=selected;self.failed=True;self.account.uncertain=True
        try:self._close_descriptors()
        except BaseException as selected:
            self.account.uncertain=True
            if error is None:error=selected
        if error is not None:raise boot.BootstrapCleanupError('external_cleanup_unverified_identity_retained') from error


def require_drain_proof(proof, account):
    boot._require(type(proof) is DrainProof and proof._token is _TOKEN and type(proof._owner) is ExternalObserver
                  and proof._owner._owner is proof._owner and proof._owner.account is account
                  and proof._owner.proof is proof and proof._owner.closed and not proof._owner.failed
                  and proof._owner.descriptors_closed_successfully is True
                  and proof.aggregate is proof._owner.aggregate
                  and (proof.uid,proof.gid,proof.unit)==(account.identity.uid,account.identity.gid,account.identity.unit),
                  'external_drain_proof_required')


def await_external_observer(*,unit,generation,binding,lease,guardian,guardian_start,outer,deadline):
    """S blocks before CLOSE; I/W stay retained until exact external ACK.

    G normalizes stdin/stdout before unshare; no worker receives this channel.
    The outside process independently binds manager, kernel objects and actors.
    """
    boot._require(str(lease.delegation)=='/sys/fs/cgroup'+service_path(unit)
                  and time.monotonic()<deadline
                  and stat.S_ISFIFO(os.fstat(0).st_mode) and stat.S_ISFIFO(os.fstat(1).st_mode))
    launcher=ns._process(os.getppid());supervisor=ns._process(os.getpid())
    actors={'launcher':{'pid':launcher['pid'],'start':launcher['start']},
            'supervisor':{'pid':supervisor['pid'],'start':supervisor['start']},
            'guardian':{'pid':guardian,'start':guardian_start},
            'init':{'pid':outer['init_pid'],'start':outer['init_start']},
            'worker':{'pid':outer['worker_pid'],'start':outer['worker_start']}}
    frame={'unit':unit,'generation':generation,'commit':binding.commit,'tree':binding.tree,
           'actors':actors,'generation_identity':list(lease.child.identity)}
    raw=_PREFIX+json.dumps(frame,sort_keys=True,separators=(',',':')).encode()+b'\n'
    boot._require(len(raw)<=4096)
    with selectors.DefaultSelector() as ready:
        ready.register(1,selectors.EVENT_WRITE)
        boot._require(ready.select(max(0,deadline-time.monotonic())),'external_observer_timeout')
        boot._require(time.monotonic()<deadline,'external_observer_timeout')
        boot._require(os.write(1,raw)==len(raw),'external_observer_write_failed')
    expected=_PREFIX+json.dumps({'ack':generation},sort_keys=True,separators=(',',':')).encode()+b'\n'
    received=bytearray()
    with selectors.DefaultSelector() as ready:
        ready.register(0,selectors.EVENT_READ)
        while not received.endswith(b'\n'):
            boot._require(time.monotonic()<deadline,'external_observer_timeout')
            boot._require(ready.select(max(0,deadline-time.monotonic())),'external_observer_timeout')
            block=os.read(0,257-len(received))
            boot._require(block and len(received)+len(block)<=256,'external_observer_ack_refused')
            received.extend(block)
    boot._require(time.monotonic()<deadline and bytes(received)==expected,'external_observer_ack_refused')
