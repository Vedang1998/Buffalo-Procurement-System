# November 3 retained-input and provenance continuation

Implementation `216169bfe82a4275ac78fcb54a716d400fe564c8`, tree
`26ac6ec1743769085ea556a2392098bf63496e0c`, descends from published00a428b
and review target3ed8222. No reset, history rewrite or accepted-input repinning.
This packet is not installation, runtime, browser or purchasing acceptance.

## Actual retention result and access blocker

**Railway objects written:0. Remote bytes verified:0. Fresh bucket readback:NOT_RUN.**
The owner authorized only object retention in project
5bdb474a-4af8-4187-b1be-be45594a22b2 / environment
5d8f28f5-168c-4eb9-8eef-6c639ae4cf46 / bucket
9d7e0ef1-e776-401d-8629-c407da96e3f0, display name
migration-private-artifacts, under `native-runtime-evidence/`, up to209715200B.
Ordinary existing-plan bucket usage is authorized; no plan/spending-limit change,
new bucket/service, lifecycle edit, public access, credential reset or deletion.
The production environment name grants no service/database authority.

The IDs match the owner instruction and historical repository design. **They
have not been verified through the authenticated Railway control plane.** No
Railway/S3 connector is callable, Railway CLI/config is absent, rclone auth config
is absent, and both provided AWS config/credential files are empty. No S3 endpoint
or credentials are supplied. This is an access blocker, not missing spending or
object-retention authorization. No secret values were requested or printed.
No fallback to other credentials, service variables or unrelated prefixes occurred.

The exact66artifacts175344809B were reacquired, complete-hash verified, and left
in disposable local SHA-addressed staging. The prior task cache did not survive;
that is direct evidence why a disposable cache is not durable retention. All67
public fetches (66artifacts plus3607B keyring) succeeded once each, in10.61s with
maximum4concurrent requests. No downloaded executable ran. New public metadata
and receipts form a locally verified transfer package:

-81payload objects179257086B, plus20682B manifest =82objects179277768B;
-30437432B below the200MiB storage ceiling;
-manifest SHA256d8ce40daf1d41cc472aa78fb5dfaa6428887a1bd9d9249658badcebce57dfbe1;
-all keys are `native-runtime-evidence/sha256/<sha256>`;
-status **INCOMPLETE_FOR_INSTALLATION**.

The exact keys, sizes, hashes and roles are in nov3-retention-package.json.
These are planned keys, not claimed uploaded objects. The local receipt verifies
all complete input bytes, single-link regular files, no-follow opens and stable
file identity. Artifact payloads and newly fetched raw candidate metadata are not
committed as a vendor collection or used as an alternative durable repository.
Git retains code, small manifests and verification receipts. Public payloads are
available locally for the authorized transfer, without a persistence guarantee.

When authenticated transfer is restored, first verify the exact project,
environment, bucket and prefix. Use conditional creation (`If-None-Match: *` or a
supported equivalent with the same no-overwrite guarantee). Existing objects may
be reused only after independent complete size/SHA verification; a conflict stops
writes. No unconditional replacement or deletion is permitted. Keep any bucket-
wide credentials solely in the trusted transfer context; do not claim prefix
scoping or pass them to acceptance processes. No credentials/signed URLs may enter
logs, Git or reusable snapshots.

Read every retained object back through a fresh isolated request/download context
and empty directory, with no upload-cache reuse. Check complete bytes and SHA256,
not ETag/metadata alone. Record exact remote object inventory, manifest identity,
aggregate retained bytes and exact local cleanup. Object lock/WORM is **not
verified or claimed**. Retain objects until separately authorized deletion; no
automatic job or bucket-wide lifecycle change is authorized.

## Exact-index recovery and authenticated replacement candidate

Original expected updates index remains
8e3c832aed4bc99e14b0e501f578cd95db6bae0e6a435d46f292048696852c25 /1406168B.
Its original404 evidence is preserved. New bounded direct snapshot `Packages.xz`
paths (not the prior by-hash requests) failed transport; this does not prove those
snapshots lack the object. No unchanged404 retry, reconstruction or silent pin
replacement occurred.

The separately prepared **metadata-only replacement candidate** is real:

| Object | SHA256 | Bytes |
|---|---|---:|
| Signed InRelease |5f4cbfd1daf09c5c6af3eeb67fe923b15010f28d9189b64995e2c0f68eec7b77|126125|
| Verified signed payload |a79193f3fbeb3d71cd949a59de3e99b384e8e2b03d73538ed79a696b5aab2940|125243|
| Packages.xz |4292c6f61b84c8685c73f6db585fce9974f293a0e62fb070a4c7a2572e333996|1407612|
| Decoded Packages |3047f3f8a2ed65735ad0821d8c4bb5f7afd1720737abff19d0c11d3064561d30|7615549|

