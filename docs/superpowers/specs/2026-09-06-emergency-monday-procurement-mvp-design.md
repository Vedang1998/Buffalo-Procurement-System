# Emergency Monday Procurement MVP — approved overnight build design

**Status:** Owner-authorized offline implementation workstream. This document
renders the approved overnight packet into a durable implementation design; it
does not renumber canonical phases or authorize production execution.

## Boundaries

- Build only on `codex/emergency-monday-procurement-mvp`, based on exact
  `1920a16a6dc13a1b4357315f5049b938cbe7c0e2`.
- Use validated loopback PostgreSQL 16 databases ending in `_test` for all
  integration work. Never fall back to ordinary `DATABASE_URL` in tests.
- Do not connect to `neondb`, deploy, call Shopify, release/finalize a
  production PO, force readiness, open a PR, merge, or begin live cutover.
- Missing Buffalo House operating facts remain editable, visible exceptions;
  they never become inferred defaults or false-PASS gates.

## Architecture

The MVP extends the accepted PostgreSQL/FastAPI architecture with additive,
transactional migrations and narrow domain services. Backend services own all
eligibility, readiness, calculations, approvals, and state transitions. HTML
renders service results and POST routes invoke those services; GET routes are
read-only.

Existing canonical entities are reused: `variants`, `sales_daily`, `vendors`,
`supplier_offers`, `prices`, `runs`, inventory snapshots, forecast results,
recommendations, review decisions, exceptions, purchase orders, PO lines,
readiness gates, run-price snapshots, change log, and the storage adapter.
Additive tables/columns provide missing provenance, typed lifecycle, staging,
reconciliation, approvals, and packet/export evidence. Migrations remain
idempotent and each material write is contained in one transaction.

## Packet sequence

1. Centralize the existing test-database safety contract and remove legacy
   direct-invocation fallback to `DATABASE_URL`.
2. Capture current operational inventory into idempotent daily and run
   snapshots; compute inventory-history readiness from durable evidence.
3. Store typed vendor operating rules with completeness diagnostics and audited
   owner edits; incomplete vendors keep `VENDOR_RULES` failed for their scope.
4. Strengthen the Procurement PO ledger and open-incoming reconciliation so
   unresolved lines cannot be purchased twice.
5. Add strict universal price-book staging and all-or-nothing verified
   promotion; staging never writes directly to active prices.
6. Produce deterministic Monday-safe forecasts from canonical sales, censoring
   only days proven unavailable and exposing thin-history confidence.
7. Calculate normal need from vendor-specific protection, available, trusted
   incoming, policies, pack/case constraints, and explicit prerequisites.
8. Evaluate price tiers sequentially and expose baseline, strategic-extra,
   minimum/fee, cash, savings, and days-of-supply evidence without filler.
9. Persist one evidence-complete recommendation per eligible vendor/variant/run.
10. Require explicit RUN_ONLY accept/edit/reject/comment decisions, reject stale
    inputs, and recalculate totals after edits.
11. Build idempotent DRAFT-only one-vendor POs from approved lines. No release
    path or Shopify mutation is introduced.
12. Export deterministic internal procurement CSV through a replaceable Shopify
    adapter. Until a live native format is proven, emit the explicit
    `SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED` blocker.
13. Assemble a deterministic Emergency Packet through `StorageAdapter`.
14. Add a two-stage Monday orchestrator: prepare stops for human review;
    post-review recalculates and may build DRAFT artifacts only.
15. Add plain operational UI surfaces backed by the same services.
16. Prove the real service chain in disposable PostgreSQL, including two
    vendors, policies, incoming, missing data, strategic evidence, approvals,
    draft POs, exports, packets, deterministic rerun, and rollback injection.

## Calculation constraints

- Candidate eligibility always requires active `CURRENT` identity. A
  `HISTORICAL_ONLY`, inactive, or non-LIVE variant cannot become replenishment.
- Forecast V1 is transparent and deterministic: robust/weighted in-stock daily
  velocity, bounded recent influence, weekly seasonal evidence where enough
  history exists, and a conservative thin-history fallback. Forecasts are
  nonnegative and carry method/confidence/evidence.
- Baseline need is `max(0, order-up-to target - effective inventory position)`.
  Protection is vendor order cycle + lead time + verified variability buffer.
  Trusted incoming includes reconciled Procurement PO evidence only.
- ONE_BOTTLE yields zero when available + trusted incoming is at least one and
  otherwise one loose unit. Allocated/routine-excluded products produce no
  routine recommendation.
- Strategic evaluation begins only after baseline need and walks price breaks
  in ascending qualifying quantity. It never auto-adds filler or jumps directly
  to the deepest tier.
- Vendor minimum shortfall is advisory economics (`PAY_FEE`, `DELAY`, or
  `ADD_LEGITIMATE_NEED` for human review), never a reason to fabricate demand.
- Recommendation and PO calculations use exact `Decimal` arithmetic and retain
  the exact price/run snapshot used.

## State and review safety

- Recommendation inputs carry a deterministic fingerprint. Review rejects a
  stale fingerprint; one RUN_ONLY decision cannot alter global rules.
- A recommendation is not PO-eligible until explicitly accepted or quantity-
  edited by a human. Rejection excludes it.
- Draft creation re-evaluates canonical readiness for every affected scope,
  enforces offer/vendor/variant relationships, stores exact cost/economics, and
  is idempotent under the one-run/one-vendor constraint.
- The emergency workflow stops after recommendations. Post-review processing is
  a separate explicit action and still cannot create FINAL/released POs.

## Verification and handoff

Each coherent packet receives focused and affected-suite tests before its own
commit. Major checkpoints run `./scripts/procurement-tests`. Final validation
includes the complete deterministic suite, startup hardening, compilation,
pinned lock validation, `git diff --check`, credential-pattern review, and
generated-artifact review. The branch is pushed without PR. Independent
ChatGPT, Claude, and targeted specialist review remain required before any
merge, deployment, production access, or purchasing reliance.
