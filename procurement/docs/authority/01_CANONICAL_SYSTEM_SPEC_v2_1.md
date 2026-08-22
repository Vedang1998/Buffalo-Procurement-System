Buffalo House Procurement OS — Canonical System Specification v2.1

Date: August 9, 2026
Status: Current authoritative product/business specification
Audience: AI reviewers, Replit Agent, Codex, Claude Code, future engineers, and the owner/operator
Assumption: Reader has ZERO knowledge of any prior conversation.

────────

A. What we are trying to build

Buffalo House Liquor & Wines is building a permanent internal procurement operating system that replaces the practical purchasing-planning role formerly served by Shopify Stocky, but with substantially better economics, demand reasoning, supplier-deal interpretation, auditability and human review.

This is not a generic SaaS product and not an AI-chat experiment. It is an internal operating system for one real wine-and-spirits retailer. Real inventory cash will eventually be committed from its recommendations.

The owner should ultimately experience the system as simple:

Most days: no procurement work. Shopify continues to run POS/online sales and operational inventory. Buffalo Procurement quietly maintains its structured data.

Monday: sync → freeze facts → forecast → calculate baseline need → evaluate supplier economics → surface only genuine judgments → owner approves/changes → recalculate → one PO per vendor → Shopify-compatible PO output + emergency packet.

15th–20th: upload each distributor’s next-month book → deterministic parsing/matching/validation → review only true ambiguities/structural changes → VERIFIED FUTURE pricing.

1st: guarded monthly rollover → FUTURE becomes CURRENT → approved permanent supplier-SKU transitions activate → assertions run.

The design must become easier to operate over time because every approved alias, policy, exception and explanation becomes structured system knowledge rather than a question asked again next week.

────────

B. Business context and operating assumptions

• Business: Buffalo House Liquor & Wines.
• Retail category: wine and spirits; no beer.
• Commerce/POS: Shopify POS + Shopify online.
• Store opened November 28, 2024.
• Multiple distributors; important current examples include Empire Merchants North and Southern Glazer’s.
• Each distributor always gets its own purchase order.
• Normal order rhythm is commonly Monday after hours, supplier processing Tuesday, receipt often Wednesday, but actual vendor calendars/lead-time variability must be stored per vendor rather than hard-coded globally.
• The application is intended to be browser-accessible and maintainable by the owner with AI coding help rather than a full-time engineering team.

────────

C. North Star and decision philosophy

Maximize sustainable gross-profit return on inventory cash while protecting important in-stock availability and minimizing unnecessary inventory investment.

Operational shorthand:

> **More Margin. Less Cash. Faster Turn. Profit Sooner.**

The system is explicitly NOT designed to maximize margin percentage, chase the deepest discount, or blindly minimize inventory. It must jointly reason about:

• availability/service risk;
• gross-profit dollars;
• gross-margin percentage;
• inventory cash deployed;
• incremental cash required to reach a supplier break;
• time to consume incremental inventory;
• forecast uncertainty;
• vendor lead time and reliability;
• current versus future supplier economics;
• case/pack/assortment constraints;
• human knowledge of events, allocations, gift packs, combos and unusual purchasing conditions.

Target Cost remains a diagnostic/hurdle:

Target Cost = Retail × (1 − target margin)

Working targets:

• Wine: ~33% margin.
• Non-wine spirits: ~25% margin.

Target Cost must never become a reason to overbuy, and the system never changes retail selling prices automatically.

────────

D. Permanent technology direction

The permanent operating application is Replit-centered:

• browser application / frontend;
• Python/FastAPI backend;
• deterministic procurement engine;
• Replit production PostgreSQL as the structured system of record;
• Replit App Storage for supplier books, import artifacts, parser fixtures, PO files, emergency packets and operational backups;
• Replit Autoscale web/API initially;
• Replit scheduled/batch execution for Monday runs, snapshots, backups and monthly jobs;
• GitHub mirror for source control and independent review;
• one small encrypted off-platform disaster backup for irreplaceable data;
• direct Shopify Admin API / ShopifyQL integration.

Sidekick is deliberately excluded.

No mandatory runtime LLM. Forecasting, safety stock, BT/CS, case breaks, Target Cost, current/future economics, assortment optimization, GP/capital metrics and PO quantities are deterministic/statistical software.

AI is used as an engineering/review accelerator and exceptional manual document compiler, not as a hidden source of business truth.

Examples of allowed AI use:

• Replit Agent, Codex or Claude Code to write/refactor/test code;
• ChatGPT/Claude manually compiling an unusually difficult price book into a strict template;
• a manual second-opinion review packet for unusual weekly recommendations.

AI-produced supplier data never goes directly to active pricing. It must pass the same deterministic staging/validation/evidence controls as any other import.

────────

E. Existing data and the identity problem

Recovered prototype/seed data includes approximately:

• 2,029 historical/current variant seed rows;
• 3,301 historical alias rows;
• 662 current variants known to have prior Shopify IDs in the historical prototype;
• 4 vendors;
• 85 supplier offers;
• 271 August price levels;
• 263 verified / 8 unverified August price rows in the current seed package.

A live connected Shopify probe on August 9, 2026 returned 2,003 current variants. The difference from the old 2,029 seed is not itself an error. Products may have been recreated, retired, consolidated, changed or newly added.

Shopify historical analytics have already shown rows where historical product_variant_id can be 0/null while historical SKU/title-at-time-of-sale remains present. Therefore history cannot be safely modeled by assuming today’s Variant IDs existed forever.

The canonical identity is the current Shopify Variant ID. Historical IDs resolve through approved aliases. Supplier SKU, barcode, title and fuzzy similarity are evidence, not canonical identity.

Identity changes are financial-risk events because a wrong old→new merge can attach another product’s sales history to the current product and corrupt forecasting. Therefore the system is intentionally fail-closed on ambiguous identity recreation.

────────

F. Source-of-truth model

Shopify owns operational commerce facts: current catalog identity, current variant attributes, retail price, current inventory states, sales analytics, collections, incoming/transfer/receiving evidence where available, and native receiving after PO import.

Procurement PostgreSQL owns procurement intelligence: historical identity aliases, supplier mappings, supplier offers, packs, assortment programs, CURRENT/FUTURE pricing, raw and canonical historical sales facts, inventory snapshots, vendor operating profiles, forecasts, deal economics, events, gift packs, combos, one-bottle policies, review decisions, procurement PO ledger, readiness gates and run snapshots.

Chat history owns nothing operationally important. A deleted AI conversation must not delete an accepted rule, mapping or explanation.

────────

G. Human intelligence that must NEVER disappear

The following are first-class structured concepts, not informal notes:

