-- buffalo-post-mapping-application-release: marker-last
-- buffalo-contract-family: synthetic-price-replacement
-- Synthetic-only complete-vendor monthly replacement after exact release 015.
-- This release does not alter 014-owned functions and creates no reusable
-- historical price archive.

DO $guard$
BEGIN
    IF (SELECT value FROM meta WHERE key='migration:015_monday_forecast_v2_retirement.sql')
           IS DISTINCT FROM
           'sha256:e3f69e23cf6fce0760add5ec6a329426444dd44b681e338aded9d833e89ba56b'
       OR (SELECT value FROM meta WHERE key='monday_forecast_v2_retirement_contract')
           IS DISTINCT FROM 'v1'
       OR (SELECT value FROM meta WHERE key='monday_price_book_contract')
           IS DISTINCT FROM 'v2-future-only' THEN
        RAISE EXCEPTION 'synthetic price replacement requires exact 011/015 predecessor';
    END IF;
    IF EXISTS (
        SELECT 1 FROM meta
         WHERE key='migration:016_synthetic_price_replacement.sql'
    ) THEN
        RAISE EXCEPTION 'synthetic price replacement body must not replay';
    END IF;
END
$guard$;

CREATE TABLE supplier_price_schedule_policies (
    policy_ref TEXT PRIMARY KEY CHECK (btrim(policy_ref)<>''),
    policy_version INTEGER NOT NULL CHECK (policy_version=1),
    vendor_id UUID NOT NULL UNIQUE REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    price_scope_key TEXT NOT NULL CHECK (price_scope_key='COMPLETE_VENDOR'),
    cadence TEXT NOT NULL CHECK (cadence='MONTHLY'),
    currency TEXT NOT NULL CHECK (currency='USD'),
    policy_timezone TEXT NOT NULL CHECK (policy_timezone='America/New_York'),
    observation_window_start_day INTEGER NOT NULL CHECK (observation_window_start_day=15),
    observation_window_end_day INTEGER NOT NULL CHECK (observation_window_end_day=20),
    effective_boundary_day INTEGER NOT NULL CHECK (effective_boundary_day=1),
    source_validity_required BOOLEAN NOT NULL CHECK (source_validity_required),
    fixture_policy_config_sha256 TEXT NOT NULL CHECK (
        fixture_policy_config_sha256 ~ '^[0-9a-f]{64}$'
    ),
    fixture_database_name TEXT NOT NULL CHECK (
        fixture_database_name ~ '^[a-z][a-z0-9_]*_demo$'
    ),
    policy_principal_ref TEXT NOT NULL CHECK (
        policy_principal_ref='synthetic:price-fixture-registration:01'
    ),
    observation_at TIMESTAMPTZ NOT NULL,
    application_at TIMESTAMPTZ NOT NULL,
    monday_evaluation_at TIMESTAMPTZ NOT NULL,
    policy_sha256 TEXT NOT NULL CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_txid BIGINT NOT NULL DEFAULT txid_current(),
    UNIQUE(vendor_id,price_scope_key)
);

ALTER TABLE price_book_batches
    ADD COLUMN replacement_contract TEXT,
    ADD COLUMN schedule_policy_ref TEXT
        REFERENCES supplier_price_schedule_policies(policy_ref) ON DELETE RESTRICT,
    ADD COLUMN price_scope_key TEXT,
    ADD COLUMN source_period_label TEXT,
    ADD COLUMN source_valid_from DATE,
    ADD COLUMN source_valid_through DATE,
    ADD COLUMN source_validity_basis TEXT,
    ADD COLUMN supplier_verified_at TIMESTAMPTZ,
    ADD COLUMN operational_effective_from DATE,
    ADD COLUMN operational_effective_through DATE,
    ADD COLUMN scope_membership_sha256 TEXT,
    ADD COLUMN declaration_sha256 TEXT;

ALTER TABLE price_book_batches ADD CONSTRAINT ck_declared_price_replacement CHECK (
    (replacement_contract IS NULL
      AND schedule_policy_ref IS NULL AND price_scope_key IS NULL
      AND source_period_label IS NULL AND source_valid_from IS NULL
      AND source_valid_through IS NULL AND source_validity_basis IS NULL
      AND supplier_verified_at IS NULL AND operational_effective_from IS NULL
      AND operational_effective_through IS NULL
      AND scope_membership_sha256 IS NULL AND declaration_sha256 IS NULL)
    OR
    (replacement_contract='SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
      AND schedule_policy_ref IS NOT NULL
      AND price_scope_key='COMPLETE_VENDOR'
      AND source_period_label IS NOT NULL AND btrim(source_period_label)<>''
      AND source_valid_from IS NOT NULL AND source_valid_through>=source_valid_from
      AND source_validity_basis IS NOT NULL AND btrim(source_validity_basis)<>''
      AND supplier_verified_at IS NOT NULL
      AND operational_effective_from=effective_from
      AND operational_effective_through IS NOT DISTINCT FROM effective_through
      AND declaration_sha256 ~ '^[0-9a-f]{64}$'
      AND (scope_membership_sha256 IS NULL
           OR scope_membership_sha256 ~ '^[0-9a-f]{64}$'))
);

CREATE TABLE price_book_scope_memberships (
    price_book_batch_id UUID NOT NULL
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    source_row_number INTEGER NOT NULL CHECK (source_row_number>=2),
    vendor_id UUID NOT NULL REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    price_scope_key TEXT NOT NULL CHECK (price_scope_key='COMPLETE_VENDOR'),
    offer_id BIGINT NOT NULL REFERENCES supplier_offers(offer_id) ON DELETE RESTRICT,
    variant_id TEXT NOT NULL,
    supplier_sku TEXT NOT NULL CHECK (btrim(supplier_sku)<>''),
    package_type TEXT NOT NULL,
    size_text TEXT NOT NULL,
    raw_pack TEXT NOT NULL,
    shopify_units_per_case NUMERIC(12,4) NOT NULL CHECK (shopify_units_per_case>0),
    qualifying_units_per_case NUMERIC(12,4) NOT NULL CHECK (qualifying_units_per_case>0),
    level_type TEXT NOT NULL CHECK (level_type IN ('BASE','BREAK')),
    break_qty NUMERIC(12,4),
    break_unit TEXT CHECK (break_unit IN ('BT','CS') OR break_unit IS NULL),
    source_file TEXT NOT NULL CHECK (btrim(source_file)<>''),
    source_page INTEGER,
    source_row_sha256 TEXT NOT NULL CHECK (source_row_sha256 ~ '^[0-9a-f]{64}$'),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    captured_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY(price_book_batch_id,source_row_number)
);

CREATE UNIQUE INDEX uq_price_book_scope_membership_ladder
    ON price_book_scope_memberships(
        price_book_batch_id,offer_id,level_type,
        COALESCE(break_qty,-1),COALESCE(break_unit,'')
    );

ALTER TABLE price_book_promotion_events
    ADD COLUMN replacement_contract TEXT,
    ADD COLUMN confirmation_idempotency_key TEXT,
    ADD COLUMN schedule_policy_ref TEXT
        REFERENCES supplier_price_schedule_policies(policy_ref) ON DELETE RESTRICT,
    ADD COLUMN price_scope_key TEXT,
    ADD COLUMN human_principal_ref TEXT,
    ADD COLUMN human_role_ref TEXT,
    ADD COLUMN human_authn_context_sha256 TEXT,
    ADD COLUMN preview_sha256 TEXT,
    ADD COLUMN confirmation_sha256 TEXT,
    ADD COLUMN confirmation_payload_sha256 TEXT,
    ADD COLUMN scope_membership_sha256 TEXT,
    ADD COLUMN raw_content_sha256 TEXT,
    ADD COLUMN declaration_sha256 TEXT,
    ADD COLUMN policy_sha256 TEXT;

ALTER TABLE price_book_promotion_events
    ADD CONSTRAINT ck_declared_price_confirmation CHECK (
      (replacement_contract IS NULL
       AND confirmation_idempotency_key IS NULL AND schedule_policy_ref IS NULL
       AND price_scope_key IS NULL AND human_principal_ref IS NULL
       AND human_role_ref IS NULL AND human_authn_context_sha256 IS NULL
       AND preview_sha256 IS NULL AND confirmation_sha256 IS NULL
       AND confirmation_payload_sha256 IS NULL AND scope_membership_sha256 IS NULL
       AND raw_content_sha256 IS NULL AND declaration_sha256 IS NULL
       AND policy_sha256 IS NULL)
      OR
      (replacement_contract='SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
       AND confirmation_idempotency_key IS NOT NULL
       AND btrim(confirmation_idempotency_key)<>''
       AND schedule_policy_ref IS NOT NULL AND price_scope_key='COMPLETE_VENDOR'
       AND human_principal_ref IS NOT NULL AND btrim(human_principal_ref)<>''
       AND human_role_ref='procurement.price.approve'
       AND human_authn_context_sha256 ~ '^[0-9a-f]{64}$'
       AND preview_sha256 ~ '^[0-9a-f]{64}$'
       AND confirmation_sha256 ~ '^[0-9a-f]{64}$'
       AND confirmation_payload_sha256 ~ '^[0-9a-f]{64}$'
       AND scope_membership_sha256 ~ '^[0-9a-f]{64}$'
       AND raw_content_sha256 ~ '^[0-9a-f]{64}$'
       AND declaration_sha256 ~ '^[0-9a-f]{64}$'
       AND policy_sha256 ~ '^[0-9a-f]{64}$'));

CREATE UNIQUE INDEX uq_declared_price_confirmation_idempotency
    ON price_book_promotion_events(confirmation_idempotency_key)
    WHERE confirmation_idempotency_key IS NOT NULL;

CREATE TABLE supplier_price_authority_events (
    supplier_price_authority_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action TEXT NOT NULL CHECK (action IN ('ADOPT_EXISTING_BASELINE','APPLY_REPLACEMENT')),
    vendor_id UUID NOT NULL REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    price_scope_key TEXT NOT NULL CHECK (price_scope_key='COMPLETE_VENDOR'),
    schedule_policy_ref TEXT NOT NULL
        REFERENCES supplier_price_schedule_policies(policy_ref) ON DELETE RESTRICT,
    price_book_batch_id UUID
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    prior_event_id UUID
        REFERENCES supplier_price_authority_events(supplier_price_authority_event_id)
        ON DELETE RESTRICT,
    expected_prior_head_version BIGINT,
    expected_current_scope_sha256 TEXT NOT NULL CHECK (
        expected_current_scope_sha256 ~ '^[0-9a-f]{64}$'
    ),
    resulting_current_scope_sha256 TEXT NOT NULL CHECK (
        resulting_current_scope_sha256 ~ '^[0-9a-f]{64}$'
    ),
    scope_membership_sha256 TEXT,
    raw_content_sha256 TEXT,
    backup_contract TEXT,
    backup_manifest_ref TEXT,
    backup_manifest_sha256 TEXT,
    backup_dump_sha256 TEXT,
    backup_storage_sha256 TEXT,
    backup_prechange_scope_sha256 TEXT,
    apply_idempotency_key TEXT,
    human_principal_ref TEXT,
    human_role_ref TEXT,
    human_authn_context_sha256 TEXT,
    preview_sha256 TEXT,
    confirmation_sha256 TEXT,
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_json JSONB NOT NULL CHECK (
        jsonb_typeof(evidence_json)='object' AND evidence_json<>'{}'::jsonb
    ),
    transaction_id BIGINT NOT NULL DEFAULT txid_current(),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(vendor_id,price_scope_key,apply_idempotency_key)
);

