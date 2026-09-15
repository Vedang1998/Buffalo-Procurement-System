# Monday Mapping-to-Input Shadow Freeze Design

**Status:** owner-contract-authorized for isolated local synthetic construction;
design review required before implementation acceptance

**Base:** `22ab1cf800963a6d99e65eb210d8cfb1bbec0abd`, tree
`7c8da387d4e2b177dd3790763382247caa60914a`

**Authority:**
`BUFFALO-SATURDAY-PURCHASING-COMPLETION-2026-09-12`, the canonical system
specification, `procurement/config/rules.toml`, and the accepted persistent
multi-offer mapping design. This design implements only the already-required
read-only shadow comparison. It does not authorize recommendation cutover.

## 1. Outcome and invariant boundary

For a newly prepared Monday run, the owner can freeze and later review an exact
comparison between:

1. the legacy active-`STANDARD` offer evidence already frozen in the run input
   manifest; and
2. the routine selected-offer head and its immutable mapping lineage observed
   at a separately recorded database instant.

The comparison is durable, append-only, visible in the Monday browser page, and
included in the existing review packet. It is evidence about input identity and
cardinality only.

The following must remain byte-for-byte behaviorally unchanged:

- `recommendations._load_context()` and its exactly-one-active-`STANDARD`
  resolver;
- `procurement_input_manifest` and `input_fingerprint` construction;
- forecast inputs/results, readiness gates, blockers, recommendation rows,
  selected prices, units/cases/loose units, fees, economics and totals;
- review decisions, DRAFT construction, PO grouping and release state;
- every already-built packet and every historical run.

The stored authority label is exactly `SHADOW_ONLY`; the recommendation effect
is exactly `NONE`. Repository flags remain false. The owned synthetic overlay
may authorize capture, but no route or database function may activate an offer,
promote a price, modify a mapping/selection record, call Shopify, or create or
release an order.

## 2. Why the live view and run manifest are not the storage contract

`v_supplier_offer_selection_shadow` is a useful current diagnostic, but it is
head-driven, reads current rows, and evaluates validity with `current_date`.
It omits requested Variants with no selection head and cannot later prove what
was observed for a particular Monday run.

The shadow must not be added to `procurement_input_manifest`. That document's
exact UTF-8 hash is the run input fingerprint. Treating a non-authoritative
selection change as a material run-input change would break existing V2
idempotency and could strand an otherwise valid unbuilt run. The shadow is
therefore an independently hashed, run-bound evidence record.

## 3. Capture timing and truthful temporal semantics

The legacy Monday run commits first. Shadow capture then runs in its own
`SERIALIZABLE` transaction. This ordering guarantees that a missing capability,
stale selection, catalog verifier failure, serialization failure, or unexpected
capture error cannot roll back or alter the legacy run.

The capture binds both:

- the run's immutable `business_date`, `started_at`/evaluation instant and
  `input_fingerprint`; and
- the comparator's database `transaction_timestamp`, database date and
  timezone.

It never claims the selected head existed at run start. If the observed database
date differs from the run business date, identity/cardinality results remain
visible but temporal comparability is exactly
`AS_OF_DATE_MISMATCH_NOT_COMPARABLE`. No historical head is reconstructed from
today's rows.

Capture is optional to legacy Monday execution. Absence is rendered as
`NOT_CAPTURED`, never silently synthesized. A failed capture is an honest
shadow-feature failure, not a Monday recommendation blocker. Capture must occur
before `DRAFTS_BUILT` if it is to enter that run's packet.

## 4. Additive migration and trust chain

Add provisional migration
`procurement/db/016_monday_mapping_shadow_freeze.sql` as a new checksum-pinned
post-mapping application release. It requires exact installed and verified
migrations 014 and 015. Neither historical migration is edited, and the
`v1-shadow-only` mapping release remains unchanged.

The current post-mapping runner is specialized to the 015 retirement family.
Generalize it with a closed, literal `(family, version) -> verifier` registry.
Each registered family owns:

- exact source header, file name and source SHA-256;
- required mapping release and required earlier application releases;
- exact contract/catalog metadata keys and values;
- a Python-owned installed-catalog projection and literal SHA-256;
- family-specific partial-object detection, apply and verify functions.

Unknown families, versions, files, markers, gaps, reordered dependencies,
marker-only state, partial objects, altered source, altered catalog, or a
missing predecessor refuse before effects. Application markers are published
last. Full apply, replay, initializer early-return, launcher preflight and
restore verification all use the same registered verifier. Migration 016 is
not added to the legacy checksum prefix or the persistent-mapping release
manifest.

## 5. Evidence relation

Create `monday_mapping_shadow_evaluations` with no cascade path:

- `evaluation_id UUID PRIMARY KEY`;
- `evaluation_idempotency_key UUID NOT NULL UNIQUE`;
- `run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT`;
- exact `run_input_fingerprint`, `run_business_date`, `run_evaluation_at`;
- fixed `selection_scope='ROUTINE_PROCUREMENT_STANDARD'`;
- fixed `contract_version='BUFFALO_MONDAY_MAPPING_SHADOW_FREEZE_V1'`;
- fixed `authority_state='SHADOW_ONLY'`;
- fixed `recommendation_effect='NONE'`;
- `observed_at TIMESTAMPTZ`, `observed_database_date DATE`, and
  `observed_timezone TEXT`;
