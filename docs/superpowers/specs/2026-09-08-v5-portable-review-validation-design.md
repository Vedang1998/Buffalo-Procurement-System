# Supplier Mapping V5 and Portable Review Validation Design

Status: owner-authorized implementation design for the Daytime offline contract.
This work is review-only. It creates no catalog, mapping, pricing, purchase-order,
Shopify, deployment, or database authority.

## Context and authority boundary

Night 2 established a bounded, fail-closed reader for the V4.1 clarification
delta plus deterministic review JSON/HTML. The supplied Daytime private kit adds
an actual V5 changed-tables package, an exact copy of the V4.1 delta, a 17-row
locator correction overlay, a synthetic developer packet, and a proposed
portable snapshot transport. The exact V4 baseline, original supplier PDFs, and
three source-page shard archives are absent. Therefore the actual private V5
package can be structurally and cryptographically validated, but cannot be
represented as a complete source-backed replay or as import-ready.

Shopify Variant ID remains canonical identity. Supplier codes, offer IDs, Tier
IDs, and source occurrences remain evidence. Every output is labelled REVIEW
ONLY / NOT IMPORT READY, and every activation/write authority remains false.

## Considered approaches

1. **Explicit V5 and portable adapters in the existing reader (selected).**
   Dispatch on a code-owned format/version contract, retain the existing public
   `ReviewPackage` and report flow, and add explicit lineage/readiness facts.
   This is the smallest extension that preserves all V4.1 behavior and prevents
   a package from defining its own validation policy.
2. **A generic declarative manifest engine.** This would reduce repeated parser
   code, but it would let untrusted package metadata choose schemas, join rules,
   stable keys, or allowed patch fields. That conflicts with fail-closed review.
3. **Translate V5 into the old V4.1 representation.** This would reuse more code,
   but loses the 17 locator corrections, positional normalized-row semantics,
   V5 cohorts, relationship tables, and source-page lineage.

## Package formats and dispatch

`read_review_package()` keeps V1 and V4.1 behavior unchanged and adds explicit
dispatch for:

- `PACKAGE_FILE_HASHES.json` object with literal `version == "V5"`;
- `BUFFALO_REVIEW_PACKAGE_ROOT.json` with literal
  `format == "BUFFALO_PRIVATE_REVIEW_SNAPSHOT_V1"`.

The public API adds an optional `prior_delta_path` argument. It does not silently
search the filesystem. The CLI exposes the same input as `--prior-delta`; the
existing `--baseline` continues to mean the exact V4 baseline. Unknown versions,
fields, logical tables, or archive roles fail closed.

The V5 adapter owns exact schemas, stable keys, patch actions, allowed fields,
row counts, control totals, and relationships in source code. Package claims are
evidence to verify, never authorization. It verifies all declared files against
the same bytes that are parsed and preserves unknown row fields only where the
code-owned contract expressly permits them.

## V4 to V5 replay order

The only complete lineage is:

1. exact V4 baseline tables;
2. exact original V4.1 delta;
3. exact 17-record locator correction overlay applied only to V4.1 patch
   locators, with base-row number, unique-row count, old key, corrected key,
   before-row hash, and table identity all matched;
4. exact nine V5 patch streams and their typed/relationship sidecars;
5. effective row-count and final-table hash verification;
6. a second replay proving idempotency.

The 308 normalized review patches use
`normalized_table_row_number_1based` as positional metadata. It is never added as
a 28th normalized-table field. The linked normalized table must establish the
corresponding `projection_row_id` and Tier/source evidence.

When the V4 baseline is missing, the reader still validates the complete supplied
V5 envelope, tables, controls, patches, and V4.1/locator linkage, but returns
`BASELINE_REQUIRED`. When source PDFs or source-page shards are absent, it also
records `SOURCE_EVIDENCE_REQUIRED`. Neither condition is converted into PASS by
hashes supplied inside the same package.

## Portable snapshot transport

The portable delivery root is a private directory containing:

- `BUFFALO_REVIEW_PACKAGE_ROOT.json`;
- a separate outer seal file;
- ordinary ZIP shards named by the root manifest.

