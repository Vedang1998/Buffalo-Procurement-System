# Hosted validation bridge — proposed amendment

**Status:** PROPOSED / NOT APPROVED / NOT IMPLEMENTED

**Applies to:** the owner-approved 2026-10-10 hosted synthetic acceptance
design and implementation plan. No business architecture or production scope
change is proposed.

## Verified dependency

The published acceptance branch is at
`99f265e9ab12dfcd970b3072a57d5f2c5163cc07`, tree
`c4beee642cb87f9b1c08f3c03d09111a0f635bc3`. Task 1 publication has happened.
The dedicated hosted workflow and positive capability probe do not yet exist.
The only existing workflow runs on `main` or PRs targeting `main`; neither
`main`, PR #24, nor a new PR may be used for this task.

Task 3 step 2 requires the full authoritative suite before step 5 publishes
the workflow. The October 17 continuation requires Linux acceptance to remain
on GitHub-hosted Linux. On the supported Mac Python 3.13 environment, discovery
of the preserved containment module fails on `fcntl.F_SEAL_SEAL`; executing
the authoritative suite locally cannot satisfy its zero-error/zero-skip gate.

Separately, the preserved live test
`test_browser_private_proc_guardian_is_stopped_mapped_and_residue_free`
requires the former host's exact `fsmount+legacy-mount / EPERM` observation.
A host on which that guardian mounts procfs successfully will return a
different result. This is a source-proven conditional portability issue,
not an observed GitHub failure: no acceptance-branch hosted run exists.

## Requested narrow change

1. Permit a reviewed, source-bound, **validation-only** workflow candidate to
   be published by normal fast-forward to the existing acceptance branch
   before the first full Linux result. Preserve exact local commits and
   parents; do not synthesize a different candidate through API file edits.
2. Keep push-only triggering on that exact branch, `contents: read`, pinned
   actions, full-history nonpersistent checkout, trusted metadata assertions,
   sanitized repository-process environments, bounded timeouts, disposable
   loopback PostgreSQL 16, and no artifacts/secrets/production resources.
   A validation-only suite job may use the existing suite's 20-minute timeout;
   the eventual capability job retains its approved ten-minute limit.
3. Run the complete unchanged suite and startup checks on the exact candidate
   first. Preserve all discovered counts, all failures, and every existing
   floor. No skipping, platform-pass substitution, weakened assertions, or
   inherited historical PASS is permitted.
4. If the exact live EPERM regression fails because procfs succeeds, stop and
   retain that result. Before any remediation, review a separate narrowly
   scoped deterministic kernel-denial test fixture that proves the same
   failure/cleanup behavior independently of ambient host restrictions.
   Approval of this bridge alone does **not** approve changing that test,
   altering its expectation, or substituting mocks for kernel acceptance.
5. Require full machine PASS and independent review/remediation before
   enabling the positive capability job. Require its actual source-bound
   hosted PASS before Tasks 4–9. Private research remains NOT RUN.

## Alternatives and consequences

Keeping the current order requires an already-authorized Linux validation
executor before workflow publication. None is established in this local
session, and reconnecting to Replit or making Mac a Linux workspace is
explicitly excluded. This proposal does not add either executor.

Publishing earlier changes the order of the machine-validation gate; it does
not close or remove that gate. The failure modes are an ungreen branch and a
retained hosted test failure. Main, PR #24, accepted artifacts, runtime,
production, Shopify, private-data transfer, and purchasing remain untouched.

The local source-binding component can continue under Task 2. Publication and
any legacy-regression change require resolution of the specific ordering and
portability boundaries above, rather than another approval of the original
synthetic objective.
