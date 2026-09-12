"""Exact, read-only adapter for the sealed V5-DAYTIME-A1 snapshot.

This module is intentionally narrow.  It recognizes one code-owned raw root
digest, never executes package-supplied code, streams every declared byte, and
returns review evidence with zero operational authority.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from typing import Any, Callable, Iterator, Mapping, Sequence

from .price_book import PRICE_BOOK_HEADERS
from .supplier_review_package import (
    CANONICAL_JSONL_HASH,
    RAW_HASH,
    PackageTable,
    ReviewLimits,
    ReviewPackage,
    ReviewPackageError,
    ValidationIssue,
    _DirectorySource,
    _PackageSource,
    _ZipSource,
    _canonical_json,
    _checked_record,
    _parse_json_bytes,
    _read_bounded,
    _require_sha256,
    _stream_sha256,
    _validated_member_name,
    canonical_record_sha256,
)
from .supplier_review_v5 import (
    PORTABLE_ROOT,
    PORTABLE_SEAL,
    _V5_CATALOG_FIELDS,
    _V5_CORE_FIELDS,
    _V5_FIELD_PROFILES_RAW_SHA256,
    _V5_PROFILE_AUTHORITY,
    _V5_PROFILE_FIELDS,
    _V5_TABLE_CATALOG_RAW_SHA256,
    _V5_TABLE_ROWS,
    _iso_datetime,
    _validate_v5_field_profiles,
    _validate_v5_normalized_contract_row,
    _validate_v5_relationships_and_authority,
)


A1_ADAPTER = "V5-DAYTIME-A1"
A1_PACKAGE_KIND = "V5_DAYTIME_A1_COMPLETE_SNAPSHOT"
A1_REVIEW_LABEL = "REVIEW ONLY / NOT_APPROVED / NOT_IMPORT_READY"
A1_PACKAGE_ID = "BUFFALO-V5-PORTABLE-DAYTIME-A1-7260a2448f83"
A1_ROOT_SHA256 = "4eef2d6cfe6b89c94804a749be89d4848d48379bff1521f66cfb42d60d599f82"
A1_ROOT_BYTES = 1_789_148
A1_SEAL_SHA256 = "02e81308aad9a242a9606d3b53598a625032dff00e21f61dcd7f8980c3ddc4cc"
A1_SEAL_BYTES = 2_008
A1_TABLE_DESCRIPTOR_SHA256 = "d38b9931eae9d14e09f309eca0d25cb02c7a3d5dd7bc1e08148c27ed2c746764"
A1_EMBEDDED_DESCRIPTOR_SHA256 = "d1cfa1aa014b3172333052391f2a8abb61b9359f707690d69e7a349e17e11300"
A1_SOURCE_DESCRIPTOR_SHA256 = "3b9a06941aadb6dc37cbd6a352dad2b0132000ca7332a8629bb0c62672cf17c1"
A1_EXTERNAL_DESCRIPTOR_SHA256 = "3038ca6309769ff86233e5704c83d4757ca272579b14367d771ec8b415995c13"
A1_CONTROLS_SHA256 = "ed07561ac1c60da75093f06ad8f20ae943ddf1057dae091c597057727287a934"
A1_PDF_PAGE_RANGE_BASIS = (
    "RANGE_CHECKED_AGAINST_PINNED_DECLARATION_NOT_INDEPENDENTLY_PARSED"
)

_ROOT_FIELDS = frozenset(
    {
        "format",
        "source_revision",
        "semantic_addendum_revision",
        "package_id",
        "generated_at_utc",
        "authority",
        "controls",
        "lineage",
        "tables",
        "embedded_files",
        "source_evidence",
        "external_original_files",
        "missing_prerequisites",
        "state_separation",
        "review_only_flags",
        "consumer_rule",
        "final_validation",
    }
)
_SEAL_FIELDS = frozenset(
    {"format", "package_id", "root_manifest", "archives", "review_reader_files", "authority"}
)
_PART_FIELDS = frozenset(
    {"ordinal", "archive", "path", "first_logical_row", "row_count", "bytes", "sha256"}
)
_TABLE_COMMON_FIELDS = frozenset(
    {
        "logical_name",
        "namespace",
        "total_rows",
        "canonical_jsonl_sha256",
        "raw_sha256",
        "restored_path",
        "encoding",
        "field_dictionary_reference",
        "revision",
        "parts",
    }
)
_ENCODING = "UTF-8_SORTED_KEYS_COMPACT_NATIVE_JSON_LF"
_EXPECTED_NAMESPACE_TOTALS: Mapping[str, tuple[int, int, int]] = {
    "effective_v5": (52, 691_311, 70),
    "v5_sidecar": (44, 36_222, 45),
    "daytime_addendum": (24, 18_515, 24),
}
_EXPECTED_TABLES = 120
_EXPECTED_ROWS = 746_048
_EXPECTED_PARTS = 139
_EXPECTED_EMBEDDED = 2_684
_EXPECTED_SOURCE_EVIDENCE = 854
_EXPECTED_INDEX_MEMBERS = 30
_EXPECTED_DAYTIME_DICTIONARY_FIELDS = 398
_DAYTIME_DICTIONARY_PATH = "daytime_addendum/DATA_DICTIONARY_DAYTIME.jsonl"
_DAYTIME_DICTIONARY_SHA256 = "3266f089c3f815557cc7f87227e6986a2b449d1c56152f23bb8c9c464f87025d"
_DAYTIME_DICTIONARY_BYTES = 171_169
_DAYTIME_DICTIONARY_FIELDS = frozenset(
    {
        "authority",
        "field",
        "join_semantics",
        "observed_native_types",
        "role",
        "table",
        "value_handling",
    }
)

_SPECIALIST_EVIDENCE: Mapping[str, tuple[str, str, int, str, int]] = {
    "alcohol_gift_components_v5": (
        "Buffalo_V5_Portable_Structured_02.zip",
        "snapshot/evidence/specialists/remaining/"
        "alcohol_gift_challenge_recommendations.jsonl",
        41_369,
        "599505048b536dc66ae205980a3b2e5464a135495ddbbf0cb3332c756fb049f2",
        8,
    ),
    "source_review_overlays": (
        "Buffalo_V5_Daytime_Review_Addendum.zip",
        "daytime_addendum/specialist_evidence/semantic/"
        "source_description_and_semantic_recommendations.jsonl",
        40_527,
        "2664b65b189bbde4c09859cd169a64d25837c3c33250f60bcde60c361427cfcd",
        25,
    ),
}
_SPECIALIST_EVIDENCE_BY_MEMBER = {
    (archive, member): (logical_name, expected_bytes, digest, row_count)
    for logical_name, (
        archive,
        member,
        expected_bytes,
        digest,
        row_count,
    ) in _SPECIALIST_EVIDENCE.items()
}

_ARCHIVES: tuple[tuple[str, int, str, str], ...] = (
    (
        "Buffalo_V5_Daytime_Review_Addendum.zip",
        37_423_162,
        "1e51bce76ea1afa02f9fcd8d7bb3d7d54fdae01c9bbb7b33b00b6aa4e31ca2f5",
        "DAYTIME_REVIEW_ADDENDUM",
    ),
    (
        "Buffalo_V5_Portable_Structured_01.zip",
        30_351_712,
        "8ae3ce3005518b80250f43e4f4074f79e006a104f0b56b5e55cadd54358f5656",
        "STRUCTURED_SNAPSHOT_SHARD",
    ),
    (
        "Buffalo_V5_Portable_Structured_02.zip",
        30_516_676,
        "0676ebfd54b70d00710f661f85340b0111b92212a041aa53565df5a7f63b7540",
        "STRUCTURED_SNAPSHOT_SHARD",
    ),
    (
        "Buffalo_V5_Portable_Structured_03.zip",
        16_704_307,
        "fbe416856466ff74a4782160e42f3bb96355ca5a061516e1c9ecb87372a3fe3c",
        "STRUCTURED_SNAPSHOT_SHARD",
    ),
    (
        "Buffalo_V5_Portable_Index_and_Validation.zip",
        1_505_773,
        "c3ec56b7db743648f7b84bf3bf5204585b2b3bd5bd2e7f0b7366b3969721d7f2",
        "INDEX_AND_HANDOFF",
    ),
)
_ARCHIVE_BY_NAME = {name: (size, digest, role) for name, size, digest, role in _ARCHIVES}
_INDEX_ARCHIVE = "Buffalo_V5_Portable_Index_and_Validation.zip"
_AUXILIARY_FILES: Mapping[str, tuple[int, str]] = {
    "Buffalo_Daytime_Final_Deliverable_Hashes.json": (
        1_320,
        "cd5b6a5d7f0db4d9e0e0d21595c93701aeb70f0d2cd18a1c54f2f05bfc2e388e",
    ),
    "Buffalo_Daytime_V5_Portable_Handoff.md": (
        3_185,
        "aba5558cda8d5f2f8b919bc07720d99c319845e126d76e3d6837b59f785119bd",
    ),
}
_READER_FILES: Mapping[str, tuple[int, str]] = {
    "restore_review_snapshot.py": (
        13_042,
        "56d3d12ac8d58a3e2fb917141b68c0c1a282e87fb8e40d0718855ea9d424aa72",
    ),
    "portable_replay.py": (
        5_655,
        "d41657c1beea99feccbef599cfe5ac759e9097d1b82e7c6f1768d4f514b3bcab",
    ),
    "frozen_review_contract.py": (
        31_664,
        "e52d18a7dafbca8660978c42136297b24acb635d24405f3384ee3143a3bd4add",
    ),
}

_MATERIALIZED_EFFECTIVE = frozenset(
    {
        "combo_components",
        "combo_validation",
        "comparison_offer_references",
        "complete_combos",
        "current_identity_census",
        "current_additions_review",
        "owner_decisions",
        "price_comparisons",
    }
)

_ORIGINAL_IDENTITY_COHORT_TABLES = frozenset(
    {
        "catalog_coverage",
        "historical_catalog_snapshot",
        "historical_inventory_refresh",
        "complete_identity_evidence",
        "coverage_change_ledger",
    }
)

_ADDITION_CATALOG_EVIDENCE_FIELDS = frozenset(
    {
        "barcode_raw",
        "description_html_raw",
        "identity_metafields_available",
        "metafield_pagination_complete",
        "product_capture_end_utc",
        "product_capture_file",
        "product_capture_sha256",
        "product_capture_start_utc",
        "product_id",
        "product_metafields_raw",
        "product_title_raw",
        "selected_options_raw",
        "sku_raw",
        "variant_capture_end_utc",
        "variant_capture_file",
        "variant_capture_sha256",
        "variant_capture_start_utc",
        "variant_id",
        "variant_metafields_raw",
        "variant_title_raw",
        "vendor_raw",
    }
)

_FALSE_AUTHORITY_FIELDS = frozenset(
    {
        "mapping_approved",
        "price_approved",
        "import_ready",
        "current_price_activated",
        "future_price_activated",
        "auto_add_authorized",
        "quantity_or_import_eligible",
        "current_price_eligible",
        "new_mapping_approval",
        "mapping_approval",
        "price_approval",
        "purchasing_approval",
        "approved_identity_alias",
        "approved_alias",
        "approval_inferred",
        "approval",
        "schema_replacement_proposed",
        "approval_by_same_model_review",
        "approved",
        "approved_alias_created",
        "buying_authority_granted",
        "combo_auto_add",
        "cross_variant_or_all_supplier_generalization_allowed",
        "current_approval_claim",
        "historical_sales_transfer_allowed",
        "mapping_or_price_approval",
        "may_influence_normalized_price_projection",
        "new_mapping_approved",
        "pack_breaking_authorized",
        "price_ladder_selected",
        "price_ladder_eligible",
        "price_or_mapping_approval",
        "projection_authority",
        "quantity_import_eligible",
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
_ZERO_AUTHORITY_FIELDS = frozenset(
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
_NULL_APPLICATION_FIELDS = frozenset(
    {"application_offer_id", "application_price_id", "application_vendor_id"}
)
_COMPLETE_COMBO_SHARED_SOURCE_FIELDS = (
    "application_offer_id",
    "application_price_id",
    "application_vendor_id",
    "artifact_role",
    "current_price_activated",
    "effective_from",
    "effective_through",
    "future_price_activated",
    "mapping_approved",
    "price_approved",
    "source_file",
    "source_offer_id",
    "source_page",
    "source_sha256",
    "supplier_name_raw",
    "supplier_sku",
    "total_cost",
    "total_cost_basis",
)
_COMPLETE_COMBO_SOURCE_ONLY_NULL_FIELDS = (
    "physical_units_per_case",
    "qualifying_units_per_case",
    "retail_packs_per_case",
    "shopify_units_per_case",
)
_REMAINING_SOURCE_FIELDS = (
    "effective_from",
    "effective_through",
    "package_type",
    "physical_units_per_case",
    "printed_page",
    "size_text",
    "source_evidence",
    "source_file",
    "source_offer_id",
    "source_page",
    "source_period_status",
    "source_sha256",
    "split_availability",
    "split_fee_basis_inclusion",
    "supplier_description",
    "supplier_name_raw",
    "supplier_pack_count",
    "supplier_sku",
    "territory",
)
_FAMILY_SOURCE_FIELDS = (
    "assortment_line_terms",
    "containers_per_pack_raw",
    "effective_from",
    "effective_through",
    "full_existing_price_ladder",
    "package_type",
    "physical_units_per_case",
    "printed_page",
    "qualification_basis",
    "qualifying_units_per_case",
    "retail_packs_per_case",
    "shopify_units_per_case",
    "size_text",
    "source_evidence",
    "source_file",
    "source_offer_id",
    "source_page",
    "source_period_status",
    "source_sha256",
    "split_availability",
    "split_fee_basis_inclusion",
    "supplier_description",
    "supplier_name_raw",
    "supplier_pack_count",
    "supplier_sku",
    "territory",
    "vintage_raw",
)
_SOURCE_OFFER_CONTRACT_FIELDS = tuple(
    dict.fromkeys(
        (
            *_COMPLETE_COMBO_SHARED_SOURCE_FIELDS,
            *_COMPLETE_COMBO_SOURCE_ONLY_NULL_FIELDS,
            *_REMAINING_SOURCE_FIELDS,
            *(
                field
                for field in _FAMILY_SOURCE_FIELDS
                if field != "full_existing_price_ladder"
            ),
            "extraction_flags",
        )
    )
)
_PDF_SOURCE_TABLES = frozenset(
    {
        "additional_rejected_alternatives",
        "candidate_matches",
        "combo_components",
        "complete_combos",
        "comparison_offer_references",
        "normalized_price_contract_links",
        "normalized_price_contract_review",
        "price_comparisons",
        "price_ladders",
        "rejected_alternatives",
        "source_offers",
        "variant_offer_relationships_v5",
    }
)


def is_real_v5_a1_root(root_bytes: bytes) -> bool:
    """Return whether raw bytes select the one production A1 adapter."""

    return (
        len(root_bytes) == A1_ROOT_BYTES
        and hashlib.sha256(root_bytes).hexdigest() == A1_ROOT_SHA256
    )


def _fail_fields(value: Mapping[str, Any], expected: frozenset[str], context: str) -> None:
    if set(value) != set(expected):
        raise ReviewPackageError("A1_SCHEMA_MISMATCH", f"{context} fields differ")


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReviewPackageError("A1_SCHEMA_MISMATCH", f"{context} must be an object")
    return value


def _sequence(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReviewPackageError("A1_SCHEMA_MISMATCH", f"{context} must be an array")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewPackageError("A1_SCHEMA_MISMATCH", f"{context} must be nonblank text")
    return value


def _count(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewPackageError("A1_SCHEMA_MISMATCH", f"{context} must be a nonnegative integer")
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _shard_limits(limits: ReviewLimits) -> ReviewLimits:
    return replace(limits, max_entries=limits.max_a1_archive_entries)


def _validate_root(root_bytes: bytes, limits: ReviewLimits) -> dict[str, Any]:
    if not is_real_v5_a1_root(root_bytes):
        raise ReviewPackageError("A1_ROOT_IDENTITY_MISMATCH", "raw A1 root digest differs")
    root = _mapping(
        _parse_json_bytes(root_bytes, path=PORTABLE_ROOT, limits=limits),
        "A1 root",
    )
    _fail_fields(root, _ROOT_FIELDS, "A1 root")
    expected_identity = {
        "format": "BUFFALO_PRIVATE_REVIEW_SNAPSHOT_V1",
        "source_revision": "V5",
        "semantic_addendum_revision": A1_ADAPTER,
        "package_id": A1_PACKAGE_ID,
        "authority": "UNAPPROVED_REVIEW",
    }
    if any(root.get(key) != value for key, value in expected_identity.items()):
        raise ReviewPackageError("A1_ROOT_IDENTITY_MISMATCH", "A1 root identity differs")
    for key, expected in (
        ("controls", A1_CONTROLS_SHA256),
        ("tables", A1_TABLE_DESCRIPTOR_SHA256),
        ("embedded_files", A1_EMBEDDED_DESCRIPTOR_SHA256),
        ("source_evidence", A1_SOURCE_DESCRIPTOR_SHA256),
        ("external_original_files", A1_EXTERNAL_DESCRIPTOR_SHA256),
    ):
        if _digest(root.get(key)) != expected:
            raise ReviewPackageError("A1_CONTRACT_MISMATCH", f"code-owned {key} contract differs")
    flags = _mapping(root.get("review_only_flags"), "review_only_flags")
    expected_flags = {
        "mapping_approval": False,
        "price_approval": False,
        "import_ready": False,
        "operational_effects": 0,
        "application_changes": 0,
        "shopify_writes": 0,
        "sales_changes": 0,
        "orders": 0,
        "supplier_messages": 0,
    }
    if flags != expected_flags:
        raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "A1 review-only flags differ")
    return root


def _validate_seal(source: _PackageSource, root_bytes: bytes) -> dict[str, Any]:
    seal_bytes = _read_bounded(source, PORTABLE_SEAL, source.limits.max_manifest_bytes)
    if len(seal_bytes) != A1_SEAL_BYTES or hashlib.sha256(seal_bytes).hexdigest() != A1_SEAL_SHA256:
        raise ReviewPackageError("A1_SEAL_MISMATCH", "raw A1 seal digest differs", path=PORTABLE_SEAL)
    seal = _mapping(
        _parse_json_bytes(seal_bytes, path=PORTABLE_SEAL, limits=source.limits),
        "A1 seal",
    )
    _fail_fields(seal, _SEAL_FIELDS, "A1 seal")
    if (
        seal.get("format") != "BUFFALO_PRIVATE_REVIEW_SNAPSHOT_SEAL_V1"
        or seal.get("package_id") != A1_PACKAGE_ID
        or seal.get("authority")
        != "UNAPPROVED_REVIEW; integrity seal is not approval or a digital signature"
    ):
        raise ReviewPackageError("A1_SEAL_MISMATCH", "A1 seal identity differs")
    root_ref = _mapping(seal.get("root_manifest"), "root_manifest")
    if root_ref != {"file": PORTABLE_ROOT, "bytes": len(root_bytes), "sha256": A1_ROOT_SHA256}:
        raise ReviewPackageError("A1_SEAL_MISMATCH", "sealed root reference differs")
    archives = _sequence(seal.get("archives"), "seal archives")
    expected_archives = [
        {"file": name, "bytes": size, "sha256": digest, "role": role}
        for name, size, digest, role in _ARCHIVES
    ]
    if archives != expected_archives:
        raise ReviewPackageError("A1_SEAL_MISMATCH", "sealed archive descriptors differ")
    reader_files = _sequence(seal.get("review_reader_files"), "review reader files")
    expected_readers = [
        {"file": name, "bytes": size, "sha256": digest}
        for name, (size, digest) in _READER_FILES.items()
    ]
    if reader_files != expected_readers:
        raise ReviewPackageError("A1_SEAL_MISMATCH", "sealed reader-file descriptors differ")
    return seal


def _validate_top_level(source: _PackageSource) -> None:
    expected = {
        PORTABLE_ROOT,
        PORTABLE_SEAL,
        *_ARCHIVE_BY_NAME,
        *_AUXILIARY_FILES,
    }
    if set(source.names) != expected:
        extra = sorted(set(source.names) - expected)
        missing = sorted(expected - set(source.names))
        detail = extra[0] if extra else (missing[0] if missing else None)
        raise ReviewPackageError(
            "UNDECLARED_FILE" if extra else "MISSING_FILE",
            "A1 top-level file set differs",
            path=detail,
        )
    for name, (expected_bytes, expected_sha) in _AUXILIARY_FILES.items():
        observed_sha, observed_bytes = _stream_sha256(source, name)
        if (observed_bytes, observed_sha) != (expected_bytes, expected_sha):
            raise ReviewPackageError("FILE_INTEGRITY_MISMATCH", "A1 handoff bytes differ", path=name)


def _sealed_archive_snapshot(source: _PackageSource, name: str) -> bytes:
    """Return immutable bytes only after the exact outer ZIP seal has passed."""

    expected = _ARCHIVE_BY_NAME.get(name)
    if expected is None:
        raise ReviewPackageError("A1_SCHEMA_MISMATCH", "A1 archive is undeclared", path=name)
    expected_bytes, expected_sha, _ = expected
    payload = _read_bounded(source, name, source.limits.max_entry_bytes)
    if len(payload) != expected_bytes or hashlib.sha256(payload).hexdigest() != expected_sha:
        raise ReviewPackageError(
            "ARCHIVE_SEAL_MISMATCH", "A1 archive bytes differ", path=name
        )
    return payload


def _validate_table_specs(
    root: Mapping[str, Any], limits: ReviewLimits
) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[tuple[str, str], dict[str, Any]]]:
    records = _sequence(root.get("tables"), "tables")
    if len(records) != _EXPECTED_TABLES:
        raise ReviewPackageError("TABLE_COUNT_MISMATCH", "A1 logical table count differs")
    namespace_counts: Counter[str] = Counter()
    namespace_rows: Counter[str] = Counter()
    namespace_parts: Counter[str] = Counter()
    logical_counts: Counter[str] = Counter()
    restored_keys: dict[str, str] = {}
    members_by_archive: dict[str, set[str]] = defaultdict(set)
    part_by_member: dict[tuple[str, str], dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for table_number, raw in enumerate(records, start=1):
        table = _mapping(raw, f"table {table_number}")
        if not _TABLE_COMMON_FIELDS.issubset(table):
            raise ReviewPackageError("A1_SCHEMA_MISMATCH", "table descriptor fields are incomplete")
        namespace = _text(table.get("namespace"), "table namespace")
        if namespace not in _EXPECTED_NAMESPACE_TOTALS:
            raise ReviewPackageError("A1_SCHEMA_MISMATCH", "table namespace is unsupported")
        logical_name = _text(table.get("logical_name"), "logical table name")
        if table.get("encoding") != _ENCODING:
            raise ReviewPackageError("A1_SCHEMA_MISMATCH", "table encoding differs", table=logical_name)
        total_rows = _count(table.get("total_rows"), f"{logical_name}.total_rows")
        if total_rows > limits.max_rows_per_table:
            raise ReviewPackageError("TOO_MANY_ROWS", "A1 table exceeds row limit", table=logical_name)
        _require_sha256(table.get("canonical_jsonl_sha256"), path=logical_name)
        _require_sha256(table.get("raw_sha256"), path=logical_name)
        restored_path = _validated_member_name(
            _text(table.get("restored_path"), "restored path"), limits
        )
        collision = restored_path.casefold()
        if collision in restored_keys:
            raise ReviewPackageError(
                "PATH_COLLISION",
                f"restored path collides with {restored_keys[collision]}",
                path=restored_path,
            )
        restored_keys[collision] = restored_path
        parts = _sequence(table.get("parts"), f"{logical_name}.parts")
        if not parts:
            raise ReviewPackageError("A1_SCHEMA_MISMATCH", "A1 table has no part", table=logical_name)
        next_row = 1
        observed_bytes = 0
        normalized_parts: list[dict[str, Any]] = []
        for ordinal, raw_part in enumerate(parts, start=1):
            part = _mapping(raw_part, f"{logical_name} part {ordinal}")
            _fail_fields(part, _PART_FIELDS, f"{logical_name} part")
            archive = _validated_member_name(_text(part.get("archive"), "part archive"), limits)
            path = _validated_member_name(_text(part.get("path"), "part path"), limits)
            if archive not in _ARCHIVE_BY_NAME or archive == _INDEX_ARCHIVE:
                raise ReviewPackageError("A1_SCHEMA_MISMATCH", "table part archive differs", path=archive)
            row_count = _count(part.get("row_count"), "part row_count")
            byte_count = _count(part.get("bytes"), "part bytes")
            if part.get("ordinal") != ordinal or part.get("first_logical_row") != next_row:
                raise ReviewPackageError(
                    "PORTABLE_PART_ORDER_MISMATCH",
                    "A1 table parts overlap or contain a gap",
                    table=logical_name,
                )
            _require_sha256(part.get("sha256"), path=path)
            if path.casefold() in {name.casefold() for name in members_by_archive[archive]}:
                raise ReviewPackageError("PATH_COLLISION", "A1 archive member is reused", path=path)
            members_by_archive[archive].add(path)
            key = (archive, path)
            if key in part_by_member:
                raise ReviewPackageError("DUPLICATE_DECLARATION", "A1 table part is duplicated", path=path)
            normalized = dict(part)
            normalized["table_number"] = table_number
            part_by_member[key] = normalized
            normalized_parts.append(normalized)
            next_row += row_count
            observed_bytes += byte_count
        if next_row - 1 != total_rows:
            raise ReviewPackageError("ROW_COUNT_MISMATCH", "A1 table part counts differ", table=logical_name)
        if "bytes" in table and table.get("bytes") != observed_bytes:
            raise ReviewPackageError("SIZE_MISMATCH", "A1 table part bytes differ", table=logical_name)
        namespace_counts[namespace] += 1
        namespace_rows[namespace] += total_rows
        namespace_parts[namespace] += len(parts)
        logical_counts[logical_name] += 1
        normalized_table = dict(table)
        normalized_table["parts"] = normalized_parts
        result.append(normalized_table)
    observed_totals = {
        namespace: (
            namespace_counts[namespace],
            namespace_rows[namespace],
            namespace_parts[namespace],
        )
        for namespace in _EXPECTED_NAMESPACE_TOTALS
    }
    if observed_totals != dict(_EXPECTED_NAMESPACE_TOTALS):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 namespace controls differ")
    if sum(namespace_rows.values()) != _EXPECTED_ROWS or sum(namespace_parts.values()) != _EXPECTED_PARTS:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 table/part controls differ")
    duplicate_names = {name for name, count in logical_counts.items() if count > 1}
    if duplicate_names != {"exact_change_log"} or logical_counts["exact_change_log"] != 2:
        raise ReviewPackageError("DUPLICATE_TABLE", "A1 namespace collision contract differs")
    return result, members_by_archive, part_by_member


def _validate_embedded_specs(
    root: Mapping[str, Any],
    limits: ReviewLimits,
    members_by_archive: dict[str, set[str]],
    restored_paths: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records = _sequence(root.get("embedded_files"), "embedded_files")
    if len(records) != _EXPECTED_EMBEDDED:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 embedded-file count differs")
    roles: Counter[str] = Counter()
    by_restored: dict[str, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    expected_fields = frozenset({"archive", "path", "restored_path", "role", "bytes", "sha256"})
    for raw in records:
        row = _mapping(raw, "embedded file")
        _fail_fields(row, expected_fields, "embedded file")
        archive = _validated_member_name(_text(row.get("archive"), "embedded archive"), limits)
        path = _validated_member_name(_text(row.get("path"), "embedded path"), limits)
        restored = _validated_member_name(
            _text(row.get("restored_path"), "embedded restored path"), limits
        )
        if archive not in _ARCHIVE_BY_NAME or archive == _INDEX_ARCHIVE:
            raise ReviewPackageError("A1_SCHEMA_MISMATCH", "embedded archive differs", path=archive)
        collision = path.casefold()
        if collision in {name.casefold() for name in members_by_archive[archive]}:
            raise ReviewPackageError("PATH_COLLISION", "embedded archive member is reused", path=path)
        members_by_archive[archive].add(path)
        restored_collision = restored.casefold()
        if restored_collision in restored_paths:
            raise ReviewPackageError("PATH_COLLISION", "embedded restored path is reused", path=restored)
        restored_paths.add(restored_collision)
        _count(row.get("bytes"), "embedded bytes")
        _require_sha256(row.get("sha256"), path=path)
        role = _text(row.get("role"), "embedded role")
        roles[role] += 1
        by_restored[restored] = dict(row)
        result.append(dict(row))
    if roles != Counter(
        {
            "METADATA_OR_CAPTURE_EVIDENCE": 572,
            "DAYTIME_VALIDATION": 12,
            "OBSERVED_FIELD_DICTIONARY": 1,
            "DAYTIME_ADDENDUM_EVIDENCE": 79,
            "HUMAN_REVIEW_PREVIEW": 2_020,
        }
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 embedded roles differ")
    return result, by_restored


def _external_original_contracts(root: Mapping[str, Any], limits: ReviewLimits) -> dict[str, dict[str, Any]]:
    rows = _sequence(root.get("external_original_files"), "external_original_files")
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _mapping(raw, "external original")
        name = _validated_member_name(_text(row.get("file"), "external file"), limits)
        if name in result:
            raise ReviewPackageError("DUPLICATE_DECLARATION", "external original is duplicated", path=name)
        _count(row.get("bytes"), "external bytes")
        _require_sha256(row.get("sha256"), path=name)
        result[name] = dict(row)
    if len(result) != 13:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 external evidence count differs")
    return result


def _validate_source_evidence(
    root: Mapping[str, Any],
    limits: ReviewLimits,
    embedded_by_restored: Mapping[str, Mapping[str, Any]],
    external_contracts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = _sequence(root.get("source_evidence"), "source_evidence")
    if len(rows) != _EXPECTED_SOURCE_EVIDENCE:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 source-evidence count differs")
    availability: Counter[str] = Counter()
    embedded_paths: set[str] = set()
    external_paths: set[tuple[str, str]] = set()
    pdf_rows = 0
    for raw in rows:
        row = _mapping(raw, "source evidence")
        status = _text(row.get("availability"), "source availability")
        if status not in {"EMBEDDED_REAL_BYTES", "EXTERNAL_EXISTING_BUNDLE_BYTES_VERIFIED"}:
            raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "source availability differs")
        availability[status] += 1
        digest = _require_sha256(row.get("evidence_content_sha256"), path="source evidence")
        if "bytes" in row:
            _count(row.get("bytes"), "source evidence bytes")
        source_file = row.get("source_pdf_file")
        if source_file is not None:
            source_file = _text(source_file, "source PDF file")
            pdf = external_contracts.get(source_file)
            if pdf is None or "physical_pages" not in pdf:
                raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "source PDF is not declared")
            if row.get("source_pdf_sha256") != pdf.get("sha256"):
                raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "source PDF hash differs")
            page = _count(row.get("physical_page"), "source physical page")
            if page <= 0 or page > pdf.get("physical_pages"):
                raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "source PDF page is out of range")
            pdf_rows += 1
        delivery_archive = _text(row.get("delivery_archive"), "source delivery archive")
        delivery_path = _validated_member_name(
            _text(row.get("delivery_path"), "source delivery path"), limits
        )
        if status == "EMBEDDED_REAL_BYTES":
            portable_path = _validated_member_name(
                _text(row.get("portable_path"), "source portable path"), limits
            )
            embedded = embedded_by_restored.get(portable_path)
            if (
                embedded is None
                or embedded.get("sha256") != digest
                or ("bytes" in row and embedded.get("bytes") != row.get("bytes"))
                or embedded.get("archive") != delivery_archive
            ):
                raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "embedded source join differs")
            embedded_paths.add(portable_path)
        else:
            bundle = external_contracts.get(delivery_archive)
            if bundle is None or "physical_pages" in bundle:
                raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "external page bundle is undeclared")
            external_paths.add((delivery_archive, delivery_path))
    if availability != Counter(
        {"EMBEDDED_REAL_BYTES": 680, "EXTERNAL_EXISTING_BUNDLE_BYTES_VERIFIED": 174}
    ) or pdf_rows != 191:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 source-evidence controls differ")
    return {
        "embedded_source_evidence": len(embedded_paths),
        "external_page_evidence": len(external_paths),
        "source_rows_with_pdf_page": pdf_rows,
    }


def _native_json_type(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if isinstance(value, Decimal):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    raise ReviewPackageError(
        "A1_SCHEMA_MISMATCH",
        f"unsupported native JSON type {type(value).__name__}",
    )


def _validate_daytime_dictionary_contract(
    rows: Sequence[Mapping[str, Any]],
    tables: Mapping[str, PackageTable],
) -> dict[str, Any]:
    """Reconcile the additive dictionary with all 24 observed sidecar schemas."""

    if len(rows) != _EXPECTED_DAYTIME_DICTIONARY_FIELDS:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "A1 daytime dictionary field count differs"
        )
    daytime_tables = {
        (
            key.split("__", 1)[1]
            if key.startswith("daytime_addendum__")
            else key
        ): table
        for key, table in tables.items()
        if table.path.startswith("daytime_addendum/")
    }
    if len(daytime_tables) != _EXPECTED_NAMESPACE_TOTALS["daytime_addendum"][0]:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "A1 daytime table inventory differs"
        )
    observed: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for table_name, table in daytime_tables.items():
        for row in table.rows:
            for field, value in row.items():
                observed[(table_name, field)].add(_native_json_type(value))

    declared: dict[tuple[str, str], set[str]] = {}
    for row_number, row in enumerate(rows, start=1):
        if set(row) != set(_DAYTIME_DICTIONARY_FIELDS):
            raise ReviewPackageError(
                "A1_SCHEMA_MISMATCH",
                "daytime dictionary row fields differ",
                row=row_number,
            )
        table_name = _text(row.get("table"), "daytime dictionary table")
        field = _text(row.get("field"), "daytime dictionary field")
        key = (table_name, field)
        if table_name not in daytime_tables or key in declared:
            raise ReviewPackageError(
                "TABLE_KEY_NOT_UNIQUE",
                "daytime dictionary key is unknown or duplicated",
                row=row_number,
            )
        types = row.get("observed_native_types")
        if (
            not isinstance(types, list)
            or not types
            or any(item not in {"bool", "dict", "float", "int", "list", "null", "str"} for item in types)
            or types != sorted(set(types))
        ):
            raise ReviewPackageError(
                "A1_SCHEMA_MISMATCH",
                "daytime dictionary native types differ",
                row=row_number,
            )
        if (
            row.get("authority") != "UNAPPROVED_REVIEW"
            or row.get("role")
            != "ADDITIVE_REVIEW_OR_TRANSPORT_SIDECAR; NOT_APPLICATION_SCHEMA"
        ):
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "daytime dictionary authority differs",
                row=row_number,
            )
        for field_name in ("join_semantics", "value_handling"):
            _text(row.get(field_name), f"daytime dictionary {field_name}")
        declared[key] = set(types)
    if set(declared) != set(observed):
        raise ReviewPackageError(
            "A1_SCHEMA_MISMATCH", "daytime dictionary field inventory differs"
        )
    mismatches = [key for key in declared if declared[key] != observed[key]]
    if mismatches:
        table_name, field = sorted(mismatches)[0]
        raise ReviewPackageError(
            "A1_SCHEMA_MISMATCH",
            f"daytime dictionary native types differ for {field}",
            table=table_name,
        )
    return {
        "daytime_tables": len(daytime_tables),
        "daytime_dictionary_fields": len(declared),
        "daytime_native_type_profiles": "PASS",
    }


def _verify_external_pdfs(
    root_path: str | Path | None,
    contracts: Mapping[str, Mapping[str, Any]],
    limits: ReviewLimits,
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    pdf_contracts = {name: row for name, row in contracts.items() if "physical_pages" in row}
    bundle_contracts = {name: row for name, row in contracts.items() if "physical_pages" not in row}
    unavailable = [f"SOURCE_PAGE_ARCHIVE:{name}" for name in sorted(bundle_contracts)]
    status: dict[str, dict[str, Any]] = {}
    if root_path is None:
        unavailable.append("ORIGINAL_SUPPLIER_PDFS")
        for name, row in pdf_contracts.items():
            status[name] = {
                "availability": "UNAVAILABLE_NOT_SUPPLIED",
                "bytes": row["bytes"],
                "sha256": row["sha256"],
                "physical_pages": row["physical_pages"],
                "page_range_basis": A1_PDF_PAGE_RANGE_BASIS,
            }
        return status, tuple(sorted(unavailable))
    with _DirectorySource(Path(root_path), limits) as evidence:
        if set(evidence.names) != set(pdf_contracts):
            extra = sorted(set(evidence.names) - set(pdf_contracts))
            missing = sorted(set(pdf_contracts) - set(evidence.names))
            raise ReviewPackageError(
                "UNDECLARED_FILE" if extra else "MISSING_FILE",
                "external PDF directory differs",
                path=(extra or missing)[0],
            )
        for name, row in sorted(pdf_contracts.items()):
            digest, size = _stream_sha256(evidence, name)
            if (size, digest) != (row["bytes"], row["sha256"]):
                raise ReviewPackageError("SOURCE_EVIDENCE_MISMATCH", "external PDF bytes differ", path=name)
            status[name] = {
                "availability": "VERIFIED_ORIGINAL_BYTES",
                "bytes": size,
                "sha256": digest,
                "physical_pages": row["physical_pages"],
                "page_range_basis": A1_PDF_PAGE_RANGE_BASIS,
            }
    return status, tuple(sorted(unavailable))


class _ProjectionBuilder:
    """Collect bounded relationship indexes while every table row is streamed."""

    def __init__(self, pdf_contracts: Mapping[str, Mapping[str, Any]]):
        self.pdf_contracts = pdf_contracts
        self.source_offers: dict[str, tuple[str, Any, str, str, int]] = {}
        self.source_offer_record_hashes: dict[str, str] = {}
        self.source_offer_contracts: dict[str, tuple[Any, ...]] = {}
        self.comparison_offer_details: dict[
            str, tuple[str, Any, str, str, frozenset[int], frozenset[str]]
        ] = {}
        self.source_offer_details: dict[str, dict[str, Any]] = {}
        self.source_sku_leading_zero = 0
        self.source_sku_nondigit = 0
        self.candidate_pairs: set[tuple[str, str]] = set()
        self.candidate_record_hashes: dict[tuple[str, str], str] = {}
        self.eligible_pairs: set[tuple[str, str]] = set()
        self.base_rejected_pairs: set[tuple[str, str]] = set()
        self.additional_rejected_pairs: set[tuple[str, str]] = set()
        self.base_rejected_facts: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.additional_rejected_facts: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.owner_question_hashes: dict[str, str] = {}
        self.ordinary_tiers: set[str] = set()
        self.comparison_tiers: set[str] = set()
        self.registry_tiers: set[str] = set()
        self.validation_tiers: set[str] = set()
        self.comparison_offers: set[str] = set()
        self.tier_details: dict[str, tuple[str, str, Any, str, str, int]] = {}
        self.tier_record_hashes: dict[str, str] = {}
        self.normalized_links: dict[
            int, tuple[str, str, str, str, int, tuple[str, ...]]
        ] = {}
        self.normalized_review_details: dict[int, tuple[str, Any, str, int]] = {}
        self.normalized_type_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
        self.normalized_rows = 0
        self.tiers_by_offer: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        self.rejected_by_variant: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        self.original_identity_cohorts: defaultdict[str, set[str]] = defaultdict(set)
        self.table_rows: Counter[tuple[str, str]] = Counter()

    def _authority(self, namespace: str, name: str, row: Mapping[str, Any], row_number: int) -> None:
        for field in _FALSE_AUTHORITY_FIELDS:
            if field not in row:
                continue
            value = row[field]
            count_zero = name == "supplier_coverage_controls_v5" and type(value) is int and value == 0
            negative_label = field == "mapping_approval" and value == "UNAPPROVED"
            if value is not False and not count_zero and not negative_label:
                raise ReviewPackageError(
                    "UNAUTHORIZED_APPROVAL_CLAIM",
                    f"{field} must remain false",
                    table=f"{namespace}__{name}",
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
                table=f"{namespace}__{name}",
                row=row_number,
            )
        if name == "variant_offer_relationships_v5" and row.get("tier_selection") is not None:
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "relationship tier_selection must remain explicit null",
                table=f"{namespace}__{name}",
                row=row_number,
            )
        if name == "supplier_vocabulary_candidates_v5" and row.get(
            "approved_vendor_uuid_crosswalk"
        ) is not None:
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "approved vendor UUID crosswalk must remain explicit null",
                table=f"{namespace}__{name}",
                row=row_number,
            )
        if name == "requests__additional_synthetic_scenarios" and (
            row.get("activation") is not False or row.get("selected_tier") is not None
        ):
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "synthetic scenario activation must remain false with no selected Tier",
                table=f"{namespace}__{name}",
                row=row_number,
            )
        if name in {
            "candidate_matches",
            "rejected_alternatives",
            "targeted_candidate_after_records",
        } and row.get("approval_status") != "UNAPPROVED_CANDIDATE":
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "candidate approval status must remain unapproved",
                table=f"{namespace}__{name}",
                row=row_number,
            )
        negative_authority_text = {
            "source_offers": ("source_approval_text", "UNAPPROVED_CANDIDATE"),
            "complete_combos": ("source_approval_text", "UNAPPROVED_CANDIDATE"),
            "price_ladders": (
                "source_approval_text",
                "UNAPPROVED_NO_TIER_SELECTED",
            ),
            "historical_catalog_snapshot": (
                "price_authority",
                "NOT_VERIFIED_CURRENT",
            ),
        }
        if name in negative_authority_text:
            field, expected = negative_authority_text[name]
            if row.get(field) != expected:
                raise ReviewPackageError(
                    "UNAUTHORIZED_APPROVAL_CLAIM",
                    f"{field} must retain its exact negative-authority label",
                    table=f"{namespace}__{name}",
                    row=row_number,
                )
        for field in _NULL_APPLICATION_FIELDS:
            if field in row and row[field] is not None:
                raise ReviewPackageError(
                    "UNAUTHORIZED_OPERATIONAL_EFFECT",
                    f"{field} must remain null",
                    table=f"{namespace}__{name}",
                    row=row_number,
                )
        for field in _ZERO_AUTHORITY_FIELDS:
            if field in row and (type(row[field]) is not int or row[field] != 0):
                raise ReviewPackageError(
                    "UNAUTHORIZED_OPERATIONAL_EFFECT",
                    f"{field} must remain exact integer zero",
                    table=f"{namespace}__{name}",
                    row=row_number,
                )

    def _source_reference(
        self,
        row: Mapping[str, Any],
        *,
        name: str,
        table: str,
        row_number: int,
    ) -> None:
        filename = row.get("source_file")
        digest = row.get("source_sha256")
        if name not in _PDF_SOURCE_TABLES:
            return
        if not isinstance(filename, str) or filename not in self.pdf_contracts:
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "table source PDF is not declared",
                table=table,
                row=row_number,
            )
        expected = self.pdf_contracts[filename]
        if (
            name != "normalized_price_contract_review"
            and digest != expected["sha256"]
        ) or (
            name == "normalized_price_contract_review"
            and digest is not None
            and digest != expected["sha256"]
        ):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "table source PDF hash differs",
                table=table,
                row=row_number,
            )
        pages = (
            row.get("source_pages")
            if name == "comparison_offer_references"
            else [row.get("source_page", row.get("physical_page"))]
        )
        if (
            not isinstance(pages, list)
            or not pages
            or any(
                type(page) is not int or page <= 0 or page > expected["physical_pages"]
                for page in pages
            )
        ):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "table source PDF page is out of range",
                table=table,
                row=row_number,
            )

    def observe(self, namespace: str, name: str, row: dict[str, Any], row_number: int) -> None:
        self.table_rows[(namespace, name)] += 1
        self._authority(namespace, name, row, row_number)
        self._source_reference(
            row,
            name=name,
            table=f"{namespace}__{name}",
            row_number=row_number,
        )
        if namespace != "effective_v5":
            return
        if name in _ORIGINAL_IDENTITY_COHORT_TABLES:
            variant = _text(row.get("variant_id"), f"{name} Variant ID")
            cohort = self.original_identity_cohorts[name]
            if variant in cohort:
                raise ReviewPackageError(
                    "TABLE_KEY_NOT_UNIQUE",
                    "original-cohort Variant ID is duplicated",
                    table=name,
                    row=row_number,
                )
            cohort.add(variant)
        if name == "source_offers":
            offer = _text(row.get("source_offer_id"), "source offer ID")
            sku = row.get("supplier_sku")
            if sku is not None and (
                not isinstance(sku, str) or not sku.strip()
            ):
                raise ReviewPackageError(
                    "A1_SCHEMA_MISMATCH", "source supplier SKU must be text or null"
                )
            value = (
                _text(row.get("supplier_name_raw"), "source supplier"),
                sku,
                _text(row.get("source_file"), "source file"),
                _require_sha256(row.get("source_sha256"), path=offer),
                _count(row.get("source_page"), "source page"),
            )
            if offer in self.source_offers:
                raise ReviewPackageError("TABLE_KEY_NOT_UNIQUE", "source offer ID is duplicated", table=name)
            self.source_offers[offer] = value
            self.source_offer_record_hashes[offer] = canonical_record_sha256(row)
            if any(field not in row for field in _SOURCE_OFFER_CONTRACT_FIELDS):
                raise ReviewPackageError(
                    "A1_SCHEMA_MISMATCH",
                    "source offer lacks a code-owned validation field",
                    table=name,
                    row=row_number,
                )
            self.source_offer_contracts[offer] = tuple(
                row[field] for field in _SOURCE_OFFER_CONTRACT_FIELDS
            )
            if isinstance(sku, str):
                self.source_sku_leading_zero += sku.startswith("0")
                self.source_sku_nondigit += not sku.isdigit()
            self.source_offer_details[offer] = {
                "vendor": row.get("supplier_name_raw"),
                "supplier_code": row.get("supplier_sku"),
                "supplier_description": row.get("supplier_description"),
                "program_type": row.get("package_type"),
                "shopify_units_per_case": row.get("shopify_units_per_case"),
                "qualifying_units_per_case": row.get("qualifying_units_per_case"),
                "physical_units_per_case": row.get("physical_units_per_case"),
                "retail_packs_per_case": row.get("retail_packs_per_case"),
                "source_file": row.get("source_file"),
                "source_page": row.get("source_page"),
                "source_sha256": row.get("source_sha256"),
                "source_evidence": row.get("source_evidence"),
            }
        elif name in {"candidate_matches", "candidate_projection_eligibility"}:
            offer_field = "offer_id" if name == "candidate_matches" else "source_offer_id"
            pair = (
                _text(row.get("variant_id"), "candidate Variant ID"),
                _text(row.get(offer_field), "candidate source offer ID"),
            )
            target = self.candidate_pairs if name == "candidate_matches" else self.eligible_pairs
            if pair in target:
                raise ReviewPackageError(
                    "TABLE_KEY_NOT_UNIQUE", "candidate pair is duplicated", table=name
                )
            target.add(pair)
            if name == "candidate_matches":
                self.candidate_record_hashes[pair] = canonical_record_sha256(row)
        elif name == "owner_question_batch":
            question = _text(row.get("question_id"), "owner question ID")
            if question in self.owner_question_hashes:
                raise ReviewPackageError(
                    "TABLE_KEY_NOT_UNIQUE", "owner question ID is duplicated", table=name
                )
            self.owner_question_hashes[question] = canonical_record_sha256(row)
        elif name == "price_ladders":
            tier = _text(row.get("source_tier_id"), "source Tier ID")
            offer = _text(row.get("source_offer_id"), "price-ladder offer ID")
            if tier in self.ordinary_tiers:
                raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "ordinary Tier ID is duplicated")
            self.ordinary_tiers.add(tier)
            self.tier_record_hashes[tier] = canonical_record_sha256(row)
            self.tier_details[tier] = (
                offer,
                _text(row.get("supplier_name_raw"), "price-ladder supplier"),
                row.get("supplier_sku"),
                _text(row.get("source_file"), "price-ladder source file"),
                _require_sha256(row.get("source_sha256"), path=tier),
                _count(row.get("source_page"), "price-ladder source page"),
            )
            self.tiers_by_offer[offer].append(
                {
                    "source_tier_id": tier,
                    "tier_type": row.get("normalized_tier_type"),
                    "break_quantity": row.get("break_quantity_raw"),
                    "break_unit": row.get("break_unit_raw"),
                    "case_price_printed": row.get("case_price_printed"),
                    "printed_page": row.get("printed_page"),
                    "individual_price_printed": row.get("individual_price_printed"),
                    "retail_pack_price_printed": row.get("retail_pack_price_printed"),
                    "unit_pack_bottle_price_printed": row.get(
                        "unit_pack_bottle_price_printed"
                    ),
                    "split_inclusive_price_printed": row.get("split_inclusive_price_printed"),
                    "total_cost": row.get("total_cost"),
                    "total_cost_basis": row.get("total_cost_basis"),
                    "source_evidence": row.get("source_evidence"),
                    "source_file": row.get("source_file"),
                    "source_page": row.get("source_page"),
                    "source_sha256": row.get("source_sha256"),
                }
            )
        elif name == "comparison_offer_references":
            offer = _text(row.get("source_offer_id"), "comparison offer ID")
            if offer in self.comparison_offers:
                raise ReviewPackageError("TABLE_KEY_NOT_UNIQUE", "comparison offer is duplicated")
            self.comparison_offers.add(offer)
            pages = row.get("source_pages")
            tiers = row.get("source_tier_ids")
            if (
                not isinstance(pages, list)
                or any(type(page) is not int or page <= 0 for page in pages)
                or not isinstance(tiers, list)
                or any(not isinstance(tier, str) or not tier for tier in tiers)
            ):
                raise ReviewPackageError(
                    "A1_SCHEMA_MISMATCH", "comparison parent pages/Tiers differ"
                )
            self.comparison_offer_details[offer] = (
                _text(row.get("supplier_name_raw"), "comparison supplier"),
                row.get("supplier_sku"),
                _text(row.get("source_file"), "comparison source file"),
                _require_sha256(row.get("source_sha256"), path=offer),
                frozenset(pages),
                frozenset(tiers),
            )
        elif name == "price_comparisons":
            tier = _text(row.get("source_tier_id"), "comparison Tier ID")
            offer = _text(row.get("source_offer_id"), "comparison price parent")
            if tier in self.comparison_tiers:
                raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "comparison Tier ID is duplicated")
            self.comparison_tiers.add(tier)
            self.tier_record_hashes[tier] = canonical_record_sha256(row)
            self.tier_details[tier] = (
                offer,
                _text(row.get("supplier_name_raw"), "comparison price supplier"),
                row.get("supplier_sku"),
                _text(row.get("source_file"), "comparison source file"),
                _require_sha256(row.get("source_sha256"), path=tier),
                _count(row.get("source_page"), "comparison source page"),
            )
        elif name == "tier_identity_registry":
            tier = _text(row.get("source_tier_id"), "registry Tier ID")
            if tier in self.registry_tiers:
                raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "registry Tier ID is duplicated")
            self.registry_tiers.add(tier)
        elif name == "price_row_validation":
            tier = _text(row.get("source_tier_id"), "validation Tier ID")
            if tier in self.validation_tiers:
                raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "validation Tier ID is duplicated")
            self.validation_tiers.add(tier)
        elif name == "normalized_price_contract_links":
            position = _count(row.get("normalized_table_row_number_1based"), "normalized position")
            if position <= 0 or position in self.normalized_links:
                raise ReviewPackageError("NORMALIZED_POSITION_MISMATCH", "normalized link position is duplicated")
            if row.get("tier_selected") is not False:
                raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "normalized Tier cannot be selected")
            tier = _text(row.get("source_tier_id"), "normalized link Tier ID")
            if row.get("projection_row_id") != tier:
                raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "projection row ID differs from Tier ID")
            variants = row.get("candidate_variant_ids")
            if not isinstance(variants, list) or any(not isinstance(item, str) for item in variants):
                raise ReviewPackageError("A1_SCHEMA_MISMATCH", "candidate_variant_ids must be text array")
            self.normalized_links[position] = (
                tier,
                _text(row.get("source_offer_id"), "normalized source offer"),
                _text(row.get("source_file"), "normalized source file"),
                _require_sha256(row.get("source_sha256"), path=tier),
                _count(row.get("source_page"), "normalized source page"),
                tuple(variants),
            )
        elif name == "normalized_price_contract_review":
            self.normalized_rows += 1
            if set(row) != set(PRICE_BOOK_HEADERS):
                raise ReviewPackageError(
                    "NORMALIZED_FIELD_SET_MISMATCH",
                    "normalized review row must contain exactly 27 contract fields",
                    table=name,
                    row=row_number,
                )
            _validate_v5_normalized_contract_row(
                row,
                row_number=row_number,
                allow_null_supplier_sku=True,
                allow_null_supplier_description=True,
            )
            if any(row[field] is not None for field in ("target_price_state", "assortable", "raw_pack")):
                raise ReviewPackageError(
                    "NORMALIZED_TYPE_MISMATCH",
                    "A1 reviewed-null contract fields must remain explicit null",
                    table=name,
                    row=row_number,
                )
            for field in (
                "supplier_sku",
                "supplier_description",
                "canonical_variant_id",
                "shopify_units_per_case",
                "qualifying_units_per_case",
            ):
                self.normalized_type_counts[field][_native_json_type(row[field])] += 1
            link = self.normalized_links.get(row_number)
            if link is None:
                raise ReviewPackageError("JOIN_MISMATCH", "normalized review row lacks its link")
            _, _, source_file, _, source_page, variants = link
            if row.get("source_file") != source_file or row.get("source_page") != source_page:
                raise ReviewPackageError("JOIN_MISMATCH", "normalized source reference differs")
            variant = row.get("canonical_variant_id")
            if variant is not None and variant not in variants:
                raise ReviewPackageError("JOIN_MISMATCH", "normalized Variant ID differs from link")
            self.normalized_review_details[row_number] = (
                _text(row.get("vendor_name"), "normalized supplier"),
                row.get("supplier_sku"),
                source_file,
                source_page,
            )
        elif name in {"rejected_alternatives", "additional_rejected_alternatives"}:
            variant = _text(row.get("variant_id"), "rejected Variant ID")
            pair = (variant, _text(row.get("offer_id"), "rejected source offer ID"))
            target = (
                self.base_rejected_pairs
                if name == "rejected_alternatives"
                else self.additional_rejected_pairs
            )
            if pair in target:
                raise ReviewPackageError(
                    "TABLE_KEY_NOT_UNIQUE", "rejected candidate pair is duplicated", table=name
                )
            target.add(pair)
            common_facts = (
                row.get("supplier_code"),
                row.get("source_file"),
                row.get("physical_page"),
                row.get("source_sha256"),
                row.get("raw_evidence"),
            )
            if name == "rejected_alternatives":
                self.base_rejected_facts[pair] = (
                    *common_facts,
                    row.get("reviewed_supplier_title"),
                    row.get("candidate_disposition"),
                    canonical_record_sha256(row),
                )
            else:
                self.additional_rejected_facts[pair] = (
                    *common_facts,
                    row.get("disposition"),
                )
            self.rejected_by_variant[variant].append(
                {
                    "source_table": name,
                    "supplier": row.get("supplier"),
                    "source_offer_id": row.get("offer_id"),
                    "supplier_code": row.get("supplier_code"),
                    "supplier_title": row.get("supplier_title"),
                    "candidate_disposition": row.get(
                        "candidate_disposition", row.get("disposition")
                    ),
                    "hard_conflicts": row.get("hard_conflicts"),
                    "nonselection_reason": row.get(
                        "v4_nonselection_reason", row.get("reason")
                    ),
                    "source_file": row.get("source_file"),
                    "physical_page": row.get("physical_page"),
                    "source_sha256": row.get("source_sha256"),
                }
            )

    def finalize(self, tables: Mapping[str, PackageTable]) -> dict[str, Any]:
        expected_tiers = self.ordinary_tiers | self.comparison_tiers
        if self.ordinary_tiers & self.comparison_tiers:
            raise ReviewPackageError("TIER_IDENTITY_MISMATCH", "ordinary and comparison Tier IDs overlap")
        if len(self.source_offers) != 38_032 or len(self.ordinary_tiers) != 76_896:
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "ordinary offer/Tier controls differ")
        if self.source_sku_leading_zero != 67 or self.source_sku_nondigit != 13_787:
            raise ReviewPackageError(
                "CONTROL_TOTAL_MISMATCH", "source supplier-code text controls differ"
            )
        relationship_rows = tables["variant_offer_relationships_v5"].rows
        relationship_pairs = {
            (str(row.get("variant_id")), str(row.get("source_offer_id")))
            for row in relationship_rows
        }
        prior_relationship_pairs = {
            (str(row.get("variant_id")), str(row.get("source_offer_id")))
            for row in relationship_rows
            if row.get("candidate_pair_existed_before_family_pass") is True
        }
        if (
            len(self.candidate_pairs) != 14_760
            or self.candidate_pairs != self.eligible_pairs
            or self.candidate_pairs != prior_relationship_pairs
            or not self.candidate_pairs.issubset(relationship_pairs)
            or len(relationship_pairs - self.candidate_pairs) != 52
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "candidate eligibility/relationship pair coverage differs"
            )
        memory_rows = tables["rejected_match_memory_v5"].rows
        memory_by_pair = {
            (str(row.get("variant_id")), str(row.get("source_offer_id"))): row
            for row in memory_rows
        }
        memory_pairs = set(memory_by_pair)
        current_memory = Counter(
            row.get("historical_rejection_still_current") for row in memory_rows
        )
        if (
            len(self.base_rejected_pairs) != 7_123
            or len(self.additional_rejected_pairs) != 22
            or len(memory_pairs) != 7_143
            or not self.base_rejected_pairs.issubset(memory_pairs)
            or len(memory_pairs - self.base_rejected_pairs) != 20
            or not self.additional_rejected_pairs.isdisjoint(self.candidate_pairs)
            or not self.additional_rejected_pairs.isdisjoint(memory_pairs)
            or current_memory != Counter({True: 7_142, False: 1})
            or any(row.get("no_automatic_reactivation") is not True for row in memory_rows)
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "rejected-candidate and negative-memory layers differ"
            )
        _validate_rejected_provenance(self, relationship_rows, memory_rows)
        if len(self.comparison_offers) != 936 or len(self.comparison_tiers) != 3_765:
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "comparison offer/Tier controls differ")
        if set(self.source_offers) & self.comparison_offers:
            raise ReviewPackageError("JOIN_MISMATCH", "ordinary and comparison offers overlap")
        declared_comparison_tiers = set().union(
            *(details[5] for details in self.comparison_offer_details.values())
        )
        if (
            declared_comparison_tiers != self.comparison_tiers
            or sum(len(details[5]) for details in self.comparison_offer_details.values())
            != len(self.comparison_tiers)
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "comparison parent Tier coverage differs"
            )
        if self.registry_tiers != expected_tiers or self.validation_tiers != expected_tiers:
            raise ReviewPackageError("JOIN_MISMATCH", "Tier registry/validation coverage differs")
        if set(item[0] for item in self.normalized_links.values()) != expected_tiers:
            raise ReviewPackageError("JOIN_MISMATCH", "normalized Tier links do not cover exact Tier registry")
        if len(self.normalized_links) != 80_661 or self.normalized_rows != 80_661:
            raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "normalized review controls differ")
        expected_normalized_types = {
            "supplier_sku": Counter({"str": 80_598, "null": 63}),
            "supplier_description": Counter({"str": 76_896, "null": 3_765}),
            "canonical_variant_id": Counter({"null": 75_989, "str": 4_672}),
            "shopify_units_per_case": Counter(
                {"null": 76_010, "float": 2_571, "int": 2_080}
            ),
            "qualifying_units_per_case": Counter({"null": 76_784, "int": 3_877}),
        }
        if dict(self.normalized_type_counts) != expected_normalized_types:
            raise ReviewPackageError(
                "CONTROL_TOTAL_MISMATCH", "normalized native-type/null controls differ"
            )
        if set(self.tier_details) != expected_tiers:
            raise ReviewPackageError("JOIN_MISMATCH", "price Tier detail coverage differs")
        for tier, (offer, supplier, sku, source_file, source_sha, source_page) in self.tier_details.items():
            if tier in self.ordinary_tiers:
                parent = self.source_offers.get(offer)
                if parent is None or parent[:4] != (
                    supplier,
                    sku,
                    source_file,
                    source_sha,
                ):
                    raise ReviewPackageError(
                        "JOIN_MISMATCH", "ordinary price Tier differs from source offer"
                    )
            else:
                parent = self.comparison_offer_details.get(offer)
                if (
                    parent is None
                    or parent[:4] != (supplier, sku, source_file, source_sha)
                    or source_page not in parent[4]
                    or tier not in parent[5]
                ):
                    raise ReviewPackageError(
                        "JOIN_MISMATCH", "comparison price Tier differs from comparison parent"
                    )
        for position, link in self.normalized_links.items():
            tier, offer, source_file, source_sha, source_page, _ = link
            tier_detail = self.tier_details[tier]
            if (
                tier_detail[0] != offer
                or tier_detail[3:] != (source_file, source_sha, source_page)
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "normalized link differs from exact price Tier"
                )
            review_detail = self.normalized_review_details.get(position)
            if (
                review_detail is None
                or review_detail[1:] != (tier_detail[2], source_file, source_page)
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "normalized review row differs from exact price Tier"
                )
        price_rows = tables["price_comparisons"].rows
        if any(row.get("source_offer_id") not in self.comparison_offers for row in price_rows):
            raise ReviewPackageError("JOIN_MISMATCH", "comparison row lacks comparison parent")
        if any(offer not in self.source_offers for offer in self.tiers_by_offer):
            raise ReviewPackageError("JOIN_MISMATCH", "price ladder lacks ordinary source offer")
        return {
            "ordinary_source_offers": len(self.source_offers),
            "ordinary_price_tiers": len(self.ordinary_tiers),
            "comparison_parent_offers": len(self.comparison_offers),
            "comparison_price_rows": len(self.comparison_tiers),
            "normalized_review_rows": self.normalized_rows,
            "normalized_contract_fields": len(PRICE_BOOK_HEADERS),
            "source_offer_details": {
                key: value for key, value in sorted(self.source_offer_details.items())
            },
            "price_ladders_by_offer": {
                key: value for key, value in sorted(self.tiers_by_offer.items())
            },
            "rejected_by_variant": {
                key: value for key, value in sorted(self.rejected_by_variant.items())
            },
        }


def _validate_rejected_provenance(
    projection: _ProjectionBuilder,
    relationship_rows: Sequence[Mapping[str, Any]],
    memory_rows: Sequence[Mapping[str, Any]],
) -> None:
    """Bind rejected occurrences to exact source rows and negative memory."""

    relationship_dispositions = {
        (str(row.get("variant_id")), str(row.get("source_offer_id"))): row.get(
            "candidate_disposition"
        )
        for row in relationship_rows
    }
    memory_by_pair = {
        (str(row.get("variant_id")), str(row.get("source_offer_id"))): row
        for row in memory_rows
    }
    if len(memory_by_pair) != len(memory_rows):
        raise ReviewPackageError(
            "TABLE_KEY_NOT_UNIQUE", "negative-memory occurrence is duplicated"
        )
    for pair, memory in memory_by_pair.items():
        if memory.get("current_candidate_disposition") != relationship_dispositions.get(
            pair
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "negative-memory disposition differs from the current occurrence",
            )
    for pair, facts in projection.base_rejected_facts.items():
        source = projection.source_offers.get(pair[1])
        details = projection.source_offer_details.get(pair[1])
        memory = memory_by_pair.get(pair)
        (
            supplier_code,
            source_file,
            source_page,
            source_sha,
            raw_evidence,
            reviewed_title,
            historical_disposition,
            record_sha,
        ) = facts
        source_code_matches = source is not None and (
            supplier_code == source[1]
            or (source[1] is None and supplier_code == "")
        )
        if (
            source is None
            or details is None
            or memory is None
            or not source_code_matches
            or (source_file, source_sha, source_page) != source[2:]
            or raw_evidence != details.get("source_evidence")
            or reviewed_title != details.get("supplier_description")
            or memory.get("historical_record_sha256") != record_sha
            or memory.get("historical_disposition") != historical_disposition
            or memory.get("historical_rejection_still_current")
            is not (
                historical_disposition
                == memory.get("current_candidate_disposition")
            )
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "base rejected alternative provenance differs from its source or memory",
            )
    for pair, facts in projection.additional_rejected_facts.items():
        source = projection.source_offers.get(pair[1])
        details = projection.source_offer_details.get(pair[1])
        (
            supplier_code,
            source_file,
            source_page,
            source_sha,
            raw_evidence,
            disposition,
        ) = facts
        if (
            source is None
            or details is None
            or supplier_code != source[1]
            or (source_file, source_sha, source_page) != source[2:]
            or raw_evidence != details.get("source_evidence")
            or not isinstance(disposition, str)
            or not disposition.startswith("REJECTED_")
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "additional rejected alternative provenance differs from its source",
            )


def _stream_table_part(
    shard: _PackageSource,
    part: Mapping[str, Any],
    *,
    limits: ReviewLimits,
    row_offset: int,
    on_row: Callable[[dict[str, Any], int], None] | None = None,
    yield_rows: bool = False,
) -> Iterator[dict[str, Any]]:
    path = str(part["path"])
    digest = hashlib.sha256()
    observed_bytes = 0
    observed_rows = 0
    with shard.open(path) as handle:
        while True:
            line = handle.readline(limits.max_line_bytes + 1)
            if not line:
                break
            if len(line) > limits.max_line_bytes:
                raise ReviewPackageError("LINE_TOO_LARGE", "A1 JSONL row exceeds limit", path=path)
            observed_bytes += len(line)
            digest.update(line)
            if not line.strip():
                raise ReviewPackageError("BLANK_JSONL_ROW", "blank A1 JSONL row", path=path)
            observed_rows += 1
            if observed_rows > part["row_count"]:
                raise ReviewPackageError("ROW_COUNT_MISMATCH", "A1 part contains excess rows", path=path)
            value = _parse_json_bytes(line, path=path, limits=limits)
            row = _checked_record(
                value,
                path=path,
                row=row_offset + observed_rows,
                limits=limits,
            )
            if line != _canonical_json(row) + b"\n":
                raise ReviewPackageError("NONCANONICAL_TABLE_ROW", "A1 JSONL row is not canonical", path=path)
            if on_row is not None:
                on_row(row, row_offset + observed_rows)
            if yield_rows:
                yield row
    if observed_bytes != shard.size(path) or observed_bytes != part["bytes"]:
        raise ReviewPackageError("SIZE_MISMATCH", "A1 table part byte count differs", path=path)
    if digest.hexdigest() != part["sha256"]:
        raise ReviewPackageError("FILE_INTEGRITY_MISMATCH", "A1 table part hash differs", path=path)
    if observed_rows != part["row_count"]:
        raise ReviewPackageError("ROW_COUNT_MISMATCH", "A1 table part row count differs", path=path)


def _read_daytime_dictionary(
    source: _PackageSource,
    embedded_by_restored: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    descriptor = embedded_by_restored.get(_DAYTIME_DICTIONARY_PATH)
    if descriptor != {
        "archive": "Buffalo_V5_Daytime_Review_Addendum.zip",
        "path": _DAYTIME_DICTIONARY_PATH,
        "restored_path": _DAYTIME_DICTIONARY_PATH,
        "role": "OBSERVED_FIELD_DICTIONARY",
        "bytes": _DAYTIME_DICTIONARY_BYTES,
        "sha256": _DAYTIME_DICTIONARY_SHA256,
    }:
        raise ReviewPackageError(
            "A1_CONTRACT_MISMATCH", "daytime dictionary descriptor differs"
        )
    part = {
        "path": _DAYTIME_DICTIONARY_PATH,
        "row_count": _EXPECTED_DAYTIME_DICTIONARY_FIELDS,
        "bytes": _DAYTIME_DICTIONARY_BYTES,
        "sha256": _DAYTIME_DICTIONARY_SHA256,
    }
    archive_name = "Buffalo_V5_Daytime_Review_Addendum.zip"
    archive = _sealed_archive_snapshot(source, archive_name)
    with _ZipSource(
        archive,
        _shard_limits(source.limits),
        display_path=source.path / archive_name,
    ) as shard:
        return tuple(
            _stream_table_part(
                shard,
                part,
                limits=source.limits,
                row_offset=0,
                yield_rows=True,
            )
        )


def _parse_specialist_evidence_jsonl(
    raw: bytes,
    *,
    path: str,
    expected_rows: int,
    limits: ReviewLimits,
) -> tuple[dict[str, Any], ...]:
    """Parse a small, exact-byte specialist evidence file as strict JSONL."""

    if not raw.endswith(b"\n"):
        raise ReviewPackageError(
            "NONCANONICAL_TABLE_ROW",
            "specialist evidence must end with LF",
            path=path,
        )
    lines = raw.splitlines(keepends=True)
    if len(lines) != expected_rows:
        raise ReviewPackageError(
            "ROW_COUNT_MISMATCH",
            "specialist evidence row count differs",
            path=path,
        )
    rows: list[dict[str, Any]] = []
    for row_number, line in enumerate(lines, start=1):
        if len(line) > limits.max_line_bytes:
            raise ReviewPackageError(
                "LINE_TOO_LARGE",
                "specialist evidence row exceeds limit",
                path=path,
                row=row_number,
            )
        if not line.strip():
            raise ReviewPackageError(
                "BLANK_JSONL_ROW",
                "blank specialist evidence row",
                path=path,
                row=row_number,
            )
        rows.append(
            _checked_record(
                _parse_json_bytes(line, path=path, limits=limits),
                path=path,
                row=row_number,
                limits=limits,
            )
        )
    return tuple(rows)


def _read_tables_and_embedded(
    source: _PackageSource,
    table_specs: Sequence[Mapping[str, Any]],
    embedded_specs: Sequence[Mapping[str, Any]],
    members_by_archive: Mapping[str, set[str]],
    projection: _ProjectionBuilder,
) -> tuple[
    dict[str, PackageTable],
    dict[str, Callable[[], Iterator[dict[str, Any]]]],
    dict[str, tuple[dict[str, Any], ...]],
    int,
    int,
]:
    specs_by_archive: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    embedded_by_archive: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for table in table_specs:
        for part in table["parts"]:
            specs_by_archive[str(part["archive"])].append((table, part))
    for row in embedded_specs:
        embedded_by_archive[str(row["archive"])].append(row)
    duplicate_logicals = Counter(str(spec["logical_name"]) for spec in table_specs)
    rows_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    observed_table_digests = {id(spec): hashlib.sha256() for spec in table_specs}
    observed_table_bytes: Counter[int] = Counter()
    observed_table_rows: Counter[int] = Counter()
    total_entries = len(source.names)
    total_expanded = sum(source.size(name) for name in source.names)
    verified_embedded = 0
    specialist_evidence: dict[str, tuple[dict[str, Any], ...]] = {}
    for archive_name, _expected_bytes, _expected_sha, _role in _ARCHIVES:
        archive_snapshot = _sealed_archive_snapshot(source, archive_name)
        with _ZipSource(
            archive_snapshot,
            _shard_limits(source.limits),
            display_path=source.path / archive_name,
        ) as shard:
            total_entries += len(shard.names)
            total_expanded += sum(shard.size(name) for name in shard.names)
            if total_entries > source.limits.max_a1_archive_entries:
                raise ReviewPackageError("TOO_MANY_ENTRIES", "A1 aggregate entry count exceeds limit")
            if total_expanded > source.limits.max_total_expanded_bytes:
                raise ReviewPackageError("PACKAGE_TOO_LARGE", "A1 aggregate expanded bytes exceed limit")
            if archive_name == _INDEX_ARCHIVE:
                if len(shard.names) != _EXPECTED_INDEX_MEMBERS:
                    raise ReviewPackageError("UNDECLARED_FILE", "A1 index member count differs")
                if set(_READER_FILES) - set(shard.names) or PORTABLE_ROOT not in shard.names:
                    raise ReviewPackageError("MISSING_FILE", "A1 index lacks sealed reader/root files")
                for name in shard.names:
                    member_sha, member_bytes = _stream_sha256(shard, name)
                    if name == PORTABLE_ROOT and (member_bytes, member_sha) != (A1_ROOT_BYTES, A1_ROOT_SHA256):
                        raise ReviewPackageError("A1_ROOT_IDENTITY_MISMATCH", "index root bytes differ")
                    if name in _READER_FILES and (member_bytes, member_sha) != _READER_FILES[name]:
                        raise ReviewPackageError("FILE_INTEGRITY_MISMATCH", "sealed reader bytes differ", path=name)
                continue
            expected_members = members_by_archive.get(archive_name, set())
            if set(shard.names) != set(expected_members):
                extras = sorted(set(shard.names) - set(expected_members))
                missing = sorted(set(expected_members) - set(shard.names))
                raise ReviewPackageError(
                    "UNDECLARED_FILE" if extras else "MISSING_FILE",
                    "A1 shard member set differs",
                    path=(extras or missing)[0],
                )
            for table, part in specs_by_archive.get(archive_name, ()):
                namespace = str(table["namespace"])
                logical = str(table["logical_name"])
                key = (
                    f"{namespace}__{logical}"
                    if duplicate_logicals[logical] > 1
                    else logical
                )
                retain = namespace != "effective_v5" or logical in _MATERIALIZED_EFFECTIVE

                def observe(row: dict[str, Any], row_number: int) -> None:
                    projection.observe(namespace, logical, row, row_number)
                    encoded = _canonical_json(row) + b"\n"
                    observed_table_digests[id(table)].update(encoded)
                    observed_table_bytes[id(table)] += len(encoded)
                    observed_table_rows[id(table)] += 1
                    if retain:
                        rows_by_key[key].append(row)

                tuple(
                    _stream_table_part(
                        shard,
                        part,
                        limits=source.limits,
                        row_offset=int(part["first_logical_row"]) - 1,
                        on_row=observe,
                    )
                )
            for embedded in embedded_by_archive.get(archive_name, ()):
                member_path = str(embedded["path"])
                specialist_contract = _SPECIALIST_EVIDENCE_BY_MEMBER.get(
                    (archive_name, member_path)
                )
                if specialist_contract is None:
                    member_sha, member_bytes = _stream_sha256(shard, member_path)
                else:
                    (
                        logical_name,
                        expected_bytes,
                        expected_sha,
                        expected_rows,
                    ) = specialist_contract
                    if (
                        embedded.get("bytes") != expected_bytes
                        or embedded.get("sha256") != expected_sha
                    ):
                        raise ReviewPackageError(
                            "A1_CONTRACT_MISMATCH",
                            "specialist evidence descriptor differs",
                            path=member_path,
                        )
                    raw = _read_bounded(shard, member_path, expected_bytes)
                    member_bytes = len(raw)
                    member_sha = hashlib.sha256(raw).hexdigest()
                    specialist_evidence[logical_name] = (
                        _parse_specialist_evidence_jsonl(
                            raw,
                            path=member_path,
                            expected_rows=expected_rows,
                            limits=source.limits,
                        )
                    )
                if (member_bytes, member_sha) != (embedded["bytes"], embedded["sha256"]):
                    raise ReviewPackageError(
                        "FILE_INTEGRITY_MISMATCH",
                        "A1 embedded bytes differ",
                        path=str(embedded["path"]),
                    )
                verified_embedded += 1
    tables: dict[str, PackageTable] = {}
    loaders: dict[str, Callable[[], Iterator[dict[str, Any]]]] = {}
    package_path = Path(source.path)
    package_limits = source.limits
    shard_limits = _shard_limits(source.limits)
    for table in table_specs:
        namespace = str(table["namespace"])
        logical = str(table["logical_name"])
        key = f"{namespace}__{logical}" if duplicate_logicals[logical] > 1 else logical
        if (
            observed_table_rows[id(table)] != table["total_rows"]
            or observed_table_digests[id(table)].hexdigest() != table["canonical_jsonl_sha256"]
            or observed_table_digests[id(table)].hexdigest() != table["raw_sha256"]
        ):
            raise ReviewPackageError("CANONICAL_HASH_MISMATCH", "A1 logical table differs", table=key)
        if "bytes" in table and observed_table_bytes[id(table)] != table["bytes"]:
            raise ReviewPackageError("SIZE_MISMATCH", "A1 logical table bytes differ", table=key)
        tables[key] = PackageTable(
            name=key,
            path=str(table["restored_path"]),
            format="jsonl",
            rows=tuple(rows_by_key.get(key, ())),
            raw_sha256=str(table["raw_sha256"]),
            canonical_jsonl_sha256=str(table["canonical_jsonl_sha256"]),
            declared_row_count=int(table["total_rows"]),
        )
        if not rows_by_key.get(key) and table["total_rows"]:
            parts = tuple(dict(part) for part in table["parts"])

            def load_parts(parts: tuple[dict[str, Any], ...] = parts) -> Iterator[dict[str, Any]]:
                for part in parts:
                    archive_name = str(part["archive"])
                    with _DirectorySource(package_path, package_limits) as current_source:
                        archive_snapshot = _sealed_archive_snapshot(
                            current_source, archive_name
                        )
                    with _ZipSource(
                        archive_snapshot,
                        shard_limits,
                        display_path=package_path / archive_name,
                    ) as shard:
                        # Buffer one bounded shard part so no row escapes until
                        # its complete byte count and exact digest have passed.
                        verified_rows = tuple(
                            _stream_table_part(
                                shard,
                                part,
                                limits=source.limits,
                                row_offset=int(part["first_logical_row"]) - 1,
                                yield_rows=True,
                            )
                        )
                        yield from verified_rows

            loaders[key] = load_parts
    if set(specialist_evidence) != set(_SPECIALIST_EVIDENCE):
        raise ReviewPackageError(
            "MISSING_FILE", "sealed specialist evidence set differs"
        )
    return tables, loaders, specialist_evidence, verified_embedded, total_entries


def _validate_duplicate_v5_surfaces(
    tables: Mapping[str, PackageTable],
    by_logical: Mapping[str, PackageTable],
) -> int:
    """Bind exact effective tables to their duplicated V5 sidecar bytes."""

    duplicate_surfaces = (
        ("catalog_coverage", "catalog_coverage_v5"),
        ("current_additions_review", "current_additions_review_v5"),
        ("unresolved_dependencies", "unresolved_dependencies_v5"),
    )
    for effective_name, sidecar_name in duplicate_surfaces:
        effective = tables.get(effective_name)
        sidecar = by_logical.get(sidecar_name)
        if effective is None or sidecar is None or (
            effective.record_count,
            effective.raw_sha256,
            effective.canonical_jsonl_sha256,
        ) != (
            sidecar.record_count,
            sidecar.raw_sha256,
            sidecar.canonical_jsonl_sha256,
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "effective and V5 duplicate table surfaces differ",
                table=effective_name,
            )
    return len(duplicate_surfaces)


def _validate_v5_sidecars(tables: Mapping[str, PackageTable]) -> dict[str, Any]:
    sidecars = {
        key: table
        for key, table in tables.items()
        if table.path.startswith("tables/v5__")
    }
    by_logical = {
        (key.split("__", 1)[1] if key.startswith("v5_sidecar__") else key): table
        for key, table in sidecars.items()
    }
    expected = set(_V5_TABLE_ROWS) | {"table_catalog_v5", "field_profiles_v5"}
    if set(by_logical) != expected:
        raise ReviewPackageError("V5_SCHEMA_VERSION_MISMATCH", "A1 V5 sidecar set differs")
    for name, count in _V5_TABLE_ROWS.items():
        if by_logical[name].record_count != count:
            raise ReviewPackageError("ROW_COUNT_MISMATCH", "V5 sidecar row count differs", table=name)
    catalog = by_logical["table_catalog_v5"]
    profiles = by_logical["field_profiles_v5"]
    if catalog.raw_sha256 != _V5_TABLE_CATALOG_RAW_SHA256 or profiles.raw_sha256 != _V5_FIELD_PROFILES_RAW_SHA256:
        raise ReviewPackageError("V5_SCHEMA_VERSION_MISMATCH", "V5 catalog/profile digest differs")
    catalog_rows: dict[str, Mapping[str, Any]] = {}
    for row_number, row in enumerate(catalog.rows, start=1):
        if set(row) != set(_V5_CATALOG_FIELDS):
            raise ReviewPackageError("INVALID_TABLE_CATALOG", "V5 catalog fields differ", row=row_number)
        name = row.get("table")
        if name not in _V5_TABLE_ROWS or name in catalog_rows:
            raise ReviewPackageError("INVALID_TABLE_CATALOG", "V5 catalog table set differs")
        if row.get("rows") != _V5_TABLE_ROWS[name]:
            raise ReviewPackageError("ROW_COUNT_MISMATCH", "V5 catalog count differs", table=str(name))
        table = by_logical[str(name)]
        if (
            row.get("byte_sha256") != table.raw_sha256
            or row.get("canonical_jsonl_sha256") != table.canonical_jsonl_sha256
            or row.get("role") != "CURRENT_DIAGNOSTIC_REVIEW_SIDECAR_UNAPPROVED"
        ):
            raise ReviewPackageError("INVALID_TABLE_CATALOG", "V5 catalog descriptor differs", table=str(name))
        keys = row.get("candidate_unique_key_fields")
        if not isinstance(keys, list) or any(not isinstance(field, str) or not field for field in keys):
            raise ReviewPackageError("INVALID_TABLE_CATALOG", "V5 candidate key list differs")
        for field in keys:
            seen: set[bytes] = set()
            for item in table.rows:
                if field not in item or item[field] is None or item[field] == "":
                    raise ReviewPackageError("TABLE_KEY_MISSING", f"{field} is blank", table=str(name))
                encoded = _canonical_json(item[field])
                if encoded in seen:
                    raise ReviewPackageError("TABLE_KEY_NOT_UNIQUE", f"{field} is duplicated", table=str(name))
                seen.add(encoded)
        catalog_rows[str(name)] = row
    if set(catalog_rows) != set(_V5_TABLE_ROWS):
        raise ReviewPackageError("INVALID_TABLE_CATALOG", "V5 catalog is incomplete")
    raw_tables = {name: by_logical[name].rows for name in _V5_TABLE_ROWS}
    for name, fields in _V5_CORE_FIELDS.items():
        for row_number, row in enumerate(raw_tables[name], start=1):
            valid = fields.issubset(row) if name == "unresolved_dependencies_v5" else set(row) == set(fields)
            if not valid:
                raise ReviewPackageError(
                    "V5_SCHEMA_VERSION_MISMATCH",
                    "critical V5 fields differ",
                    table=name,
                    row=row_number,
                )
    for row_number, row in enumerate(profiles.rows, start=1):
        if set(row) != set(_V5_PROFILE_FIELDS) or row.get("schema_authority") != _V5_PROFILE_AUTHORITY:
            raise ReviewPackageError("INVALID_FIELD_PROFILE", "V5 field-profile contract differs", row=row_number)
    _validate_v5_field_profiles(raw_tables, profiles.rows)
    relationship_controls = _validate_v5_relationships_and_authority(
        by_logical,
        None,
        enforce_exact_control_totals=True,
    )
    duplicate_surfaces = _validate_duplicate_v5_surfaces(tables, by_logical)
    return {
        "v5_sidecar_tables": len(by_logical),
        "v5_field_profiles": len(profiles.rows),
        "v5_duplicate_surfaces": duplicate_surfaces,
        **relationship_controls,
    }


def _source_offer_contract(
    projection: _ProjectionBuilder, offer_id: str, *, context: str
) -> dict[str, Any]:
    values = projection.source_offer_contracts.get(offer_id)
    if values is None:
        raise ReviewPackageError(
            "JOIN_MISMATCH", f"{context} does not resolve to an exact source offer"
        )
    return dict(zip(_SOURCE_OFFER_CONTRACT_FIELDS, values, strict=True))


def _nested_source_offer_id(
    value: Any,
    projection: _ProjectionBuilder,
    *,
    context: str,
) -> str:
    source = _mapping(value, f"{context} source offer")
    offer_id = _text(source.get("source_offer_id"), f"{context} source offer ID")
    contract = _source_offer_contract(projection, offer_id, context=context)
    if set(source) != set(_REMAINING_SOURCE_FIELDS) or any(
        source[field] != contract[field] for field in _REMAINING_SOURCE_FIELDS
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", f"{context} nested source-offer provenance differs"
        )
    return offer_id


def _embedded_json_array(
    value: Any, *, context: str, limits: ReviewLimits
) -> list[Any]:
    if not isinstance(value, str):
        raise ReviewPackageError(
            "A1_SCHEMA_MISMATCH", f"{context} must be a JSON-encoded array string"
        )
    parsed = _parse_json_bytes(value.encode("utf-8"), path=context, limits=limits)
    if not isinstance(parsed, list):
        raise ReviewPackageError(
            "A1_SCHEMA_MISMATCH", f"{context} must decode to an array"
        )
    return parsed


def _embedded_json_object(
    value: Any, *, context: str, limits: ReviewLimits
) -> dict[str, Any]:
    if not isinstance(value, str):
        raise ReviewPackageError(
            "A1_SCHEMA_MISMATCH", f"{context} must be a JSON-encoded object string"
        )
    parsed = _parse_json_bytes(value.encode("utf-8"), path=context, limits=limits)
    if not isinstance(parsed, dict):
        raise ReviewPackageError(
            "A1_SCHEMA_MISMATCH", f"{context} must decode to an object"
        )
    return parsed


def _validate_complete_combo_provenance(
    combos: Sequence[Mapping[str, Any]],
    linked_components: Sequence[Mapping[str, Any]],
    validations: Sequence[Mapping[str, Any]],
    projection: _ProjectionBuilder,
    *,
    limits: ReviewLimits,
) -> None:
    """Bind every complete combo to its source offer, components, and Tiers."""

    components_by_offer: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for component in linked_components:
        offer_id = _text(component.get("source_offer_id"), "combo component offer ID")
        _text(component.get("source_component_id"), "combo component ID")
        components_by_offer[offer_id].append(component)
    validation_by_offer: dict[str, Mapping[str, Any]] = {}
    for validation in validations:
        offer_id = _text(validation.get("source_offer_id"), "combo validation offer ID")
        if offer_id in validation_by_offer:
            raise ReviewPackageError(
                "TABLE_KEY_NOT_UNIQUE", "combo validation offer ID is duplicated"
            )
        validation_by_offer[offer_id] = validation

    observed_offers: set[str] = set()
    evidence_fields = (
        ("component_id", "source_component_id"),
        ("supplier_code", "component_supplier_sku"),
        ("title", "component_description"),
        ("quantity_as_printed", "quantity_raw"),
        ("quantity_unit", "quantity_unit_raw"),
        ("size_raw", "size_text"),
    )
    evidence_field_names = {left for left, _ in evidence_fields} | {"evidence"}
    for combo in combos:
        offer_id = _text(combo.get("source_offer_id"), "complete combo offer ID")
        if offer_id in observed_offers:
            raise ReviewPackageError(
                "TABLE_KEY_NOT_UNIQUE", "complete combo offer ID is duplicated"
            )
        observed_offers.add(offer_id)
        source = _source_offer_contract(
            projection, offer_id, context="complete combo"
        )
        if any(field not in combo for field in _COMPLETE_COMBO_SHARED_SOURCE_FIELDS):
            raise ReviewPackageError(
                "A1_SCHEMA_MISMATCH", "complete combo lacks a source-join field"
            )
        if any(
            combo[field] != source[field]
            for field in _COMPLETE_COMBO_SHARED_SOURCE_FIELDS
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "complete combo differs from its exact source offer"
            )
        if source["package_type"] != "FIXED_COMBO" or any(
            field in combo or source[field] is not None
            for field in _COMPLETE_COMBO_SOURCE_ONLY_NULL_FIELDS
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "complete combo source-only conversion nulls were collapsed or changed",
            )

        component_ids = _embedded_json_array(
            combo.get("component_ids"),
            context=f"complete combo {offer_id} component_ids",
            limits=limits,
        )
        tier_ids = _embedded_json_array(
            combo.get("tier_ids"),
            context=f"complete combo {offer_id} tier_ids",
            limits=limits,
        )
        component_evidence = _embedded_json_array(
            combo.get("component_evidence"),
            context=f"complete combo {offer_id} component_evidence",
            limits=limits,
        )
        component_rows = components_by_offer.get(offer_id, [])
        expected_component_ids = [
            str(row.get("source_component_id")) for row in component_rows
        ]
        validation = validation_by_offer.get(offer_id)
        tier_rows = projection.tiers_by_offer.get(offer_id, [])
        expected_tier_ids = [str(row.get("source_tier_id")) for row in tier_rows]
        if (
            validation is None
            or any(not isinstance(item, str) or not item for item in component_ids)
            or len(component_ids) != len(set(component_ids))
            or component_ids != expected_component_ids
            or validation.get("source_component_ids") != component_ids
            or validation.get("component_quantity_total_as_printed")
            != combo.get("component_quantity_total_as_printed")
            or any(not isinstance(item, str) or not item for item in tier_ids)
            or len(tier_ids) != len(set(tier_ids))
            or tier_ids != expected_tier_ids
            or validation.get("source_tier_ids") != tier_ids
            or any(
                projection.tier_details.get(tier_id, (None,))[0] != offer_id
                for tier_id in tier_ids
            )
            or len(component_evidence) != len(component_rows)
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "complete combo component or Tier coverage differs",
            )
        for evidence, component in zip(
            component_evidence, component_rows, strict=True
        ):
            evidence = _mapping(evidence, "complete combo component evidence")
            raw_component = _embedded_json_object(
                component.get("raw_attributes_json"),
                context="combo component raw_attributes_json",
                limits=limits,
            )
            if (
                set(evidence) != evidence_field_names
                or any(
                    evidence.get(left) != component.get(right)
                    for left, right in evidence_fields
                )
                or any(
                    evidence.get(field) != raw_component.get(field)
                    for field in evidence_field_names
                )
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "complete combo component evidence differs"
                )
            if (
                component.get("supplier_name_raw") != source["supplier_name_raw"]
                or component.get("supplier_combo_code") != source["supplier_sku"]
                or component.get("source_file") != source["source_file"]
                or component.get("source_sha256") != source["source_sha256"]
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "complete combo component source differs"
                )
    if (
        observed_offers != set(components_by_offer)
        or observed_offers != set(validation_by_offer)
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "complete combo layers contain orphan offer IDs"
        )


def _validate_remaining_combo_provenance(
    component_reviews: Sequence[Mapping[str, Any]],
    total_reviews: Sequence[Mapping[str, Any]],
    family_reviews: Sequence[Mapping[str, Any]],
    source_components: Sequence[Mapping[str, Any]],
    complete_combo_ids: set[str],
    projection: _ProjectionBuilder,
    catalog_ids: set[str],
    census_ids: set[str],
    expected_family_controls: tuple[int, int, int, int] = (24, 64, 158, 32),
) -> None:
    """Bind the seven unresolved combo totals to 19 component diagnostics."""

    family_offers: dict[str, set[str]] = {}
    family_catalog_ids: set[str] = set()
    family_source_ids: set[str] = set()
    family_tier_ids: set[str] = set()
    family_tier_fields = (
        ("break_quantity_raw", "break_quantity"),
        ("break_unit_raw", "break_unit"),
        ("case_price_printed", "case_price_printed"),
        ("normalized_tier_type", "tier_type"),
        ("printed_page", "printed_page"),
        ("source_evidence", "source_evidence"),
        ("source_page", "source_page"),
        ("source_sha256", "source_sha256"),
        ("source_tier_id", "source_tier_id"),
        ("split_inclusive_price_printed", "split_inclusive_price_printed"),
        ("total_cost", "total_cost"),
        ("total_cost_basis", "total_cost_basis"),
        ("unit_pack_bottle_price_printed", "unit_pack_bottle_price_printed"),
    )
    for family in family_reviews:
        family_id = _text(family.get("family_review_id"), "remaining family review ID")
        offers = family.get("source_offers")
        catalog_records = family.get("catalog_records")
        if (
            family.get("offer_preference") is not None
            or family.get("split_fees_applied") is not None
        ):
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "remaining family cannot select an offer or apply fees",
            )
        if (
            family_id in family_offers
            or not isinstance(offers, list)
            or not isinstance(catalog_records, list)
        ):
            raise ReviewPackageError(
                "TABLE_KEY_NOT_UNIQUE", "remaining family review identity differs"
            )
        offer_ids: set[str] = set()
        for source in offers:
            source = _mapping(source, "remaining family source offer")
            offer_id = _text(
                source.get("source_offer_id"), "remaining family source offer ID"
            )
            contract = _source_offer_contract(
                projection, offer_id, context="remaining family"
            )
            ladders = source.get("full_existing_price_ladder")
            expected_ladders = projection.tiers_by_offer.get(offer_id, [])
            if (
                offer_id in offer_ids
                or offer_id in family_source_ids
                or set(source) != set(_FAMILY_SOURCE_FIELDS)
                or any(
                    source[field] != contract[field]
                    for field in _FAMILY_SOURCE_FIELDS
                    if field != "full_existing_price_ladder"
                )
                or not isinstance(ladders, list)
                or len(ladders) != len(expected_ladders)
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "remaining family source-offer projection differs"
                )
            for ladder, expected in zip(ladders, expected_ladders, strict=True):
                ladder = _mapping(ladder, "remaining family price Tier")
                tier_id = ladder.get("source_tier_id")
                if (
                    tier_id in family_tier_ids
                    or set(ladder) != {left for left, _ in family_tier_fields}
                    or any(
                        ladder[left] != expected[right]
                        for left, right in family_tier_fields
                    )
                ):
                    raise ReviewPackageError(
                        "JOIN_MISMATCH", "remaining family price Tier differs"
                    )
                family_tier_ids.add(str(tier_id))
            offer_ids.add(offer_id)
            family_source_ids.add(offer_id)
        for catalog in catalog_records:
            catalog = _mapping(catalog, "remaining family catalog record")
            variant_id = _text(
                catalog.get("variant_id"), "remaining family catalog Variant ID"
            )
            if (
                variant_id in family_catalog_ids
                or variant_id not in catalog_ids
                or variant_id not in census_ids
                or catalog.get("cohort") != "ORIGINAL2000"
                or catalog.get("original2000_population_member") is not True
                or catalog.get("current_addition_catalog_evidence") is not None
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "remaining family catalog membership differs"
                )
            family_catalog_ids.add(variant_id)
        family_offers[family_id] = offer_ids
    if (
        len(family_reviews),
        len(family_source_ids),
        len(family_tier_ids),
        len(family_catalog_ids),
    ) != expected_family_controls:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "remaining family nested controls differ"
        )

    source_components_by_offer: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for component in source_components:
        source_components_by_offer[str(component.get("source_offer_id"))].append(component)

    components_by_group: defaultdict[
        tuple[str, str], list[Mapping[str, Any]]
    ] = defaultdict(list)
    relationship_ids: set[str] = set()
    for row in component_reviews:
        relationship_id = _text(
            row.get("research_relationship_id"), "remaining component relationship ID"
        )
        family_id = _text(row.get("family_review_id"), "remaining component family ID")
        offer_id = _nested_source_offer_id(
            row.get("source_offer"), projection, context="remaining component"
        )
        tier_ids = row.get("source_tier_ids")
        if (
            relationship_id in relationship_ids
            or family_id not in family_offers
            or offer_id not in family_offers[family_id]
            or not isinstance(tier_ids, list)
            or any(not isinstance(tier_id, str) or not tier_id for tier_id in tier_ids)
            or len(tier_ids) != len(set(tier_ids))
            or any(
                projection.tier_details.get(tier_id, (None,))[0] != offer_id
                for tier_id in tier_ids
            )
            or row.get("component_cost_allocation") is not None
            or row.get("order_increment") is not None
            or row.get("physical_component_count_is_not_shopify_conversion") is not True
            or any(
                row.get(field) is not None
                for field in (
                    "shopify_sellable_units_per_combo_component",
                    "shopify_sellable_units_per_entire_combo",
                    "supplier_qualifying_units_per_combo_component",
                    "supplier_qualifying_units_per_entire_combo",
                )
            )
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "remaining component family/source/Tier join differs"
            )
        relationship_ids.add(relationship_id)
        components_by_group[(family_id, offer_id)].append(row)

    totals_by_group: dict[tuple[str, str], Mapping[str, Any]] = {}
    summary_ids: set[str] = set()
    for row in total_reviews:
        summary_id = _text(
            row.get("research_combo_summary_id"), "remaining combo summary ID"
        )
        family_id = _text(row.get("family_review_id"), "remaining total family ID")
        offer_id = _nested_source_offer_id(
            row.get("source_offer"), projection, context="remaining total"
        )
        group = (family_id, offer_id)
        children = components_by_group.get(group, [])
        source_rows = source_components_by_offer.get(offer_id, [])
        if (
            summary_id in summary_ids
            or group in totals_by_group
            or family_id not in family_offers
            or offer_id not in family_offers[family_id]
            or offer_id in complete_combo_ids
            or any(child.get("source_visual") != row.get("source_visual") for child in children)
            or row.get("component_research_rows") != len(children)
            or row.get("existing_component_rows") != len(source_rows)
            or row.get("existing_nonnull_component_ids")
            != sum(component.get("source_component_id") is not None for component in source_rows)
            or row.get("component_count_sum_reconciled") is not True
            or row.get("average_size_created") is not False
            or row.get("component_cost_allocation") is not None
            or row.get("source_component_id") is not None
            or row.get("complete_combo_total_join") != []
            or row.get("complete_combo_total_join_status")
            != "ABSENT_FROM_EXISTING_COMPLETE_COMBOS_TABLE; DO_NOT_FABRICATE_A_TOTAL_ID"
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "remaining combo total/component controls differ"
            )
        quantities = [child.get("component_quantity_raw") for child in children]
        if any(
            isinstance(quantity, bool) or not isinstance(quantity, (int, Decimal))
            for quantity in quantities
        ):
            raise ReviewPackageError(
                "A1_SCHEMA_MISMATCH", "remaining component quantity must be an exact number"
            )
        quantity_total = sum(quantities, 0)
        if (
            row.get("printed_component_quantity_sum") != quantity_total
            or row.get("expected_explicit_total") != quantity_total
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "remaining combo printed component total differs"
            )

        price_rows = projection.tiers_by_offer.get(offer_id, [])
        price_by_tier = {
            str(price.get("source_tier_id")): price for price in price_rows
        }
        expected_tier_ids = [str(price.get("source_tier_id")) for price in price_rows]
        printed = row.get("printed_total_evidence")
        arithmetic = row.get("arithmetic_checks")
        if not isinstance(printed, list) or not isinstance(arithmetic, list):
            raise ReviewPackageError(
                "A1_SCHEMA_MISMATCH", "remaining total Tier evidence must be arrays"
            )
        printed_ids = [item.get("source_tier_id") for item in printed if isinstance(item, dict)]
        arithmetic_ids = [
            item.get("source_tier_id") for item in arithmetic if isinstance(item, dict)
        ]
        if (
            len(printed_ids) != len(printed)
            or len(arithmetic_ids) != len(arithmetic)
            or printed_ids != expected_tier_ids
            or arithmetic_ids != expected_tier_ids
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "remaining total Tier coverage differs"
            )
        printed_fields = (
            ("break_quantity_raw", "break_quantity"),
            ("break_unit_raw", "break_unit"),
            ("case_price_printed", "case_price_printed"),
            ("normalized_tier_type", "tier_type"),
            ("source_evidence", "source_evidence"),
            ("source_file", "source_file"),
            ("source_page", "source_page"),
            ("source_sha256", "source_sha256"),
            ("split_inclusive_price_printed", "split_inclusive_price_printed"),
            ("unit_pack_bottle_price_printed", "unit_pack_bottle_price_printed"),
        )
        for printed_row, arithmetic_row, tier_id in zip(
            printed, arithmetic, expected_tier_ids, strict=True
        ):
            price = price_by_tier[tier_id]
            printed_row = _mapping(printed_row, "remaining printed Tier evidence")
            arithmetic_row = _mapping(arithmetic_row, "remaining Tier arithmetic")
            unit_price = arithmetic_row.get("printed_per_bottle_amount")
            unit_count = arithmetic_row.get("explicit_component_count_for_arithmetic")
            multiplied = arithmetic_row.get("printed_unit_times_count")
            if (
                printed_row.get("source_offer_id") != offer_id
                or any(
                    printed_row.get(left) != price.get(right)
                    for left, right in printed_fields
                )
                or arithmetic_row.get("printed_case_total")
                != price.get("case_price_printed")
                or unit_price != price.get("unit_pack_bottle_price_printed")
                or unit_count != row.get("expected_explicit_total")
                or isinstance(unit_price, bool)
                or not isinstance(unit_price, (int, Decimal))
                or isinstance(unit_count, bool)
                or not isinstance(unit_count, (int, Decimal))
                or multiplied != unit_price * unit_count
                or arithmetic_row.get("difference_from_printed_case_total")
                != multiplied - arithmetic_row.get("printed_case_total")
                or arithmetic_row.get("not_a_component_cost_allocation") is not True
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "remaining total printed Tier evidence differs"
                )
        summary_ids.add(summary_id)
        totals_by_group[group] = row
    if set(totals_by_group) != set(components_by_group):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "remaining combo totals do not partition component reviews"
        )


def _validate_catalog_census_cohorts(
    catalog_rows: Sequence[Mapping[str, Any]],
    census_rows: Sequence[Mapping[str, Any]],
    addition_layers: Sequence[Sequence[Mapping[str, Any]]],
) -> tuple[set[str], set[str]]:
    """Derive private catalog identities at runtime and validate only their joins."""

    variant_gid_prefix = "gid://shopify/ProductVariant/"
    product_gid_prefix = "gid://shopify/Product/"
    catalog_required = frozenset(
        {
            "variant_id",
            "variant_gid",
            "product_id",
            "product_status",
            "current_product_title",
            "current_variant_title",
            "current_sku_raw",
            "current_barcode_raw",
            "current_identity_returned",
        }
    )
    catalog_by_id: dict[str, Mapping[str, Any]] = {}
    for row in catalog_rows:
        variant_id = _text(row.get("variant_id"), "catalog Variant ID")
        if (
            not variant_id.isdigit()
            or variant_id in catalog_by_id
            or not catalog_required.issubset(row)
            or row.get("variant_gid") != f"{variant_gid_prefix}{variant_id}"
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "original catalog identity projection differs"
            )
        catalog_by_id[variant_id] = row
    catalog_ids = set(catalog_by_id)
    if (
        len(catalog_rows) != 2_000
        or len(catalog_ids) != 2_000
    ):
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "original catalog Variant IDs differ"
        )
    census_by_id: dict[str, Mapping[str, Any]] = {}
    for row in census_rows:
        gid = row.get("id")
        product = row.get("product")
        if (
            set(row) != {"barcode", "id", "product", "sku", "title"}
            or not isinstance(gid, str)
            or not gid.startswith(variant_gid_prefix)
            or not gid[len(variant_gid_prefix) :].isdigit()
            or not isinstance(product, Mapping)
            or set(product) != {"id", "status", "title"}
            or not isinstance(product.get("id"), str)
            or not str(product["id"]).startswith(product_gid_prefix)
            or not str(product["id"])[len(product_gid_prefix) :].isdigit()
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "historical census identity projection is invalid"
            )
        variant_id = gid[len(variant_gid_prefix) :]
        if variant_id in census_by_id:
            raise ReviewPackageError(
                "TABLE_KEY_NOT_UNIQUE", "historical census Variant ID is duplicated"
            )
        census_by_id[variant_id] = row
    census_ids = set(census_by_id)
    if len(census_rows) != 2_003 or len(census_ids) != 2_003:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "historical census IDs differ")
    missing_from_current = catalog_ids - census_ids
    current_additions = census_ids - catalog_ids
    if (
        len(missing_from_current) != 1
        or len(current_additions) != 4
        or any(not variant_id.isdigit() for variant_id in missing_from_current | current_additions)
    ):
        raise ReviewPackageError("JOIN_MISMATCH", "historical census/original cohort split differs")
    returned_false: set[str] = set()
    for variant_id, catalog in catalog_by_id.items():
        returned = catalog.get("current_identity_returned")
        census = census_by_id.get(variant_id)
        if type(returned) is not bool or returned is not (census is not None):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "catalog coverage/current-census availability differs"
            )
        if not returned:
            returned_false.add(variant_id)
            continue
        assert census is not None
        product = census["product"]
        if (
            census.get("id") != catalog.get("variant_gid")
            or product.get("id") != f"{product_gid_prefix}{catalog.get('product_id')}"
            or product.get("title") != catalog.get("current_product_title")
            or product.get("status") != catalog.get("product_status")
            or census.get("title") != catalog.get("current_variant_title")
            or census.get("sku") != catalog.get("current_sku_raw")
            or census.get("barcode") != catalog.get("current_barcode_raw")
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "catalog and current-census identity fields differ"
            )
    if (
        returned_false != missing_from_current
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "catalog coverage/current-census availability differs"
        )
    for addition_rows in addition_layers:
        addition_ids = {str(row.get("variant_id")) for row in addition_rows}
        if (
            len(addition_rows) != 4
            or len(addition_ids) != 4
            or addition_ids != current_additions
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "current-addition Variant coverage differs"
            )
        for row in addition_rows:
            catalog_evidence = _mapping(
                row.get("catalog_evidence"), "current-addition catalog evidence"
            )
            variant_id = _text(row.get("variant_id"), "current-addition Variant ID")
            census = census_by_id[variant_id]
            product = census["product"]
            if (
                set(catalog_evidence) != set(_ADDITION_CATALOG_EVIDENCE_FIELDS)
                or catalog_evidence.get("variant_id") != variant_id
                or census.get("id") != f"{variant_gid_prefix}{variant_id}"
                or catalog_evidence.get("product_id")
                != str(product.get("id"))[len(product_gid_prefix) :]
                or row.get("product_title") != product.get("title")
                or catalog_evidence.get("product_title_raw") != product.get("title")
                or row.get("variant_title") != census.get("title")
                or catalog_evidence.get("variant_title_raw") != census.get("title")
                or catalog_evidence.get("sku_raw") != census.get("sku")
                or catalog_evidence.get("barcode_raw") != census.get("barcode")
                or row.get("original2000_population_member") is not False
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "current-addition catalog identity differs"
                )
    return catalog_ids, census_ids


def _addition_capture_descriptors(
    addition_rows: Sequence[Mapping[str, Any]],
    embedded_by_restored: Mapping[str, Mapping[str, Any]],
    limits: ReviewLimits,
) -> tuple[dict[str, Mapping[str, Any]], int]:
    capture_prefix = "analysis/revision_v4/catalog/"
    descriptors: dict[str, Mapping[str, Any]] = {}
    references = 0
    for row in addition_rows:
        evidence = _mapping(
            row.get("catalog_evidence"), "current-addition catalog evidence"
        )
        for kind in ("product", "variant"):
            filename = _text(
                evidence.get(f"{kind}_capture_file"),
                f"current-addition {kind} capture file",
            )
            validated = _validated_member_name(filename, limits)
            if validated != filename or not filename.startswith(capture_prefix):
                raise ReviewPackageError(
                    "INVALID_PATH", "current-addition capture path is outside its sealed prefix"
                )
            restored_path = f"evidence/v4/{filename}"
            descriptor = embedded_by_restored.get(restored_path)
            if (
                descriptor is None
                or evidence.get(f"{kind}_capture_sha256")
                != descriptor.get("sha256")
            ):
                raise ReviewPackageError(
                    "SOURCE_EVIDENCE_MISMATCH",
                    "current-addition capture bytes do not resolve",
                )
            descriptors[restored_path] = descriptor
            references += 1
    if references != 8 or len(descriptors) != 2:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "current-addition capture controls differ"
        )
    return descriptors, references


def _read_addition_capture_documents(
    source: _PackageSource,
    addition_rows: Sequence[Mapping[str, Any]],
    embedded_by_restored: Mapping[str, Mapping[str, Any]],
    limits: ReviewLimits,
) -> dict[str, Mapping[str, Any]]:
    descriptors, _ = _addition_capture_descriptors(
        addition_rows, embedded_by_restored, limits
    )
    by_archive: defaultdict[str, list[tuple[str, Mapping[str, Any]]]] = defaultdict(
        list
    )
    for restored_path, descriptor in descriptors.items():
        by_archive[_text(descriptor.get("archive"), "capture archive")].append(
            (restored_path, descriptor)
        )
    documents: dict[str, Mapping[str, Any]] = {}
    for archive_name, items in by_archive.items():
        archive = _sealed_archive_snapshot(source, archive_name)
        with _ZipSource(
            archive,
            _shard_limits(limits),
            display_path=source.path / archive_name,
        ) as shard:
            for restored_path, descriptor in items:
                member_path = _validated_member_name(
                    _text(descriptor.get("path"), "capture member path"), limits
                )
                raw = _read_bounded(shard, member_path, limits.max_manifest_bytes)
                if (
                    len(raw) != descriptor.get("bytes")
                    or hashlib.sha256(raw).hexdigest() != descriptor.get("sha256")
                ):
                    raise ReviewPackageError(
                        "FILE_INTEGRITY_MISMATCH",
                        "addition capture bytes differ",
                        path=member_path,
                    )
                documents[restored_path] = _mapping(
                    _parse_json_bytes(raw, path=member_path, limits=limits),
                    "addition capture document",
                )
    if set(documents) != set(descriptors):
        raise ReviewPackageError(
            "SOURCE_EVIDENCE_MISMATCH", "addition capture document set differs"
        )
    return documents


def _validate_addition_capture_evidence(
    addition_rows: Sequence[Mapping[str, Any]],
    census_rows: Sequence[Mapping[str, Any]],
    embedded_by_restored: Mapping[str, Mapping[str, Any]],
    capture_documents: Mapping[str, Mapping[str, Any]],
    limits: ReviewLimits,
) -> int:
    """Bind the four addition identities to exact nodes in verified captures."""

    descriptors, references = _addition_capture_descriptors(
        addition_rows, embedded_by_restored, limits
    )
    if set(capture_documents) != set(descriptors):
        raise ReviewPackageError(
            "SOURCE_EVIDENCE_MISMATCH", "addition capture document set differs"
        )
    variant_gid_prefix = "gid://shopify/ProductVariant/"
    product_gid_prefix = "gid://shopify/Product/"
    expected_node_fields = {
        "product": {
            "descriptionHtml",
            "handle",
            "id",
            "metafields",
            "productType",
            "status",
            "tags",
            "title",
            "updatedAt",
            "vendor",
        },
    }
    expected_node_fields["variant"] = {
        "barcode",
        "id",
        "metafields",
        "product",
        "selectedOptions",
        "sku",
        "title",
        "updatedAt",
    }
    census_by_variant = {
        str(row.get("id"))[len(variant_gid_prefix) :]: row
        for row in census_rows
        if isinstance(row.get("id"), str)
        and str(row.get("id")).startswith(variant_gid_prefix)
    }
    if len(census_by_variant) != len(census_rows):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "addition capture census identities differ"
        )
    capture_indexes: dict[
        str, tuple[Mapping[str, Any], dict[str, Mapping[str, Any]]]
    ] = {}
    for kind, gid_prefix, identity_field in (
        ("product", product_gid_prefix, "product_id"),
        ("variant", variant_gid_prefix, "variant_id"),
    ):
        paths = {
            f"evidence/v4/{_mapping(row.get('catalog_evidence'), 'addition evidence')[f'{kind}_capture_file']}"
            for row in addition_rows
        }
        if len(paths) != 1:
            raise ReviewPackageError(
                "CONTROL_TOTAL_MISMATCH", "addition capture file sharing differs"
            )
        path = next(iter(paths))
        document = _mapping(capture_documents.get(path), "addition capture")
        if set(document) != {
            "capture_start_utc",
            "capture_end_utc",
            "requested_ids",
            "result",
        }:
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH", "addition capture top-level schema differs"
            )
        result = _mapping(document.get("result"), "addition capture result")
        data = _mapping(result.get("data"), "addition capture data")
        requested = _sequence(document.get("requested_ids"), "capture requested IDs")
        nodes = _sequence(data.get("nodes"), "capture returned nodes")
        capture_start_text = _iso_datetime(
            document.get("capture_start_utc"),
            context=f"{kind} capture start",
        )
        capture_end_text = _iso_datetime(
            document.get("capture_end_utc"),
            context=f"{kind} capture end",
        )
        capture_start = datetime.fromisoformat(
            capture_start_text.replace("Z", "+00:00")
        )
        capture_end = datetime.fromisoformat(
            capture_end_text.replace("Z", "+00:00")
        )
        if (
            not capture_start_text.endswith("Z")
            or not capture_end_text.endswith("Z")
            or capture_start > capture_end
        ):
            raise ReviewPackageError(
                "INVALID_TIMESTAMP", "addition capture time bounds differ"
            )
        expected_ids = {
            f"{gid_prefix}{_mapping(row.get('catalog_evidence'), 'addition evidence')[identity_field]}"
            for row in addition_rows
        }
        expected_ids_in_order = [
            f"{gid_prefix}{_mapping(row.get('catalog_evidence'), 'addition evidence')[identity_field]}"
            for row in addition_rows
        ]
        node_by_id: dict[str, Mapping[str, Any]] = {}
        for raw_node in nodes:
            node = _mapping(raw_node, "addition capture node")
            identifier = _text(node.get("id"), "addition capture node ID")
            metafields = _mapping(node.get("metafields"), "capture metafields")
            page_info = _mapping(
                metafields.get("pageInfo"), "capture metafield page info"
            )
            updated_text = _iso_datetime(
                node.get("updatedAt"), context=f"{kind} capture node updated time"
            )
            updated_at = datetime.fromisoformat(
                updated_text.replace("Z", "+00:00")
            )
            metafield_nodes = metafields.get("nodes")
            end_cursor = page_info.get("endCursor")
            if isinstance(metafield_nodes, list):
                for raw_metafield in metafield_nodes:
                    metafield = _mapping(
                        raw_metafield, "addition capture metafield"
                    )
                    metafield_updated = _iso_datetime(
                        metafield.get("updatedAt"),
                        context="addition capture metafield updated time",
                    )
                    if (
                        set(metafield)
                        != {"id", "key", "namespace", "type", "updatedAt", "value"}
                        or not metafield_updated.endswith("Z")
                        or datetime.fromisoformat(
                            metafield_updated.replace("Z", "+00:00")
                        )
                        > capture_start
                    ):
                        raise ReviewPackageError(
                            "SOURCE_EVIDENCE_MISMATCH",
                            "addition capture metafield provenance differs",
                        )
            if (
                set(node) != expected_node_fields[kind]
                or set(metafields) != {"nodes", "pageInfo"}
                or not isinstance(metafield_nodes, list)
                or set(page_info) != {"endCursor", "hasNextPage"}
                or page_info.get("hasNextPage") is not False
                or (
                    (bool(metafield_nodes) and not isinstance(end_cursor, str))
                    or (not metafield_nodes and end_cursor is not None)
                )
                or (isinstance(end_cursor, str) and not end_cursor)
                or not updated_text.endswith("Z")
                or updated_at > capture_start
                or len(metafield_nodes) != (4 if kind == "product" else 0)
                or identifier in node_by_id
                or (kind == "variant"
                    and set(_mapping(node.get("product"), "capture product")) != {"id"})
            ):
                raise ReviewPackageError(
                    "SOURCE_EVIDENCE_MISMATCH", "addition capture node schema differs"
                )
            node_by_id[identifier] = node
        if (
            set(result) != {"data"}
            or set(data) != {"nodes"}
            or len(requested) != 4
            or any(not isinstance(item, str) or not item for item in requested)
            or len(set(requested)) != 4
            or set(requested) != expected_ids
            or set(node_by_id) != expected_ids
            or list(requested) != expected_ids_in_order
            or list(node_by_id) != expected_ids_in_order
        ):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "addition capture requested/returned identity coverage differs",
            )
        capture_indexes[kind] = (document, node_by_id)

    product_capture_end = datetime.fromisoformat(
        _text(
            capture_indexes["product"][0].get("capture_end_utc"),
            "product capture end",
        ).replace("Z", "+00:00")
    )
    variant_capture_start = datetime.fromisoformat(
        _text(
            capture_indexes["variant"][0].get("capture_start_utc"),
            "Variant capture start",
        ).replace("Z", "+00:00")
    )
    if product_capture_end >= variant_capture_start:
        raise ReviewPackageError(
            "INVALID_TIMESTAMP", "addition product/Variant capture order differs"
        )

    for row in addition_rows:
        evidence = _mapping(row.get("catalog_evidence"), "addition catalog evidence")
        product_document, product_nodes = capture_indexes["product"]
        variant_document, variant_nodes = capture_indexes["variant"]
        product_gid = f"{product_gid_prefix}{evidence.get('product_id')}"
        variant_gid = f"{variant_gid_prefix}{evidence.get('variant_id')}"
        product_node = product_nodes[product_gid]
        variant_node = variant_nodes[variant_gid]
        census = census_by_variant.get(str(evidence.get("variant_id")))
        census_product = (
            _mapping(census.get("product"), "addition census product")
            if census is not None
            else None
        )
        product_meta = _mapping(product_node.get("metafields"), "product metafields")
        variant_meta = _mapping(variant_node.get("metafields"), "Variant metafields")
        product_page = _mapping(product_meta.get("pageInfo"), "product page info")
        variant_page = _mapping(variant_meta.get("pageInfo"), "Variant page info")
        if (
            evidence.get("product_capture_start_utc")
            != product_document.get("capture_start_utc")
            or evidence.get("product_capture_end_utc")
            != product_document.get("capture_end_utc")
            or evidence.get("variant_capture_start_utc")
            != variant_document.get("capture_start_utc")
            or evidence.get("variant_capture_end_utc")
            != variant_document.get("capture_end_utc")
            or evidence.get("product_title_raw") != product_node.get("title")
            or census_product is None
            or product_node.get("status") != census_product.get("status")
            or evidence.get("vendor_raw") != product_node.get("vendor")
            or evidence.get("description_html_raw")
            != product_node.get("descriptionHtml")
            or evidence.get("product_metafields_raw") != product_meta.get("nodes")
            or evidence.get("variant_title_raw") != variant_node.get("title")
            or evidence.get("sku_raw") != variant_node.get("sku")
            or evidence.get("barcode_raw") != variant_node.get("barcode")
            or evidence.get("selected_options_raw")
            != variant_node.get("selectedOptions")
            or evidence.get("variant_metafields_raw") != variant_meta.get("nodes")
            or product_node.get("updatedAt") != variant_node.get("updatedAt")
            or _mapping(variant_node.get("product"), "Variant capture product").get(
                "id"
            )
            != product_gid
            or evidence.get("identity_metafields_available")
            is not bool(variant_meta.get("nodes"))
            or evidence.get("metafield_pagination_complete")
            is not (not product_page.get("hasNextPage"))
            or evidence.get("metafield_pagination_complete")
            is not (not variant_page.get("hasNextPage"))
        ):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "addition capture node provenance differs",
            )
    return references


def _validate_original_identity_cohorts(
    projection: _ProjectionBuilder, catalog_ids: set[str]
) -> int:
    """Ensure every effective identity surface preserves the original cohort."""

    if set(projection.original_identity_cohorts) != set(
        _ORIGINAL_IDENTITY_COHORT_TABLES
    ) or any(
        cohort != catalog_ids
        for cohort in projection.original_identity_cohorts.values()
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "effective identity surfaces do not preserve the catalog cohort"
        )
    return len(projection.original_identity_cohorts)


def _validate_relationship_source_provenance(
    rows: Sequence[Mapping[str, Any]], projection: _ProjectionBuilder
) -> None:
    """Bind each frozen relationship preview to its exact source occurrence."""

    field_map = (
        ("supplier_name_raw", "supplier_name_raw"),
        ("supplier_code_exact", "supplier_sku"),
        ("source_description_raw", "supplier_description"),
        ("source_package_type_raw", "package_type"),
        ("source_file", "source_file"),
        ("source_page", "source_page"),
        ("printed_page", "printed_page"),
        ("source_sha256", "source_sha256"),
        ("source_period_raw", "source_period_status"),
        ("source_territory", "territory"),
        ("source_case_pack_raw", "supplier_pack_count"),
        ("source_physical_count_raw", "physical_units_per_case"),
        ("source_size_raw", "size_text"),
        ("source_split_availability", "split_availability"),
        ("source_split_fee_basis", "split_fee_basis_inclusion"),
        ("source_assortment_line_terms", "assortment_line_terms"),
        ("source_vintage_raw", "vintage_raw"),
    )
    for row in rows:
        offer_id = _text(
            row.get("source_offer_id"), "relationship source offer ID"
        )
        source = _source_offer_contract(
            projection, offer_id, context="relationship"
        )
        tier_ids = row.get("source_tier_ids")
        expected_tier_ids = [
            str(tier.get("source_tier_id"))
            for tier in projection.tiers_by_offer.get(offer_id, [])
        ]
        if (
            any(left not in row for left, _ in field_map)
            or any(row[left] != source[right] for left, right in field_map)
            or not isinstance(tier_ids, list)
            or tier_ids != expected_tier_ids
            or len(tier_ids) != len(set(tier_ids))
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "relationship source-offer or Tier provenance differs"
            )


def _supplier_label_matches_source_file(label: str, source_file: str) -> bool:
    """Corroborate a runtime supplier batch label from its sealed PDF name."""

    compact_label = "".join(character for character in label.casefold() if character.isalnum())
    compact_file = "".join(
        character for character in Path(source_file).stem.casefold() if character.isalnum()
    )
    if compact_label and compact_label in compact_file:
        return True
    words = re.findall(r"[A-Za-z]+", Path(source_file).stem)
    initials = "".join(word[0] for word in words).upper()
    return label.isupper() and len(label) >= 2 and initials.startswith(label)


def _derive_display_supplier_contract(
    partitions: Sequence[Mapping[str, Any]],
    display_rows: Sequence[Mapping[str, Any]],
    projection: _ProjectionBuilder,
) -> tuple[dict[str, str], dict[str, list[Mapping[str, Any]]], str]:
    """Derive normalized supplier labels without embedding private batch values."""

    display_by_variant: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    source_files_by_variant: defaultdict[str, set[str]] = defaultdict(set)
    displayed_source_files: set[str] = set()
    for row in display_rows:
        variant_id = _text(row.get("variant_id"), "display Variant ID")
        offer_id = _text(row.get("source_offer_id"), "display source offer ID")
        source = _source_offer_contract(
            projection, offer_id, context="display supplier"
        )
        source_file = _text(source.get("source_file"), "display source file")
        display_by_variant[variant_id].append(row)
        source_files_by_variant[variant_id].add(source_file)
        displayed_source_files.add(source_file)

    labels_by_source_file: defaultdict[str, set[str]] = defaultdict(set)
    single_source_anchors = 0
    unfilled_labels: set[str] = set()
    for partition in partitions:
        variant_id = _text(partition.get("variant_id"), "partition Variant ID")
        source_files = source_files_by_variant.get(variant_id, set())
        if len(source_files) == 1:
            single_source_anchors += 1
            labels_by_source_file[next(iter(source_files))].add(
                _text(partition.get("supplier_batch"), "partition supplier batch")
            )
        elif not source_files:
            unfilled_labels.add(
                _text(partition.get("supplier_batch"), "unfilled supplier batch")
            )
    if (
        len(displayed_source_files) != 8
        or single_source_anchors != 1_099
        or set(labels_by_source_file) != displayed_source_files
        or any(len(labels) != 1 for labels in labels_by_source_file.values())
        or len(unfilled_labels) != 1
        or sum(1 for variant in display_by_variant if display_by_variant[variant])
        != 1_886
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "display supplier/source-file normalization differs"
        )
    supplier_by_source_file = {
        source_file: next(iter(labels))
        for source_file, labels in labels_by_source_file.items()
    }
    if any(
        not _supplier_label_matches_source_file(label, source_file)
        for source_file, label in supplier_by_source_file.items()
    ) or len(set(supplier_by_source_file.values())) != 8:
        raise ReviewPackageError(
            "JOIN_MISMATCH", "display supplier label is not supported by its source file"
        )
    return supplier_by_source_file, dict(display_by_variant), next(iter(unfilled_labels))


def _validate_partition_derivations(
    partitions: Sequence[Mapping[str, Any]],
    catalog_by_variant: Mapping[str, Mapping[str, Any]],
    display_rows: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
    conditional_gifts: Sequence[Mapping[str, Any]],
    alcohol_gifts: Sequence[Mapping[str, Any]],
    fixed_components: Sequence[Mapping[str, Any]],
    remaining_components: Sequence[Mapping[str, Any]],
    projection: _ProjectionBuilder,
) -> Counter[int]:
    """Derive every catalog card's review partition and identity state."""

    labels = {
        1: "Supported standard candidates — no recorded active material mapping requirement",
        2: "Supported offer evidence — identity, fee, territory, unit or other requirements remain",
        3: "Alternate case / retail pack / conditional gift review",
        4: "Mixed-alcohol gift / fixed-combo component review only",
        5: "No supported offer / unresolved identity / missing source / policy exclusion",
    }
    supported_variants: set[str] = set()
    representations_by_variant: defaultdict[str, set[tuple[Any, ...]]] = defaultdict(set)
    nonstandard_variants: set[str] = set()
    for row in display_rows:
        if row.get("disposition") != "PROPOSED_REVIEW_CANDIDATE":
            continue
        variant_id = _text(row.get("variant_id"), "supported display Variant ID")
        offer_id = _text(row.get("source_offer_id"), "supported display offer ID")
        source = _source_offer_contract(
            projection, offer_id, context="partition supported offer"
        )
        representation = (
            source.get("package_type"),
            source.get("size_text"),
            source.get("supplier_pack_count"),
            source.get("retail_packs_per_case"),
            source.get("physical_units_per_case"),
        )
        supported_variants.add(variant_id)
        representations_by_variant[variant_id].add(representation)
        if source.get("package_type") != "STANDARD":
            nonstandard_variants.add(variant_id)
    alternate_variants = nonstandard_variants | {
        variant_id
        for variant_id, representations in representations_by_variant.items()
        if len(representations) > 1
    }
    gift_variants = {
        str(row.get("variant_id")) for row in conditional_gifts
    }
    component_variants = {
        str(row.get("variant_id")) for row in alcohol_gifts
    } | {
        str(row.get("variant_id")) for row in fixed_components
    } | {
        str(row.get("catalog_variant_id"))
        for row in remaining_components
        if isinstance(row.get("catalog_variant_id"), str)
    }
    routes_by_variant: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for route in routes:
        routes_by_variant[str(route.get("variant_id"))].append(route)

    observed: Counter[int] = Counter()
    for partition in partitions:
        variant_id = _text(partition.get("variant_id"), "partition Variant ID")
        catalog = catalog_by_variant.get(variant_id)
        if catalog is None:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "partition Variant is outside the catalog cohort"
            )
        status = catalog.get("status")
        if status == "POLICY EXCLUDED":
            expected_partition = 5
        elif variant_id in component_variants:
            expected_partition = 4
        elif variant_id in gift_variants or variant_id in alternate_variants:
            expected_partition = 3
        elif variant_id in supported_variants and (
            routes_by_variant.get(variant_id) or status == "CONFLICT"
        ):
            expected_partition = 2
        elif variant_id in supported_variants:
            expected_partition = 1
        else:
            expected_partition = 5

        if status != "PROPOSED MATCH":
            expected_identity = "BLOCKED_OR_NO_SUPPORTED_IDENTITY"
        elif any(
            route.get("daytime_request_class")
            == "EXACT_RECORD_LABEL_OR_SELLING_UNIT_DECISION"
            for route in routes_by_variant.get(variant_id, ())
        ):
            expected_identity = (
                "SUPPORTED_EVIDENCE_WITH_ITEM_IDENTITY_OR_UNIT_QUESTION"
            )
        else:
            expected_identity = "REVIEWABLE_SUPPORTED_RELATIONSHIPS_UNAPPROVED"

        if (
            partition.get("top_level_partition") != expected_partition
            or partition.get("partition_label") != labels[expected_partition]
            or partition.get("identity_mapping_preview") != expected_identity
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "review partition derivation differs"
            )
        observed[expected_partition] += 1
    expected_counts = Counter({1: 1_035, 2: 173, 3: 83, 4: 162, 5: 547})
    if observed != expected_counts:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "review partition controls differ"
        )
    return observed


