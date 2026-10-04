-- BUFFALO_SYNTHETIC_STAGING_DATABASE_V2
--
-- Staging-only successor for an already verified 001-016 synthetic fixture.
-- The operator preflights the exact predecessor catalog before executing this
-- file.  Every name is schema-qualified and every privileged helper has a
-- pg_catalog-only path.  Migrations 001-016 remain unchanged.

CREATE OR REPLACE FUNCTION "qa_mapping_test".synthetic_staging_acl_json(
    wanted_acl ACLITEM[]
)
RETURNS JSONB
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $acl$
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'grantor',pg_get_userbyid(acl.grantor),
                'grantee',CASE WHEN acl.grantee=0 THEN 'PUBLIC'
                               ELSE pg_get_userbyid(acl.grantee) END,
                'privilege',acl.privilege_type,
                'grantable',acl.is_grantable
            ) ORDER BY acl.grantee,acl.privilege_type,acl.grantor
        ),
        '[]'::jsonb
    )
      FROM aclexplode(wanted_acl) acl
$acl$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".synthetic_staging_text_array_json(
    wanted_values TEXT[]
)
RETURNS JSONB
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $array$
    SELECT COALESCE(jsonb_agg(item ORDER BY item),'[]'::jsonb)
      FROM unnest(wanted_values) item
$array$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".compute_synthetic_staging_catalog_sha256()
RETURNS TEXT
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $catalog$
DECLARE
    database_payload JSONB;
    schemas_payload JSONB;
    classes_payload JSONB;
    columns_payload JSONB;
    routines_payload JSONB;
    constraints_payload JSONB;
    triggers_payload JSONB;
    extensions_payload JSONB;
    defaults_payload JSONB;
    roles_payload JSONB;
    memberships_payload JSONB;
    settings_payload JSONB;
    sequences_payload JSONB;
    payload JSONB;
