# Price-to-DRAFT acceptance follow-up

## Disposition

**BOUNDED SYNTHETIC MACHINE PASS — EXTERNAL INDEPENDENT REVIEW AND OWNER
ACCEPTANCE PENDING — REAL/DEFAULT USE BLOCKED**

This record is additive. It does not alter or supersede the frozen
`20dd65ad6e60f044bd25fbb92b46cca1f54d2357` archive or its evidence.

## Source identities

| Boundary | Commit | Tree |
|---|---|---|
| Received prerequisite | `20dd65ad6e60f044bd25fbb92b46cca1f54d2357` | `3ebebd652a12f0dbbe45d0f4a330dd5c8f21d638` |
| Machine-tested implementation | `450b55375fa97fa60535e2249d1cfddd5b838c5e` | `05ce96f32140e66c3089e856d220247d73b16904` |

The final closeout commit changes documentation only and is named in the
transport-level `SOURCE_IDENTITIES.txt`.

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

## Final machine validation

On exact implementation `450b553...`:

- authoritative suite: **823 discovered / 823 executed / 823 passed** in
  1032.763s; every abnormal counter zero; log SHA-256
  `acbe37ac0f3c628e2035d1bdc6161116f3038d3bfd77c772f8506788c2280998`;
- startup validation: **10/10 passed**, log SHA-256
  `b9c37738f7e36a217480772a37fa4a76f5531320ee39c8a363f9facbe1956630`;
- accepted browser summary SHA-256
  `62d76e67a95a9ac79716e149296be36ba5fc212f8896f789f05350e140419ce7`.

The documentation-only closeout is not represented as retested code.

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
- Internal read-only review does not constitute external Claude approval or
  owner acceptance.
