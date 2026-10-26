# Cloud validation checkpoint — 2026-10-26 — NON-GREEN

Repository: `Vedang1998/Buffalo-Procurement-System`.
Branch: `codex/hosted-synthetic-acceptance`.

Material candidate: `df147b2085283048afed1084686e3ccd20b99fe8`.
Tree: `07e7ebe710523e60c6293f28ed8bec58d495229e`.
Review range: `ddc982ce999b56e9eb8416bc0736e20ff88bf9a1..df147b2085283048afed1084686e3ccd20b99fe8`.
The intermediate checkpoint `272043700f211264850eb5bf3ab70fb4d8f2b764`, tree
`c46f097ce528f4578fd9418d2f032bbaccc4004f`, is also published normally.
Only the fixture driver's interpreter entrypoint differs between those two
material commits. This documentation/evidence descendant has a separate tree;
tests below were executed at the material identities, not rerun on documentation.


## Implementation and trust boundary

The ordinary trusted-program path now requires root ownership of every ancestor,
including `/`. Previously a root-owned executable beneath a user-owned 0755
directory passed validation even though that owner could rename/replace it.
Four negative ancestor cases demonstrated the defect before repair; the new
tests require refusal before executing even `--version`. Positive root-owned
chains and writable/symlink refusal are covered. The exact historical managed
Nix-store exception is unchanged; its managed-store immutability assumption was
not established anew and is not authority to fabricate a user-controlled store.

The reusable `scripts/run-synthetic-postgres-fixtures.py` executes 19 backup
contract tests and 31 real PostgreSQL integration methods in an ordinary
disposable container. It uses genuine PostgreSQL 16 tools from the existing
digest-pinned image, a nonroot UID, network=none, read-only root/software mounts,
dropped capabilities, no-new-privileges, bounded tmpfs, 2 GiB memory and 128 PIDs.
There is no host Docker socket, /proc, cgroup or writable bind mount. This does
not change host file ownership or grant the native launcher any privilege.

The driver binds raw tracked bytes/modes and index flags to the clean commit,
hashes actual read-only Python/Git/library inputs and rechecks source/dependencies
after execution. A nonce/name precedes creation; acknowledged resources are
tracked and removed by full container ID. Rename cannot hide an owned container;
a name replacement is preserved and fails cleanup certification. Signals defer
to bounded owned cleanup. Ambiguous creation is reconciled conservatively and
never certified as clean solely because an immediate lookup is empty. Timeout,
abnormal test counters, count mismatch, OOM or cleanup failure refuse success.

The first committed 50-test run exposed one fixture-launcher defect: the nested
backup verifier runs in a scrubbed environment, so invoking the system Python
left it without locked packages. The driver now invokes the existing read-only
virtualenv interpreter; it does not inject credentials or relax that child
environment. The original failed run and diagnostic correction remain evidence.

These are **CONTAINER_FIXTURES_ONLY** results, never native-host capability or
hosted synthetic acceptance. PostgreSQL catalog/ACL checks, role separation,
runtime pins, test floors and real tamper/refusal assertions remain intact.
Four new methods raise authoritative discovery/floors to 1,678 across 94 modules.
No dependency manifest, lockfile, workflow, business rule or official phase changed.

## Machine results and attribution

The final native authoritative run on the exact material candidate completed
2026-10-26 15:19:13–15:36:25 UTC: **1,678 discovered = 1,667 executed + 11
unexecuted**. Executed methods are **1,665 passed + one failed + one errored**.
Two error events are database test09 plus transfer class setup; that setup
blocked all 11 transfer methods. Test09 refuses untrusted host `pg_dump` before
APPLY; dependent test10 observes initialized 9.5000 instead of expected 30.
No module, teardown or subtest errors; skips/expected failures/unexpected
successes are zero. Runtime 1,027.513 seconds, exit 1, no timeout. All six frozen
source hashes and HEAD/tree remained unchanged. Acceptance is NON-GREEN.

The final committed container runner separately passed **50/50** in 88.144
seconds: 19 backup contract tests plus all 31 real PostgreSQL integration
methods, with every abnormal counter zero. This proves the fixture workflow
with genuinely trusted in-image PostgreSQL tools, including Backup V2/APPLY,
distinct-cluster transfer/restore, SCRAM, catalog/ACL and artifact tamper refusal.
It is not a combined native-suite PASS or the integrated browser scenario.
Exact-candidate startup passed 10/10; 18 dependency/application/tool imports
passed. Native full cleanup reaped 156 owned descendants; container success
reaped six and verified exact container absence, with OOMKilled=false.

