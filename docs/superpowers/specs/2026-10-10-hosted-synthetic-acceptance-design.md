# Hosted Synthetic Acceptance Design

**Status:** owner approved on 2026-10-10

**Risk:** Level 3 containment, browser, database, and acceptance tooling

**Baseline:** commit `cb0969059b0b85241d611124910d691e49212881`,
tree `84a362b0a22b8a607077fb8250ac1885fb08c381`

**Branch:** `codex/hosted-synthetic-acceptance`

This design implements the owner-approved publication, hosted capability
probe, and executable synthetic acceptance objective. It preserves the frozen
1,512-test baseline as historical evidence. Every later result is bound to the
new commit, tree, workflow, harness, worker, application, and dependency
identities that actually produced it.

## 1. Scope and staged authority

The work is split into three strict gates:

1. publish the exact baseline by non-force fast-forward;
2. prove the required containment mechanisms on a standard GitHub-hosted
   `ubuntu-24.04` runner with disposable sentinels only; and
3. only after a capability PASS, complete, review, and execute the canonical
   synthetic Option B acceptance scenario.

The capability result is feasibility evidence, not authorization for a later
job or process generation. The eventual synthetic worker must repeat every
release-critical containment check in its own VM and generation immediately
before generating or releasing task-local synthetic credentials.

Private procfs is an acceptance-tooling requirement. The deployed Railway
bootstrap does not execute the acceptance tools or create a private procfs.
Every hosted result therefore records
`procfs_scope=ACCEPTANCE_TOOLING` and
`railway_compatibility=NOT_PROVEN`.

Private research acceptance remains `NOT RUN`. This task transfers no private
research closure, bucket credential, or research payload.

## 2. Publication boundary

The baseline is publishable only when all of the following remain true:

- the local source is clean at the exact approved commit and tree;
- the unpublished history has no secret, private payload, unintended evidence,
  oversized object, or unexplained binary;
- the live remote `codex/railway-staging-delivery` target is an ancestor of the
  candidate;
- the update is a normal fast-forward without force; and
- `main`, PR #24, Railway, and every unrelated remote ref remain unchanged.

After publication, the acceptance branch starts at the exact published
baseline: publish `cb0969059b0b85241d611124910d691e49212881` to
`refs/heads/codex/railway-staging-delivery`, then create
`codex/hosted-synthetic-acceptance` from that verified remote identity. Harness
changes produce new candidates and must not inherit the baseline's test or
review claims by implication.

## 3. Hosted workflow boundary

Add one dedicated workflow at
`.github/workflows/hosted-synthetic-acceptance.yml` with these source-level
invariants:

- trigger only on a push to `codex/hosted-synthetic-acceptance`;
- no pull-request, fork, schedule, reusable-workflow, or manual trigger;
- `permissions: contents: read` and no OIDC or write permission;
- `runs-on: ubuntu-24.04`;
- checkout the full `${{ github.sha }}` using a full-SHA action pin,
  `persist-credentials: false`, and sufficient history for source binding;
- assert repository, event, ref, SHA, tree, clean status, and ancestry from
  the approved baseline before executing repository code;
- use hard timeouts, with the capability job limited to ten minutes;
- pass no GitHub credential, runner credential, provider secret, production
  configuration, preexisting/ambient libpq credential, Shopify credential, or
  private input to repository processes. A later synthetic job may pass only
  freshly generated task-local PostgreSQL credentials through its attested
  bounded secret channel; and
- upload no payload artifact. Bounded credential-free JSON evidence may be
  printed and retained in ordinary job logs.

The first published workflow contains only the capability preflight and its
focused validation. Synthetic execution is added only in a later reviewed
commit after the preflight succeeds. When present, the final graph is:

```text
capability-preflight -> focused-validation -> synthetic -> authoritative-validation
```

Every job independently binds its checkout. The synthetic job repeats the live
containment gate; it never consumes an earlier job's PASS as release authority.

## 4. Capability preflight

