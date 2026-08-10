# Buffalo Procurement OS v1.3 — Build Report

**Build date:** 2026-08-09  
**Milestone:** Catalog + historical-sales foundation

## What changed from v1.2

The architecture review is closed. v1.3 implements the accepted Replit-centered design and restores the original Procurement OS fail-closed sequencing.

### New production foundation

- Replit/portable deployment configuration documented.
- Shopify merchant-owned client-credentials token provider.
- Auditable GraphQL client with 401 refresh + 429/5xx retry/backoff.
- Live catalog paginator and parser.
- Catalog identity reconciliation with exact/new/missing/changed/recreation-candidate classifications.
- No automatic old→new Variant-ID merge.
- Human approval functions for recreated identity or confirmed retirement.
- Queryable `CATALOG_SYNC` readiness gate.

### Historical sales

- ShopifyQL daily sales query contract tested against the connected store back to 2024-11-28.
- Bounded date chunking + LIMIT/OFFSET pagination.
- Source facts stored before canonical aggregation.
- Source-row natural hash prevents double-counting when Shopify analytics restates a historical aggregate after returns/adjustments.
- Current Variant-ID resolution.
- Old Variant-ID alias resolution.
- historical SKU/title alias resolution.
- deterministic unique-SKU fallback.
- null/zero historical Variant-ID handling.
- ambiguity never fuzzy-auto-resolved.
- explicit human mapping and explicit historical exclusion mechanisms.
- local re-resolution after aliases change, without refetching Shopify.
- canonical `sales_daily` rebuild.
- `SALES_BACKFILL` gate.

## Seed materialized

The old SQLite seed has been converted into portable CSVs so the new system does not depend on the SQLite file surviving.

- 2,029 variants
- 3,301 aliases
- 4 vendors
- 85 supplier offers
- 271 CURRENT August price levels
- 263 verified / 8 unverified price levels
- 4 source open exceptions; the obsolete old CATALOG_SYNC exception is superseded by the new readiness gate during migration

All seed relationships validate with zero missing canonical Variant IDs/offers.

## Database additions

- `catalog_sync_runs`
- `catalog_reconciliation_items`
- `readiness_gates`
- `sales_backfill_runs`
- `shopify_sales_daily_raw`
- `historical_sales_exclusions`
- `mapping_rejections`
- `daily_inventory_snapshots`
- `run_price_snapshots`
- `change_log`
- Procurement PO reconciliation fields

## Tests

**40 automated tests pass.**

Coverage includes:
- BT vs CS arithmetic;
- Target Cost;
- assortment default/restriction/cross-product exception;
- supplier-match size conflicts;
- CURRENT/FUTURE rollover guard;
- review-decision scope;
- catalog exact/new/missing/recreation behavior;
- live-catalog node parsing;
- historical current-ID/old-ID/SKU/title resolution;
- null/zero historical IDs;
- ambiguous duplicate SKU behavior;
- structured exclusions;
- natural source-row identity hashing;
- ShopifyQL pagination;
- Shopify client-credentials token caching/refresh;
- historical query evidence fields.

Python compilation/import smoke tests also pass.

## What is intentionally NOT claimed yet

- The PostgreSQL schema has not yet been applied to the user's future Replit production database in this build environment.
- A full 2,003-row live Shopify catalog has not yet been materialized into that Postgres database.
- The complete historical ShopifyQL dataset has not yet been written into production Postgres.
- Therefore CATALOG_SYNC and SALES_BACKFILL remain intentionally FAIL until those production jobs execute and any human identity exceptions are resolved.
- No production PO output is enabled.

## Next executable sequence

1. Create/open the Replit project and production Postgres.
2. Apply `db/schema_postgres.sql`.
3. Run `tools/import_seed_csv.py`.
4. Configure Shopify deployment secrets.
5. Run `python -m procurement_os.jobs.catalog_sync`.
6. Review only catalog blockers; approve recreations/retirements.
7. Recompute/rerun catalog gate until PASS.
8. Run `python -m procurement_os.jobs.sales_backfill --start 2024-11-28 --end <today>`.
9. Review unresolved historical identities; save aliases/exclusions.
10. Re-run local identity resolution until SALES_BACKFILL = PASS.
11. Begin the inventory-history/vendor-rules phase.
