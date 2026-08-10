Buffalo Procurement OS — Replit Agent Build Execution Prompt v2.1

Paste this into Replit Agent after importing the v1.3 code package / GitHub repository.

You are the implementation agent for Buffalo House Procurement OS, a real internal procurement system for a wine-and-spirits retailer.

Read before touching code

Read, in this exact order:

1. 00_READ_ME_FIRST.md if present.
2. 01_CANONICAL_SYSTEM_SPEC_v2_1.md — authoritative intended behavior.
3. config/rules.toml — machine-enforced hard rules.
4. 05_CURRENT_BUILD_STATUS_v1_3.md or existing docs/BUILD_REPORT_v1_3.md.
5. existing schema/migrations/source/tests.

Do NOT regenerate the project from scratch merely because you can. Continue from the current v1.3 foundation and preserve verified seed relationships, tests and fail-closed behavior.

Core rule

We want maximum development speed but zero shortcuts on material identity, supplier mapping, BT/CS, supplier pricing promotion or real-money PO activation.

Sidekick is not part of this architecture. Do not add it.

Do not add a mandatory runtime LLM. AI is a development tool, not a procurement authority.

Immediate objective

Turn the existing catalog + historical-sales foundation from “code built” into a running Replit production foundation with:

• production PostgreSQL;
• imported verified seed bundle;
• Shopify connection via deployment secrets;
• full live catalog reconciliation;
• review UI for only material catalog identity blockers;
• complete historical ShopifyQL sales backfill from 2024-11-28 through current date;
• historical identity review/exclusion UI;
• CATALOG_SYNC and SALES_BACKFILL readiness dashboard;
• PO output still disabled until required gates pass.

Phase 0 — establish a safe working repo

1. Ensure repository is under Git and suitable for GitHub mirroring.
2. Create a branch/commit before any migration or large refactor.
3. Verify Python environment and run the entire existing test suite before changes.
4. Record baseline test results.
5. Do not delete old seed files or migration evidence.
6. Never commit secrets.

Phase 1 — Replit production infrastructure

Implement/configure:

• Replit production PostgreSQL via DATABASE_URL;
• App Storage adapter for uploaded documents/exports/backups; keep storage behind an interface so future migration is easy;
• environment secrets for Shopify client ID, client secret, shop domain and any required app configuration;
• web deployment entry point for FastAPI/UI;
• ordinary CLI entry points for batch jobs so scheduled jobs are portable.

Do not create a Reserved VM dependency unless measured behavior requires it.

Add startup health checks for:

• DB connectivity;
• schema version;
• required environment configuration;
• no accidental SQLite production usage.

Phase 2 — apply schema and import seed

1. Apply migrations/schema transactionally.
2. Import the seed CSVs.
3. Verify exact seed counts and foreign-key integrity.
4. Verify there are no orphan supplier offers/prices/aliases.
5. Persist an import manifest/checksum record.
6. Expose a simple admin status page showing seed import state and readiness gates.

Do not treat the old 2,029 seed count as the expected current Shopify count.

Phase 3 — execute live catalog reconciliation

Use the existing Shopify client/catalog logic and improve it only where necessary.

Pull the complete current variant universe and persist a catalog-sync run.

For each historical/current seed identity classify:

• exact current ID;
• newly observed current variant;
• missing previously-current variant;
• changed metadata;
• potential recreation;
• retired/obsolete candidate.

Potential recreation evidence may include exact SKU, barcode, normalized title/variant size and product relationship. Never auto-merge old→new Variant IDs merely from similarity.

Build a minimal review UI for recreation candidates showing:

• old Variant ID and historical attributes;
• new Variant ID and live attributes;
• SKU/barcode/title/size evidence;
• historical sales magnitude if available;
• APPROVE ALIAS / MARK RETIRED / LEAVE UNRESOLVED actions;
• actor/time/note audit record.

CATALOG_SYNC can PASS only when every material current/missing identity is accounted for and no conflicting canonical ownership remains.

Phase 4 — execute historical ShopifyQL sales backfill

Backfill daily data from 2024-11-28 through current date.

Preserve the architecture:

• source facts first;
• canonical resolution second;
• canonical daily aggregate rebuild third.

Store source fields including day, source historical Variant ID, historical SKU, product title/variant title at time of sale, net items sold, net sales, fetch batch/hash and resolution status/method.

Resolution hierarchy must remain conservative:

1. current Variant ID exact;
2. approved old-ID alias;
3. approved exact historical SKU + normalized identity alias;
4. deterministic unique evidence only when truly unique;
5. otherwise AMBIGUOUS/UNRESOLVED.

Never fuzzy-auto-resolve years of demand history.

Rows with source Variant ID null/0 are expected.

Build a review UI for unresolved historical identities with:

• source SKU/title/variant title;
• date range and unit/revenue magnitude;
• candidate current variants/evidence;
• MAP TO CANONICAL / EXCLUDE HISTORICAL ITEM / LEAVE UNRESOLVED;
• audit note.

