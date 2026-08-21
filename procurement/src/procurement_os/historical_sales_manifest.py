"""Fail-closed persistence for the owner-approved Phase 4 identity manifest.

This module writes only review decisions, their exact exclusions, safe uniform
old-ID aliases, and append-only audit records. It intentionally has no sales
re-resolution, readiness evaluation, Shopify, forecasting, procurement, or PO
execution dependency.
"""
from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .historical_sales import acquire_backfill_transaction_lock, latest_reviewable_run
from .sales import (
    CurrentIdentity,
    HistoricalAlias,
    HistoricalIdentityIndex,
    SalesSourceRow,
)


APPROVED_MANIFEST_SHA256 = (
    "95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287"
)
APPROVED_RUN_ID = "d389079c-eabf-49b5-a245-40a207025fd7"
OWNER_AUTHORIZATION_ID = "PHASE4_IDENTITY_DECISION_PERSISTENCE_2026-08-20"
OWNER_AUTHORIZATION_VERSION = "1"
FIESTA_CANONICAL_VARIANT_ID = "41193000796235"
NUTRL_CANONICAL_VARIANT_ID = "41716813627467"

EXPECTED_HEADERS = (
    "source_identity_key",
    "historical_product_title",
    "historical_variant_title",
    "material",
    "affected_raw_rows",
    "absolute_unit_magnitude",
    "absolute_sales_magnitude",
    "review_disposition",
    "canonical_variant_id",
    "canonical_product_title",
    "canonical_variant_title",
    "evidence_basis",
    "review_note",
)

EXCLUSION_SOURCE_KEYS = frozenset(
    {
        "0||DELIVERY FEE|",
        "0||SHIPPING FEES|",
        "0||TIP|",
        "||TIP|",
        "0||BUFFALO HOUSE GIFT CARD|BUFFALO HOUSE GIFT CARD",
        "||BUFFALO HOUSE GIFT CARD|BUFFALO HOUSE GIFT CARD",
        "41173357133899||BUFFALO HOUSE GIFT CARD|10.00",
        "|||",
    }
)

NUTRL_SOURCE_KEYS = frozenset(
    {
        "41157780111435||NUTRL FRUIT VARIETY 12 PACK|12 OZ",
        "41157780111435||NUTRL FRUIT VARIETY 12 PACK|8 PACK",
        "41157780111435||NUTRL FRUIT VARIETY PACK|8 PACK",
    }
)

HIGH_NOON_TEQUILA_SOURCE_KEYS = frozenset(
    {
        "0|3012337|HIGH NOON TEQUILA VARIETY PACK|12OZ",
        "0|3014701|HIGH NOON TEQUILA VARIETY 8 PACK|12OZ",
        "0|3014701|HIGH NOON TEQUILA VARIETY 8 PACK|8PK 12OZ CANS",
    }
)

EXPECTED_CONTROL_VALUES = {
    "rows": 343,
    "unique_source_identity_keys": 343,
    "material": 341,
    "nonmaterial": 2,
    "affected_raw_rows": 3112,
    "map": 55,
    "exclude": 8,
    "leave_unresolved": 280,
    "flag_owner": 0,
    "map_targets_populated": 55,
    "nonmap_targets_populated": 0,
    "distinct_map_targets": 51,
    "absolute_unit_magnitude": Decimal("3696.0000"),
    "absolute_sales_magnitude": Decimal("72616.29"),
}

DECISION_STATES = (
    "CONFLICT",
    "CURRENT_PROVENANCE",
    "LEGACY_COMPATIBLE",
    "MISSING",
)


class ManifestValidationError(ValueError):
    """The immutable manifest or one of its approved controls drifted."""


def _json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _optional(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _parse_decimal(value: str | None, *, field: str, row_number: int) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ManifestValidationError(
            f"manifest row {row_number} has invalid {field}"
        ) from exc
    if not parsed.is_finite() or parsed < 0:
        raise ManifestValidationError(
            f"manifest row {row_number} has invalid {field}"
        )
    return parsed


@dataclass(frozen=True)
class ManifestRow:
    row_number: int
    source_identity_key: str
    historical_product_title: str
    historical_variant_title: str
    material: bool
    affected_raw_rows: int
    absolute_unit_magnitude: Decimal
    absolute_sales_magnitude: Decimal
    review_disposition: str
    canonical_variant_id: str | None
    canonical_product_title: str | None
    canonical_variant_title: str | None
    evidence_basis: str
    review_note: str

    @property
    def source_variant_id(self) -> str | None:
        return self.source_identity_key.split("|", 3)[0] or None

    @property
    def source_sku(self) -> str | None:
        return self.source_identity_key.split("|", 3)[1] or None

    @property
    def stored_action(self) -> str:
        return {
            "MAP": "MAP",
            "EXCLUDE": "EXCLUDE",
            "LEAVE_UNRESOLVED": "LEAVE_UNRESOLVED",
        }[self.review_disposition]


@dataclass(frozen=True)
class ManifestControls:
    rows: int
    unique_source_identity_keys: int
    material: int
    nonmaterial: int
    affected_raw_rows: int
    map: int
    exclude: int
    leave_unresolved: int
    flag_owner: int
    map_targets_populated: int
    nonmap_targets_populated: int
    distinct_map_targets: int
    absolute_unit_magnitude: Decimal
    absolute_sales_magnitude: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "affected_raw_rows": self.affected_raw_rows,
            "absolute_sales_magnitude": str(self.absolute_sales_magnitude),
            "absolute_unit_magnitude": str(self.absolute_unit_magnitude),
            "distinct_map_targets": self.distinct_map_targets,
            "exclude": self.exclude,
            "flag_owner": self.flag_owner,
            "leave_unresolved": self.leave_unresolved,
            "map": self.map,
            "map_targets_populated": self.map_targets_populated,
            "material": self.material,
            "nonmap_targets_populated": self.nonmap_targets_populated,
            "nonmaterial": self.nonmaterial,
            "rows": self.rows,
            "unique_source_identity_keys": self.unique_source_identity_keys,
        }


@dataclass(frozen=True)
class AuthorizedManifest:
    raw_bytes: bytes
    sha256: str
    run_id: str
    rows: tuple[ManifestRow, ...]
    controls: ManifestControls


