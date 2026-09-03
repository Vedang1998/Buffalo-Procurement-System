# Phase 4 Terminal-Disposition Implementation Design

**Date:** 2026-08-25  
**Status:** Owner-approved design; implementation not yet started  
**Risk:** Level 3 / HIGH  
**Authorized scope:** Runtime/schema implementation and disposable-PostgreSQL validation only  
**Starting main:** `701548dfacbc35d505f1d726146c268d6e42260d`  
**Terminal artifact:** `procurement/review/phase4_terminal_disposition_manifest.csv`  
**Terminal artifact SHA-256:** `fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff`

## 1. Purpose and authorization boundary

Implement the merged Phase 4 terminal-disposition authority so a later,
separately authorized production milestone can:

1. restore 43 exact deleted Shopify Variant IDs as inactive historical-only
   canonical identities;
2. append the owner-approved terminal decisions for the 280 source identities
   that are currently `LEAVE_UNRESOLVED`;
3. retain the original eight exclusions with their original primary manifest
   provenance while attaching supplementary terminal authorization provenance;
4. distinguish the two allowlisted exclusion reasons;
5. prove material exclusion integrity from authoritative database state; and
6. use the existing resolver/finalizer architecture for a controlled future
   rebuild.

This implementation milestone does **not** authorize a production connection,
production dry-run, production DML, production historical restoration,
production decision persistence, historical-sales re-resolution/rebuild,
readiness evaluation or gate write, Shopify access/write, Vendor Rules,
forecasting, procurement, recommendations, or PO actions.

The frozen artifact and approved business dispositions are inputs. The
implementation must not change them.

## 2. Frozen controls

### 2.1 Terminal supplement

| Disposition | Source keys | Raw rows | Net units | Absolute units | Net sales | Absolute sales |
|---|---:|---:|---:|---:|---:|---:|
| `RESTORE_HISTORICAL_IDENTITY` | 43 | 435 | 511.0000 | 511.0000 | $8,506.52 | $8,506.52 |
| `MAP_TO_CANONICAL` | 47 | 200 | 232.0000 | 232.0000 | $4,096.03 | $4,096.03 |
| `EXCLUDE_UNATTRIBUTABLE` | 190 | 1,465 | 1,798.0000 | 1,808.0000 | $37,695.37 | $40,685.37 |
| **Total** | **280** | **2,100** | **2,541.0000** | **2,551.0000** | **$50,297.92** | **$53,287.92** |

### 2.2 Combined terminal intent

- 43 `RESTORE`
- 102 `MAP`
- 198 `EXCLUDE`
- 0 `LEAVE_UNRESOLVED`
- 343 total exact source keys
- 96 distinct MAP targets
- 1,023 MAP raw rows

### 2.3 Eventual rebuild controls

- 59,083 source facts
- 57,429 `RESOLVED`
- 1,654 `EXCLUDED`
- 0 `UNRESOLVED`
- 0 `AMBIGUOUS`
- 80,659.0000 resolved net units
- 80,693.0000 resolved absolute units
- $1,263,133.84 resolved net sales
- $1,264,065.52 resolved absolute sales
- 1,842.0000 excluded net units
- 1,852.0000 excluded absolute units
- $37,841.30 excluded net sales
- $40,855.28 excluded absolute sales
- projected `sales_daily`: 57,424 rows / 80,659.0000 units /
  $1,263,133.84 sales

These controls must be independently recomputed from test fixtures and database
state. Constants alone are not acceptance evidence.

## 3. Chosen architecture

### 3.1 One canonical variant model

Continue using `variants` as the only canonical Shopify Variant identity model.
Add a constrained identity scope:

- `CURRENT`
- `HISTORICAL_ONLY`

Do not create a parallel historical-identity table and do not fabricate Shopify
Product IDs.

The database must enforce these bidirectional invariants:

- `identity_scope = 'HISTORICAL_ONLY'` implies `active = FALSE`;
- `identity_scope = 'HISTORICAL_ONLY'` implies
  `catalog_state = 'RETIRED_CONFIRMED'`;
- `identity_scope = 'HISTORICAL_ONLY'` implies `product_id IS NULL`;
- every non-`HISTORICAL_ONLY` variant requires a non-null, non-blank real
  `product_id`.

Restored rows retain the exact historical Shopify Variant ID as `variant_id`.
Their historical SKU, product title, variant title/size, and artifact-derived
restoration provenance are recorded. They are inactive audit identities, not
current catalog products.

### 3.2 Operational ineligibility

Historical-only identities may be referenced by immutable ShopifyQL source
facts, historical sales, review-decision history, resolution audit evidence,
and archival records.

