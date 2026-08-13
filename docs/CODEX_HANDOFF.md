# Buffalo Procurement OS — Codex Handoff

**Updated:** 2026-08-13T14:44:20Z (UTC)

**Phase numbering:** This handoff follows `procurement/docs/authority/03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md`: Phase 3 is catalog reconciliation and Phase 4 is historical ShopifyQL sales backfill/reconciliation.

This is an operational checkpoint, not a replacement for the canonical specification or `procurement/config/rules.toml`. Verify repository and database state again before acting.

**Operating process:** every coding/review/release session must follow `docs/PROJECT_GOVERNANCE.md`. At each meaningful milestone, this handoff must be refreshed with verified state, tests, readiness gates, material counts/control totals, open risks/decisions, Git reference, and exact next authorization boundary.

## Verified current state

### Repository and tests

- Active unmerged branch: `tooling/overnight-hardening-2026-08-12`, based exactly on `origin/main` commit `c03969f4a3ddfcd05d95a7bd66d04df714ca1e75`.
- Phase 4 starting HEAD: `90a6b9ec2469d541ff11cb3716807754fd4edb05` (`Add durable Codex and Claude project handoff`). Verified Phase 4 implementation checkpoint: `a78b5808551f3bae584367a631cf25776d3ff038` (`Phase 4 historical sales backfill and reconciliation workflow`).
- Verified overnight logic/test checkpoint: `980b1fba88b8ccde5530e1c01425586ce139d345`. Verified audit/task-packet checkpoint: `7c736e19c09da2880981173f42e3cf3c9dd54d2f`.
- Canonical developer/CI test command, run from repository root: `./scripts/procurement-tests`.
- Overnight baseline: **142/142 PASS**, 0 failures, 0 errors, 0 skips.
- Final local CI-equivalent result: **159 discovered / 159 executed / 159 PASS**, 0 failures, 0 errors, 0 skips; unittest time 1.563 seconds and complete command wall time 14.575 seconds on 2026-08-13.
- Coverage includes an isolated PostgreSQL Phase 4 workflow plus full migration-chain idempotency, readiness-gate completeness, catalog-run failure precedence, approved-only identity resolution, disabled price rollover, and permanent safety contracts. The runner rejects non-loopback/non-`_test` databases, missing expected modules, test counts below 159, discovery/execution mismatch, any skip, or any failure/error.

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
| `INVENTORY_HISTORY` | `WARN` | Later workstream is not accepted or operational. |
| `VENDOR_RULES` | `FAIL` | Phase not started. |
| `PRICE_COVERAGE` | `WARN` | Later price-book workstream is not accepted. |
| `MAPPING_INTEGRITY` | `WARN` | Later supplier-mapping validation is not accepted. |
| `OPEN_PO_RECONCILIATION` | `WARN` | Later PO-ledger reconciliation is not operational. |

- Hardened PO readiness now requires all seven canonical global gates to exist and be `PASS`; `WARN`, `FAIL`, or missing required evidence blocks. PO generation is **disabled** and purchase-order count remains zero.
- Shopify remained strictly read-only. Phase 4 stored no customer fields/PII and made zero Shopify writes.
- No automatic historical alias approval or exclusion occurred.

## Overnight engineering hardening milestone — unmerged

### Result and CI

- Local engineering result is **PASS**; repository/governance closeout is **PARTIAL** pending remote CI, independent review, owner acceptance, and GitHub publication.
- Added `.github/workflows/procurement-tests.yml` for pull requests targeting `main` and pushes to `main`. Required job/check name is exactly `procurement-tests`.
- CI installs from root `pyproject.toml`/`uv.lock`, uses a disposable PostgreSQL 17 service, and calls the same `./scripts/procurement-tests` command used locally. No production URL or application secret is referenced.
- Workflow YAML parsed successfully and the required job name was independently checked locally. GitHub Actions cannot run until the branch is published and a PR targets `main`.
- GitHub publication is **blocked in this environment**: `origin` SSH push was denied because no usable public-key credential is available, and `gh auth status` reports no authenticated host. No credential was installed or changed. The committed local branch is intact and clean once this handoff commit is created.

### Canonical defects fixed and tests added

- Fixed two CRITICAL Class A defects: incomplete/missing readiness gates could falsely enable PO generation; operational price rollover lacked the canonical backup/completeness/transition/assertion guards. Rollover is now explicitly disabled until its authorized phase.
- Fixed three HIGH Class A defects: incomplete catalog evidence could pass, an older completed catalog run could mask a newer failed run, null/zero-ID historical sales could resolve from unapproved current SKU/title evidence, and fuzzy supplier similarity could auto-match. The two catalog symptoms are one catalog-readiness finding in the structured inventory.
- Fixed two MEDIUM Class A defects: migration SQL/audit recording was not atomic; permanent catalog decisions allowed incomplete actor/reason provenance.
- Fixed one LOW Class A secret-safety defect: malformed token responses could be echoed into an exception.
- Added **17 deterministic tests** (142 to 159) covering catalog evidence, catalog run precedence, required gate completeness/scope, approved-only historical identity, disabled rollover, permanent-decision provenance, full migration-chain idempotency/audit, no PO route, one-PO/vendor schema, automation-off rules, and token-payload redaction.
- Read-only production quantification found **zero** facts resolved by the removed unapproved current-SKU/title method, so no production remediation is indicated by that fix.
- Complete structured findings and classifications: `procurement/docs/CANONICAL_AUDIT_2026-08-13.md`.