• gift packs and their temporary supplier SKUs;
• combo programs and monthly usage restrictions;
• same-product assortment default;
• explicit cross-product assortment groups when supplier evidence allows them;
• book-stated DOES NOT ASSORT restrictions;
• BT versus CS break units;
• Shopify sellable units per case versus supplier qualifying units per case;
• Target Cost diagnostics;
• Profit Driver / Filler / Bridge Cash / full qualifying basket logic;
• immediate purchase savings versus bridge benefit versus projected run-rate GP;
• one-bottle stocking policies;
• allocated-bourbon routine-replenishment exclusion;
• vendor minimum and fee logic;
• loose/broken-case fee rules;
• temporary expected price reversion and expiry date;
• tastings, promotions, launches, holidays, sports events, bulk-customer events;
• run-only, temporary and permanent human decisions;
• rejected/negative supplier mappings so bad suggestions are not repeated;
• approved supplier-SKU transitions;
• operator comments and evidence.

Nothing above is allowed to exist only in an AI’s memory.

────────

H. Ready-state operating workflow

H1. Everyday / ordinary days

No manual procurement action is required. The application can maintain snapshots and health checks. Shopify remains the daily POS/inventory workspace.

H2. Monday procurement run

The exact sequence is:

1. Sync live Shopify catalog.
2. Reconcile current canonical identities.
3. Refresh sales through the Monday cutoff.
4. Pull Available, Incoming and useful inventory states.
5. Pull collections and inventory/adjustment evidence where available.
6. Freeze the Monday inventory/run snapshot for audit.
7. Reconcile open Procurement PO lines, receipts, incoming and possible backorders.
8. Validate supplier mappings, supplier SKUs, packs, CURRENT pricing and assortment constraints.
9. Route Allocated Bourbon and ONE_BOTTLE policies.
10. Classify demand regime.
11. Forecast demand using the approved V1 model family.
12. Apply stockout-censoring, event context and empirical protection.
13. Compute normal replenishment need.
14. If FUTURE exists, compare current-versus-future timing strategy using this Monday’s inventory and forecast.
15. Evaluate every relevant BT/CS break marginally, not merely baseline versus deepest tier.
16. Optimize approved assortment groups.
17. Identify Profit Drivers, legitimate Fillers and Bridge Cash.
18. Evaluate gift-pack alternatives.
19. Apply vendor-minimum warnings/fees without auto-buying junk filler.
20. Compare relevant combo alternatives; combo auto-add remains OFF.
21. Create a small Review Queue consisting only of real judgment calls.
22. Human approves/changes/rejects/comments.
23. Persist the decision by RUN_ONLY / TEMPORARY / PERMANENT scope.
24. Recalculate affected recommendations.
25. Optionally rework to an ad-hoc weekly budget; preserve availability first and cut/defer discretionary strategic inventory first.
26. Finalize exactly one Procurement PO per vendor.
27. Generate Shopify-compatible PO CSV.
28. Generate Emergency Packet.

The system should eventually turn a 2,000-variant purchasing job into perhaps a handful of meaningful decisions.

H3. Monthly price-book workflow (15th–20th)

For each vendor book:

UPLOAD → RAW FILE → PARSER/EXTRACTION → STAGING → SUPPLIER MAPPING → VALIDATION → CURRENT-vs-FUTURE STRUCTURAL DIFF → HUMAN EXCEPTIONS → VERIFIED FUTURE

CURRENT must remain untouched while FUTURE is being prepared.

The deterministic parser is preferred. A difficult/new format may use a manual ChatGPT/Claude compilation into the exact same strict normalized import contract. Replit independently validates the result; AI is candidate compiler, not authority.

After FUTURE exists, every Monday run re-evaluates whether buying earlier/later makes economic sense. A price increase does not automatically justify a giant forward buy.

H4. First-of-month rollover

In one guarded transaction:

1. confirm FUTURE completeness/readiness;
2. create pre-rollover backup;
3. remove old operational CURRENT rows;
4. promote verified FUTURE to CURRENT;
5. ensure FUTURE is empty;
6. activate only explicitly approved permanent supplier-SKU transitions;
7. run post-transition assertions.

There is no reusable monthly price archive in V1. Exact economics actually used by a finalized run are retained in run-price snapshots for reproducibility.

────────

I. Supplier price and deal reasoning

The engine does not simply ask “what is the cheapest unit cost?” It reasons from baseline need outward.

For every supplier program:

1. calculate baseline/normal need;
2. determine the current effective tier;
3. evaluate the next deeper tier;
4. evaluate each subsequent tier marginally;
5. stop when additional cash/holding time no longer justifies additional benefit.

Terminology:

• Profit Driver: item creating a meaningful share of the economic benefit.
• Filler: legitimate assortable item used to reach a break efficiently. It cannot be chosen merely because it is cheap.
• Bridge Cash: inventory cash above baseline need required to reach a better tier.
• Full Qualifying Basket: complete set of cases/units that qualifies for the program.

Metrics must distinguish:

• Immediate purchase savings on units bought now;
• Incremental bridge benefit attributable to buying additional inventory now;
• Projected run-rate GP, contextual and not realized cash savings;
• GP per $100 Bridge Cash;
• Expected incremental inventory days/depletion time;
• Time-adjusted return, e.g. incremental GP divided by inventory cash × expected months held;
• GMROI/capital productivity as complementary context.

A deep deal with excellent GP/$100 can still be bad if it produces excessive inventory days, slow seasonal tail, bad filler mix or poor cash timing.

────────

J. Forecasting strategy

V1 deliberately avoids “forecasting Olympics.” Keep enterprise ideas that add measurable Forecast Value Added, not complexity for prestige.

Demand regimes:

• smooth/regular;
• seasonal;
• intermittent/lumpy;
• new/thin history;
• event-distorted;
• stockout-censored;
• trending when evidence supports it.

Initial model family:

• damped ETS for regular demand;
• seasonal naive + category seasonality where appropriate;
• TSB for intermittent/lumpy demand;
• category/analog shrinkage for new or thin-history products;
• naive fallback.

Rolling-origin backtesting remains mandatory. Track WAPE where meaningful, MAE/MASE, bias and decision/service consequences. A more complex model replaces a simpler baseline only when improvement is material.

ABC should primarily reflect trailing gross-profit dollars, with FORCE_A/MUST_STOCK for strategically important traffic items. XYZ reflects predictability. Service importance and capital attractiveness are separate questions.

Stockout days are censored, not ordinary zero demand. Use Shopify evidence plus our own snapshots. Do not turn one day of availability and two sales into a fabricated 60-unit monthly forecast. Low in-stock coverage lowers confidence.

Recent 7/14/28-day momentum, category movement, season transition and known events can modify the base forecast, but must be damped/bounded so one tasting or bulk customer does not permanently redefine demand.

