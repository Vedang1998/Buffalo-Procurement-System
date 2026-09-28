# Saturday private real-data bridge closeout

## Disposition

This is a bounded local **private research** delivery. It connects the supplied
sealed A1 review evidence and read-only Shopify catalog, inventory and daily-sales
captures to a content-addressed intake, a zero-authority research projection, an
owner worksheet, offline exports and a loopback-only authenticated viewer.

It does **not** approve a supplier identity, pack, price, fee, schedule, forecast
policy, ABC cohort, purchase recommendation, DRAFT, PO or order. It does not use an
operational database and it does not write to Shopify. The official production
phase is unchanged.

## Exact identities

- Frozen parent: `c1895ff5a63599bae57fd41de827b0665446682f`, tree
  `b8aec47caca38bb517603166b1b54b32909181f8`.
- Exact tested material child: `4919d643f8c49e2b788bda4e8ff95ef24a283c4b`,
  tree `e2b88e9a421d97359b7a0165c6dc88f1d97a541b`.
- Local branch: `codex/saturday-real-data-bridge`; no remote push, PR, merge or
  protected-checkout mutation occurred.
- Parent-to-material range: 18 linear commits, 43 changed files,
  `+17,453/-103`.
- Worker A intake commit: `0e86f8a871c818906871c1e87d1ee6b808bbc365`.
- Worker B projection commit: `3d19ae5adaa90dc7d7c096e8eb5c677947c1014a`.
- Worker A native-capture adapter commit:
  `f593edbcaa8676206f41f073d8cadae4f6448abc`.
- Child-only forecast binding remediation:
  `ba7a1df32e7d28e4d8fb596c762746f2c4f6defe`.
- Material subtree IDs: `procurement/src` `182e68d1644ae7b7fc4eb24359c91e5e0726d80f`,
  `procurement/tests` `d7676e8210cc53a3970394c756c85065ff975a39`,
  `procurement/tools` `9c615217dbf51bb860716094df6aefc65d115aac`.
- `procurement/config` remains `1e6449a792393bc25edcce99fc14f6a43f9fe9eb`,
  `procurement/db` remains `c6fd3e0459473ad479c106222a3b8d9746c0862a`,
  and `scripts` remains `944d1c42331c367b0a530f97f5ffcbf58f0d6acb`.
  No table, migration or dependency was added.

This closeout and the handoff/status updates are a separate documentation-only
child. The review transport records that final documentation commit and proves
the material subtrees above are identical to the tested target.

## Frozen V2 review transport

The earlier candidate and both input archives remain unchanged:

- `Buffalo_Development_Forecast_V2_Review_Delta.zip`: 1,998,197 bytes,
  SHA-256 `d50c09b9809c269eb7e94596bf1892cfecfd6ea58b35e99607fd0757b8215337`.
- `Buffalo_Forecast_Independent_Review_Kit.zip`: 10,318,285 bytes,
  SHA-256 `aa2955d9dbe2ddfeac120f0aa728ec48d2c00389aab4f3fcaa2972dcbc7c4b0e`.
- Fresh reconstruction recovered `dd1316022c3e778963c84d818f79ce61f61f192a`
  / tree `cc98458065117be8850225099591acb60e48f469`, then
  `c1895ff5a63599bae57fd41de827b0665446682f` / tree
  `b8aec47caca38bb517603166b1b54b32909181f8`.
- Reconstruction log SHA-256:
  `6fd3ed254a096e14aa2d0ee4e0fd5ddad86bf98533abacb6d99784e3ac8c6c15`.

The frozen parent review disposition remains findings 1, 2, 4 and 5 PASS and
finding 3 FAIL. The child remediates finding 3; it does not rewrite the frozen
parent or its archive. Claude/external review remains **NOT ASSESSED** because no
external-review result was returned in this task.

## Implemented bounded path

1. A strict private-root adapter validates the native catalog and daily-sales
   manifests, every declared part/hash/count and the exact sealed A1 reader.
   Immutable blobs are written before a content-addressed manifest; readback
   rehashes and rederives the intake.
