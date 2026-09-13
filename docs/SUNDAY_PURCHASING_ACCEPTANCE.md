# Sunday Purchasing Candidate — Acceptance Checklist

Contract: `BUFFALO-SATURDAY-PURCHASING-COMPLETION-2026-09-12`

Hard cutoff: 2026-09-13 13:00 UTC. Final validation/recovery reserve begins at
11:30 UTC.

This checklist describes the isolated local candidate only. It grants no
production, Shopify, supplier-contact, price-activation, deployment, or order
authority.

## Preserved starting point

- Reader/QA I0 closeout `5f86eee6c1954ed35cee72fcfa123b75256295b5`.
- Reviewed merge-hook safety head `9cbbf3688fbf2be24ad319a742bff5f484e0a573`.
- Reviewed persistent-mapping design head `b0c8d3fec8ec57e57881e91615cc6f7c3cf75b4d`.
- Wright structural candidate `e0bd6ce641cefef198dc472d80cf690df3c1d247`
  remains separate and unchanged.

## Capability checkpoint at sprint start

| Capability | Start state | Acceptance target |
|---|---|---|
| Hook-safe isolated integration | Working | Preserve exact inert hook and test union |
| Schema through migration 013 | Working | Fresh/upgrade predecessor proof remains green |
| Verified V5/A1 package reader | Working offline | Bounded, hash-verified intake adapter |
| Persistent mapping registry | Missing | Five tables, four views, append-only services |
| Source/mapping browser | Missing | Authenticated, paged search/detail/decision/selection |
| Legacy Monday engine | Working backend | Browser-complete evidence/review/DRAFT path |
| Inventory, sales and incoming adapters | Partial | Synthetic/recorded local workflow; live access off |
| Pricing and vendor rules | Partial/working | Exact legacy CURRENT continuity; unsafe gaps block |
| Forecast portfolio | Partial | Honest implemented-model status and known-answer proof |
| Private session and CSRF | Missing | Loopback-only synthetic named-session boundary |
| Restart and local recovery | Missing | Durable launcher plus database/artifact backup/restore |
| Production/commercial readiness | Blocked/not approved | Remain separate from local acceptance |

## Required vertical proofs

- [ ] Fabricated sealed intake persists immutable batch/candidates and exact replay.
- [ ] Unresolved candidate DEFER and exact legacy-offer APPROVE are separate events.
- [ ] Routine selection requires a second confirmation and shadow `MATCH`.
- [ ] Synthetic inventory/sales/vendor/price evidence prepares recommendations.
- [ ] Quantity edit recalculates economics; MATERIAL edit has distinct confirmation.
- [ ] Blocked lines stay out; one internal DRAFT per vendor is built and downloaded.
- [ ] Unauthorized list/detail/source/download/write and stale/CSRF requests refuse.
- [ ] Restart, exact replay, backup and restore preserve state without duplicates.
- [ ] Focused tests, startup checks, browser acceptance and one final full suite pass.

## Morning states to report separately

- `LOCAL_END_TO_END`: not yet evaluated.
- `COMMERCIAL_DATA_READINESS`: `NOT_APPROVED`.
- `PRODUCTION_RELEASE`: `BLOCKED` and not authorized.
- `FULL_PRODUCT_REQUIREMENTS`: audit in progress; the canonical forecast and
  price-authority gaps must remain explicit if not closed.
