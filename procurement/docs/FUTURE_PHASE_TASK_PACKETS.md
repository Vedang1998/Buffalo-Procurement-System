# Buffalo Procurement OS — Future-phase implementation task packets

**Prepared:** 2026-08-13 UTC

**Authority:** canonical specification v2.1, current implementation roadmap, and `docs/PROJECT_GOVERNANCE.md`
**Current state:** `CATALOG_SYNC=PASS`; `SALES_BACKFILL=FAIL`; Phase 4 owner review remains open; no later operational phase is activated

These packets prepare work; they do not authorize it. They deliberately preserve both numbering systems already present in the repository:

- **Foundation implementation phases 0–6** are the execution phases in `procurement/docs/PHASE_STATUS.md` and the Replit build prompt.
- **Canonical phases 1A–9** are the product build phases in canonical specification section 37.
- **PF-01–PF-13** below are packet labels for the 13 ordered post-foundation workstreams. They are not new official phase IDs.

Every packet inherits these non-negotiable constraints: Shopify Variant ID is canonical; supplier SKU is mapping evidence; no fuzzy material mapping; no reusable price archive; no BT/CS interchange; no automatic retail-price change; no combo auto-add; one vendor per PO; human review remains in consequential decisions; no operational PO while a required gate is not `PASS`; no production activation without owner authorization.

## Foundation Phase 4 — Historical sales review and closeout

- **Official ID/name:** Foundation Phase 4 — Historical ShopifyQL sales backfill / reconciliation; maps to canonical Phase 2B.
- **Objective:** Convert the completed raw backfill into an owner-reviewed, fully canonicalized history and legitimately pass `SALES_BACKFILL`.
- **Prerequisites / current readiness:** Catalog gate passes; 59,083 durable source facts reconcile. Blocked by 343 grouped owner-review items, 341 material; gate remains `FAIL`.
- **In scope:** Authenticated grouped review; explicit map/exclude/leave decisions; local re-resolution; canonical aggregate rebuild; control-total and idempotency verification.
- **Out of scope:** Refetching to avoid review, automatic mapping/exclusion, supplier mapping, forecasts, procurement recommendations, PO work, Shopify writes.
- **Data dependencies:** `shopify_sales_daily_raw`, `variant_aliases`, `historical_sales_exclusions`, `sales_daily`, run/page/chunk evidence, decision audit.
- **Schema changes anticipated:** None unless independent review proves an additive audit constraint is needed; use migrations if so.
- **Business rules / human decisions:** Approved historical identity or explicit exclusion only. Owner decides every material unresolved group; decisions require actor, reason, and durable audit.
- **Acceptance criteria:** All requested chunks/pages complete; source=raw and resolved+excluded+unresolved controls reconcile; material unresolved units/revenue are zero or explicitly accepted by policy; canonical rebuild is reproducible; gate derives `PASS` from evidence.
- **Deterministic test plan:** Map/exclude/leave authorization, ambiguity, re-resolution without refetch, exclusions, restatement, duplicate ingestion, transaction rollback, canonical rebuild, readiness evidence, unauthorized request rejection.
- **Required control totals:** Source/unique/resolved/ambiguous/unresolved/excluded rows, units, revenue; chunk/page coverage; duplicate count; canonical rows/units/revenue; before/after decision deltas.
- **Migration/idempotency:** Decision writes unique/audited; re-resolution/rebuild repeatable; source facts immutable except controlled restatement semantics; additive migrations only.
- **Rollback/containment:** Revert an incorrect decision with an audited corrective decision, rebuild from retained raw facts, keep gate `FAIL`; never delete source evidence.
- **Risk / review:** CRITICAL identity and demand-history risk. Full machine validation, Claude adversarial review, Cursor targeted SQL/identity review, ChatGPT business-rule review, owner acceptance.
- **Release gate / authorization boundary:** `SALES_BACKFILL=PASS` only after evidence and owner decisions. No Phase 4 decision or closeout is authorized by this packet.

## Foundation Phase 5 — Foundation UI formal acceptance

