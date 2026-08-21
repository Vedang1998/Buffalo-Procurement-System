# Phase 4 Controlled Identity-Decision Persistence Implementation Plan

**Status:** Planning complete; implementation not started
**Approved design:** `docs/superpowers/specs/2026-08-20-phase4-identity-decision-persistence-design.md`
**Branch:** `phase4/identity-decision-persistence`
**Starting main:** `60db3e5a893856df5b95a05f0ec75b3ec7e84f22`
**Manifest SHA-256:** `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`
**Production run:** `d389079c-eabf-49b5-a245-40a207025fd7`

## Authorization and stop conditions

The owner has approved implementation planning only at this checkpoint. No
runtime code, tests, database state, configuration, production state, gates,
Shopify state, or PO state may be changed until implementation is separately
started.

When implementation is authorized, the one-time owner exception permits the
production persistence before independent implementation review, but only after
all tests, dry-run controls, transaction safeguards, manifest-hash checks,
database preflight checks, and rollback tests pass.

Stop immediately before production DML if any required check differs from the
approved controls. Historical-sales rebuild/re-resolution, `SALES_BACKFILL`
evaluation, readiness-gate changes, Shopify access/writes, Vendor Rules,
forecasting/procurement work, and PO actions remain unauthorized throughout.

## Planned file scope

### Add

- `procurement/review/phase4_identity_manifest_corrected.csv`
  - byte-identical approved manifest;
  - exact required SHA checked before staging and again at runtime.
- `procurement/src/procurement_os/historical_sales_manifest.py`
  - manifest parser, pure controls, authorization helper, DB preflight,
    persistence-only transaction, fingerprints, and readback.
- `procurement/tools/persist_phase4_identity_manifest.py`
  - narrow dry-run/apply CLI; no business SQL.
- `procurement/tests/test_phase4_identity_manifest.py`
  - pure manifest/auth/control tests using the approved artifact.
- `procurement/tests/test_phase4_identity_manifest_postgres.py`
  - disposable PostgreSQL persistence and rollback tests.

### Modify

- `procurement/src/procurement_os/sales.py`
  - add exact source-key approved mapping support to
    `HistoricalIdentityIndex` and `load_identity_index`.
- `procurement/src/procurement_os/historical_sales.py`
  - expose only the minimum reusable review-run/source snapshot helpers needed
    by the manifest service; do not change finalization behavior.
- `procurement/tools/run_tests.py`
  - register both new test modules with exact final minimums.
- `docs/CODEX_HANDOFF.md`
  - update only after successful production persistence/readback.
- `procurement/docs/PHASE_STATUS.md`
  - change Phase 4 wording to decisions persisted/rebuild pending only after
    successful production persistence.
- `docs/superpowers/specs/2026-08-20-phase4-identity-decision-persistence-design.md`
  - already clarified for legacy-compatible decision normalization.

No migration, API endpoint, Shopify module, readiness module, forecasting file,
procurement logic, or PO code is planned.

## Decision-state model

For each exact manifest source key, load one deterministic latest ledger row with
`ORDER BY decided_at DESC, historical_sales_review_decision_id DESC`.

Classify it as:

1. `MISSING`
   - no ledger row exists;
   - insert the approved current-manifest row.
2. `CURRENT_PROVENANCE`
   - run, exact key, stored action, and target match;
   - evidence contains the current manifest SHA and complete required provenance;
   - true no-op.
3. `LEGACY_COMPATIBLE`
   - run, exact key, stored action, and target match;
   - current manifest provenance is absent or incomplete;
   - append one current-manifest row with
     `supersedes_decision_id=<legacy decision id>`;
   - preserve the legacy row unchanged;
   - record `normalized_legacy_decision_id` in evidence.
4. `CONFLICT`
   - different run, action, or canonical target;
   - hard stop before any DML.

Actor or reason differences do not turn a semantically identical legacy row into
a conflict. The new superseding row uses the current execution actor and exact
manifest review note while preserving the prior actor/reason in the old row.

After normalization, the superseding row is the sole latest effective decision.
A second run classifies it as `CURRENT_PROVENANCE` and inserts nothing.

