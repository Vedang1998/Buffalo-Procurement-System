# Monday P1 remediation progress

Updated: 2026-09-07 21:56 EDT

## Candidate identity and safety

- Branch: `codex/emergency-monday-procurement-mvp`.
- Independent review requested changes to implementation commit
  `4b342cf67ec1d488a2f84433042a468609624d84`, tree
  `01a451f66ca8ee8d3aaad57090d00de201f30a1c`.
- The branch entered this remediation at documentation head
  `3aed22dd09f951b27f30e04517fd3fd104f99dcd`, tree
  `8574d92621d8f265deba5f740d49f94ab84920c9`.
- P1 implementation commit:
  `dda6b0986710f032f05f50273527af160cacde5c`, tree
  `6fc845669afa056854b9306d3cf05074fa57afe3`.
- Exact independently reviewed, source-tested, and browser-accepted candidate:
  `e59ea665408cb881f25cff995cc2a6957fa59f94`, tree
  `e528fa3ff9cc7a3e13758075c7b8e98b3d5a2dce`.
- The final documentation commit/head is reported in the session return because
  a commit cannot contain its own Git identity.
- Production database connections/writes: `0 / 0`.
- Shopify calls/writes: `0 / 0`.
- FINAL transitions, releases, supplier transmissions, imports, deployments,
  or real-money actions: `0`.
- G10 and `main` were not merged or modified.

## P1 remediation

### P1-1: mixed blocked and eligible inputs

- Prepare, replay, review preview, review confirmation, and DRAFT build now
  reproduce the fingerprint from the same complete requested variant set. The
  comparison no longer infers that set from recommendation rows alone.
- The original `MONDAY_INPUT_BLOCKER` exception remains immutable and OPEN.
  A new append-only `monday_run_blocker_exclusions` row may record only
  `ACKNOWLEDGE_AND_EXCLUDE`, `RUN_ONLY`, for that exact exception, run, and
  frozen input fingerprint, with actor, timestamp, nonblank reason, and retained
  blocker evidence.
- Exclusion changes no catalog, vendor, mapping, price, or global readiness
  fact and never converts the blocked item into an eligible recommendation.
  REJECT remains a separate decision for an eligible recommendation.
- Effective blocker checks and the review packet recognize only an exact bound
  exclusion. The action must occur before DRAFT build.

### P1-2: duplicate same-day DRAFTs

- The authorized simpler fallback was selected. Supersession was not added
  because the existing immutable DRAFT/artifact model cannot represent it
  cleanly within this remediation.
- PostgreSQL now permits at most one RUNNING `MONDAY_PROCUREMENT` run per
  `business_date`. The service also reports a typed conflict before loading
  inputs when a different active same-day key exists.
- Database triggers require a Monday DRAFT's run to own the active RUNNING
  claim and prevent a run that owns any DRAFT from leaving RUNNING. Migration
  013 refuses unsafe pre-existing non-running Monday DRAFT state.
- A pre-build failed run releases the date; once a DRAFT exists, that date stays
  claimed until a separately designed and reviewed supersession lifecycle
  exists. DRAFT is still never trusted incoming.

### P1-4: unrelated vendor isolation

- A mix of complete and incomplete active vendors now produces a GLOBAL
  `VENDOR_RULES` WARN with `blocks_po=false`; each incomplete vendor retains its
  own VENDOR FAIL with `blocks_po=true`.
- A genuine GLOBAL PASS remains valid evidence. A WARN summary cannot replace
  missing required vendor-scoped evidence, and a matching VENDOR FAIL blocks
  only that vendor.
- Migration 013 deterministically refreshes legacy persisted GLOBAL
  `VENDOR_RULES` state from existing per-vendor evidence, without inventing a
  vendor PASS. No active vendors remains a genuine global blocker.

### P1-5: material EDIT_QUANTITY

- The single deterministic policy location is
  `procurement/config/rules.toml` under
  `[review.emergency_material_edit]`:
  `EMERGENCY_MONDAY_MATERIAL_EDIT_V1`, owner status
  `PENDING_OWNER_APPROVAL`, maximum NORMAL multiplier `2.0`, and maximum NORMAL
  resulting days of supply `30.0`.
