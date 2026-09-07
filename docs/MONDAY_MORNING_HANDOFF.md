# Monday emergency procurement morning handoff

Updated: 2026-09-07 05:25 America/New_York

This is an offline emergency implementation checkpoint. Every generated DRAFT
and artifact is `TEST DATA — NOT FOR ORDERING`. Nothing here authorizes a
production connection, Shopify call, supplier communication, FINAL PO, release,
or real-money action.

## 1. Candidate identity

- Repository: `Vedang1998/Buffalo-Procurement-System`
- Branch: `codex/emergency-monday-procurement-mvp`
- Offline implementation candidate head:
  `4b342cf67ec1d488a2f84433042a468609624d84`.
- Exact tested implementation tree:
  `01a451f66ca8ee8d3aaad57090d00de201f30a1c`.
- The later documentation-only closeout commit changes no implementation or
  test file; its exact branch head/tree is reported in the session closeout.
- The contract-designated accepted production `main` remains
  `f308ac666a2377f540e528bc873463daecc20cf8`. This emergency branch inherited
  its pre-authorized history from `1920a16`; no new main or G10 merge occurred
  overnight.
- Frozen G10 was not modified or cherry-picked.

## 2. Working offline features

The branch contains one coherent real-service path:

1. owned, same-business-day inventory capture with explicit location-level
   available/incoming facts;
2. typed owner-proven vendor calendars, lead times, variability, minimums,
   fees, and loose-unit rules;
3. Procurement-ledger incoming reconciliation in which DRAFT is never incoming;
4. strict FUTURE-only supplier price-book staging, validation, warning
   acknowledgement, atomic full-vendor promotion, and no price archive;
5. deterministic transparent forecast, baseline need, exact pack/loose
   conversion, and exact Decimal price-tier evidence;
6. strategic candidates as evidence only, with automatic strategic-extra
   quantity fixed at zero;
7. a frozen source manifest and input fingerprint, including exact selected
   sales authority/rows, completed inventory provenance, vendor/offer facts,
   current price rows, and open-PO evidence;
8. a plain Monday UI for prepare, accept/edit/reject, read-only calculation
   preview, fingerprint-bound confirmation, and stale-input rejection;
9. a second read-only vendor preview showing merchandise, minimum shortfall,
   fee, and final DRAFT total before fingerprint-bound `PAY_FEE` confirmation;
10. one immutable, idempotent DRAFT per vendor/run and an explicit all-reject
    no-order result;
11. deterministic internal CSVs and a reconciled ZIP packet with decisions,
    recommendations, price/mapping/open-PO/readiness evidence, vendor economics,
    DB-bound payload hashes, and storage readback; and
12. six FastAPI Monday routes using those same services, with no FINAL, release,
    import, or Shopify action.

The integrated test drives actual FastAPI request handling and the real service
chain against disposable PostgreSQL. It is in-process ASGI/TestClient evidence,
not a started Uvicorn or browser acceptance claim.

The detailed requirement mapping is in
`docs/MONDAY_MVP_EVIDENCE_MATRIX.md`.

## 3. Incomplete or blocked

- Real sales are not current enough. Accepted historical authority ends
  2026-08-10; a 2026-09-07 run requires proven canonical coverage through
  2026-09-06. An unrelated fresh row cannot satisfy this gate.
- Same-day real inventory, reconciled open orders, selected-item policies,
  vendor confirmations, verified mappings/packs, and verified CURRENT September
  prices have not been supplied or evaluated.
- Packet 4 accepts uploads into FUTURE only. The guarded monthly rollover is
  deliberately absent, so an upload cannot repair missing September CURRENT
  authority.
- `holiday_blackout_notes` and `special_rules` are frozen and visible but remain
  human-interpreted free text.
- Strategic forward buying is not validated and cannot add units.
- The CSV is internal only and retains
  `SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED`.
- A started Uvicorn smoke test and browser acceptance are outstanding. Current
  HTTP proof is the real in-process FastAPI chain.
- Replit App Storage, private caller protection, production authentication,
  Nix/runtime/dependency viability, backup/restore, migrations against a real
  environment, and shadow-mode acceptance are unproven.
- A holder of the trusted database write role can deliberately insert arbitrary
  but internally hash-consistent artifact payloads and matching terminal
  evidence. The application exposes no SQL endpoint and its service path
  renders/readback-verifies exact bytes, but eliminating the stronger actor
  requires a separately accepted role/signature or database-native rendering
  boundary.
- Independent Claude completed-candidate review is PENDING. Claude Code 2.1.227
  is installed, but the bounded invocation stopped before repository access
  because its OAuth session had expired and could not refresh.

## 4. Validation and review

Exact results from the frozen implementation candidate:

- Current Monday workflow module: `39/39` passed in 33.389 seconds.
- Latest affected inventory/vendor/PO/price/forecast/need/strategic/Monday/UI/
  storage/runner checkpoint: `231/231` passed with the prior 38-test Monday
  module; its subsequently added canonical run-fact regression passed
  independently before the current complete `39/39` Monday rerun.
- Static discovery and registered floors: `569/569` across 33 modules.
- Authoritative full deterministic suite: discovered `569`, executed `569`,
  passed `569` in 662.685 seconds; failures `0`, errors `0`, skips `0`, expected
  failures `0`, unexpected successes `0`; all 33 registered modules met their
  floor.
- Startup hardening: `10/10` passed.
- Compilation, pinned offline lock check, shell syntax, `git diff --check`,
  UTF-8/newline/whitespace, high-risk secret, and candidate generated-file
  scans: PASS. A pre-existing tracked attachment ZIP was outside the candidate
  delta; no generated Monday artifact is tracked.
- Supplemental independent Codex specialist/static audits: PASS, with no
  concrete in-scope P0/P1 found.
- Required independent Claude reviewer: REVIEW PENDING because local OAuth
  expired before review; no approval is claimed.

No clean result will be claimed if any failure, error, skip, expected failure,
or unexpected success is nonzero.

### Independent review packet

- Review base: `1920a16a6dc13a1b4357315f5049b938cbe7c0e2`, tree
  `8de8b332ff71e353a359d8f02bf2c5a4f76cf446`.
- Review implementation: `4b342cf67ec1d488a2f84433042a468609624d84`,
  tree `01a451f66ca8ee8d3aaad57090d00de201f30a1c`.
- Static candidate content manifest:
  `1b43c3b30d2b53245149b7e3c1f77687f4c8ecbb7da2e79752ad3647e398b918`.
- Review the complete base-to-implementation diff, not only the last commit.
  Focus on inventory authority/freshness, exact Decimal pack/case/loose and
  minimum/fee math, informed human approvals, material-input invalidation,
  transaction rollback/replay/concurrency, duplicate ordering, DRAFT-only
  enforcement, artifact integrity, and any FINAL/release/import/Shopify path.
- The reviewer must be read-only, use no production or Shopify credential, run
  no production operation, and report concrete P0/P1 findings separately from
  nonblocking caveats. The trusted-database-writer artifact caveat in section 3
  remains an explicit review item.

## 5. Safe local startup and test instructions

The authoritative reproducible gate provisions and destroys its own loopback
PostgreSQL 16 database named `procurement_test`, unless an already validated
loopback `_test` URL is explicitly supplied. The wrapper clears runtime DB,
libpq, Shopify, and review credentials before Python starts:

```bash
cd /home/runner/workspace/.ai-auth/codex/worktrees/emergency-monday-procurement-mvp
unset TEST_DATABASE_URL
./scripts/procurement-tests
```

Do not set `DATABASE_URL`, do not substitute a remote database, and do not run
schema tools against an ambient environment. A started-server recipe is not yet
published because there is no separately reviewed safe demo fixture bootstrap;
the authoritative wrapper above includes the complete Monday HTTP/service
integration module and enforces the exact loopback, database-name,
`current_database()`, PostgreSQL-16, pinned-toolchain, and credential-scrubbing
contracts itself. There is no separately approved focused-test shell shortcut.
Browser acceptance remains outstanding.

## 6. Test DRAFTs and packet locations

Retained synthetic FastAPI/service-chain sample on this execution host:

- Root: `/tmp/buffalo-monday-handoff.Zgr3zh`
- Evidence note: `/tmp/buffalo-monday-handoff.Zgr3zh/HANDOFF_EVIDENCE.md`
- Run: `62cdeab4-c952-471f-93d5-aa14d7dcc386`; frozen input fingerprint
  `462544ab29215389bc6bd7584fa0b15fd2c35760d02659596606ef1b034a835f`
- Beta DRAFT CSV:
  `/tmp/buffalo-monday-handoff.Zgr3zh/monday-runs/62cdeab4-c952-471f-93d5-aa14d7dcc386/vendor-71f8d229-406e-4c96-9449-698ff676f1aa/b659e19d37bab7465857bb81d0d13cfacf884fbec22cb621d741fe7230f5f3fb.internal.csv`
  (`SHA-256 b659e19d37bab7465857bb81d0d13cfacf884fbec22cb621d741fe7230f5f3fb`,
  777 bytes)
- Alpha DRAFT CSV:
  `/tmp/buffalo-monday-handoff.Zgr3zh/monday-runs/62cdeab4-c952-471f-93d5-aa14d7dcc386/vendor-e9939ad3-05f9-4c63-99df-5c8e6edbbe62/d09a77f4fc1d39f0aa20fba7e2a54f284f21abc1e51e26e0b54e240a9fc2bc75.internal.csv`
  (`SHA-256 d09a77f4fc1d39f0aa20fba7e2a54f284f21abc1e51e26e0b54e240a9fc2bc75`,
  765 bytes)