- **Official ID/name:** Foundation Phase 5 — Foundation UI; partially delivered.
- **Objective:** Formally accept an operationally clear readiness/catalog/history/data-runs UI without expanding business behavior.
- **Prerequisites / current readiness:** Existing pages are present; Phase 4 owner review and formal UI acceptance are pending.
- **In scope:** Readiness status/blockers, catalog reconciliation, grouped historical review, sync-run evidence, authentication/authorization, accessibility and error-state verification.
- **Out of scope:** Procurement recommendations, pricing decisions, PO controls, redesign, production write automation.
- **Data dependencies:** Read-only readiness/run/reconciliation views plus narrowly scoped audited decision endpoints.
- **Schema changes anticipated:** None expected; audit/authorization schema changes only if separately reviewed.
- **Business rules / human decisions:** UI must not imply code existence equals readiness. Owner review actions remain explicit and irreversible effects clearly disclosed.
- **Acceptance criteria:** All canonical pages and exact blockers visible; disabled PO state visible; no unauthenticated decision route; error/loading/empty states fail closed; audit actor/reason captured.
- **Deterministic test plan:** Route/auth tests, CSRF/request validation as applicable, rendering of PASS/WARN/FAIL and blockers, empty/error states, no secret exposure, no accidental production-write paths.
- **Required control totals:** UI counts match database counts for catalog blockers, history groups, run coverage, and gates.
- **Migration/idempotency:** UI reads are side-effect free; repeated decisions are rejected or idempotently resolve to one audited result.
- **Rollback/containment:** Revert UI deployment; decision audit remains durable; no gate is set directly by UI.
- **Risk / review:** HIGH due to identity decisions. Claude adversarial review; Cursor accessibility/security specialist review; owner workflow acceptance.
- **Release gate / authorization boundary:** Formal Phase 5 closeout requires owner acceptance after Phase 4 review. No phase status change is authorized here.

## Foundation Phase 6 — Foundation test and acceptance completion

- **Official ID/name:** Foundation Phase 6 — Foundation test / acceptance completion.
- **Objective:** Prove the catalog/sales foundation meets its Definition of Done and is safe to hand into post-foundation work.
- **Prerequisites / current readiness:** Deterministic suite passes locally; GitHub CI and independent review are pending; Phase 4 gate remains failed.
- **In scope:** CI proof, full deterministic/adversarial suite, control totals, schema/migration verification, independent review/remediation, owner acceptance, closeout evidence.
- **Out of scope:** Declaring later gates passed, PO activation, post-foundation implementation bundled into acceptance.
- **Data dependencies:** Disposable PostgreSQL fixtures plus read-only verified production control totals.
- **Schema changes anticipated:** None except fixes proven necessary by review and supplied through additive migrations.
- **Business rules / human decisions:** Machine evidence outranks confidence; material identity acceptance remains with owner.
- **Acceptance criteria:** Required `procurement-tests` check green; no unexpected skips; exact controls reconcile; no unresolved critical/high review findings; owner approves completion.
- **Deterministic test plan:** Complete canonical command plus focused identity, retry/resume, transaction, duplicate, exclusion, readiness, and production-write safety tests.
- **Required control totals:** Test discovered/executed/pass/fail/skip counts; seed/catalog/sales controls; gate state; open critical/high exception counts.
- **Migration/idempotency:** Full migration chain applies once and re-runs safely on disposable PostgreSQL; imports and rebuilds demonstrate idempotency.
- **Rollback/containment:** Do not merge failed work; keep current production state unchanged; revert feature commit or deploy previous accepted revision if needed.
- **Risk / review:** CRITICAL acceptance boundary. Claude independent review, targeted Cursor review, ChatGPT program review, owner merge/phase approval.
- **Release gate / authorization boundary:** Phase 6 can close only after Phase 4 and required review/CI evidence. This packet authorizes no merge or release.

## PF-01 — Nightly inventory snapshots and evidence

- **Official phase/workstream:** Canonical Phase 3 — Inventory history + vendor rules; ordered workstream 1.
- **Objective:** Persist auditable daily Available/Incoming inventory and establish reliable stockout/adjustment evidence.
- **Prerequisites / current readiness:** Foundation acceptance and `SALES_BACKFILL=PASS`; `daily_inventory_snapshots` scaffolding exists; `INVENTORY_HISTORY` is not ready.
- **In scope:** Shopify inventory queries, location aggregation policy, nightly job, run manifests, missing-location/error handling, inventory/adjustment probes, stockout-day evidence.
- **Out of scope:** Inventory mutation, transfers, retail writes, forecasts or buying decisions.
- **Data dependencies:** Active variants/inventory item IDs, Shopify locations/quantities, run timestamps, collections, adjustment analytics where verified.
- **Schema changes anticipated:** Harden snapshot uniqueness/location provenance, job/run coverage, source hashes, adjustment evidence, and readiness evidence via additive migrations.
- **Business rules / human decisions:** Available and Incoming definitions/location scope require verified operational confirmation; unknown inventory states fail closed.
- **Acceptance criteria:** Scheduled idempotent captures cover the canonical active universe; missed/partial runs visible; stockout evidence reproducible; gate passes only after required history duration and quality criteria are owner-approved.
- **Deterministic test plan:** Pagination, location aggregation, duplicate facts, rerun, partial API failure, retry/resume, missing inventory item, clock/cutoff handling, no mutation query.
- **Required control totals:** Expected/observed variants, locations, missing/duplicate rows, Available/Incoming totals, pages, source hash, run duration, consecutive complete days.
- **Migration/idempotency:** Natural key by capture/run/location/variant as selected; upserts do not double-count; migration backfill not inferred from live current state.
- **Rollback/containment:** Disable scheduler, retain snapshots/run evidence, mark gate failed/warn, resume from last complete run.
- **Risk / review:** HIGH. Claude review plus Cursor Shopify-query/data-integrity review; owner approves location and readiness policy.
- **Release gate / authorization boundary:** Read-only Shopify access and local tests may be authorized separately; scheduler activation and `INVENTORY_HISTORY=PASS` require owner release authorization.