def _validate_display_offer_projection(
    row: Mapping[str, Any],
    *,
    relationship: Mapping[str, Any] | None,
    sibling: Mapping[str, Any] | None,
    diagnostic: Mapping[str, Any] | None,
    projection: _ProjectionBuilder,
    expected_supplier: str,
) -> str:
    """Bind the reviewer-visible offer projection to its precedence source."""

    offer_id = _text(row.get("source_offer_id"), "display source offer ID")
    source = _source_offer_contract(projection, offer_id, context="display offer")
    expected_tiers = [
        str(item.get("source_tier_id"))
        for item in projection.tiers_by_offer.get(offer_id, ())
    ]
    if (
        row.get("source_record_sha256")
        != projection.source_offer_record_hashes.get(offer_id)
        or row.get("supplier") != expected_supplier
        or row.get("supplier_code_exact") != source.get("supplier_sku")
        or row.get("source_page") != source.get("source_page")
        or row.get("source_sha256") != source.get("source_sha256")
        or row.get("source_physical_count_raw")
        != source.get("physical_units_per_case")
        or row.get("all_tier_ids") != expected_tiers
        or len(expected_tiers) != len(set(expected_tiers))
        or row.get("explicit_null_precedence") is not True
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "display offer source-record projection differs"
        )

    if sibling is not None:
        field_map = (
            ("disposition", "candidate_disposition"),
            (
                "display_containers_per_retail_pack",
                "proposed_containers_per_retail_pack",
            ),
            ("display_physical_count", "proposed_physical_units_per_case"),
            ("display_retail_packs", "proposed_retail_packs_per_case"),
            (
                "reviewed_qualifying_units_per_case",
                "proposed_qualifying_units_per_case",
            ),
            ("reviewed_shopify_units_per_case", "proposed_shopify_units_per_case"),
            ("supplier_code_exact", "supplier_code"),
            ("source_record_sha256", "source_record_sha256"),
        )
        if diagnostic is not None or any(
            row.get(display_field) != sibling.get(sibling_field)
            for display_field, sibling_field in field_map
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "display sibling-overlay projection differs"
            )
        return "sibling"

    if relationship is not None:
        relationship_fields = (
            ("disposition", "candidate_disposition"),
            (
                "display_containers_per_retail_pack",
                "source_retail_pack_raw",
            ),
            ("display_physical_count", "source_physical_count_raw"),
            (
                "reviewed_qualifying_units_per_case",
                "reviewed_qualifying_units_per_case",
            ),
            (
                "reviewed_shopify_units_per_case",
                "reviewed_shopify_units_per_case",
            ),
        )
        if diagnostic is not None or any(
            row.get(display_field) != relationship.get(relationship_field)
            for display_field, relationship_field in relationship_fields
        ) or row.get("display_retail_packs") != source.get(
            "retail_packs_per_case"
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "display frozen-relationship projection differs"
            )
        return "relationship"

    if (
        diagnostic is None
        or row.get("disposition")
        != "SAME_CODE_DIAGNOSTIC_NOT_REVIEWED_AS_IDENTITY"
        or row.get("display_containers_per_retail_pack")
        != source.get("containers_per_pack_raw")
        or row.get("display_physical_count")
        != source.get("physical_units_per_case")
        or row.get("display_retail_packs") != source.get("retail_packs_per_case")
        or row.get("reviewed_qualifying_units_per_case") is not None
        or row.get("reviewed_shopify_units_per_case") is not None
        or diagnostic.get("reviewed_qualifying_units_per_case") is not None
        or diagnostic.get("reviewed_shopify_units_per_case") is not None
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "same-code diagnostic display projection differs"
        )
    return "diagnostic"


