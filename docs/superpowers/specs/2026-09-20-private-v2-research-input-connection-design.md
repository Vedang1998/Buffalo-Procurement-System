# Private V2 Research-Input Connection Design

**Approved:** 2026-09-20  
**Frozen parent:** `74002f473e7794d0b2eca433a2b0ebc2d8936820` / `500e8d4bb0da3e49d1f6ee669e92d751da426d2d`  
**T0:** `2026-09-20T13:37:32Z`  
**Hard stop:** `2026-09-20T17:37:32Z`

## Objective and boundary

Connect sufficiently covered, real daily sales observations to the existing
registered Development Forecast V2 engine for private, zero-authority research.
The result is not an approved forecast policy, supplier calendar, purchasing
recommendation, DRAFT, PO, or order. The frozen V1 intake/projection, source
captures, exports, and parent commit remain unchanged.

## Additive contracts

The implementation adds a content-addressed private V2 research-input envelope
and V2 projection envelope. V1 continues through its existing exact contract.
V2 records and validates:

- the registered forecast evidence, policy, and method identities;
- policy source and canonical SHA-256 values;
- source intake and capture identities;
- shop, timezone, currency, query dimensions, metrics, response hashes,
  request timestamps, row counts, and completeness controls;
- an exact contiguous 138-day composite window;
- historical identity authority artifact identities and a derived allocation
  ledger;
- fixed H3, H10, and H17 `RESEARCH_SCENARIO_ASSUMPTION` entries; and
- zero commercial, production, mapping, pricing, purchasing, and order authority.

Unknown, partial, or mixed tuples fail without falling back to V1. The adapter
explicitly loads the registered V2 policy and passes it into the existing planner.
It never registers real input as a synthetic fixture and never loads the fabricated
V2 supplier schedule.

## History composition

The preserved V1 capture covers 2026-06-27 through 2026-09-18. A new read-only
extension may cover 2026-05-04 through 2026-06-26. The composite validates exact
adjacency, consistent shop/timezone/currency/query schema/metric semantics, no
duplicates or gaps, and per-request native response hashes and timestamps.
Different capture times remain explicit.

Each current Variant receives one observation for each attested complete date.
Absence on a completely queried day is observed zero sales only for a Variant
whose current or approved historical identity is in scope. Missing queries,
unresolved identities, and pre-creation periods are never padded. Every mapped
observation uses `inventory_state=UNKNOWN`; current inventory and raw incoming do
not establish historical availability, stockout, latent demand, or trusted incoming.
Signed units, revenue, and historical COGS remain unchanged.

## Historical identity

The adapter validates the exact seed-alias bundle and both owner-approved Phase-4
authority manifests before use. It applies only exact current IDs, applicable
source-key decisions, or conflict-free approved old-ID families whose target is
in the current catalog. It does not use SKU/title similarity or broaden a scoped
decision into a universal alias.

Raw rows remain immutable. A separate allocation ledger assigns every row exactly
once to direct current identity, approved historical allocation, approved
exclusion, absent-current target, or quarantine. Units/revenue/COGS control totals
must reconcile. An unresolved predecessor never becomes zero demand for a current
Variant.

## Forecast scenarios and outputs

H3, H10, and H17 run independently from the same validated history, with the
retrospective origin frozen at 2026-09-19 and half-open target intervals recorded.
They are research assumptions, not vendor schedules or delivery promises.
Detailed results are keyed by Variant, horizon, input identity, and policy identity.

The compact owner table has one row per Variant and three scenario summaries.
Large histories, allocation evidence, and model evidence remain in hashed sidecars.
Shared gaps appear once. Per-result status distinguishes a calculated numerical
zero, a blocked calculation, and no source observations.

Downstream stages remain independent:

1. `CAPTURE`
2. `IDENTITY`
3. `FORECAST`
4. `ABC`
5. `NET_NEED`
6. `CASE_QUANTITY`
7. `ECONOMICS`
8. `ORDER`

A valid pure forecast is not erased by missing packs/prices/incoming. Those inputs
continue to block only their dependent stages. Exact S1 remains a separate missing
source and cannot block sales-only forecasting.

## Validation

Tests first preserve the causal red: shaped 138-day evidence is rejected by the
old exact-84 adapter/default-V1 path, while legacy 84-day V1 remains valid. New
tests cover explicit V2 dispatch, tuple mismatch, 138-day completeness, gaps,
duplicates, incompatible partitions, UNKNOWN availability, identity-scope
conflicts, exact-once allocation, H3/H10/H17 separation, deterministic replay,
future-data isolation, a history perturbation through the private service, escaping,
and absence of operational paths. V1 bytes and behavior remain golden.

Focused checks precede the final combined suite, startup tests, and actual private
viewer/restart acceptance. Private rows and exports stay outside Git and the code
transport. Material logic receives independent read-only review before closeout.
