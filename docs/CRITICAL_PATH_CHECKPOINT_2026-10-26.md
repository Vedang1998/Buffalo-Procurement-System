# October 26 critical-path implementation — WIP, NON-GREEN

Base: `b4044194137669f6203c85276af8f23d0e500cea`, tree
`273123a18b0245ba634109c6af2665b26ca368b9`. The base and all thirteen published
review evidence hashes were verified before work. Metadata recovery is already
published; no recovery rewrite, identity investigation or repackaging occurred.
Codex is the sole writer. Official phase numbering/status is unchanged.

## Material implementation

The new `hosted_synthetic_cgroup.py` implements the owned Task2 cgroup component:
actual cgroup2 filesystem verification, held directory/control/mount identities,
finite page-aligned memory limit below 5 GiB, pids.max=2, memory.oom.group=1,
strict counters and zero OOM deltas, exact process/thread membership, and
stopped/childless/single-thread init enrollment without a resume operation.
Enrollment checks both I and G pidfds/start identities, G parent S, and G/S
outside the limited child before and after the write. The caller must retain
unreaped ownership and the deferred-cancellation envelope.

Cleanup removes only an observed empty original, handles renamed originals,
preserves observed replacements and unexpected members, closes owned descriptors,
and makes cleanup failure sticky. OOM violations still fail while permitting
safe removal of an empty original. Copied leases cannot act as the owner.
Linux name-based mkdir/open and rmdir are not atomic identity-conditional
operations: the eventual composition must establish exclusive delegation access
and exclude untrusted cgroup filesystem writers. This component alone does not
prove security against hostile concurrent same-UID pathname replacement.

The lease is not yet connected to the namespace handshake. The next integration
must preserve the existing ARMED/PDEATHSIG handshake, retain stopped I and G,
enroll I before PROBE, then prove inherited exact I/W membership. Sentinel
execution, retained runtime closure and the one-shot fabricated release remain
incomplete. No credential-bearing execution is enabled.

The new `hosted_synthetic_host.py` runs only a bounded read-only prerequisite
observation in the existing scrubbed, source-bound diagnostic. It measures
fixed native pg_dump/pg_restore paths, root-owned non-writable ancestor and leaf
metadata, content hashes, exact major16 versions and current cgroup ancestry.
Version subprocess output is capped at256 bytes with a3-second deadline and
owned child cleanup. Program atime changes are permitted; identity/content
metadata changes refuse. This is metadata/version observation, not invocation
of the application Backup V2 validator or acceptance of a runtime closure.

Missing PostgreSQL or delegation prerequisites now prevent repeating namespace
mutations. Reports retain HOST_CAPABILITY=FAIL, runtime UNSEALED, provider_calls=0,
credential_release=WITHHELD and execution_authority=false. The workflow writes
bounded canonical output to its public job summary outside the scrubbed process;
it preserves the actual failure exit code, exact branch-only trigger, pinned
actions, ten-minute bound, read-only permission, no credentials and no sudo.

## Published material and observability correction

Initial material `da082cef7b6bec8c29265cc892be3afd374fa7e4`, tree
`eb69c619cc2bd2b30876dc6412ae936ef385641c`, was secret-scanned, ordinarily
published and freshly fetched. Exact frozen validation passed hosted199/199,
related88/88 and startup10/10, with all fourteen changed-file hashes unchanged.
Raw evidence is in [review/2026-10-26-critical-path](review/2026-10-26-critical-path/manifest.json).

GitHub run40361057089/job121706277941 binds that push and exact workflow
SHA25687553d50d8ef980ce4d0d7dd1cf27173ea262a28d472bd271aaf2c30ae1e59ee.
Checkout/source/Python/focused199 succeeded; the observer exited1. The public
run/job HTML did not expose its canonical JSON or summary and required sign-in
for logs. Therefore specific host refusal and hosted cleanup were unobserved;
step failure alone does not prove a kernel restriction.

The follow-up changes observability only: a bounded fixed-title notice annotation
escapes percent/CR/LF and retains the diagnostic exit code. A shell-executed
regression proves synthetic annotation-command text cannot inject a new command.
This adds one workflow test: hosted200, total discovery/floor1727 in96modules.
No native known-failure rerun or privilege change is part of this correction.


The public notice from b1529ccfbf6014d493664cb4d5f8740e7998175f run40362033756 /
job121709746382 is now observable. Focused200 passed; the report refused with
SOURCE_OR_SUPERVISOR_PREREQUISITE_REFUSED, commit/tree null and host_prerequisites
null. PG/cgroup observation never ran. Do not infer a host restriction from this
result or enable the proposed privileged configuration before diagnosing it.
A bounded follow-up preserves all checks and exposes only code-owned refusal
categories, phase and numeric errno; no raw exceptions/environment/paths. Two
regressions increase hosted count to202 and global discovery/floor to1729/96.

## Host capability and smallest proposed alternative

Native Cloud's retained complete observation remains authoritative: no genuine
trusted native PostgreSQL16 tools, no writable cgroup delegation, no supported
privilege/system-manager helper. It was not rerun. The ordinary container's
50/50 fixture proof remains valid only for that earlier tested material and
fixture environment; it does not supply same-launcher native delegation.

Official public GitHub runner documentation supports passwordless sudo on full
Ubuntu VMs. The Ubuntu24.04 image documentation lists PostgreSQL16.15; actual
identity/version and delegation still require observation. systemd documents
Delegate=memory pids with User= ownership, but requesting controllers does not
prove they were supplied. No unprivileged user-manager delegation guarantee was
found. The current workflow expressly prohibits sudo.

