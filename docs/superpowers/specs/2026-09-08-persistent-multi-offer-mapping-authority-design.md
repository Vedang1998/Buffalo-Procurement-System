# Persistent Multi-Offer Mapping Authority Design

Status: implementation-ready proposal for a later, separately authorized
change. This document creates no schema, mapping, pricing, Shopify, purchasing,
or deployment authority.

## Objective and current boundary

The offline V5 reader can retain several distinct supplier occurrences for one
Shopify Variant ID, but every occurrence is still unapproved review evidence.
The next bounded application change should persist explicit mapping decisions
and one selected routine-procurement offer without collapsing alternatives or
letting a review package authorize itself.

The existing operational boundary is deliberately stricter:

- `supplier_offers` in `procurement/db/schema_postgres.sql` associates one
  canonical Variant ID with a vendor, exact supplier SKU, package evidence, and
  mapping confidence. Its active `(vendor_id, supplier_sku)` uniqueness remains.
- `recommendations._load_context()` currently queries active `STANDARD` offers
  and raises `EXACTLY_ONE_ACTIVE_STANDARD_OFFER_REQUIRED` unless exactly one
  exists. It additionally requires `confidence='VERIFIED'`.
- `prices` and the price-book staging/promotion machinery own price approval;
  a mapping decision cannot create CURRENT pricing.
- `supplier_aliases` and `mapping_rejections` retain positive lookup evidence
  and negative memory. Supplier SKU is evidence, never canonical identity.
- referenced or priced offer contracts are protected by the Phase 4, PO-ledger,
  and price-book triggers. A later mapping workflow must not rewrite such rows.

Until the new authority is migrated, populated, reviewed, and explicitly cut
over, the existing exactly-one-active-offer rule remains unchanged.

## Chosen model

Use append-only decisions plus a narrow current-selection pointer. Do not mark
every plausible V5 occurrence as an active operational offer, and do not infer
a winner from source priority, price, text similarity, or package completeness.

### 1. Immutable review intake

Add `supplier_mapping_review_batches` to bind one offline review input:

- batch ID and idempotency key;
- package/root/seal identity and source revision;
- package, relationship-table, and batch fingerprints;
- structural/source/semantic readiness captured separately;
- exact missing prerequisites and supplier/period scope;
- created actor/time and a payload fingerprint;
- zero mapping, price, Shopify, and import authority at intake.

Add `supplier_mapping_review_candidates` for the bounded projections submitted
for review. Each row binds the batch, Variant ID, vendor evidence, source
occurrence, exact supplier code, source/page/hash, reviewed conversion fields,
related sidecar hashes, blockers, and offer-preview fingerprint. Raw evidence
remains in immutable artifacts/sidecars; the application row is a projection,
not a replacement source of truth. Candidate rows are insert-only and cannot
reference an operational `offer_id` before a mapping decision creates or links
one.

The intake service accepts only a reader-produced supported revision whose
fingerprints and zero-authority controls recompute. `BASELINE_REQUIRED`, missing
supplier-period coverage, source-evidence blockers, or semantic blockers remain
visible and prevent approval. Package status never grants application authority.

### 2. Append-only mapping decisions

Add `supplier_mapping_decisions` with one immutable human action per candidate:

- `APPROVE_MAPPING`, `REJECT_MAPPING`, or `DEFER`;
- exact batch/candidate/Variant/vendor/source-occurrence identity;
- expected offer-preview and current candidate fingerprints;
- exact reviewed package, physical, retail, Shopify, and qualifying quantities,
  preserving present-null separately from absent;
- actor, nonblank reason, decision timestamp, idempotency key, and payload hash;
- optional resulting `offer_id` only for a successful mapping approval;
- optional `supersedes_decision_id`, constrained to the same candidate scope.

INSERT uses a SERIALIZABLE transaction and locks the batch/candidate plus the
current Variant and vendor rows. It recomputes every fingerprint and guard,
requires a private authenticated caller and a separate confirmation POST, and
rejects stale or changed evidence. Same idempotency key plus identical payload
replays; a differing payload conflicts. UPDATE/DELETE are prohibited.