CREATE TABLE supplier_price_authority_heads (
    vendor_id UUID NOT NULL REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    price_scope_key TEXT NOT NULL CHECK (price_scope_key='COMPLETE_VENDOR'),
    supplier_price_authority_event_id UUID NOT NULL UNIQUE
        REFERENCES supplier_price_authority_events(supplier_price_authority_event_id)
        ON DELETE RESTRICT,
    head_version BIGINT NOT NULL CHECK (head_version>0),
    active_price_book_batch_id UUID
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    current_scope_sha256 TEXT NOT NULL CHECK (current_scope_sha256 ~ '^[0-9a-f]{64}$'),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY(vendor_id,price_scope_key)
);

ALTER TABLE run_price_snapshots
    ADD COLUMN source_price_id BIGINT,
    ADD COLUMN source_price_book_batch_id UUID
        REFERENCES price_book_batches(price_book_batch_id) ON DELETE RESTRICT,
    ADD COLUMN source_price_book_row_number INTEGER,
    ADD COLUMN supplier_price_authority_event_id UUID
        REFERENCES supplier_price_authority_events(supplier_price_authority_event_id)
        ON DELETE RESTRICT;

ALTER TABLE run_price_snapshots ADD CONSTRAINT ck_run_price_authority_lineage CHECK (
    (source_price_id IS NULL AND source_price_book_batch_id IS NULL
      AND source_price_book_row_number IS NULL
      AND supplier_price_authority_event_id IS NULL)
    OR
    (source_price_id IS NOT NULL AND source_price_book_batch_id IS NOT NULL
      AND source_price_book_row_number>=2
      AND supplier_price_authority_event_id IS NOT NULL)
);

CREATE OR REPLACE FUNCTION supplier_price_text_sha256(value TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path=pg_catalog
AS $$ SELECT encode(qa_mapping_test.digest(convert_to(value,'UTF8'),'sha256'),'hex') $$;

CREATE OR REPLACE FUNCTION supplier_price_require_enabled_capability(expected TEXT)
RETURNS VOID LANGUAGE plpgsql STABLE
SET search_path=pg_catalog
AS $$
BEGIN
    IF current_setting('procurement.enabled_capability',true) IS DISTINCT FROM expected
       OR current_setting('procurement.synthetic_price_replacement',true)
            IS DISTINCT FROM 'enabled' THEN
        RAISE EXCEPTION 'required synthetic price replacement capability is absent';
    END IF;
    PERFORM qa_mapping_test.persistent_mapping_assert_safe_role_topology();
END
$$;

CREATE OR REPLACE FUNCTION supplier_price_scope_current_payload(
    wanted_vendor UUID
) RETURNS JSONB LANGUAGE sql STABLE
SET search_path=pg_catalog,qa_mapping_test
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_array(
        o.offer_id,o.variant_id,o.supplier_sku,o.package_type,o.size_text,o.raw_pack,
        o.shopify_units_per_case,o.qualifying_units_per_case,
        p.price_id,p.effective_month,p.level_type,p.break_qty,p.break_unit,
        p.case_price,p.unit_price,p.source_file,p.source_page,
        p.extraction_confidence,p.verified,p.source_price_book_batch_id,
        p.source_price_book_row_number,p.effective_from,p.effective_through
    ) ORDER BY o.offer_id,(p.level_type<>'BASE'),p.break_unit NULLS FIRST,
       p.break_qty NULLS FIRST,p.price_id),'[]'::jsonb)
      FROM qa_mapping_test.prices p
      JOIN qa_mapping_test.supplier_offers o ON o.offer_id=p.offer_id
     WHERE o.vendor_id=wanted_vendor AND p.price_state='current'
$$;

CREATE OR REPLACE FUNCTION supplier_price_scope_current_sha256(wanted_vendor UUID)
RETURNS TEXT LANGUAGE sql STABLE
SET search_path=pg_catalog,qa_mapping_test
AS $$
    SELECT qa_mapping_test.persistent_mapping_json_sha256(
        qa_mapping_test.supplier_price_scope_current_payload(wanted_vendor)
    )
$$;

CREATE OR REPLACE FUNCTION supplier_price_scope_batch_as_current_payload(
    wanted_vendor UUID, wanted_batch UUID
) RETURNS JSONB LANGUAGE sql STABLE
SET search_path=pg_catalog,qa_mapping_test
AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_array(
        o.offer_id,o.variant_id,o.supplier_sku,o.package_type,o.size_text,o.raw_pack,
        o.shopify_units_per_case,o.qualifying_units_per_case,
        p.price_id,p.effective_month,p.level_type,p.break_qty,p.break_unit,
        p.case_price,p.unit_price,p.source_file,p.source_page,
        p.extraction_confidence,p.verified,p.source_price_book_batch_id,
        p.source_price_book_row_number,p.effective_from,p.effective_through
    ) ORDER BY o.offer_id,(p.level_type<>'BASE'),p.break_unit NULLS FIRST,
       p.break_qty NULLS FIRST,p.price_id),'[]'::jsonb)
      FROM qa_mapping_test.prices p
      JOIN qa_mapping_test.supplier_offers o ON o.offer_id=p.offer_id
     WHERE o.vendor_id=wanted_vendor
       AND p.source_price_book_batch_id=wanted_batch
$$;

CREATE OR REPLACE FUNCTION supplier_price_scope_batch_as_current_sha256(
    wanted_vendor UUID, wanted_batch UUID
) RETURNS TEXT LANGUAGE sql STABLE
SET search_path=pg_catalog,qa_mapping_test
AS $$
    SELECT qa_mapping_test.persistent_mapping_json_sha256(
        qa_mapping_test.supplier_price_scope_batch_as_current_payload(
            wanted_vendor,wanted_batch
        )
    )
$$;

CREATE OR REPLACE FUNCTION supplier_price_unaffected_state_sha256(
    excluded_vendor UUID
) RETURNS TEXT LANGUAGE sql STABLE
SET search_path=pg_catalog,qa_mapping_test
AS $$
    SELECT qa_mapping_test.persistent_mapping_json_sha256(
      COALESCE(jsonb_agg(jsonb_build_object(
        'vendor_id',o.vendor_id,'price',to_jsonb(p)
      ) ORDER BY o.vendor_id,p.price_id),'[]'::jsonb)
    )
      FROM qa_mapping_test.prices p
      JOIN qa_mapping_test.supplier_offers o ON o.offer_id=p.offer_id
     WHERE o.vendor_id<>excluded_vendor
$$;

CREATE OR REPLACE FUNCTION supplier_price_membership_sha256(wanted_batch UUID)
RETURNS TEXT LANGUAGE sql STABLE
SET search_path=pg_catalog,qa_mapping_test
AS $$
    SELECT qa_mapping_test.persistent_mapping_json_sha256(
        COALESCE(jsonb_agg(jsonb_build_array(
          source_row_number,vendor_id,price_scope_key,offer_id,variant_id,
          supplier_sku,package_type,size_text,raw_pack,shopify_units_per_case,
          qualifying_units_per_case,level_type,break_qty,break_unit,source_file,
          source_page,source_row_sha256
        ) ORDER BY source_row_number),'[]'::jsonb)
    ) FROM qa_mapping_test.price_book_scope_memberships
       WHERE price_book_batch_id=wanted_batch
$$;

CREATE OR REPLACE FUNCTION prevent_supplier_price_authority_mutation()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$ BEGIN RAISE EXCEPTION 'supplier price authority evidence is append-only'; END $$;

CREATE OR REPLACE FUNCTION validate_supplier_price_schedule_policy_insert()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE
    registration JSONB;
    registered_policy JSONB;
BEGIN
    IF EXISTS (SELECT 1 FROM meta
                WHERE key='migration:016_synthetic_price_replacement.sql')
       OR current_setting('procurement.synthetic_price_fixture_sha256',true)
            IS DISTINCT FROM
            '4ac0137a42e79f560fbab6a4f6073324e553f2ca924d0e3a8c5956f51dd79659'
       OR current_setting('procurement.synthetic_price_fixture_registration',true)
            IS NULL THEN
        RAISE EXCEPTION 'supplier price policy requires one-use migration registration';
    END IF;
    registration := current_setting(
        'procurement.synthetic_price_fixture_registration',true
    )::JSONB;
    SELECT value INTO registered_policy
      FROM jsonb_array_elements(registration->'policies')
     WHERE value->>'policy_ref'=NEW.policy_ref
       AND value->>'vendor_id'=NEW.vendor_id::TEXT;
    IF registered_policy IS NULL
       OR NEW.fixture_database_name IS DISTINCT FROM current_database()
       OR NEW.fixture_policy_config_sha256 IS DISTINCT FROM
            current_setting('procurement.synthetic_price_fixture_sha256',true)
       OR NEW.policy_version<>1 OR NEW.price_scope_key<>'COMPLETE_VENDOR'
       OR NEW.cadence<>'MONTHLY' OR NEW.currency<>'USD'
       OR NEW.policy_timezone IS DISTINCT FROM registration->>'policy_timezone'
       OR NEW.observation_window_start_day<>15
       OR NEW.observation_window_end_day<>20
       OR NEW.effective_boundary_day<>1
       OR NOT NEW.source_validity_required
       OR NEW.policy_principal_ref<>'synthetic:price-fixture-registration:01'
       OR NEW.observation_at IS DISTINCT FROM
            (registration->>'observation_at')::TIMESTAMPTZ
       OR NEW.application_at IS DISTINCT FROM
            (registration->>'application_at')::TIMESTAMPTZ
       OR NEW.monday_evaluation_at IS DISTINCT FROM
            (registration->>'monday_evaluation_at')::TIMESTAMPTZ
       OR NEW.policy_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(registered_policy) THEN
        RAISE EXCEPTION 'supplier price policy differs from pinned registration';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION validate_price_book_scope_membership_insert()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE
    b price_book_batches%ROWTYPE;
    s price_book_staging_rows%ROWTYPE;
    o supplier_offers%ROWTYPE;
