# Buffalo Procurement OS — Codex Handoff

**Updated:** 2026-08-14T19:10:12Z (UTC)

**Phase numbering:** This handoff follows `procurement/docs/authority/03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md`: Phase 3 is catalog reconciliation and Phase 4 is historical ShopifyQL sales backfill/reconciliation.

This is an operational checkpoint, not a replacement for the canonical specification or `procurement/config/rules.toml`. Verify repository and database state again before acting.

**Operating process:** every coding/review/release session must follow `docs/PROJECT_GOVERNANCE.md`. At each meaningful milestone, this handoff must be refreshed with verified state, tests, readiness gates, material counts/control totals, open risks/decisions, Git reference, and exact next authorization boundary.

## Verified current state

### Repository and tests

- Branch: `main`.
- Phase 4 starting HEAD: `90a6b9ec2469d541ff11cb3716807754fd4edb05` (`Add durable Codex and Claude project handoff`). Verified Phase 4 implementation checkpoint: `a78b5808551f3bae584367a631cf25776d3ff038` (`Phase 4 historical sales backfill and reconciliation workflow`).
- Authoritative current test command, run from the repository root:
  `./scripts/procurement-tests`.
- Historical Phase 4 evidence only: the **142/142 PASS** checkpoint used
  `cd procurement && PYTHONPATH=src python3 -m unittest discover -s tests -v`,
  with 0 failures, 0 errors, 0 skips; unittest time 2.410 seconds and measured
  wall time 3.181 seconds on 2026-08-10. That direct unittest command is
  superseded and must not be used for current validation.
- Coverage includes an isolated, fully rolled-back PostgreSQL integration workflow for raw page persistence, interruption durability, mapping/exclusion audit, local aggregate rebuild, restatement, idempotent rerun, durable range resume, conflicting-alias rollback, and independent review of multiple zero-ID identity groups.

### PR 4a deterministic CI/tooling checkpoint

- Authorized tooling branch: `tooling/pr-4a-deterministic-ci`; intermediate
  checkpoint `678a6892dee47906f9350f8e1521ec1ce85cc4b7`. Independently reviewed head
  `159dfb4abdc9693120ad577fe4af6a7906a07734` received APPROVE with no CRITICAL
  or HIGH findings and no merge blockers. The branch-head remediation containing
  this handoff fixes only review findings S-1 and S-2. This work does not complete
  or alter a procurement phase.
- Baseline at `678a689`: **142/142 PASS**, 0 failures, 0 errors, 0 skips.
- Current complete deterministic suite: **160/160 PASS** on Python 3.13.11 and
  disposable local PostgreSQL 16.9, with 0 failures, 0 errors, 0 skips, 0 expected
  failures, and 0 unexpected successes. Discovery and execution both equal 160.
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
- Scope proof: the branch changes only CI/tooling, runner self-tests, runtime and
  dependency metadata, Procurement test-command/setup documentation, and this
  handoff. There are zero changes under `procurement/src/`, `procurement/db/`,
  `procurement/config/`, or `procurement/docs/PHASE_STATUS.md`. There are zero
  procurement business-logic, API-behavior, migration, Shopify, F4, or PR 4b
  changes; no program/phase milestone changed.
- Git push and GitHub check evidence occur after the closeout commit is created.
  GitHub CLI is not authenticated in this workspace, so PR/check inspection is
  an external remaining proof item even when the Git branch push succeeds.

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

PR 4a stops after pushing `tooling/pr-4a-deterministic-ci`. Do not merge until
the GitHub `procurement-tests` check passes and the owner accepts the PR. Do not
implement the separately approved F4 recommendation here, and do not start PR
4b.

The Phase 4 operational boundary below remains unchanged by PR 4a.

Stop for owner decisions. The only next Phase 4 operation is authenticated human review of the 343 grouped identities followed by local re-resolution/rebuild through the implemented workflow. Do not auto-map, auto-exclude, refetch Shopify merely to apply local decisions, or force `SALES_BACKFILL`.

Do not begin inventory history, vendor rules, forecasting, pricing ingestion, procurement optimization, or PO generation until the owner explicitly authorizes the next phase.
