# Railway Staging Delivery Design

**Approved:** 2026-09-29  
**Risk:** Level 3 — authentication, private research data, database isolation, and procurement boundaries  
**Starting commit:** **e25704e117a3c5bede843cac65ddfdeb7f30024f**  
**Starting tree:** **5a76089cc0eadb7f51d54da255cb611a12e2e636**  
**Frozen research checkpoint:** **608929ad00adfd2eefaab29449743c5a3f0f035e**  
**Railway project/environment:** **5bdb474a-4af8-4187-b1be-be45594a22b2** / **5d8f28f5-168c-4eb9-8eef-6c639ae4cf46**  
**Railway application/PostgreSQL/bucket:** **cd0daada-9e8c-4f48-a71e-9151dde451c7** / **cb33800d-7bc4-4b44-817f-671978ea50ce** / **9d7e0ef1-e776-401d-8629-c407da96e3f0**

## 1. Objective and authority boundary

Create a tested, authenticated Railway staging candidate from the green PR #24
head without changing main, PR #24, the frozen accepted research checkpoint, or
accepted research artifact bytes. The candidate is staging-only even though the
Railway environment is named production.

The candidate demonstrates two deliberately separate capabilities:

- an owner-only synthetic purchasing workflow using fabricated canonical data;
- an owner-only, read-only view of the accepted private research snapshot.

It does not authorize production traffic, production database access, Shopify,
supplier contact, a real recommendation, PO release, purchasing, Replit
shutdown, or a source-branch merge. A successful staging deployment is not an
application-complete or production-ready declaration.

## 2. Chosen topology and rejected alternatives

The approved topology uses the existing Railway application service with one
replica, one public Python ASGI gateway, and a lifecycle supervisor that manages
two separate loopback workers:

1. a synthetic operational worker; and
2. an on-demand read-only research worker.

The gateway binds **0.0.0.0:$PORT**. Workers bind only to authenticated Unix
sockets and never to public or container TCP ports. Railway manages public TLS;
the application implements no TLS server or certificate lifecycle.

The public namespace is deterministic:

- **/auth/** is owned by the gateway;
- **/health** is a minimal gateway liveness response;
- **/private-research/** and its exact download routes go to the research
  worker; and
- explicitly registered operational routes go to the synthetic worker.

Unknown routes and unsupported methods fail closed. The gateway does not use a
catch-all rule that turns an unknown public path into worker access.

A unified FastAPI composition root was rejected because it would mix research
payload access and database credentials in one process and require a much
larger regression of the existing module-global applications. Two Railway
services were rejected for the first staging candidate because the gateway and
Unix-socket worker boundary provides the required separation without another
always-running billable service. A second service remains a future option only
if measured process or credential isolation proves insufficient.

## 3. Process and operating-system isolation

The image defines distinct operating-system identities for the supervisor,
gateway, synthetic worker, and research worker. A minimal root bootstrap creates
runtime directories, applies ownership and modes, launches each child with an
explicit UID/GID and environment allowlist, and then acts only as a signal and
child-lifecycle supervisor.

The workers have separate runtime directories and dedicated gateway/worker
groups. Socket parent directories are mode 0750 and socket files are mode 0660;
membership is limited to the gateway and the one corresponding worker. Startup
fails if a socket, parent directory, owner, group, type, or mode is unexpected.
Stale sockets are removed only after proving that no owned process is live.

The persistent volume has separate mode-0700 roots:

- the research root is owned only by the research worker;
- the synthetic storage root is owned only by the synthetic worker; and
- transfer/bootstrap staging is owned only by the supervisor and is removed
  after an atomic, verified promotion.

The research worker receives no database variables or database credential
files. The synthetic worker receives no research path, research manifest,
research capability, gateway verifier, or research worker secret. The gateway
receives neither database credentials nor research payload access. The root
supervisor is trusted only for bootstrap and lifecycle control and exposes no
network listener.

## 4. Owner authentication and sessions

There is exactly one owner credential. A local operator tool generates 32
random bytes from the operating-system CSPRNG and encodes them as an unpadded
base64url passphrase. The raw passphrase is written once to a fresh private
handoff directory outside Git with directory mode 0700 and file mode 0600. It
is never printed, passed on a command line, placed in an image/build argument,
or included in reports or logs. The owner receives it through that trusted
private handoff. The handoff is removed after owner receipt is explicitly
confirmed.

Only a PHC-formatted salted Argon2id verifier is stored as a Railway secret.
The implementation uses a maintained Argon2 library with **m=19456 KiB,
t=2, p=1**, the current OWASP minimum profile as of this design, and records the
parameters in the verifier. A startup self-check rejects another algorithm,
weaker parameters, malformed encodings, or an unexpected verifier source.
The parameter source is the OWASP Password Storage Cheat Sheet, accessed
2026-09-29:
https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html.

Password verification has a concurrency limit of one and no unbounded queue.
A global failure window and exponential cooldown bound online guesses without
depending on a client-supplied IP address. Login responses and timing are
generic. No username enumeration, registration, fallback account, password
reset, or anonymous mode exists.

Successful authentication creates an opaque 256-bit random session identifier.
Only a digest and server-owned session record are retained. Sessions have a
30-minute idle timeout and an 8-hour absolute timeout. They are invalidated on
logout, verifier rotation, or gateway restart.

The session cookie is:

- prefixed **__Host-**;
- **Secure**;
- **HttpOnly**;
- **SameSite=Strict**;
- **Path=/**; and
- emitted without a Domain attribute.

A pre-authentication, single-use CSRF challenge protects login. Authenticated
unsafe requests, including logout, require the exact configured HTTPS Origin
and a session-bound, constant-time-checked CSRF token. Ordinary top-level GET
and HEAD requests do not require an Origin header. No business mutation is
available through GET, HEAD, or OPTIONS.

## 5. External request trust policy

The exact external host and **https://** origin are configuration, not request
inputs. The gateway rejects duplicate or ambiguous Host, Origin, Cookie,
Content-Length, transfer-encoding, CSRF, and proxy security headers. The direct
Host must equal the configured staging host.

Railway proxy metadata is parsed through a fixed allowlist. Known Railway
headers may be validated and retained only as non-authoritative request
metadata. They never choose identity, capability, worker, socket, database,
scheme, host, redirect target, or authorization. Unknown Forwarded or
X-Forwarded variants, duplicate values, and malformed values fail closed.
Cookies are always Secure and redirects are built from the configured HTTPS
origin, not from proxy headers.

Request logs contain method, normalized route identifier, result class,
duration, and a generated request ID. They exclude query strings, bodies,
cookies, authorization values, CSRF tokens, credentials, worker assertions,
database URLs, source-derived titles, and private identifiers.

Responses apply HSTS, no-store, nosniff, a restrictive referrer policy,
frame denial, and a route-appropriate Content Security Policy. Health responses
contain no PID, commit history, credential state, database detail, file path,
artifact identity, or private count.

## 6. Gateway-to-worker authentication

The gateway does not log in to a local mode, fabricate a loopback browser, set
AUTOMATED_TEST, or rewrite an untrusted request until existing local
authorization accepts it. Existing local modes and their rejection semantics
remain unchanged.

Each worker instead receives a new staging-specific internal middleware.
For every allowed request, the gateway signs a short-lived assertion with a
worker-specific HMAC key. The canonical assertion binds:

- protocol version and worker role;
- generated request ID;
- gateway session digest;
- server-owned principal and role;
- exact capabilities selected by gateway policy;
- HTTP method;
- normalized route and query digest;
- request-body digest where applicable;
- configured external origin;
- issue and expiry times; and
- a random nonce.

The gateway strips every client-supplied internal-auth, principal, capability,
worker, and forwarded-auth header before routing. The worker accepts assertions
only on its owned Unix socket, checks the socket peer contract, HMAC, worker
role, method/path/body bindings, a short expiry, and a bounded nonce replay
cache, then constructs the existing server-owned principal/capability context.
The client cannot select a principal, capability, destination, socket, data
root, or database target.

Synthetic and research keys are independently generated at each supervisor
start, stored only in mode-0600 runtime files shared with their intended
gateway/worker group, and destroyed on shutdown. A key is never reused across
workers or restarts.

The gateway reaches the supervisor through a third permission-protected Unix
socket. That socket accepts only a small versioned protocol with
**research-start**, **research-stop**, and **research-status** operations.
Peer credentials and a boot-scoped control secret are verified. Requests
cannot contain an executable, command, argument, path, UID/GID, environment
entry, socket destination, or retry count; all such values are fixed in the
supervisor. Message length and rate are bounded.

Unsafe request bodies have explicit route-specific size limits. The gateway
buffers only those bounded bodies needed to verify CSRF and bind the internal
assertion; bulk uploads are not part of this staging contract.

## 7. Research dependency and provenance contract

The research input is the accepted corrected-V3 snapshot. The reported 75 files
and 1,030,997,932 bytes are an inventory claim, not sufficient proof. A
metadata-only transfer manifest is rebuilt from the accepted manifests and
contains the exact allowed relative path, file type, size, SHA-256, required
mode, ownership class, and dependency role for every member. The transfer
copies only that closure, never the whole Replit private root.

The closure includes the accepted inputs, delta material, parent inputs and
workspace, target workspace, immutable sidecars/blobs, and sealed source
dependencies required by current semantic replay. Known unavailable
source-page bundles remain explicitly unavailable; the staging process does not
invent or replace them. Source copies remain untouched.

The previously verified single-ref source bundle with SHA-256
**07009cb0442fc45b5a81da18f435f908e141fe2c8177ca1c2c455b9269d99079**
provides the historical Git commit/tree objects required by corrected replay.
It is reconstructed into a read-only bare object store under the research
volume. The research worker receives that object store through an explicit
Git-dir setting; startup proves SHA-1 format, no shallow boundary, no replace
refs, grafts, promisor, or alternates, strict object integrity, and every
required commit/tree/ancestry pair. It does not assume that Railway's source
checkout retains full Git history.

Transfer stages use fresh private directories, exact allowlists, no symlinks,
atomic promotion, and pre/post hash verification. Private payloads and the
bundle are absent from public Git and image layers. No credential or unrelated
evidence file is transferred.

## 8. On-demand research lifecycle

The research worker is absent at ordinary service startup. The first authorized
request to **/private-research/** asks the supervisor to begin initialization.
The gateway immediately returns a bounded not-ready response with Retry-After;
it never holds a browser connection open for the full replay.

One supervisor-owned state machine permits exactly one initialization graph:

**STOPPED → VALIDATING → READY → STOPPING**, with **FAILED** as a latched
failure state. Concurrent requests observe the same state and cannot spawn a
second worker. The state machine never serves a previous or partially verified
worker.

Initialization revalidates the complete transfer manifest, Git provenance, and
existing full semantic replay before marking READY. It preserves the accepted
research controls:

- 2,009 total variants;
- 1,365 CALCULATED;
- 644 NOT_APPLICABLE;
- zero BLOCKED;
- zero NOT_PROCESSED;
- zero corrected cumulative point violations;
- zero corrected cumulative target violations; and
- the exact accepted four artifact identities.

The replay does not recalculate a fresh forecast or change snapshot dates. The
UI retains research-only and target-not-buy labels. Downloads are limited to
the exact four registered artifact names and are streamed from verified files
without buffering another complete artifact.

Initialization has the existing 45-minute ceiling. A crash or timeout triggers
process-group termination, reaping, socket/runtime cleanup, and one retry after
a bounded backoff. A second failure latches FAILED until an authenticated owner
requests an explicit retry. READY workers shut down after 30 minutes without an
authenticated research request. Every worker restart performs the complete
manifest, provenance, and semantic validation again; no hash-only or stale
fallback is served.

Hard resource gates cover the combined service, not isolated processes:

- each process peak RSS remains strictly below 5 GiB;
- service cgroup peak remains strictly below 6 GiB;
- OOM, OOM-kill, and group-OOM counters remain zero; and
- no paid-plan increase or hidden limit change is used to obtain a pass.

## 9. Synthetic database and workflow contract

The authoritative loopback test initializer and its database guards remain
unchanged. No Railway credential is supplied to that test runner.

The canonical V2 synthetic fixture is initialized and verified locally against
a disposable PostgreSQL 16 instance using the existing guarded tools. A
custom-format dump and a canonical metadata manifest are then created in a
private mode-0700/0600 transfer root. The manifest binds the source fixture
version, schema/migration identities, database object inventory, row/control
totals, and dump SHA-256/size. The dump is not committed or added to an image.

Restoration is a separate one-time operator action, never web startup. Before
restore, the operator positively verifies the exact Railway project,
environment, PostgreSQL service, private endpoint, PostgreSQL major version,
destination database/schema, and that the destination is dedicated and safe
to initialize. An unexpected existing object or marker is a hard stop; there is
no destructive reset fallback.

The restore creates a dedicated synthetic database and least-privileged runtime
role. The runtime role does not own the database, cannot create databases or
roles, and has no access to another database/schema. Administrative restore
credentials are removed from the application service after verification.

A new fail-closed staging target attestation binds:

- the exact Railway project, environment, app, and PostgreSQL service IDs;
- the expected private database host and PostgreSQL 16;
- the exact dedicated database and schema;
- the immutable in-database fixture/restore marker;
- the manifest hash and expected canonical control totals; and
- disabled production, Shopify, order, and PO-release authorities.

Every synthetic database entry point uses one centralized target verifier.
Tests prove refusal for missing/mixed IDs, another host/database/schema,
another PostgreSQL major, absent or tampered markers, unexpected capabilities,
and canonical/production targets. The new staging target is additive; it does
not weaken loopback requirements for SYNTHETIC_DEMO tests.

Acceptance proves the existing fabricated workflow end to end:

- 138 fabricated days, 966 sales rows, and seven variants;
- validated inputs, selected supplier/pricing, demand and net need, and human
  quantity review;
- seven decisions and five selection heads;
- two separate vendor DRAFTs, three lines, and a 14-member packet;
- fabricated merchandise of $282 plus a $7 fee, totaling $289; and
- byte/reconciliation checks for downloads and packet members.

All output remains labeled synthetic and nonoperational. Production and
Shopify authority remains false.

## 10. Build, startup, and configuration

The staging branch adds a reproducible multi-stage container build. It pins a
Python 3.13 patch-level base image by digest, uses the repository's locked
Python dependencies with the pinned uv version, and installs only required
runtime packages. The unrelated Node scaffold is not built or deployed.

The final image contains application source and public runtime dependencies
only. It contains no owner passphrase/verifier, database credential, private
research payload, synthetic dump, transfer archive, session key, worker key,
or temporary evidence. The image runs one replica and one supervisor process.

Startup validates exact Railway-provided project/environment/service identities,
the configured host/origin, single-replica assumption, volume type/mount and
ownership, socket/runtime roots, role-specific credential files, and absence of
production authority. It starts the gateway and synthetic worker but performs
no migration, seed, restore, forecast build, research replay, or destructive
data operation.

The public liveness endpoint proves only that the gateway event loop is alive.
An authenticated readiness endpoint reports coarse component states without
secrets or private identifiers. Deployment acceptance therefore uses browser,
database, artifact, restart, and resource evidence in addition to Railway's
health result.

Autodeploy and cron remain disabled. The branch is deployed manually by exact
commit. Deployment evidence records both Railway's deployment ID/source fields
and an application-reported public source identity that excludes private data.

## 11. Testing and review

Focused tests cover:

- verifier parsing/strength, correct and wrong passphrases, verification
  concurrency, cooldown, and credential rotation;
- session creation, idle/absolute expiry, restart invalidation, logout, and
  replay;
- login/logout and business-route CSRF;
- duplicate, spoofed, malformed, and unknown proxy/security headers;
- host/origin/redirect fixation and cookie attributes;
- explicit route/method policy and unknown-route refusal;
- direct socket/worker access, assertion expiry/replay/body/path/role mismatch,
  and cross-worker key misuse;
- worker UID/GID, environment, socket, and filesystem boundaries;
- route traversal, source-derived escaping, and unauthorized downloads;
- missing/tampered/extra research dependencies and Git provenance;
- concurrent cold research starts, bounded retry, crash cleanup, idle stop,
  restart revalidation, streaming downloads, and resource sampling;
- wrong database, service identity, PostgreSQL version, fixture marker,
  manifest, role privilege, and production-authority refusal; and
- exact canonical research and synthetic controls.

Container tests prove the image starts from a clean Git checkout with no
Replit path, global package, or injected identity dependency. They exercise the
gateway and workers with a disposable local PostgreSQL fixture and metadata
attestations, never Railway or production credentials.

After focused tests, the authoritative repository suite must retain all
existing 1,003 application tests plus every newly registered test, the 10
startup tests, and zero failures, errors, skips, expected failures, or
unexpected successes. Existing discovery floors are raised only to the frozen
actual new inventory.

Same-model subreviews are supplementary. A qualified attributable independent
reviewer must inspect the actual design, complete diff, authentication and
proxy logic, worker boundary, private-data allowlist, database target
attestation, tests, and deployment configuration. If Claude Code remains
unauthenticated, local implementation and synthetic/container validation may
finish, but the public domain and private research transfer remain blocked.

## 12. Gated Railway delivery and acceptance

Only after focused security tests, the full suite, container validation, and
independent review pass may the operator:

1. push the exact reviewed staging branch;
2. bind the existing application service to that branch with autodeploy off;
3. create the smallest sufficient persistent volume and record its cost;
4. set the verifier and scoped service configuration through secret-safe
   transport;
5. restore and verify the dedicated synthetic database;
6. transfer and verify the exact research closure and Git bundle;
7. deploy the exact reviewed commit;
8. create the Railway-managed domain; and
9. perform authenticated and unauthorized browser acceptance.

Acceptance requires terminal deployment SUCCESS, exact source/image identity,
unauthorized rejection, synthetic/research separation, exact controls and
downloads, clean research cold start, restart and redeploy persistence,
measured startup and combined memory, zero OOM events, no unexpected
deployment/scheduler, and no production/Shopify/supplier activity.

The first staging candidate uses one app service and one persistent volume.
The expected new storage footprint is approximately 1 GiB of actual data. The
Railway volume rate observed on 2026-09-29 is $0.15 per GB-month, plus the
existing app compute while running. The operator rechecks the price before
volume creation and stops for owner direction if it changed materially. No plan
upgrade or unrelated paid resource is authorized.

If any gate fails, the candidate remains private or has no domain, failed
workers are stopped, temporary credentials/tools are removed, and immutable
evidence is retained privately. A failure is never converted into acceptance
by increasing limits, weakening a check, or serving stale data.

## 13. Replit-exit evidence and stop boundary

The final handoff records separately:

- whether the app can be built and hosted from GitHub/Railway;
- whether source recovery is portable;
- whether the exact private research closure is backed up and staged;
- whether development and test execution are portable; and
- that operational database migration/cutover remains pending.

The production database migration plan may distinguish Replit development
heliumdb from published neondb, but this task does not access, export, import,
or mutate either one.

Stop after the bounded staging candidate, evidence, documentation, and remaining
blockers. Do not merge or update main, alter branch protection, touch Replit
production, migrate production data, transfer production secrets, create
consequential schedules, call Shopify, make a real purchasing recommendation,
release a PO, transmit to a supplier, or cut over production traffic.
