# Buffalo Procurement OS — Codex Handoff

**Updated:** 2026-08-15T01:44:38Z (UTC)

**Phase numbering:** This handoff follows `procurement/docs/authority/03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md`: Phase 3 is catalog reconciliation and Phase 4 is historical ShopifyQL sales backfill/reconciliation.

This is an operational checkpoint, not a replacement for the canonical specification or `procurement/config/rules.toml`. Verify repository and database state again before acting.

**Operating process:** every coding/review/release session must follow `docs/PROJECT_GOVERNANCE.md`. At each meaningful milestone, this handoff must be refreshed with verified state, tests, readiness gates, material counts/control totals, open risks/decisions, Git reference, and exact next authorization boundary.

## Verified current state

### Repository and tests

- Verified `main` baseline:
  `8d8a07a082a575ef35c6b37ecb6dedc7f47cbbaf`. Documentation-only G12
  closeout branch: `docs/pr4a-post-merge-closeout`, created from that exact
  `origin/main` commit.
- Phase 4 starting HEAD: `90a6b9ec2469d541ff11cb3716807754fd4edb05` (`Add durable Codex and Claude project handoff`). Verified Phase 4 implementation checkpoint: `a78b5808551f3bae584367a631cf25776d3ff038` (`Phase 4 historical sales backfill and reconciliation workflow`).
- Authoritative current test command, run from the repository root:
  `./scripts/procurement-tests`.
- Historical Phase 4 evidence only: the **142/142 PASS** checkpoint used
  `cd procurement && PYTHONPATH=src python3 -m unittest discover -s tests -v`,
  with 0 failures, 0 errors, 0 skips; unittest time 2.410 seconds and measured
  wall time 3.181 seconds on 2026-08-10. That direct unittest command is
  superseded and must not be used for current validation.
- Coverage includes an isolated, fully rolled-back PostgreSQL integration workflow for raw page persistence, interruption durability, mapping/exclusion audit, local aggregate rebuild, restatement, idempotent rerun, durable range resume, conflicting-alias rollback, and independent review of multiple zero-ID identity groups.

### PR 4a deterministic CI/tooling closeout

- PR #5 / PR 4a is **MERGED / CLOSED**. The exact reviewed head was
  `c04b923f57f0c38411d4e6509163fd7734ef681d`; the owner-approved merge commit
  on `main` is `8d8a07a082a575ef35c6b37ecb6dedc7f47cbbaf`.
- Pre-merge GitHub Procurement CI run #3 was **SUCCESS** on the exact reviewed
  head `c04b923f57f0c38411d4e6509163fd7734ef681d`. Post-merge GitHub Procurement
  CI run #4 was **SUCCESS** on the exact `main` merge commit
  `8d8a07a082a575ef35c6b37ecb6dedc7f47cbbaf`.
- PR 4a and this documentation-only closeout do not complete or alter a
  procurement phase. Phase 4 remains incomplete.
- Baseline at `678a689`: **142/142 PASS**, 0 failures, 0 errors, 0 skips.
- Post-closeout regression evidence: the current complete deterministic suite is
  **160 discovered / 160 executed / 160 passed** on Python 3.13.11 and
  disposable local PostgreSQL 16.9, with 0 failures, 0 errors, 0 skips, 0
  expected failures, and 0 unexpected successes.
- Runner self-tests: **18/18 PASS**. They prove fail-closed handling for expected
  failures, unexpected successes, skips, missing required modules, deficient
  per-module counts, unregistered on-disk test modules, discovery/execution
  mismatch, non-loopback URLs, non-test database names, unsafe URL/libpq routing
  inputs, inherited runtime database isolation, connected-database mismatch, and
  PostgreSQL-major mismatch. The runner self-test module is itself registered at
  its 18-test minimum.
- CI parity is Python 3.13 only, uv 0.12.3, and the immutable PostgreSQL 16 image
  `postgres:16@sha256:95206741a5b214807675e14165369d05b93a9cf692223b616d07cca227e74b0b`.
  The image was independently pulled and reported PostgreSQL 16.14. `uv lock
  --check`, Python compilation, shell syntax, TOML parsing, YAML parsing/format,
  diff whitespace, changed-file secret safety, and origin/main scope checks pass.
