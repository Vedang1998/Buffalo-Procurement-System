# Consolidated Claude remediation — REQUEST CHANGES remains

The owner reports an actual external Claude review of material
`3c1ae555b82aa650011254a9b1f8e8c4367601bb`, tree
`3de6508ec0b173b575b7ce4cacc73129af9011a7`, with verdict REQUEST CHANGES.
That review does not cover `b35c036d` or this remediation. Its reported238-test
execution is not a reproduction of GitHub's environment. The original review text,
reviewer-reported tool/model/session and execution limitations have not been
provided in this checkout/conversation; they were requested while implementation
continued. Unknown attribution fields remain null, not invented.

Implementation base: `b85218198cd421fc41ba985465d1dae847334bb3`, tree
`d7c14cccae0e64d3d4e3e70458714e21b5794bc2`. Published history is preserved. Exact
frozen material is `5e62c8e3a7030e60f77108517f5f9e06c7fc06a3`, tree
`6271745239da49a24e3664ed2623977a7d21de07`. The documentation-only evidence
follow-up carries raw logs and ordinary-push/fresh-fetch proof in the
[combined review package](review/2026-10-26-consolidated-remediation/README.md).
Frozen hosted301, related88, startup10 and imports18 pass. Real Cloud PID1 eleven
cases pass with simulated cgroup migration. Discovery1828/101 modules is not a
full-suite pass. GitHub run40403625770 passes focused301 but the separate realPID1
step exits78/NOT_RUN; overall FAILURE and review-gate step SKIPPED. No unchanged
retry or restriction bypass occurs. Official phase numbering/status, application business source and
dependency/lockfile identities remain unchanged.

## Repairs and their limits

PID1 now blocks after ARMED. G, in the ancestor PID namespace, sends SIGSTOP using
the retained init pidfd, observes CLD_STOPPED/SIGSTOP, then requests enrollment.
G stops before S migrates I; exact ACK is required before I resumes and PROBE is
sent. Generation, parent/death, identity and cleanup bindings remain.

An explicit credential-free driver creates real CLONE_NEWPID PID1 and exercises
the production init/enrollment helpers with cgroup_required=True. Its cgroup
migration/path readbacks are simulated; it does not establish live delegation.
Positive proof and missing init/G stop, missing/wrong/generation ACK, G death,
real immediate SIGINT, and early/during-enrollment init exit are exercised.
Each negative requires its fault to be reached, actual PID1 to be observed, and
no PROBE progress; ACK failures require natural bounded G refusal. Outer timeout
or a test assertion is a failure, never an expected successful refusal. This
driver's immediate SIGINT is distinct from production deferred cancellation.

All final host-supervisor entry paths now use the same full four-field UID/GID,
empty-groups, five zero-capability-set and NNP readback before Git, native PG,
delegation writes or namespace creation. The administrative operator remains a
separate role. Mapped U1 mount coordinators require exact mapped identities/NNP
and phase-appropriate U1 capabilities; they are not falsely described as host
zero-capability or sealed payload processes.

Numeric migration now requires stopped/single-threaded G, exact retained process
identities, NSpid and namespace identities immediately around migration. S must
remain a single-threaded subreaper with native SIGCHLD auto-reaping disabled. G
does not reap I between ENROLL_INIT and ACK; G death makes I an unreaped child of
S until the migration attempt ends. These are lifecycle preconditions, not an
atomic pidfd cgroup write. CLONE_INTO_CGROUP was assessed: Python os.fork lacks its
interface and raw ctypes clone3 would bypass CPython fork bookkeeping; no new
native helper or unsupported privilege workaround was introduced.

The strict S namespace readback can refuse access to nondumpable descendants.
Its supported-host availability is unproven; no ptrace capability was added and
no readback was replaced by an assertion. Actual migration remains gated. The
real PID1 regression uses a simulated lease and does not validate this production
namespace-readback path.

Cgroup close now attempts descriptor closure and records sticky terminal failure
even if its signal precondition fails. That failure prevents path mutation and
remains the first cause. Child and enclosing-service memory current/peak/events
are recorded separately, including final held-parent observation after child
removal. Migration does not move all earlier memory charges to the child.
The child256MiB and enclosing512MiB/64-task diagnostic budgets remain unchanged;
zero OOM and monotonic counters/peaks are required. Final whole-service lifetime,
cancellation and manager-kill accounting still need external live evidence.

