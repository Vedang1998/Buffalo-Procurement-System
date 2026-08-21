# Buffalo Procurement OS — Codex Handoff

**Updated:** 2026-08-21T02:27:29Z (UTC)

**Phase numbering:** This handoff follows `procurement/docs/authority/03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md`: Phase 3 is catalog reconciliation and Phase 4 is historical ShopifyQL sales backfill/reconciliation.

This is an operational checkpoint, not a replacement for the canonical specification or `procurement/config/rules.toml`. Verify repository and database state again before acting.

**Operating process:** every coding/review/release session must follow `docs/PROJECT_GOVERNANCE.md`. At each meaningful milestone, this handoff must be refreshed with verified state, tests, readiness gates, material counts/control totals, open risks/decisions, Git reference, and exact next authorization boundary.

## Verified current state

### Phase 4 controlled identity-decision persistence — OWNER DECISIONS PERSISTED AND INDEPENDENTLY REVIEWED; REBUILD PENDING — SALES_BACKFILL = FAIL

- The independently reviewed, owner-approved 343-row manifest was persisted
  exactly once to production under the one-time sequencing exception. Manifest
  SHA-256: `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`;
  production run: `d389079c-eabf-49b5-a245-40a207025fd7`.
- Execution branch: `phase4/identity-decision-persistence`; approved design and
  plan commit: `8d3dc3c5aedcf331880c7af303706f8d08176439`; implementation commit:
  `ed13b3aba73be86e8c7df0db4874fa3445710a43`; validator-remediation and exact
  executing commit: `30b6d81d2b53ad66200d4821255597e3766d72f7`.
- PR #13, `Phase 4 controlled identity-decision persistence`, is **MERGED**.
  The independently reviewed final head was
  `28370f6176c235391a5682146703326af6f7a96f`; the normal merge commit and
  current `main` checkpoint is
  `4d0c12fec29780214b944c6d625faec5cc8a30c5`.
- Independent review of the corrected identity manifest returned **APPROVE**.
  The full persistence implementation/result review returned **APPROVE WITH
  NON-BLOCKING FINDINGS**. Its sole LOW finding was remediated by deriving the
  effective-decision conflict count from the deterministic latest ledger state;
  the targeted delta review returned **APPROVE** with no blocking or
  non-blocking findings.
- Final machine validation passed **243/243**. Exact-head Procurement CI run
  `32435931948` completed **SUCCESS** on reviewed head
  `28370f6176c235391a5682146703326af6f7a96f`; post-merge Procurement CI run
  `32436953358` completed **SUCCESS** on merge/main SHA
  `4d0c12fec29780214b944c6d625faec5cc8a30c5`. The Bushmills regression accepts
  only representations that recompute to the same canonical
  `HistoricalIdentityIndex.source_key`; genuine title, SKU, variant/size, and
  old-Variant-ID changes remain hard stops.
- The final pre-apply dry-run was database-enforced read-only with no assigned
  transaction ID. Controls were exact: 343 unique keys; 341 material and 2
  nonmaterial; 3,112 affected raw rows; 55 MAP, 8 EXCLUDE, and 280
  LEAVE_UNRESOLVED; `MISSING=343`, `LEGACY_COMPATIBLE=0`,
  `CURRENT_PROVENANCE=0`, and `CONFLICT=0`; `SALES_BACKFILL=FAIL`.
- The one serializable production transaction inserted 343 provenanced ledger
  decisions, 8 active exclusions, 17 safe uniform old-ID alias families, and
  343 decision change-log rows. It normalized zero legacy decisions and
  committed 711 total controlled mutations. The transaction's complete
  readback and protected-state assertions passed before commit.
- Fresh-connection read-only reconciliation proved 343/343 effective source
  keys with current manifest provenance: 55 MAP, 8 EXCLUDE, and 280
  LEAVE_UNRESOLVED; all 55 MAP targets populated; 51 distinct targets; zero
  missing targets or conflicting effective decisions; NUTRL Fruit 3/3 maps to
  `41716813627467`; Fiesta target `41193000796235` has zero mappings; and High
  Noon Tequila Variety remains 3/3 LEAVE_UNRESOLVED.
- The active exclusion set is exactly:
  `0||DELIVERY FEE|`; `0||SHIPPING FEES|`; `0||TIP|`; `||TIP|`;
  `0||BUFFALO HOUSE GIFT CARD|BUFFALO HOUSE GIFT CARD`;
  `||BUFFALO HOUSE GIFT CARD|BUFFALO HOUSE GIFT CARD`;
  `41173357133899||BUFFALO HOUSE GIFT CARD|10.00`; and `|||`.