- server-derived `principal_ref`, `role_ref`, and
  `authn_context_sha256`;
- `canonical_payload JSONB NOT NULL` and `payload_sha256`;
- `created_txid BIGINT`.

All hashes are lowercase 64-character SHA-256. Timestamps are timezone-aware.
The run bindings must equal the immutable parent row/manifest. Actor fields must
equal the server-installed transaction-local human context. The table and its
functions remain owner-only with no `PUBLIC` privilege.

Every UPDATE and DELETE is rejected. A run can have more than one evaluation
only through a new idempotency key; each observation remains immutable and
chronologically visible. Exact replay of the same key and intent returns the
original row. Reuse of the key for another run, fingerprint, or actor is an
idempotency conflict.

The catalog contract covers the complete new relation, columns, constraints,
indexes, functions, triggers, ownership, ACL, RLS state and exact definitions.

## 6. Canonical payload

The payload contains only primitives and has exact key sets. Decimal and date
values are serialized as strings. Arrays are deterministically sorted. The
database computes the canonical JSONB SHA-256 and the insert trigger recomputes
the observation from the same snapshot before accepting it.

Top-level fields:

- contract, authority, recommendation effect and selection scope;
- run ID, run fingerprint, business date and run evaluation instant;
- observed instant/date/timezone and temporal-comparison status;
- complete requested Variant cohort from the frozen run manifest;
- one comparison item for every requested Variant, including missing heads;
- summary counts by comparison and selection state;
- explicit false flags for recommendation cutover, offer activation, price
  mutation, quantity mutation, Shopify action and PO action.

Each Variant item contains:

### Frozen legacy side

- the exact context position and requested Variant ID;
- whether legacy offer evaluation occurred in the frozen context;
- exact sorted active-`STANDARD` offer rows already present in the manifest,
  their count, offer IDs and canonical digest;
- the sole chosen legacy offer/vendor/SKU/pack/conversion evidence when one was
  chosen;
- existing legacy blocker codes, without recomputation.

The evaluator never queries a new legacy offer set for an old run. The manifest
is the only legacy side of the comparison.

### Observed selected side

- explicit `NO_SELECTION_HEAD`, never an omitted object;
- head event ID and version;
- event action, payload SHA-256, selected timestamp/transaction, effective
  bounds and all expected mapping/offer/catalog/vendor/rejection hashes;
- effective mapping decision ID, payload SHA-256, candidate/review batch IDs,
  evidence-set SHA-256, decision origin, link kind and result contract hash;
- batch source-authority/import states so identity parity cannot be mistaken
  for approved real source;
- selected offer ID, live offer fingerprint, vendor, supplier SKU, package,
  pack/conversion fields, confidence, active state and validity;
- current observed catalog, vendor and rejection-memory hashes needed to
  explain stale state.

The payload does not include secrets, source blobs, prices, margins, costs,
dollars, demand forecasts, cases, units, or counterfactual recommendations.

## 7. States and comparison rules

Selection state is evaluated against the explicit run business date and current
observed lineage, without using the existing view's `current_date` expression:

- `NO_SELECTION_HEAD`;
- `HEAD_CLEARED`;
- `HEAD_STALE_MAPPING_DECISION`;
- `HEAD_STALE_OFFER_CONTRACT`;
- `HEAD_STALE_CATALOG`;
- `HEAD_STALE_VENDOR`;
- `HEAD_STALE_REJECTION_MEMORY`;
- `HEAD_INELIGIBLE_VARIANT`;
- `HEAD_INACTIVE_VENDOR`;
- `HEAD_ACTIVE_REJECTION`;
- `HEAD_OUTSIDE_RUN_DATE_VALIDITY`;
- `SELECTED_ACTIVE`;
- `SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION`.

Identity/cardinality comparison is exactly one of:

- `MATCH` — selected state is `SELECTED_ACTIVE`, the frozen legacy set has
  exactly one active `STANDARD` offer, and its ID and exact offer-contract hash
  equal the selected offer;
- `NO_SELECTION_HEAD`;
- `HEAD_CLEARED`;
- `HEAD_STALE_OR_INELIGIBLE`;
- `SELECTED_INACTIVE_NO_LEGACY_CHANGE`;
- `LEGACY_HAS_NO_ACTIVE_STANDARD`;
- `LEGACY_HAS_MULTIPLE_ACTIVE_STANDARD`;
- `DIFFERENT_ACTIVE_STANDARD`.

There is no tolerance, score or materiality threshold. `MATCH` is exact identity
and hash equality only. It is not approval, cutover readiness, price coverage,
or commercial readiness.

## 8. Service, HTTP and browser behavior

Add a focused service module with:

- a pure frozen-manifest legacy projector;
- a selected-lineage observer using explicit columns and run business date;
- `capture_monday_mapping_shadow(...)` with SERIALIZABLE isolation,
  idempotency lock, parent `FOR SHARE`, exact contract verification and one
  append-only insert;
