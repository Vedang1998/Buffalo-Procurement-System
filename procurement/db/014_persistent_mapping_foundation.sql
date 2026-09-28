-- buffalo-migration-replay: checksum-skip-v2
-- buffalo-contract-family: persistent-mapping-foundation
-- 014_persistent_mapping_foundation.sql
-- Exact predecessor under the reviewed chain: 013_monday_p1_remediation.sql.
-- apply_schema.py supplies the enclosing transaction and migration marker.
-- The design-only tokens "qa_mapping_test" / its
-- 'qa_mapping_test' literal and
-- "qa_mapping_test" / its
-- 'qa_mapping_test' literal, plus the unquoted
-- 'ab773e859feee527bba01c4ae3193fece3258bbe471d92595302531cbc270450' and
-- '2d08568ea5df7ef3ee383381ae2d952c197ac87f8d8fffd4c1af877ca05ef69c' and
-- '[{"current_user":"qa_mapping_owner","session_user":"qa_release_login"}]', MUST be replaced as whole tokens
-- with psycopg.sql.Identifier / psycopg.sql.Literal respectively before the
-- numbered file, embedded maintenance pair contract, function-property hashes,
-- and checksum manifest are reviewed. Runtime rewriting and residual tokens in
-- a published migration are forbidden.

SET LOCAL search_path = pg_catalog,
    "qa_mapping_test", "qa_mapping_test", pg_temp;

SELECT pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
        'buffalo:migration:persistent-mapping-foundation:' ||
        'qa_mapping_test', 0
    )
);

DO $bootstrap_preconditions$
DECLARE
    pgcrypto_schema CONSTANT TEXT := 'qa_mapping_test';
    resolved_digest REGPROCEDURE;
BEGIN
    resolved_digest := pg_catalog.to_regprocedure(pg_catalog.format(
        '%I.digest(bytea,text)',pgcrypto_schema
    ));
    IF resolved_digest IS NULL OR NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_depend d
          JOIN pg_catalog.pg_extension e ON e.oid=d.refobjid
         WHERE d.classid='pg_catalog.pg_proc'::regclass
           AND d.objid=resolved_digest::oid
           AND d.deptype='e' AND e.extname='pgcrypto'
    ) THEN
        RAISE EXCEPTION 'the resolved pgcrypto digest(bytea,text) member is absent';
    END IF;
END
$bootstrap_preconditions$;

DO $migration_preconditions$
DECLARE
    actual_markers TEXT[];
    expected_markers CONSTANT TEXT[] := ARRAY[
        'migration:001_v1_3_catalog_sales.sql',
        'migration:002_seed_import_records.sql',
        'migration:003_phase3_reconciliation.sql',
        'migration:004_identity_decision_invariants.sql',
        'migration:005_identity_investigation.sql',
        'migration:006_phase4_sales_backfill.sql',
        'migration:007_phase4_terminal_disposition.sql',
        'migration:008_monday_inventory_foundation.sql',
        'migration:009_monday_vendor_rules.sql',
        'migration:010_monday_po_ledger.sql',
        'migration:011_monday_price_book_staging.sql',
        'migration:012_monday_review_draft_packet.sql',
        'migration:013_monday_p1_remediation.sql',
        'migration:schema_postgres.sql'
    ];
    installed_contract TEXT;
    installed_catalog_sha256 TEXT;
    target_schema CONSTANT TEXT := 'qa_mapping_test';
    target_schema_oid OID;
