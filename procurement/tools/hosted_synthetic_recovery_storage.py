"""Dormant owned storage materialization; no database/app lifecycle authority.

The caller's private parent must be inaccessible to workers and unrelated actors.
This component tests retained-FD/name checks, not the unresolved same-UID boundary.
It launches nothing. External process/handle absence remains the caller's gate.
Catchable SIGINT/exception cleanup is separate from fatal-signal/manager-kill
recovery, which requires the enclosing external lifecycle observer.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import signal
import stat

import hosted_synthetic_namespace as ns
import hosted_synthetic_recovery_inputs as inputs


@dataclass
class Entry:
    parent: object
    name: str
    descriptor: object
    directory: bool
    data: bytes | None = None


def _require(value):
    if not value: raise inputs.RecoveryInputRefusal('populated_recovery_materialization_refused')


def _member_inventory(members):
    _require(type(members) is tuple and 0 < len(members) <= 128
             and all(type(m) is inputs.StorageMember and type(m.data) is bytes for m in members))
    records=[{'path':m.name,'bytes':len(m.data),'sha256':hashlib.sha256(m.data).hexdigest()} for m in members]
    inputs._storage_records(records,binding=False)
    return records


def _open_directory(parent,name,owners):
    descriptor=ns._acquire(lambda:os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW,
                                        dir_fd=parent.number if parent else None))
    inputs._register_descriptor(descriptor,owners)
    return descriptor


def _verify_name(entry):
    entry.parent.verify();entry.descriptor.verify()
    named=os.stat(entry.name,dir_fd=entry.parent.number,follow_symlinks=False)
    opened=os.fstat(entry.descriptor.number)
    _require(ns._identity(named)==entry.descriptor.identity and named.st_mode==opened.st_mode)


def _mutation_boundary(chain,parent,entries):
    for entry in chain:_verify_name(entry)
    parent.verify();info=os.fstat(parent.number)
    _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
             and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()))
    for entry in entries:
        _verify_name(entry);info=os.fstat(entry.descriptor.number)
        _require((info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()))
        if entry.directory:
            _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
                     and set(os.listdir(entry.descriptor.number))=={e.name for e in entries if e.parent is entry.descriptor})
        else:_require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600)


class MaterializedStorage:
    def __init__(self):raise inputs.RecoveryInputRefusal('use_owned_storage_context')

    def verify(self):
        _require(self.state=='MATERIALIZED')
        for entry in self.chain:_verify_name(entry)
        self.parent.verify()
        info=os.fstat(self.parent.number)
        _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
                 and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()))
        for entry in self.entries:
            _verify_name(entry);info=os.fstat(entry.descriptor.number)
            _require((info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()))
            if entry.directory:
                _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700)
                children={e.name for e in self.entries if e.parent is entry.descriptor}
                _require(set(os.listdir(entry.descriptor.number))==children)
            else:
                _require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600
                         and info.st_nlink==1 and info.st_size==len(entry.data)
                         and os.pread(entry.descriptor.number,len(entry.data)+1,0)==entry.data)
        return {'scope':'OWNED_STORAGE_MATERIALIZATION_COMPONENT_ONLY','files':len(self.inventory),
                'inventory':self.inventory,'execution_authority':False,
                'worker_exclusion':'CALLER_LIVE_PROOF_REQUIRED','external_handles':'NOT_PROVEN'}


@contextmanager
def materialize_storage(parent_path,nonce,members):
    """Create one fresh child, retain every created FD, remove only exact entries.

    Any unknown/replaced/partial entry prevents ALL path cleanup. Descriptors are
    still closed when identity-safe; original failure remains the exception cause.
    No recursive deletion, chown, existing-path reuse or automatic retry.
    """
    _require(type(parent_path) is str and parent_path.startswith('/') and parent_path!='/'
             and str(Path(parent_path))==parent_path and '\0' not in parent_path
             and all(p not in ('','.','..') for p in parent_path.split('/')[1:]))
    _require(type(nonce) is str and re.fullmatch('[0-9a-f]{32}',nonce)
             and os.geteuid()>0 and os.getegid()>0)
    inventory=_member_inventory(members);inputs._signal_boundary()
    previous=signal.pthread_sigmask(signal.SIG_BLOCK,inputs._MASK)
    owners=[];chain=[];entries=[];value=None;failure=None;cleanup=[];uncertain=False
    try:
        current=_open_directory(None,'/',owners)
        for name in Path(parent_path).parts[1:]:
            child=_open_directory(current,name,owners);chain.append(Entry(current,name,child,True));current=child
        info=os.fstat(current.number)
        _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
                 and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()))
        parent=current;leaf='buffalo-recovered-storage-'+nonce
        def create_directory(parent,name):
            nonlocal uncertain
            _mutation_boundary(chain,current,entries)
            # On an ambiguous mkdir/open result, preserve every named resource.
            try:os.mkdir(name,0o700,dir_fd=parent.number)
            except FileExistsError:raise
            except BaseException:uncertain=True;raise
            uncertain=True
            descriptor=_open_directory(parent,name,owners)
            entry=Entry(parent,name,descriptor,True);entries.append(entry)
            _verify_name(entry)
            info=os.fstat(descriptor.number)
            _require(stat.S_IMODE(info.st_mode)==0o700 and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()))
            _mutation_boundary(chain,current,entries)
            uncertain=False
            return descriptor
        root=create_directory(parent,leaf);directories={():root}
        for member in members:
            parts=tuple(member.name.split('/'))
            for i in range(1,len(parts)):
                prefix=parts[:i]
                if prefix not in directories:directories[prefix]=create_directory(directories[prefix[:-1]],prefix[-1])
            directory=directories[parts[:-1]];name=parts[-1]
            # Every file parent was created here. Failure before descriptor
            # identity capture is ambiguous, so no path rollback is attempted.
            _mutation_boundary(chain,parent,entries);uncertain=True
            descriptor=ns._acquire(lambda:os.open(name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,
                                                0o600,dir_fd=directory.number))
            inputs._register_descriptor(descriptor,owners)
            entry=Entry(directory,name,descriptor,False,member.data);entries.append(entry)
            _verify_name(entry);_mutation_boundary(chain,parent,entries);uncertain=False
            inputs._write_all(descriptor.number,member.data,before_write=lambda:_mutation_boundary(chain,parent,entries))
            _mutation_boundary(chain,parent,entries);os.fsync(descriptor.number)
        for descriptor in reversed(tuple(directories.values())):
            _mutation_boundary(chain,parent,entries);os.fsync(descriptor.number)
        _mutation_boundary(chain,parent,entries)
        os.fsync(parent.number)
        value=object.__new__(MaterializedStorage);value.state='MATERIALIZED'
        value.chain=chain;value.entries=entries;value.parent=parent;value.root=root
        value.inventory=inventory;value.path=str(Path(parent_path)/leaf)
        value.verify()
        signal.pthread_sigmask(signal.SIG_SETMASK,previous)
        yield value
    except BaseException as error:
        failure=error
    finally:
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK,inputs._MASK)
            inputs._signal_boundary()
            _require(not uncertain)
            # Validate the entire owned inventory before the first unlink.
            if entries:
                remaining=list(entries)
                _mutation_boundary(chain,entries[0].parent,remaining)
                for entry in reversed(entries):
                    _mutation_boundary(chain,entries[0].parent,remaining)
                    if entry.directory:os.rmdir(entry.name,dir_fd=entry.parent.number)
                    else:os.unlink(entry.name,dir_fd=entry.parent.number)
                    remaining.pop()
                    _mutation_boundary(chain,entries[0].parent,remaining)
                    os.fsync(entry.parent.number)
                _require(not any(name==entries[0].name for name in os.listdir(entries[0].parent.number)))
        except BaseException as error:cleanup.append(error)
        for descriptor in reversed(owners):
            try:descriptor.close()
            except BaseException as error:cleanup.append(error)
        if value is not None:value.state='CLEANUP_REFUSED' if cleanup else 'REMOVED'
        try:signal.pthread_sigmask(signal.SIG_SETMASK,previous)
        except BaseException as error:
            if failure is None:failure=error
        if cleanup:raise inputs.RecoveryInputCleanupError('populated_recovery_storage_cleanup_refused') from failure
        if failure is not None:raise failure