- Protected state was byte-for-byte unchanged before and after persistence:
  `sales_daily` 55,966 rows / `fd2b4e504b492d9e7609ef8642320f7de300f5294369476da0877aee8da8b2e8`;
  raw resolution 59,083 / `06e2726cc33849fc180788fa036a45dcd1b1acd7af32cf813f0ec9311b7dd37a`;
  sales-backfill runs 1 / `d26f1326eea8e16be6626684db5623c291f582a63564e7aeda9c90167507d409`;
  readiness gates 7 / `3e3c67ec4fbf0f29824311b4b97ad77bc20635acc3a2e3822c89c73a3119c21a`;
  purchase orders 0 and lines 0, each with the empty-state SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- The mandatory second dry-run was read-only/no-XID and a true no-op:
  `CURRENT_PROVENANCE=343`; `MISSING=0`; `LEGACY_COMPATIBLE=0`; `CONFLICT=0`;
  decision, exclusion, alias, audit, normalization, and total planned mutations
  all equal zero.
- Historical-sales rebuild/re-resolution was not run. `SALES_BACKFILL` was not
  reevaluated or changed and remains **FAIL**. No readiness gate, Shopify data,
  Vendor Rules, forecasting/procurement data, historical aggregate, raw
  resolution, purchase order, or PO line was changed. Purchase orders remain
  **0** and purchase-order lines remain **0**.

### Repository and tests

- Verified current `origin/main` release baseline:
  `4d0c12fec29780214b944c6d625faec5cc8a30c5` (`Merge PR #13: controlled
  identity-decision persistence`).
- Phase 4 starting HEAD: `90a6b9ec2469d541ff11cb3716807754fd4edb05` (`Add durable Codex and Claude project handoff`). Verified Phase 4 implementation checkpoint: `a78b5808551f3bae584367a631cf25776d3ff038` (`Phase 4 historical sales backfill and reconciliation workflow`).
- Authoritative current test command, run from the repository root:
  `./scripts/procurement-tests`.
- Historical Phase 4 evidence only: the **142/142 PASS** checkpoint used
  `cd procurement && PYTHONPATH=src python3 -m unittest discover -s tests -v`,
  with 0 failures, 0 errors, 0 skips; unittest time 2.410 seconds and measured
  wall time 3.181 seconds on 2026-08-10. That direct unittest command is
  superseded and must not be used for current validation.
- Coverage includes an isolated, fully rolled-back PostgreSQL integration workflow for raw page persistence, interruption durability, mapping/exclusion audit, local aggregate rebuild, restatement, idempotent rerun, durable range resume, conflicting-alias rollback, and independent review of multiple zero-ID identity groups.

### PR #11 deployment reconciliation/hardening closeout — MERGED / DEPLOYED / CLOSED

- Reviewed head:
  `bfbe908189b35a64e6fef91b1839782ded3450b4`; merge SHA:
  `ae902a57ec22ad7f6911a57278f8997de3d0cdd5`; release tree:
  `3bd9063502f782ebd93c3a4ed65130b73220ad62`.
- Pre-merge Procurement CI run #17 (`32252622709`) completed `SUCCESS` with
  **10/10** deterministic deployment-startup tests and **199/199** Procurement
  OS tests. Post-merge Procurement CI run #18 (`32254371003`) also completed
  `SUCCESS`.
- Independent Replit platform review initially returned `BLOCK` on unproven
  startup timing. Exact-head isolated DELTA testing then returned `APPROVE`:
  supervisor launch to Node `/api/healthz` 200 was **713 ms** and **1,483 ms**;
  SIGTERM cleanup was **388 ms**; forced post-readiness FastAPI death failed
  closed and removed Node health within **493 ms**.
- Workspace reconciliation preserved
  `backup/pre-reconcile-production-startup-hardening` at
  `f4a57e57f7fdcdd74225703be6dbb96aff6f2e23`, then reconciled to the exact merge
  tree with a clean working tree. Local validation passed the same **10/10**
  startup tests, the complete **199/199** Procurement suite against a disposable
  loopback PostgreSQL database, and GET-only FastAPI, Node, and
  Node-to-FastAPI health smokes.
- Replit deployment `abcc03bd-9cd4-47fa-8f3e-9b198156c4f9` published
  successfully. Publish checkpoint
  `4bde08152cf958dc97686e01a4f27d83fdb4961f` has the identical release tree and
  is preserved at `backup/post-publish-checkpoint-4bde0815`.
- Fresh production logs proved fail-closed order: the Python supervisor launched
  FastAPI, loopback `GET /health` returned 200, the supervisor logged
  `FastAPI procurement backend is healthy; starting Node API service`, Node
  listened on 8080, and Replit received `/api/healthz` 200. After readiness
  there were no 500/502 responses, connection-refused errors, timeouts, crashes,
  or restarts.
