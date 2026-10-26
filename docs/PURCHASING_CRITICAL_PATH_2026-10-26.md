# Purchasing critical path — 2026-10-26

This is a read-only code/evidence audit, not a production measurement or an
authorization to operate. November 9 is 14 calendar days away. Current evidence
does not support a delivery commitment. No official phase changes here.

The audit starts from published `ddc982ce999b56e9eb8416bc0736e20ff88bf9a1`;
the October 26 changes address backup-tool trust and isolated fixture validation,
not the business implementation assessed below. Internal Codex reviewers are
not independent Claude review. No private inputs or external providers were read.

| Area | Implemented | Missing evidence or implementation |
| --- | --- | --- |
| Catalog/Variant identity | `catalog.py` preserves exact Variant IDs, approved aliases and blocking recreation candidates. | Current authorized catalog capture and reconciliation of new changes. Historical accepted decisions remain evidence; they are not current freshness. |
| Inventory and sales | `inventory.py` validates immutable complete captures; `recommendations.py` requires same-business-date inventory and prior-day-complete authoritative sales. | Current complete inputs and an established capture/operating loop. `capture_inventory_snapshot.py` consumes normalized JSON; no automatic collector/scheduler was found. Current inventory cannot establish historical availability. |
| Supplier mapping/prices/packs | Persistent review, price normalization, validation and transactional VERIFIED FUTURE promotion exist; `price_book.py:1196` checks the exact file/fingerprint and next Buffalo month. | Approved current mappings, packs and November prices. Production mapped selected-offer consumption is not integrated; configured cutover remains disabled; `synthetic_selected_offer.py:490` is explicitly synthetic-only. General `pricing.rollover` always refuses because its audited service is absent. November 1 is a critical dependency before November 9. |
| Fees/deal tiers | `vendor_rules.py:246` validates calendars/minima/fee profiles; tier economics exist; `draft_po.py:163` computes minimum shortfall and applicable fees without filler. | Approved supplier-specific terms/exceptions. Positive loose-unit fees still block because semantics are unresolved (`procurement_review.py:146`). Strategic analysis remains EVIDENCE_ONLY with zero extra units (`strategic.py:169`), not an operational forward-buy optimizer. |
| Incoming/open orders | `po_ledger.py:396` requires attributed FINAL/imported/reconciled, timely, unambiguous incoming; DRAFTs are excluded. Recommendations reconcile ledger incoming against captured incoming. | Current external and internal open-order evidence. Empty local tables do not prove zero incoming. Finalization/import-status/reconciliation backend functions have no non-test operational callers; operator/receipt flow remains unwired. |
| Forecast/net need | Development rolling-origin/FVA/XYZ/protection and GP-dollar ABC code exist (`development_forecast.py:1847`, `:4267`). `replenishment.py:43` subtracts availability and trusted incoming and performs pack rounding. | Current history, authoritative cohort/COGS, vendor timing and applicable policy. Development/research paths explicitly lack production/commercial authority; connected recommendations still report ABC unconfigured. Nonzero delivery variability needs a validated delay model (`replenishment.py:196`). |
| Owner review/edit/reject | ACCEPT, EDIT_QUANTITY and REJECT exist; edits recalculate attained frozen tiers, merchandise and pack quantities, with explicit confirmation for material edits (`procurement_review.py:143`, `:264`, `:625`). | Owner acceptance with current commercial evidence. Same-day active-run replacement/supersession after changed inputs is unimplemented (`recommendations.py:1439`). Within-run quantity recalculation already works and must not be conflated with this missing lifecycle. |
| Vendor drafts/outputs | `draft_po.py:409`, `:454` create immutable vendor-separated drafts; `emergency_packet.py:246` binds artifacts and internal CSVs by hash. | The implemented internal DRAFT CSV is not a validated native Shopify import format (`po_csv.py:1`). Backend finalization does not release/export/transmit and has no operational API/CLI (`po_ledger.py:285`). Real use needs validated output and a deliberate release/import/reconciliation workflow. |

Code paths above are under `procurement/src/procurement_os`, except the capture
CLI under `procurement/tools`. Line references identify this audited checkpoint.

## Required sequence

1. Satisfy supported hosted containment, executable harness and integrated
   synthetic acceptance, including independent review. Ordinary PostgreSQL
   container tests do not satisfy the same-launcher containment/release gate.
2. Establish the authorized current-input loop: catalog, same-day inventory,
   prior-day-complete sales and reconciled external open orders, preserving
   accepted identity history.
3. Close November commercial readiness: approved selections, packs, prices,
   calendars and fees, plus production price rollover and mapped-offer
   consumption. Synthetic CURRENT replacement or flipping a flag is insufficient.
4. Connect and validate the intended forecasting policy and classification/
   protection against those inputs. Any reduced operational scope requires an
   explicit consequential decision; do not silently call the baseline complete.
5. Exercise owner review through edited tiers, rejection, changed-input handling,
   separate vendor outputs and deliberate import/reconciliation. Real release
   and supplier transmission remain outside current authority.

Historical research must be labeled accurately: PHASE_STATUS's 84-day entry is
historical. Corrected V3 has a 138-day research scenario, but its September 19
forecast origin and latest October 5 target remain historical
(`private_research_v3_corrected.py:923`). Neither "forecasting/ABC do not exist"
nor "current purchasing is ready" follows from those artifacts.

Railway compatibility remains NOT_PROVEN. No deployment, production database,
Shopify operation, private-data transfer, PO release or supplier transmission
was performed. No main/PR #24/default branch or official phase was changed.