`REJECT_MAPPING` also records or links an active `mapping_rejections` row with
`mapping_type='SUPPLIER_OFFER'`, exact source key, Variant/vendor scope, and the
decision fingerprint. Later packages must surface that negative memory; they
cannot silently reactivate the candidate.

An approved mapping may create a new inactive `supplier_offers` row or link an
existing unreferenced exact-contract row. Linking requires equality of Variant,
vendor, exact supplier SKU, package type, pack conversions, source evidence, and
confidence. A referenced/priced mismatch creates a new offer rather than
mutating history. The approved decision changes confidence/mapping authority
only; it does not select the offer or approve price.

### 3. Append-only selection events and one current head

Add `supplier_offer_selection_events` for routine procurement:

- Variant ID, selected `offer_id`, selection scope
  `ROUTINE_PROCUREMENT_STANDARD`, and event action `SELECT` or `CLEAR`;
- prior selection event ID, mapping-decision ID, exact offer-contract
  fingerprint, Variant/catalog fingerprint, vendor-state fingerprint, and
  source review fingerprint;
- effective date bounds, actor, reason, timestamp, idempotency key, and payload
  hash.

Add `supplier_offer_selection_heads` with one row per
`(variant_id, selection_scope)`, referencing the latest event. The head is the
only mutable projection. A deferred constraint/trigger permits a head change
only with its exact same-transaction append-only event, checks the prior-head
compare-and-swap value, and validates that the selected offer:

- belongs to the same active CURRENT/LIVE Variant and an active vendor;
- is `STANDARD`, active, and backed by an approved current mapping decision;
- preserves exact reviewed conversions and source identity;
- is not blocked by active rejection memory or unresolved applicability;
- has not been frozen into a contradictory contract by pricing or PO history.

Concurrent selections for one scope serialize on the head; exactly one wins.
Clearing is explicit and leaves the scope with no selected offer, which is a
fail-closed state. Historical selection events are never altered.

Expose `v_selected_standard_supplier_offers` as the sole later query boundary.
It joins the head, event, decision, and offer and returns one valid selection or
no row. Multiple approved/active alternatives may coexist in `supplier_offers`;
only the selected view conveys routine-procurement choice.

## Recommendation cutover

The later migration must not silently weaken
`EXACTLY_ONE_ACTIVE_STANDARD_OFFER_REQUIRED`.

1. Install decision/selection tables, guards, and read-only UI while
   recommendations continue using the existing query.
2. Import only review evidence; create no decisions or selections automatically.
3. Owner-review and explicitly approve mappings, then explicitly select one
   standard offer for each intended Variant scope.
4. Run a read-only shadow evaluator comparing the legacy exactly-one result with
   the selected-offer view. Any missing, multiple, stale, or differing result is
   a blocker, never an automatic selection.
5. In a separately reviewed cutover, change `recommendations._load_context()`
   to consume exactly one row from `v_selected_standard_supplier_offers` and
   replace the legacy blocker with `SELECTED_STANDARD_OFFER_AUTHORITY_REQUIRED`.
6. Keep all mapping/readiness, vendor, pack, validity-date, source, CURRENT-price,
   and open-PO checks. Selection does not satisfy `PRICE_COVERAGE`.

The cutover requires a migration preflight proving that every in-scope legacy
exactly-one offer either has an owner-approved matching selection or remains
blocked. There is no synthetic or confidence-based bootstrap.

## Price and Shopify separation

Mapping approval answers “this supplier occurrence belongs to this Variant.”
Offer selection answers “this mapped STANDARD offer is the routine procurement
choice.” Price approval remains exclusively in the price-book batch validation
and promotion path. A selected offer without verified CURRENT pricing remains
blocked from price-aware procurement.

Shopify SKU writeback is a fourth, separately authorized operation. It needs its
own preview, current-Shopify readback, expected-old-value compare-and-swap,
idempotency key, actor authentication, audit event, and post-write verification.
Neither mapping approval nor selection grants a Shopify write scope.

## Gifts, alternates, combos, and raw sidecars

`STANDARD` selection never merges or retires gift, alternate-case, vintage,
territory, or conditional-program occurrences. Conditional gifts and alternate
packs remain separate candidates with their own evidence and no selection into
routine procurement unless a later typed policy is approved.