- Direct unauthenticated production checks remained correctly access-limited:
  `/api/healthz` and `/procurement/health` returned 307 to the private Replit
  shield, and no authenticated bypass mechanism was available or attempted.
- Read-only development/production verification found identical schemas and
  application-relation counts, including `variants=2049`, `prices=271`,
  `sales_daily=55966`, `source_facts=59083`, `readiness_gates=7`,
  `purchase_orders=0`, and `purchase_order_lines=0`. Gates were unchanged:
  `CATALOG_SYNC=PASS`, `SALES_BACKFILL=FAIL`, and `VENDOR_RULES=FAIL`.
- This milestone made no identity decision, mapping, exclusion, historical
  rebuild, readiness change, Shopify write, or PO action.
  `procurement/docs/PHASE_STATUS.md` is intentionally unchanged because no
  official phase or program milestone changed.

### Phase 4 historical-review catalog search implementation checkpoint

- Objective: add a small, read-only local catalog search/picker to the existing
  historical-sales human-review page. Search and selection provide evidence and
  populate the existing Canonical Variant ID field; they never decide identity
  or submit the protected mapping form.
- Exact base: `6a833f8318549aaf4b62ff400168b306579b90c6`.
- Branch: `phase4/historical-review-catalog-search`.
- Approved design commit:
  `b9bcf92849bbbad67e1b9fedb229b9b693cae856` (`Design Phase 4 historical
  catalog search helper`).
- Exact implementation/test commit:
  `746b292820b0a77be2fcb6d5933d45e35898cfcd` (`Add Phase 4 historical catalog
  search picker`).
- Implementation: `search_historical_sales_catalog` performs one bounded,
  parameterized local PostgreSQL `SELECT` over stored Variant ID, SKU, barcode,
  product title, variant title, and handle evidence. Query length is limited to
  128 characters, results are capped at 20, SQL wildcard characters remain
  literal, and relevance plus Variant ID provide deterministic order. The
  read-only endpoint is `GET /historical-sales/review/catalog-search?q=...`.
- Target eligibility remains the existing exact-`variants`-membership contract.
  Search exposes `active` and `catalog_state` as evidence and does not invent an
  active-only rule; preserved inactive historical variants remain legitimate
  canonical owners under `HistoricalIdentityIndex`. The permanent mapping path
  already rejects unknown targets and transactionally verifies that the resolver
  reaches the exact requested canonical Variant ID. No mapping-validation defect
  requiring a change was found.
- UI: each unresolved review card has an isolated vanilla-JavaScript picker.
  Results use neutral labels and DOM `textContent`; explicit selection copies the
  exact result ID only into that card's existing mapping field. It does not submit
  a form, call the decision service, create an alias/exclusion, rebuild sales,
  change readiness, or call Shopify. Existing deterministic candidates, source
  evidence, conflicts, materiality, sales impact, reviewer, reason, and review
  token controls remain visible and separate.
- Deterministic result: **198 discovered / 198 executed / 198 passed**, with 0
  failures, 0 errors, 0 skips, 0 expected failures, and 0 unexpected successes.
  Coverage includes literal wildcard/quote/injection probes, every stored search
  field, empty/long/no-result input, result cap/order, inactive-status evidence,
  generic error rendering, exact/card-local selection, no decision or Shopify
  path, and a disposable-PostgreSQL business-state hash proving no search writes.
- Pinned `uv 0.12.3` lock validation passed after resolving 22 packages. Python
  compilation passed for `main.py`, `procurement/src`, `procurement/tools`, and
  `procurement/tests`. `git diff --check`, changed-file secret safety, tracked
  auth/generated-artifact safety, and `origin/main` scope checks passed.
- Exact changed files at this checkpoint:
  `docs/CODEX_HANDOFF.md`,
  `docs/superpowers/specs/2026-08-16-historical-review-catalog-search-design.md`,
  `procurement/src/procurement_os/api.py`,
  `procurement/src/procurement_os/sales.py`,
  `procurement/tests/test_historical_sales_review_api.py`, and
  `procurement/tests/test_phase4_postgres_integration.py`.
- No migration or dependency change was made. Production database access = 0;
  Shopify access = 0; Shopify writes = 0; identity decisions = 0; mappings = 0;
  exclusions = 0; rebuilds = 0; readiness changes = 0; deployments = 0; PO
  actions = 0. `SALES_BACKFILL` remains **FAIL** and all 343 grouped identities
  remain pending human review. `procurement/docs/PHASE_STATUS.md` is unchanged.
- This pre-review implementation boundary is superseded by the completed review,
  owner-authorized PR #9 merge, and post-merge closeout recorded below.

