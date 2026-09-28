# Synthetic Selected-Offer Consumption Design

**Status:** owner-approved for bounded isolated synthetic implementation;
six review corrections incorporated for independent read-only re-review

**Acceptance recorded:** `2026-09-14T12:41:37Z`

**Expired implementation cutoff:** `2026-09-14T15:41:37Z`

**Expired hard handoff:** `2026-09-14T16:41:37Z`

**Renewed authorization / execution-host start:** `2026-09-14T22:29:54Z`

**Renewed implementation cutoff:** `2026-09-15T01:29:54Z`

**Renewed hard stop:** `2026-09-15T02:29:54Z`

**Base:** `22ab1cf800963a6d99e65eb210d8cfb1bbec0abd`, tree
`7c8da387d4e2b177dd3790763382247caa60914a`

**Owner authorization:** `NEXT AUTHORIZATION — CONNECT SELECTED OFFER TO A
SYNTHETIC DRAFT`, followed by `APPROVED — APPROACH 1: SYNTHETIC SELECTED-OFFER
CONSUMPTION`, then `NEW AUTHORIZATION — REPAIR THE SIX FINDINGS AND IMPLEMENT
THE CONNECTION`. The first implementation window was missed and its late
specification commit remains recorded; the renewed window does not relabel that
missed milestone as complete.

## 1. Outcome

Inside the already-attested local synthetic workflow only, a newly prepared
Monday run uses the separately confirmed routine selected offer as the sole
supplier-offer authority for that Variant. The service validates the complete
selection and mapping lineage, then uses the selected offer's separately
established synthetic CURRENT price, pack conversion, vendor rules and fees in
the existing inventory, demand, review and internal-DRAFT path.

The selected offer is not activated by this read. Its price is not approved,
created, promoted or altered. A missing or invalid selection blocks that item;
the enabled synthetic path never falls back to the legacy active-offer query.

The implementation proves one causal synthetic line:

```text
fabricated source evidence
  -> append-only mapping decision
  -> separate routine selection
  -> selected active STANDARD offer
  -> existing verified synthetic CURRENT ladder and vendor fees
  -> inventory/demand calculation
  -> reviewed quantity
  -> internal DRAFT and frozen packet
```

It does not implement the real supplier-to-CURRENT price lifecycle, production
identity, forecasting completion, Shopify import, PO release or ordering.

## 2. Authority boundary

The owner approval is the separately required recommendation-cutover authority
contemplated by the canonical specification, but only for an attested
`AUTOMATED_TEST` or `SYNTHETIC_DEMO` process. It supersedes the preserved
shadow-freeze draft only where that draft prohibits synthetic selected-offer
consumption or any resulting synthetic routing/economic change. The earlier
draft remains unchanged as prior design evidence.

All repository production controls remain false, including
`recommendation_cutover_enabled` and `offer_activation_enabled`. Add one new
false repository declaration, `synthetic_selected_offer_inputs_enabled`, which
describes an overlay capability rather than production authority. No request,
form value, stored label, database suffix or environment value alone enables
it.

The capability is enabled only when all of these independent facts agree:

1. `BUFFALO_ENABLE_SYNTHETIC_SELECTED_OFFER_INPUTS=1` is supplied by the
   supervised local candidate or registered test harness, never HTTP input.
2. `BUFFALO_RUNTIME_MODE` is exactly `SYNTHETIC_DEMO` or `AUTOMATED_TEST`.
3. Repository mapping/cutover/activation policy retains every reviewed false
   value and the new capability is false in `rules.toml`.
4. PostgreSQL is loopback version 16, the schema is exactly
   `qa_mapping_test`, and the session/current-role pair is exactly
   `qa_release_login` / `qa_mapping_owner`.
5. The exact mapping and forecast-retirement contracts are installed and
   verified. An owned demo also carries the exact synthetic demo marker.

Failure of an asserted overlay fact is a hard capability refusal. Absence of
the overlay selects the unchanged legacy resolver only for new default runs or
genuinely pre-existing legacy-contract runs.

Test/demo registration is exact and server-owned:

- `AUTOMATED_TEST` requires the repository test runner's existing
  `TEST_DATABASE_URL`; its parsed host, port and database must equal the active
  connection, the host/server address must be loopback, and the database must
  end in `_test`;
- `SYNTHETIC_DEMO` requires the supervised local candidate's existing
  `DATABASE_URL`; its parsed host, port and database must equal the active
  connection, the database must end in `_demo`, and the exact
  `synthetic_owner_demo_contract` marker must be present; and
