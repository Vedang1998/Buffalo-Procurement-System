# Phase 5 Foundation UI Acceptance Remediation — Design

**Date:** 2026-09-04
**Owner authorization:** Phase 5 Foundation UI acceptance remediation
**Baseline:** `983656c1fbebeaeec3be4db4ed8d43e87aa9aa77`
**Risk:** LOW-to-MODERATE; operational read-only presentation only
**Status:** Approved for implementation; formal Phase 5 acceptance remains open

## Objective

Bring the existing Foundation UI to the canonical Phase 5 acceptance shape with
the smallest possible delta. The required operational surfaces are:

1. System Readiness (`/admin/status`)
2. Catalog Reconciliation (`/reconciliation`)
3. Historical Sales Reconciliation (`/historical-sales/review`)
4. Data/Sync Runs (`/data-sync-runs`)

Every existing route remains unchanged. `/data-sync-runs` is the sole new
canonical route; no `/runs`, `/sync`, redirect, or alias is added.

## Authority and safety boundary

Backend readiness services remain the sole source of truth. The UI must not
calculate, infer, or hard-code readiness status, PO applicability, or blockers.
The implementation consumes:

- canonical `po_readiness()` output;
- the authoritative catalog evaluator and its newest-attempt semantics; and
- existing durable catalog/sales run and checkpoint tables.

The change adds no migration, business rule, readiness mutation, catalog/sales
job invocation, Shopify client call, PO action, or production write. It does
not begin formal Phase 6 or any post-foundation workstream.

## Backend operational-status aggregation

Extend the existing health/status layer so one database connection obtains the
effective readiness picture from `po_readiness()`. Preserve the existing
machine-readable health fields while making all effective gate rows and the
canonical PO readiness result available to the human page.

The catalog gate continues to be overlaid by the authoritative catalog-run
evaluator through the existing readiness service. The remaining gate rows are
the database-backed canonical messages and statuses. The presentation layer
iterates over the returned rows; it contains no list of expected gate results
and no rule that translates a gate into a PO blocker.

When database/schema health prevents canonical evaluation, the health report
remains fail-closed and PO generation remains disabled.

## System Readiness page

`/admin/status` will:

- render every backend-returned readiness gate, including its scope when
  relevant, status, and canonical message;
- render PASS, WARN, and FAIL distinctly without changing their meaning;
- display `PO generation: ENABLED` or `PO generation: DISABLED` directly from
  canonical `po_readiness()`;
- display every canonical blocker with its gate/exception identity and message;
  and
- show a disabled, inert PO control whenever readiness is disabled.

The disabled control is not inside a form and has no action URL, event handler,
JavaScript, or POST endpoint. No PO creation capability is added. If canonical
readiness eventually becomes enabled, the page may show the enabled readiness
state, but this remediation still does not supply an actionable PO control.

All backend-provided text is HTML-escaped.

## Data/Sync Runs

Add one `GET /data-sync-runs` HTML route backed by a small SELECT-only service
helper.

### Catalog evidence

Use the existing authoritative catalog evaluator rather than independently
ordering catalog runs. Display useful fields already returned by that service:

- catalog sync run ID;
- start and completion timestamps;
- run status;
- Shopify API version;
- live rows, Shopify-reported count, exact current IDs, and new live variants;
- pagination completion;
- unresolved blockers; and
- authoritative readiness status/message.

### Historical-sales evidence

Select the current operational historical-sales run from the canonical global
`SALES_BACKFILL` gate evidence when it identifies a `sales_backfill_id`. This
keeps the displayed run aligned with the readiness result that the application
actually trusts, even if another row has a later timestamp. If gate evidence
does not identify a run, use the established `latest_reviewable_run()` domain
selector for the durable, fully completed run eligible for owner review and
canonical use. Only when neither stronger definition yields a run may the
helper expose the newest attempted run for fail-closed diagnostic visibility.
That final fallback ordering is deterministic (`started_at DESC`, then run ID)
and is explicitly labeled current-attempt rather than canonical evidence.

Display existing durable evidence only:

- sales backfill run ID;
- requested start/end dates;
- start/completion timestamps and status;
- completed/expected chunks and pages;
- unique source fact count;
- resolved, unresolved, ambiguous, and excluded row counts;
- durable/control/aggregate state already recorded on the run; and
- canonical SALES_BACKFILL readiness status/message.

No new run-state model is introduced. The page contains no form, job button,
mutation link, JavaScript fetch, or POST/action path. In particular it exposes
no Sync now, Retry, Rebuild, or equivalent control.

## Shared operational navigation

Add a small shared renderer with exactly these visible labels:

- System Readiness
- Catalog Reconciliation
- Historical Sales Reconciliation
- Data/Sync Runs

It is included on all four required pages. Links are depth-aware and relative
so they work both through the production `/procurement` reverse-proxy prefix
and against direct FastAPI routes. Existing route paths and behavior remain
unchanged.

Catalog Reconciliation may also expose Catalog Identity Investigation as a
subordinate link, not as a fifth top-level tab.

## Tests

Add a dedicated Phase 5 test module and register a non-decreasing module floor.
Tests will prove:

1. every backend-returned gate is rendered;
2. PASS/WARN/FAIL and exact escaped messages render correctly;
3. PO state and blockers come from canonical `po_readiness()` output;
4. CATALOG_SYNC=PASS, SALES_BACKFILL=PASS, VENDOR_RULES=FAIL remains disabled
   specifically for the canonical VENDOR_RULES message;
5. the stale two-gate sentence is absent;
6. `/data-sync-runs` is registered and returns catalog and sales evidence;
7. catalog evidence uses the authoritative evaluator;
8. historical evidence prefers the canonical SALES_BACKFILL gate run, then
   honors the established reviewable-run selector and deterministic diagnostic
   fallback;
9. the new route is SELECT-only and exposes no mutation control or job/Shopify
   call path;
10. all four pages contain the shared four-surface navigation;
11. existing reconciliation decision forms and security behavior remain
    unchanged; and
12. the full deterministic Procurement OS suite and `git diff --check` pass.

Validation also includes Python compilation, lock validation, repository scope
review, and secret/generated-artifact checks.

## Documentation and stop condition

At implementation closeout, update `docs/CODEX_HANDOFF.md` to record the exact
branch/SHA, files, tests, and zero-write evidence as “implementation complete;
awaiting independent acceptance review.” Do not change Phase 5 to COMPLETE in
`procurement/docs/PHASE_STATUS.md`.

Push the implementation branch and stop. The exact next action is ChatGPT
review of the Phase 5 remediation before PR/merge.