### Phase 4 catalog-search independent-review remediation checkpoint

- Claude independently reviewed exact prior branch head
  `87d347a4efee7e420c2c302de8e34bb09bfd7fe9`, reproduced the complete
  **198/198 PASS** result, and returned **APPROVE** with four LOW findings.
- Owner-authorized narrow remediation accepted only LOW-1 and LOW-4. Exact
  remediation commit before this handoff-only update:
  `59cea38575da99c1c0829a4063273d7db03a983c` (`Harden Phase 4 catalog search
  review tests`). No runtime or business-rule file changed.
- LOW-1 remediated: `REQUIRED_MODULE_MINIMUMS` now matches the final discovered
  counts for `test_historical_sales_review_api.py` (**16**) and
  `test_phase4_postgres_integration.py` (**6**). Their prior floors were 12 and
  4. `GLOBAL_MINIMUM_TESTS` remains derived from the module sum and is now 199.
  An explicit discovery audit proved that deleting any one test from either
  module now violates its required floor.
- LOW-4 remediated with
  `test_unknown_mapping_target_is_rejected_without_partial_persistence`, using
  disposable PostgreSQL and the real
  `record_historical_sales_review_decision`. A nonexistent canonical Variant ID
  raised the exact safe `ValueError` `unknown canonical Variant ID`. Before and
  after values matched for alias, review-decision, change-log, exclusion, and
  unknown-variant counts; the complete `SALES_BACKFILL` readiness row; sales
  aggregates; run state; and the broader business-state hash. No partial write
  occurred, and the existing runtime guard required no change.
- Final deterministic totals: **199 discovered / 199 executed / 199 passed**,
  with 0 failures, 0 errors, 0 skips, 0 expected failures, and 0 unexpected
  successes. Exact final module counts are 16 API-review tests and 6 Phase 4
  PostgreSQL integration tests.
- LOW-2 (weak source-inspection test) and LOW-3 (`business_state_hash` omits
  some run-detail tables) are explicitly deferred as non-blocking test hygiene.
  Independent-review NOTE-1 (unauthenticated read-only catalog exposure),
  NOTE-2 (ordering case asymmetry), and NOTE-3 (prefix-tier writer-test
  coverage) remain accepted observations and were not changed in this narrow
  pass.
- Production database access = 0; Shopify access = 0; identity decisions = 0;
  rebuilds = 0; readiness changes = 0; deployments = 0; PO actions = 0. Phase 4
  remains incomplete, `SALES_BACKFILL` remains **FAIL**, all 343 unresolved
  groups remain untouched, and `procurement/docs/PHASE_STATUS.md` is unchanged.
- This remediation-review boundary is superseded by Claude's DELTA approval,
  ChatGPT business-rule approval, owner merge authorization, and the merged PR #9
  checkpoint below.

### PR #9 post-merge G12 closeout — MERGED / CLOSED

- PR #9, `Phase 4: add local catalog search to historical-sales review`, is
  **MERGED / CLOSED**. The exact reviewed PR head was
  `e597ae5a787e7ec24ea81d82285281da09f770e5`; the exact owner-authorized merge
  commit now on `origin/main` is
  `323702d06c8ba96525e97f9bf94289a164615b73`.
- Pre-merge Procurement CI run #11 (`31967110216`) was **SUCCESS** for pull
  request head `e597ae5a787e7ec24ea81d82285281da09f770e5`. Post-merge Procurement CI run
  #12 (`31967543024`) was **SUCCESS** for pushed main head
  `323702d06c8ba96525e97f9bf94289a164615b73`.
- Final deterministic baseline is **199 discovered / 199 executed / 199 passed**,
  with 0 failures, 0 errors, 0 skips, 0 expected failures, and 0 unexpected
  successes.
- Review and authorization record: Claude broad adversarial review **APPROVE**;
  Claude DELTA review **APPROVE**; ChatGPT business-rule review **APPROVE**; the
  owner explicitly authorized the PR #9 merge.
- The merged helper searches only local PostgreSQL catalog evidence and is
  read-only. It makes no Shopify call, makes no automatic identity decision, and
  preserves the existing explicit human mapping controls and review-token
  protected decision path.
- Deferred non-blocking review hygiene remains: LOW-2, the weak
  source-inspection control; and LOW-3, incomplete table coverage in
  `business_state_hash`. Neither deferred item changes accepted runtime behavior
  or the merge disposition.
- This merge does not complete Phase 4. `SALES_BACKFILL` remains **FAIL**; 343
  grouped identities, including 341 material groups, remain pending human review.
  This milestone made no identity decision, ran no rebuild/re-resolution, changed
  no readiness gate, deployed nothing, and started no downstream phase or
  post-foundation workstream.
