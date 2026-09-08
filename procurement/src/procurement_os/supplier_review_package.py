"""Bounded, read-only supplier mapping review-package handling.

This module is deliberately separate from :mod:`price_book`.  It reads review
evidence only; it cannot stage or promote operational prices and it never
connects to a database.  Real review rows remain unapproved and not import
ready regardless of structural validity.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import copy
import csv
from dataclasses import dataclass, field
from datetime import date
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
_MAX_CANONICAL_NUMBER_BYTES = 4_096


class ReviewPackageError(ValueError):
    """A fail-closed review-package validation error with a stable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        table: str | None = None,
        row: int | None = None,
    ):
        self.code = code
        self.path = path
        self.table = table
        self.row = row
        location = path or table
        suffix = f" ({location}{'' if row is None else f':{row}'})" if location else ""
        super().__init__(f"{code}: {message}{suffix}")


@dataclass(frozen=True)
class ReviewLimits:
    """Explicit bounds for large offline review evidence.

    These limits are intentionally independent of the operational price-book
    import's immutable 5,000,000-byte boundary.
    """

    max_entries: int = 2_048
    max_entry_bytes: int = 450_000_000
    # The exact frozen V4 baseline manifest totals 2,557,725,798 expanded
    # bytes.  Keep the offline reader bounded while allowing that reviewed
    # archive through the default library and CLI path.
    max_total_expanded_bytes: int = 2_600_000_000
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
    maximum = _depth
    pending: list[tuple[Any, int]] = [(value, _depth)]
    while pending:
        item, depth = pending.pop()
        maximum = max(maximum, depth)
        if isinstance(item, Mapping):
            pending.extend((nested, depth + 1) for nested in item.values())
        elif isinstance(item, list):
            pending.extend((nested, depth + 1) for nested in item)
    return maximum


def _parse_json_bytes(data: bytes, *, path: str, limits: ReviewLimits) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewPackageError("INVALID_UTF8", "JSON must be UTF-8", path=path) from exc
    try:
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON constant {token}")
            ),
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, InvalidOperation, ValueError, RecursionError) as exc:
        raise ReviewPackageError("INVALID_JSON", "JSON is malformed", path=path) from exc
    if _json_depth(value) > limits.max_json_depth:
        raise ReviewPackageError("JSON_TOO_DEEP", "JSON nesting exceeds limit", path=path)
    _validate_json_value_bounds(value, path=path, limits=limits)
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


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
        if value.bit_length() > _MAX_CANONICAL_NUMBER_BYTES * 4:
            raise ReviewPackageError("NUMBER_TOO_LARGE", "integer exceeds the canonical numeric bound")
        encoded = str(value).encode("ascii")
        if len(encoded) > _MAX_CANONICAL_NUMBER_BYTES:
            raise ReviewPackageError("NUMBER_TOO_LARGE", "integer exceeds the canonical numeric bound")
        return encoded
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ReviewPackageError("NONFINITE_NUMBER", "JSON decimals must be finite")
        sign, digits, exponent = value.as_tuple()
        if len(digits) > _MAX_CANONICAL_NUMBER_BYTES:
            raise ReviewPackageError("NUMBER_TOO_LARGE", "decimal exceeds the canonical numeric bound")
        if exponent >= 0:
            fixed_length = sign + len(digits) + exponent
        elif len(digits) + exponent > 0:
            fixed_length = sign + len(digits) + 1
        else:
            fixed_length = sign + 2 - exponent
        if fixed_length > _MAX_CANONICAL_NUMBER_BYTES:
            raise ReviewPackageError("NUMBER_TOO_LARGE", "decimal exceeds the canonical numeric bound")
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


def _validate_json_value_bounds(value: Any, *, path: str, limits: ReviewLimits) -> None:
    if isinstance(value, str):
        if len(value.encode("utf-8")) > limits.max_field_bytes:
            raise ReviewPackageError("FIELD_TOO_LARGE", "nested string exceeds the field limit", path=path)
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ReviewPackageError("INVALID_JSON_KEY", "row keys must be strings", path=path)
            if len(key.encode("utf-8")) > limits.max_field_bytes:
                raise ReviewPackageError("FIELD_TOO_LARGE", "JSON key exceeds the field limit", path=path)
            _validate_json_value_bounds(nested, path=path, limits=limits)
        return
    if isinstance(value, list):
        for nested in value:
            _validate_json_value_bounds(nested, path=path, limits=limits)
        return
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        _canonical_json(value)


def _checked_record(record: Any, *, path: str, row: int, limits: ReviewLimits) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ReviewPackageError("ROW_NOT_OBJECT", "JSONL row must be an object", path=path)
    if len(record) > limits.max_fields_per_record:
        raise ReviewPackageError("TOO_MANY_FIELDS", "row field count exceeds limit", path=path)
    if _json_depth(record) > limits.max_json_depth:
        raise ReviewPackageError("JSON_TOO_DEEP", f"row {row} nesting exceeds limit", path=path)
    _validate_json_value_bounds(record, path=path, limits=limits)
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
        if raw != "":
            raise ReviewPackageError(
                "TYPE_SIDECAR_MISMATCH",
                f"absent {field_name} must have an empty CSV cell",
                path=path,
            )
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
            value = json.loads(
                raw,
                parse_float=Decimal,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON constant {token}")
                ),
                object_pairs_hook=_unique_json_object,
            )
        except (json.JSONDecodeError, InvalidOperation, ValueError, RecursionError) as exc:
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
    append_allowed_fields: set[str] | None = None,
    required_append_fields: set[str] | None = None,
    expected_stable_key_fields: Sequence[str] | None = None,
    key_mode: str = "BASE",
    external_keys_by_row: Mapping[int, Mapping[str, Any]] | None = None,
    immutable_fields: set[str] | None = None,
    allow_field_removal: bool = True,
    expected_rows: int | None = None,
    expected_sha256: str | None = None,
) -> PatchReplay:
    """Apply an exact, non-destructive row patch and prove idempotent replay.

    ``key_mode`` is deliberately explicit.  Ordinary patches use ``BASE`` and
    must carry a nonempty key that already identifies the baseline row.
    ``POSTCONDITION_WITH_EXTERNAL_BASE`` is reserved for a reviewed format
    whose declared key is added by the patch; an independently supplied,
    nonempty key must identify the baseline row.  ``EXTERNAL_PROOF`` is for a
    row whose identity lives only in another validated table (the V4.1
    normalized Tier link); the patch's own key must be empty and a nonempty
    external proof is mandatory.  The caller must validate that companion
    proof against its own baseline row because its fields are deliberately not
    copied into the normalized 27-column row.  Row number and before-record
    hash remain material in every mode.

    Append idempotency is keyed by the declared stable key, never by a whole-
    record hash.  A same-key/different-record append is therefore a conflict,
    not a second occurrence.
    """

    if key_mode not in {"BASE", "POSTCONDITION_WITH_EXTERNAL_BASE", "EXTERNAL_PROOF"}:
        raise ValueError("unsupported patch key mode")
    expected_key_fields = None if expected_stable_key_fields is None else tuple(expected_stable_key_fields)
    if expected_key_fields is not None and (
        not expected_key_fields or len(set(expected_key_fields)) != len(expected_key_fields)
    ):
        raise ValueError("expected stable-key fields must be unique and nonempty")
    replace_allowed = None if allowed_fields is None else set(allowed_fields)
    append_allowed = (
        replace_allowed if append_allowed_fields is None else set(append_allowed_fields)
    )
    append_required = set(required_append_fields or ())
    protected = set(immutable_fields or ())

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
        stable_key = patch.get("stable_key")
        if not isinstance(stable_key, dict):
            raise ReviewPackageError("INVALID_PATCH", "stable_key must be an object")
        if expected_key_fields is not None and tuple(sorted(stable_key)) != tuple(sorted(expected_key_fields)):
            raise ReviewPackageError("PATCH_KEY_FIELDS_MISMATCH", "stable-key fields differ from the code-owned contract")
        if operation in {"append", "append_record"}:
            if append_allowed is None:
                raise ReviewPackageError("PATCH_POLICY_REQUIRED", "append field authority must be supplied independently")
            record = patch.get("append_record")
            if not isinstance(record, dict):
                raise ReviewPackageError("INVALID_PATCH", "append requires append_record")
            if not stable_key:
                raise ReviewPackageError("EMPTY_PATCH_KEY", "append requires a nonempty stable key")
            if append_allowed is not None and not set(record).issubset(append_allowed):
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", "append contains an unauthorized field")
            if not append_required.issubset(record):
                raise ReviewPackageError("PATCH_REQUIRED_FIELD_MISSING", "append omits a code-required field")
            if any(key not in record or record[key] != value for key, value in stable_key.items()):
                raise ReviewPackageError("PATCH_KEY_MISMATCH", "append stable key does not match record")
            if patch.get("before_record_sha256") is not None:
                raise ReviewPackageError("INVALID_PATCH", "append before hash must be null")
            changes = _require_sequence(
                patch.get("changes"), code="INVALID_PATCH", message="append requires changes"
            )
            changed_fields: set[str] = set()
            for raw_change in changes:
                change = _require_mapping(raw_change, code="INVALID_PATCH", message="change must be an object")
                if set(change) != {"field", "before_present", "before", "after_present", "after"}:
                    raise ReviewPackageError("INVALID_PATCH", "change fields differ from the exact patch contract")
                field_name = change.get("field")
                if not isinstance(field_name, str) or not field_name or field_name in changed_fields:
                    raise ReviewPackageError("DUPLICATE_PATCH_FIELD", "changed fields must be unique and nonblank")
                changed_fields.add(field_name)
                if (
                    change.get("before_present") is not False
                    or change.get("before") is not None
                    or change.get("after_present") is not True
                    or field_name not in record
                    or change.get("after") != record[field_name]
                ):
                    raise ReviewPackageError("PATCH_APPEND_CHANGE_MISMATCH", "append changes do not exactly describe append_record")
            if changed_fields != set(record):
                raise ReviewPackageError("PATCH_APPEND_CHANGE_MISMATCH", "append changes and append_record fields differ")
            target = ("append", tuple((key, _canonical_json(value)) for key, value in sorted(stable_key.items())))
            if target in touched:
                raise ReviewPackageError("DUPLICATE_PATCH", "the same append stable key appears more than once")
            touched.add(target)
            expected_after = _require_sha256(patch.get("after_record_sha256"), path=f"patch[{patch_number}]")
            if canonical_record_sha256(record) != expected_after:
                raise ReviewPackageError("PATCH_AFTER_HASH_MISMATCH", "append record hash differs")
            existing_matches = [
                existing
                for existing in rows
                if all(key in existing and existing[key] == value for key, value in stable_key.items())
            ]
            if len(existing_matches) > 1:
                raise ReviewPackageError("DUPLICATE_BASE_KEY", "append stable key is already duplicated")
            if existing_matches and canonical_record_sha256(existing_matches[0]) != expected_after:
                raise ReviewPackageError("PATCH_APPEND_KEY_CONFLICT", "append stable key already has different facts")
            if existing_matches:
                already_applied += 1
            else:
                rows.append(copy.deepcopy(record))
                applied += 1
            continue

        row_number = patch.get("base_row_number_1based")
        if replace_allowed is None:
            raise ReviewPackageError("PATCH_POLICY_REQUIRED", "replace field authority must be supplied independently")
        if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number <= 0 or row_number > len(rows):
            raise ReviewPackageError("PATCH_ROW_OUT_OF_RANGE", "patch row number is invalid")
        target = ("row", row_number)
        if target in touched:
            raise ReviewPackageError("DUPLICATE_PATCH", "more than one patch targets a base row")
        touched.add(target)
        row = rows[row_number - 1]
        external_key = None if external_keys_by_row is None else external_keys_by_row.get(row_number)
        if key_mode == "BASE":
            if not stable_key:
                raise ReviewPackageError("EMPTY_PATCH_KEY", "replace requires a nonempty stable key")
        elif key_mode == "POSTCONDITION_WITH_EXTERNAL_BASE":
            if not stable_key or not isinstance(external_key, Mapping) or not external_key:
                raise ReviewPackageError("MISSING_EXTERNAL_PATCH_KEY", "postcondition key requires external baseline identity")
        elif stable_key or not isinstance(external_key, Mapping) or not external_key:
            raise ReviewPackageError("MISSING_EXTERNAL_PATCH_KEY", "empty source key requires external Tier identity proof")
        before_hash = _require_sha256(patch.get("before_record_sha256"), path=f"patch[{patch_number}]")
        after_hash = _require_sha256(patch.get("after_record_sha256"), path=f"patch[{patch_number}]")
        changes = _require_sequence(
            patch.get("changes"), code="INVALID_PATCH", message="replace_fields requires changes"
        )
        normalized_changes: list[dict[str, Any]] = []
        changed_fields: set[str] = set()
        for raw_change in changes:
            change = _require_mapping(raw_change, code="INVALID_PATCH", message="change must be an object")
            if set(change) != {"field", "before_present", "before", "after_present", "after"}:
                raise ReviewPackageError("INVALID_PATCH", "change fields differ from the exact patch contract")
            field_name = change.get("field")
            if not isinstance(field_name, str) or not field_name or field_name in changed_fields:
                raise ReviewPackageError("DUPLICATE_PATCH_FIELD", "changed fields must be unique and nonblank")
            changed_fields.add(field_name)
            if replace_allowed is not None and field_name not in replace_allowed:
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", "patch changes an unauthorized field")
            if field_name in protected:
                raise ReviewPackageError("PATCH_IDENTITY_FIELD_MUTATION", "patch changes a protected identity field")
            if not isinstance(change.get("before_present"), bool) or not isinstance(change.get("after_present"), bool):
                raise ReviewPackageError("INVALID_PATCH", "presence flags must be boolean")
            if not allow_field_removal and change.get("after_present") is False:
                raise ReviewPackageError("PATCH_FIELD_REMOVAL_REJECTED", "this patch contract may not remove fields")
            normalized_changes.append(change)
        if key_mode == "BASE" and changed_fields.intersection(stable_key):
            raise ReviewPackageError("PATCH_IDENTITY_FIELD_MUTATION", "patch changes its stable key")
        if key_mode == "POSTCONDITION_WITH_EXTERNAL_BASE" and not set(stable_key).issubset(changed_fields):
            raise ReviewPackageError("PATCH_POSTCONDITION_KEY_MISMATCH", "postcondition key is not assigned by the patch")
        observed_hash = canonical_record_sha256(row)
        if key_mode == "BASE" and any(field not in row or row[field] != value for field, value in stable_key.items()):
            raise ReviewPackageError("PATCH_KEY_MISMATCH", "stable key does not match row")
        if key_mode == "POSTCONDITION_WITH_EXTERNAL_BASE" and any(
            field not in row or row[field] != value for field, value in external_key.items()
        ):
            raise ReviewPackageError("PATCH_EXTERNAL_KEY_MISMATCH", "external identity does not match row")
        if observed_hash == after_hash:
            for change in normalized_changes:
                field_name = change["field"]
                after_present = change["after_present"]
                if (field_name in row) != after_present or (after_present and row[field_name] != change.get("after")):
                    raise ReviewPackageError("PATCH_AFTER_VALUE_MISMATCH", "idempotent row differs from declared after-value")
            if key_mode == "POSTCONDITION_WITH_EXTERNAL_BASE" and any(
                field not in row or row[field] != value for field, value in stable_key.items()
            ):
                raise ReviewPackageError("PATCH_POSTCONDITION_KEY_MISMATCH", "declared postcondition key does not match row")
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
        if key_mode == "POSTCONDITION_WITH_EXTERNAL_BASE" and any(
            field not in row or row[field] != value for field, value in stable_key.items()
        ):
            raise ReviewPackageError("PATCH_POSTCONDITION_KEY_MISMATCH", "declared postcondition key does not match result")
        if canonical_record_sha256(row) != after_hash:
            raise ReviewPackageError("PATCH_AFTER_HASH_MISMATCH", "patched record hash differs")
        applied += 1
    result_hash = canonical_jsonl_sha256(rows)
    if expected_rows is not None and len(rows) != expected_rows:
        raise ReviewPackageError("PATCH_ROW_COUNT_MISMATCH", "effective row count differs")
    if expected_sha256 is not None and result_hash != _require_sha256(expected_sha256, path="effective table"):
        raise ReviewPackageError("PATCH_RESULT_HASH_MISMATCH", "effective canonical JSONL hash differs")
    return PatchReplay(tuple(rows), applied, already_applied, result_hash)


