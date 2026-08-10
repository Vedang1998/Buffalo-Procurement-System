-- Migration 004 — identity decision invariants (Phase 3 review fixes).
-- One approved recreation continuity per historical Variant ID. The seed alias
-- bundle already satisfies one-target-per-old-ID; this enforces it permanently.
CREATE UNIQUE INDEX IF NOT EXISTS uq_variant_alias_one_continuity_per_old_id
    ON variant_aliases(old_variant_id)
    WHERE approved AND old_variant_id IS NOT NULL AND source='CATALOG_RECONCILIATION';