They must be prohibited from active/current operational use in:

- active supplier offers;
- current price/cost eligibility;
- current inventory/replenishment eligibility;
- forecasts;
- recommendations;
- procurement candidates/runs; and
- purchase-order lines.

Enforcement is defense in depth:

1. database constraints/triggers reject creation or activation of operational
   rows for a `HISTORICAL_ONLY` variant;
2. changing a variant to `HISTORICAL_ONLY` fails if prohibited active/current
   dependent state exists;
3. operational service/query paths explicitly require an eligible current
   variant rather than relying only on `active`; and
4. disposable-PostgreSQL adversarial tests exercise every protected pathway.

Audit/history tables are not broadly prohibited. A guard must distinguish an
active operational eligibility record from immutable historical evidence.

### 3.3 Sole append-only decision authority

`historical_sales_review_decisions` remains the only source-key decision
authority. Extend it rather than introducing a competing decision table.

The ledger must support:

- `RESTORE_HISTORICAL_IDENTITY` as an effective action;
- structured exclusion `reason_code`;
- primary manifest SHA-256 and primary manifest row;
- evidence schema/version;
- review/owner authorization provenance;
- authority Git SHA;
- execution Git SHA; and
- append-only supersession.

Action constraints are:

- `MAP_TO_CANONICAL` requires a canonical target;
- `RESTORE_HISTORICAL_IDENTITY` requires a canonical target equal to the exact
  nonzero source Variant ID;
- `EXCLUDE_HISTORICAL_ITEM` requires no canonical target and an allowlisted
  structured reason;
- `LEAVE_UNRESOLVED` requires no canonical target.

No existing decision row is rewritten.

## 4. Provenance model

### 4.1 Remaining 280 source keys

The 280 terminal decisions use as primary provenance:

- terminal manifest SHA-256
  `fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff`;
- the exact terminal manifest row; and
- the artifact evidence version.

They append-only supersede the prior 280 `LEAVE_UNRESOLVED` rows.

### 4.2 Original eight exclusions

The original eight exclusions retain as primary provenance:

- original 343-row manifest SHA-256
  `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`;
- their exact original manifest row.

Terminal authorization, authority Git SHA, evidence version, and execution Git
SHA are supplementary provenance. The eight must never be assigned the 280-row
terminal manifest SHA or row as their primary provenance.

If structured normalization is needed, append a superseding row with the same
effective action/target and correct original primary provenance. Preserve the
prior row.

## 5. Exact prestate and conflict semantics

The tool classifies the database as a whole, not as independently repairable
rows.

### `PRE_TERMINAL_EXACT`

This is limited to the exact known pre-terminal state:

- the exact 280 source keys have the expected latest original
  `LEAVE_UNRESOLVED` decisions and approved original provenance;
- the exact eight exclusions have their expected legacy-compatible action,
  target, source scope, and original provenance;
- none of the 43 restoration rows or terminal-only aliases exists;
- no terminal decision/provenance subset exists; and
- protected catalog, source, gate, and PO controls match.

Only specifically enumerated original-eight structured normalization and the
280 approved LEAVE supersessions are legacy-compatible.

### `CURRENT_TERMINAL_EXACT`

All 343 deterministic latest decisions, 43 restored identities, safe aliases,
198 active exclusions, reason codes, and provenance exactly match the expected
terminal state. The operation performs zero DML.

### `CONFLICT`

Everything else is a hard stop, including:

- partial terminal state;
- mixed pre-terminal and terminal state;
- unknown or partial provenance;
- incompatible action, target, reason, run, source key, or manifest membership;
- an unexpected restored row, alias, or active exclusion; or
- a compatible-looking row outside the explicitly approved legacy cases.

There is no automatic repair of a partial terminal attempt.

## 6. Execution Git identity

Execution provenance must come from the actual committed/build implementation
identity, never from an arbitrary caller value.

For repository execution, the tool:

1. resolves the repository worktree containing the running implementation;
2. derives the execution SHA using Git's `HEAD` object;
3. requires a clean tracked worktree/index and verifies the relevant runtime,
   migration, and artifact files are from that commit; and
4. persists only that derived SHA.

A separately supplied owner-authorized SHA is an expected-value assertion only.
It must equal the derived SHA but is never treated as the observed execution
identity. If the repository/build identity cannot be derived and verified, the
operation fails closed before database mutation. A future packaged build would
need equivalent immutable build metadata; this implementation does not silently
fall back to caller input.

## 7. Terminal transaction

The persistence service is separate from rebuild/finalization. It must not call
the resolver finalizer, evaluate readiness, or write a gate.