- `procurement/docs/PHASE_STATUS.md` remains unchanged because PR #9 did not close
  or materially change the canonical Phase 4 milestone.

### PR 4a deterministic CI/tooling closeout

- PR #5 / PR 4a is **MERGED / CLOSED**. The exact reviewed head was
  `c04b923f57f0c38411d4e6509163fd7734ef681d`; the owner-approved merge commit
  on `main` is `8d8a07a082a575ef35c6b37ecb6dedc7f47cbbaf`.
- Pre-merge GitHub Procurement CI run #3 was **SUCCESS** on the exact reviewed
  head `c04b923f57f0c38411d4e6509163fd7734ef681d`. Post-merge GitHub Procurement
  CI run #4 was **SUCCESS** on the exact `main` merge commit
  `8d8a07a082a575ef35c6b37ecb6dedc7f47cbbaf`.
- PR 4a and this documentation-only closeout do not complete or alter a
  procurement phase. Phase 4 remains incomplete.
- Baseline at `678a689`: **142/142 PASS**, 0 failures, 0 errors, 0 skips.
- Post-closeout regression evidence: the current complete deterministic suite is
  **160 discovered / 160 executed / 160 passed** on Python 3.13.11 and
  disposable local PostgreSQL 16.9, with 0 failures, 0 errors, 0 skips, 0
  expected failures, and 0 unexpected successes.
- Runner self-tests: **18/18 PASS**. They prove fail-closed handling for expected
  failures, unexpected successes, skips, missing required modules, deficient
  per-module counts, unregistered on-disk test modules, discovery/execution
  mismatch, non-loopback URLs, non-test database names, unsafe URL/libpq routing
  inputs, inherited runtime database isolation, connected-database mismatch, and
  PostgreSQL-major mismatch. The runner self-test module is itself registered at
  its 18-test minimum.
- CI parity is Python 3.13 only, uv 0.12.3, and the immutable PostgreSQL 16 image
  `postgres:16@sha256:95206741a5b214807675e14165369d05b93a9cf692223b616d07cca227e74b0b`.
  The image was independently pulled and reported PostgreSQL 16.14. `uv lock
  --check`, Python compilation, shell syntax, TOML parsing, YAML parsing/format,
  diff whitespace, changed-file secret safety, and origin/main scope checks pass.
- PR 4a scope proof: the branch changed only CI/tooling, runner self-tests, runtime and
  dependency metadata, Procurement test-command/setup documentation, and this
  handoff. There are zero changes under `procurement/src/`, `procurement/db/`,
  `procurement/config/`, or `procurement/docs/PHASE_STATUS.md`. There are zero
  procurement business-logic, API-behavior, migration, Shopify, F4, or PR 4b
  changes; no program/phase milestone changed.
- G12 closeout scope proof: only `docs/CODEX_HANDOFF.md` and `replit.md` change;
  `procurement/docs/PHASE_STATUS.md` remains unchanged.
- Non-blocking future tooling follow-up: the current test-module registration
  invariant assumes a flat `procurement/tests/test_*.py` layout. Nested test
  directories are not yet protected by that completeness invariant.
- The historical PR 4a authorization boundary is superseded by the merged PR 4b
  checkpoint and post-merge closeout below.

### PR 4b post-merge G12 closeout — MERGED / CLOSED

- PR #7 / PR 4b, `PR 4b: Harden authoritative catalog and scoped readiness`, is
  **MERGED / CLOSED**. The exact reviewed PR head was
  `302a14673ce01bf130f28f66743e74c935ae4a03`; the owner-authorized merge
  commit now on `origin/main` is
  `527498ce39dfa504c32916b16478cbe02dc6781c`.
- Pre-merge GitHub Procurement CI run `31925468640` was **SUCCESS** on the exact
  reviewed head. Post-merge GitHub Procurement CI run `31925753302` was
  **SUCCESS** on the exact merge commit.
- Final deterministic totals were **193 discovered / 193 executed / 193
  passed**, with 0 failures, 0 errors, 0 skips, 0 expected failures, and 0
  unexpected successes.
- Independent review disposition: Claude Code DELTA review **APPROVE**; Cursor
  targeted specialist review **APPROVE**; ChatGPT business-rule review
  **APPROVE**. The owner explicitly authorized the merge, and PR #7 merged
  successfully.
- This merge and documentation-only G12 closeout did not complete Phase 4.
  `SALES_BACKFILL` remains **FAIL**; all 343 unresolved historical identity
  groups, including 341 material groups, remain for authenticated human review.
