# Monday emergency MVP evidence matrix

Status labels describe only the offline emergency branch. They do not close a
canonical phase, authorize production, validate a native Shopify PO format, or
authorize a supplier order.

| Requirement | Implementation evidence | Deterministic evidence |
| --- | --- | --- |
| Test DB cannot fall through to production | `procurement/tools/run_tests.py`, `scripts/procurement-tests`, `postgres_test_support.py` | full wrapper proved Python 3.13.11, loopback PostgreSQL 16.9, exact `_test` DB identity, and credential scrubbing |
| Owned same-day inventory with explicit incoming | `inventory.py`, migration 008 | `test_inventory.py`; Monday completed-capture and ad-hoc-row regressions |
| Typed, owner-proven vendor rules and calendar | `vendor_rules.py`, migration 009 | `test_vendor_rules.py`; Monday order-calendar and frozen-input checks |
| Unrelated incomplete vendor remains scoped | vendor evaluator emits GLOBAL WARN/nonblocking plus exact VENDOR FAIL; migration 013 refreshes legacy persisted summary | two-valid/one-incomplete real-PG build; legacy GLOBAL FAIL upgrade; WARN cannot replace missing vendor evidence |
| DRAFT is never trusted incoming | `po_ledger.py`, migration 010 | `test_po_ledger.py`; Monday DRAFT incoming regression |
| At most one active Monday run per business date | service precheck; migration 013 partial unique index and bidirectional DRAFT/RUNNING guards | prior DRAFT blocks second run; concurrent direct claims yield exactly one commit; pre-build failure releases date; unsafe upgrade aborts |
| Exact reconciled open-PO evidence | `open_po_position()` and frozen recommendation manifest | PO chronology/type tests; packet open-ledger evidence assertions |
| FUTURE-only price-book staging and atomic promotion | `price_book.py`, migration 011 | `test_price_book.py`, including rollback, replacement, legacy-CURRENT freeze, seed-bundle proof |
| No direct CURRENT upload or rollover | Packet 4 constraints; `pricing.rollover()` and API rollover fail closed | price-book and pricing regressions |
| Current sales source/coverage is authoritative | exact completed ShopifyQL authority and per-day run facts, or typed per-variant `_test` manifest | missing coverage, nominal PASS without facts, aggregate reconciliation, and source-change tests |
| Known stockouts censored; unknown days not treated as stockouts | `forecasting.py` transparent evidence | sparse demand, returns, bulk outlier, known/unknown stockout tests |
| Exact baseline need and pack/loose rounding | `replenishment.py` | `test_replenishment.py`; independent 3,020-case invariant sweep |
| No strategic filler or automatic forward buy | `strategic_extra_units=0`, `EVIDENCE_ONLY` | BT/CS, tier ordering, bridge cash, loose-permission tests |
| Only eligible CURRENT/LIVE identities | recommendation context fails closed on identity scope/status | inactive/historical and relationship tests |
| Full requested input set stays fingerprint-bound | immutable run manifest carries normalized requested IDs and full context; comparison no longer derives IDs from recommendations | mixed eligible/blocked run; aggregate-preserving raw-source drift; policy drift |
| Blocked item can only be explicitly excluded from one run | append-only migration-013 `monday_run_blocker_exclusions`; original exception remains OPEN/immutable | eligible + blocked → audited exact-fingerprint exclusion → exactly one eligible DRAFT line; update/delete/wrong-scope rejection |
| Positive-fee loose semantics fail closed | prepare/review/DRAFT Python guards plus migration-013 recommendation/review guards | visible `LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED`; direct-DML rejection; case-only `$120.00` path; zero-fee loose path |
| Human sees exact economics before immutable decision | shared read-only ACCEPT/EDIT preview and fingerprint-bound confirmation | no-XID preview, half-cent/case-price, tier, inventory, days-supply, stale-confirmation tests |
| Extreme edit cannot use normal path | rules.toml policy `2.0x` baseline and `30.0` days; append-only exact material confirmation | below/at/above Python+SQL parity; `1000x`, `$30,000.00` final / `$29,970.00` incremental proof; missing/stale/mismatched confirmation rejection |
| Vendor minimum/fee is human-confirmed, never filler | read-only DRAFT preview; merchandise-only minimum; separate loose/below-minimum fees and disposition | zero-PO preview, wrong-fingerprint rollback, exact per-vendor shortfall/fee/total tests |
| One idempotent DRAFT per vendor/run | `draft_po.py`, DB unique guards | two-vendor, replay, rollback, concurrency, wrong-association tests |
| Emergency path cannot reach FINAL/release/import | immutable `INTERNAL_DRAFT_ONLY` mode; migrations 012/013 guards; no Monday action route | route-surface and direct transition tests |
| Internal CSV deterministic and non-native | `po_csv.py` fixed schema/warning | deterministic bytes, formula escaping, exact line/vendor totals |
| Packet complete, deterministic, and hash/readback integrity-checked within the application trust boundary | `emergency_packet.py`, DB payload/build event, content-addressed storage | exclusions/material confirmations, ZIP manifest, storage/DB hashes, missing replica, rollback/retry tests |
| Local writes cannot leave partial authority objects | temporary file, fsync, atomic replacement, directory fsync | failed-replace preserves old bytes and removes temp |
| Actual FastAPI routes use the same services | Monday GET/POST routes and shared navigation | in-process TestClient prepare, exclusion/review confirmations, DRAFT preview/build, download, replay |
| Secrets excluded from artifacts and tests | no secret renderer fields; wrapper clears DB/Shopify/review credentials | high-risk scan, wrapper regression, output/form-token assertions |

## Exact P1 test gate

- Monday workflow: `54/54`.
- Vendor rules: `16/16`; readiness: `21/21`; PO ledger compatibility: `35/35`.
- Affected surface: `218/218`.
- Authoritative full suite: `587/587`; failures, errors, skips, expected failures,
  and unexpected successes all `0`.

## Deliberately unproven or blocked

- Owner approval is pending for the temporary `2.0x` raw-baseline and `30.0`
  resulting-days thresholds.
- Exact positive `loose_unit_fee` application semantics are pending; affected
  loose recommendations remain blocked.
- Supersession was not implemented. One built DRAFT holds the business-date
  claim; replacement needs a separately reviewed lifecycle.
- Real September current-sales coverage, same-day inventory, reconciled open
  orders, confirmed vendor terms, verified mappings/packs, and CURRENT prices
  have not been supplied.
- `holiday_blackout_notes` and `special_rules` are visible but free text.
- Strategic forward buying remains disabled.
- `SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED` remains mandatory.
- Trusted-write-role artifact forgery, browser/started-server acceptance, App
  Storage, private access/authentication, Nix/runtime/dependencies,
  backup/restore, real-environment migration, shadow mode, production DB,
  Shopify, supplier communication, and real-order acceptance remain unproven.
