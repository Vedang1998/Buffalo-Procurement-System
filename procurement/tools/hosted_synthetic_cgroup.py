"""Owned cgroup-v2 component for Task 2; no execution or release authority.

The platform must supply an already delegated, empty domain with memory/pids
enabled. This module never changes its permissions or enables a controller on
an ancestor. It creates one child, holds kernel identities, enrolls only an
already stopped/childless init, and removes only its empty original child.
The namespace launcher still must integrate and independently attest I/W.
The eventual composition must exclude concurrent untrusted writers of the
delegation. Linux has no identity-conditional mkdir/open or rmdir syscall;
observed replacement checks do not prove immunity to hostile same-UID races
between the final name check and a kernel name operation.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import signal
import stat

import hosted_synthetic_namespace as ns
import run_local_staging_acceptance as backend


class CgroupProofError(RuntimeError):
    pass


class CgroupCleanupError(CgroupProofError):
    pass


_CGROUP2_MAGIC = 0x63677270
_MAXIMUM = 4096
_CONTROLS = ('cgroup.type', 'cgroup.procs', 'cgroup.threads', 'cgroup.events',
             'cgroup.subtree_control', 'memory.max', 'memory.oom.group',
             'memory.events', 'pids.max')
_WRITABLE = frozenset(('cgroup.procs', 'memory.max', 'memory.oom.group', 'pids.max'))
_MEMORY_KEYS = frozenset(('low', 'high', 'max', 'oom', 'oom_kill', 'oom_group_kill'))


def _require(value):
    if not value:
        raise CgroupProofError('cgroup_proof_refused')


def parse_scalar(raw: bytes) -> int:
    _require(type(raw) is bytes and re.fullmatch(rb'(?:0|[1-9][0-9]{0,19})\n', raw)
             is not None)
    value = int(raw)
    _require(value < 2**64)
    return value


def parse_memory_events(raw: bytes) -> dict[str, int]:
    _require(type(raw) is bytes and 0 < len(raw) <= _MAXIMUM and raw.endswith(b'\n'))
    values = {}
    for line in raw[:-1].split(b'\n'):
        key, separator, number = line.partition(b' ')
        _require(separator and key in {item.encode() for item in _MEMORY_KEYS}
                 and key.decode() not in values)
        values[key.decode()] = parse_scalar(number + b'\n')
    _require(set(values) == _MEMORY_KEYS)
    return values


def require_zero_oom_delta(before: dict, after: dict) -> None:
    for selected in (before, after):
        _require(type(selected) is dict and set(selected) == _MEMORY_KEYS
                 and all(type(value) is int and 0 <= value < 2**64
                         for value in selected.values()))
    _require(all(after[key] >= before[key] for key in _MEMORY_KEYS)
             and all(after[key] == before[key] for key in ('oom', 'oom_kill', 'oom_group_kill')))


def _filesystem(fd):
    # Linux statfs begins with native long f_type. Oversize aligned storage
    # avoids assuming the remainder of the platform ABI structure.
    buffer = (ctypes.c_long * 64)()
    libc = ctypes.CDLL(None, use_errno=True)
    _require(libc.fstatfs(ctypes.c_int(fd), ctypes.byref(buffer)) == 0
             and buffer[0] == _CGROUP2_MAGIC)


def _signal_boundary():
    _require(len(os.listdir('/proc/self/task')) == 1
             and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
             and {signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT}.issubset(
                 signal.pthread_sigmask(signal.SIG_BLOCK, [])))


def _open_directory(path: Path):
    _require(type(path) is Path or isinstance(path, Path))
    _require(path.is_absolute() and str(path).startswith('/sys/fs/cgroup/')
             and '..' not in path.parts)
    descriptor = ns._acquire(lambda: os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
    try:
        for component in path.parts[1:]:
            descriptor.verify()
            child = ns._acquire(lambda: os.open(component, os.O_RDONLY | os.O_DIRECTORY |
                                               os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=descriptor.number))
            try:
                descriptor.close()
            except BaseException:
                child.close()
                raise
            descriptor = child
        _filesystem(descriptor.number)
        info = os.fstat(descriptor.number)
        _require(info.st_uid == os.geteuid() and not info.st_mode & 0o022)
        return descriptor
    except BaseException:
        descriptor.close()
        raise


def _read_at(directory, name):
    directory.verify()
    fd = ns._acquire(lambda: os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                                   dir_fd=directory.number))
    try:
        _require(stat.S_ISREG(os.fstat(fd.number).st_mode)
                 and ns._mount_id(fd) == ns._mount_id(directory))
        value = os.pread(fd.number, _MAXIMUM + 1, 0)
        _require(len(value) <= _MAXIMUM)
        return value
    finally:
        fd.close()


class CgroupLease:
    """No public constructor: creation requires actual delegated kernel files."""

    def __init__(self):
        raise CgroupProofError('use_owned_cgroup_creation')

    @classmethod
    def create(cls, delegation: Path, *, generation: str, memory_max: int):
        # Caller owns the outer cancellation envelope through final cleanup.
        # Never return a newly acquired lease across an unmasked signal edge.
        _signal_boundary()
        _require(type(generation) is str and re.fullmatch('[0-9a-f]{64}', generation))
        _require(type(memory_max) is int and 4096 <= memory_max < 5 * 1024**3
                 and memory_max % os.sysconf('SC_PAGE_SIZE') == 0)
        lease = object.__new__(cls)
        lease.parent = _open_directory(delegation)
        lease.delegation = delegation
        lease.child = None
        lease.controls = {}
        lease.name = 'buffalo-' + generation
        lease.path = str(delegation)[len('/sys/fs/cgroup'):] + '/' + lease.name
        lease.memory_max = memory_max
        lease.baseline = None
        lease.closed = False
        lease.cleanup_failed = False
        lease._owner = lease
        created = False
        try:
            lease.parent_mount = ns._mount_id(lease.parent)
            _require(_read_at(lease.parent, 'cgroup.type') == b'domain\n'
                     and _read_at(lease.parent, 'cgroup.procs') == b''
                     and _read_at(lease.parent, 'cgroup.threads') == b'')
            controllers = _read_at(lease.parent, 'cgroup.subtree_control')
            _require(controllers.endswith(b'\n') and set(controllers.split()) >= {b'memory', b'pids'})
            os.mkdir(lease.name, mode=0o700, dir_fd=lease.parent.number)
            created = True
            lease.child = ns._acquire(lambda: os.open(lease.name, os.O_RDONLY | os.O_DIRECTORY |
                                                      os.O_CLOEXEC | os.O_NOFOLLOW,
                                                      dir_fd=lease.parent.number))
            lease.child_mount = ns._mount_id(lease.child)
            _filesystem(lease.child.number)
            for name in _CONTROLS:
                lease.controls[name] = ns._acquire(lambda: os.open(
                    name, (os.O_RDWR if name in _WRITABLE else os.O_RDONLY) |
                    os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=lease.child.number))
                _require(stat.S_ISREG(os.fstat(lease.controls[name].number).st_mode))
            lease.verify()
            lease._empty()
            lease._write('memory.max', str(memory_max).encode() + b'\n')
            lease._write('memory.oom.group', b'1\n')
            lease._write('pids.max', b'2\n')
            lease.baseline = parse_memory_events(lease._read('memory.events'))
            _require(not any(lease.baseline.values()))
            lease.attest(())
            return lease
        except BaseException as error:
            try:
                if lease.child is not None:
                    lease.close()
                else:
                    lease.parent.close()
                    # A successful mkdir without a held child identity cannot
                    # justify deleting an entry by name, even on open failure.
                    if created:
                        raise CgroupCleanupError('cgroup_acquisition_ambiguous')
            except BaseException as cleanup:
                raise CgroupCleanupError('cgroup_creation_cleanup_failed') from cleanup
            raise error

    def verify(self):
        _signal_boundary()
        _require(self._owner is self and not self.closed and self.child is not None)
        self.parent.verify()
        self.child.verify()
        _filesystem(self.child.number)
        current_parent = _open_directory(self.delegation)
        named = None
        try:
            _require(current_parent.identity == self.parent.identity
                     and ns._mount_id(current_parent) == self.parent_mount)
            named = ns._acquire(lambda: os.open(self.name, os.O_RDONLY | os.O_DIRECTORY |
                                                os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=self.parent.number))
            _require(named.identity == self.child.identity
                     and ns._mount_id(named) == self.child_mount)
        finally:
            errors = []
            for descriptor in (named, current_parent):
                if descriptor is not None:
                    try:
                        descriptor.close()
                    except BaseException as error:
                        errors.append(error)
            if errors:
                raise CgroupCleanupError('cgroup_verification_cleanup_failed') from errors[0]
        info = os.fstat(self.child.number)
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                 and not info.st_mode & 0o022)
        for name, descriptor in self.controls.items():
            descriptor.verify()
            _require(ns._identity(os.stat(name, dir_fd=self.child.number, follow_symlinks=False))
                     == descriptor.identity and ns._mount_id(descriptor) == self.child_mount)

    def _read(self, name):
        self.verify()
        value = os.pread(self.controls[name].number, _MAXIMUM + 1, 0)
        _require(len(value) <= _MAXIMUM)
        return value

    def _write(self, name, value):
        self.verify()
        _require(name in _WRITABLE and os.pwrite(self.controls[name].number, value, 0) == len(value))
        self.verify()

    def _empty(self):
        _require(backend.parse_browser_cgroup_processes(self._read('cgroup.procs')) == ()
                 and backend.parse_browser_cgroup_threads(self._read('cgroup.threads')) == ()
                 and backend.parse_browser_cgroup_events(self._read('cgroup.events'))
                 == backend.BrowserCgroupEvents(populated=False, frozen=False))

    def attest(self, members: tuple[int, ...]) -> dict:
        _require(type(members) is tuple and len(members) <= 2 and len(set(members)) == len(members)
                 and all(type(pid) is int and 1 < pid <= 2**31 - 1 for pid in members))
        _require(self._read('cgroup.type') == b'domain\n'
                 and self._read('cgroup.subtree_control') == b''
                 and parse_scalar(self._read('memory.max')) == self.memory_max
                 and parse_scalar(self._read('pids.max')) == 2
                 and parse_scalar(self._read('memory.oom.group')) == 1)
        _require(backend.parse_browser_cgroup_processes(self._read('cgroup.procs')) == tuple(sorted(members))
                 and backend.parse_browser_cgroup_threads(self._read('cgroup.threads')) == tuple(sorted(members))
                 and backend.parse_browser_cgroup_events(self._read('cgroup.events'))
                 == backend.BrowserCgroupEvents(populated=bool(members), frozen=False))
        events = parse_memory_events(self._read('memory.events'))
        require_zero_oom_delta(self.baseline, events)
        return dict(path=self.path, identity=list(self.child.identity), memory_max=self.memory_max,
                    pids_max=2, memory_oom_group=1, members=sorted(members), memory_events=events,
                    execution_authority=False)

    def enroll_stopped_init(self, *, pid: int, pidfd, start: int, guardian: int,
                            guardian_pidfd, guardian_start: int, supervisor: int):
        """Caller retains unreaped I/G ownership; this never resumes a process."""
        _signal_boundary()
        _require(all(type(value) is int and value > 1 for value in (pid, guardian, supervisor))
                 and len({pid, guardian, supervisor}) == 3 and supervisor == os.getpid()
                 and type(start) is int and start > 0
                 and type(guardian_start) is int and guardian_start > 0)
        self.attest(())

        def stopped():
            for selected, number in ((pidfd, pid), (guardian_pidfd, guardian)):
                selected.verify()
                _require(os.readlink(f'/proc/self/fd/{selected.number}') == 'anon_inode:[pidfd]'
                         and re.findall(rb'(?m)^Pid:\s*([0-9]+)$', ns._read(f'/proc/self/fdinfo/{selected.number}', 4096))
                         == [str(number).encode()] and not backend._browser_pidfd_is_terminal(selected.number))
            outer = ns._process(guardian)
            _require(outer['parent'] == supervisor and outer['group'] == outer['session'] == guardian
                     and outer['start'] == guardian_start and outer['state'] in ('S', 'T'))
            observed = ns._process(pid)
            _require(observed['state'] == 'T' and observed['start'] == start
                     and observed['parent'] == guardian and observed['group'] == guardian)
            _require(sorted(os.listdir(f'/proc/{pid}/task')) == [str(pid)]
                     and ns._read(f'/proc/{pid}/task/{pid}/children', 4096) == b'')
            for outside in (guardian, supervisor):
                path = backend.parse_browser_cgroup_path(ns._read(f'/proc/{outside}/cgroup', 4096))
                _require(path != self.path and not path.startswith(self.path + '/'))

        stopped()
        self._write('cgroup.procs', str(pid).encode() + b'\n')
        stopped()
        _require(backend.parse_browser_cgroup_path(ns._read(f'/proc/{pid}/cgroup', 4096)) == self.path)
        return self.attest((pid,))

    def close(self):
        _signal_boundary()
        _require(self._owner is self)
        if self.closed:
            if self.cleanup_failed:
                raise CgroupCleanupError('cgroup_cleanup_failed')
            return
        errors = []
        try:
            self.parent.verify()
            self.child.verify()
            _filesystem(self.child.number)
            # Recover a renamed original by held identity, never by label.
            names = os.listdir(self.parent.number)
            _require(len(names) <= 4096)
            owned = []
            for name in names:
                info = os.stat(name, dir_fd=self.parent.number, follow_symlinks=False)
                if ns._identity(info) == self.child.identity and stat.S_ISDIR(info.st_mode):
                    owned.append(name)
            _require(len(owned) == 1)
            if owned != [self.name]:
                errors.append(CgroupCleanupError('cgroup_name_changed'))
            # Only empty cgroups may be removed. Never kill or move a member
            # here: the namespace owner must terminate/reap its exact pidfds.
            for name in ('cgroup.procs', 'cgroup.threads'):
                _require(_read_at(self.child, name) == b'')
            _require(backend.parse_browser_cgroup_events(_read_at(self.child, 'cgroup.events'))
                     == backend.BrowserCgroupEvents(populated=False, frozen=False))
            if self.baseline is not None:
                try:
                    require_zero_oom_delta(self.baseline, parse_memory_events(_read_at(self.child, 'memory.events')))
                except BaseException as error:
                    # Bad resource evidence still permits removing the exact
                    # empty owned child, but can never certify cleanup PASS.
                    errors.append(error)
            _require(ns._identity(os.stat(owned[0], dir_fd=self.parent.number, follow_symlinks=False))
                     == self.child.identity)
            os.rmdir(owned[0], dir_fd=self.parent.number)
            _require(all(ns._identity(os.stat(name, dir_fd=self.parent.number, follow_symlinks=False))
                         != self.child.identity for name in os.listdir(self.parent.number)))
        except BaseException as error:
            errors.append(error)
        for descriptor in (*reversed(tuple(self.controls.values())), self.child, self.parent):
            try:
                descriptor.close()
            except BaseException as error:
                errors.append(error)
        self.closed = True
        self.cleanup_failed = bool(errors)
        if errors:
            raise CgroupCleanupError('cgroup_cleanup_failed') from errors[0]
