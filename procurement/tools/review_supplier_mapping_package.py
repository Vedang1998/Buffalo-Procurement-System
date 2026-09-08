#!/usr/bin/env python3
"""Validate an offline supplier-mapping review package and render evidence.

Dry-run is the default: the canonical report is written to stdout and no
report files are created.  ``--output`` is the only write boundary.  It
publishes a new report directory atomically, or accepts an existing directory
only when every byte already matches the deterministic result.

This tool deliberately imports no database, Shopify, promotion, readiness,
inventory, forecast, recommendation, or purchase-order code.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence


# Importing the review libraries must not create bytecode files during a
# nominally read-only dry-run.  The main script itself is not cached by Python.
sys.dont_write_bytecode = True
PROCUREMENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROCUREMENT_ROOT / "src"))

from procurement_os import supplier_mapping_review as mapping_review  # noqa: E402
from procurement_os.supplier_review_package import (  # noqa: E402
    REVIEW_LABEL,
    ReviewPackageError,
    read_review_package,
)


REPORT_JSON = "report.json"
REPORT_HTML = "report.html"
CHECKSUM_MANIFEST = "SHA256SUMS.json"
REPORT_MANIFEST_FORMAT = "BUFFALO_REVIEW_REPORT_SHA256_V1"
_REPORT_FILES = (REPORT_JSON, REPORT_HTML, CHECKSUM_MANIFEST)


class ReportOutputError(RuntimeError):
    """A fail-closed local report-publication error."""

    def __init__(self, code: str, message: str, *, path: Path | None = None):
        self.code = code
        self.path = path
        suffix = f" ({path})" if path is not None else ""
        super().__init__(f"{code}: {message}{suffix}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an offline supplier-mapping review package. The default "
            "is a no-write dry-run; all results remain review-only."
        )
    )
    parser.add_argument(
        "package",
        type=Path,
        help="Versioned review-package directory or ZIP to inspect.",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        metavar="PREVIOUS_PACKAGE",
        help=(
            "Optionally compare an earlier complete review package with the "
            "positional (current) package. Omissions are not retirements."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        metavar="EXACT_BASELINE",
        help=(
            "Exact baseline directory or ZIP required by a declared patch. "
            "Missing prerequisites remain BASELINE_REQUIRED."
        ),
    )
    parser.add_argument(
        "--prior-delta",
        type=Path,
        metavar="V4_1_DELTA",
        help=(
            "Exact preceding V4.1 delta required to validate a V5 locator "
            "overlay. Missing lineage remains explicitly unavailable."
        ),
    )
    parser.add_argument(
        "--compare-prior-delta",
        type=Path,
        metavar="PREVIOUS_V4_1_DELTA",
        help=(
            "Exact preceding V4.1 delta for --compare when that package is "
            "a V5 diagnostic package."
        ),
    )
    parser.add_argument(
        "--external-evidence-root",
        type=Path,
        metavar="DIRECTORY",
        help=(
            "Caller-authorized directory containing V5 workbook/developer "
            "artifacts named by the package. Producer scratch paths are never followed."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicitly select the default no-write mode.",
    )
    mode.add_argument(
        "--output",
        type=Path,
        metavar="DIRECTORY",
        help=(
            "Explicitly publish report.json, report.html, and SHA256SUMS.json "
            "to a new atomic local directory."
        ),
    )
    return parser


def _canonical_report_bytes(report: Mapping[str, Any]) -> bytes:
    """Call the review library's canonical serializer across its stable alias."""

    serializer = getattr(mapping_review, "canonical_report_bytes", None)
    if serializer is None:
        serializer = getattr(mapping_review, "canonical_report_json", None)
    if serializer is None:
        raise RuntimeError("supplier review canonical serializer is unavailable")
    rendered = serializer(report)
    if isinstance(rendered, str):
        data = rendered.encode("utf-8")
    elif isinstance(rendered, bytes):
        data = rendered
    else:
        raise TypeError("canonical report serializer must return str or bytes")
    # Report artifacts and stdout are text files with one terminal newline.
    return data.rstrip(b"\n") + b"\n"


