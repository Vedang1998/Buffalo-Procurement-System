# Phase 4 Historical-Sales Re-resolution/Rebuild Design

**Date:** 2026-08-20
**Status:** Planning complete; implementation and execution not authorized
**Risk:** HIGH — historical attribution feeds forecasting and procurement
**Starting main:** `97fe3868fa87d17d1a8f236d993c35cd8db83805`
**Production run:** `d389079c-eabf-49b5-a245-40a207025fd7`
**Decision manifest SHA-256:** `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`

## 1. Objective and authorization boundary

Define a deterministic, fail-closed execution contract that will, only after a
separate owner authorization:

1. load the already-persisted 343 owner decisions through the existing Phase 4
   identity index;
2. re-resolve the immutable raw facts belonging to the existing production run;
3. rebuild the canonical `sales_daily` aggregate for that run's date range;
4. reconcile every source, resolution, aggregate, gate, and PO control; and
5. evaluate the resulting evidence with the current pure
   `evaluate_sales_readiness()` function without writing a readiness gate.

This design/planning checkpoint permits only read-only production analysis and
the two planning documents. It does not authorize runtime code or test changes,
production DML, re-resolution, aggregate rebuild, gate evaluation/write,
Shopify access, Vendor Rules, forecasting/procurement work, or PO action.

The approved decision population is immutable: 343 exact source keys, 55 MAP,
8 EXCLUDE, 280 LEAVE_UNRESOLVED, 51 distinct MAP targets, the exact eight
exclusions, and 17 approved uniform old-ID alias families. The future rebuild
must not invent or persist a mapping, exclusion, alias, or review decision.

The controlling authority for this design is
`procurement/docs/authority/01_CANONICAL_SYSTEM_SPEC_v2_1.md`,
`procurement/docs/CURRENT_AUTHORITY.md`, its designated
`procurement/docs/MASTER_PLAN_v2_0.md`, `procurement/config/rules.toml`,
`docs/PROJECT_GOVERNANCE.md`, `docs/CODEX_HANDOFF.md`, and
`procurement/docs/PHASE_STATUS.md`, together with the current resolver,
finalization, and readiness implementation.

## 2. Canonical readiness outcome: Outcome A

`LEAVE_UNRESOLVED` means the owner reviewed the identity and deliberately left
it unresolved. It is a durable disposition and audit result; it is not an
accepted-resolution state and it is not an exclusion.

This follows both canonical authority and current implementation:

- the canonical Phase 4 readiness rule requires every material historical row
  to be resolved or explicitly excluded;
- `load_identity_index()` loads only effective MAP rows as source-key maps;
- the resolver therefore leaves LEAVE_UNRESOLVED rows unresolved; and
- `evaluate_sales_readiness()` adds
  `MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED` whenever unresolved/ambiguous
  absolute units or sales is nonzero.

All 280 LEAVE_UNRESOLVED groups are material. They represent 2,100 of the
original 3,112 unresolved raw rows (67.48%), 2,551.0000 absolute units, and
$53,287.92 absolute sales. The expected evaluator outcome is therefore:

```text
SALES_BACKFILL = FAIL
blockers = [MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED]
```

No implementation may reinterpret LEAVE_UNRESOLVED or relax the evaluator to
obtain a passing gate.

## 3. Existing implementation and chosen approach

The current resolver is the sole historical identity authority:

1. exact active exclusion;
2. exact approved source-key MAP;
3. exact active or preserved historical Variant ID;
4. approved historical alias;
5. conservative exact identity evidence; then
6. unresolved or ambiguous.

The current `_re_resolve_run_facts()` already constructs the canonical
`SalesSourceRow`, calls `HistoricalIdentityIndex.resolve()`, and persists the
standard resolution values. The current finalizer also has mature source,
coverage, accounting, aggregate, and run-control logic. Those paths must be
reused rather than reimplemented.

The current public `finalize_sales_backfill()` is not safe to call directly for
this controlled milestone because it couples re-resolution, aggregate rebuild,
run-state mutation, readiness evaluation, and a `SALES_BACKFILL` gate write in
one transaction. It also rewrites the aggregate and advances checkpoint state
on an identical rerun, so it does not meet the required true no-op contract.

The chosen approach is a narrow controlled-rebuild orchestrator around a
gate-free extraction of the existing finalization core:

- retain `HistoricalIdentityIndex`, `load_identity_index()`,
  `_resolution_values()`, source facts, coverage logic, accounting logic, and
  aggregate SQL as the only resolver/rebuild architecture;
- extract only enough internal structure from `historical_sales.py` to run the
  existing re-resolution/rebuild logic without evaluating or writing a gate;
- preserve `finalize_sales_backfill()` behavior for existing callers;
- add deterministic preflight, state classification, protected fingerprints,
  readback, and a narrow dry-run/apply command; and
