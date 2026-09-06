# Phase 4 Published-Production Corrective Reconciliation Design

**Date:** 2026-09-05
**Status:** Owner-approved design; implementation checkpoint
**Risk:** Level 4 — published-production historical identity/data correction
**Starting main:** `631bd95e2680b1fcdba80a39f52669d83c8e93ac`
**Starting tree:** `2f9ed7a8e74967da7d5c48161cd3af1b8e557727`
**Implementation branch:** `codex/phase4-published-production-reconciliation`

## 1. Correction and authorization boundary

The previously reported Phase 4 production closeout ran against Replit's
development database, `heliumdb`. The published deployment uses a separate
database, `neondb`, which remains at the independently verified pre-Phase-4
baseline. This is an environment-identification correction, not permission to
change any owner-approved identity outcome.

This implementation may add and test a one-time corrective executor. It may not
connect to or mutate `neondb`, create a Scheduled Deployment, open or merge a PR,
contact Shopify, create a PO, or begin Phase 6 or later business work. Production
execution remains a later, separately reviewed operational step.

## 2. Minimal architecture

Add only:

1. a minimal shell bootstrap for a temporary Replit Scheduled Deployment;
2. a narrow Python corrective reconciliation runner;
3. a focused deterministic test module and its test-count registration; and
4. truthful handoff and executive-status updates.

The runner orchestrates the existing original-manifest, terminal-disposition,
local re-resolution/finalization, readiness, and fingerprint services. It does
not reproduce their identity decisions or matching rules.

## 3. Verified-clone bootstrap

The packaged deployment source is not trusted as Git provenance. The shell
bootstrap requires `REPLIT_DEPLOYMENT=1` plus literal 40-character reviewed
commit and tree assertions. It creates a unique directory under `/tmp`, clones
only the public canonical GitHub repository, checks out the exact commit in
detached state, and proves the exact origin, HEAD, tree, and clean status.

Only after those checks pass may it execute the corrective runner from the fresh
clone. Clone, network, checkout, origin, HEAD, tree, or cleanliness failure exits
nonzero without invoking repository Python and therefore without a database
connection. The bootstrap has no packaged-source fallback and never places the
database URL or review tokens on a command line.

## 4. Pre-connection authorization and provenance

Before any database connection, the runner requires and verifies:

- `REPLIT_DEPLOYMENT=1`;
- presence of `DATABASE_URL` without exposing it;
- non-empty `RECONCILIATION_REVIEW_TOKEN` and
  `PHASE4_REVIEW_TOKEN_INPUT` without exposing either;
- the existing constant-time `require_review_authorization` comparison;
- existing runtime Git provenance derivation from a clean committed clone;
- exact reviewed commit, tree, canonical origin, and tracked runtime files; and
- both frozen manifest byte hashes.

The reviewed commit supplied by the bootstrap is only an expected-value
assertion. Observed terminal execution provenance continues to come exclusively
from `derive_runtime_execution_git_identity()`.

Any failure occurs before a connection capable of mutation and means zero DDL,
zero DML, zero rebuild, and zero readiness writes.

## 5. Production target guard

Every connection is made with the inherited `DATABASE_URL`; its value is never
logged. Its first SQL statement reads only connection identity. The runner
requires:

- `current_database() = 'neondb'`;
- PostgreSQL major version 16;
- `current_schema() = 'public'`; and
- no transaction ID assigned by the identity query.

The runner never accepts a CLI or environment override for the production
database name or schema. A target mismatch stops before DDL or DML.

## 6. Restart-safe state machine

The database is classified as one whole state. Only these states are accepted:

- **A — FROZEN_PRODUCTION_BASELINE:** migrations through 006, migration 007
  wholly absent, zero review decisions/exclusions/approved Phase 4 alias
  families, exact supplied source/aggregate/readiness/PO fingerprints and
  59,083 / 55,971 / 3,112 controls. The next action is the existing original
  manifest dry-run and persistence service.
- **B — ORIGINAL_MANIFEST_PERSISTED_PRE_007:** exact canonical 343-row original
  manifest state (55 MAP / 8 EXCLUDE / 280 LEAVE), eight exclusions, 17 safe
  alias families, and unchanged frozen protected fingerprints. The next action
  is migration 007 only.