memory.events retains an explicit exact six-key V1 policy. Unknown keys refuse
compatibility; they are not ignored, relabeled, or claimed to demonstrate a kernel
failure. Missing/duplicate/noncanonical/overflow/counter-decrease/OOM checks remain.

## Dedicated identity and dormant activation wiring

The new identity module defines and tests the complete dormant provision → stage
→ native launcher → cleanup call path. CLI/workflow privilege activation is still
unconditionally disabled. No account, sudo command or host-root repository Python
was executed during these tests; native account effects are simulated.

Collision checks cover name/number NSS lookups, bounded local passwd/group rows,
dormant primary-GID reservations, subordinate ranges and running identities. One
nonce-named non-login user/group pair, different from the runner, is selected from
900–999. No existing account, supplementary membership, sudoers or kernel policy
is modified. Native passwd status must show locked; native sudo policy query must
explicitly deny the new account. Installed behavior remains a live prerequisite.

The complete native prefix is inspected before the first administrative command.
Account tools/configuration/hooks are constrained and root-owned program hashes
are bound through cleanup. Root executes only explicit maintained native tools,
with fixed deadlines. Git source staging and deletion of staged contents occur
after the existing two-stage privilege drop. There is no recursive chown, root
recursive delete, password/token, root application or reusable privileged helper.
Only the exact newly owned empty run directory may be removed by root.

Original source is bound before provisioning; the staged exact detached commit
is independently bound inside the final launcher. A real disposable Git fixture
tests staging from detached HEAD with no local branch. Operator and service IDs
are distinct; the old shared-operator native execution fallback now refuses.

An attempted native launch without a validated acknowledgment retains/quarantines
the identity, even if the unit is temporarily absent. Failed or ambiguous resource
cleanup likewise cannot authorize identity release. Account deletion is last,
after exact unit/subtree absence, UID/GID process absence, private-tree removal,
account tuple rechecks and a permissible source-bound cleanup disposition. Partial
setup, lost replies or ownership uncertainty are UNVERIFIED, not automatic rollback
or permission to recycle a UID. No blind retry/force deletion is used.

The dedicated identity requires ownership/mode checks against unrelated identities;
live denied-access proof is still required. It does not yet exclude same-host-UID
workers: current G/I/W map to the supervisor UID and the hosted runtime projection
does not make all control paths unreachable. That is missing implementation, not
merely missing evidence. The same-UID limitation remains OPEN.

## Remaining gates and next action

The [finding ledger](review/2026-10-26-consolidated-remediation/findings.json)
uses owner-summary topic IDs pending the original numbered review. No item is
VERIFIED_CLOSED by the writer. Internal Codex reviews are separately attributed.
One consolidated Claude read-only re-review should cover the P1, all eight P2
dispositions, the new bootstrap/account path and activation wiring. It is prepared,
not automatically invoked from this environment; the original review's missing
metadata/numbering must be reconciled without inventing provenance.

Privileged bootstrap/live cgroup proof: NOT_RUN. External full-service cleanup and
resource proof: OPEN. Hosted runtime closure: UNSEALED and materially incomplete;
executable/loader hashes and a writable staged checkout are not sealing. The old
Nix closure pins were not repinned to pretend to bind hosted Python3.13.5.
Credential release remains WITHHELD. Sentinel execution and browser/BackupV2/APPLY/
review-edit-reject/DRAFT/packet/restart/restore remain NOT_RUN. A later credential-
free probe may pass independently of runtime sealing, but cannot release secrets.

The complete authoritative suite on the supported final candidate remains required;
discovery is not execution. The known unchanged native-Cloud pg_dump refusal is
not rerun. Historical1668/1637/1632 and later failed native/container component
results retain their executed-source attribution. No official phase changed.

After actual blocking-review and activation dispositions are closed, run one bounded
same-generation credential-free dedicated-host proof, including kill/cancel/partial
setup and cleanup. Finish runtime/worker-control/sentinel/release gates before the
canonical business sequence. Required controls remain two DRAFTs, three lines,
14 packet members and $282 merchandise + $7 fee = $289, derived rather than seeded.
Railway compatibility and real purchasing readiness are not established. No Railway,
production, November-price/mapped-offer activation, private-data, Shopify, purchasing,
main/PR24, owner-Mac or Replit operation is authorized or performed here.