- No deployment, Shopify access or write, catalog sync, PO generation or
  release, identity decision, or PR 4c work occurred as part of this closeout.
  `procurement/docs/PHASE_STATUS.md` remains unchanged because no official
  phase/program milestone changed.

### PR 4b authoritative catalog/readiness hardening implementation checkpoint

- Objective: F1 authoritative catalog-run semantics plus owner-approved F4
  scoped readiness semantics.
- Branch: `hardening/pr-4b-readiness-catalog`.
- Exact base: `d90f7313fc6048697ef74553c3895a88e9ac8a04`.
- Implementation commit:
  `fa848cde427b405838dc6401350487718671ffe4` (`Implement scope-aware readiness
  and catalog authority`).
- This implementation checkpoint is now contained in merged PR #7. The merge
  does not authorize deployment or PR 4c.
- F1 result: there is one authoritative newest-attempt catalog selector,
  ordered by `started_at DESC, catalog_sync_id DESC`. It never falls back to an
  older successful run; an incomplete or failed newest attempt fails closed;
  and `CATALOG_SYNC` can pass only when all implemented deterministic catalog
  controls pass.
- F4 result: `FAIL` blocks the affected/applicable scope; `WARN` remains
  non-blocking; missing applicable required evidence fails closed; unrelated
  vendor/variant failures do not create global blocks; exception scope matching
  is conjunctive; and existing global required failures still block.
- Exact completed G4 test totals:
  `discovered=186; executed=186; passed=186; failures=0; errors=0; skips=0;
  expectedFailures=0; unexpectedSuccesses=0`. All 19 registered test modules
  met their per-module minimums, with no missing or unregistered test module.
- Dependency control used the repository-persistent pinned executable, which
  reported `uv 0.12.3 (x86_64-unknown-linux-gnu)`; `uv lock --check` exited 0
  after resolving 22 packages.
- Python compilation passed for `main.py`, `procurement/src`,
  `procurement/tools`, and `procurement/tests`. Working-tree and
  base-to-implementation `git diff --check` controls passed. All 1,036 added
  lines passed the changed-file secret scan; no auth state was tracked; and no
  unintended cache, bytecode, log, temporary, build, dependency, or generated
  artifact was tracked.
- Required PR 4b A-Q adversarial coverage is complete and deterministic.
- Additional F4 guardrails passed: an existing global `VENDOR_RULES` `FAIL`
  still blocks; undeclared missing `VENDOR_RULES` is not a universal blocker;
  declared-applicable missing `VENDOR_RULES` fails closed; and all relevant
  status/API consumers use the same authoritative `catalog_sync_id`.
- G4 used only disposable loopback PostgreSQL test infrastructure. There was
  zero production database access or write and zero Shopify access or write.
  PR 4b made no migration, no identity decision, no PO generation or release,
  and no deployment.
- Phase 4 remains **INCOMPLETE**. The 343 historical identity decisions remain
  untouched, and `SALES_BACKFILL` remains operationally outstanding. No
  official phase/program milestone changed, and
  `procurement/docs/PHASE_STATUS.md` remains unchanged.

### PR 4b G7 review remediation checkpoint — MERGED / CLOSED

- Independent Claude review returned **REQUEST CHANGES** against exact reviewed
  head `d978ab17e601fdc317e8e0b7a5da34b26f03afcc`.
- Exact remediation implementation commit/head before this handoff-only update:
  `b30b2706fe065373e6ad6ec4d5dfda481678e3f3` (`Remediate PR 4b review
  findings`). Its parent is the exact reviewed head; no commit was amended or
  rebased.
- Accepted HIGH-1: Shopify `productVariantsCount` drift is a control statistic,
  not authoritative catalog data and not a `CATALOG_SYNC` blocker. Pagination
  plus the independent active-product `variantsCount` verification remains
  authoritative. Reported count, mismatch, and delta remain explicit diagnostic
  evidence; they do not produce `WARN` or block readiness. All other authorized
  deterministic catalog controls remain fail-closed.
- Accepted HIGH-2: the divergent latest-`COMPLETED` selectors were removed from
  `tools/run_identity_investigation.py` and
  `tools/diagnose_count_discrepancy.py`. Both now use the centralized newest-
  attempt evaluator. Missing, failed, running, or structurally incomplete newest
  attempts are refused without older-success fallback. A structurally complete
  run may still be investigated when unresolved catalog identities are its only
  readiness blocker. The selector-uniqueness guard now scans runtime/tooling
  Python across `procurement/`, excluding tests, with case/whitespace-tolerant
  SQL detection.
- MEDIUM-1 was rejected as a defect: owner-approved exception matching remains
  conjunctive. A combined vendor/variant exception does not block vendor-only
  scope; it blocks when both dimensions match; unrelated dimensions remain
  isolated. The caller contract now states that future final-PO logic must
  evaluate each applicable line with vendor and variant scope. No PO engine was
  added.
