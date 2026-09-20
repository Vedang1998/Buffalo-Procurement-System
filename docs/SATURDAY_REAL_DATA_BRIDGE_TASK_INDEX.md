# Saturday real-data bridge task index

Status: active local implementation; private research only.

## Fixed clock

- T0: `2026-09-20T01:56:54.064728299Z`
- optional-work cutoff: `2026-09-20T09:56:54.064728299Z`
- hard stop: `2026-09-20T11:56:54.064728299Z`
- New York equivalents: September 19 21:56:54, September 20 05:56:54,
  and September 20 07:56:54 EDT.

## Frozen parent and review status

- parent: `c1895ff5a63599bae57fd41de827b0665446682f`
- parent tree: `b8aec47caca38bb517603166b1b54b32909181f8`
- V2 review ZIP: 1,998,197 bytes,
  `d50c09b9809c269eb7e94596bf1892cfecfd6ea58b35e99607fd0757b8215337`
- independent reconstruction: exact
- review disposition: Findings 1, 2, 4, and 5 pass; Finding 3 fails because
  calculator scalars were not reconciled to frozen source evidence.
- child-only remediation commit: `ba7a1df32e7d28e4d8fb596c762746f2c4f6defe`

The frozen parent and its review ZIP remain unchanged.

## Authorized private inputs

- sealed A1 snapshot: present and hash-exact; package ID
  `BUFFALO-V5-PORTABLE-DAYTIME-A1-7260a2448f83`
- original S1: not transferred to Codex
- S1 clarification: not transferred to Codex
- current Shopify catalog/inventory capture: 1,417 products / 2,009 Variants,
  71 complete pages; private manifest `0108a9e9…`
- Shopify daily Variant analytics: 84 complete local days,
  `2026-06-27..2026-09-18`, 9,431 activity rows, all daily queries below
  the connector limit; private manifest `8c52276d…`
- current local day `2026-09-19` is intentionally omitted as partial.

## Boundaries

- data mode: `PRIVATE_REAL_SOURCE_REVIEW`
- authority: `RESEARCH_ONLY_UNAPPROVED_NOT_FOR_ORDERING`
- no operational database, migration, synthetic registration, mapping approval,
  selected offer, CURRENT/FUTURE price, DRAFT, PO, Shopify write, supplier
  communication, or order action
- raw private evidence stays outside Git and outside the code transport
- canonical identity joins use Shopify Variant ID only

## Progress

- [x] expose and verify frozen V2 transport
- [x] reconstruct/review V2
- [x] inventory exact private inputs
- [x] retain failing private-bridge integration test
- [x] execute exact A1 reader and publish restart-safe private report
- [x] freeze complete current catalog/inventory and 84-day sales/returns/COGS
- [ ] immutable private intake and reconciliation service
- [ ] coverage and research projection
- [ ] authenticated private UI plus offline HTML/CSV
- [ ] focused, browser/restart, combined validation
- [ ] separate code transport and private evidence transport
