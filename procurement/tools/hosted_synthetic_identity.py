"""Dedicated ephemeral identity lifecycle; dormant until independent review.

Only maintained native binaries may receive administrative authority. Python
always remains the ordinary operator. Ambiguous partial mutation retains the
identity and refuses cleanup; there is no force deletion, retry or root helper.
Worker exclusion and full runtime closure are separate, still OPEN gates.
"""
from dataclasses import dataclass
import grp
import hashlib
import errno
import os
from pathlib import Path
import pwd
import re
import stat
import sys
import uuid

import hosted_synthetic_bootstrap as boot


@dataclass(frozen=True)
class DedicatedIdentity:
    nonce: str
    uid: int
    gid: int
    operator_uid: int
    operator_gid: int

    @property
    def user(self):return 'bf-u-' + self.nonce.replace('-','')[:24]
    @property
    def group(self):return 'bf-g-' + self.nonce.replace('-','')[:24]
    @property
    def root(self):return '/run/buffalo-' + self.nonce
    @property
    def unit(self):return 'buffalo-hosted-' + self.nonce + '.service'


def validate(identity):
    boot._require(type(identity) is DedicatedIdentity
        and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',identity.nonce)
        and all(type(x) is int for x in (identity.uid,identity.gid,identity.operator_uid,identity.operator_gid))
        and 900 <= identity.uid <= 999 and 900 <= identity.gid <= 999
        and identity.operator_uid > 0 and identity.operator_gid > 0
        and identity.uid != identity.operator_uid and identity.gid != identity.operator_gid)


def native(argv, *, purpose="account"):
    boot._require(purpose in ("account","scan","copy"))
    return ('/usr/bin/sudo','-n','--','/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',
            '/usr/bin/timeout','--signal=TERM','--kill-after=2s','15s' if purpose=='account' else '60s',*argv)


def dropped(identity, argv, *, purpose="account"):
    validate(identity)
    return native(('/usr/bin/setpriv','--clear-groups','--no-new-privs','--',
                   '/usr/bin/setpriv',f'--reuid={identity.uid}',f'--regid={identity.gid}','--keep-groups',
                   '--inh-caps=-all','--ambient-caps=-all','--bounding-set=-all','--no-new-privs','--',
                   '/usr/bin/env','-i','PATH=/usr/bin:/bin','LC_ALL=C.UTF-8',*argv),purpose=purpose)


def git_arguments(source):
    boot._path(source)
    return ('/usr/bin/env','GIT_CONFIG_NOSYSTEM=1','GIT_CONFIG_GLOBAL=/dev/null','GIT_TERMINAL_PROMPT=0',
         '/usr/bin/git','-c','maintenance.auto=false','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',
         '-c','protocol.allow=never','-c','protocol.file.allow=always','-c','safe.directory='+source)


def provisioning_commands(identity, source, commit):
    """Pure reviewable wiring: root native account tools, then dropped Git."""
    validate(identity);boot._path(source)
    boot._require(re.fullmatch('[0-9a-f]{40}',commit) is not None)
    git=git_arguments(source)
    # Separate names stop userdel from auto-removing a matching private group.
    return (
        native(('/usr/sbin/groupadd','--system','--gid',str(identity.gid),'--',identity.group)),
        native(('/usr/sbin/useradd','--system','--uid',str(identity.uid),'--gid',str(identity.gid),
                '--no-user-group','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin',
                '--no-log-init','--key','SUB_UID_COUNT=0','--key','SUB_GID_COUNT=0','--',identity.user)),
        native(('/usr/bin/passwd','--status','--',identity.user)),
        native(('/usr/bin/sudo','-n','-l','-U',identity.user)),
        native(('/usr/bin/mkdir','--mode=0700','--',identity.root)),
        native(('/usr/bin/chown','--no-dereference','--from=0:0',f'{identity.uid}:{identity.gid}','--',identity.root)),
        dropped(identity,(*git,'clone','--no-local','--no-hardlinks','--no-checkout','--single-branch','--no-tags',
                          '--',source,identity.root+'/repo'),purpose='copy'),
        dropped(identity,(*git,'-C',identity.root+'/repo','checkout','--detach',commit),purpose='copy'),
    )


def _lookup(function,value):
    try:return function(value)
    except KeyError:return None