@dataclass(frozen=True)
class ManifestExecutionContext:
    actor: str
    implementation_git_sha: str
    authorization_id: str = OWNER_AUTHORIZATION_ID
    authorization_version: str = OWNER_AUTHORIZATION_VERSION

    def __post_init__(self) -> None:
        actor = str(self.actor).strip()
        git_sha = str(self.implementation_git_sha).strip().lower()
        if not actor:
            raise ValueError("manifest execution actor is required")
        if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
            raise ValueError("implementation Git SHA must be a full 40-character SHA")
        if not str(self.authorization_id).strip() or not str(
            self.authorization_version
        ).strip():
            raise ValueError("owner authorization provenance is required")
        object.__setattr__(self, "actor", actor)
        object.__setattr__(self, "implementation_git_sha", git_sha)


@dataclass(frozen=True)
class ReviewSourceSnapshot:
    source_identity_key: str
    source_variant_id: str | None
    source_sku: str | None
    source_product_title: str | None
    source_variant_title: str | None
    affected_raw_rows: int
    absolute_unit_magnitude: Decimal
    absolute_sales_magnitude: Decimal
    material: bool


@dataclass(frozen=True)
class ExistingDecision:
    decision_id: str
    sales_backfill_id: str | None
    source_identity_key: str
    decision_action: str
    canonical_variant_id: str | None
    actor: str
    reason: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class DecisionClassification:
    source_identity_key: str
    state: str
    existing: ExistingDecision | None


@dataclass(frozen=True)
class DatabasePreflight:
    snapshot: Mapping[str, ReviewSourceSnapshot]
    classifications: tuple[DecisionClassification, ...]
    alias_insert_families: Mapping[str, str]
    exclusion_mutations: tuple[str, ...]

    @property
    def decision_state_counts(self) -> dict[str, int]:
        counter = Counter(item.state for item in self.classifications)
        return {state: counter.get(state, 0) for state in DECISION_STATES}


