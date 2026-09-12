-- Monday MVP Packet 3: Procurement run and open-PO reconciliation foundation.

ALTER TABLE runs ADD COLUMN IF NOT EXISTS business_date DATE;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS input_fingerprint TEXT;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS workflow_stage TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='ck_runs_input_fingerprint'
          AND conrelid='runs'::regclass
    ) THEN
        ALTER TABLE runs ADD CONSTRAINT ck_runs_input_fingerprint
            CHECK (input_fingerprint IS NULL OR input_fingerprint ~ '^[0-9a-f]{64}$');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='ck_runs_workflow_stage'
          AND conrelid='runs'::regclass
    ) THEN
        ALTER TABLE runs ADD CONSTRAINT ck_runs_workflow_stage CHECK (
            workflow_stage IS NULL OR workflow_stage IN (
                'PREPARING','AWAITING_REVIEW','REVIEWED','DRAFTS_BUILT','PACKET_BUILT','FAILED'
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='ck_runs_monday_procurement_identity'
          AND conrelid='runs'::regclass
    ) THEN
        ALTER TABLE runs ADD CONSTRAINT ck_runs_monday_procurement_identity CHECK (
            run_type <> 'MONDAY_PROCUREMENT'
            OR (
                business_date IS NOT NULL
                AND idempotency_key IS NOT NULL
                AND btrim(idempotency_key) <> ''
                AND input_fingerprint IS NOT NULL
                AND workflow_stage IS NOT NULL
            )
        );
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_procurement_run_idempotency
    ON runs(run_type,idempotency_key)
    WHERE idempotency_key IS NOT NULL;

ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS input_fingerprint TEXT;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS po_revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS reviewed_by TEXT;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;
ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS reconciliation_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE purchase_order_lines
    ADD COLUMN IF NOT EXISTS recommendation_id BIGINT
        REFERENCES procurement_recommendations(recommendation_id) ON DELETE RESTRICT;
ALTER TABLE purchase_order_lines
    ADD COLUMN IF NOT EXISTS review_decision_id BIGINT
        REFERENCES review_decisions(decision_id) ON DELETE RESTRICT;
ALTER TABLE purchase_order_lines ALTER COLUMN recommendation_id SET NOT NULL;
ALTER TABLE purchase_order_lines ALTER COLUMN review_decision_id SET NOT NULL;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS ordered_units NUMERIC(14,4) NOT NULL DEFAULT 0;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS received_units NUMERIC(14,4) NOT NULL DEFAULT 0;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS cancelled_units NUMERIC(14,4) NOT NULL DEFAULT 0;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS expected_receipt_at TIMESTAMPTZ;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS line_status TEXT NOT NULL DEFAULT 'DRAFT';
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS reconciliation_status TEXT NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS reconciliation_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS input_fingerprint TEXT;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ;
ALTER TABLE purchase_order_lines ADD COLUMN IF NOT EXISTS last_reconciled_by TEXT;
ALTER TABLE purchase_order_lines
    ADD COLUMN IF NOT EXISTS open_units NUMERIC(14,4)
        GENERATED ALWAYS AS (
            GREATEST(ordered_units - received_units - cancelled_units, 0::numeric)
        ) STORED;

ALTER TABLE exceptions ADD COLUMN IF NOT EXISTS po_line_id BIGINT
    REFERENCES purchase_order_lines(po_line_id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_po_line_reconciliation_exception
    ON exceptions(po_line_id,exception_type)
    WHERE po_line_id IS NOT NULL AND status='OPEN';

CREATE TABLE IF NOT EXISTS po_operational_events (
    po_operational_event_id BIGSERIAL PRIMARY KEY,
    po_id UUID NOT NULL REFERENCES purchase_orders(po_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('FINALIZATION_AUTHORITY','SHOPIFY_IMPORT_STATUS')
    ),
    prior_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    prior_reference TEXT,
    new_reference TEXT,
    evidence_json JSONB NOT NULL CHECK (
        jsonb_typeof(evidence_json)='object' AND evidence_json <> '{}'::jsonb
    ),
    recorded_by TEXT NOT NULL CHECK (btrim(recorded_by) <> ''),
    transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS po_reconciliation_events (
    po_reconciliation_event_id BIGSERIAL PRIMARY KEY,
    po_line_id BIGINT NOT NULL REFERENCES purchase_order_lines(po_line_id) ON DELETE RESTRICT,
    prior_received_units NUMERIC(14,4) NOT NULL,
    new_received_units NUMERIC(14,4) NOT NULL,
    prior_cancelled_units NUMERIC(14,4) NOT NULL,
    new_cancelled_units NUMERIC(14,4) NOT NULL,
    reconciliation_status TEXT NOT NULL
        CHECK (reconciliation_status IN ('OPEN','RECONCILED','AMBIGUOUS')),
    evidence_json JSONB NOT NULL CHECK (
        jsonb_typeof(evidence_json)='object' AND evidence_json <> '{}'::jsonb
    ),
    recorded_by TEXT NOT NULL CHECK (btrim(recorded_by) <> ''),
    transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION monday_po_reconciliation_rollup(target_po_id UUID)
RETURNS TABLE(
    derived_receipt_status TEXT,
    derived_reconciled_at TIMESTAMPTZ,
    derived_evidence JSONB
) LANGUAGE plpgsql AS $$
DECLARE
    line_count BIGINT;
    ambiguous_count BIGINT;
    open_count BIGINT;
    received_total NUMERIC(14,4);
    cancelled_total NUMERIC(14,4);
    event_actor TEXT;
    event_evidence JSONB;
    event_recorded_at TIMESTAMPTZ;
BEGIN
    SELECT e.recorded_by,e.evidence_json,e.recorded_at
      INTO event_actor,event_evidence,event_recorded_at
      FROM po_reconciliation_events e
      JOIN purchase_order_lines l ON l.po_line_id=e.po_line_id
     WHERE l.po_id=target_po_id AND e.transaction_id=txid_current()
     ORDER BY e.po_reconciliation_event_id DESC
     LIMIT 1;
    SELECT count(*),
           count(*) FILTER (WHERE reconciliation_status='AMBIGUOUS'),
           count(*) FILTER (WHERE open_units > 0),
           COALESCE(sum(received_units),0),
           COALESCE(sum(cancelled_units),0)
      INTO line_count,ambiguous_count,open_count,received_total,cancelled_total
      FROM purchase_order_lines WHERE po_id=target_po_id;
    IF event_actor IS NULL OR event_evidence IS NULL OR line_count=0 THEN
        RETURN QUERY SELECT NULL::text,NULL::timestamptz,NULL::jsonb;
        RETURN;
    END IF;
    IF ambiguous_count > 0 THEN
        derived_receipt_status := 'AMBIGUOUS';
        derived_reconciled_at := NULL;
    ELSIF open_count > 0 THEN
        derived_receipt_status := CASE
            WHEN received_total > 0 THEN 'PARTIALLY_RECEIVED'
            WHEN cancelled_total > 0 THEN 'PARTIALLY_CANCELLED'
            ELSE 'OPEN'
        END;
        derived_reconciled_at := NULL;
    ELSIF received_total > 0 AND cancelled_total > 0 THEN
        derived_receipt_status := 'PARTIALLY_RECEIVED_CANCELLED';
        derived_reconciled_at := event_recorded_at;
    ELSIF received_total > 0 THEN
        derived_receipt_status := 'RECEIVED';
        derived_reconciled_at := event_recorded_at;
    ELSE
        derived_receipt_status := 'CANCELLED';
        derived_reconciled_at := event_recorded_at;
    END IF;
    derived_evidence := jsonb_build_object(
        'source','LINE_RECONCILIATION_ROLLUP',
        'line_count',line_count,
        'open_lines',open_count,
        'ambiguous_lines',ambiguous_count,
        'received_units',received_total::text,
        'cancelled_units',cancelled_total::text,
        'recorded_by',event_actor,
        'latest_line_evidence',event_evidence
    );
    RETURN NEXT;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='ck_purchase_orders_mvp_fields'
          AND conrelid='purchase_orders'::regclass
    ) THEN
        ALTER TABLE purchase_orders ADD CONSTRAINT ck_purchase_orders_mvp_fields CHECK (
            po_revision >= 1
            AND (input_fingerprint IS NULL OR input_fingerprint ~ '^[0-9a-f]{64}$')
            AND ((reviewed_at IS NULL AND reviewed_by IS NULL)
                 OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL
                     AND btrim(reviewed_by) <> ''))
            AND receipt_status IN (
                'UNKNOWN','OPEN','PARTIALLY_RECEIVED','PARTIALLY_CANCELLED',
                'PARTIALLY_RECEIVED_CANCELLED',
                'RECEIVED','CANCELLED','AMBIGUOUS'
            )
            AND (po_status <> 'FINAL' OR (
                finalized_at IS NOT NULL
                AND reviewed_at IS NOT NULL
                AND reviewed_by IS NOT NULL
                AND btrim(reviewed_by) <> ''
            ))
            AND shopify_import_status IN (
                'NOT_IMPORTED','EXPORTED','IMPORTED','FAILED'
            )
            AND jsonb_typeof(reconciliation_evidence)='object'
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='ck_purchase_order_line_quantities'
          AND conrelid='purchase_order_lines'::regclass
    ) THEN
        ALTER TABLE purchase_order_lines ADD CONSTRAINT ck_purchase_order_line_quantities CHECK (
            cases >= 0 AND loose_units >= 0 AND ordered_units >= 0
            AND received_units >= 0 AND cancelled_units >= 0
            AND received_units + cancelled_units <= ordered_units
            AND ordered_units > 0
            AND cases + loose_units > 0
            AND (input_fingerprint IS NULL OR input_fingerprint ~ '^[0-9a-f]{64}$')
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='ck_purchase_order_line_status'
          AND conrelid='purchase_order_lines'::regclass
    ) THEN
        ALTER TABLE purchase_order_lines ADD CONSTRAINT ck_purchase_order_line_status CHECK (
            line_status IN (
                'DRAFT','ORDERED','PARTIALLY_RECEIVED','PARTIALLY_CANCELLED',
                'PARTIALLY_RECEIVED_CANCELLED',
                'RECEIVED','CANCELLED','BACKORDER_REVIEW'
            )
            AND reconciliation_status IN ('UNKNOWN','OPEN','RECONCILED','AMBIGUOUS')
            AND (
                (line_status='DRAFT'
                 AND reconciliation_status='UNKNOWN'
                 AND received_units=0 AND cancelled_units=0)
                OR (line_status='ORDERED'
                    AND reconciliation_status IN ('UNKNOWN','OPEN')
                    AND received_units=0 AND cancelled_units=0
                    AND ordered_units-received_units-cancelled_units > 0)
                OR (line_status='PARTIALLY_RECEIVED'
                    AND reconciliation_status='OPEN'
                    AND received_units > 0
                    AND ordered_units-received_units-cancelled_units > 0)
                OR (line_status='PARTIALLY_CANCELLED'
                    AND reconciliation_status='OPEN'
                    AND received_units=0 AND cancelled_units > 0
                    AND ordered_units-received_units-cancelled_units > 0)
                OR (line_status='RECEIVED'
                    AND reconciliation_status='RECONCILED'
                    AND received_units > 0
                    AND cancelled_units=0
                    AND ordered_units-received_units-cancelled_units = 0)
                OR (line_status='PARTIALLY_RECEIVED_CANCELLED'
                    AND reconciliation_status='RECONCILED'
                    AND received_units > 0 AND cancelled_units > 0
                    AND ordered_units-received_units-cancelled_units = 0)
                OR (line_status='CANCELLED'
                    AND reconciliation_status='RECONCILED'
                    AND received_units=0
                    AND cancelled_units=ordered_units)
                OR (line_status='BACKORDER_REVIEW'
                    AND reconciliation_status='AMBIGUOUS'
                    AND ordered_units-received_units-cancelled_units > 0)
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='ck_purchase_order_line_reconciliation_evidence'
          AND conrelid='purchase_order_lines'::regclass
    ) THEN
        ALTER TABLE purchase_order_lines
            ADD CONSTRAINT ck_purchase_order_line_reconciliation_evidence CHECK (
                jsonb_typeof(reconciliation_evidence)='object'
                AND (
                    (last_reconciled_at IS NULL
                     AND last_reconciled_by IS NULL
                     AND reconciliation_evidence='{}'::jsonb)
                    OR
                    (last_reconciled_at IS NOT NULL
                     AND last_reconciled_by IS NOT NULL
                     AND btrim(last_reconciled_by) <> ''
                     AND reconciliation_evidence <> '{}'::jsonb)
                )
            );
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_order_line_offer
    ON purchase_order_lines(po_id,variant_id,COALESCE(offer_id,-1));
CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_order_line_recommendation
    ON purchase_order_lines(recommendation_id)
    WHERE recommendation_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_purchase_order_line_review_decision
    ON purchase_order_lines(review_decision_id);
CREATE INDEX IF NOT EXISTS idx_purchase_order_lines_open
    ON purchase_order_lines(variant_id,line_status,reconciliation_status)
    WHERE open_units > 0;

CREATE OR REPLACE FUNCTION validate_monday_po_line()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_vendor UUID;
    parent_run UUID;
    parent_status TEXT;
    offer_vendor UUID;
    offer_variant TEXT;
    offer_active BOOLEAN;
    offer_supplier_sku TEXT;
    offer_units_per_case NUMERIC(12,4);
    recommendation_run UUID;
    recommendation_variant TEXT;
    recommendation_vendor UUID;
    recommendation_offer BIGINT;
    recommendation_cases NUMERIC(14,4);
    recommendation_loose_units NUMERIC(14,4);
    decision_run UUID;
    decision_recommendation BIGINT;
    decision_scope TEXT;
    decision_action TEXT;
    decision_type TEXT;
    decision_actor TEXT;
BEGIN
    SELECT vendor_id,run_id,po_status INTO parent_vendor,parent_run,parent_status
      FROM purchase_orders WHERE po_id=NEW.po_id;
    IF parent_vendor IS NULL THEN
        RAISE EXCEPTION 'PO line parent is missing';
    END IF;
    IF NOT is_procurement_eligible_variant(NEW.variant_id)
       AND (TG_OP='INSERT'
            OR NEW.variant_id IS DISTINCT FROM OLD.variant_id
            OR NEW.po_id IS DISTINCT FROM OLD.po_id
            OR (NEW.line_status IS DISTINCT FROM OLD.line_status
                AND parent_status <> 'FINAL')) THEN
        RAISE EXCEPTION 'PO line requires CURRENT, active, LIVE Variant ID %', NEW.variant_id;
    END IF;
    IF NEW.offer_id IS NULL THEN
        RAISE EXCEPTION 'PO line requires a verified supplier offer';
    END IF;
    SELECT vendor_id,variant_id,active,supplier_sku,shopify_units_per_case
      INTO offer_vendor,offer_variant,offer_active,offer_supplier_sku,
           offer_units_per_case
      FROM supplier_offers WHERE offer_id=NEW.offer_id;
    IF offer_vendor IS NULL
       OR offer_vendor <> parent_vendor OR offer_variant <> NEW.variant_id
       OR offer_supplier_sku IS NULL OR btrim(offer_supplier_sku)=''
       OR NEW.supplier_sku IS DISTINCT FROM offer_supplier_sku
       OR offer_units_per_case IS NULL OR offer_units_per_case <= 0
       OR NEW.cases <> trunc(NEW.cases)
       OR NEW.loose_units <> trunc(NEW.loose_units)
       OR NEW.ordered_units <> trunc(NEW.ordered_units)
       OR NEW.ordered_units IS DISTINCT FROM
          (NEW.cases * offer_units_per_case + NEW.loose_units)
       OR (
           offer_active IS DISTINCT FROM TRUE
           AND (TG_OP='INSERT'
                OR NEW.offer_id IS DISTINCT FROM OLD.offer_id
                OR NEW.po_id IS DISTINCT FROM OLD.po_id
                OR NEW.variant_id IS DISTINCT FROM OLD.variant_id
                OR (NEW.line_status IS DISTINCT FROM OLD.line_status
                    AND parent_status <> 'FINAL'))
       ) THEN
        RAISE EXCEPTION 'PO line offer/vendor/variant relationship is invalid';
    END IF;
    SELECT run_id,variant_id,vendor_id,offer_id,
           recommended_cases,recommended_loose_units
      INTO recommendation_run,recommendation_variant,recommendation_vendor,
           recommendation_offer,recommendation_cases,recommendation_loose_units
      FROM procurement_recommendations WHERE recommendation_id=NEW.recommendation_id;
    IF recommendation_run IS NULL
       OR recommendation_run <> parent_run
       OR recommendation_variant <> NEW.variant_id
       OR recommendation_vendor <> parent_vendor
       OR recommendation_offer IS DISTINCT FROM NEW.offer_id
       OR recommendation_cases IS DISTINCT FROM NEW.cases
       OR recommendation_loose_units IS DISTINCT FROM NEW.loose_units THEN
        RAISE EXCEPTION 'PO line recommendation relationship is invalid';
    END IF;
    SELECT d.run_id,d.recommendation_id,d.scope,d.action,d.decision_type,d.decided_by
      INTO decision_run,decision_recommendation,decision_scope,decision_action,
           decision_type,decision_actor
      FROM review_decisions d WHERE d.decision_id=NEW.review_decision_id;
    IF decision_run IS NULL
       OR decision_run <> parent_run
       OR decision_recommendation <> NEW.recommendation_id
       OR decision_scope <> 'RUN_ONLY'
       OR decision_action <> 'ACCEPT'
       OR decision_type <> 'PROCUREMENT_RECOMMENDATION'
       OR decision_actor IS NULL OR btrim(decision_actor)='' THEN
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

DROP TRIGGER IF EXISTS trg_validate_monday_po_line ON purchase_order_lines;
CREATE TRIGGER trg_validate_monday_po_line
BEFORE INSERT OR UPDATE OF po_id,variant_id,offer_id,recommendation_id,
    review_decision_id,supplier_sku,cases,loose_units,ordered_units,line_status
ON purchase_order_lines
FOR EACH ROW EXECUTE FUNCTION validate_monday_po_line();

CREATE OR REPLACE FUNCTION protect_final_po_line()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_parent_status TEXT;
    new_parent_status TEXT;
    old_parent_finalized_at TIMESTAMPTZ;
    old_parent_imported_at TIMESTAMPTZ;
BEGIN
    SELECT p.po_status,p.finalized_at,
           (
               SELECT max(e.recorded_at)
                 FROM po_operational_events e
                WHERE e.po_id=p.po_id
                  AND e.event_type='SHOPIFY_IMPORT_STATUS'
                  AND e.new_status='IMPORTED'
           )
      INTO old_parent_status,old_parent_finalized_at,old_parent_imported_at
      FROM purchase_orders p WHERE p.po_id=OLD.po_id;
    IF TG_OP <> 'DELETE' THEN
        SELECT po_status INTO new_parent_status
          FROM purchase_orders WHERE po_id=NEW.po_id;
    END IF;
    IF old_parent_status IN ('REVIEW','FINAL','CANCELLED') THEN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'REVIEW, FINAL, or CANCELLED Procurement PO lines are immutable';
        END IF;
        IF ROW(
            NEW.po_line_id,NEW.po_id,NEW.variant_id,NEW.offer_id,NEW.recommendation_id,
            NEW.review_decision_id,NEW.supplier_sku,NEW.cases,NEW.loose_units,
            NEW.ordered_units,NEW.unit_cost,NEW.line_total,NEW.reason_code,
            NEW.comment,NEW.input_fingerprint,NEW.expected_receipt_at
        ) IS DISTINCT FROM ROW(
            OLD.po_line_id,OLD.po_id,OLD.variant_id,OLD.offer_id,OLD.recommendation_id,
            OLD.review_decision_id,OLD.supplier_sku,OLD.cases,OLD.loose_units,
            OLD.ordered_units,OLD.unit_cost,OLD.line_total,OLD.reason_code,
            OLD.comment,OLD.input_fingerprint,OLD.expected_receipt_at
        ) THEN
            RAISE EXCEPTION 'REVIEW, FINAL, or CANCELLED Procurement PO commercial line facts are immutable';
        END IF;
        IF old_parent_status IN ('REVIEW','CANCELLED') AND ROW(
               NEW.received_units,NEW.cancelled_units,NEW.line_status,
               NEW.reconciliation_status,NEW.reconciliation_evidence,
               NEW.last_reconciled_at,NEW.last_reconciled_by
           ) IS DISTINCT FROM ROW(
               OLD.received_units,OLD.cancelled_units,OLD.line_status,
               OLD.reconciliation_status,OLD.reconciliation_evidence,
               OLD.last_reconciled_at,OLD.last_reconciled_by
           ) THEN
            RAISE EXCEPTION 'REVIEW or CANCELLED Procurement PO receipt facts are immutable';
        END IF;
        IF old_parent_status='FINAL' AND ROW(
               NEW.received_units,NEW.cancelled_units,NEW.line_status,
               NEW.reconciliation_status,NEW.reconciliation_evidence,
               NEW.last_reconciled_at,NEW.last_reconciled_by
           ) IS DISTINCT FROM ROW(
               OLD.received_units,OLD.cancelled_units,OLD.line_status,
               OLD.reconciliation_status,OLD.reconciliation_evidence,
               OLD.last_reconciled_at,OLD.last_reconciled_by
           ) THEN
            IF new_parent_status <> 'FINAL' THEN
                RAISE EXCEPTION 'FINAL Procurement PO line cannot move to another parent';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM purchase_orders p
                WHERE p.po_id=OLD.po_id AND p.shopify_import_status='IMPORTED'
            ) THEN
                RAISE EXCEPTION 'FINAL Procurement PO receipt change requires an imported PO';
            END IF;
            IF NEW.last_reconciled_at IS NULL
               OR NEW.last_reconciled_at IS NOT DISTINCT FROM OLD.last_reconciled_at
               OR NEW.last_reconciled_by IS NULL
               OR btrim(NEW.last_reconciled_by)=''
               OR jsonb_typeof(NEW.reconciliation_evidence) <> 'object'
               OR NEW.reconciliation_evidence='{}'::jsonb
               OR jsonb_typeof(NEW.reconciliation_evidence->'source') <> 'string'
               OR btrim(COALESCE(NEW.reconciliation_evidence->>'source',''))=''
               OR (
                   NEW.reconciliation_status='OPEN'
                   AND (
                       jsonb_typeof(NEW.reconciliation_evidence->'reference') <> 'string'
                       OR btrim(COALESCE(NEW.reconciliation_evidence->>'reference',''))=''
                   )
               )
               OR old_parent_finalized_at IS NULL
               OR old_parent_imported_at IS NULL
               OR NEW.last_reconciled_at < old_parent_finalized_at
               OR NEW.last_reconciled_at < old_parent_imported_at
               OR NEW.last_reconciled_at < clock_timestamp() - interval '5 minutes'
               OR NEW.last_reconciled_at > clock_timestamp() + interval '5 seconds' THEN
                RAISE EXCEPTION 'FINAL Procurement PO receipt change requires attributed direct evidence';
            END IF;
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_final_po_line ON purchase_order_lines;
CREATE TRIGGER trg_protect_final_po_line
BEFORE UPDATE OR DELETE ON purchase_order_lines
FOR EACH ROW EXECUTE FUNCTION protect_final_po_line();

CREATE OR REPLACE FUNCTION validate_monday_purchase_order()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_run_type TEXT;
    old_parent_run_type TEXT;
    parent_run_status TEXT;
    parent_workflow_stage TEXT;
    parent_input_fingerprint TEXT;
    line_count BIGINT;
    invalid_line_count BIGINT;
    line_merchandise_total NUMERIC(14,2);
    expected_receipt_status TEXT;
    expected_reconciled_at TIMESTAMPTZ;
    expected_reconciliation_evidence JSONB;
BEGIN
    SELECT run_type,status,workflow_stage,input_fingerprint
      INTO parent_run_type,parent_run_status,parent_workflow_stage,parent_input_fingerprint
      FROM runs WHERE run_id=NEW.run_id;
    IF TG_OP='UPDATE' THEN
        SELECT run_type INTO old_parent_run_type FROM runs WHERE run_id=OLD.run_id;
    END IF;
    IF parent_run_type='MONDAY_PROCUREMENT'
       OR old_parent_run_type='MONDAY_PROCUREMENT' THEN
        IF TG_OP='UPDATE'
           AND old_parent_run_type='MONDAY_PROCUREMENT'
           AND ROW(NEW.run_id,NEW.vendor_id) IS DISTINCT FROM ROW(OLD.run_id,OLD.vendor_id) THEN
            RAISE EXCEPTION 'Monday Procurement PO identity is immutable';
        END IF;
        IF NEW.input_fingerprint IS DISTINCT FROM parent_input_fingerprint THEN
            RAISE EXCEPTION 'Monday Procurement PO fingerprint must match its frozen run';
        END IF;
        IF (TG_OP='INSERT'
            OR NEW.po_status IN ('DRAFT','REVIEW')
            OR (TG_OP='UPDATE' AND OLD.po_status <> 'FINAL' AND NEW.po_status='FINAL'))
           AND (
               parent_run_status <> 'RUNNING'
               OR parent_workflow_stage NOT IN ('REVIEWED','DRAFTS_BUILT','PACKET_BUILT')
           ) THEN
            RAISE EXCEPTION 'Monday Procurement PO requires an active reviewed run';
        END IF;
        IF TG_OP='INSERT' AND NEW.po_status <> 'DRAFT' THEN
            RAISE EXCEPTION 'Monday Procurement PO must begin in DRAFT';
        END IF;
        IF TG_OP='UPDATE' THEN
            IF OLD.po_status='CANCELLED'
               AND ROW(
                   NEW.po_id,NEW.run_id,NEW.vendor_id,NEW.po_status,
                   NEW.merchandise_total,NEW.delivery_fee,NEW.po_total,
                   NEW.below_vendor_minimum,NEW.created_at,NEW.finalized_at,
                   NEW.notes,NEW.shopify_import_status,NEW.shopify_po_reference,
                   NEW.expected_receipt_at,NEW.receipt_status,NEW.reconciled_at,
                   NEW.emergency_packet_path,NEW.input_fingerprint,
                   NEW.po_revision,NEW.reviewed_by,NEW.reviewed_at,
                   NEW.reconciliation_evidence
               ) IS DISTINCT FROM ROW(
                   OLD.po_id,OLD.run_id,OLD.vendor_id,OLD.po_status,
                   OLD.merchandise_total,OLD.delivery_fee,OLD.po_total,
                   OLD.below_vendor_minimum,OLD.created_at,OLD.finalized_at,
                   OLD.notes,OLD.shopify_import_status,OLD.shopify_po_reference,
                   OLD.expected_receipt_at,OLD.receipt_status,OLD.reconciled_at,
                   OLD.emergency_packet_path,OLD.input_fingerprint,
                   OLD.po_revision,OLD.reviewed_by,OLD.reviewed_at,
                   OLD.reconciliation_evidence
               ) THEN
                RAISE EXCEPTION 'CANCELLED Procurement PO facts are immutable';
            END IF;
            IF OLD.po_status='CANCELLED' AND NEW.po_status <> 'CANCELLED' THEN
                RAISE EXCEPTION 'CANCELLED Procurement PO cannot be reopened';
            END IF;
            IF OLD.po_status='FINAL' AND NEW.po_status <> 'FINAL' THEN
                RAISE EXCEPTION 'FINAL Procurement PO cannot change lifecycle state';
            END IF;
            IF OLD.po_status='DRAFT' AND NEW.po_status='FINAL' THEN
                RAISE EXCEPTION 'Procurement PO must enter REVIEW before FINAL';
            END IF;
            IF OLD.po_status='REVIEW' AND NEW.po_status IN ('REVIEW','FINAL')
               AND ROW(
                   NEW.run_id,NEW.vendor_id,NEW.input_fingerprint,NEW.po_revision,
                   NEW.reviewed_by,NEW.reviewed_at,NEW.merchandise_total,
                   NEW.delivery_fee,NEW.po_total,NEW.below_vendor_minimum,
                   NEW.expected_receipt_at,NEW.created_at,NEW.notes
               ) IS DISTINCT FROM ROW(
                   OLD.run_id,OLD.vendor_id,OLD.input_fingerprint,OLD.po_revision,
                   OLD.reviewed_by,OLD.reviewed_at,OLD.merchandise_total,
                   OLD.delivery_fee,OLD.po_total,OLD.below_vendor_minimum,
                   OLD.expected_receipt_at,OLD.created_at,OLD.notes
               ) THEN
                RAISE EXCEPTION 'REVIEW Procurement PO facts require renewed human review';
            END IF;
            IF OLD.po_status='REVIEW' AND NEW.po_status='DRAFT'
               AND (
                   NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
                   OR NEW.finalized_at IS NOT NULL
                   OR NEW.po_revision <> OLD.po_revision + 1
               ) THEN
                RAISE EXCEPTION 'returning a reviewed PO to DRAFT requires cleared review evidence and a new revision';
            END IF;
            IF OLD.po_status='FINAL'
               AND ROW(
                   NEW.run_id,NEW.vendor_id,NEW.input_fingerprint,NEW.po_revision,
                   NEW.reviewed_by,NEW.reviewed_at,NEW.finalized_at,
                   NEW.merchandise_total,NEW.delivery_fee,NEW.po_total,
                   NEW.below_vendor_minimum,NEW.expected_receipt_at,
                   NEW.emergency_packet_path,NEW.created_at,NEW.notes
               ) IS DISTINCT FROM ROW(
                   OLD.run_id,OLD.vendor_id,OLD.input_fingerprint,OLD.po_revision,
                   OLD.reviewed_by,OLD.reviewed_at,OLD.finalized_at,
                   OLD.merchandise_total,OLD.delivery_fee,OLD.po_total,
                   OLD.below_vendor_minimum,OLD.expected_receipt_at,
                   OLD.emergency_packet_path,OLD.created_at,OLD.notes
               ) THEN
                RAISE EXCEPTION 'FINAL Procurement PO identity and economics are immutable';
            END IF;
            IF OLD.po_status='FINAL'
               AND ROW(
                   NEW.shopify_import_status,NEW.shopify_po_reference
               ) IS DISTINCT FROM ROW(
                   OLD.shopify_import_status,OLD.shopify_po_reference
               )
               AND NOT EXISTS (
                   SELECT 1 FROM po_operational_events e
                   WHERE e.po_id=OLD.po_id
                     AND e.event_type='SHOPIFY_IMPORT_STATUS'
                     AND e.prior_status=OLD.shopify_import_status
                     AND e.new_status=NEW.shopify_import_status
                     AND e.prior_reference IS NOT DISTINCT FROM OLD.shopify_po_reference
                     AND e.new_reference IS NOT DISTINCT FROM NEW.shopify_po_reference
                     AND e.transaction_id=txid_current()
               ) THEN
                RAISE EXCEPTION 'FINAL Procurement PO import fields require an audited ledger event';
            END IF;
            IF OLD.po_status='FINAL'
               AND ROW(NEW.shopify_import_status,NEW.shopify_po_reference)
                   IS DISTINCT FROM
                   ROW(OLD.shopify_import_status,OLD.shopify_po_reference)
               AND NOT (
                   (OLD.shopify_import_status='NOT_IMPORTED'
                    AND NEW.shopify_import_status IN ('EXPORTED','IMPORTED','FAILED'))
                   OR (OLD.shopify_import_status='EXPORTED'
                       AND NEW.shopify_import_status IN ('IMPORTED','FAILED'))
                   OR (OLD.shopify_import_status='FAILED'
                       AND NEW.shopify_import_status IN ('EXPORTED','IMPORTED','FAILED'))
               ) THEN
                RAISE EXCEPTION 'invalid post-final import-status transition';
            END IF;
            IF NEW.shopify_import_status IN ('EXPORTED','IMPORTED')
               AND (NEW.shopify_po_reference IS NULL
                    OR btrim(NEW.shopify_po_reference)='') THEN
                RAISE EXCEPTION 'exported/imported PO requires an external reference';
            END IF;
            IF OLD.po_status='FINAL'
               AND ROW(NEW.receipt_status,NEW.reconciled_at,NEW.reconciliation_evidence)
                   IS DISTINCT FROM
                   ROW(OLD.receipt_status,OLD.reconciled_at,OLD.reconciliation_evidence) THEN
                SELECT derived_receipt_status,derived_reconciled_at,derived_evidence
                  INTO expected_receipt_status,expected_reconciled_at,
                       expected_reconciliation_evidence
                  FROM monday_po_reconciliation_rollup(OLD.po_id);
                IF expected_reconciliation_evidence IS NULL
                   OR ROW(NEW.receipt_status,NEW.reconciled_at,NEW.reconciliation_evidence)
                      IS DISTINCT FROM ROW(
                          expected_receipt_status,expected_reconciled_at,
                          expected_reconciliation_evidence
                      ) THEN
                    RAISE EXCEPTION 'FINAL Procurement PO receipt fields require the exact audited line rollup';
                END IF;
            END IF;
        END IF;
        IF NEW.po_status IN ('REVIEW','FINAL')
           AND (NEW.reviewed_at IS NULL OR NEW.reviewed_by IS NULL OR btrim(NEW.reviewed_by)='') THEN
            RAISE EXCEPTION 'reviewed Procurement PO requires human review evidence';
        END IF;
        IF NEW.po_status='FINAL'
           AND (TG_OP='INSERT' OR OLD.po_status <> 'FINAL') THEN
            IF NEW.finalized_at IS NULL THEN
                RAISE EXCEPTION 'FINAL Procurement PO requires finalized_at';
            END IF;
            IF NEW.shopify_import_status <> 'NOT_IMPORTED'
               OR NEW.shopify_po_reference IS NOT NULL
               OR NEW.receipt_status <> 'UNKNOWN'
               OR NEW.reconciled_at IS NOT NULL
               OR NEW.reconciliation_evidence <> '{}'::jsonb THEN
                RAISE EXCEPTION 'FINAL Procurement PO must record import status after finalization';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM po_operational_events e
                WHERE e.po_id=NEW.po_id
                  AND e.event_type='FINALIZATION_AUTHORITY'
                  AND e.prior_status=OLD.po_status
                  AND e.new_status='FINAL'
                  AND e.transaction_id=txid_current()
                  AND e.evidence_json->>'source'='CANONICAL_PO_READINESS'
                  AND e.evidence_json->>'input_fingerprint'=NEW.input_fingerprint
                  AND e.evidence_json->>'po_revision'=NEW.po_revision::text
            ) THEN
                RAISE EXCEPTION 'FINAL Procurement PO requires canonical readiness authorization';
            END IF;
            SELECT count(*),count(*) FILTER (
                       WHERE l.line_status <> 'ORDERED'
                          OR l.reconciliation_status <> 'UNKNOWN'
                          OR l.open_units <= 0
                          OR l.received_units <> 0 OR l.cancelled_units <> 0
                          OR l.reconciliation_evidence <> '{}'::jsonb
                          OR l.last_reconciled_at IS NOT NULL
                          OR l.last_reconciled_by IS NOT NULL
                          OR o.active IS DISTINCT FROM TRUE
                          OR v.active IS DISTINCT FROM TRUE
                          OR NOT is_procurement_eligible_variant(l.variant_id)
                          OR o.confidence IS DISTINCT FROM 'VERIFIED'
                          OR o.supplier_sku IS NULL OR btrim(o.supplier_sku)=''
                          OR l.supplier_sku IS NULL OR btrim(l.supplier_sku)=''
                          OR l.supplier_sku IS DISTINCT FROM o.supplier_sku
                          OR o.shopify_units_per_case IS NULL
                          OR o.shopify_units_per_case <= 0
                          OR l.cases <> trunc(l.cases)
                          OR l.loose_units <> trunc(l.loose_units)
                          OR l.ordered_units <> trunc(l.ordered_units)
                          OR l.ordered_units IS DISTINCT FROM
                             (l.cases * o.shopify_units_per_case + l.loose_units)
                          OR l.unit_cost IS NULL OR l.unit_cost <= 0
                          OR l.line_total IS NULL OR l.line_total <= 0
                          OR l.line_total IS DISTINCT FROM
                             round(l.ordered_units * l.unit_cost,2)
                          OR r.recommended_cases IS DISTINCT FROM l.cases
                          OR r.recommended_loose_units IS DISTINCT FROM l.loose_units
                   ),COALESCE(sum(l.line_total),0)::numeric(14,2)
              INTO line_count,invalid_line_count,line_merchandise_total
              FROM purchase_order_lines l
              JOIN supplier_offers o ON o.offer_id=l.offer_id
              JOIN vendors v ON v.vendor_id=o.vendor_id
              JOIN procurement_recommendations r
                ON r.recommendation_id=l.recommendation_id
              WHERE l.po_id=NEW.po_id;
            IF line_count=0 OR invalid_line_count > 0 THEN
                RAISE EXCEPTION 'FINAL Procurement PO requires active ORDERED approved lines';
            END IF;
            IF NEW.merchandise_total IS NULL
               OR NEW.delivery_fee IS NULL OR NEW.delivery_fee < 0
               OR NEW.po_total IS NULL
               OR NEW.merchandise_total IS DISTINCT FROM line_merchandise_total
               OR NEW.po_total IS DISTINCT FROM
                  round(NEW.merchandise_total + NEW.delivery_fee,2) THEN
                RAISE EXCEPTION 'FINAL Procurement PO totals do not match approved lines';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_monday_purchase_order ON purchase_orders;
CREATE TRIGGER trg_validate_monday_purchase_order
BEFORE INSERT OR UPDATE ON purchase_orders
FOR EACH ROW EXECUTE FUNCTION validate_monday_purchase_order();

CREATE OR REPLACE FUNCTION prevent_monday_purchase_order_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM runs
        WHERE run_id=OLD.run_id AND run_type='MONDAY_PROCUREMENT'
    ) THEN
        RAISE EXCEPTION 'Monday Procurement POs are durable and cannot be deleted';
    END IF;
    RETURN OLD;
