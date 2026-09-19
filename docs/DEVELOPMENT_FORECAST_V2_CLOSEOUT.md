# Development Forecast V2 — Five-Finding Closeout

**Status:** bounded synthetic machine pass; same-model static review pass; external
independent review and all real purchasing authority remain blocked.

**Closed at:** 2026-09-19 (UTC)

## Exact identities

- Reviewed reconstruction prerequisite: commit
  `dd1316022c3e778963c84d818f79ce61f61f192a`, tree
  `cc98458065117be8850225099591acb60e48f469`.
- Exact machine-tested material commit:
  `ac65b8fc3263834d1a4c91001330e227f14870bb`, tree
  `6bbb6bfbeade34771b6f95cc0bf3cc257c5d986d`.
- The material range is five linear commits, 18 changed files, 4,482
  insertions and 289 deletions.
- The documentation-only closeout commit is recorded separately in the
  additive review archive. It must not be described as the tested code.
- The material subtrees are:

  | Path | Tree |
  | --- | --- |
  | `procurement/src` | `110e8560c39c009e8255f1a5a0284d4df300e75f` |
  | `procurement/db` | `c6fd3e0459473ad479c106222a3b8d9746c0862a` |
  | `procurement/config` | `1e6449a792393bc25edcce99fc14f6a43f9fe9eb` |
  | `procurement/tests` | `ec73ecab7b000d2d2e157d70d9d8a968962b8984` |
  | `procurement/tools` | `2f3eb3a126bc80f0fda05b239db6a1f100754a6a` |
  | `scripts` | `944d1c42331c367b0a530f97f5ffcbf58f0d6acb` |

No migration or table was added. `procurement/db`, `pyproject.toml`,
`uv.lock`, package metadata, scripts and the V1 policy bytes are unchanged.

## Immutable registry and artifacts

The approved profile is the separate, server-registered
`development-forecast-v2` synthetic profile. It does not convert, relabel or
reinterpret a V1 database or completed V1 run.

| Artifact | Source SHA-256 | Canonical SHA-256 |
| --- | --- | --- |
| V2 policy | `fee2e91e14565a835f1d69c27ff547ccdda5c22c6bc43a2bb003c2b78f190b52` | `ff62b1d313a18a907e811e8722431efee2349fc95a31e08ea5843ca2664ca858` |
| V2 schedule | `1cb3edf5e82f2f01cb0a4c972d6140f97e767df67468373b245d6dd414ad8640` | `2c8b09152d3005b227a93ba700a8c08eec68433bf8f39e44c85353af34639c39` |

- V2 evidence contract: `BUFFALO_DEVELOPMENT_FORECAST_EVIDENCE_V2`.
- V2 method: `DEVELOPMENT_ROLLING_ORIGIN_V2`.
- V2 registration canonical SHA-256:
  `ba5899a31ab4428d7ddaa9b29bc555bd25410efe06b44deaceefaa677320bcea`.
- Accepted design SHA-256:
  `82628d2acc4ae80f2b3b1b69849247ce145a789545c0dd17fe2520e324b75e41`.
- V1 policy source SHA-256 remains
  `a44a9760b9f9a348cf2c99ab769f9f042ee9873c4938f18e54e2ffb41d3cf6fc`.

Activation requires the exact server-owned fixture/profile/contract/policy/
schedule tuple, the existing selected-offer attestations and supervised
synthetic runtime capabilities. A request flag, profile string, database-name
suffix or caller-supplied marker cannot activate V2. Unknown, partial, mixed
V1/V2 and hash-mismatched tuples refuse; there is no silent V1 fallback.

## Five-finding closure matrix

