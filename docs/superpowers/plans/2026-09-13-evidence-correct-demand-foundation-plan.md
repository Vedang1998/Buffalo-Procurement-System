# Evidence-Correct Demand Foundation — implementation plan

**Design:** `docs/superpowers/specs/2026-09-13-evidence-correct-demand-foundation-design.md`
**Frozen parent candidate:** `64d8f74ef0d01b87fe96a4c1972faf2dcc702375`
**Frozen parent tree:** `690019481d92a9effb4b4c179cb55ef175de4ea5`
**Design commit:** `663aa6d5bca09cae0fa04705f751174e0dede613`
**Design tree:** `74ea49c7314e2e705aca7b67cd57bbbd0b208104`

## Constraints

- One writer in the isolated `codex/forecast-evidence-correction` worktree.
- Do not modify the protected connected checkout or the frozen Sunday candidate.
- No remote push/ref, PR/CI mutation, merge, deployment, public bind, Shopify
  access, operational database, production role/permission, real approval,
  supplier communication, FINAL/release, or order.
- Database tests use only newly created/owned or already approved disposable
  PostgreSQL 16 `_test` databases on loopback with credentials scrubbed.
- Preserve every existing test, migration, immutable run, DRAFT, artifact, and
  readiness safeguard. Never force a readiness gate.
- Keep the result explicitly `EMERGENCY_TRANSPARENT_V2`, non-FVA, unclassified,
  and without calculated empirical safety stock.
- The demand-evidence JSON needs no schema change. V1 retirement uses only the
  separately reviewed additive migration 015 described in the design; do not
  weaken or rewrite migrations 001–014.

## Task 0 — Reprove the implementation baseline

- Verify that every non-design blob is byte-identical to the independently
  reviewed/tested `802efc8147693d0de65d5636a0c1607364b5dd4f` code tree.
- Reconfirm both the frozen parent candidate and protected connected checkout
  are clean before the first production/test edit.
- Run the existing forecasting and replenishment modules in a scrubbed
  environment. Run the existing Monday module once against an owned disposable
  PostgreSQL 16 `_test` database if the retained exact-802 evidence cannot be
  bound mechanically to every affected baseline blob.
- Record baseline commands/results; a setup-only zero-test failure is not a test
  result and is never retried automatically.

## Task 1 — Lock failing pure evidence tests

Files:

- modify `procurement/tests/test_forecasting.py`;
- update the exact module floor in `procurement/tools/run_tests.py` after
  authoritative unittest discovery.

Add known-answer tests before production changes for:

- end-of-day zero, positive point-in-time inventory, and positive sales all
  remaining `UNKNOWN` for full-day censoring;
- complete calendar materialization only under matching validated sales
  coverage;
- exact signed 7/14/28 totals and actual-denominator velocities at 6/7, 13/14,
  and 27/28 boundaries;
- returns retained in raw evidence and nonnegative emergency output;
- missing/duplicate/non-finite/conflicting source evidence refusal;
- coherent multi-location grouping by exact run/source hash/`captured_at`, and
  refusal to synthesize an aggregate across incompatible groups;
- deterministic JSON-ready field order, reason order, exact decimal strings,
  and evidence-contract identity.

Run only the forecasting module and confirm the new assertions fail for the
expected missing builder/old classification behavior.

## Task 2 — Implement the pure demand-evidence contract

File: `procurement/src/procurement_os/forecasting.py`.

- Introduce immutable typed inputs/results for validated sales coverage,
  point-in-time snapshot references/groups, raw windows, daily observations, and
  `BUFFALO_EMERGENCY_DEMAND_EVIDENCE_V2`.
- Validate finite decimals, inclusive history bounds, exact complete coverage,
  source digest, unique sales dates, source identifiers, snapshot validation,
  and deterministic ordering.
- Materialize one daily row per covered calendar date; use `UNKNOWN` with one of
  the three approved state-basis values.
- Group snapshots only within an exact run/source-hash/`captured_at` tuple; keep
  run `completed_at` distinct and return no date aggregate on incompatibility.
- Calculate raw signed trailing 7/14/28 evidence with exact Decimal arithmetic
  and actual partial-window denominators.
- Version the emergency calculation as `EMERGENCY_TRANSPARENT_V2`, retain its
  disclosed emergency mechanics, add the censoring-evidence limitation, and do
  not claim regime/model/classification/safety-stock results.

