# October 26 critical-path checkpoint — WIP, NON-GREEN

Material is published and recoverable, but integrated acceptance is incomplete.
GitHub supplies native PostgreSQL16.15 and owned writable cgroup delegation in
the same unprivileged launcher. The measured remaining launch blocker is
**nonempty supplementary groups**. The empty-groups security assertion is intact.
The final identity gate stops before any controller write or namespace attempt.

Repository: Vedang1998/Buffalo-Procurement-System
Branch: codex/hosted-synthetic-acceptance
Base: b4044194137669f6203c85276af8f23d0e500cea
Base tree:273123a18b0245ba634109c6af2665b26ca368b9
Tested/published material:**3c1ae555b82aa650011254a9b1f8e8c4367601bb**
Material tree:**3de6508ec0b173b575b7ce4cacc73129af9011a7**

Ordinary acceptance-branch-only publication and fresh fetch verified. No history
rewrite, main/default-branch or PR24 change. Metadata recovery was already
published and was not repeated. The base's thirteen evidence hashes were verified.
Codex remained sole writer. Official phase numbering/status did not change.

The final documentation/evidence descendant has a separate tree. It preserves
all material source/workflow/test/dependency bytes. Its explicit [skip ci] commit
avoids rerunning the unchanged hosted refusal; it makes no new PASS claim.

## Implementation completed

- Owned cgroup2 component: real filesystem and held directory/control/mount
  identities; finite page-aligned memory below5GiB; pids.max=2; oom.group=1;
  exact membership and zero OOM deltas; stopped, childless single-thread init
  enrollment with G/I pidfd/start/lineage checks and G/S outside the limited child.
- Supported bounded systemd-run --user service: exact random app.slice unit,
  delegated memory/pids, separate supervisor leaf, 512MiB/64-task outer budget,
  NNP, literal arguments and scrubbed close_fds supervisor. Source binding
  precedes enabling controllers only in its owned empty unit root. No ancestor,
  permission, kernel-policy or sudo operation is introduced.
- Strict optional namespace/cgroup integration: mode is sealed into the guardian
  bootstrap. S creates the256MiB child lease before G fork. I sends ARMED then
  stops; G waits for I and stops too. S binds/enrolls I before ACK/resume. G checks
  I's generation cgroup path before I resume/PROBE. S independently checks retained
  I/W identities, NSpid, membership, limits and OOM counters. Reaping precedes
  empty lease removal. Explicit cgroup mode cannot fall back to namespace-only.
- Native PG metadata/version observer, initial boolean-only identity refusal and
  bounded canonical public evidence. Indexed base64 notices avoid GitHub's4096
  annotation truncation and bind the full report with SHA256.

Cleanup is ownership-bound and fails closed. Copied leases cannot clean originals;
OOM violations remain failures even if empty resources can be removed. Unknown
members/replacements are preserved. Linux mkdir/open/rmdir are not atomic
identity-conditional operations: eventual composition must establish exclusive
delegation access and exclude hostile same-UID writers. No stronger race guarantee
is claimed. Current host Python runtime remains unsealed.

The detached sentinel remains unexecuted. No fabricated release provider or
credential-bearing browser executes. Component evidence may say
COMPONENT_OBSERVATION_ONLY, never HOST_CAPABILITY=PASS.

## Source-bound host observations

| Material | Run/job | Observed result |
|---|---|---|
|829ea51e113ca1946d4600c976d1a7630c766be6|40363964335/121716508122|PG16.15 system metadata/version; current service lacks delegation; user manager present.|
|af6afca388f8020ea8ef5b079850d47bf425af52|40368541492/121732717740|Same user-service launcher has PG16.15 plus owned writable delegation; one controller write. Later mapped-identity assertion refuses; namespace cleanup true; unit absent observed.|
|eb736550fa8402ee26f7f5937ce882959b5a356f|40370106003/121738230784|Complete canonical boolean identity report: UID/GID consistent, supplementary groups not empty. Stops before controller/host/namespace work.|
|3c1ae555b82aa650011254a9b1f8e8c4367601bb|40372140134/121745375279|Focused238 succeeds; same identity refusal before mutations. Overall job FAILURE.|