| Finding | Implemented closure | Evidence |
| --- | --- | --- |
| Semantic forecast-to-need binding | V2 freezes a sibling binding over Variant ID, forecast evidence SHA, semantic need SHA and canonical calculation inputs. Manifest classification, current-input validation, review, DRAFT and packet boundaries deterministically replan the forecast and rerun the need calculator. Cross-Variant, mixed-version, stale-hash and changed-field substitutions refuse. Owner review edits remain separate immutable decisions and do not rewrite baseline need. | Pure tamper probes, PostgreSQL service test, packet/browser reconciliation and terminal replay all passed. |
| Incomplete ABC cohort | Classification requires the entire declared cohort and independent historical gross-profit inputs. A missing member makes the cohort `INCOMPLETE`/`NOT_CONFIGURED`; known rows do not receive provisional letters. Connected synthetic ABC remains `NOT_CONFIGURED` because historical COGS is absent, and current supplier price is never substituted. | Pure cohort mutation tests and connected evidence assertions passed. |
| Policy-bound confidence | V2 policy explicitly freezes inclusive HIGH/MEDIUM WAPE bounds `0.20` and `0.50`, the metric identity, finite ordering and exact hashes. Evidence and deterministic replan use the same values. | Threshold boundary, malformed policy and hash-mismatch tests passed. |
| Anchored alternating-week schedule | The schedule is an immutable union of fabricated recurrence series, exceptions, validity, timezone, cutoff and start-of-day receipt semantics. Review, submission and receipt opportunities are resolved independently. Southern is anchored every other week; Western is the weekly control. | Southern 2026-10-05 submission -> 10-08 receipt -> 10-19 next submission -> 10-22 receipt gives half-open H17. Western Monday review/current submission -> Wednesday next submission -> Thursday receipt gives H3. Cutoff, timezone, anchor, exception and validity negatives passed. |
| Adequate longer-horizon evaluation | V2 requires exactly 138 contiguous days and partitions them 28 training / 38 selection / 38 calibration / 34 evaluation without reducing any origin minimum. H1-H31 complete-history coverage is retained; missing, duplicate and censored evidence blocks or refuses as declared. | H3 has 36/36/32 usable origins, H17 has 22/22/18 and H31 has 8/8/4. The prior 84-day H15/H17/H21 block is not hidden or relaxed; only the separately registered 138-day V2 fixture supplies adequate evidence. |

The fixture contains exactly 966 synthetic sales rows: seven Variants x 138
days, 2026-05-20 through 2026-10-04. All dates, schedules and observation
instants are fabricated and pinned; nothing derives authority from the host
date or a real distributor schedule.

## Exact machine validation

Every accepted execution below used material commit `ac65b8fc...`, tree
`6bbb6bf...`.

| Check | Result | Raw log SHA-256 |
| --- | --- | --- |
| V2 pure forecast module | 13/13 passed in 1.426s | `6b252309548335911895df60db2fbe2bf3fe8ef2220845b37c03b428ddceb0fc` |
| V2 launcher/fixture module | 15/15 passed in 0.255s | `533d7ee8d298eb8e55eb1897f5258de24b36114b77c4a9efc7118caa03d03bb8` |
| V2 PostgreSQL service module | 13/13 passed in 68.233s | `1e2c6fc6d87b4f5298dd8769ac357fe37b3ede774185c8498e3de33389fbc775` |
| Authoritative repository wrapper | 845 discovered / 845 executed / 845 passed in 1180.378s; every abnormal counter zero | `a3205c1075393556d5de84df1a11b43dfe575bc85426f51350325194523be193` |
| Startup hardening | 10/10 passed in 0.005s | `613651f9a638a300d82c649547b5cede9523ceae784078db59332817220355d7` |
| Chromium/restart/recovery | 171/171 assertions passed | stdout `b462f7a8d74da635ab154ed4b19cc1ebcdfc1e094fe49a4556475080d3f73935`; summary `59e5cad333b05f172c86b3707c65e5cad5d5c38550579ac6274889b1b2d750b4` |

Every accepted command has a retained zero exit file with SHA-256
`9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`.
Two read-only same-model reviewers inspected exact `ac65b8fc...` and reported
no remaining P0-P2 finding. They did not run the database/browser suite and
must not be described as Claude or external independent review.

The first V2 browser attempt at implementation commit `1538856...` failed
truthfully because the fabricated Variant 4004 history produced a full case,
so the required loose-unit-fee blocker was absent. The V2-only fixture was
corrected to an adequately evidenced `2,2,0` cadence; V1/default fixture bytes
and results stayed unchanged. That failed attempt is retained only as a
diagnostic, not acceptance evidence.

