"""Explicit V5 and portable supplier-review package adapters.

Loaded lazily by the existing public reader. All contracts below are code
owned. Package hashes and schemas are claims to verify, never permissions or
approvals.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from .price_book import PRICE_BOOK_HEADERS
from .supplier_review_package import (
    CANONICAL_JSONL_HASH,
    RAW_HASH,
    REVIEW_LABEL,
    PackageTable,
    PatchReplay,
    ReviewPackage,
    ReviewPackageError,
    ValidationIssue,
    _DirectorySource,
    _PackageSource,
    _ZipSource,
    _canonical_json,
    _checked_record,
    _field_set,
    _manifest_files,
    _open_source,
    _parse_json_bytes,
    _read_bounded,
    _read_jsonl,
    _read_real_delta,
    _read_verified_bounded,
    _require_mapping,
    _require_sequence,
    _require_sha256,
    _stream_sha256,
    _table_unknown_fields,
    _validate_normalized_review_row,
    _validated_member_name,
    canonical_jsonl_sha256,
    canonical_record_sha256,
    replay_patch_table,
)


V5_VERSION = "V5"
V5_PACKAGE_KIND = "V5_CHANGED_TABLES_AND_EVIDENCE"
PORTABLE_FORMAT = "BUFFALO_PRIVATE_REVIEW_SNAPSHOT_V1"
PORTABLE_ROOT = "BUFFALO_REVIEW_PACKAGE_ROOT.json"
PORTABLE_SEAL = "BUFFALO_REVIEW_PACKAGE_SEAL.json"
PORTABLE_SEAL_FORMAT = "BUFFALO_REVIEW_PACKAGE_SEAL_V1"
PORTABLE_SYNTHETIC_REVISION = "SYNTHETIC_V5_COMPLETE_TEST_ONLY_V1"
UNAPPROVED_AUTHORITY = "UNAPPROVED_REVIEW"
PORTABLE_COMPARISON_CONTRACT = "BUFFALO_SUPPLIER_REVIEW_COMPARISON_V1"
PORTABLE_IDENTITY_CONTRACT = "SHOPIFY_VARIANT_VENDOR_SOURCE_OCCURRENCE_V1"


def _synthetic_lineage_ref(path: str, stage: str) -> dict[str, Any]:
    payload = _canonical_json(
        {
            "format": "BUFFALO_SYNTHETIC_LINEAGE_ANCHOR_V1",
            "source_revision": PORTABLE_SYNTHETIC_REVISION,
            "stage": stage,
        }
    ) + b"\n"
    return {"path": path, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


_PORTABLE_SYNTHETIC_LOCATOR_ROW = {
    "action": "SYNTHETIC_LOCATOR_CONTRACT",
    "row": 1,
    "source_revision": PORTABLE_SYNTHETIC_REVISION,
}
_PORTABLE_SYNTHETIC_LINEAGE_ANCHORS: Mapping[str, Any] = {
    "v4_manifest_refs": (
        _synthetic_lineage_ref("synthetic-contract/v4.json", "V4"),
    ),
    "v4_1_patch_refs": (
        _synthetic_lineage_ref("synthetic-contract/v4-1.jsonl", "V4_1"),
    ),
    "v4_1_locator_corrections": {
        "path": "synthetic-contract/locators.jsonl",
        "row_count": 1,
        "canonical_jsonl_sha256": canonical_jsonl_sha256(
            (_PORTABLE_SYNTHETIC_LOCATOR_ROW,)
        ),
    },
    "v5_patch_refs": (
        _synthetic_lineage_ref("synthetic-contract/v5.jsonl", "V5"),
    ),
}
PORTABLE_LINEAGE_FAMILY_SHA256 = hashlib.sha256(
    _canonical_json(
        {
            "comparison_contract": PORTABLE_COMPARISON_CONTRACT,
            "identity_contract": PORTABLE_IDENTITY_CONTRACT,
            "source_revision": PORTABLE_SYNTHETIC_REVISION,
            "lineage_anchors": _PORTABLE_SYNTHETIC_LINEAGE_ANCHORS,
        }
    )
).hexdigest()

_PORTABLE_ENCODING = (
    "UTF-8, sorted object keys, ensure_ascii=false, compact JSON, LF after each record; "
    "native JSON types and field presence preserved"
)
_PORTABLE_ROOT_FIELDS = frozenset(
    {
        "format", "source_revision", "package_id", "generated_at_utc", "authority",
        "cohorts", "approval_counts", "lineage", "tables", "source_evidence",
        "missing_prerequisites", "external_evidence", "review_only",
        "operational_effects", "snapshot_scope",
    }
)
_PORTABLE_SCOPE_FIELDS = frozenset(
    {
        "comparison_contract", "identity_contract", "lineage_family_sha256",
        "supplier_scope_kind", "supplier_scope", "cohort_scope", "period_semantics",
        "period_id", "supplier_period_coverage_complete", "missing_supplier_periods",
        "channels", "territories", "simulated",
    }
)
_PORTABLE_TABLE_FIELDS = frozenset(
    {"name", "field_dictionary_refs", "encoding", "row_count", "canonical_jsonl_sha256", "parts"}
)
_PORTABLE_PART_FIELDS = frozenset(
    {"ordinal", "archive", "path", "first_row_1based", "row_count", "bytes", "raw_sha256"}
)
_PORTABLE_TABLE_CONTRACTS: Mapping[str, tuple[frozenset[str], frozenset[str], tuple[str, ...]]] = {
    "variants": (
        frozenset({"variant_id"}),
        frozenset({"variant_id", "product_title", "variant_title", "catalog_status"}),
        ("SYNTHETIC_VARIANTS_V1",),
    ),
    "offers": (
        frozenset(
            {
                "variant_id", "vendor", "source_occurrence_id", "supplier_code",
                "source_file", "source_page", "source_sha256", "source_period",
                "territory", "channel",
                "candidate_disposition", "mapping_approved", "price_approved", "import_ready",
            }
        ),
        frozenset(
            {
                "variant_id", "vendor", "source_occurrence_id", "supplier_code", "program_type",
                "shopify_units_per_case", "qualifying_units_per_case", "physical_units_per_case",
                "retail_pack", "source_file", "source_page", "source_sha256", "source_period",
                "effective_from", "effective_through", "territory", "channel", "candidate_disposition",
                "catalog_status", "policy_excluded", "mapping_approved", "price_approved", "import_ready",
                "split_fee_basis", "owner_decision_id", "rejected_reason", "source_vintage",
            }
        ),
        ("SYNTHETIC_OFFERS_V1",),
    ),
    "tiers": (
        frozenset(
            {"source_tier_id", "variant_id", "vendor", "source_occurrence_id", "selected_tier"}
        ),
        frozenset(
            {
                "source_tier_id", "variant_id", "vendor", "source_occurrence_id", "break_unit",
                "break_quantity", "unit_price", "case_price", "selected_tier", "program_type",
            }
        ),
        ("SYNTHETIC_TIERS_V1",),
    ),
    "representation_gaps": (
        frozenset({"gap_id", "area", "current_handling", "later_route", "authority"}),
        frozenset({"gap_id", "area", "current_handling", "later_route", "authority"}),
        ("SYNTHETIC_REPRESENTATION_GAPS_V1",),
    ),
}

_V5_MANIFEST_FIELDS = frozenset(
    {
        "baseline_prerequisites",
        "current_price_approvals",
        "developer_packet",
        "files",
        "import_ready_rows",
        "mapping_approvals",
        "partitioned_at_utc",
        "path_resolution",
        "replaces_unsent_local_packaging_draft",
        "research_authority",
        "sealed_at_utc",
        "source_hashes_pages",
        "source_page_count",
        "source_page_index",
        "source_page_parts",
        "status",
        "unchanged_artifacts",
        "version",
        "workbook",
    }
)
_EXTERNAL_DESCRIPTOR_FIELDS = frozenset({"path", "workspace_relative_path", "bytes", "sha256"})
_V5_STATUS = "PRIVATE_UNAPPROVED_REVIEW_DELTA_NOT_STANDALONE_IMPORT"
_V5_UNDECIDED_PREFERENCE = "UNDECIDED"
_V5_NONPREFERRED_CONFIGURATION = (
    "NOT_THE_OWNER_PREFERRED_24_INDIVIDUAL_CONFIGURATION; SEPARATE_REVIEW_REQUIRED"
)
_V5_CONVERSION_AUTHORITY = (
    "V5_EXPLICIT_FIELD_IF_PRESENT_THEN_PRIOR_REVIEW; "
    "NONPROPOSED_OR_CONDITIONAL_GIFT_ALWAYS_NULL"
)
_V5_REASON_PRECEDENCE = (
    "V5 > V4.1 > V4 > V2; historical reasons retained, not current authority"
)
_V5_UNDECIDED_PREFERENCE = "UNDECIDED"
_V5_NONPREFERRED_CONFIGURATION = (
    "NOT_THE_OWNER_PREFERRED_24_INDIVIDUAL_CONFIGURATION; SEPARATE_REVIEW_REQUIRED"
)
_V5_TABLE_CATALOG_RAW_SHA256 = "2d291637e5f4e34ca23ec3d742712cccf102c718600f5ba40b6dfbf583e66fee"
_V5_FIELD_PROFILES_RAW_SHA256 = "5748ee81f403a59c14f13e49ad31c310025af8765530fcde5b43c20a38dc031e"
_V5_CHANGED_TABLE_HASHES_RAW_SHA256 = "af712200158c5a2b7198d74634bf5fa7ec6a399ff56a15b96219be77dae9ec59"
_V5_PATCH_RAW_SHA256: Mapping[str, str] = {
    "catalog_coverage": "ef34220ddd0999c666490d51dba8ad7a9f3ced9b192038f7eba8a195391c3424",
    "candidate_matches": "075b6a1b67f73b516f3be59d30f981b9772af454750a20d4a4e5783b610d2cdc",
    "candidate_projection_eligibility": "9a89f5a081bc722a257bdaad208979c45e6a666d1478dd36985786839875ebf0",
    "unresolved_dependencies": "6096495cbd9ba78cb5051d62fae1867d7ef52ae645b4e7c1d38959c332c8d59a",
    "variant_review_1720": "56d4c56bc58446845be48346f4dcd5b1b95ab9903a72e56b6bcde69138b7c456",
    "offer_pair_reviews": "e8454cb74643c95a68d16ce30340d968a802b0e5ba54a1b91b485b740639b06c",
    "current_additions_review": "babe1bc68faf02b17a04bc052877b1cec8c97290c7c49c610ed1bea46f06dfdb",
    "normalized_price_contract_links": "c4fd679f19eb2076bdea1244e573905c0cefcef013ed8f6d587dba6519ff9fd5",
    "normalized_price_contract_review": "af02f3ce17c4fcb36c1cd73745ec506d0f1eb9b13dff0973c0142660dbb78615",
}

_V5_TABLE_ROWS: Mapping[str, int] = {
    "alcohol_gift_components_v5": 8,
    "catalog_coverage_v5": 2_000,
    "catalog_status_transition_matrix_v5": 11,
    "conditional_gift_relationships_v5": 110,
    "current_additions_review_v5": 4,
    "dependency_evidence_groups_v5": 134,
    "dependency_group_members_v5": 796,
    "exact_change_log": 3_494,
    "exact_occurrence_equivalence_candidates_v5": 536,
    "fixed_combo_component_relationships_v5": 249,
    "future_owner_question_batch_v5": 3,
    "latitude_research_adjudicated_v5": 12,
    "missing_supplier_source_coverage_v5": 71,
    "multi_offer_families_v5": 2_000,
    "rejected_match_memory_v5": 7_143,
    "remaining_combo_component_relationships_v5": 19,
    "remaining_combo_total_reviews_v5": 7,
    "remaining_supplier_family_reviews_v5": 24,
    "researcher_tenhigh_size_corrections_v5": 2,
    "residual_scoped_dependency_closures_v5": 6,
    "root_family_visual_manifest_v5": 8,
    "root_supported_family_manual_reviews": 117,
    "root_web_read_log_v5": 43,
    "same_code_occurrence_differences_v5": 1_323,
    "scoped_dependency_closures_v5": 4,
    "source_control_reviews_v5": 9,
    "source_evidence_registry_v5": 17,
    "source_interpretation_overlays_v5": 6,
    "source_record_hash_verifications": 144,
    "specialist_historical_hash_reference_overlays": 2,
    "supplier_coverage_controls_v5": 9,
    "supplier_vocabulary_candidates_v5": 212,
    "targeted_candidate_after_records": 121,
    "targeted_identity_adjudications": 130,
    "targeted_normalized_contract_rows": 308,
    "targeted_variant_offer_relationships": 190,
    "ungrouped_historical_resolved_dependencies_v5": 3,
    "unresolved_dependencies_v5": 799,
    "v4_1_base_locator_corrections_v5": 17,
    "variant_offer_relationships_v5": 14_812,
    "web_identity_evidence_manifest_v5": 122,
    "workbook_sheet_change_log_v5": 33,
}

_V5_CATALOG_FIELDS = frozenset(
    {
        "byte_sha256",
        "candidate_unique_key_fields",
        "canonical_jsonl_sha256",
        "file",
        "key_note",
        "role",
        "rows",
        "scope_note",
        "table",
    }
)
_V5_PROFILE_FIELDS = frozenset(
    {
        "absent_count",
        "explicit_null_count",
        "field",
        "observed_json_types",
        "present_count",
        "row_count",
        "schema_authority",
        "table",
    }
)
_V5_PROFILE_AUTHORITY = "OBSERVED_REVIEW_DATA_NOT_NEW_CANONICAL_CONTRACT"

_V5_CORE_FIELDS: Mapping[str, frozenset[str]] = {
    "variant_offer_relationships_v5": _field_set(
        """
        candidate_disposition candidate_pair_existed_before_family_pass catalog_source_status
        conversion_authority current_reason diagnostic_relationship_layers family_id
        historical_reason_fields import_ready mapping_approved new_gift_sidecar_id
        no_code_replacement_inference owner_preference_decision_ids preference price_approved
        printed_page prior_candidate_record_reference product_id reason_precedence relationship_id
        reviewed_qualifying_units_per_case reviewed_shopify_units_per_case source_assortment_line_terms
        source_case_pack_raw source_description_raw source_file source_offer_id source_package_type_raw
        source_page source_period_raw source_physical_count_raw source_retail_pack_raw source_sha256
        source_size_raw source_split_availability source_split_fee_basis source_territory
        source_tier_ids source_vintage_markers_candidate source_vintage_raw supplier_code_exact
        supplier_name_raw tier_selection variant_id
        """
    ),
    "multi_offer_families_v5": _field_set(
        """
        approval_by_same_model_review candidate_occurrences catalog_source_status
        conditional_gift_links current_product_title_captured display_status family_id
        fixed_combo_component_links manual_visual_all_occurrences_claimed mapping_approved
        owner_preference_decision_ids preference price_approved
        prior_completed_variant_investigation_reused product_id product_title_raw
        rejected_occurrences remaining_supplier_component_review_ids
        remaining_supplier_component_review_links remaining_supplier_manual_family_review_ids
        root_manual_supported_family_review_id source_graph_join_check supported_proposed_occurrences
        supported_supplier_codes v41_existing_multi_candidate_family v5_targeted_identity_review_ids
        variant_id variant_title_raw
        """
    ),
    "conditional_gift_relationships_v5": _field_set(
        """
        allocated_component_cost candidate_identity_disposition complete_source_tier_ids
        diagnostic_relationship_type exact_gift_contents_and_acceptance explicit_null_precedence
        import_ready mapping_approved may_influence_normalized_price_projection order_increment
        original_candidate_disposition preference price_approved printed_page
        qualifying_units_per_case relationship_id root_review_references root_reviewed_at_utc
        shopify_units_per_case source_case_pack_raw source_description_raw source_fee_basis
        source_file source_offer_id source_page source_period_raw source_sha256 source_size_raw
        source_split_permission source_supplier_code source_territory_raw specialist_challenge_id
        variant_id whole_offer_to_single_variant_allowed
        """
    ),
    "fixed_combo_component_relationships_v5": _field_set(
        """
        allocated_component_cost basis combo_auto_add component_description_raw
        component_quantity_raw component_quantity_unit_raw component_relationship_id
        component_relationship_status component_size_raw component_supplier_sku
        cost_allocation_status import_ready manual_visual_review_claimed mapping_approved
        may_influence_normalized_price_projection parent_product_id preference price_approved
        printed_page provisional_component_conversion_basis provisional_component_shopify_units
        relationship_type root_adjudication root_reviewed_at_utc same_model_challenge_reference
        source_anchor_offer_ids source_combo_offer_id source_component_id source_file source_page
        source_sha256 supplier_combo_code supplier_qualifying_units_for_component variant_id
        whole_combo_to_variant_mapping_allowed whole_combo_total_cost
        """
    ),
    "unresolved_dependencies_v5": _field_set(
        "dependency dependency_id evidence_provider investigation_attempted provider_routing_basis resolved review_id source_reference status variant_id"
    ),
    "targeted_normalized_contract_rows": frozenset(
        {"contract_row", "row_number_1based", "source_offer_id", "source_tier_id"}
    ),
    "v4_1_base_locator_corrections_v5": frozenset(
        {
            "action",
            "after_record_sha256",
            "base_row_number_1based",
            "before_record_sha256",
            "corrected_base_stable_key",
            "original_delta_unchanged",
            "original_stable_key",
            "problem",
            "safe_locator",
            "table",
            "unique_base_row_count",
        }
    ),
}

_V5_CONTROL_TOTALS: Mapping[str, Any] = {
    "baseline_catalog_statuses": {
        "PROPOSED MATCH": 1_402, "NOT PROCESSED": 247, "NOT FOUND": 154,
        "CONFLICT": 68, "SUPPLIER SOURCE MISSING": 71, "POLICY EXCLUDED": 58,
    },
    "v5_catalog_statuses": {
        "PROPOSED MATCH": 1_450, "NOT PROCESSED": 206, "NOT FOUND": 143,
        "CONFLICT": 72, "SUPPLIER SOURCE MISSING": 71, "POLICY EXCLUDED": 58,
    },
    "baseline_candidates": 14_727,
    "v5_candidates": 14_760,
    "baseline_candidate_dispositions": {
        "SEARCH_LEAD_ONLY": 6_053, "REJECTED_ATTRIBUTE_CONFLICT": 7_121,
        "PROPOSED_REVIEW_CANDIDATE": 1_551, "REJECTED_IDENTITY_CONFLICT": 2,
    },
    "v5_candidate_dispositions": {
        "SEARCH_LEAD_ONLY": 6_016, "REJECTED_ATTRIBUTE_CONFLICT": 7_140,
        "PROPOSED_REVIEW_CANDIDATE": 1_602, "REJECTED_IDENTITY_CONFLICT": 2,
    },
    "baseline_dependencies": 745,
    "v5_dependencies": 799,
    "baseline_unresolved_dependencies": 742,
    "v5_unresolved_dependencies_including_historical_scope": 724,
    "v5_mapping_applicable_unresolved_dependencies": 714,
    "targeted_identity_adjudications": 130,
    "targeted_unique_original_variants": 130,
    "supported_targeted_pairs": 52,
    "original_cohort_count": 2_000,
    "current_census_count": 2_003,
    "current_original_returned": 1_999,
    "current_additions": 4,
    "mapping_approvals": 0,
    "current_price_approvals": 0,
    "import_ready_rows": 0,
}


@dataclass(frozen=True)
class _V5PatchContract:
    table: str
    stable_key_fields: tuple[str, ...]
    replace_fields: frozenset[str]
    append_fields: frozenset[str]
    baseline_rows: int
    effective_rows: int
    replaces: int
    appends: int
    positional_metadata_key: bool = False

    @property
    def patch_records(self) -> int:
        return self.replaces + self.appends


_V5_PATCH_CONTRACTS: Mapping[str, _V5PatchContract] = {
    "candidate_matches": _V5PatchContract(
        "candidate_matches", ("variant_id", "offer_id"),
        _field_set("""
            authoritative_catalog_status candidate_disposition
            catalog_exclusion_or_conflict_blocks_routine_purchase conversion_basis hard_conflicts
            import_ready mapping_approved mapping_status price_approved
            proposed_shopify_sellable_units_per_case review_requirements
            supplier_qualifying_units_per_case supplier_retail_pack v5_evidence_reference
            v5_explicit_reviewed_null_fields v5_hard_conflict_supersession_reason
            v5_historical_catalog_exclusion_or_conflict_flag v5_historical_hard_conflicts
            v5_nonselection_reason v5_prior_candidate_disposition v5_prior_conversion_fields
            v5_prior_review_requirements v5_review_completed v5_review_id v5_review_reason
            v5_review_status v5_reviewed_at v5_reviewed_qualifying_units_per_case
            v5_reviewed_shopify_units_per_case v5_superseded_hard_conflicts
        """),
        _field_set("""
            approval_status authoritative_catalog_status candidate_disposition captured_at
            catalog_barcode catalog_exclusion_or_conflict_blocks_routine_purchase conversion_basis
            current_planning_incoming_units import_ready mapping_approved mapping_status match_method
            offer_id offer_type physical_page physical_units_per_supplier_case policy_excluded
            policy_exclusion_reason price_approved printed_page product_id product_title
            proposed_shopify_sellable_units_per_case quantity_or_import_eligible raw_evidence
            reference_only review_requirements routine_purchase_candidate shopify_sku shopify_vendor
            source_file source_sha256 supplier supplier_case_pack supplier_code supplier_priority
            supplier_qualifying_units_per_case supplier_retail_pack supplier_size_raw supplier_title
            supplier_vintage_raw territory v5_evidence_reference v5_explicit_reviewed_null_fields
            v5_historical_catalog_exclusion_or_conflict_flag v5_nonselection_reason
            v5_original_pair_absent v5_prior_candidate_disposition v5_prior_reference_only
            v5_prior_review_requirements v5_reference_scope v5_review_completed v5_review_id
            v5_review_reason v5_review_status v5_reviewed_at v5_reviewed_qualifying_units_per_case
            v5_reviewed_shopify_units_per_case v5_scope_change_not_identity_approval
            v5_source_case_count_reviewed v5_source_physical_count_reviewed v5_source_size_evidence
            variant_gid variant_id variant_title verified_current_price
        """), 14_727, 14_760, 341, 33,
    ),
    "candidate_projection_eligibility": _V5PatchContract(
        "candidate_projection_eligibility", ("variant_id", "source_offer_id"),
        _field_set("""
            candidate_disposition candidate_has_populated_qualifying_conversion
            candidate_has_populated_shopify_conversion mapping_status
            may_influence_unapproved_review_projection selection_rule v5_review_id v5_review_status
        """),
        _field_set("""
            candidate_disposition candidate_has_populated_qualifying_conversion
            candidate_has_populated_shopify_conversion import_ready mapping_approved mapping_status
            may_influence_unapproved_review_projection selection_rule source_offer_id v5_review_id
            v5_review_status variant_id
        """), 14_727, 14_760, 88, 33,
    ),
    "catalog_coverage": _V5PatchContract(
        "catalog_coverage", ("variant_id",),
        _field_set("""
            candidate_offer_occurrences candidate_suppliers first_priority_candidate_supplier status
            v5_current_active_dependency_ids v5_current_remaining_named_dependencies
            v5_display_status v5_evidence_references v5_investigation_scope v5_named_dependencies
            v5_prior_catalog_candidate_counts v5_review_completed v5_review_ids v5_review_reason
            v5_review_status v5_reviewed_at v5_supported_proposed_offer_occurrences v5_work_attempted
        """), frozenset(), 2_000, 2_000, 2_000, 0,
    ),
    "current_additions_review": _V5PatchContract(
        "current_additions_review", ("variant_id",),
        _field_set("""
            attribute_review candidate_disposition remaining_named_dependencies review_status
            source_evidence v5_display_status v5_explicit_reviewed_null_precedence v5_identity_review
            v5_prior_reference_scope v5_qualifying_units_per_case v5_review_completed v5_review_id
            v5_reviewed_at v5_scope_authority v5_shopify_units_per_case v5_source_size_evidence
        """), frozenset(), 4, 4, 3, 0,
    ),
    "normalized_price_contract_links": _V5PatchContract(
        "normalized_price_contract_links", ("projection_row_id",),
        _field_set("""
            blocking_dependencies candidate_dispositions_used candidate_review_statuses_used
            candidate_variant_ids derived_unit_price_basis derived_unit_price_formula
            v5_explicit_null_precedence
        """), frozenset(), 80_661, 80_661, 308, 0,
    ),
    "normalized_price_contract_review": _V5PatchContract(
        "normalized_price_contract_review", ("normalized_table_row_number_1based",),
        _field_set("canonical_variant_id qualifying_units_per_case review_note shopify_units_per_case unit_price"),
        frozenset(), 80_661, 80_661, 308, 0, positional_metadata_key=True,
    ),
    "offer_pair_reviews": _V5PatchContract(
        "offer_pair_reviews", ("variant_id", "offer_id"), frozenset({"v5_review_record"}),
        frozenset({"offer_id", "review_record", "v5_review_record", "variant_id", "worker"}),
        3_820, 3_854, 87, 34,
    ),
    "unresolved_dependencies": _V5PatchContract(
        "unresolved_dependencies", ("dependency_id",),
        _field_set("""
            resolved status v5_exact_node_metafields_accessible v5_exact_node_recheck_at
            v5_exact_node_recheck_reference v5_exact_node_recheck_result
            v5_exact_node_recheck_sha256 v5_historical_census_unchanged
            v5_mapping_scope_applicability v5_original_dependency_text_and_id_retained
            v5_original_monday_buying_scope_retained v5_product_candidate_conversion_change
            v5_resolution_evidence v5_resolution_note v5_resolution_scope v5_resolved_at
            v5_scope_authority v5_scope_change_not_product_identity_resolution
        """),
        _field_set("""
            blocks_candidate_identity dependency dependency_id evidence_provider exact_evidence_needed
            investigation_attempted provider_routing_basis resolved review_id scope_offer_ids
            source_reference status v5_created_at variant_id
        """), 745, 799, 86, 54,
    ),
    "variant_review_1720": _V5PatchContract(
        "variant_review_1720", ("review_id",),
        _field_set("""
            after_status v5_named_dependencies v5_prior_after_status v5_proposed_offer_ids
            v5_review_ids v5_review_reason v5_reviewed_at v5_work_attempted
        """), frozenset(), 1_720, 1_720, 119, 0,
    ),
}

_V5_CANDIDATE_APPEND_COMMON = _field_set("""
    approval_status authoritative_catalog_status candidate_disposition captured_at
    catalog_barcode catalog_exclusion_or_conflict_blocks_routine_purchase conversion_basis
    current_planning_incoming_units import_ready mapping_approved mapping_status match_method
    offer_id offer_type physical_page policy_excluded policy_exclusion_reason price_approved
    printed_page product_id product_title proposed_shopify_sellable_units_per_case
    quantity_or_import_eligible raw_evidence routine_purchase_candidate shopify_sku
    shopify_vendor source_file source_sha256 supplier supplier_case_pack supplier_code
    supplier_priority supplier_qualifying_units_per_case supplier_size_raw supplier_title
    supplier_vintage_raw territory v5_evidence_reference v5_explicit_reviewed_null_fields
    v5_historical_catalog_exclusion_or_conflict_flag v5_nonselection_reason
    v5_original_pair_absent v5_prior_candidate_disposition v5_review_completed v5_review_id
    v5_review_reason v5_review_status v5_reviewed_at
    v5_reviewed_qualifying_units_per_case v5_reviewed_shopify_units_per_case
    variant_gid variant_id variant_title verified_current_price
