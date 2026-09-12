# Offline Wright PDF Review Extraction Prototype

**Status:** developer-tool prototype; review-only; not approved or import-ready

**Chosen base:** locally green integration closeout
`5f86eee6c1954ed35cee72fcfa123b75256295b5`, tree
`28bd811b2c4a692c9be812575a3d725adc0a7eb5`

**Branch:** `codex/offline-supplier-extraction-prototype`

## Purpose and authority boundary

This prototype turns explicitly selected pages of a local Wright-format PDF
into deterministic JSON review evidence. It is a developer tool, is not
imported by production startup or routes, and has no database, network,
Shopify, supplier, approval, pricing, purchasing, or order integration.

Every output is labeled:

`REVIEW_ONLY / NOT_APPROVED / NOT_IMPORT_READY`

The input document is untrusted source evidence, never authorization. The
prototype does not create Variant IDs, supplier offers, mapping decisions,
selected offers, approved prices, aliases, recommendations, or purchase
orders. It emits no 27-field price projection because canonical Variant,
approval, target-state, date, and unit requirements are not established by a
printed page.

## Finite implemented scope

The only registered format is `WRIGHT_V1`. It recognizes a bottom-aligned,
three-part Wright footer consisting of one unique domain-bearing
supplier-format identity line, a bounded split-fee statement, and a visible
printed-page label. Those facts
must be three distinct source lines in the footer region with the expected
left-to-right geometry. The fee amount is parsed rather than hard-coded and
remains page-scoped, source-linked evidence. Both a separately charged fee and
an explicitly inclusive fee can be represented without adding either amount
to an item price.

Two product grammars are supported:

- a regular physical-unit ladder with printed size, case pack, optional
  qualifier, code, ordered case thresholds, case price, and per-unit price;
- a nested retail-multipack ladder with printed outer and inner counts, size,
  optional container word, code, ordered thresholds, case price, physical-unit
  price, and optional retail-pack price.

Gift, special, assorted, fixed-combo, component-allocation, ambiguous,
code-less, damaged, and other layouts remain unsupported. Their evidence is
quarantined rather than coerced into the two supported grammars. The tool does
not claim whole-book coverage.

## Evidence and identity model

A source occurrence is a printed occurrence, not an operational offer. Its
stable ID hashes the format, document hash, physical page, and exact claimed
source-line span. Repeated printed occurrences are not deduplicated by code.
Every tier and page term has its own content-bound source identity and source
line reference. Codes remain text so leading zeros and meaningful suffixes
survive.

Raw extracted line text and token coordinates are retained separately from an
NFC/whitespace-normalized comparison value. Money remains exact decimal text;
arithmetic diagnostics use `Decimal`, preserve the printed figures, and never
substitute a computed value. Regular and nested-retail divisors, retail-pack
arithmetic, and printed container/per-unit consistency are diagnostics only.

The field-state contract distinguishes `ABSENT`, `EXPLICIT_NULL`, `BLANK`, and
`VALUE`. Wright V1 never infers explicit null from silence. A missing visible
code position is retained as `BLANK`; a field not printed or not representable
is `ABSENT`; zero and false remain typed values. Exact null is serializable
only when a future supported grammar has a literal null token.

## Fail-closed block and coverage rules

Parsing is page-local and column-local. A description must be immediately
adjacent to its pack line, and the first line after the pack must begin the
contiguous tier ladder. Any intervening pack, tier, heading, damaged, or
unclassified line is a hard stop. Source lines are claim-once: an overlap
becomes a quarantine and cannot be silently reassigned to another occurrence.
No cross-page inheritance is possible.

Every extracted line has exactly one coverage category. Proven occurrence
lines are `SUPPORTED_OCCURRENCE`; exact footer/decorative lines are
`BOILERPLATE`; everything else is explicitly quarantined unless a future
narrow non-price rule proves it safe. A page with no supported native price
block is marked `UNVERIFIED_NO_SUPPORTED_BLOCK`. Successful output therefore
means only that bounded review evidence was produced; it never means that a
page, book, price, or mapping is approved or complete.

## Bounded local interface

The CLI requires absolute input, output, `pdfinfo`, and `pdftotext` paths; the
format; an explicit selected-page list/range; and optionally the expected PDF
SHA-256. It:

1. rejects traversal, symlink components, non-regular input, malformed magic,
   encryption, excessive size/pages/page selection, and source-hash mismatch;
2. copies the input through a no-follow descriptor into a private snapshot,
   checks stable metadata before and after, and parses only that snapshot;
