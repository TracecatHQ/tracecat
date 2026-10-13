-- Check the application database with psql -X -f scripts/postgres/pg-trgm.sql.
-- Installation requires an explicit -v install=true and an authorized
-- provisioning role. Do not run this script against Temporal's database.
\set ON_ERROR_STOP on
\if :{?install}
\else
    \set install false
\endif

\if :install
BEGIN;
\else
BEGIN READ ONLY;
\endif
SET LOCAL search_path = pg_catalog;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_available_extensions WHERE name = 'pg_trgm'
    ) THEN
        RAISE EXCEPTION 'pg_trgm is not available on this PostgreSQL server'
            USING ERRCODE = 'feature_not_supported',
                  HINT = 'Install the PostgreSQL contrib package or select a managed PostgreSQL engine version that supports pg_trgm. Check pg_available_extensions on the application database server.';
    END IF;
END
$$;

\if :install
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE EXCEPTION 'Cannot install pg_trgm with the current role'
        USING ERRCODE = 'insufficient_privilege', DETAIL = SQLERRM,
              HINT = 'Use an authorized provisioning role with CREATE on the application database and public schema. On RDS, also check rds.allowed_extensions. Restricted migration roles can use an extension preinstalled by an administrator.';
END
$$;
\endif

DO $$
DECLARE
    extension_oid oid;
    extension_schema text;
    similarity_oid oid;
BEGIN
    SELECT e.oid, n.nspname INTO extension_oid, extension_schema
    FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    WHERE e.extname = 'pg_trgm';

    IF extension_oid IS NULL THEN
        RAISE EXCEPTION 'pg_trgm is not enabled in the application database'
            USING ERRCODE = 'object_not_in_prerequisite_state',
                  HINT = 'Ask the database administrator to run scripts/postgres/pg-trgm.sql with psql -v install=true against this application database before search-dependent migrations.';
    END IF;
    IF extension_schema <> 'public' THEN
        RAISE EXCEPTION 'pg_trgm must be installed in the public schema'
            USING ERRCODE = 'object_not_in_prerequisite_state',
                  HINT = 'Ask the database administrator to review the existing extension schema. This script does not relocate, upgrade or drop extensions.';
    END IF;
    IF NOT has_schema_privilege('public', 'USAGE') THEN
        RAISE EXCEPTION 'The current role needs USAGE on the public schema'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Ask the database administrator to grant USAGE on schema public to the application and migration roles.';
    END IF;

    -- A same-named object is insufficient: require pg_trgm membership.
    IF NOT EXISTS (
        SELECT FROM pg_opclass AS c
        JOIN pg_am AS a ON a.oid = c.opcmethod
        JOIN pg_namespace AS n ON n.oid = c.opcnamespace
        JOIN pg_depend AS d ON d.classid = 'pg_opclass'::regclass
            AND d.objid = c.oid AND d.deptype = 'e'
            AND d.refclassid = 'pg_extension'::regclass
            AND d.refobjid = extension_oid
        WHERE c.opcname = 'gin_trgm_ops' AND a.amname = 'gin'
          AND n.nspname = 'public' AND c.opcintype = 'text'::regtype
    ) THEN
        RAISE EXCEPTION 'pg_trgm must provide public.gin_trgm_ops for text'
            USING ERRCODE = 'object_not_in_prerequisite_state',
                  HINT = 'Ask the database administrator to repair the extension-owned GIN operator class. A same-named unrelated operator class is not supported.';
    END IF;

    similarity_oid := to_regprocedure('public.similarity(text,text)');
    IF similarity_oid IS NULL OR NOT EXISTS (
        SELECT FROM pg_depend
        WHERE classid = 'pg_proc'::regclass AND objid = similarity_oid
          AND deptype = 'e' AND refclassid = 'pg_extension'::regclass
          AND refobjid = extension_oid
    ) THEN
        RAISE EXCEPTION 'pg_trgm must provide public.similarity(text,text)'
            USING ERRCODE = 'object_not_in_prerequisite_state',
                  HINT = 'Ask the database administrator to repair the extension-owned similarity function. A same-named unrelated function is not supported.';
    END IF;
    IF NOT has_function_privilege(similarity_oid, 'EXECUTE') THEN
        RAISE EXCEPTION 'The current role needs EXECUTE on public.similarity(text,text)'
            USING ERRCODE = 'insufficient_privilege',
                  HINT = 'Ask the database administrator to grant EXECUTE on the pg_trgm functions used by search to the application and migration roles.';
    END IF;
END
$$;

-- Load the server library and exercise the function with the caller's permissions.
SELECT current_database() AS application_database,
       current_setting('server_version') AS postgres_version,
       extversion AS pg_trgm_version,
       public.similarity('synthetic search'::text, 'synthetic search'::text)
           AS identical_similarity
FROM pg_extension WHERE extname = 'pg_trgm';

COMMIT;
