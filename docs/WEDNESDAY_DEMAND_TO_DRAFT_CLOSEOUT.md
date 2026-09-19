# Wednesday development forecast-to-DRAFT closeout

Status: **BOUNDED SYNTHETIC MACHINE + STATIC REVIEW PASS; OWNER ACCEPTANCE,
EXTERNAL REVIEW AND ALL REAL USE REMAIN BLOCKED.**

This record is scoped to the local fabricated development fixture. It does not
replace the canonical authority chain, approve a forecasting policy for Buffalo,
or authorize deployment, Shopify access, supplier action, PO release or orders.

## Identities and authority

- Owner implementation contract SHA-256:
  `734e105ec7be99917e625b33984f5c3552e50b269b4002885e4fd9ed0464a687`.
- Protected prerequisite: commit
  `fac9d55cb91728dba36207ffe93f2c93da369cf4`, tree
  `4def364ace5e438767413aff817278091ac93f69`.
- Exact tested implementation: commit
  `49c7b334ec958e6a03ca898e45aedf3163a9a3db`, tree
  `ffdbe27d1283c5f6da03dea259d914d3094fdd5c`.
- Branch: `codex/wednesday-demand-to-draft` in an isolated local checkout with
  hooks disabled and remote writes disabled.
- Accepted design SHA-256:
  `52234c8296883a6dbf5814139143a81b9a311854c182ca068b0b8bbfa9fb378c`.
- Development policy source SHA-256:
  `a44a9760b9f9a348cf2c99ab769f9f042ee9873c4938f18e54e2ffb41d3cf6fc`.
- Material range changes 18 files with no `procurement/db` change and no new
  migration or table.

The causal red is genuine: at design child `de050824...`, the required test
failed with `ModuleNotFoundError: procurement_os.development_forecast`. Its raw
evidence SHA-256 is
`d98fb64fab534e9d6744f359e656676e4c1b0c17333d02cb5bb5446d06a337f3`.

## Acceptance matrix

| Requirement | Disposition | Evidence |
|---|---|---|
| Deterministic forecast/classification engine | IMPLEMENTED_AND_EXECUTED | NAIVE, SEASONAL_NAIVE, DAMPED_ETS and TSB are evaluated; focused and full suites pass. |
| Category shrinkage | BLOCKED_MISSING_INPUT | `CATEGORY_PRIOR_NOT_FROZEN`; no causal category prior is invented. |
| GP-dollar ABC | IMPLEMENTED_AND_EXECUTED as a pure classifier | Connected fixture is `NOT_CONFIGURED/MISSING_HISTORICAL_COGS`; current supplier price never substitutes for historical COGS. |
| XYZ | IMPLEMENTED_AND_EXECUTED | Exact quantized coefficient/class boundary and tiny-positive rounded-velocity cases are covered. Values remain development policy only. |
| Rolling-origin FVA/model selection | IMPLEMENTED_AND_EXECUTED | Candidate comparisons use comparable eligible purchase-horizon origins and deterministic ties. No real uplift claim. |
| Full-horizon empirical protection | IMPLEMENTED_AND_EXECUTED | 0.90 shortfall quantile, zeros retained, minimum eight usable origins, identical issue-time caps. |
| Availability qualification | IMPLEMENTED_AND_EXECUTED | Any UNKNOWN day forces LIMITED protection and LOW confidence; stockout targets are censored. |
| Vendor calendar horizons | IMPLEMENTED_AND_EXECUTED | Southern 10 days; Western 3 days. Nonzero lead-time variability refuses pending a delivery-delay model. |
| Need and policy connection | IMPLEMENTED_AND_EXECUTED | Point + protection once; Available/incoming once; existing pack, ONE_BOTTLE and ALLOCATED rules prevail. |
| Selected offer/price/review/DRAFT/packet | IMPLEMENTED_AND_EXECUTED | Real services and browser actions produce two DRAFTs, three lines and a 14-member packet. |
| Restart and recovery | IMPLEMENTED_AND_EXECUTED | Source restart, same-name restore to a distinct physical cluster, recovered restart and replay preserve state/artifact bytes. |
| Default/legacy compatibility | IMPLEMENTED_AND_EXECUTED | Feature requires exact server flag + fixture marker + selected synthetic attestations; absent contract keeps legacy path, malformed-present refuses. |
| Real/private-data readiness analysis | NOT_RUN | Lower-priority packet was not run; missing real COGS/availability/policy remains explicit. |
| Production or commercial activation | NOT_IMPLEMENTED / BLOCKED | No authority was requested or granted. |

## Exact machine evidence

- Focused: **19/19** in 36.618s. Log SHA-256
  `82d999558709ac501a94031dfdbedd9dd8cf4d54701e8f2a0bf24e04418c0485`.
- Authoritative suite: **837 discovered / 837 executed / 837 passed** in
  1157.416s. Failures, errors, skips, expected failures and unexpected
  successes are all zero. Log SHA-256
  `ecbb731fac57193e2c734d9df2229a1dbee73779cb6ecbbc15a73b276f1c519d`.