Fixed combos retain parent offer plus ordered component Variant relationships.
No parent metafield overwrites component-specific owner quantities, and no
component price is invented from a combo total. Source Tier IDs, rejected rows,
explicit nulls, contradictions, and all provenance sidecars remain immutable and
reachable from each decision fingerprint.

## Private owner workflow

The later UI should reuse the current report projection rather than expose raw
tables directly:

1. private authenticated GET lists fingerprinted review batches and blockers;
2. detail GET shows every same-Variant offer and retained negative memory;
3. mapping POST creates only a read-only preview;
4. confirmation POST appends one mapping decision after exact revalidation;
5. a separate selection preview/confirmation appends a selection event and
   advances the head;
6. price approval and Shopify writeback are absent from these routes.

Every response is `no-store`, escaped, free of tokens, and protected by verified
caller identity and role—not merely a shared token or typed actor name. GET and
artifact/download surfaces require the same confidentiality boundary. Forms
carry opaque server-side confirmation fingerprints, never authority values
chosen by the client.

## Staleness and rollover

A candidate or decision becomes stale when any bound Variant/catalog, vendor,
offer contract, source evidence, reviewed conversion, rejection-memory, package
scope, or policy fingerprint changes. Stale decisions remain historical but
cannot advance a selection head. Price changes do not rewrite mapping history;
they independently affect price readiness.

Monthly rollover starts a new review batch. Missing supplier books or incomplete
supplier-period coverage produce `NOT_COMPARABLE` and cannot clear or retire a
selection. A retained current head remains usable only while its explicit
validity and all live guards pass; no new occurrence automatically supersedes it.

## Acceptance matrix for the later implementation

| Area | Required proof | Fail-closed case |
|---|---|---|
| Intake | exact supported package/table/batch fingerprints; zero authority | unknown revision, missing source/period, or changed bytes |
| Mapping | preview then append-only exact decision | stale fingerprint, explicit null conversion, active rejection, or cross-Variant candidate |
| Alternatives | two or more approved offers retained distinctly | supplier-code reuse never merges identities |
| Selection | one head and one selected view row per Variant/scope | absent/multiple/stale selection blocks recommendations |
| Concurrency | two connections race one head; one commits | losing action conflicts with no partial event/head |
| Idempotency | exact request replays one decision/event | same key with changed actor/reason/payload conflicts |
| History | prior decisions/events/source sidecars remain readable | UPDATE/DELETE/reparent attempts fail in SQL |
| Pricing | selected offer still requires verified CURRENT price | mapping/selection cannot insert/promote price |
| Shopify | no Shopify calls/scopes/routes in mapping packet | any write attempt absent and counter remains zero |
| Combos | parent/component/gift/alternate evidence remains separate | no inferred units, allocation, or component price |
| Readiness | scoped `MAPPING_INTEGRITY` plus all canonical gates recompute | missing scoped gate or failed catalog/vendor/price gate blocks |
| Security | authenticated private GET/download and role-checked confirmations | shared token alone or caller-supplied actor is insufficient |
| Rollback | injected failure leaves no partial candidate decision/head | retry is exact or explicit conflict |
| Audit | packet recreates selected decision from immutable fingerprints | mutable live labels never replace frozen evidence |

## Decisions required before implementation

1. Is routine selection scoped only by Variant, or by Variant plus vendor/order
   channel? The current recommendation query behaves like a Variant-wide choice;
   changing that is a business decision.
2. May the same authenticated owner approve a mapping and select it in distinct
   confirmation actions, or are distinct roles required?
3. What validity/expiry policy applies to a selection when a monthly book is
   missing but the prior offer remains contractually valid?
4. Which current verified single offers, if any, may be bootstrapped through an
   explicit owner-reviewed batch? No automatic backfill is proposed.
5. Which private identity provider and authorization roles protect list, detail,
   evidence download, mapping confirmation, and selection confirmation?
6. Should Shopify SKU writeback ever be supported, and under which separately
   reviewed scope and rollback procedure?

These answers, an exact migration design, disposable-PostgreSQL tests,
independent review, and owner approval are prerequisites to implementation.