def _derived_owner_preference(owner: Mapping[str, Any]) -> str:
    """Derive the V5 display label from scoped owner prose, never private literals."""

    decision_id = _text(owner.get("owner_decision_id"), "owner decision ID")
    authority_scope = _text(owner.get("authority_scope"), "owner authority scope")
    decision = _text(owner.get("decision"), "owner decision")
    item_scope = _text(owner.get("item_scope"), "owner item scope")
    if authority_scope == "OWNER_CONFIRMED_EXPRESSION_SCOPE":
        match = re.fullmatch(
            r"Regular code ([A-Za-z0-9-]+) is ([1-9][0-9]*) proof and is preferred "
            r"over separately printed code ([A-Za-z0-9-]+) at ([1-9][0-9]*) proof\. "
            r"The phrase [^.]+ does not identify [^.]+ proof\.",
            decision,
        )
        if match is None:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "owner expression preference prose differs"
            )
        preferred_code, preferred_proof, other_code, other_proof = match.groups()
        if preferred_code not in item_scope or other_code not in item_scope:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "owner expression preference item scope differs"
            )
        return (
            f"{decision_id}_OWNER_REGULAR{preferred_proof}_EXPRESSION_{preferred_code}; "
            f"NOT_{other_proof}PROOF_{other_code}; NOT_MAPPING_APPROVAL"
        )
    if authority_scope == "OWNER_CONFIRMED_PACK_PREFERENCE":
        match = re.fullmatch(
            r"([1-9][0-9]*) individual ([1-9][0-9]*)mL bottles per case, "
            r"published at \$[0-9]+\.[0-9]{2}; the code suffix is retained",
            decision,
        )
        scope_match = re.fullmatch(
            r".+ ([1-9][0-9]*)ML; [^;]+ ([A-Za-z0-9-]+)", item_scope
        )
        if match is None or scope_match is None or match.group(2) != scope_match.group(1):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "owner pack preference prose or item scope differs"
            )
        pack_count, size = match.groups()
        source_code = scope_match.group(2)
        return (
            f"OWNER_PREFERS_{source_code}_{pack_count}_INDIVIDUAL_{size}ML_BOTTLES; "
            "NOT_APPROVAL_OR_PACK_BREAKING_PERMISSION"
        )
    raise ReviewPackageError(
        "JOIN_MISMATCH", "referenced owner preference scope is unsupported"
    )


