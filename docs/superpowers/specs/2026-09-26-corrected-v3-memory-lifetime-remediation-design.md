# Corrected V3 Memory-Lifetime Remediation Design

**Approved:** 2026-09-26
**Risk:** Level 3 — forecasting/research pipeline reliability
**Scope:** memory lifetime and replay sequencing only
**Starting confirmed checkpoint:** `85f454aaef1be5c7d6d2cb1262d540b1901c8b04`

## 1. Objective and boundary

Make the corrected-V3 build complete its existing publication and full
semantic readback inside the bounded host memory envelope. The remediation is
behaviorally transparent: it changes object lifetime and verification order,
not forecast math, model selection, FVA, evidence meaning, schemas, identities,
business rules, purchasing boundaries, or authority.

The failed Build 1 workspace is materialized but unconfirmed. It remains
preserved as failure evidence and is never promoted or reused as the final
workspace. A successful build uses a fresh implementation lineage and therefore
receives fresh corrected input, projection, and workspace identities.

## 2. Established failure mechanism

Build 1 published its manifest and four artifacts, then entered the nested
full-reader path while the publisher still retained the parent bundles,
corrected projection, and all rendered artifact bytes. The reader added another
large serialized projection, parsed graph, parent replay, corrected replay, and
expected artifact set. Legacy verified-source registries also retained sealed
objects and their canonical bytes indefinitely. The cgroup killed the process
during that post-publication overlap.

The failure is a lifetime/retention defect. It is not evidence of a forecast
calculation defect.

## 3. Capability lifetime

Legacy verified-source registrations become weak-lifetime identity records.
While a registered object is live, validation still requires:

- the exact object identity;
- the exact registered result/proof;
- an exact comparison with its registered canonical bytes; and
- the same typed fail-closed behavior for unregistered or mutated values.

Weak collection is only a retention safeguard. Correctness never assumes that
the garbage collector ran. An explicit release operation removes an owned
registration at the lifecycle boundary, verifies that the caller is releasing
the same object, and is idempotent only for that already-released object.

Legacy V1/V2/V3 public validation behavior and valid output bytes remain
unchanged.

## 4. Deterministic build phases

The corrected build is divided into non-overlapping phases:

1. **Source validation and construction.** Read and validate the accepted
   parent, corrected input, and delta; construct the corrected projection once.
2. **Publication.** Render and publish the four immutable artifacts and the
   manifest. The publisher returns only the small manifest/identity result.
3. **Explicit release.** In a `finally`-safe boundary, release owned capability
   registrations and delete all parent/source/workspace/projection/artifact
   references that semantic readback can reopen from immutable storage.
   `gc.collect()` may return allocator pages but is not the correctness
   mechanism.
4. **Independent readback.** Reopen the published workspace and perform the
   existing full semantic replay after the build phase has returned and its
   heavyweight locals are unreachable.
5. **Post-snapshot.** Rehash the immutable accepted parent and require exact
   pre/post equality before returning success.

The CLI retains only the small delta/input/build result fields needed for its
aggregate response. It releases the initial parent bundle before invoking the
workspace build.

Exception paths run the same owned-capability release and reference teardown.
No exception is converted into success, and no partial publication becomes an
accepted workspace merely because a manifest exists.

## 5. Sequential readback and comparison

The corrected semantic reader first authenticates the manifest, source tuple,
lineage, parent, corrected input, and rebuilt projection. Artifact verification
then proceeds one artifact at a time in the manifest's exact registered order:

1. load one immutable artifact;
2. validate its exact path, size, SHA-256, and canonical/rendered bytes;
3. retain only the artifact bytes required by the public reader's return
   contract;
4. release the temporary expected bytes and comparison structures; and
5. continue to the next artifact.

For `projection.json`, semantic equivalence is established by the already
authenticated rebuilt projection and exact canonical artifact bytes. The
reader does not parse a second equivalent full projection graph while the
rebuilt projection is live. This removes duplicate graph residency without
replacing any semantic check with a hash-only approximation.

The builder may use a compact readback result containing the verified manifest
and identities. The application reader continues to return the validated
artifact bytes required by the existing viewer contract.

## 6. Preserved controls

The remediation preserves every existing:

- source, input, projection, sidecar, artifact, manifest, and workspace hash;
- accepted-parent pre/post snapshot and immutable artifact check;
- corrected semantic replay and joint-horizon evidence rederivation;
- lineage and clean-tree binding;
- exact tuple dispatch and mixed/unknown refusal;
- tamper, mode, owner, path, schema, and canonical-byte refusal;
- deterministic reproduction check;
- zero-authority and private-data boundary; and
- V1/V2/V3 compatibility behavior.

No valid forecast, coverage, owner-row, sidecar, or artifact content may change
except where the fresh implementation lineage is an explicit identity input.

## 7. Regression evidence

Deterministic tests cover:

- weak registry collection and explicit release independently;
- mutation/unregistered/foreign-object refusal;
- no canonical-byte copy retained after release;
- the release boundary preceding semantic readback;
- parent and build graphs absent from the readback phase;
- one corrected projection construction per build phase;
- sequential artifact comparison and temporary release;
- exception-path release;
- stable registry cardinality across repeated builds/restarts;
- tampered artifact, hash, lineage, semantic, and accepted-parent refusal;
- deterministic readback and restart;
- unchanged legacy contract behavior and byte goldens; and
- unchanged joint-forecast/coherence results.

The registered suite population and independent population assertions are
updated only from actual discovery after the final test inventory freezes.

## 8. Machine acceptance

Validation follows the owner-approved order: baseline identity, focused
lifecycle tests, registry tests, replay/tamper/lineage tests, forecast
regressions, corrected focused suite, then one instrumented real build.

The real build must satisfy all of these hard gates:

- process peak RSS strictly below 5 GiB;
- cgroup peak memory strictly below 6 GiB;
- zero OOM events;
- zero OOM kills;
- no exit 137; and
- successful full semantic readback.

After that gate, a deterministic second build/readback, aggregate corrected
controls, the authoritative repository suite, startup/static/secret/private
checks, and fresh initial/restart browser acceptance must pass. Expected
corrected controls remain 1,365 CALCULATED, 644 NOT_APPLICABLE, zero BLOCKED,
zero NOT_PROCESSED, zero corrected point violations, and zero corrected target
violations, with the exact existing membership and historical controls.

## 9. Stop boundary

Stop after the memory remediation, fresh corrected artifacts, complete machine
validation, available read-only review, handoff, and bounded delivery. Do not
begin ABC, supplier/price/pack approval, trusted incoming, open-PO netting,
economics, recommendations, DRAFTs, POs, orders, deployment, Shopify writes, or
production activation.