""")
_V5_APPEND_SHAPES: Mapping[str, tuple[frozenset[str], ...]] = {
    **{
        name: (() if not contract.append_fields else (contract.append_fields,))
        for name, contract in _V5_PATCH_CONTRACTS.items()
        if name != "candidate_matches"
    },
    "candidate_matches": (
        _V5_CANDIDATE_APPEND_COMMON,
        _V5_CANDIDATE_APPEND_COMMON
        | _field_set("""
            physical_units_per_supplier_case review_requirements supplier_retail_pack
            v5_prior_review_requirements
        """),
        _V5_CANDIDATE_APPEND_COMMON
        | _field_set("""
            reference_only v5_prior_reference_only v5_reference_scope
            v5_scope_change_not_identity_approval v5_source_case_count_reviewed
            v5_source_physical_count_reviewed v5_source_size_evidence
        """),
    ),
}

_V5_DIRECT_EFFECTIVE_TABLES = {
    "catalog_coverage": "catalog_coverage_v5",
    "current_additions_review": "current_additions_review_v5",
    "unresolved_dependencies": "unresolved_dependencies_v5",
}
_PATCH_REPLACE_FIELDS = frozenset({
    "after_record_sha256", "base_row_number_1based", "before_record_sha256",
    "changes", "operation", "stable_key", "table",
})
_PATCH_APPEND_FIELDS = frozenset({
    "after_record_sha256", "append_record", "before_record_sha256",
    "operation", "stable_key", "table",
})
_PATCH_CHANGE_FIELDS = frozenset({"field", "before_present", "before", "after_present", "after"})
_V5_REVIEW_RECORD_FIELDS = _field_set("""
    adjudication_id after_source_status baseline_source_status import_ready mapping_approved
    price_approved reason research_check_id research_evidence_reference
    resolved_dependency_codes_scoped reviewed_at_utc reviewer root_disposition
    source_offer_ids supported_proposed_offer_ids tier_selected variant_id web_evidence_ids
