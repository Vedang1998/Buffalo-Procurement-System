# Offline Wright PDF Review Extraction Prototype

**Status:** developer-tool prototype; review-only; not approved or import-ready

**Chosen remediation base:** frozen prototype final head
`78c862b6d64eab7a27ab0ada8eef0c881af18032`, tree
`e6ac89966509bc182250833dc8961aa6727b246d`

**Branch:** `codex/wright-description-scope-remediation`

The original `codex/offline-supplier-extraction-prototype` history, frozen
implementation `e2bc8e18a5a4855552e02090ff4bb345e7cf117b`, and first held-out
result remain historical evidence and are not rewritten by this correction.

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

The only registered source grammar remains `WRIGHT_V1`. The public artifact
shape is explicitly versioned as `BUFFALO_SUPPLIER_PDF_REVIEW_V2`,
`BUFFALO_SUPPLIER_PDF_COVERAGE_V2`, and
`BUFFALO_SUPPLIER_PDF_REVIEW_SHA256_V2`; those names describe the corrected
evidence interface, not a new supplier grammar. The parser recognizes a bottom-aligned,
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

The V2 field-state contract distinguishes `ABSENT`, `EXPLICIT_NULL`, `BLANK`,
`UNRESOLVED`, and `VALUE`. Wright V1 never infers explicit null from silence. A
missing visible code position is retained as `BLANK`; `ABSENT` means only that
the supported grammar did not establish a value, not that the real-world fact
or note does not exist. `UNRESOLVED` retains source evidence whose positive
value or scope is not established. Zero and false remain typed values. Exact
null is serializable only when a future supported grammar has a literal null
token.

`scoped_notes` is always a state-bearing object. A clean supported block with
no recovered note is `ABSENT` with
`NO_SCOPED_NOTE_VALUE_ESTABLISHED_BY_SUPPORTED_GRAMMAR`; the narrow, exact
two-line variety-list grammar is `VALUE` with source-linked items; damaged or
competing title/note candidates are `UNRESOLVED`. An empty list is never used
to assert that notes or restrictions are absent.

## Fail-closed block and coverage rules

Parsing is page-local and column-local. Exactly one clean candidate immediately
before a pack may be a positive description. The existing two-line variety
form is supported only when its exact grammar proves one description plus one
list note. Every other multi-line cluster is
`DESCRIPTION_CANDIDATES_AMBIGUOUS_OR_DISPLACED`: no nearest-line, keyword,
confidence, brand, or lookup rule chooses a title. The whole candidate block is
quarantined with raw candidate text, coordinates, evidence hashes, a
`WRT-CANDIDATE-*` identity, and explicitly partial pack/code/tier evidence.

The first line after a pack must begin the contiguous tier ladder. Any
intervening line is a hard stop even if it looks harmless and contains no
currency or recognized vocabulary. A nearby line after a ladder is unresolved
occurrence-adjacent evidence unless it is deterministically part of a later
product block; page-wide, other-column, cross-page, or otherwise unassociated
text remains at the broader page/column scope. Allocation, territory/state,
channel, minimum-order, availability/while-supplies-last, split, handling, and
assortment language, plus damaged native text, refine diagnostics only. Lack
of a recognized word never makes a positional line safe.

Source lines are claim-once: an overlap becomes a quarantine and cannot be
silently reassigned to another occurrence. References do not create a second
claim. No cross-page inheritance is possible. Quarantine identities bind the
ordered claimed line IDs, page, scope, reason, and anchor so separate records
cannot collide merely because they share a reason.

Every extracted line has exactly one coverage category. Proven occurrence
lines are `SUPPORTED_OCCURRENCE`; exact footer/decorative lines are
`BOILERPLATE`; everything else is explicitly quarantined. Each retained
occurrence has an evidence status plus typed adjacent unresolved references.
Each extraction page contains the same occurrence/block/broader scope refs as
its coverage-page counterpart, and retains the full raw lines and quarantine
details so `extraction.json` alone reveals uncertainty. A page with no
supported native price block is marked `UNVERIFIED_NO_SUPPORTED_BLOCK`.
Successful output therefore means only that bounded review evidence was
produced; it never means that a page, book, identity, term, price, or mapping is
approved or complete.

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
and `SHA256SUMS.json`. The publisher validates the exact V2 member formats and
complete V2 field-state contract before serialization. Exact bytes and
permissions replay idempotently without changing mtimes. Missing, extra,
linked, differently permissioned, changed, or old-V1 output fails closed and
is never overwritten; old hashes are not repurposed. Canonical bytes contain
no timestamp, duration, absolute input path, or credential. CLI errors use
stable sanitized codes rather than printing private paths.

