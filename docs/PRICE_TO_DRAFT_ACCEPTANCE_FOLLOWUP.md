# Price-to-DRAFT acceptance follow-up

## Disposition

**BOUNDED SYNTHETIC MACHINE + INDEPENDENT REVIEW PASS — OWNER USABILITY AND
REAL/DEFAULT USE BLOCKED**

This record is additive. It does not alter or supersede the frozen
`20dd65ad6e60f044bd25fbb92b46cca1f54d2357` archive or its evidence.

## Source identities

| Boundary | Commit | Tree |
|---|---|---|
| Received prerequisite | `20dd65ad6e60f044bd25fbb92b46cca1f54d2357` | `3ebebd652a12f0dbbe45d0f4a330dd5c8f21d638` |
| Machine-tested implementation | `450b55375fa97fa60535e2249d1cfddd5b838c5e` | `05ce96f32140e66c3089e856d220247d73b16904` |
| Independently reviewed candidate | `85242ba6919093933fc7b684406493945cf14bba` | `2ede46ef2b3bba6e246fa81d3834ddb450bd3463` |
| Bounded regression test candidate | `369e5efc22ecd076d81d9a08b91c0686443981e5` | `1d7468c6549e6260e9ab36b4826e3dbe1b6d06e2` |

The final closeout commit after the bounded regression candidate changes
documentation only and is named in the transport-level
`SOURCE_IDENTITIES.txt`.

## Frozen-candidate checkpoint

The authoritative wrapper ran first on exact `20dd65ad...`:

- discovered/executed: 823/823;
- passed: 795;
- failures: 0;
- errors: 32;
- skips/expected failures/unexpected successes: 0/0/0;
- elapsed: 992.768s;
- raw log SHA-256:
  `d9e579c991e8a6a14faccb113d7f5989330fdf0dcca7ff738fb2f22a586940f0`.

The first error was `psycopg.errors.UndefinedColumn: column
"source_price_id" does not exist`: 013-only Monday fixtures did not contain
the nullable lineage columns introduced by 016, while shared review code
selected them directly. The repair accesses optional lineage through the row's
JSON representation, retaining old schemas and old records. Frozen-candidate
startup validation independently passed 10/10.

## Changed implementation scope

The 15-file implementation delta from the received child:

- adds one checksum-pinned synthetic multivendor mapping packet and fixture
  profile without changing existing fixture bytes;
- exercises seven browser-created mapping decisions, five selections, two
  blocker exclusions, four immutable recommendation reviews, two DRAFT POs,
  three lines and three artifacts;
- exposes frozen Available quantity/time/source/location scope in review,
  DRAFT UI and versioned new internal CSV lines;
- preserves retired V1 CSV and packet bytes;
- adds an additive browser/recovery orchestrator and profile-aware local
  initialization; and
- updates existing tests and exact source-integrity allowlists without changing
  the registered population of 823.

No migration, supplier parser, forecast model, strategic policy, release path,
real authority or remote integration was added.

## Six-input matrix and known-answer totals

| Variant | Purpose and outcome | Final DRAFT economics |
|---|---|---:|
| 1001 | Southern individual bottle; selected alternative; edit crosses uploaded 2-CS BREAK | 2 cases × $30 = $60 |
| 4001 | Western retail multipack; physical 24 / Shopify 4 / qualifying 12; edit crosses 18-BT BREAK | 2 cases × $42 = $84 |
| 4002 | Western individual bottle, accepted | 1 case × $18 = $18 |
| 4003 | Approved usable legacy offer but deliberately no selection; no fallback | blocked/excluded |
| 4004 | Selected; positive loose fee unresolved | blocked/excluded |
| 4005 | Allocated / `ROUTINE_EXCLUDED`; immutable REJECT | omitted |

Southern totals: merchandise $60, fees $0, DRAFT $60. Western totals:
merchandise $102, $120 minimum, $18 shortfall, one $7 fee, DRAFT $109.
Grand totals: merchandise **$162**, fees **$7**, DRAFT **$169**.

## Internal CSV V2 fee-field contract

For `BUFFALO_INTERNAL_DRAFT_LINE_V2`, `vendor_delivery_fee` is the existing
aggregate vendor-fees field. It equals `vendor_loose_order_fee_total +
vendor_below_minimum_fee`; the below-minimum field is a component already
**included** in `vendor_delivery_fee`. Consumers must not add that component a
second time. The total relationship is:

`vendor_po_total = vendor_merchandise_total + vendor_delivery_fee`.

In the accepted Western synthetic example, merchandise is `$102.00`, the
below-minimum component is `$7.00`, aggregate fees in `vendor_delivery_fee` are
`$7.00`, and the PO total is `$109.00`—not `$116.00`.

The vendor-level merchandise, loose-fee, below-minimum-fee, aggregate-fee,
PO-total and minimum fields repeat identically on every line for the same
`(run_id, draft_po_id)`. Consumers must read or validate those fields once per
vendor DRAFT, not sum the repeated values across its line rows. A clearer
`vendor_total_fees` name may be considered only in a future separately
versioned output change. This closeout does not change runtime calculations,
CSV headers, format versions, retired output or stored artifact bytes.

## Browser and recovery proof

The final real-loopback Chromium workflow ran five phases: price upload and
confirmation; multivendor mapping/selection/review/DRAFT; source restart;
restored target; restored target restart. Assertions were 19 + 81 + 20 + 20 +
20 = **160**, all passed.

Every DRAFT CSV line records `BUFFALO_INTERNAL_DRAFT_LINE_V2`, captured
Available `0.0000`, capture time `2026-10-05T12:00:00+00:00`, source snapshot
run ID and canonical location scope. These are the frozen run inputs, not a
later live read.