New products get no last-year-zero penalty.

────────

K. Assortment, gift packs and combos

Assortment default: within the same Shopify product only. This is a business default, not a claim that Shopify merchandising structure defines every supplier program. Explicit supplier-program assortment groups can cross products only with evidence/approval. If the book says DOES NOT ASSORT, it wins.

BT vs CS: typed separately. CS counts qualifying cases; BT counts qualifying bottles/units. A 6-bottle case and 12-bottle case both count as one CS in a CS program but contribute 6 vs 12 qualifying units in a BT program when that is the supplier rule.

Store both:

• Shopify sellable units per case;
• supplier qualifying units per case;
• break unit.

Gift packs: separate supplier offer, potentially same canonical sellable Variant ID. Gift-pack SKU never overwrites the normal Shopify SKU automatically. Large/deep gift-pack forward buys require human review.

Combos: auto-add OFF. Compare whole-basket economics: target savings, companion cash, companion inventory/velocity/days, new-item exposure, total GP and capital. Human approves.

────────

L. Inventory position, one-bottle, allocations and backorders

Baseline concept:

Normal Need = Order-up-to Target − Effective Inventory Position

Effective inventory position includes Available + trusted Incoming and relevant known commitments/reservations not already reflected. Policy layers then apply case rounding, minimum presentation stock if configured, ONE_BOTTLE, allocated exclusion, etc.

ONE_BOTTLE is a true policy override:

• Available + trusted Incoming >= 1 → buy 0;
• <= 0 → recommend one loose bottle + vendor fee;
• unusual growth triggers temporary human review, not silent permanent policy mutation.

Allocated Bourbon collection is excluded from normal auto-replenishment; allocation is manually handled.

Backorder/receipt reconciliation uses strongest direct evidence first. Conceptually:

A1 = A0 + Receipts + Other Adjustments − Sales

Open Procurement PO lines are retained in our own PO ledger so a prior line cannot be silently duplicated merely because Shopify currently shows Available=0 and Incoming=0.

────────

M. Review intelligence

Human decisions have explicit scope:

• RUN_ONLY — one run;
• TEMPORARY — effective/expiry/reversion;
• PERMANENT — alias/policy/etc.

A single approval never mutates a global rule unless the user explicitly chooses permanent scope.

Review should be tiered:

• BLOCKER: unsafe data/identity/pricing condition; prevents affected final PO.
• HUMAN REVIEW: economically/materially ambiguous decision.
• INFORMATIONAL: useful warning that does not need approval.
• SILENT AUTO-HANDLING: deterministic routine work.

Review burden must decrease over time, not become permanent exception fatigue.

────────

N. Fail-closed readiness gates

No trusted final PO for affected scope while a required gate is FAIL.

Core gates:

1. CATALOG_SYNC
2. SALES_BACKFILL
3. INVENTORY_HISTORY
4. VENDOR_RULES
5. PRICE_COVERAGE
6. MAPPING_INTEGRITY
7. OPEN_PO_RECONCILIATION

The immediate foundation gates are CATALOG_SYNC and SALES_BACKFILL.

────────

O. Fast implementation strategy

Because the code is being AI-assisted, engineering tasks can be parallelized aggressively:

• Replit Agent can build UI/integration scaffolding;
• Codex can independently inspect algorithms/tests/migrations;
• Claude Code can independently inspect safety/data-integrity/edge cases;
• GitHub gives all agents one inspectable source history.

However, parallel coding does not mean parallel authority. Identity merges, supplier mappings, BT/CS interpretations and promotion of price data remain guarded.

It is acceptable to scaffold later phases before the first two gates are green, but real-money recommendation enablement must remain gated.

────────

P. Current implementation status

Current v1.3 code already includes PostgreSQL schema, hard rules, seed migration, Shopify authentication/client, catalog reconciliation, historical ShopifyQL sales backfill logic, readiness gates, assortment/pricing/economics foundations and automated tests.

What is not yet truthfully complete: applying the schema to the actual Replit production database, importing seed into that database, executing the complete current catalog reconciliation there, resolving live identity blockers, executing the full historical sales backfill there, and turning the first two readiness gates green.

That is the immediate next milestone.

────────

Q. Master plan details

The following Master Plan text remains part of this specification. Where the explanatory sections above restate it, they are intended to clarify, not replace, the detailed rules below.

Detailed Master Plan v2.0 Incorporated into Canonical v2.1

Locked: August 9, 2026
Status: Production architecture accepted; construction resumed
Supersedes: Master Plan v1.1 for architecture/build sequencing. Historical files remain evidence, not current implementation authority.

1. North Star

Maximize sustainable gross-profit return on inventory cash while protecting important in-stock availability and minimizing unnecessary inventory investment.

Operational shorthand:

> **More Margin. Less Cash. Faster Turn. Profit Sooner.**

Every vendor always receives its own purchase order.

The system must never optimize one number in isolation. Margin %, gross-profit dollars, inventory cash, time-to-sell, service risk, supplier constraints, and uncertainty must be considered together.

────────

2. Permanent architecture

Buffalo Procurement is a standalone browser application hosted on Replit.

Replit owns the Procurement application infrastructure

• Browser UI / application frontend.
• Python / FastAPI backend.
• Deterministic procurement engine.
• Replit production PostgreSQL as the structured system of record.
• Replit App Storage for distributor books, import files, generated PO files, parser fixtures and operational exports.
• Replit Autoscale deployment for the web UI/API initially.
• Replit Scheduled Deployments for batch work such as Monday runs, inventory snapshots, backups and scheduled maintenance.
• Replit private/password-protected publishing for the internal application.

A Reserved VM is not required initially. Move to one only if measured production behavior proves a need for continuous guaranteed compute.

Portability and disaster protection

Replit is the operating home, but the code and irreplaceable data must not be trapped there.

• GitHub mirrors the source repository.
• Domain logic uses normal Python/PostgreSQL interfaces rather than Replit-specific APIs.
• Database schema changes use migrations.
• Storage access is behind a small adapter.
• Scheduled job entry points are ordinary CLI commands.
• Replit App Storage keeps fast operational backups.
• At least one encrypted off-platform backup copy is maintained as disaster protection.

This off-platform backup is an intentional exception to the “keep the operating stack in one place” goal because a backup in the same account cannot protect against losing the account/platform.

AI is outside the production trust boundary

There is no mandatory runtime LLM.

Normal procurement must continue to work if OpenAI, Anthropic and every other AI model is unavailable.

AI may be used for:

• development with Codex, Claude Code or Replit Agent;
• manually compiling an unusually difficult distributor book into the strict import format;
• optional human second-opinion review packets.

AI never has authority to promote supplier data into VERIFIED FUTURE pricing or directly approve procurement decisions.

