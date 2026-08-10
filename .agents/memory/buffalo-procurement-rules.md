---
name: Buffalo Procurement OS operating rules
description: Non-negotiable process rules for working on the procurement system in procurement/
---

- **Owner approval gates every phase.** Assessment → approval → build → stop-and-report → approval. Never start the next phase (currently: Phase 3 live catalog reconciliation) without explicit approval.
- **Why:** Real inventory cash rides on this system; the owner mandated fail-closed sequencing in the authority docs (see `procurement/docs/authority/`, order defined in replit.md).
- **How to apply:** After each phase, report test count, git commit, gate states, deviations; then wait.
- Preserve v1.3 source as-is; no rewrites for style/framework preference. Python is the sole procurement backend — never duplicate business logic in Node.
- CATALOG_SYNC / SALES_BACKFILL must remain FAIL and PO generation disabled until proven against live production data. Never auto-merge Variant identities; Shopify Variant ID is canonical, supplier SKU is evidence only.
- Schema quirk: `prices` table uses `price_state` ('current'/'future', lowercase), not `state`.
- Seed audit trail lives in `seed_import_records` (migration 002). Seed CSVs validate against `procurement/seed/manifest.json` hashes; do not "fix" seed rows during import.
- Shopify secrets (SHOPIFY_SHOP, SHOPIFY_CLIENT_ID, SHOPIFY_CLIENT_SECRET) go in Replit Secrets only; never log tokens. Missing creds must not stop app startup.
- Shopify search-syntax quirk: `product_status:active` must be lowercase — `product_status:ACTIVE` silently matches nothing on productVariants (count query tolerates it). Verify pagination totals via summing active products' variantsCount: `productVariantsCount` IGNORES its query argument and always returns the store-wide variant total — never use it to verify a filtered fetch.
- Reconciliation identity decisions are token-gated via RECONCILIATION_REVIEW_TOKEN (fail-closed 503 when unset). One approved continuity per old Variant ID; decisions valid only against unresolved blockers; rejected pairs can never be silently approved.

## Retirement lifecycle (Phase 3 complete)
Retiring a missing seed variant sets `active=FALSE, catalog_state='RETIRED_CONFIRMED'`; on the NEXT fresh sync it classifies as non-blocking INACTIVE, so gates re-pass honestly without manual forcing. **Why:** owner mandate — never force gates; recompute from DB. **How to apply:** after human dispositions, re-run the sync job to prove the gate state rather than trusting `recompute_catalog_gate` alone. Procurement OS is browsable at `/procurement` via a raw-body reverse proxy in api-server (mounted before body parsers).