BEGIN
    SELECT jsonb_build_array(
               d.datname,pg_get_userbyid(d.datdba),d.datacl IS NULL,
               "qa_mapping_test".synthetic_staging_acl_json(d.datacl),
               d.datallowconn,d.datistemplate
           )
      INTO database_payload
      FROM pg_database d
     WHERE d.datname=current_database();

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               n.nspname,pg_get_userbyid(n.nspowner),n.nspacl IS NULL,
               "qa_mapping_test".synthetic_staging_acl_json(n.nspacl)
           ) ORDER BY n.nspname),'[]'::jsonb)
      INTO schemas_payload
      FROM pg_namespace n
     WHERE n.nspname IN ('qa_mapping_test','public');

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               c.relname,c.relkind,pg_get_userbyid(c.relowner),c.relacl IS NULL,
               "qa_mapping_test".synthetic_staging_acl_json(c.relacl),
               c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,
               "qa_mapping_test".synthetic_staging_text_array_json(c.reloptions),
               CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid,true)
                    WHEN c.relkind IN ('i','I') THEN pg_get_indexdef(c.oid)
                    ELSE '<NULL>' END
           ) ORDER BY c.relname,c.relkind),'[]'::jsonb)
      INTO classes_payload
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='qa_mapping_test';

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),
               a.attnotnull,a.attidentity,a.attgenerated,a.attacl IS NULL,
               "qa_mapping_test".synthetic_staging_acl_json(a.attacl),
               COALESCE(pg_get_expr(d.adbin,d.adrelid),'<NULL>')
           ) ORDER BY c.relname,a.attnum),'[]'::jsonb)
      INTO columns_payload
      FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_attribute a ON a.attrelid=c.oid
      LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
     WHERE n.nspname='qa_mapping_test' AND a.attnum>0 AND NOT a.attisdropped;

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               c.relname,format_type(s.seqtypid,NULL),s.seqstart,s.seqincrement,
               s.seqmax,s.seqmin,s.seqcache,s.seqcycle
           ) ORDER BY c.relname),'[]'::jsonb)
      INTO sequences_payload
      FROM pg_sequence s
      JOIN pg_class c ON c.oid=s.seqrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='qa_mapping_test';

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               p.proname,pg_get_function_identity_arguments(p.oid),
               pg_get_function_result(p.oid),p.prokind,l.lanname,p.provolatile,
               p.proparallel,p.prosecdef,p.proleakproof,p.proisstrict,p.proretset,
               pg_get_userbyid(p.proowner),p.proacl IS NULL,
               "qa_mapping_test".synthetic_staging_acl_json(p.proacl),
               "qa_mapping_test".synthetic_staging_text_array_json(p.proconfig),
               p.prosrc,COALESCE(p.probin,'')
           ) ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)),
           '[]'::jsonb)
      INTO routines_payload
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_language l ON l.oid=p.prolang
     WHERE n.nspname='qa_mapping_test';

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               c.relname,k.conname,k.contype,k.condeferrable,k.condeferred,
               k.convalidated,pg_get_constraintdef(k.oid,true)
           ) ORDER BY c.relname,k.conname),'[]'::jsonb)
      INTO constraints_payload
      FROM pg_constraint k
      JOIN pg_class c ON c.oid=k.conrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='qa_mapping_test';

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               c.relname,t.tgname,t.tgenabled,t.tgdeferrable,t.tginitdeferred,
               p.proname,pg_get_function_identity_arguments(p.oid),
               pg_get_triggerdef(t.oid,true)
           ) ORDER BY c.relname,t.tgname),'[]'::jsonb)
      INTO triggers_payload
      FROM pg_trigger t
      JOIN pg_class c ON c.oid=t.tgrelid
      JOIN pg_namespace n ON n.oid=c.relnamespace
      JOIN pg_proc p ON p.oid=t.tgfoid
     WHERE n.nspname='qa_mapping_test' AND NOT t.tgisinternal;

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               e.extname,e.extversion,n.nspname,pg_get_userbyid(e.extowner)
           ) ORDER BY e.extname),'[]'::jsonb)
      INTO extensions_payload
      FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
     WHERE n.nspname='qa_mapping_test';

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               pg_get_userbyid(d.defaclrole),COALESCE(n.nspname,'<GLOBAL>'),
               d.defaclobjtype,d.defaclacl IS NULL,
               "qa_mapping_test".synthetic_staging_acl_json(d.defaclacl)
           ) ORDER BY pg_get_userbyid(d.defaclrole),COALESCE(n.nspname,'<GLOBAL>'),
                      d.defaclobjtype),'[]'::jsonb)
      INTO defaults_payload
      FROM pg_default_acl d
      LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
      JOIN pg_roles owner_role ON owner_role.oid=d.defaclrole
     WHERE (n.nspname='qa_mapping_test' OR n.nspname IS NULL)
       AND owner_role.rolname=ANY(ARRAY[
           'qa_mapping_owner','qa_release_login','buffalo_synthetic_owner',
           'buffalo_synthetic_provisioner','buffalo_synthetic_runtime'
       ]);

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,
               rolreplication,rolbypassrls
           ) ORDER BY rolname),'[]'::jsonb)
      INTO roles_payload
      FROM pg_roles
     WHERE rolname=ANY(ARRAY[
         'qa_mapping_owner','qa_release_login','buffalo_synthetic_owner',
         'buffalo_synthetic_provisioner','buffalo_synthetic_runtime'
     ]);

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               parent.rolname,member.rolname,m.admin_option,m.inherit_option,m.set_option
           ) ORDER BY parent.rolname,member.rolname),'[]'::jsonb)
      INTO memberships_payload
      FROM pg_auth_members m
      JOIN pg_roles parent ON parent.oid=m.roleid
      JOIN pg_roles member ON member.oid=m.member
     WHERE parent.rolname=ANY(ARRAY[
               'qa_mapping_owner','qa_release_login','buffalo_synthetic_owner',
               'buffalo_synthetic_provisioner','buffalo_synthetic_runtime'
           ])
        OR member.rolname=ANY(ARRAY[
               'qa_mapping_owner','qa_release_login','buffalo_synthetic_owner',
               'buffalo_synthetic_provisioner','buffalo_synthetic_runtime'
           ]);

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               r.rolname,d.datname,
               "qa_mapping_test".synthetic_staging_text_array_json(s.setconfig)
           ) ORDER BY r.rolname,d.datname),'[]'::jsonb)
      INTO settings_payload
      FROM pg_db_role_setting s
      JOIN pg_roles r ON r.oid=s.setrole
      LEFT JOIN pg_database d ON d.oid=s.setdatabase
     WHERE r.rolname=ANY(ARRAY[
         'qa_mapping_owner','qa_release_login','buffalo_synthetic_owner',
         'buffalo_synthetic_provisioner','buffalo_synthetic_runtime'
     ]);

    payload := jsonb_build_object(
        'classes',classes_payload,
        'columns',columns_payload,
        'constraints',constraints_payload,
        'database',database_payload,
        'default_acls',defaults_payload,
        'extensions',extensions_payload,
        'memberships',memberships_payload,
        'roles',roles_payload,
        'role_settings',settings_payload,
        'routines',routines_payload,
        'schemas',schemas_payload,
        'sequences',sequences_payload,
        'triggers',triggers_payload
    );
    RETURN encode(
        "qa_mapping_test".digest(convert_to(payload::text,'UTF8'),'sha256'),
        'hex'
    );
