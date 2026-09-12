# Offline Supplier Mapping Review Bridge — Design and Handoff

**Status:** Night 2 implemented offline checkpoint; review-only and not production ready

**Branch:** `codex/supplier-mapping-review-bridge`

**Base:** `88bf800708881e5801a51d0cb165e84e8c8cf198`, tree
`54357c11531018c49bfc4035dfe40d9858c1f788`

## Purpose and authority boundary

This bridge reads untrusted, versioned supplier-mapping review packages and
produces deterministic review evidence. It does not create or approve a
supplier offer, mapping, price, alias, readiness result, recommendation, or
purchase order. Every rendered result is labeled:

`REVIEW ONLY / NOT_IMPORT_READY`

The existing 27-column operational price-book contract, its 5,000,000-byte
upload limit, FUTURE-only rules, database schema, operational views, readiness
gates, and recommendation logic remain unchanged. Large review evidence is a
separate offline format and cannot be routed into the operational importer.

The supplied V4.1 clarification delta is useful evidence but is not a complete
package: the exact V4 baseline and original supplier PDFs are absent. Its
honest terminal classification is therefore `BASELINE_REQUIRED`, even when all
available file hashes and structural checks pass.

## Components

### Package and patch layer

`procurement_os.supplier_review_package` will:

- read a directory or ZIP without extracting it;
- reject absolute/traversal/backslash paths, links and special files,
  duplicate or Unicode/case-colliding names, encryption, excessive nesting,
  oversized entries, excessive expanded bytes, excessive compression ratios,
  rows, fields, field bytes, or JSON nesting;
- stream raw SHA-256, JSONL, and CSV reads under explicit limits;
- validate an explicit V1 package manifest or the supplied V4.1 delta
  `PACKAGE_FILE_HASHES.json` form;
- preserve unknown fields while reporting them;
- preserve native JSON scalar distinctions and exact decimals;
- decode `_csv_cell_types`/type-sidecar values without conflating absent,
  null, empty string, zero, false, or a deliberately protected string;
- distinguish raw-byte hashes from canonical JSONL hashes;
- validate row counts, required dictionaries, and declared joins;
- replay field patches only when the exact baseline bytes, rows, stable keys,
  before-values, allowed fields, record hashes, resulting row count, and
  canonical result hash all prove exact;
- reject delete operations and duplicate patches; and
- return `BASELINE_REQUIRED`, never an invented pass, when prerequisite base
  bytes are unavailable.

The V1 manifest is synthetic-fixture friendly and declares package version,
snapshot identity, review-only authority, cohort controls, file hashes, table
paths/formats/row counts, required/known fields, type sidecars, join rules, and
optional patch policy. It is not an operational import manifest.

### Review and comparison layer

`procurement_os.supplier_mapping_review` will:

- key candidate occurrences by exact Shopify Variant ID, vendor, and source
  occurrence ID—not name, SKU, or price;
- retain all supported, rejected, conflicting, search-lead, policy-excluded,
  and unresolved alternatives;
- keep standard, gift, alternate-case/retail-pack, limited, replacement, and
  fixed-combo programs distinct;
- report physical containers, inner/retail packs, Shopify sellable units,
  order increments, and qualifying BT/CS evidence separately;
- prevent a proposed source candidate from overriding catalog conflict,
  policy exclusion, rejection, or search-lead status;
- keep owner attribute evidence separate from mapping, price, replenishment,
  and historical-sales authority;
- preserve the original 2,000-variant and current 2,003-variant cohorts as
  distinct controls;
- report the 21 representation gaps from supplied evidence without creating
  schema fields; and
- compare two complete supplied snapshots by occurrence and tier identity.

The comparison reports unchanged candidates, additions, omissions (explicitly
not retirements), replacements, simultaneous packages, gift alternatives,
pack/proof/expression/vintage/date/territory changes, SKU reuse, BT/CS threshold
and tier changes, and rejected-match recurrence. Suggested name vocabulary and
alias transitions are always candidates, never approvals.

### CLI and outputs

`procurement/tools/review_supplier_mapping_package.py` will be database-free.
Dry-run is the default and writes nothing. An explicit output option creates an
atomic report directory containing canonical JSON, escaped HTML, and a checksum
manifest. Existing output must match exactly for idempotent replay; drift fails
closed. Interrupted construction cannot publish a partial report.

