# November 1 T1 / publisher-provenance candidate

Implementation candidate: `3ed8222244cbcac4b982a0116db566fe07839d16`, tree
`3c51022c717f730d9d63d7042c4edc1db678ffc0`.
Review baseline: `de4eda5fb6d7a8a586f415451dcb2ddc055536e4`, tree
`87e35b1f4412c82c9a236801971a26157d3e6996`.
This is an installation/measurement implementation candidate with explicit missing
provenance and retention evidence. It is **not installation-ready or activated**.

The complete received Claude report and prohibited-attempt disclosure are in
[external-review-de4eda5-owner-report.md](external-review-de4eda5-owner-report.md).
Claude2.1.316, session_01JyrJEUAi36GJ6AfNcmBBxV, reported configured/served
claude-opus-5-5/high. REQUEST CHANGES remains the latest external verdict.
H1 static graph and H3 code were closed there; H2 was repaired with READY-digest
P3. M1/N7 remain closed at34e8 within their historical scope. Writer changes below
are pending Claude delta review. The reviewer's20sudo no-ops and blocked second
attempt are excluded from acceptance evidence. Future review must run no sudo,
including no-op commands. Internal Codex path checks do not close external gates.

## Implemented delta and exact route

`hosted_synthetic_native_route.installation_measurement_entry(arguments, artifact_directory)`
→ unconditional installation decision refusal → scoped `_run` (dormant)
→ protected trust root and signed releases → exact compressed indexes and records
→ complete artifact hashes before effects → native platform observation
→ `InstallationLease._create` → per-artifact selected-member stage and import
→ `finish` → protected measurement child → typed validated NativePhaseReadiness
→ M1 before AccountLease.create → same-view fixed PostgreSQL service
→ consumer/identity drain → exact owned installation cleanup.

There is no CLI/workflow activation caller. `installation_measurement_entry`,
`activate_installation`, `_NativeOps` and direct constructor gates remain closed.
No root repository Python, shell loop, unrestricted extractor, new broker or
shared-path permission repair was added.

T1 batches native stat/hash/chmod and same-depth directory creation, at most64
paths; argument caps70/32768B. Every output must match exact ordered input names,
digests and complete stat population. Streams remain exclusive/no-follow and
per-file; final files must be regular, root-owned, single-link and exact mode/size.
Only immediate parent metadata is refreshed after owned mutations; higher
ancestors retain their exact snapshots. Backing root remains0700. Pending mutation
stays sticky until every batch readback and parent acknowledgment succeeds.
Partial writes/modes, cancellation, replacements and incomplete output quarantine
the lease; they never become cleanup PASS. Known clean work expiry still permits
bounded exact cleanup. No deadline renewal was added.

Repeated full manifest decoding/graph derivation is now once per Stage. The
immutable selection is internally derived, not supplied by a caller. Complete
pinned bytes for all three inventory inputs are rechecked before and after every
stream. Both held-file hash passes, exact inode/mode/size/ownership checks and
terminal consumption remain. Drift cannot reuse a previously decoded selection.

The parent's independent canonical READY mapping digest must match the child's
READY frame before ACK, and the returned readiness READY digest must match that
observation. Final mappings may differ after legitimate imports; final mappings
still require authorization. Two snapshots are not continuous lifetime proof.

## Command population and measured costs

Actual full-manifest lease control flow with controlled native transport:

| Phase | de4/ebc baseline | Candidate |
|---|---:|---:|
| Create/directories | 5 | 45 |
| Import, hashes, modes, identities | 12,476 | 1,365 |
| Finish/verification | 189 | 23 |
| Cleanup | 114 | 114 |
| Total | 12,784 | 1,547 |

This is an87.9% reduction. The old review's approximately5000 was an estimate;
the retained trace counts actual repeated ancestor observations. The new import
contains832dd,76sha256sum,87chmod and370stat calls. Cleanup has64stat,25find,13rm
and12rmdir calls. Manifest917native entries plus the owned root/view prefixes
produce919owned objects (832files/87directories). Full find output64686B stays
within65536; largest hash batch13881B and hash argv9660B stay bounded.

Counts are simulated transport calls through actual lease code, not target
privileged execution. They exclude publisher gpgv, decoder processes, source
binding/rehearsal, ownership scanning, account/service commands and external
observer polling. View request/consumer reservation add their own root checks;
observer/polling counts are lifecycle-dependent and are not hidden in1547.

