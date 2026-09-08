# Buffalo Procurement OS — Codex Handoff

**Updated:** 2026-09-08T03:43:01Z (UTC)

**Phase numbering:** This handoff follows `procurement/docs/authority/03_REPLIT_BUILD_EXECUTION_PROMPT_v2_1.md`: Phase 3 is catalog reconciliation and Phase 4 is historical ShopifyQL sales backfill/reconciliation.

This is an operational checkpoint, not a replacement for the canonical specification or `procurement/config/rules.toml`. Verify repository and database state again before acting.

**Operating process:** every coding/review/release session must follow `docs/PROJECT_GOVERNANCE.md`. At each meaningful milestone, this handoff must be refreshed with verified state, tests, readiness gates, material counts/control totals, open risks/decisions, Git reference, and exact next authorization boundary.

## Verified current state

### Emergency Monday offline draft-PR/CI checkpoint — NOT PRODUCTION READY

- Independent review originally returned REQUEST CHANGES on implementation commit
  `4b342cf67ec1d488a2f84433042a468609624d84`, tree
  `01a451f66ca8ee8d3aaad57090d00de201f30a1c`. The branch entered remediation at
  documentation head `3aed22dd09f951b27f30e04517fd3fd104f99dcd`, tree
  `8574d92621d8f265deba5f740d49f94ab84920c9`.
- Exact reviewed and tested source candidate:
  `e59ea665408cb881f25cff995cc2a6957fa59f94`, tree
  `e528fa3ff9cc7a3e13758075c7b8e98b3d5a2dce`, on
  `codex/emergency-monday-procurement-mvp`. The material P1 implementation commit
  within that candidate is
  `dda6b0986710f032f05f50273527af160cacde5c`, tree
  `6fc845669afa056854b9306d3cf05074fa57afe3`. That exact reviewed source
  candidate inherited the pre-authorized emergency history from `1920a16`
  before the ordinary Packet A integration of exact `main`
  `f308ac666a2377f540e528bc873463daecc20cf8` into this draft-PR branch.
- P1-1 is fail-closed with one full requested-variant manifest/fingerprint
  across prepare/replay/preview/confirm/build. Original blocked-input exceptions
  remain immutable; an append-only, exact-fingerprint RUN_ONLY
  `ACKNOWLEDGE_AND_EXCLUDE` action records actor/timestamp/reason and never
  changes global facts or creates eligibility. REJECT remains separate.
- P1-2 uses the expressly authorized simpler fallback: one active RUNNING
  Monday Procurement run per business date, backed by service precheck,
  PostgreSQL uniqueness, and DRAFT/RUNNING lifecycle triggers. A failed
  pre-build run releases the date; a built DRAFT keeps it. Supersession was not
  implemented and DRAFT remains excluded from trusted incoming.
- P1-4 restores vendor scope: incomplete vendors keep VENDOR FAIL/blocking
  evidence while mixed global coverage is WARN/nonblocking. A genuine global
  PASS is accepted, but WARN cannot replace missing vendor evidence. Migration
  013 repairs legacy persisted summary state without inventing vendor PASS.
- P1-5 uses one temporary owner-reviewable policy in `rules.toml`: strict-above
  `2.0x` frozen raw baseline units or strict-above `30.0` resulting days of
  supply is MATERIAL. Exactly-at values remain NORMAL; positive edits from zero
  baseline/forecast are MATERIAL; no quantity is capped. Material edits require
  a separate append-only confirmation bound to the exact run, recommendation,
  input/preview fingerprints, and quantities. Actor, database timestamp, and
  reason are audited; immutable evidence records the policy, inventory/days,
  and incremental/final cash. Python and SQL independently enforce the same
  rounded classification.
- P1-3 remains an explicit owner decision. No positive loose-fee meaning was
  guessed. Any required positive-fee loose quantity becomes
  `LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED`; Python/SQL/upgrade guards reject it.
  Case-only orders and confirmed zero-fee loose orders may continue when all
  other facts pass.
- The emergency service remains `INTERNAL_DRAFT_ONLY`. Seven Monday route/method
  pairs cover list, prepare, detail, blocker exclusion, review, DRAFT build, and
  artifact download; none provides FINAL, release, import, Shopify, supplier
  transmission, or production mutation. Review-token equality authorizes the
  operation but does not authenticate the caller or entered actor; verified
  private caller authentication remains a production prerequisite.
- Exact implementation validation provisioned/destroyed loopback PostgreSQL
  16.9 `procurement_test`: Monday `54/54`; readiness `21/21`; vendor rules
  `16/16`; PO ledger compatibility `35/35`; affected surface `218/218`; full
  authoritative suite discovered/executed/passed `587/587` in 842.923 seconds.
  Failures, errors, skips, expected failures, and unexpected successes were all
  `0`, and all 33 registered module floors passed. Startup hardening passed
  `10/10`; compilation, pinned `uv 0.12.3` lock, shell syntax, diff, secret,
  generated/binary, and forbidden-runtime-call scans passed.
- Hard-coded independent boundaries prove `5/6/7` units from baseline 3 are
  NORMAL/NORMAL/MATERIAL at `1.6667x/2.0000x/2.3333x` and `$50/$60/$70` final
  cash; `29.99/30.00/30.01` days are NORMAL/NORMAL/MATERIAL; raw `30.004`
  rounds to `30.00` consistently; and a `1000x` edit is MATERIAL at
  `$30,000.00` final / `$29,970.00` incremental cash.
- Claude independently re-reviewed the completed candidate and returned
  **APPROVE WITH NONBLOCKING FINDINGS**. Its independent validation passed the
  full suite `587/587` and startup hardening `10/10`; failures, errors, skips,
  expected failures, and unexpected successes were all `0`. ChatGPT accepts
  this as code-review evidence, not as merge, production, or policy approval.
- The preserved nonblocking findings are: **N-1**, the database guard contains
  the exact exclusion `run_id` check but the Python-side defense-in-depth check
  is absent; **N-2**, material confirmation is a distinct action and not a
  second-person requirement; **N-3**, days-of-supply classification uses the
  canonical rounded value; **N-4**, material-policy changes invalidate in-flight
  runs; **N-5**, a targeted positive CASE-minimum arithmetic assertion remains
  to be added; and **N-6**, confidential Monday GET/list/detail/download
  surfaces still need verified caller protection.
- Codex separately completed the authorized local started-server/browser
  acceptance on the exact source candidate using synthetic data, loopback
  PostgreSQL 16 `_test` resources, Uvicorn, and Chromium. All `48/48` browser
  assertions and `144/144` database/download/ZIP assertions passed. The one
  DRAFT and one line reconcile `$20.02` merchandise plus `$5.00` fee to
  `$25.02`; two artifacts and one packet event were preserved; the packet has
  12 entries with a valid internal manifest; replay was idempotent; and the
  server, browser, listeners, and disposable database were stopped.
- That evidence remains host-local and outside Git at
  `/home/runner/workspace/.ai-auth/codex/evidence/monday-started-server-e59ea665-20260907T180925Z`.
  `SHA256SUMS_TEST_DATA.txt` contains 53 records covering every other retained
  file and has SHA-256
  `fe6d601746de858f733731ee2834b9134b84735d243d98269624a14ffec01cd0`;
  `ACCEPTANCE_REPORT_TEST_DATA.md` has SHA-256
  `b4bad03c7f885221e0594c7163a74cecb2bf2e598e654e7589813cb845fe7887`.
  Off-host backup is not proven. The deleted pre-remediation `/tmp` sample is
  not current evidence.
- Owner decisions remain required for the temporary `2.0x`/`30.0 days` policy,
  positive loose-fee application semantics, and any future supersession design.
  Real current sales, same-day inventory, open orders, vendor facts,
  mappings/packs, and CURRENT prices remain unproven. This week's orders are
  explicitly out of scope and are being handled separately by the owner.
- Deferred P2 items remain explicit: trusted-write-role artifact semantics,
  human-interpreted blackout/special-rule text, synthetic `_test` sales fixture
  authority, native Shopify CSV, App Storage/private access/auth,
  Nix/runtime/dependencies, backup/restore, real migrations, production/private
  browser and shadow-mode acceptance, and disabled strategic forward buying.
  The accepted local started-server/browser exercise closes only that offline
  acceptance gap.
