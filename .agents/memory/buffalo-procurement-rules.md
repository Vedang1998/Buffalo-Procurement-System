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
