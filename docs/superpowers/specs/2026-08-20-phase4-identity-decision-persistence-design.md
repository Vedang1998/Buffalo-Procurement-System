# Phase 4 Controlled Identity-Decision Persistence Design

**Date:** 2026-08-20  
**Status:** Owner-approved design  
**Risk:** Level 3 — historical identity affects forecasting and procurement  
**Starting main:** `60db3e5a893856df5b95a05f0ec75b3ec7e84f22`  
**Approved manifest SHA-256:** `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`

## 1. Authorization boundary

This task may implement, test, dry-run, and then persist the 343 already-reviewed
Phase 4 identity decisions to the production PostgreSQL database. The owner has
explicitly authorized the production persistence before independent implementation
review as a one-time sequencing exception for this task only.

The task must not:

- rebuild or re-resolve historical sales;
- evaluate or change `SALES_BACKFILL` or any readiness gate;
- write to or query Shopify;
- begin Vendor Rules, forecasting, procurement, or later-phase work;
- create or release a purchase order;
- change forecasting or procurement behavior.

The implementation and production result still require independent adversarial
review before any rebuild, re-resolution, or gate evaluation is authorized.

## 2. Chosen architecture

Use the existing `historical_sales_review_decisions` table as the exact
`source_identity_key` decision authority. Do not introduce a parallel decision
table. No schema migration is expected because the current ledger already stores
the run, source key, action, canonical target, actor, reason, timestamp, evidence,
and supersession link.

Add a focused manifest application module and a narrow command-line entry point.
The module owns parsing, deterministic validation, database preflight, dry-run,
transactional persistence, and readback reconciliation. The command-line entry
point owns operator inputs, repository SHA discovery, review-token handling, and
deterministic JSON output. It must not contain ad-hoc business SQL.

The approved manifest is retained byte-for-byte in the repository so tests,
review, execution, and audit all refer to the same immutable artifact and SHA.

Rejected alternatives:

1. A new source-key mapping table would duplicate the existing review ledger and
   create competing decision authority.
2. A `skip_rebuild` flag on the existing per-row service would leave title-only
   MAP decisions unrepresentable and mix batch and rebuild responsibilities.
3. External raw SQL would bypass application validation and audit contracts.

## 3. Manifest contract

The parser accepts the approved CSV columns exactly and rejects missing, extra,
or duplicate headers. It preserves the exact source key and canonical target as
strings. Numeric controls use `Decimal`, never binary floating point.

Before any write, both dry-run and apply re-prove:

- SHA-256 equals the approved value;
- 343 rows and 343 unique source keys;
- 341 material and 2 nonmaterial rows;
- 3,112 affected raw rows;
- 55 MAP, 8 EXCLUDE, 280 LEAVE_UNRESOLVED, and 0 unknown actions;
- absolute magnitudes of 3,696.0000 units and $72,616.29;
- all 55 MAP rows have targets and all 288 non-MAP rows do not;
- 51 distinct MAP targets, all present in `variants`;
- the exclusion set equals the eight owner-approved source keys exactly;
- the three NUTRL Fruit keys map to `41716813627467` and no other key receives
  the pack-size exception;
- no row maps to Fiesta target `41193000796235`;
- all three High Noon Tequila Variety keys remain LEAVE_UNRESOLVED;
- the reviewable database run is exactly
  `d389079c-eabf-49b5-a245-40a207025fd7`;
- the live review queue key set equals the manifest key set exactly;
- live affected-row, materiality, and magnitude controls equal the manifest;
- no existing approved alias, exclusion, rejection, or latest decision conflicts
  with the manifest.

Any mismatch stops before the first data-changing statement.

## 4. Decision semantics

Manifest dispositions map to current database action values as follows:

| Manifest | Operator/report action | Stored `decision_action` |
| --- | --- | --- |
| `MAP` | `MAP_TO_CANONICAL` | `MAP` |
| `EXCLUDE` | `EXCLUDE_HISTORICAL_ITEM` | `EXCLUDE` |
| `LEAVE_UNRESOLVED` | `LEAVE_UNRESOLVED` | `LEAVE_UNRESOLVED` |

Every source key receives an effective durable ledger entry. Evidence records at
least the manifest SHA, manifest row number, evidence basis, review note,
execution Git SHA, run ID, source fields, and owner authorization scope.

An existing latest decision is idempotently accepted only when its run, action,
target, and manifest SHA match exactly. Any other pre-existing latest decision is
a hard conflict. The service inserts no duplicate decision for an identical rerun.

## 5. Source-key resolution and aliases

Extend the historical identity index with exact approved source-key mappings
loaded from the latest effective review decisions:

- latest MAP resolves only that exact source key to its approved canonical ID;
- latest EXCLUDE remains backed by `historical_sales_exclusions` and resolves
  only that exact source key as excluded;
- LEAVE_UNRESOLVED remains unresolved and does not become an implicit mapping;
- source-key decisions do not weaken current-ID, alias, SKU, size, pack, or fuzzy
  matching rules for any other source identity.

This exact-key layer supports the 34 approved title-only MAP rows without
inventing title-only auto-matching. It is approved human decision evidence, not a
new automatic matcher.

For a nonzero historical Variant ID, alias evidence may be created only when the
complete live review family for that old ID appears in the manifest and every
member maps to one identical target. The exact source-key ledger entries remain
the primary audit record. Zero/null IDs never create a broad old-ID alias. An
existing alias to another target aborts the entire transaction.

