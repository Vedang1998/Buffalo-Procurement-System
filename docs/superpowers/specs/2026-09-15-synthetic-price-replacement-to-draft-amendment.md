# Synthetic price replacement to DRAFT — bounded implementation amendment

**Status:** APPROVED DIRECTION / DESIGN REVIEW REQUIRED BEFORE CONSTRUCTION  
**Authority:** `BUFFALO-OVERNIGHT-PRICE-DRAFT-01`, received 2026-09-15  
**Base:** `d7fbb3790374eef4a6a801707de0391e5a476d91`  
**Scope:** owned PostgreSQL 16 `*_test` / `*_demo` databases and loopback UI only

## Outcome and non-authority

This slice proves that one fabricated, complete monthly vendor price book can
be uploaded as raw bytes, strictly normalized, reviewed as a complete FUTURE
scope, separately confirmed by the authenticated synthetic owner, atomically
replace its exact CURRENT scope at a server-owned synthetic effective boundary,
and drive the already-confirmed selected offer through Monday review, DRAFT,
and the existing packet.

It does not approve a real price, create a real bootstrap policy, add
carry-forward/seasonal/irregular/partial-scope behavior, activate an offer,
write Shopify, release or transmit a PO, or close the direct-SQL enforcement
gap for a real selected-offer cutover. Missing replacement leaves the prior
CURRENT rows unchanged. The supported replacement scope is exactly one
complete active, verified, procurement-eligible vendor offer set.

## Release and schema boundary

Add forward-only post-mapping application release
`016_synthetic_price_replacement.sql`, after the exact installed 015 release.
Do not change migrations 011, 014, or 015 or their markers. Extend the release
runner with an explicit predecessor-migration field and family-specific Python
verifier dispatch; publish the 016 marker last. The 016 verifier hashes only
the new/changed 016-owned catalog surface and rechecks the SQL assertion. It
must never replace a 014-owned function and must retain
`monday_price_book_contract=v2-future-only` as the predecessor contract.

The release adds:

- immutable `supplier_price_schedule_policies`, limited in V1 to `MONTHLY`,
  `COMPLETE_VENDOR`, USD, one explicit scope key, timezone, observation window,
  review-due rule, effective-boundary rule, validity requirements, policy
  version, non-commercial synthetic policy principal, fixture-policy
  configuration hash, and policy fingerprint;
- nullable declaration/policy fields on `price_book_batches`, including the
  per-book source-period label, declared source validity/basis, supplier
  verification instant, operational effective bounds, exact member-set
  fingerprint, and immutable declaration fingerprint; all remain null for
  legacy V1 calls, while a declared replacement requires the complete set and
  exact conformance to its identified policy;
- immutable `price_book_scope_memberships`, captured from validated staging
  before typed staging rows are purged, retaining only identity/scope,
  source-row locator, offer, Variant, SKU, pack and tier key plus the canonical
  source-row fingerprint. It does not retain normalized price amounts;
- append-only `supplier_price_authority_events`, with only
  `ADOPT_EXISTING_BASELINE` and `APPLY_REPLACEMENT` in this slice;
- narrow `supplier_price_authority_heads`, the only mutable projection, with a
  deferred same-transaction event/head/completed-state assertion;
- nullable source price/batch/row/authority-event lineage columns on
  `run_price_snapshots`, populated only for 016-backed prices. The retained
  source `price_id` is an immutable non-FK scalar lineage fact: a later lawful
  replacement may remove that operational row, while the finalized run
  snapshot remains the only retained normalized economics for that run.

`ADOPT_EXISTING_BASELINE` remains migration-only. A release-file,
checksum-pinned synthetic fixture-registration artifact declares exactly two
fabricated baseline vendor scopes (the replacement target and an unaffected
negative control), their disclosed pre-011 CURRENT fingerprints, policy
fingerprints, fixture-policy configuration hash, and non-commercial synthetic
policy principal. Its hash is compiled into and independently verified by the
016 runner/verifier. During the unmarked 016 application transaction only, SQL
accepts the exact registered synthetic database identity, registration hash,
fixture CURRENT fingerprints and policy fingerprints and creates exactly those
two policies, adoption events and heads. An absent or different registration,
database identity, policy, or unexplained CURRENT set stays headless. The
runner first verifies source bytes and maintenance identity, parses and
canonicalizes the pinned JSON, then passes its exact canonical bytes and hash
through one transaction-local GUC. SQL verifies the full payload, hash and
database identity and consumes it only while the 016 marker is absent. The GUC
is cleared by transaction end; no registration table or toggle survives, and
the GUC alone is never authority without the runner's source, identity and
catalog checks. The registration is a one-use release input, never a persisted
toggle or reusable runtime authority, and there is no runtime ADOPT endpoint.
Each adoption event
preserves recorded source facts and explicitly claims neither supplier approval
nor fresh verification. The initializer may not seed the uploaded replacement,
promotion confirmation, `APPLY_REPLACEMENT`, selection, Monday review, DRAFT,
or packet under test.