The earlier completed native run at `2720437…` had the same method/event totals,
ran 1,023.729 seconds and retained unchanged source. It is separate execution
evidence; its raw log SHA-256 is `a70a7c9f043d0f4c9bd6e2db442b90cfc4f270a41f832ffea61d4e382f0940f0`.

Causal lifecycle checks retain their actual attribution: renamed-container
SIGTERM at `2720437…` returned 130 and removed the full-ID-bound original.
The external virtualenv-correction diagnostics simulated lost create
acknowledgement and a copied-label same-name replacement. They removed only
the original, preserved the replacement for its test controller, and returned
failure rather than certifying ambiguous cleanup. The controller subsequently
removed its own replacement. These external diagnostics are not relabeled as
executions of the final committed driver.


Historical evidence is preserved without relabeling:

- Original `593926c355dc4bd23ea5b190e7d364b3e2074f91`: 1,668 discovered,
  1,637 executed = 1,632 passed + one failed + four errored methods. Three
  additional class-setup errors make seven error events; 31 methods unexecuted.
- October 23 material `b236f759fe1754358df976ef36feb9c4a4a0bca2`: 1,674
  discovered, 1,663 executed = 1,661 passed + one failure + one method error.
  A transfer class-setup error makes two error events and blocks 11 methods.
- October 26 pre-freeze backup baseline 15/15, repaired backup/floor checks
  57/57 and startup 10/10 passed. A prototype container integration run passed
  31/31 in 82.519 seconds. The first committed container run at `2720437…`
  executed all 50, passed 49 and failed one nested dependency import in 85.643
  seconds. A separate external virtualenv-correction diagnostic passed 50/50 in
  90.680 seconds. These do not substitute for the final exact-candidate run.

Scanned, byte-preserved synthetic raw evidence is committed under
[review/2026-10-26](review/2026-10-26/manifest.json). The manifest records every
file's byte count and SHA-256; `.log` was renamed to `.txt` without content changes.
No private identity, credential URL or high-confidence secret pattern was found.

- [Full authoritative raw log](review/2026-10-26/oct26-final-authoritative.txt)
  SHA-256 `34a09060c42a378d5ddda333521fb450ff2735c251c3ddce9a019856370cb6fd`.
- [Exact-candidate container 50-test log](review/2026-10-26/oct26-final-container-50.txt),
  [startup log](review/2026-10-26/oct26-final-startup.txt), and
  [six-file source freeze](review/2026-10-26/oct26-final-source-freeze.json).
- [Public Actions observation](review/2026-10-26/oct26-public-actions-observation.json),
  [native host capabilities](review/2026-10-26/oct26-host-capabilities.json),
  [virtualenv provenance](review/2026-10-26/oct26-venv-provenance.json),
  [publication verification](review/2026-10-26/oct26-final-material-publication.json),
  lifecycle case records and [post-run inventory](review/2026-10-26/oct26-post-validation-cleanup.json).

Current public run: https://github.com/Vedang1998/Buffalo-Procurement-System/actions/runs/40348308393
(job `121661125981`). The workflow's committed/public 3,573 bytes match SHA-256
`ea05e42dda07cea0228d7c210c7e4837ae4b12b546a59114d9c371d5dae6ec6a`.
This public status observation does not provide the sign-in-gated diagnostic log.


## Hosted gates and concrete blockers

