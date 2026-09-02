-- Phase 4 terminal disposition: historical-only canonical identity, append-only
-- terminal decision provenance, and operational ineligibility controls.

-- This is deliberately the first executable control in the migration.  A
-- pre-terminal database has no legitimate Product-ID-less canonical rows.  On
-- reapply, only the explicitly constrained HISTORICAL_ONLY rows may omit it.
DO $$
DECLARE
    invalid_count BIGINT;
    invalid_sample TEXT;
    scope_column_exists BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid='variants'::regclass
          AND attname='identity_scope'
          AND NOT attisdropped
    ) INTO scope_column_exists;
    IF scope_column_exists THEN
        EXECUTE $query$
            SELECT COUNT(*)::bigint,
                   array_to_string(
                     (array_agg(variant_id ORDER BY variant_id))[1:10],','
                   )
            FROM variants
            WHERE identity_scope<>'HISTORICAL_ONLY'
              AND COALESCE(btrim(product_id),'')=''
        $query$ INTO invalid_count,invalid_sample;
    ELSE
        SELECT COUNT(*)::bigint,
               array_to_string(
                 (array_agg(variant_id ORDER BY variant_id))[1:10],','
               )
          INTO invalid_count,invalid_sample
          FROM variants
          WHERE COALESCE(btrim(product_id),'')='';
    END IF;
    IF invalid_count>0 THEN
        RAISE EXCEPTION
          'phase4 terminal migration blocked: CURRENT variants with missing Product ID count=%, sample=%',
          invalid_count,COALESCE(invalid_sample,'');
    END IF;
END $$;

