# Monday procurement input templates

These files are operator worksheets, not import authority and not purchase
orders. Every completed copy must remain labeled `TEST DATA — NOT FOR
ORDERING` until the production data sources, private access, runtime, and
purchasing authorization gates are independently proven.

Use Shopify Variant ID as the canonical item identity. Supplier SKU is mapping
evidence only. Do not fill blanks with estimates:

1. `monday_current_inventory.csv` — one row per Variant ID/location from a
   same-business-day owned capture. Available and incoming must be explicit.
2. `monday_recent_sales_coverage.csv` — source/run coverage evidence through
   the day immediately before the buying date. Historical data ending August
   10 is stale for a September 7 run.
3. `monday_vendor_terms.csv` — owner-confirmed calendars, lead time,
   variability, minimum, and loose-unit facts.
4. `monday_open_orders.csv` — every nonterminal Procurement PO line with typed
   reconciliation provenance. DRAFT rows are never incoming.
5. `monday_future_price_book.csv` — the exact normalized 27-column Packet 4
   staging contract. `target_price_state` must be `future`; uploads cannot
   write CURRENT.
6. `monday_manual_review_worksheet.csv` — a review fallback only. A row may be
   reviewed only after every named application gate passes, and it never
   authorizes FINAL, release, transmission, or Shopify mutation.

Never place credentials, database URLs, customer data, or supplier-confidential
source files in this repository. Real completed worksheets belong only in an
approved private operational location.