def _field_set(value: str) -> frozenset[str]:
    return frozenset(value.split())


@dataclass(frozen=True)
class _V4PatchContract:
    operations: frozenset[str]
    stable_key_fields: tuple[str, ...]
    replace_fields: frozenset[str] = frozenset()
    append_fields: frozenset[str] = frozenset()
    required_append_fields: frozenset[str] = frozenset()
    key_mode: str = "BASE"
    baseline_rows: int = 0
    patch_records: int = 0
    effective_rows: int = 0


_REJECTED_ALTERNATIVE_APPEND_FIELDS = _field_set(
    """
    approval_status authoritative_catalog_status before_candidate_disposition
    candidate_disposition captured_at catalog_barcode catalog_component_size_ml
    catalog_exclusion_or_conflict_blocks_routine_purchase catalog_retail_pack_units
    catalog_vintage code_numeric compatible_attributes conversion_basis
    current_planning_incoming_units hard_conflicts historical_owner_incoming_attestation
    incoming_planning_scenario incoming_receipt_verification_status legacy_confidence
    legacy_mapping_source legacy_offer_id mapping_status mapping_status_semantics
    match_method name_rank_score new_occurrence_candidate_record
    new_occurrence_from_existing_source offer_id offer_type owner_incoming_attestation_date
    physical_page physical_units_per_supplier_case policy_excluded policy_exclusion_reason
    printed_page product_id product_title proposed_shopify_sellable_units_per_case
    quantity_or_import_eligible raw_evidence review_id review_requirements
    review_revision_precedence reviewed_supplier_title routine_purchase_candidate
    shopify_sku shopify_vendor source_extraction_issues source_file source_sha256 supplier
    supplier_case_pack supplier_code supplier_order_unit supplier_parenthetical_marker_raw
    supplier_priority supplier_qualifying_unit supplier_qualifying_units_per_case
    supplier_retail_pack supplier_size_ml supplier_size_raw supplier_title supplier_vintage
    supplier_vintage_raw territory_conflict v1_candidate_disposition v1_mapping_status
    v2_candidate_disposition v2_mapping_status v2_nonselection_reason v2_review_status
    v2_reviewed_at v3_candidate_disposition v3_hard_conflicts_preserved v3_mapping_status
    v3_review_requirements_preserved v3_review_status v3_reviewed_at
    v3_source_attributes_preserved v3_source_title_raw v4_1_owner_decision_id v4_1_reason
    v4_1_review_note v4_1_review_status v4_conversion_review_status v4_investigation_completed
    v4_issue_type v4_original_candidate_key_preserved v4_patch_reference v4_reason
    v4_required_identity_dependencies v4_review_status v4_reviewed_at v4_scope_limits
    v4_source_overlay_ids v4_valid_tier_ids variant_gid variant_id variant_title
    verified_current_price
    """
)

_REJECTED_ALTERNATIVE_REQUIRED_FIELDS = _field_set(
    """
    approval_status authoritative_catalog_status before_candidate_disposition
    candidate_disposition captured_at catalog_exclusion_or_conflict_blocks_routine_purchase
    compatible_attributes conversion_basis hard_conflicts legacy_mapping_source legacy_offer_id
    mapping_status mapping_status_semantics match_method offer_id offer_type physical_page
    physical_units_per_supplier_case policy_excluded printed_page product_id product_title
    proposed_shopify_sellable_units_per_case quantity_or_import_eligible raw_evidence
    review_requirements review_revision_precedence reviewed_supplier_title
    routine_purchase_candidate shopify_vendor source_extraction_issues source_file source_sha256
    supplier supplier_case_pack supplier_code supplier_order_unit supplier_priority
    supplier_qualifying_unit supplier_qualifying_units_per_case supplier_retail_pack
    supplier_size_ml supplier_size_raw supplier_title supplier_vintage supplier_vintage_raw
    territory_conflict v1_candidate_disposition v1_mapping_status v2_candidate_disposition
    v2_mapping_status v2_nonselection_reason v2_review_status v2_reviewed_at
    v3_candidate_disposition v3_mapping_status v3_review_status v3_reviewed_at
    v3_source_attributes_preserved v3_source_title_raw v4_1_owner_decision_id v4_1_reason
    v4_1_review_note v4_1_review_status v4_investigation_completed
    v4_original_candidate_key_preserved v4_patch_reference v4_reason v4_review_status
    variant_gid variant_id variant_title verified_current_price
    """
)

_OWNER_DECISION_FIELDS = _field_set(
    """
    authority_scope decision item_scope mapping_approval owner_date owner_decision_id
    price_approval purchasing_approval remaining_boundary source_file source_revision
    source_sha256 temporal_scope
    """
)

_UNRESOLVED_DEPENDENCY_APPEND_FIELDS = _field_set(
    """
    dependency dependency_id evidence_provider investigation_attempted provider_routing_basis
    resolved review_id source_reference status variant_id
    """
)