HTML escapes all content. Spreadsheet-formula-leading text is visibly
protected only at presentation/export boundaries; canonical raw values are not
mutated. Input scripts, formulas, macros, relationships, and embedded objects
are never executed.

## Validation strategy

New pure tests will cover every Night 2 known-answer requirement, including:

- one Variant ID with standard, gift, and alternate-case occurrences;
- identical/reused/replacement SKUs across vendors and simultaneous packages;
- duplicate occurrence versus conflicting commercial facts;
- 120 individual 50 mL bottles versus 12 inner packs;
- 72 individually sold bottles, 12 individually sold cartons, 24 bottles
  versus eight retail three-packs, and 24 cans versus six four-packs;
- historical attribution isolation and non-merging expression conflicts;
- ABV/proof distinctions, fixed combos, reviewed nulls, blocked dispositions,
  blank/contradictory price evidence, per-ounce and split-inclusive evidence;
- a 60 BT to 36 BT threshold change with unchanged price;
- raw/canonical hash, baseline, replay, tamper, atomic-output, type-sidecar,
  leading-zero/suffix, precision, malformed/oversized/traversal, and HTML/formula
  boundary cases; and
- real-delta `BASELINE_REQUIRED` separately from complete synthetic monthly
  comparisons.

Expected values are literal test authority rather than values calculated by
the implementation under test. New test modules are registered at their exact
discovered floors. Final validation includes focused tests, the authoritative
suite, startup hardening, compilation, lock, diff, generated-file, and secret/
forbidden-call scans. PostgreSQL is not required by this bridge.

## Implemented offline checkpoint

The additive reader, report/comparison library, CLI, atomic report publisher,
and two registered pure test modules are implemented on this branch. The
supplied private V4.1 delta validates all 34 members declared by its hash
manifest (35 ZIP members including the manifest itself), reproduces the typed
27-column CSV/JSONL projection, and reports `BASELINE_REQUIRED` because the
exact V4 baseline archive and original supplier PDFs were not supplied. It
does not claim full 80,661-row reconstruction or business approval.

The final implementation checkpoint is
`ecc1835dc025c21c9c0e9c5879328a01b81c04dd`, tree
`6eaf41bfe0eb62afa7cfbd379d13bc1456a8dc11`. Focused validation passed `36/36`
(`21` mapping and `15` package), startup hardening passed `10/10`, and the one
authoritative final suite discovered, executed, and passed `623/623` in
643.625 seconds across 35 registered modules. Failures, errors, skips, expected
failures, and unexpected successes were all zero. Earlier independent reviews
found fail-closed and semantic defects; the exact final five-file snapshot
received a targeted independent PASS, and the strict-completion remediation
received a second targeted independent PASS with no remaining concrete P0/P1.

The additional contract-requested Claude Code review was attempted against the
frozen final documentation head with read/search-only permissions, but Claude
Code exited before repository review because its existing OAuth session had
expired and could not be refreshed. That review remains `REVIEW PENDING`; no
OAuth workaround, plugin installation, or paid API was used.

Private input bytes and rendered commercial evidence remain outside Git. The
final reviewed report bundle is under
`/home/runner/workspace/.ai-auth/codex/evidence/night2-20260908T033550Z/`
as `v4_1-review-only-report-ecc1835-test-data` and is labeled
`REVIEW ONLY / NOT_IMPORT_READY`. Its report JSON SHA-256 is
`7f4f0adb18d0c2f3c99a6dbe26a35de22ea58a9d01c10b752b45313cf43f2fb9`.
Its concise 9,254-byte HTML has six tables, no raw JSON `<pre>`, and SHA-256
`4fb69ebabe2c55f5491d5f993d24b69232fffa440e4c7612a34a70386cfa20ed`.
The nine-record current remediation evidence manifest has SHA-256
`38150950ee5baa7103b6c4cb9e8e525477f77fa0b2d7d4405783fa2695e79f5f`.
Host-local evidence is not claimed to be backed up off-host.

## Deferred integration proposal (not authorized implementation)