Publisher Release date: Tue,03Nov2026 12:40:01UTC. The exact existing keyring
80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31 /3607B
verified signer F6ECB3762474EDA9D21B7022871920D1991BC93C. Installed gpg2.4.7
performed signature verification with sealed readonly key/release descriptors
and an isolated temporary home; the home and descriptors were cleaned. This is
actual signature math and payload verification, not target gpgv execution or an
independent Claude review. No new trust-root acceptance is inferred.

The signed payload binds the exact replacement compressed index hash/size. The
complete index has7047records. **All47unchanged package/version/architecture/
filename/SHA256/size tuples match, and all47entire package-stanza hashes match the
existing accepted package_record_sha256 values.** No missing, conflicting or
other selected version was substituted. The real complete-index parser passed
with prospective index fields changed in memory only, preserving accepted source.
All66artifact versions/hashes and832selected member/file hashes remain unchanged.
See nov3-replacement-metadata-candidate.json and nov3-candidate-route-record-verification.json.

Proposed adoption changes only the47updates records' signed-release/index metadata
identities and explicit snapshot-policy reference to these exact November3bytes.
The18noble records and Python publisher-digest assurance remain unchanged. Owner
adoption is **NOT_GRANTED**. The current code still expects the original8e3c
index and refuses without it. No source pins or original evidence were overwritten.
The existing trust-root, pinned-snapshot freshness and Python signed-build-
attestation limitations remain explicit in the consolidated installation decision.
Python's build attestation remains NOT_ESTABLISHED.

## Narrow implementation delta and validation

The dormant verifier retains the raw InRelease SHA check and gpgv signature/signer
checks. It now requests gpgv's signed text with `--output -`, sends status to fd2,
requires a single expected VALIDSIG with no contradictory failure status, and
checks emitted bytes against the existing verified_release_sha256 pin. Only that
payload reaches index-binding parsing. Raw armor or unsigned surrounding text
cannot supply a binding. Status remains bounded4096B; payload uses the existing
2MiB release bound. Runtime capacity/memory, work60s and independent cleanup5s are
unchanged. M1 before account remains mandatory. No activation wiring was enabled.

Six causal tests cover valid payload, wrong/empty/oversized/drifted output,
wrong/missing/duplicate/contradictory signatures, unsigned outer index text, raw
hash refusal before verifier effects, conflicting payload pins and failure cleanup.
Existing signature-component mocks remain explicitly synthetic. Real gpg emitted
both original retained releases' already-pinned payload hashes; these receipts
are separate from the target gpgv command, which is unavailable here and NOT_RUN.
The maintained upstream gpgv documentation supports the selected output flags.

Frozen validation:1235/1235focused,298/298scrubbed components,88/88related and
10/10startup, with zero failures/errors/skips in these groups. Groups overlap.
Discovery2799/137modules executes zero tests. No full authoritative suite or known
unchanged native Cloud refusal was rerun. Full static composition is152245797B /
976entries, below167772160B. Capacity accounting is not native memory or timing
proof. No new target installation/cleanup/service measurement was performed;
November1component timing retains its original source/scope and is not promoted.
Application source, lockfiles and canonical PHASE_STATUS are unchanged.

## External review and single decision status

The owner references a complete Claude review of3ed8222, but its report text,
exact dispositions and attribution were not included in this task message or
found in repository/shared evidence. One request for the missing text was made;
no duplicate broad review was run. **No findings are closed or attributed by
inference.** The latest complete recorded report remains de4eda5; its scoped
closures/limitations remain preserved. The3ed review reference is recorded as
REPORT_TEXT_NOT_AVAILABLE, not as a fabricated verdict. The owner-mentioned
signed-payload improvement is implemented without inventing a finding number.

When the report arrives, record it verbatim with exact scope and reconcile its
concrete dispositions. The new material delta requiring targeted Claude review
is the signed-payload binding/parser behavior and affected provenance gate;
proposed replacement metadata and retention receipts accompany it. No unchanged
architecture re-review is requested, and no callable Claude invocation is claimed.

One consolidated installation decision remains **NOT_READY_FOR_ACTIVATION**:
actual durable retention/fresh bucket readback is missing; exact3ed report and
applicable new delta review remain unavailable/pending; owner must explicitly
adopt the exact replacement metadata/snapshot policy rather than accepted pins
changing silently. Existing object-retention authority needs no repeat approval.
Installation, privileged runtime activation, credential release, browser,
deployment, database/private-data access, Shopify, pricing/purchasing and main/
PR24/merge remain disabled or unauthorized. No future memory/timing result can be
approved as already passing.

Next executable action: restore an authenticated trusted transfer context for the
already-authorized exact bucket; verify identity, conditionally retain this82object
manifest, independently download/verify every object and record cleanup. In
parallel supply the missing3ed report and obtain only the applicable narrow delta
review. All currently executable independent provenance/implementation work is
complete; the remaining transfer/review/adoption gates require unavailable access
or input. No automatic resumption is promised.
