# Recovery review and Cloud continuation — 2026-10-17

The sole writer reviewed the seven-file recovery delta before selecting any
changes. No merge or cherry-pick was performed.

Verified remote starting identities:

| Ref | Commit |
| --- | --- |
| `codex/hosted-synthetic-acceptance` | `99f265e9ab12dfcd970b3072a57d5f2c5163cc07` |
| `recovery/mac-hosted-validation-20261017` | `fe15815de527175b144cbbd61a2f9d4c16b5c72c` |
| `codex/railway-staging-delivery` | `cb0969059b0b85241d611124910d691e49212881` |
| `main` | `f308ac666a2377f540e528bc873463daecc20cf8` |
| PR #24 head | `e25704e117a3c5bede843cac65ddfdeb7f30024f` |

Active starting tree: `c4beee642cb87f9b1c08f3c03d09111a0f635bc3`.
Recovery tree: `43d087cb1eb6121eee97d08dd8fbcaff4b39b2d5`.
Recovery is a direct descendant of the active starting commit. It is not a
competing implementation already present in the active branch.

| Recovery file | Disposition |
| --- | --- |
| `procurement/tools/hosted_synthetic_source.py` | Useful new Task 2 component, selectively adapted. Original Git capture checked its limit only after buffering output; original path traversal could race ancestor replacement and read mode from a different file. Both corrected. |
| `procurement/tests/test_hosted_synthetic_source.py` | Useful 23-test base, adapted and extended to 36 tests for bounded capture, process reaping, path/mode races and actual signal cancellation. |
| `procurement/tools/run_tests.py` | Useful registration pattern. Recovery's 23-test floor superseded by the actual 36-test source module; new diagnostic/workflow modules add 16 and 7 tests. |
| `procurement/tests/test_persistent_mapping_foundation_contract.py` | Useful floor guard; recovery's 1,535 total superseded by 1,571. |
| `procurement/tests/test_supplier_format_conformance.py` | Same disposition; exact total is 1,571 across 92 registered modules. |
| `docs/CODEX_HANDOFF.md` | Mac-specific status is historical and outdated for this Cloud checkout. Replaced by a fresh Cloud evidence entry; not copied as current validation. |
| `docs/superpowers/specs/2026-10-17-hosted-validation-bridge-proposal.md` | Not adopted. It is explicitly an unapproved validation-order/workflow proposal, not implementation authority. Its lack-of-Linux premise is outdated in Cloud. The old pinned-runtime/full-validation issue remains real and separately reported. |

No recovery code was an exact duplicate of the current active implementation.
The helper complements existing containment tooling; it does not replace it.
The Mac handoff and proposed first workflow/job ordering were incompatible
with claiming the active approved hosted checkpoint complete.

## Changes and review

The new helper requires exact repository/push/ref/full-SHA metadata, a clean
detached checkout descended from the approved baseline, exact index and raw
tracked bytes, and a committed hosted workflow. It avoids repository clean
filters, hooks, inherited Git credentials and lazy network fetches. Source
capture is bounded to 16 MiB per Git output/file and 15 seconds per Git command.
Held directory/file descriptors bracket path and executable-mode identity.

A separate read-only Codex reviewer reproduced a P2 cancellation defect:
SIGINT between descriptor acquisition and ownership recording leaked a
descriptor. The writer added bounded cancellation deferral, single-thread/main-
thread preconditions, complete cleanup attempts, and cleanup-error precedence.
Actual SIGINT regressions cover initial and nested acquisition, cleanup, and
subprocess ownership. The reviewer repeated the original reproduction with zero
leaked descriptors and restored signal mask, and found no remaining P0/P1/P2
within this source-snapshot scope.

The helper is portable across compatible Linux hosts, not macOS: it requires
Linux `/proc` for thread/descriptor checks. Its result always has
`execution_authority=false`. It is a snapshot, not an immutable execution
lease or a credential-release gate. The new hosted diagnostic workflow requires a clean detached committed checkout;
ordinary attached or dirty development invocations still refuse.

Full-suite diagnosis also exposed an existing test-fixture defect. The first
private browser test omitted the production launcher's subreaper precondition.
On this container's non-reaping PID 1, Chromium descendants remained zombies;
a cleanup exception then stranded independent HTTP fixture threads. The writer
changed only the test fixture to establish and restore the incoming subreaper
state and register unconditional server shutdown/close/join. Browser assertions
and production cleanup checks remain unchanged. Independent synthetic review
verified restoration for both incoming subreaper states.

All reviews here are internal read-only reviews by separate Codex agents using
the same model family. They are not independent external/Claude/Cursor review
or owner business-rule acceptance.

## Execution contexts and remaining gates

The command sandbox maps system root ownership to `nobody` and exposes read-only
UID maps/cgroup mounts. Existing root-owned Git and namespace checks correctly
refuse there. Separate approved native Cloud checks found:

- native UID/GID 1000, system Git owned by root;
- a credential-free disposable `unshare` screen for USER/MOUNT/PID/NET and
  private procfs succeeds;
- cgroup v2 mounted read/write, but no writable delegation for the current user;
- no `sudo` executable or connected user systemd manager providing delegation.

This screen is not the required same-generation capability proof. No cgroup
permissions, kernel policies, procfs checks or legacy EPERM observer were
weakened. The old exact Nix/Replit runtime closure is not installed here.

The new diagnostic workflow will measure only namespace prerequisites on the
approved GitHub Ubuntu 24.04 VM; complete capability remains unproven. HTTPS Git
access works; Actions API access currently receives proxy CONNECT 403.
The narrow `api.github.com` addition is saved in the environment configuration
draft; draft persistence does not establish live access.

Complete test outcomes, log hashes and published checkpoint identities belong
in [CODEX_HANDOFF](CODEX_HANDOFF.md). The business audit is consolidated in
[BUSINESS_READINESS_2026-10-17](BUSINESS_READINESS_2026-10-17.md). No official
phase completion, hosted capability PASS, browser Option B PASS, Railway
readiness, or real purchasing readiness is asserted.
