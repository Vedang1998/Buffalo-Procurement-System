# Phase 4 Terminal-Disposition Implementation Plan

**Status:** Planning complete; implementation not started  
**Approved design commit:** `14ccb6845993c378e18f2697490ba7ee931f7f77`  
**Approved design:** `docs/superpowers/specs/2026-08-25-phase4-terminal-disposition-implementation-design.md`  
**Branch:** `codex/phase4-terminal-disposition-implementation`  
**Starting main:** `701548dfacbc35d505f1d726146c268d6e42260d`  
**Terminal artifact SHA-256:** `fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff`  
**Original manifest SHA-256:** `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`

## Authorization and stop conditions

Codex is the sole writer. Runtime/schema implementation, local tests, and
disposable-loopback PostgreSQL validation are authorized.

The following remain unauthorized throughout this plan:

- any production connection, including production dry-run;
- production DML, restoration, or decision persistence;
- production or local-to-production historical-sales rebuild/re-resolution;
- production readiness evaluation or gate write;
- Shopify access/write;
- Vendor Rules;
- forecasting, recommendations, procurement, or PO actions; and
- opening a PR.

Stop before altering the approved architecture if implementation reveals a
material contradiction between the frozen design and actual repository/schema
behavior. Ordinary implementation details within the approved architecture do
not require a design change.

## Planned file scope

### Add

- `procurement/db/007_phase4_terminal_disposition.sql`
  - historical-only variant scope and restoration provenance;
  - terminal ledger and exclusion provenance fields/constraints;
  - historical-only operational guards and safe operational views.
- `procurement/src/procurement_os/historical_sales_terminal.py`
  - artifact parser, frozen controls, canonical MAP serialization, execution
    identity, preflight/state classification, transaction, fingerprints, and
    readback.
- `procurement/tools/persist_phase4_terminal_disposition.py`
  - narrow terminal persistence CLI; no rebuild/readiness/Shopify logic.
- `procurement/tests/fixtures/phase4_map_target_flow_v1.json`
  - exact 14,522-byte canonical MAP target-flow preimage, ending in one LF.
- `procurement/tests/test_phase4_terminal_disposition.py`
  - pure artifact, serialization, execution-identity, and state-model tests.
- `procurement/tests/test_phase4_terminal_disposition_postgres.py`
  - migration, safety, persistence, rollback, concurrency, resolver, exclusion
    integrity, and projected-rebuild tests on disposable PostgreSQL.

### Modify

- `procurement/db/schema_postgres.sql`
  - make the base `variants` definition compatible with the final constrained
    identity model for fresh databases while leaving migration 007 authoritative
    for upgrades.
- `procurement/tools/apply_schema.py`
  - register migration 007 last.
- `procurement/tools/run_tests.py`
  - register new test modules and fail-closed minimums.
- `procurement/src/procurement_os/sales.py`
  - reason-aware exact exclusions and historical-only identity evidence while
    preserving the single resolver and existing resolution precedence.
- `procurement/src/procurement_os/historical_sales.py`
  - authoritative database-derived exclusion integrity and fail-closed
    readiness integration.
- focused existing tests that enumerate migrations, schema contracts, resolver
  behavior, or readiness function signatures.

Do not modify the frozen manifests, `rules.toml`, `docs/CODEX_HANDOFF.md`, or
`procurement/docs/PHASE_STATUS.md` for this implementation-only checkpoint.

## Task 1 — Freeze the artifact and MAP byte contract

### Tests first

Create `test_phase4_terminal_disposition.py` with failing tests for:

1. exact terminal artifact bytes and SHA;
2. exact header and 280 unique canonical source keys;
3. exact 43 RESTORE / 47 MAP / 190 EXCLUDE supplement controls;
4. exact 43 RESTORE / 102 MAP / 198 EXCLUDE combined controls;
5. exact raw-row, unit, and sales totals by disposition;
6. exact original 280-LEAVE membership and primary prior provenance;
7. all 19 continuity predecessor/successor pairs;
8. target equality for every RESTORE;
9. zero Fiesta mappings, High Noon 3/3 exclusions, Popov 2/2 exclusions;
10. terminal EXCLUDE has no target and exact reason;
11. exact original-eight membership and original row/SHA provenance;
12. reordered facts, target groups, and source keys yield identical MAP bytes;
13. exact complete canonical bytes equal the committed golden fixture;
14. the golden fixture is exactly 14,522 bytes, UTF-8, no BOM/CR, and one final
    LF;
15. exact digest
    `5d6832cea3df7a4f45d31d7e7a8100409ddc078936095fe3c775ce862a1f64a6`;
16. target, source membership/count, raw-row, unit, or sales drift changes bytes
    and digest; and
17. extra decimal precision is rejected rather than rounded.

### Implementation

In `historical_sales_terminal.py`, add immutable dataclasses for terminal rows,
flow rows, controls, preflight, classifications, and reports. Reuse
`HistoricalIdentityIndex.source_key` for source-key validation.

Parse both frozen repository manifests. Do not copy business decisions into
Python constants beyond invariant control values. The original manifest
supplies the existing 55 MAP and exact eight exclusions; the terminal artifact
supplies the 280 supplement.

Implement MAP canonicalization exactly:

1. combine the 55 original MAP rows and 47 terminal MAP rows;
2. group by target after sorting unique exact source keys lexicographically;
3. serialize only the source-key cardinality plus raw/financial flows;
4. format unit strings to four decimals and sales strings to two;
5. sort targets lexicographically in Python;
6. use compact sorted-key JSON with `ensure_ascii=True`;
7. encode UTF-8 and append exactly one LF; and
8. compare canonical bytes to the golden fixture before hashing.

The local manifests already independently reproduce 102 keys, 96 targets,
14,522 bytes, and the required digest without a database connection.

### Verify

Run the new pure module alone. Confirm altered copies fail on both bytes and
SHA, not only on aggregate counts.

## Task 2 — Add migration 007 and historical-only invariants

### Tests first

Add disposable-PostgreSQL tests that apply all migrations and prove:

- migration 007 succeeds from the exact migration-006 schema;
- migration 007 is transactionally safe and idempotent;
- a normal/current variant requires a real nonblank Product ID;
- a historical-only variant must be inactive, `RETIRED_CONFIRMED`, and have
  `product_id IS NULL`;
- every invalid combination is rejected;
- historical-only restoration provenance is complete and SHA-shaped;
- non-historical rows cannot carry restoration provenance;
- immutable historical sales/audit rows may reference historical-only variants;
- active supplier offers, any current/future price path, active manual
  overrides/policies, forecasts, recommendations, and PO lines reject a
  historical-only variant;
- inactive archival supplier evidence and inventory snapshots remain
  reference-capable where they are not operational eligibility;
- operational price/inventory views omit historical-only rows; and
- switching an existing current variant to historical-only fails when
  prohibited operational dependents exist.

### Migration implementation

Add `variants.identity_scope TEXT NOT NULL DEFAULT 'CURRENT'` with an exact
allowlist. Drop only the old `product_id NOT NULL` property and replace it with
named constraints encoding the bidirectional approved invariants.

Add explicit restoration provenance columns sufficient to prove:

- terminal manifest SHA/row;
- evidence version;
- owner authorization;
- authority Git SHA; and
- execution Git SHA.

Extend `historical_sales_review_decisions` with:

- `decision_schema_version` (legacy default plus terminal V1);
- `reason_code`;
- `primary_manifest_sha256`;
- `primary_manifest_row_number`;
- `evidence_version`;
- `owner_authorization`;
- `authority_git_sha`; and
- `execution_git_sha`.

Replace the anonymous legacy action/target checks with named compatible checks
that allow RESTORE while preserving all legacy rows. Terminal-V1 rows require
complete provenance. RESTORE requires target = nonzero source Variant ID;
terminal EXCLUDE requires no target and an allowlisted reason.

