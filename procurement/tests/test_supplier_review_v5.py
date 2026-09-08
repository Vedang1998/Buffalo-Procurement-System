"""Pure known-answer tests for V5 and portable review-only packages."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import stat
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from procurement_os.supplier_mapping_review import compare_review_packages
from procurement_os.supplier_review_package import (
    REVIEW_LABEL,
    ReviewLimits,
    ReviewPackageError,
    _canonical_json,
    canonical_jsonl_sha256,
    read_review_package,
    replay_patch_table,
)
from procurement_os.supplier_review_v5 import (
    PORTABLE_COMPARISON_CONTRACT,
    PORTABLE_FORMAT,
    PORTABLE_IDENTITY_CONTRACT,
    PORTABLE_LINEAGE_FAMILY_SHA256,
    PORTABLE_ROOT,
    PORTABLE_SEAL,
    PORTABLE_SEAL_FORMAT,
    PORTABLE_SYNTHETIC_REVISION,
    _PORTABLE_ENCODING,
    _PORTABLE_SYNTHETIC_LINEAGE_ANCHORS,
)


def _json_bytes(value: object) -> bytes:
    return _canonical_json(value) + b"\n"


def _jsonl(rows: list[dict[str, object]]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _gap_rows() -> list[dict[str, object]]:
    return [
        {
            "gap_id": f"G{index:02d}",
            "area": f"Synthetic representation gap {index}",
            "current_handling": "VISIBLE_REVIEW_ONLY",
            "later_route": "SEPARATE_OWNER_REVIEW",
            "authority": "REVIEW_ONLY",
        }
        for index in range(1, 22)
    ]


def _portable_rows(
    *, period: str = "2026-09", pad: int = 0
) -> dict[str, list[dict[str, object]]]:
    source_scope = {
        "source_file": "sources/fixture-september-page-1.pdf",
        "source_page": 1,
        "source_sha256": "1" * 64,
        "source_period": period,
        "territory": "TEST TERRITORY",
        "channel": "TEST BOOK",
    }
    return {
        "variants": [
            {"variant_id": "1001", "product_title": "Fixture One", "catalog_status": "ACTIVE"},
            {"variant_id": "1002", "product_title": "Fixture Two", "catalog_status": "ACTIVE"},
            {"variant_id": "1003", "product_title": "Fixture Three", "catalog_status": "ACTIVE"},
        ],
        "offers": [
            {
                "variant_id": "1001",
                "vendor": "Fixture Supplier",
                "source_occurrence_id": "book:one",
                "supplier_code": "0012A",
                "program_type": "STANDARD",
                "shopify_units_per_case": 6,
                "qualifying_units_per_case": Decimal("6.0"),
                "physical_units_per_case": 12,
                "retail_pack": 2,
                **source_scope,
                "candidate_disposition": "PROPOSED_REVIEW_CANDIDATE",
                "mapping_approved": False,
                "price_approved": False,
                "import_ready": False,
                "rejected_reason": "x" * pad,
            },
            {
                "variant_id": "1002",
                "vendor": "Fixture Supplier",
                "source_occurrence_id": "book:two",
                "supplier_code": "GIFT-2",
                "program_type": "GIFT",
                "shopify_units_per_case": None,
                **source_scope,
                "candidate_disposition": "SEARCH_LEAD_ONLY",
                "mapping_approved": False,
                "price_approved": False,
                "import_ready": False,
            },
            {
                "variant_id": "1003",
                "vendor": "Fixture Supplier",
                "source_occurrence_id": "book:three",
                "supplier_code": "ALT-3",
                "program_type": "ALTERNATE_CASE",
                "shopify_units_per_case": 0,
                **source_scope,
                "candidate_disposition": "REJECTED_ATTRIBUTE_CONFLICT",
                "mapping_approved": False,
                "price_approved": False,
                "import_ready": False,
                "rejected_reason": "y" * pad,
            },
        ],
        "tiers": [
            {
                "source_tier_id": "tier:one:base",
                "variant_id": "1001",
                "vendor": "Fixture Supplier",
                "source_occurrence_id": "book:one",
                "break_unit": "BASE",
                "break_quantity": None,
                "case_price": Decimal("120.00"),
                "unit_price": Decimal("20.0000"),
                "selected_tier": False,
            },
            {
                "source_tier_id": "tier:one:bt12",
                "variant_id": "1001",
                "vendor": "Fixture Supplier",
                "source_occurrence_id": "book:one",
                "break_unit": "BT",
                "break_quantity": 12,
                "case_price": None,
                "unit_price": Decimal("18.50"),
                "selected_tier": False,
            },
        ],
        "representation_gaps": _gap_rows(),
    }


def _part(archive: str, path: str, ordinal: int, first: int, rows: list[dict[str, object]]) -> dict[str, object]:
    payload = _jsonl(rows)
    return {
        "ordinal": ordinal,
        "archive": archive,
        "path": path,
        "first_row_1based": first,
        "row_count": len(rows),
        "bytes": len(payload),
        "raw_sha256": _sha(payload),
    }


def _write_portable(
    root: Path,
    *,
    period: str = "2026-09",
    missing_source: bool = False,
    pad: int = 0,
    mutate_rows=None,
) -> None:
    rows = _portable_rows(period=period, pad=pad)
    if mutate_rows is not None:
        mutate_rows(rows)
    parts = {
        "variants": [_part("shard-a.zip", "tables/variants-001.jsonl", 1, 1, rows["variants"])],
        "offers": [
            _part("shard-a.zip", "tables/offers-001.jsonl", 1, 1, rows["offers"][:2]),
            _part("shard-b.zip", "tables/offers-002.jsonl", 2, 3, rows["offers"][2:]),
        ],
        "tiers": [_part("shard-b.zip", "tables/tiers-001.jsonl", 1, 1, rows["tiers"])],
        "representation_gaps": [
            _part("shard-b.zip", "tables/gaps-001.jsonl", 1, 1, rows["representation_gaps"])
        ],
    }
    evidence = b"TEST DATA - synthetic source page\n"
    shard_members: dict[str, dict[str, bytes]] = {"shard-a.zip": {}, "shard-b.zip": {}}
    for table, table_parts in parts.items():
        offset = 0
        for item in table_parts:
            count = int(item["row_count"])
            shard_members[str(item["archive"])][str(item["path"])] = _jsonl(rows[table][offset : offset + count])
            offset += count
    source_evidence = [
        {
            "logical_path": "sources/fixture-september-page-1.pdf",
            "source_pdf_sha256": "1" * 64,
            "physical_page": 1,
            "evidence_content_sha256": None if missing_source else _sha(evidence),
            "delivery_archive": None if missing_source else "shard-a.zip",
            "delivery_path": None if missing_source else "evidence/page-1.txt",
            "availability": "MISSING" if missing_source else "DELIVERED",
            "source_review_scope": "SYNTHETIC_TEST_ONLY",
        }
    ]
    if not missing_source:
        shard_members["shard-a.zip"]["evidence/page-1.txt"] = evidence
    root.mkdir(parents=True, exist_ok=True)
    for archive, members in shard_members.items():
        with ZipFile(root / archive, "w", ZIP_DEFLATED) as output:
            for name in sorted(members):
                output.writestr(name, members[name])

    table_specs = []
    dictionaries = {
        "variants": ["SYNTHETIC_VARIANTS_V1"],
        "offers": ["SYNTHETIC_OFFERS_V1"],
        "tiers": ["SYNTHETIC_TIERS_V1"],
        "representation_gaps": ["SYNTHETIC_REPRESENTATION_GAPS_V1"],
    }
    for name in ("variants", "offers", "tiers", "representation_gaps"):
        table_specs.append(
            {
                "name": name,
                "field_dictionary_refs": dictionaries[name],
                "encoding": _PORTABLE_ENCODING,
                "row_count": len(rows[name]),
                "canonical_jsonl_sha256": canonical_jsonl_sha256(rows[name]),
                "parts": parts[name],
            }
        )
    lineage = {
        "identity_contract": PORTABLE_IDENTITY_CONTRACT,
        "v4_manifest_refs": [
            dict(row) for row in _PORTABLE_SYNTHETIC_LINEAGE_ANCHORS["v4_manifest_refs"]
        ],
        "v4_1_patch_refs": [
            dict(row) for row in _PORTABLE_SYNTHETIC_LINEAGE_ANCHORS["v4_1_patch_refs"]
        ],
        "v4_1_locator_corrections": dict(
            _PORTABLE_SYNTHETIC_LINEAGE_ANCHORS["v4_1_locator_corrections"]
        ),
        "v5_patch_refs": [
            dict(row) for row in _PORTABLE_SYNTHETIC_LINEAGE_ANCHORS["v5_patch_refs"]
        ],
        "v5_effective_tables": [
            {
                "name": item["name"],
                "row_count": item["row_count"],
                "canonical_jsonl_sha256": item["canonical_jsonl_sha256"],
            }
            for item in table_specs
        ],
    }
    package_root = {
        "format": PORTABLE_FORMAT,
        "source_revision": PORTABLE_SYNTHETIC_REVISION,
        "package_id": f"synthetic-{period}",
        "generated_at_utc": "2026-09-08T12:00:00+00:00",
        "authority": "UNAPPROVED_REVIEW",
        "cohorts": {
            "original_cohort_count": 2,
            "current_census_count": 3,
            "current_original_returned": 2,
            "current_additions": 1,
        },
        "approval_counts": {"mapping_approvals": 0, "price_approvals": 0, "import_ready_rows": 0},
        "lineage": lineage,
        "tables": table_specs,
        "source_evidence": source_evidence,
        "missing_prerequisites": [] if not missing_source else ["SYNTHETIC_SOURCE_PAGE"],
        "external_evidence": [],
        "review_only": True,
        "operational_effects": {
            "database_writes": 0,
            "shopify_calls": 0,
            "mapping_activations": 0,
            "price_activations": 0,
            "po_actions": 0,
        },
        "snapshot_scope": {
            "comparison_contract": PORTABLE_COMPARISON_CONTRACT,
            "identity_contract": PORTABLE_IDENTITY_CONTRACT,
            "lineage_family_sha256": PORTABLE_LINEAGE_FAMILY_SHA256,
            "supplier_scope_kind": "COMPLETE_SUPPLIER_PERIOD_SET",
            "supplier_scope": ["Fixture Supplier"],
            "cohort_scope": "SYNTHETIC_TEST_COHORT",
            "period_semantics": "MONTHLY_SUPPLIER_BOOK_PERIOD",
            "period_id": period,
            "supplier_period_coverage_complete": not missing_source,
            "missing_supplier_periods": [] if not missing_source else [f"Fixture Supplier/{period}"],
            "channels": ["TEST BOOK"],
            "territories": ["TEST TERRITORY"],
            "simulated": True,
        },
    }
    root_bytes = _json_bytes(package_root)
    (root / PORTABLE_ROOT).write_bytes(root_bytes)
    archive_refs = []
    for name in sorted(shard_members):
        payload = (root / name).read_bytes()
        archive_refs.append({"path": name, "bytes": len(payload), "sha256": _sha(payload)})
    seal = {
        "format": PORTABLE_SEAL_FORMAT,
        "label": REVIEW_LABEL,
        "package_id": package_root["package_id"],
        "root": {"path": PORTABLE_ROOT, "bytes": len(root_bytes), "sha256": _sha(root_bytes)},
        "archives": archive_refs,
    }
    (root / PORTABLE_SEAL).write_bytes(_json_bytes(seal))


def _rewrite_root(root: Path, mutate) -> None:
    package_root = json.loads((root / PORTABLE_ROOT).read_text(), parse_float=Decimal)
    mutate(package_root)
    root_bytes = _json_bytes(package_root)
    (root / PORTABLE_ROOT).write_bytes(root_bytes)
    seal = json.loads((root / PORTABLE_SEAL).read_text())
    seal["root"] = {"path": PORTABLE_ROOT, "bytes": len(root_bytes), "sha256": _sha(root_bytes)}
    (root / PORTABLE_SEAL).write_bytes(_json_bytes(seal))


def _reseal_portable(root: Path) -> None:
    root_bytes = (root / PORTABLE_ROOT).read_bytes()
    seal = json.loads((root / PORTABLE_SEAL).read_text())
    seal["root"] = {
        "path": PORTABLE_ROOT,
        "bytes": len(root_bytes),
        "sha256": _sha(root_bytes),
    }
    for archive in seal["archives"]:
        payload = (root / archive["path"]).read_bytes()
        archive["bytes"] = len(payload)
        archive["sha256"] = _sha(payload)
    (root / PORTABLE_SEAL).write_bytes(_json_bytes(seal))


def _rewrite_portable_part(
    root: Path,
    *,
    archive: str,
    path: str,
    payload: bytes,
    symlink: bool = False,
) -> None:
    archive_path = root / archive
    with ZipFile(archive_path) as source:
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
    members[path] = payload
    replacement = archive_path.with_suffix(".replacement")
    with ZipFile(replacement, "w", ZIP_DEFLATED) as output:
        for name in sorted(members):
            if name == path and symlink:
                info = ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                output.writestr(info, members[name])
            else:
                output.writestr(name, members[name])
    replacement.replace(archive_path)
    package_root = json.loads((root / PORTABLE_ROOT).read_text(), parse_float=Decimal)
    for table in package_root["tables"]:
        for part in table["parts"]:
            if part["archive"] == archive and part["path"] == path:
                part["bytes"] = len(payload)
                part["raw_sha256"] = _sha(payload)
    (root / PORTABLE_ROOT).write_bytes(_json_bytes(package_root))
    _reseal_portable(root)


class SupplierReviewV5Tests(unittest.TestCase):
    def test_portable_snapshot_reconstructs_parts_and_preserves_native_presence(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            package = read_review_package(root)

        self.assertEqual(package.status, "STRUCTURED_REPLAY")
        self.assertTrue(package.is_structurally_complete)
        self.assertFalse(package.is_complete)
        self.assertEqual(package.metadata["readiness"]["source_availability"], "PASS")
        self.assertEqual(package.source, "portable:synthetic-2026-09")
        offers = package.table("offers")
        self.assertEqual([row["source_occurrence_id"] for row in offers], ["book:one", "book:two", "book:three"])
        self.assertEqual(offers[0]["supplier_code"], "0012A")
        self.assertIsInstance(offers[0]["shopify_units_per_case"], int)
        self.assertIsInstance(offers[0]["qualifying_units_per_case"], Decimal)
        self.assertIn("shopify_units_per_case", offers[1])
        self.assertIsNone(offers[1]["shopify_units_per_case"])
        self.assertEqual(offers[2]["shopify_units_per_case"], 0)

    def test_missing_source_is_distinct_from_structural_replay(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root, missing_source=True)
            package = read_review_package(root)

        self.assertTrue(package.is_structurally_complete)
        self.assertEqual(package.metadata["readiness"]["source_availability"], "SOURCE_EVIDENCE_REQUIRED")
        self.assertIn("SOURCE_EVIDENCE:sources/fixture-september-page-1.pdf", package.unavailable_evidence)
        self.assertIn("SUPPLIER_PERIOD:Fixture Supplier/2026-09", package.unavailable_evidence)

    def test_portable_summary_is_cross_workspace_deterministic(self):
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            left = Path(first) / "one"
            right = Path(second) / "two"
            _write_portable(left)
            shutil.copytree(left, right)
            a = read_review_package(left)
            b = read_review_package(right)
        self.assertEqual(_json_bytes(a.summary()), _json_bytes(b.summary()))
        self.assertNotIn(str(left), a.source)

    def test_portable_rejects_mixed_roots_and_root_inside_zip(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            (root / "REVIEW_PACKAGE_MANIFEST.json").write_text("{}")
            with self.assertRaisesRegex(ReviewPackageError, "AMBIGUOUS_MANIFEST"):
                read_review_package(root)
        with TemporaryDirectory() as temp:
            archive = Path(temp) / "portable.zip"
            with ZipFile(archive, "w") as output:
                output.writestr(PORTABLE_ROOT, "{}")
            with self.assertRaisesRegex(ReviewPackageError, "PORTABLE_ROOT_MUST_BE_DIRECTORY"):
                read_review_package(archive)

    def test_portable_rejects_unknown_revision_authority_and_census_drift(self):
        mutations = (
            lambda root: root.__setitem__("source_revision", "ARBITRARY_V5"),
            lambda root: root["approval_counts"].__setitem__("mapping_approvals", 1),
            lambda root: root["approval_counts"].__setitem__("mapping_approvals", False),
            lambda root: root["operational_effects"].__setitem__("database_writes", False),
            lambda root: root["cohorts"].__setitem__("current_census_count", 4),
            lambda root: root["tables"][0].__setitem__(
                "field_dictionary_refs", "SYNTHETIC_VARIANTS_V1"
            ),
            lambda root: root["tables"][0]["parts"][0].__setitem__("ordinal", True),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                root = Path(temp) / "portable"
                _write_portable(root)
                _rewrite_root(root, mutation)
                with self.assertRaises(ReviewPackageError):
                    read_review_package(root)

    def test_portable_lineage_references_are_bound_to_the_supported_revision(self):
        mutations = (
            lambda root: root["lineage"]["v4_manifest_refs"][0].update(
                {"bytes": 987_654_321, "sha256": "9" * 64}
            ),
            lambda root: root["lineage"]["v4_1_patch_refs"][0].__setitem__(
                "sha256", "8" * 64
            ),
            lambda root: root["lineage"]["v4_1_locator_corrections"].__setitem__(
                "row_count", 2
            ),
            lambda root: root["lineage"]["v5_patch_refs"][0].__setitem__(
                "path", "synthetic/substitute-v5.jsonl"
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                root = Path(temp) / "portable"
                _write_portable(root)
                _rewrite_root(root, mutation)
                with self.assertRaisesRegex(ReviewPackageError, "LINEAGE_MISMATCH"):
                    read_review_package(root)

    def test_portable_scope_and_source_coverage_are_derived_from_offer_rows(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            _rewrite_root(root, lambda value: value["source_evidence"].clear())
            with self.assertRaisesRegex(ReviewPackageError, "SOURCE_EVIDENCE_MISMATCH"):
                read_review_package(root)

        root_mutations = (
            lambda value: value["snapshot_scope"].__setitem__(
                "supplier_scope", ["Unscoped Supplier"]
            ),
            lambda value: value["snapshot_scope"].__setitem__("period_id", "1999-01"),
            lambda value: value["snapshot_scope"].__setitem__("channels", ["OTHER CHANNEL"]),
            lambda value: value["snapshot_scope"].__setitem__(
                "territories", ["OTHER TERRITORY"]
            ),
        )
        for mutation in root_mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                root = Path(temp) / "portable"
                _write_portable(root)
                _rewrite_root(root, mutation)
                with self.assertRaisesRegex(ReviewPackageError, "PORTABLE_SCOPE_MISMATCH"):
                    read_review_package(root)

        row_mutations = (
            lambda rows: rows["offers"][1].__setitem__("vendor", "Unscoped Supplier"),
            lambda rows: rows["offers"][0].__setitem__("source_period", "1999-01"),
            lambda rows: rows["offers"][0].__setitem__("channel", "OTHER CHANNEL"),
            lambda rows: rows["offers"][0].__setitem__("territory", "OTHER TERRITORY"),
        )
        for mutation in row_mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                root = Path(temp) / "portable"
                _write_portable(root, mutate_rows=mutation)
                with self.assertRaisesRegex(ReviewPackageError, "PORTABLE_SCOPE_MISMATCH"):
                    read_review_package(root)

        with TemporaryDirectory() as temp:
            previous_root = Path(temp) / "previous"
            current_root = Path(temp) / "current"
            _write_portable(previous_root, period="2026-09")
            _write_portable(current_root, period="2026-10")
            comparison = compare_review_packages(
                read_review_package(previous_root), read_review_package(current_root)
            )
        self.assertEqual(comparison["status"], "PASS")
        self.assertEqual(comparison["previous_period_id"], "2026-09")
        self.assertEqual(comparison["current_period_id"], "2026-10")

    def test_portable_logical_table_limit_cannot_be_bypassed_by_sharding(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            limits = ReviewLimits(max_rows_per_table=2)
            with self.assertRaisesRegex(ReviewPackageError, "TOO_MANY_ROWS"):
                read_review_package(root, limits=limits)

    def test_portable_rejects_part_order_hash_extra_member_and_noncanonical_row(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            _rewrite_root(root, lambda value: value["tables"][1]["parts"][1].__setitem__("ordinal", 1))
            with self.assertRaisesRegex(ReviewPackageError, "PORTABLE_PART_ORDER_MISMATCH"):
                read_review_package(root)
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            with (root / "shard-a.zip").open("ab") as output:
                output.write(b"drift")
            with self.assertRaisesRegex(ReviewPackageError, "ARCHIVE_SEAL_MISMATCH"):
                read_review_package(root)

    def test_portable_wrapper_rejects_malformed_member_bytes_and_nested_archives(self):
        cases = (
            (b"PK\x03\x04synthetic nested archive", "NESTED_ARCHIVE_REJECTED"),
            (b'{"variant_id":"1001","variant_id":"other"}\n', "INVALID_JSON"),
            (b'{"variant_id":"1001","value":NaN}\n', "INVALID_JSON"),
            (b'{"variant_id":"1001","value":1e999999999}\n', "NUMBER_TOO_LARGE"),
            (b'{"variant_id":"\xff"}\n', "INVALID_UTF8"),
            (b'{"variant_id": "1001"}\n', "NONCANONICAL_JSONL"),
            (b'{"variant_id":"1001"}\r\n', "NONCANONICAL_JSONL"),
        )
        for payload, code in cases:
            with self.subTest(code=code), TemporaryDirectory() as temp:
                root = Path(temp) / "portable"
                _write_portable(root)
                _rewrite_portable_part(
                    root,
                    archive="shard-a.zip",
                    path="tables/variants-001.jsonl",
                    payload=payload,
                )
                with self.assertRaisesRegex(ReviewPackageError, code):
                    read_review_package(root)

    def test_portable_wrapper_rejects_backslash_collision_symlink_encryption_and_truncation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            _rewrite_root(
                root,
                lambda value: value["tables"][0]["parts"][0].__setitem__(
                    "path", "tables\\variants-001.jsonl"
                ),
            )
            with self.assertRaisesRegex(ReviewPackageError, "UNSAFE_PATH"):
                read_review_package(root)
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            _rewrite_root(
                root,
                lambda value: value["tables"][2]["parts"][0].__setitem__(
                    "path", "TABLES/VARIANTS-001.JSONL"
                ),
            )
            with self.assertRaisesRegex(ReviewPackageError, "PATH_COLLISION"):
                read_review_package(root)
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            _rewrite_portable_part(
                root,
                archive="shard-a.zip",
                path="tables/variants-001.jsonl",
                payload=_jsonl(_portable_rows()["variants"]),
                symlink=True,
            )
            with self.assertRaisesRegex(ReviewPackageError, "SYMLINK_REJECTED"):
                read_review_package(root)
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            archive = root / "shard-a.zip"
            payload = bytearray(archive.read_bytes())
            central = payload.index(b"PK\x01\x02")
            flags = int.from_bytes(payload[central + 8 : central + 10], "little") | 1
            payload[central + 8 : central + 10] = flags.to_bytes(2, "little")
            archive.write_bytes(payload)
            _reseal_portable(root)
            with self.assertRaisesRegex(ReviewPackageError, "ENCRYPTED_ENTRY"):
                read_review_package(root)
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            archive = root / "shard-a.zip"
            archive.write_bytes(archive.read_bytes()[:-22])
            _reseal_portable(root)
            with self.assertRaisesRegex(ReviewPackageError, "INVALID_ZIP"):
                read_review_package(root)

    def test_portable_rejects_unauthorized_row_claim_and_bad_join(self):
        mutations = (
            lambda rows: rows["offers"][0].__setitem__("mapping_approved", True),
            lambda rows: rows["tiers"][0].__setitem__("selected_tier", True),
            lambda rows: rows["tiers"][0].__setitem__("selected_tier", 0),
            lambda rows: rows["tiers"][0].__setitem__("variant_id", "missing"),
            lambda rows: rows["tiers"][0].__setitem__("vendor", 12),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temp:
                root = Path(temp) / "portable"
                _write_portable(root, mutate_rows=mutation)
                with self.assertRaises(ReviewPackageError):
                    read_review_package(root)

    def test_portable_enforces_aggregate_shard_expansion_limit(self):
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root, pad=30_000)
            outer_bytes = sum(path.stat().st_size for path in root.iterdir())
            limits = ReviewLimits(max_total_expanded_bytes=outer_bytes + 5_000)
            with self.assertRaisesRegex(ReviewPackageError, "PACKAGE_TOO_LARGE"):
                read_review_package(root, limits=limits)
        with TemporaryDirectory() as temp:
            root = Path(temp) / "portable"
            _write_portable(root)
            with self.assertRaisesRegex(ReviewPackageError, "PACKAGE_TOO_LARGE"):
                read_review_package(root, limits=ReviewLimits(max_entries=6))

    def test_generic_two_stage_patch_chain_is_exact_and_idempotent(self):
        baseline = ({"id": "one", "value": 1, "approved": False},)
        prior = ({
            "table": "fixture", "operation": "replace_fields", "stable_key": {"id": "one"},
            "base_row_number_1based": 1,
            "before_record_sha256": hashlib.sha256(_canonical_json(baseline[0])).hexdigest(),
            "after_record_sha256": hashlib.sha256(_canonical_json({"id": "one", "value": 2, "approved": False})).hexdigest(),
            "changes": [{"field": "value", "before_present": True, "before": 1, "after_present": True, "after": 2}],
        },)
        first = replay_patch_table(
            baseline, prior, allowed_fields={"value"}, expected_stable_key_fields=("id",),
            immutable_fields={"id"}, allow_field_removal=False,
        )
        v5 = ({
            "table": "fixture", "operation": "replace_fields", "stable_key": {"id": "one"},
            "base_row_number_1based": 1,
            "before_record_sha256": hashlib.sha256(_canonical_json(first.rows[0])).hexdigest(),
            "after_record_sha256": hashlib.sha256(_canonical_json({"id": "one", "value": 3, "approved": False})).hexdigest(),
            "changes": [{"field": "value", "before_present": True, "before": 2, "after_present": True, "after": 3}],
        },)
        second = replay_patch_table(
            first.rows, v5, allowed_fields={"value"}, expected_stable_key_fields=("id",),
            immutable_fields={"id"}, allow_field_removal=False,
        )
        replayed = replay_patch_table(
            second.rows, v5, allowed_fields={"value"}, expected_stable_key_fields=("id",),
            immutable_fields={"id"}, allow_field_removal=False,
        )
        self.assertEqual(second.rows[0], {"id": "one", "value": 3, "approved": False})
        self.assertFalse(replayed.applied)

    def test_fabricated_large_patch_replay_is_exact_bounded_and_idempotent(self):
        baseline = tuple(
            {"id": f"row-{index:05d}", "value": index, "approved": False}
            for index in range(12_000)
        )
        patches: list[dict[str, object]] = []
        expected = [dict(row) for row in baseline]
        for index in range(120):
            before = dict(expected[index])
            after = {**before, "value": 100_000 + index}
            patches.append(
                {
                    "table": "fabricated_large_fixture",
                    "operation": "replace_fields",
                    "stable_key": {"id": before["id"]},
                    "base_row_number_1based": index + 1,
                    "before_record_sha256": _sha(_canonical_json(before)),
                    "after_record_sha256": _sha(_canonical_json(after)),
                    "changes": [
                        {
                            "field": "value",
                            "before_present": True,
                            "before": before["value"],
                            "after_present": True,
                            "after": after["value"],
                        }
                    ],
                }
            )
            expected[index] = after
        for index in range(80):
            record = {
                "id": f"row-{12_000 + index:05d}",
                "value": 200_000 + index,
                "approved": False,
            }
            patches.append(
                {
                    "table": "fabricated_large_fixture",
                    "operation": "append_record",
                    "stable_key": {"id": record["id"]},
                    "before_record_sha256": None,
                    "after_record_sha256": _sha(_canonical_json(record)),
                    "append_record": record,
                }
            )
            expected.append(record)

        expected_hash = canonical_jsonl_sha256(expected)
        first = replay_patch_table(
            baseline,
            patches,
            allowed_fields={"value"},
            append_allowed_fields={"id", "value", "approved"},
            required_append_fields={"id", "value", "approved"},
            expected_stable_key_fields=("id",),
            immutable_fields={"id", "approved"},
            allow_field_removal=False,
            append_changes_required=False,
            expected_rows=12_080,
            expected_sha256=expected_hash,
        )
        replay = replay_patch_table(
            first.rows,
            patches,
            allowed_fields={"value"},
            append_allowed_fields={"id", "value", "approved"},
            required_append_fields={"id", "value", "approved"},
            expected_stable_key_fields=("id",),
            immutable_fields={"id", "approved"},
            allow_field_removal=False,
            append_changes_required=False,
            expected_rows=12_080,
            expected_sha256=expected_hash,
        )

        self.assertEqual(len(first.rows), 12_080)
        self.assertEqual(first.applied, 200)
        self.assertEqual(first.already_applied, 0)
        self.assertEqual(first.rows[0]["value"], 100_000)
        self.assertEqual(first.rows[119]["value"], 100_119)
        self.assertEqual(first.rows[-1], {"id": "row-12079", "value": 200_079, "approved": False})
        self.assertEqual(replay.applied, 0)
        self.assertEqual(replay.already_applied, 200)
        self.assertEqual(replay.canonical_jsonl_sha256, expected_hash)


if __name__ == "__main__":
    unittest.main()
