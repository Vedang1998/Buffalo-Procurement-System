"""Bounded pre-account checks; no account reservation or permission repair.

Only verified native stat/find receive setup privilege. Python and Git remain
unprivileged. Absence is limited to the visible host mounts and initial IPC
namespace at observation time; trusted administration must not race allocation.
"""
from contextlib import contextmanager
import errno
import hashlib
import collections
import math
import os
import re
from pathlib import Path
import stat
import tempfile
import time

import hosted_synthetic_bootstrap as boot
import hosted_synthetic_namespace as ns
from hosted_synthetic_source import _git, _resource_operation

POLICY='BUFFALO_ACCOUNT_PREFLIGHT_V1'
MAX_FILES=50000
MAX_BYTES=256*1024**2


class OwnershipCollision(boot.BootstrapRefusal):
    """Definite pre-reservation ownership collision, never incomplete coverage."""


def parse_ipc(raw,uid,gid):
    boot._require(type(raw) is bytes and raw.endswith(b'\n') and len(raw)<=1024**2,'ipc_coverage_incomplete')
    rows=raw.splitlines();boot._require(bool(rows),'ipc_schema_refused')
    columns=rows[0].split();required=(b'uid',b'gid',b'cuid',b'cgid')
    boot._require(len(columns)==len(set(columns)) and all(k in columns for k in required),'ipc_schema_refused')
    for row in rows[1:]:
        values=row.split();boot._require(len(values)==len(columns),'ipc_schema_refused')
        selected=[values[columns.index(k)] for k in required]
        boot._require(all(x.isdigit() and int(x)<=2**32-1 for x in selected),'ipc_schema_refused')
        if uid in (int(selected[0]),int(selected[2])) or gid in (int(selected[1]),int(selected[3])):
            raise OwnershipCollision('dedicated_ipc_ownership_collision')
    return len(rows)-1


_BINFMT='/proc/sys/fs/binfmt_misc'
_BINFMT_MAGIC=0x42494e4d


def _mount_plan(raw):
    boot._require(type(raw) is bytes and raw.endswith(b'\n') and len(raw)<=1024**2,'mount_coverage_incomplete')
    seen=set();special=[]
    for row in raw.splitlines():
        left,sep,right=row.partition(b' - ');fields=left.split();tail=right.split()
        boot._require(sep and len(fields)>=6 and len(tail)==3 and fields[0] not in seen
                      and fields[0].isdigit() and int(fields[0])>0,'mount_schema_refused')
        seen.add(fields[0]);target=fields[4];kind=tail[0]
        if kind==b'vfat':
            boot._require(target==b'/boot/efi','filesystem_scope_requires_review')
        elif kind==b'autofs':
            boot._require(target==_BINFMT.encode(),'filesystem_scope_requires_review')
        else:
            boot._require(kind in (b'ext4',b'xfs',b'btrfs',b'tmpfs',b'devtmpfs',b'proc',b'sysfs',b'cgroup2',b'devpts',b'mqueue',b'securityfs',b'debugfs',b'tracefs',b'pstore',b'efivarfs',b'configfs',b'bpf',b'hugetlbfs',b'fusectl',b'binfmt_misc',b'squashfs'), 'filesystem_scope_requires_review')
        if target==b'/proc' or target.startswith(b'/proc/'):
            boot._require(kind==b'proc' or (target==_BINFMT.encode() and kind in (b'autofs',b'binfmt_misc')),
                          'excluded_mount_requires_review')
        if target==_BINFMT.encode():special.append((int(fields[0]),kind))
    active=[number for number,kind in special if kind==b'binfmt_misc']
    boot._require(not special or (len(active)==1 and len(special)<=2
                  and all(kind in (b'autofs',b'binfmt_misc') for _,kind in special)),
                  'binfmt_active_mount_required')
    return hashlib.sha256(raw).hexdigest(),(active[0] if active else None)


def _mount_scope(raw):return _mount_plan(raw)[0]


