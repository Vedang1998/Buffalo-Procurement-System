"""Bounded, unexecuted tiny projection check; no release or exec authority.

Actual sentinel execution requires stopped cgroup enrollment and remains NOT_RUN.
This component does not use the frozen full-browser bundle's entry/count pins.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import platform
import re
import stat
import struct
import sys

import hosted_synthetic_namespace as ownership

backend = ownership.backend
_SIZE = 65536
_INODES = 8
_SENTINEL_SHA256 = '4e4042014c2ed77edea2140f0b1a85a153510fcc53b05d480d79aa5419780a91'
# x86-64 syscall-only code: PDEATHSIG/KILL; exact PID2/PPID1; dumpable0;
# NNP1; GET_DUMPABLE/GET_NNP; SIGSTOP. Every refusal or unexpected continuation
# exits127. There is no fork/open/network/output/provider or credential channel.
_CODE = bytes.fromhex(
    '31d24531d24531c0b89d000000bf01000000be090000000f054885c07579'
    'b8270000000f054883f802756cb86e0000000f054883f801755f'
    'b89d000000bf0400000031f60f054885c0754c'
    'b89d000000bf26000000be010000000f054885c07536'
    'b89d000000bf0300000031f60f054885c07523'
    'b89d000000bf270000000f054883f8017511'
    'b83e000000bf02000000be130000000f05b8e7000000bf7f0000000f050f0b'
)


def sentinel_bytes():
    length = 176 + len(_CODE)
    header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0'*9,
                         2, 62, 1, 0x4000b0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    load = struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, length, length, 4096)
    stack = struct.pack('<IIQQQQQQ', 0x6474e551, 6, 0, 0, 0, 0, 0, 16)
    result = header + load + stack + _CODE
    ownership._require(len(result) == 341 and hashlib.sha256(result).hexdigest() == _SENTINEL_SHA256)
    return result


def _platform():
    ownership._require(sys.platform == 'linux' and platform.machine() == 'x86_64'
                       and struct.calcsize('P') == 8 and sys.byteorder == 'little')


def parse_mount(raw, mount_id, target):
    ownership._require(type(raw) is bytes and 0 < len(raw) <= ownership._LIMIT
                       and raw.endswith(b'\n') and type(mount_id) is int and mount_id > 0
                       and type(target) is str and re.fullmatch(r'/tmp/buffalo-hosted-projection-[0-9a-f]{64}', target))
    selected = []
    identities = set()
    for line in raw.splitlines():
        before, separator, after = line.partition(b' - ')
        left, right = before.split(), after.split()
        ownership._require(separator and len(left) == 6 and len(right) == 3)
        number = ownership._integer(left[0])
        ownership._require(number not in identities)
        identities.add(number)
        if number == mount_id:
            options = left[5].split(b',')
            ownership._require(len(options) == len(set(options)) and left[3] == b'/'
                               and left[4] == target.encode() and right[0] == b'tmpfs'
                               and {b'ro', b'nosuid', b'nodev'}.issubset(options)
                               and not {b'rw', b'noexec'}.intersection(options))
            selected.append({'mount_id': number, 'flags': ['ro', 'nosuid', 'nodev'], 'executable': True})
    ownership._require(len(selected) == 1)
    return selected[0]


def _open_tmpfs(owners):
    filesystem = ownership._acquire(lambda: backend._browser_syscall(
        430, ctypes.c_char_p(b'tmpfs'), ctypes.c_uint(1)))
    owners.append(filesystem)
    for key, value in ((b'size', b'65536'), (b'nr_inodes', b'8'),
                       (b'mode', b'0700'), (b'uid', b'0'), (b'gid', b'0')):
        backend._browser_syscall(431, ctypes.c_int(filesystem.number), ctypes.c_uint(1),
                                 ctypes.c_char_p(key), ctypes.c_char_p(value), ctypes.c_int(0))
    backend._browser_syscall(431, ctypes.c_int(filesystem.number), ctypes.c_uint(6),
                             ctypes.c_void_p(0), ctypes.c_void_p(0), ctypes.c_int(0))
    mount = ownership._acquire(lambda: backend._browser_syscall(
        432, ctypes.c_int(filesystem.number), ctypes.c_uint(1), ctypes.c_uint(0)))
    owners.append(mount)
    root = ownership._acquire(lambda: os.open('.', os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                                            dir_fd=mount.number))
    owners.append(root)
    ownership._require(backend._browser_fstatfs_magic(root.number) == 0x01021994)
    return mount, root


def _inventory(root, expected_file):
    root.verify()
    root_info = os.fstat(root.number)
    ownership._require(stat.S_ISDIR(root_info.st_mode) and stat.S_IMODE(root_info.st_mode) == 0o700
                       and root_info.st_uid == root_info.st_gid == 0
                       and os.listdir(root.number) == ['sentinel'])
    descriptor = ownership._acquire(lambda: os.open('sentinel', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                                                   dir_fd=root.number))
    try:
        info = os.fstat(descriptor.number)
        ownership._require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o555
                           and info.st_uid == info.st_gid == 0 and info.st_nlink == 1 and info.st_size == 341
                           and descriptor.identity == expected_file and info.st_dev == root_info.st_dev
                           and ownership._mount_id(descriptor) == ownership._mount_id(root)
                           and os.pread(descriptor.number, 342, 0) == sentinel_bytes())
    finally:
        descriptor.close()


def _write_refusal(root):
    root.verify()
    try:
        raw = os.open('write-must-refuse',
                      os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=root.number)
    except OSError as exc:
        ownership._require(exc.errno == errno.EROFS)
        return
    descriptor = ownership._acquire(lambda: raw)
    descriptor.close()
    raise ownership.NamespaceProofError('projection_write_did_not_refuse')


def _named_identity(parent, name):
    parent.verify()
    descriptor = ownership._acquire(lambda: os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                                                   dir_fd=parent.number))
    try:
        return descriptor.identity, ownership._mount_id(descriptor)
    finally:
        descriptor.close()


def _mount_rows():
    raw = ownership._read('/proc/self/mountinfo')
    ownership._require(type(raw) is bytes and 0 < len(raw) <= ownership._LIMIT
                       and raw.endswith(b'\n'))
    result = []
    for line in raw.splitlines():
        left, separator, right = line.partition(b' - ')
        fields = left.split()
        ownership._require(separator and len(fields) == 6 and len(right.split()) == 3)
        result.append((ownership._integer(fields[0]), ownership._integer(fields[1], zero=True)))
    ownership._require(len({row[0] for row in result}) == len(result))
    return result


def _detach_owned(root, expected):
    root.verify()
    ownership._require((root.identity, ownership._mount_id(root)) == expected)
    rows = _mount_rows()
    if any(number == expected[1] for number, parent in rows):
        ownership._require(not any(parent == expected[1] for number, parent in rows))
        backend._browser_unmount(f'/proc/self/fd/{root.number}'.encode(), 2)
    ownership._require(not any(number == expected[1] for number, parent in _mount_rows()))


def _check_projection(parent, generation):
    """Parent is already an owned namespace-local tmpfs; never a host directory."""
    owners = []
    underlying = root = None
    created = False
    underlying_identity = mounted_identity = None
    body_error = None
    cleanup_errors = []
    result = None
    name = 'buffalo-hosted-projection-' + generation
    target = '/tmp/' + name
    try:
        parent.verify()
        os.mkdir(name, 0o700, dir_fd=parent.number)
        created = True
        named = os.stat(name, dir_fd=parent.number, follow_symlinks=False)
        ownership._require(stat.S_ISDIR(named.st_mode) and stat.S_IMODE(named.st_mode) == 0o700)
        underlying_identity = ownership._identity(named), ownership._mount_id(parent)
        underlying = ownership._acquire(lambda: os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                                                       dir_fd=parent.number))
        owners.append(underlying)
        ownership._require((underlying.identity, ownership._mount_id(underlying)) == underlying_identity)
        ownership._require(_named_identity(parent, name) == underlying_identity and not os.listdir(underlying.number))
        mount, root = _open_tmpfs(owners)
        mounted_identity = root.identity, ownership._mount_id(root)
        writer = ownership._acquire(lambda: os.open('sentinel', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                                                   0o500, dir_fd=root.number))
        owners.append(writer)
        ownership._require(os.write(writer.number, sentinel_bytes()) == 341)
        os.fchmod(writer.number, 0o555)
        file_identity = writer.identity
        writer.close()
        _inventory(root, file_identity)
        backend._seal_detached_browser_projection_mount(mount.number)
        _write_refusal(root)
        _inventory(root, file_identity)
        ownership._require(_named_identity(parent, name) == underlying_identity)
        backend._attach_detached_browser_projection_mount(mount.number, underlying.number)
        ownership._require(_named_identity(parent, name) == mounted_identity)
        observation = parse_mount(ownership._read('/proc/self/mountinfo'), mounted_identity[1], target)
        _inventory(root, file_identity)
        _write_refusal(root)
        result = dict(mount=observation, sentinel_sha256=_SENTINEL_SHA256, sentinel_bytes=341,
                      maximum_bytes=_SIZE, maximum_inodes=_INODES, write_errno=errno.EROFS,
                      sentinel_exec='NOT_RUN', reason='STOPPED_CGROUP_ENROLLMENT_REQUIRED',
                      execution_authority=False)
    except BaseException as exc:
        body_error = exc
    if root is not None and mounted_identity is not None:
        try:
            _detach_owned(root, mounted_identity)
            ownership._require(_named_identity(parent, name) == underlying_identity)
        except BaseException as exc:
            cleanup_errors.append(exc)
    for descriptor in reversed(owners):
        if descriptor is underlying:
            continue
        try:
            descriptor.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
    if created:
        try:
            ownership._require(underlying_identity is not None and _named_identity(parent, name) == underlying_identity)
            if underlying is not None:
                underlying.verify()
                ownership._require(not os.listdir(underlying.number))
            os.rmdir(name, dir_fd=parent.number)
            try:
                os.stat(name, dir_fd=parent.number, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ownership.NamespaceCleanupError('projection_name_remains')
        except BaseException as exc:
            cleanup_errors.append(exc)
    if underlying is not None:
        try:
            underlying.close()
        except BaseException as exc:
            cleanup_errors.append(exc)
    if cleanup_errors:
        raise ownership.NamespaceCleanupError('projection_cleanup_refused') from cleanup_errors[0]
    if body_error is not None:
        raise body_error
    ownership._require(result is not None)
    return dict(result, cleanup_complete=True)


def _path_identity(path):
    descriptor = ownership._acquire(lambda: os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW))
    try:
        return descriptor.identity, ownership._mount_id(descriptor)
    finally:
        descriptor.close()


def observe_projection(generation):
    """No processes/exec. A namespace-local /tmp also disappears on SIGKILL."""
    _platform()
    ownership._require(type(generation) is str and re.fullmatch(r'[0-9a-f]{64}', generation)
                       and os.getpid() == 1 and os.geteuid() == os.getegid() == 0)
    sentinel_bytes()
    previous_mask = backend._block_browser_projection_signals()
    owners = []
    parent = None
    original = parent_identity = None
    result = body_error = None
    errors = []
    try:
        _mount_rows()  # Refuse shared propagation before attaching either mount.
        underlying = ownership._acquire(lambda: os.open('/tmp', os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW))
        owners.append(underlying)
        original = underlying.identity, ownership._mount_id(underlying)
        ownership._require(_path_identity('/tmp') == original)
        # No directory is ever created in the host /tmp filesystem. Both this
        # staging parent and the sealed child disappear when I's namespace dies.
        mount, parent = _open_tmpfs(owners)
        parent_identity = parent.identity, ownership._mount_id(parent)
        ownership._require(_path_identity('/tmp') == original)
        backend._attach_detached_browser_projection_mount(mount.number, underlying.number)
        ownership._require(_path_identity('/tmp') == parent_identity)
        result = _check_projection(parent, generation)
    except BaseException as exc:
        body_error = exc
    if parent is not None and parent_identity is not None:
        try:
            _detach_owned(parent, parent_identity)
            ownership._require(_path_identity('/tmp') == original)
        except BaseException as exc:
            errors.append(exc)
    for descriptor in reversed(owners):
        try:
            descriptor.close()
        except BaseException as exc:
            errors.append(exc)
    try:
        backend._restore_browser_projection_signals(previous_mask)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)) and body_error is None:
            body_error = exc
        else:
            errors.append(exc)
    if errors:
        raise ownership.NamespaceCleanupError('projection_parent_cleanup_refused') from errors[0]
    if body_error is not None:
        raise body_error
    ownership._require(result is not None)
    return result