3. requires self-reported Poppler 25.07.0, records each resolved executable's
   binary hash, and uses fixed argv with a minimal environment; the hash is
   provenance, not an executable allowlist;
4. bounds child duration and incremental stdout/stderr, kills and waits for
   only its owned process group on failure, and caps words, lines,
   occurrences, tiers, and canonical output;
5. parses strict UTF-8 TSV without evaluating source content, hyperlinks,
   macros, markup, formulas, or instructions; and
6. atomically publishes mode-0700 output containing three mode-0600 files.

The output directory contains canonical `extraction.json`, `coverage.json`,
and `SHA256SUMS.json`. Exact bytes and permissions replay idempotently without
changing mtimes. Missing, extra, linked, differently permissioned, or changed
output fails closed and is never overwritten. Canonical bytes contain no
timestamp, duration, absolute input path, or credential. CLI errors use stable
sanitized codes rather than printing private paths.

## Tracked validation

`test_supplier_pdf_extraction.py` uses one explicitly fabricated, source-free
JSONL row and test-owned fake Poppler executables. It exercises the full
snapshot/process/TSV/parser/publisher path while stating that no real-PDF
acceptance is claimed. Its 22 tests cover:

- both supported grammars, exact codes, tiers, source identities, raw versus
  normalized text, and complete line partitioning;
- missing and reused codes, changed packs/qualifiers, leading zeros/suffixes,
  threshold versus price differences, deepest-tier preservation, nested pack
  dimensions, contradictory arithmetic, and retail-pack diagnostics;
- gift/special/assorted/combo/component scope, same-column interruption,
  cross-page isolation, unsafe markup/control text, missing fee basis,
  malformed/zero/oversized numeric layouts, and unsupported-page quarantine;
- distinct separately charged and inclusive split-fee evidence without fee
  addition or double counting;
- absent/null/blank/zero/false representation, including a deliberately lossy
  comparison transform that must be rejected;
- missing/malformed/oversized/encrypted/linked input, pinned tool versions,
  child timeout/nonzero/output limits, minimal child environment, stable local
  I/O errors, deterministic identity under rename, changed-byte identity, and
  atomic replay/drift refusal; and
- zero authority effects, no 27-field projection, exact module registration,
  and the sum-derived global test floor.

The existing eight-case/nine-test supplier-format conformance corpus remains
unchanged. It is complementary synthetic contract coverage, not evidence that
this tool parsed a PDF.

## Private source-backed evaluation boundary

The original Wright PDF is private and is not committed. Its source hash,
size, page count, pre-parser visual gold, Poppler output, comparison records,
and manifest hashes are retained outside Git. The deterministic page-grouped
split froze 12 training blocks on six pages and six held-out blocks on three
different pages before tuning.

The latest pre-held-out training evaluation starts with the original PDF
bytes, executes actual Poppler, and matches 12/12 selected projected fields,
plus occurrence/tier/term trace linkage and independently derived
case-to-unit, retail-pack, and container/unit diagnostics. All visual section
headings remain explicitly unresolved native-text references. The six
selected pages also contain additional parser output and quarantines that have
not been promoted to gold-verified facts; the coverage is deliberately partial
and not an all-clear.

The parser was frozen at `e2bc8e18a5a4855552e02090ff4bb345e7cf117b`,
tree `1ad45f20024d1529be2ec98f07fab384e5ff504a`, before the held-out
pages were run. The single first-run evaluation matched five of six blocks.
`HOLD-01` matched its page, occurrence, description, pack, code, tier,
arithmetic, and trace fields but retained zero of two visually expected award
notes. The PDF native-text layer exposed those custom-font notes only as
fragmented damaged glyph strings across several line/column records. Those
records remain quarantined; the parser did not guess or reconstruct them. The
first-run mismatch is preserved and no held-out tuning or rerun occurred.

The result is therefore `REAL_PDF_ACCEPTANCE_PARTIAL`, not a Wright-format or
whole-book pass. If this held-out case is ever used for later tuning, it
permanently becomes development evidence and cannot be called held-out again.

## Deferred adoption work

This branch is not a production-parser proposal. Any later adoption requires a
separate authorization and review for a pinned Poppler provisioning strategy,
more independently verified formats/editions, expanded page/layout coverage,
human extraction verification, canonical Variant/offer linkage, the existing
price contract's missing authority fields, private storage/access policy,
operational service boundaries, and fresh CI on the exact adopted tree.

A second supplier format was not implemented because no eligible second
original PDF/profile pair was proven locally. Synthetic fixtures or a duplicate
archive do not substitute for an independent source.