END
$$;

DROP TRIGGER IF EXISTS trg_prevent_monday_purchase_order_delete ON purchase_orders;
CREATE TRIGGER trg_prevent_monday_purchase_order_delete
BEFORE DELETE ON purchase_orders
FOR EACH ROW EXECUTE FUNCTION prevent_monday_purchase_order_delete();

CREATE OR REPLACE FUNCTION prevent_referenced_offer_identity_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
           NEW.vendor_id,NEW.variant_id,NEW.supplier_sku,NEW.package_type,
           NEW.size_text,NEW.raw_pack,NEW.shopify_units_per_case,
           NEW.qualifying_units_per_case,NEW.confidence
       ) IS DISTINCT FROM ROW(
           OLD.vendor_id,OLD.variant_id,OLD.supplier_sku,OLD.package_type,
           OLD.size_text,OLD.raw_pack,OLD.shopify_units_per_case,
           OLD.qualifying_units_per_case,OLD.confidence
       )
       AND EXISTS (
           SELECT 1 FROM purchase_order_lines WHERE offer_id=OLD.offer_id
       ) THEN
        RAISE EXCEPTION 'referenced supplier offer identity is immutable';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_prevent_referenced_offer_identity_change ON supplier_offers;
CREATE TRIGGER trg_prevent_referenced_offer_identity_change
BEFORE UPDATE OF vendor_id,variant_id,supplier_sku,package_type,size_text,raw_pack,
    shopify_units_per_case,qualifying_units_per_case,confidence ON supplier_offers
