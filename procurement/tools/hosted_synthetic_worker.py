"""Fixed authority reduction for a projected hosted worker, never a launcher.

Only the trusted namespace init calls this after switching to its sealed root.
It installs no configurable sandbox or host policy. The policy stacks on any
existing seccomp filter and prevents a worker regaining namespace authority.
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

import hosted_synthetic_namespace as ns


_AUDIT_ARCH_X86_64 = 0xC000003E
_KILL = 0x80000000
_ALLOW = 0x7FFF0000
_ERRNO = 0x00050000
_NAMESPACE_FLAGS = 0x7E020000
_SECUREBITS = 0xEF  # NOROOT/NO_SETUID_FIXUP/NO_AMBIENT_RAISE + all locks; KEEP_CAPS off
_CAPS = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')
# No new user/mount/cgroup namespace, mount authority, cross-process FD/memory
# access, handle-based filesystem lookup, BPF or asynchronous syscall bypass.
_DENIED = (101, 155, 161, 165, 166, 248, 249, 250, 272, 298, 303, 304, 308,
           310, 311, 321, 323, 425, 426, 427, 428, 429, 430, 431, 432, 433, 438, 442)


def policy_bytes():
    # struct seccomp_data: nr@0, arch@4, args[0]@16. Reject x32 as well as
    # other audit architectures before interpreting syscall numbers/arguments.
    program = [(0x20, 0, 0, 4), (0x15, 1, 0, _AUDIT_ARCH_X86_64), (0x06, 0, 0, _KILL),
               (0x20, 0, 0, 0), (0x35, 0, 1, 0x40000000), (0x06, 0, 0, _KILL)]
    for number in _DENIED:
        program.extend(((0x15, 0, 1, number), (0x06, 0, 0, _ERRNO | errno.EPERM)))
    # clone3 flags live behind a pointer: classic BPF cannot validate them.
    # ENOSYS permits libc's ordinary clone fallback, whose flags are checked.
    program.extend(((0x15, 0, 1, 435), (0x06, 0, 0, _ERRNO | errno.ENOSYS),
                    (0x15, 0, 6, 56), (0x20, 0, 0, 16),
                    (0x45, 0, 1, _NAMESPACE_FLAGS), (0x06, 0, 0, _ERRNO | errno.EPERM),
                    (0x20, 0, 0, 20), (0x15, 1, 0, 0),
                    (0x06, 0, 0, _ERRNO | errno.EPERM), (0x06, 0, 0, _ALLOW)))
    return b''.join(struct.pack('<HBBI', *row) for row in program)


POLICY_SHA256 = hashlib.sha256(b'BUFFALO_HOSTED_WORKER_SECCOMP_V1\0' + policy_bytes()).hexdigest()


def _status(pid='self'):
    ns._require(pid == 'self' or type(pid) is int and pid > 1)
    raw = ns._read(f'/proc/{pid}/status', 16384)
    result = ns.parse_status(raw)
    for key in (*_CAPS, 'Seccomp', 'Seccomp_filters'):
        pattern = rb'[0-9a-f]{16}' if key in _CAPS else rb'(?:0|[1-9][0-9]{0,3})'
        values = re.findall(rb'(?m)^' + key.encode() + rb':\s*(' + pattern + rb')$', raw)
        ns._require(len(values) == 1)
        result[key] = int(values[0], 16 if key in _CAPS else 10)
    return result


def _get_prctl(option):
    result = ns.backend._browser_libc().prctl(option, 0, 0, 0, 0)
    ns._require(result >= 0)
    return result


def drop_capabilities():
    """Called in U1 while SETPCAP still exists, never on the host operator."""
    ns._require(os.getpid() > 1 and os.getresuid() == os.getresgid() == (0,)*3
                and os.getgroups() == [] and len(os.listdir('/proc/self/task')) == 1)
    before = _status()
    ns._require(before['uid'] == before['gid'] == [0]*4 and before['groups'] == [])
    for kind in ('uid', 'gid'):
        mapping = ns._read('/proc/self/' + kind + '_map', 128).split()
        ns._require(len(mapping) == 3 and mapping[0] == b'0' and mapping[2] == b'1'
                    and ns._integer(mapping[1]) > 0)
    return _erase_capabilities(before)


def _erase_capabilities(before):
    """Shared irreversible erasure; callers first prove their fixed identity."""
    last = ns._integer(ns._read('/proc/sys/kernel/cap_last_cap', 32).strip())
    ns._require(last < 64 and before['CapEff'] & (1 << 8))  # CAP_SETPCAP
    ns.backend._browser_prctl(38, 1)
    ns.backend._browser_prctl(28, _SECUREBITS)
    ns.backend._browser_prctl(47, 4)  # PR_CAP_AMBIENT_CLEAR_ALL
    for number in range(last + 1):
        ns.backend._browser_prctl(24, number)  # PR_CAPBSET_DROP
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32),
                   ('inheritable', ctypes.c_uint32)]
    header = Header(0x20080522, 0)
    data = (Data * 2)()
    ns._require(ns.backend._browser_libc().capset(ctypes.byref(header), ctypes.byref(data)) == 0)
    ns.backend._browser_prctl(4, 0)
    after = _status()
    ns._require(all(after[key] == 0 for key in _CAPS) and after['no_new_privs'] == 1
                and _get_prctl(27) == _SECUREBITS and _get_prctl(3) == 0)
    return {key: after[key] for key in _CAPS}


def install_filter():
    """Fixed bytes installed and read back in this single-threaded process."""
    ns._require(platform.machine() == 'x86_64' and struct.calcsize('P') == 8
                and len(os.listdir('/proc/self/task')) == 1)
    before = _status()
    ns._require(before['no_new_privs'] == 1 and before['Seccomp'] in (0, 2))
    raw = policy_bytes()
    class Instruction(ctypes.Structure):
        _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte),
                   ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint32)]
    class Program(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ushort), ('filter', ctypes.POINTER(Instruction))]
    instructions = (Instruction * (len(raw)//8)).from_buffer_copy(raw)
    program = Program(len(instructions), instructions)
    libc = ns.backend._browser_libc()
    ns._require(libc.prctl(ctypes.c_int(22), ctypes.c_ulong(2), ctypes.byref(program),
                          ctypes.c_ulong(0), ctypes.c_ulong(0)) == 0)
    after = _status()
    ns._require(after['Seccomp'] == 2 and after['Seccomp_filters'] == before['Seccomp_filters'] + 1
                and after['no_new_privs'] == 1)
    return {'policy': 'BUFFALO_HOSTED_WORKER_SECCOMP_V1', 'sha256': POLICY_SHA256,
            'filter_count_before': before['Seccomp_filters'],
            'filter_count_after': after['Seccomp_filters'], 'installed_in_current_process': True}


def require_projected_root(root_identity, root_mount, *, channels=()):
    """Exact root/cwd and inherited FD inventory before authority reduction."""
    ns._require(type(root_identity) is tuple and len(root_identity) == 2
                and all(type(x) is int and x > 0 for x in root_identity)
                and type(root_mount) is int and root_mount > 0
                and os.stat('/').st_dev == root_identity[0]
                and ns._identity(os.stat('/')) == ns._identity(os.stat('.')) == root_identity)
    root = ns._acquire(lambda: os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW))
    try:
        flags = os.fstatvfs(root.number).f_flag
        ns._require(ns._mount_id(root) == root_mount and ns.backend._browser_fstatfs_magic(root.number) == 0x01021994
                    and flags & os.ST_RDONLY and flags & os.ST_NOSUID and flags & os.ST_NODEV
                    and not flags & os.ST_NOEXEC)
    finally:
        root.close()
    allowed = {0, 1, 2}
    for descriptor in channels:
        descriptor.verify()
        info = os.fstat(descriptor.number)
        ns._require(descriptor.number > 2 and descriptor.number not in allowed and stat.S_ISFIFO(info.st_mode))
        allowed.add(descriptor.number)
    observed = set()
    for name in os.listdir('/proc/self/fd'):
        try: os.fstat(int(name))
        except FileNotFoundError: continue
        except OSError as error:
            if error.errno == errno.EBADF: continue
            raise
        observed.add(int(name))
    ns._require(observed == allowed)
    for path in ('/.oldroot', '/sys', '/run', '/home', '/root', '/etc/ld.so.preload', '/etc/ld.so.cache'):
        ns._require(not os.path.lexists(path))
    return {'root_identity': list(root_identity), 'root_mount_id': root_mount,
            'descriptors': sorted(allowed), 'host_control_paths_absent': True}


def restrict_worker(root_identity, root_mount, *, channels=()):
    """Trusted I invokes in W after root switch; no credential is accepted."""
    root = require_projected_root(root_identity, root_mount, channels=channels)
    caps = drop_capabilities()
    policy = install_filter()
    ns._require(_get_prctl(3) == 0 and all(_status()[key] == 0 for key in _CAPS))
    return {'contract': 'BUFFALO_HOSTED_PROJECTED_WORKER_V1', 'root': root,
            'capabilities': caps, 'no_new_privs': 1, 'dumpable': 0, 'seccomp': policy,
            'execution_authority': False, 'credential_release': 'WITHHELD'}


def attest_worker(pid, root_identity, filter_count):
    """Trusted I/G read the stopped consumer, independently of worker output.

    Kernel status exposes stacked filter count, not filter bytes. The fixed
    policy hash is source-bound installer identity, never a kernel BPF export.
    """
    from hosted_synthetic_runtime import SENTINEL
    from hosted_synthetic_projection import sentinel_bytes
    ns._require(type(pid) is int and pid > 1 and type(filter_count) is int and filter_count > 0)
    current = _status(pid)
    ns._require(current['uid'] == current['gid'] == [0]*4 and current['groups'] == []
                and current['no_new_privs'] == 1 and all(current[key] == 0 for key in _CAPS)
                and current['Seccomp'] == 2 and current['Seccomp_filters'] == filter_count
                and ns._process(pid)['state'] == 'T')
    root = ns._acquire(lambda: os.open(f'/proc/{pid}/root', os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC))
    try:
        ns._require(root.identity == tuple(root_identity) == ns._identity(os.stat(f'/proc/{pid}/cwd')))
        flags = os.fstatvfs(root.number).f_flag
        ns._require(ns.backend._browser_fstatfs_magic(root.number) == 0x01021994
                    and flags & os.ST_RDONLY and flags & os.ST_NOSUID and flags & os.ST_NODEV
                    and not flags & os.ST_NOEXEC)
        mount_id = ns._mount_id(root)
    finally: root.close()
    ns._require(ns._identity(os.stat(f'/proc/{pid}/exe')) == ns._identity(os.stat(SENTINEL))
                and ns._read(SENTINEL, 342) == sentinel_bytes()
                and sorted(os.listdir(f'/proc/{pid}/fd')) == ['0','1','2']
                and all(os.readlink(f'/proc/{pid}/fd/{fd}') == '/dev/null' for fd in (0,1,2)))
    return {'contract': 'BUFFALO_HOSTED_PROJECTED_WORKER_V1',
            'root_identity': list(root_identity), 'root_mount_id': mount_id,
            'capabilities': {key: current[key] for key in _CAPS}, 'no_new_privs': 1,
            'seccomp': 2, 'filter_count': filter_count, 'installer_policy_sha256': POLICY_SHA256,
            'descriptors': [0,1,2], 'sentinel_sha256': hashlib.sha256(sentinel_bytes()).hexdigest(),
            'execution_authority': False, 'credential_release': 'WITHHELD'}
