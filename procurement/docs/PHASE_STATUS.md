# Build Status — v1.3

## Architecture review
**CLOSED.** Replit-centered, deterministic production architecture accepted.

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

Included from v0.1:
- 2,029 variant seed rows
- 3,301 historical alias rows
- 4 vendors
- 85 supplier offers
- 271 CURRENT August price levels
- 4 old open exceptions

Seed CSV hashes are in `seed/manifest.json`.

## Phase 2A — live catalog identity foundation
**CODE BUILT; PRODUCTION EXECUTION PENDING REPLIT DATABASE + SHOPIFY APP CREDENTIALS**

Implemented:
- merchant-owned Shopify client-credentials token provider
- portable GraphQL client with retry/backoff
- full catalog pagination
- exact-ID reconciliation
- missing/new/changed classification
- strong recreation candidates by exact SKU/barcode
- no automatic historical identity merge
- persistent catalog sync + reconciliation evidence
- CATALOG_SYNC readiness gate

Live connected probe on 2026-08-09: 2,003 variants. Historical seed count is not treated as expected equality.

## Phase 2B — historical sales foundation
**CODE BUILT; PRODUCTION BACKFILL PENDING REPLIT DATABASE + SHOPIFY APP CREDENTIALS**

Implemented:
- ShopifyQL daily net sales query contract
- date chunking + LIMIT/OFFSET pagination
- raw fact storage
- current-ID resolution
- old-ID alias resolution
- historical SKU/title resolution
- null / zero historical Variant-ID handling
- ambiguous/unresolved fail-closed behavior
- canonical `sales_daily` reconstruction
- SALES_BACKFILL readiness gate

A live connected ShopifyQL probe successfully returned sales from 2024-11-28.

## Phase 2C — next build

After the first two gates execute against production Postgres:
1. resolve catalog blockers;
2. resolve historical-sales identity exceptions;
3. begin nightly inventory snapshots;
4. complete vendor operating profiles;
5. move to the supplier/price-book ingestion subsystem.

## PO readiness

**INTENTIONALLY DISABLED** until at least:
- CATALOG_SYNC = PASS
- SALES_BACKFILL = PASS
- required vendor/pricing gates for the affected PO are PASS.