Extend `historical_sales_exclusions` with structured `reason_code` and an FK to
the effective decision. The migration must permit the exact pre-terminal legacy
eight to exist until the authorized transaction normalizes them; application
readback, not a permissive status boolean, enforces complete terminal state.

Add database trigger functions that reject historical-only operational use.
Keep immutable sales/audit FKs valid. Update current/future operational views to
join an eligible non-historical variant explicitly.

Update the base schema for fresh installations and register migration 007 in
`apply_schema.py` and every exact migration tuple in tests.

### Verify

Run migration/schema tests and inspect constraint/trigger definitions from
`pg_constraint`, `pg_trigger`, and `pg_views` in disposable PostgreSQL.

## Task 3 — Derive the actual execution Git identity

### Tests first

Add pure/temp-repository tests proving:

- clean committed repository returns exact `HEAD` SHA;
- an expected authorized SHA is an assertion only;
- expected/observed mismatch fails;
- tracked, staged, or untracked worktree changes fail;
- missing `.git`, detached/unresolvable identity, or untracked implementation
  file fails;
- caller context has no field that can supply the persisted execution SHA; and
- the CLI cannot bypass derivation.

### Implementation

Implement `derive_execution_git_identity(repo_root, expected_sha=None)` using
non-shell Git subprocess arguments. Resolve the worktree, `HEAD`, tracked-file
membership, and full porcelain status. Require a 40-character lowercase commit
SHA and a clean tree. The derived value is the only value written to restoration
or decision provenance.

The CLI may accept `--expected-execution-git-sha` solely to compare against the
derived identity. It must never pass that argument through as observed
provenance.

Integration tests may mock the derivation boundary or use a temporary Git
repository; production code itself exposes no caller-supplied observed SHA.

### Verify

Run the pure execution-identity tests and source-inspect the CLI/service for a
caller-controlled execution-SHA path.

## Task 4 — Implement exact preflight and whole-state classification

### Tests first

Build a production-shaped disposable fixture that represents the exact
post-343-persistence/pre-terminal state. Reuse the existing original manifest
persistence behavior to create its 343 ledger rows, then migrate/seed the exact
17 safe alias families and eight active legacy exclusions.

Test:

- exact state classifies `PRE_TERMINAL_EXACT`;
- complete terminal state classifies `CURRENT_TERMINAL_EXACT`;
- missing, additional, partial, mixed, unknown-schema, or unknown-provenance
  state classifies `CONFLICT`;
- a single terminal decision/restoration/alias/exclusion fragment is conflict;
- compatible-looking legacy rows outside the exact known cases are conflict;
- incompatible latest action, target, reason, run, source, or provenance is
  conflict;
- an invalid latest row wins over an older valid row;
- an exact latest terminal row may supersede invalid older history;
- source-key/raw membership, canonical targets, materiality, dates, rows,
  units, and sales must match;
- original eight retain original manifest row/SHA;
- terminal 280 use terminal manifest row/SHA;
- no missing/extra source key, restore ID, alias, or active exclusion passes;
- protected sales, aggregate, gate, and PO fingerprints are deterministic.

### Implementation

Add read-only helpers to load:

- the approved review run and raw source groups;
- deterministic latest ledger rows ordered by `decided_at DESC`, then UUID;
- variant/restoration state;
- approved old-ID aliases and full source-ID families;
- active exclusions and effective decision links; and
- protected fingerprints.

Implement whole-database classification with only three outcomes:
`PRE_TERMINAL_EXACT`, `CURRENT_TERMINAL_EXACT`, or `CONFLICT`. Do not reuse the
original manifest's broad per-row `LEGACY_COMPATIBLE` behavior for terminal
state. Only the exact known 280 LEAVE supersessions and eight original exclusion
normalizations are allowed.

Preflight must also validate exact target existence after accounting for the 43
same-transaction restorations, uniform nonzero old-ID families, active mapping
rejections, all continuity controls, and every frozen artifact total.

### Verify

Run pure and PostgreSQL classification tests. Assert conflict detection occurs
before the first mutating SQL statement.

