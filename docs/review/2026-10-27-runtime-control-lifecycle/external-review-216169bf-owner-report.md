Narrow delta review of 216169bf (from 3ed82222): signed-payload parsing correct, one status-handling gap; the replacement metadata independently verifies

The verifier now reads package-index bindings only from gpgv's verified signed output, and every binding I checked holds. The gap: it accepts statuses reporting an expired or revoked key or an expired signature. Separately, I fetched the exact replacement metadata bytes from public sources and they verify, including all 47 unchanged tuples and their complete stanza hashes.

Reviewer. Claude Code (container 2.1.317), session session_01JyrJEUAi36GJ6AfNcmBBxV. get_session reports configured and served model claude-opus-5-5, effort high.

Identities verified.

	•	Implementation 216169bfe82a4275ac78fcb54a716d400fe564c8, tree 26ac6ec1743769085ea556a2392098bf63496e0c. It descends from 3ed8222.
	•	Evidence checkpoint 9298b911…, tree 9a798a5abe1e2e94eda7cc1ab6741ef93fad21dd. It descends from the implementation.
	•	Material code delta: hosted_synthetic_native_route.py (+33/−15) plus tests and constants.

For reconciliation: my completed 3ed8222 review was delivered earlier in this session under the same session ID (container 2.1.317, effort high):

	•	T1 excess-work defect repaired; full-route timing unproven;
	•	stage cache closed;
	•	parser correct;
	•	H2 READY digest closed;
	•	P3: parse the gpgv-verified payload instead of raw armor;
	•	note: find inventory margin of about 850 B;
	•	the 8e3c index and durable storage still required.

This delta addresses the P3. Nothing else is reopened.

A. Implementation

Correct:

	•	Order of checks: the raw InRelease sha256 must match the pin (hosted_synthetic_native_route.py:226) before gpgv runs.
	•	gpgv invocation: --output - --status-fd 2 writes the payload to stdout and status lines to stderr. Limits: payload up to 2 MiB, status up to 4 KiB, return code must be 0.
	•	Signature checks: exactly one VALIDSIG from signer F6ECB3762474EDA9D21B7022871920D1991BC93C. BADSIG, ERRSIG, NO_PUBKEY and FAILURE refuse.
	•	Payload pin: the payload's sha256 must equal the pinned verified_release_sha256.
	•	Binding source: only the verified payload reaches _release_index_binding. The raw armored text no longer supplies bindings (tested: stripping the payload from the armor refuses).
	•	Existing pins match real gpgv output: noble 26758a13… and noble-updates 17ed1348….
	•	Cleanup: the gpgv home directory was still empty after real runs, which satisfies _empty_directory's empty-and-exact removal.

P2 defect: contradictory gpgv status lines are accepted (_verified_release_payload, :104-120). Running the real function on a real gpgv result:

	•	adding EXPKEYSIG, REVKEYSIG or EXPSIG → ACCEPTED;
	•	removing GOODSIG (VALIDSIG alone) → ACCEPTED.

The packet's claim that contradictory signatures are covered therefore does not hold for these statuses.

Correction:

	•	require exactly one GOODSIG whose key ID matches the VALIDSIG fingerprint;
	•	refuse REVKEYSIG and KEYREVOKED;
	•	for EXPKEYSIG/EXPSIG, either refuse or record an explicit pinned-snapshot policy that accepts historical expiry;
	•	add tests for each case.

Practical exposure is low today. The keyring is byte-pinned, so revocation data cannot arrive without its hash changing, and the 2018 key shows no expiry. It is still a gap in a verifier that claims to reject contradictions.

Other refusals I confirmed: non-zero return code, wrong signer, payload drift, empty stdout, oversize status output, duplicate VALIDSIG.

B. Replacement metadata: exact bytes independently verified

The packet did not commit the candidate bytes. I fetched them myself (public, read-only, into a disposable directory).

Object	Source	Result
Signed InRelease, 12:40:01 UTC	snapshot.ubuntu.com at 13:00Z, 14:00Z and 16:00Z	sha256 5f4cbfd1…77, 126,125 B, matches the pin. The live archive has since moved on (18:28 release, e8ce2678…), which no longer contains the candidate index.
Packages.xz	archive by-hash	sha256 4292c6f6…96, 1,407,612 B, matches the pin
Real gpgv output, with the pinned keyring (80a36b0a…, matching the local Ubuntu keyring)	—	return code 0, GOODSIG + VALIDSIG from F6EC…BC93C; payload a79193f3…40, 125,243 B, matches the pin

Run through the candidate's own code, unprivileged:

	•	_verified_release_payload accepts the real result.
	•	_release_index_binding(payload, candidate_index) accepts.
	•	verify_index_records parses 7,047 records and matches 47/47: exact package, version, architecture, filename/URL, SHA256 and size, plus the complete-stanza hash.
	•	Every complete-stanza hash equals the existing accepted package_record_sha256.
	•	The candidate JSON's 47 tuples equal the accepted source on artifact sha256 and record digest.

Readiness to adopt: the metadata is technically verified and fit for the owner's adoption decision. It changes only the 47 noble-updates signed-release and index identities, plus the snapshot-policy reference. No artifact, file, version or stanza changes.

Caveat: the snapshot service is the only current source for the 12:40 InRelease. That makes retaining both objects (126,125 B and 1,407,612 B) a prerequisite for adoption. Adoption itself is NOT_GRANTED and remains the owner's decision.

Separate items (not judged here)

	•	Storage: the packet reports 0 Railway objects written and 0 remote bytes verified, with an access blocker (no authenticated control plane or credentials). Storage verification is pending and outside this review.
	•	Installation: still requires adoption, retention with fresh-session readback, the remaining owner dispositions, and the fix for the P2 above. Then the target measurements (route timing within 60 s, cleanup within 5 s, native M1, view mappings, service budgets).

Tests and limitations

	•	Focused suite (exact workflow heredoc, uid 65534, disposable clone): Ran 1235, OK. Log /tmp/claude-0/-home-user-Buffalo-Procurement-System/0f1b2919-2af2-576d-8608-bd347e8aa4f0/scratchpad/2161-focused-uid65534.txt.
	•	Probes: real gpgv runs, status-handling negatives, payload binding, and the 47-record verification on the fetched bytes. All in disposable fixtures, cleaned up.
	•	Network: 6 public read-only GETs (archive and snapshot.ubuntu.com). No uploads.
	•	Not done: no sudo, installation, dispatch, account or service work, and no Railway access.

No verdict here authorizes adoption, installation, credential release or purchasing. Codex remains the sole writer.
