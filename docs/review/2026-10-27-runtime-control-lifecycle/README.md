# October 29 B2 capacity correction — combined Claude delta

Owner approved the PG_PHASE-only128MiB option on2026-10-29. The implementation
revises the fixed policy to `BUFFALO_HOSTED_POSTGRES_PHASE_RUNTIME_V2` under
`BUFFALO_B2_PG_PHASE_CAPACITY_2026_10_29`; historical V1 manifests/evidence are not
reinterpreted. `b2-budget-change-control.json` records the exact decision and
unchanged limits. New candidate identities and source-bound target measurement
will be recorded in the evidence follow-up after publication.

**REQUEST CHANGES remains in force. Privileged activation is DISABLED;
credentials are WITHHELD; integrated acceptance is NON-GREEN.** The owner budget
choice is resolved; technical B2 closure and all pending repairs require one
combined independent delta review. No Claude tool or approval is claimed.

The necessary delta since reviewed d863 includes B1/N7/N8/N10, V2 producer and
receiver capacity enforcement, canonical sealed policy binding, unchanged per-file
and other-policy bounds, and bounded codec/capture lifetimes. Codec wire bytes and
hashes are unchanged. The owning capture closes its two input memfds before sealing
the union. Tests cover exact scaled aggregate boundary, over-capacity, other-policy
isolation, per-file and entry limits, relabeling, malformed capacity identity,
integer overflow, receiver agreement, failed capture and cleanup.

A fixed read-only workflow step now measures actual Ubuntu24.04 selectedPython/
trustedPG16 capture, composition, serialization and adoption before the unconditional
review gate. It does not execute PostgreSQL, namespaces, accounts or services.
Process/heap/owned-memfd observations and shared-job counters are separately scoped;
capacity or component memory fit is not whole-service or20s namespace proof.
An exact-source freeze, target observation and combined review remain required.

The October28 remediation details/results below are historical. Their B2 decision
blocker is superseded by the owner approval and V2 implementation above; their
source-attributed failures and other pending findings remain preserved.

## Received external review and preserved scope

`external-review-d863bc7d.json` records the actual owner-supplied review of
`d863bc7d52cdcb34bbe0a532ddd0235cfa40e3ea`, tree
`31336c2aa583e6f68b3589c4dfe54578b3b956f3`, with evidence checkpoint
`03035fff71d63adab5b36e6f775db3932edae1e7` / tree
`44177adcde660cc0b8d194df7b0934eb109a952a`.

Reviewer attribution: Claude Code container 2.1.312, session
`session_01JyrJEUAi36GJ6AfNcmBBxV`; reviewer-reported get_session configured and
served model `claude-opus-5-5`, effort high. Codex did not inspect that account.
Claude retained six historical closures with no regression at d863; N2 and N3
were accepted and N4 closed there. Runtime, worker-control exclusion and external
cleanup stayed OPEN. New blocking findings were B1 and B2; N7–N10 are separately
tracked in `findings.json`. Later changes do not inherit those approvals.

Claude reported 977 unprivileged tests: 966 passed and 11 B1 errors; real PID1
11/11 and runtime component 3/3 passed in its separate environment. PG capture
was read-only measurement, not execution. Claude did not reproduce systemd,
sudo/account tools, real cgroup v2, GitHub execution, the full/startup/related
suites or browser acceptance. Its Node 22.22.0 differed from the writer's runtime.
The original review of 3c1ae555 and six closures at 5e62c8e3 remain historical.

## October28 remediation and decision history

- **B1 / N1 — REPAIRED_PENDING_REVIEW.** Fixed, inventory-verified native stat
  reads PID1's IPC/mount identities before and after the scan. Failures become
  fixed refusal codes; tests are hermetic. Actual GitHub runner UID1001/PID1 UID0
  and direct EACCES13 were observed. The new privileged stat path has NOT_RUN.
- **B2 — OPEN.** Claude measured 105,340,350 composed bytes against 83,886,080.
  Exact capture-fit measurement now precedes account reservation, binds the
  selected interpreter, closes both captures and refuses capacity without a
  quarantined account. That does not make the bundle fit. The real Ubuntu24.04/
  native PG16 capture-fit driver is implemented but has NOT_RUN in this task.
  `b2-budget-change-control.json` proposes a PG_PHASE-only 128 MiB cap; it is
  NOT_APPLIED. Other 80 MiB bounds, 3,000 entries, 256 MiB/2 child processes,
  512 MiB/64 aggregate tasks and 20-second deadline stay unchanged. Neither
  capacity fit nor process RSS proves aggregate memory fit. No ICU dependency
  is omitted and no accepted runtime input is repinned.