The preflight uses the same privilege and containment composition intended for
the future synthetic worker. A privileged probe followed by an unrelated
unprivileged launch is insufficient. The approved path may use only the
host-native privilege/delegation already available to the eventual launcher.
If success requires introducing a new privileged broker or proc-FD authority,
that is an out-of-scope architecture dependency and this task stops for
separate owner approval.

The preflight proves, with fresh owned resources:

1. pidfd ownership, bounded signaling, `PR_SET_PDEATHSIG`, and
   `PR_SET_NO_NEW_PRIVS`;
2. a stopped, identity-bound guardian that cannot execute the payload before
   enrollment;
3. `CLONE_NEWUSER|CLONE_NEWNS|CLONE_NEWPID|CLONE_NEWNET`, exact UID/GID maps,
   and private mount propagation;
4. a fresh procfs created with the fd-based mount API, or the separately
   attested equivalent legacy fallback, with a new mount identity,
   `nosuid,nodev,noexec`, namespace PID 1/PID 2 topology, exact `NSpid`, and no
   visibility of host-root processes;
5. a detached tmpfs projection containing only a code-owned executable
   sentinel, sealed read-only/nosuid/nodev while retaining execution,
   attached by fd, write-refusing with `EROFS`, and removed without residue;
6. unified cgroup v2 with an owned child, `memory` and `pids` controllers,
   exact limits, `memory.oom.group`, zero OOM deltas, correct process/thread
   membership, and freeze/unfreeze when the launcher depends on it; and
7. one fabricated 43-byte sentinel released through the real one-shot channel
   only after all current-generation checks pass.

`clone3(CLONE_INTO_CGROUP)` is recorded when reachable, but is not required if
the already-reviewed stopped-guardian enrollment achieves the same pre-exec
boundary. Ordinary live spawn-then-attach is never an accepted fallback.

Failure before the release gate must report provider calls `0`, payload
processes `0`, credential status `WITHHELD`, and execution authority `false`.
No unchanged deterministic capability failure is retried.

## 5. Cleanup and evidence

Cleanup outranks cancellation and diagnostic errors. Owned resources are
identified before mutation, and cleanup never signals, closes, unmounts, or
removes a foreign replacement. The preflight must:

- kill by pidfd and/or the exact owned cgroup, reap the process group, and
  prove terminal absence;
- detach every owned mount and restore the named mountpoint identity;
- close every owned descriptor without closing a reused foreign descriptor;
- remove only identity-bound task roots and cgroups;
- prove no owned PID, cgroup, mount, listener, socket, temp path, or descriptor
  remains; and
- make a cleanup failure override a capability success.

The result is bounded canonical JSON plus its SHA-256. It records the exact
repository commit/tree/workflow hash, runner/kernel/cgroup facts, every
capability disposition, fabricated-release count, cleanup counts, and the
explicit scope labels above. It contains no raw environment, credential,
private bytes, or unbounded host inventory.

## 6. Executable harness after capability PASS

The existing entry-point refusals are deliberate placeholders and remain until
their positive path is implemented. Completion does not mean changing a
failure return to success. The accepted observers, sealed runtime bundle,
projection materializer, guardian protocol, process observers, browser frame
protocol, staging services, transfer operators, backup operator, and business
validators remain the building blocks.

The smallest accepted positive composition must add:

- owned lifecycle leases for the namespace init, projection, cgroup, worker,
  browser, TLS endpoint, PostgreSQL clusters, service, evidence root, and
  task-local credential buffers;
- a live PID-namespace init and worker launched from the exact sealed
  projection, with private procfs and current-generation cgroup evidence;
- a reviewed network path that preserves the required namespace isolation
  while exposing only the task-local TLS gateway and CDP endpoint;
- exact, portable, hash-bound Python, Node, Chromium/helper, TLS, and source
  dependencies for the hosted runner;