FOR EACH ROW EXECUTE FUNCTION prevent_referenced_offer_identity_change();

CREATE OR REPLACE FUNCTION prevent_linked_run_identity_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM purchase_orders WHERE run_id=OLD.run_id) THEN
        RAISE EXCEPTION 'Procurement run identity is immutable after a PO exists';
    END IF;
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_prevent_linked_run_identity_change ON runs;
CREATE TRIGGER trg_prevent_linked_run_identity_change
BEFORE UPDATE OF run_type,business_date,idempotency_key,input_fingerprint,source_data_through OR DELETE
ON runs FOR EACH ROW EXECUTE FUNCTION prevent_linked_run_identity_change();

CREATE OR REPLACE FUNCTION prevent_linked_recommendation_identity_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM purchase_order_lines
        WHERE recommendation_id=OLD.recommendation_id
    ) THEN
        RAISE EXCEPTION 'PO-linked recommendation identity is immutable';
    END IF;
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_prevent_linked_recommendation_identity_change
    ON procurement_recommendations;
CREATE TRIGGER trg_prevent_linked_recommendation_identity_change
BEFORE UPDATE OR DELETE
ON procurement_recommendations
FOR EACH ROW EXECUTE FUNCTION prevent_linked_recommendation_identity_change();

CREATE OR REPLACE FUNCTION prevent_linked_review_decision_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM purchase_order_lines
        WHERE review_decision_id=OLD.decision_id
    ) THEN
        RAISE EXCEPTION 'PO-linked human review decision is immutable';
    END IF;
    IF TG_OP='DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_prevent_linked_review_decision_change
    ON review_decisions;
