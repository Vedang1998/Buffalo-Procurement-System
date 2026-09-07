# Monday emergency MVP evidence matrix

Status labels in this matrix describe the offline emergency branch only. They
do not close a canonical phase, authorize production, validate a native Shopify
PO format, or authorize a supplier order.

| Requirement | Implementation evidence | Deterministic evidence |
| --- | --- | --- |
| Test DB cannot fall through to production | `procurement/tools/run_tests.py`, `scripts/procurement-tests`, `postgres_test_support.py` | runner URL/connected-DB/PG16 tests; wrapper credential-scrub test |
| Owned same-day inventory with explicit incoming | `inventory.py`, migration 008 | `test_inventory.py`; Monday completed-capture and ad-hoc-row regressions |
| Typed, owner-proven vendor rules and calendar | `vendor_rules.py`, migration 009 | `test_vendor_rules.py`; Monday order-calendar and frozen-input checks |
| DRAFT is never trusted incoming | `po_ledger.py`, migration 010 | `test_po_ledger.py`; Monday DRAFT incoming regression |
| Exact reconciled open-PO evidence | `open_po_position()` and frozen recommendation manifest | PO chronology/type tests; packet open-ledger evidence assertions |
| FUTURE-only price-book staging and atomic promotion | `price_book.py`, migration 011 | `test_price_book.py`, including rollback, supersession, legacy-CURRENT freeze, seed-bundle proof |
| No direct CURRENT upload or rollover path | Packet 4 constraints; `pricing.rollover()` and API rollover fail closed | price-book and pricing regressions |
| Current sales source/coverage is authoritative | exact canonical ShopifyQL readiness contract tied to its completed backfill run and per-day run facts, or a typed per-variant manifest restricted to `_test` databases | Monday missing selected-item coverage, nominal-PASS-without-facts rejection, exact daily fact/operational reconciliation, and aggregate-preserving source-change tests |
| Known stockouts are censored; unknown days are not | `forecasting.py` transparent evidence | `test_forecasting.py`, including sparse demand, returns, bulk outlier, known/unknown stockouts |
| Exact baseline need and pack/loose rounding | `replenishment.py` | `test_replenishment.py`; independent 3,020-case invariant sweep |
| No strategic filler or auto-forward-buy | `strategic.py` always emits `strategic_extra_units=0`, `EVIDENCE_ONLY` | `test_strategic.py` tier ordering, BT/CS, cash bridge, loose-permission tests |
| Only eligible CURRENT/LIVE identities | recommendation context fails closed on identity scope/status | Monday inactive/historical and relationship tests |
| Material inputs are frozen and stale approvals fail | immutable run manifest/fingerprint, migration 012 transition/immutability triggers | raw-series collision, vendor/calendar drift, stale fingerprint, direct-DML tests |
| Human accepts, edits, rejects, and sees exact economics | read-only recommendation preview; fingerprint-bound confirmation; stored case price, merchandise, loose fee, line total, inventory, and days-supply evidence | review no-XID, half-cent, authoritative-case-price, tier-edit, quantity, days-supply, stale confirmation tests |
| Vendor minimum/fee is a human decision, never filler | read-only aggregate DRAFT preview; merchandise-only minimum test; separately accounted loose and below-minimum fees; per-vendor `PAY_FEE`/`NOT_APPLICABLE` confirmation | zero-PO preview, wrong-fingerprint rollback, loose-fee threshold, per-vendor disposition, exact shortfall/fee/total tests |
| One idempotent DRAFT per vendor/run | `draft_po.py`, unique DB guards | two-vendor, replay, rollback, concurrency, wrong-association tests |
| Emergency path cannot reach FINAL/release/import | immutable `INTERNAL_DRAFT_ONLY` mode and migration 012 guards; no Monday route for those actions | route-surface and direct status-transition tests |
| Internal CSV is deterministic and clearly non-native | `po_csv.py` fixed schema and warning | deterministic bytes, formula escaping, exact line/vendor totals |
| Review packet is complete, deterministic, and tamper-evident | `emergency_packet.py`, DB payload/effect event, content-addressed storage | ZIP manifest, storage/DB hash, missing replica, rollback/retry, terminal-state tests |
| Local storage write cannot leave a partial authority object | temp file, fsync, atomic replace, directory fsync | failed-replace preserves old bytes and removes temp |
| Actual application routes use the same services | six Monday FastAPI routes and shared navigation | in-process real FastAPI HTTP/TestClient chain through prepare, preview, confirm, build, download, replay |
| Secrets do not appear in artifacts or test subprocesses | no secret fields in renderers; test wrapper clears DB/Shopify/review credentials | static scans, wrapper test, output/form token assertions |

## Deliberately unproven or blocked

- Real September current-sales coverage and same-day inventory have not been
  supplied. The accepted historical sales authority ends August 10 and cannot
  be relabeled as current.
- Vendor terms, open orders, supplier mappings, and CURRENT prices must be
  re-proven from real sources for every affected item.
- `holiday_blackout_notes` and `special_rules` are frozen for a reviewer but
  are not interpreted by a rules engine.
- Strategic forward buying is evidence-only and remains disabled.
- `SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED` remains mandatory.
- Browser, deployment/runtime, private-access, Nix/dependency, production DB,
  Shopify, supplier communication, and real-order acceptance remain unproven
  and unauthorized.