- call the pure evaluator only after a committed rebuild, from a fresh
  read-only connection, with no gate setter in that execution path.

Rejected alternatives are direct use of the gate-coupled finalizer, a second
resolver or aggregate implementation, external SQL, and any policy exception
that treats LEAVE_UNRESOLVED as ready.

## 4. Read-only production baseline and disposition controls

Planning analysis used a database-enforced read-only transaction. Both
`txid_current_if_assigned()` checks returned null; no production transaction ID
was assigned and no DML occurred.

| Disposition | Source keys | Raw rows | Net units | Absolute units | Net sales | Absolute sales |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MAP | 55 | 823 | 1,101.0000 | 1,101.0000 | $19,158.46 | $19,158.46 |
| EXCLUDE | 8 | 189 | 44.0000 | 44.0000 | $145.93 | $169.91 |
| LEAVE_UNRESOLVED | 280 | 2,100 | 2,541.0000 | 2,551.0000 | $50,297.92 | $53,287.92 |
| **Total** | **343** | **3,112** | **3,686.0000** | **3,696.0000** | **$69,602.31** | **$72,616.29** |

Materiality is 341 material and 2 nonmaterial groups. MAP is 55 material;
EXCLUDE is 6 material and 2 nonmaterial; LEAVE_UNRESOLVED is 280 material.

The immutable source/run population is:

| Control | Exact value |
| --- | ---: |
| Run facts / unique source facts | 59,083 |
| Net source units | 82,501.0000 |
| Absolute source units | 82,545.0000 |
| Net source sales | $1,300,975.14 |
| Absolute source sales | $1,304,920.80 |
| Duplicate observations | 0 |
| Source-key recomputation mismatches | 0 |

Current raw status is 55,971 RESOLVED, 3,112 UNRESOLVED, 0 AMBIGUOUS,
and 0 EXCLUDED. The current canonical aggregate has 55,966 rows,
78,815.0000 net units, and $1,231,372.83 net sales.

## 5. Exact expected post-rebuild controls

### 5.1 Raw status and accounting

| Status | Raw rows | Net units | Absolute units | Net sales | Absolute sales |
| --- | ---: | ---: | ---: | ---: | ---: |
| RESOLVED | 56,794 | 79,916.0000 | 79,950.0000 | $1,250,531.29 | $1,251,462.97 |
| EXCLUDED | 189 | 44.0000 | 44.0000 | $145.93 | $169.91 |
| UNRESOLVED | 2,100 | 2,541.0000 | 2,551.0000 | $50,297.92 | $53,287.92 |
| AMBIGUOUS | 0 | 0.0000 | 0.0000 | $0.00 | $0.00 |
| **Total** | **59,083** | **82,501.0000** | **82,545.0000** | **$1,300,975.14** | **$1,304,920.80** |

The expected resolution-method counts are:

| Resolution method | Raw rows |
| --- | ---: |
| `EXACT_ACTIVE_VARIANT_ID` | 36,397 |
| `APPROVED_VARIANT_ID_ALIAS` | 19,430 |
| `APPROVED_HISTORICAL_IDENTITY` | 136 |
| `EXACT_PRESERVED_HISTORICAL_VARIANT_ID` | 8 |
| `APPROVED_SOURCE_IDENTITY_DECISION` | 823 |
| `EXPLICIT_EXCLUSION` | 189 |
| `NONE` | 2,089 |
| `SKU_EVIDENCE_ONLY` | 11 |
| **Total** | **59,083** |

The rebuilt run remains a completed, fully covered source acquisition with
reconciled source/resolution/aggregate controls. Its readiness result is
separate: a technically successful rebuild does not make the unresolved
population ready.

### 5.2 Canonical aggregate

After grouping only RESOLVED facts by sale date and canonical Variant ID, the
expected `SHOPIFYQL_SALES` aggregate for the run range is:

- `sales_daily` rows: 56,789;
- canonical net units: 79,916.0000;
- canonical net sales: $1,250,531.29.

The exact source totals above remain immutable. Resolution accounting must prove
resolved + excluded + unresolved + ambiguous equals source totals for both
units and sales. The aggregate must independently equal the RESOLVED net units
and net sales.

### 5.3 MAP target flows