def read_manifest_bytes(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def parse_manifest(raw_bytes: bytes) -> tuple[ManifestRow, ...]:
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestValidationError("manifest must be valid UTF-8") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if tuple(reader.fieldnames or ()) != EXPECTED_HEADERS:
            raise ManifestValidationError("manifest header does not match approved contract")
        rows: list[ManifestRow] = []
        for row_number, raw in enumerate(reader, start=1):
            if None in raw or set(raw) != set(EXPECTED_HEADERS):
                raise ManifestValidationError(
                    f"manifest row {row_number} does not match the approved header"
                )
            source_key = str(raw["source_identity_key"] or "")
            if not source_key or len(source_key.split("|")) != 4:
                raise ManifestValidationError(
                    f"manifest row {row_number} has invalid source_identity_key"
                )
            material_text = str(raw["material"] or "").strip().lower()
            if material_text not in {"true", "false"}:
                raise ManifestValidationError(
                    f"manifest row {row_number} has invalid material flag"
                )
            try:
                affected_raw_rows = int(str(raw["affected_raw_rows"]))
            except ValueError as exc:
                raise ManifestValidationError(
                    f"manifest row {row_number} has invalid affected_raw_rows"
                ) from exc
            if affected_raw_rows <= 0:
                raise ManifestValidationError(
                    f"manifest row {row_number} has invalid affected_raw_rows"
                )
            rows.append(
                ManifestRow(
                    row_number=row_number,
                    source_identity_key=source_key,
                    historical_product_title=str(raw["historical_product_title"] or ""),
                    historical_variant_title=str(raw["historical_variant_title"] or ""),
                    material=material_text == "true",
                    affected_raw_rows=affected_raw_rows,
                    absolute_unit_magnitude=_parse_decimal(
                        raw["absolute_unit_magnitude"],
                        field="absolute_unit_magnitude",
                        row_number=row_number,
                    ),
                    absolute_sales_magnitude=_parse_decimal(
                        raw["absolute_sales_magnitude"],
                        field="absolute_sales_magnitude",
                        row_number=row_number,
                    ),
                    review_disposition=str(raw["review_disposition"] or "").strip(),
                    canonical_variant_id=_optional(raw["canonical_variant_id"]),
                    canonical_product_title=_optional(raw["canonical_product_title"]),
                    canonical_variant_title=_optional(raw["canonical_variant_title"]),
                    evidence_basis=str(raw["evidence_basis"] or "").strip(),
                    review_note=str(raw["review_note"] or "").strip(),
                )
            )
    except csv.Error as exc:
        raise ManifestValidationError("manifest CSV is malformed") from exc
    return tuple(rows)


def manifest_controls(rows: Iterable[ManifestRow]) -> ManifestControls:
    rows = tuple(rows)
    actions = Counter(row.review_disposition for row in rows)
    map_targets = [
        row.canonical_variant_id
        for row in rows
        if row.review_disposition == "MAP" and row.canonical_variant_id
    ]
    return ManifestControls(
        rows=len(rows),
        unique_source_identity_keys=len({row.source_identity_key for row in rows}),
        material=sum(row.material for row in rows),
        nonmaterial=sum(not row.material for row in rows),
        affected_raw_rows=sum(row.affected_raw_rows for row in rows),
        map=actions.get("MAP", 0),
        exclude=actions.get("EXCLUDE", 0),
        leave_unresolved=actions.get("LEAVE_UNRESOLVED", 0),
        flag_owner=actions.get("FLAG_OWNER", 0),
        map_targets_populated=len(map_targets),
        nonmap_targets_populated=sum(
            row.review_disposition != "MAP" and row.canonical_variant_id is not None
            for row in rows
        ),
        distinct_map_targets=len(set(map_targets)),
        absolute_unit_magnitude=sum(
            (row.absolute_unit_magnitude for row in rows), Decimal("0")
        ),
        absolute_sales_magnitude=sum(
            (row.absolute_sales_magnitude for row in rows), Decimal("0")
        ),
    )


def validate_manifest_rows(rows: Iterable[ManifestRow]) -> ManifestControls:
    rows = tuple(rows)
    keys = [row.source_identity_key for row in rows]
    duplicate_keys = sorted(key for key, count in Counter(keys).items() if count > 1)
    if duplicate_keys:
        raise ManifestValidationError(
            f"duplicate source_identity_key: {duplicate_keys[0]}"
        )

    allowed = {"MAP", "EXCLUDE", "LEAVE_UNRESOLVED"}
    for row in rows:
        if row.review_disposition not in allowed:
            raise ManifestValidationError(
                f"unknown review disposition for {row.source_identity_key}"
            )
        if not row.evidence_basis or not row.review_note:
            raise ManifestValidationError(
                f"manifest evidence is incomplete for {row.source_identity_key}"
            )
        if row.review_disposition == "MAP" and not row.canonical_variant_id:
            raise ManifestValidationError(
                f"MAP row requires canonical target: {row.source_identity_key}"
            )
        if row.review_disposition != "MAP" and row.canonical_variant_id is not None:
            raise ManifestValidationError(
                f"non-MAP row forbids canonical target: {row.source_identity_key}"
            )

    exclusions = {
        row.source_identity_key for row in rows if row.review_disposition == "EXCLUDE"
    }
    if exclusions != EXCLUSION_SOURCE_KEYS:
        raise ManifestValidationError("manifest exclusion set differs from owner approval")

    if any(row.canonical_variant_id == FIESTA_CANONICAL_VARIANT_ID for row in rows):
        raise ManifestValidationError("High Noon Fiesta canonical target is forbidden")

    nutrl = {row.source_identity_key: row for row in rows if row.source_identity_key in NUTRL_SOURCE_KEYS}
    if set(nutrl) != NUTRL_SOURCE_KEYS or any(
        row.review_disposition != "MAP"
        or row.canonical_variant_id != NUTRL_CANONICAL_VARIANT_ID
        for row in nutrl.values()
    ):
        raise ManifestValidationError("NUTRL owner exception does not match exact 3/3 approval")
    if {
        row.source_identity_key
        for row in rows
        if row.canonical_variant_id == NUTRL_CANONICAL_VARIANT_ID
    } != NUTRL_SOURCE_KEYS:
        raise ManifestValidationError("NUTRL owner exception leaked beyond its exact source keys")

    high_noon = {
        row.source_identity_key: row
        for row in rows
        if row.source_identity_key in HIGH_NOON_TEQUILA_SOURCE_KEYS
    }
    if set(high_noon) != HIGH_NOON_TEQUILA_SOURCE_KEYS or any(
        row.review_disposition != "LEAVE_UNRESOLVED" for row in high_noon.values()
    ):
        raise ManifestValidationError(
            "High Noon Tequila Variety must remain unresolved for all 3 source keys"
        )

    controls = manifest_controls(rows)
    actual = {
        **controls.as_dict(),
        "absolute_unit_magnitude": controls.absolute_unit_magnitude,
        "absolute_sales_magnitude": controls.absolute_sales_magnitude,
    }
    for field, expected in EXPECTED_CONTROL_VALUES.items():
        if actual[field] != expected:
            raise ManifestValidationError(
                f"manifest control {field}={actual[field]} expected {expected}"
            )
    return controls


def validate_static_manifest(
    raw_bytes: bytes, rows: Iterable[ManifestRow]
) -> ManifestControls:
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if sha256 != APPROVED_MANIFEST_SHA256:
        raise ManifestValidationError(
            f"manifest SHA-256 mismatch: {sha256}"
        )
    return validate_manifest_rows(rows)


def load_authorized_manifest(path: str | Path) -> AuthorizedManifest:
    raw_bytes = read_manifest_bytes(path)
    rows = parse_manifest(raw_bytes)
    controls = validate_static_manifest(raw_bytes, rows)
    return AuthorizedManifest(
        raw_bytes=raw_bytes,
        sha256=APPROVED_MANIFEST_SHA256,
        run_id=APPROVED_RUN_ID,
        rows=rows,
        controls=controls,
    )


def require_review_authorization(
    configured_token: str | None, supplied_token: str | None
) -> None:
    if not configured_token:
        raise PermissionError("reconciliation review authorization is not configured")
    if not supplied_token or not hmac.compare_digest(
        str(supplied_token), str(configured_token)
    ):
        raise PermissionError("invalid reconciliation review authorization")


def _canonical_source_key(
    source_variant_id: str | None,
    source_sku: str | None,
    source_product_title: str | None,
    source_variant_title: str | None,
) -> str:
    return HistoricalIdentityIndex.source_key(
        SalesSourceRow(
            date(1970, 1, 1),
            source_variant_id,
            source_sku,
            source_product_title,
            source_variant_title,
            Decimal("0"),
            Decimal("0"),
        )
    )


def load_review_source_snapshot(
    conn: Any, run_id: str
) -> dict[str, ReviewSourceSnapshot]:
    with conn.cursor() as cur:
        current = latest_reviewable_run(cur)
        if current is None:
            raise ValueError("no complete reviewable historical-sales run exists")
        if current[0] != run_id:
            raise ValueError(
                f"latest reviewable run {current[0]} differs from approved run {run_id}"
            )
        cur.execute(
            """SELECT status,source,raw_rows,resolved_rows,unresolved_rows,
                      ambiguous_rows,unique_source_facts,coverage_complete,pages_complete,
                      source_facts_persisted,idempotency_verified,
                      control_totals_reconciled,canonical_aggregate_rebuilt
               FROM sales_backfill_runs WHERE sales_backfill_id=%s""",
            (run_id,),
        )
        run = cur.fetchone()
        if run is None or run != (
            "COMPLETED",
            "SHOPIFYQL_SALES",
            59083,
            55971,
            3112,
            0,
            59083,
            True,
            True,
            True,
            True,
            True,
            True,
        ):
            raise ValueError("approved review run durable controls drifted")
        cur.execute(
            """SELECT r.raw_sales_id,r.source_identity_key,
                      r.source_variant_id,r.source_sku,
                      r.source_product_title,r.source_variant_title
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s
                 AND r.resolution_status IN ('UNRESOLVED','AMBIGUOUS')
               ORDER BY r.source_identity_key,r.raw_sales_id""",
            (run_id,),
        )
        for evidence in cur.fetchall():
            stored_key = str(evidence[1])
            computed_key = _canonical_source_key(
                str(evidence[2]) if evidence[2] is not None else None,
                str(evidence[3]) if evidence[3] is not None else None,
                str(evidence[4]) if evidence[4] is not None else None,
                str(evidence[5]) if evidence[5] is not None else None,
            )
            if computed_key != stored_key:
                raise ValueError(
                    f"canonical review group drift for {stored_key}: "
                    f"raw evidence recomputes to {computed_key}"
                )
        cur.execute(
            """SELECT r.source_identity_key,
                      MIN(r.source_variant_id),MIN(r.source_sku),
                      MIN(r.source_product_title),MIN(r.source_variant_title),
                      COUNT(*)::int,
                      COALESCE(SUM(ABS(rf.observed_net_items_sold)),0),
                      COALESCE(SUM(ABS(rf.observed_net_sales)),0)
               FROM sales_backfill_run_facts rf
               JOIN shopify_sales_daily_raw r ON r.raw_sales_id=rf.raw_sales_id
               WHERE rf.sales_backfill_id=%s
                 AND r.resolution_status IN ('UNRESOLVED','AMBIGUOUS')
               GROUP BY r.source_identity_key
               ORDER BY r.source_identity_key""",
            (run_id,),
        )
        result: dict[str, ReviewSourceSnapshot] = {}
        for row in cur.fetchall():
            units = Decimal(str(row[6]))
            sales = Decimal(str(row[7]))
            result[str(row[0])] = ReviewSourceSnapshot(
                source_identity_key=str(row[0]),
                source_variant_id=str(row[1]) if row[1] is not None else None,
                source_sku=str(row[2]) if row[2] is not None else None,
                source_product_title=str(row[3]) if row[3] is not None else None,
                source_variant_title=str(row[4]) if row[4] is not None else None,
                affected_raw_rows=int(row[5]),
                absolute_unit_magnitude=units,
                absolute_sales_magnitude=sales,
                material=units != 0 or sales != 0,
            )
    return result


def load_latest_effective_decisions(
    conn: Any, source_keys: Iterable[str]
) -> dict[str, ExistingDecision]:
    keys = sorted(set(source_keys))
    if not keys:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """WITH ranked AS (
                 SELECT historical_sales_review_decision_id,sales_backfill_id,
                        source_identity_key,decision_action,canonical_variant_id,
                        actor,reason,evidence_json,
                        ROW_NUMBER() OVER (
                          PARTITION BY source_identity_key
                          ORDER BY decided_at DESC,
                                   historical_sales_review_decision_id DESC
                        ) AS row_number
                 FROM historical_sales_review_decisions
                 WHERE source_identity_key=ANY(%s)
               )
               SELECT historical_sales_review_decision_id,sales_backfill_id,
                      source_identity_key,decision_action,canonical_variant_id,
                      actor,reason,evidence_json
               FROM ranked WHERE row_number=1
               ORDER BY source_identity_key""",
            (keys,),
        )
        decisions = {}
        for row in cur.fetchall():
            evidence = row[7] if isinstance(row[7], dict) else {}
            decisions[str(row[2])] = ExistingDecision(
                decision_id=str(row[0]),
                sales_backfill_id=str(row[1]) if row[1] is not None else None,
                source_identity_key=str(row[2]),
                decision_action=str(row[3]),
                canonical_variant_id=str(row[4]) if row[4] is not None else None,
                actor=str(row[5]),
                reason=str(row[6]),
                evidence=evidence,
            )
    return decisions


