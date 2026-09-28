# Development forecast-to-DRAFT design delta

Status: approved bounded construction delta for the synthetic owner fixture only.

Authority and scope are the owner contract
`Buffalo_Wednesday_Demand_to_Draft_Implementation.md` (SHA-256
`734e105ec7be99917e625b33984f5c3552e50b269b4002885e4fd9ed0464a687`),
the canonical system specification, and the frozen parent
`fac9d55cb91728dba36207ffe93f2c93da369cf4`. This delta does not authorize
production forecasting, real purchasing data, Shopify writes, PO release, or
real supplier authority.

## Isolation and activation

- Construction occurs only on the local child
  `codex/wednesday-demand-to-draft`; the frozen parent remains unchanged.
- The existing run schema is sufficient. No migration or new historical price
  store is introduced. Full evidence is stored in the existing frozen input
  manifest, `forecast_results.diagnostics`, recommendation metrics, and a
  conditional packet member.
- The new calculation is enabled only when all of these are true: the existing
  selected-offer synthetic mode is attested, the database has the exact new
  `BUFFALO_SYNTHETIC_DEVELOPMENT_FORECAST_V1` fixture marker, and the launcher
  supplies `BUFFALO_ENABLE_SYNTHETIC_DEVELOPMENT_FORECAST=1`. The request cannot
  provide any of those facts. The additive `development-forecast-v1` fixture
  reuses the disclosed multivendor source facts but has its own marker; the
  existing `multivendor-v2` profile and acceptance remain unchanged. Absence
  uses the byte-compatible V2 emergency path; an unknown, partial, or malformed
  new contract refuses.
- The launcher may set the capability for local synthetic sessions, but the
  database marker decides whether it is applicable. Default/real databases and
  the baseline-v1 fixture cannot activate it.

## Frozen policy and calculation contract

The checked-in JSON policy is a development-only, versioned input with exact
candidate names, chronological windows, metric definitions, applicability
rules, deterministic tie order, ABC/XYZ thresholds, empirical protection
quantile, and caps. Its canonical bytes and SHA-256 are frozen into every new
run. It explicitly carries `commercial_authority=false` and
`production_activation=false`.

The engine emits `BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V1` with method
`DEVELOPMENT_ROLLING_ORIGIN_V1`. It consumes only the already validated,
calendar-complete `BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2` daily series and a
server-derived vendor horizon.

For each Variant:

1. Normalize signed net sales without discarding the original series. Negative
   net days remain evidence and contribute zero forecast demand. Proven
   stockout targets are excluded; UNKNOWN availability is never treated as
   in-stock. Any stockout imputation uses preceding observations only.
2. Split chronologically into model-selection, calibration, and final evaluation
   regions. Candidate forecasts at an origin use only dates before that origin.
   The evaluation holdout is diagnostic and cannot select a model or size
   protection.
3. Evaluate the approved candidates: naive, weekly seasonal naive, damped ETS,
   TSB for intermittent demand, and category shrinkage only when a frozen prior
   is supplied. Every candidate records applicable/inapplicable/failed status
   and reasons. Metrics are bias, MAE, edge-safe WAPE, and edge-safe MASE.
4. Select by the policy metric and deterministic tie order. A complex candidate
   must clear the configured FVA margin over naive; otherwise the simple naive
   baseline wins. Fit the selected family to all historical observations only
   after selection/evaluation evidence is frozen. Nonnegative and rate caps are
   explicit and recorded.
5. Use calibration origins only to build full-vendor-horizon shortfalls
   `max(actual_horizon - point_forecast_horizon, 0)`, retaining zero shortfalls
   in the empirical distribution. The configured upper quantile is protection.
   If the required calibration-origin count is unavailable, protection is
   `UNAVAILABLE` and the purchasing item blocks; zero is valid only when an
   adequate empirical distribution actually resolves to zero.
6. The point forecast plus empirical protection is the target exactly once.
   Available and trusted incoming are subtracted exactly once, and the existing
   pack/loose rules round the remaining need. The former lead-time-variability
   day adder is not added again as stock.

The constant oracle is mandatory: two units/day for 14 days, protection zero,
Available 8 and trusted incoming 2 yields 18 units, or three six-unit cases.

## Portfolio classes

- XYZ is parameterized from frozen demand timing/variation facts. Zero demand,
  thin history, and undefined dispersion are explicit rather than coerced.
- ABC uses independently frozen historical revenue and historical COGS for its
  own classification window, never revenue alone and never the selected/current
  supplier price as a substitute. Missing or invalid historical COGS is
  explicitly `NOT_CONFIGURED`. Eligible Variants are sorted by historical GP
  dollars, then canonical Shopify Variant ID. Cumulative policy shares assign
  A/B/C. The pure classifier accepts explicit historical GP inputs so its A/B/C
  behavior is testable even when the connected synthetic fixture correctly
  reports missing historical COGS.
- The portfolio assignment is a second deterministic pass over the already
  constructed run contexts. Excluded or blocked Variants remain in evidence
  with the exact exclusion reason; no supplier SKU becomes identity.

## Purchasing connection and replay

The calculated point forecast, protection, target, Available, trusted incoming,
raw need, and rounded order flow through the existing recommendation row. The
existing selected-offer resolver still determines the offer; its exact frozen
price ladder still determines initial and edited tier economics. Human review,
vendor-scoped DRAFTs, fees, captured stock, and packet construction remain the
only downstream path.

New evidence is duplicated only as immutable audit data:

- run manifest context: policy identity, engine evidence, and evidence hash;
- `forecast_results`: selected family, regime, point forecast, protection,
  horizon, calculated need, and complete diagnostics;
- recommendation metrics/UI: classes, model/FVA metrics, cap and fallback facts,
  point/protection/target arithmetic;
- packet: conditional `forecast-and-protection-evidence.json` for new runs.

Old manifests omit all new keys, old recommendation/packet shapes remain
unchanged, and existing stored packet bytes are replayed. If any new evidence
key, method, policy hash, or self-hash is partial or malformed, selected-run
preflight and input revalidation refuse it rather than treating it as legacy.

## Acceptance boundary

Construction starts with a causal test against the frozen parent: the constant
oracle and new contract must be absent/fail. Focused proof then covers constant,
seasonal, trend, intermittent, thin, zero, returns, stockout/UNKNOWN, leakage,
candidate failure, metric edge cases, ties, ABC/XYZ, residual protection, two
vendor horizons, malformed contracts, replay, and late rollback.

The final synthetic Chromium flow must create the mapping/selection actions,
price replacement, six-item Monday run, reviews, two vendor DRAFTs and packet.
It must visibly and independently reconcile forecast -> protection -> need ->
selected offer -> tier -> reviewed quantities -> vendor totals, then prove the
same bytes after restart and same-name recovery. This milestone remains local,
fabricated, DRAFT-only, and not a claim that the commercial system is complete.