## Browser-created state and arithmetic

The successful browser flow created, rather than inherited, the price
confirmation/application, seven mapping decisions, five selection heads, the
six-item Monday run, two blocker exclusions, four immutable recommendation
decisions, two DRAFTs, three lines and three artifacts.

- Southern Variant 1001: H17 point forecast/raw need 34; six cases / 36 units;
  selected uploaded two-case BREAK price $30; merchandise $180.
- Western Variant 4001: H3 point/raw need 4; owner edited one case to two;
  merchandise $84.
- Western Variant 4002: H3 point/raw need 4; accepted one case; merchandise
  $18.
- Western Variant 4003: blocked by
  `ROUTINE_SELECTED_OFFER_HEAD_REQUIRED`; no legacy offer fallback.
- Western Variant 4004: H3 point/raw need 4, zero cases + four loose units,
  frozen `$3.00` loose fee; blocked by
  `LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED`.
- Western Variant 4005: ALLOCATED policy remained authoritative and the owner
  immutably rejected the zero recommendation.

The final two-vendor DRAFT contains three lines: merchandise **$282**, one
Western below-minimum fee **$7**, and total **$289**. The 14-member packet and
both internal CSVs have SHA-256 values:

- packet: `7929172e542e126316c06cf46cbfd2eb301c369f7af28fd8d10db07fb5181591`;
- Southern CSV: `d730f9c988deec3cbcbb5652647630e8c1d8500c1437bee125b7ea6a81c1be7a`;
- Western CSV: `56b61473a78a3c68c25eb0b7eb21ab638f36eceb41bb1303e97e41bb070f4aec`.

## Restart, recovery and cleanup

Source restart, restore into a physically distinct PostgreSQL 16 cluster,
recovered restart and terminal replay retained the exact run, DRAFT and
artifact identities and bytes. Source/target system identifiers are
`7687385158040509340` and `7687385410796826131`. Both durable-state SHA-256
values are
`32b0a4de9f37c554e69f76693ef4ede8c914dcbee93e0b7d4efd8bdf6db08f98`.
The browser evidence tree has 28 files, 4,235,897 bytes and sorted-index digest
`e452cc43a58afb1bffcee38284aeb8d502abb4737e541f5de4535d33747fd027`.

Both database clusters, both app instances and Chromium were stopped. Secret
values from all six runtime secret files were checked against the retained
evidence with zero matches. The two exact isolated browser work roots,
including their databases, backups and secrets, were then deleted. No
listener or owned process remains. Only non-secret evidence and logs are
retained.

## Preserved boundaries and unsupported cases

This is synthetic loopback-Linux, internal-DRAFT evidence only. It grants no
real forecast accuracy, commercial data, service-level, XYZ, FVA, schedule,
price, mapping, inventory, PO-release or ordering authority. In particular:

- V1 policies, fixture bytes, completed runs, packet bytes and replay behavior
  remain historical and version-bound.
- The actual V2 connected availability is UNKNOWN, so protection is limited
  and confidence LOW. A zero empirical protection amount is not real safety
  stock evidence.
- CATEGORY_SHRINKAGE remains inapplicable without a frozen causal category
  prior. Connected ABC remains unconfigured without historical revenue and
  COGS.
- Nonzero lead-time variability refuses until a separate validated
  delivery-delay model exists.
- The pure schedule registry supports the fabricated weekly and anchored
  alternating-week contracts only. It is not approval of any real supplier
  cadence or cutoff.
- Direct-SQL enforcement, real backtesting and secondary real-input analysis,
  production identity/IdP, owner remote access, native Shopify CSV validation,
  deployment and all operational gates remain blocked. The real-input
  analysis was **NOT RUN**.

The official production phase is unchanged. The next authorized boundary is
transport-only independent review of the additive V2 package and owner
acceptance of this bounded synthetic result. Any real/default activation,
policy approval, real data, deployment, Shopify write, PO release or order
requires a separate reviewed authorization.