BEGIN
    SELECT n.oid INTO target_schema_oid
      FROM pg_catalog.pg_namespace n WHERE n.nspname=target_schema;
    IF target_schema_oid IS NULL
       OR target_schema_oid=pg_catalog.pg_my_temp_schema()
       OR pg_catalog.pg_is_other_temp_schema(target_schema_oid)
       OR target_schema ~ '^pg_(toast_)?temp_[0-9]+$'
       OR pg_catalog.to_regclass(
              pg_catalog.format('%I.%I',target_schema,'meta')
          ) IS NULL THEN
        RAISE EXCEPTION 'persistent mapping migration requires an explicit target schema';
    END IF;
    SELECT value INTO installed_contract
      FROM "qa_mapping_test".meta WHERE key='persistent_mapping_foundation_contract';
    SELECT value INTO installed_catalog_sha256
      FROM "qa_mapping_test".meta
     WHERE key='persistent_mapping_foundation_catalog_sha256';
    SELECT array_agg(key ORDER BY key) INTO actual_markers
      FROM "qa_mapping_test".meta WHERE key LIKE 'migration:%';

    -- The manifest-aware runner must skip an installed release. This v1 body
    -- is an initial transition only and may not attest to or repair itself.
    IF installed_contract IS NOT NULL OR installed_catalog_sha256 IS NOT NULL THEN
        RAISE EXCEPTION
            'v1 mapping SQL cannot execute over installed contract metadata';
    END IF;
    PERFORM pg_catalog.set_config(
        'procurement.persistent_mapping_foundation_initial_install',
        'true',true
    );

    IF actual_markers IS DISTINCT FROM expected_markers THEN
        RAISE EXCEPTION
            'persistent mapping foundation requires exact predecessor 013; markers=%',
            actual_markers;
    END IF;
    IF (SELECT value FROM "qa_mapping_test".meta
         WHERE key='monday_price_book_contract') IS DISTINCT FROM 'v2-future-only'
       OR (SELECT value FROM "qa_mapping_test".meta
            WHERE key='monday_p1_remediation_contract') IS DISTINCT FROM 'v1' THEN
        RAISE EXCEPTION 'required Monday predecessor contracts differ';
    END IF;
    IF pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_mapping_review_batches')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_mapping_review_candidates')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_mapping_decisions')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_offer_selection_events')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'supplier_offer_selection_heads')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_effective_supplier_mapping_decisions')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_supplier_offer_selection_diagnostics')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_selected_standard_supplier_offers')) IS NOT NULL
       OR pg_catalog.to_regclass(pg_catalog.format(
           '%I.%I',target_schema,'v_supplier_offer_selection_shadow')) IS NOT NULL
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
             WHERE n.oid=target_schema_oid AND p.proname ~
               '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
          )
       OR EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger t
            JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
             WHERE c.relnamespace=target_schema_oid AND t.tgname IN (
                 'trg_protect_persistently_mapped_offer_contract',
                 'trg_protect_unactivated_mapped_offer_price',
                 'trg_protect_persistent_mapping_rejection_contract'
             )
          ) THEN
        RAISE EXCEPTION 'partial persistent mapping objects exist before v1 marker';
    END IF;

    IF to_regclass(format('%I.%I',target_schema,'variants')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'vendors')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_offers')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'mapping_rejections')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'prices')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'purchase_order_lines')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'procurement_recommendations')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'run_price_snapshots')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'combo_components')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'exceptions')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'price_book_staging_rows')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'price_book_validation_issues')) IS NULL
       OR to_regprocedure(format('%I.%I(text)',target_schema,'is_procurement_eligible_variant')) IS NULL
       OR to_regprocedure(format('%I.%I()',target_schema,'prevent_referenced_offer_identity_change')) IS NULL
       OR to_regprocedure(format('%I.%I()',target_schema,'protect_promoted_offer_contract')) IS NULL
       OR to_regprocedure(format('%I.%I()',target_schema,'protect_priced_vendor_contract')) IS NULL THEN
        RAISE EXCEPTION 'required catalog, offer, pricing, or reference contract is absent';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_index i
          JOIN pg_class c ON c.oid=i.indexrelid
         WHERE c.relname='uq_active_vendor_supplier_sku'
           AND i.indrelid=to_regclass(format('%I.%I',target_schema,'supplier_offers'))
           AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive
           AND NOT i.indisprimary AND NOT i.indisexclusion AND i.indimmediate
           AND NOT i.indnullsnotdistinct
           AND i.indpred IS NOT NULL AND i.indexprs IS NULL
           AND i.indnkeyatts=2 AND i.indnatts=2
           AND pg_get_indexdef(i.indexrelid,1,true)='vendor_id'
           AND pg_get_indexdef(i.indexrelid,2,true)='supplier_sku'
           AND pg_get_expr(i.indpred,i.indrelid,false) IN (
               '((active = true) AND (supplier_sku IS NOT NULL) AND (supplier_sku <> ''''::text))',
               '(active AND (supplier_sku IS NOT NULL) AND (supplier_sku <> ''''::text))'
           )
    ) THEN
        RAISE EXCEPTION 'active vendor/supplier-code uniqueness contract is absent';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
         WHERE t.tgrelid=to_regclass(format('%I.%I',target_schema,'supplier_offers'))
           AND t.tgname='trg_prevent_referenced_offer_identity_change'
           AND t.tgfoid=to_regprocedure(format('%I.%I()',target_schema,'prevent_referenced_offer_identity_change'))
           AND t.tgtype=19 AND t.tgqual IS NULL
           AND NOT t.tgisinternal AND t.tgenabled='O'
           AND cardinality(t.tgattr)=9
           AND ARRAY(
               SELECT a.attname::text
                 FROM unnest(t.tgattr) AS changed_attribute(attnum)
                 JOIN pg_attribute a
                   ON a.attrelid=t.tgrelid
                  AND a.attnum=changed_attribute.attnum
                ORDER BY a.attname::text
           )=ARRAY[
               'confidence','package_type','qualifying_units_per_case',
               'raw_pack','shopify_units_per_case','size_text','supplier_sku',
               'variant_id','vendor_id'
           ]::text[]
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger t
         WHERE t.tgrelid=to_regclass(format('%I.%I',target_schema,'supplier_offers'))
           AND t.tgname='trg_protect_promoted_offer_contract'
           AND t.tgfoid=to_regprocedure(format('%I.%I()',target_schema,'protect_promoted_offer_contract'))
           AND t.tgtype=27 AND t.tgqual IS NULL
           AND cardinality(t.tgattr)=0
           AND NOT t.tgisinternal AND t.tgenabled='O'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_trigger t
         WHERE t.tgrelid=to_regclass(format('%I.%I',target_schema,'vendors'))
           AND t.tgname='trg_protect_priced_vendor_contract'
           AND t.tgfoid=to_regprocedure(format('%I.%I()',target_schema,'protect_priced_vendor_contract'))
           AND t.tgtype=27 AND t.tgqual IS NULL
           AND cardinality(t.tgattr)=0
           AND NOT t.tgisinternal AND t.tgenabled='O'
    ) THEN
        RAISE EXCEPTION 'referenced/priced offer or vendor protection trigger is absent';
    END IF;
    IF (SELECT encode("qa_mapping_test".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
          FROM pg_proc p
         WHERE p.oid=to_regprocedure(format('%I.%I()',target_schema,'prevent_referenced_offer_identity_change'))
           AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
           AND p.prorettype='trigger'::regtype AND p.pronargs=0
           AND p.provolatile='v' AND NOT p.proisstrict AND NOT p.prosecdef
           AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM '0c8caf40ba425c3dbf847862195f3caf131ec1221bd4e0739e3cd5ffd81219aa'
       OR (SELECT encode("qa_mapping_test".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
             FROM pg_proc p
            WHERE p.oid=to_regprocedure(format('%I.%I()',target_schema,'protect_promoted_offer_contract'))
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
              AND p.prorettype='trigger'::regtype AND p.pronargs=0
              AND p.provolatile='v' AND NOT p.proisstrict AND NOT p.prosecdef
              AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM '1799ba81c390728e069c2a73b7814f9e2be9596b8e082dbadfe9288c556318d6'
       OR (SELECT encode("qa_mapping_test".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
             FROM pg_proc p
            WHERE p.oid=to_regprocedure(format('%I.%I()',target_schema,'protect_priced_vendor_contract'))
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
              AND p.prorettype='trigger'::regtype AND p.pronargs=0
              AND p.provolatile='v' AND NOT p.proisstrict AND NOT p.prosecdef
              AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM 'b2fd1ffccc54710d44d06050c884d2d31d6af5c6d3d409c70a43f23102f85589'
       OR (SELECT encode("qa_mapping_test".digest(convert_to(p.prosrc,'UTF8'),'sha256'),'hex')
             FROM pg_proc p
            WHERE p.oid=to_regprocedure(format('%I.%I(text)',target_schema,'is_procurement_eligible_variant'))
              AND p.prolang=(SELECT oid FROM pg_language WHERE lanname='sql')
              AND p.prorettype='boolean'::regtype AND p.pronargs=1
              AND p.provolatile='s' AND NOT p.proisstrict AND NOT p.prosecdef
              AND NOT p.proleakproof AND p.proparallel='u' AND p.proconfig IS NULL)
           IS DISTINCT FROM 'cde7b0dd0793ff9f8bc16cf42618d2f3542e2c442d2724a1b07d2f6fd170529a' THEN
        RAISE EXCEPTION 'critical predecessor function body or metadata differs';
    END IF;
END
$migration_preconditions$;

-- Installed catalog calculator. It is not trusted to attest to itself: the
-- runner verifies its manifest-bound source/properties/helpers before calling
-- it, and independently verifies the assertion before replay validation.
CREATE OR REPLACE FUNCTION "qa_mapping_test".compute_persistent_mapping_catalog_sha256()
RETURNS TEXT
LANGUAGE sql STABLE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $catalog_signature$
WITH target_namespace AS (
    SELECT n.oid,n.nspname,n.nspowner,n.nspacl
      FROM pg_catalog.pg_namespace n
     WHERE n.nspname='qa_mapping_test'
), target_relations AS (
    SELECT c.oid,c.relname,c.relkind,c.relpersistence,c.relrowsecurity,
           c.relforcerowsecurity,c.relreplident,c.relowner,c.relacl
      FROM pg_class c
      JOIN target_namespace n ON n.oid=c.relnamespace
     WHERE TRUE
       AND c.relname IN (
           'supplier_mapping_review_batches',
           'supplier_mapping_review_candidates',
           'supplier_mapping_decisions',
           'supplier_offer_selection_events',
           'supplier_offer_selection_heads',
           'v_effective_supplier_mapping_decisions',
           'v_supplier_offer_selection_diagnostics',
           'v_selected_standard_supplier_offers',
           'v_supplier_offer_selection_shadow'
       )
), catalog_items AS (
    SELECT 'schema:'||n.nspname AS item_key,
           jsonb_build_object(
               'kind','schema','name',n.nspname,
               'owner',pg_get_userbyid(n.nspowner),
               'acl',COALESCE(n.nspacl::text,'')
           ) AS item
      FROM target_namespace n
    UNION ALL
    SELECT 'relation:'||r.relname AS item_key,
           jsonb_build_object(
               'kind','relation','name',r.relname,'relkind',r.relkind,
               'persistence',r.relpersistence,'row_security',r.relrowsecurity,
               'force_row_security',r.relforcerowsecurity,
               'replica_identity',r.relreplident,
               'owner',pg_get_userbyid(r.relowner),
               'acl',COALESCE(r.relacl::text,'')
           ) AS item
      FROM target_relations r
    UNION ALL
    SELECT 'column:'||r.relname||':'||lpad(a.attnum::text,4,'0'),
           jsonb_build_object(
               'kind','column','relation',r.relname,'position',a.attnum,
               'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
               'not_null',a.attnotnull,'identity',a.attidentity,
               'generated',a.attgenerated,'storage',a.attstorage,
               'compression',a.attcompression,
               'collation',CASE WHEN a.attcollation=0 THEN NULL
                                ELSE a.attcollation::regcollation::text END,
               'default',pg_get_expr(d.adbin,d.adrelid,false),
               'acl',COALESCE(a.attacl::text,'')
           )
      FROM target_relations r
      JOIN pg_attribute a ON a.attrelid=r.oid
      LEFT JOIN pg_attrdef d ON d.adrelid=r.oid AND d.adnum=a.attnum
     WHERE a.attnum>0 AND NOT a.attisdropped
    UNION ALL
    SELECT 'constraint:'||r.relname||':'||k.conname,
           jsonb_build_object(
               'kind','constraint','relation',r.relname,'name',k.conname,
               'type',k.contype,'deferrable',k.condeferrable,
               'initially_deferred',k.condeferred,'validated',k.convalidated,
               'definition',pg_get_constraintdef(k.oid,true)
           )
      FROM target_relations r
      JOIN pg_constraint k ON k.conrelid=r.oid
    UNION ALL
    SELECT 'index:'||r.relname||':'||c.relname,
           jsonb_build_object(
               'kind','index','relation',r.relname,'name',c.relname,
               'unique',i.indisunique,'primary',i.indisprimary,
               'exclusion',i.indisexclusion,'immediate',i.indimmediate,
               'nulls_not_distinct',i.indnullsnotdistinct,
               'valid',i.indisvalid,'ready',i.indisready,'live',i.indislive,
               'definition',pg_get_indexdef(i.indexrelid)
           )
      FROM target_relations r
      JOIN pg_index i ON i.indrelid=r.oid
      JOIN pg_class c ON c.oid=i.indexrelid
    UNION ALL
    SELECT 'trigger:'||c.relname||':'||t.tgname,
           jsonb_build_object(
               'kind','trigger','relation',c.relname,'name',t.tgname,
               'enabled',t.tgenabled,'internal',t.tgisinternal,
               'definition',pg_get_triggerdef(t.oid,true)
           )
      FROM pg_trigger t
      JOIN pg_class c ON c.oid=t.tgrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='qa_mapping_test' AND NOT t.tgisinternal
       AND (
           t.tgrelid IN (SELECT oid FROM target_relations)
           OR t.tgname IN (
               'trg_protect_persistently_mapped_offer_contract',
               'trg_protect_unactivated_mapped_offer_price',
               'trg_protect_persistent_mapping_rejection_contract'
           )
       )
    UNION ALL
    SELECT 'function:'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',
           jsonb_build_object(
               'kind','function','name',p.proname,
               'arguments',pg_get_function_identity_arguments(p.oid),
               'result',pg_get_function_result(p.oid),'prokind',p.prokind,
               'language',l.lanname,'volatility',p.provolatile,
               'strict',p.proisstrict,'security_definer',p.prosecdef,
               'leakproof',p.proleakproof,'parallel',p.proparallel,
               'config',COALESCE(p.proconfig::text,''),
               'owner',pg_get_userbyid(p.proowner),
               'acl',COALESCE(p.proacl::text,''),'source',p.prosrc
           )
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
     WHERE n.nspname='qa_mapping_test' AND p.proname ~
       '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    UNION ALL
    SELECT 'view:'||r.relname,
           jsonb_build_object(
               'kind','view','name',r.relname,
               'definition',pg_get_viewdef(r.oid,true)
           )
      FROM target_relations r WHERE r.relkind='v'
)
SELECT encode("qa_mapping_test".digest(convert_to(
           COALESCE(jsonb_agg(item ORDER BY item_key)::text,'[]'),'UTF8'
       ),'sha256'),'hex')
  FROM catalog_items
$catalog_signature$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_text_sha256(value TEXT)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$ SELECT encode("qa_mapping_test".digest(convert_to(value, 'UTF8'), 'sha256'), 'hex') $$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_json_sha256(value JSONB)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$ SELECT "qa_mapping_test".persistent_mapping_text_sha256(value::text) $$;

CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_mapping_review_batches (
    review_batch_id UUID PRIMARY KEY,
    intake_idempotency_key UUID NOT NULL,
    source_package_kind TEXT NOT NULL CHECK (source_package_kind='SEALED_V5_REVIEW_PACKAGE'),
    source_package_id TEXT NOT NULL CHECK (btrim(source_package_id)<>''),
    source_revision TEXT NOT NULL CHECK (btrim(source_revision)<>''),
    source_artifact_ref TEXT NOT NULL CHECK (btrim(source_artifact_ref)<>''),
    source_artifact_sha256 TEXT NOT NULL CHECK (source_artifact_sha256 ~ '^[0-9a-f]{64}$'),
    source_root_sha256 TEXT NOT NULL CHECK (source_root_sha256 ~ '^[0-9a-f]{64}$'),
    source_seal_sha256 TEXT NOT NULL CHECK (source_seal_sha256 ~ '^[0-9a-f]{64}$'),
    relationship_table_sha256 TEXT NOT NULL CHECK (relationship_table_sha256 ~ '^[0-9a-f]{64}$'),
    source_batch_sha256 TEXT NOT NULL CHECK (source_batch_sha256 ~ '^[0-9a-f]{64}$'),
    source_payload_sha256 TEXT NOT NULL CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_set_sha256 TEXT NOT NULL CHECK (candidate_set_sha256 ~ '^[0-9a-f]{64}$'),
    candidate_count INTEGER NOT NULL CHECK (candidate_count>=0),
    supplier_period_scope JSONB NOT NULL CHECK (jsonb_typeof(supplier_period_scope)='object'),
    prerequisites JSONB NOT NULL CHECK (jsonb_typeof(prerequisites)='object'),
    structural_state TEXT NOT NULL CHECK (structural_state IN ('READY','BLOCKED')),
    source_evidence_state TEXT NOT NULL CHECK (source_evidence_state IN ('READY','BLOCKED')),
    semantic_state TEXT NOT NULL CHECK (semantic_state IN ('READY','BLOCKED')),
    source_authority_state TEXT NOT NULL DEFAULT 'NOT_APPROVED'
        CHECK (source_authority_state='NOT_APPROVED'),
    source_import_state TEXT NOT NULL DEFAULT 'NOT_IMPORT_READY'
        CHECK (source_import_state='NOT_IMPORT_READY'),
    source_is_simulation BOOLEAN NOT NULL DEFAULT FALSE,
    creator_principal_ref TEXT NOT NULL CHECK (btrim(creator_principal_ref)<>''),
    creator_role_ref TEXT NOT NULL CHECK (btrim(creator_role_ref)<>''),
    creator_authn_context_sha256 TEXT NOT NULL
        CHECK (creator_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_txid BIGINT NOT NULL DEFAULT txid_current(),
    CONSTRAINT uq_mapping_review_batches_idempotency
        UNIQUE (intake_idempotency_key)
);

CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_mapping_review_candidates (
    review_batch_id UUID NOT NULL
        REFERENCES "qa_mapping_test".supplier_mapping_review_batches(review_batch_id) ON DELETE RESTRICT,
    candidate_id UUID NOT NULL,
    occurrence_index INTEGER NOT NULL CHECK (occurrence_index>=1),
    occurrence_key TEXT NOT NULL CHECK (btrim(occurrence_key)<>''),
    printed_occurrence_sha256 TEXT NOT NULL
        CHECK (printed_occurrence_sha256 ~ '^[0-9a-f]{64}$'),
    supplier_identity_key_sha256 TEXT
        CHECK (supplier_identity_key_sha256 ~ '^[0-9a-f]{64}$'),
    operational_offer_key_sha256 TEXT
        CHECK (operational_offer_key_sha256 ~ '^[0-9a-f]{64}$'),
    decision_scope_sha256 TEXT NOT NULL
        CHECK (decision_scope_sha256 ~ '^[0-9a-f]{64}$'),
    source_table_name TEXT NOT NULL CHECK (btrim(source_table_name)<>''),
    source_row_key TEXT NOT NULL CHECK (btrim(source_row_key)<>''),
    source_file_name TEXT NOT NULL CHECK (btrim(source_file_name)<>''),
    source_file_sha256 TEXT NOT NULL CHECK (source_file_sha256 ~ '^[0-9a-f]{64}$'),
    source_page_start INTEGER CHECK (source_page_start IS NULL OR source_page_start>=1),
    source_page_end INTEGER CHECK (source_page_end IS NULL OR source_page_end>=1),
    source_locator JSONB NOT NULL CHECK (jsonb_typeof(source_locator)='object'),
    proposed_variant_id TEXT,
    proposed_vendor_id UUID,
    source_vendor_identity TEXT NOT NULL CHECK (btrim(source_vendor_identity)<>''),
    distributor_product_id_state TEXT NOT NULL
        CHECK (distributor_product_id_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    distributor_product_id_value TEXT,
    supplier_code_state TEXT NOT NULL
        CHECK (supplier_code_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    supplier_code_value TEXT,
    offer_class TEXT NOT NULL CHECK (offer_class IN
        ('REGULAR','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    occurrence_role TEXT NOT NULL CHECK (occurrence_role IN
        ('PRIMARY','TIER','REPEAT','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    package_type_state TEXT NOT NULL
        CHECK (package_type_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    package_type_value TEXT,
    size_state TEXT NOT NULL CHECK (size_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    size_value TEXT,
    raw_pack_state TEXT NOT NULL CHECK (raw_pack_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    raw_pack_value TEXT,
    physical_units_state TEXT NOT NULL
        CHECK (physical_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    physical_units_value NUMERIC(12,4),
    retail_pack_units_state TEXT NOT NULL
        CHECK (retail_pack_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    retail_pack_units_value NUMERIC(12,4),
    shopify_units_state TEXT NOT NULL
        CHECK (shopify_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    shopify_units_value NUMERIC(12,4),
    qualifying_units_state TEXT NOT NULL
        CHECK (qualifying_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    qualifying_units_value NUMERIC(12,4),
    assortment_scope_state TEXT NOT NULL
        CHECK (assortment_scope_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_scope_value TEXT,
    assortment_group_state TEXT NOT NULL
        CHECK (assortment_group_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_group_value TEXT,
    assortable_state TEXT NOT NULL
        CHECK (assortable_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortable_value BOOLEAN,
    identity_qualifiers JSONB NOT NULL CHECK (jsonb_typeof(identity_qualifiers)='object'),
    component_relationships JSONB NOT NULL CHECK (jsonb_typeof(component_relationships)='array'),
    independent_linkage_evidence JSONB NOT NULL
        CHECK (jsonb_typeof(independent_linkage_evidence)='array'),
    owner_clarifications JSONB NOT NULL CHECK (jsonb_typeof(owner_clarifications)='array'),
    historical_capture_scope JSONB NOT NULL CHECK (jsonb_typeof(historical_capture_scope)='object'),
    related_artifact_hashes JSONB NOT NULL CHECK (jsonb_typeof(related_artifact_hashes)='object'),
    blockers JSONB NOT NULL CHECK (jsonb_typeof(blockers)='array'),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    candidate_sha256 TEXT NOT NULL CHECK (candidate_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY (candidate_id),
    UNIQUE (review_batch_id,candidate_id),
    UNIQUE (review_batch_id,occurrence_index),
    UNIQUE (review_batch_id,occurrence_key),
    CHECK ((source_page_start IS NULL)=(source_page_end IS NULL)),
    CHECK (source_page_end IS NULL OR source_page_end>=source_page_start),
    CHECK ((distributor_product_id_state='VALUE')=(distributor_product_id_value IS NOT NULL)),
    CHECK ((supplier_code_state='VALUE')=(supplier_code_value IS NOT NULL)),
    CHECK ((package_type_state='VALUE')=(package_type_value IS NOT NULL)),
    CHECK ((size_state='VALUE')=(size_value IS NOT NULL)),
    CHECK ((raw_pack_state='VALUE')=(raw_pack_value IS NOT NULL)),
    CHECK ((physical_units_state='VALUE')=(physical_units_value IS NOT NULL)),
    CHECK ((retail_pack_units_state='VALUE')=(retail_pack_units_value IS NOT NULL)),
    CHECK ((shopify_units_state='VALUE')=(shopify_units_value IS NOT NULL)),
    CHECK ((qualifying_units_state='VALUE')=(qualifying_units_value IS NOT NULL)),
    CHECK ((assortment_scope_state='VALUE')=(assortment_scope_value IS NOT NULL)),
    CHECK (assortment_scope_value IS NULL OR
           assortment_scope_value IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')),
    CHECK ((assortment_group_state='VALUE')=(assortment_group_value IS NOT NULL)),
    CHECK ((assortable_state='VALUE')=(assortable_value IS NOT NULL)),
    CHECK (physical_units_value IS NULL OR physical_units_value>0),
    CHECK (retail_pack_units_value IS NULL OR retail_pack_units_value>0),
    CHECK (shopify_units_value IS NULL OR shopify_units_value>0),
    CHECK (qualifying_units_value IS NULL OR qualifying_units_value>0)
);

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_candidate_supplier_identity_key(
    c "qa_mapping_test".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT CASE
      WHEN c.proposed_vendor_id IS NULL
        OR c.distributor_product_id_state<>'VALUE'
        OR btrim(c.distributor_product_id_value)='' THEN NULL
      ELSE "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','SUPPLIER_IDENTITY_V1',
        'proposed_vendor_id',c.proposed_vendor_id,
        'source_vendor_identity',c.source_vendor_identity,
        'distributor_product_id_state',c.distributor_product_id_state,
        'distributor_product_id_value',c.distributor_product_id_value,
        'supplier_code_state',c.supplier_code_state,
        'supplier_code_value',c.supplier_code_value
      ))
    END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_candidate_operational_offer_key(
    c "qa_mapping_test".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT CASE
      WHEN "qa_mapping_test".persistent_mapping_candidate_supplier_identity_key(c) IS NULL
        OR c.proposed_variant_id IS NULL
        OR c.offer_class='UNKNOWN'
        OR c.package_type_state<>'VALUE'
        OR btrim(c.package_type_value)=''
        OR (c.supplier_code_state='VALUE' AND (
             btrim(c.supplier_code_value)=''
             OR c.supplier_code_value<>btrim(c.supplier_code_value)
           ))
        OR c.assortment_scope_state<>'VALUE' THEN NULL
      ELSE "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','OPERATIONAL_OFFER_V1',
        'proposed_vendor_id',c.proposed_vendor_id,
        'supplier_code_state',c.supplier_code_state,
        'supplier_code_value',c.supplier_code_value,
        'proposed_variant_id',c.proposed_variant_id,
        'offer_class',c.offer_class,
        'package_type_state',c.package_type_state,
        'package_type_value',c.package_type_value,
        'size_state',c.size_state,'size_value',c.size_value,
        'raw_pack_state',c.raw_pack_state,'raw_pack_value',c.raw_pack_value,
        'physical_units_state',c.physical_units_state,
        'physical_units_value',c.physical_units_value,
        'retail_pack_units_state',c.retail_pack_units_state,
        'retail_pack_units_value',c.retail_pack_units_value,
        'shopify_units_state',c.shopify_units_state,
        'shopify_units_value',c.shopify_units_value,
        'qualifying_units_state',c.qualifying_units_state,
        'qualifying_units_value',c.qualifying_units_value,
        'assortment_scope_state',c.assortment_scope_state,
        'assortment_scope_value',c.assortment_scope_value,
        'assortment_group_state',c.assortment_group_state,
        'assortment_group_value',c.assortment_group_value,
        'assortable_state',c.assortable_state,
        'assortable_value',c.assortable_value,
        'identity_qualifiers',c.identity_qualifiers,
        'component_relationships',c.component_relationships
      ))
    END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_candidate_decision_scope(
    c "qa_mapping_test".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','CANDIDATE_DECISION_SCOPE_V1',
        'review_batch_id',c.review_batch_id,
        'candidate_id',c.candidate_id
    ))
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_candidate_reviewed_facts(
    c "qa_mapping_test".supplier_mapping_review_candidates
)
RETURNS JSONB
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT jsonb_build_object(
        'package_type_state',c.package_type_state,
        'package_type_value',c.package_type_value,
        'physical_units_state',c.physical_units_state,
        'physical_units_value',c.physical_units_value,
        'retail_pack_units_state',c.retail_pack_units_state,
        'retail_pack_units_value',c.retail_pack_units_value,
        'assortment_scope_state',c.assortment_scope_state,
        'assortment_scope_value',c.assortment_scope_value,
        'assortment_group_state',c.assortment_group_state,
        'assortment_group_value',c.assortment_group_value,
        'assortable_state',c.assortable_state,
        'assortable_value',c.assortable_value,
        'identity_qualifiers',c.identity_qualifiers,
        'independent_linkage_evidence',c.independent_linkage_evidence,
        'related_artifact_hashes',c.related_artifact_hashes,
        'source_locator',c.source_locator,
        'source_file_name',c.source_file_name,
        'source_file_sha256',c.source_file_sha256,
        'source_page_start',c.source_page_start,
        'source_page_end',c.source_page_end
    )
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_candidate_evidence_set_sha256(
    c "qa_mapping_test".supplier_mapping_review_candidates
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','INDEPENDENT_LINKAGE_EVIDENCE_V1',
        'printed_source_sha256',c.source_file_sha256,
        'evidence',c.independent_linkage_evidence
    ))
$$;

CREATE INDEX idx_mapping_candidates_offer_key
    ON "qa_mapping_test".supplier_mapping_review_candidates(operational_offer_key_sha256);
CREATE INDEX idx_mapping_candidates_supplier_identity
    ON "qa_mapping_test".supplier_mapping_review_candidates(supplier_identity_key_sha256);
CREATE INDEX idx_mapping_candidates_proposed_identity
    ON "qa_mapping_test".supplier_mapping_review_candidates(proposed_vendor_id,proposed_variant_id);
CREATE INDEX idx_mapping_candidates_supplier_code
    ON "qa_mapping_test".supplier_mapping_review_candidates(proposed_vendor_id,supplier_code_value)
    WHERE supplier_code_state='VALUE';

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_catalog_fingerprint(wanted_variant_id TEXT)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'variant_id',v.variant_id,'shopify_gid',v.shopify_gid,
        'product_id',v.product_id,'product_gid',v.product_gid,
        'sku',v.sku,'barcode',v.barcode,'active',v.active,
        'identity_scope',v.identity_scope,'catalog_state',v.catalog_state,
        'source_snapshot',v.source_snapshot,'last_synced_at',v.last_synced_at
    )) FROM "qa_mapping_test".variants v WHERE v.variant_id=wanted_variant_id
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_vendor_fingerprint(wanted_vendor_id UUID)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'vendor_id',v.vendor_id,'vendor_name',v.vendor_name,'active',v.active,
        'order_day',v.order_day,'order_cycle_days',v.order_cycle_days,
        'lead_time_days',v.lead_time_days,'updated_at',v.updated_at
    )) FROM "qa_mapping_test".vendors v WHERE v.vendor_id=wanted_vendor_id
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_rejection_fingerprint(
    wanted_vendor_id UUID, wanted_source_key TEXT, excluded_rejection_id BIGINT
)
RETURNS TEXT
LANGUAGE sql STABLE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(COALESCE(jsonb_agg(
        jsonb_build_object(
            'rejection_id',r.rejection_id,'mapping_type',r.mapping_type,
            'source_key',r.source_key,'variant_id',r.rejected_variant_id,
            'vendor_id',r.vendor_id,'source_text',r.source_text,
            'evidence_json',r.evidence_json,'rejected_by',r.rejected_by,
            'rejected_at',r.rejected_at
        ) ORDER BY r.rejection_id
    ),'[]'::jsonb))
     FROM "qa_mapping_test".mapping_rejections r
     WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
       AND r.vendor_id IS NOT DISTINCT FROM wanted_vendor_id
       AND r.source_key=wanted_source_key
       AND (excluded_rejection_id IS NULL OR r.rejection_id<>excluded_rejection_id)
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_rejection_contract_fingerprint(
    wanted_rejection_id BIGINT
)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','PERSISTENT_MAPPING_REJECTION_V1',
        'mapping_type',r.mapping_type,'source_key',r.source_key,
        'rejected_variant_id',r.rejected_variant_id,'vendor_id',r.vendor_id,
        'source_text',r.source_text,'evidence_json',r.evidence_json,
        'rejected_by',r.rejected_by,'active',r.active
    )) FROM "qa_mapping_test".mapping_rejections r WHERE r.rejection_id=wanted_rejection_id
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_offer_fingerprint(wanted_offer_id BIGINT)
RETURNS TEXT
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'contract_version','SUPPLIER_OFFER_CONTRACT_V1',
        'variant_id',o.variant_id,'vendor_id',o.vendor_id,
        'supplier_sku',o.supplier_sku,'package_type',o.package_type,
        'size_text',o.size_text,'raw_pack',o.raw_pack,
        'shopify_units_per_case',o.shopify_units_per_case,
        'qualifying_units_per_case',o.qualifying_units_per_case,
        'assortment_scope',o.assortment_scope,'assortment_group',o.assortment_group,
        'assortable',o.assortable,'valid_from',o.valid_from,'valid_to',o.valid_to,
        'replaces_offer_id',o.replaces_offer_id,
        'source_file',o.source_file,'source_page',o.source_page,
        'confidence',o.confidence,'active',o.active
    )) FROM "qa_mapping_test".supplier_offers o WHERE o.offer_id=wanted_offer_id
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_offer_has_prior_references(
    wanted_offer_id BIGINT
)
RETURNS BOOLEAN
LANGUAGE sql STABLE STRICT
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT
        EXISTS (SELECT 1 FROM "qa_mapping_test".prices WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".purchase_order_lines WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".procurement_recommendations WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".run_price_snapshots WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".combo_components WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".exceptions WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".price_book_staging_rows WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".price_book_validation_issues WHERE offer_id=wanted_offer_id)
        OR EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_offers
                    WHERE replaces_offer_id=wanted_offer_id)
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_require_human_context(
    expected_principal_ref TEXT,
    expected_role_ref TEXT,
    expected_authn_context_sha256 TEXT,
    expected_action TEXT
)
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
BEGIN
    IF expected_principal_ref IS NULL OR btrim(expected_principal_ref)=''
       OR expected_role_ref IS NULL OR btrim(expected_role_ref)=''
       OR expected_authn_context_sha256 IS NULL
       OR expected_authn_context_sha256 !~ '^[0-9a-f]{64}$'
       OR expected_action IS NULL OR btrim(expected_action)=''
       OR current_setting('procurement.principal_kind',true) IS DISTINCT FROM 'HUMAN'
       OR current_setting('procurement.principal_ref',true)
            IS DISTINCT FROM expected_principal_ref
       OR current_setting('procurement.authorized_role_ref',true)
            IS DISTINCT FROM expected_role_ref
       OR current_setting('procurement.authn_context_sha256',true)
            IS DISTINCT FROM expected_authn_context_sha256
       OR current_setting('procurement.authorized_action',true)
            IS DISTINCT FROM expected_action THEN
        RAISE EXCEPTION 'verified named-human authorization context is absent or differs';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_require_enabled_capability(
    expected_capability TEXT
)
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
BEGIN
    IF expected_capability NOT IN (
           'review_intake_writes_enabled',
           'human_mapping_writes_enabled',
           'policy_mapping_writes_enabled',
           'routine_selection_writes_enabled'
       )
       OR current_setting('procurement.enabled_capability',true)
            IS DISTINCT FROM expected_capability THEN
        RAISE EXCEPTION 'required server-enabled persistent-mapping capability is absent';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".supplier_mapping_policy_is_published(
    policy_ref TEXT,
    policy_version TEXT,
    publication_sha256 TEXT,
    evidence_set_sha256 TEXT
)
RETURNS BOOLEAN
LANGUAGE sql STABLE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$ SELECT FALSE $$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".reject_persistent_mapping_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is forbidden',TG_TABLE_NAME,TG_OP;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".validate_mapping_review_batch_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE expected_payload JSONB;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'mapping review intake requires SERIALIZABLE isolation';
    END IF;
    IF NEW.created_txid<>txid_current() THEN
        RAISE EXCEPTION 'review batch transaction identity differs';
    END IF;
    PERFORM "qa_mapping_test".persistent_mapping_require_enabled_capability(
        'review_intake_writes_enabled'
    );
    PERFORM "qa_mapping_test".persistent_mapping_require_human_context(
        NEW.creator_principal_ref,NEW.creator_role_ref,
        NEW.creator_authn_context_sha256,'MAPPING_REVIEW_INTAKE'
    );
    expected_payload := to_jsonb(NEW)-ARRAY[
        'review_batch_id','intake_idempotency_key','canonical_payload',
        'payload_sha256','created_at','created_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'review batch canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".validate_mapping_review_candidate_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE parent_txid BIGINT; expected_payload JSONB;
BEGIN
    SELECT created_txid INTO parent_txid
      FROM "qa_mapping_test".supplier_mapping_review_batches
     WHERE review_batch_id=NEW.review_batch_id FOR SHARE;
    IF parent_txid IS DISTINCT FROM txid_current()
       OR NEW.created_txid<>txid_current() THEN
        RAISE EXCEPTION 'batch and candidates must be created in one transaction';
    END IF;
    IF NEW.supplier_identity_key_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_candidate_supplier_identity_key(NEW)
       OR NEW.operational_offer_key_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_candidate_operational_offer_key(NEW)
       OR NEW.decision_scope_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_candidate_decision_scope(NEW) THEN
        RAISE EXCEPTION 'candidate identity, offer, or decision-scope key differs';
    END IF;
    expected_payload := to_jsonb(NEW)-ARRAY[
        'candidate_id','canonical_payload','candidate_sha256','created_at','created_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.candidate_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'candidate canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".validate_mapping_review_candidate_set()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE actual_count BIGINT; actual_sha256 TEXT;
BEGIN
    SELECT count(*),"qa_mapping_test".persistent_mapping_text_sha256(COALESCE(
        string_agg(candidate_sha256||E'\n','' ORDER BY occurrence_index,candidate_id),''
    )) INTO actual_count,actual_sha256
      FROM "qa_mapping_test".supplier_mapping_review_candidates
     WHERE review_batch_id=NEW.review_batch_id;
    IF actual_count<>NEW.candidate_count
       OR actual_sha256 IS DISTINCT FROM NEW.candidate_set_sha256 THEN
        RAISE EXCEPTION 'review batch candidate count or set fingerprint differs';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_mapping_review_batch_insert
    ON "qa_mapping_test".supplier_mapping_review_batches;
CREATE TRIGGER trg_validate_mapping_review_batch_insert
BEFORE INSERT ON "qa_mapping_test".supplier_mapping_review_batches
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".validate_mapping_review_batch_insert();
DROP TRIGGER IF EXISTS trg_validate_mapping_review_candidate_insert
    ON "qa_mapping_test".supplier_mapping_review_candidates;
CREATE TRIGGER trg_validate_mapping_review_candidate_insert
BEFORE INSERT ON "qa_mapping_test".supplier_mapping_review_candidates
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".validate_mapping_review_candidate_insert();
DROP TRIGGER IF EXISTS trg_validate_mapping_review_candidate_set
    ON "qa_mapping_test".supplier_mapping_review_batches;
CREATE CONSTRAINT TRIGGER trg_validate_mapping_review_candidate_set
AFTER INSERT ON "qa_mapping_test".supplier_mapping_review_batches DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".validate_mapping_review_candidate_set();

DROP TRIGGER IF EXISTS trg_immutable_mapping_review_batches
    ON "qa_mapping_test".supplier_mapping_review_batches;
CREATE TRIGGER trg_immutable_mapping_review_batches
BEFORE UPDATE OR DELETE ON "qa_mapping_test".supplier_mapping_review_batches
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".reject_persistent_mapping_mutation();
DROP TRIGGER IF EXISTS trg_immutable_mapping_review_candidates
    ON "qa_mapping_test".supplier_mapping_review_candidates;
CREATE TRIGGER trg_immutable_mapping_review_candidates
BEFORE UPDATE OR DELETE ON "qa_mapping_test".supplier_mapping_review_candidates
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".reject_persistent_mapping_mutation();

CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_mapping_decisions (
    mapping_decision_id UUID PRIMARY KEY,
    decision_idempotency_key UUID NOT NULL,
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    review_batch_id UUID NOT NULL,
    candidate_id UUID NOT NULL,
    decision_scope_sha256 TEXT NOT NULL CHECK (decision_scope_sha256 ~ '^[0-9a-f]{64}$'),
    supplier_identity_key_sha256 TEXT
        CHECK (supplier_identity_key_sha256 ~ '^[0-9a-f]{64}$'),
    operational_offer_key_sha256 TEXT
        CHECK (operational_offer_key_sha256 ~ '^[0-9a-f]{64}$'),
    action TEXT NOT NULL CHECK (action IN ('APPROVE_MAPPING','REJECT_MAPPING','DEFER')),
    decision_origin TEXT NOT NULL CHECK (decision_origin IN ('HUMAN','POLICY')),
    authority_kind TEXT CHECK (authority_kind IN ('HUMAN_APPROVED','POLICY_APPROVED')),
    variant_id TEXT REFERENCES "qa_mapping_test".variants(variant_id) ON DELETE RESTRICT,
    vendor_id UUID REFERENCES "qa_mapping_test".vendors(vendor_id) ON DELETE RESTRICT,
    printed_occurrence_sha256 TEXT NOT NULL
        CHECK (printed_occurrence_sha256 ~ '^[0-9a-f]{64}$'),
    distributor_product_id_state TEXT NOT NULL
        CHECK (distributor_product_id_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    distributor_product_id_value TEXT,
    supplier_code_state TEXT NOT NULL
        CHECK (supplier_code_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    supplier_code_value TEXT,
    offer_class TEXT NOT NULL CHECK (offer_class IN
        ('REGULAR','GIFT','SPECIAL','ALTERNATE','COMPONENT','COMBO','UNKNOWN')),
    result_offer_package_type TEXT CHECK (
        result_offer_package_type IS NULL OR btrim(result_offer_package_type)<>''
    ),
    size_state TEXT NOT NULL CHECK (size_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    size_value TEXT,
    raw_pack_state TEXT NOT NULL CHECK (raw_pack_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    raw_pack_value TEXT,
    shopify_units_state TEXT NOT NULL
        CHECK (shopify_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    shopify_units_value NUMERIC(12,4),
    qualifying_units_state TEXT NOT NULL
        CHECK (qualifying_units_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    qualifying_units_value NUMERIC(12,4),
    assortment_scope_state TEXT NOT NULL
        CHECK (assortment_scope_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_scope_value TEXT,
    assortment_group_state TEXT NOT NULL
        CHECK (assortment_group_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortment_group_value TEXT,
    assortable_state TEXT NOT NULL
        CHECK (assortable_state IN ('ABSENT','EXPLICIT_NULL','VALUE')),
    assortable_value BOOLEAN,
    reviewed_facts JSONB NOT NULL CHECK (jsonb_typeof(reviewed_facts)='object'),
    component_relationships JSONB NOT NULL CHECK (jsonb_typeof(component_relationships)='array'),
    owner_clarifications JSONB NOT NULL CHECK (jsonb_typeof(owner_clarifications)='array'),
    historical_capture_scope JSONB NOT NULL CHECK (jsonb_typeof(historical_capture_scope)='object'),
    expected_batch_payload_sha256 TEXT NOT NULL
        CHECK (expected_batch_payload_sha256 ~ '^[0-9a-f]{64}$'),
    expected_candidate_sha256 TEXT NOT NULL
        CHECK (expected_candidate_sha256 ~ '^[0-9a-f]{64}$'),
    expected_catalog_sha256 TEXT
        CHECK (expected_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    expected_vendor_sha256 TEXT
        CHECK (expected_vendor_sha256 ~ '^[0-9a-f]{64}$'),
    expected_rejection_memory_sha256 TEXT
        CHECK (expected_rejection_memory_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_set_sha256 TEXT NOT NULL CHECK (evidence_set_sha256 ~ '^[0-9a-f]{64}$'),
    human_principal_ref TEXT,
    human_role_ref TEXT,
    human_authn_context_sha256 TEXT,
    preview_sha256 TEXT,
    confirmation_sha256 TEXT,
    service_principal_ref TEXT,
    policy_ref TEXT,
    policy_version TEXT,
    policy_publication_sha256 TEXT,
    policy_predicate_version TEXT,
    policy_predicate_result_sha256 TEXT,
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    result_offer_id BIGINT REFERENCES "qa_mapping_test".supplier_offers(offer_id) ON DELETE RESTRICT,
    offer_link_kind TEXT CHECK (offer_link_kind IN ('CREATED_INACTIVE','LINKED_EXISTING')),
    result_offer_contract_sha256 TEXT,
    result_rejection_id BIGINT REFERENCES "qa_mapping_test".mapping_rejections(rejection_id) ON DELETE RESTRICT,
    result_rejection_contract_sha256 TEXT,
    supersedes_mapping_decision_id UUID
        REFERENCES "qa_mapping_test".supplier_mapping_decisions(mapping_decision_id) ON DELETE RESTRICT,
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    decided_txid BIGINT NOT NULL DEFAULT txid_current(),
    FOREIGN KEY (review_batch_id,candidate_id)
        REFERENCES "qa_mapping_test".supplier_mapping_review_candidates(review_batch_id,candidate_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_supplier_mapping_decisions_idempotency
        UNIQUE (decision_idempotency_key),
    UNIQUE (mapping_decision_id,result_offer_id),
    CHECK ((distributor_product_id_state='VALUE')=
           (distributor_product_id_value IS NOT NULL)),
    CHECK ((supplier_code_state='VALUE')=(supplier_code_value IS NOT NULL)),
    CHECK ((size_state='VALUE')=(size_value IS NOT NULL)),
    CHECK ((raw_pack_state='VALUE')=(raw_pack_value IS NOT NULL)),
    CHECK ((shopify_units_state='VALUE')=(shopify_units_value IS NOT NULL)),
    CHECK ((qualifying_units_state='VALUE')=(qualifying_units_value IS NOT NULL)),
    CHECK ((assortment_scope_state='VALUE')=(assortment_scope_value IS NOT NULL)),
    CHECK (assortment_scope_value IS NULL OR
           assortment_scope_value IN ('PRODUCT','EXPLICIT_CROSS_PRODUCT','NONE')),
    CHECK ((assortment_group_state='VALUE')=(assortment_group_value IS NOT NULL)),
    CHECK ((assortable_state='VALUE')=(assortable_value IS NOT NULL)),
    CHECK (shopify_units_value IS NULL OR shopify_units_value>0),
    CHECK (qualifying_units_value IS NULL OR qualifying_units_value>0),
    CHECK (human_authn_context_sha256 IS NULL OR
           human_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (preview_sha256 IS NULL OR preview_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (confirmation_sha256 IS NULL OR confirmation_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (policy_publication_sha256 IS NULL OR
           policy_publication_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (policy_predicate_result_sha256 IS NULL OR
           policy_predicate_result_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (result_offer_contract_sha256 IS NULL OR
           result_offer_contract_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (result_rejection_contract_sha256 IS NULL OR
           result_rejection_contract_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (
        (action='APPROVE_MAPPING'
         AND authority_kind IS NOT NULL
         AND variant_id IS NOT NULL AND vendor_id IS NOT NULL
         AND supplier_identity_key_sha256 IS NOT NULL
         AND operational_offer_key_sha256 IS NOT NULL
         AND result_offer_package_type IS NOT NULL
         AND expected_catalog_sha256 IS NOT NULL
         AND expected_vendor_sha256 IS NOT NULL
         AND expected_rejection_memory_sha256 IS NOT NULL
         AND result_offer_id IS NOT NULL
         AND offer_link_kind IS NOT NULL
         AND result_offer_contract_sha256 IS NOT NULL
         AND result_rejection_id IS NULL
         AND result_rejection_contract_sha256 IS NULL)
        OR
        (action='REJECT_MAPPING' AND authority_kind IS NULL
         AND variant_id IS NOT NULL AND vendor_id IS NOT NULL
         AND supplier_identity_key_sha256 IS NOT NULL
         AND result_offer_package_type IS NULL
         AND expected_catalog_sha256 IS NOT NULL
         AND expected_vendor_sha256 IS NOT NULL
         AND expected_rejection_memory_sha256 IS NOT NULL
         AND result_offer_id IS NULL AND offer_link_kind IS NULL
         AND result_offer_contract_sha256 IS NULL AND result_rejection_id IS NOT NULL
         AND result_rejection_contract_sha256 IS NOT NULL)
        OR
        (action='DEFER' AND authority_kind IS NULL
         AND variant_id IS NULL AND vendor_id IS NULL
         AND supplier_identity_key_sha256 IS NULL
         AND operational_offer_key_sha256 IS NULL
         AND result_offer_package_type IS NULL
         AND expected_catalog_sha256 IS NULL
         AND expected_vendor_sha256 IS NULL
         AND expected_rejection_memory_sha256 IS NULL
         AND result_offer_id IS NULL AND offer_link_kind IS NULL
         AND result_offer_contract_sha256 IS NULL AND result_rejection_id IS NULL
         AND result_rejection_contract_sha256 IS NULL)
    ),
    CHECK (
        (decision_origin='HUMAN'
         AND human_principal_ref IS NOT NULL AND btrim(human_principal_ref)<>''
         AND human_role_ref IS NOT NULL AND btrim(human_role_ref)<>''
         AND human_authn_context_sha256 IS NOT NULL
         AND preview_sha256 IS NOT NULL
         AND confirmation_sha256 IS NOT NULL
         AND service_principal_ref IS NULL AND policy_ref IS NULL
         AND policy_version IS NULL AND policy_publication_sha256 IS NULL
         AND policy_predicate_version IS NULL AND policy_predicate_result_sha256 IS NULL
         AND (authority_kind IS NULL OR authority_kind='HUMAN_APPROVED'))
        OR
        (decision_origin='POLICY' AND action='APPROVE_MAPPING'
         AND authority_kind='POLICY_APPROVED'
         AND human_principal_ref IS NULL AND human_role_ref IS NULL
         AND human_authn_context_sha256 IS NULL
         AND preview_sha256 IS NULL AND confirmation_sha256 IS NULL
         AND service_principal_ref IS NOT NULL AND btrim(service_principal_ref)<>''
         AND policy_ref IS NOT NULL AND btrim(policy_ref)<>''
         AND policy_version IS NOT NULL AND btrim(policy_version)<>''
         AND policy_publication_sha256 IS NOT NULL
         AND policy_predicate_version IS NOT NULL AND btrim(policy_predicate_version)<>''
         AND policy_predicate_result_sha256 IS NOT NULL)
    )
);

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_decision_preview_sha256(
    d "qa_mapping_test".supplier_mapping_decisions
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(
        (to_jsonb(d)-ARRAY[
            'mapping_decision_id','request_sha256','canonical_payload','payload_sha256',
            'decided_at','decided_txid','human_principal_ref','human_role_ref',
            'human_authn_context_sha256','preview_sha256','confirmation_sha256',
            'service_principal_ref','policy_ref','policy_version',
            'policy_publication_sha256','policy_predicate_version',
            'policy_predicate_result_sha256','result_offer_id','result_rejection_id'
        ]) || jsonb_build_object(
            'confirmation_contract_version','HUMAN_MAPPING_PREVIEW_V1',
            'confirmed_existing_offer_id',CASE
                WHEN d.offer_link_kind='LINKED_EXISTING' THEN d.result_offer_id
                ELSE NULL
            END
        )
    )
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_decision_confirmation_sha256(
    d "qa_mapping_test".supplier_mapping_decisions
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'confirmation_contract_version','HUMAN_MAPPING_CONFIRMATION_V1',
        'preview_sha256',d.preview_sha256,
        'principal_ref',d.human_principal_ref,
        'role_ref',d.human_role_ref,
        'action',d.action,
        'idempotency_key',d.decision_idempotency_key
    ))
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_decision_request_sha256(
    d "qa_mapping_test".supplier_mapping_decisions
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(
        (to_jsonb(d)-ARRAY[
            'mapping_decision_id','request_sha256','canonical_payload',
            'payload_sha256','decided_at','decided_txid',
            'result_offer_id','result_rejection_id'
        ]) || jsonb_build_object(
            'request_contract_version','PERSISTENT_MAPPING_DECISION_REQUEST_V1',
            'confirmed_existing_offer_id',CASE
                WHEN d.offer_link_kind='LINKED_EXISTING' THEN d.result_offer_id
                ELSE NULL
            END
        )
    )
$$;

CREATE UNIQUE INDEX uq_mapping_decision_root_per_scope
    ON "qa_mapping_test".supplier_mapping_decisions(decision_scope_sha256)
    WHERE supersedes_mapping_decision_id IS NULL;
CREATE UNIQUE INDEX uq_mapping_decision_successor
    ON "qa_mapping_test".supplier_mapping_decisions(supersedes_mapping_decision_id)
    WHERE supersedes_mapping_decision_id IS NOT NULL;
CREATE INDEX idx_mapping_decisions_candidate
    ON "qa_mapping_test".supplier_mapping_decisions(review_batch_id,candidate_id,decided_at);
CREATE INDEX idx_mapping_decisions_offer_key
    ON "qa_mapping_test".supplier_mapping_decisions(operational_offer_key_sha256,decided_at);
CREATE INDEX idx_mapping_decisions_supplier_identity
    ON "qa_mapping_test".supplier_mapping_decisions(supplier_identity_key_sha256,decided_at);
CREATE INDEX idx_mapping_decisions_result_offer
    ON "qa_mapping_test".supplier_mapping_decisions(result_offer_id)
    WHERE result_offer_id IS NOT NULL;

CREATE OR REPLACE FUNCTION "qa_mapping_test".validate_supplier_mapping_decision_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE
    b "qa_mapping_test".supplier_mapping_review_batches%ROWTYPE;
    c "qa_mapping_test".supplier_mapping_review_candidates%ROWTYPE;
    o "qa_mapping_test".supplier_offers%ROWTYPE;
    prior "qa_mapping_test".supplier_mapping_decisions%ROWTYPE;
    expected_payload JSONB;
    rejection_source_key TEXT;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'mapping decisions require SERIALIZABLE isolation';
    END IF;
    PERFORM "qa_mapping_test".persistent_mapping_require_enabled_capability(
        CASE NEW.decision_origin
          WHEN 'HUMAN' THEN 'human_mapping_writes_enabled'
          ELSE 'policy_mapping_writes_enabled'
        END
    );
    IF NEW.decided_txid<>txid_current() THEN
        RAISE EXCEPTION 'mapping decision transaction identity differs';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('supplier-mapping:'||NEW.decision_scope_sha256,0)
    );
    IF NEW.operational_offer_key_sha256 IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended('supplier-offer-key:'||NEW.operational_offer_key_sha256,0)
        );
    END IF;

    SELECT * INTO b FROM "qa_mapping_test".supplier_mapping_review_batches
     WHERE review_batch_id=NEW.review_batch_id FOR SHARE;
    SELECT * INTO c FROM "qa_mapping_test".supplier_mapping_review_candidates
     WHERE review_batch_id=NEW.review_batch_id AND candidate_id=NEW.candidate_id
     FOR SHARE;
    IF b.review_batch_id IS NULL OR c.candidate_id IS NULL THEN
        RAISE EXCEPTION 'decision batch or candidate is absent';
    END IF;

    IF NEW.expected_batch_payload_sha256 IS DISTINCT FROM b.payload_sha256
       OR NEW.expected_candidate_sha256 IS DISTINCT FROM c.candidate_sha256
       OR NEW.decision_scope_sha256 IS DISTINCT FROM c.decision_scope_sha256
       OR NEW.printed_occurrence_sha256 IS DISTINCT FROM c.printed_occurrence_sha256
       OR NEW.distributor_product_id_state IS DISTINCT FROM c.distributor_product_id_state
       OR NEW.distributor_product_id_value IS DISTINCT FROM c.distributor_product_id_value
       OR NEW.supplier_code_state IS DISTINCT FROM c.supplier_code_state
       OR NEW.supplier_code_value IS DISTINCT FROM c.supplier_code_value
       OR NEW.offer_class IS DISTINCT FROM c.offer_class
       OR NEW.size_state IS DISTINCT FROM c.size_state
       OR NEW.size_value IS DISTINCT FROM c.size_value
       OR NEW.raw_pack_state IS DISTINCT FROM c.raw_pack_state
       OR NEW.raw_pack_value IS DISTINCT FROM c.raw_pack_value
       OR NEW.shopify_units_state IS DISTINCT FROM c.shopify_units_state
       OR NEW.shopify_units_value IS DISTINCT FROM c.shopify_units_value
       OR NEW.qualifying_units_state IS DISTINCT FROM c.qualifying_units_state
       OR NEW.qualifying_units_value IS DISTINCT FROM c.qualifying_units_value
       OR NEW.assortment_scope_state IS DISTINCT FROM c.assortment_scope_state
       OR NEW.assortment_scope_value IS DISTINCT FROM c.assortment_scope_value
       OR NEW.assortment_group_state IS DISTINCT FROM c.assortment_group_state
       OR NEW.assortment_group_value IS DISTINCT FROM c.assortment_group_value
       OR NEW.assortable_state IS DISTINCT FROM c.assortable_state
       OR NEW.assortable_value IS DISTINCT FROM c.assortable_value
       OR NEW.reviewed_facts IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_candidate_reviewed_facts(c)
       OR NEW.evidence_set_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_candidate_evidence_set_sha256(c)
       OR NEW.component_relationships IS DISTINCT FROM c.component_relationships
       OR NEW.owner_clarifications IS DISTINCT FROM c.owner_clarifications
       OR NEW.historical_capture_scope IS DISTINCT FROM c.historical_capture_scope THEN
        RAISE EXCEPTION 'mapping preview is stale or candidate identity differs';
    END IF;
    IF NEW.action='DEFER' THEN
        IF NEW.variant_id IS NOT NULL OR NEW.vendor_id IS NOT NULL
           OR NEW.supplier_identity_key_sha256 IS NOT NULL
           OR NEW.operational_offer_key_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'defer cannot invent an operational identity';
        END IF;
    ELSIF NEW.variant_id IS DISTINCT FROM c.proposed_variant_id
       OR NEW.vendor_id IS DISTINCT FROM c.proposed_vendor_id
       OR NEW.supplier_identity_key_sha256 IS DISTINCT FROM
            c.supplier_identity_key_sha256
       OR NEW.operational_offer_key_sha256 IS DISTINCT FROM
            c.operational_offer_key_sha256 THEN
        RAISE EXCEPTION 'mapping action target differs from candidate';
    END IF;

    IF NEW.supersedes_mapping_decision_id IS NOT NULL THEN
        SELECT * INTO prior FROM "qa_mapping_test".supplier_mapping_decisions
         WHERE mapping_decision_id=NEW.supersedes_mapping_decision_id FOR UPDATE;
        IF prior.mapping_decision_id IS NULL
           OR prior.decision_scope_sha256<>NEW.decision_scope_sha256
           OR EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions d
                       WHERE d.supersedes_mapping_decision_id=prior.mapping_decision_id) THEN
            RAISE EXCEPTION 'stale or wrong-scope prior mapping decision';
        END IF;
    END IF;

    IF NEW.decision_origin='HUMAN' THEN
        PERFORM "qa_mapping_test".persistent_mapping_require_human_context(
            NEW.human_principal_ref,NEW.human_role_ref,
            NEW.human_authn_context_sha256,'SUPPLIER_MAPPING_DECIDE'
        );
        IF NEW.preview_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_decision_preview_sha256(NEW)
           OR NEW.confirmation_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_decision_confirmation_sha256(NEW) THEN
            RAISE EXCEPTION 'mapping preview or separate confirmation is not bound';
        END IF;
    ELSIF NEW.offer_class<>'REGULAR'
       OR NEW.result_offer_package_type<>'STANDARD'
       OR NEW.supplier_code_state<>'VALUE'
       OR btrim(NEW.supplier_code_value)=''
       OR NEW.supplier_code_value<>btrim(NEW.supplier_code_value) THEN
        RAISE EXCEPTION 'policy mapping is limited to an exact regular standard offer';
    ELSIF NOT "qa_mapping_test".supplier_mapping_policy_is_published(
        NEW.policy_ref,NEW.policy_version,NEW.policy_publication_sha256,
        NEW.evidence_set_sha256
    ) THEN
        RAISE EXCEPTION 'no published mapping policy authorizes this decision';
    END IF;

    rejection_source_key := CASE
        WHEN NEW.supplier_identity_key_sha256 IS NULL THEN NULL
        ELSE 'persistent-mapping:'||NEW.supplier_identity_key_sha256
    END;
    IF NEW.action='APPROVE_MAPPING' THEN
        PERFORM 1 FROM "qa_mapping_test".variants WHERE variant_id=NEW.variant_id FOR SHARE;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM "qa_mapping_test".vendors v
             WHERE v.vendor_id=NEW.vendor_id AND v.active FOR SHARE
        ) THEN
            RAISE EXCEPTION 'mapping approval requires an active canonical Variant and vendor';
        END IF;
        IF NEW.expected_catalog_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_catalog_fingerprint(NEW.variant_id)
           OR NEW.expected_vendor_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_vendor_fingerprint(NEW.vendor_id)
           OR NEW.expected_rejection_memory_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_rejection_fingerprint(
                    NEW.vendor_id,rejection_source_key,NULL
                ) THEN
            RAISE EXCEPTION 'mapping approval catalog, vendor, or rejection preview is stale';
        END IF;
        IF b.structural_state<>'READY' OR b.source_evidence_state<>'READY'
           OR b.semantic_state<>'READY' OR b.source_is_simulation
           OR jsonb_array_length(c.blockers)<>0 THEN
            RAISE EXCEPTION 'blocked or simulated evidence cannot approve a mapping';
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM jsonb_array_elements(c.independent_linkage_evidence) evidence
             WHERE evidence->>'evidence_mode'='DETERMINISTIC_INDEPENDENT'
               AND evidence->>'origin_sha256' ~ '^[0-9a-f]{64}$'
               AND evidence->>'observation_sha256' ~ '^[0-9a-f]{64}$'
               AND evidence->>'origin_sha256'<>c.source_file_sha256
        ) OR EXISTS (
            SELECT 1
              FROM jsonb_array_elements(c.independent_linkage_evidence) evidence
             WHERE evidence->>'evidence_mode'='FUZZY_SCORE'
               AND evidence->>'authoritative'='true'
        ) THEN
            RAISE EXCEPTION
                'mapping approval requires independent deterministic evidence; fuzzy is never authority';
        END IF;
        IF NEW.distributor_product_id_state<>'VALUE'
           OR btrim(NEW.distributor_product_id_value)=''
           OR (NEW.supplier_code_state='VALUE' AND (
                btrim(NEW.supplier_code_value)=''
                OR NEW.supplier_code_value<>btrim(NEW.supplier_code_value)
              ))
           OR NEW.offer_class='UNKNOWN'
           OR NEW.assortment_scope_state<>'VALUE'
           OR NEW.result_offer_package_type IS DISTINCT FROM (CASE NEW.offer_class
                WHEN 'REGULAR' THEN 'STANDARD'
                WHEN 'GIFT' THEN 'GIFT'
                WHEN 'SPECIAL' THEN 'SPECIAL'
                WHEN 'ALTERNATE' THEN 'ALTERNATE'
                WHEN 'COMPONENT' THEN 'COMPONENT'
                WHEN 'COMBO' THEN 'COMBO'
              END) THEN
            RAISE EXCEPTION 'approval lacks a supported exact operational offer identity';
        END IF;
        IF NOT "qa_mapping_test".is_procurement_eligible_variant(NEW.variant_id) THEN
            RAISE EXCEPTION 'mapping approval requires an active canonical Variant and vendor';
        END IF;
        IF EXISTS (
            SELECT 1 FROM "qa_mapping_test".mapping_rejections r
             WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
               AND r.vendor_id=NEW.vendor_id
               AND r.rejected_variant_id=NEW.variant_id
               AND r.source_key=rejection_source_key
        ) THEN
            RAISE EXCEPTION 'active rejection memory blocks mapping approval';
        END IF;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('supplier-offer-id:'||NEW.result_offer_id,0)
        );
        SELECT * INTO o FROM "qa_mapping_test".supplier_offers
         WHERE offer_id=NEW.result_offer_id FOR SHARE;
        IF o.offer_id IS NULL
           OR o.variant_id<>NEW.variant_id OR o.vendor_id<>NEW.vendor_id
           OR o.supplier_sku IS DISTINCT FROM NEW.supplier_code_value
           OR o.package_type<>NEW.result_offer_package_type
           OR o.size_text IS DISTINCT FROM NEW.size_value
           OR o.raw_pack IS DISTINCT FROM NEW.raw_pack_value
           OR o.shopify_units_per_case IS DISTINCT FROM NEW.shopify_units_value
           OR o.qualifying_units_per_case IS DISTINCT FROM NEW.qualifying_units_value
           OR o.assortment_scope IS DISTINCT FROM NEW.assortment_scope_value
           OR o.assortment_group IS DISTINCT FROM NEW.assortment_group_value
           OR o.assortable IS DISTINCT FROM NEW.assortable_value
           OR o.confidence<>'VERIFIED'
           OR "qa_mapping_test".persistent_mapping_offer_fingerprint(o.offer_id)
                IS DISTINCT FROM NEW.result_offer_contract_sha256 THEN
            RAISE EXCEPTION 'resulting supplier offer contract differs';
        END IF;
        IF NEW.offer_link_kind='CREATED_INACTIVE' AND (
            o.active
            OR o.source_file IS DISTINCT FROM c.source_file_name
            OR o.source_page IS DISTINCT FROM c.source_page_start
            OR o.valid_from IS NOT NULL OR o.valid_to IS NOT NULL
            OR o.replaces_offer_id IS NOT NULL
            OR "qa_mapping_test".persistent_mapping_offer_has_prior_references(o.offer_id)
        ) THEN
            RAISE EXCEPTION 'mapping-created supplier offer must be inactive and unreferenced';
        END IF;
        IF EXISTS (
            SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions d
             WHERE d.action='APPROVE_MAPPING'
               AND d.operational_offer_key_sha256=NEW.operational_offer_key_sha256
               AND d.result_offer_id<>NEW.result_offer_id
        ) THEN
            RAISE EXCEPTION 'equivalent printed occurrences must share one operational offer';
        END IF;
        IF EXISTS (
            SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions d
             WHERE d.action='APPROVE_MAPPING'
               AND d.result_offer_id=NEW.result_offer_id
               AND d.operational_offer_key_sha256<>NEW.operational_offer_key_sha256
        ) THEN
            RAISE EXCEPTION 'one operational offer cannot represent different material identities';
        END IF;
    ELSIF NEW.action='REJECT_MAPPING' THEN
        PERFORM 1 FROM "qa_mapping_test".variants WHERE variant_id=NEW.variant_id FOR SHARE;
        IF NOT FOUND OR NOT EXISTS (
            SELECT 1 FROM "qa_mapping_test".vendors v WHERE v.vendor_id=NEW.vendor_id FOR SHARE
        ) OR NEW.distributor_product_id_state<>'VALUE'
          OR btrim(NEW.distributor_product_id_value)=''
          OR rejection_source_key IS NULL THEN
            RAISE EXCEPTION 'targeted rejection requires exact Variant, vendor, and supplier identity';
        END IF;
        IF NEW.expected_catalog_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_catalog_fingerprint(NEW.variant_id)
           OR NEW.expected_vendor_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_vendor_fingerprint(NEW.vendor_id)
           OR NEW.expected_rejection_memory_sha256 IS DISTINCT FROM
                "qa_mapping_test".persistent_mapping_rejection_fingerprint(
                    NEW.vendor_id,rejection_source_key,NEW.result_rejection_id
                ) THEN
            RAISE EXCEPTION 'mapping rejection target or evidence preview is stale';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM "qa_mapping_test".mapping_rejections r
             WHERE r.rejection_id=NEW.result_rejection_id AND r.active
               AND r.mapping_type='SUPPLIER_OFFER'
               AND r.vendor_id=NEW.vendor_id
               AND r.rejected_variant_id=NEW.variant_id
               AND r.source_key=rejection_source_key
               AND r.rejected_by IS NOT DISTINCT FROM COALESCE(
                    NEW.human_principal_ref,NEW.service_principal_ref
               )
               AND "qa_mapping_test".persistent_mapping_rejection_contract_fingerprint(r.rejection_id)
                    IS NOT DISTINCT FROM NEW.result_rejection_contract_sha256
        ) THEN
            RAISE EXCEPTION 'mapping rejection result differs from decision scope';
        END IF;
    END IF;

    expected_payload := to_jsonb(NEW)-ARRAY[
        'mapping_decision_id','decision_idempotency_key','canonical_payload',
        'payload_sha256','decided_at','decided_txid'
    ];
    IF NEW.request_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_decision_request_sha256(NEW)
       OR NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'mapping decision request, canonical payload, or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_supplier_mapping_decision_insert
    ON "qa_mapping_test".supplier_mapping_decisions;
CREATE TRIGGER trg_validate_supplier_mapping_decision_insert
BEFORE INSERT ON "qa_mapping_test".supplier_mapping_decisions
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".validate_supplier_mapping_decision_insert();
DROP TRIGGER IF EXISTS trg_immutable_supplier_mapping_decisions
    ON "qa_mapping_test".supplier_mapping_decisions;
CREATE TRIGGER trg_immutable_supplier_mapping_decisions
BEFORE UPDATE OR DELETE ON "qa_mapping_test".supplier_mapping_decisions
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".reject_persistent_mapping_mutation();

CREATE OR REPLACE FUNCTION "qa_mapping_test".protect_persistent_mapping_rejection_contract()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions d
         WHERE d.action='REJECT_MAPPING'
           AND d.result_rejection_id=OLD.rejection_id
    ) THEN
        RAISE EXCEPTION 'rejection linked by persistent mapping authority is immutable';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_persistent_mapping_rejection_contract
    ON "qa_mapping_test".mapping_rejections;
CREATE TRIGGER trg_protect_persistent_mapping_rejection_contract
BEFORE UPDATE OR DELETE ON "qa_mapping_test".mapping_rejections
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".protect_persistent_mapping_rejection_contract();

CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_offer_selection_events (
    selection_event_id UUID PRIMARY KEY,
    selection_idempotency_key UUID NOT NULL,
    variant_id TEXT NOT NULL REFERENCES "qa_mapping_test".variants(variant_id) ON DELETE RESTRICT,
    selection_scope TEXT NOT NULL DEFAULT 'ROUTINE_PROCUREMENT_STANDARD'
        CHECK (selection_scope='ROUTINE_PROCUREMENT_STANDARD'),
    action TEXT NOT NULL CHECK (action IN ('SELECT','CLEAR')),
    selected_offer_id BIGINT REFERENCES "qa_mapping_test".supplier_offers(offer_id) ON DELETE RESTRICT,
    mapping_decision_id UUID,
    expected_prior_event_id UUID
        REFERENCES "qa_mapping_test".supplier_offer_selection_events(selection_event_id) ON DELETE RESTRICT,
    expected_prior_head_version BIGINT NOT NULL CHECK (expected_prior_head_version>=0),
    expected_mapping_decision_sha256 TEXT,
    expected_offer_contract_sha256 TEXT,
    expected_catalog_sha256 TEXT NOT NULL CHECK (expected_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    expected_vendor_sha256 TEXT,
    expected_rejection_memory_sha256 TEXT,
    effective_from DATE NOT NULL,
    effective_through DATE,
    human_principal_ref TEXT NOT NULL CHECK (btrim(human_principal_ref)<>''),
    human_role_ref TEXT NOT NULL CHECK (btrim(human_role_ref)<>''),
    human_authn_context_sha256 TEXT NOT NULL
        CHECK (human_authn_context_sha256 ~ '^[0-9a-f]{64}$'),
    preview_sha256 TEXT NOT NULL CHECK (preview_sha256 ~ '^[0-9a-f]{64}$'),
    confirmation_sha256 TEXT NOT NULL CHECK (confirmation_sha256 ~ '^[0-9a-f]{64}$'),
    reason TEXT NOT NULL CHECK (btrim(reason)<>''),
    canonical_payload JSONB NOT NULL CHECK (jsonb_typeof(canonical_payload)='object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    selected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    selected_txid BIGINT NOT NULL DEFAULT txid_current(),
    FOREIGN KEY (mapping_decision_id,selected_offer_id)
        REFERENCES "qa_mapping_test".supplier_mapping_decisions(mapping_decision_id,result_offer_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_supplier_offer_selection_events_idempotency
        UNIQUE (selection_idempotency_key),
    CHECK (effective_through IS NULL OR effective_through>=effective_from),
    CHECK (
        (action='SELECT' AND selected_offer_id IS NOT NULL
         AND mapping_decision_id IS NOT NULL
         AND expected_mapping_decision_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_offer_contract_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_vendor_sha256 ~ '^[0-9a-f]{64}$'
         AND expected_rejection_memory_sha256 ~ '^[0-9a-f]{64}$')
        OR
        (action='CLEAR' AND selected_offer_id IS NULL
         AND mapping_decision_id IS NULL
         AND expected_mapping_decision_sha256 IS NULL
         AND expected_offer_contract_sha256 IS NULL
         AND expected_vendor_sha256 IS NULL
         AND expected_rejection_memory_sha256 IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS "qa_mapping_test".supplier_offer_selection_heads (
    variant_id TEXT NOT NULL REFERENCES "qa_mapping_test".variants(variant_id) ON DELETE RESTRICT,
    selection_scope TEXT NOT NULL DEFAULT 'ROUTINE_PROCUREMENT_STANDARD'
        CHECK (selection_scope='ROUTINE_PROCUREMENT_STANDARD'),
    selection_event_id UUID NOT NULL UNIQUE
        REFERENCES "qa_mapping_test".supplier_offer_selection_events(selection_event_id) ON DELETE RESTRICT,
    head_version BIGINT NOT NULL CHECK (head_version>=1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_txid BIGINT NOT NULL DEFAULT txid_current(),
    PRIMARY KEY (variant_id,selection_scope)
);

CREATE INDEX idx_offer_selection_events_offer
    ON "qa_mapping_test".supplier_offer_selection_events(selected_offer_id)
    WHERE selected_offer_id IS NOT NULL;
CREATE INDEX idx_offer_selection_events_mapping
    ON "qa_mapping_test".supplier_offer_selection_events(mapping_decision_id)
    WHERE mapping_decision_id IS NOT NULL;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_selection_preview_sha256(
    e "qa_mapping_test".supplier_offer_selection_events
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(
        (to_jsonb(e)-ARRAY[
            'selection_event_id','canonical_payload','payload_sha256',
            'selected_at','selected_txid','human_principal_ref','human_role_ref',
            'human_authn_context_sha256','preview_sha256','confirmation_sha256'
        ]) || jsonb_build_object(
            'confirmation_contract_version','HUMAN_ROUTINE_SELECTION_PREVIEW_V1'
        )
    )
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_selection_confirmation_sha256(
    e "qa_mapping_test".supplier_offer_selection_events
)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
    SELECT "qa_mapping_test".persistent_mapping_json_sha256(jsonb_build_object(
        'confirmation_contract_version','HUMAN_ROUTINE_SELECTION_CONFIRMATION_V1',
        'preview_sha256',e.preview_sha256,
        'principal_ref',e.human_principal_ref,
        'role_ref',e.human_role_ref,
        'action',e.action,
        'idempotency_key',e.selection_idempotency_key
    ))
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".validate_supplier_offer_selection_event_insert()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE
    current_head "qa_mapping_test".supplier_offer_selection_heads%ROWTYPE;
    mapping "qa_mapping_test".supplier_mapping_decisions%ROWTYPE;
    offer "qa_mapping_test".supplier_offers%ROWTYPE;
    candidate "qa_mapping_test".supplier_mapping_review_candidates%ROWTYPE;
    expected_payload JSONB;
BEGIN
    IF current_setting('transaction_isolation')<>'serializable' THEN
        RAISE EXCEPTION 'routine selection requires SERIALIZABLE isolation';
    END IF;
    PERFORM "qa_mapping_test".persistent_mapping_require_enabled_capability(
        'routine_selection_writes_enabled'
    );
    IF NEW.selected_txid<>txid_current() THEN
        RAISE EXCEPTION 'selection event transaction identity differs';
    END IF;
    PERFORM "qa_mapping_test".persistent_mapping_require_human_context(
        NEW.human_principal_ref,NEW.human_role_ref,
        NEW.human_authn_context_sha256,'ROUTINE_OFFER_SELECT'
    );
    IF NEW.preview_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_selection_preview_sha256(NEW)
       OR NEW.confirmation_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_selection_confirmation_sha256(NEW) THEN
        RAISE EXCEPTION 'selection preview or separate confirmation is not bound';
    END IF;
    PERFORM pg_advisory_xact_lock(
        hashtextextended('routine-offer-selection:'||NEW.variant_id,0)
    );
    SELECT * INTO current_head FROM "qa_mapping_test".supplier_offer_selection_heads
     WHERE variant_id=NEW.variant_id AND selection_scope=NEW.selection_scope
     FOR UPDATE;
    IF current_head.selection_event_id IS NULL THEN
        IF NEW.expected_prior_event_id IS NOT NULL
           OR NEW.expected_prior_head_version<>0 THEN
            RAISE EXCEPTION 'stale prior selection head';
        END IF;
    ELSIF NEW.expected_prior_event_id IS DISTINCT FROM current_head.selection_event_id
       OR NEW.expected_prior_head_version<>current_head.head_version THEN
        RAISE EXCEPTION 'stale prior selection head';
    END IF;
    IF NEW.expected_catalog_sha256 IS DISTINCT FROM
        "qa_mapping_test".persistent_mapping_catalog_fingerprint(NEW.variant_id) THEN
        RAISE EXCEPTION 'selection catalog preview is stale';
    END IF;

    IF NEW.action='SELECT' THEN
        SELECT * INTO mapping FROM "qa_mapping_test".supplier_mapping_decisions
         WHERE mapping_decision_id=NEW.mapping_decision_id FOR SHARE;
        SELECT * INTO offer FROM "qa_mapping_test".supplier_offers
         WHERE offer_id=NEW.selected_offer_id FOR SHARE;
        SELECT * INTO candidate FROM "qa_mapping_test".supplier_mapping_review_candidates
         WHERE candidate_id=mapping.candidate_id FOR SHARE;
        IF mapping.mapping_decision_id IS NULL OR mapping.action<>'APPROVE_MAPPING'
           OR mapping.variant_id<>NEW.variant_id
           OR NEW.selection_idempotency_key=mapping.decision_idempotency_key
           OR (mapping.decision_origin='HUMAN' AND (
                NEW.preview_sha256=mapping.preview_sha256
                OR NEW.confirmation_sha256=mapping.confirmation_sha256
              ))
           OR EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions successor
                       WHERE successor.supersedes_mapping_decision_id=mapping.mapping_decision_id)
           OR candidate.offer_class<>'REGULAR'
           OR mapping.result_offer_package_type<>'STANDARD'
           OR mapping.supplier_code_state<>'VALUE'
           OR offer.offer_id IS NULL OR offer.variant_id<>NEW.variant_id
           OR offer.package_type<>'STANDARD'
           OR "qa_mapping_test".persistent_mapping_json_sha256(mapping.canonical_payload)
                IS DISTINCT FROM NEW.expected_mapping_decision_sha256
           OR "qa_mapping_test".persistent_mapping_offer_fingerprint(offer.offer_id)
                IS DISTINCT FROM NEW.expected_offer_contract_sha256
           OR "qa_mapping_test".persistent_mapping_vendor_fingerprint(mapping.vendor_id)
                IS DISTINCT FROM NEW.expected_vendor_sha256
           OR "qa_mapping_test".persistent_mapping_rejection_fingerprint(
                mapping.vendor_id,
                'persistent-mapping:'||mapping.supplier_identity_key_sha256,
                NULL
              ) IS DISTINCT FROM NEW.expected_rejection_memory_sha256
           OR NOT "qa_mapping_test".is_procurement_eligible_variant(NEW.variant_id)
           OR NOT EXISTS (SELECT 1 FROM "qa_mapping_test".vendors v
                           WHERE v.vendor_id=mapping.vendor_id AND v.active)
           OR (offer.valid_from IS NOT NULL AND offer.valid_from>NEW.effective_from)
           OR (offer.valid_to IS NOT NULL AND offer.valid_to<NEW.effective_from)
           OR EXISTS (
                SELECT 1 FROM "qa_mapping_test".mapping_rejections r
                 WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
                   AND r.vendor_id=mapping.vendor_id
                   AND r.rejected_variant_id=NEW.variant_id
                   AND r.source_key='persistent-mapping:'||mapping.supplier_identity_key_sha256
              )
           OR EXISTS (
                SELECT 1 FROM "qa_mapping_test".supplier_offers reused
                 WHERE reused.vendor_id=mapping.vendor_id
                   AND reused.supplier_sku=mapping.supplier_code_value
                   AND reused.offer_id<>offer.offer_id
                   AND reused.active
              ) THEN
            RAISE EXCEPTION 'selected offer is stale, ineligible, rejected, or not regular';
        END IF;
        IF NOT offer.active AND (
            NOT EXISTS (
                SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions origin
                 WHERE origin.result_offer_id=offer.offer_id
                   AND origin.offer_link_kind='CREATED_INACTIVE'
            )
            OR "qa_mapping_test".persistent_mapping_offer_has_prior_references(offer.offer_id)
        ) THEN
            RAISE EXCEPTION 'inactive selection is not a fresh unpriced mapping result';
        END IF;
    END IF;

    expected_payload := to_jsonb(NEW)-ARRAY[
        'selection_event_id','selection_idempotency_key','canonical_payload',
        'payload_sha256','selected_at','selected_txid'
    ];
    IF NEW.canonical_payload IS DISTINCT FROM expected_payload
       OR NEW.payload_sha256 IS DISTINCT FROM
            "qa_mapping_test".persistent_mapping_json_sha256(expected_payload) THEN
        RAISE EXCEPTION 'selection canonical payload or fingerprint differs';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".validate_supplier_offer_selection_head_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE event "qa_mapping_test".supplier_offer_selection_events%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'routine selection head cannot be deleted';
    END IF;
    IF NEW.updated_txid<>txid_current() THEN
        RAISE EXCEPTION 'selection head transaction identity differs';
    END IF;
    SELECT * INTO event FROM "qa_mapping_test".supplier_offer_selection_events
     WHERE selection_event_id=NEW.selection_event_id;
    IF event.selection_event_id IS NULL OR event.selected_txid<>txid_current()
       OR event.variant_id<>NEW.variant_id
       OR event.selection_scope<>NEW.selection_scope
       OR NEW.head_version<>event.expected_prior_head_version+1 THEN
        RAISE EXCEPTION 'head must advance beside its exact new event';
    END IF;
    IF TG_OP='UPDATE' AND (
        ROW(NEW.variant_id,NEW.selection_scope)
            IS DISTINCT FROM ROW(OLD.variant_id,OLD.selection_scope)
        OR event.expected_prior_event_id IS DISTINCT FROM OLD.selection_event_id
        OR event.expected_prior_head_version<>OLD.head_version
    ) THEN
        RAISE EXCEPTION 'head update prior state differs';
    ELSIF TG_OP='INSERT' AND event.expected_prior_event_id IS NOT NULL THEN
        RAISE EXCEPTION 'initial head event unexpectedly has a predecessor';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".validate_supplier_offer_selection_event_committed()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM "qa_mapping_test".supplier_offer_selection_heads h
         WHERE h.variant_id=NEW.variant_id
           AND h.selection_scope=NEW.selection_scope
           AND h.selection_event_id=NEW.selection_event_id
           AND h.head_version=NEW.expected_prior_head_version+1
    ) THEN
        RAISE EXCEPTION 'selection event has no same-transaction head advancement';
    END IF;
    RETURN NULL;
END
$$;

DROP TRIGGER IF EXISTS trg_validate_offer_selection_event_insert
    ON "qa_mapping_test".supplier_offer_selection_events;
CREATE TRIGGER trg_validate_offer_selection_event_insert
BEFORE INSERT ON "qa_mapping_test".supplier_offer_selection_events
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".validate_supplier_offer_selection_event_insert();
DROP TRIGGER IF EXISTS trg_immutable_offer_selection_events
    ON "qa_mapping_test".supplier_offer_selection_events;
CREATE TRIGGER trg_immutable_offer_selection_events
BEFORE UPDATE OR DELETE ON "qa_mapping_test".supplier_offer_selection_events
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".reject_persistent_mapping_mutation();
DROP TRIGGER IF EXISTS trg_validate_offer_selection_head_change
    ON "qa_mapping_test".supplier_offer_selection_heads;
CREATE TRIGGER trg_validate_offer_selection_head_change
BEFORE INSERT OR UPDATE OR DELETE ON "qa_mapping_test".supplier_offer_selection_heads
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".validate_supplier_offer_selection_head_change();
DROP TRIGGER IF EXISTS trg_validate_offer_selection_event_committed
    ON "qa_mapping_test".supplier_offer_selection_events;
CREATE CONSTRAINT TRIGGER trg_validate_offer_selection_event_committed
AFTER INSERT ON "qa_mapping_test".supplier_offer_selection_events DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".validate_supplier_offer_selection_event_committed();

CREATE OR REPLACE FUNCTION "qa_mapping_test".protect_persistently_mapped_offer_contract()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions d
         WHERE d.action='APPROVE_MAPPING' AND d.result_offer_id=OLD.offer_id
    ) AND (
        TG_OP='DELETE'
        OR ROW(NEW.variant_id,NEW.vendor_id,NEW.supplier_sku,NEW.package_type,
               NEW.size_text,NEW.raw_pack,NEW.shopify_units_per_case,
               NEW.qualifying_units_per_case,NEW.assortment_scope,
               NEW.assortment_group,NEW.assortable,NEW.valid_from,NEW.valid_to,
               NEW.replaces_offer_id,NEW.source_file,NEW.source_page,NEW.confidence)
           IS DISTINCT FROM
           ROW(OLD.variant_id,OLD.vendor_id,OLD.supplier_sku,OLD.package_type,
               OLD.size_text,OLD.raw_pack,OLD.shopify_units_per_case,
               OLD.qualifying_units_per_case,OLD.assortment_scope,
               OLD.assortment_group,OLD.assortable,OLD.valid_from,OLD.valid_to,
               OLD.replaces_offer_id,OLD.source_file,OLD.source_page,OLD.confidence)
        OR NEW.active IS DISTINCT FROM OLD.active
    ) THEN
        RAISE EXCEPTION
            'mapped offer identity/activation requires a separately approved transition';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".protect_unactivated_mapped_offer_price()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE checked_offer_id BIGINT;
BEGIN
    checked_offer_id := CASE WHEN TG_OP='DELETE' THEN OLD.offer_id ELSE NEW.offer_id END;
    IF EXISTS (
        SELECT 1
          FROM "qa_mapping_test".supplier_mapping_decisions d
          JOIN "qa_mapping_test".supplier_offers o ON o.offer_id=d.result_offer_id
         WHERE d.action='APPROVE_MAPPING'
           AND d.offer_link_kind='CREATED_INACTIVE'
           AND d.result_offer_id=checked_offer_id
           AND NOT o.active
    ) THEN
        RAISE EXCEPTION 'inactive mapping result cannot receive operational pricing';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_protect_persistently_mapped_offer_contract
    ON "qa_mapping_test".supplier_offers;
CREATE TRIGGER trg_protect_persistently_mapped_offer_contract
BEFORE UPDATE OR DELETE ON "qa_mapping_test".supplier_offers
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".protect_persistently_mapped_offer_contract();
DROP TRIGGER IF EXISTS trg_protect_unactivated_mapped_offer_price ON "qa_mapping_test".prices;
CREATE TRIGGER trg_protect_unactivated_mapped_offer_price
BEFORE INSERT OR UPDATE OR DELETE ON "qa_mapping_test".prices
FOR EACH ROW EXECUTE FUNCTION "qa_mapping_test".protect_unactivated_mapped_offer_price();

CREATE OR REPLACE VIEW "qa_mapping_test".v_effective_supplier_mapping_decisions AS
SELECT d.*
  FROM "qa_mapping_test".supplier_mapping_decisions d
 WHERE NOT EXISTS (
    SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions successor
     WHERE successor.supersedes_mapping_decision_id=d.mapping_decision_id
 );

CREATE OR REPLACE VIEW "qa_mapping_test".v_supplier_offer_selection_diagnostics AS
SELECT
    h.variant_id,h.selection_scope,h.selection_event_id,h.head_version,
    e.action,e.selected_offer_id,e.mapping_decision_id,
    o.vendor_id,o.supplier_sku,o.package_type,o.active AS offer_active,
    CASE
      WHEN e.action='CLEAR' THEN 'EXPLICITLY_CLEARED'
      WHEN d.mapping_decision_id IS NULL THEN 'STALE_MAPPING_DECISION'
      WHEN "qa_mapping_test".persistent_mapping_json_sha256(d.canonical_payload)
           IS DISTINCT FROM e.expected_mapping_decision_sha256
        THEN 'STALE_MAPPING_DECISION'
      WHEN o.offer_id IS NULL OR "qa_mapping_test".persistent_mapping_offer_fingerprint(o.offer_id)
           IS DISTINCT FROM e.expected_offer_contract_sha256 THEN 'STALE_OFFER_CONTRACT'
      WHEN "qa_mapping_test".persistent_mapping_catalog_fingerprint(h.variant_id)
           IS DISTINCT FROM e.expected_catalog_sha256 THEN 'STALE_CATALOG'
      WHEN "qa_mapping_test".persistent_mapping_vendor_fingerprint(d.vendor_id)
           IS DISTINCT FROM e.expected_vendor_sha256 THEN 'STALE_VENDOR'
      WHEN "qa_mapping_test".persistent_mapping_rejection_fingerprint(
               d.vendor_id,'persistent-mapping:'||d.supplier_identity_key_sha256,
               NULL
           ) IS DISTINCT FROM e.expected_rejection_memory_sha256
        THEN 'STALE_REJECTION_MEMORY'
      WHEN NOT "qa_mapping_test".is_procurement_eligible_variant(h.variant_id) THEN 'INELIGIBLE_VARIANT'
      WHEN NOT v.active THEN 'INACTIVE_VENDOR'
      WHEN EXISTS (
        SELECT 1 FROM "qa_mapping_test".mapping_rejections r
         WHERE r.active AND r.mapping_type='SUPPLIER_OFFER'
           AND r.vendor_id=d.vendor_id AND r.rejected_variant_id=d.variant_id
           AND r.source_key='persistent-mapping:'||d.supplier_identity_key_sha256
      ) THEN 'ACTIVE_REJECTION'
      WHEN e.effective_from>current_date
        OR (e.effective_through IS NOT NULL AND e.effective_through<current_date)
        OR (o.valid_from IS NOT NULL AND o.valid_from>current_date)
        OR (o.valid_to IS NOT NULL AND o.valid_to<current_date) THEN 'OUTSIDE_VALIDITY'
      WHEN o.active THEN 'SELECTED_ACTIVE'
      ELSE 'SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION'
    END AS selection_state,
    FALSE AS recommendation_cutover_enabled
  FROM "qa_mapping_test".supplier_offer_selection_heads h
  JOIN "qa_mapping_test".supplier_offer_selection_events e
    ON e.selection_event_id=h.selection_event_id
  LEFT JOIN "qa_mapping_test".v_effective_supplier_mapping_decisions d
    ON d.mapping_decision_id=e.mapping_decision_id AND d.action='APPROVE_MAPPING'
  LEFT JOIN "qa_mapping_test".supplier_offers o ON o.offer_id=e.selected_offer_id
  LEFT JOIN "qa_mapping_test".vendors v ON v.vendor_id=o.vendor_id;

CREATE OR REPLACE VIEW "qa_mapping_test".v_selected_standard_supplier_offers AS
SELECT *
  FROM "qa_mapping_test".v_supplier_offer_selection_diagnostics
 WHERE selection_state IN
    ('SELECTED_ACTIVE','SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION');

CREATE OR REPLACE VIEW "qa_mapping_test".v_supplier_offer_selection_shadow AS
WITH legacy AS (
    SELECT o.variant_id,count(*) AS active_standard_count,
           min(o.offer_id) AS only_active_standard_offer_id
      FROM "qa_mapping_test".supplier_offers o
     WHERE o.active AND o.package_type='STANDARD'
     GROUP BY o.variant_id
)
SELECT d.*,
       COALESCE(l.active_standard_count,0) AS legacy_active_standard_count,
       CASE
         WHEN d.selection_state='EXPLICITLY_CLEARED' THEN 'HEAD_CLEARED'
         WHEN d.selection_state NOT IN
              ('SELECTED_ACTIVE','SELECTED_INACTIVE_AWAITING_SEPARATE_ACTIVATION')
              THEN 'HEAD_STALE_OR_INELIGIBLE'
         WHEN NOT d.offer_active THEN 'SELECTED_INACTIVE_NO_LEGACY_CHANGE'
         WHEN l.active_standard_count=1
              AND l.only_active_standard_offer_id=d.selected_offer_id THEN 'MATCH'
         WHEN l.active_standard_count=0 THEN 'LEGACY_HAS_NO_ACTIVE_STANDARD'
         WHEN l.active_standard_count>1 THEN 'LEGACY_HAS_MULTIPLE_ACTIVE_STANDARD'
         ELSE 'DIFFERENT_ACTIVE_STANDARD'
       END AS shadow_comparison
  FROM "qa_mapping_test".v_supplier_offer_selection_diagnostics d
  LEFT JOIN legacy l ON l.variant_id=d.variant_id;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_assert_safe_role_topology()
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $role_topology$
DECLARE
    target_schema CONSTANT TEXT := 'qa_mapping_test';
    maintenance_identity_config_sha256 CONSTANT TEXT :=
        'ab773e859feee527bba01c4ae3193fece3258bbe471d92595302531cbc270450';
    maintenance_pairs_sha256 CONSTANT TEXT :=
        '2d08568ea5df7ef3ee383381ae2d952c197ac87f8d8fffd4c1af877ca05ef69c';
    maintenance_pairs_canonical_json CONSTANT TEXT :=
        '[{"current_user":"qa_mapping_owner","session_user":"qa_release_login"}]';
    approved_maintenance_pairs JSONB;
    target_schema_oid OID;
    observed_pair JSONB;
    approved_invocation_oids OID[];
    unsafe_path JSONB;
BEGIN
    approved_maintenance_pairs := maintenance_pairs_canonical_json::jsonb;
    IF maintenance_identity_config_sha256 !~ '^[0-9a-f]{64}$'
       OR maintenance_pairs_sha256 !~ '^[0-9a-f]{64}$'
       OR pg_catalog.jsonb_typeof(approved_maintenance_pairs)<>'array'
       OR pg_catalog.jsonb_array_length(approved_maintenance_pairs)=0
       OR pg_catalog.encode(
            "qa_mapping_test".digest(
                pg_catalog.convert_to(maintenance_pairs_canonical_json,'UTF8'),
                'sha256'
            ),
            'hex'
          ) IS DISTINCT FROM maintenance_pairs_sha256 THEN
        RAISE EXCEPTION 'approved maintenance identity binding is absent or malformed';
    END IF;
    observed_pair := pg_catalog.jsonb_build_object(
        'session_user',session_user::text,
        'current_user',current_user::text
    );
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.jsonb_array_elements(approved_maintenance_pairs)
               AS item(pair)
         WHERE pair=observed_pair
    ) THEN
        RAISE EXCEPTION 'maintenance session/effective-role pair is not approved';
    END IF;
    WITH approved_role_names(role_name) AS (
        SELECT DISTINCT item.pair->>'session_user'
          FROM pg_catalog.jsonb_array_elements(approved_maintenance_pairs)
               AS item(pair)
        UNION
        SELECT DISTINCT item.pair->>'current_user'
          FROM pg_catalog.jsonb_array_elements(approved_maintenance_pairs)
               AS item(pair)
    )
    SELECT pg_catalog.array_agg(r.oid ORDER BY r.oid)
      INTO approved_invocation_oids
      FROM approved_role_names approved
      JOIN pg_catalog.pg_roles r ON r.rolname=approved.role_name
    HAVING pg_catalog.count(*)=(SELECT pg_catalog.count(*) FROM approved_role_names);
    IF approved_invocation_oids IS NULL THEN
        RAISE EXCEPTION 'approved maintenance roles are absent';
    END IF;

    SELECT n.oid INTO target_schema_oid
      FROM pg_catalog.pg_namespace n WHERE n.nspname=target_schema;
    IF target_schema_oid IS NULL
       OR target_schema_oid=pg_catalog.pg_my_temp_schema()
       OR pg_catalog.pg_is_other_temp_schema(target_schema_oid)
       OR target_schema ~ '^pg_(toast_)?temp_[0-9]+$' THEN
        RAISE EXCEPTION 'trusted target schema identity is absent or temporary';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES (session_user::text,current_user::text))
               AS observed(session_role,owner_role)
          LEFT JOIN pg_catalog.pg_roles member
            ON member.rolname=observed.session_role
          LEFT JOIN pg_catalog.pg_roles owner
            ON owner.rolname=observed.owner_role
          LEFT JOIN pg_catalog.pg_auth_members membership
            ON membership.member=member.oid AND membership.roleid=owner.oid
         WHERE member.oid IS NULL
            OR owner.oid IS NULL
            OR member.rolcanlogin IS NOT TRUE
            OR owner.rolcanlogin IS NOT FALSE
            OR membership.inherit_option IS NOT FALSE
            OR membership.set_option IS NOT TRUE
            OR membership.admin_option IS NOT FALSE
            OR pg_catalog.has_schema_privilege(
                   member.oid,target_schema_oid,'CREATE'
               ) IS NOT FALSE
    ) THEN
        RAISE EXCEPTION 'approved maintenance role topology differs';
    END IF;

    WITH RECURSIVE protected_owners(owner_oid) AS (
        SELECT n.nspowner FROM pg_catalog.pg_namespace n
         WHERE n.oid=target_schema_oid
        UNION
        SELECT c.relowner FROM pg_catalog.pg_class c
         WHERE c.relnamespace=target_schema_oid AND c.relname IN (
             'supplier_mapping_review_batches','supplier_mapping_review_candidates',
             'supplier_mapping_decisions','supplier_offer_selection_events',
             'supplier_offer_selection_heads','v_effective_supplier_mapping_decisions',
             'v_supplier_offer_selection_diagnostics',
             'v_selected_standard_supplier_offers',
             'v_supplier_offer_selection_shadow','variants','vendors',
             'supplier_offers','mapping_rejections','prices'
         )
        UNION
        SELECT p.proowner FROM pg_catalog.pg_proc p
         WHERE p.pronamespace=target_schema_oid AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    ), untrusted_logins AS (
        SELECT r.oid,r.rolname FROM pg_catalog.pg_roles r
         WHERE r.rolcanlogin AND NOT r.rolsuper
           AND NOT (r.oid=ANY(approved_invocation_oids))
    ), set_reachable(login_oid,login_name,assumed_oid,set_path) AS (
        SELECT u.oid,u.rolname,u.oid,ARRAY[u.oid]::oid[]
          FROM untrusted_logins u
        UNION ALL
        SELECT s.login_oid,s.login_name,m.roleid,s.set_path||m.roleid
          FROM set_reachable s
          JOIN pg_catalog.pg_auth_members m ON m.member=s.assumed_oid
         WHERE m.set_option AND NOT m.roleid=ANY(s.set_path)
    ), effective_after_set(
        login_oid,login_name,assumed_oid,effective_oid,set_path,inherit_path
    ) AS (
        SELECT s.login_oid,s.login_name,s.assumed_oid,s.assumed_oid,
               s.set_path,ARRAY[s.assumed_oid]::oid[]
          FROM set_reachable s
        UNION ALL
        SELECT e.login_oid,e.login_name,e.assumed_oid,m.roleid,
               e.set_path,e.inherit_path||m.roleid
          FROM effective_after_set e
          JOIN pg_catalog.pg_auth_members m ON m.member=e.effective_oid
         WHERE m.inherit_option AND NOT m.roleid=ANY(e.inherit_path)
    )
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
               'login',e.login_name,
               'assumed_role',pg_catalog.pg_get_userbyid(e.assumed_oid),
               'owner',pg_catalog.pg_get_userbyid(o.owner_oid),
               'set_path',e.set_path,'inherit_path',e.inherit_path
           ) ORDER BY e.login_name,e.assumed_oid,o.owner_oid)
      INTO unsafe_path
      FROM effective_after_set e JOIN protected_owners o
        ON o.owner_oid=e.effective_oid;
    IF unsafe_path IS NOT NULL THEN
        RAISE EXCEPTION 'untrusted login has effective owner-role path: %',unsafe_path;
    END IF;

    WITH RECURSIVE untrusted_logins AS (
        SELECT r.oid,r.rolname FROM pg_catalog.pg_roles r
         WHERE r.rolcanlogin AND NOT r.rolsuper
           AND NOT (r.oid=ANY(approved_invocation_oids))
    ), set_reachable(login_oid,login_name,assumed_oid,set_path) AS (
        SELECT u.oid,u.rolname,u.oid,ARRAY[u.oid]::oid[]
          FROM untrusted_logins u
        UNION ALL
        SELECT s.login_oid,s.login_name,m.roleid,s.set_path||m.roleid
          FROM set_reachable s
          JOIN pg_catalog.pg_auth_members m ON m.member=s.assumed_oid
         WHERE m.set_option AND NOT m.roleid=ANY(s.set_path)
    ), effective_principals(
        login_oid,login_name,effective_oid,set_path,inherit_path
    ) AS (
        SELECT s.login_oid,s.login_name,s.assumed_oid,
               s.set_path,ARRAY[s.assumed_oid]::oid[]
          FROM set_reachable s
        UNION ALL
        SELECT e.login_oid,e.login_name,m.roleid,
               e.set_path,e.inherit_path||m.roleid
          FROM effective_principals e
          JOIN pg_catalog.pg_auth_members m ON m.member=e.effective_oid
         WHERE m.inherit_option AND NOT m.roleid=ANY(e.inherit_path)
    ), protected_relations AS (
        SELECT c.oid,c.relname FROM pg_catalog.pg_class c
         WHERE c.relnamespace=target_schema_oid AND c.relname IN (
             'supplier_mapping_review_batches','supplier_mapping_review_candidates',
             'supplier_mapping_decisions','supplier_offer_selection_events',
             'supplier_offer_selection_heads','v_effective_supplier_mapping_decisions',
             'v_supplier_offer_selection_diagnostics',
             'v_selected_standard_supplier_offers',
             'v_supplier_offer_selection_shadow'
         )
    ), protected_functions AS (
        SELECT p.oid,p.oid::regprocedure::text AS identity
          FROM pg_catalog.pg_proc p
         WHERE p.pronamespace=target_schema_oid AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    ), unsafe AS (
        SELECT e.login_name AS rolname,'schema CREATE'::text AS privilege
          FROM effective_principals e
         WHERE pg_catalog.has_schema_privilege(
                   e.effective_oid,target_schema_oid,'CREATE')
        UNION ALL
        SELECT e.login_name,'relation '||r.relname
          FROM effective_principals e CROSS JOIN protected_relations r
         WHERE pg_catalog.has_table_privilege(e.effective_oid,r.oid,
                   'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR pg_catalog.has_any_column_privilege(e.effective_oid,r.oid,
                   'SELECT,INSERT,UPDATE,REFERENCES')
        UNION ALL
        SELECT e.login_name,'function '||f.identity
          FROM effective_principals e CROSS JOIN protected_functions f
         WHERE pg_catalog.has_function_privilege(
                   e.effective_oid,f.oid,'EXECUTE')
    )
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
               'login',rolname,'effective_privilege',privilege
           ) ORDER BY rolname,privilege)
      INTO unsafe_path FROM unsafe;
    IF unsafe_path IS NOT NULL THEN
        RAISE EXCEPTION 'untrusted login has effective mapping privilege: %',unsafe_path;
    END IF;
END
$role_topology$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".assert_persistent_mapping_foundation_contract()
RETURNS VOID
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog, "qa_mapping_test", "qa_mapping_test", pg_temp
AS $$
DECLARE
    target_schema CONSTANT TEXT := 'qa_mapping_test';
    installed_contract TEXT;
    installed_catalog_sha256 TEXT;
BEGIN
    -- This is the first executable trust-boundary check. The no-argument
    -- assertion reads the embedded, function-hash-pinned pair contract; it
    -- cannot accept a caller, argument, meta value, request field, or GUC as
    -- maintenance identity.
    PERFORM "qa_mapping_test".persistent_mapping_assert_safe_role_topology();
    SELECT value INTO installed_contract
      FROM "qa_mapping_test".meta WHERE key='persistent_mapping_foundation_contract';
    SELECT value INTO installed_catalog_sha256
      FROM "qa_mapping_test".meta WHERE key='persistent_mapping_foundation_catalog_sha256';
    IF installed_contract IS DISTINCT FROM 'v1-shadow-only' THEN
        RAISE EXCEPTION 'persistent mapping foundation contract version differs: %',
            installed_contract;
    END IF;
    IF target_schema IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_mapping_review_batches')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_mapping_review_candidates')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_mapping_decisions')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_offer_selection_events')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'supplier_offer_selection_heads')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_effective_supplier_mapping_decisions')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_supplier_offer_selection_diagnostics')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_selected_standard_supplier_offers')) IS NULL
       OR to_regclass(format('%I.%I',target_schema,'v_supplier_offer_selection_shadow')) IS NULL THEN
        RAISE EXCEPTION 'persistent mapping foundation object is absent';
    END IF;
    IF current_setting(
           'procurement.persistent_mapping_foundation_initial_install',true
       )='true' AND (
       EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_mapping_review_batches)
       OR EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_mapping_review_candidates)
       OR EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_mapping_decisions)
       OR EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_offer_selection_events)
       OR EXISTS (SELECT 1 FROM "qa_mapping_test".supplier_offer_selection_heads)
    ) THEN
        RAISE EXCEPTION 'migration must not adopt or synthesize mapping authority';
    END IF;
    IF "qa_mapping_test".supplier_mapping_policy_is_published(NULL,NULL,NULL,NULL) THEN
        RAISE EXCEPTION 'absent mapping policy must fail closed';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL aclexplode(c.relacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND privilege.grantee<>c.relowner
    ) OR EXISTS (
        SELECT 1
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_attribute a ON a.attrelid=c.oid
          CROSS JOIN LATERAL aclexplode(a.attacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND a.attnum>0 AND NOT a.attisdropped
           AND privilege.grantee<>c.relowner
    ) OR EXISTS (
        SELECT 1
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          CROSS JOIN LATERAL aclexplode(p.proacl) privilege
         WHERE n.nspname=target_schema AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
           AND privilege.grantee<>p.proowner
    ) THEN
        RAISE EXCEPTION 'persistent mapping objects grant a non-owner principal';
    END IF;
    IF installed_catalog_sha256 IS NULL
       OR installed_catalog_sha256 !~ '^[0-9a-f]{64}$'
       OR "qa_mapping_test".compute_persistent_mapping_catalog_sha256()
            IS DISTINCT FROM installed_catalog_sha256 THEN
        RAISE EXCEPTION 'persistent mapping catalog signature differs';
    END IF;
END
$$;

REVOKE ALL ON TABLE
    "qa_mapping_test".supplier_mapping_review_batches,
    "qa_mapping_test".supplier_mapping_review_candidates,
    "qa_mapping_test".supplier_mapping_decisions,
    "qa_mapping_test".supplier_offer_selection_events,
    "qa_mapping_test".supplier_offer_selection_heads,
    "qa_mapping_test".v_effective_supplier_mapping_decisions,
    "qa_mapping_test".v_supplier_offer_selection_diagnostics,
    "qa_mapping_test".v_selected_standard_supplier_offers,
    "qa_mapping_test".v_supplier_offer_selection_shadow
FROM PUBLIC;

DO $revoke_public_function_execution$
DECLARE
    target_schema CONSTANT TEXT := 'qa_mapping_test';
    owned_function REGPROCEDURE;
BEGIN
    FOR owned_function IN
        SELECT p.oid::regprocedure
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
         WHERE n.nspname=target_schema AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC',owned_function);
    END LOOP;
END
$revoke_public_function_execution$;

-- Objects can inherit named-role privileges from ALTER DEFAULT PRIVILEGES.
-- This first slice authorizes no non-owner role, so strip every such direct
-- relation/function grant before the catalog is signed. Later role grants
-- require their own approved contract-version transition.
DO $revoke_unconfigured_named_principals$
DECLARE
    target_schema CONSTANT TEXT := 'qa_mapping_test';
    relation_grant RECORD;
    column_grant RECORD;
    function_grant RECORD;
BEGIN
    FOR relation_grant IN
        SELECT DISTINCT c.relname,
               pg_get_userbyid(privilege.grantee) AS grantee_name
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          CROSS JOIN LATERAL aclexplode(c.relacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND privilege.grantee<>0
           AND privilege.grantee<>c.relowner
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I',
            target_schema,relation_grant.relname,
            relation_grant.grantee_name
        );
    END LOOP;

    FOR column_grant IN
        SELECT DISTINCT c.relname,a.attname,
               privilege.grantee AS grantee_oid,
               CASE WHEN privilege.grantee=0 THEN NULL
                    ELSE pg_get_userbyid(privilege.grantee) END AS grantee_name
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_attribute a ON a.attrelid=c.oid
          CROSS JOIN LATERAL aclexplode(a.attacl) privilege
         WHERE n.nspname=target_schema
           AND c.relname IN (
               'supplier_mapping_review_batches',
               'supplier_mapping_review_candidates',
               'supplier_mapping_decisions',
               'supplier_offer_selection_events',
               'supplier_offer_selection_heads',
               'v_effective_supplier_mapping_decisions',
               'v_supplier_offer_selection_diagnostics',
               'v_selected_standard_supplier_offers',
               'v_supplier_offer_selection_shadow'
           )
           AND a.attnum>0 AND NOT a.attisdropped
           AND privilege.grantee<>c.relowner
    LOOP
        IF column_grant.grantee_oid=0 THEN
            EXECUTE format(
                'REVOKE SELECT (%I), INSERT (%I), UPDATE (%I), REFERENCES (%I) '
                'ON TABLE %I.%I FROM PUBLIC',
                column_grant.attname,column_grant.attname,
                column_grant.attname,column_grant.attname,
                target_schema,column_grant.relname
            );
        ELSE
            EXECUTE format(
                'REVOKE SELECT (%I), INSERT (%I), UPDATE (%I), REFERENCES (%I) '
                'ON TABLE %I.%I FROM %I',
                column_grant.attname,column_grant.attname,
                column_grant.attname,column_grant.attname,
                target_schema,column_grant.relname,column_grant.grantee_name
            );
        END IF;
    END LOOP;

    FOR function_grant IN
        SELECT DISTINCT p.oid::regprocedure AS function_identity,
               pg_get_userbyid(privilege.grantee) AS grantee_name
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid=p.pronamespace
          CROSS JOIN LATERAL aclexplode(p.proacl) privilege
         WHERE n.nspname=target_schema AND p.proname ~
           '^(compute_persistent_mapping_catalog_sha256$|persistent_mapping_|supplier_mapping_policy_is_published$|reject_persistent_mapping_|validate_mapping_review_|validate_supplier_(mapping|offer_selection)|protect_(persistently_mapped|unactivated_mapped|persistent_mapping_rejection)|assert_persistent_mapping_)'
           AND privilege.grantee<>0
           AND privilege.grantee<>p.proowner
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM %I',
            function_grant.function_identity,function_grant.grantee_name
        );
    END LOOP;
END
$revoke_unconfigured_named_principals$;

-- Deliberately do not invoke either trust-anchor function and do not publish
-- contract/signature/migration metadata in this SQL body. Still inside this
-- file transaction, the manifest-aware runner independently verifies the new
-- assert/compute/helper definitions, invokes the verified compute function,
-- inserts the exact v1 contract and catalog signature, invokes the verified
-- assertion, and only then publishes this migration's checksum marker. Any
-- failure rolls back this file's objects and all four metadata effects.
