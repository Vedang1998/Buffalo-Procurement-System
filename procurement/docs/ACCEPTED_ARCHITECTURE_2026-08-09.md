# Accepted Production Architecture — 2026-08-09

This file closes the architecture-review phase. It records only the changes accepted after the independent Gemini / Claude / Grok reviews and the subsequent primary-source verification.

## Permanent home

**Buffalo Procurement OS runs on Replit.**

- Browser application: Replit Autoscale deployment initially.
- Heavy/background work: Replit Scheduled Deployments.
- Database: Replit production PostgreSQL.
- Files: Replit App Storage.
- Code source of truth / portability: GitHub mirror.
- Disaster copy: one encrypted off-Replit backup target.

A Reserved VM is not a V1 requirement. Add it only if measured production behavior demonstrates a need for continuous compute.

## AI posture

There is **no required runtime LLM**.

The purchasing calculation must complete with AI unavailable.

Allowed AI uses:
1. interactive development with Codex / Claude Code / Replit Agent;
2. manual subscription-AI compilation of a supplier book into the strict import contract when a deterministic parser fails;
3. optional manual second-opinion review packets.

AI never directly promotes supplier data into VERIFIED FUTURE and never owns business memory.

## Shopify

Canonical identity is Shopify Variant ID.

The production app uses the current Shopify Admin GraphQL surface and a merchant-owned app authentication mechanism. The adapter is isolated so authentication details can change without touching the procurement engine.

Shopify remains the source of truth for:
- current catalog;
- current SKU/barcode/retail price/current cost;
- sales analytics;
- current inventory and incoming;
- collections;
- receiving/native PO workflow.

Buffalo Procurement remains the source of truth for:
- historical identity aliases;
- supplier offers/SKUs;
- CURRENT/FUTURE supplier ladders;
- forecasting and optimizer output;
- review decisions and policies;
- Procurement-side PO ledger and reconciliation evidence.

## Foundation ordering

The original v0.1 fail-closed ordering is restored as the production build order:

1. Catalog identity reconciliation.
2. Historical sales backfill to canonical current Variant IDs.
3. Daily inventory snapshots + vendor operating rules.
4. Supplier mappings and price-book ingestion.
5. Forecasting.
6. Normal replenishment.
7. Break / assortment / gift-pack / combo economics.
8. Review.
9. One PO per vendor.

No final PO output is trusted while CATALOG_SYNC or SALES_BACKFILL is FAIL.

## Pricing lifecycle

User decision remains authoritative:

- CURRENT + FUTURE only.
- No reusable internal monthly supplier-price archive.
- FUTURE never overwrites CURRENT during upload/review.
- On the 1st, verified FUTURE replaces CURRENT and FUTURE clears.
- Every run freezes the exact pricing rows/economics used in `run_price_snapshots` for audit/reproducibility. This is not a reusable price archive.

## Forecast V1

Keep the enterprise ideas; reduce model theater:

- damped ETS for regular demand;
- seasonal naive + category seasonal context;
- TSB for intermittent demand;
- category/analog shrinkage for new/thin products;
- naive fallback;
- rolling-origin backtesting;
- Forecast Value Added gate before selecting complexity;
- WAPE/MASE/bias/service outcomes;
- ABC on gross-profit dollars with FORCE_A override;
- XYZ for predictability;
- empirical forecast-error/service-level protection, vendor-specific protection periods.

Auto-ARIMA is a research candidate, not required V1 production logic.

## Strategic economics

Keep:
- Target Cost (diagnostic only);
- immediate purchase savings;
- Profit Driver;
- legitimate Filler;
- Bridge Cash;
- GP / $100 Bridge Cash;
- resulting inventory days;
- GMROI/capital productivity context.

Add time explicitly:
- expected depletion days;
- GP per inventory-dollar-month / equivalent time-adjusted incremental return.

Projected 90-day GP is secondary context and cannot be presented as immediate savings.

## Human intelligence that must remain structured

Nothing below may depend on a chat remembering it:
- gift packs;
- combos;
- same-product default assortment;
- explicit cross-product assortment programs;
- book DOES NOT ASSORT restrictions;
- BT vs CS;
- one-bottle policies;
- allocated-bourbon exclusion;
- vendor minimum warnings;
- temporary price/reversion intelligence;
- supplier aliases and negative mappings;
- supplier SKU transition classification;
- events/tastings/promotions/launches;
- RUN_ONLY / TEMPORARY / PERMANENT decisions;
- human-approved special forward buys.

## Business continuity

Every completed/finalized Monday run will eventually create an Emergency Packet containing at minimum:
- run summary;
- recommendations;
- one PO CSV per vendor;
- run pricing/economics snapshot;
- supplier mapping export;
- open Procurement PO ledger;
- unresolved exceptions.

A Replit outage should make Monday inconvenient, not make purchasing impossible.
