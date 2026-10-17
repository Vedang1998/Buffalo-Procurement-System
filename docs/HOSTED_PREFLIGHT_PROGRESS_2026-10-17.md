# Hosted preflight development evidence — 2026-10-17

This is an incomplete Task 2 development checkpoint. It is not Task 3 acceptance,
a runtime closure, an executable Option B harness, Railway compatibility or
purchasing authority. The one writer was Codex Cloud; reviewers were read-only.

## Repository and source scope

Repository: `Vedang1998/Buffalo-Procurement-System`.
Branch: `codex/hosted-synthetic-acceptance`.
Last verified remote commit: `99f265e9ab12dfcd970b3072a57d5f2c5163cc07`.
Remote tree: `c4beee642cb87f9b1c08f3c03d09111a0f635bc3`.
The candidate is frozen by the exact file hashes below; a subsequent evidence
entry records its local commit/tree. GitHub GH007 rejected the earlier private
author metadata. The owner's no-history-rewrite restriction remains in force;
the requested exception for only unpublished metadata is pending. No branch
push succeeded, no history was rewritten, and no new PR was created.

Recovery commit `fe15815de527175b144cbbd61a2f9d4c16b5c72c` was reviewed as a
seven-file delta; see [the disposition](RECOVERY_REVIEW_2026-10-17.md). There was
no merge/cherry-pick or adoption of its unapproved validation bridge.

### Exact local material checkpoint and source-bound diagnostic

- Local material commit `593926c355dc4bd23ea5b190e7d364b3e2074f91`, tree `c10783fb6a41664d827cdd09a13f99416cb50c77`.
- A disposable clean detached local clone of that commit ran the actual diagnostic
  entrypoint and bound all source files before observing namespace/projection
  evidence. The supplied local metadata was a test invocation, **not an observed
  GitHub push event**. Commit/tree matched; cleanup completed; the temporary
  checkout was removed. Result remained HOST_CAPABILITY=FAIL/INCOMPLETE_PREFLIGHT,
  HOST_RUNTIME_UNSEALED, WITHHELD, provider0/payload0, authorityfalse,
  sentinel_exec/cgroup NOT_RUN. This does not satisfy Task3 hosted acceptance.
- Local diagnostic log SHA256 `2ce154b64eba6cd9ff6121b37924112158f4821e084117a19cda423ab2bce1c8`.
- The following documentation-only commit records this evidence. Material code
  remains the reviewed and fully tested checkpoint above. Publication remains
  blocked by the first unpublished commit's GH007 author metadata; the owner
  history exception is still pending. Preserve the complete local chain.

## Machine results

Final authoritative result: **FAIL**, not a waived gate; 778.352 seconds, exit1.

- Discovered 1668; executed 1637; passed 1632; failures 1; errors 7.
- Skips, expected failures and unexpected successes: all zero. Three failing
  class setups prevent 31 tests from executing.
- Registered population: 1,668 across 94 modules. Focused integration: 215/215.
  Workflow-equivalent isolated `env -i`, `-I -S -B`: 151/151, exact module counts
  36 + 21 + 7 + 65 + 22. Startup hardening: 10/10.
- Remaining failures are the unchanged four exact Nix runtime refusals, the
  unchanged historical EPERM observer test on a native host where mounting
  succeeds, and three fixture setups rejected by the exact system-catalog pin.
  PostgreSQL16.14 and public Debian16.9 both matched the 141-view fingerprint;
  neither matched the required 3,297-routine fingerprint. No pin was changed.
- The preceding 1,666-test frozen run executed 1,635, passed 1,630, failed one
  and errored seven in 774.020s. The final retry follows a material cleanup
  parser repair; it is not an unchanged retry intended to hide those failures.
- Earlier namespace/re-exec validation discovered1,643, executed1,612,
  passed1,607, failures1/errors7 in776.819s. The preceding auth/signature and
  process-disappearance defects were corrected with causal tests; these
  repairs have no remaining failure in the final candidate.

## Native observations and refusals

The disposable retained supervisor/guardian/init/worker composition established
fresh USER/MOUNT/PID/NET identities, exact UID/GID maps, private propagation,
PID1/PID2 topology, stopped worker, private procfs flags and host-process
invisibility. A mapped guardian re-exec was necessary because a fork inherited
the original memory-namespace ownership and blocked inspection of nondumpable
children. Executable/loader identities and carried descriptors are bound, but
the diagnostic host runtime remains explicitly `HOST_RUNTIME_UNSEALED`.

