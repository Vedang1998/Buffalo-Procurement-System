# Monday emergency procurement morning handoff

Updated: 2026-09-07 13:12 America/New_York

This is an offline P1-remediated implementation checkpoint. Every DRAFT and
artifact remains `TEST DATA — NOT FOR ORDERING`. Nothing here authorizes a
production connection, Shopify call, supplier communication, FINAL PO, release,
deployment, or real-money action.

## 1. Candidate identity

- Repository: `Vedang1998/Buffalo-Procurement-System`.
- Branch: `codex/emergency-monday-procurement-mvp`.
- Independently reviewed/request-changes implementation:
  `4b342cf67ec1d488a2f84433042a468609624d84`, tree
  `01a451f66ca8ee8d3aaad57090d00de201f30a1c`.
- Branch head at remediation start:
  `3aed22dd09f951b27f30e04517fd3fd104f99dcd`, tree
  `8574d92621d8f265deba5f740d49f94ab84920c9`.
- Exact tested P1 implementation commit:
  `dda6b0986710f032f05f50273527af160cacde5c`, tree
  `6fc845669afa056854b9306d3cf05074fa57afe3`.
- The documentation-only closeout commit/final pushed head is reported in the
  session return; it changes no implementation or test.
- Frozen G10 was not modified or cherry-picked. `main` was not modified.

## 2. P1-remediated offline behavior

### Full requested input set and explicit run-only exclusion

The canonical input manifest carries the complete normalized requested variant
set and all contexts. Prepare, replay, preview, confirm, and build reproduce the
same fingerprint rather than rebuilding the set from recommendation rows.

A blocked item remains an immutable OPEN `MONDAY_INPUT_BLOCKER`. The only
available operation is the explicit `ACKNOWLEDGE_AND_EXCLUDE` / `RUN_ONLY`
action against that exact exception, run, and fingerprint. The append-only
record preserves the blocker and captures the entered actor, database timestamp,
nonblank reason, and original evidence. It does not create a recommendation,
change any global fact, or turn the item eligible. REJECT remains the separate
decision for an eligible recommendation.

### Same-day duplicate prevention

Supersession was deliberately not implemented. The current immutable DRAFT and
artifact model cannot make an older output unmistakably unusable without a
larger lifecycle change.

Instead, one RUNNING `MONDAY_PROCUREMENT` run may own a business date. The
invariant is enforced both by service precheck and a PostgreSQL partial unique
index. A Monday DRAFT can be created only while its run owns that claim, and a
run with a DRAFT cannot release it. A failed pre-build run releases the date; a
built DRAFT holds it until a separately authorized supersession design exists.
DRAFT remains excluded from trusted incoming.

### Vendor-rule isolation

Incomplete active vendors retain VENDOR FAIL/blocking gates. When some active
vendors are complete and others are not, GLOBAL becomes WARN/nonblocking rather
than a universal FAIL. A genuine GLOBAL PASS remains valid evidence, but GLOBAL
WARN cannot substitute for a missing required vendor row. Migration 013 repairs
the legacy persisted summary from existing vendor evidence without inventing a
PASS.

### Material edit review

The temporary owner-reviewable policy is declared once in
`procurement/config/rules.toml`:

- policy: `EMERGENCY_MONDAY_MATERIAL_EDIT_V1`;
- owner status: `PENDING_OWNER_APPROVAL`;
- maximum NORMAL multiplier: exactly `2.0x` the frozen raw baseline units;
- maximum NORMAL resulting days of supply: exactly `30.0` days.

The comparison is strict: an edit becomes MATERIAL only above either numeric
threshold. A positive edit from a zero baseline or with zero forecast is also
MATERIAL. The system never caps the quantity.

The read-only preview displays raw baseline units, pre-edit recommended units,
edited units, multiplier, resulting inventory, resulting days of supply,
incremental line cash, final line cash, exact case/unit price, merchandise, and
fee evidence. A MATERIAL EDIT requires a second distinct action before the
immutable review decision. Its append-only row binds the exact run,
recommendation, input/preview fingerprints, and quantities. Actor, database
timestamp, and reason are audited; immutable evidence records the policy,
tier/reason codes, inventory/days, and cash values. Python and PostgreSQL
independently classify the same rounded evidence.

