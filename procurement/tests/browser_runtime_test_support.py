"""Disposable exact-byte fixtures; no production runtime or execution authority."""
from contextlib import contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch

import run_local_staging_acceptance as acceptance


def run_owned_test_process(arguments, *, output, environment, timeout):
    """Bound one test session; kill its group before reaping its held leader."""
    process = subprocess.Popen(
        arguments, env=environment, stdin=subprocess.DEVNULL,
        stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
    )
    descriptor = -1
    try:
        descriptor = os.pidfd_open(process.pid, 0)
        with selectors.DefaultSelector() as ready:
            ready.register(descriptor, selectors.EVENT_READ)
            if not ready.select(timeout):
                raise TimeoutError("isolated test supervisor deadline exceeded")
    finally:
        try:
            # No poll/wait has reaped this direct child: its PID/session
            # cannot be reused while signaling only this owned group.
            try:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            finally:
                process.wait(timeout=5)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return process.returncode


def _has_direct_children():
    # CONFIG_PROC_CHILDREN is not available on every supported kernel.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rpartition(") ")[2].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if len(fields) < 2:
            raise RuntimeError("process fixture enumeration differs")
        if int(fields[1]) == os.getpid():
            return True
    return False


def _cleanup_isolated_fixture_children():
    """Rescue only children of an initially childless isolated fixture.

    Bind direct-child pidfds before signaling. Signal every verified child
    before waiting for any, so one refusal cannot strand other owned children.
    Ordinary cancellation is deferred through all descriptor cleanup.
    """
    previous=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGQUIT})
    deadline=time.monotonic()+5
    errors=[];refused=set()
    try:
        while _has_direct_children() and time.monotonic()<deadline:
            records=[]
            for entry in Path('/proc').iterdir():
                if not entry.name.isdecimal() or int(entry.name) in refused:continue
                pid=int(entry.name)
                try:before=acceptance._read_browser_worker_proc_stat(pid)
                except acceptance.LocalStagingAcceptanceError:continue
                if before.parent_pid!=os.getpid():continue
                descriptor=-1
                try:
                    descriptor=os.pidfd_open(pid,0)
                    after=acceptance._read_browser_worker_proc_stat(pid)
                    if after.parent_pid!=os.getpid() or after.start_ticks!=before.start_ticks:
                        raise RuntimeError('owned fixture process identity changed')
                    records.append((pid,descriptor));descriptor=-1
                except BaseException as error:
                    errors.append(error);refused.add(pid)
                finally:
                    if descriptor>=0:
                        try:os.close(descriptor)
                        except BaseException as error:errors.append(error)
            try:
                for pid,descriptor in records:
                    try:
                        if not acceptance._browser_pidfd_is_terminal(descriptor):
                            signal.pidfd_send_signal(descriptor,signal.SIGKILL)
                    except BaseException as error:errors.append(error)
                for pid,descriptor in records:
                    try:
                        if not acceptance._browser_pidfd_is_terminal(descriptor):
                            with selectors.DefaultSelector() as ready:
                                ready.register(descriptor,selectors.EVENT_READ)
                                if not ready.select(max(0,deadline-time.monotonic())):
                                    raise RuntimeError('owned fixture child did not exit')
                        waited,_=os.waitpid(pid,os.WNOHANG)
                        if waited!=pid:raise RuntimeError('owned fixture wait identity changed')
                    except BaseException as error:errors.append(error);refused.add(pid)
            finally:
                for pid,descriptor in records:
                    try:os.close(descriptor)
                    except BaseException as error:errors.append(error)
            if not records:break
        if _has_direct_children():errors.append(RuntimeError('owned fixture cleanup incomplete'))
    finally:
        try:signal.pthread_sigmask(signal.SIG_SETMASK,previous)
        except BaseException as error:errors.append(error)
    if errors:raise RuntimeError('owned fixture cleanup failed') from errors[0]


@contextmanager
def owned_subreaper():
    """Let this test supervisor reap its own killed namespace descendants."""
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if _has_direct_children() or libc.prctl(37, ctypes.byref(previous), 0, 0, 0):
        raise RuntimeError("clean subreaper fixture required")
    if libc.prctl(36, 1, 0, 0, 0):
        raise RuntimeError("subreaper fixture refused")
    try:
        observed = ctypes.c_int()
        if libc.prctl(37, ctypes.byref(observed), 0, 0, 0) or observed.value != 1:
            raise RuntimeError("subreaper fixture differs")
        yield
    finally:
        try:
            if _has_direct_children():
                # Rescue even a failed PID handshake without letting fixture
                # children escape to the platform's non-reaping PID1. The
                # missing primary cleanup remains a test failure after rescue.
                _cleanup_isolated_fixture_children()
                raise RuntimeError("owned subreaper fixture children remain")
        finally:
            restored = ctypes.c_int()
            if (libc.prctl(36, previous.value, 0, 0, 0)
                    or libc.prctl(37, ctypes.byref(restored), 0, 0, 0)
                    or restored.value != previous.value):
                raise RuntimeError("subreaper fixture restoration refused")


