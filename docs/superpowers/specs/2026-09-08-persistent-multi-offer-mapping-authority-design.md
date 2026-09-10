# Persistent Multi-Offer Mapping Authority Design

Status: owner-approved product direction and exact engineering proposal for a
later, separately authorized migration. This revision creates no schema,
mapping, price, Shopify permission, Shopify mutation, purchasing, deployment,
recommendation-cutover, or operational authority.

## Objective and current boundary

The offline supplier-review reader can retain several distinct supplier
occurrences for one Shopify Variant ID, but those records remain unapproved
review evidence. The persistent workflow must represent a usual primary regular
offer without deleting legitimate alternatives, and it must keep every kind of
authority separate.

The existing operational boundary remains in force:

- Shopify Variant ID is canonical identity. Supplier and Shopify SKUs are
  evidence and mutable external attributes, never canonical identity.
- `supplier_offers` currently associates one Variant with a vendor, exact
  supplier code, package evidence, and mapping confidence. Its active
  `(vendor_id, supplier_sku)` uniqueness remains unchanged until a reviewed
  migration replaces or narrows that rule.
- `recommendations._load_context()` currently raises
  `EXACTLY_ONE_ACTIVE_STANDARD_OFFER_REQUIRED` unless exactly one active,
  verified regular offer exists. No current code uses a selected-offer head.
- `prices` and the price-book staging and promotion workflow own supplier-price
  authority. Mapping or routine-offer selection cannot create CURRENT or FUTURE
  pricing.
- `supplier_aliases` and `mapping_rejections` retain positive lookup evidence
  and negative memory. Existing price, PO, and Phase 4 protections remain.
- `selling_price_auto_update=false` and
  `auto_write_supplier_sku_to_shopify=false` remain unchanged. No Shopify
  write route or expanded OAuth scope exists under this design task.

The sealed V5-DAYTIME-A1 package remains
`REVIEW ONLY / NOT_APPROVED / NOT_IMPORT_READY`. A later policy decision may
reference its verified evidence, but must append a new application authority
record. It must never alter or retrospectively label the sealed source package
approved.

## Controlling owner product decisions

These are product requirements supplied by the owner. They are recorded
separately from engineering recommendations and unresolved deployment details.

### 1. Normal offer and exceptions

Usually one regular supplier offer represents a store Variant. The initial
routine workflow uses one Variant-wide primary regular offer. Valid alternatives
remain distinct, especially gifts, special editions, alternate packs, vintages,
territory programs, and other genuine supplier relationships. “Usually” is not
a deletion or uniqueness rule. A new or changed special offer requires review
and cannot silently replace the primary regular product, supplier code, or
historical identity.

### 2. Same owner may perform both actions

The same authenticated owner may approve a mapping and select the routine offer,
but they are two distinct confirmations and two append-only events. No second
person is mandatory. An automatic action identifies the service principal and
the exact owner-approved policy; it never fabricates a human click or actor.

### 3. Last-approved-book carry-forward

When a replacement is missing, retain the last approved applicable base book.
Support supplier- and book-scope schedules that are monthly, seasonal, or
irregular. A seasonal book does not expire merely because the calendar month
changed. Source period, declared source validity, last source verification, and
owner-policy carry-forward are distinct facts. Carry-forward never makes old
evidence look newly supplier-verified.

Deal emails are dated overlays on named offers and, when relevant, named tiers.
A temporary overlay expires independently and reveals the unchanged base book.
A few emailed deals never establish coverage for an entire supplier.
Contradictory, withdrawn, or expired terms create a scoped exception.

### 4. Rule-based automatic mapping

An owner-approved deterministic policy may accept an unambiguous exact
distributor + supplier code + distributor-product-identity relationship, both
for qualifying initial mappings and unchanged later relationships. The policy
must preserve leading zeros, suffixes, punctuation, case-significant supplier
semantics, and material identity qualifiers. It requires independent
corroboration of the exact Variant, compatible identity and unit facts, and the
absence of conflicts, active rejection memory, code reuse, or ambiguous
duplicates.

Two generated copies of the same fact are not independent corroboration. New,
reused, or changed supplier codes and gift or special-edition alternatives go
to review. Unchanged identity does not approve changed prices or terms. Missing
pack evidence never defaults to a bottle conversion. Initial policy approvals
and later carry-forward actions are append-only and identify policy version,
evidence fingerprints, and service provenance.

### 5. Private access and owner-triggered field sync

Named private accounts and least-privilege roles protect list, detail, evidence
download, confirmation, reversal, and write routes. Shopify app credentials
authenticate an application to Shopify; they do not prove which human is signed
in to Procurement OS.

The same authenticated owner may edit a cost or selling-price proposal from PO
review and explicitly choose which field to sync. Generating or saving a PO,
receiving inventory, importing a price book, approving a supplier price, or
approving a mapping does not create a Shopify field-sync request.

The selected Shopify cost destination is the standard InventoryItem Cost per
item value. A confirmed cost request writes only
`inventoryItemUpdate(input.cost)` and reads back `InventoryItem.unitCost.amount`
and `.currencyCode`. The amount is for exactly one Shopify sellable unit: one
bottle when the Variant sells one bottle, or the whole four-pack when the
Variant sells a four-pack as one unit. It is never automatically interpreted as
cost per supplier case or per physical bottle. Unknown conversions, ambiguous
sellable-unit scope, nonpositive values, or a currency mismatch block sync.

