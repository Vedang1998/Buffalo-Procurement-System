"""Fail-closed Phase 4 terminal-disposition controls.

This module owns the frozen terminal artifact contract and, in later sections,
the persistence-only transaction used by disposable PostgreSQL validation.  It
does not rebuild sales, evaluate readiness, contact Shopify, or perform any
procurement/PO action.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Iterable, Mapping

from .catalog import numeric_shopify_id
from .historical_sales import acquire_backfill_transaction_lock
from .historical_sales_manifest import (
    APPROVED_MANIFEST_SHA256,
    APPROVED_RUN_ID,
    AuthorizedManifest,
    EXCLUSION_SOURCE_KEYS,
    OWNER_AUTHORIZATION_ID,
    OWNER_AUTHORIZATION_VERSION,
    classify_existing_decisions,
    load_latest_effective_decisions,
    load_authorized_manifest,
    protected_state_fingerprints,
)
from .sales import HistoricalIdentityIndex, SalesSourceRow


TERMINAL_MANIFEST_SHA256 = (
    "fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff"
)
MAP_TARGET_FLOW_SHA256 = (
    "5d6832cea3df7a4f45d31d7e7a8100409ddc078936095fe3c775ce862a1f64a6"
)
ORIGINAL_EXCLUSION_REASON = "PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION"
TERMINAL_EXCLUSION_REASON = (
    "HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW"
)
TERMINAL_ARTIFACT_VERSION = "PHASE4_TERMINAL_DISPOSITION_V1"
TERMINAL_EVIDENCE_VERSION = "phase4-terminal-disposition-evidence-v1"
FIESTA_CANONICAL_VARIANT_ID = "41193000796235"
AUTHORITY_GIT_SHA = "701548dfacbc35d505f1d726146c268d6e42260d"
TERMINAL_DECISION_SCHEMA_VERSION = "PHASE4_TERMINAL_V1"
TERMINAL_AUTHORITY_VERSION = "PHASE4_TERMINAL_EXCLUSION_AUTHORITY_V1"
TERMINAL_OWNER_AUTHORIZATION = (
    "OWNER_AUTHORIZATION_2026-08-21_PHASE4_TERMINAL_PACKET"
)
HIGH_NOON_TEQUILA_SOURCE_KEYS = frozenset(
    {
        "0|3012337|HIGH NOON TEQUILA VARIETY PACK|12OZ",
        "0|3014701|HIGH NOON TEQUILA VARIETY 8 PACK|12OZ",
        "0|3014701|HIGH NOON TEQUILA VARIETY 8 PACK|8PK 12OZ CANS",
    }
)
POPOV_CONTRADICTION_SOURCE_KEYS = frozenset(
    {
        "41178335281227|9000474|POPOV VODKA|1.5L",
        "41178335281227|9000474|POPOV VODKA|1.75L",
    }
)

TERMINAL_HEADERS = (
    "artifact_version",
    "source_identity_key",
    "prior_manifest_row_number",
    "prior_manifest_sha256",
    "prior_disposition",
    "final_disposition",
    "material",
    "source_variant_id",
    "historical_sku",
    "historical_product_title",
    "historical_variant_title",
    "canonical_variant_id",
    "continuity_role",
    "continuity_pair_id",
    "continuity_predecessor_variant_id",
    "continuity_successor_variant_id",
    "continuity_sale_periods_overlap",
    "continuity_gap_days",
    "continuity_evidence",
    "terminal_exclusion_reason_code",
    "raw_row_count",
    "first_sale_date",
    "last_sale_date",
    "net_units",
    "absolute_units",
    "net_sales",
    "absolute_sales",
    "evidence_classification",
    "evidence_version",
    "owner_approval_basis",
    "production_sales_backfill_id",
)


class TerminalValidationError(ValueError):
    """The frozen terminal artifact or an execution control drifted."""


def _optional(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _required(value: str | None, *, field: str, row_number: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise TerminalValidationError(
            f"terminal manifest row {row_number} requires {field}"
        )
    return result


def _integer(
    value: str | None,
    *,
    field: str,
    row_number: int,
    minimum: int = 0,
) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise TerminalValidationError(
            f"terminal manifest row {row_number} has invalid {field}"
        ) from exc
    if result < minimum:
        raise TerminalValidationError(
            f"terminal manifest row {row_number} has invalid {field}"
        )
    return result


def _decimal(value: str | None, *, field: str, row_number: int) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TerminalValidationError(
            f"terminal manifest row {row_number} has invalid {field}"
        ) from exc
    if not result.is_finite():
        raise TerminalValidationError(
            f"terminal manifest row {row_number} has invalid {field}"
        )
    return result


def _boolean(value: str | None, *, field: str, row_number: int) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized not in {"true", "false"}:
        raise TerminalValidationError(
            f"terminal manifest row {row_number} has invalid {field}"
        )
    return normalized == "true"


def _optional_boolean(
    value: str | None, *, field: str, row_number: int
) -> bool | None:
    if value in (None, ""):
        return None
    return _boolean(value, field=field, row_number=row_number)


def _optional_date(value: str | None, *, field: str, row_number: int) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise TerminalValidationError(
            f"terminal manifest row {row_number} has invalid {field}"
        ) from exc


@dataclass(frozen=True)
class TerminalDispositionRow:
    row_number: int
    artifact_version: str
    source_identity_key: str
    prior_manifest_row_number: int
    prior_manifest_sha256: str
    prior_disposition: str
    action: str
    material: bool
    source_variant_id: str | None
    historical_sku: str | None
    historical_product_title: str
    historical_variant_title: str
    canonical_variant_id: str | None
    continuity_role: str | None
    continuity_pair_id: str | None
    continuity_predecessor_variant_id: str | None
    continuity_successor_variant_id: str | None
    continuity_sale_periods_overlap: bool | None
    continuity_gap_days: int | None
    continuity_evidence: str | None
    exclusion_reason_code: str | None
    raw_row_count: int
    first_sale_date: date
    last_sale_date: date
    net_units: Decimal
    absolute_units: Decimal
    net_sales: Decimal
    absolute_sales: Decimal
    evidence_classification: str
    evidence_version: str
    owner_approval_basis: str
    production_sales_backfill_id: str


@dataclass(frozen=True)
class ActionTotals:
    source_keys: int
    raw_rows: int
    net_units: Decimal
    absolute_units: Decimal
    net_sales: Decimal
    absolute_sales: Decimal

    def as_tuple(self) -> tuple[int, int, Decimal, Decimal, Decimal, Decimal]:
        return (
            self.source_keys,
            self.raw_rows,
            self.net_units,
            self.absolute_units,
            self.net_sales,
            self.absolute_sales,
        )


@dataclass(frozen=True)
class TerminalControls:
    by_action: dict[str, ActionTotals]
    supplement_actions: dict[str, int]
    combined_actions: dict[str, int]
    supplement_raw_rows: int
    supplement_net_units: Decimal
    supplement_absolute_units: Decimal
    supplement_net_sales: Decimal
    supplement_absolute_sales: Decimal
    combined_map_raw_rows: int
    distinct_map_targets: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "supplement_actions": dict(self.supplement_actions),
            "combined_actions": dict(self.combined_actions),
            "supplement_raw_rows": self.supplement_raw_rows,
            "supplement_net_units": f"{self.supplement_net_units:.4f}",
            "supplement_absolute_units": f"{self.supplement_absolute_units:.4f}",
            "supplement_net_sales": f"{self.supplement_net_sales:.2f}",
            "supplement_absolute_sales": f"{self.supplement_absolute_sales:.2f}",
            "combined_map_raw_rows": self.combined_map_raw_rows,
            "distinct_map_targets": self.distinct_map_targets,
        }


@dataclass(frozen=True)
class TerminalArtifact:
    raw_bytes: bytes
    sha256: str
    rows: tuple[TerminalDispositionRow, ...]
    original_manifest: AuthorizedManifest
    controls: TerminalControls


@dataclass(frozen=True)
class ExecutionGitIdentity:
    repository_root: Path
    git_sha: str


@dataclass(frozen=True)
class SourceFactsValidation:
    lifecycle: str
    status_counts: dict[str, int]
    baseline_flow: dict[str, str | int]


def _parse_terminal_rows(raw_bytes: bytes) -> tuple[TerminalDispositionRow, ...]:
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TerminalValidationError("terminal manifest must be valid UTF-8") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if tuple(reader.fieldnames or ()) != TERMINAL_HEADERS:
            raise TerminalValidationError(
                "terminal manifest header does not match approved contract"
            )
        rows: list[TerminalDispositionRow] = []
        for row_number, raw in enumerate(reader, start=1):
            if None in raw or set(raw) != set(TERMINAL_HEADERS):
                raise TerminalValidationError(
                    f"terminal manifest row {row_number} does not match approved header"
                )
            final_disposition = _required(
                raw["final_disposition"],
                field="final_disposition",
                row_number=row_number,
            )
            try:
                action = {
                    "RESTORE_HISTORICAL_IDENTITY": "RESTORE",
                    "MAP_TO_CANONICAL": "MAP",
                    "EXCLUDE_UNATTRIBUTABLE": "EXCLUDE",
                }[final_disposition]
            except KeyError as exc:
                raise TerminalValidationError(
                    f"terminal manifest row {row_number} has unknown final disposition"
                ) from exc
            first_sale_date = _optional_date(
                raw["first_sale_date"], field="first_sale_date", row_number=row_number
            )
            last_sale_date = _optional_date(
                raw["last_sale_date"], field="last_sale_date", row_number=row_number
            )
            if first_sale_date is None or last_sale_date is None:
                raise TerminalValidationError(
                    f"terminal manifest row {row_number} requires sale dates"
                )
            gap_text = raw["continuity_gap_days"]
            rows.append(
                TerminalDispositionRow(
                    row_number=row_number,
                    artifact_version=_required(
                        raw["artifact_version"],
                        field="artifact_version",
                        row_number=row_number,
                    ),
                    source_identity_key=_required(
                        raw["source_identity_key"],
                        field="source_identity_key",
                        row_number=row_number,
                    ),
                    prior_manifest_row_number=_integer(
                        raw["prior_manifest_row_number"],
                        field="prior_manifest_row_number",
                        row_number=row_number,
                        minimum=1,
                    ),
                    prior_manifest_sha256=_required(
                        raw["prior_manifest_sha256"],
                        field="prior_manifest_sha256",
                        row_number=row_number,
                    ),
                    prior_disposition=_required(
                        raw["prior_disposition"],
                        field="prior_disposition",
                        row_number=row_number,
                    ),
                    action=action,
                    material=_boolean(
                        raw["material"], field="material", row_number=row_number
                    ),
                    source_variant_id=_optional(raw["source_variant_id"]),
                    historical_sku=_optional(raw["historical_sku"]),
                    historical_product_title=_required(
                        raw["historical_product_title"],
                        field="historical_product_title",
                        row_number=row_number,
                    ),
                    historical_variant_title=str(
                        raw["historical_variant_title"] or ""
                    ),
                    canonical_variant_id=_optional(raw["canonical_variant_id"]),
                    continuity_role=_optional(raw["continuity_role"]),
                    continuity_pair_id=_optional(raw["continuity_pair_id"]),
                    continuity_predecessor_variant_id=_optional(
                        raw["continuity_predecessor_variant_id"]
                    ),
                    continuity_successor_variant_id=_optional(
                        raw["continuity_successor_variant_id"]
                    ),
                    continuity_sale_periods_overlap=_optional_boolean(
                        raw["continuity_sale_periods_overlap"],
                        field="continuity_sale_periods_overlap",
                        row_number=row_number,
                    ),
                    continuity_gap_days=(
                        _integer(
                            gap_text,
                            field="continuity_gap_days",
                            row_number=row_number,
                            minimum=0,
                        )
                        if gap_text not in (None, "")
                        else None
                    ),
                    continuity_evidence=_optional(raw["continuity_evidence"]),
                    exclusion_reason_code=_optional(
                        raw["terminal_exclusion_reason_code"]
                    ),
                    raw_row_count=_integer(
                        raw["raw_row_count"],
                        field="raw_row_count",
                        row_number=row_number,
                        minimum=1,
                    ),
                    first_sale_date=first_sale_date,
                    last_sale_date=last_sale_date,
                    net_units=_decimal(
                        raw["net_units"], field="net_units", row_number=row_number
                    ),
                    absolute_units=_decimal(
                        raw["absolute_units"],
                        field="absolute_units",
                        row_number=row_number,
                    ),
                    net_sales=_decimal(
                        raw["net_sales"], field="net_sales", row_number=row_number
                    ),
                    absolute_sales=_decimal(
                        raw["absolute_sales"],
                        field="absolute_sales",
                        row_number=row_number,
                    ),
                    evidence_classification=_required(
                        raw["evidence_classification"],
                        field="evidence_classification",
                        row_number=row_number,
                    ),
                    evidence_version=_required(
                        raw["evidence_version"],
                        field="evidence_version",
                        row_number=row_number,
                    ),
                    owner_approval_basis=_required(
                        raw["owner_approval_basis"],
                        field="owner_approval_basis",
                        row_number=row_number,
                    ),
                    production_sales_backfill_id=_required(
                        raw["production_sales_backfill_id"],
                        field="production_sales_backfill_id",
                        row_number=row_number,
                    ),
                )
            )
    except csv.Error as exc:
        raise TerminalValidationError("terminal manifest CSV is malformed") from exc
    return tuple(rows)


def _canonical_source_key(row: TerminalDispositionRow) -> str:
    return HistoricalIdentityIndex.source_key(
        SalesSourceRow(
            date(1970, 1, 1),
            row.source_variant_id,
            row.historical_sku,
            row.historical_product_title,
            row.historical_variant_title,
            Decimal("0"),
            Decimal("0"),
        )
    )


def _action_totals(
    rows: Iterable[TerminalDispositionRow], action: str
) -> ActionTotals:
    selected = tuple(row for row in rows if row.action == action)
    return ActionTotals(
        source_keys=len(selected),
        raw_rows=sum(row.raw_row_count for row in selected),
        net_units=sum((row.net_units for row in selected), Decimal("0.0000")),
        absolute_units=sum(
            (row.absolute_units for row in selected), Decimal("0.0000")
        ),
        net_sales=sum((row.net_sales for row in selected), Decimal("0.00")),
        absolute_sales=sum(
            (row.absolute_sales for row in selected), Decimal("0.00")
        ),
    )


def _terminal_controls(
    rows: tuple[TerminalDispositionRow, ...], original: AuthorizedManifest
) -> TerminalControls:
    by_action = {
        action: _action_totals(rows, action)
        for action in ("RESTORE", "MAP", "EXCLUDE")
    }
    supplement_actions = {
        action: by_action[action].source_keys
        for action in ("RESTORE", "MAP", "EXCLUDE")
    }
    original_maps = tuple(
        row for row in original.rows if row.review_disposition == "MAP"
    )
    combined_targets = {
        row.canonical_variant_id for row in original_maps
    } | {
        row.canonical_variant_id for row in rows if row.action == "MAP"
    }
    return TerminalControls(
        by_action=by_action,
        supplement_actions=supplement_actions,
        combined_actions={
            "RESTORE": by_action["RESTORE"].source_keys,
            "MAP": len(original_maps) + by_action["MAP"].source_keys,
            "EXCLUDE": len(EXCLUSION_SOURCE_KEYS) + by_action["EXCLUDE"].source_keys,
            "LEAVE_UNRESOLVED": 0,
        },
        supplement_raw_rows=sum(row.raw_row_count for row in rows),
        supplement_net_units=sum(
            (row.net_units for row in rows), Decimal("0.0000")
        ),
        supplement_absolute_units=sum(
            (row.absolute_units for row in rows), Decimal("0.0000")
        ),
        supplement_net_sales=sum(
            (row.net_sales for row in rows), Decimal("0.00")
        ),
        supplement_absolute_sales=sum(
            (row.absolute_sales for row in rows), Decimal("0.00")
        ),
        combined_map_raw_rows=(
            sum(row.affected_raw_rows for row in original_maps)
            + by_action["MAP"].raw_rows
        ),
        distinct_map_targets=len(combined_targets),
    )


def _validate_terminal_rows(
    rows: tuple[TerminalDispositionRow, ...], original: AuthorizedManifest
) -> TerminalControls:
    if len(rows) != 280:
        raise TerminalValidationError(
            f"terminal manifest rows={len(rows)} expected 280"
        )
    keys = [row.source_identity_key for row in rows]
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
    if duplicates:
        raise TerminalValidationError(
            f"duplicate terminal source_identity_key: {duplicates[0]}"
        )
    if keys != sorted(keys):
        raise TerminalValidationError("terminal manifest source keys are not sorted")

    original_by_key = {row.source_identity_key: row for row in original.rows}
    original_leave_keys = {
        row.source_identity_key
        for row in original.rows
        if row.review_disposition == "LEAVE_UNRESOLVED"
    }
    if set(keys) != original_leave_keys:
        raise TerminalValidationError(
            "terminal manifest membership differs from original 280 LEAVE decisions"
        )

    for row in rows:
        if row.artifact_version != TERMINAL_ARTIFACT_VERSION:
            raise TerminalValidationError(
                f"terminal artifact version drift for {row.source_identity_key}"
            )
        if row.prior_manifest_sha256 != APPROVED_MANIFEST_SHA256:
            raise TerminalValidationError(
                f"prior manifest SHA drift for {row.source_identity_key}"
            )
        prior = original_by_key[row.source_identity_key]
        if (
            row.prior_manifest_row_number != prior.row_number
            or row.prior_disposition != "LEAVE_UNRESOLVED"
            or prior.review_disposition != "LEAVE_UNRESOLVED"
        ):
            raise TerminalValidationError(
                f"prior decision provenance drift for {row.source_identity_key}"
            )
        if row.evidence_version != TERMINAL_EVIDENCE_VERSION:
            raise TerminalValidationError(
                f"evidence version drift for {row.source_identity_key}"
            )
        if _canonical_source_key(row) != row.source_identity_key:
            raise TerminalValidationError(
                f"canonical source identity drift for {row.source_identity_key}"
            )
        if row.first_sale_date > row.last_sale_date:
            raise TerminalValidationError(
                f"sale chronology drift for {row.source_identity_key}"
            )
        if row.absolute_units < abs(row.net_units) or row.absolute_sales < abs(
            row.net_sales
        ):
            raise TerminalValidationError(
                f"absolute financial controls drift for {row.source_identity_key}"
            )
        if row.action == "RESTORE":
            if (
                row.source_variant_id in (None, "0")
                or row.canonical_variant_id != row.source_variant_id
                or row.exclusion_reason_code is not None
            ):
                raise TerminalValidationError(
                    f"RESTORE target/provenance drift for {row.source_identity_key}"
                )
        elif row.action == "MAP":
            if not row.canonical_variant_id or row.exclusion_reason_code is not None:
                raise TerminalValidationError(
                    f"MAP target/provenance drift for {row.source_identity_key}"
                )
        elif (
            row.canonical_variant_id is not None
            or row.exclusion_reason_code != TERMINAL_EXCLUSION_REASON
        ):
            raise TerminalValidationError(
                f"EXCLUDE target/reason drift for {row.source_identity_key}"
            )
        if row.canonical_variant_id == FIESTA_CANONICAL_VARIANT_ID:
            raise TerminalValidationError("High Noon Fiesta canonical target is forbidden")

    by_key = {row.source_identity_key: row for row in rows}
    if set(by_key).intersection(HIGH_NOON_TEQUILA_SOURCE_KEYS) != set(
        HIGH_NOON_TEQUILA_SOURCE_KEYS
    ) or any(by_key[key].action != "EXCLUDE" for key in HIGH_NOON_TEQUILA_SOURCE_KEYS):
        raise TerminalValidationError("High Noon Tequila terminal control drifted")
    if set(by_key).intersection(POPOV_CONTRADICTION_SOURCE_KEYS) != set(
        POPOV_CONTRADICTION_SOURCE_KEYS
    ) or any(
        by_key[key].action != "EXCLUDE" for key in POPOV_CONTRADICTION_SOURCE_KEYS
    ):
        raise TerminalValidationError("Popov contradiction terminal control drifted")

    controls = _terminal_controls(rows, original)
    expected = {
        "RESTORE": (
            43,
            435,
            Decimal("511.0000"),
            Decimal("511.0000"),
            Decimal("8506.52"),
            Decimal("8506.52"),
        ),
        "MAP": (
            47,
            200,
            Decimal("232.0000"),
            Decimal("232.0000"),
            Decimal("4096.03"),
            Decimal("4096.03"),
        ),
        "EXCLUDE": (
            190,
            1465,
            Decimal("1798.0000"),
            Decimal("1808.0000"),
            Decimal("37695.37"),
            Decimal("40685.37"),
        ),
    }
    for action, values in expected.items():
        if controls.by_action[action].as_tuple() != values:
            raise TerminalValidationError(
                f"terminal {action} controls differ from owner approval"
            )
    if controls.combined_actions != {
        "RESTORE": 43,
        "MAP": 102,
        "EXCLUDE": 198,
        "LEAVE_UNRESOLVED": 0,
    }:
        raise TerminalValidationError("combined terminal action controls drifted")
    if controls.combined_map_raw_rows != 1023 or controls.distinct_map_targets != 96:
        raise TerminalValidationError("combined terminal MAP controls drifted")

    continuity: dict[str, list[TerminalDispositionRow]] = defaultdict(list)
    for row in rows:
        if row.continuity_pair_id:
            continuity[row.continuity_pair_id].append(row)
        elif any(
            value is not None
            for value in (
                row.continuity_role,
                row.continuity_predecessor_variant_id,
                row.continuity_successor_variant_id,
                row.continuity_sale_periods_overlap,
                row.continuity_gap_days,
                row.continuity_evidence,
            )
        ):
            raise TerminalValidationError(
                f"partial continuity evidence for {row.source_identity_key}"
            )
    if len(continuity) != 19 or sum(map(len, continuity.values())) != 38:
        raise TerminalValidationError("continuity-pair controls drifted")
    for pair_id, pair_rows in continuity.items():
        if len(pair_rows) != 2 or {row.continuity_role for row in pair_rows} != {
            "PREDECESSOR",
            "SUCCESSOR",
        }:
            raise TerminalValidationError(f"continuity pair {pair_id} is incomplete")
        predecessor = next(
            row for row in pair_rows if row.continuity_role == "PREDECESSOR"
        )
        successor = next(
            row for row in pair_rows if row.continuity_role == "SUCCESSOR"
        )
        if (
            predecessor.action != "MAP"
            or successor.action != "RESTORE"
            or predecessor.canonical_variant_id != successor.source_variant_id
            or successor.canonical_variant_id != successor.source_variant_id
            or predecessor.continuity_sale_periods_overlap is not False
            or successor.continuity_sale_periods_overlap is not False
            or predecessor.last_sale_date >= successor.first_sale_date
        ):
            raise TerminalValidationError(f"continuity pair {pair_id} drifted")
    return controls


def load_terminal_artifact(
    terminal_path: str | Path, original_manifest_path: str | Path
) -> TerminalArtifact:
    raw_bytes = Path(terminal_path).read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if sha256 != TERMINAL_MANIFEST_SHA256:
        raise TerminalValidationError(
            f"terminal manifest SHA-256 mismatch: {sha256}"
        )
    original = load_authorized_manifest(original_manifest_path)
    rows = _parse_terminal_rows(raw_bytes)
    controls = _validate_terminal_rows(rows, original)
    artifact = TerminalArtifact(
        raw_bytes=raw_bytes,
        sha256=sha256,
        rows=rows,
        original_manifest=original,
        controls=controls,
    )
    digest = hashlib.sha256(canonical_map_target_flow_bytes(artifact)).hexdigest()
    if digest != MAP_TARGET_FLOW_SHA256:
        raise TerminalValidationError(
            f"MAP target-flow SHA-256 mismatch: {digest}"
        )
    return artifact


def _format_exact_decimal(value: Decimal, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    if not value.is_finite() or value != value.quantize(quantum):
        raise TerminalValidationError(
            f"MAP target flow has decimal precision beyond {places} places"
        )
    return f"{value:.{places}f}"


def canonical_map_target_flow_bytes(artifact: TerminalArtifact) -> bytes:
    """Return the exact reviewed canonical MAP target-flow preimage."""

    flows: dict[str, dict[str, Any]] = {}

    def add(
        *,
        target: str,
        source_key: str,
        raw_rows: int,
        net_units: Decimal,
        absolute_units: Decimal,
        net_sales: Decimal,
        absolute_sales: Decimal,
    ) -> None:
        flow = flows.setdefault(
            target,
            {
                "source_key_values": set(),
                "raw_rows": 0,
                "net_units": Decimal("0.0000"),
                "absolute_units": Decimal("0.0000"),
                "net_sales": Decimal("0.00"),
                "absolute_sales": Decimal("0.00"),
            },
        )
        flow["source_key_values"].add(source_key)
        flow["raw_rows"] += raw_rows
        flow["net_units"] += net_units
        flow["absolute_units"] += absolute_units
        flow["net_sales"] += net_sales
        flow["absolute_sales"] += absolute_sales

    for row in artifact.original_manifest.rows:
        if row.review_disposition != "MAP":
            continue
        assert row.canonical_variant_id is not None
        add(
            target=row.canonical_variant_id,
            source_key=row.source_identity_key,
            raw_rows=row.affected_raw_rows,
            net_units=row.absolute_unit_magnitude,
            absolute_units=row.absolute_unit_magnitude,
            net_sales=row.absolute_sales_magnitude,
            absolute_sales=row.absolute_sales_magnitude,
        )
    for row in artifact.rows:
        if row.action != "MAP":
            continue
        assert row.canonical_variant_id is not None
        add(
            target=row.canonical_variant_id,
            source_key=row.source_identity_key,
            raw_rows=row.raw_row_count,
            net_units=row.net_units,
            absolute_units=row.absolute_units,
            net_sales=row.net_sales,
            absolute_sales=row.absolute_sales,
        )

    records: list[dict[str, Any]] = []
    for target in sorted(flows):
        flow = flows[target]
        sorted_source_keys = tuple(sorted(flow["source_key_values"]))
        records.append(
            {
                "target": target,
                "source_keys": len(sorted_source_keys),
                "raw_rows": flow["raw_rows"],
                "net_units": _format_exact_decimal(flow["net_units"], 4),
                "absolute_units": _format_exact_decimal(
                    flow["absolute_units"], 4
                ),
                "net_sales": _format_exact_decimal(flow["net_sales"], 2),
                "absolute_sales": _format_exact_decimal(
                    flow["absolute_sales"], 2
                ),
            }
        )
    serialized = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return (serialized + "\n").encode("utf-8")


def _git(
    repository_root: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repository_root), *arguments],
            check=check,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TerminalValidationError("unable to verify Git repository identity") from exc


def derive_execution_git_identity(
    repo_root: str | Path,
    *,
    expected_sha: str | None = None,
    required_paths: Iterable[str] = (),
) -> ExecutionGitIdentity:
    """Derive execution provenance from a clean committed repository."""

    requested_root = Path(repo_root).resolve()
    top_level = _git(requested_root, "rev-parse", "--show-toplevel").stdout.strip()
    repository_root = Path(top_level).resolve()
    git_sha = _git(repository_root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise TerminalValidationError("Git repository HEAD is not a full commit SHA")

    status = _git(
        repository_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout
    if status:
        raise TerminalValidationError("execution Git worktree must be clean")

    for required_path in required_paths:
        candidate = (repository_root / required_path).resolve()
        try:
            relative = candidate.relative_to(repository_root).as_posix()
        except ValueError as exc:
            raise TerminalValidationError(
                f"required implementation path is not tracked: {required_path}"
            ) from exc
        result = _git(
            repository_root,
            "ls-files",
            "--error-unmatch",
            "--",
            relative,
            check=False,
        )
        if result.returncode != 0:
            raise TerminalValidationError(
                f"required implementation path is not tracked: {required_path}"
            )

    if expected_sha is not None:
        authorized = str(expected_sha).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", authorized) or authorized != git_sha:
            raise TerminalValidationError(
                "derived execution Git SHA differs from authorized execution Git SHA"
            )
    return ExecutionGitIdentity(repository_root=repository_root, git_sha=git_sha)


@dataclass(frozen=True)
class TerminalExecutionContext:
    """Caller intent; observed execution identity is deliberately absent."""

    actor: str
    expected_execution_git_sha: str | None = None

    def __post_init__(self) -> None:
        actor = str(self.actor).strip()
        if not actor:
            raise TerminalValidationError("terminal execution actor is required")
        expected = self.expected_execution_git_sha
        if expected is not None:
            expected = str(expected).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{40}", expected):
                raise TerminalValidationError(
                    "expected execution Git SHA must be a full commit SHA"
                )
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "expected_execution_git_sha", expected)


def derive_runtime_execution_git_identity(
    expected_sha: str | None = None,
) -> ExecutionGitIdentity:
    """Derive identity from the repository containing this running module."""

    repository_root = Path(__file__).resolve().parents[3]
    return derive_execution_git_identity(
        repository_root,
        expected_sha=expected_sha,
        required_paths=(
            "procurement/src/procurement_os/historical_sales_terminal.py",
            "procurement/src/procurement_os/historical_sales.py",
            "procurement/src/procurement_os/sales.py",
            "procurement/tools/persist_phase4_terminal_disposition.py",
            "procurement/db/007_phase4_terminal_disposition.sql",
            "procurement/review/phase4_terminal_disposition_manifest.csv",
            "docs/superpowers/specs/2026-08-25-phase4-terminal-disposition-implementation-design.md",
        ),
    )


def _validate_artifact_instance(artifact: TerminalArtifact) -> None:
    terminal_sha = hashlib.sha256(artifact.raw_bytes).hexdigest()
    if terminal_sha != TERMINAL_MANIFEST_SHA256 or artifact.sha256 != terminal_sha:
        raise TerminalValidationError("terminal artifact bytes or SHA drifted")
    if (
        hashlib.sha256(artifact.original_manifest.raw_bytes).hexdigest()
        != APPROVED_MANIFEST_SHA256
        or artifact.original_manifest.sha256 != APPROVED_MANIFEST_SHA256
    ):
        raise TerminalValidationError("original manifest bytes or SHA drifted")
    controls = _validate_terminal_rows(artifact.rows, artifact.original_manifest)
    if controls != artifact.controls:
        raise TerminalValidationError("terminal artifact cached controls drifted")
    flow_sha = hashlib.sha256(canonical_map_target_flow_bytes(artifact)).hexdigest()
    if flow_sha != MAP_TARGET_FLOW_SHA256:
        raise TerminalValidationError("terminal MAP target-flow digest drifted")


def _terminal_safe_alias_families(
    artifact: TerminalArtifact, *, terminal: bool
) -> dict[str, str]:
    terminal_by_key = {row.source_identity_key: row for row in artifact.rows}
    families: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for original_row in artifact.original_manifest.rows:
        source_variant_id = original_row.source_variant_id
        if source_variant_id in (None, "0"):
            continue
        if terminal and original_row.source_identity_key in terminal_by_key:
            row = terminal_by_key[original_row.source_identity_key]
            action, target = row.action, row.canonical_variant_id
        else:
            action, target = original_row.review_disposition, original_row.canonical_variant_id
        families[source_variant_id].append((action, target))
    safe: dict[str, str] = {}
    for old_id, outcomes in families.items():
        targets = {target for action, target in outcomes if action == "MAP" and target}
        if (
            len(targets) == 1
            and all(action == "MAP" for action, _ in outcomes)
        ):
            target = next(iter(targets))
            if target != old_id:
                safe[old_id] = target
    expected_count = 56 if terminal else 17
    if len(safe) != expected_count:
        raise TerminalValidationError(
            f"safe old-ID alias families={len(safe)} expected {expected_count}"
        )
    return safe


def _load_alias_families(
    conn: Any, source_variant_ids: Iterable[str]
) -> tuple[dict[str, str], list[str]]:
    ids = sorted(set(source_variant_ids))
    if not ids:
        return {}, []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT old_variant_id,variant_id
               FROM variant_aliases
               WHERE approved=TRUE AND old_variant_id=ANY(%s)
               ORDER BY old_variant_id,variant_id,alias_id""",
            (ids,),
        )
        grouped: dict[str, set[str]] = defaultdict(set)
        row_counts: Counter[str] = Counter()
        for old_id, target in cur.fetchall():
            grouped[str(old_id)].add(str(target))
            row_counts[str(old_id)] += 1
    conflicts = sorted(
        old_id
        for old_id, targets in grouped.items()
        if len(targets) != 1 or row_counts[old_id] != 1
    )
    return {
        old_id: next(iter(targets))
        for old_id, targets in grouped.items()
        if len(targets) == 1
    }, conflicts


