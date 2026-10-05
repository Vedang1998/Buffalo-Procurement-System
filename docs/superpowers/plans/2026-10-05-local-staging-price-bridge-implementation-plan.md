# Local Staging Price Bridge Implementation Plan

**Design:**
`docs/superpowers/specs/2026-10-05-local-staging-price-bridge-amendment.md`  
**Approved baseline:** `857b849` (design checkpoint), descended from source/image
checkpoint `8b04e97` through handoff checkpoint `e9f82cd`  
**Risk:** Level 3 pricing/database/runtime change  
**Writer:** Codex only; all reviewers read-only

This plan implements the approved stopped-service exact-fixture boundary and
registered-observation correction, then continues through the required LOCAL
integrated acceptance. No Railway, production, Shopify, supplier, purchasing,
main, or PR #24 mutation is authorized.

## Task 1 — Share and hold the existing lifecycle exclusion

Files:

- add `procurement/src/procurement_os/database_lifecycle.py`;
- update `procurement/tools/local_purchasing_candidate.py`;
- update `procurement/tools/initialize_synthetic_demo.py`;
- update `procurement/src/procurement_os/staging_process.py`;
- update the corresponding lifecycle, process-readiness, and foundation tests.

Actions:

1. Move the existing lifecycle lock name derivation and advisory-lock acquisition
   behind a small source-level helper without changing its lock text or the
   legacy launcher's identity checks.
2. Keep the legacy launcher's existing whole-service lock behavior and prove it
   remains byte/semantically compatible.
3. During staging synthetic startup, fully validate the target and runtime
   credential, acquire the same database-bound session lock before READY, and
   retain the dedicated connection for the whole server lifetime.
4. Periodically prove the retained session and lock are live. Release only
   after HTTP drain during bounded shutdown. Acquisition or lock-session
   failure is an activation/readiness failure, never a degraded mode.
5. Prove race ordering: an operator holding the lock blocks service readiness;
   a live or starting service blocks the operator; neither side performs price
   effects before ownership.

Focused proof:

- exact lock-name compatibility;
- legacy valid writes do not gain staging-only checks;
- initial acquisition, duplicate acquisition, connection loss, shutdown, and
  startup-failure cleanup;
- actual PostgreSQL two-session exclusion, not mocks alone.

## Task 2 — Implement the fixed stopped-service staging service

Files:

- add `procurement/src/procurement_os/synthetic_staging_price_stage.py`;
- update `procurement/src/procurement_os/synthetic_price_replacement.py`;
- update `procurement/src/procurement_os/synthetic_price_replacement_contract.py`;
- update `procurement/src/procurement_os/storage.py` only as needed to expose
  create-once publication through its existing adapter boundary;
- add `procurement/tests/test_synthetic_staging_price_stage.py`;
- update runner registration/floors and source-contract tests.

Actions:

1. Pin the code-owned CSV path, 1,590-byte size, and SHA-256
   `00071443ea8c54b57fc6014c3b1daf204081714a2ff09b98bed6c56a0dd3862c`.
   Read through a no-symlink, regular-file, stable-inode check and hash before
   opening the target or storage.
2. Add the fixed principal
   `synthetic:stopped-service-price-fixture:v1` with role
   `procurement.price.stage`. Refactor a private shared declared-staging core
   for the fixed operator wrapper while leaving the existing browser-facing
   function strictly `procurement.price.approve`. Confirmation, APPLY, and
   every other price action remain approval-only.
3. Expose one zero-argument root operator composition. Reject every argument
   before I/O and reject ambient `DATABASE_URL`, `TEST_DATABASE_URL`,
   `PGPASSWORD`, `PGPASSFILE`, and `PROCUREMENT_STORAGE_ROOT`. Reuse the
   centrally validated service inputs, pass the root-only runtime secret through
   one fixed bounded pipe, remove it from the child environment, and launch the
   effecting child as exact UID/GID `1102:1202` with no supplementary groups.
4. Construct the exact staging target from the existing credential-free root
   configuration. Accept no target/path/vendor/date/policy/clock/batch inputs.
   Consume the runtime credential through one bounded inherited secret
   descriptor, scrub it best-effort, and never include it in argv, URLs, env,
   exceptions, output, or evidence.