_V4_PATCH_CONTRACTS: Mapping[str, _V4PatchContract] = {
    "candidate_matches": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("variant_id", "offer_id"),
        replace_fields=_field_set(
            """
            authoritative_catalog_status candidate_disposition conversion_basis mapping_status
            owner_confirmed_cans_per_shopify_unit owner_confirmed_shopify_units_per_case
            proposed_shopify_sellable_units_per_case review_requirements
            review_revision_precedence v4_1_conversion_review_status v4_1_owner_decision_id
            v4_1_reason v4_1_required_identity_dependencies v4_1_review_note v4_1_review_status
            """
        ),
        baseline_rows=14_727,
        patch_records=14,
        effective_rows=14_727,
    ),
    "candidate_projection_eligibility": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("owner_decision_id",),
        replace_fields=_field_set(
            """
            candidate_disposition candidate_has_populated_shopify_conversion mapping_status
            may_influence_unapproved_review_projection owner_decision_id v4_1_review_status
            """
        ),
        key_mode="POSTCONDITION_WITH_EXTERNAL_BASE",
        baseline_rows=14_727,
        patch_records=14,
        effective_rows=14_727,
    ),
    "catalog_coverage": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("variant_id",),
        replace_fields=_field_set(
            """
            candidate_offer_occurrences candidate_suppliers display_status
            first_priority_candidate_supplier historical_sales_attribution_resolved
            historical_transition_date owner_confirmed_cans_per_shopify_unit
            owner_confirmed_current_expression owner_confirmed_physical_cans_per_case
            owner_confirmed_shopify_units_per_case source_status status unresolved_questions
            v4_1_named_dependencies v4_1_owner_decision_id v4_1_review_note
            v4_1_review_status v4_1_reviewed_at
            """
        ),
        baseline_rows=2_000,
        patch_records=251,
        effective_rows=2_000,
    ),
    "coverage_change_ledger": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("variant_id",),
        replace_fields=_field_set(
            "after_status changed v4_1_before_status v4_1_changed v4_1_evidence_reference v4_changed_preserved"
        ),
        baseline_rows=2_000,
        patch_records=4,
        effective_rows=2_000,
    ),
    "normalized_price_contract_links": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("projection_row_id",),
        replace_fields=_field_set(
            """
            blocking_dependencies candidate_dispositions_used candidate_variant_ids
            derived_unit_price_basis derived_unit_price_formula v4_1_owner_decision_id
            v4_1_review_status
            """
        ),
        baseline_rows=80_661,
        patch_records=5,
        effective_rows=80_661,
    ),
    "normalized_price_contract_review": _V4PatchContract(
        frozenset({"replace_fields"}),
        (),
        replace_fields=_field_set("canonical_variant_id review_note shopify_units_per_case unit_price"),
        key_mode="EXTERNAL_PROOF",
        baseline_rows=80_661,
        patch_records=5,
        effective_rows=80_661,
    ),
    "offer_pair_reviews": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("variant_id", "offer_id"),
        replace_fields=frozenset({"v4_1_review"}),
        baseline_rows=3_820,
        patch_records=5,
        effective_rows=3_820,
    ),
    "owner_decisions": _V4PatchContract(
        frozenset({"append_record"}),
        ("owner_decision_id",),
        append_fields=_OWNER_DECISION_FIELDS,
        required_append_fields=_OWNER_DECISION_FIELDS,
        baseline_rows=17,
        patch_records=3,
        effective_rows=20,
    ),
    "owner_question_batch": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("owner_decision_id",),
        replace_fields=_field_set(
            "answer owner_decision_id previous_question_preserved question question_status reason"
        ),
        key_mode="POSTCONDITION_WITH_EXTERNAL_BASE",
        baseline_rows=3,
        patch_records=3,
        effective_rows=3,
    ),
    "rejected_alternatives": _V4PatchContract(
        frozenset({"append_record"}),
        ("variant_id", "offer_id"),
        append_fields=_REJECTED_ALTERNATIVE_APPEND_FIELDS,
        required_append_fields=_REJECTED_ALTERNATIVE_REQUIRED_FIELDS,
        baseline_rows=7_121,
        patch_records=2,
        effective_rows=7_123,
    ),
    "unresolved_dependencies": _V4PatchContract(
        frozenset({"replace_fields", "append_record"}),
        ("dependency_id",),
        replace_fields=_field_set(
            """
            evidence_provider provider_routing_basis resolution_evidence
            resolution_owner_decision_id resolved status v4_1_clarification
            """
        ),
        append_fields=_UNRESOLVED_DEPENDENCY_APPEND_FIELDS,
        required_append_fields=_UNRESOLVED_DEPENDENCY_APPEND_FIELDS,
        baseline_rows=741,
        patch_records=745,
        effective_rows=745,
    ),
    "variant_review_1720": _V4PatchContract(
        frozenset({"replace_fields"}),
        ("variant_id",),
        replace_fields=_field_set(
            """
            after_status display_status named_dependencies proposed_offer_ids rejected_offer_ids
            review_reason review_status source_status v4_1_owner_decision_id v4_1_reviewed_at
            """
        ),
        baseline_rows=1_720,
        patch_records=251,
        effective_rows=1_720,
    ),
}

_PATCH_TOP_LEVEL_FIELDS = frozenset(
    {
        "after_record_sha256",
        "append_record",
        "base_jsonl",
        "base_row_number_1based",
        "before_record_sha256",
        "changes",
        "operation",
        "stable_key",
        "table",
    }
)
_PATCH_CHANGE_FIELDS = frozenset({"field", "before_present", "before", "after_present", "after"})


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

@dataclass(frozen=True)
class _V4TableContract:
    expected_rows: int
    required_fields: frozenset[str]
    known_fields: frozenset[str]
    unique_keys: tuple[tuple[str, ...], ...] = ()


_V4_TABLE_CONTRACTS: Mapping[str, _V4TableContract] = {
    "affected_variants": _V4TableContract(
        4,
        _field_set(
            """
            variant_id variant_gid product_id product_title variant_title source_status status
            display_status candidate_offer_occurrences candidate_suppliers current_catalog_identity_reference
            current_identity_returned new_mapping_approval quantity_or_import_eligible
            routine_purchase_candidate v4_1_owner_decision_id v4_1_review_note
            """
        ),
        _field_set(
            """
            variant_id variant_gid product_id product_gid product_title current_product_title
            variant_title current_variant_title shopify_sku current_sku_raw barcode
            current_barcode_raw shopify_vendor product_status source_status status display_status
            candidate_offer_occurrences candidate_suppliers first_priority_candidate_supplier
            current_catalog_identity_reference current_identity_returned current_price_verified
            new_mapping_approval quantity_or_import_eligible routine_purchase_candidate
            policy_exclusion_reason unresolved_questions v4_1_named_dependencies
            v4_1_owner_decision_id v4_1_review_note v4_1_review_status v4_1_reviewed_at
            variant_returned product_returned product variant product_metafields variant_metafields
            product_proof_interpretations variant_proof_interpretations missing_evidence
            metafield_pagination_complete catalog_evidence_limit
            """
        ),
        (("variant_id",), ("variant_gid",)),
    ),
    "affected_candidates": _V4TableContract(
        14,
        _field_set(
            """
            variant_id variant_gid supplier offer_id supplier_code supplier_title source_file
            source_sha256 candidate_disposition mapping_status authoritative_catalog_status
            policy_excluded offer_type physical_units_per_supplier_case supplier_case_pack
            supplier_retail_pack supplier_order_unit supplier_qualifying_unit
            supplier_qualifying_units_per_case approval_status quantity_or_import_eligible
            """
        ),
        _field_set(
            """
            variant_id variant_gid product_id product_title variant_title supplier offer_id
            supplier_code supplier_title supplier_title_active_source reviewed_supplier_title
            supplier_size_ml supplier_size_raw supplier_case_pack supplier_retail_pack
            supplier_order_unit supplier_order_increment supplier_qualifying_unit
            supplier_qualifying_units_per_case physical_units_per_supplier_case
            proposed_shopify_sellable_units_per_case supplier_vintage supplier_vintage_raw
            offer_type source_file source_sha256 source_page printed_page physical_page
            source_evidence raw_evidence source_extraction_issues candidate_disposition
            before_candidate_disposition after_candidate_disposition mapping_status
            authoritative_catalog_status policy_excluded policy_exclusion_reason
            approval_status quantity_or_import_eligible routine_purchase_candidate
            current_price_verified verified_current_price review_id review_status
            review_requirements review_revision_precedence remaining_dependencies
            v4_1_owner_decision_id v4_1_reason v4_1_review_note v4_1_review_status
            v4_1_conversion_review_status v4_1_required_identity_dependencies
            new_occurrence_candidate_record new_occurrence_from_existing_source
            """
        ),
        (("variant_id", "offer_id"),),
    ),
    "dependency_groups": _V4TableContract(
        106,
        _field_set(
            """
            group_id dependency_count dependency_ids exact_evidence_needed
            investigated_blocked_variant_ids routing_is_recommendation unique_variant_count
            variant_ids who_can_supply
            """
        ),
        _field_set(
            """
            group_id dependency_count dependency_ids exact_evidence_needed
            investigated_blocked_variant_ids routing_is_recommendation unique_variant_count
            variant_ids who_can_supply
            """
        ),
        (("group_id",),),
    ),
    "normalized_price_contract_links_delta": _V4TableContract(
        5,
        _field_set(
            """
            projection_row_id source_tier_id normalized_table_row_number_1based source_offer_id
            source_file source_sha256 source_page candidate_variant_ids blocking_dependencies
            v4_1_owner_decision_id application_vendor_id application_offer_id application_price_id
            mapping_approved price_approved import_ready import_status
            """
        ),
        _field_set(
            """
            application_offer_id application_price_id application_vendor_id blocking_dependencies
            candidate_dispositions_used candidate_mapping_review_only candidate_review_statuses_used
            candidate_variant_ids derived_unit_price_basis derived_unit_price_formula import_ready
            import_status mapping_approved normalized_table_row_number_1based policy_exclusion_reasons
            price_approved printed_page prior_evidence_retained_without_new_review projection_row_id
            published_prices_unchanged source_evidence_record_id source_file source_offer_id
            source_page source_sha256 source_table source_tier_id tier_selected
            v4_1_owner_decision_id v4_1_review_status
            """
        ),
        (("projection_row_id",), ("source_tier_id",), ("normalized_table_row_number_1based",)),
    ),
    "normalized_price_contract_review_delta": _V4TableContract(
        5,
        frozenset(PRICE_BOOK_HEADERS),
        frozenset(PRICE_BOOK_HEADERS),
    ),
    "owner_decisions_added": _V4TableContract(
        3,
        _OWNER_DECISION_FIELDS,
        _OWNER_DECISION_FIELDS,
        (("owner_decision_id",),),
    ),
    "owner_question_batch_effective": _V4TableContract(
        3,
        _field_set(
            """
            question_id variant_id product question question_status reason
            previous_question_preserved owner_decision_id mapping_approval_requested
            """
        ),
        _field_set(
            """
            question_id variant_id product question answer question_status reason
            previous_question_preserved owner_decision_id mapping_approval_requested
            """
        ),
        (("question_id",),),
    ),
    "preserved_target_identity_evidence": _V4TableContract(
        4,
        _field_set(
            """
            variant_id variant_gid product_id product_gid v3_product_title v3_variant_title
            v3_status variant_returned product_returned product variant product_metafields
            variant_metafields product_proof_interpretations variant_proof_interpretations
            metafield_pagination_complete catalog_evidence_limit missing_evidence
            """
        ),
        _field_set(
            """
            variant_id variant_gid product_id product_gid v3_product_title v3_variant_title
            v3_status variant_returned product_returned product variant product_metafields
            variant_metafields product_proof_interpretations variant_proof_interpretations
            metafield_pagination_complete catalog_evidence_limit missing_evidence
            """
        ),
        (("variant_id",), ("variant_gid",)),
    ),
}

_REQUIRED_DELTA_FILES = frozenset(
    {
        "README_and_Data_Dictionary.md",
        "baseline_preconditions.json",
        "changed_table_hashes.json",
        "unchanged_artifacts.json",
        "validation.json",
        "companion_record_patches.jsonl",
        "workbook_change_counts.json",
        "workbook_row_patches.jsonl",
        "workbook_validation.json",
        "normalized_price_contract_review_delta.csv",
        "normalized_price_contract_review_delta_csv_types.jsonl",
        *_DELTA_JSONL_TABLES.keys(),
    }
)

