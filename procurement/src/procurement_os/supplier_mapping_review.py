"""Deterministic, review-only supplier offer-family and monthly reports."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from typing import Any, Iterable, Mapping, Sequence

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


class SupplierReviewError(ValueError):
    pass


def _text(row: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value is not None and str(value) != "":
            return str(value)
    return None


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

    variant = _text(row, "variant_id", "canonical_variant_id", "shopify_variant_id")
    vendor = _text(row, "vendor", "vendor_name", "supplier")
    occurrence = _text(row, "source_occurrence_id", "occurrence_id", "offer_id", "source_tier_id")
    if not variant or not vendor or not occurrence:
        raise SupplierReviewError("occurrence requires Variant ID, vendor, and source occurrence ID")
    return variant, vendor, occurrence


def tier_identity(row: Mapping[str, Any]) -> tuple[tuple[str, str, str], str]:
    occurrence = occurrence_identity(row)
    tier = _text(row, "source_tier_id", "tier_id")
    if not tier:
        raise SupplierReviewError("monthly tier comparison requires source_tier_id")
    return occurrence, tier


def protect_spreadsheet_text(value: Any) -> str:
    """Protect formula-leading display cells without mutating source values."""

    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _conversions(row: Mapping[str, Any]) -> dict[str, Any]:
    physical = row.get(
        "physical_units_per_supplier_case",
        row.get("physical_containers_per_case", row.get("physical_bottles_per_case", row.get("physical_cans_per_case"))),
    )
    return {
        "physical_units_per_supplier_case": _decimal(physical),
        "retail_units_per_supplier_case": _decimal(
            row.get("supplier_case_pack", row.get("retail_units_per_case"))
        ),
        "shopify_units_per_supplier_case": _decimal(
            row.get("proposed_shopify_sellable_units_per_case", row.get("shopify_units_per_case"))
        ),
        "inner_pack_units": _decimal(
            row.get("supplier_retail_pack", row.get("containers_per_retail_unit"))
        ),
        "supplier_order_increment": _decimal(row.get("supplier_order_increment", row.get("order_increment"))),
        "supplier_qualifying_unit": _text(row, "supplier_qualifying_unit", "qualifying_unit", "break_unit"),
        "supplier_qualifying_units_per_case": _decimal(
            row.get("supplier_qualifying_units_per_case", row.get("qualifying_units_per_case"))
        ),
    }


def _commercial_fingerprint(row: Mapping[str, Any]) -> str:
    ignored = {
        "review_note",
        "notes",
        "source_reference",
        "source_file",
        "source_page",
        "observed_at",
    }
    value = {key: row[key] for key in sorted(row) if key not in ignored}
    return hashlib.sha256(_canonical_json(value)).hexdigest()


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
                    {"identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]}, "row": position}
                )
                continue
            prior_position = identities[key][1]
            conflicting_occurrences.append(
                {
                    "identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]},
                    "first_row": prior_position,
                    "conflicting_row": position,
                    "raw": row,
                }
            )
            for retained in occurrences:
                if retained["variant_id"] == key[0] and retained["vendor"] == key[1] and retained["occurrence_id"] == key[2]:
                    retained["blocked"] = True
                    retained["effective_disposition"] = "BLOCKED"
                    retained["guard_reasons"] = sorted(set(retained["guard_reasons"] + ["CONFLICTING_COMMERCIAL_FACTS"]))
                    break
            continue
        identities[key] = (fingerprint, position)
        disposition = (
            _text(row, "candidate_disposition", "mapping_status", "disposition", "source_status", "status")
            or "UNRESOLVED"
        ).upper()
        catalog_status = (
            _text(row, "catalog_status", "authoritative_catalog_status", "variant_status") or ""
        ).upper()
        policy_excluded = row.get("policy_excluded") is True
        blocked = disposition in BLOCKED_DISPOSITIONS or catalog_status in BLOCKED_DISPOSITIONS or policy_excluded
        candidate = disposition in {"PROPOSED MATCH", "PROPOSED_MATCH", "PROPOSED_REVIEW_CANDIDATE"}
        status_counts[disposition] += 1
        sku = _text(row, "supplier_sku", "supplier_code", "sku")
        if sku:
            sku_vendors[sku].add(key[1])
            sku_variants[sku].add(key[0])
        raw_dependencies = row.get("dependencies", row.get("v4_1_named_dependencies", []))
        if raw_dependencies is None:
            raw_dependencies = []
        if not isinstance(raw_dependencies, list):
            raw_dependencies = [raw_dependencies]
        for dependency in raw_dependencies:
            if isinstance(dependency, Mapping):
                dependencies[str(dependency.get("type", "UNSPECIFIED"))] += 1
            else:
                dependencies[str(dependency)] += 1
        guard_reasons = []
        if catalog_status == "CONFLICT":
            guard_reasons.append("CATALOG_CONFLICT")
        if catalog_status in {"POLICY EXCLUDED", "POLICY_EXCLUDED"}:
            guard_reasons.append("CATALOG_POLICY_EXCLUDED")
        if policy_excluded:
            guard_reasons.append("POLICY_EXCLUDED")
        if disposition in BLOCKED_DISPOSITIONS:
            guard_reasons.append("SOURCE_DISPOSITION_BLOCKED")
        occurrences.append(
            {
                "authority": "UNAPPROVED_REVIEW_EVIDENCE",
                "blocked": blocked,
                "candidate_only": candidate,
                "catalog_status": catalog_status or None,
                "conversions": _conversions(row),
                "disposition": disposition,
                "effective_disposition": "BLOCKED" if blocked else "REVIEW_CANDIDATE",
                "guard_reasons": guard_reasons,
                "evidence": {
                    "source_file": row.get("source_file"),
                    "source_page": row.get("source_page"),
                    "source_reference": row.get("source_reference", row.get("source_evidence")),
                },
                "expression": {
                    "abv_percent": _decimal(row.get("abv_percent")),
                    "proof": _decimal(row.get("proof")),
                    "vintage": row.get("vintage"),
                    "territory": row.get("territory"),
                    "package_type": row.get("package_type"),
                    "components": row.get("components"),
                },
                "occurrence_id": key[2],
                "identity": {"variant_id": key[0], "vendor": key[1], "offer_id": key[2]},
                "program_type": _text(row, "offer_type", "package_type") or "UNSPECIFIED",
                "raw": row,
                "supplier_description": row.get("supplier_description"),
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
    return {
        "label": REVIEW_LABEL,
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
        "representation_gaps": [dict(gap) if isinstance(gap, Mapping) else str(gap) for gap in representation_gaps],
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
    change_fields = (
        "supplier_sku",
        "supplier_code",
        "break_quantity",
        "break_unit",
        "case_price",
        "unit_price",
        "package_type",
        "physical_containers_per_case",
        "retail_units_per_case",
        "shopify_units_per_case",
        "inner_packs_per_case",
        "qualifying_units_per_case",
        "abv_percent",
        "proof",
        "expression",
        "vintage",
        "effective_from",
        "effective_through",
        "territory",
        "disposition",
        "candidate_disposition",
        "mapping_status",
    )
    changed = []
    unchanged = []
    categories: set[str] = set()
    for key in sorted(common):
        differences = {
            field: {"before": previous[key].get(field), "after": current[key].get(field)}
            for field in change_fields
            if previous[key].get(field) != current[key].get(field)
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
            if "supplier_sku" in differences or "supplier_code" in differences:
                categories.add("SUPPLIER_SKU_CHANGED")
            if any(field in differences for field in ("package_type", "physical_containers_per_case", "retail_units_per_case", "shopify_units_per_case", "inner_packs_per_case")):
                categories.add("PACK_CHANGED")
            if any(field in differences for field in ("abv_percent", "proof", "expression")):
                categories.add("EXPRESSION_OR_PROOF_CHANGED")
            if "vintage" in differences:
                categories.add("VINTAGE_CHANGED")
            if any(field in differences for field in ("effective_from", "effective_through", "territory")):
                categories.add("DATE_OR_TERRITORY_CHANGED")
            if "candidate_disposition" in differences:
                before_disposition = str(previous[key].get("candidate_disposition", ""))
                after_disposition = str(current[key].get("candidate_disposition", ""))
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
        elif after is None:
            kind = "MISSING_TIER_NOT_RETIREMENT"
        elif _commercial_fingerprint(before) == _commercial_fingerprint(after):
            continue
        elif before.get("unit_price") == after.get("unit_price") and before.get("break_quantity") != after.get("break_quantity"):
            kind = "THRESHOLD_CHANGED_PRICE_UNCHANGED"
        else:
            kind = "TIER_CHANGED"
        tier_changes.append(
            {
                "identity": [*key[0], key[1]],
                "kind": kind,
                "before_break_quantity": None if before is None else before.get("break_quantity"),
                "after_break_quantity": None if after is None else after.get("break_quantity"),
                "before_unit_price": None if before is None else before.get("unit_price"),
                "after_unit_price": None if after is None else after.get("unit_price"),
            }
        )
    prior_sku = defaultdict(set)
    current_sku = defaultdict(set)
    for key, row in previous.items():
        sku = row.get("supplier_sku", row.get("supplier_code"))
        if sku not in {None, ""}:
            prior_sku[(key[1], str(sku))].add(key)
    for key, row in current.items():
        sku = row.get("supplier_sku", row.get("supplier_code"))
        if sku not in {None, ""}:
            current_sku[(key[1], str(sku))].add(key)
    reuse = []
    for key in sorted(set(prior_sku) & set(current_sku)):
        if prior_sku[key] != current_sku[key]:
            reuse.append({"vendor": key[0], "supplier_sku": key[1], "before": [list(x) for x in sorted(prior_sku[key])], "after": [list(x) for x in sorted(current_sku[key])]})
    for rows_by_month in (previous, current):
        programs: defaultdict[str, set[str]] = defaultdict(set)
        for key, row in rows_by_month.items():
            programs[key[0]].add(str(row.get("offer_type", row.get("package_type", ""))))
        if any(len(values) > 1 and any("GIFT" in value for value in values) for values in programs.values()):
            categories.add("SIMULTANEOUS_GIFT_ALTERNATIVES")
    if any(
        "REJECTED" in str(previous[key].get("candidate_disposition", previous[key].get("disposition", ""))).upper()
        and "REJECTED" in str(current[key].get("candidate_disposition", current[key].get("disposition", ""))).upper()
        for key in common
    ):
        categories.add("REJECTED_MATCH_RECURRED")
    return {
        "label": REVIEW_LABEL,
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
        "categories": sorted(categories),
        "sku_reuse_or_replacement": reuse,
        "tier_changes": tier_changes,
        "candidate_vocabulary_only": True,
    }


def _package_occurrences(package: ReviewPackage) -> Sequence[Mapping[str, Any]]:
    for name in ("offers", "affected_candidates", "normalized_price_contract_review_delta"):
        rows = package.table(name)
        if rows:
            return rows
    return ()


def compare_review_packages(previous: ReviewPackage, current: ReviewPackage) -> dict[str, Any]:
    if not previous.is_complete or not current.is_complete:
        return {
            "label": REVIEW_LABEL,
            "status": "BASELINE_REQUIRED",
            "unavailable_evidence": sorted(
                set(previous.unavailable_evidence) | set(current.unavailable_evidence)
            ),
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
    if rows and all(
        _text(row, "variant_id", "canonical_variant_id", "shopify_variant_id")
        and _text(row, "vendor", "vendor_name", "supplier")
        and _text(row, "source_occurrence_id", "occurrence_id", "offer_id", "source_tier_id")
        for row in rows
    ):
        family = build_offer_family_report(
            rows,
            variants=package.table("affected_variants") or package.table("variants"),
            representation_gaps=(
                package.table("representation_gaps")
                or tuple(package.metadata.get("representation_gaps", ()))
            ),
            cohorts=package.cohorts,
            integrity={"status": package.status, "manifest_sha256": package.manifest_sha256},
            dependency_groups=package.table("dependency_groups"),
        )
    return {
        "label": REVIEW_LABEL,
        "status": package.status,
        "package": package.summary(),
        "offer_family": family,
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