- MEDIUM-2 was preserved and documented: a run-scoped HIGH/CRITICAL exception
  blocks its matching run, not another run and not a global request without a
  run. A truly global HIGH/CRITICAL exception still blocks global status.
- Accepted MEDIUM-3: unsupported readiness `scope_type` values now raise a clear
  `ValueError` before a readiness result can be produced.
- Accepted LOW-2 narrowly: `None`, non-string, blank, whitespace-only, and bare
  string applicable-gate inputs are rejected instead of being stringified or
  iterated into misleading gate names.
- Not addressed by authority: LOW-1 unresolved-count drift beyond existing
  diagnostics, LOW-3 query optimization, and LOW-4's pre-existing
  `AMBIGUOUS_IDENTITY` schema-constraint issue. LOW-4 remains a future
  pre-existing issue. No PR 4c identity/matching, pricing, migration,
  vendor-rule, forecasting, procurement, PO, or Shopify-write scope was added.
- Exact final remediation test totals:
  `discovered=193; executed=193; passed=193; failures=0; errors=0; skips=0;
  expectedFailures=0; unexpectedSuccesses=0`. All registered modules met their
  updated per-module minima with no missing or unregistered module.
- Pinned `uv 0.12.3` lock validation passed; Python compilation passed for
  `main.py`, `procurement/src`, `procurement/tools`, and `procurement/tests`;
  `git diff --check` passed; 269 added remediation lines passed the changed-file
  secret scan; and no auth state or generated artifact is tracked.
- The sole authorized production verification used this exact SQL in one
  database-enforced read-only transaction:

  ```sql
  BEGIN TRANSACTION READ ONLY;
  WITH authoritative AS (
    SELECT catalog_sync_id, started_at, completed_at, status,
           pagination_complete, source_hash, live_rows_received,
           exact_current_ids, new_live_variants,
           shopify_reported_variant_count, unresolved_count
    FROM catalog_sync_runs
    ORDER BY started_at DESC, catalog_sync_id DESC
    LIMIT 1
  )
  SELECT current_setting('transaction_read_only') AS transaction_read_only,
         a.catalog_sync_id,
         a.started_at AT TIME ZONE 'UTC' AS started_at_utc,
         a.completed_at AT TIME ZONE 'UTC' AS completed_at_utc,
         a.status,
         a.pagination_complete,
         (a.source_hash IS NOT NULL AND btrim(a.source_hash) <> '')
           AS source_hash_present,
         a.live_rows_received,
         a.exact_current_ids,
         a.new_live_variants,
         a.shopify_reported_variant_count,
         a.unresolved_count AS recorded_unresolved_count,
         (SELECT COUNT(*)
            FROM catalog_reconciliation_items cri
           WHERE cri.catalog_sync_id = a.catalog_sync_id
             AND cri.blocking = TRUE
             AND cri.resolved_at IS NULL)
           AS live_unresolved_blocking_items
    FROM authoritative a;
  COMMIT;
  ```

- Production result: `transaction_read_only=on`;
  `catalog_sync_id=7e3ebb8b-a204-43fe-8304-fe3a21216a68`;
  `started_at_utc=2026-08-10 14:55:53.619347`;
  `completed_at_utc=2026-08-10 14:55:53.634178`; `status=COMPLETED`;
  `pagination_complete=true`; `source_hash_present=true`;
  `live_rows_received=1999`; `exact_current_ids=1999`;
  `new_live_variants=0`; `shopify_reported_variant_count=2003`;
  `recorded_unresolved_count=0`; `live_unresolved_blocking_items=0`.
  The remediated evaluator therefore remains `PASS` and exposes the +4
  reported-count drift diagnostically.
- The production transaction made zero writes. No readiness gate was updated,
  no catalog sync was run, and there was zero Shopify access and zero Shopify
  writes.
  Remediation made no migration, identity decision, PO generation/release, or
  deployment.
- Phase 4 remains **INCOMPLETE**. All 343 historical identity decisions remain
  untouched, `SALES_BACKFILL` remains operationally outstanding, no official
  phase milestone changed, and `procurement/docs/PHASE_STATUS.md` remains
  unchanged.

### Phase 3 catalog checkpoint

- `CATALOG_SYNC` is **PASS**, last checked `2026-08-10T14:55:53.634178Z`.
- Independently verified ACTIVE Shopify catalog: **1,999 variants**. The unfiltered Shopify-reported count remains 2,003 because it also includes four inactive variants.
- Current variants: 2,049 total = 1,999 `LIVE`/active + 46 `RETIRED_CONFIRMED`/inactive + 4 historical inactive-as-expected (`SEEDED`/inactive, archived in Shopify).
- The 46 retirements remain individually audited. Phase 4 did not change catalog identity or retirement decisions.

