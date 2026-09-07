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

CREATE OR REPLACE FUNCTION guard_inventory_snapshot_run_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'RUNNING'
           OR NEW.completed_at IS NOT NULL
           OR NEW.eligible_rows <> 0
           OR NEW.archival_rows <> 0
           OR NEW.invalid_rows <> 0
           OR NEW.incomplete_rows <> 0 THEN
            RAISE EXCEPTION 'inventory snapshot runs must start in pristine RUNNING state';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'inventory snapshot run evidence is durable and cannot be deleted';
    END IF;
    IF OLD.status <> 'RUNNING'
       OR NEW.status NOT IN ('COMPLETED','FAILED')
       OR NEW.inventory_snapshot_run_id IS DISTINCT FROM OLD.inventory_snapshot_run_id
       OR NEW.business_date IS DISTINCT FROM OLD.business_date
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.source IS DISTINCT FROM OLD.source
       OR NEW.source_hash IS DISTINCT FROM OLD.source_hash
       OR NEW.rows_received IS DISTINCT FROM OLD.rows_received THEN
        RAISE EXCEPTION 'completed inventory snapshot run evidence is immutable';
    END IF;
    IF NEW.completed_at IS NULL THEN
        RAISE EXCEPTION 'completed or failed inventory snapshot run requires completed_at';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_inventory_snapshot_run_evidence
    ON inventory_snapshot_runs;
CREATE TRIGGER trg_guard_inventory_snapshot_run_evidence
BEFORE INSERT OR UPDATE OR DELETE ON inventory_snapshot_runs
FOR EACH ROW EXECUTE FUNCTION guard_inventory_snapshot_run_evidence();

CREATE OR REPLACE FUNCTION guard_inventory_snapshot_run_row_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_status TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'inventory snapshot run-row evidence is append-only';
    END IF;
    SELECT status INTO parent_status
      FROM inventory_snapshot_runs
     WHERE inventory_snapshot_run_id=NEW.inventory_snapshot_run_id;
    IF parent_status IS DISTINCT FROM 'RUNNING' THEN
        RAISE EXCEPTION 'inventory snapshot rows require a RUNNING parent';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_inventory_snapshot_run_row_evidence
    ON inventory_snapshot_run_rows;
CREATE TRIGGER trg_guard_inventory_snapshot_run_row_evidence
BEFORE INSERT OR UPDATE OR DELETE ON inventory_snapshot_run_rows
FOR EACH ROW EXECUTE FUNCTION guard_inventory_snapshot_run_row_evidence();

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
