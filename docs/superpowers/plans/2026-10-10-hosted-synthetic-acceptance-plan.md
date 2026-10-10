# Hosted Synthetic Acceptance Implementation Plan

**Design:**
`docs/superpowers/specs/2026-10-10-hosted-synthetic-acceptance-design.md`

**Approved baseline:** `cb0969059b0b85241d611124910d691e49212881`

**Risk:** Level 3

**Writer/operator:** Codex only; all reviewers read-only

This plan is intentionally gate-ordered. Work stops after Task 3 if the hosted
capability preflight fails. Tasks 4–9 proceed only after a source-bound hosted
PASS and may stop sooner if the positive harness requires a change outside the
owner-approved contracts.

## Task 1 — Publish and preserve the baseline

1. Verify the exact clean commit/tree and reproduce the bounded unpublished-
   history scan.
2. Authenticate only through the official GitHub device/browser flow.
3. Re-read live `codex/railway-staging-delivery`, `main`, and PR #24 refs
   immediately before push.
4. Push the exact baseline by non-force fast-forward to
   `refs/heads/codex/railway-staging-delivery` and fetch it back.
5. Verify the remote commit/tree and unchanged protected refs.
6. Create `codex/hosted-synthetic-acceptance` at that exact checkpoint.

Evidence: local and remote identities, scan counts/hashes, fast-forward
ancestry, and unchanged ref ledger.

## Task 2 — Implement the capability preflight and workflow

Files expected:

- add a small capability entry point under `procurement/tools`;
- add focused tests under `procurement/tests`;
- add `.github/workflows/hosted-synthetic-acceptance.yml`;
- update `procurement/tools/run_tests.py` and both floor guards;
- update `docs/CODEX_HANDOFF.md` at the reviewed checkpoint.

Implementation order:

1. Add source-contract tests for the exact branch-only workflow trigger,
   read-only permission, Ubuntu 24.04 runner, ten-minute timeout, full-SHA
   action pins, nonpersistent checkout, immutable checkout assertions, and
   absence of PR/schedule/manual/reusable triggers, secrets, OIDC, artifact
   upload, and write permissions.
2. Factor only the accepted low-level pidfd, namespace, mount, projection,
   guardian, and cleanup primitives needed by a success-capable probe. Preserve
   the existing local EPERM blocker observer and its tests unchanged.
3. Implement the disposable private-proc probe with exact USER/MOUNT/PID/NET
   namespaces, UID/GID maps, private propagation, fd-based proc mount plus the
   explicit legacy fallback, mount flags, `NSpid`, and host-process invisibility.
4. Implement the tiny detached tmpfs executable sentinel and prove read-only,
   nosuid, nodev, executable, `EROFS` on write, identity-bound detach, and zero
   descriptor/mount residue.
5. Implement the exact owned cgroup v2 child using the same launcher privilege
   path, memory/pids controls, freeze semantics if used, stopped enrollment,
   membership evidence, and zero OOM deltas.
6. Place the fabricated 43-byte release provider after every attestation.
   Every failure path must prove provider calls `0` and payload processes `0`.
7. Emit bounded canonical credential-free JSON and a digest; make incomplete
   cleanup an overall failure.
8. Add cancellation, stale PID/FD/name, foreign replacement, concurrent
   generation, timeout, and partial-transition regressions.

Focused validation:

- compile/AST and `git diff --check`;
- the new capability/workflow tests;
- the existing 48 browser-containment tests;
- floor/registration guards with exact increased counts; and
- repeated live disposable success/failure cleanup probes where the current
  host supports them.

## Task 3 — Review, publish, and run the capability checkpoint

1. Freeze the exact tool/test/workflow/docs hashes.
2. Run secret/private-data and generated-artifact scans, the complete
   authoritative suite with actual discovered/floor counts, and deterministic
   startup validation 10/10 against that exact candidate.
3. Obtain independent read-only review of workflow authority, containment,
   release ordering, cancellation, and cleanup.
4. If review finds a material defect, remediate it, run the smallest causal
   tests, then rerun the required full authoritative suite and startup 10/10,
   refreeze exact hashes, and obtain independent review re-anchored to the
   changed candidate. Repeat this validation/review loop until clean.
5. Commit and push the acceptance branch non-force.
6. Observe the exact GitHub Actions run and jobs through completion.
7. Verify the run event/ref/SHA/tree/workflow identities and collect bounded
   log evidence.
8. Record `HOST_CAPABILITY=PASS|FAIL`, cleanup, fabricated release count,
   `procfs_scope=ACCEPTANCE_TOOLING`, and
   `railway_compatibility=NOT_PROVEN` in `CODEX_HANDOFF.md`.

If the host gate fails, stop here. Preserve the failure, do not rerun an
unchanged restriction, and leave all later gates `NOT RUN`.

## Task 4 — Specify and prove the positive runtime composition

This task begins only after Task 3 PASS.

1. Re-run the integrated capability gate in the same VM/generation intended
   for payload execution.