def _validate_owner_preference_scopes(
    family_rows: Sequence[Mapping[str, Any]],
    relationship_rows: Sequence[Mapping[str, Any]],
    owner_by_id: Mapping[str, Mapping[str, Any]],
    scope_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, int]:
    """Resolve each private preference reference to its exact owner/Variant scope."""

    referenced_owner_ids: set[str] = set()
    owner_scoped_families = 0
    for family in family_rows:
        decision_ids = family.get("owner_preference_decision_ids")
        if not isinstance(decision_ids, list):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "family owner-preference references must be an array"
            )
        if not decision_ids:
            continue
        owner_scoped_families += 1
        if len(decision_ids) != 1:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "family owner preference must have one exact decision"
            )
        decision_id = str(decision_ids[0])
        owner = owner_by_id.get(decision_id)
        scope = scope_by_id.get(decision_id)
        variant_id = family.get("variant_id")
        if (
            owner is None
            or scope is None
            or scope.get("explicit_variant_ids") != [variant_id]
            or owner.get("mapping_approval") is not False
            or owner.get("price_approval") is not False
            or owner.get("purchasing_approval") is not False
            or scope.get("approval_inferred") is not False
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "family owner preference is outside its exact review scope"
            )
        if family.get("preference") != _derived_owner_preference(owner):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "family preference differs from its owner decision evidence"
            )
        referenced_owner_ids.add(decision_id)

    owner_scoped_relationships = 0
    for relationship in relationship_rows:
        decision_ids = relationship.get("owner_preference_decision_ids")
        if not isinstance(decision_ids, list):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "relationship owner-preference references must be an array"
            )
        if not decision_ids:
            continue
        owner_scoped_relationships += 1
        if len(decision_ids) != 1:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "relationship owner preference has ambiguous scope"
            )
        decision_id = str(decision_ids[0])
        scope = scope_by_id.get(decision_id)
        if (
            decision_id not in owner_by_id
            or scope is None
            or scope.get("explicit_variant_ids") != [relationship.get("variant_id")]
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "relationship owner preference is outside its Variant scope"
            )
        referenced_owner_ids.add(decision_id)

    controls = {
        "owner_scoped_families": owner_scoped_families,
        "owner_scoped_relationships": owner_scoped_relationships,
        "referenced_owner_decisions": len(referenced_owner_ids),
    }
    if controls != {
        "owner_scoped_families": 2,
        "owner_scoped_relationships": 17,
        "referenced_owner_decisions": 2,
    }:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "owner-preference scope controls differ"
        )
    return controls


