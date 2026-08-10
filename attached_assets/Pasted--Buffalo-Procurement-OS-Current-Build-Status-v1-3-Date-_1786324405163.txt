# Buffalo Procurement OS — Current Build Status v1\.3

**Date:** August 9, 2026
**Purpose:** Tell a new AI/engineer exactly what exists versus what is still pending production execution\.

## Executive state

Architecture planning is considered closed enough to build\. The project is now in implementation mode\.

The current code package is `Buffalo_Procurement_OS_v1_3_Catalog_Sales_Foundation.zip`\.

### Already coded

- PostgreSQL schema/foundation\.
- machine rules config\.
- CURRENT/FUTURE pricing foundations\.
- BT/CS economics foundations\.
- assortment guardrails\.
- review scopes\.
- supplier matching foundations\.
- portable verified seed CSV bundle\.
- Shopify token/client layer\.
- GraphQL catalog pagination/parsing\.
- catalog reconciliation classifications and readiness gate\.
- historical ShopifyQL sales query/chunking/pagination\.
- raw historical sales storage \+ canonical resolution design\.
- current\-ID / old\-ID alias / historical SKU\-title handling\.
- null/zero historical Variant\-ID handling\.
- ambiguous identity fail\-closed behavior\.
- historical exclusions\.
- local re\-resolution and canonical `sales_daily` rebuild\.
- SALES\_BACKFILL readiness gate\.
- automated test suite\.

### Verified seed counts

- 2,029 variants\.
- 3,301 aliases\.
- 4 vendors\.
- 85 supplier offers\.
- 271 August price levels\.
- 263 verified price rows / 8 unverified price rows\.

### Live foundation facts observed before this packet

- connected Shopify current variant probe: 2,003 variants;
- historical ShopifyQL daily sales successfully returned from opening date, 2024\-11\-28;
- historical analytics include examples with null/zero historical Variant ID while historical SKU/title survives\.

These are precisely why the reconciliation/alias layer exists\.

### What is NOT yet legitimately complete

- schema has not yet been applied to the actual final Replit production database in the packaged build environment;
- seed has not yet been imported into that final production database;
- the entire live current catalog has not yet been persisted/reconciled there;
- all live recreation/retirement blockers have not yet been human\-resolved;
- complete historical ShopifyQL backfill has not yet been persisted there;
- all material historical identity exceptions have not yet been resolved/excluded;
- CATALOG\_SYNC is not yet allowed to claim PASS;
- SALES\_BACKFILL is not yet allowed to claim PASS;
- production PO reliance is disabled\.

## Immediate executable sequence

1. Create/open Replit project and production PostgreSQL\.
2. Put v1\.3 code under Git/GitHub\.
3. Run existing tests before changes\.
4. Apply schema/migrations\.
5. Import seed CSV bundle and validate counts/foreign keys\.
6. Add Shopify app credentials to Replit deployment secrets\.
7. Execute full catalog sync/reconciliation\.
8. Review only genuine catalog recreation/retirement blockers\.
9. Re\-run/recompute until CATALOG\_SYNC = PASS\.
10. Execute ShopifyQL sales backfill from 2024\-11\-28 through current date\.
11. Review unresolved material historical identities; save approved aliases/exclusions\.
12. Re\-resolve locally/rebuild canonical sales until SALES\_BACKFILL = PASS\.
13. Start nightly inventory snapshots\.
14. Complete vendor operating profiles\.
15. Continue to price\-book engine\.

## Current code\-package build report

# Buffalo Procurement OS v1\.3 — Build Report

**Build date:** 2026\-08\-09
**Milestone:** Catalog \+ historical\-sales foundation

## What changed from v1\.2

The architecture review is closed\. v1\.3 implements the accepted Replit\-centered design and restores the original Procurement OS fail\-closed sequencing\.

### New production foundation

- Replit/portable deployment configuration documented\.
- Shopify merchant\-owned client\-credentials token provider\.
- Auditable GraphQL client with 401 refresh \+ 429/5xx retry/backoff\.
- Live catalog paginator and parser\.
- Catalog identity reconciliation with exact/new/missing/changed/recreation\-candidate classifications\.
- No automatic old→new Variant\-ID merge\.
- Human approval functions for recreated identity or confirmed retirement\.
- Queryable `CATALOG_SYNC` readiness gate\.

### Historical sales

- ShopifyQL daily sales query contract tested against the connected store back to 2024\-11\-28\.
- Bounded date chunking \+ LIMIT/OFFSET pagination\.
- Source facts stored before canonical aggregation\.
- Source\-row natural hash prevents double\-counting when Shopify analytics restates a historical aggregate after returns/adjustments\.
- Current Variant\-ID resolution\.
- Old Variant\-ID alias resolution\.
- historical SKU/title alias resolution\.
- deterministic unique\-SKU fallback\.
- null/zero historical Variant\-ID handling\.
- ambiguity never fuzzy\-auto\-resolved\.
- explicit human mapping and explicit historical exclusion mechanisms\.
- local re\-resolution after aliases change, without refetching Shopify\.
- canonical `sales_daily` rebuild\.
- `SALES_BACKFILL` gate\.

