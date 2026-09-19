# Development Forecast V2 Five-Finding Remediation Design

**Status:** Owner-approved bounded construction design  
**Date:** 2026-09-19  
**Reviewed baseline:** `dd1316022c3e778963c84d818f79ce61f61f192a`  
**Reviewed tree:** `cc98458065117be8850225099591acb60e48f469`  
**Previously tested implementation:** `49c7b334ec958e6a03ca898e45aedf3163a9a3db`

## 1. Purpose and authority boundary

This design closes five bounded findings in the synthetic development
forecast-to-DRAFT milestone:

1. semantically bind the stored baseline need to the validated forecast and
   its frozen replenishment inputs;
2. fail closed when the eligible ABC cohort is incomplete;
3. bind forecast confidence thresholds to a versioned policy;
4. support explicitly anchored weekly and every-other-week schedules; and
5. provide statistically adequate development evidence for horizons H1-H31.

The change remains synthetic and development-only. It does not approve a
real supplier schedule, historical COGS source, service level, forecast
accuracy, nonzero lead-delay model, production forecast policy, PO release,
Shopify mutation, or order. `commercial_authority` and
`production_activation` remain false.

No migration, table, dependency, real-source adapter, or production
activation is introduced. Existing JSON/text columns hold the additive V2
evidence.

## 2. Compatibility and version registry

The shared forecast engine is selected through an immutable registry entry.
Each entry is one indivisible compatible tuple:

- frozen evidence contract;
- policy contract and checked-in policy path;
- method version;
- fixture marker contract;
- optional schedule contract and checked-in schedule path.

V1 retains its existing tuple and exact policy/fixture bytes. V2 adds one new
tuple. Components cannot be mixed across entries. Unknown, partial, mixed, or
tampered identities refuse and never fall back to V1.

The recorded run contract controls interpretation and replay. The currently
active database fixture does not reinterpret an existing run, and a recorded
contract is not itself authority to activate the capability for a new run.
New-run activation still requires all existing process, database, mapping,
selected-offer, runtime-mode, and synthetic fixture attestations.

The V2 initializer uses the separate profile `development-forecast-v2` in a
fresh owned database. Its metadata binds the exact fixture marker plus the
source and canonical hashes of the V2 policy and schedule. Initializing V2
over a V1 database, supplying only a profile name, or changing only a request
flag refuses.

## 3. Shared engine and V1 preservation

The planner remains one implementation parameterized by a validated registry
entry and policy object. V1 continues to emit its existing contract, method,
policy evidence, calendar evidence, and packet bytes. V1's historical
confidence semantics remain available only to V1 validation and replay.

V2 adds evidence fields only under its own recorded contract. Validators,
manifest classification, recommendation preparation, UI rendering, packet
assembly, and browser verification dispatch on the exact recorded tuple.
Completed packet replay continues returning stored artifact bytes.

Valid historical output stays valid. Integrity defects are not grandfathered:
both V1 and V2 stored baseline needs are checked against their own recorded
forecast semantics and frozen inputs.

## 4. Finding 3: semantic forecast-to-need binding

`calculate_development_baseline_need()` remains the single authoritative
calculation. Validation must not implement a packet-specific formula.

For a READY development context, the shared validator first deterministically
replans forecast evidence from the frozen demand observations under the
recorded version. It then calls the existing calculation with:

- point forecast and empirical protection;
- recorded protection horizon;
- frozen Available and trusted incoming quantities;
- open-PO blocking state;
- frozen replenishment policy mode;
- frozen vendor cycle, lead time, and zero variability;
- selected offer sellable units per case;
- loose-order permission and fee.

The stored `need` must have the exact `BaselineNeed` key inventory and valid
JSON types. Every derived field must match the recalculation:

- status and policy mode;
- protection days;
- target and effective inventory units;
- raw need units;
- cases and loose units;
- ordered units and pack-rounding units;
- loose fee; and
- ordered reason codes.

The validator is used by manifest classification, current-input validation,
review, and packet assembly before `calculated_need` is emitted. Newly built
manifests are also classified before persistence.

An owner review decision is a separate append-only layer. A valid confirmed
`EDIT_QUANTITY` may differ from baseline need; its preview, material-risk
confirmation where required, final tier, and run-snapshot lineage remain the
authority for the approved DRAFT quantity.

A byte-identical need from another context with identical authoritative inputs
is semantically identical. A copied need whose forecast, inventory, pack,
policy, or horizon differs is rejected.

## 5. Finding 4: complete ABC cohort

The pure ABC helper accepts an explicit cohort envelope rather than treating
the supplied row subset as the denominator. The envelope contains:

- scope and independent classification lookback identity;
- the exact eligible Variant ID set;
- explicit policy exclusions and nonblank reasons;
- observed historical revenue/COGS rows; and
- coverage diagnostics.

Eligible IDs and exclusions are unique and disjoint. Duplicate rows, unknown
members, missing expected members, or mismatched scope fail closed.