BEGIN
    PERFORM supplier_price_require_enabled_capability(
        'synthetic_price_confirmation_enabled'
    );
    PERFORM persistent_mapping_require_human_context(
        current_setting('procurement.principal_ref',true),
        current_setting('procurement.authorized_role_ref',true),
        current_setting('procurement.authn_context_sha256',true),
        'PRICE_REPLACEMENT_CONFIRM'
    );
    SELECT * INTO b FROM price_book_batches
     WHERE price_book_batch_id=NEW.price_book_batch_id FOR UPDATE;
    SELECT * INTO s FROM price_book_staging_rows
     WHERE price_book_batch_id=NEW.price_book_batch_id
       AND source_row_number=NEW.source_row_number;
    SELECT * INTO o FROM supplier_offers WHERE offer_id=NEW.offer_id;
    IF b.price_book_batch_id IS NULL
       OR b.status<>'VALIDATED'
       OR b.replacement_contract<>'SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
       OR b.scope_membership_sha256 IS NOT NULL
       OR s.price_book_staging_row_id IS NULL
       OR s.validation_status<>'VALID'
       OR o.offer_id IS NULL
       OR NEW.captured_txid<>txid_current()
       OR NEW.vendor_id IS DISTINCT FROM b.vendor_id
       OR NEW.vendor_id IS DISTINCT FROM o.vendor_id
       OR NEW.price_scope_key IS DISTINCT FROM b.price_scope_key
       OR NEW.offer_id IS DISTINCT FROM s.offer_id
       OR NEW.variant_id IS DISTINCT FROM s.canonical_variant_id
       OR NEW.variant_id IS DISTINCT FROM o.variant_id
       OR NEW.supplier_sku IS DISTINCT FROM s.supplier_sku
       OR NEW.supplier_sku IS DISTINCT FROM o.supplier_sku
       OR NEW.package_type IS DISTINCT FROM s.package_type
       OR NEW.package_type IS DISTINCT FROM o.package_type
       OR NEW.size_text IS DISTINCT FROM s.size_text
       OR NEW.size_text IS DISTINCT FROM o.size_text
       OR NEW.raw_pack IS DISTINCT FROM s.raw_pack
       OR NEW.raw_pack IS DISTINCT FROM o.raw_pack
       OR NEW.shopify_units_per_case IS DISTINCT FROM s.shopify_units_per_case
       OR NEW.shopify_units_per_case IS DISTINCT FROM o.shopify_units_per_case
       OR NEW.qualifying_units_per_case IS DISTINCT FROM
            s.qualifying_units_per_case
       OR NEW.qualifying_units_per_case IS DISTINCT FROM
            o.qualifying_units_per_case
       OR NEW.level_type IS DISTINCT FROM s.level_type
       OR NEW.break_qty IS DISTINCT FROM s.break_quantity
       OR NEW.break_unit IS DISTINCT FROM s.break_unit
       OR NEW.source_file IS DISTINCT FROM s.source_file
       OR NEW.source_page IS DISTINCT FROM s.source_page
       OR NEW.source_row_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(s.raw_payload) THEN
        RAISE EXCEPTION 'price-book scope membership differs from locked validation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_protect_supplier_price_schedule_policies
BEFORE UPDATE OR DELETE ON supplier_price_schedule_policies
FOR EACH ROW EXECUTE FUNCTION prevent_supplier_price_authority_mutation();
CREATE TRIGGER trg_validate_supplier_price_schedule_policy_insert
BEFORE INSERT ON supplier_price_schedule_policies
FOR EACH ROW EXECUTE FUNCTION validate_supplier_price_schedule_policy_insert();
CREATE TRIGGER trg_protect_price_book_scope_memberships
BEFORE UPDATE OR DELETE ON price_book_scope_memberships
FOR EACH ROW EXECUTE FUNCTION prevent_supplier_price_authority_mutation();
CREATE TRIGGER trg_validate_price_book_scope_membership_insert
BEFORE INSERT ON price_book_scope_memberships
FOR EACH ROW EXECUTE FUNCTION validate_price_book_scope_membership_insert();
CREATE TRIGGER trg_protect_supplier_price_authority_events
BEFORE UPDATE OR DELETE ON supplier_price_authority_events
FOR EACH ROW EXECUTE FUNCTION prevent_supplier_price_authority_mutation();

DROP TRIGGER trg_validate_price_book_promotion_event
    ON price_book_promotion_events;
CREATE TRIGGER trg_validate_price_book_promotion_event
BEFORE INSERT ON price_book_promotion_events
FOR EACH ROW WHEN (NEW.replacement_contract IS NULL)
EXECUTE FUNCTION validate_price_book_promotion_event();

CREATE OR REPLACE FUNCTION validate_declared_price_promotion_event()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE
    b price_book_batches%ROWTYPE;
    policy supplier_price_schedule_policies%ROWTYPE;
    active_predecessor UUID;
    expected_offers JSONB;
    staged_offers JSONB;
    error_codes JSONB;
    warning_codes JSONB;
BEGIN
    SELECT * INTO b FROM price_book_batches
     WHERE price_book_batch_id=NEW.price_book_batch_id FOR UPDATE;
    SELECT * INTO policy FROM supplier_price_schedule_policies
     WHERE policy_ref=b.schedule_policy_ref;
    SELECT predecessor.price_book_batch_id INTO active_predecessor
      FROM price_book_batches predecessor
     WHERE predecessor.vendor_id=b.vendor_id
       AND predecessor.status='VERIFIED_FUTURE';
    SELECT COALESCE(jsonb_agg(o.offer_id ORDER BY o.offer_id),'[]'::jsonb)
      INTO expected_offers
      FROM supplier_offers o
      JOIN vendors v ON v.vendor_id=o.vendor_id
     WHERE o.vendor_id=b.vendor_id AND o.active
       AND v.active AND is_procurement_eligible_variant(o.variant_id);
    SELECT COALESCE(jsonb_agg(distinct_ids.offer_id ORDER BY distinct_ids.offer_id),'[]'::jsonb)
      INTO staged_offers
      FROM (
            SELECT DISTINCT s.offer_id
              FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status='VALID' AND s.offer_id IS NOT NULL
      ) distinct_ids;
    SELECT COALESCE(jsonb_agg(c.issue_code ORDER BY c.issue_code),'[]'::jsonb)
      INTO error_codes
      FROM (
            SELECT DISTINCT issue_code
              FROM price_book_validation_issues
             WHERE price_book_batch_id=b.price_book_batch_id AND severity='ERROR'
      ) c;
    SELECT COALESCE(jsonb_agg(c.issue_code ORDER BY c.issue_code),'[]'::jsonb)
      INTO warning_codes
      FROM (
            SELECT DISTINCT issue_code
              FROM price_book_validation_issues
             WHERE price_book_batch_id=b.price_book_batch_id AND severity='WARN'
      ) c;
    IF b.replacement_contract<>'SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
       OR NEW.replacement_contract IS DISTINCT FROM b.replacement_contract
       OR NEW.schedule_policy_ref IS DISTINCT FROM b.schedule_policy_ref
       OR NEW.price_scope_key IS DISTINCT FROM b.price_scope_key
       OR policy.policy_ref IS NULL
       OR policy.vendor_id IS DISTINCT FROM b.vendor_id
       OR policy.price_scope_key IS DISTINCT FROM b.price_scope_key
       OR policy.fixture_database_name IS DISTINCT FROM current_database()
       OR b.supplier_verified_at IS DISTINCT FROM policy.observation_at
       OR extract(day FROM policy.observation_at AT TIME ZONE policy.policy_timezone)
            NOT BETWEEN policy.observation_window_start_day
                    AND policy.observation_window_end_day
       OR date_trunc('month',b.effective_from)::date IS DISTINCT FROM
            (date_trunc('month',policy.observation_at AT TIME ZONE policy.policy_timezone)
             + interval '1 month')::date
       OR b.status<>'VALIDATED'
       OR b.target_price_state<>'future'
       OR b.vendor_id IS NULL
       OR NOT EXISTS (
            SELECT 1 FROM vendors v
             WHERE v.vendor_id=b.vendor_id
               AND v.vendor_name=b.vendor_name
               AND v.active
       )
       OR b.validation_fingerprint IS DISTINCT FROM NEW.validation_fingerprint
       OR b.future_predecessor_sha256 IS DISTINCT FROM NEW.predecessor_future_sha256
       OR b.future_predecessor_batch_id IS DISTINCT FROM NEW.predecessor_batch_id
       OR active_predecessor IS DISTINCT FROM b.future_predecessor_batch_id
       OR NEW.transaction_id<>txid_current()
       OR NEW.promoted_row_count<>b.row_count
       OR NEW.promoted_semantic_md5 IS DISTINCT FROM
            price_book_staging_semantic_md5(b.price_book_batch_id)
       OR NEW.evidence_json->>'promoted_future_sha256'
            IS DISTINCT FROM NEW.promoted_future_sha256
       OR NEW.evidence_json->>'promoted_semantic_md5'
            IS DISTINCT FROM NEW.promoted_semantic_md5
       OR NEW.evidence_json->>'validation_fingerprint'
            IS DISTINCT FROM NEW.validation_fingerprint
       OR NEW.evidence_json->>'content_sha256' IS DISTINCT FROM b.content_sha256
       OR expected_offers IS DISTINCT FROM
            COALESCE(b.validation_evidence->'expected_offer_ids','[]'::jsonb)
       OR expected_offers IS DISTINCT FROM
            COALESCE(b.validation_evidence->'covered_offer_ids','[]'::jsonb)
       OR staged_offers IS DISTINCT FROM expected_offers
       OR b.expected_offer_count<>jsonb_array_length(expected_offers)
       OR b.covered_offer_count<>jsonb_array_length(staged_offers)
       OR b.missing_offer_count<>0
       OR COALESCE(b.validation_evidence->'missing_offer_ids','[]'::jsonb)<>'[]'::jsonb
       OR COALESCE(b.validation_evidence->'error_codes','[]'::jsonb)
            IS DISTINCT FROM error_codes
       OR COALESCE(b.validation_evidence->'warning_codes','[]'::jsonb)
            IS DISTINCT FROM warning_codes
       OR b.error_count<>(SELECT count(*) FROM price_book_validation_issues i
                           WHERE i.price_book_batch_id=b.price_book_batch_id
                             AND i.severity='ERROR')
       OR b.warning_count<>(SELECT count(*) FROM price_book_validation_issues i
                             WHERE i.price_book_batch_id=b.price_book_batch_id
                               AND i.severity='WARN')
       OR NEW.acknowledged_warning_codes IS DISTINCT FROM
            COALESCE(b.validation_evidence->'warning_codes','[]'::jsonb)
       OR (b.warning_count>0 AND (NEW.review_reason IS NULL OR btrim(NEW.review_reason)=''))
       OR (b.warning_count=0 AND NEW.review_reason IS NOT NULL)
       OR EXISTS (
            SELECT 1 FROM price_book_batches newer
             WHERE newer.vendor_id=b.vendor_id
               AND newer.price_book_batch_id<>b.price_book_batch_id
               AND newer.status IN ('VALIDATED','VERIFIED_FUTURE')
               AND (
                    date_trunc('month',newer.effective_from)>date_trunc('month',b.effective_from)
                    OR (date_trunc('month',newer.effective_from)=date_trunc('month',b.effective_from)
                        AND newer.batch_generation>b.batch_generation)
               )
       )
       OR (b.future_predecessor_batch_id IS NULL AND EXISTS (
            SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
             WHERE o.vendor_id=b.vendor_id AND p.price_state='future'
       ))
       OR (b.future_predecessor_batch_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
             WHERE o.vendor_id=b.vendor_id AND p.price_state='future'
               AND p.source_price_book_batch_id IS DISTINCT FROM b.future_predecessor_batch_id
       ))
       OR (SELECT count(*) FROM price_book_staging_rows s
            WHERE s.price_book_batch_id=b.price_book_batch_id
              AND s.validation_status='VALID')<>b.row_count
       OR (SELECT count(*) FROM price_book_staging_rows s
            WHERE s.price_book_batch_id=b.price_book_batch_id)<>b.row_count
       OR EXISTS (
            SELECT 1 FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status<>'VALID'
       )
       OR EXISTS (
            SELECT s.offer_id
              FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status='VALID'
             GROUP BY s.offer_id
            HAVING count(*) FILTER (WHERE s.level_type='BASE')<>1
       )
       OR EXISTS (
            SELECT 1
              FROM price_book_staging_rows break_row
              JOIN price_book_staging_rows base_row
                ON base_row.price_book_batch_id=break_row.price_book_batch_id
               AND base_row.offer_id=break_row.offer_id
               AND base_row.level_type='BASE'
             WHERE break_row.price_book_batch_id=b.price_book_batch_id
               AND break_row.validation_status='VALID'
               AND break_row.level_type='BREAK'
               AND break_row.unit_price>base_row.unit_price
       )
       OR EXISTS (
            SELECT 1
              FROM price_book_staging_rows deeper
              JOIN price_book_staging_rows shallower
                ON shallower.price_book_batch_id=deeper.price_book_batch_id
               AND shallower.offer_id=deeper.offer_id
               AND shallower.level_type='BREAK'
               AND shallower.break_unit=deeper.break_unit
               AND shallower.break_quantity<deeper.break_quantity
             WHERE deeper.price_book_batch_id=b.price_book_batch_id
               AND deeper.validation_status='VALID'
               AND deeper.level_type='BREAK'
               AND deeper.unit_price>shallower.unit_price
       )
       OR EXISTS (
            SELECT 1
              FROM price_book_staging_rows s
              LEFT JOIN supplier_offers o ON o.offer_id=s.offer_id
              LEFT JOIN vendors v ON v.vendor_id=o.vendor_id
             WHERE s.price_book_batch_id=b.price_book_batch_id
               AND s.validation_status='VALID'
               AND (
                    o.offer_id IS NULL OR o.vendor_id IS DISTINCT FROM b.vendor_id
                    OR NOT o.active OR o.confidence IS DISTINCT FROM 'VERIFIED'
                    OR NOT COALESCE(v.active,FALSE)
                    OR NOT is_procurement_eligible_variant(o.variant_id)
                    OR s.vendor_name IS DISTINCT FROM b.vendor_name
                    OR s.supplier_sku IS DISTINCT FROM o.supplier_sku
                    OR s.canonical_variant_id IS DISTINCT FROM o.variant_id
                    OR upper(s.package_type) IS DISTINCT FROM upper(o.package_type)
                    OR regexp_replace(btrim(s.size_text),'\\s+',' ','g')
                       IS DISTINCT FROM regexp_replace(btrim(o.size_text),'\\s+',' ','g')
                    OR regexp_replace(btrim(s.raw_pack),'\\s+',' ','g')
                       IS DISTINCT FROM regexp_replace(btrim(o.raw_pack),'\\s+',' ','g')
                    OR s.shopify_units_per_case IS DISTINCT FROM o.shopify_units_per_case
                    OR s.qualifying_units_per_case IS DISTINCT FROM o.qualifying_units_per_case
                    OR s.assortment_scope IS DISTINCT FROM o.assortment_scope
                    OR s.assortment_group IS DISTINCT FROM o.assortment_group
                    OR s.assortable IS DISTINCT FROM o.assortable
               )
       ) THEN
        RAISE EXCEPTION 'declared price promotion event does not match locked validation authority';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_validate_declared_price_promotion