def _provenance_complete(
    decision: ExistingDecision, row: ManifestRow
) -> bool:
    evidence = decision.evidence
    expected = {
        "manifest_sha256": APPROVED_MANIFEST_SHA256,
        "manifest_row_number": row.row_number,
        "manifest_disposition": row.review_disposition,
        "stored_decision_action": row.stored_action,
        "evidence_basis": row.evidence_basis,
        "review_note": row.review_note,
        "production_run_id": APPROVED_RUN_ID,
        "source_identity_key": row.source_identity_key,
        "source_variant_id": row.source_variant_id,
        "source_sku": row.source_sku,
        "source_product_title": row.historical_product_title or None,
        "source_variant_title": row.historical_variant_title or None,
        "canonical_variant_id": row.canonical_variant_id,
        "owner_authorization_id": OWNER_AUTHORIZATION_ID,
        "owner_authorization_version": OWNER_AUTHORIZATION_VERSION,
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        return False
    return bool(
        re.fullmatch(
            r"[0-9a-f]{40}",
            str(evidence.get("implementation_git_sha") or "").lower(),
        )
    )


def classify_existing_decisions(
    manifest: AuthorizedManifest,
    existing: Mapping[str, ExistingDecision],
) -> tuple[DecisionClassification, ...]:
    classifications: list[DecisionClassification] = []
    for row in sorted(manifest.rows, key=lambda item: item.source_identity_key):
        decision = existing.get(row.source_identity_key)
        if decision is None:
            state = "MISSING"
        elif (
            decision.sales_backfill_id != manifest.run_id
            or decision.decision_action != row.stored_action
            or decision.canonical_variant_id != row.canonical_variant_id
        ):
            state = "CONFLICT"
        elif _provenance_complete(decision, row):
            state = "CURRENT_PROVENANCE"
        else:
            state = "LEGACY_COMPATIBLE"
        classifications.append(
            DecisionClassification(row.source_identity_key, state, decision)
        )
    return tuple(classifications)


def _load_base_identity_index(conn: Any) -> HistoricalIdentityIndex:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT variant_id,sku,product_title,variant_title,active,catalog_state
               FROM variants"""
        )
        current = [
            CurrentIdentity(
                str(row[0]),
                row[1],
                row[2] or "",
                row[3] or "",
                bool(row[4]),
                row[5] or "SEEDED",
            )
            for row in cur.fetchall()
        ]
        cur.execute(
            """SELECT variant_id,old_variant_id,historical_sku,
                      historical_product_title,historical_variant_title,approved
               FROM variant_aliases"""
        )
        aliases = [
            HistoricalAlias(
                str(row[0]), row[1], row[2], row[3], row[4], bool(row[5])
            )
            for row in cur.fetchall()
        ]
    return HistoricalIdentityIndex(current, aliases)


def _validate_snapshot_matches_manifest(
    manifest: AuthorizedManifest,
    snapshot: Mapping[str, ReviewSourceSnapshot],
) -> None:
    manifest_keys = {row.source_identity_key for row in manifest.rows}
    snapshot_keys = set(snapshot)
    missing = sorted(manifest_keys - snapshot_keys)
    unknown = sorted(snapshot_keys - manifest_keys)
    if missing:
        raise ValueError(f"missing manifest source key in database: {missing[0]}")
    if unknown:
        raise ValueError(f"unknown database source key outside manifest: {unknown[0]}")
    for row in manifest.rows:
        source = snapshot[row.source_identity_key]
        manifest_display_key = _canonical_source_key(
            row.source_variant_id,
            row.source_sku,
            row.historical_product_title or None,
            row.historical_variant_title or None,
        )
        if manifest_display_key != row.source_identity_key:
            raise ManifestValidationError(
                f"manifest display fields conflict with {row.source_identity_key}"
            )
        snapshot_display_key = _canonical_source_key(
            source.source_variant_id,
            source.source_sku,
            source.source_product_title,
            source.source_variant_title,
        )
        if snapshot_display_key != row.source_identity_key:
            raise ValueError(
                f"canonical review group drift for {row.source_identity_key}: "
                f"representative evidence recomputes to {snapshot_display_key}"
            )
        expected = (
            row.affected_raw_rows,
            row.absolute_unit_magnitude,
            row.absolute_sales_magnitude,
            row.material,
        )
        actual = (
            source.affected_raw_rows,
            source.absolute_unit_magnitude,
            source.absolute_sales_magnitude,
            source.material,
        )
        if actual != expected:
            raise ValueError(f"source controls drift for {row.source_identity_key}")


def _validate_targets_and_identity_evidence(
    conn: Any,
    manifest: AuthorizedManifest,
    snapshot: Mapping[str, ReviewSourceSnapshot],
) -> None:
    targets = sorted(
        {row.canonical_variant_id for row in manifest.rows if row.canonical_variant_id}
    )
    with conn.cursor() as cur:
        cur.execute("SELECT variant_id FROM variants WHERE variant_id=ANY(%s)", (targets,))
        present = {str(row[0]) for row in cur.fetchall()}
        missing = sorted(set(targets) - present)
        if missing:
            raise ValueError(f"canonical MAP target is missing from variants: {missing[0]}")
        cur.execute(
            """SELECT source_key,rejected_variant_id
               FROM mapping_rejections
               WHERE mapping_type='HISTORICAL_VARIANT' AND active"""
        )
        rejections = {(str(row[0]), str(row[1])) for row in cur.fetchall()}

    base_identity = _load_base_identity_index(conn)
    for row in manifest.rows:
        source = snapshot[row.source_identity_key]
        source_row = SalesSourceRow(
            date(2024, 11, 28),
            source.source_variant_id,
            source.source_sku,
            source.source_product_title,
            source.source_variant_title,
            Decimal("0"),
            Decimal("0"),
        )
        resolution = base_identity.resolve(source_row)
        if row.review_disposition == "MAP":
            assert row.canonical_variant_id is not None
            if (
                (row.source_identity_key, row.canonical_variant_id) in rejections
                or (row.source_variant_id, row.canonical_variant_id) in rejections
            ):
                raise ValueError(
                    f"active mapping rejection conflicts with {row.source_identity_key}"
                )
            if resolution.status == "RESOLVED" and (
                resolution.canonical_variant_id != row.canonical_variant_id
            ):
                raise ValueError(
                    f"existing identity evidence conflicts with {row.source_identity_key}"
                )
            if resolution.status == "AMBIGUOUS" and resolution.method in {
                "APPROVED_VARIANT_ID_ALIAS",
                "APPROVED_HISTORICAL_IDENTITY",
            }:
                raise ValueError(f"approved alias conflict for {row.source_identity_key}")
        elif (
            row.review_disposition == "LEAVE_UNRESOLVED"
            and resolution.status == "RESOLVED"
        ):
            raise ValueError(
                f"deterministic existing alias evidence resolves {row.source_identity_key}"
            )


def _alias_insert_families(
    conn: Any,
    manifest: AuthorizedManifest,
    snapshot: Mapping[str, ReviewSourceSnapshot],
) -> dict[str, str]:
    families: dict[str, list[ManifestRow]] = {}
    for row in manifest.rows:
        if (
            row.review_disposition == "MAP"
            and row.source_variant_id not in (None, "0")
        ):
            families.setdefault(str(row.source_variant_id), []).append(row)

    insertions: dict[str, str] = {}
    for old_id, rows in sorted(families.items()):
        manifest_family = {row.source_identity_key for row in rows}
        live_family = {
            key for key, source in snapshot.items() if source.source_variant_id == old_id
        }
        targets = {row.canonical_variant_id for row in rows}
        if live_family != manifest_family or len(targets) != 1 or None in targets:
            raise ValueError(f"nonuniform old-ID family for {old_id}")
        target = str(next(iter(targets)))
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT variant_id FROM variant_aliases
                   WHERE approved=TRUE AND old_variant_id=%s""",
                (old_id,),
            )
            existing_targets = {str(row[0]) for row in cur.fetchall()}
        if existing_targets - {target}:
            raise ValueError(f"approved alias conflict for old Variant ID {old_id}")
        if not existing_targets:
            insertions[old_id] = target
    return insertions


