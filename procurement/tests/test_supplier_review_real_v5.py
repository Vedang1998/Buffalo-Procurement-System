"""Fabricated, noncommercial tests for the exact V5-DAYTIME-A1 adapter."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from procurement_os.price_book import PRICE_BOOK_HEADERS
from procurement_os.supplier_mapping_review import (
    _html_embedded_json,
    _reviewable_sidecar_record,
    _v5_review_family_summary,
    report_document,
    render_review_html,
)
from procurement_os.supplier_review_package import (
    PackageTable,
    ReviewLimits,
    ReviewPackage,
    ReviewPackageError,
    _DirectorySource,
    _ZipSource,
    _canonical_json,
    read_review_package,
)
from procurement_os.supplier_review_v5 import (
    _validate_fixed_combo_provisional_units,
    _validate_v5_gift_chains,
    _validate_v5_normalized_contract_row,
    _validate_v5_owner_preference_contract,
    _validate_v5_row_authority,
)
import procurement_os.supplier_review_real_v5 as real
from procurement.tools.review_supplier_mapping_package import _local_pdf_href_prefix


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _table(
    name: str,
    rows: tuple[dict[str, object], ...],
    *,
    path: str | None = None,
    declared: int | None = None,
) -> PackageTable:
    payload = b"".join(_canonical_json(row) + b"\n" for row in rows)
    return PackageTable(
        name=name,
        path=path or f"daytime_addendum/{name}.jsonl",
        format="jsonl",
        rows=rows,
        raw_sha256=_sha(payload),
        canonical_jsonl_sha256=_sha(payload),
        declared_row_count=declared,
    )


def _root_document() -> dict[str, object]:
    return {
        "format": "BUFFALO_PRIVATE_REVIEW_SNAPSHOT_V1",
        "source_revision": "V5",
        "semantic_addendum_revision": real.A1_ADAPTER,
        "package_id": real.A1_PACKAGE_ID,
        "generated_at_utc": "2026-09-09T00:00:00Z",
        "authority": "UNAPPROVED_REVIEW",
        "controls": {},
        "lineage": {},
        "tables": [],
        "embedded_files": [],
        "source_evidence": [],
        "external_original_files": [],
        "missing_prerequisites": {},
        "state_separation": {},
        "review_only_flags": {
            "mapping_approval": False,
            "price_approval": False,
            "import_ready": False,
            "operational_effects": 0,
            "application_changes": 0,
            "shopify_writes": 0,
            "sales_changes": 0,
            "orders": 0,
            "supplier_messages": 0,
        },
        "consumer_rule": "fixture",
        "final_validation": {},
    }


def _normalized_row() -> dict[str, object]:
    row: dict[str, object] = {field: None for field in PRICE_BOOK_HEADERS}
    row.update(
        {
            "batch_ref": "FIXTURE",
            "vendor_name": "Fixture Supplier",
            "supplier_sku": None,
            "supplier_description": None,
            "source_file": "book.pdf",
            "source_evidence": "0012A 6 120.00",
            "source_page": 1,
            "extraction_confidence": "UNAPPROVED_CANDIDATE",
            "review_note": "Review only",
        }
    )
    return row


def _fixture_projection() -> tuple[real._ProjectionBuilder, dict[str, object]]:
    source: dict[str, object] = {
        field: None for field in real._SOURCE_OFFER_CONTRACT_FIELDS
    }
    source.update(
        {
            "source_offer_id": "O-1",
            "supplier_name_raw": "Fixture Supplier",
            "supplier_sku": "001",
            "supplier_description": "Fixture description",
            "source_file": "book.pdf",
            "source_page": 1,
            "printed_page": 1,
            "source_sha256": "a" * 64,
            "source_evidence": "001 fixture evidence",
            "source_approval_text": "UNAPPROVED_CANDIDATE",
            "source_period_status": "Fixture period",
            "package_type": "FIXED_COMBO",
            "size_text": "750ML",
            "split_availability": "NOT_INDICATED",
            "split_fee_basis_inclusion": "NOT_STATED",
            "artifact_role": "UNAPPROVED_REVIEW_EVIDENCE",
            "current_price_activated": False,
            "future_price_activated": False,
            "mapping_approved": False,
            "price_approved": False,
            "total_cost": 10,
            "total_cost_basis": "FIXTURE_TOTAL",
        }
    )
    projection = real._ProjectionBuilder(
        {"book.pdf": {"sha256": "a" * 64, "physical_pages": 2}}
    )
    projection.observe("effective_v5", "source_offers", source, 1)
    tier = {
        "source_tier_id": "T-1",
        "source_offer_id": "O-1",
        "supplier_name_raw": "Fixture Supplier",
        "supplier_sku": "001",
        "source_file": "book.pdf",
        "source_page": 1,
        "printed_page": 1,
        "source_sha256": "a" * 64,
        "source_evidence": "tier evidence",
        "source_approval_text": "UNAPPROVED_NO_TIER_SELECTED",
        "normalized_tier_type": "BASE",
        "break_quantity_raw": 1,
        "break_unit_raw": "CS",
        "case_price_printed": 10,
        "individual_price_printed": None,
        "retail_pack_price_printed": None,
        "unit_pack_bottle_price_printed": 1,
        "split_inclusive_price_printed": None,
        "total_cost": None,
        "total_cost_basis": None,
    }
    projection.observe("effective_v5", "price_ladders", tier, 1)
    return projection, source


class SupplierReviewRealV5Tests(unittest.TestCase):
    def test_dependency_routes_bind_every_reviewer_facing_source_field(self):
        dependency = {"variant_id": "V-1", "status": "UNRESOLVED"}
        member = {
            "variant_id": "V-1",
            "group_id": "G-1",
            "dependency_code_or_raw_text": "fixture dependency",
            "primary_required_evidence_class": "SUPPLIER_DOCUMENT",
            "source_status": "UNRESOLVED",
            "exact_evidence_needed": "Exact fixture evidence",
            "blocks_candidate_identity": True,
            "display_status": "ACTIVE_REVIEW_REQUIREMENT",
            "who_can_supply": "Fixture owner",
            "night2_attempt_evidence": "No prior result",
        }
        route = {
            "variant_id": "V-1",
            "source_group_id": "G-1",
            "source_dependency_sha256": _sha(_canonical_json(dependency)),
            "source_member_sha256": _sha(_canonical_json(member)),
            "raw_dependency_preserved": "fixture dependency",
            "source_provider_class_preserved": "SUPPLIER_DOCUMENT",
            "source_status": "UNRESOLVED",
            "source_status_or_resolution_changed": False,
            "exact_evidence_needed": "Exact fixture evidence",
            "blocks_candidate_identity_raw": True,
            "display_status": "ACTIVE_REVIEW_REQUIREMENT",
            "who_can_supply": "Fixture owner",
            "prior_attempt_evidence": "No prior result",
        }
        real._validate_dependency_route_provenance(route, member, dependency)
        for field, value in (
            ("exact_evidence_needed", "mutated evidence"),
            ("blocks_candidate_identity_raw", False),
            ("display_status", "MUTATED"),
            ("who_can_supply", "Another party"),
            ("prior_attempt_evidence", "Fabricated result"),
        ):
            changed = deepcopy(route)
            changed[field] = value
            with self.assertRaisesRegex(ReviewPackageError, "provenance differs"):
                real._validate_dependency_route_provenance(
                    changed, member, dependency
                )

    def test_owner_preferences_bind_family_occurrence_and_exact_owner_scope(self):
        family_rows = [
            {
                "family_id": "F-1",
                "variant_id": "V-1",
                "owner_preference_decision_ids": ["D-1"],
                "preference": (
                    "D-1_OWNER_REGULAR80_EXPRESSION_CODE-A; NOT_100PROOF_CODE-B; "
                    "NOT_MAPPING_APPROVAL"
                ),
            },
            {
                "family_id": "F-2",
                "variant_id": "V-2",
                "owner_preference_decision_ids": ["D-2"],
                "preference": (
                    "OWNER_PREFERS_CODE-C_24_INDIVIDUAL_187ML_BOTTLES; "
                    "NOT_APPROVAL_OR_PACK_BREAKING_PERMISSION"
                ),
            },
        ]
        relationship_rows: list[dict[str, object]] = []
        for index in range(17):
            family = family_rows[index % 2]
            relationship_rows.append(
                {
                    "family_id": family["family_id"],
                    "variant_id": family["variant_id"],
                    "owner_preference_decision_ids": list(
                        family["owner_preference_decision_ids"]
                    ),
                    "preference": (
                        family["preference"]
                        if index < 6
                        else "NOT_THE_OWNER_PREFERRED_24_INDIVIDUAL_CONFIGURATION; SEPARATE_REVIEW_REQUIRED"
                    ),
                    "conversion_authority": (
                        "V5_EXPLICIT_FIELD_IF_PRESENT_THEN_PRIOR_REVIEW; "
                        "NONPROPOSED_OR_CONDITIONAL_GIFT_ALWAYS_NULL"
                    ),
                    "no_code_replacement_inference": True,
                    "reason_precedence": (
                        "V5 > V4.1 > V4 > V2; historical reasons retained, "
                        "not current authority"
                    ),
                }
            )
        expected = {
            "owner_scoped_families": 2,
            "owner_scoped_relationships": 17,
            "family_preference_occurrences": 6,
            "nonpreferred_occurrences": 11,
            "referenced_owner_decisions": 2,
        }
        self.assertEqual(
            _validate_v5_owner_preference_contract(family_rows, relationship_rows),
            expected,
        )

        owner_by_id = {
            "D-1": {
                "owner_decision_id": "D-1",
                "authority_scope": "OWNER_CONFIRMED_EXPRESSION_SCOPE",
                "decision": (
                    "Regular code CODE-A is 80 proof and is preferred over separately "
                    "printed code CODE-B at 100 proof. The phrase 100% agave does not "
                    "identify 100 proof."
                ),
                "item_scope": "Fixture 750ML; Supplier CODE-A versus CODE-B",
                "mapping_approval": False,
                "price_approval": False,
                "purchasing_approval": False,
            },
            "D-2": {
                "owner_decision_id": "D-2",
                "authority_scope": "OWNER_CONFIRMED_PACK_PREFERENCE",
                "decision": (
                    "24 individual 187mL bottles per case, published at $104.00; "
                    "the code suffix is retained"
                ),
                "item_scope": "Fixture 187ML; Supplier CODE-C",
                "mapping_approval": False,
                "price_approval": False,
                "purchasing_approval": False,
            },
        }
        scope_by_id = {
            "D-1": {"explicit_variant_ids": ["V-1"], "approval_inferred": False},
            "D-2": {"explicit_variant_ids": ["V-2"], "approval_inferred": False},
        }
        self.assertEqual(
            real._validate_owner_preference_scopes(
                family_rows, relationship_rows, owner_by_id, scope_by_id
            ),
            {
                "owner_scoped_families": 2,
                "owner_scoped_relationships": 17,
                "referenced_owner_decisions": 2,
            },
        )

        for field, value in (
            ("preference", "ARBITRARY MUTATED PREFERENCE"),
            ("owner_preference_decision_ids", ["D-2"]),
            ("no_code_replacement_inference", False),
            ("conversion_authority", "APPROVED"),
            ("reason_precedence", "MUTATED"),
        ):
            changed = deepcopy(relationship_rows)
            changed[0][field] = value
            with self.assertRaises(ReviewPackageError):
                _validate_v5_owner_preference_contract(family_rows, changed)

        changed_families = deepcopy(family_rows)
        changed_families[0]["preference"] = "ARBITRARY MUTATED PREFERENCE"
        with self.assertRaisesRegex(ReviewPackageError, "JOIN_MISMATCH"):
            _validate_v5_owner_preference_contract(
                changed_families, relationship_rows
            )

        coordinated_families = deepcopy(family_rows)
        original = coordinated_families[0]["preference"]
        coordinated_families[0]["preference"] = "APPROVED PRIVATE CHOICE"
        coordinated_relationships = deepcopy(relationship_rows)
        for relationship in coordinated_relationships:
            if relationship["preference"] == original:
                relationship["preference"] = "APPROVED PRIVATE CHOICE"
        self.assertEqual(
            _validate_v5_owner_preference_contract(
                coordinated_families, coordinated_relationships
            ),
            expected,
        )
        with self.assertRaisesRegex(ReviewPackageError, "JOIN_MISMATCH"):
            real._validate_owner_preference_scopes(
                coordinated_families,
                coordinated_relationships,
                owner_by_id,
                scope_by_id,
            )

        changed_scopes = deepcopy(scope_by_id)
        changed_scopes["D-1"]["explicit_variant_ids"] = ["V-2"]
        with self.assertRaisesRegex(ReviewPackageError, "JOIN_MISMATCH"):
            real._validate_owner_preference_scopes(
                family_rows, relationship_rows, owner_by_id, changed_scopes
            )

    def test_raw_root_dispatch_is_exact_and_does_not_relax_legacy_canonical_checks(self):
        raw = b'{\n  "format": "fixture"\n}\n'
        with patch.object(real, "A1_ROOT_BYTES", len(raw)), patch.object(
            real, "A1_ROOT_SHA256", _sha(raw)
        ):
            self.assertTrue(real.is_real_v5_a1_root(raw))
            self.assertFalse(real.is_real_v5_a1_root(raw + b" "))
            self.assertFalse(real.is_real_v5_a1_root(raw.replace(b"fixture", b"changed")))

        sentinel = object()
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "BUFFALO_REVIEW_PACKAGE_ROOT.json").write_bytes(raw)
            with patch.object(real, "is_real_v5_a1_root", return_value=True), patch.object(
                real, "read_real_v5_a1_package", return_value=sentinel
            ) as adapter:
                self.assertIs(read_review_package(root), sentinel)
                self.assertEqual(adapter.call_args.kwargs["root_bytes"], raw)

            with self.assertRaisesRegex(ReviewPackageError, "NONCANONICAL_MANIFEST"):
                read_review_package(root)

    def test_deep_root_identity_and_zero_authority_are_code_owned_after_dispatch(self):
        root = _root_document()
        raw = json.dumps(root, indent=2).encode("utf-8") + b"\n"
        replacements = {
            "A1_ROOT_BYTES": len(raw),
            "A1_ROOT_SHA256": _sha(raw),
            "A1_CONTROLS_SHA256": real._digest(root["controls"]),
            "A1_TABLE_DESCRIPTOR_SHA256": real._digest(root["tables"]),
            "A1_EMBEDDED_DESCRIPTOR_SHA256": real._digest(root["embedded_files"]),
            "A1_SOURCE_DESCRIPTOR_SHA256": real._digest(root["source_evidence"]),
            "A1_EXTERNAL_DESCRIPTOR_SHA256": real._digest(root["external_original_files"]),
        }
        with patch.multiple(real, **replacements):
            self.assertEqual(real._validate_root(raw, ReviewLimits())["package_id"], real.A1_PACKAGE_ID)

        for field, value, code in (
            ("semantic_addendum_revision", "UNKNOWN", "A1_ROOT_IDENTITY_MISMATCH"),
            ("authority", "APPROVED", "A1_ROOT_IDENTITY_MISMATCH"),
        ):
            changed = deepcopy(root)
            changed[field] = value
            changed_raw = json.dumps(changed, indent=2).encode("utf-8") + b"\n"
            with patch.multiple(
                real,
                **{
                    **replacements,
                    "A1_ROOT_BYTES": len(changed_raw),
                    "A1_ROOT_SHA256": _sha(changed_raw),
                },
            ):
                with self.assertRaisesRegex(ReviewPackageError, code):
                    real._validate_root(changed_raw, ReviewLimits())

        changed = deepcopy(root)
        changed["review_only_flags"]["mapping_approval"] = True
        changed_raw = json.dumps(changed, indent=2).encode("utf-8") + b"\n"
        with patch.multiple(
            real,
            **{
                **replacements,
                "A1_ROOT_BYTES": len(changed_raw),
                "A1_ROOT_SHA256": _sha(changed_raw),
            },
        ):
            with self.assertRaisesRegex(ReviewPackageError, "UNAUTHORIZED_APPROVAL_CLAIM"):
                real._validate_root(changed_raw, ReviewLimits())

    def test_seal_schema_and_identity_are_checked_after_raw_hash(self):
        root_bytes = b"root"
        seal = {
            "format": "BUFFALO_PRIVATE_REVIEW_SNAPSHOT_SEAL_V1",
            "package_id": real.A1_PACKAGE_ID,
            "root_manifest": {
                "file": "BUFFALO_REVIEW_PACKAGE_ROOT.json",
                "bytes": len(root_bytes),
                "sha256": real.A1_ROOT_SHA256,
            },
            "archives": [],
            "review_reader_files": [],
            "authority": (
                "UNAPPROVED_REVIEW; integrity seal is not approval or a digital signature"
            ),
        }
        payload = json.dumps(seal, indent=2).encode("utf-8") + b"\n"
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "BUFFALO_REVIEW_PACKAGE_SEAL.json").write_bytes(payload)
            with _DirectorySource(root, ReviewLimits()) as source, patch.object(
                real, "A1_SEAL_BYTES", len(payload)
            ), patch.object(real, "A1_SEAL_SHA256", _sha(payload)), patch.object(
                real, "_ARCHIVES", ()
            ), patch.object(real, "_READER_FILES", {}):
                self.assertEqual(real._validate_seal(source, root_bytes)["package_id"], real.A1_PACKAGE_ID)

    def test_top_level_contract_refuses_missing_and_extra_shards(self):
        expected = {
            "BUFFALO_REVIEW_PACKAGE_ROOT.json",
            "BUFFALO_REVIEW_PACKAGE_SEAL.json",
            "one.zip",
        }
        with patch.object(real, "_ARCHIVE_BY_NAME", {"one.zip": (1, "0" * 64, "ROLE")}), patch.object(
            real, "_AUXILIARY_FILES", {}
        ):
            real._validate_top_level(SimpleNamespace(names=tuple(expected)))
            for names, code in (
                (expected - {"one.zip"}, "MISSING_FILE"),
                (expected | {"extra.zip"}, "UNDECLARED_FILE"),
            ):
                with self.subTest(code=code), self.assertRaisesRegex(ReviewPackageError, code):
                    real._validate_top_level(SimpleNamespace(names=tuple(names)))

    def test_a1_archive_entry_limit_is_scoped_and_still_bounded(self):
        with TemporaryDirectory() as temp:
            accepted = Path(temp) / "accepted.zip"
            with ZipFile(accepted, "w", ZIP_DEFLATED) as archive:
                for index in range(2_124):
                    archive.writestr(f"rows/{index:04d}.json", b"")
            with self.assertRaisesRegex(ReviewPackageError, "TOO_MANY_ENTRIES"):
                _ZipSource(accepted, ReviewLimits())
            with _ZipSource(accepted, real._shard_limits(ReviewLimits())) as source:
                self.assertEqual(len(source.names), 2_124)

            refused = Path(temp) / "refused.zip"
            with ZipFile(refused, "w", ZIP_DEFLATED) as archive:
                for index in range(4_097):
                    archive.writestr(f"rows/{index:04d}.json", b"")
            with self.assertRaisesRegex(ReviewPackageError, "TOO_MANY_ENTRIES"):
                _ZipSource(refused, real._shard_limits(ReviewLimits()))

    def test_sealed_archive_snapshot_is_immutable_and_refuses_raw_zip_drift(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "one.zip"
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                archive.writestr("rows/one.json", b"{}")
            expected = archive_path.read_bytes()
            contract = {"one.zip": (len(expected), _sha(expected), "FIXTURE")}
            with patch.object(real, "_ARCHIVE_BY_NAME", contract):
                with _DirectorySource(root, ReviewLimits()) as source:
                    snapshot = real._sealed_archive_snapshot(source, "one.zip")
                self.assertEqual(snapshot, expected)
                with _ZipSource(
                    snapshot,
                    ReviewLimits(),
                    display_path=archive_path,
                ) as source:
                    self.assertEqual(source.names, ("rows/one.json",))

                changed = bytearray(expected)
                changed[-1] ^= 1
                archive_path.write_bytes(changed)
                with _DirectorySource(root, ReviewLimits()) as source, self.assertRaisesRegex(
                    ReviewPackageError, "ARCHIVE_SEAL_MISMATCH"
                ):
                    real._sealed_archive_snapshot(source, "one.zip")

    def test_table_part_stream_preserves_native_values_and_refuses_drift(self):
        rows = (
            {"code": "0012-A", "empty": "", "false": False, "null": None, "zero": 0},
            {"amount": Decimal("12.3400"), "suffix": "ABC-01"},
        )
        payload = b"".join(_canonical_json(row) + b"\n" for row in rows)
        with TemporaryDirectory() as temp:
            archive_path = Path(temp) / "part.zip"
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                archive.writestr("parts/one.jsonl", payload)
            part = {
                "path": "parts/one.jsonl",
                "row_count": 2,
                "bytes": len(payload),
                "sha256": _sha(payload),
            }
            with _ZipSource(archive_path, ReviewLimits()) as source:
                observed = tuple(
                    real._stream_table_part(
                        source,
                        part,
                        limits=ReviewLimits(),
                        row_offset=0,
                        yield_rows=True,
                    )
                )
            self.assertEqual(observed, rows)
            self.assertIs(observed[0]["false"], False)
            self.assertIsNone(observed[0]["null"])
            self.assertEqual(observed[1]["amount"], Decimal("12.3400"))

            noncanonical = b'{"code": "0012-A"}\n'
            with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
                archive.writestr("parts/one.jsonl", noncanonical)
            bad_part = {
                "path": "parts/one.jsonl",
                "row_count": 1,
                "bytes": len(noncanonical),
                "sha256": _sha(noncanonical),
            }
            with _ZipSource(archive_path, ReviewLimits()) as source, self.assertRaisesRegex(
                ReviewPackageError, "NONCANONICAL_TABLE_ROW"
            ):
                tuple(
                    real._stream_table_part(
                        source,
                        bad_part,
                        limits=ReviewLimits(),
                        row_offset=0,
                        yield_rows=True,
                    )
                )

    def test_daytime_dictionary_reconciles_all_fields_and_native_types(self):
        values: tuple[object, ...] = (
            None,
            False,
            0,
            Decimal("1.25"),
            "0012-A",
            ["x"],
            {"k": "v"},
        )
        tables: dict[str, PackageTable] = {}
        dictionary: list[dict[str, object]] = []
        field_index = 0
        for table_index in range(24):
            table_name = f"fixture_table_{table_index:02d}"
            field_count = 17 if table_index < 14 else 16
            row: dict[str, object] = {}
            for _ in range(field_count):
                field = f"field_{field_index:03d}"
                value = values[field_index % len(values)]
                row[field] = value
                dictionary.append(
                    {
                        "authority": "UNAPPROVED_REVIEW",
                        "field": field,
                        "join_semantics": "Preserve exact fixture identity",
                        "observed_native_types": [real._native_json_type(value)],
                        "role": (
                            "ADDITIVE_REVIEW_OR_TRANSPORT_SIDECAR; NOT_APPLICATION_SCHEMA"
                        ),
                        "table": table_name,
                        "value_handling": "Preserve null, empty, zero, false and text",
                    }
                )
                field_index += 1
            tables[table_name] = _table(table_name, (row,))
        self.assertEqual(field_index, 398)
        controls = real._validate_daytime_dictionary_contract(dictionary, tables)
        self.assertEqual(controls["daytime_dictionary_fields"], 398)
        self.assertEqual(controls["daytime_native_type_profiles"], "PASS")

        changed = deepcopy(dictionary)
        changed[0]["observed_native_types"] = ["str"]
        with self.assertRaisesRegex(ReviewPackageError, "native types differ"):
            real._validate_daytime_dictionary_contract(changed, tables)

    def test_authority_and_pdf_rules_are_field_and_table_specific(self):
        builder = real._ProjectionBuilder(
            {"book.pdf": {"sha256": "a" * 64, "physical_pages": 2}}
        )
        builder._authority(
            "effective_v5",
            "historical_catalog_snapshot",
            {
                "mapping_approval": "UNAPPROVED",
                "price_authority": "NOT_VERIFIED_CURRENT",
                "questions_sent": 0,
            },
            1,
        )
        with self.assertRaisesRegex(ReviewPackageError, "UNAUTHORIZED_APPROVAL_CLAIM"):
            builder._authority("effective_v5", "fixture", {"mapping_approved": True}, 1)
        with self.assertRaisesRegex(ReviewPackageError, "UNAUTHORIZED_OPERATIONAL_EFFECT"):
            builder._authority("daytime_addendum", "fixture", {"questions_sent": False}, 1)
        for field in (
            "canonical_mutations",
            "operational_database_access",
            "root_mutations",
            "shopify_writes",
        ):
            with self.subTest(zero_authority_field=field), self.assertRaisesRegex(
                ReviewPackageError, "UNAUTHORIZED_OPERATIONAL_EFFECT"
            ):
                builder._authority("v5_sidecar", "fixture", {field: 1}, 1)
        for field in (
            "buying_authority_granted",
            "current_approval_claim",
            "price_ladder_eligible",
            "projection_authority",
            "rule_scope_approved",
            "wholesale_price_authority",
            "wholesale_price_or_account_authority",
        ):
            with self.subTest(false_authority_field=field), self.assertRaisesRegex(
                ReviewPackageError, "UNAUTHORIZED_APPROVAL_CLAIM"
            ):
                builder._authority("v5_sidecar", "fixture", {field: True}, 1)
        with self.assertRaisesRegex(ReviewPackageError, "approval status"):
            builder._authority(
                "effective_v5",
                "candidate_matches",
                {"approval_status": "APPROVED"},
                1,
            )
        for table_name, field, expected in (
            ("source_offers", "source_approval_text", "UNAPPROVED_CANDIDATE"),
            ("complete_combos", "source_approval_text", "UNAPPROVED_CANDIDATE"),
            (
                "price_ladders",
                "source_approval_text",
                "UNAPPROVED_NO_TIER_SELECTED",
            ),
            (
                "historical_catalog_snapshot",
                "price_authority",
                "NOT_VERIFIED_CURRENT",
            ),
        ):
            builder._authority(
                "effective_v5", table_name, {field: expected}, 1
            )
            with self.subTest(negative_authority_table=table_name), self.assertRaisesRegex(
                ReviewPackageError, "negative-authority"
            ):
                builder._authority(
                    "effective_v5", table_name, {field: "APPROVED"}, 1
                )
        with self.assertRaisesRegex(ReviewPackageError, "UNAUTHORIZED_APPROVAL_CLAIM"):
            builder._authority(
                "v5_sidecar",
                "supplier_vocabulary_candidates_v5",
                {"cross_variant_or_all_supplier_generalization_allowed": True},
                1,
            )
        with self.assertRaisesRegex(ReviewPackageError, "approval status"):
            _validate_v5_row_authority(
                {"approval_status": "APPROVED"},
                table_name="targeted_candidate_after_records",
                row_number=1,
            )
        with self.assertRaisesRegex(
            ReviewPackageError, "UNAUTHORIZED_OPERATIONAL_EFFECT"
        ):
            _validate_v5_row_authority(
                {"shopify_writes": 1},
                table_name="supplier_vocabulary_candidates_v5",
                row_number=1,
            )
        with self.assertRaisesRegex(ReviewPackageError, "explicit null"):
            builder._authority(
                "v5_sidecar",
                "supplier_vocabulary_candidates_v5",
                {"approved_vendor_uuid_crosswalk": "vendor"},
                1,
            )
        with self.assertRaisesRegex(ReviewPackageError, "synthetic scenario"):
            builder._authority(
                "daytime_addendum",
                "requests__additional_synthetic_scenarios",
                {"activation": True, "selected_tier": None},
                1,
            )
        with self.assertRaisesRegex(ReviewPackageError, "must remain false"):
            _validate_v5_row_authority(
                {"v4_normalized_alcohol_import_allowed": True},
                table_name="targeted_candidate_after_records",
                row_number=1,
            )

        builder._source_reference(
            {"source_file": "internal-notes.md"},
            name="unrelated_metadata",
            table="fixture",
            row_number=1,
        )
        with self.assertRaisesRegex(ReviewPackageError, "source PDF is not declared"):
            builder._source_reference(
                {"source_file": "other.pdf", "source_sha256": "b" * 64, "source_page": 1},
                name="source_offers",
                table="source_offers",
                row_number=1,
            )
        with self.assertRaisesRegex(ReviewPackageError, "out of range"):
            builder._source_reference(
                {
                    "source_file": "book.pdf",
                    "source_sha256": "a" * 64,
                    "source_pages": [1, 3],
                },
                name="comparison_offer_references",
                table="comparison_offer_references",
                row_number=1,
            )

    def test_nullable_normalized_fields_are_scoped_and_reviewed_nulls_win(self):
        row = _normalized_row()
        legacy_row = dict(row)
        legacy_row["supplier_description"] = "Fixture description"
        with self.assertRaisesRegex(ReviewPackageError, "supplier_sku"):
            _validate_v5_normalized_contract_row(legacy_row, row_number=1)
        _validate_v5_normalized_contract_row(
            row,
            row_number=1,
            allow_null_supplier_sku=True,
            allow_null_supplier_description=True,
        )

        builder = real._ProjectionBuilder(
            {"book.pdf": {"sha256": "a" * 64, "physical_pages": 2}}
        )
        builder.normalized_links[1] = (
            "tier-1",
            "offer-1",
            "book.pdf",
            "a" * 64,
            1,
            (),
        )
        builder.observe("effective_v5", "normalized_price_contract_review", dict(row), 1)
        changed = dict(row)
        changed["raw_pack"] = "legacy fallback"
        with self.assertRaisesRegex(ReviewPackageError, "explicit null"):
            builder.observe("effective_v5", "normalized_price_contract_review", changed, 2)

    def test_complete_combo_provenance_binds_source_components_tiers_and_raw_evidence(self):
        projection, source = _fixture_projection()
        evidence = {
            "component_id": "C-1",
            "supplier_code": "001-A",
            "title": "Fixture component",
            "quantity_as_printed": 1,
            "quantity_unit": "BT",
            "size_raw": "750ML",
            "evidence": {
                "source_file": "book.pdf",
                "source_page": 1,
                "source_sha256": "a" * 64,
                "raw_text": "fixture component",
            },
        }
        component = {
            "source_offer_id": "O-1",
            "source_component_id": "C-1",
            "component_supplier_sku": "001-A",
            "component_description": "Fixture component",
            "quantity_raw": 1,
            "quantity_unit_raw": "BT",
            "size_text": "750ML",
            "supplier_name_raw": "Fixture Supplier",
            "supplier_combo_code": "001",
            "source_file": "book.pdf",
            "source_page": 1,
            "source_sha256": "a" * 64,
            "raw_attributes_json": _canonical_json(evidence).decode("utf-8"),
        }
        combo = {
            field: source[field] for field in real._COMPLETE_COMBO_SHARED_SOURCE_FIELDS
        }
        combo.update(
            {
                "component_ids": '["C-1"]',
                "tier_ids": '["T-1"]',
                "component_evidence": _canonical_json([evidence]).decode("utf-8"),
                "component_quantity_total_as_printed": 1,
            }
        )
        validation = {
            "source_offer_id": "O-1",
            "source_component_ids": ["C-1"],
            "source_tier_ids": ["T-1"],
            "component_quantity_total_as_printed": 1,
        }
        real._validate_complete_combo_provenance(
            [combo], [component], [validation], projection, limits=ReviewLimits()
        )

        changed = deepcopy(combo)
        changed["physical_units_per_case"] = None
        with self.assertRaisesRegex(ReviewPackageError, "nulls were collapsed"):
            real._validate_complete_combo_provenance(
                [changed], [component], [validation], projection, limits=ReviewLimits()
            )
        changed_component = deepcopy(component)
        raw = json.loads(changed_component["raw_attributes_json"])
        raw["evidence"]["raw_text"] = "drifted"
        changed_component["raw_attributes_json"] = json.dumps(raw)
        with self.assertRaisesRegex(ReviewPackageError, "component evidence differs"):
            real._validate_complete_combo_provenance(
                [combo],
                [changed_component],
                [validation],
                projection,
                limits=ReviewLimits(),
            )

    def test_remaining_combo_family_and_total_provenance_is_exact(self):
        projection, source = _fixture_projection()
        nested_source = {
            field: source[field] for field in real._REMAINING_SOURCE_FIELDS
        }
        projected_tier = projection.tiers_by_offer["O-1"][0]
        tier_map = (
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
        family_source = {
            field: source[field]
            for field in real._FAMILY_SOURCE_FIELDS
            if field != "full_existing_price_ladder"
        }
        family_source["full_existing_price_ladder"] = [
            {left: projected_tier[right] for left, right in tier_map}
        ]
        family = {
            "family_review_id": "F-1",
            "source_offers": [family_source],
            "catalog_records": [
                {
                    "variant_id": "V-1",
                    "cohort": "ORIGINAL2000",
                    "original2000_population_member": True,
                    "current_addition_catalog_evidence": None,
                }
            ],
            "offer_preference": None,
            "split_fees_applied": None,
        }
        visual = {"path": "fixture.png", "sha256": "b" * 64}
        component = {
            "research_relationship_id": "RC-1",
            "family_review_id": "F-1",
            "source_offer": nested_source,
            "source_tier_ids": ["T-1"],
            "source_visual": visual,
            "component_quantity_raw": 1,
            "component_cost_allocation": None,
            "order_increment": None,
            "physical_component_count_is_not_shopify_conversion": True,
            "shopify_sellable_units_per_combo_component": None,
            "shopify_sellable_units_per_entire_combo": None,
            "supplier_qualifying_units_per_combo_component": None,
            "supplier_qualifying_units_per_entire_combo": None,
        }
        printed = {
            left: projected_tier[right]
            for left, right in tier_map
            if left not in {"printed_page", "total_cost", "total_cost_basis"}
        }
        printed["source_offer_id"] = "O-1"
        printed["source_file"] = "book.pdf"
        total = {
            "research_combo_summary_id": "RT-1",
            "family_review_id": "F-1",
            "source_offer": nested_source,
            "source_visual": visual,
            "component_research_rows": 1,
            "existing_component_rows": 1,
            "existing_nonnull_component_ids": 0,
            "component_count_sum_reconciled": True,
            "average_size_created": False,
            "component_cost_allocation": None,
            "source_component_id": None,
            "complete_combo_total_join": [],
            "complete_combo_total_join_status": (
                "ABSENT_FROM_EXISTING_COMPLETE_COMBOS_TABLE; "
                "DO_NOT_FABRICATE_A_TOTAL_ID"
            ),
            "printed_component_quantity_sum": 1,
            "expected_explicit_total": 1,
            "printed_total_evidence": [printed],
            "arithmetic_checks": [
                {
                    "source_tier_id": "T-1",
                    "printed_case_total": 10,
                    "printed_per_bottle_amount": 1,
                    "explicit_component_count_for_arithmetic": 1,
                    "printed_unit_times_count": 1,
                    "difference_from_printed_case_total": -9,
                    "not_a_component_cost_allocation": True,
                }
            ],
        }
        source_component = {"source_offer_id": "O-1", "source_component_id": None}
        arguments = (
            [component],
            [total],
            [family],
            [source_component],
            set(),
            projection,
            {"V-1"},
            {"V-1"},
        )
        real._validate_remaining_combo_provenance(
            *arguments, expected_family_controls=(1, 1, 1, 1)
        )
        changed = deepcopy(total)
        changed["average_size_created"] = True
        with self.assertRaisesRegex(ReviewPackageError, "controls differ"):
            real._validate_remaining_combo_provenance(
                [component],
                [changed],
                [family],
                [source_component],
                set(),
                projection,
                {"V-1"},
                {"V-1"},
                expected_family_controls=(1, 1, 1, 1),
            )
        changed_family = deepcopy(family)
        changed_family["source_offers"][0]["full_existing_price_ladder"][0][
            "case_price_printed"
        ] = 11
        with self.assertRaisesRegex(ReviewPackageError, "price Tier differs"):
            real._validate_remaining_combo_provenance(
                [component],
                [total],
                [changed_family],
                [source_component],
                set(),
                projection,
                {"V-1"},
                {"V-1"},
                expected_family_controls=(1, 1, 1, 1),
            )

    def test_relationship_source_projection_is_bound_to_exact_offer_and_tier(self):
        projection, source = _fixture_projection()
        field_map = {
            "supplier_name_raw": "supplier_name_raw",
            "supplier_code_exact": "supplier_sku",
            "source_description_raw": "supplier_description",
            "source_package_type_raw": "package_type",
            "source_file": "source_file",
            "source_page": "source_page",
            "printed_page": "printed_page",
            "source_sha256": "source_sha256",
            "source_period_raw": "source_period_status",
            "source_territory": "territory",
            "source_case_pack_raw": "supplier_pack_count",
            "source_physical_count_raw": "physical_units_per_case",
            "source_size_raw": "size_text",
            "source_split_availability": "split_availability",
            "source_split_fee_basis": "split_fee_basis_inclusion",
            "source_assortment_line_terms": "assortment_line_terms",
            "source_vintage_raw": "vintage_raw",
        }
        relationship = {
            left: source[right] for left, right in field_map.items()
        }
        relationship.update({"source_offer_id": "O-1", "source_tier_ids": ["T-1"]})
        real._validate_relationship_source_provenance([relationship], projection)
        changed = deepcopy(relationship)
        changed["source_description_raw"] = "drifted"
        with self.assertRaisesRegex(ReviewPackageError, "provenance differs"):
            real._validate_relationship_source_provenance([changed], projection)

    def test_display_projection_binds_frozen_sibling_and_diagnostic_precedence(self):
        projection, source = _fixture_projection()
        relationship = {
            "candidate_disposition": "SEARCH_LEAD_ONLY",
            "source_retail_pack_raw": source["containers_per_pack_raw"],
            "source_physical_count_raw": source["physical_units_per_case"],
            "reviewed_qualifying_units_per_case": None,
            "reviewed_shopify_units_per_case": None,
        }
        display = {
            "source_offer_id": "O-1",
            "source_record_sha256": projection.source_offer_record_hashes["O-1"],
            "supplier": source["supplier_name_raw"],
            "supplier_code_exact": source["supplier_sku"],
            "source_page": source["source_page"],
            "source_sha256": source["source_sha256"],
            "source_physical_count_raw": source["physical_units_per_case"],
            "all_tier_ids": ["T-1"],
            "explicit_null_precedence": True,
            "disposition": "SEARCH_LEAD_ONLY",
            "display_containers_per_retail_pack": source["containers_per_pack_raw"],
            "display_physical_count": source["physical_units_per_case"],
            "display_retail_packs": source["retail_packs_per_case"],
            "reviewed_qualifying_units_per_case": None,
            "reviewed_shopify_units_per_case": None,
        }
        self.assertEqual(
            real._validate_display_offer_projection(
                display,
                relationship=relationship,
                sibling=None,
                diagnostic=None,
                projection=projection,
                expected_supplier="Fixture Supplier",
            ),
            "relationship",
        )
        changed = deepcopy(display)
        changed["supplier"] = "Mutated supplier"
        with self.assertRaisesRegex(ReviewPackageError, "source-record"):
            real._validate_display_offer_projection(
                changed,
                relationship=relationship,
                sibling=None,
                diagnostic=None,
                projection=projection,
                expected_supplier="Fixture Supplier",
            )
        changed = deepcopy(display)
        changed["disposition"] = "PROPOSED_REVIEW_CANDIDATE"
        with self.assertRaisesRegex(ReviewPackageError, "frozen-relationship"):
            real._validate_display_offer_projection(
                changed,
                relationship=relationship,
                sibling=None,
                diagnostic=None,
                projection=projection,
                expected_supplier="Fixture Supplier",
            )

        sibling = {
            "candidate_disposition": "PROPOSED_REVIEW_CANDIDATE",
            "proposed_containers_per_retail_pack": 2,
            "proposed_physical_units_per_case": 12,
            "proposed_retail_packs_per_case": 6,
            "proposed_qualifying_units_per_case": 6,
            "proposed_shopify_units_per_case": 6,
            "supplier_code": source["supplier_sku"],
            "source_record_sha256": projection.source_offer_record_hashes["O-1"],
        }
        sibling_display = {
            **display,
            "disposition": "PROPOSED_REVIEW_CANDIDATE",
            "display_containers_per_retail_pack": 2,
            "display_physical_count": 12,
            "display_retail_packs": 6,
            "reviewed_qualifying_units_per_case": 6,
            "reviewed_shopify_units_per_case": 6,
        }
        self.assertEqual(
            real._validate_display_offer_projection(
                sibling_display,
                relationship=relationship,
                sibling=sibling,
                diagnostic=None,
                projection=projection,
                expected_supplier="Fixture Supplier",
            ),
            "sibling",
        )

        diagnostic = {
            "reviewed_qualifying_units_per_case": None,
            "reviewed_shopify_units_per_case": None,
        }
        diagnostic_display = {
            **display,
            "disposition": "SAME_CODE_DIAGNOSTIC_NOT_REVIEWED_AS_IDENTITY",
        }
        self.assertEqual(
            real._validate_display_offer_projection(
                diagnostic_display,
                relationship=None,
                sibling=None,
                diagnostic=diagnostic,
                projection=projection,
                expected_supplier="Fixture Supplier",
            ),
            "diagnostic",
        )

    def test_review_partitions_are_derived_per_variant(self):
        projection, _ = _fixture_projection()
        source_contract = list(projection.source_offer_contracts["O-1"])
        source_contract[real._SOURCE_OFFER_CONTRACT_FIELDS.index("package_type")] = (
            "STANDARD"
        )
        projection.source_offer_contracts["O-1"] = tuple(source_contract)
        labels = {
            1: "Supported standard candidates — no recorded active material mapping requirement",
            2: "Supported offer evidence — identity, fee, territory, unit or other requirements remain",
            3: "Alternate case / retail pack / conditional gift review",
            4: "Mixed-alcohol gift / fixed-combo component review only",
            5: "No supported offer / unresolved identity / missing source / policy exclusion",
        }
        partitions: list[dict[str, object]] = []
        catalog: dict[str, dict[str, object]] = {}
        display_rows: list[dict[str, object]] = []
        routes: list[dict[str, object]] = []
        gifts: list[dict[str, object]] = []
        fixed: list[dict[str, object]] = []
        for index in range(2_000):
            variant_id = f"V-{index:04d}"
            if index < 1_035:
                partition_number = 1
            elif index < 1_208:
                partition_number = 2
            elif index < 1_291:
                partition_number = 3
            elif index < 1_453:
                partition_number = 4
            else:
                partition_number = 5
            status = "PROPOSED MATCH" if partition_number != 5 else "NOT FOUND"
            catalog[variant_id] = {"status": status}
            partitions.append(
                {
                    "variant_id": variant_id,
                    "top_level_partition": partition_number,
                    "partition_label": labels[partition_number],
                    "identity_mapping_preview": (
                        "REVIEWABLE_SUPPORTED_RELATIONSHIPS_UNAPPROVED"
                        if status == "PROPOSED MATCH"
                        else "BLOCKED_OR_NO_SUPPORTED_IDENTITY"
                    ),
                }
            )
            if partition_number != 5:
                display_rows.append(
                    {
                        "disposition": "PROPOSED_REVIEW_CANDIDATE",
                        "source_offer_id": "O-1",
                        "variant_id": variant_id,
                    }
                )
            if partition_number == 2:
                routes.append(
                    {"daytime_request_class": "OTHER", "variant_id": variant_id}
                )
            elif partition_number == 3:
                gifts.append({"variant_id": variant_id})
            elif partition_number == 4:
                fixed.append({"variant_id": variant_id})
        self.assertEqual(
            real._validate_partition_derivations(
                partitions,
                catalog,
                display_rows,
                routes,
                gifts,
                (),
                fixed,
                (),
                projection,
            ),
            {1: 1_035, 2: 173, 3: 83, 4: 162, 5: 547},
        )
        changed = deepcopy(partitions)
        changed[0]["top_level_partition"] = 5
        with self.assertRaisesRegex(ReviewPackageError, "partition derivation"):
            real._validate_partition_derivations(
                changed,
                catalog,
                display_rows,
                routes,
                gifts,
                (),
                fixed,
                (),
                projection,
            )

    def test_rejected_provenance_binds_source_memory_and_dispositions(self):
        projection, _ = _fixture_projection()
        base = {
            "variant_id": "V-1",
            "offer_id": "O-1",
            "supplier_code": "001",
            "source_file": "book.pdf",
            "physical_page": 1,
            "source_sha256": "a" * 64,
            "raw_evidence": "001 fixture evidence",
            "reviewed_supplier_title": "Fixture description",
            "candidate_disposition": "REJECTED_ATTRIBUTE_CONFLICT",
            "approval_status": "UNAPPROVED_CANDIDATE",
        }
        projection.observe("effective_v5", "rejected_alternatives", base, 1)
        record_sha = projection.base_rejected_facts[("V-1", "O-1")][-1]
        memory = {
            "variant_id": "V-1",
            "source_offer_id": "O-1",
            "current_candidate_disposition": "REJECTED_ATTRIBUTE_CONFLICT",
            "historical_disposition": "REJECTED_ATTRIBUTE_CONFLICT",
            "historical_rejection_still_current": True,
            "historical_record_sha256": record_sha,
        }
        relationship = {
            "variant_id": "V-1",
            "source_offer_id": "O-1",
            "candidate_disposition": "REJECTED_ATTRIBUTE_CONFLICT",
        }
        real._validate_rejected_provenance(projection, [relationship], [memory])
        changed = deepcopy(memory)
        changed["historical_record_sha256"] = "b" * 64
        with self.assertRaisesRegex(ReviewPackageError, "provenance differs"):
            real._validate_rejected_provenance(projection, [relationship], [changed])
        changed = deepcopy(memory)
        changed["historical_rejection_still_current"] = False
        with self.assertRaisesRegex(ReviewPackageError, "provenance differs"):
            real._validate_rejected_provenance(projection, [relationship], [changed])

    def test_private_catalog_ids_are_derived_from_runtime_cohort_joins(self):
        catalog_ids = ["9" + f"{index:011d}" for index in range(2_000)]
        missing = catalog_ids[-1]
        additions = ["8" + f"{index:011d}" for index in range(4)]
        product_ids = {
            variant_id: "7" + f"{index:011d}"
            for index, variant_id in enumerate([*catalog_ids, *additions])
        }
        catalog: list[dict[str, object]] = []
        identity_facts: dict[str, tuple[object, ...]] = {}
        for index, variant_id in enumerate(catalog_ids):
            facts = (
                product_ids[variant_id],
                f"Fixture Product {index}",
                f"Fixture Variant {index}",
                f"SKU-{index}",
                f"BAR-{index}",
                "ACTIVE",
            )
            identity_facts[variant_id] = facts
            product_id, product_title, variant_title, sku, barcode, status = facts
            catalog.append(
                {
                    "variant_id": variant_id,
                    "variant_gid": f"gid://shopify/ProductVariant/{variant_id}",
                    "product_id": product_id,
                    "product_status": status,
                    "product_title": product_title,
                    "current_product_title": product_title,
                    "variant_title": variant_title,
                    "current_variant_title": variant_title,
                    "current_sku_raw": sku,
                    "current_barcode_raw": barcode,
                    "status": "NEEDS_REVIEW",
                    "v5_display_status": "REVIEW_ONLY",
                    "current_identity_returned": variant_id != missing,
                }
            )
        for index, variant_id in enumerate(additions, start=len(catalog_ids)):
            identity_facts[variant_id] = (
                product_ids[variant_id],
                f"Fixture Product {index}",
                f"Fixture Variant {index}",
                f"SKU-{index}",
                f"BAR-{index}",
                "ACTIVE",
            )
        census: list[dict[str, object]] = []
        for variant_id in [*catalog_ids[:-1], *additions]:
            product_id, product_title, variant_title, sku, barcode, status = (
                identity_facts[variant_id]
            )
            census.append(
                {
                    "id": f"gid://shopify/ProductVariant/{variant_id}",
                    "product": {
                        "id": f"gid://shopify/Product/{product_id}",
                        "status": status,
                        "title": product_title,
                    },
                    "title": variant_title,
                    "sku": sku,
                    "barcode": barcode,
                }
            )
        addition_rows: list[dict[str, object]] = []
        for variant_id in additions:
            product_id, product_title, variant_title, sku, barcode, _ = (
                identity_facts[variant_id]
            )
            evidence = {
                field: None for field in real._ADDITION_CATALOG_EVIDENCE_FIELDS
            }
            evidence.update(
                {
                    "variant_id": variant_id,
                    "product_id": product_id,
                    "product_title_raw": product_title,
                    "variant_title_raw": variant_title,
                    "sku_raw": sku,
                    "barcode_raw": barcode,
                    "description_html_raw": f"<p>{product_title}</p>",
                    "vendor_raw": "Fixture Vendor",
                    "selected_options_raw": [{"name": "Size", "value": variant_title}],
                    "product_metafields_raw": [
                        {
                            "id": f"MF-{product_id}-{metafield_index}",
                            "key": f"fixture_{metafield_index}",
                            "namespace": "fixture",
                            "type": "single_line_text_field",
                            "updatedAt": "2026-09-08T00:00:00Z",
                            "value": product_id,
                        }
                        for metafield_index in range(4)
                    ],
                    "variant_metafields_raw": [],
                    "identity_metafields_available": False,
                    "metafield_pagination_complete": True,
                    "product_capture_file": (
                        "analysis/revision_v4/catalog/products.json"
                    ),
                    "product_capture_sha256": "a" * 64,
                    "product_capture_start_utc": "2026-09-09T00:00:00Z",
                    "product_capture_end_utc": "2026-09-09T00:00:01Z",
                    "variant_capture_file": (
                        "analysis/revision_v4/catalog/variants.json"
                    ),
                    "variant_capture_sha256": "b" * 64,
                    "variant_capture_start_utc": "2026-09-09T00:00:02Z",
                    "variant_capture_end_utc": "2026-09-09T00:00:03Z",
                }
            )
            addition_rows.append(
                {
                    "variant_id": variant_id,
                    "product_title": product_title,
                    "variant_title": variant_title,
                    "catalog_evidence": evidence,
                    "original2000_population_member": False,
                }
            )
        observed_catalog, observed_census = real._validate_catalog_census_cohorts(
            catalog, census, (addition_rows, deepcopy(addition_rows))
        )
        self.assertEqual(observed_catalog - observed_census, {missing})
        self.assertEqual(observed_census - observed_catalog, set(additions))
        changed = deepcopy(addition_rows)
        changed[0]["catalog_evidence"]["variant_id"] = additions[1]
        with self.assertRaisesRegex(ReviewPackageError, "catalog identity differs"):
            real._validate_catalog_census_cohorts(
                catalog, census, (addition_rows, changed)
            )

        changed_census = deepcopy(census)
        changed_census[0]["title"], changed_census[1]["title"] = (
            changed_census[1]["title"],
            changed_census[0]["title"],
        )
        with self.assertRaisesRegex(ReviewPackageError, "identity fields differ"):
            real._validate_catalog_census_cohorts(
                catalog, changed_census, (addition_rows, deepcopy(addition_rows))
            )

        projection = real._ProjectionBuilder({})
        for name in real._ORIGINAL_IDENTITY_COHORT_TABLES:
            projection.original_identity_cohorts[name] = set(catalog_ids)
        self.assertEqual(
            real._validate_original_identity_cohorts(projection, set(catalog_ids)),
            5,
        )
        projection.original_identity_cohorts["coverage_change_ledger"].remove(
            catalog_ids[0]
        )
        with self.assertRaisesRegex(ReviewPackageError, "identity surfaces"):
            real._validate_original_identity_cohorts(projection, set(catalog_ids))

        product_path = "evidence/v4/analysis/revision_v4/catalog/products.json"
        variant_path = "evidence/v4/analysis/revision_v4/catalog/variants.json"
        embedded = {
            product_path: {"sha256": "a" * 64},
            variant_path: {"sha256": "b" * 64},
        }
        product_nodes = []
        variant_nodes = []
        for row in addition_rows:
            evidence = row["catalog_evidence"]
            assert isinstance(evidence, dict)
            product_id = str(evidence["product_id"])
            variant_id = str(evidence["variant_id"])
            product_nodes.append(
                {
                    "descriptionHtml": evidence["description_html_raw"],
                    "handle": f"fixture-{product_id}",
                    "id": f"gid://shopify/Product/{product_id}",
                    "metafields": {
                        "nodes": evidence["product_metafields_raw"],
                        "pageInfo": {
                            "endCursor": f"cursor-{product_id}",
                            "hasNextPage": False,
                        },
                    },
                    "productType": "Fixture",
                    "status": "ACTIVE",
                    "tags": [],
                    "title": evidence["product_title_raw"],
                    "updatedAt": "2026-09-09T00:00:00Z",
                    "vendor": evidence["vendor_raw"],
                }
            )
            variant_nodes.append(
                {
                    "barcode": evidence["barcode_raw"],
                    "id": f"gid://shopify/ProductVariant/{variant_id}",
                    "metafields": {
                        "nodes": evidence["variant_metafields_raw"],
                        "pageInfo": {"endCursor": None, "hasNextPage": False},
                    },
                    "product": {"id": f"gid://shopify/Product/{product_id}"},
                    "selectedOptions": evidence["selected_options_raw"],
                    "sku": evidence["sku_raw"],
                    "title": evidence["variant_title_raw"],
                    "updatedAt": "2026-09-09T00:00:00Z",
                }
            )
        capture_documents = {
            product_path: {
                "capture_start_utc": "2026-09-09T00:00:00Z",
                "capture_end_utc": "2026-09-09T00:00:01Z",
                "requested_ids": [row["id"] for row in product_nodes],
                "result": {"data": {"nodes": product_nodes}},
            },
            variant_path: {
                "capture_start_utc": "2026-09-09T00:00:02Z",
                "capture_end_utc": "2026-09-09T00:00:03Z",
                "requested_ids": [row["id"] for row in variant_nodes],
                "result": {"data": {"nodes": variant_nodes}},
            },
        }
        self.assertEqual(
            real._validate_addition_capture_evidence(
                addition_rows, census, embedded, capture_documents, ReviewLimits()
            ),
            8,
        )
        changed_capture = deepcopy(capture_documents)
        changed_capture[product_path]["result"]["data"]["nodes"][0]["title"] = (
            "Mismatched product"
        )
        with self.assertRaisesRegex(ReviewPackageError, "node provenance differs"):
            real._validate_addition_capture_evidence(
                addition_rows, census, embedded, changed_capture, ReviewLimits()
            )
        changed_status = deepcopy(capture_documents)
        changed_status[product_path]["result"]["data"]["nodes"][0][
            "status"
        ] = "ARCHIVED"
        with self.assertRaisesRegex(ReviewPackageError, "node provenance differs"):
            real._validate_addition_capture_evidence(
                addition_rows, census, embedded, changed_status, ReviewLimits()
            )
        invalid_time = deepcopy(capture_documents)
        invalid_time[product_path]["capture_end_utc"] = "2026-09-08T23:59:59Z"
        with self.assertRaisesRegex(ReviewPackageError, "time bounds differ"):
            real._validate_addition_capture_evidence(
                addition_rows, census, embedded, invalid_time, ReviewLimits()
            )
        unsafe = deepcopy(addition_rows)
        unsafe[0]["catalog_evidence"]["product_capture_file"] = "../capture.json"
        with self.assertRaisesRegex(ReviewPackageError, "UNSAFE_PATH"):
            real._validate_addition_capture_evidence(
                unsafe, census, embedded, capture_documents, ReviewLimits()
            )

    def test_duplicate_effective_and_v5_surfaces_are_hash_bound(self):
        pairs = (
            ("catalog_coverage", "catalog_coverage_v5"),
            ("current_additions_review", "current_additions_review_v5"),
            ("unresolved_dependencies", "unresolved_dependencies_v5"),
        )
        effective: dict[str, PackageTable] = {}
        sidecars: dict[str, PackageTable] = {}
        for index, (left, right) in enumerate(pairs):
            rows = ({"fixture_id": index},)
            effective[left] = _table(left, rows)
            sidecars[right] = _table(right, rows)
        self.assertEqual(
            real._validate_duplicate_v5_surfaces(effective, sidecars), 3
        )
        changed = dict(sidecars)
        changed["catalog_coverage_v5"] = _table(
            "catalog_coverage_v5", ({"fixture_id": "changed"},)
        )
        with self.assertRaisesRegex(
            ReviewPackageError, "duplicate table surfaces differ"
        ):
            real._validate_duplicate_v5_surfaces(effective, changed)

    def test_sealed_specialist_rows_bind_overlay_and_gift_projections(self):
        alcohol_rows = tuple(
            {
                "challenge_review_id": f"AG-{index}",
                "components": [{"fixture": index}],
                "mapping_approved": False,
                "root_adjudication": (
                    "COMPONENT_ONLY_REVIEW_ACCEPTED_WITH_ALL_NAMED_DEPENDENCIES"
                ),
                "root_reviewed_at_utc": "2026-09-09T00:00:00Z",
            }
            for index in range(8)
        )
        overlay_rows = tuple(
            {
                "addendum_revision": real.A1_ADAPTER,
                "before": {"fixture": index},
                "does_not_replace_frozen_source_row": True,
                "explicit_reviewed_null_precedence": True,
                "overlay_field_changes": [
                    {
                        "after": index + 1,
                        "after_present": True,
                        "before": index,
                        "before_present": True,
                        "field": "fixture",
                    }
                ],
                "recommendation_id": f"OV-{index}",
                "root_adjudication": (
                    "ACCEPTED_AS_UNAPPROVED_ADDITIVE_REVIEW_EVIDENCE"
                ),
                "second_read_only_challenge": (
                    "Root visual challenge of exact correction pages; same model, "
                    "not independent Claude approval"
                ),
                "source_offer_id": f"SO-{index}",
                "source_record_sha256": f"{index:064x}",
            }
            for index in range(25)
        )
        change_rows = tuple(
            {
                "after_addendum_record_sha256": real.canonical_record_sha256(row),
                "before_record_sha256": row["source_record_sha256"],
                "change_id": row["recommendation_id"],
                "fields": row["overlay_field_changes"],
                "frozen_record_changed": False,
                "kind": "ADD_SOURCE_REVIEW_OVERLAY",
                "target_source_offer_id": row["source_offer_id"],
            }
            for row in overlay_rows
        )
        tables = {
            "alcohol_gift_components_v5": _table(
                "alcohol_gift_components_v5", alcohol_rows
            ),
            "source_review_overlays": _table(
                "source_review_overlays", overlay_rows
            ),
            "daytime_addendum__exact_change_log": _table(
                "daytime_addendum__exact_change_log", change_rows
            ),
        }
        specialist = {
            "alcohol_gift_components_v5": tuple(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"root_adjudication", "root_reviewed_at_utc"}
                }
                for row in alcohol_rows
            ),
            "source_review_overlays": tuple(
                {
                    key: value
                    for key, value in row.items()
                    if key
                    not in {
                        "addendum_revision",
                        "does_not_replace_frozen_source_row",
                        "explicit_reviewed_null_precedence",
                        "overlay_field_changes",
                        "root_adjudication",
                        "second_read_only_challenge",
                    }
                }
                for row in overlay_rows
            ),
        }
        self.assertEqual(
            real._validate_specialist_evidence_projections(tables, specialist),
            {
                "alcohol_gift_components_v5": 8,
                "source_review_overlays": 25,
                "overlay_change_log_rows": 25,
            },
        )

        drifted = deepcopy(specialist)
        drifted["alcohol_gift_components_v5"][0]["components"] = [
            {"fixture": "changed"}
        ]
        with self.assertRaisesRegex(ReviewPackageError, "projection differs"):
            real._validate_specialist_evidence_projections(tables, drifted)

        coordinated_rows = list(deepcopy(overlay_rows))
        coordinated_rows[0]["before"] = {"fixture": "changed"}
        coordinated_tables = dict(tables)
        coordinated_tables["source_review_overlays"] = _table(
            "source_review_overlays", tuple(coordinated_rows)
        )
        coordinated_evidence = deepcopy(specialist)
        coordinated_evidence["source_review_overlays"][0]["before"] = {
            "fixture": "changed"
        }
        with self.assertRaisesRegex(ReviewPackageError, "change-log provenance"):
            real._validate_specialist_evidence_projections(
                coordinated_tables, coordinated_evidence
            )

    def test_gift_ids_and_alcohol_components_bind_to_exact_occurrences(self):
        links = {
            "gift-1": {
                "variant_id": "V-1",
                "source_offer_id": "O-1",
                "supplier_code_exact": "001",
                "source_case_pack_raw": 6,
                "source_physical_count_raw": 6,
                "source_file": "book.pdf",
                "source_page": 1,
                "source_sha256": "a" * 64,
            },
            "gift-2": {
                "variant_id": "V-2",
                "source_offer_id": "O-2",
                "supplier_code_exact": "002",
                "source_case_pack_raw": 6,
                "source_physical_count_raw": 6,
                "source_file": "book.pdf",
                "source_page": 2,
                "source_sha256": "a" * 64,
            },
        }
        gifts = [
            {
                "relationship_id": "gift-1",
                "variant_id": "V-1",
                "source_offer_id": "O-1",
                "source_supplier_code": "001",
                "source_case_pack_raw": 6,
                "source_file": "book.pdf",
                "source_page": 1,
                "source_sha256": "a" * 64,
                "root_review_references": ["root-1"],
                "specialist_challenge_id": "challenge-1",
                "root_reviewed_at_utc": "2026-09-09T00:00:00Z",
                "explicit_null_precedence": True,
                "allocated_component_cost": None,
                "order_increment": None,
                "qualifying_units_per_case": None,
                "shopify_units_per_case": None,
                "whole_offer_to_single_variant_allowed": False,
                "may_influence_normalized_price_projection": False,
            },
            {
                "relationship_id": "gift-2",
                "variant_id": "V-2",
                "source_offer_id": "O-2",
                "source_supplier_code": "002",
                "source_case_pack_raw": 6,
                "source_file": "book.pdf",
                "source_page": 2,
                "source_sha256": "a" * 64,
                "root_review_references": ["root-2"],
                "specialist_challenge_id": None,
                "root_reviewed_at_utc": "2026-09-09T00:00:00Z",
                "explicit_null_precedence": True,
                "allocated_component_cost": None,
                "order_increment": None,
                "qualifying_units_per_case": None,
                "shopify_units_per_case": None,
                "whole_offer_to_single_variant_allowed": False,
                "may_influence_normalized_price_projection": False,
            },
        ]
        alcohol = [
            {
                "variant_id": "V-1",
                "source_offer_id": "O-1",
                "supplier_code_exact": "001",
                "source_case_pack_raw_preserved": 6,
                "source_physical_units_per_case_raw_preserved": 6,
                "components": [
                    {
                        "description_raw": "PRIMARY FIXTURE",
                        "quantity_per_gift_candidate": 1,
                        "role": "PRIMARY",
                        "size_ml_candidate": 750,
                    },
                    {
                        "description_raw": "2 50ML FIXTURES",
                        "quantity_per_gift_candidate": 2,
                        "role": "ADDITIONAL_ALCOHOL_IDENTITY_UNRESOLVED",
                        "size_ml_candidate": 50,
                    },
                ],
                "primary_bottles_per_case_candidate": 6,
                "additional_50ml_bottles_per_case_candidate": 12,
                "total_physical_alcohol_containers_per_case_candidate": 18,
                "published_ambiguities": ["Fixture identities remain unresolved"],
                "remaining_evidence_needed": [
                    {"evidence": "Exact components", "who": "Supplier"},
                    {"evidence": "Retail handling", "who": "Owner workflow"},
                    {"evidence": "Current terms", "who": "Supplier program"},
                ],
                "root_relationship_review_id": "root-1",
                "challenge_review_id": "challenge-1",
                "root_reviewed_at_utc": "2026-09-09T00:00:00Z",
                "source": {
                    "source_offer_id": "O-1",
                    "supplier_sku": "001",
                    "source_file": "book.pdf",
                    "source_page": 1,
                    "source_sha256": "a" * 64,
                },
                "explicit_null_precedence": (
                    "REVIEWED_NULL: no fallback to original normal-bottle conversion "
                    "estimates or generic product-level metadata"
                ),
                "retail_packaging_acceptance": "NOT_APPROVED",
                "allocated_component_cost": None,
                "component_qualifying_units_per_case": None,
                "component_shopify_units_per_case": None,
                "quantity_order_increment": None,
                "shopify_units_per_case": None,
                "split_fee_applied": None,
                "supplier_qualifying_units_per_case": None,
                "whole_gift_to_single_variant_mapping_allowed": False,
                "may_influence_normalized_price_projection": False,
            }
        ]
        _validate_v5_gift_chains(links, gifts, alcohol)

        swapped = deepcopy(gifts)
        swapped[0]["relationship_id"], swapped[1]["relationship_id"] = (
            swapped[1]["relationship_id"],
            swapped[0]["relationship_id"],
        )
        with self.assertRaisesRegex(ReviewPackageError, "another occurrence"):
            _validate_v5_gift_chains(links, swapped, alcohol)

        authorized = deepcopy(gifts)
        authorized[0]["whole_offer_to_single_variant_allowed"] = True
        with self.assertRaisesRegex(ReviewPackageError, "cannot map"):
            _validate_v5_gift_chains(links, authorized, alcohol)

        detached = deepcopy(alcohol)
        detached[0]["challenge_review_id"] = "challenge-2"
        with self.assertRaisesRegex(ReviewPackageError, "does not bind"):
            _validate_v5_gift_chains(links, gifts, detached)

        drifted_components = deepcopy(alcohol)
        drifted_components[0]["components"][1]["quantity_per_gift_candidate"] = 3
        with self.assertRaisesRegex(ReviewPackageError, "arithmetic differs"):
            _validate_v5_gift_chains(links, gifts, drifted_components)
        projected_gift = _reviewable_sidecar_record(
            "alcohol_gift_components_v5", alcohol[0]
        )
        self.assertEqual(projected_gift, alcohol[0])
        self.assertIn("published_ambiguities", projected_gift)
        self.assertIn("remaining_evidence_needed", projected_gift)

        fixed_component = {
            "component_quantity_raw": 2,
            "provisional_component_shopify_units": 2,
            "provisional_component_conversion_basis": (
                "Printed component quantity times1 individually sellable standard bottle; "
                "pending component-page check, not supplier BT qualification"
            ),
        }
        self.assertEqual(
            _validate_fixed_combo_provisional_units(fixed_component),
            "quantity_preserved",
        )
        fixed_component["provisional_component_shopify_units"] = 3
        with self.assertRaisesRegex(ReviewPackageError, "conversion differs"):
            _validate_fixed_combo_provisional_units(fixed_component)

    def test_streamed_table_summary_is_truthful_and_tuple_access_fails_closed(self):
        table = _table("large", (), path="tables/large.jsonl", declared=2)
        package = ReviewPackage(
            source="/private/path",
            package_kind=real.A1_PACKAGE_KIND,
            snapshot_id="fixture",
            status="REVIEW_ONLY_VALIDATED",
            label=real.A1_REVIEW_LABEL,
            file_count=1,
            verified_file_count=1,
            manifest_sha256="a" * 64,
            tables={"large": table},
            cohorts={},
            issues=(),
            unavailable_evidence=(),
            metadata={
                "readiness": {
                    "raw_file_integrity": "PASS",
                    "effective_snapshot_restoration": "PASS",
                },
                "integrity_checks": {
                    "logical_tables": 120,
                    "logical_rows": 746_048,
                },
            },
            table_loaders={"large": lambda: iter(({"id": "1"}, {"id": "2"}))},
        )
        summary = package.summary()
        self.assertEqual(summary["tables"]["large"]["row_count"], 2)
        self.assertEqual(summary["source"], "v5:fixture")
        self.assertEqual(summary["label"], real.A1_REVIEW_LABEL)
        self.assertEqual(summary["readiness"]["raw_file_integrity"], "PASS")
        self.assertEqual(
            summary["readiness"]["effective_snapshot_restoration"], "PASS"
        )
        self.assertEqual(summary["integrity_checks"]["logical_tables"], 120)
        self.assertEqual(
            _v5_review_family_summary(package, ())["label"],
            real.A1_REVIEW_LABEL,
        )
        with self.assertRaisesRegex(ReviewPackageError, "TABLE_NOT_MATERIALIZED"):
            package.table("large")
        self.assertEqual(tuple(package.iter_table("large")), ({"id": "1"}, {"id": "2"}))

        generic = ReviewPackage(
            source="fixture",
            package_kind="FIXTURE",
            snapshot_id="fixture",
            status="REVIEW_ONLY_VALIDATED",
            label="FIXTURE REVIEW LABEL",
            file_count=0,
            verified_file_count=0,
            manifest_sha256="b" * 64,
            tables={},
            cohorts={},
            issues=(),
            unavailable_evidence=(),
        )
        self.assertEqual(report_document(generic)["label"], "FIXTURE REVIEW LABEL")

    def test_a1_html_is_inert_searchable_and_allows_only_bound_local_pdf_links(self):
        hostile = '</script><script src="https://evil.example/x.js">alert(1)</script>'
        batch = {
            "variant_id": "1001",
            "search_text": "1001 fixture supplier 0012-a",
            "catalog_review": {
                "captured_product_title": hostile,
                "captured_variant_options": "750ML",
            },
            "offers": [
                {
                    "variant_id": "1001",
                    "vendor": "Fixture Supplier",
                    "source_occurrence_id": "offer-1",
                    "supplier_code": "0012-A",
                    "supplier_description": hostile,
                    "candidate_disposition": "SEARCH_LEAD_ONLY",
                    "source": {
                        "file": "Fixture Book.pdf",
                        "page": 2,
                        "availability": "VERIFIED_ORIGINAL_BYTES",
                        "local_pdf_href": "../original_sources/Fixture%20Book.pdf#page=2",
                    },
                    "price_ladder": [],
                    "blockers": {},
                    "authority": {
                        "mapping_approved": False,
                        "price_approved": False,
                        "import_ready": False,
                    },
                }
            ],
        }
        report = {
            "label": real.A1_REVIEW_LABEL,
            "status": "REVIEW_ONLY_VALIDATED",
            "package": {"package_kind": real.A1_PACKAGE_KIND, "readiness": {}},
            "offer_family": {"summary": {}, "invariants": {}},
            "review_batches": [batch],
            "operational_effects": {},
        }
        rendered = render_review_html(report)
        self.assertIn("connect-src 'none'", rendered)
        self.assertIn("Full catalog search and evidence drill-down", rendered)
        self.assertIn("textContent", rendered)
        self.assertIn("decodeURIComponent", rendered)
        self.assertIn("encodeURIComponent(filename)", rendered)
        self.assertNotIn("innerHTML", rendered)
        self.assertIn("../original_sources/Fixture%20Book.pdf#page=2", rendered)
        self.assertNotIn(hostile, rendered)
        self.assertNotIn('<script src="https://evil.example', rendered)
        self.assertNotIn('href="https://evil.example', rendered)
        self.assertIn("\\u003c/script\\u003e", _html_embedded_json(hostile))

    def test_pdf_links_are_bound_only_to_the_verified_sibling_layout(self):
        package = SimpleNamespace(package_kind=real.A1_PACKAGE_KIND)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = root / "original_sources"
            evidence.mkdir()
            self.assertEqual(
                _local_pdf_href_prefix(
                    package,
                    output=root / "review",
                    external_evidence_root=evidence,
                ),
                "../original_sources/",
            )
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            self.assertIsNone(
                _local_pdf_href_prefix(
                    package,
                    output=root / "review",
                    external_evidence_root=elsewhere,
                )
            )
            self.assertIsNone(
                _local_pdf_href_prefix(
                    package,
                    output=None,
                    external_evidence_root=evidence,
                )
            )


if __name__ == "__main__":
    unittest.main()