## PF-02 — Vendor operating profiles

- **Official phase/workstream:** Canonical Phase 3; ordered workstream 2.
- **Objective:** Represent vendor calendars, lead times, minimums, fees, loose-bottle rules, delivery reliability, and evidence provenance.
- **Prerequisites / current readiness:** Foundation accepted; vendor seed exists, but current `VENDOR_RULES=FAIL` and operating facts are incomplete.
- **In scope:** Typed profile model, evidence/audit UI, effective dating, validation, exceptions, advisory minimum calculation inputs.
- **Out of scope:** Guessing terms, auto-filler, automatic vendor policy changes, PO release.
- **Data dependencies:** Vendor agreements/books, delivery history, owner-confirmed operating facts, existing `vendors` rows.
- **Schema changes anticipated:** Normalize effective-dated vendor rules/evidence rather than overloading current scalar fields; add constraints for threshold logic, fees, lead time, loose eligibility, provenance.
- **Business rules / human decisions:** Owner confirms uncertain thresholds, calendar, fees, routine loose rules, and reliability policy. Vendor minimum warns and never mandates junk filler.
- **Acceptance criteria:** Every active vendor needed by a run has complete, current, sourced rules; ambiguity is visible; gate evidence enumerates missing/expired fields.
- **Deterministic test plan:** Threshold combinations, below-minimum advisory behavior, fee arithmetic, calendar/cutoff, effective-date overlap, missing rules, loose restrictions, transaction rollback.
- **Required control totals:** Active vendors, complete/incomplete profiles, expired rules, overlapping rules, evidence documents, missing mandatory fields.
- **Migration/idempotency:** Effective-dated uniqueness/exclusion constraints; imports/upserts keyed to vendor+rule+effective interval; never overwrite history.
- **Rollback/containment:** Deactivate erroneous version with audit, restore prior version, set gate `FAIL`; retain source artifacts.
- **Risk / review:** HIGH financial/operating risk. Claude review, Cursor schema/economics review, owner fact approval.
- **Release gate / authorization boundary:** `VENDOR_RULES=PASS` requires verified facts and owner approval; engineering may not supply missing business terms.

## PF-03 — Procurement PO ledger and open-PO reconciliation

- **Official phase/workstream:** Canonical Phase 3; ordered workstream 3.
- **Objective:** Prevent duplicate ordering by reconciling Procurement POs, Shopify import state, receipts, Incoming, cancellations, and backorders.
- **Prerequisites / current readiness:** Accepted foundation, verified inventory definitions, vendor profiles; schema scaffolding exists; zero production PO rows at current baseline.
- **In scope:** Ledger state machine, idempotent import/reference keys, receipt/backorder reconciliation, discrepancy queue, gate computation.
- **Out of scope:** Generating recommendations, finalizing or releasing a PO, direct Shopify mutation.
- **Data dependencies:** `purchase_orders`, lines, Shopify PO/import evidence, inventory Incoming, receipt records, vendor references.
- **Schema changes anticipated:** Explicit immutable external references/events, line reconciliation quantities/statuses, state transitions, uniqueness and audit constraints.
- **Business rules / human decisions:** One vendor per PO; prior open quantity reduces need; uncertain backorder/receipt state blocks affected output and routes to review.
- **Acceptance criteria:** Every relevant open line reconciles exactly or is flagged; reruns create no duplicate ledger/line; cancelled/partial/backordered cases are correct; gate is evidence-derived.
- **Deterministic test plan:** Duplicate import, partial receipt, cancellation, backorder, conflicting Incoming, retry/crash, transition authorization, vendor isolation, no release call.
- **Required control totals:** POs/lines by state, ordered/received/open/cancelled/backordered units and dollars, unmatched external references, duplicate-key count.
- **Migration/idempotency:** Append-only events or equivalently audited transitions; external identifiers unique; reprocessing is idempotent.
- **Rollback/containment:** Stop reconciliation job, preserve ledger/events, mark affected gate `FAIL`, correct via compensating event—not destructive rewrite.
- **Risk / review:** CRITICAL duplicate-spend risk. Claude review, Cursor SQL/state-machine review, owner reconciliation acceptance.
- **Release gate / authorization boundary:** `OPEN_PO_RECONCILIATION=PASS` requires owner-approved evidence. No PO creation/finalization/release is authorized.

