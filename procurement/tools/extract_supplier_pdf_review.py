#!/usr/bin/env python3
"""Bounded offline PDF-to-review extraction for explicitly supported layouts.

This developer tool emits source evidence only.  It never imports a mapping,
selects an offer, activates a price, contacts Shopify or a supplier, or creates
an order.  Source documents are untrusted data and never authorization.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from typing import Any, Mapping, Sequence


FORMAT = "BUFFALO_SUPPLIER_PDF_REVIEW_V2"
COVERAGE_FORMAT = "BUFFALO_SUPPLIER_PDF_COVERAGE_V2"
MANIFEST_FORMAT = "BUFFALO_SUPPLIER_PDF_REVIEW_SHA256_V2"
SUPPORTED_FORMAT = "WRIGHT_V1"
LABEL = "REVIEW_ONLY / NOT_APPROVED / NOT_IMPORT_READY"
SOURCE_KIND = "LOCAL_PDF_BYTES"
POPPLER_VERSION = "25.07.0"
ZERO_AUTHORITY_EFFECTS = {
    "mapping_approvals": 0,
    "selected_offers": 0,
    "price_activations": 0,
    "shopify_writes": 0,
    "supplier_contacts": 0,
    "orders": 0,
}
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_PHYSICAL_PAGES = 64
MAX_SELECTED_PAGES = 32
MAX_TSV_BYTES = 16 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_INFO_BYTES = 64 * 1024
MAX_WORDS = 200_000
MAX_LINES = 50_000
MAX_OCCURRENCES = 5_000
MAX_TIERS = 12
MAX_CANONICAL_BYTES = 32 * 1024 * 1024
PDFINFO_TIMEOUT_SECONDS = 10.0
PDFTOTEXT_TIMEOUT_SECONDS = 60.0
_OUTPUT_FILES = ("extraction.json", "coverage.json", "SHA256SUMS.json")
_TSV_HEADER = (
    "level",
    "page_num",
    "par_num",
    "block_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)
_MISSING = object()


class ExtractionError(RuntimeError):
    """A stable, non-secret, fail-closed extraction error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ToolIdentity:
    name: str
    path: Path
    version: str
    sha256: str


@dataclass(frozen=True)
class ProcessResult:
    stdout: bytes
    stderr: bytes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _value(raw: str, value: object = _MISSING) -> dict[str, object]:
    return {"state": "VALUE", "raw": raw, "value": raw if value is _MISSING else value}


def _blank(raw: str = "") -> dict[str, str]:
    return {"state": "BLANK", "raw": raw}


def _explicit_null(raw: str) -> dict[str, object]:
    return {"state": "EXPLICIT_NULL", "raw": raw, "value": None}


def _absent(reason: str) -> dict[str, str]:
    return {"state": "ABSENT", "reason": reason}


def _reject_dot_segments(path: Path, *, code: str) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ExtractionError(code, "path must be absolute and contain no parent traversal")


def _reject_symlink_components(path: Path, *, allow_missing_leaf: bool, code: str) -> None:
    _reject_dot_segments(path, code=code)
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        is_leaf = index == len(parts) - 1
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if allow_missing_leaf and is_leaf:
                return
            raise ExtractionError(code, "path component is missing") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise ExtractionError(code, "symbolic-link path components are forbidden")


def _validated_input_path(path: Path) -> Path:
    _reject_symlink_components(path, allow_missing_leaf=False, code="INPUT_PATH_INVALID")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ExtractionError("INPUT_PATH_INVALID", f"cannot stat input ({type(exc).__name__})") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ExtractionError("INPUT_TYPE_INVALID", "input must be a regular file")
    if metadata.st_size <= 0 or metadata.st_size > MAX_INPUT_BYTES:
        raise ExtractionError("INPUT_SIZE_INVALID", "input size is outside the bounded range")
    if any(ord(character) < 32 or ord(character) == 127 for character in path.name):
        raise ExtractionError("INPUT_NAME_INVALID", "input basename contains a control character")
    return path


def _validated_output_path(path: Path) -> Path:
    _reject_symlink_components(path, allow_missing_leaf=True, code="OUTPUT_PATH_INVALID")
    if path.name in {"", ".", ".."}:
        raise ExtractionError("OUTPUT_PATH_INVALID", "output must name a child directory")
    parent = path.parent
    _reject_symlink_components(parent, allow_missing_leaf=False, code="OUTPUT_PATH_INVALID")
    try:
        metadata = parent.stat()
    except OSError as exc:
        raise ExtractionError("OUTPUT_PATH_INVALID", f"cannot stat output parent ({type(exc).__name__})") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ExtractionError("OUTPUT_PATH_INVALID", "output parent must be a real directory")
    return path


def _copy_input_snapshot(source: Path, destination: Path) -> tuple[str, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(source, flags)
    except OSError as exc:
        raise ExtractionError("INPUT_OPEN_FAILED", f"cannot open input ({type(exc).__name__})") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ExtractionError("INPUT_TYPE_INVALID", "opened input is not a regular file")
        output_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            output_flags |= os.O_NOFOLLOW
        destination_fd = os.open(destination, output_flags, 0o600)
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_INPUT_BYTES:
                    raise ExtractionError("INPUT_SIZE_INVALID", "input exceeded the bounded size while reading")
                digest.update(chunk)
                view = memoryview(chunk)
                offset = 0
                while offset < len(view):
                    written = os.write(destination_fd, view[offset:])
                    if written <= 0:
                        raise OSError("short write while copying input snapshot")
                    offset += written
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            raise ExtractionError("INPUT_CHANGED_DURING_READ", "input metadata changed while snapshotting")
        if size != before.st_size:
            raise ExtractionError("INPUT_CHANGED_DURING_READ", "input length changed while snapshotting")
    finally:
        os.close(source_fd)
    return digest.hexdigest(), size


def _terminate_owned_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
) -> ProcessResult:
    """Run fixed argv with a minimal environment and bounded incremental reads."""

    if not argv or not Path(argv[0]).is_absolute():
        raise ExtractionError("TOOL_ARGV_INVALID", "tool argv[0] must be absolute")
    try:
        process = subprocess.Popen(
            tuple(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            start_new_session=True,
        )
    except OSError as exc:
        raise ExtractionError("TOOL_START_FAILED", f"tool could not start ({type(exc).__name__})") from exc
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, ("stdout", stdout_limit))
    selector.register(process.stderr, selectors.EVENT_READ, ("stderr", stderr_limit))
    collected: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_owned_process(process)
                raise ExtractionError("TOOL_TIMEOUT", "bounded tool execution exceeded its deadline")
            events = selector.select(min(remaining, 0.1))
            if not events and process.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in tuple(selector.get_map().values())]
            for key, _ in events:
                stream_name, limit = key.data
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                collected[stream_name].extend(chunk)
                if len(collected[stream_name]) > limit:
                    _terminate_owned_process(process)
                    raise ExtractionError(
                        "TOOL_OUTPUT_LIMIT",
                        f"{stream_name} exceeded its bounded byte limit",
                    )
        remaining = max(0.0, deadline - time.monotonic())
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            _terminate_owned_process(process)
            raise ExtractionError("TOOL_TIMEOUT", "tool did not exit within its deadline") from exc
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_owned_process(process)
        process.stdout.close()
        process.stderr.close()
    if return_code != 0:
        raise ExtractionError("TOOL_NONZERO_EXIT", "bounded tool returned a nonzero status")
    return ProcessResult(bytes(collected["stdout"]), bytes(collected["stderr"]))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_tool(path: Path, expected_name: str, *, cwd: Path) -> ToolIdentity:
    _reject_dot_segments(path, code="TOOL_PATH_INVALID")
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as exc:
        raise ExtractionError("TOOL_PATH_INVALID", f"cannot resolve tool ({type(exc).__name__})") from exc
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise ExtractionError("TOOL_PATH_INVALID", "tool must resolve to an executable regular file")
    result = _run_bounded(
        (resolved.as_posix(), "-v"),
        cwd=cwd,
        timeout_seconds=PDFINFO_TIMEOUT_SECONDS,
        stdout_limit=MAX_INFO_BYTES,
        stderr_limit=MAX_INFO_BYTES,
    )
    version_text = (result.stdout + b"\n" + result.stderr).decode("utf-8", "strict")
    match = re.search(rf"(?:^|\n){re.escape(expected_name)} version ([0-9]+(?:\.[0-9]+)+)", version_text)
    if not match or match.group(1) != POPPLER_VERSION:
        raise ExtractionError("TOOL_VERSION_UNSUPPORTED", "tool version is not the pinned tested Poppler release")
    return ToolIdentity(expected_name, resolved, match.group(1), _file_sha256(resolved))


def _parse_page_selection(value: str) -> tuple[int, ...]:
    if not value or len(value) > 256:
        raise ExtractionError("PAGE_SELECTION_INVALID", "page selection is empty or too long")
    pages: set[int] = set()
    for item in value.split(","):
        if re.fullmatch(r"[1-9][0-9]*", item):
            pages.add(int(item))
            continue
        match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)", item)
        if not match:
            raise ExtractionError("PAGE_SELECTION_INVALID", "page selection has invalid syntax")
        start, end = int(match.group(1)), int(match.group(2))
        if end < start:
            raise ExtractionError("PAGE_SELECTION_INVALID", "page range is descending")
        if end - start + 1 > MAX_SELECTED_PAGES:
            raise ExtractionError("PAGE_SELECTION_INVALID", "page range is too large")
        pages.update(range(start, end + 1))
    if not pages or len(pages) > MAX_SELECTED_PAGES:
        raise ExtractionError("PAGE_SELECTION_INVALID", "selected-page count is outside the bounded range")
    return tuple(sorted(pages))


def _contiguous_ranges(pages: Sequence[int]) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append((start, previous))
        start = previous = page
    ranges.append((start, previous))
    return tuple(ranges)


