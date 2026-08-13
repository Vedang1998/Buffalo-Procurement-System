# Buffalo Procurement OS — Canonical implementation audit

**Audit date:** 2026-08-13 UTC

**Branch:** `tooling/overnight-hardening-2026-08-12`

**Authority:** canonical system specification v2.1, `config/rules.toml`, PostgreSQL schema/migrations, implementation, and deterministic tests
**Scope:** current catalog/sales foundation, shared safety primitives, and non-operational later-phase scaffolding

This audit does not declare a project phase complete. Findings fixed on this branch remain subject to GitHub CI, independent adversarial review, owner acceptance, and merge authorization. Production data was inspected read-only. No identity decision, Shopify write, PO action, or production mutation was performed.

## Classification method

- **CRITICAL:** could permit unsafe procurement, production mutation, or a materially false readiness result.
- **HIGH:** could corrupt or silently misclassify material identity, catalog, sales, or price evidence.
- **MEDIUM:** materially weakens transactional/audit correctness but has a contained operational path.
- **LOW:** limited defect or defense-in-depth gap without demonstrated business-data impact.
- **DOCUMENTATION ONLY:** implementation behavior was not defective, but durable operating state was inaccurate or incomplete.
- **NO ISSUE:** examined behavior agrees with current authority or is correctly unavailable behind a phase boundary.
- **A:** correct fix is fully determined by canonical authority and safe within this sprint.
- **B:** owner/business decision is required; no implementation decision was made.

## Discrepancy inventory

| Severity | Class | Area | Finding and evidence | Disposition |
| --- | --- | --- | --- | --- |
| CRITICAL | A | PO readiness | PO readiness considered only rows marked `blocks_po` and did not require all seven canonical global gates to exist and be `PASS`. A missing gate or a required gate at `WARN` could therefore leave `po_generation_enabled=true`. | Fixed. All seven required gates must exist globally and be `PASS`; applicable vendor/variant failures also block. Direct PostgreSQL integration tests prove missing and non-PASS gates fail closed. |
| CRITICAL | A | Price lifecycle | A callable rollover path deleted CURRENT rows and promoted FUTURE rows without the canonical completeness proof, backup, approved supplier-SKU transition handling, or post-transition assertions. | Fixed by disabling the operational endpoint and library mutation until canonical Phase 4 price-book controls exist. No price data was changed. Regression tests prove fail-closed behavior. |
| HIGH | A | Catalog readiness | `CATALOG_SYNC` could be persisted/recomputed as `PASS` without proving nonzero live rows, exact source accounting, a source hash, pagination completion, and zero blockers. Recompute could also select an older completed run over a newer failed run. | Fixed. Evidence is revalidated centrally, and the newest started run governs recompute. Adversarial tests cover incomplete evidence and a newer failed run. |
| HIGH | A | Historical identity | Rows with null/zero historical Variant ID could resolve from a current exact SKU plus normalized title without a prior approved historical alias. The canonical hierarchy requires approved historical identity evidence and otherwise review. | Fixed. Such evidence is retained for review but remains unresolved. A read-only production query found zero currently resolved facts using the removed method, so no production correction is indicated by this defect. |
| HIGH | A | Supplier matching | A fuzzy score above the configured threshold could set `auto_match=true`, contrary to `fuzzy_is_supporting_evidence_only=true`. | Fixed. Fuzzy evidence can rank a review candidate but cannot authorize an automatic supplier mapping. |
| MEDIUM | A | Migrations | Migration SQL and its `schema_migrations` audit row were committed separately. A failure between commits could leave an applied but unrecorded migration. | Fixed. Each migration and its audit record now commit atomically. A disposable-PostgreSQL test applies the complete migration chain twice and verifies idempotent audit state. |
| MEDIUM | A | Human decision audit | Catalog recreation/rejection/retirement helpers accepted blank actor or reason values, weakening mandatory decision provenance. | Fixed. Permanent catalog decisions require nonblank actor and reason before any database operation. API/HTML validation and regression tests cover the boundary. |
| LOW | A | Secret safety | A malformed Shopify token response could include the complete response payload in an exception string. | Fixed. The exception no longer echoes the payload; the test uses a sentinel to prove it is absent. |
| DOCUMENTATION ONLY | A | Handoff | The Phase 4 checkpoint told future agents to infer the implementation commit from current `HEAD`, which is not durable. | Fixed. The handoff records verified Phase 4 implementation commit `a78b5808551f3bae584367a631cf25776d3ff038`. |

## Examined controls with no defect found

