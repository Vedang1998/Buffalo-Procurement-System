# Railway Staging Delivery — implementation plan

**Design:** docs/superpowers/specs/2026-09-29-railway-staging-delivery-design.md  
**Frozen parent:** e25704e117a3c5bede843cac65ddfdeb7f30024f  
**Frozen parent tree:** 5a76089cc0eadb7f51d54da255cb611a12e2e636  
**Design commit:** b9cf8aa9e80f0bfee5e00da9588d09427d25c3df  
**Design tree:** ed8dbc6823f8923f2f1c87ac4712968949fc2405

## Constraints

- Codex is the sole writer in the isolated codex/railway-staging-delivery
  worktree. Review agents are read-only.
- Preserve main, PR #24, the accepted research checkpoint, and accepted
  artifact bytes. No merge, branch-protection change, or PR #24 mutation.
- Do not expose a public domain or transfer private research payloads until the
  focused security tests, authoritative suite, container validation, and a
  qualified attributable independent review all pass.
- Keep legacy local modes and their rejection behavior intact. Never make a
  request appear local or automated merely to pass existing authorization.
- Tests use only disposable loopback PostgreSQL. Railway credentials never
  enter the authoritative test runner.
- No migration, seed, restore, research replay, or forecast generation runs on
  ordinary web startup.
- No Replit production, production database, Shopify, supplier, PO release,
  purchasing, schedule, or traffic-cutover action is in scope.

## Task 0 — Reprove the implementation baseline

- Verify the staging branch is a clean descendant of exact commit
  e25704e117a3c5bede843cac65ddfdeb7f30024f and that its parent tree is
  5a76089cc0eadb7f51d54da255cb611a12e2e636.
- Bind the green hosted CI evidence for 1,003 application tests and 10 startup
  tests to the exact parent tree.
- Reconfirm the authorized Railway project/environment/app/PostgreSQL/bucket
  identities read-only. Record that the app has no deployment, domain, source,
  variables, volume, or cron before staging changes.
- Reconfirm Claude Code authentication once. If unavailable, record the gate
  and continue local work without repeated login attempts.
- Run the smallest baseline modules that cover local access, storage, private
  research app, corrected lineage, and startup before production edits.

## Task 1 — Lock failing authentication and gateway-policy tests

Add focused tests before implementation for:

- PHC Argon2id parsing and minimum parameters;
- correct/wrong passphrase handling, single verification concurrency, bounded
  queue refusal, failure window, cooldown, and verifier rotation;
- opaque sessions, idle and absolute expiry, logout, restart invalidation, and
  constant-time token checks;
- pre-auth login CSRF and authenticated logout/business CSRF;
- exact Secure/HttpOnly/SameSite=Strict/__Host- cookie behavior;
- exact configured Host/Origin and redirect construction;
- duplicate/malformed/unknown Host, Origin, Cookie, Content-Length,
  Transfer-Encoding, Forwarded, X-Forwarded, and internal-auth headers;
- route/method allowlisting and unknown-route refusal;
- minimal health and secret/query/body/log redaction.

Implement these tests in new staging-specific modules. Existing local-access
tests remain unchanged except for additive assertions proving the local branch
is byte-semantically unaffected.

## Task 2 — Implement owner authentication and the public gateway

Add small, bounded modules for:

- immutable staging configuration and exact Railway identity validation;
- Argon2id verifier validation and the private passphrase-generation operator
  command;
- server-side session and CSRF state;
- global bounded login throttling;
- external request normalization and proxy-header policy;
- explicit public route/method policy; and
- the ASGI gateway and security headers.

Use the locked dependency file for the maintained Argon2 library. Keep the
gateway free of database and research filesystem access. Ensure every response
path, including exceptions, applies the security headers and no-store policy.

Run only the new gateway/auth tests and existing local-access tests until they
pass.

## Task 3 — Lock and implement the internal worker protocol

Add failing tests for:

- canonical HMAC assertions and every bound field;
- expiry, future issue time, replayed nonce, altered path/query/body/method,
  wrong worker role, wrong key, and cross-worker key refusal;
- direct worker access without a valid assertion;
- fixed principal/capability construction;
- socket type, path, owner, group, and mode checks;
- distinct runtime roots, keys, environments, UIDs/GIDs, and groups;
- the fixed research-start/research-stop/research-status supervisor protocol;
- refusal of commands, paths, environment values, UIDs/GIDs, destinations, or
  retry counts supplied by a caller; and
- signal, process-group, stale-socket, timeout, and crash cleanup.

Implement a staging-only internal middleware for the existing synthetic app and
research app. Do not route through the existing loopback login or alter its
meaning. Implement the minimal supervisor and Unix-socket lifecycle only after
the protocol tests fail as expected.