def _parse_pdfinfo(raw: bytes) -> dict[str, object]:
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError("PDFINFO_INVALID", "pdfinfo output is not UTF-8") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    try:
        pages = int(values["Pages"])
    except (KeyError, ValueError) as exc:
        raise ExtractionError("PDFINFO_INVALID", "pdfinfo omitted a valid page count") from exc
    if pages <= 0 or pages > MAX_PHYSICAL_PAGES:
        raise ExtractionError("PDF_PAGE_COUNT_INVALID", "physical page count is outside the bounded range")
    encrypted_tokens = values.get("Encrypted", "").split()
    if not encrypted_tokens:
        raise ExtractionError("PDFINFO_INVALID", "pdfinfo omitted encryption status")
    encrypted = encrypted_tokens[0].lower()
    if encrypted != "no":
        raise ExtractionError("PDF_ENCRYPTED", "encrypted PDFs are not accepted")
    pdf_version = values.get("PDF version")
    if not pdf_version or not re.fullmatch(r"[0-9]+\.[0-9]+", pdf_version):
        raise ExtractionError("PDFINFO_INVALID", "pdfinfo omitted a valid PDF version")
    return {"physical_page_count": pages, "pdf_version": pdf_version, "encrypted": False}


def _decimal(raw: str, *, field: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ExtractionError("TSV_INVALID", f"{field} is not a decimal") from exc
    if not value.is_finite():
        raise ExtractionError("TSV_INVALID", f"{field} is not finite")
    return value


def _line_identifier(source_sha256: str, page: int, key: tuple[int, int, int], words: Sequence[Mapping[str, str]]) -> str:
    identity = {
        "block": key[0],
        "line": key[2],
        "page": page,
        "paragraph": key[1],
        "source_sha256": source_sha256,
        "words": [
            [word["word_num"], word["left"], word["top"], word["width"], word["height"], word["text"]]
            for word in words
        ],
    }
    return f"WRT-LINE-{_sha256(_canonical_bytes(identity))[:24]}"


def parse_poppler_tsv(raw: bytes, source_sha256: str, selected_pages: Sequence[int]) -> tuple[dict[str, object], ...]:
    """Parse strict Poppler TSV into lossless page/line/token evidence."""

    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ExtractionError("TSV_INVALID", "pdftotext TSV is not UTF-8") from exc
    rows = text.splitlines()
    if not rows or tuple(rows[0].split("\t")) != _TSV_HEADER:
        raise ExtractionError("TSV_INVALID", "pdftotext TSV header differs")
    selected = set(selected_pages)
    dimensions: dict[int, tuple[str, str]] = {}
    grouped: dict[tuple[int, int, int, int], list[dict[str, str]]] = {}
    word_count = 0
    for row_number, row in enumerate(rows[1:], start=2):
        fields = row.split("\t", 11)
        if len(fields) != 12:
            raise ExtractionError("TSV_INVALID", f"TSV row {row_number} has the wrong field count")
        record = dict(zip(_TSV_HEADER, fields, strict=True))
        try:
            level = int(record["level"])
            page = int(record["page_num"])
        except ValueError as exc:
            raise ExtractionError("TSV_INVALID", f"TSV row {row_number} has invalid integer fields") from exc
        if page not in selected:
            raise ExtractionError("TSV_PAGE_SCOPE_VIOLATION", "pdftotext returned an unselected page")
        for coordinate in ("left", "top", "width", "height"):
            _decimal(record[coordinate], field=coordinate)
        if level == 1:
            dimensions[page] = (record["width"], record["height"])
            continue
        if level != 5:
            continue
        try:
            key = (
                page,
                int(record["block_num"]),
                int(record["par_num"]),
                int(record["line_num"]),
            )
            int(record["word_num"])
        except ValueError as exc:
            raise ExtractionError("TSV_INVALID", f"TSV row {row_number} has invalid grouping fields") from exc
        if not record["text"] or any(
            ord(character) < 32 or ord(character) == 127
            for character in record["text"]
        ):
            raise ExtractionError("TSV_INVALID", "word text is blank or contains a control character")
        word_count += 1
        if word_count > MAX_WORDS:
            raise ExtractionError("TSV_WORD_LIMIT", "extracted word count exceeded the bounded limit")
        grouped.setdefault(key, []).append(record)
    if set(dimensions) != selected:
        raise ExtractionError("TSV_PAGE_SCOPE_VIOLATION", "not every selected page appeared in TSV")
    if len(grouped) > MAX_LINES:
        raise ExtractionError("TSV_LINE_LIMIT", "extracted line count exceeded the bounded limit")

    pages: list[dict[str, object]] = []
    for page in selected_pages:
        width_raw, height_raw = dimensions[page]
        width = _decimal(width_raw, field="page width")
        lines: list[dict[str, object]] = []
        page_groups = [(key, words) for key, words in grouped.items() if key[0] == page]
        for key, words in page_groups:
            words.sort(key=lambda word: int(word["word_num"]))
            left = min(_decimal(word["left"], field="left") for word in words)
            top = min(_decimal(word["top"], field="top") for word in words)
            right = max(
                _decimal(word["left"], field="left") + _decimal(word["width"], field="width")
                for word in words
            )
            bottom = max(
                _decimal(word["top"], field="top") + _decimal(word["height"], field="height")
                for word in words
            )
            center = (left + right) / 2
            if center < width * Decimal("0.48"):
                column = "LEFT"
            elif center > width * Decimal("0.52"):
                column = "RIGHT"
            else:
                column = "PAGE_WIDE"
            line_key = (key[1], key[2], key[3])
            line_words = [
                {
                    "ordinal": int(word["word_num"]),
                    "text": word["text"],
                    "left": word["left"],
                    "top": word["top"],
                    "width": word["width"],
                    "height": word["height"],
                    "confidence": word["conf"],
                }
                for word in words
            ]
            lines.append(
                {
                    "line_id": _line_identifier(source_sha256, page, line_key, words),
                    "physical_page": page,
                    "column": column,
                    "block_num": key[1],
                    "paragraph_num": key[2],
                    "line_num": key[3],
                    "bbox": {
                        "left": format(left, "f"),
                        "top": format(top, "f"),
                        "right": format(right, "f"),
                        "bottom": format(bottom, "f"),
                    },
                    "text": " ".join(word["text"] for word in words),
                    "words": line_words,
                }
            )
        lines.sort(key=lambda line: (Decimal(str(line["bbox"]["top"])), Decimal(str(line["bbox"]["left"])), str(line["line_id"])))
        pages.append(
            {
                "physical_page": page,
                "page_width": width_raw,
                "page_height": height_raw,
                "lines": lines,
            }
        )
    return tuple(pages)


_REGULAR_PACK = re.compile(
    r"^(?P<size>[1-9][0-9]{0,4}(?:\.[0-9]{1,3})?\s*(?:mL|L|oz))\s+"
    r"(?P<case_pack>[1-9][0-9]{0,3})\s+Pack"
    r"(?:\s+(?P<qualifier>[A-Za-z][A-Za-z0-9-]{0,31}))?\s*"
    r"-\s*(?P<code>[A-Za-z0-9][A-Za-z0-9-]*)$",
    re.IGNORECASE,
)
_RETAIL_SIZE_FIRST = re.compile(
    r"^\((?P<outer>[1-9][0-9]{0,3})\)\s+"
    r"(?P<size>[1-9][0-9]{0,4}(?:\.[0-9]{1,3})?\s*(?:mL|L|oz))\s+"
    r"(?P<inner>[1-9][0-9]{0,3})\s+Pack\s*"
    r"-\s*(?P<code>[A-Za-z0-9][A-Za-z0-9-]*)$",
    re.IGNORECASE,
)
_RETAIL_PACK_FIRST = re.compile(
    r"^\((?P<outer>[1-9][0-9]{0,3})\)\s+"
    r"(?P<inner>[1-9][0-9]{0,3})\s*pack\s+"
    r"(?P<size>[1-9][0-9]{0,4}(?:\.[0-9]{1,3})?\s*(?:mL|L|oz))\s+"
    r"(?P<container>cans?|bottles?|boxes?)\s*-\s*(?P<code>[A-Za-z0-9][A-Za-z0-9-]*)$",
    re.IGNORECASE,
)
_TIER = re.compile(
    r"^(?P<count>[1-9][0-9]{0,5})(?P<plus>\+)?\s+Cases?:\s*"
    r"\$(?P<case>[0-9]{1,6}\.[0-9]{2})\s*[·•]\s*"
    r"Per\s+(?P<unit>Bottle|Can|Box):\s*"
    r"\$(?P<unit_price>[0-9]{1,6}\.[0-9]{2})"
    r"(?:\s*[·•]\s*Per\s+Pack:\s*"
    r"\$(?P<pack>[0-9]{1,6}\.[0-9]{2}))?$",
    re.IGNORECASE,
)
_MISSING_CODE_PACK = re.compile(r"(?:Pack|Cans?|Bottles?|Boxes?)\s*-\s*$", re.IGNORECASE)
_PRINTED_PAGE = re.compile(r"\bPage\s+([1-9][0-9]{0,3})\b", re.IGNORECASE)
_SPLIT_CHARGE = re.compile(
    r"^Split case charge \$(?P<amount>[0-9]{1,6}\.[0-9]{2}) "
    r"per (?P<basis>bottle|can|box)$",
    re.IGNORECASE,
)
_SPLIT_INCLUDED = re.compile(
    r"^Prices include split case fee \$(?P<amount>[0-9]{1,6}\.[0-9]{2}) "
    r"per (?P<basis>bottle|can|box)$",
    re.IGNORECASE,
)
_UNSUPPORTED_SCOPE = re.compile(
    r"\b(?:gift|combo|specials?|assorted|components?)\b",
    re.IGNORECASE,
)
_POTENTIAL_COMMERCIAL_SCOPE = re.compile(
    r"\b(?:"
    r"allocat(?:e|ed|es|ion|ions)?|"
    r"territor(?:y|ies)|states?|channels?|"
    r"on[- ]premise|off[- ]premise|retail[- ]only|restaurant[- ]only|"
    r"minimum(?:\s+order)?|min(?:imum)?\.?\s*order|"
    r"availability|available|while\s+supplies\s+last|"
    r"split(?:\s+case)?|handling|"
    r"assort(?:ed|ment|ments|ing)?|does\s+not\s+assort"
    r")\b",
    re.IGNORECASE,
)
_PACK_LIKE = re.compile(r"\b(?:pack|cans?|bottles?|boxes?)\b.*-", re.IGNORECASE)
_TIER_LIKE = re.compile(r"\bCases?\s*:|\bPer\s+(?:Bottle|Can|Box|Pack)\s*:", re.IGNORECASE)
_UNSAFE_MARKUP = re.compile(r"[<>]")


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def _has_damaged_text(text: str) -> bool:
    """Recognize damaged native text without assigning it a semantic meaning."""

    return any(
        character == "\ufffd"
        or character in {"\u25a1", "\u25a0", "\u25fb", "\u25fc"}
        or unicodedata.category(character) in {"Co", "Cs"}
        for character in text
    )


def _line_evidence(line: Mapping[str, object]) -> dict[str, object]:
    """Return source-linked evidence without dropping raw text or coordinates."""

    raw = str(line["text"])
    payload = {
        "source_line_id": str(line["line_id"]),
        "raw": raw,
        "normalized": _normalized(raw),
        "column": line["column"],
        "bbox": dict(line["bbox"]),
    }
    return {
        **payload,
        "evidence_sha256": _sha256(_canonical_bytes(payload)),
    }


def _scope_diagnostic(text: str) -> tuple[str, str]:
    """Classify uncertainty for diagnostics only; classification never clears it."""

    if _has_damaged_text(text):
        return "DAMAGED_SCOPE_TEXT_UNRESOLVED", "DAMAGED_NATIVE_TEXT"
    if _POTENTIAL_COMMERCIAL_SCOPE.search(text):
        return "POTENTIAL_COMMERCIAL_SCOPE_UNRESOLVED", "COMMERCIAL_SCOPE_LANGUAGE"
    if _UNSUPPORTED_SCOPE.search(text):
        return "UNSUPPORTED_CLASS_OR_COMPONENT_EVIDENCE", "UNSUPPORTED_LAYOUT_LANGUAGE"
    if _UNSAFE_MARKUP.search(text):
        return "UNSAFE_MARKUP_LIKE_SOURCE_TEXT", "UNSAFE_MARKUP"
    if "$" in text or _TIER_LIKE.search(text):
        return "UNASSIGNED_PRICE_LIKE_LINE", "PRICE_OR_TIER_LANGUAGE"
    if _PACK_LIKE.search(text):
        return "UNSUPPORTED_OR_MALFORMED_PACK_LINE", "PACK_LIKE_LANGUAGE"
    return "UNCLASSIFIED_SOURCE_LINE", "UNKNOWN_SCOPE_LANGUAGE"


def _top(line: Mapping[str, object]) -> Decimal:
    return Decimal(str(line["bbox"]["top"]))


def _bottom(line: Mapping[str, object]) -> Decimal:
    return Decimal(str(line["bbox"]["bottom"]))


def _pack_match(text: str) -> tuple[str, re.Match[str]] | None:
    for kind, pattern in (
        ("REGULAR", _REGULAR_PACK),
        ("RETAIL_MULTIPACK", _RETAIL_SIZE_FIRST),
        ("RETAIL_MULTIPACK", _RETAIL_PACK_FIRST),
    ):
        match = pattern.fullmatch(text)
        if match:
            return kind, match
    return None


def _is_boundary_text(text: str) -> bool:
    return (
        not text
        or "wrightbev.com" in text.lower()
        or _SPLIT_CHARGE.fullmatch(text) is not None
        or _SPLIT_INCLUDED.fullmatch(text) is not None
        or bool(_PRINTED_PAGE.search(text))
        or set(text.replace(" ", "")) <= {"•", "."}
        or text.upper() in {"SPIRITS", "WINES", "RTD BEVERAGES"}
    )


def _ordered_column_lines(
    pack: Mapping[str, object],
    lines: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        sorted(
            (
                line for line in lines
                if line["column"] in {pack["column"], "PAGE_WIDE"}
            ),
            key=lambda line: (_top(line), str(line["line_id"])),
        )
    )


def _description_cluster(pack: Mapping[str, object], lines: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    ordered = _ordered_column_lines(pack, lines)
    try:
        pack_index = next(
            index for index, line in enumerate(ordered)
            if line["line_id"] == pack["line_id"]
        )
    except StopIteration as exc:
        raise ExtractionError("BLOCK_SCOPE_INVALID", "pack line is absent from its column") from exc
    if pack_index == 0:
        return ()
    nearest = ordered[pack_index - 1]
    nearest_text = _normalized(str(nearest["text"]))
    if (
        _top(pack) - _top(nearest) > Decimal("26")
        or nearest["column"] == "PAGE_WIDE"
        or _is_boundary_text(nearest_text)
        or _pack_match(nearest_text) is not None
        or _TIER.fullmatch(nearest_text) is not None
        or _PACK_LIKE.search(nearest_text) is not None
        or _TIER_LIKE.search(nearest_text) is not None
    ):
        return ()
    cluster: list[Mapping[str, object]] = [nearest]
    cursor = pack_index - 2
    while cursor >= 0 and len(cluster) < 3:
        line = ordered[cursor]
        text = _normalized(str(line["text"]))
        if (
            _top(cluster[0]) - _top(line) > Decimal("26")
            or line["column"] == "PAGE_WIDE"
            or _is_boundary_text(text)
            or _pack_match(text) is not None
            or _TIER.fullmatch(text) is not None
            or _PACK_LIKE.search(text) is not None
            or _TIER_LIKE.search(text) is not None
        ):
            break
        cluster.insert(0, line)
        cursor -= 1
    return tuple(cluster)


def _nearby_scope_lines(
    pack: Mapping[str, object],
    lines: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Return the bounded same-column heading/description scope before a pack."""

    result: list[Mapping[str, object]] = []
    for line in reversed(_ordered_column_lines(pack, lines)):
        if _top(line) >= _top(pack):
            continue
        if _top(pack) - _top(line) > Decimal("90"):
            break
        text = _normalized(str(line["text"]))
        if set(text.replace(" ", "")) <= {"•", "."}:
            break
        if _pack_match(text) is not None or _TIER.fullmatch(text) is not None:
            break
        result.append(line)
    return tuple(reversed(result))


def _description_and_notes(
    cluster: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object] | None, dict[str, object], str | None]:
    """Resolve only an exact supported description/note shape.

    Position, keywords, and confidence never choose between competing lines.
    Any other multi-line cluster remains source evidence and is quarantined.
    """

    candidates = [_line_evidence(line) for line in cluster]
    if len(cluster) == 1:
        raw = str(cluster[0]["text"])
        normalized = _normalized(raw)
        if _has_damaged_text(raw) or _POTENTIAL_COMMERCIAL_SCOPE.search(normalized):
            reason = "DESCRIPTION_OR_SCOPED_NOTE_UNRESOLVED"
            return (
                None,
                {
                    "state": "UNRESOLVED",
                    "reason": reason,
                    "candidate_line_ids": [str(cluster[0]["line_id"])],
                    "candidates": candidates,
                },
                reason,
            )
        return (
            cluster[0],
            {
                "state": "ABSENT",
                "reason": "NO_SCOPED_NOTE_VALUE_ESTABLISHED_BY_SUPPORTED_GRAMMAR",
            },
            None,
        )
    if len(cluster) == 2:
        description_text = _normalized(str(cluster[0]["text"]))
        note_text = _normalized(str(cluster[1]["text"]))
        if (
            "variety pack" in description_text.lower()
            and ("," in note_text or " and " in note_text.lower())
            and not _has_damaged_text(str(cluster[0]["text"]))
            and not _has_damaged_text(str(cluster[1]["text"]))
            and not _POTENTIAL_COMMERCIAL_SCOPE.search(note_text)
        ):
            return (
                cluster[0],
                {
                    "state": "VALUE",
                    "reason": "SUPPORTED_VARIETY_LIST_GRAMMAR",
                    "items": [_line_evidence(cluster[1])],
                },
                None,
            )
    reason = "DESCRIPTION_CANDIDATES_AMBIGUOUS_OR_DISPLACED"
    return (
        None,
        {
            "state": "UNRESOLVED",
            "reason": reason,
            "candidate_line_ids": [str(line["line_id"]) for line in cluster],
            "candidates": candidates,
        },
        reason,
    )


def _tier_scope(
    pack: Mapping[str, object],
    lines: Sequence[Mapping[str, object]],
) -> tuple[
    tuple[Mapping[str, object], ...],
    Mapping[str, object] | None,
    tuple[Mapping[str, object], ...],
]:
    """Return contiguous tiers, a positional barrier, and partial later tiers."""

    ordered = _ordered_column_lines(pack, lines)
    try:
        pack_index = next(
            index for index, line in enumerate(ordered)
            if line["line_id"] == pack["line_id"]
        )
    except StopIteration as exc:
        raise ExtractionError("BLOCK_SCOPE_INVALID", "pack line is absent from its column") from exc
    following = ordered[pack_index + 1 :]
    tiers: list[Mapping[str, object]] = []
    previous_bottom = _bottom(pack)
    barrier: Mapping[str, object] | None = None
    barrier_index: int | None = None
    for index, line in enumerate(following):
        gap = _top(line) - previous_bottom
        text = _normalized(str(line["text"]))
        match = _TIER.fullmatch(text)
        if line["column"] != "PAGE_WIDE" and match and gap <= Decimal("24"):
            tiers.append(line)
            previous_bottom = _bottom(line)
            if len(tiers) > MAX_TIERS:
                raise ExtractionError("TIER_LIMIT", "a source block exceeded the tier limit")
            continue
        if (
            gap <= Decimal("24")
            and not _is_boundary_text(text)
            and _pack_match(text) is None
        ):
            barrier = line
            barrier_index = index
        break

    partial_later_tiers: list[Mapping[str, object]] = []
    if barrier is not None and barrier_index is not None:
        previous_bottom = _bottom(barrier)
        for line in following[barrier_index + 1 :]:
            gap = _top(line) - previous_bottom
            text = _normalized(str(line["text"]))
            if (
                line["column"] == "PAGE_WIDE"
                or gap > Decimal("24")
                or _TIER.fullmatch(text) is None
            ):
                break
            partial_later_tiers.append(line)
            previous_bottom = _bottom(line)
            if len(partial_later_tiers) > MAX_TIERS:
                raise ExtractionError("TIER_LIMIT", "a source block exceeded the tier limit")
    return tuple(tiers), barrier, tuple(partial_later_tiers)


def _tier_lines(pack: Mapping[str, object], lines: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    """Compatibility helper for the supported contiguous tier subset."""

    return _tier_scope(pack, lines)[0]


def _source_term_identifier(source_sha256: str, page: int, line_id: str) -> str:
    payload = {
        "format": SUPPORTED_FORMAT,
        "kind": "SPLIT_CASE_TERM",
        "physical_page": page,
        "source_line_id": line_id,
        "source_sha256": source_sha256,
    }
    return f"WRT-TERM-{_sha256(_canonical_bytes(payload))[:32]}"


def _wright_footer(page: Mapping[str, object], source_sha256: str) -> dict[str, object]:
    page_number = int(page["physical_page"])
    page_height = Decimal(str(page["page_height"]))
    footer_lines = [
        line for line in page["lines"]
        if _top(line) >= page_height * Decimal("0.90")
    ]
    identity_lines = [
        line for line in footer_lines
        if "www.wrightbev.com" in _normalized(str(line["text"])).lower()
    ]
    term_matches: list[tuple[Mapping[str, object], re.Match[str], str]] = []
    page_matches: list[tuple[Mapping[str, object], re.Match[str]]] = []
    for line in footer_lines:
        normalized = _normalized(str(line["text"]))
        charge = _SPLIT_CHARGE.fullmatch(normalized)
        included = _SPLIT_INCLUDED.fullmatch(normalized)
        if charge is not None:
            term_matches.append((line, charge, "SEPARATELY_CHARGED_NOT_INCLUDED_IN_ITEM_PRICE"))
        elif included is not None:
            term_matches.append((line, included, "ALREADY_INCLUDED_DO_NOT_ADD"))
        page_match = _PRINTED_PAGE.fullmatch(normalized)
        if page_match is not None:
            page_matches.append((line, page_match))
    if len(identity_lines) != 1 or len(term_matches) != 1 or len(page_matches) != 1:
        raise ExtractionError(
            "UNSUPPORTED_SOURCE_LAYOUT",
            "selected page lacks one exact Wright footer identity, split term, or printed-page label",
        )
    identity_line = identity_lines[0]
    term_line, term_match, inclusion = term_matches[0]
    page_line, page_match = page_matches[0]
    footer_triplet = (identity_line, term_line, page_line)
    if len({str(line["line_id"]) for line in footer_triplet}) != 3:
        raise ExtractionError("UNSUPPORTED_SOURCE_LAYOUT", "Wright footer facts are not distinct lines")
    tops = [_top(line) for line in footer_triplet]
    lefts = [Decimal(str(line["bbox"]["left"])) for line in footer_triplet]
    if max(tops) - min(tops) > Decimal("4") or not lefts[0] < lefts[1] < lefts[2]:
        raise ExtractionError("UNSUPPORTED_SOURCE_LAYOUT", "Wright footer geometry differs")
    term_line_id = str(term_line["line_id"])
    term_id = _source_term_identifier(source_sha256, page_number, term_line_id)
    return {
        "source_line_ids": [str(line["line_id"]) for line in footer_triplet],
        "printed_page": _value(str(page_line["text"]), int(page_match.group(1))),
        "term": {
            "source_term_id": term_id,
            "source_line_id": term_line_id,
            "physical_page": page_number,
            "raw": str(term_line["text"]),
            "amount": _value(term_match.group("amount"), term_match.group("amount")),
            "basis": _value(term_match.group("basis"), term_match.group("basis").upper()),
            "scope": "PAGE_LEVEL_ONLY",
            "inclusion": inclusion,
            "allocation": "UNRESOLVED_NOT_ALLOCATED_TO_ANY_OCCURRENCE",
        },
    }


def _source_tier_identifier(source_sha256: str, page: int, line_id: str) -> str:
    payload = {
        "format": SUPPORTED_FORMAT,
        "kind": "PRINTED_LADDER_TIER",
        "physical_page": page,
        "source_line_id": line_id,
        "source_sha256": source_sha256,
    }
    return f"WRT-TIER-{_sha256(_canonical_bytes(payload))[:32]}"


def _tier_record(
    line: Mapping[str, object],
    source_sha256: str,
    page: int,
) -> dict[str, object]:
    text = _normalized(str(line["text"]))
    match = _TIER.fullmatch(text)
    assert match is not None
    count = int(match.group("count"))
    threshold_raw = f"{match.group('count')}{match.group('plus') or ''} {'Case' if count == 1 else 'Cases'}"
    return {
        "source_tier_id": _source_tier_identifier(
            source_sha256,
            page,
            str(line["line_id"]),
        ),
        "source_line_id": line["line_id"],
        "threshold": {
            "raw": threshold_raw,
            "count": count,
            "or_more": bool(match.group("plus")),
            "unit": "CASE",
        },
        "case_price": _value(match.group("case"), match.group("case")),
        "per_unit": {
            "label_raw": match.group("unit"),
            "price": _value(match.group("unit_price"), match.group("unit_price")),
        },
        "per_retail_pack": (
            _value(match.group("pack"), match.group("pack"))
            if match.group("pack") is not None
            else _absent("NO_PER_PACK_VALUE_PRINTED_ON_TIER")
        ),
        "text": line["text"],
    }


def _arithmetic_diagnostic(
    kind: str,
    groups: Mapping[str, str],
    tier: Mapping[str, object],
) -> dict[str, object]:
    try:
        if kind == "REGULAR":
            divisor = int(groups["case_pack"])
            basis = "PRINTED_CASE_PACK"
            inner = None
        else:
            outer = int(groups["outer"])
            inner = int(groups["inner"])
            divisor = outer * inner
            basis = "PRINTED_OUTER_COUNT_X_INNER_PACK_COUNT"
        if divisor <= 0 or divisor > 10_000_000:
            raise ValueError("divisor outside bounded positive range")
        case_price = Decimal(str(tier["case_price"]["value"]))
        unit_price = Decimal(str(tier["per_unit"]["price"]["value"]))
        scale = max(0, -unit_price.as_tuple().exponent)
        quantum = Decimal(1).scaleb(-scale)
        computed = (case_price / Decimal(divisor)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    except (DecimalException, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ExtractionError("SOURCE_NUMERIC_INVALID", "printed arithmetic is outside bounded grammar") from exc
    diagnostic: dict[str, object] = {
        "basis": basis,
        "printed_divisor": divisor,
        "computed_at_printed_scale": format(computed, f".{scale}f"),
        "printed_per_unit": format(unit_price, f".{scale}f"),
        "matches_rounding_at_printed_scale": computed == unit_price,
        "policy": "PRESERVE_PRINTED_NO_TOLERANCE_NO_SUBSTITUTION",
    }
    if kind == "RETAIL_MULTIPACK":
        per_pack = tier["per_retail_pack"]
        if per_pack["state"] == "VALUE":
            assert inner is not None
            printed_pack = Decimal(str(per_pack["value"]))
            pack_scale = max(0, -printed_pack.as_tuple().exponent)
            pack_quantum = Decimal(1).scaleb(-pack_scale)
            computed_pack = (unit_price * Decimal(inner)).quantize(
                pack_quantum,
                rounding=ROUND_HALF_UP,
            )
            diagnostic["retail_pack_check"] = {
                "basis": "PRINTED_PER_PHYSICAL_UNIT_X_PRINTED_INNER_PACK_COUNT",
                "computed_at_printed_scale": format(computed_pack, f".{pack_scale}f"),
                "printed_per_pack": format(printed_pack, f".{pack_scale}f"),
                "matches_rounding_at_printed_scale": computed_pack == printed_pack,
            }
        else:
            diagnostic["retail_pack_check"] = _absent("NO_PER_PACK_VALUE_PRINTED_ON_TIER")
        container_units = {
            "bottle": "BOTTLE",
            "bottles": "BOTTLE",
            "box": "BOX",
            "boxes": "BOX",
            "can": "CAN",
            "cans": "CAN",
        }
        expected_unit = container_units.get(groups.get("container", "").casefold(), "")
        observed_unit = str(tier["per_unit"]["label_raw"]).upper()
        diagnostic["container_unit_consistency"] = (
            {
                "state": "VALUE",
                "expected": expected_unit,
                "observed": observed_unit,
                "matches": expected_unit == observed_unit,
            }
            if expected_unit
            else _absent("NO_CONTAINER_WORD_PRINTED_IN_SIZE_FIRST_GRAMMAR")
        )
    return diagnostic


def _occurrence_identifier(source_sha256: str, page: int, line_ids: Sequence[str]) -> str:
    payload = {
        "format": SUPPORTED_FORMAT,
        "line_ids": list(line_ids),
        "physical_page": page,
        "source_sha256": source_sha256,
    }
    return f"WRT-OCC-{_sha256(_canonical_bytes(payload))[:32]}"


def _candidate_block_identifier(
    source_sha256: str,
    page: int,
    line_ids: Sequence[str],
    reason: str,
) -> str:
    payload = {
        "format": FORMAT,
        "kind": "UNRESOLVED_OCCURRENCE_BLOCK",
        "line_ids": list(line_ids),
        "physical_page": page,
        "reason": reason,
        "source_sha256": source_sha256,
    }
    return f"WRT-CANDIDATE-{_sha256(_canonical_bytes(payload))[:32]}"


def _package_record(kind: str, groups: Mapping[str, str], pack_text: str) -> dict[str, object]:
    if kind == "REGULAR":
        return {
            "size": _value(groups["size"], groups["size"]),
            "case_pack": _value(groups["case_pack"], int(groups["case_pack"])),
            "qualifier": (
                _value(groups["qualifier"], groups["qualifier"])
                if "qualifier" in groups
                else _absent("NO_QUALIFIER_PRINTED")
            ),
            "outer_count": _absent("NOT_A_RETAIL_MULTIPACK_GRAMMAR"),
            "inner_pack_count": _absent("NOT_A_RETAIL_MULTIPACK_GRAMMAR"),
            "container": _absent("NOT_A_RETAIL_MULTIPACK_GRAMMAR"),
        }
    return {
        "size": _value(groups["size"], groups["size"]),
        "case_pack": _absent("NO_SHOPIFY_OR_OPERATIONAL_CASE_COUNT_INFERENCE"),
        "qualifier": _absent("NO_OPERATIONAL_QUALIFIER_INFERENCE"),
        "outer_count": _value(groups["outer"], int(groups["outer"])),
        "inner_pack_count": _value(groups["inner"], int(groups["inner"])),
        "container": (
            _value(groups["container"], groups["container"])
            if "container" in groups
            else _absent("NO_CONTAINER_WORD_PRINTED_IN_SUPPORTED_SIZE_FIRST_GRAMMAR")
        ),
        "multipack_semantics": {
            "state": "UNRESOLVED",
            "raw": pack_text,
            "reason": "PRINTED_COUNTS_RETAINED_WITHOUT_SHOPIFY_UNIT_INFERENCE",
        },
    }


def _tier_records(
    tier_lines: Sequence[Mapping[str, object]],
    source_sha256: str,
    page: int,
    kind: str,
    groups: Mapping[str, str],
) -> list[dict[str, object]]:
    records = [_tier_record(line, source_sha256, page) for line in tier_lines]
    for tier in records:
        tier["arithmetic_diagnostic"] = _arithmetic_diagnostic(kind, groups, tier)
    return records


def parse_wright_pages(pages: Sequence[Mapping[str, object]], source_sha256: str) -> tuple[dict[str, object], dict[str, object]]:
    """Recognize two Wright grammars while surfacing every scope uncertainty."""

    occurrences: list[dict[str, object]] = []
    all_quarantines: list[dict[str, object]] = []
    all_page_terms: list[dict[str, object]] = []
    page_outputs: list[dict[str, object]] = []
    for page in pages:
        page_number = int(page["physical_page"])
        footer = _wright_footer(page, source_sha256)
        lines = list(page["lines"])
        lines_by_id = {str(line["line_id"]): line for line in lines}
        assignments: dict[str, str] = {}
        quarantines: list[dict[str, object]] = []
        page_occurrences: list[dict[str, object]] = []
        for line_id in footer["source_line_ids"]:
            assignments[str(line_id)] = "BOILERPLATE"
        for line in lines:
            text = _normalized(str(line["text"]))
            if set(text.replace(" ", "")) <= {"•", "."}:
                assignments[str(line["line_id"])] = "BOILERPLATE"

        def unique_lines(
            candidates: Sequence[Mapping[str, object]],
        ) -> tuple[Mapping[str, object], ...]:
            seen: set[str] = set()
            result: list[Mapping[str, object]] = []
            for candidate in candidates:
                line_id = str(candidate["line_id"])
                if line_id not in seen:
                    seen.add(line_id)
                    result.append(candidate)
            return tuple(result)

        def add_quarantine(
            reason: str,
            candidates: Sequence[Mapping[str, object]],
            *,
            anchor: Mapping[str, object] | None,
            text: str,
            conflicts: Sequence[str] = (),
            scope_level: str = "COLUMN",
            uncertainty_class: str = "UNKNOWN_SCOPE_LANGUAGE",
        ) -> dict[str, object]:
            scoped = unique_lines(candidates)
            claimable = tuple(
                candidate
                for candidate in scoped
                if str(candidate["line_id"]) not in assignments
            )
            for candidate in claimable:
                assignments[str(candidate["line_id"])] = "EXPLICIT_QUARANTINE"
            anchor_id = (
                str(anchor["line_id"])
                if anchor is not None
                else f"PAGE-{page_number}"
            )
            line_ids = [str(line["line_id"]) for line in claimable]
            conflict_ids = list(dict.fromkeys(conflicts))
            identity = {
                "anchor": anchor_id,
                "conflicts": conflict_ids,
                "format": FORMAT,
                "line_ids": line_ids,
                "physical_page": page_number,
                "reason": reason,
                "scope_level": scope_level,
                "source_sha256": source_sha256,
            }
            record: dict[str, object] = {
                "quarantine_id": f"WRT-Q-{_sha256(_canonical_bytes(identity))[:24]}",
                "physical_page": page_number,
                "column": anchor["column"] if anchor is not None else "PAGE",
                "scope_level": scope_level,
                "uncertainty_class": uncertainty_class,
                "reason": reason,
                "source_line_ids": line_ids,
                "source_evidence": [_line_evidence(line) for line in claimable],
                "conflicting_line_ids": conflict_ids,
                "text": text,
            }
            quarantines.append(record)
            return record

        protected_description_ids: set[str] = set()
        for candidate_pack in lines:
            candidate_text = _normalized(str(candidate_pack["text"]))
            if (
                _pack_match(candidate_text) is not None
                or _MISSING_CODE_PACK.search(candidate_text)
                or _PACK_LIKE.search(candidate_text)
            ):
                protected_description_ids.update(
                    str(line["line_id"])
                    for line in _description_cluster(candidate_pack, lines)
                )

        def add_candidate_block(
            reason: str,
            source_lines: Sequence[Mapping[str, object]],
            *,
            pack: Mapping[str, object],
            description_line: Mapping[str, object] | None,
            scoped_notes: Mapping[str, object],
            printed_code: Mapping[str, object],
            package: Mapping[str, object] | None,
            tier_records: Sequence[Mapping[str, object]],
            uncertainty_class: str,
        ) -> None:
            scoped = unique_lines(source_lines)
            record = add_quarantine(
                reason,
                scoped,
                anchor=pack,
                text=str(pack["text"]),
                scope_level="OCCURRENCE_BLOCK",
                uncertainty_class=uncertainty_class,
            )
            candidate_id = _candidate_block_identifier(
                source_sha256,
                page_number,
                [str(line["line_id"]) for line in scoped],
                reason,
            )
            description: dict[str, object]
            if description_line is None:
                description = {
                    "state": "UNRESOLVED",
                    "reason": reason,
                    "candidate_line_ids": [
                        str(line["line_id"])
                        for line in scoped
                        if line is not pack and _pack_match(_normalized(str(line["text"]))) is None
                        and _TIER.fullmatch(_normalized(str(line["text"]))) is None
                    ],
                    "candidates": [
                        _line_evidence(line)
                        for line in scoped
                        if line is not pack and _pack_match(_normalized(str(line["text"]))) is None
                        and _TIER.fullmatch(_normalized(str(line["text"]))) is None
                    ],
                }
            else:
                description = _value(
                    str(description_line["text"]),
                    _normalized(str(description_line["text"])),
                )
            block_notes = dict(scoped_notes)
            if block_notes.get("state") == "ABSENT":
                unresolved_note_lines = [
                    line
                    for line in scoped
                    if line is not description_line
                    and line is not pack
                    and _TIER.fullmatch(_normalized(str(line["text"]))) is None
                ]
                block_notes = {
                    "state": "UNRESOLVED",
                    "reason": "BLOCK_SCOPE_UNRESOLVED; NOTE_ABSENCE_NOT_ESTABLISHED",
                    "candidate_line_ids": [
                        str(line["line_id"]) for line in unresolved_note_lines
                    ],
                    "candidates": [
                        _line_evidence(line) for line in unresolved_note_lines
                    ],
                }
            record.update(
                {
                    "candidate_block_id": candidate_id,
                    "identity_class": "UNRESOLVED_SOURCE_BLOCK_NOT_SUPPORTED_OCCURRENCE",
                    "evidence_status": "PARTIAL_REVIEW_REQUIRED",
                    "description": description,
                    "scoped_notes": block_notes,
                    "evidence_fields": {
                        "printed_code": dict(printed_code),
                        "package": dict(package) if package is not None else _absent(
                            "PACKAGE_GRAMMAR_NOT_ESTABLISHED"
                        ),
                        "pack_line": _value(
                            str(pack["text"]),
                            _normalized(str(pack["text"])),
                        ),
                        "tiers": [dict(tier) for tier in tier_records],
                    },
                }
            )
            record["unresolved_evidence_refs"] = [
                {
                    "quarantine_id": record["quarantine_id"],
                    "scope_level": record["scope_level"],
                    "reason": record["reason"],
                    "source_line_ids": record["source_line_ids"],
                }
            ]

        for pack in lines:
            pack_line_id = str(pack["line_id"])
            if pack_line_id in assignments:
                continue
            pack_text = _normalized(str(pack["text"]))
            matched = _pack_match(pack_text)
            if matched is None:
                if _MISSING_CODE_PACK.search(pack_text):
                    cluster = _description_cluster(pack, lines)
                    tiers, barrier, later_tiers = _tier_scope(pack, lines)
                    scoped_barrier = (
                        (barrier,)
                        if (not tiers or later_tiers)
                        and barrier is not None
                        and barrier["column"] == pack["column"]
                        and str(barrier["line_id"]) not in protected_description_ids
                        else ()
                    )
                    add_candidate_block(
                        "MISSING_PRINTED_CODE",
                        (*cluster, pack, *tiers, *scoped_barrier, *later_tiers),
                        pack=pack,
                        description_line=None,
                        scoped_notes={
                            "state": "UNRESOLVED",
                            "reason": "DESCRIPTION_AND_NOTES_NOT_RESOLVED_FOR_CODELESS_BLOCK",
                            "candidate_line_ids": [str(line["line_id"]) for line in cluster],
                            "candidates": [_line_evidence(line) for line in cluster],
                        },
                        printed_code=_blank(""),
                        package=None,
                        tier_records=(),
                        uncertainty_class="IDENTITY_SCOPE_UNRESOLVED",
                    )
                continue

            kind, match = matched
            groups = {
                key: value
                for key, value in match.groupdict().items()
                if value is not None
            }
            package = _package_record(kind, groups, pack_text)
            description_cluster = _description_cluster(pack, lines)
            tiers, barrier, later_tiers = _tier_scope(pack, lines)
            description_line: Mapping[str, object] | None = None
            scoped_notes: dict[str, object] = {
                "state": "UNRESOLVED",
                "reason": "DESCRIPTION_SCOPE_UNRESOLVED",
                "candidate_line_ids": [],
                "candidates": [],
            }
            reason: str | None = None
            uncertainty_class = "IDENTITY_SCOPE_UNRESOLVED"
            if not description_cluster:
                reason = "DESCRIPTION_SCOPE_UNRESOLVED"
            else:
                description_line, scoped_notes, description_reason = _description_and_notes(
                    description_cluster
                )
                if description_reason is not None:
                    reason = description_reason
                    diagnostic_classes = [
                        _scope_diagnostic(_normalized(str(line["text"])))[1]
                        for line in description_cluster
                    ]
                    if "DAMAGED_NATIVE_TEXT" in diagnostic_classes:
                        uncertainty_class = "DAMAGED_NATIVE_TEXT"
                    elif "COMMERCIAL_SCOPE_LANGUAGE" in diagnostic_classes:
                        uncertainty_class = "COMMERCIAL_SCOPE_LANGUAGE"
                scope_lines = unique_lines(
                    (*_nearby_scope_lines(pack, lines), *description_cluster, pack)
                )
                scope_text = "\n".join(
                    _normalized(str(line["text"])) for line in scope_lines
                )
                if _UNSAFE_MARKUP.search(scope_text):
                    reason = "UNSAFE_MARKUP_LIKE_SOURCE_TEXT"
                    uncertainty_class = "UNSAFE_MARKUP"
                elif _UNSUPPORTED_SCOPE.search(scope_text):
                    reason = "UNSUPPORTED_CLASS_OR_COMPONENT_LAYOUT"
                    uncertainty_class = "UNSUPPORTED_LAYOUT_LANGUAGE"
            if not tiers and reason is None:
                if barrier is not None:
                    reason = "INTERVENING_SCOPE_LINE_BEFORE_PRICE_LADDER"
                    uncertainty_class = (
                        _scope_diagnostic(_normalized(str(barrier["text"])))[1]
                    )
                else:
                    reason = "PRICE_LADDER_MISSING_OR_NONCONTIGUOUS"
                    uncertainty_class = "PRICE_LADDER_UNRESOLVED"
            elif tiers and later_tiers and reason is None:
                reason = "INTERVENING_SCOPE_LINE_WITHIN_PRICE_LADDER"
                assert barrier is not None
                uncertainty_class = _scope_diagnostic(
                    _normalized(str(barrier["text"]))
                )[1]

            if reason is not None:
                scoped_barrier = (
                    (barrier,)
                    if (not tiers or later_tiers)
                    and barrier is not None
                    and barrier["column"] == pack["column"]
                    else ()
                )
                partial_tier_lines = (*tiers, *later_tiers)
                partial_tiers = _tier_records(
                    partial_tier_lines,
                    source_sha256,
                    page_number,
                    kind,
                    groups,
                )
                add_candidate_block(
                    reason,
                    (*description_cluster, pack, *tiers, *scoped_barrier, *later_tiers),
                    pack=pack,
                    description_line=description_line,
                    scoped_notes=scoped_notes,
                    printed_code=_value(groups["code"], groups["code"]),
                    package=package,
                    tier_records=partial_tiers,
                    uncertainty_class=uncertainty_class,
                )
                continue

            assert description_line is not None
            source_lines = unique_lines((*description_cluster, pack, *tiers))
            conflicts = [
                str(line["line_id"])
                for line in source_lines
                if str(line["line_id"]) in assignments
            ]
            if conflicts:
                add_quarantine(
                    "OVERLAPPING_SOURCE_SCOPE",
                    source_lines,
                    anchor=pack,
                    text=str(pack["text"]),
                    conflicts=conflicts,
                    scope_level="OCCURRENCE_BLOCK",
                    uncertainty_class="OVERLAPPING_SCOPE",
                )
                continue

            tier_records = _tier_records(tiers, source_sha256, page_number, kind, groups)
            occurrence_id = _occurrence_identifier(
                source_sha256,
                page_number,
                [str(line["line_id"]) for line in source_lines],
            )
            for source_line in source_lines:
                assignments[str(source_line["line_id"])] = "SUPPORTED_OCCURRENCE"

            adjacent_refs: list[dict[str, object]] = []
            pre_scope = [
                line
                for line in _nearby_scope_lines(pack, lines)
                if str(line["line_id"]) not in assignments
                and line["column"] == pack["column"]
                and (
                    _has_damaged_text(str(line["text"]))
                    or _POTENTIAL_COMMERCIAL_SCOPE.search(_normalized(str(line["text"])))
                )
            ]
            adjacent_candidates: list[Mapping[str, object]] = list(pre_scope)
            if (
                barrier is not None
                and barrier["column"] == pack["column"]
                and str(barrier["line_id"]) not in protected_description_ids
                and str(barrier["line_id"]) not in assignments
            ):
                adjacent_candidates.append(barrier)
            for candidate in unique_lines(adjacent_candidates):
                diagnostic_reason, diagnostic_class = _scope_diagnostic(
                    _normalized(str(candidate["text"]))
                )
                record = add_quarantine(
                    diagnostic_reason,
                    (candidate,),
                    anchor=candidate,
                    text=str(candidate["text"]),
                    scope_level="OCCURRENCE_ADJACENT",
                    uncertainty_class=diagnostic_class,
                )
                adjacent_refs.append(
                    {
                        "quarantine_id": record["quarantine_id"],
                        "scope_level": record["scope_level"],
                        "reason": record["reason"],
                        "source_line_ids": record["source_line_ids"],
                    }
                )

            if adjacent_refs and scoped_notes.get("state") == "ABSENT":
                adjacent_line_ids = [
                    line_id
                    for ref in adjacent_refs
                    for line_id in ref["source_line_ids"]
                ]
                scoped_notes = {
                    "state": "UNRESOLVED",
                    "reason": "ADJACENT_SCOPE_EVIDENCE_COULD_INCLUDE_NOTES_OR_RESTRICTIONS",
                    "candidate_line_ids": adjacent_line_ids,
                    "candidates": [
                        _line_evidence(lines_by_id[str(line_id)])
                        for line_id in adjacent_line_ids
                    ],
                }

            description_raw = str(description_line["text"])
            occurrence = {
                "source_occurrence_id": occurrence_id,
                "identity_class": "PRINTED_SOURCE_OCCURRENCE_NOT_OPERATIONAL_OFFER",
                "evidence_status": (
                    "PARTIAL_REVIEW_REQUIRED"
                    if adjacent_refs
                    else "SUPPORTED_SOURCE_EVIDENCE_NOT_OPERATIONAL_AUTHORITY"
                ),
                "source_offer_class": kind,
                "supplier_format": SUPPORTED_FORMAT,
                "document_id": f"sha256:{source_sha256}",
                "physical_page": page_number,
                "printed_page": footer["printed_page"],
                "column_context": pack["column"],
                "section_context": {
                    "state": "UNRESOLVED_NATIVE_TEXT",
                    "reason": "PAGE_LINES_RETAINED; NO_OCR_OR_HEADING_INFERENCE",
                },
                "description": _value(description_raw, _normalized(description_raw)),
                "scoped_notes": scoped_notes,
                "printed_code": _value(groups["code"], groups["code"]),
                "package": package,
                "pack_line": _value(str(pack["text"]), pack_text),
                "tiers": tier_records,
                "page_term_ids": [footer["term"]["source_term_id"]],
                "source_edition": _absent("NOT_VISIBLE_IN_SELECTED_PAGE_NATIVE_TEXT"),
                "literal_validity": _absent("NO_ITEM_VALIDITY_LABEL_PRINTED"),
                "price_projection_27_field": _absent(
                    "CANONICAL_VARIANT_AND_APPROVAL_REQUIREMENTS_NOT_ESTABLISHED"
                ),
                "source_line_ids": [str(line["line_id"]) for line in source_lines],
                "adjacent_unresolved_line_ids": [
                    line_id
                    for ref in adjacent_refs
                    for line_id in ref["source_line_ids"]
                ],
                "unresolved_evidence_refs": adjacent_refs,
            }
            occurrences.append(occurrence)
            page_occurrences.append(occurrence)
            if len(occurrences) > MAX_OCCURRENCES:
                raise ExtractionError(
                    "OCCURRENCE_LIMIT",
                    "parsed occurrence count exceeded the bounded limit",
                )

        for line in lines:
            line_id = str(line["line_id"])
            if line_id in assignments:
                continue
            text = _normalized(str(line["text"]))
            reason, uncertainty_class = _scope_diagnostic(text)
            add_quarantine(
                reason,
                (line,),
                anchor=line,
                text=str(line["text"]),
                scope_level="PAGE" if line["column"] == "PAGE_WIDE" else "COLUMN",
                uncertainty_class=uncertainty_class,
            )

        if not page_occurrences:
            add_quarantine(
                "NO_SUPPORTED_NATIVE_PRICE_BLOCK_ON_SELECTED_PAGE",
                (),
                anchor=None,
                text="",
                scope_level="PAGE",
                uncertainty_class="NO_SUPPORTED_BLOCK",
            )

        expected_line_ids = [str(line["line_id"]) for line in lines]
        if set(assignments) != set(expected_line_ids) or len(assignments) != len(expected_line_ids):
            raise ExtractionError(
                "COVERAGE_PARTITION_INVALID",
                "every extracted source line must have exactly one coverage claim",
            )
        assignment_rows = [
            {"line_id": line["line_id"], "category": assignments[str(line["line_id"])]}
            for line in lines
        ]
        unresolved_scope_line_ids = [
            row["line_id"]
            for row in assignment_rows
            if row["category"] == "EXPLICIT_QUARANTINE"
        ]
        unassigned_price_like = [
            line_id
            for line_id in unresolved_scope_line_ids
            if "$" in _normalized(str(lines_by_id[str(line_id)]["text"]))
            or _TIER_LIKE.search(_normalized(str(lines_by_id[str(line_id)]["text"])))
        ]
        scope_refs = [
            {
                "quarantine_id": item["quarantine_id"],
                "scope_level": item["scope_level"],
                "uncertainty_class": item["uncertainty_class"],
                "reason": item["reason"],
                "source_line_ids": item["source_line_ids"],
                **(
                    {"candidate_block_id": item["candidate_block_id"]}
                    if "candidate_block_id" in item
                    else {}
                ),
            }
            for item in quarantines
        ]
        occurrence_refs = [
            {
                "source_occurrence_id": occurrence["source_occurrence_id"],
                "evidence_status": occurrence["evidence_status"],
                "unresolved_evidence_refs": occurrence["unresolved_evidence_refs"],
            }
            for occurrence in page_occurrences
        ]
        broader_refs = [
            ref for ref in scope_refs if ref["scope_level"] in {"COLUMN", "PAGE"}
        ]
        page_outputs.append(
            {
                **page,
                "printed_page": footer["printed_page"],
                "recognition": "WRIGHT_BOTTOM_ALIGNED_THREE_PART_FOOTER_V1",
                "footer_source_line_ids": footer["source_line_ids"],
                "page_terms": [footer["term"]],
                "coverage_status": (
                    "UNVERIFIED_NO_SUPPORTED_BLOCK"
                    if not page_occurrences
                    else "PARTIAL_REVIEW_REQUIRED"
                    if quarantines
                    else "SUPPORTED_LINES_PARTITIONED_NOT_APPROVED"
                ),
                "coverage_partition": assignment_rows,
                "supported_occurrence_ids": [
                    occurrence["source_occurrence_id"] for occurrence in page_occurrences
                ],
                "quarantines": quarantines,
                "unresolved_price_like_line_ids": unassigned_price_like,
                "unresolved_scope_line_ids": unresolved_scope_line_ids,
                "unresolved_scope_refs": scope_refs,
                "occurrence_unresolved_refs": occurrence_refs,
                "broader_scope_unresolved_refs": broader_refs,
            }
        )
        all_quarantines.extend(quarantines)
        all_page_terms.append(footer["term"])

    occurrence_ids = [str(item["source_occurrence_id"]) for item in occurrences]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise ExtractionError("OCCURRENCE_ID_COLLISION", "source occurrence IDs are not unique")
    tier_ids = [
        str(tier["source_tier_id"])
        for occurrence in occurrences
        for tier in occurrence["tiers"]
    ]
    if len(tier_ids) != len(set(tier_ids)):
        raise ExtractionError("TIER_ID_COLLISION", "source tier IDs are not unique")
    term_ids = [str(term["source_term_id"]) for term in all_page_terms]
    if len(term_ids) != len(set(term_ids)):
        raise ExtractionError("TERM_ID_COLLISION", "source term IDs are not unique")
    quarantine_ids = [str(item["quarantine_id"]) for item in all_quarantines]
    if len(quarantine_ids) != len(set(quarantine_ids)):
        raise ExtractionError("QUARANTINE_ID_COLLISION", "quarantine IDs are not unique")

    coverage_pages = [
        {
            "physical_page": page["physical_page"],
            "printed_page": page["printed_page"],
            "coverage_status": page["coverage_status"],
            "supported_occurrence_ids": page["supported_occurrence_ids"],
            "quarantine_ids": [item["quarantine_id"] for item in page["quarantines"]],
            "coverage_partition": page["coverage_partition"],
            "unresolved_price_like_line_ids": page["unresolved_price_like_line_ids"],
            "unresolved_scope_line_ids": page["unresolved_scope_line_ids"],
            "unresolved_scope_refs": page["unresolved_scope_refs"],
            "occurrence_unresolved_refs": page["occurrence_unresolved_refs"],
            "broader_scope_unresolved_refs": page["broader_scope_unresolved_refs"],
        }
        for page in page_outputs
    ]
    coverage = {
        "format": COVERAGE_FORMAT,
        "label": LABEL,
        "supplier_format": SUPPORTED_FORMAT,
        "selected_pages": [page["physical_page"] for page in page_outputs],
        "page_count": len(page_outputs),
        "supported_occurrence_count": len(occurrences),
        "quarantine_count": len(all_quarantines),
        "unresolved_price_like_line_count": sum(
            len(page["unresolved_price_like_line_ids"]) for page in page_outputs
        ),
        "unresolved_scope_line_count": sum(
            len(page["unresolved_scope_line_ids"]) for page in page_outputs
        ),
        "pages": coverage_pages,
        "coverage_statement": (
            "EVERY_EXTRACTED_LINE_PARTITIONED; UNVERIFIED_OR QUARANTINED "
            "IDENTITY, NOTE, OR COMMERCIAL SCOPE IS NEVER AN ALL-CLEAR"
        ),
    }
    extraction = {
        "format": FORMAT,
        "label": LABEL,
        "status": "REVIEW_ONLY_WITH_EXPLICIT_COVERAGE",
        "supplier_format": SUPPORTED_FORMAT,
        "source_kind": SOURCE_KIND,
        "authority_effects": dict(ZERO_AUTHORITY_EFFECTS),
        "supported_layouts": [
            "WRIGHT_REGULAR_BOTTLE_LADDER_V1",
            "WRIGHT_RETAIL_MULTIPACK_LADDER_V1",
        ],
        "unsupported_layouts": [
            "GIFT",
            "SPECIAL",
            "ASSORTED",
            "FIXED_COMBO",
            "COMPONENT_ALLOCATION",
            "CODELESS_OR_AMBIGUOUS_BLOCK",
        ],
        "rounding_policy": "PRESERVE_PRINTED_NO_TOLERANCE_NO_SUBSTITUTION",
        "field_state_contract": {
            "ABSENT": "VALUE_NOT_ESTABLISHED_BY_THE_SUPPORTED_SOURCE_GRAMMAR",
            "EXPLICIT_NULL": "SERIALIZABLE_ONLY_WHEN_LITERAL_NULL_IS_PRESENT; NEVER_INFERRED",
            "BLANK": "VISIBLE_FIELD_POSITION_WITH_NO_VALUE",
            "UNRESOLVED": "SOURCE_EVIDENCE_EXISTS_BUT_POSITIVE_VALUE_OR_SCOPE_IS_NOT_ESTABLISHED",
            "VALUE": "SOURCE_VALUE_WITH_EXACT_RAW_AND_SEPARATE_NORMALIZED_OR_TYPED_VALUE",
        },
        "source_occurrences": occurrences,
        "page_terms": all_page_terms,
        "quarantines": all_quarantines,
        "pages": page_outputs,
    }
    return extraction, coverage


def _tool_manifest(tool: ToolIdentity) -> dict[str, str]:
    return {"engine": tool.name, "version": tool.version, "binary_sha256": tool.sha256}


def extract_document(
    *,
    input_path: Path,
    supplier_format: str,
    pages: Sequence[int],
    pdfinfo_path: Path,
    pdftotext_path: Path,
    expected_sha256: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Snapshot, validate, extract, parse, and return deterministic evidence."""

    if supplier_format != SUPPORTED_FORMAT:
        raise ExtractionError("FORMAT_UNSUPPORTED", "only WRIGHT_V1 is implemented")
    source = _validated_input_path(input_path)
    if tuple(pages) != tuple(sorted(set(pages))) or not pages or len(pages) > MAX_SELECTED_PAGES:
        raise ExtractionError("PAGE_SELECTION_INVALID", "pages must be sorted, unique, nonempty, and bounded")
    with tempfile.TemporaryDirectory(prefix="buffalo-pdf-extract-") as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        snapshot = root / "source.pdf"
        source_sha256, source_bytes = _copy_input_snapshot(source, snapshot)
        with snapshot.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ExtractionError("PDF_MAGIC_INVALID", "input does not begin with the PDF signature")
        if expected_sha256 is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
                raise ExtractionError("EXPECTED_SHA256_INVALID", "expected SHA-256 must be lowercase hex")
            if source_sha256 != expected_sha256:
                raise ExtractionError("SOURCE_SHA256_MISMATCH", "input bytes differ from the expected SHA-256")
        pdfinfo_tool = _validated_tool(pdfinfo_path, "pdfinfo", cwd=root)
        pdftotext_tool = _validated_tool(pdftotext_path, "pdftotext", cwd=root)
        info_result = _run_bounded(
            (pdfinfo_tool.path.as_posix(), snapshot.as_posix()),
            cwd=root,
            timeout_seconds=PDFINFO_TIMEOUT_SECONDS,
            stdout_limit=MAX_INFO_BYTES,
            stderr_limit=MAX_STDERR_BYTES,
        )
        info = _parse_pdfinfo(info_result.stdout)
        if pages[-1] > int(info["physical_page_count"]):
            raise ExtractionError("PAGE_SELECTION_INVALID", "selected page exceeds the physical page count")
        tsv_parts: list[bytes] = []
        for start, end in _contiguous_ranges(pages):
            result = _run_bounded(
                (
                    pdftotext_tool.path.as_posix(),
                    "-tsv",
                    "-r",
                    "72",
                    "-enc",
                    "UTF-8",
                    "-f",
                    str(start),
                    "-l",
                    str(end),
                    "-nopgbrk",
                    snapshot.as_posix(),
                    "-",
                ),
                cwd=root,
                timeout_seconds=PDFTOTEXT_TIMEOUT_SECONDS,
                stdout_limit=MAX_TSV_BYTES,
                stderr_limit=MAX_STDERR_BYTES,
            )
            if sum(len(part) for part in tsv_parts) + len(result.stdout) > MAX_TSV_BYTES:
                raise ExtractionError("TOOL_OUTPUT_LIMIT", "combined TSV exceeded the bounded byte limit")
            lines = result.stdout.splitlines(keepends=True)
            if not lines:
                raise ExtractionError("TSV_INVALID", "pdftotext returned empty TSV")
            tsv_parts.append(b"".join(lines if not tsv_parts else lines[1:]))
        parsed_pages = parse_poppler_tsv(b"".join(tsv_parts), source_sha256, pages)
        extraction, coverage = parse_wright_pages(parsed_pages, source_sha256)
        extraction["source"] = {
            "document_id": f"sha256:{source_sha256}",
            "sha256": source_sha256,
            "bytes": source_bytes,
            "observed_filename_alias": source.name,
            "pdf_version": info["pdf_version"],
            "physical_page_count": info["physical_page_count"],
            "selected_pages": list(pages),
            "edition": _absent("NOT_INFERRED_FROM_FILENAME_OR_UPLOAD_TIME"),
        }
        extraction["extractor_provenance"] = {
            "pdfinfo": _tool_manifest(pdfinfo_tool),
            "pdftotext": _tool_manifest(pdftotext_tool),
            "argv_contract": "FIXED_POPPLER_TSV_72DPI_UTF8_SELECTED_CONTIGUOUS_RANGES_V1",
        }
        extraction["limits"] = {
            "input_bytes": MAX_INPUT_BYTES,
            "physical_pages": MAX_PHYSICAL_PAGES,
            "selected_pages": MAX_SELECTED_PAGES,
            "tsv_bytes": MAX_TSV_BYTES,
            "words": MAX_WORDS,
            "lines": MAX_LINES,
            "occurrences": MAX_OCCURRENCES,
            "tiers_per_occurrence": MAX_TIERS,
            "canonical_bytes": MAX_CANONICAL_BYTES,
        }
        coverage["source_sha256"] = source_sha256
        coverage["document_id"] = f"sha256:{source_sha256}"
        return extraction, coverage


def _bundle_bytes(extraction: Mapping[str, object], coverage: Mapping[str, object]) -> dict[str, bytes]:
    if extraction.get("format") != FORMAT or coverage.get("format") != COVERAGE_FORMAT:
        raise ExtractionError(
            "OUTPUT_FORMAT_MISMATCH",
            "extraction and coverage must use the current explicit V2 output contract",
        )
    if (
        extraction.get("supplier_format") != SUPPORTED_FORMAT
        or coverage.get("supplier_format") != SUPPORTED_FORMAT
    ):
        raise ExtractionError(
            "OUTPUT_FORMAT_MISMATCH",
            "output members do not declare the supported Wright source grammar",
        )
    field_states = extraction.get("field_state_contract")
    if not isinstance(field_states, Mapping) or set(field_states) != {
        "ABSENT",
        "BLANK",
        "EXPLICIT_NULL",
        "UNRESOLVED",
        "VALUE",
    }:
        raise ExtractionError(
            "OUTPUT_FORMAT_MISMATCH",
            "extraction does not declare the complete V2 field-state contract",
        )
    extraction_bytes = _canonical_bytes(extraction)
    coverage_bytes = _canonical_bytes(coverage)
    if len(extraction_bytes) > MAX_CANONICAL_BYTES or len(coverage_bytes) > MAX_CANONICAL_BYTES:
        raise ExtractionError("CANONICAL_OUTPUT_LIMIT", "canonical output exceeded the bounded byte limit")
    manifest = {
        "format": MANIFEST_FORMAT,
        "label": LABEL,
        "self_excluded": True,
        "files": [
            {"path": "coverage.json", "bytes": len(coverage_bytes), "sha256": _sha256(coverage_bytes)},
            {"path": "extraction.json", "bytes": len(extraction_bytes), "sha256": _sha256(extraction_bytes)},
        ],
    }
    return {
        "extraction.json": extraction_bytes,
        "coverage.json": coverage_bytes,
        "SHA256SUMS.json": _canonical_bytes(manifest),
    }


def _read_exact(path: Path, expected: bytes) -> bool:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != len(expected)
        ):
            return False
        with path.open("rb") as handle:
            return handle.read(len(expected) + 1) == expected
    except OSError:
        return False


def _existing_bundle_matches(output: Path, expected: Mapping[str, bytes]) -> bool:
    try:
        metadata = output.lstat()
    except FileNotFoundError:
        return False
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ExtractionError("OUTPUT_TYPE_MISMATCH", "output must be a real directory")
    try:
        names = {entry.name for entry in output.iterdir()}
    except OSError as exc:
        raise ExtractionError("OUTPUT_READ_FAILED", f"cannot inspect output ({type(exc).__name__})") from exc
    return names == set(expected) and all(_read_exact(output / name, data) for name, data in expected.items())


def _write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_noreplace(staged: Path, output: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "atomic no-replace directory publish is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(staged), -100, os.fsencode(output), 1)
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), output)


def write_bundle(output_path: Path, extraction: Mapping[str, object], coverage: Mapping[str, object]) -> dict[str, object]:
    destination = _validated_output_path(output_path)
    expected = _bundle_bytes(extraction, coverage)
    if destination.exists() or destination.is_symlink():
        if not _existing_bundle_matches(destination, expected):
            raise ExtractionError("OUTPUT_DRIFT", "existing output differs and was not overwritten")
        return {"idempotent_replay": True, "manifest_sha256": _sha256(expected["SHA256SUMS.json"])}
    staged = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    os.chmod(staged, 0o700)
    written: list[str] = []
    published = False
    try:
        for name in _OUTPUT_FILES:
            written.append(name)
            _write_exclusive(staged / name, expected[name])
        _fsync_directory(staged)
        try:
            _publish_noreplace(staged, destination)
            published = True
        except OSError:
            if not destination.exists() or not _existing_bundle_matches(destination, expected):
                raise ExtractionError("OUTPUT_DRIFT", "concurrent output differs and was not overwritten") from None
        _fsync_directory(destination.parent)
    finally:
        if not published:
            for name in reversed(written):
                try:
                    (staged / name).unlink()
                except FileNotFoundError:
                    pass
            try:
                staged.rmdir()
            except FileNotFoundError:
                pass
    return {"idempotent_replay": not published, "manifest_sha256": _sha256(expected["SHA256SUMS.json"])}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="absolute local original PDF path")
    parser.add_argument("--format", required=True, choices=(SUPPORTED_FORMAT,))
    parser.add_argument("--output", required=True, type=Path, help="absolute new review-output directory")
    parser.add_argument("--pages", required=True, help="explicit physical pages/ranges, e.g. 4,6-7")
    parser.add_argument("--expected-sha256", help="optional exact lowercase source SHA-256")
    parser.add_argument("--pdfinfo", required=True, type=Path, help="absolute pdfinfo executable")
    parser.add_argument("--pdftotext", required=True, type=Path, help="absolute pdftotext executable")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        pages = _parse_page_selection(args.pages)
        extraction, coverage = extract_document(
            input_path=args.input,
            supplier_format=args.format,
            pages=pages,
            pdfinfo_path=args.pdfinfo,
            pdftotext_path=args.pdftotext,
            expected_sha256=args.expected_sha256,
        )
        result = write_bundle(args.output, extraction, coverage)
    except ExtractionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError:
        print("LOCAL_IO_FAILED: bounded local file operation failed", file=sys.stderr)
        return 2
    except (DecimalException, UnicodeError, ValueError):
        print("SOURCE_DATA_INVALID: untrusted source data was outside the bounded grammar", file=sys.stderr)
        return 2
    summary = {
        "format": FORMAT,
        "label": LABEL,
        "source_sha256": extraction["source"]["sha256"],
        "selected_page_count": len(pages),
        "supported_occurrence_count": len(extraction["source_occurrences"]),
        "quarantine_count": len(extraction["quarantines"]),
        "manifest_sha256": result["manifest_sha256"],
        "idempotent_replay": result["idempotent_replay"],
        "authority_effects": dict(ZERO_AUTHORITY_EFFECTS),
    }
    try:
        print(_canonical_bytes(summary).decode("utf-8"), end="")
    except OSError:
        print("LOCAL_IO_FAILED: bounded output emission failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