| Canonical Variant ID | Source keys | Raw rows |
| --- | ---: | ---: |
| 41154951053387 | 1 | 7 |
| 41154955739211 | 1 | 8 |
| 41154956165195 | 1 | 27 |
| 41156901339211 | 1 | 5 |
| 41156901535819 | 1 | 11 |
| 41156902420555 | 1 | 23 |
| 41157201657931 | 1 | 8 |
| 41157202608203 | 1 | 6 |
| 41157208539211 | 1 | 35 |
| 41157731156043 | 1 | 8 |
| 41172569292875 | 1 | 3 |
| 41191231848523 | 1 | 11 |
| 41224455684171 | 1 | 6 |
| 41237974417483 | 1 | 1 |
| 41239557406795 | 1 | 1 |
| 41318429556811 | 1 | 3 |
| 41318429589579 | 1 | 9 |
| 41318440337483 | 1 | 27 |
| 41318441189451 | 1 | 2 |
| 41318441222219 | 1 | 1 |
| 41318441254987 | 1 | 1 |
| 41318441287755 | 1 | 4 |
| 41339109474379 | 1 | 2 |
| 41355677827147 | 1 | 12 |
| 41367659839563 | 1 | 1 |
| 41395427180619 | 1 | 3 |
| 41446027362379 | 1 | 2 |
| 41477332205643 | 1 | 1 |
| 41679364653131 | 1 | 6 |
| 41687552589899 | 1 | 11 |
| 41716809072715 | 1 | 42 |
| 41716809105483 | 1 | 23 |
| 41716809203787 | 1 | 42 |
| 41716809236555 | 1 | 35 |
| 41716809269323 | 1 | 19 |
| 41716809302091 | 1 | 62 |
| 41716809465931 | 1 | 33 |
| 41716809498699 | 1 | 49 |
| 41716809597003 | 1 | 47 |
| 41716810481739 | 1 | 31 |
| 41716813561931 | 1 | 14 |
| 41716813627467 | 3 | 24 |
| 41716813660235 | 2 | 8 |
| 41716813693003 | 2 | 16 |
| 41716813791307 | 1 | 27 |
| 41716813856843 | 1 | 50 |
| 41716813987915 | 1 | 13 |
| 41773486342219 | 1 | 6 |
| 41889590050891 | 1 | 5 |
| 42671537684555 | 1 | 27 |
| 42913592377419 | 1 | 5 |
| **Total** | **55** | **823** |

NUTRL Fruit is exactly 3/3 reviewed source keys to `41716813627467`, covering
24 raw facts. No manifest MAP targets Fiesta Variant ID `41193000796235`.
High Noon Tequila Variety is exactly 3/3 LEAVE_UNRESOLVED source keys, covering
41 raw facts.

### 5.4 Exact exclusions

The only approved exclusion keys are:

```text
0||BUFFALO HOUSE GIFT CARD|BUFFALO HOUSE GIFT CARD
0||DELIVERY FEE|
0||SHIPPING FEES|
0||TIP|
41173357133899||BUFFALO HOUSE GIFT CARD|10.00
||BUFFALO HOUSE GIFT CARD|BUFFALO HOUSE GIFT CARD
||TIP|
|||
```

The post-rebuild count must remain eight exact active exclusions with no added,
missing, deactivated, or changed row.

## 6. Deterministic execution contract

### 6.1 Preflight and state classification

Dry-run and apply share one preflight implementation. Dry-run uses a
database-enforced read-only transaction and proves no XID assignment. Apply
repeats every check after acquiring the existing transaction advisory lock and
locking the run row.

Required preflight includes:

- clean worktree and exact committed implementation SHA;
- the existing reconciliation review authorization supplied without logging or
  persisting its secret value;
- exact production run and exact manifest SHA;
- 343 current-provenance effective decisions with exact 55/8/280 actions and
  51 targets;
- exact 17 approved old-ID alias families and exact eight exclusions;
- exact immutable source/run controls and recomputed source keys;
- every expected target still exists and no decision/target conflict exists;
- current status is either the exact documented pre-rebuild state or the exact
  documented post-rebuild state;
- protected readiness-gate and PO fingerprints match the approved baseline;
- no facts exist outside the run that would be affected by the target-range
  aggregate replacement.

Classify the controlled state as:

- `NEEDS_REBUILD`: exact baseline state and all expected resolver outcomes;
- `CURRENT_REBUILD_STATE`: exact post-rebuild state, aggregate, and evidence;
  return a true no-op with no DML or timestamp change;
- `CONFLICT`: any partial, unexpected, mixed, added, missing, or changed state;
  hard stop before DML.

### 6.2 One-transaction apply

Only `NEEDS_REBUILD` may enter the write transaction:

1. acquire the existing Phase 4 transaction advisory lock;
2. use `SERIALIZABLE` isolation and lock the exact run row `FOR UPDATE`;
3. repeat static, database, decision, target, exclusion, alias, source, gate,
   and PO preflight;
4. capture protected fingerprints;
5. load the existing identity index once;
6. compute and assert all 59,083 expected resolutions before mutation;
7. update resolution metadata only where it differs (expected: 823 MAP and
   189 EXCLUDE facts; 1,012 total);
