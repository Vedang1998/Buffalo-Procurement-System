# Sunday Purchasing Candidate — Acceptance Checklist

Contract: `BUFFALO-SATURDAY-PURCHASING-COMPLETION-2026-09-12`

Cutoff: 2026-09-13 13:00 UTC. Evidence below was completed before the
11:30 UTC validation/recovery reserve.

This is an isolated, loopback-only synthetic owner-demo candidate. It grants
no production, Shopify, supplier-contact, price-activation, deployment, PO
release, or real-order authority.

## Frozen identities

- Integration base: `f4f376442b8d019bd2418d74bb8766d0739c73f4`.
- Exact code/test candidate independently reviewed and tested:
  `802efc8147693d0de65d5636a0c1607364b5dd4f`, tree
  `843959cd3598eba12c6899bb1a003fad6a1e1003`.
- Branch: `codex/sunday-purchasing-release-candidate`; no remotes, disabled
  hooks and disabled default push.
- Preserved inputs: reader/QA I0 `5f86eee6...`, reviewed merge-hook safety
  `9cbbf368...`, reviewed persistent-mapping design `b0c8d3fe...`, and the
  separate Wright bundle at `e0bd6ce6...`. No remote push or protected-main
  merge occurred; nothing was installed into a connected app, deployed,
  published, or run against an operational database.

## Bounded local vertical — observed PASS

- [x] Two fabricated, sealed review packets were ingested through the real
  HTTP/service boundary into two immutable batches and two candidates.
- [x] One unresolved candidate was deferred. One fully fabricated
  authoritative-format candidate was approved by exact linkage to the one
  pre-existing active legacy STANDARD offer. Mapping and approval were
  distinct append-only events.
- [x] Routine offer selection required its own preview, idempotency key and
  confirmation. The selected head reported shadow `MATCH`; offer activation,
  price authority and recommendation cutover remained disabled.
- [x] Synthetic inventory, 84 days of synthetic sales, one vendor rule and one
  legacy CURRENT price prepared one eligible recommendation plus one blocked
  variant. The original blocker remained OPEN and its exclusion was RUN_ONLY.
- [x] The synthetic owner-demo workflow edited the eligible line to two cases / 12 units,
  separately confirmed the MATERIAL change, and recalculated exact cash
  economics.
- [x] One internal DRAFT for one synthetic vendor was built at merchandise and
  PO total `$20.02`; there were zero non-DRAFT POs, zero Shopify calls and no
  release action.
- [x] The internal CSV and 12-member review ZIP were downloaded, hash-checked,
  reopened after process restart, and replayed without duplicate effects.
- [x] Named-session authorization, exact loopback Host/Origin, CSRF, stale
  state and protected list/detail/source/download/write boundaries were
  exercised. A client-supplied spoof actor persisted in zero audited rows.
- [x] The real launcher survived an application restart. A real PostgreSQL
  custom-format dump plus storage archive restored into a new empty owned
  database with the exact pre-backup state hash and full relation/sequence
  inventory.

Chromium/restart evidence from the clean validation clone is bound to the exact
code/test commit and tree. It passed 126 phase-one assertions and 23
post-restart assertions. Durable state
SHA-256 was
`80ffbb0786cb748e58167e4963f13b19764b1eda847068a0d331f7b3496e9fbb`.
The two DRAFT artifact SHA-256 values were
`7924b9a999a6a70a295b6589764e6a2d28d1511c6ee43fe485461ede6af015ea`
and
`68c53a5c05a01b317e4a30a935a083a13a4bbc655d2aba72e7e2d3ce3a49c00f`.

## Machine validation

- Lock validation: cached `uv 0.12.3` reported `Resolved 22 packages`.
- Direct locked project dependencies in the executed Python 3.13.11 runtime
  matched `uv.lock`: FastAPI 0.141.1, HTTPX 0.28.1, Psycopg 3.3.4,
  python-multipart 0.0.32 and Uvicorn 0.52.1.
- Startup hardening: 10/10 PASS.
- Final focused set: 77/77 PASS in 67.188 seconds.
- Final authoritative suite against owned loopback PostgreSQL 16.9:
  **788 discovered / 788 executed / 788 passed** in 827.302 seconds;
  failures 0, errors 0, skips 0, expected failures 0, unexpected successes 0.
- The complete suite's owned fixture teardown removed the shared synthetic
  SET-role membership. The exact reviewed `qa_release_login` to
  `qa_mapping_owner` SET-only edge was restored in the owned cluster; a final
  read-only launchability check re-proved the role/database/PG16 contract and
  the unchanged durable-state SHA-256 above.
