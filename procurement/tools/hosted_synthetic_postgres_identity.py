"""Nonzero PG worker identity inside an already projected disposable U1 child.

No host account, host mapping, cgroup, mount, network, process spawn, application
or credential operation. This dormant entry is not wired into the diagnostic
launcher. After any partial transition the owning init must terminate/reap this
child; neither rollback nor successful external cleanup is claimed here.
"""
from __future__ import annotations

import ctypes
import fcntl
import os
import signal
import stat

import hosted_synthetic_namespace as ns
import hosted_synthetic_worker as worker
import hosted_synthetic_native_core as native_core
from hosted_synthetic_cgroup import _signal_boundary

INNER_ID=1000
_MAPPING=b'1000 0 1\n'


def _namespaces(pid):
    result=ns._namespaces(pid)
    result['cgroup']=list(ns._identity(os.stat('/proc/'+pid+'/ns/cgroup')))
    return result


def _outer_identity():
    ns._require(os.getpid()>1 and os.getppid()==1 and os.getgroups()==[]
                and len(os.listdir('/proc/self/task'))==1)
    ns._require_mapped_identity()
    ns._require(worker._get_prctl(3)==0)
    # Nondumpable proc ownership comes from mm->user_ns. This checks the U1
    # reexec prerequisite before creating U2; never make the process dumpable
    # merely to gain write access to its map files.
    # The world-readable /proc/<pid> directory is exempt from this ownership
    # rule; a regular per-process map file is not.
    owner=os.stat('/proc/self/uid_map')
    ns._require(stat.S_ISREG(owner.st_mode) and owner.st_uid==owner.st_gid==0)
    for name in ('uid','gid'):
        values=ns._read('/proc/self/'+name+'_map',128).split()
        ns._require(len(values)==3 and values[0]==b'0' and values[2]==b'1'
                    and ns._integer(values[1])>0)
    ns._require(ns._read(f'/proc/self/task/{os.getpid()}/children',4096)==b'')


def _stdio(channels,*,expected_id=0):
    ns._require(type(expected_id) is int and expected_id in (0,INNER_ID))
    reserved=set()
    for descriptor in channels:
        descriptor.verify();reserved.add(ns._identity(os.fstat(descriptor.number)))
    result=[]
    for number in (0,1,2):
        info=os.fstat(number);flags=fcntl.fcntl(number,fcntl.F_GETFL)
        ns._require(ns._identity(info) not in reserved and not flags & os.O_PATH)
        if stat.S_ISCHR(info.st_mode):
            ns._require(info.st_rdev==os.makedev(1,3) and os.readlink(f'/proc/self/fd/{number}')=='/dev/null'
                and flags & os.O_ACCMODE in ((os.O_RDONLY,os.O_RDWR) if number==0 else (os.O_WRONLY,os.O_RDWR)))
        else:
            ns._require(stat.S_ISFIFO(info.st_mode) and info.st_uid==info.st_gid==expected_id
                and flags & os.O_ACCMODE==(os.O_RDONLY if number==0 else os.O_WRONLY))
        result.append({'number':number,'identity':list(ns._identity(info)),'mode':stat.S_IFMT(info.st_mode),
            'access':flags & os.O_ACCMODE})
    return result


def _parent_boundary(parent,identity):
    parent.verify()
    ns._require(os.getppid()==1 and not ns.backend._browser_pidfd_is_terminal(parent.number)
                and ns._process(1)['start']==identity['start']
                and ns._process(1)['parent']==0)
    info=ns.backend._read_browser_pidfd_metadata(parent.number)
    ns._require(info[1]==1)


def _write_map(name,raw):
    ns._require((name,raw) in (('uid_map',_MAPPING),('setgroups',b'deny\n'),('gid_map',_MAPPING)))
    descriptor=ns._acquire(lambda:os.open('/proc/self/'+name,os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW))
    error=None
    try:
        ns._require(stat.S_ISREG(os.fstat(descriptor.number).st_mode))
        # These proc interfaces require one complete write; retrying a short
        # namespace-map write would not safely reconstruct a partial mapping.
        ns._require(os.write(descriptor.number,raw)==len(raw))
    except BaseException as failure:error=failure
    try:descriptor.close()
    except BaseException as cleanup:
        raise ns.NamespaceCleanupError('postgres_identity_map_close_refused') from (error or cleanup)
    if error is not None:raise error


