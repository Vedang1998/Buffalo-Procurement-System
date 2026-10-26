# Supported empty-groups bootstrap — WIP, NON-GREEN

## Authority and source

Owner approval authorizes the minimum native bootstrap on the existing ephemeral
GitHub-hosted Ubuntu VM, at risk Level 3. Codex remains the sole implementation
writer. Independent Claude review of this new delta must precede privileged live
execution. The earlier frozen-candidate review does not cover this delta.

Review base: `010aef5baf02f1c9bc038583f59cb314d6e8ee97`, tree
`42228c6217d1d51f1bb15da231d508cac0399703`. The implementation preserves that
published lineage. The evidence follow-up records the exact tested material and
ordinary branch-only publication. No history rewrite or email investigation is
needed; the previously verified account-linked noreply identity is unchanged.

## Implemented, with privileged invocation disabled

`hosted_synthetic_bootstrap.py` constructs a fixed native argv, validates the
source-bound plan, inspects root-owned native binaries and already enabled
ancestor controllers, bounds the native client, and collects non-authorizing
observations. Its CLI always exits 78 with
`DISABLED_PENDING_INDEPENDENT_REVIEW`. No argument, ambient variable or supplied
review-PASS file activates it. The workflow calls this gate before any service
creation. `run_native_plan` is dormant until actual independent findings and a
reviewed activation change are recorded.

The proposed system-manager oneshot unit delegates only its nonce-owned memory/
pids cgroup to the verified nonroot UID/GID. The `ExecStartEx` credential-only `!`
prefix permits the native transition while retaining systemd's NNP, resource and
capability restrictions. `:` disables environment expansion. The initial native
bounding set contains only SETUID, SETGID and SETPCAP, with no ambient capability.
There is no root shell, root repository Python, application, browser or fixture.

Two maintained `setpriv` exec stages are deliberate. Upstream util-linux changes
IDs before processing groups within one invocation: the first stage clears groups
while permitted, and the second changes UID/GID and drops inheritable, ambient and
bounding sets. Existing native effective/permitted bootstrap privileges disappear
at the subsequent nonroot exec. Explicit `env -i` precedes isolated stdlib Python.
Systemd provides stdio; subprocess boundaries use controlled environment, stdin
and closed unrelated descriptors. The existing clean-supervisor assertion remains.

`hosted_synthetic_launcher.py` reads its actual four UID/GID fields, empty Groups,
NNP=1 and all five capability sets=0, and binds its original user namespace and
exact unit/supervisor membership before dispatching a child. The capability child
repeats this readback before Git/source binding. It then verifies post-drop access
to the workspace and owned delegated controls without writing them, plus the
unchanged outer 512 MiB/64-task limits. Failed inherited groups, IDs, capabilities,
access or cleanup prevent controller writes and namespace work. Native PostgreSQL
trust, source/dependency binding, namespace, stopped enrollment, sentinel and
release assertions remain in force.

`prepare_user_service` supports an explicit system-manager scope without relaxing
the default user-manager scope or permitting arbitrary ancestor paths. Both
`/sys/fs/cgroup` and `system.slice` must already enable memory/pids; otherwise the
native plan refuses. It never enables ancestor controllers or changes global
groups, sudoers, kernel policy, permissions or unrelated services.

The service has TimeoutStartSec=120s, TimeoutStopSec=5s and KillMode=control-group;
the client has a native 150s timeout plus kill/reap bounds. A oneshot first-command
failure skips the fixed trailing unprivileged `true`. The source collector limits
stdout/stderr and treats incomplete lifecycle or ancestor/binary drift as cleanup
failure. Read-only unit absence is an observation, not exclusive access, proof
against later creation, or a successful cancellation result. Actual signaling of
a privileged sudo monitor and manager cleanup remain explicit live-review risks.

## Machine validation and limits

Pre-freeze hosted tests: 272/272 pass. New tests cover native serialization and
ordering, isolated CLI refusal, retained authority, malformed readback, causal
pre-Git/pre-write failures, explicit manager scopes, post-drop access, bounded
output, cleanup precedence and strict source/report decoding. Ordinary disposable
Python children test transport/reaping. A real deferred SIGINT is combined with
simulated native/lifecycle operations; it does not establish privileged cleanup.

Discovery is 1,799 tests in 99 registered modules, with floor 1,799 and no module
registration/minimum errors. This is discovery, not execution. Frozen material
test logs and source/dependency hashes follow in the review package. The complete
authoritative suite on the supported candidate remains required after the live
prerequisites; the known unchanged native-Cloud pg_dump refusal was not rerun.
Historical failures and separately scoped container 50/50 remain attributed to
their original executed commits. No test counts are summed across launches to
claim integrated acceptance.

Upstream systemd v255, util-linux v2.39.3 and Linux v6.8 source research supports
the command construction. It is not attestation of installed runner binaries.
All five host capability sets must read zero. Entering a new user namespace has
its own kernel capability semantics; no host SYS_ADMIN/SYS_PTRACE bit is retained.
The earlier speculative host bounding-set exception was withdrawn, not deployed.

## Gates and exact continuation

- Independent Claude review of this delta: NOT_ASSESSED. Internal Codex static
  reviews are separately attributed and cannot satisfy it.
- Privileged bootstrap, same-launch identity/access, native cleanup, stopped
  enrollment and cgroup/resource/OOM proof: NOT_RUN for this candidate.
- Exclusive cgroup access: UNPROVEN; hostile same-UID pathname races remain a
  limitation. Runtime closure: UNSEALED. No new execution authority is granted.
- Sentinel: NOT_RUN. Credential release: WITHHELD; provider/payload execution is
  prohibited by the active review gate.
- Browser confirmation, genuine Backup V2/binding, APPLY, review/edit/reject,
  vendor DRAFTs, packet reconciliation, restart/restore/resource acceptance:
  NOT_RUN. Controls remain two DRAFTs, three lines, 14 packet members,
  $282 merchandise + $7 fee = $289, derived from the workflow rather than seeded.
- Railway compatibility: NOT_PROVEN. November rollover/mapped-offer activation
  and real purchasing remain unauthorized and unready; current approved catalog,
  inventory, sales, incoming and commercial terms are still missing evidence.

Next: actual read-only Claude review of the published bootstrap delta and raw
evidence, recording reviewer/tool/version, exact commit/tree and ranked findings.
Codex repairs findings and reruns applicable tests. Only then activate the reviewed
native path and perform one bounded credential-free proof on the same ephemeral
GitHub VM, including cancellation/partial failure and cleanup. No second owner
approval is needed merely because the bootstrap passes. Continue retained sentinel
and release-boundary work, then the canonical browser scenario and full suite as
their prerequisites pass. Do not infer Railway or production authority.

No application business source, dependencies or lockfiles changed. No official
phase milestone changed. No production, Shopify, private-data, purchasing,
deployment, main/PR24, owner-Mac or Replit operation occurred. Existing unknown
volumes remain untouched. A saved Start draft remains distinct from a published
snapshot and a successfully tested fresh-task restoration.