Quoted PO cost, per-Shopify-sellable-unit cost, received cost, invoiced cost,
and any separately authorized calculated value remain distinct Procurement OS
records. Only the exact value explicitly selected and confirmed may be copied,
with its source and calculation provenance. No automatic averaging or assumed
invoice timing is introduced. Cost, selling price, and SKU are independently
controlled fields; authority for one never implies authority for another.

### 6. Very-high-confidence SKU writeback

The product must support a separately guarded capability to fill a blank SKU or
replace a demonstrably obsolete primary regular SKU when backed by a current
human- or policy-approved mapping. General fuzzy-match score is never write
authority. An unexplained conflicting or manually locked SKU is not overwritten,
and a newly changed supplier code still requires exception review.

Only the designated primary regular code may be written to Shopify. Alternate
vendor, gift, special-edition, and pack codes remain in the mapping registry.
Execution records before, requested, and observed-after values; rechecks the
current value; is idempotent; verifies post-write state; supports pause; and
permits reversal only while the live field still equals the value written by the
recorded event. It never rewrites Variant ID, barcode, inventory quantity, or
historical sales.

## Engineering decisions and recommendations

The owner requirements above control. The following are the recommended exact
implementation shape; they are not deployed behavior.

| Area | Recommended implementation | Reason |
|---|---|---|
| Routine scope | One `ROUTINE_PROCUREMENT_STANDARD` head per Variant | Matches the initial workflow and current recommendation semantics |
| Mapping authority | One append-only decision table with a checked `HUMAN_APPROVED` / `POLICY_APPROVED` discriminator | Makes both authorities queryable without parallel architectures |
| Same owner | Permit one principal to hold mapping and selection roles; require separate previews, confirmations, fingerprints, and idempotency keys | Preserves action separation without inventing a second-person rule |
| Missing books | Retain unchanged CURRENT rows and append a policy evaluation event | Preserves the last applicable base without forging new source verification |
| Deal terms | Separate offer/tier-scoped overlays | Allows independent expiry and protects the base book |
| Initial policy corroboration | Allow only independently observed exact barcode/GTIN, exact current Shopify SKU plus distributor identity, or an existing human-approved crosswalk | Conservative starting set; avoids circular candidate evidence |
| SKU execution | Require explicit owner confirmation in the first release even when policy-eligible | Capability is approved; unattended execution remains a separate future authority choice |
| Shopify sync | One request and outcome stream per field | Prevents cost, retail, and SKU authority from bleeding into each other |

## Chosen persistent model

Retain the existing design: immutable review intake, append-only authority
events, and a narrow mutable head that points to the current event. Extend it
with typed policy provenance, price-scope authority, deal overlays, and separate
Shopify field-sync requests. Do not infer authority from mutable status labels,
confidence scores, uploaded package flags, or client-supplied actors.

### 1. Immutable review intake

Add `supplier_mapping_review_batches` to bind one offline review input:

- batch ID and idempotency key;
- package/root/seal identity and supported source revision;
- package, relationship-table, batch, and payload fingerprints;
- structural, source, and semantic readiness captured separately;
- exact prerequisites and supplier/period scope;
- server-derived creator principal and timestamp;
- zero mapping, price, Shopify, selection, and import authority at intake.

Add `supplier_mapping_review_candidates` for bounded reviewer projections. Each
row binds the batch, canonical Variant, distributor identity, source occurrence,
exact supplier code and distributor product identity, source/page/hash,
qualifier-preserving identity fields, reviewed unit facts with absent versus
explicit-null preserved, related sidecar hashes, blockers, and preview
fingerprint. Raw evidence remains in immutable artifacts; the candidate is not
a replacement source of truth and cannot authorize itself.

The intake service accepts only a reader-produced supported revision whose
fingerprints and zero-authority controls recompute. Missing prerequisites,
incomplete source scope, structural blockers, or semantic blockers remain
visible. The application does not infer approval from package status.

### 2. Append-only mapping decisions

Add `supplier_mapping_decisions` with one immutable action per candidate:

- `APPROVE_MAPPING`, `REJECT_MAPPING`, or `DEFER`;
- `authority_kind` exactly `HUMAN_APPROVED` or `POLICY_APPROVED` for approval;
- exact batch/candidate/Variant/vendor/source-occurrence and supplier-product
  identities;
- expected review, candidate, catalog, vendor, rejection-memory, and evidence
  fingerprints;
- exact reviewed package, physical, retail, Shopify, and qualifying quantities,
  preserving absent separately from explicit null;
- reason, decision timestamp, idempotency key, and payload hash;
- optional resulting `offer_id` only for a successful approval;
- optional same-scope superseded decision reference.

Human approval records the authenticated `principal_id` and separate
confirmation fingerprint. Policy approval records `service_principal_id`, the
owner-approved `policy_id` and immutable version, policy-publication hash,
predicate version/result, and exact independent evidence-set fingerprint. A
policy event has no human-click field and cannot claim a human principal
performed the action. The policy publication itself records the owner who
approved it.

An INSERT uses SERIALIZABLE isolation, locks the candidate and current catalog,
vendor, rejection, and relevant offer rows, recomputes every fingerprint, and
rejects stale evidence. Same idempotency key plus identical payload replays the
record; a different payload conflicts. UPDATE and DELETE are prohibited.

