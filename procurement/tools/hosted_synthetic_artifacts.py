"""Dormant bounded byte ingress for the canonical hosted synthetic packet.

Registry expectations must come from the same-generation read-only database
observation, never from the downloaded bytes. This module does not query a DB,
operate a browser, grant execution authority, or certify business semantics.
It reuses the held-ancestor primitive and never extracts ZIP members to disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import struct
import zipfile
import zlib

from hosted_synthetic_source import HostedSourceError, _held_source_file, _resource_operation
from hosted_synthetic_runtime import _metadata

MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024
SAFETY_LABEL = "TEST DATA — NOT FOR ORDERING"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MEMBERS = frozenset((
    "human-review-decisions.csv", "frozen-input-manifest.json",
    "recommendations-and-reasons.json", "frozen-price-economics.json",
    "supplier-mapping-evidence.json", "open-po-ledger-evidence.json",
    "blocked-item-exclusions.json", "material-edit-confirmations.json",
    "draft-readiness-evidence.json", "forecast-and-protection-evidence.json",
    "packet-summary.json", "manifest.json",
))


class HostedArtifactError(ValueError):
    """Fixed diagnostics: never include downloaded data or caller paths."""


def _require(value: bool) -> None:
    if not value:
        raise HostedArtifactError("hosted_artifact_refused")


@dataclass(frozen=True)
class RegisteredArtifact:
    run_id: str
    artifact_id: str
    kind: str
    vendor_id: str | None
    size_bytes: int
    sha256: str

    def validate(self) -> None:
        _require(type(self) is RegisteredArtifact
                 and type(self.run_id) is str and _UUID.fullmatch(self.run_id)
                 and type(self.artifact_id) is str and re.fullmatch(r"[1-9][0-9]{0,17}", self.artifact_id)
                 and type(self.kind) is str and self.kind in ("VENDOR_INTERNAL_CSV", "EMERGENCY_REVIEW_PACKET")
                 and type(self.size_bytes) is int and 0 < self.size_bytes <= MAX_ARTIFACT_BYTES
                 and type(self.sha256) is str and _HASH.fullmatch(self.sha256))
        _require((self.kind == "EMERGENCY_REVIEW_PACKET" and self.vendor_id is None)
                 or (self.kind == "VENDOR_INTERNAL_CSV" and type(self.vendor_id) is str
                     and _UUID.fullmatch(self.vendor_id)))


@dataclass(frozen=True)
class CapturedArtifact:
    registry: RegisteredArtifact
    guid: str
    data: bytes = field(repr=False)

    def validate(self) -> None:
        _require(type(self) is CapturedArtifact and type(self.registry) is RegisteredArtifact)
        self.registry.validate()
        _require(type(self.guid) is str and _UUID.fullmatch(self.guid)
                 and type(self.data) is bytes and len(self.data) == self.registry.size_bytes
                 and hashlib.sha256(self.data).hexdigest() == self.registry.sha256)


@_resource_operation
def read_owned_download(root: str, guid: str, registry: RegisteredArtifact,
                        *, uid: int, gid: int) -> CapturedArtifact:
    """Copy one allowAndName GUID through held private-directory/file FDs.

    The outer browser owner must have configured allowAndName in the bound
    context. Directory ownership is not proof of worker/control isolation.
    No removal, permission changes, path reopening or lifecycle PASS occurs.
    """
    _require(type(root) is str and len(root) <= 4096 and root.startswith("/") and "\0" not in root
             and str(Path(root)) == root and root != "/"
             and all(part not in ("", ".", "..") for part in root.split("/")[1:]))
    _require(type(guid) is str and _UUID.fullmatch(guid)
             and type(uid) is int and uid >= 0 and type(gid) is int and gid >= 0
             and type(registry) is RegisteredArtifact)
    registry.validate()
    path = Path(root)
    try:
        with _held_source_file(path.parent, path.name) as directory:
            directory_before = os.fstat(directory)
            _require(stat.S_ISDIR(directory_before.st_mode)
                     and stat.S_IMODE(directory_before.st_mode) == 0o700
                     and (directory_before.st_uid, directory_before.st_gid) == (uid, gid))
            named = os.stat(guid, dir_fd=directory, follow_symlinks=False)
            _require(stat.S_ISREG(named.st_mode) and named.st_nlink == 1)
            fd = os.open(guid, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=directory)
            try:
                before = os.fstat(fd)
                _require(_metadata(before) == _metadata(named)
                         and stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                         and stat.S_IMODE(before.st_mode) in (0o600, 0o644)
                         and (before.st_uid, before.st_gid) == (uid, gid)
                         and before.st_size == registry.size_bytes)
                chunks, offset = [], 0
                while offset <= registry.size_bytes:
                    try:
                        chunk = os.pread(fd, min(65536, registry.size_bytes + 1 - offset), offset)
                    except InterruptedError:
                        # EINTR cannot extend a byte budget or substitute data.
                        raise HostedArtifactError("hosted_artifact_interrupted") from None
                    if not chunk:
                        break
                    chunks.append(chunk)
                    offset += len(chunk)
                data = b"".join(chunks)
                result = CapturedArtifact(registry, guid, data)
                result.validate()
                _require(_metadata(before) == _metadata(os.fstat(fd))
                         == _metadata(os.stat(guid, dir_fd=directory, follow_symlinks=False))
                         and _metadata(directory_before) == _metadata(os.fstat(directory)))
            finally:
                os.close(fd)
        return result
    except (OSError, HostedSourceError):
        raise HostedArtifactError("hosted_artifact_read_refused") from None


def bounded_json(data: bytes):
    _require(type(data) is bytes and 0 < len(data) <= MAX_ARTIFACT_BYTES)

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result

    def constant(_value):
        raise HostedArtifactError("hosted_artifact_json_refused")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        pending, nodes = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            nodes += 1
            _require(depth <= 32 and nodes <= 100000)
            if type(item) is dict:
                pending.extend((v, depth + 1) for v in item.values())
            elif type(item) is list:
                pending.extend((v, depth + 1) for v in item)
            elif type(item) is float:
                # Existing packet economics are Decimal strings, never floats.
                raise HostedArtifactError("hosted_artifact_json_float_refused")
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, HostedArtifactError):
            raise
        raise HostedArtifactError("hosted_artifact_json_refused") from None


def packet_members(packet: CapturedArtifact, vendors: tuple[str, str]) -> tuple[tuple[str, bytes], ...]:
    """Validate canonical ZIP framing and its 13-member hash manifest only.

    This returns original immutable member bytes. Fourteen members and matching
    hashes alone do not certify procurement, forecasting or packet semantics.
    """
    _require(type(packet) is CapturedArtifact)
    packet.validate()
    _require(packet.registry.kind == "EMERGENCY_REVIEW_PACKET"
             and type(vendors) is tuple and len(vendors) == 2 and vendors[0] != vendors[1]
             and all(type(v) is str and _UUID.fullmatch(v) for v in vendors))
    names = _MEMBERS | {f"vendor-{vendor}.internal.csv" for vendor in vendors}
    raw = packet.data
    try:
        _require(len(raw) >= 22 and raw.startswith(b"PK\x03\x04"))
        end = struct.unpack("<4s4H2LH", raw[-22:])
        _require(end[:5] == (b"PK\x05\x06", 0, 0, 14, 14) and end[-1] == 0
                 and end[5] + end[6] == len(raw) - 22)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            _require(len(infos) == 14 and [x.filename for x in infos] == sorted(names)
                     and not archive.comment and sum(x.file_size for x in infos) <= MAX_EXPANDED_BYTES)
            entries, offset = {}, 0
            for info in infos:
                _require(info.filename in names and info.filename not in entries
                         and info.header_offset == offset and not info.extra and not info.comment
                         and info.flag_bits == 0 and info.compress_type == zipfile.ZIP_DEFLATED
                         and info.external_attr == 0o644 << 16
                         and info.date_time == (1980, 1, 1, 0, 0, 0)
                         and 0 < info.file_size <= MAX_ARTIFACT_BYTES and 0 < info.compress_size <= len(raw))
                header = struct.unpack("<4s5H3L2H", raw[offset:offset + 30])
                filename = info.filename.encode("ascii")
                _require(header[0] == b"PK\x03\x04" and header[2] == 0 and header[3] == zipfile.ZIP_DEFLATED
                         and header[4:6] == (0, 33)
                         and header[6:9] == (info.CRC, info.compress_size, info.file_size)
                         and header[9:] == (len(filename), 0)
                         and raw[offset + 30:offset + 30 + len(filename)] == filename)
                start = offset + 30 + len(filename)
                offset = start + info.compress_size
                _require(offset <= end[6])
                inflater = zlib.decompressobj(-zlib.MAX_WBITS)
                data = inflater.decompress(raw[start:offset], info.file_size + 1)
                _require(len(data) == info.file_size and inflater.eof and not inflater.unconsumed_tail
                         and not inflater.unused_data and zlib.crc32(data) == info.CRC)
                entries[info.filename] = data
            _require(offset == end[6])
        manifest = bounded_json(entries["manifest.json"])
        _require(type(manifest) is dict and set(manifest) == {"safety_label", "entries"}
                 and manifest["safety_label"] == SAFETY_LABEL and type(manifest["entries"]) is dict
                 and set(manifest["entries"]) == names - {"manifest.json"})
        for name, expected in manifest["entries"].items():
            _require(type(expected) is str and _HASH.fullmatch(expected)
                     and hashlib.sha256(entries[name]).hexdigest() == expected)
        return tuple(sorted(entries.items()))
    except (OSError, ValueError, KeyError, TypeError, NotImplementedError, struct.error, zipfile.BadZipFile, zlib.error) as exc:
        if isinstance(exc, HostedArtifactError):
            raise
        raise HostedArtifactError("hosted_packet_framing_refused") from None