The populated V1 backup was restored into a different PostgreSQL physical
cluster with the same logical database name. Source system identifier
`7685939127348653914` differs from target `7685939307666866400`. Source and
target durable state SHA-256 both equal
`fa62374e234522f43bd948d59c1786863a1dee1ecec397597278abf8807f8101`.
Authenticated downloads before restart, after source restart, after restore and
after restored restart retained the same three artifact hashes and replay
created no extra POs, lines, decisions or artifacts.

## Independent review checkpoint

The owner accepted Claude's coordinated independent review of exact commit
`85242ba...`, tree `2ede46e...`. Claude independently executed or verified at
that exact target:

- authoritative **823/823**, with every abnormal counter zero;
- startup **10/10**;
- selected-offer **6/6** and synthetic-price **7/7**;
- actual browser/recovery **160/160**;
- populated restore into a distinct PostgreSQL cluster with byte-identical
  artifacts; and
- fresh cleanup checks.

The exact implementation beneath that documentation target is `450b553...`.
Its authoritative log SHA-256 is
`acbe37ac0f3c628e2035d1bdc6161116f3038d3bfd77c772f8506788c2280998`,
startup log SHA-256 is
`b9c37738f7e36a217480772a37fa4a76f5531320ee39c8a363f9facbe1956630`,
and accepted browser-summary SHA-256 is
`62d76e67a95a9ac79716e149296be36ba5fc212f8896f789f05350e140419ce7`.
Those are Claude's results at the reviewed target, not executions of the later
bounded regression follow-up.

## Bounded regression and documentation closeout

Exact test commit `369e5ef...`, tree `1d7468c...`, retains the accepted
reviewer's nonblocking proofs:

- **A-1:** the confirmed offer has both a higher ID and a higher price than an
  eligible alternative, yet the actual mapping/selection/Monday path uses its
  independently expected cost and freezes only its BASE/BREAK ladder;
- **B-1:** two separate actual review/economics-path negatives reject,
  respectively, an authority envelope without required snapshot lineage and
  snapshot lineage without its authority envelope; the lawful both-absent
  legacy path still completes review, DRAFT and packet generation;
- **C-2:** a separate prepare-to-CSV scenario freezes Available `2.0000` from
  two named locations at `2026-09-07T13:17:00+00:00`, including its source-run
  ID, despite a later capture of `17`; and
- **C-1:** the documentation above clarifies aggregate fee containment without
  changing runtime arithmetic, CSV headers, versions or prior artifact bytes.

New machine results on that exact test commit are:

- affected focused modules: **113/113 passed** in 118.836s; log SHA-256
  `6b167b1a830d440314e3ec50c9190eded3078c56d5d7eceb808463d52bbcef9e`;
- authoritative suite: **827 discovered / 827 executed / 827 passed** in
  1029.822s; failures, errors, skips, expected failures and unexpected
  successes all zero; log SHA-256
  `69aa1938cd28ae3cc19b06b4d4916b7a9839ac3bfb251c09e03f8172b9c1cb4f`;
- startup validation: **10/10 passed** in 0.003s; log SHA-256
  `5eef6e01d9377b779737bd712f324b0d67e4b63b4c371bee617609073ad34b47`.

The additional stock scenario is one of the new 827 tests and is reported
separately from the unchanged prior 160 browser/recovery assertions. The
browser and populated-restore workflow was not rerun merely for this
test/documentation delta. Read-only reviewers found no product or test
causality defect; their one documentation-status finding is remediated here.

Between reviewed parent `85242ba...` and test commit `369e5ef...`, the
`procurement/src`, `procurement/db` and `procurement/config` tree objects remain
exactly `21466a109cf2faa387d1c7ceb083a625ba8acabb`,
`c6fd3e0459473ad479c106222a3b8d9746c0862a` and
`37fda861a54c6ab915ef97474e98f4e35b28fbf9`. Product runtime, migrations and
configuration are therefore byte-identical. The original causal-red raw log
and original cleanup transcript remain absent; this follow-up does not
recreate or infer their historical bytes.

## Additive chronology correction

The previous hard stop was `2026-09-15T11:15:34Z`. Source commit
`20dd65ad...` at 07:54:24Z and price-browser evidence around 07:55Z preceded
that stop. The recovery backup's own `created_utc` is `20260915T225242Z`, so it
did not. The prior statement that all recovery completed before the cutoff is
unsupported and corrected here without rewriting old evidence. Nothing in the
records establishes continuous work during the intervening gap.

The accepted follow-up instruction was clocked at `2026-09-15T23:53:21Z`.
Its implementation cutoff is `2026-09-16T02:53:21Z`; hard stop is
`2026-09-16T03:53:21Z`. The successful browser, suite and startup validation
all completed before the implementation cutoff.

## Limits retained

- Fabricated synthetic data only; no real mapping or price authority.
- Southern alone uses uploaded replacement prices; Western uses disclosed
  schema-valid synthetic CURRENT prices.
- Internal DRAFT CSV is not a validated native Shopify import format.
- Direct SQL does not independently enforce recommendation offer = selected
  head.
- Loopback Linux proof does not provide remote owner access or establish Mac,
  Replit or deployment portability.
- Real-data readiness, full forecast/model/FVA policy, production activation,
  Shopify writes, PO release, supplier communication and orders remain blocked.
- Claude's coordinated independent review is accepted only for exact synthetic
  candidate `85242ba...`; it is not commercial approval, owner usability
  acceptance, deployment approval or real cutover. The later regression
  executions are separately attributed above.
