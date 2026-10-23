# Cloud fixture checkpoint — 2026-10-23 — NON-GREEN

Repository: `Vedang1998/Buffalo-Procurement-System`.
Branch: `codex/hosted-synthetic-acceptance`.
Executed material candidate: `b236f759fe1754358df976ef36feb9c4a4a0bca2`.
Tree: `01e81038c415104af8719065db87160cfd26b7e7`.
Review range: `a55f147d92140bba0096bad7ba65b1d10f3ba9d8..b236f759fe1754358df976ef36feb9c4a4a0bca2`.

## Changes and preserved guarantees

Ten files changed: two test-support helpers, seven test modules and population
floors in `procurement/tools/run_tests.py`. Six new regression methods increase
discovery/floor from 1,668 to 1,674 across the same 94 registered modules.
Application code, production runtime/catalog pins, validators, workflows,
dependency manifests and lockfiles are unchanged from the review base.

Runtime tests now use declared disposable exact-byte fixtures and independently
encoded manifests. Scoped current-interpreter expectations exercise actual
descriptor/process checks without claiming an accepted runtime closure. Frozen
production pins still reject those fixtures. Source drift, missing inputs,
provenance, absent caches/archives and zero execution authority remain asserted.
Child observations establish real no-new-privileges and seccomp state; the
fixture filter is not represented as the accepted complete worker policy.

The private-proc lifecycle test accepts only the historical dual-EPERM outcome
or exact successful mount outcomes, retaining mappings, identity, cancellation,
process-group and descriptor cleanup assertions. The production blocker observer
is unchanged and still withholds authority on successful mounting as well.

A full-order interaction was reproduced: earlier browser/Git fixtures left
zombie children attached to the test runner, so the new clean-entry subreaper
guard correctly refused. Only this lifecycle body now runs in a fresh isolated
test supervisor. Its original clean-entry/no-children-exit checks remain. The
parent verifies an unrelated owned zombie remains unreaped. A separate causal
timeout assertion proves an ordinary descendant is killed and reaped. Owned
session cleanup signals the group before reaping the held leader, preventing
PID/group reuse; bounded waiting still occurs if signaling reports an error.

Fresh owned PostgreSQL 16 clusters freeze their routine fingerprint before
scaffolding. Source/destination must agree. The complete security projection
and unchanged production global privilege verifier run on read-only repeatable
read snapshots. Only the test-process expected routine digest is scoped; actual
catalog/ACL tampering assertions and production pins remain. Fixtures explicitly
use UTC. Repeated nested setup/cleanup failures restore the pin before resource
cleanup. Accounting regressions distinguish setup/teardown events from methods.

## Machine evidence and accounting

The completed full authoritative run executed the exact candidate above on
2026-10-23, 18:33:15–18:49:30 UTC: **1,674 discovered = 1,663 executed + 11
unexecuted**. The executed methods are **1,661 passed + one failed + one
errored**. Two reported error events comprise that method error (database 09)
and one transfer class-setup error, which blocked all 11 transfer methods.
There were no module, teardown or subtest errors; skips, expected failures and
unexpected successes were all zero. Runtime was 971.814 seconds, exit 1,
without timeout. The runner correctly rejects discovered/executed mismatch.

All ten frozen file hashes still match; HEAD/tree and the worktree stayed
unchanged. Exact candidate startup validation passed 10/10. The full-order
runtime lifecycle check passed. Acceptance remains **NON-GREEN**.

The run removed its exact synthetic PostgreSQL container and reaped 156 owned
descendants. A separate post-run and post-installer native inventory found zero
live database/browser/application processes, Docker containers, temporary
database directories, traversal errors or host projection paths. Eight
nonrunning database/browser zombie entries from the earlier interrupted attempt
remain under PID1; this account cannot reap foreign children. Six pre-existing
anonymous Docker volumes remain unattributed and untouched. No environment
snapshot was published, and this inventory does not claim their contents are safe.


The historical completed run remains attributed to original executed commit
`593926c355dc4bd23ea5b190e7d364b3e2074f91`: 1,668 discovered/1,637 executed =
1,632 passes + one failed method + four errored methods. Three additional class
setup errors produce seven error events; 31 methods did not execute
(database 18, price-stage 2, transfer 11). No module, teardown or subtest errors;
zero skips, expected failures or unexpected successes. Metadata equivalence
does not mean those tests ran again.