def _exclusion_mutations(
    conn: Any,
    snapshot: Mapping[str, ReviewSourceSnapshot],
) -> tuple[str, ...]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT source_key,source_variant_id,source_sku,source_product_title,
                      source_variant_title,active
               FROM historical_sales_exclusions"""
        )
        exclusions = {str(row[0]): row[1:] for row in cur.fetchall()}
    active_keys = {key for key, row in exclusions.items() if bool(row[4])}
    if active_keys - EXCLUSION_SOURCE_KEYS:
        raise ValueError(
            f"unexpected active historical exclusion: {sorted(active_keys - EXCLUSION_SOURCE_KEYS)[0]}"
        )
    mutations: list[str] = []
    for key in sorted(EXCLUSION_SOURCE_KEYS):
        source = snapshot[key]
        existing = exclusions.get(key)
        if existing is None or not bool(existing[4]):
            mutations.append(key)
            continue
        expected_fields = (
            source.source_variant_id,
            source.source_sku,
            source.source_product_title,
            source.source_variant_title,
        )
        if tuple(existing[:4]) != expected_fields:
            raise ValueError(f"active exclusion source fields conflict for {key}")
    return tuple(mutations)


def validate_database_preflight(
    conn: Any, manifest: AuthorizedManifest
) -> DatabasePreflight:
    validate_static_manifest(manifest.raw_bytes, manifest.rows)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT status FROM readiness_gates
               WHERE gate_name='SALES_BACKFILL'
                 AND scope_type='GLOBAL' AND scope_id=''"""
        )
        gate = cur.fetchone()
    if gate is None or str(gate[0]) != "FAIL":
        raise ValueError("SALES_BACKFILL must remain FAIL during decision persistence")
    snapshot = load_review_source_snapshot(conn, manifest.run_id)
    _validate_snapshot_matches_manifest(manifest, snapshot)
    _validate_targets_and_identity_evidence(conn, manifest, snapshot)
    alias_insertions = _alias_insert_families(conn, manifest, snapshot)
    exclusion_mutations = _exclusion_mutations(conn, snapshot)
    existing = load_latest_effective_decisions(
        conn, (row.source_identity_key for row in manifest.rows)
    )
    classifications = classify_existing_decisions(manifest, existing)
    conflicts = [item for item in classifications if item.state == "CONFLICT"]
    if conflicts:
        raise ValueError(
            f"conflicting prior decision for {conflicts[0].source_identity_key}"
        )
    return DatabasePreflight(
        snapshot=snapshot,
        classifications=classifications,
        alias_insert_families=alias_insertions,
        exclusion_mutations=exclusion_mutations,
    )


