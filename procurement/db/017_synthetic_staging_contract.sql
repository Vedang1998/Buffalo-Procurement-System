-- BUFFALO_SYNTHETIC_STAGING_DATABASE_V1
-- Applied only by the explicit operator provisioning command after fixture restore.
CREATE OR REPLACE FUNCTION assert_synthetic_staging_contract()
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, qa_mapping_test
AS $$
DECLARE
    markers jsonb;
BEGIN
    IF session_user <> 'buffalo_synthetic_runtime'
       OR current_user <> 'buffalo_synthetic_runtime'
       OR current_database() <> 'buffalo_synthetic_staging'
       OR current_setting('server_version_num')::integer / 10000 <> 16
       OR current_schema() <> 'qa_mapping_test' THEN
        RAISE EXCEPTION 'synthetic staging connected identity differs';
    END IF;
    SELECT jsonb_object_agg(key,value) INTO markers
      FROM qa_mapping_test.meta
     WHERE key IN ('synthetic_owner_demo_contract','synthetic_staging_contract',
                   'synthetic_staging_permission_matrix_sha256');
    IF markers->>'synthetic_owner_demo_contract' <> 'BUFFALO_SYNTHETIC_OWNER_DEMO_V1'
       OR markers->>'synthetic_staging_contract' <> 'BUFFALO_SYNTHETIC_STAGING_DATABASE_V1'
       OR markers->>'synthetic_staging_permission_matrix_sha256' IS NULL THEN
        RAISE EXCEPTION 'synthetic staging contract marker differs';
    END IF;
    IF has_database_privilege(current_user,current_database(),'TEMP')
       OR has_schema_privilege(current_user,'qa_mapping_test','CREATE')
       OR pg_has_role(current_user,'buffalo_synthetic_owner','MEMBER')
       OR pg_has_role(current_user,'buffalo_synthetic_provisioner','MEMBER') THEN
        RAISE EXCEPTION 'synthetic staging forbidden authority is effective';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION assert_synthetic_staging_contract() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION assert_synthetic_staging_contract() TO buffalo_synthetic_runtime;