## PF-04 — Universal price-book staging/import contract

- **Official phase/workstream:** Canonical Phase 4 — Price-book engine; ordered workstream 4.
- **Objective:** Define one strict normalized contract for every supplier file before vendor-specific parsing.
- **Prerequisites / current readiness:** Foundation and Phase 3 gates; price scaffold/seed exists, but no accepted production import engine.
- **In scope:** Immutable upload manifest, staging rows, parse/validation states, source page/row provenance, supplier matching evidence, BT/CS/EA types, CURRENT/FUTURE isolation.
- **Out of scope:** Promoting prices, permanent supplier mappings, retail changes, interpreting ambiguous terms.
- **Data dependencies:** Supplier documents, vendors/offers/aliases, existing prices, config rules, object storage.
- **Schema changes anticipated:** Upload/import/staging/error/manifests, normalized price ladder/assortment/combo fields, approval provenance, checksums and uniqueness constraints.
- **Business rules / human decisions:** No reusable operational price archive; source uploads may be retained for audit. Ambiguous SKU/pack/break/assortment goes to human review.
- **Acceptance criteria:** Contract losslessly represents Empire/Southern samples; invalid rows never reach operational prices; exact structural/error reports; rerun by checksum is deterministic.
- **Deterministic test plan:** Schema validation, cents/Decimal precision, BT/CS distinction, duplicate/conflict, CURRENT/FUTURE isolation, malicious/malformed document metadata, transaction rollback.
- **Required control totals:** Source pages/rows, parsed/staged/valid/invalid/unmapped/ambiguous rows, levels, offers, dollar/hash controls.
- **Migration/idempotency:** Import ID+checksum uniqueness; append immutable manifests; re-import returns same result; staging replacement scoped to import only.
- **Rollback/containment:** Reject/quarantine import, retain source and report, leave operational CURRENT/FUTURE untouched.
- **Risk / review:** CRITICAL pricing/mapping risk. Claude review, Cursor schema/parser-contract review, owner contract acceptance.
- **Release gate / authorization boundary:** Contract/staging work may proceed after authorization; no operational price promotion or mapping decision is included.

## PF-05 — Empire parser

- **Official phase/workstream:** Canonical Phase 4; ordered workstream 5.
- **Objective:** Parse Empire books into the universal staging contract with exact provenance and explicit uncertainty.
- **Prerequisites / current readiness:** PF-04 accepted; representative owner-provided corpus and expected fixtures available.
- **In scope:** Deterministic extraction, row/page evidence, break ladders, assortment statements, warnings, regression corpus.
- **Out of scope:** Silent OCR inference, supplier mapping approval, promotion, business-policy interpretation.
- **Data dependencies:** Empire source files and annotated expected outputs.
- **Schema changes anticipated:** None beyond contract; parser-version metadata if absent.
- **Business rules / human decisions:** `DOES NOT ASSORT` wins; pack/BT/CS ambiguity fails review; fuzzy match is supporting evidence only.
- **Acceptance criteria:** Golden corpus controls match; every source row accounted for; unsupported layout fails explicitly; zero operational writes.
- **Deterministic test plan:** Known layouts, changed headers, repeated pages, missing values, size/pack conflict, assortment conflict, Decimal arithmetic, corrupt file.
- **Required control totals:** Pages, source rows, parsed/skipped/error rows, products, ladders, costs, unmapped/ambiguous counts, checksum.
- **Migration/idempotency:** Parser version and source checksum determine stable result; repeated parse creates no duplicate staging facts.
- **Rollback/containment:** Disable parser version, quarantine affected imports, preserve prior accepted parser and raw document.
- **Risk / review:** HIGH. Claude review, Cursor parser specialist review, owner validates sample economics/terms.
- **Release gate / authorization boundary:** Parser acceptance allows staging only; mappings and price promotion remain separately authorized.

## PF-06 — Southern regular parser