_BASELINE_ROW_HASH_FORMAT = (
    "sha256 of UTF-8 Python JSON sorted keys, ensure_ascii=False, separators comma/colon; "
    "native scalar types preserved"
)
_UNCHANGED_ARTIFACT_FIELDS = frozenset(
    {"path", "file", "bytes", "sha256", "role", "hash_basis", "v4_1_byte_hash_rechecked"}
)
_UNCHANGED_ARTIFACT_ROLES = frozenset(
    {
        "ORIGINAL_PRESERVED; MASTER_SUPERSEDED_BY_V4.1",
        "UNCHANGED_AUTHORITATIVE_BASELINE",
        "UNCHANGED_ORIGINAL_ATTACHMENT",
        "UNCHANGED_V4_APPLICATION_CONTRACT_EVIDENCE",
        "UNCHANGED_V4_ARTIFACT",
        "UNCHANGED_V4_ARTIFACT; REVIEW_TABS_USE_V4.1_PATCHES",
        "UNCHANGED_V4_BASE; APPLY_V4.1_ROW_PATCHES",
    }
)
_UNCHANGED_ARTIFACT_HASH_BASES = frozenset({"V4_FROZEN_MANIFEST", "REHASHED_THIS_REVISION"})
_UNCHANGED_ARTIFACT_COUNT = 189
_V4_SOURCE_STATUS_BEFORE = {
    "PROPOSED MATCH": 1_400,
    "NOT PROCESSED": 247,
    "NOT FOUND": 154,
    "CONFLICT": 70,
    "SUPPLIER SOURCE MISSING": 71,
    "POLICY EXCLUDED": 58,
}
_V4_SOURCE_STATUS_AFTER = {
    "PROPOSED MATCH": 1_402,
    "NOT PROCESSED": 247,
    "NOT FOUND": 154,
    "CONFLICT": 68,
    "SUPPLIER SOURCE MISSING": 71,
    "POLICY EXCLUDED": 58,
}
_V4_CANDIDATE_DISPOSITIONS = {
    "SEARCH_LEAD_ONLY": 6_053,
    "REJECTED_ATTRIBUTE_CONFLICT": 7_121,
    "PROPOSED_REVIEW_CANDIDATE": 1_551,
    "REJECTED_IDENTITY_CONFLICT": 2,
}

_NORMALIZED_REQUIRED_TEXT_FIELDS = frozenset(
    {
        "batch_ref",
        "vendor_name",
        "supplier_sku",
        "supplier_description",
        "canonical_variant_id",
        "package_type",
        "size_text",
        "level_type",
        "source_file",
        "source_evidence",
        "extraction_confidence",
        "review_note",
    }
)
_NORMALIZED_OPTIONAL_TEXT_FIELDS = frozenset(
    {
        "target_price_state",
        "effective_from",
        "effective_through",
        "raw_pack",
        "assortment_scope",
        "assortment_group",
        "assortment_evidence",
        "break_unit",
    }
)
_NORMALIZED_OPTIONAL_NUMBER_FIELDS = frozenset(
    {"shopify_units_per_case", "qualifying_units_per_case", "break_quantity"}
)
_NORMALIZED_REQUIRED_NUMBER_FIELDS = frozenset({"case_price", "unit_price"})


def _validate_normalized_review_row(row: Mapping[str, Any], *, row_number: int) -> None:
    if set(row) != set(PRICE_BOOK_HEADERS):
        raise ReviewPackageError(
            "NORMALIZED_CONTRACT_MISMATCH",
            "normalized review row must contain exactly the 27 contract fields",
            table="normalized_price_contract_review_delta",
            row=row_number,
        )
    for field_name in _NORMALIZED_REQUIRED_TEXT_FIELDS:
        value = row[field_name]
        if not isinstance(value, str) or not value.strip():
            raise ReviewPackageError(
                "NORMALIZED_TYPE_MISMATCH",
                f"{field_name} must be nonblank exact text",
                table="normalized_price_contract_review_delta",
                row=row_number,
            )
    for field_name in _NORMALIZED_OPTIONAL_TEXT_FIELDS:
        value = row[field_name]
        if value is not None and not isinstance(value, str):
            raise ReviewPackageError(
                "NORMALIZED_TYPE_MISMATCH",
                f"{field_name} must be text or explicit null",
                table="normalized_price_contract_review_delta",
                row=row_number,
            )
    for field_name in _NORMALIZED_OPTIONAL_NUMBER_FIELDS | _NORMALIZED_REQUIRED_NUMBER_FIELDS:
        value = row[field_name]
        if value is None and field_name in _NORMALIZED_OPTIONAL_NUMBER_FIELDS:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ReviewPackageError(
                "NORMALIZED_TYPE_MISMATCH",
                f"{field_name} must be an exact number",
                table="normalized_price_contract_review_delta",
                row=row_number,
            )
        number = Decimal(value)
        if not number.is_finite() or number <= 0 or number.as_tuple().exponent < -4:
            raise ReviewPackageError(
                "NORMALIZED_NUMBER_MISMATCH",
                f"{field_name} must be positive with at most four decimal places",
                table="normalized_price_contract_review_delta",
                row=row_number,
            )
        if field_name in _NORMALIZED_OPTIONAL_NUMBER_FIELDS and number != number.to_integral_value():
            raise ReviewPackageError(
                "NORMALIZED_NUMBER_MISMATCH",
                f"{field_name} must be a whole number when present",
                table="normalized_price_contract_review_delta",
                row=row_number,
            )
    if row["target_price_state"] is not None:
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "review projection target_price_state must remain explicit null",
            row=row_number,
        )
    for field_name in ("effective_from", "effective_through"):
        if row[field_name] is not None:
            try:
                date.fromisoformat(row[field_name])
            except ValueError as exc:
                raise ReviewPackageError(
                    "NORMALIZED_DATE_MISMATCH",
                    f"{field_name} must be an ISO date or explicit null",
                    row=row_number,
                ) from exc
    if row["assortable"] is not None and type(row["assortable"]) is not bool:
        raise ReviewPackageError("NORMALIZED_TYPE_MISMATCH", "assortable must be boolean or explicit null", row=row_number)
    if type(row["source_page"]) is not int or row["source_page"] <= 0:
        raise ReviewPackageError("NORMALIZED_TYPE_MISMATCH", "source_page must be a positive integer", row=row_number)
    if row["extraction_confidence"] != "UNAPPROVED_CANDIDATE":
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "real V4.1 extraction confidence must remain UNAPPROVED_CANDIDATE",
            row=row_number,
        )
    level = row["level_type"]
    if level == "BASE":
        if row["break_quantity"] is not None or row["break_unit"] is not None:
            raise ReviewPackageError("NORMALIZED_LADDER_MISMATCH", "BASE break fields must be null", row=row_number)
    elif level == "BREAK":
        if row["break_quantity"] is None or row["break_unit"] not in {"BT", "CS"}:
            raise ReviewPackageError("NORMALIZED_LADDER_MISMATCH", "BREAK requires whole BT/CS evidence", row=row_number)
    else:
        raise ReviewPackageError("NORMALIZED_LADDER_MISMATCH", "level_type must be BASE or BREAK", row=row_number)


def _validate_unchanged_artifacts(
    value: Any,
    *,
    master_workbook_sha256: str,
    limits: ReviewLimits,
) -> tuple[dict[str, Any], ...]:
    rows = _require_sequence(
        value,
        code="INVALID_UNCHANGED_ARTIFACTS",
        message="unchanged artifacts must be an array",
    )
    if len(rows) != _UNCHANGED_ARTIFACT_COUNT:
        raise ReviewPackageError(
            "UNCHANGED_ARTIFACT_COUNT_MISMATCH",
            f"unchanged artifact manifest must contain {_UNCHANGED_ARTIFACT_COUNT} records",
        )
    normalized: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    master_matches = 0
    for row_number, raw in enumerate(rows, start=1):
        row = _require_mapping(
            raw,
            code="INVALID_UNCHANGED_ARTIFACTS",
            message=f"unchanged artifact {row_number} must be an object",
        )
        if set(row) != _UNCHANGED_ARTIFACT_FIELDS:
            raise ReviewPackageError(
                "INVALID_UNCHANGED_ARTIFACTS",
                f"unchanged artifact {row_number} fields differ",
            )
        path = _validated_member_name(row.get("path"), limits)
        file_name = row.get("file")
        if not isinstance(file_name, str) or not file_name or file_name != PurePosixPath(path).name:
            raise ReviewPackageError(
                "INVALID_UNCHANGED_ARTIFACTS",
                f"unchanged artifact {row_number} filename differs from its path",
                path=path,
            )
        if path in seen_paths:
            raise ReviewPackageError("DUPLICATE_UNCHANGED_ARTIFACT", "unchanged artifact path is duplicated", path=path)
        seen_paths.add(path)
        byte_count = row.get("bytes")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            raise ReviewPackageError("INVALID_UNCHANGED_ARTIFACTS", "artifact byte count is invalid", path=path)
        digest = _require_sha256(row.get("sha256"), path=path)
        if row.get("role") not in _UNCHANGED_ARTIFACT_ROLES:
            raise ReviewPackageError("INVALID_UNCHANGED_ARTIFACTS", "artifact role is unsupported", path=path)
        if row.get("hash_basis") not in _UNCHANGED_ARTIFACT_HASH_BASES:
            raise ReviewPackageError("INVALID_UNCHANGED_ARTIFACTS", "artifact hash basis is unsupported", path=path)
        if row.get("v4_1_byte_hash_rechecked") is not True:
            raise ReviewPackageError("UNCHANGED_ARTIFACT_NOT_RECHECKED", "artifact was not rechecked for V4.1", path=path)
        if digest == master_workbook_sha256 and file_name.startswith("Buffalo_Catalog_and_Mapping_Review_v4"):
            master_matches += 1
        normalized.append(dict(row))
    if master_matches < 1:
        raise ReviewPackageError(
            "MASTER_WORKBOOK_PRECONDITION_MISSING",
            "unchanged artifact manifest does not contain the exact V4 master workbook",
        )
    return tuple(normalized)


def _verified_jsonl(
    source: _PackageSource,
    path: str,
    declared: Mapping[str, tuple[int, str]],
) -> list[dict[str, Any]]:
    rows, raw_sha = _read_jsonl(source, path)
    if raw_sha != declared[path][1]:
        raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "JSONL bytes differ", path=path)
    return rows