────────

3. Source-of-truth hierarchy

Shopify is operational commerce truth

Shopify supplies:

• current Product and Variant identity;
• Variant ID;
• product/variant title;
• SKU and barcode;
• retail selling price;
• Shopify inventory-item cost as a current reference value;
• current inventory states;
• sales analytics/history;
• collections;
• incoming inventory / transfers where available;
• receiving and native purchase-order workflow after CSV import.

Procurement PostgreSQL owns procurement intelligence

Procurement OS owns:

• current canonical identity reconciliation;
• historical Variant-ID aliases;
• raw historical sales facts and their canonical resolution;
• supplier mappings and negative/rejected mappings;
• supplier offers and packs;
• assortment programs;
• CURRENT/FUTURE supplier pricing;
• price-book staging and validation;
• forecasting results;
• inventory snapshots;
• vendor operating rules;
• strategic deal economics;
• gift-pack intelligence;
• combo intelligence;
• events and human explanations;
• ONE_BOTTLE and other product policies;
• review decisions;
• procurement-side PO ledger;
• run snapshots and the exact economics used to make each recommendation.

Chat history is never system memory

A deleted chat must never erase an operational rule, accepted mapping or human explanation.

────────

4. Shopify application connection

Buffalo Procurement uses a merchant-owned Shopify Dev Dashboard app.

For a store and app owned by the same Shopify organization, use Shopify’s client credentials grant:

1. store Client ID and Client Secret only in deployment secrets;
2. exchange them server-to-server for an access token;
3. cache the token only until shortly before expiry;
4. refresh automatically; Shopify client-credentials tokens are approximately 24-hour tokens;
5. never expose the secret in frontend code or Git.

Initial scopes should be read-only and minimal. Likely foundation scopes include:

• read_products
• read_inventory
• read_reports
• relevant location scope if required by the final query surface

Order scopes are added only if the order API is genuinely needed beyond ShopifyQL analytics. If the application uses raw orders older than 60 days, read_all_orders is a separate requirement. The preferred historical sales foundation is ShopifyQL sales, which has already been successfully probed against Buffalo House history from store opening.

Write scopes are not added simply because the app might someday change SKUs. Any future Shopify write capability should be deliberately enabled and separately guarded.

────────

5. Build-order readiness gates

The historical Procurement OS correctly treated data foundations as hard gates. v2.0 generalizes that pattern.

A readiness_gates layer uses PASS / WARN / FAIL and supports global, vendor or item scope.

No final PO output is trusted while a required gate is FAIL.

Foundation gates

1. CATALOG_SYNC — current Shopify identity is reconciled.
2. SALES_BACKFILL — historical sales have been canonicalized with every material identity either resolved to a proven canonical Variant ID or covered by a proven owner-approved terminal exclusion.
3. INVENTORY_HISTORY — own snapshots are running; Shopify inventory analytics are tested and used where reliable.
4. VENDOR_RULES — order cycles, lead times, delivery/minimum/loose-fee rules needed for calculation are present.
5. PRICE_COVERAGE — affected supplier offers have verified CURRENT pricing before strategic price-aware decisions are made.
6. MAPPING_INTEGRITY — supplier mappings used by a PO are verified.
7. OPEN_PO_RECONCILIATION — relevant prior Procurement POs/incoming evidence are reconciled before duplicate quantities are proposed.

Routine catalog products lacking supplier pricing may still be visible, but price-aware deal logic for those items must fail closed rather than invent costs.

────────

6. Phase 1 — Catalog identity foundation

The August prototype seeded:

• 2,029 current-variant identity rows;
• 3,301 historical alias rows;
• 662 current variants known to have prior Shopify IDs.

Those rows are migration seeds, not current truth.

A live Shopify probe on August 9, 2026 returned 2,003 current product variants, proving that the seed cannot simply be copied forward without reconciliation.

Canonical identity

The canonical product identity is the current Shopify Variant ID.

Supplier SKU is not identity.

Barcode is not identity.

Title is not identity.

Historical Variant IDs resolve to the current canonical Variant ID through variant_aliases.

Catalog reconciliation behavior

For each live Shopify variant:

• exact known current Variant ID → refresh current attributes;
• genuinely new Variant ID with no identity collision → create a new canonical variant;
• missing old current Variant ID → do not silently delete history;
• new variant sharing strong identity evidence with a missing old variant → create a POTENTIAL_RECREATION review item, never auto-merge;
• inactive/archived current variant → retain historical identity but exclude from normal replenishment unless policy says otherwise.

Strong recreation evidence can include exact SKU, exact barcode, exact normalized title/size and product relationship, but merging IDs always requires deterministic certainty or human approval.

Every approved recreation creates a historical alias so old sales remain attached to the new canonical Variant ID.

Catalog acceptance

CATALOG_SYNC does not pass merely because row counts are similar. It passes when:

• every live current variant has a known canonical identity;
• missing previously-current IDs have been classified;
• recreation candidates have been resolved;
• no two live canonical variants accidentally claim the same historical identity;
• the catalog snapshot is timestamped and auditable.

────────

7. Phase 2 — Historical sales foundation

Historical sales are required before production forecasting.

Preferred source: ShopifyQL sales analytics

The current connected Shopify schema has already been tested successfully back to Buffalo House’s opening date (November 28, 2024).

Use ShopifyQL sales with dimensions including:

• day;
• product_variant_id;
• product title at time of sale;
• variant title at time of sale;
• SKU at time of sale;

and metrics including:

• net_items_sold;
• net_sales.

This has two important advantages:

1. it already reflects sales reversals/adjustments in Shopify’s analytics rather than requiring us to hand-reconstruct every refund from raw orders;
2. it retains SKU/title-at-time-of-sale even on historical rows whose product_variant_id is null/zero.

Raw first, canonical second

Do not write ShopifyQL rows directly into final sales_daily.

Store raw daily source facts first:

shopify_sales_daily_raw

including:

• source date;
• source Variant ID when supplied;
• source SKU;
• product/variant title at time of sale;
• net units;
• net sales;
• fetch batch;
• canonical Variant ID after resolution;
• resolution method;
• resolution status.

Then aggregate resolved facts into canonical sales_daily.

This allows aliases to be corrected and sales to be re-resolved without re-querying Shopify.

Resolution hierarchy

1. Current Variant ID exact.
2. Historical old Variant ID exact through approved alias.
3. Approved historical alias using exact historical SKU + normalized product/variant identity.
4. Otherwise unresolved/ambiguous → review.

A fuzzy match is evidence for a human mapping decision, not permission to rewrite years of demand history automatically.

Historical rows with Variant ID 0/null

These are expected and already observed in live ShopifyQL output.

