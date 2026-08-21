# Phase 4 Historical-Sales Re-resolution/Rebuild Implementation Plan

**Status:** Planning complete; implementation not started
**Approved design:** `docs/superpowers/specs/2026-08-20-phase4-historical-sales-reresolution-rebuild-design.md`
**Branch:** `codex/phase4-historical-sales-reresolution-rebuild-planning`
**Starting main:** `97fe3868fa87d17d1a8f236d993c35cd8db83805`
**Production run:** `d389079c-eabf-49b5-a245-40a207025fd7`
**Decision manifest SHA-256:** `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`

## Authorization and stop conditions

This checkpoint authorizes only design, implementation planning, and read-only
production analysis. Do not begin any task below until the owner separately
authorizes implementation.

Implementation authorization would permit runtime/test changes and disposable
PostgreSQL validation only unless the owner expressly expands it. Production
dry-run, production DML, re-resolution/rebuild, `SALES_BACKFILL` or other gate
write/reevaluation, Shopify, Vendor Rules, forecasting/procurement, and PO work
remain separately unauthorized.

Stop on any authority conflict or deterministic-control mismatch. Never change
the evaluator or identity policy merely to reduce the unresolved population.

## Planned file scope

### Add

- `procurement/src/procurement_os/historical_sales_rebuild.py`
  - controlled state classification, expected-resolution simulation,
    fingerprints, transaction orchestration, and readback;
  - orchestration only; it must use the existing identity resolver and rebuild
    primitives.
- `procurement/tools/rebuild_phase4_historical_sales.py`
  - narrow deterministic `--dry-run`/`--apply` operator command;
  - no Shopify client and no business-rule SQL.
- `procurement/tests/test_phase4_historical_sales_rebuild.py`
  - pure expected-control, state-classification, evaluator, and CLI tests.
- `procurement/tests/test_phase4_historical_sales_rebuild_postgres.py`
  - disposable PostgreSQL locking, transaction, rollback, reconciliation, and
    idempotence tests.

### Modify

- `procurement/src/procurement_os/historical_sales.py`
  - extract the minimum gate-free reusable re-resolution/rebuild core from the
    existing finalizer;
  - retain current `finalize_sales_backfill()` behavior for existing callers.
- `procurement/tools/run_tests.py`
  - register the two new modules with fail-closed module minimums.
- `docs/CODEX_HANDOFF.md`
  - only after a separately authorized and independently reviewed production
    milestone.
- `procurement/docs/PHASE_STATUS.md`
  - only after a separately authorized and independently reviewed production
    milestone; do not mark Phase 4 complete.

No schema migration, manifest/decision edit, configuration change, API, Shopify,
resolver-policy, Vendor Rules, forecasting, procurement, or PO file is planned.

## Frozen execution controls

Every pure test, PostgreSQL test, dry-run, apply preflight, and fresh readback
must use the exact controls in the approved design. The core stop controls are:

- decisions: 343 = 55 MAP + 8 EXCLUDE + 280 LEAVE_UNRESOLVED;
- materiality: 341 material + 2 nonmaterial;
- distinct MAP targets: 51;
- approved old-ID alias families: 17;
- affected raw facts: 3,112 = 823 MAP + 189 EXCLUDE + 2,100 LEAVE;
- affected absolute units/sales: 3,696.0000 / $72,616.29;
- immutable run facts: 59,083, net 82,501.0000 units and $1,300,975.14;
- expected status: 56,794 RESOLVED, 189 EXCLUDED,
  2,100 UNRESOLVED, 0 AMBIGUOUS;
- expected aggregate: 56,789 rows, 79,916.0000 units, $1,250,531.29;
- expected evaluator: FAIL with only
  `MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED`;
- POs/PO lines: 0/0; and
- every stored readiness-gate row unchanged.

## Task 1 — Freeze the planning contract in tests

### Tests first

Add pure fixtures/constants from the approved design and tests proving:

1. exact starting run and manifest SHA;
2. exact 343/55/8/280/51/17 decision controls;
3. exact disposition rows, units, sales, and materiality controls;
4. exact post-rebuild status and resolution-method controls;
5. exact 51 MAP-target/raw-row flows;
6. NUTRL Fruit 3/3 to `41716813627467`;
7. zero Fiesta mappings to `41193000796235`;
8. High Noon Tequila 3/3 remains LEAVE_UNRESOLVED;
9. exact exclusion membership; and
10. LEAVE_UNRESOLVED evidence produces Outcome A and the sole expected blocker
    from the unchanged pure evaluator.

### Implementation

Create immutable control dataclasses and expected-control constants in the new
rebuild module. Reuse the existing manifest and decision readback functions; do
not introduce a second parser or decision authority.

### Verify

Run the new pure test module. Review all constants against the approved design
and planning-analysis output before continuing.

## Task 2 — Extract the existing gate-free rebuild core

### Tests first

Extend existing Phase 4 historical-sales tests to prove:

- the extracted core produces the same resolutions, status totals, coverage,
  source accounting, and aggregate as the current finalizer;