- both modes require the distinct selected-input environment flag, the
  existing synthetic-mapping process capability, exact role/schema facts, and
  successful installed mapping and forecast-retirement contract assertions.

No connection fact is inferred from an HTTP request or run row. A suffix or an
environment value without every other attestation refuses. The launcher adds
the distinct environment value only after its existing source, database,
role, schema, catalog and demo-marker preflight succeeds.

The accepted limitation is explicit: no new migration adds an independent
database trigger proving that a recommendation's offer equals the selected
head. The service, UI and frozen evidence prove the isolated synthetic path;
direct-SQL enforcement remains a blocker to any real/default cutover.

## 3. Resolution contracts and compatibility

Offer resolution is versioned independently of the unchanged forecast method:

- `LEGACY_ACTIVE_STANDARD_V1`
- `SYNTHETIC_CONFIRMED_SELECTION_V1`

Legacy/default manifest construction remains byte-for-byte unchanged: it omits
`offer_resolution_contract` and every selected-only field for both old and new
legacy runs. Omission is the sole valid representation of
`LEGACY_ACTIVE_STANDARD_V1`. Only a newly prepared, independently authorized
selected-synthetic run adds
`offer_resolution_contract = SYNTHETIC_CONFIRMED_SELECTION_V1` and its complete
selected evidence. An unknown or malformed value, selected-only fields without
that exact discriminator, a selected discriminator without complete evidence,
or any selected run whose capability is unavailable refuses. None downgrades
to legacy.

`prepare_monday_run()` chooses the contract server-side for a new run. An
asserted selected overlay acquires the full request Variant session-lock set
before the idempotency lookup, then requires the request business date and
normalized Variant IDs to match the stored manifest before dispatching through
its stored contract. A non-asserted/default call retains the legacy path; if
its minimal idempotency lookup discovers a selected-contract run, it refuses
without reconstructing selected inputs. It never treats the stored label
itself as capability and never switches an existing run merely because process
configuration changed.

Every idempotency, DRAFT or packet replay first passes the existing
server-verified local-session/named-principal authorization boundary. A run ID,
idempotency key or stored contract never grants access. A selected terminal
`DRAFTS_BUILT`/`PACKET_BUILT` replay reattests the synthetic selected-input
capability and then returns the immutable stored result without recalculation.

For a write-capable review/build validation, a preliminary immutable-run read
may discover the lock set but grants no authority. The service ends that
preflight transaction, acquires the same sorted Variant session locks, begins a
fresh validation transaction, re-reads the exact run and recommendation, and
then calls `validate_monday_run_inputs()`. A selected contract independently
rechecks capability before reconstructing current selected inputs. Review and
DRAFT creation therefore stop if the selected capability disappears or if any
selection, mapping, offer, price or vendor input changes. `DRAFTS_BUILT` and
`PACKET_BUILT` replay retains the existing early immutable-return behavior and
never enters this recalculation path.

No forecast method/version changes. Existing run rows, input-manifest bytes,
fingerprints, DRAFTs and packet bytes are never rewritten or backfilled.

## 4. Transaction, locking and temporal semantics

The selected resolver uses a complete, bounded transaction attempt around the
existing Monday preparation body. For selected-contract preparation:

1. validate the process-owned environment and disabled repository policy;
   normalize and UTF-8-byte-sort the requested Variant IDs without a database
   read, and require the supplied connection state to be `IDLE`;
2. acquire the existing bigint `MONDAY_ANALYSIS_LOCK` in session scope, then
   acquire the exact existing session advisory-lock key
   `persistent-mapping:selection-variant:<Variant ID>` for every requested
   Variant using the same `pg_try_advisory_lock(hashtextextended(name,0))`
   helper and bounded deadline as routine selection; acquire no new domain;
3. end the Psycopg `autocommit=False` lock-acquisition transaction so state is
   `IDLE` while all session locks remain held; enter `conn.transaction()` and
   immediately execute `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` before
   any snapshot-taking statement (do not send a nested raw `BEGIN`);
4. if any session lock is unavailable, roll back, release
   every acquired session lock in reverse order, verify cleanup and persist
   nothing;
5. as the first snapshot-taking reads, independently attest the database,
   schema, roles, demo marker and installed mapping/retirement contracts, then
   load the idempotency/date claim;