def _local_rows(path,count):
    raw=Path(path).read_text();boot._require(len(raw)<=1024**2)
    rows=[]
    for line in raw.splitlines():
        if not line or line.startswith('#'):continue
        row=line.split(':')
        boot._require(len(row)==count and row[2].isdecimal() and (count!=7 or row[3].isdecimal()),
                      'local_account_schema_refused')
        rows.append(row)
    return rows


def inspect_account(identity, *, present):
    validate(identity)
    local_users=_local_rows('/etc/passwd',7);local_groups=_local_rows('/etc/group',4)
    matching_users=[row for row in local_users if row[0]==identity.user or int(row[2])==identity.uid]
    matching_groups=[row for row in local_groups if row[0]==identity.group or int(row[2])==identity.gid]
    primary_group_users=[row for row in local_users if int(row[3])==identity.gid]
    boot._require(len(matching_users)==len(matching_groups)==(1 if present else 0)
                  and primary_group_users==matching_users,'dedicated_local_identity_collision')
    if present:
        row=matching_users[0];group_row=matching_groups[0]
        boot._require(row[0]==identity.user and int(row[2])==identity.uid and int(row[3])==identity.gid
                      and row[5:] == ['/nonexistent','/usr/sbin/nologin']
                      and group_row[0]==identity.group and int(group_row[2])==identity.gid and group_row[3]=='')
    users=(_lookup(pwd.getpwnam,identity.user),_lookup(pwd.getpwuid,identity.uid))
    groups=(_lookup(grp.getgrnam,identity.group),_lookup(grp.getgrgid,identity.gid))
    if not present:
        boot._require(all(item is None for item in (*users,*groups)), 'dedicated_identity_collision')
        for path,number in (('/etc/subuid',identity.uid),('/etc/subgid',identity.gid)):
            if not os.path.lexists(path):continue
            raw=Path(path).read_text();boot._require(len(raw)<=1024**2)
            for line in raw.splitlines():
                if not line or line.startswith('#'):continue
                fields=line.split(':')
                boot._require(len(fields)==3 and fields[1].isdecimal() and fields[2].isdecimal())
                start,count=int(fields[1]),int(fields[2])
                boot._require(not start <= number < start+count,'dedicated_subordinate_identity_collision')
        return
    boot._require(users[0] is not None and users[0]==users[1] and groups[0] is not None and groups[0]==groups[1])
    account,group=users[0],groups[0]
    boot._require(account.pw_name==identity.user and account.pw_uid==identity.uid
                  and account.pw_gid==identity.gid and account.pw_dir=='/nonexistent'
                  and account.pw_shell=='/usr/sbin/nologin' and group.gr_name==identity.group
                  and group.gr_gid==identity.gid and group.gr_mem==[])
    boot._require(os.getgrouplist(identity.user,identity.gid)==[identity.gid], 'dedicated_supplementary_membership_refused')
    # Empty explicit group membership does not substitute for the final
    # process's getgroups()/proc-status readback after the native clear.


def identity_processes(identity):
    """Read-only whole-host UID/GID scan; unreadable entries refuse absence."""
    validate(identity);found=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():continue
        try:raw=(path/'status').read_bytes()
        except FileNotFoundError:continue
        boot._require(len(raw)<=16384)
        fields={}
        for line in raw.splitlines():
            key,_,value=line.partition(b':')
            if key in (b'Uid',b'Gid',b'Groups'):
                boot._require(key not in fields and all(x.isdigit() for x in value.split()))
                fields[key]=[int(x) for x in value.split()]
        boot._require(set(fields)=={b'Uid',b'Gid',b'Groups'}
                      and len(fields[b'Uid'])==len(fields[b'Gid'])==4)
        if identity.uid in fields[b'Uid'] or identity.gid in fields[b'Gid']+fields[b'Groups']:
            found.append(int(path.name))
    return sorted(found)