Use SKU/title-at-time-of-sale against approved aliases. If still unresolved, route to review.

Terminal historical-identity exclusion

After exhaustive review, the owner may explicitly exclude a genuine historical
sale from canonical Variant-level attribution when no safe canonical Shopify
Variant ID can be established. The terminal reason is
HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW. This disposition
does not delete or alter the immutable source fact, does not invent a canonical
identity, and does not contribute to per-Variant sales_daily. Source identity,
units, sales, reversals and forensic evidence remain permanently auditable, and
reason-coded excluded net and absolute units/sales remain part of control-total
reporting. It can never make a product eligible for forecasting, replenishment,
procurement or a purchase order.

Future unresolved identities remain fail-closed and may not be automatically
converted to a terminal exclusion. For Phase 4, the only allowlisted exclusion
reasons are PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION and
HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW.

Pagination

ShopifyQL results are paged deterministically using LIMIT + OFFSET and stable ORDER BY, preferably in date chunks so an interrupted backfill can resume.

Sales acceptance

SALES_BACKFILL passes only when:

• the requested historical period is complete;
• every material unit/revenue row is resolved or has an effective owner-approved EXCLUDE ledger decision for its exact source key, an allowlisted structured reason, required manifest/evidence provenance and no canonical target;
• a trusted exclusion-integrity computation reconciles complete reason-coded excluded membership, rows, net/absolute units and net/absolute sales, with the original exact eight exclusions separately identifiable;
• aggregates reconcile to ShopifyQL control totals within defined tolerances;
• re-running the same batch is idempotent;
• canonical sales can be regenerated from raw facts.

An EXCLUDED status by itself is never sufficient readiness evidence. Missing,
unprovenanced, unknown-reason, target-bearing, scope-mismatched or financially
unreconciled exclusions keep SALES_BACKFILL failed.

────────

8. Phase 3 — Inventory history, incoming and vendor operating rules

Own daily inventory snapshots

Regardless of what Shopify exposes analytically, capture our own daily snapshot going forward:

• Variant ID;
• location;
• available;
• incoming;
• on hand if used;
• committed/reserved states where useful;
• timestamp.

This creates an independent history for stockout censoring and reconciliation.

Shopify inventory analytics

Shopify currently documents ShopifyQL inventory and inventory_adjustment_history. Test actual Grow-store access before depending on them.

Where available, use adjustment reason, inventory state and reference-document fields to strengthen receipt/backorder investigation.

Own snapshots remain in place even when ShopifyQL works.

Vendor operating profile

Per vendor store:

• order days/cutoffs;
• expected delivery days;
• measured lead time;
• lead-time variability / reliability;
• case/dollar minimum;
• fee if below minimum;
• loose/broken-case fee;
• vendor-specific special rules;
• optional holiday/blackout delivery notes.

The original default $3 loose fee is a historical placeholder only. Use vendor-specific verified rules.

────────

9. Supplier identity and SKU strategy

Matching hierarchy:

1. vendor + supplier SKU exact;
2. previously approved supplier alias;
3. deterministic entity resolution using normalized brand/title, size, pack, style/type, proof/ABV, vintage where material, barcode and vendor;
4. fuzzy/token similarity only as supporting evidence;
5. material ambiguity → human review.

Once a hard mapping is approved, store it so the same question should not return every month.

Negative mappings

Store rejected candidate mappings too.

Example:

> Empire 4471 is **not** Variant 998877.

Otherwise the matcher will repeatedly propose the same bad candidate and review burden will not decline.

Supplier SKU changes

Detected FUTURE supplier SKU changes are staged and classified as one of:

• permanent supplier SKU replacement;
• temporary offer SKU;
• gift-pack SKU;
• combo SKU;
• alternate-pack SKU;
• possible different product / unresolved.

Never let a temporary/gift/combo SKU overwrite normal Shopify identity.

A Shopify SKU field update is a deliberate separate action, not an automatic consequence of supplier-book ingestion.

────────

10. Monthly price-book architecture

Universal normalized import contract

All price sources produce the same strict normalized record format.

Producer examples:

• Empire deterministic parser;
• Southern regular-book parser;
• Southern combo parser;
• spreadsheet adapter;
• manual entry;
• ChatGPT/Claude manual compilation fallback.

Everything downstream sees the same structure.

Core fields include:

• import batch;
• effective month;
• vendor;
• supplier SKU;
• supplier description;
• canonical Shopify Variant ID;
• package type;
• size;
• raw pack;
• Shopify units per case;
• qualifying units per case;
• assortment eligibility/scope/group;
• level type;
• break quantity;
• break unit;
• case price;
• unit price;
• source file/page;
• extraction confidence;
• review note.

Staging lifecycle

RAW FILE → EXTRACTED/STAGING → MAPPED → VALIDATED → STRUCTURAL DIFF → HUMAN EXCEPTIONS → VERIFIED FUTURE

No upload may write directly into CURRENT.

Promotion into FUTURE is transactional and fail-closed.

Deterministic validation

At minimum validate:

• known vendor;
• valid Variant ID;
• valid supplier mapping;
• nonzero sensible numeric prices;
• explicit BT vs CS for breaks;
• pack consistency;
• case/unit price plausibility;
• no duplicate ladder row;
• structural ladder consistency;
• assortment rule validity;
• changed SKU/package classification;
• expected source coverage.

AI fallback safety

If a book defeats the deterministic parser, the application exports:

• source PDF;
• mapping reference;
• strict template.

The owner may use a high-end ChatGPT/Claude subscription to populate the template manually.

On import, Replit treats every AI-produced row as candidate data.

Where a text layer exists, independently verify that claimed supplier SKU and price values actually occur on the cited source page. A failed evidence check becomes REVIEW, never VERIFIED.

────────

11. CURRENT / FUTURE price lifecycle — no reusable archive

The explicit current rule remains:

• operational supplier pricing has CURRENT and FUTURE only;
• do not reintroduce a reusable monthly price archive in V1.

15th–20th

• upload next-month books;
• validate into FUTURE;
• leave CURRENT untouched.

Every Monday once FUTURE exists

Reevaluate transition strategy using that Monday’s current:

• sales;
• stock;
• incoming;
• forecast;
• strategic economics.

Do not freeze a one-time forward-buy decision on upload day.

1st

In a guarded transaction:

1. verify FUTURE completeness;
2. take a pre-transition backup;
3. delete old operational CURRENT rows;
4. change verified FUTURE → CURRENT;
5. ensure FUTURE is empty;
6. activate only explicitly-approved supplier-SKU transitions;
7. run assertions.

Historical reproducibility without a price archive

Every finalized run stores a run_price_snapshot containing the exact offer/ladder economics actually used in that run.

This is an audit/reproducibility record, not a reusable historical monthly price-book warehouse.

