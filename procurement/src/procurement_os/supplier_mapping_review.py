"""Deterministic, review-only supplier offer-family and monthly reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from typing import Any, Iterable, Mapping, Sequence
import unicodedata

from .supplier_review_package import REVIEW_LABEL, ReviewPackage, _canonical_json


REVIEW_PREVIEW_LABEL = "REVIEW PREVIEW — NOT APPROVED"


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


def build_review_batches(package: ReviewPackage) -> list[dict[str, Any]]:
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
    return batches


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
        "label": REVIEW_LABEL,
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


def report_document(package: ReviewPackage, *, comparison: Mapping[str, Any] | None = None) -> dict[str, Any]:
    is_v5 = bool(package.table("variant_offer_relationships_v5"))
    review_batches = build_review_batches(package)
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
    return {
        "label": REVIEW_LABEL,
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


def render_review_html(report: Mapping[str, Any], *, title: str = "Supplier mapping review") -> str:
    """Render a bounded reviewer summary; canonical detail remains in report.json."""

    package_value = report.get("package", {})
    package = package_value if isinstance(package_value, Mapping) else {}
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
        ("Alternatives shown", min(len(alternatives), 200)),
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
    if alternative_rows:
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
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        f"<title>{escaped_title}</title>"
        "<style>body{font-family:system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%;margin:0 0 1.25rem}"
        "th,td{border:1px solid #bbb;padding:.45rem;text-align:left;vertical-align:top}"
        "th{background:#eee}.summary{max-width:48rem}code{overflow-wrap:anywhere}</style>"
        f"</head><body><h1>{escaped_title}</h1><p>{label}</p><p>Status: <strong>{status}</strong></p>"
        "<p>No mapping, price, inventory, readiness, Shopify, or purchase-order write occurred.</p>"
        + "".join(sections)
        + "<p>Full canonical machine detail, including raw evidence and exceptions, "
        "is retained in <code>report.json</code>.</p>"
        "</body></html>"
    )


render_report_html = render_review_html