- **Official phase/workstream:** Canonical Phase 4; ordered workstream 6.
- **Objective:** Parse Southern regular price-book offers into the universal contract.
- **Prerequisites / current readiness:** PF-04 accepted; representative regular-book corpus and expected fixtures available.
- **In scope:** Regular offers, ladders, packs, assortment evidence, source provenance, explicit errors.
- **Out of scope:** Combo interpretation, promotion, mapping approval, assumptions about ambiguous book conventions.
- **Data dependencies:** Southern regular files plus annotated expected results.
- **Schema changes anticipated:** Contract only; parser-version metadata as needed.
- **Business rules / human decisions:** BT/CS remains typed; same-product assortment default; explicit restrictions win; supplier SKU cannot become canonical identity.
- **Acceptance criteria:** Golden controls exact; no combo rows misclassified as regular; all rows accounted; staging only.
- **Deterministic test plan:** Layout variants, BT/CS ladders, pack conflicts, duplicates, unknown offers, pennies/rounding, corrupt/partial pages.
- **Required control totals:** Pages/rows/offers/ladders, valid/invalid/unmapped/ambiguous rows, cost totals and hashes.
- **Migration/idempotency:** Checksum+parser version stable; repeated parsing idempotent.
- **Rollback/containment:** Quarantine import/parser version; operational prices remain untouched.
- **Risk / review:** HIGH. Claude and targeted Cursor parser/economics review; owner sample verification.
- **Release gate / authorization boundary:** Staging acceptance only; no supplier decision or promotion authorized.

## PF-07 — Southern combo parser

- **Official phase/workstream:** Canonical Phase 4; ordered workstream 7.
- **Objective:** Represent whole-basket combo economics and components without automatic buying.
- **Prerequisites / current readiness:** PF-04 and Southern regular parser accepted; authoritative combo corpus and component rules supplied.
- **In scope:** Combo headers/components/quantities/costs/limits, whole-basket validation, provenance, review flags.
- **Out of scope:** Auto-add, permanent mappings, recommended quantities, operational PO lines.
- **Data dependencies:** Southern combo documents, verified offers, combo/component schema, owner interpretation for ambiguous programs.
- **Schema changes anticipated:** Strengthen combo import identity, component completeness, terms/limits, state/month and source provenance.
- **Business rules / human decisions:** Combo auto-add off; whole-basket economics mandatory; every consequential addition requires human approval.
- **Acceptance criteria:** Component sums and declared totals reconcile or block; all components map or review; no regular deal is silently replaced.
- **Deterministic test plan:** Missing/duplicate component, mixed pack, total mismatch, customer limit, CURRENT/FUTURE separation, regular-versus-combo comparison fixture, no auto-add.
- **Required control totals:** Combos/components, quantities, declared/calculated cost, unmatched components, balance differences, source pages/rows.
- **Migration/idempotency:** Import-scoped immutable combo versions; checksum idempotency; no reuse-counter mutation during parsing.
- **Rollback/containment:** Quarantine combo import and leave regular offers intact.
- **Risk / review:** CRITICAL basket/spend risk. Claude, Cursor parser/economics review, explicit owner term validation.
- **Release gate / authorization boundary:** Parsed combos remain review-only; no combo selection or PO effect authorized.

## PF-08 — CURRENT/FUTURE comparison and guarded rollover

- **Official phase/workstream:** Canonical Phase 4; ordered workstream 8.
- **Objective:** Compare structures and transactionally promote verified FUTURE on the first of the month with complete safeguards.
- **Prerequisites / current readiness:** PF-04–PF-07 accepted, full affected price/mapping coverage, backup/restore tested. Current rollover is intentionally disabled.
- **In scope:** Structural diff, readiness report, approved supplier-SKU transitions, pre-rollover backup, atomic CURRENT deletion/FUTURE promotion, post-assertions.
- **Out of scope:** Reusable monthly price archive, automatic retail price changes, guessing missing FUTURE rows, off-calendar promotion.
- **Data dependencies:** Verified CURRENT/FUTURE, offers/mappings, approvals, source manifests, backup storage.
- **Schema changes anticipated:** Transition plan/approval/audit and backup manifest; preserve only CURRENT/FUTURE operational states and exact finalized run economics snapshots.
- **Business rules / human decisions:** Promotion only on day 1, FUTURE complete, explicit transition approval, FUTURE empty afterward. Owner approves the transition/release.
- **Acceptance criteria:** Dry-run diff exact; backup restorable; transaction all-or-nothing; counts and offer/ladders reconcile; failures leave prior CURRENT intact; no archive accumulation.
- **Deterministic test plan:** Missing/unverified/extra FUTURE, date guard, mapping transition absent/unapproved, backup failure, mid-transaction failure, post-assertion failure, repeated invocation.
- **Required control totals:** CURRENT/FUTURE offers/rows/ladders/cost totals before/after, missing/extra/changed structures, approved transitions, backup hash, FUTURE residual rows.
- **Migration/idempotency:** Unique transition ID/month; one committed transition; retry observes completed state or safely resumes without a second promotion.
- **Rollback/containment:** Restore verified pre-transition backup via owner-approved runbook; disable imports/promotion; preserve audit and source documents.
- **Risk / review:** CRITICAL financial risk. Claude, Cursor SQL/transaction/pricing review, ChatGPT rule review, owner witnessed acceptance.
- **Release gate / authorization boundary:** Implementation/dry-run requires phase authorization; first production rollover is a separate owner release and is not authorized here.