def _load_latest_terminal_decisions(
    conn: Any, source_keys: Iterable[str]
) -> dict[str, dict[str, Any]]:
    keys = sorted(set(source_keys))
    with conn.cursor() as cur:
        cur.execute(
            """WITH ranked AS (
                 SELECT d.*,
                        ROW_NUMBER() OVER (
                          PARTITION BY source_identity_key
                          ORDER BY decided_at DESC,
                                   historical_sales_review_decision_id DESC
                        ) AS effective_row
                 FROM historical_sales_review_decisions d
                 WHERE source_identity_key=ANY(%s)
               )
               SELECT historical_sales_review_decision_id,sales_backfill_id,
                      source_identity_key,source_variant_id,source_sku,
                      source_product_title,source_variant_title,decision_action,
                      canonical_variant_id,evidence_json,supersedes_decision_id,
                      decision_schema_version,reason_code,primary_manifest_sha256,
                      primary_manifest_row_number,evidence_version,
                      owner_authorization,authority_git_sha,execution_git_sha,
                      actor,reason
               FROM ranked WHERE effective_row=1 ORDER BY source_identity_key""",
            (keys,),
        )
        result: dict[str, dict[str, Any]] = {}
        for row in cur.fetchall():
            result[str(row[2])] = {
                "decision_id": str(row[0]),
                "sales_backfill_id": str(row[1]) if row[1] is not None else None,
                "source_identity_key": str(row[2]),
                "source_variant_id": str(row[3]) if row[3] is not None else None,
                "source_sku": str(row[4]) if row[4] is not None else None,
                "source_product_title": str(row[5]) if row[5] is not None else None,
                "source_variant_title": str(row[6]) if row[6] is not None else None,
                "decision_action": str(row[7]),
                "canonical_variant_id": str(row[8]) if row[8] is not None else None,
                "evidence_json": row[9] if isinstance(row[9], dict) else {},
                "supersedes_decision_id": str(row[10]) if row[10] is not None else None,
                "decision_schema_version": str(row[11]),
                "reason_code": str(row[12]) if row[12] is not None else None,
                "primary_manifest_sha256": str(row[13]) if row[13] is not None else None,
                "primary_manifest_row_number": int(row[14]) if row[14] is not None else None,
                "evidence_version": str(row[15]) if row[15] is not None else None,
                "owner_authorization": str(row[16]) if row[16] is not None else None,
                "authority_git_sha": str(row[17]) if row[17] is not None else None,
                "execution_git_sha": str(row[18]) if row[18] is not None else None,
                "actor": str(row[19]),
                "reason": str(row[20]),
            }
    return result