- Browser acceptance: PASS, 126 + 23 assertions, real loopback Uvicorn and
  Chromium, two phases separated by process restart.
- Backup/restore: PASS into `buffalo_802efc8_restore_demo`; state SHA-256 was
  identical before backup and after restore.
- Independent read-only code review found no P0–P2 issue in the `802efc8`
  synthetic-download-label delta; the prior mapping review remains applicable
  to its byte-identical persistent-mapping core, migration/apply-schema and
  39+3 test blobs, subject to the strict matrix caveat below.
- Accepted browser evidence root:
  `/home/runner/workspace/.ai-auth/codex/evidence/sunday-purchasing-browser-802efc8-final-20260913T102200Z`;
  `ACCEPTANCE_SUMMARY.json` SHA-256
  `f941ce3bb9bc0c35b608964eb62a9f06faaebc6954421da809084002c4836dd1`.
- Validation evidence root:
  `/home/runner/workspace/.ai-auth/codex/evidence/sunday-purchasing-validation-802efc8-20260913T102300Z`;
  accepted focused/startup/full log SHA-256 values are respectively
  `48531a2bd6d5911af6ab51bb959d81a28e422774ba343afb77c50dfeb6eafe09`,
  `8cbc2321289aad482131291f1375331b42ef87afbd8e2b0aeabd20b734d71193`
  and
  `722fc32e6849bc32a461b38e4df90a1194fd0c215aae3bfadff822a4787313e0`.
- Backup manifest:
  `/home/runner/workspace/.ai-auth/codex/sunday-purchasing-802efc8-runtime/backups/candidate-20260913T101222Z/manifest.json`,
  SHA-256
  `cd29779baed8c0aa4f2b622c493e3b9e80003422e4e9527a64225e13a9a86ee8`.
- Owner transport ZIP:
  `/home/runner/workspace/Buffalo_Sunday_Purchasing_Candidate_20260913.zip`,
  exposed through the final Codex file link; verify its separately reported
  SHA-256 and the package's recursive `SHA256SUMS` before use.

Earlier preserved setup attempts executed zero tests when an offline `uv run`
lacked a cached HTTPX wheel and an isolated repository `.venv` lacked Psycopg.
The first final lock-check setup also stopped before package resolution because
its scrubbed PATH omitted the Python 3.13 interpreter. None reached the
authoritative suite or PostgreSQL. The accepted startup/full runs used the
already-installed, dependency-version-matching Python 3.13.11 environment
under `env -i`; there was no network fallback or automatic CI retry, and the
accepted DB-reaching complete suite ran once.

The persistent-mapping modules contain 39 named PostgreSQL methods and three
pure methods, and all 42 execute green. Independent clause-by-clause review
does **not** accept that count as proof of the entire requested 39+3 matrix:
only PostgreSQL rows 7, 11, 34 and 39 are full strict-clause PASS; the other 35
PostgreSQL rows and pure P1–P3 are PARTIAL. No row is hollow after remediation,
and no production source defect was found, but full matrix acceptance remains
open.

## Working, partial, blocked and not implemented

| Area | Status | Exact boundary |
|---|---|---|
| Local synthetic owner vertical | WORKING | Intake through DRAFT/download/restart/recovery is observed on one vendor, one eligible line and one blocked line. |
| Private local access | WORKING FOR SYNTHETIC DEMO | Named in-memory sessions, server principals, loopback-only bind, CSRF and route capabilities; not a configured production IdP. |
| Persistent mapping foundation | WORKING / SHADOW ONLY | Five tables, four views and append-only intake/decision/selection services; activation, pricing and recommendation cutover remain false. Strict 39+3 clause evidence is partial. |
| Source-package readiness | PARTIAL | Two fabricated packets exercised. Private V5/S1 originals and real-source import/approval were NOT RUN and remain NOT_APPROVED. |
| Monday workflow | WORKING FOR BOUNDED SYNTHETIC CASE | Real services/UI produced one internal DRAFT and exact packet. Same-day immutable-DRAFT supersession remains unimplemented. |
| Export target | PARTIAL / INTERNAL DRAFT ONLY | `SHOPIFY_PO_CSV_FORMAT_NOT_LIVE_VALIDATED`; no native Shopify import was attempted or proven. |
| Forecasting | PARTIAL | `EMERGENCY_TRANSPARENT_V1` deterministic baseline works; model-selection/backtest/FVA, full ABC/XYZ and complete seasonal/category portfolio are not complete. |
| Price and pack authority | PARTIAL | One exact legacy active STANDARD offer and CURRENT synthetic price were used. Scoped replacement/carry-forward/withdrawal/deal overlays and the complete gift/multipack/BT/CS/assortment/combo browser matrix were NOT RUN. |
| Cash/basket optimization | PARTIAL | Exact line/vendor totals and material-edit cash bridge work. Strategic/forward-buy optimization remains evidence-only and buys zero extra. |
| Recovery | WORKING, SAME-HOST ONLY | Exact DB/storage backup and restore passed. This is not an off-host backup or host-replacement guarantee. |
| Commercial data | BLOCKED | No fresh real catalog, sales, inventory, incoming, supplier-source, mapping, price or vendor confirmations were supplied. |
| Production/Shopify/order release | BLOCKED | No deployment, publication, live Shopify access, operational DB access, PO release or real order was authorized or performed. |