""")
_V5_REVIEW_RECORD_SHAPES = (
    _V5_REVIEW_RECORD_FIELDS,
    _V5_REVIEW_RECORD_FIELDS | frozenset({"source_contradictions_retained"}),
)


def _assert_v5_review_only_payload(value: Any, *, context: str) -> None:
    """Reject authority-bearing post-state anywhere in a reviewed V5 payload."""

    pending = [value]
    false_fields = {
        "mapping_approved", "price_approved", "import_ready", "tier_selected",
        "quantity_or_import_eligible", "routine_purchase_candidate", "verified_current_price",
        "auto_add_authorized", "current_price_activated", "whole_gift_to_single_variant_mapping_allowed",
    }
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            for field, nested in item.items():
                if field in false_fields and nested is not False:
                    raise ReviewPackageError(
                        "UNAUTHORIZED_APPROVAL_CLAIM",
                        f"{context}.{field} must remain false",
                    )
                if field == "approval_status" and nested != "UNAPPROVED_CANDIDATE":
                    raise ReviewPackageError(
                        "UNAUTHORIZED_APPROVAL_CLAIM",
                        f"{context}.approval_status must remain unapproved",
                    )
                pending.append(nested)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            pending.extend(item)


def _assert_v5_review_record(value: Any, *, stable_key: Mapping[str, Any]) -> None:
    record = _require_mapping(
        value, code="V5_SCHEMA_VERSION_MISMATCH", message="v5_review_record must be an object"
    )
    if frozenset(record) not in _V5_REVIEW_RECORD_SHAPES:
        raise ReviewPackageError(
            "V5_SCHEMA_VERSION_MISMATCH", "v5_review_record fields differ from the reviewed contract"
        )
    if record.get("variant_id") != stable_key.get("variant_id"):
        raise ReviewPackageError("PATCH_KEY_MISMATCH", "review record Variant ID differs")
    offers = _require_sequence(
        record.get("source_offer_ids"),
        code="V5_SCHEMA_VERSION_MISMATCH",
        message="review record source offers must be an array",
    )
    if stable_key.get("offer_id") not in offers:
        raise ReviewPackageError("PATCH_KEY_MISMATCH", "review record source occurrence differs")
    _assert_v5_review_only_payload(record, context="offer_pair_reviews.v5_review_record")


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], *, code: str, context: str) -> None:
    if set(value) != expected:
        raise ReviewPackageError(code, f"{context} fields differ from the code-owned contract")


def _same_json_value(left: Any, right: Any) -> bool:
    """Compare values with the canonical native JSON type contract."""

    return _canonical_json(left) == _canonical_json(right)


def _nonnegative_int(value: Any, *, code: str, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewPackageError(code, f"{context} must be a nonnegative integer")
    return value


def _nonblank_text(value: Any, *, code: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewPackageError(code, f"{context} must be nonblank text")
    return value


def _iso_datetime(value: Any, *, context: str) -> str:
    text = _nonblank_text(value, code="INVALID_TIMESTAMP", context=context)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewPackageError("INVALID_TIMESTAMP", f"{context} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ReviewPackageError("INVALID_TIMESTAMP", f"{context} must include an offset")
    return text


def _verified_json(source: _PackageSource, path: str, declared: Mapping[str, tuple[int, str]]) -> Any:
    data = _read_verified_bounded(source, path, source.limits.max_manifest_bytes, declared)
    return _parse_json_bytes(data, path=path, limits=source.limits)


def _verified_jsonl(
    source: _PackageSource, path: str, declared: Mapping[str, tuple[int, str]]
) -> list[dict[str, Any]]:
    rows, raw_sha = _read_jsonl(source, path)
    if raw_sha != declared[path][1]:
        raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "JSONL bytes differ", path=path)
    return rows


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if isinstance(value, Decimal):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise ReviewPackageError("UNSUPPORTED_VALUE", "V5 row contains a non-JSON value")


def _hash_regular_file(path: str | Path, *, maximum: int) -> tuple[int, str]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ReviewPackageError("MISSING_PACKAGE", "required prerequisite must be a real file")
    size = candidate.stat(follow_symlinks=False).st_size
    if size > maximum:
        raise ReviewPackageError("ENTRY_TOO_LARGE", "prerequisite exceeds the offline limit")
    digest = hashlib.sha256()
    observed = 0
    with candidate.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            digest.update(chunk)
    if observed != size:
        raise ReviewPackageError("SIZE_MISMATCH", "prerequisite changed while hashing")
    return observed, digest.hexdigest()


def _validate_external_descriptor(
    value: Any,
    *,
    context: str,
    source: _PackageSource,
) -> dict[str, Any]:
    descriptor = _require_mapping(
        value,
        code="INVALID_MANIFEST",
        message=f"{context} must be an object",
    )
    _exact_fields(descriptor, _EXTERNAL_DESCRIPTOR_FIELDS, code="INVALID_MANIFEST", context=context)
    _validated_member_name(
        _nonblank_text(descriptor["path"], code="INVALID_MANIFEST", context=context),
        source.limits,
    )
    _nonblank_text(
        descriptor["workspace_relative_path"],
        code="INVALID_MANIFEST",
        context=f"{context}.workspace_relative_path",
    )
    _nonnegative_int(descriptor["bytes"], code="INVALID_MANIFEST", context=f"{context}.bytes")
    _require_sha256(descriptor["sha256"], path=context)
    return descriptor


def _validate_v5_external_evidence(
    manifest: Mapping[str, Any],
    *,
    root_path: str | Path | None,
    source: _PackageSource,
) -> tuple[dict[str, str], list[str]]:
    descriptors = {
        "workbook": _validate_external_descriptor(
            manifest.get("workbook"), context="workbook", source=source
        ),
        "developer_packet": _validate_external_descriptor(
            manifest.get("developer_packet"), context="developer_packet", source=source
        ),
    }
    if root_path is None:
        return {}, [
            f"EXTERNAL_WORKBOOK:{descriptors['workbook']['path']}",
            f"EXTERNAL_DEVELOPER_PACKET:{descriptors['developer_packet']['path']}",
        ]
    root = Path(root_path)
    if root.is_symlink() or not root.is_dir():
        raise ReviewPackageError(
            "INVALID_EXTERNAL_EVIDENCE_ROOT", "external evidence root must be a real directory"
        )
    verified: dict[str, str] = {}
    for label, descriptor in descriptors.items():
        target = root
        for component in descriptor["path"].split("/"):
            target = target / component
            if target.is_symlink():
                raise ReviewPackageError(
                    "EXTERNAL_EVIDENCE_SYMLINK",
                    f"{label} path contains a symlink",
                    path=descriptor["path"],
                )
        observed_bytes, observed_hash = _hash_regular_file(
            target, maximum=source.limits.max_entry_bytes
        )
        if observed_bytes != descriptor["bytes"] or observed_hash != descriptor["sha256"]:
            raise ReviewPackageError(
                "EXTERNAL_EVIDENCE_MISMATCH",
                f"{label} differs from the reviewed V5 descriptor",
                path=descriptor["path"],
            )
        verified[label] = observed_hash
    return verified, []


def _validate_v5_manifest(
    source: _PackageSource,
    manifest: Mapping[str, Any],
) -> tuple[dict[str, tuple[int, str]], int]:
    _exact_fields(manifest, _V5_MANIFEST_FIELDS, code="INVALID_MANIFEST", context="V5 manifest")
    if manifest.get("version") != V5_VERSION or manifest.get("status") != _V5_STATUS:
        raise ReviewPackageError("UNSUPPORTED_PACKAGE_VERSION", "V5 manifest identity or status differs")
    if any(type(manifest.get(field)) is not int or manifest.get(field) != 0 for field in ("mapping_approvals", "current_price_approvals", "import_ready_rows")):
        raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "V5 package must retain zero approval counts")
    _iso_datetime(manifest.get("sealed_at_utc"), context="sealed_at_utc")
    _iso_datetime(manifest.get("partitioned_at_utc"), context="partitioned_at_utc")
    if manifest.get("baseline_prerequisites") != "baseline_prerequisites_v5.json":
        raise ReviewPackageError("INVALID_MANIFEST", "V5 baseline prerequisite path differs")
    if manifest.get("unchanged_artifacts") != "unchanged_artifacts_v5.json":
        raise ReviewPackageError("INVALID_MANIFEST", "V5 unchanged-artifact path differs")
    if manifest.get("source_page_index") != "EXTERNAL_SOURCE_PAGE_INDEX.json" or manifest.get("source_page_count") != 174:
        raise ReviewPackageError("INVALID_MANIFEST", "V5 source-page controls differ")
    if not isinstance(manifest.get("path_resolution"), dict):
        raise ReviewPackageError("INVALID_MANIFEST", "path_resolution must be an object")
    _validate_external_descriptor(
        manifest.get("replaces_unsent_local_packaging_draft"),
        context="replaces_unsent_local_packaging_draft",
        source=source,
    )
    _nonblank_text(manifest.get("research_authority"), code="INVALID_MANIFEST", context="research_authority")
    _nonblank_text(manifest.get("source_hashes_pages"), code="INVALID_MANIFEST", context="source_hashes_pages")
    _validate_external_descriptor(manifest.get("workbook"), context="workbook", source=source)
    _validate_external_descriptor(manifest.get("developer_packet"), context="developer_packet", source=source)
    source_parts = _require_sequence(
        manifest.get("source_page_parts"),
        code="INVALID_MANIFEST",
        message="source_page_parts must be an array",
    )
    if len(source_parts) != 3:
        raise ReviewPackageError("INVALID_MANIFEST", "V5 must describe three source-page archives")
    part_paths: set[str] = set()
    for index, raw in enumerate(source_parts, start=1):
        descriptor = _validate_external_descriptor(
            raw,
            context=f"source_page_parts[{index}]",
            source=source,
        )
        path = descriptor["path"]
        if path in part_paths:
            raise ReviewPackageError("DUPLICATE_DECLARATION", "source-page archive is duplicated", path=path)
        part_paths.add(path)
    files = _require_sequence(
        manifest.get("files"), code="INVALID_MANIFEST", message="V5 files must be an array"
    )
    if len(files) != 200:
        raise ReviewPackageError("FILE_COUNT_MISMATCH", "V5 manifest must declare 200 members")
    for index, raw in enumerate(files, start=1):
        row = _require_mapping(raw, code="INVALID_MANIFEST", message=f"files[{index}] must be an object")
        _exact_fields(row, _EXTERNAL_DESCRIPTOR_FIELDS, code="INVALID_MANIFEST", context=f"files[{index}]")
    return _manifest_files(
        source,
        files,
        manifest_name="PACKAGE_FILE_HASHES.json",
        require_complete=True,
    )


def _read_v5_tables(
    source: _PackageSource,
    declared: Mapping[str, tuple[int, str]],
) -> dict[str, PackageTable]:
    catalog_path = "tables/table_catalog_v5.jsonl"
    profile_path = "tables/field_profiles_v5.jsonl"
    catalog_rows, catalog_raw_sha = _read_jsonl(source, catalog_path)
    if catalog_raw_sha != declared[catalog_path][1]:
        raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "V5 table catalog bytes differ")
    if catalog_raw_sha != _V5_TABLE_CATALOG_RAW_SHA256:
        raise ReviewPackageError("V5_SCHEMA_VERSION_MISMATCH", "V5 table catalog is not the reviewed schema")
    if len(catalog_rows) != len(_V5_TABLE_ROWS):
        raise ReviewPackageError("TABLE_COUNT_MISMATCH", "V5 table catalog count differs")

    catalog: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(catalog_rows, start=1):
        _exact_fields(row, _V5_CATALOG_FIELDS, code="INVALID_TABLE_CATALOG", context=f"catalog row {row_number}")
        name = _nonblank_text(row.get("table"), code="INVALID_TABLE_CATALOG", context="table")
        if name in catalog or name not in _V5_TABLE_ROWS:
            raise ReviewPackageError("INVALID_TABLE_CATALOG", "table set differs or is duplicated", table=name)
        if row.get("rows") != _V5_TABLE_ROWS[name]:
            raise ReviewPackageError("ROW_COUNT_MISMATCH", "V5 code-owned row count differs", table=name)
        expected_path = f"tables/{name}.jsonl"
        if row.get("file") != expected_path or expected_path not in declared:
            raise ReviewPackageError("INVALID_TABLE_CATALOG", "V5 table path differs", table=name)
        _require_sha256(row.get("byte_sha256"), path=expected_path)
        _require_sha256(row.get("canonical_jsonl_sha256"), path=expected_path)
        keys = _require_sequence(
            row.get("candidate_unique_key_fields"),
            code="INVALID_TABLE_CATALOG",
            message="candidate key fields must be an array",
        )
        if any(not isinstance(field, str) or not field for field in keys) or len(set(keys)) != len(keys):
            raise ReviewPackageError("INVALID_TABLE_CATALOG", "candidate key fields are invalid", table=name)
        if row.get("role") != "CURRENT_DIAGNOSTIC_REVIEW_SIDECAR_UNAPPROVED":
            raise ReviewPackageError("INVALID_AUTHORITY", "V5 table role must remain unapproved", table=name)
        catalog[name] = row
    if set(catalog) != set(_V5_TABLE_ROWS):
        raise ReviewPackageError("INVALID_TABLE_CATALOG", "V5 table set is incomplete")

    profile_rows, profile_raw_sha = _read_jsonl(source, profile_path)
    if profile_raw_sha != declared[profile_path][1]:
        raise ReviewPackageError("FILE_CHANGED_DURING_VALIDATION", "V5 field-profile bytes differ")
    if profile_raw_sha != _V5_FIELD_PROFILES_RAW_SHA256:
        raise ReviewPackageError("V5_SCHEMA_VERSION_MISMATCH", "V5 field profiles are not the reviewed schema")

    tables: dict[str, PackageTable] = {}
    raw_rows: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(catalog):
        item = catalog[name]
        path = item["file"]
        rows, raw_sha = _read_jsonl(source, path)
        if raw_sha != declared[path][1] or raw_sha != item["byte_sha256"]:
            raise ReviewPackageError("FILE_INTEGRITY_MISMATCH", "V5 table byte hash differs", path=path)
        canonical_sha = canonical_jsonl_sha256(rows)
        if canonical_sha != item["canonical_jsonl_sha256"]:
            raise ReviewPackageError("CANONICAL_HASH_MISMATCH", "V5 table canonical hash differs", path=path)
        if len(rows) != item["rows"]:
            raise ReviewPackageError("ROW_COUNT_MISMATCH", "V5 table row count differs", table=name)
        core_fields = _V5_CORE_FIELDS.get(name)
        if core_fields is not None:
            for row_number, row in enumerate(rows, start=1):
                valid_shape = (
                    core_fields.issubset(row)
                    if name == "unresolved_dependencies_v5"
                    else set(row) == core_fields
                )
                if not valid_shape:
                    raise ReviewPackageError(
                        "V5_SCHEMA_VERSION_MISMATCH",
                        "critical V5 table fields differ",
                        table=name,
                        row=row_number,
                    )
        for field_name in item["candidate_unique_key_fields"]:
            seen: set[bytes] = set()
            for row_number, row in enumerate(rows, start=1):
                value = row.get(field_name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    raise ReviewPackageError("TABLE_KEY_MISSING", f"{field_name} is blank", table=name, row=row_number)
                encoded = _canonical_json(value)
                if encoded in seen:
                    raise ReviewPackageError("TABLE_KEY_NOT_UNIQUE", f"{field_name} is duplicated", table=name)
                seen.add(encoded)
        raw_rows[name] = rows
        tables[name] = PackageTable(name, path, "jsonl", tuple(rows), raw_sha, canonical_sha)

    _validate_v5_field_profiles(raw_rows, profile_rows)
    known_by_table: dict[str, set[str]] = defaultdict(set)
    for row in profile_rows:
        known_by_table[row["table"]].add(row["field"])
    result = {
        name: PackageTable(
            table.name,
            table.path,
            table.format,
            table.rows,
            table.raw_sha256,
            table.canonical_jsonl_sha256,
            _table_unknown_fields(table.rows, known_by_table[name]),
        )
        for name, table in tables.items()
    }
    result["v5_table_catalog"] = PackageTable(
        "v5_table_catalog",
        catalog_path,
        "jsonl",
        tuple(catalog_rows),
        catalog_raw_sha,
        canonical_jsonl_sha256(catalog_rows),
    )
    result["v5_field_profiles"] = PackageTable(
        "v5_field_profiles",
        profile_path,
        "jsonl",
        tuple(profile_rows),
        profile_raw_sha,
        canonical_jsonl_sha256(profile_rows),
    )
    return result


def _validate_v5_field_profiles(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    profiles: Sequence[Mapping[str, Any]],
) -> None:
    observed_profiles: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row_number, row in enumerate(profiles, start=1):
        _exact_fields(row, _V5_PROFILE_FIELDS, code="INVALID_FIELD_PROFILE", context=f"profile row {row_number}")
        table_name = row.get("table")
        field_name = row.get("field")
        if table_name not in tables or not isinstance(field_name, str) or not field_name:
            raise ReviewPackageError("INVALID_FIELD_PROFILE", "profile table or field is invalid")
        key = (table_name, field_name)
        if key in observed_profiles:
            raise ReviewPackageError("INVALID_FIELD_PROFILE", "field profile is duplicated")
        if row.get("schema_authority") != _V5_PROFILE_AUTHORITY:
            raise ReviewPackageError("INVALID_AUTHORITY", "field profile may not claim schema authority")
        observed_profiles[key] = row
    expected_keys = {
        (table_name, field_name)
        for table_name, rows in tables.items()
        for row in rows
        for field_name in row
    }
    if set(observed_profiles) != expected_keys:
        raise ReviewPackageError("FIELD_PROFILE_MISMATCH", "field profiles do not cover exact V5 row fields")
    for (table_name, field_name), profile in observed_profiles.items():
        rows = tables[table_name]
        present_values = [row[field_name] for row in rows if field_name in row]
        types = Counter(_json_type_name(value) for value in present_values)
        expected = {
            "row_count": len(rows),
            "present_count": len(present_values),
            "absent_count": len(rows) - len(present_values),
            "explicit_null_count": sum(value is None for value in present_values),
            "observed_json_types": dict(types),
        }
        for key, value in expected.items():
            if profile.get(key) != value:
                raise ReviewPackageError(
                    "FIELD_PROFILE_MISMATCH",
                    f"{table_name}.{field_name} {key} differs",
                )


def _validate_v5_patch_rows(
    contract: _V5PatchContract,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if len(rows) != contract.patch_records:
        raise ReviewPackageError("PATCH_ROW_COUNT_MISMATCH", f"{contract.table} patch count differs")
    operations = Counter(row.get("operation") for row in rows)
    if operations != Counter({"replace_fields": contract.replaces, "append_record": contract.appends}):
        raise ReviewPackageError("PATCH_OPERATION_COUNT_MISMATCH", f"{contract.table} operations differ")
    seen_rows: set[int] = set()
    seen_keys: set[tuple[tuple[str, bytes], ...]] = set()
    for patch_number, raw in enumerate(rows, start=1):
        patch = _require_mapping(raw, code="INVALID_PATCH", message="V5 patch must be an object")
        operation = patch.get("operation")
        expected_fields = _PATCH_REPLACE_FIELDS if operation == "replace_fields" else _PATCH_APPEND_FIELDS
        _exact_fields(patch, expected_fields, code="INVALID_PATCH", context=f"{contract.table} patch {patch_number}")
        if patch.get("table") != contract.table:
            raise ReviewPackageError("INVALID_PATCH", "V5 patch table differs", table=contract.table)
        stable_key = _require_mapping(
            patch.get("stable_key"), code="INVALID_PATCH", message="stable_key must be an object"
        )
        if tuple(sorted(stable_key)) != tuple(sorted(contract.stable_key_fields)):
            raise ReviewPackageError("PATCH_KEY_FIELDS_MISMATCH", f"{contract.table} key fields differ")
        if contract.positional_metadata_key:
            invalid_key_value = any(type(value) is not int or value <= 0 for value in stable_key.values())
        else:
            invalid_key_value = any(not isinstance(value, str) or not value.strip() for value in stable_key.values())
        if invalid_key_value:
            raise ReviewPackageError("EMPTY_PATCH_KEY", f"{contract.table} key values must be exact nonblank IDs")
        key = tuple((field, _canonical_json(value)) for field, value in sorted(stable_key.items()))
        if key in seen_keys:
            raise ReviewPackageError("DUPLICATE_PATCH", f"{contract.table} stable key is duplicated")
        seen_keys.add(key)
        _require_sha256(patch.get("after_record_sha256"), path=f"{contract.table}[{patch_number}]")
        if operation == "append_record":
            if patch.get("before_record_sha256") is not None:
                raise ReviewPackageError("INVALID_PATCH", "V5 append before hash must be null")
            record = _require_mapping(
                patch.get("append_record"), code="INVALID_PATCH", message="append_record must be an object"
            )
            if frozenset(record) not in _V5_APPEND_SHAPES[contract.table]:
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", f"{contract.table} append fields differ")
            if not set(contract.stable_key_fields).issubset(record):
                raise ReviewPackageError("PATCH_REQUIRED_FIELD_MISSING", "V5 append omits its stable identity")
            if any(record.get(field) != value for field, value in stable_key.items()):
                raise ReviewPackageError("PATCH_KEY_MISMATCH", "append stable key differs from its record")
            _assert_v5_review_only_payload(record, context=f"{contract.table} append")
            if contract.table == "offer_pair_reviews":
                _assert_v5_review_record(record.get("v5_review_record"), stable_key=stable_key)
            if canonical_record_sha256(record) != patch["after_record_sha256"]:
                raise ReviewPackageError("PATCH_AFTER_HASH_MISMATCH", "V5 append record hash differs")
            continue
        if operation != "replace_fields":
            raise ReviewPackageError("INVALID_PATCH_OPERATION", "V5 patch operation is unsupported")
        row_number = patch.get("base_row_number_1based")
        if (
            isinstance(row_number, bool)
            or not isinstance(row_number, int)
            or row_number <= 0
            or row_number > contract.baseline_rows
            or row_number in seen_rows
        ):
            raise ReviewPackageError("PATCH_ROW_OUT_OF_RANGE", f"{contract.table} row locator is invalid")
        seen_rows.add(row_number)
        _require_sha256(patch.get("before_record_sha256"), path=f"{contract.table}[{patch_number}]")
        if contract.positional_metadata_key:
            if stable_key != {"normalized_table_row_number_1based": row_number}:
                raise ReviewPackageError(
                    "NORMALIZED_POSITION_MISMATCH",
                    "normalized positional key must equal the base row locator",
                )
        changes = _require_sequence(
            patch.get("changes"), code="INVALID_PATCH", message="replace_fields changes must be an array"
        )
        if not changes:
            raise ReviewPackageError("INVALID_PATCH", "replace_fields must change at least one field")
        changed_fields: set[str] = set()
        for raw_change in changes:
            change = _require_mapping(raw_change, code="INVALID_PATCH", message="change must be an object")
            _exact_fields(change, _PATCH_CHANGE_FIELDS, code="INVALID_PATCH", context="V5 patch change")
            field = change.get("field")
            if not isinstance(field, str) or not field or field in changed_fields:
                raise ReviewPackageError("DUPLICATE_PATCH_FIELD", "V5 patch fields must be unique")
            changed_fields.add(field)
            if field not in contract.replace_fields:
                raise ReviewPackageError("PATCH_FIELD_NOT_ALLOWED", f"{contract.table}.{field} is not authorized")
            _assert_v5_review_only_payload(
                {str(field): change.get("after")},
                context=f"{contract.table} replacement",
            )
            if contract.table == "offer_pair_reviews" and field == "v5_review_record":
                _assert_v5_review_record(change.get("after"), stable_key=stable_key)
            if field in stable_key:
                raise ReviewPackageError("PATCH_IDENTITY_FIELD_MUTATION", "V5 patch changes its stable key")
            if type(change.get("before_present")) is not bool or change.get("after_present") is not True:
                raise ReviewPackageError("PATCH_FIELD_REMOVAL_REJECTED", "V5 replacements must preserve fields")


def _read_v5_patches(
    source: _PackageSource,
    declared: Mapping[str, tuple[int, str]],
    tables: dict[str, PackageTable],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    if declared["changed_table_hashes.json"][1] != _V5_CHANGED_TABLE_HASHES_RAW_SHA256:
        raise ReviewPackageError(
            "V5_SCHEMA_VERSION_MISMATCH",
            "V5 changed-table hash gate is not the reviewed revision",
        )
    changed_rows = _require_sequence(
        _verified_json(source, "changed_table_hashes.json", declared),
        code="INVALID_CHANGED_TABLE_HASHES",
        message="V5 changed-table hashes must be an array",
    )
    expected_fields = frozenset(
        {
            "appends",
            "base_effective_v4_1_canonical_jsonl_sha256",
            "base_effective_v4_1_rows",
            "effective_v5_canonical_jsonl_sha256",
            "effective_v5_rows",
            "patch_byte_sha256",
            "patch_records",
            "replaces",
            "table",
        }
    )
    changed: dict[str, dict[str, Any]] = {}
    patches: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(changed_rows, start=1):
        item = _require_mapping(
            raw, code="INVALID_CHANGED_TABLE_HASHES", message=f"changed table {index} must be an object"
        )
        _exact_fields(item, expected_fields, code="INVALID_CHANGED_TABLE_HASHES", context=f"changed table {index}")
        name = item.get("table")
        if name not in _V5_PATCH_CONTRACTS or name in changed:
            raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "V5 changed table set differs")
        contract = _V5_PATCH_CONTRACTS[name]
        expected_numbers = {
            "base_effective_v4_1_rows": contract.baseline_rows,
            "effective_v5_rows": contract.effective_rows,
            "patch_records": contract.patch_records,
            "replaces": contract.replaces,
            "appends": contract.appends,
        }
        if any(
            not _same_json_value(item.get(field), value)
            for field, value in expected_numbers.items()
        ):
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", f"{name} patch controls differ")
        _require_sha256(item.get("base_effective_v4_1_canonical_jsonl_sha256"), path=name)
        _require_sha256(item.get("effective_v5_canonical_jsonl_sha256"), path=name)
        patch_sha = _require_sha256(item.get("patch_byte_sha256"), path=name)
        path = f"table_deltas/{name}.patch.jsonl"
        if (
            path not in declared
            or declared[path][1] != patch_sha
            or patch_sha != _V5_PATCH_RAW_SHA256[name]
        ):
            raise ReviewPackageError("PATCH_FILE_HASH_MISMATCH", "V5 patch file hash differs", path=path)
        rows = _verified_jsonl(source, path, declared)
        _validate_v5_patch_rows(contract, rows)
        patches[name] = rows
        changed[name] = item
        tables[f"v5_patch_{name}"] = PackageTable(
            f"v5_patch_{name}", path, "jsonl", tuple(rows), declared[path][1], canonical_jsonl_sha256(rows)
        )
    if set(changed) != set(_V5_PATCH_CONTRACTS):
        raise ReviewPackageError("INVALID_CHANGED_TABLE_HASHES", "V5 changed table set is incomplete")
    for patch_name, direct_name in _V5_DIRECT_EFFECTIVE_TABLES.items():
        direct = tables[direct_name]
        spec = changed[patch_name]
        if (
            len(direct.rows) != spec["effective_v5_rows"]
            or direct.canonical_jsonl_sha256 != spec["effective_v5_canonical_jsonl_sha256"]
        ):
            raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", f"{direct_name} differs from its V5 hash gate")
    return patches, changed


def _validate_v5_controls(
    source: _PackageSource,
    declared: Mapping[str, tuple[int, str]],
) -> dict[str, Any]:
    controls = _require_mapping(
        _verified_json(source, "control_totals.json", declared),
        code="INVALID_CONTROL_TOTALS",
        message="V5 control totals must be an object",
    )
    if set(controls) != set(_V5_CONTROL_TOTALS) | {"original_not_returned_variant_id"}:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "V5 control-total fields differ")
    for key, value in _V5_CONTROL_TOTALS.items():
        if not _same_json_value(controls.get(key), value):
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", f"V5 control {key} differs")
    _nonblank_text(
        controls.get("original_not_returned_variant_id"),
        code="CONTROL_TOTAL_MISMATCH",
        context="original_not_returned_variant_id",
    )
    if controls["current_original_returned"] + 1 != controls["original_cohort_count"]:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "original cohort return count does not reconcile")
    if controls["current_original_returned"] + controls["current_additions"] != controls["current_census_count"]:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "current census does not reconcile")

    family = _require_mapping(
        _verified_json(source, "family_control_totals.json", declared),
        code="INVALID_CONTROL_TOTALS",
        message="family controls must be an object",
    )
    expected_family = {
        "original_catalog_rows": 2_000,
        "prior_multi_candidate_families_checked": 1_789,
        "root_supported_families_manually_compared": 117,
        "remaining_supplier_manual_family_records": 24,
        "current_occurrence_relationships": 14_812,
        "current_multi_occurrence_families": 1_796,
        "families_with_multiple_supported_proposals": 128,
        "conditional_gift_pairs": 110,
        "conditional_gift_pairs_new_to_candidate_table": 52,
        "alcohol_gift_component_reviews": 8,
        "fixed_combo_component_links": 249,
        "negative_memory_records": 7_143,
        "remaining_supplier_component_review_rows": 19,
        "remaining_supplier_component_catalog_links": 8,
        "remaining_supplier_component_offers": 7,
        "historical_negative_records_with_new_current_disposition": 1,
        "mapping_approvals": 0,
        "price_approvals": 0,
        "import_ready_rows": 0,
        "private_commercial_sidecar_not_competing_canonical_model": True,
    }
    if not _same_json_value(family, expected_family):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "V5 family controls differ")

    sidecar = _require_mapping(
        _verified_json(source, "review_sidecar_control_totals.json", declared),
        code="INVALID_CONTROL_TOTALS",
        message="V5 sidecar controls must be an object",
    )
    for key, expected in {
        "vocabulary_candidates": 212,
        "original_dependency_groups": 106,
        "new_dependency_groups": 28,
        "total_display_groups": 134,
        "grouped_dependency_records": 796,
        "already_resolved_ungrouped_baseline_records": 3,
        "active_mapping_dependencies": 714,
        "active_unique_variants": 475,
        "source_interpretation_overlays": 6,
        "web_retrieval_summary_records": 122,
        "root_identity_web_read_entries": 43,
        "root_family_pages_actually_viewed": 8,
        "mapping_approvals": 0,
        "price_approvals": 0,
        "source_missing_catalog_variants": 71,
        "active_unattempted_source_read_dependencies": 0,
    }.items():
        if not _same_json_value(sidecar.get(key), expected):
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", f"V5 sidecar control {key} differs")
    provider_counts = sidecar.get("active_dependencies_by_provider_class")
    if (
        not isinstance(provider_counts, dict)
        or any(not isinstance(key, str) or not key for key in provider_counts)
        or any(type(value) is not int or value < 0 for value in provider_counts.values())
        or sum(provider_counts.values()) != 714
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "active provider-class counts do not reconcile")
    missing_by_vendor = sidecar.get("source_missing_variants_by_captured_vendor_label")
    if (
        not isinstance(missing_by_vendor, dict)
        or any(not isinstance(key, str) or not key for key in missing_by_vendor)
        or any(type(value) is not int or value < 0 for value in missing_by_vendor.values())
        or sum(missing_by_vendor.values()) != 71
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "source-missing vendor counts do not reconcile")

    validation = _require_mapping(
        _verified_json(source, "data_validation_v5.json", declared),
        code="INVALID_VALIDATION",
        message="V5 validation must be an object",
    )
    required_validation = {
        "status": "PASS",
        "passed_checks": 602,
        "failed_checks": 0,
        "original_189_hashes_match": True,
        "original_cohort": 2_000,
        "current_census_historical": 2_003,
        "mapping_approvals": 0,
        "price_approvals": 0,
        "import_ready_rows": 0,
        "application_code_executed": False,
        "same_model_review_not_independent_claude_approval": True,
    }
    if any(
        not _same_json_value(validation.get(key), value)
        for key, value in required_validation.items()
    ):
        raise ReviewPackageError("INVALID_VALIDATION", "V5 validation controls differ")
    return {"control_totals": controls, "family_controls": family, "sidecar_controls": sidecar}


def _validate_source_page_index(
    source: _PackageSource,
    declared: Mapping[str, tuple[int, str]],
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    index = _require_mapping(
        _verified_json(source, "EXTERNAL_SOURCE_PAGE_INDEX.json", declared),
        code="INVALID_SOURCE_INDEX",
        message="source-page index must be an object",
    )
    _exact_fields(
        index,
        frozenset({"version", "parts", "pages"}),
        code="INVALID_SOURCE_INDEX",
        context="source-page index",
    )
    if index.get("version") != V5_VERSION:
        raise ReviewPackageError("INVALID_SOURCE_INDEX", "source-page index version differs")
    parts = _require_sequence(
        index.get("parts"), code="INVALID_SOURCE_INDEX", message="source-page parts must be an array"
    )
    manifest_parts = _require_sequence(
        manifest.get("source_page_parts"),
        code="INVALID_SOURCE_INDEX",
        message="manifest source-page parts must be an array",
    )
    if len(parts) != 3 or tuple(parts) != tuple(manifest_parts):
        raise ReviewPackageError("SOURCE_INDEX_MISMATCH", "source-page part descriptors differ")
    part_names = {row["path"] for row in parts}
    pages = _require_sequence(
        index.get("pages"), code="INVALID_SOURCE_INDEX", message="source-page rows must be an array"
    )
    if len(pages) != 174:
        raise ReviewPackageError("SOURCE_PAGE_COUNT_MISMATCH", "source-page count differs")
    page_fields = frozenset(
        {"path", "workspace_relative_path", "bytes", "sha256", "delivery_part"}
    )
    seen: set[str] = set()
    part_counts: Counter[str] = Counter()
    for row_number, raw in enumerate(pages, start=1):
        row = _require_mapping(
            raw, code="INVALID_SOURCE_INDEX", message="source-page record must be an object"
        )
        _exact_fields(
            row,
            page_fields,
            code="INVALID_SOURCE_INDEX",
            context=f"source-page row {row_number}",
        )
        path = _validated_member_name(
            _nonblank_text(row.get("path"), code="INVALID_SOURCE_INDEX", context="source page path"),
            source.limits,
        )
        if path in seen:
            raise ReviewPackageError("DUPLICATE_SOURCE_PAGE", "source-page path is duplicated", path=path)
        seen.add(path)
        _nonblank_text(
            row.get("workspace_relative_path"),
            code="INVALID_SOURCE_INDEX",
            context="source-page workspace reference",
        )
        _nonnegative_int(row.get("bytes"), code="INVALID_SOURCE_INDEX", context="source-page bytes")
        _require_sha256(row.get("sha256"), path=path)
        delivery_part = row.get("delivery_part")
        if delivery_part not in part_names:
            raise ReviewPackageError("SOURCE_INDEX_MISMATCH", "page references an unknown delivery part")
        part_counts[delivery_part] += 1
    if sorted(part_counts.values()) != [44, 49, 81]:
        raise ReviewPackageError("SOURCE_PAGE_COUNT_MISMATCH", "source-page partition counts differ")
    unavailable = tuple(f"SOURCE_PAGE_ARCHIVE:{name}" for name in sorted(part_names))
    return {
        "status": "INDEX_VERIFIED_BYTES_UNAVAILABLE",
        "page_count": len(pages),
        "part_count": len(parts),
        "part_page_counts": dict(sorted(part_counts.items())),
    }, unavailable


def _validate_baseline_prerequisites(
    source: _PackageSource,
    declared: Mapping[str, tuple[int, str]],
) -> dict[str, Any]:
    value = _require_mapping(
        _verified_json(source, "baseline_prerequisites_v5.json", declared),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="V5 baseline prerequisites must be an object",
    )
    expected_fields = frozenset(
        {
            "version",
            "standalone_import",
            "required_sequence",
            "original_v4_companion_zip",
            "v4_1_delta_zip",
            "v4_1_master_workbook",
            "baseline_table_files",
            "original_v4_1_preconditions_reference",
            "v4_1_hash_gate_file",
            "v5_hash_gate_file",
            "original_encoding",
            "not_retransmitted",
            "mapping_approvals",
            "current_price_approvals",
            "import_ready_rows",
        }
    )
    _exact_fields(
        value,
        expected_fields,
        code="INVALID_BASELINE_PRECONDITIONS",
        context="V5 baseline prerequisites",
    )
    if (
        value.get("version") != V5_VERSION
        or value.get("standalone_import") is not False
        or any(type(value.get(field)) is not int or value.get(field) != 0 for field in ("mapping_approvals", "current_price_approvals", "import_ready_rows"))
    ):
        raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "V5 baseline authority differs")
    sequence = _require_sequence(
        value.get("required_sequence"),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="required replay sequence must be an array",
    )
    if tuple(sequence) != (
        "Full exact V4 companion baseline",
        "Exact original V4.1 delta +17 base-locator overlay",
        "Nine V5 patches + new diagnostic sidecars",
    ):
        raise ReviewPackageError("INVALID_REPLAY_ORDER", "V5 replay sequence differs")
    original = _require_mapping(
        value.get("original_v4_companion_zip"),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="V4 baseline descriptor must be an object",
    )
    prior = _require_mapping(
        value.get("v4_1_delta_zip"),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="V4.1 descriptor must be an object",
    )
    for name, descriptor, expected_file, expected_bytes, expected_sha in (
        (
            "original_v4_companion_zip",
            original,
            "Buffalo_Mapping_Review_v4_Companion_Tables_and_Evidence.zip",
            388_502_348,
            "8696e12755280732577d474137aacfd480902703e5a6a8c8b440c34198a44602",
        ),
        (
            "v4_1_delta_zip",
            prior,
            "Buffalo_V4_1_Clarification_Delta.zip",
            1_063_507,
            "8e814872d1674c88412271434fcb0a81d475f104f60a6bf8ca757b578862cf39",
        ),
    ):
        if descriptor.get("file") != expected_file or descriptor.get("bytes") != expected_bytes:
            raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", f"{name} identity differs")
        if _require_sha256(descriptor.get("sha256"), path=name) != expected_sha:
            raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", f"{name} hash differs")
    baseline_files = _require_sequence(
        value.get("baseline_table_files"),
        code="INVALID_BASELINE_PRECONDITIONS",
        message="baseline table files must be an array",
    )
    if len(baseline_files) != 52:
        raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline table inventory differs")
    seen_paths: set[str] = set()
    for row_number, raw in enumerate(baseline_files, start=1):
        row = _require_mapping(
            raw, code="INVALID_BASELINE_PRECONDITIONS", message="baseline table record must be an object"
        )
        required = {"path", "file", "bytes", "sha256", "role", "hash_basis"}
        if not required.issubset(row):
            raise ReviewPackageError("INVALID_BASELINE_PRECONDITIONS", "baseline table record is incomplete")
        path = _validated_member_name(
            _nonblank_text(row.get("path"), code="INVALID_BASELINE_PRECONDITIONS", context="baseline path"),
            source.limits,
        )
        if path in seen_paths:
            raise ReviewPackageError("DUPLICATE_DECLARATION", "baseline table path is duplicated", path=path)
        seen_paths.add(path)
        _nonnegative_int(row.get("bytes"), code="INVALID_BASELINE_PRECONDITIONS", context="baseline bytes")
        _require_sha256(row.get("sha256"), path=path)
    return {
        "required_sequence": list(sequence),
        "baseline": {
            "file": original["file"], "bytes": original["bytes"], "sha256": original["sha256"]
        },
        "prior_delta": {
            "file": prior["file"], "bytes": prior["bytes"], "sha256": prior["sha256"]
        },
        "baseline_table_records": len(baseline_files),
    }


def _validate_locator_corrections(
    tables: Mapping[str, PackageTable],
    prior_source: _PackageSource | None,
) -> dict[str, dict[int, dict[str, Any]]]:
    rows = tables["v4_1_base_locator_corrections_v5"].rows
    if len(rows) != 17:
        raise ReviewPackageError("LOCATOR_CORRECTION_MISMATCH", "locator correction count differs")
    corrections: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    expected_counts = Counter({"candidate_projection_eligibility": 14, "owner_question_batch": 3})
    if Counter(row.get("table") for row in rows) != expected_counts:
        raise ReviewPackageError("LOCATOR_CORRECTION_MISMATCH", "locator correction table counts differ")
    prior_patches: dict[str, dict[int, Mapping[str, Any]]] = {}
    if prior_source is not None:
        for table_name in expected_counts:
            path = f"table_deltas/{table_name}.patch.jsonl"
            parsed, _ = _read_jsonl(prior_source, path)
            prior_patches[table_name] = {
                int(row["base_row_number_1based"]): row for row in parsed
            }
    for row in rows:
        table_name = row["table"]
        row_number = _nonnegative_int(
            row.get("base_row_number_1based"),
            code="LOCATOR_CORRECTION_MISMATCH",
            context="locator row",
        )
        if row_number <= 0 or row_number in corrections[table_name]:
            raise ReviewPackageError("LOCATOR_CORRECTION_MISMATCH", "locator row is invalid or duplicated")
        if (
            row.get("original_delta_unchanged") is not True
            or row.get("unique_base_row_count") != 1
            or row.get("corrected_base_stable_key") != row.get("safe_locator")
            or row.get("problem") != "KEY_DESCRIBES_POST_STATE_NOT_BASE"
            or row.get("action")
            != "USE_CORRECTED_BASE_KEY_PLUS_EXACT_ROW_AND_BEFORE_HASH; ORIGINAL_POST_STATE_OWNER_KEY_IS_NOT_BASE_LOCATOR"
        ):
            raise ReviewPackageError("LOCATOR_CORRECTION_MISMATCH", "locator safety controls differ")
        corrected = _require_mapping(
            row.get("corrected_base_stable_key"),
            code="LOCATOR_CORRECTION_MISMATCH",
            message="corrected key must be an object",
        )
        expected_key_fields = (
            {"variant_id", "source_offer_id"}
            if table_name == "candidate_projection_eligibility"
            else {"variant_id"}
        )
        if set(corrected) != expected_key_fields or any(
            not isinstance(value, str) or not value for value in corrected.values()
        ):
            raise ReviewPackageError("LOCATOR_CORRECTION_MISMATCH", "corrected locator fields differ")
        _require_sha256(row.get("before_record_sha256"), path="locator correction")
        _require_sha256(row.get("after_record_sha256"), path="locator correction")
        if prior_source is not None:
            patch = prior_patches.get(table_name, {}).get(row_number)
            if patch is None or any(
                patch.get(field) != row.get(field)
                for field in (
                    "table",
                    "base_row_number_1based",
                    "before_record_sha256",
                    "after_record_sha256",
                    "stable_key",
                )
            ):
                # The correction calls the original key original_stable_key.
                if patch is None or (
                    patch.get("table") != table_name
                    or patch.get("base_row_number_1based") != row_number
                    or patch.get("before_record_sha256") != row.get("before_record_sha256")
                    or patch.get("after_record_sha256") != row.get("after_record_sha256")
                    or patch.get("stable_key") != row.get("original_stable_key")
                ):
                    raise ReviewPackageError(
                        "LOCATOR_CORRECTION_MISMATCH",
                        "locator correction does not bind the original V4.1 patch",
                    )
        corrections[table_name][row_number] = dict(corrected)
    return {name: dict(values) for name, values in corrections.items()}


def _require_unique_text_values(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    *,
    table: str,
) -> set[str]:
    values: set[str] = set()
    for row_number, row in enumerate(rows, start=1):
        value = _nonblank_text(
            row.get(field), code="TABLE_KEY_MISSING", context=f"{table}.{field}"
        )
        if value in values:
            raise ReviewPackageError(
                "TABLE_KEY_NOT_UNIQUE", f"{field} is duplicated", table=table, row=row_number
            )
        values.add(value)
    return values


def _validate_alcohol_gift_components(row: Mapping[str, Any]) -> int:
    components = row.get("components")
    if not isinstance(components, list) or len(components) not in {2, 3}:
        raise ReviewPackageError(
            "JOIN_MISMATCH", "alcohol gift component list differs"
        )
    base_fields = {
        "description_raw",
        "quantity_per_gift_candidate",
        "role",
        "size_ml_candidate",
    }
    primary_roles = {
        "PRIMARY",
        "PRIMARY_IDENTITY_ABBREVIATED",
        "PRIMARY_COMPONENT_QUANTITY_SIZE_UNRESOLVED",
    }
    secondary_roles = {
        "ADDITIONAL_ALCOHOL_IDENTITY_UNRESOLVED",
        "ADDITIONAL_ALCOHOL_IDENTITY_AND_QUANTITY_UNRESOLVED",
        "ADDITIONAL_50ML_IDENTITY_AND_QUANTITY_UNRESOLVED",
        "SECONDARY_COMPONENT_QUANTITY_SIZE_UNRESOLVED",
        "UNRESOLVED_ABBREVIATION_CONTENT",
    }
    descriptions: set[str] = set()
    for index, component in enumerate(components):
        if not isinstance(component, Mapping) or frozenset(component) not in {
            frozenset(base_fields),
            frozenset(base_fields | {"contains_alcohol"}),
        }:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "alcohol gift component schema differs"
            )
        description = _nonblank_text(
            component.get("description_raw"),
            code="JOIN_MISMATCH",
            context="alcohol gift component description",
        )
        if description in descriptions:
            raise ReviewPackageError(
                "DUPLICATE_OCCURRENCE", "alcohol gift component is duplicated"
            )
        descriptions.add(description)
        quantity = component.get("quantity_per_gift_candidate")
        size = component.get("size_ml_candidate")
        if (
            (quantity is not None and (type(quantity) is not int or quantity <= 0))
            or (size is not None and (type(size) is not int or size <= 0))
            or (
                "contains_alcohol" in component
                and component.get("contains_alcohol") is not None
            )
            or component.get("role")
            not in (primary_roles if index == 0 else secondary_roles)
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "alcohol gift component facts differ"
            )

    case_pack = row.get("source_case_pack_raw_preserved")
    if type(case_pack) is not int or case_pack <= 0:
        raise ReviewPackageError(
            "JOIN_MISMATCH", "alcohol gift preserved case pack differs"
        )
    primary_quantity = components[0].get("quantity_per_gift_candidate")
    expected_primary = (
        case_pack * primary_quantity if type(primary_quantity) is int else None
    )
    secondary_quantities = [
        component.get("quantity_per_gift_candidate") for component in components[1:]
    ]
    expected_additional = (
        case_pack * sum(secondary_quantities)
        if all(type(value) is int for value in secondary_quantities)
        else None
    )
    expected_total = (
        expected_primary + expected_additional
        if expected_primary is not None and expected_additional is not None
        else None
    )
    if (
        row.get("primary_bottles_per_case_candidate") != expected_primary
        or row.get("additional_50ml_bottles_per_case_candidate")
        != expected_additional
        or row.get("total_physical_alcohol_containers_per_case_candidate")
        != expected_total
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "alcohol gift component arithmetic differs"
        )
    ambiguities = row.get("published_ambiguities")
    remaining = row.get("remaining_evidence_needed")
    if (
        not isinstance(ambiguities, list)
        or not ambiguities
        or any(not isinstance(value, str) or not value for value in ambiguities)
        or not isinstance(remaining, list)
        or len(remaining) != 3
        or any(
            not isinstance(value, Mapping)
            or set(value) != {"evidence", "who"}
            or any(not isinstance(item, str) or not item for item in value.values())
            for value in remaining
        )
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "alcohol gift ambiguity/evidence record differs"
        )
    return len(components)


def _validate_v5_gift_chains(
    relationship_gifts: Mapping[str, Mapping[str, Any]],
    gift_rows: Sequence[Mapping[str, Any]],
    alcohol_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Bind gift sidecar IDs and alcohol-component reviews to exact occurrences."""

    conditional_by_id: dict[str, Mapping[str, Any]] = {}
    conditional_by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
    relationship_fields = (
        ("source_supplier_code", "supplier_code_exact"),
        ("source_description_raw", "source_description_raw"),
        ("source_case_pack_raw", "source_case_pack_raw"),
        ("source_file", "source_file"),
        ("source_page", "source_page"),
        ("printed_page", "printed_page"),
        ("source_sha256", "source_sha256"),
        ("source_period_raw", "source_period_raw"),
        ("source_territory_raw", "source_territory"),
        ("source_size_raw", "source_size_raw"),
        ("source_fee_basis", "source_split_fee_basis"),
        ("source_split_permission", "source_split_availability"),
        ("complete_source_tier_ids", "source_tier_ids"),
        ("preference", "preference"),
    )
    for row in gift_rows:
        identifier = _nonblank_text(
            row.get("relationship_id"),
            code="JOIN_MISMATCH",
            context="conditional gift relationship ID",
        )
        pair = (
            _nonblank_text(
                row.get("variant_id"), code="JOIN_MISMATCH", context="gift Variant ID"
            ),
            _nonblank_text(
                row.get("source_offer_id"),
                code="JOIN_MISMATCH",
                context="gift source offer ID",
            ),
        )
        if identifier in conditional_by_id or pair in conditional_by_pair:
            raise ReviewPackageError(
                "DUPLICATE_OCCURRENCE", "conditional gift identity is duplicated"
            )
        relationship = relationship_gifts.get(identifier)
        relationship_pair = (
            relationship.get("variant_id"),
            relationship.get("source_offer_id"),
        ) if relationship is not None else None
        if (
            relationship_pair != pair
            or any(
                row.get(gift_field) != relationship.get(relationship_field)
                for gift_field, relationship_field in relationship_fields
            )
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "conditional gift ID or source facts resolve to another occurrence",
            )
        if (
            row.get("whole_offer_to_single_variant_allowed") is not False
            or row.get("may_influence_normalized_price_projection") is not False
            or row.get("explicit_null_precedence") is not True
            or any(
                row.get(field) is not None
                for field in (
                    "allocated_component_cost",
                    "order_increment",
                    "qualifying_units_per_case",
                    "shopify_units_per_case",
                )
            )
        ):
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "conditional gift cannot map a whole offer or affect normalized pricing",
            )
        conditional_by_id[identifier] = row
        conditional_by_pair[pair] = row
    if set(conditional_by_id) != set(relationship_gifts):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "conditional gifts do not match occurrence sidecars"
        )

    alcohol_pairs: set[tuple[str, str]] = set()
    component_counts: Counter[int] = Counter()
    known_physical_totals = 0
    for row in alcohol_rows:
        pair = (
            _nonblank_text(
                row.get("variant_id"),
                code="JOIN_MISMATCH",
                context="alcohol-gift Variant ID",
            ),
            _nonblank_text(
                row.get("source_offer_id"),
                code="JOIN_MISMATCH",
                context="alcohol-gift source offer ID",
            ),
        )
        conditional = conditional_by_pair.get(pair)
        relationship = (
            relationship_gifts.get(str(conditional.get("relationship_id")))
            if conditional is not None
            else None
        )
        root_references = (
            conditional.get("root_review_references") if conditional is not None else None
        )
        source = row.get("source")
        component_counts[_validate_alcohol_gift_components(row)] += 1
        if row.get("total_physical_alcohol_containers_per_case_candidate") is not None:
            known_physical_totals += 1
        if (
            pair in alcohol_pairs
            or conditional is None
            or relationship is None
            or not isinstance(root_references, list)
            or not isinstance(source, Mapping)
            or row.get("root_relationship_review_id") not in root_references
            or row.get("challenge_review_id")
            != conditional.get("specialist_challenge_id")
            or row.get("root_reviewed_at_utc")
            != conditional.get("root_reviewed_at_utc")
            or source.get("source_offer_id") != pair[1]
            or source.get("supplier_sku") != conditional.get("source_supplier_code")
            or source.get("source_file") != conditional.get("source_file")
            or source.get("source_page") != conditional.get("source_page")
            or source.get("source_sha256") != conditional.get("source_sha256")
            or row.get("supplier_code_exact")
            != conditional.get("source_supplier_code")
            or row.get("source_case_pack_raw_preserved")
            != conditional.get("source_case_pack_raw")
            or row.get("source_physical_units_per_case_raw_preserved")
            != relationship.get("source_physical_count_raw")
            or row.get("whole_gift_to_single_variant_mapping_allowed") is not False
            or row.get("may_influence_normalized_price_projection") is not False
            or row.get("explicit_null_precedence")
            != (
                "REVIEWED_NULL: no fallback to original normal-bottle conversion "
                "estimates or generic product-level metadata"
            )
            or row.get("retail_packaging_acceptance") != "NOT_APPROVED"
            or any(
                row.get(field) is not None
                for field in (
                    "allocated_component_cost",
                    "component_qualifying_units_per_case",
                    "component_shopify_units_per_case",
                    "quantity_order_increment",
                    "shopify_units_per_case",
                    "split_fee_applied",
                    "supplier_qualifying_units_per_case",
                )
            )
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "alcohol gift component does not bind to its conditional relationship",
            )
        alcohol_pairs.add(pair)
    challenged_conditional_pairs = {
        pair
        for pair, row in conditional_by_pair.items()
        if row.get("specialist_challenge_id") is not None
    }
    if alcohol_pairs != challenged_conditional_pairs:
        raise ReviewPackageError(
            "JOIN_MISMATCH",
            "alcohol specialist rows do not exactly cover challenged gifts",
        )
    if len(alcohol_rows) == 8 and (
        component_counts != Counter({2: 7, 3: 1})
        or known_physical_totals != 4
    ):
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "alcohol gift component controls differ"
        )


