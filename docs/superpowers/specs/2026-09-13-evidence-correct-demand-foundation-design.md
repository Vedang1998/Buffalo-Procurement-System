# Evidence-Correct Demand Foundation — design

**Status:** Owner-authorized correction under the Saturday purchasing-app
contract. Implementation is confined to the isolated
`codex/forecast-evidence-correction` branch based on exact commit
`64d8f74ef0d01b87fe96a4c1972faf2dcc702375` and tree
`690019481d92a9effb4b4c179cb55ef175de4ea5`.

## Purpose

The Monday recommendation path currently promotes a point-in-time daily
inventory snapshot into a whole-day availability state. A zero snapshot becomes
`STOCKOUT`; a positive snapshot or any sale becomes `IN_STOCK`. That inference
is not supported by the stored evidence and violates the controlling contract:
one end-of-day zero must not establish an entire stockout day.

This slice makes the demand evidence honest before adding canonical model
selection, ABC/XYZ, or empirical protection. It removes unproven stockout
censoring from new emergency forecasts, retains exact source provenance, and
surfaces the limitation to the owner. It does not claim that the full Forecasting
V1 program is complete.

## Authority and boundaries

The following rules are already authoritative:

- Proven full-day stockouts are censored observations, not ordinary zero
  demand.
- Unknown availability is not a stockout and is not silently promoted to
  in-stock evidence.
- Sparse availability evidence must not be multiplied into fabricated demand.
- Recent 7-, 14-, and 28-day signals are evidence inputs; unapproved weights or
  selection thresholds must not be invented.
- The current emergency forecast remains explicitly non-FVA and empirical
  safety stock remains unvalidated.

This design does **not** introduce or approve:

- an availability-interval ingestion authority or a new operational data
  source;
- a forecasting lookback policy, FVA threshold, regime thresholds, model
  hyperparameters, XYZ cutoffs, service-level matrix, error quantile, GP-dollar
  history window, category hierarchy, analog rule, or event-lift rule;
- a readiness override, mapping/offer activation, price change, strategic extra
  quantity, FINAL/release path, Shopify access, or real order;
- any write to an operational database or any change to the protected connected
  checkout.

All tests and demonstrations remain fabricated and use owned isolated
PostgreSQL 16 `_test` or `_demo` databases only.

## Alternatives considered

1. **Chosen: correct the evidence used by new emergency recommendations.** This
   removes the prohibited censoring inference from the active local candidate,
   versions every changed result, and leaves all unapproved model/protection
   policy visibly incomplete.
2. **Shadow diagnostics beside the old quantity path.** This would preserve old
   recommendation quantities, but the active calculation would continue using
   an inference the contract forbids. It was rejected because disclosure does
   not make unsupported evidence safe.
3. **Implement the complete model portfolio and empirical safety stock now.**
   This would make more functional progress, but it requires business thresholds
   and source authorities that the repository does not define. It was rejected
   until those owner decisions are supplied.

## Chosen architecture

### Pure evidence construction

`forecasting.py` will own a pure demand-evidence builder. Its inputs are the
exact history bounds, canonical daily net-sales rows, the already-validated
sales-coverage authority, and point-in-time inventory snapshot rows already
loaded by the Monday service. The builder refuses to materialize absent dates as
zero unless that authority proves complete coverage of the same bounds and
source rows. It returns:

- one complete calendar row per day;
- exact net units for that day, including returns;
- `inventory_state` for stockout-censoring purposes;
- a typed state basis and source references;
- exact raw 7-, 14-, and 28-calendar-day net-unit totals and velocities;
- point-in-time positive, zero, and missing snapshot-row counts, separated by
  source run and capture instant;
- proven full-day in-stock/stockout counts, which remain zero until a separately
  authorized interval source exists;
- deterministic reason codes and a serializable evidence object.

Point-in-time snapshots never establish `IN_STOCK` or `STOCKOUT`. They produce
`UNKNOWN` for censoring purposes. Positive sales may show that the item was
sellable at some time, but they do not prove full-day availability and therefore
also leave the daily censoring state `UNKNOWN`. Snapshot values and source
metadata are retained as evidence rather than discarded.

