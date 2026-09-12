"""Source-free contracts for the offline Wright PDF review prototype.

Every scenario is fabricated.  Fake Poppler executables exercise the complete
snapshot/process/TSV/parser/output path without claiming real-PDF acceptance.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from supplier_format_test_support import presence_aware_differences


TESTS_DIR = Path(__file__).resolve().parent
TOOL_PATH = TESTS_DIR.parent / "tools" / "extract_supplier_pdf_review.py"
FIXTURE_PATH = TESTS_DIR / "fixtures" / "supplier_pdf_extraction_wright_v1.jsonl"
RUNNER_PATH = TESTS_DIR.parent / "tools" / "run_tests.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


extractor = _load_module("supplier_pdf_extraction_tool", TOOL_PATH)
runner = _load_module("supplier_pdf_extraction_runner", RUNNER_PATH)


def _load_fixture() -> dict[str, object]:
    raw = FIXTURE_PATH.read_bytes()
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise AssertionError("fabricated fixture must be exactly one JSONL row")
    value = json.loads(raw)
    if value.get("synthetic") is not True or value.get("source_kind") != "FABRICATED_TEST_SCENARIO":
        raise AssertionError("fixture is not explicitly fabricated")
    return value


def _tsv_for_pages(page_rows: list[dict[str, object]]) -> bytes:
    rows = ["\t".join(extractor._TSV_HEADER)]
    for page_row in page_rows:
        page = int(page_row["physical_page"])
        rows.append(
            f"1\t{page}\t0\t0\t0\t0\t0.000000\t0.000000\t612.000000\t792.000000\t-1\t###PAGE###"
        )
        for block, line in enumerate(page_row["lines"]):
            words = str(line["text"]).split(" ")
            left = float(line["left"])
            total_width = float(line["width"])
            word_width = total_width / max(1, len(words))
            for ordinal, word in enumerate(words):
                rows.append(
                    "\t".join(
                        (
                            "5",
                            str(page),
                            "0",
                            str(block),
                            "0",
                            str(ordinal),
                            f"{left + ordinal * word_width:.2f}",
                            str(line["top"]),
                            f"{word_width:.2f}",
                            str(line["height"]),
                            "100",
                            word,
                        )
                    )
                )
    return ("\n".join(rows) + "\n").encode("utf-8")


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def _fake_tools(
    root: Path,
    fixture: dict[str, object],
    *,
    encryption_value: str | None = "no",
):
    python = Path(sys.executable).resolve().as_posix()
    pages = fixture["pages"]
    per_page = {
        int(page["physical_page"]): _tsv_for_pages([page]).decode("utf-8")
        for page in pages
    }
    pdfinfo = root / "fake-pdfinfo"
    pdftotext = root / "fake-pdftotext"
    encryption_line = (
        "" if encryption_value is None else f"Encrypted: {encryption_value}\n"
    )
    _write_executable(
        pdfinfo,
        textwrap.dedent(
            f"""\
            #!{python}
            import sys
            if '-v' in sys.argv:
                sys.stderr.write('pdfinfo version {extractor.POPPLER_VERSION}\\n')
            else:
                sys.stdout.write('Pages: {len(pages)}\\n' + {encryption_line!r} + 'PDF version: 1.6\\n')
            """
        ),
    )
    _write_executable(
        pdftotext,
        textwrap.dedent(
            f"""\
            #!{python}
            import sys
            payloads = {per_page!r}
            if '-v' in sys.argv:
                sys.stderr.write('pdftotext version {extractor.POPPLER_VERSION}\\n')
            else:
                start = int(sys.argv[sys.argv.index('-f') + 1])
                end = int(sys.argv[sys.argv.index('-l') + 1])
                selected = [payloads[page].splitlines() for page in range(start, end + 1)]
                rows = [selected[0][0]]
                for page_rows in selected:
                    rows.extend(page_rows[1:])
                sys.stdout.write('\\n'.join(rows) + '\\n')
            """
        ),
    )
    return pdfinfo, pdftotext


class SupplierPdfExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _load_fixture()
        self.temp = tempfile.TemporaryDirectory(prefix="buffalo-pdf-test-")
        self.root = Path(self.temp.name).resolve()
        self.input_path = self.root / "fabricated.pdf"
        self.input_path.write_bytes(self.fixture["pdf_bytes"].encode("utf-8"))
        self.pdfinfo, self.pdftotext = _fake_tools(self.root, self.fixture)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _extract(self, *, input_path: Path | None = None, expected_sha256: str | None = None):
        return extractor.extract_document(
            input_path=input_path or self.input_path,
            supplier_format="WRIGHT_V1",
            pages=(1, 2),
            pdfinfo_path=self.pdfinfo,
            pdftotext_path=self.pdftotext,
            expected_sha256=expected_sha256,
        )

    def _parsed_fixture(self):
        raw = _tsv_for_pages(self.fixture["pages"])
        digest = hashlib.sha256(self.fixture["pdf_bytes"].encode()).hexdigest()
        pages = extractor.parse_poppler_tsv(raw, digest, (1, 2))
        return extractor.parse_wright_pages(pages, digest)

    def _parse_pages(self, pages, *, marker="fabricated-scope-case"):
        digest = hashlib.sha256(marker.encode("utf-8")).hexdigest()
        selected = tuple(int(page["physical_page"]) for page in pages)
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages(pages), digest, selected)
        return extractor.parse_wright_pages(parsed, digest)

    def test_fabricated_end_to_end_retains_regular_retail_tiers_and_coverage(self):
        extraction, coverage = self._extract()
        expected = self.fixture["expected"]
        self.assertEqual(
            [row["printed_code"]["value"] for row in extraction["source_occurrences"]],
            expected["supported_codes"],
        )
        self.assertEqual(
            [row["identity_class"] for row in extraction["source_occurrences"]],
            ["PRINTED_SOURCE_OCCURRENCE_NOT_OPERATIONAL_OFFER"] * 2,
        )
        self.assertEqual(
            [len(row["tiers"]) for row in extraction["source_occurrences"]],
            expected["tier_counts"],
        )
        self.assertEqual(extraction["authority_effects"], expected["authority_effects"])
        self.assertEqual(coverage["supported_occurrence_count"], 2)
        tier_ids = [
            tier["source_tier_id"]
            for occurrence in extraction["source_occurrences"]
            for tier in occurrence["tiers"]
        ]
        self.assertEqual(len(tier_ids), len(set(tier_ids)))
        self.assertTrue(all(value.startswith("WRT-TIER-") for value in tier_ids))
        self.assertEqual(len(extraction["page_terms"]), 2)
        self.assertEqual(
            {term["raw"] for term in extraction["page_terms"]},
            {
                "Split case charge $9.87 per bottle",
                "Prices include split case fee $8.76 per can",
            },
        )
        self.assertEqual(extraction["pages"][0]["printed_page"]["raw"], "PAGE 41")
        for page in coverage["pages"]:
            self.assertTrue(page["coverage_partition"])
            self.assertEqual(
                len({row["line_id"] for row in page["coverage_partition"]}),
                len(page["coverage_partition"]),
            )

        self.assertEqual(extraction["format"], extractor.FORMAT)
        self.assertEqual(coverage["format"], extractor.COVERAGE_FORMAT)
        self.assertEqual(
            {row["scoped_notes"]["state"] for row in extraction["source_occurrences"]},
            {"ABSENT"},
        )
        self.assertTrue(
            all(
                row["evidence_status"]
                == "SUPPORTED_SOURCE_EVIDENCE_NOT_OPERATIONAL_AUTHORITY"
                for row in extraction["source_occurrences"]
            )
        )

    def test_clean_award_line_displaces_no_title_and_quarantines_the_candidate_block(self):
        page = json.loads(json.dumps(self.fixture["pages"][0]))
        award_text = "Gold Medal 2025 San Francisco Competition"
        page["lines"].insert(
            2,
            {
                "text": award_text,
                "left": "54.00",
                "top": "78.00",
                "width": "190.00",
                "height": "4.00",
            },
        )
        extraction, coverage = self._parse_pages([page], marker="clean-award-displacement")

        self.assertFalse(
            any(
                row["printed_code"]["value"] == "0012-A"
                for row in extraction["source_occurrences"]
            )
        )
        block = next(
            row
            for row in extraction["quarantines"]
            if row["reason"] == "DESCRIPTION_CANDIDATES_AMBIGUOUS_OR_DISPLACED"
            and row.get("evidence_fields", {}).get("printed_code", {}).get("value")
            == "0012-A"
        )
        self.assertEqual(block["scope_level"], "OCCURRENCE_BLOCK")
        self.assertEqual(block["evidence_status"], "PARTIAL_REVIEW_REQUIRED")
        self.assertTrue(str(block["candidate_block_id"]).startswith("WRT-CANDIDATE-"))
        self.assertEqual(block["description"]["state"], "UNRESOLVED")
        self.assertEqual(block["scoped_notes"]["state"], "UNRESOLVED")
        self.assertEqual(len(block["evidence_fields"]["tiers"]), 2)
        candidate_raw = {row["raw"] for row in block["description"]["candidates"]}
        self.assertEqual(candidate_raw, {"Fabricated Citrus Reserve", award_text})
        self.assertTrue(
            all(
                row["evidence_sha256"]
                and row["bbox"]
                and row["source_line_id"] in block["source_line_ids"]
                for row in block["description"]["candidates"]
            )
        )
        categories = {
            row["line_id"]: row["category"]
            for row in extraction["pages"][0]["coverage_partition"]
        }
        for candidate in block["description"]["candidates"]:
            self.assertEqual(categories[candidate["source_line_id"]], "EXPLICIT_QUARANTINE")
        extraction_ref = next(
            ref
            for ref in extraction["pages"][0]["unresolved_scope_refs"]
            if ref.get("candidate_block_id") == block["candidate_block_id"]
        )
        coverage_ref = next(
            ref
            for ref in coverage["pages"][0]["unresolved_scope_refs"]
            if ref.get("candidate_block_id") == block["candidate_block_id"]
        )
        self.assertEqual(extraction_ref, coverage_ref)
        self.assertNotIn(award_text, [row["description"].get("value") for row in extraction["source_occurrences"]])

    def test_multiple_descriptions_fail_closed_but_single_description_control_is_supported(self):
        control, _ = self._parse_pages(
            [json.loads(json.dumps(self.fixture["pages"][0]))],
            marker="single-description-control",
        )
        regular = next(
            row for row in control["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(regular["description"]["value"], "Fabricated Citrus Reserve")
        self.assertEqual(regular["scoped_notes"]["state"], "ABSENT")
        self.assertEqual(
            regular["scoped_notes"]["reason"],
            "NO_SCOPED_NOTE_VALUE_ESTABLISHED_BY_SUPPORTED_GRAMMAR",
        )
        self.assertNotIn("items", regular["scoped_notes"])

        ambiguous_page = json.loads(json.dumps(self.fixture["pages"][0]))
        ambiguous_page["lines"].insert(
            2,
            {
                "text": "Fabricated Citrus Reserve Select",
                "left": "54.00",
                "top": "78.00",
                "width": "180.00",
                "height": "4.00",
            },
        )
        ambiguous, _ = self._parse_pages(
            [ambiguous_page], marker="two-description-candidates"
        )
        block = next(
            row for row in ambiguous["quarantines"]
            if row["reason"] == "DESCRIPTION_CANDIDATES_AMBIGUOUS_OR_DISPLACED"
        )
        self.assertEqual(len(block["description"]["candidate_line_ids"]), 2)
        self.assertNotIn("0012-A", [row["printed_code"]["value"] for row in ambiguous["source_occurrences"]])

    def test_scoped_note_states_distinguish_absent_recovered_and_unresolved(self):
        plain, _ = self._parse_pages(
            [json.loads(json.dumps(self.fixture["pages"][0]))], marker="notes-absent"
        )
        plain_note = next(
            row["scoped_notes"] for row in plain["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(plain_note["state"], "ABSENT")

        variety_page = json.loads(json.dumps(self.fixture["pages"][0]))
        variety_page["lines"][1]["text"] = "Fabricated Citrus Variety Pack"
        variety_page["lines"].insert(
            2,
            {
                "text": "Orange, Lime and Berry",
                "left": "54.00",
                "top": "78.00",
                "width": "155.00",
                "height": "4.00",
            },
        )
        recovered, _ = self._parse_pages([variety_page], marker="notes-recovered")
        recovered_note = next(
            row["scoped_notes"] for row in recovered["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(recovered_note["state"], "VALUE")
        self.assertEqual(recovered_note["reason"], "SUPPORTED_VARIETY_LIST_GRAMMAR")
        self.assertEqual(recovered_note["items"][0]["raw"], "Orange, Lime and Berry")
        self.assertRegex(recovered_note["items"][0]["evidence_sha256"], r"^[0-9a-f]{64}$")

        damaged_page = json.loads(json.dumps(self.fixture["pages"][0]))
        damaged_page["lines"].insert(
            2,
            {
                "text": "Gold \ufffd Medal",
                "left": "54.00",
                "top": "78.00",
                "width": "120.00",
                "height": "4.00",
            },
        )
        unresolved, _ = self._parse_pages([damaged_page], marker="notes-damaged")
        unresolved_note = next(
            row["scoped_notes"] for row in unresolved["quarantines"]
            if row["reason"] == "DESCRIPTION_CANDIDATES_AMBIGUOUS_OR_DISPLACED"
        )
        self.assertEqual(unresolved_note["state"], "UNRESOLVED")
        self.assertNotIn("EXPLICIT_NULL", {plain_note["state"], recovered_note["state"], unresolved_note["state"]})

    def test_harmless_looking_and_damaged_interveners_remain_partial_raw_evidence(self):
        for ordinal, intervening in enumerate(("Featured Selection", "Fragment \ue123 \ufffd")):
            with self.subTest(intervening=intervening):
                page = json.loads(json.dumps(self.fixture["pages"][0]))
                page["lines"].insert(
                    3,
                    {
                        "text": intervening,
                        "left": "54.00",
                        "top": "97.00",
                        "width": "135.00",
                        "height": "0.50",
                    },
                )
                extraction, _ = self._parse_pages(
                    [page], marker=f"intervening-{ordinal}"
                )
                block = next(
                    row for row in extraction["quarantines"]
                    if row["reason"] == "INTERVENING_SCOPE_LINE_BEFORE_PRICE_LADDER"
                )
                self.assertEqual(block["evidence_status"], "PARTIAL_REVIEW_REQUIRED")
                self.assertEqual(block["evidence_fields"]["printed_code"]["value"], "0012-A")
                self.assertEqual(len(block["evidence_fields"]["tiers"]), 2)
                self.assertIn(intervening, {row["raw"] for row in block["source_evidence"]})
                self.assertNotIn(
                    next(
                        row["source_line_id"]
                        for row in block["source_evidence"]
                        if row["raw"] == intervening
                    ),
                    extraction["pages"][0]["footer_source_line_ids"],
                )
                self.assertFalse(
                    any(
                        row["printed_code"]["value"] == "0012-A"
                        for row in extraction["source_occurrences"]
                    )
                )

    def test_nonprice_commercial_scope_language_is_elevated_at_each_position(self):
        cases = (
            ("Allocation required", "BEFORE_PACK"),
            ("NY territory only", "BETWEEN_PACK_AND_LADDER"),
            ("Wholesale channel only", "AFTER_LADDER"),
            ("Minimum order 5 cases", "BEFORE_PACK"),
            ("Available while supplies last", "BETWEEN_PACK_AND_LADDER"),
            ("Split case restrictions apply", "AFTER_LADDER"),
            ("Handling conditions apply", "BEFORE_PACK"),
            ("Does not assort", "AFTER_LADDER"),
        )
        for ordinal, (restriction, position) in enumerate(cases):
            with self.subTest(restriction=restriction, position=position):
                page = json.loads(json.dumps(self.fixture["pages"][0]))
                if position == "BEFORE_PACK":
                    index, top, height = 2, "78.00", "4.00"
                elif position == "BETWEEN_PACK_AND_LADDER":
                    index, top, height = 3, "97.00", "0.50"
                else:
                    index, top, height = 5, "121.00", "6.00"
                page["lines"].insert(
                    index,
                    {
                        "text": restriction,
                        "left": "54.00",
                        "top": top,
                        "width": "175.00",
                        "height": height,
                    },
                )
                extraction, coverage = self._parse_pages(
                    [page], marker=f"commercial-{ordinal}-{position}"
                )
                restriction_quarantine = next(
                    row for row in extraction["quarantines"]
                    if restriction in {item["raw"] for item in row["source_evidence"]}
                )
                self.assertEqual(
                    restriction_quarantine["uncertainty_class"],
                    "COMMERCIAL_SCOPE_LANGUAGE",
                )
                self.assertNotIn("$", restriction)
                self.assertFalse(extractor._TIER_LIKE.search(restriction))
                self.assertIn(
                    restriction_quarantine["quarantine_id"],
                    {
                        ref["quarantine_id"]
                        for ref in coverage["pages"][0]["unresolved_scope_refs"]
                    },
                )
                if position == "AFTER_LADDER":
                    regular = next(
                        row for row in extraction["source_occurrences"]
                        if row["printed_code"]["value"] == "0012-A"
                    )
                    self.assertEqual(regular["evidence_status"], "PARTIAL_REVIEW_REQUIRED")
                    self.assertIn(
                        restriction_quarantine["source_line_ids"][0],
                        regular["adjacent_unresolved_line_ids"],
                    )

    def test_scope_boundaries_do_not_guess_across_columns_pages_or_neighboring_products(self):
        other_column = json.loads(json.dumps(self.fixture["pages"][0]))
        other_column["lines"].insert(
            5,
            {
                "text": "Territory limited",
                "left": "390.00",
                "top": "121.00",
                "width": "130.00",
                "height": "6.00",
            },
        )
        extracted, _ = self._parse_pages([other_column], marker="other-column-scope")
        regular = next(
            row for row in extracted["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(regular["adjacent_unresolved_line_ids"], [])
        territory = next(
            row for row in extracted["quarantines"] if row["text"] == "Territory limited"
        )
        self.assertEqual(territory["scope_level"], "COLUMN")

        page_wide = json.loads(json.dumps(self.fixture["pages"][0]))
        page_wide["lines"].insert(
            3,
            {
                "text": "Allocation pending",
                "left": "100.00",
                "top": "97.00",
                "width": "412.00",
                "height": "0.50",
            },
        )
        page_wide_result, _ = self._parse_pages(
            [page_wide], marker="page-wide-scope"
        )
        allocation = next(
            row for row in page_wide_result["quarantines"]
            if row["text"] == "Allocation pending"
        )
        self.assertEqual(allocation["scope_level"], "PAGE")
        self.assertFalse(
            any(
                row["printed_code"]["value"] == "0012-A"
                for row in page_wide_result["source_occurrences"]
            )
        )

        neighbor = json.loads(json.dumps(self.fixture["pages"][0]))
        neighbor["lines"].insert(
            5,
            {
                "text": "Fabricated Neighbor",
                "left": "54.00",
                "top": "122.00",
                "width": "135.00",
                "height": "6.00",
            },
        )
        neighbor["lines"].insert(
            6,
            {
                "text": "750mL 6 Pack - NBR-2",
                "left": "54.00",
                "top": "130.00",
                "width": "145.00",
                "height": "6.00",
            },
        )
        neighbor["lines"].insert(
            7,
            {
                "text": "1 Case: $66.00 \u00b7 Per Bottle: $11.00",
                "left": "54.00",
                "top": "138.00",
                "width": "200.00",
                "height": "6.00",
            },
        )
        neighboring, _ = self._parse_pages([neighbor], marker="neighbor-product")
        by_code = {
            row["printed_code"]["value"]: row
            for row in neighboring["source_occurrences"]
        }
        self.assertIn("0012-A", by_code)
        self.assertIn("NBR-2", by_code)
        self.assertNotIn(
            "Fabricated Neighbor",
            json.dumps(by_code["0012-A"], ensure_ascii=False),
        )

        first = json.loads(json.dumps(self.fixture["pages"][0]))
        second = json.loads(json.dumps(self.fixture["pages"][1]))
        second["lines"].insert(
            0,
            {
                "text": "Minimum order applies",
                "left": "54.00",
                "top": "20.00",
                "width": "140.00",
                "height": "8.00",
            },
        )
        cross_page, _ = self._parse_pages([first, second], marker="cross-page-scope")
        page_one_regular = next(
            row for row in cross_page["source_occurrences"]
            if row["physical_page"] == 1 and row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(page_one_regular["adjacent_unresolved_line_ids"], [])

    def test_extraction_and_coverage_mirror_refs_with_claim_once_integrity(self):
        page = json.loads(json.dumps(self.fixture["pages"][0]))
        page["lines"].insert(
            5,
            {
                "text": "Handling restrictions apply",
                "left": "54.00",
                "top": "121.00",
                "width": "165.00",
                "height": "6.00",
            },
        )
        extraction, coverage = self._parse_pages([page], marker="mirror-integrity")
        extraction_page = extraction["pages"][0]
        coverage_page = coverage["pages"][0]
        for key in (
            "unresolved_scope_line_ids",
            "unresolved_scope_refs",
            "occurrence_unresolved_refs",
            "broader_scope_unresolved_refs",
        ):
            self.assertEqual(extraction_page[key], coverage_page[key])
        line_ids = [row["line_id"] for row in extraction_page["lines"]]
        claims = [row["line_id"] for row in extraction_page["coverage_partition"]]
        self.assertEqual(claims, line_ids)
        self.assertEqual(len(claims), len(set(claims)))
        quarantine_ids = [row["quarantine_id"] for row in extraction["quarantines"]]
        self.assertEqual(len(quarantine_ids), len(set(quarantine_ids)))
        self.assertTrue(
            all(
                ref["quarantine_id"] in quarantine_ids
                for ref in extraction_page["unresolved_scope_refs"]
            )
        )
        supported_claims = {
            line_id
            for row in extraction["source_occurrences"]
            for line_id in row["source_line_ids"]
        }
        quarantine_claims = {
            line_id
            for row in extraction["quarantines"]
            for line_id in row["source_line_ids"]
        }
        self.assertTrue(supported_claims.isdisjoint(quarantine_claims))

    def test_v2_publication_rejects_stale_shapes_and_preserves_old_output(self):
        extraction, coverage = self._extract()
        output = self.root / "v2-output"
        first = extractor.write_bundle(output, extraction, coverage)
        before = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns, stat.S_IMODE(path.stat().st_mode))
            for path in output.iterdir()
        }
        second = extractor.write_bundle(output, extraction, coverage)
        after = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns, stat.S_IMODE(path.stat().st_mode))
            for path in output.iterdir()
        }
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(before, after)
        manifest = json.loads((output / "SHA256SUMS.json").read_bytes())
        self.assertEqual(manifest["format"], extractor.MANIFEST_FORMAT)

        stale_extraction = json.loads(json.dumps(extraction))
        stale_extraction["format"] = "BUFFALO_SUPPLIER_PDF_REVIEW_V1"
        with self.assertRaisesRegex(extractor.ExtractionError, "OUTPUT_FORMAT_MISMATCH"):
            extractor.write_bundle(self.root / "stale-shape", stale_extraction, coverage)

        old_output = self.root / "old-v1-output"
        old_output.mkdir(mode=0o700)
        for name in extractor._OUTPUT_FILES:
            path = old_output / name
            path.write_bytes(b'{"format":"V1"}\n')
            path.chmod(0o600)
        old_before = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in old_output.iterdir()
        }
        with self.assertRaisesRegex(extractor.ExtractionError, "OUTPUT_DRIFT"):
            extractor.write_bundle(old_output, extraction, coverage)
        old_after = {
            path.name: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in old_output.iterdir()
        }
        self.assertEqual(old_before, old_after)

    def test_missing_code_is_quarantined_without_fabricating_identity(self):
        extraction, _ = self._parsed_fixture()
        reasons = [row["reason"] for row in extraction["quarantines"]]
        self.assertIn("MISSING_PRINTED_CODE", reasons)
        missing = next(
            row for row in extraction["quarantines"]
            if row["reason"] == "MISSING_PRINTED_CODE"
        )
        self.assertEqual(
            missing["evidence_fields"]["printed_code"],
            {"state": "BLANK", "raw": ""},
        )
        self.assertNotIn("", [row["printed_code"]["value"] for row in extraction["source_occurrences"]])

    def test_same_code_with_changed_pack_or_expression_has_distinct_occurrence_ids(self):
        page = json.loads(json.dumps(self.fixture["pages"][0]))
        page["lines"].insert(
            -1,
            {"text": "1L 6 Pack PET - 0012-A", "left": "54.00", "top": "220.00", "width": "145.00", "height": "12.00"},
        )
        page["lines"].insert(
            -1,
            {"text": "Fabricated Changed Expression", "left": "54.00", "top": "206.00", "width": "150.00", "height": "12.00"},
        )
        page["lines"].insert(
            -1,
            {"text": "1 Case: $66.00 · Per Bottle: $11.00", "left": "54.00", "top": "234.00", "width": "200.00", "height": "10.00"},
        )
        digest = "1" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([page]), digest, (1,))
        extraction, _ = extractor.parse_wright_pages(parsed, digest)
        matching = [row for row in extraction["source_occurrences"] if row["printed_code"]["value"] == "0012-A"]
        self.assertEqual(len(matching), 2)
        self.assertEqual(len({row["source_occurrence_id"] for row in matching}), 2)
        self.assertNotEqual(matching[0]["pack_line"], matching[1]["pack_line"])

    def test_leading_zero_and_meaningful_code_suffix_are_preserved_exactly(self):
        extraction, _ = self._parsed_fixture()
        code = extraction["source_occurrences"][0]["printed_code"]
        self.assertEqual(code, {"state": "VALUE", "raw": "0012-A", "value": "0012-A"})

    def test_regular_and_gift_evidence_are_separated_and_gift_is_unsupported(self):
        extraction, _ = self._parsed_fixture()
        self.assertEqual(
            [row["source_offer_class"] for row in extraction["source_occurrences"]],
            ["REGULAR", "RETAIL_MULTIPACK"],
        )
        self.assertTrue(
            any(
                row["reason"] == "UNSUPPORTED_CLASS_OR_COMPONENT_LAYOUT"
                for row in extraction["quarantines"]
            )
        )
        self.assertFalse(any(row["printed_code"]["value"] == "GIFT-7" for row in extraction["source_occurrences"]))

        for marker in ("SPECIALS", "ASSORTED", "FIXED COMBO", "COMPONENTS"):
            with self.subTest(marker=marker):
                page = json.loads(json.dumps(self.fixture["pages"][0]))
                page["lines"][5]["text"] = f"FABRICATED {marker}"
                digest = hashlib.sha256(marker.encode()).hexdigest()
                parsed = extractor.parse_poppler_tsv(_tsv_for_pages([page]), digest, (1,))
                result, _ = extractor.parse_wright_pages(parsed, digest)
                self.assertFalse(
                    any(
                        row["printed_code"]["value"] == "GIFT-7"
                        for row in result["source_occurrences"]
                    )
                )
                self.assertTrue(
                    any(
                        row["reason"] == "UNSUPPORTED_CLASS_OR_COMPONENT_LAYOUT"
                        for row in result["quarantines"]
                    )
                )

        qualifier_page = json.loads(json.dumps(self.fixture["pages"][0]))
        qualifier_page["lines"][5]["text"] = "FABRICATED PRESENTATIONS"
        qualifier_page["lines"][7]["text"] = "750mL 6 Pack SPECIAL - GIFT-7"
        digest = "5" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([qualifier_page]), digest, (1,))
        result, _ = extractor.parse_wright_pages(parsed, digest)
        self.assertFalse(
            any(row["printed_code"]["value"] == "GIFT-7" for row in result["source_occurrences"])
        )

    def test_column_and_unrelated_product_continuations_do_not_leak(self):
        extraction, coverage = self._parsed_fixture()
        retail = next(row for row in extraction["source_occurrences"] if row["printed_code"]["value"] == "RTD-09")
        self.assertEqual(len(retail["tiers"]), 2)
        self.assertNotIn("999.00", json.dumps(retail))
        page_two = next(page for page in coverage["pages"] if page["physical_page"] == 2)
        self.assertEqual(len(page_two["unresolved_price_like_line_ids"]), 1)

        same_column = json.loads(json.dumps(self.fixture["pages"][0]))
        same_column["lines"].insert(
            3,
            {
                "text": "Fabricated Intervening Product",
                "left": "54.00",
                "top": "91.00",
                "width": "170.00",
                "height": "6.00",
            },
        )
        digest = "6" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([same_column]), digest, (1,))
        interrupted, _ = extractor.parse_wright_pages(parsed, digest)
        self.assertFalse(
            any(
                row["printed_code"]["value"] == "0012-A"
                for row in interrupted["source_occurrences"]
            )
        )

        for intervening in (
            "FABRICATED PAGE-WIDE SPECIAL TRANSITION",
            "1 Case: $777.00 · Per Bottle: $77.00",
        ):
            with self.subTest(page_wide_intervening=intervening):
                page_wide = json.loads(json.dumps(self.fixture["pages"][0]))
                page_wide["lines"].insert(
                    3,
                    {
                        "text": intervening,
                        "left": "100.00",
                        "top": "91.00",
                        "width": "412.00",
                        "height": "6.00",
                    },
                )
                digest = hashlib.sha256(intervening.encode()).hexdigest()
                parsed = extractor.parse_poppler_tsv(
                    _tsv_for_pages([page_wide]),
                    digest,
                    (1,),
                )
                page_wide_result, _ = extractor.parse_wright_pages(parsed, digest)
                self.assertFalse(
                    any(
                        row["printed_code"]["value"] == "0012-A"
                        for row in page_wide_result["source_occurrences"]
                    )
                )
                self.assertTrue(
                    any(
                        row["text"] == intervening
                        for row in page_wide_result["quarantines"]
                    )
                )

        first = json.loads(json.dumps(self.fixture["pages"][0]))
        first["lines"] = [
            line for line in first["lines"]
            if not str(line["text"]).startswith(("1 Case: $120.00", "3 Cases: $108.00"))
        ]
        second = json.loads(json.dumps(self.fixture["pages"][1]))
        second["lines"].insert(
            0,
            {
                "text": "1 Case: $120.00 · Per Bottle: $10.00",
                "left": "54.00",
                "top": "20.00",
                "width": "200.00",
                "height": "10.00",
            },
        )
        digest = "7" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([first, second]), digest, (1, 2))
        cross_page, _ = extractor.parse_wright_pages(parsed, digest)
        self.assertFalse(
            any(
                row["printed_code"]["value"] == "0012-A"
                for row in cross_page["source_occurrences"]
            )
        )
        self.assertTrue(
            any(row["reason"] == "UNASSIGNED_PRICE_LIKE_LINE" for row in cross_page["quarantines"])
        )

        claimed = [
            line_id
            for occurrence in extraction["source_occurrences"]
            for line_id in occurrence["source_line_ids"]
        ] + [
            line_id
            for quarantine in extraction["quarantines"]
            for line_id in quarantine["source_line_ids"]
        ]
        self.assertEqual(len(claimed), len(set(claimed)))

    def test_threshold_and_price_changes_remain_distinct_and_deepest_is_not_substituted(self):
        extraction, _ = self._parsed_fixture()
        regular = next(row for row in extraction["source_occurrences"] if row["printed_code"]["value"] == "0012-A")
        self.assertEqual([row["threshold"]["count"] for row in regular["tiers"]], [1, 3])
        self.assertEqual([row["case_price"]["value"] for row in regular["tiers"]], ["120.00", "108.00"])
        self.assertEqual(regular["tiers"][0]["per_unit"]["price"]["value"], "10.00")

    def test_nested_retail_counts_and_price_bases_are_not_coerced_to_case_units(self):
        extraction, _ = self._parsed_fixture()
        retail = next(row for row in extraction["source_occurrences"] if row["printed_code"]["value"] == "RTD-09")
        self.assertEqual(retail["package"]["outer_count"]["value"], 6)
        self.assertEqual(retail["package"]["inner_pack_count"]["value"], 4)
        self.assertEqual(retail["package"]["case_pack"]["state"], "ABSENT")
        self.assertEqual(retail["tiers"][0]["per_unit"]["label_raw"], "Can")
        self.assertEqual(retail["tiers"][0]["per_retail_pack"]["value"], "8.00")
        diagnostic = retail["tiers"][0]["arithmetic_diagnostic"]
        self.assertEqual(diagnostic["printed_divisor"], 24)
        self.assertEqual(
            diagnostic["retail_pack_check"]["computed_at_printed_scale"],
            "8.00",
        )
        self.assertIs(
            diagnostic["retail_pack_check"]["matches_rounding_at_printed_scale"],
            True,
        )
        self.assertIs(diagnostic["container_unit_consistency"]["matches"], True)

        for container, unit, singular in (
            ("BOXES", "Box", "BOX"),
            ("CANS", "Can", "CAN"),
            ("bottles", "Bottle", "BOTTLE"),
        ):
            with self.subTest(container=container):
                page = json.loads(json.dumps(self.fixture["pages"][1]))
                page["lines"][2]["text"] = (
                    f"(6) 4 pack 355mL {container} - RTD-09"
                )
                page["lines"][3]["text"] = (
                    f"1 Case: $48.00 · Per {unit}: $2.00 · Per Pack: $8.00"
                )
                page["lines"][4]["text"] = (
                    f"5 Cases: $42.00 · Per {unit}: $1.75 · Per Pack: $7.00"
                )
                digest = hashlib.sha256(container.encode()).hexdigest()
                parsed = extractor.parse_poppler_tsv(
                    _tsv_for_pages([page]),
                    digest,
                    (2,),
                )
                result, _ = extractor.parse_wright_pages(parsed, digest)
                row = next(
                    occurrence for occurrence in result["source_occurrences"]
                    if occurrence["printed_code"]["value"] == "RTD-09"
                )
                check = row["tiers"][0]["arithmetic_diagnostic"][
                    "container_unit_consistency"
                ]
                self.assertEqual(check["expected"], singular)
                self.assertEqual(check["observed"], singular)
                self.assertIs(check["matches"], True)

    def test_contradictory_printed_arithmetic_is_retained_and_flagged(self):
        page = json.loads(json.dumps(self.fixture["pages"][0]))
        for line in page["lines"]:
            if line["text"].startswith("1 Case: $120.00"):
                line["text"] = "1 Case: $101.00 · Per Bottle: $8.00"
        digest = "2" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([page]), digest, (1,))
        extraction, _ = extractor.parse_wright_pages(parsed, digest)
        tier = next(row for row in extraction["source_occurrences"] if row["printed_code"]["value"] == "0012-A")["tiers"][0]
        self.assertEqual(tier["case_price"]["value"], "101.00")
        self.assertEqual(tier["per_unit"]["price"]["value"], "8.00")
        self.assertEqual(tier["arithmetic_diagnostic"]["computed_at_printed_scale"], "8.42")
        self.assertIs(tier["arithmetic_diagnostic"]["matches_rounding_at_printed_scale"], False)

    def test_split_fee_is_page_scoped_and_never_added_or_double_counted(self):
        extraction, _ = self._parsed_fixture()
        separate, included = extraction["page_terms"]
        self.assertEqual(separate["amount"]["value"], "9.87")
        self.assertEqual(separate["basis"]["value"], "BOTTLE")
        self.assertEqual(
            separate["inclusion"],
            "SEPARATELY_CHARGED_NOT_INCLUDED_IN_ITEM_PRICE",
        )
        self.assertEqual(included["amount"]["value"], "8.76")
        self.assertEqual(included["basis"]["value"], "CAN")
        self.assertEqual(included["inclusion"], "ALREADY_INCLUDED_DO_NOT_ADD")
        self.assertEqual(
            {row["source_term_id"] for row in extraction["page_terms"]},
            {
                row["page_term_ids"][0]
                for row in extraction["source_occurrences"]
            },
        )
        self.assertTrue(all(row["source_line_id"] for row in extraction["page_terms"]))
        self.assertEqual(extraction["source_occurrences"][0]["tiers"][0]["case_price"]["value"], "120.00")
        self.assertNotIn("129.87", json.dumps(extraction))
        self.assertNotIn("56.76", json.dumps(extraction))

    def test_missing_required_split_fee_basis_refuses_the_layout(self):
        page = json.loads(json.dumps(self.fixture["pages"][0]))
        term = next(line for line in page["lines"] if str(line["text"]).startswith("Split case charge"))
        term["text"] = "Split case charge $9.87"
        digest = "3" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([page]), digest, (1,))
        with self.assertRaisesRegex(extractor.ExtractionError, "UNSUPPORTED_SOURCE_LAYOUT"):
            extractor.parse_wright_pages(parsed, digest)

    def test_missing_malformed_oversize_encrypted_and_symlink_sources_fail_closed(self):
        missing = self.root / "missing.pdf"
        with self.assertRaisesRegex(extractor.ExtractionError, "INPUT_PATH_INVALID"):
            self._extract(input_path=missing)
        malformed = self.root / "malformed.pdf"
        malformed.write_bytes(b"NOT-A-PDF")
        with self.assertRaisesRegex(extractor.ExtractionError, "PDF_MAGIC_INVALID"):
            self._extract(input_path=malformed)
        oversized = self.root / "oversized.pdf"
        with oversized.open("wb") as handle:
            handle.truncate(extractor.MAX_INPUT_BYTES + 1)
        with self.assertRaisesRegex(extractor.ExtractionError, "INPUT_SIZE_INVALID"):
            self._extract(input_path=oversized)
        link = self.root / "linked.pdf"
        link.symlink_to(self.input_path)
        with self.assertRaisesRegex(extractor.ExtractionError, "INPUT_PATH_INVALID"):
            self._extract(input_path=link)
        encrypted_info, encrypted_text = _fake_tools(
            self.root,
            self.fixture,
            encryption_value="yes",
        )
        self.pdfinfo, self.pdftotext = encrypted_info, encrypted_text
        with self.assertRaisesRegex(extractor.ExtractionError, "PDF_ENCRYPTED"):
            self._extract()

        for label, encryption_value in (("missing", None), ("blank", "")):
            with self.subTest(encryption_metadata=label):
                self.pdfinfo, self.pdftotext = _fake_tools(
                    self.root,
                    self.fixture,
                    encryption_value=encryption_value,
                )
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    exit_code = extractor.main(
                        [
                            "--input", self.input_path.as_posix(),
                            "--format", "WRIGHT_V1",
                            "--output", (self.root / f"bad-encryption-{label}").as_posix(),
                            "--pages", "1-2",
                            "--pdfinfo", self.pdfinfo.as_posix(),
                            "--pdftotext", self.pdftotext.as_posix(),
                        ]
                    )
                self.assertEqual(exit_code, 2)
                self.assertIn("PDFINFO_INVALID", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())

        for bad_pack in (
            "750mL 0 Pack - ZERO",
            f"750mL {'9' * 5000} Pack - HUGE",
        ):
            with self.subTest(bad_pack=bad_pack[:32]):
                page = json.loads(json.dumps(self.fixture["pages"][0]))
                page["lines"][2]["text"] = bad_pack
                digest = hashlib.sha256(bad_pack.encode()).hexdigest()
                parsed = extractor.parse_poppler_tsv(_tsv_for_pages([page]), digest, (1,))
                result, _ = extractor.parse_wright_pages(parsed, digest)
                self.assertFalse(
                    any(
                        row["printed_code"]["value"] in {"ZERO", "HUGE"}
                        for row in result["source_occurrences"]
                    )
                )
                self.assertTrue(
                    any(
                        row["reason"] == "UNSUPPORTED_OR_MALFORMED_PACK_LINE"
                        for row in result["quarantines"]
                    )
                )

        control_page = json.loads(json.dumps(self.fixture["pages"][0]))
        control_page["lines"][1]["text"] = "Unsafe\u0001Control"
        with self.assertRaisesRegex(extractor.ExtractionError, "TSV_INVALID"):
            extractor.parse_poppler_tsv(_tsv_for_pages([control_page]), "8" * 64, (1,))

        stderr = io.StringIO()
        with (
            mock.patch.object(extractor, "extract_document", return_value=({}, {})),
            mock.patch.object(extractor, "write_bundle", side_effect=OSError("/private/path/leak")),
            redirect_stderr(stderr),
        ):
            exit_code = extractor.main(
                [
                    "--input", self.input_path.as_posix(),
                    "--format", "WRIGHT_V1",
                    "--output", (self.root / "io-failure").as_posix(),
                    "--pages", "1",
                    "--pdfinfo", self.pdfinfo.as_posix(),
                    "--pdftotext", self.pdftotext.as_posix(),
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            stderr.getvalue(),
            "LOCAL_IO_FAILED: bounded local file operation failed\n",
        )
        self.assertNotIn("/private", stderr.getvalue())

        class BrokenStdout(io.StringIO):
            def write(self, value):
                raise OSError("broken stdout")

        minimal_extraction = {
            "source": {"sha256": "0" * 64},
            "source_occurrences": [],
            "quarantines": [],
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(
                extractor,
                "extract_document",
                return_value=(minimal_extraction, {}),
            ),
            mock.patch.object(
                extractor,
                "write_bundle",
                return_value={"manifest_sha256": "1" * 64, "idempotent_replay": False},
            ),
            redirect_stdout(BrokenStdout()),
            redirect_stderr(stderr),
        ):
            exit_code = extractor.main(
                [
                    "--input", self.input_path.as_posix(),
                    "--format", "WRIGHT_V1",
                    "--output", (self.root / "stdout-failure").as_posix(),
                    "--pages", "1",
                    "--pdfinfo", self.pdfinfo.as_posix(),
                    "--pdftotext", self.pdftotext.as_posix(),
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertEqual(
            stderr.getvalue(),
            "LOCAL_IO_FAILED: bounded output emission failed\n",
        )

    def test_identical_bytes_under_another_filename_keep_document_and_occurrence_identity(self):
        first, _ = self._extract()
        alias = self.root / "renamed-fabricated.pdf"
        alias.write_bytes(self.input_path.read_bytes())
        second, _ = self._extract(input_path=alias)
        self.assertNotEqual(first["source"]["observed_filename_alias"], second["source"]["observed_filename_alias"])
        self.assertEqual(first["source"]["document_id"], second["source"]["document_id"])
        self.assertEqual(
            [row["source_occurrence_id"] for row in first["source_occurrences"]],
            [row["source_occurrence_id"] for row in second["source_occurrences"]],
        )

    def test_changed_bytes_same_filename_change_identity_and_cannot_overwrite(self):
        first, coverage = self._extract()
        output = self.root / "bundle"
        extractor.write_bundle(output, first, coverage)
        self.input_path.write_bytes(self.input_path.read_bytes() + b"CHANGED")
        changed, changed_coverage = self._extract()
        self.assertNotEqual(first["source"]["document_id"], changed["source"]["document_id"])
        with self.assertRaisesRegex(extractor.ExtractionError, "OUTPUT_DRIFT"):
            extractor.write_bundle(output, changed, changed_coverage)

    def test_unsupported_native_text_page_is_fully_partitioned_without_all_clear(self):
        page = {
            "physical_page": 1,
            "lines": [
                {"text": "FABRICATED IMAGE-ONLY REGION", "left": "54.00", "top": "70.00", "width": "180.00", "height": "12.00"},
                *self.fixture["pages"][0]["lines"][-3:],
            ],
        }
        digest = "4" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([page]), digest, (1,))
        extraction, coverage = extractor.parse_wright_pages(parsed, digest)
        self.assertEqual(extraction["source_occurrences"], [])
        self.assertEqual(len(coverage["pages"][0]["coverage_partition"]), 4)
        self.assertEqual(
            coverage["pages"][0]["coverage_status"],
            "UNVERIFIED_NO_SUPPORTED_BLOCK",
        )
        self.assertGreaterEqual(coverage["quarantine_count"], 2)
        self.assertIn("NEVER AN ALL-CLEAR", coverage["coverage_statement"])

        unsafe = json.loads(json.dumps(self.fixture["pages"][0]))
        unsafe["lines"][1]["text"] = "<script>Fabricated Citrus</script>"
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([unsafe]), "9" * 64, (1,))
        unsafe_result, _ = extractor.parse_wright_pages(parsed, "9" * 64)
        self.assertFalse(
            any(
                row["printed_code"]["value"] == "0012-A"
                for row in unsafe_result["source_occurrences"]
            )
        )
        self.assertTrue(
            any(
                row["reason"] == "UNSAFE_MARKUP_LIKE_SOURCE_TEXT"
                for row in unsafe_result["quarantines"]
            )
        )

    def test_absent_null_blank_zero_false_and_faulty_collapsing_transform_are_detected(self):
        extraction, _ = self._parsed_fixture()
        regular = next(
            row for row in extraction["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(regular["source_edition"]["state"], "ABSENT")
        missing = next(
            row for row in extraction["quarantines"]
            if row["reason"] == "MISSING_PRINTED_CODE"
        )
        self.assertEqual(missing["evidence_fields"]["printed_code"]["state"], "BLANK")
        self.assertEqual(extractor._explicit_null("null")["state"], "EXPLICIT_NULL")
        self.assertIsNone(extractor._explicit_null("null")["value"])
        self.assertIn("NEVER_INFERRED", extraction["field_state_contract"]["EXPLICIT_NULL"])
        self.assertIs(type(extraction["authority_effects"]["orders"]), int)

        decomposed = json.loads(json.dumps(self.fixture["pages"][0]))
        decomposed["lines"][1]["text"] = "Cafe\u0301 Fabricated Reserve"
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([decomposed]), "b" * 64, (1,))
        normalized_result, _ = extractor.parse_wright_pages(parsed, "b" * 64)
        normalized_row = next(
            row for row in normalized_result["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(normalized_row["description"]["raw"], "Cafe\u0301 Fabricated Reserve")
        self.assertEqual(normalized_row["description"]["value"], "Café Fabricated Reserve")

        literal_null = json.loads(json.dumps(self.fixture["pages"][0]))
        literal_null["lines"][1]["text"] = "null"
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([literal_null]), "f" * 64, (1,))
        literal_result, _ = extractor.parse_wright_pages(parsed, "f" * 64)
        literal_row = next(
            row for row in literal_result["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertEqual(
            literal_row["description"],
            {"state": "VALUE", "raw": "null", "value": "null"},
        )

        contradictory = json.loads(json.dumps(self.fixture["pages"][0]))
        contradictory["lines"][3]["text"] = "1 Case: $101.00 · Per Bottle: $8.00"
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([contradictory]), "c" * 64, (1,))
        contradiction_result, _ = extractor.parse_wright_pages(parsed, "c" * 64)
        contradiction = next(
            row for row in contradiction_result["source_occurrences"]
            if row["printed_code"]["value"] == "0012-A"
        )
        self.assertIs(
            contradiction["tiers"][0]["arithmetic_diagnostic"]["matches_rounding_at_printed_scale"],
            False,
        )

        expected = {"absent": {}, "null": None, "blank": "", "zero": 0, "false": False}
        self.assertEqual(presence_aware_differences(expected, expected), ())
        collapsed = {"absent": None, "null": None, "blank": None, "zero": False, "false": 0}
        differences = presence_aware_differences(expected, collapsed)
        self.assertGreaterEqual(len(differences), 4)
        canonical = json.loads(extractor._canonical_bytes(expected))
        self.assertIn("absent", canonical)
        self.assertIsNone(canonical["null"])
        self.assertIs(type(canonical["zero"]), int)
        self.assertIs(type(canonical["false"]), bool)

    def test_exact_output_replay_preserves_bytes_and_refuses_extra_missing_or_symlink_members(self):
        extraction, coverage = self._extract()
        output = self.root / "exact"
        first = extractor.write_bundle(output, extraction, coverage)
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.iterdir()}
        second = extractor.write_bundle(output, extraction, coverage)
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in output.iterdir()}
        self.assertIs(first["idempotent_replay"], False)
        self.assertIs(second["idempotent_replay"], True)
        self.assertEqual(before, after)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertTrue(
            all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
        )

        extra = self.root / "extra"
        extractor.write_bundle(extra, extraction, coverage)
        (extra / "unexpected").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(extractor.ExtractionError, "OUTPUT_DRIFT"):
            extractor.write_bundle(extra, extraction, coverage)
        missing = self.root / "missing-member"
        extractor.write_bundle(missing, extraction, coverage)
        (missing / "coverage.json").unlink()
        with self.assertRaisesRegex(extractor.ExtractionError, "OUTPUT_DRIFT"):
            extractor.write_bundle(missing, extraction, coverage)
        linked = self.root / "linked-member"
        extractor.write_bundle(linked, extraction, coverage)
        (linked / "coverage.json").unlink()
        (linked / "coverage.json").symlink_to(linked / "extraction.json")
        with self.assertRaisesRegex(extractor.ExtractionError, "OUTPUT_DRIFT"):
            extractor.write_bundle(linked, extraction, coverage)
        wrong_mode = self.root / "wrong-mode"
        extractor.write_bundle(wrong_mode, extraction, coverage)
        (wrong_mode / "coverage.json").chmod(0o644)
        with self.assertRaisesRegex(extractor.ExtractionError, "OUTPUT_DRIFT"):
            extractor.write_bundle(wrong_mode, extraction, coverage)

    def test_child_process_environment_timeout_nonzero_and_output_caps_fail_closed(self):
        python = Path(sys.executable).resolve().as_posix()
        env_tool = self.root / "env-tool"
        _write_executable(
            env_tool,
            f"#!{python}\nimport json,os\nprint(json.dumps(dict(os.environ),sort_keys=True))\n",
        )
        result = extractor._run_bounded(
            (env_tool.as_posix(),), cwd=self.root, timeout_seconds=1, stdout_limit=4096, stderr_limit=4096
        )
        child_environment = json.loads(result.stdout)
        self.assertEqual(child_environment["LANG"], "C.UTF-8")
        self.assertEqual(child_environment["LC_ALL"], "C.UTF-8")
        self.assertFalse(
            any(
                key.startswith(("DATABASE", "PG", "SHOPIFY", "HTTP_PROXY", "HTTPS_PROXY"))
                or "TOKEN" in key
                or "CREDENTIAL" in key
                for key in child_environment
            ),
            child_environment,
        )

        timeout_tool = self.root / "timeout-tool"
        _write_executable(timeout_tool, f"#!{python}\nimport time\ntime.sleep(10)\n")
        with self.assertRaisesRegex(extractor.ExtractionError, "TOOL_TIMEOUT"):
            extractor._run_bounded(
                (timeout_tool.as_posix(),), cwd=self.root, timeout_seconds=0.05, stdout_limit=10, stderr_limit=10
            )
        output_tool = self.root / "output-tool"
        _write_executable(output_tool, f"#!{python}\nprint('x'*1000)\n")
        with self.assertRaisesRegex(extractor.ExtractionError, "TOOL_OUTPUT_LIMIT"):
            extractor._run_bounded(
                (output_tool.as_posix(),), cwd=self.root, timeout_seconds=1, stdout_limit=10, stderr_limit=10
            )
        nonzero_tool = self.root / "nonzero-tool"
        _write_executable(nonzero_tool, f"#!{python}\nraise SystemExit(9)\n")
        with self.assertRaisesRegex(extractor.ExtractionError, "TOOL_NONZERO_EXIT"):
            extractor._run_bounded(
                (nonzero_tool.as_posix(),), cwd=self.root, timeout_seconds=1, stdout_limit=10, stderr_limit=10
            )

    def test_tool_version_is_pinned_and_binary_identity_is_recorded(self):
        extraction, _ = self._extract()
        provenance = extraction["extractor_provenance"]
        self.assertEqual(provenance["pdfinfo"]["version"], extractor.POPPLER_VERSION)
        self.assertRegex(provenance["pdfinfo"]["binary_sha256"], r"^[0-9a-f]{64}$")
        bad = self.root / "bad-version"
        python = Path(sys.executable).resolve().as_posix()
        _write_executable(bad, f"#!{python}\nimport sys\nsys.stderr.write('pdfinfo version 99.0.0\\n')\n")
        with self.assertRaisesRegex(extractor.ExtractionError, "TOOL_VERSION_UNSUPPORTED"):
            extractor._validated_tool(bad, "pdfinfo", cwd=self.root)

    def test_layout_recognition_is_independent_of_hash_and_page_number_but_hash_binding_fails_closed(self):
        page = json.loads(json.dumps(self.fixture["pages"][0]))
        page["physical_page"] = 7
        page_label = next(
            line for line in page["lines"]
            if str(line["text"]).lower().startswith("page ")
        )
        page_label["text"] = "page 91"
        digest = "a" * 64
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([page]), digest, (7,))
        extraction, _ = extractor.parse_wright_pages(parsed, digest)
        self.assertTrue(extraction["source_occurrences"])
        wrong = "0" * 64
        with self.assertRaisesRegex(extractor.ExtractionError, "SOURCE_SHA256_MISMATCH"):
            self._extract(expected_sha256=wrong)

        scattered = json.loads(json.dumps(self.fixture["pages"][0]))
        term = next(
            line for line in scattered["lines"]
            if "split case" in str(line["text"]).lower()
        )
        term["top"] = "500.00"
        parsed = extractor.parse_poppler_tsv(_tsv_for_pages([scattered]), "d" * 64, (1,))
        with self.assertRaisesRegex(extractor.ExtractionError, "UNSUPPORTED_SOURCE_LAYOUT"):
            extractor.parse_wright_pages(parsed, "d" * 64)

    def test_no_27_field_projection_or_operational_effect_is_fabricated(self):
        extraction, _ = self._extract()
        self.assertEqual(extraction["authority_effects"], extractor.ZERO_AUTHORITY_EFFECTS)
        self.assertEqual(extraction["source_kind"], "LOCAL_PDF_BYTES")
        for row in extraction["source_occurrences"]:
            self.assertEqual(row["price_projection_27_field"]["state"], "ABSENT")
            self.assertNotIn("variant_id", row)
            self.assertNotIn("operational_offer_id", row)
        source = TOOL_PATH.read_text(encoding="utf-8")
        self.assertNotIn("DATABASE_URL", source)
        self.assertNotIn("SHOPIFY_ACCESS_TOKEN", source)
        self.assertNotIn("os.getenv", source)

    def test_new_module_floor_is_registered_and_global_floor_remains_sum_derived(self):
        module = "test_supplier_pdf_extraction.py"
        self.assertEqual(runner.REQUIRED_MODULE_MINIMUMS[module], 30)
        self.assertEqual(runner.GLOBAL_MINIMUM_TESTS, sum(runner.REQUIRED_MODULE_MINIMUMS.values()))
        self.assertEqual(runner.REQUIRED_MODULE_MINIMUMS["test_supplier_format_conformance.py"], 9)


if __name__ == "__main__":
    unittest.main()