- **N7 — REPAIRED_PENDING_REVIEW.** A separate pre-ACK exit drain requires canonical
  source-bound terminal transport, exact service/path/process absence, retained
  actor termination where available, fresh zero-OOM aggregate counters and closed
  descriptors. Missing generation remains missing; operation remains FAILED.
  Unknown transport or partial binding stays quarantined. Later account cleanup
  uncertainty cannot be hidden by an external drain proof. Live cases have NOT_RUN.
- **N8 — REPAIRED_PENDING_REVIEW.** Actual target inventory confirmed vfat EFI and
  stacked autofs/binfmt. The repair permits only the fixed EFI path and explicitly
  scans the fixed active binfmt root. O_PATH, mount ID, filesystem magic, held/named
  identity, unchanged mountinfo and cleanup checks guard that access. Inactive or
  other automounts refuse. A GitHub read-only component observed and closed the
  active binfmt handle; the privileged ownership scan has NOT_RUN. Hidden autofs
  is not claimed scanned; trusted administration must not overmount/lazy-unmount
  during the scan. This is not atomic pathname binding.
- **N9 — OPEN measurement risk.** The full native scan retains its 60-second bound.
  No target scan has run, so timeout is not a demonstrated kernel/platform failure.
- **N10 — REPAIRED_PENDING_REVIEW.** Only typed, definite pre-reservation ownership
  collisions retry, at most three candidates under one shared scan/reservation
  deadline. Incomplete coverage, timeout or cleanup uncertainty cannot retry.

The preceding d9c2670 delta preserves bounded source-bound failure evidence and
rejects other-source fallback. The workflow now exports only bounded test counts,
test/type/source-line failure identities and sanitized mount inventory. It excludes
exception messages, locals and subtest parameter values. The hosted-only test error
at edf8d12 was a hardcoded interpreter path; 3cbb2d3 fixes that test while retaining
the actual production interpreter identity check and negative regression.

## October28 source-bound validation history

At 3cbb2d3, GitHub run40699325717 executed1,033/1,033 diagnostics with zero
failures/errors/skips. The exact workflow command separately passed1,033/1,033 in
Cloud; startup10/10 passed there. Discovery2,597/131 modules is discovery only.
The GitHub review gate intentionally exited78; real PID1 was skipped. This was
not a privileged proof, full authoritative suite or browser acceptance.

At preceding de34685, normal and scrubbed diagnostic runs each passed1,033; price17,
snapshot20, related88 and startup10 also passed. These are separately attributed
results, not tests rerun at the final candidate. The original checkout's pinned
runner mode remains0600; related validation used a disposable exact-source0644
clone and removed it without changing existing checkout permissions.

The10af37d hosted focused step failed with unavailable detailed counts. Theedf8d12
run executed1,033:1,032 passed/one test error. Both failures remain indexed in the
packet. Full supported final-candidate validation is still required after the
blocking implementation/review prerequisites are met.

## Exact activation and review boundary (unchanged)

Review only the material delta since the actual d863 review, including d9c2670
and these repairs, plus the approved V2 B2 change and shared codec/lifetime delta. Do not repeat the
unchanged architecture review. Recheck impact on the six historical closures,
pre-account ordering, native stat/find authority, collision retry, pre-ACK cleanup,
N8 coverage, failure evidence and exact activation wiring.

```bash
git fetch --no-tags origin refs/heads/codex/hosted-synthetic-acceptance
git rev-parse 3cbb2d34ed07c182336cfdab4dcbd41fdf4ab06c 3cbb2d34ed07c182336cfdab4dcbd41fdf4ab06c^{tree}
git diff d863bc7d52cdcb34bbe0a532ddd0235cfa40e3ea 3cbb2d34ed07c182336cfdab4dcbd41fdf4ab06c -- .github/workflows/hosted-synthetic-acceptance.yml procurement/tools procurement/tests
```

The bootstrap CLI still unconditionally exits78. The workflow cannot reach live
service or PID1 execution. Its obsolete user-service body is unreachable and is
not the proposed PG activation. Dormant
`hosted_synthetic_identity.postgres_version_activation_entry([repository,"push",ref,commit])`
selects the dedicated identity, preflights, and runs only
`--native-bootstrap-postgres-version` through the reviewed native privilege-drop
chain. `run_lifecycle_case(...,diagnostic="postgres-version")` retains the six fixed
lifecycle cases. Exact published activation wiring needs independent review before
use; no caller-supplied PASS flag or old conditional approval enables it.

After B2 and applicable review gates close, the bounded credential-free proof is
already owner-authorized; no second routine approval is needed. It must bind the
actual generation's identity, enrollment, fixed FD execution, resources and cleanup.
This remains separate from worker adversarial proof, full runtime closure,
sentinel/release and browser/Backup V2/APPLY/DRAFT/packet/restart/restore acceptance.
The canonical two DRAFTs, three lines, 14 members and $282+$7=$289 have NOT_RUN here.
Railway, current business data, price activation/cutover and purchasing authority
remain separate and unproven. No deployment or production action is authorized.

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