### Phase 4 implementation and live run

- ShopifyQL access probe: **PASS** using configured Admin API `2026-07`; store timezone is `America/New_York`. No customer dimensions or Orders API fallback were used.
- Additive migration `procurement/db/006_phase4_sales_backfill.sql` is applied. It adds durable run/chunk/page checkpoints, run-to-fact observations, restatement evidence, complete control fields, and append-only historical-sales review decisions.
- Live run: `d389079c-eabf-49b5-a245-40a207025fd7`, started `2026-08-10T16:44:26.811525Z`, completed `2026-08-10T16:45:59.804015Z`.
- Requested range: **2024-11-28 through 2026-08-10** (current store-local date at execution).
- Coverage: **21/21 date chunks**, **70/70 structurally contiguous pages**, all pages/chunks complete; no parse error, duplicate observation, missing chunk, or coverage gap. Current-code local finalization re-proved page indexes, offsets, terminal-page structure, stored range, and run-creation date evidence without refetching Shopify.
- Durable source: **59,083 source rows = 59,083 unique natural facts**.
- Resolution: **55,971 resolved rows**, **3,112 unresolved rows**, **0 ambiguous rows**, **0 explicitly excluded rows**.
- The former owner review queue contained **343 unresolved identity groups**
  ranked by materiality (**341 material**, 2 zero-impact but retained). All 343
  owner decisions are now durably persisted, but source rows have not been
  re-resolved and the canonical aggregate has not been rebuilt.
- Browser review UI: `/procurement/historical-sales/review` (FastAPI route `/historical-sales/review`); JSON: `/procurement/historical-sales/review/items`. Decisions require actor, reason, and `RECONCILIATION_REVIEW_TOKEN`.
- Shopify source totals exactly equal persisted raw totals:
  - net items: **82,501.0000** source = **82,501.0000** raw;
  - net sales: **$1,300,975.14** source = **$1,300,975.14** raw.
- Canonical resolved totals: **78,815.0000 net items** and **$1,231,372.83 net sales**.
- Unresolved totals: **3,686.0000 net items** and **$69,602.31 net sales**; materiality is **3,696.0000 absolute units** and **$72,616.29 absolute sales**.
- Pre-rebuild excluded totals remain **0 items / $0.00**. There are now 343
  effective review decisions and exactly 8 active historical exclusions; they
  have not yet been applied to historical source resolution.
- Coverage, source persistence, idempotency, source/raw controls, resolution accounting, and canonical controls all reconcile. The canonical aggregate was rebuilt from this run's durable facts.
- Phase 4 workflow implementation, initial live fetch, owner-decision
  persistence, and independent persistence review are complete, but **Phase 4
  remains incomplete until separately authorized historical
  re-resolution/rebuild and gate reevaluation are complete**.

### Current readiness and safety state

| Gate | Current status | Notes |
| --- | --- | --- |
| `CATALOG_SYNC` | `PASS` | Phase 3 catalog remains reconciled. |
| `SALES_BACKFILL` | `FAIL` | Owner decisions are persisted; historical re-resolution/rebuild and gate reevaluation have not run. |
| `VENDOR_RULES` | `FAIL` | Phase not started. |

- PO generation is **disabled**, blocked by `SALES_BACKFILL` and `VENDOR_RULES`; purchase-order count remains zero.
- Shopify remained strictly read-only. Phase 4 stored no customer fields/PII and made zero Shopify writes.
- Only the exact owner-approved identity decisions, exclusions, and safe uniform
  old-ID aliases were persisted; no automatic identity decision occurred.

## Historical checkpoint

- Phases 0–2 are complete; the historical seed contains 2,029 identities.
- Pre-retirement Phase 3 had 1,979 exact active historical/current IDs, 20 genuinely new active variants, 46 deleted historical identities, and 4 inactive-as-expected identities.
- Exact lookup plus deterministic continuity review found no credible current counterpart for all 46 deleted identities; human-authorized retirement was executed and audited before the successful post-retirement catalog sync.

## Authorization boundary / next action

Owner decisions are persisted, independently reviewed, and merged under PR #13.
The exact next action is to obtain **separate owner authorization for Phase 4
historical-sales re-resolution/rebuild and subsequent `SALES_BACKFILL` and
readiness-gate reevaluation**. Until that authorization is granted, do not
rebuild or re-resolve historical sales, reevaluate or change any readiness gate,
query or write Shopify, begin Vendor Rules/forecasting/procurement work, or
create/release any PO.