def inspect_account_platform():
    """Narrow supported configuration only; never edit system policy/hooks."""
    # This includes sudo/env/timeout/setpriv before any account mutation, and
    # binds the already-enabled ancestor policy without modifying it.
    wrappers=boot.inspect_native_prerequisites()
    for kind in ('passwd','group'):
        raw=Path('/etc/nsswitch.conf').read_text()
        values=re.findall(r'(?m)^'+kind+r':\s*([^#\n]+)',raw)
        boot._require(len(values)==1 and values[0].split() in (['files'],['files','systemd']),
                      'unsupported_identity_lookup_configuration')
    config=Path('/etc/login.defs').read_text()
    boot._require(not re.search(r'(?m)^\s*USERDEL_CMD\s+\S',config),'account_deletion_hook_refused')
    for path in (Path('/etc'),Path('/etc/nsswitch.conf'),Path('/etc/login.defs'),Path('/etc/shadow-maint'),
                 Path('/etc/passwd'),Path('/etc/group')):
        if not os.path.lexists(path):continue
        info=path.lstat()
        boot._require(info.st_uid==0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode))
    for name in ('useradd-pre.d','useradd-post.d','userdel-pre.d','userdel-post.d'):
        path=Path('/etc/shadow-maint')/name
        boot._require(not os.path.lexists(path) or (path.is_dir() and not path.is_symlink() and not any(path.iterdir())),
                      'account_hook_refused')
        if os.path.lexists(path):
            info=path.lstat();boot._require(info.st_uid==0 and not info.st_mode & 0o022)
    # Shadow may notify these cache daemons. Their absence is the narrow
    # currently reviewed configuration; do not start or reconfigure a daemon.
    for path in ('/usr/sbin/nscd','/usr/sbin/sss_cache'):
        boot._require(not os.path.lexists(path),'account_cache_helper_requires_review')
    programs={}
    for path in ('/usr/sbin/groupadd','/usr/sbin/useradd','/usr/sbin/userdel','/usr/sbin/groupdel','/usr/bin/passwd',
                 '/usr/sbin/nologin','/usr/bin/mkdir','/usr/bin/chown','/usr/bin/git','/usr/bin/find','/usr/bin/rmdir'):
        selected=Path(path)
        for parent in (selected,*selected.parents):
            info=parent.lstat()
            boot._require(info.st_uid==0 and not info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode))
        info=selected.lstat()
        boot._require(stat.S_ISREG(info.st_mode) and info.st_mode & 0o111
                      and not info.st_mode & (0o2000 if path=='/usr/bin/passwd' else 0o6000)
                      and 0<info.st_size<=32*1024**2)
        descriptor=os.open(selected,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            data=os.pread(descriptor,info.st_size+1,0)
            try:os.getxattr(descriptor,'security.capability')
            except OSError as error:boot._require(error.errno==errno.ENODATA)
            else:raise boot.BootstrapRefusal('account_native_file_capability_refused')
            key=lambda value:(value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid,
                              value.st_size,value.st_mtime_ns,value.st_ctime_ns)
            boot._require(len(data)==info.st_size and key(info)==key(os.fstat(descriptor))==key(selected.lstat()))
        finally:os.close(descriptor)
        programs[path]={'sha256':hashlib.sha256(data).hexdigest(),'device':info.st_dev,'inode':info.st_ino}
    run=Path('/run').lstat()
    boot._require(stat.S_ISDIR(run.st_mode) and run.st_uid==0 and not run.st_mode & 0o022)
    return {'wrappers_and_ancestors':wrappers,'account_programs':programs}


class AccountLease:
    def __init__(self):raise boot.BootstrapRefusal('use_explicit_account_provisioning')

    @classmethod
    def create(cls, identity, source, commit, *, lifecycle=None):
        validate(identity)
        if lifecycle is not None:
            from hosted_synthetic_lifecycle import require_case
            require_case(lifecycle,identity,commit)
        boot._require(os.getresuid()==(identity.operator_uid,)*3 and os.getresgid()==(identity.operator_gid,)*3)
        platform=inspect_account_platform();inspect_account(identity,present=False)
        boot._require(not identity_processes(identity) and not os.path.lexists(identity.root)
                      and not os.path.lexists('/sys/fs/cgroup/system.slice/'+identity.unit)
                      and not os.path.lexists('/sys/fs/cgroup'+boot.launcher.service_cgroup_path(identity.unit)))
        from hosted_synthetic_account_preflight import inspect_source,inspect_ownership
        source_preflight=inspect_source(identity,source,commit)
        ownership_preflight=inspect_ownership(identity)
        inspect_account(identity,present=False)
        boot._require(not identity_processes(identity),"identity_appeared_before_reservation")
        from hosted_synthetic_quarantine import Quarantine
        lease=object.__new__(cls);lease.identity=identity;lease._owner=lease
        lease.created=False;lease.root_identity=None;lease.closed=False;lease.uncertain=False
        lease.platform=platform
        lease.preflight={"source":source_preflight,"ownership":ownership_preflight}
        lease.service_attempted=False;lease.service_acknowledged=False
        lease.service_creation_acknowledged=False
        lease.source_commit=commit
        lease.drain_proof=None
        lease.journal=Quarantine.create(identity,commit)
        try:
            if lifecycle is not None:lifecycle.account_created(lease)
            for index,command in enumerate(provisioning_commands(identity,source,commit)):
                lease.journal.record(f'provision_attempt_{index}')
                code,output=boot._collect_native_client(command,cwd=source)
                boot._require(code in (0,1) if index==3 else code==0,'dedicated_setup_refused')
                if index==1:
                    inspect_account(identity,present=True);lease.created=True
                if index==2:
                    boot._require(len(output)<=256 and re.fullmatch(re.escape(identity.user.encode())+
                        rb' L [0-9/\-]{8,10} -?[0-9]+ -?[0-9]+ -?[0-9]+ -?[0-9]+\n',output),
                        'dedicated_password_lock_unverified')
                if index==3:
                    boot._require(re.fullmatch(b'User '+re.escape(identity.user.encode())+
                        rb' is not allowed to run sudo on [A-Za-z0-9.\-]{1,253}\.\n',output) is not None,
                        'dedicated_sudo_policy_unverified')
                if index==4:
                    info=os.lstat(identity.root)
                    boot._require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and stat.S_IMODE(info.st_mode)==0o700)
                    lease.root_identity=(info.st_dev,info.st_ino)
                if index>=5:lease.verify_root()
                lease.journal.record(f'provision_ack_{index}',
                    {'staging_identity':list(lease.root_identity)} if index>=4 else None)
                if lifecycle is not None:lifecycle.provisioned(index)
            return lease
        except BaseException as error:
            # A timed-out account tool may have committed a database mutation.
            # No inferred rollback, force deletion, retry or UID recycling.
            lease.uncertain=True
            lease.journal.close()
            raise boot.BootstrapCleanupError('dedicated_partial_setup_unverified_identity_retained') from error

    def verify_root(self):
        boot._require(self._owner is self and not self.closed and not self.uncertain and self.created)
        self.journal.verify()
        inspect_account(self.identity,present=True)
        info=os.lstat(self.identity.root)
        boot._require((info.st_dev,info.st_ino)==self.root_identity and stat.S_ISDIR(info.st_mode)
                      and info.st_uid==self.identity.uid and info.st_gid==self.identity.gid
                      and stat.S_IMODE(info.st_mode)==0o700)

    def close_after_service(self):
        boot._require(self._owner is self and not self.closed and not self.uncertain)
        if self.service_attempted and not self.service_acknowledged:
            self.uncertain=True
            self.journal.close()
            raise boot.BootstrapCleanupError('native_acknowledgment_uncertain_identity_retained')
        identity=self.identity
        try:
            if self.service_attempted:
                from hosted_synthetic_observer import require_drain_proof
                require_drain_proof(self.drain_proof,self)
            self.verify_root()
            boot._require(inspect_account_platform()==self.platform,'account_native_platform_drift')
            boot._require(boot._observe_unit_absence(identity.unit)=='NOT_FOUND_OBSERVED'
                          and not os.path.lexists('/sys/fs/cgroup'+boot.launcher.service_cgroup_path(identity.unit))
                          and not identity_processes(identity),'dedicated_resources_not_drained')
            # Deletion is unprivileged, non-following and confined to this
            # exact owned filesystem. Root removes only the empty bound root.
            commands=(dropped(identity,('/usr/bin/find','-P',identity.root,'-xdev','-mindepth','1','-depth','-delete')),
                      native(('/usr/bin/rmdir','--',identity.root)),
                      native(('/usr/sbin/userdel','--',identity.user)),
                      native(('/usr/sbin/groupdel','--',identity.group)))
            for index,command in enumerate(commands):
                if index<=1:self.verify_root()
                if index<=2:inspect_account(identity,present=True)
                if index==3:
                    group=_lookup(grp.getgrnam,identity.group)
                    boot._require(group is not None and group.gr_gid==identity.gid and group.gr_mem==[]
                                  and _lookup(grp.getgrgid,identity.gid)==group
                                  and _lookup(pwd.getpwnam,identity.user) is None
                                  and _lookup(pwd.getpwuid,identity.uid) is None)
                boot._require(not identity_processes(identity),'dedicated_process_absence_uncertain')
                self.journal.record(f'remove_attempt_{index}')
                code,_=boot._collect_native_client(command,cwd='/')
                boot._require(code==0,'dedicated_cleanup_command_refused')
                if index==1:boot._require(not os.path.lexists(identity.root))
                self.journal.record(f'remove_ack_{index}')
            inspect_account(identity,present=False)
            self.journal.resolve()
            self.closed=True
        except BaseException as error:
            self.uncertain=True
            self.journal.close()
            raise boot.BootstrapCleanupError('dedicated_cleanup_unverified_identity_retained') from error