If the bounded hosted observation finds no delegation, the smallest proposed
owner-approved configuration change is a nonce-scoped native systemd transient
service on the GitHub Ubuntu24.04 VM: User=runner, Delegate=memory pids, bounded
runtime/cleanup, and the same environment-cleared reviewed unprivileged launcher.
A reviewed bootstrap would place the launcher in its own supervisor leaf before
enabling child-domain controllers. Verify genuine native PostgreSQL16 and actual
owned-child control/enrollment in that same service. Repeat current-generation
containment before every eventual release; a separate root probe grants nothing.
No root application execution, new privilege broker, sysctl change, ad hoc
chmod/chown, privileged Docker or containment weakening is proposed or enabled.
This changes the existing no-sudo contract and needs owner approval before use.

Public documentation observed2026-10-26 (mutable upstream, hashes bind these
observations rather than a runner image):

- github/docs `content/actions/reference/runners/github-hosted-runners.md`:
  SHA256 `2ba4307f64e55251e63555e97a51381e4eb36334693a464c743522a09546a148`.
- actions/runner-images `images/ubuntu/Ubuntu2404-Readme.md`:
  SHA256 `01917c6c12c3efc53f2b803f5b25268bc4477aedb38705742123b041bba0a409`.
- systemd/systemd `docs/CGROUP_DELEGATION.md`:
  SHA256 `7a542a153328ea1af0e7fa15a1ac1e908d9b9484f4d39a804ffc18df18f0a50f`.

## November rollover and mapped-offer boundary

The bounded read-only audit verified the canonical first-of-month contract
(spec H4 and section11), existing transactional VERIFIED FUTURE promotion
(`price_book.py`), legacy rollover refusal (`pricing.py`), HTTP503 endpoint,
and migrations011/014/016. No production pricing/business source changed.

A production CURRENT transaction cannot safely reuse migration016: it explicitly
pins the synthetic fixture, database, named authority and clocks. Existing
promotion completeness is currently-active eligible offers per vendor; it is not
an owner-approved whole-production monthly vendor/offer universe. Production
mapped-offer activation/cutover remains separately approval-gated by the canonical
spec, rules and SQL. No capability flag was changed.

Specific proposed production contract, requiring owner authority and review:

1. Publish an immutable version for the exact monthly vendor/offer scope and
   omission/retirement policy; establish approved CURRENT baseline provenance.
   Fail closed on missing or conflicting scope. No implicit partial rollover.
2. Derive effective date from trusted aware server time in America/New_York;
   require local day1 and the exact effective month. Include midnight and DST
   regressions; caller/browser dates and synthetic fixture clocks confer no
   production authority.
3. Lock the exact approved Variant IDs, offer identities, supplier SKUs, pack
   conversions, validity dates, supplier source hashes, validation and named
   promotion provenance. Initially refuse all SKU/pack identity transitions;
   any supported permanent transition needs its separate explicit authority.
4. Bind an independently verified prechange backup to the exact database,
   source, predecessor CURRENT/FUTURE fingerprints and controls. A boolean or
   caller-provided digest is not backup authority.
5. Within one serializable audited transaction, require complete VERIFIED
   FUTURE, remove only the prior operational CURRENT, promote exactly that
   verified set, assert FUTURE empty and unchanged identity/economics, append
   one immutable versioned rollover event, and commit only if all checks pass.
   Retry must return the exact prior event or refuse changed inputs. Faults
   must roll back the entire transaction; prove restore separately.
6. Preserve finalized-run economics and audit identities/counts without creating
   a reusable normalized historical price archive. Separate audited offer
   activation and recommendation cutover require their own SQL-enforced events
   and approval; never enable the synthetic resolver in production.

The missing authority/evidence is exact November scope and approved supplier
prices/packs/validity, baseline adoption, any explicit SKU transitions, and the
production backup/operator binding. None is inferred from fixtures. Current
catalog, inventory, complete prior-day sales and reconciled incoming also remain
unestablished by verified current evidence; no private/provider data was queried.
November1 precedes the November9 objective; commercial readiness is not proven.

## Validation and review

Prechange hosted baseline:151/151. Current focused hosted group:199/199,
related legacy-containment/runner/floor contracts:88/88, startup:10/10.
Discovery/floor increased from1678 in94 modules to1726 in96 modules; no floor
was lowered. The new cgroup suite uses explicit filesystem/kernel simulations;
these are not live cgroup proof. Scoped internal Codex reviews found and drove
repairs for copied ownership, OOM cleanup, real empty-controller file format,
cancellation/mount checks, temporary descriptor cleanup, atime and bounded
version-output handling. These are internal reviews, not Claude/Cursor.

The unchanged full native suite was deliberately not rerun to reproduce its
known pg_dump refusal. Preserve the historical exact material df147b2 result:
1678 discovered=1667 executed+11 unexecuted;1667 executed=1665 passed+one failed
method+one errored method. Two error events include the transfer class-setup
error. Container50/50, startup10/10 and focused results are separate evidence;
never add them to synthesize a green full-suite total.

Independent Claude review remains NOT_ASSESSED. The review package must bind
this final published material, the complete diff from b404, focused raw logs,
source manifest, observed hosted job and both known limitations. Reviewers are
read-only. Cursor is warranted for the eventual real production rollover SQL,
identity and pricing contract, not a substitute for current broad review.
No callable external reviewer is available in this environment.

Hosted browser confirmation/BackupV2 binding/APPLY/review-edit-reject/two vendor
DRAFTs/three lines/14 packet members/$289/restart/restore/resource acceptance
remain NOT_RUN. The historical container fixtures do not establish these gates.
Railway remains NOT_PROVEN. No deployment, production database, private-data,
Shopify, real purchasing, PO release, supplier transmission, main/default branch,
PR24, Mac or Replit operation occurred. Dependencies/lockfiles are unchanged.
