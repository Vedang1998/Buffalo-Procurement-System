-- buffalo-post-mapping-replay: checksum-skip-v1
-- buffalo-contract-family: monday-forecast-v2-retirement
-- Retire an unbuilt EMERGENCY_TRANSPARENT_V1 run without rewriting evidence.
-- This migration adds no FINAL, release, Shopify, production, or order path.

DO $$
DECLARE
    mapping_marker TEXT;
BEGIN
    SELECT value INTO mapping_marker
      FROM meta
     WHERE key='migration:014_persistent_mapping_foundation.sql';
    IF mapping_marker IS DISTINCT FROM
       'sha256:80c5d6c0a0299edf8d04f9c5f684f9cea277494b894fa0feb0a387476bfa8c86'
       OR NOT EXISTS (
           SELECT 1 FROM meta
            WHERE key='persistent_mapping_foundation_contract'
              AND value='v1-shadow-only'
       ) THEN
        RAISE EXCEPTION
            'Monday forecast V2 retirement requires the exact installed mapping predecessor';
    END IF;
    IF pg_catalog.to_regclass(
           pg_catalog.current_schema() || '.monday_stale_forecast_retirements'
       ) IS NOT NULL
       OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_attribute a
            WHERE a.attrelid='change_log'::pg_catalog.regclass
              AND a.attname='evidence_json'
              AND NOT a.attisdropped
       ) THEN
        RAISE EXCEPTION
            'partial Monday forecast V2 retirement objects already exist';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM runs
         WHERE run_type='MONDAY_PROCUREMENT'
           AND model_version='EMERGENCY_TRANSPARENT_V1'
           AND (
               procurement_output_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY'
               OR NOT (
                   status='RUNNING'
                   AND workflow_stage IN (
                       'PREPARING','AWAITING_REVIEW','REVIEWED',
                       'DRAFTS_BUILT','PACKET_BUILT'
                   )
                   OR status='FAILED' AND workflow_stage='FAILED'
               )
           )
    ) THEN
        RAISE EXCEPTION
            'unsupported historical V1 Monday run blocks retirement migration';
    END IF;
END
$$;