def _validate_dependency_route_provenance(
    route: Mapping[str, Any],
    member: Mapping[str, Any] | None,
    dependency: Mapping[str, Any] | None,
) -> None:
    """Bind every reviewer-facing request field to its frozen dependency member."""

    if member is None or dependency is None:
        raise ReviewPackageError(
            "JOIN_MISMATCH", "active dependency route lacks its source records"
        )
    variant = str(route.get("variant_id"))
    member_fields = (
        ("exact_evidence_needed", "exact_evidence_needed"),
        ("blocks_candidate_identity_raw", "blocks_candidate_identity"),
        ("display_status", "display_status"),
        ("who_can_supply", "who_can_supply"),
        ("prior_attempt_evidence", "night2_attempt_evidence"),
    )
    if (
        variant != str(dependency.get("variant_id"))
        or variant != str(member.get("variant_id"))
        or route.get("source_group_id") != member.get("group_id")
        or route.get("source_dependency_sha256")
        != canonical_record_sha256(dependency)
        or route.get("source_member_sha256") != canonical_record_sha256(member)
        or route.get("raw_dependency_preserved")
        != member.get("dependency_code_or_raw_text")
        or route.get("source_provider_class_preserved")
        != member.get("primary_required_evidence_class")
        or route.get("source_status") != member.get("source_status")
        or route.get("source_status") != dependency.get("status")
        or route.get("source_status_or_resolution_changed") is not False
        or any(
            route.get(route_field) != member.get(member_field)
            for route_field, member_field in member_fields
        )
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "active dependency route provenance differs"
        )