- `finalize_sales_backfill()` still evaluates/writes its gate for its existing
  callers exactly as before;
- the new controlled path cannot import/call `_set_sales_gate`,
  `rerun_sales_identity_resolution`, or a Shopify client; and
- resolver order and methods are unchanged.

### Implementation

Refactor `historical_sales.py` only enough to expose internal gate-free helpers
for:

- re-resolving one frozen run using `load_identity_index()` and
  `_resolution_values()`;
- computing status, coverage, page-structure, source-control, resolution-
  accounting, and canonical reconciliation evidence;
- rebuilding the run-range `SHOPIFYQL_SALES` aggregate; and
- updating the existing run's resolution/control fields.

The public finalizer composes those helpers plus the current evaluator and gate
setter. The new orchestrator composes the same helpers without the evaluator or
gate setter inside its write transaction. No SQL or matching rule is duplicated.

### Verify

Run `test_phase4_historical_sales.py`, `test_sales.py`, and the new pure module.
Compare before/after source inspection of the resolver and existing finalizer.

## Task 3 — Implement deterministic read-only preflight and simulation

### Tests first

Using disposable PostgreSQL with production-shaped fixtures, prove that dry-run:

- requires an exact run and current-provenance 343-row effective ledger;
- requires 55/8/280 decisions, 51 targets, 17 alias families, and eight exact
  exclusions;
- requires exact source facts, keys, observations, magnitudes, date coverage,
  chunk/page structure, and duplicate controls;
- recomputes every fact through the existing resolver and matches all expected
  status, method, target-flow, aggregate, and special-case controls;
- detects missing/added/changed facts, decisions, aliases, exclusions, targets,
  gates, or POs;
- uses a DB-enforced read-only transaction and has null
  `txid_current_if_assigned()` before and after; and
- emits deterministic, credential-free JSON.

### Implementation

Add focused functions such as:

- `load_rebuild_preflight(conn, run_id)`;
- `simulate_run_resolutions(conn, run_id, identity)`;
- `classify_rebuild_state(preflight, simulation)`;
- `protected_rebuild_fingerprints(conn, run_id)`;
- `dry_run_rebuild(conn, context)`.

Classification must be exactly:

1. `NEEDS_REBUILD`: exact approved prestate;
2. `CURRENT_REBUILD_STATE`: exact approved poststate and zero planned changes;
3. `CONFLICT`: anything else, with no DML path.

The expected mutation plan from the exact prestate is 1,012 raw resolution
updates: 823 MAP and 189 EXCLUDE. LEAVE_UNRESOLVED facts receive no invented
resolution. The aggregate plan is 55,966 current rows to 56,789 expected rows.

### Verify

Run both new rebuild modules against disposable loopback PostgreSQL and inspect
the stable JSON report.

## Task 4 — Add the single-transaction controlled apply

### Tests first

Add disposable PostgreSQL tests for:

- exact `NEEDS_REBUILD` applies once and matches every approved post-control;
- `CURRENT_REBUILD_STATE` performs zero writes and does not advance a timestamp;
- partial current state is `CONFLICT`, never resumed or silently repaired;
- exact advisory-lock use and `SERIALIZABLE` transaction isolation;
- concurrent or changed-state execution fails closed;
- injected failure before, during, and after raw updates rolls back every change;
- aggregate mismatch, control drift, or fingerprint drift rolls back;
- source payloads, run-fact membership, ledger history, aliases, exclusions,
  variants, gates, and PO data remain byte-for-byte unchanged;
- only expected run resolution/control fields may change; and
- no gate setter/evaluator runs inside the write transaction.

### Implementation

Add `apply_controlled_rebuild(conn, context)`:

1. require `NEEDS_REBUILD` from a prior dry-run report but trust no cached data;
2. open one `SERIALIZABLE` transaction;
3. acquire `acquire_backfill_transaction_lock()` and lock the exact run row;
4. repeat full static/DB preflight and protected fingerprints;
5. load one identity index and recompute all predicted resolutions;
6. apply only the 1,012 exact resolution-metadata changes;
7. execute the existing aggregate rebuild for only the authorized run range;
8. update allowed run control evidence/counts;
9. reconcile exact post-controls and protected fingerprints; and
10. commit once or roll back everything.

Do not add progress commits, repair modes, force flags, batches, gate writes, or
data-changing fallback paths.

### Verify

Run the PostgreSQL module repeatedly, including all failure-injection points and
the true-no-op second execution.

## Task 5 — Add the narrow operator command

### Tests first

Prove the command:

- accepts exact run ID and exactly one of `--dry-run`/`--apply`;
- rejects apply unless HEAD is a full committed SHA and worktree is clean;
- reuses the existing reconciliation review authorization and rejects missing
  or invalid apply authorization before creating a write-capable connection;
- validates the approved manifest SHA from the repository artifact;
- keeps the database URL and credentials out of arguments, output, errors, and
  evidence;
- never imports Shopify or gate-write paths;
- reports exact state, mutation plan, controls, XID evidence, and fingerprints;
  and