## Seed materialized

The old SQLite seed has been converted into portable CSVs so the new system does not depend on the SQLite file surviving\.

- 2,029 variants
- 3,301 aliases
- 4 vendors
- 85 supplier offers
- 271 CURRENT August price levels
- 263 verified / 8 unverified price levels
- 4 source open exceptions; the obsolete old CATALOG\_SYNC exception is superseded by the new readiness gate during migration

All seed relationships validate with zero missing canonical Variant IDs/offers\.

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

**40 automated tests pass\.**

Coverage includes:

- BT vs CS arithmetic;
- Target Cost;
- assortment default/restriction/cross\-product exception;
- supplier\-match size conflicts;
- CURRENT/FUTURE rollover guard;
- review\-decision scope;
- catalog exact/new/missing/recreation behavior;
- live\-catalog node parsing;
- historical current\-ID/old\-ID/SKU/title resolution;
- null/zero historical IDs;
- ambiguous duplicate SKU behavior;
- structured exclusions;
- natural source\-row identity hashing;
- ShopifyQL pagination;
- Shopify client\-credentials token caching/refresh;
- historical query evidence fields\.

Python compilation/import smoke tests also pass\.

## What is intentionally NOT claimed yet

- The PostgreSQL schema has not yet been applied to the user’s future Replit production database in this build environment\.
- A full 2,003\-row live Shopify catalog has not yet been materialized into that Postgres database\.
- The complete historical ShopifyQL dataset has not yet been written into production Postgres\.
- Therefore CATALOG\_SYNC and SALES\_BACKFILL remain intentionally FAIL until those production jobs execute and any human identity exceptions are resolved\.
- No production PO output is enabled\.

## Next executable sequence

1. Create/open the Replit project and production Postgres\.
2. Apply `db/schema_postgres.sql`\.
3. Run `tools/import_seed_csv.py`\.
4. Configure Shopify deployment secrets\.
5. Run `python -m procurement_os.jobs.catalog_sync`\.
6. Review only catalog blockers; approve recreations/retirements\.
7. Recompute/rerun catalog gate until PASS\.
8. Run `python -m procurement_os.jobs.sales_backfill --start 2024-11-28 --end <today>`\.
9. Review unresolved historical identities; save aliases/exclusions\.
10. Re\-run local identity resolution until SALES\_BACKFILL = PASS\.
11. Begin the inventory\-history/vendor\-rules phase\.

## Current phase status

# Build Status — v1\.3

## Architecture review

**CLOSED\.** Replit\-centered, deterministic production architecture accepted\.

## Phase 1A — production foundation

**BUILT**

- PostgreSQL schema
- hard rules config
- CURRENT/FUTURE lifecycle
- BT/CS economics
- assortment guardrails
- review scopes
- supplier matching foundation
- API skeleton

## Phase 1B — verified August seed bundle

**BUILT / READY TO IMPORT**

Included from v0\.1:

- 2,029 variant seed rows
- 3,301 historical alias rows
- 4 vendors
- 85 supplier offers
- 271 CURRENT August price levels
- 4 old open exceptions

Seed CSV hashes are in `seed/manifest.json`\.

## Phase 2A — live catalog identity foundation

**CODE BUILT; PRODUCTION EXECUTION PENDING REPLIT DATABASE \+ SHOPIFY APP CREDENTIALS**

Implemented:

- merchant\-owned Shopify client\-credentials token provider
- portable GraphQL client with retry/backoff
- full catalog pagination
- exact\-ID reconciliation
- missing/new/changed classification
- strong recreation candidates by exact SKU/barcode
- no automatic historical identity merge
- persistent catalog sync \+ reconciliation evidence
- CATALOG\_SYNC readiness gate

Live connected probe on 2026\-08\-09: 2,003 variants\. Historical seed count is not treated as expected equality\.

## Phase 2B — historical sales foundation

**CODE BUILT; PRODUCTION BACKFILL PENDING REPLIT DATABASE \+ SHOPIFY APP CREDENTIALS**

Implemented:

- ShopifyQL daily net sales query contract
- date chunking \+ LIMIT/OFFSET pagination
- raw fact storage
- current\-ID resolution
- old\-ID alias resolution
- historical SKU/title resolution
- null / zero historical Variant\-ID handling
- ambiguous/unresolved fail\-closed behavior
- canonical `sales_daily` reconstruction
- SALES\_BACKFILL readiness gate

A live connected ShopifyQL probe successfully returned sales from 2024\-11\-28\.

## Phase 2C — next build

After the first two gates execute against production Postgres:

1. resolve catalog blockers;
2. resolve historical\-sales identity exceptions;
3. begin nightly inventory snapshots;
4. complete vendor operating profiles;
5. move to the supplier/price\-book ingestion subsystem\.

## PO readiness

**INTENTIONALLY DISABLED** until at least:

- CATALOG\_SYNC = PASS
- SALES\_BACKFILL = PASS
- required vendor/pricing gates for the affected PO are PASS\.