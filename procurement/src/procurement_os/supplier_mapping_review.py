"""Deterministic, review-only supplier offer-family and monthly reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from typing import Any, Iterable, Mapping, Sequence
import unicodedata
from urllib.parse import quote

from .supplier_review_package import REVIEW_LABEL, ReviewPackage, _canonical_json


REVIEW_PREVIEW_LABEL = "REVIEW PREVIEW — NOT APPROVED"
_A1_PACKAGE_KIND = "V5_DAYTIME_A1_COMPLETE_SNAPSHOT"
_A1_LEDGER_TABLE_COUNTS: Mapping[str, int] = {
    "owner_decisions": 20,
    "owner_decision_display_scopes": 20,
    "requests__prior_owner_answers_do_not_reask": 20,
    "requests__retained_owner_questions_policy_check": 3,
    "source_review_overlays": 25,
    "complete_combos": 460,
    "combo_components": 1_822,
    "combo_validation": 460,
    "fixed_combo_component_relationships_v5": 249,
    "remaining_combo_component_relationships_v5": 19,
    "remaining_combo_total_reviews_v5": 7,
    "remaining_supplier_family_reviews_v5": 24,
}
_A1_LEDGER_DOCUMENT_KEYS = (
    "owner_decision_ledger",
    "owner_decision_display_scopes",
    "prior_owner_answers_do_not_reask",
    "retained_owner_questions_policy_check",
    "source_review_overlays",
    "combo_review_ledger",
)


BLOCKED_DISPOSITIONS = frozenset(
    {
        "CONFLICT",
        "POLICY EXCLUDED",
        "POLICY_EXCLUDED",
        "REJECTED",
        "REJECTED_ATTRIBUTE_CONFLICT",
        "REJECTED_IDENTITY_CONFLICT",
        "SEARCH_LEAD_ONLY",
        "NOT FOUND",
        "NOT_FOUND",
        "NOT PROCESSED",
        "NOT_PROCESSED",
        "SUPPLIER SOURCE MISSING",
        "SUPPLIER_SOURCE_MISSING",
    }
)

REPRESENTATION_GAP_CATALOG: tuple[dict[str, Any], ...] = tuple(
    {
        "gap_id": gap_id,
        "area": area,
        "current_handling": current_handling,
        "later_route": later_route,
        "later_route_status": "SEPARATE_REVIEW_REQUIRED",
        "authority": "REVIEW_ONLY",
        "application_changes": 0,
        "source": "CODE_OWNED_ROUTE_DERIVED_FROM_V4_1_CONTRACT_GAPS",
    }
    for gap_id, area, current_handling, later_route in (
        ("G01", "Unapproved mappings", "BLOCKED_CANDIDATE_SIDECAR", "MAPPING_DECISION_AND_CROSSWALK"),
        ("G02", "Extraction/activation state", "NULL_TARGET_UNAPPROVED_REVIEW", "GUARDED_FUTURE_IMPORT_WORKFLOW"),
        ("G03", "External evidence IDs", "EXTERNAL_ID_CROSSWALK_APP_IDS_NULL", "IMMUTABLE_SOURCE_OCCURRENCE_LINK"),
        ("G04", "Source hashes/pages", "MULTI_SOURCE_EVIDENCE_SIDECAR", "TYPED_SOURCE_PROVENANCE"),
        ("G05", "Nested physical/inner/retail units", "SEPARATE_UNIT_EVIDENCE_SIDECAR", "REVIEWED_PACKAGING_MODEL"),
        ("G06", "Split-fee scope/inclusion", "RAW_SCOPED_FEE_EVIDENCE_NO_INFERENCE", "OFFER_TIER_FEE_SCHEDULE"),
        ("G07", "Territory applicability", "UNRESOLVED_TERRITORY_BLOCKS_USE", "TYPED_APPLICABILITY_MODEL"),
        ("G08", "Fixed combos", "PARENT_COMPONENT_TOTAL_PRESERVED", "COMPLETE_COMBO_CONTRACT"),
        ("G09", "Gift/alternate packaging", "DISTINCT_CONFIGURATION_OCCURRENCES", "PACKAGING_SELECTION_AND_SUBSTITUTABILITY"),
        ("G10", "Contradictory published arithmetic", "PRINTED_AND_DERIVED_VALUES_SEPARATE", "TYPED_PRICE_BASIS_AND_RESOLUTION"),
        ("G11", "Missing/multiple dates", "NULL_DATE_DEPENDENCY_NO_INVENTION", "ROW_OR_PROGRAM_PERIOD_MODEL"),
        ("G12", "Threshold shapes/order units", "RAW_UNSUPPORTED_THRESHOLD_PRESERVED", "GENERALIZED_THRESHOLD_ORDER_UNIT"),
        ("G13", "Conditional/parallel assortments", "PROGRAM_TERMS_AND_MEMBERS_PRESERVED", "CONDITIONAL_PROGRAM_RELATIONS"),
        ("G14", "Duplicate occurrences/distinct programs", "ALL_OCCURRENCES_AND_TIER_IDS_PRESERVED", "SOURCE_OCCURRENCE_PROGRAM_ENTITY"),
        ("G15", "Same-code offer alternatives", "EXTERNAL_ALTERNATIVES_NO_MERGE", "MULTI_OFFER_SELECTION_DECISION"),
        ("G16", "Owner decisions/review status", "SCOPED_DECISION_HISTORY_NO_APPROVAL", "IMMUTABLE_DECISION_LEDGER_UI"),
        ("G17", "Inventory time scope", "HISTORICAL_SNAPSHOT_ONLY_NO_REFRESH", "AUTHORIZED_INVENTORY_CAPTURE_PATH"),
        ("G18", "Identity attributes/proof provenance", "RAW_ATTRIBUTE_PROVENANCE_SIDECAR", "TYPED_IDENTITY_EVIDENCE_MODEL"),
        ("G19", "Live approval/vendor registry", "UNKNOWN_LIVE_REGISTRY_FAIL_CLOSED", "AUTHENTICATED_PRIVATE_REGISTRY_CHECK"),
        ("G20", "Parser size/vendor coverage", "OFFLINE_PACKAGE_NOT_IMPORT_BATCH", "COMPLETE_VENDOR_PERIOD_ADAPTER_WITH_LIMIT"),
        ("G21", "Mapping labels versus price lifecycle", "MAPPING_LABELS_PRESERVED_SEPARATELY", "DEDICATED_MAPPING_REVIEW_LIFECYCLE"),
    )
)

_MISSING = object()


class SupplierReviewError(ValueError):
    pass


def _is_missing_or_blank(value: Any) -> bool:
    return value is _MISSING or value is None or value == ""


def _first_present(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return _MISSING


def _text(row: Mapping[str, Any], *names: str) -> str | None:
    value = _first_present(row, *names)
    if value is _MISSING or value is None or str(value) == "":
        return None
    return str(value)


def _aliased_value(row: Mapping[str, Any], canonical: str, aliases: Sequence[str]) -> tuple[Any, list[dict[str, Any]]]:
    present = [(name, row[name]) for name in aliases if name in row]
    if not present:
        return _MISSING, []
    value = present[0][1]
    conflicts = [
        {
            "canonical_field": canonical,
            "selected_field": present[0][0],
            "selected_value": value,
            "conflicting_field": name,
            "conflicting_value": candidate,
        }
        for name, candidate in present[1:]
        if type(candidate) is not type(value) or _canonical_json(candidate) != _canonical_json(value)
    ]
    return value, conflicts


def _identity_text(row: Mapping[str, Any], label: str, aliases: Sequence[str]) -> str:
    present = [(name, row[name]) for name in aliases if name in row]
    if not present:
        raise SupplierReviewError(f"occurrence requires {label} as exact text")
    first_name, first_value = present[0]
    if not isinstance(first_value, str) or not first_value.strip():
        raise SupplierReviewError(f"{first_name} must be nonblank exact text")
    for name, value in present[1:]:
        if not isinstance(value, str) or not value.strip():
            raise SupplierReviewError(f"{name} must be nonblank exact text")
        if value != first_value:
            raise SupplierReviewError(f"conflicting {label} identity aliases")
    return first_value


def _decimal_alias(row: Mapping[str, Any], canonical: str, aliases: Sequence[str]) -> tuple[Decimal | None, list[dict[str, Any]]]:
    value, conflicts = _aliased_value(row, canonical, aliases)
    if value is _MISSING:
        return None, conflicts
    return _decimal(value), conflicts


def _normalised_supplier_term(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _exception(
    code: str,
    *,
    identity: tuple[str, str, str],
    affected_fields: Sequence[str],
    raw_values: Mapping[str, Any],
    source_references: Mapping[str, Any],
    blocking: bool = True,
    severity: str = "BLOCKER",
    dependency_ids: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "scope": {
            "variant_id": identity[0],
            "vendor": identity[1],
            "source_occurrence_id": identity[2],
        },
        "affected_fields": list(affected_fields),
        "raw_values": dict(raw_values),
        "source_references": dict(source_references),
        "blocks_use": blocking,
        "dependency_ids": sorted(str(value) for value in dependency_ids),
    }


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise SupplierReviewError("boolean is not a numeric review value")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SupplierReviewError(f"invalid exact decimal {value!r}") from exc
    if not result.is_finite():
        raise SupplierReviewError("nonfinite review number")
    return result


def occurrence_identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return the only supported occurrence identity.

    Supplier SKU and normalized name are deliberately absent: both may be
    reused and are evidence, never durable identity.
    """

    variant = _identity_text(
        row,
        "Variant ID",
        ("variant_id", "canonical_variant_id", "shopify_variant_id"),
    )
    vendor = _identity_text(row, "vendor", ("vendor", "vendor_name", "supplier"))
    occurrence = _identity_text(
        row,
        "source occurrence ID",
        ("source_occurrence_id", "occurrence_id", "offer_id", "source_offer_id"),
    )
    return variant, vendor, occurrence


def tier_identity(row: Mapping[str, Any]) -> tuple[tuple[str, str, str], str]:
    occurrence = occurrence_identity(row)
    tier = _identity_text(row, "source Tier ID", ("source_tier_id", "tier_id"))
    return occurrence, tier


def protect_spreadsheet_text(value: Any) -> str:
    """Protect formula-leading display cells without mutating source values."""

    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _conversions(row: Mapping[str, Any]) -> dict[str, Any]:
    definitions = {
        "physical_units_per_supplier_case": (
            "physical_units_per_supplier_case",
            "physical_containers_per_case",
            "physical_bottles_per_case",
            "physical_cans_per_case",
        ),
        "retail_units_per_supplier_case": ("supplier_case_pack", "retail_units_per_case"),
        "shopify_units_per_supplier_case": (
            "proposed_shopify_sellable_units_per_case",
            "shopify_units_per_case",
        ),
        "inner_pack_units": ("supplier_retail_pack", "containers_per_retail_unit", "inner_pack_units"),
        "supplier_order_increment": ("supplier_order_increment", "order_increment"),
        "supplier_qualifying_units_per_case": (
            "supplier_qualifying_units_per_case",
            "qualifying_units_per_case",
        ),
    }
    conversions: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    for canonical, aliases in definitions.items():
        conversions[canonical], found = _decimal_alias(row, canonical, aliases)
        conflicts.extend(found)
    qualifying, found = _aliased_value(
        row,
        "supplier_qualifying_unit",
        ("supplier_qualifying_unit", "qualifying_unit", "break_unit"),
    )
    conflicts.extend(found)
    conversions["supplier_qualifying_unit"] = None if _is_missing_or_blank(qualifying) else str(qualifying)
    order_unit, found = _aliased_value(
        row, "supplier_order_unit", ("supplier_order_unit", "order_unit")
    )
    conflicts.extend(found)
    conversions["supplier_order_unit"] = (
        None if _is_missing_or_blank(order_unit) else str(order_unit)
    )
    conversions["alias_conflicts"] = conflicts
    return conversions


def _commercial_fingerprint(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(row))).hexdigest()


def _dependency_ids(row: Mapping[str, Any]) -> list[str]:
    raw = _first_present(
        row,
        "remaining_dependencies",
        "dependencies",
        "v4_1_named_dependencies",
        "v4_1_required_identity_dependencies",
        "review_requirements",
    )
    if raw is _MISSING or raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    result = []
    for value in values:
        if isinstance(value, Mapping):
            identifier = _text(value, "dependency_id", "id", "type", "dependency")
        else:
            identifier = str(value)
        if identifier:
            result.append(identifier)
    return sorted(set(result))


def _source_references(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_file": row.get("source_file"),
        "source_sha256": row.get("source_sha256"),
        "source_page": row.get("source_page", row.get("physical_page")),
        "printed_page": row.get("printed_page"),
        "source_reference": row.get("source_reference", row.get("source_evidence")),
    }