Run protocol, supervisor, local-access, startup, and private-research-app tests.

## Task 4 — Lock and implement the research transfer contract

Create a metadata-only allowlist builder and validator driven by the accepted
manifests. Tests must prove:

- exact path/type/size/SHA-256/mode/ownership-role coverage;
- the original 75-record / 1,030,999,197-byte membership and accepted inventory
  identity remain unchanged historical evidence;
- the additive deployment closure is exactly 77 records / 1,031,003,702 bytes:
  73 host records / 1,030,618,184 bytes plus the unchanged four Git records /
  385,518 bytes, with only the two reader-pinned A1 auxiliaries (4,505 bytes)
  added;
- refusal of missing, extra, duplicate, symlink, traversal, changed-mode,
  changed-owner, changed-byte, or unrelated files;
- known unavailable source bundles remain explicitly unavailable;
- atomic promotion never exposes a partial closure; and
- source files remain unchanged.

Add a source-bundle validator for exact SHA-256
07009cb0442fc45b5a81da18f435f908e141fe2c8177ca1c2c455b9269d99079,
single-ref reconstruction, strict Git integrity, no shallow/replace/graft/
promisor/alternate state, and every required commit/tree/ancestry pair.

No private payload body or source bundle is added to Git or an image.

## Task 5 — Lock and implement on-demand research lifecycle

Add state-machine and integration tests for:

- STOPPED, VALIDATING, READY, STOPPING, and latched FAILED;
- one cold-start graph under concurrent authorized requests;
- immediate bounded not-ready responses with Retry-After;
- a 45-minute initialization limit;
- one bounded automatic retry, second-failure latch, and explicit owner retry;
- process-group reaping, socket/key/temp cleanup, and no stale fallback;
- ownership/type/mode-checked recovery of crash-left unpublished research
  release/control staging directories before a retry or transfer;
- 30-minute authenticated-idle shutdown;
- full manifest, Git, and semantic revalidation after every restart;
- no database environment in the worker;
- exact research controls and artifact identities; and
- streaming of only the four allowed downloads without duplicate buffering.

Integrate the existing corrected semantic replay rather than replacing it with
a hash-only attestation or a new forecast build. Add resource sampling hooks
that reveal only aggregate process/cgroup measurements.

## Task 6 — Lock and implement Railway synthetic-target attestation

First centralize the existing synthetic database target decision without
weakening any current loopback predicate. Add failing tests for the additive
Railway staging target:

- exact project/environment/app/PostgreSQL service IDs;
- exact private host, PostgreSQL 16, dedicated database, and schema;
- exact fixture/restore marker and manifest hash;
- least-privileged role identity and privilege constraints;
- production, Shopify, order, and PO-release authority false; and
- refusal of missing, partial, mixed, altered, canonical, production, or
  unmarked targets.

Update every synthetic database consumer to call the one typed verifier. The
authoritative loopback test mode must continue to refuse passwords and remote
hosts exactly as before.

Run persistent mapping, selected-offer, price-replacement, recommendations,
local-access, and end-to-end purchasing modules against an owned disposable
PostgreSQL 16 database.

## Task 7 — Build the canonical synthetic transfer and restore tooling

Add operator-only commands that:

1. invoke the existing guarded initializer against an owned disposable
   PostgreSQL 16 database;
2. prove the canonical V2 controls;
3. produce a private custom-format dump plus canonical metadata manifest;
4. preflight the exact Railway destination without exposing credential values;
5. refuse any unexpected destination object/marker;
6. restore once using an administrative role;
7. create/grant the least-privileged runtime role;
8. revalidate schema, controls, marker, role privileges, and dump identity; and
9. remove administrative credentials from app scope.

Dump/manifest output is outside Git with 0700/0600 protection. Ordinary service
startup never invokes these commands. Unit and disposable-database tests cover
every fail-closed branch; no Railway credential is used during tests.

## Task 8 — Add the reproducible container and startup contract

Add a multi-stage Docker build with:

- a Python 3.13 patch image pinned by digest;
- the repository's frozen lock and pinned uv;
- only required runtime packages;
- distinct OS users/groups and a minimal init/supervisor;
- no Node scaffold;
- no private payload, secret, dump, archive, or evidence layer; and
- an unprivileged public gateway process.

Add deployment configuration for one replica, $PORT, minimal /health, volume
mount, no cron, and manual deployment/autodeploy-off semantics. Startup checks
the exact Railway identity, configured host/origin, one-replica assumption,
volume and runtime paths, role-specific credentials, and disabled production
authority.

Container tests build from a clean Git checkout, scan layers/config/history for
secret/private-data markers, prove correct users/modes, exercise the public
gateway and both workers, and verify no Replit path or global package
dependency.