def _validate_specialist_evidence_projections(
    tables: Mapping[str, PackageTable],
    specialist_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, int]:
    """Bind displayed overlays and alcohol reviews to sealed specialist rows."""

    contracts = {
        "alcohol_gift_components_v5": (
            "challenge_review_id",
            frozenset({"root_adjudication", "root_reviewed_at_utc"}),
            8,
        ),
        "source_review_overlays": (
            "recommendation_id",
            frozenset(
                {
                    "addendum_revision",
                    "does_not_replace_frozen_source_row",
                    "explicit_reviewed_null_precedence",
                    "overlay_field_changes",
                    "root_adjudication",
                    "second_read_only_challenge",
                }
            ),
            25,
        ),
    }
    controls: dict[str, int] = {}
    rows_by_logical: dict[str, dict[str, Mapping[str, Any]]] = {}
    for logical_name, (identity_field, table_only_fields, expected_rows) in contracts.items():
        table_rows = tables[logical_name].rows
        evidence_rows = specialist_evidence.get(logical_name)
        if evidence_rows is None or len(table_rows) != expected_rows or len(evidence_rows) != expected_rows:
            raise ReviewPackageError(
                "CONTROL_TOTAL_MISMATCH",
                "sealed specialist evidence row count differs",
                table=logical_name,
            )
        table_by_id: dict[str, Mapping[str, Any]] = {}
        for row in table_rows:
            identifier = _text(
                row.get(identity_field), f"{logical_name} specialist identity"
            )
            if identifier in table_by_id:
                raise ReviewPackageError(
                    "TABLE_KEY_NOT_UNIQUE",
                    "specialist-backed table identity is duplicated",
                    table=logical_name,
                )
            table_by_id[identifier] = row
        evidence_ids: set[str] = set()
        for evidence_row in evidence_rows:
            identifier = _text(
                evidence_row.get(identity_field),
                f"{logical_name} sealed specialist identity",
            )
            table_row = table_by_id.get(identifier)
            if (
                identifier in evidence_ids
                or table_row is None
                or set(table_row) - set(evidence_row) != set(table_only_fields)
                or any(
                    key not in table_row or table_row[key] != value
                    for key, value in evidence_row.items()
                )
            ):
                raise ReviewPackageError(
                    "SOURCE_EVIDENCE_MISMATCH",
                    "sealed specialist evidence projection differs",
                    table=logical_name,
                )
            evidence_ids.add(identifier)
        if evidence_ids != set(table_by_id):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "sealed specialist evidence identity coverage differs",
                table=logical_name,
            )
        rows_by_logical[logical_name] = table_by_id
        controls[logical_name] = len(evidence_ids)

    alcohol_rows = rows_by_logical["alcohol_gift_components_v5"].values()
    for row in alcohol_rows:
        _text(row.get("root_adjudication"), "alcohol specialist root adjudication")
        _iso_datetime(
            row.get("root_reviewed_at_utc"),
            context="alcohol specialist root-reviewed timestamp",
        )

    overlay_rows = rows_by_logical["source_review_overlays"]
    overlay_change_rows = [
        row
        for row in tables["daytime_addendum__exact_change_log"].rows
        if row.get("kind") == "ADD_SOURCE_REVIEW_OVERLAY"
    ]
    overlay_changes = {
        str(row.get("change_id")): row
        for row in overlay_change_rows
    }
    if (
        len(overlay_change_rows) != 25
        or len(overlay_changes) != 25
        or set(overlay_changes) != set(overlay_rows)
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "overlay exact-change-log coverage differs"
        )
    for identifier, row in overlay_rows.items():
        change = overlay_changes[identifier]
        if (
            row.get("addendum_revision") != A1_ADAPTER
            or row.get("does_not_replace_frozen_source_row") is not True
            or row.get("explicit_reviewed_null_precedence") is not True
            or change.get("target_source_offer_id") != row.get("source_offer_id")
            or change.get("before_record_sha256") != row.get("source_record_sha256")
            or change.get("after_addendum_record_sha256")
            != canonical_record_sha256(row)
            or change.get("fields") != row.get("overlay_field_changes")
            or change.get("frozen_record_changed") is not False
        ):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "overlay specialist/change-log provenance differs",
            )
    controls["overlay_change_log_rows"] = len(overlay_changes)
    return controls