### Files changed

- CI/parity: `.github/workflows/procurement-tests.yml`, `scripts/procurement-tests`, `procurement/tools/run_tests.py`, `procurement/README.md`.
- Implementation: `procurement/src/procurement_os/api.py`, `catalog.py`, `matching.py`, `pricing.py`, `readiness.py`, `sales.py`, `shopify/auth.py`, and `procurement/tools/apply_schema.py`.
- Tests: `test_catalog.py`, `test_matching.py`, `test_phase4_historical_sales.py`, `test_phase4_postgres_integration.py`, `test_pricing.py`, `test_readiness.py`, `test_safety_contracts.py`, `test_sales.py`, `test_schema_migrations.py`, and `test_shopify_auth.py` under `procurement/tests/`.
- Documentation: `docs/CODEX_HANDOFF.md`, `docs/TOOLING_SETUP.md`, `docs/superpowers/specs/2026-08-13-overnight-hardening-design.md`, `procurement/docs/CANONICAL_AUDIT_2026-08-13.md`, and `procurement/docs/FUTURE_PHASE_TASK_PACKETS.md`.
- `procurement/docs/PHASE_STATUS.md` was intentionally unchanged because no phase/program milestone changed.

### Validation and security evidence

- `uv lock --check`, Python compilation, shell syntax, YAML parse/job-name check, migration/integrity tests, Git whitespace review, and tracked-secret/auth-state scans passed.
- Authentication state under `.ai-auth/` remains ignored and untracked. `procurement/.env.example` contains placeholders/public API-version configuration. No tracked private-key or token signature was detected.
- Pre-existing seed CSVs and the historical v1.3 source packet were reviewed as intentional repository inputs, not overnight-generated production exports.
- Latest production read-only verification used a database-enforced read-only transaction. All seven gate states are unchanged; 7/7 migration markers are present; purchase orders, historical review decisions, and active historical exclusions remain zero.
- Phase 4 controls remain exact: 21/21 chunks, 70/70 pages, 59,083 source/unique facts, 55,971 resolved, 3,112 unresolved, 0 ambiguous/excluded; source/raw 82,501.0000 units and $1,300,975.14; canonical 78,815.0000 units and $1,231,372.83; unresolved 3,686.0000 units and $69,602.31.
- Production writes: **ZERO**. Shopify writes: **ZERO**. PO generation/release: **ZERO**. Automatic owner decisions: **ZERO**.

### Prepared later work and intentional deferrals

- `procurement/docs/FUTURE_PHASE_TASK_PACKETS.md` preserves current foundation phase IDs, canonical phases 3–9, and all 13 ordered post-foundation workstreams. Each packet records prerequisites/readiness, scope, dependencies, anticipated schema, human decisions, acceptance/tests/controls, idempotency, containment, review, release gate, and exact authorization boundary.
- No later operational feature was implemented. Inventory/vendor/pricing/forecasting/procurement/PO work remains gated by Phase 4 and explicit phase authorization.
- Owner decisions remain: the 343 Phase 4 identity groups; future verified vendor terms, supplier mappings, price transitions, model/policy acceptance, strategic quantities, combo/new-item decisions, and every final PO.
- Independent Claude review, targeted Cursor review, ChatGPT business/program review, remote CI proof, PR, merge, deployment, and release are intentionally deferred.

## Historical checkpoint

- Phases 0–2 are complete; the historical seed contains 2,029 identities.
- Pre-retirement Phase 3 had 1,979 exact active historical/current IDs, 20 genuinely new active variants, 46 deleted historical identities, and 4 inactive-as-expected identities.
- Exact lookup plus deterministic continuity review found no credible current counterpart for all 46 deleted identities; human-authorized retirement was executed and audited before the successful post-retirement catalog sync.

## Authorization boundary / next action

Morning engineering sequence: restore an owner-controlled GitHub authentication route without sharing credentials in chat; push `tooling/overnight-hardening-2026-08-12`; open a draft PR to `main`; require green `procurement-tests`; obtain Claude adversarial review; remediate through one writer; obtain targeted Cursor and ChatGPT reviews; then request owner merge acceptance. Do not merge or deploy before those steps.

The only next Phase 4 business operation remains authenticated human review of the 343 grouped identities followed by local re-resolution/rebuild through the implemented workflow. Do not auto-map, auto-exclude, refetch Shopify merely to apply local decisions, or force `SALES_BACKFILL`.

Do not begin inventory history, vendor rules, forecasting, pricing ingestion, procurement optimization, or PO generation until the owner explicitly authorizes the next phase.