- **C — POST_007_PRE_TERMINAL:** exact migration marker and schema
  postconditions, unchanged protected fingerprints, and the existing terminal
  dry-run classification `PRE_TERMINAL_EXACT` with no diagnostics and the exact
  43/280/8/39/198/1 mutation plan. The next action is existing terminal
  persistence.
- **D — CURRENT_TERMINAL_PRE_REBUILD:** exact current terminal state, 631 ledger
  rows, 43 historical-only variants, 2,092 variants, 56 safe alias families,
  198 structured exclusions, one authority registration, `PRE_REBUILD`, and
  unchanged frozen protected fingerprints. The required second identical
  terminal persistence must prove zero planned and committed mutations before
  the next action, canonical local re-resolution.
- **E — CURRENT_TERMINAL_POST_REBUILD:** exact final terminal, source,
  resolution, aggregate, exclusion-integrity, readiness, and PO controls. A
  repeated invocation performs read-only verification and no finalizer or gate
  rewrite.

A partial, mixed, drifted, or otherwise unknown state is `CONFLICT` and stops.
Each mutating stage is one transaction, so a crash can expose only the preceding
or succeeding permitted state.

## 7. Stage implementations

### Original manifest

Use `load_authorized_manifest`, `dry_run_manifest`, and
`persist_manifest_decisions` with `ManifestExecutionContext`. Require the State A
dry-run's exact plan, apply once transactionally, reopen for State B readback,
then require a second dry-run to report a true no-op. Do not add manifest SQL.

### Migration 007

Read and execute only
`procurement/db/007_phase4_terminal_disposition.sql`, followed by the standard
`meta` marker, in one transaction. Re-prove the expected columns, constraints,
triggers, functions, authority table, operational views, marker, and protected
fingerprints from a fresh read-only connection. Migration failure rolls back the
entire migration and marker.

### Terminal disposition

Use `load_terminal_artifact`, `dry_run_terminal_disposition`,
`persist_terminal_disposition`, `TerminalExecutionContext`, existing Git
provenance, existing advisory locking, and existing exact classification. Require
`PRE_TERMINAL_EXACT`, persist once, require fresh `CURRENT_TERMINAL_EXACT`, then
execute the mandatory identical persistence and require zero DML.

### Local rebuild and readiness

Only from exact State D, call
`rerun_sales_identity_resolution(conn, start_date=date(2024, 11, 28),
end_date=date(2026, 8, 10))`. This selects the established durable run and uses
only locally persisted facts. Its canonical finalizer derives exclusion
integrity, rebuilds `sales_daily`, evaluates sales readiness, and writes the gate
in one locked transaction. No Shopify client is imported or invoked by the
corrective path.

Fresh State E postflight recomputes every owner-supplied row, unit, sales,
resolution-method, exclusion-bucket, gate, and PO control. Frozen protected
fingerprints are required only through State D. State E records new
production-specific timestamp-bearing fingerprints instead of forcing equality
with development output.

## 8. Test-database safety

The new test module loads the existing test-database validation functions from
`procurement/tools/run_tests.py`. Fixture infrastructure reads only
`TEST_DATABASE_URL`, clears libpq `PG*` redirect variables, requires a loopback
PostgreSQL URL with exactly one `_test` database name and no query/fragment/
parameter tricks, connects to that exact URL, then verifies exact
`current_database()` identity and PostgreSQL major version 16 before fixture
schema DDL. Ordinary `DATABASE_URL` is never a fixture authority.

Disposable-PostgreSQL tests cover all safety failures, exact A→E transitions,
transaction rollback, zero-DML replay, restart behavior, unknown-state refusal,
canonical final controls, no Shopify/PO path, and secret-safe errors. Existing
Phase 4, Phase 5, startup, and full deterministic suites remain required.

## 9. Review and deployment boundary

After implementation and disposable validation, commit and push the branch but
do not open a PR. The next action is independent ChatGPT implementation review.
Only a later owner/reviewer-approved exact commit/tree may be placed in the
temporary Scheduled Deployment command. Production execution evidence and final
documentation closeout occur only after that separate review and authorization
boundary.