def _validate_alcohol_gift_provenance(
    rows: Sequence[Mapping[str, Any]],
    catalog_by_variant: Mapping[str, Mapping[str, Any]],
    projection: _ProjectionBuilder,
    source_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Bind specialist gift facts to catalog, offer, Tier, and visual evidence."""

    source_fields = frozenset(
        {
            "effective_from",
            "effective_through",
            "physical_units_per_case",
            "printed_page",
            "qualification_basis",
            "qualifying_units_per_case",
            "retail_packs_per_case",
            "shopify_units_per_case",
            "size_text",
            "source_evidence",
            "source_file",
            "source_offer_id",
            "source_page",
            "source_period_status",
            "source_sha256",
            "split_availability",
            "split_fee_basis_inclusion",
            "supplier_description",
            "supplier_name_raw",
            "supplier_pack_count",
            "supplier_sku",
            "territory",
        }
    )
    tier_field_map = {
        "break_quantity_raw": "break_quantity",
        "break_unit_raw": "break_unit",
        "case_price_printed": "case_price_printed",
        "normalized_tier_type": "tier_type",
        "source_evidence": "source_evidence",
        "source_sha256": "source_sha256",
        "source_tier_id": "source_tier_id",
        "split_inclusive_price_printed": "split_inclusive_price_printed",
        "unit_pack_bottle_price_printed": "unit_pack_bottle_price_printed",
    }
    evidence_by_path: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for evidence in source_evidence:
        logical_path = evidence.get("logical_path")
        if isinstance(logical_path, str):
            evidence_by_path[logical_path].append(evidence)
    visual_paths: set[str] = set()
    for row in rows:
        variant_id = _text(row.get("variant_id"), "alcohol gift Variant ID")
        offer_id = _text(row.get("source_offer_id"), "alcohol gift offer ID")
        catalog = catalog_by_variant.get(variant_id)
        offer = _source_offer_contract(
            projection, offer_id, context="alcohol gift source"
        )
        source = _mapping(row.get("source"), "alcohol gift source")
        source_ladder = source.get("full_price_ladder")
        expected_ladder = projection.tiers_by_offer.get(offer_id, [])
        visual_path = _text(
            row.get("visual_evidence_file"), "alcohol gift visual evidence path"
        )
        visual_rows = evidence_by_path.get(visual_path, [])
        if (
            catalog is None
            or set(source) != set(source_fields | {"full_price_ladder"})
            or any(source.get(field) != offer.get(field) for field in source_fields)
            or not isinstance(source_ladder, list)
            or len(source_ladder) != len(expected_ladder)
            or any(
                not isinstance(actual, dict)
                or set(actual) != set(tier_field_map)
                or any(
                    actual.get(actual_field) != expected.get(expected_field)
                    for actual_field, expected_field in tier_field_map.items()
                )
                for actual, expected in zip(
                    source_ladder, expected_ladder, strict=True
                )
            )
            or row.get("current_catalog_title")
            != catalog.get("current_product_title")
            or row.get("current_catalog_title") != catalog.get("product_title")
            or row.get("current_variant_title")
            != catalog.get("current_variant_title")
            or row.get("current_variant_title") != catalog.get("variant_title")
            or len(visual_rows) != 1
            or visual_rows[0].get("evidence_content_sha256")
            != row.get("visual_evidence_sha256")
            or visual_rows[0].get("source_pdf_file") != source.get("source_file")
            or visual_rows[0].get("source_pdf_sha256")
            != source.get("source_sha256")
            or visual_rows[0].get("physical_page") != source.get("source_page")
            or row.get("visually_checked_this_source_occurrence") is not True
        ):
            raise ReviewPackageError(
                "SOURCE_EVIDENCE_MISMATCH",
                "alcohol gift catalog/source/Tier/visual provenance differs",
            )
        visual_paths.add(visual_path)
    controls = {"rows": len(rows), "visual_evidence_files": len(visual_paths)}
    if controls != {"rows": 8, "visual_evidence_files": 6}:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "alcohol gift provenance controls differ"
        )
    return controls


def _validate_snapshot_relationships(
    root: Mapping[str, Any],
    tables: Mapping[str, PackageTable],
    embedded_by_restored: Mapping[str, Mapping[str, Any]],
    projection: _ProjectionBuilder,
    capture_documents: Mapping[str, Mapping[str, Any]],
    specialist_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    limits: ReviewLimits,
) -> dict[str, Any]:
    catalog_rows = tables["catalog_coverage_v5"].rows
    census_rows = tables["current_identity_census"].rows
    catalog_ids, census_ids = _validate_catalog_census_cohorts(
        catalog_rows,
        census_rows,
        (
            tables["current_additions_review"].rows,
            tables["current_additions_review_v5"].rows,
        ),
    )
    identity_surfaces = _validate_original_identity_cohorts(projection, catalog_ids)
    addition_capture_references = _validate_addition_capture_evidence(
        tables["current_additions_review"].rows,
        census_rows,
        embedded_by_restored,
        capture_documents,
        limits,
    )
    specialist_controls = _validate_specialist_evidence_projections(
        tables, specialist_evidence
    )
    catalog_by_variant = {
        str(row["variant_id"]): row for row in catalog_rows
    }
    alcohol_gift_controls = _validate_alcohol_gift_provenance(
        tables["alcohol_gift_components_v5"].rows,
        catalog_by_variant,
        projection,
        _sequence(root.get("source_evidence"), "source evidence"),
    )
    catalog_ordinals = {
        str(row["variant_id"]): ordinal
        for ordinal, row in enumerate(catalog_rows, start=1)
    }
    family_rows = tables["multi_offer_families_v5"].rows
    family_by_variant = {
        str(row["variant_id"]): row for row in family_rows
    }
    if len(family_by_variant) != len(catalog_ids):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "V5 family identities do not cover the catalog cohort"
        )
    owner_rows = tables["owner_decisions"].rows
    owner_ids = {str(row["owner_decision_id"]) for row in owner_rows}
    if len(owner_rows) != 20 or len(owner_ids) != 20:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "owner decision controls differ")
    scopes = tables["owner_decision_display_scopes"].rows
    if {str(row.get("owner_decision_id")) for row in scopes} != owner_ids:
        raise ReviewPackageError("JOIN_MISMATCH", "owner display scopes differ")
    owner_by_id = {str(row["owner_decision_id"]): row for row in owner_rows}
    scope_by_id = {str(row["owner_decision_id"]): row for row in scopes}
    scope_binding_counts: Counter[str] = Counter()
    explicit_scope_variants: set[str] = set()
    explicit_scope_references = 0
    for row in scopes:
        if row.get("approval_inferred") is not False:
            raise ReviewPackageError("UNAUTHORIZED_APPROVAL_CLAIM", "owner display scope inferred approval")
        owner = owner_by_id[str(row["owner_decision_id"])]
        if (
            row.get("source_owner_record_sha256")
            != canonical_record_sha256(owner)
            or row.get("original_scope") != owner.get("item_scope")
        ):
            raise ReviewPackageError("JOIN_MISMATCH", "owner display hash differs")
        variants = row.get("explicit_variant_ids")
        scope_binding = row.get("scope_binding")
        scope_binding_counts[str(scope_binding)] += 1
        if scope_binding == "EXACT_NAMED_VARIANT_ONLY":
            if (
                not isinstance(variants, list)
                or not variants
                or len(variants) != len(set(variants))
                or any(item not in catalog_ids for item in variants)
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "owner display scope contains unknown Variant ID"
                )
            explicit_scope_references += len(variants)
            explicit_scope_variants.update(variants)
        elif (
            scope_binding
            == "RETAINED_ORIGINAL_LEDGER_SCOPE; NO_NEW_ITEM_BINDING_INFERRED"
        ):
            if variants is not None:
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "unbound owner ledger scope gained a Variant"
                )
        else:
            raise ReviewPackageError("JOIN_MISMATCH", "owner display scope contains unknown Variant ID")
    if (
        scope_binding_counts
        != Counter(
            {
                "EXACT_NAMED_VARIANT_ONLY": 11,
                "RETAINED_ORIGINAL_LEDGER_SCOPE; NO_NEW_ITEM_BINDING_INFERRED": 9,
            }
        )
        or explicit_scope_references != 12
        or len(explicit_scope_variants) != 12
    ):
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "owner display-scope controls differ"
        )

    naturally_scoped_variants: defaultdict[str, list[str]] = defaultdict(list)
    for catalog in catalog_rows:
        variant_id = str(catalog.get("variant_id"))
        for field in ("owner_clarification_id", "v4_1_owner_decision_id"):
            decision_id = catalog.get(field)
            if isinstance(decision_id, str) and decision_id:
                naturally_scoped_variants[decision_id].append(variant_id)
    for family in family_rows:
        variant_id = str(family.get("variant_id"))
        decision_ids = family.get("owner_preference_decision_ids")
        if not isinstance(decision_ids, list):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "family owner scope references must be an array"
            )
        for decision_id in decision_ids:
            if variant_id not in naturally_scoped_variants[str(decision_id)]:
                naturally_scoped_variants[str(decision_id)].append(variant_id)
    if (
        len(naturally_scoped_variants) != 8
        or sum(len(variants) for variants in naturally_scoped_variants.values()) != 9
        or any(
            decision_id not in scope_by_id
            or not isinstance(
                scope_by_id[decision_id].get("explicit_variant_ids"), list
            )
            or len(scope_by_id[decision_id]["explicit_variant_ids"])
            != len(variants)
            or set(scope_by_id[decision_id]["explicit_variant_ids"])
            != set(variants)
            for decision_id, variants in naturally_scoped_variants.items()
        )
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "owner display scope differs from natural record references"
        )
    prior_answers = tables["requests__prior_owner_answers_do_not_reask"].rows
    if (
        len(prior_answers) != len(owner_rows)
        or {str(row.get("owner_decision_id")) for row in prior_answers} != owner_ids
        or any(
            row.get("effective_v5_record_sha256")
            != canonical_record_sha256(owner_by_id[str(row.get("owner_decision_id"))])
            for row in prior_answers
        )
        or any(
            row.get("authority_scope_preserved")
            != owner_by_id[str(row.get("owner_decision_id"))].get("authority_scope")
            or row.get("decision_preserved")
            != owner_by_id[str(row.get("owner_decision_id"))].get("decision")
            or row.get("item_scope")
            != owner_by_id[str(row.get("owner_decision_id"))].get("item_scope")
            or row.get("remaining_boundary_preserved")
            != owner_by_id[str(row.get("owner_decision_id"))].get("remaining_boundary")
            or row.get("temporal_scope_preserved")
            != owner_by_id[str(row.get("owner_decision_id"))].get("temporal_scope")
            or row.get("questions_sent") != 0
            for row in prior_answers
        )
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "prior owner-answer preservation differs"
        )
    owner_preference_scopes = _validate_owner_preference_scopes(
        family_rows,
        tables["variant_offer_relationships_v5"].rows,
        owner_by_id,
        scope_by_id,
    )
    retained_questions = tables[
        "requests__retained_owner_questions_policy_check"
    ].rows
    retained_question_ids: set[str] = set()
    for row in retained_questions:
        question_id = _text(row.get("question_id"), "retained question ID")
        source_question = _mapping(
            row.get("source_question_unchanged"), "retained source question"
        )
        recommendation_id = _text(
            source_question.get("recommendation_id"),
            "retained source recommendation ID",
        )
        if question_id in retained_question_ids:
            raise ReviewPackageError(
                "TABLE_KEY_NOT_UNIQUE", "retained question ID is duplicated"
            )
        retained_question_ids.add(question_id)
        if (
            question_id != recommendation_id
            or row.get("source_question_sha256")
            != canonical_record_sha256(source_question)
            or type(row.get("questions_sent")) is not int
            or row.get("questions_sent") != 0
            or source_question.get("artifact_role")
            != "INTERNAL_FUTURE_OWNER_QUESTION_RECOMMENDATION_NOT_SENT"
            or type(source_question.get("questions_sent")) is not int
            or source_question.get("questions_sent") != 0
            or type(source_question.get("supplier_contacts")) is not int
            or source_question.get("supplier_contacts") != 0
            or type(source_question.get("root_mutations")) is not int
            or source_question.get("root_mutations") != 0
            or source_question.get("mapping_or_price_approval") is not False
            or source_question.get("root_mapping_or_price_approval") is not False
            or source_question.get("root_review_disposition")
            != "ACCEPTED_FUTURE_QUESTION_NOT_SENT"
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "retained future-question provenance or review-only state differs",
            )
    if len(retained_question_ids) != 3:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "retained future-question total differs"
        )
    partitions = tables["approval_preview_partition"].rows
    display_rows = tables["approval_preview_offer_rows"].rows
    supplier_by_source_file, display_rows_by_variant, unfilled_supplier_label = (
        _derive_display_supplier_contract(partitions, display_rows, projection)
    )
    partition_by_preview: dict[str, Mapping[str, Any]] = {}
    partition_variants: set[str] = set()
    card_paths: set[str] = set()
    active_dependency_ids: set[str] = set()
    approval_only = 0
    identity_preview_values = {
        "REVIEWABLE_SUPPORTED_RELATIONSHIPS_UNAPPROVED",
        "BLOCKED_OR_NO_SUPPORTED_IDENTITY",
        "SUPPORTED_EVIDENCE_WITH_ITEM_IDENTITY_OR_UNIT_QUESTION",
    }
    for row in partitions:
        preview = _text(row.get("preview_id"), "preview ID")
        variant = _text(row.get("variant_id"), "preview Variant ID")
        catalog = catalog_by_variant.get(variant)
        family = family_by_variant.get(variant)
        variant_display_rows = display_rows_by_variant.get(variant, [])
        proposed_display = next(
            (
                display
                for display in variant_display_rows
                if display.get("disposition") == "PROPOSED_REVIEW_CANDIDATE"
            ),
            None,
        )
        expected_supplier_batch = (
            proposed_display.get("supplier")
            if proposed_display is not None
            else (
                variant_display_rows[0].get("supplier")
                if variant_display_rows
                else unfilled_supplier_label
            )
        )
        card = _validated_member_name(_text(row.get("card_path"), "card path"), ReviewLimits())
        if preview in partition_by_preview or variant in partition_variants or card in card_paths:
            raise ReviewPackageError("TABLE_KEY_NOT_UNIQUE", "preview/card identity is duplicated")
        restored_card = f"review_batches/{card}"
        if (
            catalog is None
            or family is None
            or row.get("product_id") != catalog.get("product_id")
            or row.get("family_id") != family.get("family_id")
            or row.get("captured_product_title")
            != catalog.get("current_product_title")
            or row.get("captured_variant_options")
            != catalog.get("current_variant_title")
            or row.get("display_status") != catalog.get("v5_display_status")
            or row.get("frozen_source_status") != catalog.get("status")
            or row.get("supplier_batch") != expected_supplier_batch
            or row.get("original_cohort_ordinal") != catalog_ordinals[variant]
            or row.get("all_prices_visually_checked") is not False
            or row.get("ordering_eligibility")
            != "NOT_AUTHORIZED; FRESH_APPROVED_WORKFLOW_REQUIRED"
            or row.get("pricing_eligibility")
            != "UNAPPROVED_AND_APPLICABILITY_NOT_ASSERTED"
            or row.get("identity_mapping_preview") not in identity_preview_values
            or restored_card not in embedded_by_restored
            or embedded_by_restored[restored_card].get("role") != "HUMAN_REVIEW_PREVIEW"
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH",
                "preview catalog, authority, or embedded-card projection differs",
            )
        dependencies = row.get("active_dependency_ids")
        pending = row.get("pending_approval_only_requirement_ids")
        if not isinstance(dependencies, list) or not isinstance(pending, list):
            raise ReviewPackageError("A1_SCHEMA_MISMATCH", "preview requirement IDs must be arrays")
        active_dependency_ids.update(str(item) for item in dependencies)
        approval_only += len(pending)
        partition_by_preview[preview] = row
        partition_variants.add(variant)
        card_paths.add(card)
    if partition_variants != catalog_ids or len(partitions) != 2_000 or approval_only != 65:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "preview catalog controls differ")
    relationship_rows = tables["variant_offer_relationships_v5"].rows
    _validate_relationship_source_provenance(relationship_rows, projection)
    relationship_by_id = {str(row["relationship_id"]): row for row in relationship_rows}
    relationship_pairs = {(str(row["variant_id"]), str(row["source_offer_id"])) for row in relationship_rows}
    sibling_rows_for_display = tables["sibling_offer_reviews"].rows
    sibling_by_pair_for_display = {
        (str(row.get("variant_id")), str(row.get("source_offer_id"))): row
        for row in sibling_rows_for_display
    }
    diagnostic_by_pair_for_display = {
        (str(row.get("variant_id")), str(row.get("source_offer_id"))): row
        for row in tables["same_code_sibling_display_context"].rows
    }
    display_pairs: set[tuple[str, str]] = set()
    nonrelationship_pairs: set[tuple[str, str]] = set()
    addendum_ids_seen: set[str] = set()
    displayed_by_variant: Counter[str] = Counter()
    supported_by_variant: Counter[str] = Counter()
    display_dispositions: Counter[str] = Counter()
    display_projection_kinds: Counter[str] = Counter()
    for row in display_rows:
        preview = partition_by_preview.get(str(row.get("preview_id")))
        pair = (str(row.get("variant_id")), str(row.get("source_offer_id")))
        if preview is None or preview.get("variant_id") != row.get("variant_id") or pair in display_pairs:
            raise ReviewPackageError("JOIN_MISMATCH", "display offer preview join differs")
        display_pairs.add(pair)
        relationship = relationship_by_id.get(str(row.get("existing_relationship_id")))
        sibling = sibling_by_pair_for_display.get(pair)
        diagnostic = diagnostic_by_pair_for_display.get(pair)
        display_source = _source_offer_contract(
            projection,
            _text(row.get("source_offer_id"), "display source offer ID"),
            context="display supplier",
        )
        expected_supplier = supplier_by_source_file.get(
            _text(display_source.get("source_file"), "display source file")
        )
        if expected_supplier is None:
            raise ReviewPackageError(
                "JOIN_MISMATCH", "display source file lacks its supplier batch"
            )
        display_projection_kinds[
            _validate_display_offer_projection(
                row,
                relationship=relationship,
                sibling=sibling,
                diagnostic=diagnostic,
                projection=projection,
                expected_supplier=expected_supplier,
            )
        ] += 1
        disposition = _text(row.get("disposition"), "display disposition")
        display_dispositions[disposition] += 1
        displayed_by_variant[pair[0]] += 1
        if disposition == "PROPOSED_REVIEW_CANDIDATE":
            supported_by_variant[pair[0]] += 1
        relationship_id = row.get("existing_relationship_id")
        if relationship_id is None:
            nonrelationship_pairs.add(pair)
        else:
            if relationship is None or pair != (
                str(relationship.get("variant_id")),
                str(relationship.get("source_offer_id")),
            ):
                raise ReviewPackageError("JOIN_MISMATCH", "display offer relationship differs")
        addendum_id = row.get("addendum_review_id")
        if addendum_id is not None:
            sibling = sibling_by_pair_for_display.get(pair)
            if (
                not isinstance(addendum_id, str)
                or not addendum_id
                or addendum_id in addendum_ids_seen
                or sibling is None
                or sibling.get("recommendation_id") != addendum_id
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "display addendum review join differs"
                )
            addendum_ids_seen.add(addendum_id)
    diagnostic_pairs = {
        (str(row.get("variant_id")), str(row.get("source_offer_id")))
        for row in tables["same_code_sibling_display_context"].rows
    }
    sibling_pairs = {
        (str(row.get("variant_id")), str(row.get("source_offer_id")))
        for row in tables["sibling_offer_reviews"].rows
    }
    if (
        len(display_rows) != 14_823
        or len(display_pairs) != 14_823
        or display_pairs & relationship_pairs != relationship_pairs
        or not (diagnostic_pairs | sibling_pairs).issubset(display_pairs)
        or nonrelationship_pairs != (diagnostic_pairs | sibling_pairs) - relationship_pairs
        or len(diagnostic_pairs) != 6
        or len(sibling_pairs) != 7
        or len(diagnostic_pairs | sibling_pairs) != 13
        or len(nonrelationship_pairs) != 11
        or addendum_ids_seen
        != {str(row.get("recommendation_id")) for row in sibling_rows_for_display}
        or display_dispositions
        != Counter(
            {
                "PROPOSED_REVIEW_CANDIDATE": 1_609,
                "REJECTED_ATTRIBUTE_CONFLICT": 7_140,
                "REJECTED_IDENTITY_CONFLICT": 2,
                "SAME_CODE_DIAGNOSTIC_NOT_REVIEWED_AS_IDENTITY": 6,
                "SEARCH_LEAD_ONLY": 6_066,
            }
        )
        or display_projection_kinds
        != Counter({"relationship": 14_810, "sibling": 7, "diagnostic": 6})
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "displayed occurrence controls differ")
    for partition in partitions:
        variant = str(partition.get("variant_id"))
        if (
            partition.get("all_displayed_source_occurrences")
            != displayed_by_variant[variant]
            or partition.get("supported_review_occurrences")
            != supported_by_variant[variant]
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "preview occurrence totals differ"
            )
    dependencies = tables["dependency_group_members_v5"].rows
    member_by_dependency = {
        str(row.get("dependency_id")): row for row in dependencies
    }
    dependency_by_id = {
        str(row.get("dependency_id")): row
        for row in tables["unresolved_dependencies_v5"].rows
    }
    active = {
        str(row["dependency_id"])
        for row in dependencies
        if row.get("resolved") is False and row.get("historical_scope_only") is False
    }
    routes = tables["requests__active_dependency_request_routes"].rows
    route_ids = {str(row.get("dependency_id")) for row in routes}
    route_variants = {str(row.get("variant_id")) for row in routes}
    route_by_dependency = {
        str(row.get("dependency_id")): row for row in routes
    }
    request_route_ids = {
        str(row.get("request_route_id")) for row in routes
    }
    if (
        active != route_ids
        or active != active_dependency_ids
        or len(routes) != len(route_by_dependency)
        or len(routes) != len(request_route_ids)
        or len(active) != 714
        or len(route_variants) != 475
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "active requirement route controls differ")
    routes_by_variant: defaultdict[str, set[str]] = defaultdict(set)
    for dependency_id, route in route_by_dependency.items():
        dependency = dependency_by_id.get(dependency_id)
        member = member_by_dependency.get(dependency_id)
        variant = str(route.get("variant_id"))
        _validate_dependency_route_provenance(route, member, dependency)
        routes_by_variant[variant].add(dependency_id)

    approval_rows = tables[
        "requests__owner_approval_requirement_interpretations"
    ].rows
    approval_by_dependency = {
        str(row.get("dependency_id")): row for row in approval_rows
    }
    if (
        len(approval_rows) != 65
        or len(approval_by_dependency) != 65
        or any(
            dependency_id not in route_by_dependency
            or canonical_record_sha256(row)
            != canonical_record_sha256(route_by_dependency[dependency_id])
            for dependency_id, row in approval_by_dependency.items()
        )
    ):
        raise ReviewPackageError(
            "JOIN_MISMATCH", "owner approval-only requirement routes differ"
        )
    approval_by_variant: defaultdict[str, set[str]] = defaultdict(set)
    for dependency_id, row in approval_by_dependency.items():
        approval_by_variant[str(row.get("variant_id"))].add(dependency_id)
    for partition in partitions:
        variant = str(partition.get("variant_id"))
        active_ids = partition.get("active_dependency_ids")
        material_ids = partition.get("active_material_request_ids")
        pending_ids = partition.get("pending_approval_only_requirement_ids")
        if (
            not isinstance(active_ids, list)
            or not isinstance(material_ids, list)
            or not isinstance(pending_ids, list)
            or len(active_ids) != len(set(active_ids))
            or len(material_ids) != len(set(material_ids))
            or len(pending_ids) != len(set(pending_ids))
            or set(active_ids) != routes_by_variant[variant]
            or set(pending_ids) != approval_by_variant[variant]
            or set(material_ids)
            != routes_by_variant[variant] - approval_by_variant[variant]
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "preview dependency/request membership differs"
            )

    ranked_rows = tables["requests__ranked_evidence_request_groups"].rows
    ranked_ids: set[str] = set()
    ranked_dependencies: set[str] = set()
    for row in ranked_rows:
        identifier = _text(row.get("request_group_id"), "ranked request group ID")
        dependency_ids = row.get("dependency_ids")
        variant_ids = row.get("variant_ids")
        if not isinstance(dependency_ids, list) or not isinstance(variant_ids, list):
            raise ReviewPackageError(
                "A1_SCHEMA_MISMATCH", "ranked request membership must be arrays"
            )
        dependencies_in_group = set(dependency_ids)
        expected_variants = {
            str(route_by_dependency[dependency_id].get("variant_id"))
            for dependency_id in dependencies_in_group
            if dependency_id in route_by_dependency
        }
        if (
            identifier in ranked_ids
            or ranked_dependencies & dependencies_in_group
            or len(dependency_ids) != len(dependencies_in_group)
            or not dependencies_in_group.issubset(active)
            or row.get("dependency_count") != len(dependencies_in_group)
            or set(variant_ids) != expected_variants
            or len(variant_ids) != len(expected_variants)
            or row.get("unique_affected_variants") != len(expected_variants)
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "ranked request-group coverage differs"
            )
        ranked_ids.add(identifier)
        ranked_dependencies.update(dependencies_in_group)
    if len(ranked_rows) != 120 or ranked_dependencies != active:
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "ranked request groups do not cover active dependencies"
        )
    overlays = tables["source_review_overlays"].rows
    siblings = tables["sibling_offer_reviews"].rows
    if len(overlays) != 25 or len(siblings) != 7:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "daytime overlay/sibling controls differ")
    overlay_ids: set[str] = set()
    candidate_counts_by_offer = Counter(
        offer_id for _variant_id, offer_id in projection.candidate_pairs
    )
    description_proposed = frozenset(
        {
            "frozen_supplier_description_is_extraction_error",
            "reviewed_supplier_description",
        }
    )
    conversion_before = frozenset(
        {
            "containers_per_pack_raw",
            "extraction_flags",
            "package_type",
            "physical_units_per_case",
            "qualifying_units_per_case",
            "retail_packs_per_case",
            "shopify_units_per_case",
            "supplier_pack_count",
        }
    )
    overlay_before_by_proposed = {
        description_proposed: frozenset({"source_evidence", "supplier_description"}),
        frozenset(
            {
                "retail_pack_conflict",
                "reviewed_containers_per_retail_pack",
                "reviewed_qualifying_units_per_case",
                "reviewed_retail_packs_per_case",
                "reviewed_shopify_units_per_case",
            }
        ): conversion_before,
        frozenset(
            {"reviewed_qualifying_units_per_case", "reviewed_relationship"}
        ): conversion_before,
        frozenset(
            {
                "reviewed_qualifying_units_per_case",
                "reviewed_relationship",
                "reviewed_shopify_units_per_case",
                "reviewed_total_physical_containers_per_case",
            }
        ): conversion_before,
    }
    overlay_kind_by_proposed = {
        description_proposed: "ADDITIVE_SOURCE_DESCRIPTION_CORRECTION",
        frozenset(
            {
                "retail_pack_conflict",
                "reviewed_containers_per_retail_pack",
                "reviewed_qualifying_units_per_case",
                "reviewed_retail_packs_per_case",
                "reviewed_shopify_units_per_case",
            }
        ): "PUBLISHED_RETAIL_PACK_CONTRADICTION",
        frozenset(
            {"reviewed_qualifying_units_per_case", "reviewed_relationship"}
        ): "MIXED_ASSORTMENT_SOURCE_CLASSIFICATION",
        frozenset(
            {
                "reviewed_qualifying_units_per_case",
                "reviewed_relationship",
                "reviewed_shopify_units_per_case",
                "reviewed_total_physical_containers_per_case",
            }
        ): "MIXED_ALCOHOL_GIFT_PHYSICAL_COUNT_INCOMPLETE",
    }
    overlay_shape_counts: Counter[frozenset[str]] = Counter()
    for row in overlays:
        identifier = _text(row.get("recommendation_id"), "source overlay ID")
        offer = _text(row.get("source_offer_id"), "source overlay offer")
        source = _mapping(row.get("source"), "source overlay evidence")
        details = projection.source_offer_details.get(offer)
        contract = _source_offer_contract(
            projection, offer, context="source overlay"
        )
        tiers = row.get("source_tier_ids")
        tier_hashes = row.get("ladder_record_sha256")
        expected_tiers = [
            str(item.get("source_tier_id"))
            for item in projection.tiers_by_offer.get(offer, ())
        ]
        before = _mapping(row.get("before"), "source overlay before values")
        proposed = _mapping(
            row.get("proposed_review_overlay"), "source overlay proposed values"
        )
        changes = row.get("overlay_field_changes")
        proposed_shape = frozenset(proposed)
        expected_before = overlay_before_by_proposed.get(proposed_shape)
        if (
            identifier in overlay_ids
            or details is None
            or row.get("source_record_sha256")
            != projection.source_offer_record_hashes.get(offer)
            or not isinstance(tiers, list)
            or not isinstance(tier_hashes, list)
            or tiers != expected_tiers
            or tier_hashes
            != [projection.tier_record_hashes.get(str(tier)) for tier in tiers]
            or row.get("existing_candidate_pair_count")
            != candidate_counts_by_offer[offer]
            or source.get("source_offer_id") != offer
            or source.get("supplier_sku") != details.get("supplier_code")
            or source.get("source_file") != details.get("source_file")
            or source.get("source_page") != details.get("source_page")
            or source.get("source_sha256") != details.get("source_sha256")
            or source.get("source_evidence") != details.get("source_evidence")
            or row.get("raw_record_unchanged") is not True
            or row.get("does_not_replace_frozen_source_row") is not True
            or row.get("explicit_reviewed_null_precedence") is not True
            or expected_before is None
            or row.get("kind") != overlay_kind_by_proposed.get(proposed_shape)
            or set(before) != set(expected_before)
            or any(
                field not in contract or value != contract[field]
                for field, value in before.items()
            )
            or not isinstance(changes, list)
            or not changes
        ):
            raise ReviewPackageError("JOIN_MISMATCH", "source overlay join differs")
        changed_fields: list[str] = []
        for change in changes:
            change = _mapping(change, "source overlay field change")
            field = _text(change.get("field"), "source overlay changed field")
            changed_fields.append(field)
            before_present = change.get("before_present")
            after_present = change.get("after_present")
            if (
                type(before_present) is not bool
                or type(after_present) is not bool
                or before_present != (field in before)
                or after_present != (field in proposed)
                or (before_present and change.get("before") != before[field])
                or (not before_present and change.get("before") is not None)
                or (after_present and change.get("after") != proposed[field])
                or (not after_present and change.get("after") is not None)
            ):
                raise ReviewPackageError(
                    "JOIN_MISMATCH", "source overlay before/after facts differ"
                )
        if (
            len(changed_fields) != len(set(changed_fields))
            or set(changed_fields) != set(proposed)
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "source overlay change-field coverage differs"
            )
        overlay_shape_counts[proposed_shape] += 1
        overlay_ids.add(identifier)
    if overlay_shape_counts != Counter(
        {
            description_proposed: 21,
            frozenset(
                {
                    "retail_pack_conflict",
                    "reviewed_containers_per_retail_pack",
                    "reviewed_qualifying_units_per_case",
                    "reviewed_retail_packs_per_case",
                    "reviewed_shopify_units_per_case",
                }
            ): 1,
            frozenset(
                {"reviewed_qualifying_units_per_case", "reviewed_relationship"}
            ): 1,
            frozenset(
                {
                    "reviewed_qualifying_units_per_case",
                    "reviewed_relationship",
                    "reviewed_shopify_units_per_case",
                    "reviewed_total_physical_containers_per_case",
                }
            ): 2,
        }
    ):
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "source overlay projection shapes differ"
        )

    sibling_ids: set[str] = set()
    sibling_pairs_checked: set[tuple[str, str]] = set()
    for row in siblings:
        identifier = _text(row.get("recommendation_id"), "sibling review ID")
        variant = _text(row.get("variant_id"), "sibling Variant ID")
        offer = _text(row.get("source_offer_id"), "sibling source offer")
        pair = (variant, offer)
        source = _mapping(row.get("source_evidence"), "sibling source evidence")
        details = projection.source_offer_details.get(offer)
        anchor_pair = (variant, _text(row.get("anchor_source_offer_id"), "sibling anchor offer"))
        if (
            identifier in sibling_ids
            or pair in sibling_pairs_checked
            or details is None
            or row.get("source_record_sha256")
            != projection.source_offer_record_hashes.get(offer)
            or row.get("anchor_candidate_record_sha256")
            != projection.candidate_record_hashes.get(anchor_pair)
            or source.get("source_offer_id") != offer
            or source.get("supplier_sku") != details.get("supplier_code")
            or source.get("source_file") != details.get("source_file")
            or source.get("source_page") != details.get("source_page")
            or source.get("source_sha256") != details.get("source_sha256")
            or source.get("source_evidence") != details.get("source_evidence")
            or row.get("frozen_candidate_row_changed") is not False
            or row.get("price_ladder_selected") is not False
        ):
            raise ReviewPackageError("JOIN_MISMATCH", "sibling-offer review join differs")
        current_hash = projection.candidate_record_hashes.get(pair)
        if row.get("current_pair_present") is True:
            if row.get("current_pair_record_sha256") != current_hash:
                raise ReviewPackageError("JOIN_MISMATCH", "sibling current-pair hash differs")
        elif row.get("current_pair_present") is False:
            if current_hash is not None or row.get("current_pair_record_sha256") is not None:
                raise ReviewPackageError("JOIN_MISMATCH", "sibling absent-pair state differs")
        else:
            raise ReviewPackageError("A1_SCHEMA_MISMATCH", "sibling pair presence differs")
        sibling_ids.add(identifier)
        sibling_pairs_checked.add(pair)

    diagnostic_rows = tables["same_code_sibling_display_context"].rows
    for row in diagnostic_rows:
        pair = (str(row.get("variant_id")), str(row.get("source_offer_id")))
        anchor = str(row.get("anchor_offer_id"))
        source_identity = projection.source_offers.get(pair[1])
        anchor_identity = projection.source_offers.get(anchor)
        if (
            pair[0] not in catalog_ids
            or source_identity is None
            or anchor_identity is None
            or source_identity[:2] != anchor_identity[:2]
            or (pair[0], anchor) not in relationship_pairs
            or (pair[0], anchor) not in projection.candidate_pairs
            or row.get("relationship")
            != "SAME_CODE_OCCURRENCE_DIAGNOSTIC_ONLY_NOT_IDENTITY_SUPPORT"
            or row.get("reviewed_shopify_units_per_case") is not None
            or row.get("reviewed_qualifying_units_per_case") is not None
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "same-code sibling diagnostic differs"
            )
    combos = tables["complete_combos"].rows
    combo_ids = {str(row.get("source_offer_id")) for row in combos}
    components = tables["combo_components"].rows
    linked_components = [row for row in components if row.get("source_component_id") is not None]
    unlinked_components = [row for row in components if row.get("source_component_id") is None]
    component_by_id = {
        str(row.get("source_component_id")): row for row in linked_components
    }
    components_by_combo: Counter[str] = Counter(
        str(row.get("source_offer_id")) for row in linked_components
    )
    if (
        len(combos) != 460
        or len(combo_ids) != 460
        or len(linked_components) != 1_193
        or len(component_by_id) != 1_193
        or len(unlinked_components) != 629
        or set(components_by_combo) != combo_ids
    ):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "complete combo controls differ")
    for combo in combos:
        if components_by_combo[str(combo.get("source_offer_id"))] != combo.get("component_count"):
            raise ReviewPackageError("JOIN_MISMATCH", "combo component count differs")
    combo_by_id = {str(row.get("source_offer_id")): row for row in combos}
    validation_rows = tables["combo_validation"].rows
    validation_by_id = {
        str(row.get("source_offer_id")): row for row in validation_rows
    }
    if len(validation_rows) != 460 or set(validation_by_id) != combo_ids:
        raise ReviewPackageError("JOIN_MISMATCH", "combo validation coverage differs")
    for combo_id, validation in validation_by_id.items():
        combo = combo_by_id[combo_id]
        expected_components = {
            component_id
            for component_id, component in component_by_id.items()
            if str(component.get("source_offer_id")) == combo_id
        }
        declared_components = validation.get("source_component_ids")
        if (
            not isinstance(declared_components, list)
            or set(declared_components) != expected_components
            or len(declared_components) != len(expected_components)
            or validation.get("component_count") != combo.get("component_count")
            or validation.get("complete_combo_total_as_printed") != combo.get("total_cost")
            or validation.get("review_status") != "UNAPPROVED_FIXED_COMBO"
        ):
            raise ReviewPackageError("JOIN_MISMATCH", "combo validation facts differ")
    _validate_complete_combo_provenance(
        combos,
        linked_components,
        validation_rows,
        projection,
        limits=limits,
    )

    fixed_rows = tables["fixed_combo_component_relationships_v5"].rows
    fixed_counts: Counter[str] = Counter()
    for row in fixed_rows:
        combo_id = str(row.get("source_combo_offer_id"))
        component_id = str(row.get("source_component_id"))
        variant = str(row.get("variant_id"))
        combo = combo_by_id.get(combo_id)
        component = component_by_id.get(component_id)
        if (
            combo is None
            or component is None
            or variant not in catalog_ids
            or component.get("source_offer_id") != combo_id
            or component.get("source_file") != row.get("source_file")
            or component.get("source_page") != row.get("source_page")
            or component.get("source_sha256") != row.get("source_sha256")
            or component.get("component_supplier_sku") != row.get("component_supplier_sku")
            or component.get("component_description") != row.get("component_description_raw")
            or component.get("quantity_raw") != row.get("component_quantity_raw")
            or component.get("size_text") != row.get("component_size_raw")
            or combo.get("total_cost") != row.get("whole_combo_total_cost")
            or row.get("whole_combo_to_variant_mapping_allowed") is not False
            or row.get("combo_auto_add") is not False
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "fixed combo relationship facts differ"
            )
        fixed_counts[variant] += 1
    remaining_rows = tables["remaining_combo_component_relationships_v5"].rows
    remaining_counts: Counter[str] = Counter()
    for row in remaining_rows:
        variant = row.get("catalog_variant_id")
        if (
            row.get("source_component_id") is not None
            or row.get("complete_combo_total_record_id") is not None
            or row.get("whole_combo_maps_to_single_variant") is not False
            or row.get("mapping_approved") is not False
            or row.get("price_approved") is not False
            or row.get("import_ready") is not False
            or (variant is not None and variant not in catalog_ids)
        ):
            raise ReviewPackageError(
                "UNAUTHORIZED_APPROVAL_CLAIM",
                "remaining combo diagnostic gained unsupported authority",
            )
        if isinstance(variant, str):
            remaining_counts[variant] += 1
    if (
        len(fixed_rows) != 249
        or len(remaining_rows) != 19
        or sum(remaining_counts.values()) != 8
        or len(remaining_counts) != 7
    ):
        raise ReviewPackageError(
            "CONTROL_TOTAL_MISMATCH", "fixed/remaining combo controls differ"
        )
    _validate_remaining_combo_provenance(
        remaining_rows,
        tables["remaining_combo_total_reviews_v5"].rows,
        tables["remaining_supplier_family_reviews_v5"].rows,
        components,
        combo_ids,
        projection,
        catalog_ids,
        census_ids,
    )
    for partition in partitions:
        variant = str(partition.get("variant_id"))
        if partition.get("fixed_combo_component_relationships") != (
            fixed_counts[variant] + remaining_counts[variant]
        ):
            raise ReviewPackageError(
                "JOIN_MISMATCH", "preview combo relationship count differs"
            )
    partition_counts = _validate_partition_derivations(
        partitions,
        catalog_by_variant,
        display_rows,
        routes,
        tables["conditional_gift_relationships_v5"].rows,
        tables["alcohol_gift_components_v5"].rows,
        fixed_rows,
        remaining_rows,
        projection,
    )
    controls = _mapping(root.get("controls"), "controls")
    expected_controls = {
        "original_cohort_rows": 2_000,
        "unique_original_variants": 2_000,
        "displayed_occurrences": 14_823,
        "active_requirements": 714,
        "active_requirement_variants": 475,
        "approval_only_requirements": 65,
        "historical_census_unique_variants": 2_003,
        "normalized_price_rows": 80_661,
        "tier_id_repairs": 4_171,
        "southern_complete_combo_totals": 460,
        "mapping_approved_count": 0,
        "price_approved_count": 0,
        "import_ready_count": 0,
        "current_price_eligible_count": 0,
    }
    if any(controls.get(key) != value for key, value in expected_controls.items()):
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "A1 root controls differ")
    return {
        "original_catalog_variants": len(catalog_ids),
        "historical_census_variants": len(census_ids),
        "effective_identity_surfaces": identity_surfaces,
        "current_addition_capture_references": addition_capture_references,
        "sealed_specialist_evidence": specialist_controls,
        "alcohol_gift_provenance": alcohol_gift_controls,
        "review_partition_counts": {
            str(partition): count
            for partition, count in sorted(partition_counts.items())
        },
        "review_cards": len(partitions),
        "displayed_occurrences": len(display_rows),
        "active_requirements": len(active),
        "active_requirement_variants": len(route_variants),
        "owner_decisions": len(owner_rows),
        "owner_preference_scopes": owner_preference_scopes,
        "complete_combos": len(combos),
        "source_review_overlays": len(overlays),
        "sibling_offer_reviews": len(siblings),
    }


def read_real_v5_a1_package(
    source: _PackageSource,
    *,
    root_bytes: bytes,
    external_evidence_root: str | Path | None,
) -> ReviewPackage:
    """Validate and expose the one sealed genuine A1 snapshot."""

    if not isinstance(source, _DirectorySource):
        raise ReviewPackageError("PORTABLE_ROOT_MUST_BE_DIRECTORY", "A1 root must be a directory")
    _validate_top_level(source)
    root = _validate_root(root_bytes, source.limits)
    _validate_seal(source, root_bytes)
    table_specs, members_by_archive, _ = _validate_table_specs(root, source.limits)
    restored_paths = {str(table["restored_path"]).casefold() for table in table_specs}
    embedded_specs, embedded_by_restored = _validate_embedded_specs(
        root,
        source.limits,
        members_by_archive,
        restored_paths,
    )
    external_contracts = _external_original_contracts(root, source.limits)
    source_controls = _validate_source_evidence(
        root,
        source.limits,
        embedded_by_restored,
        external_contracts,
    )
    pdf_status, unavailable = _verify_external_pdfs(
        external_evidence_root,
        external_contracts,
        source.limits,
    )
    projection = _ProjectionBuilder(
        {name: row for name, row in external_contracts.items() if "physical_pages" in row}
    )
    (
        tables,
        loaders,
        specialist_evidence,
        verified_embedded,
        total_entries,
    ) = _read_tables_and_embedded(
        source,
        table_specs,
        embedded_specs,
        members_by_archive,
        projection,
    )
    if verified_embedded != _EXPECTED_EMBEDDED:
        raise ReviewPackageError("CONTROL_TOTAL_MISMATCH", "verified embedded-file count differs")
    schema_controls = _validate_daytime_dictionary_contract(
        _read_daytime_dictionary(source, embedded_by_restored),
        tables,
    )
    capture_documents = _read_addition_capture_documents(
        source,
        tables["current_additions_review"].rows,
        embedded_by_restored,
        source.limits,
    )
    projection_controls = projection.finalize(tables)
    sidecar_controls = _validate_v5_sidecars(tables)
    relationship_controls = _validate_snapshot_relationships(
        root,
        tables,
        embedded_by_restored,
        projection,
        capture_documents,
        specialist_evidence,
        source.limits,
    )
    tables_by_namespace = {
        namespace: {
            "tables": expected[0],
            "rows": expected[1],
            "parts": expected[2],
        }
        for namespace, expected in _EXPECTED_NAMESPACE_TOTALS.items()
    }
    readiness = {
        "replacement_transport": "NOT_EVALUATED_BY_PORTABLE_READER",
        "raw_file_integrity": "PASS",
        "effective_snapshot_restoration": "PASS",
        "structural_replay": "PASS",
        "relationship_validation": "PASS",
        "original_pdf_bytes": "PASS" if external_evidence_root is not None else "UNAVAILABLE",
        "source_page_bundle_bytes": "UNAVAILABLE_3_DECLARED_BUNDLES",
        "historical_patch_replay": "UNAVAILABLE_EXACT_V4_BASELINE_REQUIRED",
        "semantic_review": "TARGETED_UNAPPROVED_REVIEW",
        "mapping_approval": "NOT_APPROVED",
        "price_approval": "NOT_APPROVED",
        "import_readiness": "NOT_IMPORT_READY",
    }
    issues = (
        ValidationIssue(
            "HISTORICAL_PATCH_REPLAY_UNAVAILABLE",
            "Exact original V4 baseline was not part of the complete effective snapshot transfer.",
        ),
        ValidationIssue(
            "SOURCE_PAGE_BUNDLES_UNAVAILABLE",
            "Three declared original page-image bundles were not transferred.",
        ),
    )
    metadata = {
        "adapter": A1_ADAPTER,
        "replacement_transport": {
            "file": "Buffalo_Codex_Real_Package_Replacement_R1.zip",
            "bytes": 386_533_606,
            "sha256": "5f8d07805f9cd83bf4421f6b355062188b04550d18adb207f60672b7b6604bce",
            "scope": "EXPECTED_EXTERNAL_RECEIVING_IDENTITY_NOT_REVERIFIED_BY_PORTABLE_READER",
            "status": "NOT_EVALUATED_BY_PORTABLE_READER",
        },
        "raw_hash_basis": RAW_HASH,
        "canonical_hash_basis": CANONICAL_JSONL_HASH,
        "readiness": readiness,
        "integrity_checks": {
            "root_raw_sha256": A1_ROOT_SHA256,
            "seal_raw_sha256": A1_SEAL_SHA256,
            "sealed_archives": len(_ARCHIVES),
            "archive_members": total_entries - len(source.names),
            "logical_tables": len(tables),
            "logical_rows": _EXPECTED_ROWS,
            "embedded_files": verified_embedded,
        },
        "snapshot_scope": {
            "comparison_contract": "V5_DAYTIME_A1_COMPLETE_EFFECTIVE_SNAPSHOT",
            "identity_contract": "SHOPIFY_VARIANT_VENDOR_SOURCE_OCCURRENCE_V1",
            "lineage_family_sha256": A1_ROOT_SHA256,
            "supplier_scope_kind": "FROZEN_MULTI_SUPPLIER_REVIEW",
            "supplier_scope": sorted(root["controls"]["supplier_batch_counts"]),
            "cohort_scope": "ORIGINAL_2000_PLUS_SEPARATE_HISTORICAL_CENSUS",
            "period_semantics": "PRINTED_SOURCE_PERIOD_NOT_CURRENT_PRICE_AUTHORITY",
            "period_id": "V5-DAYTIME-A1",
            "supplier_period_coverage_complete": False,
            "missing_supplier_periods": ["NAMED_ABSENT_SUPPLIER_BOOKS"],
            "channels": ["OFFLINE_REVIEW"],
            "territories": ["AS_CAPTURED_UNAPPROVED"],
            "simulated": False,
        },
        "tables_by_namespace": tables_by_namespace,
        "source_evidence": source_controls,
        "schema_controls": schema_controls,
        "external_pdf_status": pdf_status,
        "page_bundle_status": {
            name: "UNAVAILABLE_ORIGINAL_BYTES"
            for name, row in external_contracts.items()
            if "physical_pages" not in row
        },
        "projection_controls": {
            key: value
            for key, value in projection_controls.items()
            if key not in {
                "price_ladders_by_offer",
                "rejected_by_variant",
                "source_offer_details",
            }
        },
        "source_offer_details": projection_controls["source_offer_details"],
        "price_ladders_by_offer": projection_controls["price_ladders_by_offer"],
        "rejected_by_variant": projection_controls["rejected_by_variant"],
        "v5_sidecar_controls": sidecar_controls,
        "relationship_controls": relationship_controls,
        "authority": {
            "label": A1_REVIEW_LABEL,
            "mapping_approvals": 0,
            "price_approvals": 0,
            "import_ready_rows": 0,
            "operational_effects": 0,
        },
    }
    return ReviewPackage(
        source=str(source.path),
        package_kind=A1_PACKAGE_KIND,
        snapshot_id=A1_PACKAGE_ID,
        status="REVIEW_ONLY_VALIDATED",
        label=A1_REVIEW_LABEL,
        file_count=total_entries,
        verified_file_count=total_entries,
        manifest_sha256=A1_ROOT_SHA256,
        tables=tables,
        cohorts={
            "original_cohort_count": 2_000,
            "current_census_count": 2_003,
            "current_original_returned": 1_999,
            "current_additions": 4,
        },
        issues=issues,
        unavailable_evidence=unavailable,
        metadata=metadata,
        table_loaders=loaders,
    )