`REJECT_MAPPING` records or links active `mapping_rejections` evidence at exact
Variant/vendor/source scope. A later package must surface that negative memory
and cannot silently reactivate it.

An approval may create a new inactive `supplier_offers` row or link an
unreferenced exact-contract row. A referenced or priced contract mismatch
creates a new historical offer instead of mutating the old row. Mapping
authority never selects the offer, approves a supplier price, or writes Shopify.

### 3. Exact automatic-mapping eligibility

Every condition below is required. Failure of one condition means review, not a
lower-confidence automatic approval.

1. The occurrence is a regular `STANDARD` offer, not a gift, special edition,
   alternate pack, combo, conditional program, or unknown class.
2. The distributor is an exact active identity and the raw distributor product
   identity is present, stable, and unique within its declared scope.
3. Supplier-code bytes match exactly after rejecting outer whitespace. No
   numeric coercion, leading-zero removal, suffix stripping, case folding, or
   punctuation deletion is allowed for equality.
4. A fresh canonical catalog projection independently identifies the exact
   active Shopify Variant ID and binds its catalog fingerprint. The candidate's
   copied Variant field is not corroboration.
5. At least one owner-approved independent linkage class is present: unique
   exact barcode/GTIN in raw distributor and catalog evidence; exact current
   Shopify SKU plus independent distributor-product identity; or a prior
   human-approved external crosswalk fingerprint.
6. Normalized names are compatible while preserving product, brand, gift,
   special edition, vintage, flavor, expression, proof, and other material
   qualifiers. Dropping a qualifier fails the predicate.
7. Size, expression/proof/flavor, package kind, physical count, retail-pack
   count, Shopify sellable units, and qualifying units are compatible and
   present where material. An unknown pack or conversion is not one bottle.
8. The complete candidate set resolves to exactly one Variant and one regular
   occurrence. No material identity/conversion conflict, active rejection,
   supplier-code reuse, duplicate distributor product identity, or ambiguous
   candidate exists.
9. The source and catalog facts come from independently hashed origins. Two
   fields copied from one generated projection count once.
10. For an unchanged later relationship, vendor, distributor product identity,
    supplier code, Variant, package/conversion contract, and policy-relevant
    identity fingerprint exactly equal the prior effective accepted
    relationship. New, reused, or changed codes always route to review.

The predicate excludes price and deal terms. A changed price does not invalidate
an unchanged identity, but it requires its own price validation and authority.
Each qualifying initial or carry-forward policy action appends a new decision;
no earlier source row is rewritten as approved.

### 4. Append-only routine selection

Add `supplier_offer_selection_events` with Variant ID, selected `offer_id`,
scope `ROUTINE_PROCUREMENT_STANDARD`, action `SELECT` or `CLEAR`, prior event,
mapping decision, exact offer/catalog/vendor/source fingerprints, effective
bounds, authenticated principal, reason, timestamp, idempotency key, and payload
hash.

Add `supplier_offer_selection_heads`, one row per Variant and scope, pointing to
the latest event. This is the only mutable projection within the routine-offer
selection subsystem. A deferred trigger permits a head change only with its
same-transaction append-only event and expected prior-head value. The selected
offer must be a current human- or policy-approved regular offer for that
Variant, preserve its reviewed contract, and have no active rejection or
unresolved applicability conflict.

The same owner may approve and select, but must complete separate mapping and
selection previews and confirmations. A mapping decision never advances the
head. Gifts, specials, alternate packs, vintages, territory programs, and combos
remain reachable offers and cannot become the routine head under this scope.

Expose `v_selected_standard_supplier_offers` as the later sole routine-offer
query boundary. Multiple valid alternatives may coexist; only the exact head
conveys routine choice.

## Supplier base-book lifecycle

### Proposed additive records

The later migration should add:

- `supplier_price_schedule_policies`: immutable versioned policy per
  `(vendor_id, price_scope_key)`, with cadence `MONTHLY`, `SEASONAL`, or
  `IRREGULAR`, timezone, expected observation window, review-due rule, source
  validity requirements, owner approver, and policy fingerprint.
- `price_book_scope_memberships`: exact batch membership for each price scope
  and covered offer/tier. Coverage is declared and validated; it is not inferred
  as every active offer for a vendor.
- columns on `price_book_batches` for source-period label, declared
  source-valid-from/through, validity basis, supplier/source verification time,
  and price-scope key. Existing `effective_from/through` remain operational
  application dates and are not overloaded as those facts.
- `supplier_price_authority_events`: append-only `ADOPT_EXISTING_BASELINE`,
  `APPLY_REPLACEMENT`, `CARRY_FORWARD`, `WITHDRAW_SCOPE`, or
  `BLOCK_CONTRADICTION` events with source batch, policy, evidence, expected
  prior-head, actor/service provenance, reason, timestamp, idempotency key, and
  fingerprints. `ADOPT_EXISTING_BASELINE` is migration-only: it may seed an
  empty authority head from exactly reconciled pre-migration CURRENT rows, but
  cannot change their bytes, source period, validity, or verification time and
  cannot claim replacement or carry-forward authority.
