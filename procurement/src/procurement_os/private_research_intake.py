"""Fail-closed, offline intake for private real-source research evidence.

The intake is deliberately outside every operational authority boundary.  It
accepts either two code-owned normalized snapshot contracts or the frozen
native Shopify catalog/sales capture contracts plus the one exact sealed A1
review package. It preserves raw and normalized bytes in private
content-addressed storage and publishes one immutable canonical manifest only
after all validation succeeds.

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
SHOPIFY_CATALOG_CAPTURE_CONTRACT = "BUFFALO_PRIVATE_SHOPIFY_CATALOG_SNAPSHOT_V1"
SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT = (
    "BUFFALO_PRIVATE_SHOPIFY_DAILY_VARIANT_SALES_V1"
)
SHOPIFY_CAPTURE_AUTHORITY = "PRIVATE_REAL_SOURCE_REVIEW_ONLY"
SHOPIFY_CAPTURE_APPROVAL_STATE = "PROPOSED_UNAPPROVED_REVIEW_ONLY"
SHOPIFY_CAPTURE_ADAPTER = "BUFFALO_PRIVATE_SHOPIFY_CAPTURE_ADAPTER_V1"
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

_CAPTURE_CATALOG_MANIFEST_FIELDS = frozenset(
    {
        "contract",
        "authority",
        "approval_state",
        "captured_at_utc",
        "capture_timing_note",
        "source",
        "population",
        "parts",
        "limitations",
    }
)
_CAPTURE_CATALOG_SOURCE_FIELDS = frozenset(
    {
        "connector",
        "shop",
        "query_path",
        "query_bytes",
        "query_sha256",
        "page_size",
        "page_count",
        "pagination_complete",
        "nested_variant_pagination_complete",
        "nested_inventory_location_pagination_complete",
        "omitted_current_day_demand",
    }
)
_CAPTURE_CATALOG_SHOP_FIELDS = frozenset(
    {"name", "domain", "currency_code", "timezone", "country"}
)
_CAPTURE_CATALOG_POPULATION_FIELDS = frozenset({"products", "variants"})
_CAPTURE_SALES_MANIFEST_FIELDS = frozenset(
    {
        "contract",
        "authority",
        "approval_state",
        "captured_at_utc",
        "source",
        "columns",
        "day_queries",
        "totals",
        "parts",
        "limitations",
    }
)
_CAPTURE_SALES_SOURCE_FIELDS = frozenset(
    {
        "connector",
        "shop",
        "query_template_path",
        "query_template_bytes",
        "query_template_sha256",
        "first_complete_business_date",
        "last_complete_business_date",
        "business_days",
        "local_capture_day_omitted_as_partial",
        "one_query_per_business_date",
        "connector_row_ceiling",
        "every_query_below_row_ceiling",
        "population",
        "absent_variant_day_semantics",
    }
)
_CAPTURE_SALES_SHOP_FIELDS = frozenset({"domain", "currency_code", "timezone"})
_CAPTURE_PART_FIELDS = frozenset({"path", "bytes", "sha256", "rows"})
_CAPTURE_SALES_PART_FIELDS = _CAPTURE_PART_FIELDS | {"start_date", "end_date"}
_CAPTURE_DAY_QUERY_FIELDS = frozenset({"business_date", "row_count"})
_CAPTURE_TOTAL_FIELDS = frozenset({"rows"})
_CAPTURE_CATALOG_ROW_FIELDS = frozenset(
    {
        "id",
        "title",
        "handle",
        "vendor",
        "productType",
        "status",
        "tags",
        "updatedAt",
        "variants",
    }
)
_CAPTURE_VARIANTS_FIELDS = frozenset({"pageInfo", "nodes"})
_CAPTURE_PAGE_INFO_FIELDS = frozenset({"hasNextPage", "endCursor"})
_CAPTURE_VARIANT_FIELDS = frozenset(
    {
        "id",
        "title",
        "sku",
        "barcode",
        "price",
        "compareAtPrice",
        "inventoryQuantity",
        "updatedAt",
        "selectedOptions",
        "inventoryItem",
    }
)
_CAPTURE_OPTION_FIELDS = frozenset({"name", "value"})
_CAPTURE_INVENTORY_ITEM_FIELDS = frozenset(
    {"id", "tracked", "updatedAt", "unitCost", "inventoryLevels"}
)
_CAPTURE_UNIT_COST_FIELDS = frozenset({"amount", "currencyCode"})
_CAPTURE_INVENTORY_LEVELS_FIELDS = frozenset({"pageInfo", "nodes"})
_CAPTURE_INVENTORY_LEVEL_FIELDS = frozenset(
    {"id", "updatedAt", "location", "quantities"}
)
_CAPTURE_LOCATION_FIELDS = frozenset({"id", "name", "isActive"})
_CAPTURE_QUANTITY_FIELDS = frozenset({"name", "quantity", "updatedAt"})
_CAPTURE_SALES_ROW_FIELDS = frozenset(
    {
        "day",
        "product_id",
        "product_variant_id",
        "product_title",
        "product_variant_title",
        "net_items_sold",
        "gross_sales",
        "returns",
        "net_sales",
        "cost_of_goods_sold",
        "gross_profit",
    }
)
_CAPTURE_SALES_COLUMNS = (
    ("day", "DAY_TIMESTAMP"),
    ("product_id", "IDENTITY"),
    ("product_variant_id", "IDENTITY"),
    ("product_title", "STRING"),
    ("product_variant_title", "STRING"),
    ("net_items_sold", "INTEGER"),
    ("gross_sales", "MONEY"),
    ("returns", "MONEY"),
    ("net_sales", "MONEY"),
    ("cost_of_goods_sold", "MONEY"),
    ("gross_profit", "MONEY"),
)
_CAPTURE_QUANTITY_NAMES = frozenset({"available", "on_hand", "committed", "incoming"})
_PRODUCT_GID = re.compile(r"^gid://shopify/Product/([1-9][0-9]*)$")
_VARIANT_GID = re.compile(r"^gid://shopify/ProductVariant/([1-9][0-9]*)$")
_INVENTORY_ITEM_GID = re.compile(r"^gid://shopify/InventoryItem/[1-9][0-9]*$")
_LOCATION_GID = re.compile(r"^gid://shopify/Location/[1-9][0-9]*$")
_CAPTURE_DESCRIPTOR_FIELDS = frozenset(
    {"adapter", "contract", "manifest_blob", "files", "unjoined_sales_blob"}
)
_CAPTURE_FILE_DESCRIPTOR_FIELDS = frozenset({"path", "blob"})
_UNJOINED_SALES_ROW_FIELDS = (
    "business_date",
    "source_product_id",
    "source_shopify_variant_id",
    "historical_product_title",
    "historical_variant_title",
    "net_units",
    "gross_sales",
    "returns",
    "net_revenue",
    "historical_cogs",
    "gross_profit",
)


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


@dataclass(frozen=True)
class _CaptureFile:
    path: str
    data: bytes
    media_type: str


@dataclass(frozen=True)
class _NativeCapture:
    contract: str
    manifest_bytes: bytes
    files: tuple[_CaptureFile, ...]
    facts: Mapping[str, Any]
    unjoined_sales_bytes: bytes | None = None


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


def _parse_json_document(raw: bytes, *, path: str) -> dict[str, Any]:
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
    return value


def _parse_canonical_json(raw: bytes, *, path: str) -> dict[str, Any]:
    value = _parse_json_document(raw, path=path)
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


def _gid_decimal(value: Any, pattern: re.Pattern[str], *, context: str) -> str:
    if not isinstance(value, str):
        raise PrivateResearchIntakeError(
            "INVALID_SHOPIFY_GID", f"{context} must be a Shopify GID"
        )
    match = pattern.fullmatch(value)
    if match is None:
        raise PrivateResearchIntakeError(
            "INVALID_SHOPIFY_GID", f"{context} has the wrong Shopify GID type"
        )
    return match.group(1)


def _capture_jsonl(raw: bytes, *, path: str) -> tuple[dict[str, Any], ...]:
    lines = raw.splitlines(keepends=True)
    if not lines or len(lines) > _MAX_ROWS:
        raise PrivateResearchIntakeError(
            "INVALID_CAPTURE_JSONL", "capture JSONL must contain bounded rows", path=path
        )
    rows: list[dict[str, Any]] = []
    for row_number, line in enumerate(lines, start=1):
        if (
            len(line) > _MAX_JSONL_LINE_BYTES
            or not line.endswith(b"\n")
            or line.endswith(b"\r\n")
            or line == b"\n"
        ):
            raise PrivateResearchIntakeError(
                "INVALID_CAPTURE_JSONL",
                f"capture row {row_number} must be nonblank LF-terminated JSON",
                path=path,
            )
        rows.append(_parse_json_document(line, path=f"{path}:{row_number}"))
    return tuple(rows)


def _normalized_pagination(lines: Sequence[bytes]) -> dict[str, Any]:
    page_size = 1_000
    page_count = max(1, math.ceil(len(lines) / page_size))
    pages: list[dict[str, Any]] = []
    for index in range(page_count):
        first = index * page_size
        selected = lines[first : first + page_size]
        pages.append(
            {
                "page_index": index,
                "first_row_index": first,
                "row_count": len(selected),
                "sha256": hashlib.sha256(b"".join(selected)).hexdigest(),
                "terminal": index == page_count - 1,
            }
        )
    return {
        "page_size": page_size,
        "expected_pages": page_count,
        "completed_pages": page_count,
        "pages": pages,
        "terminal_page_seen": True,
        "truncated": False,
    }


def _canonical_jsonl(rows: Sequence[Mapping[str, Any]]) -> tuple[bytes, list[bytes]]:
    lines = [canonical_json_bytes(dict(row)) for row in rows]
    return b"".join(lines), lines


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


def _capture_timezone(value: Any, *, context: str) -> tuple[str, ZoneInfo]:
    """Normalize the frozen Shopify shop abbreviation without guessing offsets."""

    name = _text(value, context=context)
    if name == "EDT":
        normalized = "America/New_York"
    else:
        normalized = name
    return normalized, _timezone(normalized, context=context)


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
    if not isinstance(value, (str, Path)):
        raise PrivateResearchIntakeError(
            "UNSAFE_PATH", f"{context} must be a relative path string"
        )
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


def _capture_limitations(value: Any, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in value
    ):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", f"{context} must be an array of trimmed text"
        )
    return tuple(value)


def _capture_child(
    root: Path,
    manifest_path: Path,
    value: Any,
    *,
    context: str,
    maximum: int,
) -> tuple[str, bytes]:
    key = _safe_key(value, context=context)
    _, path = _private_entry(root, manifest_path.parent / key, context=context)
    return key, _read_file(path, maximum=maximum, context=context)


def _capture_file(
    root: Path,
    manifest_path: Path,
    value: Any,
    *,
    expected_bytes: Any,
    expected_sha256: Any,
    context: str,
    media_type: str,
    maximum: int,
    provided_files: Mapping[str, bytes] | None = None,
) -> _CaptureFile:
    key = _safe_key(value, context=context)
    if provided_files is None:
        key, data = _capture_child(
            root, manifest_path, key, context=context, maximum=maximum
        )
    else:
        if key not in provided_files:
            raise PrivateResearchIntakeError(
                "MISSING_CAPTURE_BLOB", f"{context} is absent from immutable capture blobs"
            )
        data = provided_files[key]
        if len(data) > maximum:
            raise PrivateResearchIntakeError(
                "SOURCE_SIZE_INVALID", f"{context} exceeds its byte limit"
            )
    byte_count = _integer(expected_bytes, context=f"{context} bytes", minimum=1)
    digest = _sha256(expected_sha256, context=f"{context} SHA-256")
    if len(data) != byte_count or hashlib.sha256(data).hexdigest() != digest:
        raise PrivateResearchIntakeError(
            "CONTENT_HASH_MISMATCH", f"{context} bytes differ from the manifest", path=key
        )
    return _CaptureFile(key, data, media_type)


def _capture_page_info(value: Any, *, context: str) -> None:
    page_info = _exact_fields(value, _CAPTURE_PAGE_INFO_FIELDS, context=context)
    if page_info.get("hasNextPage") is not False:
        raise PrivateResearchIntakeError(
            "PAGINATION_INCOMPLETE", f"{context} reports an unconsumed next page"
        )
    _text(page_info.get("endCursor"), context=f"{context} end cursor", nullable=True)


def _native_catalog_capture(
    root: Path,
    manifest_path: Path,
    manifest_bytes: bytes,
    manifest: Mapping[str, Any],
    *,
    provided_files: Mapping[str, bytes] | None = None,
) -> tuple[_Snapshot, _NativeCapture]:
    _exact_fields(
        manifest, _CAPTURE_CATALOG_MANIFEST_FIELDS, context="Shopify catalog capture"
    )
    if (
        manifest.get("contract") != SHOPIFY_CATALOG_CAPTURE_CONTRACT
        or manifest.get("authority") != SHOPIFY_CAPTURE_AUTHORITY
        or manifest.get("approval_state") != SHOPIFY_CAPTURE_APPROVAL_STATE
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", "Shopify catalog capture envelope differs"
        )
    captured = _utc_datetime(
        manifest.get("captured_at_utc"), context="catalog captured-at time"
    )
    _text(manifest.get("capture_timing_note"), context="catalog timing note")
    limitations = _capture_limitations(
        manifest.get("limitations"), context="catalog limitations"
    )

    source = _exact_fields(
        manifest.get("source"),
        _CAPTURE_CATALOG_SOURCE_FIELDS,
        context="Shopify catalog capture source",
    )
    if source.get("connector") != "ALREADY_CONNECTED_SHOPIFY_READ_ONLY":
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", "catalog connector identity differs"
        )
    shop = _exact_fields(
        source.get("shop"), _CAPTURE_CATALOG_SHOP_FIELDS, context="catalog shop"
    )
    for key in _CAPTURE_CATALOG_SHOP_FIELDS:
        _text(shop.get(key), context=f"catalog shop {key}")
    normalized_timezone, _ = _capture_timezone(
        shop.get("timezone"), context="catalog shop timezone"
    )
    query_file = _capture_file(
        root,
        manifest_path,
        source.get("query_path"),
        expected_bytes=source.get("query_bytes"),
        expected_sha256=source.get("query_sha256"),
        context="catalog query",
        media_type="application/graphql",
        maximum=_MAX_MANIFEST_BYTES,
        provided_files=provided_files,
    )
    page_size = _integer(source.get("page_size"), context="catalog page size", minimum=1)
    page_count = _integer(
        source.get("page_count"), context="catalog page count", minimum=1
    )
    if (
        source.get("pagination_complete") is not True
        or source.get("nested_variant_pagination_complete") is not True
        or source.get("nested_inventory_location_pagination_complete") is not True
        or source.get("omitted_current_day_demand") is not True
    ):
        raise PrivateResearchIntakeError(
            "PAGINATION_INCOMPLETE",
            "catalog top-level or nested pagination is not attested complete",
        )

    population = _exact_fields(
        manifest.get("population"),
        _CAPTURE_CATALOG_POPULATION_FIELDS,
        context="catalog capture population",
    )
    product_count = _integer(
        population.get("products"), context="catalog product count", minimum=1
    )
    variant_count = _integer(
        population.get("variants"), context="catalog variant count", minimum=1
    )
    if product_count > _MAX_ROWS or variant_count > _MAX_ROWS:
        raise PrivateResearchIntakeError(
            "TOO_MANY_ROWS", "catalog capture population exceeds the intake limit"
        )
    if page_count != math.ceil(product_count / page_size):
        raise PrivateResearchIntakeError(
            "PAGINATION_INCOMPLETE", "catalog page count does not cover products"
        )

    parts_value = manifest.get("parts")
    if not isinstance(parts_value, list) or not parts_value:
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "catalog parts must be a nonempty array"
        )
    capture_files: list[_CaptureFile] = [query_file]
    raw_products: list[dict[str, Any]] = []
    seen_paths = {query_file.path}
    declared_rows = 0
    for index, raw_part in enumerate(parts_value):
        part = _exact_fields(
            raw_part, _CAPTURE_PART_FIELDS, context=f"catalog part {index}"
        )
        capture_file = _capture_file(
            root,
            manifest_path,
            part.get("path"),
            expected_bytes=part.get("bytes"),
            expected_sha256=part.get("sha256"),
            context=f"catalog part {index}",
            media_type="application/x-ndjson",
            maximum=_MAX_BLOB_BYTES,
            provided_files=provided_files,
        )
        if capture_file.path in seen_paths:
            raise PrivateResearchIntakeError(
                "DUPLICATE_CAPTURE_PATH", "catalog capture path is duplicated"
            )
        seen_paths.add(capture_file.path)
        capture_files.append(capture_file)
        rows = _capture_jsonl(capture_file.data, path=capture_file.path)
        expected_rows = _integer(
            part.get("rows"), context=f"catalog part {index} rows", minimum=1
        )
        if len(rows) != expected_rows:
            raise PrivateResearchIntakeError(
                "ROW_COUNT_MISMATCH", "catalog part row count differs"
            )
        declared_rows += expected_rows
        if declared_rows > product_count:
            raise PrivateResearchIntakeError(
                "ROW_COUNT_MISMATCH", "catalog parts exceed the declared population"
            )
        raw_products.extend(rows)
    if declared_rows != product_count or len(raw_products) != product_count:
        raise PrivateResearchIntakeError(
            "ROW_COUNT_MISMATCH", "catalog parts do not preserve the product population"
        )

    product_ids: set[str] = set()
    variant_ids: set[str] = set()
    normalized_rows: list[dict[str, Any]] = []
    inventory_by_variant: dict[str, dict[str, Any]] = {}
    for product_index, product in enumerate(raw_products):
        _exact_fields(
            product,
            _CAPTURE_CATALOG_ROW_FIELDS,
            context=f"catalog product {product_index}",
        )
        product_id = _gid_decimal(
            product.get("id"), _PRODUCT_GID, context="catalog product ID"
        )
        if product_id in product_ids:
            raise PrivateResearchIntakeError(
                "DUPLICATE_PRODUCT_ID", "catalog product ID is duplicated"
            )
        product_ids.add(product_id)
        product_text: dict[str, str] = {}
        for field in ("title", "handle", "vendor", "productType"):
            raw_text = product.get(field)
            if not isinstance(raw_text, str):
                raise PrivateResearchIntakeError(
                    "INVALID_TEXT", f"catalog product {field} must be text"
                )
            product_text[field] = _text(
                raw_text.strip(), context=f"catalog product {field}"
            )
        status_value = _text(product.get("status"), context="catalog product status")
        if status_value not in {"ACTIVE", "ARCHIVED", "DRAFT"}:
            raise PrivateResearchIntakeError(
                "INVALID_CATALOG_STATUS", "catalog product status is unsupported"
            )
        tags = product.get("tags")
        if not isinstance(tags, list) or any(
            not isinstance(item, str) or item != item.strip() for item in tags
        ):
            raise PrivateResearchIntakeError(
                "SCHEMA_MISMATCH", "catalog product tags must be trimmed strings"
            )
        product_updated_at = _text(
            product.get("updatedAt"), context="catalog product updated-at"
        )
        _utc_datetime(product_updated_at, context="catalog product updated-at")
        variants = _exact_fields(
            product.get("variants"),
            _CAPTURE_VARIANTS_FIELDS,
            context="catalog nested variants",
        )
        _capture_page_info(variants.get("pageInfo"), context="catalog variant page")
        nodes = variants.get("nodes")
        if not isinstance(nodes, list):
            raise PrivateResearchIntakeError(
                "SCHEMA_MISMATCH", "catalog variant nodes must be an array"
            )
        for raw_variant in nodes:
            variant = _exact_fields(
                raw_variant, _CAPTURE_VARIANT_FIELDS, context="catalog variant"
            )
            variant_id = _gid_decimal(
                variant.get("id"), _VARIANT_GID, context="catalog Variant ID"
            )
            if variant_id in variant_ids:
                raise PrivateResearchIntakeError(
                    "DUPLICATE_VARIANT_ID", "catalog Shopify Variant ID is duplicated"
                )
            variant_ids.add(variant_id)
            raw_variant_title = variant.get("title")
            if not isinstance(raw_variant_title, str):
                raise PrivateResearchIntakeError(
                    "INVALID_TEXT", "catalog variant title must be text"
                )
            variant_title = _text(
                raw_variant_title.strip(), context="catalog variant title"
            )
            cleaned_optional: dict[str, str | None] = {}
            for field in ("sku", "barcode"):
                raw_optional = variant.get(field)
                if raw_optional is None:
                    cleaned_optional[field] = None
                elif isinstance(raw_optional, str):
                    cleaned_optional[field] = _text(
                        raw_optional.strip(), context=f"catalog variant {field}"
                    )
                else:
                    raise PrivateResearchIntakeError(
                        "INVALID_TEXT", f"catalog variant {field} must be text or null"
                    )
            price = _decimal_text(
                variant.get("price"), context="catalog current price", nonnegative=True
            )
            _decimal_text(
                variant.get("compareAtPrice"),
                context="catalog compare-at price",
                nullable=True,
                nonnegative=True,
            )
            inventory_quantity = variant.get("inventoryQuantity")
            if type(inventory_quantity) is not int:
                raise PrivateResearchIntakeError(
                    "INVALID_COUNT", "catalog inventoryQuantity must be an integer"
                )
            variant_updated_at = _text(
                variant.get("updatedAt"), context="catalog variant updated-at"
            )
            _utc_datetime(variant_updated_at, context="catalog variant updated-at")
            options = variant.get("selectedOptions")
            if not isinstance(options, list):
                raise PrivateResearchIntakeError(
                    "SCHEMA_MISMATCH", "selected options must be an array"
                )
            for option in options:
                option = _exact_fields(
                    option, _CAPTURE_OPTION_FIELDS, context="selected option"
                )
                _text(option.get("name"), context="selected option name")
                _text(option.get("value"), context="selected option value")

            inventory_item = _exact_fields(
                variant.get("inventoryItem"),
                _CAPTURE_INVENTORY_ITEM_FIELDS,
                context="catalog inventory item",
            )
            inventory_item_id = _text(
                inventory_item.get("id"), context="catalog inventory item ID"
            )
            if not _INVENTORY_ITEM_GID.fullmatch(inventory_item_id):
                raise PrivateResearchIntakeError(
                    "INVALID_SHOPIFY_GID", "inventory item has the wrong GID type"
                )
            if type(inventory_item.get("tracked")) is not bool:
                raise PrivateResearchIntakeError(
                    "SCHEMA_MISMATCH", "inventory item tracked must be boolean"
                )
            item_updated_at = _text(
                inventory_item.get("updatedAt"), context="inventory item updated-at"
            )
            _utc_datetime(item_updated_at, context="inventory item updated-at")
            unit_cost = inventory_item.get("unitCost")
            current_cost: str | None = None
            if unit_cost is not None:
                unit_cost = _exact_fields(
                    unit_cost, _CAPTURE_UNIT_COST_FIELDS, context="inventory unit cost"
                )
                current_cost_value = _decimal_text(
                    unit_cost.get("amount"),
                    context="current inventory item cost",
                    nonnegative=True,
                )
                current_cost = _decimal_output(current_cost_value)
                if unit_cost.get("currencyCode") != shop.get("currency_code"):
                    raise PrivateResearchIntakeError(
                        "CURRENCY_MISMATCH", "inventory unit cost currency differs"
                    )

            inventory_levels = _exact_fields(
                inventory_item.get("inventoryLevels"),
                _CAPTURE_INVENTORY_LEVELS_FIELDS,
                context="catalog inventory levels",
            )
            _capture_page_info(
                inventory_levels.get("pageInfo"), context="catalog inventory-level page"
            )
            level_nodes = inventory_levels.get("nodes")
            if not isinstance(level_nodes, list) or not level_nodes:
                raise PrivateResearchIntakeError(
                    "SCHEMA_MISMATCH", "inventory-level nodes must be nonempty"
                )
            location_ids: set[str] = set()
            locations: list[dict[str, Any]] = []
            aggregate = {name: Decimal("0") for name in _CAPTURE_QUANTITY_NAMES}
            for raw_level in level_nodes:
                level = _exact_fields(
                    raw_level,
                    _CAPTURE_INVENTORY_LEVEL_FIELDS,
                    context="catalog inventory level",
                )
                level_id = _text(level.get("id"), context="inventory level ID")
                if not level_id.startswith("gid://shopify/InventoryLevel/"):
                    raise PrivateResearchIntakeError(
                        "INVALID_SHOPIFY_GID", "inventory level has the wrong GID type"
                    )
                level_updated_at = _text(
                    level.get("updatedAt"), context="inventory level updated-at"
                )
                _utc_datetime(level_updated_at, context="inventory level updated-at")
                location = _exact_fields(
                    level.get("location"),
                    _CAPTURE_LOCATION_FIELDS,
                    context="inventory location",
                )
                location_id = _text(
                    location.get("id"), context="inventory location ID"
                )
                if not _LOCATION_GID.fullmatch(location_id):
                    raise PrivateResearchIntakeError(
                        "INVALID_SHOPIFY_GID", "location has the wrong GID type"
                    )
                if location_id in location_ids:
                    raise PrivateResearchIntakeError(
                        "DUPLICATE_LOCATION", "variant inventory location is duplicated"
                    )
                location_ids.add(location_id)
                location_name = _text(
                    location.get("name"), context="inventory location name"
                )
                if type(location.get("isActive")) is not bool:
                    raise PrivateResearchIntakeError(
                        "SCHEMA_MISMATCH", "inventory location isActive must be boolean"
                    )
                quantities_value = level.get("quantities")
                if not isinstance(quantities_value, list):
                    raise PrivateResearchIntakeError(
                        "SCHEMA_MISMATCH", "inventory quantities must be an array"
                    )
                quantities: dict[str, str] = {}
                quantity_timestamps: dict[str, str | None] = {}
                for raw_quantity in quantities_value:
                    quantity = _exact_fields(
                        raw_quantity,
                        _CAPTURE_QUANTITY_FIELDS,
                        context="inventory quantity",
                    )
                    name = _text(quantity.get("name"), context="inventory quantity name")
                    if name not in _CAPTURE_QUANTITY_NAMES or name in quantities:
                        raise PrivateResearchIntakeError(
                            "INVENTORY_QUANTITY_MISMATCH",
                            "inventory quantities must contain each expected state once",
                        )
                    number = quantity.get("quantity")
                    if type(number) is not int:
                        raise PrivateResearchIntakeError(
                            "INVALID_NUMBER", "inventory quantity must be an integer"
                        )
                    parsed_number = Decimal(number)
                    quantities[name] = _decimal_output(parsed_number)
                    aggregate[name] += parsed_number
                    updated = quantity.get("updatedAt")
                    if updated is not None:
                        updated = _text(updated, context="inventory quantity updated-at")
                        _utc_datetime(updated, context="inventory quantity updated-at")
                    quantity_timestamps[name] = updated
                if set(quantities) != _CAPTURE_QUANTITY_NAMES:
                    raise PrivateResearchIntakeError(
                        "INVENTORY_QUANTITY_MISMATCH",
                        "inventory quantities do not cover all requested states",
                    )
                locations.append(
                    {
                        "inventory_level_id": level_id,
                        "inventory_level_updated_at": level_updated_at,
                        "location_id": location_id,
                        "location_name": location_name,
                        "location_active": location["isActive"],
                        "available": quantities["available"],
                        "on_hand": quantities["on_hand"],
                        "committed": quantities["committed"],
                        "raw_incoming": quantities["incoming"],
                        "quantity_updated_at": quantity_timestamps,
                    }
                )
            if aggregate["available"] != Decimal(inventory_quantity):
                raise PrivateResearchIntakeError(
                    "INVENTORY_QUANTITY_MISMATCH",
                    "variant inventoryQuantity differs from complete location Available",
                )
            locations.sort(key=lambda item: item["location_id"])
            inventory_by_variant[variant_id] = {
                "available": _decimal_output(aggregate["available"]),
                "on_hand": _decimal_output(aggregate["on_hand"]),
                "committed": _decimal_output(aggregate["committed"]),
                "raw_incoming": _decimal_output(aggregate["incoming"]),
                "raw_incoming_trust": "UNTRUSTED_CAPTURE_ONLY",
                "trusted_incoming": None,
                "current_inventory_item_cost_currency": shop["currency_code"],
                "product_updated_at": product_updated_at,
                "variant_updated_at": variant_updated_at,
                "inventory_item_updated_at": item_updated_at,
                "locations": locations,
            }
            normalized_rows.append(
                {
                    "shopify_variant_id": variant_id,
                    "product_title": product_text["title"],
                    "variant_title": variant_title,
                    "shopify_sku": cleaned_optional["sku"],
                    "barcode": cleaned_optional["barcode"],
                    "current_retail_price": _decimal_output(price),
                    "current_inventory_item_cost": current_cost,
                    "product_status": status_value,
                    "shopify_vendor": product_text["vendor"],
                    "product_type": product_text["productType"],
                    "inventory_item_id": inventory_item_id,
                    "inventory_tracked": inventory_item["tracked"],
                }
            )
    if len(variant_ids) != variant_count or len(normalized_rows) != variant_count:
        raise PrivateResearchIntakeError(
            "ROW_COUNT_MISMATCH", "catalog capture variant population differs"
        )
    normalized_rows.sort(key=lambda item: (len(item["shopify_variant_id"]), item["shopify_variant_id"]))
    data_bytes, lines = _canonical_jsonl(normalized_rows)
    raw_digest = hashlib.sha256(manifest_bytes).hexdigest()
    normalized_manifest = {
        "contract": CATALOG_SNAPSHOT_CONTRACT,
        "data_mode": PRIVATE_REAL_SOURCE_REVIEW,
        "authority": dict(SOURCE_AUTHORITY),
        "snapshot_id": f"shopify-catalog-{raw_digest}",
        "source": {
            "system": "SHOPIFY_ADMIN_GRAPHQL",
            "store_identity": shop["domain"],
            "store_timezone": normalized_timezone,
        },
        "extraction": {
            "started_at_utc": manifest["captured_at_utc"],
            "completed_at_utc": manifest["captured_at_utc"],
            "snapshot_at_utc": manifest["captured_at_utc"],
        },
        "population": {
            "row_count": len(normalized_rows),
            "reported_row_count": variant_count,
        },
        "query": {
            "identity": f"{SHOPIFY_CATALOG_CAPTURE_CONTRACT}:{query_file.path}",
            "sha256": hashlib.sha256(query_file.data).hexdigest(),
        },
        "pagination": _normalized_pagination(lines),
        "blob": {
            "path": "derived/shopify-catalog-variants.jsonl",
            "bytes": len(data_bytes),
            "sha256": hashlib.sha256(data_bytes).hexdigest(),
            "format": JSONL_FORMAT,
            "schema": list(CATALOG_ROW_FIELDS),
        },
    }
    normalized_manifest_bytes = canonical_json_bytes(normalized_manifest)
    snapshot = _catalog_snapshot(normalized_manifest_bytes, data_bytes)
    capture = _NativeCapture(
        SHOPIFY_CATALOG_CAPTURE_CONTRACT,
        manifest_bytes,
        tuple(capture_files),
        {
            "inventory_by_variant": inventory_by_variant,
            "source_limitations": list(limitations),
            "raw_product_count": product_count,
            "raw_variant_count": variant_count,
            "shop_currency_code": shop["currency_code"],
            "raw_shop_timezone": shop["timezone"],
        },
    )
    return snapshot, capture


def _source_sales_identity(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or (
        value not in {"", "0"} and not _VARIANT_ID.fullmatch(value)
    ):
        raise PrivateResearchIntakeError(
            "INVALID_HISTORICAL_IDENTITY",
            f"{context} must be blank, zero, or a canonical positive decimal ID",
        )
    return value


def _native_sales_capture(
    root: Path,
    manifest_path: Path,
    manifest_bytes: bytes,
    manifest: Mapping[str, Any],
    *,
    current_variant_ids: set[str],
    provided_files: Mapping[str, bytes] | None = None,
) -> tuple[_Snapshot, _NativeCapture]:
    _exact_fields(
        manifest, _CAPTURE_SALES_MANIFEST_FIELDS, context="Shopify sales capture"
    )
    if (
        manifest.get("contract") != SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT
        or manifest.get("authority") != SHOPIFY_CAPTURE_AUTHORITY
        or manifest.get("approval_state") != SHOPIFY_CAPTURE_APPROVAL_STATE
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", "Shopify sales capture envelope differs"
        )
    captured = _utc_datetime(
        manifest.get("captured_at_utc"), context="sales captured-at time"
    )
    limitations = _capture_limitations(
        manifest.get("limitations"), context="sales limitations"
    )
    source = _exact_fields(
        manifest.get("source"),
        _CAPTURE_SALES_SOURCE_FIELDS,
        context="Shopify sales capture source",
    )
    if source.get("connector") != "ALREADY_CONNECTED_SHOPIFY_READ_ONLY_ANALYTICS":
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", "sales connector identity differs"
        )
    shop = _exact_fields(
        source.get("shop"), _CAPTURE_SALES_SHOP_FIELDS, context="sales shop"
    )
    for key in _CAPTURE_SALES_SHOP_FIELDS:
        _text(shop.get(key), context=f"sales shop {key}")
    normalized_timezone, zone = _capture_timezone(
        shop.get("timezone"), context="sales shop timezone"
    )
    query_file = _capture_file(
        root,
        manifest_path,
        source.get("query_template_path"),
        expected_bytes=source.get("query_template_bytes"),
        expected_sha256=source.get("query_template_sha256"),
        context="sales query template",
        media_type="text/plain",
        maximum=_MAX_MANIFEST_BYTES,
        provided_files=provided_files,
    )
    start = _iso_date(
        source.get("first_complete_business_date"), context="sales first complete date"
    )
    end = _iso_date(
        source.get("last_complete_business_date"), context="sales last complete date"
    )
    if end < start:
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE", "sales complete range is reversed"
        )
    day_count = (end - start).days + 1
    omitted_day = _iso_date(
        source.get("local_capture_day_omitted_as_partial"),
        context="sales omitted partial day",
    )
    ceiling = _integer(
        source.get("connector_row_ceiling"), context="sales row ceiling", minimum=1
    )
    if (
        _integer(source.get("business_days"), context="sales business days", minimum=1)
        != day_count
        or omitted_day != end + timedelta(days=1)
        or captured.astimezone(zone).date() != omitted_day
        or source.get("one_query_per_business_date") is not True
        or source.get("every_query_below_row_ceiling") is not True
        or source.get("population")
        != "ALL_VARIANT_GROUPS_RETURNED_BY_UNFILTERED_DAILY_SALES_QUERY"
        or source.get("absent_variant_day_semantics")
        != (
            "OBSERVED_ZERO_ONLY_FOR_DAYS_WITH_A_RECORDED_COMPLETE_QUERY; "
            "NEVER IMPUTE AN UNQUERIED DAY"
        )
    ):
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE",
            "sales daily query completeness contract differs",
        )

    columns = manifest.get("columns")
    if not isinstance(columns, list) or len(columns) != len(_CAPTURE_SALES_COLUMNS):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "sales columns differ from the frozen query schema"
        )
    for raw_column, expected in zip(columns, _CAPTURE_SALES_COLUMNS):
        column = _exact_fields(
            raw_column, frozenset({"name", "dataType"}), context="sales column"
        )
        if (column.get("name"), column.get("dataType")) != expected:
            raise PrivateResearchIntakeError(
                "SCHEMA_MISMATCH", "sales columns differ from the frozen query schema"
            )

    day_queries_value = manifest.get("day_queries")
    if not isinstance(day_queries_value, list) or len(day_queries_value) != day_count:
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE", "sales day-query count differs"
        )
    declared_by_date: dict[str, int] = {}
    for index, raw_query in enumerate(day_queries_value):
        query = _exact_fields(
            raw_query, _CAPTURE_DAY_QUERY_FIELDS, context="sales day query"
        )
        business_date = _iso_date(
            query.get("business_date"), context="sales query business date"
        )
        expected_date = start + timedelta(days=index)
        row_count = _integer(
            query.get("row_count"), context="sales daily row count", minimum=0
        )
        if business_date != expected_date or row_count >= ceiling:
            raise PrivateResearchIntakeError(
                "DATE_COVERAGE_INCOMPLETE",
                "sales day queries must be contiguous and below the row ceiling",
            )
        declared_by_date[business_date.isoformat()] = row_count

    totals = _exact_fields(
        manifest.get("totals"), _CAPTURE_TOTAL_FIELDS, context="sales totals"
    )
    total_rows = _integer(totals.get("rows"), context="sales total rows", minimum=1)
    if total_rows > _MAX_ROWS:
        raise PrivateResearchIntakeError(
            "TOO_MANY_ROWS", "sales capture population exceeds the intake limit"
        )
    if sum(declared_by_date.values()) != total_rows:
        raise PrivateResearchIntakeError(
            "ROW_COUNT_MISMATCH", "sales daily row counts differ from total rows"
        )

    parts_value = manifest.get("parts")
    if not isinstance(parts_value, list) or not parts_value:
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", "sales parts must be a nonempty array"
        )
    capture_files: list[_CaptureFile] = [query_file]
    seen_paths = {query_file.path}
    raw_rows: list[dict[str, Any]] = []
    declared_part_rows = 0
    next_start = start
    for index, raw_part in enumerate(parts_value):
        part = _exact_fields(
            raw_part, _CAPTURE_SALES_PART_FIELDS, context=f"sales part {index}"
        )
        part_start = _iso_date(part.get("start_date"), context="sales part start")
        part_end = _iso_date(part.get("end_date"), context="sales part end")
        if part_start != next_start or part_end < part_start or part_end > end:
            raise PrivateResearchIntakeError(
                "DATE_COVERAGE_INCOMPLETE", "sales part date ranges are not contiguous"
            )
        next_start = part_end + timedelta(days=1)
        capture_file = _capture_file(
            root,
            manifest_path,
            part.get("path"),
            expected_bytes=part.get("bytes"),
            expected_sha256=part.get("sha256"),
            context=f"sales part {index}",
            media_type="application/x-ndjson",
            maximum=_MAX_BLOB_BYTES,
            provided_files=provided_files,
        )
        if capture_file.path in seen_paths:
            raise PrivateResearchIntakeError(
                "DUPLICATE_CAPTURE_PATH", "sales capture path is duplicated"
            )
        seen_paths.add(capture_file.path)
        capture_files.append(capture_file)
        rows = _capture_jsonl(capture_file.data, path=capture_file.path)
        expected_rows = _integer(
            part.get("rows"), context=f"sales part {index} rows", minimum=1
        )
        if len(rows) != expected_rows:
            raise PrivateResearchIntakeError(
                "ROW_COUNT_MISMATCH", "sales part row count differs"
            )
        for row in rows:
            row_date = _iso_date(row.get("day"), context="sales row day")
            if row_date < part_start or row_date > part_end:
                raise PrivateResearchIntakeError(
                    "DATE_OUT_OF_RANGE", "sales row is outside its part range"
                )
        declared_part_rows += expected_rows
        if declared_part_rows > total_rows:
            raise PrivateResearchIntakeError(
                "ROW_COUNT_MISMATCH", "sales parts exceed the declared population"
            )
        raw_rows.extend(rows)
    if next_start != end + timedelta(days=1):
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE", "sales parts do not cover the complete range"
        )
    if declared_part_rows != total_rows or len(raw_rows) != total_rows:
        raise PrivateResearchIntakeError(
            "ROW_COUNT_MISMATCH", "sales parts do not preserve the row population"
        )

    actual_by_date: defaultdict[str, int] = defaultdict(int)
    seen_facts: set[tuple[str, str]] = set()
    raw_identity_ids: set[str] = set()
    joined_ids: set[str] = set()
    unjoined_ids: set[str] = set()
    normalized_rows: list[dict[str, Any]] = []
    unjoined_rows: list[dict[str, Any]] = []
    current_sales_details: dict[tuple[str, str], dict[str, str]] = {}
    for row_index, row in enumerate(raw_rows):
        _exact_fields(
            row, _CAPTURE_SALES_ROW_FIELDS, context=f"sales capture row {row_index}"
        )
        business_date = _iso_date(row.get("day"), context="sales row business date")
        if business_date < start or business_date > end:
            raise PrivateResearchIntakeError(
                "DATE_OUT_OF_RANGE", "sales row is outside the complete range"
            )
        day_text = business_date.isoformat()
        actual_by_date[day_text] += 1
        product_id = _source_sales_identity(
            row.get("product_id"), context="historical product ID"
        )
        source_variant_id = _source_sales_identity(
            row.get("product_variant_id"), context="historical Variant ID"
        )
        if (
            (source_variant_id == "" and product_id != "")
            or (source_variant_id == "0" and product_id != "0")
            or (
                _VARIANT_ID.fullmatch(source_variant_id) is not None
                and _VARIANT_ID.fullmatch(product_id) is None
            )
        ):
            raise PrivateResearchIntakeError(
                "INVALID_HISTORICAL_IDENTITY", "sales product and Variant IDs disagree"
            )
        for title_field in ("product_title", "product_variant_title"):
            if not isinstance(row.get(title_field), str):
                raise PrivateResearchIntakeError(
                    "SCHEMA_MISMATCH", f"sales {title_field} must be text"
                )
        net_units = _decimal_text(row.get("net_items_sold"), context="sales net units")
        if net_units != net_units.to_integral_value():
            raise PrivateResearchIntakeError(
                "INVALID_DECIMAL", "sales net units must be an integer string"
            )
        gross_sales = _decimal_text(row.get("gross_sales"), context="sales gross sales")
        returns = _decimal_text(row.get("returns"), context="sales returns")
        net_revenue = _decimal_text(row.get("net_sales"), context="sales net revenue")
        historical_cogs = _decimal_text(
            row.get("cost_of_goods_sold"), context="sales historical COGS"
        )
        gross_profit = _decimal_text(row.get("gross_profit"), context="sales gross profit")
        fact_key = (day_text, source_variant_id)
        if fact_key in seen_facts:
            raise PrivateResearchIntakeError(
                "DUPLICATE_SALES_FACT",
                "sales capture must be unique by date and source Variant identity",
            )
        seen_facts.add(fact_key)
        raw_identity_ids.add(source_variant_id)
        if source_variant_id in current_variant_ids:
            joined_ids.add(source_variant_id)
            current_sales_details[(source_variant_id, day_text)] = {
                "gross_sales": _decimal_output(gross_sales),
                "returns": _decimal_output(returns),
                "source_gross_profit": _decimal_output(gross_profit),
            }
            normalized_rows.append(
                {
                    "business_date": day_text,
                    "shopify_variant_id": source_variant_id,
                    "net_units": _decimal_output(net_units),
                    "net_revenue": _decimal_output(net_revenue),
                    "historical_cogs": _decimal_output(historical_cogs),
                }
            )
        else:
            unjoined_ids.add(source_variant_id)
            unjoined_rows.append(
                {
                    "business_date": day_text,
                    "source_product_id": product_id,
                    "source_shopify_variant_id": source_variant_id,
                    "historical_product_title": row["product_title"],
                    "historical_variant_title": row["product_variant_title"],
                    "net_units": _decimal_output(net_units),
                    "gross_sales": _decimal_output(gross_sales),
                    "returns": _decimal_output(returns),
                    "net_revenue": _decimal_output(net_revenue),
                    "historical_cogs": _decimal_output(historical_cogs),
                    "gross_profit": _decimal_output(gross_profit),
                }
            )
    if any(
        actual_by_date[business_date] != row_count
        for business_date, row_count in declared_by_date.items()
    ) or any(business_date not in declared_by_date for business_date in actual_by_date):
        raise PrivateResearchIntakeError(
            "DATE_COVERAGE_INCOMPLETE", "sales rows differ from daily query counts"
        )

    normalized_rows.sort(
        key=lambda item: (
            item["business_date"],
            len(item["shopify_variant_id"]),
            item["shopify_variant_id"],
        )
    )
    unjoined_rows.sort(
        key=lambda item: (
            item["business_date"],
            item["source_shopify_variant_id"],
            item["source_product_id"],
        )
    )
    data_bytes, lines = _canonical_jsonl(normalized_rows)
    unjoined_bytes, _ = _canonical_jsonl(unjoined_rows)
    raw_digest = hashlib.sha256(manifest_bytes).hexdigest()
    normalized_manifest = {
        "contract": DAILY_SALES_SNAPSHOT_CONTRACT,
        "data_mode": PRIVATE_REAL_SOURCE_REVIEW,
        "authority": dict(SOURCE_AUTHORITY),
        "snapshot_id": f"shopify-daily-sales-{raw_digest}",
        "source": {
            "system": "SHOPIFYQL_SALES",
            "store_identity": shop["domain"],
            "store_timezone": normalized_timezone,
        },
        "extraction": {
            "started_at_utc": manifest["captured_at_utc"],
            "completed_at_utc": manifest["captured_at_utc"],
            "snapshot_at_utc": manifest["captured_at_utc"],
        },
        "date_range": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "complete_through_date": end.isoformat(),
            "store_timezone": normalized_timezone,
            "complete_day_count": day_count,
            "missing_dates": [],
        },
        "population": {
            "row_count": len(normalized_rows),
            "distinct_variant_count": len(joined_ids),
        },
        "query": {
            "identity": f"{SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT}:{query_file.path}",
            "sha256": hashlib.sha256(query_file.data).hexdigest(),
        },
        "pagination": _normalized_pagination(lines),
        "blob": {
            "path": "derived/shopify-current-variant-sales.jsonl",
            "bytes": len(data_bytes),
            "sha256": hashlib.sha256(data_bytes).hexdigest(),
            "format": JSONL_FORMAT,
            "schema": list(DAILY_SALES_ROW_FIELDS),
        },
    }
    normalized_manifest_bytes = canonical_json_bytes(normalized_manifest)
    snapshot = _sales_snapshot(normalized_manifest_bytes, data_bytes)
    identity_sha = hashlib.sha256(
        canonical_json_bytes(sorted(unjoined_ids, key=lambda item: (len(item), item)))
    ).hexdigest()
    capture = _NativeCapture(
        SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT,
        manifest_bytes,
        tuple(capture_files),
        {
            "source_limitations": list(limitations),
            "shop_currency_code": shop["currency_code"],
            "raw_shop_timezone": shop["timezone"],
            "current_sales_details": current_sales_details,
            "raw_row_count": total_rows,
            "raw_distinct_source_variant_identity_count": len(raw_identity_ids),
            "joined_current_variant_count": len(joined_ids),
            "unjoined_source_variant_identity_count": len(unjoined_ids),
            "unjoined_source_row_count": len(unjoined_rows),
            "unjoined_source_variant_identities_sha256": identity_sha,
            "current_catalog_variants_observed_zero_all_days": len(
                current_variant_ids - joined_ids
            ),
            "abc_eligible_cohort_evidence": "NOT_CONFIGURED_IN_CAPTURE",
        },
        unjoined_sales_bytes=unjoined_bytes,
    )
    return snapshot, capture


def _load_snapshot_from_path(
    root: Path,
    manifest_value: str | Path,
    *,
    kind: str,
    current_variant_ids: set[str] | None = None,
) -> tuple[str, _Snapshot, _NativeCapture | None]:
    manifest_key, manifest_path = _private_entry(
        root, manifest_value, context=f"{kind} manifest"
    )
    manifest_bytes = _read_file(
        manifest_path, maximum=_MAX_MANIFEST_BYTES, context=f"{kind} manifest"
    )
    parsed = _parse_json_document(manifest_bytes, path=f"{kind} manifest")
    contract = parsed.get("contract")
    native_contract = (
        SHOPIFY_CATALOG_CAPTURE_CONTRACT
        if kind == "catalog"
        else SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT
    )
    normalized_contract = (
        CATALOG_SNAPSHOT_CONTRACT
        if kind == "catalog"
        else DAILY_SALES_SNAPSHOT_CONTRACT
    )
    if contract == native_contract:
        if kind == "catalog":
            snapshot, capture = _native_catalog_capture(
                root, manifest_path, manifest_bytes, parsed
            )
        else:
            if current_variant_ids is None:
                raise PrivateResearchIntakeError(
                    "ADAPTER_INPUT_MISSING",
                    "native daily sales require the validated current catalog identity set",
                )
            snapshot, capture = _native_sales_capture(
                root,
                manifest_path,
                manifest_bytes,
                parsed,
                current_variant_ids=current_variant_ids,
            )
        return manifest_key, snapshot, capture
    if contract != normalized_contract:
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", f"unsupported {kind} manifest contract"
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
    return manifest_key, snapshot, None


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


def _native_capture_descriptor(
    capture: _NativeCapture,
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], bytes]]]:
    manifest_ref = _blob_ref(capture.manifest_bytes, media_type="application/json")
    publications: list[tuple[dict[str, Any], bytes]] = [
        (manifest_ref, capture.manifest_bytes)
    ]
    files: list[dict[str, Any]] = []
    for item in capture.files:
        reference = _blob_ref(item.data, media_type=item.media_type)
        publications.append((reference, item.data))
        files.append({"path": item.path, "blob": reference})
    unjoined_ref: dict[str, Any] | None = None
    if capture.unjoined_sales_bytes is not None:
        raw_unjoined_ref = _blob_ref(
            capture.unjoined_sales_bytes, media_type="application/x-ndjson"
        )
        unjoined_ref = {
            **raw_unjoined_ref,
            "schema": list(_UNJOINED_SALES_ROW_FIELDS),
        }
        publications.append((raw_unjoined_ref, capture.unjoined_sales_bytes))
    return (
        {
            "adapter": SHOPIFY_CAPTURE_ADAPTER,
            "contract": capture.contract,
            "manifest_blob": manifest_ref,
            "files": files,
            "unjoined_sales_blob": unjoined_ref,
        },
        publications,
    )


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
    native_capture: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    descriptor = {
        "contract": snapshot.manifest["contract"],
        "snapshot_id": snapshot.manifest["snapshot_id"],
        "manifest_blob": dict(manifest_ref),
        "data_blob": {**dict(data_ref), "schema": list(schema)},
        "source": _json_ready(snapshot.manifest["source"]),
        "extraction": _json_ready(snapshot.manifest["extraction"]),
        "query": _json_ready(snapshot.manifest["query"]),
        "pagination": _json_ready(snapshot.manifest["pagination"]),
    }
    if native_capture is not None:
        descriptor["native_capture"] = _json_ready(native_capture)
    return descriptor


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
    catalog_capture: _NativeCapture | None = None,
    sales_capture: _NativeCapture | None = None,
    catalog_capture_descriptor: Mapping[str, Any] | None = None,
    sales_capture_descriptor: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    catalog_source = catalog.manifest["source"]
    sales_source = sales.manifest["source"]
    if (catalog_capture is None) != (sales_capture is None):
        raise PrivateResearchIntakeError(
            "MIXED_SOURCE_CONTRACTS",
            "catalog and daily sales must both be native captures or both normalized",
        )
    if (
        catalog_source["store_identity"] != sales_source["store_identity"]
        or catalog_source["store_timezone"] != sales_source["store_timezone"]
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_SCOPE_MISMATCH", "catalog and sales store identity/timezone differ"
        )
    if (
        catalog_capture is not None
        and sales_capture is not None
        and (
            catalog_capture.facts.get("shop_currency_code")
            != sales_capture.facts.get("shop_currency_code")
            or catalog_capture.facts.get("raw_shop_timezone")
            != sales_capture.facts.get("raw_shop_timezone")
        )
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_SCOPE_MISMATCH", "catalog and sales currency/timezone differ"
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
    native_sales_details = (
        sales_capture.facts.get("current_sales_details", {})
        if sales_capture is not None
        else {}
    )
    for variant in sorted(current_ids, key=lambda item: (len(item), item)):
        catalog_row = catalog_by_id[variant]
        unit_series: list[str] = []
        revenue_series: list[str] = []
        cogs_series: list[str | None] = []
        gross_sales_series: list[str] = []
        returns_series: list[str] = []
        source_gross_profit_series: list[str] = []
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
            if sales_capture is not None:
                detail = native_sales_details.get(
                    (variant, business_date.isoformat())
                )
                if source_row is None:
                    detail = {
                        "gross_sales": "0",
                        "returns": "0",
                        "source_gross_profit": "0",
                    }
                if not isinstance(detail, Mapping):
                    raise PrivateResearchIntakeError(
                        "ADAPTER_REDERIVATION_MISMATCH",
                        "native current sales detail is missing for a captured fact",
                    )
                gross_sales_series.append(str(detail["gross_sales"]))
                returns_series.append(str(detail["returns"]))
                source_gross_profit_series.append(str(detail["source_gross_profit"]))
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
        history_values_complete = bool(lookback_dates) and variant_cogs_complete
        inventory = (
            catalog_capture.facts.get("inventory_by_variant", {}).get(variant)
            if catalog_capture is not None
            else None
        )
        if catalog_capture is not None and not isinstance(inventory, Mapping):
            raise PrivateResearchIntakeError(
                "ADAPTER_REDERIVATION_MISMATCH",
                "native catalog inventory evidence is missing for a current Variant ID",
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
                "current_inventory_item_cost_currency": (
                    inventory.get("current_inventory_item_cost_currency")
                    if inventory
                    else None
                ),
                "historical_revenue": (
                    _decimal_output(lookback_revenue) if lookback_dates else None
                ),
                "historical_cogs": (
                    _decimal_output(lookback_cogs)
                    if history_values_complete
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
                    "gross_sales_series": (
                        gross_sales_series if sales_capture is not None else None
                    ),
                    "returns_series": (
                        returns_series if sales_capture is not None else None
                    ),
                    "source_gross_profit_series": (
                        source_gross_profit_series
                        if sales_capture is not None
                        else None
                    ),
                },
                "available": inventory.get("available") if inventory else None,
                "incoming": None,
                "on_hand": inventory.get("on_hand") if inventory else None,
                "committed": inventory.get("committed") if inventory else None,
                "raw_incoming": inventory.get("raw_incoming") if inventory else None,
                "raw_incoming_trust": (
                    inventory.get("raw_incoming_trust") if inventory else None
                ),
                "inventory_evidence": (
                    _json_ready(inventory) if inventory is not None else None
                ),
                "units_per_case": None,
                "qualifying_units_per_case": None,
                "break_unit": None,
                "allocated_excluded": None,
                "combo_excluded": None,
                "one_bottle_policy": None,
                "gross_profit_dollars": (
                    _decimal_output(lookback_revenue - lookback_cogs)
                    if history_values_complete
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
    abc_coverage_complete = cogs_complete and sales_capture is None
    if abc_coverage_complete:
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

    sales_facts = sales_capture.facts if sales_capture is not None else {}
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
        "sales_source_row_count": sales_facts.get("raw_row_count", len(sales.rows)),
        "sales_normalized_current_row_count": len(sales.rows),
        "sales_distinct_variant_count": sales_facts.get(
            "raw_distinct_source_variant_identity_count",
            sales.manifest["population"]["distinct_variant_count"],
        ),
        "sales_variants_joined_to_current_catalog": sales_facts.get(
            "joined_current_variant_count",
            len({row["shopify_variant_id"] for row in sales.rows}),
        ),
        "sales_variants_not_in_current_catalog": sales_facts.get(
            "unjoined_source_variant_identity_count", 0
        ),
        "sales_historical_unjoined_source_row_count": sales_facts.get(
            "unjoined_source_row_count", 0
        ),
        "sales_historical_unjoined_variant_identities_sha256": sales_facts.get(
            "unjoined_source_variant_identities_sha256"
        ),
        "sales_current_catalog_variants_observed_zero_all_days": sales_facts.get(
            "current_catalog_variants_observed_zero_all_days",
            len(current_ids - {row["shopify_variant_id"] for row in sales.rows}),
        ),
        "abc_cohort": {
            "coverage_complete": abc_coverage_complete,
            "scope_id": abc_scope,
            "lookback_start": lookback_start,
            "lookback_end": lookback_end,
            "classification_period_days": _ABC_DAYS,
            "eligible_variant_count": len(active_ids),
            "excluded_variant_count": len(current_ids - active_ids),
            "basis": (
                "HISTORICAL_REVENUE_AND_HISTORICAL_COGS_ONLY"
                if sales_capture is None
                else "NOT_CONFIGURED; CAPTURE_LACKS_EXACT_ELIGIBLE_MEMBERSHIP_AND_EXCLUSIONS"
            ),
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
    if sales_capture is not None:
        limitations.extend(
            [
                "RAW_SHOPIFY_INCOMING_IS_UNTRUSTED_CAPTURE_EVIDENCE; TRUSTED_INCOMING_REMAINS_UNKNOWN",
                "HISTORICAL_SALES_IDENTITIES_NOT_IN_CURRENT_CATALOG_ARE_PRESERVED_UNJOINED",
                "ACTUAL_CAPTURE_LACKS_EXACT_ABC_ELIGIBLE_COHORT_AND_EXCLUSION_EVIDENCE",
                "NATIVE_DESCRIPTIVE_TEXT_IS_TRIMMED_ONLY_IN_THE_NORMALIZED_VIEW; RAW_BYTES_ARE_PRESERVED",
            ]
        )
        limitations.extend(
            f"CATALOG_CAPTURE_LIMITATION:{item}"
            for item in catalog_capture.facts.get("source_limitations", [])
        )
        limitations.extend(
            f"SALES_CAPTURE_LIMITATION:{item}"
            for item in sales_capture.facts.get("source_limitations", [])
        )

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
                native_capture=catalog_capture_descriptor,
            ),
            "daily_sales": {
                **_source_descriptor(
                    sales,
                    manifest_ref=sales_manifest_ref,
                    data_ref=sales_data_ref,
                    schema=DAILY_SALES_ROW_FIELDS,
                    native_capture=sales_capture_descriptor,
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


def _read_native_capture(
    root: Path,
    value: Any,
    *,
    kind: str,
    current_variant_ids: set[str] | None = None,
) -> tuple[_NativeCapture, dict[str, Any]]:
    descriptor = _exact_fields(
        value, _CAPTURE_DESCRIPTOR_FIELDS, context=f"{kind} native capture"
    )
    expected_contract = (
        SHOPIFY_CATALOG_CAPTURE_CONTRACT
        if kind == "catalog"
        else SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT
    )
    if (
        descriptor.get("adapter") != SHOPIFY_CAPTURE_ADAPTER
        or descriptor.get("contract") != expected_contract
    ):
        raise PrivateResearchIntakeError(
            "SOURCE_CONTRACT_MISMATCH", f"{kind} native capture adapter differs"
        )
    manifest_ref = _validate_blob_reference(
        descriptor.get("manifest_blob"), context=f"{kind} native manifest blob"
    )
    manifest_bytes = _read_blob(
        root, manifest_ref, context=f"{kind} native immutable manifest"
    )
    manifest = _parse_json_document(
        manifest_bytes, path=f"{kind} native immutable manifest"
    )
    files_value = descriptor.get("files")
    if not isinstance(files_value, list) or not files_value:
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", f"{kind} native capture files must be nonempty"
        )
    provided_files: dict[str, bytes] = {}
    for index, raw_file in enumerate(files_value):
        file_descriptor = _exact_fields(
            raw_file,
            _CAPTURE_FILE_DESCRIPTOR_FIELDS,
            context=f"{kind} native capture file {index}",
        )
        path = _safe_key(
            file_descriptor.get("path"), context=f"{kind} native capture source path"
        )
        if path in provided_files:
            raise PrivateResearchIntakeError(
                "DUPLICATE_CAPTURE_PATH", f"{kind} native capture path is duplicated"
            )
        reference = _validate_blob_reference(
            file_descriptor.get("blob"), context=f"{kind} native file blob"
        )
        provided_files[path] = _read_blob(
            root, reference, context=f"{kind} native immutable file"
        )
    placeholder = root / "private-research" / "native-capture-manifest.json"
    if kind == "catalog":
        snapshot, capture = _native_catalog_capture(
            root,
            placeholder,
            manifest_bytes,
            manifest,
            provided_files=provided_files,
        )
    else:
        if current_variant_ids is None:
            raise PrivateResearchIntakeError(
                "ADAPTER_INPUT_MISSING", "native sales readback requires catalog IDs"
            )
        snapshot, capture = _native_sales_capture(
            root,
            placeholder,
            manifest_bytes,
            manifest,
            current_variant_ids=current_variant_ids,
            provided_files=provided_files,
        )
    if [item.path for item in capture.files] != list(provided_files):
        raise PrivateResearchIntakeError(
            "ADAPTER_REDERIVATION_MISMATCH",
            f"{kind} native capture file set or order differs",
        )
    unjoined_value = descriptor.get("unjoined_sales_blob")
    if kind == "catalog":
        if unjoined_value is not None or capture.unjoined_sales_bytes is not None:
            raise PrivateResearchIntakeError(
                "SCHEMA_MISMATCH", "catalog capture cannot carry unjoined sales"
            )
    else:
        if (
            not isinstance(unjoined_value, Mapping)
            or unjoined_value.get("schema") != list(_UNJOINED_SALES_ROW_FIELDS)
        ):
            raise PrivateResearchIntakeError(
                "BLOB_CONTRACT_MISMATCH", "unjoined historical sales schema differs"
            )
        unjoined_ref = _validate_blob_reference(
            {key: unjoined_value.get(key) for key in _BLOB_REF_FIELDS},
            context="unjoined historical sales blob",
        )
        unjoined_bytes = _read_blob(
            root, unjoined_ref, context="unjoined historical sales immutable blob"
        )
        if unjoined_bytes != capture.unjoined_sales_bytes:
            raise PrivateResearchIntakeError(
                "ADAPTER_REDERIVATION_MISMATCH",
                "unjoined historical sales differ from native capture derivation",
            )
    expected_descriptor, _ = _native_capture_descriptor(capture)
    if canonical_json_bytes(expected_descriptor) != canonical_json_bytes(descriptor):
        raise PrivateResearchIntakeError(
            "ADAPTER_REDERIVATION_MISMATCH", f"{kind} native descriptor differs"
        )
    return capture, expected_descriptor


def _read_source_blobs(
    root: Path,
    source: Any,
    *,
    kind: str,
    current_variant_ids: set[str] | None = None,
) -> tuple[
    _Snapshot,
    dict[str, Any],
    dict[str, Any],
    _NativeCapture | None,
    dict[str, Any] | None,
]:
    if not isinstance(source, Mapping):
        raise PrivateResearchIntakeError(
            "SCHEMA_MISMATCH", f"{kind} source descriptor must be an object"
        )
    native_value = source.get("native_capture")
    expected_fields = {
        "contract",
        "snapshot_id",
        "manifest_blob",
        "data_blob",
        "source",
        "extraction",
        "query",
        "pagination",
    }
    if kind == "daily-sales":
        expected_fields.add("date_range")
    if native_value is not None:
        expected_fields.add("native_capture")
    _exact_fields(source, frozenset(expected_fields), context=f"{kind} source descriptor")
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
    capture: _NativeCapture | None = None
    capture_descriptor: dict[str, Any] | None = None
    if native_value is not None:
        capture, capture_descriptor = _read_native_capture(
            root,
            native_value,
            kind=kind,
            current_variant_ids=current_variant_ids,
        )
        if (
            capture.contract
            != (
                SHOPIFY_CATALOG_CAPTURE_CONTRACT
                if kind == "catalog"
                else SHOPIFY_DAILY_SALES_CAPTURE_CONTRACT
            )
        ):
            raise PrivateResearchIntakeError(
                "SOURCE_CONTRACT_MISMATCH", f"{kind} native contract differs"
            )
        adapted_snapshot = (
            _native_catalog_capture(
                root,
                root / "unused-native-manifest.json",
                capture.manifest_bytes,
                _parse_json_document(
                    capture.manifest_bytes, path=f"{kind} native manifest"
                ),
                provided_files={item.path: item.data for item in capture.files},
            )[0]
            if kind == "catalog"
            else _native_sales_capture(
                root,
                root / "unused-native-manifest.json",
                capture.manifest_bytes,
                _parse_json_document(
                    capture.manifest_bytes, path=f"{kind} native manifest"
                ),
                current_variant_ids=current_variant_ids or set(),
                provided_files={item.path: item.data for item in capture.files},
            )[0]
        )
        if (
            adapted_snapshot.manifest_bytes != snapshot.manifest_bytes
            or adapted_snapshot.data_bytes != snapshot.data_bytes
        ):
            raise PrivateResearchIntakeError(
                "ADAPTER_REDERIVATION_MISMATCH",
                f"stored normalized {kind} differs from native capture",
            )
    return snapshot, manifest_ref, data_ref, capture, capture_descriptor


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
    _, catalog, catalog_capture = _load_snapshot_from_path(
        root, catalog_manifest_path, kind="catalog"
    )
    _, sales, sales_capture = _load_snapshot_from_path(
        root,
        daily_sales_manifest_path,
        kind="daily-sales",
        current_variant_ids={row["shopify_variant_id"] for row in catalog.rows},
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
    catalog_capture_descriptor: dict[str, Any] | None = None
    sales_capture_descriptor: dict[str, Any] | None = None
    native_publications: list[tuple[dict[str, Any], bytes]] = []
    if catalog_capture is not None:
        catalog_capture_descriptor, publications = _native_capture_descriptor(
            catalog_capture
        )
        native_publications.extend(publications)
    if sales_capture is not None:
        sales_capture_descriptor, publications = _native_capture_descriptor(sales_capture)
        native_publications.extend(publications)
    storage = LocalFilesystemStorage(root)
    # Immutable source blobs are published first.  A failed validation or blob
    # conflict can leave only harmless content-addressed orphans, never an
    # intake that appears complete.
    publications = [
        (catalog_manifest_ref, catalog.manifest_bytes),
        (catalog_data_ref, catalog.data_bytes),
        (sales_manifest_ref, sales.manifest_bytes),
        (sales_data_ref, sales.data_bytes),
        *native_publications,
    ]
    for reference, data in publications:
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
        catalog_capture=catalog_capture,
        sales_capture=sales_capture,
        catalog_capture_descriptor=catalog_capture_descriptor,
        sales_capture_descriptor=sales_capture_descriptor,
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
    (
        catalog,
        catalog_manifest_ref,
        catalog_data_ref,
        catalog_capture,
        catalog_capture_descriptor,
    ) = _read_source_blobs(
        root, sources["catalog"], kind="catalog"
    )
    (
        sales,
        sales_manifest_ref,
        sales_data_ref,
        sales_capture,
        sales_capture_descriptor,
    ) = _read_source_blobs(
        root,
        sources["daily_sales"],
        kind="daily-sales",
        current_variant_ids={row["shopify_variant_id"] for row in catalog.rows},
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
        catalog_capture=catalog_capture,
        sales_capture=sales_capture,
        catalog_capture_descriptor=catalog_capture_descriptor,
        sales_capture_descriptor=sales_capture_descriptor,
    )
    if canonical_json_bytes(expected) != raw:
        raise PrivateResearchIntakeError(
            "INTAKE_REDERIVATION_MISMATCH",
            "stored intake differs from freshly revalidated source evidence",
        )
    return manifest
