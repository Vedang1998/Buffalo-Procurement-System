# Claude Code review package — NOT YET REVIEWED

Independent reviewer: Claude Code, read-only in a separate environment. Codex is
the sole implementation writer. No Claude execution is available in this writer
environment; internal Codex static reviews do not satisfy independent review.

## Exact new delta

Repository: Vedang1998/Buffalo-Procurement-System
Branch: codex/hosted-synthetic-acceptance
Review base: `010aef5baf02f1c9bc038583f59cb314d6e8ee97`
Base tree: `42228c6217d1d51f1bb15da231d508cac0399703`
Material: **`b35c036d06b5db495cec297a6b928417b82cffae`**
Material tree: **`f7162be46d70caa70adc0507f39dedc0275dfeb2`**

The earlier 3c1ae555 frozen review does not cover this new native bootstrap delta.
The later evidence descendant changes documentation only and explicitly skips CI;
tests belong to the exact material above. Publication used an ordinary branch-only
fast-forward, followed by a fresh fetch verifying both commit and tree. Protected
main, PR24 and recovery refs were unchanged. No force push or original private
email metadata was published.

Read AGENTS.md, the canonical specification/rules, PROJECT_GOVERNANCE.md,
CODEX_HANDOFF.md, PHASE_STATUS.md, hosted plan/design, and
`docs/EMPTY_GROUPS_BOOTSTRAP_2026-10-26.md`. Inspect the actual source diff:

```bash
git diff 010aef5baf02f1c9bc038583f59cb314d6e8ee97 b35c036d06b5db495cec297a6b928417b82cffae -- .github/workflows/hosted-synthetic-acceptance.yml procurement/tools procurement/tests
```

Do not edit the writer checkout, dispatch/rerun workflows, invoke sudo, call
`run_native_plan`, create services/resources, release credentials or run browsers,
production, provider or purchasing actions during this read-only review. Do not
repeat the known native-Cloud full-suite pg_dump refusal. The owner has approved
the provisioning contract; independent review before privileged execution remains
mandatory, and cannot be supplied by a local PASS flag or inferred from tests.

## Evidence and exact scope

`manifest.json` binds byte lengths, SHA256 and transformations. The frozen logs
are unchanged bytes with `.txt` extension. The validation ledger records clean
exact HEAD and all changed-file hashes before/after tests. Source identities cover
all 15 launcher contract files. Dependency identities bind the unchanged manifests
and lockfiles. No application business source or migrations changed.

- Frozen material: hosted **272/272**, related **88/88**, startup **10/10** pass;
  no skips, expected failures or unexpected successes. Imports **18/18** pass.
- Discovery **1,799 in 99 modules**, floor 1,799, no registration/minimum errors.
  Discovery does not execute tests or establish a full-suite PASS.
- Exact GitHub run40384581205/job121789355348: focused step success, then review
  gate failure with public exit78 annotation, overall FAILURE. The workflow's
  exact272/no-skip assertion binds the successful step; public per-test logs and
  gate stdout require sign-in and were not read. No live service path is reached.
  See `bootstrap-hosted-observation.json` for attribution and scope.
- Complete supported-candidate authoritative suite: still required, NOT_RUN.
  Historical df147b2 full native failure and container 50/50 remain separately
  source-attributed in the preceding review package/handoff.
- The SIGINT test delivers an actual deferred signal but simulates native service
  execution and cleanup observations. Transport tests spawn only ordinary
  unprivileged Python children. They do not prove privileged cleanup or cgroups.
- `bootstrap-inert-native-plan.json` is inert serialization data. Its UID/GID,
  namespace, nonce and paths are examples, not observations or an executable
  approved host configuration. Never execute it as a copied recipe.
- `bootstrap-native-source-references.json` records public upstream source hashes.
  It is not installed systemd/util-linux/kernel/package attestation.
- `bootstrap-internal-review.json` attributes repaired internal findings and
  exclusions. Neither reviewer was Claude, and neither reran root's frozen tests.
- Cleanup inventory found no live application/DB/browser, container, temporary
  database or projection. Eight old PID1 zombies persist; six unattributed old
  volumes remain untouched. No test credential was retained or added to config.

## Adversarial review targets

1. Correct systemd v255 `ExecStartEx=:!` property serialization, native-only root
   command prefix, oneshot ordering, ownership/delegation, NNP and three-capability
   ceiling. Confirm no shell/repository Python/application executes as root.
2. Actual util-linux ordering: first clear groups, then IDs and capabilities;
   kernel exec transition clears effective/permitted privileges before Python.
   All five capability sets must read zero in the host supervisor. Check installed
   binary/package/version and descriptor/environment assumptions before activation.
3. Full same-process readback before child dispatch and Git: real/effective/saved/
   fs IDs, empty groups, NNP, capabilities, initial user namespace and exact nonce
   service membership. Post-drop file/control access must precede any writes.
4. Exact source/dependency/native PostgreSQL trust, owned cgroup path/identity and
   limits. Refuse absent ancestor controllers instead of enabling them. Existing
   namespace/enrollment/retained I/W/release controls must not silently relax.
5. Client/manager timeout and cancellation semantics, partial setup failures,
   stdout/stderr bounds, fd/pid ownership and cleanup precedence. Signaling the
   actual privileged sudo monitor is NOT proved by unprivileged transport tests.
   Read-only unit absence cannot disprove later creation after lost acknowledgment.
   Review cleanup under cancellation and late manager replies before live use.
6. Same-UID pathname race and exclusive delegation remain unresolved. No sealing,
   containment, runtime closure, sentinel execution or credential authority may be
   inferred from this component or its canonical FAIL report.
7. Unconditional CLI/workflow exit78 must remain closed until actual review is
   recorded. Review the eventual activation wiring as well; the dormant function
   does not grant itself execution authority.

Return actual reviewer identity/tool/version, exact reviewed commit/tree, findings
ranked P0/P1/P2 with file/line and a concrete failure scenario, missing causal tests,
and explicit judgment on credential-free native proof readiness. Codex addresses
findings and reruns applicable tests. Cursor is not requested for this host-only
delta; targeted business/database review remains separate if later warranted.

## After review

The next executable action is the independent read-only review above. After
findings are resolved and activation wiring is reviewed, Codex performs one bounded
credential-free proof on the same ephemeral GitHub VM: native identity/package
binding, actual post-drop authority/access, cgroup ownership/membership/limits/OOM,
stopped enrollment, cancellation, partial setup and cleanup. No second owner
approval is needed merely because this bootstrap passes.

Then continue retained sentinel cleanup, runtime/release gates and the exact
operator VALIDATED → authenticated browser confirmation → genuine Backup V2 and
binding → APPLY → review/edit/reject → vendor DRAFT/packet → restart/restore flow.
Controls remain two DRAFTs, three lines, 14 members, $282 + $7 = $289. All these
integrated gates are currently NOT_RUN, credential release WITHHELD, runtime
UNSEALED, Railway NOT_PROVEN. Execute the full authoritative suite on the supported
candidate as its prerequisites pass. No production, November activation, mapped
offer cutover, private-data, Shopify, real purchasing, deployment, main/PR24,
owner-Mac or Replit operation is authorized by this package.