def run_provisioned_candidate(identity, *, source, python, repository, event, ref, commit, lifecycle=None, diagnostic="retained"):
    """Complete dormant wiring; CLI/workflow review gate never calls this."""
    from hosted_synthetic_source import bind_hosted_source, _resource_operation
    from hosted_synthetic_capability import _require_clean_supervisor
    @_resource_operation
    def execute():
        boot._require(diagnostic in ("retained","postgres-version"))
        _require_clean_supervisor()
        binding=bind_hosted_source(Path(source),repository=repository,event=event,ref=ref,sha=commit)
        if lifecycle is not None:
            from hosted_synthetic_lifecycle import require_case
            require_case(lifecycle,identity,commit)
            boot._require(lifecycle.binding==binding)
        options={} if lifecycle is None else {'lifecycle':lifecycle}
        lease=AccountLease.create(identity,source,commit,**options)
        body_error=None
        try:
            initial=os.stat('/proc/self/ns/user')
            plan=boot.plan_native_bootstrap(root=identity.root+'/repo',python=python,repository=repository,
                event=event,ref=ref,commit=commit,uid=identity.uid,gid=identity.gid,unit=identity.unit,
                initial_user_namespace=(initial.st_dev,initial.st_ino),
                **({"diagnostic":diagnostic} if diagnostic!="retained" else {}))
            result=boot.run_native_plan(plan,source_root=source,account=lease,**options)
            boot._require(result['report']['tree']==binding.tree)
        except BaseException as error:
            body_error=error
        try:
            lease.close_after_service()
        except BaseException:
            if body_error is not None:
                raise boot.BootstrapCleanupError('candidate_cleanup_unverified_identity_retained') from body_error
            raise
        if body_error is not None:raise body_error
        result.update(dedicated_identity_removed=lease.closed,worker_control_exclusion='UNPROVEN',
                      full_service_lifetime_cleanup='OBSERVED' if lease.drain_proof is not None else 'UNPROVEN',
                      account_native_programs=lease.platform)
        return result
    return execute()


