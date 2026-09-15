-- Check the application database with psql -X -f scripts/postgres/pgvector.sql.
-- Only an explicit -v install=true enables installation. Never run as an app
-- startup hook for managed databases: they may require a separate provisioning role.
\set ON_ERROR_STOP on
\if :{?install}
\else
    \set install false
\endif

BEGIN;
SET LOCAL search_path = pg_catalog, public;

-- A working vector operator does not prove that existing text indexes remain
-- valid after changing the database image's libc or ICU libraries.
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM pg_database
        WHERE (datname = current_database() OR datname = 'template1')
          AND datcollversion IS DISTINCT FROM pg_database_collation_actual_version(oid)
    ) OR EXISTS (
        SELECT FROM pg_collation
        -- The default collation's version is stored in pg_database above.
        WHERE collprovider <> 'd'
          AND collversion IS DISTINCT FROM pg_collation_actual_version(oid)
    ) THEN
        RAISE EXCEPTION 'PostgreSQL collation version mismatch'
            USING HINT = 'Stop the upgrade and restore compatible database libraries, or have an administrator rebuild affected objects before refreshing collation versions. Do not refresh versions without rebuilding indexes.';
    END IF;
END
$$;

\if :install
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM pg_available_extensions WHERE name = 'vector'
    ) THEN
        RAISE EXCEPTION 'pgvector is not available on this PostgreSQL server'
            USING HINT = 'Install the pgvector server package or use a supported managed PostgreSQL version before provisioning the application database.';
    END IF;
END
$$;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
\endif

DO $$
DECLARE
    installed_version text;
    installed_schema text;
BEGIN
    SELECT e.extversion, n.nspname INTO installed_version, installed_schema
    FROM pg_extension AS e
    JOIN pg_namespace AS n ON n.oid = e.extnamespace
    WHERE e.extname = 'vector';

    IF installed_version IS NULL THEN
        RAISE EXCEPTION 'pgvector is not enabled in the application database'
            USING HINT = 'Ask the database administrator to run scripts/postgres/pgvector.sql with psql -v install=true against this database before vector-dependent migrations.';
    END IF;
    IF installed_schema <> 'public' THEN
        RAISE EXCEPTION 'pgvector must be installed in the public schema'
            USING HINT = 'Ask the database administrator to review the existing extension schema; this script does not relocate extensions.';
    END IF;
    IF installed_version !~ '^[0-9]+\.[0-9]+\.[0-9]+$' THEN
        RAISE EXCEPTION 'Unrecognized pgvector version: %', installed_version;
    END IF;
    IF string_to_array(installed_version, '.')::integer[] < ARRAY[0, 8, 0] THEN
        RAISE EXCEPTION 'pgvector 0.8.0 or newer is required; found %', installed_version
            USING HINT = 'Ask the database administrator to upgrade the vector extension before vector-dependent migrations. Installation mode does not upgrade an existing extension.';
    END IF;
END
$$;

-- Exercise the installed type/operator using the caller's permissions.
SELECT extversion AS pgvector_version,
       '[1,0,0]'::public.vector OPERATOR(public.<=>) '[1,0,0]'::public.vector
           AS cosine_distance
FROM pg_extension
WHERE extname = 'vector';

COMMIT;