- Review packet:
  `/tmp/buffalo-monday-handoff.Zgr3zh/monday-runs/62cdeab4-c952-471f-93d5-aa14d7dcc386/packet/a93272e3f56429b8accb5b493c8e3d87aab82e1b1500d12efaf3e2b0e03f36e5.review.zip`
  (`SHA-256 a93272e3f56429b8accb5b493c8e3d87aab82e1b1500d12efaf3e2b0e03f36e5`,
  11,501 bytes, 11 reconciled entries)

The HTTP path covered prepare, review preview/confirm, DRAFT preview/confirm,
downloads, and replay. It produced exactly two DRAFT POs and zero non-DRAFT
POs. The `/tmp` root is retained for this host session but is not durable across
host replacement; copy it only to an approved private location if longer
retention is required.

The content-addressed layout is:

- `<storage-root>/monday-runs/<run-id>/vendor-<vendor-id>/<sha256>.internal.csv`
- `<storage-root>/monday-runs/<run-id>/packet/<sha256>.review.zip`

Every sample is synthetic, contains the safety label, and is not a supplier or
Shopify file. Database-backed tests normally use temporary directories that are
deleted during teardown; paths will not be invented if a durable sample cannot
be generated safely.

## 7. Required real inputs and owner decisions

Priority order:

1. Confirm the exact business date, selected Shopify Variant IDs, and that each
   vendor order window/cutoff is still open.
2. Supply canonical incremental ShopifyQL sales through the prior day with
   durable coverage/control evidence. August 10 history is not current.
3. Capture complete same-day inventory by location, including explicit incoming
   facts; unknown is not zero.
4. Reconcile every nonterminal Procurement PO line with received/cancelled/open
   units, expected receipt, source/reference, actor, and timestamp.
5. Reconfirm vendor calendars, delivery days, protection inputs, minimums,
   below-minimum fees, loose rules, special rules, blackout notes, and provenance.
6. Prove exactly one active verified standard offer, supplier SKU evidence,
   pack conversion, validity dates, and owner-approved replenishment mode for
   each item.
7. Prove a verified CURRENT price ladder for the business month. A FUTURE upload
   does not become CURRENT without a separately reviewed rollover.
8. Review each exact line preview, cash exposure, resulting days of supply, and
   vendor aggregate shortfall/fee. Choose `PAY_FEE`, or cancel and deliberately
   `DELAY`/add only legitimate future need—never filler.
9. Decide whether the baseline-only result, with strategic extras disabled, is
   sufficient for offline review.

Blank operator templates are under `procurement/templates/`. Completed real
copies must stay out of this public repository and in an approved private
location.

## 8. Deployment and production prerequisites

Before any live invocation, all of the following remain required:

- separate owner/ChatGPT authorization and formal Phase 4 production closeout;
- exact reviewed commit/tree and green exact-head CI;
- reviewed migration/backup/restore and rollback evidence on the intended
  environment;
- current real source authorities and every affected readiness gate passing
  without manual forcing;
- verified private caller protection and least-privilege credentials;
- reviewed durable App Storage behavior and retention;
- proven Replit Nix/runtime/dependency viability;
- started-server and browser/shadow acceptance with read-only controls;
- a real sample independently proving the Shopify PO format before removing the
  format warning; and
- explicit human acceptance of exact DRAFT economics followed by a separate
  release/order authorization.

None of these gates is implied by offline test success.

## 9. Feasible morning sequence toward 6 PM

1. ChatGPT and the independent reviewer verify the frozen offline candidate and
   its exact machine evidence.
2. Owner supplies the priority inputs above in an approved private workspace.
3. Reconcile catalog, sales, inventory, open POs, vendor terms, mappings, packs,
   policies, and CURRENT pricing; leave any uncertain item blocked.
4. Run a new offline prepare operation and inspect blockers. Do not bypass a
   failed gate.
5. Human-review every recommendation and exact line preview; reject/edit as
   needed.
6. Review the aggregate vendor shortfall/fee preview. Confirm `PAY_FEE` only if
   intended; otherwise cancel and choose delay or legitimate need outside this
   frozen run.
7. Build and reconcile only the labeled internal DRAFT packet, then have a
   second person verify hashes, quantities, costs, fees, and vendor separation.
8. Stop. Deployment, Shopify import, FINAL, supplier transmission, and ordering
   require their own later authorization and are not part of this sequence.

## 10. Readiness

`OFFLINE CANDIDATE READY FOR REVIEW.`

`PRODUCTION PURCHASING: NOT AUTHORIZED.`

Production DB connections/writes: `0 / 0`

Shopify calls/writes: `0 / 0`

PO releases/transmissions/real-money actions: `0`
