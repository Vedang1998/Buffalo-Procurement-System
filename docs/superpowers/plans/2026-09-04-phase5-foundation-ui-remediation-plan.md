# Phase 5 Foundation UI Acceptance Remediation — Implementation Plan

**Design:** `docs/superpowers/specs/2026-09-04-phase5-foundation-ui-remediation-design.md`
**Baseline:** `983656c1fbebeaeec3be4db4ed8d43e87aa9aa77`
**Design commit:** `7be544b8ccd89aba5fc78d74cf2d385f3e3753ef`

## Constraints

- One writer: Codex.
- No production connection or DML.
- No Shopify access.
- No migration, readiness mutation, job trigger, rebuild, or PO action.
- Preserve every existing route and protected reconciliation decision path.
- Keep Phase 5 formally open pending independent acceptance.

## Task 1 — Add failing Phase 5 acceptance tests

Files:

- create `procurement/tests/test_phase5_foundation_ui.py`;
- update `procurement/tools/run_tests.py` with a non-decreasing module floor.

Cover canonical readiness aggregation, every gate/status/message, dynamic PO
blocking, the current VENDOR_RULES blocker, route registration, authoritative
catalog selection, canonical historical-run selection, durable evidence,
read-only Data/Sync Runs markup, and shared navigation on all four pages.

Run the dedicated module against disposable PostgreSQL and confirm the intended
pre-implementation failures.

## Task 2 — Extend backend operational status

File: `procurement/src/procurement_os/health.py`.

- Use `po_readiness()` for effective gates and PO state.
- Preserve the current `gates` compatibility mapping while exposing the full
  canonical readiness payload.
- Keep unavailable database/schema states explicitly fail-closed.
- Add a SELECT-only data/sync run evidence helper.
- Anchor historical selection to the canonical SALES_BACKFILL gate run, then
  the established reviewable-run selector, then a labeled deterministic
  diagnostic fallback.
- Use the existing authoritative catalog evaluator for catalog evidence.

Run the dedicated tests.

## Task 3 — Implement shared rendering and the new route

File: `procurement/src/procurement_os/api.py`.

- Add one depth-aware shared navigation renderer.
- Refactor System Readiness rendering into a testable helper.
- Render all backend-returned gates and exact messages.
- Render canonical PO enabled/disabled state and blocker messages.
- Add an inert disabled PO control only when blocked.
- Add `GET /data-sync-runs` and its escaped, read-only renderer.
- Add navigation to both normal and empty states of Catalog Reconciliation and
  Historical Sales Reconciliation.
- Preserve all existing mutation routes and forms unchanged.

Run the dedicated tests and existing catalog/historical API modules.

## Task 4 — Validate safety and regressions

Run:

1. dedicated Phase 5 tests;
2. existing catalog/readiness/historical review tests;
3. `./scripts/procurement-tests`;
4. `uv lock --check` with pinned uv 0.12.3;
5. Python compilation for runtime, tools, and tests;
6. `git diff --check` against baseline;
7. changed-file secret scan and tracked generated/auth-artifact scan;
8. route/method and source-call-path checks proving no new mutation path.

## Task 5 — Record implementation checkpoint

File: `docs/CODEX_HANDOFF.md`.

Record baseline, branch, exact implementation result, changed files, tests,
zero production/Shopify/PO activity, and the next review boundary. Do not edit
`procurement/docs/PHASE_STATUS.md` and do not mark Phase 5 complete.

Commit all implementation/test/documentation changes, push the branch, verify
the remote SHA, and stop for ChatGPT review before PR/merge.