────────

12. Price-ladder structural comparison

Do not compare only price numbers.

Example:

CURRENT:

• 12 BT = $11
• 36 BT = $10
• 60 BT = $9.33

FUTURE:

• 12 BT = $10
• 36 BT = $9.33

The engine must report that the $9.33 tier moved from 60 BT to 36 BT.

Detect:

• price increase/decrease;
• threshold shallower/deeper;
• break added;
• break removed;
• break unit changed BT↔CS;
• pack changed;
• supplier SKU changed;
• assortment changed;
• gift pack appeared/disappeared;
• new/disappearing offer;
• mapping ambiguity.

Always report the structural change first. Human explanation of why comes second.

────────

13. BT vs CS is a typed business concept

Never allow bare quantities to blur their unit.

CS

A threshold of 5 CS means five qualifying cases. A 6-bottle case and a 12-bottle case each contribute one case when the program is case-qualified.

BT

A threshold of 60 BT means sixty qualifying bottles/units. Cases convert using the supplier offer’s explicit qualifying-unit count.

Store separately:

• Shopify sellable units per case;
• qualifying units per case;
• break unit.

In code, prefer unit-typed values (Cases, Bottles/QualifyingUnits) so invalid addition/conversion cannot silently occur.

────────

14. Assortment

Default:

> **Same Shopify product only.**

A supplier-program assortment group may intentionally cross Shopify products only when:

• the supplier book explicitly permits it; or
• the owner approves and records the exception.

Examples such as some Stella Rosa or Arbor Mist flavor programs are rare explicit exceptions.

If the book says DOES NOT ASSORT, that wins.

There is no 50ML/100ML blanket exception.

Assortment evidence should retain source book/page where practical.

────────

15. Gift packs

Gift packs are separate supplier offers that may map to the same canonical sellable Variant ID as the normal bottle.

Store independently:

• supplier SKU;
• package type GIFT_PACK;
• pack;
• ladder;
• assortment eligibility;
• effective dates;
• source evidence.

Gift-pack supplier SKU never automatically replaces normal Shopify SKU.

A special gift-pack ladder can create an unusually good forward-buy opportunity, but every large gift-pack buy remains human-reviewed.

────────

16. Combos

Combo auto-add remains OFF.

Before finalizing a relevant normal deal, compare it with valid combo alternatives.

Whole-basket analysis includes:

• target-product savings;
• companion cash;
• companion inventory;
• companion forecast/velocity;
• companion days of supply;
• new-item exposure;
• total incremental GP;
• total capital.

V1 may present deterministic side-by-side economics rather than solve every combo through a global optimizer.

The owner approves any combo.

Monthly combo SKU usage restrictions are recorded structurally.

────────

17. Forecasting V1 — enterprise ideas without forecasting theater

Keep demand-regime classification and backtesting, but reduce unnecessary model competition.

Demand regimes

• smooth/regular;
• seasonal;
• intermittent/lumpy;
• new/thin-history;
• event-distorted;
• stockout-censored;
• trending where evidence supports it.

Initial model set

• damped ETS for regular/smooth demand;
• seasonal naive plus category-level seasonality where appropriate;
• TSB for intermittent/declining lumpy items;
• hierarchical/category prior or analog shrinkage for new/thin-history products;
• naive fallback.

Auto-ARIMA remains a research candidate, not required V1 production behavior.

Forecast Value Added

Rolling-origin backtesting remains mandatory, but a complex model should replace a simpler baseline only when it demonstrates meaningful improvement.

Track:

• WAPE where meaningful;
• MASE/MAE;
• bias;
• service/stockout consequence;
• decision-level inventory outcomes.

Do not rely on MAPE for low-volume products.

────────

18. ABC / XYZ

ABC = business importance

V1 should classify primarily on trailing gross-profit dollars rather than revenue alone because the North Star is profit generated from inventory investment.

Working shares remain approximately:

• A: top 80%;
• B: next 15%;
• C: final 5%.

Add a permanent human FORCE_A / MUST_STOCK policy for traffic-driving products whose importance is not captured by GP ranking.

XYZ = predictability

• X stable;
• Y seasonal/moderately variable;
• Z erratic/intermittent.

Keep ABC independent of the forecasting lookback.

Do not confuse service importance with capital attractiveness. GMROI/capital productivity is a separate lens.

────────

19. Protection period and safety stock

The system is a periodic-review replenishment process.

Protection should cover the realistic interval until the next receipt, not a permanent 15-day number.

For a typical Monday order / Wednesday receipt rhythm, a starting protection window may be around the next 9–10 days, but it must be vendor-specific and account for:

• actual order cycle;
• vendor delivery calendar;
• measured lead time;
• lead-time variability;
• holiday disruptions.

Use empirical forecast-error/service-level protection rather than arbitrary fixed days for every SKU.

ABC/XYZ influences the desired protection/service level.

────────

20. Stockout-censored demand

Zero sales while unavailable are censored observations, not ordinary zero demand.

Use:

• ShopifyQL inventory analytics/adjustment history where accessible;
• own daily inventory snapshots;
• sales history.

Track both calendar velocity and in-stock evidence.

Do not multiply sparse in-stock observations into huge fabricated demand.

Low in-stock coverage reduces confidence and can trigger review/caps, especially for A products.

────────

21. Demand sensing, seasonality, events and outliers

Recent demand should influence the near-term forecast, but it is bounded/damped.

Use signals such as:

• recent momentum;
• category movement;
• sell-through;
• seasonal transitions;
• known events.

Avoid multiplying many short-window factors together.

Known events

Store forward and historical events:

• tastings;
• promotions;
• launches;
• holidays;
• Bills/Sabres games where material;
• local events;
• expected bulk purchases.

Outliers

A bulk customer or tasting sale remains real sales history but does not automatically redefine the recurring baseline.

Human explanation becomes structured event context.

New products

Last-year zero is not a negative signal for a product that did not exist.

Use recent actuals, category priors/analogs and uncertainty.

────────

22. Normal replenishment

Conceptually:

Order-up-to target - effective inventory position = baseline need

Effective inventory position includes at least:

• Available;
• trusted Incoming;
• relevant known commitments/reservations if not already reflected in Available.

Policy layers then apply:

• ONE_BOTTLE;
• allocated exclusion;
• case/pack rounding;
• supplier minimum-order multiples;
• minimum presentation stock if deliberately configured.

Strategic deal buying is evaluated after baseline need exists.

────────

23. Procurement PO ledger and backorder reconciliation

Buffalo Procurement maintains its own PO ledger even while Shopify native POs are created through CSV import.

Each finalized Procurement PO records:

• run;
• vendor;
• lines;
• quantity/cost;
• finalization time;
• Shopify-import status/reference when known;
• expected receipt;
• receipt/reconciliation status.

Inventory-flow evidence

Let:

• A0 = prior Monday Available;
• S = units sold since then;
• A1 = current Available;
• R = documented receipts/positive adjustments;
• J = other adjustments.

Conceptually:

A1 = A0 + R + J - S

Use direct receipt/transfer/adjustment evidence first.

Fallback reasoning remains:

• S > A0 means some new units had to become available unless another positive adjustment explains them;
• S <= A0 means prior inventory could have covered all sales;
• A0=0, A1=0, Incoming=0, S=0 is a strong possible unreceived/backorder signal;
• ambiguity → review, never duplicate automatically.

An open Procurement PO line is itself evidence and prevents silent duplicate ordering.

────────

24. ONE_BOTTLE and allocated products

ONE_BOTTLE

Treat as a real policy override:

• if Available + trusted Incoming >= 1 → buy 0;
• if <= 0 → recommend one loose bottle;
• apply vendor-specific loose fee;
• large temporary demand change may trigger human override rather than silently rewriting the policy.

Allocated Bourbon

Dedicated Allocated Bourbon collection excludes routine automated replenishment.

Keep a redundant policy guard if useful so a collection edit cannot silently turn allocated products back on.

────────

25. Strategic break-ladder economics

For every supplier program:

1. calculate normal need;
2. determine the current effective tier/economic baseline;
3. evaluate the next deeper tier;
4. evaluate each subsequent tier marginally;
5. stop when incremental economics no longer justify extra inventory.

Never jump directly from baseline to deepest published discount.

Profit Driver

Variant producing a meaningful portion of the economic benefit.

Filler

A legitimate assortable item used to qualify efficiently.

A filler is never chosen solely because it is cheap. It must be sensible based on:

• current inventory;
• forecast/future need;
• case pack;
• velocity;
• resulting days of supply;
• cash;
• assortment eligibility.

Bridge Cash

Extra inventory cash beyond baseline need required to reach the better tier.

────────

26. Strategic financial metrics

Keep intuitive measures but do not let any one metric dominate.

Target Cost

Retail × (1 − target margin)

• Wine target ≈ 33%.
• Non-wine target ≈ 25%.

Diagnostic only. Never a reason to overbuy.

Immediate purchase savings

Savings on units actually purchased now versus the baseline/current counterfactual.

Incremental bridge benefit

Benefit attributable specifically to deploying additional inventory cash now.

Projected run-rate GP

Context only. Never label it as realized savings. If the same deal can simply be captured again next month, the forward-buy benefit must not pretend the future savings were created by today’s extra inventory.

GP per $100 Bridge Cash

Keep because it is understandable, but pair it with time.

Time-adjusted return

Track a measure such as:

Incremental GP / (Incremental Cash × expected months cash is tied up)

alongside:

• resulting days of supply;
• expected depletion days;
• GMROI/capital productivity;
• payback/cash exposure.

The goal is exactly: profit per inventory dollar, sooner.

────────

27. Assortment optimization

Use the simplest exact method that solves the actual program.

For small assortment groups, enumeration may be easier to audit than a solver.

For genuinely combinatorial programs, use integer optimization such as HiGHS/PuLP/OR-Tools.

Decision variables are integer cases/allowed loose units.

Constraints can include:

• normal need protection;
• BT/CS qualification;
• case packs;
• explicit assortment group;
• allocation;
• maximum practical days of supply;
• capacity where configured;
• gift-pack/combo rules.

Availability protection is a hard constraint, not a weighted preference.

Do not expose a giant Pareto frontier. Auto-select routine clear winners; show 2–3 alternatives only when materially different tradeoffs deserve human choice.

────────

28. Vendor minimums

Vendor minimums do not block legitimate PO generation.

If under minimum, show:

• PO total/cases;
• shortfall;
• expected fee;
• legitimate future need if any.

Never auto-add unnecessary inventory just to avoid the fee.

The system may recommend PAY FEE / DELAY / ADD LEGITIMATE NEED based on service risk, but the owner decides.

────────

29. FUTURE price transition strategy

Permanent increase

Do not automatically buy a year.

Forward-buy only where extra cash, expected sell-through and avoided future cost justify it.

Otherwise replenish normally and flag future retail-price review when margin falls below target.

Temporary expensive month

If expected reversion is known, buy only enough economical inventory to bridge the expensive window.

Permanent decrease

Avoid unnecessary expensive old-month stock, but do not intentionally stock out important products merely to wait for cheaper pricing.

Temporary/permanent explanations are stored as structured human intelligence with expiry/reversion dates.

────────

30. Review intelligence

Every human decision is explicitly scoped.

RUN_ONLY

Affects only this run.

TEMPORARY

Has effective/expiry dates and optional expected reversion.

PERMANENT

Supplier alias, assortment exception, ONE_BOTTLE policy, etc.

A single review decision can never silently rewrite a global core rule.

The review queue must trend downward as aliases/policies/thresholds are learned.

Negative mapping decisions are also retained so the system does not repeatedly ask the same bad question.

────────

31. Monday production sequence

Preconditions

Required readiness gates must be healthy for affected scope.

Run

1. Sync current Shopify catalog.
2. Reconcile canonical identities.
3. Refresh historical/incremental sales through cutoff.
4. Pull current Available and Incoming.
5. Pull collections and inventory analytics where useful.
6. Freeze own inventory snapshot and run snapshot.
7. Reconcile prior Procurement PO/open incoming state.
8. Validate mappings, packs, current supplier pricing and material exceptions.
9. Apply allocated/ONE_BOTTLE policy routing.
10. Classify demand regime and forecast.
11. Apply stockout/event corrections and empirical protection.
12. Calculate normal need.
13. Evaluate CURRENT/FUTURE counterfactuals when FUTURE exists.
14. Evaluate every relevant BT/CS break ladder.
15. Optimize explicit assortment groups.
16. Evaluate Profit Drivers, legitimate Fillers and Bridge Cash.
17. Evaluate gift-pack opportunities.
18. Check vendor minimums.
19. Compare combo alternatives.
20. Produce small Review Queue.
21. Human approves/changes/rejects/comments.
22. Persist decision by scope.
23. Recalculate.
24. Optional ad-hoc weekly budget rework; protect core availability and cut/defer discretionary strategic inventory first.
25. Finalize exactly one Procurement PO per vendor.
26. Generate Shopify-compatible PO CSV.
27. Generate Emergency Packet.

No final Shopify write happens merely because the model produced a recommendation.

────────

32. PO handoff to Shopify

Current stable workflow:

Buffalo Procurement FINAL → Shopify native PO CSV → Shopify Purchase Order → Shopify receiving/transfer workflow