def _manifest(domain, records):
    """Independent fixture encoding from declared bytes, not observed output."""
    digest = hashlib.sha256(domain)
    for kind, path, mode, content in sorted(records, key=lambda row: row[1]):
        path = os.fsencode(path)
        payload = hashlib.sha256(content).digest() if kind == "F" else content
        digest.update(kind.encode())
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(mode.to_bytes(4, "big"))
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(len(payload).to_bytes(4, "big"))
        digest.update(payload)
    return digest.hexdigest()


@contextmanager
def current_python_file_expectation():
    """Exercise descriptor/process validation against this exact test interpreter.

    This is deliberately not a runtime-closure or hosted-acceptance claim.
    Keep the interpreter in place: copies may lose relative shared-library lookup.
    """
    path = Path(sys.executable).resolve(strict=True)
    metadata = path.stat()
    contents = path.read_bytes()
    with patch.multiple(
        acceptance,
        _BROWSER_PYTHON_EXECUTABLE=path,
        _BROWSER_PYTHON_BYTES=len(contents),
        _BROWSER_PYTHON_SHA256=hashlib.sha256(contents).hexdigest(),
        _BROWSER_PYTHON_UID=metadata.st_uid,
        _BROWSER_PYTHON_GID=metadata.st_gid,
        _BROWSER_PYTHON_MODE=stat.S_IMODE(metadata.st_mode),
    ):
        yield