def _occurrence_exceptions(
    row: Mapping[str, Any],
    *,
    identity: tuple[str, str, str],
    disposition: str,
    alias_conflicts: Sequence[Mapping[str, Any]],
    conversions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    source_references = _source_references(row)
    dependency_ids = _dependency_ids(row)
    if alias_conflicts:
        result.append(
            _exception(
                "ALIAS_FIELD_CONFLICT",
                identity=identity,
                affected_fields=sorted({str(item["canonical_field"]) for item in alias_conflicts}),
                raw_values={"conflicts": [dict(item) for item in alias_conflicts]},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    owner_status = (_text(row, "owner_decision_status", "owner_review_status") or "").upper()
    if "STALE" in owner_status:
        result.append(
            _exception(
                "STALE_OWNER_DECISION",
                identity=identity,
                affected_fields=("owner_decision_status",),
                raw_values={"owner_decision_status": owner_status},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    if disposition in {"SUPPLIER SOURCE MISSING", "SUPPLIER_SOURCE_MISSING"}:
        result.append(
            _exception(
                "SUPPLIER_BOOK_MISSING",
                identity=identity,
                affected_fields=("source_file", "source_status"),
                raw_values={"source_file": row.get("source_file"), "disposition": disposition},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    price_fields_present = any(
        field_name in row
        for field_name in ("case_price", "unit_price", "published_case_price", "published_unit_price")
    )
    case_price, case_conflicts = _decimal_alias(
        row, "case_price", ("case_price", "published_case_price")
    )
    unit_price, unit_conflicts = _decimal_alias(
        row, "unit_price", ("unit_price", "published_unit_price")
    )
    price_alias_conflicts = [*case_conflicts, *unit_conflicts]
    if price_alias_conflicts:
        result.append(
            _exception(
                "ALIAS_FIELD_CONFLICT",
                identity=identity,
                affected_fields=("case_price", "unit_price"),
                raw_values={"conflicts": price_alias_conflicts},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    if price_fields_present and case_price is None and unit_price is None:
        result.append(
            _exception(
                "PRICE_EVIDENCE_BLANK",
                identity=identity,
                affected_fields=("case_price", "unit_price"),
                raw_values={"case_price": case_price, "unit_price": unit_price},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    units = conversions.get("shopify_units_per_supplier_case")
    if case_price is not None and unit_price is not None and isinstance(units, Decimal) and units > 0:
        derived = unit_price * units
        difference = abs(case_price - derived)
        if difference > Decimal("0.01"):
            result.append(
                _exception(
                    "PUBLISHED_PRICE_ARITHMETIC_CONFLICT",
                    identity=identity,
                    affected_fields=("case_price", "unit_price", "shopify_units_per_supplier_case"),
                    raw_values={
                        "printed_case_price": case_price,
                        "printed_unit_price": unit_price,
                        "shopify_units_per_supplier_case": units,
                        "derived_case_equivalent": derived,
                        "absolute_difference": difference,
                    },
                    source_references=source_references,
                    dependency_ids=dependency_ids,
                )
            )
    per_ounce = _first_present(row, "per_ounce_price", "price_per_ounce")
    if per_ounce is not _MISSING and per_ounce is not None and per_ounce != "":
        result.append(
            _exception(
                "PER_OUNCE_NOT_A_TIER_PRICE",
                identity=identity,
                affected_fields=("per_ounce_price",),
                raw_values={"per_ounce_price": per_ounce},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    split_value = _first_present(row, "split_inclusive_price", "split_price", "split_inclusive_charge")
    if split_value is not _MISSING and split_value is not None and split_value != "":
        result.append(
            _exception(
                "SPLIT_INCLUSION_REQUIRES_SCOPE",
                identity=identity,
                affected_fields=("split_inclusive_price",),
                raw_values={"split_inclusive_value": split_value},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    if row.get("reviewed_null") is True or "REVIEWED_NULL" in owner_status:
        result.append(
            _exception(
                "REVIEWED_NULL_PRESERVED",
                identity=identity,
                affected_fields=tuple(
                    sorted(
                        field_name
                        for field_name in (
                            "supplier_qualifying_units_per_case",
                            "supplier_order_increment",
                            "case_price",
                            "unit_price",
                        )
                        if field_name in row and row[field_name] is None
                    )
                ),
                raw_values={"reviewed_null": True},
                source_references=source_references,
                dependency_ids=dependency_ids,
            )
        )
    approval = (_text(row, "approval_status", "mapping_approval_status") or "").upper()
    if "UNAPPROVED" in approval or row.get("new_mapping_approval") is False:
        result.append(
            _exception(
                "MAPPING_APPROVAL_MISSING",
                identity=identity,
                affected_fields=("approval_status",),
                raw_values={"approval_status": approval or None},
                source_references=source_references,
                severity="WARN",
                dependency_ids=dependency_ids,
            )
        )
    dependency_text = " ".join(dependency_ids).upper()
    for marker, code in (
        ("PACK", "PACK_EVIDENCE_MISSING"),
        ("FEE", "FEE_EVIDENCE_MISSING"),
        ("PROGRAM", "PROGRAM_EVIDENCE_MISSING"),
        ("TERRITOR", "TERRITORY_EVIDENCE_MISSING"),
        ("APPROV", "APPROVAL_EVIDENCE_MISSING"),
        ("PRICE", "PRICE_BASIS_UNRESOLVED"),
    ):
        if marker in dependency_text:
            result.append(
                _exception(
                    code,
                    identity=identity,
                    affected_fields=("dependencies",),
                    raw_values={"matching_dependencies": [item for item in dependency_ids if marker in item.upper()]},
                    source_references=source_references,
                    dependency_ids=dependency_ids,
                )
            )
    return result


def _supplier_name_vocabulary(occurrences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    evidence: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    fields: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for occurrence in occurrences:
        raw_value = occurrence.get("raw")
        raw = raw_value if isinstance(raw_value, Mapping) else {}
        for field_name in ("supplier_title", "reviewed_supplier_title", "supplier_description"):
            value = raw.get(field_name)
            if isinstance(value, str) and value != "":
                key = (str(occurrence["vendor"]), value)
                evidence[key].add(str(occurrence["occurrence_id"]))
                fields[key].add(field_name)
        if not raw:
            value = occurrence.get("supplier_description")
            if isinstance(value, str) and value != "":
                key = (str(occurrence["vendor"]), value)
                evidence[key].add(str(occurrence["occurrence_id"]))
                fields[key].add("supplier_description")
    result = []
    for (vendor, observed), occurrence_ids in sorted(evidence.items()):
        record = {
            "authority": "CANDIDATE_ONLY",
            "vendor": vendor,
            "observed_supplier_text": observed,
            "normalized_candidate": _normalised_supplier_term(observed),
            "source_fields": sorted(fields[(vendor, observed)]),
            "source_occurrence_ids": sorted(occurrence_ids),
            "approval_created": False,
        }
        record["evidence_fingerprint"] = hashlib.sha256(_canonical_json(record)).hexdigest()
        result.append(record)
    return result


def _snapshot_alias_candidates(occurrences: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        grouped[(str(occurrence["variant_id"]), str(occurrence["vendor"]))].append(occurrence)
    candidates: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for (variant_id, vendor), values in grouped.items():
        for occurrence in values:
            current_code = occurrence.get("supplier_sku")
            raw_value = occurrence.get("raw")
            if not isinstance(raw_value, Mapping):
                continue
            prior_code = _text(
                raw_value,
                "predecessor_supplier_sku",
                "previous_supplier_code",
                "replaces_supplier_sku",
            )
            if prior_code and current_code not in {None, ""} and prior_code != str(current_code):
                key = (variant_id, vendor, prior_code, str(current_code))
                candidates[key] = {
                    "authority": "CANDIDATE_ONLY",
                    "variant_id": variant_id,
                    "vendor": vendor,
                    "before_supplier_code": prior_code,
                    "after_supplier_code": str(current_code),
                    "source_occurrence_id": occurrence["occurrence_id"],
                    "basis": "EXPLICIT_REPLACEMENT_EVIDENCE",
                    "retirement_created": False,
                    "approval_created": False,
                }
    return [candidates[key] for key in sorted(candidates)]


def build_offer_family_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    variants: Sequence[Mapping[str, Any]] = (),
    representation_gaps: Sequence[Mapping[str, Any] | str] = (),
    cohorts: Mapping[str, Any] | None = None,
    integrity: Mapping[str, Any] | None = None,
    dependency_groups: Sequence[Mapping[str, Any]] = (),
    retain_raw_records: bool = True,
    include_flat_occurrences: bool = True,
) -> dict[str, Any]:
    """Build an occurrence-preserving projection; never select an offer."""

    occurrences: list[dict[str, Any]] = []
    identities: dict[tuple[str, str, str], tuple[str, int]] = {}
    duplicate_count = 0
    conflicting_occurrences: list[dict[str, Any]] = []
    duplicate_occurrences: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    exception_counts: Counter[str] = Counter()
    sku_vendors: defaultdict[str, set[str]] = defaultdict(set)
    sku_variants: defaultdict[str, set[str]] = defaultdict(set)
    dependencies: Counter[str] = Counter()

    for position, source_row in enumerate(rows, start=1):
        row = dict(source_row)
        key = occurrence_identity(row)
        fingerprint = _commercial_fingerprint(row)
        if key in identities:
            if identities[key][0] == fingerprint:
                duplicate_count += 1
                duplicate: dict[str, Any] = {
                    "identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]},
                    "row": position,
                    "source_references": _source_references(row),
                    "record_sha256": fingerprint,
                }
                if retain_raw_records:
                    duplicate["raw"] = row
                duplicate_occurrences.append(duplicate)
                continue
            prior_position = identities[key][1]
            conflict: dict[str, Any] = {
                "identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]},
                "first_row": prior_position,
                "conflicting_row": position,
                "first_commercial_sha256": identities[key][0],
                "conflicting_commercial_sha256": fingerprint,
            }
            if retain_raw_records:
                conflict["raw"] = row
            conflicting_occurrences.append(conflict)
            exception_counts["CONFLICTING_COMMERCIAL_FACTS"] += 1
            for retained in occurrences:
                if retained["variant_id"] == key[0] and retained["vendor"] == key[1] and retained["occurrence_id"] == key[2]:
                    retained["blocked"] = True
                    retained["effective_disposition"] = "BLOCKED"
                    retained["guard_reasons"] = sorted(set(retained["guard_reasons"] + ["CONFLICTING_COMMERCIAL_FACTS"]))
                    conflict_exception = _exception(
                            "CONFLICTING_COMMERCIAL_FACTS",
                            identity=key,
                            affected_fields=("commercial_facts",),
                            raw_values={
                                "first_row": prior_position,
                                "conflicting_row": position,
                                "first_commercial_sha256": identities[key][0],
                                "conflicting_commercial_sha256": fingerprint,
                            },
                            source_references=_source_references(row),
                            dependency_ids=_dependency_ids(row),
                        )
                    if retain_raw_records:
                        retained["exceptions"].append(conflict_exception)
                    retained["exception_codes"] = sorted(
                        set(retained["exception_codes"] + ["CONFLICTING_COMMERCIAL_FACTS"])
                    )
                    break
            continue
        identities[key] = (fingerprint, position)
        disposition_value, disposition_conflicts = _aliased_value(
            row,
            "candidate_disposition",
            ("candidate_disposition", "mapping_status", "disposition", "source_status", "status"),
        )
        disposition = (
            "UNRESOLVED"
            if _is_missing_or_blank(disposition_value)
            else str(disposition_value).upper()
        )
        catalog_value, catalog_conflicts = _aliased_value(
            row,
            "authoritative_catalog_status",
            ("authoritative_catalog_status", "catalog_status", "variant_status"),
        )
        catalog_status = (
            "" if _is_missing_or_blank(catalog_value) else str(catalog_value).upper()
        )
        sku_value, sku_conflicts = _aliased_value(
            row,
            "supplier_code",
            ("supplier_code", "supplier_sku", "sku"),
        )
        if _is_missing_or_blank(sku_value):
            sku = None
        elif isinstance(sku_value, str):
            sku = sku_value
        else:
            sku = None
            sku_conflicts.append(
                {
                    "canonical_field": "supplier_code",
                    "selected_field": "supplier_code",
                    "selected_value": sku_value,
                    "conflicting_field": "required_type",
                    "conflicting_value": "EXACT_TEXT",
                }
            )
        program_value, program_conflicts = _aliased_value(
            row,
            "offer_type",
            ("offer_type", "package_type", "program_type"),
        )
        program_type = (
            "UNSPECIFIED"
            if _is_missing_or_blank(program_value)
            else str(program_value)
        )
        abv_value, abv_conflicts = _aliased_value(
            row, "abv_percent", ("abv_percent", "alcohol_by_volume")
        )
        vintage_value, vintage_conflicts = _aliased_value(
            row, "supplier_vintage", ("supplier_vintage", "vintage")
        )
        territory_value, territory_conflicts = _aliased_value(
            row, "territory", ("territory", "territory_applicability")
        )
        effective_from, effective_from_conflicts = _aliased_value(
            row, "effective_from", ("effective_from", "valid_from", "start_date")
        )
        effective_through, effective_through_conflicts = _aliased_value(
            row,
            "effective_through",
            ("effective_through", "effective_to", "valid_to", "end_date"),
        )
        conversions = _conversions(row)
        alias_conflicts = [
            *disposition_conflicts,
            *catalog_conflicts,
            *sku_conflicts,
            *program_conflicts,
            *abv_conflicts,
            *vintage_conflicts,
            *territory_conflicts,
            *effective_from_conflicts,
            *effective_through_conflicts,
            *conversions["alias_conflicts"],
        ]
        exceptions = _occurrence_exceptions(
            row,
            identity=key,
            disposition=disposition,
            alias_conflicts=alias_conflicts,
            conversions=conversions,
        )
        exception_counts.update(item["code"] for item in exceptions)
        policy_excluded = row.get("policy_excluded") is True
        blocked = (
            disposition in BLOCKED_DISPOSITIONS
            or catalog_status in BLOCKED_DISPOSITIONS
            or policy_excluded
            or any(item["blocks_use"] for item in exceptions)
        )
        candidate = disposition in {"PROPOSED MATCH", "PROPOSED_MATCH", "PROPOSED_REVIEW_CANDIDATE"}
        status_counts[disposition] += 1
        if sku:
            sku_vendors[sku].add(key[1])
            sku_variants[sku].add(key[0])
        for dependency in _dependency_ids(row):
            dependencies[dependency] += 1
        guard_reasons = []
        if catalog_status == "CONFLICT":
            guard_reasons.append("CATALOG_CONFLICT")
        if catalog_status in {"POLICY EXCLUDED", "POLICY_EXCLUDED"}:
            guard_reasons.append("CATALOG_POLICY_EXCLUDED")
        if policy_excluded:
            guard_reasons.append("POLICY_EXCLUDED")
        if disposition in BLOCKED_DISPOSITIONS:
            guard_reasons.append("SOURCE_DISPOSITION_BLOCKED")
        if any(item["code"] == "STALE_OWNER_DECISION" for item in exceptions):
            guard_reasons.append("STALE_OWNER_DECISION")
        if alias_conflicts:
            guard_reasons.append("ALIAS_FIELD_CONFLICT")
        occurrence: dict[str, Any] = {
                "authority": "UNAPPROVED_REVIEW_EVIDENCE",
                "blocked": blocked,
                "candidate_only": candidate,
                "catalog_status": catalog_status or None,
                "conversions": conversions,
                "disposition": disposition,
                "effective_disposition": "BLOCKED" if blocked else "REVIEW_CANDIDATE",
                "guard_reasons": sorted(set(guard_reasons)),
                "evidence": _source_references(row),
                "exceptions": exceptions if retain_raw_records else [],
                "exception_codes": sorted({item["code"] for item in exceptions}),
                "applicability": {
                    "effective_from": None
                    if _is_missing_or_blank(effective_from)
                    else effective_from,
                    "effective_through": None
                    if _is_missing_or_blank(effective_through)
                    else effective_through,
                    "territory": None
                    if _is_missing_or_blank(territory_value)
                    else territory_value,
                },
                "expression": {
                    "abv_percent": _decimal(
                        None if _is_missing_or_blank(abv_value) else abv_value
                    ),
                    "proof": _decimal(row.get("proof")),
                    "vintage": None
                    if _is_missing_or_blank(vintage_value)
                    else vintage_value,
                    "vintage_raw": row.get("supplier_vintage_raw"),
                    "territory": None
                    if _is_missing_or_blank(territory_value)
                    else territory_value,
                    "package_type": program_type,
                    "components": row.get("components"),
                },
                "occurrence_id": key[2],
                "identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]},
                "program_type": program_type,
                "supplier_description": _text(
                    row, "reviewed_supplier_title", "supplier_title", "supplier_description"
                ),
                "supplier_sku": sku,
                "variant_id": key[0],
                "vendor": key[1],
                "source_record_sha256": fingerprint,
            }
        if retain_raw_records:
            occurrence["raw"] = row
        occurrences.append(occurrence)

    occurrences.sort(key=lambda item: (item["variant_id"], item["vendor"], item["occurrence_id"]))
    variant_rows = [dict(row) for row in variants]
    variant_rows.sort(key=lambda row: str(row.get("variant_id", row.get("canonical_variant_id", ""))))
    families_by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        families_by_variant[occurrence["variant_id"]].append(occurrence)
    offer_families = [
        {"variant_id": variant_id, "alternatives": alternatives}
        for variant_id, alternatives in sorted(families_by_variant.items())
    ]
    all_exceptions = (
        sorted(
            (item for occurrence in occurrences for item in occurrence["exceptions"]),
            key=lambda item: (
                item["scope"]["variant_id"],
                item["scope"]["vendor"],
                item["scope"]["source_occurrence_id"],
                item["code"],
            ),
        )
        if retain_raw_records
        else []
    )
    gap_evidence = [
        dict(gap) if isinstance(gap, Mapping) else {"description": str(gap)}
        for gap in representation_gaps
    ]
    result: dict[str, Any] = {
        "label": REVIEW_LABEL,
        "status": "PASS",
        "authority": "REVIEW_ONLY",
        "cohorts": dict(cohorts or {}),
        "summary": {
            "source_rows": len(rows),
            "distinct_occurrences": len(occurrences),
            "exact_duplicates_collapsed": duplicate_count,
            "conflicting_occurrences": len(conflicting_occurrences),
            "dispositions": dict(sorted(status_counts.items())),
        },
        "offer_families": offer_families,
        "simultaneous_alternatives": [
            {"variant_id": family["variant_id"], "count": len(family["alternatives"])}
            for family in offer_families
            if len(family["alternatives"]) > 1
        ],
        "duplicate_occurrences": duplicate_occurrences,
        "conflicting_occurrences": conflicting_occurrences,
        "exceptions": all_exceptions,
        "exception_counts": dict(sorted(exception_counts.items())),
        "variants": variant_rows,
        "sku_reuse": [
            {
                "supplier_sku": sku,
                "vendors": sorted(sku_vendors[sku]),
                "variant_ids": sorted(sku_variants[sku]),
            }
            for sku in sorted(sku_vendors)
            if len(sku_vendors[sku]) > 1 or len(sku_variants[sku]) > 1
        ],
        "dependency_counts": dict(sorted(dependencies.items())),
        "dependency_groups": [dict(group) for group in dependency_groups],
        "representation_gaps": [dict(gap) for gap in REPRESENTATION_GAP_CATALOG],
        "representation_gap_evidence": gap_evidence,
        "supplier_name_vocabulary": _supplier_name_vocabulary(occurrences),
        "alias_transition_candidates": _snapshot_alias_candidates(occurrences),
        "integrity": dict(integrity or {}),
        "invariants": {
            "missing_source_is_retirement": False,
            "names_or_skus_are_identity": False,
            "mapping_approvals": 0,
            "price_approvals": 0,
            "import_ready_rows": 0,
        },
    }
    if include_flat_occurrences:
        result["occurrences"] = occurrences
    else:
        result["occurrence_storage"] = "OFFER_FAMILIES_ONLY_NO_DUPLICATED_RAW_RECORDS"
    return result


def _index_occurrences(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = occurrence_identity(row)
        if key in result:
            raise SupplierReviewError(f"duplicate occurrence in monthly snapshot: {key!r}")
        result[key] = row
    return result


def _tier_map(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[tuple[str, str, str], str], Mapping[str, Any]]:
    result = {}
    for row in rows:
        key = tier_identity(row)
        if key in result:
            raise SupplierReviewError(f"duplicate source tier identity: {key!r}")
        result[key] = row
    return result


_COMPARISON_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("supplier_code", ("supplier_code", "supplier_sku", "sku")),
    ("break_quantity", ("break_quantity", "threshold_quantity", "break_qty")),
    ("break_unit", ("break_unit", "supplier_qualifying_unit", "qualifying_unit")),
    ("case_price", ("case_price", "published_case_price")),
    ("unit_price", ("unit_price", "published_unit_price")),
    ("program_type", ("offer_type", "package_type", "program_type")),
    (
        "physical_units_per_supplier_case",
        (
            "physical_units_per_supplier_case",
            "physical_containers_per_case",
            "physical_bottles_per_case",
            "physical_cans_per_case",
        ),
    ),
    ("retail_units_per_supplier_case", ("supplier_case_pack", "retail_units_per_case")),
    (
        "shopify_units_per_supplier_case",
        (
            "reviewed_shopify_units_per_case",
            "proposed_shopify_sellable_units_per_case",
            "shopify_units_per_case",
        ),
    ),
    ("inner_pack_units", ("supplier_retail_pack", "containers_per_retail_unit", "inner_pack_units")),
    ("supplier_order_increment", ("supplier_order_increment", "order_increment")),
    (
        "supplier_qualifying_units_per_case",
        (
            "reviewed_qualifying_units_per_case",
            "supplier_qualifying_units_per_case",
            "qualifying_units_per_case",
        ),
    ),
    ("abv_percent", ("abv_percent", "alcohol_by_volume")),
    ("proof", ("proof",)),
    ("expression", ("expression",)),
    ("vintage", ("supplier_vintage", "vintage")),
    ("vintage_raw", ("supplier_vintage_raw",)),
    ("effective_from", ("effective_from", "valid_from", "start_date")),
    ("effective_through", ("effective_through", "effective_to", "valid_to", "end_date")),
    ("territory", ("territory", "territory_applicability")),
    ("channel", ("channel", "source_channel")),
    ("source_period", ("source_period", "source_period_raw", "period_id")),
    ("source_file", ("source_file",)),
    ("source_page", ("source_page",)),
    ("printed_page", ("printed_page", "physical_page")),
    ("source_sha256", ("source_sha256",)),
    ("source_size_raw", ("source_size_raw", "supplier_size_raw")),
    ("source_case_pack_raw", ("source_case_pack_raw",)),
    ("source_physical_count_raw", ("source_physical_count_raw",)),
    ("source_retail_pack_raw", ("source_retail_pack_raw",)),
    ("source_split_fee_basis", ("source_split_fee_basis", "split_fee_basis")),
    ("source_split_availability", ("source_split_availability",)),
    ("catalog_status", ("authoritative_catalog_status", "catalog_status", "catalog_source_status")),
    ("policy_excluded", ("policy_excluded",)),
    ("mapping_approved", ("mapping_approved",)),
    ("price_approved", ("price_approved",)),
    ("import_ready", ("import_ready",)),
    (
        "candidate_disposition",
        ("candidate_disposition", "disposition", "source_status", "status"),
    ),
    ("mapping_status", ("mapping_status",)),
)


def _comparison_values(row: Mapping[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    for canonical, aliases in _COMPARISON_FIELDS:
        reviewed_field = {
            "shopify_units_per_supplier_case": "reviewed_shopify_units_per_case",
            "supplier_qualifying_units_per_case": "reviewed_qualifying_units_per_case",
        }.get(canonical)
        if reviewed_field is not None and reviewed_field in row:
            value, found = row[reviewed_field], []
        else:
            value, found = _aliased_value(row, canonical, aliases)
        values[canonical] = None if value is _MISSING else value
        conflicts.extend(found)
    if conflicts:
        fields = sorted({str(item["canonical_field"]) for item in conflicts})
        raise SupplierReviewError(f"conflicting monthly aliases for fields: {fields!r}")
    return values


def _comparison_states(row: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for canonical, aliases in _COMPARISON_FIELDS:
        reviewed_field = {
            "shopify_units_per_supplier_case": "reviewed_shopify_units_per_case",
            "supplier_qualifying_units_per_case": "reviewed_qualifying_units_per_case",
        }.get(canonical)
        if reviewed_field is not None and reviewed_field in row:
            value, found, present = row[reviewed_field], [], True
        else:
            value, found = _aliased_value(row, canonical, aliases)
            present = value is not _MISSING
        states[canonical] = {"present": present, "value": None if not present else value}
        conflicts.extend(found)
    if conflicts:
        fields = sorted({str(item["canonical_field"]) for item in conflicts})
        raise SupplierReviewError(f"conflicting monthly aliases for fields: {fields!r}")
    return states


def _comparison_alias_candidates(
    common: Iterable[tuple[str, str, str]],
    previous: Mapping[tuple[str, str, str], Mapping[str, Any]],
    current: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for identity in sorted(common):
        before = _comparison_values(previous[identity])["supplier_code"]
        after = _comparison_values(current[identity])["supplier_code"]
        if before not in {None, ""} and after not in {None, ""} and before != after:
            result.append(
                {
                    "authority": "CANDIDATE_ONLY",
                    "variant_id": identity[0],
                    "vendor": identity[1],
                    "source_occurrence_id": identity[2],
                    "before_supplier_code": str(before),
                    "after_supplier_code": str(after),
                    "basis": "SAME_SOURCE_OCCURRENCE_MONTHLY_TRANSITION",
                    "retirement_created": False,
                    "approval_created": False,
                }
            )
    return result


def _comparison_vocabulary(
    rows: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    projected = [
        {
            "variant_id": identity[0],
            "vendor": identity[1],
            "occurrence_id": identity[2],
            "raw": dict(row),
        }
        for identity, row in sorted(rows.items())
    ]
    return _supplier_name_vocabulary(projected)


def _simultaneous_packages(
    rows: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[tuple[tuple[str, str, str], Mapping[str, Any]]]] = defaultdict(list)
    for identity, row in rows.items():
        grouped[identity[0]].append((identity, row))
    result = []
    for variant_id, values in sorted(grouped.items()):
        if len(values) <= 1:
            continue
        result.append(
            {
                "variant_id": variant_id,
                "source_occurrence_ids": sorted(identity[2] for identity, _ in values),
                "program_types": sorted(
                    {
                        str(_comparison_values(row)["program_type"] or "UNSPECIFIED")
                        for _, row in values
                    }
                ),
                "selection_created": False,
            }
        )
    return result


def compare_review_snapshots(
    previous_rows: Sequence[Mapping[str, Any]],
    current_rows: Sequence[Mapping[str, Any]],
    *,
    previous_tiers: Sequence[Mapping[str, Any]] = (),
    current_tiers: Sequence[Mapping[str, Any]] = (),
    simulated: bool = False,
) -> dict[str, Any]:
    if not isinstance(simulated, bool):
        raise SupplierReviewError("simulated must be an explicit boolean")
    previous = _index_occurrences(previous_rows)
    current = _index_occurrences(current_rows)
    previous_keys, current_keys = set(previous), set(current)
    common = previous_keys & current_keys
    changed = []
    unchanged = []
    categories: set[str] = set()
    for key in sorted(common):
        before_values = _comparison_values(previous[key])
        after_values = _comparison_values(current[key])
        before_states = _comparison_states(previous[key])
        after_states = _comparison_states(current[key])
        differences = {
            field: {
                "before_present": before_states[field]["present"],
                "before": before_states[field]["value"],
                "after_present": after_states[field]["present"],
                "after": after_states[field]["value"],
            }
            for field in before_states
            if (
                before_states[field]["present"] != after_states[field]["present"]
                or (
                    before_states[field]["present"]
                    and _canonical_json(before_states[field]["value"])
                    != _canonical_json(after_states[field]["value"])
                )
            )
        }
        item = {"variant_id": key[0], "vendor": key[1], "occurrence_id": key[2]}
        if differences:
            item["changes"] = differences
            item["before"] = dict(previous[key])
            item["after"] = dict(current[key])
            if any(field in differences for field in ("break_quantity", "break_unit")):
                categories.add("BT_CS_THRESHOLD_CHANGED")
            if any(field in differences for field in ("unit_price", "case_price")):
                categories.add("PRICE_CHANGED")
            if "supplier_code" in differences:
                categories.add("SUPPLIER_SKU_CHANGED")
            if any(
                field in differences
                for field in (
                    "program_type",
                    "physical_units_per_supplier_case",
                    "retail_units_per_supplier_case",
                    "shopify_units_per_supplier_case",
                    "inner_pack_units",
                    "supplier_order_increment",
                    "supplier_qualifying_units_per_case",
                    "source_size_raw",
                    "source_case_pack_raw",
                    "source_physical_count_raw",
                    "source_retail_pack_raw",
                )
            ):
                categories.add("PACK_CHANGED")
            if any(field in differences for field in ("abv_percent", "proof", "expression")):
                categories.add("EXPRESSION_OR_PROOF_CHANGED")
            if "vintage" in differences or "vintage_raw" in differences:
                categories.add("VINTAGE_CHANGED")
            if any(field in differences for field in ("effective_from", "effective_through")):
                categories.add("EFFECTIVE_DATE_CHANGED")
            if any(field in differences for field in ("channel", "territory")):
                categories.add("CHANNEL_OR_TERRITORY_SCOPE_CHANGED")
            if "source_period" in differences:
                categories.add("SOURCE_PERIOD_CHANGED")
            if any(field in differences for field in ("effective_from", "effective_through", "territory")):
                categories.add("DATE_OR_TERRITORY_CHANGED")
            if "source_sha256" in differences:
                categories.add("SOURCE_DOCUMENT_CHANGED")
            elif "source_page" in differences:
                categories.add("SOURCE_REPAGINATED")
            elif "source_file" in differences:
                categories.add("SOURCE_FILE_REFERENCE_CHANGED")
            if any(
                field in differences
                for field in ("source_split_fee_basis", "source_split_availability")
            ):
                categories.add("PROGRAM_TERMS_CHANGED")
            if "candidate_disposition" in differences:
                before_disposition = str(before_values["candidate_disposition"] or "")
                after_disposition = str(after_values["candidate_disposition"] or "")
                if "REJECTED" in before_disposition and "REJECTED" in after_disposition:
                    categories.add("REJECTED_MATCH_RECURRED")
            changed.append(item)
        else:
            unchanged.append(item)
    old_tiers = _tier_map(previous_tiers)
    new_tiers = _tier_map(current_tiers)
    tier_changes = []
    for key in sorted(set(old_tiers) | set(new_tiers)):
        before, after = old_tiers.get(key), new_tiers.get(key)
        if before is None:
            kind = "ADDED_TIER"
            categories.add("ADDED_TIER")
        elif after is None:
            kind = "MISSING_TIER_NOT_RETIREMENT"
            categories.add("MISSING_TIER_NOT_RETIREMENT")
        elif _commercial_fingerprint(before) == _commercial_fingerprint(after):
            continue
        elif (
            _comparison_values(before)["unit_price"]
            == _comparison_values(after)["unit_price"]
            and _comparison_values(before)["break_quantity"]
            != _comparison_values(after)["break_quantity"]
        ):
            kind = "THRESHOLD_CHANGED_PRICE_UNCHANGED"
        else:
            kind = "TIER_CHANGED"
        tier_changes.append(
            {
                "identity": [*key[0], key[1]],
                "kind": kind,
                "before_break_quantity": None
                if before is None
                else _comparison_values(before)["break_quantity"],
                "after_break_quantity": None
                if after is None
                else _comparison_values(after)["break_quantity"],
                "before_unit_price": None
                if before is None
                else _comparison_values(before)["unit_price"],
                "after_unit_price": None
                if after is None
                else _comparison_values(after)["unit_price"],
            }
        )
    prior_sku = defaultdict(set)
    current_sku = defaultdict(set)
    for key, row in previous.items():
        sku = _comparison_values(row)["supplier_code"]
        if sku not in {None, ""}:
            prior_sku[(key[1], str(sku))].add(key)
    for key, row in current.items():
        sku = _comparison_values(row)["supplier_code"]
        if sku not in {None, ""}:
            current_sku[(key[1], str(sku))].add(key)
    reuse = []
    for key in sorted(set(prior_sku) & set(current_sku)):
        if prior_sku[key] != current_sku[key]:
            reuse.append({"vendor": key[0], "supplier_sku": key[1], "before": [list(x) for x in sorted(prior_sku[key])], "after": [list(x) for x in sorted(current_sku[key])]})
    for rows_by_month in (previous, current):
        programs: defaultdict[str, set[str]] = defaultdict(set)
        for key, row in rows_by_month.items():
            programs[key[0]].add(str(_comparison_values(row)["program_type"] or ""))
        if any(len(values) > 1 and any("GIFT" in value for value in values) for values in programs.values()):
            categories.add("SIMULTANEOUS_GIFT_ALTERNATIVES")
    if any(
        "REJECTED" in str(_comparison_values(previous[key])["candidate_disposition"] or "").upper()
        and "REJECTED" in str(_comparison_values(current[key])["candidate_disposition"] or "").upper()
        for key in common
    ):
        categories.add("REJECTED_MATCH_RECURRED")
    simultaneous = _simultaneous_packages(current)
    if simultaneous:
        categories.add("SIMULTANEOUS_PACKAGES")
    added_codes = [
        {
            "variant_id": key[0],
            "vendor": key[1],
            "source_occurrence_id": key[2],
            "supplier_code": _comparison_values(current[key])["supplier_code"],
        }
        for key in sorted(current_keys - previous_keys)
    ]
    missing_codes = [
        {
            "variant_id": key[0],
            "vendor": key[1],
            "source_occurrence_id": key[2],
            "supplier_code": _comparison_values(previous[key])["supplier_code"],
            "retirement_inferred": False,
        }
        for key in sorted(previous_keys - current_keys)
    ]
    if any(
        "GIFT" in str(_comparison_values(previous[key])["program_type"] or "").upper()
        for key in previous_keys - current_keys
    ):
        categories.add("GIFT_DISAPPEARED_NOT_RETIREMENT")
    return {
        "label": REVIEW_LABEL,
        "status": "PASS",
        "authority": "REVIEW_ONLY",
        "simulated": simulated,
        "summary": {
            "previous_occurrences": len(previous),
            "current_occurrences": len(current),
            "unchanged": len(unchanged),
            "changed": len(changed),
            "added": len(current_keys - previous_keys),
            "missing_not_retired": len(previous_keys - current_keys),
        },
        "unchanged": unchanged,
        "changed": changed,
        "added": [list(key) for key in sorted(current_keys - previous_keys)],
        "missing_not_retired": [list(key) for key in sorted(previous_keys - current_keys)],
        "added_occurrences": [
            {"identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]}, "current": dict(current[key])}
            for key in sorted(current_keys - previous_keys)
        ],
        "missing_occurrences": [
            {"identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]}, "previous": dict(previous[key]), "retirement_inferred": False}
            for key in sorted(previous_keys - current_keys)
        ],
        "changed_occurrences": changed,
        "new_supplier_codes": added_codes,
        "missing_supplier_codes_not_retired": missing_codes,
        "simultaneous_packages": simultaneous,
        "categories": sorted(categories),
        "sku_reuse_or_replacement": reuse,
        "alias_transition_candidates": _comparison_alias_candidates(common, previous, current),
        "supplier_name_vocabulary": _comparison_vocabulary(current),
        "tier_changes": tier_changes,
        "candidate_vocabulary_only": True,
        "operational_effects": {
            "mapping_approvals": 0,
            "retirements": 0,
            "price_approvals": 0,
        },
    }


_REVIEWABLE_SIDECAR_FIELDS: Mapping[str, tuple[str, ...]] = {
    "conditional_gift_relationships_v5": (
        "relationship_id",
        "diagnostic_relationship_type",
        "exact_gift_contents_and_acceptance",
        "source_description_raw",
        "source_case_pack_raw",
        "shopify_units_per_case",
        "qualifying_units_per_case",
        "source_fee_basis",
        "source_split_permission",
        "allocated_component_cost",
        "whole_offer_to_single_variant_allowed",
        "candidate_identity_disposition",
        "preference",
        "mapping_approved",
        "price_approved",
        "import_ready",
    ),
    "fixed_combo_component_relationships_v5": (
        "component_relationship_id",
        "relationship_type",
        "component_description_raw",
        "component_quantity_raw",
        "component_quantity_unit_raw",
        "component_size_raw",
        "component_supplier_sku",
        "whole_combo_total_cost",
        "allocated_component_cost",
        "cost_allocation_status",
        "whole_combo_to_variant_mapping_allowed",
        "combo_auto_add",
        "mapping_approved",
        "price_approved",
        "import_ready",
    ),
    "remaining_combo_component_relationships_v5": (
        "research_relationship_id",
        "component_description_raw",
        "component_quantity_raw",
        "component_quantity_unit_raw",
        "component_size_raw",
        "component_supplier_sku_raw",
        "component_cost_allocation",
        "shopify_sellable_units_per_combo_component",
        "shopify_sellable_units_per_entire_combo",
        "whole_combo_maps_to_single_variant",
        "component_relationship_disposition",
        "mapping_approved",
        "price_approved",
        "import_ready",
    ),
    "alcohol_gift_components_v5": (
        "root_relationship_review_id",
        "relationship_classification",
        "components",
        "primary_bottles_per_case_candidate",
        "additional_50ml_bottles_per_case_candidate",
        "total_physical_alcohol_containers_per_case_candidate",
        "component_shopify_units_per_case",
        "component_qualifying_units_per_case",
        "allocated_component_cost",
        "whole_gift_to_single_variant_mapping_allowed",
        "whole_gift_mapping_disposition",
        "mapping_approved",
        "price_approved",
        "import_ready",
    ),
}


def _reviewable_sidecar_record(
    table_name: str, row: Mapping[str, Any]
) -> dict[str, Any] | None:
    if table_name in {
        "alcohol_gift_components_v5",
        "conditional_gift_relationships_v5",
    }:
        # These bounded gift tables carry unresolved component identities,
        # published ambiguities, and evidence requests that reviewers must see
        # together; filtering individual fields can change their meaning.
        return dict(row)
    fields = _REVIEWABLE_SIDECAR_FIELDS.get(table_name)
    if fields is None:
        return None
    return {field: row[field] for field in fields if field in row}


def _v5_occurrence_projection(package: ReviewPackage) -> list[dict[str, Any]]:
    dependencies: defaultdict[str, list[str]] = defaultdict(list)
    for dependency in package.table("unresolved_dependencies_v5"):
        variant = dependency.get("variant_id")
        identifier = dependency.get("dependency_id")
        if isinstance(variant, str) and isinstance(identifier, str):
            dependencies[variant].append(identifier)
    result: list[dict[str, Any]] = []
    for raw in package.table("variant_offer_relationships_v5"):
        # Keep the report projection bounded. The immutable 44-field source
        # relationship remains available in the verified PackageTable and is
        # bound here by its exact canonical record hash.
        row = {
            "variant_id": raw.get("variant_id"),
            "vendor": raw.get("supplier_name_raw"),
            "source_occurrence_id": raw.get("source_offer_id"),
            "supplier_code": raw.get("supplier_code_exact"),
            "supplier_description": raw.get("source_description_raw"),
            "program_type": raw.get("source_package_type_raw"),
            # Reviewed fields are authoritative only as review evidence.
            # Their explicit nulls must not fall back to raw/legacy pack facts.
            "reviewed_shopify_units_per_case": raw.get("reviewed_shopify_units_per_case"),
            "reviewed_qualifying_units_per_case": raw.get("reviewed_qualifying_units_per_case"),
            "shopify_units_per_case": raw.get("reviewed_shopify_units_per_case"),
            "qualifying_units_per_case": raw.get("reviewed_qualifying_units_per_case"),
            "authoritative_catalog_status": raw.get("catalog_source_status"),
            "candidate_disposition": raw.get("candidate_disposition"),
            "source_file": raw.get("source_file"),
            "source_page": raw.get("source_page"),
            "printed_page": raw.get("printed_page"),
            "source_sha256": raw.get("source_sha256"),
            "source_period": raw.get("source_period_raw"),
            "territory": raw.get("source_territory"),
            "source_vintage": raw.get("source_vintage_raw"),
            "supplier_vintage_raw": raw.get("source_vintage_raw"),
            "source_case_pack_raw": raw.get("source_case_pack_raw"),
            "source_physical_count_raw": raw.get("source_physical_count_raw"),
            "source_retail_pack_raw": raw.get("source_retail_pack_raw"),
            "source_size_raw": raw.get("source_size_raw"),
            "source_split_fee_basis": raw.get("source_split_fee_basis"),
            "source_split_availability": raw.get("source_split_availability"),
            "preference": raw.get("preference"),
            "new_gift_sidecar_id": raw.get("new_gift_sidecar_id"),
            "source_tier_ids": raw.get("source_tier_ids"),
            "mapping_approved": False,
            "price_approved": False,
            "import_ready": False,
            "approval_status": "UNAPPROVED_CANDIDATE",
            "dependencies": sorted(dependencies.get(str(raw.get("variant_id")), ())),
            "relationship_record_sha256": hashlib.sha256(
                _canonical_json(dict(raw))
            ).hexdigest(),
        }
        if raw.get("reviewed_shopify_units_per_case") is None:
            row["reviewed_null"] = True
        result.append(row)
    return result


def _package_occurrences(package: ReviewPackage) -> Sequence[Mapping[str, Any]]:
    if package.table("variant_offer_relationships_v5"):
        return _v5_occurrence_projection(package)
    for name in ("offers", "affected_candidates", "normalized_price_contract_review_delta"):
        rows = package.table(name)
        if rows:
            return rows
    return ()


def build_review_batches(
    package: ReviewPackage,
    *,
    local_pdf_href_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic review-preview batches with no approval capability."""

    relationship_table = package.tables.get("variant_offer_relationships_v5")
    if relationship_table is None:
        return []
    sidecars: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for table_name, table in sorted(package.tables.items()):
        if table_name == "variant_offer_relationships_v5" or table_name.startswith("v5_patch_"):
            continue
        for row in table.rows:
            variant = row.get("variant_id", row.get("catalog_variant_id"))
            if not isinstance(variant, str):
                continue
            offers: list[str] = []
            direct = row.get("source_offer_id", row.get("source_combo_offer_id"))
            if isinstance(direct, str):
                offers.append(direct)
            nested_offer = row.get("source_offer")
            if isinstance(nested_offer, Mapping):
                nested_id = nested_offer.get("source_offer_id")
                if isinstance(nested_id, str):
                    offers.append(nested_id)
            for field in ("source_offer_ids", "source_anchor_offer_ids"):
                values = row.get(field)
                if isinstance(values, list):
                    offers.extend(value for value in values if isinstance(value, str))
            record_hash = hashlib.sha256(_canonical_json(dict(row))).hexdigest()
            for offer in sorted(set(offers)):
                sidecar: dict[str, Any] = {
                    "table": table_name,
                    "record_sha256": record_hash,
                }
                review_evidence = _reviewable_sidecar_record(table_name, row)
                if review_evidence is not None:
                    sidecar["review_evidence"] = review_evidence
                sidecars[(variant, offer)].append(sidecar)

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    global_source_missing = any(
        value.startswith(("SOURCE_PAGE_ARCHIVE:", "ORIGINAL_SUPPLIER_PDFS"))
        for value in package.unavailable_evidence
    )
    pdf_status_value = package.metadata.get("external_pdf_status", {})
    pdf_status = pdf_status_value if isinstance(pdf_status_value, Mapping) else {}
    for row in relationship_table.rows:
        variant = str(row["variant_id"])
        offer = str(row["source_offer_id"])
        candidate = str(row.get("candidate_disposition") or "UNRESOLVED")
        identity_blockers = []
        if candidate != "PROPOSED_REVIEW_CANDIDATE":
            identity_blockers.append(candidate)
        if str(row.get("catalog_source_status") or "").upper() in BLOCKED_DISPOSITIONS:
            identity_blockers.append("CATALOG_STATUS_BLOCKS_USE")
        packaging_blockers = []
        if row.get("reviewed_shopify_units_per_case") is None:
            packaging_blockers.append("SHOPIFY_UNITS_EXPLICITLY_UNRESOLVED")
        if row.get("new_gift_sidecar_id") is not None:
            packaging_blockers.append("CONDITIONAL_GIFT_REQUIRES_SEPARATE_REVIEW")
        program_price_blockers = ["NO_CURRENT_PRICE_OR_TIER_AUTHORITY"]
        if row.get("source_split_fee_basis") not in {None, ""}:
            program_price_blockers.append("SPLIT_FEE_SCOPE_REQUIRES_REVIEW")
        if package.package_kind == _A1_PACKAGE_KIND:
            occurrence_source = pdf_status.get(row.get("source_file"), {})
            source_blockers = (
                []
                if isinstance(occurrence_source, Mapping)
                and occurrence_source.get("availability") == "VERIFIED_ORIGINAL_BYTES"
                else ["SOURCE_BYTES_UNAVAILABLE"]
            )
        else:
            source_blockers = ["SOURCE_BYTES_UNAVAILABLE"] if global_source_missing else []
        related = sorted(
            sidecars.get((variant, offer), ()),
            key=lambda item: (item["table"], item["record_sha256"]),
        )
        preview = {
            "label": REVIEW_PREVIEW_LABEL,
            "variant_id": variant,
            "vendor": row.get("supplier_name_raw"),
            "source_occurrence_id": offer,
            "supplier_code": row.get("supplier_code_exact"),
            "supplier_description": row.get("source_description_raw"),
            "program_type": row.get("source_package_type_raw"),
            "candidate_disposition": candidate,
            "preference": row.get("preference"),
            "owner_preference_decision_ids": row.get("owner_preference_decision_ids"),
            "reviewed_shopify_units_per_case": row.get("reviewed_shopify_units_per_case"),
            "reviewed_qualifying_units_per_case": row.get("reviewed_qualifying_units_per_case"),
            "raw_packaging": {
                "source_case_pack_raw": row.get("source_case_pack_raw"),
                "source_physical_count_raw": row.get("source_physical_count_raw"),
                "source_retail_pack_raw": row.get("source_retail_pack_raw"),
                "source_size_raw": row.get("source_size_raw"),
            },
            "source": {
                "file": row.get("source_file"),
                "page": row.get("source_page"),
                "sha256": row.get("source_sha256"),
                "period": row.get("source_period_raw"),
                "territory": row.get("source_territory"),
            },
            "blockers": {
                "identity": sorted(set(identity_blockers)),
                "packaging": sorted(set(packaging_blockers)),
                "program_price": sorted(set(program_price_blockers)),
                "source_availability": sorted(set(source_blockers)),
            },
            "relationship_record_sha256": hashlib.sha256(
                _canonical_json(dict(row))
            ).hexdigest(),
            "related_sidecar_records": related,
            "authority": {
                "mapping_approved": False,
                "price_approved": False,
                "import_ready": False,
                "selection_created": False,
            },
        }
        fingerprint_basis = {
            "package_manifest_sha256": package.manifest_sha256,
            "relationship_table_sha256": relationship_table.canonical_jsonl_sha256,
            "preview": preview,
        }
        preview["offer_preview_fingerprint"] = hashlib.sha256(
            _canonical_json(fingerprint_basis)
        ).hexdigest()
        grouped[variant].append(preview)

    batches = []
    for variant, offers in sorted(grouped.items()):
        offers.sort(key=lambda item: (str(item["vendor"]), str(item["source_occurrence_id"])))
        batch = {
            "label": REVIEW_PREVIEW_LABEL,
            "package_id": package.snapshot_id,
            "package_manifest_sha256": package.manifest_sha256,
            "relationship_table_sha256": relationship_table.canonical_jsonl_sha256,
            "variant_id": variant,
            "offers": offers,
            "authority": {
                "mapping_approvals": 0,
                "price_approvals": 0,
                "import_ready_rows": 0,
            },
        }
        batch["batch_fingerprint"] = hashlib.sha256(_canonical_json(batch)).hexdigest()
        batch["variant_review_fingerprint"] = batch["batch_fingerprint"]
        batches.append(batch)
    if package.package_kind == _A1_PACKAGE_KIND:
        return _augment_a1_review_batches(
            package,
            batches,
            local_pdf_href_prefix=local_pdf_href_prefix,
        )
    return batches


def _augment_a1_review_batches(
    package: ReviewPackage,
    existing_batches: Sequence[Mapping[str, Any]],
    *,
    local_pdf_href_prefix: str | None,
) -> list[dict[str, Any]]:
    """Add the complete 2,000-Variant A1 display layer without selecting offers."""

    batches = {str(row.get("variant_id")): dict(row) for row in existing_batches}
    existing_offers: dict[tuple[str, str], dict[str, Any]] = {}
    for variant, batch in batches.items():
        offers = batch.get("offers", ())
        if not isinstance(offers, Sequence) or isinstance(offers, str):
            continue
        copied = [dict(row) for row in offers if isinstance(row, Mapping)]
        batch["offers"] = copied
        for offer in copied:
            existing_offers[(variant, str(offer.get("source_occurrence_id")))] = offer

    metadata = package.metadata
    ladder_value = metadata.get("price_ladders_by_offer", {})
    ladders = ladder_value if isinstance(ladder_value, Mapping) else {}
    source_detail_value = metadata.get("source_offer_details", {})
    source_details = source_detail_value if isinstance(source_detail_value, Mapping) else {}
    rejected_value = metadata.get("rejected_by_variant", {})
    rejected = rejected_value if isinstance(rejected_value, Mapping) else {}
    pdf_value = metadata.get("external_pdf_status", {})
    pdf_status = pdf_value if isinstance(pdf_value, Mapping) else {}

    sibling_by_pair = {
        (str(row.get("variant_id")), str(row.get("source_offer_id"))): row
        for row in package.table("sibling_offer_reviews")
    }
    diagnostic_by_pair = {
        (str(row.get("variant_id")), str(row.get("source_offer_id"))): row
        for row in package.table("same_code_sibling_display_context")
    }
    display_by_variant: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in package.table("approval_preview_offer_rows"):
        display_by_variant[str(row.get("variant_id"))].append(row)

    owner_records = {
        str(row.get("owner_decision_id")): row for row in package.table("owner_decisions")
    }
    owners_by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for scope in package.table("owner_decision_display_scopes"):
        variant_ids = scope.get("explicit_variant_ids")
        if not isinstance(variant_ids, list):
            continue
        identifier = str(scope.get("owner_decision_id"))
        for variant in variant_ids:
            if isinstance(variant, str):
                owners_by_variant[variant].append(
                    {
                        "display_scope": dict(scope),
                        "owner_decision": dict(owner_records[identifier]),
                    }
                )

    requirements_by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in package.table("requests__active_dependency_request_routes"):
        variant = row.get("variant_id")
        if isinstance(variant, str):
            requirements_by_variant[variant].append(dict(row))
    approval_requirements_by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in package.table("requests__owner_approval_requirement_interpretations"):
        variant = row.get("variant_id")
        if isinstance(variant, str):
            approval_requirements_by_variant[variant].append(dict(row))
    memory_by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in package.table("rejected_match_memory_v5"):
        variant = row.get("variant_id")
        if isinstance(variant, str):
            memory_by_variant[variant].append(dict(row))
    combo_by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for table_name in (
        "fixed_combo_component_relationships_v5",
        "remaining_combo_component_relationships_v5",
    ):
        for row in package.table(table_name):
            variant = row.get("variant_id", row.get("catalog_variant_id"))
            if isinstance(variant, str):
                combo_by_variant[variant].append(
                    {"source_table": table_name, "record": dict(row)}
                )

    for partition in package.table("approval_preview_partition"):
        variant = str(partition.get("variant_id"))
        batch = batches.setdefault(
            variant,
            {
                "label": REVIEW_PREVIEW_LABEL,
                "package_id": package.snapshot_id,
                "package_manifest_sha256": package.manifest_sha256,
                "relationship_table_sha256": package.tables[
                    "variant_offer_relationships_v5"
                ].canonical_jsonl_sha256,
                "variant_id": variant,
                "offers": [],
                "authority": {
                    "mapping_approvals": 0,
                    "price_approvals": 0,
                    "import_ready_rows": 0,
                },
            },
        )
        batch["catalog_review"] = dict(partition)
        batch["owner_clarifications"] = owners_by_variant.get(variant, [])
        batch["active_requirements"] = requirements_by_variant.get(variant, [])
        batch["pending_approval_requirements"] = approval_requirements_by_variant.get(
            variant, []
        )
        batch["rejected_matches"] = list(rejected.get(variant, ()))
        batch["rejected_match_memory"] = memory_by_variant.get(variant, [])
        batch["combo_relationships"] = combo_by_variant.get(variant, [])

        display_rows = sorted(
            display_by_variant.get(variant, ()),
            key=lambda row: (str(row.get("supplier")), str(row.get("source_offer_id"))),
        )
        offers: list[dict[str, Any]] = []
        for display in display_rows:
            occurrence = str(display.get("source_offer_id"))
            pair = (variant, occurrence)
            frozen_offer = dict(existing_offers.get(pair, {}))
            offer = dict(frozen_offer)
            source_detail = source_details.get(occurrence, {})
            if not isinstance(source_detail, Mapping):
                source_detail = {}
            sibling = sibling_by_pair.get(pair)
            sibling_source = sibling.get("source_evidence", {}) if sibling else {}
            if not isinstance(sibling_source, Mapping):
                sibling_source = {}
            if not offer:
                offer = {
                    "label": REVIEW_PREVIEW_LABEL,
                    "variant_id": variant,
                    "source_occurrence_id": occurrence,
                    "supplier_description": source_detail.get("supplier_description"),
                    "program_type": source_detail.get("program_type"),
                    "related_sidecar_records": [],
                }
            display_disposition = str(display.get("disposition") or "UNRESOLVED")
            display_shopify = display.get("reviewed_shopify_units_per_case")
            display_qualifying = display.get("reviewed_qualifying_units_per_case")
            frozen_raw_value = frozen_offer.get("raw_packaging", {})
            frozen_raw = (
                frozen_raw_value if isinstance(frozen_raw_value, Mapping) else {}
            )
            if frozen_offer and any(
                (
                    frozen_offer.get("vendor") != display.get("supplier"),
                    frozen_offer.get("candidate_disposition") != display_disposition,
                    frozen_offer.get("reviewed_shopify_units_per_case") != display_shopify,
                    frozen_offer.get("reviewed_qualifying_units_per_case")
                    != display_qualifying,
                    frozen_raw.get("source_retail_pack_raw")
                    != display.get("display_retail_packs"),
                )
            ):
                offer["frozen_v5_relationship_preview"] = {
                    "vendor": frozen_offer.get("vendor"),
                    "candidate_disposition": frozen_offer.get("candidate_disposition"),
                    "reviewed_shopify_units_per_case": frozen_offer.get(
                        "reviewed_shopify_units_per_case"
                    ),
                    "reviewed_qualifying_units_per_case": frozen_offer.get(
                        "reviewed_qualifying_units_per_case"
                    ),
                    "relationship_record_sha256": frozen_offer.get(
                        "relationship_record_sha256"
                    ),
                    "raw_packaging": dict(frozen_raw),
                    "scope": "PRESERVED_PRE_ADDENDUM_REVIEW_EVIDENCE",
                }
            offer.update(
                {
                    "label": REVIEW_PREVIEW_LABEL,
                    "variant_id": variant,
                    "vendor": display.get("supplier", source_detail.get("vendor")),
                    "source_occurrence_id": occurrence,
                    "supplier_code": display.get(
                        "supplier_code_exact", source_detail.get("supplier_code")
                    ),
                    "candidate_disposition": display_disposition,
                    "reviewed_shopify_units_per_case": display_shopify,
                    "reviewed_qualifying_units_per_case": display_qualifying,
                    "shopify_units_per_case": display_shopify,
                    "qualifying_units_per_case": display_qualifying,
                }
            )
            # Preserve the frozen V5 raw package fields byte-semantically.
            # Daytime values are a separate reviewed display projection; an
            # explicit null here must never overwrite or masquerade as raw
            # supplier evidence.
            offer["raw_packaging"] = dict(frozen_raw)
            offer["reviewed_packaging_display"] = {
                "source_physical_count_raw": display.get(
                    "source_physical_count_raw"
                ),
                "display_physical_count": display.get("display_physical_count"),
                "display_retail_packs": display.get("display_retail_packs"),
                "display_containers_per_retail_pack": display.get(
                    "display_containers_per_retail_pack"
                ),
                "explicit_null_precedence": display.get(
                    "explicit_null_precedence"
                ),
            }
            prior_blockers_value = frozen_offer.get("blockers", {})
            prior_blockers = (
                prior_blockers_value
                if isinstance(prior_blockers_value, Mapping)
                else {}
            )
            packaging_blockers = []
            if display_shopify is None:
                packaging_blockers.append("SHOPIFY_UNITS_EXPLICITLY_UNRESOLVED")
            if "CONDITIONAL_GIFT_REQUIRES_SEPARATE_REVIEW" in prior_blockers.get(
                "packaging", ()
            ):
                packaging_blockers.append("CONDITIONAL_GIFT_REQUIRES_SEPARATE_REVIEW")
            program_blockers = ["NO_CURRENT_PRICE_OR_TIER_AUTHORITY"]
            if "SPLIT_FEE_SCOPE_REQUIRES_REVIEW" in prior_blockers.get(
                "program_price", ()
            ):
                program_blockers.append("SPLIT_FEE_SCOPE_REQUIRES_REVIEW")
            identity_blockers = (
                []
                if display_disposition == "PROPOSED_REVIEW_CANDIDATE"
                else [display_disposition]
            )
            if "CATALOG_STATUS_BLOCKS_USE" in prior_blockers.get("identity", ()):
                identity_blockers.append("CATALOG_STATUS_BLOCKS_USE")
            offer["blockers"] = {
                "identity": sorted(set(identity_blockers)),
                "packaging": sorted(set(packaging_blockers)),
                "program_price": sorted(set(program_blockers)),
                "source_availability": [],
            }
            offer["authority"] = {
                "mapping_approved": False,
                "price_approved": False,
                "import_ready": False,
                "selection_created": False,
            }
            source_value = offer.get("source", {})
            source = dict(source_value) if isinstance(source_value, Mapping) else {}
            source.setdefault(
                "file", sibling_source.get("source_file", source_detail.get("source_file"))
            )
            source.setdefault("page", display.get("source_page", source_detail.get("source_page")))
            source.setdefault("sha256", display.get("source_sha256", source_detail.get("source_sha256")))
            source_status = pdf_status.get(source.get("file"), {})
            if isinstance(source_status, Mapping):
                source["availability"] = source_status.get("availability")
                page = source.get("page")
                if (
                    source_status.get("availability") == "VERIFIED_ORIGINAL_BYTES"
                    and local_pdf_href_prefix == "../original_sources/"
                    and type(page) is int
                    and 1 <= page <= source_status.get("physical_pages", 0)
                ):
                    filename = source.get("file")
                    if isinstance(filename, str):
                        source["local_pdf_href"] = (
                            f"{local_pdf_href_prefix}{quote(filename, safe='')}#page={page}"
                        )
            if source.get("availability") != "VERIFIED_ORIGINAL_BYTES":
                offer["blockers"]["source_availability"] = [
                    "SOURCE_BYTES_UNAVAILABLE"
                ]
            offer["source"] = source
            offer["display_evidence"] = dict(display)
            offer["price_ladder"] = list(ladders.get(occurrence, ()))
            offer["sibling_review"] = None if sibling is None else dict(sibling)
            offer["same_code_diagnostic"] = (
                None if pair not in diagnostic_by_pair else dict(diagnostic_by_pair[pair])
            )
            offer.pop("offer_preview_fingerprint", None)
            fingerprint_basis = {
                "package_manifest_sha256": package.manifest_sha256,
                "relationship_table_sha256": package.tables[
                    "variant_offer_relationships_v5"
                ].canonical_jsonl_sha256,
                "final_preview": offer,
            }
            offer["offer_preview_fingerprint"] = hashlib.sha256(
                _canonical_json(fingerprint_basis)
            ).hexdigest()
            offers.append(offer)
        batch["offers"] = offers
        terms = [
            variant,
            str(partition.get("captured_product_title") or ""),
            str(partition.get("captured_variant_options") or ""),
            str(partition.get("supplier_batch") or ""),
            str(partition.get("display_status") or ""),
            str(partition.get("frozen_source_status") or ""),
            str(partition.get("top_level_partition") or ""),
            str(partition.get("partition_label") or ""),
        ]
        for offer in offers:
            terms.extend(
                str(offer.get(field) or "")
                for field in (
                    "vendor",
                    "supplier_code",
                    "supplier_description",
                    "source_occurrence_id",
                )
            )
        batch["search_text"] = " ".join(terms).casefold()
        batch.pop("batch_fingerprint", None)
        batch.pop("variant_review_fingerprint", None)
        batch["batch_fingerprint"] = hashlib.sha256(_canonical_json(batch)).hexdigest()
        batch["variant_review_fingerprint"] = batch["batch_fingerprint"]

    result = sorted(batches.values(), key=lambda row: str(row.get("variant_id")))
    disposition_counts = Counter(
        str(offer.get("candidate_disposition"))
        for batch in result
        for offer in batch.get("offers", ())
        if isinstance(offer, Mapping)
    )
    if (
        len(result) != 2_000
        or sum(len(row.get("offers", ())) for row in result) != 14_823
        or disposition_counts
        != Counter(
            {
                "PROPOSED_REVIEW_CANDIDATE": 1_609,
                "REJECTED_ATTRIBUTE_CONFLICT": 7_140,
                "REJECTED_IDENTITY_CONFLICT": 2,
                "SAME_CODE_DIAGNOSTIC_NOT_REVIEWED_AS_IDENTITY": 6,
                "SEARCH_LEAD_ONLY": 6_066,
            }
        )
    ):
        raise SupplierReviewError("A1 full-catalog review batches do not reconcile")
    return result


def _v5_review_family_summary(
    package: ReviewPackage,
    review_batches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize V5 once; occurrence detail lives only in review_batches."""

    dispositions: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    multi_offer = []
    occurrence_count = 0
    blocked_count = 0
    for batch in review_batches:
        offers_value = batch.get("offers", ())
        offers = (
            offers_value
            if isinstance(offers_value, Sequence) and not isinstance(offers_value, str)
            else ()
        )
        occurrence_count += len(offers)
        if len(offers) > 1:
            multi_offer.append(
                {"variant_id": batch.get("variant_id"), "count": len(offers)}
            )
        for offer in offers:
            if not isinstance(offer, Mapping):
                continue
            dispositions[str(offer.get("candidate_disposition") or "UNRESOLVED")] += 1
            blockers = offer.get("blockers", {})
            if not isinstance(blockers, Mapping):
                continue
            is_blocked = False
            for values in blockers.values():
                if isinstance(values, Sequence) and not isinstance(values, str):
                    blocker_counts.update(str(value) for value in values)
                    if values:
                        is_blocked = True
            if is_blocked:
                blocked_count += 1
    integrity = package.metadata.get("integrity_checks", {})
    return {
        "label": package.label,
        "status": package.status,
        "authority": "REVIEW_ONLY",
        "cohorts": dict(package.cohorts),
        "summary": {
            "source_rows": occurrence_count,
            "distinct_occurrences": occurrence_count,
            "offer_families": len(review_batches),
            "multi_offer_families": len(multi_offer),
            "blocked_alternatives": blocked_count,
            "dispositions": dict(sorted(dispositions.items())),
        },
        "occurrence_storage": "REVIEW_BATCHES_ONLY_NO_DUPLICATED_RAW_RECORDS",
        "simultaneous_alternatives": multi_offer,
        "exception_counts": dict(sorted(blocker_counts.items())),
        "representation_gaps": [dict(gap) for gap in REPRESENTATION_GAP_CATALOG],
        "supplier_name_vocabulary": [
            dict(row) for row in package.table("supplier_vocabulary_candidates_v5")
        ],
        "integrity": dict(integrity) if isinstance(integrity, Mapping) else {},
        "invariants": {
            "missing_source_is_retirement": False,
            "names_or_skus_are_identity": False,
            "mapping_approvals": 0,
            "price_approvals": 0,
            "import_ready_rows": 0,
        },
    }


def compare_review_packages(previous: ReviewPackage, current: ReviewPackage) -> dict[str, Any]:
    missing = sorted(set(previous.unavailable_evidence) | set(current.unavailable_evidence))
    reasons: list[str] = []
    if not previous.is_structurally_complete:
        reasons.append("PREVIOUS_STRUCTURAL_REPLAY_REQUIRED")
    if not current.is_structurally_complete:
        reasons.append("CURRENT_STRUCTURAL_REPLAY_REQUIRED")
    previous_scope = previous.metadata.get("snapshot_scope")
    current_scope = current.metadata.get("snapshot_scope")
    if not isinstance(previous_scope, Mapping) or not isinstance(current_scope, Mapping):
        reasons.append("SNAPSHOT_SCOPE_REQUIRED")
    else:
        if previous_scope.get("supplier_period_coverage_complete") is not True:
            reasons.append("PREVIOUS_SUPPLIER_PERIOD_COVERAGE_INCOMPLETE")
        if current_scope.get("supplier_period_coverage_complete") is not True:
            reasons.append("CURRENT_SUPPLIER_PERIOD_COVERAGE_INCOMPLETE")
        compatibility = (
            ("comparison_contract", "COMPARISON_CONTRACT_MISMATCH"),
            ("identity_contract", "IDENTITY_CONTRACT_MISMATCH"),
            ("lineage_family_sha256", "LINEAGE_FAMILY_MISMATCH"),
            ("supplier_scope_kind", "SUPPLIER_SCOPE_MISMATCH"),
            ("supplier_scope", "SUPPLIER_SCOPE_MISMATCH"),
            ("cohort_scope", "COHORT_SCOPE_MISMATCH"),
            ("period_semantics", "PERIOD_SEMANTICS_MISMATCH"),
            ("channels", "CHANNEL_SCOPE_MISMATCH"),
            ("territories", "TERRITORY_SCOPE_MISMATCH"),
            ("simulated", "SIMULATION_SCOPE_MISMATCH"),
        )
        for field, reason in compatibility:
            if (
                field not in previous_scope
                or field not in current_scope
                or type(previous_scope.get(field)) is not type(current_scope.get(field))
                or _canonical_json(previous_scope.get(field))
                != _canonical_json(current_scope.get(field))
            ):
                reasons.append(reason)
    if reasons:
        return {
            "label": REVIEW_LABEL,
            "status": "NOT_COMPARABLE",
            "authority": "REVIEW_ONLY",
            "reason_codes": sorted(set(reasons)),
            "missing_prerequisites": missing,
            "unavailable_evidence": missing,
            "change_claims": {
                "unchanged": [],
                "changed": [],
                "added": [],
                "missing_not_retired": [],
                "tier_changes": [],
                "alias_transition_candidates": [],
            },
            "missing_is_retirement": False,
        }
    result = compare_review_snapshots(
        _package_occurrences(previous),
        _package_occurrences(current),
        previous_tiers=previous.table("tiers"),
        current_tiers=current.table("tiers"),
        simulated=(
            previous_scope.get("simulated") is True
            and current_scope.get("simulated") is True
        ),
    )
    result["previous_period_id"] = previous_scope.get("period_id")
    result["current_period_id"] = current_scope.get("period_id")
    return result


def _required_a1_ledger_rows(
    package: ReviewPackage,
    table_name: str,
) -> list[dict[str, Any]]:
    """Return an exact materialized A1 ledger table or fail closed."""

    expected_count = _A1_LEDGER_TABLE_COUNTS.get(table_name)
    if expected_count is None:
        raise SupplierReviewError(
            f"unknown required A1 ledger table {table_name!r}"
        )
    table = package.tables.get(table_name)
    if table is None:
        raise SupplierReviewError(f"required A1 ledger table is missing: {table_name}")
    if table_name in package.table_loaders:
        raise SupplierReviewError(
            f"required A1 ledger table is not materialized: {table_name}"
        )
    if (
        table.name != table_name
        or table.declared_row_count != expected_count
        or len(table.rows) != expected_count
    ):
        raise SupplierReviewError(
            f"required A1 ledger table count or identity differs: {table_name}"
        )
    digest = hashlib.sha256()
    for row in table.rows:
        digest.update(_canonical_json(dict(row)))
        digest.update(b"\n")
    if (
        not isinstance(table.canonical_jsonl_sha256, str)
        or digest.hexdigest() != table.canonical_jsonl_sha256
    ):
        raise SupplierReviewError(
            f"required A1 ledger table digest differs: {table_name}"
        )
    return [dict(row) for row in table.rows]


def report_document(
    package: ReviewPackage,
    *,
    comparison: Mapping[str, Any] | None = None,
    local_pdf_href_prefix: str | None = None,
) -> dict[str, Any]:
    is_v5 = bool(package.table("variant_offer_relationships_v5"))
    review_batches = build_review_batches(
        package,
        local_pdf_href_prefix=local_pdf_href_prefix,
    )
    rows = () if is_v5 else _package_occurrences(package)
    family = _v5_review_family_summary(package, review_batches) if is_v5 else None
    if rows:
        if not all(
            _text(row, "variant_id", "canonical_variant_id", "shopify_variant_id")
            and _text(row, "vendor", "vendor_name", "supplier")
            and _text(
                row,
                "source_occurrence_id",
                "occurrence_id",
                "offer_id",
                "source_offer_id",
            )
            for row in rows
        ):
            raise SupplierReviewError(
                "review rows require Variant ID, vendor, and source occurrence ID"
            )
        variant_rows: Sequence[Mapping[str, Any]]
        if is_v5:
            variant_rows = [
                {
                    "variant_id": row.get("variant_id"),
                    "status": row.get("status"),
                    "display_status": row.get("v5_display_status"),
                }
                for row in package.table("catalog_coverage_v5")
            ]
        else:
            variant_rows = package.table("affected_variants") or package.table("variants")
        family = build_offer_family_report(
            rows,
            variants=variant_rows,
            representation_gaps=package.table("representation_gaps"),
            cohorts=package.cohorts,
            integrity={
                "package_status": package.status,
                "manifest_sha256": package.manifest_sha256,
                "available_checks": dict(package.metadata.get("integrity_checks", {})),
            },
            dependency_groups=(
                package.table("dependency_evidence_groups_v5")
                or package.table("dependency_groups")
            ),
            retain_raw_records=not is_v5,
            include_flat_occurrences=not is_v5,
        )
    a1_ledger_rows = (
        {
            table_name: _required_a1_ledger_rows(package, table_name)
            for table_name in _A1_LEDGER_TABLE_COUNTS
        }
        if package.package_kind == _A1_PACKAGE_KIND
        else {}
    )
    document = {
        "label": package.label,
        "status": package.status,
        "package": package.summary(),
        "offer_family": family,
        "review_batches": review_batches,
        "representation_gaps": [dict(gap) for gap in REPRESENTATION_GAP_CATALOG],
        "unavailable_evidence": list(package.unavailable_evidence),
        "comparison": None if comparison is None else dict(comparison),
        "operational_effects": {
            "database_writes": 0,
            "mapping_approvals": 0,
            "price_approvals": 0,
            "shopify_calls": 0,
            "po_actions": 0,
        },
    }
    if package.package_kind == _A1_PACKAGE_KIND:
        document["catalog_search"] = {
            "variant_count": len(review_batches),
            "displayed_occurrence_count": sum(
                len(batch.get("offers", ())) for batch in review_batches
            ),
            "identity": "SHOPIFY_VARIANT_VENDOR_SOURCE_OCCURRENCE_V1",
            "preferred_offer_selected": False,
            "local_pdf_navigation": (
                "VERIFIED_SIBLING_DIRECTORY_LINKS"
                if local_pdf_href_prefix == "../original_sources/"
                else "OMITTED_UNBOUND_OUTPUT_LAYOUT"
            ),
        }
        document["owner_decision_ledger"] = a1_ledger_rows["owner_decisions"]
        document["owner_decision_display_scopes"] = a1_ledger_rows[
            "owner_decision_display_scopes"
        ]
        document["prior_owner_answers_do_not_reask"] = a1_ledger_rows[
            "requests__prior_owner_answers_do_not_reask"
        ]
        document["retained_owner_questions_policy_check"] = a1_ledger_rows[
            "requests__retained_owner_questions_policy_check"
        ]
        document["source_review_overlays"] = a1_ledger_rows[
            "source_review_overlays"
        ]
        document["combo_review_ledger"] = {
            "complete_combo_totals": a1_ledger_rows["complete_combos"],
            "source_components": a1_ledger_rows["combo_components"],
            "combo_validation": a1_ledger_rows["combo_validation"],
            "fixed_component_relationships": a1_ledger_rows[
                "fixed_combo_component_relationships_v5"
            ],
            "remaining_component_diagnostics": a1_ledger_rows[
                "remaining_combo_component_relationships_v5"
            ],
            "remaining_combo_total_reviews": a1_ledger_rows[
                "remaining_combo_total_reviews_v5"
            ],
            "remaining_supplier_family_reviews": a1_ledger_rows[
                "remaining_supplier_family_reviews_v5"
            ],
        }
        metadata = package.metadata
        document["real_package_acceptance"] = {
            key: dict(metadata.get(key, {}))
            for key in (
                "replacement_transport",
                "tables_by_namespace",
                "source_evidence",
                "schema_controls",
                "external_pdf_status",
                "page_bundle_status",
                "projection_controls",
                "v5_sidecar_controls",
                "relationship_controls",
                "integrity_checks",
                "authority",
            )
            if isinstance(metadata.get(key), Mapping)
        }
        document["real_package_acceptance"]["ledger_projection"] = {
            table_name: {
                "row_count": len(rows),
                "canonical_jsonl_sha256": package.tables[
                    table_name
                ].canonical_jsonl_sha256,
            }
            for table_name, rows in a1_ledger_rows.items()
        }
    return document


def canonical_report_bytes(report: Mapping[str, Any]) -> bytes:
    return _canonical_json(report) + b"\n"


def canonical_report_json(report: Mapping[str, Any]) -> str:
    return canonical_report_bytes(report).decode("utf-8")


def _html_value(value: Any) -> str:
    if value is None:
        text = "—"
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, Mapping):
        text = "; ".join(f"{key}: {item}" for key, item in sorted(value.items()))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        text = "; ".join(str(item) for item in value)
    else:
        text = str(value)
    return html.escape(protect_spreadsheet_text(text))


def _html_table(headers: Sequence[str], rows: Iterable[Sequence[Any]], *, css_class: str = "") -> str:
    class_attr = f' class="{html.escape(css_class)}"' if css_class else ""
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_html_value(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table{class_attr}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _html_embedded_json(value: Any) -> str:
    """Embed canonical JSON in an inert script node without an HTML escape seam."""

    text = _canonical_json(value).decode("utf-8")
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _a1_catalog_browser(
    review_batches: Sequence[Mapping[str, Any]],
    ledgers: Mapping[str, Any],
) -> str:
    """Render the code-owned offline browser for every A1 catalog Variant."""

    data = _html_embedded_json(
        {"batches": list(review_batches), "global_review_ledgers": dict(ledgers)}
    )
    return (
        '<section id="catalog-browser" aria-labelledby="catalog-heading">'
        '<h2 id="catalog-heading">Full catalog search and evidence drill-down</h2>'
        '<p>Search all 2,000 original catalog Variants by Variant ID, title, option, '
        'supplier, supplier code, description, or source occurrence. Results do not rank '
        'or select a preferred offer.</p>'
        '<label for="catalog-search">Search catalog</label> '
        '<input id="catalog-search" type="search" autocomplete="off" '
        'placeholder="Variant ID, product, supplier code…"> '
        '<span id="catalog-count" role="status" aria-live="polite"></span>'
        '<span class="catalog-pagination">'
        '<button id="catalog-previous" type="button">Previous</button> '
        '<button id="catalog-next" type="button">Next</button>'
        '</span>'
        '<div class="catalog-layout"><nav id="catalog-results" '
        'aria-label="Catalog search results"></nav>'
        '<article id="catalog-detail" tabindex="-1">Enter a search or open a review.</article></div>'
        '</section>'
        '<section aria-labelledby="global-ledgers-heading">'
        '<h2 id="global-ledgers-heading">Global review evidence ledgers</h2>'
        '<p>These are preserved review records, not mappings, selections, prices, or approvals.</p>'
        '<div id="a1-global-ledgers"></div></section>'
        f'<script id="a1-catalog-data" type="application/json">{data}</script>'
        r'''<script>
"use strict";
(() => {
  const payload = JSON.parse(document.getElementById("a1-catalog-data").textContent);
  const batches = payload.batches;
  const globalLedgers = payload.global_review_ledgers || {};
  const search = document.getElementById("catalog-search");
  const results = document.getElementById("catalog-results");
  const detail = document.getElementById("catalog-detail");
  const count = document.getElementById("catalog-count");
  const previous = document.getElementById("catalog-previous");
  const next = document.getElementById("catalog-next");
  const globalLedgerRoot = document.getElementById("a1-global-ledgers");
  const pageSize = 100;
  let resultPage = 0;
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  const appendJson = (target, heading, value, initiallyOpen = false) => {
    const values = Array.isArray(value) ? value : (value ? [value] : []);
    if (!values.length) return;
    const box = make("details");
    box.open = initiallyOpen;
    box.append(make("summary", `${heading} (${values.length})`));
    box.append(make("pre", JSON.stringify(value, null, 2)));
    target.append(box);
  };
  const safePdfHref = value => {
    if (typeof value !== "string") return null;
    const match = /^\.\.\/original_sources\/([^/]+\.pdf)#page=([1-9][0-9]*)$/.exec(value);
    if (!match) return null;
    let filename;
    try { filename = decodeURIComponent(match[1]); } catch (_) { return null; }
    if (!filename || /[\\/\u0000-\u001f\u007f]/.test(filename)) return null;
    const encoded = encodeURIComponent(filename).replace(/[!'()*]/g, character =>
      `%${character.charCodeAt(0).toString(16).toUpperCase()}`
    );
    return value === `../original_sources/${encoded}#page=${match[2]}` ? value : null;
  };
  const openReview = batch => {
    detail.replaceChildren();
    const catalog = batch.catalog_review || {};
    detail.append(make("h3", catalog.captured_product_title || `Variant ${batch.variant_id}`));
    detail.append(make("p", `Variant ID ${batch.variant_id} · ${catalog.captured_variant_options || "option not captured"}`));
    detail.append(make("p", "REVIEW PREVIEW — NOT APPROVED", "notice"));
    detail.append(make("p", `${(batch.offers || []).length} displayed source occurrences; no preferred offer selected.`));
    appendJson(detail, "Catalog capture and partition", catalog, true);
    (batch.offers || []).forEach(offer => {
      const box = make("section", null, "offer-card");
      const heading = `${offer.vendor || "Unknown supplier"} · ${offer.supplier_code || "code unavailable"}`;
      box.append(make("h4", heading));
      box.append(make("p", `${offer.source_occurrence_id || "occurrence unavailable"} · ${offer.candidate_disposition || "UNRESOLVED"}`));
      if (offer.supplier_description) box.append(make("p", offer.supplier_description));
      const source = offer.source || {};
      const sourceLine = make("p");
      sourceLine.append(document.createTextNode(`Source: ${source.file || "unavailable"} · page ${source.page || "—"} · expected SHA-256 ${source.sha256 || "unavailable"} `));
      const href = safePdfHref(source.local_pdf_href);
      if (href) {
        const link = make("a", "Open local PDF page (bytes verified when report was generated)");
        link.setAttribute("href", href);
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
        sourceLine.append(link);
      } else {
        sourceLine.append(make("span", `[${source.availability || "source bytes unavailable"}]`));
      }
      box.append(sourceLine);
      const facts = {
        program_type: offer.program_type,
        reviewed_shopify_units_per_case: offer.reviewed_shopify_units_per_case,
        reviewed_qualifying_units_per_case: offer.reviewed_qualifying_units_per_case,
        raw_packaging: offer.raw_packaging,
        reviewed_packaging_display: offer.reviewed_packaging_display,
        display_evidence: offer.display_evidence,
        frozen_v5_relationship_preview: offer.frozen_v5_relationship_preview,
        blockers: offer.blockers,
        authority: offer.authority,
        offer_preview_fingerprint: offer.offer_preview_fingerprint
      };
      const factsBox = make("details");
      factsBox.append(make("summary", "Conversion, package, blockers, and authority"));
      factsBox.append(make("pre", JSON.stringify(facts, null, 2)));
      box.append(factsBox);
      const tiers = offer.price_ladder || [];
      const tierBox = make("details");
      tierBox.append(make("summary", `Price-ladder evidence (${tiers.length})`));
      tierBox.append(make("pre", JSON.stringify(tiers, null, 2)));
      box.append(tierBox);
      if ((offer.related_sidecar_records || []).length) {
        const sidecars = make("details");
        sidecars.append(make("summary", `Gift, component, and other sidecar evidence (${offer.related_sidecar_records.length})`));
        sidecars.append(make("pre", JSON.stringify(offer.related_sidecar_records, null, 2)));
        box.append(sidecars);
      }
      if (offer.sibling_review) {
        const sibling = make("details");
        sibling.append(make("summary", "Daytime sibling-offer review"));
        sibling.append(make("pre", JSON.stringify(offer.sibling_review, null, 2)));
        box.append(sibling);
      }
      if (offer.same_code_diagnostic) {
        const diagnostic = make("details");
        diagnostic.append(make("summary", "Same-code diagnostic (not identity support)"));
        diagnostic.append(make("pre", JSON.stringify(offer.same_code_diagnostic, null, 2)));
        box.append(diagnostic);
      }
      detail.append(box);
    });
    appendJson(detail, "Rejected alternatives (base plus separate additions)", batch.rejected_matches);
    appendJson(detail, "Negative match memory", batch.rejected_match_memory);
    appendJson(detail, "Owner clarifications with exact scope", batch.owner_clarifications);
    appendJson(detail, "Active unresolved evidence requirements", batch.active_requirements);
    appendJson(detail, "Pending approval-only requirements", batch.pending_approval_requirements);
    appendJson(detail, "Fixed and remaining combo relationships", batch.combo_relationships);
    try {
      history.replaceState(null, "", `#variant=${encodeURIComponent(batch.variant_id)}`);
    } catch (_) {
      // Some hardened file:// profiles prohibit history mutation. The detail
      // remains fully usable; only the optional address fragment is omitted.
    }
    detail.focus({preventScroll: true});
  };
  const refresh = () => {
    const query = search.value.trim().toLowerCase();
    const matches = batches.filter(batch => !query || String(batch.search_text || "").includes(query));
    const pageCount = Math.ceil(matches.length / pageSize);
    resultPage = pageCount ? Math.min(resultPage, pageCount - 1) : 0;
    const start = resultPage * pageSize;
    const end = Math.min(start + pageSize, matches.length);
    count.textContent = matches.length
      ? `${matches.length} matching Variants; showing ${start + 1}-${end}; page ${resultPage + 1}/${pageCount}`
      : "0 matching Variants; showing 0; page 0/0";
    previous.disabled = resultPage === 0;
    next.disabled = pageCount === 0 || resultPage >= pageCount - 1;
    results.replaceChildren();
    matches.slice(start, end).forEach(batch => {
      const catalog = batch.catalog_review || {};
      const button = make("button", `${batch.variant_id} — ${catalog.captured_product_title || "Untitled Variant"}`);
      button.type = "button";
      button.addEventListener("click", () => openReview(batch));
      results.append(button);
    });
  };
  search.addEventListener("input", () => {
    resultPage = 0;
    refresh();
  });
  previous.addEventListener("click", () => {
    if (resultPage > 0) resultPage -= 1;
    refresh();
  });
  next.addEventListener("click", () => {
    resultPage += 1;
    refresh();
  });
  Object.entries(globalLedgers).forEach(([name, value]) => {
    appendJson(globalLedgerRoot, name.replaceAll("_", " "), value);
  });
  refresh();
  const match = /^#variant=([^&]+)$/.exec(location.hash);
  if (match) {
    let id = "";
    try { id = decodeURIComponent(match[1]); } catch (_) { id = ""; }
    const batch = batches.find(item => item.variant_id === id);
    if (batch) {
      search.value = id;
      refresh();
      openReview(batch);
    }
  }
})();
</script>'''
    )


def render_review_html(report: Mapping[str, Any], *, title: str = "Supplier mapping review") -> str:
    """Render a bounded reviewer summary; canonical detail remains in report.json."""

    package_value = report.get("package", {})
    package = package_value if isinstance(package_value, Mapping) else {}
    is_a1 = package.get("package_kind") == _A1_PACKAGE_KIND
    label = html.escape(str(report.get("label", REVIEW_LABEL)))
    status = html.escape(str(report.get("status", package.get("status", "REVIEW"))))
    escaped_title = html.escape(title)
    family_value = report.get("offer_family")
    if not isinstance(family_value, Mapping):
        family_value = report if isinstance(report.get("offer_families"), Sequence) else {}
    family = family_value
    summary_value = family.get("summary", {})
    summary = summary_value if isinstance(summary_value, Mapping) else {}
    invariants_value = family.get("invariants", {})
    invariants = invariants_value if isinstance(invariants_value, Mapping) else {}
    operational_value = report.get("operational_effects", {})
    operational = operational_value if isinstance(operational_value, Mapping) else {}
    unavailable_value = report.get("unavailable_evidence", package.get("unavailable_evidence", ()))
    unavailable = (
        unavailable_value
        if isinstance(unavailable_value, Sequence) and not isinstance(unavailable_value, str)
        else ()
    )
    gaps_value = report.get("representation_gaps", family.get("representation_gaps", ()))
    gaps = (
        gaps_value
        if isinstance(gaps_value, Sequence) and not isinstance(gaps_value, str)
        else ()
    )
    readiness_value = package.get("readiness", {})
    readiness = readiness_value if isinstance(readiness_value, Mapping) else {}
    review_batches_value = report.get("review_batches", ())
    review_batches = (
        review_batches_value
        if isinstance(review_batches_value, Sequence) and not isinstance(review_batches_value, str)
        else ()
    )
    preview_by_identity: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for batch in review_batches:
        if not isinstance(batch, Mapping):
            continue
        offers = batch.get("offers", ())
        if not isinstance(offers, Sequence) or isinstance(offers, str):
            continue
        for offer in offers:
            if isinstance(offer, Mapping):
                key = (
                    str(offer.get("variant_id")),
                    str(offer.get("vendor")),
                    str(offer.get("source_occurrence_id")),
                )
                preview_by_identity[key] = offer

    alternatives: list[Mapping[str, Any]] = []
    families_value = family.get("offer_families", ())
    if isinstance(families_value, Sequence) and not isinstance(families_value, str):
        for offer_family in families_value:
            if not isinstance(offer_family, Mapping):
                continue
            candidate_alternatives = offer_family.get("alternatives", ())
            if isinstance(candidate_alternatives, Sequence) and not isinstance(
                candidate_alternatives, str
            ):
                alternatives.extend(item for item in candidate_alternatives if isinstance(item, Mapping))
    if not alternatives:
        for batch in review_batches:
            if not isinstance(batch, Mapping):
                continue
            offers = batch.get("offers", ())
            if isinstance(offers, Sequence) and not isinstance(offers, str):
                alternatives.extend(item for item in offers if isinstance(item, Mapping))
    computed_blocked_count = 0
    for item in alternatives:
        blockers_value = item.get("blockers", {})
        blockers = blockers_value if isinstance(blockers_value, Mapping) else {}
        if item.get("blocked") is True or any(
            isinstance(values, Sequence)
            and not isinstance(values, str)
            and bool(values)
            for values in blockers.values()
        ):
            computed_blocked_count += 1
    package_rows = (
        ("Package kind", package.get("package_kind", "—")),
        ("Snapshot", package.get("snapshot_id", "—")),
        ("Files verified", f"{package.get('verified_file_count', '—')} / {package.get('file_count', '—')}"),
        ("Manifest SHA-256", package.get("manifest_sha256", "—")),
        ("Unavailable evidence", len(unavailable)),
    )
    review_rows = (
        ("Source rows", summary.get("source_rows", len(alternatives))),
        ("Distinct occurrences", summary.get("distinct_occurrences", len(alternatives))),
        (
            "Offer families",
            summary.get(
                "offer_families",
                len(families_value)
                if isinstance(families_value, Sequence) and not isinstance(families_value, str)
                else 0,
            ),
        ),
        ("Alternatives shown", len(alternatives) if is_a1 else min(len(alternatives), 200)),
        (
            "Blocked alternatives",
            summary.get("blocked_alternatives", computed_blocked_count),
        ),
        ("Conflicting occurrences", summary.get("conflicting_occurrences", 0)),
        ("Import-ready rows", invariants.get("import_ready_rows", 0)),
        ("Representation gaps", len(gaps)),
        ("Review-preview batches", len(review_batches)),
    )
    alternative_rows = []
    for item in alternatives[:200]:
        conversions_value = item.get("conversions", {})
        conversions = dict(conversions_value) if isinstance(conversions_value, Mapping) else {}
        if not conversions:
            conversions = {
                "shopify_units_per_supplier_case": item.get(
                    "reviewed_shopify_units_per_case"
                ),
                "supplier_qualifying_units_per_case": item.get(
                    "reviewed_qualifying_units_per_case"
                ),
            }
        occurrence_id = item.get("occurrence_id", item.get("source_occurrence_id"))
        preview = preview_by_identity.get(
            (str(item.get("variant_id")), str(item.get("vendor")), str(occurrence_id)),
            item if item.get("offer_preview_fingerprint") else {},
        )
        raw_packaging_value = item.get("raw_packaging", {})
        raw_packaging = (
            raw_packaging_value if isinstance(raw_packaging_value, Mapping) else {}
        )
        evidence_value = item.get("evidence", {})
        if not isinstance(evidence_value, Mapping):
            evidence_value = {}
        source_value = item.get("source", {})
        evidence = (
            source_value
            if not evidence_value and isinstance(source_value, Mapping)
            else evidence_value
        )
        guard_reasons = item.get("guard_reasons", ())
        if not guard_reasons:
            blockers = item.get("blockers", {})
            if isinstance(blockers, Mapping):
                guard_reasons = sorted(
                    {
                        str(reason)
                        for values in blockers.values()
                        if isinstance(values, Sequence) and not isinstance(values, str)
                        for reason in values
                    }
                )
        alternative_rows.append(
            (
                item.get("variant_id"),
                item.get("vendor"),
                occurrence_id
                if occurrence_id is not None
                else (
                    item.get("identity", {}).get("offer_id")
                    if isinstance(item.get("identity"), Mapping)
                    else None
                ),
                item.get("supplier_sku", item.get("supplier_code")),
                item.get("supplier_description"),
                item.get("program_type"),
                item.get("effective_disposition", item.get("candidate_disposition")),
                item.get("owner_preference_decision_ids"),
                guard_reasons,
                raw_packaging.get("source_case_pack_raw"),
                raw_packaging.get(
                    "source_physical_count_raw",
                    conversions.get("physical_units_per_supplier_case"),
                ),
                raw_packaging.get(
                    "source_retail_pack_raw",
                    conversions.get("retail_units_per_supplier_case"),
                ),
                conversions.get("shopify_units_per_supplier_case"),
                evidence.get("source_file", evidence.get("file")),
                evidence.get("source_page", evidence.get("page")),
                preview.get("offer_preview_fingerprint"),
                preview.get("related_sidecar_records", ()),
            )
        )
    exception_counts_value = family.get("exception_counts", {})
    exception_counts = exception_counts_value if isinstance(exception_counts_value, Mapping) else {}
    gap_rows = [
        (
            gap.get("gap_id"),
            gap.get("area"),
            gap.get("current_handling"),
            gap.get("later_route_status"),
        )
        for gap in gaps[:100]
        if isinstance(gap, Mapping)
    ]
    comparison_value = report.get("comparison")
    comparison = comparison_value if isinstance(comparison_value, Mapping) else None

    sections = [
        (
            f"<p><strong>{html.escape(REVIEW_PREVIEW_LABEL)}</strong></p>"
            if review_batches
            else ""
        ),
        "<h2>Package integrity</h2>",
        _html_table(("Check", "Value"), package_rows, css_class="summary"),
        "<h2>Readiness (separate gates)</h2>",
        _html_table(("Gate", "Status"), sorted(readiness.items()), css_class="summary"),
        "<h2>Review summary</h2>",
        _html_table(("Measure", "Value"), review_rows, css_class="summary"),
        "<h2>Operational effects</h2>",
        _html_table(("Effect", "Count"), sorted(operational.items()), css_class="summary"),
    ]
    if unavailable:
        sections.extend(
            (
                "<h2>Unavailable evidence</h2>",
                "<ul>" + "".join(f"<li>{_html_value(item)}</li>" for item in unavailable) + "</ul>",
            )
        )
    if is_a1:
        missing_ledgers = [
            key for key in _A1_LEDGER_DOCUMENT_KEYS if key not in report
        ]
        if missing_ledgers:
            raise SupplierReviewError(
                "A1 report is missing required review ledgers: "
                + ", ".join(missing_ledgers)
            )
        sections.append(
            _a1_catalog_browser(
                review_batches,
                {key: report[key] for key in _A1_LEDGER_DOCUMENT_KEYS},
            )
        )
    elif alternative_rows:
        sections.extend(
            (
                "<h2>Offer alternatives</h2>",
                _html_table(
                    (
                        "Variant ID",
                        "Vendor",
                        "Occurrence",
                        "Supplier code",
                        "Description",
                        "Program",
                        "Disposition",
                        "Owner decision IDs",
                        "Guards",
                        "Raw case pack",
                        "Raw physical/case",
                        "Raw retail/case",
                        "Reviewed Shopify/case",
                        "Source file",
                        "Source page",
                        "Offer preview fingerprint",
                        "Related gift/component evidence",
                    ),
                    alternative_rows,
                ),
            )
        )
        if len(alternatives) > len(alternative_rows):
            sections.append(
                f"<p>Showing {_html_value(len(alternative_rows))} of "
                f"{_html_value(len(alternatives))} alternatives.</p>"
            )
    if exception_counts:
        sections.extend(
            (
                "<h2>Machine-readable exception counts</h2>",
                _html_table(("Exception", "Count"), sorted(exception_counts.items())),
            )
        )
    if gap_rows:
        sections.extend(
            (
                "<h2>Representation-gap routes</h2>",
                _html_table(("Gap", "Area", "Current handling", "Later status"), gap_rows),
            )
        )
    if comparison is not None:
        comparison_summary = comparison.get("summary", {})
        comparison_rows = [
            ("Status", comparison.get("status", "—")),
            ("Simulated", comparison.get("simulated", "—")),
        ]
        if isinstance(comparison_summary, Mapping):
            comparison_rows.extend(sorted(comparison_summary.items()))
        sections.extend(
            (
                "<h2>Monthly comparison</h2>",
                _html_table(("Measure", "Value"), comparison_rows),
            )
        )
    detail_note = (
        "Full canonical report detail and review projections are retained in "
        "<code>report.json</code>; the sealed portable package remains the source "
        "for all 120 tables and 746,048 canonical records."
        if is_a1
        else "Full canonical machine detail, including raw evidence and exceptions, "
        "is retained in <code>report.json</code>."
    )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        "img-src 'self' data: file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "connect-src 'none'; object-src 'none'; frame-src 'none'; form-action 'none'; "
        "base-uri 'none'\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        f"<title>{escaped_title}</title>"
        "<style>body{font-family:system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%;margin:0 0 1.25rem}"
        "th,td{border:1px solid #bbb;padding:.45rem;text-align:left;vertical-align:top}"
        "th{background:#eee}.summary{max-width:48rem}code,pre{overflow-wrap:anywhere}"
        "#catalog-search{box-sizing:border-box;font:inherit;margin:.5rem 0;padding:.55rem;"
        "width:min(42rem,100%)}#catalog-count{display:inline-block;margin-left:.5rem}"
        ".catalog-layout{display:grid;gap:1rem;grid-template-columns:minmax(16rem,1fr) "
        "minmax(0,2fr);margin-top:1rem}.catalog-layout nav{border:1px solid #bbb;"
        "max-height:70vh;overflow:auto;padding:.4rem}.catalog-layout nav button{background:#fff;"
        "border:0;border-bottom:1px solid #ddd;cursor:pointer;display:block;font:inherit;"
        "padding:.55rem;text-align:left;width:100%}.catalog-layout nav button:hover,"
        ".catalog-layout nav button:focus{background:#eef5ff}.catalog-layout article{"
        "min-width:0}.offer-card{border:1px solid #bbb;border-radius:.25rem;margin:1rem 0;"
        "padding:.75rem}.offer-card pre,details pre{background:#f6f6f6;max-height:32rem;"
        "overflow:auto;padding:.65rem;white-space:pre-wrap}.notice{color:#8a2500;font-weight:700}"
        "@media(max-width:760px){.catalog-layout{grid-template-columns:1fr}.catalog-layout nav{"
        "max-height:18rem}}</style>"
        f"</head><body><h1>{escaped_title}</h1><p>{label}</p><p>Status: <strong>{status}</strong></p>"
        "<p>No mapping, price, inventory, readiness, Shopify, or purchase-order write occurred.</p>"
        + "".join(sections)
        + f"<p>{detail_note}</p>"
        "</body></html>"
    )


render_report_html = render_review_html
