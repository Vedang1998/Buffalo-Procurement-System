# Real V5 Package Acceptance Adapter Design

Status: owner-approved bounded follow-on design for offline implementation and
validation. This design creates no commercial, mapping, pricing, import,
Shopify, database, deployment, or purchase-order authority.

Adapter identifier: `V5-DAYTIME-A1`

## Starting boundary

The immutable completed checkpoint is
`6528bc69b1a49c786d7a61fbf293989b6ec093f4`, tree
`26670893ead77e49627693fca9b7e27108751a0b`. Work occurs only on child branch
`codex/supplier-mapping-real-package-acceptance`. The completed branch and its
historical K0-K7 evidence remain unchanged.

The genuine package has these code-owned transport pins:

- package ID `BUFFALO-V5-PORTABLE-DAYTIME-A1-7260a2448f83`;
- raw root SHA-256
  `4eef2d6cfe6b89c94804a749be89d4848d48379bff1521f66cfb42d60d599f82`;
- seal SHA-256
  `02e81308aad9a242a9606d3b53598a625032dff00e21f61dcd7f8980c3ddc4cc`.

The replacement R1 transfer identity is separate transport evidence:
`386,533,606` bytes and SHA-256
`5f8d07805f9cd83bf4421f6b355062188b04550d18adb207f60672b7b6604bce`.
The unavailable original outer transfer ZIP hash is not an application
acceptance condition. Raw-byte hashes remain distinct from typed-row and
canonical JSONL hashes.

The unchanged checkpoint reader rejects the real root as
`NONCANONICAL_MANIFEST` because it requires canonical JSON serialization. The
real root's exact raw bytes are nevertheless independently pinned. The adapter
must accept only those exact bytes, not weaken canonical-manifest validation.

## Considered approaches

1. **Exact pinned adapter selected.** Hash the bounded raw root before parsing.
   Dispatch to `V5-DAYTIME-A1` only when the raw digest matches the immutable
   code-owned pin, then apply code-owned schemas, counts, relationships, and
   authority rules. Preserve the existing canonical synthetic portable route,
   V5 diagnostic route, V4.1 route, and V1 route unchanged.
2. **Canonicalize or rewrite the input, rejected.** Reserializing the root or
   seal would discard the received-byte identity and turn a transport mismatch
   into an apparent pass.
3. **Generic/noncanonical fallback, rejected.** Letting arbitrary manifests or
   callers supply trust pins, schemas, joins, or policy would make the input its
   own validator and weaken `NONCANONICAL_MANIFEST` globally.

## Format dispatch and trust boundary

The directory source remains mandatory. Before interpreting a noncanonical
root, the reader performs the existing bounded byte read, computes SHA-256, and
looks it up in a closed code-owned adapter registry. Only the exact A1 root is
eligible. Any byte change, unknown digest, mixed root, wrong package ID, or
wrong seal is refused.

After exact raw-root dispatch, JSON parsing retains duplicate-key, type, depth,
number, field-size, and UTF-8 controls. The adapter verifies the exact root and
seal schemas, the exact package/revision/authority identity, every declared
archive and embedded member, and zero mapping/price/import/operational authority.
The package cannot select validation policy. Package-supplied Python is never
imported or executed; the supplied restorer remains only a separately labelled
comparison control.

Archive processing remains streamed and bounded. The adapter rejects duplicate
or case/Unicode-ambiguous members, unsafe paths, links/special files, encryption,
undeclared or missing members, truncation, compression/expanded-byte/record
limits, overlapping or gapped parts, and raw/canonical hash drift.

## Restoration and relationship validation

The adapter reconstructs all 120 effective logical tables from the sealed
structured shards plus semantic addendum without changing source bytes. It
preserves each row as typed JSON evidence, including absent keys versus explicit
null, empty string, zero, and false; exact textual identifiers; leading zeros;
and suffixes. An explicitly reviewed null never falls back to an older inferred
conversion.

Code-owned validations independently derive and reconcile the real-package
controls rather than trusting summary claims:

- 120 tables and 746,048 records;
- 2,684 embedded files;
- 2,000 original catalog variants and a separate 2,003-ID historical census;
- 2,000 review cards;
- 714 active requirements across 475 variants;
- 20 retained owner decisions;
- 460 complete combos;
- 80,661 normalized review rows with exactly 27 fields;
- 25 additive source overlays and seven sibling-offer reviews.

Relationships are validated by exact typed keys and documented cardinalities.
Ordinary supplier offers remain distinct from the 936 comparison-parent records
used by 3,765 change-report rows. Standard, gift, alternate-pack, conditional,
and combo offers remain distinct; combo components, rejected candidates, source
occurrences, owner answers, historical scope, unknowns, and unresolved
requirements remain reachable. No preferred offer, mapping, disposition, price,
or retirement is inferred.

## Separate acceptance results

The result model and report expose these independently:

1. replacement/transport and raw-file integrity;
2. complete effective-snapshot restoration;
3. structural and relationship validation;
4. original PDF availability and direct byte-hash verification;
5. availability of each of the three declared page-image bundles;
6. original V4-to-V5 historical patch replay;
7. semantic/commercial review;
8. mapping approval, price approval, and import readiness.

The complete effective snapshot may pass without the old V4 baseline. Original
historical patch replay remains unavailable without its exact inputs. The ten
received PDFs are checked directly against root declarations. Missing original
page bundles remain missing; freshly rendered PDF pages cannot substitute for
their bytes. Hash verification is not printed-price review. Owner product
clarification is not mapping approval. The terminal authority remains
`REVIEW ONLY / NOT_APPROVED / NOT_IMPORT_READY`.

## Offline review interface

The report adds bounded, escaped full-catalog search and drill-down. A reviewer
can locate any original catalog Variant and inspect every related supplier code,
package alternative, conversion, price-ladder evidence, source reference,
rejected match, owner clarification, and unresolved requirement. Search never
selects or ranks a preferred offer.

The implementation uses static local evidence only. It escapes all untrusted
text, rejects unsafe link targets, permits only verified local evidence
references, and contains no executable package script, remote URL navigation,
approval control, persistent decision route, operational route, or public
listener. Chromium acceptance runs with external networking disabled and labels
`file://` rendering separately from any loopback-only started-server exercise.

## Regression and refusal proof

Fabricated public fixtures cover the adapter mechanism without embedding real
commercial data or exposing a caller-controlled production trust pin. Focused
tests prove the demonstrated noncanonical-root compatibility and refusal of:

- wrong/mixed roots, altered bytes, and missing shards/addendum;
- duplicate/ambiguous members, unsafe paths, and symlinks;
- truncation, decompression, aggregate, row, field, and record limits;
- wrong types, duplicate IDs, invalid joins, and explicit-null loss;
- gift/combo/rejected-candidate loss;
- interrupted output, output drift, and overwrite attempts;
- unescaped display, unsafe links, and external requests.

Tests intended for deeper structural rules use a code-owned fabricated adapter
fixture so they pass the initial exact-root dispatch and genuinely reach those
rules. The real package is exercised privately. Identical replay must be
deterministic and an existing exact report is accepted only as idempotent replay;
prior differing output is never overwritten.

Validation proceeds as focused pure tests, authoritative full suite and startup
tests on the frozen material candidate, private real-data controls, offline
Chromium acceptance, and independent read-only review. Any database-backed
suite uses only the existing disposable loopback PostgreSQL 16 `_test`
infrastructure; this adapter itself should remain database-free.

## Completion boundary

Allowed changes are limited to the offline reader/validator/report/CLI, public
fabricated regressions, and necessary handoff/status documentation. Commercial
inputs, PDFs, real rows, traces, and rendered reports stay outside Git.

No main merge, new PR, PR #23 mutation/retry, G10 change, deployment,
operational database access, Shopify activity, SKU writeback, persistent mapping
authority, mapping/price approval, CURRENT activation, supplier communication,
or orders are part of this design. The existing six persistent-design owner
decisions remain open and are reported without implementation.