The first new full attempt at `470ac7a5bca23e84c68f2e5bd03948dff7bb1c8a`
discovered 1,674 but was interrupted during environment reconnection before
summary/tracebacks. Its raw log is retained as incomplete, not a completed total.
Its remaining synthetic PostgreSQL container and fixture directory were verified
and removed. The runtime failure was then reproduced by a 160-method sequence:
159 passed/one error with a concrete clean-subreaper-entry traceback and seven
preexisting zombie identities. After isolation/timeout repair, the sequence
passed 160/160 with nine descendants reaped. These pre-freeze focused executions
are distinguished from the exact committed full run above.

Focused PostgreSQL integration before the full run: 31 intended, 20 executed =
18 passed + one failed + one errored method; a separate class-setup error blocked 11
methods (two error events). These are not 17 passes: class-setup errors execute
zero methods. The observed failure cascade is described below.

Raw evidence stays local under `/workspace/.cloud-setup/buffalo/logs`.
Bounded evidence identities:

| Evidence file | SHA-256 |
| --- | --- |
| `runtime-isolation-authoritative.log` | `ace7342344612ea1709e4b71df6fe7b7d2b2d068c0951a05dd6456fd9b2c5d0e` |
| `runtime-isolation-authoritative-state.json` | `7f44b1e1283a4010bc42de2aa48da5888dd32ad9fa1b152d71a83823a19d58cf` |
| `runtime-isolation-source-freeze.json` | `4faf1fbb5781209fe2c6021087304160da1cc29e094d68c36cf3470cbe12b876` |
| `runtime-isolation-startup-final.log` | `6f1aee17904511979dbbb3f1f548c5fb00c5a00c523f88644ec27d2cdd758ae7` |
| `runtime-order-reproduction.log` | `8fd9993de4f4762ec0b5dd41e3f75c172fa1bffdb24f27d2e965498ec4fd8ebd` |
| `runtime-order-repaired-final.log` | `e09bd030624b50182134dc05fbf41f3b8c37f06c861d28abe556e2d32973d173` |
| `catalog-portable-focused.log` | `660fd9a1ee72e3d4e1e253c8e77b956a5a862845647c96f9ffc109ec5f262d0d` |
| `portable-fixtures-authoritative.log` | `ed6d793637bcd1087fcf6d609cf4c864ffe7949c2fc61e54f946449d217a284e` |
| `install-final-cleanup-verification.log` | `a0b357c8de2b586baceafedfd4736c414a4947881ddbe89bf4703b17f80d4153` |
| `install-final-verification.json` | `cb54d9cc803f16a65e9c8ce03707aa638e9c7ed42d1ea4e846dd6127e51345ed` |
| `post-authoritative-cleanup.json` | `0defec4fab89234b81fdb65cf3b9df3bc194907404d9ddbbc5f33c6929a4af80` |
| `post-install-cleanup.json` | `0defec4fab89234b81fdb65cf3b9df3bc194907404d9ddbbc5f33c6929a4af80` |
| `portable-fixtures-publication.json` | `f42286f86bf27b146afbc31742842efb65671d36f1a143a85f6f0a8f71c23723` |

Tested Install script SHA-256: `c0e9a2e65577fc4bc4510e208b5f6c96966a07fdfe44071b372d6ebfdd58ffd5`.


## Remaining gates

Backup V2/transfer correctly refuse the user-owned extracted `pg_dump`. The
unchanged validator requires a genuine trusted PostgreSQL 16 system executable
and safe ancestors (or the historical Nix closure). Cloud has no native system
PostgreSQL executable or supported administrator installer available to this
account. Do not fake ownership or introduce a Docker-based privilege workaround.
Database test 09 stops before applying replacement price 30.00; test 10 therefore
sees initialized 9.50 and fails its 30.00 expectation. Both failures remain
recorded; the latter is a dependent fixture-state cascade, not evidence of a
new financial calculation defect. Do not alter the expected price to hide it.

Native read-only inspection found cgroup2 rw/nsdelegate and memory/pids enabled,
but all four visible directories and enrollment/control files are root-owned
and unwritable to UID1000. No supported delegation or manager socket exists.
A writable mount alone is not delegation. No platform permissions were changed.