def _validate_v5_normalized_contract_row(
    row: Mapping[str, Any],
    *,
    row_number: int,
    allow_null_supplier_sku: bool = False,
    allow_null_supplier_description: bool = False,
) -> None:
    """Validate V5's exact 27 fields while preserving explicitly reviewed nulls."""

    if set(row) != set(PRICE_BOOK_HEADERS):
        raise ReviewPackageError(
            "NORMALIZED_CONTRACT_MISMATCH",
            "V5 normalized row must contain exactly the 27 contract fields",
            row=row_number,
        )
    required_text = {
        "batch_ref", "vendor_name", "supplier_sku", "supplier_description",
        "source_file", "source_evidence",
        "extraction_confidence", "review_note",
    }
    optional_text = {
        "canonical_variant_id", "target_price_state", "effective_from", "effective_through",
        "package_type", "size_text", "level_type",
        "raw_pack", "assortment_scope", "assortment_group", "assortment_evidence", "break_unit",
    }
    if allow_null_supplier_sku:
        required_text.remove("supplier_sku")
        optional_text.add("supplier_sku")
    if allow_null_supplier_description:
        required_text.remove("supplier_description")
        optional_text.add("supplier_description")
    for field in required_text:
        if not isinstance(row[field], str) or not row[field].strip():
            raise ReviewPackageError("NORMALIZED_TYPE_MISMATCH", f"{field} must be nonblank text", row=row_number)
    for field in optional_text:
        if row[field] is not None and not isinstance(row[field], str):
            raise ReviewPackageError("NORMALIZED_TYPE_MISMATCH", f"{field} must be text or null", row=row_number)
    for field in {"case_price", "unit_price", "shopify_units_per_case", "qualifying_units_per_case", "break_quantity"}:
        value = row[field]
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ReviewPackageError("NORMALIZED_TYPE_MISMATCH", f"{field} must be an exact number or allowed null", row=row_number)
        number = Decimal(value)
        if not number.is_finite() or number <= 0:
            raise ReviewPackageError("NORMALIZED_NUMBER_MISMATCH", f"{field} has invalid value", row=row_number)
        if field in {"shopify_units_per_case", "qualifying_units_per_case", "break_quantity"} and number != number.to_integral_value():
            raise ReviewPackageError("NORMALIZED_NUMBER_MISMATCH", f"{field} must be whole when present", row=row_number)
    if row["target_price_state"] is not None:
        raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "V5 target price state must remain null", row=row_number)
    for field in ("effective_from", "effective_through"):
        if row[field] is not None:
            try:
                date.fromisoformat(row[field])
            except ValueError as exc:
                raise ReviewPackageError("NORMALIZED_DATE_MISMATCH", f"{field} must be an ISO date or null", row=row_number) from exc
    if row["assortable"] is not None and type(row["assortable"]) is not bool:
        raise ReviewPackageError("NORMALIZED_TYPE_MISMATCH", "assortable must be boolean or null", row=row_number)
    if type(row["source_page"]) is not int or row["source_page"] <= 0:
        raise ReviewPackageError("NORMALIZED_TYPE_MISMATCH", "source_page must be a positive integer", row=row_number)
    if row["extraction_confidence"] != "UNAPPROVED_CANDIDATE":
        raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "V5 extraction confidence must remain unapproved", row=row_number)


