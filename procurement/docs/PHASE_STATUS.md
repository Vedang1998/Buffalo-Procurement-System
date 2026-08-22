# Buffalo Procurement OS — Executive Phase Status

**Current checkpoint source:** `docs/CODEX_HANDOFF.md`  
**Project process authority:** `docs/PROJECT_GOVERNANCE.md`

This file is the executive roadmap/status view. It is updated when a phase/program milestone changes. It does not replace the canonical system specification or the verified current-state handoff.

## Architecture

**CLOSED / ACCEPTED.** Replit-centered, deterministic, fail-closed production architecture. No mandatory runtime LLM. Shopify Variant ID remains canonical identity.

## Official implementation phases

### Phase 0 — Safe working repository / baseline

**COMPLETE**

- Git repository established
- baseline tests preserved
- authority documents retained
- no-secret discipline established

### Phase 1 — Production infrastructure

**COMPLETE**

- Replit PostgreSQL
- health/status foundation
- portable storage abstraction
- Shopify environment configuration
- production-safe PostgreSQL guardrails

### Phase 2 — Schema + verified seed

**COMPLETE**

Verified seed foundation includes:

- 2,029 historical variant identities
- 3,301 historical aliases
- 4 vendors
- 85 supplier offers
- 271 August/current seed price levels

Seed integrity/import audit passed.

### Phase 3 — Live catalog reconciliation

**COMPLETE — `CATALOG_SYNC = PASS`**

Verified production state at the current handoff:

- 1,999 ACTIVE Shopify variants
- 46 deleted historical identities investigated, human-authorized for local historical retirement, and individually audited
- 4 historical inactive-as-expected identities preserved
- no unresolved catalog identity blockers
- zero unauthorized Shopify writes

### Phase 4 — Historical ShopifyQL sales backfill / reconciliation

**OWNER-APPROVED TERMINAL DISPOSITION FROZEN; INDEPENDENT ARTIFACT REVIEW AND IMPLEMENTATION PENDING; REBUILD PENDING — `SALES_BACKFILL = FAIL`**

Current handoff records:

- range: 2024-11-28 through 2026-08-10
- 21/21 date chunks and 70/70 pages complete
- 59,083 durable unique source facts
- source/raw control totals reconcile exactly
- 55,971 resolved rows
- 3,112 unresolved rows
- 0 ambiguous rows
- 343/343 owner decisions persisted with current manifest provenance
- decisions: 55 MAP / 8 EXCLUDE / 280 LEAVE_UNRESOLVED
- 51 distinct canonical MAP targets
- exactly 8 active historical exclusions and 17 safe old-ID alias families
- approved manifest SHA-256:
  `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`
- production persistence execution SHA:
  `30b6d81d2b53ad66200d4821255597e3766d72f7`
- second production dry-run: `CURRENT_PROVENANCE=343` and all planned
  mutations `0`
- independent full review: **APPROVE WITH NON-BLOCKING FINDINGS**; the sole
  LOW finding was remediated and the targeted delta review returned **APPROVE**
  with no findings
- PR #13 is **MERGED**; reviewed final head
  `28370f6176c235391a5682146703326af6f7a96f`, merge/main SHA
  `4d0c12fec29780214b944c6d625faec5cc8a30c5`
- exact-head Procurement CI run `32435931948`: **243/243 PASS**; post-merge CI
  run `32436953358`: **SUCCESS**
- protected sales, resolution, readiness-gate, and PO fingerprints are
  unchanged; purchase orders remain 0 and purchase-order lines remain 0
- historical source resolution and canonical aggregate remain unchanged pending rebuild
- review UI available at `/procurement/historical-sales/review`
- historical-sales rebuild/re-resolution was not run
- `SALES_BACKFILL` and readiness-gate reevaluation were not run
- owner-approved terminal supplement:
  `procurement/review/phase4_terminal_disposition_manifest.csv`, SHA-256
  `fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff`
- supplement controls: 280/280 prior LEAVE keys; 43 RESTORE / 47 MAP / 190
  EXCLUDE / 0 unresolved
- combined terminal intent: 43 RESTORE / 102 MAP / 198 EXCLUDE / 0
  LEAVE_UNRESOLVED; current production effective state remains the prior 55 MAP
  / 8 EXCLUDE / 280 LEAVE until separately authorized implementation
- expected eventual post-rebuild controls: 57,429 RESOLVED / 1,654 EXCLUDED /
  0 UNRESOLVED / 0 AMBIGUOUS; 80,659 resolved and 1,842 excluded net units;
  $1,263,133.84 resolved and $37,841.30 excluded net sales; expected
  `sales_daily` rows 57,424
- terminal exclusion readiness is not status-based: every material excluded
  source key must later prove an effective owner-approved exact-key decision,
  allowlisted reason, manifest/evidence provenance, no target, complete
  membership and exact reason-coded rows/units/sales reconciliation
- no runtime, schema, rules, database, catalog, alias, sales, gate, Shopify,
  procurement or PO change was authorized or performed by the artifact freeze

**Next Phase 4 checkpoint:** independent adversarial review of the owner-approved
authority change and SHA-pinned terminal manifest. Implementation, restoration,
persistence, historical-sales rebuild and readiness-gate evaluation remain
separately unauthorized.

### Phase 5 — Foundation UI

**PARTIALLY DELIVERED AS NEEDED; FORMAL PHASE NOT YET CLOSED**

Existing operational pages include readiness/catalog/historical-sales review functionality. Formal acceptance should follow the canonical Phase 5 requirements after Phase 4 review is resolved.

### Phase 6 — Foundation test / acceptance completion

**NOT YET CLOSED**

The project already has substantial adversarial/integration coverage, but formal foundation acceptance remains gated by the pending Phase 4 rebuild, gate reevaluation, and canonical completion criteria.

## Post-foundation ordered workstreams

After the required foundation gates pass, continue in the canonical order:

1. nightly inventory snapshots + inventory/adjustment evidence;
2. vendor calendars, minimums, broken-case fees, loose-bottle rules, lead-time/reliability profiles;
3. Procurement PO ledger and open-PO reconciliation;
4. universal supplier price-book staging/import contract;
5. Empire parser;
6. Southern regular parser;
7. Southern combo parser;
8. CURRENT/FUTURE structural comparison and guarded monthly rollover;
9. forecasting V1 + backtesting/FVA;
10. strategic procurement economics / break-assortment optimizer;
11. human review queue;
12. one PO per vendor + Shopify-compatible PO CSV + Emergency Packet;
13. shadow mode before trusted production purchasing.

These are program workstreams. Do not renumber or overwrite official phase IDs in the canonical implementation documents without owner-approved change control.

## Current readiness summary

| Gate | Status | Meaning |
| --- | --- | --- |
| `CATALOG_SYNC` | PASS | Catalog identity foundation is reconciled. |
| `SALES_BACKFILL` | FAIL | Terminal intent is artifact-frozen only; independent review, implementation, rebuild and gate reevaluation remain pending. |
| `VENDOR_RULES` | FAIL | Not yet built/validated. |
| PO readiness | DISABLED | Intentionally blocked while required gates fail. |

## Project-management rule

Every meaningful milestone must close out using `docs/PROJECT_GOVERNANCE.md`, including machine validation, risk-appropriate independent review, verified post-change state, Git history, and `docs/CODEX_HANDOFF.md` update. No phase may be declared complete by code existence alone.