The projection contains one fixed 341-byte static x86-64 ELF, SHA256
`4e4042014c2ed77edea2140f0b1a85a153510fcc53b05d480d79aa5419780a91`.
It has no interpreter/dynamic segment, forks, file/network operations or provider.
It was constructed and inspected, **never executed**. The measured 64KiB/eight-
inode leaf is read-only/nosuid/nodev and executable; writes fail with EROFS.
Both staging parent and leaf are namespace-local tmpfs mounts. Exact inode,
device, mount and held-FD identity bind the file and teardown.

Four abrupt-death probes cover init/supervisor SIGKILL before/after attachment;
all owned processes became terminal and no host path remained. An owned mount
moved after attachment was detached by its held identity. A foreign same-content
bind mount was refused and preserved, producing CLEANUP_FAILED; its disposable
namespace then exited. Empty/unterminated mount evidence now refuses rather
than claiming cleanup. Integrated happy-path cleanup succeeded.

These component runs have null commit/tree and are not hosted source-bound
results. They always report `HOST_CAPABILITY=FAIL`, `WITHHELD`, provider_calls0,
payload_processes0 and execution_authority=false. Projection is
`MATERIALIZATION_CHECK_ONLY`; sentinel_exec and cgroup remain `NOT_RUN`.
Projection is cleaned before returning evidence, not retained for later use.

Native cgroup2 is mounted read/write with controllers available, but UID1000 has
no writable delegation; root/procs/subtree-control ownership does not authorize
this launcher to create/enroll an owned child. No sudo or connected user-systemd
manager exists. This is not a claim that the kernel lacks cgroups. No host
ownership/controller/LSM/sysctl change, root broker or Docker-based privilege
workaround was introduced. The command sandbox has additional ownership/UID-map
restrictions; it is distinct from native Cloud and from the unmeasured GitHub VM.

## Review and remediation

Internal read-only Codex agents inspected actual source/diffs and evidence:
`acceptance_path` (namespace/projection ownership, cancellation and cleanup),
`inspect_workflow` (workflow/source authority, exact populations, static ELF),
and `recovery_review` (two application fixes and business audit).

Material projection findings were fixed: namespace-local staging replaces a
host directory; exact original file inode/device/mount defeats same-content
foreign replacement; held mount-ID cleanup handles moved owned mounts;
missing/truncated mount evidence cannot prove absence. Capability pre-ownership
prctl/mask/cancellation errors now produce canonical refusal. Tests and native
fault injection substantiate these repairs. Final post-full review re-anchored all 19 material files to the frozen
source/evidence and found no remaining in-scope P0/P1/P2. The failed required
suite and incomplete host capability remain blocking gates. These are internal same-model-family reviews, not independent
external/Claude/Cursor review or owner business-rule acceptance.

The application fixes are limited to rejecting noncanonical base64url signature
aliases and treating ENOENT/ESRCH proc-stat disappearance as absence. Permission
and malformed records still fail closed. One-time nonce handling already blocked
replay of signature aliases; no replay bypass is alleged. The original browser
worker, acceptance implementation and exact-EPERM test are unchanged.

## Continuation and setup

Task2 remains incomplete: implement owned cgroup limits/stopped enrollment/
exact membership/zero-OOM attestation, actual sentinel exec and finally the
fabricated43-byte one-shot release only after every same-generation check.
Those implementations and a usable authorized launcher path are both missing.
Task3 cannot PASS yet; Tasks4–9 remain NOT_RUN. Do not start payloads or weaken
checks to turn component success into authority.

Dependency manifests and lockfiles are unchanged. The Install script uses
frozen uv/pnpm dependencies and exact existing PostgreSQL image tools. The Start
skill runs only basic Git/tool/import checks; it imports18 modules and does not
automatically start any service, database, browser, full suite or research run.
The environment settings are a saved draft, not proof of publication or fresh
restoration. Local-only commit restoration is not reliable pending GitHub
publication. Preserve the local repository and recovery bundle.

The [business-readiness matrix](BUSINESS_READINESS_2026-10-17.md) separates
historical corrected-V3 coverage from current October facts and supplier policy.
Railway remains NOT_PROVEN, real purchasing blocked. November9 delivery of a
hosted synthetic workflow is conditional; no verified completion date exists,
and real operations by that date remain at risk. No production action occurred.