The smallest later reviewed application change would add immutable private
review-package metadata and explicit human mapping-decision records keyed by
Variant ID/vendor/source occurrence; a selected offer relationship that still
preserves rejected alternatives; a guarded initial CURRENT bootstrap plus the
already-required FUTURE-to-CURRENT rollover authority; private caller identity
for confidential review/download surfaces; and supplier-specific parser
adapters proven against licensed source fixtures. None belongs in this branch.

## Completion-audit remediation implemented

The completion audit found that structural hash agreement was narrower than
the required review-package authority. The V4.1 adapter now owns an immutable
contract for every patched table: exact stable-key modes, separately enumerated
replace/append fields, protected identity fields, required projection fields,
expected-count evidence, and documented cross-table relationships. A patch
cannot authorize its own changed fields. The normalized 27-column table
deliberately uses external postcondition proof rather than an intrinsic source
key: the same original row number is bound to a nonempty Tier ID by the
validated companion link patch. Duplicate append keys, key mutation/removal,
undeclared fields, unmatched counts, and broken joins fail closed before
mechanical baseline replay.

The report layer materializes all 21 supplied Contract Gaps as explicit
review-only routing records without copying commercial rows into Git. It also
emits deterministic supplier-name vocabulary and alias-transition candidates,
never approvals; recognizes the actual V4.1 pack/program/vintage field names
during comparison; and surfaces stale-owner, missing-source, blank-price, and
contradictory-arithmetic evidence as machine-readable exceptions.

Focused proof now covers those controls, stable keys and allowlists, real-delta
counts/joins, added/missing tiers, exact decimal quantities,
replacement/simultaneous packages, missing supplier evidence, partial-real
versus complete-synthetic comparison, and interrupted atomic publication
cleanup. The exact candidate received one authoritative full regression,
startup/static checks, and targeted read-only re-review. PR #23 and all
operational surfaces remained frozen.

Full source validation remains intentionally unavailable. The supplied kit
lacks the exact V4 baseline archive and original supplier PDFs, and the
baseline schemas needed to prove Tier/source-registry and exposed/effective
projection joins were not guessed. The actual delta therefore remains
`BASELINE_REQUIRED`; even a future twelve-table mechanical replay remains
`SOURCE_EVIDENCE_REQUIRED` until those adapters and source bytes are supplied.
The 21-gap routing catalog is a code-owned mapping derived from the private
Contract Gaps sheet; runtime equality against the full baseline artifact is
explicitly unavailable, not represented as a performed check.

## Packet A checkpoint inherited by this branch

PR #23 remains draft. Its history-preserving integration commit is
`ec71fe9c5a6f13832a8cad65b065be9747010486`, tree
`543be91aee06386b7889a1fc4198eaafe89e74df`; both `88bf800...` and exact main
`f308ac6...` are parents/ancestors. Local integration validation passed
`599/599` plus startup `10/10`. Configured pull-request CI run `34185466802`
passed startup but its full-suite step exited 1 after 959 seconds; detailed job
logs require repository-admin credentials and were not available through the
public API. GitHub's synthetic pull-request merge was
`f7bbb45a31953eccd4ec5016269057e9e2c003d3`, tree
`543be91aee06386b7889a1fc4198eaafe89e74df`, with parents exact main then
`ec71fe9...`; the Actions checkout of that SHA is inferred rather than proven
because its detailed log returned `403`. The run was not retried and is not
represented as green. This bridge is therefore based on the contract-authorized
verified `88bf800...` fallback, not the unexplained failing integration tree.

One process deviation is retained for audit: three read-only child agents were
briefly live simultaneously, above the contract maximum of two, before one was
immediately interrupted. Root remained the sole writer and no child performed
a repository, database, or network mutation. The final independent reviewer
classified the deviation as documentation-only rather than a code-verdict
defect.

The strict completion audit additionally proved the exact G01–G21 workbook
gap sequence and an actual partial-delta `NOT_COMPARABLE` monthly boundary in
private supplemental evidence. It also closed three final public deliverable
gaps: the HTML is now a bounded escaped reviewer summary with machine detail
left in JSON; same-vendor SKU reuse and true serialization/reload are explicit
known answers; and public source no longer hard-codes the private association
between a catalog ID and the not-returned classification. Runtime validation
still preserves exactly one supplied nonblank ID, exact cohort arithmetic, and
the no-retirement invariant.
