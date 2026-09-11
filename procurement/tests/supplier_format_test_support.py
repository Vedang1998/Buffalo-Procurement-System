"""Pure support for fabricated future supplier-format conformance cases.

This module deliberately does not extract PDFs or decide mappings, offers,
prices, or supplier terms.  It only loads the public synthetic corpus strictly
and compares independently supplied observations with presence-aware expected
values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class SupplierFormatCorpusError(ValueError):
    """Raised when the retained synthetic corpus violates its test contract."""


EXPECTED_CASE_IDS = tuple(f"SF-{number:02d}" for number in range(1, 9))
SOURCE_KIND = "FABRICATED_TEST_SCENARIO"
REQUIRED_COVERAGE_LABELS = (
    "INPUT_EXPECTED_EXAMPLE_PREPARED",
    "HELPER_VALIDATION_EXECUTED",
    "EXTRACTOR_NOT_IMPLEMENTED_NOT_TESTED",
    "GENUINE_LATER_EDITION_NOT_PROVEN",
)
ZERO_AUTHORITY_EFFECTS = {
    "mapping_approvals": 0,
    "selected_offers": 0,
    "price_activations": 0,
    "shopify_writes": 0,
    "supplier_contacts": 0,
    "orders": 0,
}
_REQUIRED_KEYS = {
    "case_id",
    "synthetic",
    "source_kind",
    "supplier_format",
    "scenario",
    "expected",
    "authority_effects",
    "coverage_labels",
}
_OPTIONAL_KEYS = {"price_projection"}


def load_synthetic_corpus(path: Path) -> tuple[dict[str, Any], ...]:
    """Load and structurally validate the exact eight-case JSONL corpus."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SupplierFormatCorpusError(f"cannot read corpus: {type(exc).__name__}") from exc
    if not raw or not raw.endswith(b"\n"):
        raise SupplierFormatCorpusError("corpus must be nonempty and end in one LF")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SupplierFormatCorpusError("corpus must be UTF-8") from exc

    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise SupplierFormatCorpusError(f"blank JSONL line {line_number}")
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SupplierFormatCorpusError(f"invalid JSON on line {line_number}") from exc
        if not isinstance(case, dict):
            raise SupplierFormatCorpusError(f"line {line_number} is not an object")
        unknown = set(case) - _REQUIRED_KEYS - _OPTIONAL_KEYS
        missing = _REQUIRED_KEYS - set(case)
        if missing or unknown:
            raise SupplierFormatCorpusError(
                f"line {line_number} keys differ: missing={sorted(missing)!r} "
                f"unknown={sorted(unknown)!r}"
            )
        if case["synthetic"] is not True or case["source_kind"] != SOURCE_KIND:
            raise SupplierFormatCorpusError(
                f"line {line_number} is not explicitly fabricated"
            )
        if not isinstance(case["supplier_format"], str) or not case["supplier_format"]:
            raise SupplierFormatCorpusError(f"line {line_number} has no supplier format")
        if not isinstance(case["scenario"], dict) or not isinstance(case["expected"], dict):
            raise SupplierFormatCorpusError(
                f"line {line_number} scenario/expected must be objects"
            )
        if case["authority_effects"] != ZERO_AUTHORITY_EFFECTS:
            raise SupplierFormatCorpusError(
                f"line {line_number} must retain exact zero authority effects"
            )
        if tuple(case["coverage_labels"]) != REQUIRED_COVERAGE_LABELS:
            raise SupplierFormatCorpusError(
                f"line {line_number} coverage labels differ"
            )
        cases.append(case)

    case_ids = tuple(case["case_id"] for case in cases)
    if case_ids != EXPECTED_CASE_IDS:
        raise SupplierFormatCorpusError(
            f"case IDs/order differ: expected={EXPECTED_CASE_IDS!r} actual={case_ids!r}"
        )
    return tuple(cases)


def cases_by_id(cases: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Index already validated cases without interpreting their business facts."""

    return {str(case["case_id"]): case for case in cases}


def presence_aware_differences(
    expected: Any,
    observed: Any,
    *,
    path: str = "$",
) -> tuple[str, ...]:
    """Return deterministic exact differences, preserving missing/null/zero/type."""

    differences: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return (f"{path}: expected object; observed {type(observed).__name__}",)
        for key in sorted(expected):
            child_path = f"{path}.{key}"
            if key not in observed:
                differences.append(f"{child_path}: missing")
            else:
                differences.extend(
                    presence_aware_differences(
                        expected[key], observed[key], path=child_path
                    )
                )
        for key in sorted(set(observed) - set(expected)):
            differences.append(f"{path}.{key}: unexpected")
        return tuple(differences)

    if isinstance(expected, list):
        if not isinstance(observed, list):
            return (f"{path}: expected array; observed {type(observed).__name__}",)
        if len(expected) != len(observed):
            differences.append(
                f"{path}: expected length {len(expected)}; observed {len(observed)}"
            )
        for index, (expected_item, observed_item) in enumerate(
            zip(expected, observed, strict=False)
        ):
            differences.extend(
                presence_aware_differences(
                    expected_item, observed_item, path=f"{path}[{index}]"
                )
            )
        return tuple(differences)

    if type(expected) is not type(observed):
        return (
            f"{path}: expected {type(expected).__name__} {expected!r}; "
            f"observed {type(observed).__name__} {observed!r}",
        )
    if expected != observed:
        return (f"{path}: expected {expected!r}; observed {observed!r}",)
    return ()


def price_projection_differences(
    case: Mapping[str, Any], required_headers: Sequence[str]
) -> tuple[str, ...]:
    """Check only the shape of an optional existing-contract projection."""

    if "price_projection" not in case:
        return ()
    projection = case["price_projection"]
    if not isinstance(projection, dict):
        return ("$.price_projection: expected object",)
    expected_keys = tuple(required_headers)
    actual_keys = tuple(projection)
    if actual_keys != expected_keys:
        return (
            "$.price_projection: keys/order differ: "
            f"expected={expected_keys!r} actual={actual_keys!r}",
        )
    return ()
