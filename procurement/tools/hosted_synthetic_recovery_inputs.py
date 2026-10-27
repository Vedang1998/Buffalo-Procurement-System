"""Dormant immutable populated-recovery inputs; no DB/process/restore authority.

Uses the existing held-ancestor and descriptor primitives. Inputs must already
come from the reviewed stopped source capture; hashes are not capture provenance.
Only private disposable files are opened. No extraction or pathname deletion.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import tarfile
import threading

import hosted_synthetic_namespace as ns
from hosted_synthetic_source import _held_source_file, HostedSourceError
from hosted_synthetic_runtime import _metadata
from hosted_synthetic_recovery_toc import validate_tocs

LIMITS = {'database.dump': 16*1024**2, 'schema.dump': 8*1024**2, 'storage.tar': 8*1024**2}
_SEALS = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK
_MASK = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT}
_HASH = re.compile('[0-9a-f]{64}\\Z')
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
_PRICE = re.compile(r'price-books/raw/([0-9a-f]{64})\.csv\Z')
_ARTIFACT = re.compile(rf'monday-runs/{_UUID}/(?:vendor-{_UUID}/([0-9a-f]{{64}})\.internal\.csv|packet/([0-9a-f]{{64}})\.review\.zip)\Z')
_TOKEN = object()


class RecoveryInputRefusal(RuntimeError):
    """Fixed diagnostic without source data, credentials or paths."""


class RecoveryInputCleanupError(RecoveryInputRefusal):
    pass


def _require(condition):
    if not condition:
        raise RecoveryInputRefusal('populated_recovery_inputs_refused')


def _signal_boundary():
    _require(threading.current_thread() is threading.main_thread()
             and len(os.listdir('/proc/self/task')) == 1
             and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL)


@dataclass(frozen=True)
class ArchiveSpec:
    name: str
    size: int
    sha256: str

    def validate(self):
        _require(type(self.name) is str and self.name in LIMITS and type(self.size) is int
                 and 0 < self.size <= LIMITS[self.name] and type(self.sha256) is str
                 and _HASH.fullmatch(self.sha256))


@dataclass(frozen=True)
class StorageMember:
    name: str
    data: bytes


def _write_all(fd, data, *, before_write=None):
    offset = 0
    while offset < len(data):
        if before_write is not None: before_write()
        count = os.write(fd, data[offset:])
        _require(count > 0)
        offset += count


def _new_sealed(name, chunks, owners):
    descriptor = ns._acquire(lambda: os.memfd_create('buffalo-recovery-'+name,
                            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING))
    _register_descriptor(descriptor, owners)
    for block in chunks:
        _write_all(descriptor.number, block)
    os.fchmod(descriptor.number, 0o400)
    fcntl.fcntl(descriptor.number, fcntl.F_ADD_SEALS, _SEALS)
    _require(fcntl.fcntl(descriptor.number, fcntl.F_GET_SEALS) == _SEALS)
    return descriptor


def _register_descriptor(descriptor, owners):
    try: owners.append(descriptor)
    except BaseException as error:
        try: descriptor.close()
        except BaseException: raise RecoveryInputCleanupError('recovery_descriptor_registration_cleanup_refused') from error
        raise


def _copy_archive(directory, spec, owners):
    named = os.stat(spec.name, dir_fd=directory, follow_symlinks=False)
    _require(stat.S_ISREG(named.st_mode) and named.st_nlink == 1)
    source = ns._acquire(lambda: os.open(spec.name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW,
                                      dir_fd=directory))
    try:
        before = os.fstat(source.number)
        _require(_metadata(before) == _metadata(named) and stat.S_ISREG(before.st_mode)
                 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                 and (before.st_uid, before.st_gid) == (os.geteuid(), os.getegid())
                 and before.st_size == spec.size)
        digest = hashlib.sha256()
        def chunks():
            offset = 0
            while offset < spec.size:
                block = os.pread(source.number, min(65536, spec.size-offset), offset)
                _require(block)
                offset += len(block); digest.update(block)
                yield block
            _require(not os.pread(source.number, 1, offset))
        held = _new_sealed(spec.name, chunks(), owners)
        _require(digest.hexdigest() == spec.sha256
                 and _metadata(before) == _metadata(os.fstat(source.number))
                 == _metadata(os.stat(spec.name, dir_fd=directory, follow_symlinks=False)))
        if spec.name.endswith('.dump'):
            _require(os.pread(held.number, 11, 0) == b'PGDMP\x01\x0f\x00\x04\x08\x01')
        return held
    finally:
        source.close()


class HeldRecoveryInputs:
    def __init__(self):
        raise RecoveryInputRefusal('use_owned_recovery_context')

    def _verify(self):
        _require(self._token is _TOKEN and self.state == 'HELD')
        for spec, descriptor in zip(self.specs, self.archives, strict=True):
            descriptor.verify()
            info = os.fstat(descriptor.number)
            _require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o400
                     and info.st_size == spec.size and fcntl.fcntl(descriptor.number, fcntl.F_GET_SEALS) == _SEALS)

    def descriptors(self):
        self._verify()
        return tuple(d.number for d in self.archives)

    def read_storage(self):
        self._verify()
        spec, descriptor = self.specs[2], self.archives[2]
        data = os.pread(descriptor.number, spec.size+1, 0)
        _require(len(data) == spec.size and hashlib.sha256(data).hexdigest() == spec.sha256)
        return data

    def bind_tocs(self, captured, full_toc, schema_toc):
        """Bind supplied TOC evidence, without claiming it was emitted by native PG.

        The reviewed caller must emit both TOCs from these exact held descriptors
        with its bound trusted PG16 tool, before any target mutation.
        """
        self._verify()
        _require(self.use_list is None)
        use, observation = validate_tocs(captured, full_toc, schema_toc)
        # This method executes inside the owned context, whose finally closes a
        # partially created handle even if cancellation occurs after allocation.
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, _MASK)
        try:
            self.use_list = _new_sealed('use-list', (use,), self._owners)
            self.toc_observation = observation
            self.use_list.verify()
        except BaseException:
            self.state = 'INPUTS_REFUSED'
            raise
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        return {**observation, 'archives': [dict(name=s.name, bytes=s.size, sha256=s.sha256) for s in self.specs],
                'native_toc_emission': 'CALLER_HELD_DESCRIPTOR_PREREQUISITE',
                'scope': 'HELD_RECOVERY_INPUT_COMPONENT_ONLY', 'execution_authority': False}


@contextmanager
def hold_recovery_inputs(root, specs):
    """Seal exact private input bytes; close all owned handles on every exit.

    Pending cancellation during acquisition is delivered inside this generator's
    cleanup boundary. Even a failed exit signal precondition closes descriptors;
    no path is deleted. A replaced descriptor is preserved and cleanup refuses.
    """
    _require(type(root) is str and root.startswith('/') and str(Path(root)) == root and root != '/'
             and all(p not in ('', '.', '..') for p in root.split('/')[1:]) and '\0' not in root)
    _require(type(specs) is tuple and len(specs) == 3
             and all(type(s) is ArchiveSpec for s in specs)
             and tuple(s.name for s in specs) == tuple(LIMITS))
    for spec in specs: spec.validate()
    _require(os.geteuid() > 0 and os.getegid() > 0)
    _signal_boundary()
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, _MASK)
    owners = []; value = None; failure = None; cleanup = []
    try:
        path = Path(root)
        with _held_source_file(path.parent, path.name) as directory:
            info = os.fstat(directory)
            _require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
                     and (info.st_uid, info.st_gid) == (os.geteuid(), os.getegid()))
            archives = tuple(_copy_archive(directory, spec, owners) for spec in specs)
        value = object.__new__(HeldRecoveryInputs)
        value._token = _TOKEN; value.state = 'HELD'; value.specs = specs
        value.archives = archives; value._owners = owners; value.use_list = None
        value.toc_observation = None; value._verify()
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        yield value
    except BaseException as error:
        failure = (RecoveryInputRefusal('populated_recovery_input_acquisition_refused')
                   if value is None and isinstance(error, (OSError, HostedSourceError, ns.NamespaceProofError)) else error)
    finally:
        # Do not put a refusing signal precondition before descriptor closure.
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, _MASK)
            _signal_boundary()
        except BaseException as error:
            cleanup.append(error)
        for descriptor in reversed(owners):
            try: descriptor.close()
            except BaseException as error: cleanup.append(error)
        if value is not None: value.state = 'CLEANUP_REFUSED' if cleanup else 'CLOSED'
        try: signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        except BaseException as error:
            if failure is None: failure = error
        if cleanup:
            raise RecoveryInputCleanupError('populated_recovery_handle_cleanup_refused') from failure
        if failure is not None: raise failure


def _storage_records(rows, *, binding):
    _require(type(rows) is list and 0 < len(rows) <= 128)
    result = {}; total = 0
    for row in rows:
        _require(type(row) is dict and set(row) == {'path','bytes','sha256'})
        name, size, digest = row['path'], row['bytes'], row['sha256']
        _require(type(name) is str and len(name) <= 256 and type(digest) is str and _HASH.fullmatch(digest))
        price = _PRICE.fullmatch(name); artifact = _ARTIFACT.fullmatch(name)
        _require(price or artifact)
        _require(digest == (price.group(1) if price else next(v for v in artifact.groups() if v)))
        if binding and price:
            _require(size is None)
        else:
            _require(type(size) is int and 0 <= size <= 1024**2)
            total += size
        _require(name not in result and total <= LIMITS['storage.tar'])
        result[name] = row
    _require(list(result) == sorted(result))
    return result


def validate_storage_archive(raw, inventory, bindings):
    """Validate the actual writer's bounded uncompressed PAX format in memory.

    Re-encoding exactly detects hidden/trailing headers and metadata, including
    unneeded PAX overrides. No tar member or supplied UID/GID is extracted.
    """
    _require(type(raw) is bytes and 0 < len(raw) <= LIMITS['storage.tar'])
    observed = _storage_records(inventory, binding=False)
    expected = _storage_records(bindings, binding=True)
    _require(set(observed) == set(expected))
    for name, item in observed.items():
        other = expected[name]
        _require(item['sha256'] == other['sha256'] and (other['bytes'] is None or item['bytes'] == other['bytes']))
    members = []
    try:
        # Bound the physical header chain before tarfile follows PAX extensions.
        # At most one small local path record can precede each regular file.
        offset = 0; pending = False; files = 0
        while True:
            _require(offset+512 <= len(raw))
            header = raw[offset:offset+512]
            if header == bytes(512):
                _require(not pending and raw[offset:] == bytes(len(raw)-offset))
                break
            info = tarfile.TarInfo.frombuf(header, 'ascii', 'strict')
            if info.type == tarfile.XHDTYPE:
                _require(not pending and 0 < info.size <= 512 and info.name == '././@PaxHeader')
                pending = True
                payload = raw[offset+512:offset+512+info.size]
                # A single exact ASCII path record, no size/global/sparse keys.
                match = re.fullmatch(rb'([1-9][0-9]{1,2}) path=([^\n\x00]{1,256})\n', payload)
                _require(match is not None and int(match.group(1)) == len(payload))
            else:
                _require(info.type == tarfile.REGTYPE and 0 <= info.size <= 1024**2)
                pending = False; files += 1
                _require(files <= len(observed))
            offset += 512 + (info.size+511)//512*512
            _require(offset <= len(raw))
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            for info in archive:
                index = len(members)
                _require(index < len(observed) and info.name == list(observed)[index])
                row = observed[info.name]
                _require(info.type == tarfile.REGTYPE and info.size == row['bytes']
                         and info.mode == 0o600 and info.uid == info.gid == info.mtime == 0
                         and info.uname == info.gname == info.linkname == ''
                         and not info.sparse and set(info.pax_headers) <= {'path'}
                         and (not info.pax_headers or info.pax_headers['path'] == info.name))
                stream = archive.extractfile(info)
                _require(stream is not None)
                with stream: data = stream.read(info.size+1)
                _require(len(data) == info.size and hashlib.sha256(data).hexdigest() == row['sha256'])
                members.append(StorageMember(info.name, data))
        _require(len(members) == len(observed))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for member in members:
                info = tarfile.TarInfo(member.name); info.size = len(member.data)
                info.mode = 0o600; info.mtime = info.uid = info.gid = 0
                info.uname = info.gname = ''
                archive.addfile(info, io.BytesIO(member.data))
        _require(output.getvalue() == raw)
    except (tarfile.TarError, UnicodeError, ValueError, OSError):
        raise RecoveryInputRefusal('populated_recovery_storage_refused') from None
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(',',':')).encode('ascii')
    return tuple(members), {'contract':'BUFFALO_POPULATED_RECOVERY_STORAGE_V1',
        'archive_sha256':hashlib.sha256(raw).hexdigest(), 'inventory_sha256':hashlib.sha256(canonical(inventory)).hexdigest(),
        'binding_sha256':hashlib.sha256(canonical(bindings)).hexdigest(), 'files':len(members),
        'scope':'STORAGE_BYTES_AND_REGISTRY_COMPONENT_ONLY', 'database_observation':'CALLER_ATTESTED_SNAPSHOT_REQUIRED',
        'restore_execution':'NOT_RUN', 'execution_authority':False}
