# Buffalo House Procurement OS v1.3 — Catalog + Historical Sales Foundation

**North Star:** More Margin. Less Cash. Faster Turn. Profit Sooner.

v1.3 resumes construction in the original fail-closed order: **identity first, historical demand second, then inventory/vendor rules, pricing, forecasting, strategy, review and POs.**

## Permanent architecture

- Replit is the operating home.
- Replit Autoscale serves the browser/API.
- Replit Scheduled Deployments run batch jobs.
- Replit PostgreSQL holds structured Procurement intelligence.
- Replit App Storage holds supplier files/exports/backups.
- GitHub mirrors code/migrations/tests.
- One encrypted off-platform backup protects against account/vendor-level failure.
- No runtime LLM is required.

## Shopify

Canonical identity = **Shopify Variant ID**.

Supplier SKU is mapping evidence, not identity.

Current build adapters support:
- live catalog reconciliation;
- current SKU/barcode/retail/current cost fields;
- ShopifyQL historical daily sales;
- historical alias repair.

## Hard readiness gates

No final PO output is trusted while:
- `CATALOG_SYNC = FAIL`, or
- `SALES_BACKFILL = FAIL`.

The database also supports scoped vendor/variant readiness gates so one bad item does not force staff to bypass every safety control.

## Current build contents

### Database
- `db/schema_postgres.sql`
- `db/001_v1_3_catalog_sales.sql`

### Seed
- `seed/variants.csv` — 2,029
- `seed/variant_aliases.csv` — 3,301
- `seed/vendors.csv` — 4
- `seed/supplier_offers.csv` — 85
- `seed/current_prices.csv` — 271
- `seed/open_exceptions.csv` — 4
- `seed/manifest.json`

### Shopify foundation
- `src/procurement_os/shopify/auth.py`
- `src/procurement_os/shopify/graphql.py`
- `src/procurement_os/shopify/queries.py`
- `src/procurement_os/catalog.py`
- `src/procurement_os/sales.py`

### Scheduled job entrypoints
- `python -m procurement_os.jobs.catalog_sync`
- `python -m procurement_os.jobs.sales_backfill --start 2024-11-28` (discovers and enforces the current Shopify store-local end date; use `--resume RUN_ID` only for an interrupted durable run)

### Seed migration
After applying `db/schema_postgres.sql`:

```bash
python tools/import_seed_csv.py --seed-dir seed --database-url "$DATABASE_URL"
```

### Test suite

```bash
./scripts/procurement-tests
```

Run the command from the repository root. It invokes the locked `uv`
environment, provisions disposable local PostgreSQL when needed, discovers the
complete suite, and fails on missing modules, a reduced baseline count, skipped
tests, or any test failure. CI supplies its own ephemeral PostgreSQL service and
uses the same command. Tests never inherit the runtime `DATABASE_URL`.

## Important accepted rules retained

- one vendor = one PO;
- no automatic retail price changes;
- Target Cost diagnostic only;
- CURRENT/FUTURE only — no reusable supplier-price archive;
- run-level pricing snapshot retained for audit;
- same-product assortment by default;
- explicit supplier-program cross-product exceptions only;
- book `DOES NOT ASSORT` wins;
- no 50ML/100ML blanket assortment exception;
- BT and CS never interchangeable;
- gift packs are separate supplier offers and never overwrite normal SKU identity;
- combo auto-add OFF;
- vendor minimum is a warning, not a filler mandate;
- ONE_BOTTLE policy override;
- Allocated Bourbon excluded from routine auto-replenishment;
- RUN_ONLY / TEMPORARY / PERMANENT human intelligence stored structurally;
- no AI chat memory as source of truth.

See `docs/MASTER_PLAN_v2_0.md` for the complete current design.
