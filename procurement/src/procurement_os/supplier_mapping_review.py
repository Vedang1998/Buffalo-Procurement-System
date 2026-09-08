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
        raw = occurrence["raw"]
        for field_name in ("supplier_title", "reviewed_supplier_title", "supplier_description"):
            value = raw.get(field_name)
            if isinstance(value, str) and value != "":
                key = (str(occurrence["vendor"]), value)
                evidence[key].add(str(occurrence["occurrence_id"]))
                fields[key].add(field_name)
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
            prior_code = _text(
                occurrence["raw"],
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
) -> dict[str, Any]:
    """Build an occurrence-preserving projection; never select an offer."""

    occurrences: list[dict[str, Any]] = []
    identities: dict[tuple[str, str, str], tuple[str, int]] = {}
    duplicate_count = 0
    conflicting_occurrences: list[dict[str, Any]] = []
    duplicate_occurrences: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
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
                duplicate_occurrences.append(
                    {
                        "identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]},
                        "row": position,
                        "source_references": _source_references(row),
                        "raw": row,
                    }
                )
                continue
            prior_position = identities[key][1]
            conflict = {
                "identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]},
                "first_row": prior_position,
                "conflicting_row": position,
                "first_commercial_sha256": identities[key][0],
                "conflicting_commercial_sha256": fingerprint,
                "raw": row,
            }
            conflicting_occurrences.append(conflict)
            for retained in occurrences:
                if retained["variant_id"] == key[0] and retained["vendor"] == key[1] and retained["occurrence_id"] == key[2]:
                    retained["blocked"] = True
                    retained["effective_disposition"] = "BLOCKED"
                    retained["guard_reasons"] = sorted(set(retained["guard_reasons"] + ["CONFLICTING_COMMERCIAL_FACTS"]))
                    retained["exceptions"].append(
                        _exception(
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
        occurrences.append(
            {
                "authority": "UNAPPROVED_REVIEW_EVIDENCE",
                "blocked": blocked,
                "candidate_only": candidate,
                "catalog_status": catalog_status or None,
                "conversions": conversions,
                "disposition": disposition,
                "effective_disposition": "BLOCKED" if blocked else "REVIEW_CANDIDATE",
                "guard_reasons": sorted(set(guard_reasons)),
                "evidence": _source_references(row),
                "exceptions": exceptions,
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
                "raw": row,
                "supplier_description": _text(
                    row, "reviewed_supplier_title", "supplier_title", "supplier_description"
                ),
                "supplier_sku": sku,
                "variant_id": key[0],
                "vendor": key[1],
            }
        )

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
    all_exceptions = sorted(
        (item for occurrence in occurrences for item in occurrence["exceptions"]),
        key=lambda item: (
            item["scope"]["variant_id"],
            item["scope"]["vendor"],
            item["scope"]["source_occurrence_id"],
            item["code"],
        ),
    )
    gap_evidence = [
        dict(gap) if isinstance(gap, Mapping) else {"description": str(gap)}
        for gap in representation_gaps
    ]
    return {
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
        "occurrences": occurrences,
        "offer_families": offer_families,
        "simultaneous_alternatives": [
            {"variant_id": family["variant_id"], "count": len(family["alternatives"])}
            for family in offer_families
            if len(family["alternatives"]) > 1
        ],
        "duplicate_occurrences": duplicate_occurrences,
        "conflicting_occurrences": conflicting_occurrences,
        "exceptions": all_exceptions,
        "exception_counts": dict(sorted(Counter(item["code"] for item in all_exceptions).items())),
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
        ("proposed_shopify_sellable_units_per_case", "shopify_units_per_case"),
    ),
    ("inner_pack_units", ("supplier_retail_pack", "containers_per_retail_unit", "inner_pack_units")),
    ("supplier_order_increment", ("supplier_order_increment", "order_increment")),
    (
        "supplier_qualifying_units_per_case",
        ("supplier_qualifying_units_per_case", "qualifying_units_per_case"),
    ),
    ("abv_percent", ("abv_percent", "alcohol_by_volume")),
    ("proof", ("proof",)),
    ("expression", ("expression",)),
    ("vintage", ("supplier_vintage", "vintage")),
    ("vintage_raw", ("supplier_vintage_raw",)),
    ("effective_from", ("effective_from", "valid_from", "start_date")),
    ("effective_through", ("effective_through", "effective_to", "valid_to", "end_date")),
    ("territory", ("territory", "territory_applicability")),
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
        value, found = _aliased_value(row, canonical, aliases)
        values[canonical] = None if value is _MISSING else value
        conflicts.extend(found)
    if conflicts:
        fields = sorted({str(item["canonical_field"]) for item in conflicts})
        raise SupplierReviewError(f"conflicting monthly aliases for fields: {fields!r}")
    return values


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
) -> dict[str, Any]:
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
        differences = {
            field: {"before": before_values[field], "after": after_values[field]}
            for field in before_values
            if before_values[field] != after_values[field]
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
                )
            ):
                categories.add("PACK_CHANGED")
            if any(field in differences for field in ("abv_percent", "proof", "expression")):
                categories.add("EXPRESSION_OR_PROOF_CHANGED")
            if "vintage" in differences or "vintage_raw" in differences:
                categories.add("VINTAGE_CHANGED")
            if any(field in differences for field in ("effective_from", "effective_through", "territory")):
                categories.add("DATE_OR_TERRITORY_CHANGED")
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
    return {
        "label": REVIEW_LABEL,
        "status": "PASS",
        "authority": "REVIEW_ONLY",
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


def _package_occurrences(package: ReviewPackage) -> Sequence[Mapping[str, Any]]:
    for name in ("offers", "affected_candidates", "normalized_price_contract_review_delta"):
        rows = package.table(name)
        if rows:
            return rows
    return ()


def compare_review_packages(previous: ReviewPackage, current: ReviewPackage) -> dict[str, Any]:
    if not previous.is_complete or not current.is_complete:
        missing = sorted(
            set(previous.unavailable_evidence) | set(current.unavailable_evidence)
        )
        return {
            "label": REVIEW_LABEL,
            "status": "NOT_COMPARABLE",
            "authority": "REVIEW_ONLY",
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
    return compare_review_snapshots(
        _package_occurrences(previous),
        _package_occurrences(current),
        previous_tiers=previous.table("tiers"),
        current_tiers=current.table("tiers"),
    )


def report_document(package: ReviewPackage, *, comparison: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = _package_occurrences(package)
    family = None
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
        family = build_offer_family_report(
            rows,
            variants=package.table("affected_variants") or package.table("variants"),
            representation_gaps=package.table("representation_gaps"),
            cohorts=package.cohorts,
            integrity={
                "package_status": package.status,
                "manifest_sha256": package.manifest_sha256,
                "available_checks": dict(package.metadata.get("integrity_checks", {})),
            },
            dependency_groups=package.table("dependency_groups"),
        )
    return {
        "label": REVIEW_LABEL,
        "status": package.status,
        "package": package.summary(),
        "offer_family": family,
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


def render_review_html(report: Mapping[str, Any], *, title: str = "Supplier mapping review") -> str:
    """Render a compact escaped report; input markup and formulas stay inert."""

    label = html.escape(str(report.get("label", REVIEW_LABEL)))
    status = html.escape(str(report.get("status", report.get("package", {}).get("status", "REVIEW"))))
    payload = canonical_report_json(report)
    escaped_payload = html.escape(protect_spreadsheet_text(payload))
    escaped_title = html.escape(title)
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"robots\" content=\"noindex,nofollow\">"
        f"<title>{escaped_title}</title>"
        "<style>body{font-family:system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;padding:1rem}</style>"
        f"</head><body><h1>{escaped_title}</h1><p>{label}</p><p>Status: <strong>{status}</strong></p>"
        "<p>No mapping, price, inventory, readiness, Shopify, or purchase-order write occurred.</p>"
        f"<pre>{escaped_payload}</pre></body></html>"
    )


render_report_html = render_review_html
