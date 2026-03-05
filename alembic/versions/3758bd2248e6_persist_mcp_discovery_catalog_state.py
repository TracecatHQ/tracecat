"""persist mcp discovery catalog state

Revision ID: 3758bd2248e6
Revises: 8e2a638ae873
Create Date: 2026-03-05 17:26:55.837099

"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3758bd2248e6"
down_revision: str | None = "8e2a638ae873"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _generate_scope_namespace(existing_namespaces: set[str]) -> str:
    while True:
        if (candidate := secrets.token_hex(8)) not in existing_namespaces:
            existing_namespaces.add(candidate)
            return candidate


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "mcp_integration_catalog_entry",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mcp_integration_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("integration_name", sa.String(), nullable=False),
        sa.Column("artifact_type", sa.String(length=16), nullable=False),
        sa.Column("artifact_key", sa.String(length=96), nullable=False),
        sa.Column("artifact_ref", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["mcp_integration_id"],
            ["mcp_integration.id"],
            name=op.f(
                "fk_mcp_integration_catalog_entry_mcp_integration_id_mcp_integration"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_mcp_integration_catalog_entry_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_integration_catalog_entry")),
        sa.UniqueConstraint(
            "mcp_integration_id",
            "artifact_type",
            "artifact_key",
            name="uq_mcp_integration_catalog_entry_integration_artifact",
        ),
    )
    op.create_index(
        op.f("ix_mcp_integration_catalog_entry_id"),
        "mcp_integration_catalog_entry",
        ["id"],
        unique=True,
    )
    op.create_index(
        "ix_mcp_integration_catalog_entry_search_vector",
        "mcp_integration_catalog_entry",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_mcp_integration_catalog_entry_workspace_active_type",
        "mcp_integration_catalog_entry",
        ["workspace_id", "is_active", "artifact_type"],
        unique=False,
    )
    op.execute(
        "CREATE INDEX ix_mcp_integration_catalog_entry_display_name_trgm "
        "ON mcp_integration_catalog_entry "
        "USING gin (lower(display_name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_mcp_integration_catalog_entry_artifact_ref_trgm "
        "ON mcp_integration_catalog_entry "
        "USING gin (lower(artifact_ref) gin_trgm_ops)"
    )

    op.create_table(
        "mcp_integration_discovery_attempt",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("mcp_integration_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "artifact_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=1024), nullable=True),
        sa.Column(
            "error_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["mcp_integration_id"],
            ["mcp_integration.id"],
            name=op.f(
                "fk_mcp_integration_discovery_attempt_mcp_integration_id_mcp_integration"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_mcp_integration_discovery_attempt_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_mcp_integration_discovery_attempt")
        ),
    )
    op.create_index(
        op.f("ix_mcp_integration_discovery_attempt_id"),
        "mcp_integration_discovery_attempt",
        ["id"],
        unique=True,
    )

    op.add_column(
        "mcp_integration",
        sa.Column("scope_namespace", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "mcp_integration",
        sa.Column(
            "discovery_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.add_column(
        "mcp_integration",
        sa.Column(
            "catalog_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "mcp_integration",
        sa.Column(
            "last_discovery_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "mcp_integration",
        sa.Column("last_discovered_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "mcp_integration",
        sa.Column("last_discovery_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "mcp_integration",
        sa.Column(
            "last_discovery_error_summary", sa.String(length=1024), nullable=True
        ),
    )
    op.add_column(
        "mcp_integration",
        sa.Column(
            "sandbox_allow_network",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "mcp_integration",
        sa.Column(
            "sandbox_egress_allowlist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "mcp_integration",
        sa.Column(
            "sandbox_egress_denylist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    connection = op.get_bind()
    existing_namespaces = {
        namespace
        for namespace in connection.execute(
            sa.text(
                "SELECT scope_namespace "
                "FROM mcp_integration "
                "WHERE scope_namespace IS NOT NULL"
            )
        ).scalars()
        if namespace is not None
    }
    integration_ids = connection.execute(
        sa.text("SELECT id FROM mcp_integration WHERE scope_namespace IS NULL")
    ).scalars()
    for integration_id in integration_ids:
        connection.execute(
            sa.text(
                "UPDATE mcp_integration "
                "SET scope_namespace = :scope_namespace "
                "WHERE id = :integration_id"
            ),
            {
                "scope_namespace": _generate_scope_namespace(existing_namespaces),
                "integration_id": integration_id,
            },
        )

    op.alter_column(
        "mcp_integration",
        "scope_namespace",
        existing_type=sa.String(length=16),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_mcp_integration_scope_namespace",
        "mcp_integration",
        ["scope_namespace"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_mcp_integration_scope_namespace",
        "mcp_integration",
        type_="unique",
    )
    op.drop_column("mcp_integration", "sandbox_egress_denylist")
    op.drop_column("mcp_integration", "sandbox_egress_allowlist")
    op.drop_column("mcp_integration", "sandbox_allow_network")
    op.drop_column("mcp_integration", "last_discovery_error_summary")
    op.drop_column("mcp_integration", "last_discovery_error_code")
    op.drop_column("mcp_integration", "last_discovered_at")
    op.drop_column("mcp_integration", "last_discovery_attempt_at")
    op.drop_column("mcp_integration", "catalog_version")
    op.drop_column("mcp_integration", "discovery_status")
    op.drop_column("mcp_integration", "scope_namespace")

    op.drop_index(
        op.f("ix_mcp_integration_discovery_attempt_id"),
        table_name="mcp_integration_discovery_attempt",
    )
    op.drop_table("mcp_integration_discovery_attempt")

    op.execute(
        "DROP INDEX IF EXISTS ix_mcp_integration_catalog_entry_artifact_ref_trgm"
    )
    op.execute(
        "DROP INDEX IF EXISTS ix_mcp_integration_catalog_entry_display_name_trgm"
    )
    op.drop_index(
        "ix_mcp_integration_catalog_entry_workspace_active_type",
        table_name="mcp_integration_catalog_entry",
    )
    op.drop_index(
        "ix_mcp_integration_catalog_entry_search_vector",
        table_name="mcp_integration_catalog_entry",
        postgresql_using="gin",
    )
    op.drop_index(
        op.f("ix_mcp_integration_catalog_entry_id"),
        table_name="mcp_integration_catalog_entry",
    )
    op.drop_table("mcp_integration_catalog_entry")
