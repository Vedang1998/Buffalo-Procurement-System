---
name: Buffalo Procurement OS operating rules
description: Non-negotiable process rules for working on the procurement system in procurement/
---

- **Owner approval gates canonical phase transitions, not every checkpoint.** Follow section 13 of `docs/PROJECT_GOVERNANCE.md`: inside an authorized task, implement/test/repair/checkpoint/continue. Do not start an unapproved next phase. Read current status from `docs/CODEX_HANDOFF.md` and `procurement/docs/PHASE_STATUS.md`; do not hard-code a current phase here.
- **Why:** Real inventory cash rides on this system; preserve canonical business rules, machine-enforced gates and the authority order in `docs/PROJECT_GOVERNANCE.md`. This repository is Buffalo Procurement OS only, separate from ProPo/Stocky.
- **How to apply:** At each meaningful milestone publish a source-bound handoff and continue eligible authorized work. At an unapproved phase or consequential action boundary, stop that transition. Claude reviews frozen commits read-only; Codex is the sole implementation writer; Cursor is targeted specialist review.
- Preserve v1.3 source as-is; no rewrites for style/framework preference. Python is the sole procurement backend — never duplicate business logic in Node.
- CATALOG_SYNC / SALES_BACKFILL must reflect the required evidence through normal gate logic; never force PASS or FAIL. Keep operational PO generation disabled until applicable live-data, review and authorization gates pass. Never auto-merge Variant identities; Shopify Variant ID is canonical, supplier SKU is evidence only.
- Schema quirk: `prices` table uses `price_state` ('current'/'future', lowercase), not `state`.
- Seed audit trail lives in `seed_import_records` (migration 002). Seed CSVs validate against `procurement/seed/manifest.json` hashes; do not "fix" seed rows during import.
- Ordinary development is cloud-only; no Mac/Replit SSH dependency. Existing Replit assets remain legacy recovery/operations context; Railway is the intended separately authorized destination. Shopify secrets belong only in the approved runtime secret store; no secret transfer is implied. Never log tokens or store them in Git/reusable development snapshots. Missing creds must not stop app startup.
- Shopify search-syntax quirk: `product_status:active` must be lowercase — `product_status:ACTIVE` silently matches nothing on productVariants (count query tolerates it). Verify pagination totals via summing active products' variantsCount: `productVariantsCount` IGNORES its query argument and always returns the store-wide variant total — never use it to verify a filtered fetch.
- Reconciliation identity decisions are token-gated via RECONCILIATION_REVIEW_TOKEN (fail-closed 503 when unset). One approved continuity per old Variant ID; decisions valid only against unresolved blockers; rejected pairs can never be silently approved.

## Retirement lifecycle (Phase 3 complete)
Retiring a missing seed variant sets `active=FALSE, catalog_state='RETIRED_CONFIRMED'`; on the NEXT fresh sync it classifies as non-blocking INACTIVE, so gates re-pass honestly without manual forcing. **Why:** owner mandate — never force gates; recompute from DB. **How to apply:** after human dispositions, re-run the sync job to prove the gate state rather than trusting `recompute_catalog_gate` alone. Procurement OS is browsable at `/procurement` via a raw-body reverse proxy in api-server (mounted before body parsers).