## Task 9 — Integrated local acceptance

> **2026-10-05 additive amendment:** the LOCAL run uses the stopped-service,
> exact-fixture `VALIDATED` staging boundary in the
> [Local Staging Price Bridge Amendment](../specs/2026-10-05-local-staging-price-bridge-amendment.md).
> This replaces only the browser-upload step; confirmation, Backup V2, APPLY,
> and the rest of the canonical browser workflow remain unchanged.

Against a disposable local PostgreSQL 16 target and a synthetic mounted-volume
fixture:

- authenticate through the real gateway;
- prove anonymous/wrong/expired/replayed/CSRF/proxy attacks fail;
- complete the canonical synthetic browser workflow;
- verify two DRAFTs, three lines, 14 packet members, $282 merchandise, $7 fee,
  and $289 total;
- cold-start research once, verify 2,009/1,365/644/0/0 and zero corrected
  coherence violations;
- download and hash all four accepted artifacts;
- exercise concurrent access, idle stop, crash/retry, and full restart;
- measure each process RSS and aggregate cgroup peak; and
- prove zero OOM events and complete process/socket/temp cleanup.

The first realistic research replay may run only after static/focused tests and
memory-capture preflight pass. Never retry an OOM or semantic failure blindly.

## Task 10 — Freeze candidate, run full validation, and obtain review

After the implementation diff is complete:

1. run formatting, compilation, lock, Docker, shell syntax, Git, secret, and
   generated-file checks;
2. run the 10 startup tests;
3. run the authoritative suite once, preserving the original 1,003 tests plus
   all newly registered tests and zero abnormal counters;
4. run the integrated browser/restart/resource acceptance once;
5. freeze an exact candidate commit/tree;
6. obtain supplementary same-model read-only reviews;
7. obtain a qualified attributable independent review of the actual design,
   diff, tests, auth/proxy protocol, worker isolation, database attestation,
   private-data allowlist, and deployment configuration; and
8. fix findings only through Codex, rerun affected tests, then rerun the final
   authoritative gates on the new exact candidate.

If Claude Code remains unauthenticated, stop the delivery package at a locally
tested, private, nonoperational candidate. Do not create a domain or transfer
private research payloads.

## Task 11 — Gated Railway configuration and private transfer

Only after Task 10 gates pass:

- re-read exact Railway scope and current pricing;
- install/authenticate approved Railway tooling without exposing secrets;
- bind the existing app service to the exact staging branch with autodeploy
  disabled;
- create the minimum app volume and exact directory ownership/modes;
- set the verifier and scoped runtime configuration through secret-safe input;
- run the one-time synthetic restore and remove admin access;
- transfer the exact research allowlist and source bundle, then verify every
  destination hash/mode/provenance fact;
- deploy the exact reviewed commit with one replica and no cron; and
- record the deployment/source/image identity and resource configuration.

No public domain exists until transfer, deployment, private-network checks, and
log/secret scans pass.

## Task 12 — Railway browser, persistence, and resource acceptance

Create Railway-managed HTTPS only after every earlier gate passes. Then:

- prove anonymous, wrong-credential, CSRF, host, origin, proxy-header, direct
  worker, traversal, and unauthorized-download rejection;
- run authenticated synthetic and research browser flows;
- verify exact controls, downloads, labels, and no production authority;
- restart and redeploy, proving database, volume, credential rotation,
  research revalidation, and artifact persistence;
- record startup, per-process RSS, service peak, OOM counters, process/socket
  cleanup, and ongoing app/volume footprint;
- verify no Shopify/supplier/outbound production activity, no cron, no
  unexpected deployment, and no second replica; and
- remove temporary credentials, admin tooling, transfer staging, and transient
  evidence from the service.

Any failed acceptance gate removes or withholds user exposure and leaves the
candidate private/nonoperational. It never triggers a production containment
action outside this scope.

## Task 13 — Documentation and bounded closeout

Update docs/CODEX_HANDOFF.md with exact commits/trees, reviewer attribution,
commands/counts/hashes, data scope, deployment identity, access method, browser
and persistence evidence, memory/OOM/resource footprint, Replit-exit checklist,
and remaining blockers. Update procurement/docs/PHASE_STATUS.md only if a
verified program milestone changed.

Return a staging URL only if every gate passed. Otherwise return the exact
blocked gate and keep the service unexposed or nonoperational.

Prepare but do not execute a future production database migration/cutover plan
that distinguishes development heliumdb from published neondb. Do not merge,
touch main/protection/Replit production, migrate production data or secrets,
call Shopify, schedule consequential jobs, make real purchasing decisions,
release a PO, contact a supplier, or cut over traffic.
