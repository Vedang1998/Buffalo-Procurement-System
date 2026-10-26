# Combined Claude re-review — REQUEST CHANGES remains

This is one scoped read-only re-review package, not a new architecture review.
Codex remains the sole writer. Claude has not been invoked from this environment;
there is no available Claude execution channel here. Internal Codex reviews are
not independent external review. Privileged activation remains disabled and
credential release withheld.

## Exact candidate and attribution

Repository: `Vedang1998/Buffalo-Procurement-System`  
Branch: `codex/hosted-synthetic-acceptance`  
Published implementation: `5e62c8e3a7030e60f77108517f5f9e06c7fc06a3`  
Implementation tree: `6271745239da49a24e3664ed2623977a7d21de07`  
Remediation base: `b85218198cd421fc41ba985465d1dae847334bb3`  
Base tree: `d7c14cccae0e64d3d4e3e70458714e21b5794bc2`

The owner's actual external Claude review is recorded in `external-review.json`:
reviewed commit `3c1ae555b82aa650011254a9b1f8e8c4367601bb`, tree
`3de6508ec0b173b575b7ce4cacc73129af9011a7`, verdict REQUEST CHANGES. The reported
238-test execution is not GitHub environment reproduction. Its original text,
reviewer-reported tool version/model/session and limitations have not arrived;
those fields remain null. No review coverage is attributed to b35c036d or this
new candidate. Reconcile the original numbered findings when that text is supplied.

The machine-readable `findings.json` maps the owner's P1 and eight P2 topics to
code and causal tests. Topic IDs are explicitly not invented Claude finding numbers.
Six topics are REPAIRED_PENDING_REVIEW; three remain OPEN; zero are VERIFIED_CLOSED
or EXPLICITLY_DEFERRED. The three OPEN topics are full-service external cleanup,
runtime closure and worker exclusion from delegated control. A pending repair is
not independent closure, and a diagnostic PASS cannot authorize credential release.

Read repository AGENTS/governance, handoff, canonical authority/rules and the
existing hosted plan, then `docs/CLAUDE_REMEDIATION_2026-10-26.md`. Inspect the actual
combined delta from the externally reviewed material, including the previously
unreviewed bootstrap, rather than reviewing only this writer's summary:

```bash
git diff 3c1ae555b82aa650011254a9b1f8e8c4367601bb 5e62c8e3a7030e60f77108517f5f9e06c7fc06a3 -- .github/workflows/hosted-synthetic-acceptance.yml procurement/tools procurement/tests
git diff b85218198cd421fc41ba985465d1dae847334bb3 5e62c8e3a7030e60f77108517f5f9e06c7fc06a3 --stat
```

Review in a separate environment without changing the writer checkout. Do not
invoke dormant provisioning functions, sudo, services, credentials, browsers,
production/provider/purchasing actions or workflow reruns. No argument, environment
flag or caller-authored approval file enables the CLI. The complete dormant
`activation_entry → run_provisioned_candidate → run_native_plan` wiring is included
for review; connecting it remains a reviewed source change, not a runtime toggle.

## Source-bound execution evidence

`manifest.json` records exact lengths/hashes. Frozen raw logs retain their bytes;
only their extension changes to `.txt`. `remediation-frozen-validation.json` binds
clean exact HEAD/tree and changed-file hashes before/after execution. Tests were
executed at the implementation commit above, not at its later documentation-only
evidence descendant. `remediation-material-publication.json` records ordinary
branch-only publication, immediate remote-base check and fresh commit/tree fetch.
Main, PR24 and recovery refs remained unchanged. Secret/private-metadata scans
found no hits. No history was rewritten.

- Hosted focused: **301 discovered/executed/passed**, zero failures/errors/skips/
  expected failures/unexpected successes. Related checks: **88/88** with the same
  zero-event conditions. Startup: **10/10**. Dependency/application imports: **18/18**.
- Real CLONE_NEWPID: **11/11 cases PASS** on this Cloud instance. Actual PID1,
  retained pidfds, ancestor-origin stop, kernel stopped state, production
  `cgroup_required=True` init/enrollment helpers and natural bounded ACK refusal
  execute. Cgroup writes/path observations are explicitly simulated. No private
  proc, runtime projection, payload, provider or credential-bearing work executes.
  Immediate test SIGINT is not production deferred-cancellation/manager-kill proof.
- Cases: success; omitted init stop; omitted guardian stop; missing ACK; wrong ACK;
  wrong generation; guardian death before/during enrollment; actual SIGINT; init
  exit before/during enrollment. Each injected fault must be reached. Assertions,
  outer timeouts and cleanup escalation cannot masquerade as expected refusal.
- CLI activation: **exit 78 as designed**, before any privilege/service call.
- Discovery only: **1,828 tests / 101 modules**, floor 1,828, no registration/minimum
  errors. This preserves the old population and adds 29 tests. It is not full-suite
  execution. Complete authoritative validation on the supported final candidate
  remains required. The unchanged native-Cloud pg_dump trust failure was not rerun.
- Application business source, database schema/migrations, dependency manifests and
  lockfiles are unchanged. Five dependency files and 17 source-contract files have
  exact identities in the adjacent records; a hash is not a runtime seal.