The code-owned root contract binds snapshot identity, lineage, periods, cohort
controls, logical table names, part ordering, row ranges, exact row counts,
uncompressed byte counts, member paths, and SHA-256 values. The outer seal binds
the canonical root-manifest bytes and every shard archive's bytes. It is an
integrity boundary, not a signature or business approval.

Validation first checks bounded root/seal metadata and shard byte hashes, then
streams each ZIP member and canonical JSONL record. It rejects missing, extra,
overlapping, reordered, duplicated, or gapped parts; path traversal; absolute or
backslash paths; links; encrypted entries; case collisions; nested archives;
duplicate JSON keys; non-finite or unbounded numbers; invalid UTF-8; oversized
records/fields/depth; excessive compression; and row/byte/count/hash drift.
Publication remains atomic and no-replace.

Large logical tables may be split into 25–40 MiB uncompressed parts. Security
limits are code-owned and sized for the known V5 envelope, not derived from the
package. Validation reports measured elapsed time and peak resident memory for
the synthetic scale case. Real V5 validation never needs the absent V4 archive
unless full replay is explicitly requested.

## Readiness and report model

Readiness is represented as separate facts:

- package/envelope integrity;
- structural replay readiness;
- source-evidence readiness;
- semantic review readiness;
- business approval/import readiness.

This prevents a structurally valid package from being called approved or
source-proven. The actual private package remains `BASELINE_REQUIRED`, with all
mapping, price, import, Shopify, database, and PO authority flags false. A fully
synthetic portable chain may be `STRUCTURED_REPLAY` but is still REVIEW ONLY.

V5 reporting projects `variant_offer_relationships_v5` and related sidecars into
the existing occurrence/family model without merging alternatives. It preserves
vendor, supplier-code, source occurrence/page/hash, package/physical/retail unit,
territory, period, conditional assortment, fixed-combo, gift/alternate,
dependency, rejected-memory, catalog guard, owner-decision, and authority facts.

Each review batch and offer preview gets a deterministic fingerprint over:

- exact package/root/seal identity and lineage;
- Variant/vendor/offer/source-occurrence identity;
- source hashes/pages and Tier IDs;
- complete normalized commercial and conversion evidence;
- blockers, prerequisites, cohort and dependency facts;
- all false authority flags.

The concise HTML renders bounded tables and links fingerprinted evidence; the
canonical JSON retains full machine detail. Spreadsheet-leading text is escaped.
No report contains a write/apply/activate action.

## Monthly comparison

Comparison requires both snapshots to be structurally complete and compatible
in supplier scope, period semantics, identity contract, and lineage. Otherwise it
returns `NOT_COMPARABLE` with exact reasons and no invented change claims.

Compatible snapshots classify occurrence-level additions/removals, code changes,
same-vendor SKU reuse, explicit replacement evidence, offer/program changes,
standard/gift/alternate packaging, fixed and conditional assortment changes,
Tier additions/removals/changes, source/provenance drift, catalog guard changes,
rejected-match recurrence, owner-decision staleness, and missing-versus-retired
states. Candidate vocabulary/alias transitions remain review candidates only;
they never mutate identity.

Eight synthetic layouts and twelve simulated monthly cases provide deterministic
known answers. The actual partial V5 package is exercised, but is not presented
as an actual month-over-month result without a compatible prior snapshot.

## Validation and evidence

Tests cover unchanged V1/V4.1 behavior; exact V5 counts, hashes, types, joins,
locator overlay, patch replay, positional normalized rows, and authority zeros;
portable complete/missing/extra/reordered/corrupt parts; archive and JSON attacks;
large synthetic scale/memory; report fingerprints; multi-offer semantics; and
monthly comparison compatibility/classifications.

Validation runs are intentionally bounded: focused tests during implementation,
one startup check, and one authoritative full suite after the candidate freezes.
The actual private partial package and complete synthetic chain are exercised via
the real CLI and local browser/HTTP review surface where supported. Raw private
packages, reports, traces, and manifests stay outside Git under the private
evidence root. Public Git receives only code, synthetic fixtures, tests, and
sanitized documentation.

Independent read-only review follows machine validation. Any P0/P1 is remediated
and re-reviewed before the Daytime checkpoint. Missing baseline/source evidence,
policy approvals, deployment, operational data, and authenticated private access
remain explicit blockers rather than guessed completion.