- An edit is MATERIAL when it is strictly above either threshold. Exactly
  `2.0x` and exactly `30.0` days remain NORMAL. A positive edit from zero raw
  baseline units, or with zero forecast, is MATERIAL. Quantities are never
  silently capped.
- The multiplier denominator is the frozen raw `baseline_units`, not the
  pack-rounded recommended order. The preview separately shows both values.
- The policy is frozen into the run manifest and recommendation evidence;
  runtime policy drift invalidates the run.
- ACCEPT and EDIT require a read-only exact economics preview. A MATERIAL EDIT
  additionally requires a distinct append-only confirmation action before the
  immutable review decision. It binds the exact run, recommendation,
  input/preview fingerprints, and quantities. Actor, database timestamp, and
  reason are audited; immutable evidence records tier/reasons, resulting
  inventory/days supply, incremental cash, and final line cash. Python and SQL
  independently enforce the same classification.

### P1-3: loose-fee semantics still awaiting owner decision

- No flat, per-line, or per-unit fee meaning was guessed.
- Any baseline recommendation that needs loose units while the confirmed
  `loose_unit_fee` is positive is retained as the visible blocking exception
  `LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED`.
- Python and SQL reject later positive-fee loose edits or decisions, and
  migration 013 refuses unsafe active pre-remediation evidence.
- Case-only recommendations may continue even when the configured loose fee is
  positive. Confirmed zero-fee loose ordering may continue when loose ordering
  itself is allowed.
- Owner decision remains required on the exact fee unit/application semantics
  before production reliance.

## Machine evidence

- Baseline focused checkpoint: Monday `39/39` plus vendor rules `15/15`.
- Final Monday workflow module: `54/54` passed in 64.674 seconds.
- Readiness: `21/21`; vendor rules: `16/16`; Packet 3 PO ledger compatibility:
  `35/35`.
- Final affected inventory/vendor/PO/price/forecast/replenishment/strategic/
  review/readiness/Monday surface: `218/218` passed in 161.769 seconds.
- An earlier, non-accepted 217-test affected run exposed the overly strict
  global-PASS compatibility edge (one failure and 18 errors). It was corrected;
  the clean 218-test rerun above is the accepted evidence.
- Authoritative exact-commit wrapper: discovered `587`, executed `587`, passed
  `587` in 842.923 seconds. Failures `0`, errors `0`, skips `0`, expected
  failures `0`, unexpected successes `0`; all 33 registered module floors met.
- Runtime proof: Python `3.13.11`, PostgreSQL `16.9`, loopback database
  `procurement_test`. The wrapper created and destroyed the `_test` database
  and scrubbed runtime DB, libpq, Shopify, and review credentials.
- Startup hardening: `10/10` passed.
- Python compilation, pinned `uv 0.12.3` lock check, Bash/POSIX shell syntax,
  `git diff --check`, high-risk secret scan, generated/binary candidate scan,
  and forbidden Monday runtime-call scan: PASS.
- The only warning was the pre-existing Starlette TestClient/httpx deprecation
  warning.
- Claude independently reran the full `587/587` and startup `10/10` gates with
  every abnormal counter at zero, then returned **APPROVE WITH NONBLOCKING
  FINDINGS**. ChatGPT accepts this as code-review evidence, not merge,
  production, policy, or owner approval.

### Independent nonblocking findings

- **N-1:** the database guard contains the exact exclusion `run_id` check; the
  Python-side defense-in-depth check is absent.
- **N-2:** material confirmation is a distinct action, not a second-person
  requirement.
- **N-3:** days-of-supply classification uses the canonical rounded value.
- **N-4:** material-policy changes invalidate in-flight runs.
- **N-5:** a targeted positive CASE-minimum arithmetic assertion remains to be
  added.
- **N-6:** confidential Monday GET/list/detail/download surfaces still need
  verified caller protection.

### Offline started-server/browser acceptance