The transaction uses `SERIALIZABLE` isolation and a task-specific PostgreSQL
advisory lock:

1. Verify clean committed execution identity.
2. Verify both manifest hashes, schemas, row membership, canonical source keys,
   dispositions, continuity controls, Fiesta prohibition, High Noon and Popov
   controls, targets, and all frozen totals.
3. Open the transaction and acquire the advisory lock without waiting.
4. Capture protected source, sales/aggregate, readiness-gate, and PO
   fingerprints.
5. Lock/read the exact relevant decision, variant, alias, and exclusion state.
6. Classify the entire state as `PRE_TERMINAL_EXACT`,
   `CURRENT_TERMINAL_EXACT`, or `CONFLICT`.
7. If current exact, return a no-op report.
8. If pre-terminal exact:
   - insert the 43 historical-only variant rows;
   - append 43 RESTORE, 47 MAP, and 190 terminal EXCLUDE decisions;
   - append the eight approved structured exclusion normalizations;
   - create only safe uniform old-ID aliases, excluding zero/null IDs and
     restored self-identities;
   - reconcile exactly 198 active exclusions with structured reasons and the
     effective decision rows.
9. Re-read effective state and recompute all controls from database rows.
10. Require protected fingerprints unchanged and planned mutation counts exact.
11. Commit once; any exception rolls back everything.

Material-stage failure injection proves rollback after each mutation stage.
Concurrency tests prove the lock fails closed. A second complete execution must
be a true zero-DML no-op.

## 8. Resolver behavior

Reuse `HistoricalIdentityIndex`; do not create a second resolver.

- An exact inactive, non-recreated historical-only variant resolves as
  `EXACT_PRESERVED_HISTORICAL_VARIANT_ID`.
- An effective source-key MAP resolves as
  `APPROVED_SOURCE_IDENTITY_DECISION`.
- Only a complete nonzero old-ID family agreeing on one target may receive an
  approved alias.
- A zero/null source Variant ID never creates a broad alias.
- A restored exact identity never receives a recreation alias to itself.
- The original eight resolve as `EXPLICIT_EXCLUSION`.
- The 190 terminal unattributable identities resolve as
  `EXPLICIT_UNATTRIBUTABLE_EXCLUSION`.

## 9. Exclusion reasons and integrity

The exact Phase 4 allowlist is:

- `PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION`
- `HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW`

Expected reason buckets are:

| Reason | Keys | Raw rows | Net units | Absolute units | Net sales | Absolute sales |
|---|---:|---:|---:|---:|---:|---:|
| Original exact non-product | 8 | 189 | 44.0000 | 44.0000 | $145.93 | $169.91 |
| Exhaustively unattributable | 190 | 1,465 | 1,798.0000 | 1,808.0000 | $37,695.37 | $40,685.37 |

Readiness never trusts a caller-supplied `exclusion_integrity_reconciled`
boolean. It derives integrity from authoritative database state using the
deterministic latest decision ordered by `decided_at DESC`, then decision UUID.

For every material excluded exact source key, it proves:

- latest effective decision for the exact run/key is EXCLUDE;
- owner authorization is present;
- reason code is allowlisted;
- primary and supplementary provenance are correct;
- no canonical target exists in the decision, exclusion, or raw resolution;
- active exclusion membership points to/matches the effective decision;
- raw source-key membership is exact and every fact is excluded;
- rows, net/absolute units, and net/absolute sales reconcile per reason bucket;
- no extra active exclusion exists; and
- original-eight membership remains exact.

Any mismatch emits `EXCLUSION_INTEGRITY_NOT_PROVEN` with deterministic
diagnostics and keeps readiness failed. An invalid latest row fails even if an
older valid row exists. A valid latest row may supersede older invalid history;
superseded history is not an effective conflict.

## 10. Frozen MAP target-flow byte contract

The implementation must reproduce the reviewed audit algorithm, made explicit
and platform-independent here.

### 10.1 Flow derivation

Consider only facts whose eventual resolution method is
`APPROVED_SOURCE_IDENTITY_DECISION`. Group them by canonical target.

Within each target:

1. collect exact `source_identity_key` values;
2. sort the unique keys lexicographically by Unicode code point and freeze them
   as an immutable tuple;
3. count that tuple for the serialized `source_keys` integer;
4. count raw facts for `raw_rows`;
5. sum net units, absolute units, net sales, and absolute sales using
   `Decimal`; and
6. reject values with precision beyond four unit decimals or two sales
   decimals instead of silently rounding.

