"""Pure tests for the bounded offline supplier review-package reader."""

from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from procurement_os.price_book import PRICE_BOOK_HEADERS
from procurement_os.supplier_review_package import (
    PACKAGE_FORMAT,
    REVIEW_LABEL,
    _V4_PATCH_CONTRACTS,
    _V4_TABLE_CONTRACTS,
    _validate_normalized_review_row,
    _validate_v4_dictionary,
    _validate_workbook_patch_evidence,
    _validate_workbook_validation,
    ReviewLimits,
    ReviewPackageError,
    canonical_jsonl_sha256,
    canonical_record_sha256,
    read_review_package,
    replay_patch_table,
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _write_v1_package(
    root: Path,
    *,
    payloads: dict[str, bytes],
    tables: list[dict[str, object]],
    joins: list[dict[str, object]] | None = None,
) -> None:
    files = []
    for name, payload in payloads.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        files.append(
            {
                "path": name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = {
        "format": PACKAGE_FORMAT,
        "snapshot_id": "synthetic-2026-09",
        "authority": "REVIEW_ONLY",
        "files": files,
        "tables": tables,
        "joins": joins or [],
        "cohorts": {"original": 2000, "current": 2003},
    }
    (root / "REVIEW_PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )


class SupplierReviewPackageTests(unittest.TestCase):
    def test_canonical_hashes_preserve_scalar_types_and_decimal_scale(self):
        record = {
            "money": Decimal("12.3400"),
            "code": "0012A",
            "empty": "",
            "null": None,
            "zero": 0,
            "false": False,
        }
        self.assertEqual(
            canonical_record_sha256(record),
            "94272759b58d0be556d53ff9bfaaba5ee9b683ac41e67c20de2f8e49cc94cce9",
        )
        self.assertEqual(
            canonical_jsonl_sha256([record]),
            "74f4cb625c8ae4c5a4c89695c2672b95610f89ebc1e6f36c186119ae33cb953b",
        )

    def test_v1_jsonl_validates_hash_rows_join_and_preserves_unknown_fields(self):
        variants = b'{"variant_id":"100","title":"Fixture"}\n'
        offers = (
            b'{"variant_id":"100","vendor":"Empire","offer_id":"std",'
            b'"unit_price":10.2500,"future_evidence":"preserved"}\n'
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            _write_v1_package(
                root,
                payloads={"tables/variants.jsonl": variants, "tables/offers.jsonl": offers},
                tables=[
                    {
                        "name": "variants",
                        "path": "tables/variants.jsonl",
                        "format": "jsonl",
                        "rows": 1,
                        "required_fields": ["variant_id", "title"],
                        "known_fields": ["variant_id", "title"],
                    },
                    {
                        "name": "offers",
                        "path": "tables/offers.jsonl",
                        "format": "jsonl",
                        "rows": 1,
                        "required_fields": ["variant_id", "vendor", "offer_id"],
                        "known_fields": ["variant_id", "vendor", "offer_id", "unit_price"],
                    },
                ],
                joins=[
                    {
                        "from_table": "offers",
                        "from_fields": ["variant_id"],
                        "to_table": "variants",
                        "to_fields": ["variant_id"],
                        "allow_null": False,
                    }
                ],
            )
            package = read_review_package(root)

        self.assertEqual(package.status, "PASS")
        self.assertEqual(package.label, REVIEW_LABEL)
        self.assertEqual(package.cohorts, {"original": 2000, "current": 2003})
        self.assertEqual(package.table("offers")[0]["unit_price"], Decimal("10.2500"))
        self.assertEqual(package.tables["offers"].unknown_fields, ("future_evidence",))
        self.assertIn("UNKNOWN_FIELDS", {issue.code for issue in package.issues})

    def test_declared_hash_row_count_and_join_fail_closed(self):
        cases = ("hash", "rows", "join")
        for case in cases:
            with self.subTest(case=case), TemporaryDirectory() as temp:
                root = Path(temp)
                _write_v1_package(
                    root,
                    payloads={
                        "variants.jsonl": b'{"variant_id":"100"}\n',
                        "offers.jsonl": b'{"variant_id":"999","offer_id":"one"}\n',
                    },
                    tables=[
                        {"name": "variants", "path": "variants.jsonl", "format": "jsonl", "rows": 1},
                        {
                            "name": "offers",
                            "path": "offers.jsonl",
                            "format": "jsonl",
                            "rows": 2 if case == "rows" else 1,
                        },
                    ],
                    joins=[] if case != "join" else [
                        {
                            "from_table": "offers",
                            "from_fields": ["variant_id"],
                            "to_table": "variants",
                            "to_fields": ["variant_id"],
                            "allow_null": False,
                        }
                    ],
                )
                if case == "hash":
                    manifest_path = root / "REVIEW_PACKAGE_MANIFEST.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["files"][0]["sha256"] = "0" * 64
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(ReviewPackageError):
                    read_review_package(root)

    def test_typed_csv_keeps_absent_null_empty_zero_false_and_codes_distinct(self):
        fields = [
            "code",
            "money",
            "absent",
            "null_value",
            "empty",
            "zero",
            "false_value",
            "protected",
            "nested",
        ]
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(fields)
        writer.writerow(
            ["0012A", "12.3400", "", "", "", "0", "false", "'=SUM(A1:A2)", '{"k":"v"}']
        )
        csv_payload = output.getvalue().encode()
        sidecar = _json_bytes(
            {
                "row_number_1based": 1,
                "types": ["s", "f", "m", "n", "s", "i", "b", "q", "j"],
            }
        )
        with TemporaryDirectory() as temp:
            root = Path(temp)
            _write_v1_package(
                root,
                payloads={"typed.csv": csv_payload, "typed.types.jsonl": sidecar},
                tables=[
                    {
                        "name": "typed",
                        "path": "typed.csv",
                        "format": "csv",
                        "rows": 1,
                        "fields": fields,
                        "type_sidecar": "typed.types.jsonl",
                    }
                ],
            )
            table = read_review_package(root).tables["typed"]

        row = table.rows[0]
        self.assertNotIn("absent", row)
        self.assertIsNone(row["null_value"])
        self.assertEqual(row["empty"], "")
        self.assertEqual(row["zero"], 0)
        self.assertIs(row["false_value"], False)
        self.assertEqual(row["code"], "0012A")
        self.assertEqual(row["money"], Decimal("12.3400"))
        self.assertEqual(row["protected"], "=SUM(A1:A2)")
        self.assertEqual(row["nested"], {"k": "v"})
        self.assertEqual(
            table.canonical_jsonl_sha256,
            "62ebb3cefb1d7dcdc7d61d90d75ffcc9fa214149fa0154579e76df73a24396a8",
        )

    def test_absent_type_cannot_discard_a_nonempty_cell_and_huge_exponents_are_bounded(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            csv_payload = b"value\ncontradiction\n"
            sidecar = _json_bytes({"row_number_1based": 1, "types": ["m"]})
            _write_v1_package(
                root,
                payloads={"typed.csv": csv_payload, "typed.types.jsonl": sidecar},
                tables=[
                    {
                        "name": "typed",
                        "path": "typed.csv",
                        "format": "csv",
                        "rows": 1,
                        "fields": ["value"],
                        "type_sidecar": "typed.types.jsonl",
                    }
                ],
            )
            with self.assertRaisesRegex(ReviewPackageError, "TYPE_SIDECAR_MISMATCH"):
                read_review_package(root)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            _write_v1_package(
                root,
                payloads={"numbers.jsonl": b'{"value":1e999999999}\n'},
                tables=[{"name": "numbers", "path": "numbers.jsonl", "format": "jsonl", "rows": 1}],
            )
            with self.assertRaisesRegex(ReviewPackageError, "NUMBER_TOO_LARGE"):
                read_review_package(root)

        for payload in (b'{"value":NaN}\n', b'{"value":1,"value":2}\n'):
            with self.subTest(payload=payload), TemporaryDirectory() as temp:
                root = Path(temp)
                _write_v1_package(
                    root,
                    payloads={"strict.jsonl": payload},
                    tables=[{"name": "strict", "path": "strict.jsonl", "format": "jsonl", "rows": 1}],
                )
                with self.assertRaisesRegex(ReviewPackageError, "INVALID_JSON"):
                    read_review_package(root)

    def test_patch_replay_checks_baseline_allowed_fields_and_idempotency(self):
        base = ({"id": "A", "price": Decimal("10.00")},)
        patch = {
            "operation": "replace_fields",
            "base_row_number_1based": 1,
            "stable_key": {"id": "A"},
            "before_record_sha256": "99e3abc316e09b2da9201cadf8c3c47ae4cf01f62bcd543268355f32f7dba7b2",
            "after_record_sha256": "61f97e45b2ef76cc612d677a0e29b78201cb23f147fb54793363670b7f3b3031",
            "changes": [
                {
                    "field": "price",
                    "before_present": True,
                    "before": Decimal("10.00"),
                    "after_present": True,
                    "after": Decimal("11.25"),
                }
            ],
            "append_record": None,
        }
        replay = replay_patch_table(
            base,
            [patch],
            allowed_fields={"price"},
            expected_rows=1,
            expected_sha256="d3795d28ee1c185d28aeaeedd27954a8ffa61f0400bb1481fe7d29649546086f",
        )
        self.assertEqual(replay.rows, ({"id": "A", "price": Decimal("11.25")},))
        self.assertEqual((replay.applied, replay.already_applied), (1, 0))

        second = replay_patch_table(replay.rows, [patch], allowed_fields={"price"})
        self.assertEqual(second.rows, replay.rows)
        self.assertEqual((second.applied, second.already_applied), (0, 1))
        malformed_replay = {
            **patch,
            "changes": [{**patch["changes"][0], "field": "id", "before_present": "yes"}],
        }
        with self.assertRaises(ReviewPackageError):
            replay_patch_table(replay.rows, [malformed_replay], allowed_fields={"price"})

        bad_base = ({"id": "A", "price": Decimal("10.01")},)
        with self.assertRaises(ReviewPackageError):
            replay_patch_table(bad_base, [patch], allowed_fields={"price"})
        with self.assertRaises(ReviewPackageError):
            replay_patch_table(base, [patch, patch], allowed_fields={"price"})
        forbidden = {**patch, "changes": [{**patch["changes"][0], "field": "id"}]}
        with self.assertRaises(ReviewPackageError):
            replay_patch_table(base, [forbidden], allowed_fields={"price"})
        deletion = {
            **patch,
            "changes": [{**patch["changes"][0], "after_present": False, "after": None}],
        }
        with self.assertRaises(ReviewPackageError):
            replay_patch_table(base, [deletion], allowed_fields={"price"})

    def test_patch_policy_is_independent_and_keys_cannot_be_empty_or_self_authorized(self):
        base = ({"id": "A", "value": "before", "source_id": "SRC-1"},)
        after = {"id": "A", "value": "after", "source_id": "SRC-1"}
        patch = {
            "operation": "replace_fields",
            "base_row_number_1based": 1,
            "stable_key": {"id": "A"},
            "before_record_sha256": canonical_record_sha256(base[0]),
            "after_record_sha256": canonical_record_sha256(after),
            "changes": [
                {
                    "field": "value",
                    "before_present": True,
                    "before": "before",
                    "after_present": True,
                    "after": "after",
                }
            ],
            "append_record": None,
        }
        with self.assertRaisesRegex(ReviewPackageError, "PATCH_POLICY_REQUIRED"):
            replay_patch_table(base, [patch])
        with self.assertRaisesRegex(ReviewPackageError, "EMPTY_PATCH_KEY"):
            replay_patch_table(base, [{**patch, "stable_key": {}}], allowed_fields={"value"})
        with self.assertRaisesRegex(ReviewPackageError, "PATCH_KEY_FIELDS_MISMATCH"):
            replay_patch_table(
                base,
                [patch],
                allowed_fields={"value"},
                expected_stable_key_fields=("source_id",),
            )
        identity_change = {
            **patch,
            "changes": [{**patch["changes"][0], "field": "source_id", "after": "SRC-2"}],
        }
        with self.assertRaisesRegex(ReviewPackageError, "PATCH_IDENTITY_FIELD_MUTATION"):
            replay_patch_table(
                base,
                [identity_change],
                allowed_fields={"source_id"},
                immutable_fields={"source_id"},
            )

    def test_append_identity_is_unique_and_same_key_different_facts_fail(self):
        first_record = {"id": "A", "value": "one"}
        second_record = {"id": "A", "value": "two"}

        def append_patch(record: dict[str, str]) -> dict[str, object]:
            return {
                "operation": "append_record",
                "base_row_number_1based": None,
                "stable_key": {"id": record["id"]},
                "before_record_sha256": None,
                "after_record_sha256": canonical_record_sha256(record),
                "changes": [
                    {
                        "field": field,
                        "before_present": False,
                        "before": None,
                        "after_present": True,
                        "after": record[field],
                    }
                    for field in ("id", "value")
                ],
                "append_record": record,
            }

        first = replay_patch_table(
            (),
            [append_patch(first_record)],
            append_allowed_fields={"id", "value"},
            required_append_fields={"id", "value"},
            expected_stable_key_fields=("id",),
        )
        self.assertEqual(first.rows, (first_record,))
        self.assertEqual((first.applied, first.already_applied), (1, 0))
        replay = replay_patch_table(
            first.rows,
            [append_patch(first_record)],
            append_allowed_fields={"id", "value"},
            required_append_fields={"id", "value"},
            expected_stable_key_fields=("id",),
        )
        self.assertEqual((replay.applied, replay.already_applied), (0, 1))
        with self.assertRaisesRegex(ReviewPackageError, "PATCH_APPEND_KEY_CONFLICT"):
            replay_patch_table(
                first.rows,
                [append_patch(second_record)],
                append_allowed_fields={"id", "value"},
                required_append_fields={"id", "value"},
                expected_stable_key_fields=("id",),
            )

    def test_external_and_postcondition_patch_identity_modes_are_exact_and_idempotent(self):
        normalized_base = ({"unit_price": Decimal("8.00")},)
        normalized_after = {**normalized_base[0], "unit_price": Decimal("7.5000")}
        normalized_patch = {
            "operation": "replace_fields",
            "base_row_number_1based": 1,
            "stable_key": {},
            "before_record_sha256": canonical_record_sha256(normalized_base[0]),
            "after_record_sha256": canonical_record_sha256(normalized_after),
            "changes": [{
                "field": "unit_price",
                "before_present": True,
                "before": Decimal("8.00"),
                "after_present": True,
                "after": Decimal("7.5000"),
            }],
            "append_record": None,
        }
        external = {1: {"source_tier_id": "T-1", "projection_row_id": "P-1"}}
        applied = replay_patch_table(
            normalized_base,
            [normalized_patch],
            allowed_fields={"unit_price"},
            key_mode="EXTERNAL_PROOF",
            external_keys_by_row=external,
        )
        self.assertEqual(applied.rows[0]["unit_price"], Decimal("7.5000"))
        second = replay_patch_table(
            applied.rows,
            [normalized_patch],
            allowed_fields={"unit_price"},
            key_mode="EXTERNAL_PROOF",
            external_keys_by_row=external,
        )
        self.assertEqual((second.applied, second.already_applied), (0, 1))
        with self.assertRaisesRegex(ReviewPackageError, "MISSING_EXTERNAL_PATCH_KEY"):
            replay_patch_table(
                normalized_base,
                [normalized_patch],
                allowed_fields={"unit_price"},
                key_mode="EXTERNAL_PROOF",
            )
        alternate_proof = replay_patch_table(
            normalized_base,
            [normalized_patch],
            allowed_fields={"unit_price"},
            key_mode="EXTERNAL_PROOF",
            external_keys_by_row={1: {"source_tier_id": "T-2", "projection_row_id": "P-2"}},
        )
        self.assertEqual(alternate_proof.rows, applied.rows)

        projection_base = ({"variant_id": "V-1", "offer_id": "O-1"},)
        projection_after = {**projection_base[0], "owner_decision_id": "D-1"}
        projection_patch = {
            "operation": "replace_fields",
            "base_row_number_1based": 1,
            "stable_key": {"owner_decision_id": "D-1"},
            "before_record_sha256": canonical_record_sha256(projection_base[0]),
            "after_record_sha256": canonical_record_sha256(projection_after),
            "changes": [{
                "field": "owner_decision_id",
                "before_present": False,
                "before": None,
                "after_present": True,
                "after": "D-1",
            }],
            "append_record": None,
        }
        projected = replay_patch_table(
            projection_base,
            [projection_patch],
            allowed_fields={"owner_decision_id"},
            expected_stable_key_fields=("owner_decision_id",),
            key_mode="POSTCONDITION_WITH_EXTERNAL_BASE",
            external_keys_by_row={1: {"variant_id": "V-1", "offer_id": "O-1"}},
        )
        self.assertEqual(projected.rows, (projection_after,))

    def test_v4_contract_counts_and_special_key_modes_are_code_owned_literals(self):
        self.assertGreaterEqual(ReviewLimits().max_total_expanded_bytes, 2_557_725_798)
        self.assertEqual(
            {name: contract.patch_records for name, contract in _V4_PATCH_CONTRACTS.items()},
            {
                "candidate_matches": 14,
                "candidate_projection_eligibility": 14,
                "catalog_coverage": 251,
                "coverage_change_ledger": 4,
                "normalized_price_contract_links": 5,
                "normalized_price_contract_review": 5,
                "offer_pair_reviews": 5,
                "owner_decisions": 3,
                "owner_question_batch": 3,
                "rejected_alternatives": 2,
                "unresolved_dependencies": 745,
                "variant_review_1720": 251,
            },
        )
        self.assertEqual(
            _V4_PATCH_CONTRACTS["normalized_price_contract_review"].key_mode,
            "EXTERNAL_PROOF",
        )
        self.assertEqual(
            _V4_PATCH_CONTRACTS["normalized_price_contract_review"].stable_key_fields,
            (),
        )
        self.assertEqual(
            _V4_PATCH_CONTRACTS["candidate_projection_eligibility"].key_mode,
            "POSTCONDITION_WITH_EXTERNAL_BASE",
        )
        self.assertEqual(
            {name: contract.expected_rows for name, contract in _V4_TABLE_CONTRACTS.items()},
            {
                "affected_variants": 4,
                "affected_candidates": 14,
                "dependency_groups": 106,
                "normalized_price_contract_links_delta": 5,
                "normalized_price_contract_review_delta": 5,
                "owner_decisions_added": 3,
                "owner_question_batch_effective": 3,
                "preserved_target_identity_evidence": 4,
            },
        )

    def test_v4_dictionary_and_workbook_controls_are_independently_recomputed(self):
        normalized = {field_name: None for field_name in PRICE_BOOK_HEADERS}
        normalized.update(
            {
                "batch_ref": "SYNTHETIC-REVIEW",
                "vendor_name": "Synthetic Vendor",
                "supplier_sku": "0012-A",
                "supplier_description": "Synthetic item",
                "canonical_variant_id": "1001",
                "package_type": "STANDARD",
                "size_text": "750 mL",
                "shopify_units_per_case": 12,
                "level_type": "BASE",
                "case_price": Decimal("120.0000"),
                "unit_price": Decimal("10.0000"),
                "source_file": "synthetic.pdf",
                "source_page": 1,
                "source_evidence": "synthetic test evidence",
                "extraction_confidence": "UNAPPROVED_CANDIDATE",
                "review_note": "REVIEW ONLY / NOT_IMPORT_READY",
            }
        )
        _validate_normalized_review_row(normalized, row_number=1)
        with self.assertRaisesRegex(ReviewPackageError, "NORMALIZED_TYPE_MISMATCH"):
            _validate_normalized_review_row({**normalized, "supplier_sku": 12}, row_number=1)
        with self.assertRaisesRegex(ReviewPackageError, "NORMALIZED_TYPE_MISMATCH"):
            _validate_normalized_review_row({**normalized, "unit_price": "10.0000"}, row_number=1)

        canonical_fields = ", ".join(
            (
                "batch_ref",
                "target_price_state",
                "effective_from",
                "effective_through",
                "vendor_name",
                "supplier_sku",
                "supplier_description",
                "canonical_variant_id",
                "package_type",
                "size_text",
                "raw_pack",
                "shopify_units_per_case",
                "qualifying_units_per_case",
                "assortment_scope",
                "assortment_group",
                "assortable",
                "assortment_evidence",
                "level_type",
                "break_quantity",
                "break_unit",
                "case_price",
                "unit_price",
                "source_file",
                "source_page",
                "source_evidence",
                "extraction_confidence",
                "review_note",
            )
        )
        dictionary = (
            "# Buffalo V4.1 clarification checkpoint\n"
            "## Table dictionary and precedence\n"
            "companion_record_patches.jsonl\n"
            "normalized_price_contract_review_delta_csv_types.jsonl\n"
            f"{canonical_fields}\n"
            "All existing21 documented contract gaps\n"
            "No candidate is approved or verified CURRENT.\n"
        ).encode()
        _validate_v4_dictionary(dictionary)
        with self.assertRaisesRegex(ReviewPackageError, "INVALID_DICTIONARY"):
            _validate_v4_dictionary(dictionary.replace(b"existing21", b"existing20"))

        workbook_patch = {
            "workbook": "fixture.xlsx",
            "sheet": "Review",
            "excel_row": 7,
            "operation": "replace_row",
            "before": ["old"],
            "after": ["new"],
        }
        _validate_workbook_patch_evidence(
            [workbook_patch], {"fixture.xlsx | Review": 1}
        )
        with self.assertRaisesRegex(
            ReviewPackageError, "WORKBOOK_PATCH_COUNT_MISMATCH"
        ):
            _validate_workbook_patch_evidence(
                [workbook_patch], {"fixture.xlsx | Review": 2}
            )
        with self.assertRaisesRegex(
            ReviewPackageError, "DUPLICATE_WORKBOOK_PATCH"
        ):
            _validate_workbook_patch_evidence(
                [workbook_patch, workbook_patch], {"fixture.xlsx | Review": 2}
            )

        sheet_counts = {
            "Catalog": 2_000,
            "Matches": 14_727,
            "Dependencies": 745,
            "Owner Decisions": 20,
            "Owner Questions": 3,
            "Contract Field Map": 27,
            "Contract Gaps": 21,
            "Evidence Needed Groups": 106,
        }
        workbook_validation = {
            "status": "PASS",
            "issue_count": 0,
            "issues": [],
            "xlsx_bytes": 123,
            "xlsx_sha256": "3" * 64,
            "payload_sha256": "4" * 64,
            "counts": {
                "numeric": 1,
                "text": 1,
                "boolean": 1,
                "dates": 1,
                "formulas": 4_000,
                "formula_caches": 4_000,
                "blank": 0,
            },
            "sheets": [
                {"sheet": name, "data_rows": count}
                for name, count in sheet_counts.items()
            ],
        }
        _validate_workbook_validation(workbook_validation)
        workbook_validation["sheets"][-2]["data_rows"] = 20
        with self.assertRaisesRegex(
            ReviewPackageError, "WORKBOOK_VALIDATION_MISMATCH"
        ):
            _validate_workbook_validation(workbook_validation)

    def test_malformed_oversized_and_unsafe_packages_are_rejected(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "REVIEW_PACKAGE_MANIFEST.json").write_bytes(b"{")
            with self.assertRaises(ReviewPackageError):
                read_review_package(root)

        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "large.bin").write_bytes(b"12345")
            with self.assertRaisesRegex(ReviewPackageError, "ENTRY_TOO_LARGE"):
                read_review_package(root, limits=ReviewLimits(max_entry_bytes=4))

        unsafe_members = (("../escape.json", b"{}"), ("/absolute.json", b"{}"))
        for name, payload in unsafe_members:
            with self.subTest(name=name), TemporaryDirectory() as temp:
                archive = Path(temp) / "bad.zip"
                with ZipFile(archive, "w") as bundle:
                    bundle.writestr(name, payload)
                with self.assertRaisesRegex(ReviewPackageError, "UNSAFE_PATH"):
                    read_review_package(archive)

        with TemporaryDirectory() as temp:
            archive = Path(temp) / "bomb.zip"
            with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
                bundle.writestr("repeated.bin", b"0" * 10_000)
            with self.assertRaisesRegex(ReviewPackageError, "ARCHIVE_BOMB"):
                read_review_package(archive, limits=ReviewLimits(max_compression_ratio=2))

    def test_zip_case_collisions_are_rejected_without_extraction(self):
        with TemporaryDirectory() as temp:
            archive = Path(temp) / "collision.zip"
            with ZipFile(archive, "w") as bundle:
                bundle.writestr("Data/Rows.jsonl", b"{}\n")
                bundle.writestr("data/rows.jsonl", b"{}\n")
            with self.assertRaisesRegex(ReviewPackageError, "PATH_COLLISION"):
                read_review_package(archive)

    def test_join_keys_must_exist_and_null_targets_never_match(self):
        for variants, offers in ((b'{"title":"missing"}\n', b'{"offer_id":"one"}\n'), (b'{"variant_id":null}\n', b'{"variant_id":null}\n')):
            with self.subTest(variants=variants), TemporaryDirectory() as temp:
                root = Path(temp)
                _write_v1_package(
                    root,
                    payloads={"variants.jsonl": variants, "offers.jsonl": offers},
                    tables=[
                        {"name": "variants", "path": "variants.jsonl", "format": "jsonl", "rows": 1},
                        {"name": "offers", "path": "offers.jsonl", "format": "jsonl", "rows": 1},
                    ],
                    joins=[{"from_table":"offers","from_fields":["variant_id"],"to_table":"variants","to_fields":["variant_id"],"allow_null":False}],
                )
                with self.assertRaises(ReviewPackageError):
                    read_review_package(root)

    def test_empty_v4_1_hash_manifest_is_not_a_valid_partial_package(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "PACKAGE_FILE_HASHES.json").write_text("[]\n", encoding="utf-8")
            with self.assertRaisesRegex(ReviewPackageError, "INCOMPLETE_DELTA_PACKAGE"):
                read_review_package(root)


if __name__ == "__main__":
    unittest.main()