Historical rows from different snapshot runs or capture instants are never
summed into a synthetic point-in-time position. A per-date total may be reported
only when every contributing location row belongs to one exact completed run,
source hash, and `captured_at` instant. Otherwise the raw rows remain visible and the
aggregate state is `INCOMPATIBLE_POINT_IN_TIME_EVIDENCE` with no total.

The builder will not accept a caller-supplied boolean such as
`assume_stockout`. A future full-day availability source must arrive through a
separately typed, provenance-bearing interface and receive its own reviewed
design before the Monday path can emit `STOCKOUT`.

### Emergency forecast versioning

`forecast_demand` remains the transparent emergency calculation. New runs use
`EMERGENCY_TRANSPARENT_V2`; historical V1 runs remain immutable. V2 differs only
in its evidence boundary and additional raw-window diagnostics:

- it receives `UNKNOWN` for the current point-in-time inventory history;
- it cannot apply a stockout-censoring uplift without proven stockout input;
- it continues to use the existing calendar-demand fallback and existing
  disclosed emergency mechanics;
- it retains `MODEL_SELECTION_FVA_NOT_VALIDATED` and adds
  `STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE` when no qualifying interval evidence
  exists;
- it does not describe emergency constants as canonical policy.

The existing 84-day service query remains an explicitly emergency implementation
boundary for this slice; it does not become an approved forecasting or ABC
lookback merely because V2 consumes it.

Removing an unproven censoring uplift may change a recommendation. That is an
intentional evidence correction, not a new forecasting policy. Every affected
run receives the V2 method identity and a different deterministic input
fingerprint; existing frozen runs, reviews, DRAFTs, and artifacts are never
rewritten.

### V1-to-V2 run lifecycle

Changing the method constant must not silently strand a V1 run behind the
single-active-business-date constraint. Run validation becomes version-aware:

- all V1 runs remain readable;
- a V1 run at `DRAFTS_BUILT` may package only its already-frozen DRAFT evidence;
  a V1 run at `PACKET_BUILT` remains replayable, and all existing downloads
  remain available byte-for-byte;
- no V1 run may receive a new review, edit, material confirmation, or DRAFT
  after V2 is installed;
- an active, unbuilt V1 run returns the typed state
  `FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED`, never a generic stale-input
  boolean and never a V1 recomputation using the prohibited inference.

Migration 013 alone cannot enforce this boundary: it permits a direct unbuilt
run transition to `FAILED`, its generic `change_log` is mutable and non-unique,
and it does not reject new V1 review, exclusion, confirmation, or DRAFT rows.
The lifecycle therefore requires the additive, checksum-pinned
`015_monday_forecast_v2_retirement.sql` before V2 is startable.

Migration 015 adds one append-only `monday_stale_forecast_retirements` row per
run. The row binds the exact input fingerprint, retired V1 method, prior and
target status/stage, zero PO/artifact counts, server actor, reason, exact full
before/after run JSON, current transaction ID, timestamp, and a PostgreSQL-
recomputed confirmation SHA-256 under contract
`BUFFALO_STALE_FORECAST_RETIREMENT_CONFIRMATION_V1`. A run-transition trigger
allows an internal-DRAFT V1 run only to package an already-built DRAFT or to
move from an eligible unbuilt stage to `FAILED` when its exact retirement event
exists in the same transaction. It rejects new V1 runs and every other V1
transition with `FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED`.

Additional insert guards reject V1 review decisions, run-only blocker
exclusions, material-edit confirmations, purchase orders, and purchase-order
lines, resolving the actual parent run rather than trusting a caller's declared
run ID. V1 guards also reject INSERT/UPDATE/DELETE on
`procurement_recommendations`, `forecast_results`, `inventory_snapshots`,
`run_price_snapshots`, and `exceptions`, resolving the authoritative parent run
for every operation. Thus a pre-015 `PREPARING` fixture cannot gain, lose, or
rewrite analysis/evidence after installation. The only post-015 V1 child writes
left open are the already-reviewed packet event/artifact inserts needed to
package an existing `DRAFTS_BUILT` run. The retirement table is immutable.