Browser-only coverage was intentionally bounded. Corrupted/unsafe package
intake, regular/gift/multipack/rejection-memory breadth, simultaneous browser
actions, vendor-rule and price-book approval screens, CURRENT/FUTURE/carry/deal
scenarios, the full demand portfolio, one-bottle/received-stock cases,
BT/CS/assortment/combo economics and multi-vendor baskets were not all driven
through Chromium. Their lower-level tests may be green, but they are not
reported as completed browser scenarios.

## Final states

- `LOCAL_END_TO_END`: **PASS** for the bounded synthetic owner-demo vertical.
- `COMMERCIAL_DATA_READINESS`: **NOT_APPROVED**.
- `PRODUCTION_RELEASE`: **BLOCKED** and not authorized.
- `FULL_PRODUCT_REQUIREMENTS`: **PARTIAL / INCOMPLETE**.
- `GOAL`: **INCOMPLETE** relative to the full Saturday contract.

## Owner Sunday queue

1. Review this frozen bundle and the explicit 39+3 clause-coverage gaps; obtain
   independent external release review before treating the foundation matrix
   as accepted.
2. Provide or authorize a physically separate private-real review environment
   and the missing hash-verified V5/S1/source packages. Keep every resulting
   proposal read-only and unapproved until reviewed by a named owner.
3. Confirm real catalog, same-day inventory, canonical sales, incoming POs,
   vendor terms, mappings and scoped CURRENT/FUTURE/deal price authority.
4. Close the remaining forecast, price lifecycle, supersession and browser
   scenario gaps before claiming the full product requirement.
5. Treat receiver isolation, remote integration, CI, deployment and any
   Shopify/PO/order action as later, separately authorized checkpoints.

## Start and reachability

After PostgreSQL 16 is running with the preserved/restored owned demo database,
start the application from the candidate repository with:

```bash
set -euo pipefail
cd /home/runner/workspace/.ai-auth/codex/sunday-purchasing-release-candidate
test "$(git rev-parse 802efc8147693d0de65d5636a0c1607364b5dd4f^{tree})" = \
  843959cd3598eba12c6899bb1a003fad6a1e1003
git merge-base --is-ancestor \
  802efc8147693d0de65d5636a0c1607364b5dd4f HEAD
git diff --quiet 802efc8147693d0de65d5636a0c1607364b5dd4f HEAD -- . \
  ':(exclude)docs/CODEX_HANDOFF.md' \
  ':(exclude)docs/SUNDAY_PURCHASING_ACCEPTANCE.md' \
  ':(exclude)procurement/docs/PHASE_STATUS.md'
test -z "$(git status --porcelain)"
env -i \
  PATH=/home/runner/workspace/.pythonlibs/bin:/usr/local/bin:/usr/bin:/bin \
  LANG=C.UTF-8 \
  PYTHONPATH=procurement/src:procurement/tools \
  /home/runner/workspace/.pythonlibs/bin/python \
  procurement/tools/local_purchasing_candidate.py serve \
  --database-url 'postgresql://qa_release_login@127.0.0.1:33975/buffalo_802efc8_candidate_demo?options=-c%20role%3Dqa_mapping_owner' \
  --runtime-root /home/runner/workspace/.ai-auth/codex/sunday-purchasing-802efc8-runtime \
  --port 8765
```

Reach it only on the same host at `http://127.0.0.1:8765/`. The accepted
auditor used port 18771; port 8765 above is the same-host operator port, not the
evidence URL. The acceptance server was stopped after testing; there is no
public preview or tunnel. The owned loopback PostgreSQL cluster was also
stopped after the final launchability readback; its host-local data root is
preserved for the restart command in the owner transport. This candidate's
generated local secret values remain
only in the mode-0600 files beneath the mode-0700 runtime root and are
intentionally absent from this document and the transport package.