6. read the head, event, effective mapping, candidate/batch, rejection state,
   offer, vendor rules and full CURRENT price ladder from that same snapshot;
7. calculate and insert the run, snapshots, recommendations and blockers; and
8. commit once, then release the session locks in reverse order and verify each
   release.

The session advisory locks are the same locks used by the selection writer. A
concurrent selection therefore either completes before Monday starts its fresh
snapshot, waits until after Monday commits, or causes an explicit bounded lock
refusal. No mixed head, event, mapping and offer result is accepted.

`40001` serialization and `40P01` deadlock failures abort the complete attempt,
return the connection to `IDLE`, and may retry the entire database transaction
at most three times under one bounded operation deadline while retaining the
already authenticated global/Variant session locks. Each retry obtains a
genuinely fresh snapshot beneath those locks. Exhaustion returns typed
`MONDAY_PREPARATION_RETRY_REQUIRED`. Other errors do not retry. An ambiguous
commit closes/discards the connection and returns an outcome-unknown error; it
is never blindly replayed. No retry occurs inside an aborted transaction and no
partial run is accepted.

One `finally` path governs success, refusal, retry exhaustion, cancellation and
unexpected failure: roll back whenever driver state is not `IDLE`, release all
acquired session locks in reverse order through the existing balanced cleanup
helper, verify each unlock, and close/discard the connection if cleanup or lock
ownership is uncertain. Factor the attempt around the existing mapping
deadline/retry/cleanup primitives rather than inventing a second lock protocol.

The connection must be idle before this ordering begins and after every failed
attempt. Callers may not hide a pre-existing snapshot inside a larger
transaction. Tests instrument transaction control, locks, first evidence read,
retry cleanup and persistence, and refuse selected-mode preparation whose
connection is already active. Default legacy preparation retains its
established transaction contract and exact manifest bytes.

The run records one database transaction timestamp as `evaluation_at` and
`selection_observed_at`. Resolver validation tests current rows for the run's
explicit `business_date`. Evidence is labeled:

- `temporal_semantics = CURRENT_STATE_OBSERVED_IN_RUN_TRANSACTION`
- `historical_reconstruction = false`

Later input validation is labeled `CURRENT_STATE_REVALIDATION`. It reuses the
frozen observation timestamp in the expected payload; it never generates a new
timestamp that makes otherwise unchanged inputs stale, and it never claims a
historical as-of reconstruction.

## 5. Selected lineage and eligibility

Resolve with explicit base-table joins rather than the current-date diagnostic
view. For each requested Variant, require exactly one current
`ROUTINE_PROCUREMENT_STANDARD` head whose event is `SELECT` and whose effective
period includes the run business date.

Freeze and revalidate at least:

- head Variant, scope, event ID and head version;
- selection event ID, payload SHA-256, mapping decision ID, selected offer ID,
  expected prior head, effective bounds, selected timestamp/transaction and
  every expected mapping/offer/catalog/vendor/rejection fingerprint;
- effective append-only APPROVE mapping decision, payload SHA-256, action,
  Variant/vendor/result offer, link kind, candidate and evidence-set identity;
- immutable candidate and batch IDs, source package/revision, source authority
  and import states, occurrence/supplier identity and sealed hashes;
- the selected offer's live contract fingerprint and explicit Variant, vendor,
  supplier SKU, `STANDARD` package class, size/raw pack, Shopify units per case,
  qualifying units per case, assortment fields, confidence, activity and
  validity bounds;
- current catalog, vendor and rejection-memory fingerprints recomputed in the
  same snapshot.

The selected offer must be the effective mapping result and must be:

- for the requested Shopify Variant ID;
- on the mapping decision's exact vendor;
- active, `STANDARD` and `VERIFIED`;
- non-rejected and within business-date validity;
- equipped with nonblank supplier SKU and positive whole Shopify and qualifying
  unit conversions; and
- pack-compatible with the sealed candidate evidence wherever that evidence is
  `VALUE`.

The enabled selected path emits a typed affected-item blocker, with no legacy
fallback, for at least:

- no head or a CLEAR head;
- stale or superseded mapping decision;
- mapping/Variant/vendor/offer mismatch;
- changed catalog, vendor, rejection or offer fingerprint;
- active rejection;
- inactive vendor;
- inactive, non-`STANDARD`, unverified or out-of-validity offer;
- missing supplier SKU or invalid/incompatible pack; and
- absent, invalid or wrong-month verified CURRENT price.