Allow re-resolution from locally stored raw facts after aliases change, without re-querying Shopify.

Add control-total reconciliation. SALES_BACKFILL cannot PASS until the requested history is complete and every material unit/revenue row is resolved or explicitly excluded under policy.

Phase 5 — foundation UI

Build a simple, operationally clear dashboard — do not overdesign.

Pages/tabs:

• System Readiness
• Catalog Reconciliation
• Historical Sales Reconciliation
• Data/Sync Runs

Readiness page should show PASS/WARN/FAIL and exact blockers for:

• CATALOG_SYNC
• SALES_BACKFILL
• future gates as disabled/not-ready placeholders if useful.

PO generation controls must be visibly disabled while required gates fail.

Phase 6 — tests required before declaring foundation complete

Preserve all existing tests and add integration/adversarial tests for:

Catalog:

• current exact ID;
• old missing ID + exact same SKU but conflicting size => no auto-merge;
• exact barcode recreation candidate;
• duplicate SKU across two live variants => blocker/review;
• approved recreation creates one alias and cannot create conflicting canonical ownership;
• retirement does not delete sales history.

Sales:

• historical current ID;
• approved old ID;
• null/0 ID + approved historical SKU/title;
• duplicate SKU ambiguity;
• excluded old item;
• Shopify analytics restatement does not double count;
• rerun is idempotent;
• local re-resolution updates canonical aggregate without refetch;
• control totals reconcile.

Infrastructure:

• token refresh/retry;
• pagination/chunking resumes after interruption;
• DB transaction rollback on failed seed/import;
• no secret logging.

Parallel work allowed for speed

While identity reviews are awaiting human decisions, you MAY scaffold the following behind disabled feature flags:

• daily inventory snapshot tables/jobs;
• vendor operating-profile UI;
• price-book upload/staging UI shell;
• universal normalized price import contract;
• parser interface;
• PO-ledger schema/UI shell.

But do not allow downstream strategic procurement output to be treated as trusted until foundation gates pass.

Next phase immediately after the first two gates pass

Build in this order:

1. nightly inventory snapshots + test Shopify inventory/adjustment evidence;
2. vendor calendars/minimums/loose fees/lead-time profile;
3. Procurement PO ledger and open-PO reconciliation;
4. universal price-book staging/import contract;
5. Empire parser;
6. Southern regular parser;
7. Southern combo parser;
8. CURRENT/FUTURE structural comparison and guarded monthly rollover;
9. forecasting V1;
10. strategic procurement economics;
11. review queue;
12. one PO/vendor + Shopify-compatible PO CSV + Emergency Packet;
13. shadow mode.

Non-negotiable implementation invariants

Encode with constraints/tests where feasible:

• Variant ID canonical identity.
• Supplier SKU is not canonical identity.
• No automatic temporary/gift/combo supplier SKU overwrite of Shopify SKU.
• CURRENT/FUTURE only; no reusable monthly price archive.
• FUTURE import cannot mutate CURRENT.
• Promotion to FUTURE/CURRENT is transactional and fail-closed.
• BT and CS are typed separately.
• Same-product assortment default; explicit exception only; DOES NOT ASSORT wins.
• Combo auto-add OFF.
• Gift-pack large forward buy = human review.
• Vendor minimum = warning/economic decision, not filler mandate.
• ONE_BOTTLE overrides normal case replenishment.
• Allocated Bourbon excluded from routine auto-replenishment.
• Target Cost informational/diagnostic only.
• No automatic retail-price changes.
• No material fuzzy identity/supplier mapping guess.
• No duplicate open PO line due to zero Available/Incoming alone.
• Human decisions have explicit RUN_ONLY/TEMPORARY/PERMANENT scope.
• No final PO while affected blocking gate is FAIL.

Engineering quality

Use:

• standard Python/PostgreSQL patterns;
• migrations;
• Decimal for money/cost calculations;
• UTC timestamps in storage with explicit local-time presentation;
• structured logging with run IDs/import IDs;
• retry/backoff for Shopify;
• idempotent jobs;
• explicit transaction boundaries;
• append/change audit for mappings/policies/approvals;
• typed enums/value objects for break units and decision scope;
• small pure functions for financial logic;
• no hidden business rules in frontend components.

How to report progress to the owner

At each meaningful milestone report only:

• what is now actually running;
• tests passing/failing;
• exact readiness-gate state;
• exact human decisions needed;
• next executable step.

Never claim a production gate passed just because code exists.

First response after reading this prompt

Do not immediately rewrite code.

First return:

1. your understanding of the current repo and authority files;
2. any conflicts you detect between code and canonical spec;
3. a numbered execution plan for Phases 0–6;
4. commands/actions that require the owner to supply credentials or click in Replit/Shopify;
5. tasks you can perform autonomously now.

Then proceed with autonomous tasks.