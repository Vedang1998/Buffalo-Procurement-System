# Buffalo Procurement OS — Overnight Monday MVP Progress

Updated: 2026-09-07 01:31 EDT

This is an operational engineering log for the authorized offline emergency workstream. It does not change canonical phase authority or authorize production activity.

## Execution boundary

- Isolated worktree: `/home/runner/workspace/.ai-auth/codex/worktrees/emergency-monday-procurement-mvp`
- Branch: `codex/emergency-monday-procurement-mvp`
- Starting emergency head: `7068f54fe2fb8b54397888aadba6990d3644b19a`
- Starting emergency tree: `d00efd71a3f7b2e1ad55f2258767d6765122c88b`
- Upstream at setup: exact same head; no unpushed commits.
- Accepted `main` recorded by the execution contract: `f308ac666a2377f540e528bc873463daecc20cf8`.
- The emergency history intentionally remains based on `1920a16a6dc13a1b4357315f5049b938cbe7c0e2`; current `main` and G10 will not be merged or cherry-picked overnight.
- Frozen G10 worktree remains separate at `86da9669a9f83f81fa3a49c59cf62e9fc1a7a3b6`, tree `045395d55046fa78013b4a79d64e15139ad1f933`.
- Codex is the sole writer. Parallel agents are read-only auditors.

## Setup checkpoint

- The owner-supplied execution contract is saved as `docs/OVERNIGHT_MONDAY_RUN.md`.
- The worktree was clean before that contract file was saved; no unrelated local changes were found.
- Local disposable database safety was proven with PostgreSQL 16.9 on `127.0.0.1`, database `overnight_setup_test`.
- The shared validator confirmed the database name ends in `_test`, server major version 16, and `current_database()` before fixture DDL.
- `txid_current_if_assigned()` was `NULL` before fixture DDL; setup fixture DDL count was zero.
- Ambient `DATABASE_URL` and libpq redirect variables were scrubbed without reading or displaying their values. There was no fallback to `DATABASE_URL`.
- The disposable cluster was stopped and removed after the proof.
- Production DB connections/writes: `0 / 0`.
- Shopify calls/writes: `0 / 0`.
- Production PO actions: `0`.

## Existing packet inventory

| Packet | Commit | Evidence status at overnight start |
|---|---|---|
| Design | `7c24419` | Approved emergency design present. |
| Packet 0 — PostgreSQL test isolation | `d0834a8` | Implemented; exact-current audit and tests pending. |
| Packet 1 — inventory snapshots | `955d468` | Implemented; exact-current audit and tests pending. |
| Packet 2 — vendor rules | `3c81704` | Implemented; exact-current audit and tests pending. |
| Packet 3 — PO ledger | `7068f54` | Implemented; exact-current high-risk audit and tests pending. |
| Packet 4 onward | — | Not yet implemented on this branch. |

Code presence is not acceptance. Packets 0–3 remain under audit until their focused tests, affected suites, and control invariants pass on the exact candidate.

## Current work

1. Reconcile read-only gap audits for Packets 0–3.
2. Run focused disposable-PostgreSQL validation and repair concrete defects.
3. Continue with the universal price-book staging packet, then the minimum end-to-end Monday workflow in the approved priority order.

Feature work freezes at 06:30 America/New_York for integration, full validation, gap reporting, and handoff. If a packet cannot be completed coherently before that boundary, it will remain explicitly deferred rather than being left half-implemented.

## Packet 0–3 remediation checkpoint

- The first exact-current focused run discovered 81 tests: 77 passed and four Packet 3 trusted-incoming assertions failed. The cause was a split clock: reconciliation wrote database `now()` but evaluated the caller's frozen `as_of` timestamp.
- Reconciliation now uses one database-owned evidence timestamp for both the durable ledger write and trust evaluation; caller `as_of` is only a narrow clock-consistency assertion. SQL requires the evidence to follow FINAL/import events and to be near the database clock. Trusted open incoming requires nonblank JSON-string source and reference evidence in both Python and the database view.
- DRAFT PO replay now rejects a changed expected receipt and rejects timezone-naive receipt timestamps.
- Inventory readiness now blocks on stale snapshots and unknown incoming quantities. Capture timestamps must equal the `America/New_York` business date, and default status evaluation uses that store timezone rather than the host UTC date.
- Completed inventory run evidence and inventory run rows are database-protected; vendor-rule revisions are append-only.
- Vendor cycle/lead times reject booleans and fractional days; CASE minimums require whole case counts in both service validation and the database.
- The affected focused suite then passed `85 / 85`, with failures/errors/skips/expected failures/unexpected successes all `0`.
- Exact registered branch floor after these added tests: `452`.
- Two independent read-only re-audits returned `APPROVE` with no remaining P0/P1 blocker in this bounded remediation. The full authoritative suite has not yet run on this candidate.

## Packet 4 — universal FUTURE price-book staging checkpoint

- Added a strict normalized CSV template, bounded upload, raw content-addressed evidence, deterministic validation results, explicit authenticated promote/reject actions, and read-only batch/exception screens.
- Uploads are FUTURE-only. There is no Packet-4 rollover table, status, API, or executable CURRENT transition; the legacy rollover entrypoint now fails closed.
- Promotion re-reads and hashes the raw object, reparses it, revalidates the locked database state, requires exact mapping/pack/assortment/coverage evidence, records warning acknowledgement, and replaces the vendor's complete FUTURE set in one serializable transaction.
- Effective month outranks upload generation: an older effective book cannot replace a newer one. Same-month corrections bind the exact active predecessor batch and supersede/purge prior typed economics transactionally.
- SQL guards independently enforce complete eligible-offer coverage, one BASE per offer, BT/CS ladder monotonicity, case/unit arithmetic, mapping eligibility, offer/vendor stability, exact event-to-batch claims, and append-only audit evidence.
- Existing verified source-null CURRENT rows are grandfathered but immutable. Fresh-install/idempotent August seed writes require the exact reviewed six-file seed manifest, every file SHA-256/count, the exact 85-offer/271-price controls, a transaction-scoped append-only seed event, and a deferred semantic digest over vendor/variant eligibility, offer mapping/pack/trust fields, and price economics. Tampered offer or price evidence is rejected before price writes.
- Rejected/superseded batches purge typed staging rows and resolve their diagnostics while retaining raw supplier evidence plus compact audit controls; no reusable operational price archive is created.
- Candidate FUTURE imports do not poison operational CURRENT readiness. Trusted views exclude unverified offers/prices, inactive vendors, and non-CURRENT/non-LIVE variants.
- Focused price-book validation passed `42 / 42`; Packet-4/pricing/storage passed `52 / 52`; the combined Packet-0-through-4 affected suite passed `137 / 137`. All abnormal counters were `0`; the only warning was the pre-existing Starlette `TestClient` deprecation notice.
- Registered deterministic-suite floor is now `496` (`452` foundation checkpoint + `42` price-book + one pricing + one storage test).
- Two independent read-only Packet-4 audits returned PASS with no remaining concrete P0/P1 blocker. This offline checkpoint still requires morning business-rule and broader independent review before any operational reliance.

## Review and release status

- Emergency candidate review: pending.
- Browser acceptance: pending availability of installed browser tooling.
- Published-production Phase 4 remains OPEN.
- Phase 6 remains OWNER AUTHORIZED but PAUSED.
- Deployment, production connection, production mutation, Shopify access, and real PO creation/release remain unauthorized.
