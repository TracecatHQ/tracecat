"""Enable pg_trgm for text search without changing source tables.

The server must provide pg_trgm. Restricted migration roles can use an extension
preinstalled by an administrator. An existing extension stays in its schema. A
new one goes in public, or in ibm_extension on IBM Cloud Databases for
PostgreSQL, which only allows extensions in that read-only, provider-managed
schema. Downgrade retains this shared dependency.

Revision ID: 3e54217aadda
Revises: 8b51e0c47d2a
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3e54217aadda"
down_revision: str | None = "8b51e0c47d2a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable and validate the shared trigram extension in the app database."""
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_available_extensions WHERE name = 'pg_trgm'
            ) THEN
                RAISE EXCEPTION 'pg_trgm is not available on this PostgreSQL server'
                    USING ERRCODE = 'feature_not_supported',
                          HINT = 'Install the PostgreSQL contrib package or select a managed PostgreSQL engine version that supports pg_trgm. Check pg_available_extensions on the application database server.';
            END IF;
        END
        $$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_extension WHERE extname = 'pg_trgm') THEN
                NULL;
            ELSIF pg_catalog.to_regnamespace('ibm_extension') IS NOT NULL THEN
                -- IBM's documented form; IBM places it in ibm_extension.
                CREATE EXTENSION IF NOT EXISTS pg_trgm;
            ELSE
                CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
            END IF;
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'Cannot install pg_trgm with the current role'
                USING ERRCODE = 'insufficient_privilege', DETAIL = SQLERRM,
                      HINT = 'Ask an authorized administrator to preinstall pg_trgm in public (ibm_extension on IBM Cloud), then rerun migrations. On RDS, also check rds.allowed_extensions. The migration does not grant privileges.';
        END
        $$;
    """)

    op.execute("""
        DO $$
        DECLARE
            extension_oid oid;
            extension_schema text;
            similarity_oid oid;
        BEGIN
            SELECT e.oid, n.nspname INTO extension_oid, extension_schema
            FROM pg_catalog.pg_extension AS e
            JOIN pg_catalog.pg_namespace AS n ON n.oid = e.extnamespace
            WHERE e.extname = 'pg_trgm';

            IF extension_oid IS NULL THEN
                RAISE EXCEPTION 'pg_trgm is not enabled in the application database'
                    USING ERRCODE = 'object_not_in_prerequisite_state',
                          HINT = 'Ask the database administrator to run scripts/postgres/pg-trgm.sql with psql -v install=true against this application database before search-dependent migrations.';
            END IF;
            IF NOT pg_catalog.has_schema_privilege(extension_schema, 'USAGE') THEN
                RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',
                    MESSAGE = 'The current role needs USAGE on the ' || extension_schema || ' schema',
                    HINT = 'Ask the database administrator to grant USAGE on schema ' || quote_ident(extension_schema) || ' to the application and migration roles.';
            END IF;

            -- A same-named object is insufficient: require pg_trgm membership.
            IF NOT EXISTS (
                SELECT FROM pg_catalog.pg_opclass AS c
                JOIN pg_catalog.pg_am AS a ON a.oid = c.opcmethod
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.opcnamespace
                JOIN pg_catalog.pg_depend AS d ON d.classid = 'pg_opclass'::regclass
                    AND d.objid = c.oid AND d.deptype = 'e'
                    AND d.refclassid = 'pg_extension'::regclass
                    AND d.refobjid = extension_oid
                WHERE c.opcname = 'gin_trgm_ops' AND a.amname = 'gin'
                  AND n.nspname = extension_schema AND c.opcintype = 'text'::regtype
            ) THEN
                RAISE EXCEPTION USING ERRCODE = 'object_not_in_prerequisite_state',
                    MESSAGE = 'pg_trgm must provide ' || extension_schema || '.gin_trgm_ops for text',
                    HINT = 'Ask the database administrator to repair the extension-owned GIN operator class. A same-named unrelated operator class is not supported.';
            END IF;

            similarity_oid := pg_catalog.to_regprocedure(
                quote_ident(extension_schema) || '.similarity(text,text)'
            );
            IF similarity_oid IS NULL OR NOT EXISTS (
                SELECT FROM pg_catalog.pg_depend
                WHERE classid = 'pg_proc'::regclass AND objid = similarity_oid
                  AND deptype = 'e' AND refclassid = 'pg_extension'::regclass
                  AND refobjid = extension_oid
            ) THEN
                RAISE EXCEPTION USING ERRCODE = 'object_not_in_prerequisite_state',
                    MESSAGE = 'pg_trgm must provide ' || extension_schema || '.similarity(text,text)',
                    HINT = 'Ask the database administrator to repair the extension-owned similarity function. A same-named unrelated function is not supported.';
            END IF;
            IF NOT pg_catalog.has_function_privilege(similarity_oid, 'EXECUTE') THEN
                RAISE EXCEPTION USING ERRCODE = 'insufficient_privilege',
                    MESSAGE = 'The current role needs EXECUTE on ' || extension_schema || '.similarity(text,text)',
                    HINT = 'Ask the database administrator to grant EXECUTE on the pg_trgm functions used by search to the application and migration roles.';
            END IF;

            EXECUTE 'SELECT ' || quote_ident(extension_schema) || '.similarity($1, $2)'
                USING 'synthetic search'::text, 'synthetic search'::text;
        END
        $$;
    """)


def downgrade() -> None:
    """Retain pg_trgm because other migrations or operators may depend on it."""
    # Only shared extension registration was added; source data is unchanged.
    # Removing it would break independently managed indexes and functions.
    pass