def _required_nonnegative_int(value: Any, *, code: str, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewPackageError(code, message)
    return value


def _nonblank_key(row: Mapping[str, Any], fields: Sequence[str], *, table: str, row_number: int) -> tuple[Any, ...]:
    values = []
    for field_name in fields:
        if field_name not in row or row[field_name] is None or (
            isinstance(row[field_name], str) and not row[field_name].strip()
        ):
            raise ReviewPackageError(
                "TABLE_KEY_MISSING",
                f"{table} row {row_number} lacks nonblank key field {field_name}",
                table=table,
                row=row_number,
            )
        values.append(row[field_name])
    return tuple(values)


def _validate_v4_table(
    name: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    contract = _V4_TABLE_CONTRACTS[name]
    if len(rows) != contract.expected_rows:
        raise ReviewPackageError("ROW_COUNT_MISMATCH", f"{name} must contain {contract.expected_rows} rows")
    for row_number, row in enumerate(rows, start=1):
        missing = sorted(contract.required_fields - set(row))
        if missing:
            raise ReviewPackageError(
                "REQUIRED_FIELD_MISSING",
                f"{name} row {row_number} lacks {', '.join(missing)}",
                table=name,
                row=row_number,
            )
    for key_fields in contract.unique_keys:
        seen: set[tuple[Any, ...]] = set()
        for row_number, row in enumerate(rows, start=1):
            key = _nonblank_key(row, key_fields, table=name, row_number=row_number)
            if key in seen:
                raise ReviewPackageError(
                    "TABLE_KEY_NOT_UNIQUE",
                    f"{name} duplicates key {','.join(key_fields)}",
                    table=name,
                    row=row_number,
                )
            seen.add(key)
    return _table_unknown_fields(rows, set(contract.known_fields))


def _change_by_field(patch: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    changes = patch.get("changes")
    if not isinstance(changes, list):
        return {}
    return {
        change["field"]: change
        for change in changes
        if isinstance(change, dict) and isinstance(change.get("field"), str)
    }


def _validate_v4_patch_rows(
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_file: str,
) -> None:
    contract = _V4_PATCH_CONTRACTS[table_name]
    if len(rows) != contract.patch_records:
        raise ReviewPackageError("PATCH_ROW_COUNT_MISMATCH", f"{table_name} patch count differs")
    seen_rows: set[int] = set()
    seen_replace_keys: set[tuple[tuple[str, bytes], ...]] = set()
    seen_append_keys: set[tuple[tuple[str, bytes], ...]] = set()
    for patch_number, patch in enumerate(rows, start=1):
        if set(patch) != _PATCH_TOP_LEVEL_FIELDS:
            raise ReviewPackageError("INVALID_PATCH", "patch fields differ from the code-owned contract")
        if patch.get("table") != table_name or patch.get("base_jsonl") != PurePosixPath(baseline_file).name:
            raise ReviewPackageError("INVALID_PATCH", "patch table or baseline file differs")
        operation = patch.get("operation")
        if operation not in contract.operations:
            raise ReviewPackageError("INVALID_PATCH_OPERATION", f"{table_name} operation is not authorized")
        stable_key = patch.get("stable_key")
        if not isinstance(stable_key, dict) or tuple(sorted(stable_key)) != tuple(sorted(contract.stable_key_fields)):
            raise ReviewPackageError("PATCH_KEY_FIELDS_MISMATCH", f"{table_name} stable-key fields differ")
        changes = _require_sequence(
            patch.get("changes"), code="INVALID_PATCH", message="patch changes must be an array"
        )
        if not changes:
            raise ReviewPackageError("INVALID_PATCH", "patch changes may not be empty")
        change_fields: set[str] = set()
        for change in changes:
            item = _require_mapping(change, code="INVALID_PATCH", message="change must be an object")
            if set(item) != _PATCH_CHANGE_FIELDS:
                raise ReviewPackageError("INVALID_PATCH", "change fields differ from the code-owned contract")
            field_name = item.get("field")
            if not isinstance(field_name, str) or not field_name or field_name in change_fields:
                raise ReviewPackageError("DUPLICATE_PATCH_FIELD", "changed fields must be unique and nonblank")
            if not isinstance(item.get("before_present"), bool) or not isinstance(item.get("after_present"), bool):
                raise ReviewPackageError("INVALID_PATCH", "patch presence flags must be boolean")
            if item.get("after_present") is not True:
                raise ReviewPackageError("PATCH_FIELD_REMOVAL_REJECTED", "V4.1 patches may not remove source fields")
            change_fields.add(field_name)
        _require_sha256(patch.get("after_record_sha256"), path=f"{table_name}[{patch_number}]")
        if operation == "replace_fields":
            row_number = patch.get("base_row_number_1based")
            if (
                isinstance(row_number, bool)
                or not isinstance(row_number, int)
                or row_number <= 0
                or row_number > contract.baseline_rows
                or row_number in seen_rows
            ):
                raise ReviewPackageError("PATCH_ROW_OUT_OF_RANGE", f"{table_name} row target is invalid or duplicated")
            seen_rows.add(row_number)
            if contract.key_mode == "BASE":
                canonical_key = tuple(
                    (key, _canonical_json(value)) for key, value in sorted(stable_key.items())
                )
                if canonical_key in seen_replace_keys:
                    raise ReviewPackageError(
                        "DUPLICATE_PATCH", f"{table_name} replace key is duplicated"
                    )
                seen_replace_keys.add(canonical_key)
            _require_sha256(patch.get("before_record_sha256"), path=f"{table_name}[{patch_number}]")
            if patch.get("append_record") is not None:
                raise ReviewPackageError("INVALID_PATCH", "replace patch may not carry append_record")
            if not change_fields.issubset(contract.replace_fields):
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", f"{table_name} changes an unauthorized field")
            if contract.key_mode == "BASE" and change_fields.intersection(stable_key):
                raise ReviewPackageError("PATCH_IDENTITY_FIELD_MUTATION", f"{table_name} changes its stable key")
            if contract.key_mode == "POSTCONDITION_WITH_EXTERNAL_BASE":
                changes_by_field = _change_by_field(patch)
                if any(
                    field_name not in changes_by_field
                    or changes_by_field[field_name].get("after") != value
                    for field_name, value in stable_key.items()
                ):
                    raise ReviewPackageError("PATCH_POSTCONDITION_KEY_MISMATCH", f"{table_name} postcondition key differs")
        else:
            if patch.get("base_row_number_1based") is not None or patch.get("before_record_sha256") is not None:
                raise ReviewPackageError("INVALID_PATCH", "append baseline row and before hash must be null")
            record = patch.get("append_record")
            if not isinstance(record, dict):
                raise ReviewPackageError("INVALID_PATCH", "append requires append_record")
            if not contract.required_append_fields.issubset(record) or not set(record).issubset(contract.append_fields):
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", f"{table_name} append schema differs")
            if not stable_key or any(key not in record or record[key] != value for key, value in stable_key.items()):
                raise ReviewPackageError("PATCH_KEY_MISMATCH", f"{table_name} append key differs")
            canonical_key = tuple((key, _canonical_json(value)) for key, value in sorted(stable_key.items()))
            if canonical_key in seen_append_keys:
                raise ReviewPackageError("DUPLICATE_PATCH", f"{table_name} append key is duplicated")
            seen_append_keys.add(canonical_key)
            if change_fields != set(record):
                raise ReviewPackageError("PATCH_APPEND_CHANGE_MISMATCH", f"{table_name} append fields differ")
            for field_name, change in _change_by_field(patch).items():
                if (
                    change.get("before_present") is not False
                    or change.get("before") is not None
                    or change.get("after_present") is not True
                    or change.get("after") != record[field_name]
                ):
                    raise ReviewPackageError("PATCH_APPEND_CHANGE_MISMATCH", f"{table_name} append values differ")
            if canonical_record_sha256(record) != patch.get("after_record_sha256"):
                raise ReviewPackageError("PATCH_AFTER_HASH_MISMATCH", f"{table_name} append hash differs")


def _read_verified_json(
    source: _PackageSource,
    path: str,
    declared: Mapping[str, tuple[int, str]],
) -> Any:
    return _parse_json_bytes(
        _read_verified_bounded(source, path, source.limits.max_manifest_bytes, declared),
        path=path,
        limits=source.limits,
    )


def _patch_stable_keys(rows: Sequence[Mapping[str, Any]]) -> set[tuple[Any, ...]]:
    keys = set()
    for patch in rows:
        stable_key = patch["stable_key"]
        keys.add(tuple(stable_key[name] for name in sorted(stable_key)))
    return keys


def _validate_v4_dictionary(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewPackageError(
            "INVALID_DICTIONARY", "V4.1 dictionary must be UTF-8"
        ) from exc
    canonical_fields = ", ".join(PRICE_BOOK_HEADERS)
    required_fragments = (
        "# Buffalo V4.1 clarification checkpoint",
        "## Table dictionary and precedence",
        "companion_record_patches.jsonl",
        "normalized_price_contract_review_delta_csv_types.jsonl",
        canonical_fields,
        "All existing21 documented contract gaps",
        "No candidate is approved or verified CURRENT.",
    )
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        raise ReviewPackageError(
            "INVALID_DICTIONARY",
            "V4.1 dictionary omits a code-required format or authority declaration",
        )


def _validate_workbook_patch_evidence(
    rows: Sequence[Mapping[str, Any]],
    declared_counts: Mapping[str, Any],
) -> None:
    allowed_operations = {
        "replace_row",
        "append_row",
        "replace_sheet_title",
        "replace_sheet_columns",
        "add_sheet",
    }
    row_operations = {"replace_row", "append_row"}
    observed: Counter[str] = Counter()
    targets: set[tuple[str, str, str, int | str]] = set()
    for row_number, row in enumerate(rows, start=1):
        operation = row.get("operation")
        workbook = row.get("workbook")
        sheet = row.get("sheet")
        if (
            operation not in allowed_operations
            or not isinstance(workbook, str)
            or not workbook
            or not isinstance(sheet, str)
            or not sheet
        ):
            raise ReviewPackageError(
                "INVALID_WORKBOOK_PATCH", "workbook patch identity is invalid", row=row_number
            )
        observed[f"{workbook} | {sheet}"] += 1
        if operation in row_operations:
            if set(row) != {"workbook", "sheet", "excel_row", "operation", "before", "after"}:
                raise ReviewPackageError(
                    "INVALID_WORKBOOK_PATCH", "row patch fields differ", row=row_number
                )
            excel_row = row.get("excel_row")
            if isinstance(excel_row, bool) or not isinstance(excel_row, int) or excel_row <= 0:
                raise ReviewPackageError(
                    "INVALID_WORKBOOK_PATCH", "workbook row number is invalid", row=row_number
                )
            if operation == "append_row" and row.get("before") is not None:
                raise ReviewPackageError(
                    "INVALID_WORKBOOK_PATCH", "append row must have a null before-value", row=row_number
                )
            target = (workbook, sheet, operation, excel_row)
        elif operation == "add_sheet":
            if set(row) != {"workbook", "sheet", "operation", "sheet_payload"}:
                raise ReviewPackageError(
                    "INVALID_WORKBOOK_PATCH", "add-sheet patch fields differ", row=row_number
                )
            payload = row.get("sheet_payload")
            if not isinstance(payload, dict) or payload.get("name") != sheet:
                raise ReviewPackageError(
                    "INVALID_WORKBOOK_PATCH", "add-sheet payload identity differs", row=row_number
                )
            target = (workbook, sheet, operation, sheet)
        else:
            if set(row) != {"workbook", "sheet", "operation", "before", "after"}:
                raise ReviewPackageError(
                    "INVALID_WORKBOOK_PATCH", "sheet patch fields differ", row=row_number
                )
            target = (workbook, sheet, operation, sheet)
        if target in targets:
            raise ReviewPackageError(
                "DUPLICATE_WORKBOOK_PATCH", "workbook patch target is duplicated", row=row_number
            )
        targets.add(target)
    expected: dict[str, int] = {}
    for key, value in declared_counts.items():
        if not isinstance(key, str) or not key:
            raise ReviewPackageError(
                "INVALID_WORKBOOK_COUNTS", "workbook count key must be nonblank"
            )
        expected[key] = _required_nonnegative_int(
            value,
            code="INVALID_WORKBOOK_COUNTS",
            message="workbook change counts must be nonnegative integers",
        )
    if observed != Counter(expected):
        raise ReviewPackageError(
            "WORKBOOK_PATCH_COUNT_MISMATCH",
            "workbook patch distribution differs from declared counts",
        )


def _validate_workbook_validation(value: Mapping[str, Any]) -> None:
    if (
        value.get("status") != "PASS"
        or value.get("issue_count") != 0
        or value.get("issues") != []
        or isinstance(value.get("xlsx_bytes"), bool)
        or not isinstance(value.get("xlsx_bytes"), int)
        or value["xlsx_bytes"] <= 0
    ):
        raise ReviewPackageError(
            "WORKBOOK_VALIDATION_MISMATCH", "workbook validation status is not clean"
        )
    _require_sha256(value.get("xlsx_sha256"), path="workbook_validation.json")
    _require_sha256(value.get("payload_sha256"), path="workbook_validation.json")
    counts = _require_mapping(
        value.get("counts"),
        code="WORKBOOK_VALIDATION_MISMATCH",
        message="workbook validation counts must be an object",
    )
    for field_name in ("numeric", "text", "boolean", "dates", "formulas", "formula_caches", "blank"):
        _required_nonnegative_int(
            counts.get(field_name),
            code="WORKBOOK_VALIDATION_MISMATCH",
            message="workbook validation count is invalid",
        )
    if counts["formulas"] != 4_000 or counts["formula_caches"] != 4_000:
        raise ReviewPackageError(
            "WORKBOOK_VALIDATION_MISMATCH", "workbook formula controls differ"
        )
    sheets = _require_sequence(
        value.get("sheets"),
        code="WORKBOOK_VALIDATION_MISMATCH",
        message="workbook validation sheets must be an array",
    )
    by_name: dict[str, Mapping[str, Any]] = {}
    for sheet in sheets:
        item = _require_mapping(
            sheet,
            code="WORKBOOK_VALIDATION_MISMATCH",
            message="workbook sheet validation must be an object",
        )
        name = item.get("sheet")
        if not isinstance(name, str) or not name or name in by_name:
            raise ReviewPackageError(
                "WORKBOOK_VALIDATION_MISMATCH", "workbook sheet identity is invalid"
            )
        by_name[name] = item
    required_rows = {
        "Catalog": 2_000,
        "Matches": 14_727,
        "Dependencies": 745,
        "Owner Decisions": 20,
        "Owner Questions": 3,
        "Contract Field Map": 27,
        "Contract Gaps": 21,
        "Evidence Needed Groups": 106,
    }
    if any(
        name not in by_name or by_name[name].get("data_rows") != rows
        for name, rows in required_rows.items()
    ):
        raise ReviewPackageError(
            "WORKBOOK_VALIDATION_MISMATCH", "required workbook sheet row controls differ"
        )


def _validate_v4_controls_and_relationships(
    *,
    tables: Mapping[str, PackageTable],
    validation: Mapping[str, Any],
    patches_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
    consolidated_patches: Sequence[Mapping[str, Any]],
    workbook_patches: Sequence[Mapping[str, Any]],
    workbook_change_counts: Mapping[str, Any],
) -> dict[str, Any]:
    for field_name in ("mapping_approvals", "current_price_approvals", "import_ready_rows"):
        if validation.get(field_name) != 0:
            raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", f"{field_name} must remain zero")
    exact_controls = {
        "original_variants": 2_000,
        "current_census": 2_003,
        "original_returned": 1_999,
        "current_additions": 4,
        "candidate_occurrences": 14_727,
        "normalized_rows_changed": 5,
        "normalized_total_rows": 80_661,
        "dependency_records": 745,
        "resolved_dependency_records": 3,
        "unresolved_dependency_records": 742,
        "dependency_groups": 106,
        "workbook_row_patch_count": 5_172,
        "companion_row_patch_count": 1_302,
        "source_status_sum": 2_000,
    }
    for field_name, expected in exact_controls.items():
        if validation.get(field_name) != expected:
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", f"validation.{field_name} must equal {expected}")
    required_true = (
        "original_variant_id_set_preserved",
        "prior_candidate_keys_preserved",
        "all247_have_named_grouped_dependencies",
        "no_history_alias_or_inventory_mutation",
        "no_new_catalog_retrieval",
        "no_price_tier_selection_or_fee_added",
    )
    if any(validation.get(field_name) is not True for field_name in required_true):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "a required V4.1 preservation control is not true")
    source_before = _require_mapping(
        validation.get("source_status_before"), code="INVALID_VALIDATION", message="source_status_before must be an object"
    )
    source_after = _require_mapping(
        validation.get("source_status_after"), code="INVALID_VALIDATION", message="source_status_after must be an object"
    )
    dispositions = _require_mapping(
        validation.get("candidate_disposition_counts"),
        code="INVALID_VALIDATION",
        message="candidate_disposition_counts must be an object",
    )
    for values, expected, label in (
        (source_before, 2_000, "source_status_before"),
        (source_after, 2_000, "source_status_after"),
        (dispositions, 14_727, "candidate_disposition_counts"),
    ):
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()):
            raise ReviewPackageError("INVALID_VALIDATION", f"{label} values must be nonnegative integers")
        if sum(values.values()) != expected:
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", f"{label} does not reconcile")
    if (
        dict(source_before) != _V4_SOURCE_STATUS_BEFORE
        or dict(source_after) != _V4_SOURCE_STATUS_AFTER
        or dict(dispositions) != _V4_CANDIDATE_DISPOSITIONS
    ):
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH",
            "source outcomes or candidate dispositions differ from the code-owned V4.1 checkpoint",
        )
    original_not_returned = validation.get("original_not_returned")
    if (
        original_not_returned != ["42035918438475"]
        or validation.get("not_returned_is_deletion") is not False
        or validation["original_returned"] + len(original_not_returned) != validation["original_variants"]
        or validation["original_returned"] + validation["current_additions"] != validation["current_census"]
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "original/current cohort controls do not reconcile")
    if validation["resolved_dependency_records"] + validation["unresolved_dependency_records"] != validation["dependency_records"]:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "dependency controls do not reconcile")

    changed_counts = _require_mapping(
        validation.get("changed_companion_record_counts"),
        code="INVALID_VALIDATION",
        message="changed companion counts must be an object",
    )
    if set(changed_counts) != set(_V4_PATCH_CONTRACTS):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "changed companion table set differs")
    for table_name, contract in _V4_PATCH_CONTRACTS.items():
        if changed_counts.get(table_name) != contract.patch_records or len(patches_by_table[table_name]) != contract.patch_records:
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", f"{table_name} patch count differs")

    union_counter: Counter[bytes] = Counter()
    for rows in patches_by_table.values():
        union_counter.update(_canonical_json(row) for row in rows)
    consolidated_counter = Counter(_canonical_json(row) for row in consolidated_patches)
    if len(consolidated_patches) != 1_302 or consolidated_counter != union_counter:
        raise ReviewPackageError("CONSOLIDATED_PATCH_MISMATCH", "companion patch multiset differs from table patches")
    if len(workbook_patches) != 5_172:
        raise ReviewPackageError("WORKBOOK_PATCH_COUNT_MISMATCH", "workbook patch rows do not reconcile")
    workbook_total = 0
    for value in workbook_change_counts.values():
        workbook_total += _required_nonnegative_int(
            value, code="INVALID_WORKBOOK_COUNTS", message="workbook change counts must be nonnegative integers"
        )
    if workbook_total != 5_172:
        raise ReviewPackageError("WORKBOOK_PATCH_COUNT_MISMATCH", "workbook change-count total differs")

    affected_variants = tables["affected_variants"].rows
    affected_candidates = tables["affected_candidates"].rows
    dependency_groups = tables["dependency_groups"].rows
    links = tables["normalized_price_contract_links_delta"].rows
    normalized = tables["normalized_price_contract_review_delta"].rows
    decisions = tables["owner_decisions_added"].rows
    questions = tables["owner_question_batch_effective"].rows
    identities = tables["preserved_target_identity_evidence"].rows
    variant_ids = {str(row["variant_id"]) for row in affected_variants}
    variant_gids = {str(row["variant_gid"]) for row in affected_variants}
    if {str(row["variant_id"]) for row in identities} != variant_ids or {
        str(row["variant_gid"]) for row in identities
    } != variant_gids:
        raise ReviewPackageError("IDENTITY_COHORT_MISMATCH", "preserved identities differ from affected variants")
    if any(
        row.get("new_mapping_approval") is not False
        or row.get("quantity_or_import_eligible") is not False
        or row.get("routine_purchase_candidate") is not False
        or row.get("current_price_verified") is not False
        for row in affected_variants
    ):
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "affected variants may not claim mapping, price, import, or purchase authority",
        )
    if any(
        row.get("approval_status") != "UNAPPROVED_CANDIDATE"
        or row.get("quantity_or_import_eligible") is not False
        or row.get("routine_purchase_candidate") is not False
        or row.get("verified_current_price") is not False
        or row.get("new_mapping_approval", False) is not False
        or row.get("current_price_verified", False) is not False
        for row in affected_candidates
    ):
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "affected candidates may not claim mapping, price, import, or purchase authority",
        )
    for row in (*affected_candidates, *links, *decisions):
        source_sha = row.get("source_sha256")
        if source_sha is not None:
            _require_sha256(source_sha, path="exposed V4.1 source evidence")
    if any(
        row.get("target_price_state") is not None
        or row.get("extraction_confidence") != "UNAPPROVED_CANDIDATE"
        for row in normalized
    ):
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "normalized review rows must remain unapproved with no target price state",
        )
    candidate_keys = {(str(row["variant_id"]), str(row["offer_id"])) for row in affected_candidates}
    if any(str(row["variant_id"]) not in variant_ids for row in affected_candidates):
        raise ReviewPackageError("JOIN_MISSING", "affected candidate references an unknown affected variant")
    candidate_patch_keys = {
        (str(patch["stable_key"]["variant_id"]), str(patch["stable_key"]["offer_id"]))
        for patch in patches_by_table["candidate_matches"]
    }
    if candidate_patch_keys != candidate_keys:
        raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "affected candidate keys differ from candidate patches")
    for table_name in ("offer_pair_reviews", "rejected_alternatives"):
        keys = {
            (str(patch["stable_key"]["variant_id"]), str(patch["stable_key"]["offer_id"]))
            for patch in patches_by_table[table_name]
        }
        if not keys.issubset(candidate_keys):
            raise ReviewPackageError("JOIN_MISSING", f"{table_name} references an unknown affected candidate")

    decision_ids = {str(row["owner_decision_id"]) for row in decisions}
    question_decision_ids = {str(row["owner_decision_id"]) for row in questions}
    if question_decision_ids != decision_ids:
        raise ReviewPackageError(
            "JOIN_MISSING", "owner questions and appended owner decisions differ"
        )
    referenced_decisions = {
        str(value)
        for row in (*affected_variants, *affected_candidates, *links, *questions)
        for value in (row.get("v4_1_owner_decision_id", row.get("owner_decision_id")),)
        if value not in {None, ""}
    }
    if not referenced_decisions.issubset(decision_ids):
        raise ReviewPackageError("JOIN_MISSING", "a V4.1 owner decision reference is missing")
    if any(str(row["variant_id"]) not in variant_ids for row in questions):
        raise ReviewPackageError("JOIN_MISSING", "owner question references an unknown affected variant")
    if any(
        row.get("mapping_approval") is not False
        or row.get("price_approval") is not False
        or row.get("purchasing_approval") is not False
        for row in decisions
    ):
        raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "owner decisions may not approve mapping, price, or purchasing")
    if any(row.get("mapping_approval_requested") is not False for row in questions):
        raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "owner questions may not request mapping approval")

    for group_number, group in enumerate(dependency_groups, start=1):
        dependency_ids = group["dependency_ids"]
        group_variants = group["variant_ids"]
        if not isinstance(dependency_ids, list) or not isinstance(group_variants, list):
            raise ReviewPackageError("INVALID_DEPENDENCY_GROUP", "dependency group members must be arrays")
        if len(dependency_ids) != len(set(dependency_ids)) or len(group_variants) != len(set(group_variants)):
            raise ReviewPackageError("INVALID_DEPENDENCY_GROUP", "dependency group members must be unique")
        if group["dependency_count"] != len(dependency_ids) or group["unique_variant_count"] != len(group_variants):
            raise ReviewPackageError("DEPENDENCY_GROUP_COUNT_MISMATCH", f"dependency group {group_number} does not reconcile")
    dependency_patches = patches_by_table["unresolved_dependencies"]
    resolved_ids = {
        str(patch["stable_key"]["dependency_id"])
        for patch in dependency_patches
        if patch["operation"] == "replace_fields"
        and _change_by_field(patch).get("resolved", {}).get("after") is True
    }
    all_dependency_ids = {
        str(patch["stable_key"]["dependency_id"])
        for patch in dependency_patches
    }
    grouped_dependency_ids = {
        str(dependency_id) for group in dependency_groups for dependency_id in group["dependency_ids"]
    }
    grouped_dependency_sequence = [
        str(dependency_id)
        for group in dependency_groups
        for dependency_id in group["dependency_ids"]
    ]
    if (
        len(all_dependency_ids) != 745
        or len(grouped_dependency_sequence) != 742
        or len(grouped_dependency_ids) != len(grouped_dependency_sequence)
        or len(resolved_ids) != 3
        or grouped_dependency_ids != all_dependency_ids - resolved_ids
    ):
        raise ReviewPackageError("DEPENDENCY_GROUP_JOIN_MISMATCH", "dependency groups do not cover the unresolved set")

    position_rows = {int(row["normalized_table_row_number_1based"]): row for row in links}
    normalized_patch_positions = {
        int(patch["base_row_number_1based"]) for patch in patches_by_table["normalized_price_contract_review"]
    }
    link_patch_positions = {
        int(patch["base_row_number_1based"]) for patch in patches_by_table["normalized_price_contract_links"]
    }
    if set(position_rows) != normalized_patch_positions or set(position_rows) != link_patch_positions:
        raise ReviewPackageError("NORMALIZED_POSITION_MISMATCH", "normalized review/link patch positions differ")
    for link, review_row in zip(links, normalized, strict=True):
        candidate_variants = link.get("candidate_variant_ids")
        if not isinstance(candidate_variants, list) or not candidate_variants or any(
            str(variant_id) not in variant_ids for variant_id in candidate_variants
        ):
            raise ReviewPackageError("JOIN_MISSING", "normalized link references an unknown candidate variant")
        if any((str(variant_id), str(link["source_offer_id"])) not in candidate_keys for variant_id in candidate_variants):
            raise ReviewPackageError("JOIN_MISSING", "normalized link source offer is not an affected candidate")
        if str(review_row["canonical_variant_id"]) not in {str(value) for value in candidate_variants}:
            raise ReviewPackageError("NORMALIZED_POSITION_MISMATCH", "normalized review variant differs from its link")
        if review_row["source_file"] != link["source_file"] or review_row["source_page"] != link["source_page"]:
            raise ReviewPackageError("NORMALIZED_POSITION_MISMATCH", "normalized source evidence differs from its link")
        if (
            link.get("application_vendor_id") is not None
            or link.get("application_offer_id") is not None
            or link.get("application_price_id") is not None
            or link.get("mapping_approved") is not False
            or link.get("price_approved") is not False
            or link.get("import_ready") is not False
            or link.get("import_status") != "NOT_IMPORT_READY"
            or link.get("tier_selected") is not False
            or link.get("candidate_mapping_review_only") is not True
            or link.get("published_prices_unchanged") is not True
            or link.get("prior_evidence_retained_without_new_review") is not False
            or link.get("candidate_dispositions_used") != ["PROPOSED_REVIEW_CANDIDATE"]
            or link.get("candidate_review_statuses_used") != []
            or link.get("policy_exclusion_reasons") != []
            or link.get("v4_1_review_status") != "REVIEWED_IDENTITY_SUPPORTED_UNAPPROVED"
            or not isinstance(link.get("blocking_dependencies"), list)
            or not link["blocking_dependencies"]
        ):
            raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "normalized link claims operational authority")
    return {
        "status": "PASS",
        "dictionary": "CODE_OWNED_V4_1_AND_27_COLUMN_CONTRACT",
        "dictionary_contract": "PASS",
        "patch_tables": len(_V4_PATCH_CONTRACTS),
        "patch_records": len(consolidated_patches),
        "workbook_patch_records": len(workbook_patches),
        "workbook_patch_distribution": "PASS",
        "exposed_tables": len(_V4_TABLE_CONTRACTS),
        "delta_cross_table_joins": "PASS",
        "control_totals": "PASS",
        "normalized_type_projection": "PASS",
        "operational_approvals": 0,
    }


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
    _validate_v4_dictionary(
        _read_verified_bounded(
            source,
            "README_and_Data_Dictionary.md",
            source.limits.max_manifest_bytes,
            declared,
        )
    )
    preconditions = _require_mapping(
        _read_verified_json(source, "baseline_preconditions.json", declared),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="baseline preconditions must be an object",
    )
    if set(preconditions) != {"master_workbook_sha256", "row_hash_format", "tables"}:
        raise ReviewPackageError(
            "INVALID_BASELINE_PRECONDITIONS",
            "baseline precondition fields differ from the code-owned contract",
        )
    master_workbook_sha256 = _require_sha256(
        preconditions.get("master_workbook_sha256"), path="baseline_preconditions.json"
    )
    if preconditions.get("row_hash_format") != _BASELINE_ROW_HASH_FORMAT:
        raise ReviewPackageError(
            "INVALID_BASELINE_PRECONDITIONS",
            "baseline row-hash format differs from the code-owned contract",
        )
    unchanged_artifacts = _validate_unchanged_artifacts(
        _read_verified_json(source, "unchanged_artifacts.json", declared),
        master_workbook_sha256=master_workbook_sha256,
        limits=source.limits,
    )
    precondition_rows = _require_sequence(
        preconditions.get("tables"),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="baseline tables must be a nonempty array",
    )
    if not precondition_rows:
        raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline table list is empty")
    changed_tables = _require_sequence(
        _read_verified_json(source, "changed_table_hashes.json", declared),
        code="INVALID_CHANGED_TABLE_HASHES",
        message="changed table hashes must be a nonempty array",
    )
    if not changed_tables:
        raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "changed table list is empty")

    base_specs: dict[str, dict[str, Any]] = {}
    for raw in precondition_rows:
        item = _require_mapping(
            raw, code="INVALID_BASELINE_PRECONDITIONS", message="baseline table must be an object"
        )
        if set(item) != {"table", "file", "sha256", "rows", "patch_records"}:
            raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline table fields differ")
        table_name = item.get("table")
        base_file = item.get("file")
        if table_name not in _V4_PATCH_CONTRACTS or not isinstance(base_file, str) or table_name in base_specs:
            raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline table set differs")
        _validated_member_name(base_file, source.limits)
        _require_sha256(item.get("sha256"), path=base_file)
        contract = _V4_PATCH_CONTRACTS[table_name]
        if item.get("rows") != contract.baseline_rows or item.get("patch_records") != contract.patch_records:
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", f"{table_name} baseline controls differ")
        base_specs[table_name] = dict(item)
    if set(base_specs) != set(_V4_PATCH_CONTRACTS):
        raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline table set is incomplete")

    changed_specs: dict[str, dict[str, Any]] = {}
    patches_by_table: dict[str, list[dict[str, Any]]] = {}
    for raw_changed in changed_tables:
        changed = _require_mapping(
            raw_changed, code="INVALID_CHANGED_TABLE_HASHES", message="changed table record must be an object"
        )
        if set(changed) != {
            "table",
            "patch_file",
            "patch_file_sha256",
            "patched_or_appended_records",
            "effective_table_rows",
            "effective_table_canonical_jsonl_sha256",
            "canonical_hash_encoding",
        }:
            raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "changed-table fields differ")
        table_name = changed.get("table")
        patch_path = changed.get("patch_file")
        if table_name not in base_specs or table_name in changed_specs or not isinstance(patch_path, str):
            raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "changed table lacks a baseline precondition")
        patch_path = _validated_member_name(patch_path, source.limits)
        if patch_path != f"table_deltas/{table_name}.patch.jsonl":
            raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "patch path differs from the code-owned contract")
        if patch_path not in declared:
            raise ReviewPackageError("INCOMPLETE_DELTA_PACKAGE", "declared patch file is absent", path=patch_path)
        if changed.get("patch_file_sha256") != declared[patch_path][1]:
            raise ReviewPackageError("PATCH_FILE_HASH_MISMATCH", "patch hash evidence differs", path=patch_path)
        contract = _V4_PATCH_CONTRACTS[table_name]
        _require_sha256(changed.get("effective_table_canonical_jsonl_sha256"), path=table_name)
        if (
            changed.get("patched_or_appended_records") != contract.patch_records
            or changed.get("effective_table_rows") != contract.effective_rows
            or changed.get("canonical_hash_encoding")
            != "UTF-8, sorted object keys, compact JSON, native numeric types, LF after each row; NOT raw original CSV/JSONL file bytes"
        ):
            raise ReviewPackageError(
                "PATCH_CONTROL_MISMATCH",
                "patch count, effective rows, or canonical hash encoding differs",
                path=patch_path,
            )
        patch_rows = _verified_jsonl(source, patch_path, declared)
        _validate_v4_patch_rows(table_name, patch_rows, baseline_file=base_specs[table_name]["file"])
        patches_by_table[table_name] = patch_rows
        changed_specs[table_name] = dict(changed)
    if set(changed_specs) != set(_V4_PATCH_CONTRACTS):
        raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "changed table set is incomplete")

    match_patches = {
        int(patch["base_row_number_1based"]): patch
        for patch in patches_by_table["candidate_matches"]
    }
    projection_patches = {
        int(patch["base_row_number_1based"]): patch
        for patch in patches_by_table["candidate_projection_eligibility"]
    }
    if set(match_patches) != set(projection_patches):
        raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "candidate patch row sets differ")
    for row_number, projection_patch in projection_patches.items():
        decision_change = _change_by_field(match_patches[row_number]).get("v4_1_owner_decision_id")
        if decision_change is None or decision_change.get("after") != projection_patch["stable_key"]["owner_decision_id"]:
            raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "candidate projection decision differs")

    tables: dict[str, PackageTable] = {}
    issues: list[ValidationIssue] = []
    for path, table_name in _DELTA_JSONL_TABLES.items():
        rows = _verified_jsonl(source, path, declared)
        unknown_fields = _validate_v4_table(table_name, rows)
        if table_name == "normalized_price_contract_review_delta":
            for row_number, row in enumerate(rows, start=1):
                _validate_normalized_review_row(row, row_number=row_number)
        if unknown_fields:
            issues.append(
                ValidationIssue(
                    "UNKNOWN_FIELDS",
                    "Unknown fields were retained: " + ", ".join(unknown_fields),
                    table=table_name,
                    path=path,
                )
            )
        tables[table_name] = PackageTable(
            name=table_name,
            path=path,
            format="jsonl",
            rows=tuple(rows),
            raw_sha256=declared[path][1],
            canonical_jsonl_sha256=canonical_jsonl_sha256(rows),
            unknown_fields=unknown_fields,
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

    sidecar_rows = _verified_jsonl(source, sidecar_name, declared)
    if len(sidecar_rows) != 5:
        raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", "V4.1 type sidecar must contain five rows")
    allowed_type_codes = {"m", "n", "s", "q", "b", "i", "f", "j"}
    for row_number, row in enumerate(sidecar_rows, start=1):
        if set(row) != {"row_number_1based", "types"} or row.get("row_number_1based") != row_number:
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", "type sidecar row identity differs")
        types = row.get("types")
        if not isinstance(types, list) or len(types) != len(PRICE_BOOK_HEADERS) or any(
            code not in allowed_type_codes for code in types
        ):
            raise ReviewPackageError("TYPE_SIDECAR_MISMATCH", "type sidecar vector differs")
        for field_name, code in zip(PRICE_BOOK_HEADERS, types, strict=True):
            if field_name in _NORMALIZED_REQUIRED_TEXT_FIELDS:
                allowed = {"s", "q"}
            elif field_name in _NORMALIZED_OPTIONAL_TEXT_FIELDS:
                allowed = {"n", "s", "q"}
            elif field_name in _NORMALIZED_OPTIONAL_NUMBER_FIELDS:
                allowed = {"n", "i", "f"}
            elif field_name in _NORMALIZED_REQUIRED_NUMBER_FIELDS:
                allowed = {"i", "f"}
            elif field_name == "assortable":
                allowed = {"n", "b"}
            elif field_name == "source_page":
                allowed = {"i"}
            else:  # pragma: no cover - exact 27-field partition is asserted below
                allowed = set()
            if code not in allowed:
                raise ReviewPackageError(
                    "TYPE_SIDECAR_MISMATCH",
                    f"{field_name} sidecar type is outside the 27-column contract",
                    row=row_number,
                )

    validation = _require_mapping(
        _read_verified_json(source, "validation.json", declared),
        code="INVALID_VALIDATION",
        message="validation evidence must be an object",
    )
    workbook_change_counts = _require_mapping(
        _read_verified_json(source, "workbook_change_counts.json", declared),
        code="INVALID_WORKBOOK_COUNTS",
        message="workbook change counts must be an object",
    )
    workbook_validation = _require_mapping(
        _read_verified_json(source, "workbook_validation.json", declared),
        code="INVALID_WORKBOOK_VALIDATION",
        message="workbook validation must be an object",
    )
    workbook_patches = _verified_jsonl(source, "workbook_row_patches.jsonl", declared)
    consolidated_patches = _verified_jsonl(source, "companion_record_patches.jsonl", declared)
    _validate_workbook_patch_evidence(workbook_patches, workbook_change_counts)
    _validate_workbook_validation(workbook_validation)
    if (
        workbook_validation.get("status") != "PASS"
        or workbook_validation.get("xlsx_sha256") != validation.get("final_workbook_sha256")
        or workbook_validation.get("counts", {}).get("formulas") != validation.get("final_formula_count")
    ):
        raise ReviewPackageError("WORKBOOK_VALIDATION_MISMATCH", "workbook validation evidence differs")
    integrity_checks = _validate_v4_controls_and_relationships(
        tables=tables,
        validation=validation,
        patches_by_table=patches_by_table,
        consolidated_patches=consolidated_patches,
        workbook_patches=workbook_patches,
        workbook_change_counts=workbook_change_counts,
    )
    integrity_checks = {
        **integrity_checks,
        "baseline_preconditions": "PASS",
        "unchanged_artifact_manifest": "PASS",
        "unchanged_artifact_records": len(unchanged_artifacts),
    }

    unavailable: list[str] = []
    mechanical_replay_completed = False
    if baseline_path is None:
        unavailable.extend(
            (
                "EXACT_V4_BASELINE_ARCHIVE",
                "ORIGINAL_SUPPLIER_PDFS",
                "FINAL_V4_1_WORKBOOK_BYTES",
                "TIER_SOURCE_JOIN_VALIDATION",
                "EXACT_REGISTRY_SCHEMA_ADAPTER",
                "EXPOSED_EFFECTIVE_PROJECTION_VALIDATION",
                "REPRESENTATION_GAP_ADAPTER_REQUIRED",
            )
        )
    else:
        with _open_source(baseline_path, source.limits) as baseline:
            missing = sorted(row["path"] for row in unchanged_artifacts if row["path"] not in baseline.names)
            base_rows_by_table: dict[str, list[dict[str, Any]]] = {}
            base_raw_sha_by_table: dict[str, str] = {}
            if missing:
                unavailable.append("INCOMPLETE_EXACT_V4_BASELINE:" + ",".join(sorted(missing)))
            else:
                for artifact in unchanged_artifacts:
                    digest, byte_count = _stream_sha256(baseline, artifact["path"])
                    if digest != artifact["sha256"] or byte_count != artifact["bytes"]:
                        raise ReviewPackageError(
                            "BASELINE_ARTIFACT_MISMATCH",
                            "baseline artifact bytes differ from the V4 frozen manifest",
                            path=artifact["path"],
                        )
                for table_name, item in base_specs.items():
                    base_name = _validated_member_name(item["file"], source.limits)
                    base_rows, base_raw_sha = _read_jsonl(baseline, base_name)
                    if base_raw_sha != item["sha256"] or len(base_rows) != item["rows"]:
                        raise ReviewPackageError("BASELINE_TABLE_MISMATCH", "baseline rows or bytes differ", path=base_name)
                    base_rows_by_table[table_name] = base_rows
                    base_raw_sha_by_table[table_name] = base_raw_sha
                candidate_external = {
                    row_number: dict(patch["stable_key"])
                    for row_number, patch in match_patches.items()
                }
                question_external = {
                    row_number: {
                        "question_id": row["question_id"],
                        "variant_id": row["variant_id"],
                    }
                    for row_number, row in enumerate(tables["owner_question_batch_effective"].rows, start=1)
                }
                link_rows = tables["normalized_price_contract_links_delta"].rows
                normalized_external = {
                    int(row["normalized_table_row_number_1based"]): {
                        "projection_row_id": row["projection_row_id"],
                        "source_tier_id": row["source_tier_id"],
                    }
                    for row in link_rows
                }
                baseline_links = base_rows_by_table["normalized_price_contract_links"]
                observed_tiers: set[str] = set()
                for row_number, proof in normalized_external.items():
                    baseline_link = baseline_links[row_number - 1]
                    if any(baseline_link.get(field_name) != value for field_name, value in proof.items()):
                        raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "baseline Tier link differs from delta proof")
                    tier_id = str(proof["source_tier_id"])
                    if not tier_id or tier_id in observed_tiers:
                        raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "source Tier identity is blank or duplicated")
                    observed_tiers.add(tier_id)

                effective: dict[str, PatchReplay] = {}
                for table_name, contract in _V4_PATCH_CONTRACTS.items():
                    changed = changed_specs[table_name]
                    external = None
                    if table_name == "candidate_projection_eligibility":
                        external = candidate_external
                    elif table_name == "owner_question_batch":
                        external = question_external
                    elif table_name == "normalized_price_contract_review":
                        external = normalized_external
                    replay = replay_patch_table(
                        base_rows_by_table[table_name],
                        patches_by_table[table_name],
                        allowed_fields=set(contract.replace_fields),
                        append_allowed_fields=set(contract.append_fields),
                        required_append_fields=set(contract.required_append_fields),
                        expected_stable_key_fields=(
                            None if contract.key_mode == "EXTERNAL_PROOF" else contract.stable_key_fields
                        ),
                        key_mode=contract.key_mode,
                        external_keys_by_row=external,
                        allow_field_removal=False,
                        expected_rows=contract.effective_rows,
                        expected_sha256=changed["effective_table_canonical_jsonl_sha256"],
                    )
                    second = replay_patch_table(
                        replay.rows,
                        patches_by_table[table_name],
                        allowed_fields=set(contract.replace_fields),
                        append_allowed_fields=set(contract.append_fields),
                        required_append_fields=set(contract.required_append_fields),
                        expected_stable_key_fields=(
                            None if contract.key_mode == "EXTERNAL_PROOF" else contract.stable_key_fields
                        ),
                        key_mode=contract.key_mode,
                        external_keys_by_row=external,
                        allow_field_removal=False,
                        expected_rows=contract.effective_rows,
                        expected_sha256=changed["effective_table_canonical_jsonl_sha256"],
                    )
                    if second.applied or second.rows != replay.rows:
                        raise ReviewPackageError("PATCH_NOT_IDEMPOTENT", "effective table changed on replay")
                    effective[table_name] = replay
                    base_name = base_specs[table_name]["file"]
                    tables[f"effective_{table_name}"] = PackageTable(
                        name=f"effective_{table_name}",
                        path=base_name,
                        format="jsonl",
                        rows=replay.rows,
                        raw_sha256=base_raw_sha_by_table[table_name],
                        canonical_jsonl_sha256=replay.canonical_jsonl_sha256,
                    )
                for link, review_row in zip(
                    tables["normalized_price_contract_links_delta"].rows,
                    tables["normalized_price_contract_review_delta"].rows,
                    strict=True,
                ):
                    row_number = int(link["normalized_table_row_number_1based"])
                    if effective["normalized_price_contract_links"].rows[row_number - 1] != link:
                        raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "effective normalized link differs")
                    if effective["normalized_price_contract_review"].rows[row_number - 1] != review_row:
                        raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "effective normalized review differs")
                for row_number, question in enumerate(tables["owner_question_batch_effective"].rows, start=1):
                    if effective["owner_question_batch"].rows[row_number - 1] != question:
                        raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "effective owner question differs")
                integrity_checks = {
                    **integrity_checks,
                    "exact_v4_baseline_artifacts": "PASS",
                    "exact_v4_baseline_artifact_records": len(unchanged_artifacts),
                    "twelve_changed_tables_patch_replay": "PASS",
                    "twelve_changed_tables_idempotent_replay": "PASS",
                }
                mechanical_replay_completed = True
            unavailable.extend(
                (
                    "FINAL_V4_1_WORKBOOK_BYTES",
                    "TIER_SOURCE_JOIN_VALIDATION",
                    "EXACT_REGISTRY_SCHEMA_ADAPTER",
                    "EXPOSED_EFFECTIVE_PROJECTION_VALIDATION",
                    "REPRESENTATION_GAP_ADAPTER_REQUIRED",
                )
            )
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
            "original_returned",
            "original_not_returned",
            "not_returned_is_deletion",
            "investigated_blocked",
            "unattempted_required_reviews",
            "source_status_before",
            "source_status_after",
            "candidate_occurrences",
            "candidate_disposition_counts",
        )
        if key in validation
    }
    return ReviewPackage(
        source=str(source.path),
        package_kind="V4_1_CLARIFICATION_DELTA",
        snapshot_id="V4.1-CLARIFICATION-DELTA",
        status="SOURCE_EVIDENCE_REQUIRED" if mechanical_replay_completed else "BASELINE_REQUIRED",
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
            "canonical_hash_basis": CANONICAL_JSONL_HASH,
            "integrity_checks": integrity_checks,
            "declared_representation_gap_count": 21,
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