Missing or invalid historical COGS for any eligible member makes the entire
cohort `INCOMPLETE` / `NOT_CONFIGURED`. No eligible member receives an A/B/C
letter. Known gross-profit dollars remain diagnostic only, accompanied by
expected, observed, calculable, missing, and invalid counts/IDs. Policy
exclusions remain separately labelled and never masquerade as missing cost.

For a complete cohort, the existing deterministic ranking, Variant-ID tie
break, positive-contribution denominator, and A/B/C threshold behavior remain
unchanged. The all-nonpositive cohort retains its existing explicit
`NONPOSITIVE_HISTORICAL_GROSS_PROFIT` meaning. Connected purchasing remains
`NOT_CONFIGURED` because no approved historical COGS adapter exists; current
supplier-book prices are never substituted.

## 6. Finding 5: V2 confidence policy

The V2 policy contains the exact section:

```json
"confidence": {
  "boundary_rule": "UPPER_BOUNDS_INCLUSIVE",
  "high_max_inclusive": "0.20",
  "medium_max_inclusive": "0.50",
  "metric": "EVALUATION_WAPE"
}
```

Keys are exact. Thresholds must be finite and nonnegative with
`high_max_inclusive < medium_max_inclusive`. Missing, malformed, unordered, or
non-finite values refuse; V2 has no hidden defaults.

Classification uses the frozen six-decimal evaluation WAPE:

- HIGH when evaluation is sufficient, availability is FULL, and WAPE is at
  most the inclusive high bound;
- MEDIUM when those preconditions hold and WAPE is at most the inclusive
  medium bound; and
- LOW otherwise.

V2 forecast evidence freezes the normalized thresholds and boundary rule in
addition to the policy source/canonical hashes. A legitimate policy change
therefore changes the policy identity and, where applicable, classification.
V1 historical evidence remains under V1 semantics.

## 7. Finding 1: anchored schedule V2

The checked-in schedule is a synthetic overlay, not a real vendor-source
adapter. It contains exact contract/version, profile, timezone, validity,
fabricated provenance, vendor identity, source/canonical hashes, and separate
schedule components:

- review opportunity recurrence;
- submission opportunity recurrence;
- local submission cutoff;
- delivery opportunity recurrence;
- lead time and zero variability; and
- explicit added and removed dates for each recurrence.

Each recurrence has an anchor date, cadence in weeks, and weekday. Membership
uses the signed whole-day distance from the anchor modulo `cadence_weeks * 7`.
Parity is never inferred from the current date or weekday alone. Added and
removed dates must be valid, disjoint, in scope, and use only the supported
explicit exception form.

The V2 resolver:

1. converts the server-owned evaluation instant into the declared timezone;
2. requires the business date and evaluation local date to agree;
3. requires a current review and submission occurrence before the explicit
   cutoff;
4. resolves the receipt associated with the current submission;
5. resolves the next review and submission occurrence;
6. applies lead time and selects the first permitted delivery associated with
   the next submission; and
7. sets the protection horizon to the whole local-date interval from the
   evaluation business date through the next-order receipt date.

The start date is day zero and demand targets contain the next `H` complete
calendar dates after each forecast origin, matching the existing planner's
half-open index slices. No demand day is omitted or counted twice.

For the fabricated Southern example:

- evaluation and current eligible submission: 2026-10-05;
- current-order receipt: 2026-10-08;
- next eligible submission: 2026-10-19;
- next-order receipt: 2026-10-22; and
- horizon: 17 days from 2026-10-05 to 2026-10-22.

Review/submission are every other Monday anchored on 2026-10-05. Delivery is
independently every other Thursday anchored on 2026-10-08. The evidence does
not claim that 2026-10-22 is the earliest receipt for the current order.

The weekly Western control retains its Monday/Wednesday review/submission and
Thursday delivery behavior, yielding H3 at the fixture evaluation instant.

Evidence freezes evaluation instant/business date, current submission and
receipt, next submission and receipt, horizon, timezone, cutoff, validity,
anchors, cadences, applied exceptions, schedule version, and both schedule
hashes. Missing/contradictory anchors, missed cutoffs, off-cycle dates, expired
coverage, unavailable occurrences, unsupported exceptions, or disagreement
with the frozen vendor-rule projection refuse. No catch-up order, holiday
engine, or inferred exception is added.

## 8. Finding 2: V2 H1-H31 policy

V2 uses a fixed chronological partition:

- initial training minimum: 28 days;
- selection window: 38 days;
- calibration window: 38 days;
- evaluation window: 34 days; and
- connected/max history: 138 days.

The required input is the full 138 dated days. The theoretical adaptive lower
bound `3H + 45` is documented only as derivation; it never admits shorter V2
input while the fixed 38/38/34 partition is claimed.

For a complete uncensored history, origin counts are:

```text
selection   = 38 - H + 1
calibration = 38 - H + 1
evaluation  = 34 - H + 1
```