def _html_report_bytes(report: Mapping[str, Any]) -> bytes:
    rendered = mapping_review.render_review_html(report)
    if not isinstance(rendered, str):
        raise TypeError("HTML report renderer must return str")
    return rendered.rstrip("\n").encode("utf-8") + b"\n"


def _manifest_bytes(report_json: bytes, report_html: bytes) -> bytes:
    records = []
    for name, data in sorted(
        ((REPORT_JSON, report_json), (REPORT_HTML, report_html)),
        key=lambda item: item[0],
    ):
        records.append(
            {
                "bytes": len(data),
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    document = {
        "files": records,
        "format": REPORT_MANIFEST_FORMAT,
        "label": REVIEW_LABEL,
    }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _expected_bundle(report_json: bytes, report_html: bytes) -> dict[str, bytes]:
    return {
        REPORT_JSON: report_json,
        REPORT_HTML: report_html,
        CHECKSUM_MANIFEST: _manifest_bytes(report_json, report_html),
    }


def _resolved_output_path(output: str | Path) -> Path:
    requested = Path(output)
    if requested.name in {"", ".", ".."}:
        raise ReportOutputError(
            "INVALID_OUTPUT_PATH", "output must name a report directory", path=requested
        )
    parent = requested.parent
    try:
        if parent.is_symlink() or not parent.is_dir():
            raise ReportOutputError(
                "INVALID_OUTPUT_PARENT",
                "output parent must be an existing directory",
                path=parent,
            )
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise ReportOutputError(
            "INVALID_OUTPUT_PARENT",
            "output parent could not be resolved",
            path=parent,
        ) from exc
    return resolved_parent / requested.name


def _read_existing_exact(path: Path, expected: bytes) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ReportOutputError(
            "OUTPUT_TYPE_MISMATCH", "report member must be a regular file", path=path
        )
    if metadata.st_size != len(expected):
        return False
    try:
        with path.open("rb") as handle:
            observed = handle.read(len(expected) + 1)
    except OSError as exc:
        raise ReportOutputError(
            "OUTPUT_READ_FAILED", "existing report member could not be read", path=path
        ) from exc
    return observed == expected


def _existing_bundle_matches(output: Path, expected: Mapping[str, bytes]) -> bool:
    try:
        metadata = output.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or output.is_symlink():
        raise ReportOutputError(
            "OUTPUT_TYPE_MISMATCH", "output must be a real directory", path=output
        )
    try:
        names = {entry.name for entry in output.iterdir()}
    except OSError as exc:
        raise ReportOutputError(
            "OUTPUT_READ_FAILED", "existing report directory could not be read", path=output
        ) from exc
    if names != set(expected):
        return False
    return all(_read_existing_exact(output / name, data) for name, data in expected.items())


def _write_file_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset:])
            if written <= 0:  # pragma: no cover - defensive OS contract guard
                raise OSError("short write while constructing report")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_directory(staged: Path, output: Path) -> None:
    """Publish a fully synced staged directory without replacing a target."""

    # Linux renameat2 is the only primitive used here because ordinary
    # ``os.rename`` may replace an empty directory created in the race window.
    # Fail closed on platforms without a no-replace rename primitive.
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "atomic no-replace directory publish is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    result = renameat2(
        at_fdcwd,
        os.fsencode(staged),
        at_fdcwd,
        os.fsencode(output),
        rename_noreplace,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), output)


def _remove_owned_staging(staged: Path, written_names: Sequence[str]) -> None:
    """Remove only files created in our private mkdtemp directory."""

    for name in reversed(tuple(written_names)):
        try:
            (staged / name).unlink()
        except FileNotFoundError:
            pass
    try:
        staged.rmdir()
    except FileNotFoundError:
        pass