_FINGERPRINT_QUERIES = {
    "sales_daily": """
        SELECT sale_date::text || '|' || variant_id || '|' || source AS sort_key,
               to_jsonb(s)::text AS payload FROM sales_daily s
    """,
    "raw_resolution": """
        SELECT raw_sales_id::text AS sort_key,
               jsonb_build_object(
                 'raw_sales_id',raw_sales_id,
                 'canonical_variant_id',canonical_variant_id,
                 'resolution_status',resolution_status,
                 'resolution_method',resolution_method,
                 'resolution_evidence',resolution_evidence
               )::text AS payload
        FROM shopify_sales_daily_raw
    """,
    "sales_backfill_runs": """
        SELECT sales_backfill_id::text AS sort_key,to_jsonb(r)::text AS payload
        FROM sales_backfill_runs r
    """,
    "readiness_gates": """
        SELECT gate_name || '|' || scope_type || '|' || scope_id AS sort_key,
               to_jsonb(g)::text AS payload FROM readiness_gates g
    """,
    "purchase_orders": """
        SELECT po_id::text AS sort_key,to_jsonb(p)::text AS payload
        FROM purchase_orders p
    """,
    "purchase_order_lines": """
        SELECT po_line_id::text AS sort_key,to_jsonb(p)::text AS payload
        FROM purchase_order_lines p
    """,
}


def protected_state_fingerprints(conn: Any) -> dict[str, dict[str, Any]]:
    fingerprints: dict[str, dict[str, Any]] = {}
    with conn.cursor() as cur:
        for name, query in _FINGERPRINT_QUERIES.items():
            cur.execute(
                f"""SELECT encode(
                       digest(
                         COALESCE(string_agg(payload,E'\\n' ORDER BY sort_key,payload),''),
                         'sha256'
                       ),'hex'
                     ),COUNT(*)::int
                     FROM ({query}) fingerprint_rows"""
            )
            row = cur.fetchone()
            fingerprints[name] = {"sha256": str(row[0]), "row_count": int(row[1])}
    return fingerprints


def _decision_evidence(
    row: ManifestRow,
    context: ManifestExecutionContext,
    *,
    normalized_legacy_decision_id: str | None,
) -> dict[str, Any]:
    evidence = {
        "manifest_sha256": APPROVED_MANIFEST_SHA256,
        "manifest_row_number": row.row_number,
        "manifest_disposition": row.review_disposition,
        "stored_decision_action": row.stored_action,
        "evidence_basis": row.evidence_basis,
        "review_note": row.review_note,
        "production_run_id": APPROVED_RUN_ID,
        "implementation_git_sha": context.implementation_git_sha,
        "source_identity_key": row.source_identity_key,
        "source_variant_id": row.source_variant_id,
        "source_sku": row.source_sku,
        "source_product_title": row.historical_product_title or None,
        "source_variant_title": row.historical_variant_title or None,
        "canonical_variant_id": row.canonical_variant_id,
        "owner_authorization_id": context.authorization_id,
        "owner_authorization_version": context.authorization_version,
    }
    if normalized_legacy_decision_id is not None:
        evidence["normalized_legacy_decision_id"] = normalized_legacy_decision_id
    return evidence


