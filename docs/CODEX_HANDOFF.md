# Buffalo Procurement OS — Codex Handoff

**Updated:** 2026-08-10T15:37:13Z (UTC)

**Phase numbering:** This handoff follows `procurement/docs/authority/03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md`: Phase 3 is catalog reconciliation and Phase 4 is historical sales backfill.

This is an operational checkpoint, not a replacement for the canonical specification or `procurement/config/rules.toml`. Current facts below were checked independently and read-only. Historical facts are labeled separately so future sessions do not mistake an earlier checkpoint for current database state.

## Verified current state

### Repository and tests

- Branch: `main`.
- Inspected base HEAD before the durable-memory commit: `0cfe034979993de580890894f86c697c00f96f19` (`memory update`).
- Worktree was clean before the initial durable-memory documentation task. The intended portability commit adds `AGENTS.md`, `CLAUDE.md`, `docs/CODEX_HANDOFF.md`, and the narrow `.gitignore` entries described below.
- Unexpected `.agents/skills/` and `skills-lock.json` were traced to optional Replit-targeted installs by the third-party `skills` CLI. They are not application dependencies; they were preserved locally, ignored, and excluded from the durable-memory commit.
- Current test command: `cd procurement && PYTHONPATH=src python3 -m unittest discover -s tests -v`.
- Current test result after the portability documentation changes: **89/89 PASS**, with 0 failures, errors, or skips (run on 2026-08-10; unittest time 0.643 seconds). No warnings were emitted.

### Database and catalog

Read-only PostgreSQL inspection was performed on 2026-08-10. No database or Shopify data was changed.

- The latest catalog sync completed with full pagination at `2026-08-10T14:55:53.634178Z`.
- It fetched and independently verified **1,999 ACTIVE Shopify variants**. The stored Shopify-reported count is 2,003 because that store-wide count includes four inactive variants and must not be treated as the filtered ACTIVE count.
- Latest sync results: 1,999 live rows, 1,999 exact current IDs, 0 new, 0 missing, 0 potential recreations, and 0 unresolved blockers.
- Current `variants` state: 2,049 total = 1,999 `LIVE`/active + 46 `RETIRED_CONFIRMED`/inactive + 4 historical inactive-as-expected (`ARCHIVED` in Shopify; `SEEDED`/inactive locally).
- The latest reconciliation contains 50 nonblocking `INACTIVE` rows: the 46 confirmed retirements plus the four expected inactive identities.
- All 46 retirement decisions are persisted with 46 individual audit rows dated `2026-08-10T14:52:08Z`. No recreation aliases were created for them.
- `CATALOG_SYNC` is **currently PASS** because the fresh post-retirement sync produced zero blockers. Future sessions must still read the actual database state; this file is not authority to assume that the gate remains PASS.

### Current global readiness gates

| Gate | Status | Blocks PO when failing | Last checked (UTC) |
| --- | --- | --- | --- |
| `CATALOG_SYNC` | `PASS` | Yes | 2026-08-10 14:55:53 |
| `SALES_BACKFILL` | `FAIL` | Yes | 2026-08-10 01:25:59 |
| `VENDOR_RULES` | `FAIL` | Yes | 2026-08-10 01:22:46 |
| `INVENTORY_HISTORY` | `WARN` | No | 2026-08-10 01:22:46 |
| `MAPPING_INTEGRITY` | `WARN` | No | 2026-08-10 01:22:46 |
| `OPEN_PO_RECONCILIATION` | `WARN` | No | 2026-08-10 01:22:46 |
| `PRICE_COVERAGE` | `WARN` | No | 2026-08-10 01:22:46 |

- Runtime PO readiness is **disabled**, blocked by `SALES_BACKFILL` and `VENDOR_RULES`; the database contains zero purchase orders.
- There are no scoped readiness-gate rows. Three scoped open exceptions remain (two HIGH and one MEDIUM); none is global.
- `SALES_BACKFILL` remains **FAIL**. There are zero sales-backfill runs, zero raw Shopify sales rows, and zero canonical `sales_daily` rows.
- Phase 4 historical sales backfill has not begun and is **not authorized**.
- Shopify remains read-only.

## Verified historical checkpoint and transition

- Phase 0 and Phases 1–2 are complete. Repository history records the Phase 0 import/baseline and the production schema plus seed import; the latest database seed-import audit is `PASS`.
- Historical seed: **2,029** identities.
- The pre-retirement Phase 3 reconciliation had **1,979** exact active historical/current IDs, **20** genuinely new active variants, **46** missing historical identities, and **4** historical inactive-as-expected identities, against the independently verified 1,999 ACTIVE Shopify variants.
- All 46 missing historical IDs were investigated through exact Shopify lookup and a full deterministic continuity sweep. The stored result for all 46 is deleted/not resolvable with no credible current counterpart.
- Human retirement authorization was submitted. The current database evidence above proves that the 46 authorized retirements were subsequently executed and audited.
- Phase 3 catalog reconciliation was therefore completed through human retirement review. The recorded pre-retirement suite checkpoint was **89/89**, and the fresh current suite is also **89/89**.

## Authorization boundary / next action

The next task in the supplied pre-validation checkpoint was **Phase 3 post-retirement validation only**. Current database evidence shows that this validation already ran successfully and set `CATALOG_SYNC` to `PASS` from actual state.

Stop at this phase boundary. Do not rerun identity decisions, begin Phase 4, write to Shopify, enable PO generation, or treat `CATALOG_SYNC = PASS` as authorization for later work. Wait for explicit owner authorization.