Unprivileged env→timeout→stat transport sampling:50calls,0.16856s total;
single-path median3.437682ms,64-path median3.523812ms on this Cloud. Multiplying
1547calls by those medians gives about5.32–5.45s;114cleanup calls about0.39–0.40s.
These are projections only: no sudo cost, full file IO, target scheduling or
service proof. The reviewer separately reported8.07ms on another host; that is
not a same-host pipeline measurement and is not combined into a fabricated PASS.

Actual selected-artifact, unprivileged decode/stage/read measurement:

| Component | Before stream optimization | Frozen material7a106c1 |
|---|---:|---:|
| Whole component including cleanup |31.063369s|10.513303s|
| Verified-stream consumption |25.452848s|4.647992s|
| Extraction/staging |2.289111s|2.214620s|
|198complete artifact hash passes |0.397630s|0.385948s|
| Stage cleanup |0.062569s|0.062536s|

All66artifacts175344809B,831selected archive members and832logical streams
149567173B completed under one60s allowance. Other inventory/control overhead is
included in total, not silently attributed to hashes. Descriptor identities
matched and all owned stage directories were removed. Native binaries were not
executed. Concurrent frozen unit tests may have affected CPU time.

The unoptimized measurement's component files were stable; a concurrent
run_tests.py edit caused its reporting drift guard to exit1. That fact is retained.
The optimized measurement has stable HEAD/tree and all observed tool/JSON hashes.
An earlier reporting-harness failure is retained as NOT_RECORDED, not a component
PASS. No unchanged failed component was repeatedly attempted.

Optimized self RSS high-water138648KiB (141975552B). Waited-child high-water may
include inherited/pre-exec pages; it is not additive or an exec-only value.
Allocation census, page-cache/memfd charges, cgroup usage and native M1/service
peaks were not measured. This is not256MiB child or512MiB aggregate fit proof.
Candidate3ed8222 differs from measured7a106c1 only in two global test-population
constants; exact runtime/source-input blob equality is recorded separately.
Tests are attributed to their actually executed commits.

The remaining60s costs—native privilege overhead, real protected M1 capture,
source rehearsal, full ownership scan, account and service work—remain unmeasured
on the target. Cleanup114calls must still fit its separate fixed5s. Optimization
removes the demonstrated per-file parsing/command amplification; it does not
promise the entire route fits. No phase-budget increase is proposed or applied.

## Provenance recovery and installation gate

Exact pinned noble-updates index:
`8e3c832aed4bc99e14b0e501f578cd95db6bae0e6a435d46f292048696852c25`,1406168B.
Publisher by-hash returned404. Two bounded official snapshot requests had
transport errors; they do not establish absence at those snapshots. Bounded
retained-cache/reachable-Git searches found no copy; one apt cache directory was
inaccessible. No exhaustive host-absence claim, reconstructed index, changed pin,
current-index substitution or package refresh is made.

Exact noble index retained in Git:
[noble-2a6a199e-Packages.xz](noble-2a6a199e-Packages.xz),1401160B,
SHA256`2a6a199e1031a5c279cb346646d594993f35b1c03dd4a82aaa0323980dd92451`.
The real new parser reads6099records and verifies all18selected noble records,
exact version/architecture/filename/hash/size and stanza digest. Its disposable
copy and descriptors are cleaned. This new local test does not reproduce gpgv
signature verification; Claude's signed-release verification remains separately
attributed. Local gpgv is unavailable. No tool installation was attempted.

Both exact retained InRelease files still bind their exact index hash/size.
The new installation path requires that signature, exact compressed/decompressed
index hashes/sizes and unique matching package records, then hashes all66complete
artifacts **before InstallationLease creation**. XZ parsing is incremental,
64MiB decoding/output bounded,256KiB records,≤4streams, strict terminal/hash/schema
checks. The publisher's empty second XZ stream is supported; junk, truncation,
wrong/duplicate/conflicting selected records and version/artifact drift refuse.
Every selected archive member and destination remains separately verified.

All66artifact URLs currently retrieved exact pinned bytes175344809B,66/66verified.
This does not repair the missing signed-index→47updates-record links. The cache is
disposable, not durable evidence retention; URLs alone are not a recovered package.
No approved durable binary-storage destination/control was found. No bucket,
release, vendor-binary Git collection or spending increase was created. Only the
small exact public index and sanitized retrieval receipts were committed.
Python remains publisher HTTPS/GitHub digest assurance, signed build attestation
NOT_ESTABLISHED. Neither writer hashes nor a source-review PASS supply missing
publisher provenance.