_V5_FALSE_AUTHORITY_FIELDS = frozenset(
    {
            "approval_by_same_model_review",
            "approved",
            "approved_alias",
            "approved_alias_created",
            "approved_identity_alias",
            "auto_add_authorized",
            "buying_authority_granted",
            "combo_auto_add",
            "cross_variant_or_all_supplier_generalization_allowed",
            "current_approval_claim",
            "current_price_activated",
            "future_price_activated",
            "historical_sales_transfer_allowed",
            "import_ready",
            "mapping_approved",
            "mapping_or_price_approval",
            "may_influence_normalized_price_projection",
            "new_mapping_approval",
            "new_mapping_approved",
            "pack_breaking_authorized",
            "price_approved",
            "price_ladder_eligible",
            "price_ladder_selected",
            "price_or_mapping_approval",
            "projection_authority",
            "quantity_import_eligible",
            "quantity_or_import_eligible",
            "root_mapping_approved",
            "root_mapping_or_price_approval",
            "root_price_approved",
            "rule_scope_approved",
            "routine_purchase_candidate",
            "source_price_activation",
            "tier_selected",
            "verified_current_price",
            "v4_normalized_alcohol_import_allowed",
            "whole_combo_maps_to_single_variant",
            "whole_combo_to_variant_mapping_allowed",
            "whole_gift_to_single_variant_mapping_allowed",
            "whole_offer_to_single_variant_allowed",
            "whole_package_breaking_authorized",
            "wholesale_price_authority",
            "wholesale_price_or_account_authority",
    }
)

_V5_ZERO_AUTHORITY_FIELDS = frozenset(
    {
        "application_changes",
        "canonical_mutations",
        "operational_database_access",
        "questions_sent",
        "root_mutations",
        "shopify_writes",
        "supplier_contacts",
    }
)


def _validate_v5_row_authority(
    row: Mapping[str, Any], *, table_name: str, row_number: int
) -> None:
    """Reject operational authority in one effective V5 review row."""

    for field in _V5_FALSE_AUTHORITY_FIELDS:
        value = row.get(field)
        count_control = (
            table_name == "supplier_coverage_controls_v5"
            and field in {"mapping_approved", "price_approved"}
            and type(value) is int
            and value == 0
        )
        if field in row and value is not False and not count_control:
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                f"{field} must remain false",
                table=table_name,
                row=row_number,
            )
    for field in _V5_ZERO_AUTHORITY_FIELDS:
        if field in row and (type(row[field]) is not int or row[field] != 0):
            raise ReviewPackageError(
                "UNAUTHORIZED_OPERATIONAL_EFFECT",
                f"{field} must remain exact integer zero",
                table=table_name,
                row=row_number,
            )
    if (
        table_name == "targeted_candidate_after_records"
        and row.get("approval_status") != "UNAPPROVED_CANDIDATE"
    ):
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "candidate approval status must remain unapproved",
            table=table_name,
            row=row_number,
        )
    applicable_tier = row.get("applicable_tier_selected")
    if (
        "applicable_tier_selected" in row
        and applicable_tier is not None
        and applicable_tier is not False
    ):
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "applicable_tier_selected must remain false or explicit null",
            table=table_name,
            row=row_number,
        )
    if table_name == "supplier_vocabulary_candidates_v5" and row.get(
        "approved_vendor_uuid_crosswalk"
    ) is not None:
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "approved vendor UUID crosswalk must remain explicit null",
            table=table_name,
            row=row_number,
        )
    if (
        table_name == "variant_offer_relationships_v5"
        and row.get("tier_selection") is not None
    ):
        raise ReviewPackageError(
            "UNAUTHORIZED_APPROVAL_CLAIM",
            "relationship tier_selection must remain explicit null",
            table=table_name,
            row=row_number,
        )