The V1 run guard compares full row projections. `DRAFTS_BUILT` to
`PACKET_BUILT` may change `workflow_stage` only. An eligible unbuilt retirement
may change `status` and `workflow_stage` only, and must match its exact same-
transaction event. Every other V1 column delta—including timestamps, price
months, exception count, notes, method/input fields, or completion data—is
rejected. Historical V1 run rows and child evidence are therefore immutable at
the database boundary rather than by service convention.

Migration 015 adds `change_log.evidence_json JSONB NOT NULL DEFAULT '{}'` and
uses the exact audit envelope:

```json
{
  "contract": "BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1",
  "retirement_run_id": "<run UUID>",
  "reason": "<normalized reason>",
  "confirmation_sha256": "<lowercase SHA-256>",
  "transaction_id": "<decimal txid_current text>"
}
```

For that tagged row, `table_name='runs'`, `row_key=run_id::text`,
`action='UPDATE'`, `run_id` is exact, `before_json` and `after_json` are the full
run rows, `actor` is exact, and `occurred_at` equals the retirement event time.
A partial unique index on `run_id` for this exact contract permits one mirror
per retirement. The audit trigger rejects UPDATE/DELETE when either OLD or NEW
is tagged, rejects turning an existing row into or out of the tag, and validates
tagged INSERT against the same-transaction event and final run. A deferred
audit-side assertion checks audit-to-event/final-run integrity, while the event-
side deferred assertion checks event-to-audit/final-run integrity. Audit-only,
event-only, update-only, tag-removal, or missing-audit transactions fail
atomically.

The narrow pre-build retirement service previews a V1 run in `PREPARING`,
`AWAITING_REVIEW`, or `REVIEWED` without writes. It binds the run ID, frozen
fingerprint, V1 method, stage, zero-PO/zero-artifact proof, server-derived actor,
and required reason into the database-compatible confirmation hash. Confirm
uses one `SERIALIZABLE` transaction: acquire the existing Monday advisory lock,
lock the run, resolve an exact stored replay first, revalidate the preview facts,
insert the retirement event, update only `status/workflow_stage` to `FAILED`,
and insert the exact audit. It never deletes or edits frozen inputs, forecasts,
recommendations, reviews, exceptions, DRAFTs, or artifacts. A changed reason,
actor, fingerprint, method, stage, or confirmation conflicts; any PO/artifact
is ineligible.

The authenticated browser action uses the existing order-approval capability
and displays that retirement releases the date but cannot supersede an
immutable DRAFT. Its route is
`POST /monday-runs/{run_id}/retire-stale-forecast`; the first request renders the
preview and the second must carry the exact confirmation hash.

Migration 014's frozen predecessor contract recognizes exactly schema through
013. Migration 015 is therefore a post-mapping application migration: the
runner applies it only after successful 014 installation/verification and only
when persistent mapping is explicitly included. It is not added to or used to
rewrite the frozen legacy-prefix hashes. The runner gains a separate literal
post-mapping checksum manifest, ordered-prefix marker checks, and rejection of a
015-without-014 database.

The source checksum and marker alone are not installed-contract proof. The
post-mapping release manifest also pins one independently computed catalog hash
covering every 015-owned table column/default/constraint, index, function
identity/body/property, trigger definition/enabled state, and protected
`change_log` column/constraint. Migration 015 supplies a direct assertion
function that independently checks its required object/topology inventory and
contract marker. Apply, installed replay, launcher/startup, and V2 preparation
must verify the literal source release and installed catalog before trusting or
invoking that assertion. A forged marker, removed/disabled/replaced trigger,
changed function, missing constraint/index, or partial object set refuses before
a V2 run or authority write. The apply transaction publishes the migration
marker only after DDL, catalog verification, and direct assertion all pass.