## One consolidated installation decision — not ready to activate

The exact unchanged832-row/66-artifact manifest and owned effects remain in
[nov1-native-installation-decision.json](nov1-native-installation-decision.json),
manifest3923d1404aa5b780e4a026b33cca6570a1be0a7bcedded51c20da4dc8de4c070.
The successor decision must also resolve:

1. Recover and retain the exact8e3c832a index. If unavailable, the sole proposed
   alternative is an explicit **metadata-only authenticated rebaseline**: retained
   Ubuntu-signed publisher snapshot/index bytes proving all47unchanged package,
   version,architecture,filename,SHA256,size tuples. Keep every66artifact/file hash
   and version unchanged. Record exact replacement metadata identities before
   approval; none are currently verified or invented. Missing tuples refuse.
   This is not permission to silently select today's index or update packages.
2. Designate/authorize a durable content-addressed destination for exactly66blobs,
   175344809B, plus both exact indexes2807328B and small public trust metadata.
   Specify retention and read access, then prove fresh-session readback of every
   exact blob. No destination or storage spending is assumed authorized.
3. Retain the prior proposed pinned-snapshot freshness policy, protected keyring
   fingerprint, Python assurance limitation, normalized modes/logical aliases and
   excluded PGDG inputs as explicit owner decisions. No policy acceptance inferred.
4. Applicable Claude delta review must pass. Then one decision may enable only the
   exact bounded native effects and measurement route. Future target timing/memory
   results are unknown, remain fail-closed and cannot be approved as already PASS.

Effects remain one collision-checked root0700 `/run/buffalo-native-<32hex>` tree,
private readonly views, unprivileged per-artifact stages, existing dedicated
identity/system-manager/cgroup effects, ownership-bound consumer drain/removal.
No global permissions, apt upgrades/scripts or root application/repository code.
Work60s and cleanup5s, PG_PHASE167772160B, per-file/other-runtime83886080B,
entries3000, child268435456B/2tasks, aggregate536870912B/64tasks and namespace20s
are unchanged. M1 before account is mandatory; capacity FIT is not memory FIT.

## Coherent validation and remaining acceptance

Final source-bound validation is indexed in nov1-t1-provenance-evidence.json.
Focused1229/1229, scrubbed292/292, related88/88 and startup10/10 passed; groups
 overlap. Discovery2793/137modules executes zero tests. The previous7a106c1 related
run had86passes/2failures from stale2765population assertions; fixed to2793without
lowering any floor. Those failures and their original source attribution remain.
Full authoritative native suite, supported-host privilege/timing, selected
interpreter READY/final view, real native M1/PG execution, worker/control exclusion,
whole-service lifecycle and sentinel/release/browser/Backup/APPLY/DRAFT/packet/
restart/restore remain NOT_RUN or OPEN. Known unchanged native Cloud failures were
not replayed. Railway and commercial readiness are unchanged and unproven.

Complete source/generated/native accounting:152244619regular bytes/976entries,
15527541B PG_PHASE headroom; diagnostic81645321B, PG-only80524891B stay under80MiB.
This is exact inventory-derived accounting, not captured or executed bundle proof.
Application source, lockfiles and canonical PHASE_STATUS are unchanged.

## One targeted read-only Claude delta request

Review de4eda5..3ed8222, using this packet and the evidence manifest. Recheck T1
batching, partial-batch/foreign-replacement cleanup, Stage selection caching and
drift; the full provenance parser/refusals and still-missing index/retention;
parent READY-digest equality; impact on scoped H1/H2/H3/M1/N7 closures; and exact
dormant entry/activation effects. Do not repeat unchanged architecture review.
Return implementation dispositions separately from installation-decision readiness.
No sudo (including no-op), edits, pushes, workflow dispatch, installation, accounts,
services, permission changes, credentials, private data or production actions.
No callable Claude channel exists here; this request is prepared, not a claim
Claude has been invoked or approved this candidate.

Next executable action outside the writer's present access: deliver this one delta
to Claude and recover the exact pinned index/authorize the explicitly bounded
metadata-retention alternative in the consolidated decision. No installation or
measurement can bypass those gates. Independent implementation and available local
validation for this delta are complete; no automatic resumption is claimed.