- Startup: **10/10** in 0.004s. Log SHA-256
  `8cbc2321289aad482131291f1375331b42ef87afbd8e2b0aeabd20b734d71193`.
- Chromium/restart/recovery: **165/165 assertions** = price 19 + full workflow
  83 + source restart 21 + restored workflow 21 + restored restart 21. Summary
  SHA-256
  `325fffc5f135e25eacfc55d6e8c56e7d4937f810992a7f303e02f441ea3d7e25`.
- Two read-only same-model reviewers reported no remaining P0-P2 finding at
  exact `49c7b334...`. They did not run the software and are not represented as
  Claude or external review.

Earlier browser3/browser5 and pre-final suite logs are superseded historical
evidence tied to earlier commits. They are preserved but are not used as proof
for `49c7b334...`. A failed exact-target browser start caused solely by a
124-byte Unix-socket path is also preserved as a failed diagnostic, not a test
failure or pass; it created no listener and the accepted retry changed only the
private runtime path.

## Connected known answers

The browser created the mapping/selection, price, review and DRAFT actions. It
did not fixture-seed finished decisions, selections, reviews or DRAFTs.

- Forecast results: four recommendation records. Southern Variant 1001 has a
  10-day horizon, point forecast 14.5833, zero empirically calculated but
  availability-limited protection, and an original recommendation of three
  cases. Western Variants 4001/4002 have 3-day horizons and original one-case
  recommendations; allocated 4005 remains zero routine units.
- Owner review: 1001 ACCEPT 3 cases; 4001 EDIT from 1 to 2 cases; 4002 ACCEPT 1
  case; 4005 REJECT 0. The model recommendation remains separately frozen from
  the owner edit.
- Blockers: 4003 has `ROUTINE_SELECTED_OFFER_HEAD_REQUIRED` and does not fall
  back to its usable legacy offer; 4004 has
  `LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED`.
- DRAFT economics: Southern $90 merchandise/$90 total. Western $102
  merchandise, $120 minimum, $18 shortfall, one $7 below-minimum fee and $109
  total. Grand merchandise is **$192**, fees **$7**, total **$199**.
- Artifacts: two internal CSVs and one 14-member review packet. Exact artifact
  SHA-256 values are
  `02f7fffd7410911f88039cdda5a88b65e828695eaf3548a92b9890dac39e586b`,
  `5d13130d9da8ad6c15d3c6992aafb8ba7ea2d549b7d90256324bfbdacd7e8988`
  and
  `f9033b2e212ea9ac43cf633f4f5627ebbe81f047a42f10eb043b5e04f0a48e0c`.
- Recovery: source system ID `7687272298707398797`, target system ID
  `7687272544110264867`; full durable state SHA-256 on both is
  `31cca9cc6075a477718bf4a09c87d7a75e45dabd3a1c4ad8ed0d404cb33ffc8b`.

The recorded emergency comparator is evidence, not a performance claim. The
new connected result stores the previous baseline need beside its new point,
protection and order result. It changes Variant 1001's unrounded demand basis
while lawful case rounding still yields three cases; Western known answers and
ALLOCATED zero remain controlled. Fabricated outcomes cannot establish real
forecast accuracy or FVA.

## Development policy values requiring real approval

The following values are finite test policy, not Buffalo policy: 84-day history;
28-day naive window; 7-day seasonal period; damped ETS alpha 0.35, beta 0.10,
phi 0.80; TSB alpha values 0.20 and intermittent zero share 0.40; FVA minimum
relative improvement 0.02; ABC 80%/next 15%; XYZ CV boundaries 0.50/1.00;
0.90 protection quantile with minimum eight origins; 28 proven in-stock days
for full qualification; 1.25 calendar-mean rate cap; and all split-window
minimums. Historical COGS basis, service target, availability policy, category
prior, lead-delay model and real-data acceptance thresholds remain unapproved.

## Safety, cleanup and next boundary

The accepted audit used fresh loopback-only PostgreSQL 16 clusters and private
mode-0700 runtimes. It emitted no secret into evidence. Both databases, app
servers and Chromium were stopped; the exact temporary runtime (including its
fresh secrets and synthetic databases) was then removed and is not recoverable.
Evidence and Git objects remain.

The original engineering window began `2026-09-17T03:35:33.373323Z`, entered
its validation reserve at `2026-09-17T09:35:33.373323Z`, and expired at
`2026-09-17T11:35:33.373323Z`. Later work is explicitly late-resume remediation
and closeout, not an automatic extension.

Next action is external/owner review of the additive package. Do not enable the
development feature on default/real databases. Direct-SQL enforcement, real
historical COGS and availability, real backtesting, approved service/XYZ/FVA
policy, production identity/IdP, owner remote access, native Shopify CSV
validation, PO release and orders remain blocked.