END
$catalog$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".compute_synthetic_staging_persistent_mapping_sha256()
RETURNS TEXT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $mapping_catalog$
    SELECT "qa_mapping_test".compute_persistent_mapping_catalog_sha256()
$mapping_catalog$;

-- The old topology assertion is deliberately replaced only in this staging
-- destination.  It admits the runtime directly and the same runtime while an
-- owner-owned SECURITY DEFINER validation trigger is executing.
CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_assert_safe_role_topology()
RETURNS VOID
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $topology$
DECLARE
    edge_count BIGINT;
BEGIN
    IF session_user::text IS DISTINCT FROM 'buffalo_synthetic_runtime'
       OR current_user::text NOT IN (
           'buffalo_synthetic_runtime','buffalo_synthetic_owner'
       ) THEN
        RAISE EXCEPTION 'synthetic staging runtime identity differs';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname='buffalo_synthetic_owner'
          AND NOT rolsuper AND NOT rolinherit AND NOT rolcreaterole
          AND NOT rolcreatedb AND NOT rolcanlogin AND NOT rolreplication
          AND NOT rolbypassrls
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname='buffalo_synthetic_provisioner'
          AND NOT rolsuper AND rolinherit AND NOT rolcreaterole
          AND NOT rolcreatedb AND rolcanlogin AND NOT rolreplication
          AND NOT rolbypassrls
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname='buffalo_synthetic_runtime'
          AND NOT rolsuper AND NOT rolinherit AND NOT rolcreaterole
          AND NOT rolcreatedb AND rolcanlogin AND NOT rolreplication
          AND NOT rolbypassrls
    ) THEN
        RAISE EXCEPTION 'synthetic staging role flags differ';
    END IF;
    SELECT count(*) INTO edge_count
      FROM pg_auth_members m
      JOIN pg_roles parent ON parent.oid=m.roleid
      JOIN pg_roles member ON member.oid=m.member
     WHERE parent.rolname=ANY(ARRAY[
               'qa_mapping_owner','qa_release_login','buffalo_synthetic_owner',
               'buffalo_synthetic_provisioner','buffalo_synthetic_runtime'
           ])
        OR member.rolname=ANY(ARRAY[
               'qa_mapping_owner','qa_release_login','buffalo_synthetic_owner',
               'buffalo_synthetic_provisioner','buffalo_synthetic_runtime'
           ]);
    IF edge_count<>2 OR NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles parent ON parent.oid=m.roleid
        JOIN pg_roles member ON member.oid=m.member
        WHERE parent.rolname='qa_mapping_owner'
          AND member.rolname='buffalo_synthetic_provisioner'
          AND NOT m.admin_option AND m.inherit_option AND m.set_option
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_auth_members m
        JOIN pg_roles parent ON parent.oid=m.roleid
        JOIN pg_roles member ON member.oid=m.member
        WHERE parent.rolname='buffalo_synthetic_owner'
          AND member.rolname='buffalo_synthetic_provisioner'
          AND NOT m.admin_option AND m.inherit_option AND m.set_option
    ) OR pg_has_role('buffalo_synthetic_runtime','buffalo_synthetic_owner','MEMBER')
       OR pg_has_role('buffalo_synthetic_runtime','buffalo_synthetic_provisioner','MEMBER')
       OR has_schema_privilege(
              'buffalo_synthetic_runtime','qa_mapping_test','CREATE'
          ) THEN
        RAISE EXCEPTION 'synthetic staging role membership differs';
    END IF;