- This remediation changes no formal phase completion. Published-production
  Phase 4 remains OPEN and formal Phase 6 remains owner-authorized but PAUSED.
  Production database connections/writes: `0 / 0`; Shopify calls/writes:
  `0 / 0`; FINAL/release/transmission/real-money actions: `0`.
- **Exact next authorization boundary:** keep PR #23 draft, obtain the existing
  configured CI on the history-preserving integration if it passes local
  integration validation, then stop for ChatGPT PR/CI review. This is an
  **OFFLINE DRAFT-ONLY FOUNDATION — NOT PRODUCTION READY**. No merge,
  deployment, republish, production connection, Shopify action, supplier
  communication, or PO release is authorized.

### G9 read-only published-production preflight mode — IMPLEMENTED / AWAITING INDEPENDENT REVIEW

- G9 began from authoritative `main`
  `1920a16a6dc13a1b4357315f5049b938cbe7c0e2`, tree
  `8de8b332ff71e353a359d8f02bf2c5a4f76cf446`, on dedicated branch
  `codex/phase4-production-preflight-mode`. Implementation commit
  `353acb72f9184523ce6327c70426b020574c8885`, tree
  `52810e629d9c8fb3142672c27d18c1ae3f08148e`, changes only the corrective
  executor, its immutable bootstrap, the focused corrective test module, and
  the fail-closed test-count floor.
- The executor now accepts an explicit, non-abbreviated `--preflight-only`
  mode. It still completes the existing deployment, authorization, manifest,
  clean-Git, exact-SHA/tree, and database-target checks, then uses the existing
  read-only classifier. It succeeds only for exact
  `A_FROZEN_PRODUCTION_BASELINE`; States B, C, D, E, partial, drifted, or
  unknown states fail closed. The branch returns before construction or entry
  of the mutation-stage loop.
- A fresh `REPEATABLE READ, READ ONLY` snapshot re-attests exact State A and
  reports the database identity, NULL transaction IDs before and after the
  evidence reads, all six frozen protected fingerprints, all seven canonical
  readiness rows, exact migration markers through 006, migration 007 absent,
  no partial terminal schema, exact PRE-007 semantic hash
  `cf7e091c334c3a78e9ced12731025b2b7d08529cdf34e6cbe3818b85df36253a`,
  State-A source/resolution/sales/decision/exclusion/alias counts, zero
  residual `phase5_ui_%` schemas, and purchase orders / lines **0 / 0**.
  Output contains only presence booleans for the two review-token environment
  names and for `DATABASE_URL`; it never emits their values or claims that the
  URL was Replit-injected.
- Structural dispatch tests prove the original-manifest, migration-007,
  terminal, terminal-no-op, rebuild, and canonical sales re-resolution/
  finalizer paths are unreachable in preflight mode. Real disposable-database
  tests additionally prove exact before/after database snapshots, stable
  readiness/fingerprints, NULL XIDs, stale-classification rejection, and
  failure before mutation for States B-E and material count, fingerprint,
  marker, semantic-schema, and residual-fixture drift.
- The reviewed bootstrap keeps the exact immutable Nix interpreter and Git/
  PATH isolation boundary. Its original two-argument behavior is unchanged.
  A third argument is accepted only when it is literally `--preflight-only`;
  malformed, abbreviated, environment-selected, or additional arguments fail
  before clone or Python execution. The flag is forwarded exactly once as a
  source-coded literal, never through `$@`, `eval`, PATH discovery, an
  interpreter fallback, or a caller-controlled environment value.
- Deterministic validation passed: complete corrective module **70/70**;
  complete Phase 4 suite **212/212**; Phase 5 **22/22**; startup hardening
  **10/10**; and the authoritative full suite **397/397**, with failures,
  errors, skips, expected failures, and unexpected successes all exactly zero.
  Pinned `uv 0.12.3` lock verification, Python compilation, `/bin/sh` bootstrap
  syntax, `git diff --check`, added-line credential-pattern review, and tracked
  generated-artifact/cache scan also pass. All PostgreSQL validation used an
  explicitly validated disposable loopback PostgreSQL 16.9 database named
  `procurement_test`; ordinary production `DATABASE_URL` was never fixture
  authority.
- This implementation has not been run in a Scheduled Deployment or against
  published production. Actual deployment ID/status, Replit injection
  provenance for `DATABASE_URL`, immutable-Nix viability in that deployment
  image, and actual `neondb` State-A evidence therefore remain **STOP /
  UNPROVEN** until a separately authorized runtime preflight. Production
  database connections/writes: **0 / 0**. Shopify calls/writes: **0 / 0**. PO
  actions: **0**. Published-production Phase 4 remains **OPEN** and Phase 6
  remains **OWNER AUTHORIZED but PAUSED**. The separate paused
  `codex/emergency-monday-procurement-mvp` workstream remains untouched by G9.
- **Historical G9 boundary:** this section records the main-side G9 checkpoint
  merged into the draft-PR branch. It does not authorize G10 invocation,
  deployment, production connection, correction execution, Shopify access, or
  any PO action.

### PR #20 post-merge checkpoint — MERGED / CI PASS / RELEASE PREFLIGHT PENDING