@contextmanager
def synthetic_python_closure():
    """Tiny startup/stdlib/native/gconv/locale trees; patch expectations only."""
    with TemporaryDirectory(prefix="buffalo-runtime-fixture-") as temporary:
        root = Path(temporary)
        uid, gid = os.geteuid(), os.getegid()
        python_bytes = b"synthetic Python bytes; never executed\n"
        config_bytes = b"# fabricated sysconfig, no executable workload\n"
        startup = (
            ("F", str(root / "python3.13"), 0o555, python_bytes),
            ("F", str(root / "loader"), 0o555, b"synthetic loader\n"),
            ("L", str(root / "loader-link"), 0o777, os.fsencode(root / "loader")),
        )
        stdlib = root / "stdlib"
        config_name = "_sysconfigdata__linux_x86_64-linux-gnu.py"
        standard = (
            ("F", config_name, 0o444, config_bytes),
            ("D", "site-packages", 0o555, b""),
            ("L", "site-packages/" + config_name, 0o777, ("../" + config_name).encode()),
        )
        gconv, locale = root / "gconv", root / "locale"
        gconv_tree = (("F", "gconv-modules", 0o444, b"gconv"),)
        locale_tree = (("F", "LC_CTYPE", 0o444, b"locale"),)
        native_files = (("F", str(root / "native.so"), 0o444, b"synthetic native"),)

        def materialize(records, parent=None):
            for kind, name, mode, contents in sorted(records, key=lambda row: row[1]):
                path = parent / name if parent else Path(name)
                if kind == "D":
                    path.mkdir(mode=0o755)
                elif kind == "L":
                    path.symlink_to(os.fsdecode(contents))
                else:
                    path.write_bytes(contents)
                    path.chmod(mode)
            for kind, name, mode, _ in records:
                if kind == "D":
                    (parent / name if parent else Path(name)).chmod(mode)

        materialize(startup)
        materialize(native_files)
        for directory, records in ((stdlib, standard), (gconv, gconv_tree), (locale, locale_tree)):
            directory.mkdir(mode=0o755)
            materialize(records, directory)
            directory.chmod(0o555)
        native = native_files + (
            ("D", str(gconv), 0o555, b""),
            ("F", str(gconv / "gconv-modules"), 0o444, b"gconv"),
            ("D", str(locale), 0o555, b""),
            ("F", str(locale / "LC_CTYPE"), 0o444, b"locale"),
        )

        def expectations(records):
            return tuple(acceptance._BrowserRuntimeFileExpectation(
                Path(name), kind, len(content), mode,
                hashlib.sha256(content).hexdigest() if kind == "F" else os.fsdecode(content),
                uid, gid,
            ) for kind, name, mode, content in records)

        values = {
            "_BROWSER_PYTHON_EXECUTABLE": root / "python3.13",
            "_BROWSER_PYTHON_BYTES": len(python_bytes),
            "_BROWSER_PYTHON_SHA256": hashlib.sha256(python_bytes).hexdigest(),
            "_BROWSER_PYTHON_UID": uid, "_BROWSER_PYTHON_GID": gid,
            "_BROWSER_PYTHON_MODE": 0o555,
            "_BROWSER_RUNTIME_STARTUP_FILES": expectations(startup),
            "_BROWSER_RUNTIME_STARTUP_SHA256": _manifest(acceptance._BROWSER_RUNTIME_STARTUP_DOMAIN, startup),
            "_BROWSER_RUNTIME_STDLIB_ROOT": stdlib,
            "_BROWSER_RUNTIME_STDLIB_ZIP": root / "python.zip",
            "_BROWSER_RUNTIME_STDLIB_SHA256": _manifest(acceptance._BROWSER_RUNTIME_STDLIB_DOMAIN, standard),
            "_BROWSER_RUNTIME_STDLIB_ENTRIES": 3, "_BROWSER_RUNTIME_STDLIB_FILES": 1,
            "_BROWSER_RUNTIME_STDLIB_DIRECTORIES": 1, "_BROWSER_RUNTIME_STDLIB_SYMLINKS": 1,
            "_BROWSER_RUNTIME_STDLIB_BYTES": len(config_bytes),
            "_BROWSER_RUNTIME_NATIVE_FILES": expectations(native_files),
            "_BROWSER_RUNTIME_NATIVE_SHA256": _manifest(acceptance._BROWSER_RUNTIME_NATIVE_DOMAIN, native),
            "_BROWSER_RUNTIME_NATIVE_ENTRIES": 5, "_BROWSER_RUNTIME_NATIVE_REGULAR_FILES": 3,
            "_BROWSER_RUNTIME_NATIVE_DIRECTORIES": 2, "_BROWSER_RUNTIME_NATIVE_SYMLINKS": 0,
            "_BROWSER_RUNTIME_NATIVE_BYTES": sum(len(row[3]) for row in native if row[0] == "F"),
        }
        for prefix, directory, records, absent in (
            ("GCONV", gconv, gconv_tree, gconv / "gconv-modules.cache"),
            ("LOCALE", locale, locale_tree, root / "locale-archive"),
        ):
            key = "_BROWSER_RUNTIME_" + prefix
            values.update({key + "_ROOT": directory, key + "_SHA256": _manifest(
                getattr(acceptance, key + "_DOMAIN"), records), key + "_ENTRIES": 1,
                key + "_FILES": 1, key + "_DIRECTORIES": 0,
                key + "_BYTES": sum(len(row[3]) for row in records),
                key + ("_CACHE" if prefix == "GCONV" else "_ARCHIVE"): absent})
        with patch.multiple(acceptance, **values):
            yield values


# This child-only fixture establishes real kernel flags for process observation.
# It is NOT the accepted worker filter or a complete containment policy.
# Reject foreign architectures/x32, process creation, exec and socket creation;
# prove getppid is denied before announcing readiness. No parent state changes.
DIRECT_PYTHON_CHILD_HARDENING = '''
import ctypes, errno, platform
if platform.machine() != 'x86_64':
    raise RuntimeError('x86_64 process fixture required')
class F(ctypes.Structure):
    _fields_ = [('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint32)]
class P(ctypes.Structure):
    _fields_ = [('len',ctypes.c_ushort),('filter',ctypes.POINTER(F))]
instructions = [F(0x20,0,0,4), F(0x15,1,0,0xc000003e), F(0x06,0,0,0x80000000),
                F(0x20,0,0,0), F(0x45,0,1,0x40000000), F(0x06,0,0,0x80000000)]
for syscall in (41,53,56,57,58,59,110,322,435):
    instructions.extend((F(0x15,0,1,syscall),F(0x06,0,0,0x00050000|errno.EPERM)))
instructions.append(F(0x06,0,0,0x7fff0000))
filters = (F * len(instructions))(*instructions)
program = P(len(instructions),filters)
libc = ctypes.CDLL(None,use_errno=True)
if libc.prctl(38,1,0,0,0) != 0 or libc.prctl(22,2,ctypes.byref(program),0,0) != 0:
    raise RuntimeError('process fixture hardening refused')
if libc.prctl(39,0,0,0,0) != 1 or libc.prctl(21,0,0,0,0) != 2:
    raise RuntimeError('process fixture kernel flags differ')
ctypes.set_errno(0)
if libc.syscall(110) != -1 or ctypes.get_errno() != errno.EPERM:
    raise RuntimeError('process fixture syscall restriction differs')
'''