- Codex separately exercised the exact candidate with an actual local Uvicorn
  server, Chromium, synthetic data, and validated disposable loopback
  PostgreSQL 16 `_test` resources.
- Browser assertions passed `48/48`; database/download/ZIP assertions passed
  `144/144`. The accepted output has one DRAFT, one line, two artifacts, one
  packet event, and exact `$20.02` merchandise + `$5.00` fee = `$25.02`.
  The packet has 12 entries with a verified internal manifest. Replay preserved
  all identities and counts, and all server/browser/database processes and
  listeners were stopped.
- Host-local evidence remains outside Git at
  `/home/runner/workspace/.ai-auth/codex/evidence/monday-started-server-e59ea665-20260907T180925Z`.
  Its `SHA256SUMS_TEST_DATA.txt` manifest has 53 records covering every other
  retained file and has SHA-256
  `fe6d601746de858f733731ee2834b9134b84735d243d98269624a14ffec01cd0`;
  the report hash is
  `b4bad03c7f885221e0594c7163a74cecb2bf2e598e654e7589813cb845fe7887`.
  It is not proven backed up off-host.

### Independent hard-coded boundary values

- Raw baseline `3`, unit price `$10`: edited units `5 / 6 / 7` produce
  multipliers `1.6667 / 2.0000 / 2.3333`, incremental cash
  `$20 / $30 / $40`, final cash `$50 / $60 / $70`, and tiers
  `NORMAL / NORMAL / MATERIAL`.
- Raw baseline `20`, original cash `$200`: resulting days
  `29.99 / 30.00 / 30.01` produce edited units `29 / 30 / 31`, incremental
  cash `$90 / $100 / $110`, final cash `$290 / $300 / $310`, and tiers
  `NORMAL / NORMAL / MATERIAL`.
- A database ratio of `30.004` is canonically rounded to displayed `30.00` in
  both Python and SQL and remains NORMAL.
- The `1000x` probe is MATERIAL with `$30,000.00` final line cash and
  `$29,970.00` incremental cash; it cannot use the normal one-confirm path.
- Positive-fee case-only proof persists exactly one case, zero loose units,
  `$120.00` merchandise, `$0.00` delivery fee, and `$120.00` DRAFT total.

## Deferred P2 and production blockers

- A malicious holder of the trusted database write role can construct
  arbitrary but internally hash-consistent artifact payloads and terminal
  evidence. Eliminating that stronger actor requires a separately accepted
  role/signature or database-native semantic-rendering boundary.
- `holiday_blackout_notes` and `special_rules` are frozen and displayed but
  remain human-interpreted free text.
- The disposable `_test` workflow uses a narrow synthetic sales authority
  because no lightweight canonical sales evaluator exists for that fixture.
- Native Shopify PO CSV format, App Storage, private caller protection,
  production authentication, Nix/runtime/dependency viability, backup/restore,
  real-environment migration, and production/private browser and shadow mode
  remain unproven. Review-token equality authorizes an operation; it does not
  prove the caller or entered actor's identity. Local synthetic
  started-Uvicorn/Chromium acceptance is complete only for this offline
  candidate.
- Strategic forward buying remains evidence-only and cannot add units.
- Real current sales, same-day inventory, open-order reconciliation, vendor
  terms, mappings/packs, and CURRENT price authority have not been supplied.
  No controlled real-data DRAFT candidate exists yet.
- The retained post-remediation evidence is synthetic and host-local. The
  former `/tmp` pre-remediation sample is deleted and is not current evidence.

## Exact next action

Create and push one documentation-only checkpoint, open one draft PR, obtain
the configured CI if GitHub can form an integration tree, and then stop for
ChatGPT PR/CI review. Any current-main conflict requires separately reviewed
resolution. The owner must explicitly accept or replace the temporary `2.0x` /
`30.0 days` policy and decide loose-fee semantics before production reliance.

This week's orders are explicitly outside this checkpoint and are being handled
separately by the owner.

This is an **OFFLINE DRAFT-ONLY FOUNDATION — NOT PRODUCTION READY**. No merge,
deployment, production connection, Shopify action, supplier communication,
FINAL transition, release, or real PO is authorized.