2. Extend the stopped guardian into an owned live namespace-init lease that
   retains private procfs, projection, and cgroup until worker completion.
3. Define the narrow network composition for only task-local TLS and CDP while
   retaining the accepted network-isolation guarantee.
4. Construct hash-bound hosted closures for Python, Node,
   Chromium/helpers/fonts/NSS, TLS, and source identity. Do not accept a
   runner-installed binary by name/version alone.
5. Bind exact cgroup, namespace, projection, process, environment, descriptor,
   and generation evidence before enabling a credential provider.
6. Add owned cleanup leases and failure-precedence tests for every new live
   resource.

Stop and report the exact dependency if any safe solution requires new
application architecture or weaker accepted containment.

## Task 5 — Implement the executable worker and supervisor paths

1. Keep ordinary worker invocation inert. Add one hidden exact-FD dispatch
   mode with no ambient argument, environment, or descriptor authority.
2. Validate REQUEST and current-generation source/projection/process/cgroup/
   namespace evidence before emitting READY.
3. Deliver the exact one-shot task-local secret only after frozen
   re-attestation. Read once, close, and scrub all mutable buffers.
4. Execute the selected staging browser phase and emit only a canonical,
   phase-specific RESULT after independent proof validation.
5. Replace the acceptance `main()` placeholder with an exact command surface
   for the canonical hosted synthetic scenario; retain failure for every other
   invocation.
6. Prove child/browser failure, cancellation, stale generation, changed source,
   dependency drift, and partial execution cannot produce PASS.

## Task 6 — Add the staging APPLY/downstream browser phase

1. Preserve the existing staging confirmation phase byte-for-byte unless a
   reviewed interface change is required.
2. Port only the canonical multivendor DOM actions into a new staging phase.
3. Use the staging gateway's authentication and CSRF model; do not port the
   legacy upload, plaintext endpoint, review token, or price token.
4. Validate the phase-specific proof for APPLY, three mapping batches, seven
   candidates/decisions, five selection heads, exact blocker exclusions,
   reviews, DRAFT build/replay, and downloads.
5. Reuse the existing trusted database/artifact validators to independently
   derive two DRAFTs, three lines, 14 members, `$282 + $7 = $289`.

## Task 7 — Orchestrate canonical Option B

1. Create and bind distinct disposable loopback PostgreSQL source/target
   clusters.
2. Run export, target preparation, restore, and SCRAM transition.
3. Prove final-state absence, then stage the fixed fixture while stopped to
   `VALIDATED`.
4. Start the real staging composition, keeping private research `NOT RUN`.
5. Launch task-local TLS and Chromium, run CONFIRM, and prove
   `VERIFIED_FUTURE`.
6. Stop, create/verify Backup V2, bind it through trusted configuration, and
   restart.
7. Run the APPLY/downstream browser phase and reconcile all outputs.
8. Restart and prove the same durable state and byte-identical downloads.
9. Restore the populated backup into a distinct recovery target and reconcile
   the required control totals.

## Task 8 — Resource and cleanup acceptance

1. Sample all owned service/worker/browser descendants by stable process
   identity through every phase and restart.
2. Preserve and attest the existing service hard memory limit of 5 GiB minus
   one page; require each process peak below 5 GiB, service cgroup peak below
   6 GiB, and zero `oom`, `oom_kill`, and `oom_group_kill` deltas.
3. On success, failure, timeout, cancellation, or reviewer fault injection,
   clean all owned processes, clusters, containers, mounts, cgroups, listeners,
   keys, secret buffers, temp roots, and non-allowlisted evidence.
4. Compare before/after inventories and refuse PASS on any residue or foreign
   resource mutation.

## Task 9 — Freeze, review, execute, and close out

1. Freeze exact commit/tree/workflow/application/harness/worker/dependency
   identities and scan the full newly publishable range.
2. Run focused tests, floor guards, the authoritative suite with actual counts,
   and deterministic startup 10/10.
3. Obtain attributable independent broad and containment-specialist review;
   for every material remediation, rerun affected/focused tests plus the
   authoritative suite and startup 10/10, refreeze exact hashes, and obtain
   both reviews re-anchored to the changed candidate. Repeat until no in-scope
   P0/P1/P2 remains.
4. Push the reviewed checkpoint non-force and run the exact hosted workflow.
5. Record each gate separately:
   `SOURCE_PUBLICATION`, `HOST_CAPABILITY`, `EXECUTABLE_HARNESS`,
   `SYNTHETIC_BROWSER_WORKFLOW`, `BACKUP_RESTORE`,
   `RESOURCE_AND_CLEANUP`, and `INDEPENDENT_REVIEW`.
6. Update `docs/CODEX_HANDOFF.md` with exact run/job IDs, source bindings,
   totals, cleanup, blockers, and next authority. Update `PHASE_STATUS.md` only
   if the official program milestone changes.

No task in this plan authorizes main/PR #24 mutation, a new PR, merge,
Railway/public endpoint mutation, external application-database access,
private research transfer, Shopify, real purchasing, supplier transmission,
or Replit shutdown.