- Corrective published-production reconciliation PR
  [#20](https://github.com/Vedang1998/Buffalo-Procurement-System/pull/20)
  merged successfully using a normal two-parent merge commit. Pre-merge
  `main` was `631bd95e2680b1fcdba80a39f52669d83c8e93ac`; the approved PR head was
  `946623bc59fbfa8b0c6abce7b5f1bf9c35ac2691`; and that PR's merge commit
  was `74d864ab46df3bdd0f5aede510aa0c6d62ffbfeb`, tree
  `ce728ec4016cd68be63c1563453838427f556579`.
- Exact-head pull-request CI run `34006299559` was **completed / success** on
  the approved PR head. Exact post-merge `main` push CI run `34007212711` was
  also **completed / success** on the merge commit. Startup hardening passed
  **10/10** and the full deterministic Procurement suite passed **385/385**,
  with failures, errors, skips, expected failures, and unexpected successes
  all exactly zero.
- Corrective implementation, authority, independent review, merge, and CI
  gates are complete. Published-production Phase 4 remains **OPEN** because
  production execution and independent post-action reconciliation have not
  occurred. Phase 6 remains **OWNER AUTHORIZED but PAUSED**.
- Production database connections/writes so far: **0 / 0**. Shopify
  calls/writes: **0 / 0**. PO actions: **0**.
- **Exact next action:** ChatGPT-controlled final published-production release
  preflight before any mutation. That preflight must verify, without exposing
  secret values:
  1. exact reviewed `main` SHA/tree and successful CI;
  2. the production Scheduled Deployment environment supports the exact
     reviewed bootstrap;
  3. the exact approved Nix Python exists and passes every reviewed
     non-writability, type, and executable check;
  4. `RECONCILIATION_REVIEW_TOKEN` and `PHASE4_REVIEW_TOKEN_INPUT` are present;
  5. no manually defined production `DATABASE_URL` exists and Replit injects
     its environment-specific production URL;
  6. the actual target is `neondb`, PostgreSQL 16, schema `public`, and the
     first identity query has a NULL transaction ID;
  7. published production still matches the frozen pre-correction State A
     counts and fingerprints;
  8. migration markers are the exact pre-007 set;
  9. migration 007 is absent;
  10. the actual PRE-007 semantic signature is exactly
      `cf7e091c334c3a78e9ced12731025b2b7d08529cdf34e6cbe3818b85df36253a`;
  11. purchase orders / lines remain **0 / 0**.
  Any mismatch must stop before mutation.

### Historical G8 PR CI timeout remediation — SUPERSEDED BY SUCCESSFUL EXACT-HEAD CI AND MERGE

- Corrective published-production reconciliation PR
  [#20](https://github.com/Vedang1998/Buffalo-Procurement-System/pull/20)
  was opened from `codex/phase4-published-production-reconciliation` at the
  independently approved head
  `15129704817cc48c1abc5d267f4dc4b45f3cc9d3`, tree
  `dee3f4d3257a637360e3630f737dd72a9e553573`, against exact `main`
  `631bd95e2680b1fcdba80a39f52669d83c8e93ac`.
- Exact-head pull-request CI run `34004231407` was **completed / cancelled**
  on both attempts because the workflow's fixed 10-minute job timeout was
  insufficient for the expanded deterministic suite. Attempt 1 nevertheless
  completed the authoritative runner and emitted **385/385 PASS**, zero
  failures, errors, skips, expected failures, or unexpected successes, and
  `OK` in 583.503 seconds immediately before GitHub cancelled the overall job.
  Attempt 2 reached the same job timeout without reporting a test failure.
  The formal GitHub CI gate therefore remains **UNSATISFIED**; the cancelled
  run is not accepted as passing CI.
- The only workflow change raises `timeout-minutes` from **10** to **20** to
  accommodate normal hosted-runner variance. The suite, job structure,
  dependency lock, startup hardening, and all material corrective runtime,
  database, bootstrap, migration, fixture, identity, readiness, Shopify, and
  PO implementation files remain byte-identical to the independently approved
  head above.
- Production execution remains unauthorized. Production database
  connections/writes: **0 / 0**. Shopify calls/writes: **0 / 0**. PO actions:
  **0**. Published-production corrective Phase 4 remains **OPEN**; Phase 6
  remains owner-authorized but **PAUSED**.
- **Exact next action:** fresh pull-request CI on the workflow/docs-only head,
  followed by ChatGPT verification of the narrow delta, exact-head CI, and
  byte-identical material implementation before any merge authorization.

### G7 semantic-signature provenance remediation — IMPLEMENTED / AWAITING INDEPENDENT RE-REVIEW

- Independent adversarial review returned **REQUEST CHANGES** on exact head
  `8b23e1a7bf9ee107693b13cd5b2e2a7df7a1f81b`, tree
  `312392f59558f9307f3394cde5f1c853d6b609ce`. The blocker was confined to
  semantic-signature provenance: the previous test authority began from the
  consolidated current schema and surgically removed terminal structures.
  That synthetic history retained dropped-column `attnum` holes and also
  reconstructed the two pre-007 price views with an `o.active` condition not
  present in the genuine historical schema. The rejected pre/post hashes are
  no longer authority.
- Corrective implementation commit
  `fa8f07d0860889250270f6aeb23813cf9d9ed231` replaces that source with the
  self-contained byte-exact fixture
  `procurement/tests/fixtures/schema_postgres_pre_terminal_198b213e.sql`.
  Its authority is commit
  `198b213e9b8f733e4cc76e568e91697d187e817f` (the sole parent of terminal
  implementation `326ad7659f41e63c9353e9372e7f67b94af47357`), Git blob
  `4f7dc2517f373513f132e1bf40970986bc607e55`, 22,818 raw bytes, and raw
  SHA-256
  `d5b5731d668d71a88af35e14ebe1fec60901f4bc222374303f7365d629dd38e6`.
  Tests reproduce the blob ID locally from the frozen bytes, so shallow CI
  needs neither repository history nor network access.
- Current migrations 001-006 were independently compared with the same files
  at `198b213...`, terminal commit `326ad765...`, and reviewed head
  `8b23e1a...`; they are byte-identical across that authority range. Frozen
  Git-blob / raw-SHA-256 pairs are: 001 `1ef0e23d...` /
  `66bc873f91142e0941728af4260031a908195195a2e8d1a54d4ed5fee28ff50b`;
  002 `bf4fed48...` /
  `950e9520a12938ca0bee68ac3d68976da5a6119ec97753d4a4c243cb80ea3ad9`;
  003 `fa7d1b3c...` /
  `079396b466baadea0501fd591aaa585a39299f6649bbc71fc52897d9d8a65e42`;
  004 `483332d9...` /
  `bb490864a7657da672505d3fcba9df8c87ded72fefa0497db11298a058b64e7e`;
  005 `b814439b...` /
  `c9cc337fe34857368c471f493702fa0b42691ac48fe3fae4ca1a4fc3ea929b01`;
  and 006 `7a331ae2...` /
  `3ea0e2f280395c83d99d51617c7ffc6d6c1097d95f90e1bcad59698e67d775b1`.
  Current migration 007 remains unchanged at blob `19af2602...`, raw SHA-256
  `657b4db6f150b26aa93a83ba32e1e8d648ed183d9c939f4ebd1109f6afcdd363`.
- Signature contract v2 starts with the exact historical fixture, reapplies
  the exact current 001-006 bytes in canonical order, computes PRE-007, then
  applies the exact committed 007 and computes POST-007. Protected columns on
  the three pre-existing relations (`variants`, review decisions, exclusions)
  retain relation/name/type/typmod/nullability/collation/identity/generated/
  storage/compression/default semantics but omit physical `attnum` and sort by
  relation/name. Ordered view output and the wholly migration-created authority
  table retain their column positions as material contract. Function, trigger,
  constraint, view, and authority-relation semantics are otherwise unchanged.
- Disposable PostgreSQL 16.9 generated the reviewed genuine-chain hashes:
  PRE-007
  `cf7e091c334c3a78e9ced12731025b2b7d08529cdf34e6cbe3818b85df36253a`;
  POST-007
  `26dac49f608dbc31527cc4fe105e854d3b6ab86ac1d094ce0508d1c4e0fbeced`.
  The post contract contains exactly 10 functions, 13 triggers, 20
  constraints, five views / 93 ordered view columns, one protected authority
  relation / 12 ordered authority columns, and 18 position-independent
  pre-existing protected columns.
- Two independently randomized genuine historical-chain schemas reproduce
  both frozen hashes. A separate regression creates real dropped-column holes
  on all three pre-existing relations, proves the protected raw `attnum` values
  differ after migration 007, and still reproduces byte-identical normalized
  protected-column payloads and the complete POST-007 hash. Source assertions
  prove neither current consolidated `schema_postgres.sql` nor hand-written
  DROP/recreated-view surgery participates in hash authority.
- Existing fail-closed semantic drift coverage remains green and now also
  includes a required-name extra function overload and a correct-table,
  same-name trigger redirected to a no-op function. Always-true helper,
  wrong-table trigger, tautological same-name constraint, superficially guarded
  `OR TRUE` view, UNLOGGED authority registry, and unexpected marker drift all
  continue to reject. Bootstrap isolation, downstream real-Git isolation,
  post-lock predecessor proofs, stale/concurrent execution, exact 858/0
  terminal mutation/replay, late-finalizer rollback, and State-E read-only
  replay remain unchanged and green.
- Deterministic validation passed: new G7 focus **7/7**; complete corrective
  module **58/58**; complete Phase 4 set **200/200**; Phase 5 **22/22**;
  startup hardening **10/10**; and authoritative suite **385/385**, with zero
  failures, errors, skips, expected failures, or unexpected successes. The
  module/global floors are now 58/385. The authoritative runner independently
  created and verified Python 3.13.11, PostgreSQL 16.9, loopback, and exact
  disposable database `procurement_test` before discovery. Every G7 database
  test used only the validated `TEST_DATABASE_URL` contract; ordinary
  `DATABASE_URL` was never fixture authority.
- Pinned `uv 0.12.3` and `uv lock --check`, Python compilation, `/bin/sh`
  bootstrap syntax, `git diff --check`, added-line credential-pattern review,
  and tracked generated-artifact/cache scan pass. The non-blocking review notes
  about newly named objects outside the frozen whitelist, independent Git-tool
  ownership re-attestation inside Python, and transaction-isolation readback
  remain recorded for later review; none is a demonstrated bypass and none was
  broadened into this G7 correction.
- Production database connections/writes: **0 / 0**. Shopify calls/writes:
  **0 / 0**. PO actions: **0**. No `neondb`, deployment, PR, merge, or Phase 6
  implementation occurred. Published-production corrective Phase 4 remains
  **OPEN**; Phase 6 remains owner-authorized but **PAUSED**.
- **Exact next action:** ChatGPT code re-review and independent adversarial
  re-review before PR, deployment, merge, or production execution.

### Prior final adversarial checkpoint — SUPERSEDED BY G7 PROVENANCE REMEDIATION

- ChatGPT approved design commit
  `e27e5d4644164161483dfd7ce15ff38c08a6aed6`. The implementation commits are
  `9699e677ba90e80cb0b2ade3ac5d10d17c676b4c` and
  `7c206eca74fe6a83c01bf1accd6f8192787e90bf`; the latter preserves the
  bootstrap's executable mode. The reviewed implementation tree before this
  documentation checkpoint is
  `3fcdc8ef565f40767aa3110254739d86cd7b6def` on
  `codex/phase4-published-production-reconciliation`.
- The Scheduled Deployment bootstrap is now POSIX `/bin/sh` and invokes every
  external utility through an absolute reviewed literal. It checks the fixed
  shell, ownership utility, temporary-directory utility, directory utility,
  cleanup utility, environment utility, Git, and noninteractive failure
  utility before use. Git receives an `env -i` allowlist with isolated
  `HOME`/`XDG_CONFIG_HOME`; clone, detached checkout, canonical origin, exact
  HEAD, exact tree, and clean status all precede repository Python.
- The sole permitted interpreter is the literal
  `/nix/store/yp3s28b4xjvcq53wapb1v7hv5hlmmmma-python-wrapped-0.1.0/bin/.python-wrapped`.
  There is no interpreter argument, environment override, `PATH` lookup,
  discovery, alternate, or fallback. `/nix`, `/nix/store`, the exact package
  directory, its `bin` directory, and the executable must all pass the
  reviewed non-writability/type/executability checks or the bootstrap stops
  before Python and before any database connection.
- Hostile-bootstrap tests placed fake `sh`, `bash`, `stat`, `mktemp`, `mkdir`,
  `rm`, `env`, `git`, `false`, and `python3` executables first in parent
  `PATH`, plus hostile Git hooks/templates/configuration. No sentinel ran. A
  real local clone completed every provenance proof, verified-clone Python
  received the exact synthetic authorized parent environment and parent
  `PATH` only after those proofs, and the ephemeral clone was removed through
  the trusted cleanup path. A separate writable-parent regression proves
  failure before Git or Python. GitHub CI portability is confined to a
  generated test-only copy using literal `/usr/bin/python3` when the approved
  Nix path is absent; the production bootstrap remains exact-Nix-only.
- Every canonical terminal Git derivation is now enclosed by the corrective
  sanitized subprocess environment: read-only classification, first terminal
  persistence, and mandatory replay. The real-Git integration regression
  changes only the terminal module's tracked repository anchor, delegates to
  the real canonical Git implementation, observes at least three real
  derivations with `PATH=/usr/bin:/bin` and disabled inherited configuration,
  proves the database URL and both review-token variables absent in every Git
  child, proves hostile Git/fsmonitor sentinels absent, and retains exact 858
  first-run / 0 replay mutation behavior.
- Canonical manifest and terminal persistence services gained only optional
  keyword-only `locked_precondition` callbacks. Defaults preserve all existing
  callers. Each callback runs immediately after the established advisory
  transaction lock and before canonical preflight/inspection or DML. The
  corrective caller re-proves exact A for manifest persistence, exact B for
  migration 007, exact C for terminal persistence, exact D/PRE_REBUILD for
  mandatory replay, and exact D/PRE_REBUILD for the real local finalizer.
- Two-connection PostgreSQL regressions prove stale A-to-B, B-to-C, C-to-D,
  and D-to-E classifications cannot repeat a committed stage. Additional
  post-classification drift tests cover unexpected migration markers at every
  mutating/replay entry point, pre-007 view drift at both A and B, duplicate
  Phase-4 alias provenance at B, and replay invoked directly at C or E. Every
  case rejects inside the lock-owning transaction and preserves the exact
  observed state; the stale D-to-E path never calls the finalizer twice.
- Migration 007 classification now attests a canonical PostgreSQL-catalog
  payload rather than object names alone. The payload signs 10 functions, 13
  triggers, 20 constraints, five operational views and their 93 output
  columns, 30 protected table columns, and the authority registry relation's
  table kind, persistence, RLS, replica identity, partition, and relation
  options. Target schema names are normalized, nested collections are sorted,
  canonical JSON is hashed, raw OIDs/timestamps/owners/statistics are excluded,
  and public read-only inspection retains the no-XID contract.
- **Superseded provenance:** independent review rejected the prior pre-007
  `ecf12c0...` and post-007 `238a8b88...` hashes because their fixture used
  consolidated-schema surgery rather than the historical authority chain. The
  G7 checkpoint above supplies the only current frozen hashes. Same-name
  always-true function, wrong-table trigger, tautological constraint,
  superficially guarded `OR TRUE` view, and UNLOGGED authority-table changes
  all classify `PARTIAL_OR_DRIFTED`; semantic drift blocks C, D, and E.
- The retained late-finalizer rollback regression invokes real
  `apply_rebuild_stage()` and the unmocked canonical
  `rerun_sales_identity_resolution()`, then raises only from
  `final_business_controls()` after real in-transaction finalization. At the
  injection point it observes 57,429 RESOLVED / 1,654 EXCLUDED,
  `sales_daily` 57,424 rows / 80,659.0000 units / $1,263,133.84,
  `canonical_aggregate_rebuilt=true`, `SALES_BACKFILL=PASS`, and
  `POST_REBUILD`. Fresh readback after rollback exactly equals pre-rebuild
  State D: 55,971 RESOLVED / 3,112 UNRESOLVED; 55,966 `sales_daily` rows /
  78,815.0000 units / $1,231,372.83; exact backfill/readiness rows and
  fingerprints; `SALES_BACKFILL=FAIL` with
  `MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED`; `CURRENT_TERMINAL_EXACT` /
  `PRE_REBUILD`; and zero POs/lines.
- Deterministic validation passed: corrective module **52/52**; complete Phase
  4 set **194/194**; Phase 5 **22/22**; startup hardening **10/10**; and the
  authoritative Procurement OS suite **379/379**, with zero failures, errors,
  skips, expected failures, or unexpected successes. The authoritative runner
  independently verified Python 3.13.11, PostgreSQL 16.9, loopback, and exact
  database `procurement_adversarial_test` before discovery. The corrective
  module/global floors rose from 36/363 to 52/379.
- Pinned `uv 0.12.3` reported the exact version and `uv lock --check` passed.
  Python compilation, `/bin/sh` syntax, `git diff --check`, changed-file
  credential-pattern inspection, sensitive-variable added-line review, and
  tracked generated-artifact/cache scan passed. Only explicit disposable
  loopback PostgreSQL 16 `_test` infrastructure was used; libpq redirect
  variables were cleared and connected database identity/major were verified
  before fixture DDL.
- Fail-closed deployment preflight observation: the approved interpreter is
  present and its exact package/bin/executable are non-writable in the current
  development image, but `/nix/store` itself is writable by this development
  user. The unmodified production bootstrap therefore correctly refuses to
  run here before clone/Python/database access. No weakening or interpreter
  substitution was made. The eventual Scheduled Deployment image must prove
  this exact interpreter and every reviewed non-writable parent check; if it
  does not, stop and return for a separately reviewed change. Also, the schema
  hash was generated on PostgreSQL 16.9 while published production was
  reported as 16.15; any minor-version deparser difference safely causes a
  pre-mutation false-fail and must not be bypassed.
- Future reviewed deployment command template (placeholders only):
  `/bin/sh ./scripts/phase4-published-production-bootstrap.sh '<REVIEWED_40_CHAR_SHA>' '<REVIEWED_40_CHAR_TREE>'`.
- Production database connections/writes during this remediation: **0 / 0**.
  Shopify calls/writes: **0 / 0**. PO actions: **0**. No deployment, PR,
  merge, or Phase 6 implementation occurred.
- Published-production corrective Phase 4 closeout remains **OPEN**. Phase 6
  remains owner-authorized but **PAUSED** behind it.
- **Exact next action:** ChatGPT code re-review and independent adversarial
  re-review before PR, deployment, merge, or production execution.

### Published-production environment correction — IMPLEMENTED / AWAITING INDEPENDENT REVIEW

- **Environment correction:** the prior Phase 4 closeout and Phase 5 live
  acceptance evidence labeled as production was obtained from Replit's
  development database, `heliumdb`. The published deployment uses a separate
  PostgreSQL database, `neondb`. This was an environment-identification error;
  independent read-only inspection found no evidence of rogue or partial
  mutation in `neondb`.
- Exact independently verified published-production prestate: database
  `neondb`; PostgreSQL 16.15; schema `public`; database-enforced read-only
  inspection; XID NULL; zero residual `phase5_ui_%` schemas; migrations through
  006 present; migration 007 wholly absent; 2,049 variants; 59,083 durable raw
  facts = 55,971 RESOLVED / 3,112 UNRESOLVED / 0 AMBIGUOUS / 0 EXCLUDED;
  55,966 `sales_daily` rows; zero review decisions; zero historical
  exclusions; zero approved Phase 4 old-ID alias families; zero purchase
  orders and zero PO lines.
- Exact published-production readiness prestate is
  `CATALOG_SYNC=PASS`, `SALES_BACKFILL=FAIL` with blocker
  `MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED`, `VENDOR_RULES=FAIL`, and the
  remaining four future gates WARN. PO generation remains disabled.
- Frozen `neondb` initial protected fingerprints are
  `sales_daily=fd2b4e504b492d9e7609ef8642320f7de300f5294369476da0877aee8da8b2e8`,
  `raw_resolution=06e2726cc33849fc180788fa036a45dcd1b1acd7af32cf813f0ec9311b7dd37a`,
  `sales_backfill_runs=d26f1326eea8e16be6626684db5623c291f582a63564e7aeda9c90167507d409`,
  `readiness_gates=3e3c67ec4fbf0f29824311b4b97ad77bc20635acc3a2e3822c89c73a3119c21a`,
  and the empty PO/PO-line digest
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- Owner authorization is recorded for the corrective published-production
  reconciliation. Verified implementation baseline was exact `main`
  `631bd95e2680b1fcdba80a39f52669d83c8e93ac`, tree
  `2f9ed7a8e74967da7d5c48161cd3af1b8e557727`, on branch
  `codex/phase4-published-production-reconciliation`. Approved design
  checkpoint is `edec3ed8cbb75d1b926e31050e4770db4791a126`; implementation checkpoint is
  `e2a5dff9687ebac24a904f15e20b3d90b3f55ada`, tree
  `2bc62b20717c96933ca7287a01a56335fc131439`.
- The implementation adds a verified-clone Scheduled Deployment bootstrap and
  a narrow restart-safe corrective runner. Before any database connection the
  runner requires the deployment marker, database configuration, both
  non-empty review-token inputs, the existing constant-time authorization
  comparison, exact clean derived Git identity, exact reviewed commit/tree and
  canonical origin, and both frozen manifest hashes. The bootstrap executes no
  repository Python until a unique `/tmp` clone proves origin, detached HEAD,
  tree, and clean status; it has no packaged-source fallback.
- Every runner connection's first SQL statement proves exact database
  `neondb`, PostgreSQL major 16, schema `public`, and no assigned XID. The
  runner accepts only exact States A through E: frozen baseline; original
  manifest persisted/pre-007; post-007 `PRE_TERMINAL_EXACT`; current terminal/
  pre-rebuild; and current terminal/post-rebuild. Partial or mixed state stops.
  A completed State E invocation is a read-only no-op and does not call the
  finalizer or rewrite readiness.
- Existing Phase 4 services remain sole authority: original manifest dry-run
  and persistence, migration 007 plus its standard marker in one transaction,
  terminal dry-run/persistence/exact classification/Git provenance/advisory
  lock, mandatory zero-DML terminal replay, and
  `rerun_sales_identity_resolution()` for the fixed 2024-11-28 through
  2026-08-10 durable range. The corrective path has no Shopify client, sync,
  procurement, or PO action.
- ChatGPT's review of prior head
  `b744ea17b4ac0c6d32a718b6b4fe8385778fdc52` found no production-code defect
  and requested one additional Level-4 proof: rollback after the real
  canonical finalizer has changed State D but before the outer corrective
  transaction commits. The narrow remediation implementation checkpoint is
  `0d6ff674e64403d5840a3cb730f495e982f0b69f`, tree
  `d5e52b7648308e16c01997b9220c9fc565d1847c`. The regression
  `test_real_finalizer_changes_roll_back_when_final_controls_raise` patches
  only `final_business_controls()` to raise at that exact point; it does not
  mock `apply_rebuild_stage()`, `rerun_sales_identity_resolution()`, or the
  canonical finalizer. The corrective runner and bootstrap remain unchanged.
- The disposable rollback evidence run began and, after the injected failure,
  returned on a fresh connection to identical State-D fingerprints:
  `sales_daily=4d56a799ade44ee91e1119ccaad5be24abeec1ef273af6eb1fbe3662e32d3cc3`
  (55,966 rows),
  `raw_resolution=f79ead85ac0f5e828799b81b313f18f54273851822115b0aa302ea68fc37f8a5`
  (59,083),
  `sales_backfill_runs=7a11c470cfee5ba1b33aff1b678e2a68921be4f6193d08654a25810bb65d0629`
  (1), and
  `readiness_gates=a7d42841cf28ff56c4734d33541f75bd3479364c25d6ad8907fdb60f7755402d`
  (7); both PO fingerprints remained the empty digest. Before and after were
  exactly 55,971 RESOLVED / 3,112 UNRESOLVED / 0 AMBIGUOUS / 0 EXCLUDED;
  `sales_daily` 55,966 rows / 78,815.0000 units / $1,231,372.83;
  backfill status `COMPLETED` with `canonical_aggregate_rebuilt=false`;
  `SALES_BACKFILL=FAIL` with exact blocker
  `MATERIAL_HISTORICAL_IDENTITIES_UNRESOLVED`; terminal classification
  `CURRENT_TERMINAL_EXACT`; lifecycle `PRE_REBUILD`; zero terminal mutations;
  and zero POs/lines. Exact full backfill-run evidence and all exact readiness
  rows were included in the equality assertion, not merely these summaries.
- At the injected point, the unmocked real finalizer had visibly produced
  57,429 RESOLVED / 1,654 EXCLUDED / 0 unresolved / 0 ambiguous;
  `sales_daily` 57,424 rows / 80,659.0000 units / $1,263,133.84;
  `canonical_aggregate_rebuilt=true`; `SALES_BACKFILL=PASS` with zero blockers;
  terminal lifecycle `POST_REBUILD`; and changed fingerprints. Raising from
  the patched final validator then rolled all of those in-transaction effects
  back, proving the corrective wrapper remains the final commit boundary over
  the canonical finalizer's nested `conn.transaction()`.
- Independent adversarial review then returned **REQUEST CHANGES** at exact
  reviewed head `193ed4abcd699fe1c5dd44b679d509af5942a5c9`, tree
  `a5f24607c35cdcc161a17483a295a8cadf0335c8`, for two remaining fail-closed
  gaps: inherited Git configuration/environment could affect pre-verification
  Git children, and the state classifier accepted extra `migration:*`
  markers. The earlier late-finalizer rollback finding remains closed and its
  real-finalizer regression remains green.
- Remediation checkpoint `e620a5a353421aa67829ac24aee8362d650e5666`,
  tree `a829cbaac3c784fd94be40845434b2f2dec59283`, isolates every bootstrap Git
  operation with `/usr/bin/env -i`, fixed `/usr/bin/git` and
  `PATH=/usr/bin:/bin`, isolated mode-0700 `HOME`/`XDG_CONFIG_HOME`, disabled
  system/global Git configuration and prompts, and an explicit minimal
  allowlist. The corrective Python provenance section uses the same
  corrective-only isolation boundary around the unchanged canonical
  `derive_runtime_execution_git_identity()` path and restores the normal
  Python environment afterward. Database URLs, review tokens, inherited
  `GIT_*`, proxy, SSH/askpass, template, hook, and transport configuration are
  therefore unavailable to Git children.
- Real-Git regressions use local temporary repositories under hostile global
  and XDG configuration, `core.hooksPath`, an executable checkout hook, a
  hostile template hook, `GIT_CONFIG_*` injection, `core.fsmonitor`, a hostile
  leading `PATH` with a fake `git`, and synthetic database/token sentinels.
  Clone, checkout, and both bootstrap/Python provenance proofs succeeded via
  the fixed trusted Git binary; neither hook, fsmonitor, nor fake Git executed;
  a real Git child explicitly observed all three sensitive variables absent;
  and verified repository Python received the restored authorized parent
  environment only after all clone proofs.
- Migration state is now exact-dictionary classified: States A/B require only
  the seven named through-006 markers, each value exactly `applied`; States
  C/D/E require exactly those seven plus migration 007, again each exactly
  `applied`. Any missing, extra, or wrong-valued marker classifies as
  `PARTIAL_OR_DRIFTED`. A PostgreSQL regression traverses real A→B→C→D→E and,
  at every state, injects `migration:008_unapproved.sql` once with `applied`
  and once with `unexpected-value`; all ten cases stop through the real outer
  classifier before any permitted stage is called, then deterministically
  restore the exact lifecycle state.
- Dedicated corrective tests passed **36/36** against explicit disposable
  loopback PostgreSQL 16 `_test` infrastructure. Existing Phase 4 tests passed
  **142/142**, Phase 5 passed **22/22**, startup hardening passed **10/10**, and
  the authoritative full deterministic Procurement OS suite passed **363/363**
  with zero failures, errors, skips, expected failures, or unexpected
  successes. The full runner verified Python 3.13.11, PostgreSQL 16.9,
  `procurement_test`, and loopback before test discovery. Pinned `uv 0.12.3`
  lock validation, Python compilation, shell syntax, `git diff --check`, secret
  scan, and generated-artifact scan passed.
- Test fixtures use only the existing `TEST_DATABASE_URL` contract from
  `procurement/tools/run_tests.py`: PostgreSQL URL, loopback host, exactly one
  `_test` database name, no parameter/query/fragment tricks, cleared libpq
  `PG*` redirects, exact connected-database identity, and PostgreSQL major 16
  before fixture DDL. A production-style ordinary `DATABASE_URL` was present
  during focused corrective validation and was demonstrably ignored by the
  fixture path.
- Non-blocking residual risk: migration-COMPLETE verification still checks
  several schema protections primarily by object name rather than full
  semantic definition. No concrete same-named bypass was found. Scope remains
  bounded because clean B→C applies the exact reviewed migration SQL in one
  transaction, execution source is exact-SHA/tree/origin verified, and exact
  protected state/business controls remain mandatory. This residual is
  recorded for independent review and was not expanded into an unapproved
  schema-introspection redesign.
- Production database connections and writes during this implementation task:
  **0 / 0**. Shopify calls/writes: **0 / 0**. PO actions: **0**. No Scheduled
  Deployment was created and no PR was opened.
- Phase 4's implementation and owner-approved identity authority remain
  accepted, but published-production reconciliation is **IN PROGRESS** until
  independent implementation review, PR/CI, reviewed temporary Scheduled
  Deployment execution, and post-execution evidence are complete. Phase 6 is
  owner-authorized but **PAUSED** on this prerequisite.
- **Exact next action:** ChatGPT re-review and independent adversarial
  re-review before PR or any production connection/execution.

### Phase 5 Foundation UI — COMPLETE

- Formal acceptance result: **PASS**. The accepted implementation chain is
  design `7be544b8ccd89aba5fc78d74cf2d385f3e3753ef`, plan
  `31854acc7da591dcbf3422f3aff8bac51358d949`, UI implementation
  `1bb1cb5a1eb7b0daccf96cf67543fc32efcb256a`, and test-safety remediation
  `da6e45346b7960964db83a6362d05e47a6d6a66a`. PR #18, `Complete Phase 5
  Foundation UI acceptance remediation`, was reviewed at exact head
  `bfecaccfca2361d73078591e0d989921ec87446c` and merged as
  `2af6a7258695023ff7c24ad6ff3f1ad9de760d2d`, exact tree
  `c873c8097614babd14726eff182414fc38fb4e9a`.
- Exact-head Procurement CI run `33935952570` completed successfully on
  `bfecaccfca2361d73078591e0d989921ec87446c`. Post-merge Procurement CI run
  `33936303756` completed successfully on exact merged main SHA
  `2af6a7258695023ff7c24ad6ff3f1ad9de760d2d`. Dedicated Phase 5 tests passed
  22/22 and the complete deterministic Procurement OS suite passed 327/327
  with zero failures, errors, or skips.
- Independent live development acceptance ran against exact tree
  `c873c8097614babd14726eff182414fc38fb4e9a`. The required endpoints returned:
  `/procurement/admin/status` 200, `/procurement/reconciliation` 200,
  `/procurement/historical-sales/review` 200,
  `/procurement/data-sync-runs` 200, and `/procurement/health/full` 200. The
  temporarily started existing Procurement OS/API workflows were stopped
  immediately after acceptance.
- All four HTML surfaces—System Readiness, Catalog Reconciliation, Historical
  Sales Reconciliation, and Data/Sync Runs—displayed shared navigation to one
  another. `/data-sync-runs` remained GET-only and read-only, displayed
  authoritative catalog and historical-sales run evidence, including 59,083
  source facts / 57,429 resolved / 1,654 excluded / 0 unresolved / 0
  ambiguous, and exposed no sync, retry, rebuild, job, or mutation control.
- System Readiness displayed every canonical gate and exact message:
  `CATALOG_SYNC=PASS` — `Catalog reconciliation passed.`;
  `SALES_BACKFILL=PASS` — `Historical ShopifyQL sales backfill, identity
  accounting, and controls passed.`; `VENDOR_RULES=FAIL` — `Vendor operating
  rules are not yet confirmed complete.`; `INVENTORY_HISTORY=WARN` — `Own
  daily inventory snapshots are not yet confirmed running.`;
  `MAPPING_INTEGRITY=WARN` — `Supplier mapping integrity is not yet fully
  validated.`; `OPEN_PO_RECONCILIATION=WARN` — `Procurement PO reconciliation
  is not yet fully operational.`; and `PRICE_COVERAGE=WARN` — `Full-catalog
  verified supplier pricing is not yet complete.`
- PO generation visibly remained **DISABLED** for the exact canonical blocker
  `VENDOR_RULES`. The disabled control was inert and had no form, action,
  JavaScript, POST, or PO mutation path. Purchase orders remained 0 and
  purchase-order lines remained 0.
- Live GET-only no-write controls passed: readiness gates remained 7; the
  readiness fingerprint remained
  `66f69117e24adedf72b3a6df4fd80607482e8bb339c4803520e697237be10046`;
  gate statuses, messages, and `checked_at` values were unchanged; purchase
  orders and lines remained 0 before and after; and assigned transaction XID
  remained NULL. No acceptance GET caused database DML or readiness changes.
- The production-test incident remains disclosed: an earlier direct Phase 5
  integration-test invocation inherited production `DATABASE_URL`, created
  three isolated `phase5_ui_*` fixture schemas, and removed them in teardown.
  Independent read-only reconciliation subsequently proved zero residual
  `phase5_ui_%` schemas, all approved Phase 4 protected fingerprints exact,
  no public business-state drift, no PO creation, and unchanged protected
  source, resolution, readiness, and aggregate controls. Remediation
  `da6e45346b7960964db83a6362d05e47a6d6a66a` makes Phase 5 integration tests
  use only validated `TEST_DATABASE_URL`, requiring disposable loopback
  PostgreSQL 16 and exact `_test` database identity before fixture DDL. The
  remaining legacy direct-invocation test-harness pattern is deferred to Phase
  6; no Phase 6 implementation has started.
- `PHASE4_REVIEW_TOKEN_INPUT` was independently verified absent without
  accessing or exposing its value. `RECONCILIATION_REVIEW_TOKEN` remains
  separately governed. Phase 5 caused zero Shopify writes and added or invoked
  no PO-generation action.
- **Current boundary:** Phase 5 application/UI acceptance remains COMPLETE.
  Published-production Phase 4 correction now takes precedence. Phase 6 is
  owner-authorized but PAUSED until that prerequisite is independently
  reviewed, executed, and verified.

### Development-database Phase 4 closeout — COMPLETE HISTORICAL EVIDENCE ONLY

> Correction: this sequence ran against development `heliumdb`, not published
> production `neondb`. Its implementation/authority and deterministic outcomes
> remain valid, but its database results are not published-production closeout
> evidence.

- Owner-authorized closeout sequence executed from exact clean merged `main`
  SHA `dbd4cdc1d48e098e20e8f7642a64fb409966c793`, tree
  `fb9887834beff0b39a24f633503f3b78d8992f97`. Remote `origin/main`
  resolved to the same SHA. GitHub Procurement CI run `33818188106` was
  independently re-read through the GitHub API and was `completed / success`
  for that exact SHA.
- Before any production connection, both `PHASE4_REVIEW_TOKEN_INPUT` and
  `RECONCILIATION_REVIEW_TOKEN` were proven present and non-empty without
  printing, echoing, hashing, logging, or otherwise exposing either value. The
  existing `require_review_authorization` constant-time comparison passed.
- The development-database preflight used a database-enforced `REPEATABLE READ,
  READ ONLY` snapshot with no transaction ID before or after inspection. Runtime identity
  was PostgreSQL `16.10`, database `heliumdb`, schema `public`, and the database
  name matched the configured URL. Migration 007 was wholly absent. Frozen
  prestate was exact: 2,049 variants / 1,999 active / 46 retired-confirmed;
  59,083 raw facts = 55,971 RESOLVED / 3,112 UNRESOLVED / 0 AMBIGUOUS /
  0 EXCLUDED; 55,966 `sales_daily` rows; 343 review decisions; 8 active
  exclusions; 17 review aliases; 7 readiness gates with `CATALOG_SYNC=PASS`,
  `SALES_BACKFILL=FAIL`, and `VENDOR_RULES=FAIL`; 0 purchase orders and 0 PO
  lines.
- Exact protected prestate fingerprints were:
  `sales_daily=fd2b4e504b492d9e7609ef8642320f7de300f5294369476da0877aee8da8b2e8`
  (55,966 rows),
  `raw_resolution=06e2726cc33849fc180788fa036a45dcd1b1acd7af32cf813f0ec9311b7dd37a`
  (59,083),
  `sales_backfill_runs=d26f1326eea8e16be6626684db5623c291f582a63564e7aeda9c90167507d409`
  (1),
  `readiness_gates=3e3c67ec4fbf0f29824311b4b97ad77bc20635acc3a2e3822c89c73a3119c21a`
  (7), and the empty PO/PO-line digest
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- The legacy protected dry-run was also read-only/no-XID and proved
  `CURRENT_PROVENANCE=343`, `CONFLICT=0`, and zero planned mutations for the
  prior manifest state. It revalidated the original manifest SHA-256
  `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`.
- Only migration `procurement/db/007_phase4_terminal_disposition.sql` and its
  standard migration marker were applied, transactionally. Fresh read-only
  postconditions proved all 7 new variant columns, all 3 validated identity
  constraints, all 13 Phase 4 triggers, all 5 filtered operational views,
  2,049/2,049 pre-existing variants still `CURRENT`, zero premature
  historical-only rows, zero premature terminal provenance, and unchanged
  protected fingerprints.
- The protected terminal dry-run revalidated terminal manifest SHA-256
  `fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff`
  and classified the entire database `PRE_TERMINAL_EXACT` with no diagnostics,
  read-only/no-XID, and zero DML. Its exact plan was 43 restored identities,
  280 terminal decision supersessions, 8 original-exclusion normalizations,
  39 new proven alias families, 198 structured active exclusions, and 1
  authority registration.
- One serializable transaction changed `PRE_TERMINAL_EXACT` to
  `CURRENT_TERMINAL_EXACT` and committed exactly 858 controlled mutations.
  Fresh readback proved 43 RESTORE / 102 MAP / 198 EXCLUDE / 0
  LEAVE_UNRESOLVED; 43 exact inactive `HISTORICAL_ONLY` identities; 56 total
  approved source-ID alias families; 198 structured active exclusions; all
  current-state components true; no diagnostic; and no protected
  source/aggregate/gate/PO fingerprint change during terminal persistence.
- A fresh read-only terminal dry-run again returned
  `CURRENT_TERMINAL_EXACT`, zero DML, and no transaction ID. The mandatory
  second identical persistence execution returned
  `CURRENT_TERMINAL_EXACT -> CURRENT_TERMINAL_EXACT`, every planned mutation
  count zero, committed mutations zero, and identical before/after protected
  fingerprints.
- The public canonical `finalize_sales_backfill` path then re-resolved all
  59,083 durable facts, rebuilt the canonical aggregate, derived exclusion
  integrity from authoritative PostgreSQL state, and evaluated readiness. It
  completed with `status=PASS`, `run_status=COMPLETED`, zero blockers, 21/21
  chunks, 70/70 pages, durable source facts, zero duplicate observations,
  reconciled source/resolution/canonical controls, and
  `canonical_aggregate_rebuilt=true`.
- Final whole-source controls are exact: 59,083 facts = 57,429 RESOLVED /
  1,654 EXCLUDED / 0 UNRESOLVED / 0 AMBIGUOUS. Source totals are 82,501.0000
  net / 82,545.0000 absolute units and $1,300,975.14 net / $1,304,920.80
  absolute sales. Resolved totals are 80,659.0000 net / 80,693.0000 absolute
  units and $1,263,133.84 net / $1,264,065.52 absolute sales. Excluded totals
  are 1,842.0000 net / 1,852.0000 absolute units and $37,841.30 net /
  $40,855.28 absolute sales. `sales_daily` is exactly 57,424 rows /
  80,659.0000 units / $1,263,133.84 sales.
- Final resolution-method counts are exact: 36,397
  `EXACT_ACTIVE_VARIANT_ID`; 19,430 `APPROVED_VARIANT_ID_ALIAS`; 136
  `APPROVED_HISTORICAL_IDENTITY`; 443
  `EXACT_PRESERVED_HISTORICAL_VARIANT_ID`; 1,023
  `APPROVED_SOURCE_IDENTITY_DECISION`; 189 `EXPLICIT_EXCLUSION`; and 1,465
  `EXPLICIT_UNATTRIBUTABLE_EXCLUSION`.
- Authoritative exclusion integrity is `PASS` with no diagnostics, 198 exact
  active source keys, and 1,654 excluded facts. The original-eight bucket is
  8 keys / 189 rows / 44.0000 net and absolute units / $145.93 net and
  $169.91 absolute sales. The exhaustively-unattributable bucket is 190 keys /
  1,465 rows / 1,798.0000 net and 1,808.0000 absolute units / $37,695.37 net
  and $40,685.37 absolute sales.
- Fresh post-rebuild read-only verification classified the database
  `CURRENT_TERMINAL_EXACT` with lifecycle `POST_REBUILD`, zero planned
  terminal mutations, zero DML, and no transaction ID. Final protected
  fingerprints are:
  `sales_daily=43a6e808c5ff78d6de3cb2c7f382643f82935dc7d4f41d2fbaf72f9d995d4b14`
  (57,424),
  `raw_resolution=80ee8c2502eac33c75722d680f8e0ec5e0336ea08b72f341019f8d6b8e7e9b1f`
  (59,083),
  `sales_backfill_runs=f6789dfd2a064a62dcdbccc62ea21e5930080f29f47a879d333ce96073a2734b`
  (1), and
  `readiness_gates=66f69117e24adedf72b3a6df4fd80607482e8bb339c4803520e697237be10046`
  (7); both PO digests remain the empty-state digest.
- `SALES_BACKFILL` reached `PASS` through the canonical readiness path at
  `2026-09-04T11:48:28.619695Z`; its persisted evidence contains zero blockers
  and database-derived exclusion integrity `passed=true`. `CATALOG_SYNC`
  remains `PASS`; `VENDOR_RULES` remains `FAIL`; other not-yet-started gates
  remain `WARN`. Purchase orders and PO lines remain zero.
- Post-production machine validation passed 305/305 with zero failures,
  errors, skips, expected failures, or unexpected successes on Python 3.13.11
  and disposable loopback PostgreSQL 16.9. No production database was exposed
  to the test runner.
- Shopify access/writes, Packet A, Vendor Rules, forecasting,
  recommendations, procurement, PO generation/release, and deployments were
  all zero/not started. Phase 4 is complete. No downstream phase or workstream
  is authorized by this closeout.

### Historical checkpoint — Phase 4 owner-approved terminal disposition artifact freeze

- Owner authorization dated 2026-08-21 approved the terminal exclusion semantic
  `HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW`, all 19
  predecessor/successor continuity pairs, the final five individual MAP
  exceptions, and terminal exclusion of the three High Noon Tequila keys,
  KILLR flavor ambiguity, and both contradictory Popov size keys. Fiesta target
  `41193000796235` remains prohibited, and neither Popov size is canonical.
- The original 343-row manifest remains immutable historical authority at
  SHA-256 `95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287`.
  Its 280 effective `LEAVE_UNRESOLVED` decisions have not been changed in
  production. The append-only terminal supplement is
  `procurement/review/phase4_terminal_disposition_manifest.csv`, SHA-256
  `fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff`.
- The supplement is exactly 280 unique prior-LEAVE keys: 43
  `RESTORE_HISTORICAL_IDENTITY`, 47 `MAP_TO_CANONICAL`, 190
  `EXCLUDE_UNATTRIBUTABLE`, and 0 unresolved. RESTORE covers 435 raw rows,
  511.0000 net/absolute units and $8,506.52 net/absolute sales. MAP covers 200
  raw rows, 232.0000 net/absolute units and $4,096.03 net/absolute sales.
  Terminal unattributable exclusion covers 1,465 raw rows, 1,798.0000 net /
  1,808.0000 absolute units and $37,695.37 net / $40,685.37 absolute sales.
- Combined effective terminal intent across 343 source keys is 102 MAP, 198
  EXCLUDE (the immutable original exact eight plus 190 new terminal
  exclusions), 43 RESTORE and 0 LEAVE_UNRESOLVED. The 102 MAP keys cover 1,023
  raw rows and 96 distinct targets. Canonically sorted target-flow control
  SHA-256 is `5d6832cea3df7a4f45d31d7e7a8100409ddc078936095fe3c775ce862a1f64a6`.
  The 43 RESTORE keys use 43 distinct exact historical Variant IDs; each target
  equals its source historical Variant ID. All 19 continuity successors are
  restored and all 19 predecessors map to the corresponding successor.
- The original 3,112 unresolved raw facts terminally partition into 1,458
  resolved-attribution rows and 1,654 excluded rows: resolved-attribution
  impact is 1,844.0000 net/absolute units and $31,761.01 net/absolute sales;
  exclusion impact is 1,842.0000 net / 1,852.0000 absolute units and
  $37,841.30 net / $40,855.28 absolute sales.
- Eventual post-rebuild whole-source controls are frozen at 59,083 facts:
  57,429 RESOLVED, 1,654 EXCLUDED, 0 UNRESOLVED and 0 AMBIGUOUS; 80,659.0000
  resolved net / 80,693.0000 absolute units; 1,842.0000 excluded net /
  1,852.0000 absolute units; $1,263,133.84 resolved net / $1,264,065.52
  absolute sales; and $37,841.30 excluded net / $40,855.28 absolute sales.
  Source totals remain 82,501.0000 net / 82,545.0000 absolute units and
  $1,300,975.14 net / $1,304,920.80 absolute sales. Expected `sales_daily` is
  57,424 rows, 80,659.0000 units and $1,263,133.84 sales.
- Frozen resolution-method counts are: 36,397 `EXACT_ACTIVE_VARIANT_ID`;
  19,430 `APPROVED_VARIANT_ID_ALIAS`; 136
  `APPROVED_HISTORICAL_IDENTITY`; 443
  `EXACT_PRESERVED_HISTORICAL_VARIANT_ID`; 1,023
  `APPROVED_SOURCE_IDENTITY_DECISION`; 189 `EXPLICIT_EXCLUSION`; and 1,465
  `EXPLICIT_UNATTRIBUTABLE_EXCLUSION`.
- Final exclusion reason buckets are separately controlled: the original exact
  eight use `PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION` and cover 189 raw
  rows, 44.0000 net/absolute units and $145.93 net / $169.91 absolute sales;
  the new 190 use
  `HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW` and cover the
  1,465-row controls above. An `EXCLUDED` status alone is never readiness
  evidence: later implementation must prove effective owner-approved ledger
  decisions, allowlisted reasons, exact source scope, manifest/evidence
  provenance, no target, complete membership and exact reason-coded financial
  reconciliation.
- Canonical authority and governance text now record the approved semantic.
  Runtime code, SQL/schema, `rules.toml`, database state, catalog, aliases,
  source facts, aggregate sales, readiness gates, Shopify and POs remain
  unchanged. Phase 4 is not complete.

### Historical checkpoint — Phase 4 controlled identity-decision persistence

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

- Verified authorized `origin/main` baseline for the terminal artifact freeze:
  `97fe3868fa87d17d1a8f236d993c35cd8db83805`.
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

### Historical checkpoint — Phase 4 initial implementation and live run

- ShopifyQL access probe: **PASS** using configured Admin API `2026-07`; store timezone is `America/New_York`. No customer dimensions or Orders API fallback were used.
- Additive migration `procurement/db/006_phase4_sales_backfill.sql` is applied. It adds durable run/chunk/page checkpoints, run-to-fact observations, restatement evidence, complete control fields, and append-only historical-sales review decisions.
- Live run: `d389079c-eabf-49b5-a245-40a207025fd7`, started `2026-08-10T16:44:26.811525Z`, completed `2026-08-10T16:45:59.804015Z`.
- Requested range: **2024-11-28 through 2026-08-10** (current store-local date at execution).
- Coverage: **21/21 date chunks**, **70/70 structurally contiguous pages**, all pages/chunks complete; no parse error, duplicate observation, missing chunk, or coverage gap. Current-code local finalization re-proved page indexes, offsets, terminal-page structure, stored range, and run-creation date evidence without refetching Shopify.
- Durable source: **59,083 source rows = 59,083 unique natural facts**.
- Resolution: **55,971 resolved rows**, **3,112 unresolved rows**, **0 ambiguous rows**, **0 explicitly excluded rows**.
- At this historical checkpoint, the owner review queue contained **343
  unresolved identity groups**
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
- At this historical checkpoint, Phase 4 workflow implementation, initial live
  fetch, owner-decision
  persistence, and independent persistence review are complete, but **Phase 4
  remains incomplete until separately authorized historical
  re-resolution/rebuild and gate reevaluation are complete**.

### Historical pre-closeout readiness and safety state

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

Phase 5 Foundation UI remains **COMPLETE**. Corrective published-production
Phase 4 implementation, authority, review, merge, and CI gates are complete at
`main` `1920a16a6dc13a1b4357315f5049b938cbe7c0e2`, but production execution and
independent post-action reconciliation remain outstanding. G9 read-only
preflight-mode implementation is validated on
`codex/phase4-production-preflight-mode` but has not been reviewed, merged, or
run in the published Scheduled Deployment. No deployment or production
connection is authorized yet. Phase 6 is owner-authorized but **PAUSED** on
this prerequisite. The exact next action is ChatGPT implementation review and
independent adversarial review of the G9 branch before PR authorization.
Vendor Rules, inventory snapshots, price books, forecasting, procurement, PO
generation/release, Shopify mutation, and other downstream implementation
remain out of scope.
