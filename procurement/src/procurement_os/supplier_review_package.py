"""Bounded, read-only supplier mapping review-package handling.

This module is deliberately separate from :mod:`price_book`.  It reads review
evidence only; it cannot stage or promote operational prices and it never
connects to a database.  Real review rows remain unapproved and not import
ready regardless of structural validity.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, BinaryIO, Iterator, Mapping, Sequence
import unicodedata
from zipfile import BadZipFile, ZipFile, ZipInfo

from .price_book import PRICE_BOOK_HEADERS


REVIEW_LABEL = "REVIEW ONLY / NOT_IMPORT_READY"
PACKAGE_FORMAT = "BUFFALO_MAPPING_REVIEW_PACKAGE_V1"
RAW_HASH = "RAW_BYTES_SHA256"
CANONICAL_JSONL_HASH = "CANONICAL_JSONL_SHA256"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ReviewPackageError(ValueError):
    """A fail-closed review-package validation error with a stable code."""

    def __init__(self, code: str, message: str, *, path: str | None = None):
        self.code = code
        self.path = path
        suffix = f" ({path})" if path else ""
        super().__init__(f"{code}: {message}{suffix}")


@dataclass(frozen=True)
class ReviewLimits:
    """Explicit bounds for large offline review evidence.

    These limits are intentionally independent of the operational price-book
    import's immutable 5,000,000-byte boundary.
    """

    max_entries: int = 2_048
    max_entry_bytes: int = 450_000_000
    max_total_expanded_bytes: int = 800_000_000
    max_compression_ratio: int = 200
    max_path_depth: int = 16
    max_rows_per_table: int = 250_000
    max_fields_per_record: int = 512
    max_field_bytes: int = 2_000_000
    max_line_bytes: int = 8_000_000
    max_json_depth: int = 24
    max_manifest_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_LIMITS = ReviewLimits()


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "WARN"
    path: str | None = None
    table: str | None = None
    row: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "table": self.table,
            "row": self.row,
        }


@dataclass(frozen=True)
class PackageTable:
    name: str
    path: str
    format: str
    rows: tuple[dict[str, Any], ...]
    raw_sha256: str
    canonical_jsonl_sha256: str | None
    unknown_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewPackage:
    source: str
    package_kind: str
    snapshot_id: str
    status: str
    label: str
    file_count: int
    verified_file_count: int
    manifest_sha256: str
    tables: Mapping[str, PackageTable]
    cohorts: Mapping[str, Any]
    issues: tuple[ValidationIssue, ...]
    unavailable_evidence: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return self.status == "PASS"

    def table(self, name: str) -> tuple[dict[str, Any], ...]:
        table = self.tables.get(name)
        return () if table is None else table.rows

    def summary(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "source": self.source,
            "package_kind": self.package_kind,
            "snapshot_id": self.snapshot_id,
            "status": self.status,
            "file_count": self.file_count,
            "verified_file_count": self.verified_file_count,
            "manifest_sha256": self.manifest_sha256,
            "tables": {
                name: {
                    "path": table.path,
                    "format": table.format,
                    "row_count": len(table.rows),
                    "raw_sha256": table.raw_sha256,
                    "canonical_jsonl_sha256": table.canonical_jsonl_sha256,
                    "unknown_fields": list(table.unknown_fields),
                }
                for name, table in sorted(self.tables.items())
            },
            "cohorts": dict(self.cohorts),
            "issues": [issue.as_dict() for issue in self.issues],
            "unavailable_evidence": list(self.unavailable_evidence),
        }


@dataclass(frozen=True)
class PatchReplay:
    rows: tuple[dict[str, Any], ...]
    applied: int
    already_applied: int
    canonical_jsonl_sha256: str


def _validated_member_name(name: str, limits: ReviewLimits) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ReviewPackageError("UNSAFE_PATH", "package member path is invalid")
    normalized = unicodedata.normalize("NFC", name)
    if normalized != name:
        raise ReviewPackageError(
            "PATH_NORMALIZATION_COLLISION",
            "package paths must already use NFC normalization",
            path=name,
        )
    if "\\" in name or name.startswith("/") or _WINDOWS_DRIVE.match(name):
        raise ReviewPackageError("UNSAFE_PATH", "absolute or backslash path", path=name)
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ReviewPackageError("UNSAFE_PATH", "path traversal or empty segment", path=name)
    if len(path.parts) > limits.max_path_depth:
        raise ReviewPackageError("PATH_TOO_DEEP", "package path nesting exceeds limit", path=name)
    return str(path)


class _PackageSource:
    def __init__(self, path: Path, limits: ReviewLimits):
        self.path = path
        self.limits = limits
        self._members: dict[str, int] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._members))

    def size(self, name: str) -> int:
        self._require_name(name)
        return self._members[name]

    def _require_name(self, name: str) -> None:
        if name not in self._members:
            raise ReviewPackageError("MISSING_FILE", "declared file is absent", path=name)

    @contextmanager
    def open(self, name: str) -> Iterator[BinaryIO]:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def __enter__(self) -> _PackageSource:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _DirectorySource(_PackageSource):
    def __init__(self, path: Path, limits: ReviewLimits):
        super().__init__(path, limits)
        if path.is_symlink() or not path.is_dir():
            raise ReviewPackageError("INVALID_PACKAGE", "package directory is not a real directory")
        total = 0
        collisions: dict[str, str] = {}
        for candidate in sorted(path.rglob("*")):
            relative = candidate.relative_to(path).as_posix()
            name = _validated_member_name(relative, limits)
            if candidate.is_symlink():
                raise ReviewPackageError("SYMLINK_REJECTED", "symlinks are not permitted", path=name)
            if candidate.is_dir():
                continue
            mode = candidate.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise ReviewPackageError("SPECIAL_FILE_REJECTED", "only regular files are permitted", path=name)
            collision_key = unicodedata.normalize("NFC", name).casefold()
            if collision_key in collisions:
                raise ReviewPackageError(
                    "PATH_COLLISION",
                    f"path collides with {collisions[collision_key]}",
                    path=name,
                )
            collisions[collision_key] = name
            size = candidate.stat(follow_symlinks=False).st_size
            if size > limits.max_entry_bytes:
                raise ReviewPackageError("ENTRY_TOO_LARGE", "package member exceeds limit", path=name)
            total += size
            if total > limits.max_total_expanded_bytes:
                raise ReviewPackageError("PACKAGE_TOO_LARGE", "expanded package exceeds limit")
            self._members[name] = size
        if len(self._members) > limits.max_entries:
            raise ReviewPackageError("TOO_MANY_ENTRIES", "package entry count exceeds limit")

    @contextmanager
    def open(self, name: str) -> Iterator[BinaryIO]:
        self._require_name(name)
        candidate = self.path / PurePosixPath(name)
        if candidate.is_symlink() or not candidate.is_file():
            raise ReviewPackageError("FILE_CHANGED", "package member changed after scan", path=name)
        with candidate.open("rb") as handle:
            yield handle


class _ZipSource(_PackageSource):
    def __init__(self, path: Path, limits: ReviewLimits):
        super().__init__(path, limits)
        try:
            self._zip = ZipFile(path)
        except (BadZipFile, OSError) as exc:
            raise ReviewPackageError("INVALID_ZIP", "package is not a readable ZIP") from exc
        collisions: dict[str, str] = {}
        members: dict[str, ZipInfo] = {}
        total = 0
        try:
            infos = self._zip.infolist()
            if len(infos) > limits.max_entries:
                raise ReviewPackageError("TOO_MANY_ENTRIES", "ZIP entry count exceeds limit")
            for info in infos:
                name = _validated_member_name(info.filename.rstrip("/"), limits)
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise ReviewPackageError("ENCRYPTED_ENTRY", "encrypted entries are not permitted", path=name)
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                if file_type == stat.S_IFLNK:
                    raise ReviewPackageError("SYMLINK_REJECTED", "ZIP symlinks are not permitted", path=name)
                if file_type not in {0, stat.S_IFREG}:
                    raise ReviewPackageError("SPECIAL_FILE_REJECTED", "ZIP special files are not permitted", path=name)
                collision_key = unicodedata.normalize("NFC", name).casefold()
                if name in members or collision_key in collisions:
                    prior = members.get(name)
                    prior_name = name if prior is not None else collisions[collision_key]
                    raise ReviewPackageError("PATH_COLLISION", f"path collides with {prior_name}", path=name)
                collisions[collision_key] = name
                if info.file_size > limits.max_entry_bytes:
                    raise ReviewPackageError("ENTRY_TOO_LARGE", "ZIP member exceeds limit", path=name)
                if info.file_size and not info.compress_size:
                    raise ReviewPackageError("ARCHIVE_BOMB", "nonempty member has no compressed size", path=name)
                if info.compress_size and info.file_size > info.compress_size * limits.max_compression_ratio:
                    raise ReviewPackageError("ARCHIVE_BOMB", "compression ratio exceeds limit", path=name)
                total += info.file_size
                if total > limits.max_total_expanded_bytes:
                    raise ReviewPackageError("PACKAGE_TOO_LARGE", "expanded ZIP exceeds limit")
                members[name] = info
                self._members[name] = info.file_size
        except BaseException:
            self._zip.close()
            raise
        self._infos = members

    @contextmanager
    def open(self, name: str) -> Iterator[BinaryIO]:
        self._require_name(name)
        try:
            with self._zip.open(self._infos[name], "r") as handle:
                yield handle
        except (BadZipFile, RuntimeError, OSError) as exc:
            raise ReviewPackageError("ZIP_READ_FAILED", "ZIP member could not be read", path=name) from exc

    def close(self) -> None:
        self._zip.close()


def _open_source(path: str | Path, limits: ReviewLimits) -> _PackageSource:
    candidate = Path(path)
    if candidate.is_dir():
        return _DirectorySource(candidate, limits)
    if candidate.is_file():
        return _ZipSource(candidate, limits)
    raise ReviewPackageError("MISSING_PACKAGE", "review package path does not exist")


def _stream_sha256(source: _PackageSource, name: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    observed = 0
    with source.open(name) as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > source.limits.max_entry_bytes:
                raise ReviewPackageError("ENTRY_TOO_LARGE", "stream exceeded entry limit", path=name)
            digest.update(chunk)
    if observed != source.size(name):
        raise ReviewPackageError("SIZE_MISMATCH", "member size changed or disagrees with ZIP metadata", path=name)
    return digest.hexdigest(), observed


def _read_bounded(source: _PackageSource, name: str, maximum: int) -> bytes:
    if source.size(name) > maximum:
        raise ReviewPackageError("ENTRY_TOO_LARGE", "member exceeds bounded read limit", path=name)
    with source.open(name) as handle:
        data = handle.read(maximum + 1)
        if len(data) > maximum or handle.read(1):
            raise ReviewPackageError("ENTRY_TOO_LARGE", "member exceeds bounded read limit", path=name)
    return data


def _read_verified_bounded(
    source: _PackageSource,
    name: str,
    maximum: int,
    declared: Mapping[str, tuple[int, str]],
) -> bytes:
    data = _read_bounded(source, name, maximum)
    expected_bytes, expected_sha = declared[name]
    if len(data) != expected_bytes or hashlib.sha256(data).hexdigest() != expected_sha:
        raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "parsed bytes differ from manifest", path=name)
    return data


def _json_depth(value: Any, *, _depth: int = 0) -> int:
    if isinstance(value, Mapping):
        if not value:
            return _depth
        return max(_json_depth(item, _depth=_depth + 1) for item in value.values())
    if isinstance(value, list):
        if not value:
            return _depth
        return max(_json_depth(item, _depth=_depth + 1) for item in value)
    return _depth


def _parse_json_bytes(data: bytes, *, path: str, limits: ReviewLimits) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewPackageError("INVALID_UTF8", "JSON must be UTF-8", path=path) from exc
    try:
        value = json.loads(text, parse_float=Decimal)
    except (json.JSONDecodeError, InvalidOperation) as exc:
        raise ReviewPackageError("INVALID_JSON", "JSON is malformed", path=path) from exc
    if _json_depth(value) > limits.max_json_depth:
        raise ReviewPackageError("JSON_TOO_DEEP", "JSON nesting exceeds limit", path=path)
    return value


def _canonical_json(value: Any) -> bytes:
    """Encode native JSON values deterministically while retaining Decimal scale."""

    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, bool):  # pragma: no cover - guarded above
        raise TypeError("boolean branch is unreachable")
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ReviewPackageError("NONFINITE_NUMBER", "JSON decimals must be finite")
        return format(value, "f").encode("ascii")
    if isinstance(value, float):
        raise ReviewPackageError("FLOAT_NOT_EXACT", "binary floats are not canonical review values")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if isinstance(value, list) or isinstance(value, tuple):
        return b"[" + b",".join(_canonical_json(item) for item in value) + b"]"
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ReviewPackageError("INVALID_JSON_KEY", "JSON object keys must be strings")
        pieces = []
        for key in sorted(value):
            pieces.append(_canonical_json(key) + b":" + _canonical_json(value[key]))
        return b"{" + b",".join(pieces) + b"}"
    raise ReviewPackageError("UNSUPPORTED_VALUE", f"unsupported canonical value {type(value).__name__}")


def canonical_record_sha256(record: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(record)).hexdigest()


def canonical_jsonl_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(_canonical_json(row))
        digest.update(b"\n")
    return digest.hexdigest()


def _checked_record(record: Any, *, path: str, row: int, limits: ReviewLimits) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ReviewPackageError("ROW_NOT_OBJECT", "JSONL row must be an object", path=path)
    if len(record) > limits.max_fields_per_record:
        raise ReviewPackageError("TOO_MANY_FIELDS", "row field count exceeds limit", path=path)
    if _json_depth(record) > limits.max_json_depth:
        raise ReviewPackageError("JSON_TOO_DEEP", f"row {row} nesting exceeds limit", path=path)
    for key, value in record.items():
        if not isinstance(key, str):
            raise ReviewPackageError("INVALID_JSON_KEY", "row keys must be strings", path=path)
        if isinstance(value, str) and len(value.encode("utf-8")) > limits.max_field_bytes:
            raise ReviewPackageError("FIELD_TOO_LARGE", f"field {key} exceeds limit", path=path)
    return record


def _read_jsonl(source: _PackageSource, name: str) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    with source.open(name) as handle:
        while True:
            line = handle.readline(source.limits.max_line_bytes + 1)
            if not line:
                break
            if len(line) > source.limits.max_line_bytes:
                raise ReviewPackageError("LINE_TOO_LARGE", "JSONL line exceeds limit", path=name)
            digest.update(line)
            if not line.strip():
                raise ReviewPackageError("BLANK_JSONL_ROW", "blank JSONL rows are not permitted", path=name)
            if len(rows) >= source.limits.max_rows_per_table:
                raise ReviewPackageError("TOO_MANY_ROWS", "JSONL row count exceeds limit", path=name)
            value = _parse_json_bytes(line, path=name, limits=source.limits)
            rows.append(_checked_record(value, path=name, row=len(rows) + 1, limits=source.limits))
    return rows, digest.hexdigest()


def _decode_csv_value(raw: str, code: str, *, path: str, row: int, field_name: str) -> tuple[bool, Any]:
    if code == "m":
        return False, None
    if code == "n":
        if raw != "":
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", f"null {field_name} must be empty", path=path)
        return True, None
    if code == "s":
        return True, raw
    if code == "q":
        if not raw.startswith("'"):
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", f"protected {field_name} lacks apostrophe", path=path)
        return True, raw[1:]
    if code == "b":
        if raw not in {"true", "false"}:
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", f"boolean {field_name} is invalid", path=path)
        return True, raw == "true"
    if code == "i":
        if not re.fullmatch(r"-?(0|[1-9][0-9]*)", raw):
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", f"integer {field_name} is invalid", path=path)
        return True, int(raw)
    if code == "f":
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", f"decimal {field_name} is invalid", path=path) from exc
        if not value.is_finite():
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", f"decimal {field_name} is nonfinite", path=path)
        return True, value
    if code == "j":
        try:
            value = json.loads(raw, parse_float=Decimal)
        except (json.JSONDecodeError, InvalidOperation) as exc:
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", f"JSON {field_name} is invalid", path=path) from exc
        return True, value
    raise ReviewPackageError("UNKNOWN_CELL_TYPE", f"unknown type code {code!r} at row {row}", path=path)


class _DigestingReader(io.RawIOBase):
    def __init__(self, raw: BinaryIO, maximum: int):
        super().__init__()
        self.raw = raw
        self.maximum = maximum
        self.digest = hashlib.sha256()
        self.count = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray) -> int:
        data = self.raw.read(len(buffer))
        if not data:
            return 0
        self.count += len(data)
        if self.count > self.maximum:
            raise ReviewPackageError("ENTRY_TOO_LARGE", "CSV stream exceeds limit")
        self.digest.update(data)
        buffer[: len(data)] = data
        return len(data)


def _read_typed_csv(
    source: _PackageSource,
    name: str,
    *,
    sidecar_name: str | None,
    expected_fields: Sequence[str] | None,
    expected_raw_sha256: str | None = None,
    expected_sidecar_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    sidecar_rows: list[dict[str, Any]] | None = None
    if sidecar_name:
        sidecar_rows, sidecar_sha = _read_jsonl(source, sidecar_name)
        if expected_sidecar_sha256 is not None and sidecar_sha != expected_sidecar_sha256:
            raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "type sidecar bytes differ", path=sidecar_name)
    prior_limit = csv.field_size_limit()
    csv.field_size_limit(source.limits.max_field_bytes)
    try:
        with source.open(name) as raw_handle:
            digesting = _DigestingReader(raw_handle, source.limits.max_entry_bytes)
            buffered = io.BufferedReader(digesting)
            text_handle = io.TextIOWrapper(buffered, encoding="utf-8-sig", newline="")
            try:
                reader = csv.DictReader(text_handle)
                fields = reader.fieldnames
                if not fields or len(fields) > source.limits.max_fields_per_record:
                    raise ReviewPackageError("INVALID_CSV_HEADER", "CSV header is missing or too wide", path=name)
                if len(set(fields)) != len(fields) or any(not field for field in fields):
                    raise ReviewPackageError("INVALID_CSV_HEADER", "CSV fields must be unique and nonblank", path=name)
                if expected_fields is not None and list(expected_fields) != fields:
                    raise ReviewPackageError("CSV_HEADER_MISMATCH", "CSV header differs from declared contract", path=name)
                rows: list[dict[str, Any]] = []
                for raw_row in reader:
                    if len(rows) >= source.limits.max_rows_per_table:
                        raise ReviewPackageError("TOO_MANY_ROWS", "CSV row count exceeds limit", path=name)
                    if None in raw_row or any(value is None for value in raw_row.values()):
                        raise ReviewPackageError("CSV_WIDTH_MISMATCH", "CSV row width differs from header", path=name)
                    row_number = len(rows) + 1
                    if sidecar_rows is None:
                        row = dict(raw_row)
                    else:
                        if row_number > len(sidecar_rows):
                            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", "type sidecar has too few rows", path=sidecar_name)
                        sidecar = sidecar_rows[row_number - 1]
                        if sidecar.get("row_number_1based") != row_number:
                            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", "type sidecar row number drifted", path=sidecar_name)
                        types = sidecar.get("types")
                        if not isinstance(types, list) or len(types) != len(fields):
                            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", "type vector width drifted", path=sidecar_name)
                        row = {}
                        for field_name, code in zip(fields, types, strict=True):
                            present, value = _decode_csv_value(
                                raw_row[field_name], code, path=name, row=row_number, field_name=field_name
                            )
                            if present:
                                row[field_name] = value
                    rows.append(_checked_record(row, path=name, row=row_number, limits=source.limits))
                if sidecar_rows is not None and len(sidecar_rows) != len(rows):
                    raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", "type sidecar row count drifted", path=sidecar_name)
            finally:
                text_handle.detach()
    except csv.Error as exc:
        raise ReviewPackageError("INVALID_CSV", "CSV parsing failed", path=name) from exc
    finally:
        csv.field_size_limit(prior_limit)
    raw_sha = digesting.digest.hexdigest()
    if digesting.count != source.size(name):
        raise ReviewPackageError("SIZE_MISMATCH", "CSV stream size differs from scanned metadata", path=name)
    if expected_raw_sha256 is not None and raw_sha != expected_raw_sha256:
        raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "CSV bytes differ from manifest", path=name)
    return rows, raw_sha


def _require_mapping(value: Any, *, code: str, message: str, path: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewPackageError(code, message, path=path)
    return value


def _require_sequence(value: Any, *, code: str, message: str, path: str | None = None) -> list[Any]:
    if not isinstance(value, list):
        raise ReviewPackageError(code, message, path=path)
    return value


def _require_sha256(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ReviewPackageError("INVALID_HASH", "SHA-256 must be 64 lowercase hexadecimal characters", path=path)
    return value


def _manifest_files(
    source: _PackageSource,
    records: Any,
    *,
    manifest_name: str,
    require_complete: bool,
) -> tuple[dict[str, tuple[int, str]], int]:
    declared: dict[str, tuple[int, str]] = {}
    for index, raw in enumerate(
        _require_sequence(records, code="INVALID_MANIFEST", message="files must be an array", path=manifest_name),
        start=1,
    ):
        record = _require_mapping(
            raw, code="INVALID_MANIFEST", message=f"files[{index}] must be an object", path=manifest_name
        )
        candidate = record.get("path", record.get("file"))
        if not isinstance(candidate, str):
            raise ReviewPackageError("INVALID_MANIFEST", "file path must be a string", path=manifest_name)
        name = _validated_member_name(candidate, source.limits)
        if name == manifest_name or name in declared:
            raise ReviewPackageError("DUPLICATE_DECLARATION", "manifest file declaration is duplicated", path=name)
        expected_bytes = record.get("bytes")
        if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ReviewPackageError("INVALID_MANIFEST", "declared byte count is invalid", path=name)
        expected_sha = _require_sha256(record.get("sha256"), path=name)
        actual_sha, actual_bytes = _stream_sha256(source, name)
        if (actual_bytes, actual_sha) != (expected_bytes, expected_sha):
            raise ReviewPackageError("FILE_INTEGRITY_MISMATCH", "declared bytes or SHA-256 do not match", path=name)
        declared[name] = (actual_bytes, actual_sha)
    if require_complete:
        extras = sorted(set(source.names) - set(declared) - {manifest_name})
        if extras:
            raise ReviewPackageError("UNDECLARED_FILE", "package contains an undeclared member", path=extras[0])
    return declared, len(declared)


def _table_unknown_fields(rows: Sequence[Mapping[str, Any]], known: set[str]) -> tuple[str, ...]:
    return tuple(sorted({field for row in rows for field in row if field not in known}))


def _read_declared_table(
    source: _PackageSource,
    spec: Mapping[str, Any],
    *,
    declared: Mapping[str, tuple[int, str]],
) -> PackageTable:
    name = spec.get("name")
    path = spec.get("path")
    table_format = spec.get("format")
    if not isinstance(name, str) or not name or not isinstance(path, str):
        raise ReviewPackageError("INVALID_TABLE_SPEC", "table name and path must be nonblank strings")
    path = _validated_member_name(path, source.limits)
    if path not in declared:
        raise ReviewPackageError("UNDECLARED_TABLE_FILE", "table path is not integrity-declared", path=path)
    expected_fields = spec.get("fields")
    if expected_fields is not None and (
        not isinstance(expected_fields, list) or not all(isinstance(item, str) and item for item in expected_fields)
    ):
        raise ReviewPackageError("INVALID_TABLE_SPEC", "fields must be a string array", path=path)
    if table_format == "jsonl":
        rows, raw_sha = _read_jsonl(source, path)
    elif table_format == "csv":
        sidecar = spec.get("type_sidecar")
        if sidecar is not None:
            if not isinstance(sidecar, str):
                raise ReviewPackageError("INVALID_TABLE_SPEC", "type_sidecar must be a path", path=path)
            sidecar = _validated_member_name(sidecar, source.limits)
            if sidecar not in declared:
                raise ReviewPackageError("UNDECLARED_TABLE_FILE", "type sidecar is not declared", path=sidecar)
        rows, raw_sha = _read_typed_csv(
            source,
            path,
            sidecar_name=sidecar,
            expected_fields=expected_fields,
            expected_raw_sha256=declared[path][1],
            expected_sidecar_sha256=None if sidecar is None else declared[sidecar][1],
        )
    else:
        raise ReviewPackageError("UNSUPPORTED_TABLE_FORMAT", "only jsonl and csv tables are supported", path=path)
    if raw_sha != declared[path][1]:
        raise ReviewPackageError(
            "FILE_CHANGED_DURING_VALIDATION",
            "table bytes no longer match the integrity-verified manifest",
            path=path,
        )
    expected_rows = spec.get("rows")
    if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or expected_rows < 0:
        raise ReviewPackageError("INVALID_TABLE_SPEC", "table row count must be a nonnegative integer", path=path)
    if len(rows) != expected_rows:
        raise ReviewPackageError("ROW_COUNT_MISMATCH", "table row count differs from declaration", path=path)
    required = spec.get("required_fields", [])
    if not isinstance(required, list) or not all(isinstance(item, str) and item for item in required):
        raise ReviewPackageError("INVALID_TABLE_SPEC", "required_fields must be a string array", path=path)
    for row_number, row in enumerate(rows, start=1):
        missing = [field for field in required if field not in row]
        if missing:
            raise ReviewPackageError(
                "REQUIRED_FIELD_MISSING", f"row {row_number} lacks {', '.join(missing)}", path=path
            )
    known_raw = spec.get("known_fields", expected_fields or required)
    if not isinstance(known_raw, list) or not all(isinstance(item, str) for item in known_raw):
        raise ReviewPackageError("INVALID_TABLE_SPEC", "known_fields must be a string array", path=path)
    canonical_sha = canonical_jsonl_sha256(rows)
    declared_canonical = spec.get("canonical_jsonl_sha256")
    if declared_canonical is not None and canonical_sha != _require_sha256(declared_canonical, path=path):
        raise ReviewPackageError("CANONICAL_HASH_MISMATCH", "canonical JSONL hash differs", path=path)
    return PackageTable(
        name=name,
        path=path,
        format=table_format,
        rows=tuple(rows),
        raw_sha256=raw_sha,
        canonical_jsonl_sha256=canonical_sha,
        unknown_fields=_table_unknown_fields(rows, set(known_raw)),
    )


def _validate_joins(tables: Mapping[str, PackageTable], joins: Any) -> None:
    for index, raw in enumerate(
        _require_sequence(joins, code="INVALID_JOIN", message="joins must be an array"), start=1
    ):
        join = _require_mapping(raw, code="INVALID_JOIN", message=f"join {index} must be an object")
        from_name = join.get("from_table")
        to_name = join.get("to_table")
        from_fields = join.get("from_fields")
        to_fields = join.get("to_fields")
        if from_name not in tables or to_name not in tables:
            raise ReviewPackageError("INVALID_JOIN", "join references an unknown table")
        if (
            not isinstance(from_fields, list)
            or not isinstance(to_fields, list)
            or not from_fields
            or len(from_fields) != len(to_fields)
            or not all(isinstance(field, str) and field for field in from_fields + to_fields)
        ):
            raise ReviewPackageError("INVALID_JOIN", "join fields must be equal-width nonblank string arrays")
        destinations: set[tuple[Any, ...]] = set()
        for row_number, row in enumerate(tables[to_name].rows, start=1):
            if any(field not in row for field in to_fields):
                raise ReviewPackageError("JOIN_KEY_MISSING", f"target row {row_number} lacks a join field")
            key = tuple(row[field] for field in to_fields)
            if any(item is None for item in key):
                raise ReviewPackageError("JOIN_TARGET_NULL", "join target keys may not be null")
            if key in destinations:
                raise ReviewPackageError("JOIN_TARGET_NOT_UNIQUE", "join target key is duplicated", table=to_name)
            destinations.add(key)
        allow_null = join.get("allow_null", False)
        if not isinstance(allow_null, bool):
            raise ReviewPackageError("INVALID_JOIN", "allow_null must be boolean")
        for row_number, row in enumerate(tables[from_name].rows, start=1):
            if any(field not in row for field in from_fields):
                raise ReviewPackageError("JOIN_KEY_MISSING", f"source row {row_number} lacks a join field")
            key = tuple(row[field] for field in from_fields)
            if allow_null and any(item is None for item in key):
                continue
            if key not in destinations:
                raise ReviewPackageError(
                    "JOIN_MISSING",
                    f"declared relationship from {from_name} has no target",
                    path=tables[from_name].path,
                )


def replay_patch_table(
    base_rows: Sequence[Mapping[str, Any]],
    patches: Sequence[Mapping[str, Any]],
    *,
    allowed_fields: set[str] | None = None,
    expected_rows: int | None = None,
    expected_sha256: str | None = None,
) -> PatchReplay:
    """Apply an exact, non-destructive row patch and prove idempotent replay."""

    rows = [copy.deepcopy(dict(row)) for row in base_rows]
    touched: set[tuple[str, int | tuple[tuple[str, bytes], ...]]] = set()
    applied = 0
    already_applied = 0
    for patch_number, raw_patch in enumerate(patches, start=1):
        patch = _require_mapping(
            raw_patch, code="INVALID_PATCH", message=f"patch {patch_number} must be an object"
        )
        operation = patch.get("operation")
        if operation in {"delete", "remove", "delete_row"}:
            raise ReviewPackageError("DELETE_PATCH_REJECTED", "patches may not delete source occurrences")
        if operation not in {"replace_fields", "append", "append_record"}:
            raise ReviewPackageError("INVALID_PATCH_OPERATION", "patch operation is unsupported")
        if operation in {"append", "append_record"}:
            record = patch.get("append_record")
            if not isinstance(record, dict):
                raise ReviewPackageError("INVALID_PATCH", "append requires append_record")
            if allowed_fields is not None and not set(record).issubset(allowed_fields):
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", "append contains an unauthorized field")
            stable_key = patch.get("stable_key", {})
            if not isinstance(stable_key, dict) or any(record.get(key) != value for key, value in stable_key.items()):
                raise ReviewPackageError("PATCH_KEY_MISMATCH", "append stable key does not match record")
            if patch.get("before_record_sha256") is not None:
                raise ReviewPackageError("INVALID_PATCH", "append before hash must be null")
            target = ("append", tuple((key, _canonical_json(value)) for key, value in sorted(record.items())))
            if target in touched:
                raise ReviewPackageError("DUPLICATE_PATCH", "the same append appears more than once")
            touched.add(target)
            expected_after = _require_sha256(patch.get("after_record_sha256"), path=f"patch[{patch_number}]")
            if canonical_record_sha256(record) != expected_after:
                raise ReviewPackageError("PATCH_AFTER_HASH_MISMATCH", "append record hash differs")
            if any(canonical_record_sha256(existing) == expected_after for existing in rows):
                already_applied += 1
            else:
                rows.append(copy.deepcopy(record))
                applied += 1
            continue

        row_number = patch.get("base_row_number_1based")
        if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number <= 0 or row_number > len(rows):
            raise ReviewPackageError("PATCH_ROW_OUT_OF_RANGE", "patch row number is invalid")
        target = ("row", row_number)
        if target in touched:
            raise ReviewPackageError("DUPLICATE_PATCH", "more than one patch targets a base row")
        touched.add(target)
        row = rows[row_number - 1]
        stable_key = patch.get("stable_key", {})
        if not isinstance(stable_key, dict):
            raise ReviewPackageError("INVALID_PATCH", "stable_key must be an object")
        if any(field not in row or row[field] != value for field, value in stable_key.items()):
            raise ReviewPackageError("PATCH_KEY_MISMATCH", "stable key does not match base row")
        before_hash = _require_sha256(patch.get("before_record_sha256"), path=f"patch[{patch_number}]")
        after_hash = _require_sha256(patch.get("after_record_sha256"), path=f"patch[{patch_number}]")
        changes = _require_sequence(
            patch.get("changes"), code="INVALID_PATCH", message="replace_fields requires changes"
        )
        normalized_changes: list[dict[str, Any]] = []
        changed_fields: set[str] = set()
        for raw_change in changes:
            change = _require_mapping(raw_change, code="INVALID_PATCH", message="change must be an object")
            field_name = change.get("field")
            if not isinstance(field_name, str) or not field_name or field_name in changed_fields:
                raise ReviewPackageError("DUPLICATE_PATCH_FIELD", "changed fields must be unique and nonblank")
            changed_fields.add(field_name)
            if allowed_fields is not None and field_name not in allowed_fields:
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", "patch changes an unauthorized field")
            if not isinstance(change.get("before_present"), bool) or not isinstance(change.get("after_present"), bool):
                raise ReviewPackageError("INVALID_PATCH", "presence flags must be boolean")
            normalized_changes.append(change)
        observed_hash = canonical_record_sha256(row)
        if observed_hash == after_hash:
            for change in normalized_changes:
                field_name = change["field"]
                after_present = change["after_present"]
                if (field_name in row) != after_present or (after_present and row[field_name] != change.get("after")):
                    raise ReviewPackageError("PATCH_AFTER_VALUE_MISMATCH", "idempotent row differs from declared after-value")
            already_applied += 1
            continue
        if observed_hash != before_hash:
            raise ReviewPackageError("PATCH_BASELINE_MISMATCH", "base record hash is not the declared before hash")
        for change in normalized_changes:
            field_name = change["field"]
            before_present = change.get("before_present")
            after_present = change.get("after_present")
            if not isinstance(before_present, bool) or not isinstance(after_present, bool):
                raise ReviewPackageError("INVALID_PATCH", "presence flags must be boolean")
            if (field_name in row) != before_present or (before_present and row[field_name] != change.get("before")):
                raise ReviewPackageError("PATCH_BEFORE_VALUE_MISMATCH", "field before-value differs")
            if after_present:
                row[field_name] = copy.deepcopy(change.get("after"))
            else:
                row.pop(field_name, None)
        if canonical_record_sha256(row) != after_hash:
            raise ReviewPackageError("PATCH_AFTER_HASH_MISMATCH", "patched record hash differs")
        applied += 1
    result_hash = canonical_jsonl_sha256(rows)
    if expected_rows is not None and len(rows) != expected_rows:
        raise ReviewPackageError("PATCH_ROW_COUNT_MISMATCH", "effective row count differs")
    if expected_sha256 is not None and result_hash != _require_sha256(expected_sha256, path="effective table"):
        raise ReviewPackageError("PATCH_RESULT_HASH_MISMATCH", "effective canonical JSONL hash differs")
    return PatchReplay(tuple(rows), applied, already_applied, result_hash)


_DELTA_JSONL_TABLES = {
    "affected_variant_records.jsonl": "affected_variants",
    "affected_candidate_records.jsonl": "affected_candidates",
    "dependency_groups.jsonl": "dependency_groups",
    "normalized_price_contract_links_delta.jsonl": "normalized_price_contract_links_delta",
    "normalized_price_contract_review_delta.jsonl": "normalized_price_contract_review_delta",
    "owner_decisions_added.jsonl": "owner_decisions_added",
    "owner_question_batch_effective.jsonl": "owner_question_batch_effective",
    "preserved_target_identity_evidence.jsonl": "preserved_target_identity_evidence",
}

_REQUIRED_DELTA_FILES = frozenset(
    {
        "README_and_Data_Dictionary.md",
        "baseline_preconditions.json",
        "changed_table_hashes.json",
        "validation.json",
        "companion_record_patches.jsonl",
        "normalized_price_contract_review_delta.csv",
        "normalized_price_contract_review_delta_csv_types.jsonl",
        *_DELTA_JSONL_TABLES.keys(),
    }
)


def _read_real_delta(
    source: _PackageSource,
    *,
    manifest_name: str,
    baseline_path: str | Path | None,
) -> ReviewPackage:
    manifest_bytes = _read_bounded(source, manifest_name, source.limits.max_manifest_bytes)
    manifest = _parse_json_bytes(manifest_bytes, path=manifest_name, limits=source.limits)
    declared, verified = _manifest_files(
        source, manifest, manifest_name=manifest_name, require_complete=True
    )
    missing_required = sorted(_REQUIRED_DELTA_FILES - set(declared))
    if missing_required:
        raise ReviewPackageError(
            "INCOMPLETE_DELTA_PACKAGE",
            "required V4.1 review evidence is absent",
            path=missing_required[0],
        )
    preconditions = _require_mapping(
        _parse_json_bytes(
            _read_verified_bounded(source, "baseline_preconditions.json", source.limits.max_manifest_bytes, declared),
            path="baseline_preconditions.json",
            limits=source.limits,
        ),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="baseline preconditions must be an object",
    )
    precondition_rows = _require_sequence(
        preconditions.get("tables"),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="baseline tables must be a nonempty array",
    )
    if not precondition_rows:
        raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline table list is empty")
    changed_tables = _require_sequence(
        _parse_json_bytes(
            _read_verified_bounded(source, "changed_table_hashes.json", source.limits.max_manifest_bytes, declared),
            path="changed_table_hashes.json",
            limits=source.limits,
        ),
        code="INVALID_CHANGED_TABLE_HASHES",
        message="changed table hashes must be a nonempty array",
    )
    if not changed_tables:
        raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "changed table list is empty")
    precondition_names = {
        item.get("table")
        for item in precondition_rows
        if isinstance(item, dict) and isinstance(item.get("table"), str)
    }
    for raw_changed in changed_tables:
        changed = _require_mapping(
            raw_changed, code="INVALID_CHANGED_TABLE_HASHES", message="changed table record must be an object"
        )
        table_name = changed.get("table")
        patch_path = changed.get("patch_file")
        if table_name not in precondition_names or not isinstance(patch_path, str):
            raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "changed table lacks a baseline precondition")
        patch_path = _validated_member_name(patch_path, source.limits)
        if patch_path not in declared:
            raise ReviewPackageError("INCOMPLETE_DELTA_PACKAGE", "declared patch file is absent", path=patch_path)
        if changed.get("patch_file_sha256") != declared[patch_path][1]:
            raise ReviewPackageError("PATCH_FILE_HASH_MISMATCH", "patch hash evidence differs", path=patch_path)
        patch_rows, patch_raw_sha = _read_jsonl(source, patch_path)
        if patch_raw_sha != declared[patch_path][1]:
            raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "patch bytes changed", path=patch_path)
        if len(patch_rows) != changed.get("patched_or_appended_records"):
            raise ReviewPackageError("PATCH_ROW_COUNT_MISMATCH", "patch record count differs", path=patch_path)
        seen_targets: set[tuple[Any, Any]] = set()
        for patch in patch_rows:
            if patch.get("table") != table_name or patch.get("operation") not in {"replace_fields", "append_record"}:
                raise ReviewPackageError("INVALID_PATCH", "patch table or operation differs", path=patch_path)
            target = (
                patch.get("operation"),
                canonical_record_sha256(patch.get("append_record", {}))
                if patch.get("operation") == "append_record"
                else patch.get("base_row_number_1based"),
            )
            if target in seen_targets:
                raise ReviewPackageError("DUPLICATE_PATCH", "patch target is duplicated", path=patch_path)
            seen_targets.add(target)
    tables: dict[str, PackageTable] = {}
    issues: list[ValidationIssue] = []
    for path, table_name in _DELTA_JSONL_TABLES.items():
        if path not in declared:
            continue
        rows, raw_sha = _read_jsonl(source, path)
        if raw_sha != declared[path][1]:
            raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "JSONL bytes differ", path=path)
        tables[table_name] = PackageTable(
            name=table_name,
            path=path,
            format="jsonl",
            rows=tuple(rows),
            raw_sha256=raw_sha,
            canonical_jsonl_sha256=canonical_jsonl_sha256(rows),
        )
    json_name = "normalized_price_contract_review_delta.jsonl"
    csv_name = "normalized_price_contract_review_delta.csv"
    sidecar_name = "normalized_price_contract_review_delta_csv_types.jsonl"
    if all(name in declared for name in (json_name, csv_name, sidecar_name)):
        csv_rows, csv_sha = _read_typed_csv(
            source,
            csv_name,
            sidecar_name=sidecar_name,
            expected_fields=PRICE_BOOK_HEADERS,
            expected_raw_sha256=declared[csv_name][1],
            expected_sidecar_sha256=declared[sidecar_name][1],
        )
        json_rows = tables["normalized_price_contract_review_delta"].rows
        if tuple(csv_rows) != json_rows:
            raise ReviewPackageError(
                "TYPED_CSV_JSONL_MISMATCH", "typed CSV does not reproduce the canonical JSONL rows", path=csv_name
            )
        tables["normalized_price_contract_review_delta_csv"] = PackageTable(
            name="normalized_price_contract_review_delta_csv",
            path=csv_name,
            format="csv",
            rows=tuple(csv_rows),
            raw_sha256=csv_sha,
            canonical_jsonl_sha256=canonical_jsonl_sha256(csv_rows),
        )
    validation: dict[str, Any] = {}
    if "validation.json" in declared:
        validation = _require_mapping(
            _parse_json_bytes(
                _read_verified_bounded(source, "validation.json", source.limits.max_manifest_bytes, declared),
                path="validation.json",
                limits=source.limits,
            ),
            code="INVALID_VALIDATION",
            message="validation evidence must be an object",
        )
        for field_name in ("mapping_approvals", "current_price_approvals", "import_ready_rows"):
            if validation.get(field_name) != 0:
                raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", f"{field_name} must remain zero")
    unavailable = []
    if baseline_path is None:
        unavailable.extend(("EXACT_V4_BASELINE_ARCHIVE", "ORIGINAL_SUPPLIER_PDFS"))
    else:
        # The large baseline can be checked without loading whole tables.  Full
        # replay is possible only when every exact precondition path is present.
        with _open_source(baseline_path, source.limits) as baseline:
            missing = []
            base_specs: dict[str, dict[str, Any]] = {}
            for raw in precondition_rows:
                item = _require_mapping(
                    raw, code="INVALID_BASELINE_PRECONDITIONS", message="baseline table must be an object"
                )
                table_name = item.get("table")
                base_file = item.get("file")
                if not isinstance(table_name, str) or not isinstance(base_file, str):
                    raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline file is invalid")
                base_name = _validated_member_name(base_file, source.limits)
                base_specs[table_name] = dict(item)
                if base_name not in baseline.names:
                    missing.append(base_name)
                    continue
                actual_sha, _ = _stream_sha256(baseline, base_name)
                if actual_sha != _require_sha256(item.get("sha256"), path=base_name):
                    raise ReviewPackageError("BASELINE_HASH_MISMATCH", "baseline table bytes differ", path=base_name)
            if missing:
                unavailable.append("INCOMPLETE_EXACT_V4_BASELINE:" + ",".join(sorted(missing)))
            else:
                for raw_changed in changed_tables:
                    changed = dict(raw_changed)
                    table_name = changed["table"]
                    spec = base_specs[table_name]
                    base_name = _validated_member_name(spec["file"], source.limits)
                    base_rows, base_raw_sha = _read_jsonl(baseline, base_name)
                    if base_raw_sha != spec["sha256"] or len(base_rows) != spec.get("rows"):
                        raise ReviewPackageError("BASELINE_TABLE_MISMATCH", "baseline rows or bytes differ", path=base_name)
                    patch_name = changed["patch_file"]
                    patch_rows, _ = _read_jsonl(source, patch_name)
                    allowed = {
                        change["field"]
                        for patch in patch_rows
                        for change in patch.get("changes", [])
                        if isinstance(change, dict) and isinstance(change.get("field"), str)
                    }
                    replay = replay_patch_table(
                        base_rows,
                        patch_rows,
                        allowed_fields=allowed,
                        expected_rows=changed.get("effective_table_rows"),
                        expected_sha256=changed.get("effective_table_canonical_jsonl_sha256"),
                    )
                    second = replay_patch_table(
                        replay.rows,
                        patch_rows,
                        allowed_fields=allowed,
                        expected_rows=changed.get("effective_table_rows"),
                        expected_sha256=changed.get("effective_table_canonical_jsonl_sha256"),
                    )
                    if second.applied or second.rows != replay.rows:
                        raise ReviewPackageError("PATCH_NOT_IDEMPOTENT", "effective table changed on replay")
                    tables[f"effective_{table_name}"] = PackageTable(
                        name=f"effective_{table_name}",
                        path=base_name,
                        format="jsonl",
                        rows=replay.rows,
                        raw_sha256=base_raw_sha,
                        canonical_jsonl_sha256=replay.canonical_jsonl_sha256,
                    )
            unavailable.append("ORIGINAL_SUPPLIER_PDFS")
    issues.append(
        ValidationIssue(
            "STRUCTURAL_VALIDITY_IS_NOT_APPROVAL",
            "Hash and shape checks do not approve mappings, prices, or purchasing use.",
            severity="INFO",
        )
    )
    cohorts = {
        key: validation.get(key)
        for key in (
            "original_variants",
            "current_census",
            "current_additions",
            "original_not_returned",
            "not_returned_is_deletion",
            "candidate_occurrences",
            "candidate_disposition_counts",
        )
        if key in validation
    }
    return ReviewPackage(
        source=str(source.path),
        package_kind="V4_1_CLARIFICATION_DELTA",
        snapshot_id="V4.1-CLARIFICATION-DELTA",
        status="BASELINE_REQUIRED" if unavailable else "PASS",
        label=REVIEW_LABEL,
        file_count=len(source.names),
        verified_file_count=verified,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        tables=tables,
        cohorts=cohorts,
        issues=tuple(issues),
        unavailable_evidence=tuple(unavailable),
        metadata={
            "validation": validation,
            "raw_hash_basis": RAW_HASH,
            "representation_gaps": [
                {
                    "status": "COMPANION_WORKBOOK_REQUIRED",
                    "declared_count": 21,
                    "handling": "REVIEW_ONLY_SIDECAR_OR_LATER_SCHEMA_PROPOSAL",
                }
            ],
        },
    )


def _read_v1(source: _PackageSource, *, manifest_name: str) -> ReviewPackage:
    manifest_bytes = _read_bounded(source, manifest_name, source.limits.max_manifest_bytes)
    manifest = _require_mapping(
        _parse_json_bytes(manifest_bytes, path=manifest_name, limits=source.limits),
        code="INVALID_MANIFEST",
        message="package manifest must be an object",
        path=manifest_name,
    )
    if manifest.get("format") != PACKAGE_FORMAT:
        raise ReviewPackageError("UNSUPPORTED_PACKAGE_VERSION", "review-package format is unsupported")
    if manifest.get("authority") != "REVIEW_ONLY":
        raise ReviewPackageError("INVALID_AUTHORITY", "package authority must be REVIEW_ONLY")
    snapshot_id = manifest.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ReviewPackageError("INVALID_MANIFEST", "snapshot_id must be nonblank")
    declared, verified = _manifest_files(
        source, manifest.get("files"), manifest_name=manifest_name, require_complete=True
    )
    tables: dict[str, PackageTable] = {}
    for raw in _require_sequence(
        manifest.get("tables"), code="INVALID_MANIFEST", message="tables must be an array", path=manifest_name
    ):
        spec = _require_mapping(raw, code="INVALID_TABLE_SPEC", message="table spec must be an object")
        table = _read_declared_table(source, spec, declared=declared)
        if table.name in tables:
            raise ReviewPackageError("DUPLICATE_TABLE", "table name is duplicated", table=table.name)
        tables[table.name] = table
    _validate_joins(tables, manifest.get("joins", []))
    issues = []
    for table in tables.values():
        if table.unknown_fields:
            issues.append(
                ValidationIssue(
                    "UNKNOWN_FIELDS",
                    "Unknown fields were retained: " + ", ".join(table.unknown_fields),
                    table=table.name,
                    path=table.path,
                )
            )
    cohorts = manifest.get("cohorts", {})
    if not isinstance(cohorts, dict):
        raise ReviewPackageError("INVALID_MANIFEST", "cohorts must be an object")
    return ReviewPackage(
        source=str(source.path),
        package_kind=PACKAGE_FORMAT,
        snapshot_id=snapshot_id,
        status="PASS",
        label=REVIEW_LABEL,
        file_count=len(source.names),
        verified_file_count=verified,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        tables=tables,
        cohorts=cohorts,
        issues=tuple(issues),
        unavailable_evidence=(),
        metadata={"raw_hash_basis": RAW_HASH, "canonical_hash_basis": CANONICAL_JSONL_HASH},
    )


def read_review_package(
    path: str | Path,
    *,
    baseline_path: str | Path | None = None,
    limits: ReviewLimits = DEFAULT_LIMITS,
) -> ReviewPackage:
    """Read and validate a versioned offline package without extracting it."""

    with _open_source(path, limits) as source:
        v1_names = [name for name in ("REVIEW_PACKAGE_MANIFEST.json", "review_package_manifest.json") if name in source.names]
        if len(v1_names) > 1:
            raise ReviewPackageError("AMBIGUOUS_MANIFEST", "multiple review-package manifests are present")
        if v1_names:
            return _read_v1(source, manifest_name=v1_names[0])
        if "PACKAGE_FILE_HASHES.json" in source.names:
            return _read_real_delta(source, manifest_name="PACKAGE_FILE_HASHES.json", baseline_path=baseline_path)
        raise ReviewPackageError("MISSING_MANIFEST", "package has no recognized versioned manifest")