def _open_binfmt(mount_id):
    held=None
    try:
        # O_PATH on the final inactive autofs endpoint does not trigger it.
        # O_NOFOLLOW refuses a link; O_DIRECTORY requires the actual directory.
        held=ns._acquire(lambda:os.open(_BINFMT,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC))
        boot._require(ns._mount_id(held)==mount_id
                      and ns.backend._browser_fstatfs_magic(held.number)==_BINFMT_MAGIC,
                      'binfmt_active_mount_required')
        return held
    except BaseException as error:
        if held is not None:
            try:held.close()
            except BaseException:raise boot.BootstrapCleanupError('binfmt_descriptor_cleanup_refused') from error
        if isinstance(error,ns.NamespaceCleanupError):
            raise boot.BootstrapCleanupError('binfmt_descriptor_cleanup_refused') from error
        if isinstance(error,(OSError,ns.NamespaceProofError)):
            raise boot.BootstrapRefusal('binfmt_mount_unreadable') from error
        raise


@contextmanager
def _scan_mount_lease(raw):
    digest,mount_id=_mount_plan(raw);held=None
    try:
        if mount_id is not None:held=_open_binfmt(mount_id)
        yield digest,(() if held is None else (_BINFMT,))
        if held is not None:
            held.verify();named=_open_binfmt(mount_id)
            try:boot._require(named.identity==held.identity,'binfmt_mount_changed')
            finally:
                try:named.close()
                except BaseException as error:raise boot.BootstrapCleanupError('binfmt_descriptor_cleanup_refused') from error
    finally:
        if held is not None:
            try:held.close()
            except BaseException as error:raise boot.BootstrapCleanupError('binfmt_descriptor_cleanup_refused') from error


@_resource_operation
def observe_scan_host(binding):
    """Read-only target inventory, before spending any privileged proof.

    Fixed public mount points/types and hashes only. Do not traverse automounts,
    change the allowlist, run sudo/find, create namespaces or reserve an identity.
    Direct PID1 access is observed as a prerequisite condition, not a repaired
    native-stat PASS. Actual native execution remains independently review-gated.
    """
    from hosted_synthetic_source import HostedSourceBinding
    boot._require(type(binding) is HostedSourceBinding and os.geteuid()>0)
    raw=ns._read('/proc/self/mountinfo',1024**2)
    boot._require(raw.endswith(b'\n') and len(raw)<=1024**2,'mount_coverage_incomplete')
    counts=collections.Counter();selected={};under_proc=[]
    fixed={b'/',b'/proc',b'/sys',b'/boot/efi',b'/proc/sys/fs/binfmt_misc'}
    for row in raw.splitlines():
        left,sep,right=row.partition(b' - ');fields=left.split();tail=right.split()
        boot._require(sep and len(fields)>=6 and len(tail)==3
                      and re.fullmatch(rb'[a-zA-Z0-9_.-]{1,64}',tail[0]),'mount_schema_refused')
        kind=tail[0].decode('ascii');counts[kind]+=1;target=fields[4]
        if target in fixed:
            selected.setdefault(target.decode(),[]).append(kind)
        if target.startswith(b'/proc/') and kind!='proc':
            under_proc.append({'filesystem':kind,'target_sha256':hashlib.sha256(target).hexdigest()})
    active_mount='NOT_OBSERVED'
    try:
        _mount_scope(raw)
        with _scan_mount_lease(raw) as (_,roots):
            boot._require(ns._read('/proc/self/mountinfo',1024**2)==raw,'mount_coverage_changed')
        active_mount='ACTIVE_BINFMT_FD_OBSERVED_AND_CLOSED' if roots else 'NO_BINFMT_MOUNT'
    except boot.BootstrapRefusal as error:
        reason=str(error)
        boot._require(reason in ('filesystem_scope_requires_review','excluded_mount_requires_review',
            'binfmt_active_mount_required','binfmt_mount_unreadable','binfmt_mount_changed','mount_coverage_changed'))
        policy='REFUSED'
    else:policy='SCHEMA_AND_FIXED_MOUNT_READBACK_OBSERVED';reason=None
    pid1=os.stat('/proc/1',follow_symlinks=False)
    namespaces={}
    for name in ('ipc','mnt'):
        try:info=os.stat('/proc/1/ns/'+name)
        except OSError as error:
            boot._require(type(error.errno) is int and 0<error.errno<=4095)
            namespaces[name]={'result':'DIRECT_READ_REFUSED','errno':error.errno}
        else:namespaces[name]={'result':'DIRECT_READ_OBSERVED','device':info.st_dev,'inode':info.st_ino}
    return {'commit':binding.commit,'tree':binding.tree,'operator_uid':os.geteuid(),'pid1_uid':pid1.st_uid,
        'root_owned_pid1_condition':'MET' if pid1.st_uid==0 else 'NOT_MET',
        'direct_pid1_namespace_read':namespaces,'repaired_native_stat_regression':'NOT_RUN_REVIEW_REQUIRED',
        'mountinfo_sha256':hashlib.sha256(raw).hexdigest(),'filesystem_counts':dict(sorted(counts.items())),
        'fixed_mount_points':selected,'non_proc_mounts_beneath_proc':under_proc,
        'ownership_scan_mount_policy':policy,'refusal':reason,
        'active_binfmt_observation':active_mount,'ownership_scan':'NOT_RUN_REVIEW_REQUIRED',
        'account_mutations':0,'namespace_operations':0,'privileged_processes_started':0,
        'credential_release':'WITHHELD','execution_authority':False}