Selection does not make an inactive offer eligible and does not approve price.

## 6. Common purchasing path and frozen evidence

After resolution, use the existing common logic without a second offer lookup:

1. validate selected vendor rules/calendar/minimum/loose-fee facts;
2. load the complete verified CURRENT price ladder for the selected offer and
   run month;
3. capture current inventory and independently reconciled incoming/open-PO
   position;
4. build the existing explicitly limited emergency demand evidence;
5. calculate baseline need, pack rounding and the current strategic-zero result;
6. persist run price snapshots, recommendation and frozen metrics;
7. apply existing preview, material-edit, review and stale-input rules; and
8. build the existing vendor-separated internal DRAFT.

The full price ladder and all fee evidence are frozen, not just the initially
selected tier. A later quantity edit therefore re-evaluates the eligible tier
from `run_price_snapshots` and frozen vendor terms. The calculation returns the
chosen `run_price_snapshot_id`, level type, break unit/quantity, source
`price_id`, unit price and case price. The source `price_id` is recovered only
by an exact unique match against the manifest-bound ladder; zero or multiple
matches refuse. These exact tier facts enter the preview fingerprint, material
confirmation and append-only
`review_decisions.evidence_json.review.final_price_tier`. That object contains
the source `price_id`, `run_price_snapshot_id`, level type, break unit/quantity,
unit/case price and ladder digest. It is projected into the DRAFT service/UI,
the existing `purchase_orders.reconciliation_evidence`, and selected-only
packet enrichment. It never retains the initial tier identity while
calculating with a different tier and never fetches a live replacement price
or fee.

Add a versioned `selected_offer_input_evidence` object to the context and
recommendation metrics. It contains the complete lineage listed above plus:

- `contract = SYNTHETIC_CONFIRMED_SELECTION_V1`;
- `authority = SYNTHETIC_TEST_ONLY`;
- `commercial_source_authority` and `source_import_state` from the batch;
- `selection_observed_at`, `business_date`, temporal labels and false historical
  reconstruction;
- exact selected offer ID/vendor/SKU/pack;
- exact selected price-row/tier IDs and canonical ladder digest;
- exact vendor-rule and fee evidence digest;
- diagnostic legacy active-`STANDARD` offer IDs/count observed in the same
  snapshot, labeled `COMPARISON_ONLY_NO_AUTHORITY`; and
- explicit false flags for offer activation, price mutation, Shopify action, PO
  release and real/commercial authority.

The run's existing canonical input JSON and SHA-256 bind this evidence. Copy the
same object into `procurement_recommendations.metrics`; recommendation columns,
run price snapshots and DRAFT lines continue to carry the chosen offer ID,
vendor ID, supplier SKU, units per case, selected unit cost and fee terms.

For new selected-contract packets, enrich the existing
`supplier-mapping-evidence.json` item with the exact versioned evidence object
and payload digest. Do not add a ZIP member. Old runs retain their old member
shape and exact stored bytes.

## 7. Controlled synthetic fixtures

The fixed demo may seed offers, vendors, rules and synthetic CURRENT price rows
only through the legitimate pre-011 predecessor seam already used by the
initializer. It may not disable a trigger or alter migrations 011, 014 or 015.

This fixture change is fresh-database-only. The initializer inserts both
offers and their complete BASE/BREAK ladders before applying migration 011,
then applies the unmodified remaining migration chain and verifies the final
rows with all constraints enabled. An already initialized demo refuses the new
fixture contract rather than mutating protected CURRENT prices in place.

The positive causal fixture contains two schema-valid active `STANDARD` offers
for one Variant with distinguishable vendor/SKU/pack or economics. Both are
valid after the full current migration chain; each synthetic CURRENT ladder was
established before migration 011. The reviewed mapping candidate exactly links
one offer, and the browser separately selects it. The legacy resolver would see
ambiguous active-offer cardinality, while the selected resolver deterministically
uses the confirmed offer.

The initializer must not seed the mapping decision, selection event/head,
Monday run under test, recommendation, review decision or DRAFT. Existing fixed
stale-V1 lifecycle evidence remains separate.

A negative fixture retains an otherwise usable single legacy offer but no
selection head. Selected mode must create the exact blocker and no
recommendation/DRAFT. If a simultaneous alternative violates a final-state
constraint, use separate valid fixture states rather than weakening the
constraint.