def _load_decisions_by_id(
    conn: Any, decision_ids: Iterable[str]
) -> dict[str, tuple[str, str, str | None, str | None, str]]:
    ids = sorted(set(decision_ids))
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT historical_sales_review_decision_id,source_identity_key,
                      decision_action,canonical_variant_id,sales_backfill_id,
                      decision_schema_version
               FROM historical_sales_review_decisions
               WHERE historical_sales_review_decision_id=ANY(%s::uuid[])""",
            (ids,),
        )
        return {
            str(row[0]): (
                str(row[1]),
                str(row[2]),
                str(row[3]) if row[3] is not None else None,
                str(row[4]) if row[4] is not None else None,
                str(row[5]),
            )
            for row in cur.fetchall()
        }


def _legacy_decision_exact(
    db_row: Mapping[str, Any], original_row: Any, legacy_state: str
) -> bool:
    return (
        legacy_state == "CURRENT_PROVENANCE"
        and db_row["decision_schema_version"] == "LEGACY_V1"
        and db_row["reason_code"] is None
        and db_row["primary_manifest_sha256"] is None
        and db_row["primary_manifest_row_number"] is None
        and db_row["evidence_version"] is None
        and db_row["owner_authorization"] is None
        and db_row["authority_git_sha"] is None
        and db_row["execution_git_sha"] is None
        and db_row["source_variant_id"] == original_row.source_variant_id
        and db_row["source_sku"] == original_row.source_sku
        and db_row["source_product_title"]
        == (original_row.historical_product_title or None)
        and db_row["source_variant_title"]
        == (original_row.historical_variant_title or None)
    )


def _terminal_decision_exact(
    db_row: Mapping[str, Any],
    *,
    source_variant_id: str | None,
    source_sku: str | None,
    source_product_title: str | None,
    source_variant_title: str | None,
    action: str,
    canonical_variant_id: str | None,
    reason_code: str | None,
    primary_manifest_sha256: str,
    primary_manifest_row_number: int,
    execution_git_sha: str,
    superseded: tuple[str, str, str | None, str | None, str] | None,
    expected_prior_action: str,
    expected_decision_run_id: str,
) -> bool:
    expected_target = canonical_variant_id
    return (
        db_row["sales_backfill_id"] == expected_decision_run_id
        and db_row["source_variant_id"] == source_variant_id
        and db_row["source_sku"] == source_sku
        and db_row["source_product_title"] == source_product_title
        and db_row["source_variant_title"] == source_variant_title
        and db_row["decision_action"] == action
        and db_row["canonical_variant_id"] == expected_target
        and db_row["decision_schema_version"] == TERMINAL_DECISION_SCHEMA_VERSION
        and db_row["reason_code"] == reason_code
        and db_row["primary_manifest_sha256"] == primary_manifest_sha256
        and db_row["primary_manifest_row_number"] == primary_manifest_row_number
        and db_row["evidence_version"] == TERMINAL_EVIDENCE_VERSION
        and db_row["owner_authorization"] == TERMINAL_OWNER_AUTHORIZATION
        and db_row["authority_git_sha"] == AUTHORITY_GIT_SHA
        and db_row["execution_git_sha"] == execution_git_sha
        and superseded is not None
        and superseded[0] == db_row["source_identity_key"]
        and superseded[1] == expected_prior_action
        and superseded[2] is None
        and superseded[3] == expected_decision_run_id
        and superseded[4] == "LEGACY_V1"
    )


def _validate_source_facts(
    conn: Any, artifact: TerminalArtifact
) -> SourceFactsValidation:
    """Validate the complete 59,083-fact source and the 3,112-key partition."""

    all_keys = sorted(
        row.source_identity_key for row in artifact.original_manifest.rows
    )
    with conn.cursor() as cur:
        cur.execute(
            """SELECT status,source,raw_rows,resolved_rows,unresolved_rows,
                      ambiguous_rows,unique_source_facts,coverage_complete,pages_complete,
                      source_facts_persisted,idempotency_verified,
                      control_totals_reconciled,canonical_aggregate_rebuilt
               FROM sales_backfill_runs WHERE sales_backfill_id=%s""",
            (APPROVED_RUN_ID,),
        )
        run = cur.fetchone()
        immutable_run = (
            run is not None
            and run[0] == "COMPLETED"
            and run[1] == "SHOPIFYQL_SALES"
            and int(run[2]) == 59083
            and int(run[6]) == 59083
            and tuple(run[7:]) == (True, True, True, True, True, True)
        )
        if not immutable_run:
            raise TerminalValidationError("approved source run controls drifted")
        run_shape = (int(run[3]), int(run[4]), int(run[5]))
        if run_shape == (55971, 3112, 0):
            lifecycle = "PRE_REBUILD"
        elif run_shape == (57429, 0, 0):
            lifecycle = "POST_REBUILD"
        else:
            raise TerminalValidationError("approved source run lifecycle drifted")

        cur.execute(
            """SELECT COUNT(*)::int,COUNT(DISTINCT rf.raw_sales_id)::int,
                      COUNT(*) FILTER (WHERE r.resolution_status='RESOLVED')::int,
                      COUNT(*) FILTER (WHERE r.resolution_status='UNRESOLVED')::int,
                      COUNT(*) FILTER (WHERE r.resolution_status='AMBIGUOUS')::int,
                      COUNT(*) FILTER (WHERE r.resolution_status='EXCLUDED')::int
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s""",
            (APPROVED_RUN_ID,),
        )
        fact_shape = tuple(int(value) for value in cur.fetchone())
        expected_shape = (
            (59083, 59083, 55971, 3112, 0, 0)
            if lifecycle == "PRE_REBUILD"
            else (59083, 59083, 57429, 0, 0, 1654)
        )
        if fact_shape != expected_shape:
            raise TerminalValidationError("complete source-fact status controls drifted")

        cur.execute(
            """SELECT COUNT(*)::int,
                      COALESCE(SUM(rf.observed_net_items_sold),0),
                      COALESCE(SUM(ABS(rf.observed_net_items_sold)),0),
                      COALESCE(SUM(rf.observed_net_sales),0),
                      COALESCE(SUM(ABS(rf.observed_net_sales)),0)
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s
                 AND NOT (r.source_identity_key=ANY(%s))""",
            (APPROVED_RUN_ID, all_keys),
        )
        baseline = cur.fetchone()
        baseline_flow = {
            "raw_rows": int(baseline[0]),
            "net_units": str(Decimal(baseline[1])),
            "absolute_units": str(Decimal(baseline[2])),
            "net_sales": str(Decimal(baseline[3])),
            "absolute_sales": str(Decimal(baseline[4])),
        }
        if baseline_flow != {
            "raw_rows": 55971,
            "net_units": "78815.0000",
            "absolute_units": "78849.0000",
            "net_sales": "1231372.83",
            "absolute_sales": "1232304.51",
        }:
            raise TerminalValidationError("previously resolved source flow drifted")

        cur.execute(
            """SELECT r.source_identity_key,COUNT(*)::int,MIN(r.sale_date),MAX(r.sale_date),
                      COALESCE(SUM(rf.observed_net_items_sold),0),
                      COALESCE(SUM(ABS(rf.observed_net_items_sold)),0),
                      COALESCE(SUM(rf.observed_net_sales),0),
                      COALESCE(SUM(ABS(rf.observed_net_sales)),0)
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE rf.sales_backfill_id=%s
                 AND r.source_identity_key=ANY(%s)
               GROUP BY r.source_identity_key ORDER BY r.source_identity_key""",
            (APPROVED_RUN_ID, all_keys),
        )
        metrics = {str(row[0]): row[1:] for row in cur.fetchall()}
        cur.execute(
            """SELECT r.source_identity_key,r.source_variant_id,r.source_sku,
                      r.source_product_title,r.source_variant_title,r.resolution_status
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r USING(raw_sales_id)
               WHERE rf.sales_backfill_id=%s
                 AND r.source_identity_key=ANY(%s)
               ORDER BY r.source_identity_key,r.raw_sales_id""",
            (APPROVED_RUN_ID, all_keys),
        )
        evidence_rows = cur.fetchall()

    original_by_key = {
        row.source_identity_key: row for row in artifact.original_manifest.rows
    }
    if set(metrics) != set(original_by_key):
        raise TerminalValidationError("source-key membership drifted")
    if len(evidence_rows) != 3112:
        raise TerminalValidationError("affected raw-row population drifted")
    terminal_by_key = {row.source_identity_key: row for row in artifact.rows}
    expected_post_status: dict[str, str] = {}
    for original_row in artifact.original_manifest.rows:
        terminal_row = terminal_by_key.get(original_row.source_identity_key)
        action = (
            terminal_row.action
            if terminal_row is not None
            else original_row.review_disposition
        )
        expected_post_status[original_row.source_identity_key] = (
            "EXCLUDED" if action == "EXCLUDE" else "RESOLVED"
        )
    for key, variant_id, sku, product_title, variant_title, status in evidence_rows:
        computed = HistoricalIdentityIndex.source_key(
            SalesSourceRow(
                date(1970, 1, 1),
                str(variant_id) if variant_id is not None else None,
                str(sku) if sku is not None else None,
                str(product_title) if product_title is not None else None,
                str(variant_title) if variant_title is not None else None,
                Decimal("0"),
                Decimal("0"),
            )
        )
        expected_status = (
            "UNRESOLVED"
            if lifecycle == "PRE_REBUILD"
            else expected_post_status[str(key)]
        )
        if computed != str(key) or status != expected_status:
            raise TerminalValidationError(f"source evidence drifted for {key}")

    for key, original_row in original_by_key.items():
        metric = metrics[key]
        row_count, first_date, last_date = int(metric[0]), metric[1], metric[2]
        net_units, absolute_units = Decimal(metric[3]), Decimal(metric[4])
        net_sales, absolute_sales = Decimal(metric[5]), Decimal(metric[6])
        if (
            row_count != original_row.affected_raw_rows
            or absolute_units != original_row.absolute_unit_magnitude
            or absolute_sales != original_row.absolute_sales_magnitude
        ):
            raise TerminalValidationError(f"original source controls drifted for {key}")
        terminal_row = terminal_by_key.get(key)
        if terminal_row is not None and (
            row_count != terminal_row.raw_row_count
            or first_date != terminal_row.first_sale_date
            or last_date != terminal_row.last_sale_date
            or net_units != terminal_row.net_units
            or absolute_units != terminal_row.absolute_units
            or net_sales != terminal_row.net_sales
            or absolute_sales != terminal_row.absolute_sales
        ):
            raise TerminalValidationError(f"terminal source controls drifted for {key}")
    return SourceFactsValidation(
        lifecycle=lifecycle,
        status_counts={
            "facts": fact_shape[0],
            "resolved": fact_shape[2],
            "unresolved": fact_shape[3],
            "ambiguous": fact_shape[4],
            "excluded": fact_shape[5],
        },
        baseline_flow=baseline_flow,
    )


def _load_restored_variants(
    conn: Any, restore_ids: Iterable[str]
) -> dict[str, tuple[Any, ...]]:
    ids = sorted(set(restore_ids))
    with conn.cursor() as cur:
        cur.execute(
            """SELECT variant_id,product_id,product_title,variant_title,sku,active,
                      catalog_state,identity_scope,restoration_manifest_sha256,
                      restoration_manifest_row_number,restoration_evidence_version,
                      restoration_owner_authorization,restoration_authority_git_sha,
                      restoration_execution_git_sha
               FROM variants WHERE variant_id=ANY(%s) ORDER BY variant_id""",
            (ids,),
        )
        return {str(row[0]): tuple(row[1:]) for row in cur.fetchall()}


def _load_active_exclusions(conn: Any) -> dict[str, tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT source_key,source_variant_id,source_sku,source_product_title,
                      source_variant_title,reason_code,effective_decision_id
               FROM historical_sales_exclusions WHERE active ORDER BY source_key"""
        )
        return {
            str(row[0]): (
                str(row[1]) if row[1] is not None else None,
                str(row[2]) if row[2] is not None else None,
                str(row[3]) if row[3] is not None else None,
                str(row[4]) if row[4] is not None else None,
                str(row[5]) if row[5] is not None else None,
                str(row[6]) if row[6] is not None else None,
            )
            for row in cur.fetchall()
        }


