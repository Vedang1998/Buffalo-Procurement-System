"""Fail-closed, offline intake for private real-source research evidence.

The intake is deliberately outside every operational authority boundary.  It
accepts two code-owned snapshot contracts plus the one exact sealed A1 review
package, preserves their raw bytes in private content-addressed storage, and
publishes one immutable canonical manifest only after all validation succeeds.

There is no database or network access here.  Supplier evidence is attached to
the current catalog solely through an exact Shopify Variant ID.  Supplier SKU,
vendor name, title, and fuzzy similarity are never identity joins.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .storage import LocalFilesystemStorage
from .supplier_mapping_review import build_review_batches
from .supplier_review_package import ReviewPackage, read_review_package
from .supplier_review_real_v5 import (
    A1_ADAPTER,
    A1_PACKAGE_ID,
    A1_PACKAGE_KIND,
    A1_REVIEW_LABEL,
    A1_ROOT_SHA256,
    A1_SEAL_SHA256,
)


CATALOG_SNAPSHOT_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_CATALOG_SNAPSHOT_V1"
DAILY_SALES_SNAPSHOT_CONTRACT = (
    "BUFFALO_PRIVATE_RESEARCH_DAILY_SALES_SNAPSHOT_V1"
)
PRIVATE_RESEARCH_INTAKE_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_INTAKE_V1"
PRIVATE_REAL_SOURCE_REVIEW = "PRIVATE_REAL_SOURCE_REVIEW"
SOURCE_AUTHORITY = {
    "status": "REVIEW_ONLY",
    "approval_status": "UNAPPROVED",
    "operational_authority": False,
}
INTAKE_AUTHORITY = {
    "status": "REVIEW_ONLY",
    "approval_status": "UNAPPROVED",
    "operational_use": "PROHIBITED",
    "mapping_authority": False,
    "price_authority": False,
    "selection_authority": False,
    "inventory_authority": False,
    "forecast_authority": False,
    "procurement_authority": False,
    "shopify_write_authority": False,
    "po_authority": False,
}
ZERO_AUTHORITY = {
    "database_writes": 0,
    "mapping_approvals": 0,
    "price_approvals": 0,
    "selected_offers": 0,
    "activated_prices": 0,
    "inventory_writes": 0,
    "forecast_authorizations": 0,
    "shopify_writes": 0,
    "supplier_messages": 0,
    "po_actions": 0,
}

JSONL_FORMAT = "JSONL_UTF8_CANONICAL_LF"
CATALOG_ROW_FIELDS = (
    "shopify_variant_id",
    "product_title",
    "variant_title",
    "shopify_sku",
    "barcode",
    "current_retail_price",
    "current_inventory_item_cost",
    "product_status",
    "shopify_vendor",
    "product_type",
    "inventory_item_id",
    "inventory_tracked",
)
DAILY_SALES_ROW_FIELDS = (
    "business_date",
    "shopify_variant_id",
    "net_units",
    "net_revenue",
    "historical_cogs",
)

_TOP_LEVEL_FIELDS = frozenset(
    {
        "contract",
        "data_mode",
        "authority",
        "intake_id",
        "sources",
        "coverage",
        "variants",
        "vendors",
        "limitations",
        "zero_authority",
    }
)
_CATALOG_MANIFEST_FIELDS = frozenset(
    {
        "contract",
        "data_mode",
        "authority",
        "snapshot_id",
        "source",
        "extraction",
        "population",
        "query",
        "pagination",
        "blob",
    }
)
_SALES_MANIFEST_FIELDS = _CATALOG_MANIFEST_FIELDS | {"date_range"}
_SOURCE_FIELDS = frozenset({"system", "store_identity", "store_timezone"})
_EXTRACTION_FIELDS = frozenset(
    {"started_at_utc", "completed_at_utc", "snapshot_at_utc"}
)
_CATALOG_POPULATION_FIELDS = frozenset({"row_count", "reported_row_count"})
_SALES_POPULATION_FIELDS = frozenset({"row_count", "distinct_variant_count"})
_QUERY_FIELDS = frozenset({"identity", "sha256"})
_PAGINATION_FIELDS = frozenset(
    {
        "page_size",
        "expected_pages",
        "completed_pages",
        "pages",
        "terminal_page_seen",
        "truncated",
    }
)
_PAGE_FIELDS = frozenset(
    {"page_index", "first_row_index", "row_count", "sha256", "terminal"}
)
_BLOB_FIELDS = frozenset({"path", "bytes", "sha256", "format", "schema"})
_DATE_RANGE_FIELDS = frozenset(
    {
        "start_date",
        "end_date",
        "complete_through_date",
        "store_timezone",
        "complete_day_count",
        "missing_dates",
    }
)
_BLOB_REF_FIELDS = frozenset({"key", "bytes", "sha256", "media_type"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VARIANT_ID = re.compile(r"^[1-9][0-9]*$")
_INTAKE_PREFIX = "private-research/intakes"
_BLOB_PREFIX = "private-research/blobs/sha256"
_MAX_MANIFEST_BYTES = 16_000_000
_MAX_BLOB_BYTES = 1_000_000_000
_MAX_JSONL_LINE_BYTES = 8_000_000
_MAX_ROWS = 2_500_000
_ABC_DAYS = 84


class PrivateResearchIntakeError(ValueError):
    """Stable fail-closed validation error for private research intake."""

    def __init__(self, code: str, message: str, *, path: str | None = None):
        self.code = code
        self.path = path
        suffix = f" ({path})" if path else ""
        super().__init__(f"{code}: {message}{suffix}")


@dataclass(frozen=True)
class _Snapshot:
    manifest: dict[str, Any]
    manifest_bytes: bytes
    data_bytes: bytes
    rows: tuple[dict[str, Any], ...]


def canonical_json_bytes(value: Any) -> bytes:
    """Return the sole accepted canonical JSON representation, including LF."""

    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PrivateResearchIntakeError(
                "DUPLICATE_JSON_KEY", f"duplicate JSON key {key!r}"
            )
        value[key] = item
    return value


def _parse_canonical_json(raw: bytes, *, path: str) -> dict[str, Any]:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrivateResearchIntakeError(
            "INVALID_UTF8", "JSON bytes are not UTF-8", path=path
        ) from exc
    try:
        value = json.loads(decoded, object_pairs_hook=_duplicate_rejecting_object)
    except PrivateResearchIntakeError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise PrivateResearchIntakeError(
            "INVALID_JSON", "JSON cannot be parsed", path=path
        ) from exc
    if not isinstance(value, dict):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "JSON document must be an object", path=path
        )
    try:
        expected = canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PrivateResearchIntakeError(
            "INVALID_JSON_VALUE", "JSON contains an unsupported value", path=path
        ) from exc
    if raw != expected:
        raise PrivateResearchIntakeError(
            "NONCANONICAL_JSON",
            "JSON bytes must use sorted compact UTF-8 with one trailing LF",
            path=path,
        )
    return value


def _exact_fields(value: Any, expected: frozenset[str], *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH",
            f"{context} fields differ; missing={missing}, extra={extra}",
        )
    return value


def _exact_typed_mapping(
    value: Any, expected: Mapping[str, Any], *, context: str
) -> dict[str, Any]:
    result = _exact_fields(value, frozenset(expected), context=context)
    if any(
        type(result[key]) is not type(expected_value)
        or result[key] != expected_value
        for key, expected_value in expected.items()
    ):
        raise PrivateResearchIntakeError(
            "AUTHORITY_MISMATCH", f"{context} values or JSON types differ"
        )
    return result


def _integer(value: Any, *, context: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PrivateResearchIntakeError(
            "INVALID_COUNT", f"{context} must be an integer >= {minimum}"
        )
    return value


def _text(value: Any, *, context: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PrivateResearchIntakeError(
            "INVALID_TEXT", f"{context} must be nonblank trimmed text"
        )
    return value


def _sha256(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PrivateResearchIntakeError(
            "INVALID_SHA256", f"{context} must be lowercase SHA-256"
        )
    return value


def _variant_id(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not _VARIANT_ID.fullmatch(value):
        raise PrivateResearchIntakeError(
            "INVALID_VARIANT_ID",
            f"{context} must be a canonical positive decimal Shopify Variant ID",
        )
    return value


def _decimal_text(
    value: Any,
    *,
    context: str,
    nullable: bool = False,
    nonnegative: bool = False,
) -> Decimal | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise PrivateResearchIntakeError(
            "INVALID_DECIMAL", f"{context} must be a canonical decimal string"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PrivateResearchIntakeError(
            "INVALID_DECIMAL", f"{context} is not a decimal"
        ) from exc
    if not parsed.is_finite() or format(parsed, "f") != value:
        raise PrivateResearchIntakeError(
            "INVALID_DECIMAL", f"{context} must be finite non-exponent decimal text"
        )
    if nonnegative and parsed < 0:
        raise PrivateResearchIntakeError(
            "INVALID_DECIMAL", f"{context} must be nonnegative"
        )
    return parsed


def _decimal_output(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _utc_datetime(value: Any, *, context: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PrivateResearchIntakeError(
            "INVALID_UTC_TIME", f"{context} must be an RFC3339 UTC value ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PrivateResearchIntakeError(
            "INVALID_UTC_TIME", f"{context} is not a valid UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PrivateResearchIntakeError(
            "INVALID_UTC_TIME", f"{context} is not UTC"
        )
    return parsed.astimezone(timezone.utc)


def _iso_date(value: Any, *, context: str) -> date:
    if not isinstance(value, str):
        raise PrivateResearchIntakeError(
            "INVALID_DATE", f"{context} must be YYYY-MM-DD"
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PrivateResearchIntakeError(
            "INVALID_DATE", f"{context} must be YYYY-MM-DD"
        ) from exc
    if parsed.isoformat() != value:
        raise PrivateResearchIntakeError(
            "INVALID_DATE", f"{context} must be canonical YYYY-MM-DD"
        )
    return parsed


def _timezone(value: Any, *, context: str) -> ZoneInfo:
    name = _text(value, context=context)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise PrivateResearchIntakeError(
            "INVALID_TIMEZONE", f"{context} is not an IANA timezone"
        ) from exc


def validate_private_root(root: str | Path) -> Path:
    """Require one absolute, owned, non-symlink directory with exact mode 0700."""

    requested = Path(root)
    if not requested.is_absolute():
        raise PrivateResearchIntakeError(
            "PRIVATE_ROOT_NOT_ABSOLUTE", "private root must be an absolute path"
        )
    try:
        metadata = requested.lstat()
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise PrivateResearchIntakeError(
            "PRIVATE_ROOT_INVALID", "private root does not exist"
        ) from exc
    if requested != resolved or stat.S_ISLNK(metadata.st_mode):
        raise PrivateResearchIntakeError(
            "PRIVATE_ROOT_SYMLINK", "private root and its path must not use symlinks"
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise PrivateResearchIntakeError(
            "PRIVATE_ROOT_INVALID", "private root must be a directory"
        )
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise PrivateResearchIntakeError(
            "PRIVATE_ROOT_MODE", "private root mode must be exactly 0700"
        )
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise PrivateResearchIntakeError(
            "PRIVATE_ROOT_OWNER", "private root must be owned by the current user"
        )
    return resolved


def _safe_key(value: str | Path, *, context: str) -> str:
    text = str(value)
    if (
        not text
        or "\x00" in text
        or "\\" in text
        or Path(text).is_absolute()
        or unicodedata.normalize("NFC", text) != text
    ):
        raise PrivateResearchIntakeError(
            "UNSAFE_PATH", f"{context} must be a normalized relative POSIX path"
        )
    path = PurePosixPath(text)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise PrivateResearchIntakeError(
            "UNSAFE_PATH", f"{context} contains traversal or an empty component"
        )
    return path.as_posix()


def _private_entry(
    root: Path,
    value: str | Path,
    *,
    context: str,
    directory: bool = False,
) -> tuple[str, Path]:
    supplied = Path(value)
    if supplied.is_absolute():
        candidate = supplied
    else:
        key = _safe_key(value, context=context)
        candidate = root / PurePosixPath(key)
    try:
        resolved = candidate.resolve(strict=True)
        metadata = candidate.lstat()
    except OSError as exc:
        raise PrivateResearchIntakeError(
            "MISSING_PRIVATE_SOURCE", f"{context} is missing"
        ) from exc
    if not resolved.is_relative_to(root) or resolved != candidate:
        raise PrivateResearchIntakeError(
            "UNSAFE_PATH", f"{context} escapes the private root or uses a symlink"
        )
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if stat.S_ISLNK(metadata.st_mode) or not expected_type(metadata.st_mode):
        kind = "directory" if directory else "regular file"
        raise PrivateResearchIntakeError(
            "UNSAFE_PATH", f"{context} must be a non-symlink {kind}"
        )
    return resolved.relative_to(root).as_posix(), resolved


def _read_file(path: Path, *, maximum: int, context: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PrivateResearchIntakeError(
            "PRIVATE_SOURCE_READ_FAILED", f"cannot open {context}"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise PrivateResearchIntakeError(
                "SOURCE_SIZE_INVALID", f"{context} exceeds its byte limit"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1_048_576, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise PrivateResearchIntakeError(
                    "SOURCE_SIZE_INVALID", f"{context} exceeds its byte limit"
                )
        after = os.fstat(descriptor)
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if not stable or total != before.st_size:
            raise PrivateResearchIntakeError(
                "SOURCE_CHANGED", f"{context} changed while it was read"
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_source_header(
    manifest: Mapping[str, Any], *, expected_system: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _exact_fields(manifest.get("source"), _SOURCE_FIELDS, context="source")
    if source.get("system") != expected_system:
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", f"source system must be {expected_system}"
        )
    _text(source.get("store_identity"), context="source store identity")
    _timezone(source.get("store_timezone"), context="source store timezone")

    extraction = _exact_fields(
        manifest.get("extraction"), _EXTRACTION_FIELDS, context="extraction"
    )
    started = _utc_datetime(
        extraction.get("started_at_utc"), context="extraction start"
    )
    completed = _utc_datetime(
        extraction.get("completed_at_utc"), context="extraction completion"
    )
    snapshot = _utc_datetime(
        extraction.get("snapshot_at_utc"), context="snapshot time"
    )
    if not (started <= snapshot <= completed):
        raise PrivateResearchIntakeError(
            "INVALID_EXTRACTION_RANGE",
            "snapshot time must be within the extraction interval",
        )

    query = _exact_fields(manifest.get("query"), _QUERY_FIELDS, context="query")
    _text(query.get("identity"), context="query identity")
    _sha256(query.get("sha256"), context="query SHA-256")
    return source, extraction


def _validate_pagination(
    value: Any, *, row_count: int
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    pagination = _exact_fields(value, _PAGINATION_FIELDS, context="pagination")
    page_size = _integer(
        pagination.get("page_size"), context="pagination page size", minimum=1
    )
    if page_size > 100_000:
        raise PrivateResearchIntakeError(
            "INVALID_PAGINATION", "pagination page size exceeds 100000"
        )
    expected = _integer(
        pagination.get("expected_pages"), context="expected pages", minimum=1
    )
    completed = _integer(
        pagination.get("completed_pages"), context="completed pages", minimum=1
    )
    pages_value = pagination.get("pages")
    if not isinstance(pages_value, list):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "pagination pages must be an array"
        )
    pages = tuple(
        _exact_fields(row, _PAGE_FIELDS, context="pagination page")
        for row in pages_value
    )
    calculated_pages = max(1, math.ceil(row_count / page_size))
    if (
        expected != calculated_pages
        or completed != expected
        or len(pages) != expected
        or pagination.get("terminal_page_seen") is not True
        or pagination.get("truncated") is not False
    ):
        raise PrivateResearchIntakeError(
            "PAGINATION_INCOMPLETE",
            "page counts, terminal evidence, or truncation state differ",
        )
    offset = 0
    for index, page in enumerate(pages):
        page_index = _integer(
            page.get("page_index"), context="page index", minimum=0
        )
        first = _integer(
            page.get("first_row_index"), context="page first row", minimum=0
        )
        count = _integer(page.get("row_count"), context="page row count", minimum=0)
        _sha256(page.get("sha256"), context="page SHA-256")
        is_last = index == len(pages) - 1
        if (
            page_index != index
            or first != offset
            or (not is_last and count != page_size)
            or count > page_size
            or page.get("terminal") is not is_last
        ):
            raise PrivateResearchIntakeError(
                "PAGINATION_INCOMPLETE",
                "pages must be contiguous, bounded, and terminal only at the end",
            )
        offset += count
    if offset != row_count:
        raise PrivateResearchIntakeError(
            "PAGINATION_INCOMPLETE", "page row counts do not cover the source rows"
        )
    return pagination, pages


def _parse_jsonl(
    raw: bytes,
    *,
    path: str,
    schema: tuple[str, ...],
    pages: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    lines = raw.splitlines(keepends=True)
    if len(lines) > _MAX_ROWS:
        raise PrivateResearchIntakeError(
            "TOO_MANY_ROWS", "JSONL row count exceeds the intake limit", path=path
        )
    rows: list[dict[str, Any]] = []
    for row_number, line in enumerate(lines, start=1):
        if len(line) > _MAX_JSONL_LINE_BYTES:
            raise PrivateResearchIntakeError(
                "JSONL_LINE_TOO_LARGE", "JSONL line exceeds the limit", path=path
            )
        if not line.endswith(b"\n") or line in {b"\n", b"\r\n"}:
            raise PrivateResearchIntakeError(
                "NONCANONICAL_JSONL",
                f"JSONL row {row_number} must be nonblank and LF terminated",
                path=path,
            )
        row = _parse_canonical_json(line, path=f"{path}:{row_number}")
        _exact_fields(row, frozenset(schema), context=f"JSONL row {row_number}")
        rows.append(row)
    if not raw and lines:
        raise AssertionError("splitlines returned rows for an empty payload")
    for page in pages:
        first = int(page["first_row_index"])
        count = int(page["row_count"])
        page_bytes = b"".join(lines[first : first + count])
        if hashlib.sha256(page_bytes).hexdigest() != page["sha256"]:
            raise PrivateResearchIntakeError(
                "PAGE_HASH_MISMATCH",
                f"page {page['page_index']} bytes differ from its SHA-256",
                path=path,
            )
    return tuple(rows)


def _validate_blob(
    manifest: Mapping[str, Any],
    data: bytes,
    *,
    schema: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    blob = _exact_fields(manifest.get("blob"), _BLOB_FIELDS, context="blob")
    path = _safe_key(blob.get("path"), context="blob path")
    size = _integer(blob.get("bytes"), context="blob bytes", minimum=0)
    digest = _sha256(blob.get("sha256"), context="blob SHA-256")
    if blob.get("format") != JSONL_FORMAT or blob.get("schema") != list(schema):
        raise PrivateResearchIntakeError(
            "BLOB_CONTRACT_MISMATCH", "blob format or schema differs"
        )
    if size != len(data) or digest != hashlib.sha256(data).hexdigest():
        raise PrivateResearchIntakeError(
            "CONTENT_HASH_MISMATCH", "blob byte count or SHA-256 differs", path=path
        )
    population = manifest.get("population")
    if not isinstance(population, dict):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "population must be an object"
        )
    row_count = _integer(population.get("row_count"), context="row count", minimum=0)
    pagination, pages = _validate_pagination(
        manifest.get("pagination"), row_count=row_count
    )
    rows = _parse_jsonl(data, path=path, schema=schema, pages=pages)
    if len(rows) != row_count:
        raise PrivateResearchIntakeError(
            "ROW_COUNT_MISMATCH", "JSONL row count differs from population"
        )
    return pagination, rows


def _catalog_snapshot(manifest_bytes: bytes, data_bytes: bytes) -> _Snapshot:
    manifest = _parse_canonical_json(manifest_bytes, path="catalog manifest")
    _exact_fields(manifest, _CATALOG_MANIFEST_FIELDS, context="catalog manifest")
    if (
        manifest.get("contract") != CATALOG_SNAPSHOT_CONTRACT
        or manifest.get("data_mode") != PRIVATE_REAL_SOURCE_REVIEW
        or not isinstance(manifest.get("authority"), Mapping)
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", "catalog contract, mode, or authority differs"
        )
    _exact_typed_mapping(
        manifest.get("authority"), SOURCE_AUTHORITY, context="catalog authority"
    )
    _text(manifest.get("snapshot_id"), context="catalog snapshot ID")
    _validate_source_header(manifest, expected_system="SHOPIFY_ADMIN_GRAPHQL")
    population = _exact_fields(
        manifest.get("population"),
        _CATALOG_POPULATION_FIELDS,
        context="catalog population",
    )
    row_count = _integer(population.get("row_count"), context="catalog rows", minimum=1)
    reported = _integer(
        population.get("reported_row_count"),
        context="reported catalog rows",
        minimum=1,
    )
    if reported != row_count:
        raise PrivateResearchIntakeError(
            "CATALOG_TRUNCATED",
            "reported catalog population differs from delivered rows",
        )
    _, rows = _validate_blob(manifest, data_bytes, schema=CATALOG_ROW_FIELDS)
    seen: set[str] = set()
    for row in rows:
        variant = _variant_id(
            row.get("shopify_variant_id"), context="catalog Shopify Variant ID"
        )
        if variant in seen:
            raise PrivateResearchIntakeError(
                "DUPLICATE_VARIANT_ID", "catalog Shopify Variant ID is duplicated"
            )
        seen.add(variant)
        _text(row.get("product_title"), context="catalog product title")
        _text(row.get("variant_title"), context="catalog variant title")
        for field in (
            "shopify_sku",
            "barcode",
            "shopify_vendor",
            "product_type",
            "inventory_item_id",
        ):
            _text(row.get(field), context=f"catalog {field}", nullable=True)
        _decimal_text(
            row.get("current_retail_price"),
            context="current retail price",
            nullable=True,
            nonnegative=True,
        )
        _decimal_text(
            row.get("current_inventory_item_cost"),
            context="current inventory item cost",
            nullable=True,
            nonnegative=True,
        )
        status_value = _text(row.get("product_status"), context="product status")
        if status_value not in {"ACTIVE", "ARCHIVED", "DRAFT"}:
            raise PrivateResearchIntakeError(
                "INVALID_CATALOG_STATUS", "catalog product status is unsupported"
            )
        if row.get("inventory_tracked") is not None and type(
            row.get("inventory_tracked")
        ) is not bool:
            raise PrivateResearchIntakeError(
                "SCHEMA_MISMATCH", "inventory_tracked must be boolean or null"
            )
    return _Snapshot(manifest, manifest_bytes, data_bytes, rows)


def _sales_snapshot(manifest_bytes: bytes, data_bytes: bytes) -> _Snapshot:
    manifest = _parse_canonical_json(manifest_bytes, path="daily-sales manifest")
    _exact_fields(manifest, _SALES_MANIFEST_FIELDS, context="daily-sales manifest")
    if (
        manifest.get("contract") != DAILY_SALES_SNAPSHOT_CONTRACT
        or manifest.get("data_mode") != PRIVATE_REAL_SOURCE_REVIEW
        or not isinstance(manifest.get("authority"), Mapping)
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH",
            "daily-sales contract, mode, or authority differs",
        )
    _exact_typed_mapping(
        manifest.get("authority"),
        SOURCE_AUTHORITY,
        context="daily-sales authority",
    )
    _text(manifest.get("snapshot_id"), context="daily-sales snapshot ID")
    source, extraction = _validate_source_header(
        manifest, expected_system="SHOPIFYQL_SALES"
    )
    population = _exact_fields(
        manifest.get("population"),
        _SALES_POPULATION_FIELDS,
        context="daily-sales population",
    )
    _integer(population.get("row_count"), context="daily-sales rows", minimum=0)
    declared_variants = _integer(
        population.get("distinct_variant_count"),
        context="daily-sales distinct variants",
        minimum=0,
    )
    date_range = _exact_fields(
        manifest.get("date_range"), _DATE_RANGE_FIELDS, context="date range"
    )
    start = _iso_date(date_range.get("start_date"), context="sales start date")
    end = _iso_date(date_range.get("end_date"), context="sales end date")
    through = _iso_date(
        date_range.get("complete_through_date"), context="sales complete-through date"
    )
    if end < start or through != end:
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE",
            "sales range must be ordered and complete through its end date",
        )
    if date_range.get("store_timezone") != source.get("store_timezone"):
        raise PrivateResearchIntakeError(
            "TIMEZONE_MISMATCH", "sales date-range and source timezones differ"
        )
    zone = _timezone(date_range.get("store_timezone"), context="sales timezone")
    completed_at = _utc_datetime(
        extraction.get("completed_at_utc"), context="sales extraction completion"
    )
    if end > completed_at.astimezone(zone).date():
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE", "sales end date is after extraction local date"
        )
    day_count = (end - start).days + 1
    if (
        _integer(
            date_range.get("complete_day_count"),
            context="complete sales days",
            minimum=1,
        )
        != day_count
        or date_range.get("missing_dates") != []
    ):
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE",
            "every day in the sales range must be explicitly complete",
        )
    _, rows = _validate_blob(manifest, data_bytes, schema=DAILY_SALES_ROW_FIELDS)
    seen: set[tuple[str, str]] = set()
    variants: set[str] = set()
    for row in rows:
        business_date = _iso_date(
            row.get("business_date"), context="sales business date"
        )
        if business_date < start or business_date > end:
            raise PrivateResearchIntakeError(
                "DATE_OUT_OF_RANGE", "sales row is outside the declared date range"
            )
        variant = _variant_id(
            row.get("shopify_variant_id"), context="sales Shopify Variant ID"
        )
        key = (business_date.isoformat(), variant)
        if key in seen:
            raise PrivateResearchIntakeError(
                "DUPLICATE_SALES_FACT",
                "daily sales must be unique by business date and Shopify Variant ID",
            )
        seen.add(key)
        variants.add(variant)
        _decimal_text(row.get("net_units"), context="sales net units")
        _decimal_text(row.get("net_revenue"), context="sales net revenue")
        _decimal_text(
            row.get("historical_cogs"), context="historical COGS", nullable=True
        )
    if len(variants) != declared_variants:
        raise PrivateResearchIntakeError(
            "ROW_COUNT_MISMATCH", "daily-sales distinct Variant count differs"
        )
    return _Snapshot(manifest, manifest_bytes, data_bytes, rows)


def _load_snapshot_from_path(
    root: Path, manifest_value: str | Path, *, kind: str
) -> tuple[str, _Snapshot]:
    manifest_key, manifest_path = _private_entry(
        root, manifest_value, context=f"{kind} manifest"
    )
    manifest_bytes = _read_file(
        manifest_path, maximum=_MAX_MANIFEST_BYTES, context=f"{kind} manifest"
    )
    parsed = _parse_canonical_json(manifest_bytes, path=f"{kind} manifest")
    blob_value = parsed.get("blob")
    if not isinstance(blob_value, dict) or "path" not in blob_value:
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", f"{kind} manifest lacks a blob path"
        )
    _, data_path = _private_entry(
        root, blob_value["path"], context=f"{kind} data blob"
    )
    data_bytes = _read_file(
        data_path, maximum=_MAX_BLOB_BYTES, context=f"{kind} data blob"
    )
    snapshot = (
        _catalog_snapshot(manifest_bytes, data_bytes)
        if kind == "catalog"
        else _sales_snapshot(manifest_bytes, data_bytes)
    )
    return manifest_key, snapshot


def _blob_key(digest: str) -> str:
    _sha256(digest, context="content blob digest")
    return f"{_BLOB_PREFIX}/{digest}"


def _blob_ref(data: bytes, *, media_type: str) -> dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    return {
        "key": _blob_key(digest),
        "bytes": len(data),
        "sha256": digest,
        "media_type": media_type,
    }


def _publish_once(
    storage: LocalFilesystemStorage, reference: Mapping[str, Any], data: bytes
) -> None:
    key = str(reference["key"])
    try:
        storage.put_bytes_once(key, data)
    except FileExistsError:
        try:
            existing = storage.get_bytes(key)
        except OSError as exc:
            raise PrivateResearchIntakeError(
                "IMMUTABLE_OBJECT_CONFLICT", "existing immutable object is unreadable"
            ) from exc
        if existing != data:
            raise PrivateResearchIntakeError(
                "IMMUTABLE_OBJECT_CONFLICT",
                "existing content-addressed object has different bytes",
                path=key,
            )


def _json_ready(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if isinstance(value, Decimal):
        return _decimal_output(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PrivateResearchIntakeError(
                "INVALID_JSON_VALUE", "non-finite floating point evidence is forbidden"
            )
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PrivateResearchIntakeError(
                    "INVALID_JSON_VALUE", "evidence object keys must be text"
                )
            result[key] = _json_ready(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_ready(item) for item in value]
    raise PrivateResearchIntakeError(
        "INVALID_JSON_VALUE", f"unsupported evidence value {type(value).__name__}"
    )


def _validate_a1_package(package: ReviewPackage) -> None:
    expected_cohorts = {
        "original_cohort_count": 2_000,
        "current_census_count": 2_003,
        "current_original_returned": 1_999,
        "current_additions": 4,
    }
    metadata = package.metadata
    authority = metadata.get("authority") if isinstance(metadata, Mapping) else None
    readiness = metadata.get("readiness") if isinstance(metadata, Mapping) else None
    integrity = (
        metadata.get("integrity_checks") if isinstance(metadata, Mapping) else None
    )
    if (
        package.package_kind != A1_PACKAGE_KIND
        or package.snapshot_id != A1_PACKAGE_ID
        or package.status != "REVIEW_ONLY_VALIDATED"
        or package.label != A1_REVIEW_LABEL
        or package.manifest_sha256 != A1_ROOT_SHA256
        or package.file_count <= 0
        or package.verified_file_count != package.file_count
        or dict(package.cohorts) != expected_cohorts
        or not isinstance(metadata, Mapping)
        or metadata.get("adapter") != A1_ADAPTER
        or not isinstance(authority, Mapping)
        or authority.get("label") != A1_REVIEW_LABEL
        or type(authority.get("mapping_approvals")) is not int
        or authority.get("mapping_approvals") != 0
        or type(authority.get("price_approvals")) is not int
        or authority.get("price_approvals") != 0
        or type(authority.get("import_ready_rows")) is not int
        or authority.get("import_ready_rows") != 0
        or type(authority.get("operational_effects")) is not int
        or authority.get("operational_effects") != 0
        or not isinstance(readiness, Mapping)
        or readiness.get("raw_file_integrity") != "PASS"
        or readiness.get("effective_snapshot_restoration") != "PASS"
        or readiness.get("structural_replay") != "PASS"
        or readiness.get("relationship_validation") != "PASS"
        or readiness.get("mapping_approval") != "NOT_APPROVED"
        or readiness.get("price_approval") != "NOT_APPROVED"
        or readiness.get("import_readiness") != "NOT_IMPORT_READY"
        or not isinstance(integrity, Mapping)
        or integrity.get("root_raw_sha256") != A1_ROOT_SHA256
        or integrity.get("seal_raw_sha256") != A1_SEAL_SHA256
    ):
        raise PrivateResearchIntakeError(
            "A1_CONTRACT_MISMATCH", "review package is not the exact sealed A1 package"
        )
    source_evidence = metadata.get("source_evidence")
    external_pdf_status = metadata.get("external_pdf_status")
    page_bundle_status = metadata.get("page_bundle_status")
    if (
        not isinstance(source_evidence, Mapping)
        or not isinstance(external_pdf_status, Mapping)
        or not isinstance(page_bundle_status, Mapping)
    ):
        raise PrivateResearchIntakeError(
            "A1_SOURCE_EVIDENCE_MISSING",
            "A1 source, page, and unavailable-evidence controls are required",
        )
    for filename, item in external_pdf_status.items():
        if not isinstance(filename, str) or not isinstance(item, Mapping):
            raise PrivateResearchIntakeError(
                "A1_SOURCE_EVIDENCE_MISSING", "A1 PDF status is malformed"
            )
        _sha256(item.get("sha256"), context=f"A1 source PDF {filename}")
        _integer(item.get("bytes"), context=f"A1 source PDF {filename} bytes", minimum=1)
        _integer(
            item.get("physical_pages"),
            context=f"A1 source PDF {filename} pages",
            minimum=1,
        )
        _text(item.get("availability"), context=f"A1 source PDF {filename} availability")


def _a1_hypotheses(
    package: ReviewPackage,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    batches = build_review_batches(package)
    if len(batches) != 2_000:
        raise PrivateResearchIntakeError(
            "A1_POPULATION_MISMATCH", "A1 review batches must preserve all 2000 Variants"
        )
    by_variant: dict[str, list[dict[str, Any]]] = {}
    occurrence_ids: set[tuple[str, str]] = set()
    for batch in batches:
        if not isinstance(batch, Mapping):
            raise PrivateResearchIntakeError(
                "A1_PROJECTION_MISMATCH", "A1 review batch must be an object"
            )
        variant = _variant_id(batch.get("variant_id"), context="A1 batch Variant ID")
        if variant in by_variant:
            raise PrivateResearchIntakeError(
                "A1_POPULATION_MISMATCH", "A1 batch Variant ID is duplicated"
            )
        authority = batch.get("authority")
        if (
            not isinstance(authority, Mapping)
            or type(authority.get("mapping_approvals")) is not int
            or authority.get("mapping_approvals") != 0
            or type(authority.get("price_approvals")) is not int
            or authority.get("price_approvals") != 0
            or type(authority.get("import_ready_rows")) is not int
            or authority.get("import_ready_rows") != 0
        ):
            raise PrivateResearchIntakeError(
                "A1_AUTHORITY_MISMATCH", "A1 batch carries nonzero authority"
            )
        offers = batch.get("offers")
        if not isinstance(offers, list):
            raise PrivateResearchIntakeError(
                "A1_PROJECTION_MISMATCH", "A1 batch offers must be an array"
            )
        hypotheses: list[dict[str, Any]] = []
        for offer in offers:
            if not isinstance(offer, Mapping):
                raise PrivateResearchIntakeError(
                    "A1_PROJECTION_MISMATCH", "A1 offer preview must be an object"
                )
            if _variant_id(
                offer.get("variant_id"), context="A1 offer Variant ID"
            ) != variant:
                raise PrivateResearchIntakeError(
                    "A1_IDENTITY_JOIN_MISMATCH",
                    "A1 offer Variant ID differs from its parent batch",
                )
            offer_authority = offer.get("authority")
            if (
                not isinstance(offer_authority, Mapping)
                or any(value is not False for value in offer_authority.values())
            ):
                raise PrivateResearchIntakeError(
                    "A1_AUTHORITY_MISMATCH", "A1 offer carries authority"
                )
            occurrence = _text(
                offer.get("source_occurrence_id"), context="A1 source occurrence ID"
            )
            pair = (variant, occurrence)
            if pair in occurrence_ids:
                raise PrivateResearchIntakeError(
                    "A1_PROJECTION_MISMATCH", "A1 source occurrence is duplicated"
                )
            occurrence_ids.add(pair)
            vendor_name = _text(offer.get("vendor"), context="A1 vendor label")
            source = offer.get("source")
            if not isinstance(source, Mapping):
                raise PrivateResearchIntakeError(
                    "A1_SOURCE_EVIDENCE_MISSING", "A1 offer source reference is missing"
                )
            source_sha = source.get("sha256")
            if source_sha is not None:
                _sha256(source_sha, context="A1 offer source SHA-256")
            hypotheses.append(
                _json_ready(
                    {
                        "shopify_variant_id": variant,
                        "vendor_name": vendor_name,
                        "supplier_sku": offer.get("supplier_code"),
                        "supplier_description": offer.get("supplier_description"),
                        "source_occurrence_id": occurrence,
                        "mapping_status": "UNAPPROVED_"
                        + str(offer.get("candidate_disposition") or "UNRESOLVED"),
                        "mapping_confidence": None,
                        "mapping_evidence": {
                            "candidate_disposition": offer.get("candidate_disposition"),
                            "blockers": offer.get("blockers"),
                            "relationship_record_sha256": offer.get(
                                "relationship_record_sha256"
                            ),
                            "offer_preview_fingerprint": offer.get(
                                "offer_preview_fingerprint"
                            ),
                        },
                        "current_unit_cost": None,
                        "package_type": offer.get("program_type"),
                        "units_per_case": offer.get(
                            "reviewed_shopify_units_per_case"
                        ),
                        "qualifying_units_per_case": offer.get(
                            "reviewed_qualifying_units_per_case"
                        ),
                        "break_unit": None,
                        "assortment_scope": None,
                        "assortment_group": None,
                        "allocated_excluded": None,
                        "combo_excluded": None,
                        "source_ref": {
                            key: source.get(key)
                            for key in (
                                "file",
                                "page",
                                "sha256",
                                "period",
                                "territory",
                                "availability",
                            )
                            if key in source
                        },
                        "unapproved_price_ladder_evidence": offer.get(
                            "price_ladder", []
                        ),
                        "authority": {
                            "mapping_approved": False,
                            "price_approved": False,
                            "selected_offer": False,
                            "import_ready": False,
                        },
                    }
                )
            )
        hypotheses.sort(
            key=lambda item: (
                str(item["vendor_name"]),
                str(item["source_occurrence_id"]),
            )
        )
        by_variant[variant] = hypotheses
    return by_variant, set(by_variant)


def _source_descriptor(
    snapshot: _Snapshot,
    *,
    manifest_ref: Mapping[str, Any],
    data_ref: Mapping[str, Any],
    schema: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "contract": snapshot.manifest["contract"],
        "snapshot_id": snapshot.manifest["snapshot_id"],
        "manifest_blob": dict(manifest_ref),
        "data_blob": {**dict(data_ref), "schema": list(schema)},
        "source": _json_ready(snapshot.manifest["source"]),
        "extraction": _json_ready(snapshot.manifest["extraction"]),
        "query": _json_ready(snapshot.manifest["query"]),
        "pagination": _json_ready(snapshot.manifest["pagination"]),
    }


def _a1_descriptor(
    package: ReviewPackage,
    *,
    private_path: str,
    external_evidence_path: str | None,
) -> dict[str, Any]:
    metadata = package.metadata
    return {
        "private_path": private_path,
        "external_evidence_path": external_evidence_path,
        "adapter": metadata["adapter"],
        "package_kind": package.package_kind,
        "snapshot_id": package.snapshot_id,
        "status": package.status,
        "label": package.label,
        "manifest_sha256": package.manifest_sha256,
        "file_count": package.file_count,
        "verified_file_count": package.verified_file_count,
        "cohorts": _json_ready(package.cohorts),
        "readiness": _json_ready(metadata["readiness"]),
        "integrity_checks": _json_ready(metadata["integrity_checks"]),
        "source_evidence": _json_ready(metadata["source_evidence"]),
        "external_pdf_status": _json_ready(metadata["external_pdf_status"]),
        "page_bundle_status": _json_ready(metadata["page_bundle_status"]),
        "unavailable_evidence": _json_ready(package.unavailable_evidence),
        "authority": _json_ready(metadata["authority"]),
    }


def _snapshot_dates(snapshot: _Snapshot) -> tuple[date, date, list[date]]:
    date_range = snapshot.manifest["date_range"]
    start = date.fromisoformat(date_range["start_date"])
    end = date.fromisoformat(date_range["end_date"])
    dates = [start + timedelta(days=index) for index in range((end - start).days + 1)]
    return start, end, dates


def _assemble_intake(
    catalog: _Snapshot,
    sales: _Snapshot,
    package: ReviewPackage,
    *,
    catalog_manifest_ref: Mapping[str, Any],
    catalog_data_ref: Mapping[str, Any],
    sales_manifest_ref: Mapping[str, Any],
    sales_data_ref: Mapping[str, Any],
    a1_private_path: str,
    a1_external_evidence_path: str | None,
) -> dict[str, Any]:
    catalog_source = catalog.manifest["source"]
    sales_source = sales.manifest["source"]
    if (
        catalog_source["store_identity"] != sales_source["store_identity"]
        or catalog_source["store_timezone"] != sales_source["store_timezone"]
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_SCOPE_MISMATCH", "catalog and sales store identity/timezone differ"
        )
    catalog_snapshot_at = _utc_datetime(
        catalog.manifest["extraction"]["snapshot_at_utc"],
        context="catalog snapshot time",
    )
    zone = _timezone(
        catalog_source["store_timezone"], context="catalog store timezone"
    )
    _, sales_end, sales_dates = _snapshot_dates(sales)
    if sales_end > catalog_snapshot_at.astimezone(zone).date():
        raise PrivateResearchIntakeError(
            "SOURCE_RANGE_MISMATCH", "sales range ends after the catalog snapshot"
        )

    catalog_by_id = {row["shopify_variant_id"]: row for row in catalog.rows}
    for row in sales.rows:
        if row["shopify_variant_id"] not in catalog_by_id:
            raise PrivateResearchIntakeError(
                "SALES_VARIANT_NOT_IN_CATALOG",
                "sales may join the current catalog only by exact Shopify Variant ID",
            )

    hypotheses_by_variant, a1_variant_ids = _a1_hypotheses(package)
    current_ids = set(catalog_by_id)
    joined_a1 = current_ids & a1_variant_ids

    sales_by_variant_date: dict[tuple[str, str], Mapping[str, Any]] = {
        (row["shopify_variant_id"], row["business_date"]): row for row in sales.rows
    }
    lookback_dates = sales_dates[-_ABC_DAYS:] if len(sales_dates) >= _ABC_DAYS else []
    active_ids = {
        variant
        for variant, row in catalog_by_id.items()
        if row["product_status"] == "ACTIVE"
    }
    cogs_complete = bool(lookback_dates)
    if cogs_complete:
        for variant in active_ids:
            for business_date in lookback_dates:
                source_row = sales_by_variant_date.get(
                    (variant, business_date.isoformat())
                )
                if source_row is not None and source_row["historical_cogs"] is None:
                    cogs_complete = False
                    break
            if not cogs_complete:
                break

    variants: list[dict[str, Any]] = []
    vendor_variant_ids: defaultdict[str, set[str]] = defaultdict(set)
    vendor_hypotheses: defaultdict[str, int] = defaultdict(int)
    for variant in sorted(current_ids, key=lambda item: (len(item), item)):
        catalog_row = catalog_by_id[variant]
        unit_series: list[str] = []
        revenue_series: list[str] = []
        cogs_series: list[str | None] = []
        lookback_revenue = Decimal("0")
        lookback_cogs = Decimal("0")
        variant_cogs_complete = bool(lookback_dates)
        lookback_set = set(lookback_dates)
        for business_date in sales_dates:
            source_row = sales_by_variant_date.get(
                (variant, business_date.isoformat())
            )
            if source_row is None:
                units = Decimal("0")
                revenue = Decimal("0")
                cogs: Decimal | None = Decimal("0")
            else:
                units = Decimal(source_row["net_units"])
                revenue = Decimal(source_row["net_revenue"])
                cogs = (
                    None
                    if source_row["historical_cogs"] is None
                    else Decimal(source_row["historical_cogs"])
                )
            unit_series.append(_decimal_output(units))
            revenue_series.append(_decimal_output(revenue))
            cogs_series.append(None if cogs is None else _decimal_output(cogs))
            if business_date in lookback_set:
                lookback_revenue += revenue
                if cogs is None:
                    variant_cogs_complete = False
                else:
                    lookback_cogs += cogs

        hypotheses = hypotheses_by_variant.get(variant, [])
        for hypothesis in hypotheses:
            vendor = str(hypothesis["vendor_name"])
            vendor_variant_ids[vendor].add(variant)
            vendor_hypotheses[vendor] += 1
        history_complete_for_abc = (
            cogs_complete and variant in active_ids and variant_cogs_complete
        )
        variants.append(
            {
                "shopify_variant_id": variant,
                "product_title": catalog_row["product_title"],
                "variant_title": catalog_row["variant_title"],
                "shopify_sku": catalog_row["shopify_sku"],
                "barcode": catalog_row["barcode"],
                "current_retail_price": catalog_row["current_retail_price"],
                "current_inventory_item_cost": catalog_row[
                    "current_inventory_item_cost"
                ],
                "historical_revenue": (
                    _decimal_output(lookback_revenue) if lookback_dates else None
                ),
                "historical_cogs": (
                    _decimal_output(lookback_cogs)
                    if history_complete_for_abc
                    else None
                ),
                "sales_history": {
                    "start_date": sales_dates[0].isoformat(),
                    "end_date": sales_dates[-1].isoformat(),
                    "day_count": len(sales_dates),
                    "coverage_complete": True,
                    "observation_basis": (
                        "ATTESTED_COMPLETE_DAYS; ABSENT_VARIANT_DATE_SOURCE_ROW_IS_OBSERVED_ZERO"
                    ),
                    "net_units_series": unit_series,
                    "net_revenue_series": revenue_series,
                    "historical_cogs_series": cogs_series,
                },
                "available": None,
                "incoming": None,
                "units_per_case": None,
                "qualifying_units_per_case": None,
                "break_unit": None,
                "allocated_excluded": None,
                "combo_excluded": None,
                "one_bottle_policy": None,
                "gross_profit_dollars": (
                    _decimal_output(lookback_revenue - lookback_cogs)
                    if history_complete_for_abc
                    else None
                ),
                "forecast_evidence": {
                    "coverage_complete": False,
                    "horizon_days": None,
                    "observations": [],
                    "reason": "INVENTORY_STATE_HISTORY_NOT_SUPPLIED",
                },
                "supplier_hypotheses": hypotheses,
            }
        )

    abc_scope = None
    lookback_start = lookback_dates[0].isoformat() if lookback_dates else None
    lookback_end = lookback_dates[-1].isoformat() if lookback_dates else None
    if cogs_complete:
        abc_scope = hashlib.sha256(
            canonical_json_bytes(
                {
                    "eligible_shopify_variant_ids": sorted(
                        active_ids, key=lambda item: (len(item), item)
                    ),
                    "lookback_start": lookback_start,
                    "lookback_end": lookback_end,
                    "classification_period_days": _ABC_DAYS,
                }
            )
        ).hexdigest()

    vendors = [
        {
            "vendor_name": vendor,
            "identity_role": "DESCRIPTIVE_EVIDENCE_ONLY",
            "variant_count": len(vendor_variant_ids[vendor]),
            "supplier_hypothesis_count": vendor_hypotheses[vendor],
            "authority": "UNAPPROVED_REVIEW_LABEL_ONLY",
        }
        for vendor in sorted(vendor_variant_ids)
    ]

    coverage = {
        "current_catalog_population": len(current_ids),
        "a1_original_review_population": package.cohorts["original_cohort_count"],
        "a1_historical_current_census_population": package.cohorts[
            "current_census_count"
        ],
        "a1_current_original_returned": package.cohorts[
            "current_original_returned"
        ],
        "a1_current_additions": package.cohorts["current_additions"],
        "a1_variants_joined_to_current_catalog": len(joined_a1),
        "a1_variants_not_in_current_catalog": len(a1_variant_ids - current_ids),
        "current_catalog_variants_without_a1_review": len(
            current_ids - a1_variant_ids
        ),
        "catalog_pagination_complete": True,
        "sales_pagination_complete": True,
        "sales_start_date": sales_dates[0].isoformat(),
        "sales_end_date": sales_dates[-1].isoformat(),
        "sales_complete_day_count": len(sales_dates),
        "sales_source_row_count": len(sales.rows),
        "sales_distinct_variant_count": sales.manifest["population"][
            "distinct_variant_count"
        ],
        "sales_variants_joined_to_current_catalog": len(
            {row["shopify_variant_id"] for row in sales.rows}
        ),
        "sales_variants_not_in_current_catalog": 0,
        "abc_cohort": {
            "coverage_complete": cogs_complete,
            "scope_id": abc_scope,
            "lookback_start": lookback_start,
            "lookback_end": lookback_end,
            "classification_period_days": _ABC_DAYS,
            "eligible_variant_count": len(active_ids),
            "excluded_variant_count": len(current_ids - active_ids),
            "basis": "HISTORICAL_REVENUE_AND_HISTORICAL_COGS_ONLY",
        },
    }

    limitations = [
        "REVIEW_ONLY_UNAPPROVED",
        "NO_MAPPING_PRICE_SELECTION_INVENTORY_FORECAST_PROCUREMENT_SHOPIFY_OR_PO_AUTHORITY",
        "SHOPIFY_VARIANT_ID_IS_THE_ONLY_CATALOG_SALES_AND_A1_JOIN_KEY",
        "SUPPLIER_SKU_VENDOR_NAME_TITLE_AND_SIMILARITY_ARE_EVIDENCE_NOT_IDENTITY",
        "CURRENT_INVENTORY_ITEM_COST_IS_CURRENT_REFERENCE_ONLY_NOT_HISTORICAL_COGS",
        "A1_ORIGINAL_2000_REVIEW_COHORT_IS_SEPARATE_FROM_CURRENT_CATALOG_POPULATION",
        "COMPLETE_DAY_ABSENCE_IS_OBSERVED_ZERO; INCOMPLETE_DAYS_ARE_REJECTED",
        "NO_AVAILABILITY_INCOMING_OR_STOCKOUT_HISTORY_WAS_SUPPLIED",
        "A1_SEALED_PACKAGE_REMAINS_PRIVATE_PATH_BOUND_AND_IS_REHASHED_ON_READBACK",
    ]
    limitations.extend(
        f"A1_UNAVAILABLE_EVIDENCE:{item}" for item in package.unavailable_evidence
    )
    if not cogs_complete:
        limitations.append("ABC_COHORT_INCOMPLETE_WITHOUT_EXACT_84_DAY_HISTORICAL_COGS")

    payload: dict[str, Any] = {
        "contract": PRIVATE_RESEARCH_INTAKE_CONTRACT,
        "data_mode": PRIVATE_REAL_SOURCE_REVIEW,
        "authority": dict(INTAKE_AUTHORITY),
        "intake_id": None,
        "sources": {
            "catalog": _source_descriptor(
                catalog,
                manifest_ref=catalog_manifest_ref,
                data_ref=catalog_data_ref,
                schema=CATALOG_ROW_FIELDS,
            ),
            "daily_sales": {
                **_source_descriptor(
                    sales,
                    manifest_ref=sales_manifest_ref,
                    data_ref=sales_data_ref,
                    schema=DAILY_SALES_ROW_FIELDS,
                ),
                "date_range": _json_ready(sales.manifest["date_range"]),
            },
            "a1_review_package": _a1_descriptor(
                package,
                private_path=a1_private_path,
                external_evidence_path=a1_external_evidence_path,
            ),
        },
        "coverage": coverage,
        "variants": variants,
        "vendors": vendors,
        "limitations": limitations,
        "zero_authority": dict(ZERO_AUTHORITY),
    }
    payload["intake_id"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return payload


def intake_manifest_key(intake_id: str) -> str:
    _sha256(intake_id, context="intake ID")
    return f"{_INTAKE_PREFIX}/{intake_id}.json"


def _validate_blob_reference(value: Any, *, context: str) -> dict[str, Any]:
    reference = _exact_fields(value, _BLOB_REF_FIELDS, context=context)
    digest = _sha256(reference.get("sha256"), context=f"{context} SHA-256")
    if reference.get("key") != _blob_key(digest):
        raise PrivateResearchIntakeError(
            "CONTENT_ADDRESS_MISMATCH", f"{context} key differs from its SHA-256"
        )
    _integer(reference.get("bytes"), context=f"{context} bytes", minimum=0)
    _text(reference.get("media_type"), context=f"{context} media type")
    return reference


def _read_blob(root: Path, reference: Mapping[str, Any], *, context: str) -> bytes:
    key, path = _private_entry(root, reference["key"], context=context)
    if key != reference["key"]:
        raise PrivateResearchIntakeError(
            "CONTENT_ADDRESS_MISMATCH", f"{context} path normalization differs"
        )
    data = _read_file(path, maximum=_MAX_BLOB_BYTES, context=context)
    if (
        len(data) != reference["bytes"]
        or hashlib.sha256(data).hexdigest() != reference["sha256"]
    ):
        raise PrivateResearchIntakeError(
            "CONTENT_HASH_MISMATCH", f"{context} bytes differ from the intake"
        )
    return data


def _read_source_blobs(
    root: Path, source: Any, *, kind: str
) -> tuple[_Snapshot, dict[str, Any], dict[str, Any]]:
    if not isinstance(source, Mapping):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", f"{kind} source descriptor must be an object"
        )
    manifest_ref = _validate_blob_reference(
        source.get("manifest_blob"), context=f"{kind} manifest blob"
    )
    data_value = source.get("data_blob")
    if not isinstance(data_value, Mapping) or "schema" not in data_value:
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", f"{kind} data blob descriptor is incomplete"
        )
    data_ref = _validate_blob_reference(
        {key: data_value.get(key) for key in _BLOB_REF_FIELDS},
        context=f"{kind} data blob",
    )
    expected_schema = CATALOG_ROW_FIELDS if kind == "catalog" else DAILY_SALES_ROW_FIELDS
    if data_value.get("schema") != list(expected_schema):
        raise PrivateResearchIntakeError(
            "BLOB_CONTRACT_MISMATCH", f"{kind} stored schema differs"
        )
    manifest_bytes = _read_blob(
        root, manifest_ref, context=f"{kind} immutable manifest blob"
    )
    data_bytes = _read_blob(root, data_ref, context=f"{kind} immutable data blob")
    snapshot = (
        _catalog_snapshot(manifest_bytes, data_bytes)
        if kind == "catalog"
        else _sales_snapshot(manifest_bytes, data_bytes)
    )
    return snapshot, manifest_ref, data_ref


def _read_exact_a1_from_descriptor(
    root: Path, value: Any
) -> tuple[ReviewPackage, str, str | None]:
    if not isinstance(value, Mapping):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "A1 source descriptor must be an object"
        )
    private_value = value.get("private_path")
    private_key, private_path = _private_entry(
        root, private_value, context="A1 package", directory=True
    )
    external_value = value.get("external_evidence_path")
    external_key: str | None = None
    external_path: Path | None = None
    if external_value is not None:
        external_key, external_path = _private_entry(
            root,
            external_value,
            context="A1 external evidence root",
            directory=True,
        )
    package = read_review_package(
        private_path,
        external_evidence_root=external_path,
    )
    _validate_a1_package(package)
    return package, private_key, external_key


def build_private_research_intake(
    private_root: str | Path,
    *,
    catalog_manifest_path: str | Path,
    daily_sales_manifest_path: str | Path,
    a1_package_path: str | Path,
    a1_external_evidence_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate, immutably publish, and read back one private research intake."""

    root = validate_private_root(private_root)
    _, catalog = _load_snapshot_from_path(
        root, catalog_manifest_path, kind="catalog"
    )
    _, sales = _load_snapshot_from_path(
        root, daily_sales_manifest_path, kind="daily-sales"
    )
    a1_key, a1_path = _private_entry(
        root, a1_package_path, context="A1 package", directory=True
    )
    external_key: str | None = None
    external_path: Path | None = None
    if a1_external_evidence_root is not None:
        external_key, external_path = _private_entry(
            root,
            a1_external_evidence_root,
            context="A1 external evidence root",
            directory=True,
        )
    package = read_review_package(a1_path, external_evidence_root=external_path)
    _validate_a1_package(package)

    catalog_manifest_ref = _blob_ref(
        catalog.manifest_bytes, media_type="application/json"
    )
    catalog_data_ref = _blob_ref(
        catalog.data_bytes, media_type="application/x-ndjson"
    )
    sales_manifest_ref = _blob_ref(
        sales.manifest_bytes, media_type="application/json"
    )
    sales_data_ref = _blob_ref(
        sales.data_bytes, media_type="application/x-ndjson"
    )
    storage = LocalFilesystemStorage(root)
    # Immutable source blobs are published first.  A failed validation or blob
    # conflict can leave only harmless content-addressed orphans, never an
    # intake that appears complete.
    for reference, data in (
        (catalog_manifest_ref, catalog.manifest_bytes),
        (catalog_data_ref, catalog.data_bytes),
        (sales_manifest_ref, sales.manifest_bytes),
        (sales_data_ref, sales.data_bytes),
    ):
        _publish_once(storage, reference, data)

    payload = _assemble_intake(
        catalog,
        sales,
        package,
        catalog_manifest_ref=catalog_manifest_ref,
        catalog_data_ref=catalog_data_ref,
        sales_manifest_ref=sales_manifest_ref,
        sales_data_ref=sales_data_ref,
        a1_private_path=a1_key,
        a1_external_evidence_path=external_key,
    )
    manifest_bytes = canonical_json_bytes(payload)
    manifest_reference = {
        "key": intake_manifest_key(payload["intake_id"]),
        "bytes": len(manifest_bytes),
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "media_type": "application/json",
    }
    # The intake manifest is always the final publication.
    _publish_once(storage, manifest_reference, manifest_bytes)
    return read_private_research_intake(root, payload["intake_id"])


