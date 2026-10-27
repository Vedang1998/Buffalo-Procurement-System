"""Disposable retained private-proc proof; never a payload/release authority.

The outer guardian remains in the supervisor's PID view. Its child is PID 1
in a fresh namespace and retains private procfs while a stopped PID 2 sentinel
is independently observed. The delegated path re-executes its bound diagnostic
runtime inside a sealed root. No application, credential or database starts.
Legacy EPERM observers are deliberately not changed by this component.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import sys
import time

# This module's direct entry is only a bounded diagnostic continuation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
if __name__ == '__main__':
    # Projection cleanup errors must share this process's exact ownership types.
    sys.modules['hosted_synthetic_namespace'] = sys.modules[__name__]
import run_local_staging_acceptance as backend


class NamespaceProofError(RuntimeError):
    pass


class NamespaceCleanupError(NamespaceProofError):
    """A failed cleanup obligation remains a failure after fallback cleanup."""


_LIMIT = 256 * 1024
_TIMEOUT = 20.0
_NAMESPACES = ("user", "mnt", "pid", "net")
_DECIMAL = re.compile(rb"(?:0|[1-9][0-9]{0,9})\Z")


def _require(condition: bool) -> None:
    if not condition:
        raise NamespaceProofError("namespace_proof_refused")


def _read(path: str, maximum: int = _LIMIT) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        result = bytearray()
        while len(result) <= maximum:
            block = os.read(descriptor, min(65536, maximum + 1 - len(result)))
            if not block:
                break
            result.extend(block)
        _require(len(result) <= maximum)
        return bytes(result)
    finally:
        os.close(descriptor)


def _integer(raw: bytes, *, zero: bool = False) -> int:
    _require(type(raw) is bytes and _DECIMAL.fullmatch(raw) is not None)
    value = int(raw)
    _require((zero or value > 0) and value <= 2**31 - 1)
    return value


def parse_process_stat(raw: bytes, expected_pid: int) -> dict:
    """Kernel stat parser accepting namespace PID 1 / parent 0 explicitly."""
    _require(type(expected_pid) is int and 0 < expected_pid <= 2**31 - 1)
    _require(type(raw) is bytes and len(raw) <= 8192 and raw.endswith(b"\n"))
    prefix, separator, tail = raw.rpartition(b") ")
    pid, opening, _ = prefix.partition(b" (")
    fields = tail.split()
    _require(bool(separator and opening) and len(fields) >= 22)
    _require(_integer(pid) == expected_pid and fields[0] in tuple(bytes((value,)) for value in b"RSDZTtXxKWPI"))
    _require(re.fullmatch(rb"[1-9][0-9]{0,19}", fields[19]) is not None and int(fields[19]) < 2**64)
    return dict(pid=expected_pid, state=fields[0].decode("ascii"),
                parent=_integer(fields[1], zero=True),
                group=_integer(fields[2], zero=True),
                session=_integer(fields[3], zero=True),
                start=int(fields[19]))


def parse_status(raw: bytes) -> dict:
    _require(type(raw) is bytes and len(raw) <= 16384 and raw.endswith(b"\n"))
    pairs = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(b":")
        _require(bool(separator and key) and key not in pairs)
        pairs[key] = value.strip()
    required = (b"Pid", b"PPid", b"NSpid", b"Uid", b"Gid", b"Groups", b"NoNewPrivs")
    _require(all(key in pairs for key in required))
    result = {
        "pid": _integer(pairs[b"Pid"]), "parent": _integer(pairs[b"PPid"], zero=True),
        "nspid": [_integer(value) for value in pairs[b"NSpid"].split()],
        "uid": [_integer(value, zero=True) for value in pairs[b"Uid"].split()],
        "gid": [_integer(value, zero=True) for value in pairs[b"Gid"].split()],
        "groups": [_integer(value, zero=True) for value in pairs[b"Groups"].split()],
        "no_new_privs": _integer(pairs[b"NoNewPrivs"], zero=True),
    }
    _require(1 <= len(result["nspid"]) <= 32 and result["nspid"][0] == result["pid"]
             and len(result["uid"]) == len(result["gid"]) == 4
             and len(result["groups"]) <= 128 and result["no_new_privs"] in (0, 1))
    return result


def parse_id_map(raw: bytes, outside: int) -> list[int]:
    _require(type(outside) is int and 0 <= outside <= 2**31 - 1)
    _require(type(raw) is bytes and len(raw) <= 128 and raw.endswith(b"\n")
             and len(raw.splitlines()) == 1)
    values = [_integer(value, zero=True) for value in raw.split()]
    _require(values == [0, outside, 1])
    return values


def parse_private_proc_mount(raw: bytes, expected_mount_id: int) -> dict:
    """Require a unique proc root and private propagation on every mount."""
    _require(type(raw) is bytes and 0 < len(raw) <= _LIMIT and raw.endswith(b"\n"))
    _require(type(expected_mount_id) is int and expected_mount_id > 0)
    selected = []
    identities = set()
    for line in raw.splitlines():
        before, separator, after = line.partition(b" - ")
        left, right = before.split(), after.split()
        _require(bool(separator) and len(left) >= 6 and len(right) == 3)
        number = _integer(left[0])
        _require(number not in identities)
        identities.add(number)
        _integer(left[1], zero=True)
        _require(re.fullmatch(rb"[0-9]+:[0-9]+", left[2]) is not None)
        # Unknown propagation/optional fields do not establish private mounts.
        _require(len(left) == 6)
        if number == expected_mount_id:
            options = left[5].split(b",")
            _require(len(options) == len(set(options)))
            _require(left[3] == b"/" and left[4] == b"/proc" and right[0] == b"proc"
                     and {b"nosuid", b"nodev", b"noexec"}.issubset(options))
            selected.append({"mount_id": number, "device": left[2].decode("ascii"),
                             "flags": ["nosuid", "nodev", "noexec"],
                             "propagation": "private"})
    _require(len(selected) == 1)
    return selected[0]


def _namespaces(pid: str) -> dict:
    result = {}
    for name in _NAMESPACES:
        try:
            result[name] = list(_identity(os.stat(f"/proc/{pid}/ns/{name}")))
        except OSError as exc:
            role = {"self": "self", "1": "init", "2": "sentinel"}.get(pid, "outer")
            _require(type(exc.errno) is int and 0 < exc.errno <= 4095)
            raise NamespaceProofError(f"_namespace_{role}_{name}:{exc.errno}") from None
    return result


def _identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


@dataclass
class _Descriptor:
    number: int
    identity: tuple[int, int]
    flags: int
    _owner: object = field(default=None, repr=False, compare=False)

    @classmethod
    def capture(cls, number):
        _require(type(number) is int and number > 2)
        _require(not os.get_inheritable(number))
        owned = cls(number, _identity(os.fstat(number)), fcntl.fcntl(number, fcntl.F_GETFL))
        owned._owner = owned
        return owned

    def verify(self):
        _require(self._owner is self and self.number > 2 and _identity(os.fstat(self.number)) == self.identity
                 and fcntl.fcntl(self.number, fcntl.F_GETFL) & ~os.O_NONBLOCK
                 == self.flags & ~os.O_NONBLOCK and not os.get_inheritable(self.number))

    def close(self):
        if self.number >= 0:
            try:
                self.verify()
                os.close(self.number)
                self.number = -1
            except BaseException as exc:
                raise NamespaceCleanupError('descriptor_cleanup_refused') from exc


def _pipe():
    numbers = os.pipe2(os.O_CLOEXEC)
    identities = {}
    try:
        for number in numbers:
            identities[number] = _identity(os.fstat(number))
        return tuple(_Descriptor.capture(number) for number in numbers)
    except BaseException as error:
        errors = []
        for number in numbers:
            try:
                if number in identities:
                    _require(_identity(os.fstat(number)) == identities[number])
                os.close(number)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise NamespaceCleanupError("pipe_cleanup_refused") from error
        raise


def _acquire(factory):
    """Retain a raw allocation until descriptor identity capture succeeds.

    Callers defer cancellation in their single-threaded process. No callback
    executes between the kernel allocation and initial fstat; failure there
    therefore still owns the newly returned descriptor. Later cleanup compares
    the original kernel identity before closing and preserves replacements.
    """
    number = factory()
    identity = None
    try:
        identity = _identity(os.fstat(number))
        return _Descriptor.capture(number)
    except BaseException as error:
        try:
            if identity is not None:
                _require(_identity(os.fstat(number)) == identity)
            os.close(number)
        except BaseException:
            raise NamespaceCleanupError("descriptor_acquisition_cleanup_refused") from error
        raise


def _mount_id(descriptor):
    descriptor.verify()
    raw = _read(f"/proc/self/fdinfo/{descriptor.number}", 4096)
    matches = re.findall(rb"(?m)^mnt_id:\s*([0-9]+)$", raw)
    _require(len(matches) == 1)
    return _integer(matches[0])


def _process(pid):
    return parse_process_stat(_read(f"/proc/{pid}/stat", 8192), pid)


def _send(descriptor, generation, stage, deadline, **fields):
    descriptor.verify()
    backend._write_browser_guardian_frame(descriptor.number, dict(
        protocol=backend._BROWSER_CONTAINMENT_PROTOCOL, generation=generation,
        stage=stage, **fields), deadline=deadline)


def _receive(descriptor, generation, stage, deadline, *, fields=()):
    descriptor.verify()
    value = backend._read_browser_guardian_frame(descriptor.number, deadline=deadline)
    if (type(value) is dict and set(value) == {"protocol", "generation", "stage", "location", "cleanup_failed"}
            and value["protocol"] == backend._BROWSER_CONTAINMENT_PROTOCOL
            and value["generation"] == generation and value["stage"] == "REFUSED"
            and type(value["cleanup_failed"]) is bool
            and type(value["location"]) is str
            and re.fullmatch(r"_[a-z_]{1,48}:[1-9][0-9]{0,3}", value["location"])):
        selected = NamespaceCleanupError if value["cleanup_failed"] else NamespaceProofError
        raise selected(value["location"])
    _require(type(value) is dict and set(value) == {"protocol", "generation", "stage", *fields}
             and value["protocol"] == backend._BROWSER_CONTAINMENT_PROTOCOL
             and value["generation"] == generation and value["stage"] == stage)
    return value


def _failure_location(error):
    if isinstance(error, NamespaceProofError) and re.fullmatch(r"_[a-z_]{1,48}:[1-9][0-9]{0,3}", str(error)):
        return str(error)
    location = "_unknown:1"
    trace = error.__traceback__
    while trace is not None:
        name = trace.tb_frame.f_code.co_name
        if (trace.tb_frame.f_code.co_filename == __file__ and name not in {"_require", "_namespaces", "_read", "_acquire"}
                and re.fullmatch(r"_[a-z_]{1,48}", name) and 0 < trace.tb_lineno < 10000):
            location = f"{name}:{trace.tb_lineno}"
        trace = trace.tb_next
    return location


def _child_failure(status, generation, deadline, error):
    try:
        _send(status, generation, "REFUSED", deadline, location=_failure_location(error),
              cleanup_failed=isinstance(error, NamespaceCleanupError))
    except BaseException:
        pass


def _wait(pid, deadline, *, stopped=False):
    while time.monotonic() < deadline:
        selected = os.waitid(os.P_PID, pid, os.WNOHANG | os.WNOWAIT | os.WEXITED
                             | (os.WSTOPPED if stopped else 0))
        if selected is not None:
            _require(selected.si_pid == pid and
                     ((selected.si_code == os.CLD_STOPPED and selected.si_status == signal.SIGSTOP)
                      if stopped else (selected.si_code == os.CLD_EXITED and selected.si_status == 0)))
            os.waitpid(pid, os.WUNTRACED if stopped else 0)
            return
        time.sleep(0.005)
    raise NamespaceProofError("namespace_timeout")


def _pidfd(pid):
    descriptor = _acquire(lambda: os.pidfd_open(pid, 0))
    try:
        _require(os.readlink(f"/proc/self/fd/{descriptor.number}") == "anon_inode:[pidfd]")
        raw = _read(f"/proc/self/fdinfo/{descriptor.number}", 4096)
        matches = re.findall(rb"(?m)^Pid:\s*([0-9]+)$", raw)
        _require(len(matches) == 1 and _integer(matches[0]) == pid
                 and not backend._browser_pidfd_is_terminal(descriptor.number))
        return descriptor
    except BaseException:
        descriptor.close()
        raise


def _hardening(parent):
    backend._browser_prctl(1, int(signal.SIGKILL))
    _require(os.getppid() == parent)
    backend._browser_prctl(4, 0)
    backend._browser_prctl(38, 1)


def _require_mapped_identity():
    """U1 mount coordinator, not a zero-capability host/payload entry."""
    raw = _read('/proc/self/status', 16384)
    current = parse_status(raw)
    _require(current['uid'] == current['gid'] == [0]*4 and current['groups'] == []
             and current['no_new_privs'] == 1 and os.getresuid() == os.getresgid() == (0,)*3)
    caps = {}
    for name in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        values = re.findall(rb'(?m)^' + name.encode() + rb':\s*([0-9a-f]{16})$', raw)
        _require(len(values) == 1)
        caps[name] = int(values[0], 16)
    last = _integer(_read('/proc/sys/kernel/cap_last_cap', 32).strip())
    _require(last < 64 and caps['CapInh'] == caps['CapAmb'] == 0
             and caps['CapPrm'] == caps['CapEff'] == caps['CapBnd'] == (1 << (last + 1))-1)


def _runtime_images():
    """Bind readable ELF/loader identities, without claiming runtime closure."""
    current_identity = _identity(os.stat('/proc/self/exe'))
    executable = _acquire(lambda: os.open(str(Path('/proc/self/exe').resolve()),
                                         os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW))
    loader = None
    try:
        _require(executable.identity == current_identity == _identity(os.stat('/proc/self/exe')))
        header = os.pread(executable.number, 64, 0)
        _require(len(header) == 64 and header[:7] == b'\x7fELF\x02\x01\x01')
        offset = struct.unpack_from('<Q', header, 32)[0]
        size, count = struct.unpack_from('<HH', header, 54)
        _require(size == 56 and 0 < count <= 128 and offset < 1048576)
        interpreters = []
        for index in range(count):
            entry = os.pread(executable.number, size, offset + size * index)
            _require(len(entry) == size)
            if struct.unpack_from('<I', entry)[0] == 3:
                start = struct.unpack_from('<Q', entry, 8)[0]
                length = struct.unpack_from('<Q', entry, 32)[0]
                _require(1 < length <= 4096)
                raw = os.pread(executable.number, length, start)
                _require(len(raw) == length and raw.startswith(b'/') and raw.endswith(b'\0')
                         and b'\0' not in raw[:-1])
                interpreters.append(raw[:-1].decode('ascii'))
        _require(len(interpreters) == 1)
        loader = _acquire(lambda: os.open(str(Path(interpreters[0]).resolve()),
                                         os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW))
        for descriptor in (executable, loader):
            info = os.fstat(descriptor.number)
            # Host UID 0 is unmapped in U1. These are readable identities of
            # the already-running diagnostic, not a trusted runtime closure.
            _require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o6022
                     and info.st_mode & 0o444 == 0o444)
        identity = dict(executable=list(executable.identity), loader=list(loader.identity))
        _require(_image_is_mapped(loader.identity))
        loader.close()
        return executable, identity
    except BaseException as error:
        errors = []
        for descriptor in (loader, executable):
            if descriptor is not None:
                try:
                    descriptor.close()
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise NamespaceCleanupError('runtime_image_cleanup_refused') from error
        raise


def _image_is_mapped(identity):
    device, inode = identity
    expected_device = f'{os.major(device):02x}:{os.minor(device):02x}'.encode()
    mapped = [line.split() for line in _read('/proc/self/maps').splitlines()]
    return any(len(row) >= 5 and row[3] == expected_device and row[4] == str(inode).encode()
               and row[1].startswith(b'r') for row in mapped)


def _resume_descriptor(value):
    _require(type(value) is list and len(value) == 4
             and all(type(item) is int and item >= 0 for item in value) and value[0] > 2)
    _require(list(_identity(os.fstat(value[0]))) == value[1:3]
             and fcntl.fcntl(value[0], fcntl.F_GETFL) == value[3]
             and os.get_inheritable(value[0]))
    os.set_inheritable(value[0], False)
    descriptor = _Descriptor.capture(value[0])
    _require([descriptor.number, *descriptor.identity, descriptor.flags] == value)
    return descriptor


def _resume_supervisor(parent, supervisor):
    _require(type(supervisor) is int and supervisor > 1)
    _hardening(supervisor)
    parent.verify()
    _require(os.readlink(f'/proc/self/fd/{parent.number}') == 'anon_inode:[pidfd]'
             and re.findall(rb'(?m)^Pid:\s*([0-9]+)$', _read(f'/proc/self/fdinfo/{parent.number}', 4096))
             == [str(supervisor).encode()] and not backend._browser_pidfd_is_terminal(parent.number))


def _resume_signal_state(expected):
    _require(type(expected) is list and all(type(item) is int for item in expected)
             and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
             and sorted(int(item) for item in signal.pthread_sigmask(signal.SIG_BLOCK, [])) == expected
             and set(backend._BROWSER_PROJECTION_MASKED_SIGNALS).issubset(expected))


def _resume_namespaces(original, expected):
    _require(type(original) is dict and type(expected) is dict
             and set(original) == set(expected) == set(_NAMESPACES)
             and _namespaces('self') == expected
             and all(original[name] != expected[name] for name in ('user', 'mnt', 'net'))
             and original['pid'] == expected['pid'])
    # Linux exposes no target for a fresh PID namespace until its first child
    # exists. The symlink must exist; its inode is independently bound after I
    # is created, before the retained evidence can be emitted.
    _require(stat.S_ISLNK(os.lstat('/proc/self/ns/pid_for_children').st_mode))
    try:
        os.stat('/proc/self/ns/pid_for_children')
    except FileNotFoundError:
        return
    raise NamespaceProofError('pid_namespace_not_empty')


def _reexec_guardian(gate, status, parent, generation, supervisor, deadline,
                    outside_uid, outside_gid, original_namespaces, cgroup_required=False, *, runtime=None):
    # fork preserves mm->user_ns. Exec after mapping creates the new mm under
    # U1, permitting independent nondumpable child inspection in that namespace.
    _require(not cgroup_required or runtime is not None)
    runtime_root = None
    script = str(Path(__file__).resolve())
    executable_path = sys.executable
    if runtime is None:
        executable, images = _runtime_images()
    else:
        from hosted_synthetic_runtime import enter_runtime, projected_images, PROJECTED_TOOLS
        _require(runtime.generation == generation)
        runtime_root = enter_runtime(runtime)
        executable, images = projected_images(runtime)
        executable_path = runtime.executable
        script = PROJECTED_TOOLS + '/hosted_synthetic_namespace.py'
    bootstrap = _acquire(lambda: os.memfd_create('buffalo-diagnostic', os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING))
    descriptors = (gate, status, parent)
    packet = dict(generation=generation, supervisor=supervisor, deadline=deadline,
                  cgroup_required=cgroup_required,
                  outside_uid=outside_uid, outside_gid=outside_gid,
                  original_namespaces=original_namespaces, namespaces=_namespaces('self'),
                  process=_process(os.getpid()), images=images,
                  descriptors=[[item.number, *item.identity, fcntl.fcntl(item.number, fcntl.F_GETFL)]
                               for item in descriptors],
                  mask=sorted(int(item) for item in signal.pthread_sigmask(signal.SIG_BLOCK, [])))
    if runtime is not None:
        packet.update(runtime=runtime.packet(), runtime_root=runtime_root)
    encoded = json.dumps(packet, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(len(encoded) <= 8192 and os.write(bootstrap.number, encoded) == len(encoded))
    seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    fcntl.fcntl(bootstrap.number, fcntl.F_ADD_SEALS, seals)
    for item in (*descriptors, bootstrap):
        item.verify()
        os.set_inheritable(item.number, True)
    if runtime is not None:
        os.set_inheritable(runtime.bundle.descriptor, True)
    for number in (0, 1, 2):
        _require(os.readlink(f'/proc/self/fd/{number}') == '/dev/null')
        os.set_inheritable(number, True)
    os.execve(executable.number,
              [executable_path, '-I', '-S', '-B', script,
               '--resume-diagnostic', str(bootstrap.number)],
              {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})
    raise NamespaceProofError('exec_returned')


def _resume_guardian(arguments):
    for number in (0, 1, 2):
        _require(os.readlink(f'/proc/self/fd/{number}') == '/dev/null')
        os.set_inheritable(number, False)
    _require(len(arguments) == 2 and arguments[0] == '--resume-diagnostic'
             and re.fullmatch(r'[1-9][0-9]{0,8}', arguments[1]) is not None)
    number = int(arguments[1])
    _require(number > 2)
    os.set_inheritable(number, False)
    bootstrap = _Descriptor.capture(number)
    seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    _require(fcntl.fcntl(number, fcntl.F_GET_SEALS) == seals)
    encoded = os.pread(number, 8193, 0)
    _require(0 < len(encoded) <= 8192)
    packet = json.loads(encoded)
    required = {
                 'generation', 'supervisor', 'deadline', 'outside_uid', 'outside_gid',
                 'original_namespaces', 'namespaces', 'process', 'images', 'descriptors', 'mask', 'cgroup_required'}
    _require(json.dumps(packet, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() == encoded
             and type(packet) is dict and set(packet) in (required, required | {'runtime', 'runtime_root'}))
    _require(type(packet['cgroup_required']) is bool)
    _require(not packet['cgroup_required'] or 'runtime' in packet)
    _require(type(packet['descriptors']) is list and len(packet['descriptors']) == 3)
    gate, status, parent = [_resume_descriptor(value) for value in packet['descriptors']]
    _require(len({number, gate.number, status.number, parent.number}) == 4)
    bootstrap.close()
    runtime = None
    try:
        supervisor = packet['supervisor']
        _resume_supervisor(parent, supervisor)
        _require(type(packet['generation']) is str and re.fullmatch(r'[0-9a-f]{64}', packet['generation'])
                 and type(packet['deadline']) is float and time.monotonic() < packet['deadline'] <= time.monotonic() + _TIMEOUT)
        from hosted_synthetic_capability import _ENVIRONMENT
        _require(dict(os.environ) == _ENVIRONMENT
                 and set(_read('/proc/self/environ', 128).split(b'\0')[:-1])
                 == {f'{key}={value}'.encode() for key, value in _ENVIRONMENT.items()}
                 and len(os.listdir('/proc/self/task')) == 1)
        expected = {0, 1, 2, gate.number, status.number, parent.number}
        if 'runtime' in packet:
            from hosted_synthetic_runtime import receive_runtime, verify_consumer
            runtime = receive_runtime(packet['runtime'])
            _require(runtime.generation == packet['generation'] and runtime.bundle.descriptor not in expected)
            expected.add(runtime.bundle.descriptor)
        for name in os.listdir('/proc/self/fd'):
            try:
                os.fstat(int(name))
            except OSError:
                continue
            _require(int(name) in expected)
        _resume_signal_state(packet['mask'])
        _require_mapped_identity()
        current = _process(os.getpid())
        _require(current == packet['process'] and current['parent'] == supervisor
                 and current['group'] == current['session'] == os.getpid()
                 and (os.geteuid(), os.getegid(), os.getgroups()) == (0, 0, []))
        _resume_namespaces(packet['original_namespaces'], packet['namespaces'])
        parse_id_map(_read('/proc/self/uid_map', 128), packet['outside_uid'])
        parse_id_map(_read('/proc/self/gid_map', 128), packet['outside_gid'])
        _require(list(_identity(os.stat('/proc/self/exe'))) == packet['images']['executable'])
        _require(_image_is_mapped(packet['images']['loader']))
        runtime_proof = None
        if runtime is not None:
            runtime_proof = verify_consumer(runtime, packet['runtime_root'])
            runtime.close()
            runtime = None
        _resume_supervisor(parent, supervisor)
        _mapped_guardian(gate, status, parent, packet['generation'], supervisor, packet['deadline'],
                         packet['outside_uid'], packet['outside_gid'], packet['original_namespaces'],
                         packet['cgroup_required'], **({'runtime_proof': runtime_proof} if runtime_proof else {}))
        return 0
    except BaseException as exc:
        _child_failure(status, packet['generation'], packet['deadline'], exc)
        return 1
    finally:
        if runtime is not None:
            runtime.close()


def _mount_proc():
    """FD-based mount with required attributes; fallback only at fsmount."""
    filesystem = mount = None
    try:
        filesystem = _acquire(lambda: backend._browser_syscall(430, ctypes.c_char_p(b"proc"), ctypes.c_uint(1)))
        backend._browser_syscall(431, ctypes.c_int(filesystem.number), ctypes.c_uint(6),
                                 ctypes.c_void_p(0), ctypes.c_void_p(0), ctypes.c_int(0))
        try:
            raw = backend._browser_syscall(432, ctypes.c_int(filesystem.number),
                                          ctypes.c_uint(1), ctypes.c_uint(2 | 4 | 8))
        except OSError as exc:
            failed_errno = exc.errno
            backend._browser_mount(b"proc", b"/proc", b"proc", 2 | 4 | 8, None)
            return {"method": "legacy", "fsmount_errno": failed_errno}
        mount = _acquire(lambda: raw)
        backend._browser_syscall(429, ctypes.c_int(mount.number), ctypes.c_char_p(b""),
                                 ctypes.c_int(-100), ctypes.c_char_p(b"/proc"), ctypes.c_uint(4))
        return {"method": "fd", "fsmount_errno": 0}
    finally:
        errors = []
        for descriptor in (mount, filesystem):
            if descriptor is not None:
                try:
                    descriptor.close()
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            raise NamespaceCleanupError("mount_cleanup_refused") from errors[0]


def _kill_unreaped_child(pid, descriptor):
    """Direct-child PID ownership survives a failed pidfd acquisition/check."""
    errors = []
    try:
        if descriptor is not None:
            descriptor.verify()
            signal.pidfd_send_signal(descriptor.number, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except BaseException as exc:
        errors.append(exc)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except BaseException as exc:
            errors.append(exc)
    try:
        backend._wait_browser_child_bounded(pid, deadline=time.monotonic() + 5.0)
    except BaseException as exc:
        errors.append(exc)
    if errors:
        raise NamespaceCleanupError("child_cleanup_refused") from errors[0]


def _private_evidence(outside_uid, outside_gid, original_namespaces, root, *, runtime_proof=None, filter_count=None):
    root.verify()
    _require(root.identity == _identity(os.stat("/proc")))
    members = sorted(int(path.name) for path in Path("/proc").iterdir() if path.name.isdecimal())
    _require(members == [1, 2])
    records = []
    for pid, parent, state in ((1, 0, "R"), (2, 1, "T")):
        process = _process(pid)
        status = parse_status(_read(f"/proc/{pid}/status", 16384))
        _require(process["parent"] == parent and process["state"] == state)
        _require(status == {"pid": pid, "parent": parent, "nspid": [pid],
                            "uid": [0]*4, "gid": [0]*4, "groups": [], "no_new_privs": 1})
        records.append({"process": process, "status": status, "namespaces": _namespaces(str(pid))})
    namespaces = records[0]["namespaces"]
    _require(namespaces == records[1]["namespaces"]
             and all(namespaces[name] != original_namespaces[name] for name in _NAMESPACES))
    _require(sorted(os.listdir("/proc/2/fd")) == ["0", "1", "2"])
    _require(all(os.readlink(f"/proc/2/fd/{number}") == "/dev/null" for number in (0, 1, 2)))
    evidence = dict(members=members, records=records,
                uid_map=parse_id_map(_read("/proc/self/uid_map", 128), outside_uid),
                gid_map=parse_id_map(_read("/proc/self/gid_map", 128), outside_gid),
                mount=parse_private_proc_mount(_read("/proc/self/mountinfo"), _mount_id(root)),
                proc_identity=list(_identity(os.stat("/proc"))))
    if runtime_proof is not None:
        from hosted_synthetic_worker import attest_worker
        evidence.update(runtime=runtime_proof,
                        worker_authority=attest_worker(2, runtime_proof['root_identity'], filter_count))
    return evidence


def _init(gate, status, guardian_pidfd, generation, deadline, outside_uid, outside_gid, original_namespaces, cgroup_required=False, *, runtime_proof=None):
    # PID1 death tears down all nested children; guardian death is bound by pidfd.
    backend._browser_prctl(1, int(signal.SIGKILL))
    _require((os.getpid(), os.getppid()) == (1, 0)
             and not backend._browser_pidfd_is_terminal(guardian_pidfd.number))
    os.unshare(os.CLONE_NEWNS)
    backend._browser_mount(None, b"/", None, (1 << 18) | 16384, None)
    backend._close_browser_guardian_descriptors(frozenset((0, 1, 2, gate.number, status.number, guardian_pidfd.number)))
    _send(status, generation, "ARMED", deadline)
    # PID-namespace init does not honor a self-generated SIGSTOP. Stay blocked
    # on this generation-bound pipe while the ancestor guardian stops us via
    # its retained pidfd. Only a verified enrollment ACK lets G send PROBE.
    _receive(gate, generation, "PROBE", deadline)
    _require(not backend._browser_pidfd_is_terminal(guardian_pidfd.number))
    guardian_pidfd.close()
    before = _identity(os.stat("/proc"))
    method = _mount_proc()
    root = _acquire(lambda: os.open("/proc", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW))
    _require(root.identity != before and backend._browser_fstatfs_magic(root.number) == 0x9FA0)
    if runtime_proof is None:
        from hosted_synthetic_projection import observe_projection
        projection = observe_projection(generation)
        worker_options = {}
    else:
        from hosted_synthetic_worker import _status, restrict_worker
        from hosted_synthetic_runtime import SENTINEL
        _require(runtime_proof['consumer_exec_verified'] is True and runtime_proof['generation'] == generation)
        selected = _acquire(lambda: os.open('/', os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW))
        try:
            _require(list(selected.identity) == runtime_proof['root_identity'])
            worker_root, worker_mount = selected.identity, _mount_id(selected)
        finally: selected.close()
        worker_options = {'runtime_proof': runtime_proof, 'filter_count': _status()['Seccomp_filters'] + 1}
        projection = {'scope': 'HOSTED_DIAGNOSTIC_RUNTIME', 'sealed_root': True,
                      'generation': generation, 'execution_authority': False}
    child = os.fork()
    if child == 0:
        try:
            _hardening(1)
            backend._close_browser_guardian_descriptors(frozenset((0, 1, 2)))
            if runtime_proof is None:
                os.kill(os.getpid(), signal.SIGSTOP)
            else:
                restrict_worker(worker_root, worker_mount)
                os.execve(SENTINEL, [SENTINEL], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C.UTF-8'})
        finally:
            os._exit(127)
    _require(child == 2)
    _wait(child, deadline, stopped=True)
    child_fd = _pidfd(child)
    evidence = _private_evidence(outside_uid, outside_gid, original_namespaces, root, **worker_options)
    evidence["method"] = method
    evidence["projection"] = projection
    _send(status, generation, "PRIVATE_PROC", deadline, evidence=evidence)
    # This bounded wait retains the namespace and its mount during outer proof.
    _receive(gate, generation, "CLOSE", deadline)
    repeated = _private_evidence(outside_uid, outside_gid, original_namespaces, root, **worker_options)
    _require(repeated == {key: value for key, value in evidence.items() if key not in {"method", "projection"}})
    try:
        child_fd.verify()
        signal.pidfd_send_signal(child_fd.number, signal.SIGKILL)
        wait_status = backend._wait_browser_child_bounded(child, deadline=deadline)
        _require(os.WIFSIGNALED(wait_status) and os.WTERMSIG(wait_status) == signal.SIGKILL)
        child_fd.close()
        root.verify()
        _require(root.identity == _identity(os.stat("/proc")))
        _require(parse_private_proc_mount(_read("/proc/self/mountinfo"), _mount_id(root)) == evidence["mount"])
        backend._browser_unmount(b"/proc", 2)
        root.close()
        _require(_identity(os.stat("/proc")) == before)
    except BaseException as exc:
        raise NamespaceCleanupError('private_proc_cleanup_refused') from exc
    _send(status, generation, "CLEAN", deadline)


def _start_init(own, parent, inner_read, inner_write, result_read, result_write,
                generation, supervisor, deadline, outside_uid, outside_gid, original_namespaces, cgroup_required=False, *, runtime_proof=None):
    child = -1
    init_fd = None
    try:
        backend._browser_prctl(1, 0)
        _require(os.getppid() == supervisor and not backend._browser_pidfd_is_terminal(parent.number))
        child = os.fork()
        if child == 0:
            try:
                _init(inner_read, result_write, own, generation, deadline, outside_uid, outside_gid, original_namespaces, cgroup_required,
                      **({'runtime_proof': runtime_proof} if runtime_proof is not None else {}))
                os._exit(0)
            except BaseException as exc:
                _child_failure(result_write, generation, deadline, exc)
                os._exit(1)
        inner_read.close()
        result_write.close()
        init_fd = _pidfd(child)
        parent.verify()
        parent_info = os.fstat(parent.number)
        backend._read_browser_guardian_armed_and_rearm(
            result_read.number, parent.number, expected_parent=supervisor,
            expected_pidfd_device=parent_info.st_dev, expected_pidfd_inode=parent_info.st_ino,
            generation=generation, deadline=deadline)
    except BaseException as error:
        cleanup_errors = []
        if child > 1:
            try:
                _kill_unreaped_child(child, init_fd)
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            backend._browser_prctl(1, int(signal.SIGKILL))
        except BaseException as exc:
            cleanup_errors.append(exc)
        if init_fd is not None:
            try:
                init_fd.close()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise NamespaceCleanupError("init_start_cleanup_refused") from error
        raise
    return child, init_fd


def _guardian(gate, status, generation, supervisor, deadline, outside_uid, outside_gid, original_namespaces, cgroup_required=False, *, runtime=None):
    backend._block_browser_projection_signals()
    _hardening(supervisor)
    os.setsid()
    os.environ.clear()
    backend._normalize_browser_guardian_stdio()
    backend._close_browser_guardian_descriptors(frozenset((0, 1, 2, gate.number, status.number,
                                                         *((runtime.bundle.descriptor,) if runtime else ()))))
    _send(status, generation, "STOPPED", deadline)
    os.kill(os.getpid(), signal.SIGSTOP)
    _receive(gate, generation, "START", deadline)
    backend._browser_prctl(4, 1)
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNS | os.CLONE_NEWPID | os.CLONE_NEWNET)
    _send(status, generation, "USERNS", deadline)
    _receive(gate, generation, "MAPPED", deadline)
    _require_mapped_identity()
    backend._browser_prctl(4, 0)
    parent = _pidfd(supervisor)
    _reexec_guardian(gate, status, parent, generation, supervisor, deadline,
                     outside_uid, outside_gid, original_namespaces, cgroup_required,
                     **({'runtime': runtime} if runtime is not None else {}))


def _guardian_enrollment(gate, status, child, init_fd, generation, deadline):
    # Both G and I stop before S enrolls I; G retains its unreaped child.
    init_fd.verify()
    _require(not backend._browser_pidfd_is_terminal(init_fd.number))
    signal.pidfd_send_signal(init_fd.number, signal.SIGSTOP)
    _wait(child, deadline, stopped=True)
    init_fd.verify()
    process = _process(child)
    _require(process['state'] == 'T' and process['parent'] == os.getpid()
             and process['group'] == os.getpid()
             and not backend._browser_pidfd_is_terminal(init_fd.number))
    _send(status, generation, 'ENROLL_INIT', deadline, pid=child, start=process['start'])
    os.kill(os.getpid(), signal.SIGSTOP)
    ack = _receive(gate, generation, 'ENROLLED', deadline, fields=('pid', 'start', 'path'))
    current = backend.parse_browser_cgroup_path(_read('/proc/self/cgroup', 4096))
    _require(current.endswith('/supervisor') and ack['pid'] == child and ack['start'] == process['start']
             and ack['path'] == str(Path(current).parent / ('buffalo-' + generation))
             and backend.parse_browser_cgroup_path(_read(f'/proc/{child}/cgroup', 4096)) == ack['path'])
    init_fd.verify()
    _require(_process(child) == process and not backend._browser_pidfd_is_terminal(init_fd.number))
    signal.pidfd_send_signal(init_fd.number, signal.SIGCONT)


def _supervisor_enrollment(lease, gate, status, guardian_fd, guardian, guardian_start,
                          generation, deadline, owners):
    request = _receive(status, generation, 'ENROLL_INIT', deadline, fields=('pid', 'start'))
    pid, start = request['pid'], request['start']
    _require(type(pid) is int and 1 < pid <= 2**31 - 1 and type(start) is int and 0 < start < 2**64
             and pid not in (os.getpid(), guardian))
    _wait(guardian, deadline, stopped=True)
    init_fd = _pidfd(pid)
    owners.append(init_fd)
    _require(_process(guardian)['state'] == 'T')
    lease.enroll_stopped_init(pid=pid, pidfd=init_fd, start=start, guardian=guardian,
                             guardian_pidfd=guardian_fd, guardian_start=guardian_start,
                             supervisor=os.getpid())
    _send(gate, generation, 'ENROLLED', deadline, pid=pid, start=start, path=lease.path)
    guardian_fd.verify()
    signal.pidfd_send_signal(guardian_fd.number, signal.SIGCONT)
    return dict(pid=pid, start=start, pidfd=init_fd)


def _attest_cgroup_pair(lease, outer, enrolled, guardian_fd, guardian, guardian_start, owners):
    _require(type(outer) is dict and set(outer) == {'init_pid', 'init_start', 'worker_pid', 'worker_start'}
             and all(type(value) is int and value > 0 for value in outer.values())
             and outer['init_pid'] == enrolled['pid'] and outer['init_start'] == enrolled['start']
             and len({os.getpid(), guardian, outer['init_pid'], outer['worker_pid']}) == 4)
    worker_fd = _pidfd(outer['worker_pid'])
    owners.append(worker_fd)

    def verify():
        for descriptor in (guardian_fd, enrolled['pidfd'], worker_fd):
            descriptor.verify()
            _require(not backend._browser_pidfd_is_terminal(descriptor.number))
        g = _process(guardian)
        _require(g['start'] == guardian_start and g['parent'] == os.getpid()
                 and g['group'] == g['session'] == guardian)
        for name, parent, namespace_pid in (('init', guardian, 1), ('worker', outer['init_pid'], 2)):
            pid = outer[name + '_pid']
            process = _process(pid)
            _require(process['start'] == outer[name + '_start'] and process['parent'] == parent
                     and process['group'] == process['session'] == guardian
                     and (process['state'] == 'T' if name == 'worker' else process['state'] in ('R', 'S')))
            _require(parse_status(_read(f'/proc/{pid}/status', 16384))['nspid'] == [pid, namespace_pid]
                     and sorted(os.listdir(f'/proc/{pid}/task')) == [str(pid)]
                     and backend.parse_browser_cgroup_path(_read(f'/proc/{pid}/cgroup', 4096)) == lease.path)
        for pid in (os.getpid(), guardian):
            path = backend.parse_browser_cgroup_path(_read(f'/proc/{pid}/cgroup', 4096))
            _require(path != lease.path and not path.startswith(lease.path + '/'))

    verify()
    evidence = lease.attest((outer['init_pid'], outer['worker_pid']))
    verify()
    return evidence


def _mapped_guardian(gate, status, parent, generation, supervisor, deadline,
                     outside_uid, outside_gid, original_namespaces, cgroup_required=False, *, runtime_proof=None):
    backend._browser_mount(None, b"/", None, (1 << 18) | 16384, None)
    own = _pidfd(os.getpid())
    inner_read, inner_write = _pipe()
    result_read, result_write = _pipe()
    child, init_fd = _start_init(own, parent, inner_read, inner_write, result_read, result_write,
                                 generation, supervisor, deadline, outside_uid, outside_gid, original_namespaces, cgroup_required,
                                 **({'runtime_proof': runtime_proof} if runtime_proof is not None else {}))
    if cgroup_required:
        _guardian_enrollment(gate, status, child, init_fd, generation, deadline)
    parent.close()
    own.close()
    _send(inner_write, generation, "PROBE", deadline)
    evidence = _receive(result_read, generation, "PRIVATE_PROC", deadline, fields=("evidence",))["evidence"]
    _require(type(evidence) is dict and set(evidence) == {"members", "records", "uid_map", "gid_map", "mount", "proc_identity", "method", "projection"}
             | ({'runtime', 'worker_authority'} if runtime_proof is not None else set()))
    if runtime_proof is not None:
        _require(evidence['runtime'] == runtime_proof)
    process = _process(child)
    init_start = process["start"]
    _require(process["parent"] == os.getpid() and process["group"] == os.getpid())
    _require(parse_status(_read(f"/proc/{child}/status", 16384))["nspid"] == [child, 1])
    # G has only its newly created user namespace's privileges. Failure to read
    # these kernel objects refuses proof; outer S is never made ptrace-capable.
    _require(_namespaces(str(child)) == evidence["records"][0]["namespaces"])
    _require(parse_private_proc_mount(_read(f"/proc/{child}/mountinfo"), evidence["mount"]["mount_id"]) == evidence["mount"])
    _require(list(_identity(os.stat(f"/proc/{child}/root/proc"))) == evidence["proc_identity"])
    guardian_namespaces = _namespaces("self")
    init_namespaces = evidence["records"][0]["namespaces"]
    _require(guardian_namespaces["user"] == init_namespaces["user"]
             and guardian_namespaces["net"] == init_namespaces["net"]
             and guardian_namespaces["mnt"] not in (original_namespaces["mnt"], init_namespaces["mnt"])
             and guardian_namespaces["pid"] == original_namespaces["pid"]
             and list(_identity(os.stat("/proc/self/ns/pid_for_children"))) == init_namespaces["pid"])
    candidates = list(Path("/proc").iterdir())
    _require(len(candidates) <= 65536)
    children = []
    for path in candidates:
        if not path.name.isdecimal():
            continue
        try:
            observed = _process(int(path.name))
        except FileNotFoundError:
            continue
        if observed["parent"] == child:
            children.append(observed)
    _require(len(children) == 1)
    worker = children[0]
    _require(worker["state"] == "T" and worker["group"] == os.getpid()
             and worker["start"] == evidence["records"][1]["process"]["start"])
    worker_fd = _pidfd(worker["pid"])
    _require(parse_status(_read(f"/proc/{worker['pid']}/status", 16384))["nspid"] == [worker["pid"], 2]
             and _namespaces(str(worker["pid"])) == init_namespaces
             and _process(worker["pid"]) == worker)
    if runtime_proof is not None:
        from hosted_synthetic_worker import attest_worker, _status
        _require(evidence['worker_authority'] == attest_worker(worker['pid'], runtime_proof['root_identity'],
                                                              _status()['Seccomp_filters'] + 1))
    outer = dict(init_pid=child, init_start=init_start, worker_pid=worker['pid'], worker_start=worker['start'])
    _send(status, generation, "RETAINED", deadline, evidence=evidence,
          **({'outer': outer} if cgroup_required else {}))
    _receive(gate, generation, "CLOSE", deadline)
    _require(_process(child)["start"] == init_start and not backend._browser_pidfd_is_terminal(init_fd.number))
    _finish_private_probe(inner_write, result_read, worker_fd, init_fd, child, generation, deadline)
    _send(status, generation, "CLEAN", deadline)


def _finish_private_probe(inner_write, result_read, worker_fd, init_fd, child, generation, deadline):
    errors = []
    try:
        _send(inner_write, generation, 'CLOSE', deadline)
        _receive(result_read, generation, 'CLEAN', deadline)
        _wait(child, deadline)
        worker_fd.verify()
        _require(backend._browser_pidfd_is_terminal(worker_fd.number))
    except BaseException as exc:
        errors.append(exc)
    for descriptor in (worker_fd, init_fd, inner_write, result_read):
        try:
            descriptor.close()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise NamespaceCleanupError('guardian_cleanup_refused') from errors[0]


def observe_private_proc(generation: str, *, delegation: Path | None = None, source_binding=None) -> dict:
    """Run in a fresh scrubbed single-thread supervisor; return no authority."""
    from hosted_synthetic_capability import _require_clean_supervisor
    _require_clean_supervisor()
    from hosted_synthetic_launcher import inspect_host_identity
    inspect_host_identity()
    _require(type(generation) is str and re.fullmatch(r"[0-9a-f]{64}", generation) is not None
             and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL)
    from hosted_synthetic_cgroup import CgroupLease, CgroupCleanupError
    if delegation is not None:
        current = backend.parse_browser_cgroup_path(_read('/proc/self/cgroup', 4096))
        _require(type(delegation) is Path or isinstance(delegation, Path))
        _require(current.endswith('/supervisor') and delegation == Path('/sys/fs/cgroup' + current).parent)
    original_namespaces = _namespaces("self")
    previous_subreaper = ctypes.c_int()
    _require(backend._browser_libc().prctl(37, ctypes.byref(previous_subreaper), 0, 0, 0) == 0)
    previous_mask = backend._block_browser_projection_signals()
    descriptors = []
    process = -1
    process_start = None
    child_fd = None
    reaped = False
    evidence = None
    cgroup = None
    cgroup_evidence = None
    resource_cleanup = None
    enrolled = None
    runtime = None
    process_descriptors = []
    errors = []
    reason = "NAMESPACE_PROOF_REFUSED"
    refusal_location = None
    deadline = time.monotonic() + _TIMEOUT
    try:
        try:
            backend._browser_prctl(36, 1)
            if delegation is not None:
                from hosted_synthetic_runtime import capture_runtime
                runtime = capture_runtime(generation, Path(__file__).resolve().parents[2], source_binding)
                cgroup = CgroupLease.create(delegation, generation=generation, memory_max=256 * 1024**2)
            for _ in range(2):
                descriptors.extend(_pipe())
            gate_read, gate_write, status_read, status_write = descriptors
            parent = os.getpid()
            outside_uid, outside_gid = os.geteuid(), os.getegid()
            process = os.fork()
            if process == 0:
                try:
                    backend._restore_browser_projection_signals(previous_mask)
                    _guardian(gate_read, status_write, generation, parent, deadline,
                              outside_uid, outside_gid, original_namespaces, cgroup is not None,
                              **({'runtime': runtime} if runtime is not None else {}))
                    os._exit(0)
                except BaseException as exc:
                    _child_failure(status_write, generation, deadline, exc)
                    os._exit(1)
            gate_read.close()
            status_write.close()
            child_fd = _pidfd(process)
            _wait(process, deadline, stopped=True)
            observed = _process(process)
            process_start = observed["start"]
            _require(observed["parent"] == parent and observed["group"] == process and observed["session"] == process)
            _receive(status_read, generation, "STOPPED", deadline)
            _send(gate_write, generation, "START", deadline)
            child_fd.verify()
            signal.pidfd_send_signal(child_fd.number, signal.SIGCONT)
            _receive(status_read, generation, "USERNS", deadline)
            backend._write_browser_user_namespace_maps(process, pidfd=child_fd.number, expected_start_ticks=process_start)
            _send(gate_write, generation, "MAPPED", deadline)
            if cgroup is not None:
                enrolled = _supervisor_enrollment(cgroup, gate_write, status_read, child_fd,
                                                  process, process_start, generation, deadline, process_descriptors)
            retained = _receive(status_read, generation, "RETAINED", deadline,
                                fields=("evidence", "outer") if cgroup is not None else ("evidence",))
            evidence = retained['evidence']
            if runtime is not None:
                from hosted_synthetic_runtime import verify_report_binding
                verify_report_binding(runtime, evidence.get('runtime'))
            if cgroup is not None:
                cgroup_evidence = _attest_cgroup_pair(cgroup, retained['outer'], enrolled,
                                                     child_fd, process, process_start, process_descriptors)
            _send(gate_write, generation, "CLOSE", deadline)
            _receive(status_read, generation, "CLEAN", deadline)
            _wait(process, deadline)
            reaped = True
            reason = "INCOMPLETE_PREFLIGHT"
        except BaseException as exc:
            reason = "CANCELLED" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "NAMESPACE_PROOF_REFUSED"
            refusal_location = _failure_location(exc)
            if isinstance(exc, (NamespaceCleanupError, CgroupCleanupError)):
                errors.append(exc)
        # Keep the direct child unreaped until its group has been signaled on
        # failure; this anchors group identity and prevents numeric PID reuse.
        if process > 1 and not reaped:
            try:
                if process_start is not None:
                    observed = _process(process)
                    _require(observed["start"] == process_start and observed["group"] == process)
                    os.killpg(process, signal.SIGKILL)
            except BaseException as exc:
                errors.append(exc)
            try:
                if child_fd is not None:
                    child_fd.verify()
                    signal.pidfd_send_signal(child_fd.number, signal.SIGKILL)
                else:
                    # An unreaped, direct fork child cannot have its PID reused.
                    os.kill(process, signal.SIGKILL)
            except BaseException as exc:
                errors.append(exc)
                try:
                    os.kill(process, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as exc:
                    errors.append(exc)
            try:
                backend._wait_browser_child_bounded(process, deadline=time.monotonic() + 5.0)
                reaped = True
            except BaseException as exc:
                errors.append(exc)
        if process > 1 and reaped:
            try:
                cleanup_deadline = time.monotonic() + 5.0
                while True:
                    try:
                        selected, _ = os.waitpid(-process, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if selected == 0:
                        _require(time.monotonic() < cleanup_deadline)
                        time.sleep(0.005)
            except BaseException as exc:
                errors.append(exc)
        for descriptor in process_descriptors:
            try:
                descriptor.verify()
                _require(backend._browser_pidfd_is_terminal(descriptor.number))
            except BaseException as exc:
                errors.append(exc)
        if cgroup is not None:
            try:
                cgroup.close()
                resource_cleanup = cgroup.final_resources
                _require(type(resource_cleanup) is dict)
            except BaseException as exc:
                errors.append(exc)
        for descriptor in [*reversed(process_descriptors), child_fd, *reversed(descriptors)]:
            if descriptor is not None:
                try:
                    descriptor.close()
                except BaseException as exc:
                    errors.append(exc)
        if runtime is not None:
            try:
                runtime.close()
            except BaseException as exc:
                errors.append(exc)
        try:
            backend._browser_prctl(36, previous_subreaper.value)
        except BaseException as exc:
            errors.append(exc)
        try:
            _require_clean_supervisor()
        except BaseException as exc:
            errors.append(exc)
    finally:
        try:
            backend._restore_browser_projection_signals(previous_mask)
        except (KeyboardInterrupt, SystemExit):
            reason = "CANCELLED"
        except BaseException as exc:
            errors.append(exc)
    if errors or (process > 1 and not reaped):
        reason = "CLEANUP_FAILED"
    return dict(reason=reason, cleanup_complete=not errors and (process < 0 or reaped),
                refusal_location=refusal_location, cgroup_evidence=cgroup_evidence,
                resource_cleanup=resource_cleanup,
                cgroup='COMPONENT_OBSERVATION_ONLY' if cgroup_evidence is not None else 'NOT_RUN',
                runtime="HOST_RUNTIME_UNSEALED",
                private_proc=evidence, HOST_CAPABILITY="FAIL", provider_calls=0,
                payload_processes=0, credential_release="WITHHELD", execution_authority=False)


if __name__ == '__main__':
    try:
        os._exit(_resume_guardian(sys.argv[1:]))
    except BaseException:
        os._exit(1)