5. Require the synthetic worker UID/GID and derive storage only from the
   centrally validated bootstrap layout (fixed `/data/synthetic` in the image).
   Validate type, ownership, mode, mount relationship, and absence of symlink
   traversal before use.
6. Connect as the restricted runtime role, attest the complete target, acquire
   and hold the lifecycle lock, and reattest inside the deciding staging
   transaction.
7. Verify the exact registered declaration, policy, and prestate; then pass the
   raw bytes through `stage_and_validate_declared_price_book`. Do not construct
   a prevalidated row or persist confirmation-owned scope-membership rows.
   Staging may emit the computed proposed-membership hash as evidence only.
8. Publish raw evidence with create-once semantics. If the content-addressed
   object exists, compare its exact bytes and metadata; never overwrite it.
9. Permit only a new exact `VALIDATED` candidate or an exact idempotent replay
   of that same `VALIDATED` candidate. Refuse `STAGING`, `INVALID`,
   `VERIFIED_FUTURE`, `APPLIED_CURRENT`, rejected, conflicting, or otherwise
   unexpected existing state.
10. Compare canonical pre/post projections proving zero change to CURRENT,
   confirmation/APPLY events, persisted memberships, selections, reviews,
   Monday runs, DRAFTs, packets, unrelated vendor state, and prior artifacts.
11. Add three bounded whole-transaction retries only for `40001`/`40P01`.
    Recover a lost/ambiguous commit only from a fresh restricted connection,
    complete reattestation, lifecycle-lock reacquisition, exact DB/storage
    observation, and immutable batch match; never blindly resubmit.
12. Return one canonical credential-free proof with only contract/source/batch/
   `VALIDATED` and immutable hashes. Ordinary startup never invokes this entry
   point.

Focused proof uses a real staging PostgreSQL target and real filesystem storage:

- exact success and exact idempotent replay;
- altered source bytes (test seam only), wrong declaration/target/role/catalog/
  storage, live-service lock, and advanced/conflicting state refusal;
- injected late failure and ambiguous-result recovery with fresh-observer
  no-authority-advance evidence;
- zero secret/raw/private output and exact cleanup.

## Task 3 — Correct declared temporal projection and presentation

Files:

- update `procurement/src/procurement_os/synthetic_price_replacement.py`;
- update `procurement/src/procurement_os/api.py`;
- update `procurement/tests/test_synthetic_price_replacement_postgres.py` and
  API/staging gateway tests;
- update the browser assertions without weakening them.

Actions:

1. Leave `price_book.py` generic list/get and legacy promotion host-clock logic
   unchanged.
2. Add a declared-only list/detail wrapper that first runs complete process,
   target, fixture, contract, declaration, policy, observation, timezone, and
   boundary attestation.
3. Select the registered `observation_at` before computing temporal status for
   exact declared-contract rows. A mixed list overlays only those rows; every
   legacy/undeclared row retains the generic host-clock result.
4. Make detail identify declared state before presentation and call the
   attested wrapper directly. Missing/inconsistent registration returns a
   fail-closed conflict; never catch it and silently render a generic result.
5. Render stable, separately labeled durable and operational state elements.
   Render confirmation/APPLY controls only when both values match the required
   transition.
6. Suppress all upload/import controls in root-composed staging while retaining
   the staging gateway's explicit denial of `POST /price-books/import`.
   Preserve the legacy local browser upload UI outside staging.
7. Do not modify migration 016, catalog hashes, durable statuses, confirmation/
   application/Monday instants, audit timestamps, or any security clock.

Focused proof:

- reproduced 2026-10-05 host mismatch becomes declared
  `VERIFIED_FUTURE/VERIFIED_FUTURE` using the registered 2026-09-16 instant;
- host-date/window changes do not alter declared output;
- invalid observation/timezone/boundary/fixture/target evidence refuses;
- mixed list and legacy wrong-month behavior remain exact;
- list/detail/confirmation agree on the authorized observation basis;
- state mismatch suppresses action controls;
- session/assertion expiry, throttling, deadlines, and idle timeouts remain
  monotonic/host-time based.

## Task 4 — Add the minimal staging browser driver and run the focused risk gate

