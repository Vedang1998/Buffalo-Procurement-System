# October 27 fixed PostgreSQL execution delta — read-only review

Candidate: this descendant of published `a05c0671f7a4e4a86a62550c4e91e8dd3cc56ff3`,
tree `6202c915e7a5a696699a0ac9f48046e8536ab376`. The following source-bound evidence
follow-up records its exact frozen commit/tree and executed validation. Do not
attribute the earlier d1b8124 results to this changed implementation.

**Activation DISABLED. Credentials WITHHELD. Integrated acceptance NON-GREEN.**
The bootstrap CLI still returns78 unconditionally. No sudo/account/systemd,
namespace prerequisite, sealed PostgreSQL, database, browser or credential-bearing
execution has run for this candidate. The fixed execution path is now implemented;
passing causal/component tests are not evidence that native execution occurred.

## Reconciled external review

The owner supplied the Claude re-review of
`5e62c8e3a7030e60f77108517f5f9e06c7fc06a3`, tree
`6271745239da49a24e3664ed2623977a7d21de07`: six findings VERIFIED_CLOSED **there**.
See `external-review-update.json` and `findings.json`. Numeric-PID closure covers
its reuse window, not atomic migration; memory closure covers disclosure and
separate accounting, not live lifetime proof. Runtime, worker-control exclusion
and external cleanup remain OPEN. Later change impact is independently tracked;
historical closure is retained and does not approve this descendant.

Owner attribution: Claude Code CLI2.1.310, session
`session_01JyrJEUAi36GJ6AfNcmBBxV`, reported claude-opus-5-5, effort medium.
Original REQUEST CHANGES remains attributed to3c1ae555. Claude's environment had
no real cgroup v2/systemd/GitHub-host execution or complete-suite reproduction.
Verbatim prose is not invented. No callable Claude tool exists in this task;
Codex read-only subagent inspections are internal, not external review.

## New material to review

- N1: before reservation/account mutation, verified native `find -P /` scans
  visible local mounts (including cgroup2, /dev/shm and /dev/mqueue), excluding
  proc process metadata covered separately. Nonzero exit, timeout, collision,
  mount drift, unsupported filesystem or unreadable SysV owner/creator rows
  refuses. No object is deleted or chowned. Visible ownership is the scope;
  hidden/unmounted objects, named ACL grants outside source and general keyring
  authority are not claimed absent. Worker keyring syscalls remain denied.
- N2: candidate UID/GID mode/ACL/ancestor/source checks, external Git object-layout
  refusal, real disposable unprivileged clone/detached-checkout rehearsal, and
  actual /run byte/inode/writable-mount/traversal checks precede reservation.
  Existing checkout permissions are unchanged. Native account commands retain15s;
  copy commands have60s bounds. Later drift remains a quarantine failure.
- N3: the owner trusts GitHub platform administration, verified installed native
  tools, and exact reviewed orchestration **only for this ephemeral credential-free
  diagnostic**. Arbitrary repository/dependency/application/browser code is not
  thereby trusted. No malicious-platform-admin isolation, production model change,
  sudoers/AppArmor/kernel-policy change or privileged broker is introduced.
- N4: unsupported comment about internal setpriv ordering removed. The existing
  two-stage clear-groups then IDs/capabilities chain remains unchanged.
- Fixed native path: sealed PG/Python phase projection/reexec → existing G/I
  stopped enrollment → I private-proc/root mount-ID rebinding → reduced U2 W →
  fixed image lease → ARMED/block → I pidfd SIGSTOP and real T readback → same
  generation S cgroup/whole-service observer ACK → exact role/start/image/source
  EXEC ACK → SIGCONT → `os.execve(held_fd,[fixed_postgres,"--version"],fixed_env)`.
  There is no database startup, arbitrary argv/path, provider or browser call.
- The exact native failure code survives I/G/S protocol hops. Missing/wrong ACK,
  drift, early exit, cancellation, excessive output and descriptor/cleanup failures
  refuse. I keeps child ownership until descendant checks and reap. G independently
  observes I/W mount/root/namespace/process identities. Both child and enclosing
  service counters retain their separate existing budgets.
- The namespace prerequisite performs one combined USER/MNT/PID/NET syscall in
  a bounded dedicated unprivileged child and preserves actual numeric errno.
  Parent and post-exec child policy context must match. Generic exit, malformed
  output, timeout and syscall refusal are different results; AppArmor attribution
  remains UNCONFIRMED without attributable denial evidence. No retry/fallback.