The synthetic initializer and launcher use the same ordered boundary. Existing
schema-through-013 callers that do not opt into persistent mapping continue to
stop before both 014 and 015; `prepare_monday_run` also refuses V2 explicitly
unless verified 015 is installed, so invoking application code outside the
launcher cannot silently rely on migration 013's weaker transitions.

The fabricated stale-V1 browser fixture is created under the only permitted
initialization order: apply schema through 013, apply and verify 014, seed the
raw fabricated evidence plus exactly one `PREPARING` V1 fixture, apply and
verify 015, then publish the synthetic-demo initialized marker last. No runtime
V1 override exists. If 015 fails, the initialized marker is absent. Initializer
replay/early-return verifies the exact 014 and 015 source markers, installed
catalogs, assertions, and final demo marker before reporting success.

### Monday integration and persistence

`recommendations.py` will call the pure builder instead of assigning daily
states inline. The exact demand-evidence object becomes part of the frozen
context before the run fingerprint is computed. The existing
`forecast_results` row will continue to store:

- `demand_regime = NULL`, because no canonical regime was classified;
- `selected_model = NULL`, because no candidate won an FVA gate;
- `abc_class = NULL` and `xyz_class = NULL`, because neither classification was
  calculated;
- `safety_stock_units = NULL`, because empirical protection was not calculated;
- `method_version = EMERGENCY_TRANSPARENT_V2`, which identifies the applied
  emergency calculation without masquerading as a selected model;
- the corrected calendar velocity, `in_stock_velocity = NULL`, and explicitly
  limited confidence evidence;
- the complete typed demand evidence and limitation reasons in `diagnostics`.

Diagnostics use these typed statuses:

- `forecast_status = EMERGENCY_BASELINE_ONLY`;
- `model_selection_status = NOT_VALIDATED`;
- `classification_status = NOT_CALCULATED`;
- `stockout_censoring_status = EVIDENCE_UNAVAILABLE`;
- `safety_stock_status = NOT_CALCULATED`.

Numerical zero is never used to encode an unanswered question.

The recommendation `metrics` object will mirror the method, history bounds,
coverage counts, state basis, raw 7/14/28 diagnostics, and reason codes. The
existing baseline-need, ONE_BOTTLE, allocated exclusion, trusted-incoming,
pack-rounding, price-tier, material-review, and DRAFT rules remain unchanged.
Strategic extra stays zero.

No migration is required for the demand-evidence JSON itself because the
existing immutable diagnostics and metrics fields retain the complete object.
The separately justified migration 015 exists only for V1 retirement lifecycle
and direct-write enforcement; it does not add forecast values, authority, or
readiness state.

### Owner-visible and packet evidence

The Monday detail page will show, for each recommendation:

- emergency method version and explicit non-FVA status;
- history bounds;
- raw 7/14/28 totals and velocities;
- point-in-time snapshot coverage and the `UNKNOWN` censoring disposition;
- proven stockout/in-stock day counts;
- stockout-censoring availability status;
- safety-stock status and the existing forecast/need reason codes.

The emergency packet's frozen forecast/economics evidence will carry the same
typed fields. The UI and packet must say that point-in-time inventory did not
prove full-day availability; they must not abbreviate `UNKNOWN` into an empty
value or render uncalculated safety stock as numerical zero. The exact evidence
appears in both `frozen-input-manifest.json` and each applicable item's `metrics`
inside `recommendations-and-reasons.json`. Synthetic artifacts retain
`TEST DATA — NOT FOR ORDERING` and `INTERNAL_DRAFT_ONLY` labels.

### Frozen demand-evidence contract

The frozen object is named `demand_evidence` and has contract identity
`BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2`. All decimal values serialize as exact
base-10 strings and all dates/timestamps use ISO 8601. Its required shape is:

- `contract`, `method_version`, `history_start`, `history_end`, and
  `calendar_days`;
- `sales_coverage` containing the validated authority contract, source, exact
  bounds, and SHA-256 of the canonical full authority object;
- `daily`, ordered by date, where each row contains `business_date`, signed
  `net_units`, `inventory_state`, `state_basis`, and ordered `snapshot_refs`;