def _broad_alias_family_evidence(
    conn: Any,
    artifact: TerminalArtifact,
    aliases: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """Prove every full-run member of each proposed old-ID alias family."""

    old_ids = sorted(aliases)
    if not old_ids:
        return {}
    original_by_key = {
        row.source_identity_key: row for row in artifact.original_manifest.rows
    }
    terminal_by_key = {row.source_identity_key: row for row in artifact.rows}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.source_identity_key,r.source_variant_id,r.source_sku,
                      r.source_product_title,r.source_variant_title,
                      r.resolution_status,r.canonical_variant_id,r.resolution_method,
                      rf.observed_net_items_sold,rf.observed_net_sales
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s
                 AND regexp_replace(btrim(COALESCE(r.source_variant_id,'')),'^.*/','')=ANY(%s)
               ORDER BY r.source_identity_key,r.raw_sales_id""",
            (APPROVED_RUN_ID, old_ids),
        )
        facts = cur.fetchall()

    grouped: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
    for fact in facts:
        old_id = numeric_shopify_id(fact[1])
        if old_id in aliases:
            computed_key = HistoricalIdentityIndex.source_key(
                SalesSourceRow(
                    date(1970, 1, 1),
                    fact[1],
                    fact[2],
                    fact[3],
                    fact[4],
                    Decimal(str(fact[8])),
                    Decimal(str(fact[9])) if fact[9] is not None else None,
                )
            )
            if computed_key != str(fact[0]):
                raise TerminalValidationError(
                    f"broad alias source-key drift for old Variant ID {old_id}"
                )
            grouped[old_id].append(fact)

    result: dict[str, dict[str, Any]] = {}
    for old_id, target in sorted(aliases.items()):
        family = grouped.get(old_id, [])
        if not family:
            raise TerminalValidationError(
                f"broad alias family has no full-run facts for old Variant ID {old_id}"
            )
        source_keys: set[str] = set()
        baseline_facts = manifest_facts = 0
        net_units = Decimal("0")
        absolute_units = Decimal("0")
        net_sales = Decimal("0")
        absolute_sales = Decimal("0")
        for fact in family:
            key = str(fact[0])
            source_keys.add(key)
            units = Decimal(str(fact[8]))
            sales = Decimal(str(fact[9] or 0))
            net_units += units
            absolute_units += abs(units)
            net_sales += sales
            absolute_sales += abs(sales)
            if key in original_by_key:
                manifest_facts += 1
                terminal = terminal_by_key.get(key)
                action = terminal.action if terminal else original_by_key[key].review_disposition
                approved_target = (
                    terminal.canonical_variant_id
                    if terminal
                    else original_by_key[key].canonical_variant_id
                )
                if action != "MAP" or approved_target != target:
                    raise TerminalValidationError(
                        f"broad alias family conflicts with approved disposition for {key}"
                    )
            else:
                baseline_facts += 1
                if fact[5] != "RESOLVED" or str(fact[6] or "") != target:
                    raise TerminalValidationError(
                        f"broad alias would reattribute prior resolved fact {key}"
                    )
        result[old_id] = {
            "old_variant_id": old_id,
            "canonical_variant_id": target,
            "full_run_source_keys": sorted(source_keys),
            "full_run_raw_facts": len(family),
            "previously_resolved_raw_facts": baseline_facts,
            "manifest_raw_facts": manifest_facts,
            "net_units": f"{net_units:.4f}",
            "absolute_units": f"{absolute_units:.4f}",
            "net_sales": f"{net_sales:.2f}",
            "absolute_sales": f"{absolute_sales:.2f}",
            "proof": "FULL_APPROVED_RUN_FAMILY_UNIFORM",
        }
    return result


def inspect_terminal_state(
    conn: Any, artifact: TerminalArtifact, execution_git_sha: str
) -> dict[str, Any]:
    """Classify the complete relevant database state without mutation."""

    _validate_artifact_instance(artifact)
    execution_git_sha = str(execution_git_sha).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", execution_git_sha):
        raise TerminalValidationError("execution Git SHA must be a full commit SHA")
    diagnostics: list[str] = []
    source_validation: SourceFactsValidation | None = None
    try:
        source_validation = _validate_source_facts(conn, artifact)
    except TerminalValidationError as exc:
        diagnostics.append(str(exc))

    all_keys = [row.source_identity_key for row in artifact.original_manifest.rows]
    latest = _load_latest_terminal_decisions(conn, all_keys)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT source_identity_key
               FROM historical_sales_review_decisions
               WHERE sales_backfill_id=%s ORDER BY source_identity_key""",
            (APPROVED_RUN_ID,),
        )
        approved_run_decision_keys = {str(row[0]) for row in cur.fetchall()}
    decision_key_membership_exact = approved_run_decision_keys == set(all_keys)
    old_latest = load_latest_effective_decisions(conn, all_keys)
    legacy_states = {
        item.source_identity_key: item.state
        for item in classify_existing_decisions(artifact.original_manifest, old_latest)
    }
    original_by_key = {
        row.source_identity_key: row for row in artifact.original_manifest.rows
    }
    terminal_by_key = {row.source_identity_key: row for row in artifact.rows}
    superseded = _load_decisions_by_id(
        conn,
        (
            row["supersedes_decision_id"]
            for row in latest.values()
            if row["supersedes_decision_id"]
        ),
    )
    legacy_exact = {
        key: _legacy_decision_exact(latest[key], original_by_key[key], legacy_states.get(key, ""))
        for key in latest
        if key in original_by_key
    }

    pre_decisions_exact = decision_key_membership_exact and len(latest) == 343 and all(
        legacy_exact.get(key, False) for key in original_by_key
    )
    current_decisions_exact = decision_key_membership_exact and len(latest) == 343
    for key, original_row in original_by_key.items():
        db_row = latest.get(key)
        if db_row is None:
            current_decisions_exact = False
            continue
        terminal_row = terminal_by_key.get(key)
        if terminal_row is not None:
            current_decisions_exact &= _terminal_decision_exact(
                db_row,
                source_variant_id=terminal_row.source_variant_id,
                source_sku=terminal_row.historical_sku,
                source_product_title=terminal_row.historical_product_title or None,
                source_variant_title=terminal_row.historical_variant_title or None,
                action=terminal_row.action,
                canonical_variant_id=terminal_row.canonical_variant_id,
                reason_code=terminal_row.exclusion_reason_code,
                primary_manifest_sha256=TERMINAL_MANIFEST_SHA256,
                primary_manifest_row_number=terminal_row.row_number,
                execution_git_sha=execution_git_sha,
                superseded=superseded.get(db_row["supersedes_decision_id"]),
                expected_prior_action="LEAVE_UNRESOLVED",
                expected_decision_run_id=APPROVED_RUN_ID,
            )
        elif original_row.review_disposition == "EXCLUDE":
            current_decisions_exact &= _terminal_decision_exact(
                db_row,
                source_variant_id=original_row.source_variant_id,
                source_sku=original_row.source_sku,
                source_product_title=original_row.historical_product_title or None,
                source_variant_title=original_row.historical_variant_title or None,
                action="EXCLUDE",
                canonical_variant_id=None,
                reason_code=ORIGINAL_EXCLUSION_REASON,
                primary_manifest_sha256=APPROVED_MANIFEST_SHA256,
                primary_manifest_row_number=original_row.row_number,
                execution_git_sha=execution_git_sha,
                superseded=superseded.get(db_row["supersedes_decision_id"]),
                expected_prior_action="EXCLUDE",
                expected_decision_run_id=APPROVED_RUN_ID,
            )
        else:
            current_decisions_exact &= legacy_exact.get(key, False)

    restore_rows = {
        row.source_variant_id: row for row in artifact.rows if row.action == "RESTORE"
    }
    restored = _load_restored_variants(conn, restore_rows)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT variant_id FROM variants
               WHERE identity_scope='HISTORICAL_ONLY' ORDER BY variant_id"""
        )
        all_historical_only_ids = {str(row[0]) for row in cur.fetchall()}
    pre_restores_exact = not restored and not all_historical_only_ids
    current_restores_exact = (
        len(restored) == 43 and all_historical_only_ids == set(restore_rows)
    )
    for variant_id, row in restore_rows.items():
        stored = restored.get(variant_id)
        current_restores_exact &= stored == (
            None,
            row.historical_product_title,
            row.historical_variant_title,
            row.historical_sku,
            False,
            "RETIRED_CONFIRMED",
            "HISTORICAL_ONLY",
            TERMINAL_MANIFEST_SHA256,
            row.row_number,
            TERMINAL_EVIDENCE_VERSION,
            TERMINAL_OWNER_AUTHORIZATION,
            AUTHORITY_GIT_SHA,
            execution_git_sha,
        )

    population_ids = {
        row.source_variant_id
        for row in artifact.original_manifest.rows
        if row.source_variant_id not in (None, "0")
    }
    observed_aliases, alias_conflicts = _load_alias_families(conn, population_ids)
    pre_aliases = _terminal_safe_alias_families(artifact, terminal=False)
    current_aliases = _terminal_safe_alias_families(artifact, terminal=True)
    pre_aliases_exact = not alias_conflicts and observed_aliases == pre_aliases
    current_aliases_exact = not alias_conflicts and observed_aliases == current_aliases
    terminal_only_aliases = {
        old_id: target
        for old_id, target in current_aliases.items()
        if old_id not in pre_aliases
    }
    broad_alias_evidence: dict[str, dict[str, Any]] = {}
    broad_aliases_exact = True
    try:
        broad_alias_evidence = _broad_alias_family_evidence(
            conn, artifact, terminal_only_aliases
        )
    except TerminalValidationError as exc:
        broad_aliases_exact = False
        diagnostics.append(str(exc))
    with conn.cursor() as cur:
        cur.execute(
            """SELECT old_variant_id,variant_id,historical_product_title,
                      historical_variant_title,historical_sku,match_method,
                      confidence,source,approved,approved_by,approved_at,evidence_json
               FROM variant_aliases
               WHERE old_variant_id=ANY(%s)
               ORDER BY old_variant_id,alias_id""",
            (sorted(terminal_only_aliases),),
        )
        terminal_alias_rows = {str(row[0]): row[1:] for row in cur.fetchall()}
    rows_by_source_variant: dict[str, list[TerminalDispositionRow]] = defaultdict(list)
    for terminal_row in artifact.rows:
        if terminal_row.source_variant_id:
            rows_by_source_variant[terminal_row.source_variant_id].append(terminal_row)
    if current_aliases_exact:
        for old_id, target in terminal_only_aliases.items():
            alias = terminal_alias_rows.get(old_id)
            evidence_rows = sorted(
                rows_by_source_variant[old_id],
                key=lambda row: row.source_identity_key,
            )
            expected_evidence = {
                "terminal_manifest_sha256": TERMINAL_MANIFEST_SHA256,
                "canonical_variant_id": target,
                "evidence_version": TERMINAL_EVIDENCE_VERSION,
                "owner_authorization": TERMINAL_OWNER_AUTHORIZATION,
                "authority_git_sha": AUTHORITY_GIT_SHA,
                "execution_git_sha": execution_git_sha,
                "broad_family": broad_alias_evidence.get(old_id),
            }
            current_aliases_exact &= alias is not None and alias == (
                target,
                evidence_rows[0].historical_product_title,
                evidence_rows[0].historical_variant_title or None,
                evidence_rows[0].historical_sku,
                "OWNER_APPROVED_TERMINAL_CONTINUITY",
                Decimal("1.0000"),
                "PHASE4_TERMINAL_DISPOSITION",
                True,
                alias[8] if alias is not None else None,
                alias[9] if alias is not None else None,
                expected_evidence,
            ) and bool(alias[8]) and alias[9] is not None
    current_aliases_exact &= broad_aliases_exact
    pre_aliases_exact &= broad_aliases_exact

    expected_targets = {
        row.canonical_variant_id
        for row in artifact.original_manifest.rows
        if row.review_disposition == "MAP"
    } | {
        row.canonical_variant_id
        for row in artifact.rows
        if row.action in {"MAP", "RESTORE"}
    }
    expected_targets.discard(None)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT variant_id,active FROM variants
               WHERE variant_id=ANY(%s) ORDER BY variant_id""",
            (sorted(expected_targets),),
        )
        observed_targets = {str(row[0]): bool(row[1]) for row in cur.fetchall()}
        cur.execute(
            """SELECT variant_id,active,catalog_state FROM variants
               WHERE variant_id=ANY(%s) ORDER BY variant_id""",
            (sorted(current_aliases),),
        )
        conflicting_old_ids = {
            str(row[0])
            for row in cur.fetchall()
            if bool(row[1]) or str(row[2]) != "RESOLVED_RECREATED"
        }
    nonrestore_targets = expected_targets - set(restore_rows)
    pre_targets_exact = (
        set(observed_targets) == nonrestore_targets and not conflicting_old_ids
    )
    current_targets_exact = (
        set(observed_targets) == expected_targets and not conflicting_old_ids
    )

    exclusions = _load_active_exclusions(conn)
    pre_exclusions_exact = set(exclusions) == EXCLUSION_SOURCE_KEYS
    for key in EXCLUSION_SOURCE_KEYS:
        exclusion = exclusions.get(key)
        original = original_by_key[key]
        pre_exclusions_exact &= exclusion == (
            original.source_variant_id,
            original.source_sku,
            original.historical_product_title or None,
            original.historical_variant_title or None,
            None,
            None,
        )
    expected_exclusion_keys = set(EXCLUSION_SOURCE_KEYS) | {
        row.source_identity_key for row in artifact.rows if row.action == "EXCLUDE"
    }
    current_exclusions_exact = set(exclusions) == expected_exclusion_keys
    for key in expected_exclusion_keys:
        exclusion = exclusions.get(key)
        decision = latest.get(key)
        if exclusion is None or decision is None:
            current_exclusions_exact = False
            continue
        expected_reason = (
            ORIGINAL_EXCLUSION_REASON
            if key in EXCLUSION_SOURCE_KEYS
            else TERMINAL_EXCLUSION_REASON
        )
        current_exclusions_exact &= (
            exclusion[0] == decision["source_variant_id"]
            and exclusion[1] == decision["source_sku"]
            and exclusion[2] == decision["source_product_title"]
            and exclusion[3] == decision["source_variant_title"]
            and exclusion[4] == expected_reason
            and exclusion[5] == decision["decision_id"]
            and decision["decision_action"] == "EXCLUDE"
            and decision["canonical_variant_id"] is None
        )

    with conn.cursor() as cur:
        cur.execute(
            """SELECT authority_version,decision_authority_run_id,
                      original_manifest_sha256,terminal_manifest_sha256,
                      decision_schema_version,evidence_version,owner_authorization,
                      authority_git_sha,execution_git_sha,registered_by,registered_at
               FROM historical_sales_exclusion_authority_runs
               WHERE sales_backfill_id=%s""",
            (APPROVED_RUN_ID,),
        )
        authority_row = cur.fetchone()
        cur.execute(
            """SELECT status FROM readiness_gates
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        gate = cur.fetchone()
        cur.execute("SELECT COUNT(*)::int FROM purchase_orders")
        po_count = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*)::int FROM purchase_order_lines")
        po_line_count = int(cur.fetchone()[0])
    pre_authority_exact = authority_row is None
    current_authority_exact = (
        authority_row is not None
        and tuple(str(value) for value in authority_row[:9])
        == (
            TERMINAL_AUTHORITY_VERSION,
            APPROVED_RUN_ID,
            APPROVED_MANIFEST_SHA256,
            TERMINAL_MANIFEST_SHA256,
            TERMINAL_DECISION_SCHEMA_VERSION,
            TERMINAL_EVIDENCE_VERSION,
            TERMINAL_OWNER_AUTHORIZATION,
            AUTHORITY_GIT_SHA,
            execution_git_sha,
        )
        and bool(authority_row[9])
        and authority_row[10] is not None
    )
    source_pre_exact = (
        source_validation is not None
        and source_validation.lifecycle == "PRE_REBUILD"
    )
    source_current_exact = (
        source_validation is not None
        and source_validation.lifecycle in {"PRE_REBUILD", "POST_REBUILD"}
    )
    gate_status = str(gate[0]) if gate else None
    pre_protected_controls_exact = (
        gate_status == "FAIL" and po_count == 0 and po_line_count == 0
    )
    current_lifecycle_exact = (
        source_validation is not None
        and (
            (
                source_validation.lifecycle == "PRE_REBUILD"
                and gate_status == "FAIL"
            )
            or (
                source_validation.lifecycle == "POST_REBUILD"
                and gate_status == "PASS"
            )
        )
        and po_count == 0
        and po_line_count == 0
    )

    pre_components = {
        "source_facts": source_pre_exact,
        "decisions": pre_decisions_exact,
        "restores": pre_restores_exact,
        "aliases": pre_aliases_exact,
        "targets": pre_targets_exact,
        "exclusions": pre_exclusions_exact,
        "authority_registry": pre_authority_exact,
        "protected_controls": pre_protected_controls_exact,
    }
    current_components = {
        "source_facts": source_current_exact,
        "decisions": current_decisions_exact,
        "restores": current_restores_exact,
        "aliases": current_aliases_exact,
        "targets": current_targets_exact,
        "exclusions": current_exclusions_exact,
        "authority_registry": current_authority_exact,
        "protected_controls": current_lifecycle_exact,
    }
    if all(pre_components.values()):
        classification = "PRE_TERMINAL_EXACT"
    elif all(current_components.values()):
        classification = "CURRENT_TERMINAL_EXACT"
    else:
        classification = "CONFLICT"
        diagnostics.extend(
            f"pre:{name}" for name, passed in pre_components.items() if not passed
        )
        diagnostics.extend(
            f"current:{name}"
            for name, passed in current_components.items()
            if not passed
        )

    actions = Counter(row["decision_action"] for row in latest.values())
    return {
        "classification": classification,
        "diagnostics": sorted(set(diagnostics)),
        "preterminal_components": pre_components,
        "current_components": current_components,
        "effective_actions": {
            "RESTORE": actions.get("RESTORE", 0),
            "MAP": actions.get("MAP", 0),
            "EXCLUDE": actions.get("EXCLUDE", 0),
            "LEAVE_UNRESOLVED": actions.get("LEAVE_UNRESOLVED", 0),
        },
        "historical_only_variants": len(restored),
        "active_exclusions": len(exclusions),
        "approved_alias_families": len(observed_aliases),
        "source_lifecycle": (
            source_validation.lifecycle if source_validation is not None else "INVALID"
        ),
        "source_status_counts": (
            source_validation.status_counts if source_validation is not None else {}
        ),
        "baseline_source_flow": (
            source_validation.baseline_flow if source_validation is not None else {}
        ),
        "broad_alias_family_evidence": broad_alias_evidence,
        "planned_mutations": (
            {
                "restored_variants": 43,
                "terminal_decisions": 280,
                "original_exclusion_normalizations": 8,
                "terminal_aliases": len(set(current_aliases) - set(pre_aliases)),
                "active_exclusions": 198,
                "authority_registrations": 1,
            }
            if classification == "PRE_TERMINAL_EXACT"
            else {
                "restored_variants": 0,
                "terminal_decisions": 0,
                "original_exclusion_normalizations": 0,
                "terminal_aliases": 0,
                "active_exclusions": 0,
                "authority_registrations": 0,
            }
        ),
        "latest_decision_ids": {
            key: row["decision_id"] for key, row in latest.items()
        },
        "protected_fingerprints": protected_state_fingerprints(conn),
    }


def _terminal_decision_evidence(
    row: TerminalDispositionRow, execution_sha: str
) -> dict[str, Any]:
    return {
        "artifact_version": row.artifact_version,
        "terminal_manifest_sha256": TERMINAL_MANIFEST_SHA256,
        "terminal_manifest_row_number": row.row_number,
        "prior_manifest_sha256": row.prior_manifest_sha256,
        "prior_manifest_row_number": row.prior_manifest_row_number,
        "prior_disposition": row.prior_disposition,
        "final_action": row.action,
        "canonical_variant_id": row.canonical_variant_id,
        "evidence_classification": row.evidence_classification,
        "evidence_version": row.evidence_version,
        "owner_authorization": TERMINAL_OWNER_AUTHORIZATION,
        "authority_git_sha": AUTHORITY_GIT_SHA,
        "execution_git_sha": execution_sha,
        "source_identity_key": row.source_identity_key,
    }


def _original_exclusion_evidence(
    row: Any, execution_sha: str
) -> dict[str, Any]:
    return {
        "primary_manifest_sha256": APPROVED_MANIFEST_SHA256,
        "primary_manifest_row_number": row.row_number,
        "original_manifest_disposition": "EXCLUDE",
        "structured_reason_code": ORIGINAL_EXCLUSION_REASON,
        "terminal_authorization": TERMINAL_OWNER_AUTHORIZATION,
        "evidence_version": TERMINAL_EVIDENCE_VERSION,
        "authority_git_sha": AUTHORITY_GIT_SHA,
        "execution_git_sha": execution_sha,
        "source_identity_key": row.source_identity_key,
    }


def _insert_change_log(
    conn: Any,
    *,
    table_name: str,
    row_key: str,
    action: str,
    after: Mapping[str, Any],
    actor: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO change_log(table_name,row_key,action,after_json,actor)
               VALUES (%s,%s,%s,%s::jsonb,%s)""",
            (
                table_name,
                row_key,
                action,
                json.dumps(after, sort_keys=True, separators=(",", ":"), default=str),
                actor,
            ),
        )