- `list_monday_mapping_shadow_evaluations(...)`, which reads only frozen rows.

Capture requires the existing synthetic
`selected_offer_shadow_reads_enabled` overlay and an authenticated
`procurement.order.approve` action principal. Repository mapping flags remain
false. Browser actor fields are neither accepted nor trusted.

Add `POST /monday-runs/{run_id}/mapping-shadow` and a run-level capture control
only while the run is `RUNNING` and `AWAITING_REVIEW` or `REVIEWED`. The outer
loopback/session/origin middleware authorizes before database I/O. Capture
errors return a typed non-success response while the previously committed run
remains intact.

The Monday detail page renders all stored evaluations under:

> Routine selection shadow — SHADOW ONLY / NO RECOMMENDATION EFFECT

It shows observation time, temporal status, cohort coverage, per-Variant legacy
cardinality, selected state, comparison, exact IDs/hashes and unapproved source
states. A historical run with no row says `NOT_CAPTURED`; it never displays a
live reconstruction.

## 9. Packet and recovery

For a new run with evaluations, enrich the existing
`supplier-mapping-evidence.json` top-level object with the exact frozen shadow
evaluation rows and payload hashes. Do not add another ZIP member. A run with
no evaluation retains the existing member shape. `PACKET_BUILT` replay returns
stored bytes and never re-queries or regenerates shadow evidence.

Browser, database and packet payload/hash equality is required. Backup/restore
state evidence includes the new table and its sequence/index state. The local
initializer applies and verifies 016 after the existing 014 -> seed/V1 -> 015
sequence. Early return verifies exact 014, 015 and 016 source/marker/catalog
contracts before accepting the demo marker. The launcher refuses missing or
altered 016 source, metadata, catalog, trigger or marker before serving.

## 10. Acceptance tests

Add a dedicated exact-role PostgreSQL module and register its exact discovered
floor. Preserve the accepted 39+3 module identities and counts.

Required cases:

1. Fresh and upgrade order is 014 -> 015 -> 016; exact replay skips all bodies.
2. Wrong/missing source, predecessor, marker, catalog, function, trigger, ACL,
   partial object or unregistered family refuses before effects.
3. Capture requires SERIALIZABLE, exact parent bindings, correct run type/mode,
   an eligible pre-DRAFT stage, synthetic capability and server-derived actor.
4. UPDATE/DELETE, forged payload/hash/txid, incomplete cohort and malformed
   lineage refuse with no partial row.
5. Exact idempotent replay returns one row; changed intent conflicts; concurrent
   same-key capture has one winner/one replay; different keys append complete
   immutable observations.
6. Known-answer rows cover every selection and comparison state, including no
   head, clear, stale hashes, inactive selected, zero/multiple/different legacy
   offers and exact match.
7. Run-date/database-date mismatch is explicit and cannot become comparable.
8. A selection race yields one coherent pre- or post-head observation, never a
   mixed event/decision/offer fingerprint set.
9. Failed or disabled capture leaves the already-prepared run, fingerprint,
   recommendations, prices, quantities, reviews and DRAFT eligibility exact.
10. Selection before or after preparation does not change `_load_context()`,
    `monday_run_inputs_match()`, the run input fingerprint, recommendation
    projection, price snapshots, quantities, fees or totals.
11. Old runs remain `NOT_CAPTURED`; no API, replay, initializer or migration
    backfills them.
12. Browser capture is authorized before I/O, visibly bounded, and survives
    restart. Browser, DB and packet evidence are byte/hash equal.
13. Existing 12-member packet shape is retained; terminal packet bytes replay
    exactly; pre-contract DRAFTS are not recaptured.
14. Backup/restore reproduces relation rows, hashes and launcher verification.
15. Full suite population/floors rise only after actual discovery; all abnormal
    counters remain zero.

Independent SQL/backend review and business-boundary review are required after
focused tests. Final acceptance requires a clean exact commit, affected tests,
the authoritative suite, real browser endpoints and restart/backup/restore
evidence.

## 11. Explicitly out of scope / STOP conditions

Stop and return to design/owner authority if any implementation would:

- read the selected offer from `_load_context()` or substitute it into price,
  forecast, recommendation, review, DRAFT or PO logic;
- change a run fingerprint, existing manifest, recommendation, blocker, gate,
  quantity, price, cost, fee, total or packet already built;
- activate/deactivate an offer, write/promote/carry a price, alter mapping or
  selection authority, call Shopify, contact a supplier, release a PO or create
  a real order;
- calculate selected-offer economics or a counterfactual quantity;
- backfill an old run from current mapping state or call the result historical;
- enable a repository mapping/cutover/activation flag or widen the synthetic
  runtime/database boundary;
- alter migrations 014/015 or weaken their checksums, catalogs, constraints,
  role topology, privileges or accepted 39+3 proof.

Any later economic/quantity counterfactual, tolerance, inactive/unpriced
policy, selection activation, recommendation cutover, or definition of how
many shadow Mondays are sufficient requires a new owner-approved design.