def _read_gate_and_po_state(conn: Any) -> tuple[str, int, int]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT status FROM readiness_gates
               WHERE gate_name='SALES_BACKFILL' AND scope_type='GLOBAL' AND scope_id=''"""
        )
        gate = cur.fetchone()
        cur.execute("SELECT COUNT(*)::int FROM purchase_orders")
        po_count = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*)::int FROM purchase_order_lines")
        po_line_count = int(cur.fetchone()[0])
    if gate is None:
        raise ValueError("SALES_BACKFILL readiness gate is missing")
    return str(gate[0]), po_count, po_line_count


def _sorted_active_exclusion_keys(rows: Iterable[Any]) -> list[str]:
    return sorted(str(row[0]) for row in rows)


def readback_manifest_decisions(
    conn: Any,
    manifest: AuthorizedManifest,
    *,
    require_complete: bool = True,
) -> dict[str, Any]:
    latest = load_latest_effective_decisions(
        conn, (row.source_identity_key for row in manifest.rows)
    )
    by_key = {row.source_identity_key: row for row in manifest.rows}
    classifications = classify_existing_decisions(manifest, latest)
    conflicting_effective_decisions = sum(
        item.state == "CONFLICT" for item in classifications
    )
    action_counts = Counter(decision.decision_action for decision in latest.values())
    map_targets = [
        decision.canonical_variant_id
        for decision in latest.values()
        if decision.decision_action == "MAP" and decision.canonical_variant_id
    ]
    provenance_complete = sum(
        _provenance_complete(decision, by_key[key])
        for key, decision in latest.items()
        if key in by_key
    )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_key FROM historical_sales_exclusions WHERE active"
        )
        active_exclusions = _sorted_active_exclusion_keys(cur.fetchall())
        if map_targets:
            cur.execute(
                "SELECT variant_id FROM variants WHERE variant_id=ANY(%s)",
                (sorted(set(map_targets)),),
            )
            present_targets = {str(row[0]) for row in cur.fetchall()}
        else:
            present_targets = set()
    gate_status, po_count, po_line_count = _read_gate_and_po_state(conn)
    report = {
        "effective_source_keys": len(latest),
        "map_to_canonical": action_counts.get("MAP", 0),
        "exclude_historical_item": action_counts.get("EXCLUDE", 0),
        "leave_unresolved": action_counts.get("LEAVE_UNRESOLVED", 0),
        "map_targets_populated": len(map_targets),
        "distinct_map_targets": len(set(map_targets)),
        "missing_canonical_map_targets": len(set(map_targets) - present_targets),
        "active_exclusions": len(active_exclusions),
        "active_exclusion_source_keys": active_exclusions,
        "nutrl_mappings": sum(
            latest.get(key) is not None
            and latest[key].decision_action == "MAP"
            and latest[key].canonical_variant_id == NUTRL_CANONICAL_VARIANT_ID
            for key in NUTRL_SOURCE_KEYS
        ),
        "fiesta_mappings": sum(
            decision.decision_action == "MAP"
            and decision.canonical_variant_id == FIESTA_CANONICAL_VARIANT_ID
            for decision in latest.values()
        ),
        "high_noon_tequila_unresolved": sum(
            latest.get(key) is not None
            and latest[key].decision_action == "LEAVE_UNRESOLVED"
            for key in HIGH_NOON_TEQUILA_SOURCE_KEYS
        ),
        "conflicting_effective_decisions": conflicting_effective_decisions,
        "manifest_provenance_complete": provenance_complete,
        "sales_backfill_status": gate_status,
        "purchase_orders": po_count,
        "purchase_order_lines": po_line_count,
    }
    if require_complete:
        expected = {
            "effective_source_keys": 343,
            "map_to_canonical": 55,
            "exclude_historical_item": 8,
            "leave_unresolved": 280,
            "map_targets_populated": 55,
            "distinct_map_targets": 51,
            "missing_canonical_map_targets": 0,
            "active_exclusions": 8,
            "nutrl_mappings": 3,
            "fiesta_mappings": 0,
            "high_noon_tequila_unresolved": 3,
            "conflicting_effective_decisions": 0,
            "manifest_provenance_complete": 343,
            "sales_backfill_status": "FAIL",
        }
        for key, value in expected.items():
            if report[key] != value:
                raise ValueError(
                    f"post-write readback {key}={report[key]} expected {value}"
                )
        if set(active_exclusions) != EXCLUSION_SOURCE_KEYS:
            raise ValueError("post-write active exclusions differ from exact approved set")
    return report


def _planned_mutations(preflight: DatabasePreflight) -> dict[str, int]:
    counts = preflight.decision_state_counts
    decision_rows = counts["MISSING"] + counts["LEGACY_COMPATIBLE"]
    exclusion_rows = len(preflight.exclusion_mutations)
    alias_rows = len(preflight.alias_insert_families)
    change_log_rows = decision_rows
    return {
        "decision_rows": decision_rows,
        "legacy_normalizations": counts["LEGACY_COMPATIBLE"],
        "exclusion_rows": exclusion_rows,
        "alias_rows": alias_rows,
        "change_log_rows": change_log_rows,
        "total": decision_rows + exclusion_rows + alias_rows + change_log_rows,
    }


def _decision_artifact_counts(
    conn: Any,
    manifest: AuthorizedManifest,
) -> dict[str, int]:
    old_id_families = {
        row.source_variant_id
        for row in manifest.rows
        if row.review_disposition == "MAP"
        and row.source_variant_id not in (None, "0")
    }
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*)::int FROM historical_sales_review_decisions")
        decision_rows = int(cur.fetchone()[0])
        cur.execute(
            "SELECT COUNT(*)::int FROM historical_sales_exclusions WHERE active"
        )
        active_exclusions = int(cur.fetchone()[0])
        cur.execute(
            """SELECT COUNT(*)::int FROM variant_aliases
               WHERE source='SALES_BACKFILL_REVIEW'"""
        )
        review_alias_rows = int(cur.fetchone()[0])
        cur.execute(
            """SELECT COUNT(*)::int FROM change_log
               WHERE table_name='historical_sales_review_decisions'"""
        )
        decision_change_rows = int(cur.fetchone()[0])
        cur.execute(
            """SELECT COUNT(DISTINCT old_variant_id)::int
               FROM variant_aliases
               WHERE approved=TRUE AND old_variant_id=ANY(%s)""",
            (sorted(old_id_families),),
        )
        compatible_alias_families = int(cur.fetchone()[0])
    effective = load_latest_effective_decisions(
        conn, (row.source_identity_key for row in manifest.rows)
    )
    return {
        "historical_sales_review_decisions": decision_rows,
        "effective_manifest_decisions": len(effective),
        "active_historical_sales_exclusions": active_exclusions,
        "sales_backfill_review_alias_rows": review_alias_rows,
        "manifest_old_id_families": len(old_id_families),
        "compatible_existing_alias_families": compatible_alias_families,
        "decision_change_log_rows": decision_change_rows,
    }


def dry_run_manifest(
    conn: Any,
    manifest: AuthorizedManifest,
    context: ManifestExecutionContext,
) -> dict[str, Any]:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT current_setting('transaction_read_only')")
            transaction_read_only = str(cur.fetchone()[0])
            if transaction_read_only != "on":
                raise RuntimeError("database did not enforce a read-only dry-run transaction")
            cur.execute("SELECT txid_current_if_assigned()")
            txid_before = cur.fetchone()[0]
        if txid_before is not None:
            raise RuntimeError("dry-run transaction unexpectedly had an assigned transaction ID")
        preflight = validate_database_preflight(conn, manifest)
        fingerprints = protected_state_fingerprints(conn)
        artifact_counts = _decision_artifact_counts(conn, manifest)
        complete = preflight.decision_state_counts["CURRENT_PROVENANCE"] == 343
        readback = readback_manifest_decisions(
            conn, manifest, require_complete=complete
        )
        with conn.cursor() as cur:
            cur.execute("SELECT txid_current_if_assigned()")
            txid_after = cur.fetchone()[0]
        if txid_after is not None:
            raise RuntimeError("dry-run transaction assigned a transaction ID")
        return {
            "mode": "DRY_RUN",
            "manifest_sha256": manifest.sha256,
            "production_run_id": manifest.run_id,
            "implementation_git_sha": context.implementation_git_sha,
            "controls": manifest.controls.as_dict(),
            "transaction_read_only": transaction_read_only,
            "txid_before": txid_before,
            "txid_after": txid_after,
            "decision_state_counts": preflight.decision_state_counts,
            "decision_artifact_counts": artifact_counts,
            "planned_mutations": _planned_mutations(preflight),
            "protected_fingerprints": fingerprints,
            "readback": readback,
        }


def persist_manifest_decisions(
    conn: Any,
    manifest: AuthorizedManifest,
    context: ManifestExecutionContext,
    *,
    inject_failure_after_row: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            cur.execute("SELECT current_setting('transaction_isolation')")
            if str(cur.fetchone()[0]).lower() != "serializable":
                raise RuntimeError("manifest transaction isolation is not serializable")
        acquire_backfill_transaction_lock(conn)
        preflight = validate_database_preflight(conn, manifest)
        fingerprints_before = protected_state_fingerprints(conn)
        classifications = {
            item.source_identity_key: item for item in preflight.classifications
        }
        inserted_decisions = 0
        processed_rows = 0

        for row in sorted(manifest.rows, key=lambda item: item.source_identity_key):
            classification = classifications[row.source_identity_key]
            if classification.state not in {"MISSING", "LEGACY_COMPATIBLE"}:
                continue
            legacy_id = (
                classification.existing.decision_id
                if classification.state == "LEGACY_COMPATIBLE"
                and classification.existing is not None
                else None
            )
            evidence = _decision_evidence(
                row,
                context,
                normalized_legacy_decision_id=legacy_id,
            )
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO historical_sales_review_decisions(
                         sales_backfill_id,source_identity_key,source_variant_id,source_sku,
                         source_product_title,source_variant_title,decision_action,
                         canonical_variant_id,actor,reason,evidence_json,
                         supersedes_decision_id
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                       RETURNING historical_sales_review_decision_id""",
                    (
                        manifest.run_id,
                        row.source_identity_key,
                        row.source_variant_id,
                        row.source_sku,
                        row.historical_product_title or None,
                        row.historical_variant_title or None,
                        row.stored_action,
                        row.canonical_variant_id,
                        context.actor,
                        row.review_note,
                        _json(evidence),
                        legacy_id,
                    ),
                )
                decision_id = str(cur.fetchone()[0])
                cur.execute(
                    """INSERT INTO change_log(
                         table_name,row_key,action,after_json,actor
                       ) VALUES (
                         'historical_sales_review_decisions',%s,%s,%s::jsonb,%s
                       )""",
                    (
                        row.source_identity_key,
                        "SUPERSEDE" if legacy_id else "APPROVE",
                        _json(
                            {
                                **evidence,
                                "historical_sales_review_decision_id": decision_id,
                            }
                        ),
                        context.actor,
                    ),
                )
            inserted_decisions += 1
            processed_rows += 1
            if (
                inject_failure_after_row is not None
                and processed_rows == inject_failure_after_row
            ):
                raise RuntimeError("injected manifest persistence failure")

        rows_by_key = {row.source_identity_key: row for row in manifest.rows}
        for source_key in preflight.exclusion_mutations:
            row = rows_by_key[source_key]
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO historical_sales_exclusions(
                         source_key,source_variant_id,source_sku,source_product_title,
                         source_variant_title,reason,approved_by,approved_at,active
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,now(),TRUE)
                       ON CONFLICT(source_key) DO UPDATE SET
                         source_variant_id=EXCLUDED.source_variant_id,
                         source_sku=EXCLUDED.source_sku,
                         source_product_title=EXCLUDED.source_product_title,
                         source_variant_title=EXCLUDED.source_variant_title,
                         reason=EXCLUDED.reason,approved_by=EXCLUDED.approved_by,
                         approved_at=now(),active=TRUE""",
                    (
                        source_key,
                        row.source_variant_id,
                        row.source_sku,
                        row.historical_product_title or None,
                        row.historical_variant_title or None,
                        row.review_note,
                        context.actor,
                    ),
                )

        for old_id, target in sorted(preflight.alias_insert_families.items()):
            covered_keys = sorted(
                row.source_identity_key
                for row in manifest.rows
                if row.review_disposition == "MAP" and row.source_variant_id == old_id
            )
            evidence = {
                "manifest_sha256": manifest.sha256,
                "production_run_id": manifest.run_id,
                "implementation_git_sha": context.implementation_git_sha,
                "old_variant_id": old_id,
                "canonical_variant_id": target,
                "covered_source_identity_keys": covered_keys,
                "owner_authorization_id": context.authorization_id,
                "owner_authorization_version": context.authorization_version,
            }
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO variant_aliases(
                         variant_id,old_variant_id,match_method,confidence,source,notes,
                         approved,approved_by,approved_at,evidence_json
                       ) VALUES (
                         %s,%s,'HUMAN_HISTORICAL_SALES_MAPPING',1.0,
                         'SALES_BACKFILL_REVIEW',%s,TRUE,%s,now(),%s::jsonb
                       )""",
                    (
                        target,
                        old_id,
                        "Owner-approved uniform Phase 4 historical Variant-ID family.",
                        context.actor,
                        _json(evidence),
                    ),
                )

        readback = readback_manifest_decisions(conn, manifest)
        artifact_counts = _decision_artifact_counts(conn, manifest)
        fingerprints_after = protected_state_fingerprints(conn)
        if fingerprints_after != fingerprints_before:
            raise RuntimeError("protected sales, resolution, gate, or PO state changed")
        planned = _planned_mutations(preflight)
        committed_mutations = planned["total"]
        result = {
            "mode": "APPLY",
            "manifest_sha256": manifest.sha256,
            "production_run_id": manifest.run_id,
            "implementation_git_sha": context.implementation_git_sha,
            "controls": manifest.controls.as_dict(),
            "transaction_isolation": "serializable",
            "decision_state_counts": preflight.decision_state_counts,
            "decision_artifact_counts_after": artifact_counts,
            "inserted_decisions": inserted_decisions,
            "normalized_legacy_decisions": preflight.decision_state_counts[
                "LEGACY_COMPATIBLE"
            ],
            "inserted_alias_families": len(preflight.alias_insert_families),
            "upserted_exclusions": len(preflight.exclusion_mutations),
            "change_log_rows": inserted_decisions,
            "committed_mutations": committed_mutations,
            "protected_fingerprints_before": fingerprints_before,
            "protected_fingerprints_after": fingerprints_after,
            "readback": readback,
        }
    return result