2. Shopify Variant ID is the only canonical join. A1 supplier observations stay
   hypotheses with occurrence, mapping, blocker, ladder and source lineage; they
   never become mappings or selected offers.
3. A pure projection emits coverage, research rows and an owner worksheet from
   the same canonical object. HTML is escaped with offline CSP; CSV formula
   prefixes are neutralized. Raw captured incoming is explicitly untrusted and
   excluded from procurement arithmetic.
4. A separate GET-only loopback viewer uses a private, port-scoped HTTP Basic
   protection space and owned `0600` secret. Its seven routes contain no write
   method. No operational service/DB composition is imported.
5. The browser harness verifies exact Git source bytes, route inventory,
   missing/wrong authentication, all observed browser targets, credential
   confinement, no external network, downloads, restart identity and descendant
   cleanup.
6. The child also closes the reviewed V2 semantic-need defect and authenticates
   referenced immutable inventory captures against their exact database header
   and full rows before prepare and first packet build. V1 compatibility remains
   covered by the authoritative suite.

## Actual private coverage and outputs

All dates and counts below are measured controls, not extrapolations.

| Control | Result |
| --- | ---: |
| Current catalog | 1,417 products / 2,009 Variants / 71 complete pages |
| A1 original review / separate census | 2,000 / 2,003 |
| A1 original returned + additions | 1,999 + 4 |
| A1 joined to current catalog | 1,995 |
| A1 identities absent from current catalog | 5 |
| Current Variants without A1 review | 14 |
| Complete daily-sales period | 2026-06-27 through 2026-09-18 (84 days) |
| Raw sales rows / historical Variant IDs | 9,431 / 1,740 |
| Current catalog sales joins | 1,436 |
| Historical IDs not in current catalog | 304 |
| Current Variants observed zero on all 84 complete days | 573 |
| Projection coverage / owner worksheet rows | 2,009 / 2,009 |
| Research rows / exact supplier hypotheses | 14,901 / 14,774 |
| Supplier names represented | 8 |
| Forecast / ABC / purchasing economics calculated | 0 / 0 / 0 |

The local day `2026-09-19` was intentionally omitted because it was partial at
capture time. Inventory, on-hand, committed, raw incoming, current item cost,
historical revenue/COGS and source timestamps are retained distinctly. Captured
incoming is labelled `UNTRUSTED_CAPTURE_ONLY`; trusted incoming remains absent.

The owner outputs are:

- `owner-preview.html` — 72,703,796 bytes,
  SHA-256 `f769aa44cf59f835503a3984eb89c80a3f712693ccd05f9887ee1fa170ae7cdc`.
- `owner-worksheet.csv` — 128,227,017 bytes,
  SHA-256 `65b6a888d588df0fdb06857f996bb3479fe60cc8896aed4b7b422b18a66b5bc9`.
- `coverage.json` — 10,246,278 bytes,
  SHA-256 `9bca2094ca6ce2ffd2370f2147a57e54de1cc47ed31dcc27303b7f787077a6b8`.
- `projection.json` — 135,695,585 bytes,
  raw SHA-256 `4c0a77bb321e1d024bc7976c7b978fc4fc95ffee48d56a37a18e0d38a0e6d3f7`.

These files contain private business data. They travel only in the separately
labelled private preview archive, not in Git or the code/review transport.

## Why numerical purchasing remains blocked

- The ABC input has 84 days of revenue/COGS but lacks an independently attested
  exact eligible/excluded cohort. ABC is therefore `NOT_CONFIGURED`; current
  supplier price is never substituted for historical COGS.
- The real capture does not supply an approved forecast profile, 138-day V2
  history, availability/stockout states, vendor schedule/cutoff/receipt contract
  or purchase horizon. Forecast and protection remain
  `REAL_NUMERICAL_EVALUATION_NOT_RUN`.
- Exact approved pack/qualifying-unit facts, exclusions, target margins,
  selected price ladders, fees and purchasing policy are incomplete. The output
  may show current cost or unapproved supplier ladder evidence, but it does not
  calculate or recommend purchasing economics.
- There is no trusted incoming/open-order/transfer feed. Raw captured incoming is
  visible only as untrusted research evidence.