BEFORE INSERT ON price_book_promotion_events
FOR EACH ROW WHEN (NEW.replacement_contract IS NOT NULL)
EXECUTE FUNCTION validate_declared_price_promotion_event();

CREATE OR REPLACE FUNCTION validate_declared_price_confirmation_event()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE
    b price_book_batches%ROWTYPE;
    policy supplier_price_schedule_policies%ROWTYPE;
BEGIN
    SELECT * INTO b FROM price_book_batches
     WHERE price_book_batch_id=NEW.price_book_batch_id FOR UPDATE;
    IF b.price_book_batch_id IS NULL
       OR (b.replacement_contract IS NULL)
            IS DISTINCT FROM (NEW.replacement_contract IS NULL) THEN
        RAISE EXCEPTION 'price confirmation contract cannot downgrade or cross modes';
    END IF;
    IF NEW.replacement_contract IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO policy FROM supplier_price_schedule_policies
     WHERE policy_ref=b.schedule_policy_ref;
    PERFORM supplier_price_require_enabled_capability(
        'synthetic_price_confirmation_enabled'
    );
    IF b.replacement_contract IS DISTINCT FROM NEW.replacement_contract
       OR b.schedule_policy_ref IS DISTINCT FROM NEW.schedule_policy_ref
       OR b.price_scope_key IS DISTINCT FROM NEW.price_scope_key
       OR b.declaration_sha256 IS NULL
       OR policy.policy_ref IS NULL
       OR policy.fixture_database_name IS DISTINCT FROM current_database()
       OR NEW.raw_content_sha256 IS DISTINCT FROM b.content_sha256
       OR NEW.declaration_sha256 IS DISTINCT FROM b.declaration_sha256
       OR NEW.policy_sha256 IS DISTINCT FROM policy.policy_sha256
       OR NEW.scope_membership_sha256 IS DISTINCT FROM
            supplier_price_membership_sha256(NEW.price_book_batch_id)
       OR NEW.scope_membership_sha256 IS DISTINCT FROM b.scope_membership_sha256
       OR NEW.transaction_id<>txid_current()
       OR NEW.human_principal_ref IS DISTINCT FROM
            current_setting('procurement.principal_ref',true)
       OR NEW.human_role_ref IS DISTINCT FROM
            current_setting('procurement.authorized_role_ref',true)
       OR NEW.human_authn_context_sha256 IS DISTINCT FROM
            current_setting('procurement.authn_context_sha256',true)
       OR NEW.human_role_ref<>'procurement.price.approve' THEN
        RAISE EXCEPTION 'declared price confirmation authority differs';
    END IF;
    PERFORM persistent_mapping_require_human_context(
        NEW.human_principal_ref,NEW.human_role_ref,
        NEW.human_authn_context_sha256,'PRICE_REPLACEMENT_CONFIRM'
    );
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_validate_declared_price_confirmation
BEFORE INSERT ON price_book_promotion_events
FOR EACH ROW EXECUTE FUNCTION validate_declared_price_confirmation_event();

CREATE OR REPLACE FUNCTION validate_supplier_price_authority_event()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE
    h supplier_price_authority_heads%ROWTYPE;
    b price_book_batches%ROWTYPE;
    policy supplier_price_schedule_policies%ROWTYPE;
    promotion price_book_promotion_events%ROWTYPE;
    expected_count INTEGER;
    resulting_count INTEGER;
    unaffected_sha256 TEXT;