## PF-09 — Forecasting V1 and FVA

- **Official phase/workstream:** Canonical Phase 5 — Forecasting V1; ordered workstream 9.
- **Objective:** Produce auditable stockout-aware demand forecasts selected by rolling backtest/FVA.
- **Prerequisites / current readiness:** Accepted sales and sufficient inventory history; verified events/collections/policies; current gate state is not ready.
- **In scope:** Demand regimes, naive/seasonal naive/damped ETS/TSB/category shrinkage, GP$ ABC+XYZ, stockout censoring, protection and bounded sensing, rolling-origin metrics.
- **Out of scope:** Auto-ARIMA production V1, opaque ML, procurement quantities, PO output, tuning to one favorable period.
- **Data dependencies:** Canonical sales, stockout/inventory history, event calendar, product/category hierarchy, current retail/cost context for ABC.
- **Schema changes anticipated:** Forecast experiment/backtest windows, candidate metrics, selected-model provenance and versions; existing results table may be extended.
- **Business rules / human decisions:** Stockout days are censored, not zero; same-period-last-year is input, not forecast; model must add value versus benchmark. Owner accepts protection/service policy.
- **Acceptance criteria:** Leakage-free rolling backtests; selected model beats or justifiably falls back to benchmark; diagnostics reproducible; sparse/intermittent/new products handled.
- **Deterministic test plan:** Synthetic known series, all-zero/intermittent/new item, stockout periods, returns/negative net items, leap/seasonal boundaries, FVA rejection, reproducibility.
- **Required control totals:** Eligible/excluded variants, history days, censored days, models evaluated/selected, WAPE/MASE/bias/FVA by segment, forecast unit totals.
- **Migration/idempotency:** Version model/config/data cutoff; same run inputs yield identical stored outputs; never overwrite prior finalized run evidence.
- **Rollback/containment:** Fall back to approved benchmark, mark model version inactive, preserve all comparison evidence.
- **Risk / review:** HIGH forecasting/inventory risk. Claude methodology review, Cursor statistical/data-leakage review, ChatGPT/owner policy acceptance.
- **Release gate / authorization boundary:** Shadow forecasts only until owner accepts backtests/FVA. No buying reliance authorized.

## PF-10 — Strategic procurement economics / break-assortment optimizer

- **Official phase/workstream:** Canonical Phase 6 — Strategic procurement; ordered workstream 10.
- **Objective:** Compute normal need and transparent marginal economics across breaks, assortments, gifts, minimums, FUTURE, and approved combos.
- **Prerequisites / current readiness:** All seven affected readiness gates pass; forecasting accepted; verified mappings/prices/vendor/ledger data. Not currently ready.
- **In scope:** Normal need, effective tier, marginal ladders, Target Cost diagnostics, immediate/bridge economics, inventory days, assortment enumeration, gift/minimum/FUTURE/combo alternatives, allocated/ONE_BOTTLE routing.
- **Out of scope:** Automatic final approval, retail changes, filler mandates, combo auto-add, routine allocated replenishment, unapproved new-item decisions.
- **Data dependencies:** Frozen run inventory/price snapshots, forecast, vendor rules, offers/mappings, open PO ledger, policies/events/combos.
- **Schema changes anticipated:** Immutable candidate/economic components and explanation provenance; finalized run economics only, not reusable price history.
- **Business rules / human decisions:** Evaluate every marginal tier; same-product assortment default; `DOES NOT ASSORT` wins; vendor minimum advisory; gift/combos/new items route to review.
- **Acceptance criteria:** Decimal math reconciles line-to-basket; baseline separated from strategic additions; constraints never silently relaxed; explanations reproduce every recommendation.
- **Deterministic test plan:** BT/CS/EA, tier dominance, holding horizon, bridge cash, assortment conflicts, minimum below threshold, gift pack, combo comparison/no-auto-add, allocated/ONE_BOTTLE, open PO subtraction, missing-gate block.
- **Required control totals:** Baseline/strategic cases and dollars, incremental cash/GP, price snapshot rows, variants by reason, constraint violations, review-required counts, vendor totals.
- **Migration/idempotency:** Immutable run inputs/results; recalculation creates versioned result or controlled replacement before finalization; finalized run snapshot fixed.
- **Rollback/containment:** Disable optimizer output, return to baseline/no recommendation, preserve run evidence; never emit final PO from incomplete run.
- **Risk / review:** CRITICAL real-money logic. Claude adversarial review, Cursor economics/constraint specialist review, ChatGPT rule review, owner scenario acceptance.
- **Release gate / authorization boundary:** Calculation/shadow authorization is separate from operational reliance. No PO creation/finalization is authorized by this packet.