Expected amounts are calculated independently in tests. At least one reviewed
quantity edit must cross an existing price break so the final DRAFT proves that
the complete selected-offer ladder, rather than the initial tier or live price,
drives recalculation.

## 8. Service, UI and packet surfaces

Keep selection consumption internal to Monday preparation. No request chooses
the resolution contract. The Monday list/detail pages visibly label new runs:

> SYNTHETIC SELECTED OFFER — TEST DATA / NO REAL AUTHORITY

The detail and review/DRAFT preview expose the selected offer ID, vendor,
supplier SKU, pack conversion, applicable ladder, fee evidence, mapping
decision, selection event/head version, observation time and source authority
states. Every displayed value is escaped. Existing emergency forecast
limitations remain visible.

The DRAFT remains `INTERNAL_DRAFT_ONLY`. There is no FINAL, release, import,
Shopify or supplier-contact control.

## 9. Acceptance proof

First add and execute a regression that creates a valid mapping and separate
selection for one offer while another schema-valid active offer exists. Against
the baseline implementation, Monday preparation must fail to produce the
selected recommendation because it ignores the head. Preserve that failing
output as the causal baseline.

Register new tests without reducing or repurposing the accepted strict 39+3
method population. Required proof includes:

1. default/real-disabled and old legacy-contract runs retain exact prior
   behavior and hashes;
2. the server capability requires every environment, rules, database, schema,
   role, migration and demo-marker attestation;
3. selected offer, SKU, pack, vendor, full ladder, fees and lineage are exact in
   manifest, recommendation, review preview, DRAFT line and packet;
4. a distinguishable alternative proves the resolver did not use legacy
   cardinality or an arbitrary offer;
5. missing/CLEAR selection blocks despite a usable legacy offer;
6. stale/superseded/rejected/mismatched selection and changed catalog/vendor/
   offer/rejection fingerprints block;
7. inactive, unpriced, wrong-vendor and incompatible-pack cases block without
   side effects;
8. quantity editing crosses a price break and uses only the frozen selected
   ladder and frozen fees;
9. deterministic multi-Variant lock order, held-lock refusal, concurrent
   selection coherence and complete rollback are observed. The rollback test
   injects a genuine public-service failure after run, inventory snapshot,
   forecast, run-price and recommendation inserts but before commit; a fresh
   observer connection proves zero partial rows, reviews, DRAFTs, artifacts or
   selection-head changes and the exact preexisting selection-head digest is
   unchanged. The service call is not hidden inside an outer test transaction,
   and the test does not assert sequence-counter rollback;
10. exact idempotent replay returns one run/DRAFT; conflicting reuse refuses;
11. late selection/price/vendor change blocks review/build through existing
    stale-input validation;
12. existing terminal DRAFT/packet replay returns stored bytes after restart;
13. actual Chromium creates mapping, selection, Monday run, review and DRAFT and
    downloads the exact packet; and
14. browser, database and packet lineage/economics agree with independently
    calculated expected values.

Run focused tests during implementation. Before acceptance, obtain independent
read-only backend/data review, remediate findings, then run the affected
combined set and actual browser/restart acceptance. Run the authoritative full
suite only if the remaining timebox safely permits it; report execution results
separately from unmet acceptance clauses.

## 10. STOP conditions and retained limitations

Stop and return a recoverable incomplete handoff if the implementation requires:

- a new migration or general temporal/shadow database;
- changing a historical migration or weakening uniqueness, activation, pricing,
  identity, pack, inventory, fee, audit or lifecycle guards;
- enabling a real/default mapping, cutover, activation, Shopify or PO-release
  control;
- accepting a request/caller label as capability;
- falling back to legacy resolution in enabled selected mode;
- seeding the mapping decision, selected head, recommendation, review or DRAFT;
- rewriting an old run or artifact;
- inventing supplier schedules, real approval, forecast/FVA/safety policy,
  strategic extra, DRAFT supersession or Shopify behavior;
- accessing an operational database, live Shopify, supplier or ordering system;
  or
- exceeding the fixed implementation or hard-handoff cutoff.

Emergency forecast limitations remain exact: model selection/FVA is
`NOT_VALIDATED`, classification and safety stock are `NOT_CALCULATED`, and
stockout censoring is `EVIDENCE_UNAVAILABLE`. Real price lifecycle, real-data
readiness, direct-SQL selected-head enforcement, production authentication,
native Shopify format, deployment and order release remain incomplete and
blocked.
