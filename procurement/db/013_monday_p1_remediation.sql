-- Monday emergency P1 remediation: scoped exclusions, duplicate-run safety,
-- material-edit confirmation, and unresolved loose-fee fail-closed controls.
-- This migration adds no FINAL, release, Shopify, or production action path.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM runs r
          JOIN purchase_orders p ON p.run_id=r.run_id
         WHERE r.run_type='MONDAY_PROCUREMENT'
           AND r.status<>'RUNNING'
           AND p.po_status='DRAFT'
    ) THEN
        RAISE EXCEPTION
            'cannot install one-active-Monday-run control while a non-running Monday run owns a DRAFT';
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM runs r
         WHERE r.run_type='MONDAY_PROCUREMENT'
           AND r.procurement_output_mode='INTERNAL_DRAFT_ONLY'
           AND r.status='RUNNING'
           AND (
               jsonb_typeof(
                   r.procurement_input_manifest::jsonb->'material_edit_policy'
               ) IS DISTINCT FROM 'object'
               OR nullif(btrim(
                   r.procurement_input_manifest::jsonb
                       #>> '{material_edit_policy,policy_version}'
               ),'') IS NULL
               OR nullif(btrim(
                   r.procurement_input_manifest::jsonb
                       #>> '{material_edit_policy,owner_approval_status}'
               ),'') IS NULL
               OR nullif(btrim(
                   r.procurement_input_manifest::jsonb
                       #>> '{material_edit_policy,max_normal_baseline_multiplier}'
               ),'') IS NULL
               OR nullif(btrim(
                   r.procurement_input_manifest::jsonb
                       #>> '{material_edit_policy,max_normal_resulting_days_supply}'
               ),'') IS NULL
               OR NOT CASE
                   WHEN r.procurement_input_manifest::jsonb
                            #>> '{material_edit_policy,max_normal_baseline_multiplier}'
                        ~ '^(0|[1-9][0-9]*)([.][0-9]+)?$'
                   THEN (r.procurement_input_manifest::jsonb
                            #>> '{material_edit_policy,max_normal_baseline_multiplier}')::numeric>0
                   ELSE FALSE
               END
               OR NOT CASE
                   WHEN r.procurement_input_manifest::jsonb
                            #>> '{material_edit_policy,max_normal_resulting_days_supply}'
                        ~ '^(0|[1-9][0-9]*)([.][0-9]+)?$'
                   THEN (r.procurement_input_manifest::jsonb
                            #>> '{material_edit_policy,max_normal_resulting_days_supply}')::numeric>0
                   ELSE FALSE
               END
               OR EXISTS (
                   SELECT 1 FROM procurement_recommendations pr
                    WHERE pr.run_id=r.run_id
                      AND pr.metrics->'material_edit_policy' IS DISTINCT FROM
                          r.procurement_input_manifest::jsonb->'material_edit_policy'
               )
           )
    ) THEN
        RAISE EXCEPTION
            'cannot install material-edit controls while an active legacy Monday run lacks the frozen policy';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM runs r
          JOIN procurement_recommendations pr ON pr.run_id=r.run_id
          LEFT JOIN review_decisions d ON d.recommendation_id=pr.recommendation_id
         WHERE r.run_type='MONDAY_PROCUREMENT'
           AND r.procurement_output_mode='INTERNAL_DRAFT_ONLY'
           AND r.status='RUNNING'
           AND pr.frozen_loose_unit_fee>0
           AND (pr.recommended_loose_units>0 OR d.approved_loose_units>0)
    ) THEN
        RAISE EXCEPTION
            'cannot install loose-fee controls while active Monday evidence uses unconfirmed positive-fee loose units';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_active_monday_run_business_date
    ON runs(business_date)
    WHERE run_type='MONDAY_PROCUREMENT' AND status='RUNNING';