- Original S1 and its clarification were not transferred. Their absence does not
  invalidate the sealed A1 snapshot but preserves distinct source gaps.

The narrow recommended next sequence is: owner reviews the offline worksheet;
owner resolves exact Variant/pack/exclusion and supplier-price evidence; an
authorized capture supplies trusted incoming/open orders and vendor schedules;
the owner separately approves the ABC cohort, target margins and forecast policy;
then a new bounded review may evaluate real need. Until all of those occur, the
blocked action is any DRAFT, PO, supplier communication or order.

## Validation and review evidence

- Exact material authoritative wrapper: **931 discovered / 931 executed / 931
  passed** in 1229.597s; failures, errors, skips, expected failures and unexpected
  successes all zero. Log SHA-256:
  `04f8f26fc30e92c6433c1fbcdd517aa0803661e09fabb560f5364edf6ee9be4c`.
- Exact material startup: **10/10 passed** in 0.004s. Log SHA-256:
  `8cbc2321289aad482131291f1375331b42ef87afbd8e2b0aeabd20b734d71193`.
- Fresh real-data Chromium acceptance at the exact material commit: initial and
  restart each passed **70/70** unique assertions and recorded 38 requests / 38
  responses. All external HTTP, WebSocket, blocked-external and unexpected-scheme
  lists are empty. Four observed targets per phase were attached, guarded and
  resumed. All four exports are byte-identical across restart.
- Browser acceptance result SHA-256:
  `d5704289d673ce088d8f1316e78a0a457f15d700d3c1c09fbe837127d18967eb`.
  Initial/restart ledgers are respectively
  `387b79bc5db1d6f871134db1d864205f878d74809e242775b8e6e90c78ab36e5`
  and `0536389c8020d08cd0ba5f4798b36a594242afc6f79a224ab9b47557a785a719`.
- Earlier focused PostgreSQL validation at the core inventory checkpoint passed
  inventory 16/16, Monday 61/61 and V2 service 14/14. It is supporting evidence,
  not relabelled as exact-final proof; all 91 tests are included in the exact
  931-test wrapper.
- Two read-only same-model reviewers independently inspected the exact material
  target and the retained browser evidence and reported no P0-P2 finding. They
  are not Claude/external review and did not substitute for machine execution.

The original causal red log is a missing-module import error and is preserved as
development provenance, not overstated as a complete integration failure. A
first real-browser diagnostic at the predecessor completed its first phase but
exposed repeated shutdown signalling; the final child added a discriminating
graceful-shutdown regression and passed both phases.

## Retained limitations

- First emergency-packet build authenticates the exact immutable inventory
  capture in the database, but the packet does not embed every row required to
  recompute the capture-wide source hash offline. This is build-time database
  authentication, not a standalone offline capture proof.
- Terminal packet replay returns hash-verified stored bytes inside the existing
  selected-input lock/attestation wrapper. Loss of that runtime attestation can
  block the builder replay path; `read_monday_artifact` remains the artifact-only
  hash-checked reader.
- The private preview archive is not encrypted disaster recovery, does not contain
  the raw source books/captures and must be handled as private business data.
- The large offline HTML/CSV files are owner-readable but are not claimed to be a
  lightweight browser experience on every machine.

## Cleanup, clock and next boundary

- Task T0: `2026-09-20T01:56:54.064728299Z` (September 19 21:56:54 EDT).
- Optional-work cutoff: `2026-09-20T09:56:54.064728299Z` (05:56:54 EDT).
- Hard stop: `2026-09-20T11:56:54.064728299Z` (07:56:54 EDT).
- Exact final suite and startup completed by `2026-09-20T08:02:31Z`, before the
  optional-work cutoff.
- Both viewer launches and both Chromium process trees stopped; loopback and CDP
  listeners stopped; the runtime root and authentication material were removed.
  No persistent viewer, tunnel, database or Shopify session was created.

The next authorized boundary is delivery of the self-contained code/review ZIP
and the separate private offline-preview ZIP for owner review. Any deployment,
remote access, refreshed private capture, commercial approval or real purchasing
requires a new explicit authorization.