[Final job](https://github.com/Vedang1998/Buffalo-Procurement-System/actions/runs/40372140134/job/121745375279).
Final canonical report:2373bytes, SHA256
`6d731daa925240f59b53dbe1c1f5e87520d3ac1a621fe59362b11c218b63bc2f`.
All13 reported source hashes match exact Git objects. Workflow6799bytes SHA256
`7e17ae2e6a311e76c01a4e49986a9f6900b109a3bd4572ef9e2cca470cb95239`.
Final provider/payload counts0, credentials WITHHELD, execution_authority=false.
Its owned unit was NOT_FOUND_OBSERVED with client status1; this manager observation
is not full namespace/resource acceptance.

At af6afca, `_guardian:722` checks mapped UID/GID/empty supplementary groups.
`os.unshare` is line719 and the USERNS/MAPPED handshake already completed. Do not
mislabel this as a namespace syscall refusal or kernel-policy ban. Its annotation
was truncated to4096bytes (exact prefix preserved, not fabricated complete JSON).
Later complete reports establish the initial supplementary-group limitation.

Native Cloud's previously measured lack of trusted root-owned PG tools/delegation
was not retested. The earlier container50/50 remains fixture-only evidence. The
hosted diagnostic no longer repeats known namespace failure on unsuitable identity.

## Supported launch change proposed for owner approval

Smallest proposed supported launch contract, requiring owner approval before
implementation of privileged provisioning: a platform-owned native service
bootstrap clears supplementary groups BEFORE dropping to the verified nonroot
runner UID/GID, removes ambient/inheritable capabilities and enables NNP, then
executes the same exact reviewed scrubbed supervisor inside its nonce-owned
delegated cgroup. No application or browser runs as root; native PostgreSQL16
checks, stopped enrollment, all namespace/identity assertions, limits and release
ordering remain unchanged. Re-prove each property in that same current generation.
An empty systemd SupplementaryGroups= setting alone is insufficient because
User='s configured groups may still be inherited. Do not remove NNP, add a Docker
root broker, change host kernel policy or attempt setgroups after mapping denial.
No privileged implementation or command has been activated. A platform with
documented equivalent initial credentials is also acceptable after review.

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

## Actual validation and acceptance gates

Exact3c1ae555 frozen results: **hosted238/238; related88/88; startup10/10**.
Source hashes, HEAD and clean worktree were unchanged across execution. Hosted
GitHub focused238 also passed. Local lightweight dependency/tool imports18/18 pass.
Discovery only is1765 tests in98 registered modules; no floor was lowered.

Seventeen new enrollment tests include real disposable G/I stops and cleanup with
explicitly simulated cgroup operations. Failures at enrollment, ACK, resume,
retained-pair proof and lease cleanup remain failures. Tests cover PID/start/path/
membership mismatch, malformed sealed mode, no-fallback and namespace-only behavior.
These tests do not establish live cgroup enforcement or integrated acceptance.
Pre-freeze failing test logs remain in the package with their original outcomes.

No unchanged full native rerun was made merely to reproduce pg_dump refusal.
Historical exact df147b2:1678 discovered=1667 executed+11 unexecuted;
1667 executed=1665 passed+one failed method+one method error. Two error events
include a separate transfer class-setup error. Original historical evidence remains
1668 discovered/1637 executed/1632 passed/one failed/four method errors/three
class-setup errors/31 unexecuted. Class errors are not additional executed methods.
Neither historical result is green. Container50/50 is separately attributed;
do not combine scoped runs into an invented authoritative-suite PASS.

| Gate | Result |
|---|---|
|Exact material/dependency identities|Verified; application business source, manifests and lockfiles unchanged from b404.|
|Native PG16 + same-launcher owned delegation|Observed at af6afca; not a BackupV2 invocation or complete runtime closure.|
|Current supported initial identity|FAIL: supplementary groups nonempty; no controller/namespace retry.|
|Wired stopped enrollment/live containment|Implemented and focused-tested; live proof NOT_RUN.|
|Sentinel execution/cleanup + one-shot release|NOT_RUN; provider0 and release WITHHELD.|
|Operator VALIDATED → browser confirmation → BackupV2 → APPLY|NOT_RUN.|
|Review/edit/reject → vendor DRAFT → reconciled packet|NOT_RUN; canonical two DRAFTs/three lines/14 members/$289 remain required, not preseeded.|
|Restart/restore/resource acceptance|NOT_RUN.|
|Independent Claude review|NOT_ASSESSED. Internal Codex reviews are internal.|
|Railway readiness|NOT_PROVEN; no deployment.|
|November/current real purchasing|Not ready; business authority and current approved inputs remain absent.|

## Review, continuity and exact next action

[Independent review package](review/2026-10-26-critical-path/README.md) freezes the
exact base/material/tree, diff scope, raw test logs, dependency/source hashes,
publication, canonical hosted evidence, cleanup and internal review attribution.
No actual Claude/Cursor review occurred. Cursor is reserved for targeted SQL,
identity/pricing risk when production contract implementation is authorized.

Next executable action is Claude's read-only adversarial review of that exact
material. In parallel, obtain the supported initial-empty-groups launch authority
specified above. Codex then implements the narrowly approved bootstrap, proves
initial identity/current-generation enrollment and cleanup, and continues retained
sentinel/release gates before the existing browser scenario. No kernel workaround,
assertion weakening or speculative production rollover is an authorized substitute.

No live app/database/browser, containers, temporary databases or projection paths
remain locally. Eight old PID1-owned zombies and six unattributed October17 volumes
remain untouched. No new credential or test resource enters the saved setup.
The reusable Install remains unchanged; Start continuity is updated separately as
a saved draft, not a published snapshot or verified fresh-task restoration.

No production access, Railway deployment, private-data transfer, Shopify activity,
real purchasing, PO release, supplier transmission, main/PR24 modification, Mac,
Replit SSH or Replit shutdown occurred. No automatic future resumption is promised.
