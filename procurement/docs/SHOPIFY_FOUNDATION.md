# Shopify Foundation Contract

## Live schema probes completed 2026-08-09

The currently connected Buffalo House store was probed before implementing the foundation adapters.

### Catalog

Current live probe returned **2,003 product variants**. This is a control observation only, not an acceptance count.

Historical seed: 2,029 identities.
Old v0.1 failed sync observation: 1,998.
Current connected observation: 2,003.

Therefore reconciliation must classify identities; simple row-count equality is forbidden as an acceptance rule.

Verified current GraphQL fields used by the adapter:
- ProductVariant.id / legacyResourceId
- ProductVariant.sku / barcode / price
- ProductVariant.createdAt / updatedAt
- ProductVariant.inventoryQuantity
- ProductVariant.inventoryItem.id / sku / tracked / unitCost
- Product.id / legacyResourceId / title / handle / status / vendor / productType

### Historical sales

A live ShopifyQL probe successfully returned daily sales from **2024-11-28**, the store-opening date, grouped by:
- day
- product_variant_id
- product_title_at_time_of_sale
- product_variant_title_at_time_of_sale
- product_variant_sku_at_time_of_sale
- net_items_sold
- net_sales

Observed historical rows include:
- normal old/current Variant IDs;
- `product_variant_id = 0`;
- null product_variant_id;
while SKU/title-at-time-of-sale can survive.

This validates the raw-first / alias-resolution-second architecture.

### Sales backfill rule

1. Store the ShopifyQL source row unchanged enough to reproduce its identity/economics.
2. Resolve to current canonical Variant ID using deterministic identity evidence.
3. Do not fuzzy-auto-map historical revenue.
4. Unresolved/ambiguous material rows keep SALES_BACKFILL at FAIL.
5. Once resolved, aggregate canonical daily demand into `sales_daily`.

### Catalog reconciliation rule

- Same Variant ID: authoritative current identity.
- New Variant ID: add as new identity unless evidence links it to a missing old identity.
- Missing old active ID: blocker until classified.
- Exact SKU/barcode candidate for a missing old ID: evidence of recreation, but **never auto-merge**.
- Human-approved recreation becomes an alias and historical continuity remains on the current Variant ID.

### Inventory

Current schema supports multi-state inventory through InventoryLevel quantities. Procurement will also store its own daily snapshots so stockout evidence remains available independently of future Shopify analytics changes.

## Required app access

The initial production app should be read-first. Minimum capabilities must cover:
- products/catalog and current costs;
- inventory/location states;
- ShopifyQL reports/sales analytics;
- sufficient historical order/report access only where a specific fallback requires it.

Do not add Shopify write scopes until a concrete workflow needs them.
