"""Bounded pre-account checks; no account reservation or permission repair.

Only verified native find receives setup privilege. Python and Git remain
unprivileged. Absence is limited to the visible host mounts and initial IPC
namespace at observation time; trusted administration must not race allocation.
"""
import errno
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time

import hosted_synthetic_bootstrap as boot
import hosted_synthetic_namespace as ns
from hosted_synthetic_source import _git

POLICY='BUFFALO_ACCOUNT_PREFLIGHT_V1'
MAX_FILES=50000
MAX_BYTES=256*1024**2


def parse_ipc(raw,uid,gid):
    boot._require(type(raw) is bytes and raw.endswith(b'\n') and len(raw)<=1024**2,'ipc_coverage_incomplete')
    rows=raw.splitlines();boot._require(bool(rows),'ipc_schema_refused')
    columns=rows[0].split();required=(b'uid',b'gid',b'cuid',b'cgid')
    boot._require(len(columns)==len(set(columns)) and all(k in columns for k in required),'ipc_schema_refused')
    for row in rows[1:]:
        values=row.split();boot._require(len(values)==len(columns),'ipc_schema_refused')
        selected=[values[columns.index(k)] for k in required]
        boot._require(all(x.isdigit() and int(x)<=2**32-1 for x in selected),'ipc_schema_refused')
        boot._require(not (uid in (int(selected[0]),int(selected[2])) or gid in (int(selected[1]),int(selected[3]))),
                      'dedicated_ipc_ownership_collision')
    return len(rows)-1


def _mount_scope(raw):
    boot._require(type(raw) is bytes and raw.endswith(b'\n') and len(raw)<=1024**2,'mount_coverage_incomplete')
    seen=set()
    for row in raw.splitlines():
        left,sep,right=row.partition(b' - ');fields=left.split();tail=right.split()
        boot._require(sep and len(fields)>=6 and len(tail)==3 and fields[0] not in seen,'mount_schema_refused')
        seen.add(fields[0]);target=fields[4]
        boot._require(tail[0] in (b'ext4',b'xfs',b'btrfs',b'tmpfs',b'devtmpfs',b'proc',b'sysfs',b'cgroup2',b'devpts',b'mqueue',b'securityfs',b'debugfs',b'tracefs',b'pstore',b'efivarfs',b'configfs',b'bpf',b'hugetlbfs',b'fusectl',b'binfmt_misc',b'squashfs'), 'filesystem_scope_requires_review')
        if target==b'/proc' or target.startswith(b'/proc/'):
            # Hidden non-proc objects beneath the excluded prefix are not silently skipped.
            boot._require(tail[0]==b'proc','excluded_mount_requires_review')
    return hashlib.sha256(raw).hexdigest()