The CSV adapter uses the current Shopify-native import format and must be validated against a live sample before reliance.

When Shopify exposes a stable production Purchase Order creation mutation, replace the final adapter with direct draft-PO creation. Upstream forecasting/procurement logic remains unchanged.

────────

33. Emergency Packet

After every finalized run, create a self-contained operational bundle containing at least:

• vendor PO CSVs;
• recommendations/quantities/reasons;
• current run economics/cost snapshot;
• supplier mapping export relevant to the run;
• open Procurement PO ledger;
• unresolved exceptions;
• human-readable run summary.

Keep a copy outside the running web process so a Replit outage turns Monday procurement into an inconvenience rather than a business crisis.

────────

34. Backups and audit

Minimum responsible V1 protection:

• regular logical PostgreSQL dump to Replit App Storage;
• encrypted off-platform copy;
• dump before material schema migrations and monthly price rollover;
• periodic restore test;
• GitHub code mirror;
• append-only/change history for irreplaceable mapping/policy/approval changes.

Do not build enterprise multi-region infrastructure for one store.

────────

35. Price-book and parser regression corpus

The August recovered data is the golden starting fixture set:

• 85 supplier offers;
• 271 August price levels;
• Empire and Southern mined books;
• source pages/item codes and known mapping edge cases.

Do not re-create these relationships from scratch.

Future parsers should be tested against the known August corpus before being trusted.

The old assortable_working blanket flag is not authoritative under the current assortment policy.

────────

36. Shadow acceptance before real reliance

Run at least several live shadow Mondays and include a monthly price-book cycle before trusting automatic recommendations.

Hard stop conditions include:

• any silent wrong Variant-ID mapping;
• any BT/CS interpretation error;
• any duplicate-order risk caused by reconciliation logic;
• any unverified supplier row entering FUTURE/CURRENT;
• material unexplained PO-dollar discrepancy;
• reproducibility failure.

Track:

• forecast bias/error where statistically meaningful;
• A-item service/stockout outcomes;
• inventory days/cash exposure;
• owner quantity overrides;
• exception count and repeat rate;
• mapping accuracy;
• price-book validation accuracy;
• prior-PO reconciliation accuracy.

Human final approval remains in the workflow even after shadow mode.

────────

37. Build phases — current authoritative order

Phase 1A — Cloud-ready foundation

Completed in v1.2; upgraded in v1.3.

• Postgres schema.
• locked rules.
• review scopes.
• core economics.
• BT/CS foundations.
• CURRENT/FUTURE lifecycle.

Phase 1B — Seed migration

Built / packaged.

• migrate 2,029 variants;
• migrate 3,301 historical aliases;
• migrate 4 vendors;
• migrate 85 supplier offers;
• migrate 271 August current price rows as seed/regression data;
• do not trust old blanket assortment flag.

Phase 2A — Live catalog reconciliation

Construction resumed now.

• Shopify client-credentials auth module;
• direct GraphQL pagination against current variants;
• canonical identity reconciliation;
• recreation candidate detection;
• readiness gate.

Phase 2B — Historical sales backfill

Construction resumed now.

• ShopifyQL historical daily sales retrieval;
• raw source-fact storage;
• canonical alias resolution;
• unresolved review;
• idempotent canonical aggregation;
• readiness gate.

Phase 3 — Inventory history + vendor rules

• nightly snapshots;
• ShopifyQL inventory/adjustment probe;
• incoming;
• vendor calendars/minimums/fees;
• open PO ledger reconciliation.

Phase 4 — Price-book engine

• universal import contract;
• Empire parser;
• Southern regular parser;
• Southern combo parser;
• staging/validation;
• structural diff;
• manual-AI fallback packet.

Phase 5 — Forecasting V1

• demand regimes;
• damped ETS / seasonal naive / TSB / hierarchical new-product model;
• rolling backtests/FVA;
• GP$ ABC + XYZ;
• stockout-censored treatment;
• empirical protection;
• events/seasonality.

Phase 6 — Strategic procurement

• normal need;
• break ladders;
• current effective tier;
• Target Cost context;
• immediate vs bridge benefit;
• time-adjusted capital return;
• assortment enumeration/optimization;
• gift packs;
• minimums;
• FUTURE transitions;
• combo comparisons.

Phase 7 — Review + PO

• small review queue;
• structured write-back;
• recalc;
• one PO/vendor;
• Shopify CSV;
• Emergency Packet;
• next-run reconciliation.

Phase 8 — Production UI / hardening

• dashboard;
• Monday Run;
• Review Queue;
• Price Books;
• Vendor POs;
• Settings/policies;
• private deployment/auth;
• backup/restore;
• monitoring;
• operator SOP.

Phase 9 — Calibration / V1.5 / V2

Only after real operating data:

• expand models if FVA supports it;
• richer demand sensing;
• portfolio capital allocator;
• learned event lift;
• more automated combo optimization;
• stable direct Shopify PO mutation when available.

────────

38. Non-negotiable guardrails

• Never guess a material identity mapping.
• Never treat supplier SKU as canonical identity.
• Never let gift/combo/temporary SKU overwrite normal Shopify SKU automatically.
• Never confuse BT and CS.
• Never make cross-product assortment implicit.
• Never override a book-stated DOES NOT ASSORT rule.
• Never buy filler merely because it is cheap.
• Never auto-add a combo.
• Never block a legitimate vendor PO solely because it is below vendor minimum.
• Never count known stockout days as ordinary zero-demand observations.
• Never let a tasting/bulk spike blindly establish recurring demand.
• Never penalize a new launch because prior-year sales were zero.
• Never overwrite CURRENT when loading FUTURE.
• Never allow partial/unverified price-book import to become active.
• Never auto-change retail selling price.
• Never make a large forward buy solely because price is rising.
• Never allow a single review approval to silently change global policy.
• Never rely on an AI conversation as database memory.
• Never finalize a PO while a blocking readiness gate for that scope is FAIL.
• Never duplicate an open/unreconciled PO line merely because Available and Incoming both read zero.

────────

39. Current implementation milestone

The immediate milestone is not forecasting.

It is:

> **Prove current catalog identity and historical demand are complete enough to trust.**

Acceptance target for the current build:

1. connect to Shopify through merchant-owned app credentials;
2. materialize the full current Variant catalog;
3. reconcile it against the historical 2,029-row seed;
4. resolve/review missing/recreated identities;
5. pull historical ShopifyQL daily sales from store opening;
6. resolve historical sales to canonical current Variant IDs;
7. rebuild sales_daily idempotently;
8. show CATALOG_SYNC and SALES_BACKFILL readiness results;
9. keep PO generation disabled until those gates pass.

Only then do we advance to forecasting and purchasing recommendations.