def _self_namespaces():
    try:return {name:ns._identity(os.stat('/proc/self/ns/'+name)) for name in ('ipc','mnt')}
    except OSError as error:raise boot.BootstrapRefusal('host_self_namespace_unreadable') from error


def _initial_namespaces(*, deadline=None):
    """PID1's links are ptrace-gated; only fixed native stat receives privilege.

    AccountLease verifies this executable and every privileged wrapper before
    calling the preflight. No caller path, shell, repository code or mutation.
    """
    from hosted_synthetic_identity import native
    command=native(('/usr/bin/stat','-L','--format=%d:%i','--',
                    '/proc/1/ns/ipc','/proc/1/ns/mnt'))
    try:code,raw=boot._collect_native_client(command,cwd='/',**({} if deadline is None else {'operation_deadline':deadline}))
    except boot.BootstrapCleanupError:raise
    except (OSError,boot.BootstrapRefusal) as error:
        raise boot.BootstrapRefusal('host_initial_namespace_unreadable') from error
    boot._require(code==0 and type(raw) is bytes and len(raw)<=96
                  and re.fullmatch(rb'[0-9]{1,20}:[1-9][0-9]{0,19}\n[0-9]{1,20}:[1-9][0-9]{0,19}\n',raw),
                  'host_initial_namespace_unreadable')
    values=[tuple(int(part) for part in row.split(b':')) for row in raw.splitlines()]
    boot._require(all(all(part<2**64 for part in value) for value in values),'host_initial_namespace_unreadable')
    return dict(zip(('ipc','mnt'),values))