### Loose-fee uncertainty

The owner has not defined whether a positive `loose_unit_fee` applies per unit,
line, order, or another basis. No meaning was guessed.

- A recommendation needing loose units with a positive fee is blocked as
  `LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED`.
- Python, SQL, and migration upgrade guards reject positive-fee loose edits or
  active pre-remediation evidence.
- Case-only ordering may continue if otherwise eligible.
- Confirmed zero-fee loose ordering may continue only when loose ordering is
  itself confirmed.

## 3. Other working offline features

The pre-existing branch still provides owned same-day inventory, confirmed
vendor calendars/lead-time/minimum evidence, Procurement-ledger incoming,
FUTURE-only price-book staging, deterministic forecast and baseline need,
pack/loose conversion, verified CURRENT price selection, strategic evidence
with zero automatic extra units, immutable human review, one DRAFT per vendor/run,
vendor-economics preview, deterministic internal CSVs, and a reconciled packet.

There are now seven Monday FastAPI route/method pairs: list, prepare, detail,
blocker exclusion, recommendation review, build, and artifact download. The
mutation routes use existing review-token equality before domain DB/storage
work. Token equality enables/authorizes the operation; it does not authenticate
the caller or the entered actor. Verified private caller authentication remains
a live-use prerequisite. There is still no Monday FINAL, release, import,
Shopify, or supplier-send route.

The integrated HTTP evidence uses in-process FastAPI TestClient. It is not a
started-Uvicorn, network, or browser acceptance claim.

The detailed requirement mapping is in
`docs/MONDAY_MVP_EVIDENCE_MATRIX.md`.

## 4. Exact machine evidence

All accepted database-backed results used a freshly created loopback PostgreSQL
16.9 database named `procurement_test` or another validated loopback name ending
in `_test`. Ordinary `DATABASE_URL` was never fixture authority.

- Baseline before remediation: Monday `39/39`; vendor rules `15/15`.
- Final Monday module: `54/54` in 64.674 seconds.
- Vendor rules `16/16`; readiness `21/21`; PO ledger compatibility `35/35`.
- Affected P1 surface: `218/218` in 161.769 seconds.
- Authoritative full deterministic suite on exact implementation commit:
  discovered `587`, executed `587`, passed `587` in 842.923 seconds. Failures
  `0`, errors `0`, skips `0`, expected failures `0`, unexpected successes `0`;
  33/33 registered module floors met.
- Startup hardening: `10/10`.
- Python `3.13.11`; PostgreSQL `16.9`; pinned `uv 0.12.3` lock: PASS.
- Python compilation, Bash/POSIX syntax, `git diff --check`, high-risk secret,
  generated/binary candidate, and forbidden Monday runtime-call scans: PASS.
- The only warning was the pre-existing Starlette TestClient/httpx deprecation.

One earlier 217-test affected run was intentionally not accepted: it exposed an
overly strict compatibility rule for an existing authoritative GLOBAL
`VENDOR_RULES=PASS` (one failure, 18 errors). The rule was narrowed without
allowing GLOBAL WARN to replace missing vendor evidence, and the accepted
218-test rerun is clean.

Independent Codex P1 specialists report PASS on the stabilized implementation.
The original independent verdict remains REQUEST CHANGES until the completed
commit receives narrow independent re-review; no owner acceptance is claimed.

## 5. Independently calculated boundary proof

- Baseline `3`, `$10` per unit: edits to `5 / 6 / 7` units yield
  `1.6667x / 2.0000x / 2.3333x`, incremental cash
  `$20 / $30 / $40`, final cash `$50 / $60 / $70`, and
  `NORMAL / NORMAL / MATERIAL`.
- Baseline `20`, original line cash `$200`: resulting days
  `29.99 / 30.00 / 30.01` with edited units `29 / 30 / 31` yield incremental
  cash `$90 / $100 / $110`, final cash `$290 / $300 / $310`, and
  `NORMAL / NORMAL / MATERIAL`.
