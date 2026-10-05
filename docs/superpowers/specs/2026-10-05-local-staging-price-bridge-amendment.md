# Local Staging Price Bridge Amendment

**Status:** owner approved on 2026-10-05  
**Scope:** Task 9 LOCAL staging acceptance only  
**Baseline:** commit `e9f82cd76cead4f432622c26e4907c434bff8761`, tree
`560f765a787b98d3be2ee9c45ed32c4277c3b7bf`

This is an additive amendment to the accepted Railway staging design and plan.
It preserves the historical browser-upload requirement everywhere except the
one LOCAL staging acceptance scenario defined here. The resulting proof is
named **operator-staged input, browser-approved workflow**.

## 1. Authority and non-authority

For this scenario only, a stopped-service operator stages the registered
synthetic replacement fixture through the canonical normalization and
validation pipeline. That operator may create only the exact unapproved
`VALIDATED` candidate and its raw-source, validation, membership, and audit
evidence.

The operator does not gain or exercise price approval, promotion, APPLY,
selection, purchasing review, DRAFT, packet, production, Shopify, supplier,
PO-release, or commercial authority. It cannot create `VERIFIED_FUTURE`,
`APPLY_REPLACEMENT`, alter CURRENT prices, or preseed downstream decisions.
The staging gateway continues to reject bulk upload and exposes no replacement
import route.

Direct CURRENT seeding, post-APPLY fixtures, and an independently transferred
`VALIDATED` checkpoint remain forbidden substitutes for the integrated run.

## 2. Fixed candidate source

The only accepted input is the tracked file
`procurement/config/synthetic_price_replacement_book.csv`, exactly 1,590 bytes
with SHA-256
`00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c`.

The operator entry point accepts no fixture path, fixture bytes, vendor,
declaration, policy, date, clock, database target, storage root, or batch
selection. It resolves the fixture and the dedicated target from code-owned
paths and the same exact, centrally validated staging target configuration used
by the service. A database credential may enter only through the bounded
secret channel defined by the operator composition; it is not business input
and is never printed, returned, persisted, or placed in a URL.

Before any database or storage effect, the operator verifies:

- the exact fixture type, size, bytes, and SHA-256;
- the registered declaration and policy identities;
- the fixed PostgreSQL 16 staging database, schema, runtime role, target IDs,
  transfer marker, catalog, permissions, and canonical controls;
- the exact synthetic persistent-storage root and process identity; and
- the stopped-service lifecycle exclusion described below.

## 3. Lifecycle exclusion

The existing local purchasing database lifecycle advisory-lock contract is the
single cooperative exclusion. Its lock name and database binding remain
unchanged and move behind a shared source-level helper so the legacy launcher,
the staging synthetic service, and the new operator use the same primitive.

The staging synthetic service acquires and holds the session advisory lock
before reporting READY and releases it only during bounded shutdown. Losing or
failing to acquire the lock is a readiness/lifecycle failure. The stopped-
service operator must acquire the same lock before effects and hold it through
all fixture, database, storage, and postcondition checks. Therefore a live or
starting synthetic service makes the operator refuse, and an operator in
progress makes service startup refuse. Merely supplying a `STOPPED` string or
observing one PID is not proof.

This exclusion is staging-only when added to the root-composed service. It does
not change the legacy launcher's lock identity or database behavior.

## 4. Canonical staging transaction

The operator invokes the existing declared staging service with the raw bytes;
it does not insert a prevalidated row or copy precomputed validation results.
The real parser, normalization, database validation, complete-ladder checks,
membership computation, raw content-addressed storage, validation issues,
fingerprints, and audit columns execute normally.

A new fixed non-human principal identifies only this setup action. Its role is
`procurement.price.stage`, distinct from `procurement.price.approve`. The
staging function may accept that role only for candidate staging; confirmation
and APPLY continue to require their existing owner/session-bound approval
principal.

The exact `VALIDATED` result is idempotent. A replay must revalidate the raw
member and immutable evidence and return the same batch without inserting
duplicates. Any conflicting bytes, declaration, policy, batch identity,
partial status, `VERIFIED_FUTURE`, `APPLIED_CURRENT`, or unexpected existing
state refuses without overwrite or authority advancement.

Pre/post evidence proves that CURRENT rows, confirmation/APPLY events,
selection heads, reviews, Monday runs, DRAFTs, packets, unrelated vendor state,
and prior completed artifacts are unchanged. The operator emits only a
credential-free canonical proof containing the contract, source identity,
batch ID, `VALIDATED` disposition, raw/declaration/validation/membership hashes,
and unchanged-state fingerprints.

## 5. Browser and Backup V2 sequence

After operator success, the normal staging service starts. The authenticated
browser must:

1. render the actual `VALIDATED` candidate, its durable and operational states,
   all normalized tiers, and the CURRENT-to-candidate comparison;
2. request confirmation preview and submit explicit `CONFIRM` as the named
   owner session;
3. prove the durable transition to `VERIFIED_FUTURE`;
4. stop the affected service through the same lifecycle boundary;
5. run the real Backup V2 dump/storage/manifest operator;
6. bind the trusted label, manifest SHA-256, and source tree through root
   launcher configuration and restart;
7. reauthenticate and perform browser APPLY; and
8. perform browser mapping, selection, Monday review, quantity edits, DRAFT
   generation, and downloads.

No browser field or request may select a backup path, label, manifest, database,
clock, or recovery proof. Backup V2 remains strictly after confirmation and
before APPLY.

## 6. Registered-observation temporal projection

Declared list/detail evaluation and FUTURE confirmation use the exact
registered `observation_at` and policy timezone after complete synthetic target,
fixture, declaration, and policy attestation. APPLY continues to use the
distinct registered `application_at`; Monday evaluation continues to use the
distinct registered `monday_evaluation_at`.

The generic price-book list/detail functions retain their existing host-clock
semantics byte-for-byte. A declared-only presentation wrapper selects the
registered reference before evaluating temporal status. In a mixed list only
rows with the exact declared replacement contract receive that projection.
Missing or inconsistent registration, database, policy, timezone, or boundary
evidence refuses; it never falls back to host time.

Durable batch state and evaluated operational state remain distinct values.
Declared HTML labels both with stable machine-addressable elements. Confirmation
or APPLY controls render only when both durable and evaluated states match the
required transition. No persisted status is rewritten to mask an incorrect
calculation.

The correction does not alter real/default price evaluation, the host clock,
audit execution timestamps, session/internal-assertion expiry, rate limits,
deadlines, idle timeouts, migrations, catalog identities, or durable data.

## 7. Failure and recovery behavior

All preflight failures occur before staging effects. A deterministic validation
failure remains an unapproved invalid candidate only where the canonical
staging contract already requires retaining that evidence; it is never
promoted. Database serialization/deadlock retry follows the existing bounded
whole-transaction policy. Ambiguous results are recovered only by reattesting,
reacquiring the lifecycle lock, and verifying the exact immutable batch and
postconditions; they are never blindly replayed.

The operator never resets, deletes, repairs, or overwrites conflicting target
state. A failed integrated acceptance blocks acceptance but permits diagnosis
and in-scope repair.

## 8. Required proof

Focused proof precedes the expensive integrated run.

Operator tests use actual PostgreSQL where identity, locking, or transaction
behavior matters and prove:

- exact bytes produce exactly one `VALIDATED` batch through real validation;
- altered bytes/declaration, wrong target/role/catalog/storage, or a live
  service refuses before effects;
- replay is idempotent and cannot advance authority;
- conflicting or already-advanced state refuses;
- CURRENT, confirmation/APPLY events, selection/review, DRAFT/packet, unrelated
  vendor, and prior artifact state remain unchanged; and
- secrets and private/raw evidence never enter logs, argv, output, or Git.

Clock tests prove:

- the reproduced 2026-10-05 host-clock mismatch is corrected by the registered
  2026-09-16 observation instant;
- host-date movement does not change declared results;
- list, detail, and confirmation share the same registered basis;
- invalid observation, timezone, boundary, declaration, or target evidence
  refuses with no controls or writes;
- mixed lists change only declared rows;
- legacy/default temporal behavior and all security clocks remain unchanged;
  and
- the UI exposes separate durable/operational states and suppresses actions on
  any mismatch.

Backup/APPLY tests prove absent confirmation or absent/invalid Backup V2 refuses,
browser input cannot select timing/recovery authority, and the successful
sequence preserves exact raw, batch, event, scope, price, recommendation,
review, DRAFT, and packet lineage.

The integrated run derives and independently reconciles two vendor DRAFTs,
three lines, 14 packet members, $282 merchandise, one $7 fee, and a $289 total.
It then completes restart, backup/restore, artifact/control-total, research
cold-start/concurrency/idle/crash/retry, RSS/cgroup/OOM, cleanup, startup, and
authoritative-suite gates. No result is claimed before its own bound evidence
exists.

## 9. Publication and hard boundaries

Meaningful source checkpoints are scanned for secrets/private data and pushed
non-force to the dedicated staging branch, with the remote tip verified. The
final source identity is frozen before integrated acceptance and receives
attributable independent review of this amendment, the clock change, and
affected runtime/database behavior.

This amendment authorizes no Railway mutation/deployment, public endpoint,
external database access, remote private-data transfer, production access,
main merge, PR #24 change, Shopify call, real purchasing, or Replit shutdown.