CREATE TRIGGER trg_prevent_linked_review_decision_change
BEFORE UPDATE OR DELETE
ON review_decisions
FOR EACH ROW EXECUTE FUNCTION prevent_linked_review_decision_change();

ALTER TABLE po_reconciliation_events
    ADD COLUMN IF NOT EXISTS transaction_id BIGINT NOT NULL DEFAULT txid_current();

CREATE OR REPLACE FUNCTION audit_final_po_line_reconciliation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_status TEXT;
    header_status TEXT;
    header_reconciled_at TIMESTAMPTZ;
    header_evidence JSONB;
BEGIN
    SELECT po_status INTO parent_status
      FROM purchase_orders WHERE po_id=OLD.po_id;
    IF parent_status <> 'FINAL' THEN
        RETURN NEW;
    END IF;
    INSERT INTO po_reconciliation_events(
        po_line_id,prior_received_units,new_received_units,
        prior_cancelled_units,new_cancelled_units,reconciliation_status,
        evidence_json,recorded_by,recorded_at
    ) VALUES (
        NEW.po_line_id,OLD.received_units,NEW.received_units,
        OLD.cancelled_units,NEW.cancelled_units,NEW.reconciliation_status,
        NEW.reconciliation_evidence,NEW.last_reconciled_by,NEW.last_reconciled_at
    );

    SELECT derived_receipt_status,derived_reconciled_at,derived_evidence
      INTO header_status,header_reconciled_at,header_evidence
      FROM monday_po_reconciliation_rollup(OLD.po_id);
    UPDATE purchase_orders
       SET receipt_status=header_status,
           reconciled_at=header_reconciled_at,
           reconciliation_evidence=header_evidence
     WHERE po_id=OLD.po_id;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_audit_final_po_line_reconciliation
    ON purchase_order_lines;