## Task 5 — Implement the serializable terminal transaction

### Tests first

Add integration tests for:

- exact planned mutation counts from `PRE_TERMINAL_EXACT`;
- 43 restored variants with exact IDs and complete provenance;
- append-only 43 RESTORE / 47 MAP / 190 EXCLUDE rows superseding the 280 LEAVEs;
- append-only structured normalization of the exact original eight;
- no rewrite of any original ledger row;
- final latest 43 RESTORE / 102 MAP / 198 EXCLUDE / 0 LEAVE state;
- exactly 198 active exclusions with correct effective decision FK/reason;
- safe uniform old-ID aliases only, no zero/null alias, no restored self-alias;
- all protected fingerprints unchanged;
- injected failure after each restoration, decision, alias, exclusion, and
  readback stage rolls back all DML;
- nonblocking advisory-lock acquisition fails closed under concurrency; and
- second execution reports `CURRENT_TERMINAL_EXACT` with zero planned and
  committed DML.

### Implementation

Implement a persistence-only service using one `SERIALIZABLE` transaction and
the existing Phase 4 advisory lock. Before mutation, repeat artifact validation,
execution-identity verification, database preflight, whole-state
classification, and protected fingerprints.

Mutation order is:

1. insert 43 exact historical-only variants;
2. append 280 terminal decisions in deterministic source-key order;
3. append eight original-exclusion normalization decisions using original
   primary provenance and separate supplementary terminal provenance;
4. create only proven safe aliases in deterministic old-ID order;
5. reconcile exactly 198 active exclusion rows to structured reasons and their
   latest effective decisions; and
6. perform fresh readback and fingerprint checks.

Any mismatch raises and rolls back. The service must not import/call finalizer,
gate setter, Shopify, forecasting, procurement, recommendation, or PO code.

### Verify

Run the entire terminal PostgreSQL module twice: exact apply/readback and true
no-op. Inspect transaction XID and table counts in the disposable database.

## Task 6 — Extend the single resolver

### Tests first

Add focused resolver and database-loader tests proving:

- every restored exact source ID resolves to itself via
  `EXACT_PRESERVED_HISTORICAL_VARIANT_ID`;
- current active exact-ID behavior is unchanged;
- recreated identities still require approved continuity aliases;
- source-key MAP behavior remains exact;
- original exclusions return `EXPLICIT_EXCLUSION`;
- terminal unattributable exclusions return
  `EXPLICIT_UNATTRIBUTABLE_EXCLUSION`;
- unknown/missing reason fails closed rather than silently excluding;
- historical-only status creates no SKU-only/fuzzy mapping;
- Fiesta remains zero mappings;
- High Noon and Popov are excluded exactly; and
- existing resolver regression tests remain unchanged.

### Implementation

Retain `HistoricalIdentityIndex` and its canonical `source_key`. Change active
exclusions from a bare key set to exact key/reason-method data while preserving
backward-compatible construction for existing tests where appropriate.

`load_identity_index` reads the structured active exclusion state and
deterministic latest MAP decisions. Restored identities enter through the
existing `variants` query and exact preserved-inactive behavior. RESTORE does
not create a second source-key resolver path.

### Verify

Run `test_sales.py`, historical-sales resolver tests, and both terminal modules.
Inspect for any fuzzy, closest-title, SKU-only, or guessed-successor behavior.

## Task 7 — Derive exclusion integrity from authoritative database state

### Tests first

Add deterministic/adversarial tests for:

- exact 198-key terminal state passes with the two exact reason buckets;
- unprovenanced, unknown-reason, target-bearing, source-mismatched, inactive,
  extra, or missing exclusion fails;
- effective-decision FK mismatch fails;
- invalid latest ledger row fails despite older valid history;
- valid latest row supersedes older invalid history without false conflict;
- partial fact exclusion and any canonical target on raw facts fails;
- rows, net/absolute units, or net/absolute sales drift fails;
- original-eight membership or primary provenance mutation fails;
- terminal 190 membership/provenance mutation fails;
- caller-supplied `exclusion_integrity_reconciled=True` cannot make readiness
  pass; and