- hidden worker dispatch that accepts only exact inherited descriptors and the
  canonical request, emits READY before one-shot secret delivery, and emits a
  phase-specific RESULT only after full proof validation; and
- a second staging browser phase for APPLY, mapping, selection, review, DRAFT,
  and packet downloads, using staging authentication and no upload substitute.

If completing any item requires new application architecture, weakened
containment, broader database privilege, altered procurement calculations, or
a fixture substitution, work stops at that exact dependency rather than
silently changing the accepted contracts.

## 7. Canonical synthetic sequence

After the new harness passes focused proof and independent review, it executes:

1. create physically distinct disposable loopback PostgreSQL 16 source and
   target clusters;
2. run the canonical synthetic transfer, target preparation, restore, and
   SCRAM transition;
3. with services stopped, stage the exact registered raw fixture through the
   canonical validator to `VALIDATED`;
4. start the real staging composition without invoking private research;
5. authenticate through task-local TLS and use the browser to preview and
   `CONFIRM`, proving `VERIFIED_FUTURE`;
6. stop the service, create and verify Backup V2, bind its exact label,
   manifest, and source tree, and restart;
7. browser-`APPLY`, complete the canonical mapping/selection/review sequence,
   generate DRAFTs, and download the packet;
8. independently reconcile two vendor DRAFTs, three lines, 14 packet members,
   `$282` merchandise, one `$7` fee, and `$289` total;
9. prove restart, backup/restore to a distinct recovery target, byte/control-
   total reconciliation, process and cgroup resource ceilings, and zero OOM;
   and
10. remove all owned processes, clusters, containers, mounts, cgroups,
    listeners, secrets, temp paths, and evidence not explicitly allowlisted.

No final-state row, decision, DRAFT, packet, or result may be preseeded. The
harness records prestate absence and causal event lineage before claiming PASS.

## 8. Validation and review

Focused tests precede any live hosted run and prove:

- exact workflow triggers, permissions, checkout, timeouts, and absence of
  secret-bearing or untrusted paths;
- every failed namespace, procfs, projection, cgroup, source, dependency,
  generation, process, environment, or descriptor check withholds release;
- valid prerequisites reach real execution rather than a mocked success;
- browser and child failures propagate and partial execution cannot emit PASS;
- cancellation at every ownership transition cleans all owned resources while
  preserving foreign replacements;
- stale evidence from another job or generation cannot authorize execution;
- preseeded final database state is rejected; and
- the canonical synthetic result is derived from source and decisions.

Test floors only increase. Each meaningful candidate receives secret/private-
data scanning, exact focused and authoritative test counts, startup validation,
and attributable independent read-only review. `docs/CODEX_HANDOFF.md` records
the commit/tree, workflow run and job IDs, source/dependency bindings, each gate
as `PASS`, `FAIL`, or `NOT RUN`, cleanup evidence, and the exact next boundary.

## 9. Alternatives considered

The selected approach is the staged preflight-first design above. It publishes
the smallest reviewable mutation, obtains evidence from the actual hosted
kernel before implementing expensive orchestration, and still requires the
real synthetic job to re-prove containment in its own generation.

Two alternatives were rejected:

- A single workflow commit containing both the probe and full harness would
  front-load large, unexercisable containment and browser changes before host
  feasibility is known. A deterministic host restriction would waste work and
  make review attribution harder.
- A YAML wrapper around the current local acceptance entry point cannot work
  honestly. That entry point is intentionally inert, its blocker observer
  accepts only the local `EPERM` result, and its dependency identities are
  local Nix/Replit paths absent from a standard hosted runner. Deleting those
  refusals would bypass rather than satisfy the accepted safety boundary.

## 10. Hard exclusions

This design authorizes no change to `main`, PR #24, Railway, a public endpoint,
an external application database, Shopify, supplier systems, real purchasing,
production credentials, private research data, or Replit lifecycle. It creates
no PR and performs no merge or protection bypass. Synthetic acceptance alone
is not complete Task 9, Railway readiness, or production readiness.