def read_private_research_intake(
    private_root: str | Path, intake_id: str
) -> dict[str, Any]:
    """Rehash every immutable blob and rederive every join before returning it."""

    root = validate_private_root(private_root)
    key = intake_manifest_key(intake_id)
    _, path = _private_entry(root, key, context="private research intake manifest")
    raw = _read_file(
        path, maximum=_MAX_BLOB_BYTES, context="private research intake manifest"
    )
    manifest = _parse_canonical_json(raw, path=key)
    _exact_fields(manifest, _TOP_LEVEL_FIELDS, context="private research intake")
    if (
        manifest.get("contract") != PRIVATE_RESEARCH_INTAKE_CONTRACT
        or manifest.get("data_mode") != PRIVATE_REAL_SOURCE_REVIEW
        or manifest.get("intake_id") != intake_id
    ):
        raise PrivateResearchIntakeError(
            "INTAKE_CONTRACT_MISMATCH", "intake contract, mode, authority, or ID differs"
        )
    _exact_typed_mapping(
        manifest.get("authority"), INTAKE_AUTHORITY, context="intake authority"
    )
    _exact_typed_mapping(
        manifest.get("zero_authority"),
        ZERO_AUTHORITY,
        context="zero-authority counts",
    )
    identity_basis = dict(manifest)
    identity_basis["intake_id"] = None
    if hashlib.sha256(canonical_json_bytes(identity_basis)).hexdigest() != intake_id:
        raise PrivateResearchIntakeError(
            "INTAKE_ID_MISMATCH", "intake ID does not address its canonical content"
        )
    sources = manifest.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != {
        "catalog",
        "daily_sales",
        "a1_review_package",
    }:
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "intake source set differs"
        )
    catalog, catalog_manifest_ref, catalog_data_ref = _read_source_blobs(
        root, sources["catalog"], kind="catalog"
    )
    sales, sales_manifest_ref, sales_data_ref = _read_source_blobs(
        root, sources["daily_sales"], kind="daily-sales"
    )
    package, a1_key, external_key = _read_exact_a1_from_descriptor(
        root, sources["a1_review_package"]
    )
    expected = _assemble_intake(
        catalog,
        sales,
        package,
        catalog_manifest_ref=catalog_manifest_ref,
        catalog_data_ref=catalog_data_ref,
        sales_manifest_ref=sales_manifest_ref,
        sales_data_ref=sales_data_ref,
        a1_private_path=a1_key,
        a1_external_evidence_path=external_key,
    )
    if canonical_json_bytes(expected) != raw:
        raise PrivateResearchIntakeError(
            "INTAKE_REDERIVATION_MISMATCH",
            "stored intake differs from freshly revalidated source evidence",
        )
    return manifest