## Exact dormant activation wiring and expected effects

After actual review approves the exact published candidate, the existing
unprivileged operator may call
`hosted_synthetic_identity.postgres_version_activation_entry([repository,"push",ref,commit])`
on the ephemeral GitHub Ubuntu24.04 host. It selects the dedicated identity,
performs pre-account checks, provisions only the reviewed native account/staging
resources, and selects `--native-bootstrap-postgres-version`. The system-manager
chain drops privileges before any repository Python. The launcher verifies the
actual four-field IDs, empty groups, zero capability sets and NNP, then dispatches
the source-bound prerequisite and retained PG path. The current CLI/workflow does
not invoke this function. This exact activation delta needs review, not assumed
approval inherited from5e62c8e3.

`run_lifecycle_case(...,diagnostic="postgres-version")` forwards the existing six
fixed normal/cancel/manager-kill/timeout/lost-ACK/partial-setup cases to this path.
Those live cases have NOT_RUN. Quarantine is never reported as drained cleanup;
an ambiguous identity is not recycled. No unrelated objects/volumes are removed.

Budgets unchanged: child I+W pids2/256MiB; enclosing service/slice512MiB/64tasks;
namespace proof20s; service start120s/stop5s; native client150s. Sealed closure
80MiB/3000entries unchanged. No accepted Nix/browser input is repinned. Actual PG
closure fit, trusted native PG availability and non-piped core policy remain
unmeasured on the target. `--version` may finish before library maps can be sampled;
its FD-bound execution result must not be relabeled native-library lifetime,
full runtime closure, worker adversarial isolation or purchasing acceptance.

## Independent review request

Review the actual material delta since reviewed5e62c8e3, focusing on later
change-impact, N1–N4 and the complete fixed activation/execution/cleanup wiring.
Do not redo unchanged architecture or erase the six historical closures. Return
source-bound findings and an explicit disposition on readiness for one bounded
credential-free live proof. No approval of credentials or browser acceptance is
requested. Record reviewer/tool/model/session, commands, results and limitations.

The next evidence follow-up supplies exact `git diff 5e62c8e3 <candidate>` identities,
raw results and publication verification. No Claude invocation has been fabricated.

## Earlier component history (scope and historical evidence retained)

## Implemented components

1. **Sealed runtime execution.** `hosted_synthetic_runtime.py` connects the existing
   sealed memfd inventory to a private read-only root, detaches the old root and
   executes the projected interpreter/loader. Actual mapped ELF identity/hash
   checks follow execution. The original 23 locked wheel artifacts are verified
   before ZIP parsing, with bounded extraction, complete RECORD ownership and
   strictly closed native dependency resolution. Current application bytes come
   from bound Git tree objects, not a mutable HEAD reread. Root-trusted timezone
   inputs are bound. No Nix identity is fabricated and accepted inputs are unchanged.
   Cancellation and partial materialization close owned bundles. This proves a
   diagnostic/application-import component, not the full browser/PG lifecycle.
2. **Worker/control boundary.** `hosted_synthetic_worker.py` checks exact root,
   working directory and descriptor inventory; locks securebits, drops all
   capability sets, enables NNP, drops dumpability and installs the fixed syscall
   policy. Private projection removes host control paths/handles. Namespace
   integration attests the actual stopped worker before executing the exact static
   sentinel. The diagnostic's real mapped worker/delegated-control adversarial
   proof has not run. Dedicated UID alone and mutable hashes are never sufficient.
3. **External lifecycle/resource observation.** `hosted_synthetic_observer.py`
   holds exact owned cgroup/process identities outside the service and samples a
   nonce-owned accounting slice with the unchanged 512 MiB / 64-task aggregate
   limits. Child and aggregate counters remain separate. Native creation is bounded;
   repository Python never runs as root. Readiness/ACK binds this generation before
   cleanup. Drain requires terminal retained processes, exact path removal, final
   counters, no identity-owned processes and successful descriptor closure.
   `hosted_synthetic_quarantine.py` durably reserves ambiguous identities; a journal
   is not deletion authority. Unknown resources are untouched.