BEGIN
    IF NEW.transaction_id<>txid_current()
       OR NEW.payload_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(NEW.evidence_json) THEN
        RAISE EXCEPTION 'supplier price event transaction differs';
    END IF;
    IF NEW.action='ADOPT_EXISTING_BASELINE' THEN
        IF current_setting('procurement.synthetic_price_fixture_sha256',true)
              IS DISTINCT FROM
              '4ac0137a42e79f560fbab6a4f6073324e553f2ca924d0e3a8c5956f51dd79659'
           OR current_setting('procurement.synthetic_price_fixture_registration',true)
              IS NULL
           OR EXISTS (SELECT 1 FROM meta
                WHERE key='migration:016_synthetic_price_replacement.sql')
           OR NEW.price_book_batch_id IS NOT NULL
           OR NEW.prior_event_id IS NOT NULL
           OR NEW.expected_prior_head_version IS NOT NULL
           OR NEW.apply_idempotency_key IS NOT NULL THEN
            RAISE EXCEPTION 'baseline adoption lacks one-use migration registration';
        END IF;
    ELSE
        PERFORM supplier_price_require_enabled_capability(
            'synthetic_price_apply_enabled'
        );
        PERFORM persistent_mapping_assert_safe_role_topology();
        SELECT * INTO h FROM supplier_price_authority_heads
         WHERE vendor_id=NEW.vendor_id AND price_scope_key=NEW.price_scope_key
         FOR UPDATE;
        SELECT * INTO b FROM price_book_batches
         WHERE price_book_batch_id=NEW.price_book_batch_id FOR UPDATE;
        SELECT * INTO policy FROM supplier_price_schedule_policies
         WHERE policy_ref=NEW.schedule_policy_ref FOR SHARE;
        BEGIN
            expected_count :=
                (NEW.evidence_json->>'expected_current_row_count')::INTEGER;
            resulting_count :=
                (NEW.evidence_json->>'resulting_current_row_count')::INTEGER;
            unaffected_sha256 :=
                NEW.evidence_json->>'unaffected_price_state_sha256';
            SELECT * INTO promotion FROM price_book_promotion_events
             WHERE price_book_promotion_event_id=
                   (NEW.evidence_json->>'price_book_promotion_event_id')::BIGINT
               AND price_book_batch_id=NEW.price_book_batch_id;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'replacement event evidence is malformed';
        END;
        IF h.vendor_id IS NULL
           OR current_setting('transaction_isolation')<>'serializable'
           OR policy.policy_ref IS NULL
           OR policy.fixture_database_name IS DISTINCT FROM current_database()
           OR policy.policy_ref IS DISTINCT FROM b.schedule_policy_ref
           OR policy.policy_sha256 IS DISTINCT FROM
                NEW.evidence_json->>'policy_sha256'
           OR NEW.recorded_at IS DISTINCT FROM policy.application_at
           OR b.price_book_batch_id IS NULL
           OR b.vendor_id IS DISTINCT FROM NEW.vendor_id
           OR b.status<>'VERIFIED_FUTURE'
           OR b.replacement_contract<>'SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
           OR b.price_scope_key IS DISTINCT FROM NEW.price_scope_key
           OR b.content_sha256 IS DISTINCT FROM NEW.raw_content_sha256
           OR b.scope_membership_sha256 IS DISTINCT FROM NEW.scope_membership_sha256
           OR b.scope_membership_sha256 IS DISTINCT FROM
                supplier_price_membership_sha256(b.price_book_batch_id)
           OR b.row_count IS DISTINCT FROM resulting_count
           OR b.effective_from IS DISTINCT FROM
                (policy.application_at AT TIME ZONE policy.policy_timezone)::DATE
           OR NEW.prior_event_id IS DISTINCT FROM h.supplier_price_authority_event_id
           OR NEW.expected_prior_head_version IS DISTINCT FROM h.head_version
           OR NEW.expected_current_scope_sha256 IS DISTINCT FROM h.current_scope_sha256
           OR NEW.expected_current_scope_sha256 IS DISTINCT FROM
                supplier_price_scope_current_sha256(NEW.vendor_id)
           OR NEW.price_book_batch_id IS NULL
           OR NEW.scope_membership_sha256 IS NULL
           OR NEW.raw_content_sha256 IS NULL
           OR NEW.backup_contract<>'BUFFALO_LOCAL_CANDIDATE_BACKUP_V2'
           OR NEW.backup_manifest_ref IS NULL
           OR NEW.backup_manifest_sha256 !~ '^[0-9a-f]{64}$'
           OR NEW.backup_dump_sha256 !~ '^[0-9a-f]{64}$'
           OR NEW.backup_storage_sha256 !~ '^[0-9a-f]{64}$'
           OR NEW.backup_prechange_scope_sha256 IS DISTINCT FROM
                NEW.expected_current_scope_sha256
           OR NEW.apply_idempotency_key IS NULL
           OR promotion.price_book_promotion_event_id IS NULL
           OR promotion.replacement_contract IS DISTINCT FROM b.replacement_contract
           OR promotion.schedule_policy_ref IS DISTINCT FROM b.schedule_policy_ref
           OR promotion.scope_membership_sha256 IS DISTINCT FROM b.scope_membership_sha256
           OR promotion.raw_content_sha256 IS DISTINCT FROM b.content_sha256
           OR promotion.declaration_sha256 IS DISTINCT FROM b.declaration_sha256
           OR promotion.policy_sha256 IS DISTINCT FROM policy.policy_sha256
           OR promotion.confirmation_payload_sha256 IS DISTINCT FROM
                NEW.evidence_json->>'confirmation_payload_sha256'
           OR expected_count<1 OR resulting_count<1
           OR expected_count<>(SELECT count(*) FROM prices p
                JOIN supplier_offers o USING(offer_id)
               WHERE o.vendor_id=NEW.vendor_id AND p.price_state='current')
           OR resulting_count<>(SELECT count(*) FROM prices p
               WHERE p.source_price_book_batch_id=NEW.price_book_batch_id
                 AND p.price_state='future' AND p.verified)
           OR resulting_count<>(SELECT count(*) FROM price_book_scope_memberships m
               WHERE m.price_book_batch_id=NEW.price_book_batch_id)
           OR EXISTS (SELECT 1 FROM prices p
               WHERE p.source_price_book_batch_id=NEW.price_book_batch_id
                 AND (p.price_state<>'future' OR NOT p.verified))
           OR NEW.resulting_current_scope_sha256 IS DISTINCT FROM
                supplier_price_scope_batch_as_current_sha256(
                    NEW.vendor_id,NEW.price_book_batch_id
                )
           OR unaffected_sha256 IS DISTINCT FROM
                supplier_price_unaffected_state_sha256(NEW.vendor_id)
           OR NEW.human_role_ref<>'procurement.price.approve'
           OR NEW.human_principal_ref IS DISTINCT FROM
                current_setting('procurement.principal_ref',true)
           OR NEW.human_authn_context_sha256 IS DISTINCT FROM
                current_setting('procurement.authn_context_sha256',true)
           OR NEW.payload_sha256 IS DISTINCT FROM
                persistent_mapping_json_sha256(NEW.evidence_json)
           OR NEW.evidence_json->>'contract'<>'BUFFALO_SYNTHETIC_PRICE_APPLY_EVENT_V1'
           OR NEW.evidence_json->>'supplier_price_authority_event_id'
                IS DISTINCT FROM NEW.supplier_price_authority_event_id::TEXT
           OR NEW.evidence_json->>'price_book_batch_id'
                IS DISTINCT FROM NEW.price_book_batch_id::TEXT
           OR NEW.evidence_json->>'vendor_id' IS DISTINCT FROM NEW.vendor_id::TEXT
           OR NEW.evidence_json->>'price_scope_key' IS DISTINCT FROM NEW.price_scope_key
           OR NEW.evidence_json->>'policy_ref' IS DISTINCT FROM NEW.schedule_policy_ref
           OR NEW.evidence_json->>'prior_event_id' IS DISTINCT FROM NEW.prior_event_id::TEXT
           OR (NEW.evidence_json->>'expected_prior_head_version')::BIGINT
                IS DISTINCT FROM NEW.expected_prior_head_version
           OR NEW.evidence_json->>'expected_current_scope_sha256'
                IS DISTINCT FROM NEW.expected_current_scope_sha256
           OR NEW.evidence_json->>'resulting_current_scope_sha256'
                IS DISTINCT FROM NEW.resulting_current_scope_sha256
           OR NEW.evidence_json->>'scope_membership_sha256'
                IS DISTINCT FROM NEW.scope_membership_sha256
           OR NEW.evidence_json->>'raw_content_sha256'
                IS DISTINCT FROM NEW.raw_content_sha256
           OR NEW.evidence_json->>'backup_contract' IS DISTINCT FROM NEW.backup_contract
           OR NEW.evidence_json->>'backup_manifest_ref'
                IS DISTINCT FROM NEW.backup_manifest_ref
           OR NEW.evidence_json->>'backup_manifest_sha256'
                IS DISTINCT FROM NEW.backup_manifest_sha256
           OR NEW.evidence_json->>'backup_dump_sha256'
                IS DISTINCT FROM NEW.backup_dump_sha256
           OR NEW.evidence_json->>'backup_storage_sha256'
                IS DISTINCT FROM NEW.backup_storage_sha256
           OR NEW.evidence_json->>'backup_prechange_scope_sha256'
                IS DISTINCT FROM NEW.backup_prechange_scope_sha256
           OR NEW.evidence_json->>'apply_idempotency_key'
                IS DISTINCT FROM NEW.apply_idempotency_key
           OR NEW.evidence_json->>'principal_ref'
                IS DISTINCT FROM NEW.human_principal_ref
           OR NEW.evidence_json->>'role_ref' IS DISTINCT FROM NEW.human_role_ref
           OR NEW.evidence_json->>'human_authn_context_sha256'
                IS DISTINCT FROM NEW.human_authn_context_sha256
           OR NEW.evidence_json->>'preview_sha256' IS DISTINCT FROM NEW.preview_sha256
           OR NEW.evidence_json->>'confirmation_sha256'
                IS DISTINCT FROM NEW.confirmation_sha256
           OR NEW.evidence_json->'commercial_authority'<>'false'::jsonb
           OR NEW.evidence_json->'real_price_approval'<>'false'::jsonb THEN
            RAISE EXCEPTION 'replacement event does not match current authority head';
        END IF;
        PERFORM persistent_mapping_require_human_context(
            NEW.human_principal_ref,NEW.human_role_ref,
            NEW.human_authn_context_sha256,'PRICE_REPLACEMENT_APPLY'
        );
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_validate_supplier_price_authority_event
BEFORE INSERT ON supplier_price_authority_events
FOR EACH ROW EXECUTE FUNCTION validate_supplier_price_authority_event();

CREATE OR REPLACE FUNCTION validate_supplier_price_authority_head()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE
    e supplier_price_authority_events%ROWTYPE;
    b price_book_batches%ROWTYPE;
BEGIN
    SELECT * INTO e FROM supplier_price_authority_events
     WHERE supplier_price_authority_event_id=NEW.supplier_price_authority_event_id;
    IF e.price_book_batch_id IS NOT NULL THEN
        SELECT * INTO b FROM price_book_batches
         WHERE price_book_batch_id=e.price_book_batch_id;
    END IF;
    IF e.vendor_id IS DISTINCT FROM NEW.vendor_id
       OR e.price_scope_key IS DISTINCT FROM NEW.price_scope_key
       OR e.transaction_id<>txid_current()
       OR NEW.updated_txid<>txid_current()
       OR NEW.updated_at IS DISTINCT FROM e.recorded_at
       OR NEW.current_scope_sha256 IS DISTINCT FROM e.resulting_current_scope_sha256
       OR (TG_OP='INSERT' AND (e.action<>'ADOPT_EXISTING_BASELINE'
             OR NEW.head_version<>1 OR NEW.active_price_book_batch_id IS NOT NULL))
       OR (TG_OP='UPDATE' AND (e.action<>'APPLY_REPLACEMENT'
             OR NEW.vendor_id IS DISTINCT FROM OLD.vendor_id
             OR NEW.price_scope_key IS DISTINCT FROM OLD.price_scope_key
             OR OLD.supplier_price_authority_event_id IS DISTINCT FROM e.prior_event_id
             OR OLD.head_version IS DISTINCT FROM e.expected_prior_head_version
             OR OLD.current_scope_sha256 IS DISTINCT FROM e.expected_current_scope_sha256
             OR NEW.head_version<>OLD.head_version+1
             OR NEW.head_version<>e.expected_prior_head_version+1
             OR NEW.active_price_book_batch_id IS DISTINCT FROM e.price_book_batch_id
             OR b.status<>'APPLIED_CURRENT')) THEN
        RAISE EXCEPTION 'supplier price authority head is not event-bound';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER trg_validate_supplier_price_authority_head
BEFORE INSERT OR UPDATE ON supplier_price_authority_heads
FOR EACH ROW EXECUTE FUNCTION validate_supplier_price_authority_head();

CREATE TRIGGER trg_protect_supplier_price_authority_head_delete
BEFORE DELETE ON supplier_price_authority_heads
FOR EACH ROW EXECUTE FUNCTION prevent_supplier_price_authority_mutation();

