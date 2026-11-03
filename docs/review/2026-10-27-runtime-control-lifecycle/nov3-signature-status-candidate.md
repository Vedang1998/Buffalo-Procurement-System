# Signature-status repair: narrow delta and retention continuation

Tested/published implementation `fb2dc86a43330f13376369c59db815dd03eb4d70`, tree `4fd42f3c33ccd5abcb923239bbee6d2a02ae6373`.
Reviewed baseline `216169bfe82a4275ac78fcb54a716d400fe564c8`, tree
`26ac6ec1743769085ea556a2392098bf63496e0c`. The published evidence follow-up
has its own identity; no tests are attributed to that documentation-only tree.

The complete [received Claude report](external-review-216169bf-owner-report.md)
and [attribution/dispositions](external-review-216169bf.json) are retained. Signed
payload parsing is accepted at216169bf; all47 replacement tuples and complete
stanzas were independently verified there. The supplied same-session3ed mapping
supersedes the old missing-report note: T1 excess-work defect repaired (timing
unproven), stage cache closed, parser correct, H2 READY digest closed. No other
historical repair is reopened or extended automatically to changed descendants.

Signature-status P2 was OPEN at216169bf and is REPAIRED_PENDING_REVIEW here.
Exactly one GOODSIG and one VALIDSIG must identify the pinned full signing
fingerprint. GOODSIG accepts its documented16-hex long key ID or full40-hex
fingerprint; display names/primary-key hints cannot authorize another signer.
Missing/duplicate/malformed/mismatched pairs refuse. REVKEYSIG, KEYREVOKED,
EXPKEYSIG, EXPSIG, BADSIG, ERRSIG, NO_PUBKEY and FAILURE refuse in either order.
Benign information remains accepted, including KEYEXPIRED for an unused subkey.
Calendar/time fields are schema-checked without a wall-clock policy. Raw/payload
hash pins, sizes, return-code checks, seals, exact cleanup and full signer pin stay.
No caller override, historical-expiry bypass, refresh or accepted-input change.

Evidence and limits

- Before repair, real retained output plus each of four injected expiry/revocation
  statuses and missing GOODSIG reproduced acceptance. These are parser injections,
  not actual expired/revoked-key verification.
- Nine added registered test methods, including connected refusal before
  InstallationLease/AccountLease effects and exact metadata FD/home cleanup.
  Source-bound1244/1244 focused,307/307 scrubbed,88/88 related and10/10 startup
  passed, zero failures/errors/skips. Groups overlap; do not add them.
  Discovery2808/137modules executed0. Full authoritative suite NOT_RUN; unchanged
  native Cloud pg_dump/trust failures were not rerun as passing evidence.
- Actual local `/usr/bin/gpg`2.4.7 verified both original retained releases and the
  exact proposed replacement with sealed key/release FDs. Strict parser and signed
  index binding passed; replacement parser7047records/47matches passed. All owned
  temporary homes removed and descriptor inventory equal. No network required.
  `/usr/bin/gpgv` is absent here: current writer targetgpgv is NOT_RUN, separately
  from Claude's reported realgpgv execution at216169bf. Status tests do not prove
  current revocation freshness: upstreamgpgv documentation explicitly disclaims
  checking expired/revoked keys. Byte-pinned keyring cannot imply fresh revocations.
- Full phase static accounting: 152247532regular bytes /
  976entries including source/generated content.
  Capacity fits160MiB; this is not capture, memory, timing or execution proof.
  All limits unchanged: shared60s work, separate5s cleanup,256MiB child/2tasks,
  512MiB aggregate/64tasks,20s namespace. M1 remains mandatory before account.
- [Raw evidence manifest](nov3-status-evidence.json) binds logs and reproduction
  scripts. Internal read-only documentation/test analysis is Codex work, not Claude.

Retention remains blocked by access

The **same**82-object179277768-byte package was fully rehashed locally; manifest
`d8ce40daf1d41cc472aa78fb5dfaa6428887a1bd9d9249658badcebce57dfbe1`,
30437432bytes below200MiB. No repackaging or input/receipt mutation. Existing exact
bucket/prefix authority persists. No Railway/S3 connector, usable CLI/config/env
credentials or active login/transfer was observed. No duplicate login initiated;
no auth material created/persisted, no secret values requested or displayed.
Exact project/environment/bucket remain owner/historical identities, not current
remote authentication proof. **0remote objects,0remote bytes; independent fresh
bucket download NOT_RUN.** Local cache is disposable, package remains
INCOMPLETE_FOR_INSTALLATION. No object-lock/WORM or durable retention claim.

Narrow Claude recheck requested

Review only216169bf..fb2dc86a43330f13376369c59db815dd03eb4d70: signature-status handling,
causal tests and effect on the accepted provenance path. Confirm cardinality/key
pairing, negative statuses in either order, malformed and benign output, unchanged
pins/bounds/cleanup, and pre-effect refusal. No sudo (including no-op), installation,
auth/login, transfer, dispatch, account/service/permission changes or source edits.
Report exact SHA/tree, tool/model/session, commands/results/limitations. No broad
architecture, batching or historical-finding review. There is no callable Claude
channel here; prepared/requested review is not an actual independent verdict.

Consolidated decision and next action

[Current decision readiness](nov3-status-decision-readiness.json) retains the exact
832-file/66-artifact manifest, trust root/fingerprint, provenance assurances,
alias/mode policy, native-only effects and bounded cleanup from the existing
complete decision packet. Recommend exact replacement Packages.xz
`4292c6f61b84c8685c73f6db585fce9974f293a0e62fb070a4c7a2572e333996` and signed InRelease
`5f4cbfd1daf09c5c6af3eeb67fe923b15010f28d9189b64995e2c0f68eec7b77`;
**not adopted**. Original missing8e3c evidence is preserved; no moving-index fetch.

Next executable action after authenticated trusted transfer becomes available:
verify exact approved project/environment/bucket/prefix, conditionally create or
verify/reuse the82content-addressed objects, then download/hash/size every object
in a fresh isolated context and clean temporary authentication/download resources.
Never overwrite conflicts, touch other prefixes, expose signed URLs or pass
bucket credentials to acceptance workers. Do not start a duplicate login.
In parallel obtain the one narrow Claude result for this frozen implementation.
Only after those prerequisites pass present the single metadata-adoption/native
installation decision; do not ask again for storage authority. New native
installation is not authorized. Target60s/5s/M1/mappings/service-budget measurements
remain NOT_RUN and fail closed if eventually authorized. Runtime, worker-control,
external cleanup, sentinel/release and browser gates remain open. Railway hosting
and commercial purchasing readiness are unchanged. No application acceptance claim.