4. **Executable but dormant lifecycle cases.** `hosted_synthetic_lifecycle.py`
   wires normal exit, actual deferred operator SIGINT, bound manager SIGSTOP/KILL,
   lost ACK, ACK deadline expiry and partial account setup into the same native
   path. The timeout is the existing 20-second namespace deadline, not systemd's
   separate 120-second startup timeout. A successful negative assertion can still
   mean FAILED operation / QUARANTINED identity / UNVERIFIED resources. Terminal
   observer state is explicitly distinct from successful descriptor closure.
   Native commands remain inactive pending review; this is not live cleanup proof.
5. **Independent APPLY implementation.** `hosted_synthetic_apply_step.mjs` validates
   trusted expected intent and public verified-Backup-V2 bindings, confirmed batch,
   exact preview hash, unchanged CSRF/form, one-shot intercepted request, exact
   redirect and APPLIED_CURRENT result. Caller expectations must come from verified
   launcher evidence, never the DOM. The component has no login/DB/release authority.
   Its adapter is scripted in tests; no actual browser adapter or APPLY is claimed.

Internal read-only Codex investigators `app_runtime_inputs`, `cleanup_audit` and
`harness_audit` identified source ABA, cancellation ownership, descriptor-closure,
replayed-request and test-causality defects that the sole writer repaired. They did
not run the writer's tests or supply independent external approval.

The same descendant adds guarded mapping and owner-review/DRAFT components.
Mapping binds exact Variant/offer identity and deterministic decision UUIDv5;
review verifies actual SKU/pack conversions, CS2/BT18/BASE tiers, minima20/120,
shortfall0/18, distinct vendor PO UUIDs, persisted tiers, fees and captured stock.
Its eleven POSTs are scripted component execution, not actual DRAFT persistence.
The external observer now replaces its provisional drain aggregate with its last
validated pre-stop sample; stale or mismatched proof cannot release the identity.
These repairs are covered by the frozen 485 tests and remain pending external review.

## Source-bound results and retained failures

`manifest.json` records exact artifact sizes and SHA-256 hashes. Raw logs are copied
byte-for-byte, with `.log` renamed `.txt`. Frozen validation records bind clean
commit/tree and source-file hashes before/after execution. Publication records
bind ordinary branch-only pushes and fresh remote commit/tree verification. Main,
PR #24 and the Mac recovery branch remained unchanged. No history was rewritten.
The documentation descendant containing this packet has a separate tree and is
not presented as another execution of the material tests.

| Evidence | Source | Actual result / scope |
| --- | --- | --- |
| Hosted focused suite | 2bfd85fe | 485 executed, 485 passed; no failures/errors/skips/expected failures |
| Related regressions | 2bfd85fe | 88 executed, 88 passed; no failures/errors/skips/expected failures |
| Startup tests | 2bfd85fe | 10 executed, 10 passed |
| Dependency/application/tool imports | 2bfd85fe | 18 imports passed; no server/DB/browser started |
| Inert activation gate | 2bfd85fe | exit 78 as required, before native privilege |
| Authoritative discovery | 2bfd85fe | 2,012 tests / 111 modules; floor 2,012, zero registration errors; execution count 0 |
| Diagnostic kernel component | 4dd50dea | 3 cases passed in an unprivileged namespace component |
| Application kernel component | 4dd50dea | 3 cases passed; 81,844,207 regular bytes under 80 MiB, 51 mapped ELFs verified |
| New APPLY tests | 2bfd85fe | 15 scripted DOM/request tests, included in 485; not browser acceptance |
| Mapping / selection | 2bfd85fe | 20 scripted tests, included in 485; 25 guarded POSTs, exact seven decisions/five selections |
| Owner review / DRAFT | 2bfd85fe | 23 scripted tests, included in 485; actual API/gateway form contracts, exact vendor economics |
| Complete authoritative suite | final supported candidate | NOT_RUN; required after review/composition |
| Dedicated GitHub native lifecycle | current generation | NOT_RUN; review/activation gate closed |

The application kernel cases are actual sealed imports/native calls and the
required missing-timezone/private-libpq refusals, without host fallback. Private
network identity changes and only loopback is present. They do not start the app,
authenticate a browser, access a database, establish cgroup delegation, or exercise
the strict dedicated-identity/worker entry. Separate launches are not combined into
one containment/resource PASS. Earlier real CLONE_NEWPID 11-case evidence remains
at 5e62, with simulated cgroup writes; its supported-host counterpart remains open.

