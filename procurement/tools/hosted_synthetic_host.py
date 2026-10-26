"""Bounded, credential-free host prerequisites; never containment authority.

Only the fixed native PostgreSQL16 system paths are considered. Metadata and
version observation is not the application's Backup V2 validator or a sealed
runtime closure. No installer, system manager, permission or cgroup write runs.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time

import hosted_synthetic_cgroup as cg
import hosted_synthetic_namespace as ns
import run_local_staging_acceptance as backend
from hosted_synthetic_source import _resource_operation


def _version(path: Path) -> bytes:
    process = None
    try:
        process = subprocess.Popen([str(path), '--version'], env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, bufsize=0)
        deadline = time.monotonic() + 3
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                cg._require(remaining > 0 and selector.select(remaining))
                block = os.read(process.stdout.fileno(), 257 - len(output))
                if not block:
                    break
                output.extend(block)
                cg._require(len(output) <= 256)
        remaining = deadline - time.monotonic()
        cg._require(remaining > 0 and process.wait(timeout=remaining) == 0)
        return bytes(output)
    finally:
        if process is not None:
            errors = []
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
            except BaseException as error:
                errors.append(error)
            try:
                process.stdout.close()
            except BaseException as error:
                errors.append(error)
            if errors:
                raise cg.CgroupCleanupError('host_version_process_cleanup_failed') from errors[0]


def _program(name: str) -> dict:
    if name not in ('pg_dump', 'pg_restore'):
        raise ValueError('fixed_program_only')
    path = Path('/usr/lib/postgresql/16/bin') / name
    result = dict(path=str(path), status='UNAVAILABLE', version=None, sha256=None)
    descriptors = []
    try:
        # Hold every ancestor; reject symlinks and mutable ownership. This is
        # deliberately narrower than the unchanged historic managed-Nix rule.
        parent = ns._acquire(lambda: os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
        descriptors.append(parent)
        for index, component in enumerate(path.parts[1:]):
            info = os.fstat(parent.number)
            cg._require(info.st_uid == 0 and stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022)
            is_leaf = index == len(path.parts) - 2
            child = ns._acquire(lambda: os.open(component, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW |
                                                (0 if is_leaf else os.O_DIRECTORY), dir_fd=parent.number))
            descriptors.append(child)
            parent = child
        before = os.fstat(parent.number)
        cg._require(before.st_uid == 0 and stat.S_ISREG(before.st_mode)
                    and before.st_mode & 0o111 and not before.st_mode & 0o022
                    and 0 < before.st_size <= 32 * 1024**2)
        content = os.pread(parent.number, before.st_size + 1, 0)
        cg._require(len(content) == before.st_size)
        # Trusted immutable system ancestry is checked before this version-only
        # subprocess. No database arguments or ambient libpq values are passed.
        version = _version(path)
        cg._require(re.fullmatch(
            name.encode() + rb' \(PostgreSQL\) 16\.[0-9]+(?: [\x20-\x7e]{1,160})?\n', version) is not None)
        after = os.fstat(parent.number)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                                 info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        # Reading/exec may legitimately advance atime on an unchanged program.
        cg._require(identity(before) == identity(after)
                    and identity(os.stat(path, follow_symlinks=False)) == identity(after)
                    and ns._identity(after) == parent.identity)
        result.update(status='SYSTEM_METADATA_AND_VERSION_MATCH',
                      version=version.decode('ascii').strip(), sha256=hashlib.sha256(content).hexdigest())
    except (cg.CgroupCleanupError, ns.NamespaceCleanupError):
        raise
    except (OSError, ValueError, cg.CgroupProofError, ns.NamespaceProofError, subprocess.TimeoutExpired):
        result['status'] = 'UNAVAILABLE_OR_UNTRUSTED'
    finally:
        failed = False
        for descriptor in reversed(descriptors):
            try:
                descriptor.close()
            except BaseException:
                failed = True
        if failed:
            raise cg.CgroupCleanupError('host_program_descriptor_cleanup_failed')
    return result


def _delegation() -> dict:
    result = dict(status='UNAVAILABLE', current_path=None, candidates=[], observed=[],
                  user_manager_socket=False)
    current = backend.parse_browser_cgroup_path(ns._read('/proc/self/cgroup', 4096))
    result['current_path'] = current
    result['user_manager_socket'] = stat.S_ISSOCK(
        os.stat(f'/run/user/{os.geteuid()}/systemd/private', follow_symlinks=False).st_mode
    ) if os.path.lexists(f'/run/user/{os.geteuid()}/systemd/private') else False
    # Only the current ancestry is relevant to same-launcher delegation. No
    # broad host traversal or probing of another tenant's cgroups occurs.
    parts = current.strip('/').split('/') if current != '/' else []
    cg._require(len(parts) <= 64)
    for length in range(len(parts), -1, -1):
        relative = '/' + '/'.join(parts[:length])
        path = Path('/sys/fs/cgroup' + relative)
        record = dict(path=relative, writable=False, delegated_empty_domain=False)
        descriptor = None
        try:
            # _open_directory requires an owned non-world-writable actual
            # cgroup2 directory and no symlink ancestor. Root is never leased.
            descriptor = cg._open_directory(path)
            enabled = cg._read_at(descriptor, 'cgroup.subtree_control')
            processes = cg._read_at(descriptor, 'cgroup.procs')
            threads = cg._read_at(descriptor, 'cgroup.threads')
            record['writable'] = os.access(path, os.W_OK | os.X_OK)
            record['delegated_empty_domain'] = bool(
                record['writable'] and cg._read_at(descriptor, 'cgroup.type') == b'domain\n'
                and enabled.endswith(b'\n') and {b'memory', b'pids'}.issubset(enabled.split())
                and processes == threads == b'' and os.access(path / 'cgroup.procs', os.W_OK))
            if record['delegated_empty_domain']:
                result['candidates'].append(relative)
        except (cg.CgroupCleanupError, ns.NamespaceCleanupError):
            raise
        except (OSError, cg.CgroupProofError, ns.NamespaceProofError):
            pass
        finally:
            if descriptor is not None:
                descriptor.close()
        result['observed'].append(record)
    result['status'] = 'CANDIDATE_REQUIRES_LIVE_LEASE' if result['candidates'] else 'NO_USABLE_CURRENT_ANCESTRY_DELEGATION'
    return result


def observe_supervisor_identity() -> dict:
    """Boolean-only launch requirements; never change groups or credentials.

    Unprivileged gid mapping requires setgroups=deny. Inherited supplementary
    groups cannot then be cleared inside that namespace. Refuse before mutation
    when the platform has not provided the required empty-group launcher.
    """
    uids, gids = os.getresuid(), os.getresgid()
    checks = dict(nonroot_uid_consistent=len(set(uids)) == 1 and uids[0] > 0,
                  gid_consistent=len(set(gids)) == 1,
                  supplementary_groups_empty=os.getgroups() == [])
    from hosted_synthetic_launcher import inspect_host_identity, LauncherRefusal
    try:
        inspect_host_identity()
        checks['full_postdrop_identity'] = True
    except (LauncherRefusal, OSError):
        checks['full_postdrop_identity'] = False
    return dict(contract='BUFFALO_SUPERVISOR_IDENTITY_V1', **checks,
                eligible_for_user_mapping=all(checks.values()), execution_authority=False)


@_resource_operation
def observe_host_prerequisites() -> dict:
    from hosted_synthetic_launcher import inspect_host_identity
    inspect_host_identity()
    programs = {name: _program(name) for name in ('pg_dump', 'pg_restore')}
    try:
        delegation = _delegation()
    except (cg.CgroupCleanupError, ns.NamespaceCleanupError):
        raise
    except (OSError, cg.CgroupProofError, ns.NamespaceProofError, backend.LocalStagingAcceptanceError):
        delegation = dict(status='OBSERVATION_REFUSED', candidates=[])
    return dict(contract='BUFFALO_HOST_PREREQUISITES_V1', uid=os.geteuid(),
                programs=programs, delegation=delegation,
                prerequisites_available=all(p['status'] == 'SYSTEM_METADATA_AND_VERSION_MATCH'
                                            for p in programs.values()) and bool(delegation['candidates']),
                application_backup_validator='NOT_RUN', runtime_closure='UNSEALED',
                cgroup_write_attempts=0, provider_calls=0, execution_authority=False)
