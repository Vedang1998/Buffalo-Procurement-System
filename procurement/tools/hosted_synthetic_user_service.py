"""Prepare only an exact delegated user service, never a host/slice ancestor.

systemd owns the service lifecycle. The source-bound supervisor may enable
controllers in its empty, delegated unit root, while it and its launcher remain
in the separate supervisor leaf. This is preparation, not containment authority.
"""
import os
from pathlib import Path
import re
import stat

import hosted_synthetic_cgroup as cg
import hosted_synthetic_namespace as ns
from hosted_synthetic_source import _resource_operation
import run_local_staging_acceptance as backend


def _require(condition, code):
    if not condition:
        raise cg.CgroupProofError(code)


def _controllers(raw):
    if raw == b'':
        return set()
    _require(type(raw) is bytes and re.fullmatch(rb'[a-z]+(?: [a-z]+)*\n', raw) is not None,
             'delegation_controls_unavailable')
    values = raw[:-1].split(b' ')
    _require(len(values) == len(set(values)), 'delegation_controls_unavailable')
    return set(values)


@_resource_operation
def prepare_user_service(unit: str, *, manager: str = 'user') -> dict:
    _require(type(unit) is str and re.fullmatch(
        r'buffalo-hosted-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.service', unit),
        'delegation_unit_mismatch')
    current = backend.parse_browser_cgroup_path(ns._read('/proc/self/cgroup', 4096))
    uid = os.geteuid()
    _require(manager in ('user', 'system'), 'delegation_scope_mismatch')
    expected_path = (f'/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/{unit}/supervisor'
                     if manager == 'user' else f'/system.slice/{unit}/supervisor')
    _require(uid == os.getuid() and uid > 0 and current == expected_path,
        'delegation_scope_mismatch')
    parent_path = Path('/sys/fs/cgroup' + current).parent
    supervisor_path = parent_path / 'supervisor'
    parent = supervisor = control = None
    writes = 0
    try:
        parent = cg._open_directory(parent_path)
        supervisor = cg._open_directory(supervisor_path)
        mount = ns._mount_id(parent)
        expected = tuple(sorted((os.getpid(), os.getppid())))

        def verify():
            parent.verify()
            supervisor.verify()
            _require(ns._mount_id(parent) == ns._mount_id(supervisor) == mount,
                     'delegation_scope_mismatch')
            for path, held in ((parent_path, parent), (supervisor_path, supervisor)):
                named = cg._open_directory(path)
                try:
                    _require(named.identity == held.identity and ns._mount_id(named) == mount,
                             'delegation_scope_mismatch')
                finally:
                    named.close()
            _require(backend.parse_browser_cgroup_path(ns._read('/proc/self/cgroup', 4096)) == current
                     and backend.parse_browser_cgroup_path(ns._read(f'/proc/{os.getppid()}/cgroup', 4096)) == current,
                     'delegation_scope_mismatch')
            _require(cg._read_at(parent, 'cgroup.type') == b'domain\n'
                     and cg._read_at(parent, 'cgroup.procs') == cg._read_at(parent, 'cgroup.threads') == b'',
                     'delegation_scope_mismatch')
            _require(backend.parse_browser_cgroup_processes(cg._read_at(supervisor, 'cgroup.procs')) == expected
                     and backend.parse_browser_cgroup_threads(cg._read_at(supervisor, 'cgroup.threads')) == expected,
                     'delegation_supervisor_members_refused')
            _require(cg.parse_scalar(cg._read_at(parent, 'memory.max')) == 512 * 1024**2
                     and cg.parse_scalar(cg._read_at(parent, 'pids.max')) == 64,
                     'delegation_scope_mismatch')
            _require({b'memory', b'pids'}.issubset(_controllers(cg._read_at(parent, 'cgroup.controllers'))),
                     'delegation_controls_unavailable')

        verify()
        before = _controllers(cg._read_at(parent, 'cgroup.subtree_control'))
        required = {b'memory', b'pids'}
        _require(before in (set(), required), 'delegation_controls_unavailable')
        if before != required:
            control = ns._acquire(lambda: os.open('cgroup.subtree_control', os.O_WRONLY | os.O_CLOEXEC |
                                                   os.O_NOFOLLOW, dir_fd=parent.number))
            info = os.fstat(control.number)
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022
                     and ns._mount_id(control) == mount
                     and ns._identity(os.stat('cgroup.subtree_control', dir_fd=parent.number, follow_symlinks=False))
                     == control.identity, 'delegation_scope_mismatch')
            verify()
            control.verify()
            # Standard delegatee control operation on this owned service only;
            # no permission change and no controller write to any ancestor.
            value = b'+memory +pids\n'
            writes += 1
            _require(os.write(control.number, value) == len(value), 'delegation_readback_refused')
        verify()
        after = _controllers(cg._read_at(parent, 'cgroup.subtree_control'))
        _require(after == required, 'delegation_readback_refused')
        return dict(contract='BUFFALO_USER_SERVICE_DELEGATION_V1', unit=unit,
                    root_path=str(parent_path)[len('/sys/fs/cgroup'):], identity=list(parent.identity),
                    mount_id=mount, enabled_before=sorted(x.decode() for x in before),
                    enabled_after=['memory', 'pids'], controller_write_attempts=writes,
                    execution_authority=False, provider_calls=0,
                    lifecycle_owner='SYSTEMD_USER_MANAGER' if manager == 'user' else 'SYSTEMD_SYSTEM_MANAGER')
    finally:
        errors = []
        for descriptor in (control, supervisor, parent):
            if descriptor is not None:
                try:
                    descriptor.close()
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise cg.CgroupCleanupError('delegation_descriptor_cleanup_failed') from errors[0]