@_resource_operation
def inspect_ownership(identity, *, scan_deadline=None):
    from hosted_synthetic_identity import validate,native
    validate(identity)
    boot._require(scan_deadline is None or (type(scan_deadline) in (int,float) and math.isfinite(scan_deadline)),
                  'identity_selection_deadline')
    namespaces=_self_namespaces()
    boot._require(namespaces==_initial_namespaces(**({} if scan_deadline is None else {'deadline':scan_deadline})),'host_scan_namespace_refused')
    try:mounts=ns._read('/proc/self/mountinfo',1024**2)
    except OSError as error:raise boot.BootstrapRefusal('mount_coverage_incomplete') from error
    with _scan_mount_lease(mounts) as (digest,extra_roots):
        # No -xdev: scan every reachable mount, including /dev/shm and /dev/mqueue.
        # Never follow symbolic links or print paths/content. Any find error/timeout
        # or truncated collection refuses; no failed scan can authorize allocation.
        remaining=60 if scan_deadline is None else min(60,int(scan_deadline-time.monotonic()))
        boot._require(remaining>0,'identity_selection_deadline')
        command=native(('/usr/bin/find','-P','/',*extra_roots,'-path','/proc','-prune','-o',
            '(','-uid',str(identity.uid),'-o','-gid',str(identity.gid),')','-printf','COLLISION\n','-quit'),purpose='scan',timeout_seconds=remaining)
        code,raw=boot._collect_native_client(command,cwd='/',**({} if scan_deadline is None else {'operation_deadline':scan_deadline}))
        boot._require(scan_deadline is None or time.monotonic()<scan_deadline,'identity_selection_deadline')
        if code==0 and raw==b'COLLISION\n':raise OwnershipCollision('dedicated_filesystem_ownership_collision')
        boot._require(code==0 and raw==b'','dedicated_filesystem_coverage_or_collision_refused')
        ipc={}
        for name in ('shm','sem','msg'):
            try:raw=ns._read('/proc/sysvipc/'+name,1024**2)
            except OSError as error:raise boot.BootstrapRefusal('ipc_coverage_incomplete') from error
            ipc[name]=parse_ipc(raw,identity.uid,identity.gid)
        try:repeated=ns._read('/proc/self/mountinfo',1024**2)
        except OSError as error:raise boot.BootstrapRefusal('mount_coverage_incomplete') from error
        boot._require(repeated==mounts,'mount_coverage_changed')
        boot._require(_self_namespaces()==namespaces==_initial_namespaces(**({} if scan_deadline is None else {'deadline':scan_deadline})),'host_scan_namespace_changed')
    return {'policy':POLICY,'scope':'VISIBLE_HOST_MOUNTS_AND_INITIAL_SYSV_IPC','root':'/',
        'exclusions':{'/proc':'kernel process metadata; separate full process/SysV IPC checks; active binfmt mount scanned explicitly'},
        'additional_scan_roots':list(extra_roots),
        'hidden_autofs':'Not traversed; active binfmt mount held; no hidden-object absence claim',
        'path_race_scope':'Trusted administration must not overmount/lazy-unmount during scan; not atomic path binding',
        'kernel_ownership_files':'/sys including every visible cgroup2 subtree is scanned',
        'symlinks':'LSTAT_NOT_FOLLOWED; targets scanned through their actual visible paths',
        'hidden_unmounted_filesystems':'NOT_CLAIMED; trusted administration must not expose them during lease',
        'mountinfo_sha256':digest,'sysv_objects':ipc,'posix_ipc':'/dev/shm and /dev/mqueue included',
        'namespace_reference':'VERIFIED_NATIVE_STAT_OF_PID1_BEFORE_AND_AFTER',
        'timeout_seconds':60,'collision':False,'named_acl_grants_outside_source':'NOT_ASSESSED_OWNERSHIP_SCOPE_ONLY','temporal_scope':'PRE_ACCOUNT_OBSERVATION_ONLY'}


def _access(path,identity,*,directory=False):
    info=os.lstat(path)
    boot._require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),'source_type_refused')
    # Conservatively reject ACL-dependent access rather than misread mode bits.
    try:os.getxattr(path,'system.posix_acl_access',follow_symlinks=False)
    except OSError as error:boot._require(error.errno in (errno.ENODATA,errno.EOPNOTSUPP),'source_acl_unobserved')
    else:raise boot.BootstrapRefusal('source_acl_requires_review')
    shift=6 if info.st_uid==identity.uid else 3 if info.st_gid==identity.gid else 0
    required=5 if directory else 4
    boot._require((info.st_mode>>shift)&required==required,'source_candidate_access_refused')
    return info