Run `test_forecasting.py`, then `test_replenishment.py`. Preserve the low-level
explicit-`STOCKOUT` test seam for a future provenance-authorized caller while
proving the Monday-facing builder cannot manufacture that state.

## Task 3 — Integrate exact frozen evidence into Monday preparation

Files:

- modify `procurement/src/procurement_os/recommendations.py`;
- modify `procurement/tests/test_monday_workflow.py`;
- update its exact runner module floor after discovery.

- Extend the historical snapshot query to retain `captured_at`, run
  `completed_at`, validation status, source, source hash, run ID, location, and
  raw quantities.
- Bind the builder to the already-validated exact sales authority and replace
  the inline end-of-day stock-state inference.
- Freeze the full `demand_evidence` object in each context before hashing.
- Persist NULL for demand regime, selected model, ABC, XYZ, in-stock velocity,
  and safety stock; persist V2 method identity plus exact typed diagnostics.
- Mirror the same evidence and statuses in recommendation metrics without
  changing ONE_BOTTLE, allocated exclusion, incoming, pack, tier, review,
  material-confirmation, or zero-strategic-extra behavior.
- Add PG tests for exact source survival, V1 immutability, V2 fingerprint change,
  replay, stale evidence refusal, NULL facts, typed statuses, and unchanged
  protection/pack/review safeguards.

Run the forecasting, replenishment, and Monday workflow modules against one
owned disposable PostgreSQL 16 database. Do not retry a DB-reaching test run
automatically.

## Task 4 — Add version-aware V1 transition and retirement

Files:

- add `procurement/db/015_monday_forecast_v2_retirement.sql`;
- modify `procurement/tools/apply_schema.py` and
  `procurement/tools/initialize_synthetic_demo.py`;
- modify `procurement/tools/local_purchasing_candidate.py`;
- modify `procurement/src/procurement_os/recommendations.py`;
- modify callers in `procurement/src/procurement_os/procurement_review.py` and
  `procurement/src/procurement_os/draft_po.py`;
- modify `procurement/src/procurement_os/api.py`;
- extend `procurement/tests/test_monday_workflow.py` and
  `procurement/tests/test_local_access.py`;
- extend migration, runner, initializer, launcher, and startup contract tests.

- Add one append-only retirement event per run, database-compatible preview
  hashing, exact before/after row projections, a protected unique `change_log`
  mirror, and a deferred commit assertion.
- Add `change_log.evidence_json` and use the exact
  `BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1` envelope from the design. Index
  one tagged row per run; validate and defer both event-to-audit and
  audit-to-event/final-run directions; reject OLD/NEW tag mutation and
  audit-only transactions.
- Add database guards for V1 run transitions and new review, exclusion,
  material-confirmation, purchase-order, and line writes. Preserve packaging
  and replay of an already-built V1 DRAFT.
- Compare full V1 run rows and allow only a workflow-stage-only packet transition
  or a status+stage-only audited retirement. Block INSERT/UPDATE/DELETE against
  V1 recommendations, forecasts, inventory snapshots, run-price snapshots, and
  exceptions; leave only existing-DRAFT packet event/artifact insertion open.
- Keep 014's predecessor inventory frozen. Add a distinct checksum-pinned
  post-mapping application-migration manifest; apply 015 only after verified
  014 and only on the explicit persistent-mapping path. Reject 015 without 014,
  marker gaps, changed bytes, and partial/reordered state.
- Pin an independent installed-catalog digest across every 015-owned relation,
  column/default/constraint/index/function property and body, trigger definition
  and enabled state. Add an independent SQL assertion; invoke catalog
  verification before the assertion on apply/replay and from launcher/startup.
  Prove missing, altered, disabled, or marker-only objects refuse.
- Require `prepare_monday_run` to verify the installed 015 contract before any
  V2 context read or write; schema-through-013 remains a supported legacy runner
  output but cannot create V2 runs.
- Initialize in the fixed order 001–013 -> verified 014 -> raw fabricated
  evidence plus one exact `PREPARING` V1 fixture -> verified 015 -> demo marker
  last. On early-return, verify both installed contracts and markers; never add
  a runtime V1 insertion override.
- Replace the global-version boolean check with a typed validation result.
- Keep a compatibility boolean wrapper only where existing read-only callers
  require it; write paths must surface the exact retired-method state.
- Allow read/replay of immutable V1 evidence and packaging of an already-built
  V1 DRAFT; deny new V1 review/edit/confirmation/DRAFT writes.