Required boundary counts are:

| Horizon | Selection | Calibration | Evaluation |
| ---: | ---: | ---: | ---: |
| 3 | 36 | 36 | 32 |
| 17 | 22 | 22 | 18 |
| 31 | 8 | 8 | 4 |

At each origin, training is strictly the observations before the origin;
the complete target interval is `[origin, origin + H)`. Selection,
calibration, and evaluation partitions remain chronological and disjoint.
Candidate-specific minimum history and model applicability remain enforced.
Stockout-censored targets and inapplicable models reduce usable counts rather
than becoming observations.

V2 evidence reports planned, usable, censored, and model-applicable origin
counts and dates separately for all three stages. Overlapping H-day target
intervals are explicitly labelled overlapping and not independent samples.
READY remains separate from those counts.

The connected context loader reads exactly the policy's 138-day range from
the authoritative source and requires calendar-complete dated rows. Missing
dates refuse; they are not fabricated or treated as observed zero. Inventory
states retain their existing causal meanings.

## 9. Synthetic fixture and browser acceptance

The additive V2 fixture supplies exactly 138 dated fabricated history rows per
participating Variant, ending before the evaluation date. Southern constant
two-unit demand yields an independently checkable H17 point forecast of 34
units; with zero protection, Available, and trusted incoming, six-unit cases
produce raw need 34 and six cases / 36 ordered units. The selected uploaded
break price of $30 per case produces $180 baseline merchandise.

The authenticated browser flow must create, rather than pre-seed:

- price upload, confirmation, backup-bound APPLY;
- mapping decisions and offer selections;
- the H17/H3 Monday run;
- blocker dispositions and recommendation reviews;
- at least one legitimate quantity edit;
- two vendor DRAFTs and the packet.

The browser and independent verifier reconcile forecast evidence, baseline
need, selected ladder/tier, review decision, DRAFT line, packet member, vendor
fees, and totals. The V2 H17 value must change the actual need/economics, not
only a label. The weekly H3 control remains independently reconciled.

Restart and distinct-cluster populated recovery must preserve policy/schedule
hashes, contracts, database state, and artifact bytes. Active-run changes to
policy, schedule, vendor rules, or source history trigger the established
stale-input refusal. Completed V1 and V2 runs replay under their recorded
contracts. Mixed version records refuse.

## 10. Tests and evidence order

Implementation begins with targeted red reproductions:

1. forged `need` accepted after cases/units and hashes are changed;
2. incomplete ABC subset receives apparently complete letters;
3. V2 policy missing explicit confidence thresholds;
4. anchored 14-day schedule rejected by the weekday-only resolver; and
5. V1 H15/H17/H31 constant histories block.

Focused proof then covers:

- classifier, review, and packet rejection for cases/ordered units, raw/loose
  fields, internally coherent forged fields/hashes, foreign-context need, and
  missing/malformed need;
- a valid owner edit distinct from baseline;
- missing dominant/small ABC members, invalid cost, duplicates, membership
  mismatch, exclusions, order invariance, and complete cohorts;
- confidence values below, exactly at, and above both boundaries, policy hash
  change, and insufficient evaluation/availability remaining LOW;
- weekly/alternating schedules, before/after anchor, cutoff, timezone,
  month/year boundary, exceptions, expired/insufficient coverage, and V1
  replay;
- H1-H31 over constant, variable, and intermittent complete histories;
- H14/H15 and H30/H31 boundaries, insufficient/censored histories, actual
  planned/usable counts, and future-data perturbation non-leakage;
- actual service history perturbation producing a different need/quantity;
- V1/V2 activation, downgrade, stale-input, and packet-byte compatibility.

Only after focused tests pass is one final authoritative full suite run,
startup check, and changed browser/restart/recovery acceptance executed. All
new tests are registered in the sum-derived floor with independent population
checks retained.

## 11. Review, closeout, and remaining blockers

Two bounded read-only reviews inspect the final material commit and its
immediate integrations. Findings are remediated before final validation or
explicitly dispositioned within the retained authority boundary.

The tested material commit is followed only by documentation/evidence
closeout changes. The handoff records exact identities, five-finding matrix,
origin counts, schedule trace, arithmetic, compatibility/tamper/stale/recovery
results, commands, versions, cleanup, and limitations.

One additive review ZIP contains a prerequisite-thin Git bundle from the
reviewed `dd131602` baseline, complete patch and changed-file list, manifest,
design/policy/schedule, raw test and browser/recovery evidence, independent
reviews, reconstruction proof, and focused reviewer request. Existing
archives and evidence are referenced, not regenerated.

Real purchasing remains blocked on real schedule provenance, real historical
COGS and cohort policy, real forecast/backtest evidence, nonzero lead-delay
modeling, policy approval, production identity/access, deployment, Shopify
integration, PO release, and ordering authority. The acknowledged direct-SQL
selected-head enforcement limitation is not waived.