ALTER TABLE variants ADD COLUMN IF NOT EXISTS identity_scope TEXT NOT NULL DEFAULT 'CURRENT';
ALTER TABLE variants ADD COLUMN IF NOT EXISTS restoration_manifest_sha256 TEXT;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS restoration_manifest_row_number INTEGER;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS restoration_evidence_version TEXT;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS restoration_owner_authorization TEXT;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS restoration_authority_git_sha TEXT;
ALTER TABLE variants ADD COLUMN IF NOT EXISTS restoration_execution_git_sha TEXT;
ALTER TABLE variants ALTER COLUMN product_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='variants'::regclass AND conname='ck_variants_identity_scope'
    ) THEN
        ALTER TABLE variants ADD CONSTRAINT ck_variants_identity_scope
            CHECK (identity_scope IN ('CURRENT','HISTORICAL_ONLY'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='variants'::regclass AND conname='ck_variants_identity_invariants'
    ) THEN
        ALTER TABLE variants ADD CONSTRAINT ck_variants_identity_invariants CHECK (
            (
                identity_scope='HISTORICAL_ONLY'
                AND active=FALSE
                AND catalog_state='RETIRED_CONFIRMED'
                AND product_id IS NULL
                AND retail_price IS NULL
                AND shopify_current_cost IS NULL
                AND inventory_item_gid IS NULL
                AND COALESCE(inventory_tracked,FALSE)=FALSE
            )
            OR
            (
                identity_scope='CURRENT'
                AND product_id IS NOT NULL
                AND btrim(product_id)<>''
            )
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='variants'::regclass AND conname='ck_variants_restoration_provenance'
    ) THEN
        ALTER TABLE variants ADD CONSTRAINT ck_variants_restoration_provenance CHECK (
            (
                identity_scope='HISTORICAL_ONLY'
                AND restoration_manifest_sha256 ~ '^[0-9a-f]{64}$'
                AND restoration_manifest_row_number > 0
                AND restoration_evidence_version IS NOT NULL
                AND btrim(restoration_evidence_version)<>''
                AND restoration_owner_authorization IS NOT NULL
                AND btrim(restoration_owner_authorization)<>''
                AND restoration_authority_git_sha ~ '^[0-9a-f]{40}$'
                AND restoration_execution_git_sha ~ '^[0-9a-f]{40}$'
            )
            OR
            (
                identity_scope='CURRENT'
                AND restoration_manifest_sha256 IS NULL
                AND restoration_manifest_row_number IS NULL
                AND restoration_evidence_version IS NULL
                AND restoration_owner_authorization IS NULL
                AND restoration_authority_git_sha IS NULL
                AND restoration_execution_git_sha IS NULL
            )
        );
    END IF;
END $$;

ALTER TABLE historical_sales_review_decisions
    ADD COLUMN IF NOT EXISTS decision_schema_version TEXT NOT NULL DEFAULT 'LEGACY_V1';
ALTER TABLE historical_sales_review_decisions ADD COLUMN IF NOT EXISTS reason_code TEXT;
ALTER TABLE historical_sales_review_decisions ADD COLUMN IF NOT EXISTS primary_manifest_sha256 TEXT;
ALTER TABLE historical_sales_review_decisions ADD COLUMN IF NOT EXISTS primary_manifest_row_number INTEGER;
ALTER TABLE historical_sales_review_decisions ADD COLUMN IF NOT EXISTS evidence_version TEXT;
ALTER TABLE historical_sales_review_decisions ADD COLUMN IF NOT EXISTS owner_authorization TEXT;
ALTER TABLE historical_sales_review_decisions ADD COLUMN IF NOT EXISTS authority_git_sha TEXT;
ALTER TABLE historical_sales_review_decisions ADD COLUMN IF NOT EXISTS execution_git_sha TEXT;

ALTER TABLE historical_sales_review_decisions
    DROP CONSTRAINT IF EXISTS historical_sales_review_decisions_decision_action_check;
ALTER TABLE historical_sales_review_decisions
    DROP CONSTRAINT IF EXISTS historical_sales_review_decisions_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='historical_sales_review_decisions'::regclass
          AND conname='ck_historical_sales_review_decision_action'
    ) THEN
        ALTER TABLE historical_sales_review_decisions
            ADD CONSTRAINT ck_historical_sales_review_decision_action
            CHECK (decision_action IN ('MAP','EXCLUDE','LEAVE_UNRESOLVED','RESTORE'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='historical_sales_review_decisions'::regclass
          AND conname='ck_historical_sales_review_decision_target'
    ) THEN
        ALTER TABLE historical_sales_review_decisions
            ADD CONSTRAINT ck_historical_sales_review_decision_target CHECK (
                (decision_action='MAP' AND canonical_variant_id IS NOT NULL)
                OR
                (
                    decision_action='RESTORE'
                    AND canonical_variant_id IS NOT NULL
                    AND source_variant_id IS NOT NULL
                    AND btrim(source_variant_id)<>''
                    AND source_variant_id<>'0'
                    AND canonical_variant_id=source_variant_id
                )
                OR
                (
                    decision_action IN ('EXCLUDE','LEAVE_UNRESOLVED')
                    AND canonical_variant_id IS NULL
                )
            );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='historical_sales_review_decisions'::regclass
          AND conname='ck_historical_sales_review_decision_terminal_provenance'
    ) THEN
        ALTER TABLE historical_sales_review_decisions
            ADD CONSTRAINT ck_historical_sales_review_decision_terminal_provenance CHECK (
                (
                    decision_schema_version='LEGACY_V1'
                    AND reason_code IS NULL
                    AND primary_manifest_sha256 IS NULL
                    AND primary_manifest_row_number IS NULL
                    AND evidence_version IS NULL
                    AND owner_authorization IS NULL
                    AND authority_git_sha IS NULL
                    AND execution_git_sha IS NULL
                )
                OR
                (
                    decision_schema_version='PHASE4_TERMINAL_V1'
                    AND decision_action IN ('MAP','EXCLUDE','RESTORE')
                    AND primary_manifest_sha256 IN (
                        '95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287',
                        'fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff'
                    )
                    AND primary_manifest_row_number > 0
                    AND evidence_version IS NOT NULL AND btrim(evidence_version)<>''
                    AND owner_authorization IS NOT NULL AND btrim(owner_authorization)<>''
                    AND authority_git_sha ~ '^[0-9a-f]{40}$'
                    AND execution_git_sha ~ '^[0-9a-f]{40}$'
                    AND (
                        (
                            decision_action='EXCLUDE'
                            AND reason_code IN (
                                'PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION',
                                'HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW'
                            )
                            AND (
                                (reason_code='PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION'
                                 AND primary_manifest_sha256='95fe0c7902efc337bb51ba0b5a2f974f9b2ac76d7221a25e7dcd52a8cd28d287')
                                OR
                                (reason_code='HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW'
                                 AND primary_manifest_sha256='fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff')
                            )
                        )
                        OR
                        (
                            decision_action IN ('MAP','RESTORE')
                            AND reason_code IS NULL
                            AND primary_manifest_sha256='fb1e15e67fe66c7742b84ea2c50bf01ce8a5008f00b4887293404ac09d3f59ff'
                        )
                    )
                )
            );
    END IF;
END $$;

CREATE OR REPLACE FUNCTION phase4_reject_review_decision_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'historical_sales_review_decisions is append-only; supersede with a new decision';
END $$;

