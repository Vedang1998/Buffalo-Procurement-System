"""Fixed native version-image ownership, not a launcher or execution grant.

The projected phase init prepares an immutable in-memory specification before
fork. Its reduced U2 child reopens only that fixed PG image/loader on the sealed
root. No bundle/control descriptor crosses the reduction boundary. This module
never forks, signals, acknowledges enrollment, executes code or accepts secrets.
The retained stopped-child/ACK lifecycle must still be connected separately.
"""
from __future__ import annotations

from dataclasses import dataclass,field
import fcntl
import hashlib
import os
import stat

import hosted_synthetic_namespace as ns
import hosted_synthetic_runtime as runtime
import hosted_synthetic_postgres_runtime as pg
import hosted_synthetic_postgres_identity as identity
import hosted_synthetic_native_core as core
import hosted_synthetic_worker as worker
from hosted_synthetic_cgroup import _signal_boundary
from hosted_synthetic_source import _resource_operation

ROLES=('postgres','initdb','pg_dump','pg_restore','pg_controldata')
_TOKEN=object()


@dataclass(frozen=True)
class VersionSpec:
    generation:str
    source_commit:str
    source_tree:str
    bundle_sha256:str
    role:str
    executable:str
    executable_sha256:str
    loader:str
    loader_sha256:str
    root_identity:tuple
    root_mount:int
    init_start:int
    namespaces:tuple
    filter_count:int
    _owner:object=field(default=None,init=False,repr=False,compare=False)
    _token:object=field(default=None,init=False,repr=False,compare=False)

    def verify(self):
        ns._require(self._owner is self and self._token is _TOKEN
            and self.role in ROLES and self.executable==pg.BIN+self.role)


def prepare_version_spec(value,root_observation,role):
    """Actual projected PID1 prepares one fixed role; no child creation."""
    _signal_boundary()
    ns._require(type(role) is str and role in ROLES and os.getpid()==1 and os.getppid()==0)
    ns._require_mapped_identity()
    ns._require(ns.parse_status(ns._read('/proc/self/status',16384))['nspid']==[1]
        and ns._read('/proc/self/task/1/children',4096)==b'')
    proof=pg.verify_postgres_phase_consumer(value,root_observation)
    entries=pg._phase_inventory(value)
    path=pg.BIN+role;loader=runtime.parse_elf(entries[path].content)['interpreter']
    ns._require(loader in value.native_files and entries[loader].mode==0o555)
    current=worker._status();init=ns._process(1)
    ns._require(init['parent']==0)
    spec=VersionSpec(value.generation,value.source_commit,value.source_tree,value.bundle.sha256,
        role,path,value.native_files[path],loader,value.native_files[loader],
        tuple(proof['root_identity']),proof['root_mount_id'],init['start'],
        tuple(sorted((k,tuple(v)) for k,v in identity._namespaces('self').items())),current['Seccomp_filters']+1)
    object.__setattr__(spec,'_owner',spec);object.__setattr__(spec,'_token',_TOKEN)
    return spec


def _worker_entry(spec):
    ns._require(type(spec) is VersionSpec)
    spec.verify();_signal_boundary();core._entry()
    ns._require(os.getpid()>1 and os.getppid()==1
        and ns._process(1)['start']==spec.init_start and ns._process(1)['parent']==0)
    identity._inner_identity();identity._death_signal()
    current=identity._namespaces('self')
    expected={k:list(v) for k,v in spec.namespaces}
    # W has no ptrace authority upward into nondumpable I's U1. Bind to the
    # tuple actually captured by I before fork; fresh I/G observations belong
    # to the trusted stopped-child lifecycle, not a privileged worker fallback.
    ns._require(set(current)==set(expected) and current['user']!=expected['user']
        and all(current[k]==expected[k] for k in expected if k!='user')
        and worker._status()['Seccomp_filters']==spec.filter_count
        and ns.parse_status(ns._read('/proc/self/status',16384))['nspid']==[os.getpid()])


def _root(spec):
    ns._require(ns._identity(os.stat('/'))==ns._identity(os.stat('.'))==spec.root_identity)
    root=ns._acquire(lambda:os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW))
    error=None
    try:
        flags=os.fstatvfs(root.number).f_flag
        ns._require(root.identity==spec.root_identity and ns._mount_id(root)==spec.root_mount
            and ns.backend._browser_fstatfs_magic(root.number)==0x01021994
            and flags & os.ST_RDONLY and flags & os.ST_NOSUID and flags & os.ST_NODEV
            and not flags & os.ST_NOEXEC)
    except BaseException as failure:error=failure
    try:root.close()
    except BaseException as cleanup:raise ns.NamespaceCleanupError('postgres_root_close_refused') from (error or cleanup)
    if error is not None:raise error


