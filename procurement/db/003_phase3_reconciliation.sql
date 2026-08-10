-- Migration 003 — Phase 3 live catalog reconciliation support.
-- 1) Allow AMBIGUOUS_IDENTITY classification (multiple/conflicting recreation evidence).
-- 2) Record pagination completion on catalog sync runs.
ALTER TABLE catalog_reconciliation_items DROP CONSTRAINT IF EXISTS catalog_reconciliation_items_classification_check;
ALTER TABLE catalog_reconciliation_items ADD CONSTRAINT catalog_reconciliation_items_classification_check
    CHECK(classification IN (
        'EXACT','NEW','MISSING','INACTIVE','POTENTIAL_RECREATION','CHANGED_ATTRIBUTES','RESOLVED','AMBIGUOUS_IDENTITY'
    ));
ALTER TABLE catalog_sync_runs ADD COLUMN IF NOT EXISTS pagination_complete BOOLEAN;