Complete provenance means evidence contains at least:

- `manifest_sha256`;
- one-based `manifest_row_number`;
- manifest disposition and stored action;
- evidence basis and review note;
- production run ID;
- executing implementation Git SHA;
- exact source fields and canonical target;
- owner-exception authorization identifier/version.

## Task 1 — Freeze the approved artifact and pure contract

### Tests first

Add pure tests that fail until the parser exists:

1. approved file hash and all 343-row controls match exactly;
2. altered bytes fail the SHA check;
3. duplicate source key fails;
4. missing/extra/unknown disposition fails;
5. MAP without a target fails;
6. non-MAP with a target fails;
7. exclusion-set drift fails;
8. any Fiesta target fails;
9. NUTRL exception is exactly 3/3 to `41716813627467`;
10. High Noon Tequila is exactly 3/3 LEAVE_UNRESOLVED;
11. output/control ordering is deterministic;
12. numeric controls use exact `Decimal` values.

### Implementation

In `historical_sales_manifest.py`, add immutable row/control dataclasses and:

- `read_manifest_bytes(path)`;
- `parse_manifest(bytes)` with exact header validation;
- `validate_static_manifest(bytes, rows)`;
- `manifest_controls(rows)`;
- canonical action conversion helpers.

Constants hold the authorized SHA, run ID, counts, target IDs, exclusions, NUTRL
keys, Fiesta ID, and High Noon Tequila keys. Error messages contain controls and
keys but never credentials or database URLs.

### Verify

Run only the new pure test module. Confirm the repository copy hashes exactly to
the approved value before continuing.

## Task 2 — Add exact source-key decisions to the resolver

### Tests first

Add focused resolver tests proving:

- a title-only zero/null-ID source resolves only after an exact source-key MAP;
- another normalized-similar source does not inherit that decision;
- source-key EXCLUDE remains exact;
- LEAVE_UNRESOLVED does not resolve;
- an approved preserved inactive target remains valid;
- existing current-ID, old-ID alias, SKU-conflict, size-conflict, and exclusion
  tests remain unchanged.

### Implementation

Add an immutable `HistoricalSourceDecision` representation and an optional
source-key decision collection to `HistoricalIdentityIndex`.

Resolution order remains fail-closed:

1. exact active exclusion;
2. exact approved source-key MAP;
3. existing current-ID/preserved-historical-ID behavior;
4. approved old-ID/full historical aliases;
5. conservative current SKU/title evidence;
6. unresolved/ambiguous.

`load_identity_index` selects the latest effective MAP decisions and loads them
by exact source key. It does not load LEAVE_UNRESOLVED as a mapping. Persistence
syncs EXCLUDE rows into the existing exclusions table, so the existing exact
exclusion path remains authoritative.

This code changes future resolver capability but does not invoke a resolver or
alter any persisted sales row during this task.

### Verify

Run `test_sales.py` and the new pure module. Inspect the resolver diff for any
new fuzzy, SKU-only, size, pack, or active-only behavior; none is allowed.

## Task 3 — Implement read-only database preflight and dry-run

### Tests first

Build disposable PostgreSQL fixtures with the real schema/migrations and test:

- exact reviewable run and exact source-key set pass;
- unknown manifest key fails;
- missing manifest key fails;
- source materiality/affected-row/magnitude drift fails;
- unknown MAP target fails;
- nonuniform old-ID family fails;
- conflicting alias or active mapping rejection fails;
- dry-run assigns no transaction ID and changes no table/hash;
- current decision classifications and expected insert/normalize/no-op counts
  are deterministic.

### Implementation

Add:

- `load_review_source_snapshot(conn, run_id)`;
- `load_latest_effective_decisions(conn, source_keys)`;
- `classify_existing_decisions(...)`;
- `validate_database_preflight(conn, manifest)`;
- `dry_run_manifest(conn, manifest, context)`.

Dry-run uses `BEGIN TRANSACTION READ ONLY`, verifies
`current_setting('transaction_read_only')='on'`, and reports
`txid_current_if_assigned()` before and after. It validates the exact run, queue,
targets, decision state, alias state, rejection state, and exclusion state.