def _image_bytes(descriptor,path,digest,spec):
    descriptor.verify()
    before=os.fstat(descriptor.number)
    ns._require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode)==0o555
        and before.st_uid==before.st_gid==identity.INNER_ID and before.st_nlink==1
        and 0<before.st_size<=runtime.MAX_BYTES and before.st_dev==spec.root_identity[0]
        and ns._mount_id(descriptor)==spec.root_mount
        and fcntl.fcntl(descriptor.number,fcntl.F_GETFL)&(os.O_ACCMODE|os.O_PATH|os.O_NONBLOCK)==os.O_RDONLY
        and not os.get_inheritable(descriptor.number)
        and ns._identity(os.stat(path,follow_symlinks=False))==descriptor.identity)
    data=os.pread(descriptor.number,before.st_size+1,0)
    ns._require(len(data)==before.st_size and hashlib.sha256(data).hexdigest()==digest
        and runtime._metadata(before)==runtime._metadata(os.fstat(descriptor.number))
        ==runtime._metadata(os.stat(path,follow_symlinks=False)))
    runtime.parse_elf(data)
    return data


def _open_image(path,digest,spec):
    descriptor=ns._acquire(lambda:os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
    try:return descriptor,_image_bytes(descriptor,path,digest,spec)
    except BaseException as error:
        try:descriptor.close()
        except BaseException:raise ns.NamespaceCleanupError('postgres_image_open_cleanup_refused') from error
        raise


def _descriptors(image,channels):
    allowed={0,1,2,image.number}
    for channel in channels:
        channel.verify();info=os.fstat(channel.number)
        ns._require(channel.number>2 and channel.number not in allowed and stat.S_ISFIFO(info.st_mode)
            and not os.get_inheritable(channel.number))
        allowed.add(channel.number)
    observed=set()
    for name in os.listdir('/proc/self/fd'):
        try:os.fstat(int(name))
        except OSError as error:
            if error.errno==9:continue
            raise
        observed.add(int(name))
    ns._require(observed==allowed)


class VersionImage:
    def __init__(self):raise ns.NamespaceProofError('use_postdrop_version_image')

    def verify(self):
        ns._require(self._owner is self and not self.closed and self.pid==os.getpid())
        _worker_entry(self.spec);_root(self.spec)
        data=_image_bytes(self.descriptor,self.spec.executable,self.spec.executable_sha256,self.spec)
        ns._require(runtime.parse_elf(data)['interpreter']==self.spec.loader)
        loader=None;error=None
        try:
            loader,_=_open_image(self.spec.loader,self.spec.loader_sha256,self.spec)
            ns._require(loader.identity==self.loader_identity)
        except BaseException as failure:error=failure
        if loader is not None:
            try:loader.close()
            except BaseException as cleanup:raise ns.NamespaceCleanupError('postgres_loader_close_refused') from (error or cleanup)
        if error is not None:raise error
        _root(self.spec);_descriptors(self.descriptor,self.channels)
        identity._stdio(self.channels,expected_id=identity.INNER_ID)
        return {'scope':'FIXED_POSTGRES_VERSION_IMAGE_ONLY','role':self.spec.role,
            'generation':self.spec.generation,'source_commit':self.spec.source_commit,'source_tree':self.spec.source_tree,
            'bundle_sha256':self.spec.bundle_sha256,'image_identity':list(self.descriptor.identity),
            'image_sha256':self.spec.executable_sha256,'loader_identity':list(self.loader_identity),
            'loader_sha256':self.spec.loader_sha256,'execution_authority':False,'native_execution':'NOT_RUN',
            'loader_observation':'REOPENED_VERIFIED_CLOSED','stopped_enrollment':'NOT_PROVEN','credential_release':'WITHHELD'}

    def close(self):
        ns._require(self._owner is self and self.pid==os.getpid())
        if self.closed:
            ns._require(not self.cleanup_failed);return
        # Always close the owned image even after an entry precondition fails.
        # Descriptor identity verification preserves any foreign replacement.
        try:self.descriptor.close()
        except BaseException:
            self.cleanup_failed=True;raise
        finally:self.closed=True


def open_version_image(spec,*,channels=()):
    result=None
    @_resource_operation
    def guarded():
        nonlocal result
        _worker_entry(spec)
        ns._require(type(channels) is tuple)
        worker.require_projected_root(spec.root_identity,spec.root_mount,channels=channels)
        identity._stdio(channels,expected_id=identity.INNER_ID)
        image=None;loader=None;value=None;error=None
        try:
            image,data=_open_image(spec.executable,spec.executable_sha256,spec)
            ns._require(runtime.parse_elf(data)['interpreter']==spec.loader)
            loader,_=_open_image(spec.loader,spec.loader_sha256,spec)
            value=object.__new__(VersionImage)
            value._owner=value;value.spec=spec;value.pid=os.getpid();value.descriptor=image
            value.channels=channels;value.loader_identity=loader.identity;value.closed=False;value.cleanup_failed=False
        except BaseException as failure:error=failure
        if loader is not None:
            try:loader.close()
            except BaseException as failure:
                if error is None:
                    error=ns.NamespaceCleanupError('postgres_loader_close_refused');error.__cause__=failure
                else:error.add_note('postgres_loader_close_refused')
        if error is None:
            try:value.verify()
            except BaseException as failure:error=failure
        if error is not None:
            if image is not None:
                try:image.close()
                except BaseException:raise ns.NamespaceCleanupError('postgres_image_acquire_cleanup_refused') from error
            raise error
        result=value;return value
    try:return guarded()
    except BaseException as error:
        if result is not None:
            try:result.close()
            except BaseException:raise ns.NamespaceCleanupError('postgres_image_cancel_cleanup_refused') from error
        raise