END
$topology$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".persistent_mapping_lock_supplier_offers(
    wanted_offer_ids BIGINT[]
)
RETURNS BIGINT[]
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $lock_offers$
DECLARE
    normalized BIGINT[];
    locked BIGINT[];
BEGIN
    IF session_user::text IS DISTINCT FROM 'buffalo_synthetic_runtime'
       OR wanted_offer_ids IS NULL
       OR array_position(wanted_offer_ids,NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'synthetic staging offer lock input differs';
    END IF;
    SELECT COALESCE(array_agg(value ORDER BY value),'{}'::BIGINT[])
      INTO normalized FROM (SELECT DISTINCT unnest(wanted_offer_ids) AS value) valueset;
    IF normalized IS DISTINCT FROM wanted_offer_ids THEN
        RAISE EXCEPTION 'synthetic staging offer lock input is not canonical';
    END IF;
    SELECT COALESCE(array_agg(offer_id ORDER BY offer_id),'{}'::BIGINT[])
      INTO locked
      FROM (
          SELECT offer_id FROM "qa_mapping_test".supplier_offers
           WHERE offer_id=ANY(wanted_offer_ids)
           ORDER BY offer_id FOR UPDATE
      ) selected;
    RETURN locked;
END
$lock_offers$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".synthetic_staging_lock_exception(
    wanted_exception_id BIGINT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $lock_exception$
BEGIN
    IF session_user::text IS DISTINCT FROM 'buffalo_synthetic_runtime'
       OR wanted_exception_id IS NULL THEN
        RAISE EXCEPTION 'synthetic staging exception lock input differs';
    END IF;
    PERFORM 1 FROM "qa_mapping_test".exceptions
     WHERE exception_id=wanted_exception_id FOR UPDATE;
    RETURN FOUND;
END
$lock_exception$;

CREATE OR REPLACE FUNCTION "qa_mapping_test".synthetic_staging_lock_recommendation(
    wanted_recommendation_id BIGINT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog
AS $lock_recommendation$
BEGIN
    IF session_user::text IS DISTINCT FROM 'buffalo_synthetic_runtime'
       OR wanted_recommendation_id IS NULL THEN
        RAISE EXCEPTION 'synthetic staging recommendation lock input differs';
    END IF;
    PERFORM 1 FROM "qa_mapping_test".procurement_recommendations
     WHERE recommendation_id=wanted_recommendation_id FOR UPDATE;
    RETURN FOUND;
END
$lock_recommendation$;

-- Row locks in these trusted validation roots must not require UPDATE on
-- immutable canonical evidence.  Their bodies are inherited unchanged from
-- the preflighted 014/016 source; only execution identity/path changes.
ALTER FUNCTION "qa_mapping_test".validate_mapping_review_candidate_insert()
    SECURITY DEFINER;
ALTER FUNCTION "qa_mapping_test".validate_mapping_review_candidate_insert()
    SET search_path = pg_catalog, "qa_mapping_test", pg_temp;
ALTER FUNCTION "qa_mapping_test".validate_supplier_mapping_decision_insert()
    SECURITY DEFINER;
ALTER FUNCTION "qa_mapping_test".validate_supplier_mapping_decision_insert()
    SET search_path = pg_catalog, "qa_mapping_test", pg_temp;
ALTER FUNCTION "qa_mapping_test".validate_supplier_offer_selection_event_insert()
    SECURITY DEFINER;
ALTER FUNCTION "qa_mapping_test".validate_supplier_offer_selection_event_insert()
    SET search_path = pg_catalog, "qa_mapping_test", pg_temp;
ALTER FUNCTION "qa_mapping_test".validate_supplier_price_authority_event()
    SECURITY DEFINER;
ALTER FUNCTION "qa_mapping_test".validate_supplier_price_authority_event()
    SET search_path = pg_catalog, "qa_mapping_test", pg_temp;

CREATE OR REPLACE FUNCTION "qa_mapping_test".assert_synthetic_staging_contract()
RETURNS VOID
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $assertion$
DECLARE
    staging_count BIGINT;
    staging_contract TEXT;
    fixture_manifest TEXT;
    permission_matrix TEXT;
    catalog_sha256 TEXT;
    legacy_count BIGINT;
BEGIN
    IF session_user::text IS DISTINCT FROM 'buffalo_synthetic_runtime'
       OR current_user::text IS DISTINCT FROM 'buffalo_synthetic_runtime'
       OR current_database() IS DISTINCT FROM 'buffalo_synthetic_staging_demo'
       OR current_setting('server_version_num')::INTEGER / 10000<>16 THEN
        RAISE EXCEPTION 'synthetic staging connected identity differs';
    END IF;
    PERFORM "qa_mapping_test".persistent_mapping_assert_safe_role_topology();

    SELECT count(*),
           max(value) FILTER (WHERE key='synthetic_staging_contract'),
           max(value) FILTER (
               WHERE key='synthetic_staging_fixture_manifest_sha256'
           ),
           max(value) FILTER (
               WHERE key='synthetic_staging_permission_matrix_sha256'
           ),
           max(value) FILTER (WHERE key='synthetic_staging_catalog_sha256')
      INTO staging_count,staging_contract,fixture_manifest,
           permission_matrix,catalog_sha256
      FROM "qa_mapping_test".meta
     WHERE key LIKE 'synthetic_staging_%';
    IF staging_count<>4
       OR staging_contract IS DISTINCT FROM 'BUFFALO_SYNTHETIC_STAGING_DATABASE_V2'
       OR fixture_manifest IS DISTINCT FROM '__FIXTURE_MANIFEST_SHA256__'
       OR permission_matrix IS DISTINCT FROM '__PERMISSION_MATRIX_SHA256__'
       OR catalog_sha256 IS NULL
       OR catalog_sha256 !~ '^[0-9a-f]{64}$'
       OR "qa_mapping_test".compute_synthetic_staging_catalog_sha256()
            IS DISTINCT FROM catalog_sha256 THEN
        RAISE EXCEPTION 'synthetic staging contract marker or catalog differs';
    END IF;

    SELECT count(*) INTO legacy_count
      FROM (VALUES
          ('migration:014_persistent_mapping_foundation.sql',
           'sha256:80c5d6c0a0299edf8d04f9c5f684f9cea277494b894fa0feb0a387476bfa8c86'),
          ('persistent_mapping_foundation_contract','v1-shadow-only'),
          ('persistent_mapping_foundation_catalog_sha256',
           '5a9fff00c1d62c2de89ca1dc27d4e264def12eb726ab249a9ab86a9fc529c72e'),
          ('migration:015_monday_forecast_v2_retirement.sql',
           'sha256:e3f69e23cf6fce0760add5ec6a329426444dd44b681e338aded9d833e89ba56b'),
          ('monday_forecast_v2_retirement_contract','v1'),
          ('monday_forecast_v2_retirement_catalog_sha256',
           '2fafe14a6dd9394fbebb471f84b768f77bd9e2675cd8e3746576a8a9d3099e7d'),
          ('monday_forecast_v2_retirement_catalog_sha256',
           '0d151f70f6eec2965428e0bec64ab573962a8aad344b14a9d44332edff284cd9'),
          ('migration:016_synthetic_price_replacement.sql',
           'sha256:5d74074b7c2f79eb8e9b92bbb3302923c80003b6bce25803671078b0645ea35d'),
          ('synthetic_price_replacement_contract',
           'v1-complete-vendor-monthly-synthetic'),
          ('synthetic_price_replacement_catalog_sha256',
           '00e01c90bdc324544b2746880d5c8d6821dad3a6b1698b761a78d91c8b9c1a88')
      ) expected(key,value)
      JOIN "qa_mapping_test".meta actual
        ON actual.key=expected.key AND actual.value=expected.value;
    IF legacy_count<>9 THEN
        RAISE EXCEPTION 'synthetic staging predecessor provenance differs';
    END IF;
END
$assertion$;

REVOKE ALL ON FUNCTION
    "qa_mapping_test".synthetic_staging_acl_json(ACLITEM[]),
    "qa_mapping_test".synthetic_staging_text_array_json(TEXT[]),
    "qa_mapping_test".compute_synthetic_staging_catalog_sha256(),
    "qa_mapping_test".compute_synthetic_staging_persistent_mapping_sha256(),
    "qa_mapping_test".persistent_mapping_lock_supplier_offers(BIGINT[]),
    "qa_mapping_test".synthetic_staging_lock_exception(BIGINT),
    "qa_mapping_test".synthetic_staging_lock_recommendation(BIGINT),
    "qa_mapping_test".assert_synthetic_staging_contract()
FROM PUBLIC;