- PR 4a scope proof: the branch changed only CI/tooling, runner self-tests, runtime and
  dependency metadata, Procurement test-command/setup documentation, and this
  handoff. There are zero changes under `procurement/src/`, `procurement/db/`,
  `procurement/config/`, or `procurement/docs/PHASE_STATUS.md`. There are zero
  procurement business-logic, API-behavior, migration, Shopify, F4, or PR 4b
  changes; no program/phase milestone changed.
- G12 closeout scope proof: only `docs/CODEX_HANDOFF.md` and `replit.md` change;
  `procurement/docs/PHASE_STATUS.md` remains unchanged.
- Non-blocking future tooling follow-up: the current test-module registration
  invariant assumes a flat `procurement/tests/test_*.py` layout. Nested test
  directories are not yet protected by that completeness invariant.
- The historical PR 4a authorization boundary is superseded by the
  owner-authorized, still-unmerged PR 4b checkpoint below.

### PR 4b authoritative catalog/readiness hardening checkpoint — UNMERGED

- Objective: F1 authoritative catalog-run semantics plus owner-approved F4
  scoped readiness semantics.
- Branch: `hardening/pr-4b-readiness-catalog`.
- Exact base: `d90f7313fc6048697ef74553c3895a88e9ac8a04`.
- Implementation commit:
  `fa848cde427b405838dc6401350487718671ffe4` (`Implement scope-aware readiness
  and catalog authority`).
- PR 4b remains **UNMERGED**. This checkpoint does not authorize merge,
  deployment, or PR 4c.
- F1 result: there is one authoritative newest-attempt catalog selector,
  ordered by `started_at DESC, catalog_sync_id DESC`. It never falls back to an
  older successful run; an incomplete or failed newest attempt fails closed;
  and `CATALOG_SYNC` can pass only when all implemented deterministic catalog
  controls pass.
- F4 result: `FAIL` blocks the affected/applicable scope; `WARN` remains
  non-blocking; missing applicable required evidence fails closed; unrelated
  vendor/variant failures do not create global blocks; exception scope matching
  is conjunctive; and existing global required failures still block.
- Exact completed G4 test totals:
  `discovered=186; executed=186; passed=186; failures=0; errors=0; skips=0;
  expectedFailures=0; unexpectedSuccesses=0`. All 19 registered test modules
  met their per-module minimums, with no missing or unregistered test module.
- Dependency control used the repository-persistent pinned executable, which
  reported `uv 0.12.3 (x86_64-unknown-linux-gnu)`; `uv lock --check` exited 0
  after resolving 22 packages.
- Python compilation passed for `main.py`, `procurement/src`,
  `procurement/tools`, and `procurement/tests`. Working-tree and
  base-to-implementation `git diff --check` controls passed. All 1,036 added
  lines passed the changed-file secret scan; no auth state was tracked; and no
  unintended cache, bytecode, log, temporary, build, dependency, or generated
  artifact was tracked.
- Required PR 4b A-Q adversarial coverage is complete and deterministic.
- Additional F4 guardrails passed: an existing global `VENDOR_RULES` `FAIL`
  still blocks; undeclared missing `VENDOR_RULES` is not a universal blocker;
  declared-applicable missing `VENDOR_RULES` fails closed; and all relevant
  status/API consumers use the same authoritative `catalog_sync_id`.
- G4 used only disposable loopback PostgreSQL test infrastructure. There was
  zero production database access or write and zero Shopify access or write.
  PR 4b made no migration, no identity decision, no PO generation or release,
  and no deployment.
- Phase 4 remains **INCOMPLETE**. The 343 historical identity decisions remain
  untouched, and `SALES_BACKFILL` remains operationally outstanding. No
  official phase/program milestone changed, and
  `procurement/docs/PHASE_STATUS.md` remains unchanged.

### Phase 3 catalog checkpoint

- `CATALOG_SYNC` is **PASS**, last checked `2026-08-10T14:55:53.634178Z`.
- Independently verified ACTIVE Shopify catalog: **1,999 variants**. The unfiltered Shopify-reported count remains 2,003 because it also includes four inactive variants.
- Current variants: 2,049 total = 1,999 `LIVE`/active + 46 `RETIRED_CONFIRMED`/inactive + 4 historical inactive-as-expected (`SEEDED`/inactive, archived in Shopify).
- The 46 retirements remain individually audited. Phase 4 did not change catalog identity or retirement decisions.

### Phase 4 implementation and live run