def _inner_identity():
    ns._require(os.getresuid()==os.getresgid()==(INNER_ID,)*3 and os.getgroups()==[])
    current=worker._status()
    ns._require(current['uid']==current['gid']==[INNER_ID]*4 and current['groups']==[]
                and current['no_new_privs']==1)
    for name in ('uid','gid'):
        ns._require(ns._read('/proc/self/'+name+'_map',128).split()==_MAPPING.split())
    ns._require(ns._read('/proc/self/setgroups',32)==b'deny\n')
    return current


def _death_signal():
    value=ctypes.c_int()
    ns._require(ns.backend._browser_libc().prctl(2,ctypes.byref(value),0,0,0)==0
                and value.value==signal.SIGKILL)


def restrict_postgres_worker(root_identity,root_mount,*,channels=()):
    """Irreversible mapped-child-only path; no release or native exec authority.

    Caller owns the cancellation envelope and stopped child lifecycle. All
    inherited control handles must already be absent except explicit FIFO
    protocol channels. U2 maps exactly the caller's one U1 ID to nonzero1000;
    no additional host ID or privilege is provisioned. The fixed filter is
    installed only after mapping and erasure, and then forbids new namespaces.
    """
    _signal_boundary()
    _outer_identity()
    root=worker.require_projected_root(root_identity,root_mount,channels=channels)
    stdio=_stdio(channels)
    namespaces=_namespaces('self')
    ns._require(namespaces==_namespaces('1'))
    cgroup=ns._read('/proc/self/cgroup',4096)
    process=ns._process(os.getpid())
    parent_identity=ns._process(1)
    ns._require(parent_identity['parent']==0 and process['parent']==1)
    parent=ns._pidfd(1);result=None;error=None
    try:
        _parent_boundary(parent,parent_identity)
        ns._hardening(1)
        os.unshare(os.CLONE_NEWUSER)
        changed=_namespaces('self')
        ns._require(changed['user']!=namespaces['user'] and
            all(changed[name]==namespaces[name] for name in namespaces if name!='user'))
        for name,raw in (('uid_map',_MAPPING),('setgroups',b'deny\n'),('gid_map',_MAPPING)):
            _parent_boundary(parent,parent_identity)
            ns._require(os.getgroups()==[])
            _write_map(name,raw)
        _inner_identity()
        # Namespace/credential transitions cannot leave a stale death binding.
        ns._hardening(1);_death_signal()
        capabilities=worker._erase_capabilities(_inner_identity())
        policy=worker.install_filter()
        core=native_core.enforce_native_core()
        final=_inner_identity()
        ns._require(all(final[key]==0 for key in worker._CAPS)
                    and worker._get_prctl(27)==worker._SECUREBITS
                    and worker._get_prctl(3)==0
                    and _namespaces('self')==changed
                    and ns._read('/proc/self/cgroup',4096)==cgroup
                    and ns._process(os.getpid())['start']==process['start'])
        _parent_boundary(parent,parent_identity);_death_signal()
        result={'contract':'BUFFALO_HOSTED_POSTGRES_IDENTITY_V1','uid':INNER_ID,'gid':INNER_ID,
            'groups':[],'capabilities':capabilities,'no_new_privs':1,'dumpable':0,
            'namespaces':changed,'root':root,'stdio':stdio,'seccomp':policy,'native_core':core,
            'parent_death_signal':int(signal.SIGKILL),
            'execution_authority':False,'credential_release':'WITHHELD',
            'external_containment_and_cleanup':'NOT_PROVEN',
            'same_kernel_uid_control_exclusion':'NOT_PROVEN','postdrop_resource_access':'NOT_PROVEN'}
    except BaseException as failure:error=failure
    try:parent.close()
    except BaseException as cleanup:
        raise ns.NamespaceCleanupError('postgres_identity_parent_close_refused') from (error or cleanup)
    if error is not None:raise error
    # Closing the temporary parent pidfd must leave only the exact protocol FDs.
    worker.require_projected_root(root_identity,root_mount,channels=channels)
    ns._require(_stdio(channels,expected_id=INNER_ID)==stdio)
    ns._require(result is not None)
    return result