DROP TRIGGER IF EXISTS trg_phase4_review_decisions_append_only
ON historical_sales_review_decisions;
CREATE TRIGGER trg_phase4_review_decisions_append_only
BEFORE UPDATE OR DELETE ON historical_sales_review_decisions
FOR EACH ROW EXECUTE FUNCTION phase4_reject_review_decision_mutation();

ALTER TABLE historical_sales_exclusions ADD COLUMN IF NOT EXISTS reason_code TEXT;
ALTER TABLE historical_sales_exclusions ADD COLUMN IF NOT EXISTS effective_decision_id UUID;

CREATE TABLE IF NOT EXISTS historical_sales_exclusion_authority_runs (
    sales_backfill_id UUID PRIMARY KEY
        REFERENCES sales_backfill_runs(sales_backfill_id) ON DELETE RESTRICT,
    authority_version TEXT NOT NULL CHECK (btrim(authority_version)<>''),
    decision_authority_run_id UUID NOT NULL
        REFERENCES sales_backfill_runs(sales_backfill_id) ON DELETE RESTRICT,
    original_manifest_sha256 TEXT NOT NULL
        CHECK (original_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    terminal_manifest_sha256 TEXT NOT NULL
        CHECK (terminal_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    decision_schema_version TEXT NOT NULL
        CHECK (btrim(decision_schema_version)<>''),
    evidence_version TEXT NOT NULL CHECK (btrim(evidence_version)<>''),
    owner_authorization TEXT NOT NULL CHECK (btrim(owner_authorization)<>''),
    authority_git_sha TEXT NOT NULL CHECK (authority_git_sha ~ '^[0-9a-f]{40}$'),
    execution_git_sha TEXT NOT NULL CHECK (execution_git_sha ~ '^[0-9a-f]{40}$'),
    registered_by TEXT NOT NULL CHECK (btrim(registered_by)<>''),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION phase4_reject_exclusion_authority_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'historical sales exclusion authority is append-only';
END $$;

DROP TRIGGER IF EXISTS trg_phase4_exclusion_authority_append_only
ON historical_sales_exclusion_authority_runs;
CREATE TRIGGER trg_phase4_exclusion_authority_append_only
BEFORE UPDATE OR DELETE ON historical_sales_exclusion_authority_runs
FOR EACH ROW EXECUTE FUNCTION phase4_reject_exclusion_authority_mutation();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='historical_sales_exclusions'::regclass
          AND conname='fk_historical_sales_exclusion_effective_decision'
    ) THEN
        ALTER TABLE historical_sales_exclusions
            ADD CONSTRAINT fk_historical_sales_exclusion_effective_decision
            FOREIGN KEY(effective_decision_id)
            REFERENCES historical_sales_review_decisions(historical_sales_review_decision_id)
            ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='historical_sales_exclusions'::regclass
          AND conname='ck_historical_sales_exclusion_structured_provenance'
    ) THEN
        ALTER TABLE historical_sales_exclusions
            ADD CONSTRAINT ck_historical_sales_exclusion_structured_provenance CHECK (
                (
                    reason_code IS NULL
                    AND effective_decision_id IS NULL
                )
                OR
                (
                    reason_code IN (
                        'PHASE4_ORIGINAL_EXACT_NON_PRODUCT_EXCLUSION',
                        'HISTORICAL_IDENTITY_UNATTRIBUTABLE_AFTER_EXHAUSTIVE_REVIEW'
                    )
                    AND effective_decision_id IS NOT NULL
                )
            );
    END IF;
END $$;

CREATE OR REPLACE FUNCTION phase4_validate_exclusion_decision_link()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    linked RECORD;
BEGIN
    IF NEW.effective_decision_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT source_identity_key,decision_action,reason_code,canonical_variant_id
      INTO linked
      FROM historical_sales_review_decisions
     WHERE historical_sales_review_decision_id=NEW.effective_decision_id;
    IF linked IS NULL
       OR linked.source_identity_key<>NEW.source_key
       OR linked.decision_action<>'EXCLUDE'
       OR linked.reason_code IS DISTINCT FROM NEW.reason_code
       OR linked.canonical_variant_id IS NOT NULL THEN
        RAISE EXCEPTION 'historical exclusion effective decision does not match exact source/reason';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_phase4_exclusion_decision_link ON historical_sales_exclusions;
CREATE TRIGGER trg_phase4_exclusion_decision_link
BEFORE INSERT OR UPDATE OF source_key,reason_code,effective_decision_id
ON historical_sales_exclusions
FOR EACH ROW EXECUTE FUNCTION phase4_validate_exclusion_decision_link();

CREATE OR REPLACE FUNCTION is_operational_current_variant(checked_variant_id TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM variants
        WHERE variant_id=checked_variant_id
          AND identity_scope='CURRENT'
          AND active=TRUE
    )
$$;

CREATE OR REPLACE FUNCTION phase4_assert_current_operational_variant(
    checked_variant_id TEXT,
    operation_name TEXT
) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
BEGIN
    IF checked_variant_id IS NULL THEN
        RETURN;
    END IF;
    IF NOT is_operational_current_variant(checked_variant_id) THEN
        RAISE EXCEPTION '% requires an active CURRENT variant; variant % is ineligible',
            operation_name,checked_variant_id;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION phase4_guard_direct_operational_variant()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM phase4_assert_current_operational_variant(NEW.variant_id,TG_TABLE_NAME);
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION phase4_guard_active_operational_variant()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.active THEN
        PERFORM phase4_assert_current_operational_variant(NEW.variant_id,TG_TABLE_NAME);
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION phase4_guard_approved_supplier_alias()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.approved THEN
        PERFORM phase4_assert_current_operational_variant(NEW.variant_id,TG_TABLE_NAME);
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION phase4_guard_offer_operational_reference()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    offer_variant_id TEXT;
BEGIN
    IF NEW.offer_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT variant_id INTO offer_variant_id
      FROM supplier_offers WHERE offer_id=NEW.offer_id;
    PERFORM phase4_assert_current_operational_variant(offer_variant_id,TG_TABLE_NAME);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_phase4_supplier_offer_guard ON supplier_offers;
CREATE TRIGGER trg_phase4_supplier_offer_guard
BEFORE INSERT OR UPDATE OF variant_id,active ON supplier_offers
FOR EACH ROW EXECUTE FUNCTION phase4_guard_active_operational_variant();

DROP TRIGGER IF EXISTS trg_phase4_supplier_alias_guard ON supplier_aliases;
CREATE TRIGGER trg_phase4_supplier_alias_guard
BEFORE INSERT OR UPDATE OF variant_id,approved ON supplier_aliases
FOR EACH ROW EXECUTE FUNCTION phase4_guard_approved_supplier_alias();

DROP TRIGGER IF EXISTS trg_phase4_price_guard ON prices;
CREATE TRIGGER trg_phase4_price_guard
BEFORE INSERT OR UPDATE OF offer_id ON prices
FOR EACH ROW EXECUTE FUNCTION phase4_guard_offer_operational_reference();

DROP TRIGGER IF EXISTS trg_phase4_combo_component_guard ON combo_components;
CREATE TRIGGER trg_phase4_combo_component_guard
BEFORE INSERT OR UPDATE OF offer_id ON combo_components
FOR EACH ROW EXECUTE FUNCTION phase4_guard_offer_operational_reference();

DROP TRIGGER IF EXISTS trg_phase4_manual_override_guard ON manual_overrides;
CREATE TRIGGER trg_phase4_manual_override_guard
BEFORE INSERT OR UPDATE OF variant_id,active ON manual_overrides
FOR EACH ROW EXECUTE FUNCTION phase4_guard_active_operational_variant();

DROP TRIGGER IF EXISTS trg_phase4_variant_policy_guard ON variant_policies;
CREATE TRIGGER trg_phase4_variant_policy_guard
BEFORE INSERT OR UPDATE OF variant_id,active ON variant_policies
FOR EACH ROW EXECUTE FUNCTION phase4_guard_active_operational_variant();

DROP TRIGGER IF EXISTS trg_phase4_forecast_guard ON forecast_results;
CREATE TRIGGER trg_phase4_forecast_guard
BEFORE INSERT OR UPDATE OF variant_id ON forecast_results
FOR EACH ROW EXECUTE FUNCTION phase4_guard_direct_operational_variant();

DROP TRIGGER IF EXISTS trg_phase4_procurement_recommendation_guard ON procurement_recommendations;
CREATE TRIGGER trg_phase4_procurement_recommendation_guard
BEFORE INSERT OR UPDATE OF variant_id ON procurement_recommendations
FOR EACH ROW EXECUTE FUNCTION phase4_guard_direct_operational_variant();

DROP TRIGGER IF EXISTS trg_phase4_po_line_guard ON purchase_order_lines;
CREATE TRIGGER trg_phase4_po_line_guard
BEFORE INSERT OR UPDATE OF variant_id ON purchase_order_lines
FOR EACH ROW EXECUTE FUNCTION phase4_guard_direct_operational_variant();

CREATE OR REPLACE FUNCTION phase4_guard_historical_scope_transition()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.identity_scope='HISTORICAL_ONLY'
       AND OLD.identity_scope IS DISTINCT FROM 'HISTORICAL_ONLY'
       AND (
           EXISTS (SELECT 1 FROM supplier_offers WHERE variant_id=OLD.variant_id AND active)
           OR EXISTS (
               SELECT 1 FROM prices p JOIN supplier_offers o USING(offer_id)
               WHERE o.variant_id=OLD.variant_id
           )
           OR EXISTS (SELECT 1 FROM supplier_aliases WHERE variant_id=OLD.variant_id AND approved)
           OR EXISTS (SELECT 1 FROM manual_overrides WHERE variant_id=OLD.variant_id AND active)
           OR EXISTS (SELECT 1 FROM variant_policies WHERE variant_id=OLD.variant_id AND active)
           OR EXISTS (SELECT 1 FROM forecast_results WHERE variant_id=OLD.variant_id)
           OR EXISTS (SELECT 1 FROM procurement_recommendations WHERE variant_id=OLD.variant_id)
           OR EXISTS (SELECT 1 FROM purchase_order_lines WHERE variant_id=OLD.variant_id)
           OR EXISTS (
               SELECT 1 FROM combo_components c JOIN supplier_offers o USING(offer_id)
               WHERE o.variant_id=OLD.variant_id
           )
       ) THEN
        RAISE EXCEPTION 'variant % has operational dependents and cannot become HISTORICAL_ONLY',
            OLD.variant_id;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_phase4_historical_scope_transition ON variants;
CREATE TRIGGER trg_phase4_historical_scope_transition
BEFORE UPDATE OF identity_scope ON variants
FOR EACH ROW EXECUTE FUNCTION phase4_guard_historical_scope_transition();

CREATE OR REPLACE VIEW v_current_prices AS
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,o.shopify_units_per_case,
       o.qualifying_units_per_case,o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
WHERE p.price_state='current'
  AND o.active
  AND is_operational_current_variant(o.variant_id);

CREATE OR REPLACE VIEW v_future_prices AS
SELECT p.*,o.variant_id,o.vendor_id,o.supplier_sku,o.shopify_units_per_case,
       o.qualifying_units_per_case,o.assortment_scope,o.assortment_group,o.assortable
FROM prices p
JOIN supplier_offers o ON o.offer_id=p.offer_id
WHERE p.price_state='future'
  AND o.active
  AND is_operational_current_variant(o.variant_id);

CREATE OR REPLACE VIEW v_operational_inventory_snapshots AS
SELECT i.*
FROM inventory_snapshots i
WHERE is_operational_current_variant(i.variant_id);

CREATE OR REPLACE VIEW v_operational_daily_inventory_snapshots AS
SELECT i.*
FROM daily_inventory_snapshots i
WHERE is_operational_current_variant(i.variant_id);

CREATE OR REPLACE VIEW v_operational_variants AS
SELECT v.*
FROM variants v
WHERE is_operational_current_variant(v.variant_id);

-- Fail closed if a future edit leaves the migration only partially enforcing
-- its contract.  These checks also make idempotent reapplication observable.
DO $$
DECLARE
    missing TEXT[] := ARRAY[]::TEXT[];
    view_definition TEXT;
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid='variants'::regclass AND attname='product_id'
          AND attnotnull AND NOT attisdropped
    ) THEN
        missing := array_append(missing,'variants.product_id still NOT NULL');
    END IF;
    IF (
        SELECT COUNT(*) FROM pg_constraint
        WHERE conrelid='variants'::regclass
          AND conname IN (
            'ck_variants_identity_scope',
            'ck_variants_identity_invariants',
            'ck_variants_restoration_provenance'
          ) AND convalidated
    ) <> 3 THEN
        missing := array_append(missing,'validated variant identity constraints');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname='is_operational_current_variant'
          AND pg_function_is_visible(oid)
    ) THEN
        missing := array_append(missing,'operational CURRENT helper');
    END IF;
    SELECT pg_get_viewdef('v_current_prices'::regclass,TRUE)
      INTO view_definition;
    IF position('is_operational_current_variant' IN view_definition)=0 THEN
        missing := array_append(missing,'filtered current-price view');
    END IF;
    SELECT pg_get_viewdef('v_future_prices'::regclass,TRUE)
      INTO view_definition;
    IF position('is_operational_current_variant' IN view_definition)=0 THEN
        missing := array_append(missing,'filtered future-price view');
    END IF;
    IF cardinality(missing)>0 THEN
        RAISE EXCEPTION 'phase4 terminal migration postcondition failure: %',
            array_to_string(missing,', ');
    END IF;
END $$;