- ShopifyQL access probe: **PASS** using configured Admin API `2026-07`; store timezone is `America/New_York`. No customer dimensions or Orders API fallback were used.
- Additive migration `procurement/db/006_phase4_sales_backfill.sql` is applied. It adds durable run/chunk/page checkpoints, run-to-fact observations, restatement evidence, complete control fields, and append-only historical-sales review decisions.
- Live run: `d389079c-eabf-49b5-a245-40a207025fd7`, started `2026-08-10T16:44:26.811525Z`, completed `2026-08-10T16:45:59.804015Z`.
- Requested range: **2024-11-28 through 2026-08-10** (current store-local date at execution).
- Coverage: **21/21 date chunks**, **70/70 structurally contiguous pages**, all pages/chunks complete; no parse error, duplicate observation, missing chunk, or coverage gap. Current-code local finalization re-proved page indexes, offsets, terminal-page structure, stored range, and run-creation date evidence without refetching Shopify.
- Durable source: **59,083 source rows = 59,083 unique natural facts**.
- Resolution: **55,971 resolved rows**, **3,112 unresolved rows**, **0 ambiguous rows**, **0 explicitly excluded rows**.
- Owner review queue: **343 unresolved identity groups** ranked by materiality (**341 material**, 2 zero-impact but retained); one group has a SKU-only candidate, which remains evidence only and is not approved.
- Browser review UI: `/procurement/historical-sales/review` (FastAPI route `/historical-sales/review`); JSON: `/procurement/historical-sales/review/items`. Decisions require actor, reason, and `RECONCILIATION_REVIEW_TOKEN`.
- Shopify source totals exactly equal persisted raw totals:
  - net items: **82,501.0000** source = **82,501.0000** raw;
  - net sales: **$1,300,975.14** source = **$1,300,975.14** raw.
- Canonical resolved totals: **78,815.0000 net items** and **$1,231,372.83 net sales**.
- Unresolved totals: **3,686.0000 net items** and **$69,602.31 net sales**; materiality is **3,696.0000 absolute units** and **$72,616.29 absolute sales**.
- Excluded totals: **0 items / $0.00**. There are zero review decisions and zero active historical exclusions at this checkpoint.
- Coverage, source persistence, idempotency, source/raw controls, resolution accounting, and canonical controls all reconcile. The canonical aggregate was rebuilt from this run's durable facts.
- Phase 4 workflow implementation is complete and the initial live fetch is complete, but **Phase 4 is not complete while human identity review and `SALES_BACKFILL` remain outstanding**.

### Current readiness and safety state

| Gate | Current status | Notes |
| --- | --- | --- |
| `CATALOG_SYNC` | `PASS` | Phase 3 catalog remains reconciled. |
| `SALES_BACKFILL` | `FAIL` | 341 material unresolved identity groups (343 total groups) await owner decisions. |
| `VENDOR_RULES` | `FAIL` | Phase not started. |

- PO generation is **disabled**, blocked by `SALES_BACKFILL` and `VENDOR_RULES`; purchase-order count remains zero.
- Shopify remained strictly read-only. Phase 4 stored no customer fields/PII and made zero Shopify writes.
- No automatic historical alias approval or exclusion occurred.

## Historical checkpoint

- Phases 0–2 are complete; the historical seed contains 2,029 identities.
- Pre-retirement Phase 3 had 1,979 exact active historical/current IDs, 20 genuinely new active variants, 46 deleted historical identities, and 4 inactive-as-expected identities.
- Exact lookup plus deterministic continuity review found no credible current counterpart for all 46 deleted identities; human-authorized retirement was executed and audited before the successful post-retirement catalog sync.

## Authorization boundary / next action

PR 4b implementation/G4 is accepted for closeout preparation but remains
**UNMERGED**. The exact next authorization boundary is independent adversarial
review of the final exact branch head, then targeted Cursor read-only specialist
review, then ChatGPT business-rule review. Do not merge without explicit owner
authorization.

The Phase 4 operational boundary below remains unchanged by PR 4b and this
documentation-only closeout preparation.

Stop for owner decisions. The only next Phase 4 operation is authenticated human review of the 343 grouped identities followed by local re-resolution/rebuild through the implemented workflow. Do not auto-map, auto-exclude, refetch Shopify merely to apply local decisions, or force `SALES_BACKFILL`.

Do not begin inventory history, vendor rules, forecasting, pricing ingestion, procurement optimization, or PO generation until the owner explicitly authorizes the next phase.
