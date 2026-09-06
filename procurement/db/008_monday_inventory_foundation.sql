-- Monday MVP Packet 1: owned current inventory and daily snapshot evidence.
-- Additive only. This migration must not reach production before the separately
-- controlled published-production Phase 4 reconciliation is complete.

CREATE OR REPLACE FUNCTION is_procurement_eligible_variant(checked_variant_id TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1
        FROM variants
        WHERE variant_id = checked_variant_id
          AND identity_scope = 'CURRENT'
          AND active = TRUE
          AND catalog_state = 'LIVE'
    )
$$;

CREATE TABLE IF NOT EXISTS inventory_snapshot_runs (
    inventory_snapshot_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_date DATE NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    source TEXT NOT NULL CHECK (btrim(source) <> ''),
    source_hash TEXT NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    rows_received INTEGER NOT NULL DEFAULT 0 CHECK (rows_received >= 0),
    eligible_rows INTEGER NOT NULL DEFAULT 0 CHECK (eligible_rows >= 0),
    archival_rows INTEGER NOT NULL DEFAULT 0 CHECK (archival_rows >= 0),
    invalid_rows INTEGER NOT NULL DEFAULT 0 CHECK (invalid_rows >= 0),
    incomplete_rows INTEGER NOT NULL DEFAULT 0 CHECK (incomplete_rows >= 0),
    evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    UNIQUE (business_date, source, source_hash)
);

CREATE INDEX IF NOT EXISTS idx_inventory_snapshot_runs_authoritative
    ON inventory_snapshot_runs(business_date DESC, completed_at DESC,
                               inventory_snapshot_run_id DESC)
    WHERE status = 'COMPLETED';

CREATE TABLE IF NOT EXISTS inventory_snapshot_run_rows (
    inventory_snapshot_run_id UUID NOT NULL
        REFERENCES inventory_snapshot_runs(inventory_snapshot_run_id)
        ON DELETE RESTRICT,
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE RESTRICT,
    location_gid TEXT NOT NULL DEFAULT '',
    available_quantity NUMERIC(14,4),
    incoming_quantity NUMERIC(14,4),
    on_hand_quantity NUMERIC(14,4),
    committed_quantity NUMERIC(14,4),
    reserved_quantity NUMERIC(14,4),
    damaged_quantity NUMERIC(14,4),
    validation_status TEXT NOT NULL
        CHECK (validation_status IN ('VALID','INCOMPLETE','INVALID','ARCHIVAL_ONLY')),
    validation_message TEXT,
    PRIMARY KEY (inventory_snapshot_run_id, variant_id, location_gid)
);

ALTER TABLE daily_inventory_snapshots
    ADD COLUMN IF NOT EXISTS inventory_snapshot_run_id UUID
        REFERENCES inventory_snapshot_runs(inventory_snapshot_run_id)
        ON DELETE RESTRICT;
ALTER TABLE daily_inventory_snapshots
    ADD COLUMN IF NOT EXISTS validation_status TEXT NOT NULL DEFAULT 'INCOMPLETE';
ALTER TABLE daily_inventory_snapshots
    ADD COLUMN IF NOT EXISTS validation_message TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_daily_inventory_validation_status'
          AND conrelid = 'daily_inventory_snapshots'::regclass
    ) THEN
        ALTER TABLE daily_inventory_snapshots
            ADD CONSTRAINT ck_daily_inventory_validation_status
            CHECK (validation_status IN ('VALID','INCOMPLETE','INVALID','ARCHIVAL_ONLY'));
    END IF;
END
$$;

CREATE OR REPLACE VIEW v_procurement_daily_inventory_snapshots AS
SELECT snapshot_date, captured_at, variant_id, location_gid,
       available_quantity, incoming_quantity, on_hand_quantity,
       committed_quantity, reserved_quantity, damaged_quantity,
       source, inventory_snapshot_run_id, validation_status,
       validation_message
FROM daily_inventory_snapshots
WHERE validation_status IN ('VALID','INCOMPLETE')
  AND is_procurement_eligible_variant(variant_id);

CREATE OR REPLACE VIEW v_latest_procurement_inventory AS
SELECT DISTINCT ON (variant_id, location_gid)
       snapshot_date, captured_at, variant_id, location_gid,
       available_quantity, incoming_quantity, on_hand_quantity,
       committed_quantity, reserved_quantity, damaged_quantity,
       source, inventory_snapshot_run_id, validation_status,
       validation_message
FROM v_procurement_daily_inventory_snapshots
ORDER BY variant_id, location_gid, snapshot_date DESC, captured_at DESC,
         inventory_snapshot_run_id DESC;

INSERT INTO meta(key, value)
VALUES ('monday_inventory_contract', 'v1')
ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