Canonical display-title drift is diagnostic only; canonical Variant ID membership
controls target eligibility. Exact source titles/variants and manifest source
controls must still match the live review snapshot.

### Verify

Run the new PostgreSQL module against disposable loopback PostgreSQL only.

## Task 4 — Implement persistence-only transaction

### Tests first

Add integration tests for:

- 343 effective rows produce 55/8/280 readback controls;
- title-only MAP is durable and source-key resolvable;
- repeated old IDs produce exact per-key decisions and one safe target family;
- NUTRL's three keys remain an enumerated exception;
- all 280 LEAVE_UNRESOLVED rows are durable;
- exactly eight exclusions are active;
- identical current-provenance rows are no-ops;
- legacy identical row without manifest SHA is superseded once with full
  provenance, original history remains, and the second execution is a no-op;
- different action or target is a hard conflict;
- injected failure after a deterministic row number rolls back decisions,
  exclusions, aliases, and change log completely;
- no finalization, re-resolution, readiness evaluator, or gate setter is called;
- protected fingerprints remain exact before/after.

### Implementation

Add `persist_manifest_decisions(conn, manifest, context)` using one transaction:

1. acquire `acquire_backfill_transaction_lock`;
2. set/verify the intended transaction isolation;
3. repeat the full static and database preflight;
4. capture protected fingerprints;
5. iterate rows in exact source-key order;
6. insert MISSING rows or superseding rows for LEGACY_COMPATIBLE state;
7. skip CURRENT_PROVENANCE rows;
8. upsert exactly eight exclusions;
9. create at most one manifest alias per uniform nonzero old-ID/target family;
10. add one `change_log` row for each new/superseding decision;
11. query effective decision/readback controls;
12. compare protected fingerprints;
13. commit only after all assertions pass.

The persistence module must not import or reference rebuild/readiness entry points.
A source-inspection test rejects forbidden call names in the new module/tool.

### Uniform old-ID aliases

For each nonzero old ID represented by a MAP:

- enumerate every live review key with that old ID;
- require exact equality with the manifest family;
- require every family member to MAP to one target;
- reject an approved alias to another target;
- reuse a compatible existing alias;
- otherwise insert one `SALES_BACKFILL_REVIEW` alias whose evidence enumerates
  every covered source key and the manifest SHA.

Zero/null IDs and title-only rows never create broad aliases.

## Task 5 — Authorization and command-line interface

### Tests first

Add tests proving:

- missing configured review token fails before connection creation;
- missing supplied token fails before connection creation;
- invalid token fails in constant-time validation before connection creation;
- neither token value appears in returned JSON, logs, exceptions, or subprocess
  arguments;
- `--dry-run` never calls persistence;
- `--apply` requires a clean committed Git worktree and records exact HEAD;
- output JSON is stable and contains no database URL or credential.

### Implementation

The CLI accepts:

- `--manifest <path>`;
- exactly one of `--dry-run` or `--apply`;
- `--actor <nonblank owner/reviewer>`.

The expected token remains `RECONCILIATION_REVIEW_TOKEN`. The supplied token is
read only from the task-specific ephemeral environment variable
`PHASE4_REVIEW_TOKEN_INPUT`. Constant-time comparison occurs before opening the
database connection. Neither value is printed or persisted.

For apply, the tool discovers `git rev-parse HEAD`, verifies a clean worktree,
and passes the full SHA into the application service as audit evidence. The tool
uses `DATABASE_URL` only to create the connection and never emits it.

## Task 6 — Machine validation and pre-write implementation commit

### Targeted validation

Run the registered targeted modules through the repository's pinned environment,
then run:

- Python compilation for changed Python paths;
- `git diff --check`;
- TOML/lock validation already required by the repository runner;
- changed-file secret scan;
- tracked generated/auth artifact scan;
- source inspection proving no forbidden rebuild/gate/Shopify/PO call path.

### Full validation

Run exactly:

```text
./scripts/procurement-tests
```

The runner must discover and execute every registered test with zero failures,
errors, skips, expected failures, or unexpected successes.

### Commit gate

Review the complete diff, stage only approved implementation/artifact/test files,
and commit. Production dry-run/apply must execute from that clean exact commit.
Do not write production data if any validation is incomplete or failing.

## Task 7 — Production baseline and dry-run

Using the committed implementation and configured authorization secret:

1. capture read-only baseline fingerprints, gate rows, review-decision counts,
   active exclusions, relevant aliases, and PO counts;
2. verify `SALES_BACKFILL=FAIL`;
3. run the CLI dry-run against the exact repository manifest;
4. require the complete approved controls and exact run ID;
5. require decision-state classification totals to reconcile to 343;
6. require no database transaction ID/data mutation from dry-run;
7. record deterministic JSON output without secrets.

Any mismatch is a STOP. Do not run apply.

## Task 8 — One-transaction production persistence

Only after Task 7 passes, run `--apply` once with the exact manifest, executing
commit, actor, and ephemeral review-token input.

Expected from the currently verified empty decision state:

- 343 new current-manifest decisions;
- 55 MAP, 8 EXCLUDE, 280 LEAVE_UNRESOLVED;
- 8 active exclusions;
- 16 uniform nonzero old-ID alias families, subject to repeat preflight;
- 343 decision audit/change-log events;
- zero protected-state fingerprint changes.

If preflight discovers compatible legacy rows instead, the expected new-row count
becomes `MISSING + LEGACY_COMPATIBLE`; each legacy row is superseded once and the
effective total remains 343.

Do not retry blindly after an uncertain connection/result. First run read-only
reconciliation; a fully provenanced complete state is a safe no-op rerun, while
any partial/conflicting state is a hard stop.

## Task 9 — Fresh post-write reconciliation

Open a fresh database connection in a read-only transaction and verify:

- effective manifest source keys 343/343;
- MAP_TO_CANONICAL 55;
- EXCLUDE_HISTORICAL_ITEM 8;
- LEAVE_UNRESOLVED 280;
- MAP targets 55/55 populated, 51 distinct, 0 missing from variants;
- exclusions exactly 8/8;
- NUTRL 3/3 to `41716813627467`;
- Fiesta mappings 0;
- High Noon Tequila 3/3 LEAVE_UNRESOLVED;
- effective-decision conflicts 0;
- all 343 latest effective rows carry the required manifest SHA and full
  provenance;
- protected fingerprints equal the pre-write baseline;
- readiness gates byte-for-byte unchanged and `SALES_BACKFILL=FAIL`;
- purchase-order and line counts/content unchanged;
- no historical source resolution or canonical aggregate changed.

Run a second dry-run after readback. It must classify all 343 decisions as
CURRENT_PROVENANCE and report zero inserts, zero normalizations, and zero other
mutations.

## Task 10 — Documentation, final commit, push, and draft PR

Only after successful post-write reconciliation:

- update `docs/CODEX_HANDOFF.md` with executing commit SHA, final branch SHA,
  manifest SHA, reviewer APPROVE, exact tests, dry-run controls, persistence
  controls, fingerprints, gate/PO before-after state, and zero rebuild/Shopify/PO
  actions;
- update Phase 4 status to owner decisions persisted/rebuild pending without
  declaring completion;
- run documentation diff/secret/whitespace checks;
- commit the documentation checkpoint;
- push the existing branch once;
- open one draft PR against canonical `main` and allow one necessary PR CI run;
- do not merge.

Stop with the exact next action:

> Independent review of persistence implementation/results, then separately
> authorize Phase 4 historical-sales re-resolution/rebuild and gate reevaluation.

## Required final handoff fields

Return exactly the fifteen requested fields from the owner task plus:

```text
HISTORICAL SALES REBUILD = NOT RUN
SALES_BACKFILL REEVALUATION = NOT RUN
SHOPIFY WRITES = 0
PO ACTIONS = 0
```

At the end of implementation planning, before any implementation begins, obtain
explicit authorization to execute this plan.