- failure emits `EXCLUSION_INTEGRITY_NOT_PROVEN` plus stable diagnostics.

### Implementation

In `historical_sales.py`, add a database-derived exclusion-integrity report for
an exact run. It queries latest decisions, active exclusions, and run facts,
recomputes exact key and reason buckets, and returns structured diagnostics.

Refactor readiness so its public database-aware path obtains this report itself.
A private pure helper may consume the already-derived report for unit testing,
but no public evaluator/finalizer accepts a trusted caller boolean. The existing
finalizer invokes integrity derivation only after resolver updates and before
gate evaluation.

### Verify

Run readiness and finalizer tests. Source-inspect for any path that trusts
`exclusion_integrity_reconciled` from arbitrary evidence.

## Task 8 — Prove controlled rebuild behavior on disposable PostgreSQL

### Tests first and implementation reuse

Use the existing resolver/finalizer architecture in the disposable terminal
fixture. Extract a gate-free core only if tests cannot safely exercise the
existing structure without duplication; do not build a second resolver.

Prove the projected terminal outcome:

- 59,083 facts;
- 57,429 RESOLVED / 1,654 EXCLUDED / 0 UNRESOLVED / 0 AMBIGUOUS;
- exact resolved/excluded net and absolute unit/sales controls;
- 57,424 `sales_daily` rows / 80,659.0000 units / $1,263,133.84 sales;
- frozen resolution-method counts;
- 43 RESTORE / 102 MAP / 198 EXCLUDE effective decisions;
- both exact reason buckets; and
- exclusion integrity derived as PASS.

The fixture rebuild and any gate row it exercises exist only inside a unique
disposable schema/database and are removed by teardown. No production command
or connection is used.

### Verify

Run the terminal PostgreSQL integration module and inspect the disposable
database identity/loopback protections before accepting results.

## Task 9 — Add the narrow CLI and forbidden-path controls

### Tests first

Test:

- CLI requires exact manifest paths/SHA and explicit apply acknowledgement;
- CLI derives execution identity and treats expected SHA only as an assertion;
- dry-run/apply reporting is deterministic;
- production execution is not invoked by tests;
- no CLI/service import or call references Shopify, rebuild/finalizer, gate
  mutation, forecasts, recommendations, procurement, or POs; and
- sanitized errors never expose database URLs or secrets.

### Implementation

Add a thin CLI that opens the provided database connection only after local
artifact and Git checks pass, delegates to the terminal persistence service,
and emits structured JSON. It contains no business SQL and no rebuild option.

Although the CLI is implemented for later reviewed use, this task does not run
it against production or any non-loopback database.

### Verify

Run CLI unit tests and static forbidden-name/import checks.

## Task 10 — Full validation, commit, and push

Run, in order:

1. pure terminal tests;
2. migration/constraint tests;
3. resolver/readiness focused tests;
4. disposable terminal PostgreSQL tests;
5. Python compile checks;
6. `./scripts/procurement-tests`;
7. `git diff --check`;
8. normal repository secret/generated-artifact scan; and
9. manual changed-file review for migration safety and forbidden execution
   paths.

Require zero failures, errors, skips, expected failures, or unexpected
successes. Record exact discovered/executed/pass counts and disposable
PostgreSQL version/database identity. Do not intentionally start GitHub Actions.

Commit the complete implementation and tests on the existing branch, push it,
and verify the remote branch points at the exact implementation SHA. Do not open
a PR.

Return the full checkpoint required by the owner, including branch/base/SHA,
changed files, schema and safety behavior, provenance behavior, exact MAP bytes
and digest, targeted/disposable/full test evidence, frozen controls, deviations,
production DML = 0, all prohibited actions = 0, clean Git status, and this exact
next action:

> Independent adversarial review of the committed implementation and
> disposable-PostgreSQL evidence. Production execution remains unauthorized.