CREATE OR REPLACE FUNCTION assert_supplier_price_authority_commit()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
BEGIN
    IF NEW.payload_sha256 IS DISTINCT FROM
            persistent_mapping_json_sha256(NEW.evidence_json)
       OR NOT EXISTS (
        SELECT 1 FROM supplier_price_authority_heads h
         WHERE h.vendor_id=NEW.vendor_id
           AND h.price_scope_key=NEW.price_scope_key
           AND h.supplier_price_authority_event_id=NEW.supplier_price_authority_event_id
           AND h.current_scope_sha256=NEW.resulting_current_scope_sha256
    ) OR NEW.resulting_current_scope_sha256 IS DISTINCT FROM
            supplier_price_scope_current_sha256(NEW.vendor_id)
       OR (NEW.action='APPLY_REPLACEMENT' AND (
            NOT EXISTS (SELECT 1 FROM price_book_batches b
              WHERE b.price_book_batch_id=NEW.price_book_batch_id
                AND b.status='APPLIED_CURRENT'
                AND b.scope_membership_sha256=NEW.scope_membership_sha256
                AND b.content_sha256=NEW.raw_content_sha256
                AND b.row_count=(NEW.evidence_json->>'resulting_current_row_count')::INTEGER)
            OR NOT EXISTS (SELECT 1 FROM price_book_promotion_events pe
                 WHERE pe.price_book_promotion_event_id=
                       (NEW.evidence_json->>'price_book_promotion_event_id')::BIGINT
                   AND pe.price_book_batch_id=NEW.price_book_batch_id
                   AND pe.scope_membership_sha256=NEW.scope_membership_sha256
                   AND pe.raw_content_sha256=NEW.raw_content_sha256)
            OR EXISTS (SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
                 WHERE o.vendor_id=NEW.vendor_id AND p.price_state='future')
            OR EXISTS (SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
                 WHERE o.vendor_id=NEW.vendor_id AND p.price_state='current'
                   AND p.source_price_book_batch_id IS DISTINCT FROM NEW.price_book_batch_id)
            OR (SELECT count(*) FROM prices p
                 WHERE p.source_price_book_batch_id=NEW.price_book_batch_id
                   AND p.price_state='current')<>
                   (NEW.evidence_json->>'resulting_current_row_count')::INTEGER
            OR (SELECT count(*) FROM price_book_scope_memberships m
                 WHERE m.price_book_batch_id=NEW.price_book_batch_id)<>
                   (NEW.evidence_json->>'resulting_current_row_count')::INTEGER
            OR NEW.resulting_current_scope_sha256 IS DISTINCT FROM
                 supplier_price_scope_batch_as_current_sha256(
                     NEW.vendor_id,NEW.price_book_batch_id
                 )
            OR supplier_price_unaffected_state_sha256(NEW.vendor_id)
                 IS DISTINCT FROM
                 NEW.evidence_json->>'unaffected_price_state_sha256'
       )) THEN
        RAISE EXCEPTION 'supplier price authority event lacks completed exact state';
    END IF;
    RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER trg_assert_supplier_price_authority_commit
AFTER INSERT ON supplier_price_authority_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION assert_supplier_price_authority_commit();

ALTER TABLE price_book_batches DROP CONSTRAINT price_book_batches_status_check;
ALTER TABLE price_book_batches ADD CONSTRAINT price_book_batches_status_check CHECK (
    status IN ('STAGING','INVALID','VALIDATED','VERIFIED_FUTURE',
               'APPLIED_CURRENT','REJECTED','SUPERSEDED')
);
ALTER TABLE price_book_batches DROP CONSTRAINT ck_price_book_batch_state;
ALTER TABLE price_book_batches ADD CONSTRAINT ck_price_book_batch_state CHECK (
    (status='STAGING' AND valid_row_count=0 AND error_count=0 AND warning_count=0
      AND expected_offer_count=0 AND covered_offer_count=0 AND missing_offer_count=0
      AND validation_fingerprint IS NULL AND validation_evidence='{}'::jsonb
      AND promoted_by IS NULL AND promoted_at IS NULL
      AND disposition_by IS NULL AND disposition_reason IS NULL AND disposition_at IS NULL)
    OR (status='INVALID' AND error_count>0 AND validation_fingerprint IS NOT NULL
      AND promoted_by IS NULL AND promoted_at IS NULL
      AND disposition_by IS NULL AND disposition_reason IS NULL AND disposition_at IS NULL)
    OR (status='VALIDATED' AND error_count=0 AND missing_offer_count=0
      AND valid_row_count=row_count AND validation_fingerprint IS NOT NULL
      AND promoted_by IS NULL AND promoted_at IS NULL
      AND disposition_by IS NULL AND disposition_reason IS NULL AND disposition_at IS NULL)
    OR (status IN ('VERIFIED_FUTURE','APPLIED_CURRENT')
      AND error_count=0 AND missing_offer_count=0 AND valid_row_count=row_count
      AND validation_fingerprint IS NOT NULL
      AND promoted_by IS NOT NULL AND btrim(promoted_by)<>'' AND promoted_at IS NOT NULL
      AND disposition_by IS NULL AND disposition_reason IS NULL AND disposition_at IS NULL)
    OR (status IN ('REJECTED','SUPERSEDED') AND validation_fingerprint IS NOT NULL
      AND disposition_by IS NOT NULL AND btrim(disposition_by)<>''
      AND disposition_reason IS NOT NULL AND btrim(disposition_reason)<>''
      AND disposition_at IS NOT NULL)
);

CREATE OR REPLACE FUNCTION guard_price_book_batch_update()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE event_ok BOOLEAN := FALSE;
BEGIN
    IF ROW(NEW.batch_generation,NEW.vendor_id,NEW.vendor_name,NEW.batch_ref,
           NEW.target_price_state,NEW.effective_from,NEW.effective_through,
           NEW.content_sha256,NEW.raw_storage_key,NEW.future_predecessor_sha256,
           NEW.future_predecessor_batch_id,NEW.row_count,NEW.staged_by,NEW.staged_at,
           NEW.replacement_contract,NEW.schedule_policy_ref,NEW.price_scope_key,
           NEW.source_period_label,NEW.source_valid_from,NEW.source_valid_through,
           NEW.source_validity_basis,NEW.supplier_verified_at,
           NEW.operational_effective_from,NEW.operational_effective_through,
           NEW.declaration_sha256)
       IS DISTINCT FROM
       ROW(OLD.batch_generation,OLD.vendor_id,OLD.vendor_name,OLD.batch_ref,
           OLD.target_price_state,OLD.effective_from,OLD.effective_through,
           OLD.content_sha256,OLD.raw_storage_key,OLD.future_predecessor_sha256,
           OLD.future_predecessor_batch_id,OLD.row_count,OLD.staged_by,OLD.staged_at,
           OLD.replacement_contract,OLD.schedule_policy_ref,OLD.price_scope_key,
           OLD.source_period_label,OLD.source_valid_from,OLD.source_valid_through,
           OLD.source_validity_basis,OLD.supplier_verified_at,
           OLD.operational_effective_from,OLD.operational_effective_through,
           OLD.declaration_sha256) THEN
        RAISE EXCEPTION 'price-book batch identity and raw provenance are immutable';
    END IF;
    IF OLD.status<>'STAGING' AND ROW(
        NEW.valid_row_count,NEW.error_count,NEW.warning_count,
        NEW.expected_offer_count,NEW.covered_offer_count,NEW.missing_offer_count,
        NEW.validation_fingerprint,NEW.validation_evidence)
      IS DISTINCT FROM ROW(
        OLD.valid_row_count,OLD.error_count,OLD.warning_count,
        OLD.expected_offer_count,OLD.covered_offer_count,OLD.missing_offer_count,
        OLD.validation_fingerprint,OLD.validation_evidence) THEN
        RAISE EXCEPTION 'validated price-book controls are immutable';
    END IF;
    IF OLD.status IN ('REJECTED','SUPERSEDED','APPLIED_CURRENT')
       AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'completed price-book batch is immutable';
    END IF;
    IF OLD.status='VALIDATED' AND NEW.status='VALIDATED'
       AND OLD.replacement_contract='SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
       AND OLD.scope_membership_sha256 IS NULL
       AND NEW.scope_membership_sha256 IS NOT NULL
       AND (to_jsonb(NEW)-'scope_membership_sha256') =
           (to_jsonb(OLD)-'scope_membership_sha256') THEN
        PERFORM supplier_price_require_enabled_capability(
            'synthetic_price_confirmation_enabled'
        );
        PERFORM persistent_mapping_require_human_context(
            current_setting('procurement.principal_ref',true),
            current_setting('procurement.authorized_role_ref',true),
            current_setting('procurement.authn_context_sha256',true),
            'PRICE_REPLACEMENT_CONFIRM'
        );
        IF NEW.scope_membership_sha256 IS DISTINCT FROM
             supplier_price_membership_sha256(NEW.price_book_batch_id)
           OR NOT EXISTS (
               SELECT 1 FROM price_book_scope_memberships m
                WHERE m.price_book_batch_id=NEW.price_book_batch_id
           ) THEN
            RAISE EXCEPTION 'declared scope membership digest differs';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status='VERIFIED_FUTURE' AND NEW.status='APPLIED_CURRENT' THEN
        event_ok := EXISTS (
            SELECT 1 FROM supplier_price_authority_events e
            JOIN supplier_price_authority_heads h
              ON h.vendor_id=e.vendor_id AND h.price_scope_key=e.price_scope_key
            JOIN price_book_promotion_events pe
              ON pe.price_book_batch_id=OLD.price_book_batch_id
             AND pe.replacement_contract=OLD.replacement_contract
             WHERE e.price_book_batch_id=OLD.price_book_batch_id
               AND e.action='APPLY_REPLACEMENT'
               AND e.transaction_id=txid_current()
               AND h.supplier_price_authority_event_id=e.prior_event_id
               AND h.head_version=e.expected_prior_head_version
               AND h.current_scope_sha256=e.expected_current_scope_sha256
               AND e.scope_membership_sha256=OLD.scope_membership_sha256
               AND e.raw_content_sha256=OLD.content_sha256
               AND pe.price_book_promotion_event_id=
                   (e.evidence_json->>'price_book_promotion_event_id')::BIGINT
        );
        IF OLD.replacement_contract<>'SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
          OR (to_jsonb(NEW)-'status')<>(to_jsonb(OLD)-'status')
          OR NOT event_ok OR EXISTS (
            SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
             WHERE o.vendor_id=OLD.vendor_id AND p.price_state='future'
        ) OR EXISTS (
            SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
             WHERE o.vendor_id=OLD.vendor_id AND p.price_state='current'
               AND p.source_price_book_batch_id IS DISTINCT FROM OLD.price_book_batch_id
        ) OR (SELECT count(*) FROM prices p
               WHERE p.source_price_book_batch_id=OLD.price_book_batch_id
                 AND p.price_state='current')<>OLD.row_count
          OR (SELECT count(*) FROM price_book_scope_memberships m
               WHERE m.price_book_batch_id=OLD.price_book_batch_id)<>OLD.row_count
          OR EXISTS (
            SELECT 1 FROM prices p
            LEFT JOIN price_book_scope_memberships m
              ON m.price_book_batch_id=p.source_price_book_batch_id
             AND m.source_row_number=p.source_price_book_row_number
             AND m.offer_id=p.offer_id
             AND m.level_type=p.level_type
             AND m.break_qty IS NOT DISTINCT FROM p.break_qty
             AND m.break_unit IS NOT DISTINCT FROM p.break_unit
           WHERE p.source_price_book_batch_id=OLD.price_book_batch_id
             AND p.price_state='current' AND m.price_book_batch_id IS NULL
        ) THEN
            RAISE EXCEPTION 'APPLIED CURRENT batch lacks exact replacement event/state';
        END IF;
        RETURN NEW;
    END IF;
    IF ROW(NEW.promoted_by,NEW.promoted_at) IS DISTINCT FROM
       ROW(OLD.promoted_by,OLD.promoted_at) THEN
        IF NOT (OLD.status='VALIDATED' AND NEW.status='VERIFIED_FUTURE'
          AND EXISTS (SELECT 1 FROM price_book_promotion_events e
             WHERE e.price_book_batch_id=OLD.price_book_batch_id
               AND e.validation_fingerprint=OLD.validation_fingerprint
               AND e.transaction_id=txid_current()
               AND e.recorded_by=NEW.promoted_by AND e.recorded_at=NEW.promoted_at)) THEN
            RAISE EXCEPTION 'price-book promotion attribution is immutable and event-bound';
        END IF;
    END IF;
    IF ROW(NEW.disposition_by,NEW.disposition_reason,NEW.disposition_at)
       IS DISTINCT FROM ROW(OLD.disposition_by,OLD.disposition_reason,OLD.disposition_at) THEN
        IF NOT (NEW.status IN ('REJECTED','SUPERSEDED') AND EXISTS (
            SELECT 1 FROM price_book_disposition_events e
             WHERE e.price_book_batch_id=OLD.price_book_batch_id
               AND e.prior_status=OLD.status AND e.new_status=NEW.status
               AND e.transaction_id=txid_current()
               AND e.recorded_by=NEW.disposition_by
               AND e.reason=NEW.disposition_reason
               AND e.recorded_at=NEW.disposition_at)) THEN
            RAISE EXCEPTION 'price-book disposition attribution is immutable and event-bound';
        END IF;
    END IF;
    IF OLD.status='STAGING' AND NEW.status NOT IN ('INVALID','VALIDATED') THEN
        RAISE EXCEPTION 'STAGING batch must transition through validation';
    ELSIF OLD.status='INVALID' AND NEW.status<>'REJECTED' THEN
        RAISE EXCEPTION 'INVALID batch may only be rejected';
    ELSIF OLD.status='VALIDATED'
       AND NEW.status NOT IN ('VERIFIED_FUTURE','REJECTED','SUPERSEDED') THEN
        RAISE EXCEPTION 'VALIDATED batch requires promotion or disposition';
    ELSIF OLD.status='VERIFIED_FUTURE'
       AND NEW.status NOT IN ('SUPERSEDED','APPLIED_CURRENT') THEN
        RAISE EXCEPTION 'VERIFIED FUTURE batch requires supersession or exact apply';
    END IF;
    IF OLD.status='VALIDATED' AND NEW.status='VERIFIED_FUTURE' THEN
        event_ok := EXISTS (SELECT 1 FROM price_book_promotion_events e
          WHERE e.price_book_batch_id=OLD.price_book_batch_id
            AND e.validation_fingerprint=OLD.validation_fingerprint
            AND e.transaction_id=txid_current()
            AND e.recorded_by=NEW.promoted_by AND e.recorded_at=NEW.promoted_at);
        IF NOT event_ok
          OR EXISTS (SELECT 1 FROM price_book_staging_rows s
              WHERE s.price_book_batch_id=OLD.price_book_batch_id)
          OR (SELECT count(*) FROM prices p
               WHERE p.source_price_book_batch_id=OLD.price_book_batch_id
                 AND p.price_state='future' AND p.verified)<>OLD.row_count
          OR EXISTS (SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
               WHERE o.vendor_id=OLD.vendor_id AND p.price_state='future'
                 AND p.source_price_book_batch_id IS DISTINCT FROM OLD.price_book_batch_id) THEN
            RAISE EXCEPTION 'price-book promotion does not own complete vendor FUTURE set';
        END IF;
    ELSIF NEW.status IN ('REJECTED','SUPERSEDED') THEN
        event_ok := EXISTS (SELECT 1 FROM price_book_disposition_events e
          WHERE e.price_book_batch_id=OLD.price_book_batch_id
            AND e.prior_status=OLD.status AND e.new_status=NEW.status
            AND e.transaction_id=txid_current());
        IF NOT event_ok OR EXISTS (SELECT 1 FROM price_book_staging_rows s
             WHERE s.price_book_batch_id=OLD.price_book_batch_id) THEN
            RAISE EXCEPTION 'price-book disposition lacks authority or typed-row purge';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION validate_price_book_price_provenance()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE b price_book_batches%ROWTYPE; offer_vendor UUID; seed_offer BOOLEAN:=FALSE;
