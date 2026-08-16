# Phase 4 Historical Review Catalog Search — Design

**Date:** 2026-08-16  
**Base:** `6a833f8318549aaf4b62ff400168b306579b90c6`  
**Branch:** `phase4/historical-review-catalog-search`  
**Status:** Owner-approved design for authorized Phase 4 support work

## Objective and boundary

Add a small, read-only local catalog search/picker to the existing historical-sales human-review page. The helper lets a reviewer inspect an existing canonical `variants` record and copy its exact Shopify Variant ID into the existing `MAP_TO_CANONICAL` form. Search and selection never decide identity, record a review decision, create an alias or exclusion, rebuild sales, change readiness, access Shopify, or access production data.

This work does not start Phase 5 or any post-foundation workstream. It adds no migration, dependency, deployment, production access, identity decision, or readiness change. `procurement/docs/PHASE_STATUS.md` remains unchanged.

## Canonical target eligibility

The existing mapping contract is exact membership in the local `variants` table. `record_historical_sales_review_decision` rejects an unknown Variant ID and verifies that the resulting resolver state points uniquely to the requested target before committing the audited decision. `HistoricalIdentityIndex` intentionally preserves inactive historical variants as valid canonical owners of their historical sales.

The search therefore returns matching rows from `variants` without inventing an `active` or `catalog_state` restriction. It displays both fields so the reviewer can evaluate them. Narrowing the picker to `active = TRUE` and `catalog_state = 'LIVE'` would create a new eligibility rule and could hide a valid preserved historical target. The permanent mapping path remains the final authority and retains its existing resolver-effect validation.

## Search service

Add `search_historical_sales_catalog(conn, query, limit=20)` to `procurement_os.sales`.

- Trim the query; a blank query returns no rows without executing SQL.
- Reject a query longer than 128 characters.
- Bound the result limit to a positive value no greater than 20.
- Search local `variants` fields: `variant_id`, `sku`, `barcode`, `product_title`, `variant_title`, and `handle`.
- Use only fixed SQL plus bound parameters. Escape PostgreSQL `LIKE` wildcard characters so `%`, `_`, and `\` in user input are literal evidence, not uncontrolled wildcards.
- Return only stored evidence: Variant ID, product title, variant title, SKU, barcode, active status, and catalog state.
- Order deterministically by exact Variant ID; exact SKU/barcode; identifier/title prefix; other substring match; then stable Variant ID.
- Perform one `SELECT` and no mutations, external calls, logging, or Shopify-client construction.

Search ordering is navigation relevance only. The endpoint and UI will not expose confidence, approval, recommendation, or automatic-match labels.

## API

Add `GET /historical-sales/review/catalog-search?q=<query>` to the existing FastAPI application.

The route opens the normal local database connection, calls the search service, and returns:

```json
{
  "query": "trimmed query",
  "count": 1,
  "items": [
    {
      "variant_id": "123",
      "product_title": "Stored product title",
      "variant_title": "750ML",
      "sku": "SKU-123",
      "barcode": "012345678901",
      "active": true,
      "catalog_state": "LIVE"
    }
  ]
}
```

The read-only route requires no review token. Input validation errors return a bounded client error. The page handles lookup failures with a generic message and never renders raw exception details.

## Review-page picker

Keep the existing server-rendered page and forms. Each unresolved identity card receives its own local catalog search input, explicit search button, status area, and result container adjacent to the existing mapping form.

Minimal vanilla JavaScript will:

1. submit only the trimmed catalog query to the read-only endpoint;
2. render returned values with DOM `textContent` rather than HTML interpolation;
3. label entries neutrally as local catalog results;
4. require an explicit `Select Variant ID` click;
5. populate only the canonical-Variant-ID field in the result's containing review card; and
6. never submit the mapping form or call the decision endpoint.

No result is pre-selected. Multiple cards remain isolated by resolving controls through the nearest review-card container. Empty results display exactly `No local catalog results found.` Lookup failures display a generic search error. Existing deterministic candidates, evidence, conflicts, materiality, sales impact, reviewer, reason, review-token, exclusion, and leave-unresolved controls remain intact.

## Validation

Extend the existing API test module and PostgreSQL integration module rather than adding a new test module.

Deterministic coverage will prove:

- route registration and response shape;
- blank and whitespace input;
- maximum query length and bounded result limit;
- exact Variant ID, SKU, barcode, product-title, variant-title, and handle search;
- literal wildcard and quote handling with no injection path;
- no-result behavior and deterministic ordering;
- existing inactive/preserved targets remain visible with status evidence because they are valid under the current mapping contract;
- unknown targets remain rejected by the existing mapping path;
- search performs no writes by comparing control counts before and after a disposable PostgreSQL lookup;
- search does not instantiate or call a Shopify client;
- picker selection copies the exact returned ID, is card-local, and does not call the decision service;
- existing candidates and human-approval controls remain visible and unchanged.

Run the repository-required baseline and final `./scripts/procurement-tests`, `uv lock --check`, Python compilation, `git diff --check`, changed-file secret scan, and `origin/main` scope review. Final acceptance requires discovered = executed = passed, with zero failures, errors, skips, expected failures, or unexpected successes.

## Git and handoff

After deterministic validation, update `docs/CODEX_HANDOFF.md` with the exact base, branch, implementation commit, test totals, changed files, safety counts, unchanged Phase 4 gate and group boundary, and the independent-review requirement. Commit and push the feature branch only. Do not create a pull request, merge, deploy, access production, or record identity decisions.
