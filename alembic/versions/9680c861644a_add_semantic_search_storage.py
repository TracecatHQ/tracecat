"""Add disabled semantic search storage; no source tables or rows are modified.

Install pgvector server files before upgrading. The migration enables the
extension if needed; its role needs permission to do so. Downgrade removes only
derived search data and retains the extension; stop search workers first.
Revision ID: 9680c861644a
Revises: 31ee4b7f175a
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "9680c861644a"
down_revision = "31ee4b7f175a"
branch_labels = None
depends_on = None

SEARCH_TABLES = (
    "search_workspace_state",
    "search_embedding_config",
    "search_collection",
    "search_document",
    "search_chunk",
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
                WHERE e.extname = 'vector' AND n.nspname = 'public'
                  AND string_to_array(e.extversion, '.')::int[] >= ARRAY[0,8,0]
            ) THEN
                RAISE EXCEPTION 'Provision pgvector >= 0.8.0 in public before migrating: run scripts/postgres/pgvector.sql with install=true as a database administrator';
            END IF;
        END $$;
    """)
    op.create_table(
        "search_embedding_config",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("credential_id", sa.UUID(), nullable=False),
        sa.Column("credential_environment", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("input_token_limit", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "input_token_limit > 0", name=op.f("ck_search_embedding_config_input_limit")
        ),
        sa.CheckConstraint(
            "version > 0 AND dimensions BETWEEN 1 AND 3072",
            name=op.f("ck_search_embedding_config_version_dimensions"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "workspace_id",
            "version",
            name=op.f("pk_search_embedding_config"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "version",
            "dimensions",
            name=op.f(
                "uq_search_embedding_config_organization_id_workspace_id_version_dimensions"
            ),
        ),
    )
    op.create_table(
        "search_workspace_state",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "current_version", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("state", sa.Text(), server_default="disabled", nullable=False),
        sa.Column(
            "reconciliation_required",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('disabled','active','paused','reindex_required')",
            name=op.f("ck_search_workspace_state_state"),
        ),
        sa.CheckConstraint(
            "current_version >= 0", name=op.f("ck_search_workspace_state_version")
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "workspace_id", name=op.f("pk_search_workspace_state")
        ),
    )
    op.create_table(
        "search_collection",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("source_type", sa.Text(), server_default="table", nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("selected_column_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("config_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "chunker_settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("backfill_cursor", sa.UUID(), nullable=True),
        sa.Column(
            "backfill_complete", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "generation > 0 AND source_type = 'table'",
            name=op.f("ck_search_collection_generation_source"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "config_version"],
            [
                "search_embedding_config.organization_id",
                "search_embedding_config.workspace_id",
                "search_embedding_config.version",
            ],
            name=op.f(
                "fk_search_collection_organization_id_workspace_id_config_version_search_embedding_config"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_collection")),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "id",
            name=op.f("uq_search_collection_organization_id_workspace_id_id"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "source_type",
            "source_id",
            name=op.f(
                "uq_search_collection_organization_id_workspace_id_source_type_source_id"
            ),
        ),
    )
    op.create_table(
        "search_document",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("source_row_id", sa.UUID(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column(
            "desired_revision", sa.BigInteger(), server_default="1", nullable=False
        ),
        sa.Column("build_revision", sa.BigInteger(), nullable=True),
        sa.Column("indexed_revision", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column(
            "enumeration_cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "enumeration_complete", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "expected_chunks", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("fence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lease_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state IN ('ready','empty')) = (indexed_revision IS NOT NULL)",
            name=op.f("ck_search_document_publication"),
        ),
        sa.CheckConstraint(
            "state IN ('pending','building','ready','empty','failed','deleted')",
            name=op.f("ck_search_document_state"),
        ),
        sa.CheckConstraint(
            "build_revision IS NULL OR (build_revision > 0 AND build_revision <= desired_revision)",
            name=op.f("ck_search_document_build_revision"),
        ),
        sa.CheckConstraint(
            "desired_revision > 0 AND generation > 0 AND fence >= 0",
            name=op.f("ck_search_document_revision"),
        ),
        sa.CheckConstraint(
            "expected_chunks >= 0", name=op.f("ck_search_document_expected_chunks")
        ),
        sa.CheckConstraint(
            "indexed_revision IS NULL OR (indexed_revision = desired_revision AND build_revision IS NOT NULL AND build_revision = indexed_revision AND enumeration_complete)",
            name=op.f("ck_search_document_indexed_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "collection_id"],
            [
                "search_collection.organization_id",
                "search_collection.workspace_id",
                "search_collection.id",
            ],
            name=op.f(
                "fk_search_document_organization_id_workspace_id_collection_id_search_collection"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_document")),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "collection_id",
            "id",
            name=op.f(
                "uq_search_document_organization_id_workspace_id_collection_id_id"
            ),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "collection_id",
            "source_row_id",
            name=op.f(
                "uq_search_document_organization_id_workspace_id_collection_id_source_row_id"
            ),
        ),
    )
    op.create_index(
        "ix_search_document_dispatch",
        "search_document",
        ["workspace_id", "state", "next_attempt_at"],
        unique=False,
    )
    op.create_table(
        "search_chunk",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("collection_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("config_version", sa.BigInteger(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("column_id", sa.UUID(), nullable=False),
        sa.Column("column_name", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.BigInteger(), nullable=False),
        sa.Column("end_offset", sa.BigInteger(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("state", sa.Text(), server_default="prepared", nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(state = 'embedded') = (embedding IS NOT NULL)",
            name=op.f("ck_search_chunk_embedding_state"),
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[a-f0-9]{64}$'", name=op.f("ck_search_chunk_input_hash")
        ),
        sa.CheckConstraint(
            "state IN ('prepared','embedded','failed')",
            name=op.f("ck_search_chunk_state"),
        ),
        sa.CheckConstraint(
            "embedding IS NULL OR (vector_dims(embedding) = dimensions AND vector_norm(embedding) > 0)",
            name=op.f("ck_search_chunk_vector_valid"),
        ),
        sa.CheckConstraint(
            "generation > 0 AND revision > 0 AND ordinal >= 0",
            name=op.f("ck_search_chunk_revision_ordinal"),
        ),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset",
            name=op.f("ck_search_chunk_offsets"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "collection_id", "document_id"],
            [
                "search_document.organization_id",
                "search_document.workspace_id",
                "search_document.collection_id",
                "search_document.id",
            ],
            name=op.f(
                "fk_search_chunk_organization_id_workspace_id_collection_id_document_id_search_document"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "config_version", "dimensions"],
            [
                "search_embedding_config.organization_id",
                "search_embedding_config.workspace_id",
                "search_embedding_config.version",
                "search_embedding_config.dimensions",
            ],
            name=op.f(
                "fk_search_chunk_organization_id_workspace_id_config_version_dimensions_search_embedding_config"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_chunk")),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "document_id",
            "generation",
            "revision",
            "ordinal",
            name=op.f(
                "uq_search_chunk_organization_id_workspace_id_document_id_generation_revision_ordinal"
            ),
        ),
    )

    # Freeze policies in this revision rather than importing mutable app helpers.
    for table in SEARCH_TABLES:
        predicate = f"""
            current_setting('app.rls_bypass', true) = 'on'
            OR (
                workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::uuid
                AND organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
                AND EXISTS (SELECT 1 FROM workspace w WHERE w.id = "{table}".workspace_id
                            AND w.organization_id = "{table}".organization_id)
            )
        """
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY rls_policy_{table} ON "{table}" FOR ALL USING ({predicate}) WITH CHECK ({predicate})'
        )


def downgrade() -> None:
    op.drop_table("search_chunk")
    op.drop_table("search_document")
    op.drop_table("search_collection")
    op.drop_table("search_embedding_config")
    op.drop_table("search_workspace_state")