- each snapshot reference contains date, location, run ID, source, source hash,
  `captured_at`, run `completed_at`, available quantity, incoming quantity, and
  validation status;
- `snapshot_groups`, ordered by date/run/location, with compatibility status and
  an aggregate only for one coherent run/source-hash/capture instant;
- `raw_windows` with exact keys `7`, `14`, and `28`; each value contains the
  requested window, actual denominator days, inclusive start/end, signed net
  units, and signed calendar velocity;
- `availability_summary`, `statuses`, and ordered unique `reason_codes`.

Each daily `state_basis` is exactly one of `NO_POINT_IN_TIME_SNAPSHOT`,
`POINT_IN_TIME_SNAPSHOT_ONLY`, or
`INCOMPATIBLE_POINT_IN_TIME_EVIDENCE`. Each snapshot group contains
`snapshot_date`, `inventory_snapshot_run_id`, `source`, `source_hash`,
`captured_at`, run `completed_at`, ordered locations, compatibility status, and
nullable aggregate available/incoming quantities. A date with more than one
distinct run/source-hash/`captured_at` tuple is incompatible even when each
individual row is valid. Run completion time remains separate provenance and is
never substituted for the capture instant.

`availability_summary` contains exact integer counts for calendar days, unknown
days, proven full-day in-stock days, proven full-day stockout days, dates with no
snapshot, compatible point-in-time dates, incompatible point-in-time dates,
snapshot rows, positive snapshot rows, and zero snapshot rows. `statuses`
contains exactly `forecast_status`, `model_selection_status`,
`classification_status`, `stockout_censoring_status`, and
`safety_stock_status`, with the values defined above. When the current inputs
contain any point-in-time snapshot but no full-day authority, ordered reasons
include `POINT_IN_TIME_INVENTORY_NOT_FULL_DAY_AVAILABILITY` and
`STOCKOUT_CENSORING_EVIDENCE_UNAVAILABLE`.

For a history shorter than a requested raw window, the window ends at
`history_end`, starts at `history_start`, and divides by the actual inclusive
calendar-day count. For a longer history it uses exactly the trailing requested
calendar days. No missing date is materialized until `sales_coverage` has proved
the complete inclusive range. Raw signed velocity is evidence only; the
nonnegative emergency decision velocity remains a separately named field.

## Data flow

1. The Monday service validates canonical sales authority for exact complete
   coverage and reads the existing point-in-time inventory snapshot rows and
   their run/source/hash/capture metadata.
2. The pure builder creates a complete calendar series, labels each current
   daily state `UNKNOWN`, and computes raw window diagnostics without weights.
3. The emergency V2 calculation consumes those observations. With no proven
   stockout days, it uses calendar demand and records that censoring evidence was
   unavailable.
4. The service freezes the evidence object inside the input manifest and hashes
   the complete manifest before creating the run.
5. Forecast, recommendation, UI, review, DRAFT, and packet paths consume the
   same frozen result. No later request re-queries or reinterprets live history.
6. Replay with identical inputs returns the same run; changed sales, snapshots,
   evidence metadata, method version, or diagnostics changes the fingerprint and
   is rejected under an existing idempotency key.

## Error handling and fail-closed behavior

- Duplicate sales dates, non-finite quantities, invalid history bounds, invalid
  snapshot quantities, conflicting snapshot provenance, or incomplete source
  identifiers fail before run creation.
- Missing calendar dates are explicit zero-sales/`UNKNOWN` rows, not silently
  removed observations.
- Multiple location snapshots remain separately traceable. A total is allowed
  only within one exact completed run/source hash/`captured_at` instant; incompatible
  rows retain no aggregate and never become a full-day state.
- A point-in-time zero always produces the limitation reason; neither sales nor
  a positive snapshot clears it.
- The builder exposes signed raw net sales. Any nonnegative decision velocity is
  derived separately and labeled, so returns are not erased from evidence.
- If the serialized demand evidence cannot be represented exactly in the frozen
  manifest, the run fails; it never falls back to the old inline inference.
