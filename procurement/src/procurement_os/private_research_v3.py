"""Additive private V3 wrapper for exact catalog-preexistence evidence.

V3 does not replace or reinterpret the immutable V2 input.  It binds the V2
parent by content address, adds only reviewed exact-ID creation evidence and the
newly supportable observation series, and reuses the registered Development
Forecast V2 numerical engine.  It remains research-only and zero-authority.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from .development_forecast import V2_CONTRACT, development_forecast_definition
from .private_research_intake import (
    INTAKE_AUTHORITY,
    PRIVATE_RESEARCH_INTAKE_CONTRACT,
    ZERO_AUTHORITY,
    validate_private_root,
)
from .private_research_v2 import (
    DATA_MODE,
    EXPECTED_END,
    EXPECTED_START,
    INPUT_CONTRACT as PARENT_INPUT_CONTRACT,
    REQUIRED_LIMITATIONS,
    SCENARIO_HORIZONS,
    PrivateResearchV2Error,
    _INPUT_SOURCE_PROOF,
    _base_intake_identity,
    _build_private_research_projection_from_validated_input,
    _canonical,
    _compact_unapproved_hypothesis,
    _decimal,
    _ensure_private_parent,
    _json_clone,
    _projection_canonical,
    _sha,
    _sha_bytes,
    _summarize_unapproved_hypotheses,
    _source_verified,
    _seal_source_verified,
    input_manifest_key as parent_input_manifest_key,
    read_private_v2_research_bundle,
    validate_private_v2_research_input,
)
from .storage import LocalFilesystemStorage


INPUT_CONTRACT = "BUFFALO_PRIVATE_DEVELOPMENT_FORECAST_RESEARCH_INPUT_V3"
PROJECTION_CONTRACT = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V3"
EXISTENCE_EVIDENCE_CONTRACT = "BUFFALO_PRIVATE_VARIANT_EXISTENCE_EVIDENCE_V1"
ELIGIBILITY_CONTRACT = "BUFFALO_PRIVATE_FORECAST_ELIGIBILITY_LEDGER_V1"
COMPOSITE_HISTORY_CONTRACT = "BUFFALO_PRIVATE_FORECAST_HISTORY_COMPOSITE_V3"
SEED_MANIFEST_SHA256 = "2231ee97b9f01e98ada7da765718f1b217d4c6003a56e27506443f2848556456"
SEED_VARIANTS_SHA256 = "dd31f1852c0ea79f8a66f8a34e7b1dac892501183da7f83d4de025a2e1349509"
SEED_SOURCE_SHA256 = "379ee8df2beeb387de308d16b107591935378a98a3d83e8dc8c1429872a88755"
SEED_VARIANT_ROW_COUNT = 2029
_INPUT_PREFIX = "private-research/v3-inputs"
_SOURCE_PREFIX = "private-research/v3-sources/sha256"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VARIANT_ID = re.compile(r"^[1-9][0-9]*$")
_SEED_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$"
)
_V3_INPUT_SOURCE_PROOF = object()
_V3_COMBINED_SOURCE_PROOF = object()
_ADDED_EXISTENCE_BASIS = "REVIEWED_EXACT_CURRENT_ID_CREATED_BEFORE_2026_05_04"
_PARENT_EXISTENCE_BASIS = "ALLOCATED_SOURCE_ROW_PRESENT_ON_2026_05_04"
_ZERO_BASIS = (
    "ATTESTED_COMPLETE_QUERY_AND_REVIEWED_PREWINDOW_EXACT_ID_EXISTENCE"
)
_LIMITATIONS = sorted(
    set(REQUIRED_LIMITATIONS)
    | {
        "REVIEWED_SEED_CREATION_EVIDENCE_IS_HISTORICAL_NOT_CURRENT_CATALOG_AUTHORITY",
        "RECORDED_SALES_ARE_NOT_UNCONSTRAINED_TRUE_DEMAND",
        "QUARANTINED_SOURCE_ROWS_ARE_NOT_ATTRIBUTED_TO_CURRENT_VARIANT_FORECASTS",
        "SEED_CREATION_TIMESTAMPS_HAVE_NO_TIMEZONE_START_DATE_IS_BLOCKED_CONSERVATIVELY",
    }
)
_CONTROL_KEYS = {
    "contract",
    "catalog_variant_count",
    "parent_first_day_supported_count",
    "added_prewindow_exact_id_count",
    "eligible_variant_count",
    "missing_evidence_variant_count",
    "eligibility_ledger_sha256",
    "added_variants_sha256",
    "recent_observed_sales_sha256",
    "missing_reason_counts",
}
_MISSING_REASONS = {
    "EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE",
    "VARIANT_CREATION_TIMESTAMP_NOT_AVAILABLE",
    "VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED",
    "VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED",
}


class PrivateResearchV3Error(ValueError):
    """The additive private V3 evidence or result is not trustworthy."""


def _expected_policy() -> dict[str, Any]:
    definition = development_forecast_definition(V2_CONTRACT)
    return {
        "evidence_contract": definition.evidence_contract,
        "policy_contract": definition.policy_contract,
        "method_version": definition.method_version,
        "profile": definition.profile,
        "policy_source_sha256": definition.policy_source_sha256,
        "policy_canonical_sha256": definition.policy_canonical_sha256,
        "commercial_authority": False,
        "production_activation": False,
    }


def _expected_existence_evidence() -> dict[str, Any]:
    return {
        "contract": EXISTENCE_EVIDENCE_CONTRACT,
        "seed_manifest_sha256": SEED_MANIFEST_SHA256,
        "seed_manifest_storage_key": f"{_SOURCE_PREFIX}/{SEED_MANIFEST_SHA256}.json",
        "seed_variants_sha256": SEED_VARIANTS_SHA256,
        "seed_variants_storage_key": f"{_SOURCE_PREFIX}/{SEED_VARIANTS_SHA256}.csv",
        "seed_source_sha256": SEED_SOURCE_SHA256,
        "declared_variant_row_count": SEED_VARIANT_ROW_COUNT,
        "identity_join": "EXACT_SHOPIFY_VARIANT_ID_ONLY",
        "timestamp_semantics": "SHOPIFY_VARIANT_CREATED_AT_REVIEWED_SEED_EVIDENCE",
        "history_start_rule": "SOURCE_TIMESTAMP_DATE_STRICTLY_BEFORE_2026_05_04_NO_TIMEZONE_INFERENCE",
        "commercial_authority": False,
    }


def _read_exact_file(
    path: str | Path,
    expected_sha: str,
    *,
    field: str,
    require_private_mode: bool = False,
) -> bytes:
    value = Path(path)
    try:
        resolved = value.resolve(strict=True)
    except OSError as exc:
        raise PrivateResearchV3Error(f"{field} is unavailable") from exc
    if not value.is_absolute() or value != resolved or value.is_symlink():
        raise PrivateResearchV3Error(f"{field} path differs")
    try:
        info = value.stat(follow_symlinks=False)
        raw = value.read_bytes()
    except OSError as exc:
        raise PrivateResearchV3Error(f"{field} is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or (
            require_private_mode
            and (
                info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            )
        )
        or _sha_bytes(raw) != expected_sha
    ):
        raise PrivateResearchV3Error(f"{field} bytes differ")
    return raw


def _seed_evidence(
    manifest_path: str | Path,
    variants_path: str | Path,
    *,
    require_private_mode: bool = False,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    manifest_raw = _read_exact_file(
        manifest_path,
        SEED_MANIFEST_SHA256,
        field="seed manifest",
        require_private_mode=require_private_mode,
    )
    variants_raw = _read_exact_file(
        variants_path,
        SEED_VARIANTS_SHA256,
        field="seed Variant evidence",
        require_private_mode=require_private_mode,
    )
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateResearchV3Error("seed manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("source_sha256") != SEED_SOURCE_SHA256
        or not isinstance(manifest.get("files"), dict)
        or manifest["files"].get("variants.csv")
        != {"rows": SEED_VARIANT_ROW_COUNT, "sha256": SEED_VARIANTS_SHA256}
    ):
        raise PrivateResearchV3Error("seed manifest controls differ")
    try:
        reader = csv.DictReader(io.StringIO(variants_raw.decode("utf-8-sig")))
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise PrivateResearchV3Error("seed Variant evidence is unreadable") from exc
    expected_fields = [
        "variant_id",
        "shopify_gid",
        "product_id",
        "product_gid",
        "product_title",
        "variant_title",
        "handle",
        "status",
        "sku",
        "barcode",
        "variant_created_at",
        "retail_price",
        "current_cost",
        "inventory_quantity",
        "inventory_tracked",
        "shopify_vendor",
        "product_type",
        "active",
        "source_snapshot",
        "last_synced_at",
    ]
    if fieldnames != expected_fields or len(rows) != SEED_VARIANT_ROW_COUNT:
        raise PrivateResearchV3Error("seed Variant evidence schema differs")
    by_id: dict[str, dict[str, str]] = {}
    for row in rows:
        variant_id = row.get("variant_id", "")
        if not _VARIANT_ID.fullmatch(variant_id) or variant_id in by_id:
            raise PrivateResearchV3Error("seed Variant identity differs")
        by_id[variant_id] = row
    return _expected_existence_evidence(), by_id


def _parse_creation(value: str) -> datetime | None:
    if value == "":
        return None
    if not _SEED_TIMESTAMP.fullmatch(value):
        raise PrivateResearchV3Error("seed Variant creation timestamp differs")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise PrivateResearchV3Error("seed Variant creation timestamp differs") from exc
    return parsed


def _allocated_daily(
    parent: Mapping[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in parent["history"]["allocation_ledger"]:
        if row["disposition"] not in {"DIRECT_CURRENT", "HISTORICALLY_ALLOCATED"}:
            continue
        variant_id = row["target_shopify_variant_id"]
        key = (variant_id, row["business_date"])
        aggregate = result.setdefault(
            key,
            {
                "net_units": Decimal(0),
                "net_revenue": Decimal(0),
                "historical_cogs": Decimal(0),
                "source_row_count": 0,
                "direct_current_row_count": 0,
                "historically_allocated_row_count": 0,
            },
        )
        for metric in ("net_units", "net_revenue", "historical_cogs"):
            aggregate[metric] += _decimal(row[metric], field=metric)
        aggregate["source_row_count"] += 1
        aggregate[
            "direct_current_row_count"
            if row["disposition"] == "DIRECT_CURRENT"
            else "historically_allocated_row_count"
        ] += 1
    return result


def _observations_for(
    variant_id: str,
    daily: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    zero_basis: str,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for offset in range(138):
        business_date = EXPECTED_START + timedelta(days=offset)
        aggregate = daily.get((variant_id, business_date.isoformat()))
        observations.append(
            {
                "business_date": business_date.isoformat(),
                "net_units": format(aggregate["net_units"], "f") if aggregate else "0",
                "net_revenue": format(aggregate["net_revenue"], "f") if aggregate else "0",
                "historical_cogs": format(aggregate["historical_cogs"], "f") if aggregate else "0",
                "source_row_count": int(aggregate["source_row_count"]) if aggregate else 0,
                "inventory_state": "UNKNOWN",
                "observed_zero_basis": None if aggregate else zero_basis,
            }
        )
    return observations


def _recent_sales(
    current_ids: list[str],
    daily: Mapping[tuple[str, str], Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    windows = {"D7": 7, "D28": 28}
    result: dict[str, Any] = {}
    for variant_id in current_ids:
        decision = decisions[variant_id]
        created = _parse_creation(str(decision.get("variant_created_at") or ""))
        item_windows: dict[str, Any] = {}
        for label, days in windows.items():
            start = EXPECTED_END - timedelta(days=days - 1)
            complete = (
                decision["status"] == "ELIGIBLE"
                or (created is not None and created.date() < start)
            )
            window_existence_basis = (
                decision["existence_basis"]
                if decision["status"] == "ELIGIBLE"
                else (
                    "REVIEWED_EXACT_CURRENT_ID_CREATED_BEFORE_WINDOW_START"
                    if complete
                    else None
                )
            )
            units = Decimal(0)
            revenue = Decimal(0)
            source_rows = 0
            observed_days = 0
            direct_rows = 0
            historical_rows = 0
            for offset in range(days):
                day = start + timedelta(days=offset)
                aggregate = daily.get((variant_id, day.isoformat()))
                if aggregate:
                    units += aggregate["net_units"]
                    revenue += aggregate["net_revenue"]
                    source_rows += int(aggregate["source_row_count"])
                    direct_rows += int(aggregate["direct_current_row_count"])
                    historical_rows += int(
                        aggregate["historically_allocated_row_count"]
                    )
                    observed_days += 1
            if direct_rows and historical_rows:
                identity_scope = (
                    "EXACT_CURRENT_AND_APPROVED_HISTORICAL_IDENTITY_ALLOCATION"
                )
            elif historical_rows:
                identity_scope = "APPROVED_HISTORICAL_IDENTITY_ALLOCATION_ONLY"
            elif direct_rows:
                identity_scope = "EXACT_CURRENT_SHOPIFY_VARIANT_ID_ONLY"
            else:
                identity_scope = (
                    "EXACT_CURRENT_ID_EXISTENCE_SCOPE_NO_RECORDED_ROWS"
                    if complete
                    else "NO_SUPPORTED_WINDOW_EXISTENCE_OR_RECORDED_ROWS"
                )
            item_windows[label] = {
                "start_date": start.isoformat(),
                "end_date": EXPECTED_END.isoformat(),
                "window_day_count": days,
                "complete_day_count": days if complete else None,
                "observed_source_day_count": observed_days,
                "existence_basis": window_existence_basis,
                "identity_scope": identity_scope,
                "coverage_status": (
                    "COMPLETE_ATTESTED_DAILY_QUERY_AND_EXISTENCE_SCOPE"
                    if complete
                    else (
                        "PARTIAL_OBSERVED_ROWS_ONLY"
                        if source_rows
                        else "NO_SUPPORTED_SOURCE_OBSERVATIONS"
                    )
                ),
                "net_units": format(units, "f") if (complete or source_rows) else None,
                "net_revenue": format(revenue, "f") if (complete or source_rows) else None,
                "source_row_count": source_rows,
                "direct_current_row_count": direct_rows,
                "historically_allocated_row_count": historical_rows,
            }
        result[variant_id] = {
            "status": "DESCRIPTIVE_RECORDED_SALES_NOT_FORECAST_OR_TRUE_DEMAND",
            "identity_scope": "PER_WINDOW_DIRECT_AND_APPROVED_HISTORICAL_DISPOSITION_COUNTS",
            "history_endpoint": EXPECTED_END.isoformat(),
            "windows": item_windows,
        }
    return result


def build_private_v3_research_input(
    base_intake: Mapping[str, Any],
    *,
    parent_v2_input: Mapping[str, Any],
    seed_manifest_path: str | Path,
    seed_variants_path: str | Path,
    _require_private_sources: bool = False,
) -> dict[str, Any]:
    """Create a source-authenticated V3 delta over one immutable V2 parent."""

    parent = validate_private_v2_research_input(
        _source_verified(
            parent_v2_input,
            proof=_INPUT_SOURCE_PROOF,
            field="parent V2 research input",
        )
    )
    base_identity = _base_intake_identity(base_intake)
    if parent["base_intake"] != base_identity:
        raise PrivateResearchV3Error("parent V2 input is bound to another base intake")
    existence, seed_by_id = _seed_evidence(
        seed_manifest_path,
        seed_variants_path,
        require_private_mode=_require_private_sources,
    )
    current_rows = base_intake.get("variants")
    if not isinstance(current_rows, list):
        raise PrivateResearchV3Error("current Variant population is unavailable")
    current_ids = sorted(
        (str(row.get("shopify_variant_id")) for row in current_rows if isinstance(row, Mapping)),
        key=lambda item: (len(item), item),
    )
    if (
        len(current_ids) != len(current_rows)
        or len(set(current_ids)) != len(current_ids)
        or any(not _VARIANT_ID.fullmatch(item) for item in current_ids)
    ):
        raise PrivateResearchV3Error("current Variant population differs")
    parent_variants = {row["shopify_variant_id"]: row for row in parent["variants"]}
    if not set(parent_variants).issubset(current_ids):
        raise PrivateResearchV3Error("parent V2 eligible population differs")

    decisions: dict[str, dict[str, Any]] = {}
    reason_counts: Counter[str] = Counter()
    added_ids: list[str] = []
    for variant_id in current_ids:
        seed_row = seed_by_id.get(variant_id)
        created_text = seed_row.get("variant_created_at", "") if seed_row else ""
        seed_row_sha = _sha(seed_row) if seed_row else None
        if variant_id in parent_variants:
            status = "ELIGIBLE"
            basis = _PARENT_EXISTENCE_BASIS
            reason = None
        elif seed_row is None:
            status = "MISSING_EVIDENCE"
            basis = None
            reason = "EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE"
        else:
            created = _parse_creation(created_text)
            if created is None:
                status = "MISSING_EVIDENCE"
                basis = None
                reason = "VARIANT_CREATION_TIMESTAMP_NOT_AVAILABLE"
            elif created.date() < EXPECTED_START:
                status = "ELIGIBLE"
                basis = _ADDED_EXISTENCE_BASIS
                reason = None
                added_ids.append(variant_id)
            elif created.date() == EXPECTED_START:
                status = "MISSING_EVIDENCE"
                basis = None
                reason = "VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED"
            else:
                status = "MISSING_EVIDENCE"
                basis = None
                reason = "VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED"
        if reason:
            reason_counts[reason] += 1
        decisions[variant_id] = {
            "shopify_variant_id": variant_id,
            "status": status,
            "existence_basis": basis,
            "blocker_reason": reason,
            "variant_created_at": created_text or None,
            "seed_row_sha256": seed_row_sha,
        }

    daily = _allocated_daily(parent)
    added_variants: list[dict[str, Any]] = []
    for variant_id in added_ids:
        observations = _observations_for(
            variant_id,
            daily,
            zero_basis=_ZERO_BASIS,
        )
        added_variants.append(
            {
                "shopify_variant_id": variant_id,
                "existence_basis": decisions[variant_id]["existence_basis"],
                "observations": observations,
                "observations_sha256": _sha(observations),
            }
        )
    recent = _recent_sales(current_ids, daily, decisions)
    eligibility_ledger = [decisions[variant_id] for variant_id in current_ids]
    blocked_reasons = {
        variant_id: [decision["blocker_reason"]]
        for variant_id, decision in decisions.items()
        if decision["status"] == "MISSING_EVIDENCE"
    }
    parent_descriptor = {
        "contract": PARENT_INPUT_CONTRACT,
        "input_id": parent["input_id"],
        "sha256": _sha(parent),
    }
    controls = {
        "contract": ELIGIBILITY_CONTRACT,
        "catalog_variant_count": len(current_ids),
        "parent_first_day_supported_count": len(parent_variants),
        "added_prewindow_exact_id_count": len(added_variants),
        "eligible_variant_count": len(parent_variants) + len(added_variants),
        "missing_evidence_variant_count": len(blocked_reasons),
        "eligibility_ledger_sha256": _sha(eligibility_ledger),
        "added_variants_sha256": _sha(added_variants),
        "recent_observed_sales_sha256": _sha(recent),
        "missing_reason_counts": dict(sorted(reason_counts.items())),
    }
    if controls["eligible_variant_count"] + controls["missing_evidence_variant_count"] != len(current_ids):
        raise PrivateResearchV3Error("V3 eligibility controls do not reconcile")
    payload = {
        "contract": INPUT_CONTRACT,
        "data_mode": DATA_MODE,
        "authority": dict(INTAKE_AUTHORITY),
        "input_id": None,
        "parent_v2_input": parent_descriptor,
        "base_intake": base_identity,
        "policy": parent["policy"],
        "existence_evidence": existence,
        "eligibility_controls": controls,
        "eligibility_ledger": eligibility_ledger,
        "blocked_reasons_by_variant": blocked_reasons,
        "recent_observed_sales_by_variant": recent,
        "added_variants": added_variants,
        "limitations": list(_LIMITATIONS),
        "zero_authority": dict(ZERO_AUTHORITY),
    }
    payload["input_id"] = _sha(payload)
    return _seal_source_verified(
        validate_private_v3_research_input(payload),
        proof=_V3_INPUT_SOURCE_PROOF,
    )


def _validate_observation_rows(rows: Any) -> set[str]:
    expected_dates = [
        (EXPECTED_START + timedelta(days=offset)).isoformat() for offset in range(138)
    ]
    if not isinstance(rows, list):
        raise PrivateResearchV3Error("V3 added Variant inventory differs")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "shopify_variant_id",
            "existence_basis",
            "observations",
            "observations_sha256",
        }:
            raise PrivateResearchV3Error("V3 added Variant differs")
        variant_id = row.get("shopify_variant_id")
        observations = row.get("observations")
        if (
            not isinstance(variant_id, str)
            or not _VARIANT_ID.fullmatch(variant_id)
            or variant_id in seen
            or row.get("existence_basis")
            != _ADDED_EXISTENCE_BASIS
            or not isinstance(observations, list)
            or len(observations) != 138
            or row.get("observations_sha256") != _sha(observations)
            or [item.get("business_date") for item in observations if isinstance(item, Mapping)]
            != expected_dates
        ):
            raise PrivateResearchV3Error("V3 added Variant coverage differs")
        seen.add(variant_id)
        for item in observations:
            if not isinstance(item, Mapping) or set(item) != {
                "business_date",
                "net_units",
                "net_revenue",
                "historical_cogs",
                "source_row_count",
                "inventory_state",
                "observed_zero_basis",
            }:
                raise PrivateResearchV3Error("V3 added observation differs")
            for metric in ("net_units", "net_revenue", "historical_cogs"):
                _decimal(item[metric], field=metric)
            count = item.get("source_row_count")
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or item.get("inventory_state") != "UNKNOWN"
                or (
                    count == 0
                    and item.get("observed_zero_basis") != _ZERO_BASIS
                )
                or (count > 0 and item.get("observed_zero_basis") is not None)
            ):
                raise PrivateResearchV3Error("V3 added observation semantics differ")
    return seen


def _validate_recent_sales(
    recent: Mapping[str, Any],
    decisions: Mapping[str, Mapping[str, Any]],
) -> None:
    for variant_id, decision in decisions.items():
        item = recent.get(variant_id)
        if not isinstance(item, Mapping) or set(item) != {
            "status",
            "identity_scope",
            "history_endpoint",
            "windows",
        }:
            raise PrivateResearchV3Error("V3 recent-sales evidence differs")
        windows = item.get("windows")
        if (
            item.get("status")
            != "DESCRIPTIVE_RECORDED_SALES_NOT_FORECAST_OR_TRUE_DEMAND"
            or item.get("identity_scope")
            != "PER_WINDOW_DIRECT_AND_APPROVED_HISTORICAL_DISPOSITION_COUNTS"
            or item.get("history_endpoint") != EXPECTED_END.isoformat()
            or not isinstance(windows, Mapping)
            or set(windows) != {"D7", "D28"}
        ):
            raise PrivateResearchV3Error("V3 recent-sales identity differs")
        for label, days in (("D7", 7), ("D28", 28)):
            window = windows[label]
            start = EXPECTED_END - timedelta(days=days - 1)
            created = _parse_creation(
                str(decision.get("variant_created_at") or "")
            )
            expected_complete = decision.get("status") == "ELIGIBLE" or (
                created is not None and created.date() < start
            )
            expected_existence_basis = (
                decision.get("existence_basis")
                if decision.get("status") == "ELIGIBLE"
                else (
                    "REVIEWED_EXACT_CURRENT_ID_CREATED_BEFORE_WINDOW_START"
                    if expected_complete
                    else None
                )
            )
            if not isinstance(window, Mapping) or set(window) != {
                "start_date",
                "end_date",
                "window_day_count",
                "complete_day_count",
                "observed_source_day_count",
                "existence_basis",
                "identity_scope",
                "coverage_status",
                "net_units",
                "net_revenue",
                "source_row_count",
                "direct_current_row_count",
                "historically_allocated_row_count",
            }:
                raise PrivateResearchV3Error("V3 recent-sales window differs")
            status = window.get("coverage_status")
            source_rows = window.get("source_row_count")
            observed_days = window.get("observed_source_day_count")
            direct_rows = window.get("direct_current_row_count")
            historical_rows = window.get("historically_allocated_row_count")
            expected_identity_scope = (
                "EXACT_CURRENT_AND_APPROVED_HISTORICAL_IDENTITY_ALLOCATION"
                if direct_rows and historical_rows
                else (
                    "APPROVED_HISTORICAL_IDENTITY_ALLOCATION_ONLY"
                    if historical_rows
                    else (
                        "EXACT_CURRENT_SHOPIFY_VARIANT_ID_ONLY"
                        if direct_rows
                        else (
                            "EXACT_CURRENT_ID_EXISTENCE_SCOPE_NO_RECORDED_ROWS"
                            if expected_complete
                            else "NO_SUPPORTED_WINDOW_EXISTENCE_OR_RECORDED_ROWS"
                        )
                    )
                )
            )
            if (
                window.get("start_date") != start.isoformat()
                or window.get("end_date") != EXPECTED_END.isoformat()
                or window.get("window_day_count") != days
                or isinstance(source_rows, bool)
                or not isinstance(source_rows, int)
                or source_rows < 0
                or isinstance(observed_days, bool)
                or not isinstance(observed_days, int)
                or not 0 <= observed_days <= days
                or observed_days > source_rows
                or isinstance(direct_rows, bool)
                or not isinstance(direct_rows, int)
                or direct_rows < 0
                or isinstance(historical_rows, bool)
                or not isinstance(historical_rows, int)
                or historical_rows < 0
                or direct_rows + historical_rows != source_rows
                or window.get("identity_scope") != expected_identity_scope
            ):
                raise PrivateResearchV3Error("V3 recent-sales controls differ")
            if status == "COMPLETE_ATTESTED_DAILY_QUERY_AND_EXISTENCE_SCOPE":
                if (
                    not expected_complete
                    or window.get("complete_day_count") != days
                    or window.get("existence_basis")
                    != expected_existence_basis
                    or window.get("net_units") is None
                    or window.get("net_revenue") is None
                ):
                    raise PrivateResearchV3Error(
                        "V3 complete recent-sales evidence differs"
                    )
            elif status == "PARTIAL_OBSERVED_ROWS_ONLY":
                if (
                    expected_complete
                    or window.get("complete_day_count") is not None
                    or window.get("existence_basis") is not None
                    or source_rows == 0
                    or observed_days == 0
                    or window.get("net_units") is None
                    or window.get("net_revenue") is None
                ):
                    raise PrivateResearchV3Error(
                        "V3 partial recent-sales evidence differs"
                    )
            elif status == "NO_SUPPORTED_SOURCE_OBSERVATIONS":
                if (
                    expected_complete
                    or window.get("complete_day_count") is not None
                    or window.get("existence_basis") is not None
                    or source_rows != 0
                    or observed_days != 0
                    or window.get("net_units") is not None
                    or window.get("net_revenue") is not None
                ):
                    raise PrivateResearchV3Error(
                        "V3 absent recent-sales evidence differs"
                    )
            else:
                raise PrivateResearchV3Error("V3 recent-sales status differs")
            if window.get("net_units") is not None:
                _decimal(window["net_units"], field="recent net units")
            if window.get("net_revenue") is not None:
                _decimal(window["net_revenue"], field="recent net revenue")


def validate_private_v3_research_input(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "contract",
        "data_mode",
        "authority",
        "input_id",
        "parent_v2_input",
        "base_intake",
        "policy",
        "existence_evidence",
        "eligibility_controls",
        "eligibility_ledger",
        "blocked_reasons_by_variant",
        "recent_observed_sales_by_variant",
        "added_variants",
        "limitations",
        "zero_authority",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PrivateResearchV3Error("V3 research input key inventory differs")
    if (
        value.get("contract") != INPUT_CONTRACT
        or value.get("data_mode") != DATA_MODE
        or value.get("authority") != INTAKE_AUTHORITY
        or value.get("zero_authority") != ZERO_AUTHORITY
        or value.get("policy") != _expected_policy()
        or value.get("limitations") != _LIMITATIONS
    ):
        raise PrivateResearchV3Error("V3 research input contract differs")
    parent = value.get("parent_v2_input")
    base = value.get("base_intake")
    evidence = value.get("existence_evidence")
    controls = value.get("eligibility_controls")
    ledger = value.get("eligibility_ledger")
    blocked = value.get("blocked_reasons_by_variant")
    recent = value.get("recent_observed_sales_by_variant")
    added = value.get("added_variants")
    if (
        not isinstance(parent, Mapping)
        or set(parent) != {"contract", "input_id", "sha256"}
        or parent.get("contract") != PARENT_INPUT_CONTRACT
        or any(not isinstance(parent.get(key), str) or not _HEX64.fullmatch(parent[key]) for key in ("input_id", "sha256"))
        or not isinstance(base, Mapping)
        or set(base) != {"contract", "intake_id", "sha256"}
        or base.get("contract") != PRIVATE_RESEARCH_INTAKE_CONTRACT
        or any(not isinstance(base.get(key), str) or not _HEX64.fullmatch(base[key]) for key in ("intake_id", "sha256"))
    ):
        raise PrivateResearchV3Error("V3 parent identity differs")
    expected_evidence_keys = {
        "contract",
        "seed_manifest_sha256",
        "seed_manifest_storage_key",
        "seed_variants_sha256",
        "seed_variants_storage_key",
        "seed_source_sha256",
        "declared_variant_row_count",
        "identity_join",
        "timestamp_semantics",
        "history_start_rule",
        "commercial_authority",
    }
    if (
        not isinstance(evidence, Mapping)
        or set(evidence) != expected_evidence_keys
        or evidence.get("contract") != EXISTENCE_EVIDENCE_CONTRACT
        or evidence.get("seed_manifest_sha256") != SEED_MANIFEST_SHA256
        or evidence.get("seed_variants_sha256") != SEED_VARIANTS_SHA256
        or evidence.get("seed_source_sha256") != SEED_SOURCE_SHA256
        or evidence.get("declared_variant_row_count") != SEED_VARIANT_ROW_COUNT
        or evidence.get("seed_manifest_storage_key")
        != f"{_SOURCE_PREFIX}/{SEED_MANIFEST_SHA256}.json"
        or evidence.get("seed_variants_storage_key")
        != f"{_SOURCE_PREFIX}/{SEED_VARIANTS_SHA256}.csv"
        or evidence.get("identity_join") != "EXACT_SHOPIFY_VARIANT_ID_ONLY"
        or evidence.get("timestamp_semantics")
        != "SHOPIFY_VARIANT_CREATED_AT_REVIEWED_SEED_EVIDENCE"
        or evidence.get("history_start_rule")
        != "SOURCE_TIMESTAMP_DATE_STRICTLY_BEFORE_2026_05_04_NO_TIMEZONE_INFERENCE"
        or evidence.get("commercial_authority") is not False
    ):
        raise PrivateResearchV3Error("V3 existence evidence differs")
    if (
        not isinstance(controls, Mapping)
        or set(controls) != _CONTROL_KEYS
        or controls.get("contract") != ELIGIBILITY_CONTRACT
        or not isinstance(ledger, list)
        or not isinstance(blocked, Mapping)
        or not isinstance(recent, Mapping)
        or not isinstance(added, list)
        or controls.get("catalog_variant_count") != len(ledger)
        or controls.get("eligibility_ledger_sha256") != _sha(ledger)
        or controls.get("added_variants_sha256") != _sha(added)
        or controls.get("recent_observed_sales_sha256") != _sha(recent)
        or controls.get("added_prewindow_exact_id_count") != len(added)
        or controls.get("missing_evidence_variant_count") != len(blocked)
        or len(recent) != len(ledger)
        or any(
            isinstance(controls.get(key), bool)
            or not isinstance(controls.get(key), int)
            or controls[key] < 0
            for key in (
                "catalog_variant_count",
                "parent_first_day_supported_count",
                "added_prewindow_exact_id_count",
                "eligible_variant_count",
                "missing_evidence_variant_count",
            )
        )
    ):
        raise PrivateResearchV3Error("V3 eligibility controls differ")
    seen: set[str] = set()
    eligible: set[str] = set()
    parent_eligible: set[str] = set()
    added_eligible: set[str] = set()
    expected_blocked: dict[str, list[str]] = {}
    expected_reason_counts: Counter[str] = Counter()
    ordered_ids: list[str] = []
    decisions_by_id: dict[str, Mapping[str, Any]] = {}
    for decision in ledger:
        if not isinstance(decision, Mapping) or set(decision) != {
            "shopify_variant_id",
            "status",
            "existence_basis",
            "blocker_reason",
            "variant_created_at",
            "seed_row_sha256",
        }:
            raise PrivateResearchV3Error("V3 eligibility decision differs")
        variant_id = decision.get("shopify_variant_id")
        if (
            not isinstance(variant_id, str)
            or not _VARIANT_ID.fullmatch(variant_id)
            or variant_id in seen
        ):
            raise PrivateResearchV3Error("V3 eligibility identity differs")
        seen.add(variant_id)
        ordered_ids.append(variant_id)
        decisions_by_id[variant_id] = decision
        created_value = decision.get("variant_created_at")
        if created_value is not None and not isinstance(created_value, str):
            raise PrivateResearchV3Error("V3 creation evidence differs")
        created = _parse_creation(created_value or "")
        seed_sha = decision.get("seed_row_sha256")
        if seed_sha is not None and (
            not isinstance(seed_sha, str) or not _HEX64.fullmatch(seed_sha)
        ):
            raise PrivateResearchV3Error("V3 seed row evidence differs")
        if created is not None and seed_sha is None:
            raise PrivateResearchV3Error("V3 creation source evidence differs")
        if decision.get("status") == "ELIGIBLE":
            existence_basis = decision.get("existence_basis")
            if existence_basis not in {
                _PARENT_EXISTENCE_BASIS,
                _ADDED_EXISTENCE_BASIS,
            } or decision.get("blocker_reason") is not None:
                raise PrivateResearchV3Error("V3 eligible decision differs")
            if existence_basis == _ADDED_EXISTENCE_BASIS:
                if (
                    created is None
                    or created.date() >= EXPECTED_START
                    or seed_sha is None
                ):
                    raise PrivateResearchV3Error(
                        "V3 pre-window existence evidence differs"
                    )
                added_eligible.add(variant_id)
            else:
                parent_eligible.add(variant_id)
            eligible.add(variant_id)
        elif decision.get("status") == "MISSING_EVIDENCE":
            reason = decision.get("blocker_reason")
            if (
                decision.get("existence_basis") is not None
                or reason not in _MISSING_REASONS
            ):
                raise PrivateResearchV3Error("V3 blocked decision differs")
            if reason == "EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE":
                valid_reason_evidence = created is None and seed_sha is None
            elif reason == "VARIANT_CREATION_TIMESTAMP_NOT_AVAILABLE":
                valid_reason_evidence = created is None and seed_sha is not None
            elif reason == "VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED":
                valid_reason_evidence = (
                    created is not None
                    and created.date() == EXPECTED_START
                    and seed_sha is not None
                )
            else:
                valid_reason_evidence = (
                    created is not None
                    and created.date() > EXPECTED_START
                    and seed_sha is not None
                )
            if not valid_reason_evidence:
                raise PrivateResearchV3Error("V3 blocker source evidence differs")
            expected_blocked[variant_id] = [reason]
            expected_reason_counts[reason] += 1
        else:
            raise PrivateResearchV3Error("V3 eligibility status differs")
    if (
        ordered_ids != sorted(ordered_ids, key=lambda item: (len(item), item))
        or blocked != expected_blocked
        or set(recent) != seen
        or controls.get("catalog_variant_count") != len(seen)
        or controls.get("parent_first_day_supported_count")
        != len(parent_eligible)
        or controls.get("added_prewindow_exact_id_count")
        != len(added_eligible)
        or controls.get("eligible_variant_count") != len(eligible)
        or controls.get("missing_evidence_variant_count")
        != len(expected_blocked)
        or controls.get("missing_reason_counts")
        != dict(sorted(expected_reason_counts.items()))
    ):
        raise PrivateResearchV3Error("V3 eligibility population differs")
    added_ids = _validate_observation_rows(added)
    if added_ids != added_eligible:
        raise PrivateResearchV3Error("V3 added Variant eligibility differs")
    _validate_recent_sales(recent, decisions_by_id)
    basis = _json_clone(value)
    supplied = basis["input_id"]
    basis["input_id"] = None
    if not isinstance(supplied, str) or not _HEX64.fullmatch(supplied) or supplied != _sha(basis):
        raise PrivateResearchV3Error("V3 research input content address differs")
    return _json_clone(value)


def _combined_input(
    value: Mapping[str, Any], parent: Mapping[str, Any]
) -> dict[str, Any]:
    history = deepcopy(parent["history"])
    history["contract"] = COMPOSITE_HISTORY_CONTRACT
    history["parent_v2_input"] = value["parent_v2_input"]
    history["existence_evidence"] = value["existence_evidence"]
    history["eligibility_controls"] = value["eligibility_controls"]
    history["eligibility_ledger"] = value["eligibility_ledger"]
    history["blocked_reasons_by_variant"] = value["blocked_reasons_by_variant"]
    history["recent_observed_sales_by_variant"] = value[
        "recent_observed_sales_by_variant"
    ]
    history["preexistence_unproven_variant_ids"] = sorted(
        value["blocked_reasons_by_variant"], key=lambda item: (len(item), item)
    )
    history["allocation_controls"] = {
        **history["allocation_controls"],
        "eligible_variant_count": value["eligibility_controls"][
            "eligible_variant_count"
        ],
        "preexistence_unproven_variant_count": value["eligibility_controls"][
            "missing_evidence_variant_count"
        ],
    }
    return {
        "contract": INPUT_CONTRACT,
        "data_mode": DATA_MODE,
        "authority": dict(INTAKE_AUTHORITY),
        "input_id": value["input_id"],
        "base_intake": value["base_intake"],
        "source_identity": parent["source_identity"],
        "policy": parent["policy"],
        "history": history,
        "scenarios": parent["scenarios"],
        "variants": list(parent["variants"]) + list(value["added_variants"]),
        "limitations": value["limitations"],
        "zero_authority": dict(ZERO_AUTHORITY),
    }


def _grouped_decision_queue(value: Mapping[str, Any]) -> dict[str, Any]:
    missing_counts = value["eligibility_controls"]["missing_reason_counts"]
    retrievable_items = [
        {
            "stage": "CAPTURE",
            "reason": reason,
            "affected_variant_count": count,
            "scope": "EXACT_VARIANTS_LISTED_IN_ELIGIBILITY_LEDGER",
        }
        for reason, count in sorted(missing_counts.items())
        if reason
        in {
            "EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE",
            "VARIANT_CREATION_TIMESTAMP_NOT_AVAILABLE",
        }
    ] + [
        {
            "stage": "ABC",
            "reason": "EXACT_COHORT_AND_COST_AUTHORITY_REMAINS_INCOMPLETE",
            "affected_variant_count": None,
            "scope": "SHARED_GATE_AFTER_SUPPORTED_FORECAST",
        },
        {
            "stage": "NET_NEED",
            "reason": "TRUSTED_INCOMING_OPEN_ORDERS_AND_OPERATIONAL_POLICY_NOT_SUPPLIED",
            "affected_variant_count": None,
            "scope": "SHARED_GATE_AFTER_SUPPORTED_FORECAST",
        },
    ]
    owner_items = [
        {
            "stage": "CASE_QUANTITY",
            "reason": "APPROVED_PACK_NOT_SUPPLIED",
            "affected_variant_count": None,
            "scope": "SHARED_GATE_REQUIRING_APPLICABLE_APPROVED_EVIDENCE",
        },
        {
            "stage": "ECONOMICS",
            "reason": "APPROVED_SELECTED_PRICE_MARGIN_AND_FEES_NOT_SUPPLIED",
            "affected_variant_count": None,
            "scope": "SHARED_GATE_REQUIRING_APPLICABLE_APPROVED_EVIDENCE",
        },
    ]
    release_items = [
        {
            "stage": "ORDER",
            "reason": "PRODUCTION_AND_ORDER_AUTHORITY_NOT_GRANTED",
            "affected_variant_count": None,
            "scope": "FUTURE_RELEASE_AUTHORITY_ONLY",
        }
    ]
    categories = [
        {
            "category": "MACHINE_FIXABLE_DEFECTS",
            "item_count": 0,
            "items": [],
        },
        {
            "category": "RETRIEVABLE_MISSING_SOURCES",
            "item_count": len(retrievable_items),
            "items": retrievable_items,
        },
        {
            "category": "GENUINELY_OWNER_SPECIFIC_UNANSWERED_FACTS",
            "item_count": len(owner_items),
            "items": owner_items,
        },
        {
            "category": "FUTURE_RELEASE_AUTHORITY",
            "item_count": len(release_items),
            "items": release_items,
        },
    ]
    return {
        "contract": "BUFFALO_PRIVATE_RESEARCH_DECISION_QUEUE_V1",
        "deduplication_basis": "SHARED_STAGE_OR_EXACT_ELIGIBILITY_REASON",
        "categories": categories,
    }


def build_private_v3_research_projection(
    value: Mapping[str, Any],
    parent_v2_input: Mapping[str, Any],
    base_intake: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        normalized = validate_private_v3_research_input(
            _source_verified(
                value,
                proof=_V3_INPUT_SOURCE_PROOF,
                field="V3 research input",
            )
        )
        parent = validate_private_v2_research_input(
            _source_verified(
                parent_v2_input,
                proof=_INPUT_SOURCE_PROOF,
                field="parent V2 research input",
            )
        )
    except PrivateResearchV2Error as exc:
        raise PrivateResearchV3Error(str(exc)) from exc
    if (
        normalized["parent_v2_input"]
        != {
            "contract": PARENT_INPUT_CONTRACT,
            "input_id": parent["input_id"],
            "sha256": _sha(parent),
        }
        or normalized["base_intake"] != _base_intake_identity(base_intake)
        or normalized["policy"] != parent["policy"]
    ):
        raise PrivateResearchV3Error("V3 parent source binding differs")
    combined = _seal_source_verified(
        _combined_input(normalized, parent),
        proof=_V3_COMBINED_SOURCE_PROOF,
    )
    return _build_private_research_projection_from_validated_input(
        combined,
        base_intake,
        source_proof=_V3_COMBINED_SOURCE_PROOF,
        projection_contract=PROJECTION_CONTRACT,
        input_contract=INPUT_CONTRACT,
        data_mode=DATA_MODE,
        projection_extras={
            "grouped_decision_queue": _grouped_decision_queue(normalized)
        },
    )


def validate_private_v3_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate V3-specific evidence, then reuse the full V2 result validator."""

    if not isinstance(value, Mapping):
        raise PrivateResearchV3Error("V3 projection differs")
    source = _json_clone(value)
    supplied = source.pop("projection_sha256", None)
    if (
        source.get("contract") != PROJECTION_CONTRACT
        or source.get("data_mode") != DATA_MODE
        or not isinstance(supplied, str)
        or not _HEX64.fullmatch(supplied)
        or _sha_bytes(_projection_canonical(source)) != supplied
    ):
        raise PrivateResearchV3Error("V3 projection identity differs")
    forecast = source.get("forecast_research")
    history = forecast.get("history") if isinstance(forecast, Mapping) else None
    owners = source.get("owner_worksheet")
    if (
        not isinstance(history, Mapping)
        or history.get("contract") != COMPOSITE_HISTORY_CONTRACT
        or not isinstance(owners, list)
        or source.get("intake", {}).get("contract") != INPUT_CONTRACT
    ):
        raise PrivateResearchV3Error("V3 projection provenance differs")
    decisions = history.get("eligibility_ledger")
    recent = history.get("recent_observed_sales_by_variant")
    blocked = history.get("blocked_reasons_by_variant")
    controls = history.get("eligibility_controls")
    parent_descriptor = history.get("parent_v2_input")
    existence = history.get("existence_evidence")
    if (
        not isinstance(decisions, list)
        or not isinstance(recent, Mapping)
        or not isinstance(blocked, Mapping)
        or not isinstance(controls, Mapping)
        or set(controls) != _CONTROL_KEYS
        or controls.get("contract") != ELIGIBILITY_CONTRACT
        or not isinstance(parent_descriptor, Mapping)
        or set(parent_descriptor) != {"contract", "input_id", "sha256"}
        or parent_descriptor.get("contract") != PARENT_INPUT_CONTRACT
        or any(
            not isinstance(parent_descriptor.get(key), str)
            or not _HEX64.fullmatch(parent_descriptor[key])
            for key in ("input_id", "sha256")
        )
        or existence != _expected_existence_evidence()
        or controls.get("eligibility_ledger_sha256") != _sha(decisions)
        or controls.get("recent_observed_sales_sha256") != _sha(recent)
        or not isinstance(controls.get("added_variants_sha256"), str)
        or not _HEX64.fullmatch(controls["added_variants_sha256"])
        or not isinstance(controls.get("missing_reason_counts"), Mapping)
        or source.get("grouped_decision_queue")
        != _grouped_decision_queue({"eligibility_controls": controls})
        or any(
            isinstance(controls.get(key), bool)
            or not isinstance(controls.get(key), int)
            or controls[key] < 0
            for key in (
                "catalog_variant_count",
                "parent_first_day_supported_count",
                "added_prewindow_exact_id_count",
                "eligible_variant_count",
                "missing_evidence_variant_count",
            )
        )
    ):
        raise PrivateResearchV3Error("V3 projection eligibility evidence differs")
    variant_history = forecast.get("variant_observations_sha256")
    if not isinstance(variant_history, Mapping):
        raise PrivateResearchV3Error("V3 projection Variant history differs")
    decision_by_id: dict[str, Mapping[str, Any]] = {}
    parent_eligible: set[str] = set()
    added_eligible: set[str] = set()
    expected_blocked: dict[str, list[str]] = {}
    expected_reason_counts: Counter[str] = Counter()
    ordered_ids: list[str] = []
    for decision in decisions:
        if not isinstance(decision, Mapping) or set(decision) != {
            "shopify_variant_id",
            "status",
            "existence_basis",
            "blocker_reason",
            "variant_created_at",
            "seed_row_sha256",
        }:
            raise PrivateResearchV3Error("V3 projection decision differs")
        variant_id = decision.get("shopify_variant_id")
        if (
            not isinstance(variant_id, str)
            or not _VARIANT_ID.fullmatch(variant_id)
            or variant_id in decision_by_id
        ):
            raise PrivateResearchV3Error("V3 projection decision identity differs")
        ordered_ids.append(variant_id)
        decision_by_id[variant_id] = decision
        created_value = decision.get("variant_created_at")
        if created_value is not None and not isinstance(created_value, str):
            raise PrivateResearchV3Error("V3 projection creation evidence differs")
        created = _parse_creation(created_value or "")
        seed_sha = decision.get("seed_row_sha256")
        if seed_sha is not None and (
            not isinstance(seed_sha, str) or not _HEX64.fullmatch(seed_sha)
        ):
            raise PrivateResearchV3Error("V3 projection seed evidence differs")
        if created is not None and seed_sha is None:
            raise PrivateResearchV3Error("V3 projection creation source differs")
        status = decision.get("status")
        basis = decision.get("existence_basis")
        reason = decision.get("blocker_reason")
        if status == "ELIGIBLE" and reason is None:
            if basis == _PARENT_EXISTENCE_BASIS:
                parent_eligible.add(variant_id)
            elif (
                basis == _ADDED_EXISTENCE_BASIS
                and created is not None
                and created.date() < EXPECTED_START
                and seed_sha is not None
            ):
                added_eligible.add(variant_id)
            else:
                raise PrivateResearchV3Error("V3 projection eligibility differs")
        elif (
            status == "MISSING_EVIDENCE"
            and basis is None
            and reason in _MISSING_REASONS
        ):
            if reason == "EXACT_CURRENT_VARIANT_ABSENT_FROM_REVIEWED_CREATION_EVIDENCE":
                valid_reason = created is None and seed_sha is None
            elif reason == "VARIANT_CREATION_TIMESTAMP_NOT_AVAILABLE":
                valid_reason = created is None and seed_sha is not None
            elif reason == "VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED":
                valid_reason = (
                    created is not None
                    and created.date() == EXPECTED_START
                    and seed_sha is not None
                )
            else:
                valid_reason = (
                    created is not None
                    and created.date() > EXPECTED_START
                    and seed_sha is not None
                )
            if not valid_reason:
                raise PrivateResearchV3Error(
                    "V3 projection blocker source differs"
                )
            expected_blocked[variant_id] = [reason]
            expected_reason_counts[reason] += 1
        else:
            raise PrivateResearchV3Error("V3 projection decision status differs")
    eligible = parent_eligible | added_eligible
    owner_ids = {
        owner.get("shopify_variant_id")
        for owner in owners
        if isinstance(owner, Mapping)
    }
    if (
        ordered_ids != sorted(ordered_ids, key=lambda item: (len(item), item))
        or len(owner_ids) != len(owners)
        or owner_ids != set(decision_by_id)
        or set(recent) != set(decision_by_id)
        or blocked != expected_blocked
        or set(variant_history) != eligible
        or controls.get("catalog_variant_count") != len(decision_by_id)
        or controls.get("parent_first_day_supported_count")
        != len(parent_eligible)
        or controls.get("added_prewindow_exact_id_count")
        != len(added_eligible)
        or controls.get("eligible_variant_count") != len(eligible)
        or controls.get("missing_evidence_variant_count")
        != len(expected_blocked)
        or controls.get("missing_reason_counts")
        != dict(sorted(expected_reason_counts.items()))
        or history.get("preexistence_unproven_variant_ids")
        != sorted(expected_blocked, key=lambda item: (len(item), item))
    ):
        raise PrivateResearchV3Error("V3 projection population differs")
    _validate_recent_sales(recent, decision_by_id)
    try:
        expected_recent = _recent_sales(
            ordered_ids,
            _allocated_daily({"history": history}),
            decision_by_id,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PrivateResearchV3Error(
            "V3 projection recent-sales source differs"
        ) from exc
    if recent != expected_recent:
        raise PrivateResearchV3Error(
            "V3 projection recent-sales derivation differs"
        )
    derived_primary_counts = {
        scenario_id: {
            "CALCULATED": 0,
            "BLOCKED": 0,
            "NOT_APPLICABLE": 0,
            "NOT_PROCESSED": 0,
            "numerical_zero": 0,
        }
        for scenario_id in ("H3", "H10", "H17")
    }
    compact_hypotheses_by_variant: defaultdict[str, list[dict[str, Any]]] = (
        defaultdict(list)
    )
    research_rows = source.get("research_rows")
    coverage_rows = source.get("coverage_rows")
    if not isinstance(research_rows, list) or not isinstance(coverage_rows, list):
        raise PrivateResearchV3Error("V3 research-row inventory differs")
    coverage_by_id: dict[str, Mapping[str, Any]] = {}
    for coverage_row in coverage_rows:
        variant_id = (
            coverage_row.get("shopify_variant_id")
            if isinstance(coverage_row, Mapping)
            else None
        )
        if (
            not isinstance(variant_id, str)
            or variant_id in coverage_by_id
        ):
            raise PrivateResearchV3Error("V3 coverage-row identity differs")
        coverage_by_id[variant_id] = coverage_row
    if set(coverage_by_id) != set(decision_by_id):
        raise PrivateResearchV3Error("V3 coverage-row population differs")
    for research_row in research_rows:
        if not isinstance(research_row, Mapping):
            raise PrivateResearchV3Error("V3 research row differs")
        if research_row.get("supplier_name"):
            compact_hypotheses_by_variant[
                str(research_row.get("shopify_variant_id"))
            ].append(_compact_unapproved_hypothesis(research_row))
    for hypotheses in compact_hypotheses_by_variant.values():
        hypotheses.sort(key=lambda item: _canonical(item))
    for owner in owners:
        if not isinstance(owner, Mapping):
            raise PrivateResearchV3Error("V3 owner result differs")
        variant_id = owner.get("shopify_variant_id")
        decision = decision_by_id.get(variant_id)
        scenario_results = owner.get("scenario_results")
        if not isinstance(scenario_results, Mapping) or set(scenario_results) != {
            "H3",
            "H10",
            "H17",
        }:
            raise PrivateResearchV3Error("V3 owner scenario inventory differs")
        for scenario_id, summary in scenario_results.items():
            if not isinstance(summary, Mapping):
                raise PrivateResearchV3Error("V3 owner scenario differs")
            if variant_id in variant_history:
                expected_primary = (
                    "CALCULATED"
                    if summary.get("status") == "CALCULATED_RESEARCH_ONLY"
                    else "BLOCKED"
                )
            else:
                expected_primary = (
                    "NOT_APPLICABLE"
                    if decision.get("blocker_reason")
                    in {
                        "VARIANT_CREATED_DURING_FIRST_HISTORY_DAY_FULL_DAY_ZERO_NOT_SUPPORTED",
                        "VARIANT_CREATED_AFTER_HISTORY_START_FULL_WINDOW_NOT_SUPPORTED",
                    }
                    else "BLOCKED"
                )
            if summary.get("primary_status") != expected_primary:
                raise PrivateResearchV3Error("V3 owner primary outcome differs")
            derived_primary_counts[scenario_id][expected_primary] += 1
            point = summary.get("point_forecast_units")
            if expected_primary == "CALCULATED" and point is not None and _decimal(
                point, field="V3 primary forecast point"
            ) == 0:
                derived_primary_counts[scenario_id]["numerical_zero"] += 1
        primary_values = {
            summary.get("primary_status")
            for summary in scenario_results.values()
            if isinstance(summary, Mapping)
        }
        expected_coverage_primary = (
            next(iter(primary_values))
            if len(primary_values) == 1
            else "BLOCKED"
        )
        if (
            coverage_by_id[variant_id].get("primary_forecast_status")
            != expected_coverage_primary
        ):
            raise PrivateResearchV3Error(
                "V3 coverage primary outcome differs"
            )
        if variant_id not in variant_history:
            expected_next = {
                "stage": "CAPTURE",
                "reason": blocked.get(variant_id, [None])[0],
            }
        elif (
            isinstance(scenario_results, Mapping)
            and set(scenario_results) == {"H3", "H10", "H17"}
            and all(
                isinstance(result, Mapping)
                and result.get("status") == "CALCULATED_RESEARCH_ONLY"
                for result in scenario_results.values()
            )
        ):
            expected_next = {
                "stage": "ABC",
                "reason": "EXACT_COHORT_AND_COST_AUTHORITY_REMAINS_INCOMPLETE",
            }
        else:
            blocked_results = (
                [
                    result
                    for result in scenario_results.values()
                    if isinstance(result, Mapping)
                    and result.get("status") != "CALCULATED_RESEARCH_ONLY"
                ]
                if isinstance(scenario_results, Mapping)
                else []
            )
            expected_next = {
                "stage": "FORECAST",
                "reason": (
                    blocked_results[0].get("reason_codes", [None])[0]
                    if blocked_results
                    else None
                ),
            }
        if (
            not isinstance(decision, Mapping)
            or owner.get("existence_basis") != decision.get("existence_basis")
            or owner.get("recent_observed_sales") != recent.get(variant_id)
            or owner.get("unapproved_supplier_offer_summary")
            != _summarize_unapproved_hypotheses(
                compact_hypotheses_by_variant.get(variant_id, [])
            )
            or owner.get("next_missing_stage") != expected_next
        ):
            raise PrivateResearchV3Error("V3 owner evidence differs")
    if source.get("forecast_primary_status_counts") != derived_primary_counts:
        raise PrivateResearchV3Error("V3 primary result controls differ")
    for research_row in research_rows:
        variant_id = research_row.get("shopify_variant_id")
        forecast = research_row.get("forecast")
        if (
            variant_id not in coverage_by_id
            or not isinstance(forecast, Mapping)
            or forecast.get("primary_status")
            != coverage_by_id[variant_id].get("primary_forecast_status")
        ):
            raise PrivateResearchV3Error(
                "V3 research-row primary outcome differs"
            )

    # The V3 result shape intentionally extends, but never weakens, the V2
    # sidecar/evidence/owner validator.  Extra V3 fields are bound above.
    shadow = deepcopy(source)
    shadow.pop("forecast_primary_status_counts", None)
    shadow.pop("grouped_decision_queue", None)
    for owner in shadow.get("owner_worksheet", []):
        if isinstance(owner, Mapping):
            for summary in owner.get("scenario_results", {}).values():
                if isinstance(summary, dict):
                    summary.pop("primary_status", None)
    shadow["contract"] = "BUFFALO_PRIVATE_RESEARCH_PROJECTION_V2"
    shadow["intake"]["contract"] = PARENT_INPUT_CONTRACT
    shadow["projection_sha256"] = _sha_bytes(_projection_canonical(shadow))
    try:
        from .private_research_projection import _validated_projection

        _validated_projection(shadow)
    except Exception as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise PrivateResearchV3Error("V3 projection result evidence differs") from exc
    return {**source, "projection_sha256": supplied}


def input_manifest_key(input_id: str) -> str:
    if not isinstance(input_id, str) or not _HEX64.fullmatch(input_id):
        raise PrivateResearchV3Error("V3 research input ID differs")
    return f"{_INPUT_PREFIX}/{input_id}.json"


def write_private_v3_research_input(
    private_root: str | Path,
    value: Mapping[str, Any],
    *,
    seed_manifest_path: str | Path,
    seed_variants_path: str | Path,
) -> dict[str, Any]:
    root = validate_private_root(Path(private_root))
    normalized = validate_private_v3_research_input(
        _source_verified(
            value,
            proof=_V3_INPUT_SOURCE_PROOF,
            field="V3 research input",
        )
    )
    storage = LocalFilesystemStorage(root)
    for path, key, expected_sha, field in (
        (
            seed_manifest_path,
            normalized["existence_evidence"]["seed_manifest_storage_key"],
            SEED_MANIFEST_SHA256,
            "seed manifest",
        ),
        (
            seed_variants_path,
            normalized["existence_evidence"]["seed_variants_storage_key"],
            SEED_VARIANTS_SHA256,
            "seed Variant evidence",
        ),
    ):
        raw = _read_exact_file(path, expected_sha, field=field)
        _ensure_private_parent(root, key)
        if storage.exists(key):
            if storage.get_bytes(key) != raw:
                raise PrivateResearchV3Error(f"immutable {field} differs")
        else:
            storage.put_bytes_once(key, raw)
    key = input_manifest_key(normalized["input_id"])
    data = _canonical(normalized)
    _ensure_private_parent(root, key)
    if storage.exists(key):
        if storage.get_bytes(key) != data:
            raise PrivateResearchV3Error("immutable V3 research input differs")
    else:
        storage.put_bytes_once(key, data)
    for stored_path, expected_sha, field in (
        (
            root / normalized["existence_evidence"]["seed_manifest_storage_key"],
            SEED_MANIFEST_SHA256,
            "stored seed manifest",
        ),
        (
            root / normalized["existence_evidence"]["seed_variants_storage_key"],
            SEED_VARIANTS_SHA256,
            "stored seed Variant evidence",
        ),
        (
            root / key,
            _sha_bytes(data),
            "stored V3 research input",
        ),
    ):
        _read_exact_file(
            stored_path,
            expected_sha,
            field=field,
            require_private_mode=True,
        )
    # ``normalized`` was built from a source-authenticated capability and the
    # exact immutable bytes above were re-read and checked.  Return that same
    # in-process capability so the orchestration path does not perform a second
    # full parent/raw-source replay before numerical evaluation.  Independent
    # callers and app startup still use ``read_private_v3_research_bundle``.
    return _seal_source_verified(normalized, proof=_V3_INPUT_SOURCE_PROOF)


def _replay_private_v3_input(
    root: Path, value: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    normalized = validate_private_v3_research_input(value)
    parent, base_intake = read_private_v2_research_bundle(
        root, normalized["parent_v2_input"]["input_id"]
    )
    storage = LocalFilesystemStorage(root)
    manifest_path = root / normalized["existence_evidence"]["seed_manifest_storage_key"]
    variants_path = root / normalized["existence_evidence"]["seed_variants_storage_key"]
    # Resolve through storage first so internal symlink/traversal rules are applied.
    storage.get_bytes(normalized["existence_evidence"]["seed_manifest_storage_key"])
    storage.get_bytes(normalized["existence_evidence"]["seed_variants_storage_key"])
    expected = build_private_v3_research_input(
        base_intake,
        parent_v2_input=parent,
        seed_manifest_path=manifest_path,
        seed_variants_path=variants_path,
        _require_private_sources=True,
    )
    if expected != normalized:
        raise PrivateResearchV3Error("V3 research input semantic replay differs")
    return expected, parent, base_intake


def read_private_v3_research_bundle(
    private_root: str | Path, input_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = validate_private_root(Path(private_root))
    data = LocalFilesystemStorage(root).get_bytes(input_manifest_key(input_id))
    try:
        value = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivateResearchV3Error("V3 research input is unreadable") from exc
    normalized, parent, base = _replay_private_v3_input(root, value)
    if data != _canonical(normalized):
        raise PrivateResearchV3Error("V3 research input is not canonical")
    return normalized, parent, base


def read_private_v3_research_input(
    private_root: str | Path, input_id: str
) -> dict[str, Any]:
    return read_private_v3_research_bundle(private_root, input_id)[0]