The NUTRL Fruit mapping remains one enumerated owner exception and must not alter
general size or pack matching behavior.

## 6. Review authorization

The batch path uses constant-time comparison against the configured
`RECONCILIATION_REVIEW_TOKEN`. The caller supplies the token through a separate,
task-specific ephemeral environment input; no token appears in command arguments,
output, logs, fixtures, Git, evidence, or error messages.

Missing configuration, missing supplied authorization, or a mismatch fails before
database access for apply. Tests use synthetic values supplied through test-only
environment patching and never use the production secret.

## 7. Dry-run

Dry-run opens a database-enforced read-only transaction, performs all manifest and
database checks, compares the current latest decision state, and emits stable JSON
controls. It performs no DML and confirms no transaction ID was assigned.

Dry-run and apply share the same validation functions. Apply repeats every
preflight check inside its write transaction so a successful earlier dry-run
cannot mask later drift.

## 8. Transactional persistence

Apply requires a clean committed worktree. The CLI records the exact executing Git
SHA in every decision's audit evidence.

Persistence uses one PostgreSQL transaction for all 343 source keys:

1. acquire the existing Phase 4 transaction advisory lock;
2. revalidate the manifest SHA and all static controls;
3. revalidate the exact reviewable run, live queue, targets, exclusions, aliases,
   and existing decisions;
4. capture protected-state fingerprints;
5. insert all missing review decisions in deterministic source-key order;
6. upsert exactly the eight active historical exclusions;
7. insert only safe, uniform old-ID alias evidence;
8. insert corresponding append-only `change_log` evidence;
9. read back the effective decisions and decision-side artifacts;
10. recalculate protected-state fingerprints and require exact equality;
11. commit only if every assertion passes.

Any exception rolls back the complete transaction. There are no batches or
progress markers because 343 rows are safely bounded and one transaction provides
the strongest no-partial-completion guarantee.

The persistence path must not call `_re_resolve_run_facts`,
`_finalize_sales_backfill_unlocked`, `finalize_sales_backfill`,
`rerun_sales_identity_resolution`, `evaluate_sales_readiness`, or any gate setter.

## 9. Protected-state fingerprints

Deterministic before/after fingerprints cover:

- `sales_daily` contents;
- raw canonical IDs, resolution statuses, methods, and evidence;
- `sales_backfill_runs` resolution/control state;
- every `readiness_gates` row;
- `purchase_orders` and `purchase_order_lines` contents and counts.

Decision tables, the eight authorized exclusion rows, approved alias evidence,
and their `change_log` rows are intentionally outside the protected fingerprint.

Fingerprint equality is asserted inside the transaction before commit. A fresh
read-only connection repeats the fingerprints and required readback controls after
commit. `SALES_BACKFILL` must remain FAIL.

## 10. Deterministic readback

Required post-write effective controls are:

- reviewed source keys: 343/343;
- MAP_TO_CANONICAL: 55;
- EXCLUDE_HISTORICAL_ITEM: 8;
- LEAVE_UNRESOLVED: 280;
- MAP targets populated: 55/55;
- distinct MAP targets: 51;
- missing canonical MAP targets: 0;
- exact manifest exclusions: 8/8 with no additional manifest exclusion;
- NUTRL Fruit: 3/3 to `41716813627467`;
- Fiesta mappings: 0;
- High Noon Tequila Variety: 3/3 LEAVE_UNRESOLVED;
- conflicting duplicate effective decisions: 0;
- manifest SHA present in all 343 effective decision evidence records.

The report also records before/after gate statuses, `SALES_BACKFILL` FAIL, PO
counts, and protected fingerprints.

## 11. Test strategy

Add pure parser/control tests and disposable PostgreSQL integration tests covering
all twenty required cases:

1. exact 343-row contract;
2. SHA mismatch;
3. duplicate key;
4. unknown database key;
5. missing database key;
6. unknown MAP target;
7. target on non-MAP;
8. exclusion drift;
9. Fiesta mapping;
10. NUTRL 3/3 exception;
11. identical existing decision idempotency;
12. conflicting existing decision rollback;
13. title-only MAP persistence and later exact-key resolution;
14. repeated old ID across multiple source keys;
15. explicit LEAVE_UNRESOLVED persistence;
16. missing and invalid review authorization;
17. no rebuild call path;
18. no gate mutation;
19. injected mid-transaction failure rollback;
20. deterministic readback and protected fingerprint equality.

Register any new test module in the fail-closed test runner and run targeted tests
before `./scripts/procurement-tests`.

## 12. Execution and handoff sequence

1. Commit the implementation and tests before production execution.
2. Run targeted and complete deterministic suites against disposable PostgreSQL.
3. Run the production dry-run and verify every expected control.
4. Stop immediately on any mismatch.
5. Run the one-transaction production persistence.
6. Perform fresh read-only post-write reconciliation.
7. Update `docs/CODEX_HANDOFF.md` with exact evidence.
8. Update `procurement/docs/PHASE_STATUS.md` to “owner decisions persisted;
   rebuild pending” without marking Phase 4 complete.
9. Commit documentation, push the dedicated branch, and open one draft PR.
10. Stop for independent adversarial review of the implementation and production
    result.

The next authorization boundary is exactly:

> Independent review of persistence implementation/results, then separately
> authorize Phase 4 historical-sales re-resolution/rebuild and gate reevaluation.