The reviewed serialization emits the source-key **count**, not the key strings.
The sorted tuple is nevertheless part of canonical flow derivation and must be
tested so source input or database return order cannot affect membership or
bytes.

Sort the 96 target records lexicographically by the string Shopify Variant ID
before serialization. Do not use database collation.

### 10.2 Record representation

Each target record is a JSON object with these logical fields:

```text
target: string
source_keys: integer
raw_rows: integer
net_units: string formatted exactly with four fractional digits
absolute_units: string formatted exactly with four fractional digits
net_sales: string formatted exactly with two fractional digits
absolute_sales: string formatted exactly with two fractional digits
```

The complete value is a JSON array. Serialize with Python-equivalent semantics:

```python
json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
```

Consequently, each object's byte-level key order is:

```text
absolute_sales, absolute_units, net_sales, net_units,
raw_rows, source_keys, target
```

There is no indentation or optional whitespace. JSON uses double quotes, decimal
values remain quoted strings, integer fields are unquoted, encoding is UTF-8,
and the document ends with exactly one byte `0A`. There is no BOM and no CRLF.

The canonical bytes must be compared to a committed golden byte fixture before
hashing. Only after exact byte equality is established is SHA-256 computed. The
required digest is:

```text
5d6832cea3df7a4f45d31d7e7a8100409ddc078936095fe3c775ce862a1f64a6
```

Tests must prove:

- the exact complete canonical byte sequence matches the golden fixture;
- its SHA-256 matches the required digest;
- reordered facts, targets, and source keys yield identical bytes;
- changing any target, source-key membership/count, raw-row flow, unit flow, or
  sales flow changes the bytes and digest; and
- locale, PostgreSQL collation, and platform newline settings cannot change the
  output.

## 11. Rebuild plumbing

The existing resolver/finalization architecture remains authoritative. A
gate-free controlled rebuild core may be extracted for disposable integration
testing and a later separately authorized production milestone. The public
finalizer must derive exclusion integrity from its database connection and may
then evaluate readiness using that evidence; it must not accept a trusted
caller boolean.

This implementation milestone does not execute that rebuild or evaluator in
production and does not write a readiness gate.

## 12. Test and validation strategy

Implementation is test-first.

### Unit and artifact tests

- exact artifact SHA and 280 unique canonical keys;
- exact 43/47/190 supplement and 43/102/198 combined controls;
- all 19 predecessor/successor pairs;
- Fiesta target count zero;
- High Noon 3/3 terminal exclusion;
- Popov 2/2 terminal exclusion;
- exact original eight membership/provenance;
- canonical MAP bytes and digest;
- reason allowlist and provenance validation;
- effective-latest decision semantics; and
- exact prestate/current-state/conflict classification.

### Schema and resolver tests

- migration forward success;
- historical-only invariant combinations;
- current variants still require real Product IDs;
- exact-ID restored resolution;
- historical/audit references permitted;
- every operational eligibility path blocked;
- safe alias family rules; and
- reason-specific exclusion resolution methods.

### Disposable PostgreSQL tests

- production-shaped exact prestate apply;
- exact terminal poststate and all readback controls;
- second execution zero-DML no-op;
- partial/mixed/unknown state conflict;
- rollback after every material stage;
- transaction and advisory-lock concurrency behavior;
- exclusion-integrity success;
- target-bearing exclusion, missing provenance, unknown reason, source mismatch,
  partial fact exclusion, financial drift, extra exclusion, and exact-eight
  mutation failures;
- unchanged source, aggregate, gate, and PO fingerprints; and
- frozen projected resolution methods and final financial controls.

### Repository validation

- targeted tests pass;
- migration/disposable integration tests pass;
- `./scripts/procurement-tests` reports zero failures, errors, skips, expected
  failures, or unexpected successes;
- Python compilation passes;
- `git diff --check` passes;
- normal secret/generated-artifact scan passes;
- changed files contain no production, Shopify, procurement, or PO execution
  path; and
- migration safety/reversibility receives explicit inspection.

No GitHub Actions run is intentionally consumed during implementation
iteration.

## 13. Git and review checkpoint

Work occurs on `codex/phase4-terminal-disposition-implementation` directly from
`701548dfacbc35d505f1d726146c268d6e42260d`, with Codex as sole writer.

The complete implementation and disposable validation evidence will be
committed and pushed without opening a PR. `docs/CODEX_HANDOFF.md` and
`procurement/docs/PHASE_STATUS.md` are not changed merely because code exists.

The implementation then stops. The exact next action is:

> Independent adversarial review of the committed implementation and
> disposable-PostgreSQL evidence. Production execution remains unauthorized.