def write_report_bundle(
    output: str | Path,
    report_json: bytes | str,
    report_html: bytes | str,
) -> dict[str, Any]:
    """Atomically publish deterministic local review evidence.

    An already-existing exact bundle is an idempotent replay.  Any missing,
    extra, symlinked, or byte-different member is drift and is never replaced.
    """

    json_bytes = report_json.encode("utf-8") if isinstance(report_json, str) else report_json
    html_bytes = report_html.encode("utf-8") if isinstance(report_html, str) else report_html
    if not isinstance(json_bytes, bytes) or not isinstance(html_bytes, bytes):
        raise TypeError("report JSON and HTML must be str or bytes")
    expected = _expected_bundle(json_bytes, html_bytes)
    destination = _resolved_output_path(output)

    if destination.exists() or destination.is_symlink():
        if not _existing_bundle_matches(destination, expected):
            raise ReportOutputError(
                "OUTPUT_DRIFT",
                "existing report bundle does not exactly match this result",
                path=destination,
            )
        return _bundle_result(destination, expected, idempotent_replay=True)

    staged = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.tmp-",
            dir=destination.parent,
        )
    )
    written: list[str] = []
    published = False
    try:
        for name in _REPORT_FILES:
            # Track the name before opening so a short/failed write is also
            # removed from the private staging directory.
            written.append(name)
            _write_file_exclusive(staged / name, expected[name])
        _fsync_directory(staged)
        try:
            _publish_directory(staged, destination)
            published = True
        except OSError as exc:
            # A concurrent invocation may have published first.  Accept only
            # the same exact bytes; otherwise preserve the winning directory
            # and fail closed.
            if not destination.exists() or not _existing_bundle_matches(destination, expected):
                raise ReportOutputError(
                    "OUTPUT_PUBLISH_FAILED",
                    "atomic report directory could not be published",
                    path=destination,
                ) from exc
        if published:
            _fsync_directory(destination.parent)
    finally:
        if not published and staged.exists():
            _remove_owned_staging(staged, written)

    return _bundle_result(destination, expected, idempotent_replay=not published)


def _bundle_result(
    output: Path,
    expected: Mapping[str, bytes],
    *,
    idempotent_replay: bool,
) -> dict[str, Any]:
    return {
        "files": [
            {
                "bytes": len(expected[name]),
                "path": name,
                "sha256": hashlib.sha256(expected[name]).hexdigest(),
            }
            for name in _REPORT_FILES
        ],
        "idempotent_replay": idempotent_replay,
        "label": REVIEW_LABEL,
        "output": str(output),
    }


def _load(
    path: Path,
    *,
    baseline: Path | None = None,
    prior_delta: Path | None = None,
    external_evidence_root: Path | None = None,
) -> Any:
    return read_review_package(
        path,
        baseline_path=baseline,
        prior_delta_path=prior_delta,
        external_evidence_root=external_evidence_root,
    )


def execute(argv: Sequence[str] | None = None) -> dict[str, Any]:
    """Execute the offline review and return its report document."""

    args = _parser().parse_args(argv)
    package = _load(
        args.package,
        baseline=args.baseline,
        prior_delta=args.prior_delta,
        external_evidence_root=args.external_evidence_root,
    )

    comparison: Mapping[str, Any] | None = None
    if args.compare is not None:
        previous = _load(
            args.compare,
            prior_delta=args.compare_prior_delta,
        )
        comparison = mapping_review.compare_review_packages(previous, package)

    report = mapping_review.report_document(package, comparison=comparison)
    if not isinstance(report, dict):
        raise TypeError("supplier review report must be a dictionary")
    if report.get("label") != REVIEW_LABEL:
        raise RuntimeError("supplier review report is missing its review-only label")

    # The actual V5 diagnostic package retains hundreds of megabytes of
    # verified source rows. The report contains only bounded projections and
    # hashes, so release the package before materializing JSON/HTML bytes.
    del package
    if args.compare is not None:
        del previous
    report_json = _canonical_report_bytes(report)
    report_html = _html_report_bytes(report)
    if args.output is not None:
        write_report_bundle(args.output, report_json, report_html)
    return report


def _safe_error(exc: Exception) -> str:
    return (str(exc).replace("\r", " ").replace("\n", " ")[:1000] or type(exc).__name__)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        output = _parser().parse_args(arguments).output
        report = execute(arguments)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {_safe_error(exc)}", file=sys.stderr)
        return 2
    stdout_document: Mapping[str, Any]
    if output is None:
        stdout_document = report
    else:
        stdout_document = {
            "label": REVIEW_LABEL,
            "output": str(output),
            "report_written": True,
            "status": report.get("status"),
        }
    sys.stdout.buffer.write(_canonical_report_bytes(stdout_document))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