## PF-11 — Human review queue

- **Official phase/workstream:** Canonical Phase 7 — Review + PO; ordered workstream 11.
- **Objective:** Present only material judgment calls and persist audited RUN_ONLY/TEMPORARY/PERMANENT decisions with recalculation.
- **Prerequisites / current readiness:** Accepted strategic recommendation engine and complete explanations; not currently ready.
- **In scope:** Queue prioritization, approve/change/reject/comment, scopes/effective dates/reversion, structured writeback, impacted-result recalculation.
- **Out of scope:** Silent global rule changes, automatic permanent mapping, PO release, unreviewed combo/new-item additions.
- **Data dependencies:** Recommendations, exceptions, mappings, policies, combo/gift candidates, actor authorization.
- **Schema changes anticipated:** Decision version/status, reviewer roles, writeback validation, supersession/reversion audit, optimistic concurrency.
- **Business rules / human decisions:** Human approval remains; permanent decisions are explicit; comments alone are not hidden rules.
- **Acceptance criteria:** Every consequential decision has actor/time/scope/reason; stale decisions cannot overwrite newer runs; recalculation is deterministic; permanent writeback constrained by decision type.
- **Deterministic test plan:** Authorization, stale/concurrent update, each scope, expiry/reversion, reject/override, mapping prohibition, recalculation impact, audit immutability.
- **Required control totals:** Queue by reason/severity, decisions by action/scope, stale/rejected, writebacks, recalculated recommendations, unresolved material items.
- **Migration/idempotency:** Decision request key prevents duplicates; append/supersede rather than edit history; repeat recalculation stable.
- **Rollback/containment:** Revoke/supersede with audited correction, recalculate, keep affected PO state non-final.
- **Risk / review:** CRITICAL human-authority boundary. Claude, Cursor auth/workflow review, ChatGPT/owner acceptance.
- **Release gate / authorization boundary:** Owner authorizes reviewer roles and production decision use; no final PO without completed authorized review.

## PF-12 — One PO/vendor, Shopify CSV, and Emergency Packet

- **Official phase/workstream:** Canonical Phase 7; ordered workstream 12.
- **Objective:** Finalize one audited Procurement PO per vendor and produce portable outputs after every gate and review passes.
- **Prerequisites / current readiness:** PF-03 and PF-10–PF-11 accepted; all affected gates `PASS`; owner approves finalization. Not currently ready.
- **In scope:** Draft/review/final state machine, one-vendor uniqueness, exact line/basket totals, Shopify-compatible CSV, emergency packet, import/receipt handoff evidence.
- **Out of scope:** Direct Shopify PO mutation unless a later separately accepted phase allows it, supplier ordering, email transmission, release without owner approval.
- **Data dependencies:** Final reviewed recommendations, run price snapshot, vendor/mapping rules, ledger/open PO state, export/storage adapters.
- **Schema changes anticipated:** Finalization authorization/signature, artifact hashes/versions, export/import status and immutable ledger linkage.
- **Business rules / human decisions:** Exactly one PO per vendor/run; routine loose restrictions; minimum advisory; strategic comments only; human final approval.
- **Acceptance criteria:** Required gates rechecked atomically at finalization; totals reconcile; duplicate finalization/import blocked; CSV contract and emergency packet reproduce frozen run.
- **Deterministic test plan:** Missing/WARN/FAIL gate, duplicate vendor/run, stale review, line rounding, loose restriction, CSV escaping/schema, artifact hash, partial storage failure, zero network release.
- **Required control totals:** Vendors/POs/lines, cases/loose units, merchandise/fees/totals, source recommendations, unmatched/dropped lines, artifact row/hash controls.
- **Migration/idempotency:** Unique run+vendor; immutable final version; repeat export produces same hash; import reference unique.
- **Rollback/containment:** Cancel unsubmitted draft/final with audit; never delete ledger; regenerate artifacts only from same frozen snapshot; reconcile any externally imported PO before retry.
- **Risk / review:** CRITICAL spend/release risk. Claude, Cursor export/ledger review, ChatGPT business review, owner explicit final approval.
- **Release gate / authorization boundary:** Output generation and especially external release require explicit owner authorization. This packet authorizes neither.