- cannot apply unless the immediately preceding production dry-run was exact,
  as enforced operationally by the authorization checkpoint and repeated
  internally by apply preflight.

### Implementation

Create `rebuild_phase4_historical_sales.py` with deterministic JSON output.
`--dry-run` opens a read-only connection. `--apply` passes the exact execution
SHA into audit evidence and calls only the controlled orchestrator. Reuse the
existing `RECONCILIATION_REVIEW_TOKEN` contract with a task-specific ephemeral
input; never print, persist, or place either value in process arguments.

No option may set a gate, update decisions, change mappings/exclusions, query
Shopify, generate a PO, relax controls, or bypass a mismatch.

### Verify

Run CLI unit tests and source-inspection tests for forbidden imports/call paths.

## Task 6 — Complete implementation validation and commit

After implementation is authorized:

1. run targeted pure, resolver, historical-sales, CLI, and PostgreSQL modules;
2. run Python compilation for changed Python files;
3. run `git diff --check`;
4. run the repository secret/generated-artifact scans;
5. run `./scripts/procurement-tests` and require every registered test to execute
   with zero failures, errors, skips, expected failures, or unexpected successes;
6. inspect the entire diff for a second resolver, gate write, Shopify path,
   policy change, or out-of-scope file; and
7. commit the complete implementation before any production access.

Stop and return the implementation SHA and evidence for independent adversarial
review. Production execution remains unauthorized unless the owner explicitly
grants it after review.

## Task 7 — Future production dry-run checkpoint (not authorized)

From a clean worktree at the exact independently reviewed implementation SHA:

1. capture fresh read-only gate/PO/decision/source/aggregate fingerprints;
2. verify the exact manifest SHA and run ID;
3. run the controlled production dry-run;
4. require `NEEDS_REBUILD`, 1,012 planned raw updates, and the complete approved
   post-rebuild simulation;
5. require null XID before/after and production DML = 0;
6. require stored `SALES_BACKFILL=FAIL`, POs/lines=0/0, and every unrelated
   gate/fingerprint unchanged; and
7. stop for the owner's explicit production-apply authorization.

Any changed value is a hard stop. Do not attempt repair or apply.

## Task 8 — Future production apply/readback checkpoint (not authorized)

Only after exact-SHA owner authorization:

1. repeat every preflight under lock inside the transaction;
2. run the controlled apply exactly once;
3. open a fresh read-only connection;
4. require exact 56,794/189/2,100/0 status counts, resolution methods,
   56,789-row aggregate, 79,916.0000 units, and $1,250,531.29 sales;
5. require exact NUTRL, Fiesta, High Noon, target-flow, and exclusion controls;
6. require source/decision/alias/exclusion/gate/PO protected invariants;
7. run the pure evaluator separately and require FAIL with only
   `MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED`;
8. do not call `_set_sales_gate` or otherwise update stored readiness state;
9. run a second dry-run and second apply invocation, both proving
   `CURRENT_REBUILD_STATE`, zero planned changes, and no further DML; and
10. stop for independent adversarial review of the production result.

If post-commit reconciliation differs, perform no corrective write. Preserve
evidence and request owner direction.

## Task 9 — Independent review and milestone documentation

An independent reviewer must attempt to disprove:

- resolver reuse and exact owner-decision application;
- source immutability and accounting;
- all raw status/method and MAP-target flows;
- exclusion exactness and LEAVE_UNRESOLVED semantics;
- aggregate reconstruction and idempotence;
- transaction rollback, concurrency, and protected fingerprints;
- evaluator separation and expected fail-closed outcome; and
- absence of Shopify, gate, Vendor Rules, forecasting/procurement, and PO paths.

Only after review acceptance may the single writer update
`docs/CODEX_HANDOFF.md` and `procurement/docs/PHASE_STATUS.md`. The checkpoint
must state approximately:

```text
HISTORICAL SALES RE-RESOLVED AND AGGREGATE REBUILT; 280 MATERIAL IDENTITIES
REMAIN UNRESOLVED — SALES_BACKFILL = FAIL; PHASE 4 BLOCKED PENDING OWNER DECISIONS
```

Do not mark Phase 4 complete. A later gate write, new decision artifact, or
canonical policy change remains a separate authorization.

## Definition of Done

Implementation/rebuild is complete only when all of the following are true:

- implementation is clean, committed, fully tested, and independently reviewed;
- production dry-run and apply were each separately authorized and exact;
- every approved post-rebuild and immutable-source control reconciles;
- protected gate and PO state is unchanged;
- the second identical invocation is a true no-op;
- the untouched pure evaluator returns the one expected blocker;
- independent production-result review is accepted; and
- milestone documents record Phase 4 as blocked, not complete.

## Exact next authorization boundary

> Authorize implementation and disposable-PostgreSQL validation of the committed
> design. Do not authorize production dry-run/apply, historical re-resolution,
> aggregate rebuild, readiness-gate evaluation/write, Shopify, Vendor Rules,
> forecasting/procurement, or PO work at that checkpoint.
