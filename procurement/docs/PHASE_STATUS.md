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

**IMPLEMENTATION / AUTHORITY ACCEPTED — PUBLISHED-PRODUCTION CORRECTION IN PROGRESS**

The previously recorded terminal closeout ran against Replit development
`heliumdb`, not published-production `neondb`. Its reviewed implementation,
owner-approved identity authority, and deterministic expected controls remain
accepted. Independent read-only inspection found published production at the
exact frozen pre-correction state: migration 007 absent, zero review decisions,
zero historical exclusions, zero approved Phase 4 old-ID alias families, 59,083
facts = 55,971 resolved / 3,112 unresolved, and `SALES_BACKFILL=FAIL` with
`MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED`.

Owner-authorized corrective implementation is in progress on
`codex/phase4-published-production-reconciliation`. No published-production
connection or write is authorized during implementation/review. The accepted
deterministic terminal target remains:

- range: 2024-11-28 through 2026-08-10
- 21/21 date chunks and 70/70 pages complete
- 59,083 durable unique source facts
- source/raw control totals reconcile exactly
- 57,429 resolved rows
- 1,654 excluded rows
- 0 unresolved rows
- 0 ambiguous rows
- final terminal decisions: 43 RESTORE / 102 MAP / 198 EXCLUDE / 0
  LEAVE_UNRESOLVED
- 96 distinct canonical MAP targets
- 43 exact inactive historical-only identities
- exactly 198 structured active historical exclusions and 56 safe approved
  old-ID alias families
- approved manifest SHA-256:
  `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`
- owner-approved terminal supplement:
  `procurement/review/phase4_terminal_disposition_manifest.csv`, SHA-256
  `fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff`
- prior accepted Phase 4 implementation/merge SHA:
  `dbd4cdc1d48e098e20e8f7642a64fb409966c793`
- post-merge Procurement CI run `33818188106`: **completed / success** on that
  exact SHA; post-production deterministic suite: **305/305 PASS**
- migration 007 applied transactionally with all schema postconditions proven
- terminal dry-run: `PRE_TERMINAL_EXACT`, no diagnostics, read-only/no-XID,
  zero DML
- terminal persistence: `PRE_TERMINAL_EXACT -> CURRENT_TERMINAL_EXACT`, 858
  controlled mutations, protected source/aggregate/gate/PO fingerprints
  unchanged
- second identical terminal persistence: `CURRENT_TERMINAL_EXACT`, zero
  planned mutations and zero committed DML
- final source controls: 82,501.0000 net / 82,545.0000 absolute units and
  $1,300,975.14 net / $1,304,920.80 absolute sales
- resolved controls: 80,659.0000 net / 80,693.0000 absolute units and
  $1,263,133.84 net / $1,264,065.52 absolute sales
- excluded controls: 1,842.0000 net / 1,852.0000 absolute units and $37,841.30
  net / $40,855.28 absolute sales
- final `sales_daily`: 57,424 rows / 80,659.0000 units / $1,263,133.84 sales
- database-derived exclusion integrity: PASS, no diagnostics, exact original
  8-key/189-row bucket and exact exhaustively-unattributable 190-key/1,465-row
  bucket
- final post-rebuild terminal inspection: `CURRENT_TERMINAL_EXACT`, lifecycle
  `POST_REBUILD`, zero planned terminal mutations, read-only/no-XID
- `SALES_BACKFILL` reached PASS through the canonical readiness evaluator with
  zero blockers at `2026-09-04T11:48:28.619695Z`
- `CATALOG_SYNC` remains PASS; `VENDOR_RULES` remains FAIL; purchase orders and
  purchase-order lines remain zero
- Shopify writes, Packet A, Vendor Rules, forecasting, recommendations,
  procurement, PO generation/release, and deployments were not started

**Published-production Phase 4 closeout boundary:** not yet satisfied. It
requires independent corrective implementation review, PR/CI, reviewed
temporary Scheduled Deployment execution against exact `neondb`, and verified
post-execution evidence. Phase 6 remains paused until this prerequisite closes.

### Phase 5 — Foundation UI

**COMPLETE**

- Phase 5 application/UI acceptance remains complete. Its prior live runtime
  exercise was against development `heliumdb`; it is not evidence that the
  separate published `neondb` has received the Phase 4 data closeout.

- All four required operational surfaces are present and live-accepted: System
  Readiness, Catalog Reconciliation, Historical Sales Reconciliation, and
  Data/Sync Runs.
- System Readiness displays all canonical PASS/WARN/FAIL results and exact
  backend-owned messages; canonical PO readiness dynamically displays the
  current blocker.
- Data/Sync Runs is GET-only and read-only, and the four surfaces share
  operational navigation.
- Dedicated Phase 5 tests passed 22/22; the complete deterministic suite passed
  327/327.
- PR #18 merged as `2af6a7258695023ff7c24ad6ff3f1ad9de760d2d`.
- PR-head Procurement CI run `33935952570` and post-merge Procurement CI run
  `33936303756` completed successfully.
- Live no-write acceptance passed: all required routes returned 200; readiness
  and PO controls were unchanged; no GET assigned a transaction XID.
- PO readiness remains **DISABLED** because `VENDOR_RULES=FAIL`; purchase
  orders and purchase-order lines remain zero.

### Phase 6 — Foundation test / acceptance completion

**OWNER AUTHORIZED / PAUSED ON PUBLISHED-PRODUCTION PHASE 4 PREREQUISITE**

Formal Phase 6 foundation acceptance, including the deferred legacy
direct-invocation test-harness hardening item, is authorized but implementation
has not started. Do not begin until the corrective published-production Phase 4
closeout is independently reviewed, executed, and verified.

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

## Published-production readiness summary (pre-correction)

| Gate | Status | Meaning |
| --- | --- | --- |
| `CATALOG_SYNC` | PASS | Catalog identity foundation is reconciled. |
| `SALES_BACKFILL` | FAIL | Published `neondb` still has 3,112 unresolved facts; corrective reconciliation has not executed. |
| `VENDOR_RULES` | FAIL | Not yet built/validated. |
| PO readiness | DISABLED | Blocked by `SALES_BACKFILL` and `VENDOR_RULES`; POs and PO lines remain zero. |

## Project-management rule

Every meaningful milestone must close out using `docs/PROJECT_GOVERNANCE.md`, including machine validation, risk-appropriate independent review, verified post-change state, Git history, and `docs/CODEX_HANDOFF.md` update. No phase may be declared complete by code existence alone.