Before browser execution, add the smallest tracked staging-specific CDP phase
that consumes the operator-produced batch ID/proof instead of uploading bytes.
It proves no import form exists, observes `VALIDATED/VALIDATED`, performs real
preview/CONFIRM, and observes `VERIFIED_FUTURE/VERIFIED_FUTURE`. Preserve the
legacy browser-upload driver and its original failure evidence as a separate
legacy/clock regression; it is not the Option-B acceptance driver.

Order:

1. compile and diff checks;
2. lifecycle/operator unit tests;
3. real PostgreSQL operator and clock modules;
4. gateway/API/browser contract tests;
5. the previously failing disposable legacy Chromium price phase for the clock
   regression, retaining both the old failure and new result;
6. the new operator-staged staging Chromium phase through CONFIRM;
7. Backup V2/APPLY negative and lineage modules;
8. runner registration/conformance tests.

Any failure is diagnosed from preserved evidence, fixed, independently reviewed,
and rerun at the smallest causal scope before expanding. No unchanged expensive
failure is retried blindly.

## Task 5 — Expand the tracked LOCAL staging acceptance driver

Files are selected after Task 4 establishes the stable operator/API contract.
The expected shape is one Python orchestrator plus one staging-specific CDP
driver under `procurement/tools`, with focused tests for subprocess, TLS,
secret-FD, cgroup, and cleanup behavior.

The driver must:

1. use one clean frozen source/image identity;
2. build/materialize the accepted research release without changing its fixed
   77-member private closure;
3. create physically distinct owned PostgreSQL 16 source/target clusters and
   run the accepted transfer/prepare/restore/SCRAM sequence;
4. start no service, run the exact operator stage, and prove `VALIDATED`;
5. start the real root/Tini multi-UID image, authenticate through TLS/gateway,
   preview and CONFIRM the candidate, and prove `VERIFIED_FUTURE`;
6. stop through the lifecycle exclusion, create real Backup V2, bind it only
   through trusted root configuration, and restart;
7. browser-APPLY and complete mapping, selection, review, quantity edit, two
   DRAFTs, and downloads;
8. independently derive/reconcile three lines, 14 packet members, $282
   merchandise, one $7 fee, and $289 total from raw source and decisions;
9. run gateway negative/security cases, persistence restart, and exact lineage
   verification; and
10. always perform bounded cleanup, retaining only allowlisted aggregate JSON
    evidence with no credentials, cookies, CSRF values, or private payload.

## Task 6 — Complete research, resource, restart, and cleanup acceptance

After an immediate memory/cgroup preflight:

- run one real corrected-V3 cold start and verify 2,009/1,365/644/0/0 plus zero
  corrected-coherence violations;
- download and hash all four accepted artifacts;
- prove concurrent single-flight behavior, bounded overload response, idle stop,
  one crash/retry, and full restart;
- sample each process identity/RSS/HWM and aggregate cgroup current/peak/events;
- require each process below 5 GiB, service peak below 6 GiB, and zero OOM
  counters; and
- prove complete process, listener, socket, key, temp, container, volume, and
  owned PostgreSQL cleanup while private source hashes remain unchanged.

Abort before replay if current headroom is below the known peak plus safety.

## Task 7 — Freeze, validate, review, publish, and hand off

1. Freeze the exact source commit/tree and rebuild the image twice from its
   clean detached checkout.
2. Run formatting/compilation, lock, Docker, shell, JSON, Git, secret/private-
   data, generated-file, container history/layer, startup 10/10, focused
   staging, and authoritative suite gates with exact counts and zero abnormal
   counters.
3. Obtain attributable independent read-only review of the amendment,
   lifecycle exclusion, operator, clock projection, database/runtime behavior,
   acceptance driver, and final diff. Resolve every P0/P1 or record an explicit
   owner exception.
4. Update `docs/CODEX_HANDOFF.md` with exact source/image identities, commands,
   controls, browser/Backup/APPLY/DRAFT/research/resource/cleanup results,
   reviewer attribution, and remaining deployment-only gates.
5. Scan each meaningful checkpoint for secrets/private data, push non-force to
   `codex/railway-staging-delivery`, and verify the remote tip. Do not touch
   main, PR #24, Railway, public endpoints, production, Shopify, or real
   purchasing.
