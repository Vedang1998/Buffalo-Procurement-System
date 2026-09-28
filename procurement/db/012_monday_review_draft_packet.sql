-- Monday MVP Packets 5-13: frozen analysis, human review, DRAFT-only output.
-- This migration adds no FINAL, release, Shopify, or production action path.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS procurement_output_mode TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS procurement_input_manifest TEXT;

ALTER TABLE inventory_snapshots
    ADD COLUMN IF NOT EXISTS source_inventory_snapshot_run_id UUID
        REFERENCES inventory_snapshot_runs(inventory_snapshot_run_id) ON DELETE RESTRICT;

ALTER TABLE forecast_results ADD COLUMN IF NOT EXISTS input_fingerprint TEXT;
ALTER TABLE forecast_results ADD COLUMN IF NOT EXISTS method_version TEXT;
ALTER TABLE forecast_results ADD COLUMN IF NOT EXISTS history_start DATE;
ALTER TABLE forecast_results ADD COLUMN IF NOT EXISTS history_end DATE;
ALTER TABLE forecast_results ADD COLUMN IF NOT EXISTS horizon_days INTEGER;
ALTER TABLE forecast_results ADD COLUMN IF NOT EXISTS generated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS input_fingerprint TEXT;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS recommendation_status TEXT;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS strategic_extra_units NUMERIC(14,4) NOT NULL DEFAULT 0;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS units_per_case INTEGER;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS recommended_units NUMERIC(14,4);
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS frozen_supplier_sku TEXT;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS frozen_qualifying_units_per_case INTEGER;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS frozen_loose_order_allowed BOOLEAN;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS frozen_loose_unit_fee NUMERIC(14,2);
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS frozen_minimum_type TEXT;
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS frozen_minimum_value NUMERIC(14,2);
ALTER TABLE procurement_recommendations ADD COLUMN IF NOT EXISTS frozen_below_minimum_fee NUMERIC(14,2);

ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS input_fingerprint TEXT;
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS decision_fingerprint TEXT;
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS approved_cases NUMERIC(14,4);
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS approved_loose_units NUMERIC(14,4);
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS approved_units NUMERIC(14,4);
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS approved_unit_cost NUMERIC(14,4);
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS approved_line_total NUMERIC(14,2);
ALTER TABLE review_decisions ADD COLUMN IF NOT EXISTS evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_runs_procurement_output_mode' AND conrelid='runs'::regclass) THEN
        ALTER TABLE runs ADD CONSTRAINT ck_runs_procurement_output_mode CHECK (
            procurement_output_mode IS NULL OR procurement_output_mode='INTERNAL_DRAFT_ONLY'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_monday_input_manifest' AND conrelid='runs'::regclass) THEN
        ALTER TABLE runs ADD CONSTRAINT ck_monday_input_manifest CHECK (
            procurement_input_manifest IS NULL OR (
                btrim(procurement_input_manifest)<>''
                AND jsonb_typeof(procurement_input_manifest::jsonb)='object'
                AND encode(digest(convert_to(procurement_input_manifest,'UTF8'),'sha256'),'hex')=input_fingerprint
            )
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_forecast_monday_fields' AND conrelid='forecast_results'::regclass) THEN
        ALTER TABLE forecast_results ADD CONSTRAINT ck_forecast_monday_fields CHECK (
            input_fingerprint IS NULL OR (
                input_fingerprint ~ '^[0-9a-f]{64}$'
                AND method_version IS NOT NULL AND btrim(method_version) <> ''
                AND history_start IS NOT NULL AND history_end IS NOT NULL AND history_end >= history_start
                AND horizon_days IS NOT NULL AND horizon_days >= 1
            )
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_monday_recommendation_fields' AND conrelid='procurement_recommendations'::regclass) THEN
        ALTER TABLE procurement_recommendations ADD CONSTRAINT ck_monday_recommendation_fields CHECK (
            input_fingerprint IS NULL OR (
                input_fingerprint ~ '^[0-9a-f]{64}$'
                AND recommendation_status IN ('READY_FOR_REVIEW','NO_ORDER_NEEDED','ROUTINE_EXCLUDED')
                AND units_per_case IS NOT NULL AND units_per_case >= 1
                AND recommended_units IS NOT NULL AND recommended_units >= 0
                AND recommended_units = trunc(recommended_units)
                AND baseline_units >= 0 AND baseline_units = trunc(baseline_units)
                AND recommended_cases >= 0 AND recommended_cases = trunc(recommended_cases)
                AND recommended_loose_units >= 0 AND recommended_loose_units = trunc(recommended_loose_units)
                AND strategic_extra_units = 0
                AND recommended_units = recommended_cases * units_per_case + recommended_loose_units
                AND frozen_supplier_sku IS NOT NULL AND btrim(frozen_supplier_sku)<>''
                AND frozen_qualifying_units_per_case IS NOT NULL AND frozen_qualifying_units_per_case>=1
                AND frozen_loose_order_allowed IS NOT NULL
                AND frozen_loose_unit_fee IS NOT NULL AND frozen_loose_unit_fee>=0
                AND frozen_minimum_type IN ('NONE','CASE','DOLLAR')
                AND frozen_below_minimum_fee IS NOT NULL AND frozen_below_minimum_fee>=0
                AND ((frozen_minimum_type='NONE' AND frozen_minimum_value IS NULL)
                     OR (frozen_minimum_type IN ('CASE','DOLLAR') AND frozen_minimum_value>0))
            )
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_monday_review_fields' AND conrelid='review_decisions'::regclass) THEN
        ALTER TABLE review_decisions ADD CONSTRAINT ck_monday_review_fields CHECK (
            input_fingerprint IS NULL OR (
                input_fingerprint ~ '^[0-9a-f]{64}$'
                AND decision_fingerprint IS NOT NULL AND decision_fingerprint ~ '^[0-9a-f]{64}$'
                AND action IN ('ACCEPT','EDIT_QUANTITY','REJECT')
                AND approved_cases IS NOT NULL AND approved_cases >= 0 AND approved_cases = trunc(approved_cases)
                AND approved_loose_units IS NOT NULL AND approved_loose_units >= 0 AND approved_loose_units = trunc(approved_loose_units)
                AND approved_units IS NOT NULL AND approved_units >= 0 AND approved_units = trunc(approved_units)
                AND approved_unit_cost IS NOT NULL AND approved_unit_cost >= 0
                AND approved_line_total IS NOT NULL AND approved_line_total >= 0
                AND jsonb_typeof(evidence_json)='object' AND evidence_json <> '{}'::jsonb
            )
        );
    END IF;
END
$$;

DROP INDEX IF EXISTS uq_monday_recommendation_scope;
CREATE UNIQUE INDEX uq_monday_recommendation_scope
    ON procurement_recommendations(run_id,variant_id,vendor_id)
    WHERE input_fingerprint IS NOT NULL;
DROP INDEX IF EXISTS uq_monday_review_once;
CREATE UNIQUE INDEX uq_monday_review_once
    ON review_decisions(recommendation_id)
    WHERE recommendation_id IS NOT NULL AND input_fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS monday_run_artifacts (
    monday_run_artifact_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    artifact_type TEXT NOT NULL CHECK (artifact_type IN ('VENDOR_INTERNAL_CSV','EMERGENCY_REVIEW_PACKET')),
    vendor_id UUID REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    storage_key TEXT NOT NULL CHECK (btrim(storage_key) <> ''),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    payload BYTEA NOT NULL,
    content_type TEXT NOT NULL CHECK (btrim(content_type) <> ''),
    safety_label TEXT NOT NULL CHECK (safety_label='TEST DATA — NOT FOR ORDERING'),
    input_fingerprint TEXT NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    created_by TEXT NOT NULL CHECK (btrim(created_by) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    packet_build_transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    UNIQUE (run_id,artifact_type,vendor_id),
    CONSTRAINT ck_monday_artifact_vendor CHECK (
        (artifact_type='VENDOR_INTERNAL_CSV' AND vendor_id IS NOT NULL)
        OR (artifact_type='EMERGENCY_REVIEW_PACKET' AND vendor_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_monday_emergency_packet
    ON monday_run_artifacts(run_id,artifact_type)
    WHERE artifact_type='EMERGENCY_REVIEW_PACKET';

ALTER TABLE monday_run_artifacts ADD COLUMN IF NOT EXISTS payload BYTEA;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM monday_run_artifacts WHERE payload IS NULL) THEN
        RAISE EXCEPTION 'migration 012 refuses artifact rows without DB-verifiable payload evidence';
    END IF;
    ALTER TABLE monday_run_artifacts ALTER COLUMN payload SET NOT NULL;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_monday_artifact_payload_evidence'
           AND conrelid='monday_run_artifacts'::regclass
    ) THEN
        ALTER TABLE monday_run_artifacts ADD CONSTRAINT ck_monday_artifact_payload_evidence CHECK (
            octet_length(payload)=size_bytes
            AND encode(digest(payload,'sha256'),'hex')=sha256
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS monday_packet_build_events (
    run_id UUID PRIMARY KEY REFERENCES runs(run_id) ON DELETE RESTRICT,
    input_fingerprint TEXT NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    artifact_set_sha256 TEXT NOT NULL CHECK (artifact_set_sha256 ~ '^[0-9a-f]{64}$'),
    packet_sha256 TEXT NOT NULL CHECK (packet_sha256 ~ '^[0-9a-f]{64}$'),
    csv_count INTEGER NOT NULL CHECK (csv_count >= 0),
    artifact_count INTEGER NOT NULL CHECK (artifact_count = csv_count + 1),
    verification_method TEXT NOT NULL
        CHECK (verification_method='DB_PAYLOAD_AND_STORAGE_READBACK_SHA256_V1'),
    actor TEXT NOT NULL CHECK (btrim(actor)<>''),
    transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE (run_id,transaction_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='fk_monday_artifact_packet_build_event'
           AND conrelid='monday_run_artifacts'::regclass
    ) THEN
        ALTER TABLE monday_run_artifacts
            ADD CONSTRAINT fk_monday_artifact_packet_build_event
            FOREIGN KEY (run_id,packet_build_transaction_id)
            REFERENCES monday_packet_build_events(run_id,transaction_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_monday_artifact_content_type'
           AND conrelid='monday_run_artifacts'::regclass
    ) THEN
        ALTER TABLE monday_run_artifacts ADD CONSTRAINT ck_monday_artifact_content_type CHECK (
            (artifact_type='VENDOR_INTERNAL_CSV' AND content_type='text/csv')
            OR (artifact_type='EMERGENCY_REVIEW_PACKET' AND content_type='application/zip')
        );
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION monday_artifact_set_sha256(checked_run_id UUID)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT encode(
        digest(
            convert_to(COALESCE(string_agg(
                artifact_type || '|' || COALESCE(vendor_id::text,'') || '|' ||
                storage_key || '|' || sha256 || '|' || size_bytes::text || '|' ||
                content_type || '|' || created_by,
                E'\n' ORDER BY artifact_type,vendor_id,storage_key
            ),''),'UTF8'),
            'sha256'
        ),
        'hex'
    )
    FROM monday_run_artifacts WHERE run_id=checked_run_id
$$;

CREATE OR REPLACE FUNCTION validate_monday_packet_build_event()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_stage TEXT; parent_mode TEXT; parent_fingerprint TEXT;
    actual_csv_count BIGINT; actual_count BIGINT; actual_packet_sha TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Monday packet-build events are append-only';
    END IF;
    SELECT workflow_stage,procurement_output_mode,input_fingerprint
      INTO parent_stage,parent_mode,parent_fingerprint
      FROM runs WHERE run_id=NEW.run_id FOR UPDATE;
    IF parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY'
       OR parent_stage IS DISTINCT FROM 'DRAFTS_BUILT'
       OR parent_fingerprint IS DISTINCT FROM NEW.input_fingerprint
       OR NEW.transaction_id IS DISTINCT FROM txid_current() THEN
        RAISE EXCEPTION 'packet-build event does not match the locked DRAFT run';
    END IF;
    SELECT count(*) FILTER (WHERE artifact_type='VENDOR_INTERNAL_CSV'),count(*),
           min(sha256) FILTER (WHERE artifact_type='EMERGENCY_REVIEW_PACKET')
      INTO actual_csv_count,actual_count,actual_packet_sha
      FROM monday_run_artifacts WHERE run_id=NEW.run_id
        AND packet_build_transaction_id=NEW.transaction_id;
    IF actual_csv_count IS DISTINCT FROM NEW.csv_count
       OR actual_count IS DISTINCT FROM NEW.artifact_count
       OR actual_packet_sha IS DISTINCT FROM NEW.packet_sha256
       OR monday_artifact_set_sha256(NEW.run_id) IS DISTINCT FROM NEW.artifact_set_sha256
       OR EXISTS (
           SELECT 1 FROM monday_run_artifacts a WHERE a.run_id=NEW.run_id
            AND (a.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint
                 OR a.created_by IS DISTINCT FROM NEW.actor
                 OR a.packet_build_transaction_id IS DISTINCT FROM NEW.transaction_id)
       )
       OR EXISTS (
           (SELECT p.vendor_id FROM purchase_orders p WHERE p.run_id=NEW.run_id)
           EXCEPT
           (SELECT a.vendor_id FROM monday_run_artifacts a
             WHERE a.run_id=NEW.run_id AND a.artifact_type='VENDOR_INTERNAL_CSV')
       )
       OR EXISTS (
           (SELECT a.vendor_id FROM monday_run_artifacts a
             WHERE a.run_id=NEW.run_id AND a.artifact_type='VENDOR_INTERNAL_CSV')
           EXCEPT
           (SELECT p.vendor_id FROM purchase_orders p WHERE p.run_id=NEW.run_id)
       ) THEN
        RAISE EXCEPTION 'packet-build event does not match the exact artifact and DRAFT vendor set';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_monday_packet_build_event ON monday_packet_build_events;
CREATE TRIGGER trg_validate_monday_packet_build_event
BEFORE INSERT OR UPDATE OR DELETE ON monday_packet_build_events
FOR EACH ROW EXECUTE FUNCTION validate_monday_packet_build_event();

CREATE OR REPLACE FUNCTION validate_monday_recommendation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_type TEXT;
    parent_mode TEXT;
    parent_stage TEXT;
    parent_fingerprint TEXT;
    offer_vendor UUID;
    offer_variant TEXT;
    offer_active BOOLEAN;
    offer_confidence TEXT;
    offer_package TEXT;
    offer_pack NUMERIC(12,4);
    offer_sku TEXT;
    offer_qualifying NUMERIC(12,4);
    rule_loose BOOLEAN;
    rule_loose_fee NUMERIC(14,2);
    rule_minimum_type TEXT;
    rule_minimum_value NUMERIC(14,2);
    rule_below_fee NUMERIC(14,2);
BEGIN
    SELECT run_type,workflow_stage,input_fingerprint,procurement_output_mode
      INTO parent_type,parent_stage,parent_fingerprint,parent_mode
      FROM runs WHERE run_id=NEW.run_id;
    IF parent_mode='INTERNAL_DRAFT_ONLY' THEN
        IF parent_type IS DISTINCT FROM 'MONDAY_PROCUREMENT' THEN
            RAISE EXCEPTION 'internal DRAFT output requires a Monday Procurement run';
        END IF;
        IF parent_stage <> 'PREPARING' OR NEW.input_fingerprint IS DISTINCT FROM parent_fingerprint THEN
            RAISE EXCEPTION 'Monday recommendation requires the frozen PREPARING run fingerprint';
        END IF;
        IF NOT is_procurement_eligible_variant(NEW.variant_id) OR NEW.offer_id IS NULL THEN
            RAISE EXCEPTION 'Monday recommendation requires an eligible CURRENT/LIVE variant and offer';
        END IF;
        SELECT o.vendor_id,o.variant_id,o.active,o.confidence,o.package_type,
               o.shopify_units_per_case,o.supplier_sku,o.qualifying_units_per_case,
               vr.loose_order_allowed,COALESCE(vr.loose_unit_fee,0),vr.minimum_type,
               vr.minimum_value,vr.below_minimum_fee
          INTO offer_vendor,offer_variant,offer_active,offer_confidence,offer_package,
               offer_pack,offer_sku,offer_qualifying,rule_loose,rule_loose_fee,
               rule_minimum_type,rule_minimum_value,rule_below_fee
          FROM supplier_offers o
          LEFT JOIN vendor_operating_rules vr ON vr.vendor_id=o.vendor_id
         WHERE o.offer_id=NEW.offer_id;
        IF offer_vendor IS DISTINCT FROM NEW.vendor_id OR offer_variant IS DISTINCT FROM NEW.variant_id
           OR offer_active IS DISTINCT FROM TRUE OR offer_confidence IS DISTINCT FROM 'VERIFIED'
           OR offer_package IS DISTINCT FROM 'STANDARD' OR offer_pack IS NULL
           OR offer_pack <> trunc(offer_pack) OR offer_pack <> NEW.units_per_case
           OR offer_sku IS DISTINCT FROM NEW.frozen_supplier_sku
           OR offer_qualifying IS NULL OR offer_qualifying<>trunc(offer_qualifying)
           OR offer_qualifying IS DISTINCT FROM NEW.frozen_qualifying_units_per_case
           OR rule_loose IS DISTINCT FROM NEW.frozen_loose_order_allowed
           OR rule_loose_fee IS DISTINCT FROM NEW.frozen_loose_unit_fee
           OR rule_minimum_type IS DISTINCT FROM NEW.frozen_minimum_type
           OR rule_minimum_value IS DISTINCT FROM NEW.frozen_minimum_value
           OR rule_below_fee IS DISTINCT FROM NEW.frozen_below_minimum_fee THEN
            RAISE EXCEPTION 'Monday recommendation offer/vendor/variant/pack relationship is invalid';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_monday_recommendation ON procurement_recommendations;
CREATE TRIGGER trg_validate_monday_recommendation
BEFORE INSERT OR UPDATE ON procurement_recommendations
FOR EACH ROW EXECUTE FUNCTION validate_monday_recommendation();

CREATE OR REPLACE FUNCTION validate_monday_review_decision()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_run UUID;
    parent_variant TEXT;
    parent_fingerprint TEXT;
    parent_cases NUMERIC(14,4);
    parent_loose NUMERIC(14,4);
    parent_pack INTEGER;
    parent_offer BIGINT;
    parent_vendor UUID;
    run_stage TEXT;
    run_fingerprint TEXT;
    loose_allowed BOOLEAN;
    loose_fee NUMERIC(14,2);
    qualifying_pack NUMERIC(12,4);
    derived_cost NUMERIC(14,4);
    derived_case_price NUMERIC(14,4);
    derived_merchandise NUMERIC(14,2);
    derived_loose_order_fee NUMERIC(14,2);
    derived_total NUMERIC(14,2);
    parent_run_type TEXT;
    parent_mode TEXT;
    target_recommendation BIGINT;
    old_recommendation BIGINT;
    old_mode TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_recommendation := OLD.recommendation_id;
        SELECT ru.procurement_output_mode INTO old_mode
          FROM procurement_recommendations r JOIN runs ru ON ru.run_id=r.run_id
         WHERE r.recommendation_id=old_recommendation;
        IF old_mode='INTERNAL_DRAFT_ONLY' THEN
            RAISE EXCEPTION 'Monday review decisions are append-only';
        END IF;
    END IF;
    target_recommendation := CASE WHEN TG_OP='DELETE' THEN OLD.recommendation_id ELSE NEW.recommendation_id END;
    IF target_recommendation IS NULL THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    SELECT r.run_id,r.variant_id,r.input_fingerprint,r.recommended_cases,
           r.recommended_loose_units,r.units_per_case,r.offer_id,r.vendor_id,
           ru.workflow_stage,ru.input_fingerprint,ru.run_type,
           r.frozen_loose_order_allowed,r.frozen_loose_unit_fee,
           r.frozen_qualifying_units_per_case,ru.procurement_output_mode
      INTO parent_run,parent_variant,parent_fingerprint,parent_cases,parent_loose,parent_pack,
           parent_offer,parent_vendor,run_stage,run_fingerprint,parent_run_type,loose_allowed,
           loose_fee,qualifying_pack,parent_mode
      FROM procurement_recommendations r JOIN runs ru ON ru.run_id=r.run_id
     WHERE r.recommendation_id=target_recommendation;
    IF parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF parent_run_type IS DISTINCT FROM 'MONDAY_PROCUREMENT' THEN
        RAISE EXCEPTION 'internal DRAFT review has an invalid parent run';
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Monday review decisions are append-only';
    END IF;
    IF parent_run IS NULL OR parent_run IS DISTINCT FROM NEW.run_id
       OR parent_fingerprint IS DISTINCT FROM NEW.input_fingerprint
       OR run_fingerprint IS DISTINCT FROM parent_fingerprint
       OR run_stage <> 'AWAITING_REVIEW'
       OR NEW.decision_type <> 'PROCUREMENT_RECOMMENDATION'
       OR NEW.scope <> 'RUN_ONLY' OR NEW.effective_from IS NOT NULL OR NEW.effective_through IS NOT NULL
       OR NEW.decided_by IS NULL OR btrim(NEW.decided_by)=''
       OR NEW.approved_units IS DISTINCT FROM NEW.approved_cases * parent_pack + NEW.approved_loose_units
       OR NEW.approved_loose_units >= parent_pack
       OR (NEW.approved_loose_units > 0 AND loose_allowed IS DISTINCT FROM TRUE) THEN
        RAISE EXCEPTION 'Monday review decision does not match its frozen run recommendation';
    END IF;
    IF NEW.action='ACCEPT' AND (
        NEW.approved_cases IS DISTINCT FROM parent_cases
        OR NEW.approved_loose_units IS DISTINCT FROM parent_loose
    ) THEN
        RAISE EXCEPTION 'ACCEPT must preserve the recommended quantity';
    END IF;
    IF NEW.action='EDIT_QUANTITY' AND (NEW.comment IS NULL OR btrim(NEW.comment)='') THEN
        RAISE EXCEPTION 'EDIT_QUANTITY requires a review comment';
    END IF;
    IF NEW.action='REJECT' AND (
        NEW.approved_cases <> 0 OR NEW.approved_loose_units <> 0 OR NEW.approved_units <> 0
        OR NEW.comment IS NULL OR btrim(NEW.comment)=''
    ) THEN
        RAISE EXCEPTION 'REJECT requires zero quantity and a review comment';
    END IF;
    IF NEW.action='REJECT' THEN
        derived_cost := 0;
        derived_case_price := NULL;
        derived_merchandise := 0;
        derived_loose_order_fee := 0;
        derived_total := 0;
    ELSE
        SELECT s.unit_price,s.case_price INTO derived_cost,derived_case_price
          FROM run_price_snapshots s
         WHERE s.run_id=parent_run AND s.offer_id=parent_offer
           AND (
                s.level_type='BASE'
                OR (s.level_type='BREAK' AND s.break_unit='CS' AND s.break_qty<=NEW.approved_cases)
                OR (s.level_type='BREAK' AND s.break_unit='BT' AND s.break_qty<=NEW.approved_cases*qualifying_pack)
           )
         ORDER BY s.unit_price ASC,s.run_price_snapshot_id DESC LIMIT 1;
        IF derived_cost IS NULL THEN RAISE EXCEPTION 'reviewed quantity has no frozen applicable price'; END IF;
        derived_merchandise := round((
            NEW.approved_cases * COALESCE(derived_case_price,parent_pack*derived_cost)
            + NEW.approved_loose_units * derived_cost
        )::numeric,2);
        derived_loose_order_fee := CASE
            WHEN NEW.approved_loose_units>0 THEN loose_fee ELSE 0 END;
        derived_total := round((derived_merchandise+derived_loose_order_fee)::numeric,2);
    END IF;
    IF NEW.approved_unit_cost IS DISTINCT FROM derived_cost
       OR NEW.approved_line_total IS DISTINCT FROM derived_total
       OR (NEW.evidence_json #>> '{review,approved_case_price}')::numeric
            IS DISTINCT FROM derived_case_price
       OR (NEW.evidence_json #>> '{review,approved_merchandise_total}')::numeric
            IS DISTINCT FROM derived_merchandise
       OR (NEW.evidence_json #>> '{review,approved_loose_order_fee}')::numeric
            IS DISTINCT FROM derived_loose_order_fee THEN
        RAISE EXCEPTION 'reviewed quantity economics do not match frozen prices and vendor fees';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_monday_review_decision ON review_decisions;
CREATE TRIGGER trg_validate_monday_review_decision
BEFORE INSERT OR UPDATE OR DELETE ON review_decisions
FOR EACH ROW EXECUTE FUNCTION validate_monday_review_decision();

-- Preserve Packet 3's validation for all existing/non-emergency PO workflows.
-- The reviewed internal-DRAFT mode gets an additional, edit-aware validator.
DO $$
BEGIN
    IF to_regprocedure('validate_pre012_monday_po_line()') IS NULL THEN
        ALTER FUNCTION validate_monday_po_line() RENAME TO validate_pre012_monday_po_line;
    END IF;
END
$$;
CREATE OR REPLACE FUNCTION is_internal_draft_po(target_po_id UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT COALESCE((
        SELECT r.procurement_output_mode='INTERNAL_DRAFT_ONLY'
          FROM purchase_orders p JOIN runs r ON r.run_id=p.run_id
         WHERE p.po_id=target_po_id
    ),FALSE)
$$;
DROP TRIGGER IF EXISTS trg_validate_monday_po_line ON purchase_order_lines;
CREATE TRIGGER trg_validate_monday_po_line
BEFORE INSERT OR UPDATE OF po_id,variant_id,offer_id,recommendation_id,
    review_decision_id,supplier_sku,cases,loose_units,ordered_units,line_status
ON purchase_order_lines
FOR EACH ROW WHEN (NOT is_internal_draft_po(NEW.po_id))
EXECUTE FUNCTION validate_pre012_monday_po_line();

CREATE OR REPLACE FUNCTION validate_emergency_monday_po_line()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_vendor UUID; parent_run UUID; parent_status TEXT; parent_po_fingerprint TEXT; parent_run_fingerprint TEXT;
    parent_mode TEXT; old_parent_mode TEXT;
    offer_vendor UUID; offer_variant TEXT; offer_active BOOLEAN; offer_confidence TEXT; offer_package TEXT;
    offer_supplier_sku TEXT; offer_units_per_case NUMERIC(12,4); offer_qualifying NUMERIC(12,4);
    recommendation_run UUID; recommendation_variant TEXT; recommendation_vendor UUID; recommendation_offer BIGINT;
    recommendation_fingerprint TEXT; recommendation_sku TEXT; recommendation_qualifying INTEGER;
    decision_run UUID; decision_recommendation BIGINT; decision_scope TEXT; decision_action TEXT;
    decision_type TEXT; decision_actor TEXT; decision_cases NUMERIC(14,4); decision_loose NUMERIC(14,4);
    decision_fingerprint TEXT; decision_cost NUMERIC(14,4); decision_total NUMERIC(14,2);
BEGIN
    SELECT p.vendor_id,p.run_id,p.po_status,p.input_fingerprint,r.input_fingerprint,r.procurement_output_mode
      INTO parent_vendor,parent_run,parent_status,parent_po_fingerprint,parent_run_fingerprint,parent_mode
      FROM purchase_orders p JOIN runs r ON r.run_id=p.run_id WHERE p.po_id=NEW.po_id;
    IF TG_OP='UPDATE' THEN
        SELECT r.procurement_output_mode INTO old_parent_mode
          FROM purchase_orders p JOIN runs r ON r.run_id=p.run_id WHERE p.po_id=OLD.po_id;
    END IF;
    IF old_parent_mode='INTERNAL_DRAFT_ONLY' AND NEW.po_id IS DISTINCT FROM OLD.po_id THEN
        RAISE EXCEPTION 'emergency Monday PO lines cannot be reparented';
    END IF;
    IF parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY'
       AND old_parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY' THEN
        RETURN NEW;
    END IF;
    IF parent_vendor IS NULL THEN RAISE EXCEPTION 'PO line parent is missing'; END IF;
    IF NOT is_procurement_eligible_variant(NEW.variant_id) THEN
        RAISE EXCEPTION 'PO line requires CURRENT, active, LIVE Variant ID %',NEW.variant_id;
    END IF;
    SELECT vendor_id,variant_id,active,confidence,package_type,supplier_sku,
           shopify_units_per_case,qualifying_units_per_case
      INTO offer_vendor,offer_variant,offer_active,offer_confidence,offer_package,
           offer_supplier_sku,offer_units_per_case,offer_qualifying
      FROM supplier_offers WHERE offer_id=NEW.offer_id;
    IF offer_vendor IS DISTINCT FROM parent_vendor OR offer_variant IS DISTINCT FROM NEW.variant_id
       OR offer_active IS DISTINCT FROM TRUE OR offer_confidence IS DISTINCT FROM 'VERIFIED'
       OR offer_package IS DISTINCT FROM 'STANDARD'
       OR offer_supplier_sku IS NULL OR btrim(offer_supplier_sku)=''
       OR NEW.supplier_sku IS DISTINCT FROM offer_supplier_sku OR offer_units_per_case IS NULL OR offer_units_per_case <= 0
       OR NEW.cases <> trunc(NEW.cases) OR NEW.loose_units <> trunc(NEW.loose_units)
       OR NEW.ordered_units <> trunc(NEW.ordered_units)
       OR NEW.ordered_units IS DISTINCT FROM NEW.cases * offer_units_per_case + NEW.loose_units THEN
        RAISE EXCEPTION 'PO line offer/vendor/variant relationship is invalid';
    END IF;
    SELECT run_id,variant_id,vendor_id,offer_id,input_fingerprint,
           frozen_supplier_sku,frozen_qualifying_units_per_case
      INTO recommendation_run,recommendation_variant,recommendation_vendor,recommendation_offer,
           recommendation_fingerprint,recommendation_sku,recommendation_qualifying
      FROM procurement_recommendations WHERE recommendation_id=NEW.recommendation_id;
    IF recommendation_run IS DISTINCT FROM parent_run OR recommendation_variant IS DISTINCT FROM NEW.variant_id
       OR recommendation_vendor IS DISTINCT FROM parent_vendor OR recommendation_offer IS DISTINCT FROM NEW.offer_id
       OR recommendation_fingerprint IS DISTINCT FROM parent_run_fingerprint
       OR recommendation_sku IS DISTINCT FROM offer_supplier_sku
       OR recommendation_qualifying IS DISTINCT FROM offer_qualifying
       OR NEW.input_fingerprint IS DISTINCT FROM parent_run_fingerprint
       OR parent_po_fingerprint IS DISTINCT FROM parent_run_fingerprint THEN
        RAISE EXCEPTION 'PO line recommendation relationship is invalid';
    END IF;
    SELECT d.run_id,d.recommendation_id,d.scope,d.action,d.decision_type,d.decided_by,
           d.approved_cases,d.approved_loose_units,d.input_fingerprint,
           d.approved_unit_cost,d.approved_line_total
      INTO decision_run,decision_recommendation,decision_scope,decision_action,decision_type,decision_actor,
           decision_cases,decision_loose,decision_fingerprint,decision_cost,decision_total
      FROM review_decisions d WHERE d.decision_id=NEW.review_decision_id;
    IF decision_run IS DISTINCT FROM parent_run OR decision_recommendation IS DISTINCT FROM NEW.recommendation_id
       OR decision_scope <> 'RUN_ONLY' OR decision_action NOT IN ('ACCEPT','EDIT_QUANTITY')
       OR decision_type <> 'PROCUREMENT_RECOMMENDATION' OR decision_actor IS NULL OR btrim(decision_actor)=''
       OR decision_cases IS DISTINCT FROM NEW.cases OR decision_loose IS DISTINCT FROM NEW.loose_units
       OR decision_fingerprint IS DISTINCT FROM parent_run_fingerprint
       OR decision_cost IS DISTINCT FROM NEW.unit_cost OR decision_total IS DISTINCT FROM NEW.line_total THEN
        RAISE EXCEPTION 'PO line requires matching RUN_ONLY human approval';
    END IF;
    IF parent_status IN ('REVIEW','FINAL','CANCELLED') AND TG_OP='INSERT' THEN
        RAISE EXCEPTION 'REVIEW, FINAL, or CANCELLED PO cannot accept new lines';
    END IF;
    IF parent_status IN ('FINAL','CANCELLED') AND NEW.line_status='DRAFT' THEN
        RAISE EXCEPTION 'FINAL or CANCELLED PO cannot contain a DRAFT line';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_emergency_monday_po_line ON purchase_order_lines;
CREATE TRIGGER trg_validate_emergency_monday_po_line
BEFORE INSERT OR UPDATE OF po_id,variant_id,offer_id,recommendation_id,
    review_decision_id,supplier_sku,cases,loose_units,ordered_units,unit_cost,
    line_total,input_fingerprint,line_status,reconciliation_status
ON purchase_order_lines
FOR EACH ROW EXECUTE FUNCTION validate_emergency_monday_po_line();

CREATE OR REPLACE FUNCTION enforce_emergency_monday_draft_only()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_run UUID; old_run UUID; target_mode TEXT; old_mode TEXT;
BEGIN
    IF TG_TABLE_NAME='purchase_orders' THEN
        target_run := CASE WHEN TG_OP='DELETE' THEN OLD.run_id ELSE NEW.run_id END;
        old_run := CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.run_id END;
    ELSE
        SELECT run_id INTO target_run FROM purchase_orders WHERE po_id=CASE WHEN TG_OP='DELETE' THEN OLD.po_id ELSE NEW.po_id END;
        IF TG_OP<>'INSERT' THEN SELECT run_id INTO old_run FROM purchase_orders WHERE po_id=OLD.po_id; END IF;
    END IF;
    SELECT procurement_output_mode INTO target_mode FROM runs WHERE run_id=target_run;
    SELECT procurement_output_mode INTO old_mode FROM runs WHERE run_id=old_run;
    IF target_mode='INTERNAL_DRAFT_ONLY' OR old_mode='INTERNAL_DRAFT_ONLY' THEN
        IF TG_TABLE_NAME='purchase_orders' THEN
            IF TG_OP<>'DELETE' AND (
                NEW.po_status <> 'DRAFT' OR NEW.finalized_at IS NOT NULL
                OR NEW.shopify_import_status <> 'NOT_IMPORTED' OR NEW.shopify_po_reference IS NOT NULL
            ) THEN RAISE EXCEPTION 'emergency Monday workflow permits DRAFT POs only'; END IF;
        ELSIF TG_OP<>'DELETE' AND NEW.line_status <> 'DRAFT' THEN
            RAISE EXCEPTION 'emergency Monday workflow permits DRAFT PO lines only';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_emergency_monday_draft_only_po ON purchase_orders;
CREATE TRIGGER trg_emergency_monday_draft_only_po
BEFORE INSERT OR UPDATE OR DELETE ON purchase_orders
FOR EACH ROW EXECUTE FUNCTION enforce_emergency_monday_draft_only();
DROP TRIGGER IF EXISTS trg_emergency_monday_draft_only_line ON purchase_order_lines;
CREATE TRIGGER trg_emergency_monday_draft_only_line
BEFORE INSERT OR UPDATE OR DELETE ON purchase_order_lines
FOR EACH ROW EXECUTE FUNCTION enforce_emergency_monday_draft_only();

CREATE OR REPLACE FUNCTION protect_monday_reviewed_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_run UUID; new_run UUID;
    old_stage TEXT; new_stage TEXT;
    old_mode TEXT; new_mode TEXT;
    new_fingerprint TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN old_run := OLD.run_id; END IF;
    IF TG_OP <> 'DELETE' THEN new_run := NEW.run_id; END IF;
    SELECT workflow_stage,procurement_output_mode INTO old_stage,old_mode FROM runs WHERE run_id=old_run;
    SELECT workflow_stage,procurement_output_mode,input_fingerprint
      INTO new_stage,new_mode,new_fingerprint FROM runs WHERE run_id=new_run;
    IF old_mode='INTERNAL_DRAFT_ONLY' THEN
        IF TG_OP='UPDATE' AND new_run IS DISTINCT FROM old_run THEN
            RAISE EXCEPTION 'Monday analysis evidence cannot be reparented';
        END IF;
        IF old_stage IS DISTINCT FROM 'PREPARING' THEN
            RAISE EXCEPTION 'Monday analysis evidence is immutable after review begins';
        END IF;
    END IF;
    IF new_mode='INTERNAL_DRAFT_ONLY' THEN
        IF new_stage IS DISTINCT FROM 'PREPARING' THEN
            RAISE EXCEPTION 'Monday analysis evidence is immutable after review begins';
        END IF;
        IF TG_TABLE_NAME='forecast_results'
           AND (to_jsonb(NEW)->>'input_fingerprint') IS DISTINCT FROM new_fingerprint THEN
            RAISE EXCEPTION 'Monday forecast fingerprint must match its frozen run';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_monday_recommendation_evidence ON procurement_recommendations;
CREATE TRIGGER trg_protect_monday_recommendation_evidence BEFORE INSERT OR UPDATE OR DELETE ON procurement_recommendations
FOR EACH ROW EXECUTE FUNCTION protect_monday_reviewed_evidence();
DROP TRIGGER IF EXISTS trg_protect_monday_forecast_evidence ON forecast_results;
CREATE TRIGGER trg_protect_monday_forecast_evidence BEFORE INSERT OR UPDATE OR DELETE ON forecast_results
FOR EACH ROW EXECUTE FUNCTION protect_monday_reviewed_evidence();
DROP TRIGGER IF EXISTS trg_protect_monday_inventory_evidence ON inventory_snapshots;
CREATE TRIGGER trg_protect_monday_inventory_evidence BEFORE INSERT OR UPDATE OR DELETE ON inventory_snapshots
FOR EACH ROW EXECUTE FUNCTION protect_monday_reviewed_evidence();
DROP TRIGGER IF EXISTS trg_protect_monday_price_evidence ON run_price_snapshots;
CREATE TRIGGER trg_protect_monday_price_evidence BEFORE INSERT OR UPDATE OR DELETE ON run_price_snapshots
FOR EACH ROW EXECUTE FUNCTION protect_monday_reviewed_evidence();
DROP TRIGGER IF EXISTS trg_protect_monday_exception_evidence ON exceptions;
CREATE TRIGGER trg_protect_monday_exception_evidence BEFORE INSERT OR UPDATE OR DELETE ON exceptions
FOR EACH ROW EXECUTE FUNCTION protect_monday_reviewed_evidence();

CREATE OR REPLACE FUNCTION protect_built_monday_draft()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_run UUID; new_run UUID;
    old_stage TEXT; new_stage TEXT;
    old_mode TEXT; new_mode TEXT;
BEGIN
    IF TG_TABLE_NAME='purchase_orders' THEN
        IF TG_OP <> 'INSERT' THEN old_run := OLD.run_id; END IF;
        IF TG_OP <> 'DELETE' THEN new_run := NEW.run_id; END IF;
    ELSE
        IF TG_OP <> 'INSERT' THEN
            SELECT run_id INTO old_run FROM purchase_orders WHERE po_id=OLD.po_id;
        END IF;
        IF TG_OP <> 'DELETE' THEN
            SELECT run_id INTO new_run FROM purchase_orders WHERE po_id=NEW.po_id;
        END IF;
    END IF;
    SELECT workflow_stage,procurement_output_mode INTO old_stage,old_mode FROM runs WHERE run_id=old_run;
    SELECT workflow_stage,procurement_output_mode INTO new_stage,new_mode FROM runs WHERE run_id=new_run;
    IF (old_mode='INTERNAL_DRAFT_ONLY' AND old_stage IN ('DRAFTS_BUILT','PACKET_BUILT','FAILED'))
       OR (new_mode='INTERNAL_DRAFT_ONLY' AND new_stage IN ('DRAFTS_BUILT','PACKET_BUILT','FAILED')) THEN
        RAISE EXCEPTION 'built Monday DRAFT evidence is immutable';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_built_monday_po ON purchase_orders;
CREATE TRIGGER trg_protect_built_monday_po BEFORE INSERT OR UPDATE OR DELETE ON purchase_orders
FOR EACH ROW EXECUTE FUNCTION protect_built_monday_draft();
DROP TRIGGER IF EXISTS trg_protect_built_monday_po_line ON purchase_order_lines;
CREATE TRIGGER trg_protect_built_monday_po_line BEFORE INSERT OR UPDATE OR DELETE ON purchase_order_lines
FOR EACH ROW EXECUTE FUNCTION protect_built_monday_draft();

CREATE OR REPLACE FUNCTION protect_monday_run_artifact()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_fingerprint TEXT; parent_stage TEXT; parent_mode TEXT; expected_key TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Monday run artifacts are append-only'; END IF;
    SELECT input_fingerprint,workflow_stage,procurement_output_mode
      INTO parent_fingerprint,parent_stage,parent_mode FROM runs WHERE run_id=NEW.run_id;
    IF parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY'
       OR parent_fingerprint IS DISTINCT FROM NEW.input_fingerprint THEN
        RAISE EXCEPTION 'Monday artifact fingerprint must match its frozen run';
    END IF;
    IF (NEW.artifact_type='VENDOR_INTERNAL_CSV' AND parent_stage <> 'DRAFTS_BUILT')
       OR (NEW.artifact_type='EMERGENCY_REVIEW_PACKET' AND parent_stage <> 'DRAFTS_BUILT') THEN
        RAISE EXCEPTION 'Monday artifact does not match the run workflow stage';
    END IF;
    expected_key := CASE
        WHEN NEW.artifact_type='VENDOR_INTERNAL_CSV' THEN
            'monday-runs/' || NEW.run_id::text || '/vendor-' || NEW.vendor_id::text ||
            '/' || NEW.sha256 || '.internal.csv'
        ELSE
            'monday-runs/' || NEW.run_id::text || '/packet/' || NEW.sha256 || '.review.zip'
        END;
    IF NEW.storage_key IS DISTINCT FROM expected_key
       OR NEW.packet_build_transaction_id IS DISTINCT FROM txid_current() THEN
        RAISE EXCEPTION 'Monday artifact requires its canonical content-addressed key and build transaction';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER IF EXISTS trg_protect_monday_run_artifact ON monday_run_artifacts;
CREATE TRIGGER trg_protect_monday_run_artifact BEFORE INSERT OR UPDATE OR DELETE ON monday_run_artifacts
FOR EACH ROW EXECUTE FUNCTION protect_monday_run_artifact();

CREATE OR REPLACE FUNCTION validate_emergency_monday_run()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    bad_count BIGINT;
    draft_count BIGINT;
    artifact_csv_count BIGINT;
    packet_count BIGINT;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.procurement_output_mode='INTERNAL_DRAFT_ONLY'
           AND NEW.run_type IS DISTINCT FROM 'MONDAY_PROCUREMENT' THEN
            RAISE EXCEPTION 'internal DRAFT output requires a Monday Procurement run';
        END IF;
        IF NEW.procurement_output_mode='INTERNAL_DRAFT_ONLY'
           AND (NEW.status <> 'RUNNING' OR NEW.workflow_stage <> 'PREPARING'
                OR NEW.procurement_input_manifest IS NULL
                OR NEW.input_fingerprint IS NULL
                OR NEW.input_fingerprint !~ '^[0-9a-f]{64}$') THEN
            RAISE EXCEPTION 'Monday Procurement run must begin RUNNING/PREPARING';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.procurement_output_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.procurement_output_mode='INTERNAL_DRAFT_ONLY' THEN
            RAISE EXCEPTION 'existing runs cannot acquire internal DRAFT output mode';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Monday Procurement runs are durable audit evidence';
    END IF;
    IF NEW.run_type IS DISTINCT FROM OLD.run_type
       OR NEW.procurement_output_mode IS DISTINCT FROM OLD.procurement_output_mode
       OR NEW.business_date IS DISTINCT FROM OLD.business_date
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
       OR NEW.source_data_through IS DISTINCT FROM OLD.source_data_through
       OR NEW.model_version IS DISTINCT FROM OLD.model_version
       OR NEW.procurement_input_manifest IS DISTINCT FROM OLD.procurement_input_manifest
       OR NOT ((NEW.workflow_stage='FAILED' AND NEW.status='FAILED')
               OR (NEW.workflow_stage<>'FAILED' AND NEW.status='RUNNING')) THEN
        RAISE EXCEPTION 'Monday Procurement run identity and inputs are immutable';
    END IF;
    IF NEW.workflow_stage IS DISTINCT FROM OLD.workflow_stage
       AND NOT (
           (OLD.workflow_stage='PREPARING' AND NEW.workflow_stage IN ('AWAITING_REVIEW','FAILED'))
           OR (OLD.workflow_stage='AWAITING_REVIEW' AND NEW.workflow_stage IN ('REVIEWED','FAILED'))
           OR (OLD.workflow_stage='REVIEWED' AND NEW.workflow_stage IN ('DRAFTS_BUILT','FAILED'))
           OR (OLD.workflow_stage='DRAFTS_BUILT' AND NEW.workflow_stage IN ('PACKET_BUILT','FAILED'))
       ) THEN
        RAISE EXCEPTION 'invalid Monday Procurement workflow-stage transition';
    END IF;
    IF NEW.workflow_stage IN ('AWAITING_REVIEW','REVIEWED','DRAFTS_BUILT','PACKET_BUILT') THEN
        SELECT count(*) INTO bad_count FROM procurement_recommendations r
         WHERE r.run_id=NEW.run_id AND r.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint;
        IF bad_count <> 0 THEN
            RAISE EXCEPTION 'Monday recommendations do not match the frozen run fingerprint';
        END IF;
        SELECT count(*) INTO bad_count FROM forecast_results f
         WHERE f.run_id=NEW.run_id AND f.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint;
        IF bad_count <> 0 THEN
            RAISE EXCEPTION 'Monday forecasts do not match the frozen run fingerprint';
        END IF;
    END IF;
    IF NEW.workflow_stage IN ('REVIEWED','DRAFTS_BUILT','PACKET_BUILT') THEN
        SELECT count(*) INTO bad_count
          FROM procurement_recommendations r
          LEFT JOIN review_decisions d ON d.recommendation_id=r.recommendation_id
         WHERE r.run_id=NEW.run_id
           AND (d.decision_id IS NULL OR d.run_id IS DISTINCT FROM NEW.run_id
                OR d.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint);
        IF bad_count <> 0 OR EXISTS (
            SELECT 1 FROM exceptions WHERE run_id=NEW.run_id
             AND status='OPEN' AND severity IN ('HIGH','CRITICAL')
        ) THEN
            RAISE EXCEPTION 'Monday run cannot advance before complete fresh review and blocker resolution';
        END IF;
    END IF;
    IF NEW.workflow_stage IN ('DRAFTS_BUILT','PACKET_BUILT') THEN
        SELECT count(*) INTO draft_count FROM purchase_orders WHERE run_id=NEW.run_id;
        IF EXISTS (
            SELECT 1 FROM purchase_orders p WHERE p.run_id=NEW.run_id
             AND (p.po_status<>'DRAFT' OR p.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint)
        ) OR EXISTS (
            SELECT 1
              FROM procurement_recommendations r
              JOIN review_decisions d ON d.recommendation_id=r.recommendation_id
              LEFT JOIN purchase_order_lines l ON l.recommendation_id=r.recommendation_id
             WHERE r.run_id=NEW.run_id AND (
                 (d.action IN ('ACCEPT','EDIT_QUANTITY') AND d.approved_units>0 AND l.po_line_id IS NULL)
                 OR ((d.action='REJECT' OR d.approved_units=0) AND l.po_line_id IS NOT NULL)
                 OR (l.po_line_id IS NOT NULL AND (
                     l.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint
                     OR l.cases IS DISTINCT FROM d.approved_cases
                     OR l.loose_units IS DISTINCT FROM d.approved_loose_units
                     OR l.ordered_units IS DISTINCT FROM d.approved_units
                     OR l.unit_cost IS DISTINCT FROM d.approved_unit_cost
                     OR l.line_total IS DISTINCT FROM d.approved_line_total
                     OR l.line_status<>'DRAFT'
                 ))
             )
        ) OR EXISTS (
            SELECT 1 FROM purchase_orders p
            LEFT JOIN LATERAL (
                SELECT count(*) AS line_count,
                       COALESCE(sum(
                           (d.evidence_json #>> '{review,approved_merchandise_total}')::numeric
                       ),0)::numeric(14,2) AS merchandise,
                       COALESCE(sum(
                           (d.evidence_json #>> '{review,approved_loose_order_fee}')::numeric
                       ),0)::numeric(14,2) AS loose_fees,
                       COALESCE(sum(l.cases),0) AS cases
                  FROM purchase_order_lines l
                  JOIN review_decisions d ON d.decision_id=l.review_decision_id
                 WHERE l.po_id=p.po_id
            ) totals ON TRUE
            LEFT JOIN LATERAL (
                SELECT count(DISTINCT jsonb_build_array(
                           r.frozen_minimum_type,r.frozen_minimum_value,
                           r.frozen_below_minimum_fee)) AS contract_count,
                       min(r.frozen_minimum_type) AS minimum_type,
                       min(r.frozen_minimum_value) AS minimum_value,
                       min(r.frozen_below_minimum_fee) AS below_fee
                  FROM procurement_recommendations r
                 WHERE r.run_id=p.run_id AND r.vendor_id=p.vendor_id
            ) terms ON TRUE
            WHERE p.run_id=NEW.run_id AND (
                totals.line_count=0 OR terms.contract_count<>1
                OR p.merchandise_total IS DISTINCT FROM totals.merchandise
                OR p.below_vendor_minimum IS DISTINCT FROM CASE
                    WHEN terms.minimum_type='DOLLAR' THEN totals.merchandise<terms.minimum_value
                    WHEN terms.minimum_type='CASE' THEN totals.cases<terms.minimum_value
                    ELSE FALSE END
                OR p.delivery_fee IS DISTINCT FROM CASE WHEN CASE
                    WHEN terms.minimum_type='DOLLAR' THEN totals.merchandise<terms.minimum_value
                    WHEN terms.minimum_type='CASE' THEN totals.cases<terms.minimum_value
                    ELSE FALSE END THEN totals.loose_fees+terms.below_fee
                    ELSE totals.loose_fees END
                OR p.po_total IS DISTINCT FROM round(p.merchandise_total+p.delivery_fee,2)
                OR (p.reconciliation_evidence->>'minimum_disposition')
                   IS DISTINCT FROM CASE WHEN CASE
                       WHEN terms.minimum_type='DOLLAR' THEN totals.merchandise<terms.minimum_value
                       WHEN terms.minimum_type='CASE' THEN totals.cases<terms.minimum_value
                       ELSE FALSE END THEN 'PAY_FEE' ELSE 'NOT_APPLICABLE' END
                OR (p.reconciliation_evidence->>'loose_order_fee_total')::numeric
                   IS DISTINCT FROM totals.loose_fees
                OR (p.reconciliation_evidence->>'below_minimum_fee')::numeric
                   IS DISTINCT FROM CASE WHEN CASE
                       WHEN terms.minimum_type='DOLLAR' THEN totals.merchandise<terms.minimum_value
                       WHEN terms.minimum_type='CASE' THEN totals.cases<terms.minimum_value
                       ELSE FALSE END THEN terms.below_fee ELSE 0 END
            )
        ) THEN
            RAISE EXCEPTION 'Monday DRAFT set is incomplete or its reviewed economics do not reconcile';
        END IF;
    END IF;
    IF NEW.workflow_stage='PACKET_BUILT' THEN
        SELECT count(*) INTO packet_count FROM monday_run_artifacts
         WHERE run_id=NEW.run_id AND artifact_type='EMERGENCY_REVIEW_PACKET';
        SELECT count(*) INTO artifact_csv_count FROM monday_run_artifacts
         WHERE run_id=NEW.run_id AND artifact_type='VENDOR_INTERNAL_CSV';
        IF packet_count<>1 OR artifact_csv_count<>draft_count
           OR NOT EXISTS (
               SELECT 1 FROM monday_packet_build_events e
                WHERE e.run_id=NEW.run_id AND e.transaction_id=txid_current()
                  AND e.input_fingerprint=NEW.input_fingerprint
                  AND e.csv_count=artifact_csv_count
                  AND e.artifact_count=artifact_csv_count+1
                  AND e.artifact_set_sha256=monday_artifact_set_sha256(NEW.run_id)
           )
           OR EXISTS (
               (SELECT p.vendor_id FROM purchase_orders p WHERE p.run_id=NEW.run_id)
               EXCEPT
               (SELECT a.vendor_id FROM monday_run_artifacts a
                 WHERE a.run_id=NEW.run_id AND a.artifact_type='VENDOR_INTERNAL_CSV')
           )
           OR EXISTS (
               (SELECT a.vendor_id FROM monday_run_artifacts a
                 WHERE a.run_id=NEW.run_id AND a.artifact_type='VENDOR_INTERNAL_CSV')
               EXCEPT
               (SELECT p.vendor_id FROM purchase_orders p WHERE p.run_id=NEW.run_id)
           ) THEN
            RAISE EXCEPTION 'Monday packet requires one packet and one internal CSV per vendor DRAFT';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_emergency_monday_run ON runs;
CREATE TRIGGER trg_validate_emergency_monday_run
BEFORE INSERT OR UPDATE OR DELETE ON runs
FOR EACH ROW EXECUTE FUNCTION validate_emergency_monday_run();

CREATE OR REPLACE FUNCTION assert_monday_packet_build_commit()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_stage TEXT; parent_fingerprint TEXT; actual_count BIGINT; actual_csv BIGINT;
BEGIN
    SELECT workflow_stage,input_fingerprint INTO parent_stage,parent_fingerprint
      FROM runs WHERE run_id=NEW.run_id;
    SELECT count(*),count(*) FILTER (WHERE artifact_type='VENDOR_INTERNAL_CSV')
      INTO actual_count,actual_csv FROM monday_run_artifacts WHERE run_id=NEW.run_id;
    IF parent_stage IS DISTINCT FROM 'PACKET_BUILT'
       OR parent_fingerprint IS DISTINCT FROM NEW.input_fingerprint
       OR actual_count IS DISTINCT FROM NEW.artifact_count
       OR actual_csv IS DISTINCT FROM NEW.csv_count
       OR monday_artifact_set_sha256(NEW.run_id) IS DISTINCT FROM NEW.artifact_set_sha256
       OR NOT EXISTS (
           SELECT 1 FROM monday_run_artifacts a WHERE a.run_id=NEW.run_id
             AND a.artifact_type='EMERGENCY_REVIEW_PACKET'
             AND a.sha256=NEW.packet_sha256
             AND a.packet_build_transaction_id=NEW.transaction_id
       ) THEN
        RAISE EXCEPTION 'packet-build transaction did not commit an exact terminal artifact set';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_assert_monday_packet_build_commit ON monday_packet_build_events;
CREATE CONSTRAINT TRIGGER trg_assert_monday_packet_build_commit
AFTER INSERT ON monday_packet_build_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_monday_packet_build_commit();

INSERT INTO meta(key,value) VALUES ('monday_review_draft_packet_contract','v2')
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now();