- A raw database days ratio of `30.004` rounds to the canonical/displayed
  `30.00`; Python and SQL both classify it NORMAL.
- A `1000x` edit is MATERIAL, with `$30,000.00` final cash and `$29,970.00`
  incremental cash. The normal single-review path rejects it.
- The positive-fee case-only fixture creates exactly one case, zero loose units,
  `$120.00` merchandise, no fee, and a `$120.00` DRAFT total.

## 6. Safe test instruction

Use only the authoritative wrapper. It scrubs runtime DB/libpq, Shopify, and
review credentials and provisions/destroys the loopback PostgreSQL 16 database:

```bash
cd /home/runner/workspace/.ai-auth/codex/worktrees/emergency-monday-procurement-mvp
unset TEST_DATABASE_URL
./scripts/procurement-tests
```

Do not set `DATABASE_URL`, substitute a remote database, or run schema tools
against an ambient environment. No separate focused shortcut or started-server
recipe is approved.

## 7. Output/sample status

No durable post-P1 sample DRAFT or packet was generated. Database tests use
self-cleaning temporary storage, and paths are not invented. The former
pre-remediation `/tmp/buffalo-monday-handoff.Zgr3zh` sample no longer exists and
must not be used as proof of the remediated controls.

The content-addressed layout, when run in an approved environment, remains:

- `<storage-root>/monday-runs/<run-id>/vendor-<vendor-id>/<sha256>.internal.csv`
- `<storage-root>/monday-runs/<run-id>/packet/<sha256>.review.zip`

Every output remains synthetic/internal and labeled not for ordering until a
separate real-data and release authorization exists.

## 8. Required owner decisions and real inputs

The owner must explicitly, through the requested ChatGPT/owner re-review:

1. accept, replace, or reject the temporary `2.0x` raw-baseline and `30.0 days`
   NORMAL thresholds before production reliance;
2. define exact positive loose-fee semantics and application basis;
3. accept the one-active-run limitation or authorize a separately designed
   supersession lifecycle; and
4. supply/re-prove the business date and order window, canonical current sales,
   complete same-day inventory, open-order reconciliation, vendor calendars and
   terms, mappings/packs/policies, and verified CURRENT prices.

The accepted historical sales authority ends 2026-08-10 and cannot be relabeled
as current for 2026-09-07. FUTURE uploads do not update CURRENT because rollover
is deliberately absent. Any uncertain affected item must remain blocked or be
explicitly excluded only from this run.

## 9. Deferred P2 items

- A malicious holder of the trusted database write role can create arbitrary
  but internally hash-consistent artifact bytes and matching terminal evidence.
  Closing that stronger actor needs a reviewed role/signature or database-native
  semantic-rendering boundary.
- `holiday_blackout_notes` and vendor `special_rules` remain frozen free text,
  not machine-interpreted rules.
- The disposable `_test` workflow uses a narrow synthetic sales authority
  because no lightweight canonical evaluator exists for that fixture.
- Native Shopify PO CSV format remains unvalidated.
- Started-Uvicorn/browser acceptance, App Storage, private caller protection,
  production authentication, Nix/runtime/dependency viability, backup/restore,
  real-environment migrations, and shadow-mode acceptance are unproven.
- Strategic forward buying remains evidence-only and disabled.

## 10. Production boundary and next action

Before live use, all real inputs/readiness, exact reviewed commit/tree, CI,
migration/rollback, private access, storage, runtime, browser/shadow, native CSV,
and explicit human DRAFT/release gates remain required. Offline test success
does not satisfy them.

Exact next action: push this branch and STOP for narrow ChatGPT/owner re-review
and independent completed-candidate review.

`OFFLINE P1 CANDIDATE READY FOR RE-REVIEW.`

`PRODUCTION PURCHASING: NOT AUTHORIZED.`

Production DB connections/writes: `0 / 0`

Shopify calls/writes: `0 / 0`

FINAL/release/transmission/real-money actions: `0`