| Classification | Control examined | Evidence / conclusion |
| --- | --- | --- |
| NO ISSUE | Canonical Shopify Variant ID | Schema keys, catalog resolution, aliases, and sales aggregates retain Variant ID as canonical identity. Supplier SKU is not used as the permanent product key. |
| NO ISSUE | Recreated identities | Candidate evidence does not silently create old-to-new aliases. Permanent alias creation remains an explicit audited human action. |
| NO ISSUE | Retirement | Retirement is an audited identity state; it does not delete raw or canonical historical sales. |
| NO ISSUE | Sales source/raw/canonical controls | Production read-only evidence showed 59,083 source facts and 59,083 unique raw facts, exact source/raw unit and revenue totals, and resolved plus unresolved totals that reconcile to source. Canonical rebuild and idempotency evidence were true. |
| NO ISSUE | Duplicate ingestion and resume | Natural source-row hashes, run/chunk/page evidence, and upserts prevent double counting and support retry/resume. Existing and added integration tests cover restatement and idempotency paths. |
| NO ISSUE | Sales exclusions | Exclusion is a separately audited owner decision; unresolved facts cannot silently enter `sales_daily`. No exclusions were created during this sprint. |
| NO ISSUE | CURRENT/FUTURE separation | Validation treats states separately and tests prevent premature FUTURE promotion. The unsafe incomplete operational rollover was disabled as described above. |
| NO ISSUE | BT versus CS | Typed break units and arithmetic tests preserve BT/CS distinction. |
| NO ISSUE | Assortment | Same-product default, explicit cross-product exception, and `DOES NOT ASSORT` precedence are represented in rules and deterministic tests. |
| NO ISSUE | Retail pricing | Automatic retail-price update remains disabled; no tested production Shopify write path is invoked. |
| NO ISSUE | Vendor minimum | Rules preserve advisory warning behavior and forbid junk-filler auto-add. No current PO engine exists to contradict the rule. |
| NO ISSUE | Combos, gifts, allocations, loose bottles | Auto-add/replenishment remains off or requires later structured human review. These later-phase behaviors are not operationally activated. |
| NO ISSUE | PO ledger / one PO per vendor | Schema foundations exist, while PO generation remains disabled. There were zero production purchase-order rows at baseline. Later operational behavior is correctly gated rather than claimed complete. |
| NO ISSUE | No-runtime-LLM architecture | No mandatory runtime model dependency was found. |
| NO ISSUE | Production-write containment | Tests use disposable PostgreSQL only; the canonical runner rejects non-loopback and non-`_test` databases and clears inherited `DATABASE_URL`. |

## Owner/business decisions — Class B, intentionally untouched

1. **Phase 4 historical identities:** 343 grouped review items (341 material) require authenticated owner map/exclude/leave decisions. No item was mapped, excluded, or auto-decided.
2. **Vendor operating rules:** calendars, minimum details, broken-case fees, loose-bottle policy, lead times, and reliability profiles require verified vendor evidence and owner approval before `VENDOR_RULES` can pass.
3. **Supplier mappings and exceptions:** any ambiguous supplier identity, temporary/gift/combo SKU, pack interpretation, BT/CS ambiguity, assortment exception, or permanent mapping requires the defined human decision scope.
4. **Price-book promotion:** future price coverage, approved supplier-SKU transitions, and the first operational rollover require a reviewed Phase 4 implementation and explicit release authorization.
5. **Forecasting and procurement policy calibration:** model acceptance, service/protection policy, event overrides, strategic quantities, combos/new items, and final POs remain future human-review boundaries.

## Open risks and containment

- Remote GitHub Actions proof is pending until a pull request targets `main`; the local runner provides parity but cannot substitute for the remote check.
- Material changes require independent adversarial review. This implementation agent's audit is evidence for that review, not a replacement for it.
- `SALES_BACKFILL` remains `FAIL`; `VENDOR_RULES` remains `FAIL`; all other non-catalog readiness gates are not accepted for procurement reliance. PO generation therefore remains disabled.
- Post-foundation modules are incomplete by design. Absence of later-phase operating behavior is not treated as a present defect and must not be bypassed with scaffolding.

## Regression evidence added

The branch adds or expands deterministic tests for required-gate completeness, gate scope, catalog evidence completeness, newest-run failure precedence, approved-only historical identity resolution, fuzzy-match review-only behavior, disabled rollover, human-decision provenance, migration atomicity/idempotency, secret-safe token errors, and static no-production-write/no-PO-release contracts. Exact final counts belong in `docs/CODEX_HANDOFF.md` after closeout validation.