def inspect_ownership(identity):
    from hosted_synthetic_identity import validate,native
    validate(identity)
    namespaces={name:ns._identity(os.stat('/proc/self/ns/'+name)) for name in ('ipc','mnt')}
    boot._require(all(namespaces[name]==ns._identity(os.stat('/proc/1/ns/'+name)) for name in namespaces),'host_scan_namespace_refused')
    mounts=ns._read('/proc/self/mountinfo',1024**2);digest=_mount_scope(mounts)
    # No -xdev: scan every reachable mount, including /dev/shm and /dev/mqueue.
    # Never follow symbolic links or print paths/content. Any find error/timeout
    # or truncated collection refuses; no failed scan can authorize allocation.
    command=native(('/usr/bin/find','-P','/','-path','/proc','-prune','-o',
        '(','-uid',str(identity.uid),'-o','-gid',str(identity.gid),')','-printf','COLLISION\n','-quit'),purpose='scan')
    code,raw=boot._collect_native_client(command,cwd='/')
    boot._require(code==0 and raw==b'','dedicated_filesystem_coverage_or_collision_refused')
    ipc={}
    for name in ('shm','sem','msg'):
        ipc[name]=parse_ipc(ns._read('/proc/sysvipc/'+name,1024**2),identity.uid,identity.gid)
    boot._require(ns._read('/proc/self/mountinfo',1024**2)==mounts,'mount_coverage_changed')
    boot._require(all(ns._identity(os.stat('/proc/'+pid+'/ns/'+name))==value
        for name,value in namespaces.items() for pid in ('self','1')),'host_scan_namespace_changed')
    return {'policy':POLICY,'scope':'VISIBLE_HOST_MOUNTS_AND_INITIAL_SYSV_IPC','root':'/',
        'exclusions':{'/proc':'kernel process metadata; separate full process and SysV IPC checks'},
        'kernel_ownership_files':'/sys including every visible cgroup2 subtree is scanned',
        'symlinks':'LSTAT_NOT_FOLLOWED; targets scanned through their actual visible paths',
        'hidden_unmounted_filesystems':'NOT_CLAIMED; trusted administration must not expose them during lease',
        'mountinfo_sha256':digest,'sysv_objects':ipc,'posix_ipc':'/dev/shm and /dev/mqueue included',
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


def inspect_source(identity,source,commit):
    """Check prospective numeric credentials and rehearse exact local Git copy.

    No permissions on the checkout change. Rehearsal is the current non-root
    operator in an owned private temporary directory, never repository Python.
    """
    from hosted_synthetic_identity import validate,git_arguments
    validate(identity);boot._path(source)
    boot._require(os.geteuid()==identity.operator_uid and os.getegid()==identity.operator_gid,'source_operator_refused')
    root=Path(source);deadline=time.monotonic()+30;count=0;size=0
    for parent in (root,*root.parents):_access(parent,identity,directory=True)
    listed=_git(root,'ls-files','-z').split(b'\0');boot._require(listed[-1]==b'','source_index_refused')
    paths=set()
    for name in listed[:-1]:
        boot._require(time.monotonic()<deadline and len(paths)<=MAX_FILES,'source_index_bound')
        path=Path(os.fsdecode(name));boot._require(not path.is_absolute() and '..' not in path.parts,'source_index_refused')
        paths.add(root/path)
        paths.update((root/path).parents)
    gitdir=root/'.git';boot._require(gitdir.is_dir() and not gitdir.is_symlink(),'source_git_directory_refused')
    for relative in ('objects/info/alternates','objects/info/http-alternates','shallow','commondir'):
        boot._require(not os.path.lexists(gitdir/relative),'external_git_object_layout_refused')
    boot._require(not list((gitdir/'objects/pack').glob('*.promisor')),'partial_git_object_layout_refused')
    def walk_error(error):raise boot.BootstrapRefusal('source_traversal_incomplete') from error
    for current,dirs,files in os.walk(gitdir,followlinks=False,onerror=walk_error):
        boot._require(time.monotonic()<deadline,'source_scan_timeout')
        paths.add(Path(current));paths.update(Path(current)/name for name in dirs+files)
        boot._require(len(paths)<=MAX_FILES,'source_scan_file_bound')
    for path in sorted(paths):
        boot._require(time.monotonic()<deadline,'source_scan_timeout')
        info=_access(path,identity,directory=stat.S_ISDIR(os.lstat(path).st_mode))
        count+=1;size+=info.st_size if stat.S_ISREG(info.st_mode) else 0
        boot._require(count<=MAX_FILES and size<=MAX_BYTES,'source_copy_bound')
    destination=os.stat('/run',follow_symlinks=False);capacity=os.statvfs('/run')
    boot._require(stat.S_ISDIR(destination.st_mode) and destination.st_uid==0
        and not destination.st_mode & 0o022 and destination.st_mode & 1
        and not capacity.f_flag & os.ST_RDONLY and capacity.f_favail>=2*count+128
        and capacity.f_bavail*capacity.f_frsize>=2*size+16*1024**2,
        'source_destination_preflight_refused')
    original=_git(root,'rev-parse','HEAD','HEAD^{tree}').decode().splitlines()
    boot._require(len(original)==2 and original[0]==commit,'source_commit_changed')
    with tempfile.TemporaryDirectory(prefix='buffalo-source-preflight-') as temporary:
        stage=Path(temporary)/'repo';space=os.statvfs(temporary)
        boot._require(space.f_bavail*space.f_frsize>=2*size+16*1024**2,'source_copy_space_refused')
        git=git_arguments(source)
        for args in ((*git,'clone','--no-local','--no-hardlinks','--no-checkout','--single-branch','--no-tags','--',source,str(stage)),
                     (*git,'-C',str(stage),'checkout','--detach',commit)):
            command=('/usr/bin/timeout','--signal=TERM','--kill-after=2s','60s',*args)
            code,_=boot._collect_native_client(command,cwd=source)
            boot._require(code==0,'source_copy_rehearsal_refused')
        boot._require(_git(stage,'rev-parse','HEAD','HEAD^{tree}').decode().splitlines()==original,'source_copy_identity_refused')
    repeated=os.stat('/run',follow_symlinks=False)
    boot._require((destination.st_dev,destination.st_ino,destination.st_mode)==(repeated.st_dev,repeated.st_ino,repeated.st_mode),'source_destination_changed')
    boot._require(_git(root,'rev-parse','HEAD','HEAD^{tree}').decode().splitlines()==original,'source_commit_changed')
    return {'policy':POLICY,'candidate_mode_access':'VERIFIED_WITHOUT_ACL','copy':'LOCAL_GIT_CLONE_AND_DETACHED_CHECKOUT_REHEARSED',
        'commit':commit,'tree':original[1],'entries':count,'regular_bytes':size,
        'account_mutations':0,'account_command_timeout_seconds':15,'copy_command_timeout_seconds':60,
        'future_permission_or_disk_drift':'FAIL_CLOSED_AT_ACTUAL_COPY; quarantine on ambiguous post-account failure'}