CREATE TRIGGER trg_audit_final_po_line_reconciliation
AFTER UPDATE OF received_units,cancelled_units,line_status,reconciliation_status,
    reconciliation_evidence,last_reconciled_at,last_reconciled_by
ON purchase_order_lines
FOR EACH ROW
WHEN (
    ROW(
        OLD.received_units,OLD.cancelled_units,OLD.line_status,
        OLD.reconciliation_status,OLD.reconciliation_evidence,
        OLD.last_reconciled_at,OLD.last_reconciled_by
    ) IS DISTINCT FROM ROW(
        NEW.received_units,NEW.cancelled_units,NEW.line_status,
        NEW.reconciliation_status,NEW.reconciliation_evidence,
        NEW.last_reconciled_at,NEW.last_reconciled_by
    )
)
EXECUTE FUNCTION audit_final_po_line_reconciliation();

CREATE OR REPLACE FUNCTION prevent_po_reconciliation_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Procurement PO reconciliation events are append-only';
END
$$;

DROP TRIGGER IF EXISTS trg_po_reconciliation_events_append_only
    ON po_reconciliation_events;
CREATE TRIGGER trg_po_reconciliation_events_append_only
BEFORE UPDATE OR DELETE ON po_reconciliation_events
FOR EACH ROW EXECUTE FUNCTION prevent_po_reconciliation_event_mutation();