BEGIN
    IF TG_OP='UPDATE' THEN
        IF OLD.source_price_book_batch_id IS NOT NULL
           AND NEW.source_price_book_batch_id IS NOT DISTINCT FROM OLD.source_price_book_batch_id
           AND OLD.price_state='future' AND NEW.price_state='current'
           AND (to_jsonb(NEW)-'price_state')=(to_jsonb(OLD)-'price_state')
           AND EXISTS (
                SELECT 1 FROM supplier_price_authority_events e
                JOIN supplier_price_authority_heads h
                  ON h.vendor_id=e.vendor_id AND h.price_scope_key=e.price_scope_key
                JOIN price_book_batches pb
                  ON pb.price_book_batch_id=e.price_book_batch_id
                JOIN price_book_scope_memberships m
                  ON m.price_book_batch_id=OLD.source_price_book_batch_id
                 AND m.source_row_number=OLD.source_price_book_row_number
                 AND m.offer_id=OLD.offer_id
                 AND m.level_type=OLD.level_type
                 AND m.break_qty IS NOT DISTINCT FROM OLD.break_qty
                 AND m.break_unit IS NOT DISTINCT FROM OLD.break_unit
               WHERE e.action='APPLY_REPLACEMENT'
                 AND e.price_book_batch_id=OLD.source_price_book_batch_id
                 AND e.transaction_id=txid_current()
                 AND h.supplier_price_authority_event_id=e.prior_event_id
                 AND h.head_version=e.expected_prior_head_version
                 AND h.current_scope_sha256=e.expected_current_scope_sha256
                 AND pb.status='VERIFIED_FUTURE'
                 AND pb.scope_membership_sha256=e.scope_membership_sha256
                 AND pb.content_sha256=e.raw_content_sha256
           ) THEN
            RETURN NEW;
        END IF;
        IF OLD.source_price_book_batch_id IS NULL THEN
            RAISE EXCEPTION 'grandfathered unprovenanced price evidence is immutable';
        END IF;
        RAISE EXCEPTION 'batch price provenance cannot be attached or rewritten';
    END IF;
    IF NEW.source_price_book_batch_id IS NULL THEN
        SELECT notes LIKE 'v0.1 seed migration:%' INTO seed_offer
          FROM supplier_offers WHERE offer_id=NEW.offer_id;
        IF seed_offer AND NEW.price_state='current'
          AND NEW.effective_month='2026-08-01'::date
          AND EXISTS (SELECT 1 FROM legacy_price_seed_events e
                       WHERE e.transaction_id=txid_current()) THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'new unprovenanced operational prices are not authorized';
    END IF;
    SELECT * INTO b FROM price_book_batches
     WHERE price_book_batch_id=NEW.source_price_book_batch_id;
    SELECT vendor_id INTO offer_vendor FROM supplier_offers WHERE offer_id=NEW.offer_id;
    IF b.vendor_id IS NULL OR offer_vendor IS DISTINCT FROM b.vendor_id
       OR NEW.verified IS NOT TRUE OR NEW.price_state<>'future'
       OR b.status<>'VALIDATED'
       OR NOT EXISTS (SELECT 1 FROM price_book_promotion_events e
            WHERE e.price_book_batch_id=b.price_book_batch_id
              AND e.transaction_id=txid_current())
       OR NOT EXISTS (
          SELECT 1 FROM price_book_staging_rows s
           WHERE s.price_book_batch_id=NEW.source_price_book_batch_id
             AND s.source_row_number=NEW.source_price_book_row_number
             AND s.validation_status='VALID' AND s.offer_id=NEW.offer_id
             AND s.level_type IS NOT DISTINCT FROM NEW.level_type
             AND s.break_quantity IS NOT DISTINCT FROM NEW.break_qty
             AND s.break_unit IS NOT DISTINCT FROM NEW.break_unit
             AND s.case_price IS NOT DISTINCT FROM NEW.case_price
             AND s.unit_price IS NOT DISTINCT FROM NEW.unit_price
             AND s.source_file IS NOT DISTINCT FROM NEW.source_file
             AND s.source_page IS NOT DISTINCT FROM NEW.source_page
             AND s.extraction_confidence IS NOT DISTINCT FROM NEW.extraction_confidence
             AND s.review_note IS NOT DISTINCT FROM NEW.notes
       ) THEN
        RAISE EXCEPTION 'promoted price differs from validated staging evidence';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION protect_promoted_price()
RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,qa_mapping_test
AS $$
DECLARE old_vendor UUID;
BEGIN
    SELECT vendor_id INTO old_vendor FROM supplier_offers WHERE offer_id=OLD.offer_id;
    IF TG_OP='DELETE' AND OLD.price_state='current'
       AND EXISTS (
           SELECT 1 FROM supplier_price_authority_events e
           JOIN supplier_price_authority_heads h
             ON h.vendor_id=e.vendor_id AND h.price_scope_key=e.price_scope_key
          WHERE e.action='APPLY_REPLACEMENT'
            AND e.transaction_id=txid_current()
            AND e.vendor_id=old_vendor
            AND h.supplier_price_authority_event_id=e.prior_event_id
            AND h.head_version=e.expected_prior_head_version
            AND h.current_scope_sha256=e.expected_current_scope_sha256
            AND OLD.source_price_book_batch_id
                IS NOT DISTINCT FROM h.active_price_book_batch_id
       ) THEN
        RETURN OLD;
    END IF;
    IF OLD.source_price_book_batch_id IS NULL THEN
        IF TG_OP='DELETE' AND OLD.price_state='current'
           AND OLD.effective_month='2026-08-01'::date
           AND EXISTS (SELECT 1 FROM supplier_offers o WHERE o.offer_id=OLD.offer_id
                         AND o.notes LIKE 'v0.1 seed migration:%')
           AND EXISTS (SELECT 1 FROM legacy_price_seed_events e
                         WHERE e.transaction_id=txid_current()) THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'grandfathered unprovenanced price evidence is immutable';
    END IF;
    IF TG_OP='UPDATE' AND OLD.price_state='future' AND NEW.price_state='current'
       AND (to_jsonb(NEW)-'price_state')=(to_jsonb(OLD)-'price_state')
       AND EXISTS (
           SELECT 1 FROM supplier_price_authority_events e
           JOIN supplier_price_authority_heads h
             ON h.vendor_id=e.vendor_id AND h.price_scope_key=e.price_scope_key
           JOIN price_book_batches b
             ON b.price_book_batch_id=e.price_book_batch_id
           JOIN price_book_scope_memberships m
             ON m.price_book_batch_id=OLD.source_price_book_batch_id
            AND m.source_row_number=OLD.source_price_book_row_number
            AND m.offer_id=OLD.offer_id
            AND m.level_type=OLD.level_type
            AND m.break_qty IS NOT DISTINCT FROM OLD.break_qty
            AND m.break_unit IS NOT DISTINCT FROM OLD.break_unit
          WHERE e.action='APPLY_REPLACEMENT'
            AND e.transaction_id=txid_current()
            AND e.price_book_batch_id=OLD.source_price_book_batch_id
            AND h.supplier_price_authority_event_id=e.prior_event_id
            AND h.head_version=e.expected_prior_head_version
            AND h.current_scope_sha256=e.expected_current_scope_sha256
            AND b.status='VERIFIED_FUTURE'
            AND b.scope_membership_sha256=e.scope_membership_sha256
            AND b.content_sha256=e.raw_content_sha256
       ) THEN RETURN NEW; END IF;
    IF TG_OP='DELETE' AND OLD.price_state='future' AND EXISTS (
        SELECT 1 FROM price_book_promotion_events e
        JOIN price_book_batches b USING(price_book_batch_id)
         WHERE e.transaction_id=txid_current() AND b.vendor_id=old_vendor
           AND b.target_price_state='future'
           AND b.future_predecessor_batch_id=OLD.source_price_book_batch_id
    ) THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'promoted price economics are immutable outside authorized replacement';