Historical 1,668 discovered / 1,637 executed / 1,632 passed / 1 failed method /
4 method errors / 3 class-setup errors / 31 unexecuted remains failed evidence.
Class-setup error events are outside the executed-method count. Later native
df147b2 failure, container 50/50 fixtures and prior GitHub run 40403625770 remain
separately attributed in the existing packet. The unchanged native-Cloud pg_dump
trust refusal and GitHub unshare refusal were not rerun.

Development failures are retained, not silently replaced: obsolete floor assertions;
kernel driver refusal of a linked-worktree `.git` layout and concurrent path change;
initial FIFO mock; an incorrect fixture test name; stale workflow expectation.
Later frozen/full-clone serial component records identify the repairs. Existing
Git auto-maintenance and a death fixture had detached children under non-reaping
platform PID1. Maintenance is disabled in disposable repositories; the test uses
an owned subreaper. The 167-test causal rerun created zero new zombies. Existing
1,393 unrelated PID1 zombies and six unattributed old volumes were left untouched.
They preclude claiming a freshly clean environment snapshot.

## Exact remaining gate and smallest contract decision

The implemented native path stays unreachable from CLI activation. Its bounded
credential-free proof can proceed only after the complete delta and activation
wiring receive actual independent review. Then execute the six fixed lifecycle
cases on the same authorized ephemeral GitHub VM, binding native PostgreSQL trust,
dedicated credentials, cgroup enrollment, workers, counters and cleanup to each
source/generation. A failed prerequisite remains refusal, not a reason to loosen
policy. Whole-service and child budgets must not be combined or raised.

Browser execution also has a demonstrated composition contradiction:

- The diagnostic child cgroup writes/verifies `pids.max=2` and exact I/W membership
  (`hosted_synthetic_cgroup.py`). The existing Python browser driver starts Node,
  whose normal execution needs threads, and expects a separately running Chromium.
- The worker's private proc/net, zero capabilities, NNP/nosuid and namespace syscall
  restrictions do not implement the old external-browser discovery, TLS/CDP path
  or Chromium sandbox identity. Disabling Chromium's sandbox is not an alternative.
- `audit_staging_purchasing_browser.py` binds Node 24.13.0 at its accepted Nix path
  and Chromium 152.0.7977.64 plus `/repl/ctls` dependencies. Cloud's Node 24.19.0 and
  Chromium 154.0.8037.57 are not those accepted artifacts. No repinning occurred.
- The fixed current worker request/result and inherited handle inventory are
  confirmation-only. Adding APPLY does not create a reviewed end-to-end adapter.

**Authority correction:** the approved October10 design section6 and plan Tasks4,
7 and8 already authorize a separate browser/PostgreSQL runtime/network/process
composition and its phase-specific resource limits. The diagnostic two-task child
and512MiB service do not prohibit implementing that composition. Earlier wording
requiring another owner design decision before any such coding was too broad.
The larger historical acceptance ceiling does not authorize raising the current
diagnostic512MiB/64-task envelope. Continue the authorized phase implementation
and retain its existing5GiB-minus-one-
page service limit, per-process peak below5GiB, service peak below6GiB and zero-OOM
criteria. No additional budget, privilege, accepted-input repin, host-proc exposure
or disabled browser sandbox is authorized. Review and machine-validation gates
still precede privileged activation and credential-bearing execution. Runtime
artifact availability and safe same-generation composition remain demonstrated
implementation/execution gaps, not reasons to stop independent coding.

The canonical operator VALIDATED → authenticated confirmation → stopped-service
Backup V2 → browser APPLY → selection/edit/reject → vendor DRAFTs → packets →
restart/restore remains NOT_RUN. Required output remains two DRAFTs, three lines,
14 packet members, $282 merchandise + $7 fee = $289, derived from execution.
Existing local DOM actions and V2 reconciliation validators can be reused after
composition; old token-file/no-sandbox paths and V1 totals cannot be imported.

Railway readiness and commercial readiness are NOT_PROVEN. No deployment, price
activation, mapped-offer cutover, provider call, production data access or purchasing
occurred. The existing business-data/authority blockers remain in
`docs/PURCHASING_CRITICAL_PATH_2026-10-26.md`; hosting evidence does not clear them.

**Next executable action:** in the separate read-only Claude session, run the exact
review commands above and return a combined disposition of the original findings
and this delta, including bounded native-proof readiness. In parallel resolve only
the concrete browser-phase contract above. Neither step requests another broad
architecture review. No automatic future-task resumption is claimed.