- `supplier_price_authority_heads`: one narrow mutable pointer per vendor and
  price scope to the latest event and applicable base source.

### Exact lifecycle

1. Import a candidate replacement into FUTURE through existing strict staging.
   CURRENT remains untouched.
2. Validate its exact price-scope membership, source period, declared validity,
   source bytes, mappings, ladders, warnings, and completeness.
3. At the scope's effective boundary, lock the price-scope head and covered
   rows; verify expected prior head, backup, readiness, and fingerprints; delete
   only the covered scope's old CURRENT rows; promote the exact verified FUTURE
   rows; append `APPLY_REPLACEMENT`; advance the head; remove that scope's FUTURE
   rows; and reconcile pre/post totals in one database transaction.
4. If no replacement exists, leave the applicable CURRENT rows byte-for-byte
   unchanged. Append `CARRY_FORWARD` only when the current source's declared
   validity, schedule policy, vendor/mapping state, and contradiction/withdrawal
   checks pass.
5. Carry-forward updates only the policy evaluation event/time and next review
   due. It does not change the source-period label, declared source validity, or
   supplier/source verification timestamp.
6. A seasonal or irregular base remains applicable across month boundaries
   until its actual validity or policy boundary. A calendar month alone is not
   expiration.
7. Explicit expiry, withdrawal, contradiction, changed material terms, or a
   failed policy evaluation blocks only the affected price scope and causes the
   relevant PRICE_COVERAGE result to fail closed.

### Design-only migration of existing CURRENT rows

Existing CURRENT rows do not automatically acquire schedule or carry-forward
authority. A later migration must:

1. inventory every CURRENT row and reconcile its exact batch, source, vendor,
   offer/tier, effective bounds, and existing approval provenance;
2. propose deterministic price-scope membership without changing any source or
   verification timestamp, and fail ambiguous, mixed-provenance, or incomplete
   groups for owner review;
3. require the owner to approve each supplier/book schedule, validity basis,
   and scope definition as configuration authority;
4. append an explicit `ADOPT_EXISTING_BASELINE` authority event for each
   accepted scope, identifying the pre-migration rows and evidence fingerprint
   without claiming new supplier verification;
5. seed the corresponding authority head from that event in the same guarded
   transaction;
6. shadow-reconcile pre/post row counts, values, scope membership, fingerprints,
   PRICE_COVERAGE, and finalized-run economics; and
7. retain the old CURRENT/FUTURE path until a separate reviewed cutover proves
   exact parity. Unresolved scopes remain on the old path and cannot be carried
   forward under the new policy.

### Reconciliation with no reusable archive

`archive_enabled=false` remains. Only CURRENT and FUTURE are reusable
operational projections. Carry-forward does not clone CURRENT into a new month
or create an historical query surface. An approved replacement deletes the
superseded operational CURRENT rows for its exact scope.

Immutable batch metadata, raw source objects, authority events, and audit
fingerprints remain provenance, not reusable operational price history.
Finalized runs continue to retain only the exact economics actually used in
`run_price_snapshots`. Thus carry-forward preserves one applicable base while
the system still has no unrestricted historical-price warehouse.

## Scoped deal overlays

Add immutable `supplier_deal_overlays`, `supplier_deal_overlay_targets`, and
append-only overlay disposition events. Each overlay binds:

- exact vendor and named offer, plus exact tier natural key when applicable;
- source email/file identity and content hash;
- source date, effective start, and mandatory temporary-promotion end;
- exact term delta or replacement term;
- human or separately approved policy authority and fingerprint;
- creation, activation, expiry, withdrawal, and conflict evidence.

The effective-price view applies an active overlay only to its exact targets.
Expiry or withdrawal reveals the unchanged base book. It never rewrites the base
source or expands a few named deals into vendor-wide coverage. Overlapping
contradictory overlays, an explicitly withdrawn term, or an overlay outside its
dates creates a scoped blocker rather than implicit precedence.

## Private owner workflow and authorization

Every list, detail, evidence, and mutation surface requires a server-validated
named human session before database or storage access. Recommended independent
roles are:

- `procurement.review.read`;
- `procurement.evidence.download`;
- `procurement.mapping.approve`;
- `procurement.offer.select`;
- `procurement.price.approve`;
- `procurement.shopify.cost.sync`;
- `procurement.shopify.retail.sync`;
- `procurement.shopify.sku.sync`;
- `procurement.shopify.sync.reverse`;
- `procurement.order.approve` and `procurement.order.release`.

One named owner may hold both mapping and selection roles. Possessing a role
permits reaching a confirmation boundary; it does not itself create an action.
GET/list/detail/download responses are private, `no-store`, escaped, audited as
appropriate, and subject to the same object-scope checks as writes. Shared
review tokens and client-supplied actor text are insufficient. Confirmation
forms carry opaque server-side fingerprints and CSRF protection.

Service actions use a distinct service identity with a narrowly published
policy. The event records both service principal and owner-approved policy; it
does not reuse the owner's identity.

## Typed internal cost evidence

Do not overload a generic `unit_cost`. Add an append-only cost-evidence ledger.
Its source stage is exactly one of:

- `QUOTED_PO_COST`;
- `RECEIVED_COST`;
- `INVOICED_COST`;
- `CALCULATED_COST`, only with an explicitly authorized method and complete
  input fingerprint;