## PF-13 — Production UI, hardening, and shadow mode

- **Official phase/workstream:** Canonical Phase 8 — Production UI / hardening; ordered workstream 13.
- **Objective:** Operate the complete Monday workflow privately and prove it in shadow mode before purchasing reliance.
- **Prerequisites / current readiness:** Phases 3–7 accepted, all gates computable, backup/restore and monitoring ready. Not currently ready.
- **In scope:** Monday Run, readiness, review, price books, vendor POs, settings/policies, authentication, monitoring, backup/restore, SOP, shadow comparisons and calibration log.
- **Out of scope:** Unattended final approval, automatic business-rule changes, stable direct Shopify mutation, phase 9 model expansion.
- **Data dependencies:** All operational runs/artifacts/gates, actual owner orders/outcomes for comparison, audit/monitoring telemetry without secrets.
- **Schema changes anticipated:** Operator/run audit, shadow comparison/outcome, incident/restore verification, artifact retention metadata.
- **Business rules / human decisions:** Human final approval remains after shadow mode; any discrepancy affecting identity, price, quantity, or vendor is reviewed.
- **Acceptance criteria:** Private/authenticated deployment, tested recovery, observable jobs, no secret logs, multiple owner-defined shadow cycles with acceptable controls and documented exceptions.
- **Deterministic test plan:** End-to-end disposable run, auth/roles, job retry/resume, backup restore drill, monitoring alerts, shadow comparison, artifact integrity, no external release in shadow mode.
- **Required control totals:** Run completeness, gate states, recommendation versus actual order units/dollars, exceptions, overrides, artifact hashes, restore rows/hashes, job failures/retries.
- **Migration/idempotency:** Deployment migrations additive/transactional; job run keys prevent duplicates; restore rehearsed to a disposable environment.
- **Rollback/containment:** Disable schedules/output, deploy prior accepted build, restore verified backup, maintain emergency manual packet and incident record.
- **Risk / review:** CRITICAL operational acceptance. Claude full adversarial review, Cursor security/UX/operations specialists, ChatGPT program review, owner shadow acceptance.
- **Release gate / authorization boundary:** Owner defines shadow duration/thresholds and separately authorizes trusted production purchasing. No merge/deploy/reliance is authorized here.

## Canonical Phase 9 — Calibration / V1.5 / V2

- **Official ID/name:** Canonical Phase 9 — Calibration / V1.5 / V2.
- **Objective:** Improve only those models/automation layers whose value is demonstrated by real operating evidence.
- **Prerequisites / current readiness:** Accepted Phase 8 and sufficient owner-approved shadow/production outcomes; not ready.
- **In scope:** FVA-supported model expansion, bounded demand sensing, portfolio capital allocation, learned event lift, more automated combo comparison, potential direct Shopify PO integration.
- **Out of scope:** Technology-for-technology's-sake, weakening human approval, retroactive price archive, changes unsupported by measured value.
- **Data dependencies:** Versioned forecasts/recommendations, actual orders/receipts/sales/stockouts, decisions, exceptions, realized financial metrics.
- **Schema changes anticipated:** Experiment/calibration registry, outcome attribution and version governance; any direct integration requires separate ledger/audit design.
- **Business rules / human decisions:** Owner selects objectives/risk tolerance and approves every expansion; canonical change control required for rule changes.
- **Acceptance criteria:** Predeclared evaluation shows material improvement without worse safety/control performance; rollback and shadow comparison proven.
- **Deterministic test plan:** Reproducible historical evaluation, challenger-versus-champion, leakage checks, stress/adversarial scenarios, integration sandbox tests.
- **Required control totals:** FVA/financial/availability metrics, cash exposure, error/bias, overrides/exceptions, mapping and PO reconciliation accuracy.
- **Migration/idempotency:** Version every model/policy and preserve prior results; migrations remain additive/audited.
- **Rollback/containment:** Champion fallback, feature flag/kill switch, no direct-production mutation until separately released.
- **Risk / review:** HIGH to CRITICAL by feature. Independent review intensity and targeted specialists are selected per proposal.
- **Release gate / authorization boundary:** Each Phase 9 proposal requires a new owner-approved design, plan, validation packet, and explicit release; none is pre-authorized.

## Required sequencing

1. Close Foundation Phase 4 through owner identity review.
2. Formally accept Foundation Phases 5 and 6 with CI and independent-review proof.
3. Execute PF-01 through PF-13 in the repository's ordered dependency sequence, stopping at every phase/release boundary.
4. Consider canonical Phase 9 only after measured shadow/operating evidence exists.

Task packets may be refined with verified evidence, but no packet may be marked complete merely because code or scaffolding exists.