def activation_entry(arguments, *, diagnostic="retained"):
    """Review-target activation wiring, unreachable behind bootstrap.main.

    There is intentionally no command-line/environment switch to invoke it.
    Actual external review and its recorded disposition precede connecting the
    existing workflow to this function in a separately verified gate change.
    """
    from hosted_synthetic_capability import _require_clean_supervisor
    from hosted_synthetic_source import bind_hosted_source
    _require_clean_supervisor()
    boot._require(len(arguments)==4 and diagnostic in ("retained","postgres-version"))
    repository,event,ref,commit=arguments
    source=str(Path(__file__).resolve().parents[2])
    bind_hosted_source(Path(source),repository=repository,event=event,ref=ref,sha=commit)
    inspect_account_platform()
    nonce=str(uuid.uuid4())
    from hosted_synthetic_quarantine import reserved_ids
    reserved=reserved_ids()
    for number in range(900,1000):
        if any(number in pair for pair in reserved):continue
        candidate=DedicatedIdentity(nonce,number,number,os.geteuid(),os.getegid())
        try:
            inspect_account(candidate,present=False)
        except boot.BootstrapRefusal as error:
            if str(error) in ('dedicated_identity_collision','dedicated_local_identity_collision',
                              'dedicated_subordinate_identity_collision'):
                continue
            raise
        if identity_processes(candidate):continue
        return run_provisioned_candidate(candidate,source=source,python=sys.executable,
            repository=repository,event=event,ref=ref,commit=commit,
            **({"diagnostic":diagnostic} if diagnostic!="retained" else {}))
    raise boot.BootstrapRefusal('dedicated_identity_range_unavailable')


def postgres_version_activation_entry(arguments):
    """Exact candidate for review; never called by the inert CLI/workflow."""
    return activation_entry(arguments,diagnostic="postgres-version")