def inspect_source(identity,source,commit, *, operation_deadline=None, rehearsal=None):
    """Check prospective numeric credentials and rehearse exact local Git copy.

    No permissions on the checkout change. Rehearsal is the current non-root
    operator in an owned private temporary directory, never repository Python.
    """
    from hosted_synthetic_identity import validate,git_arguments
    validate(identity);boot._path(source)
    boot._require(os.geteuid()==identity.operator_uid and os.getegid()==identity.operator_gid,'source_operator_refused')
    root=Path(source);started=time.monotonic();deadline=started+30;count=0;size=0
    if operation_deadline is not None:deadline=min(deadline,operation_deadline)
    def git(where,*args):
        return _git(where,*args,**({} if operation_deadline is None else {'operation_deadline':operation_deadline}))
    def identity_of(info,name):
        value=(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)
        # Unrelated siblings of source ancestors (including our own /tmp
        # rehearsal) may change directory times. Never relax checkout metadata.
        return value if Path(name) in root.parents else value+(info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    def destination_identity():
        info=os.stat('/run',follow_symlinks=False)
        boot._require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
                      and info.st_mode&1,'source_destination_changed')
        return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)
    boot._require(time.monotonic()<deadline,'source_scan_timeout')
    reused=rehearsal is not None and bool(rehearsal)
    if reused:
        boot._require(set(rehearsal)=={'source','commit','rows','original','report','destination'}
                      and rehearsal['source']==source and rehearsal['commit']==commit,'source_rehearsal_binding_refused')
        for name,expected in rehearsal['rows']:
            boot._require(time.monotonic()<deadline,'source_scan_timeout')
            info=os.lstat(name)
            boot._require(identity_of(info,name)==expected,'source_rehearsal_drift')
            _access(Path(name),identity,directory=stat.S_ISDIR(info.st_mode))
        original=git(root,'rev-parse','HEAD','HEAD^{tree}').decode().splitlines()
        boot._require(original==rehearsal['original'],'source_commit_changed')
        boot._require(destination_identity()==rehearsal['destination'],'source_destination_changed')
        capacity=os.statvfs('/run');report=dict(rehearsal['report'])
        boot._require(not capacity.f_flag&os.ST_RDONLY and capacity.f_favail>=2*report['entries']+128
            and capacity.f_bavail*capacity.f_frsize>=2*report['regular_bytes']+16*1024**2,'source_destination_preflight_refused')
        boot._require(time.monotonic()<deadline,'source_scan_timeout')
        return dict(report,rehearsal='REUSED_WITH_FULL_METADATA_ACCESS_AND_SOURCE_DRIFT_CHECKS',
                    candidate_access_seconds=time.monotonic()-started)

    for parent in (root,*root.parents):_access(parent,identity,directory=True)
    listed=git(root,'ls-files','-z').split(b'\0');boot._require(listed[-1]==b'','source_index_refused')
    paths=set()
    for name in listed[:-1]:
        boot._require(time.monotonic()<deadline and len(paths)<=MAX_FILES,'source_index_bound')
        path=Path(os.fsdecode(name));boot._require(not path.is_absolute() and '..' not in path.parts,'source_index_refused')
        paths.add(root/path)
        paths.update((root/path).parents)
    gitdir=root/'.git';boot._require(gitdir.is_dir() and not gitdir.is_symlink(),'source_git_directory_refused')
    for relative in ('objects/info/alternates','objects/info/http-alternates','shallow','commondir'):
        boot._require(not os.path.lexists(gitdir/relative),'external_git_object_layout_refused')
    pending=[gitdir]
    while pending:
        current=pending.pop();paths.add(current)
        boot._require(time.monotonic()<deadline and len(paths)<=MAX_FILES,'source_scan_file_bound')
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    boot._require(time.monotonic()<deadline and len(paths)<MAX_FILES,'source_scan_file_bound')
                    path=Path(entry.path)
                    boot._require(not (current==gitdir/'objects/pack' and entry.name.endswith('.promisor')),
                                  'partial_git_object_layout_refused')
                    paths.add(path)
                    if entry.is_dir(follow_symlinks=False):pending.append(path)
        except OSError as error:raise boot.BootstrapRefusal('source_traversal_incomplete') from error
    rows=[]
    for path in sorted(paths):
        boot._require(time.monotonic()<deadline,'source_scan_timeout')
        info=_access(path,identity,directory=stat.S_ISDIR(os.lstat(path).st_mode))
        rows.append((str(path),identity_of(info,path)))
        count+=1;size+=info.st_size if stat.S_ISREG(info.st_mode) else 0
        boot._require(count<=MAX_FILES and size<=MAX_BYTES,'source_copy_bound')
    destination=os.stat('/run',follow_symlinks=False);capacity=os.statvfs('/run')
    boot._require(stat.S_ISDIR(destination.st_mode) and destination.st_uid==0
        and not destination.st_mode & 0o022 and destination.st_mode & 1
        and not capacity.f_flag & os.ST_RDONLY and capacity.f_favail>=2*count+128
        and capacity.f_bavail*capacity.f_frsize>=2*size+16*1024**2,
        'source_destination_preflight_refused')
    original=git(root,'rev-parse','HEAD','HEAD^{tree}').decode().splitlines()
    boot._require(len(original)==2 and original[0]==commit,'source_commit_changed')
    with tempfile.TemporaryDirectory(prefix='buffalo-source-preflight-') as temporary:
        stage=Path(temporary)/'repo';space=os.statvfs(temporary)
        boot._require(space.f_bavail*space.f_frsize>=2*size+16*1024**2,'source_copy_space_refused')
        git_argv=git_arguments(source)
        for args in ((*git_argv,'clone','--no-local','--no-hardlinks','--no-checkout','--single-branch','--no-tags','--',source,str(stage)),
                     (*git_argv,'-C',str(stage),'checkout','--detach',commit)):
            remaining=60 if operation_deadline is None else min(60,int(operation_deadline-time.monotonic()))
            boot._require(remaining>0,'source_copy_rehearsal_deadline')
            command=('/usr/bin/timeout','--signal=TERM','--kill-after=2s',str(remaining)+'s',*args)
            code,_=boot._collect_native_client(command,cwd=source,
                **({} if operation_deadline is None else {'operation_deadline':operation_deadline}))
            boot._require(operation_deadline is None or time.monotonic()<operation_deadline,'source_copy_rehearsal_deadline')
            boot._require(code==0,'source_copy_rehearsal_refused')
        boot._require(git(stage,'rev-parse','HEAD','HEAD^{tree}').decode().splitlines()==original,'source_copy_identity_refused')
    repeated=os.stat('/run',follow_symlinks=False)
    boot._require((destination.st_dev,destination.st_ino,destination.st_mode)==(repeated.st_dev,repeated.st_ino,repeated.st_mode),'source_destination_changed')
    boot._require(git(root,'rev-parse','HEAD','HEAD^{tree}').decode().splitlines()==original,'source_commit_changed')
    report={'policy':POLICY,'candidate_mode_access':'VERIFIED_WITHOUT_ACL','copy':'LOCAL_GIT_CLONE_AND_DETACHED_CHECKOUT_REHEARSED',
        'commit':commit,'tree':original[1],'entries':count,'regular_bytes':size,
        'account_mutations':0,'account_command_timeout_seconds':15,'copy_command_timeout_seconds':60,
        'future_permission_or_disk_drift':'FAIL_CLOSED_AT_ACTUAL_COPY; quarantine on ambiguous post-account failure'}

    boot._require(operation_deadline is None or time.monotonic()<operation_deadline,'source_copy_rehearsal_deadline')
    if rehearsal is not None:
        rehearsal.update(source=source,commit=commit,rows=rows,original=original,report=dict(report),destination=destination_identity())
    return dict(report,rehearsal='EXECUTED_ONCE',source_rehearsal_seconds=time.monotonic()-started)