END
$$;

CREATE OR REPLACE VIEW v_verified_current_prices AS
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,
       o.shopify_units_per_case,o.qualifying_units_per_case,
       o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
JOIN vendors v ON v.vendor_id=o.vendor_id
WHERE p.price_state='current' AND p.verified
  AND p.source_price_book_batch_id IS NULL
  AND o.active AND o.confidence='VERIFIED' AND v.active
  AND is_procurement_eligible_variant(o.variant_id)
UNION ALL
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,
       o.shopify_units_per_case,o.qualifying_units_per_case,
       o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
JOIN vendors v ON v.vendor_id=o.vendor_id
JOIN price_book_batches b
  ON b.price_book_batch_id=p.source_price_book_batch_id
 AND b.vendor_id=o.vendor_id AND b.status='APPLIED_CURRENT'
 AND b.replacement_contract='SYNTHETIC_COMPLETE_VENDOR_MONTHLY_V1'
JOIN price_book_scope_memberships m
  ON m.price_book_batch_id=p.source_price_book_batch_id
 AND m.source_row_number=p.source_price_book_row_number
 AND m.offer_id=p.offer_id AND m.variant_id=o.variant_id
 AND m.level_type=p.level_type
 AND m.break_qty IS NOT DISTINCT FROM p.break_qty
 AND m.break_unit IS NOT DISTINCT FROM p.break_unit
JOIN supplier_price_schedule_policies policy
  ON policy.policy_ref=b.schedule_policy_ref
 AND policy.vendor_id=o.vendor_id
 AND policy.fixture_database_name=current_database()
JOIN supplier_price_authority_heads h
  ON h.vendor_id=o.vendor_id AND h.price_scope_key=b.price_scope_key
 AND h.active_price_book_batch_id=b.price_book_batch_id
JOIN supplier_price_authority_events e
  ON e.supplier_price_authority_event_id=h.supplier_price_authority_event_id
 AND e.action='APPLY_REPLACEMENT'
 AND e.price_book_batch_id=b.price_book_batch_id
 AND e.schedule_policy_ref=b.schedule_policy_ref
 AND e.scope_membership_sha256=b.scope_membership_sha256
 AND e.raw_content_sha256=b.content_sha256
 AND e.resulting_current_scope_sha256=h.current_scope_sha256
JOIN price_book_promotion_events pe
  ON pe.price_book_promotion_event_id=
       (e.evidence_json->>'price_book_promotion_event_id')::BIGINT
 AND pe.price_book_batch_id=b.price_book_batch_id
 AND pe.scope_membership_sha256=b.scope_membership_sha256
 AND pe.raw_content_sha256=b.content_sha256
 AND pe.policy_sha256=policy.policy_sha256
WHERE p.price_state='current' AND p.verified
  AND o.active AND o.confidence='VERIFIED' AND v.active
  AND is_procurement_eligible_variant(o.variant_id)
  AND b.scope_membership_sha256=supplier_price_membership_sha256(b.price_book_batch_id)
  AND h.current_scope_sha256=supplier_price_scope_current_sha256(o.vendor_id)
  AND e.payload_sha256=persistent_mapping_json_sha256(e.evidence_json);

CREATE OR REPLACE VIEW v_current_prices AS
SELECT p.price_id,p.offer_id,p.price_state,p.effective_month,p.level_type,
       p.break_qty,p.break_unit,p.case_price,p.unit_price,p.source_file,
       p.source_page,p.extraction_confidence,p.verified,p.notes,p.created_at,
       p.variant_id,p.vendor_id,p.supplier_sku,p.shopify_units_per_case,
       p.qualifying_units_per_case,p.assortment_scope,p.assortment_group,p.assortable
FROM v_verified_current_prices p
;

CREATE OR REPLACE FUNCTION assert_synthetic_price_replacement_contract()
RETURNS VOID LANGUAGE plpgsql SECURITY INVOKER
SET search_path=pg_catalog,qa_mapping_test
AS $$
BEGIN
    IF (SELECT value FROM meta WHERE key='monday_price_book_contract')
         IS DISTINCT FROM 'v2-future-only'
       OR to_regclass('qa_mapping_test.supplier_price_schedule_policies') IS NULL
       OR to_regclass('qa_mapping_test.price_book_scope_memberships') IS NULL
       OR to_regclass('qa_mapping_test.supplier_price_authority_events') IS NULL
       OR to_regclass('qa_mapping_test.supplier_price_authority_heads') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
             WHERE c.relnamespace='qa_mapping_test'::regnamespace
               AND t.tgname='trg_assert_supplier_price_authority_commit'
               AND t.tgdeferrable AND t.tginitdeferred AND NOT t.tgisinternal) THEN
        RAISE EXCEPTION 'synthetic price replacement installed contract differs';
    END IF;
END
$$;

-- Optional one-use synthetic baseline adoption. Generic installs remain
-- headless. The runner supplies canonical registered JSON only for a fresh
-- owned demo fixture and publishes the migration marker after this succeeds.
DO $adopt$
DECLARE
    registration_text TEXT := current_setting(
        'procurement.synthetic_price_fixture_registration',true
    );
    registration_sha TEXT := current_setting(
        'procurement.synthetic_price_fixture_sha256',true
    );
    registration JSONB;
    policy JSONB;
    actual_rows JSONB;
    policy_hash TEXT;
    current_hash TEXT;
    event_id UUID;
    adoption_evidence JSONB;
BEGIN
    IF registration_text IS NULL OR registration_text='' THEN RETURN; END IF;
    IF registration_sha IS DISTINCT FROM
       '4ac0137a42e79f560fbab6a4f6073324e553f2ca924d0e3a8c5956f51dd79659'
       OR supplier_price_text_sha256(registration_text) IS DISTINCT FROM registration_sha
       OR current_database() !~ '^[a-z][a-z0-9_]*_demo$' THEN
        RAISE EXCEPTION 'synthetic baseline registration identity differs';
    END IF;
    registration := registration_text::jsonb;
    IF registration->>'contract'<>'BUFFALO_SYNTHETIC_PRICE_REPLACEMENT_FIXTURE_V1'
       OR registration->>'schema'<>'qa_mapping_test'
       OR jsonb_array_length(registration->'policies')<>2 THEN
        RAISE EXCEPTION 'synthetic baseline registration payload differs';
    END IF;
    FOR policy IN SELECT value FROM jsonb_array_elements(registration->'policies')
    LOOP
        SELECT COALESCE(jsonb_agg(jsonb_build_array(
            o.supplier_sku,p.level_type,
            CASE WHEN p.break_qty IS NULL THEN NULL
                 ELSE to_char(p.break_qty,'FM9999999990.0000') END,
            p.break_unit,to_char(p.case_price,'FM9999999990.0000'),
            to_char(p.unit_price,'FM9999999990.0000'),p.source_file,p.source_page
        ) ORDER BY o.supplier_sku,(p.level_type<>'BASE'),p.source_page),'[]'::jsonb)
          INTO actual_rows
          FROM prices p JOIN supplier_offers o USING(offer_id)
         WHERE o.vendor_id=(policy->>'vendor_id')::uuid
           AND p.price_state='current' AND p.source_price_book_batch_id IS NULL;
        IF actual_rows IS DISTINCT FROM policy->'baseline_rows'
           OR NOT EXISTS (SELECT 1 FROM vendors v WHERE v.vendor_id=(policy->>'vendor_id')::uuid
                 AND v.vendor_name=policy->>'vendor_name' AND v.active)
           OR EXISTS (SELECT 1 FROM supplier_offers o
                WHERE o.vendor_id=(policy->>'vendor_id')::uuid
                  AND (NOT o.active OR o.confidence<>'VERIFIED'
                    OR o.package_type<>'STANDARD'
                    OR o.shopify_units_per_case<=0 OR o.qualifying_units_per_case<=0)) THEN
            RAISE EXCEPTION 'synthetic baseline CURRENT scope differs from registration';
        END IF;
        policy_hash := persistent_mapping_json_sha256(policy);
        INSERT INTO supplier_price_schedule_policies(
            policy_ref,policy_version,vendor_id,price_scope_key,cadence,currency,
            policy_timezone,observation_window_start_day,observation_window_end_day,
            effective_boundary_day,source_validity_required,
            fixture_policy_config_sha256,fixture_database_name,policy_principal_ref,
            observation_at,application_at,monday_evaluation_at,policy_sha256
        ) VALUES (
            policy->>'policy_ref',1,(policy->>'vendor_id')::uuid,
            policy->>'price_scope_key','MONTHLY',policy->>'currency',
            registration->>'policy_timezone',15,20,1,true,registration_sha,
            current_database(),
            'synthetic:price-fixture-registration:01',
            (registration->>'observation_at')::timestamptz,
            (registration->>'application_at')::timestamptz,
            (registration->>'monday_evaluation_at')::timestamptz,policy_hash
        );
        current_hash := supplier_price_scope_current_sha256((policy->>'vendor_id')::uuid);
        adoption_evidence := jsonb_build_object(
            'action','ADOPT_EXISTING_BASELINE',
            'policy_ref',policy->>'policy_ref',
            'policy_sha256',policy_hash,
            'vendor_id',policy->>'vendor_id',
            'price_scope_key',policy->>'price_scope_key',
            'fixture_registration_sha256',registration_sha,
            'baseline_member_set_sha256',persistent_mapping_json_sha256(
                policy->'baseline_rows'
            ),
            'current_scope_sha256',current_hash,
            'commercial_authority',false,
            'supplier_verified',false
        );
        INSERT INTO supplier_price_authority_events(
            action,vendor_id,price_scope_key,schedule_policy_ref,
            expected_current_scope_sha256,resulting_current_scope_sha256,
            payload_sha256,evidence_json
        ) VALUES (
            'ADOPT_EXISTING_BASELINE',(policy->>'vendor_id')::uuid,
            policy->>'price_scope_key',policy->>'policy_ref',current_hash,current_hash,
            persistent_mapping_json_sha256(adoption_evidence),adoption_evidence
        ) RETURNING supplier_price_authority_event_id INTO event_id;
        INSERT INTO supplier_price_authority_heads(
            vendor_id,price_scope_key,supplier_price_authority_event_id,
            head_version,active_price_book_batch_id,current_scope_sha256
        ) VALUES ((policy->>'vendor_id')::uuid,policy->>'price_scope_key',
                  event_id,1,NULL,current_hash);
    END LOOP;
END
$adopt$;

SELECT assert_synthetic_price_replacement_contract();