-- Upgrade old persisted global semantics without claiming missing vendor
-- evidence is valid.  The GLOBAL row is only a summary; each active vendor
-- still needs its own PASS row or readiness.py reports missing/failed evidence.
WITH vendor_summary AS (
    SELECT count(*)::integer AS active_vendors,
           count(*) FILTER (WHERE g.status='PASS')::integer AS passing_vendors,
           count(*) FILTER (WHERE g.status IS DISTINCT FROM 'PASS')::integer
               AS failing_vendors,
           COALESCE(
               array_agg(v.vendor_id::text ORDER BY v.vendor_id::text)
                   FILTER (WHERE g.status IS DISTINCT FROM 'PASS'),
               ARRAY[]::text[]
           ) AS failing_vendor_ids
      FROM vendors v
      LEFT JOIN readiness_gates g
        ON g.gate_name='VENDOR_RULES'
       AND g.scope_type='VENDOR'
       AND g.scope_id=v.vendor_id::text
     WHERE v.active
), refreshed AS (
    SELECT CASE
               WHEN active_vendors=0 THEN 'FAIL'
               WHEN failing_vendors>0 THEN 'WARN'
               ELSE 'PASS'
           END AS status,
           active_vendors=0 AS blocks_po,
           CASE
               WHEN active_vendors=0
                   THEN 'No active vendors exist for Monday procurement.'
               WHEN failing_vendors>0
                   THEN format(
                       'Vendor operating rules are incomplete for %s active vendor(s); affected vendors remain blocked by their VENDOR-scoped gates.',
                       failing_vendors
                   )
               ELSE 'All active vendor operating rules are complete and owner-confirmed.'
           END AS message,
           jsonb_build_object(
               'active_vendors',active_vendors,
               'passing_vendors',passing_vendors,
               'failing_vendors',failing_vendors,
               'failing_vendor_ids',to_jsonb(failing_vendor_ids),
               'refresh_source','MIGRATION_013_SCOPED_VENDOR_SUMMARY'
           ) AS evidence_json
      FROM vendor_summary
)
INSERT INTO readiness_gates(
    gate_name,scope_type,scope_id,status,severity,blocks_po,message,
    evidence_json,checked_at
)
SELECT 'VENDOR_RULES','GLOBAL','',status,'HIGH',blocks_po,message,
       evidence_json,transaction_timestamp()
  FROM refreshed
ON CONFLICT(gate_name,scope_type,scope_id) DO UPDATE SET
    status=EXCLUDED.status,
    severity=EXCLUDED.severity,
    blocks_po=EXCLUDED.blocks_po,
    message=EXCLUDED.message,
    evidence_json=EXCLUDED.evidence_json,
    checked_at=EXCLUDED.checked_at
WHERE (readiness_gates.status,readiness_gates.severity,
       readiness_gates.blocks_po,readiness_gates.message,
       readiness_gates.evidence_json)
      IS DISTINCT FROM
      (EXCLUDED.status,EXCLUDED.severity,EXCLUDED.blocks_po,
       EXCLUDED.message,EXCLUDED.evidence_json);