8. rebuild only the run-range `SHOPIFYQL_SALES` aggregate using the existing
   grouped RESOLVED-fact query;
9. update the existing run's resolution/control evidence and counts without
   changing source facts or decision evidence;
10. reconcile every exact post-rebuild control and protected fingerprint; and
11. commit only if all assertions succeed.

The transaction must not call a gate setter. It must not mutate source payload
fields, `sales_backfill_run_facts`, review decisions, exclusions, aliases,
unrelated `sales_daily` rows, readiness gates, POs, or PO lines.

### 6.3 Post-commit reconciliation and idempotence

A fresh read-only connection must re-prove the complete post-rebuild state,
source invariants, decisions, aliases, exclusions, protected fingerprints, and
PO counts. It must then run the pure evaluator on fresh evidence and obtain the
single expected blocker without updating `readiness_gates`.

A second identical rebuild invocation must classify
`CURRENT_REBUILD_STATE`, plan zero row updates/rebuilds, assign no transaction
ID in dry-run, and leave all data and timestamps unchanged. This is the required
idempotence proof; delete/reinsert of an already-current aggregate is not a
no-op.

## 7. Protected state and readiness gates

Pre/post deterministic fingerprints cover:

- immutable raw source fields and `sales_backfill_run_facts` membership;
- the 343-row effective owner-decision state and full ledger history;
- exclusions, aliases, variants, and all out-of-run/out-of-range sales data;
- every readiness-gate row including evidence and timestamps; and
- `purchase_orders` and `purchase_order_lines` contents and counts.

Production currently has 0 purchase orders and 0 PO lines; both must remain
zero. All stored gates must remain unchanged byte-for-byte during rebuild:

| Gate | Current status | Blocks PO |
| --- | --- | --- |
| `CATALOG_SYNC` | PASS | yes |
| `SALES_BACKFILL` | FAIL | yes |
| `VENDOR_RULES` | FAIL | yes |
| `INVENTORY_HISTORY` | WARN | no |
| `MAPPING_INTEGRITY` | WARN | no |
| `OPEN_PO_RECONCILIATION` | WARN | no |
| `PRICE_COVERAGE` | WARN | no |

Pure evaluation is evidence, not a readiness-gate reevaluation/write. A later
gate-write operation needs separate owner authorization even though its expected
result is still FAIL.

## 8. Fail-closed rollback criteria

Stop before DML, or roll back the whole transaction, on any discrepancy in:

- Git SHA/worktree, manifest SHA, run identity, or decision provenance;
- 343/55/8/280/51/17 counts or exact exclusion membership;
- source rows, source keys, quantities, sales, coverage, page structure, or
  duplicate-observation controls;
- predicted resolver output, MAP target flow, status/method totals, or special
  NUTRL/Fiesta/High Noon controls;
- canonical aggregate counts/totals;
- active variants, aliases, decision ledger, exclusions, gates, or PO state;
- transaction isolation, advisory lock, row lock, reconciliation, or protected
  fingerprints; or
- any attempted mutation outside the explicitly approved rebuild targets.

After a post-commit mismatch, stop and perform no corrective DML. Preserve the
evidence for independent review and require new owner direction.

## 9. Validation, review, and Definition of Done

Before any production execution, implementation must be committed, tested with
pure/unit tests and disposable PostgreSQL, pass the complete registered suite,
and receive independent adversarial review. Production dry-run must then match
every control exactly before a separately authorized apply.

The rebuild milestone Definition of Done requires:

- exact committed implementation and clean execution worktree;
- deterministic dry-run and transaction rollback tests;
- exact post-rebuild raw, method, aggregate, special-case, and invariant totals;
- zero unauthorized mutations and unchanged protected fingerprints;
- fresh readback and true second-run no-op proof;
- current evaluator result FAIL with only
  `MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED`;
- independent adversarial review of implementation and production evidence;
- handoff/status documentation that says rebuild performed but Phase 4 blocked,
  never Phase 4 complete; and
- separate owner acceptance before any gate write or later-phase work.

## 10. Owner decision and next authorization boundary

Under current authority the approved manifest cannot close Phase 4: 280 material
identities remain unresolved. The business owner must eventually issue a new,
separately reviewed decision artifact that revisits those exact keys and assigns
each a verified MAP or a justified EXCLUDE where evidence permits.

A blanket accepted-unresolved exception or readiness-evaluator change would be
a canonical policy change requiring explicit owner change control and an updated
independent review; it is not part of this design.

The next authorization boundary is exactly:

> Authorize implementation and disposable-database validation of this controlled
> rebuild design. Production dry-run, production rebuild, readiness-gate write,
> Shopify, Vendor Rules, forecasting/procurement, and PO actions remain separately
> unauthorized.
