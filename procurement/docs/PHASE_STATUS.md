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

**OWNER DECISIONS PERSISTED; REBUILD PENDING — `SALES_BACKFILL = FAIL`**

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
- exactly 8 active historical exclusions and 17 safe old-ID alias families
- historical source resolution and canonical aggregate remain unchanged pending rebuild
- review UI available at `/procurement/historical-sales/review`
- full test suite: 241/241 PASS at the current Phase 4 checkpoint

**Next Phase 4 checkpoint:** independent adversarial review of the persistence
implementation and production results. Historical re-resolution/rebuild and
`SALES_BACKFILL` gate reevaluation require separate owner authorization after
that review.

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
| `SALES_BACKFILL` | FAIL | Owner decisions are persisted; rebuild and gate reevaluation remain pending. |
| `VENDOR_RULES` | FAIL | Not yet built/validated. |
| PO readiness | DISABLED | Intentionally blocked while required gates fail. |

## Project-management rule

Every meaningful milestone must close out using `docs/PROJECT_GOVERNANCE.md`, including machine validation, risk-appropriate independent review, verified post-change state, Git history, and `docs/CODEX_HANDOFF.md` update. No phase may be declared complete by code existence alone.