Exact GitHub run `40403625770` / job `121855637415` passed the focused step whose
frozen workflow requires 301 successful executions. Its real PID1 step exited 78,
classified NOT_RUN; the later review-gate step was SKIPPED. Overall FAILURE.
The public report digest exactly matches source-derived reconstruction of the
credential-free unshare capability probe returning 1. That reconstruction is
identified separately from direct stdout (which requires sign-in and was not read).
No errno or kernel-policy cause was observed. See `remediation-hosted-observation.json`.
No unchanged retry or restriction bypass was attempted. Do not combine Cloud PID1
evidence, simulated cgroup tests, historical container fixtures or different
launches into a same-generation containment PASS.

Historical failed 1,668 discovered / 1,637 executed / 1,632 passed / one failure /
four method errors / three class-setup errors / 31 unexecuted remains in the
existing reports. The three setup errors are events outside the executed-method
count. The later df147b2 native failure and container 50/50 component results keep
their original source attribution. No historical failure is relabeled passing.

Development-attempt logs are preserved separately: `remediation-first-focused.txt`
contains the initial 202-test run with two failures/seven errors;
`remediation-pid1-debug.txt` records the initial proc-children observation issue;
`remediation-final-causal.txt` records an accidental default Python 3.12 import
refusal before tests. The corrected native Python 3.13 causal run passed 47 tests,
and the identity/bootstrap causal run passed 45. These were unfrozen development
attempts, not extra final-candidate executions. Final evidence is the frozen run.

## Recheck the concrete findings and new authority

1. P1: I blocks after ARMED; ancestor G sends SIGSTOP using the retained pidfd and
   observes CLD_STOPPED/SIGSTOP before enrollment. ACK must precede resume/PROBE.
   Independently challenge all eleven causal negatives and cleanup accounting.
2. Entry authority: exact four UID/GID fields, empty groups, five zero capability
   sets and NNP before host-supervisor effects. Distinguish administrative operator,
   mapped U1 mount-coordinator authority and eventual sealed payload authority.
3. Numeric migration: no atomic-write claim. Audit unreaped-child ownership, S
   subreaper/single-thread/default SIGCHLD/no-SA_NOCLDWAIT requirements, G stop,
   pidfd/start/NSpid/namespace readbacks and no competing reaper before ACK. S's
   namespace readback may refuse nondumpable descendants; real migration is unproven.
   CLONE_INTO_CGROUP was assessed, not silently emulated by unsafe ctypes clone3.
4. Cleanup: failed signal precondition must still close every owned descriptor and
   record terminal failure while preserving the original cause and preventing path
   mutation. Full-service cancellation/timeout/manager-kill proof remains OPEN;
   LoadState=not-found alone never closes it. Ambiguous launch/cleanup quarantines
   the identity and prohibits UID recycling.
5. Memory: child charges do not include all pre-migration allocations. Inspect
   separate child and enclosing observations, post-removal sampling, unchanged
   256 MiB child / 512 MiB enclosing / 64-task diagnostic budgets and zero-OOM
   requirements. Whole-service lifetime is explicitly unobserved.
6. Runtime: UNSEALED. Executable/loader hashes, mutable checkout paths and old Nix
   pins are not hosted interpreter/library closure. Missing immutable execution
   closure and worker-control exclusion are implementation gaps, not evidence-only
   omissions; they continue to block release.
7. memory.events: exact six-key V1 fail-closed compatibility policy, not a claimed
   kernel defect. Unknown events refuse; duplicates, absent keys, malformed numbers,
   overflow, decreasing counters and OOM are not ignored.
8. Dedicated account/bootstrap: explicit native argv, one collision-checked per-run
   non-login UID/GID, locked password and explicit sudo denial, native prefix trust
   before account mutation, two-stage clear-groups/ID/capability drop, scrubbed
   environment/FDs, exact staged source and final same-process identity/access
   readback. No root repository Python, recursive chown, sudoers/group-policy change,
   ancestor cgroup mutation or unrelated service/account cleanup is permitted.
   Inspect NSS/local/subordinate/dormant-GID collisions, rootless source staging,
   partial setup failure, lost acknowledgments and account-last cleanup. Native
   account effects are mocked in current tests; installed behavior is NOT_RUN.

Return the actual tool/model/session, exact inspected source, commands/results and
limitations; map each original finding to the ledger and distinguish fixes from
deferred/open gates. State separately whether the complete dormant provisioning
and activation delta is ready for one bounded credential-free live proof, and
whether any credential-bearing execution is authorized. The latter is currently
blocked. Do not extend the old review's approval to new code or close findings
solely on this writer's assertions.

After actual independent disposition and activation review, the next live action
is one same-generation credential-free dedicated-host bootstrap/enrollment proof
with cancellation/kill-path/partial-setup cleanup and aggregate resource evidence.
Runtime, worker exclusion, sentinel and release checks must then pass before the
browser confirmation → Backup V2 → APPLY → review/edit/reject → vendor DRAFT →
packet → restart/restore sequence. Controls remain two DRAFTs, three lines,
14 packet members and $282 merchandise + $7 fee = $289, derived from workflow.
No integrated acceptance, Railway readiness or real purchasing readiness is claimed.