## Final cleanup and Install verification

The final suite removed its owned synthetic PostgreSQL container and its external
subreaper reaped154 test descendants. Native read-only inventory found zero Docker
containers, live database/browser/application test processes, stopped database/
browser entries, temporary database directories, projection paths or traversal
errors. No temporary test credentials or running resources are retained.

The exact reusable Install script was rerun successfully after the suite. Frozen
uv/pnpm installation, lock compatibility, all18 imports, JavaScript syntax,
TypeScript/Chromium and PostgreSQL tool checks passed. Its version-only and
never-started extraction containers were removed. Active versions are Python3.13.5,
uv0.12.3, Node24.19.0, pnpm10.34.7, TypeScript5.9.3, Docker28.4.0, Git2.52.0,
gh2.46.0, PostgreSQL16.14 and Chromium154.0.8037.57.

## Frozen source SHA256

| File | SHA256 |
| --- | --- |
| `procurement/tools/hosted_synthetic_source.py` | `2719c96073bf9391e4fe1a924beb150c98fe3ebb5d9e717af3c02a1d259a2a96` |
| `procurement/tools/hosted_synthetic_capability.py` | `06d1df6ea54b711d0cc660d7a3618114b9bea9e949017f7e5c69cf8719152a7f` |
| `procurement/tools/hosted_synthetic_namespace.py` | `1ad1739e2a51ecb3fc5fd1059d9fb30564571f96b8917306de50ac87ed5d3e09` |
| `procurement/tools/hosted_synthetic_projection.py` | `a7dd7716851985c0897e6ed9c7e40e2ddff0a146e1b8a65ab143c0afe100d64b` |
| `procurement/tests/test_hosted_synthetic_namespace.py` | `0ff4003681ddcaa590ef0e3f0ff44f50f2d3970355be0842ac3922c1caab6bce` |
| `procurement/tests/test_hosted_synthetic_projection.py` | `d955237b21b436715b890fe14cf19740d40b156bf435690238b983b639dda7f1` |
| `.github/workflows/hosted-synthetic-acceptance.yml` | `ea05e42dda07cea0228d7c210c7e4837ae4b12b546a59114d9c371d5dae6ec6a` |
| `procurement/src/procurement_os/staging_access.py` | `686fd551cc65b6d2d6b50c40a871bd5b131ec5ee946332d7ba636eb282649589` |
| `procurement/src/procurement_os/staging_supervisor.py` | `1734e3a3c186609304e4034d18b95143c5b04150af141d790ab839e3059921eb` |

## Local evidence SHA256

Logs are under `/workspace/.cloud-setup/buffalo/logs/`.

| Log | SHA256 |
| --- | --- |
| `cloud-authoritative-reexec.log` | `4d30a08cf0a79b63b56f695d971479154ade6d135b136d95d8ff94b0631a10c7` |
| `cloud-authoritative-projection.log` | `80f246b2fdb536536e13708eda02f36e3b6259a8bfd1b1b6d9165b4fc90b1478` |
| `cloud-authoritative-projection-final.log` | `09c07b102a4e2a34e2b7f533c8494ca1133f96c92843d7619bb66d043f7dfeac` |
| `projection-final-focused.log` | `da1ce9af548890e35d0fd7a0f33d97eb18c61357b607272768a916c5a9c3bc9a` |
| `projection-final-isolated.log` | `7b0af305ee8b09b6c36e2e34a4a8599f767237fbf22159bad40c1921ca411fce` |
| `projection-final-native.log` | `864137b5cdd483a184cced5a7d1fdfafd58eb6cf554567921a662434956daaba` |
| `projection-final-death.log` | `743e10f61f4621cfde96938791ee3cd79c69105bdef63d4db42de1ccfbc84e61` |
| `projection-final-faults.log` | `84446bc3b732c99fd1e2cabb19b096cca5ab2a7ce4fa39ff537040d0a4fd6641` |
| `startup-projection-final.log` | `684a2c8e6282975a75626fe0e40afece3ca25536f07857ead4ceac8335d22b74` |
| `projection-final-cleanup.json` | `3361939ac322d4f2e9c3c786900561f47be4076031034fd3fd5e4d8a34f33402` |
| `install-projection-final.log` | `d805a8c5b97ae77c7ce7beca2b35a4460846ff216a1cc195fa99e20d2d4aad18` |