CREATE TABLE IF NOT EXISTS monday_run_blocker_exclusions (
    exclusion_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    exception_id BIGINT NOT NULL UNIQUE REFERENCES exceptions(exception_id) ON DELETE RESTRICT,
    variant_id TEXT NOT NULL REFERENCES variants(variant_id) ON DELETE RESTRICT,
    input_fingerprint TEXT NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    action TEXT NOT NULL CHECK (action='ACKNOWLEDGE_AND_EXCLUDE'),
    scope TEXT NOT NULL CHECK (scope='RUN_ONLY'),
    actor TEXT NOT NULL CHECK (btrim(actor)<>''),
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    evidence_json JSONB NOT NULL CHECK (
        jsonb_typeof(evidence_json)='object' AND evidence_json<>'{}'::jsonb
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE (run_id,variant_id)
);

CREATE TABLE IF NOT EXISTS monday_material_edit_confirmations (
    material_edit_confirmation_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
    recommendation_id BIGINT NOT NULL REFERENCES procurement_recommendations(recommendation_id) ON DELETE RESTRICT,
    input_fingerprint TEXT NOT NULL CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    review_preview_fingerprint TEXT NOT NULL CHECK (review_preview_fingerprint ~ '^[0-9a-f]{64}$'),
    approved_cases NUMERIC(14,4) NOT NULL CHECK (
        approved_cases>=0 AND approved_cases=trunc(approved_cases)
    ),
    approved_loose_units NUMERIC(14,4) NOT NULL CHECK (
        approved_loose_units>=0 AND approved_loose_units=trunc(approved_loose_units)
    ),
    approved_units NUMERIC(14,4) NOT NULL CHECK (
        approved_units>=0 AND approved_units=trunc(approved_units)
    ),
    action TEXT NOT NULL CHECK (action='CONFIRM_MATERIAL_EDIT'),
    confirmed_by TEXT NOT NULL CHECK (btrim(confirmed_by)<>''),
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    evidence_json JSONB NOT NULL CHECK (
        jsonb_typeof(evidence_json)='object' AND evidence_json<>'{}'::jsonb
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE (recommendation_id,review_preview_fingerprint)
);

ALTER TABLE review_decisions
    ADD COLUMN IF NOT EXISTS material_edit_confirmation_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='fk_review_material_edit_confirmation'
           AND conrelid='review_decisions'::regclass
    ) THEN
        ALTER TABLE review_decisions
            ADD CONSTRAINT fk_review_material_edit_confirmation
            FOREIGN KEY (material_edit_confirmation_id)
            REFERENCES monday_material_edit_confirmations(material_edit_confirmation_id)
            ON DELETE RESTRICT;
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION monday_effective_material_blocker_count(target_run UUID)
RETURNS BIGINT LANGUAGE sql STABLE AS $$
    SELECT count(*)
      FROM exceptions e
      JOIN runs r ON r.run_id=e.run_id
     WHERE e.run_id=target_run
       AND e.status='OPEN'
       AND e.severity IN ('HIGH','CRITICAL')
       AND NOT EXISTS (
           SELECT 1
             FROM monday_run_blocker_exclusions x
            WHERE x.exception_id=e.exception_id
              AND x.run_id=e.run_id
              AND x.variant_id=e.variant_id
              AND x.input_fingerprint=r.input_fingerprint
              AND x.action='ACKNOWLEDGE_AND_EXCLUDE'
              AND x.scope='RUN_ONLY'
       )
$$;

CREATE OR REPLACE FUNCTION protect_monday_blocker_exclusion()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_stage TEXT;
    parent_mode TEXT;
    parent_fingerprint TEXT;
    blocker_run UUID;
    blocker_variant TEXT;
    blocker_type TEXT;
    blocker_severity TEXT;
    blocker_status TEXT;
    blocker_message TEXT;
    manifest JSONB;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Monday blocker exclusions are append-only';
    END IF;
    SELECT workflow_stage,procurement_output_mode,input_fingerprint,
           procurement_input_manifest::jsonb
      INTO parent_stage,parent_mode,parent_fingerprint,manifest
      FROM runs WHERE run_id=NEW.run_id;
    SELECT run_id,variant_id,exception_type,severity,status,message
      INTO blocker_run,blocker_variant,blocker_type,blocker_severity,
           blocker_status,blocker_message
      FROM exceptions WHERE exception_id=NEW.exception_id;
    IF parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY'
       OR parent_stage IS DISTINCT FROM 'AWAITING_REVIEW'
       OR parent_fingerprint IS DISTINCT FROM NEW.input_fingerprint
       OR blocker_run IS DISTINCT FROM NEW.run_id
       OR blocker_variant IS DISTINCT FROM NEW.variant_id
       OR blocker_type IS DISTINCT FROM 'MONDAY_INPUT_BLOCKER'
       OR blocker_severity NOT IN ('HIGH','CRITICAL')
       OR blocker_status IS DISTINCT FROM 'OPEN'
       OR NOT EXISTS (
           SELECT 1 FROM jsonb_array_elements_text(manifest->'variant_ids') value
            WHERE value=NEW.variant_id
       )
       OR EXISTS (
           SELECT 1 FROM procurement_recommendations r
            WHERE r.run_id=NEW.run_id AND r.variant_id=NEW.variant_id
       )
       OR NEW.evidence_json->>'original_exception_type'
            IS DISTINCT FROM blocker_type
       OR NEW.evidence_json->>'original_message'
            IS DISTINCT FROM blocker_message THEN
        RAISE EXCEPTION 'RUN_ONLY exclusion does not match an immutable blocked run input';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_monday_blocker_exclusion
    ON monday_run_blocker_exclusions;
CREATE TRIGGER trg_protect_monday_blocker_exclusion
BEFORE INSERT OR UPDATE OR DELETE ON monday_run_blocker_exclusions
FOR EACH ROW EXECUTE FUNCTION protect_monday_blocker_exclusion();

CREATE OR REPLACE FUNCTION monday_edit_materiality(
    target_recommendation BIGINT,
    edited_units NUMERIC
) RETURNS TEXT LANGUAGE plpgsql STABLE AS $$
DECLARE
    baseline NUMERIC;
    available NUMERIC;
    incoming NUMERIC;
    velocity NUMERIC;
    multiplier_limit NUMERIC;
    days_limit NUMERIC;
BEGIN
    SELECT r.baseline_units,
           (r.metrics->>'available_units')::numeric,
           (r.metrics->>'trusted_incoming_units')::numeric,
           (r.metrics->>'forecast_daily_velocity')::numeric,
           (r.metrics #>> '{material_edit_policy,max_normal_baseline_multiplier}')::numeric,
           (r.metrics #>> '{material_edit_policy,max_normal_resulting_days_supply}')::numeric
      INTO baseline,available,incoming,velocity,multiplier_limit,days_limit
      FROM procurement_recommendations r
     WHERE r.recommendation_id=target_recommendation;
    IF baseline IS NULL OR baseline<0 OR edited_units IS NULL OR edited_units<0
       OR available IS NULL OR incoming IS NULL OR velocity IS NULL OR velocity<0
       OR multiplier_limit IS NULL OR multiplier_limit<=0
       OR days_limit IS NULL OR days_limit<=0 THEN
        RAISE EXCEPTION 'material-edit policy or frozen recommendation evidence is invalid';
    END IF;
    IF edited_units>0 AND (
       baseline=0
       OR edited_units>baseline*multiplier_limit
       OR velocity=0
       -- Python review evidence records resulting days of supply at two
       -- decimal places with ROUND_HALF_UP.  PostgreSQL round(numeric, 2)
       -- has the same rule for these non-negative inputs, so the database
       -- independently classifies the exact evidence the reviewer sees.
       OR (velocity>0 AND round(
              (available+incoming+edited_units)/velocity,2
          )>days_limit)
    ) THEN
        RETURN 'MATERIAL';
    END IF;
    RETURN 'NORMAL';
END
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM review_decisions d
          JOIN procurement_recommendations pr
            ON pr.recommendation_id=d.recommendation_id
          JOIN runs r ON r.run_id=pr.run_id
         WHERE r.run_type='MONDAY_PROCUREMENT'
           AND r.procurement_output_mode='INTERNAL_DRAFT_ONLY'
           AND r.status='RUNNING'
           AND d.action='EDIT_QUANTITY'
           AND monday_edit_materiality(pr.recommendation_id,d.approved_units)='MATERIAL'
           AND NOT EXISTS (
               SELECT 1
                 FROM monday_material_edit_confirmations c
                WHERE c.material_edit_confirmation_id=d.material_edit_confirmation_id
                  AND c.run_id=r.run_id
                  AND c.recommendation_id=pr.recommendation_id
                  AND c.input_fingerprint=r.input_fingerprint
                  AND c.review_preview_fingerprint=d.decision_fingerprint
                  AND c.approved_cases=d.approved_cases
                  AND c.approved_loose_units=d.approved_loose_units
                  AND c.approved_units=d.approved_units
                  AND d.evidence_json #>> '{review,material_edit_confirmation_id}'
                      =c.material_edit_confirmation_id::text
           )
    ) THEN
        RAISE EXCEPTION
            'cannot install material-edit controls while an active MATERIAL edit lacks exact confirmation';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION protect_monday_material_edit_confirmation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_run UUID;
    parent_fingerprint TEXT;
    parent_stage TEXT;
    parent_mode TEXT;
    parent_pack INTEGER;
    parent_fee NUMERIC;
    policy JSONB;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'material-edit confirmations are append-only';
    END IF;
    SELECT r.run_id,r.input_fingerprint,ru.workflow_stage,
           ru.procurement_output_mode,r.units_per_case,r.frozen_loose_unit_fee,
           r.metrics->'material_edit_policy'
      INTO parent_run,parent_fingerprint,parent_stage,parent_mode,parent_pack,
           parent_fee,policy
      FROM procurement_recommendations r
      JOIN runs ru ON ru.run_id=r.run_id
     WHERE r.recommendation_id=NEW.recommendation_id;
    IF parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY'
       OR parent_stage IS DISTINCT FROM 'AWAITING_REVIEW'
       OR parent_run IS DISTINCT FROM NEW.run_id
       OR parent_fingerprint IS DISTINCT FROM NEW.input_fingerprint
       OR NEW.approved_units IS DISTINCT FROM
            NEW.approved_cases*parent_pack+NEW.approved_loose_units
       OR NEW.approved_loose_units>=parent_pack
       OR (NEW.approved_loose_units>0 AND parent_fee>0)
       OR monday_edit_materiality(NEW.recommendation_id,NEW.approved_units)<>'MATERIAL'
       OR NEW.evidence_json->>'materiality_tier' IS DISTINCT FROM 'MATERIAL'
       OR NEW.evidence_json->'policy' IS DISTINCT FROM policy
       OR EXISTS (
           SELECT 1 FROM review_decisions d
            WHERE d.recommendation_id=NEW.recommendation_id
       ) THEN
        RAISE EXCEPTION 'material-edit confirmation does not match a pending material edit';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_monday_material_edit_confirmation
    ON monday_material_edit_confirmations;
CREATE TRIGGER trg_protect_monday_material_edit_confirmation
BEFORE INSERT OR UPDATE OR DELETE ON monday_material_edit_confirmations
FOR EACH ROW EXECUTE FUNCTION protect_monday_material_edit_confirmation();

CREATE OR REPLACE FUNCTION guard_monday_p1_recommendation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_mode TEXT;
BEGIN
    SELECT procurement_output_mode INTO parent_mode FROM runs WHERE run_id=NEW.run_id;
    IF parent_mode='INTERNAL_DRAFT_ONLY'
       AND NEW.recommended_loose_units>0
       AND NEW.frozen_loose_unit_fee>0 THEN
        RAISE EXCEPTION 'LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_monday_p1_recommendation
    ON procurement_recommendations;
CREATE TRIGGER trg_guard_monday_p1_recommendation
BEFORE INSERT OR UPDATE ON procurement_recommendations
FOR EACH ROW EXECUTE FUNCTION guard_monday_p1_recommendation();

CREATE OR REPLACE FUNCTION guard_monday_p1_review_decision()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    parent_mode TEXT;
    parent_run UUID;
    parent_fingerprint TEXT;
    parent_fee NUMERIC;
    required_tier TEXT;
    confirmation monday_material_edit_confirmations%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    IF TG_OP<>'INSERT' OR NEW.recommendation_id IS NULL THEN RETURN NEW; END IF;
    SELECT ru.procurement_output_mode,r.run_id,r.input_fingerprint,
           r.frozen_loose_unit_fee
      INTO parent_mode,parent_run,parent_fingerprint,parent_fee
      FROM procurement_recommendations r
      JOIN runs ru ON ru.run_id=r.run_id
     WHERE r.recommendation_id=NEW.recommendation_id;
    IF parent_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY' THEN RETURN NEW; END IF;
    IF NEW.approved_loose_units>0 AND parent_fee>0 THEN
        RAISE EXCEPTION 'LOOSE_UNIT_FEE_SEMANTICS_UNCONFIRMED';
    END IF;
    required_tier := CASE WHEN NEW.action='EDIT_QUANTITY'
        THEN monday_edit_materiality(NEW.recommendation_id,NEW.approved_units)
        ELSE 'NORMAL' END;
    IF required_tier='MATERIAL' THEN
        SELECT * INTO confirmation
          FROM monday_material_edit_confirmations
         WHERE material_edit_confirmation_id=NEW.material_edit_confirmation_id;
        IF confirmation.material_edit_confirmation_id IS NULL
           OR confirmation.run_id IS DISTINCT FROM parent_run
           OR confirmation.recommendation_id IS DISTINCT FROM NEW.recommendation_id
           OR confirmation.input_fingerprint IS DISTINCT FROM parent_fingerprint
           OR confirmation.review_preview_fingerprint IS DISTINCT FROM NEW.decision_fingerprint
           OR confirmation.approved_cases IS DISTINCT FROM NEW.approved_cases
           OR confirmation.approved_loose_units IS DISTINCT FROM NEW.approved_loose_units
           OR confirmation.approved_units IS DISTINCT FROM NEW.approved_units
           OR (NEW.evidence_json #>> '{review,material_edit_confirmation_id}')::bigint
                IS DISTINCT FROM confirmation.material_edit_confirmation_id THEN
            RAISE EXCEPTION 'MATERIAL EDIT_QUANTITY requires its exact distinct confirmation';
        END IF;
    ELSIF NEW.material_edit_confirmation_id IS NOT NULL THEN
        RAISE EXCEPTION 'NORMAL review cannot consume a material-edit confirmation';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_monday_p1_review_decision ON review_decisions;
CREATE TRIGGER trg_guard_monday_p1_review_decision
BEFORE INSERT OR UPDATE OR DELETE ON review_decisions
FOR EACH ROW EXECUTE FUNCTION guard_monday_p1_review_decision();

CREATE OR REPLACE FUNCTION guard_monday_p1_failed_run_with_draft()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.run_type='MONDAY_PROCUREMENT'
       AND OLD.status='RUNNING'
       AND (NEW.status<>'RUNNING' OR NEW.run_type<>'MONDAY_PROCUREMENT')
       AND EXISTS (
           SELECT 1 FROM purchase_orders p
            WHERE p.run_id=OLD.run_id AND p.po_status='DRAFT'
       ) THEN
        RAISE EXCEPTION 'a Monday run with DRAFT evidence cannot release its business-date claim';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_monday_p1_failed_run_with_draft ON runs;
CREATE TRIGGER trg_guard_monday_p1_failed_run_with_draft
BEFORE UPDATE ON runs
FOR EACH ROW EXECUTE FUNCTION guard_monday_p1_failed_run_with_draft();

CREATE OR REPLACE FUNCTION guard_monday_p1_draft_requires_active_run()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_type TEXT; target_status TEXT;
BEGIN
    IF TG_OP<>'DELETE' AND NEW.po_status='DRAFT' THEN
        SELECT run_type,status INTO target_type,target_status
          FROM runs WHERE run_id=NEW.run_id;
        IF target_type='MONDAY_PROCUREMENT'
           AND target_status IS DISTINCT FROM 'RUNNING' THEN
            RAISE EXCEPTION 'a Monday DRAFT requires the run to hold its active business-date claim';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_guard_monday_p1_draft_requires_active_run
    ON purchase_orders;
CREATE TRIGGER trg_guard_monday_p1_draft_requires_active_run
BEFORE INSERT OR UPDATE OF run_id,po_status ON purchase_orders
FOR EACH ROW EXECUTE FUNCTION guard_monday_p1_draft_requires_active_run();

-- Replace the reviewed 012 transition validator only to recognize an exact
-- append-only RUN_ONLY blocker exclusion.  Every other 012 invariant remains.
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
        IF bad_count <> 0 OR monday_effective_material_blocker_count(NEW.run_id)<>0 THEN
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

INSERT INTO meta(key,value) VALUES ('monday_p1_remediation_contract','v1')
ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_at=now();
