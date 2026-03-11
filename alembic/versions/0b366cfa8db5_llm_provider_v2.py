"""llm provider v2

Revision ID: 0b366cfa8db5
Revises: 6171727be56a
Create Date: 2026-03-11 12:08:41.092500

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0b366cfa8db5"
down_revision: str | None = "6171727be56a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_model_sources",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("encrypted_config", sa.LargeBinary(), nullable=True),
        sa.Column("api_key_header", sa.String(length=120), nullable=True),
        sa.Column("api_version", sa.String(length=120), nullable=True),
        sa.Column(
            "discovery_status",
            sa.String(length=32),
            server_default=sa.text("'never'"),
            nullable=False,
        ),
        sa.Column("last_refreshed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "declared_models", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_model_sources_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_model_sources")),
        sa.UniqueConstraint(
            "organization_id",
            "id",
            name=op.f("uq_agent_model_sources_organization_id_id"),
        ),
    )
    op.create_index(
        op.f("ix_agent_model_sources_id"),
        "agent_model_sources",
        ["id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_agent_model_sources_type"),
        "agent_model_sources",
        ["type"],
        unique=False,
    )
    op.create_table(
        "agent_discovered_models",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("source_type", sa.String(length=120), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("catalog_ref", sa.String(length=500), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_provider", sa.String(length=120), nullable=False),
        sa.Column("runtime_provider", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("raw_model_id", sa.String(length=500), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column(
            "model_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("last_refreshed_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_discovered_models_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["agent_model_sources.id"],
            name=op.f("fk_agent_discovered_models_source_id_agent_model_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_agent_discovered_models"),
        ),
    )
    op.create_index(
        op.f("ix_agent_discovered_models_catalog_ref"),
        "agent_discovered_models",
        ["catalog_ref"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_discovered_models_organization_id"),
        "agent_discovered_models",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_discovered_models_source_id",
        "agent_discovered_models",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_discovered_models_source_type"),
        "agent_discovered_models",
        ["source_type"],
        unique=False,
    )
    op.create_index(
        "uq_agent_discovered_models_catalog_ref_global",
        "agent_discovered_models",
        ["catalog_ref"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_index(
        "uq_agent_discovered_models_organization_id_catalog_ref",
        "agent_discovered_models",
        ["organization_id", "catalog_ref"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_table(
        "agent_enabled_models",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("catalog_ref", sa.String(length=500), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=True),
        sa.Column("source_type", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("model_provider", sa.String(length=120), nullable=False),
        sa.Column("runtime_provider", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column(
            "enabled_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name=op.f("fk_agent_enabled_models_organization_id_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["agent_model_sources.id"],
            name=op.f("fk_agent_enabled_models_source_id_agent_model_sources"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_agent_enabled_models")),
        sa.UniqueConstraint(
            "organization_id",
            "catalog_ref",
            name=op.f("uq_agent_enabled_models_organization_id_catalog_ref"),
        ),
    )
    op.create_index(
        op.f("ix_agent_enabled_models_catalog_ref"),
        "agent_enabled_models",
        ["catalog_ref"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_enabled_models_id"),
        "agent_enabled_models",
        ["id"],
        unique=True,
    )
    op.add_column(
        "agent_preset",
        sa.Column("model_catalog_ref", sa.String(length=500), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_preset_model_catalog_ref"),
        "agent_preset",
        ["model_catalog_ref"],
        unique=False,
    )
    op.add_column(
        "agent_preset_version",
        sa.Column("model_catalog_ref", sa.String(length=500), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_preset_version_model_catalog_ref"),
        "agent_preset_version",
        ["model_catalog_ref"],
        unique=False,
    )
    op.add_column(
        "agent_session",
        sa.Column("model_catalog_ref", sa.String(length=500), nullable=True),
    )
    op.create_index(
        op.f("ix_agent_session_model_catalog_ref"),
        "agent_session",
        ["model_catalog_ref"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agent_session_model_catalog_ref"),
        table_name="agent_session",
    )
    op.drop_column("agent_session", "model_catalog_ref")
    op.drop_index(
        op.f("ix_agent_preset_version_model_catalog_ref"),
        table_name="agent_preset_version",
    )
    op.drop_column("agent_preset_version", "model_catalog_ref")
    op.drop_index(
        op.f("ix_agent_preset_model_catalog_ref"),
        table_name="agent_preset",
    )
    op.drop_column("agent_preset", "model_catalog_ref")
    op.drop_index(op.f("ix_agent_enabled_models_id"), table_name="agent_enabled_models")
    op.drop_index(
        op.f("ix_agent_enabled_models_catalog_ref"),
        table_name="agent_enabled_models",
    )
    op.drop_table("agent_enabled_models")
    op.drop_index(
        "uq_agent_discovered_models_organization_id_catalog_ref",
        table_name="agent_discovered_models",
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_agent_discovered_models_catalog_ref_global",
        table_name="agent_discovered_models",
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.drop_index(
        op.f("ix_agent_discovered_models_source_type"),
        table_name="agent_discovered_models",
    )
    op.drop_index(
        "ix_agent_discovered_models_source_id",
        table_name="agent_discovered_models",
    )
    op.drop_index(
        op.f("ix_agent_discovered_models_organization_id"),
        table_name="agent_discovered_models",
    )
    op.drop_index(
        op.f("ix_agent_discovered_models_catalog_ref"),
        table_name="agent_discovered_models",
    )
    op.drop_table("agent_discovered_models")
    op.drop_index(op.f("ix_agent_model_sources_type"), table_name="agent_model_sources")
    op.drop_index(op.f("ix_agent_model_sources_id"), table_name="agent_model_sources")
    op.drop_table("agent_model_sources")