The existing strict 27-column CSV remains byte-compatible. The browser submits
a separate explicit declaration whose policy identity, registered synthetic
declaration hash, and policy-governed fields must match the policy; its per-book
facts must satisfy—not equal—the policy requirements. The declaration becomes
part of the batch validation fingerprint. Legacy staging without a declaration
retains its old shape and behavior. Hashes prove integrity, not approval.

## Separate confirmation and retained evidence

For a declared replacement, the existing `VALIDATED -> VERIFIED_FUTURE`
promotion becomes the separate price confirmation boundary. Add a read-only
preview and a second exact `CONFIRM` submission. The promotion event stores an
idempotency key, named human principal, action role
`procurement.price.approve`, session-bound authentication hash, preview hash,
confirmation hash, payload hash, warning acknowledgement, declaration/policy,
raw-content hash, exact membership hash, predecessor FUTURE hash, and promoted
FUTURE hash. Existing undeclared promotion calls and rows remain compatible.
All added promotion-event fields are nullable for undeclared legacy rows and
mandatory as one checked group for a declared replacement; 016 attestation and
confirmation rules apply only to declared operations. Undeclared V1 staging and
promotion remain byte- and behavior-compatible and never gain CURRENT authority.

The detail page shows every normalized tier and the CURRENT-to-candidate diff
before confirmation. Raw bytes remain in content-addressed storage. Typed
staging rows are purged after confirmation; immutable membership locators and
fingerprints bind their identities to the verified raw bytes, validation
fingerprint, promotion event, and policy without creating a reusable normalized
price archive. CURRENT and FUTURE remain the only reusable operational price
states, and finalized run snapshots alone retain exact run economics.

## Synthetic capability, clock, and recovery proof

Add repository policy `pricing.synthetic_price_replacement_enabled=false` and
a distinct launcher-owned environment capability. Both mapping/selected and
price policies remain false. A request field, database suffix, stored label, or
environment value alone grants nothing.

Before accepting any declared staging write, list/detail temporal evaluation,
preview, confirmation, or replacement, verify process policy and a
loopback PostgreSQL 16 connection with the exact demo/test database,
`qa_release_login -> qa_mapping_owner`, `qa_mapping_test`, current 014/015/016
markers/catalog assertions, and exact synthetic demo registration. Set the new
price capability and human-context GUCs only after that attestation. New 016
triggers use a new 016-owned capability helper and may call the unchanged
014-owned human-context helper.

Two distinct immutable server-owned fixture instants are registered during
fresh initialization and reattested from the database: a source-observation /
FUTURE-confirmation instant inside the configured day-15-through-20 upload
window, and an application/Monday-evaluation instant at the configured day-one
effective boundary. Neither is accepted from an HTTP form/query and neither
changes the host clock. Default/real paths continue using their existing
clock. Promotion may use the registered observation instant and policy month
only inside this fully attested synthetic mode; replacement uses the distinct
application instant and refuses before the policy boundary or outside declared
source validity. Declared list/detail temporal status uses the same registered
observation instant. Both instants and all window/boundary comparisons use the
policy timezone: the observation month must immediately precede the declared
effective month, and the application local date must equal the declared day-one
boundary. Late application is not supported in this slice.

The application never manufactures its own backup label. Introduce additive
manifest contract `BUFFALO_LOCAL_CANDIDATE_BACKUP_V2`; leave V1 generation and
restore parsing untouched, and never accept V1 as APPLY authority. V2 adds the
source commit/tree, 016 marker/catalog identity, canonical pre-change target-
scope projection and hash, and all member byte sizes and hashes. After FUTURE
confirmation the operator stops the app, invokes the launcher V2 backup, and
restarts with a launcher-resolved V2 manifest beneath the exact owned backup
root. The launcher and service verify the manifest, dump, storage archive,
source database identity, commit/tree, scope evidence, and all member hashes
and sizes. Immediately before mutation the service recomputes the full
relation/state evidence and exact scope-CURRENT projection through the active
locked SERIALIZABLE connection, never a helper-opened second connection. A
request cannot supply or replace the manifest path. The replacement event
stores the manifest, dump, storage and pre-change scope hashes. A label or
non-owned path without surviving matching bytes refuses.

## Lock, retry, and mutation protocol

Validate request-independent process policy, then perform an attested
preflight read and end it. Before the deciding snapshot, acquire session locks
in this order:

1. existing `MONDAY_ANALYSIS_LOCK`;
2. byte-sorted existing selection Variant lock names for every scope member;
3. one price-scope lock `(vendor_id, price_scope_key)`;
4. one apply-idempotency lock.

Use one absolute deadline and balanced reverse cleanup. On uncertain lock or
COMMIT ownership, discard the connection. Inside each fresh SERIALIZABLE
attempt, reattest and re-read the policy, raw bytes, batch, promotion event,
membership, current authority head, exact CURRENT/FUTURE sets, offer/pack/vendor
facts, source validity, effective instant, and recovery proof.

Insert the `APPLY_REPLACEMENT` event as same-transaction mutation authority;
delete only the exact old scope CURRENT rows; change the exact promoted FUTURE
rows in place to CURRENT so their `price_id` and source batch/row provenance
survive; set the batch to `APPLIED_CURRENT`; advance only the exact scope head;
and reconcile event/head/count/value/member/unaffected-scope fingerprints
before commit. The state change consumes the scope's FUTURE projection. New
016 forward-replaces only the 011-owned batch-status constraint/update guard,
price-provenance guard, protected-price guard, and CURRENT-price view definitions
needed for this transition. The batch constraint/guard admits only the declared
`VERIFIED_FUTURE -> APPLIED_CURRENT` transition. Every legacy and undeclared
branch remains exact. New guard arms permit deletion of the registered adopted
baseline and FUTURE-to-CURRENT transition only with the same-transaction APPLY
event, head, membership and verified raw evidence; all other price mutations
remain refused. Views expose batch-sourced CURRENT only when backed by the exact
applicable head/event/policy. No 014-owned function is replaced.

Same apply key plus identical intent returns the existing event after full
authorization; changed intent refuses. Retry only whole transactions on 40001
or 40P01 within the deadline. An ambiguous COMMIT outcome is recovered only
after reauthentication and reacquisition of the same locks; never blindly
replayed. A late injected failure is observed from a fresh connection and must
leave old CURRENT, FUTURE, event, head, batch, and unrelated-vendor state exact
(sequence gaps are allowed).

## Downstream causal binding and compatibility

The selected-offer resolver remains the sole offer authority in the explicit
synthetic selected mode. It reads the new applicable CURRENT rows through the
guarded view and freezes a separate `applicable_price_authority` envelope with
policy, head/event, source batch/raw hash, membership hash, price IDs and source
row IDs. Do not change the existing ten-cell selected price-ladder contract.

The chosen source `price_id`, batch/row and authority event flow into the
nullable run snapshot lineage, selected recommendation metrics, review
`final_price_tier`, DRAFT reconciliation evidence/UI, and the existing
supplier-mapping packet member. A quantity edit across BASE to BREAK must bind
one exact source price ID and run-snapshot ID and reconcile independently
calculated merchandise, vendor-level fees, and total. Old manifests, undeclared
price books, old run snapshots, and already-built packets omit every new
conditional field and remain byte-compatible.

## Required acceptance

1. Preserve a causal red against d7fbb379: real raw upload/validation/FUTURE
   confirmation cannot make replacement CURRENT or change the DRAFT.
2. Fresh 016 apply/replay and catalog verification; wrong/missing predecessor,
   source/catalog/marker tampering, partial install, and default/unregistered
   databases refuse before effects.
3. Positive uploaded replacement to selected offer to BASE recommendation,
   quantity-crossing BREAK review, DRAFT, and 12-member packet with exact IDs,
   raw hash, ladder, fees and totals.
4. Early apply, absent confirmation, stale head/predecessor, wrong scope/vendor/
   pack, incomplete or contradictory ladder, changed raw bytes, conflicting
   retry, lock race, late failure, and missing recovery bytes all refuse with
   fresh-observer no-partial-state proof.
5. An unrelated second vendor is byte-identical; a pre-existing completed
   packet is byte-identical; a not-yet-built stale preview refuses.
6. Browser actions—not fixture completion—perform upload, preview/confirmation,
   apply, mapping/selection, review, DRAFT and downloads.
7. A broader two-vendor/six-item scenario independently reconciles item/pack/
   inventory/threshold/vendor-fee behavior and explicit blockers/exclusions.
8. From a clean clone and empty owned root, Linux prerequisite preflight,
   owned loopback PG16 init, fresh secrets, initialization, login/workflow,
   restart, backup, empty-target restore, state/artifact comparison and bounded
   cleanup succeed. Other platforms remain untested unless actually run.

One final combined suite and one final browser/restart/restore sequence are run
only after focused causal, transaction, and compatibility tests pass. External
review of the frozen d7fbb379 parent remains separate from review of this child.