CREATE OR REPLACE FUNCTION prevent_po_operational_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Procurement PO operational events are append-only';
END
$$;

DROP TRIGGER IF EXISTS trg_po_operational_events_append_only
    ON po_operational_events;
CREATE TRIGGER trg_po_operational_events_append_only
BEFORE UPDATE OR DELETE ON po_operational_events
FOR EACH ROW EXECUTE FUNCTION prevent_po_operational_event_mutation();

CREATE OR REPLACE VIEW v_open_procurement_incoming AS
SELECT l.variant_id,p.vendor_id,
       sum(l.open_units)::numeric(14,4) AS trusted_incoming_units,
       min(COALESCE(l.expected_receipt_at,p.expected_receipt_at)) AS next_expected_receipt_at,
       count(*) AS open_line_count
FROM purchase_order_lines l
JOIN purchase_orders p ON p.po_id=l.po_id
WHERE p.po_status='FINAL'
  AND p.shopify_import_status='IMPORTED'
  AND l.line_status IN ('ORDERED','PARTIALLY_RECEIVED','PARTIALLY_CANCELLED')
  AND l.reconciliation_status='OPEN'
  AND l.open_units > 0
  AND COALESCE(l.expected_receipt_at,p.expected_receipt_at) IS NOT NULL
  AND COALESCE(l.expected_receipt_at,p.expected_receipt_at) >= now()
  AND l.last_reconciled_at IS NOT NULL
  AND l.last_reconciled_at <= now()
  AND l.last_reconciled_by IS NOT NULL
  AND btrim(l.last_reconciled_by) <> ''
  AND jsonb_typeof(l.reconciliation_evidence)='object'
  AND l.reconciliation_evidence <> '{}'::jsonb
  AND btrim(COALESCE(l.reconciliation_evidence->>'source','')) <> ''
  AND btrim(COALESCE(l.reconciliation_evidence->>'reference','')) <> ''
  AND jsonb_typeof(l.reconciliation_evidence->'source')='string'
  AND jsonb_typeof(l.reconciliation_evidence->'reference')='string'
GROUP BY l.variant_id,p.vendor_id;

INSERT INTO meta(key, value)
VALUES ('monday_po_ledger_contract', 'v1')
ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