def _inject(stage: str, requested: str | None) -> None:
    if requested == stage:
        raise RuntimeError(f"injected terminal persistence failure at {stage}")


def dry_run_terminal_disposition(
    conn: Any,
    artifact: TerminalArtifact,
    context: TerminalExecutionContext,
) -> dict[str, Any]:
    """Inspect one locked PostgreSQL snapshot and prove it assigned no XID."""

    _validate_artifact_instance(artifact)
    execution = derive_runtime_execution_git_identity(
        context.expected_execution_git_sha
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            cur.execute(
                """SELECT current_setting('transaction_read_only'),
                          current_setting('transaction_isolation'),
                          txid_current_if_assigned()"""
            )
            read_only, isolation, xid_before = cur.fetchone()
        if read_only != "on" or str(isolation).lower() != "repeatable read":
            raise RuntimeError("terminal dry-run transaction is not read-only snapshot")
        if xid_before is not None:
            raise RuntimeError("terminal dry-run assigned an XID before inspection")
        acquire_backfill_transaction_lock(conn)
        report = inspect_terminal_state(conn, artifact, execution.git_sha)
        with conn.cursor() as cur:
            cur.execute("SELECT txid_current_if_assigned()")
            xid_after = cur.fetchone()[0]
        if xid_after is not None:
            raise RuntimeError("terminal dry-run assigned an XID during inspection")
        return {
            "execution_git_sha": execution.git_sha,
            "transaction_read_only": True,
            "transaction_isolation": "repeatable read",
            "xid_before": None,
            "xid_after": None,
            "database_dml": 0,
            **report,
        }


def persist_terminal_disposition(
    conn: Any,
    artifact: TerminalArtifact,
    context: TerminalExecutionContext,
    *,
    locked_precondition: Callable[[Any], None] | None = None,
    inject_failure_stage: str | None = None,
) -> dict[str, Any]:
    """Persist only terminal catalog/decision artifacts in one transaction."""

    _validate_artifact_instance(artifact)
    execution = derive_runtime_execution_git_identity(
        context.expected_execution_git_sha
    )
    result: dict[str, Any]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            cur.execute("SELECT current_setting('transaction_isolation')")
            if str(cur.fetchone()[0]).lower() != "serializable":
                raise RuntimeError("terminal transaction isolation is not serializable")
        acquire_backfill_transaction_lock(conn)
        if locked_precondition is not None:
            locked_precondition(conn)
        before = inspect_terminal_state(conn, artifact, execution.git_sha)
        classification = before["classification"]
        if classification == "CONFLICT":
            raise TerminalValidationError(
                "terminal database state is CONFLICT: "
                + ",".join(before["diagnostics"])
            )
        if classification == "CURRENT_TERMINAL_EXACT":
            result = {
                "classification_before": classification,
                "classification_after": classification,
                "execution_git_sha": execution.git_sha,
                "committed_mutations": 0,
                "planned_mutations": before["planned_mutations"],
                "protected_fingerprints_before": before["protected_fingerprints"],
                "protected_fingerprints_after": before["protected_fingerprints"],
            }
            return result

        fingerprints_before = before["protected_fingerprints"]
        latest_ids = before["latest_decision_ids"]
        restore_rows = sorted(
            (row for row in artifact.rows if row.action == "RESTORE"),
            key=lambda row: row.source_identity_key,
        )
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO variants(
                     variant_id,product_id,product_title,variant_title,sku,active,
                     catalog_state,identity_scope,catalog_resolution_note,
                     restoration_manifest_sha256,restoration_manifest_row_number,
                     restoration_evidence_version,restoration_owner_authorization,
                     restoration_authority_git_sha,restoration_execution_git_sha
                   ) VALUES (%s,NULL,%s,%s,%s,FALSE,'RETIRED_CONFIRMED',
                             'HISTORICAL_ONLY',%s,%s,%s,%s,%s,%s,%s)""",
                [
                    (
                        row.source_variant_id,
                        row.historical_product_title,
                        row.historical_variant_title,
                        row.historical_sku,
                        "Owner-approved restoration of exact deleted historical Variant identity.",
                        TERMINAL_MANIFEST_SHA256,
                        row.row_number,
                        TERMINAL_EVIDENCE_VERSION,
                        TERMINAL_OWNER_AUTHORIZATION,
                        AUTHORITY_GIT_SHA,
                        execution.git_sha,
                    )
                    for row in restore_rows
                ],
            )
        _inject("after_restorations", inject_failure_stage)

        latest_terminal_decision_ids: dict[str, str] = {}
        for row in sorted(artifact.rows, key=lambda item: item.source_identity_key):
            evidence = _terminal_decision_evidence(row, execution.git_sha)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO historical_sales_review_decisions(
                         sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                         source_product_title,source_variant_title,decision_action,
                         canonical_variant_id,actor,reason,evidence_json,
                         supersedes_decision_id,decision_schema_version,reason_code,
                         primary_manifest_sha256,primary_manifest_row_number,
                         evidence_version,owner_authorization,authority_git_sha,
                         execution_git_sha
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
                                 %s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING historical_sales_review_decision_id""",
                    (
                        APPROVED_RUN_ID,
                        row.source_identity_key,
                        row.source_variant_id,
                        row.historical_sku,
                        row.historical_product_title or None,
                        row.historical_variant_title or None,
                        row.action,
                        row.canonical_variant_id,
                        context.actor,
                        row.owner_approval_basis,
                        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                        latest_ids[row.source_identity_key],
                        TERMINAL_DECISION_SCHEMA_VERSION,
                        row.exclusion_reason_code,
                        TERMINAL_MANIFEST_SHA256,
                        row.row_number,
                        TERMINAL_EVIDENCE_VERSION,
                        TERMINAL_OWNER_AUTHORIZATION,
                        AUTHORITY_GIT_SHA,
                        execution.git_sha,
                    ),
                )
                decision_id = str(cur.fetchone()[0])
            latest_terminal_decision_ids[row.source_identity_key] = decision_id
            _insert_change_log(
                conn,
                table_name="historical_sales_review_decisions",
                row_key=row.source_identity_key,
                action="SUPERSEDE",
                after={**evidence, "decision_id": decision_id},
                actor=context.actor,
            )

        original_exclusions = sorted(
            (
                row
                for row in artifact.original_manifest.rows
                if row.review_disposition == "EXCLUDE"
            ),
            key=lambda row: row.source_identity_key,
        )
        for row in original_exclusions:
            evidence = _original_exclusion_evidence(row, execution.git_sha)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO historical_sales_review_decisions(
                         sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                         source_product_title,source_variant_title,decision_action,
                         canonical_variant_id,actor,reason,evidence_json,
                         supersedes_decision_id,decision_schema_version,reason_code,
                         primary_manifest_sha256,primary_manifest_row_number,
                         evidence_version,owner_authorization,authority_git_sha,
                         execution_git_sha
                       ) VALUES (%s,%s,%s,%s,%s,%s,'EXCLUDE',NULL,%s,%s,%s::jsonb,%s,
                                 %s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING historical_sales_review_decision_id""",
                    (
                        APPROVED_RUN_ID,
                        row.source_identity_key,
                        row.source_variant_id,
                        row.source_sku,
                        row.historical_product_title or None,
                        row.historical_variant_title or None,
                        context.actor,
                        row.review_note,
                        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                        latest_ids[row.source_identity_key],
                        TERMINAL_DECISION_SCHEMA_VERSION,
                        ORIGINAL_EXCLUSION_REASON,
                        APPROVED_MANIFEST_SHA256,
                        row.row_number,
                        TERMINAL_EVIDENCE_VERSION,
                        TERMINAL_OWNER_AUTHORIZATION,
                        AUTHORITY_GIT_SHA,
                        execution.git_sha,
                    ),
                )
                decision_id = str(cur.fetchone()[0])
            latest_terminal_decision_ids[row.source_identity_key] = decision_id
            _insert_change_log(
                conn,
                table_name="historical_sales_review_decisions",
                row_key=row.source_identity_key,
                action="SUPERSEDE",
                after={**evidence, "decision_id": decision_id},
                actor=context.actor,
            )
        _inject("after_decisions", inject_failure_stage)

        pre_aliases = _terminal_safe_alias_families(artifact, terminal=False)
        current_aliases = _terminal_safe_alias_families(artifact, terminal=True)
        terminal_aliases = {
            old_id: target
            for old_id, target in current_aliases.items()
            if old_id not in pre_aliases
        }
        rows_by_variant_id: dict[str, list[TerminalDispositionRow]] = defaultdict(list)
        for row in artifact.rows:
            if row.source_variant_id:
                rows_by_variant_id[row.source_variant_id].append(row)
        with conn.cursor() as cur:
            for old_id, target in sorted(terminal_aliases.items()):
                evidence_rows = sorted(
                    rows_by_variant_id[old_id], key=lambda row: row.source_identity_key
                )
                cur.execute(
                    """INSERT INTO variant_aliases(
                         variant_id,old_variant_id,historical_product_title,
                         historical_variant_title,historical_sku,match_method,
                         confidence,source,notes,approved,approved_by,approved_at,
                         evidence_json
                       ) VALUES (%s,%s,%s,%s,%s,
                                 'OWNER_APPROVED_TERMINAL_CONTINUITY',1.0,
                                 'PHASE4_TERMINAL_DISPOSITION',%s,TRUE,%s,now(),%s::jsonb)""",
                    (
                        target,
                        old_id,
                        evidence_rows[0].historical_product_title,
                        evidence_rows[0].historical_variant_title or None,
                        evidence_rows[0].historical_sku,
                        "Complete old-ID family agrees on one owner-approved canonical target.",
                        context.actor,
                        json.dumps(
                            {
                                "terminal_manifest_sha256": TERMINAL_MANIFEST_SHA256,
                                "canonical_variant_id": target,
                                "evidence_version": TERMINAL_EVIDENCE_VERSION,
                                "owner_authorization": TERMINAL_OWNER_AUTHORIZATION,
                                "authority_git_sha": AUTHORITY_GIT_SHA,
                                "execution_git_sha": execution.git_sha,
                                "broad_family": before[
                                    "broad_alias_family_evidence"
                                ][old_id],
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
        _inject("after_aliases", inject_failure_stage)

        original_by_key = {
            row.source_identity_key: row for row in artifact.original_manifest.rows
        }
        terminal_by_key = {row.source_identity_key: row for row in artifact.rows}
        exclusion_keys = sorted(
            set(EXCLUSION_SOURCE_KEYS)
            | {row.source_identity_key for row in artifact.rows if row.action == "EXCLUDE"}
        )
        with conn.cursor() as cur:
            for key in exclusion_keys:
                if key in EXCLUSION_SOURCE_KEYS:
                    row = original_by_key[key]
                    source_variant_id = row.source_variant_id
                    source_sku = row.source_sku
                    product_title = row.historical_product_title or None
                    variant_title = row.historical_variant_title or None
                    reason_code = ORIGINAL_EXCLUSION_REASON
                else:
                    row = terminal_by_key[key]
                    source_variant_id = row.source_variant_id
                    source_sku = row.historical_sku
                    product_title = row.historical_product_title or None
                    variant_title = row.historical_variant_title or None
                    reason_code = TERMINAL_EXCLUSION_REASON
                cur.execute(
                    """INSERT INTO historical_sales_exclusions(
                         source_key,source_variant_id,source_sku,source_product_title,
                         source_variant_title,reason,approved_by,approved_at,active,
                         reason_code,effective_decision_id
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,now(),TRUE,%s,%s)
                       ON CONFLICT(source_key) DO UPDATE SET
                         source_variant_id=EXCLUDED.source_variant_id,
                         source_sku=EXCLUDED.source_sku,
                         source_product_title=EXCLUDED.source_product_title,
                         source_variant_title=EXCLUDED.source_variant_title,
                         reason=EXCLUDED.reason,approved_by=EXCLUDED.approved_by,
                         approved_at=now(),active=TRUE,
                         reason_code=EXCLUDED.reason_code,
                         effective_decision_id=EXCLUDED.effective_decision_id""",
                    (
                        key,
                        source_variant_id,
                        source_sku,
                        product_title,
                        variant_title,
                        reason_code,
                        context.actor,
                        reason_code,
                        latest_terminal_decision_ids[key],
                    ),
                )
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO historical_sales_exclusion_authority_runs(
                     sales_backfill_id,authority_version,decision_authority_run_id,
                     original_manifest_sha256,terminal_manifest_sha256,
                     decision_schema_version,evidence_version,owner_authorization,
                     authority_git_sha,execution_git_sha,registered_by
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    APPROVED_RUN_ID,
                    TERMINAL_AUTHORITY_VERSION,
                    APPROVED_RUN_ID,
                    APPROVED_MANIFEST_SHA256,
                    TERMINAL_MANIFEST_SHA256,
                    TERMINAL_DECISION_SCHEMA_VERSION,
                    TERMINAL_EVIDENCE_VERSION,
                    TERMINAL_OWNER_AUTHORIZATION,
                    AUTHORITY_GIT_SHA,
                    execution.git_sha,
                    context.actor,
                ),
            )
        _insert_change_log(
            conn,
            table_name="historical_sales_exclusion_authority_runs",
            row_key=APPROVED_RUN_ID,
            action="INSERT",
            after={
                "authority_version": TERMINAL_AUTHORITY_VERSION,
                "decision_authority_run_id": APPROVED_RUN_ID,
                "original_manifest_sha256": APPROVED_MANIFEST_SHA256,
                "terminal_manifest_sha256": TERMINAL_MANIFEST_SHA256,
                "decision_schema_version": TERMINAL_DECISION_SCHEMA_VERSION,
                "evidence_version": TERMINAL_EVIDENCE_VERSION,
                "owner_authorization": TERMINAL_OWNER_AUTHORIZATION,
                "authority_git_sha": AUTHORITY_GIT_SHA,
                "execution_git_sha": execution.git_sha,
            },
            actor=context.actor,
        )
        _inject("after_exclusions", inject_failure_stage)
        _inject("before_readback", inject_failure_stage)

        after = inspect_terminal_state(conn, artifact, execution.git_sha)
        if after["classification"] != "CURRENT_TERMINAL_EXACT":
            raise TerminalValidationError(
                "terminal post-write readback is not CURRENT_TERMINAL_EXACT: "
                + ",".join(after["diagnostics"])
            )
        fingerprints_after = after["protected_fingerprints"]
        if fingerprints_after != fingerprints_before:
            raise RuntimeError("protected sales, resolution, gate, or PO state changed")
        committed_mutations = 43 + 280 + 8 + len(terminal_aliases) + 198 + 288 + 2
        result = {
            "classification_before": classification,
            "classification_after": after["classification"],
            "execution_git_sha": execution.git_sha,
            "committed_mutations": committed_mutations,
            "planned_mutations": before["planned_mutations"],
            "protected_fingerprints_before": fingerprints_before,
            "protected_fingerprints_after": fingerprints_after,
            "readback": after,
        }
    return result