| Gate | Actual status |
| --- | --- |
| Source publication | PASS for the material candidate: ordinary branch-only update, fresh-fetch commit/tree verified; protected refs unchanged. |
| Native authoritative suite | NON-GREEN; see exact accounting above. |
| Ordinary container PostgreSQL fixtures | Separate bounded validation, not acceptance. |
| Hosted capability/containment | INCOMPLETE. Fresh native inspection: UID1000, no effective capabilities, cgroup2 rw with memory/pids enabled, but all three visible directories and controls root-owned and unwritable; no supported installer/delegation manager is available. |
| Executable harness / sentinel / release | Incomplete. Stopped PID1 enrollment, owned cgroup limits/membership/zero-OOM proof, retained executable projection and one-shot 43-byte release remain outstanding. Hosted provider was not invoked. |
| Browser / canonical workflow | NOT RUN. Authenticated confirmation, Backup V2 binding, browser APPLY, actual two vendor DRAFTs / three lines / 14 packet members / $289 have not been reproduced through the hosted workflow. |
| Integrated restart/restore/resource acceptance | NOT RUN. PostgreSQL fixture transfer/restore tests are not that scenario. |
| GitHub Actions | Public run 40348308393 at `df147b2…` is FAILURE: checkout/source/Python/focused 151-test gate succeeded; namespace observation exited 1. Public workflow bytes match the commit. Detailed logs/JSON/cleanup require sign-in; API remains Forbidden. This intentionally non-authorizing diagnostic failure is not proof of a particular host restriction. |
| Independent review | NOT ASSESSED. Internal Codex reviews are identified as internal. No callable Claude Code/Claude connector or Cursor reviewer is available. |
| Railway readiness | NOT_PROVEN; no deployment or production endpoint was touched. |

Native Cloud still correctly rejects extracted user-owned `pg_dump`. Ordinary
container success establishes the portable fixtures on genuine trusted tools;
it does not convert those host tools into trusted system executables. No Docker
root broker, host chown, cgroup permission change or platform restriction bypass
was attempted. The existing hosted plan gates Tasks 4–9 behind Task 3 PASS.

See [PURCHASING_CRITICAL_PATH_2026-10-26](PURCHASING_CRITICAL_PATH_2026-10-26.md)
for current-evidence versus implementation gaps. General price rollover and
production mapped-offer integration are concrete implementation dependencies;
current catalog/inventory/sales, approved November economics and reconciled
incoming are evidence dependencies. November 9 readiness is not established.

## Independent review package and next executable action

Review the exact material range above, this evidence, the hosted plan/design,
and the older recovery/fixture reports. Reviewer remains read-only. Focus on:

1. ordinary versus historical Nix executable trust and mutable ancestors;
2. raw source/dependency binding versus an accepted runtime closure;
3. cancellation, ambiguous creation, renamed resources, replacement preservation
   and cleanup-failure precedence;
4. genuine PostgreSQL program ownership and complete role/catalog/tamper checks;
5. honest method/error-event accounting and boundaries between component tests,
   native host capability and integrated browser acceptance.

Reproduce the bounded container check on the exact clean candidate with the
tested Cloud toolchain (explicit invocation only, never automatic startup):

```bash
cd /workspace/Buffalo-Procurement-System
source /workspace/.cloud-setup/buffalo/activate.sh
python3 -I -S -B scripts/run-synthetic-postgres-fixtures.py integration
```

The next native prerequisite is supported Cloud provisioning of genuine trusted
PostgreSQL 16 system tools and a writable delegated cgroup subtree usable by
the same reviewed launcher. Verify those capabilities before running changed
live containment probes. Then implement/review the owned lease, stopped PID1
enrollment and exact I/W membership, retained sentinel execution and gated
one-shot release. Keep every provider unreachable until all current-generation
proofs pass; do not use a privileged stand-alone probe as later authority.

Post-run native inventory found no live database/browser/application process,
Docker container, temporary database directory, traversal error or host projection.
The same eight old nonrunning database/browser zombies and six unattributed
October 17 anonymous volumes remain; this work created no retained volume.
No unknown resource was removed. Temporary synthetic resources were disposed;
no test credential was added to reusable configuration or published evidence.

The existing tested Install script is unchanged. Start instructions preserve
lightweight imports, the public noreply identity and an explicit-only invocation
of the fixture runner. Saving an environment draft is not applying/publishing a
snapshot or proving fresh-task restoration. No environment snapshot was published;
the old volume-attribution limitation still needs resolution before snapshotting.


No metadata rewrite was repeated. The confirmed noreply configuration and
published recovery lineage remain intact. No main/default-branch or PR #24
change, merge, Railway deployment, production database access, private-data
transfer, Shopify operation, PO release, supplier transmission, Replit SSH,
owner Mac access or Replit shutdown occurred. This is WIP, not integrated
acceptance or real purchasing readiness, and no automatic future resumption is
represented as a platform guarantee.