ALTER TABLE change_log
    ADD COLUMN evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE monday_stale_forecast_retirements (
    run_id UUID CONSTRAINT pk_monday_stale_forecast_retirements PRIMARY KEY
        CONSTRAINT fk_monday_stale_forecast_retirement_run
        REFERENCES runs(run_id) ON DELETE RESTRICT,
    input_fingerprint TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_fingerprint
        CHECK (input_fingerprint ~ '^[0-9a-f]{64}$'),
    retired_model_version TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_method
        CHECK (retired_model_version='EMERGENCY_TRANSPARENT_V1'),
    prior_status TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_prior_status
        CHECK (prior_status='RUNNING'),
    prior_workflow_stage TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_prior_stage
        CHECK (prior_workflow_stage IN ('PREPARING','AWAITING_REVIEW','REVIEWED')),
    target_status TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_target_status
        CHECK (target_status='FAILED'),
    target_workflow_stage TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_target_stage
        CHECK (target_workflow_stage='FAILED'),
    purchase_order_count INTEGER NOT NULL
        CONSTRAINT ck_monday_stale_retirement_po_count
        CHECK (purchase_order_count=0),
    artifact_count INTEGER NOT NULL
        CONSTRAINT ck_monday_stale_retirement_artifact_count
        CHECK (artifact_count=0),
    actor TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_actor
        CHECK (actor=btrim(actor) AND actor<>''),
    reason TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_reason
        CHECK (reason=btrim(reason) AND reason<>''),
    confirmation_sha256 TEXT NOT NULL
        CONSTRAINT ck_monday_stale_retirement_confirmation
        CHECK (confirmation_sha256 ~ '^[0-9a-f]{64}$'),
    before_run_json JSONB NOT NULL
        CONSTRAINT ck_monday_stale_retirement_before
        CHECK (jsonb_typeof(before_run_json)='object' AND before_run_json<>'{}'::jsonb),
    after_run_json JSONB NOT NULL
        CONSTRAINT ck_monday_stale_retirement_after
        CHECK (jsonb_typeof(after_run_json)='object' AND after_run_json<>'{}'::jsonb),
    transaction_id BIGINT NOT NULL DEFAULT pg_catalog.txid_current(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT pg_catalog.transaction_timestamp()
);

CREATE UNIQUE INDEX uq_monday_stale_forecast_retirement_confirmation
    ON monday_stale_forecast_retirements(confirmation_sha256);

CREATE UNIQUE INDEX uq_change_log_stale_forecast_retirement
    ON change_log(run_id)
    WHERE evidence_json->>'contract'=
          'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1';

CREATE FUNCTION monday_stale_forecast_retirement_confirmation_sha256(
    target_run_id UUID,
    target_input_fingerprint TEXT,
    target_prior_workflow_stage TEXT,
    target_actor TEXT,
    target_reason TEXT
) RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path FROM CURRENT
AS $$
    SELECT pg_catalog.encode(
        digest(
            pg_catalog.convert_to(
                pg_catalog.jsonb_build_object(
                    'action','RETIRE_STALE_FORECAST_RUN',
                    'actor',target_actor,
                    'artifact_count',0,
                    'contract','BUFFALO_STALE_FORECAST_RETIREMENT_CONFIRMATION_V1',
                    'input_fingerprint',target_input_fingerprint,
                    'prior_status','RUNNING',
                    'prior_workflow_stage',target_prior_workflow_stage,
                    'purchase_order_count',0,
                    'reason',target_reason,
                    'retired_model_version','EMERGENCY_TRANSPARENT_V1',
                    'run_id',target_run_id::text,
                    'target_status','FAILED',
                    'target_workflow_stage','FAILED'
                )::text,
                'UTF8'
            ),
            'sha256'
        ),
        'hex'
    )
$$;

CREATE FUNCTION validate_monday_stale_forecast_retirement()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    current_run runs%ROWTYPE;
    actual_before JSONB;
    expected_after JSONB;
    actual_po_count INTEGER;
    actual_artifact_count INTEGER;
    expected_confirmation TEXT;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Monday stale-forecast retirements are append-only';
    END IF;
    SELECT * INTO current_run FROM runs WHERE run_id=NEW.run_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'retirement run is absent';
    END IF;
    actual_before := pg_catalog.to_jsonb(current_run);
    expected_after := actual_before || pg_catalog.jsonb_build_object(
        'status','FAILED','workflow_stage','FAILED'
    );
    SELECT count(*)::integer INTO actual_po_count
      FROM purchase_orders WHERE run_id=NEW.run_id;
    SELECT count(*)::integer INTO actual_artifact_count
      FROM monday_run_artifacts WHERE run_id=NEW.run_id;
    expected_confirmation := monday_stale_forecast_retirement_confirmation_sha256(
        NEW.run_id,NEW.input_fingerprint,NEW.prior_workflow_stage,
        NEW.actor,NEW.reason
    );
    IF current_run.run_type IS DISTINCT FROM 'MONDAY_PROCUREMENT'
       OR current_run.procurement_output_mode IS DISTINCT FROM 'INTERNAL_DRAFT_ONLY'
       OR current_run.model_version IS DISTINCT FROM 'EMERGENCY_TRANSPARENT_V1'
       OR current_run.status IS DISTINCT FROM 'RUNNING'
       OR current_run.workflow_stage NOT IN ('PREPARING','AWAITING_REVIEW','REVIEWED')
       OR current_run.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint
       OR NEW.retired_model_version IS DISTINCT FROM current_run.model_version
       OR NEW.prior_status IS DISTINCT FROM current_run.status
       OR NEW.prior_workflow_stage IS DISTINCT FROM current_run.workflow_stage
       OR NEW.target_status IS DISTINCT FROM 'FAILED'
       OR NEW.target_workflow_stage IS DISTINCT FROM 'FAILED'
       OR actual_po_count IS DISTINCT FROM 0
       OR actual_artifact_count IS DISTINCT FROM 0
       OR NEW.purchase_order_count IS DISTINCT FROM actual_po_count
       OR NEW.artifact_count IS DISTINCT FROM actual_artifact_count
       OR NEW.before_run_json IS DISTINCT FROM actual_before
       OR NEW.after_run_json IS DISTINCT FROM expected_after
       OR NEW.transaction_id IS DISTINCT FROM pg_catalog.txid_current()
       OR NEW.created_at IS DISTINCT FROM pg_catalog.transaction_timestamp()
       OR NEW.confirmation_sha256 IS DISTINCT FROM expected_confirmation THEN
        RAISE EXCEPTION
            'stale-forecast retirement does not match the exact active V1 run';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_validate_monday_stale_forecast_retirement
BEFORE INSERT OR UPDATE OR DELETE ON monday_stale_forecast_retirements
FOR EACH ROW EXECUTE FUNCTION validate_monday_stale_forecast_retirement();

CREATE FUNCTION guard_monday_forecast_v1_run()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    exact_event_count INTEGER;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.run_type='MONDAY_PROCUREMENT'
           AND NEW.model_version='EMERGENCY_TRANSPARENT_V1' THEN
            RAISE EXCEPTION 'FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.run_type<>'MONDAY_PROCUREMENT'
       OR OLD.model_version IS DISTINCT FROM 'EMERGENCY_TRANSPARENT_V1' THEN
        IF TG_OP<>'DELETE'
           AND NEW.run_type='MONDAY_PROCUREMENT'
           AND NEW.model_version='EMERGENCY_TRANSPARENT_V1' THEN
            RAISE EXCEPTION 'FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED';
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'historical V1 Monday runs are immutable';
    END IF;
    IF OLD.workflow_stage='DRAFTS_BUILT'
       AND NEW.workflow_stage='PACKET_BUILT'
       AND pg_catalog.to_jsonb(NEW)-'workflow_stage'
           = pg_catalog.to_jsonb(OLD)-'workflow_stage' THEN
        RETURN NEW;
    END IF;
    IF OLD.status='RUNNING'
       AND OLD.workflow_stage IN ('PREPARING','AWAITING_REVIEW','REVIEWED')
       AND NEW.status='FAILED'
       AND NEW.workflow_stage='FAILED'
       AND pg_catalog.to_jsonb(NEW)-ARRAY['status','workflow_stage']
           = pg_catalog.to_jsonb(OLD)-ARRAY['status','workflow_stage'] THEN
        SELECT count(*)::integer INTO exact_event_count
          FROM monday_stale_forecast_retirements e
         WHERE e.run_id=OLD.run_id
           AND e.transaction_id=pg_catalog.txid_current()
           AND e.before_run_json=pg_catalog.to_jsonb(OLD)
           AND e.after_run_json=pg_catalog.to_jsonb(NEW)
           AND e.input_fingerprint=OLD.input_fingerprint
           AND e.retired_model_version=OLD.model_version
           AND e.prior_status=OLD.status
           AND e.prior_workflow_stage=OLD.workflow_stage
           AND e.target_status=NEW.status
           AND e.target_workflow_stage=NEW.workflow_stage;
        IF exact_event_count=1 THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED';
END
$$;

CREATE TRIGGER trg_guard_monday_forecast_v1_run
BEFORE INSERT OR UPDATE OR DELETE ON runs
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_run();

CREATE FUNCTION guard_monday_forecast_v1_child_evidence()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    old_run UUID;
    new_run UUID;
    old_declared_run UUID;
    new_declared_run UUID;
    old_is_v1 BOOLEAN := FALSE;
    new_is_v1 BOOLEAN := FALSE;
BEGIN
    IF TG_TABLE_NAME IN ('purchase_order_lines','po_operational_events') THEN
        IF TG_OP<>'INSERT' THEN
            SELECT run_id INTO old_run FROM purchase_orders WHERE po_id=OLD.po_id;
        END IF;
        IF TG_OP<>'DELETE' THEN
            SELECT run_id INTO new_run FROM purchase_orders WHERE po_id=NEW.po_id;
        END IF;
    ELSIF TG_TABLE_NAME='po_reconciliation_events' THEN
        IF TG_OP<>'INSERT' THEN
            SELECT p.run_id INTO old_run
              FROM purchase_order_lines l
              JOIN purchase_orders p ON p.po_id=l.po_id
             WHERE l.po_line_id=OLD.po_line_id;
        END IF;
        IF TG_OP<>'DELETE' THEN
            SELECT p.run_id INTO new_run
              FROM purchase_order_lines l
              JOIN purchase_orders p ON p.po_id=l.po_id
             WHERE l.po_line_id=NEW.po_line_id;
        END IF;
    ELSIF TG_TABLE_NAME='exceptions' THEN
        IF TG_OP<>'INSERT' THEN
            old_declared_run := OLD.run_id;
            SELECT p.run_id INTO old_run
              FROM purchase_order_lines l
              JOIN purchase_orders p ON p.po_id=l.po_id
             WHERE l.po_line_id=OLD.po_line_id;
        END IF;
        IF TG_OP<>'DELETE' THEN
            new_declared_run := NEW.run_id;
            SELECT p.run_id INTO new_run
              FROM purchase_order_lines l
              JOIN purchase_orders p ON p.po_id=l.po_id
             WHERE l.po_line_id=NEW.po_line_id;
        END IF;
    ELSIF TG_TABLE_NAME IN ('review_decisions','monday_material_edit_confirmations') THEN
        IF TG_OP<>'INSERT' THEN
            old_declared_run := OLD.run_id;
            SELECT run_id INTO old_run
              FROM procurement_recommendations
             WHERE recommendation_id=OLD.recommendation_id;
        END IF;
        IF TG_OP<>'DELETE' THEN
            new_declared_run := NEW.run_id;
            SELECT run_id INTO new_run
              FROM procurement_recommendations
             WHERE recommendation_id=NEW.recommendation_id;
        END IF;
    ELSIF TG_TABLE_NAME='monday_run_blocker_exclusions' THEN
        IF TG_OP<>'INSERT' THEN
            old_declared_run := OLD.run_id;
            SELECT run_id INTO old_run FROM exceptions
             WHERE exception_id=OLD.exception_id;
        END IF;
        IF TG_OP<>'DELETE' THEN
            new_declared_run := NEW.run_id;
            SELECT run_id INTO new_run FROM exceptions
             WHERE exception_id=NEW.exception_id;
        END IF;
    ELSE
        IF TG_OP<>'INSERT' THEN old_run := OLD.run_id; END IF;
        IF TG_OP<>'DELETE' THEN new_run := NEW.run_id; END IF;
    END IF;
    IF old_run IS NOT NULL OR old_declared_run IS NOT NULL THEN
        SELECT EXISTS(
            SELECT 1 FROM runs
             WHERE (run_id=old_run OR run_id=old_declared_run)
               AND run_type='MONDAY_PROCUREMENT'
               AND model_version='EMERGENCY_TRANSPARENT_V1'
        ) INTO old_is_v1;
    END IF;
    IF new_run IS NOT NULL OR new_declared_run IS NOT NULL THEN
        SELECT EXISTS(
            SELECT 1 FROM runs
             WHERE (run_id=new_run OR run_id=new_declared_run)
               AND run_type='MONDAY_PROCUREMENT'
               AND model_version='EMERGENCY_TRANSPARENT_V1'
        ) INTO new_is_v1;
    END IF;
    IF old_is_v1 OR new_is_v1 THEN
        RAISE EXCEPTION 'FORECAST_METHOD_RETIRED_REPREPARATION_REQUIRED';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_guard_v1_procurement_recommendations
BEFORE INSERT OR UPDATE OR DELETE ON procurement_recommendations
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_forecast_results
BEFORE INSERT OR UPDATE OR DELETE ON forecast_results
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_inventory_snapshots
BEFORE INSERT OR UPDATE OR DELETE ON inventory_snapshots
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_run_price_snapshots
BEFORE INSERT OR UPDATE OR DELETE ON run_price_snapshots
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_exceptions
BEFORE INSERT OR UPDATE OR DELETE ON exceptions
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_review_decisions
BEFORE INSERT OR UPDATE OR DELETE ON review_decisions
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_monday_run_blocker_exclusions
BEFORE INSERT OR UPDATE OR DELETE ON monday_run_blocker_exclusions
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_monday_material_edit_confirmations
BEFORE INSERT OR UPDATE OR DELETE ON monday_material_edit_confirmations
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_purchase_orders
BEFORE INSERT OR UPDATE OR DELETE ON purchase_orders
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_purchase_order_lines
BEFORE INSERT OR UPDATE OR DELETE ON purchase_order_lines
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_po_operational_events
BEFORE INSERT OR UPDATE OR DELETE ON po_operational_events
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();
CREATE TRIGGER trg_guard_v1_po_reconciliation_events
BEFORE INSERT OR UPDATE OR DELETE ON po_reconciliation_events
FOR EACH ROW EXECUTE FUNCTION guard_monday_forecast_v1_child_evidence();

CREATE FUNCTION protect_monday_stale_forecast_retirement_audit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    event monday_stale_forecast_retirements%ROWTYPE;
    current_run_json JSONB;
    expected_envelope JSONB;
    old_tagged BOOLEAN := FALSE;
    new_tagged BOOLEAN := FALSE;
BEGIN
    IF TG_OP<>'INSERT' THEN
        old_tagged := (
            OLD.evidence_json->>'contract'=
                'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1'
        ) IS TRUE;
    END IF;
    IF TG_OP<>'DELETE' THEN
        new_tagged := (
            NEW.evidence_json->>'contract'=
                'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1'
        ) IS TRUE;
    END IF;
    IF TG_OP<>'INSERT' AND (old_tagged OR new_tagged) THEN
        RAISE EXCEPTION 'stale-forecast retirement audits are append-only';
    END IF;
    IF NOT new_tagged THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO event
      FROM monday_stale_forecast_retirements
     WHERE run_id=NEW.run_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'retirement audit has no exact event';
    END IF;
    SELECT pg_catalog.to_jsonb(r) INTO current_run_json
      FROM runs r WHERE run_id=NEW.run_id;
    expected_envelope := pg_catalog.jsonb_build_object(
        'confirmation_sha256',event.confirmation_sha256,
        'contract','BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1',
        'reason',event.reason,
        'retirement_run_id',event.run_id::text,
        'transaction_id',event.transaction_id::text
    );
    IF NEW.table_name IS DISTINCT FROM 'runs'
       OR NEW.row_key IS DISTINCT FROM event.run_id::text
       OR NEW.action IS DISTINCT FROM 'UPDATE'
       OR NEW.actor IS DISTINCT FROM event.actor
       OR NEW.before_json IS DISTINCT FROM event.before_run_json
       OR NEW.after_json IS DISTINCT FROM event.after_run_json
       OR NEW.evidence_json IS DISTINCT FROM expected_envelope
       OR NEW.occurred_at IS DISTINCT FROM event.created_at
       OR current_run_json IS DISTINCT FROM event.after_run_json THEN
        RAISE EXCEPTION 'retirement audit differs from its event and final run';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_protect_monday_stale_forecast_retirement_audit
BEFORE INSERT OR UPDATE OR DELETE ON change_log
FOR EACH ROW EXECUTE FUNCTION protect_monday_stale_forecast_retirement_audit();

CREATE FUNCTION assert_monday_stale_forecast_retirement_commit()
RETURNS trigger
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    event monday_stale_forecast_retirements%ROWTYPE;
    audit_count INTEGER;
    current_run_json JSONB;
    expected_envelope JSONB;
BEGIN
    SELECT * INTO event
      FROM monday_stale_forecast_retirements
     WHERE run_id=NEW.run_id;
    SELECT pg_catalog.to_jsonb(r) INTO current_run_json
      FROM runs r WHERE run_id=NEW.run_id;
    IF NOT FOUND OR current_run_json IS DISTINCT FROM event.after_run_json THEN
        RAISE EXCEPTION 'retirement commit is missing its exact event or final run';
    END IF;
    expected_envelope := pg_catalog.jsonb_build_object(
        'confirmation_sha256',event.confirmation_sha256,
        'contract','BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1',
        'reason',event.reason,
        'retirement_run_id',event.run_id::text,
        'transaction_id',event.transaction_id::text
    );
    SELECT count(*)::integer INTO audit_count
      FROM change_log c
     WHERE c.run_id=event.run_id
       AND c.table_name='runs'
       AND c.row_key=event.run_id::text
       AND c.action='UPDATE'
       AND c.actor=event.actor
       AND c.before_json=event.before_run_json
       AND c.after_json=event.after_run_json
       AND c.evidence_json=expected_envelope
       AND c.occurred_at=event.created_at;
    IF audit_count<>1 THEN
        RAISE EXCEPTION 'retirement commit requires one exact protected audit';
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER trg_assert_monday_stale_forecast_retirement_event_commit
AFTER INSERT ON monday_stale_forecast_retirements
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_monday_stale_forecast_retirement_commit();

CREATE CONSTRAINT TRIGGER trg_assert_monday_stale_forecast_retirement_audit_commit
AFTER INSERT ON change_log
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
WHEN (NEW.evidence_json->>'contract'=
      'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1')
EXECUTE FUNCTION assert_monday_stale_forecast_retirement_commit();

CREATE FUNCTION assert_monday_forecast_v2_retirement_contract()
RETURNS VOID
LANGUAGE plpgsql
SET search_path FROM CURRENT
AS $$
DECLARE
    known_hash TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM meta
         WHERE key='monday_forecast_v2_retirement_contract' AND value='v1'
    ) OR pg_catalog.to_regclass(
        pg_catalog.current_schema() || '.monday_stale_forecast_retirements'
    ) IS NULL THEN
        RAISE EXCEPTION 'Monday forecast V2 retirement contract is absent';
    END IF;
    -- The independent runner catalog check binds the exact default.  This
    -- direct check deliberately uses information_schema rather than trusting
    -- a catalog-compute helper installed by this migration.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema=pg_catalog.current_schema()
           AND table_name='change_log'
           AND column_name='evidence_json'
           AND is_nullable='NO'
           AND column_default LIKE '%{}%jsonb%'
    ) THEN
        RAISE EXCEPTION 'protected change-log evidence column differs';
    END IF;
    IF EXISTS (
        WITH required(relation_name,trigger_name,function_name) AS (
            VALUES
            ('change_log','trg_assert_monday_stale_forecast_retirement_audit_commit','assert_monday_stale_forecast_retirement_commit'),
            ('monday_stale_forecast_retirements','trg_assert_monday_stale_forecast_retirement_event_commit','assert_monday_stale_forecast_retirement_commit'),
            ('runs','trg_guard_monday_forecast_v1_run','guard_monday_forecast_v1_run'),
            ('exceptions','trg_guard_v1_exceptions','guard_monday_forecast_v1_child_evidence'),
            ('forecast_results','trg_guard_v1_forecast_results','guard_monday_forecast_v1_child_evidence'),
            ('inventory_snapshots','trg_guard_v1_inventory_snapshots','guard_monday_forecast_v1_child_evidence'),
            ('monday_material_edit_confirmations','trg_guard_v1_monday_material_edit_confirmations','guard_monday_forecast_v1_child_evidence'),
            ('monday_run_blocker_exclusions','trg_guard_v1_monday_run_blocker_exclusions','guard_monday_forecast_v1_child_evidence'),
            ('procurement_recommendations','trg_guard_v1_procurement_recommendations','guard_monday_forecast_v1_child_evidence'),
            ('po_operational_events','trg_guard_v1_po_operational_events','guard_monday_forecast_v1_child_evidence'),
            ('po_reconciliation_events','trg_guard_v1_po_reconciliation_events','guard_monday_forecast_v1_child_evidence'),
            ('purchase_order_lines','trg_guard_v1_purchase_order_lines','guard_monday_forecast_v1_child_evidence'),
            ('purchase_orders','trg_guard_v1_purchase_orders','guard_monday_forecast_v1_child_evidence'),
            ('review_decisions','trg_guard_v1_review_decisions','guard_monday_forecast_v1_child_evidence'),
            ('run_price_snapshots','trg_guard_v1_run_price_snapshots','guard_monday_forecast_v1_child_evidence'),
            ('change_log','trg_protect_monday_stale_forecast_retirement_audit','protect_monday_stale_forecast_retirement_audit'),
            ('monday_stale_forecast_retirements','trg_validate_monday_stale_forecast_retirement','validate_monday_stale_forecast_retirement')
        ), actual AS (
            SELECT c.relname AS relation_name,t.tgname AS trigger_name,
                   p.proname AS function_name
              FROM pg_catalog.pg_trigger t
              JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
              JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
             JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
             WHERE n.nspname=pg_catalog.current_schema()
               AND NOT t.tgisinternal AND t.tgenabled='O'
               AND t.tgname IN (SELECT trigger_name FROM required)
        )
        (SELECT * FROM required EXCEPT SELECT * FROM actual)
        UNION ALL
        (SELECT * FROM actual EXCEPT SELECT * FROM required)
    ) THEN
        RAISE EXCEPTION 'Monday forecast V2 retirement trigger contract differs';
    END IF;
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema=pg_catalog.current_schema()
           AND table_name='monday_stale_forecast_retirements')<>16
       OR (SELECT count(*) FROM pg_catalog.pg_constraint c
           WHERE c.conrelid='monday_stale_forecast_retirements'::pg_catalog.regclass
             AND c.convalidated)<>16
       OR EXISTS (
           SELECT required_name FROM pg_catalog.unnest(ARRAY[
               'pk_monday_stale_forecast_retirements',
               'uq_monday_stale_forecast_retirement_confirmation',
               'uq_change_log_stale_forecast_retirement'
           ]) required_name
           WHERE NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_class i
               JOIN pg_catalog.pg_index x ON x.indexrelid=i.oid
               WHERE i.relnamespace=pg_catalog.to_regnamespace(pg_catalog.current_schema())
                 AND i.relname=required_name
                 AND x.indisvalid AND x.indisready AND x.indislive
           )
       )
       OR EXISTS (
           SELECT required_name,required_args FROM (
               VALUES
               ('assert_monday_forecast_v2_retirement_contract',''),
               ('assert_monday_stale_forecast_retirement_commit',''),
               ('guard_monday_forecast_v1_child_evidence',''),
               ('guard_monday_forecast_v1_run',''),
               ('monday_stale_forecast_retirement_confirmation_sha256','target_run_id uuid, target_input_fingerprint text, target_prior_workflow_stage text, target_actor text, target_reason text'),
               ('protect_monday_stale_forecast_retirement_audit',''),
               ('validate_monday_stale_forecast_retirement','')
           ) required(required_name,required_args)
           WHERE NOT EXISTS (
               SELECT 1 FROM pg_catalog.pg_proc p
               WHERE p.pronamespace=pg_catalog.to_regnamespace(pg_catalog.current_schema())
                 AND p.proname=required_name
                 AND pg_catalog.pg_get_function_identity_arguments(p.oid)=required_args
           )
       ) THEN
        RAISE EXCEPTION 'Monday forecast V2 retirement object inventory differs';
    END IF;
    known_hash := monday_stale_forecast_retirement_confirmation_sha256(
        '00000000-0000-4000-8000-000000000001'::uuid,
        repeat('a',64),'PREPARING','synthetic:known-answer','known answer'
    );
    IF known_hash IS DISTINCT FROM
       '486fa52a631efe2f4a4d0707394390fa35b11fe1dfba9a6b341764d383a98c33' THEN
        RAISE EXCEPTION 'Monday forecast retirement known-answer hash differs';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM monday_stale_forecast_retirements e
         WHERE pg_catalog.to_jsonb((SELECT r FROM runs r WHERE r.run_id=e.run_id))
                   IS DISTINCT FROM e.after_run_json
            OR NOT EXISTS (
                SELECT 1 FROM change_log c
                 WHERE c.run_id=e.run_id
                   AND c.evidence_json->>'contract'=
                       'BUFFALO_STALE_FORECAST_RETIREMENT_AUDIT_V1'
            )
    ) THEN
        RAISE EXCEPTION 'persisted stale-forecast retirement evidence differs';
    END IF;
END
$$;

REVOKE ALL ON TABLE monday_stale_forecast_retirements FROM PUBLIC;
REVOKE ALL ON FUNCTION
    monday_stale_forecast_retirement_confirmation_sha256(UUID,TEXT,TEXT,TEXT,TEXT),
    validate_monday_stale_forecast_retirement(),
    guard_monday_forecast_v1_run(),
    guard_monday_forecast_v1_child_evidence(),
    protect_monday_stale_forecast_retirement_audit(),
    assert_monday_stale_forecast_retirement_commit(),
    assert_monday_forecast_v2_retirement_contract()
FROM PUBLIC;

-- Contract metadata and the checksum marker are deliberately published by the
-- independently pinned runner only after its pg_catalog projection matches.