- Implement read-only retirement preview and exact confirm for active unbuilt V1
  runs. Bind run/fingerprint/method/stage/no-PO/no-artifact/server actor/reason in
  the confirmation hash.
- Under the Monday advisory lock plus row lock, revalidate and atomically insert
  the event, mark only status/stage FAILED, and append the exact audit. Make
  identical replay stable and differing/stale/concurrent attempts fail closed.
- Add the authenticated POST route and owner-visible form without accepting a
  client-supplied actor. Preserve Origin/CSRF/capability/body-before-I/O safety.
- Prove no DRAFT-bearing run can retire and no direct or stale action releases
  its date. Prove event-only/update-only/missing-audit transactions roll back,
  retirement/audit rows are immutable, every other V1 run-column or child-
  evidence mutation refuses, and V2 remains unaffected.

Run the Monday and local-access modules plus all existing draft/packet tests.

## Task 5 — Render and export the same frozen evidence

Files:

- modify `procurement/src/procurement_os/api.py`;
- change `procurement/src/procurement_os/emergency_packet.py` only if existing
  metrics/input-manifest serialization does not already preserve the exact
  fields;
- extend `procurement/tests/test_monday_workflow.py` and affected HTTP tests.

- Render method version, non-FVA state, history bounds, raw windows, point-in-
  time coverage, `UNKNOWN` disposition, compatible/incompatible groups, proven
  full-day counts, and `NOT_CALCULATED` safety stock.
- Preserve explicit synthetic/non-order labels and HTML escaping.
- Assert exact parity among the run input manifest, `forecast_results`
  diagnostics, recommendation metrics, `frozen-input-manifest.json`, and
  `recommendations-and-reasons.json`.
- Assert packet manifest hashes and replay bytes remain deterministic.

Run focused service/HTTP/packet tests and inspect one generated packet without
exposing synthetic secret values.

## Task 6 — Extend real-browser and recovery acceptance

Files:

- modify `procurement/tools/initialize_synthetic_demo.py` only for explicitly
  fabricated raw evidence and a stale-V1 lifecycle fixture; do not force a gate;
- modify `procurement/tools/audit_local_purchasing_browser.mjs`;
- modify `procurement/tools/audit_local_purchasing_browser.py`;
- extend their existing tests and exact assertion counts.

- Drive the real loopback UI through V1 retirement preview/confirm and V2
  prepare/review/edit/material-confirm/DRAFT/download/replay.
- Assert visible raw windows/UNKNOWN/NOT_CALCULATED evidence and exact POST/GET
  statuses.
- Parse both packet members and compare the typed evidence to database rows and
  browser state.
- Restart the server, prove stale sessions fail, reauthenticate, re-fetch exact
  artifact bytes, and repeat backup/restore state equality.
- Preserve the network denylist, loopback-only bind, zero Shopify/release facts,
  credential scan, process cleanup, and candidate/protected worktree cleanliness.

## Task 7 — Independent review and combined validation

After focused tests pass and the implementation tree is clean:

1. freeze an exact candidate commit and tree;
2. obtain one independent read-only code/test review against the approved design;
3. reproduce and repair every concrete P0–P2 finding, then freeze a new exact
   candidate if needed;
4. run startup tests once;
5. run `./scripts/procurement-tests` once against an owned disposable PostgreSQL
   16 database with an explicit scrubbed environment;
6. run the real browser/restart acceptance once;
7. run backup/restore into a new empty owned `_demo` target;
8. run compilation, lock, `git diff --check`, Git integrity, generated-artifact,
   exact secret/pattern, and process/port cleanup checks.

Record exact commands, SHA/tree, counts, durations, hashes, database identities,
and setup-only failures. Never call a registered method count full clause
coverage without inspecting its body.

## Task 8 — Closeout without overstating completion

Files:

- update `docs/CODEX_HANDOFF.md`;
- update `docs/SUNDAY_PURCHASING_ACCEPTANCE.md` and
  `procurement/docs/PHASE_STATUS.md` only if the verified milestone changes their
  current facts;
- refresh the owner-facing START_HERE, recursive evidence index, bundle, patch,
  checksums, verification record, secret scan, and downloadable ZIP outside the
  protected checkout.

Preserve the prior frozen package and identify this as a descendant candidate.
State that the stockout inference is corrected and the evidence foundation is
verified, but Forecasting V1, empirical safety stock, pricing lifecycle,
strategic economics, broader browser matrix, commercial readiness, and
production release remain incomplete unless separately proven.