- Low confidence remains owner-visible and review-required. This slice does not
  invent a new automatic blocker or cap for low confidence.
- A V1 active run cannot pass a V2 write check. Retirement requires the exact
  preview/confirm service; direct status updates remain guarded and an existing
  DRAFT can never release its business-date claim through this path.

## Verification design

### Pure known-answer tests

- One end-of-day zero snapshot with zero sales yields `UNKNOWN`, never
  `STOCKOUT`.
- A positive point-in-time snapshot yields `UNKNOWN`, never full-day
  `IN_STOCK`.
- Positive sales with a zero or absent snapshot retain the sale and yield
  `UNKNOWN`.
- Missing dates are materialized as zero-sales/`UNKNOWN` rows.
- Returns remain in signed raw totals while decision forecasts remain
  nonnegative.
- Raw 7/14/28 totals and velocities match exact `Decimal` known answers at
  boundary lengths of 6/7, 13/14, and 27/28 days.
- Duplicate/conflicting/invalid evidence refuses deterministically.
- Missing dates can become zero-sales rows only with an exact complete
  sales-authority object for the same source and history bounds.
- Rows from different inventory runs or capture instants remain separate and
  yield `INCOMPATIBLE_POINT_IN_TIME_EVIDENCE`, never a fabricated total.
- Explicit `STOCKOUT` observations remain supported by the low-level forecast
  function for a future provenance-authorized caller, but the Monday builder
  cannot manufacture them.

### PostgreSQL Monday tests

- A completed daily snapshot with available zero is frozen as point-in-time
  evidence and produces no censored stockout day.
- Snapshot source, source hash, run ID, location, capture time, and exact raw
  values survive into the run manifest, forecast diagnostics, recommendation
  metrics, API response, and packet.
- An old V1 run remains byte-for-byte unchanged; a new V2 run has a distinct
  method identity and fingerprint.
- V1 built/packet runs remain readable and downloadable. V1 unbuilt writes fail
  with `FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED`; exact retirement
  preview/confirm releases the business date while preserving all old evidence.
- V1 retirement refuses after any PO/artifact, rejects stale or changed
  confirmation facts, is concurrent-single-winner and exactly replayable, and
  records the server principal in one append-only retirement event and one
  exact protected `change_log` mirror.
- Direct V1 review, exclusion, confirmation, PO, line, stage, failure, event-
  only, update-only, missing-audit, run-column, and analysis/evidence-child
  writes reject at the database boundary; V2 writes remain unaffected.
- Migration replay is checksum-pinned; 015 without 014, a marker gap, changed
  migration bytes, or partial migration effects refuse with no authority
  mutation.
- Exact replay is idempotent, while changed evidence under the same key refuses
  with zero new run/recommendation/DRAFT effects.
- ONE_BOTTLE, allocated exclusion, open-PO protection, case/loose conversion,
  material-edit confirmation, and DRAFT immutability retain their existing
  outcomes.
- No readiness status is directly forced to PASS.

### Browser and regression evidence

The actual loopback browser flow will assert that the owner can see the V2
method, raw windows, `UNKNOWN` availability state, limitation reasons, and
`NOT_CALCULATED` safety stock before review. It will exercise the typed V1
retirement preview/confirm path, then repeat the existing
review/edit/material-confirm/DRAFT/download/restart sequence and verify the
packet fields and hashes. The complete test suite, startup suite, backup/restore
rehearsal, independent read-only review, secret scan, and owner package are
refreshed only after focused tests pass.

## Completion boundary

This slice is complete only when the old inference is absent from production
code, all affected evidence is frozen and owner-visible, focused and full tests
pass, the real browser/recovery path passes, and independent review finds no
material issue. It advances but does not complete Forecasting V1.

The next forecast design still requires owner-approved values for history and
backtest windows, FVA materiality, model eligibility/hyperparameters, XYZ
cutoffs, GP-dollar cost/window semantics, service targets, empirical error
protection, category/analog eligibility, and event authority. Until then the
system remains explicitly emergency/non-FVA and production release remains
blocked.