Task 2 remains incomplete: supported same-launcher cgroup ownership, stopped
PID1 enrollment before probe, exact PID1/PID2 membership/limits and zero-OOM
proof, retained executable projection, sentinel execution and one-shot fabricated
43-byte release after all current-generation checks. Component evidence is not
a containment PASS. Runtime remains HOST_RUNTIME_UNSEALED; browser/restart/
restore is NOT RUN; procfs_scope=ACCEPTANCE_TOOLING; Railway=NOT_PROVEN.
GitHub Actions status is UNVERIFIED: CLI/public API observation hit proxy 403.

## Recovery, setup, publication and review

The completed five-commit recovery map is in
[METADATA_RECOVERY_2026-10-23](METADATA_RECOVERY_2026-10-23.md).
Original `e7edbbacfc42ec83a5eecf421d0b1f18c42f5354` became
`0302ca1d75ecd4bebdefd29fe8bb33a32af2a760`; both retain tree
`f09ec211b168284a50920df2c4daf3c017414995`. Published base
`99f265e9ab12dfcd970b3072a57d5f2c5163cc07` remains unchanged. Recovery docs
were separately published at `a55f147d92140bba0096bad7ba65b1d10f3ba9d8`, tree
`831c0840e05c136d698eb4c70054800dc09e22b6`. Original private metadata/bundle
remain private; no original private-email commits were published.

The revised full Install script passed: public Git identity configuration,
frozen uv/pnpm installation, 18 imports, syntax/version checks and disposable
PostgreSQL tool inspection. Versions: Python 3.13.5, uv 0.12.3, Node 24.19.0,
pnpm 10.34.7, TypeScript 5.9.3, Docker 28.4.0, Git 2.52.0, gh 2.46.0,
PostgreSQL 16.14, Chromium 154.0.8037.57. Install and routine Start do not run
application, database, browser, purchasing, research replay or full acceptance.
The installer now removes its exact extraction container with its anonymous
volume. One event-bound orphan from the earlier installer was removed; six
pre-existing October 17 volumes have unproven ownership and were retained.
Saving reusable configuration is distinct from publishing/applying it;
fresh-task restoration is NOT VERIFIED.

The material candidate was published by ordinary branch-only fast-forward from
`a55f147d92140bba0096bad7ba65b1d10f3ba9d8` to
`b236f759fe1754358df976ef36feb9c4a4a0bca2`; a fresh fetch verified tree
`01e81038c415104af8719065db87160cfd26b7e7`. The 21 newly published Git objects
passed the original-private-identity and high-confidence secret/path scan.
Main, PR #24 head and the recovery branch were unchanged before/after the push.
This documentation-only descendant records that material publication;
its own final commit/tree are reported separately after fresh remote verification.
Tests remain attributed to the material commit, not to this later documentation.


Internal Codex reviews are internal, not independent Claude review. No remaining
blocking scoped internal finding was identified after timeout remediation.
Independent Claude review remains NOT ASSESSED; no callable Claude reviewer was
available. Cursor specialist review was not invoked. The exact review range
above is prepared for independent examination of fixture versus production
authority, catalog baseline timing/restoration, lifecycle isolation, timeout/
cancellation cleanup, historical EPERM semantics and accounting. The broader
incomplete hosted implementation requires its own containment/release review.

Next executable prerequisite after a supported host provides genuine system
PostgreSQL 16 tools (expected to refuse on this unchanged host):

```bash
cd /workspace/Buffalo-Procurement-System
source /workspace/.cloud-setup/buffalo/activate.sh
PATH=/usr/lib/postgresql/16/bin:$PATH PYTHONPATH=procurement/src python -c 'from procurement_os.synthetic_staging_backup_v2 import _postgres_program; print(_postgres_program("pg_dump")); print(_postgres_program("pg_restore"))'
```

Then rerun the 31 affected PostgreSQL integration methods. Continue cgroup work
only through supported delegation in the same source-bound launcher lifecycle;
no separate privileged probe/broker. Run focused and full validation after
material repairs and obtain independent review anchored to the published source.
Tasks 4–9 remain gated by Task 3 PASS. No official phase advanced.

No main/default branch or PR #24 change, force push, merge, deployment, production
access, private-data transfer, Shopify, supplier transmission, real purchasing,
Replit SSH or owner's Mac use occurred. This is WIP, not integrated acceptance.