- `OWNER_ENTERED_PREVIEW`.

Source stage is independent of unit basis. Every row separately records a
code-owned unit basis such as `SUPPLIER_CASE`, `PHYSICAL_CONTAINER`,
`SUPPLIER_RETAIL_PACK`, or `SHOPIFY_SELLABLE_UNIT`, plus the exact unit count,
currency, source object, source timestamp, and immutable fingerprint. A derived
per-Shopify-sellable-unit value links its source row and records the explicit
conversion formula, inputs, rounding rule, and result; it does not disguise the
source stage as a new source.

No automatic average is introduced. PO save, receipt, invoice import, and
price-book import may append their own evidence only when separately
implemented; none selects a Shopify value.

## Shopify field-sync design

This section specifies a future route contract only. The configured Admin API
version is `2026-07`.

### Official operations and scopes

- Every preflight and post-write read uses the 2026-07
  [`productVariant(id:)`](https://shopify.dev/docs/api/admin-graphql/2026-07/queries/productVariant)
  query with the exact Variant GID and retrieves `id`, `product { id }`,
  `price`, and `inventoryItem { id sku unitCost { amount currencyCode } }`.
  Re-querying through the Variant after a cost or SKU write proves that the
  observed InventoryItem is still associated with that exact Variant; an
  InventoryItem-ID-only read is insufficient. The future app installation
  needs only the corresponding `read_products` and `read_inventory` access
  required by those reads.
- Cost previews and executions also query the authenticated shop through the
  2026-07 [`shop`](https://shopify.dev/docs/api/admin-graphql/2026-07/queries/shop)
  query and retrieve `id`, `myshopifyDomain`, and `currencyCode`. The shop ID,
  domain, and currency are part of the preview and request fingerprint and are
  re-read immediately before a cost mutation. This is the authoritative
  currency proof when the current `InventoryItem.unitCost` is null; a null
  current cost is not itself evidence of any currency. Unknown or changed shop
  identity/currency blocks execution.
- Selling price uses
  [`productVariantsBulkUpdate`](https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/productVariantsBulkUpdate)
  with `write_products`, one exact Product/Variant, `allowPartialUpdates:false`,
  and only the `price` field selected by the owner.
- Cost and SKU use
  [`inventoryItemUpdate`](https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/inventoryItemUpdate)
  with `write_inventory`, the InventoryItem verified from the exact Variant,
  and only the selected `cost` or `sku` field. Shopify's 2026-07
  [`InventoryItemInput`](https://shopify.dev/docs/api/admin-graphql/2026-07/input-objects/InventoryItemInput)
  exposes both fields.

Write scopes are not requested or deployed until a separate permission review,
test-store proof, owner authorization, and deployment boundary. Shopify app
authentication remains distinct from Procurement OS human authorization.

### Request and event model

Add append-only `shopify_field_sync_requests` and
`shopify_field_sync_events`. Each request controls exactly one field:
`COST_PER_SHOPIFY_SELLABLE_UNIT`, `RETAIL_PRICE`, or `PRIMARY_REGULAR_SKU`.
It binds:

- exact canonical Variant ID and fresh catalog fingerprint;
- exact shop ID, `myshopifyDomain`, and shop-currency fingerprint;
- verified Product ID when price is targeted;
- verified InventoryItem ID when cost or SKU is targeted;
- expected live value and currency, requested value and currency, and the
  selected internal evidence fingerprint;
- mapping and routine-selection fingerprints where applicable;
- authenticated owner principal, preview and confirmation fingerprints,
  idempotency key, reason, and creation/expiry times;
- pause/lock state and exact before, API result, and observed-after values.

If an interface allows selecting multiple fields, the server creates one
explicit confirmed request per selected field. Unselected fields do not appear
as authorized requests, and every field has an independent outcome.

Add append-only `shopify_field_sync_control_events` plus a narrowly constrained
`shopify_field_sync_control_heads` row per `(Variant ID, field)`. A control event
is `PAUSE`, `RESUME`, `MANUAL_LOCK`, or `MANUAL_UNLOCK` and records the named
principal or service policy, exact prior head, reason, field/catalog
fingerprints, idempotency key, and timestamp. A deferred constraint permits a
head change only beside its event. Preflight checks the current head before it
creates or executes a request. Thus a pre-existing manual SKU lock and a pause
created by a partial failure also block later requests; they are not merely
attributes copied into one request.

Add a separate narrowly constrained `shopify_field_sync_effective_heads` row
per `(shop ID, Variant ID, field, target resource ID)`. It points to the latest
successful, post-write-verified effect event for that exact field and resource.
The pointer advances only in the same local transaction that appends a verified
successful write or reversal event; conflicts and partial failures never
advance it. Every effect event names its expected prior effective head, and a
reversal names the event it reverses. This head is lineage state, not permission
and not a substitute for the pause/manual-lock control head.

### Cost eligibility and execution

The requested cost must be a positive amount in Shopify's shop currency and
must be explicitly marked per one Shopify sellable unit. The exact current
Variant-to-InventoryItem relationship and InventoryItem ID must match the
preview. The selected evidence must prove the sellable-unit conversion: bottle
cost for a one-bottle Variant, four-pack cost for a four-pack Variant, and so on.
Unknown or conflicting pack, physical, retail, or Shopify-unit evidence blocks.

Preview and execution both read `shop { id myshopifyDomain currencyCode }` and
bind that identity and currency. When current `InventoryItem.unitCost` is
nonnull, its currency must also equal the bound shop currency. When it is null,
the request records an expected null live cost and uses the bound shop currency
as the currency authority; it may proceed only when the selected value's
currency equals that known shop currency. Execution re-reads both shop currency
and `InventoryItem.unitCost { amount currencyCode }`, refuses a changed expected
value, shop identity, or currency, submits only `InventoryItemInput.cost`, and
then re-reads amount and currency. It does not update quoted, received,
invoiced, calculated, or PO values.

### Selling-price eligibility and execution

The owner selects an exact retail value and confirms a retail-only request.
Execution re-reads the exact Variant and price, refuses stale or mismatched
state, submits only that Variant's `price`, and verifies the returned and
read-back value. Mapping approval, supplier-price approval, PO generation, PO
save, and book import cannot create this request.

### SKU eligibility and execution

A SKU request is eligible only when all conditions hold:

1. the target offer is the current Variant-wide primary regular selection;
2. its mapping is currently human- or policy-approved;
3. its exact supplier code is preserved and unique in the approved scope;
4. any new or changed code has completed the exception-review path;
5. the live Shopify SKU is blank, or exactly equals a documented obsolete
   predecessor in the same approved regular-offer lineage;
6. there is no gift/special/alternate code, active rejection, code-reuse
   ambiguity, unexplained nonblank conflict, or manual field lock;
7. the request was derived from the dedicated SKU predicate, never the fuzzy
   ranking score.

Only the primary regular code is submitted. All alternate codes remain in the
registry. Execution re-verifies Variant-to-InventoryItem identity and the live
SKU immediately before writing, submits only `InventoryItemInput.sku`, and
performs exact readback.

### Concurrency, partial failure, and reversal

The Shopify mutations above do not provide an application-level atomic
compare-and-swap for these fields, nor a transaction spanning price and
InventoryItem updates. A Procurement OS row lock protects only local records;
it cannot prevent an external Shopify edit.

Therefore each field follows read-check-write-read:

1. lock the local request and verify idempotency and pause state;
2. read the live field and exact resource identity;
3. compare with the recorded expected value and currency;
4. perform one field-scoped mutation;
5. read back and compare the observed result;
6. append the immutable outcome before presenting success.

A race can still occur after the pre-read. Unexpected mutation results or
post-read values produce `CONFLICT` or `PARTIAL_FAILURE`, pause that
Variant/field scope, and require explicit reconciliation. An exact duplicate
request returns the existing outcome; a reused idempotency key with a different
payload conflicts. There is no blind retry.

A reversal is a new confirmed event. Its target must be the current
`shopify_field_sync_effective_heads` event for the same shop, Variant, field,
and resource; that target must be successful, post-write verified, and not
already reversed. An older event is refused even if its after-value happens to
recur later. The reversal also verifies that the live field still exactly
equals the target event's recorded after-value and that the live resource and
currency fingerprints still match. If another actor changed it, the target is
stale/out of order, or the target was already reversed, reversal refuses. A
successful verified reversal appends a new effect event and advances the head
to that reversal outcome; it never rewrites the target event. A failed reversal
preserves all outcomes, does not advance the effective head, leaves the scope
paused, and requires explicit review. Cross-field compensation is never
described as transactional rollback.

## Authority-state separation

| State | Evidence and action | Does not imply |
|---|---|---|
| Human-approved mapping | Named owner decision after exact confirmation | Routine selection, price approval, Shopify sync, order approval |
| Policy-approved mapping | Service event under exact owner-approved policy and evidence fingerprint | Human click, price approval, Shopify sync, order approval |
| Chosen routine offer | Selection event/head for one approved regular offer | Price validity, Shopify field sync, PO release |
| Supplier-price authority | Verified base replacement or explicit carry-forward event | Retail sync, mapping authority, order approval |
| Owner-triggered cost sync | One confirmed InventoryItem cost request | Retail or SKU sync, receiving/invoice policy |
| Owner-triggered retail sync | One confirmed Variant price request | Cost or SKU sync, supplier-price approval |
| Policy-eligible SKU sync | Dedicated eligibility result plus required execution authority | Automatic execution, canonical identity change |
| Order approval/release | Separate readiness and owner action | Any preceding state alone |

## Recommendation cutover

The later migration must not silently weaken
`EXACTLY_ONE_ACTIVE_STANDARD_OFFER_REQUIRED`.

1. Install new tables, constraints, views, and read-only UI while recommendations
   continue using the current query.
2. Import review evidence only; create no authority from the sealed package.
3. Publish and owner-approve exact policy versions and configured source
   schedules in a separately reviewed step.
4. Append human or qualifying policy mapping decisions. Append a separate
   routine selection for each intended Variant.
5. Run a read-only shadow evaluator comparing the legacy exactly-one result to
   `v_selected_standard_supplier_offers`. Missing, multiple, stale, or differing
   results block cutover.
6. Only in a separately authorized change may recommendations consume the
   selected view and replace the legacy blocker with
   `SELECTED_STANDARD_OFFER_AUTHORITY_REQUIRED`.
7. Preserve all mapping, catalog, vendor, unit, price, open-PO, and readiness
   guards. Selection does not satisfy PRICE_COVERAGE.

No synthetic, confidence-score, or mutable-flag bootstrap is allowed. A policy
bootstrap, if later approved, consists of new append-only decisions produced by
the exact predicate and evidence; it does not relabel old rows.

## Exact canonical, config, and implementation conflicts

The following conflicts must be amended in a future authority change before
runtime implementation. This design does not edit them. The concrete current
anchors are canonical specification sections 1 and 11
(`procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md`, source lines
22-24 and 895-932); the price, rollover, archive, fuzzy, and Shopify flags in
`procurement/config/rules.toml` (source lines 4, 22, 26, 37-41, 99, and 103);
the FUTURE-only/per-vendor/full-coverage staging constraints in
`procurement/db/011_monday_price_book_staging.sql`; and the current shared-token
and GET/write route seams in `procurement/src/procurement_os/api.py`.

1. **Universal monthly lifecycle.** The canonical specification describes every
   source as next-month FUTURE followed by day-1 delete/promote. That conflicts
   with supplier-specific seasonal and irregular sources and missing-book
   carry-forward. Amend it to make monthly windows defaults for monthly scopes
   and make activation/replacement scope-specific.
2. **`delete_old_current_on_rollover=true`.** Replace its later meaning with
   `delete_old_current_on_approved_replacement=true`; add
   `retain_current_when_replacement_missing=true`. Old CURRENT rows are deleted
   only when their exact scope has a complete approved replacement.
3. **`archive_enabled=false`.** Keep it false and explicitly state that retained
   CURRENT plus immutable audit provenance is not a reusable price archive.
4. **CURRENT/FUTURE implementation.** Current batch schema accepts only FUTURE,
   permits one verified FUTURE batch per vendor, assumes full-vendor coverage,
   and validates next calendar month. Amend to exact price scopes and schedule
   policies while retaining staging and transactional promotion.
5. **Fuzzy `auto_match`.** `auto_match_min_score` coexists with
   `fuzzy_is_supporting_evidence_only`. Rename or remove the authority-sounding
   result and add `fuzzy_can_authorize=false`; implement the exact policy
   predicate separately with its own disabled rollout flag.
6. **Multiple valid regular offers.** The current recommendations query requires
   one active STANDARD row. Preserve it until the selected-offer view passes
   shadow comparison and a separate cutover is approved.
7. **Human authentication.** Current review routes rely on a shared token and
   caller-supplied actor, and GET/detail/download routes lack the required named
   account boundary. Add a private identity provider/session and server-derived
   roles before persistent workflow exposure.
8. **Shopify flags.** Keep `selling_price_auto_update=false` because an explicit
   owner sync is not automatic. Keep `auto_write_supplier_sku_to_shopify=false`
   now; a later migration should replace it with separate disabled capability,
   owner-confirmed execution, and unattended-policy flags.
9. **Shopify authentication config.** Preserve merchant-owned app credentials
   for application transport, but add a separate human identity/authorization
   configuration. App credentials never populate the human actor field.
10. **Cost model.** Current generic price/PO fields do not preserve all quoted,
    sellable-unit, received, invoiced, calculated, and selected-Shopify bases.
    Add the typed evidence and field-sync model before any cost write.

## Acceptance matrix for the later implementation

| Case | Required proof/result |
|---|---|
| Sealed V5 intake | Remains unapproved immutable evidence; no retrospective policy approval |
| Human mapping approval | Named principal, separate confirmation, immutable exact evidence and result |
| Policy mapping approval | Service principal + owner-approved policy/version + independent evidence fingerprint; no human-click claim |
| Two copies of one generated field | Ineligible; review required |
| Leading-zero or suffixed supplier code | Exact bytes preserved; no numeric coercion or suffix removal |
| Qualifying unchanged monthly relationship | Append one idempotent policy decision under exact prior identity contract; no price approval |
| New, changed, or reused supplier code | Exception review; never automatic mapping |
| Same code reused for another distributor product | Block and retain both histories without merge |
| Changed or missing pack/unit evidence | Block policy mapping; no default bottle conversion |
| Name match drops gift/vintage/flavor/proof/expression/special qualifier | Block |
| New gift, special edition, or alternate pack | Retain separately; cannot replace routine head |
| Same owner maps then selects | Allowed only as two confirmed append-only events |
| Multiple approved alternatives | All remain queryable; exactly one regular routine head at most |
| Selection absent/stale/conflicting | Selected view returns no usable row; recommendations remain blocked |
| Missing monthly replacement with valid base | CURRENT bytes unchanged; append carry-forward event; source verification unchanged |
| Missing seasonal replacement across month boundary | Base remains applicable until actual validity/policy boundary |
| Irregular book has no replacement and is not yet review-due | Exact base may carry forward under its approved scope; source verification timestamp is unchanged |
| Irregular book passes its configured review-due boundary | Scoped PRICE_COVERAGE blocks until an explicit policy evaluation or replacement; no calendar guess |
| Expired, withdrawn, or contradicted base | Scoped PRICE_COVERAGE failure; no carry-forward |
| Existing CURRENT scope adopted during migration | Append `ADOPT_EXISTING_BASELINE` only after exact reconciliation and owner-approved scope/policy; seed an empty head without changing source bytes or verification time |
| Approved replacement | Atomic scope-only CURRENT replacement with pre/post totals and event/head reconciliation |
| No-price-archive check | No reusable superseded operational projection; finalized-run snapshot remains exact |
| Deal email names three offers | Only those targets may be overlaid; no supplier-wide extrapolation |
| Temporary deal expires | Overlay ceases; unchanged base resumes |
| Conflicting active overlays | Scoped blocker; no silent precedence |
| Unauthenticated list/detail/download | Denied before database/storage access |
| Shopify app token without human session | Cannot establish owner actor or confirm a sync |
| PO generate/save, receipt, invoice, mapping, or price-book import | Zero Shopify cost, retail, and SKU requests/calls |
| Cost source is per supplier case but sellable-unit conversion unknown | Block cost sync |
| One-bottle Variant cost | Selected one-bottle cost may be proposed; exact currency and InventoryItem required |
| Four-pack-as-one-Variant cost | Selected four-pack cost is the one-unit Shopify cost; no bottle division |
| Cost currency differs from Shopify shop/unitCost currency | Block before mutation |
| Current `unitCost` is null and shop currency is known and matches selected evidence | Expected null may proceed after all other predicates pass; bind the shop identity/currency fingerprint and verify nonnull amount/currency after write |
| Current `unitCost` is null and shop currency is unknown, changed, or mismatched | Block; null cost supplies no currency authority |
| Explicit cost-only sync | Only InventoryItem cost attempted and read back with amount/currency |
| Explicit retail-only sync | Only exact Variant price attempted and read back |
| Cost and retail both selected | Independent requests/outcomes; one does not authorize or conceal failure of the other |
| SKU blank and all predicate checks pass | Owner-confirmed first-release request may proceed for primary regular code only |
| SKU equals proven obsolete predecessor | Eligible only after exact code-transition review and fresh readback |
| Unexplained nonblank SKU or manual lock | Refuse write |
| Fuzzy score alone | Never permits SKU or mapping write |
| External edit between preview and pre-read | Conflict before write |
| External edit during write/readback race | Conflict/partial failure, pause, append exact observed state |
| Duplicate identical request | Idempotent replay of recorded result |
| Same idempotency key, different payload | Conflict with no mutation |
| Reversal of current successful unreversed effective-head event while live value equals its after-value | New confirmed reversal may restore exact before-value, verify it, and advance the effective head |
| Reversal targets an older event after the same value recurs | Refuse as stale/out of order even though the live value matches |
| Reversal target is already reversed or is not the effective head | Refuse with no mutation |
| Reversal after independent live edit | Refuse; preserve all events and pause state |
| Cross-field second operation fails | Preserve each outcome; mark partial failure; no transactional rollback claim |
| Rollback attempt fails | Preserve original, partial, and failed-reversal evidence; require explicit reconciliation |
| Mapping, selection, price, sync, and order states | Independently queryable; none implies another |
| Actual PO approval/release | Separate owner action and readiness boundary remains mandatory |

## Read-only branch and future integration plan

This design revision lives on
`codex/supplier-mapping-review-policy-followup`, descended from the preserved
review checkpoint. The current `main`, PR #23, and unmerged foundation history
remain untouched. This branch must not be rebased, force-pushed, or merged under
the present authorization.

Future review should compare the exact preserved parent to this branch, then
review the Packet A correction independently from this design-only policy
revision. If the owner later authorizes integration, create a fresh integration
branch from the then-approved target, verify ancestry and conflicts read-only,
and cherry-pick only the reviewed commits in order. Do not silently absorb
unmerged foundation history or resolve architectural conflicts by favoring the
newer file.

Persistent implementation must be decomposed into separately reviewed changes:

1. canonical/config amendments and exact migration design;
2. immutable review, mapping decision, and routine selection schema;
3. read-only shadow views and private authorization boundary;
4. price-scope schedules, carry-forward authority, and deal overlays;
5. explicit policy publication and deterministic mapping evaluator;
6. recommendation shadow comparison and later cutover;
7. Shopify field-sync schema and test-store-only execution;
8. separately approved OAuth scope deployment and production enablement.

Each step requires disposable-PostgreSQL proof, independent review, owner
authorization, and a new handoff boundary. No step may infer authority from a
later step.

## Genuinely unresolved details

The six policy questions above are answered. The cost destination and unit basis
are also answered. These narrower implementation details remain genuinely open:

1. Which private identity provider and exact named-account role assignments will
   protect review, evidence download, mapping, selection, price approval, field
   sync, reversal, and order actions?
2. Which independent initial-linkage evidence classes will the owner publish in
   policy? The three-class conservative allowlist above is the engineering
   recommendation, not yet deployed authority.
3. After the owner-confirmed first release, may a policy-eligible SKU request ever
   execute unattended under a separately approved service policy? The current
   recommendation remains explicit owner confirmation.
4. The actual cadence, source validity, price scope, and expected update facts
   for each distributor/book family must be supplied and approved as
   configuration data. They must not be guessed from calendar behavior.

## Completion boundary

This document is the exact revised design and acceptance matrix only. It does
not implement any proposed table, migration, route, identity provider, role,
policy evaluator, source schedule, price-state change, recommendation cutover,
OAuth scope, Shopify read/write, mapping/price approval, purchase action, or
deployment. Every current runtime flag and operational guard remains unchanged.