def _validate_v5_owner_preference_contract(
    family_rows: Sequence[Mapping[str, Any]],
    relationship_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind private owner choices without copying those choices into public code."""

    family_by_id: dict[str, Mapping[str, Any]] = {}
    owner_scoped_family_ids: set[str] = set()
    referenced_decision_ids: set[str] = set()
    for family in family_rows:
        family_id = _nonblank_text(
            family.get("family_id"),
            code="JOIN_MISMATCH",
            context="owner-preference family ID",
        )
        decision_ids = family.get("owner_preference_decision_ids")
        if (
            not isinstance(decision_ids, list)
            or len(decision_ids) > 1
            or len(decision_ids) != len(set(decision_ids))
            or any(not isinstance(value, str) or not value for value in decision_ids)
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "family owner-preference decision references differ"
            )
        preference = _nonblank_text(
            family.get("preference"),
            code="JOIN_MISMATCH",
            context="family owner preference",
        )
        if decision_ids:
            if preference in {
                _V5_UNDECIDED_PREFERENCE,
                _V5_NONPREFERRED_CONFIGURATION,
            }:
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "scoped family owner preference is not preserved"
                )
            owner_scoped_family_ids.add(family_id)
            referenced_decision_ids.update(decision_ids)
        elif preference != _V5_UNDECIDED_PREFERENCE:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "unscoped family gained an owner preference"
            )
        family_by_id[family_id] = family

    owner_scoped_relationships = 0
    family_preference_occurrences = 0
    nonpreferred_occurrences = 0
    matched_scoped_families: set[str] = set()
    for row in relationship_rows:
        family_id = _nonblank_text(
            row.get("family_id"),
            code="JOIN_MISMATCH",
            context="relationship owner-preference family ID",
        )
        family = family_by_id.get(family_id)
        if family is None:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "relationship owner preference has no family"
            )
        decision_ids = row.get("owner_preference_decision_ids")
        if (
            not isinstance(decision_ids, list)
            or decision_ids != family.get("owner_preference_decision_ids")
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "relationship owner decision references differ from family"
            )
        if (
            row.get("conversion_authority") != _V5_CONVERSION_AUTHORITY
            or row.get("no_code_replacement_inference") is not True
            or row.get("reason_precedence") != _V5_REASON_PRECEDENCE
        ):
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "relationship negative-authority contract differs",
            )
        preference = _nonblank_text(
            row.get("preference"),
            code="JOIN_MISMATCH",
            context="relationship owner preference",
        )
        family_preference = str(family.get("preference"))
        if decision_ids:
            owner_scoped_relationships += 1
            if preference == family_preference:
                family_preference_occurrences += 1
                matched_scoped_families.add(family_id)
            elif preference == _V5_NONPREFERRED_CONFIGURATION:
                nonpreferred_occurrences += 1
            else:
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "relationship owner preference differs from family scope"
                )
        elif preference != _V5_UNDECIDED_PREFERENCE:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "unscoped relationship gained an owner preference"
            )
    if matched_scoped_families != owner_scoped_family_ids:
        raise ReviewPackageError(
            "JOIN_MISMATCH", "family owner preference has no matching occurrence"
        )
    return {
        "owner_scoped_families": len(owner_scoped_family_ids),
        "owner_scoped_relationships": owner_scoped_relationships,
        "family_preference_occurrences": family_preference_occurrences,
        "nonpreferred_occurrences": nonpreferred_occurrences,
        "referenced_owner_decisions": len(referenced_decision_ids),
    }


def _validate_fixed_combo_provisional_units(row: Mapping[str, Any]) -> str:
    conversion_basis = row.get("provisional_component_conversion_basis")
    provisional_units = row.get("provisional_component_shopify_units")
    if conversion_basis == (
        "Printed component quantity times1 individually sellable standard bottle; "
        "pending component-page check, not supplier BT qualification"
    ) and provisional_units == row.get("component_quantity_raw"):
        return "quantity_preserved"
    if conversion_basis == (
        "REVIEWED_NULL: component pack-or-bottle meaning/nested selling-unit "
        "conversion unresolved; do not fall back to source case pack"
    ) and provisional_units is None:
        return "reviewed_null"
    raise ReviewPackageError(
        "JOIN_MISMATCH", "fixed combo provisional conversion differs"
    )


def _validate_v5_relationships_and_authority(
    tables: Mapping[str, PackageTable],
    patches: Mapping[str, Sequence[Mapping[str, Any]]] | None,
) -> dict[str, Any]:
    for table_name, table in tables.items():
        if table_name.startswith("v5_patch_") or table_name in {"v5_table_catalog", "v5_field_profiles"}:
            continue
        for row_number, row in enumerate(table.rows, start=1):
            _validate_v5_row_authority(
                row, table_name=table_name, row_number=row_number
            )

    catalog_rows = tables["catalog_coverage_v5"].rows
    catalog_variants = _require_unique_text_values(
        catalog_rows, "variant_id", table="catalog_coverage_v5"
    )
    catalog_statuses = Counter(row.get("status") for row in catalog_rows)
    if dict(catalog_statuses) != _V5_CONTROL_TOTALS["v5_catalog_statuses"]:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "catalog status counts differ from V5 controls")
    catalog_by_variant = {str(row["variant_id"]): row for row in catalog_rows}
    variant_gid_prefix = "gid://shopify/ProductVariant/"
    if any(
        row.get("variant_gid") != f"{variant_gid_prefix}{row['variant_id']}"
        for row in catalog_rows
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "V5 catalog Variant GID projection differs"
        )

    family_rows = tables["multi_offer_families_v5"].rows
    family_variants = _require_unique_text_values(
        family_rows, "variant_id", table="multi_offer_families_v5"
    )
    if family_variants != catalog_variants:
        raise ReviewPackageError("JOIN_MISMATCH", "V5 families do not cover the exact catalog cohort")
    family_by_id = {
        _nonblank_text(row.get("family_id"), code="TABLE_KEY_MISSING", context="family_id"): row
        for row in family_rows
    }
    if len(family_by_id) != len(family_rows):
        raise ReviewPackageError("TABLE_KEY_NOT_UNIQUE", "family_id is duplicated")
    family_catalog_fields = (
        ("product_id", "product_id"),
        ("product_title_raw", "product_title"),
        ("variant_title_raw", "variant_title"),
        ("catalog_source_status", "status"),
        ("display_status", "v5_display_status"),
    )
    for family in family_rows:
        catalog = catalog_by_variant[str(family["variant_id"])]
        if any(
            family.get(family_field) != catalog.get(catalog_field)
            for family_field, catalog_field in family_catalog_fields
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "V5 family catalog identity projection differs"
            )

    relationship_rows = tables["variant_offer_relationships_v5"].rows
    owner_preference_controls = _validate_v5_owner_preference_contract(
        family_rows, relationship_rows
    )
    if owner_preference_controls != {
        "owner_scoped_families": 2,
        "owner_scoped_relationships": 17,
        "family_preference_occurrences": 6,
        "nonpreferred_occurrences": 11,
        "referenced_owner_decisions": 2,
    }:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "V5 owner-preference controls differ"
        )
    relationship_ids = _require_unique_text_values(
        relationship_rows, "relationship_id", table="variant_offer_relationships_v5"
    )
    relationship_keys: set[tuple[str, str, str]] = set()
    source_offer_by_variant: set[tuple[str, str]] = set()
    tiers_by_occurrence: dict[tuple[str, str], set[str]] = {}
    occurrence_counts: Counter[str] = Counter()
    prior_pair_counts: Counter[bool] = Counter()
    source_hashes: set[str] = set()
    relationship_gifts: dict[str, Mapping[str, Any]] = {}
    for row_number, row in enumerate(relationship_rows, start=1):
        variant = _nonblank_text(row.get("variant_id"), code="JOIN_MISMATCH", context="relationship variant")
        family = _nonblank_text(row.get("family_id"), code="JOIN_MISMATCH", context="relationship family")
        supplier = _nonblank_text(
            row.get("supplier_name_raw"), code="JOIN_MISMATCH", context="relationship supplier"
        )
        offer = _nonblank_text(row.get("source_offer_id"), code="JOIN_MISMATCH", context="relationship offer")
        if variant not in catalog_variants or family not in family_by_id:
            raise ReviewPackageError("JOIN_MISMATCH", "relationship does not resolve to catalog/family")
        family_row = family_by_id[family]
        catalog_row = catalog_by_variant[variant]
        if (
            family_row.get("variant_id") != variant
            or row.get("product_id") != family_row.get("product_id")
            or row.get("catalog_source_status")
            != family_row.get("catalog_source_status")
            or row.get("product_id") != catalog_row.get("product_id")
            or row.get("catalog_source_status") != catalog_row.get("status")
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "relationship catalog/family identity projection differs"
            )
        key = (variant, supplier, offer)
        if key in relationship_keys:
            raise ReviewPackageError("DUPLICATE_OCCURRENCE", "V5 source occurrence is duplicated")
        relationship_keys.add(key)
        if (variant, offer) in source_offer_by_variant:
            raise ReviewPackageError("DUPLICATE_OCCURRENCE", "Variant/source-offer pair is duplicated")
        source_offer_by_variant.add((variant, offer))
        tier_values = _require_sequence(
            row.get("source_tier_ids"),
            code="TIER_IDENTITY_MISMATCH",
            message="relationship Tier IDs must be an array",
        )
        tiers_by_occurrence[(variant, offer)] = {
            _nonblank_text(value, code="TIER_IDENTITY_MISMATCH", context="relationship Tier ID")
            for value in tier_values
        }
        occurrence_counts[variant] += 1
        prior_pair = row.get("candidate_pair_existed_before_family_pass")
        if type(prior_pair) is not bool:
            raise ReviewPackageError("V5_SCHEMA_VERSION_MISMATCH", "prior candidate-pair marker must be boolean")
        prior_pair_counts[prior_pair] += 1
        source_hashes.add(_require_sha256(row.get("source_sha256"), path=f"relationship[{row_number}]"))
        gift = row.get("new_gift_sidecar_id")
        if gift is not None:
            gift_id = _nonblank_text(
                gift, code="JOIN_MISMATCH", context="gift relationship ID"
            )
            if gift_id in relationship_gifts:
                raise ReviewPackageError(
                    "DUPLICATE_OCCURRENCE", "gift relationship ID is duplicated"
                )
            relationship_gifts[gift_id] = row
    if len(relationship_ids) != 14_812 or len({row["variant_id"] for row in relationship_rows}) != 1_886:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "V5 relationship counts differ")
    if prior_pair_counts != Counter({True: 14_760, False: 52}):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "prior/new relationship counts differ")
    if any(row.get("candidate_occurrences") != occurrence_counts[row["variant_id"]] for row in family_rows):
        raise ReviewPackageError("JOIN_MISMATCH", "family occurrence count differs from relationship table")

    gift_rows = tables["conditional_gift_relationships_v5"].rows
    alcohol_gift_rows = tables["alcohol_gift_components_v5"].rows
    _validate_v5_gift_chains(relationship_gifts, gift_rows, alcohol_gift_rows)

    fixed_relationship_ids: set[str] = set()
    fixed_component_ids: set[str] = set()
    fixed_provisional_units: Counter[str] = Counter()
    for row in tables["fixed_combo_component_relationships_v5"].rows:
        variant = row.get("variant_id")
        relationship_id = _nonblank_text(
            row.get("component_relationship_id"),
            code="JOIN_MISMATCH",
            context="fixed combo relationship ID",
        )
        component_id = _nonblank_text(
            row.get("source_component_id"),
            code="JOIN_MISMATCH",
            context="fixed combo source component ID",
        )
        anchors = _require_sequence(
            row.get("source_anchor_offer_ids"),
            code="JOIN_MISMATCH",
            message="fixed-combo anchors must be an array",
        )
        if not anchors or any((variant, anchor) not in source_offer_by_variant for anchor in anchors):
            raise ReviewPackageError("JOIN_MISMATCH", "fixed-combo component anchor is unresolved")
        if relationship_id in fixed_relationship_ids or component_id in fixed_component_ids:
            raise ReviewPackageError(
                "DUPLICATE_OCCURRENCE", "fixed combo component identity is duplicated"
            )
        if (
            row.get("whole_combo_to_variant_mapping_allowed") is not False
            or row.get("combo_auto_add") is not False
            or row.get("manual_visual_review_claimed") is not False
            or row.get("allocated_component_cost") is not None
            or row.get("supplier_qualifying_units_for_component") is not None
        ):
            raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "fixed combo cannot auto-map or auto-add")
        fixed_provisional_units[
            _validate_fixed_combo_provisional_units(row)
        ] += 1
        fixed_relationship_ids.add(relationship_id)
        fixed_component_ids.add(component_id)
    if len(fixed_relationship_ids) == 249 and fixed_provisional_units != Counter(
        {"quantity_preserved": 245, "reviewed_null": 4}
    ):
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "fixed combo provisional conversion controls differ"
        )

    registry_rows = tables["source_evidence_registry_v5"].rows
    registry_hashes: set[str] = set()
    for row in registry_rows:
        if row.get("new_price_authority") is not False:
            raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "source registry authority differs")
        if "actual_byte_sha256" in row:
            registry_hashes.add(_require_sha256(row.get("actual_byte_sha256"), path="source registry"))
        if "hash_matches_source" in row and row.get("hash_matches_source") is not True:
            raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "source registry hash status differs")
    if not source_hashes.issubset(registry_hashes):
        raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "relationship source hash is absent from registry")

    dependency_rows = tables["unresolved_dependencies_v5"].rows
    dependency_by_id = {row["dependency_id"]: row for row in dependency_rows}
    dependency_ids = _require_unique_text_values(
        dependency_rows, "dependency_id", table="unresolved_dependencies_v5"
    )
    if any(str(row.get("variant_id")) not in catalog_variants for row in dependency_rows):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "V5 dependency references a Variant outside the catalog"
        )
    member_rows = tables["dependency_group_members_v5"].rows
    grouped_ids = _require_unique_text_values(
        member_rows, "dependency_id", table="dependency_group_members_v5"
    )
    ungrouped_rows = tables["ungrouped_historical_resolved_dependencies_v5"].rows
    ungrouped_ids = _require_unique_text_values(
        ungrouped_rows,
        "dependency_id",
        table="ungrouped_historical_resolved_dependencies_v5",
    )
    group_rows = tables["dependency_evidence_groups_v5"].rows
    group_ids = _require_unique_text_values(
        group_rows, "group_id", table="dependency_evidence_groups_v5"
    )
    if grouped_ids.isdisjoint(ungrouped_ids) is False or grouped_ids | ungrouped_ids != dependency_ids:
        raise ReviewPackageError("JOIN_MISMATCH", "grouped and historical dependencies do not cover V5 dependencies")
    if any(row.get("group_id") not in group_ids for row in member_rows):
        raise ReviewPackageError("JOIN_MISMATCH", "dependency group member references an unknown group")
    members_by_group: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for member in member_rows:
        authoritative = dependency_by_id[member["dependency_id"]]
        dependency_projection = (
            ("variant_id", "variant_id"),
            ("resolved", "resolved"),
            ("source_status", "status"),
            ("dependency_code_or_raw_text", "dependency"),
        )
        if any(
            member.get(member_field) != authoritative.get(source_field)
            for member_field, source_field in dependency_projection
        ) or member.get("no_owner_contact_sent") is not True:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "dependency member differs from its authoritative dependency"
            )
        for field in ("exact_evidence_needed", "blocks_candidate_identity"):
            if field in authoritative and member.get(field) != authoritative.get(field):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", f"dependency member {field} differs from authority"
                )
        members_by_group[member["group_id"]].append(member)
    ungrouped_projection_fields = frozenset(
        {
            "dependency",
            "dependency_id",
            "evidence_provider",
            "investigation_attempted",
            "provider_routing_basis",
            "resolution_evidence",
            "resolution_owner_decision_id",
            "resolved",
            "review_id",
            "source_reference",
            "status",
            "variant_id",
        }
    )
    for row in ungrouped_rows:
        authoritative = dependency_by_id[row["dependency_id"]]
        if set(row) != set(ungrouped_projection_fields) or any(
            field not in authoritative or row[field] != authoritative[field]
            for field in ungrouped_projection_fields
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "ungrouped historical dependency differs from its authoritative record",
            )
    for group in group_rows:
        members = members_by_group[group["group_id"]]
        active = [
            row for row in members
            if row.get("resolved") is False and row.get("historical_scope_only") is False
        ]
        if (
            group.get("no_approval") is not True
            or group.get("routing_recommendation_not_contact") is not True
        ):
            raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "dependency group claims approval")
        expected_values = {
            "baseline_or_created_dependency_ids": [row["dependency_id"] for row in members],
            "active_mapping_dependency_ids": [row["dependency_id"] for row in active],
            "active_variant_ids": sorted({str(row["variant_id"]) for row in active}),
            "all_member_count": len(members),
            "active_mapping_dependency_count": len(active),
            "active_unique_variant_count": len({row["variant_id"] for row in active}),
            "resolved_member_count": sum(row.get("resolved") is True for row in members),
            "historical_scope_only_count": sum(
                row.get("historical_scope_only") is True for row in members
            ),
        }
        if any(group.get(field) != value for field, value in expected_values.items()):
            raise ReviewPackageError("JOIN_MISMATCH", "dependency group controls differ from members")
    active_members = [
        row for row in member_rows if row.get("resolved") is False and row.get("historical_scope_only") is False
    ]
    if len(active_members) != 714 or len({row.get("variant_id") for row in active_members}) != 475:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "active dependency controls differ")

    adjudication_rows = tables["targeted_identity_adjudications"].rows
    expected_target_pairs: list[tuple[str, str]] = []
    supported_pairs: set[tuple[str, str]] = set()
    for adjudication in adjudication_rows:
        variant = _nonblank_text(
            adjudication.get("variant_id"), code="JOIN_MISMATCH", context="adjudication Variant ID"
        )
        for offer in _require_sequence(
            adjudication.get("source_offer_ids"),
            code="JOIN_MISMATCH",
            message="adjudication source offers must be an array",
        ):
            expected_target_pairs.append(
                (variant, _nonblank_text(offer, code="JOIN_MISMATCH", context="adjudication offer"))
            )
        for offer in _require_sequence(
            adjudication.get("supported_proposed_offer_ids"),
            code="JOIN_MISMATCH",
            message="supported source offers must be an array",
        ):
            supported_pairs.add(
                (variant, _nonblank_text(offer, code="JOIN_MISMATCH", context="supported offer"))
            )
    targeted_relationships = tables["targeted_variant_offer_relationships"].rows
    targeted_pairs = [
        (
            _nonblank_text(row.get("variant_id"), code="JOIN_MISMATCH", context="target Variant ID"),
            _nonblank_text(row.get("source_offer_id"), code="JOIN_MISMATCH", context="target offer"),
        )
        for row in targeted_relationships
    ]
    targeted_ids = _require_unique_text_values(
        targeted_relationships,
        "relationship_id",
        table="targeted_variant_offer_relationships",
    )
    if (
        len(targeted_pairs) != 190
        or len(set(targeted_pairs)) != 190
        or len(targeted_ids) != 190
        or set(targeted_pairs) != set(expected_target_pairs)
        or len(expected_target_pairs) != len(set(expected_target_pairs))
    ):
        raise ReviewPackageError("JOIN_MISMATCH", "targeted relationship/adjudication coverage differs")
    joined_target_pairs = set(targeted_pairs) & source_offer_by_variant
    if len(joined_target_pairs) != 180 or len(set(targeted_pairs) - source_offer_by_variant) != 10:
        raise ReviewPackageError("JOIN_MISMATCH", "targeted relationship V5 join controls differ")
    targeted_by_pair = {pair: row for pair, row in zip(targeted_pairs, targeted_relationships)}
    if (
        len(supported_pairs) != 52
        or not supported_pairs.issubset(source_offer_by_variant)
        or any(
            targeted_by_pair[pair].get("candidate_disposition") != "PROPOSED_REVIEW_CANDIDATE"
            for pair in supported_pairs
        )
    ):
        raise ReviewPackageError("JOIN_MISMATCH", "supported targeted relationships differ")

    for row in tables["rejected_match_memory_v5"].rows:
        if (row.get("variant_id"), row.get("source_offer_id")) not in source_offer_by_variant:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "rejected_match_memory_v5 occurrence is unresolved"
            )

    # The complete A1 snapshot carries the exact effective V5 tables and
    # sidecars, but deliberately omits the old V4 baseline/patch files.  Its
    # dedicated raw-hash adapter can therefore reuse every snapshot join above
    # while keeping original historical patch replay separately unavailable.
    if patches is None:
        return {
            "authority_zeroes": "PASS",
            "catalog_family_join": "PASS",
            "occurrence_relationships": len(relationship_rows),
            "conditional_gifts": len(gift_rows),
            "fixed_combo_components": len(tables["fixed_combo_component_relationships_v5"].rows),
            "source_registry_hash_join": "PASS",
            "dependencies": len(dependency_rows),
            "active_mapping_dependencies": len(active_members),
            "targeted_normalized_rows": len(tables["targeted_normalized_contract_rows"].rows),
            "targeted_patch_projections": "UNAVAILABLE_EXACT_V4_BASELINE_REQUIRED",
            "owner_preferences": owner_preference_controls,
        }

    normalized_rows = tables["targeted_normalized_contract_rows"].rows
    normalized_patch_by_row = {
        int(row["base_row_number_1based"]): row
        for row in patches["normalized_price_contract_review"]
    }
    link_patch_by_row = {
        int(row["base_row_number_1based"]): row
        for row in patches["normalized_price_contract_links"]
    }
    seen_normalized_rows: set[int] = set()
    seen_tiers: set[str] = set()
    for row in normalized_rows:
        row_number = _nonnegative_int(
            row.get("row_number_1based"), code="NORMALIZED_POSITION_MISMATCH", context="normalized row"
        )
        if row_number <= 0 or row_number in seen_normalized_rows:
            raise ReviewPackageError("NORMALIZED_POSITION_MISMATCH", "normalized row is duplicated")
        seen_normalized_rows.add(row_number)
        tier = _nonblank_text(row.get("source_tier_id"), code="TIER_IDENTITY_MISMATCH", context="Tier ID")
        offer = _nonblank_text(row.get("source_offer_id"), code="TIER_IDENTITY_MISMATCH", context="source offer")
        if tier in seen_tiers:
            raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "targeted Tier ID is duplicated")
        seen_tiers.add(tier)
        contract_row = _require_mapping(
            row.get("contract_row"), code="V5_SCHEMA_VERSION_MISMATCH", message="contract_row must be an object"
        )
        _validate_v5_normalized_contract_row(contract_row, row_number=row_number)
        patch = normalized_patch_by_row.get(row_number)
        link_patch = link_patch_by_row.get(row_number)
        if patch is None or link_patch is None:
            raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "targeted normalized row lacks both patches")
        if canonical_record_sha256(contract_row) != patch.get("after_record_sha256"):
            raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "targeted contract row differs from patch result")
        if link_patch.get("stable_key") != {"projection_row_id": tier}:
            raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "normalized link patch Tier identity differs")
        variant_id = contract_row.get("canonical_variant_id")
        if (
            variant_id is not None
            and (
                (variant_id, offer) not in source_offer_by_variant
                or tier not in tiers_by_occurrence[(variant_id, offer)]
            )
        ) or (
            variant_id is None
            and not any(
                candidate_offer == offer and tier in tiers_by_occurrence[(candidate_variant, candidate_offer)]
                for candidate_variant, candidate_offer in source_offer_by_variant
            )
        ):
            raise ReviewPackageError("JOIN_MISMATCH", "targeted normalized source offer is absent")
    if set(normalized_patch_by_row) != seen_normalized_rows or set(link_patch_by_row) != seen_normalized_rows:
        raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "targeted normalized rows do not cover both patch sets")

    target_after_rows = tables["targeted_candidate_after_records"].rows
    candidate_patch_by_key = {
        (row["stable_key"]["variant_id"], row["stable_key"]["offer_id"]): row
        for row in patches["candidate_matches"]
    }
    projection_keys = {
        (row["stable_key"]["variant_id"], row["stable_key"]["source_offer_id"])
        for row in patches["candidate_projection_eligibility"]
    }
    target_by_key = {
        (row.get("variant_id"), row.get("offer_id")): row for row in target_after_rows
    }
    if len(target_by_key) != len(target_after_rows) or set(target_by_key) != projection_keys:
        raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "targeted candidates differ from projection patches")
    for key, row in target_by_key.items():
        patch = candidate_patch_by_key.get(key)
        if patch is None or canonical_record_sha256(row) != patch.get("after_record_sha256"):
            raise ReviewPackageError("PATCH_PROJECTION_MISMATCH", "targeted candidate differs from candidate patch")

    return {
        "authority_zeroes": "PASS",
        "catalog_family_join": "PASS",
        "occurrence_relationships": len(relationship_rows),
        "conditional_gifts": len(gift_rows),
        "fixed_combo_components": len(tables["fixed_combo_component_relationships_v5"].rows),
        "source_registry_hash_join": "PASS",
        "dependencies": len(dependency_rows),
        "active_mapping_dependencies": len(active_members),
        "targeted_normalized_rows": len(normalized_rows),
        "targeted_patch_projections": "PASS",
        "owner_preferences": owner_preference_controls,
    }


def _validate_v5_unchanged_artifacts(
    source: _PackageSource,
    declared: Mapping[str, tuple[int, str]],
) -> int:
    rows = _require_sequence(
        _verified_json(source, "unchanged_artifacts_v5.json", declared),
        code="INVALID_UNCHANGED_ARTIFACTS",
        message="unchanged-artifact evidence must be an array",
    )
    if len(rows) != 226:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "unchanged-artifact count differs")
    seen: set[str] = set()
    required = {"path", "file", "bytes", "sha256", "role", "v5_byte_hash_rechecked"}
    for raw in rows:
        row = _require_mapping(
            raw, code="INVALID_UNCHANGED_ARTIFACTS", message="unchanged artifact must be an object"
        )
        if not required.issubset(row):
            raise ReviewPackageError("INVALID_UNCHANGED_ARTIFACTS", "unchanged artifact fields are incomplete")
        path = _validated_member_name(
            _nonblank_text(row.get("path"), code="INVALID_UNCHANGED_ARTIFACTS", context="artifact path"),
            source.limits,
        )
        if path in seen:
            raise ReviewPackageError("DUPLICATE_DECLARATION", "unchanged artifact is duplicated", path=path)
        seen.add(path)
        _nonnegative_int(row.get("bytes"), code="INVALID_UNCHANGED_ARTIFACTS", context="artifact bytes")
        _require_sha256(row.get("sha256"), path=path)
        if row.get("v5_byte_hash_rechecked") is not True:
            raise ReviewPackageError("INVALID_UNCHANGED_ARTIFACTS", "V5 byte recheck is not true", path=path)
    return len(rows)


def _apply_v5_patch_chain(
    *,
    prior_package: ReviewPackage,
    baseline_path: str | Path,
    baseline_prerequisites: Mapping[str, Any],
    patches: Mapping[str, Sequence[Mapping[str, Any]]],
    changed: Mapping[str, Mapping[str, Any]],
    tables: dict[str, PackageTable],
    limits: Any,
) -> bool:
    base_rows: dict[str, Sequence[Mapping[str, Any]]] = {}
    for table_name in _V5_PATCH_CONTRACTS:
        prior_table = prior_package.tables.get(f"effective_{table_name}")
        if prior_table is not None:
            base_rows[table_name] = prior_table.rows
    # current_additions_review did not change in V4.1, so it is not exposed as
    # an effective table by the V4.1 adapter. Read only its exact reviewed V4
    # path from the caller-authorized baseline archive.
    if "current_additions_review" not in base_rows:
        with _open_source(baseline_path, limits) as baseline:
            path = "analysis/revision_v4/integration/tables/current_additions_review.jsonl"
            rows, raw_sha = _read_jsonl(baseline, path)
            spec = changed["current_additions_review"]
            if (
                len(rows) != spec["base_effective_v4_1_rows"]
                or canonical_jsonl_sha256(rows) != spec["base_effective_v4_1_canonical_jsonl_sha256"]
            ):
                raise ReviewPackageError(
                    "BASELINE_TABLE_MISMATCH",
                    "current additions do not match the V5 base hash gate",
                    path=path,
                )
            base_rows["current_additions_review"] = rows
            tables["v5_base_current_additions_review"] = PackageTable(
                "v5_base_current_additions_review",
                path,
                "jsonl",
                tuple(rows),
                raw_sha,
                canonical_jsonl_sha256(rows),
            )
    if set(base_rows) != set(_V5_PATCH_CONTRACTS):
        return False

    targeted_normalized = {
        int(row["row_number_1based"]): row
        for row in tables["targeted_normalized_contract_rows"].rows
    }
    effective: dict[str, PatchReplay] = {}
    for table_name, contract in _V5_PATCH_CONTRACTS.items():
        base = base_rows[table_name]
        spec = changed[table_name]
        if (
            len(base) != spec["base_effective_v4_1_rows"]
            or canonical_jsonl_sha256(base) != spec["base_effective_v4_1_canonical_jsonl_sha256"]
        ):
            raise ReviewPackageError(
                "V5_BASE_HASH_MISMATCH", f"{table_name} does not match the exact V4.1 result"
            )
        patch_rows: Sequence[Mapping[str, Any]] = patches[table_name]
        key_mode = "BASE"
        expected_key_fields: Sequence[str] | None = contract.stable_key_fields
        external: Mapping[int, Mapping[str, Any]] | None = None
        if contract.positional_metadata_key:
            patch_rows = tuple({**dict(row), "stable_key": {}} for row in patch_rows)
            key_mode = "EXTERNAL_PROOF"
            expected_key_fields = None
            external = {
                row_number: {
                    "source_tier_id": record["source_tier_id"],
                    "source_offer_id": record["source_offer_id"],
                }
                for row_number, record in targeted_normalized.items()
            }
        append_shapes = _V5_APPEND_SHAPES[table_name]
        required_append_fields = (
            set.intersection(*(set(shape) for shape in append_shapes))
            if append_shapes
            else set()
        )
        replay = replay_patch_table(
            base,
            patch_rows,
            allowed_fields=set(contract.replace_fields),
            append_allowed_fields=set(contract.append_fields),
            required_append_fields=required_append_fields,
            expected_stable_key_fields=expected_key_fields,
            key_mode=key_mode,
            external_keys_by_row=external,
            immutable_fields=set(contract.stable_key_fields) if key_mode == "BASE" else set(),
            allow_field_removal=False,
            append_changes_required=False,
            expected_rows=contract.effective_rows,
            expected_sha256=spec["effective_v5_canonical_jsonl_sha256"],
        )
        second = replay_patch_table(
            replay.rows,
            patch_rows,
            allowed_fields=set(contract.replace_fields),
            append_allowed_fields=set(contract.append_fields),
            required_append_fields=required_append_fields,
            expected_stable_key_fields=expected_key_fields,
            key_mode=key_mode,
            external_keys_by_row=external,
            immutable_fields=set(contract.stable_key_fields) if key_mode == "BASE" else set(),
            allow_field_removal=False,
            append_changes_required=False,
            expected_rows=contract.effective_rows,
            expected_sha256=spec["effective_v5_canonical_jsonl_sha256"],
        )
        if second.applied or second.rows != replay.rows:
            raise ReviewPackageError("PATCH_NOT_IDEMPOTENT", f"{table_name} changed on second replay")
        effective[table_name] = replay
        tables[f"effective_v5_{table_name}"] = PackageTable(
            f"effective_v5_{table_name}",
            f"table_deltas/{table_name}.patch.jsonl",
            "jsonl",
            replay.rows,
            tables[f"v5_patch_{table_name}"].raw_sha256,
            replay.canonical_jsonl_sha256,
        )
    for patch_name, direct_name in _V5_DIRECT_EFFECTIVE_TABLES.items():
        if tuple(tables[direct_name].rows) != effective[patch_name].rows:
            raise ReviewPackageError(
                "PATCH_PROJECTION_MISMATCH", f"{direct_name} differs from replayed V5 result"
            )
    effective_review = effective["normalized_price_contract_review"].rows
    effective_links = effective["normalized_price_contract_links"].rows
    for row_number, target in targeted_normalized.items():
        if row_number > len(effective_review) or row_number > len(effective_links):
            raise ReviewPackageError(
                "NORMALIZED_POSITION_MISMATCH", "targeted normalized row exceeds replayed tables"
            )
        if effective_review[row_number - 1] != target["contract_row"]:
            raise ReviewPackageError(
                "PATCH_PROJECTION_MISMATCH", "targeted normalized contract differs after replay"
            )
        link = effective_links[row_number - 1]
        if (
            link.get("normalized_table_row_number_1based") != row_number
            or link.get("projection_row_id") != target["source_tier_id"]
            or link.get("source_offer_id") != target["source_offer_id"]
        ):
            raise ReviewPackageError(
                "PATCH_PROJECTION_MISMATCH", "targeted normalized link differs after replay"
            )
    return True


def read_v5_review_delta(
    source: _PackageSource,
    *,
    manifest_value: Mapping[str, Any],
    manifest_bytes: bytes,
    baseline_path: str | Path | None,
    prior_delta_path: str | Path | None,
    external_evidence_root: str | Path | None,
) -> ReviewPackage:
    """Validate the explicit V5 delta without granting operational authority."""

    declared, verified = _validate_v5_manifest(source, manifest_value)
    tables = _read_v5_tables(source, declared)
    patches, changed = _read_v5_patches(source, declared, tables)
    controls = _validate_v5_controls(source, declared)
    baseline_prerequisites = _validate_baseline_prerequisites(source, declared)
    unchanged_count = _validate_v5_unchanged_artifacts(source, declared)
    source_index, source_unavailable = _validate_source_page_index(
        source, declared, manifest_value
    )
    external_evidence, external_unavailable = _validate_v5_external_evidence(
        manifest_value,
        root_path=external_evidence_root,
        source=source,
    )

    if baseline_path is not None:
        expected_baseline = baseline_prerequisites["baseline"]
        observed_bytes, observed_hash = _hash_regular_file(
            baseline_path, maximum=source.limits.max_entry_bytes
        )
        if observed_bytes != expected_baseline["bytes"] or observed_hash != expected_baseline["sha256"]:
            raise ReviewPackageError(
                "BASELINE_ARCHIVE_MISMATCH", "supplied V4 baseline differs from the exact prerequisite"
            )

    prior_package: ReviewPackage | None = None
    prior_source: _PackageSource | None = None
    try:
        if prior_delta_path is not None:
            observed_bytes, observed_hash = _hash_regular_file(
                prior_delta_path, maximum=source.limits.max_entry_bytes
            )
            expected_prior = baseline_prerequisites["prior_delta"]
            if observed_bytes != expected_prior["bytes"] or observed_hash != expected_prior["sha256"]:
                raise ReviewPackageError(
                    "PRIOR_DELTA_MISMATCH", "supplied V4.1 delta differs from the exact V5 prerequisite"
                )
            prior_source = _open_source(prior_delta_path, source.limits)
        corrections = _validate_locator_corrections(tables, prior_source)
        if prior_source is not None:
            prior_package = _read_real_delta(
                prior_source,
                manifest_name="PACKAGE_FILE_HASHES.json",
                baseline_path=baseline_path,
                locator_corrections=corrections,
            )
    finally:
        if prior_source is not None:
            prior_source.close()

    relationships = _validate_v5_relationships_and_authority(tables, patches)
    unavailable: list[str] = [*source_unavailable, *external_unavailable]
    mechanical_replay = False
    if prior_delta_path is None:
        unavailable.append("EXACT_ORIGINAL_V4_1_DELTA")
    if baseline_path is None:
        unavailable.append("EXACT_V4_BASELINE_ARCHIVE")
    elif prior_package is None:
        raise ReviewPackageError(
            "MISSING_PRIOR_DELTA", "V5 replay requires the exact original V4.1 delta before V5"
        )
    else:
        mechanical_replay = _apply_v5_patch_chain(
            prior_package=prior_package,
            baseline_path=baseline_path,
            baseline_prerequisites=baseline_prerequisites,
            patches=patches,
            changed=changed,
            tables=tables,
            limits=source.limits,
        )
        if not mechanical_replay:
            unavailable.append("INCOMPLETE_EXACT_V4_BASELINE")

    for candidate, descriptor, code in (
        (baseline_path, baseline_prerequisites["baseline"], "BASELINE_ARCHIVE_CHANGED"),
        (prior_delta_path, baseline_prerequisites["prior_delta"], "PRIOR_DELTA_CHANGED"),
    ):
        if candidate is None:
            continue
        observed_bytes, observed_hash = _hash_regular_file(
            candidate, maximum=source.limits.max_entry_bytes
        )
        if observed_bytes != descriptor["bytes"] or observed_hash != descriptor["sha256"]:
            raise ReviewPackageError(
                code,
                "a prerequisite changed while the V5 replay chain was validated",
            )

    unavailable.extend(
        (
            "ORIGINAL_SUPPLIER_PDFS",
            "PORTABLE_V5_SNAPSHOT_ROOT",
        )
    )
    if not mechanical_replay:
        unavailable.append("UNCHANGED_ARTIFACT_SOURCE_BYTES_FROM_V4_BASELINE")
    unavailable = sorted(set(unavailable))
    status = "SOURCE_EVIDENCE_REQUIRED" if mechanical_replay else "BASELINE_REQUIRED"
    readiness = {
        "package_integrity": "PASS",
        "schema_validation": "PASS",
        "structural_replay": "PASS" if mechanical_replay else "BASELINE_REQUIRED",
        "source_availability": "SOURCE_EVIDENCE_REQUIRED",
        "semantic_review": "REVIEW_REQUIRED",
        "approval": "NOT_APPROVED",
        "import_ready": "NO",
    }
    supplier_scope = sorted(
        {row["supplier_name_raw"] for row in tables["variant_offer_relationships_v5"].rows}
    )
    snapshot_scope = {
        "comparison_contract": "BUFFALO_SUPPLIER_REVIEW_COMPARISON_V1",
        "identity_contract": "SHOPIFY_VARIANT_VENDOR_SOURCE_OCCURRENCE_V1",
        "lineage_family_sha256": hashlib.sha256(
            _canonical_json(
                {
                    "v4": baseline_prerequisites["baseline"]["sha256"],
                    "v4_1": baseline_prerequisites["prior_delta"]["sha256"],
                }
            )
        ).hexdigest(),
        "supplier_scope_kind": "CAPTURED_VENDOR_LABELS",
        "supplier_scope": supplier_scope,
        "cohort_scope": "ORIGINAL_2000_PLUS_2003_CENSUS",
        "period_semantics": "MIXED_PRINTED_PERIODS_NOT_CURRENT_PRICE_AUTHORITY",
        "period_id": "MIXED_SOURCE_PERIODS",
        "supplier_period_coverage_complete": False,
        "missing_supplier_periods": ["MISSING_SUPPLIER_BOOK_COVERAGE"],
        "channels": ["SUPPLIER_BOOK_REVIEW"],
        "territories": ["NEW_YORK_REVIEW_ONLY"],
        "simulated": False,
    }
    issues = (
        ValidationIssue(
            "STRUCTURAL_VALIDITY_IS_NOT_APPROVAL",
            "V5 hashes, joins, and replay controls do not approve mappings, prices, or purchasing use.",
            severity="INFO",
        ),
    )
    cohort_controls = controls["control_totals"]
    cohorts = {
        "original_cohort": cohort_controls["original_cohort_count"],
        "current_census": cohort_controls["current_census_count"],
        "original_returned": cohort_controls["current_original_returned"],
        "original_not_returned": 1,
        "current_additions": cohort_controls["current_additions"],
        "catalog_statuses": cohort_controls["v5_catalog_statuses"],
        "candidate_dispositions": cohort_controls["v5_candidate_dispositions"],
    }
    return ReviewPackage(
        source=str(source.path),
        package_kind=V5_PACKAGE_KIND,
        snapshot_id=f"V5-{hashlib.sha256(manifest_bytes).hexdigest()[:16]}",
        status=status,
        label=REVIEW_LABEL,
        file_count=len(source.names),
        verified_file_count=verified,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        tables=tables,
        cohorts=cohorts,
        issues=issues,
        unavailable_evidence=tuple(unavailable),
        metadata={
            "readiness": readiness,
            "snapshot_scope": snapshot_scope,
            "integrity_checks": {
                "manifest_files": "PASS",
                "table_catalog_and_profiles": "PASS",
                "patch_contracts": "PASS",
                "locator_overlay": "PASS" if mechanical_replay else "BASELINE_REQUIRED",
                "locator_overlay_patch_binding": (
                    "PASS" if prior_package is not None else "PRIOR_DELTA_REQUIRED"
                ),
                "relationships": relationships,
                "unchanged_artifact_descriptor_records": unchanged_count,
                "external_evidence": external_evidence or "NOT_SUPPLIED",
                "source_page_index": source_index,
                "v4_1_prerequisite": "PASS" if prior_package is not None else "NOT_SUPPLIED",
                "v4_to_v5_replay": "PASS" if mechanical_replay else "BASELINE_REQUIRED",
            },
            "validation": controls,
            "raw_hash_basis": RAW_HASH,
            "canonical_hash_basis": CANONICAL_JSONL_HASH,
            "review_authority": UNAPPROVED_AUTHORITY,
        },
    )


def _portable_sorted_unique_texts(value: Any, *, context: str) -> list[str]:
    rows = _require_sequence(
        value, code="INVALID_PORTABLE_ROOT", message=f"{context} must be an array"
    )
    if any(not isinstance(item, str) or not item.strip() for item in rows):
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", f"{context} contains invalid text")
    result = list(rows)
    if result != sorted(set(result)):
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", f"{context} must be sorted and unique")
    return result


def _validate_portable_scope(value: Any) -> dict[str, Any]:
    scope = _require_mapping(
        value, code="INVALID_PORTABLE_ROOT", message="snapshot_scope must be an object"
    )
    _exact_fields(scope, _PORTABLE_SCOPE_FIELDS, code="INVALID_PORTABLE_ROOT", context="snapshot_scope")
    for field in (
        "comparison_contract", "identity_contract", "lineage_family_sha256", "supplier_scope_kind",
        "cohort_scope", "period_semantics", "period_id",
    ):
        _nonblank_text(scope.get(field), code="INVALID_PORTABLE_ROOT", context=f"snapshot_scope.{field}")
    _require_sha256(scope.get("lineage_family_sha256"), path="snapshot_scope.lineage_family_sha256")
    if (
        scope.get("comparison_contract") != PORTABLE_COMPARISON_CONTRACT
        or scope.get("identity_contract") != PORTABLE_IDENTITY_CONTRACT
        or scope.get("lineage_family_sha256") != PORTABLE_LINEAGE_FAMILY_SHA256
        or scope.get("supplier_scope_kind") != "COMPLETE_SUPPLIER_PERIOD_SET"
        or scope.get("cohort_scope") != "SYNTHETIC_TEST_COHORT"
        or scope.get("period_semantics") != "MONTHLY_SUPPLIER_BOOK_PERIOD"
    ):
        raise ReviewPackageError(
            "PORTABLE_SCOPE_CONTRACT_MISMATCH",
            "portable comparison, identity, lineage, supplier, cohort, or period contract differs",
        )
    for field in ("supplier_scope", "missing_supplier_periods", "channels", "territories"):
        _portable_sorted_unique_texts(scope.get(field), context=f"snapshot_scope.{field}")
    if type(scope.get("supplier_period_coverage_complete")) is not bool or type(scope.get("simulated")) is not bool:
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", "snapshot scope booleans are invalid")
    if scope.get("simulated") is not True:
        raise ReviewPackageError("UNSUPPORTED_SOURCE_REVISION", "the supported portable revision is test-only")
    if scope.get("supplier_period_coverage_complete") is True and scope.get("missing_supplier_periods"):
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", "complete supplier coverage cannot list missing periods")
    if scope.get("supplier_period_coverage_complete") is False and not scope.get("missing_supplier_periods"):
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", "incomplete supplier coverage must name missing periods")
    return dict(scope)


def _validate_portable_lineage(
    value: Any,
    table_specs: Sequence[Mapping[str, Any]],
    *,
    limits: Any,
) -> dict[str, Any]:
    lineage = _require_mapping(
        value, code="INVALID_PORTABLE_ROOT", message="lineage must be an object"
    )
    fields = frozenset(
        {
            "identity_contract", "v4_manifest_refs", "v4_1_patch_refs",
            "v4_1_locator_corrections", "v5_patch_refs", "v5_effective_tables",
        }
    )
    _exact_fields(lineage, fields, code="INVALID_PORTABLE_ROOT", context="lineage")
    if lineage.get("identity_contract") != PORTABLE_IDENTITY_CONTRACT:
        raise ReviewPackageError("LINEAGE_MISMATCH", "portable identity contract differs")
    ref_fields = frozenset({"path", "bytes", "sha256"})
    all_lineage_paths: set[str] = set()
    for field in ("v4_manifest_refs", "v4_1_patch_refs", "v5_patch_refs"):
        values = _require_sequence(
            lineage.get(field), code="INVALID_PORTABLE_ROOT", message=f"{field} must be an array"
        )
        if not values:
            raise ReviewPackageError("INVALID_PORTABLE_ROOT", f"{field} cannot be empty")
        seen: set[str] = set()
        observed_paths: list[str] = []
        for raw in values:
            row = _require_mapping(raw, code="INVALID_PORTABLE_ROOT", message="lineage reference must be an object")
            _exact_fields(row, ref_fields, code="INVALID_PORTABLE_ROOT", context=field)
            path = _validated_member_name(
                _nonblank_text(row.get("path"), code="INVALID_PORTABLE_ROOT", context=f"{field}.path"),
                limits,
            )
            if path in seen:
                raise ReviewPackageError("DUPLICATE_DECLARATION", "lineage path is duplicated", path=path)
            if path in all_lineage_paths:
                raise ReviewPackageError("DUPLICATE_DECLARATION", "lineage path is reused", path=path)
            seen.add(path)
            all_lineage_paths.add(path)
            observed_paths.append(path)
            _nonnegative_int(row.get("bytes"), code="INVALID_PORTABLE_ROOT", context=f"{field}.bytes")
            _require_sha256(row.get("sha256"), path=path)
        if observed_paths != sorted(observed_paths):
            raise ReviewPackageError("INVALID_PORTABLE_ROOT", f"{field} must be path-sorted")
    locator = _require_mapping(
        lineage.get("v4_1_locator_corrections"),
        code="INVALID_PORTABLE_ROOT",
        message="locator correction lineage must be an object",
    )
    _exact_fields(
        locator,
        frozenset({"path", "row_count", "canonical_jsonl_sha256"}),
        code="INVALID_PORTABLE_ROOT",
        context="locator correction lineage",
    )
    _validated_member_name(
        _nonblank_text(locator.get("path"), code="INVALID_PORTABLE_ROOT", context="locator path"),
        limits,
    )
    locator_count = _nonnegative_int(locator.get("row_count"), code="INVALID_PORTABLE_ROOT", context="locator row count")
    if locator_count <= 0:
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", "locator correction proof cannot be empty")
    _require_sha256(locator.get("canonical_jsonl_sha256"), path="locator corrections")

    observed_anchors = {
        field: lineage[field]
        for field in (
            "v4_manifest_refs",
            "v4_1_patch_refs",
            "v4_1_locator_corrections",
            "v5_patch_refs",
        )
    }
    if not _same_json_value(observed_anchors, _PORTABLE_SYNTHETIC_LINEAGE_ANCHORS):
        raise ReviewPackageError(
            "LINEAGE_MISMATCH",
            "portable lineage does not match the code-owned synthetic revision",
        )

    effective_rows = _require_sequence(
        lineage.get("v5_effective_tables"),
        code="INVALID_PORTABLE_ROOT",
        message="effective-table lineage must be an array",
    )
    expected = {
        row["name"]: (row["row_count"], row["canonical_jsonl_sha256"])
        for row in table_specs
    }
    observed: dict[str, tuple[int, str]] = {}
    for raw in effective_rows:
        row = _require_mapping(raw, code="INVALID_PORTABLE_ROOT", message="effective-table row must be an object")
        _exact_fields(
            row,
            frozenset({"name", "row_count", "canonical_jsonl_sha256"}),
            code="INVALID_PORTABLE_ROOT",
            context="effective table",
        )
        name = row.get("name")
        if name in observed or name not in expected:
            raise ReviewPackageError("INVALID_PORTABLE_ROOT", "effective-table lineage set differs")
        count = _nonnegative_int(row.get("row_count"), code="INVALID_PORTABLE_ROOT", context="effective rows")
        digest = _require_sha256(row.get("canonical_jsonl_sha256"), path=str(name))
        observed[name] = (count, digest)
    if observed != expected:
        raise ReviewPackageError("LINEAGE_MISMATCH", "effective-table lineage differs from table specs")
    if [row.get("name") if isinstance(row, Mapping) else None for row in effective_rows] != list(expected):
        raise ReviewPackageError("LINEAGE_MISMATCH", "effective-table lineage order differs")
    return dict(lineage)


def _read_portable_part(
    shard: _ZipSource,
    *,
    path: str,
    expected_bytes: int,
    expected_sha256: str,
    expected_rows: int,
) -> list[dict[str, Any]]:
    if path.casefold().endswith((".zip", ".zipx")):
        raise ReviewPackageError("NESTED_ARCHIVE_REJECTED", "portable table part names a nested ZIP", path=path)
    if shard.size(path) != expected_bytes:
        raise ReviewPackageError("SIZE_MISMATCH", "portable part size differs", path=path)
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    observed = 0
    with shard.open(path) as handle:
        if handle.read(4) in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
            raise ReviewPackageError("NESTED_ARCHIVE_REJECTED", "portable table part is a nested ZIP", path=path)
        handle.seek(0) if hasattr(handle, "seek") and handle.seekable() else None
        # ZipExtFile is seekable for ordinary members, but do not depend on it.
    with shard.open(path) as handle:
        while True:
            line = handle.readline(shard.limits.max_line_bytes + 1)
            if not line:
                break
            observed += len(line)
            digest.update(line)
            if len(line) > shard.limits.max_line_bytes:
                raise ReviewPackageError("LINE_TOO_LARGE", "portable JSONL line exceeds limit", path=path)
            if not line.endswith(b"\n") or line.endswith(b"\r\n"):
                raise ReviewPackageError("NONCANONICAL_JSONL", "portable JSONL requires LF records", path=path)
            value = _parse_json_bytes(line[:-1], path=path, limits=shard.limits)
            row = _checked_record(value, path=path, row=len(rows) + 1, limits=shard.limits)
            if _canonical_json(row) + b"\n" != line:
                raise ReviewPackageError("NONCANONICAL_JSONL", "portable row bytes are not canonical", path=path)
            rows.append(row)
            if len(rows) > shard.limits.max_rows_per_table:
                raise ReviewPackageError("TOO_MANY_ROWS", "portable part exceeds row limit", path=path)
    if observed != expected_bytes or digest.hexdigest() != expected_sha256:
        raise ReviewPackageError("FILE_INTEGRITY_MISMATCH", "portable part bytes differ", path=path)
    if len(rows) != expected_rows:
        raise ReviewPackageError("ROW_COUNT_MISMATCH", "portable part row count differs", path=path)
    return rows


def _validate_portable_table_rows(name: str, rows: Sequence[Mapping[str, Any]]) -> None:
    required, allowed, _ = _PORTABLE_TABLE_CONTRACTS[name]
    keys: set[tuple[bytes, ...]] = set()
    key_fields = {
        "variants": ("variant_id",),
        "offers": ("variant_id", "vendor", "source_occurrence_id"),
        "tiers": ("source_tier_id",),
        "representation_gaps": ("gap_id",),
    }[name]
    for row_number, row in enumerate(rows, start=1):
        if not required.issubset(row) or not set(row).issubset(allowed):
            raise ReviewPackageError("PORTABLE_SCHEMA_MISMATCH", f"{name} row fields differ", row=row_number)
        key: list[bytes] = []
        for field in key_fields:
            value = _nonblank_text(row.get(field), code="TABLE_KEY_MISSING", context=f"{name}.{field}")
            key.append(_canonical_json(value))
        encoded = tuple(key)
        if encoded in keys:
            raise ReviewPackageError("TABLE_KEY_NOT_UNIQUE", f"{name} key is duplicated", row=row_number)
        keys.add(encoded)
        if name == "variants":
            for field in ("product_title", "variant_title", "catalog_status"):
                if field in row and row[field] is not None and not isinstance(row[field], str):
                    raise ReviewPackageError(
                        "PORTABLE_SCHEMA_MISMATCH", f"variants.{field} must be text or null", row=row_number
                    )
        if name == "offers":
            if not isinstance(row.get("supplier_code"), str) or not row["supplier_code"]:
                raise ReviewPackageError("PORTABLE_SCHEMA_MISMATCH", "supplier_code must retain exact text", row=row_number)
            for flag in ("mapping_approved", "price_approved", "import_ready"):
                if row.get(flag) is not False:
                    raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", f"{flag} must be false", row=row_number)
            if row.get("candidate_disposition") not in {
                "PROPOSED_REVIEW_CANDIDATE", "SEARCH_LEAD_ONLY",
                "REJECTED_ATTRIBUTE_CONFLICT", "REJECTED_IDENTITY_CONFLICT",
            }:
                raise ReviewPackageError(
                    "PORTABLE_SCHEMA_MISMATCH", "candidate disposition is unsupported", row=row_number
                )
            if "policy_excluded" in row and type(row["policy_excluded"]) is not bool:
                raise ReviewPackageError(
                    "PORTABLE_SCHEMA_MISMATCH", "policy_excluded must be boolean", row=row_number
                )
            for field in (
                "shopify_units_per_case", "qualifying_units_per_case", "physical_units_per_case",
                "retail_pack", "source_page",
            ):
                if field in row and row[field] is not None and (
                    isinstance(row[field], bool) or not isinstance(row[field], (int, Decimal))
                ):
                    raise ReviewPackageError(
                        "PORTABLE_SCHEMA_MISMATCH", f"{field} has invalid native type", row=row_number
                    )
            for field in (
                "program_type", "source_file", "source_period", "effective_from", "effective_through",
                "territory", "channel", "catalog_status", "split_fee_basis", "owner_decision_id",
                "rejected_reason", "source_vintage",
            ):
                if field in row and row[field] is not None and not isinstance(row[field], str):
                    raise ReviewPackageError(
                        "PORTABLE_SCHEMA_MISMATCH", f"offers.{field} must be text or null", row=row_number
                    )
            if "source_sha256" in row and row["source_sha256"] is not None:
                _require_sha256(row["source_sha256"], path=f"offers[{row_number}].source_sha256")
        if name == "tiers":
            for field in ("variant_id", "vendor", "source_occurrence_id"):
                _nonblank_text(
                    row.get(field), code="TABLE_KEY_MISSING", context=f"tiers.{field}"
                )
            if row.get("selected_tier") is not False:
                raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "portable tier cannot be selected", row=row_number)
            if "break_unit" in row and row["break_unit"] not in {None, "BASE", "BT", "CS"}:
                raise ReviewPackageError(
                    "PORTABLE_SCHEMA_MISMATCH", "tier break_unit is unsupported", row=row_number
                )
            if "program_type" in row and row["program_type"] is not None and not isinstance(row["program_type"], str):
                raise ReviewPackageError(
                    "PORTABLE_SCHEMA_MISMATCH", "tier program_type must be text or null", row=row_number
                )
            for field in ("break_quantity", "unit_price", "case_price"):
                if field in row and row[field] is not None and (
                    isinstance(row[field], bool) or not isinstance(row[field], (int, Decimal))
                ):
                    raise ReviewPackageError(
                        "PORTABLE_SCHEMA_MISMATCH", f"tier {field} has invalid native type", row=row_number
                    )
        if name == "representation_gaps":
            for field in ("area", "current_handling", "later_route"):
                _nonblank_text(
                    row.get(field), code="PORTABLE_SCHEMA_MISMATCH", context=f"representation_gaps.{field}"
                )
            if row.get("authority") != "REVIEW_ONLY":
                raise ReviewPackageError("INVALID_AUTHORITY", "representation gaps must remain review-only", row=row_number)


def _portable_source_evidence_specs(
    root: Mapping[str, Any],
    *,
    limits: Any,
) -> tuple[list[dict[str, Any]], list[str], dict[tuple[str, str], str]]:
    values = _require_sequence(
        root.get("source_evidence"),
        code="INVALID_PORTABLE_ROOT",
        message="source_evidence must be an array",
    )
    if not values:
        raise ReviewPackageError(
            "SOURCE_EVIDENCE_MISMATCH",
            "the supported portable revision requires explicit source evidence descriptors",
        )
    evidence_fields = frozenset(
        {
            "logical_path", "source_pdf_sha256", "physical_page", "evidence_content_sha256",
            "delivery_archive", "delivery_path", "availability", "source_review_scope",
        }
    )
    result: list[dict[str, Any]] = []
    unavailable: list[str] = []
    delivered: dict[tuple[str, str], str] = {}
    seen_logical: set[str] = set()
    for raw in values:
        row = _require_mapping(
            raw, code="INVALID_PORTABLE_ROOT", message="source evidence must be an object"
        )
        _exact_fields(row, evidence_fields, code="INVALID_PORTABLE_ROOT", context="source evidence")
        logical = _validated_member_name(
            _nonblank_text(
                row.get("logical_path"), code="INVALID_PORTABLE_ROOT", context="evidence path"
            ),
            limits,
        )
        if logical in seen_logical:
            raise ReviewPackageError("DUPLICATE_DECLARATION", "source evidence is duplicated", path=logical)
        seen_logical.add(logical)
        _require_sha256(row.get("source_pdf_sha256"), path=logical)
        _nonblank_text(
            row.get("source_review_scope"),
            code="INVALID_PORTABLE_ROOT",
            context="source review scope",
        )
        if row.get("physical_page") is not None:
            page = _nonnegative_int(
                row.get("physical_page"), code="INVALID_PORTABLE_ROOT", context="physical page"
            )
            if page <= 0:
                raise ReviewPackageError("INVALID_PORTABLE_ROOT", "physical page must be positive")
        availability = row.get("availability")
        if availability not in {"DELIVERED", "EXTERNAL_BY_HASH", "MISSING"}:
            raise ReviewPackageError("INVALID_PORTABLE_ROOT", "source availability is unsupported")
        content_hash = row.get("evidence_content_sha256")
        delivery_archive, delivery_path = row.get("delivery_archive"), row.get("delivery_path")
        if availability == "DELIVERED":
            archive = _validated_member_name(
                _nonblank_text(
                    delivery_archive, code="SOURCE_EVIDENCE_MISMATCH", context="delivery archive"
                ),
                limits,
            )
            path = _validated_member_name(
                _nonblank_text(
                    delivery_path, code="SOURCE_EVIDENCE_MISMATCH", context="delivery path"
                ),
                limits,
            )
            digest = _require_sha256(content_hash, path=logical)
            key = (archive, path)
            if key in delivered:
                raise ReviewPackageError(
                    "DUPLICATE_DECLARATION", "delivered evidence member is reused", path=path
                )
            delivered[key] = digest
        elif any(value is not None for value in (content_hash, delivery_archive, delivery_path)):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH", "external/missing evidence cannot claim delivered bytes"
            )
        else:
            unavailable.append(f"SOURCE_EVIDENCE:{logical}")
        result.append(dict(row))
    return result, unavailable, delivered


def _validate_portable_scope_and_source_coverage(
    scope: Mapping[str, Any],
    tables: Mapping[str, PackageTable],
    source_evidence: Sequence[Mapping[str, Any]],
) -> None:
    """Bind declared monthly scope to reconstructed offers and source descriptors."""

    offers = tables["offers"].rows
    if not offers:
        raise ReviewPackageError(
            "PORTABLE_SCOPE_MISMATCH",
            "the supported portable revision requires at least one supplier offer",
        )

    evidence_by_source: dict[tuple[str, str, int], Mapping[str, Any]] = {}
    for row in source_evidence:
        logical_path = _nonblank_text(
            row.get("logical_path"),
            code="SOURCE_EVIDENCE_MISMATCH",
            context="source evidence logical path",
        )
        source_hash = _require_sha256(row.get("source_pdf_sha256"), path=logical_path)
        page = _nonnegative_int(
            row.get("physical_page"),
            code="SOURCE_EVIDENCE_MISMATCH",
            context="source evidence physical page",
        )
        if page <= 0 or row.get("source_review_scope") != "SYNTHETIC_TEST_ONLY":
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "synthetic source evidence page or review scope differs",
            )
        key = (logical_path, source_hash, page)
        if key in evidence_by_source:
            raise ReviewPackageError(
                "DUPLICATE_DECLARATION",
                "portable source evidence identity is duplicated",
                path=logical_path,
            )
        evidence_by_source[key] = row

    suppliers: set[str] = set()
    periods: set[str] = set()
    channels: set[str] = set()
    territories: set[str] = set()
    supplier_period_complete: dict[tuple[str, str], bool] = {}
    used_evidence: set[tuple[str, str, int]] = set()
    for row_number, row in enumerate(offers, start=1):
        vendor = _nonblank_text(
            row.get("vendor"),
            code="PORTABLE_SCOPE_MISMATCH",
            context=f"offers[{row_number}].vendor",
        )
        period = _nonblank_text(
            row.get("source_period"),
            code="PORTABLE_SCOPE_MISMATCH",
            context=f"offers[{row_number}].source_period",
        )
        channel = _nonblank_text(
            row.get("channel"),
            code="PORTABLE_SCOPE_MISMATCH",
            context=f"offers[{row_number}].channel",
        )
        territory = _nonblank_text(
            row.get("territory"),
            code="PORTABLE_SCOPE_MISMATCH",
            context=f"offers[{row_number}].territory",
        )
        source_file = _nonblank_text(
            row.get("source_file"),
            code="SOURCE_EVIDENCE_MISMATCH",
            context=f"offers[{row_number}].source_file",
        )
        source_hash = _require_sha256(
            row.get("source_sha256"), path=f"offers[{row_number}].source_sha256"
        )
        source_page = _nonnegative_int(
            row.get("source_page"),
            code="SOURCE_EVIDENCE_MISMATCH",
            context=f"offers[{row_number}].source_page",
        )
        if source_page <= 0:
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH", "offer source pages must be positive"
            )
        source_key = (source_file, source_hash, source_page)
        evidence = evidence_by_source.get(source_key)
        if evidence is None:
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "an offer is not bound to an exact source evidence descriptor",
                row=row_number,
            )
        used_evidence.add(source_key)
        pair = (vendor, period)
        supplier_period_complete[pair] = supplier_period_complete.get(pair, True) and (
            evidence.get("availability") == "DELIVERED"
        )
        suppliers.add(vendor)
        periods.add(period)
        channels.add(channel)
        territories.add(territory)

    if used_evidence != set(evidence_by_source):
        raise ReviewPackageError(
            "SOURCE_EVIDENCE_MISMATCH",
            "portable source evidence contains an occurrence-unbound descriptor",
        )
    if (
        list(scope.get("supplier_scope", ())) != sorted(suppliers)
        or list(scope.get("channels", ())) != sorted(channels)
        or list(scope.get("territories", ())) != sorted(territories)
        or periods != {scope.get("period_id")}
    ):
        raise ReviewPackageError(
            "PORTABLE_SCOPE_MISMATCH",
            "declared supplier, period, channel, or territory scope differs from offer rows",
        )
    expected_pairs = {
        (supplier, scope["period_id"])
        for supplier in scope["supplier_scope"]
    }
    if set(supplier_period_complete) != expected_pairs:
        raise ReviewPackageError(
            "PORTABLE_SCOPE_MISMATCH",
            "declared supplier-period coverage differs from reconstructed offers",
        )
    derived_missing = sorted(
        f"{supplier}/{period}"
        for (supplier, period), complete in supplier_period_complete.items()
        if not complete
    )
    derived_complete = not derived_missing
    if (
        scope.get("supplier_period_coverage_complete") is not derived_complete
        or list(scope.get("missing_supplier_periods", ())) != derived_missing
    ):
        raise ReviewPackageError(
            "PORTABLE_SCOPE_COVERAGE_MISMATCH",
            "supplier-period completeness is not supported by exact source evidence",
        )


def read_portable_review_package(source: _PackageSource) -> ReviewPackage:
    """Validate a self-contained, code-owned TEST-ONLY portable snapshot."""

    if not isinstance(source, _DirectorySource):
        raise ReviewPackageError(
            "PORTABLE_ROOT_MUST_BE_DIRECTORY",
            "portable root and seal must be ordinary sibling files, not nested in a ZIP",
        )
    root_bytes = _read_bounded(source, PORTABLE_ROOT, source.limits.max_manifest_bytes)
    root = _require_mapping(
        _parse_json_bytes(root_bytes, path=PORTABLE_ROOT, limits=source.limits),
        code="INVALID_PORTABLE_ROOT",
        message="portable root must be an object",
    )
    if root_bytes != _canonical_json(root) + b"\n":
        raise ReviewPackageError("NONCANONICAL_MANIFEST", "portable root bytes are not canonical")
    _exact_fields(root, _PORTABLE_ROOT_FIELDS, code="INVALID_PORTABLE_ROOT", context="portable root")
    if root.get("format") != PORTABLE_FORMAT or root.get("source_revision") != PORTABLE_SYNTHETIC_REVISION:
        raise ReviewPackageError("UNSUPPORTED_SOURCE_REVISION", "portable format or source revision is unsupported")
    package_id = _nonblank_text(root.get("package_id"), code="INVALID_PORTABLE_ROOT", context="package_id")
    _iso_datetime(root.get("generated_at_utc"), context="portable generated_at_utc")
    if root.get("authority") != UNAPPROVED_AUTHORITY or root.get("review_only") is not True:
        raise ReviewPackageError("INVALID_AUTHORITY", "portable package must remain unapproved review-only")

    approval_counts = _require_mapping(
        root.get("approval_counts"), code="INVALID_PORTABLE_ROOT", message="approval_counts must be an object"
    )
    expected_approval_counts = {
        "mapping_approvals": 0,
        "price_approvals": 0,
        "import_ready_rows": 0,
    }
    if set(approval_counts) != set(expected_approval_counts) or any(
        type(approval_counts[field]) is not int or approval_counts[field] != expected
        for field, expected in expected_approval_counts.items()
    ):
        raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "portable approval counts must be zero")
    operational = _require_mapping(
        root.get("operational_effects"), code="INVALID_PORTABLE_ROOT", message="operational_effects must be an object"
    )
    expected_effects = {
        "database_writes": 0, "shopify_calls": 0, "mapping_activations": 0,
        "price_activations": 0, "po_actions": 0,
    }
    if set(operational) != set(expected_effects) or any(
        type(operational[field]) is not int or operational[field] != expected
        for field, expected in expected_effects.items()
    ):
        raise ReviewPackageError("UNAUTHORIZED_OPERATIONAL_EFFECT", "portable operational effects must be zero")
    cohorts = _require_mapping(
        root.get("cohorts"), code="INVALID_PORTABLE_ROOT", message="cohorts must be an object"
    )
    if set(cohorts) != {"original_cohort_count", "current_census_count", "current_original_returned", "current_additions"}:
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", "portable cohort fields differ")
    for field, value in cohorts.items():
        _nonnegative_int(value, code="INVALID_PORTABLE_ROOT", context=f"cohorts.{field}")
    if cohorts["current_original_returned"] > cohorts["original_cohort_count"]:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "returned cohort exceeds original cohort")
    if cohorts["current_census_count"] != (
        cohorts["current_original_returned"] + cohorts["current_additions"]
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "portable census does not reconcile")

    scope = _validate_portable_scope(root.get("snapshot_scope"))
    table_specs_raw = _require_sequence(
        root.get("tables"), code="INVALID_PORTABLE_ROOT", message="portable tables must be an array"
    )
    if [row.get("name") if isinstance(row, Mapping) else None for row in table_specs_raw] != list(_PORTABLE_TABLE_CONTRACTS):
        raise ReviewPackageError("INVALID_PORTABLE_ROOT", "portable table order/set differs")
    table_specs: list[dict[str, Any]] = []
    all_parts: list[dict[str, Any]] = []
    global_member_keys: set[str] = set()
    for raw in table_specs_raw:
        spec = _require_mapping(raw, code="INVALID_PORTABLE_ROOT", message="portable table must be an object")
        _exact_fields(spec, _PORTABLE_TABLE_FIELDS, code="INVALID_PORTABLE_ROOT", context="portable table")
        name = spec["name"]
        _, _, dictionary_refs = _PORTABLE_TABLE_CONTRACTS[name]
        observed_dictionary_refs = _require_sequence(
            spec.get("field_dictionary_refs"),
            code="PORTABLE_SCHEMA_MISMATCH",
            message=f"{name} field_dictionary_refs must be an array",
        )
        if tuple(observed_dictionary_refs) != dictionary_refs or spec.get("encoding") != _PORTABLE_ENCODING:
            raise ReviewPackageError("PORTABLE_SCHEMA_MISMATCH", f"{name} dictionary or encoding differs")
        row_count = _nonnegative_int(spec.get("row_count"), code="INVALID_PORTABLE_ROOT", context=f"{name}.row_count")
        if row_count > source.limits.max_rows_per_table:
            raise ReviewPackageError(
                "TOO_MANY_ROWS", "portable logical table exceeds row limit", path=name
            )
        _require_sha256(spec.get("canonical_jsonl_sha256"), path=name)
        parts = _require_sequence(spec.get("parts"), code="INVALID_PORTABLE_ROOT", message="parts must be an array")
        if not parts:
            raise ReviewPackageError("INVALID_PORTABLE_ROOT", "every portable table needs at least one part")
        next_row = 1
        for ordinal, raw_part in enumerate(parts, start=1):
            part = _require_mapping(raw_part, code="INVALID_PORTABLE_ROOT", message="portable part must be an object")
            _exact_fields(part, _PORTABLE_PART_FIELDS, code="INVALID_PORTABLE_ROOT", context="portable part")
            observed_ordinal = _nonnegative_int(
                part.get("ordinal"), code="INVALID_PORTABLE_ROOT", context="part ordinal"
            )
            observed_first_row = _nonnegative_int(
                part.get("first_row_1based"),
                code="INVALID_PORTABLE_ROOT",
                context="part first row",
            )
            if observed_ordinal != ordinal or observed_first_row != next_row:
                raise ReviewPackageError("PORTABLE_PART_ORDER_MISMATCH", "portable part ordering or row range differs")
            archive = _validated_member_name(
                _nonblank_text(part.get("archive"), code="INVALID_PORTABLE_ROOT", context="part archive"),
                source.limits,
            )
            path = _validated_member_name(
                _nonblank_text(part.get("path"), code="INVALID_PORTABLE_ROOT", context="part path"),
                source.limits,
            )
            collision = path.casefold()
            if collision in global_member_keys:
                raise ReviewPackageError("PATH_COLLISION", "portable member path is reused", path=path)
            global_member_keys.add(collision)
            count = _nonnegative_int(part.get("row_count"), code="INVALID_PORTABLE_ROOT", context="part rows")
            if count <= 0:
                raise ReviewPackageError("ROW_COUNT_MISMATCH", "portable parts cannot be empty")
            _nonnegative_int(part.get("bytes"), code="INVALID_PORTABLE_ROOT", context="part bytes")
            _require_sha256(part.get("raw_sha256"), path=path)
            next_row += count
            all_parts.append(dict(part))
        if next_row - 1 != row_count:
            raise ReviewPackageError("ROW_COUNT_MISMATCH", "portable table part rows do not reconcile")
        table_specs.append(dict(spec))
    lineage = _validate_portable_lineage(root.get("lineage"), table_specs, limits=source.limits)
    source_evidence, evidence_unavailable, delivered_evidence = _portable_source_evidence_specs(
        root, limits=source.limits
    )
    for archive, path in delivered_evidence:
        collision = path.casefold()
        if collision in global_member_keys:
            raise ReviewPackageError("PATH_COLLISION", "evidence and table paths collide", path=path)
        global_member_keys.add(collision)

    seal_bytes = _read_bounded(source, PORTABLE_SEAL, source.limits.max_manifest_bytes)
    seal = _require_mapping(
        _parse_json_bytes(seal_bytes, path=PORTABLE_SEAL, limits=source.limits),
        code="INVALID_PORTABLE_SEAL",
        message="portable seal must be an object",
    )
    if seal_bytes != _canonical_json(seal) + b"\n":
        raise ReviewPackageError("NONCANONICAL_MANIFEST", "portable seal bytes are not canonical")
    _exact_fields(
        seal,
        frozenset({"format", "label", "package_id", "root", "archives"}),
        code="INVALID_PORTABLE_SEAL",
        context="portable seal",
    )
    if seal.get("format") != PORTABLE_SEAL_FORMAT or seal.get("label") != REVIEW_LABEL or seal.get("package_id") != package_id:
        raise ReviewPackageError("INVALID_PORTABLE_SEAL", "portable seal identity differs")
    root_ref = _require_mapping(seal.get("root"), code="INVALID_PORTABLE_SEAL", message="root seal must be an object")
    if root_ref != {"path": PORTABLE_ROOT, "bytes": len(root_bytes), "sha256": hashlib.sha256(root_bytes).hexdigest()}:
        raise ReviewPackageError("INVALID_PORTABLE_SEAL", "portable root seal differs")
    archive_refs = _require_sequence(
        seal.get("archives"), code="INVALID_PORTABLE_SEAL", message="archive seals must be an array"
    )
    archive_names = sorted(
        {part["archive"] for part in all_parts}
        | {archive for archive, _ in delivered_evidence}
    )
    if [row.get("path") if isinstance(row, Mapping) else None for row in archive_refs] != archive_names:
        raise ReviewPackageError("INVALID_PORTABLE_SEAL", "sealed archive set/order differs")
    if set(source.names) != {PORTABLE_ROOT, PORTABLE_SEAL, *archive_names}:
        raise ReviewPackageError("UNDECLARED_FILE", "portable root directory contains missing or extra files")

    parts_by_archive: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for part in all_parts:
        parts_by_archive[part["archive"]].append(part)
    rows_by_table: dict[str, list[dict[str, Any]]] = {name: [] for name in _PORTABLE_TABLE_CONTRACTS}
    part_table = {
        (part["archive"], part["path"]): spec["name"]
        for spec in table_specs
        for part in spec["parts"]
    }
    total_entries = len(source.names)
    total_expanded = sum(source.size(name) for name in source.names)
    for archive_ref in archive_refs:
        ref = _require_mapping(archive_ref, code="INVALID_PORTABLE_SEAL", message="archive seal must be an object")
        _exact_fields(ref, frozenset({"path", "bytes", "sha256"}), code="INVALID_PORTABLE_SEAL", context="archive seal")
        archive = ref["path"]
        digest, byte_count = _stream_sha256(source, archive)
        if byte_count != ref.get("bytes") or digest != _require_sha256(ref.get("sha256"), path=archive):
            raise ReviewPackageError("ARCHIVE_SEAL_MISMATCH", "portable archive bytes differ", path=archive)
        with _ZipSource(source.path / archive, source.limits) as shard:
            expected_members = {part["path"] for part in parts_by_archive[archive]} | {
                path for evidence_archive, path in delivered_evidence if evidence_archive == archive
            }
            if set(shard.names) != expected_members:
                raise ReviewPackageError("UNDECLARED_FILE", "portable shard member set differs", path=archive)
            total_entries += len(shard.names)
            total_expanded += sum(shard.size(name) for name in shard.names)
            if total_entries > source.limits.max_entries or total_expanded > source.limits.max_total_expanded_bytes:
                raise ReviewPackageError("PACKAGE_TOO_LARGE", "portable shards exceed aggregate limits")
            for part in parts_by_archive[archive]:
                table_name = part_table[(archive, part["path"])]
                rows_by_table[table_name].extend(
                    _read_portable_part(
                        shard,
                        path=part["path"],
                        expected_bytes=part["bytes"],
                        expected_sha256=part["raw_sha256"],
                        expected_rows=part["row_count"],
                    )
                )
            for evidence_archive, path in delivered_evidence:
                if evidence_archive != archive:
                    continue
                if path.casefold().endswith((".zip", ".zipx")):
                    raise ReviewPackageError(
                        "NESTED_ARCHIVE_REJECTED",
                        "portable evidence member names a nested ZIP",
                        path=path,
                    )
                with shard.open(path) as handle:
                    if handle.read(4) in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
                        raise ReviewPackageError(
                            "NESTED_ARCHIVE_REJECTED",
                            "portable evidence member is a nested ZIP",
                            path=path,
                        )
                digest, _ = _stream_sha256(shard, path)
                if digest != delivered_evidence[(evidence_archive, path)]:
                    raise ReviewPackageError(
                        "SOURCE_EVIDENCE_MISMATCH", "delivered source evidence hash differs", path=path
                    )
        final_digest, final_bytes = _stream_sha256(source, archive)
        if final_bytes != ref["bytes"] or final_digest != ref["sha256"]:
            raise ReviewPackageError(
                "FILE_CHANGED_DURING_VALIDATION",
                "portable archive changed while its members were parsed",
                path=archive,
            )

    tables: dict[str, PackageTable] = {}
    for spec in table_specs:
        name = spec["name"]
        rows = rows_by_table[name]
        _validate_portable_table_rows(name, rows)
        canonical_hash = canonical_jsonl_sha256(rows)
        if len(rows) != spec["row_count"] or canonical_hash != spec["canonical_jsonl_sha256"]:
            raise ReviewPackageError("CANONICAL_HASH_MISMATCH", f"portable {name} reconstruction differs")
        tables[name] = PackageTable(
            name,
            f"portable:{name}",
            "canonical-jsonl-shards",
            tuple(rows),
            canonical_hash,
            canonical_hash,
        )
    variant_ids = {row["variant_id"] for row in tables["variants"].rows}
    if len(variant_ids) != cohorts["current_census_count"]:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "portable Variant census differs")
    offer_keys = {
        (row["variant_id"], row["vendor"], row["source_occurrence_id"])
        for row in tables["offers"].rows
    }
    if any(row["variant_id"] not in variant_ids for row in tables["offers"].rows):
        raise ReviewPackageError("JOIN_MISMATCH", "portable offer references an unknown Variant ID")
    if any(
        (row["variant_id"], row["vendor"], row["source_occurrence_id"]) not in offer_keys
        for row in tables["tiers"].rows
    ):
        raise ReviewPackageError("JOIN_MISMATCH", "portable tier references an unknown occurrence")
    if len(tables["representation_gaps"].rows) != 21:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "portable representation-gap count differs")
    if {row["gap_id"] for row in tables["representation_gaps"].rows} != {
        f"G{index:02d}" for index in range(1, 22)
    }:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "portable representation-gap IDs differ")
    _validate_portable_scope_and_source_coverage(scope, tables, source_evidence)

    unavailable = _portable_sorted_unique_texts(
        root.get("missing_prerequisites"), context="missing_prerequisites"
    )
    unavailable.extend(evidence_unavailable)
    if scope["supplier_period_coverage_complete"] is not True:
        unavailable.extend(
            f"SUPPLIER_PERIOD:{item}" for item in scope["missing_supplier_periods"]
        )
    external = _require_sequence(
        root.get("external_evidence"), code="INVALID_PORTABLE_ROOT", message="external_evidence must be an array"
    )
    if external:
        raise ReviewPackageError("UNSUPPORTED_EXTERNAL_EVIDENCE", "test-only portable revision accepts no opaque external records")
    unavailable = sorted(set(unavailable))
    readiness = {
        "package_integrity": "PASS",
        "schema_validation": "PASS",
        "structural_replay": "PASS",
        "source_availability": "PASS" if not unavailable else "SOURCE_EVIDENCE_REQUIRED",
        "semantic_review": "REVIEW_REQUIRED",
        "approval": "NOT_APPROVED",
        "import_ready": "NO",
    }
    return ReviewPackage(
        source=f"portable:{package_id}",
        package_kind=PORTABLE_FORMAT,
        snapshot_id=package_id,
        status="STRUCTURED_REPLAY",
        label=REVIEW_LABEL,
        file_count=total_entries,
        verified_file_count=total_entries,
        manifest_sha256=hashlib.sha256(root_bytes).hexdigest(),
        tables=tables,
        cohorts=dict(cohorts),
        issues=(
            ValidationIssue(
                "TEST_ONLY_STRUCTURAL_REPLAY_IS_NOT_APPROVAL",
                "Portable reconstruction is synthetic test evidence and grants no mapping, price, or PO authority.",
                severity="INFO",
            ),
        ),
        unavailable_evidence=tuple(unavailable),
        metadata={
            "readiness": readiness,
            "snapshot_scope": scope,
            "lineage": lineage,
            "root_sha256": hashlib.sha256(root_bytes).hexdigest(),
            "seal_sha256": hashlib.sha256(seal_bytes).hexdigest(),
            "package_id": package_id,
            "source_revision": PORTABLE_SYNTHETIC_REVISION,
            "review_authority": UNAPPROVED_AUTHORITY,
            "integrity_checks": {
                "root_and_seal": "PASS",
                "archive_readback": "PASS",
                "canonical_table_reconstruction": "PASS",
                "aggregate_shard_limits": "PASS",
                "cross_table_joins": "PASS",
            },
        },
    )