## Tracked validation

`test_supplier_pdf_extraction.py` uses one explicitly fabricated, source-free
JSONL row and test-owned fake Poppler executables. It exercises the full
snapshot/process/TSV/parser/publisher path while stating that no real-PDF
acceptance is claimed. Its 30 tests cover all previous cases plus the bounded
B-1/B-2 correction. They cover:

- both supported grammars, exact codes, tiers, source identities, raw versus
  normalized text, and complete line partitioning;
- missing and reused codes, changed packs/qualifiers, leading zeros/suffixes,
  threshold versus price differences, deepest-tier preservation, nested pack
  dimensions, contradictory arithmetic, and retail-pack diagnostics;
- gift/special/assorted/combo/component scope, same-column interruption,
  cross-page isolation, unsafe markup/control text, missing fee basis,
  malformed/zero/oversized numeric layouts, and unsupported-page quarantine;
- clean award-line displacement, multiple plausible descriptions, a plain
  single-description control, and fail-closed candidate-block evidence without
  moving the genuine title to boilerplate;
- explicit note-state absence, exact recovered variety text, and damaged or
  ambiguous unresolved notes without an inferred null;
- harmless-looking, damaged, allocation, territory/state/channel,
  minimum-order, availability, split, handling, and assortment text before a
  pack, between pack and ladder, and after a ladder, independent of currency;
- occurrence/block versus page/column boundaries, unrelated neighboring
  products, mirrored extraction/coverage refs, reference resolution, and
  claim-once/disjoint line accounting;
- distinct separately charged and inclusive split-fee evidence without fee
  addition or double counting;
- absent/null/blank/zero/false representation, including a deliberately lossy
  comparison transform that must be rejected;
- missing/malformed/oversized/encrypted/linked input, pinned tool versions,
  child timeout/nonzero/output limits, minimal child environment, stable local
  I/O errors, deterministic identity under rename, changed-byte identity, and
  atomic replay, explicit V2 manifest/member validation, old/new drift refusal,
  and preservation of old output bytes and mtimes; and
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

The original parser was frozen at `e2bc8e18a5a4855552e02090ff4bb345e7cf117b`,
tree `1ad45f20024d1529be2ec98f07fab384e5ff504a`, before the held-out
pages were run. The single first-run evaluation matched five of six blocks.
`HOLD-01` matched its page, occurrence, description, pack, code, tier,
arithmetic, and trace fields but retained zero of two visually expected award
notes. The PDF native-text layer exposed those custom-font notes only as
fragmented damaged glyph strings across several line/column records. Those
records remain quarantined; the parser did not guess or reconstruct them. The
first-run mismatch is preserved and no held-out tuning or rerun occurred.

That custom-font diagnosis explains the observed private failure but is not a
complete statement of the risk. Independent review supplied a clean readable
counterexample: placing `Gold Medal 2025 San Francisco Competition` between a
genuine title and its pack caused the frozen parser's unconditional
`cluster[-1]` choice to emit the award as the positive description, return
`scoped_notes: []`, and quarantine the real title as a generic source line.
The canonical source-free old-output reproduction has SHA-256
`6c4270b699f0c2c2b5f5597364450eebdaa28bbb1165191c5cef30ad6a4ba1a6`.
The V2 correction treats that block as ambiguous/displaced and preserves the
code, pack, and tiers only as explicitly partial review evidence.

The historical result remains `REAL_PDF_ACCEPTANCE_PARTIAL`, not a
Wright-format or whole-book pass. Any correction rerun of those pages is
regression/development evidence, never a second blind held-out result; correct
quarantine may be a safety PASS while expected field extraction remains
incomplete. No gold row may be changed to turn quarantine into extraction or
promote 5/6 to 6/6.

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
